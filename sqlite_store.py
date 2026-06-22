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

DB 위치: config.DATA_DIR/lorekeeper.db
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

_DB_PATH = os.path.join(config.DATA_DIR, "lorekeeper.db")

# sqlite3 연결은 스레드마다 따로 두는 게 안전. thread-local로 관리.
_local = threading.local()
_init_lock = threading.Lock()
_initialized = False


def _get_conn() -> Optional[sqlite3.Connection]:
    """스레드별 연결 반환. 실패 시 None (봇 안전)."""
    conn = getattr(_local, "conn", None)
    if conn is not None:
        return conn
    try:
        os.makedirs(config.DATA_DIR, exist_ok=True)
        conn = sqlite3.connect(_DB_PATH, timeout=10.0)
        # WAL: 동시 읽기 중 쓰기 허용. 틱 루프(스프린트 4) 대비.
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        _local.conn = conn
        return conn
    except Exception as e:
        logger.warning(f"[SQLiteStore] connect 실패 (무시, JSON 경로 계속): {e}")
        return None


def _ensure_schema() -> bool:
    """테이블 생성 (1회). 실패해도 False 반환, 예외 안 던짐."""
    global _initialized
    if _initialized:
        return True
    with _init_lock:
        if _initialized:
            return True
        conn = _get_conn()
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
            # [V10 Sprint 1] 관계 정규화 테이블 (v10_sprint1_relations_spec.md §3)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS npc_relations (
                    channel_id        TEXT NOT NULL,
                    npc_name          TEXT NOT NULL,
                    attitude          TEXT NOT NULL DEFAULT 'neutral',
                    reason            TEXT NOT NULL DEFAULT '',
                    depth             INTEGER NOT NULL DEFAULT 0,
                    tension           INTEGER NOT NULL DEFAULT 0,
                    last_change_turn  INTEGER,
                    updated_at        TEXT NOT NULL DEFAULT '',
                    PRIMARY KEY (channel_id, npc_name)
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_relations_channel ON npc_relations(channel_id)")
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
            conn.commit()
            _initialized = True
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
    if not _ensure_schema():
        return False
    conn = _get_conn()
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
    if not channel_id or not _ensure_schema():
        return None
    conn = _get_conn()
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


def iter_all() -> Iterator[Tuple[str, Dict[str, Any]]]:
    """모든 세션 (channel_id, data) 순회. 검증/마이그레이션용."""
    if not _ensure_schema():
        return
    conn = _get_conn()
    if conn is None:
        return
    try:
        cur = conn.execute("SELECT channel_id, data FROM sessions")
        for channel_id, blob in cur.fetchall():
            try:
                yield channel_id, json.loads(blob)
            except Exception:
                continue
    except Exception as e:
        logger.warning(f"[SQLiteStore] iter_all 실패: {e}")
        return


def count_sessions() -> int:
    """저장된 세션 수. 검증용. 실패 시 -1."""
    if not _ensure_schema():
        return -1
    conn = _get_conn()
    if conn is None:
        return -1
    try:
        cur = conn.execute("SELECT COUNT(*) FROM sessions")
        return int(cur.fetchone()[0])
    except Exception:
        return -1


# =========================================================
# [V10 Sprint 1] npc_relations — 관계 정규화 테이블 API
# 전 함수 예외 안전: 실패 시 False/None, 절대 raise 안 함.
# JSON dict 키 계약: attitude/reason/depth/tension/last_updated(+ last_change_turn 선택)
#   - DB 컬럼 updated_at ↔ JSON 키 "last_updated" 매핑
#   - last_change_turn은 NULL이면 dict에서 키 자체를 생략 (JSON의 키 부재와 동치 — parity 핵심)
# =========================================================

def _row_to_relation(row) -> Dict[str, Any]:
    """npc_relations 행 → JSON attitude dict 포맷."""
    rel: Dict[str, Any] = {
        "attitude": row[0],
        "reason": row[1],
        "depth": row[2],
        "tension": row[3],
        "last_updated": row[5],
    }
    if row[4] is not None:
        rel["last_change_turn"] = row[4]
    return rel

_REL_COLS = "attitude, reason, depth, tension, last_change_turn, updated_at"


def upsert_relation(channel_id: str, npc_name: str, rel: Dict[str, Any]) -> bool:
    """관계 1행 upsert. rel은 JSON attitude dict 포맷 (검증 방벽 통과 후 호출).
    last_change_turn 키가 없으면 NULL로 저장 (update_npc_attitude의 dict 재구성 quirk 미러)."""
    if not channel_id or not npc_name or not isinstance(rel, dict):
        return False
    if not _ensure_schema():
        return False
    conn = _get_conn()
    if conn is None:
        return False
    try:
        conn.execute(
            f"INSERT INTO npc_relations (channel_id, npc_name, {_REL_COLS}) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(channel_id, npc_name) DO UPDATE SET "
            "attitude=excluded.attitude, reason=excluded.reason, depth=excluded.depth, "
            "tension=excluded.tension, last_change_turn=excluded.last_change_turn, "
            "updated_at=excluded.updated_at",
            (
                channel_id, npc_name,
                rel.get("attitude", "neutral"), rel.get("reason", ""),
                rel.get("depth", 0), rel.get("tension", 0),
                rel.get("last_change_turn"),  # 키 없으면 None → NULL
                rel.get("last_updated", ""),
            ),
        )
        conn.commit()
        return True
    except Exception as e:
        logger.warning(f"[SQLiteStore] upsert_relation 실패 (무시): {channel_id}/{npc_name}: {e}")
        return False


def read_relations(channel_id: str) -> Optional[Dict[str, Dict[str, Any]]]:
    """채널의 전체 관계 조회. 행 0개 또는 실패 시 None (→ 호출부 JSON 폴백)."""
    if not channel_id or not _ensure_schema():
        return None
    conn = _get_conn()
    if conn is None:
        return None
    try:
        cur = conn.execute(
            f"SELECT npc_name, {_REL_COLS} FROM npc_relations WHERE channel_id=?",
            (channel_id,),
        )
        rows = cur.fetchall()
        if not rows:
            return None
        return {r[0]: _row_to_relation(r[1:]) for r in rows}
    except Exception as e:
        logger.warning(f"[SQLiteStore] read_relations 실패: {channel_id}: {e}")
        return None


def read_relation(channel_id: str, npc_name: str) -> Optional[Dict[str, Any]]:
    """관계 단건 조회 — B의 첫 실증 (통짜 로드 없는 포인트 질의). 없거나 실패 시 None."""
    if not channel_id or not npc_name or not _ensure_schema():
        return None
    conn = _get_conn()
    if conn is None:
        return None
    try:
        cur = conn.execute(
            f"SELECT {_REL_COLS} FROM npc_relations WHERE channel_id=? AND npc_name=?",
            (channel_id, npc_name),
        )
        row = cur.fetchone()
        return _row_to_relation(row) if row else None
    except Exception as e:
        logger.warning(f"[SQLiteStore] read_relation 실패: {channel_id}/{npc_name}: {e}")
        return None


def delete_relation(channel_id: str, npc_name: str) -> bool:
    """관계 1행 삭제 (identity reveal 등). 행이 없어도 True 아님 — rowcount 기준."""
    if not channel_id or not npc_name or not _ensure_schema():
        return False
    conn = _get_conn()
    if conn is None:
        return False
    try:
        cur = conn.execute(
            "DELETE FROM npc_relations WHERE channel_id=? AND npc_name=?",
            (channel_id, npc_name),
        )
        conn.commit()
        return cur.rowcount > 0
    except Exception as e:
        logger.warning(f"[SQLiteStore] delete_relation 실패 (무시): {channel_id}/{npc_name}: {e}")
        return False


def set_relation_turn(channel_id: str, npc_name: str, turn: int) -> bool:
    """last_change_turn만 갱신. 행이 존재할 때만 (— _save_attitude_turn 의 'if npc in attitudes' 미러)."""
    if not channel_id or not npc_name or not _ensure_schema():
        return False
    conn = _get_conn()
    if conn is None:
        return False
    try:
        cur = conn.execute(
            "UPDATE npc_relations SET last_change_turn=? WHERE channel_id=? AND npc_name=?",
            (int(turn), channel_id, npc_name),
        )
        conn.commit()
        return cur.rowcount > 0
    except Exception as e:
        logger.warning(f"[SQLiteStore] set_relation_turn 실패 (무시): {channel_id}/{npc_name}: {e}")
        return False


def count_relations(channel_id: Optional[str] = None) -> int:
    """관계 행 수 (채널 지정 시 해당 채널만). 검증용. 실패 시 -1."""
    if not _ensure_schema():
        return -1
    conn = _get_conn()
    if conn is None:
        return -1
    try:
        if channel_id:
            cur = conn.execute("SELECT COUNT(*) FROM npc_relations WHERE channel_id=?", (channel_id,))
        else:
            cur = conn.execute("SELECT COUNT(*) FROM npc_relations")
        return int(cur.fetchone()[0])
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
    if not _ensure_schema():
        return False
    conn = _get_conn()
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
    if not _ensure_schema():
        return False
    conn = _get_conn()
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
    if not channel_id or not _ensure_schema():
        return None
    conn = _get_conn()
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
    if not channel_id or not npc_name or not _ensure_schema():
        return None
    conn = _get_conn()
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
    if not channel_id or not npc_name or not _ensure_schema():
        return False
    conn = _get_conn()
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
    if not _ensure_schema():
        return -1
    conn = _get_conn()
    if conn is None:
        return -1
    try:
        if channel_id:
            cur = conn.execute("SELECT COUNT(*) FROM npc_knowledge WHERE channel_id=?", (channel_id,))
        else:
            cur = conn.execute("SELECT COUNT(*) FROM npc_knowledge")
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
    if not _ensure_schema():
        return False
    conn = _get_conn()
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
    if not _ensure_schema():
        return False
    conn = _get_conn()
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
    if not _ensure_schema():
        return False
    conn = _get_conn()
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
    if not channel_id or not _ensure_schema():
        return None
    conn = _get_conn()
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
    if not channel_id or not npc_name or not _ensure_schema():
        return None
    conn = _get_conn()
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
    if not channel_id or not npc_name or not _ensure_schema():
        return False
    conn = _get_conn()
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
    if not channel_id or not keep_sources or not _ensure_schema():
        return -1
    conn = _get_conn()
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
    """채널의 모든 SQLite 행 삭제 (전 테이블).
    reset_domain(파일 삭제 리셋) 미러 — 안 지우면 읽기 플래그 ON 시 유령 데이터 부활."""
    if not channel_id or not _ensure_schema():
        return False
    conn = _get_conn()
    if conn is None:
        return False
    try:
        for table in ("sessions", "npc_relations", "npc_knowledge", "npcs",
                      "history_log", "fermented_history", "deep_memory", "dai_logs",
                      "offscreen_ledger", "emotion_log", "turn_snapshot", "attitude_log"):
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
    """history_log에 1행 append (방벽 통과 후). 영구 기록 — DELETE는 리셋뿐."""
    if not channel_id or not isinstance(entry, dict):
        return False
    if not _ensure_schema():
        return False
    conn = _get_conn()
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
    if not channel_id or n <= 0 or not _ensure_schema():
        return None
    conn = _get_conn()
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
    if not channel_id or not query or not _ensure_schema():
        return []
    conn = _get_conn()
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
    if not _ensure_schema():
        return -1
    conn = _get_conn()
    if conn is None:
        return -1
    try:
        if channel_id:
            cur = conn.execute("SELECT COUNT(*) FROM history_log WHERE channel_id=?", (channel_id,))
        else:
            cur = conn.execute("SELECT COUNT(*) FROM history_log")
        return int(cur.fetchone()[0])
    except Exception:
        return -1


def clear_history_log(channel_id: str) -> bool:
    """history_log 채널 행 전체 삭제 — 리셋=완전 새 이야기 (사용자 결정 2026-06-10)."""
    if not channel_id or not _ensure_schema():
        return False
    conn = _get_conn()
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
    if not channel_id or not isinstance(entries, list) or not _ensure_schema():
        return False
    conn = _get_conn()
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
    """fermented_history 조회 (seq 순). 행 0개/실패 시 None."""
    if not channel_id or not _ensure_schema():
        return None
    conn = _get_conn()
    if conn is None:
        return None
    try:
        cur = conn.execute(
            "SELECT entry FROM fermented_history WHERE channel_id=? ORDER BY seq",
            (channel_id,),
        )
        rows = cur.fetchall()
        if not rows:
            return None
        out = []
        for (blob,) in rows:
            try:
                out.append(json.loads(blob))
            except Exception:
                continue
        return out if out else None
    except Exception as e:
        logger.warning(f"[SQLiteStore] read_fermented 실패: {channel_id}: {e}")
        return None


def sync_deep(channel_id: str, narrative: str, data: Dict[str, Any]) -> bool:
    """deep_memory upsert 미러."""
    if not channel_id or not _ensure_schema():
        return False
    conn = _get_conn()
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
    if not _ensure_schema():
        return False
    conn = _get_conn()
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


def append_ledger(channel_id: str, entry: Dict[str, Any], keep: int = 200) -> bool:
    """막간 장부 1행 기록 (validate_ledger_write 통과 후). 채널당 최근 keep행 롤링."""
    if not channel_id or not isinstance(entry, dict):
        return False
    if not _ensure_schema():
        return False
    conn = _get_conn()
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
    if not channel_id or n <= 0 or not _ensure_schema():
        return []
    conn = _get_conn()
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
    if not channel_id or not _ensure_schema():
        return False
    conn = _get_conn()
    if conn is None:
        return False
    try:
        conn.execute("DELETE FROM offscreen_ledger WHERE channel_id=?", (channel_id,))
        conn.commit()
        return True
    except Exception as e:
        logger.warning(f"[SQLiteStore] clear_ledger 실패 (무시): {channel_id}: {e}")
        return False


def read_dai_logs(channel_id: str, limit: int = 10) -> list:
    """최근 N턴 DAI 스냅샷 (오래된→최신). [(turn, dai_dict), ...]"""
    if not channel_id or limit <= 0 or not _ensure_schema():
        return []
    conn = _get_conn()
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

def append_emotion_log(channel_id: str, turn: int, emotion_bus: Dict[str, Any], keep: int = 1500) -> bool:
    """bus.emotion summary를 턴별 per-NPC 행으로 적립. 채널당 최근 keep행 롤링. 실패 무해.
    생성자(writer). emotion_bus = EmotionEngine.to_bus_dict() 결과."""
    if not channel_id or not isinstance(emotion_bus, dict):
        return False
    summary = emotion_bus.get("summary") or {}
    states = emotion_bus.get("states") or {}
    if not isinstance(summary, dict) or not summary:
        return False
    if not _ensure_schema():
        return False
    conn = _get_conn()
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
                json.dumps(states.get(npc, {}), ensure_ascii=False, default=str),
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
    if not channel_id or not npc_name or limit <= 0 or not _ensure_schema():
        return []
    conn = _get_conn()
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
    if not channel_id or limit <= 0 or not _ensure_schema():
        return []
    conn = _get_conn()
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


def read_emotion_turn(channel_id: str, turn: int) -> list:
    """독자: 특정 턴의 전 NPC 감정 스냅샷. [{npc_name, base, modifier, intensity, spike}, ...]"""
    if not channel_id or not _ensure_schema():
        return []
    conn = _get_conn()
    if conn is None:
        return []
    try:
        cur = conn.execute(
            "SELECT npc_name, base, modifier, intensity, spike FROM emotion_log "
            "WHERE channel_id=? AND turn=? ORDER BY npc_name",
            (channel_id, int(turn)),
        )
        return [{"npc_name": r[0], "base": r[1], "modifier": r[2], "intensity": r[3], "spike": bool(r[4])}
                for r in cur.fetchall()]
    except Exception as e:
        logger.warning(f"[SQLiteStore] read_emotion_turn 실패: {channel_id}: {e}")
        return []


# =========================================================
# [V10 적립] turn_snapshot — 턴 스칼라 상태 (생성자 + 독자)
# =========================================================

def append_turn_snapshot(channel_id: str, turn: int, snap: Dict[str, Any], keep: int = 400) -> bool:
    """턴당 스칼라 상태 1행(upsert: 같은 turn은 최신으로 교체). 채널당 최근 keep턴 롤링. 실패 무해."""
    if not channel_id or not isinstance(snap, dict):
        return False
    if not _ensure_schema():
        return False
    conn = _get_conn()
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
                1 if snap.get("judgment_active") else 0, 1 if snap.get("anomaly_triggered") else 0,
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


def read_turn_snapshots(channel_id: str, limit: int = 30) -> list:
    """독자: 최근 N턴 스냅샷 (오래된→최신)."""
    if not channel_id or limit <= 0 or not _ensure_schema():
        return []
    conn = _get_conn()
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
    if not _ensure_schema():
        return False
    conn = _get_conn()
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
    if not channel_id or limit <= 0 or not _ensure_schema():
        return []
    conn = _get_conn()
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


# =========================================================
# [V10 적립] attitude_log — 관계 태도 전이 이벤트 (생성자 + 독자)
# =========================================================

def append_attitude_log(channel_id: str, turn: int, npc_name: str, from_attitude: str,
                        to_attitude: str, result: str = "accepted", reason: str = "",
                        keep: int = 1000) -> bool:
    """태도 전이 1건 적립 (실 전이만 — 호출부에서 no-op/cooldown 거름). 채널당 keep행 롤링. 실패 무해."""
    if not channel_id or not npc_name:
        return False
    if not _ensure_schema():
        return False
    conn = _get_conn()
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
    if not channel_id or limit <= 0 or not _ensure_schema():
        return []
    conn = _get_conn()
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


def read_arc_window(channel_id: str, start_turn: int, end_turn: int) -> Dict[str, list]:
    """독자(범위): 턴 [start,end]의 감정/태도/스냅샷을 한 번에. 발효 청크 호(弧) digest용.
    {"emotion":[...], "attitudes":[...], "snapshots":[...]}. 실패 시 빈 묶음."""
    out = {"emotion": [], "attitudes": [], "snapshots": []}
    if not channel_id or not _ensure_schema():
        return out
    conn = _get_conn()
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
        cur = conn.execute(
            "SELECT turn, doom_phase, sd_tension FROM turn_snapshot "
            "WHERE channel_id=? AND turn BETWEEN ? AND ? ORDER BY turn", (channel_id, s, e))
        out["snapshots"] = [{"turn": r[0], "phase": r[1], "tension": r[2]} for r in cur.fetchall()]
        return out
    except Exception as e:
        logger.warning(f"[SQLiteStore] read_arc_window 실패: {channel_id}: {e}")
        return out


def read_deep(channel_id: str) -> Optional[Dict[str, Any]]:
    """deep_memory 조회. {"narrative": str, "data": dict} or None."""
    if not channel_id or not _ensure_schema():
        return None
    conn = _get_conn()
    if conn is None:
        return None
    try:
        cur = conn.execute("SELECT narrative, data FROM deep_memory WHERE channel_id=?", (channel_id,))
        row = cur.fetchone()
        if row is None:
            return None
        try:
            data = json.loads(row[1])
        except Exception:
            data = {}
        return {"narrative": row[0], "data": data}
    except Exception as e:
        logger.warning(f"[SQLiteStore] read_deep 실패: {channel_id}: {e}")
        return None


def count_npcs(channel_id: Optional[str] = None) -> int:
    """NPC 행 수. 검증용. 실패 시 -1."""
    if not _ensure_schema():
        return -1
    conn = _get_conn()
    if conn is None:
        return -1
    try:
        if channel_id:
            cur = conn.execute("SELECT COUNT(*) FROM npcs WHERE channel_id=?", (channel_id,))
        else:
            cur = conn.execute("SELECT COUNT(*) FROM npcs")
        return int(cur.fetchone()[0])
    except Exception:
        return -1
