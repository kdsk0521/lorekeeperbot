"""
Lorekeeper - Universal Narrative Engine (UNE) Facade
The main entry point for the UNE engine.
"""

import logging
import random
from typing import Dict, Any, List, Tuple

from orchestration_context import GameContext
from waterfall_pipeline import WaterfallPipeline
import domain_manager
import game_character
import game_world

logger = logging.getLogger("UNE")


def _cv_vigor(channel_id: str, user_id: str, mem: Dict[str, Any]) -> int:
    """[2026-08-18 Phase 2.5] 기력 = 레지스트리 값. 폴백 계단은 custom_vars 한 곳에만 있다."""
    try:
        import custom_vars as _cv
        return _cv.vigor_value(channel_id, user_id, mem)
    except Exception as e:
        logger.debug(f"[CustomVar] 기력 조회 skip: {e}")
        src = (mem or {}).get("vigor") or (mem or {}).get("mental") or {}
        return int(src.get("value", 100) or 100)


def _to_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _position_tier(value: float) -> str:
    if value <= 0.25:
        return "desperate"
    if value <= 0.5:
        return "risky"
    return "controlled"


def _mc_move(position: str, result: str) -> str:
    matrix = {
        ("desperate", "critical_failure"): "Catastrophic - something irreversible happens.",
        ("desperate", "failure"): "the threat becomes real - irreversible consequences.",
        ("desperate", "partial"): "Heavy price - gain what was sought but lose something.",
        ("desperate", "success"): "Dramatic turnaround - shining in the direst moment.",
        ("desperate", "critical_success"): "Miraculous reversal - transcendent moment.",
        ("risky", "critical_failure"): "Worst case unfolds - danger becomes reality.",
        ("risky", "failure"): "the danger escalates - a new one reveals itself.",
        ("risky", "partial"): "Success with cost - complications follow.",
        ("risky", "success"): "Danger cleared - competent execution.",
        ("risky", "critical_success"): "Brilliant - impressive against the odds.",
        ("controlled", "critical_failure"): "Unexpected reversal - safety shatters.",
        ("controlled", "failure"): "Minor cost - a small setback.",
        ("controlled", "partial"): "Minor friction - less smooth than expected.",
        ("controlled", "success"): "Clean - smooth and effortless.",
        ("controlled", "critical_success"): "Overwhelming mastery - exceeds expectations.",
    }
    return matrix.get((position, result), "Outcome determined by world logic.")


def _collect_aspect_stance(aspects: Any) -> Tuple[List[str], List[str]]:
    if not isinstance(aspects, list):
        return [], []

    favorable: List[str] = []
    against: List[str] = []
    for aspect in aspects:
        if not isinstance(aspect, dict):
            continue
        name = str(aspect.get("name", "")).strip()
        if not name:
            continue
        stance = str(aspect.get("for_or_against", aspect.get("stance", ""))).strip().lower()
        if stance in ("for", "support", "positive", "pro"):
            favorable.append(name)
        elif stance in ("against", "oppose", "negative", "con"):
            against.append(name)
    return favorable, against


# ── Directing Notation Tables (♪ 음악 | ▶ 카메라 | ◎ 사진 — 3축 연출 표기) ──
_POSITION_NOTATION = {
    "controlled": "situation | ♪ mp, andante, legato | ▶ wide, parallel, pan | ◎ real-time",
    "risky":      "situation | ♪ f, allegro, marcato | ▶ two-shot, facing, cut | ◎ slow-motion",
    "desperate":  "situation | ♪ ff, presto, staccato | ▶ low-angle, back-to-back, jump-cut | ◎ freeze",
}
_ENERGY_NOTATION = {
    "idle":       "scene | ♪ mp, andante, legato | ▶ pan, pillow, long-take | ◎ long-exposure",
    "steady":     "scene | ♪ mf, andante, legato | ▶ eye-level, parallel, match-cut | ◎ real-time",
    "rising":     "scene | ♪ f, allegro, marcato, crescendo | ▶ two-shot, facing, crosscut | ◎ interval",
    "falling":    "scene | ♪ p, adagio, legato, diminuendo | ▶ long-take, back-to-back, fade | ◎ long-exposure",
    "peak":       "scene | ♪ ff, presto, sforzando | ▶ close-up, cut, jump-cut | ◎ slow-motion",
    "stagnant":   "scene | ♪ pp, largo, legato | ▶ long-take, pillow, height-gap | ◎ long-exposure",
    "detonation": "scene | ♪ sfz, presto, sforzando | ▶ wide, montage, cut | ◎ freeze",
    "aftershock": "scene | ♪ p, adagio, staccato | ▶ long-take, back-to-back, fade | ◎ long-exposure",
}
_VIGOR_NOTATION = {
    "high":       "body | ♪ f, allegro, legato | ▶ wide, parallel | ◎ real-time",
    "strained":   "body | ♪ p, adagio, marcato | ▶ close-up:muscle, height-gap | ◎ slow-motion",
    "collapsing": "body | ♪ pp, largo, staccato | ▶ close-up:breath, back-to-back | ◎ freeze",
}
_COMPOSURE_NOTATION = {
    "high":       "psyche | ♪ mf, andante, legato | ▶ two-shot, parallel, match-cut | ◎ real-time",
    "strained":   "psyche | ♪ p, adagio, staccato | ▶ close-up:gaze, height-gap | ◎ slow-motion",
    "collapsing": "psyche | ♪ pp, largo, sforzando | ▶ high-angle, back-to-back | ◎ freeze",
}
_MIXED_NOTATION = {
    "desperate": "body+psyche | ♪ pp, largo, staccato | ▶ high-angle, back-to-back | ◎ freeze",
    "reckless":  "action | ♪ f, presto, sforzando | ▶ wide, jump-cut | ◎ slow-motion",
    "fragile":   "consciousness | ♪ p, adagio, legato | ▶ close-up:eyes, pillow | ◎ long-exposure",
}
# _DOOM_NOTATION 제거 (2026-07-06 감사): 형제(_COMPOSURE/_MIXED)와 달리 소비자 0 —
# 둠→챕터볼륨 리브랜드로 world-layer doom 연출 분기가 사라진 잔재. doom 무드는
# doom_module lens×phase atmosphere가 담당.
_SCENE_PHOTO_OVERRIDE = {
    "summary":  "◎ bulb",
    "combat":   "◎ slow-motion",
    "intimate": "◎ real-time",
}
def _build_world_layer(bus) -> str:
    """World Layer: 연출 표기 + 장면 초점 + 액션만.
    데이터(position reason, effect, NPC attitudes, psyche, narrative chain,
    quality flags)는 iceberg 개별 슬롯(13-17, 28)이 커버."""
    dai = bus.dai if isinstance(bus.dai, dict) else {}
    parts: List[str] = []

    # Position → directing notation ONLY (reason+friction은 Slot 13 iceberg)
    pos = dai.get("position", {}) if isinstance(dai.get("position"), dict) else {}
    pos_value = _to_float(pos.get("value", 0.5), 0.5)
    pos_tier = _position_tier(pos_value)
    notation = _POSITION_NOTATION.get(pos_tier, "")
    if notation:
        parts.append(notation)

    scene_type = str(dai.get("scene_type", "normal"))

    # Energy → directing notation ONLY (산문 힌트는 Slot 16 iceberg)
    energy = str(dai.get("energy_direction", "idle"))
    energy_notation = _ENERGY_NOTATION.get(energy, "")
    if energy_notation:
        parts.append(energy_notation)

    # Scene → ◎ 시간 밀도 보정 (SceneType이 에너지 테이블 기본값을 override)
    scene_photo = _SCENE_PHOTO_OVERRIDE.get(scene_type, "")
    if scene_photo:
        parts.append(f"time override: {scene_photo}")

    # Action Reading (고유 — iceberg에 없음)
    needs_judgment = bool(dai.get("needs_judgment", False))
    action_meta = dai.get("action_meta", {}) if isinstance(dai.get("action_meta"), dict) else {}
    action_name = str(action_meta.get("action", "")).strip()
    if action_name:
        difficulty = str(action_meta.get("difficulty", "normal"))
        parts.append(f"'{action_name}' attempt — {difficulty}")
        if not needs_judgment:
            parts.append(f"no roll; resolved by the situation ({pos_tier}) and the world's logic.")

    if not parts:
        return ""
    return "── World ──\n" + "\n".join(parts)


def _build_events_layer(context, bus) -> str:
    parts: List[str] = []

    # World Event → fact only (metadata stripped)
    anomaly = bus.anomaly if isinstance(bus.anomaly, dict) else {}
    if anomaly.get("triggered"):
        line = anomaly.get("line", "")
        if line:
            parts.append(f"world event: {line}")

    # Active Conditions → tag stripped
    _st_state = {}
    _ch_id = (context.narrative_anchors or {}).get("channel_id", "")
    if _ch_id:
        import domain_manager as _dm_ev
        _st_state = _dm_ev.get_storyteller_state(_ch_id)
    _all_conds = _st_state.get("active_conditions", [])
    _pc_loc = (bus.dai.get("current_location", "") if isinstance(bus.dai, dict) else "").strip()

    for cond in _all_conds:
        cond_loc = (cond.get("location") or "").strip()
        if not cond_loc or cond_loc == _pc_loc:
            _desc = cond.get("description", "")
            if _desc:
                parts.append(
                    f"current situation: {_desc} — "
                    "it shapes the environment, NPC behavior, and the available actions."
                )

    # Omen → tag stripped
    _omen = anomaly.get("omen")
    if isinstance(_omen, dict) and _omen.get("tag"):
        _omen_line = _omen.get("line", "")
        if _omen_line:
            parts.append(
                f"omen: {_omen_line} — "
                "hinted only through sensory detail; the event itself stays unfired."
            )

    # Condition resolved → text only
    _cond_resolved_log = anomaly.get("conditions_resolved_log")
    if _cond_resolved_log:
        parts.append(str(_cond_resolved_log))

    # Clock Progress / Doom Shift → REMOVED (system_msg only)
    doom = bus.doom if isinstance(bus.doom, dict) else {}

    # Completed clocks → fact
    completed_clocks = doom.get("completed_this_turn", [])
    if isinstance(completed_clocks, list):
        for clock in completed_clocks:
            if isinstance(clock, dict):
                threat = clock.get("threat", "")
                cname = clock.get("name", "?")
                parts.append(
                    f"{cname} has become real — {threat}. "
                    "the changed world surfaces; the POV stays with the PC."
                )

    # Climax → fact
    if doom.get("climax_triggered"):
        parts.append("critical point — everything has converged at once. the final choice.")

    # Defense → REMOVED (mechanical log)

    # Quest Failed → fact
    for fail in doom.get("quest_failed", []):
        if isinstance(fail, dict):
            parts.append(f"{fail.get('quest', '?')} has failed — {fail.get('reason', '')}")

    # Imminent clocks → no numbers
    clocks = doom.get("clocks", [])
    if isinstance(clocks, list) and clocks:
        imminent: List[str] = []
        for clock in clocks:
            if not isinstance(clock, dict) or clock.get("resolved"):
                continue
            segments = int(_to_float(clock.get("segments", 4), 4))
            filled = int(_to_float(clock.get("filled", clock.get("progress", 0)), 0))
            remaining = max(0, segments - filled)
            if remaining <= 1:
                imminent.append(str(clock.get("name", "?")))
        if imminent:
            parts.append(" | ".join(f"{n} is about to become real" for n in imminent[:3]))

    # Status effects → tag stripped
    status_seen = set()
    for container in (bus.vigor, bus.composure, bus.dai):
        if not isinstance(container, dict):
            continue
        for key, label_kr in (("new_status_effects", "begins"), ("expired_status_effects", "ends")):
            entries = container.get(key, [])
            if not isinstance(entries, list):
                continue
            for entry in entries:
                name = ""
                hint = ""
                if isinstance(entry, dict):
                    name = str(entry.get("name", "")).strip()
                    hint = str(entry.get("narrative_hint", "")).strip()
                else:
                    name = str(entry).strip()
                if not name:
                    continue
                sig = (label_kr, name)
                if sig in status_seen:
                    continue
                status_seen.add(sig)
                # [2026-07-06] 시스템 라벨("X begins") → 신체-우선 연출 지시 (body-before-mind).
                # 상태명은 렌더러 참조용 컨텐츠 — 산문에 이름/상태언어로 올리지 말라는 프레임 내장.
                if label_kr == "begins":
                    line = (f"{name}: onset — the body notices before the mind names it "
                            "(a sensation, a reflex, a small failure of ease). "
                            "the state itself stays unnamed; no status language in prose.")
                else:
                    line = (f"{name}: lifting — register it as absence "
                            "(breath comes easier, a weight quietly gone). no announcement.")
                if hint:
                    line += f" ({hint})"
                parts.append(line)

    # Flashback → tag stripped
    flashback = bus.dai.get("flashback_result") if isinstance(bus.dai, dict) else None
    if not flashback and isinstance(bus.dai, dict):
        flashback = bus.dai.get("flashback_eval")
    if isinstance(flashback, dict):
        declaration = str(flashback.get("declaration", "")).strip()
        if not declaration:
            declaration = str(flashback.get("reason", "")).strip()
        if declaration:
            parts.append(f"retroactive declaration: {declaration}")

    # Quest Echo → tag stripped
    # 매 턴 stale 퀘스트(QUEST_STALE_ARCHIVE_TURNS 이상) archive 이동 후,
    # 남은 active 퀘스트 중 8턴+ stale은 directive softening (강제 진전 금지).
    # 즉 8~(threshold-1)턴 사이가 "약화된 채 살아있는" 구간.
    channel_id = (context.narrative_anchors or {}).get("channel_id", "")
    if channel_id:
        try:
            import game_character
            turn_index = int(_to_float((bus.dai or {}).get("turn_index", 0), 0))
            # Archive 먼저: threshold 넘은 퀘스트를 active에서 빼냄
            try:
                game_character.archive_stale_quests(channel_id, turn_index)
            except Exception:
                pass
            # 남은 active 퀘스트에서 8턴+ stale은 약화 directive
            active_quests = game_character.get_active_quests_raw(channel_id)
            for q in active_quests:
                if not isinstance(q, dict):
                    continue
                last_progress_turn = q.get("last_progress_turn", 0)
                stale_turns = turn_index - last_progress_turn
                if stale_turns >= 8:
                    parts.append(
                        f"{q.get('content', '?')} — "
                        "surfaces only when the user moves in this direction; the thread advances at their lead."
                    )
        except Exception:
            pass

    # Downtime activity (from dead code migration)
    _dai_dt = bus.dai if isinstance(bus.dai, dict) else {}
    _dt_rest = _dai_dt.get("rest_eval") or {}
    _dt_activity = _dt_rest.get("activity", "rest")
    if _dt_activity != "rest" and _dt_rest.get("detected"):
        _dt_hints = {
            "recover": "the PC spends time tending to injuries.",
            "vice": "the PC spends time lost in indulgence.",
            "train": "the PC throws themselves into training.",
            "socialize": "the PC spends time among people.",
            "project": "the PC focuses on their work.",
        }
        parts.append(_dt_hints.get(_dt_activity, "the PC spends the time with purpose."))
        if _dai_dt.get("vice_overindulge"):
            parts.append("the cost of indulgence has come back around.")

    if not parts:
        return ""
    return "── Events ──\n" + "\n".join(parts)


# Universal narrative principles — separated from MC Moves (genre flavor)
CONSEQUENCE_DIRECTIVES = {
    "critical_success": (
        "the PC achieves more than they intended. "
        "A new possibility opens, or the PC takes decisive control of the situation. "
        "with NPCs present, the success shifts the relationship for the better."
    ),
    "partial": (
        "the PC's intent is achieved, but one unwanted change follows with it. "
        "What was lost, exposed, or complicated takes concrete shape. "
        "a partial success carries a cost; the cost-free version doesn't exist. "
        "with NPCs present, the cost leaves a subtle mark on the relationship."
    ),
    "failure": (
        "the PC's intent goes unachieved. "
        "The situation is now different from before the attempt — more than 'it didn't work.' "
        "What has changed shows in the scene. "
        "with NPCs present, their reaction to witnessing it surfaces."
    ),
    "critical_failure": (
        "an irreversible change occurs in this scene. "
        "The opposite of the PC's intent comes about, or an unforeseen reality reveals itself. "
        "The world after this moment differs from the world before. "
        "with NPCs present, the catastrophe shakes the relationship to its foundation."
    ),
}


def _build_judgment_layer(bus, mask: str) -> str:
    judgment = bus.judgment if isinstance(bus.judgment, dict) else {}
    if not judgment.get("active"):
        return ""

    meta = judgment.get("meta", {}) if isinstance(judgment.get("meta"), dict) else {}
    action = str(meta.get("action", "action"))
    result = str(judgment.get("result", "failure"))
    reason = str(judgment.get("reason", "")).strip()

    dai = bus.dai if isinstance(bus.dai, dict) else {}
    pos_value = _to_float((dai.get("position", {}) or {}).get("value", 0.5), 0.5)
    pos_tier = _position_tier(pos_value)

    move = _mc_move(pos_tier, result)

    favorable, against = _collect_aspect_stance(dai.get("aspects", []))

    # Natural language — no tags, no framework labels
    _result_kr = {
        "critical_success": "critical success", "success": "success",
        "partial": "partial success", "failure": "failure", "critical_failure": "critical failure",
    }
    _pos_kr = {"controlled": "controlled position", "risky": "risky position", "desperate": "dire position"}
    reason_part = f" {reason}" if reason else ""
    lines = [
        f"{mask} attempted '{action}'.{reason_part}",
        f"{_result_kr.get(result, result)} — {_pos_kr.get(pos_tier, pos_tier)}.",
        move,
    ]
    if favorable:
        lines.append("in favor: " + ", ".join(favorable))
    if against:
        lines.append("against: " + ", ".join(against))

    # 범용 서사 원칙 (장르 불문) — tag stripped
    cons_dir = CONSEQUENCE_DIRECTIVES.get(result, "")
    if cons_dir:
        lines.append(cons_dir)

    # Effort / Absorb (각오 / 흡수) — from dead code migration
    effort_used = judgment.get("effort_used")
    if effort_used:
        eu_action = effort_used.get("action", "")
        if judgment.get("absorb_applied"):
            lines.append(f"the PC gave everything to {eu_action} and still failed; resolve spared them the worst.")
        elif result in ("failure", "critical_failure"):
            lines.append(f"the PC staked all on {eu_action} and failed; the price paid spared the worst.")
        else:
            lines.append(f"the PC paid the price for {eu_action}, and it was worth it.")
    elif isinstance(bus.dai, dict) and bus.dai.get("effort_failed"):
        lines.append("the PC steeled themselves, but the body wouldn't follow.")

    return "\n".join(lines)


def _build_atmosphere_layer(context, bus) -> str:
    parts: List[str] = []

    # N8 경량 채택: [지배] 태그 + [전환] 디렉티브만 사용 (합성 파이프라인 제거)
    dai = bus.dai if isinstance(bus.dai, dict) else {}

    # [전환] 디렉티브 — 에너지 방향 변화 시에만
    from notation_compositor import compose_transition
    # [2026-07-02 shape fix] scene_continuity는 {"frames":[...]} — 마지막 프레임(=직전 턴)을 넘겨야
    # 실제 전환에서만 발화. (기존엔 dict 통째로 넘겨 prev_energy 항상 idle → 상승 지속 중에도
    # 매 턴 "정적이 깨진다" 오발화 + 진짜 전환(rising→falling 등)은 영구 침묵)
    _sc_raw = (context.narrative_anchors or {}).get("session_memory", {}).get("scene_continuity", {})
    _sc_frames = _sc_raw.get("frames", []) if isinstance(_sc_raw, dict) else []
    prev_frame = _sc_frames[-1] if _sc_frames else {}
    current_energy = str(dai.get("energy_direction", "idle"))
    _transition = compose_transition(prev_frame, current_energy)
    if _transition:
        parts.append(_transition)

    # [지배] 태그 — 장르+씬타입 기반 우선 레이어 1줄
    from notation_compositor import LAYER_PRIORITY, GENRE_NOTATION_WEIGHTS
    scene_type = str(dai.get("scene_type", "normal"))
    mechanic = context.request.genres.get("mechanic", {})
    genre = mechanic.get("primary_lens", "")
    _weights = GENRE_NOTATION_WEIGHTS.get(genre, {})
    _dominant = _weights.get("dominant", "")
    _reframe = _weights.get("reframe", {})
    if _dominant:
        _lens = _reframe.get(_dominant, "")
        _lens_tag = f" ({_lens})" if _lens else ""
        parts.append(f"[dominant:{_dominant}{_lens_tag}]")

    vigor_val = int(_to_float((bus.vigor or {}).get("value", 100), 100))
    composure_val = int(_to_float((bus.composure or {}).get("value", 100), 100))

    # Vigor → directing notation
    if vigor_val >= 70:
        parts.append(_VIGOR_NOTATION["high"])
    elif vigor_val >= 40:
        pass  # 정상 구간 — directive 없음
    elif vigor_val >= 15:
        parts.append(_VIGOR_NOTATION["strained"])
    else:
        parts.append(_VIGOR_NOTATION["collapsing"])

    # Composure → directing notation
    if composure_val >= 70:
        parts.append(_COMPOSURE_NOTATION["high"])
    elif composure_val >= 40:
        pass  # 정상 구간
    elif composure_val >= 15:
        parts.append(_COMPOSURE_NOTATION["strained"])
    else:
        parts.append(_COMPOSURE_NOTATION["collapsing"])

    # Mixed conditions → directing notation
    v_low = vigor_val <= 39
    c_low = composure_val <= 39

    if v_low and c_low:
        parts.append(_MIXED_NOTATION["desperate"])
    elif vigor_val >= 70 and c_low:
        parts.append(_MIXED_NOTATION["reckless"])
    elif composure_val >= 70 and v_low:
        parts.append(_MIXED_NOTATION["fragile"])

    # NPC Reaction → natural language
    if composure_val <= 14:
        parts.append("those nearby sense the PC's instability — concern, avoidance, or exploitation.")
    if vigor_val <= 14:
        parts.append("those nearby witness the PC's physical limit.")

    # Doom = Chapter Volume Gauge (Phase × Lens) — 둠 리브랜드 산문 주입
    # phase × lens atmosphere block을 산문 주입의 진짜 매체로 사용.
    # 페이즈 letter(起承轉結間)는 식별자, lens(noir/comedy/romance/drama)는 톤.
    doom_val = int(_to_float((bus.doom or {}).get("value", 0), 0))
    import config as _cfg
    lens_tags = bus.doom.get("lens_tags", []) if isinstance(bus.doom, dict) else []
    phase = bus.doom.get("chapter_phase", "") if isinstance(bus.doom, dict) else ""
    if not phase:
        phase = _cfg.get_lens_phase(doom_val, lens_tags[0] if lens_tags else "default")

    if not lens_tags:
        # C-Lens 미활성 → default 블록만
        block = _cfg.get_lens_atmosphere("default", phase)
        if block:
            parts.append(f"[Tension {doom_val}% — phase {phase}]\n{block}")
    elif len(lens_tags) == 1:
        # 단일 lens
        block = _cfg.get_lens_atmosphere(lens_tags[0], phase)
        if block:
            parts.append(f"[Tension {doom_val}% — phase {phase}, {lens_tags[0]}]\n{block}")
    else:
        # 다중 lens (hybrid) — 양쪽 block + neither-erases 디렉티브
        blocks = [(lens, _cfg.get_lens_atmosphere(lens, phase)) for lens in lens_tags]
        blocks = [(l, b) for l, b in blocks if b]
        if blocks:
            header = f"[Tension {doom_val}% — phase {phase}, dual register: {' × '.join(l for l, _ in blocks)}]"
            joined = "\n× crosscut with:\n".join(b for _, b in blocks)
            parts.append(f"{header}\n{joined}\n— neither register erases the other; both qualities in the same beat")

    # 챕터 종결 라벨 (climax 발동 직후, 間 페이즈)
    if isinstance(bus.doom, dict) and bus.doom.get("intermission_active"):
        parts.append("chapter close — epilogue / lingering phase. new clocks carry over to the next chapter.")

    # ◎ optics (conditional — derived from DAI fields)
    dai = bus.dai if isinstance(bus.dai, dict) else {}
    optical: List[str] = []

    # [multiple-exposure]: memory_triggers → time overlap
    mem_triggers = dai.get("memory_triggers", [])
    if isinstance(mem_triggers, list) and mem_triggers:
        optical.append("[multiple-exposure]")

    # [polarizer]: self_opacity → facade crack (POV guard: surface contradictions only)
    psyche_states = dai.get("psyche_states", {})
    if isinstance(psyche_states, dict):
        for _npc_data in psyche_states.values():
            if isinstance(_npc_data, dict):
                if (_npc_data.get("psyche") or {}).get("self_opacity"):
                    optical.append("[polarizer]")
                    break

    # [infrared]: leak_risk >= medium → behavioral leak (POV guard: distortion, not secrets)
    npc_knowledge = dai.get("npc_knowledge", {})
    if isinstance(npc_knowledge, dict):
        for _kn_data in npc_knowledge.values():
            if isinstance(_kn_data, dict) and _kn_data.get("leak_risk") in ("medium", "high"):
                optical.append("[infrared]")
                break

    # [solarization]: doom >= 80 → inversion
    if doom_val >= 80:
        optical.append("[solarization]")

    # [vignette]: position <= 0.15 → tunnel vision
    pos = dai.get("position", {}) if isinstance(dai.get("position"), dict) else {}
    if _to_float(pos.get("value", 0.5), 0.5) <= 0.15:
        optical.append("[vignette]")

    if optical:
        parts.append("◎ optics: " + " ".join(optical))

    # Pacing rules (narrative constraints only — rhythm/density covered by ♪▶◎)
    if doom_val < 20:
        parts.append("the existing tension stays unresolved; this turn plants seeds, not payoffs.")
    elif doom_val >= 80:
        parts.append("resolution comes only through PC action; the way out is earned, not handed over.")

    # Distant conditions → natural language (camera NOT there)
    _st_atm = {}
    _ch_atm = (context.narrative_anchors or {}).get("channel_id", "")
    if _ch_atm:
        import domain_manager as _dm_atm
        _st_atm = _dm_atm.get_storyteller_state(_ch_atm)
    _all_conds_atm = _st_atm.get("active_conditions", [])
    _pc_loc_atm = (bus.dai.get("current_location", "") if isinstance(bus.dai, dict) else "").strip()

    for _c in _all_conds_atm:
        _cloc = (_c.get("location") or "").strip()
        if _cloc and _cloc != _pc_loc_atm:
            parts.append(f"a sense of {_c.get('tag', '?')} drifts in from far off")
    # [World Conditions] → REMOVED (Events Active와 중복)

    # 서사 공간 → tag stripped
    narrative_space = int(_to_float((bus.doom or {}).get("narrative_space", 0), 0))
    if narrative_space > 0:
        if narrative_space >= 15:
            intensity = "wide"
        elif narrative_space >= 8:
            intensity = "moderate"
        else:
            intensity = "small"
        mechanic = context.request.genres.get("mechanic", {})
        primary_res = mechanic.get("primary_resource") or "vigor"
        if primary_res == "vigor":
            parts.append(
                f"{intensity} narrative space — the tension has eased. room to:\n"
                "- deepen character relationships (dialogue, emotional exchange, confirming bonds)\n"
                "- the world's response to the user's actions, and its echo\n"
                "- a natural seed of foreshadowing for the next crisis"
            )
        else:
            parts.append(
                f"{intensity} narrative space — the everyday has returned. room to:\n"
                "- deepen relationships (small talk, emotional exchange)\n"
                "- the effect the user's choices had on those around them\n"
                "- a natural seed of new change"
            )

    # Clock surfacing → tag stripped (clock name removed, threat only)
    clocks = (bus.doom or {}).get("clocks", [])
    if isinstance(clocks, list):
        mechanic = context.request.genres.get("mechanic", {})
        primary_res = mechanic.get("primary_resource") or "vigor"
        for clock in clocks:
            if not isinstance(clock, dict) or clock.get("resolved"):
                continue
            segments = int(_to_float(clock.get("segments", 4), 4))
            filled = int(_to_float(clock.get("filled", 0), 0))
            if segments <= 0:
                continue
            ratio = filled / segments
            threat = clock.get("threat", "")
            if ratio >= 0.75:
                if primary_res == "vigor":
                    parts.append(f"the omen of {threat} is clear — it takes concrete shape around the PC; the POV stays with the PC.")
                else:
                    parts.append(f"the omen of {threat} is felt — in shifts among those nearby, in subtle atmosphere; the POV stays with the PC.")
            elif ratio >= 0.5:
                if primary_res == "vigor":
                    parts.append(f"a sign of {threat} hinted through sensory detail.")
                else:
                    parts.append(f"the omen of {threat} hinted through small everyday dissonances.")

    # Status effects → tag stripped
    status_effects = (context.narrative_anchors or {}).get("status_effects", [])
    if isinstance(status_effects, list):
        for status in status_effects[:3]:
            if not isinstance(status, dict):
                continue
            name = str(status.get("name", "")).strip()
            if not name:
                continue
            hint = str(status.get("narrative_hint", status.get("description", ""))).strip()
            if hint:
                parts.append(f"{name}: {hint}")

    # POV Lock → tag stripped
    if isinstance(clocks, list):
        active_clocks = [c for c in clocks if isinstance(c, dict) and not c.get("resolved")]
        if active_clocks:
            parts.append(
                "Clock events are rendered strictly within the PC's POV. "
                "No 'meanwhile', 'around that time', 'elsewhere' — no POV shift. "
                "Only what the PC directly witnesses or senses."
            )

    if not parts:
        return ""
    return "── Atmosphere ──\n" + "\n".join(parts)


def _build_aspects_layer(context, bus) -> str:
    """
    Aspects (시스템 교차 결합) 산문 디렉티브. 활성 라벨 → typological 텍스트만.
    라벨 자체는 Pro 산문에 직접 노출 X (내부 식별자).
    Arc 사이클 시 백업한 자리 V3 재이식.
    """
    try:
        import narrative_tracker as _nt
        import domain_manager as _dm
        import config as _cfg
    except Exception:
        return ""

    # primary axis (자원 임계 평가용)
    try:
        mechanic = context.request.genres.get("mechanic", {})
        primary_axis = mechanic.get("primary_resource") or "vigor"
    except Exception:
        primary_axis = "vigor"

    # storyteller_state (arc proximity / armed 평가용)
    channel_id = (context.narrative_anchors or {}).get("channel_id", "") if context.narrative_anchors else ""
    state = {}
    if channel_id:
        try:
            state = _dm.get_narrative_tracker_state(channel_id)
        except Exception:
            state = {}

    # 활성 라벨 평가
    try:
        active = _nt.compute_aspects(bus, state, primary_axis=primary_axis)
    except Exception:
        return ""

    if not active:
        return ""

    # typological 디렉티브 변환
    directives = _cfg.ASPECTS_DIRECTIVES or {}
    lines = []
    for label in active:
        text = directives.get(label, "")
        if text:
            lines.append(text)

    if not lines:
        return ""

    return (
        "── Convergence Flow ──\n"
        + "\n".join(lines)
        + "\n(only the grain of these joins carries into the prose; the naming labels stay out of it.)"
    )


def _build_system_message(bus) -> str:
    chunks: List[str] = []

    judgment = bus.judgment if isinstance(bus.judgment, dict) else {}
    if judgment.get("output"):
        chunks.append(str(judgment.get("output")))

    anomaly = bus.anomaly if isinstance(bus.anomaly, dict) else {}
    if anomaly.get("triggered"):
        tag = anomaly.get("tag", "anomaly")
        chunks.append(f"[이변] {tag}")

    doom = bus.doom if isinstance(bus.doom, dict) else {}
    # relief_log 제거 (2026-05-23) — legacy 위기진폭 잔재
    for key in ("mental_pressure_log", "clock_log", "log"):
        val = doom.get(key)
        if val:
            chunks.append(str(val))

    vigor = bus.vigor if isinstance(bus.vigor, dict) else {}
    if vigor.get("log"):
        chunks.append(str(vigor.get("log")))

    deduped: List[str] = []
    seen = set()
    for chunk in chunks:
        text = chunk.strip()
        if not text or text in seen:
            continue
        seen.add(text)
        deduped.append(text)

    return "\n".join(deduped).strip()

def convert_to_game_context(channel_id: str, user_id: str, user_input: str, lore_chunks_ranked: list = None) -> GameContext:
    """[UNE Bridge] ParticipantData -> GameContext"""
    from orchestration_context import GameContext, RequestData, SharedBus

    p_data = domain_manager.get_participant_data(channel_id, user_id)
    mem = p_data.get("ai_memory", {}) if p_data else {}
    world = domain_manager.get_world_state(channel_id)

    # Genre mapping
    genres_raw = domain_manager.get_active_genres(channel_id)
    if isinstance(genres_raw, dict) and "layers" in genres_raw:
        layers = genres_raw["layers"]
        mechanic = genres_raw.get("mechanic_profile", {})
        genres = {
            "stage": layers.get("world_setting", []),
            "flavor": layers.get("style_tech", []),
            "lens": layers.get("narrative_tone", []),
            "atmosphere": genres_raw.get("atmosphere_guide", ""),
            "mechanic": mechanic,
        }
    else:
        genres = {
            "stage": [genres_raw[0]] if isinstance(genres_raw, list) and genres_raw else ([str(genres_raw)] if genres_raw else []),
            "flavor": [],
            "lens": [],
            "atmosphere": "",
            "mechanic": {},
        }
    # 하위 호환: str이 들어오면 List로 래핑
    for key in ["stage", "flavor", "lens"]:
        val = genres[key]
        if isinstance(val, str):
            genres[key] = [val] if val else []

    # Active Modules
    active_modules = domain_manager.get_active_modules(channel_id)

    # Lore Summary (V4) + Chunks (V5)
    lore_summary = domain_manager.get_lore_summary_data(channel_id)
    lore_chunks = domain_manager.get_lore_chunks(channel_id)

    # History & Lore Text (V4 - fallback)
    history = domain_manager.get_history(channel_id)
    recent_history = history[-30:] if history else []  # Last 30 turns
    history_text = "\n".join([f"{h['role']}: {h['content']}" for h in recent_history])
    lore_text = domain_manager.get_lore(channel_id)

    # Narrative Anchors (행동자 PC)
    anchors = {
        "channel_id": channel_id,
        "appearance": mem.get("appearance", ""),
        "personality": mem.get("personality", ""),
        "background": mem.get("background", ""),
        "relations": mem.get("relationships", {}),
        "passives": mem.get("passives", []),
        "status_effects": p_data.get("status_effects", []) if p_data else [],
        "inventory": game_character.migrate_notebook_to_inventory(mem.get("inventory", [])).get("items", []),
        "memos": mem.get("memos", [])
    }

    # 모든 활성 PC 정보 수집 (다인 플레이 지원)
    all_participants = domain_manager.get_domain(channel_id).get("participants", {})
    all_pcs = {}
    for uid, pdata in all_participants.items():
        if pdata.get("status") == "active":
            pmem = pdata.get("ai_memory", {})
            all_pcs[uid] = {
                "mask": pdata.get("mask", "Unknown"),
                "appearance": pmem.get("appearance", ""),
                "personality": pmem.get("personality", ""),
                "passives": pmem.get("passives", []),
                # [Phase 2.5] 기력은 레지스트리 우선(PC별 슬롯), 없으면 옛 자리 폴백.
                "vigor_value": _cv_vigor(channel_id, uid, pmem),
                "composure_value": pmem.get("composure", {}).get("value", 100),
            }
    anchors["all_pcs"] = all_pcs
    anchors["acting_user_id"] = user_id

    # NPC Knowledge & Attitudes (피드백용)
    anchors["stored_npc_knowledge"] = domain_manager.get_npc_knowledge(channel_id)
    anchors["stored_npc_attitudes"] = domain_manager.get_npc_attitudes(channel_id)
    # [2026-08-02 B축 지속] 이전 턴 soma(polyvagal/dissociation) — `Track across turns` 집행 재료.
    try:
        anchors["stored_npc_soma"] = (domain_manager.get_world_state(channel_id) or {}).get(
            "npc_soma_states", {}) or {}
    except Exception:
        anchors["stored_npc_soma"] = {}
    # [2026-08-11 사망 파이프라인] 생존축이 anchors로 안 흘러 theoria가 볼 수가 없었다.
    #   가장 싼 배선 = **비활성 이름만** 여기서 동봉(전체 status 맵은 대부분 active라 낭비).
    #   빈 dict가 정상값(전원 생존) — 소비처는 `.get(name)`으로 조용히 통과한다.
    try:
        import npc_manager as _npm_st
        anchors["npc_inactive"] = {
            _n: _npm_st.get_npc_status(_d)
            for _n, _d in (domain_manager.get_npcs(channel_id) or {}).items()
            if not _npm_st.is_npc_active(_d)
        }
    except Exception:
        anchors["npc_inactive"] = {}

    # [V10 Sprint 4] 막간 장부 (이번 턴 재구성분) — Theoria가 막간을 알아야 NPC 심리 추론 정합
    try:
        import interim_engine
        _ledger_block = interim_engine.get_last_block(channel_id)
        if _ledger_block:
            anchors["interim_ledger"] = _ledger_block
    except Exception:
        pass

    # NPC Roster (Theoria용 이름+역할 요약)
    import npc_manager as _npc_mgr
    _npc_mgr.migrate_npc_fields(channel_id)  # desc→description 통일 + 구조화 필드 자동 추출
    anchors["npc_roster"] = _npc_mgr.get_npc_roster(channel_id)

    # [2026-09-02 R4/R5] 3단 출석 앵커 — 스펙 §2.4 / §6 R4·R5.
    # 여기서 한 번만 뽑아 세 소비자에게 나눠 준다: scene_cast(R5 psyche 대상 명시),
    #   nearby_cast(1단 = "들어올 수 있다"), absent+hops(아래 offscreen 후보 정렬).
    # 앵커가 **비었으면 키를 안 넣는다** — 빈 목록을 넣으면 프롬프트에 "아무도 없다"는
    #   블록이 서는데, 위치 미기록과 부재는 다른 사실이다(§2.4 unresolved 규율과 같은 이유).
    _tiers = {}
    try:
        _tiers = _npc_mgr.get_presence_tiers(channel_id) or {}
        if not _tiers.get("unresolved"):
            if _tiers.get("scene"):
                anchors["scene_cast"] = list(_tiers.get("scene") or [])
            if _tiers.get("reachable"):
                anchors["nearby_cast"] = list(_tiers.get("reachable") or [])
    except Exception as _e_tiers:
        logger.debug(f"[presence-check] tiers anchor skip: {_e_tiers}")
        _tiers = {}

    # [2026-07-02 Offscreen Motion — 뮈토스 이식] 부재 등록 NPC 후보 → Theoria offscreen_trace 재료.
    # 장면 밖 NPC의 "세계가 턴 사이에 움직인 흔적" 공급용. 후보 6명 캡, 실패 무해.
    # [Phase 0] last_seen(emotion_log 기준)으로 부재 '기간' 동봉 — 오래 빈 인물일수록 흔적 가치↑.
    try:
        # [2026-07-28] 구 코드는 get_scene_npc_names(=로어 아닌 **전체** 명부)를 "무대 위"로 봤다.
        # 그래서 _absent_names에는 거의 로어 NPC만 남고, 정작 노리던 "한동안 안 나온 세션 NPC"는
        # 절대 후보에 못 올랐다 — 오프스크린 흔적 기능이 사실상 사문.
        # [2026-09-02 R4] 그 "무대 위의 여집합"을 **2단(absent)**으로 교체한다. 스펙 §2.4.
        #   병: 여집합은 평평하다 — 옆방 사람과 다른 도시 사람이 같은 무게로 후보에 오르고,
        #     1단(근접)이 통째로 여기 섞여 "들어올 수 있는 사람"이 부재 흔적으로 소비됐다.
        #   처방: 후보는 2단만, 정렬은 **홉 오름차순**(옆방 흔적이 다른 도시 흔적보다 먼저).
        #     같은 홉이면 현행 규칙 — 오래 안 보인 사람이 먼저(흔적 가치↑, 아래 last_seen).
        _reg_all = domain_manager.get_npcs(channel_id) or {}
        _att_map = domain_manager.get_npc_attitudes(channel_id) or {}
        _last_seen = {}
        try:
            import narrative_queries as _nq
            _last_seen = _nq.last_seen_turns(channel_id)
        except Exception:
            _last_seen = {}
        _cur_t = int(world.get("turn_index", 0) or 0)

        def _ls_lookup(name: str):
            if name in _last_seen:
                return _last_seen[name]
            _base = name.split("(")[0].strip().lower()
            for _k, _v in _last_seen.items():
                _kb = _k.split("(")[0].strip().lower()
                if _kb == _base or _base in _k.lower() or _kb in name.lower():
                    return _v
            return None

        if (not _tiers) or _tiers.get("unresolved"):
            # 폴백 — PC 노드 미해상(전환 직후·위치 미기록)이거나 tiers 조회 자체가 실패한 경우.
            # 판독기(`get_onstage_npc_names`)가 같은 조건에서 같은 레거시로 내려가므로 정합.
            _scene_set = set(_npc_mgr.get_onstage_npc_names(channel_id, within_turns=2))
            # [2026-08-11 사망 파이프라인] 생존축 필터 — 구멍 순위 2.
            #   죽은/쓰러진 인물은 정의상 **항상 부재**라 상시 offscreen_trace 후보였다
            #   (그 자리에 `likely-now=` 현재 추정 행동까지 붙었다).
            _absent_pool = [n for n, _d in _reg_all.items()
                            if n not in _scene_set and _npc_mgr.is_npc_active(_d)]
            _hops_map = {}
        else:
            _absent_pool = list(_tiers.get("absent") or [])   # 생존축 필터는 tiers가 이미 건다
            _hops_map = _tiers.get("hops") or {}
        # 정렬 키는 전부 유한·결정적이다(미배치=UNPLACED_HOPS, 미관측=-1, 마지막은 이름) —
        #   dict 반복 순서에 기대는 선택은 버그 후보라는 Pass D-2 교훈.
        _far = int(getattr(_npc_mgr, "UNPLACED_HOPS", 999))

        def _absent_sort_key(_n):
            try:
                _h = int(_hops_map.get(_n, _far))
            except (TypeError, ValueError):
                _h = _far
            try:
                _l = _ls_lookup(_n)
                _l = int(_l) if _l is not None else -1   # 미관측 = 가장 오래된 쪽으로
            except (TypeError, ValueError):
                _l = -1
            return (_h, _l, str(_n))

        _absent_names = sorted(_absent_pool, key=_absent_sort_key)[:6]
        if _absent_names:
            # [2026-07-14 고아 재배선] NPC 시간 힌트(P1 관찰/P2 스케줄, 무근거 폴백 없음)
            # — ctx.rule_txt 고아 회로에서 버려지던 것을 offscreen_trace 재료로 이송.
            # 서사 콜이 큐레이션 게이트 = 렌더러 직행 노이즈(random cast pull) 없음.
            _time_hints = {}
            try:
                import npc_manager as _npm
                for _h in (_npm.get_npc_time_progression(channel_id) or []):
                    if ":" in _h:
                        _hn, _hv = _h.split(":", 1)
                        _time_hints[_hn.strip()] = _hv.strip()
            except Exception:
                _time_hints = {}

            # [2026-07-18 고아 승격] world_tree last-known 위치 — 부재 NPC가 '마지막으로
            # 목격된 장소' 앵커 (get_npc_location 소비 0이던 것). Contract-First 오프스크린 바닥.
            _wt_loc = {}
            try:
                import world_tree as _wt
                for _an in _absent_names:
                    _lloc = _wt.get_npc_location(channel_id, _an)
                    if _lloc:
                        _wt_loc[_an] = _lloc
            except Exception:
                _wt_loc = {}

            _cand_lines = []
            for _an in _absent_names:
                _aa = _att_map.get(_an, {}) if isinstance(_att_map, dict) else {}
                _ls = _ls_lookup(_an)
                _absent_str = (f", absent_for={max(0, _cur_t - int(_ls))} turns"
                               if _ls is not None else ", absent_for=unknown")
                _th = _time_hints.get(_an) or _time_hints.get(_an.split("(")[0].strip())
                _th_str = f", likely-now={_th}" if _th else ""
                _loc_str = f", last_seen_at={_wt_loc[_an]}" if _an in _wt_loc else ""
                _cand_lines.append(
                    f"{_an}: attitude={_aa.get('attitude', 'neutral')}, depth={int(_aa.get('depth', 0) or 0)}{_absent_str}{_loc_str}{_th_str}"
                )
            anchors["offscreen_candidates"] = _cand_lines
    except Exception as _e_osc:
        logger.debug(f"[OffscreenMotion] candidates skip: {_e_osc}")

    # [Phase 0 2026-07-02] 최근 제안 비트 목록 → 반복 회피 재료 (suggested_beats가 매 턴 같은 축 반복 방지)
    try:
        import narrative_queries as _nq_rb
        _rb = _nq_rb.recent_beats(channel_id, n=10, cap=8)
        if _rb:
            anchors["recent_beats_avoid"] = _rb
    except Exception as _e_rb:
        logger.debug(f"[Phase0] recent_beats skip: {_e_rb}")

    # [2026-06-11 소비자 감사 #6 부활] P6 capability hints — Flash *입력*으로 (원설계 배달).
    # 등록 NPC 전용 (PC는 npcs 레지스트리에 없음 — NPC 제한, PC 자율성 무관).
    try:
        from waterfall_pipeline import _inject_capability_hints
        _cap = _inject_capability_hints(domain_manager.get_npcs(channel_id) or {})
        if _cap:
            anchors["capability_hints"] = _cap
    except Exception as _e_cap:
        logger.debug(f"[P6] capability hints skip: {_e_cap}")

    # Session Memory (World State Updater 피드백용)
    anchors["session_memory"] = domain_manager.get_session_ai_memory(channel_id)

    # [2026-07-15 수리] theoria가 읽는데 아무도 안 쓰던 앵커 2종 (dead_scan B: anchors READ-ONLY).
    # 07-04 entity_state / 07-15 arc 와 같은 병 — .get(k, default)라 조용히 default를 먹었다.
    #
    #  ① scene_type — theoria L76이 `anchors.get("scene_type", "normal")`로 읽어
    #     `_build_system_instruction`의 조건부 게이트에 쓴다:
    #         if scene_context.get("scene_type") == "intimate":
    #             rule_tables += SEXUAL_PSYCHOLOGY_ANALYSIS
    #     키가 없어 항상 "normal" → **SEXUAL_PSYCHOLOGY_ANALYSIS가 한 번도 로딩된 적 없음.**
    #     추출 콜은 이번 턴 SceneType을 아직 모른다(그걸 생산하는 게 이 콜) → 직전 프레임의
    #     dai_snapshot에서 가져온다. 장면은 이어지므로 1턴 지연은 감수(렌더러 쪽과 동일 성질).
    #
    #  ② turn_index — theoria L80이 `len(anchors.get("history", []))`로 turn_count를 세어
    #     `get_session_spotlight(seed, turn_number, 5, ...)`에 넘긴다. 그 안에서
    #         group_idx = turn_number % total_groups
    #     즉 turn_number가 로테이션 축인데 항상 0 → group_idx 항상 0 → **매 턴 pool[0:5]
    #     동일**. "This turn, actively apply these highlighted theories"라 써놓고 채널당
    #     같은 5개 이론이 영구 고정이었다. history 길이 대신 실 카운터(turn_index)를 준다.
    try:
        _prev_frame = domain_manager.get_latest_frame(channel_id) or {}
        _prev_dai = _prev_frame.get("dai_snapshot") or {}
        anchors["scene_type"] = str(_prev_dai.get("scene_type", "normal") or "normal")
    except Exception as _e_st:
        logger.debug("[UNE] anchors.scene_type skip: %s", _e_st)
        anchors["scene_type"] = "normal"
    try:
        anchors["turn_index"] = int(world.get("turn_index", 0) or 0)
    except (TypeError, ValueError):
        anchors["turn_index"] = 0

    #  ③ active_genres — 같은 병 6번째 (dead_scan v2.3의 narrative_anchors 별칭이 드러냄).
    #     doom_module L185가 `(context.narrative_anchors or {}).get("active_genres", [])`로
    #     읽어 C-Lens/B-Flavor를 뽑는다:
    #         lens_tags   = _extract_lens_tags(...)    → noir/comedy/romance/drama
    #         flavor_tags = _extract_flavor_tags(...)  → urban_fantasy/steampunk/...
    #         _get_doom_phase L109: primary = lens_tags[0] if lens_tags else "default"
    #     키가 없어 항상 [] → **primary 영구 "default"** → doom_chapter_volume_plan의
    #     lens×flavor×phase atmosphere 매트릭스가 통째로 미가동(起承轉結 경계가 늘 기본값).
    #     생산자·소비자 모양은 이미 일치했다(command_handler L423-432가 layers.narrative_tone에
    #     lens를, layers.style_tech에 flavor를 저장 / _extract_*가 {layers} 형식 판독).
    #     **배달만 없었다.**
    try:
        anchors["active_genres"] = domain_manager.get_active_genres(channel_id)
    except Exception as _e_ag:
        logger.debug("[UNE] anchors.active_genres skip: %s", _e_ag)
        anchors["active_genres"] = []

    # Bus initialization
    bus = SharedBus()
    bus.doom["value"] = world.get("doom", 40)
    bus.doom["carry"] = world.get("doom_carry", 0.0)  # 소수 이월 누적 (round 0증발 방지)
    # 0626: pending_doom_gain(퀘스트 완성 등 외부 raw 적립) → bus.doom delta 합류 → doom_module이 수식(phase×lens)+carry 통과. 소비 후 클리어.
    _pending_dg = world.get("pending_doom_gain", 0)
    if _pending_dg:
        bus.doom["delta"] = bus.doom.get("delta", 0) + _pending_dg
        world["pending_doom_gain"] = 0
        domain_manager.update_world_state(channel_id, world)
    # [Reader-GM Stage 3-A] 수신형 시계 후보 적립분 → bus 폴백 인테이크
    # (doom_module이 Flash 제안 부재 시 소비 — 기존 캡·중복·間 규칙 그대로 통과. 소비 후 클리어.)
    _pending_ck = world.get("pending_clock_new")
    if _pending_ck:
        bus.doom["pending_clock_new"] = _pending_ck
        world["pending_clock_new"] = None
        domain_manager.update_world_state(channel_id, world)
    # Doom clocks (local threats)
    clocks = world.get("doom_clocks", [])
    bus.doom["clocks"] = clocks if isinstance(clocks, list) else []

    # [2026-08-11 로드아웃 삭제] loadout 자동 지급 + anchors["pending_flashback"] 급식 제거.
    # (전자는 아무도 안 읽는 dict를 매 세션 심고 있었고, 후자의 생산자 !회상 명령이 폐기됨.)

    # Vigor/Composure migration: old "mental" → vigor + composure
    if "mental" in mem and "vigor" not in mem:
        old_val = mem["mental"].get("value", 100)
        old_delta = mem["mental"].get("last_delta", 0)
        mem["vigor"] = {"value": old_val, "last_delta": old_delta}
        mem["composure"] = {"value": old_val, "last_delta": 0}
        del mem["mental"]

    # [2026-08-18 Phase 2.5] 기력의 정본은 **레지스트리**(custom_var_values["기력"][user_id]).
    #   bus.vigor 는 이번 턴 읽기 사본이다 — 하류(판정 폴백·notation·cascade·스냅샷·로그)가
    #   전부 bus 를 읽으므로 여기 한 곳만 갈아끼우면 배선이 통째로 옮겨간다.
    #   레지스트리가 답을 못 주면(기능 off) 옛 자리로 폴백 — 이월 승계와 같은 계단.
    vigor_data = mem.get("vigor", {"value": 100, "last_delta": 0})
    _vigor_val = vigor_data.get("value", 100)
    try:
        import custom_vars as _cv_bus
        _reg_v = _cv_bus.get_system_value(channel_id, "기력", user_id)
        if _reg_v is not None:
            _vigor_val = _reg_v
    except Exception as _e_cvb:
        logger.debug(f"[CustomVar] 기력 레지스트리 로드 skip: {_e_cvb}")
    bus.vigor["value"] = _vigor_val
    bus.vigor["last_delta"] = vigor_data.get("last_delta", 0)
    composure_data = mem.get("composure", {"value": 100, "last_delta": 0})
    bus.composure["value"] = composure_data.get("value", 100)
    bus.composure["last_delta"] = composure_data.get("last_delta", 0)
    # stage3_turns(붕괴 dwell) 로드 제거 — 트라우마 각성 폐지 (2026-07-06)

    # 기력/평형 채널 토글 (off면 prime/process가 스킵 → 수치 동결)
    _vc_on = domain_manager.is_vigor_composure_active(channel_id)
    bus.vigor["module_active"] = _vc_on
    bus.composure["module_active"] = _vc_on

    # Momentum 로드 (이전 턴 판정 결과의 여운)
    bus.judgment["momentum_carry"] = mem.get("judgment_momentum", 0)

    context = GameContext(
        request=RequestData(
            user_input=user_input,
            genres=genres,
            active_modules=active_modules,
            lore_summary=lore_summary,
            history_text=history_text,
            lore_text=lore_text,
            lore_chunks=lore_chunks,
            lore_chunks_ranked=lore_chunks_ranked or []
        ),
        narrative_anchors=anchors,
        shared_bus=bus
    )

    return context

def sync_from_game_context(channel_id: str, user_id: str, ctx: Any) -> None:
    """[UNE Bridge] GameContext -> ParticipantData/WorldState Sync"""
    import config
    from orchestration_context import GameContext
    if isinstance(ctx, dict):
        ctx = GameContext.from_dict(ctx)
    bus = ctx.shared_bus

    # 1. World State Sync (Doom)
    if bus.doom.get("active") or isinstance(bus.doom.get("clocks"), list):
        world = domain_manager.get_world_state(channel_id)
        world["doom"] = bus.doom["value"]
        world["doom_carry"] = bus.doom.get("carry", 0.0)  # 소수 이월 영속
        if isinstance(bus.doom.get("clocks"), list):
            world["doom_clocks"] = bus.doom.get("clocks", [])
        domain_manager.update_world_state(channel_id, world)

    # 2. Participant Data Sync (Vigor, Composure)
    p_data = domain_manager.get_participant_data(channel_id, user_id)
    if p_data:
        mem = p_data.setdefault("ai_memory", {})

        # Remove legacy "mental" key if present
        mem.pop("mental", None)

        # [2026-08-18 Phase 2.5] **평형만** 여기서 영속한다. 기력의 저장처는 레지스트리로
        #   옮겨갔고 쓰기는 그쪽 관문(apply_deltas / apply_system_delta)이 전담한다 —
        #   두 자리에 같은 수를 적으면 어느 쪽이 정본인지 아무도 모르게 된다.
        #   ai_memory["vigor"] 는 손대지 않고 그대로 둔다: 이월 승계의 소스이기 때문이다.
        for axis_name in ("composure",):
            axis_bus = getattr(bus, axis_name)
            if axis_bus.get("active"):
                axis_sys = mem.setdefault(axis_name, {"value": 100, "last_delta": 0})
                axis_sys["value"] = axis_bus["value"]
                axis_sys["last_delta"] = axis_bus.get("last_delta", 0)
                # (트라우마 각성 소비 블록은 2026-07-06 폐지 — 히스토리는 감사 보고서 §E)

        # [2026-07-06 status begins/ends 재배선 — 레티어스 승인: 플레이어 표시 X, 산문 힌트만]
        # 스냅샷 diff: 직전 턴 status 이름 목록 vs 현재 → 신규=begins, 소실=ends.
        # 소스 불문(추출 add/remove·turns 만료·수동 편집) 전부 커버 — 전용 이벤트 배선 불필요.
        # sync가 Events 레이어(_extract)보다 먼저라 같은 턴 프롬프트에 실린다.
        try:
            _cur_names = [
                str(s.get("name", "")).strip()
                for s in (p_data.get("status_effects") or [])
                if isinstance(s, dict) and str(s.get("name", "")).strip()
            ]
            _prev_names = mem.get("_status_names_prev")
            if _prev_names is None:
                pass  # 콜드 스타트: 기존 status를 begins로 오인하지 않게 스냅샷만 초기화
            else:
                _new = [n for n in _cur_names if n not in _prev_names]
                _gone = [n for n in _prev_names if n not in _cur_names]
                if _new and isinstance(bus.dai, dict):
                    bus.dai.setdefault("new_status_effects", []).extend(_new)
                if _gone and isinstance(bus.dai, dict):
                    bus.dai.setdefault("expired_status_effects", []).extend(_gone)
            mem["_status_names_prev"] = _cur_names
        except Exception as _e_st:
            logger.warning("[StatusTransition] diff skipped: %s", _e_st)

        # Momentum 저장 (다음 턴 carry)
        momentum_next = bus.judgment.get("momentum_next", 0)
        if momentum_next != 0:
            mem["judgment_momentum"] = momentum_next
        else:
            mem.pop("judgment_momentum", None)

        domain_manager.save_participant_data(channel_id, user_id, p_data)

class UniversalNarrativeEngine:
    def __init__(self, client, model_id: str):
        self.pipeline = WaterfallPipeline(client, model_id)

    async def run(self, channel_id: str, user_id: str, user_input: str, lore_chunks_ranked: list = None) -> Dict[str, Any]:
        """단일 PC 행동 처리 (솔로/자동 모드용)"""
        turn_index = game_world.increment_turn_index(channel_id)
        game_character.process_status_expiry(channel_id, user_id, turn_index)
        # [2026-07-02 감사 노트] expiry 반환값(만료 이름 리스트)을 bus.dai["expired_status_effects"]에
        # 실으면 Events 레이어 "ends" 알림이 산문에 뜬다(현재 기아 소비자). 트라우마 각성 존폐 결정
        # 대기로 배선 보류 — 원하면 여기 1줄.
        context = convert_to_game_context(channel_id, user_id, user_input, lore_chunks_ranked=lore_chunks_ranked)
        p_data = domain_manager.get_participant_data(channel_id, user_id)
        mask = p_data.get("mask") if p_data else "PC"

        updated_context = await self.pipeline.execute(context)
        sync_from_game_context(channel_id, user_id, updated_context)

        result = self._extract_pc_result_v3(updated_context, mask)
        return {
            "game_context": updated_context,
            "directive": result["directive"],
            "system_message": result["system_msg"]
        }

    async def run_batch(self, channel_id: str, pending_actions: Dict[str, Dict]) -> Dict[str, Any]:
        """다인 동시 행동 처리. pending_actions = {uid: {"mask":str, "actions":[str]}}"""
        all_results = []
        anomaly_data = None
        last_context = None
        turn_index = game_world.increment_turn_index(channel_id)

        for uid, info in pending_actions.items():
            game_character.process_status_expiry(channel_id, uid, turn_index)
            combined_input = "\n".join(info["actions"])
            context = convert_to_game_context(channel_id, uid, combined_input)

            # 스토리텔러 중복 방지: 이미 발동했으면 동일 이벤트 데이터 복사
            if anomaly_data:
                context.shared_bus.anomaly["_skip_storyteller"] = True
                context.shared_bus.anomaly.update(anomaly_data)

            updated = await self.pipeline.execute(context)

            # 이변 정보 보존 (첫 발동분)
            if updated.shared_bus.anomaly.get("triggered") and not anomaly_data:
                anomaly_data = {
                    "tag": updated.shared_bus.anomaly.get("tag"),
                    "intensity": updated.shared_bus.anomaly.get("intensity"),
                    "polarity": updated.shared_bus.anomaly.get("polarity"),
                    "category": updated.shared_bus.anomaly.get("category"),
                    "potential": True,
                }

            sync_from_game_context(channel_id, uid, updated)
            all_results.append(self._extract_pc_result_v3(updated, info["mask"]))
            last_context = updated

        return self._combine_batch_results_v3(all_results, last_context)

    async def run_observation(self, channel_id: str) -> Dict[str, Any]:
        """관찰 모드: PC 행동 없이 세계 묘사"""
        turn_index = game_world.increment_turn_index(channel_id)
        participants = domain_manager.get_domain(channel_id).get("participants", {})
        base_uid = None
        for uid, p in participants.items():
            if p.get("status") == "active":
                base_uid = uid
                break

        if not base_uid:
            return {"game_context": None, "directive": "", "system_message": ""}

        for uid, pdata in participants.items():
            if pdata.get("status") == "active":
                game_character.process_status_expiry(channel_id, uid, turn_index)

        observation_input = "[observation mode — watching the surroundings without taking direct action]"
        context = convert_to_game_context(channel_id, base_uid, observation_input)

        # 판정 비활성화 (관찰은 행동이 아님)
        context.shared_bus.judgment["active"] = False

        updated = await self.pipeline.execute(context)
        sync_from_game_context(channel_id, base_uid, updated)

        result = self._extract_pc_result_v3(updated, "")
        return {
            "game_context": updated,
            "directive": "[observation mode] the world and its NPCs go about their natural activity; the PC takes no action.\n" + result["directive"],
            "system_message": result["system_msg"]
        }

    def _evaluate_npc_autonomy(self, bus, context) -> str:
        """Evaluate NPC autonomous behavior triggers and store results in bus.

        Merges Helena metrics (depth/tension) from domain storage into DAI attitudes,
        runs NPCAutonomousEngine trigger evaluation, and returns the directive string.
        """
        if not (bus.dai and bus.dai.get("psyche_states")):
            return ""

        from npc_autonomous import NPCAutonomousEngine

        # Helena metrics(depth/tension) merge: domain 영속 데이터 → DAI attitudes
        _dai_att = bus.dai.get("npc_attitudes") or {}  # [07-27] 명시 null 방어
        _stored_att = (context.narrative_anchors or {}).get("stored_npc_attitudes", {})
        _merged_att = {}
        for _n, _a in _dai_att.items():
            _m = dict(_a) if isinstance(_a, dict) else {}
            # DAI 이름 → 저장 키 해상도 (e.g. "이하윤" → "Lee Ha-yoon(이하윤)")
            _resolved = _n
            for _sk in _stored_att:
                if _sk == _n:
                    _resolved = _sk
                    break
                _sk_base = _sk.split("(")[0].strip().lower() if "(" in _sk else _sk.lower()
                if _sk_base == _n.strip().lower() or _n.strip().lower() in _sk.lower():
                    _resolved = _sk
                    break
            _s = _stored_att.get(_resolved, {})
            if isinstance(_s, dict):
                _m.setdefault("depth", _s.get("depth", 0))
                _m.setdefault("tension", _s.get("tension", 0))
            _merged_att[_n] = _m

        # N6: Gather static traits for desistance gate enrichment
        import npc_manager as _npc_mgr
        _cd_channel = (context.narrative_anchors or {}).get("channel_id", "")
        _static_traits_map = {}
        if _cd_channel:
            for _npc_name in bus.dai.get("psyche_states", {}):
                _st = _npc_mgr.get_npc_static_traits(_cd_channel, _npc_name)
                if _st:
                    _static_traits_map[_npc_name] = _st

        triggers = NPCAutonomousEngine.evaluate_triggers(
            psyche_states=bus.dai.get("psyche_states", {}),
            npc_knowledge=bus.dai.get("npc_knowledge", {}),
            npc_attitudes=_merged_att,
            scene_type=bus.dai.get("scene_type", "normal"),
            static_traits_map=_static_traits_map,
        )

        # M2: Filter out triggers for NPCs on decision cooldown
        # [2026-06-10 Fix] + 미등록 이름(PC 등) 트리거 제외 — DAI psyche에 PC가 섞여 들어와
        # PC 이름으로 자율 지시문/쿨다운이 생성되던 경로 차단 (Contract-First: 미등록 NPC엔 트리거 X).
        # 부수효과: 매 턴 찍히던 [DecisionCooldown] not found 경고 소거. 신규 NPC는 이번 턴
        # 후처리에서 자동 등록되므로 다음 턴부터 정상 평가됨 (1턴 지연은 의도적 보수).
        if _cd_channel and triggers:
            _filtered = []
            for t in triggers:
                if _npc_mgr.get_npc(_cd_channel, t.npc_name) is None:
                    logger.debug(f"[NPC Autonomy] '{t.npc_name}' 미등록(PC 추정) — trigger '{t.trigger_id}' 제외")
                    continue
                # [2026-07-28] 쿨다운은 **중대 결정(priority>=4)에만** 적용한다.
                # 구 코드는 우선순위를 안 보고 그 NPC의 **모든 트리거**를 막아서,
                # 한 번 중대 결정이 나면 이후 2~3턴간 emotional_contagion(1)·agenda_manifest(2)
                # 같은 사소한 반응까지 전부 차단 = "결정 페이싱"이 아니라 "자율성 정지"였다.
                # (쿨다운을 거는 쪽도 priority>=4 기준이므로 대칭이 맞다 — 아래 set_decision_cooldown.)
                cd = _npc_mgr.check_decision_cooldown(_cd_channel, t.npc_name)
                if cd > 0 and t.priority >= 4:
                    logger.debug(f"[NPC Autonomy] {t.npc_name} trigger '{t.trigger_id}' suppressed — cooldown {cd} turns remaining")
                else:
                    _filtered.append(t)
            triggers = _filtered

        # [V10 적립] autonomy_log — 자율 트리거 발동 기록(npc/trigger/priority/directive).
        # 대사·관계 압력의 출처. dai_logs는 이 시점 이전에 써져서 안 잡힘 → 전용 적립. 실패 무해.
        if _cd_channel and triggers:
            try:
                import sqlite_store as _sq_auto
                _auto_turn = int(_to_float((bus.dai or {}).get("turn_index", 0), 0))
                _sq_auto.append_autonomy_log(_cd_channel, _auto_turn, [
                    {"npc_name": t.npc_name, "trigger_id": t.trigger_id,
                     "priority": t.priority, "directive": t.directive}
                    for t in triggers
                ])
            except Exception as _e_autolog:
                logger.debug(f"[AutonomyLog] skip: {_e_autolog}")

        auto_directive = NPCAutonomousEngine.build_autonomous_directive(triggers)
        # iceberg per-NPC depth 계산용 구조 데이터 저장
        if triggers:
            bus.dai["autonomous_triggers"] = [
                {"npc_name": t.npc_name, "trigger_id": t.trigger_id, "priority": t.priority}
                for t in triggers
            ]
            # M2: 고우선순위 트리거(중대 결정) 발동 시 쿨다운 설정
            if _cd_channel:
                for t in triggers:
                    if t.priority >= 4:
                        _npc_mgr.set_decision_cooldown(_cd_channel, t.npc_name, turns=3)
        return auto_directive or ""

    def _extract_pc_result_v3(self, context, mask: str) -> Dict[str, Any]:
        """Build directive-layer v3: World -> Events -> Judgment -> Atmosphere."""
        bus = context.shared_bus
        layers: List[str] = []

        world = _build_world_layer(bus)
        if world:
            layers.append(world)

        events = _build_events_layer(context, bus)
        if events:
            layers.append(events)

        effective_mask = mask or context.get_acting_mask()
        judgment = _build_judgment_layer(bus, effective_mask)
        if judgment:
            layers.append(judgment)

        atmosphere = _build_atmosphere_layer(context, bus)
        if atmosphere:
            layers.append(atmosphere)

        # Aspects (시스템 교차 결합 신호) — 라벨 내부 식별자, 산문 typological.
        aspects_layer = _build_aspects_layer(context, bus)
        if aspects_layer:
            layers.append(aspects_layer)

        # Preserve autonomous NPC behavior directive after core v3 layers.
        auto_directive = self._evaluate_npc_autonomy(bus, context)
        if auto_directive:
            layers.append(auto_directive)

        return {
            "directive": "\n\n".join(part for part in layers if part).strip(),
            "system_msg": _build_system_message(bus),
            "has_anomaly": bool(bus.anomaly and bus.anomaly.get("triggered")),
            "anomaly_header": "",
            # [2026-08-11 비일상적응도 삭제] adaptation_line 키 제거 — 항상 "" + 소비자 0
            "mental_log": bus.vigor.get("log", "") if bus.vigor else "",
        }

    def _combine_batch_results_v3(self, results: list, last_context) -> Dict[str, Any]:
        """Merge multi-PC directives and system logs for batch processing."""
        all_directives: List[str] = []
        system_chunks: List[str] = []
        seen_sys = set()

        for result in results:
            directive = (result.get("directive") or "").strip()
            if directive:
                all_directives.append(directive)

            sys_msg = (result.get("system_msg") or "").strip()
            if sys_msg and sys_msg not in seen_sys:
                seen_sys.add(sys_msg)
                system_chunks.append(sys_msg)

        return {
            "game_context": last_context,
            "directive": "\n\n".join(all_directives).strip(),
            "system_message": "\n\n".join(system_chunks).strip(),
        }

