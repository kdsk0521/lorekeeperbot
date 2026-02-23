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
from typing import Any, Dict, List, Optional, Tuple, Union

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
    base = _calc_depth(scene_type, energy)
    t_mod = _turn_mod(turn_count)
    global_depth = max(0.0, min(1.0, base + t_mod))

    result = {name: global_depth for name in npc_names}

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


def _safe_get(data: Any, key: str, default: Any = None) -> Any:
    """dict.get() with isinstance guard."""
    if isinstance(data, dict):
        return data.get(key, default)
    return default


# =========================================================
# 1. psyche_states (Slot 14)
# =========================================================

_LAYER_RENAMES = {
    "Surface": "▸평소(80%)",
    "Adaptation": "▸반복패턴",
    "Core": "▸극한에서만",
    "Lack": "▸절대 직접 말하지 마",
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
            if not part.startswith("절대 직접"):
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

        # descriptor 추출 (depth에 따라 축 제한) — soma는 항상 수면 위
        parts = []
        if soma.get("descriptor"):
            parts.append(soma["descriptor"])
        if depth < 0.8 and psyche.get("descriptor"):
            parts.append(psyche["descriptor"])
        if depth < 0.6 and relation.get("descriptor"):
            parts.append(relation["descriptor"])

        line = f"- {name}: {'. '.join(parts)}" if parts else f"- {name}"
        lines.append(line)

        # deep_read: depth에 따라 필터링
        if deep and isinstance(deep, str):
            filtered = _filter_deep_read_by_depth(deep, depth)
            if filtered:
                lines.append(f"  └ {filtered}")

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

_ENERGY_HINTS = {
    "idle": "일상 — 환경 질감, 間(MA), 일상적 디테일, 느린 리듬",
    "rising": "고조 — 인물 간 마찰, 체언어 모순, 대인 긴장",
    "stagnant": "정적 — 침묵, 부재의 존재감, 말하지 않은 무게",
    "detonation": "폭발 — 물리적 충격, 짧은 문장, 행동의 대가",
    "aftershock": "여진 — 침묵, 잔해, 지연된 반응, 무감각",
}


def translate_energy_direction(energy: str) -> str:
    """energy_direction 라벨 → 산문 호흡 힌트."""
    if not energy:
        return ""
    hint = _ENERGY_HINTS.get(energy.lower().strip(), "")
    if hint:
        return f"### 장면 호흡: {hint}"
    return ""


# =========================================================
# 4. quality_flags (Slot 16)
# =========================================================

_FLAG_DIRECTIVES = {
    "convergence_warning": "장면이 갈등 없이 합의에 도달하고 있다. 불편함을 유지하라.",
    "echo_warning": "NPC가 PC 감정을 따라하고 있다. NPC만의 반응을 만들어라.",
    "stagnation_warning": "3턴째 장면 에너지가 평평하다. 외부 자극을 자연스럽게 도입하라.",
    "mse_deviation": "NPC의 정신 상태가 급변했다. 이전 행동과의 일관성을 점검하고, 변화에 인과적 근거를 부여하라.",
    "dissonance_flag": "NPC가 모순된 신념/행동을 보이고 있다. 즉시 해소하지 마라 — 불편함을 행동으로 보여줘라.",
    "redemption_warning": "NPC가 근거 없이 태도를 누그러뜨리고 있다. 변화에는 대가가 필요하다. 되돌려라.",
}

_SYMPTOM_TEMPLATE = "NPC가 {cluster} 증상군을 보이고 있다. 증상을 일관된 세트로 유지하라. 체리피킹 금지."


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
# 5. NPC attitudes (Slot 17)
# =========================================================

_TRAJECTORY_HINTS = {
    "warming": "경계를 풀기 시작하는 기미",
    "cooling": "거리를 두기 시작하는 기미",
    "stable": "현재 태도 유지",
    "volatile": "태도가 불안정, 작은 자극에도 변화 가능",
    "declining": "관계가 약해지고 있는 기미",
    "improving": "관계가 나아지고 있는 기미",
}


def translate_npc_attitudes(attitudes: Optional[dict]) -> str:
    """NPCAttitudes → attitude 라벨 제거, trajectory 행동 힌트, reason 유지."""
    if not attitudes or not isinstance(attitudes, dict):
        return ""
    lines = []
    for name, att in attitudes.items():
        if not isinstance(att, dict):
            continue
        trajectory = att.get("trajectory", "stable")
        reason = att.get("reason", "")
        hint = _TRAJECTORY_HINTS.get(trajectory, trajectory)
        if reason:
            lines.append(f"- {name}: {reason} — {hint}")
        else:
            lines.append(f"- {name}: {hint}")
    return "\n".join(lines)


# =========================================================
# 6. connection_depth (Slot 17)
# =========================================================

_STAGE_HINTS = {
    "Initial": "첫 만남 — 서로를 파악하는 중",
    "Warming": "관계 심화 중 — 경계가 낮아지고 있다",
    "Established": "관계 형성됨 — 일정한 패턴이 자리잡았다",
    "Intimate": "깊은 관계 — 취약성이 노출되기 시작한다",
    "Ruptured": "관계 파열 — 신뢰가 깨졌거나 위기 상태",
}


def translate_connection_depth(
    npc_name: str,
    stage_name: str,
    depth: int,
    tension: int,
    hint_en: str = "",
) -> str:
    """Connection depth 수치/스테이지명 → 행동 힌트."""
    stage_hint = _STAGE_HINTS.get(stage_name, hint_en or stage_name)
    parts = [f"- {npc_name}: {stage_hint}"]
    if tension > 50:
        parts[0] += ". 긴장감이 매우 높다."
    elif tension > 20:
        parts[0] += ". 긴장감이 있다."
    return "\n".join(parts)


# =========================================================
# 7. IntimacyAnalysis (Slot 17)
# =========================================================

_WINDOW_HINTS = {
    "within": "안정 범위 — 참여 가능, 감각이 살아있음",
    "above": "과각성 — 압도됨, 호흡 빨라짐, 경계 상태",
    "below": "저각성 — 얼어붙음, 감각 둔화, 해리 가능",
}

_DESIRE_HINTS = {
    "attachment": "확인받고 싶다 — 거리가 생기면 불안",
    "power": "주도권을 쥐고 싶다 — 통제 욕구",
    "escape": "여기서 벗어나고 싶다 — 현실 회피",
    "connection": "연결되고 싶다 — 진짜 접촉",
    "validation": "인정받고 싶다 — 자기 가치 확인",
    "sensation": "느끼고 싶다 — 순수 감각 추구",
}


def translate_intimacy(intimacy_data: Optional[dict]) -> str:
    """IntimacyAnalysis → 프레임워크 라벨 제거, 행동 힌트로 변환."""
    if not intimacy_data or not isinstance(intimacy_data, dict):
        return ""
    lines = []

    # window_check (기존 버그: vulnerability → window_check 정정)
    window = intimacy_data.get("window_check", intimacy_data.get("vulnerability", {}))
    if window and isinstance(window, dict):
        for char_name, state in window.items():
            state_lower = str(state).lower().strip()
            hint = _WINDOW_HINTS.get(state_lower, state)
            lines.append(f"- {char_name}: {hint}")

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

    return "\n".join(lines)


# =========================================================
# 8. emotion_intensity (Slot 29)
# =========================================================

_INTENSITY_HINTS = [
    (30, "미세한 표정 변화 수준 — 주의 깊게 봐야 알아챔"),
    (60, "눈에 띄는 체언어 — 관찰자가 알아챌 수 있음"),
    (80, "뚜렷한 신체 반응 — 숨기기 어려움"),
    (100, "신체가 압도됨 — 평정을 유지할 수 없음"),
]


def translate_emotion_intensity(psyche_states: Optional[dict]) -> str:
    """psyche value → 관찰 가능한 강도 힌트."""
    if not psyche_states or not isinstance(psyche_states, dict):
        return ""
    lines = []
    for name, pdata in psyche_states.items():
        if not isinstance(pdata, dict):
            continue
        psyche = pdata.get("psyche", pdata.get("mental", {}))
        if not isinstance(psyche, dict):
            continue
        val = abs(psyche.get("value", 0))
        hint = _to_tier(val, _INTENSITY_HINTS)
        lines.append(f"  {name}: {hint}")
    if not lines:
        return ""
    return (
        "[감정 강도]\n"
        "감정을 신체 증거로 렌더링하라. 감정명, 강도 라벨, 수치를 산문에 쓰지 마.\n"
        + "\n".join(lines)
    )


# =========================================================
# 9. vigor/composure contrast (Slot 29)
# =========================================================

def translate_vigor_composure(vigor: int, composure: int) -> str:
    """기력/평정 괴리가 클 때 사실만 전달."""
    gap = abs(vigor - composure)
    if gap < 30:
        return ""
    return "기력과 평정 사이에 큰 괴리가 있다. 행동으로 드러내라."


# =========================================================
# 10. gm_move (Slot 30)
# =========================================================

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

_SILENCE_HINTS = {
    "reflective": "사색적 침묵 — 시간이 느려진다",
    "hesitant": "망설이는 침묵 — 삼킨 말이 있다",
    "heavy": "묵직한 침묵 — 둘 다 알고 있지만 말하지 않는다",
    "tense": "긴장된 침묵 — 한 마디가 모든 걸 바꿀 수 있다",
}


def translate_narrative_chain(chain_data: Optional[dict]) -> str:
    """narrative_chain 라벨 → 산문 힌트."""
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

    # silence_type
    silence = chain_data.get("silence_type")
    if silence and isinstance(silence, str):
        silence_hint = _SILENCE_HINTS.get(silence.lower(), silence)
        parts.append(silence_hint)

    return ". ".join(parts) + "." if parts else ""


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
        line = f"- {npc_name}: 뻔한 방향({primary}) 대신 → {deflection}"
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
    """NPCKnowledge — knows/secrets/false_beliefs/deception_cues 유지, leak_risk/would_share 제거."""
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
        # leak_risk, would_share → 제거 (GM이론의 "빈칸")
        if parts_k:
            lines.append(f"- {npc_name}\n" + "\n".join(parts_k))
    if not lines:
        return ""
    return (
        "### NPC 지식 상태\n"
        "(NPC가 아는 것/숨기는 것은 행동을 형성한다. 이 개념 자체를 산문에 쓰지 마.)\n"
        + "\n".join(lines)
    )
