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
        # [2026-08-12 출력파생 §8] context_kind 도장 — ai_brief는 **직전 산문 머리 300자 원문**
        #   (orchestration.py:1090 `response[:300]`, 요약 아님)이므로 출처를 "raw"로 표기한다.
        #   렌더 가장자리(format_storylines_for_prompt)가 이 도장을 보고 원문 재진입을 막는다.
        #   발효 압축(fermentation:830)은 분석 콜이라 kind 무관 원문 그대로 소비 — 무접촉.
        brief = turn_entry.get("ai_brief", "")
        if brief:
            best_match["current_context"] = brief[:260]
            best_match["context_kind"] = "raw"
    else:
        # 새 스토리라인 생성
        # D-6 fix: len+1은 prune/resolve 후 id 충돌(중복) → arc_update가 first-match로 오라우팅.
        # 모노토닉 카운터로 유일성 보장(기존 state는 len+1 fallback으로 backward-compat).
        sl_id = state.get("_next_storyline_id", len(state["storylines"]) + 1)
        state["_next_storyline_id"] = sl_id + 1
        new_sl = {
            "id": sl_id,
            "name": f"Storyline #{sl_id}",
            "entities": list(entities),
            "turns": [turn],
            "first_turn": turn,
            "last_turn": turn,
            "current_context": turn_entry.get("ai_brief", "")[:260],
            "context_kind": "raw",  # [2026-08-12 출력파생 §8] 산문 원문 출처 — 렌더 미주입
            "key_points": [],
            "ongoing_tensions": [],
            "summaries": [],
            "status": "active",
            # === Arc 시스템 (Phase 1) ===
            # 신규 storyline은 is_arc=False. Arc 격상 시 promote 함수가 신규 필드 추가.
            # 자세히: 파티쳇수정/arc_spec_v2.md §4.1
            "is_arc": False,
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
            # [2026-08-12 출력파생 §8] 이 휴리스틱 요약은 ai_brief를 " → "로 이은 것 = **여전히 산문 원문**.
            #   앞선 Flash 요약이 남긴 "summary" 도장을 반드시 되돌린다(안 하면 raw가 요약 행세).
            sl["context_kind"] = "raw"

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
                    # [2026-08-12 출력파생 §8] Flash 요약 콜 산출 = 파생 요약 → 렌더 주입 허가
                    sl["context_kind"] = "summary"
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
        # [2026-08-12 출력파생 §8] 산문 원문 봉인 — 지문 원문의 렌더 재진입은 히스토리 한 곳뿐이다.
        #   current_context는 5턴마다 Flash 요약으로 덮이지만 그 사이 턴은 **직전 산문 머리 원문**이라
        #   Slot 11로 직행하고 있었다(§7-3). "summary" 도장이 찍힌 것만 본문으로 싣고,
        #   raw/legacy(도장 없음 = 보수적으로 raw 취급)는 엔티티 라벨 한 줄로 대체한다.
        #   → 스토리라인의 **존재·구성원·긴장**은 그대로 전달되고 문장만 빠진다.
        if sl.get("context_kind") == "summary":
            context = sl.get("current_context", "")[:180]
        else:
            context = "(unfolding)"
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
    _logs = state.get("entity_state_log", {}) or {}
    log = _logs.get(npc_name)
    # [2026-07-28] 이름 해상도 — 기록 시점과 조회 시점의 표기가 다를 수 있다
    # (Theoria가 "리리스"/"Lilith(리리스)"를 번갈아 씀). 리터럴 미스면 별칭·정규화로 한 번 더.
    if not log and _logs:
        try:
            import domain_manager as _dm_res
            _alt = _dm_res._find_npc_key(_logs, npc_name)
            if _alt:
                log = _logs.get(_alt)
        except Exception:
            pass
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


# =========================================================
# ARC SYSTEM (Phase 1: 스키마 + helper)
# =========================================================
# 자세히: 파티쳇수정/arc_spec_v2.md
# Phase 1 = 자료구조 + 격상/격하 함수만. 좌표 갱신(tick_arcs)은 Phase 3.

def is_arc(sl: dict) -> bool:
    """Storyline이 arc인지. 단순 helper — 누락 default False."""
    return bool(sl.get("is_arc", False))


def get_active_arcs(state: dict) -> list:
    """state.storylines 중 is_arc=True AND status=active만."""
    return [
        s for s in state.get("storylines", [])
        if isinstance(s, dict) and s.get("is_arc") and s.get("status") == "active"
    ]


# [2026-07-15 폐기] get_focus_arc / current_focus_arc_id — 제거.
#
# 불가능한 함수였다: fid는 state["current_focus_arc_id"](spec §1.1 = storyteller_state
# = world_state["storyteller"])에서, 순회는 state["storylines"](narrative_tracker_state
# = ai_memory["narrative_tracker"])에서 읽었다. 두 키가 같은 dict에 있는 상태는 없다
# → 어느 쪽을 넘겨도 무조건 None. 스펙 §1.1이 포인터를 대상과 다른 저장소에 배치했고,
# 게터는 합쳐진 상태를 가정해 쓰였다. 세터는 애초에 명세되지 않아 존재한 적 없다.
#
# 개념 자체도 흡수·폐기됨:
#   - 계획서(arc_director_integration_plan §2.2/2.6)의 자동 세팅 = "pc_engagement 최고"
#     → spec v2에서 pc_engagement가 proximity로 개명되며 slot_manager의
#       proximity 임계 필터 + 내림차순 정렬로 흡수됨. focus가 하려던 일을 이미 함.
#   - 남은 갈래 `!아크 포커스` 수동 지정 → 조작면 최소주의(새 명령어 기본 0)
#   - 남은 갈래 자동 전환 Discord 알림 → GM 디렉팅 알림 금지(anti-railroad)
#
# 되살릴 일이 생기면 스키마 필드는 그대로 두었으니 세터부터 명세할 것.


def promote_to_arc(
    sl: dict,
    *,
    current_turn: int,
    declared_goal: str,
    initial_phase_label: str,
    origin_category: str,
    next_waypoint: str = "",
    backstage_reality: str = "",
) -> None:
    """
    Storyline → Arc 격상 (in-place). spec v2 §4.1.

    - 기존 storyline carrier 재활용 (새 자료구조 X)
    - is_arc=True 토글
    - 좌표 5축 단순 기본값 초기화
    - phases는 initial_phase_label 1개로 시작
    - entities/turns/current_context/summaries는 격상 시점 동결 (이후 갱신 X)
    - last_turn = current_turn 동결
    """
    sl["is_arc"] = True

    # 좌표 5축 — 단순 기본값 (운영 검증 후 PMU 추론으로 정교화 가능)
    sl["phase_pos"] = 0.05
    sl["proximity"] = 0.5
    sl["pacing"] = 0.3
    sl["momentum"] = 0.3
    sl["weight"] = 0.3

    # 의미 라벨
    sl["declared_goal"] = declared_goal
    sl["origin_category"] = origin_category
    sl["phases"] = [initial_phase_label] if initial_phase_label else []
    sl["next_waypoint"] = next_waypoint
    sl["backstage_reality"] = backstage_reality

    # 누적 흔적 (초기 빈)
    sl["sensory_foreshadowing"] = []  # List[dict] — {summary, polarity, intensity, turn}
    sl["offscreen_actions"] = []
    sl["trajectory"] = []  # List[Tuple[turn, phase_pos, weight]]

    # 운영 추적
    sl["promoted_at_turn"] = current_turn
    sl["last_advanced_turn"] = current_turn
    sl["armed"] = False

    # 동결
    sl["last_turn"] = current_turn  # 격상 시점 동결 (이후 갱신 X)


def demote_to_storyline(sl: dict) -> None:
    """
    Arc → Storyline 격하 (in-place). spec v2 §4.5.

    - is_arc=False 토글
    - 좌표/라벨/누적흔적/운영추적 모두 제거
    - entities/turns 동결 풀고 일반 storyline 자동 분류 재개
    - 재진입 가능 (다음 promote 임계 도달 시 다시 arc로, 깔끔한 재시작)
    """
    sl["is_arc"] = False

    # 신규 필드 모두 제거
    for key in (
        "phase_pos", "proximity", "pacing", "momentum", "weight",
        "declared_goal", "origin_category", "phases",
        "next_waypoint", "backstage_reality",
        "sensory_foreshadowing", "offscreen_actions", "trajectory",
        "promoted_at_turn", "last_advanced_turn", "armed",
    ):
        sl.pop(key, None)


def supernova_branch(arc: dict, current_turn: int) -> str:
    """
    spec v2 §4.5 Supernova 분기 (armed + trigger 닿음). [2026-07-06 배선]
    상수(FORCED 0.7/VANISH 0.3)·armed 토글·absorb 흐름·demote_to_storyline은
    전부 기구현이었고, 트리거→score→분기 커넥터만 없었음 (감사 A2).

    호출처: ① anomaly_module ABSORB — 이미 armed인 arc에 같은 카테고리 시드 흡수 성공 시
           ② tick_arcs — armed arc + PC 결정적 행동(ctx["decisive"]) 신호

    Returns: "forced_climax" | "vanish" | "demote"
    """
    import config as _cfg
    proximity = float(arc.get("proximity", 0.0) or 0.0)
    momentum = float(arc.get("momentum", 0.0) or 0.0)
    phase_pos = float(arc.get("phase_pos", 0.0) or 0.0)
    pacing = float(arc.get("pacing", 0.0) or 0.0)
    score = (proximity * 0.4 + momentum * 0.3 + phase_pos * 0.2
             + (1.0 if pacing >= 0.7 else 0.0) * 0.1)

    if score >= _cfg.ARC_SUPERNOVA_FORCED_THRESHOLD:
        # forced_climax: proximity 1.0 점프 → 기존 채널(arc digest S11)로 산문 ignite.
        # 시스템 메시지 알림 없음 (anti-railroad — GM 디렉팅 알림 금지).
        arc["proximity"] = 1.0
        if phase_pos > 0.9:
            arc["status"] = "resolved"
        arc["armed"] = False
        _label = arc.get("declared_goal") or arc.get("summary") or str(arc.get("id", ""))
        arc.setdefault("phases", []).append(f"[강제 발사: {_label}]")
        arc["last_advanced_turn"] = int(current_turn)
        branch = "forced_climax"
    elif score <= _cfg.ARC_SUPERNOVA_VANISH_THRESHOLD:
        # vanish silent: 산문 ignite X (안티체호프). 좌표/라벨 보존 — 재활성 가능.
        arc["status"] = "dormant"
        arc["armed"] = False
        branch = "vanish"
    else:
        # 중간 score — 격하. 같은 카테고리 시드 재누적 시 재진입 가능.
        demote_to_storyline(arc)
        branch = "demote"

    logger.info("[Arc] SUPERNOVA %s arc#%s score=%.2f (prox=%.2f mom=%.2f pos=%.2f pacing=%.2f)",
                branch, arc.get("id"), score, proximity, momentum, phase_pos, pacing)
    return branch


# =========================================================
# ARC SYSTEM (Phase 2: 라우터 helper)
# =========================================================
# spec v2 §4.0 라우터 우선순위 + §4.2 흡수 메커니즘.
# anomaly_module._route_candidate가 이 helper들을 호출.

def find_absorbing_arc(state: dict, category: str) -> dict | None:
    """
    같은 origin_category active arc 찾기. weight × proximity 큰 쪽 우선.
    동률 시 promoted_at_turn 빠른 쪽 (오래된 arc 우선).

    spec v2 §4.2 — 같은 카테고리 active arc 다수 시 우선순위.
    """
    if not category:
        return None
    candidates = []
    for sl in state.get("storylines", []):
        if not isinstance(sl, dict):
            continue
        if not sl.get("is_arc"):
            continue
        if sl.get("status") != "active":
            continue
        if sl.get("origin_category") != category:
            continue
        candidates.append(sl)

    if not candidates:
        return None

    candidates.sort(key=lambda a: (
        -(a.get("weight", 0.0) * a.get("proximity", 0.0)),
        a.get("promoted_at_turn", 0),
    ))
    return candidates[0]


def _reader_hangul_bigrams(text: str) -> set:
    """[Reader-GM R4b] 한글 bigram — 독자 인용↔후보 line/reason 겹침 매칭(순수)."""
    import re as _re
    s = _re.sub(r"[^가-힣]", "", str(text))
    return {s[i:i + 2] for i in range(len(s) - 1)}


def check_promote_threshold(
    state: dict,
    candidate: dict,
    recent_categories: list,
    event_queue: list,
    reader_axes: list = None,
) -> bool:
    """
    spec v2 §4.0/§4.2: arc_promote 임계 도달 검사.

    조건:
      - 같은 카테고리 active arc 없음 (있으면 흡수로 가야)
      - 같은 카테고리 누적 (recent_categories + event_queue + candidate) ≥ ARC_PROMOTE_CATEGORY_MIN (3)
      - 누적 항목 중 최소 1개 intensity High/Extreme
    [Reader-GM R4b] reader_axes(독자 지속 축 인용)가 후보 line/reason과 겹치면 누적 증거 1표 가산 —
      독자의 지속 수신도 "이 축이 살아있다"는 관측이므로. [2026-08-11 리더 §7] 현행 FEED=1이라
      호출부가 실제 축을 넘긴다(구 "FEED=0이면 None" 기술 정정). 단 강도 조건(High+ 1개)은 우회 불가.
    """
    import config as _cfg
    cat = candidate.get("category", "")
    if not cat:
        return False

    # 같은 카테고리 active arc 있으면 promote X (흡수 분기로)
    if find_absorbing_arc(state, cat) is not None:
        return False

    # 같은 카테고리 누적 카운트
    recent_count = sum(1 for c in (recent_categories or []) if c == cat)
    queue_count = sum(
        1 for e in (event_queue or [])
        if isinstance(e, dict) and e.get("category") == cat
    )
    total = recent_count + queue_count + 1  # +1 for candidate
    if reader_axes:
        _c_bg = _reader_hangul_bigrams(
            f"{candidate.get('line', '')} {candidate.get('reason', '')}")
        if _c_bg and any(
            len(_reader_hangul_bigrams(a) & _c_bg) >= 3 for a in reader_axes
        ):
            total += 1  # 독자 1표
    if total < _cfg.ARC_PROMOTE_CATEGORY_MIN:
        return False

    # High/Extreme 1+ 검사
    high_set = ("High", "Extreme")
    cand_high = candidate.get("intensity") in high_set
    queue_high = any(
        isinstance(e, dict) and e.get("category") == cat and e.get("intensity") in high_set
        for e in (event_queue or [])
    )
    return cand_high or queue_high


def similar_seed_present(arc: dict, seed: dict) -> bool:
    """
    spec v2 §4.2 거부 게이트: 같은 polarity + 같은 intensity 시드가
    sensory_foreshadowing 또는 offscreen_actions에 이미 있나?

    완전 거부 — 약한 효과 폭주 위험 회피.
    """
    s_pol = seed.get("polarity")
    s_int = seed.get("intensity")
    pool = (arc.get("sensory_foreshadowing", []) or []) + (arc.get("offscreen_actions", []) or [])
    for stored in pool:
        if not isinstance(stored, dict):
            continue
        if stored.get("polarity") == s_pol and stored.get("intensity") == s_int:
            return True
    return False


def compute_absorption(arc: dict, foreground: bool) -> float:
    """
    spec v2 §3 compute_absorption 잠정값 1차.
      foreground (proximity ≥ 0.3): +0.04
      background:                    +0.015
      phase_pos > 0.7 후반:          ×0.7 (흡수 약화)
    """
    base = 0.04 if foreground else 0.015
    if arc.get("phase_pos", 0.0) > 0.7:
        base *= 0.7
    return base


def absorb_to_arc(arc: dict, seed: dict, current_turn: int) -> bool:
    """
    spec v2 §4.2 흡수 (in-place). 성공 시 True, 거부 시 False.

    - 거부 게이트 (similar_seed_present) 통과 시 흡수
    - weight 증가 (compute_absorption)
    - foreground/background 분기로 sensory/offscreen 누적
    - cap ring buffer 적용
    - armed 자동 토글 (weight ≥ 0.95)
    - last_advanced_turn 갱신 X (phase_transition만 갱신)
    - 좌표 직접 점프 X
    """
    import config as _cfg

    # 거부 게이트
    if similar_seed_present(arc, seed):
        return False

    foreground = arc.get("proximity", 0.0) >= _cfg.ARC_PROXIMITY_EXPOSURE_THRESHOLD
    delta = compute_absorption(arc, foreground)
    arc["weight"] = min(1.0, arc.get("weight", 0.0) + delta)

    entry = {
        "summary": seed.get("summary") or seed.get("line") or seed.get("tag") or "",
        "polarity": seed.get("polarity", "mixed"),
        "intensity": seed.get("intensity", "Mid"),
        "turn": int(current_turn),
    }

    if foreground:
        arr = arc.setdefault("sensory_foreshadowing", [])
        arr.append(entry)
        cap = _cfg.ARC_FORESHADOWING_CAP
        if len(arr) > cap:
            arc["sensory_foreshadowing"] = arr[-cap:]
    else:
        arr = arc.setdefault("offscreen_actions", [])
        arr.append(entry)
        cap = _cfg.ARC_OFFSCREEN_ACTIONS_CAP
        if len(arr) > cap:
            arc["offscreen_actions"] = arr[-cap:]

    # armed 자동 토글
    if arc["weight"] >= _cfg.ARC_SUPERNOVA_ARMED_THRESHOLD:
        arc["armed"] = True

    return True


# =========================================================
# ARC SYSTEM (Phase 3: 좌표 변환 룰 + tick_arcs)
# =========================================================
# spec v2 §3 산출 함수 + §4.3 자연 감쇠 + §4.4 armed + §4.6 dormant.
# 모든 좌표 갱신은 매 턴 tick_arcs() 호출에서.
# 호출 위치: orchestration이 매 턴 narrative_tracker 호출하는 자리에 1줄 추가
# (Phase 5 통합 단계에서 배선).

def compute_decay_rate(
    arc: dict,
    turn_since_advanced: int,
    expected_volume_length: int = None,
) -> float:
    """
    weight 자연 감쇠율 — 선형 감쇠. spec v2 §3.

    weight=0.95 가정 시 expected_volume_length(150)턴에 0 도달.
    선형 (0.95 / 150 ≈ 0.0063/turn). 시작 weight 무관 절대값.
    """
    import config as _cfg
    if expected_volume_length is None:
        expected_volume_length = _cfg.ARC_EXPECTED_VOLUME_LENGTH
    return 0.95 / max(1, expected_volume_length)


def compute_dormant_threshold(
    arc: dict,
    pacing: float,
    phase_pos: float,
) -> int:
    """
    dormant 판정 turn 수. pacing/phase_pos 따라 다름. spec v2 §3.

    잠정값:
      mundane (pacing<0.3): 50턴 — 평범 씬은 늦게 dormant
      crucial (pacing>0.7): 15턴 — 정교한 빌드업은 빨리 결판
      baseline:             30턴 — ARC_DORMANT_BASE_TURNS
    """
    import config as _cfg
    if pacing < 0.3:
        return 50
    elif pacing > 0.7:
        return 15
    else:
        return _cfg.ARC_DORMANT_BASE_TURNS


def compute_proximity_update(arc: dict, ctx: dict) -> float:
    """
    proximity 갱신. spec v2 §3 / 블루프린트 §2.3.

    새 측정값 = location 매칭(0.4) + npc 매칭(0.3) + category 매칭(0.3)
    return max(arc.proximity - 자연감쇠, 새 측정값)
    """
    score = 0.0

    # location 매칭 (PC current_location vs arc.entities)
    pc_loc = (ctx.get("current_location") or "").strip()
    entities = arc.get("entities") or []
    if pc_loc and any(pc_loc == e or (pc_loc and e and (pc_loc in e or e in pc_loc)) for e in entities):
        score += 0.4

    # npc 매칭 (relevant_npcs vs arc.entities)
    relevant = ctx.get("relevant_npcs") or []
    # relevant_npcs가 dict 리스트일 수 있음 (lorekeeper 패턴)
    npc_names = [n if isinstance(n, str) else n.get("name", "") for n in relevant]
    npc_names = [n for n in npc_names if n]
    entity_set = set(entities)
    matched_npcs = sum(1 for n in npc_names if n in entity_set)
    if matched_npcs > 0:
        score += min(0.3, matched_npcs * 0.15)

    # category 매칭 (이번 턴 anomaly_category vs arc.origin_category)
    anomaly_cat = (ctx.get("anomaly_category") or "").strip()
    if anomaly_cat and anomaly_cat == arc.get("origin_category", ""):
        score += 0.3

    # 자연 감쇠 (PC 안 닿으면 약해짐)
    decay = 0.05
    decayed_old = max(0.0, arc.get("proximity", 0.0) - decay)

    # max(과거 감쇠, 새 측정값)
    return min(1.0, max(decayed_old, score))


def compute_momentum_update(
    arc: dict,
    quality_flags: dict,
    decisive_action: bool,
) -> float:
    """
    momentum 갱신. baseline + spike + 감쇠. spec v2 §3 / 블루프린트 §2.3.

    잠정값:
      baseline:               0.2
      decisive 시 spike:      +0.3
      quality_flags signal:   +0.2
      자연 감쇠:              -0.05/turn
    """
    baseline = 0.2
    spike = 0.0

    if decisive_action:
        spike += 0.3

    if quality_flags and isinstance(quality_flags, dict):
        if quality_flags.get("decisive_signal") or quality_flags.get("crisis_warning"):
            spike += 0.2

    decay = 0.05
    decayed = max(0.0, arc.get("momentum", 0.0) - decay)

    return min(1.0, max(decayed, baseline) + spike)


def compute_pacing_mode(
    arc: dict,
    scene_type: str,
    doom_phase: str,
    decisive: bool,
) -> float:
    """
    pacing 모드 (mundane 0 ↔ crucial 1). spec v2 §3 / 블루프린트 §2.3.

    crucial 트리거:
      proximity ≥ 0.7 / decisive / 클라이맥스 페이즈(轉/結) / 긴장 씬(combat/tension/intimate)

    부드러운 전환 (lerp 30%) — 갑작스러운 모드 전환 회피.
    """
    target = 0.3  # mundane 기본

    if arc.get("proximity", 0.0) >= 0.7:
        target = 0.9
    elif decisive:
        target = 0.8
    elif doom_phase in ("轉", "結"):
        target = 0.7
    elif scene_type in ("combat", "tension", "intimate"):
        target = 0.6
    elif scene_type in ("normal", "exploration", "social"):
        target = 0.25

    current = arc.get("pacing", 0.3)
    return current + (target - current) * 0.3  # 30% lerp


def compute_phase_drift(arc: dict) -> float:
    """
    phase_pos 자체 표류 (PC 무관심 시). spec v2 §3 / 블루프린트 §2.3.

    proximity 낮으면 momentum × offscreen_rate 만큼 자체 진행.
    proximity 높으면 PMU의 phase_transition이 별도 진행 (이 함수는 0 반환).
    """
    import config as _cfg
    if arc.get("proximity", 0.0) >= _cfg.ARC_PROXIMITY_EXPOSURE_THRESHOLD:
        return 0.0  # PC 관심 시 자체 표류 X (PMU만 진행)

    offscreen_rate = 0.005  # 매 턴 momentum × rate
    return arc.get("momentum", 0.0) * offscreen_rate


def apply_arc_updates(state: dict, arc_updates: list, current_turn: int) -> dict:
    """
    PMU 응답의 arc_updates를 storyline에 적용. spec v2 §5.1.

    Args:
        state: storyteller_state
        arc_updates: PMU 출력 [{arc_id, phase_transition, next_waypoint_update, ...}, ...]
        current_turn: int

    Returns:
        events: {"phase_transitions": [arc_id, ...], "applied": [arc_id, ...]}
    """
    import config as _cfg
    events = {"phase_transitions": [], "applied": []}
    if not arc_updates or not isinstance(arc_updates, list):
        return events

    storylines = state.get("storylines", [])

    for upd in arc_updates:
        if not isinstance(upd, dict):
            continue
        arc_id = upd.get("arc_id")
        if arc_id is None:
            continue
        # find arc
        target = None
        for sl in storylines:
            if isinstance(sl, dict) and sl.get("id") == arc_id and sl.get("is_arc"):
                target = sl
                break
        if not target:
            continue

        applied = False

        # phase_transition
        pt = upd.get("phase_transition")
        if isinstance(pt, dict) and pt.get("enter") and pt.get("label"):
            phases = target.setdefault("phases", [])
            phases.append(str(pt["label"]))
            if len(phases) > _cfg.ARC_PHASES_CAP:
                target["phases"] = phases[-_cfg.ARC_PHASES_CAP:]
            target["phase_pos"] = min(1.0, target.get("phase_pos", 0.0) + 0.1)
            target["last_advanced_turn"] = current_turn
            events["phase_transitions"].append(arc_id)
            applied = True

        # next_waypoint
        nw = upd.get("next_waypoint_update")
        if nw and isinstance(nw, str):
            target["next_waypoint"] = nw
            applied = True

        # backstage_reality
        br = upd.get("backstage_reality_update")
        if br and isinstance(br, str):
            target["backstage_reality"] = br
            applied = True

        # sensory_foreshadowing_add
        for entry in (upd.get("sensory_foreshadowing_add") or []):
            if not isinstance(entry, dict):
                continue
            if not entry.get("summary"):
                continue
            target.setdefault("sensory_foreshadowing", []).append({
                "summary": str(entry.get("summary", ""))[:200],
                "polarity": entry.get("polarity", "mixed"),
                "intensity": entry.get("intensity", "Mid"),
                "turn": current_turn,
            })
            cap = _cfg.ARC_FORESHADOWING_CAP
            if len(target["sensory_foreshadowing"]) > cap:
                target["sensory_foreshadowing"] = target["sensory_foreshadowing"][-cap:]
            applied = True

        # offscreen_actions_add
        for entry in (upd.get("offscreen_actions_add") or []):
            if not isinstance(entry, dict):
                continue
            if not entry.get("summary"):
                continue
            target.setdefault("offscreen_actions", []).append({
                "summary": str(entry.get("summary", ""))[:200],
                "polarity": entry.get("polarity", "mixed"),
                "intensity": entry.get("intensity", "Mid"),
                "turn": current_turn,
            })
            cap = _cfg.ARC_OFFSCREEN_ACTIONS_CAP
            if len(target["offscreen_actions"]) > cap:
                target["offscreen_actions"] = target["offscreen_actions"][-cap:]
            applied = True

        if applied:
            events["applied"].append(arc_id)

    return events


def apply_arc_decisions(state: dict, arc_decisions: dict, current_turn: int) -> dict:
    """
    PMU 응답의 arc_decisions(confirms/rejects)를 처리. spec v2 §5.1 / §4.1.

    confirms[i] = {candidate_category, declared_goal, initial_phase_label, origin_summary}
    → 같은 카테고리 active storyline (is_arc=False) 중 가장 최근 last_turn 것을 promote_to_arc.

    rejects[i] = {candidate_category, reason}
    → 무시 (log만).

    Returns:
        events: {"promoted": [arc_id, ...], "rejected": [category, ...]}
    """
    events = {"promoted": [], "rejected": []}
    if not isinstance(arc_decisions, dict):
        return events

    storylines = state.get("storylines", [])

    # confirms
    for confirm in (arc_decisions.get("confirms") or []):
        if not isinstance(confirm, dict):
            continue
        category = confirm.get("candidate_category", "")
        if not category:
            continue
        # 같은 카테고리 active storyline 찾기 (is_arc=False만, 이미 arc면 흡수가 처리)
        non_arc_candidates = [
            s for s in storylines
            if isinstance(s, dict) and not s.get("is_arc") and s.get("status") == "active"
        ]
        if not non_arc_candidates:
            continue
        # 가장 최근 last_turn storyline 선택 (해당 카테고리 시드 누적 가능성 높음)
        target_sl = max(non_arc_candidates, key=lambda s: s.get("last_turn", 0))
        try:
            promote_to_arc(
                target_sl,
                current_turn=current_turn,
                declared_goal=confirm.get("declared_goal", "(미정)"),
                initial_phase_label=confirm.get("initial_phase_label", "(initial)"),
                origin_category=category,
                next_waypoint=confirm.get("next_waypoint", ""),
                backstage_reality=confirm.get("backstage_reality", ""),
            )
            events["promoted"].append(target_sl.get("id"))
        except Exception:
            pass

    # rejects (log만)
    for reject in (arc_decisions.get("rejects") or []):
        if not isinstance(reject, dict):
            continue
        cat = reject.get("candidate_category", "")
        if cat:
            events["rejected"].append(cat)

    return events


def tick_arcs(state: dict, ctx: dict, current_turn: int) -> dict:
    """
    매 턴 모든 active arc의 좌표 갱신 + dormant 판정. spec v2 §4.3/§4.4/§4.6.

    Args:
        state: storyteller_state
        ctx: 갱신 컨텍스트 dict {
            "current_location": str,
            "relevant_npcs": List[str|dict],
            "scene_type": str,
            "anomaly_category": str,         # 이번 턴 발사된 anomaly category (있으면)
            "doom_phase": str,                # 起承轉結間
            "quality_flags": dict,            # DAI quality_flags
            "decisive": bool,                 # PC 결정적 행동 신호
        }
        current_turn: int

    Returns:
        events: {
            "ticked": [arc_id, ...],         # 이번 턴 갱신된 arc id
            "dormant": [arc_id, ...],        # 이번 턴 dormant 처리된 arc id
            "armed": [arc_id, ...],          # 이번 턴 armed 진입한 arc id
        }
    """
    import config as _cfg

    events = {"ticked": [], "dormant": [], "armed": []}

    for sl in state.get("storylines", []):
        if not isinstance(sl, dict):
            continue
        if not sl.get("is_arc"):
            continue
        if sl.get("status") != "active":
            continue

        arc_id = sl.get("id")
        events["ticked"].append(arc_id)
        prev_armed = bool(sl.get("armed", False))

        # 1. proximity 갱신
        sl["proximity"] = compute_proximity_update(sl, ctx)

        # 2. pacing 모드 (proximity 갱신 후 평가)
        sl["pacing"] = compute_pacing_mode(
            sl,
            ctx.get("scene_type", "normal"),
            ctx.get("doom_phase", ""),
            bool(ctx.get("decisive", False)),
        )

        # 3. momentum 갱신
        sl["momentum"] = compute_momentum_update(
            sl,
            ctx.get("quality_flags", {}) or {},
            bool(ctx.get("decisive", False)),
        )

        # 4. weight 자연 감쇠 (선형)
        turn_since_advanced = max(0, current_turn - sl.get("last_advanced_turn", current_turn))
        decay = compute_decay_rate(sl, turn_since_advanced)
        sl["weight"] = max(0.0, sl.get("weight", 0.0) - decay)

        # 5. phase_pos 표류 (PC 무관심 시 momentum × offscreen_rate)
        drift = compute_phase_drift(sl)
        if drift > 0:
            sl["phase_pos"] = min(1.0, sl.get("phase_pos", 0.0) + drift)

        # 6. armed 자동 토글 (hysteresis 0.05)
        if sl.get("weight", 0.0) >= _cfg.ARC_SUPERNOVA_ARMED_THRESHOLD:
            if not prev_armed:
                events["armed"].append(arc_id)
            sl["armed"] = True
        elif sl.get("weight", 0.0) < (_cfg.ARC_SUPERNOVA_ARMED_THRESHOLD - 0.05):
            sl["armed"] = False

        # 6b. Supernova 트리거(b): armed + PC 결정적 행동 (spec §4.5 — 2026-07-06 배선).
        #     이번 턴 armed 진입분은 제외 (spec: "자동 발사 X. 다음 trigger 대기").
        if sl.get("armed") and prev_armed and ctx.get("decisive"):
            _branch = supernova_branch(sl, current_turn)
            events.setdefault("supernova", []).append((arc_id, _branch))
            if _branch != "forced_climax":
                continue  # vanish/demote — 이후 좌표 스텝(7/8) 진행 안 함

        # 7. trajectory 갱신 (ring buffer cap 20)
        traj = sl.setdefault("trajectory", [])
        traj.append((current_turn, sl["phase_pos"], sl["weight"]))
        if len(traj) > _cfg.ARC_TRAJECTORY_CAP:
            sl["trajectory"] = traj[-_cfg.ARC_TRAJECTORY_CAP:]

        # 8. dormant 판정 (proximity 게이트 + 무진행 임계)
        threshold = compute_dormant_threshold(
            sl, sl.get("pacing", 0.3), sl.get("phase_pos", 0.0)
        )
        if (sl.get("proximity", 0.0) < _cfg.ARC_PROXIMITY_EXPOSURE_THRESHOLD and
                turn_since_advanced >= threshold):
            sl["status"] = "dormant"
            events["dormant"].append(arc_id)

    return events


# =========================================================
# Aspects 부활 — 시스템 교차 결합 신호
# =========================================================
# 8개 라벨 결합 조건 평가. Arc 사이클 시 백업, V3 재이식.
# 라벨 내부 식별자 (산문 노출 X), typological 디렉티브는 config.ASPECTS_DIRECTIVES.

def _any_active_arc_proximate(state: dict, threshold: float = None) -> bool:
    """active arc 중 proximity ≥ threshold인 게 있나? (외부 사건 인식 자리)."""
    import config as _cfg
    if threshold is None:
        threshold = _cfg.ASPECTS_ARC_PROXIMITY_THRESHOLD
    for sl in state.get("storylines", []):
        if not isinstance(sl, dict):
            continue
        if not sl.get("is_arc") or sl.get("status") != "active":
            continue
        if float(sl.get("proximity", 0.0) or 0.0) >= threshold:
            return True
    return False


def _any_arc_armed(state: dict) -> bool:
    """active arc 중 armed=True인 게 있나? (큰 흐름 임박)."""
    for sl in state.get("storylines", []):
        if not isinstance(sl, dict):
            continue
        if sl.get("is_arc") and sl.get("status") == "active" and sl.get("armed"):
            return True
    return False


def compute_aspects(bus, state: dict, primary_axis: str = "vigor") -> list:
    """
    시스템 교차 결합 평가. 활성 라벨 리스트 반환.

    Args:
        bus: SharedBus (judgment / anomaly / vigor / composure / doom 읽기)
        state: storyteller_state (Arc proximity / armed 평가용)
        primary_axis: "vigor" or "composure"

    Returns:
        active_aspects: List[str] — 활성 라벨 식별자 (산문 노출 X, 내부 추적용)
    """
    aspects = []

    # bus 안전 추출
    judgment = bus.judgment if isinstance(bus.judgment, dict) else {}
    anomaly = bus.anomaly if isinstance(bus.anomaly, dict) else {}
    vigor = bus.vigor if isinstance(bus.vigor, dict) else {}
    composure = bus.composure if isinstance(bus.composure, dict) else {}
    doom = bus.doom if isinstance(bus.doom, dict) else {}

    j_active = bool(judgment.get("active"))
    j_result = str(judgment.get("result", "") or "")
    a_triggered = bool(anomaly.get("triggered"))
    a_escalated = bool(anomaly.get("escalated"))
    a_intensity = str(anomaly.get("intensity", "") or "")
    a_arc_absorbed = isinstance(anomaly.get("arc_absorbed"), dict)
    clock_fired = bool(doom.get("completed_this_turn"))

    v_val = int(vigor.get("value", 100) or 100)
    c_val = int(composure.get("value", 100) or 100)

    primary_val = v_val if primary_axis == "vigor" else c_val

    arc_proximate = _any_active_arc_proximate(state)
    arc_armed = _any_arc_armed(state)

    # 외부 사건 통합 신호 (anomaly OR arc 흡수 OR 시계 발사)
    external_event = a_triggered or a_arc_absorbed or clock_fired

    # === 1. Failure Resonance ===
    if j_active and j_result in ("failure", "critical_failure") and external_event:
        aspects.append("Failure Resonance")

    # === 2. Glory's Shadow ===
    if j_active and j_result == "critical_success" and external_event:
        aspects.append("Glory's Shadow")

    # === 3. Body Erosion ===
    # primary_axis == vigor + 외부 인식 + vigor 한계 가까움
    if primary_axis == "vigor" and (a_triggered or arc_proximate) and v_val <= _aspects_resource_threshold():
        aspects.append("Body Erosion")

    # === 4. Mind Fracture ===
    if primary_axis == "composure" and (a_triggered or arc_proximate) and c_val <= _aspects_resource_threshold():
        aspects.append("Mind Fracture")

    # === 5/6. Inner-Outer Convergence · Resurgence 제거 (2026-07-06) ===
    # 둘 다 trauma_trigger(트라우마 각성) 게이트였음 — 각성 폐지로 함께 폐지.
    # (부수 정리: 여기서 읽던 anomaly["arc_forced_climax"]는 생산자가 없던 유령 키.
    #  supernova FORCED/VANISH 분기는 spec상 미구현 — 부활 시 신호 생산부터.)

    # === 7. Abyss ===
    if j_result == "critical_failure" and primary_val <= _aspects_abyss_threshold():
        aspects.append("Abyss")

    # === 8. Loss of Control ===
    if a_intensity == "Extreme" or (a_escalated and arc_armed):
        aspects.append("Loss of Control")

    return aspects


def _aspects_resource_threshold() -> int:
    import config as _cfg
    return _cfg.ASPECTS_RESOURCE_THRESHOLD


def _aspects_abyss_threshold() -> int:
    import config as _cfg
    return _cfg.ASPECTS_ABYSS_THRESHOLD
