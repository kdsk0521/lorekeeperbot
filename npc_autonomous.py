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

        # Sort by priority descending
        results.sort(key=lambda x: x.priority, reverse=True)
        return results

    @staticmethod
    def build_autonomous_directive(triggers: List[TriggerResult], max_triggers: int = 3) -> str:
        """Convert trigger results into a directive string for the renderer."""
        if not triggers:
            return ""
        selected = triggers[:max_triggers]
        lines = ["[NPC Autonomous Behavior]"]
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
            f"Critical needs ({need_str}) — NPC will actively pursue fulfillment this turn",
            priority=7,
        )
    return None


def _check_attachment_activation(ctx: Dict) -> TriggerResult | None:
    """Anxious attachment → NPC initiates contact."""
    attachment = ctx["relation"].get("attachment", "secure")
    if attachment == "anxious":
        return TriggerResult(
            "attachment_activation", ctx["name"],
            f"Anxious attachment activated — {ctx['name']} seeks reassurance or proximity to PC",
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
            f"Reactance triggered — {ctx['name']} resists or defies current expectations",
            priority=6,
        )
    return None


def _check_info_gap(ctx: Dict) -> TriggerResult | None:
    """Information Gap: NPC has false_beliefs → driven to investigate."""
    false_beliefs = ctx["knowledge"].get("false_beliefs", [])
    if false_beliefs:
        return TriggerResult(
            "information_gap_fill", ctx["name"],
            f"Information gap — {ctx['name']} is driven to verify or investigate (false belief: {false_beliefs[0][:40]})",
            priority=4,
        )
    return None


def _check_secret_pressure(ctx: Dict) -> TriggerResult | None:
    """Secret pressure: leak_risk medium+ → NPC may slip."""
    leak_risk = ctx["knowledge"].get("leak_risk", "none")
    secrets = ctx["knowledge"].get("secrets_held", [])
    if leak_risk in ("medium", "high") and secrets:
        return TriggerResult(
            "secret_pressure", ctx["name"],
            f"Secret pressure ({leak_risk}) — {ctx['name']} may inadvertently reveal clues about hidden information",
            priority=5 if leak_risk == "high" else 3,
        )
    return None


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
            other_psyche = other_state.get("psyche", {})
            other_val = other_psyche.get("value", 0)
            if other_val < -30:
                return TriggerResult(
                    "emotional_contagion", ctx["name"],
                    f"Emotional contagion from {other_name} — {ctx['name']}'s calm may waver",
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
            f"Moral disengagement active — {ctx['name']} continues harmful pattern with self-justification",
            priority=4,
        )
    return None


def _check_desistance(ctx: Dict) -> TriggerResult | None:
    """Desistance check: needs ALL FOUR conditions for behavioral change.
    1. Alternative identity, 2. Social support, 3. Generative motivation, 4. Redemption narrative.
    Almost never fires — this is by design (Maruna)."""
    deep_read = ctx.get("deep_read", "")
    relation = ctx["relation"]
    attitude = ctx["attitude"].get("attitude", "neutral")
    trajectory = ctx["attitude"].get("trajectory", "stable")

    # Simplified heuristic: hostile NPC with improving trajectory + high relation value
    if attitude in ("hostile", "unfriendly") and trajectory == "improving":
        rel_val = relation.get("value", 0)
        if rel_val > 30:  # Strong positive relationship despite hostile tag
            return TriggerResult(
                "desistance_check", ctx["name"],
                f"Desistance conditions emerging for {ctx['name']} — behavioral change may be possible (verify: alternative identity + social support + generative motivation + redemption narrative)",
                priority=1,  # Low priority — very rare
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
            f"Personal agenda surfaces — {ctx['name']}'s need for {need_kr} colors in-scene behavior (dialogue/body language)",
            priority=2,
        )
    return None
