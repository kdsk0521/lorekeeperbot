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
from typing import Optional, Dict, Any, Iterator, Tuple

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
                    PRIMARY KEY (channel_id, npc_name)
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_knowledge_channel ON npc_knowledge(channel_id)")
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
    return {
        "knows": knows,
        "secrets_held": secrets,
        "would_share": bool(row[2]),
        "leak_risk": row[3],
        "last_updated": row[4],
    }

_KNOW_COLS = "knows, secrets_held, would_share, leak_risk, updated_at"


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
            "VALUES (?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(channel_id, npc_name) DO UPDATE SET "
            "knows=excluded.knows, secrets_held=excluded.secrets_held, "
            "would_share=excluded.would_share, leak_risk=excluded.leak_risk, "
            "updated_at=excluded.updated_at",
            (
                channel_id, npc_name,
                json.dumps(kn.get("knows", []), ensure_ascii=False),
                json.dumps(kn.get("secrets_held", []), ensure_ascii=False),
                1 if kn.get("would_share") else 0,
                kn.get("leak_risk", "none"),
                kn.get("last_updated", ""),
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
            )
            for name, kn in all_kn.items() if isinstance(kn, dict) and name
        ]
        conn.executemany(
            f"INSERT INTO npc_knowledge (channel_id, npc_name, {_KNOW_COLS}) "
            "VALUES (?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(channel_id, npc_name) DO UPDATE SET "
            "knows=excluded.knows, secrets_held=excluded.secrets_held, "
            "would_share=excluded.would_share, leak_risk=excluded.leak_risk, "
            "updated_at=excluded.updated_at",
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
    """채널의 모든 SQLite 행 삭제 (sessions + npc_relations + npc_knowledge + npcs).
    reset_domain(파일 삭제 리셋) 미러 — 안 지우면 읽기 플래그 ON 시 유령 데이터 부활."""
    if not channel_id or not _ensure_schema():
        return False
    conn = _get_conn()
    if conn is None:
        return False
    try:
        for table in ("sessions", "npc_relations", "npc_knowledge", "npcs"):
            conn.execute(f"DELETE FROM {table} WHERE channel_id=?", (channel_id,))
        conn.commit()
        return True
    except Exception as e:
        logger.warning(f"[SQLiteStore] delete_channel_rows 실패 (무시): {channel_id}: {e}")
        return False


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
