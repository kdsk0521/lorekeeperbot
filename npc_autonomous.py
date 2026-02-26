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
    ) -> List[TriggerResult]:
        """Evaluate all triggers for all NPCs in psyche_states.
        Returns list of TriggerResult sorted by priority (descending)."""
        if not _cfg.__dict__.get("NPC_AUTONOMOUS_ENABLED", True):
            return []

        results = []
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

            # Agenda Manifest
            r = _check_agenda_manifest(npc_ctx)
            if r:
                results.append(r)

            # Ethical Arrest (Lévinas)
            r = _check_ethical_arrest(npc_ctx, psyche_states)
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
    """Emotional contagion: if another NPC is in extreme state, this NPC may be affected."""
    current_polyvagal = ctx["soma"].get("polyvagal", "ventral")
    if current_polyvagal != "ventral":
        return None  # already stressed, no contagion trigger

    for other_name, other_state in all_psyche.items():
        if other_name == ctx["name"] or not isinstance(other_state, dict):
            continue
        other_soma = other_state.get("soma", {})
        other_polyvagal = other_soma.get("polyvagal", "ventral")
        if other_polyvagal in ("sympathetic", "dorsal"):
            other_relation = other_state.get("relation", {})
            other_val = other_relation.get("value", 0)
            if isinstance(other_val, (int, float)) and other_val < -30:
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
    """Incremental Desistance: layered by depth + trajectory (Maruna).
    Tier 1 (depth≥30 + improving): micro-change — hostile remarks decrease
    Tier 2 (depth≥50 + improving): notable — neutral observation, guarded cooperation
    Tier 3 (depth≥70 + improving + rel_val>30): turning point — old patterns break"""
    relation = ctx["relation"]
    attitude = ctx["attitude"].get("attitude", "neutral")
    trajectory = ctx["attitude"].get("trajectory", "stable")

    if attitude not in ("hostile", "unfriendly") or trajectory != "improving":
        return None

    depth = ctx["attitude"].get("depth", 0)
    rel_val = relation.get("value", 0)

    if depth >= 70 and rel_val > 30:
        return TriggerResult(
            "desistance_check", ctx["name"],
            f"{ctx['name']} — 전환점. 오래된 패턴이 무너지고 새로운 행동이 나타난다",
            priority=3,
        )
    elif depth >= 50:
        return TriggerResult(
            "desistance_check", ctx["name"],
            f"{ctx['name']} — 적대적 발언 감소, 중립적 관찰 증가, 조심스러운 협력 가능",
            priority=2,
        )
    elif depth >= 30:
        return TriggerResult(
            "desistance_check", ctx["name"],
            f"{ctx['name']} — 미세 변화: 공격 빈도 감소, 짧은 침묵, 시선 회피",
            priority=1,
        )
    return None



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
