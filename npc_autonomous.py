"""
Lorekeeper UNE - NPC Autonomous Behavior Trigger Engine (Phase 7)
Evaluates NPC psychological state from Flash data to generate autonomous behavior directives.
"""

from typing import Dict, Any, List, Optional

import config as _cfg


# =========================================================
# Trigger Definitions
# =========================================================
NPC_AUTONOMOUS_TRIGGERS = {
    "henderson_need_critical": {
        "desc": "활성 욕구 임계 — NPC의 핵심 욕구가 충족되지 않아 자발적 행동",
        "check": "_check_henderson_need_critical",
    },
    "attachment_activation": {
        "desc": "PC 부재 시 anxious NPC가 찾아옴",
        "check": "_check_attachment_activation",
    },
    "reactance": {
        "desc": "자유 제한 → NPC 반발",
        "check": "_check_reactance",
    },
    "information_gap_fill": {
        "desc": "궁금한 것 → 알아내려 함",
        "check": "_check_info_gap",
    },
    "secret_pressure": {
        "desc": "비밀 유지 기간 ↑ → 누출 확률 ↑",
        "check": "_check_secret_pressure",
    },
    "emotional_contagion": {
        "desc": "주변 NPC 감정 전이",
        "check": "_check_emotional_contagion",
    },
    "moral_disengagement_stable": {
        "desc": "악한 NPC는 계속 악하게 행동 (기본값)",
        "check": "_check_moral_disengagement",
    },
    "desistance_check": {
        "desc": "변화 조건 4가지 충족? → 행동 변화",
        "check": "_check_desistance",
    },
    "agenda_manifest": {
        "desc": "NPC의 개인 목표/욕구가 씬 상호작용 중 자연스럽게 드러남",
        "check": "_check_agenda_manifest",
    },
    "ethical_arrest": {
        "desc": "타자의 취약성 목격 → 행동 멈춤/외면/잔인 강화 (Lévinas)",
        "check": "_check_ethical_arrest",
    },
    "groupthink_pressure": {
        "desc": "3+ NPC 씬 · groupthink 다수 · fear/value_conflict → 반대 의견 삼킴 (Janis)",
        "check": "_check_groupthink_pressure",
    },
    "conformity_drift": {
        "desc": "3+ NPC 씬 · conformity 다수 · anxious → 다수 의견 동조 (Asch)",
        "check": "_check_conformity_drift",
    },
    "obedience_cascade": {
        "desc": "3+ NPC 씬 · obedience 다수 · disorganized/avoidant → 권위 추종 (Milgram)",
        "check": "_check_obedience_cascade",
    },
}


# =========================================================
# Desistance 4-Condition Gate (Maruna / Narrative Identity)
# =========================================================
DESISTANCE_CONDITIONS = {
    "alternative_identity":
        lambda ctx: ctx.get("depth", 0) >= 50
                    and ctx.get("trajectory") == "improving",
    "social_support":
        lambda ctx: ctx.get("attitude") in ("friendly", "loyal"),
    "generative_motivation":
        lambda ctx: bool(
            set(ctx.get("active_needs", []))
            & {"belonging", "intimacy", "esteem", "self-actualization"}
        ),
    "redemption_narrative":
        lambda ctx: ctx.get("depth", 0) >= 70,
}


class TriggerResult:
    def __init__(self, trigger_id: str, npc_name: str, directive: str, priority: int = 0):
        self.trigger_id = trigger_id
        self.npc_name = npc_name
        self.directive = directive
        self.priority = priority  # higher = more urgent

    def __repr__(self):
        return f"TriggerResult({self.trigger_id}, {self.npc_name}, pri={self.priority})"


class NPCAutonomousEngine:
    """Evaluates NPC psychological triggers from Flash psyche_states data."""

    @staticmethod
    def evaluate_triggers(
        psyche_states: Dict[str, Any],
        npc_knowledge: Dict[str, Any],
        npc_attitudes: Dict[str, Any],
        scene_type: str = "normal",
        static_traits_map: Optional[Dict[str, dict]] = None,
    ) -> List[TriggerResult]:
        """Evaluate all triggers for all NPCs in psyche_states.
        Returns list of TriggerResult sorted by priority (descending).

        Args:
            static_traits_map: {npc_name: static_traits_dict} for desistance gate enrichment (N6).
        """
        if not _cfg.__dict__.get("NPC_AUTONOMOUS_ENABLED", True):
            return []

        _traits_map = static_traits_map or {}
        results = []
        if not isinstance(psyche_states, dict):
            return results
        for npc_name, state in psyche_states.items():
            if not isinstance(state, dict):
                continue

            psyche = state.get("psyche", {})
            soma = state.get("soma", {})
            relation = state.get("relation", {})
            deep_read = state.get("deep_read", "")

            kn = npc_knowledge.get(npc_name, {}) if npc_knowledge else {}
            att = npc_attitudes.get(npc_name, {}) if npc_attitudes else {}

            npc_ctx = {
                "name": npc_name,
                "psyche": psyche,
                "soma": soma,
                "relation": relation,
                "deep_read": deep_read,
                "knowledge": kn,
                "attitude": att,
                "scene_type": scene_type,
                "static_traits": _traits_map.get(npc_name, {}),
            }

            # Henderson Need Critical
            r = _check_henderson_need_critical(npc_ctx)
            if r:
                results.append(r)

            # Attachment Activation
            r = _check_attachment_activation(npc_ctx)
            if r:
                results.append(r)

            # Reactance
            r = _check_reactance(npc_ctx)
            if r:
                results.append(r)

            # Information Gap Fill
            r = _check_info_gap(npc_ctx)
            if r:
                results.append(r)

            # Secret Pressure
            r = _check_secret_pressure(npc_ctx)
            if r:
                results.append(r)

            # Emotional Contagion (needs all psyche_states)
            r = _check_emotional_contagion(npc_ctx, psyche_states)
            if r:
                results.append(r)

            # Moral Disengagement
            r = _check_moral_disengagement(npc_ctx)
            if r:
                results.append(r)

            # Desistance 4-Condition Gate
            r = _check_desistance(npc_ctx)
            if r:
                results.append(r)

            # Agenda Manifest
            r = _check_agenda_manifest(npc_ctx)
            if r:
                results.append(r)

            # Ethical Arrest (Lévinas)
            r = _check_ethical_arrest(npc_ctx, psyche_states)
            if r:
                results.append(r)

            # Group Dynamic Hooks (Pass D-3, 2026-04-21): group_dynamic dark
            # circuit 해결. Flash가 채우는 relation.group_dynamic이 지금까지
            # iceberg 대사 수식어로만 소비되고 NPC 행동에 영향 0이었던 것
            # (§8b-10 S5). 3+ NPC 씬에서 다수 공유 시 취약 NPC(fear/anxious/
            # disorganized)에게 행동 directive 발동.
            r = _check_groupthink_pressure(npc_ctx, psyche_states)
            if r:
                results.append(r)
            r = _check_conformity_drift(npc_ctx, psyche_states)
            if r:
                results.append(r)
            r = _check_obedience_cascade(npc_ctx, psyche_states)
            if r:
                results.append(r)

        # Sort by priority descending
        results.sort(key=lambda x: x.priority, reverse=True)
        return results

    @staticmethod
    def build_autonomous_directive(triggers: List[TriggerResult], max_triggers: int = 3) -> str:
        """Convert trigger results into a directive string for the renderer."""
        if not triggers:
            return ""
        selected = triggers[:max_triggers]
        lines = [
            "[NPC Autonomous Behavior]",
            "(Show through action only. Never name trigger types or psychology terms in prose.)",
        ]
        for t in selected:
            lines.append(f"- {t.npc_name}: {t.directive}")
        return "\n".join(lines)


# =========================================================
# Individual Trigger Checks
# =========================================================

def _check_henderson_need_critical(ctx: Dict) -> TriggerResult | None:
    """Henderson 14 Needs: 핵심 욕구가 active_needs에 2개 이상이면 자발적 행동."""
    needs = ctx["psyche"].get("active_needs", [])
    if len(needs) >= 2:
        need_str = "/".join(needs[:2])
        return TriggerResult(
            "henderson_need_critical", ctx["name"],
            f"{ctx['name']}의 욕구({need_str})가 충족되지 않았다 — 이번 턴에 스스로 움직인다",
            priority=7,
        )
    return None


def _check_attachment_activation(ctx: Dict) -> TriggerResult | None:
    """Anxious attachment → NPC initiates contact."""
    attachment = ctx["relation"].get("attachment", "secure")
    if attachment == "anxious":
        return TriggerResult(
            "attachment_activation", ctx["name"],
            f"{ctx['name']}이(가) 가까움을 갈망한다 — 먼저 다가가고 확인을 구한다",
            priority=5,
        )
    return None


def _check_reactance(ctx: Dict) -> TriggerResult | None:
    """Reactance: coping=avoidant + negative relation → NPC pushes back."""
    coping = ctx["psyche"].get("coping")
    rel_val = ctx["relation"].get("value", 0)
    if coping == "avoidant" and rel_val < -10:
        return TriggerResult(
            "reactance", ctx["name"],
            f"{ctx['name']}이(가) 반발한다 — 기대에 저항하거나 거부한다",
            priority=6,
        )
    return None


def _check_info_gap(ctx: Dict) -> TriggerResult | None:
    """Information Gap: NPC has false_beliefs → investigate or conflict.
    High tension + false_belief → belief collision (defensive/confrontational)."""
    false_beliefs = ctx["knowledge"].get("false_beliefs", [])
    if not false_beliefs:
        return None

    tension = ctx.get("attitude", {}).get("tension", 0)
    belief = false_beliefs[0][:40]

    # High tension → belief collision (5-7: False Belief→Conflict)
    if tension >= 50:
        return TriggerResult(
            "information_gap_fill", ctx["name"],
            f"{ctx['name']}의 믿음이 흔들린다 — '{belief}'와 현실이 충돌. 방어하거나, 의심하거나, 무시한다",
            priority=5,
        )

    return TriggerResult(
        "information_gap_fill", ctx["name"],
        f"{ctx['name']}이(가) 뭔가 어긋남을 느낀다 — '{belief}'에 대해 확인하거나 캐묻는다",
        priority=4,
    )


def _check_secret_pressure(ctx: Dict) -> TriggerResult | None:
    """Secret pressure: leak_risk medium+ → NPC may slip. high+tension≥60 → concrete fragment leak."""
    leak_risk = ctx["knowledge"].get("leak_risk", "none")
    secrets = ctx["knowledge"].get("secrets_held", [])
    if not secrets or leak_risk not in ("medium", "high"):
        return None

    tension = ctx.get("attitude", {}).get("tension", 0)
    secret = secrets[0]

    # high risk + high tension → concrete secret fragment in directive
    if leak_risk == "high" and tension >= 60:
        fragment = secret[:30] if len(secret) > 30 else secret
        return TriggerResult(
            "secret_pressure", ctx["name"],
            f"{ctx['name']}의 압력 한계 — '{fragment}'의 조각이 행동이나 말실수로 새어나온다",
            priority=6,
        )

    return TriggerResult(
        "secret_pressure", ctx["name"],
        f"{ctx['name']}이(가) 비밀을 유지하기 힘들어한다 — 말실수나 행동으로 단서가 새어나올 수 있다",
        priority=5 if leak_risk == "high" else 3,
    )


def _check_emotional_contagion(ctx: Dict, all_psyche: Dict) -> TriggerResult | None:
    """Emotional contagion: if another NPC is in extreme state, this NPC may be affected.

    게이트 정책 (Pass D-1, 2026-04-21 — 폴리베이걸 이론 기반 재조정):
      - dorsal(freeze): 지각 셧다운 상태 → 수신 차단
      - sympathetic(fight/flight): 과각성 상태 → 감지 허용하되 어조·우선순위 차등
      - ventral(calm): 평상 감지 → 기본 priority 2 + "평온이 흔들린다"

    수신자 상태별 directive:
      - ventral → priority 2, "평온이 흔들리기 시작한다"
      - sympathetic → priority 1, "이미 곤두선 신경이 더 날카로워진다"

    이전 게이트(`polyvagal != "ventral"` 차단)는 쉐어하우스처럼 모두가
    약간씩 긴장한 장면에서 contagion 완전 침묵 → "고독한 공황" 패턴 유발
    (§8b-10 S2). sympathetic은 오히려 타인 고통에 더 예민하므로 허용.
    """
    current_polyvagal = ctx["soma"].get("polyvagal", "ventral")
    if current_polyvagal == "dorsal":
        return None  # freeze 상태: 지각 셧다운, 수신 불가

    for other_name, other_state in all_psyche.items():
        if other_name == ctx["name"] or not isinstance(other_state, dict):
            continue
        other_soma = other_state.get("soma", {})
        other_polyvagal = other_soma.get("polyvagal", "ventral")
        if other_polyvagal in ("sympathetic", "dorsal"):
            other_relation = other_state.get("relation", {})
            other_val = other_relation.get("value", 0)
            if isinstance(other_val, (int, float)) and other_val < -30:
                if current_polyvagal == "sympathetic":
                    return TriggerResult(
                        "emotional_contagion", ctx["name"],
                        f"{ctx['name']}이(가) {other_name}의 고통을 감지한다 — 이미 곤두선 신경이 더 날카로워진다",
                        priority=1,
                    )
                # ventral (또는 기타): 기본 어조
                return TriggerResult(
                    "emotional_contagion", ctx["name"],
                    f"{ctx['name']}이(가) {other_name}의 고통을 감지한다 — 평온이 흔들리기 시작한다",
                    priority=2,
                )
    return None


def _check_moral_disengagement(ctx: Dict) -> TriggerResult | None:
    """Moral disengagement: NPC with hostile attitude maintains harmful patterns."""
    attitude = ctx["attitude"].get("attitude", "neutral")
    self_opacity = ctx["psyche"].get("self_opacity")
    if attitude == "hostile" and self_opacity:
        return TriggerResult(
            "moral_disengagement_stable", ctx["name"],
            f"{ctx['name']}이(가) 해로운 행동을 강화한다 — 스스로에게 합리화하며",
            priority=4,
        )
    return None


def _check_desistance(ctx: Dict) -> TriggerResult | None:
    """Incremental Desistance: 4조건 게이트 기반 (Maruna).
    Uses check_desistance_gate() for structured condition checking.
    4/4 met → full transition eligible, priority 5
    2-3/4 met → micro-cracks, priority 2
    0-1/4 met → denied"""
    attitude_data = ctx["attitude"]
    attitude = attitude_data.get("attitude", "neutral")

    if attitude not in ("hostile", "unfriendly"):
        return None

    gate = check_desistance_gate(ctx["name"], attitude_data, ctx["psyche"],
                                static_traits=ctx.get("static_traits"))
    if not gate["eligible"]:
        return None

    return TriggerResult(
        "desistance_check", ctx["name"],
        gate["directive"],
        priority=5 if gate["met"] == 4 else 2,
    )


# =========================================================
# Leak Risk Calculator (replaces Flash leak_risk)
# =========================================================

def calculate_leak_risk(
    secrets_held: list,
    tension: int,
    depth: int,
    turns_since_secret: int,
    moral_stance: str,
) -> str:
    """비밀 누출 위험도 코드 계산. Flash의 leak_risk 대체.
    Returns: "none", "low", "medium", or "high"
    """
    if not secrets_held:
        return "none"

    time_pressure = min(turns_since_secret * 5, 30)
    tension_factor = tension * 0.4
    depth_factor = max(0, (depth - 40) * 0.3)
    moral_mod = {"disengaged": -15, "conflicted": 15, "principled": 5, "neutral": 0}
    moral_factor = moral_mod.get(moral_stance, 0)

    risk_score = time_pressure + tension_factor + depth_factor + moral_factor

    if risk_score >= 60:
        return "high"
    elif risk_score >= 30:
        return "medium"
    return "low"


# =========================================================
# Desistance 4-Condition Gate Check
# =========================================================

def check_desistance_gate(
    npc_name: str,
    attitude: dict,
    psyche: dict,
    static_traits: dict = None,
) -> dict:
    """4조건 중 몇 개 충족되는지 검사.
    Returns: {"met": int, "total": 4, "eligible": bool, "directive": str}

    Rules:
    - Only applies to hostile/unfriendly NPCs
    - 4/4 met → full transition eligible, priority 5 trigger
    - 2-3/4 met → micro-cracks, priority 2 trigger
    - 0-1/4 met → earned change denied
    """
    att_label = attitude.get("attitude", "neutral")
    if att_label not in ("hostile", "unfriendly"):
        return {"met": 0, "total": 4, "eligible": False,
                "directive": ""}

    # Build context dict for lambda evaluation
    gate_ctx = {
        "depth": attitude.get("depth", 0),
        "trajectory": attitude.get("trajectory", "stable"),
        "attitude": att_label,
        "active_needs": psyche.get("active_needs", []),
    }
    if static_traits:
        gate_ctx.update(static_traits)

    met_count = 0
    met_names = []
    for cond_name, check_fn in DESISTANCE_CONDITIONS.items():
        try:
            if check_fn(gate_ctx):
                met_count += 1
                met_names.append(cond_name)
        except Exception:
            pass  # malformed data → condition not met

    if met_count >= 4:
        directive = (
            f"{npc_name} — 전환점: 4조건 충족({', '.join(met_names)}). "
            f"오래된 패턴이 무너지고 새로운 행동이 나타난다"
        )
        return {"met": met_count, "total": 4, "eligible": True,
                "directive": directive}
    elif met_count >= 2:
        directive = (
            f"{npc_name} — 균열 징후({met_count}/4: {', '.join(met_names)}). "
            f"적대적 발언 감소, 짧은 침묵, 시선 회피"
        )
        return {"met": met_count, "total": 4, "eligible": True,
                "directive": directive}
    else:
        return {"met": met_count, "total": 4, "eligible": False,
                "directive": ""}


def _check_agenda_manifest(ctx: Dict) -> Optional[TriggerResult]:
    """NPC 개인 어젠다가 인씬에서 드러남."""
    needs = ctx["psyche"].get("active_needs", [])
    scene_type = ctx.get("scene_type", "normal")
    if scene_type not in ("social", "normal", "intimate"):
        return None
    if not needs or len(needs) >= 2:  # 2+ needs → henderson이 처리
        return None
    need = needs[0]
    secrets = ctx.get("knowledge", {}).get("secrets_held", [])
    agenda_needs = ("autonomy", "esteem", "self-actualization", "belonging", "intimacy")
    if secrets or need in agenda_needs:
        need_kr = {"autonomy": "자율성", "esteem": "인정", "self-actualization": "자아실현",
                   "belonging": "소속감", "intimacy": "친밀감", "safety": "안전"}.get(need, need)
        return TriggerResult(
            "agenda_manifest", ctx["name"],
            f"{ctx['name']}의 개인 목표가 드러난다 — {need_kr}에 대한 욕구가 대사와 행동에 묻어난다",
            priority=2,
        )
    return None


def _check_ethical_arrest(ctx: Dict, all_psyche: Dict) -> Optional[TriggerResult]:
    """Ethical Arrest (Lévinas): NPC witnesses vulnerability → pre-rational ethical call.
    Hostile/unfriendly NPC + another character in dorsal/severe state → 3 possible responses:
    stops, looks away, or becomes MORE cruel. The outcome depends on NPC's initial conditions."""
    attitude = ctx.get("attitude", {}).get("attitude", "neutral")
    if attitude not in ("hostile", "unfriendly", "neutral"):
        return None  # friendly+ NPCs don't need this trigger

    for other_name, other_state in all_psyche.items():
        if other_name == ctx["name"] or not isinstance(other_state, dict):
            continue
        other_soma = other_state.get("soma", {})
        other_polyvagal = other_soma.get("polyvagal", "ventral")
        other_psyche = other_state.get("psyche", {})
        other_val = other_psyche.get("value", 0)
        dissociation = other_soma.get("dissociation", "none")

        # Vulnerability: dorsal state, extreme negative psyche, or moderate+ dissociation
        is_vulnerable = (
            other_polyvagal == "dorsal"
            or (isinstance(other_val, (int, float)) and other_val <= -60)
            or dissociation in ("moderate", "severe")
        )
        if is_vulnerable:
            return TriggerResult(
                "ethical_arrest", ctx["name"],
                f"타자의 고통이 보였다 — {ctx['name']}의 행동이 멈추거나, 외면하거나, 더 잔인해진다",
                priority=3,
            )
    return None


# =========================================================
# Group Dynamic Hooks (Pass D-3, 2026-04-21)
# =========================================================
# §8b-10 S5에서 실증된 dark circuit 해소. `relation.group_dynamic`이 Flash DAI
# 에선 채워지고 iceberg.compose_dialogue_directives에서 대사 수식어로만
# 소비되어 NPC 행동 결정 로직에 영향이 0이었음. 3+ NPC 씬에서 다수가 같은
# group_dynamic을 공유할 때 "취약 조건" NPC에게 행동 directive를 생성해
# Asch/Janis/Milgram 심리학적 압력을 실제 자율 행동으로 반영.
#
# 게이트 공통: `len(all_psyche) >= 3` + `동일 group_dynamic >= 2`
#   (theoria_analyzer 스펙: "active in 3+ character scenes")

def _check_groupthink_pressure(ctx: Dict, all_psyche: Dict) -> TriggerResult | None:
    """Janis Groupthink: 다수가 만장일치 환상 쪽으로 수렴할 때 반대 의견 억제.
    본인 primary_emotion=fear 또는 value_conflict 존재 → 반대 삼킴.
    """
    if len(all_psyche) < 3:
        return None
    gt_count = sum(
        1 for s in all_psyche.values()
        if isinstance(s, dict)
        and s.get("relation", {}).get("group_dynamic") == "groupthink"
    )
    if gt_count < 2:
        return None
    own_emotion = ctx["psyche"].get("primary_emotion", "")
    own_conflict = ctx["relation"].get("value_conflict")
    if own_emotion == "fear" or own_conflict:
        return TriggerResult(
            "groupthink_pressure", ctx["name"],
            f"{ctx['name']}이(가) 반대 의견을 삼킨다 — 집단 결론 쪽으로 말끝이 휘어진다",
            priority=3,
        )
    return None


def _check_conformity_drift(ctx: Dict, all_psyche: Dict) -> TriggerResult | None:
    """Asch Conformity: 다수의 시선 자체가 압력이 되는 동조.
    본인 attachment=anxious → 자기 판단 유보하고 다수 따름.
    """
    if len(all_psyche) < 3:
        return None
    cf_count = sum(
        1 for s in all_psyche.values()
        if isinstance(s, dict)
        and s.get("relation", {}).get("group_dynamic") == "conformity"
    )
    if cf_count < 2:
        return None
    own_attachment = ctx["relation"].get("attachment", "")
    if own_attachment == "anxious":
        return TriggerResult(
            "conformity_drift", ctx["name"],
            f"{ctx['name']}이(가) 다수의 시선을 따른다 — 자기 판단을 유보한 채",
            priority=3,
        )
    return None


def _check_obedience_cascade(ctx: Dict, all_psyche: Dict) -> TriggerResult | None:
    """Milgram Obedience: 권위 구조가 서면 책임이 위로 이전된다는 감각.
    본인 attachment in (disorganized, avoidant) → 지시 따름, 책임 분산 신호.
    """
    if len(all_psyche) < 3:
        return None
    ob_count = sum(
        1 for s in all_psyche.values()
        if isinstance(s, dict)
        and s.get("relation", {}).get("group_dynamic") == "obedience"
    )
    if ob_count < 2:
        return None
    own_attachment = ctx["relation"].get("attachment", "")
    if own_attachment in ("disorganized", "avoidant"):
        return TriggerResult(
            "obedience_cascade", ctx["name"],
            f"{ctx['name']}이(가) 권위의 흐름에 몸을 맡긴다 — 책임은 위에 있다고 믿는다",
            priority=4,
        )
    return None
