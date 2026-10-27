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
    extra_entities: Optional[List[str]] = None,
) -> dict:
    """턴 로그에 새 턴 기록. quality_flags에서 중요도 추론.

    [2026-09-14 W5] `extra_entities`(장소·세력 이름)는 **별도 키** `extra`로 간다.
    `entities`에 섞으면 `_infer_importance`의 `len(entities) >= 3`이 흔들리고
    `entities[:8]` 상한이 인물을 밀어낸다 — 둘 다 W5의 관심사가 아니다(지시서 §0 ③).
    값이 없으면 키 자체를 안 만든다(기존 엔트리 모양 불변)."""
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
    if extra_entities:
        try:
            import config as _cfg_nt
            _xmax = int(getattr(_cfg_nt, "WIKI_TURNLOG_EXTRA_MAX", 4))
        except Exception:
            _xmax = 4
        _x = [str(n).strip() for n in extra_entities if str(n or "").strip()]
        if _x:
            entry["extra"] = _x[:max(0, _xmax)]
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
            for k in ("location", "mood", "health", "notable", "said")
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
        # [2026-10-10 M1″] 한 말 — 속마음 사실 줄(turn_mail `_after_it_lines`)만 읽는다. Slot 7·DEEP 은 안 읽는다.
        if isinstance(changes.get("said"), str) and changes["said"].strip():
            entry["said"] = changes["said"].strip()[:200]

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
            "current_context": (turn_entry.get("ai_brief") or "")[:260],
            "context_kind": "raw",  # [2026-08-12 출력파생 §8] 산문 원문 출처 — 렌더 미주입
            "key_points": [],
            "ongoing_tensions": [],
            "summaries": [],
            "status": "active",
            # [2026-10-24 C75 G2] 아크는 줄거리가 아니라 우물(well_store)에 산다 — 옛 `is_arc` 칸 안 씀.
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


def summary_due(state: dict, current_turn: int) -> bool:
    """[2026-10-24 C74 W4-1b] summarize_if_needed 가 일을 할 턴인가(콜 없이 같은 문턱). 꼬리 태스크가 사본을 뜨기 전에 본다."""
    if current_turn - (state.get("last_summary_turn", 0) or 0) < SUMMARY_INTERVAL:
        return False
    return any(isinstance(s, dict) and s.get("status") == "active" for s in state.get("storylines", []) or [])


_SUMMARY_KEYS = ("summaries", "current_context", "context_kind")


def merge_summary_fields(fresh: dict, work: dict, base: dict) -> dict:
    """[2026-10-24 C74 W1-7] 요약 콜은 사본(work = base 의 deepcopy) 위에서 돌고, 돌아온 뒤 **다시 읽은** fresh 에
    요약이 바꾼 칸만 얹는다 — 콜을 기다리는 동안 다른 자리(다음 턴 이변 흡수 등)가 저장한 줄거리 상태를 지우지 않게.
    줄거리별(id) summaries · current_context · context_kind 는 바뀐 것만 옮기고, ongoing_tensions 는 요약이 새로 붙인
    것만 덧붙인다(상한 5). last_summary_turn 은 큰 쪽. fresh 를 그 자리에서 고쳐 돌려준다."""
    import copy as _copy
    base_by = {s.get("id"): s for s in (base.get("storylines") or []) if isinstance(s, dict)}
    work_by = {s.get("id"): s for s in (work.get("storylines") or []) if isinstance(s, dict)}
    for sl in fresh.get("storylines") or []:
        if not isinstance(sl, dict):
            continue
        w = work_by.get(sl.get("id"))
        if w is None:
            continue
        b = base_by.get(sl.get("id")) or {}
        for k in _SUMMARY_KEYS:
            if k in w and w.get(k) != b.get(k):
                sl[k] = _copy.deepcopy(w.get(k))
        bt = b.get("ongoing_tensions") or []
        added = [x for x in (w.get("ongoing_tensions") or []) if x not in bt]
        if added:
            sl["ongoing_tensions"] = ((sl.get("ongoing_tensions") or []) + added)[-5:]
    fresh["last_summary_turn"] = max(int(fresh.get("last_summary_turn", 0) or 0),
                                     int(work.get("last_summary_turn", 0) or 0))
    return fresh


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
            f"T{t['turn']}: {_sanitize((t.get('user_brief') or '')[:60])} → {_sanitize((t.get('ai_brief') or '')[:80])}"
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
        # [2026-10-11 C27] 600 → 8192. 추론 켠 콜은 이 엔드포인트에서 추론이 출력 예산에 같이 들어간다 —
        #   녹화 107판 중 파싱 실패 30·빈칸 6(추론 1.3k자 뒤 한국어 JSON이 중간에 끊김). 배경 콜이라 상한을 넉넉히.
        #   근거 analysis_line/C21_분석샘플링_측정·스펙_2026-10-11 §4
        max_output_tokens=8192,
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
        # [2026-09-24 감사] null/문자열 priority 방어 — 이 함수는 NarrativeTracker 블록 try 안이라
        #   TypeError 하나가 그 턴의 tracker 저장 전체를 날린다.
        try:
            priority = max(0.0, min(1.0, float(raw.get("priority") if raw.get("priority") is not None else 0.5)))
        except (TypeError, ValueError):
            priority = 0.5

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
            context = (sl.get("current_context") or "")[:180]
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
            # [2026-10-06 어휘 V2] "Tension:" → "Open:" — 한 렌더에 tension이 넷(장면 긴장 축·Energy·챕터 게이지·여기)이었다.
            #   여기 담기는 건 줄기의 열린 질문·약속([Payoff]·[Lock]) — 정적 "Open threads are pressure"와 같은 말.
            line += f" | Open: {'; '.join(_fmt_tension(t) for t in top)}"
        parts.append(line)

    return "\n".join(parts)


_QUOTE_PAIRS = (('"', '"'), ("“", "”"), ("'", "'"), ("‘", "’"), ("「", "」"), ("『", "』"))


def strip_quoted(s: str) -> str:
    """[2026-10-10 M1″] 따옴표 안 대사를 '…'로 — Slot 7 Notable·발효 DEEP 으로 내보낼 때의 안전망.
    notable 은 이제 한 일만 받지만(말은 said 칸) 모델이 대사를 섞어 오면 프로필 칸이 지난 대사를 되먹인다.
    저장은 원문 그대로, 걷는 건 내보낼 때만."""
    import re as _re
    if not isinstance(s, str) or not s:
        return s or ""
    for a, b in _QUOTE_PAIRS:
        s = _re.sub(_re.escape(a) + r"[^" + _re.escape(a + b) + r"\n]{1,200}" + _re.escape(b), a + "…" + b, s)
    return s


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
            # [2026-10-06 어휘 O6] "Critical" → "Notable" — 판정 등급 "Critical success/failure"와 한 렌더에 겹쳤다.
            #   이 기록의 조건 = 건강 변화·위치 이동·changes.notable(위 감지부) — 원천 필드 이름과 같은 말.
            parts.append(f"  Notable(T{c.get('turn', '?')}): {strip_quoted(c.get('description') or '')[:80]}")

    return "\n".join(parts)


# =========================================================
# ARC SYSTEM — [2026-10-24 C75 G2] 중력 우물로 이사
# =========================================================
# 아크는 줄거리(`is_arc`)가 아니라 `well_store`(sqlite well_log 행 → fold)에 산다. 옛 격상 · 흡수 · 좌표 5축 tick ·
# 감쇠 · 잠듦 · 초신성 · 배치 갱신/결정 함수는 은퇴했다(is_arc · get_active_arcs · promote_to_arc ·
# demote_to_storyline · supernova_branch · find_absorbing_arc · check_promote_threshold · similar_seed_present ·
# compute_absorption · absorb_to_arc · compute_decay_rate · compute_dormant_threshold · compute_proximity_update ·
# compute_momentum_update · compute_phase_drift · apply_arc_updates · apply_arc_decisions · tick_arcs).
#   읽기: well_store.arcs(ch) — 옛 get_active_arcs 칸 이름 그대로. 갱신: well_store.apply_updates / apply_decisions.
#   매 턴: well_store.tick(옮기기 · 시계 맞춤 · 증발 · 그리기 캐시). 스펙 C75_G2_옮기기·강착·관측_지도·스펙_2026-10-24.
# 남은 것: 그리기 모드 lerp(우물 캐시가 부른다) · aspects.

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


# =========================================================
# Aspects 부활 — 시스템 교차 결합 신호
# =========================================================
# 8개 라벨 결합 조건 평가. Arc 사이클 시 백업, V3 재이식.
# 라벨 내부 식별자 (산문 노출 X), typological 디렉티브는 config.ASPECTS_DIRECTIVES.

def _arcs_of(state) -> list:
    """[2026-10-24 C75 G2] aspects 가 보는 아크 = `{"arcs": well_store.arcs(ch)}` 의 목록(이름 얻은 산 우물)."""
    arcs = (state or {}).get("arcs") if isinstance(state, dict) else None
    return [a for a in (arcs or []) if isinstance(a, dict)]


def _any_active_arc_proximate(state: dict, threshold: float = None) -> bool:
    """아크(우물) 중 proximity ≥ threshold인 게 있나? (외부 사건 인식 자리)."""
    import config as _cfg
    if threshold is None:
        threshold = _cfg.ASPECTS_ARC_PROXIMITY_THRESHOLD
    return any(float(a.get("proximity", 0.0) or 0.0) >= threshold for a in _arcs_of(state))


def _any_arc_armed(state: dict) -> bool:
    """아크(우물) 중 armed=True인 게 있나? (큰 흐름 임박)."""
    return any(a.get("armed") for a in _arcs_of(state))


def compute_aspects(bus, state: dict, primary_axis: str = "vigor") -> list:
    """
    시스템 교차 결합 평가. 활성 라벨 리스트 반환.

    Args:
        bus: SharedBus (judgment / anomaly / vigor / composure / doom 읽기)
        state: {"arcs": well_store.arcs(ch)} (Arc proximity / armed 평가용 — C75 G2)
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

    # [2026-09-24 감사] `or 100` 이 값 0(완전 고갈)을 100 으로 바꿔 고갈 aspect·abyss 판정이 정확히 0 에서 실패했다.
    v_val = int(vigor.get("value") if vigor.get("value") is not None else 100)
    c_val = int(composure.get("value") if composure.get("value") is not None else 100)

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
