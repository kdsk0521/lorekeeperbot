"""
Lorekeeper V10 — State Write Guards (Sprint 1)

철학: 콜은 돈, 코드는 공짜. LLM에게 "정확히 써줘"를 비는 대신,
깨질 수 있는 LLM 출력과 DB 사이에 코드 방벽을 세운다.
(아이스버그 — Flash 출력을 코드가 번역·검증해 Pro로 넘김 — 와 동일 혈통)

설계 원칙 (v10_sprint1_relations_spec.md §4-4):
- 순수 함수. DB I/O 없음, 외부 모듈 import 없음 (단독 스모크 가능).
- 어떤 입력에도 예외를 던지지 않는다. 정제 불가 시 None.
- **dual-write 철칙: JSON 경로가 받는 것은 다 받는다. 거부는 둘 다 거부일 때만.**
  → enum 위반은 경고 로그만 (identity reveal 등 직접 경로가 임의 문자열 저장 가능하므로).
  → 거부(None)는 "행 자체를 특정할 수 없는" 경우만: npc_name 불량, payload가 dict 아님,
    (existing_npcs 제공 시) 실재하지 않는 NPC (Contract-First).

확장 여지: 나중에 LLM이 구조화된 쓰기 의도를 직접 뱉어도 같은 방벽을 통과시키면 됨.
방벽 재사용, 입력만 교체.
"""

import logging
from typing import Any, Dict, Iterable, Optional

logger = logging.getLogger("StateGuards")

# npc_manager.ATTITUDE_LEVELS와 동기 (검증용 참조 — 강제 아님, 경고만)
KNOWN_ATTITUDES = ("hostile", "unfriendly", "neutral", "friendly", "devoted")

_CLAMP_MIN, _CLAMP_MAX = 0, 100


def _safe_int(value: Any, default: int = 0) -> int:
    """int 강제 변환. bool/float/숫자문자열 허용, 실패 시 default."""
    try:
        if isinstance(value, bool):  # True/False가 1/0으로 새는 것 방지
            return default
        return int(value)
    except (TypeError, ValueError):
        return default


def validate_relation_write(
    npc_name: Any,
    payload: Any,
    existing_npcs: Optional[Iterable[str]] = None,
) -> Optional[Dict[str, Any]]:
    """관계 쓰기 직전 검증/정규화 방벽. 모든 upsert_relation은 이걸 통과한다.

    Args:
        npc_name: 저장 키가 될 NPC 이름 (_resolve_npc_name을 이미 거친 이름이어야 함)
        payload: JSON attitude dict 포맷 후보
                 (attitude/reason/depth/tension/last_updated[/last_change_turn])
        existing_npcs: 제공 시 Contract-First 실재 확인 (없는 NPC엔 안 씀)

    Returns:
        정제된 dict (DB에 그대로 넣어도 안전) — 또는 None (이번 쓰기만 무시, 행 무손상)
    """
    # --- 거부 사유 1: 행을 특정할 수 없음 ---
    if not isinstance(npc_name, str) or not npc_name.strip():
        logger.warning("[Guard] relation write 거부: npc_name 불량 (%r)", npc_name)
        return None
    if not isinstance(payload, dict):
        logger.warning("[Guard] relation write 거부: payload가 dict 아님 (%s) for %s",
                       type(payload).__name__, npc_name)
        return None

    npc_name = npc_name.strip()

    # --- 거부 사유 2: Contract-First — 실재하지 않는 NPC ---
    if existing_npcs is not None and npc_name not in set(existing_npcs):
        logger.warning("[Guard] relation write 거부: 미등록 NPC '%s' (Contract-First)", npc_name)
        return None

    # --- 정규화 (거부 아님 — JSON이 받는 건 받되, 타입만 강제) ---
    attitude = payload.get("attitude", "neutral")
    if not isinstance(attitude, str) or not attitude:
        attitude = "neutral"
    elif attitude not in KNOWN_ATTITUDES:
        # enum 강제 ❌ — parity 철칙. 경고만.
        logger.warning("[Guard] unknown attitude '%s' for %s (저장은 함)", attitude, npc_name)

    reason = payload.get("reason", "")
    if not isinstance(reason, str):
        reason = str(reason) if reason is not None else ""

    depth = max(_CLAMP_MIN, min(_CLAMP_MAX, _safe_int(payload.get("depth"), 0)))
    tension = max(_CLAMP_MIN, min(_CLAMP_MAX, _safe_int(payload.get("tension"), 0)))

    last_updated = payload.get("last_updated", "")
    if not isinstance(last_updated, str):
        last_updated = str(last_updated) if last_updated is not None else ""

    clean: Dict[str, Any] = {
        "attitude": attitude,
        "reason": reason,
        "depth": depth,
        "tension": tension,
        "last_updated": last_updated,
    }

    # last_change_turn: 키 부재는 부재로 보존 (§1b quirk — NULL 미러)
    if "last_change_turn" in payload:
        lct = payload.get("last_change_turn")
        if lct is not None:
            clean["last_change_turn"] = _safe_int(lct, -999)

    return clean


def _safe_str_list(value: Any, cap: int = 0) -> list:
    """list[str] 강제. 비list는 빈 리스트, 비str 원소는 str() 변환. cap>0이면 최근 cap개."""
    if not isinstance(value, list):
        return []
    out = [v if isinstance(v, str) else str(v) for v in value if v is not None]
    return out[-cap:] if cap > 0 else out


def validate_knowledge_write(npc_name: Any, payload: Any) -> Optional[Dict[str, Any]]:
    """[Sprint 2-A] NPC 지식 쓰기 방벽. 모든 upsert_knowledge는 이걸 통과.

    거부는 행 특정 불가만 (npc_name 불량 / payload 비dict).
    leak_risk enum 강제 ❌ — 자유 문자열 (parity 철칙)."""
    if not isinstance(npc_name, str) or not npc_name.strip():
        logger.warning("[Guard] knowledge write 거부: npc_name 불량 (%r)", npc_name)
        return None
    if not isinstance(payload, dict):
        logger.warning("[Guard] knowledge write 거부: payload가 dict 아님 (%s) for %s",
                       type(payload).__name__, npc_name)
        return None

    leak_risk = payload.get("leak_risk", "none")
    if not isinstance(leak_risk, str) or not leak_risk:
        leak_risk = "none"

    last_updated = payload.get("last_updated", "")
    if not isinstance(last_updated, str):
        last_updated = str(last_updated) if last_updated is not None else ""

    return {
        "knows": _safe_str_list(payload.get("knows"), cap=20),
        "secrets_held": _safe_str_list(payload.get("secrets_held")),
        "would_share": bool(payload.get("would_share")),
        "leak_risk": leak_risk,
        "last_updated": last_updated,
    }


def validate_npc_write(npc_name: Any, data: Any) -> Optional[Dict[str, Any]]:
    """[Sprint 2-B] NPC 본체 쓰기 방벽. 필드 스키마 강제 ❌ — NPC dict는 의도적 자유 문서.

    거부: npc_name 불량 / data 비dict / JSON 직렬화 불가 (행 오염 방지).
    통과 시 data 원본 그대로 반환 (변형 없음 — JSON 경로와 동일 내용 보장)."""
    if not isinstance(npc_name, str) or not npc_name.strip():
        logger.warning("[Guard] npc write 거부: npc_name 불량 (%r)", npc_name)
        return None
    if not isinstance(data, dict):
        logger.warning("[Guard] npc write 거부: data가 dict 아님 (%s) for %s",
                       type(data).__name__, npc_name)
        return None
    try:
        import json as _json
        _json.dumps(data, ensure_ascii=False)
    except (TypeError, ValueError) as e:
        logger.warning("[Guard] npc write 거부: JSON 직렬화 불가 for %s: %s", npc_name, e)
        return None
    return data
