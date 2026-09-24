"""
Lorekeeper V10 — SQLite Store (Sprint 0: Dual-Write Foundation)

목적: 통짜 JSON 세션 저장과 *병렬로* SQLite에도 같은 데이터를 쓴다.
이 단계에서 봇은 V9과 100% 동일하게 동작한다 — 읽기는 여전히 JSON.
SQLite는 그림자처럼 따라 쌓이기만 한다. (v10_architecture_vision.md §4 스프린트 0)

설계 원칙:
- **봇 안전 최우선**: 모든 함수는 실패해도 예외를 밖으로 던지지 않는다.
  SQLite 쪽 문제가 절대 봇 본체(JSON 경로)를 죽이면 안 된다.
- **단일 테이블로 시작**: sessions(channel_id PK, data JSON, updated_at).
  도메인 분해(관계/NPC/이력 테이블)는 스프린트 1+에서.
- **WAL 모드**: 동시 읽기/쓰기 안전성 (스프린트 4 틱 루프 대비 선투자).
- **롤백 자유**: 이 모듈 호출부 한 줄만 지우면 V9으로 복귀.

DB 위치: [2026-09-14 W0] config.CHANNELS_DIR/{channel_id}/memory.db (채널 폴더 = 파일 하나가 채널 하나).
        `_DB_PATH`가 설정되면 레거시 모드 — 전 채널이 그 단일 파일(옛 스모크/도구 호환).
"""

import os
import json
import sqlite3
import logging
import threading
import time
from typing import Optional, Dict, Any, Iterator, Tuple, List

import config

logger = logging.getLogger("SQLiteStore")

# [2026-09-14 W0] 기본값 None = 채널 폴더 모드(data/channels/{id}/memory.db).
#   설정돼 있으면 **레거시 모드**: 전 채널이 그 파일 하나를 쓴다.
#   옛 스모크 23개가 `sqlite_store._DB_PATH = <임시파일>` 로 갈아끼우는 그 자리.
_DB_PATH = None

# sqlite3 연결은 스레드마다 따로 두는 게 안전. thread-local로 관리.
#   레거시 모드 : _local.conn (단일 슬롯 — 옛 스모크가 `_local.conn = None`으로 재연결시킨다)
#   채널 모드   : _local.conns = {path: conn}
_local = threading.local()
_init_lock = threading.Lock()
_initialized = set()          # 스키마를 만든 DB 파일 경로 집합 (옛 코드의 bool 1개를 대체)

# 리셋/삭제 때 다른 스레드의 연결까지 닫아야 파일이 지워진다(Windows 잠금).
_conn_reg_lock = threading.Lock()
_conn_registry = {}           # path -> list[sqlite3.Connection]


def _db_path_for(channel_id: str = "") -> Optional[str]:
    """이 채널이 쓸 DB 파일 경로. 레거시 모드면 _DB_PATH, 아니면 채널 폴더/memory.db.
    채널도 없고 _DB_PATH도 없으면 None(= 채널 없는 자리)."""
    if _DB_PATH:
        return _DB_PATH
    if not channel_id:
        return None
    try:
        import domain_manager
        d = domain_manager.get_channel_dir(channel_id)
    except Exception:
        d = os.path.join(config.CHANNELS_DIR, str(channel_id))
    try:
        os.makedirs(d, exist_ok=True)
    except Exception as e:
        logger.warning(f"[SQLiteStore] 채널 폴더 생성 실패 (무시): {d}: {e}")
    return os.path.join(d, config.CHANNEL_DB_FILE)


def _connect(path: str) -> Optional[sqlite3.Connection]:
    try:
        d = os.path.dirname(path)
        if d:
            os.makedirs(d, exist_ok=True)
        conn = sqlite3.connect(path, timeout=10.0)
        # WAL: 동시 읽기 중 쓰기 허용. 틱 루프(스프린트 4) 대비.
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        with _conn_reg_lock:
            _conn_registry.setdefault(path, []).append(conn)
        return conn
    except Exception as e:
        logger.warning(f"[SQLiteStore] connect 실패 (무시, JSON 경로 계속): {e}")
        return None


def _get_conn(channel_id: str = "") -> Optional[sqlite3.Connection]:
    """채널별 연결 반환. 실패 시 None (봇 안전)."""
    if _DB_PATH:
        # 레거시 단일 파일 — 옛 계약 그대로(단일 슬롯).
        conn = getattr(_local, "conn", None)
        if conn is not None:
            return conn
        conn = _connect(_DB_PATH)
        if conn is not None:
            _local.conn = conn
        return conn
    path = _db_path_for(channel_id)
    if not path:
        return None
    conns = getattr(_local, "conns", None)
    if conns is None:
        conns = {}
        _local.conns = conns
    conn = conns.get(path)
    if conn is not None:
        return conn
    conn = _connect(path)
    if conn is not None:
        conns[path] = conn
    return conn


def close_channel(channel_id: str = "") -> None:
    """채널 DB 연결을 전부 닫고 캐시에서 지운다(파일 삭제 전 호출). 예외 안 던짐."""
    path = _db_path_for(channel_id)
    if not path:
        return
    with _conn_reg_lock:
        conns = _conn_registry.pop(path, [])
    for c in conns:
        try:
            c.close()
        except Exception:
            pass
    # 이 스레드 캐시에서도 제거 (다른 스레드는 닫힌 연결 → _get_conn 재연결은 못 하지만
    # 읽기 실패 = None 반환 계약이라 봇은 안전하다).
    try:
        conns_map = getattr(_local, "conns", None)
        if conns_map is not None:
            conns_map.pop(path, None)
        if _DB_PATH and getattr(_local, "conn", None) is not None:
            _local.conn = None
    except Exception:
        pass
    with _init_lock:
        try:
            _initialized.discard(path)
        except Exception:
            pass


# =========================================================
# [2026-09-14 W3b] cache.db — 재생성 가능한 채널 캐시(절 벡터). memory.db와 **별 파일**.
#   정본이 아니다: 지우면 다음 턴에 다시 계산된다 → 리셋·클리어는 통째 삭제로 끝낸다.
#   memory.db와 같은 스레드 로컬 패턴이되 **별 키**(_local.cache_conns).
# =========================================================

_cache_initialized = set()


def _cache_db_path_for(channel_id: str = "") -> Optional[str]:
    """이 채널이 쓸 cache.db 경로. 레거시 모드면 _DB_PATH 옆, 아니면 채널 폴더/cache.db."""
    name = getattr(config, "CHANNEL_CACHE_FILE", "cache.db")
    if _DB_PATH:
        d = os.path.dirname(_DB_PATH) or "."
        return os.path.join(d, name)
    if not channel_id:
        return None
    try:
        import domain_manager
        d = domain_manager.get_channel_dir(channel_id)
    except Exception:
        d = os.path.join(config.CHANNELS_DIR, str(channel_id))
    try:
        os.makedirs(d, exist_ok=True)
    except Exception as e:
        logger.warning(f"[SQLiteStore] 채널 폴더 생성 실패 (무시): {d}: {e}")
    return os.path.join(d, name)


def get_cache_conn(channel_id: str = "") -> Optional[sqlite3.Connection]:
    """cache.db 연결 반환. 실패 시 None (봇 안전)."""
    path = _cache_db_path_for(channel_id)
    if not path:
        return None
    conns = getattr(_local, "cache_conns", None)
    if conns is None:
        conns = {}
        _local.cache_conns = conns
    conn = conns.get(path)
    if conn is not None:
        return conn
    conn = _connect(path)
    if conn is not None:
        conns[path] = conn
    return conn


def ensure_cache_schema(channel_id: str = "") -> bool:
    """cache.db 스키마 생성 (파일당 1회). 실패해도 False 반환, 예외 안 던짐."""
    global _cache_initialized
    path = _cache_db_path_for(channel_id)
    if not path:
        return False
    with _init_lock:
        if not isinstance(_cache_initialized, set):
            _cache_initialized = set()
        if path in _cache_initialized:
            return True
        conn = get_cache_conn(channel_id)
        if conn is None:
            return False
        try:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS section_vectors (
                    page_id    TEXT NOT NULL,
                    section    TEXT NOT NULL,
                    hash       TEXT NOT NULL DEFAULT '',
                    model      TEXT NOT NULL DEFAULT '',
                    dim        INTEGER NOT NULL DEFAULT 0,
                    vec        BLOB,
                    updated_at REAL NOT NULL DEFAULT 0,
                    PRIMARY KEY (page_id, section)
                )
            """)
            conn.commit()
            _cache_initialized.add(path)
            return True
        except Exception as e:
            logger.warning(f"[SQLiteStore] cache 스키마 실패 (무시): {path}: {e}")
            return False


def close_cache(channel_id: str = "") -> None:
    """cache.db 연결을 닫고 캐시에서 지운다(파일 삭제 전 호출). 예외 안 던짐."""
    path = _cache_db_path_for(channel_id)
    if not path:
        return
    with _conn_reg_lock:
        conns = _conn_registry.pop(path, [])
    for c in conns:
        try:
            c.close()
        except Exception:
            pass
    try:
        m = getattr(_local, "cache_conns", None)
        if m is not None:
            m.pop(path, None)
    except Exception:
        pass
    with _init_lock:
        try:
            _cache_initialized.discard(path)
        except Exception:
            pass


def delete_cache_db(channel_id: str = "") -> bool:
    """cache.db(-wal/-shm) 삭제. 재생성 가능하므로 정책은 단순히 **지운다**."""
    path = _cache_db_path_for(channel_id)
    if not path:
        return False
    close_cache(channel_id)
    ok = True
    for suffix in ("", "-wal", "-shm"):
        f = path + suffix
        if os.path.exists(f):
            try:
                os.remove(f)
            except (OSError, PermissionError) as e:
                logger.warning(f"[SQLiteStore] cache 삭제 실패 (무시): {f}: {e}")
                ok = False
    return ok


def _iter_channel_ids() -> List[str]:
    """CHANNELS_DIR 아래 채널 id 목록(폴더 이름). 실패 시 빈 목록."""
    try:
        base = config.CHANNELS_DIR
        return sorted(n for n in os.listdir(base) if os.path.isdir(os.path.join(base, n)))
    except Exception:
        return []


def _ensure_schema(channel_id: str = "") -> bool:
    """테이블 생성 (DB 파일당 1회). 실패해도 False 반환, 예외 안 던짐."""
    global _initialized
    path = _db_path_for(channel_id)
    if not path:
        return False
    with _init_lock:
        if not isinstance(_initialized, set):
            # 옛 스모크가 `_initialized = False` 로 리셋하는 자리 — set으로 복구.
            _initialized = set()
        if path in _initialized:
            return True
        conn = _get_conn(channel_id)
        if conn is None:
            return False
        try:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS sessions (
                    channel_id TEXT PRIMARY KEY,
                    data       TEXT NOT NULL,
                    updated_at REAL NOT NULL
                )
            """)
            # [2026-09-15 관계 통합 1차] 관계 원천 = `relations` 엣지 하나.
            #   설계: 파티쳇수정/state_v10/relation_unify_design_v0.1_2026-09-15.md §2.
            #   (channel_id, source, target) PK — 방향 보존. NPC→PC(kind NULL) · NPC↔NPC(kind enum).
            #   옛 `npc_relations`(attitude/depth 미러)는 마이그레이션 없이 **삭제**(읽지 않는다).
            conn.execute("""
                CREATE TABLE IF NOT EXISTS relations (
                    channel_id  TEXT NOT NULL,
                    source      TEXT NOT NULL,
                    target      TEXT NOT NULL,
                    bond        INTEGER NOT NULL DEFAULT 0,
                    tension     INTEGER NOT NULL DEFAULT 0,
                    stance      TEXT NOT NULL DEFAULT '',
                    kind        TEXT,
                    last_turn   INTEGER,
                    history     TEXT NOT NULL DEFAULT '[]',
                    PRIMARY KEY (channel_id, source, target)
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_rel_edges_target ON relations(channel_id, target)")
            conn.execute("DROP TABLE IF EXISTS npc_relations")
            # [V10 Sprint 2-A] NPC 지식 테이블 (v10_sprint2_npc_spec.md §A-3)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS npc_knowledge (
                    channel_id    TEXT NOT NULL,
                    npc_name      TEXT NOT NULL,
                    knows         TEXT NOT NULL DEFAULT '[]',
                    secrets_held  TEXT NOT NULL DEFAULT '[]',
                    would_share   INTEGER NOT NULL DEFAULT 0,
                    leak_risk     TEXT NOT NULL DEFAULT 'none',
                    updated_at    TEXT NOT NULL DEFAULT '',
                    suspects      TEXT NOT NULL DEFAULT '[]',
                    misbeliefs    TEXT NOT NULL DEFAULT '[]',
                    PRIMARY KEY (channel_id, npc_name)
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_knowledge_channel ON npc_knowledge(channel_id)")
            # [V10 지식 lite] 기존 배포 DB 마이그레이션 — 컬럼 없으면 추가(있으면 ALTER 실패→무시).
            for _alter in (
                "ALTER TABLE npc_knowledge ADD COLUMN suspects TEXT NOT NULL DEFAULT '[]'",
                "ALTER TABLE npc_knowledge ADD COLUMN misbeliefs TEXT NOT NULL DEFAULT '[]'",
            ):
                try:
                    conn.execute(_alter)
                except Exception:
                    pass
            # [V10 Sprint 2-B] NPC 본체 테이블 — 문서 컬럼(data JSON) + 질의용 메타 (§B-1)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS npcs (
                    channel_id  TEXT NOT NULL,
                    npc_name    TEXT NOT NULL,
                    source      TEXT NOT NULL DEFAULT 'session',
                    status      TEXT NOT NULL DEFAULT '',
                    data        TEXT NOT NULL,
                    updated_at  REAL NOT NULL,
                    PRIMARY KEY (channel_id, npc_name)
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_npcs_channel ON npcs(channel_id)")
            # [V10 Sprint 3] 대화 이력 도메인 (v10_sprint3_history_spec.md)
            # history_log: append-only 영구 기록. JSON history(작업 창)와 의도적 비대칭 —
            # trim/발효 소비로 JSON에서 사라져도 여기엔 남는다. DELETE는 리셋뿐.
            conn.execute("""
                CREATE TABLE IF NOT EXISTS history_log (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    channel_id  TEXT NOT NULL,
                    role        TEXT NOT NULL,
                    content     TEXT NOT NULL,
                    message_id  TEXT,
                    game_time   TEXT,
                    created_at  REAL NOT NULL
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_histlog_channel ON history_log(channel_id, id)")
            conn.execute("""
                CREATE TABLE IF NOT EXISTS fermented_history (
                    channel_id  TEXT NOT NULL,
                    seq         INTEGER NOT NULL,
                    entry       TEXT NOT NULL,
                    updated_at  REAL NOT NULL,
                    PRIMARY KEY (channel_id, seq)
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS deep_memory (
                    channel_id  TEXT PRIMARY KEY,
                    narrative   TEXT NOT NULL DEFAULT '',
                    data        TEXT NOT NULL DEFAULT '{}',
                    updated_at  REAL NOT NULL
                )
            """)
            # [V10] DAI 스냅샷 롤링 로그 — Theoria 분석의 턴별 보존.
            # 용도: ①관측(필드 비대/모델 JSON 버릇) ②Sprint 4 동적 NPC 원재료 (턴별 심리·사회 이력)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS dai_logs (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    channel_id  TEXT NOT NULL,
                    turn        INTEGER NOT NULL,
                    dai         TEXT NOT NULL,
                    created_at  REAL NOT NULL
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_dailogs_channel ON dai_logs(channel_id, id)")
            # [V10 Sprint 4] 막간 장부 — 장면 밖 NPC 행적의 기록 (환각 대체).
            # 순수 코드 전진 결과만 들어옴 (LLM 출력 아님 → act enum 방벽 강제).
            conn.execute("""
                CREATE TABLE IF NOT EXISTS offscreen_ledger (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    channel_id  TEXT NOT NULL,
                    npc_name    TEXT NOT NULL,
                    act         TEXT NOT NULL,
                    summary     TEXT NOT NULL,
                    motive      TEXT NOT NULL DEFAULT '',
                    route       TEXT NOT NULL DEFAULT '',
                    traces      TEXT NOT NULL DEFAULT '[]',
                    mood_delta  TEXT NOT NULL DEFAULT '',
                    game_span   TEXT NOT NULL DEFAULT '',
                    consumed    INTEGER NOT NULL DEFAULT 0,
                    created_at  REAL NOT NULL
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_ledger_channel ON offscreen_ledger(channel_id, consumed, id)")
            # [notebook v2 2026-09-06] 노트북 행 장부 — 섹션별 append-only 이력.
            # 이번 단계 호출자 0 (P2·P3가 쓴다). 세션 파생 → clear_session_scoped 대상.
            conn.execute("""
                CREATE TABLE IF NOT EXISTS notebook_log (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    channel_id  TEXT NOT NULL,
                    user_id     TEXT NOT NULL DEFAULT '',
                    section     TEXT NOT NULL,
                    content     TEXT NOT NULL,
                    game_time   TEXT NOT NULL DEFAULT '',
                    turn_index  INTEGER NOT NULL DEFAULT 0,
                    created_at  REAL NOT NULL
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_notebook_log_channel "
                         "ON notebook_log(channel_id, section, id)")
            # [V10 적립 패러다임] 감정 매핑 장부 — emotion_engine 턴별 per-NPC 스냅샷.
            # 기존엔 bus.emotion이 매 턴 계산→슬롯 주입→증발. 여기 적립해서 궤적/스파이크 질의 가능.
            # 질의축을 컬럼으로 분해(잘 찾아오기) + 전체는 raw_json. 콜0·append-only·읽기경로 무변경.
            conn.execute("""
                CREATE TABLE IF NOT EXISTS emotion_log (
                    id              INTEGER PRIMARY KEY AUTOINCREMENT,
                    channel_id      TEXT NOT NULL,
                    turn            INTEGER NOT NULL,
                    npc_name        TEXT NOT NULL,
                    base            TEXT NOT NULL DEFAULT '',
                    modifier        TEXT NOT NULL DEFAULT '',
                    intensity       REAL NOT NULL DEFAULT 0,
                    spike           INTEGER NOT NULL DEFAULT 0,
                    scene_base      TEXT NOT NULL DEFAULT '',
                    scene_mod       TEXT NOT NULL DEFAULT '',
                    pair_confidence REAL NOT NULL DEFAULT 0,
                    raw_json        TEXT NOT NULL DEFAULT '{}',
                    created_at      REAL NOT NULL
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_emolog_channel ON emotion_log(channel_id, id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_emolog_npc ON emotion_log(channel_id, npc_name, id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_emolog_turn ON emotion_log(channel_id, turn)")
            # [V10 적립] 턴 스냅샷 — 턴당 1행, doom/storydir/vigor 스칼라 상태. (콜0·읽기경로 무변경)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS turn_snapshot (
                    channel_id       TEXT NOT NULL,
                    turn             INTEGER NOT NULL,
                    doom_value       INTEGER,
                    doom_phase       TEXT NOT NULL DEFAULT '',
                    vigor            INTEGER,
                    vigor_delta      INTEGER NOT NULL DEFAULT 0,
                    composure        INTEGER,
                    composure_delta  INTEGER NOT NULL DEFAULT 0,
                    sd_pacing        TEXT NOT NULL DEFAULT '',
                    sd_tension       TEXT NOT NULL DEFAULT '',
                    sd_focus         TEXT NOT NULL DEFAULT '',
                    sd_beat          INTEGER NOT NULL DEFAULT 0,
                    sd_idle          INTEGER NOT NULL DEFAULT 0,
                    judgment_active  INTEGER NOT NULL DEFAULT 0,
                    anomaly_triggered INTEGER NOT NULL DEFAULT 0,
                    raw_json         TEXT NOT NULL DEFAULT '{}',
                    created_at       REAL NOT NULL,
                    PRIMARY KEY (channel_id, turn)
                )
            """)
            # [V10 적립] 태도 전이 이벤트 — 관계가 *언제* 뒤집혔나(실 전이만, no-op/cooldown 제외).
            conn.execute("""
                CREATE TABLE IF NOT EXISTS attitude_log (
                    id            INTEGER PRIMARY KEY AUTOINCREMENT,
                    channel_id    TEXT NOT NULL,
                    turn          INTEGER NOT NULL,
                    npc_name      TEXT NOT NULL,
                    from_attitude TEXT NOT NULL DEFAULT '',
                    to_attitude   TEXT NOT NULL DEFAULT '',
                    result        TEXT NOT NULL DEFAULT '',
                    reason        TEXT NOT NULL DEFAULT '',
                    created_at    REAL NOT NULL
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_attlog_npc ON attitude_log(channel_id, npc_name, id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_attlog_channel ON attitude_log(channel_id, id)")
            # [2026-08-11 soma 지속] B축 전이 이벤트 — 몸 상태(polyvagal/dissociation)가 *언제*
            # 뒤집혔나. attitude_log 동형(관측 턴의 실 전이만, 무변화·오프스테이지는 기록 안 함).
            conn.execute("""
                CREATE TABLE IF NOT EXISTS soma_log (
                    id                INTEGER PRIMARY KEY AUTOINCREMENT,
                    channel_id        TEXT NOT NULL,
                    turn              INTEGER NOT NULL,
                    npc_name          TEXT NOT NULL,
                    from_polyvagal    TEXT NOT NULL DEFAULT '',
                    to_polyvagal      TEXT NOT NULL DEFAULT '',
                    from_dissociation TEXT NOT NULL DEFAULT '',
                    to_dissociation   TEXT NOT NULL DEFAULT '',
                    created_at        REAL NOT NULL
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_somalog_npc ON soma_log(channel_id, npc_name, id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_somalog_channel ON soma_log(channel_id, id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_somalog_turn ON soma_log(channel_id, turn)")
            # [V10 적립] autonomy_log — NPC 자율 트리거 발동(npc/trigger/priority/directive).
            # 대사·관계 압력의 출처. dai_logs는 트리거 평가 전에 써져서 안 잡힘 → 전용 적립.
            conn.execute("""
                CREATE TABLE IF NOT EXISTS autonomy_log (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    channel_id  TEXT NOT NULL,
                    turn        INTEGER NOT NULL,
                    npc_name    TEXT NOT NULL,
                    trigger_id  TEXT NOT NULL DEFAULT '',
                    priority    INTEGER NOT NULL DEFAULT 0,
                    directive   TEXT NOT NULL DEFAULT '',
                    created_at  REAL NOT NULL
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_autolog_npc ON autonomy_log(channel_id, npc_name, id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_autolog_channel ON autonomy_log(channel_id, id)")
            # [2026-08-16 도착물 라우트] turn_mail — 이번 턴 산문 메시지에 딸린 **도착물**
            # (💌 편지·쪽지 = world_board message 채널 / 💭 속마음). 공개 스레드 대신 그 턴의
            # 산문 메시지 버튼 → ephemeral 로 본다. message_id 가 **턴 고정 키** —
            # 조회는 항상 (channel_id, message_id)로, 나중 턴의 내용이 옛 버튼에 새지 않는다.
            conn.execute("""
                CREATE TABLE IF NOT EXISTS turn_mail (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    channel_id  TEXT NOT NULL,
                    message_id  INTEGER NOT NULL,
                    turn        INTEGER NOT NULL DEFAULT 0,
                    kind        TEXT NOT NULL DEFAULT 'mail',
                    payload     TEXT NOT NULL DEFAULT '{}',
                    created_at  REAL NOT NULL
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_turnmail_msg ON turn_mail(channel_id, message_id, kind)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_turnmail_channel ON turn_mail(channel_id, id)")

            # [!다시] 리두용 턴-직전 도메인 스냅샷 — 채널당 1개(REPLACE), 봇 재시작에도 보존.
            #   인메모리 _RETRY_SNAPSHOTS가 인스턴스 재생성/재시작에 날아가도 retry_last가 여기서 폴백 복원.
            conn.execute("""
                CREATE TABLE IF NOT EXISTS retry_snapshot (
                    channel_id  TEXT PRIMARY KEY,
                    turn        INTEGER NOT NULL,
                    data        TEXT NOT NULL,
                    created_at  REAL NOT NULL,
                    marks       TEXT NOT NULL DEFAULT '{}'
                )
            """)
            # [2026-08-12 !다시 유령 정리] 기존 배포 DB 마이그레이션 — 컬럼 없으면 추가(있으면 ALTER 실패→무시).
            # marks = 스냅샷 시점 로그 테이블별 max(id). data는 복원 시 **그대로 도메인이 되므로**
            # 여기 섞으면 도메인 오염 — 별 컬럼이 정답.
            try:
                conn.execute("ALTER TABLE retry_snapshot ADD COLUMN marks TEXT NOT NULL DEFAULT '{}'")
            except Exception:
                pass

            # [Reader-GM 2026-07-05] 서브 GM 독자의 턴별 수신 노트(blind read 다이제스트).
            # [2026-08-11 리더 §7] 구 "log-only·프롬프트 급식 없음"은 stale — 현행 FEED=1에서 13경로가
            # 소비(서사 콜 조건부 급식 포함). 세션 파생이라 clear_session_scoped 대상 + 채널당 KEEP 롤링.
            conn.execute("""
                CREATE TABLE IF NOT EXISTS reader_log (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    channel_id  TEXT NOT NULL,
                    turn        INTEGER NOT NULL,
                    digest      TEXT NOT NULL,
                    dropped     INTEGER NOT NULL DEFAULT 0,
                    created_at  REAL NOT NULL
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_readerlog_channel ON reader_log(channel_id, id)")
            # [V10 Secret Ledger] NPC 지식경계 상태 기계 (에로스 타워 E3, 2026-07-14).
            # truth/surface 이중층 + 3단 인식 등급 + 압력 축적 + reveal_gate.
            # 스펙: 파티쳇수정/state_v10/v10_secret_ledger_spec.md. 삭제 대신 retire(status).
            conn.execute("""
                CREATE TABLE IF NOT EXISTS secret_ledger (
                    channel_id     TEXT NOT NULL,
                    secret_id      TEXT NOT NULL,
                    truth          TEXT NOT NULL,
                    surface        TEXT NOT NULL DEFAULT '',
                    owners         TEXT NOT NULL DEFAULT '[]',
                    knowers        TEXT NOT NULL DEFAULT '[]',
                    suspecters     TEXT NOT NULL DEFAULT '[]',
                    cannot_know    TEXT NOT NULL DEFAULT '[]',
                    leak_pressure  INTEGER NOT NULL DEFAULT 0,
                    reveal_gate    TEXT NOT NULL DEFAULT '',
                    risk_if_revealed TEXT NOT NULL DEFAULT '',
                    status         TEXT NOT NULL DEFAULT 'kept',
                    canon_level    TEXT NOT NULL DEFAULT 'established',
                    turn_count     INTEGER NOT NULL DEFAULT 0,
                    updated_at     TEXT NOT NULL DEFAULT '',
                    reader_exposure INTEGER NOT NULL DEFAULT 0,
                    reader_exposure_turn INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY (channel_id, secret_id)
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_secretledger_channel ON secret_ledger(channel_id)")
            # [2026-08-11 리더 소비자] 기존 배포 DB 마이그레이션 — 컬럼 없으면 추가(있으면 ALTER 실패→무시).
            # reader_exposure = 독자가 established로 접지한 사실이 이 비밀과 겹친 턴 수(누적 관측).
            # leak_pressure가 파생 재계산이라 직접 가산이 덮이는 문제의 저장측 해법.
            try:
                conn.execute("ALTER TABLE secret_ledger ADD COLUMN reader_exposure INTEGER NOT NULL DEFAULT 0")
            except Exception:
                pass
            # [2026-08-12 !다시 유령 정리] reader_exposure_turn = 마지막 가산 턴.
            # 동턴 dedup 도장이 세션(session_ai_memory.reader_leak_turn)에만 있어 !다시가 그걸 롤백 →
            # 재실행 시 같은 비밀이 또 가산됐다. **행 내부** 도장이라 도메인 롤백과 무관.
            try:
                conn.execute("ALTER TABLE secret_ledger ADD COLUMN reader_exposure_turn INTEGER NOT NULL DEFAULT 0")
            except Exception:
                pass
            # [2026-09-14 S5a] fact_sources — NPC 지식 사실의 **첫 출처** 원장.
            # knows 리스트 형태는 무변경(문자열 그대로) — 출처는 이 옆 테이블에 append만.
            # INSERT OR IGNORE: 나중 재언급이 첫 출처를 덮지 않는다(LIBRA inherited 대응).
            conn.execute("""
                CREATE TABLE IF NOT EXISTS fact_sources (
                    channel_id   TEXT NOT NULL,
                    npc_name     TEXT NOT NULL,
                    fact_norm    TEXT NOT NULL,
                    src_turn     INTEGER,
                    src_msg_id   INTEGER,
                    first_seen   TEXT NOT NULL DEFAULT '',
                    PRIMARY KEY (channel_id, npc_name, fact_norm)
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_factsrc_channel ON fact_sources(channel_id)")
            # [2026-09-14 W1] 내부 위키 — pages / sections / section_history.
            # 이 파일은 **스키마·연결만** 진다(비대화 방지). 페이지 API 로직은 wiki_store.py.
            # 설계: internal_wiki_spec_2026-09-14.md §2.
            conn.execute("""
                CREATE TABLE IF NOT EXISTS pages (
                    channel_id   TEXT NOT NULL,
                    page_id      TEXT NOT NULL,
                    kind         TEXT NOT NULL,
                    name         TEXT NOT NULL,
                    aliases      TEXT NOT NULL DEFAULT '[]',
                    source       TEXT NOT NULL DEFAULT 'play',
                    status       TEXT NOT NULL DEFAULT '',
                    created_turn INTEGER,
                    updated_turn INTEGER,
                    PRIMARY KEY (channel_id, page_id)
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS sections (
                    channel_id   TEXT NOT NULL,
                    page_id      TEXT NOT NULL,
                    section      TEXT NOT NULL,
                    owner        TEXT NOT NULL,
                    body         TEXT NOT NULL DEFAULT '',
                    src_turns    TEXT NOT NULL DEFAULT '[]',
                    updated_turn INTEGER,
                    hash         TEXT NOT NULL DEFAULT '',
                    derived_hash TEXT NOT NULL DEFAULT '',
                    PRIMARY KEY (channel_id, page_id, section)
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS section_history (
                    id         INTEGER PRIMARY KEY AUTOINCREMENT,
                    channel_id TEXT NOT NULL,
                    page_id    TEXT NOT NULL,
                    section    TEXT NOT NULL,
                    revision   INTEGER NOT NULL,
                    body       TEXT NOT NULL,
                    src_turns  TEXT NOT NULL DEFAULT '[]',
                    turn       INTEGER,
                    reason     TEXT NOT NULL DEFAULT ''
                )
            """)
            # [2026-09-14 W5] 모음 줄 패치 횟수(승격 판정용). 내용은 안 담는다 — 계수만.
            conn.execute("""
                CREATE TABLE IF NOT EXISTS wiki_tally (
                    channel_id TEXT NOT NULL,
                    kind       TEXT NOT NULL,
                    name       TEXT NOT NULL,
                    hits       INTEGER NOT NULL DEFAULT 0,
                    last_turn  INTEGER,
                    PRIMARY KEY (channel_id, kind, name)
                )
            """)
            # [2026-09-25 스레드 장부] 약속·사안 수명주기 = **이벤트 행이 정본**(현재 상태는 thread_ledger.fold).
            #   롤링 삭제 없음(open 행이 잘리면 fold 가 깨진다). !다시 = _RETRY_LOG_TABLES 워터마크 트림이 곧 상태 복원.
            conn.execute("""
                CREATE TABLE IF NOT EXISTS thread_log (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    channel_id  TEXT NOT NULL,
                    turn        INTEGER NOT NULL,
                    thread_id   TEXT NOT NULL,
                    op          TEXT NOT NULL,
                    kind        TEXT NOT NULL DEFAULT '',
                    title       TEXT NOT NULL DEFAULT '',
                    parties     TEXT NOT NULL DEFAULT '[]',
                    outcome     TEXT NOT NULL DEFAULT '',
                    remaining   TEXT NOT NULL DEFAULT '',
                    due_start   INTEGER,
                    due_end     INTEGER,
                    due_prec    TEXT NOT NULL DEFAULT '',
                    quote       TEXT NOT NULL DEFAULT '',
                    created_at  REAL NOT NULL
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_threadlog_channel ON thread_log(channel_id, id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_threadlog_thread ON thread_log(channel_id, thread_id, id)")
            # [2026-09-16 시트 2차] PC = 위키 인물 페이지. owner_uid(참가자↔페이지 1:1 표식, ''=NPC 등)
            #   + built_len(grow_sheet 정리 마커 — 직전 정리 직후 Observed 길이). 기존 DB는 ALTER(있으면 무시).
            for _alt in ("ALTER TABLE pages ADD COLUMN owner_uid TEXT NOT NULL DEFAULT ''",
                         "ALTER TABLE pages ADD COLUMN built_len INTEGER NOT NULL DEFAULT 0"):
                try:
                    conn.execute(_alt)
                except Exception:
                    pass
            conn.execute("CREATE INDEX IF NOT EXISTS idx_pages_channel_kind ON pages(channel_id, kind)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_sections_page ON sections(channel_id, page_id)")
            # [2026-09-24 감사] play 절 쓰기마다 `MAX(revision) … WHERE channel_id,page_id,section` 이 전체 스캔이었다.
            conn.execute("CREATE INDEX IF NOT EXISTS idx_sechist_sec ON section_history(channel_id, page_id, section, revision)")
            conn.commit()
            _initialized.add(path)
            return True
        except Exception as e:
            logger.warning(f"[SQLiteStore] schema 생성 실패 (무시): {e}")
            return False


def write_session(channel_id: str, data: Dict[str, Any]) -> bool:
    """세션 데이터를 SQLite에 upsert. 실패해도 False 반환 (봇 영향 없음).

    save_domain에서 JSON 저장 성공 *후* 호출된다. 즉 JSON이 진실의 원천이고
    SQLite는 미러. 여기서 실패해도 데이터 유실 없음 (JSON에 이미 저장됨)."""
    if not channel_id:
        return False
    if not _ensure_schema(channel_id):
        return False
    conn = _get_conn(channel_id)
    if conn is None:
        return False
    try:
        blob = json.dumps(data, ensure_ascii=False)
        conn.execute(
            "INSERT INTO sessions (channel_id, data, updated_at) VALUES (?, ?, ?) "
            "ON CONFLICT(channel_id) DO UPDATE SET data=excluded.data, updated_at=excluded.updated_at",
            (channel_id, blob, time.time()),
        )
        conn.commit()
        return True
    except Exception as e:
        logger.warning(f"[SQLiteStore] write 실패 (무시, JSON은 정상): {channel_id}: {e}")
        return False


def read_session(channel_id: str) -> Optional[Dict[str, Any]]:
    """SQLite에서 세션 읽기. 검증용. 스프린트 0에서 봇은 이걸 안 씀(읽기는 JSON).
    없거나 실패 시 None."""
    if not channel_id or not _ensure_schema(channel_id):
        return None
    conn = _get_conn(channel_id)
    if conn is None:
        return None
    try:
        cur = conn.execute("SELECT data FROM sessions WHERE channel_id=?", (channel_id,))
        row = cur.fetchone()
        if row is None:
            return None
        return json.loads(row[0])
    except Exception as e:
        logger.warning(f"[SQLiteStore] read 실패: {channel_id}: {e}")
        return None


def _iter_scope_channels() -> List[str]:
    """[2026-09-14 W0] 채널 인자가 없는 함수가 돌 대상.
    레거시 모드면 단일 파일 하나(빈 채널 id), 아니면 CHANNELS_DIR 폴더 목록."""
    if _DB_PATH:
        return [""]
    return _iter_channel_ids()


def iter_all() -> Iterator[Tuple[str, Dict[str, Any]]]:
    """모든 세션 (channel_id, data) 순회. 검증/마이그레이션용.
    [2026-09-14 W0] 채널 폴더를 돌며 합산(레거시 모드면 종전 단일 파일)."""
    for ch in _iter_scope_channels():
        if not _ensure_schema(ch):
            continue
        conn = _get_conn(ch)
        if conn is None:
            continue
        try:
            cur = conn.execute("SELECT channel_id, data FROM sessions")
            for channel_id, blob in cur.fetchall():
                try:
                    yield channel_id, json.loads(blob)
                except Exception:
                    continue
        except Exception as e:
            logger.warning(f"[SQLiteStore] iter_all 실패: {e}")
            continue


def _count_all(table: str) -> int:
    """[2026-09-14 W0] 채널 폴더 합산 COUNT(*). 전부 실패하면 -1."""
    total = -1
    for ch in _iter_scope_channels():
        if not _ensure_schema(ch):
            continue
        conn = _get_conn(ch)
        if conn is None:
            continue
        try:
            n = int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
        except Exception:
            continue
        total = n if total < 0 else total + n
    return total


def count_sessions() -> int:
    """저장된 세션 수. 검증용. 실패 시 -1."""
    return _count_all("sessions")


# =========================================================
# [2026-09-15 관계 통합 1차] relations 엣지 API — 관계의 유일한 저장소.
# 전 함수 예외 안전: 실패 시 None/[]/0, 절대 raise 안 함.
#   upsert_edge  — 쓰기 관문 하나. 클램프(턴당 캡은 origin="theoria"만) + 하드 범위 + history.
#   get_edges    — 읽기 하나. source/target 필터.
#   decay_edges  — 감쇠 하나. last_turn 시계, bond→0 수렴, tension 하한 0.
# `origin` = 누가 썼나(theoria/batch/ooc/seed/npc_sheet_initial/rename…) — 엣지의 `source` 칸
#   (관계 주체)과 이름이 겹쳐 인자명을 달리했다. history 항목엔 `source` 키로 적는다(설계 §2).
# =========================================================

EDGE_HISTORY_CAP = 20
EDGE_CAPPED_ORIGINS = ("theoria",)          # 턴당 이동폭 캡 대상 — LLM 판독만
EDGE_BOND_STEP_CAP = 5                       # |Δbond| ≤ 5 / 턴
EDGE_TENSION_UP_CAP = 10                     # Δtension ≤ +10 / 턴
EDGE_TENSION_DOWN_CAP = 5                    # Δtension ≥ −5 / 턴
_EDGE_COLS = "source, target, bond, tension, stance, kind, last_turn, history"


def _edge_row(row) -> Dict[str, Any]:
    try:
        hist = json.loads(row[7] or "[]")
        if not isinstance(hist, list):
            hist = []
    except Exception:
        hist = []
    return {"source": row[0], "target": row[1], "bond": int(row[2] or 0),
            "tension": int(row[3] or 0), "stance": row[4] or "", "kind": row[5],
            "last_turn": row[6], "history": hist}


def _clamp_int(v, lo, hi):
    return max(lo, min(hi, int(v)))


def get_edge(channel_id: str, source: str, target: str) -> Optional[Dict[str, Any]]:
    """엣지 단건. 없거나 실패 시 None."""
    if not channel_id or not source or not target or not _ensure_schema(channel_id):
        return None
    conn = _get_conn(channel_id)
    if conn is None:
        return None
    try:
        row = conn.execute(f"SELECT {_EDGE_COLS} FROM relations WHERE channel_id=? AND source=? AND target=?",
                           (channel_id, source, target)).fetchone()
        return _edge_row(row) if row else None
    except Exception as e:
        logger.warning(f"[SQLiteStore] get_edge 실패: {channel_id}/{source}->{target}: {e}")
        return None


def get_edges(channel_id: str, source: Optional[str] = None,
              target: Optional[str] = None) -> List[Dict[str, Any]]:
    """엣지 목록(필터 선택). 실패 시 []."""
    if not channel_id or not _ensure_schema(channel_id):
        return []
    conn = _get_conn(channel_id)
    if conn is None:
        return []
    q = f"SELECT {_EDGE_COLS} FROM relations WHERE channel_id=?"
    args: list = [channel_id]
    if source is not None:
        q += " AND source=?"
        args.append(source)
    if target is not None:
        q += " AND target=?"
        args.append(target)
    try:
        return [_edge_row(r) for r in conn.execute(q + " ORDER BY source, target", args).fetchall()]
    except Exception as e:
        logger.warning(f"[SQLiteStore] get_edges 실패: {channel_id}: {e}")
        return []


def _edge_base(existing: Optional[Dict[str, Any]], turn: int, origin: str) -> Tuple[int, int]:
    """턴당 캡의 기준점 — 이번 턴 **이전** 값.
    같은 턴에 theoria 쓰기가 이미 있었으면(재시도·재생성) 그 쓰기 이전 값을 기준으로 잡는다
    — 한 턴에 두 번 써서 캡을 두 배로 넘는 길을 막는다. 새 엣지는 (0, 0)."""
    if not existing:
        return 0, 0
    hist = existing.get("history") or []
    base_b, base_t = existing["bond"], existing["tension"]
    if hist and isinstance(hist[-1], dict) and hist[-1].get("turn") == turn \
            and hist[-1].get("source") == origin:
        prev = hist[-2] if len(hist) >= 2 and isinstance(hist[-2], dict) else None
        if prev is not None:
            base_b, base_t = int(prev.get("bond", 0) or 0), int(prev.get("tension", 0) or 0)
        else:
            base_b, base_t = 0, 0
    return base_b, base_t


def edge_step_values(existing: Optional[Dict[str, Any]], turn: int,
                     bond: Optional[int], tension: Optional[int],
                     origin: str = "theoria") -> Tuple[int, int]:
    """upsert_edge 의 값 계산부(순수) — 같은 입력이면 upsert 가 저장할 (bond, tension).

    [2026-09-25 관계 한 숫자] Theoria 직후 미리보기(domain_manager.align_theoria_relations)와
    저장이 **같은 식 하나**를 쓰게 뽑았다. 전엔 캡이 저장 때만 걸려서, 같은 턴에 감정 엔진·iceberg 는
    모델이 쓴 캡 전 날숫자를 읽고 저장소엔 캡 뒤 값이 들어갔다(한 턴 두 숫자).
    None 인자 = 기존 값 유지. 하드 범위는 state_guards.validate_edge_write 와 같다(저장 직전 그쪽이 다시 자른다)."""
    cur_b = existing["bond"] if existing else 0
    cur_t = existing["tension"] if existing else 0
    new_b = cur_b if bond is None else int(bond)
    new_t = cur_t if tension is None else int(tension)
    if origin in EDGE_CAPPED_ORIGINS:
        base_b, base_t = _edge_base(existing, turn, origin)
        if bond is not None:
            new_b = _clamp_int(new_b, base_b - EDGE_BOND_STEP_CAP, base_b + EDGE_BOND_STEP_CAP)
        if tension is not None:
            new_t = _clamp_int(new_t, base_t - EDGE_TENSION_DOWN_CAP, base_t + EDGE_TENSION_UP_CAP)
    return max(-100, min(100, new_b)), max(0, min(100, new_t))


def upsert_edge(channel_id: str, source: str, target: str, *,
                bond: Optional[int] = None, tension: Optional[int] = None,
                stance: Optional[str] = None, kind: Optional[str] = None,
                turn: Optional[int] = None, origin: str = "theoria") -> Optional[Dict[str, Any]]:
    """엣지 1행 upsert — 관계 쓰기의 유일한 관문. 쓰인 최종 엣지 dict 반환(실패 None).

    - None 인자는 "이 칸은 안 건드림"(기존 값 유지, 새 엣지면 0/''/NULL).
    - 하드 클램프(전 origin): bond −100~+100, tension 0~100.
    - 턴당 캡(origin ∈ EDGE_CAPPED_ORIGINS만): |Δbond| ≤ 5, Δtension ∈ [−5, +10].
      ooc/seed/npc_sheet_initial/batch 등은 면제 — LLM 판독의 흔들림만 자른다(P8b 방식).
    - history: 값(bond·tension·kind)이 직전 history와 달라졌을 때만 {turn,bond,tension,source} 추가, 최근 20.
    """
    if not channel_id or not source or not target or source == target:
        return None
    try:
        import state_guards
        existing = get_edge(channel_id, source, target)
        t = int(turn) if turn is not None else int((existing or {}).get("last_turn") or 0)
        new_b, new_t = edge_step_values(existing, t, bond, tension, origin)  # [2026-09-25] 순수 함수로 이사(식 무변경)
        cur_b = existing["bond"] if existing else 0      # 아래 [Relation] 전이 로그용(이전 값)
        cur_t = existing["tension"] if existing else 0
        payload = {
            "bond": new_b, "tension": new_t,
            "stance": (existing or {}).get("stance", "") if stance is None else stance,
            "kind": (existing or {}).get("kind") if kind is None else kind,
            "last_turn": t,
            "history": list((existing or {}).get("history") or []),
        }
        clean = state_guards.validate_edge_write(source, target, payload)
        if clean is None:
            return None
        hist = clean["history"]
        last = hist[-1] if hist else None
        if (last is None or last.get("bond") != clean["bond"] or last.get("tension") != clean["tension"]
                or (existing is not None and existing.get("kind") != clean["kind"])):
            hist.append({"turn": t, "bond": clean["bond"], "tension": clean["tension"],
                         "source": str(origin or "code")})
        clean["history"] = hist[-EDGE_HISTORY_CAP:]
        if not _ensure_schema(channel_id):
            return None
        conn = _get_conn(channel_id)
        if conn is None:
            return None
        conn.execute(
            "INSERT INTO relations (channel_id, source, target, bond, tension, stance, kind, last_turn, history) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(channel_id, source, target) DO UPDATE SET bond=excluded.bond, "
            "tension=excluded.tension, stance=excluded.stance, kind=excluded.kind, "
            "last_turn=excluded.last_turn, history=excluded.history",
            (channel_id, clean["source"], clean["target"], clean["bond"], clean["tension"],
             clean["stance"], clean["kind"], clean["last_turn"],
             json.dumps(clean["history"], ensure_ascii=False)),
        )
        conn.commit()
        if existing is None or existing["bond"] != clean["bond"] or existing["tension"] != clean["tension"]:
            logger.info("[Relation] %s->%s bond %s→%s tension %s→%s origin=%s",
                        source, target, cur_b, clean["bond"], cur_t, clean["tension"], origin)
        return clean
    except Exception as e:
        logger.warning(f"[SQLiteStore] upsert_edge 실패 (무시): {channel_id}/{source}->{target}: {e}")
        return None


def decay_edges(channel_id: str, turn: int) -> int:
    """감쇠 하나 — last_turn(마지막 관측) 기준.

    grace(config.RELATION_DECAY_GRACE) 턴을 넘겨 안 관측된 엣지는
    bond가 0으로 턴당 RELATION_DECAY_DEPTH(1)씩, tension이 RELATION_DECAY_TENSION(2)씩 내려간다(하한 0).
    ★멱등: 기준점은 마지막 관측값(history[-1])이고 이동폭은 경과 턴에서 계산한다 — 같은 턴에
      두 번 불려도 두 번 깎이지 않는다. 감쇠는 history에 안 적는다(관측이 아니다), last_turn도 안 민다.
    끄기: RELATION_DECAY_GRACE = 0. Returns: 값이 바뀐 엣지 수."""
    grace = int(getattr(config, "RELATION_DECAY_GRACE", 0) or 0)
    if grace <= 0 or not channel_id:
        return 0
    b_step = max(0, int(getattr(config, "RELATION_DECAY_DEPTH", 1) or 0))
    t_step = max(0, int(getattr(config, "RELATION_DECAY_TENSION", 2) or 0))
    changed = 0
    try:
        conn = _get_conn(channel_id) if _ensure_schema(channel_id) else None
        if conn is None:
            return 0
        for e in get_edges(channel_id):
            lt = e.get("last_turn")
            if lt is None:
                continue
            steps = int(turn) - int(lt) - grace
            if steps <= 0:
                continue
            hist = e.get("history") or []
            anchor = hist[-1] if hist and isinstance(hist[-1], dict) else {"bond": e["bond"], "tension": e["tension"]}
            ab, at = int(anchor.get("bond", 0) or 0), int(anchor.get("tension", 0) or 0)
            nb = (1 if ab > 0 else -1) * max(0, abs(ab) - b_step * steps)
            nt = max(0, at - t_step * steps)
            if nb == e["bond"] and nt == e["tension"]:
                continue
            conn.execute("UPDATE relations SET bond=?, tension=? WHERE channel_id=? AND source=? AND target=?",
                         (nb, nt, channel_id, e["source"], e["target"]))
            changed += 1
        if changed:
            conn.commit()
            logger.info("[Relation] decay %d edges (turn %s)", changed, turn)
    except Exception as e:
        logger.warning(f"[SQLiteStore] decay_edges 실패 (무시): {channel_id}: {e}")
    return changed


def rename_edge_entity(channel_id: str, old: str, new: str) -> int:
    """개명·병합 — old가 걸린 엣지를 new로 옮긴다. 충돌(new 쪽 엣지 이미 있음)이면 new 쪽 유지, old 행 삭제."""
    if not channel_id or not old or not new or old == new or not _ensure_schema(channel_id):
        return 0
    conn = _get_conn(channel_id)
    if conn is None:
        return 0
    moved = 0
    try:
        for e in get_edges(channel_id):
            if e["source"] != old and e["target"] != old:
                continue
            ns = new if e["source"] == old else e["source"]
            nt = new if e["target"] == old else e["target"]
            conn.execute("DELETE FROM relations WHERE channel_id=? AND source=? AND target=?",
                         (channel_id, e["source"], e["target"]))
            if ns == nt:
                continue
            conn.execute(
                "INSERT OR IGNORE INTO relations (channel_id, source, target, bond, tension, stance, kind, last_turn, history) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (channel_id, ns, nt, e["bond"], e["tension"], e["stance"], e["kind"], e["last_turn"],
                 json.dumps(e["history"], ensure_ascii=False)))
            moved += 1
        conn.commit()
    except Exception as ex:
        logger.warning(f"[SQLiteStore] rename_edge_entity 실패 (무시): {channel_id}: {ex}")
    return moved


def delete_edges(channel_id: str, name: str, source_only: bool = False) -> int:
    """name이 걸린 엣지 삭제(source_only면 name→* 만). 삭제 행 수."""
    if not channel_id or not name or not _ensure_schema(channel_id):
        return 0
    conn = _get_conn(channel_id)
    if conn is None:
        return 0
    try:
        if source_only:
            cur = conn.execute("DELETE FROM relations WHERE channel_id=? AND source=?", (channel_id, name))
        else:
            cur = conn.execute("DELETE FROM relations WHERE channel_id=? AND (source=? OR target=?)",
                               (channel_id, name, name))
        conn.commit()
        return cur.rowcount or 0
    except Exception as e:
        logger.warning(f"[SQLiteStore] delete_edges 실패 (무시): {channel_id}/{name}: {e}")
        return 0


def count_edges(channel_id: Optional[str] = None) -> int:
    """엣지 행 수. 검증용. 실패 시 -1."""
    if not channel_id:
        return _count_all("relations")
    if not _ensure_schema(channel_id):
        return -1
    conn = _get_conn(channel_id)
    if conn is None:
        return -1
    try:
        return int(conn.execute("SELECT COUNT(*) FROM relations WHERE channel_id=?", (channel_id,)).fetchone()[0])
    except Exception:
        return -1


# =========================================================
# [V10 Sprint 2-A] npc_knowledge — NPC 지식 테이블 API
# JSON dict 키 계약: knows/secrets_held/would_share/leak_risk/last_updated
#   - knows/secrets_held는 JSON array 컬럼 (set union 머지 의미론은 domain_manager 책임)
#   - DB 컬럼 updated_at ↔ JSON 키 "last_updated"
# =========================================================

def _row_to_knowledge(row) -> Dict[str, Any]:
    """npc_knowledge 행 → JSON knowledge dict 포맷."""
    try:
        knows = json.loads(row[0])
    except Exception:
        knows = []
    try:
        secrets = json.loads(row[1])
    except Exception:
        secrets = []
    # [V10 지식 lite] suspects/misbeliefs는 끝에 append (row[5]/row[6]). 구DB 마이그레이션 전이면 빈 배열.
    try:
        suspects = json.loads(row[5]) if len(row) > 5 and row[5] else []
    except Exception:
        suspects = []
    try:
        misbeliefs = json.loads(row[6]) if len(row) > 6 and row[6] else []
    except Exception:
        misbeliefs = []
    return {
        "knows": knows,
        "secrets_held": secrets,
        "would_share": bool(row[2]),
        "leak_risk": row[3],
        "last_updated": row[4],
        "suspects": suspects,
        "misbeliefs": misbeliefs,
    }

_KNOW_COLS = "knows, secrets_held, would_share, leak_risk, updated_at, suspects, misbeliefs"


def upsert_knowledge(channel_id: str, npc_name: str, kn: Dict[str, Any]) -> bool:
    """지식 1행 upsert (방벽 통과 후 호출). 실패해도 False (봇 무영향)."""
    if not channel_id or not npc_name or not isinstance(kn, dict):
        return False
    if not _ensure_schema(channel_id):
        return False
    conn = _get_conn(channel_id)
    if conn is None:
        return False
    try:
        conn.execute(
            f"INSERT INTO npc_knowledge (channel_id, npc_name, {_KNOW_COLS}) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(channel_id, npc_name) DO UPDATE SET "
            "knows=excluded.knows, secrets_held=excluded.secrets_held, "
            "would_share=excluded.would_share, leak_risk=excluded.leak_risk, "
            "updated_at=excluded.updated_at, "
            "suspects=excluded.suspects, misbeliefs=excluded.misbeliefs",
            (
                channel_id, npc_name,
                json.dumps(kn.get("knows", []), ensure_ascii=False),
                json.dumps(kn.get("secrets_held", []), ensure_ascii=False),
                1 if kn.get("would_share") else 0,
                kn.get("leak_risk", "none"),
                kn.get("last_updated", ""),
                json.dumps(kn.get("suspects", []), ensure_ascii=False),
                json.dumps(kn.get("misbeliefs", []), ensure_ascii=False),
            ),
        )
        conn.commit()
        return True
    except Exception as e:
        logger.warning(f"[SQLiteStore] upsert_knowledge 실패 (무시): {channel_id}/{npc_name}: {e}")
        return False


def upsert_knowledge_bulk(channel_id: str, all_kn: Dict[str, Dict[str, Any]]) -> bool:
    """여러 NPC 지식 일괄 upsert — propagate_npc_knowledge bulk 미러용. 단일 트랜잭션."""
    if not channel_id or not isinstance(all_kn, dict) or not all_kn:
        return False
    if not _ensure_schema(channel_id):
        return False
    conn = _get_conn(channel_id)
    if conn is None:
        return False
    try:
        rows = [
            (
                channel_id, name,
                json.dumps(kn.get("knows", []), ensure_ascii=False),
                json.dumps(kn.get("secrets_held", []), ensure_ascii=False),
                1 if kn.get("would_share") else 0,
                kn.get("leak_risk", "none"),
                kn.get("last_updated", ""),
                json.dumps(kn.get("suspects", []), ensure_ascii=False),
                json.dumps(kn.get("misbeliefs", []), ensure_ascii=False),
            )
            for name, kn in all_kn.items() if isinstance(kn, dict) and name
        ]
        conn.executemany(
            f"INSERT INTO npc_knowledge (channel_id, npc_name, {_KNOW_COLS}) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(channel_id, npc_name) DO UPDATE SET "
            "knows=excluded.knows, secrets_held=excluded.secrets_held, "
            "would_share=excluded.would_share, leak_risk=excluded.leak_risk, "
            "updated_at=excluded.updated_at, "
            "suspects=excluded.suspects, misbeliefs=excluded.misbeliefs",
            rows,
        )
        conn.commit()
        return True
    except Exception as e:
        logger.warning(f"[SQLiteStore] upsert_knowledge_bulk 실패 (무시): {channel_id}: {e}")
        return False


def read_knowledge_all(channel_id: str) -> Optional[Dict[str, Dict[str, Any]]]:
    """채널 전체 지식. 행 0개/실패 시 None (→ JSON 폴백)."""
    if not channel_id or not _ensure_schema(channel_id):
        return None
    conn = _get_conn(channel_id)
    if conn is None:
        return None
    try:
        cur = conn.execute(
            f"SELECT npc_name, {_KNOW_COLS} FROM npc_knowledge WHERE channel_id=?",
            (channel_id,),
        )
        rows = cur.fetchall()
        if not rows:
            return None
        return {r[0]: _row_to_knowledge(r[1:]) for r in rows}
    except Exception as e:
        logger.warning(f"[SQLiteStore] read_knowledge_all 실패: {channel_id}: {e}")
        return None


def read_knowledge(channel_id: str, npc_name: str) -> Optional[Dict[str, Any]]:
    """지식 단건 포인트 질의. 없거나 실패 시 None."""
    if not channel_id or not npc_name or not _ensure_schema(channel_id):
        return None
    conn = _get_conn(channel_id)
    if conn is None:
        return None
    try:
        cur = conn.execute(
            f"SELECT {_KNOW_COLS} FROM npc_knowledge WHERE channel_id=? AND npc_name=?",
            (channel_id, npc_name),
        )
        row = cur.fetchone()
        return _row_to_knowledge(row) if row else None
    except Exception as e:
        logger.warning(f"[SQLiteStore] read_knowledge 실패: {channel_id}/{npc_name}: {e}")
        return None


def delete_knowledge(channel_id: str, npc_name: str) -> bool:
    """지식 1행 삭제."""
    if not channel_id or not npc_name or not _ensure_schema(channel_id):
        return False
    conn = _get_conn(channel_id)
    if conn is None:
        return False
    try:
        cur = conn.execute(
            "DELETE FROM npc_knowledge WHERE channel_id=? AND npc_name=?",
            (channel_id, npc_name),
        )
        conn.commit()
        return cur.rowcount > 0
    except Exception as e:
        logger.warning(f"[SQLiteStore] delete_knowledge 실패 (무시): {channel_id}/{npc_name}: {e}")
        return False


def count_knowledge(channel_id: Optional[str] = None) -> int:
    """지식 행 수. 검증용. 실패 시 -1."""
    # [2026-09-14 W0] channel_id 없음 = 채널 폴더 전량 합산(레거시 모드면 종전 단일 파일).
    if not channel_id:
        return _count_all("npc_knowledge")
    if not _ensure_schema(channel_id):
        return -1
    conn = _get_conn(channel_id)
    if conn is None:
        return -1
    try:
        cur = conn.execute("SELECT COUNT(*) FROM npc_knowledge WHERE channel_id=?", (channel_id,))
        return int(cur.fetchone()[0])
    except Exception:
        return -1


# =========================================================
# [V10 Sprint 2-B] npcs — NPC 본체 (문서 컬럼 + 질의용 메타)
# data 컬럼(JSON 통짜)이 진실. source/status는 WHERE용 인덱스 사본 —
# 읽기 복원은 data만 사용 (불일치 위험 제거, spec §B-1).
# =========================================================

def upsert_npc(channel_id: str, npc_name: str, data: Dict[str, Any]) -> bool:
    """NPC 1행 upsert. data는 NPC dict 전체 (방벽 통과 후)."""
    if not channel_id or not npc_name or not isinstance(data, dict):
        return False
    if not _ensure_schema(channel_id):
        return False
    conn = _get_conn(channel_id)
    if conn is None:
        return False
    try:
        blob = json.dumps(data, ensure_ascii=False)
        conn.execute(
            "INSERT INTO npcs (channel_id, npc_name, source, status, data, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(channel_id, npc_name) DO UPDATE SET "
            "source=excluded.source, status=excluded.status, data=excluded.data, "
            "updated_at=excluded.updated_at",
            (
                channel_id, npc_name,
                str(data.get("source", "session")),
                str(data.get("status", "")),
                blob, time.time(),
            ),
        )
        conn.commit()
        return True
    except Exception as e:
        logger.warning(f"[SQLiteStore] upsert_npc 실패 (무시): {channel_id}/{npc_name}: {e}")
        return False


def rename_npc(channel_id: str, old_name: str, new_name: str, data: Dict[str, Any]) -> bool:
    """키 마이그레이션 미러 (update_npc의 비정규→정규 키 이동). DELETE old + upsert new, 한 트랜잭션."""
    if not channel_id or not old_name or not new_name or not isinstance(data, dict):
        return False
    if not _ensure_schema(channel_id):
        return False
    conn = _get_conn(channel_id)
    if conn is None:
        return False
    try:
        blob = json.dumps(data, ensure_ascii=False)
        conn.execute("DELETE FROM npcs WHERE channel_id=? AND npc_name=?", (channel_id, old_name))
        conn.execute(
            "INSERT INTO npcs (channel_id, npc_name, source, status, data, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(channel_id, npc_name) DO UPDATE SET "
            "source=excluded.source, status=excluded.status, data=excluded.data, "
            "updated_at=excluded.updated_at",
            (
                channel_id, new_name,
                str(data.get("source", "session")),
                str(data.get("status", "")),
                blob, time.time(),
            ),
        )
        conn.commit()
        return True
    except Exception as e:
        logger.warning(f"[SQLiteStore] rename_npc 실패 (무시): {channel_id}/{old_name}→{new_name}: {e}")
        return False


def bulk_upsert_npcs(channel_id: str, npcs: Dict[str, Dict[str, Any]]) -> bool:
    """NPC 전체 일괄 upsert — tick_all_cooldowns/리셋 등 bulk 미러. 단일 트랜잭션."""
    if not channel_id or not isinstance(npcs, dict) or not npcs:
        return False
    if not _ensure_schema(channel_id):
        return False
    conn = _get_conn(channel_id)
    if conn is None:
        return False
    try:
        now = time.time()
        rows = [
            (
                channel_id, name,
                str(data.get("source", "session")),
                str(data.get("status", "")),
                json.dumps(data, ensure_ascii=False), now,
            )
            for name, data in npcs.items() if isinstance(data, dict) and name
        ]
        conn.executemany(
            "INSERT INTO npcs (channel_id, npc_name, source, status, data, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(channel_id, npc_name) DO UPDATE SET "
            "source=excluded.source, status=excluded.status, data=excluded.data, "
            "updated_at=excluded.updated_at",
            rows,
        )
        conn.commit()
        return True
    except Exception as e:
        logger.warning(f"[SQLiteStore] bulk_upsert_npcs 실패 (무시): {channel_id}: {e}")
        return False


def read_npcs(channel_id: str) -> Optional[Dict[str, Dict[str, Any]]]:
    """채널 전체 NPC. 행 0개/실패 시 None (→ JSON 폴백). 복원은 data 컬럼만."""
    if not channel_id or not _ensure_schema(channel_id):
        return None
    conn = _get_conn(channel_id)
    if conn is None:
        return None
    try:
        cur = conn.execute("SELECT npc_name, data FROM npcs WHERE channel_id=?", (channel_id,))
        rows = cur.fetchall()
        if not rows:
            return None
        out = {}
        for name, blob in rows:
            try:
                out[name] = json.loads(blob)
            except Exception:
                continue
        return out if out else None
    except Exception as e:
        logger.warning(f"[SQLiteStore] read_npcs 실패: {channel_id}: {e}")
        return None


def read_npc(channel_id: str, npc_name: str) -> Optional[Dict[str, Any]]:
    """NPC 단건 포인트 질의 (정확한 키 — 별칭 해상도는 domain_manager 책임)."""
    if not channel_id or not npc_name or not _ensure_schema(channel_id):
        return None
    conn = _get_conn(channel_id)
    if conn is None:
        return None
    try:
        cur = conn.execute(
            "SELECT data FROM npcs WHERE channel_id=? AND npc_name=?",
            (channel_id, npc_name),
        )
        row = cur.fetchone()
        return json.loads(row[0]) if row else None
    except Exception as e:
        logger.warning(f"[SQLiteStore] read_npc 실패: {channel_id}/{npc_name}: {e}")
        return None


def delete_npc_row(channel_id: str, npc_name: str) -> bool:
    """NPC 1행 삭제."""
    if not channel_id or not npc_name or not _ensure_schema(channel_id):
        return False
    conn = _get_conn(channel_id)
    if conn is None:
        return False
    try:
        cur = conn.execute("DELETE FROM npcs WHERE channel_id=? AND npc_name=?", (channel_id, npc_name))
        conn.commit()
        return cur.rowcount > 0
    except Exception as e:
        logger.warning(f"[SQLiteStore] delete_npc_row 실패 (무시): {channel_id}/{npc_name}: {e}")
        return False


def delete_npcs_except_sources(channel_id: str, keep_sources: tuple) -> int:
    """keep_sources 외 NPC 일괄 삭제 — clear_session_npcs/세션 리셋 미러. 삭제 행 수 반환, 실패 -1."""
    if not channel_id or not keep_sources or not _ensure_schema(channel_id):
        return -1
    conn = _get_conn(channel_id)
    if conn is None:
        return -1
    try:
        ph = ",".join("?" * len(keep_sources))
        cur = conn.execute(
            f"DELETE FROM npcs WHERE channel_id=? AND source NOT IN ({ph})",
            (channel_id, *keep_sources),
        )
        conn.commit()
        return cur.rowcount
    except Exception as e:
        logger.warning(f"[SQLiteStore] delete_npcs_except_sources 실패 (무시): {channel_id}: {e}")
        return -1


def delete_channel_rows(channel_id: str) -> bool:
    """채널의 모든 SQLite 행 삭제.

    [2026-09-14 W0] 채널 모드에서는 **연결을 닫고 memory.db(-wal/-shm)를 지운다** —
    파일 하나가 채널 하나라 테이블 목록을 돌 이유가 없다. 레거시 모드
    (_DB_PATH 설정 = 옛 스모크)에서는 종전의 목록 삭제를 그대로 유지한다."""
    if not channel_id:
        return False
    # [2026-09-14 W3b] cache.db는 재생성 가능한 파생물 — 리셋이면 무조건 통째로 간다.
    try:
        delete_cache_db(channel_id)
    except Exception:
        pass
    if not _DB_PATH:
        path = _db_path_for(channel_id)
        if not path:
            return False
        close_channel(channel_id)
        ok = True
        for suffix in ("", "-wal", "-shm"):
            f = path + suffix
            if os.path.exists(f):
                try:
                    os.remove(f)
                except (OSError, PermissionError) as e:
                    logger.warning(f"[SQLiteStore] db 파일 삭제 실패 (무시): {f}: {e}")
                    ok = False
        return ok
    if not _ensure_schema(channel_id):
        return False
    conn = _get_conn(channel_id)
    if conn is None:
        return False
    try:
        for table in ("sessions", "relations", "npc_knowledge", "npcs",
                      "history_log", "fermented_history", "deep_memory", "dai_logs",
                      "offscreen_ledger", "emotion_log", "turn_snapshot", "attitude_log",
                      "soma_log", "notebook_log",  # [2026-08-11 soma 지속 / 09-06 notebook v2]
                      # [2026-09-14] 리셋 목록에서 빠져 있던 세션 파생 5 + S5a fact_sources.
                      #   channel_id가 바뀌는 리셋이라 실해는 없었지만 옛 행이 영구 잔존했다.
                      "secret_ledger", "turn_mail", "retry_snapshot", "reader_log", "autonomy_log",
                      "fact_sources",
                      "thread_log",  # [2026-09-25 스레드 장부]
                      # [2026-09-14 W1] 위키 페이지 3테이블 — 레거시 모드에서만 목록 삭제
                      #   (채널 모드는 파일 삭제라 자동). 클리어(§6 W4)와는 별개.
                      "pages", "sections", "section_history"):
            conn.execute(f"DELETE FROM {table} WHERE channel_id=?", (channel_id,))
        conn.commit()
        return True
    except Exception as e:
        logger.warning(f"[SQLiteStore] delete_channel_rows 실패 (무시): {channel_id}: {e}")
        return False


# =========================================================
# [V10 Sprint 3] 대화 이력 도메인 API
# =========================================================

def append_history(channel_id: str, entry: Dict[str, Any]) -> bool:
    """history_log에 1행 append (방벽 통과 후). 영구 기록 — DELETE는 리셋과
    !다시 워터마크 트림(trim_logs_to_watermarks)뿐. 후자는 **폐기된 턴**만 회수한다."""
    if not channel_id or not isinstance(entry, dict):
        return False
    if not _ensure_schema(channel_id):
        return False
    conn = _get_conn(channel_id)
    if conn is None:
        return False
    try:
        gt = entry.get("game_time")
        conn.execute(
            "INSERT INTO history_log (channel_id, role, content, message_id, game_time, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                channel_id, entry.get("role", ""), entry.get("content", ""),
                entry.get("message_id"),
                json.dumps(gt, ensure_ascii=False) if isinstance(gt, dict) else None,
                time.time(),
            ),
        )
        conn.commit()
        return True
    except Exception as e:
        logger.warning(f"[SQLiteStore] append_history 실패 (무시): {channel_id}: {e}")
        return False


def _row_to_history(row) -> Dict[str, Any]:
    entry: Dict[str, Any] = {"role": row[0], "content": row[1]}
    if row[2] is not None:
        entry["message_id"] = row[2]
    if row[3]:
        try:
            entry["game_time"] = json.loads(row[3])
        except Exception:
            pass
    return entry


def read_history_tail(channel_id: str, n: int = 50) -> Optional[list]:
    """최근 N개 엔트리 (오래된→최신 순). 행 0개/실패 시 None."""
    if not channel_id or n <= 0 or not _ensure_schema(channel_id):
        return None
    conn = _get_conn(channel_id)
    if conn is None:
        return None
    try:
        cur = conn.execute(
            "SELECT role, content, message_id, game_time FROM history_log "
            "WHERE channel_id=? ORDER BY id DESC LIMIT ?",
            (channel_id, int(n)),
        )
        rows = cur.fetchall()
        if not rows:
            return None
        return [_row_to_history(r) for r in reversed(rows)]
    except Exception as e:
        logger.warning(f"[SQLiteStore] read_history_tail 실패: {channel_id}: {e}")
        return None


def search_history_log(channel_id: str, query: str, limit: int = 20) -> list:
    """전체 로그 텍스트 검색 (trim된 과거 포함) — B의 실증, RAG/틱 루프 토대. 최신순."""
    if not channel_id or not query or not _ensure_schema(channel_id):
        return []
    conn = _get_conn(channel_id)
    if conn is None:
        return []
    try:
        cur = conn.execute(
            "SELECT role, content, message_id, game_time FROM history_log "
            "WHERE channel_id=? AND content LIKE ? ORDER BY id DESC LIMIT ?",
            (channel_id, f"%{query}%", int(limit)),
        )
        return [_row_to_history(r) for r in cur.fetchall()]
    except Exception as e:
        logger.warning(f"[SQLiteStore] search_history_log 실패: {channel_id}: {e}")
        return []


def count_history(channel_id: Optional[str] = None) -> int:
    """history_log 행 수. 실패 시 -1."""
    # [2026-09-14 W0] channel_id 없음 = 채널 폴더 전량 합산(레거시 모드면 종전 단일 파일).
    if not channel_id:
        return _count_all("history_log")
    if not _ensure_schema(channel_id):
        return -1
    conn = _get_conn(channel_id)
    if conn is None:
        return -1
    try:
        cur = conn.execute("SELECT COUNT(*) FROM history_log WHERE channel_id=?", (channel_id,))
        return int(cur.fetchone()[0])
    except Exception:
        return -1


def clear_history_log(channel_id: str) -> bool:
    """history_log 채널 행 전체 삭제 — 리셋=완전 새 이야기 (사용자 결정 2026-06-10)."""
    if not channel_id or not _ensure_schema(channel_id):
        return False
    conn = _get_conn(channel_id)
    if conn is None:
        return False
    try:
        conn.execute("DELETE FROM history_log WHERE channel_id=?", (channel_id,))
        conn.commit()
        return True
    except Exception as e:
        logger.warning(f"[SQLiteStore] clear_history_log 실패 (무시): {channel_id}: {e}")
        return False


def sync_fermented(channel_id: str, entries: list) -> bool:
    """fermented_history 전체 교체 미러 (발효의 리스트 교체 의미론 그대로). 단일 트랜잭션."""
    if not channel_id or not isinstance(entries, list) or not _ensure_schema(channel_id):
        return False
    conn = _get_conn(channel_id)
    if conn is None:
        return False
    try:
        now = time.time()
        conn.execute("DELETE FROM fermented_history WHERE channel_id=?", (channel_id,))
        if entries:
            conn.executemany(
                "INSERT INTO fermented_history (channel_id, seq, entry, updated_at) VALUES (?, ?, ?, ?)",
                [(channel_id, i, json.dumps(e, ensure_ascii=False), now) for i, e in enumerate(entries)],
            )
        conn.commit()
        return True
    except Exception as e:
        logger.warning(f"[SQLiteStore] sync_fermented 실패 (무시): {channel_id}: {e}")
        return False


def read_fermented(channel_id: str) -> Optional[list]:
    """fermented_history 조회 (seq 순).

    [2026-09-05 P4] 없음 != 실패. 행 0개 → [] (정상, 데이터 없음). 연결/스키마/쿼리 실패 → None.
    행이 정본이 된 뒤로는 이 구분이 폴백 판단의 근거다."""
    if not channel_id or not _ensure_schema(channel_id):
        return None
    conn = _get_conn(channel_id)
    if conn is None:
        return None
    try:
        cur = conn.execute(
            "SELECT entry FROM fermented_history WHERE channel_id=? ORDER BY seq",
            (channel_id,),
        )
        rows = cur.fetchall()
        out = []
        for _seq, (blob,) in enumerate(rows):
            try:
                out.append(json.loads(blob))
            except Exception:
                # [2026-09-05 P4] 행이 정본이 된 뒤로 이 스킵은 조용한 데이터 손실이다 — 소리는 낸다.
                logger.warning("[SQLiteStore] fermented entry parse failed ch=%s seq=%s — skipped",
                               channel_id, _seq)
        return out
    except Exception as e:
        logger.warning(f"[SQLiteStore] read_fermented 실패: {channel_id}: {e}")
        return None


def sync_deep(channel_id: str, narrative: str, data: Dict[str, Any]) -> bool:
    """deep_memory upsert 미러."""
    if not channel_id or not _ensure_schema(channel_id):
        return False
    conn = _get_conn(channel_id)
    if conn is None:
        return False
    try:
        conn.execute(
            "INSERT INTO deep_memory (channel_id, narrative, data, updated_at) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(channel_id) DO UPDATE SET narrative=excluded.narrative, "
            "data=excluded.data, updated_at=excluded.updated_at",
            (
                channel_id,
                narrative if isinstance(narrative, str) else str(narrative or ""),
                json.dumps(data if isinstance(data, dict) else {}, ensure_ascii=False),
                time.time(),
            ),
        )
        conn.commit()
        return True
    except Exception as e:
        logger.warning(f"[SQLiteStore] sync_deep 실패 (무시): {channel_id}: {e}")
        return False


def append_dai_log(channel_id: str, turn: int, dai: Dict[str, Any], keep: int = 100) -> bool:
    """DAI 스냅샷 1턴 저장 + 채널당 최근 keep개 롤링. 실패해도 봇 무영향."""
    if not channel_id or not isinstance(dai, dict) or not dai:
        return False
    if not _ensure_schema(channel_id):
        return False
    conn = _get_conn(channel_id)
    if conn is None:
        return False
    try:
        conn.execute(
            "INSERT INTO dai_logs (channel_id, turn, dai, created_at) VALUES (?, ?, ?, ?)",
            (channel_id, int(turn), json.dumps(dai, ensure_ascii=False, default=str), time.time()),
        )
        # 롤링: 최근 keep개 초과분 삭제
        conn.execute(
            "DELETE FROM dai_logs WHERE channel_id=? AND id NOT IN "
            "(SELECT id FROM dai_logs WHERE channel_id=? ORDER BY id DESC LIMIT ?)",
            (channel_id, channel_id, int(keep)),
        )
        conn.commit()
        return True
    except Exception as e:
        logger.warning(f"[SQLiteStore] append_dai_log 실패 (무시): {channel_id}: {e}")
        return False


def save_retry_snapshot(channel_id: str, turn: int, snapshot: Dict[str, Any],
                        marks: Optional[Dict[str, int]] = None) -> bool:
    """[!다시] 턴-직전 도메인 스냅샷 영속화 (채널당 1개 REPLACE, 봇 재시작에도 보존). 실패 무해.

    [2026-08-12 !다시 유령 정리] marks = snapshot_log_watermarks() 결과(로그 테이블별 max(id)).
    data와 **별 컬럼**인 이유: data는 read_retry_snapshot이 그대로 도메인으로 복원한다."""
    if not channel_id or not isinstance(snapshot, dict) or not snapshot:
        return False
    if not _ensure_schema(channel_id):
        return False
    conn = _get_conn(channel_id)
    if conn is None:
        return False
    try:
        conn.execute(
            "INSERT OR REPLACE INTO retry_snapshot (channel_id, turn, data, created_at, marks) "
            "VALUES (?, ?, ?, ?, ?)",
            (channel_id, int(turn), json.dumps(snapshot, ensure_ascii=False, default=str), time.time(),
             json.dumps(marks or {}, ensure_ascii=False)),
        )
        conn.commit()
        return True
    except Exception as e:
        logger.warning(f"[SQLiteStore] save_retry_snapshot 실패 (무시): {channel_id}: {e}")
        return False


def read_retry_snapshot(channel_id: str) -> Optional[Dict[str, Any]]:
    """[!다시] 영속화된 스냅샷 조회 (인메모리 miss 시 폴백). 없으면 None."""
    if not channel_id or not _ensure_schema(channel_id):
        return None
    conn = _get_conn(channel_id)
    if conn is None:
        return None
    try:
        cur = conn.execute("SELECT data FROM retry_snapshot WHERE channel_id=?", (channel_id,))
        row = cur.fetchone()
        if row and row[0]:
            return json.loads(row[0])
    except Exception as e:
        logger.warning(f"[SQLiteStore] read_retry_snapshot 실패: {channel_id}: {e}")
    return None


def read_retry_marks(channel_id: str) -> Dict[str, int]:
    """[!다시] 영속 스냅샷에 동봉된 로그 워터마크. 구 스냅샷/없음이면 빈 dict = trim no-op."""
    if not channel_id or not _ensure_schema(channel_id):
        return {}
    conn = _get_conn(channel_id)
    if conn is None:
        return {}
    try:
        cur = conn.execute("SELECT marks FROM retry_snapshot WHERE channel_id=?", (channel_id,))
        row = cur.fetchone()
        if row and row[0]:
            m = json.loads(row[0])
            if isinstance(m, dict):
                return {str(k): int(v) for k, v in m.items()}
    except Exception as e:
        logger.warning(f"[SQLiteStore] read_retry_marks 실패: {channel_id}: {e}")
    return {}


# =========================================================
# [2026-08-12 !다시 유령 정리] 로그 워터마크 — 폐기 턴이 SQLite에 남긴 유령 행 회수
# 병: retry_last는 JSON 도메인 스냅샷만 되돌리고 SQLite append 로그는 무접촉이라,
#     폐기된 턴의 행이 남은 채 재실행분이 또 쌓였다(아크 창·narrative_queries 창·
#     리더 recall 풀 오염). 스냅샷 시점 max(id)를 동봉해 두고 복원 직후 초과분을 지운다.
# 대상 = AUTOINCREMENT id + channel_id를 가진 **행 추가형** 로그 8종.
#   제외: turn_snapshot — PK(channel_id, turn) upsert형이라 재실행이 같은 턴 행을 REPLACE(자가치유),
#         애초에 id 컬럼이 없어 워터마크가 성립 안 함.
#   제외: sessions/npcs/relations/npc_knowledge/secret_ledger/fermented_history/
#         deep_memory/retry_snapshot — 상태·설정 테이블(upsert형).
# =========================================================

_RETRY_LOG_TABLES = (
    "history_log", "dai_logs", "offscreen_ledger", "emotion_log",
    "attitude_log", "soma_log", "autonomy_log", "reader_log",
    # [2026-08-16 도착물 라우트] turn_mail — id 컬럼 있는 행 추가형. !다시가 산문 메시지를
    # 지우면 그 message_id를 가리키던 도착물 행은 영영 못 여는 유령이 된다 → 같이 회수.
    "turn_mail",
    # [2026-09-24 감사] notebook_log — P12 append 기록(🎞)·expr `_record`·경계 일지가 쌓는 **행 정본**.
    #   빠져 있어 !다시 뒤에도 폐기된 턴의 기록 줄이 남고 재실행분이 또 쌓였다.
    "notebook_log",
    # [2026-09-25 스레드 장부] 이벤트 행이 정본 — 트림이 곧 상태 되감기.
    "thread_log",
)


def snapshot_log_watermarks(channel_id: str) -> Dict[str, int]:
    """로그성 테이블별 현재 max(id) (행 없으면 0). 실패 시 빈 dict = trim no-op."""
    if not channel_id or not _ensure_schema(channel_id):
        return {}
    conn = _get_conn(channel_id)
    if conn is None:
        return {}
    try:
        marks: Dict[str, int] = {}
        for t in _RETRY_LOG_TABLES:
            cur = conn.execute(f"SELECT COALESCE(MAX(id), 0) FROM {t} WHERE channel_id=?", (channel_id,))
            marks[t] = int(cur.fetchone()[0] or 0)
        return marks
    except Exception as e:
        logger.warning(f"[SQLiteStore] snapshot_log_watermarks 실패 (무시): {channel_id}: {e}")
        return {}


def trim_logs_to_watermarks(channel_id: str, marks: Dict[str, int]) -> int:
    """워터마크 초과분 삭제 → 삭제 행 수. 실패해도 raise 안 함.
    테이블명은 _RETRY_LOG_TABLES 화이트리스트로만 — marks 키가 SQL 조각이 되지 않는 단일 관문."""
    if not channel_id or not isinstance(marks, dict) or not marks:
        return 0
    if not _ensure_schema(channel_id):
        return 0
    conn = _get_conn(channel_id)
    if conn is None:
        return 0
    total = 0
    try:
        for t in _RETRY_LOG_TABLES:
            if t not in marks:
                continue  # 부분 marks(구 스냅샷/신설 테이블) — 모르는 자리는 건드리지 않는다
            try:
                mark = int(marks[t])
            except Exception:
                continue
            cur = conn.execute(f"DELETE FROM {t} WHERE channel_id=? AND id>?", (channel_id, mark))
            total += max(0, int(cur.rowcount or 0))
        conn.commit()
        return total
    except Exception as e:
        logger.warning(f"[SQLiteStore] trim_logs_to_watermarks 실패 (무시): {channel_id}: {e}")
        return total


_THREAD_COLS = ("turn", "thread_id", "op", "kind", "title", "parties", "outcome", "remaining",
                "due_start", "due_end", "due_prec", "quote")


def append_thread_events(channel_id: str, rows: List[Dict[str, Any]]) -> int:
    """[2026-09-25 스레드 장부] 관문(thread_ledger.apply_events)을 통과한 이벤트 행 적립 → 쓴 행 수. 롤링 없음."""
    if not channel_id or not rows or not _ensure_schema(channel_id):
        return 0
    conn = _get_conn(channel_id)
    if conn is None:
        return 0
    try:
        n = 0
        now = time.time()
        for r in rows:
            if not isinstance(r, dict) or not r.get("thread_id") or not r.get("op"):
                continue
            conn.execute(
                "INSERT INTO thread_log (channel_id, turn, thread_id, op, kind, title, parties, outcome, "
                "remaining, due_start, due_end, due_prec, quote, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (channel_id, int(r.get("turn") or 0), str(r["thread_id"]), str(r["op"]),
                 str(r.get("kind") or ""), str(r.get("title") or ""), str(r.get("parties") or "[]"),
                 str(r.get("outcome") or ""), str(r.get("remaining") or ""),
                 r.get("due_start"), r.get("due_end"), str(r.get("due_prec") or ""),
                 str(r.get("quote") or ""), now),
            )
            n += 1
        conn.commit()
        return n
    except Exception as e:
        logger.warning(f"[SQLiteStore] append_thread_events 실패 (무시): {channel_id}: {e}")
        return 0


def read_thread_log(channel_id: str) -> List[Dict[str, Any]]:
    """[2026-09-25 스레드 장부] 채널 thread_log 전 행(id 오름차순) — fold 입력. 실패 시 []."""
    if not channel_id or not _ensure_schema(channel_id):
        return []
    conn = _get_conn(channel_id)
    if conn is None:
        return []
    try:
        cur = conn.execute("SELECT id, " + ", ".join(_THREAD_COLS) +
                           " FROM thread_log WHERE channel_id=? ORDER BY id", (channel_id,))
        return [dict(zip(("id",) + _THREAD_COLS, row)) for row in cur.fetchall()]
    except Exception as e:
        logger.warning(f"[SQLiteStore] read_thread_log 실패 (무시): {channel_id}: {e}")
        return []


def append_ledger(channel_id: str, entry: Dict[str, Any], keep: int = 200) -> bool:
    """막간 장부 1행 기록 (validate_ledger_write 통과 후). 채널당 최근 keep행 롤링."""
    if not channel_id or not isinstance(entry, dict):
        return False
    if not _ensure_schema(channel_id):
        return False
    conn = _get_conn(channel_id)
    if conn is None:
        return False
    try:
        conn.execute(
            "INSERT INTO offscreen_ledger (channel_id, npc_name, act, summary, motive, route, "
            "traces, mood_delta, game_span, consumed, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (
                channel_id, entry["npc_name"], entry["act"], entry["summary"],
                entry.get("motive", ""), entry.get("route", ""),
                json.dumps(entry.get("traces", []), ensure_ascii=False),
                entry.get("mood_delta", ""), entry.get("game_span", ""),
                1 if entry.get("consumed") else 0, time.time(),
            ),
        )
        conn.execute(
            "DELETE FROM offscreen_ledger WHERE channel_id=? AND id NOT IN "
            "(SELECT id FROM offscreen_ledger WHERE channel_id=? ORDER BY id DESC LIMIT ?)",
            (channel_id, channel_id, int(keep)),
        )
        conn.commit()
        return True
    except Exception as e:
        logger.warning(f"[SQLiteStore] append_ledger 실패 (무시): {channel_id}: {e}")
        return False


def read_ledger_tail(channel_id: str, n: int = 20) -> list:
    """최근 N행 (오래된→최신). 디버그/관측용."""
    if not channel_id or n <= 0 or not _ensure_schema(channel_id):
        return []
    conn = _get_conn(channel_id)
    if conn is None:
        return []
    try:
        cur = conn.execute(
            "SELECT npc_name, act, summary, motive, route, traces, mood_delta, consumed "
            "FROM offscreen_ledger WHERE channel_id=? ORDER BY id DESC LIMIT ?",
            (channel_id, int(n)),
        )
        out = []
        for r in reversed(cur.fetchall()):
            try:
                traces = json.loads(r[5])
            except Exception:
                traces = []
            out.append({"npc_name": r[0], "act": r[1], "summary": r[2], "motive": r[3],
                        "route": r[4], "traces": traces, "mood_delta": r[6], "consumed": bool(r[7])})
        return out
    except Exception as e:
        logger.warning(f"[SQLiteStore] read_ledger_tail 실패: {channel_id}: {e}")
        return []


def clear_ledger(channel_id: str) -> bool:
    """막간 장부 채널 행 삭제 — 리셋=완전 새 이야기."""
    if not channel_id or not _ensure_schema(channel_id):
        return False
    conn = _get_conn(channel_id)
    if conn is None:
        return False
    try:
        conn.execute("DELETE FROM offscreen_ledger WHERE channel_id=?", (channel_id,))
        conn.commit()
        return True
    except Exception as e:
        logger.warning(f"[SQLiteStore] clear_ledger 실패 (무시): {channel_id}: {e}")
        return False


def append_notebook_log(channel_id: str, user_id: str, section: str, content: str,
                        game_time: str = "", turn_index: int = 0) -> bool:
    """노트북 행 1건 적립(append-only). 실패해도 예외 안 던짐(봇 안전)."""
    if not channel_id or not section or not str(content or "").strip():
        return False
    if not _ensure_schema(channel_id):
        return False
    conn = _get_conn(channel_id)
    if conn is None:
        return False
    try:
        conn.execute(
            "INSERT INTO notebook_log (channel_id, user_id, section, content, game_time, "
            "turn_index, created_at) VALUES (?,?,?,?,?,?,?)",
            (channel_id, str(user_id or ""), str(section), str(content).strip(),
             str(game_time or ""), int(turn_index or 0), time.time()),
        )
        conn.commit()
        return True
    except Exception as e:
        logger.warning(f"[SQLiteStore] append_notebook_log 실패 (무시): {channel_id}: {e}")
        return False


def read_notebook_tail(channel_id: str, section: str = "", n: int = 20) -> list:
    """최근 N행 (오래된→최신). section 빈 문자열이면 전 섹션."""
    if not channel_id or n <= 0 or not _ensure_schema(channel_id):
        return []
    conn = _get_conn(channel_id)
    if conn is None:
        return []
    try:
        if section:
            q = ("SELECT user_id, section, content, game_time, turn_index, created_at "
                 "FROM notebook_log WHERE channel_id=? AND section=? ORDER BY id DESC LIMIT ?")
            args = (channel_id, str(section), int(n))
        else:
            q = ("SELECT user_id, section, content, game_time, turn_index, created_at "
                 "FROM notebook_log WHERE channel_id=? ORDER BY id DESC LIMIT ?")
            args = (channel_id, int(n))
        cur = conn.execute(q, args)
        return [{"user_id": r[0], "section": r[1], "content": r[2], "game_time": r[3],
                 "turn_index": r[4], "created_at": r[5]} for r in reversed(cur.fetchall())]
    except Exception as e:
        logger.warning(f"[SQLiteStore] read_notebook_tail 실패: {channel_id}: {e}")
        return []


def write_reader_log(channel_id: str, turn: int, digest: Dict[str, Any], dropped: int = 0,
                     keep: Optional[int] = None) -> bool:
    """[Reader-GM] 턴별 독자 다이제스트 적립. 실패해도 봇 무영향.

    [2026-08-11 리더 §7 → 당일 정정(레티어스)] **무캡이 기본이 맞다** — reader_log는
    계측 로그(keep 트림 계열)가 아니라 history_log와 같은 **영구 사료**(독자 공책의 원본.
    챕터 회고 등 미래 소비자의 재료). 읽기는 항상 LIMIT≤40이라 조회 비용 불변, DB만 자람.
    keep>0 설정 시에만 롤링(손잡이 잔존, 기본 0=무캡)."""
    if not channel_id or not _ensure_schema(channel_id):
        return False
    conn = _get_conn(channel_id)
    if conn is None:
        return False
    if keep is None:
        keep = int(getattr(config, "READER_LOG_KEEP", 0) or 0)
    try:
        conn.execute(
            "INSERT INTO reader_log (channel_id, turn, digest, dropped, created_at) VALUES (?, ?, ?, ?, ?)",
            (channel_id, int(turn), json.dumps(digest, ensure_ascii=False), int(dropped), time.time()),
        )
        if keep > 0:
            conn.execute(
                "DELETE FROM reader_log WHERE channel_id=? AND id NOT IN "
                "(SELECT id FROM reader_log WHERE channel_id=? ORDER BY id DESC LIMIT ?)",
                (channel_id, channel_id, int(keep)),
            )
        conn.commit()
        return True
    except Exception as e:
        logger.warning(f"[SQLiteStore] write_reader_log 실패 (무시): {channel_id}: {e}")
        return False


def read_reader_log_tail(channel_id: str, limit: int = 5) -> list:
    """[Reader-GM] 최근 N턴 독자 다이제스트 (오래된→최신). [(turn, digest_dict), ...].

    [2026-08-11 리더 §7] 소비자 정정: 사람(로그 대조)뿐이던 Stage 0은 끝났다 — 현행
    READER_GM_FEED=1에서 narrative_queries.reader_persistence·story_director 거부권·
    reader_gm 자기 노트북/예측 채점 등 13경로가 이 tail을 읽는다(항상 LIMIT≤40)."""
    if not channel_id or limit <= 0 or not _ensure_schema(channel_id):
        return []
    conn = _get_conn(channel_id)
    if conn is None:
        return []
    try:
        cur = conn.execute(
            "SELECT turn, digest FROM reader_log WHERE channel_id=? ORDER BY id DESC LIMIT ?",
            (channel_id, limit),
        )
        rows = cur.fetchall()
        out = []
        for turn, blob in reversed(rows):
            try:
                out.append((int(turn), json.loads(blob)))
            except Exception:
                continue
        return out
    except Exception as e:
        logger.warning(f"[SQLiteStore] read_reader_log_tail 실패: {channel_id}: {e}")
        return []


# =========================================================
# [V10 Secret Ledger] CRUD (에로스 타워 E3, 2026-07-14)
# JSON 키 계약: truth/surface/owners/knowers/suspecters/cannot_know/
#   leak_pressure/reveal_gate/risk_if_revealed/status/canon_level/turn_count
# =========================================================

_SECRET_COLS = ("truth, surface, owners, knowers, suspecters, cannot_know, "
                "leak_pressure, reveal_gate, risk_if_revealed, status, canon_level, "
                "turn_count, updated_at, reader_exposure, "
                # [2026-08-11 리더 소비자] reader_exposure / [2026-08-12 !다시 유령 정리] _turn — 둘 다 말미 추가
                "reader_exposure_turn")


def _row_to_secret(row) -> Dict[str, Any]:
    def _j(idx, default):
        try:
            v = json.loads(row[idx]) if row[idx] else default
            return v if isinstance(v, list) else default
        except Exception:
            return default
    return {
        "secret_id": row[0],
        "truth": row[1] or "",
        "surface": row[2] or "",
        "owners": _j(3, []),
        "knowers": _j(4, []),
        "suspecters": _j(5, []),
        "cannot_know": _j(6, []),
        "leak_pressure": int(row[7] or 0),
        "reveal_gate": row[8] or "",
        "risk_if_revealed": row[9] or "",
        "status": row[10] or "kept",
        "canon_level": row[11] or "established",
        "turn_count": int(row[12] or 0),
        "updated_at": row[13] or "",
        # [2026-08-11 리더 소비자] 독자 관측 누적 — leak_pressure 재계산의 가산항 원천
        "reader_exposure": int(row[14] or 0) if len(row) > 14 else 0,
        # [2026-08-12 !다시 유령 정리] 마지막 가산 턴 — 행 내부 도장(도메인 롤백 무관)
        "reader_exposure_turn": int(row[15] or 0) if len(row) > 15 else 0,
    }


def upsert_secret(channel_id: str, entry: Dict[str, Any]) -> bool:
    """비밀 1행 upsert. secret_id 필수. 실패해도 False (봇 무영향).
    게이트 ③: revealed 행은 kept로 되돌리지 않음 — status 후퇴는 여기서 차단."""
    sid = (entry or {}).get("secret_id", "")
    if not channel_id or not sid or not entry.get("truth"):
        return False
    if not _ensure_schema(channel_id):
        return False
    conn = _get_conn(channel_id)
    if conn is None:
        return False
    try:
        # 게이트 ③: 기존 status가 revealed/retired면 kept/leaking으로 후퇴 금지
        cur = conn.execute(
            "SELECT status FROM secret_ledger WHERE channel_id=? AND secret_id=?",
            (channel_id, sid))
        old = cur.fetchone()
        new_status = entry.get("status", "kept")
        if old and old[0] in ("revealed", "retired") and new_status in ("kept", "leaking"):
            new_status = old[0]
        conn.execute(
            f"INSERT INTO secret_ledger (channel_id, secret_id, {_SECRET_COLS}) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(channel_id, secret_id) DO UPDATE SET "
            "truth=excluded.truth, surface=excluded.surface, owners=excluded.owners, "
            "knowers=excluded.knowers, suspecters=excluded.suspecters, "
            "cannot_know=excluded.cannot_know, leak_pressure=excluded.leak_pressure, "
            "reveal_gate=excluded.reveal_gate, risk_if_revealed=excluded.risk_if_revealed, "
            "status=excluded.status, canon_level=excluded.canon_level, "
            "turn_count=excluded.turn_count, updated_at=excluded.updated_at, "
            "reader_exposure=excluded.reader_exposure, "
            "reader_exposure_turn=excluded.reader_exposure_turn",
            (
                channel_id, sid,
                str(entry.get("truth", "")),
                str(entry.get("surface", "")),
                json.dumps(entry.get("owners", []), ensure_ascii=False),
                json.dumps(entry.get("knowers", []), ensure_ascii=False),
                json.dumps(entry.get("suspecters", []), ensure_ascii=False),
                json.dumps(entry.get("cannot_know", []), ensure_ascii=False),
                int(entry.get("leak_pressure", 0)),
                str(entry.get("reveal_gate", "")),
                str(entry.get("risk_if_revealed", "")),
                new_status,
                str(entry.get("canon_level", "established")),
                int(entry.get("turn_count", 0)),
                entry.get("updated_at", ""),
                int(entry.get("reader_exposure", 0) or 0),  # [2026-08-11 리더 소비자]
                int(entry.get("reader_exposure_turn", 0) or 0),  # [2026-08-12 !다시 유령 정리]
            ),
        )
        conn.commit()
        return True
    except Exception as e:
        logger.warning(f"[SQLiteStore] upsert_secret 실패 (무시): {channel_id}/{sid}: {e}")
        return False


def read_secrets(channel_id: str, include_closed: bool = False) -> list:
    """채널 비밀 전체. 기본은 kept/leaking만 (revealed/retired 제외)."""
    if not channel_id or not _ensure_schema(channel_id):
        return []
    conn = _get_conn(channel_id)
    if conn is None:
        return []
    try:
        q = f"SELECT secret_id, {_SECRET_COLS} FROM secret_ledger WHERE channel_id=?"
        if not include_closed:
            q += " AND status IN ('kept','leaking')"
        cur = conn.execute(q, (channel_id,))
        return [_row_to_secret(r) for r in cur.fetchall()]
    except Exception as e:
        logger.warning(f"[SQLiteStore] read_secrets 실패 (무시): {channel_id}: {e}")
        return []


def clear_session_scoped(channel_id: str) -> bool:
    """세션 파생 테이블 채널 행 일괄 삭제 — !클리어 혼입 수리 (2026-07-05).

    발단: !클리어 후 첫 턴에 옛 세션 잔재 관측(AttitudeGate cooldown -64 / npc_knowledge 6 facts /
    서사 콜 계측이 옛 dai_logs·emotion_log를 급식받아 'Deep(은색 캔 약속)' 혼입).
    대상 8테이블 = 전부 플레이 파생. 여기서 안 지우는 것: sessions(채널 설정)·npcs(reset의
    delete_npcs_except_sources가 별도 처리)·history_log/offscreen_ledger(기존 clear_* 담당)·
    fermented_history/deep_memory(save_domain 빈 스냅샷 미러 담당).
    """
    if not channel_id or not _ensure_schema(channel_id):
        return False
    conn = _get_conn(channel_id)
    if conn is None:
        return False
    tables = (
        "relations", "npc_knowledge", "dai_logs", "emotion_log",
        "turn_snapshot", "attitude_log", "autonomy_log", "retry_snapshot",
        "soma_log",  # [2026-08-11 soma 지속] 몸 상태 전이도 세션 파생
        "reader_log",  # [Reader-GM] 독자 노트도 세션 파생
        "secret_ledger",  # [V10 Secret Ledger] 비밀 원장도 세션 파생 (2026-07-14)
        "turn_mail",  # [2026-08-16 도착물 라우트] 도착물은 그 턴 전용 — 세션 파생
        "notebook_log",  # [notebook v2 2026-09-06] 노트북 행 장부도 세션 파생
        "fact_sources",  # [2026-09-14 S5a] 지식 사실 출처 원장 — knows와 같은 수명
    )
    try:
        for t in tables:
            conn.execute(f"DELETE FROM {t} WHERE channel_id=?", (channel_id,))
        conn.commit()
        return True
    except Exception as e:
        logger.warning(f"[SQLiteStore] clear_session_scoped 실패 (무시): {channel_id}: {e}")
        return False


def read_dai_logs(channel_id: str, limit: int = 10) -> list:
    """최근 N턴 DAI 스냅샷 (오래된→최신). [(turn, dai_dict), ...]"""
    if not channel_id or limit <= 0 or not _ensure_schema(channel_id):
        return []
    conn = _get_conn(channel_id)
    if conn is None:
        return []
    try:
        cur = conn.execute(
            "SELECT turn, dai FROM dai_logs WHERE channel_id=? ORDER BY id DESC LIMIT ?",
            (channel_id, int(limit)),
        )
        out = []
        for turn, blob in reversed(cur.fetchall()):
            try:
                out.append((turn, json.loads(blob)))
            except Exception:
                continue
        return out
    except Exception as e:
        logger.warning(f"[SQLiteStore] read_dai_logs 실패: {channel_id}: {e}")
        return []


# =========================================================
# [V10 적립 패러다임] emotion_log — 감정 매핑 장부 (생성자 + 독자)
# =========================================================

def append_emotion_log(channel_id: str, turn: int, emotion_bus: Dict[str, Any], keep: int = 1500,
                       log_extra: Optional[Dict[str, Dict[str, Any]]] = None) -> bool:
    """bus.emotion summary를 턴별 per-NPC 행으로 적립. 채널당 최근 keep행 롤링. 실패 무해.
    생성자(writer). emotion_bus = EmotionEngine.to_bus_dict() 결과.
    log_extra: [2026-09-15 §12] states에서 뺀 로그 전용 진단 키(base_source/mod_source) — raw_json에만 병합."""
    if not channel_id or not isinstance(emotion_bus, dict):
        return False
    summary = emotion_bus.get("summary") or {}
    states = emotion_bus.get("states") or {}
    if not isinstance(summary, dict) or not summary:
        return False
    if not _ensure_schema(channel_id):
        return False
    conn = _get_conn(channel_id)
    if conn is None:
        return False
    try:
        ts = time.time()
        rows = []
        for npc, s in summary.items():
            if not isinstance(s, dict):
                continue
            rows.append((
                channel_id, int(turn), str(npc),
                str(s.get("base", "") or ""), str(s.get("modifier", "") or ""),
                float(s.get("intensity", 0.0) or 0.0),
                1 if s.get("spike") else 0,
                str(s.get("scene_base", "") or ""), str(s.get("scene_mod", "") or ""),
                float(s.get("pair_confidence", 0.0) or 0.0),
                json.dumps({**(states.get(npc, {}) or {}),
                            **((log_extra or {}).get(npc, {}) or {})}, ensure_ascii=False, default=str),
                ts,
            ))
        if not rows:
            return False
        conn.executemany(
            "INSERT INTO emotion_log (channel_id, turn, npc_name, base, modifier, intensity, "
            "spike, scene_base, scene_mod, pair_confidence, raw_json, created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            rows,
        )
        conn.execute(
            "DELETE FROM emotion_log WHERE channel_id=? AND id NOT IN "
            "(SELECT id FROM emotion_log WHERE channel_id=? ORDER BY id DESC LIMIT ?)",
            (channel_id, channel_id, int(keep)),
        )
        conn.commit()
        return True
    except Exception as e:
        logger.warning(f"[SQLiteStore] append_emotion_log 실패 (무시): {channel_id}: {e}")
        return False


def read_emotion_trajectory(channel_id: str, npc_name: str, limit: int = 30) -> list:
    """독자: 한 NPC의 최근 감정 궤적 (오래된→최신).
    [{turn, base, modifier, intensity, spike, scene_base, scene_mod, pair_confidence}, ...]"""
    if not channel_id or not npc_name or limit <= 0 or not _ensure_schema(channel_id):
        return []
    conn = _get_conn(channel_id)
    if conn is None:
        return []
    try:
        cur = conn.execute(
            "SELECT turn, base, modifier, intensity, spike, scene_base, scene_mod, pair_confidence "
            "FROM emotion_log WHERE channel_id=? AND npc_name=? ORDER BY id DESC LIMIT ?",
            (channel_id, npc_name, int(limit)),
        )
        out = []
        for r in reversed(cur.fetchall()):
            out.append({"turn": r[0], "base": r[1], "modifier": r[2], "intensity": r[3],
                        "spike": bool(r[4]), "scene_base": r[5], "scene_mod": r[6], "pair_confidence": r[7]})
        return out
    except Exception as e:
        logger.warning(f"[SQLiteStore] read_emotion_trajectory 실패: {channel_id}: {e}")
        return []


def read_emotion_spikes(channel_id: str, limit: int = 20) -> list:
    """독자: 최근 스파이크 이벤트만 (오래된→최신). [{turn, npc_name, base, modifier, intensity}, ...]"""
    if not channel_id or limit <= 0 or not _ensure_schema(channel_id):
        return []
    conn = _get_conn(channel_id)
    if conn is None:
        return []
    try:
        cur = conn.execute(
            "SELECT turn, npc_name, base, modifier, intensity FROM emotion_log "
            "WHERE channel_id=? AND spike=1 ORDER BY id DESC LIMIT ?",
            (channel_id, int(limit)),
        )
        return [{"turn": r[0], "npc_name": r[1], "base": r[2], "modifier": r[3], "intensity": r[4]}
                for r in reversed(cur.fetchall())]
    except Exception as e:
        logger.warning(f"[SQLiteStore] read_emotion_spikes 실패: {channel_id}: {e}")
        return []


# [2026-07-18 고아 삭제] read_emotion_turn — D2 감정부채 때 read_emotion_spikes만 승격 — turn 단위 리더 소비 계획 없음 (dead_scan 참조0 확인, git 이력 복원 가능)


# =========================================================
# [V10 적립] turn_snapshot — 턴 스칼라 상태 (생성자 + 독자)
# =========================================================

def append_turn_snapshot(channel_id: str, turn: int, snap: Dict[str, Any], keep: int = 400) -> bool:
    """턴당 스칼라 상태 1행(upsert: 같은 turn은 최신으로 교체). 채널당 최근 keep턴 롤링. 실패 무해."""
    if not channel_id or not isinstance(snap, dict):
        return False
    if not _ensure_schema(channel_id):
        return False
    conn = _get_conn(channel_id)
    if conn is None:
        return False
    try:
        def _i(x):
            try:
                return int(x)
            except Exception:
                return None
        conn.execute(
            "INSERT OR REPLACE INTO turn_snapshot (channel_id, turn, doom_value, doom_phase, "
            "vigor, vigor_delta, composure, composure_delta, sd_pacing, sd_tension, sd_focus, "
            "sd_beat, sd_idle, judgment_active, anomaly_triggered, raw_json, created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                channel_id, int(turn), _i(snap.get("doom_value")), str(snap.get("doom_phase", "") or ""),
                _i(snap.get("vigor")), int(snap.get("vigor_delta", 0) or 0),
                _i(snap.get("composure")), int(snap.get("composure_delta", 0) or 0),
                str(snap.get("sd_pacing", "") or ""), str(snap.get("sd_tension", "") or ""),
                str(snap.get("sd_focus", "") or ""),
                1 if snap.get("sd_beat") else 0, 1 if snap.get("sd_idle") else 0,
                # [2026-09-16 3차] 칸은 그대로(INTEGER) — 값은 `judgment` dict 의 판정 여부에서 파생, dict 는 raw_json.
                1 if (isinstance(snap.get("judgment"), dict) and snap["judgment"].get("result")) or snap.get("judgment_active") else 0,
                1 if snap.get("anomaly_triggered") else 0,
                json.dumps(snap, ensure_ascii=False, default=str), time.time(),
            ),
        )
        conn.execute(
            "DELETE FROM turn_snapshot WHERE channel_id=? AND turn NOT IN "
            "(SELECT turn FROM turn_snapshot WHERE channel_id=? ORDER BY turn DESC LIMIT ?)",
            (channel_id, channel_id, int(keep)),
        )
        conn.commit()
        return True
    except Exception as e:
        logger.warning(f"[SQLiteStore] append_turn_snapshot 실패 (무시): {channel_id}: {e}")
        return False


def merge_turn_snapshot_raw(channel_id: str, turn: int, patch: Dict[str, Any]) -> bool:
    """[2026-09-18 식별 허브 S6] 턴 영수증 — `turn_snapshot.raw_json`에 키를 **병합**한다.

    행이 없으면(파이프라인 스냅샷보다 배경 추출이 먼저 끝난 턴) 그 턴 행을 새로 만든다.
    테이블 추가 0 · 스칼라 칸 무접촉 · 실패 무해. 화면이 조용한 설계라 이게 유일한 창이다.
    """
    if not channel_id or not isinstance(patch, dict) or not patch:
        return False
    if not _ensure_schema(channel_id):
        return False
    conn = _get_conn(channel_id)
    if conn is None:
        return False
    try:
        row = conn.execute(
            "SELECT raw_json FROM turn_snapshot WHERE channel_id=? AND turn=?",
            (channel_id, int(turn))).fetchone()
        cur: Dict[str, Any] = {}
        if row and row[0]:
            try:
                _v = json.loads(row[0])
                cur = _v if isinstance(_v, dict) else {}
            except Exception:
                cur = {}
        cur.update(patch)
        blob = json.dumps(cur, ensure_ascii=False, default=str)
        if row:
            conn.execute("UPDATE turn_snapshot SET raw_json=? WHERE channel_id=? AND turn=?",
                         (blob, channel_id, int(turn)))
        else:
            conn.execute(
                "INSERT INTO turn_snapshot (channel_id, turn, raw_json, created_at) VALUES (?,?,?,?)",
                (channel_id, int(turn), blob, time.time()))
        conn.commit()
        return True
    except Exception as e:
        logger.warning(f"[SQLiteStore] merge_turn_snapshot_raw 실패 (무시): {channel_id}: {e}")
        return False


def read_turn_snapshot_raw(channel_id: str, turn: Optional[int] = None,
                           limit: int = 30) -> list:
    """[2026-09-18 식별 허브 S6] 턴 영수증 읽기 — `raw_json`까지 준다(오래된→최신).

    `read_turn_snapshots`는 스칼라 칸만 SELECT 해서 raw_json 을 못 돌려준다 —
    쓰기만 있고 읽기가 없으면 그건 **아무도 못 보는 기록**이다. 이 함수가 그 창.
    turn 을 주면 그 턴 하나만. Returns: [{"turn": int, "raw": dict}]
    """
    if not channel_id or not _ensure_schema(channel_id):
        return []
    conn = _get_conn(channel_id)
    if conn is None:
        return []
    try:
        if turn is None:
            cur = conn.execute(
                "SELECT turn, raw_json FROM turn_snapshot WHERE channel_id=? "
                "ORDER BY turn DESC LIMIT ?", (channel_id, int(limit)))
            rows = list(reversed(cur.fetchall()))
        else:
            rows = conn.execute(
                "SELECT turn, raw_json FROM turn_snapshot WHERE channel_id=? AND turn=?",
                (channel_id, int(turn))).fetchall()
        out = []
        for r in rows:
            try:
                _v = json.loads(r[1]) if r[1] else {}
            except Exception:
                _v = {}
            out.append({"turn": r[0], "raw": _v if isinstance(_v, dict) else {}})
        return out
    except Exception as e:
        logger.warning(f"[SQLiteStore] read_turn_snapshot_raw 실패: {channel_id}: {e}")
        return []


def read_turn_snapshots(channel_id: str, limit: int = 30) -> list:
    """독자: 최근 N턴 스냅샷 (오래된→최신)."""
    if not channel_id or limit <= 0 or not _ensure_schema(channel_id):
        return []
    conn = _get_conn(channel_id)
    if conn is None:
        return []
    try:
        cur = conn.execute(
            "SELECT turn, doom_value, doom_phase, vigor, vigor_delta, composure, composure_delta, "
            "sd_pacing, sd_tension, sd_focus, sd_beat, sd_idle FROM turn_snapshot "
            "WHERE channel_id=? ORDER BY turn DESC LIMIT ?",
            (channel_id, int(limit)),
        )
        out = []
        for r in reversed(cur.fetchall()):
            out.append({"turn": r[0], "doom_value": r[1], "doom_phase": r[2], "vigor": r[3],
                        "vigor_delta": r[4], "composure": r[5], "composure_delta": r[6],
                        "sd_pacing": r[7], "sd_tension": r[8], "sd_focus": r[9],
                        "sd_beat": bool(r[10]), "sd_idle": bool(r[11])})
        return out
    except Exception as e:
        logger.warning(f"[SQLiteStore] read_turn_snapshots 실패: {channel_id}: {e}")
        return []


# =========================================================
# [V10 적립] autonomy_log — NPC 자율 트리거 발동 (생성자 + 독자)
# =========================================================

def append_autonomy_log(channel_id: str, turn: int, entries: List[Dict[str, Any]], keep: int = 800) -> bool:
    """NPC 자율 트리거 발동 1행/트리거. 대사·관계 압력의 출처 적립.
    entries: [{npc_name, trigger_id, priority, directive}, ...]. 채널당 최근 keep행 롤링. 실패 무해."""
    if not channel_id or not entries:
        return False
    if not _ensure_schema(channel_id):
        return False
    conn = _get_conn(channel_id)
    if conn is None:
        return False
    try:
        now = time.time()
        rows = [
            (channel_id, int(turn), str(e.get("npc_name", "") or ""), str(e.get("trigger_id", "") or ""),
             int(e.get("priority", 0) or 0), str(e.get("directive", "") or ""), now)
            for e in entries if isinstance(e, dict) and e.get("npc_name")
        ]
        if not rows:
            return False
        conn.executemany(
            "INSERT INTO autonomy_log (channel_id, turn, npc_name, trigger_id, priority, directive, created_at) "
            "VALUES (?,?,?,?,?,?,?)",
            rows,
        )
        conn.execute(
            "DELETE FROM autonomy_log WHERE channel_id=? AND id NOT IN "
            "(SELECT id FROM autonomy_log WHERE channel_id=? ORDER BY id DESC LIMIT ?)",
            (channel_id, channel_id, int(keep)),
        )
        conn.commit()
        return True
    except Exception as e:
        logger.warning(f"[SQLiteStore] append_autonomy_log 실패 (무시): {channel_id}: {e}")
        return False


def read_autonomy_log(channel_id: str, limit: int = 50) -> list:
    """독자: 최근 N행 자율 트리거 (오래된→최신). [{turn, npc_name, trigger_id, priority, directive}, ...]"""
    if not channel_id or limit <= 0 or not _ensure_schema(channel_id):
        return []
    conn = _get_conn(channel_id)
    if conn is None:
        return []
    try:
        cur = conn.execute(
            "SELECT turn, npc_name, trigger_id, priority, directive FROM autonomy_log "
            "WHERE channel_id=? ORDER BY id DESC LIMIT ?",
            (channel_id, int(limit)),
        )
        return [{"turn": r[0], "npc_name": r[1], "trigger_id": r[2], "priority": r[3], "directive": r[4]}
                for r in reversed(cur.fetchall())]
    except Exception as e:
        logger.warning(f"[SQLiteStore] read_autonomy_log 실패: {channel_id}: {e}")
        return []


def read_npc_last_turns(channel_id: str) -> dict:
    """[Phase 0 2026-07-02] 독자: NPC별 마지막 등장 턴 (emotion_log 기준 — 장면에 있던 턴만 기록됨).
    {npc_name: last_turn}. offscreen 후보의 '부재 기간' 산출용."""
    if not channel_id or not _ensure_schema(channel_id):
        return {}
    conn = _get_conn(channel_id)
    if conn is None:
        return {}
    try:
        cur = conn.execute(
            "SELECT npc_name, MAX(turn) FROM emotion_log WHERE channel_id=? GROUP BY npc_name",
            (channel_id,),
        )
        return {str(r[0]): int(r[1]) for r in cur.fetchall() if r[0] is not None and r[1] is not None}
    except Exception as e:
        logger.warning(f"[SQLiteStore] read_npc_last_turns 실패: {channel_id}: {e}")
        return {}


def read_npc_intensity_sums(channel_id: str, turn_from: int, turn_to: int) -> dict:
    """[Phase 0 2026-07-02] 독자: 구간 내 NPC별 감정 강도 합 (스크린타임 근사, B안 회고 팩용).
    {npc_name: intensity_sum}"""
    if not channel_id or not _ensure_schema(channel_id):
        return {}
    conn = _get_conn(channel_id)
    if conn is None:
        return {}
    try:
        cur = conn.execute(
            "SELECT npc_name, SUM(intensity) FROM emotion_log "
            "WHERE channel_id=? AND turn BETWEEN ? AND ? GROUP BY npc_name",
            (channel_id, int(turn_from), int(turn_to)),
        )
        return {str(r[0]): float(r[1] or 0.0) for r in cur.fetchall() if r[0] is not None}
    except Exception as e:
        logger.warning(f"[SQLiteStore] read_npc_intensity_sums 실패: {channel_id}: {e}")
        return {}


# =========================================================
# [V10 적립] attitude_log — 관계 태도 전이 이벤트 (생성자 + 독자)
# =========================================================

def append_attitude_log(channel_id: str, turn: int, npc_name: str, from_attitude: str,
                        to_attitude: str, result: str = "accepted", reason: str = "",
                        keep: int = 1000) -> bool:
    """태도 전이 1건 적립 (실 전이만 — 호출부에서 no-op/cooldown 거름). 채널당 keep행 롤링. 실패 무해."""
    if not channel_id or not npc_name:
        return False
    if not _ensure_schema(channel_id):
        return False
    conn = _get_conn(channel_id)
    if conn is None:
        return False
    try:
        conn.execute(
            "INSERT INTO attitude_log (channel_id, turn, npc_name, from_attitude, to_attitude, "
            "result, reason, created_at) VALUES (?,?,?,?,?,?,?,?)",
            (channel_id, int(turn), str(npc_name), str(from_attitude or ""), str(to_attitude or ""),
             str(result or ""), str(reason or ""), time.time()),
        )
        conn.execute(
            "DELETE FROM attitude_log WHERE channel_id=? AND id NOT IN "
            "(SELECT id FROM attitude_log WHERE channel_id=? ORDER BY id DESC LIMIT ?)",
            (channel_id, channel_id, int(keep)),
        )
        conn.commit()
        return True
    except Exception as e:
        logger.warning(f"[SQLiteStore] append_attitude_log 실패 (무시): {channel_id}: {e}")
        return False


def read_attitude_log(channel_id: str, npc_name: Optional[str] = None, limit: int = 30) -> list:
    """독자: 태도 전이 이력 (오래된→최신). npc_name 주면 그 NPC만."""
    if not channel_id or limit <= 0 or not _ensure_schema(channel_id):
        return []
    conn = _get_conn(channel_id)
    if conn is None:
        return []
    try:
        if npc_name:
            cur = conn.execute(
                "SELECT turn, npc_name, from_attitude, to_attitude, result, reason FROM attitude_log "
                "WHERE channel_id=? AND npc_name=? ORDER BY id DESC LIMIT ?",
                (channel_id, npc_name, int(limit)),
            )
        else:
            cur = conn.execute(
                "SELECT turn, npc_name, from_attitude, to_attitude, result, reason FROM attitude_log "
                "WHERE channel_id=? ORDER BY id DESC LIMIT ?",
                (channel_id, int(limit)),
            )
        return [{"turn": r[0], "npc_name": r[1], "from": r[2], "to": r[3], "result": r[4], "reason": r[5]}
                for r in reversed(cur.fetchall())]
    except Exception as e:
        logger.warning(f"[SQLiteStore] read_attitude_log 실패: {channel_id}: {e}")
        return []


# =========================================================
# [2026-08-11 soma 지속] soma_log — B축(신체) 전이 이벤트 (생성자 + 독자)
# =========================================================

def append_soma_log(channel_id: str, turn: int, npc_name: str,
                    from_polyvagal: Optional[str] = None, to_polyvagal: Optional[str] = None,
                    from_dissociation: Optional[str] = None, to_dissociation: Optional[str] = None,
                    keep: Optional[int] = None) -> bool:
    """몸 상태 전이 1건 적립 (실 전이만 — 호출부에서 무변화 거름). 채널당 keep행 롤링. 실패 무해.

    최초 관측(이전 상태 없음)은 from_*=None → attitude_log의 'initial'과 동형으로 ''로 저장한다."""
    if not channel_id or not npc_name:
        return False
    if not _ensure_schema(channel_id):
        return False
    conn = _get_conn(channel_id)
    if conn is None:
        return False
    if keep is None:
        keep = int(getattr(config, "SOMA_LOG_KEEP", 800) or 800)
    try:
        conn.execute(
            "INSERT INTO soma_log (channel_id, turn, npc_name, from_polyvagal, to_polyvagal, "
            "from_dissociation, to_dissociation, created_at) VALUES (?,?,?,?,?,?,?,?)",
            (channel_id, int(turn), str(npc_name), str(from_polyvagal or ""),
             str(to_polyvagal or ""), str(from_dissociation or ""), str(to_dissociation or ""),
             time.time()),
        )
        conn.execute(
            "DELETE FROM soma_log WHERE channel_id=? AND id NOT IN "
            "(SELECT id FROM soma_log WHERE channel_id=? ORDER BY id DESC LIMIT ?)",
            (channel_id, channel_id, int(keep)),
        )
        conn.commit()
        return True
    except Exception as e:
        logger.warning(f"[SQLiteStore] append_soma_log 실패 (무시): {channel_id}: {e}")
        return False


def read_soma_log(channel_id: str, npc_name: Optional[str] = None, limit: int = 30) -> list:
    """독자: 몸 상태 전이 이력 (오래된→최신). npc_name 주면 그 NPC만."""
    if not channel_id or limit <= 0 or not _ensure_schema(channel_id):
        return []
    conn = _get_conn(channel_id)
    if conn is None:
        return []
    _cols = ("SELECT turn, npc_name, from_polyvagal, to_polyvagal, from_dissociation, "
             "to_dissociation FROM soma_log ")
    try:
        if npc_name:
            cur = conn.execute(_cols + "WHERE channel_id=? AND npc_name=? ORDER BY id DESC LIMIT ?",
                               (channel_id, npc_name, int(limit)))
        else:
            cur = conn.execute(_cols + "WHERE channel_id=? ORDER BY id DESC LIMIT ?",
                               (channel_id, int(limit)))
        return [{"turn": r[0], "npc": r[1], "from_polyvagal": r[2], "to_polyvagal": r[3],
                 "from_dissociation": r[4], "to_dissociation": r[5]}
                for r in reversed(cur.fetchall())]
    except Exception as e:
        logger.warning(f"[SQLiteStore] read_soma_log 실패: {channel_id}: {e}")
        return []


# =========================================================
# [2026-08-16 도착물 라우트] turn_mail — 턴 도착물 (생성자 + 독자)
# =========================================================

def append_turn_mail(channel_id: str, message_id: int, turn: int, kind: str,
                     payload: Dict[str, Any], keep: Optional[int] = None) -> bool:
    """도착물 1건 적립. (channel_id, message_id, kind) 자리에 이미 있으면 **교체**.

    교체가 계약인 이유: 같은 턴을 두 번 계산해도(배경 태스크 재실행) 버튼 하나에
    같은 내용이 두 번 붙지 않아야 한다. 실패는 무해(False) — 버튼이 안 붙을 뿐이다.
    """
    if not channel_id or not message_id or not isinstance(payload, dict):
        return False
    if not _ensure_schema(channel_id):
        return False
    conn = _get_conn(channel_id)
    if conn is None:
        return False
    if keep is None:
        keep = int(getattr(config, "TURN_MAIL_KEEP", 500) or 500)
    try:
        conn.execute(
            "DELETE FROM turn_mail WHERE channel_id=? AND message_id=? AND kind=?",
            (channel_id, int(message_id), str(kind or "mail")),
        )
        conn.execute(
            "INSERT INTO turn_mail (channel_id, message_id, turn, kind, payload, created_at) "
            "VALUES (?,?,?,?,?,?)",
            (channel_id, int(message_id), int(turn or 0), str(kind or "mail"),
             json.dumps(payload, ensure_ascii=False), time.time()),
        )
        conn.execute(
            "DELETE FROM turn_mail WHERE channel_id=? AND id NOT IN "
            "(SELECT id FROM turn_mail WHERE channel_id=? ORDER BY id DESC LIMIT ?)",
            (channel_id, channel_id, int(keep)),
        )
        conn.commit()
        return True
    except Exception as e:
        logger.warning(f"[SQLiteStore] append_turn_mail 실패 (무시): {channel_id}: {e}")
        return False


def read_turn_mail(channel_id: str, message_id: int, kind: Optional[str] = None) -> list:
    """독자: 그 메시지에 딸린 도착물 (오래된→최신). kind 주면 그 종류만.
    반환 [{"turn", "kind", "payload"}]. 트림으로 사라졌으면 [] (= 버튼이 만료 안내)."""
    if not channel_id or not message_id or not _ensure_schema(channel_id):
        return []
    conn = _get_conn(channel_id)
    if conn is None:
        return []
    try:
        if kind:
            cur = conn.execute(
                "SELECT turn, kind, payload FROM turn_mail "
                "WHERE channel_id=? AND message_id=? AND kind=? ORDER BY id",
                (channel_id, int(message_id), str(kind)),
            )
        else:
            cur = conn.execute(
                "SELECT turn, kind, payload FROM turn_mail "
                "WHERE channel_id=? AND message_id=? ORDER BY id",
                (channel_id, int(message_id)),
            )
        out = []
        for r in cur.fetchall():
            try:
                payload = json.loads(r[2])
            except Exception:
                payload = {}
            if not isinstance(payload, dict):
                payload = {}
            out.append({"turn": r[0], "kind": r[1], "payload": payload})
        return out
    except Exception as e:
        logger.warning(f"[SQLiteStore] read_turn_mail 실패: {channel_id}: {e}")
        return []


def read_channel_mail(channel_id: str, limit: int = 10) -> list:
    """독자(채널 전체): 최근 도착물 N건, **최신 위**. [{"message_id","turn","kind","payload"}].

    [2026-09-13 P9c] `read_turn_mail` 은 (channel_id, message_id) 로만 본다 — 그게
    턴 고정 계약이라 **옛 버튼에 새 내용이 새지 않게** 하는 자리다. 💠 "쌓인 것" 창의
    도착물 **목록**은 그 반대 축(어느 메시지든, 채널에 최근 무엇이 왔나)이라 조회가
    따로 필요했다. 인덱스는 이미 있다(idx_turnmail_channel = channel_id, id).
    ⚠ 목록용이다 — 본문을 다시 그리는 자리가 아니다(그건 여전히 message_id 버튼).
    """
    if not channel_id or limit <= 0 or not _ensure_schema(channel_id):
        return []
    conn = _get_conn(channel_id)
    if conn is None:
        return []
    try:
        cur = conn.execute(
            "SELECT message_id, turn, kind, payload FROM turn_mail "
            "WHERE channel_id=? ORDER BY id DESC LIMIT ?",
            (channel_id, int(limit)),
        )
        out = []
        for r in cur.fetchall():
            try:
                payload = json.loads(r[3])
            except Exception:
                payload = {}
            if not isinstance(payload, dict):
                payload = {}
            out.append({"message_id": r[0], "turn": r[1], "kind": r[2], "payload": payload})
        return out
    except Exception as e:
        logger.warning(f"[SQLiteStore] read_channel_mail 실패: {channel_id}: {e}")
        return []


def read_arc_window(channel_id: str, start_turn: int, end_turn: int) -> Dict[str, list]:
    """독자(범위): 턴 [start,end]의 감정/태도/몸/스냅샷을 한 번에. 발효 청크 호(弧) digest용.
    {"emotion":[...], "attitudes":[...], "soma":[...], "snapshots":[...]}. 실패 시 빈 묶음."""
    out = {"emotion": [], "attitudes": [], "soma": [], "snapshots": []}
    if not channel_id or not _ensure_schema(channel_id):
        return out
    conn = _get_conn(channel_id)
    if conn is None:
        return out
    try:
        s, e = int(start_turn), int(end_turn)
        cur = conn.execute(
            "SELECT turn, npc_name, base, modifier, intensity, spike FROM emotion_log "
            "WHERE channel_id=? AND turn BETWEEN ? AND ? ORDER BY id", (channel_id, s, e))
        out["emotion"] = [{"turn": r[0], "npc": r[1], "base": r[2], "modifier": r[3],
                           "intensity": r[4], "spike": bool(r[5])} for r in cur.fetchall()]
        cur = conn.execute(
            "SELECT turn, npc_name, from_attitude, to_attitude, result FROM attitude_log "
            "WHERE channel_id=? AND turn BETWEEN ? AND ? ORDER BY id", (channel_id, s, e))
        out["attitudes"] = [{"turn": r[0], "npc": r[1], "from": r[2], "to": r[3], "result": r[4]}
                            for r in cur.fetchall()]
        # [2026-08-11 soma 지속] 몸 상태 전이 창 — 기존 키 무변경, "soma" 추가만.
        cur = conn.execute(
            "SELECT turn, npc_name, from_polyvagal, to_polyvagal, from_dissociation, "
            "to_dissociation FROM soma_log "
            "WHERE channel_id=? AND turn BETWEEN ? AND ? ORDER BY id", (channel_id, s, e))
        out["soma"] = [{"turn": r[0], "npc": r[1], "from_polyvagal": r[2], "to_polyvagal": r[3],
                        "from_dissociation": r[4], "to_dissociation": r[5]}
                       for r in cur.fetchall()]
        cur = conn.execute(
            "SELECT turn, doom_phase, sd_tension FROM turn_snapshot "
            "WHERE channel_id=? AND turn BETWEEN ? AND ? ORDER BY turn", (channel_id, s, e))
        out["snapshots"] = [{"turn": r[0], "phase": r[1], "tension": r[2]} for r in cur.fetchall()]
        return out
    except Exception as e:
        logger.warning(f"[SQLiteStore] read_arc_window 실패: {channel_id}: {e}")
        return out


def read_deep(channel_id: str) -> Optional[Dict[str, Any]]:
    """deep_memory 조회.

    [2026-09-05 P4] 없음 != 실패. 행 없음 → {"narrative": "", "data": {}}. 실패 → None."""
    if not channel_id or not _ensure_schema(channel_id):
        return None
    conn = _get_conn(channel_id)
    if conn is None:
        return None
    try:
        cur = conn.execute("SELECT narrative, data FROM deep_memory WHERE channel_id=?", (channel_id,))
        row = cur.fetchone()
        if row is None:
            return {"narrative": "", "data": {}}
        try:
            data = json.loads(row[1])
        except Exception:
            logger.warning("[SQLiteStore] deep data parse failed ch=%s — falling back to empty dict", channel_id)
            data = {}
        return {"narrative": row[0], "data": data}
    except Exception as e:
        logger.warning(f"[SQLiteStore] read_deep 실패: {channel_id}: {e}")
        return None


def count_npcs(channel_id: Optional[str] = None) -> int:
    """NPC 행 수. 검증용. 실패 시 -1."""
    # [2026-09-14 W0] channel_id 없음 = 채널 폴더 전량 합산(레거시 모드면 종전 단일 파일).
    if not channel_id:
        return _count_all("npcs")
    if not _ensure_schema(channel_id):
        return -1
    conn = _get_conn(channel_id)
    if conn is None:
        return -1
    try:
        cur = conn.execute("SELECT COUNT(*) FROM npcs WHERE channel_id=?", (channel_id,))
        return int(cur.fetchone()[0])
    except Exception:
        return -1


# =========================================================
# [2026-09-14 S5a] fact_sources — 지식 사실 출처 원장 (쓰기 + 독자)
# 정규화는 fermentation._norm_quote 재사용 (두 번째 정규화 함수 금지).
# =========================================================

def _fact_norm(fact: Any) -> str:
    """fact_norm 산출 — NFKC+공백접기(fermentation 재사용) 뒤 config 상한 절단."""
    from fermentation import _norm_quote
    cap = int(getattr(config, "FACT_SOURCE_FACT_CHARS", 200))
    return _norm_quote(str(fact or ""))[:cap]


def append_fact_sources(channel_id: str, npc_name: str, facts: List[str],
                        src_turn: Optional[int] = None,
                        src_msg_id: Optional[int] = None) -> int:
    """새 지식 사실의 첫 출처 적립. 반환 = 실제 삽입 수(중복은 0), 실패 시 -1. 예외 삼킴."""
    if not channel_id or not npc_name or not facts:
        return 0
    if not _ensure_schema(channel_id):
        return -1
    conn = _get_conn(channel_id)
    if conn is None:
        return -1
    try:
        ts = time.strftime('%Y-%m-%d %H:%M')
        _t = int(src_turn) if src_turn is not None else None
        _m = int(src_msg_id) if src_msg_id is not None else None
        n = 0
        for f in facts:
            fn = _fact_norm(f)
            if not fn:
                continue
            cur = conn.execute(
                "INSERT OR IGNORE INTO fact_sources "
                "(channel_id, npc_name, fact_norm, src_turn, src_msg_id, first_seen) "
                "VALUES (?,?,?,?,?,?)",
                (str(channel_id), str(npc_name), fn, _t, _m, ts),
            )
            if cur.rowcount and cur.rowcount > 0:
                n += cur.rowcount
        conn.commit()
        return n
    except Exception as e:
        logger.warning(f"[SQLiteStore] append_fact_sources 실패 (무시): {channel_id}: {e}")
        return -1


def lookup_fact_sources(channel_id: str, npc_name: Optional[str] = None,
                        limit: int = 100) -> Optional[list]:
    """독자: 출처 원장 조회 (최신→오래된). npc_name None이면 채널 전체.
    행 0 = [], 실패 = None (09-05 계약)."""
    if not channel_id or limit <= 0 or not _ensure_schema(channel_id):
        return None
    conn = _get_conn(channel_id)
    if conn is None:
        return None
    try:
        if npc_name:
            cur = conn.execute(
                "SELECT npc_name, fact_norm, src_turn, src_msg_id FROM fact_sources "
                "WHERE channel_id=? AND npc_name=? ORDER BY rowid DESC LIMIT ?",
                (str(channel_id), str(npc_name), int(limit)),
            )
        else:
            cur = conn.execute(
                "SELECT npc_name, fact_norm, src_turn, src_msg_id FROM fact_sources "
                "WHERE channel_id=? ORDER BY rowid DESC LIMIT ?",
                (str(channel_id), int(limit)),
            )
        return [{"npc_name": r[0], "fact_norm": r[1], "src_turn": r[2], "src_msg_id": r[3]}
                for r in cur.fetchall()]
    except Exception as e:
        logger.warning(f"[SQLiteStore] lookup_fact_sources 실패: {channel_id}: {e}")
        return None
