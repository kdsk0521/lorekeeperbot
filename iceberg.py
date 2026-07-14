"""
Iceberg Translation Layer — Theoria↔Renderer 수면 레이어

Theoria(Flash/Left Brain)의 분석 데이터를 Renderer(Main/Right Brain)가 소비할
관찰 가능한 행동 힌트로 번역한다. 분석 용어, 수치, 프레임워크 이름은 제거되고
descriptor와 행동 방향만 전달된다.

설계 원칙:
- DAI를 읽기만 한다 (절대 in-place 수정 금지 — bus.dai 보존)
- 모든 함수는 str을 반환한다
- 번역 실패 시 graceful fallback ("" 또는 원본 통과)

수면 기준: "Does this exist without cognitive processing?"
→ YES = 수면 위 (Renderer에 전달)
→ NO = 수면 아래 (코드가 번역 또는 제거)
"""

import logging
import re
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("Iceberg")

# =========================================================
# 동적 수면 (Water Level) 시스템
# =========================================================

_WATER_LEVEL = {
    "summary":  0.9,
    "combat":   0.8,
    "normal":   0.5,
    "social":   0.3,
    "intimate": 0.1,
}

_ENERGY_MOD = {
    "idle":       -0.1,
    "stagnant":   -0.05,
    "rising":      0.0,
    "detonation": +0.2,
    "aftershock": -0.15,
}


def _calc_depth(scene_type: str = "normal", energy: str = "idle") -> float:
    """수면 높이 계산. 높을수록 정보가 적게 노출됨."""
    base = _WATER_LEVEL.get(scene_type, 0.5)
    mod = _ENERGY_MOD.get(energy, 0.0)
    return max(0.0, min(1.0, base + mod))


# =========================================================
# NPC별 동적 수면 (Per-NPC Depth Knobs)
# =========================================================

# --- 세션 단계 보정 ---
_TURN_THRESHOLDS = [(3, 0.10), (8, 0.00)]
_TURN_LATE_MOD = -0.05


def _turn_mod(turn_count: int) -> float:
    """세션 단계별 전역 depth 보정."""
    for threshold, mod in _TURN_THRESHOLDS:
        if turn_count <= threshold:
            return mod
    return _TURN_LATE_MOD


# --- 트리거 보정 ---
def _trigger_depth_mod(priority: int) -> float:
    """autonomous trigger priority → depth 보정. pri 1~7 → -0.04 ~ -0.25."""
    if priority <= 0:
        return 0.0
    return -(0.04 + (min(priority, 7) - 1) * 0.035)


# --- 커넥션 보정 ---
def _connection_depth_mod(depth_value: int) -> float:
    """connection depth 0-100 → depth 보정. 0.0 ~ -0.15."""
    if depth_value <= 0:
        return 0.0
    return -(min(depth_value, 100) / 100) * 0.15


# --- 태도 변화 보정 ---
_TRAJECTORY_DEPTH_MOD = {
    "warming": -0.05, "cooling": -0.03, "volatile": -0.08,
    "stable": 0.00, "improving": -0.05, "declining": -0.03,
}

# --- Lack 층 보호 바닥 ---
_DEFAULT_FLOOR = 0.20   # 기본: Lack(▸절대 직접 말하지 마) 비노출
_EXTREME_FLOOR = 0.15   # intimate + 유대(80+) + 트리거 동시 충족 시에만


def compute_npc_depths(
    npc_names: list,
    scene_type: str,
    energy: str,
    turn_count: int = 0,
    autonomous_triggers: Optional[list] = None,
    connection_depths: Optional[Dict[str, int]] = None,
    npc_attitudes: Optional[Dict[str, Any]] = None,
) -> Dict[str, float]:
    """NPC별 수면 깊이 계산. {npc_name: depth}."""
    # [Sprint J 2026-04-28] npc_names None/non-list 안전 가드
    if not npc_names or not isinstance(npc_names, list):
        return {}
    base = _calc_depth(scene_type, energy)
    t_mod = _turn_mod(turn_count)
    global_depth = max(0.0, min(1.0, base + t_mod))

    result = {name: global_depth for name in npc_names if name and isinstance(name, str)}

    # 트리거 매핑 (NPC → 최고 priority)
    trigger_map: Dict[str, int] = {}
    if autonomous_triggers:
        for t in autonomous_triggers:
            n = t.get("npc_name", "")
            p = t.get("priority", 0)
            if n in result:
                trigger_map[n] = max(trigger_map.get(n, 0), p)
                result[n] += _trigger_depth_mod(p)

    # 커넥션 깊이 보정
    conn_map = connection_depths or {}
    for name, dv in conn_map.items():
        if name in result and dv > 0:
            result[name] += _connection_depth_mod(dv)

    # 태도 변화 보정
    if npc_attitudes:
        for name, att in npc_attitudes.items():
            if name in result and isinstance(att, dict):
                traj = att.get("trajectory", "stable")
                result[name] += _TRAJECTORY_DEPTH_MOD.get(traj, 0.0)

    # Floor 적용: 기본 0.2, 극한 조건(intimate+유대+트리거) 시 0.15
    for name in result:
        has_trigger = name in trigger_map
        conn_val = conn_map.get(name, 0)
        is_extreme = (scene_type == "intimate" and conn_val >= 80 and has_trigger)
        floor = _EXTREME_FLOOR if is_extreme else _DEFAULT_FLOOR
        result[name] = max(floor, min(1.0, result[name]))

    return result


# =========================================================
# Utility
# =========================================================

def _to_tier(value: float, tiers: List[Tuple[float, str]]) -> str:
    """수치를 서수 tier로 변환."""
    for threshold, label in tiers:
        if value <= threshold:
            return label
    return tiers[-1][1] if tiers else "unknown"



# =========================================================
# 1. psyche_states (Slot 14)
# =========================================================

_POLYVAGAL_NOTATION = {
    "ventral":     "soma | ♪ mp, andante, legato | ▶ two-shot, parallel [diffused, warm, solid] | ◎ real-time",
    "sympathetic": "soma | ♪ f, allegro, staccato | ▶ close-up, back-to-back, cut [side_light, cool, vivid] | ◎ slow-motion",
    "dorsal":      "soma | ♪ pp, largo, legato | ▶ long-take, pillow [single_source, grey, washed] | ◎ freeze",
}

_CULTURAL_AFFECT_NOTATION = {
    "han":       "affect | ♪ p, adagio, legato, diminuendo | ▶ long-take, back-to-back [backlight, grey, washed] | ◎ long-exposure",
    "jeong":     "affect | ♪ mp, andante, legato | ▶ two-shot, pillow [diffused, amber, solid] | ◎ real-time",
    "hwabyung":  "affect | ♪ ff, presto, sforzando | ▶ close-up, facing [side_light, crimson, vivid] | ◎ slow-motion",
    "nunchi":    "affect | ♪ p, andante, staccato | ▶ over-the-shoulder, height-gap [side_light, grey] | ◎ slow-motion",
    "chaemyeon": "affect | ♪ mf, andante, legato | ▶ two-shot, facing [high_key, solid] | ◎ real-time",
    "simma":     "affect | ♪ mf, allegro, staccato, crescendo | ▶ close-up [side_light, amber, vivid] | ◎ slow-motion",
    "gi":        "affect | ♪ f, allegro, marcato | ▶ wide, height-gap [single_source, vivid] | ◎ real-time",
}

_DISSOCIATION_NOTATION = {
    "mild":     "consciousness | ♪ pp, adagio, legato | ▶ eye-level, pillow [diffused, grey, pastel] | ◎ long-exposure",
    "moderate": "consciousness | ♪ pp, largo, staccato | ▶ high-angle, height-gap [single_source, grey, washed] | ◎ interval",
    "severe":   "consciousness | ♪ pp, largo, legato | ▶ long-take, back-to-back [low_key, grey, washed] | ◎ freeze",
}

_LAYER_RENAMES = {
    "Surface": "▸usual(80%)",
    "Adaptation": "▸repeat-pattern",
    "Core": "▸only-at-the-edge",
    "Lack": "▸beneath the surface",
}


def _rename_layer_labels(deep_read: str) -> str:
    """4층 분석 레이블 → 렌더링 가중치 교체."""
    result = deep_read
    for eng, kor in _LAYER_RENAMES.items():
        result = result.replace(f"{eng}:", f"{kor}:")
        result = result.replace(f"{eng}：", f"{kor}:")
    return result


def _filter_deep_read_by_depth(deep_read: str, depth: float) -> str:
    """depth에 따라 deep_read 노출 범위를 제한."""
    if depth >= 0.8:
        return ""
    renamed = _rename_layer_labels(deep_read)
    if depth >= 0.6:
        # ▸usual only
        lines = renamed.split("▸")
        kept = [lines[0]]  # prefix before first ▸
        for part in lines[1:]:
            if part.startswith("usual"):
                kept.append("▸" + part)
        return "".join(kept).strip()
    if depth >= 0.4:
        # ▸usual + ▸repeat-pattern
        lines = renamed.split("▸")
        kept = [lines[0]]
        for part in lines[1:]:
            if part.startswith("usual") or part.startswith("repeat-pattern"):
                kept.append("▸" + part)
        return "".join(kept).strip()
    if depth >= 0.2:
        # ▸only-at-the-edge included
        lines = renamed.split("▸")
        kept = [lines[0]]
        for part in lines[1:]:
            if not part.startswith("beneath the surface"):
                kept.append("▸" + part)
        return "".join(kept).strip()
    # depth < 0.2: 전부 노출
    return renamed


# 색 변조 vocab (Phase 1: per-NPC 빛 진폭 — warm-cluster 깸, Phase 2 compositor 입력 먹이)
# 트리플 = NPC에게 내리쬐는 빛: [lighting 세팅, hue 색온도, saturation 강도]
_HUE_GENERIC = {"warm", "amber", "cool", "grey"}   # crimson 등 distinctive hue는 보존(변조 제외)
_SAT_TOKENS = {"solid", "vivid", "washed", "pastel"}
_LIGHT_SOFT = {"diffused", "high_key"}             # backlight/single_source/golden_hour=distinctive, 보존
_LIGHT_HARD = {"side_light", "low_key"}


def _modulate_color_triplet(triplet: str, valence) -> str:
    """[lighting, hue, saturation] 트리플을 valence로 변조 (= NPC에게 내리쬐는 빛).
    hue: valence 부호 → 색온도(neg→cool/grey, pos→amber). generic hue만, crimson 등 distinctive 보존.
    saturation: |valence| → 강도(강한 정동→vivid, 밋밋→washed).
    lighting: |valence| → 경도(강한 정동→side_light 강한 빛, 밋밋→diffused 부드러운 빛). distinctive 보존.
    위치 아닌 vocab으로 토큰 식별(2~3토큰 가변)."""
    if not isinstance(valence, (int, float)):
        return triplet
    toks = [t.strip() for t in triplet.split(",")]
    av = abs(valence)
    for i, t in enumerate(toks):
        if t in _HUE_GENERIC:
            if valence <= -40:
                toks[i] = "cool"
            elif valence <= -15:
                toks[i] = "grey"
            elif valence >= 50:
                toks[i] = "amber"
        elif t in _SAT_TOKENS:
            if av >= 60:
                toks[i] = "vivid"
            elif av <= 15:
                toks[i] = "washed"
        elif t in _LIGHT_SOFT:
            if av >= 60:
                toks[i] = "side_light"
        elif t in _LIGHT_HARD:
            if av <= 15:
                toks[i] = "diffused"
    return ", ".join(toks)


def _value_modulate_notation(notation: str, psyche_val, relation_val) -> str:
    """valence value(-100~+100)로 enum-base ♪▶◎에 intra-state 변주 삽입 (trajectory 삽입 패턴 일반화).
    psyche.value → ♪ 수식어, relation.value → ▶ 거리. 같은 polyvagal state라도 NPC별로 갈라짐.
    (2026-06-25: enum 고정 노테이션의 narrowness 보완 — 재료 더 먹여 변주.)"""
    if not notation:
        return notation
    if isinstance(psyche_val, (int, float)):
        m = ""
        if psyche_val <= -50:
            m = "marcato"
        elif psyche_val <= -20:
            m = "staccato"
        elif psyche_val >= 50:
            m = "dolce"
        if m and " | ▶" in notation:
            notation = notation.replace(" | ▶", f", {m} | ▶", 1)
    if isinstance(relation_val, (int, float)):
        c = ""
        if relation_val >= 50:
            c = "push-in"
        elif relation_val <= -50:
            c = "pull-back"
        if c and " | ◎" in notation:
            notation = notation.replace(" | ◎", f", {c} | ◎", 1)
    # [color] 트리플 변조: valence 부호→hue 온도, |valence|→saturation 강도 (warm-cluster 깸)
    if isinstance(psyche_val, (int, float)):
        _cm = re.search(r"\[([^\]]+)\]", notation)
        if _cm:
            notation = notation[:_cm.start(1)] + _modulate_color_triplet(_cm.group(1), psyche_val) + notation[_cm.end(1):]
    return notation


def translate_psyche_states(
    psyche_data: dict,
    scene_type: str = "normal",
    energy: str = "idle",
    npc_depths: Optional[Dict[str, float]] = None,
) -> str:
    """6축 심리 데이터 → 관찰 가능한 행동 힌트로 변환.
    npc_depths가 있으면 NPC별 개별 수면 적용, 없으면 전역 depth."""
    if not psyche_data or not isinstance(psyche_data, dict):
        return ""

    global_depth = _calc_depth(scene_type, energy)
    lines = []
    _npc_notations = []  # Phase 2: cross-NPC 대비용 per-NPC 대표 노테이션(색 변조된 soma) 수집

    for name, state in psyche_data.items():
        if isinstance(state, str):
            lines.append(f"- {name}: {state}")
            continue
        if not isinstance(state, dict):
            continue

        # per-NPC depth (있으면) 또는 전역 depth
        depth = npc_depths.get(name, global_depth) if npc_depths else global_depth

        psyche = state.get("psyche", state.get("mental", {}))
        if not isinstance(psyche, dict):
            psyche = {}
        soma = state.get("soma", {})
        if not isinstance(soma, dict):
            soma = {}
        relation = state.get("relation", {})
        if not isinstance(relation, dict):
            relation = {}
        deep = state.get("deep_read", "")

        # ♪▶◎ notation 줄 (soma는 항상 수면 위)
        notations = []
        pvg = soma.get("polyvagal", "")
        if pvg and isinstance(pvg, str):
            pvg_n = _POLYVAGAL_NOTATION.get(pvg.lower().strip())
            if pvg_n:
                pvg_n = _value_modulate_notation(pvg_n, psyche.get("value"), relation.get("value"))
                notations.append(pvg_n)
                _npc_notations.append((name, pvg_n))
        ca = soma.get("cultural_affect", "")
        if ca and isinstance(ca, str) and ca != "null":
            ca_n = _CULTURAL_AFFECT_NOTATION.get(ca.lower().strip())
            if ca_n:
                notations.append(ca_n)
        diss = soma.get("dissociation", "")
        if diss and isinstance(diss, str) and diss not in ("null", "none"):
            diss_n = _DISSOCIATION_NOTATION.get(diss.lower().strip())
            if diss_n:
                notations.append(diss_n)

        # prose descriptor 줄 (depth에 따라 축 제한)
        prose = []
        if soma.get("descriptor"):
            prose.append(soma["descriptor"])
        env = soma.get("env_influence")
        if env and isinstance(env, str) and env != "null":
            prose.append(env)
        if depth < 0.8 and psyche.get("descriptor"):
            prose.append(psyche["descriptor"])
        if depth < 0.6 and relation.get("descriptor"):
            prose.append(relation["descriptor"])

        lines.append(f"- {name}:")
        for n in notations:
            lines.append(f"  {n}")
        if prose:
            lines.append(f"  {'. '.join(prose)}")

        # deep_read: depth에 따라 필터링 + 렌더링 제약
        if deep and isinstance(deep, str):
            filtered = _filter_deep_read_by_depth(deep, depth)
            if filtered:
                lines.append(f"  └ {filtered}")
                # B3: 노출 깊을수록 행동-only 제약 강화
                if depth < 0.2:
                    lines.append("    [irreducible: a reaction only this character, this moment makes — irreducible, not performed.]")
                elif depth < 0.4:
                    lines.append("    [through action, reflex, bodily response; the naming and the psych vocabulary stay with the narrator, out of the prose.]")

        # resurfacing: depth < 0.6일 때만 노출 (intimate/social)
        resurface = state.get("resurfacing")
        if resurface and isinstance(resurface, str) and resurface != "null" and depth < 0.6:
            lines.append(f"  └ resurfacing: {resurface}")

    # [Phase 2] cross-NPC 대비 (연출가-독자): 2+ NPC면 색/♪▶◎ 진폭을 contrast-lead로 합성 → Slot14 envelope가 감쌈
    if len(_npc_notations) >= 2:
        try:
            from notation_compositor import compose_notations
            _contrast = compose_notations(_npc_notations, scene_type)
            if _contrast:
                lines.append("")
                lines.append(_contrast)
        except Exception:
            pass

    return "\n".join(lines)


# =========================================================
# 2. position/effect (Slot 13) + friction 통합
# =========================================================

_POS_TIERS = [
    (0.2, "dire"),
    (0.4, "adverse"),
    (0.6, "even"),
    (0.8, "favorable"),
    (1.0, "dominant"),
]

_POS_FRICTION = {
    "dire": "the barrier is real; world-logic governs, not narrative convenience.",
    "adverse": "this situation carries a cost; the cost stays honest.",
}


def translate_position_effect(
    position: Optional[dict],
    effect: Optional[dict],
) -> str:
    """Position/Effect 수치 → 서수 tier + reason + friction."""
    parts = []
    if position and isinstance(position, dict):
        tier = _to_tier(position.get("value", 0.5), _POS_TIERS)
        reason = position.get("reason", "")
        line = f"position: {tier} ({reason})" if reason else f"position: {tier}"
        friction = _POS_FRICTION.get(tier, "")
        if friction:
            line += f"\n  └ {friction}"
        parts.append(line)
    if effect and isinstance(effect, dict):
        tier = _to_tier(effect.get("value", 0.5), _POS_TIERS)
        reason = effect.get("reason", "")
        parts.append(f"leverage: {tier} ({reason})" if reason else f"leverage: {tier}")
    return "\n".join(parts)


# =========================================================
# 3. energy_direction (Slot 16)
# =========================================================

_ENERGY_VISUAL = {
    "idle":       "[diffused, amber, pastel]",
    "rising":     "[side_light, cool, solid]",
    "stagnant":   "[single_source, grey, washed]",
    "detonation": "[single_source, crimson, vivid]",
    "aftershock": "[backlight, sepia, washed]",
}

# --- PACING tables (was PACING_CONTROL_PROTOCOL static constant) ---

_ENERGY_TONE = {
    "rising":        "tension builds → sensory weight",
    "detonation":    "peak → max rendering",
    "stagnant":      "world at rest → sensory density",
    "aftershock":    "debris settling → residue only, no verdict",
    "exploration":   "threads open → ground in body, evidence",
    "establishment": "personality drives → comfort earned, no echo",
    "rupture":       "contradictions coexist → no immunity",
}

_ENERGY_BEAT = {
    "idle":       "1 beat (sensory or a line of exchange)",
    "rising":     "1-2 beats (exchange+texture)",
    "detonation": "2 beats (action+consequence)",
    "stagnant":   "1 beat (sensory or a line of exchange)",
    "aftershock": "1 beat (residue)",
}

_ENERGY_END = {
    "idle":       "a quiet forward tilt",
    "rising":     "render tension weight",
    "aftershock": "residue, no verdict",
}


def translate_energy_direction(energy: str, scene_light: Optional[dict] = None) -> str:
    """energy_direction → light + tone + beat + end (conditional injection).
    Replaces static PACING_CONTROL_PROTOCOL constant.
    scene_light: theoria spatial_read.light(조건-도출 씬-색) 있으면 Light 라인에 우선 사용, 없으면 energy 5버킷 fallback."""
    if not energy:
        return ""
    key = energy.lower().strip()

    parts = []
    # scene Light: theoria 조건-도출 색 우선, 없으면 energy→visual fallback (역할분리: theoria=풍부, energy=폴백)
    if isinstance(scene_light, dict) and (scene_light.get("lighting") or scene_light.get("hue")):
        _trip = ", ".join(x for x in (scene_light.get("lighting"), scene_light.get("hue"), scene_light.get("saturation")) if x)
        if _trip:
            parts.append(f"Light: [{_trip}]")
    else:
        visual = _ENERGY_VISUAL.get(key, "")
        if visual:
            parts.append(f"Light: {visual}")

    tone = _ENERGY_TONE.get(key, "")
    if tone:
        parts.append(tone)

    beat = _ENERGY_BEAT.get(key, "")
    if beat:
        parts.append(f"Beat: {beat}")

    end = _ENERGY_END.get(key, "")
    if end:
        parts.append(f"End: {end}")

    if key in ("detonation", "rupture"):
        parts.append("Time lock: this turn = one moment")

    if not parts:
        return ""
    return "### Energy Pacing\n" + "\n".join(parts)


# =========================================================
# 2-1. story_direction (Slot 16) — StoryDirector pacing/tension/transition/focus
# =========================================================

_PACING_KR = {
    "push":    "pacing: ♪ accelerando · ▶ push-in",
    "hold":    "pacing: ♪ a tempo · ▶ held frame, still breathing",
    "breathe": "pacing: ♪ ritardando · ▶ slow-pull",
    "pivot":   "pacing: ▶ cut-to · ♪ key-change",
}

_TENSION_KR = {
    "critical":  "tension: ♪ ff · ▶ tight close-up",
    "rising":    "tension: ♪ crescendo · ▶ slow push-in",
    "plateau":   "tension: ♪ sostenuto · ▶ held frame, uneasy",
    "falling":   "tension: ♪ diminuendo · ▶ pull-back",
}

_CUT_KR = {
    "hard_cut":        "cut: ▶ hard-cut",
    "fade":            "cut: ▶ fade",
    "contrast_cut":    "cut: ▶ match-cut",
    "natural":         "cut: ▶ continuous",
    "dramatic_entrance": "cut: ▶ smash-cut",
}

_IDLE_SOURCE_KR = {
    "active_condition":  "an ongoing situation widens.",
    "narrative_chain":   "the narrative chain carries forward.",
    "emotion":           "the NPC whose feeling runs hottest takes the lead.",
    "doom":              "environmental pressure surfaces through the narrative.",
    "ambient":           "the world moves on its own; a small, quiet advance.",
}


def translate_register(register) -> str:
    """scene_register → Korean rendering hint for Pro."""
    if not register or not isinstance(register, str):
        return ""
    _REGISTER_KR = {
        "mirror": "[Mirror] the character sees themselves in the other without recognizing it; the trait and the misreading surface in concrete detail.",
        "law": "[Law] hierarchy / expectation / protocol bends under the input; the order, the crack, and the one feigning ignorance all surface.",
        "remainder": "[Remainder] what the scene can't digest into dialogue or action; sensation, repetition, a detail serving no plot stays behind.",
    }
    if register == "mirror":
        try:
            import config as _cfg_ib
            if not getattr(_cfg_ib, "ICEBERG_MIRROR_ENABLED", True):
                return ""
        except Exception:
            pass
    return _REGISTER_KR.get(register, "")


def infer_scene_register(
    psyche_states: Optional[dict],
    narrative_chain: Optional[dict],
) -> str:
    """B1: Flash scene_register가 null일 때 기존 DAI에서 register 추론.
    Returns register string or empty string."""
    if not psyche_states or not isinstance(psyche_states, dict):
        return ""
    for _name, state in psyche_states.items():
        if not isinstance(state, dict):
            continue
        psyche = state.get("psyche", state.get("mental", {}))
        if not isinstance(psyche, dict):
            psyche = {}
        relation = state.get("relation", {})
        if not isinstance(relation, dict):
            relation = {}
        # mirror: self_opacity(자기기만, str) + apprehension_gap 존재
        _so = psyche.get("self_opacity")
        if (isinstance(_so, str) and _so.strip() and _so.strip().lower() != "null"
                and psyche.get("apprehension_gap")):
            logger.info("[Iceberg] register=mirror (inferred)")
            return "mirror"
        # law: value_conflict + 적대적 협상자세(competitive/exploitative)
        if relation.get("value_conflict") and relation.get("negotiation_stance") in (
            "competitive", "exploitative",
        ):
            logger.info("[Iceberg] register=law (inferred)")
            return "law"
    # remainder: still-silence only (間). U1 게이팅(2026-07-04): 전진-charged 침묵(tense='한 마디에
    # 다 바뀜' / hesitant='삼킨 말')은 push라 잔여(residue) 아님 → remainder 자동선택에서 제외.
    # reflective(내면·시간지연)·heavy(둘 다 알고 침묵)만 remainder. 대사멈춤마다 카버 register
    # 자동공급하던 것 축소(헤비노벨). Flash가 scene_register를 명시 선택한 경우는 이 폴백을 안 탐.
    if narrative_chain and isinstance(narrative_chain, dict):
        _sil = narrative_chain.get("silence_type")
        if isinstance(_sil, str) and _sil.strip().lower() in ("reflective", "heavy"):
            logger.info("[Iceberg] register=remainder (inferred, still-silence)")
            return "remainder"
    return ""


# ----- B5: Propagation Shape (장면 전파 형태) -----

_PROPAGATION_KR = {
    "compression": "[Compression] impact hits the target hardest, damping outward; the direct weight lands first, the reverberation slower.",
    "radiation": "[Radiation] impact spreads in all directions; each figure's own reaction tracks separately.",
    "oscillation": "[Oscillation] stimulus and response alternate; rendered rhythmically, each pass carrying a gradual shift.",
    "convergence": "[Convergence] several forces toward one point; the pressure accumulates, the result more than the sum of its parts.",
    "divergence": "[Divergence] one decision splits into branches; each path tracks on its own.",
    "torsion": "[Torsion] surface and interior pull opposite ways; the spoken surface and the under-force render at once.",
}


def infer_propagation_shape(
    psyche_states: Optional[dict],
    narrative_chain: Optional[dict],
    scene_type: str = "normal",
    energy: str = "idle",
    npc_count: int = 1,
) -> str:
    """B5: 기존 DAI에서 장면의 전파 형태 추론. Returns shape key or empty."""
    if not psyche_states or not isinstance(psyche_states, dict):
        return ""

    # torsion: 자기기만(self_opacity, str)이 활성 — 최우선 (다른 전파와 겹쳐도 비틀림이 지배)
    for _name, state in psyche_states.items():
        if not isinstance(state, dict):
            continue
        psyche = state.get("psyche", state.get("mental", {})) or {}
        _so = psyche.get("self_opacity")
        if isinstance(_so, str) and _so.strip() and _so.strip().lower() != "null":
            logger.info("[Iceberg] propagation=torsion (inferred)")
            return "torsion"

    # convergence: open_threads 복수 수렴 + 높은 에너지
    chain = narrative_chain or {}
    threads = chain.get("open_threads", [])
    if isinstance(threads, list) and len(threads) >= 3 and energy in ("detonation", "aftershock"):
        return "convergence"

    # divergence: 복수 open_threads가 비-클라이맥스에서 벌어짐 (스키마에 native branching 없음 → open_threads proxy)
    if isinstance(threads, list) and len(threads) >= 2 and energy in ("idle", "rising", "stagnant"):
        logger.info("[Iceberg] propagation=divergence (inferred)")
        return "divergence"

    # radiation: 3명+ NPC + 공개적 장면
    if npc_count >= 3 and scene_type in ("social", "normal", "combat"):
        return "radiation"

    # oscillation: 대화 중심 + negotiation 활성
    for _name, state in psyche_states.items():
        if not isinstance(state, dict):
            continue
        relation = state.get("relation", {}) or {}
        if relation.get("negotiation_stance") in ("cooperative", "competitive", "exploitative"):
            logger.info("[Iceberg] propagation=oscillation (inferred)")
            return "oscillation"

    # compression: 1:1 + 강한 에너지
    if npc_count <= 2 and energy in ("detonation", "aftershock"):
        return "compression"

    return ""


def translate_propagation_shape(shape: str) -> str:
    """propagation shape key → Korean rendering directive."""
    if not shape or not isinstance(shape, str):
        return ""
    return _PROPAGATION_KR.get(shape, "")


def translate_story_direction(story_dir: Optional[dict], scene_type: str = "normal") -> str:
    """story_direction → pacing + tension + transition + focus 지시 (Korean)."""
    if not story_dir or not isinstance(story_dir, dict) or not story_dir.get("active"):
        return ""

    parts = []

    # Pacing
    pacing = story_dir.get("pacing", "")
    pacing_hint = _PACING_KR.get(pacing, "")
    if pacing_hint:
        parts.append(f"Pacing: {pacing_hint}")

    # Tension
    tension = story_dir.get("tension_axis", "")
    tension_hint = _TENSION_KR.get(tension, "")
    if tension_hint:
        parts.append(f"Tension: {tension_hint}")

    # Transition
    transition = story_dir.get("transition", {})
    if isinstance(transition, dict):
        cut = transition.get("cut", "")
        cut_hint = _CUT_KR.get(cut, "")
        if cut_hint:
            parts.append(cut_hint)
        suggest = transition.get("suggest_shift", "")
        if suggest:
            parts.append(f"scene-shift cue: → {suggest}")

    # Focus spotlight
    focus = story_dir.get("focus", {})
    if isinstance(focus, dict):
        spotlight = focus.get("spotlight", "none")
        if spotlight and spotlight != "none":
            reason = focus.get("reason", "")
            parts.append(f"focus: {spotlight}" + (f" ({reason})" if reason else ""))

    # Idle direction (proactive scene guidance)
    idle_dir = story_dir.get("idle_direction")
    if idle_dir and isinstance(idle_dir, dict):
        source = idle_dir.get("source", "ambient")
        idle_hint = _IDLE_SOURCE_KR.get(source, "")
        if idle_hint:
            parts.append(f"[active advance] {idle_hint}")
        npc = idle_dir.get("npc", "")
        if npc:
            parts.append(f"leading NPC: {npc}")

    # Seven Dice (W9) — 은닉 4면만 Slot 16 분위기로. 가시 3면은 Slot 19(WRITING_DIRECTIVES) 경로.
    dice = story_dir.get("dice")
    if dice and isinstance(dice, dict) and not dice.get("visible"):
        effect = dice.get("effect", "")
        if effect:
            parts.append(f"[narrative undercurrent] {effect}")

    # latent relations (conflict/alliance 그래프) → "생길 수 있는 사건" 잠재 힌트 (anti-railroad, 단정 아님)
    latent = story_dir.get("latent_relations")
    if latent and isinstance(latent, list):
        for _lr in latent[:3]:
            parts.append(f"[latent — could surface, not mandated] {_lr}")

    if not parts:
        return ""
    return "### Story Direction\n" + "\n".join(parts)


# =========================================================
# 3-1. memory_type (Slot 16) — was MEMORY_HIERARCHY static constant
# =========================================================

_MEMORY_PROSE_STYLE = {
    "traumatic": "fragmented, non-linear, sensory shards, flash-cuts",
    "nostalgic": "soft-focus, idealized, gentle rhythm",
    "shameful": "intrusive, body recoils before mind",
    "loving": "hyper-clear, specific details preserved",
    "mundane": "blurred, fog-like, uncertain",
}


def translate_memory_type(memory_triggers: list) -> str:
    """memory_triggers type → prose style hint. Empty string when no triggers."""
    if not memory_triggers or not isinstance(memory_triggers, list):
        return ""
    styles = []
    seen = set()
    for m in memory_triggers:
        if not isinstance(m, dict):
            continue
        mtype = m.get("type", "")
        if not mtype or mtype in seen:
            continue
        seen.add(mtype)
        style = _MEMORY_PROSE_STYLE.get(mtype.lower().strip(), "")
        if style:
            styles.append(f"- {mtype}: {style}")
    if not styles:
        return ""
    return "### Memory Rendering\n" + "\n".join(styles)


# =========================================================
# 3-2. time_atmosphere (Slot 16) — was TEMPORAL_FLOW TIME-OF-DAY/DURATION tables
# =========================================================

_TIME_ATMOSPHERE = {
    "새벽": "silence, mist, blue",
    "오전": "vitality, sunlight",
    "오후": "peak heat",
    "황혼": "long shadows, gold",
    "저녁": "streetlights, danger rises",
    "심야": "darkness, danger max",
}

_DURATION_HINTS = {
    "combat": "seconds-minutes. Momentum > environmental bookkeeping",
}


def translate_time_atmosphere(time_context: str, scene_type: str = "normal") -> str:
    """TimeContext keyword → sensory hint. Combat adds duration hint."""
    if not time_context or not isinstance(time_context, str):
        return ""
    parts = []
    for key, hint in _TIME_ATMOSPHERE.items():
        if key in time_context:
            parts.append(f"Time: {key}({hint})")
            break
    if scene_type == "combat":
        parts.append(f"Duration: {_DURATION_HINTS['combat']}")
    if not parts:
        return ""
    return "\n".join(parts)


# =========================================================
# 4. quality_flags (Slot 16)
# =========================================================

_FLAG_DIRECTIVES = {
    "convergence_warning": "relationship shifting fast; the pace needs a cause behind it.",
    "echo_warning": "NPC tracking PC's emotion; the reaction wants its own reason.",
    "stagnation_warning": "scene energy flat 3 turns; an external stimulus enters naturally.",
    "mse_deviation": "NPC behavior jumped; it stays consistent with before, or the change earns a cause.",
    "dissonance_flag": "NPC's words and actions diverge; the gap stays unresolved, surfacing as small mismatch in gesture, expression, breath.",
    "redemption_warning": "NPC softening without cause; the prior pattern holds.",
    "shallow_read": "analysis stayed at the surface; beneath the shown action lies the unsaid, the room's pressure, the unpaid debt.",
    "sensory_habituated": "senses habituated in this space; the micro-shift, or a fresh sense channel, carries it now.",
    "label_internalization": "NPC starts believing its label; the label stays unspoken, showing through habit, posture, reaction.",
    "sheet_deducible": "vending-machine read: the reaction is a literal translation of sheet tags; the specific one belongs to this character, this moment.",
}

_SYMPTOM_TEMPLATE = "NPC shows {cluster} symptoms; they hold as one consistent set."

# =========================================================
# 시간 방향 번역
# =========================================================

_TEMPORAL_KR = {
    "past": "the character's gaze turns toward the past",
    "future": "the character's gaze turns toward what's ahead",
    "present": "the character stays in this exact moment",
}


def translate_temporal_orientation(temporal_data: Optional[dict]) -> str:
    """TemporalOrientation → 한국어 시간 방향 힌트."""
    if not temporal_data or not isinstance(temporal_data, dict):
        return ""
    focus = temporal_data.get("focus", "")
    intensity = temporal_data.get("intensity", 0)
    if not focus or not isinstance(intensity, (int, float)) or intensity <= 0.3:
        return ""
    hint = _TEMPORAL_KR.get(focus, "")
    if not hint:
        return ""
    if intensity > 0.7:
        hint += ", strongly"
    return f"### temporal orientation\n{hint}"


def translate_quality_flags(flags: Optional[dict]) -> str:
    """QualityFlags → 행동 지시 텍스트."""
    if not flags or not isinstance(flags, dict):
        return ""
    directives = []
    for key, template in _FLAG_DIRECTIVES.items():
        if flags.get(key):
            directives.append(template)
    # symptom_cluster: string, not boolean
    cluster = flags.get("symptom_cluster")
    if cluster and isinstance(cluster, str) and cluster.lower() != "null":
        directives.append(_SYMPTOM_TEMPLATE.format(cluster=cluster))
    return "\n".join(directives)


# =========================================================
# 4-1. Scene Continuity Check → 보정 지시
# =========================================================

_CONTINUITY_TYPE_KR = {
    "spatial_break": "spatial discontinuity",
    "sensory_break": "sensory discontinuity",
    "object_break": "object discontinuity",
    "tone_break": "tonal discontinuity",
    "npc_break": "character discontinuity",
    "rhythm_break": "rhythm discontinuity",
}

_SHIFT_HINTS = {
    "gradual": "light and color are shifting slowly",
    "sudden": "light and color jumped; a bodily shock comes with it",
}
_THRESHOLD_HINTS = {
    "mild": "the space has changed; one sentence of sensory transition",
    "sharp": "the sensory drop is steep; a bodily reaction — glare, chill, wind",
}


def translate_spatial_inscription(spatial_read: Optional[dict]) -> str:
    """spatial_read → 공간 각인/전환 렌더링 힌트.
    ambient이면 배경 질감. render이면 전부."""
    if not spatial_read or not isinstance(spatial_read, dict):
        return ""
    weight = spatial_read.get("weight", "ambient")

    lines = []

    traces = spatial_read.get("active_traces")
    if traces and isinstance(traces, list):
        for t in traces[:4]:
            if isinstance(t, dict) and t.get("detail"):
                lines.append(f"  {t['detail']}")

    flt = spatial_read.get("filter")
    if flt and isinstance(flt, str):
        lines.append(f"  [perceptual bias] {flt}, not a physical change")

    tension = spatial_read.get("tension")
    if tension and isinstance(tension, str) and tension != "null":
        lines.append(f"  [spatial gap] {tension}")

    shift = spatial_read.get("shift")
    if shift and shift != "null":
        hint = _SHIFT_HINTS.get(shift, "")
        if hint:
            lines.append(f"  {hint}")
    threshold = spatial_read.get("threshold")
    if threshold and threshold != "null":
        hint = _THRESHOLD_HINTS.get(threshold, "")
        if hint:
            lines.append(f"  {hint}")

    if not lines:
        return ""
    header = ("### space imprint\n(background texture; it stays in the backdrop.)\n"
              if weight == "ambient" else
              "### space imprint\n(what the space has been through surfaces as sensation; the analytic terms stay out of the prose.)\n")
    return header + "\n".join(lines)


def translate_continuity_check(check_data) -> str:
    """continuity_check → 보정 지시 (Korean behavioral directives)."""
    if not check_data or not isinstance(check_data, dict):
        return ""
    flags = check_data.get("flags", [])
    if not flags or not isinstance(flags, list):
        return ""
    directives = []
    for f in flags[:3]:
        if not isinstance(f, dict):
            continue
        ftype = f.get("type", "")
        correction = f.get("correction", "") or f.get("risk", "")
        type_kr = _CONTINUITY_TYPE_KR.get(ftype, ftype)
        if correction:
            directives.append(f"- {type_kr}: {correction}")
    # Retroactive rewriting cue
    rewrite = check_data.get("rewrite")
    if rewrite and isinstance(rewrite, str):
        directives.append(f"- retroactive: {rewrite}")
    # anchor_consumed: 이전 감각 앵커 소비 여부
    if check_data.get("anchor_consumed"):
        directives.append("- the previous scene's sensory anchor is spent; a new sensory point of origin sets in.")
    if not directives:
        return ""
    return ("### scene continuity\n"
            "a discontinuity from the previous scene is present; a natural connection forms.\n"
            + "\n".join(directives))


_SCHEME_KR = {
    "deflection": "deflection",
    "displacement": "displacement",
    "circling": "circling",
    "substitution": "substitution",
}

def translate_prev_scheme(prev_scheme: str) -> str:
    """render_fingerprint.withholding_scheme → 직전 수법 피드백 (Slot 16 주입)."""
    if not prev_scheme or prev_scheme == "none":
        return ""
    scheme_kr = _SCHEME_KR.get(prev_scheme, prev_scheme)
    return f"prev scheme: {scheme_kr}"


# =========================================================
# 5. NPC attitudes (Slot 17)
# =========================================================

_TRAJECTORY_NOTATION = {
    "warming":   "crescendo",
    "cooling":   "diminuendo",
    "stable":    "",
    "volatile":  "sforzando",
    "declining": "diminuendo",
    "improving": "crescendo",
}

_ATTITUDE_NOTATION = {
    "hostile":    "attitude | ♪ f, allegro, staccato | ▶ facing, height-gap [side_light, cool, vivid] | ◎ slow-motion",
    "unfriendly": "attitude | ♪ mf, andante, staccato | ▶ back-to-back [side_light, cool] | ◎ real-time",
    "neutral":    "",
    "friendly":   "attitude | ♪ mp, andante, legato | ▶ two-shot, parallel [diffused, amber, solid] | ◎ real-time",
    "devoted":    "attitude | ♪ mp, adagio, legato | ▶ close-up, pillow [golden_hour, amber, solid] | ◎ real-time",
}


def translate_npc_attitudes(attitudes: Optional[dict]) -> str:
    """NPCAttitudes → ♪▶◎ notation + trajectory 방향 합성 + reason prose."""
    if not attitudes or not isinstance(attitudes, dict):
        return ""
    lines = []
    for name, att in attitudes.items():
        if not isinstance(att, dict):
            continue
        attitude = att.get("attitude", "neutral")
        trajectory = att.get("trajectory", "stable")
        reason = att.get("reason", "")
        notation = _ATTITUDE_NOTATION.get(attitude, "") if attitude else ""
        if not notation:
            if reason:
                lines.append(f"- {name}: {reason}")
            continue
        # trajectory → ♪ 방향 수식어 합성
        traj_dir = _TRAJECTORY_NOTATION.get(trajectory, "")
        if traj_dir:
            notation = notation.replace(" | ▶", f", {traj_dir} | ▶")
        lines.append(f"- {name}:")
        lines.append(f"  {notation}")
        if reason:
            lines.append(f"  {reason}")
        # B7: Trust Dynamics 비대칭 힌트
        if trajectory == "declining" and attitude in ("friendly", "devoted"):
            lines.append("  [trust fracture: it happens in one moment; the longer the calm held, the bigger the shock. Rebuilding needs more evidence than the first time.]")
        elif trajectory == "improving" and attitude in ("hostile", "unfriendly"):
            lines.append("  [trust rebuilding: skepticism is the default; only an accumulation of consistent action counts as evidence.]")
    return "\n".join(lines)


# =========================================================
# 6. connection_depth (Slot 17)
# =========================================================

_STAGE_NOTATION = {
    "Initial":     "distance | ♪ p, andante, staccato | ▶ wide, height-gap | ◎ real-time",
    "Warming":     "distance | ♪ mp, andante, legato, crescendo | ▶ two-shot | ◎ real-time",
    "Established": "distance | ♪ mf, andante, legato | ▶ two-shot, match-cut | ◎ real-time",
    "Intimate":    "distance | ♪ mp, adagio, legato | ▶ close-up, pillow | ◎ real-time",
    "Ruptured":    "distance | ♪ f, allegro, staccato | ▶ back-to-back, cut | ◎ freeze",
}


def translate_connection_depth(
    npc_name: str,
    stage_name: str,
    depth: int,
    tension: int,
    hint_en: str = "",
) -> str:
    """Connection depth → ♪▶◎ notation. tension > 50 → marcato 합성."""
    notation = _STAGE_NOTATION.get(stage_name, "")
    if not notation:
        return ""
    if tension > 50:
        notation = notation.replace(" | ▶", ", marcato | ▶")
    return f"- {npc_name}:\n  {notation}"


# =========================================================
# 7. IntimacyAnalysis (Slot 17)
# =========================================================

_WINDOW_NOTATION = {
    "within": "♪ mf, andante, legato | ◎ real-time",
    "above":  "♪ ff, presto, staccato | ◎ slow-motion",
    "below":  "♪ pp, largo, legato | ◎ freeze",
}

_DESIRE_HINTS = {
    "attachment": "wants to be reassured; distance makes them anxious",
    "power": "wants the upper hand; reaching to hold the situation",
    "escape": "wants out of here; pulling away from this very spot",
    "connection": "wants to connect; real contact",
    "validation": "wants recognition; asking to be seen",
    "sensation": "wants to feel; the sensation itself is the want",
}


def translate_intimacy(intimacy_data: Optional[dict]) -> str:
    """IntimacyAnalysis → 프레임워크 라벨 제거, 행동 힌트로 변환."""
    if not intimacy_data or not isinstance(intimacy_data, dict):
        return ""
    lines = []

    # window_check → ♪◎ notation
    window = intimacy_data.get("window_check", intimacy_data.get("vulnerability", {}))
    if window and isinstance(window, dict):
        for char_name, state in window.items():
            state_lower = str(state).lower().strip()
            notation = _WINDOW_NOTATION.get(state_lower)
            if notation:
                lines.append(f"- {char_name}: {notation}")
            else:
                lines.append(f"- {char_name}: {state}")

    # dual_control (SES/SIS 라벨 제거)
    dual = intimacy_data.get("dual_control", {})
    if dual and isinstance(dual, dict):
        for char_name, controls in dual.items():
            if not isinstance(controls, dict):
                continue
            ses = controls.get("SES", "")
            sis = controls.get("SIS", "")
            if ses:
                lines.append(f"- {char_name} — what draws them in: {ses}")
            if sis:
                lines.append(f"- {char_name} — what makes them stop: {sis}")

    # desire_type
    desire = intimacy_data.get("desire_type", {})
    if desire and isinstance(desire, dict):
        for char_name, dtype in desire.items():
            dtype_lower = str(dtype).lower().strip()
            hint = _DESIRE_HINTS.get(dtype_lower, dtype)
            lines.append(f"- {char_name} motive: {hint}")

    # power_dynamic (한국어 통과)
    power = intimacy_data.get("power_dynamic", "")
    if power:
        lines.append(f"- relational dynamic: {power}")

    # body_memory (한국어 통과)
    body_mem = intimacy_data.get("body_memory", "")
    if body_mem:
        lines.append(f"- body memory: {body_mem}")

    # post_encounter_prediction: 친밀씬 후 가능한 행동 패턴 (확정 아님)
    post_pred = intimacy_data.get("post_encounter_prediction", {})
    if post_pred and isinstance(post_pred, dict):
        for char_name, prediction in post_pred.items():
            if prediction and isinstance(prediction, str) and prediction.lower() != "null":
                lines.append(f"- {char_name} possible reaction afterward (varies with character and relational pattern): {prediction}")

    return "\n".join(lines)


# =========================================================
# 8. emotion_intensity (Slot 29)
# =========================================================

_STAGE_PACING = {
    "escalating":         "accelerating, not yet at peak; tension builds, the decisive moment held back.",
    "rising":             "rising, a gradual accumulation; the next stimulus lands amplified.",
    "declining_from_peak": "just past peak, residual; easing over 2-3 turns, snapping back to acceleration if the same stimulus returns.",
    "sustained":          "sustained; the chance of a crack accumulating.",
    "fading":             "easing; full resolution within one scene is rare.",
}


def translate_emotion_intensity(
    psyche_states: Optional[dict],
    prev_psyche_values: Optional[Dict[str, int]] = None,
) -> str:
    """psyche value 턴간 *추세*(델타) → B4 stage pacing notation.

    [2026-06-22 A3] 강도 절대값(intensity tier)은 Slot 16 build_emotion_context(emotion_engine 정규화본)가
    소유 → 여기선 중복 제거하고 *추세만* 낸다. 변화(delta) 있는 NPC만 출력 → 블록 축소.
    """
    if not psyche_states or not isinstance(psyche_states, dict):
        return ""
    prev = prev_psyche_values or {}
    lines = []
    for name, pdata in psyche_states.items():
        if not isinstance(pdata, dict):
            continue
        psyche = pdata.get("psyche", pdata.get("mental", {}))
        if not isinstance(psyche, dict):
            continue
        # 추세는 이전 턴 값이 있어야 산출. 없으면 생략(강도는 Slot 16이 이미 보여줌).
        prev_val = prev.get(name)
        if prev_val is None:
            continue
        val = abs(psyche.get("value", 0))
        try:
            delta = val - abs(int(prev_val))
        except (ValueError, TypeError):
            continue
        if delta > 15:
            trend = "escalating"
        elif delta > 5:
            trend = "rising"
        elif delta < -5 and val > 40:
            trend = "declining_from_peak"
        elif abs(delta) <= 5:
            continue  # sustained = 변화 없음 → 생략(과소비 방지)
        else:
            trend = "fading"
        pacing = _STAGE_PACING.get(trend, "")
        if pacing:
            lines.append(f"  {name}: {pacing}")
    if not lines:
        return ""
    return (
        "[emotion trend]\n"
        "emotion lives in the body; how it moves this turn (the rise or the ebb) shows through what the body does, not a stated number.\n"
        + "\n".join(lines)
    )


# =========================================================
# 9. vigor/composure contrast (Slot 29)
# =========================================================

def translate_vigor_composure(vigor: int, composure: int) -> str:
    """컨디션 두 축의 괴리가 클 때 방향만 전달. 스탯명·수치·예시 없이, 몸으로만."""
    gap = abs(vigor - composure)
    if gap < 30:
        return ""
    if vigor < composure:
        return "body at its limit, the surface holding; the gap shows in movement, unnamed."
    return "surface intact, the inside shaking; the gap shows in movement, unnamed."


# =========================================================
# 10. gm_move (Slot 30)
# =========================================================

_DETAIL_DENSITY = {
    "combat": "dense", "intimate": "dense",
    "social": "moderate", "normal": "moderate",
    "exploration": "moderate", "rest": "sparse", "summary": "sparse",
}
_DENSITY_KR = {
    "dense": "high detail density; physical and sensory specifics render in full.",
    "moderate": "moderate detail; the key setting and only the senses it needs.",
    "sparse": "minimal detail; only the key events and the transitions.",
}


def translate_detail_density(scene_type: str = "normal", energy: str = "idle") -> str:
    """scene_type + energy → 디테일 밀도 힌트."""
    base = _DETAIL_DENSITY.get(scene_type, "moderate")
    # energy 보정: detonation/aftershock → +1, idle → -1
    density_order = ["sparse", "moderate", "dense"]
    idx = density_order.index(base) if base in density_order else 1
    if energy in ("detonation", "aftershock"):
        idx = min(idx + 1, 2)
    elif energy == "idle":
        idx = max(idx - 1, 0)
    final = density_order[idx]
    return _DENSITY_KR.get(final, "")


# translate_gm_move 제거 (2026-07-02): dai["gm_move"] 생산자가 Theoria 스키마에서 사라져
# 리더(slot_manager Slot 30)가 매 턴 빈손 호출이었음. 판정 기반 무브는 une_facade._mc_move가
# 담당 — 별개 라인, 무영향. 부활 시 Theoria 스키마에 생산 필드부터 복원할 것.


def translate_offscreen_trace(trace: Optional[dict]) -> str:
    """[2026-07-02 Offscreen Motion — 뮈토스 이식] 부재 캐스트의 흔적 → Slot 30 장면 압력 힌트.

    입력: dai["offscreen_trace"] = {"name", "movement", "visible_sign"} or None (null이 상례).
    출력: 흔적만 장면에 닿게 하는 디렉티브. 도착/폭로/사인 확정 금지 프레임 내장.
    """
    if not trace or not isinstance(trace, dict):
        return ""
    name = str(trace.get("name", "") or "").strip()
    sign = str(trace.get("visible_sign", "") or "").strip()
    movement = str(trace.get("movement", "") or "").strip()
    if not name or not (sign or movement):
        return ""
    line = sign if sign else movement
    return (
        f"[offscreen] {name}: {line}\n"
        "It reaches the scene only as a trace (rumor, delay, absence, changed readiness); "
        "the cause stays uncertain. No arrival, no reveal: the world has simply kept moving, "
        "and the scene may notice or ignore it."
    )


# =========================================================
# 11. telescope [Who] (Slot 34 prefill)
# =========================================================

def translate_telescope_who(psyche_states: Optional[dict]) -> str:
    """프리필 — 이름만 반환 (라벨은 호출자가 씌움)."""
    if not psyche_states or not isinstance(psyche_states, dict):
        return ""
    names = [str(name) for name in psyche_states]
    return " | ".join(names) if names else ""


# =========================================================
# 12. narrative_chain (Slot 28)
# =========================================================

_CHAIN_STATUS_HINTS = {
    "OPEN": "exchange open",
    "CLOSED": "exchange closed",
    "DORMANT": "exchange dormant; the thread rests, not resolved",
}

# conclusion_proximity: 0-100 → 서사 페이싱 힌트
_PROXIMITY_HINTS = [
    (20, ""),  # still far off — no hint needed
    (45, "the narrative is unfolding; a new thread can be let loose."),
    (70, "tension is climbing; the existing threads tighten rather than new ones opening."),
    (90, "the climax is near; every action carries weight."),
    (100, "the narrative is at its peak; every action produces a consequence."),
]

_SILENCE_NOTATION = {
    "reflective": "♪ pp, adagio, legato | ◎ long-exposure",
    "hesitant":   "♪ p, andante, staccato | ◎ slow-motion",
    "heavy":      "♪ pp, largo, legato | ◎ freeze",
    "tense":      "♪ p, allegro, staccato | ◎ slow-motion",
}


def translate_narrative_chain(chain_data: Optional[dict]) -> str:
    """narrative_chain 라벨 → prose + silence notation."""
    if not chain_data or not isinstance(chain_data, dict):
        return ""
    parts = []

    # chain_status
    status = chain_data.get("chain_status", "OPEN")
    status_hint = _CHAIN_STATUS_HINTS.get(status, status)
    parts.append(status_hint)

    # topic_lock
    topic = chain_data.get("topic_lock")
    if topic and str(topic).lower() != "none":
        parts.append(f"topic: {topic}")

    # conclusion_proximity: 0-100 → 페이싱 힌트
    proximity = chain_data.get("conclusion_proximity")
    if proximity is not None and isinstance(proximity, (int, float)) and proximity > 20:
        for threshold, hint in _PROXIMITY_HINTS:
            if proximity <= threshold:
                if hint:
                    parts.append(hint)
                break

    result = ". ".join(parts) + "." if parts else ""

    # silence_type → ♪◎ notation (별도 줄)
    silence = chain_data.get("silence_type")
    if silence and isinstance(silence, str):
        silence_n = _SILENCE_NOTATION.get(silence.lower())
        if silence_n:
            result = f"{result}\n  {silence_n}" if result else f"  {silence_n}"

    return result


# =========================================================
# 13. open_threads (Slot 28)
# =========================================================

_THREAD_CATEGORY_RE = re.compile(
    r"^(Mystery|Threat|Desire|Interpersonal|Goal|Fear|Secret|Conflict)\s*:\s*",
    re.IGNORECASE,
)


def translate_open_threads(threads: Optional[list]) -> str:
    """open_threads에서 카테고리 라벨(Mystery:, Threat: 등) 제거."""
    if not threads or not isinstance(threads, list):
        return ""
    cleaned = []
    for t in threads:
        if not isinstance(t, str) or not t.strip():
            continue
        cleaned_thread = _THREAD_CATEGORY_RE.sub("", t.strip())
        cleaned.append(f"- {cleaned_thread}")
    return "\n".join(cleaned)


# =========================================================
# 14. trait_connections (Slot 16 Apophenia Guard)
# =========================================================

def translate_trait_connections(trait_conn: Optional[dict]) -> str:
    """OBVIOUS= 라벨 → 한국어, 나머지 구조 유지."""
    if not trait_conn or not isinstance(trait_conn, dict):
        return ""
    lines = []
    for npc_name, conn in trait_conn.items():
        if not isinstance(conn, dict):
            continue
        primary = conn.get("primary_link", "")
        deflection = conn.get("deflection", "")
        if not primary or not deflection:
            continue
        # trait_pair: 어떤 특질 조합이 연결되는지 표시
        pair = conn.get("trait_pair", "")
        prefix = f"[{pair}] " if pair else ""
        line = f"- {npc_name}: {prefix}instead of the obvious direction ({primary}) → {deflection}"
        hint = conn.get("render_hint", "")
        if hint:
            line += f" | {hint}"
        lines.append(line)
    if not lines:
        return ""
    return (
        "### obvious-link edge\n"
        "the obvious link is a cliché; the prose refracts toward the suggested direction instead.\n"
        + "\n".join(lines)
    )


# =========================================================
# NPCKnowledge (Slot 17) — 예측 라벨만 제거
# =========================================================

def _secret_surfaces(info: dict) -> list:
    """[V10 Secret Ledger 게이트① 2026-07-14] 렌더러 공급용 비밀 표시 목록.
    secret_updates에 surface가 있으면 truth 대신 surface(겉모습)를 공급 —
    렌더러가 진실 문자열을 모르면 산문 누출 자체가 불가능. surface 없으면
    truth 폴백(기존 동작 무변경 = 회귀 0)."""
    secrets = info.get("secrets_held", [])
    if not secrets or not isinstance(secrets, list):
        return []
    surface_map = {}
    for up in (info.get("secret_updates") or []):
        if isinstance(up, dict) and up.get("surface") and up.get("truth_ref"):
            surface_map[str(up["truth_ref"]).strip().lower()] = str(up["surface"])
    out = []
    for s in secrets:
        s = str(s)
        matched = next((v for k, v in surface_map.items() if k and k in s.lower()), None)
        out.append(matched if matched else s)
    return out


def translate_npc_knowledge(npc_knowledge: Optional[dict]) -> str:
    """NPCKnowledge — knows/secrets/false_beliefs/deception_cues/would_share 유지. leak_risk는 compose_dialogue_directives에서 소비."""
    if not npc_knowledge or not isinstance(npc_knowledge, dict):
        return ""
    lines = []
    for npc_name, info in npc_knowledge.items():
        if not isinstance(info, dict):
            continue
        parts_k = []
        knows = info.get("knows", [])
        if knows and isinstance(knows, list):
            parts_k.append(f"  knows: {', '.join(str(k) for k in knows)}")
        # [V10 지식 lite] suspects(의심, 불확실) — 플래그 ON 시만. 확신(knows)과 구분.
        try:
            import config as _cfg_kb
            if getattr(_cfg_kb, "V10_KNOWLEDGE_BOUNDARY_INJECT", False):
                suspects = info.get("suspects", [])
                if suspects and isinstance(suspects, list):
                    parts_k.append(f"  suspects (unsure): {', '.join(str(s) for s in suspects)}")
        except Exception:
            pass
        secrets = _secret_surfaces(info)
        if secrets:
            parts_k.append(f"  hides: {', '.join(secrets)}")
        false_beliefs = info.get("false_beliefs", [])
        if false_beliefs and isinstance(false_beliefs, list):
            parts_k.append(f"  believes wrongly: {', '.join(str(f) for f in false_beliefs)}")
        deception = info.get("deception_cues")
        if isinstance(deception, str) and deception.strip() and deception.strip().lower() != "null":
            parts_k.append(f"  tells of lying: {deception.strip()}")
        elif isinstance(deception, list) and deception:
            parts_k.append(f"  tells of lying: {', '.join(str(d) for d in deception)}")
        # would_share: NPC가 자발적으로 정보를 공유하려는 의향
        if info.get("would_share"):
            parts_k.append("  wants to tell it themselves; given the chance, it comes out naturally")
        if parts_k:
            lines.append(f"- {npc_name}\n" + "\n".join(parts_k))
    if not lines:
        return ""
    return (
        "### NPC knowledge state\n"
        "(what an NPC knows or hides shapes its behavior; the concept itself stays out of the prose, surfacing only as action.)\n"
        + "\n".join(lines)
    )


# =========================================================
# 15. Dialogue Directives (Slot 17) — 대사 방향 지시
# =========================================================

_STRATEGY_HINTS = {
    # coping (Lazarus)
    "problem_focused": "directly",
    "emotion_focused": "circling through emotion",
    "avoidant": "changing the subject",
    # stage (Goffman)
    "front": "keeping up appearances",
    "back": "without pretense",
    # decision_mode (Kahneman)
    "reactive": "off the cuff",
    "deliberate": "calculating",
    # negotiation_stance (NEGOTIATION 모듈)
    "cooperative": "cooperatively",
    "competitive": "cutting in first",
    "exploitative": "pressing the weak spot in what's said",
    # group_dynamic (GROUP_DYNAMICS 모듈)
    "conformity": "speaking only after the room agrees",
    "obedience": "answering short, as told",
    "groupthink": "echoing others' conclusions as their own",
    "diffusion": "blurring the subject, pointing at someone else",
}

# relation.phase → 관계 단계별 대화 전략 힌트
_PHASE_HINTS = {
    "orientation": "feeling it out — carefully drawing the lines",
    "identification": "building rapport — looking for common ground",
    "exploitation": "drawing on the bond — asking easily, leaning on it",
    "resolution": "winding down — retracing what the relationship meant",
}

_NEEDS_HINTS = {
    "safety": "to secure safety",
    "belonging": "to gain belonging",
    "esteem": "to win recognition",
    "autonomy": "to protect autonomy",
    "competence": "to prove competence",
    "relatedness": "to form a bond",
    "trust": "to build trust",
    "identity": "to confirm identity",
    "control": "to take control",
    "understanding": "to read the other",
    "intimacy": "to close the distance",
    "power": "to gain the upper hand",
    "survival": "to survive",
    "justice": "to uphold fairness",
    "meaning": "to find meaning",
}

# relation.attachment → 소유욕 대체 행동 힌트 (secure=없음, non-secure=구체 행동)
_ATTACHMENT_POSSESSIVENESS = {
    "secure":       "",
    "anxious":      "asks first when distance opens, and asks again when the answer is slow",
    "avoidant":     "steps back when feeling runs high, looks away when it gets close",
    "disorganized": "moves close then suddenly pulls away, treats the same person differently",
}

_FRAMEWORK_TERMS_RE = re.compile(
    r'\b(membrane|monolithic|interleaving|fracture|collapse|logos|layer|'
    r'peplau|goffman|bowlby|lazarus|kahneman|erikson|henderson|'
    r'front stage|back stage|orientation phase|identification phase|'
    r'exploitation phase|resolution phase|'
    r'ventral|sympathetic|dorsal)\b',
    re.IGNORECASE,
)


def _strip_framework_terms(text: str) -> str:
    """logos_layer 등에서 프레임워크 용어 제거."""
    result = _FRAMEWORK_TERMS_RE.sub('', text)
    result = re.sub(r'\s+', ' ', result).strip()
    result = re.sub(r'^[\s—\-:]+|[\s—\-:]+$', '', result)
    return result


def _extract_actual(opacity_str: str) -> str:
    """'claims X — actual: Y' 형식에서 Y만 추출."""
    low = opacity_str.lower()
    if "actual:" in low:
        idx = low.index("actual:")
        return opacity_str[idx + 7:].strip().rstrip(".")
    if "—" in opacity_str:
        return opacity_str.split("—")[-1].strip().rstrip(".")
    return ""


def compose_dialogue_directives(
    psyche_states: Optional[dict],
    npc_knowledge: Optional[dict],
    prev_gaze: str = "",
    npc_depths: Optional[Dict[str, float]] = None,
    npc_imprints: Optional[Dict[str, list]] = None,
    voice_quirks: Optional[Dict[str, str]] = None,
) -> str:
    """psyche_states + NPCKnowledge + 이전 gaze → NPC별 대사 방향 지시.

    Gaze 심도:
      이름 in gaze → full directive (전 축)
      이름 not in gaze → minimal (logos_layer만)
      gaze 없음 (첫 턴) → moderate (logos_layer + purpose)

    Returns: Slot 17 주입용 한국어 텍스트. ~60-100 tokens for 2-3 NPCs.
    """
    if not psyche_states or not isinstance(psyche_states, dict):
        return ""

    knowledge = npc_knowledge if isinstance(npc_knowledge, dict) else {}
    has_gaze = bool(prev_gaze and prev_gaze.strip())

    lines = []
    for name, state in psyche_states.items():
        if not isinstance(state, dict):
            continue

        # 얕은 수면(depth >= 0.8) → skip
        if npc_depths and npc_depths.get(name, 0.5) >= 0.8:
            continue

        relation = state.get("relation", {})
        if not isinstance(relation, dict):
            relation = {}
        psyche = state.get("psyche", {})
        if not isinstance(psyche, dict):
            psyche = {}

        # logos_layer 없으면 skip (분석 안 된 NPC)
        logos = relation.get("logos_layer", "")
        if not logos or not isinstance(logos, str):
            continue

        # Gaze 기반 심도 결정 (exact match — substring 위양성 방지)
        if has_gaze:
            _gaze_names = {g.strip() for g in prev_gaze.replace("\n", ",").split(",") if g.strip()}
            in_focus = name in _gaze_names
        else:
            in_focus = True  # 첫 턴: moderate for all

        # === MINIMAL DIRECTIVE (배경 NPC) ===
        if has_gaze and not in_focus:
            clean_logos = _strip_framework_terms(logos)
            if clean_logos:
                lines.append(f"- {name}: {clean_logos}")
            continue

        # === FULL / MODERATE DIRECTIVE (초점 NPC) ===
        directive_parts = []

        # 목적 (Purpose): active_needs → Korean
        needs = psyche.get("active_needs", [])
        if isinstance(needs, list) and needs:
            need_hints = []
            for n in needs[:2]:
                hint = _NEEDS_HINTS.get(n.lower().strip(), "")
                if hint:
                    need_hints.append(hint)
                elif n.strip():
                    need_hints.append(n.strip())  # fallback: raw need
            if need_hints:
                directive_parts.append(" ".join(need_hints))

        # 전략 (Strategy): logos_layer (core)
        clean_logos = _strip_framework_terms(logos)
        if clean_logos:
            directive_parts.append(clean_logos)

        # 관계 단계 (phase): 대화 전략의 기저 톤
        phase = relation.get("phase", "")
        if phase and isinstance(phase, str) and phase != "null":
            phase_hint = _PHASE_HINTS.get(phase.lower().strip(), "")
            if phase_hint:
                directive_parts.append(phase_hint)

        # 애착 유형 → 소유욕 대체 행동
        attachment = relation.get("attachment", "")
        if attachment and isinstance(attachment, str) and attachment != "null":
            att_hint = _ATTACHMENT_POSSESSIVENESS.get(attachment.lower().strip(), "")
            if att_hint:
                directive_parts.append(att_hint)

        # 전략 수식어: coping, decision_mode, stage, negotiation_stance, group_dynamic
        strategy_mods = []
        for field in ("coping", "decision_mode"):
            val = psyche.get(field, "")
            if val and isinstance(val, str) and val != "null":
                mod = _STRATEGY_HINTS.get(val, "")
                if mod:
                    strategy_mods.append(mod)
        for field in ("stage", "negotiation_stance", "group_dynamic"):
            val = relation.get(field, "")
            if val and isinstance(val, str) and val != "null":
                mod = _STRATEGY_HINTS.get(val, "")
                if mod:
                    strategy_mods.append(mod)
        if strategy_mods:
            directive_parts.append(", ".join(strategy_mods))

        # 숨김 (Hidden): self_opacity
        # [Sprint J 2026-04-28] depth < 0.4 시 추상 압축 — 거울공방 axiom (named+explained=죽음)
        # 더 깊은 NPC는 alpha 그대로 박지 X. signal은 보존, 라벨화 약화
        _depth_for_npc = (npc_depths or {}).get(name, 0.5)
        opacity = psyche.get("self_opacity")
        if opacity and isinstance(opacity, str) and opacity != "null":
            actual = _extract_actual(opacity)
            if actual:
                if _depth_for_npc < 0.4:
                    directive_parts.append("(interior sealed)")
                else:
                    directive_parts.append(f"(actually {actual})")

        # 숨김: apprehension_gap (인식 왜곡)
        ag = psyche.get("apprehension_gap")
        if ag and isinstance(ag, str) and ag != "null":
            if _depth_for_npc < 0.4:
                directive_parts.append("(perception blurred)")
            else:
                directive_parts.append(f"(perception skewed: {ag})")

        # 숨김: NPCKnowledge (leak_risk >= medium 일 때만)
        nk = knowledge.get(name, {})
        if isinstance(nk, dict):
            leak = nk.get("leak_risk", "none")
            if leak in ("medium", "high"):
                secrets = _secret_surfaces(nk)
                if secrets:
                    directive_parts.append(f"hiding: {secrets[0]}")
                false_b = nk.get("false_beliefs", [])
                if false_b and isinstance(false_b, list) and false_b[0]:
                    directive_parts.append(f"wrongly believing: {false_b[0]}")

        # 갈등 (value_conflict)
        vc = relation.get("value_conflict")
        if vc and isinstance(vc, str) and vc != "null":
            conflict = vc.split("+")[0].strip() if "+" in vc else vc
            directive_parts.append(f"conflict: {conflict}")

        # 행동 각인 (imprints) — 최근 1-2개만
        if npc_imprints and isinstance(npc_imprints, dict):
            imp_list = npc_imprints.get(name, [])
            if isinstance(imp_list, list):
                for imp in imp_list[-2:]:
                    if isinstance(imp, dict) and imp.get("mark"):
                        directive_parts.append(f"imprint: {imp['mark']}")

        # 말투 (voice quirks) — gaze=Full인 NPC만 (in_focus)
        if voice_quirks and isinstance(voice_quirks, dict) and in_focus:
            vq = voice_quirks.get(name, "")
            if vq:
                directive_parts.append(f"voice: {vq}")

        if directive_parts:
            lines.append(f"- {name}: {'. '.join(directive_parts)}")

    if not lines:
        return ""

    return (
        "### dialogue direction\n"
        "(the aim and strategy behind an NPC's lines; the terms stay out of the prose, the dialogue itself performing them.)\n"
        + "\n".join(lines)
    )
