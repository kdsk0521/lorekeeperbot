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
        ("desperate", "failure"): "Make the threat real - irreversible consequences.",
        ("desperate", "partial"): "Heavy price - gain what was sought but lose something.",
        ("desperate", "success"): "Dramatic turnaround - shining in the direst moment.",
        ("desperate", "critical_success"): "Miraculous reversal - transcendent moment.",
        ("risky", "critical_failure"): "Worst case unfolds - danger becomes reality.",
        ("risky", "failure"): "Escalate - a new danger reveals itself.",
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
_DOOM_NOTATION = {
    "high":     "world | ♪ f, allegro, marcato | ▶ wide, facing | ◎ interval",
    "critical": "world | ♪ ff, presto, sforzando | ▶ low-angle, jump-cut | ◎ slow-motion",
}
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
    energy = str(dai.get("energy_direction", "steady"))
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
        parts.append(f"'{action_name}' 시도 — {difficulty}")
        if not needs_judgment:
            parts.append(f"판정 없음. 상황({pos_tier})과 세계 논리로 해결.")

    if not parts:
        return ""
    return "── 세계 ──\n" + "\n".join(parts)


def _build_events_layer(context, bus) -> str:
    parts: List[str] = []

    # World Event → fact only (metadata stripped)
    anomaly = bus.anomaly if isinstance(bus.anomaly, dict) else {}
    if anomaly.get("triggered"):
        line = anomaly.get("line", "")
        if line:
            parts.append(f"세계 사건: {line}")

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
                    f"현재 상황: {_desc} — "
                    "환경, NPC 행동, 가용 행동에 반영하라."
                )

    # Omen → tag stripped
    _omen = anomaly.get("omen")
    if isinstance(_omen, dict) and _omen.get("tag"):
        _omen_line = _omen.get("line", "")
        if _omen_line:
            parts.append(
                f"전조: {_omen_line} — "
                "감각적 디테일로만 암시하라. 이벤트 발동 금지."
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
                    f"{cname}이 현실이 되었다 — {threat}. "
                    "변화된 세계를 보여주라. 시점 전환 금지."
                )

    # Climax → fact
    if doom.get("climax_triggered"):
        parts.append("임계점 — 모든 것이 동시에 수렴했다. 최후의 선택.")

    # Defense → REMOVED (mechanical log)

    # Quest Failed → fact
    for fail in doom.get("quest_failed", []):
        if isinstance(fail, dict):
            parts.append(f"{fail.get('quest', '?')}가 실패했다 — {fail.get('reason', '')}")

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
            parts.append(" | ".join(f"{n}이 곧 현실이 된다" for n in imminent[:3]))

    # Status effects → tag stripped
    status_seen = set()
    for container in (bus.vigor, bus.composure, bus.dai):
        if not isinstance(container, dict):
            continue
        for key, label_kr in (("new_status_effects", "시작"), ("expired_status_effects", "종료")):
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
                line = f"{name} {label_kr}"
                if hint:
                    line += f" — {hint}"
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
            parts.append(f"소급 선언: {declaration}")

    # Quest Echo → tag stripped
    channel_id = (context.narrative_anchors or {}).get("channel_id", "")
    if channel_id:
        try:
            import game_character
            active_quests = game_character.get_active_quests_raw(channel_id)
            turn_index = int(_to_float((bus.dai or {}).get("turn_index", 0), 0))
            for q in active_quests:
                if not isinstance(q, dict):
                    continue
                last_progress_turn = q.get("last_progress_turn", 0)
                stale_turns = turn_index - last_progress_turn
                if stale_turns >= 8:
                    parts.append(
                        f"{q.get('content', '?')} — "
                        "유저가 이 방향으로 행동할 때만 반영. 강제 진전 금지."
                    )
        except Exception:
            pass

    # Downtime activity (from dead code migration)
    _dai_dt = bus.dai if isinstance(bus.dai, dict) else {}
    _dt_rest = _dai_dt.get("rest_eval") or {}
    _dt_activity = _dt_rest.get("activity", "rest")
    if _dt_activity != "rest" and _dt_rest.get("detected"):
        _dt_hints = {
            "recover": "PC가 부상을 치료하며 시간을 보낸다.",
            "vice": "PC가 쾌락에 빠져 시간을 보낸다.",
            "train": "PC가 훈련에 몰두한다.",
            "socialize": "PC가 사람들과 교류하며 시간을 보낸다.",
            "project": "PC가 작업에 집중한다.",
        }
        parts.append(_dt_hints.get(_dt_activity, "PC가 목적 있는 시간을 보낸다."))
        if _dai_dt.get("vice_overindulge"):
            parts.append("쾌락의 대가가 돌아왔다.")

    if not parts:
        return ""
    return "── 사건 ──\n" + "\n".join(parts)


# Universal narrative principles — separated from MC Moves (genre flavor)
CONSEQUENCE_DIRECTIVES = {
    "critical_success": (
        "Show the PC achieving MORE than they intended. "
        "A new possibility opens, or the PC seizes decisive control of the situation. "
        "If NPCs are present, show how this success positively shifts the relationship."
    ),
    "partial": (
        "The PC's intent is achieved, but ONE unwanted change necessarily follows. "
        "Concretely depict what was lost, exposed, or complicated. "
        "A partial success with zero cost is FORBIDDEN. "
        "If NPCs are present, let this cost leave a subtle mark on the relationship."
    ),
    "failure": (
        "The PC's intent is NOT achieved. "
        "The situation is now different from before the attempt — do NOT simply say 'it didn't work.' "
        "Show what has changed. "
        "If NPCs are present, show their reaction to witnessing this failure."
    ),
    "critical_failure": (
        "An IRREVERSIBLE change occurs in this scene. "
        "The opposite of the PC's intent is realized, or an unforeseen new reality is revealed. "
        "The world after this moment is different from the world before. "
        "If NPCs are present, this catastrophe fundamentally shakes the relationship."
    ),
}


def _build_judgment_layer(bus, mask: str) -> str:
    judgment = bus.judgment if isinstance(bus.judgment, dict) else {}
    if not judgment.get("active"):
        return ""

    meta = judgment.get("meta", {}) if isinstance(judgment.get("meta"), dict) else {}
    action = str(meta.get("action", "행동"))
    result = str(judgment.get("result", "failure"))
    reason = str(judgment.get("reason", "")).strip()

    dai = bus.dai if isinstance(bus.dai, dict) else {}
    pos_value = _to_float((dai.get("position", {}) or {}).get("value", 0.5), 0.5)
    pos_tier = _position_tier(pos_value)

    move = _mc_move(pos_tier, result)

    favorable, against = _collect_aspect_stance(dai.get("aspects", []))

    # Natural language — no tags, no framework labels
    _result_kr = {
        "critical_success": "대성공", "success": "성공",
        "partial": "부분 성공", "failure": "실패", "critical_failure": "대실패",
    }
    _pos_kr = {"controlled": "안전한 상황", "risky": "위험한 상황", "desperate": "절망적 상황"}
    reason_part = f" {reason}" if reason else ""
    lines = [
        f"{mask}가 '{action}'를 시도했다.{reason_part}",
        f"{_result_kr.get(result, result)} — {_pos_kr.get(pos_tier, pos_tier)}.",
        move,
    ]
    if favorable:
        lines.append("유리: " + ", ".join(favorable))
    if against:
        lines.append("불리: " + ", ".join(against))

    # 범용 서사 원칙 (장르 불문) — tag stripped
    cons_dir = CONSEQUENCE_DIRECTIVES.get(result, "")
    if cons_dir:
        lines.append(cons_dir)

    # Effort / Absorb (각오 / 흡수) — from dead code migration
    effort_used = judgment.get("effort_used")
    if effort_used:
        eu_action = effort_used.get("action", "")
        if judgment.get("absorb_applied"):
            lines.append(f"PC가 {eu_action}에 전력을 다했으나 실패. 각오 덕에 최악은 면했다.")
        elif result in ("failure", "critical_failure"):
            lines.append(f"PC가 {eu_action}에 모든 걸 걸었지만 실패. 대가만큼 최악은 면했다.")
        else:
            lines.append(f"PC가 {eu_action}에 대가를 치렀고 그만한 가치가 있었다.")
    elif isinstance(bus.dai, dict) and bus.dai.get("effort_failed"):
        lines.append("PC가 각오했지만 몸이 따르지 않았다.")

    return "\n".join(lines)


def _build_atmosphere_layer(context, bus) -> str:
    parts: List[str] = []

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
        parts.append("주변 인물들이 PC의 불안정을 감지한다 — 걱정, 회피, 또는 이용.")
    if vigor_val <= 14:
        parts.append("주변 인물들이 PC의 물리적 한계를 목격한다.")

    # Doom/Tension → directing notation (50+ only)
    doom_val = int(_to_float((bus.doom or {}).get("value", 0), 0))
    if doom_val >= 80:
        parts.append(_DOOM_NOTATION["critical"])
    elif doom_val >= 50:
        parts.append(_DOOM_NOTATION["high"])

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
        parts.append("기존 긴장을 해결하지 마라. 씨앗만 심어라.")
    elif doom_val >= 80:
        parts.append("해결은 PC 행동으로만 가능. 편의적 탈출구 금지.")

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
            parts.append(f"멀리서 {_c.get('tag', '?')}의 기운이 느껴진다")
    # [World Conditions] → REMOVED (Events Active와 중복)

    # 서사 공간 → tag stripped
    narrative_space = int(_to_float((bus.doom or {}).get("narrative_space", 0), 0))
    if narrative_space > 0:
        if narrative_space >= 15:
            intensity = "넓은"
        elif narrative_space >= 8:
            intensity = "적당한"
        else:
            intensity = "작은"
        mechanic = context.request.genres.get("mechanic", {})
        primary_res = mechanic.get("primary_resource") or "vigor"
        if primary_res == "vigor":
            parts.append(
                f"{intensity} 서사 공간 — 긴장이 풀렸다. 이 여유를 써라:\n"
                "- 캐릭터 관계 심화 (대화, 감정 교류, 유대 확인)\n"
                "- 유저 행동에 대한 세계의 반응과 되새김\n"
                "- 다음 위기의 복선을 자연스럽게 배치"
            )
        else:
            parts.append(
                f"{intensity} 서사 공간 — 일상이 돌아왔다. 이 여유를 써라:\n"
                "- 인물 간 관계 심화 (소소한 대화, 감정 교류)\n"
                "- 유저의 선택이 주변에 미친 영향 묘사\n"
                "- 새로운 변화의 씨앗을 자연스럽게 배치"
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
                    parts.append(f"{threat}의 전조가 뚜렷하다 — PC 주변에서 구체적으로 묘사하라. 시점 전환 금지.")
                else:
                    parts.append(f"{threat}의 전조가 감지된다 — 주변 인물의 태도 변화, 미묘한 분위기로 묘사하라. 시점 전환 금지.")
            elif ratio >= 0.5:
                if primary_res == "vigor":
                    parts.append(f"{threat}의 징후를 감각적 디테일로 암시하라.")
                else:
                    parts.append(f"{threat}의 전조를 일상의 작은 어긋남으로 암시하라.")

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
                "시계 이벤트는 PC 시점 안에서만 묘사하라. "
                "'한편', '그 무렵', '다른 곳에서는' 등 시점 전환 절대 금지. "
                "PC가 직접 목격·감지하는 것만 서술."
            )

    if not parts:
        return ""
    return "── 분위기 ──\n" + "\n".join(parts)


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
    for key in ("relief_log", "mental_pressure_log", "clock_log", "log"):
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

def convert_to_game_context(channel_id: str, user_id: str, user_input: str) -> GameContext:
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
        "inventory": mem.get("inventory", []),
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
                "vigor_value": pmem.get("vigor", pmem.get("mental", {})).get("value", 100),
                "composure_value": pmem.get("composure", {}).get("value", 100),
            }
    anchors["all_pcs"] = all_pcs
    anchors["acting_user_id"] = user_id

    # NPC Knowledge & Attitudes (피드백용)
    anchors["stored_npc_knowledge"] = domain_manager.get_npc_knowledge(channel_id)
    anchors["stored_npc_attitudes"] = domain_manager.get_npc_attitudes(channel_id)

    # NPC Roster (Theoria용 이름+역할 요약)
    import npc_manager as _npc_mgr
    _npc_mgr.migrate_npc_fields(channel_id)  # desc→description 통일 + 구조화 필드 자동 추출
    anchors["npc_roster"] = _npc_mgr.get_npc_roster(channel_id)

    # Session Memory (World State Updater 피드백용)
    anchors["session_memory"] = domain_manager.get_session_ai_memory(channel_id)

    # Pending Flashback (회상 대기)
    anchors["pending_flashback"] = domain_manager.get_pending_flashback(channel_id)

    # Bus initialization
    bus = SharedBus()
    bus.doom["value"] = world.get("doom", 40)
    # Doom clocks (local threats)
    clocks = world.get("doom_clocks", [])
    bus.doom["clocks"] = clocks if isinstance(clocks, list) else []

    # Vigor/Composure migration: old "mental" → vigor + composure
    if "mental" in mem and "vigor" not in mem:
        old_val = mem["mental"].get("value", 100)
        old_delta = mem["mental"].get("last_delta", 0)
        mem["vigor"] = {"value": old_val, "last_delta": old_delta}
        mem["composure"] = {"value": old_val, "last_delta": 0}
        del mem["mental"]

    vigor_data = mem.get("vigor", {"value": 100, "last_delta": 0})
    bus.vigor["value"] = vigor_data.get("value", 100)
    bus.vigor["last_delta"] = vigor_data.get("last_delta", 0)
    composure_data = mem.get("composure", {"value": 100, "last_delta": 0})
    bus.composure["value"] = composure_data.get("value", 100)
    bus.composure["last_delta"] = composure_data.get("last_delta", 0)

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
            lore_chunks=lore_chunks
        ),
        narrative_anchors=anchors,
        shared_bus=bus
    )

    return context

def sync_from_game_context(channel_id: str, user_id: str, ctx: Any) -> None:
    """[UNE Bridge] GameContext -> ParticipantData/WorldState Sync"""
    from orchestration_context import GameContext
    if isinstance(ctx, dict):
        ctx = GameContext.from_dict(ctx)
    bus = ctx.shared_bus

    # 1. World State Sync (Doom)
    if bus.doom.get("active") or isinstance(bus.doom.get("clocks"), list):
        world = domain_manager.get_world_state(channel_id)
        world["doom"] = bus.doom["value"]
        if isinstance(bus.doom.get("clocks"), list):
            world["doom_clocks"] = bus.doom.get("clocks", [])
        domain_manager.update_world_state(channel_id, world)

    # 2. Participant Data Sync (Vigor, Composure, Adaptation)
    p_data = domain_manager.get_participant_data(channel_id, user_id)
    if p_data:
        mem = p_data.setdefault("ai_memory", {})

        # Remove legacy "mental" key if present
        mem.pop("mental", None)

        for axis_name in ("vigor", "composure"):
            axis_bus = getattr(bus, axis_name)
            if axis_bus.get("active"):
                axis_sys = mem.setdefault(axis_name, {"value": 100, "last_delta": 0})
                axis_sys["value"] = axis_bus["value"]
                axis_sys["last_delta"] = axis_bus.get("last_delta", 0)

                # Trauma Trigger (사용 후 pop — 다음 턴 중복 방지)
                if axis_bus.get("trauma_trigger"):
                    passives = mem.setdefault("passives", [])
                    trauma_name = f"트라우마 ({axis_name} 각성)"
                    if not any(p.get("name") == trauma_name for p in passives if isinstance(p, dict)):
                        label = "기력" if axis_name == "vigor" else "평정"
                        passives.append({
                            "name": trauma_name,
                            "tags": ["Trauma", "Hard-to-cure"],
                            "modifier": -5,
                            "desc": f"{label} 붕괴에서 깨어난 트라우마입니다. 모든 판정에 -5 패널티를 받습니다."
                        })
                    axis_bus.pop("trauma_trigger", None)

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

    async def run(self, channel_id: str, user_id: str, user_input: str) -> Dict[str, Any]:
        """단일 PC 행동 처리 (솔로/자동 모드용)"""
        turn_index = game_world.increment_turn_index(channel_id)
        game_character.process_status_expiry(channel_id, user_id, turn_index)
        context = convert_to_game_context(channel_id, user_id, user_input)
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

        observation_input = "[관찰 모드 — 직접적인 행동 없이 주변을 지켜본다]"
        context = convert_to_game_context(channel_id, base_uid, observation_input)

        # 판정 비활성화 (관찰은 행동이 아님)
        context.shared_bus.judgment["active"] = False

        updated = await self.pipeline.execute(context)
        sync_from_game_context(channel_id, base_uid, updated)

        result = self._extract_pc_result_v3(updated, "")
        return {
            "game_context": updated,
            "directive": "[관찰 모드] 세계와 NPC의 자연스러운 활동을 묘사하라. PC의 행동은 없다.\n" + result["directive"],
            "system_message": result["system_msg"]
        }

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

        # Preserve autonomous NPC behavior directive after core v3 layers.
        if bus.dai and bus.dai.get("psyche_states"):
            from npc_autonomous import NPCAutonomousEngine

            # Helena metrics(depth/tension) merge: domain 영속 데이터 → DAI attitudes
            _dai_att = bus.dai.get("npc_attitudes", {})
            _stored_att = (context.narrative_anchors or {}).get("stored_npc_attitudes", {})
            _merged_att = {}
            for _n, _a in _dai_att.items():
                _m = dict(_a) if isinstance(_a, dict) else {}
                _s = _stored_att.get(_n, {})
                if isinstance(_s, dict):
                    _m.setdefault("depth", _s.get("depth", 0))
                    _m.setdefault("tension", _s.get("tension", 0))
                _merged_att[_n] = _m

            triggers = NPCAutonomousEngine.evaluate_triggers(
                psyche_states=bus.dai.get("psyche_states", {}),
                npc_knowledge=bus.dai.get("npc_knowledge", {}),
                npc_attitudes=_merged_att,
                scene_type=bus.dai.get("scene_type", "normal"),
            )
            auto_directive = NPCAutonomousEngine.build_autonomous_directive(triggers)
            if auto_directive:
                layers.append(auto_directive)
            # iceberg per-NPC depth 계산용 구조 데이터 저장
            if triggers:
                bus.dai["autonomous_triggers"] = [
                    {"npc_name": t.npc_name, "trigger_id": t.trigger_id, "priority": t.priority}
                    for t in triggers
                ]

        fallback_msg = self.pipeline.get_fallback_directives(context.request.active_modules)
        if fallback_msg:
            layers.append(f"[Module Constraints]\n{fallback_msg}")

        return {
            "directive": "\n\n".join(part for part in layers if part).strip(),
            "system_msg": _build_system_message(bus),
            "has_anomaly": bool(bus.anomaly and bus.anomaly.get("triggered")),
            "anomaly_header": "",
            "adaptation_line": "",
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

    def _extract_pc_result(self, context, mask: str) -> Dict[str, Any]:
        """Extract 5-Layer Directive + system_msg from single PC pipeline result.

        Layer 0: [Base Directive] — DAI soft hints (when Judgment OFF)
        Layer 1: [Narrative] — FitD Position + PbtA MC Move (Judgment result)
        Layer 2: [Aspects] — Fate Aspect declaration (cross-module interaction)
        Layer 3: [Intrusion] — Cypher GM Intrusion (Anomaly event)
        Layer 4: [Atmosphere] — Doom Clock progress + Vigor state
        """
        bus = context.shared_bus
        directive_parts = []
        system_msg = ""
        result_line = ""

        doom_val = bus.doom.get("value", 0) if bus.doom else 0
        vigor_val = bus.vigor.get("value", 100) if bus.vigor else 100
        composure_val = bus.composure.get("value", 100) if bus.composure else 100

        # ── Layer 1: [Narrative] — Position + MC Move ──
        j_active = bus.judgment and bus.judgment.get("active")
        j_result = ""
        if j_active:
            j_mask = bus.judgment.get("mask", mask)
            meta = bus.judgment.get("meta", {})
            action = meta.get("action", "행동")
            j_result = bus.judgment.get("result", "failure")
            reason_txt = bus.judgment.get("reason", "")

            # Position from Theoria (FitD)
            pos_val = bus.dai.get("position", {}).get("value", 0.5) if bus.dai else 0.5
            if pos_val <= 0.25:
                pos_tier = "desperate"
            elif pos_val <= 0.5:
                pos_tier = "risky"
            else:
                pos_tier = "controlled"

            # MC Move (PbtA): generic matrix
            move = _mc_move(pos_tier, j_result)
            reason_part = f" ({reason_txt})" if reason_txt else ""
            directive_parts.append(
                f"[Narrative: {j_mask} '{action}'{reason_part}] {move}\n"
                f"(Render outcome through scene events. Never echo move descriptions or tier names in prose.)"
            )
            system_msg += bus.judgment.get("output", "")
            if bus.judgment.get("party_wide_hook"):
                system_msg += "\n⚠️ **[전체 파티 영향]** — 이 결과는 모든 동료에게 영향을 미칩니다."

        # ── Layer 0: [Base Directive] — DAI soft hints (Judgment OFF) ──
        if not j_active and bus.dai and bus.dai.get("active"):
            hints = []

            # Genre scene hint
            mechanic = context.request.genres.get("mechanic", {})
            primary_genre = mechanic.get("primary_lens", "")

            genre_scene_hints = {
                "cosmic_horror": "Genre: Cosmic Horror — dread builds from the unseen and unknowable",
                "romance": "Genre: Romance — emotional resonance and interpersonal nuance matter most",
                "comedy": "Genre: Comedy — timing, escalation, and social absurdity drive the scene",
                "noir": "Genre: Noir — shadows hide truth, trust is currency, everyone has angles",
                "action": "Genre: Action — momentum, physical stakes, and tactical decisions",
                "slice_of_life": "Genre: Slice of Life — quiet moments carry meaning, change is gradual",
            }
            if primary_genre in genre_scene_hints:
                hints.append(genre_scene_hints[primary_genre])

            # Position → narrative tone
            pos_data = bus.dai.get("position", {})
            pos_val = pos_data.get("value", 0.5)
            if pos_val <= 0.25:
                hints.append("Position: Desperate — stakes are lethal, consequences loom")
            elif pos_val <= 0.5:
                hints.append("Position: Risky — danger present, outcome uncertain")
            else:
                hints.append("Position: Controlled — situation favors the actor")

            # SceneType → scene-specific guidance
            scene_type = bus.dai.get("scene_type", "normal")
            scene_hints = {
                "combat": "Combat scene: emphasize physicality, positioning, and threat",
                "tension": "Tension scene: build suspense, restrict information flow",
                "intimate": "Intimate scene: focus on emotion, subtlety, and vulnerability",
                "exploration": "Exploration scene: reward curiosity, reveal the world",
                "social": "Social scene: weigh reputation, leverage, and hidden agendas",
            }
            if scene_type in scene_hints:
                hints.append(scene_hints[scene_type])

            # EnergyDirection → pacing
            energy = bus.dai.get("energy_direction", "steady")
            energy_hints = {
                "rising": "Energy rising — escalate tension, accelerate pacing",
                "falling": "Energy falling — allow breathing room, reflect on aftermath",
                "peak": "Energy at peak — climactic moment, maximum intensity",
                "steady": "Energy steady — maintain current rhythm",
            }
            if energy in energy_hints:
                hints.append(energy_hints[energy])

            # needs_judgment=True but module OFF → soft probability hint
            if bus.dai.get("needs_judgment"):
                action_meta = bus.dai.get("action_meta", {})
                action_name = action_meta.get("action", "")
                if action_name:
                    hints.append(f"Action '{action_name}' attempted — judge outcome by situational probability, no dice")
                else:
                    hints.append("Meaningful action attempted — judge outcome by situational probability, no dice")

            if hints:
                directive_parts.append("[Base Directive]\n" + "\n".join(hints))

        # ── Layer 3: [Intrusion] — World Initiative (Genre-Aware) ──
        anomaly_sys = ""
        a_triggered = bus.anomaly and bus.anomaly.get("triggered")
        if a_triggered:
            tag = bus.anomaly.get("tag") or "이변"
            intensity = bus.anomaly.get("intensity")
            polarity = bus.anomaly.get("polarity")
            line = bus.anomaly.get("line", "")

            # Resolve genre for framing
            mechanic = context.request.genres.get("mechanic", {})
            intrusion_genre = mechanic.get("primary_lens", "")

            # Genre-specific anomaly framing
            genre_frames = {
                "cosmic_horror": {"positive": "a glimpse of forbidden understanding", "negative": "the veil thins — reality distorts", "mixed": "revelation wrapped in dread"},
                "romance": {"positive": "a fateful encounter or revelation", "negative": "emotional disruption — hearts shaken", "mixed": "a moment that changes everything"},
                "comedy": {"positive": "absurd luck — things go impossibly right", "negative": "comedic disaster — everything that can go wrong does", "mixed": "the situation escalates hilariously"},
                "noir": {"positive": "an unexpected card to play", "negative": "the net tightens — exposure looms", "mixed": "a new piece enters the game"},
                "action": {"positive": "tactical advantage appears", "negative": "the battlefield shifts against you", "mixed": "chaos reshapes the fight"},
                "slice_of_life": {"positive": "a pleasant surprise in the routine", "negative": "the familiar becomes uncomfortable", "mixed": "change ripples through daily life"},
            }
            default_frame = {"positive": "may serve as opportunity", "negative": "arrives as threat", "mixed": "both opportunity and threat"}
            frame_table = genre_frames.get(intrusion_genre, default_frame)
            polarity_frame = frame_table.get(polarity, frame_table.get("mixed", "shifts the situation"))
            intrusion = f"[Intrusion: {tag}] {polarity_frame}"
            if line:
                intrusion += f"\n{line}"
            directive_parts.append(intrusion)

            # Anomaly system message (Discord)
            header = f"⚡ 이변 발생: [[{tag}]]"
            anomaly_sys += f"\n{header}"
            if line:
                anomaly_sys += f"\n{line}"
            else:
                info_parts = []
                if tag: info_parts.append(f"태그: {tag}")
                if intensity: info_parts.append(f"강도: {intensity}")
                if polarity: info_parts.append(f"성격: {polarity}")
                anomaly_sys += f"\n{' / '.join(info_parts) if info_parts else '이변 정보: (미상)'}"
        system_msg += anomaly_sys

        # ── System Logs (Discord) ──
        if bus.doom and bus.doom.get("relief_log"):
            system_msg += f"\n{bus.doom.get('relief_log')}"
        if bus.doom and bus.doom.get("mental_pressure_log"):
            system_msg += f"\n{bus.doom.get('mental_pressure_log')}"
        if bus.doom and bus.doom.get("clock_log"):
            system_msg += f"\n⏰ {bus.doom.get('clock_log')}"
        if bus.doom and bus.doom.get("defense_log"):
            system_msg += f"\n{bus.doom.get('defense_log')}"
        if bus.vigor:
            log_parts = []
            if bus.vigor.get("log"):
                log_parts.append(bus.vigor.get("log"))
            if log_parts:
                system_msg += f"\n{' → '.join(log_parts)}"

        # ── Layer 2: [Aspects] — Fate Aspect declaration (Genre-Aware) ──
        aspects = []
        import config as _cfg
        mechanic = context.request.genres.get("mechanic", {})
        primary_axis = mechanic.get("primary_resource") or "vigor"
        primary_val = vigor_val if primary_axis == "vigor" else composure_val

        m_trauma = (bus.vigor and bus.vigor.get("trauma_trigger")) or (bus.composure and bus.composure.get("trauma_trigger"))
        if j_active and a_triggered:
            if j_result in ("critical_failure", "failure"):
                aspects.append("Failure Resonance")
            elif j_result == "critical_success":
                aspects.append("Glory's Shadow")
        if a_triggered and primary_val <= 39:
            erosion_label = "Body Erosion" if primary_axis == "vigor" else "Mind Fracture"
            aspects.append(erosion_label)
        if m_trauma and a_triggered:
            aspects.append("Inner-Outer Convergence")
        if m_trauma and j_active:
            aspects.append("Resurgence")
        if j_result == "critical_failure" and primary_val <= 14:
            aspects.append("Abyss")
        if bus.anomaly and bus.anomaly.get("escalated"):
            aspects.append("Loss of Control")
        if aspects:
            directive_parts.append("[Aspects]: " + ", ".join(aspects))

        # ── Layer 4: [Atmosphere] — Doom Clock + Vigor ──
        atmosphere = []
        active_modules = context.request.active_modules

        # Doom = 8-Segment FitD Clock (only when module active)
        # (Atmosphere reference. Never name doom stages, clock names, or percentages in prose.)
        if "doom" in active_modules:
            # Genre-aware doom stage lookup
            import game_world as _gw
            mechanic_doom = context.request.genres.get("mechanic", {})
            primary_genre = mechanic_doom.get("primary_lens", "")
            doom_info = _gw.get_doom_info(doom_val, genre=primary_genre)
            stage_name = doom_info.get("name", "")
            stage_emoji = doom_info.get("emoji", "")

            if doom_val >= 88:
                atmosphere.append(f"Tension Clock {doom_val}% {stage_emoji}[{stage_name}] — about to break")
            elif doom_val >= 76:
                atmosphere.append(f"Tension Clock {doom_val}% {stage_emoji}[{stage_name}] — running out of time")
            elif doom_val >= 63:
                atmosphere.append(f"Tension Clock {doom_val}% {stage_emoji}[{stage_name}] — closing in")
            elif doom_val >= 50:
                atmosphere.append(f"Tension Clock {doom_val}% {stage_emoji}[{stage_name}] — tension fills the air")
            elif doom_val >= 38:
                atmosphere.append(f"Tension Clock {doom_val}% {stage_emoji}[{stage_name}] — uneasy calm")
            elif doom_val >= 25:
                atmosphere.append(f"Tension Clock {doom_val}% {stage_emoji}[{stage_name}] — equilibrium")
            elif doom_val >= 13:
                atmosphere.append(f"Tension Clock {doom_val}% {stage_emoji}[{stage_name}] — relative calm")
            else:
                atmosphere.append(f"Tension Clock {doom_val}% {stage_emoji}[{stage_name}] — tension has receded")

        # Vigor + Composure = 2-axis PC state (only when module active)
        # (Show through behavior only. Never name 기력/평정/vigor/composure in prose.)
        if "mental" in active_modules:
            if vigor_val <= 14:
                atmosphere.append(f"Body collapse ({vigor_val}%) — limbs fail, can barely stand")
            elif vigor_val <= 39:
                atmosphere.append(f"Body exhaustion ({vigor_val}%) — heavy limbs, labored breath")
            elif vigor_val <= 69:
                atmosphere.append(f"Body strain ({vigor_val}%) — muscles ache, movements slow")

            if composure_val <= 14:
                atmosphere.append(f"Mind collapse ({composure_val}%) — thoughts scatter, reality blurs")
            elif composure_val <= 39:
                atmosphere.append(f"Mind fraying ({composure_val}%) — emotions leak, focus breaks")
            elif composure_val <= 69:
                atmosphere.append(f"Mind uneasy ({composure_val}%) — inner tension, restless")

        v_trauma = bus.vigor and bus.vigor.get("trauma_trigger")
        c_trauma = bus.composure and bus.composure.get("trauma_trigger")
        if v_trauma:
            atmosphere.append("Trauma surge (body) — adrenaline reignites failing limbs")
        if c_trauma:
            atmosphere.append("Trauma surge (mind) — survival instinct overrides breakdown")

        if atmosphere:
            directive_parts.append("[Atmosphere]: " + " / ".join(atmosphere))

        # ── NPC Autonomous Behavior Triggers (Phase 7) ──
        if bus.dai and bus.dai.get("psyche_states"):
            from npc_autonomous import NPCAutonomousEngine

            # Helena metrics(depth/tension) merge: domain 영속 데이터 → DAI attitudes
            _dai_att = bus.dai.get("npc_attitudes", {})
            _stored_att = (context.narrative_anchors or {}).get("stored_npc_attitudes", {})
            _merged_att = {}
            for _n, _a in _dai_att.items():
                _m = dict(_a) if isinstance(_a, dict) else {}
                _s = _stored_att.get(_n, {})
                if isinstance(_s, dict):
                    _m.setdefault("depth", _s.get("depth", 0))
                    _m.setdefault("tension", _s.get("tension", 0))
                _merged_att[_n] = _m

            triggers = NPCAutonomousEngine.evaluate_triggers(
                psyche_states=bus.dai.get("psyche_states", {}),
                npc_knowledge=bus.dai.get("npc_knowledge", {}),
                npc_attitudes=_merged_att,
                scene_type=bus.dai.get("scene_type", "normal"),
            )
            auto_directive = NPCAutonomousEngine.build_autonomous_directive(triggers)
            if auto_directive:
                directive_parts.append(auto_directive)
            # iceberg per-NPC depth 계산용 구조 데이터 저장
            if triggers:
                bus.dai["autonomous_triggers"] = [
                    {"npc_name": t.npc_name, "trigger_id": t.trigger_id, "priority": t.priority}
                    for t in triggers
                ]

        # Fallbacks
        fallback_msg = self.pipeline.get_fallback_directives(context.request.active_modules)
        if fallback_msg:
            directive_parts.append(f"\n[Module Constraints]:\n{fallback_msg}")

        return {
            "directive": "\n".join(directive_parts),
            "system_msg": system_msg,
            "has_anomaly": bool(a_triggered),
            "anomaly_header": anomaly_sys.strip() if anomaly_sys else "",
            "adaptation_line": "",
            "mental_log": bus.vigor.get("log", "") if bus.vigor else "",
        }

    def _combine_batch_results(self, results: list, last_context) -> Dict[str, Any]:
        """다인 배치 결과를 통합 출력으로 합침"""
        all_directives = []
        judgment_msgs = []
        anomaly_header = ""
        mental_lines = []

        for r in results:
            if r["directive"]:
                all_directives.append(r["directive"])

            # 판정 부분만 추출 (system_msg에서 이변/멘탈 제외)
            sys = r["system_msg"]
            if "🎲" in sys:
                judgment_part = sys.split("⚡")[0].split("⏳")[0]
                for marker in ["\n📈", "\n📉", "\n🧠"]:
                    if marker in judgment_part:
                        judgment_part = judgment_part[:judgment_part.index(marker)]
                judgment_msgs.append(judgment_part.strip())

            # 이변 헤더는 1회만
            if r["has_anomaly"] and not anomaly_header:
                anomaly_header = r["anomaly_header"]

            if r["mental_log"]:
                mental_lines.append(r["mental_log"])

        # 통합 시스템 메시지 구성
        combined_sys = ""

        # 1. 모든 판정 결과
        if judgment_msgs:
            combined_sys += "\n\n".join(judgment_msgs)

        # 2. 이변 헤더 (1회)
        if anomaly_header:
            combined_sys += f"\n\n{anomaly_header}"

        # 3. 멘탈 변동 (PC별)
        if mental_lines:
            combined_sys += "\n"
            for line in mental_lines:
                combined_sys += f"\n{line}"

        return {
            "game_context": last_context,
            "directive": "\n\n".join(all_directives),
            "system_message": combined_sys.strip()
        }
