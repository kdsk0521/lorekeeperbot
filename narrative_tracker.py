"""
Lorekeeper — Narrative Tracker (v1.0)
턴별 서사 기록 + 엔티티 상태 이력 추적.

역할: 관찰 + 집계 + 보강 레이어
- 타이밍 결정 안 함 (Storyteller 소관)
- 페이싱 안 함 (StoryDirector 소관)
- 기억 압축 안 함 (Fermentation 소관)
- ai_session_memory 직접 수정 안 함 (읽기만)

Sprint 1: 턴 로그 + 엔티티 상태 이력
Sprint 2: 스토리라인 분류 + 요약 (예정)
"""

import logging
from typing import Dict, Any, List, Optional

logger = logging.getLogger("NarrativeTracker")

# =========================================================
# 턴 로그 — 매 턴 기록
# =========================================================

MAX_TURN_LOG = 50
MAX_ENTITY_STATES = 10
MAX_CRITICAL_MOMENTS = 20


def record_turn(
    state: dict,
    turn: int,
    user_brief: str,
    ai_brief: str,
    involved_entities: List[str],
    quality_flags: Optional[dict] = None,
) -> dict:
    """턴 로그에 새 턴 기록. quality_flags에서 중요도 추론."""
    if "turn_log" not in state:
        state["turn_log"] = []

    # quality_flags에서 중요도 추론 (LIBRA의 turn_importance 대용)
    importance = _infer_importance(quality_flags, involved_entities)

    entry = {
        "turn": turn,
        "user_brief": (user_brief or "")[:200],
        "ai_brief": (ai_brief or "")[:300],
        "entities": involved_entities[:8],
        "importance": importance,
    }
    state["turn_log"].append(entry)

    # 최대 크기 유지
    if len(state["turn_log"]) > MAX_TURN_LOG:
        state["turn_log"] = state["turn_log"][-MAX_TURN_LOG:]

    logger.info(
        "[NarrativeTracker] Turn %d recorded | entities=%s | importance=%d",
        turn, ",".join(involved_entities[:4]), importance
    )
    return state


def _infer_importance(quality_flags: Optional[dict], entities: List[str]) -> int:
    """quality_flags + 엔티티 수에서 턴 중요도 추론 (1-10)."""
    if not quality_flags:
        return 5

    score = 5
    # 수렴 경고 = 관계/아크 변화 진행 중 → 중요
    if quality_flags.get("convergence_warning"):
        score += 2
    # 정체 경고 = 서사 정체 → 낮은 중요도
    if quality_flags.get("stagnation_warning"):
        score -= 1
    # MSE 이탈 = 심리 이상 감지 → 중요
    if quality_flags.get("mse_deviation"):
        score += 1
    # 불일치 = NPC 모순 → 중요
    if quality_flags.get("dissonance_flag"):
        score += 1
    # 엔티티 많으면 복잡한 씬 → 약간 중요
    if len(entities) >= 3:
        score += 1

    return max(1, min(10, score))


# =========================================================
# 엔티티 상태 이력 — NPC별 상태 변화 추적
# =========================================================

def update_entity_states(
    state: dict,
    turn: int,
    entity_state_changes: Optional[Dict[str, dict]] = None,
) -> dict:
    """cognition 배치의 entity_state_changes를 이력에 기록."""
    if not entity_state_changes or not isinstance(entity_state_changes, dict):
        return state

    if "entity_state_log" not in state:
        state["entity_state_log"] = {}

    for npc_name, changes in entity_state_changes.items():
        if not isinstance(npc_name, str) or not isinstance(changes, dict):
            continue
        # 변화 없으면 스킵
        has_change = any(
            changes.get(k) is not None
            for k in ("location", "mood", "health", "notable")
        )
        if not has_change:
            continue

        if npc_name not in state["entity_state_log"]:
            state["entity_state_log"][npc_name] = {
                "recent_states": [],
                "critical_moments": [],
            }

        npc_log = state["entity_state_log"][npc_name]

        # 상태 기록
        entry = {"turn": turn}
        if changes.get("location"):
            entry["location"] = changes["location"]
        if changes.get("mood"):
            entry["mood"] = changes["mood"]
        if changes.get("health"):
            entry["health"] = changes["health"]

        npc_log["recent_states"].append(entry)
        if len(npc_log["recent_states"]) > MAX_ENTITY_STATES:
            npc_log["recent_states"] = npc_log["recent_states"][-MAX_ENTITY_STATES:]

        # critical moment 감지: 위치 변경 or 건강 변화
        is_critical = False
        if changes.get("health"):
            is_critical = True
        if changes.get("notable"):
            is_critical = True
        # 위치 변경: 이전 상태와 비교
        if changes.get("location") and len(npc_log["recent_states"]) >= 2:
            prev = npc_log["recent_states"][-2]
            if prev.get("location") and prev["location"] != changes["location"]:
                is_critical = True

        if is_critical:
            desc = changes.get("notable") or changes.get("health") or f"위치: {changes.get('location', '?')}"
            npc_log["critical_moments"].append({
                "turn": turn,
                "description": str(desc)[:200],
            })
            if len(npc_log["critical_moments"]) > MAX_CRITICAL_MOMENTS:
                npc_log["critical_moments"] = npc_log["critical_moments"][-MAX_CRITICAL_MOMENTS:]

            logger.info(
                "[EntityState] Critical moment: %s at turn %d — %s",
                npc_name, turn, desc[:80]
            )

    return state


# =========================================================
# 상태 로드/저장 헬퍼
# =========================================================

def get_default_state() -> dict:
    """초기 narrative_tracker 상태."""
    return {
        "turn_log": [],
        "entity_state_log": {},
        "storylines": [],
        "last_summary_turn": 0,
        "archived_storylines": [],
    }


# =========================================================
# Sprint 2: 스토리라인 분류
# =========================================================

MAX_STORYLINES = 8
SUMMARY_INTERVAL = 5


def assign_to_storyline(state: dict, turn_entry: dict) -> dict:
    """턴 로그 엔트리를 엔티티 겹침 기반으로 스토리라인에 분류."""
    entities = turn_entry.get("entities", [])
    if not entities:
        return state

    if "storylines" not in state:
        state["storylines"] = []

    # 기존 스토리라인과 겹침 계산
    best_match = None
    best_score = 0
    for sl in state["storylines"]:
        if sl.get("status") not in ("active", None):
            continue
        sl_entities = set(sl.get("entities", []))
        overlap = len(sl_entities & set(entities))
        score = overlap / max(len(entities), 1)
        if score > best_score and score >= 0.3:
            best_score = score
            best_match = sl

    turn = turn_entry.get("turn", 0)

    if best_match:
        best_match["turns"] = best_match.get("turns", [])
        best_match["turns"].append(turn)
        best_match["last_turn"] = turn
        # 새 엔티티 추가
        existing = set(best_match.get("entities", []))
        for e in entities:
            if e not in existing:
                best_match["entities"].append(e)
        # 현재 컨텍스트 업데이트
        brief = turn_entry.get("ai_brief", "")
        if brief:
            best_match["current_context"] = brief[:260]
    else:
        # 새 스토리라인 생성
        sl_id = len(state["storylines"]) + 1
        new_sl = {
            "id": sl_id,
            "name": f"Storyline #{sl_id}",
            "entities": list(entities),
            "turns": [turn],
            "first_turn": turn,
            "last_turn": turn,
            "current_context": turn_entry.get("ai_brief", "")[:260],
            "key_points": [],
            "ongoing_tensions": [],
            "summaries": [],
            "status": "active",
        }
        state["storylines"].append(new_sl)

        # 스토리라인 수 제한 — active가 MAX 초과 시 오래된 것부터 dormant
        active = [s for s in state["storylines"] if s.get("status") == "active"]
        while len(active) > MAX_STORYLINES:
            oldest = min(active, key=lambda s: s.get("last_turn", 0))
            oldest["status"] = "dormant"
            active.remove(oldest)
            logger.info("[NarrativeTracker] Storyline '%s' → dormant (cap=%d)", oldest.get("name"), MAX_STORYLINES)

        # dormant 누적 정리 (10개 초과 시 오래된 것 제거)
        dormant = [s for s in state["storylines"] if s.get("status") == "dormant"]
        if len(dormant) > 10:
            state["storylines"] = [
                s for s in state["storylines"]
                if s.get("status") != "dormant" or s in dormant[-10:]
            ]

    return state


def resolve_storyline(state: dict, storyline_id: int) -> dict:
    """스토리라인을 resolved → archived로 이동."""
    found = False
    for sl in state.get("storylines", []):
        if sl.get("id") == storyline_id and sl.get("status") == "active":
            sl["status"] = "resolved"
            if "archived_storylines" not in state:
                state["archived_storylines"] = []
            state["archived_storylines"].append({
                "id": sl["id"],
                "name": sl.get("name", ""),
                "entities": sl.get("entities", []),
                "summary": sl.get("current_context", ""),
                "turns": len(sl.get("turns", [])),
            })
            if len(state["archived_storylines"]) > 20:
                state["archived_storylines"] = state["archived_storylines"][-20:]
            found = True
            logger.info("[NarrativeTracker] Storyline #%d '%s' resolved → archived", storyline_id, sl.get("name", ""))
            break
    if not found:
        logger.warning("[NarrativeTracker] Storyline #%d not found or not active", storyline_id)
    # resolved 제거
    state["storylines"] = [s for s in state.get("storylines", []) if s.get("status") != "resolved"]
    return state


async def summarize_if_needed(state: dict, current_turn: int, client=None, model_id: str = "") -> dict:
    """5턴 간격으로 스토리라인별 요약. Flash 소형 콜 1회."""
    last = state.get("last_summary_turn", 0)
    if current_turn - last < SUMMARY_INTERVAL:
        return state

    active_storylines = [s for s in state.get("storylines", []) if s.get("status") == "active"]
    if not active_storylines:
        return state

    # 최근 턴 로그에서 스토리라인별 관련 턴 수집
    recent_log = state.get("turn_log", [])[-15:]
    for sl in active_storylines:
        sl_turns = set(sl.get("turns", []))
        related = [t for t in recent_log if t.get("turn") in sl_turns]
        if len(related) < 2:
            continue

        # 요약 텍스트 생성 (LLM 없이 휴리스틱)
        brief_parts = [t.get("ai_brief", "") for t in related[-5:] if t.get("ai_brief")]
        if brief_parts:
            summary = " → ".join(brief_parts)[:300]
            sl["summaries"] = sl.get("summaries", [])
            sl["summaries"].append({
                "up_to_turn": current_turn,
                "summary": summary,
            })
            if len(sl["summaries"]) > 10:
                sl["summaries"] = sl["summaries"][-10:]
            sl["current_context"] = summary

    # Flash 요약 콜 (client가 있을 때만)
    if client and model_id and active_storylines:
        try:
            await _flash_summarize_storylines(state, active_storylines, recent_log, client, model_id)
        except Exception as e:
            logger.warning("[NarrativeTracker] Flash summary failed: %s", e)

    state["last_summary_turn"] = current_turn
    logger.info("[NarrativeTracker] Storyline summaries updated at turn %d", current_turn)
    return state


async def _flash_summarize_storylines(state, storylines, recent_log, client, model_id):
    """Flash 소형 콜로 스토리라인 요약 생성."""
    from google.genai import types
    import config as _cfg
    import json

    def _sanitize(text: str) -> str:
        """프롬프트 주입 방지: 줄바꿈/따옴표 제거."""
        return text.replace("\n", " ").replace('"', "'").strip()

    sl_texts = []
    for sl in storylines[:4]:
        sl_turns = set(sl.get("turns", []))
        related = [t for t in recent_log if t.get("turn") in sl_turns][-5:]
        turn_text = " | ".join(
            f"T{t['turn']}: {_sanitize(t.get('user_brief', '')[:60])} → {_sanitize(t.get('ai_brief', '')[:80])}"
            for t in related
        )
        entities_str = ",".join(_sanitize(e) for e in sl.get("entities", [])[:5])
        sl_texts.append(f"[{_sanitize(sl.get('name', '?'))}] Entities: {entities_str}\n{turn_text}")

    prompt = (
        "Summarize each storyline's current state in 1-2 sentences. "
        "Focus on what changed, what's unresolved, and where the story is heading. "
        "Respond in the same language as the content.\n"
        "Output JSON: {\"summaries\": [{\"id\": N, \"context\": \"...\", \"tension\": \"...\"}]}\n\n"
        + "\n\n".join(sl_texts)
    )

    gen_config = types.GenerateContentConfig(
        temperature=0.2,
        max_output_tokens=600,
        response_mime_type="application/json",
        safety_settings=_cfg.SAFETY_SETTINGS,
    )

    response = await client.aio.models.generate_content(
        model=model_id, contents=[prompt], config=gen_config
    )
    if not response.text:
        return

    import bot_utils
    cleaned = bot_utils.clean_json_text(response.text)
    try:
        result = json.loads(cleaned)
    except json.JSONDecodeError:
        repaired = bot_utils.repair_json(cleaned)
        result = json.loads(repaired)

    for item in result.get("summaries", []):
        sl_id = item.get("id")
        context = item.get("context", "")
        tension = item.get("tension", "")
        for sl in storylines:
            if sl.get("id") == sl_id:
                if context:
                    sl["current_context"] = context[:260]
                if tension:
                    sl["ongoing_tensions"] = sl.get("ongoing_tensions", [])
                    sl["ongoing_tensions"].append(tension[:120])
                    sl["ongoing_tensions"] = sl["ongoing_tensions"][-5:]
                break


# =========================================================
# 프롬프트 포맷 (Slot 11, Slot 7)
# =========================================================

def format_storylines_for_prompt(state: dict) -> str:
    """활성 스토리라인을 프롬프트용 텍스트로 변환. Slot 11 (Chapter) 확장."""
    active = [s for s in state.get("storylines", []) if s.get("status") == "active"]
    if not active:
        return ""

    parts = ["[Active Storylines]"]
    for sl in active[:4]:
        name = sl.get("name", "?")
        entities = ", ".join(sl.get("entities", [])[:5])
        context = sl.get("current_context", "")[:180]
        tensions = sl.get("ongoing_tensions", [])[-2:]
        line = f"  [{name}] ({entities}): {context}"
        if tensions:
            line += f" | Tension: {'; '.join(tensions)}"
        parts.append(line)

    return "\n".join(parts)


def format_entity_state_for_prompt(state: dict, npc_name: str) -> str:
    """NPC 상태 이력을 프롬프트용 텍스트로 변환. Slot 7 NPC 프로필에 추가."""
    log = state.get("entity_state_log", {}).get(npc_name)
    if not log:
        return ""

    parts = []
    recent = log.get("recent_states", [])[-3:]
    if recent:
        trail = []
        for s in recent:
            segments = [f"T{s.get('turn', '?')}"]
            if s.get("location"):
                segments.append(s["location"])
            if s.get("mood"):
                segments.append(s["mood"])
            trail.append(":".join(segments))
        parts.append(f"  Recent: {' → '.join(trail)}")

    critical = log.get("critical_moments", [])[-2:]
    if critical:
        for c in critical:
            parts.append(f"  Critical(T{c.get('turn', '?')}): {c.get('description', '')[:80]}")

    return "\n".join(parts)
