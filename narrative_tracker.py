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
        "Respond in the same language as the content.\n\n"
        "CRITICAL OUTPUT RULES:\n"
        "- Return JSON ONLY. No prose, preamble, or explanation before or after.\n"
        "- Your response MUST start directly with `{` and end with `}`.\n"
        "- Do NOT write \"Here is the JSON\" or any introductory text.\n"
        "- Schema: {\"summaries\": [{\"id\": N, \"context\": \"...\", \"tension\": \"...\"}]}\n\n"
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

# =========================================================
# Tension priority + decay (체호프 vs 세헤라자드, 인간 기억 모델)
# =========================================================
# Sprint D (2026-04-28): ongoing_tensions에 priority + last_touched 메타데이터.
# LIBRA Story Ledger X 흡수 — 무거운 약속만 surface, 가벼운 것은 자연 소멸.
# 체호프의 미발사된 총 = RP의 살아있는 세계.

TENSION_DORMANT_TURNS = 12   # turn - last_touched > 12 → prompt 제외 (dormant)
TENSION_EXPIRE_TURNS = 36    # turn - last_touched > 36 AND priority < 0.5 → 자연 소멸
TENSION_MIN_PROMPT_PRIORITY = 0.25


def _normalize_tension(item, current_turn: int):
    """str 또는 dict 둘 다 받아 dict로 정규화. 마이그레이션 호환.

    Sprint E (2026-04-28): kind / primary 필드 추가.
    - kind: "open_question" (default) / "payoff" / "lock"
    - primary: scene의 무게중심 (priority/dormant 무관 surface, 단 do_not_resolve_yet은 여전히 제외)
    """
    valid_kinds = ("open_question", "payoff", "lock")
    if isinstance(item, str):
        return {
            "label": item[:120],
            "priority": 0.5,
            "introduced_turn": current_turn,
            "last_touched_turn": current_turn,
            "do_not_resolve_yet": False,
            "kind": "open_question",
            "primary": False,
        }
    if isinstance(item, dict):
        kind_raw = str(item.get("kind", "open_question"))
        kind = kind_raw if kind_raw in valid_kinds else "open_question"
        return {
            "label": str(item.get("label", ""))[:120],
            "priority": max(0.0, min(1.0, float(item.get("priority", 0.5)))),
            "introduced_turn": int(item.get("introduced_turn", current_turn)),
            "last_touched_turn": int(item.get("last_touched_turn", current_turn)),
            "do_not_resolve_yet": bool(item.get("do_not_resolve_yet", False)),
            "kind": kind,
            "primary": bool(item.get("primary", False)),
        }
    return None


def apply_tension_decay(state: dict, current_turn: int) -> dict:
    """매 턴 호출. 모든 storyline의 ongoing_tensions에 decay 룰 적용.

    - dormant: turn - last_touched > 12 → prompt 제외 (데이터는 보존)
    - expire: turn - last_touched > 36 AND priority < 0.5 → 자연 소멸 (제거)
    - do_not_resolve_yet=True는 decay 면제 (명시적 보류는 영원)
    """
    for sl in state.get("storylines", []):
        tensions = sl.get("ongoing_tensions", [])
        if not tensions:
            continue
        normalized = []
        for item in tensions:
            t = _normalize_tension(item, current_turn)
            if t is None:
                continue
            age = current_turn - t["last_touched_turn"]
            # expire: 자연 소멸 (단 do_not_resolve_yet은 면제, priority >= 0.5도 보존)
            if (not t["do_not_resolve_yet"]
                    and age > TENSION_EXPIRE_TURNS
                    and t["priority"] < 0.5):
                continue  # 자연 소멸
            normalized.append(t)
        sl["ongoing_tensions"] = normalized
    return state




def apply_tension_labels(state: dict, labeled_tensions: list, current_turn: int) -> dict:
    """Sprint G (2026-04-28): Flash 사후 라벨링 결과를 ongoing_tensions에 반영.

    BG Extract narrative section의 tensions 필드 결과를 받아:
    - label로 active storylines의 ongoing_tensions에서 매칭 시도
    - 매칭 → priority/kind/primary 갱신, last_touched_turn = current_turn
    - 미매칭 → primary storyline (또는 가장 active)에 새 entry insert

    매칭 전략:
    - case-insensitive substring (label[:20] 비교)
    - 첫 active storyline이 default insertion target (storyline_id 미지정 시)

    Anti-Chekhov 자세 — *진짜 발사된 약속만* 라벨됨. 발사 안 된 후보는 별도 layer (decay).
    """
    if not labeled_tensions:
        return state

    active = [s for s in state.get("storylines", []) if s.get("status") == "active"]
    if not active:
        return state

    # primary가 1개만 보존되도록 — 새 라벨에 primary=True가 있으면 기존 primary 모두 false 처리 후 적용
    has_new_primary = any(
        isinstance(item, dict) and item.get("primary") for item in labeled_tensions
    )
    if has_new_primary:
        for sl in active:
            for t in sl.get("ongoing_tensions", []):
                if isinstance(t, dict) and t.get("primary"):
                    t["primary"] = False

    target_sl = active[0]  # default insertion target

    for raw in labeled_tensions:
        if not isinstance(raw, dict):
            continue
        label = str(raw.get("label", ""))[:120].strip()
        if not label:
            continue
        kind_raw = str(raw.get("kind", "open_question"))
        kind = kind_raw if kind_raw in ("open_question", "payoff", "lock") else "open_question"
        primary = bool(raw.get("primary", False))
        priority = max(0.0, min(1.0, float(raw.get("priority", 0.5))))

        # 매칭 — substring 양방향
        matched = False
        label_short = label[:20]
        for sl in active:
            for t in sl.get("ongoing_tensions", []):
                norm_t = _normalize_tension(t, current_turn)
                if norm_t is None:
                    continue
                tl = norm_t["label"][:20]
                if not tl or not label_short:
                    continue
                if label_short in norm_t["label"] or tl in label:
                    # 갱신
                    norm_t["last_touched_turn"] = current_turn
                    norm_t["priority"] = max(norm_t["priority"], priority)
                    norm_t["kind"] = kind
                    norm_t["primary"] = primary
                    # 원본 자리에 정규화된 dict 저장 (str legacy → dict 마이그레이션)
                    idx = sl["ongoing_tensions"].index(t)
                    sl["ongoing_tensions"][idx] = norm_t
                    matched = True
                    break
            if matched:
                break

        if not matched:
            # 새 entry
            new_entry = {
                "label": label,
                "priority": priority,
                "introduced_turn": current_turn,
                "last_touched_turn": current_turn,
                "do_not_resolve_yet": False,
                "kind": kind,
                "primary": primary,
            }
            target_sl.setdefault("ongoing_tensions", []).append(new_entry)

    return state


def _is_tension_promptable(t: dict, current_turn: int) -> bool:
    """tension이 prompt에 surface할 자격이 있는가.

    Sprint E (2026-04-28): primary=True는 priority/dormant 무관 surface.
    단 do_not_resolve_yet은 여전히 제외 (명시적 보류는 의도된 silence).
    """
    if t["do_not_resolve_yet"]:
        return False  # 명시적 보류는 surface 안 함
    if t.get("primary"):
        return True   # 무게중심은 항상 surface (단 hold 제외)
    if t["priority"] < TENSION_MIN_PROMPT_PRIORITY:
        return False
    age = current_turn - t["last_touched_turn"]
    if age > TENSION_DORMANT_TURNS:
        return False  # dormant
    return True


def format_storylines_for_prompt(state: dict, current_turn: int = 0) -> str:
    """활성 스토리라인을 프롬프트용 텍스트로 변환. Slot 11 (Chapter) 확장.

    Sprint D (2026-04-28): tension priority + decay 적용.
    무거운 약속(priority >= 0.25) + 활성(dormant 아님) + non-hold만 surface.
    str legacy tensions도 호환 (_normalize_tension).
    """
    active = [s for s in state.get("storylines", []) if s.get("status") == "active"]
    if not active:
        return ""

    parts = ["[Active Storylines]"]
    for sl in active[:4]:
        name = sl.get("name", "?")
        entities = ", ".join(sl.get("entities", [])[:5])
        context = sl.get("current_context", "")[:180]
        # tensions: priority+decay 룰 적용 후 surface
        raw = sl.get("ongoing_tensions", [])
        promptable = []
        for item in raw:
            t = _normalize_tension(item, current_turn)
            if t is None:
                continue
            if _is_tension_promptable(t, current_turn):
                promptable.append(t)
        # Sprint E: primary 우선 정렬 + kind별 라벨 prefix
        promptable.sort(key=lambda x: (not x.get("primary"), -x["priority"]))
        top = promptable[:2]
        line = f"  [{name}] ({entities}): {context}"
        if top:
            def _fmt_tension(t):
                lbl = t["label"]
                k = t.get("kind", "open_question")
                if t.get("primary"):
                    return f"★{lbl}"
                if k == "payoff":
                    return f"[Payoff] {lbl}"
                if k == "lock":
                    return f"[Lock] {lbl}"
                return lbl
            line += f" | Tension: {'; '.join(_fmt_tension(t) for t in top)}"
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
