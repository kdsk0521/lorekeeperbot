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
    return tiers[-1][1] if tiers else "알 수 없음"



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
    "Surface": "▸평소(80%)",
    "Adaptation": "▸반복패턴",
    "Core": "▸극한에서만",
    "Lack": "▸직접 드러내지 마",
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
        # ▸평소만
        lines = renamed.split("▸")
        kept = [lines[0]]  # prefix before first ▸
        for part in lines[1:]:
            if part.startswith("평소"):
                kept.append("▸" + part)
        return "".join(kept).strip()
    if depth >= 0.4:
        # ▸평소 + ▸반복패턴
        lines = renamed.split("▸")
        kept = [lines[0]]
        for part in lines[1:]:
            if part.startswith("평소") or part.startswith("반복패턴"):
                kept.append("▸" + part)
        return "".join(kept).strip()
    if depth >= 0.2:
        # ▸극한에서만 포함
        lines = renamed.split("▸")
        kept = [lines[0]]
        for part in lines[1:]:
            if not part.startswith("직접 드러내지"):
                kept.append("▸" + part)
        return "".join(kept).strip()
    # depth < 0.2: 전부 노출
    return renamed


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
                notations.append(pvg_n)
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
                    lines.append("    [환원불가: 이 캐릭터, 이 순간에만 가능한 반응으로. 수행(performed) 아닌 환원불가(irreducible).]")
                elif depth < 0.4:
                    lines.append("    [행동/반사/신체반응으로만. 서술자 명명·심리용어 금지.]")

        # resurfacing: depth < 0.6일 때만 노출 (intimate/social)
        resurface = state.get("resurfacing")
        if resurface and isinstance(resurface, str) and resurface != "null" and depth < 0.6:
            lines.append(f"  └ 재부상: {resurface}")

    return "\n".join(lines)


# =========================================================
# 2. position/effect (Slot 13) + friction 통합
# =========================================================

_POS_TIERS = [
    (0.2, "절망적"),
    (0.4, "불리"),
    (0.6, "보통"),
    (0.8, "유리"),
    (1.0, "지배적"),
]

_POS_FRICTION = {
    "절망적": "장벽은 실제로 존재한다. 서사적 편의가 아닌 세계의 논리를 따르라.",
    "불리": "현재 상황에는 비용이 따른다. 그 비용을 정직하게 반영하라.",
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
        line = f"상황: {tier} — {reason}" if reason else f"상황: {tier}"
        friction = _POS_FRICTION.get(tier, "")
        if friction:
            line += f"\n  └ {friction}"
        parts.append(line)
    if effect and isinstance(effect, dict):
        tier = _to_tier(effect.get("value", 0.5), _POS_TIERS)
        reason = effect.get("reason", "")
        parts.append(f"영향력: {tier} — {reason}" if reason else f"영향력: {tier}")
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
    "idle":       "1 beat (sensory)",
    "rising":     "1-2 beats (exchange+texture)",
    "detonation": "2 beats (action+consequence)",
    "stagnant":   "1 beat (sensory)",
    "aftershock": "1 beat (residue)",
}

_ENERGY_END = {
    "idle":       "stillness",
    "rising":     "render tension weight",
    "aftershock": "residue, no verdict",
}


def translate_energy_direction(energy: str) -> str:
    """energy_direction → light + tone + beat + end (conditional injection).
    Replaces static PACING_CONTROL_PROTOCOL constant."""
    if not energy:
        return ""
    key = energy.lower().strip()

    parts = []
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
    "push":    "박자를 올려라 — 다음 비트로 빠르게 전진",
    "hold":    "현재 리듬 유지 — 성급히 전진하지 마라",
    "breathe": "숨을 돌려라 — 속도를 늦추고 여운을 남겨라",
    "pivot":   "방향 전환 — 새로운 톤이나 장소로 전이하라",
}

_TENSION_KR = {
    "critical":  "극한 긴장 — 모든 서술에 무게를 실어라",
    "rising":    "긴장 상승 — 압박을 점진적으로 강화하라",
    "plateau":   "긴장 유지 — 불안한 안정, 뭔가 올 것 같은 기류",
    "falling":   "긴장 해소 — 이완과 성찰의 공간을 확보하라",
}

_CUT_KR = {
    "hard_cut":        "하드 컷: 빠르게 다음 비트로 전환",
    "fade":            "페이드: 서서히 전환, 여운이 머물게",
    "contrast_cut":    "대비 컷: 톤/장소를 의도적으로 전환",
    "natural":         "자연 전환: 흐름을 따르라",
    "dramatic_entrance": "극적 등장: 이벤트가 장면을 깨뜨린다",
}

_IDLE_SOURCE_KR = {
    "active_condition":  "진행 중인 상황을 확대하라",
    "narrative_chain":   "서사 체인을 이어가라",
    "emotion":           "감정이 격한 NPC가 주도하게 하라",
    "doom":              "환경 압력을 서사적으로 표현하라",
    "ambient":           "세계가 스스로 움직이게 하라 — 소소한 진행",
}


def translate_register(register) -> str:
    """scene_register → Korean rendering hint for Pro."""
    if not register or not isinstance(register, str):
        return ""
    _REGISTER_KR = {
        "mirror": "[거울] 인물이 상대에게서 자신을 보되 자각하지 못한다 — 특질과 오인을 구체적으로 렌더링하라",
        "law": "[법칙] 위계/기대/프로토콜이 입력에 의해 휘어진다 — 질서, 균열, 모른 척하는 자를 렌더링하라",
        "remainder": "[잔여] 장면이 대화나 행동으로 소화할 수 없는 것 — 감각, 반복, 플롯에 봉사하지 않는 디테일을 남겨라",
    }
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
        # mirror: self_opacity 높고 + apprehension_gap 존재
        opacity = psyche.get("self_opacity", 0)
        try:
            opacity = float(opacity)
        except (ValueError, TypeError):
            opacity = 0
        if opacity > 0.6 and psyche.get("apprehension_gap"):
            return "mirror"
        # law: value_conflict + defensive/aggressive stance
        if relation.get("value_conflict") and relation.get("negotiation_stance") in (
            "defensive", "aggressive",
        ):
            return "law"
    # remainder: silence_type in narrative_chain
    if narrative_chain and isinstance(narrative_chain, dict):
        if narrative_chain.get("silence_type"):
            return "remainder"
    return ""


# ----- B5: Propagation Shape (장면 전파 형태) -----

_PROPAGATION_KR = {
    "compression": "[압축파] 충격이 대상에게 가장 강하고 주변으로 감쇠. 직격의 무게를 먼저, 잔향은 느리게.",
    "radiation": "[방사] 충격이 전방향으로 퍼진다. 각 인물의 개별 반응을 추적하라.",
    "oscillation": "[왕복] 자극↔반응이 교대. 리듬적으로 묘사하되 매 왕복마다 점진 변화를 추적하라.",
    "convergence": "[수렴] 복수의 힘이 한 점으로. 압력 누적을 보여주되 결과는 개별 힘의 합 이상.",
    "divergence": "[발산] 하나의 결정이 갈래로 쪼개진다. 각 경로를 개별 추적하라.",
    "torsion": "[비틀림] 겉과 속이 다른 방향. 표면의 말과 이면의 힘을 동시에 렌더링하라.",
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

    # torsion: 기만/자기기만이 활성 — 최우선 (다른 전파와 겹쳐도 비틀림이 지배)
    for _name, state in psyche_states.items():
        if not isinstance(state, dict):
            continue
        psyche = state.get("psyche", state.get("mental", {})) or {}
        relation = state.get("relation", {}) or {}
        if relation.get("deception_cues"):
            return "torsion"
        try:
            opacity = float(psyche.get("self_opacity", 0))
        except (ValueError, TypeError):
            opacity = 0
        if opacity > 0.7:
            return "torsion"

    # convergence: open_threads 복수 수렴 + 높은 에너지
    chain = narrative_chain or {}
    threads = chain.get("open_threads", [])
    if isinstance(threads, list) and len(threads) >= 3 and energy in ("detonation", "aftershock"):
        return "convergence"

    # divergence: 선택/분기 + NPC별 반응 상이
    if chain.get("chain_status") in ("branching", "diverging"):
        return "divergence"

    # radiation: 3명+ NPC + 공개적 장면
    if npc_count >= 3 and scene_type in ("social", "normal", "combat"):
        return "radiation"

    # oscillation: 대화 중심 + negotiation 활성
    for _name, state in psyche_states.items():
        if not isinstance(state, dict):
            continue
        relation = state.get("relation", {}) or {}
        if relation.get("negotiation_stance") in ("defensive", "aggressive", "cooperative"):
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
            parts.append(f"장면 전이 제안: → {suggest}")

    # Focus spotlight
    focus = story_dir.get("focus", {})
    if isinstance(focus, dict):
        spotlight = focus.get("spotlight", "none")
        if spotlight and spotlight != "none":
            reason = focus.get("reason", "")
            parts.append(f"초점: {spotlight}" + (f" ({reason})" if reason else ""))

    # Idle direction (proactive scene guidance)
    idle_dir = story_dir.get("idle_direction")
    if idle_dir and isinstance(idle_dir, dict):
        source = idle_dir.get("source", "ambient")
        idle_hint = _IDLE_SOURCE_KR.get(source, "")
        if idle_hint:
            parts.append(f"[능동 전개] {idle_hint}")
        npc = idle_dir.get("npc", "")
        if npc:
            parts.append(f"주도 NPC: {npc}")

    # Seven Dice (W9) — 은닉 4면만 Slot 16 분위기로. 가시 3면은 Slot 19(WRITING_DIRECTIVES) 경로.
    dice = story_dir.get("dice")
    if dice and isinstance(dice, dict) and not dice.get("visible"):
        effect = dice.get("effect", "")
        if effect:
            parts.append(f"[서사 저류] {effect}")

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
    "convergence_warning": "관계 변화가 빠르다. 이 속도에 맞는 근거가 있는지 확인하라.",
    "echo_warning": "NPC가 PC 감정을 따라가고 있다. NPC 자신의 이유가 있는 반응인지 확인하라.",
    "stagnation_warning": "3턴째 장면 에너지가 평평하다. 외부 자극을 자연스럽게 도입하라.",
    "mse_deviation": "NPC 행동이 급변했다. 이전과 일관되는지 확인하고, 변화에 근거를 부여하라.",
    "dissonance_flag": "NPC의 말과 행동이 어긋나고 있다. 그 어긋남이 해소되지 않은 채 몸짓·표정·호흡의 작은 불일치로 남는다.",
    "redemption_warning": "NPC가 근거 없이 누그러지고 있다. 이전 패턴을 유지하라.",
    "shallow_read": "분석이 표면에 머물렀다. 드러난 행동 아래를 더 보라 — 입 밖에 안 낸 것, 공간이 주는 압박, 갚지 못한 빚.",
    "sensory_habituated": "같은 공간에서 감각이 적응했다. 동일한 감각을 반복하지 말고, 미세한 변화를 포착하거나 새로운 감각 채널로 전환하라.",
    "label_internalization": "NPC가 자기에게 붙은 라벨을 믿기 시작했다. 라벨을 입으로 말하지 마 — 습관, 자세, 반응으로 보여줘라.",
    "sheet_deducible": "⚠️ 자판기 위험: 반응이 시트 태그의 직역. 이 캐릭터, 이 순간에만 가능한 구체적 반응을 찾을 것.",
}

_SYMPTOM_TEMPLATE = "NPC가 {cluster} 증상을 보이고 있다. 한 세트로 일관되게 유지하라."

# =========================================================
# 시간 방향 번역
# =========================================================

_TEMPORAL_KR = {
    "past": "인물의 시선이 과거를 향한다",
    "future": "인물의 시선이 앞을 향한다",
    "present": "인물이 지금 이 순간에 머문다",
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
        hint += " — 강하게"
    return f"### 시간 방향\n{hint}"


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
    "spatial_break": "공간 불연속",
    "sensory_break": "감각 불연속",
    "object_break": "사물 불연속",
    "tone_break": "분위기 불연속",
    "npc_break": "인물 불연속",
    "rhythm_break": "리듬 불연속",
}

_SHIFT_HINTS = {
    "gradual": "빛/색이 서서히 바뀌고 있다",
    "sudden": "빛/색이 급변했다 — 신체 충격 수반",
}
_THRESHOLD_HINTS = {
    "mild": "공간이 바뀌었다 — 감각 한 문장 전환",
    "sharp": "감각 낙차가 크다 — 눈부심/한기/바람 등 신체 반응",
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
        lines.append(f"  [지각 편향] {flt} — 물리적 변화 아님")

    tension = spatial_read.get("tension")
    if tension and isinstance(tension, str) and tension != "null":
        lines.append(f"  [공간 간극] {tension}")

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
    header = ("### 공간 각인\n(배경 질감. 전개하지 마.)\n"
              if weight == "ambient" else
              "### 공간 각인\n(공간이 겪은 것을 감각으로 보여줘라. 분석 용어 쓰지 마.)\n")
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
        directives.append(f"- 소급: {rewrite}")
    # anchor_consumed: 이전 감각 앵커 소비 여부
    if check_data.get("anchor_consumed"):
        directives.append("- 이전 장면의 감각 앵커가 소비되었다. 새로운 감각 기점을 설정하라.")
    if not directives:
        return ""
    return ("### 씬 연속성 보정\n"
            "이전 장면과의 불연속이 감지되었다. 자연스러운 연결을 만들어라.\n"
            + "\n".join(directives))


_SCHEME_KR = {
    "deflection": "전환(농담/제스처로 회피)",
    "displacement": "치환(무관한 곳에서 폭발)",
    "circling": "선회(다른 각도에서 같은 것)",
    "substitution": "대체(비슷하지만 다른 것 제공)",
}

def translate_prev_scheme(prev_scheme: str) -> str:
    """render_fingerprint.withholding_scheme → 직전 수법 피드백 (Slot 16 주입)."""
    if not prev_scheme or prev_scheme == "none":
        return ""
    scheme_kr = _SCHEME_KR.get(prev_scheme, prev_scheme)
    return f"직전 보류 수법: {scheme_kr}"


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
            lines.append("  [신뢰 균열: 한 순간에 발생. 안정이 길었을수록 충격 더 큼. 재건은 최초보다 더 많은 증거 필요.]")
        elif trajectory == "improving" and attitude in ("hostile", "unfriendly"):
            lines.append("  [신뢰 재건 중: 회의가 기본값. 일관된 행동의 누적만이 증거.]")
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
    "attachment": "확인받고 싶다 — 거리가 생기면 불안",
    "power": "주도권을 쥐고 싶다 — 상황을 쥐려 한다",
    "escape": "여기서 벗어나고 싶다 — 지금 이 자리에서 빠지려 한다",
    "connection": "연결되고 싶다 — 진짜 접촉",
    "validation": "인정받고 싶다 — 나를 봐달라는 것",
    "sensation": "느끼고 싶다 — 감각 그 자체를 원한다",
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
                lines.append(f"- {char_name} — 끌어당기는 것: {ses}")
            if sis:
                lines.append(f"- {char_name} — 멈추게 하는 것: {sis}")

    # desire_type
    desire = intimacy_data.get("desire_type", {})
    if desire and isinstance(desire, dict):
        for char_name, dtype in desire.items():
            dtype_lower = str(dtype).lower().strip()
            hint = _DESIRE_HINTS.get(dtype_lower, dtype)
            lines.append(f"- {char_name} 동기: {hint}")

    # power_dynamic (한국어 통과)
    power = intimacy_data.get("power_dynamic", "")
    if power:
        lines.append(f"- 관계 역학: {power}")

    # body_memory (한국어 통과)
    body_mem = intimacy_data.get("body_memory", "")
    if body_mem:
        lines.append(f"- 신체 기억: {body_mem}")

    # post_encounter_prediction: 친밀씬 후 가능한 행동 패턴 (확정 아님)
    post_pred = intimacy_data.get("post_encounter_prediction", {})
    if post_pred and isinstance(post_pred, dict):
        for char_name, prediction in post_pred.items():
            if prediction and isinstance(prediction, str) and prediction.lower() != "null":
                lines.append(f"- {char_name} 이후 가능 반응 (성격·관계 패턴에 따라 다름): {prediction}")

    return "\n".join(lines)


# =========================================================
# 8. emotion_intensity (Slot 29)
# =========================================================

_INTENSITY_NOTATION = [
    (30,  "♪ pp"),
    (60,  "♪ mf"),
    (80,  "♪ f"),
    (100, "♪ ff"),
]


_STAGE_PACING = {
    "escalating":         "가속 중 — 정점 아님. 텐션 빌드업, 결정적 순간 유보.",
    "rising":             "상승 중 — 점진적 축적. 다음 자극의 효과가 증폭됨.",
    "declining_from_peak": "정점 직후 — 잔류. 2-3턴 서서히 해소. 같은 자극 재투입 시 즉시 가속 복귀.",
    "sustained":          "유지 — 균열 가능성 축적 중.",
    "fading":             "해소 중 — 완전 해소는 장면 내에서 드묾.",
}


def translate_emotion_intensity(
    psyche_states: Optional[dict],
    prev_psyche_values: Optional[Dict[str, int]] = None,
) -> str:
    """psyche value → ♪ dynamics notation + B4 stage pacing."""
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

        val = abs(psyche.get("value", 0))
        hint = _to_tier(val, _INTENSITY_NOTATION)
        line = f"  {name}: {hint}"

        # B4: 이전 턴 대비 stage pacing
        prev_val = prev.get(name)
        if prev_val is not None:
            try:
                delta = val - abs(int(prev_val))
            except (ValueError, TypeError):
                delta = 0
            if delta > 15:
                trend = "escalating"
            elif delta > 5:
                trend = "rising"
            elif delta < -5 and val > 40:
                trend = "declining_from_peak"
            elif abs(delta) <= 5:
                trend = "sustained"
            else:
                trend = "fading"
            pacing = _STAGE_PACING.get(trend, "")
            if pacing and trend != "sustained":
                line += f" — {pacing}"
        lines.append(line)
    if not lines:
        return ""
    return (
        "[감정 강도]\n"
        "감정은 몸으로 보여줘라. 감정명, 강도 라벨, 수치를 산문에 쓰지 마.\n"
        + "\n".join(lines)
    )


# =========================================================
# 9. vigor/composure contrast (Slot 29)
# =========================================================

def translate_vigor_composure(vigor: int, composure: int) -> str:
    """활력/평형 괴리가 클 때 사실만 전달."""
    gap = abs(vigor - composure)
    if gap < 30:
        return ""
    return "활력과 평형 사이에 큰 괴리가 있다. 행동으로 드러내라."


# =========================================================
# 10. gm_move (Slot 30)
# =========================================================

_DETAIL_DENSITY = {
    "combat": "dense", "intimate": "dense",
    "social": "moderate", "normal": "moderate",
    "exploration": "moderate", "rest": "sparse", "summary": "sparse",
}
_DENSITY_KR = {
    "dense": "디테일 밀도 높게 — 물리적/감각적 세부 전부 렌더링",
    "moderate": "디테일 적정 — 핵심 환경 + 필요 감각만",
    "sparse": "디테일 최소 — 핵심 이벤트와 전환만",
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


def translate_gm_move(gm_data: Optional[dict]) -> str:
    """GM Move → type 라벨 제거, description만."""
    if not gm_data:
        return ""
    if isinstance(gm_data, dict):
        return gm_data.get("description", "")
    if isinstance(gm_data, str):
        return gm_data
    return ""


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
    "OPEN": "대화가 열려있다",
    "LOCKED": "대화가 한 주제에 고정되어 있다",
    "CLOSING": "대화가 마무리로 향하고 있다",
    "CLOSED": "대화가 끝났다",
}

# conclusion_proximity: 0-100 → 서사 페이싱 힌트
_PROXIMITY_HINTS = [
    (20, ""),  # 아직 멀다 — 힌트 불필요
    (45, "서사가 전개되고 있다. 새로운 실마리를 풀어놓아도 좋다"),
    (70, "긴장이 고조되고 있다. 새 떡밥보다 기존 실을 조이라"),
    (90, "절정이 가깝다. 모든 행동이 무게를 가진다"),
    (100, "서사가 정점에 있다. 모든 행동이 결과를 낳는다"),
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
        parts.append(f"주제: {topic}")

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
        line = f"- {npc_name}: {prefix}뻔한 방향({primary}) 대신 → {deflection}"
        hint = conn.get("render_hint", "")
        if hint:
            line += f" | {hint}"
        lines.append(line)
    if not lines:
        return ""
    return (
        "### 뻔한 연결 경계\n"
        "뻔한 연결은 클리셰다. 제안된 방향으로 굴절하라.\n"
        + "\n".join(lines)
    )


# =========================================================
# NPCKnowledge (Slot 17) — 예측 라벨만 제거
# =========================================================

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
            parts_k.append(f"  알고 있는 것: {', '.join(str(k) for k in knows)}")
        secrets = info.get("secrets_held", [])
        if secrets and isinstance(secrets, list):
            parts_k.append(f"  숨기는 것: {', '.join(str(s) for s in secrets)}")
        false_beliefs = info.get("false_beliefs", [])
        if false_beliefs and isinstance(false_beliefs, list):
            parts_k.append(f"  잘못 알고 있는 것: {', '.join(str(f) for f in false_beliefs)}")
        deception = info.get("deception_cues", [])
        if deception and isinstance(deception, list):
            parts_k.append(f"  거짓말 단서: {', '.join(str(d) for d in deception)}")
        # would_share: NPC가 자발적으로 정보를 공유하려는 의향
        if info.get("would_share"):
            parts_k.append("  스스로 말하고 싶어한다 — 기회가 오면 자연스럽게 꺼낸다")
        if parts_k:
            lines.append(f"- {npc_name}\n" + "\n".join(parts_k))
    if not lines:
        return ""
    return (
        "### NPC 지식 상태\n"
        "(NPC가 아는 것/숨기는 것은 행동을 형성한다. 이 개념 자체를 산문에 쓰지 마.)\n"
        + "\n".join(lines)
    )


# =========================================================
# 15. Dialogue Directives (Slot 17) — 대사 방향 지시
# =========================================================

_STRATEGY_HINTS = {
    # coping (Lazarus)
    "problem_focused": "직접적으로",
    "emotion_focused": "감정으로 우회하여",
    "avoidant": "화제를 돌리며",
    # stage (Goffman)
    "front": "체면을 유지하며",
    "back": "꾸밈없이",
    # decision_mode (Kahneman)
    "reactive": "즉흥적으로",
    "deliberate": "계산하며",
    # negotiation_stance (NEGOTIATION 모듈)
    "cooperative": "협력적으로",
    "competitive": "먼저 말을 끊으며",
    "exploitative": "상대 말의 약한 곳을 짚으며",
    # group_dynamic (GROUP_DYNAMICS 모듈)
    "conformity": "주변이 동의한 다음에 말하며",
    "obedience": "지시받은 대로 짧게 답하며",
    "groupthink": "남의 결론을 자기 말로 반복하며",
    "diffusion": "주어를 흐리고 다른 사람을 가리키며",
}

# relation.phase → 관계 단계별 대화 전략 힌트
_PHASE_HINTS = {
    "orientation": "탐색 중 — 조심스럽게 경계를 그리며",
    "identification": "동질감 형성 중 — 공통점을 찾으며",
    "exploitation": "관계 활용 중 — 편하게 요청하고 의지하며",
    "resolution": "정리 중 — 관계의 의미를 되짚으며",
}

_NEEDS_HINTS = {
    "safety": "안전을 확보하려",
    "belonging": "소속감을 얻으려",
    "esteem": "인정을 받으려",
    "autonomy": "자율성을 지키려",
    "competence": "능력을 증명하려",
    "relatedness": "유대를 형성하려",
    "trust": "신뢰를 쌓으려",
    "identity": "정체성을 확인하려",
    "control": "주도권을 잡으려",
    "understanding": "상대를 파악하려",
    "intimacy": "거리를 좁히려",
    "power": "우위를 점하려",
    "survival": "생존하려",
    "justice": "공정함을 지키려",
    "meaning": "의미를 찾으려",
}

# relation.attachment → 소유욕 대체 행동 힌트 (secure=없음, non-secure=구체 행동)
_ATTACHMENT_POSSESSIVENESS = {
    "secure":       "",
    "anxious":      "거리가 벌어지면 먼저 묻고, 답이 늦으면 묻기를 거듭한다",
    "avoidant":     "감정이 고조되면 한 발 물러서고, 가까워지면 시선을 다른 데 둔다",
    "disorganized": "다가갔다가 갑자기 거리를 두고, 같은 사람을 다르게 대한다",
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
                    directive_parts.append("(내면 잠김)")
                else:
                    directive_parts.append(f"(실제로는 {actual})")

        # 숨김: apprehension_gap (인식 왜곡)
        ag = psyche.get("apprehension_gap")
        if ag and isinstance(ag, str) and ag != "null":
            if _depth_for_npc < 0.4:
                directive_parts.append("(인식 흐림)")
            else:
                directive_parts.append(f"(인식 왜곡: {ag})")

        # 숨김: NPCKnowledge (leak_risk >= medium 일 때만)
        nk = knowledge.get(name, {})
        if isinstance(nk, dict):
            leak = nk.get("leak_risk", "none")
            if leak in ("medium", "high"):
                secrets = nk.get("secrets_held", [])
                if secrets and isinstance(secrets, list) and secrets[0]:
                    directive_parts.append(f"숨기는 중: {secrets[0]}")
                false_b = nk.get("false_beliefs", [])
                if false_b and isinstance(false_b, list) and false_b[0]:
                    directive_parts.append(f"잘못 믿는 중: {false_b[0]}")

        # 갈등 (value_conflict)
        vc = relation.get("value_conflict")
        if vc and isinstance(vc, str) and vc != "null":
            conflict = vc.split("+")[0].strip() if "+" in vc else vc
            directive_parts.append(f"갈등: {conflict}")

        # 행동 각인 (imprints) — 최근 1-2개만
        if npc_imprints and isinstance(npc_imprints, dict):
            imp_list = npc_imprints.get(name, [])
            if isinstance(imp_list, list):
                for imp in imp_list[-2:]:
                    if isinstance(imp, dict) and imp.get("mark"):
                        directive_parts.append(f"각인: {imp['mark']}")

        # 말투 (voice quirks) — gaze=Full인 NPC만 (in_focus)
        if voice_quirks and isinstance(voice_quirks, dict) and in_focus:
            vq = voice_quirks.get(name, "")
            if vq:
                directive_parts.append(f"말투: {vq}")

        if directive_parts:
            lines.append(f"- {name}: {'. '.join(directive_parts)}")

    if not lines:
        return ""

    return (
        "### 대사 방향\n"
        "(NPC 대사의 목적과 전략. 이 용어를 산문에 쓰지 마 — 대사가 수행하게 하라.)\n"
        + "\n".join(lines)
    )
