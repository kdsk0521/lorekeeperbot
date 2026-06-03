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
