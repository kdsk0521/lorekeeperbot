"""
Lorekeeper UNE - NPC Autonomous Behavior Trigger Engine (Phase 7)
Evaluates NPC psychological state from Flash data to generate autonomous behavior directives.
"""

from typing import Dict, Any, List, Optional

import config as _cfg


# =========================================================
# Trigger Definitions
# ⚠ doc-only 카탈로그 (2026-07-06 감사): 아래 dict의 "check" 문자열은 어디서도
# 역참조되지 않음 — 디스패처(evaluate_triggers)가 13개 _check_* 함수를 직접
# 하드코딩 호출. 트리거 추가 시 dict만 고치면 아무 일도 일어나지 않는다.
# evaluate_triggers 본문에 호출을 추가해야 배선 완료.
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
    "cost_of_inaction": {
        "desc": "판돈 有 + 압력 실측 + 회피성 멈춤 → 계산된 이니셔티브/능동 대기 (에로스 타워 E1, 2026-07-14)",
        "check": "_check_cost_of_inaction",
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


# =========================================================
# LLM 값 정규화 헬퍼 (2026-07-27)
# =========================================================
# 병: LLM은 Optional 필드를 "누락"으로도 보내지만 **명시 null**로도 보낸다.
# `d.get("k", [])`는 키가 존재하고 값이 null이면 기본값이 아니라 None을 반환 —
# 하류의 len()/비교 연산이 그대로 TypeError.
#   라이브 크래시: psyche.active_needs=null → _check_henderson_need_critical
#   `len(needs)` TypeError (2026-07-27). 07-19 secret_ledger setdefault 건과 동병.
# emotion_engine·iceberg·interim_engine은 이미 isinstance 가드 보유 —
# npc_autonomous만 무방비였다(2026-06-20 str 방어 때 컨테이너만 막고 잎은 놓침).
# 정책: LLM 유래 값은 default 인자에 의존하지 말고 타입으로 판정한다.

def _as_list(v) -> list:
    return v if isinstance(v, list) else []


def _as_dict(v) -> dict:
    return v if isinstance(v, dict) else {}


def _as_num(v, default: float = 0):
    return v if isinstance(v, (int, float)) and not isinstance(v, bool) else default


def _as_str(v, default: str = "") -> str:
    return v if isinstance(v, str) else default


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

        # [Schema 안전망] Flash가 soma/psyche/relation을 dict 대신 str 등으로 채우는 경우 방어.
        # emotion_engine/iceberg는 이미 isinstance 가드 보유 — npc_autonomous에만 누락되어
        # 2026-06-20 라이브 크래시 (AttributeError: 'str' object has no attribute 'get', _check_emotional_contagion).
        # 빌드 ctx와 raw all_psyche(컨테이전/해리 트리거가 직접 읽는 경로) 양쪽을 단일 지점에서 정규화.
        _norm: Dict[str, Any] = {}
        for _n, _s in psyche_states.items():
            if not isinstance(_s, dict):
                continue
            _s2 = dict(_s)
            for _k in ("psyche", "soma", "relation"):
                if not isinstance(_s2.get(_k), dict):
                    _s2[_k] = {}
            _norm[_n] = _s2
        psyche_states = _norm

        for npc_name, state in psyche_states.items():
            if not isinstance(state, dict):
                continue

            psyche = state.get("psyche", {})
            soma = state.get("soma", {})
            relation = state.get("relation", {})
            deep_read = _as_str(state.get("deep_read"))

            # knowledge/attitude도 명시 null 가능 — _norm은 psyche_states만 훑는다.
            kn = _as_dict(npc_knowledge.get(npc_name)) if isinstance(npc_knowledge, dict) else {}
            att = _as_dict(npc_attitudes.get(npc_name)) if isinstance(npc_attitudes, dict) else {}

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

            # Cost of Inaction (에로스 타워 E1, 2026-07-14)
            r = _check_cost_of_inaction(npc_ctx)
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
            "(surfaced through action or speech, a line of dialogue often the most direct channel; the trigger types and psychology terms stay out of the prose.)",
        ]
        for t in selected:
            lines.append(f"- {t.npc_name}: {t.directive}")
        return "\n".join(lines)


# =========================================================
# Individual Trigger Checks
# =========================================================

def _check_henderson_need_critical(ctx: Dict) -> TriggerResult | None:
    """Henderson 14 Needs: 핵심 욕구가 active_needs에 2개 이상이면 자발적 행동."""
    needs = [n for n in _as_list(ctx["psyche"].get("active_needs")) if isinstance(n, str) and n.strip()]
    if len(needs) >= 2:
        need_str = "/".join(needs[:2])
        return TriggerResult(
            "henderson_need_critical", ctx["name"],
            f"{ctx['name']}'s needs ({need_str}) go unmet — this turn they move on their own",
            priority=7,
        )
    return None


def _check_attachment_activation(ctx: Dict) -> TriggerResult | None:
    """Anxious attachment → NPC initiates contact."""
    attachment = ctx["relation"].get("attachment", "secure")
    if attachment == "anxious":
        return TriggerResult(
            "attachment_activation", ctx["name"],
            f"{ctx['name']} aches for closeness — reaching out first, seeking reassurance",
            priority=5,
        )
    return None


def _check_reactance(ctx: Dict) -> TriggerResult | None:
    """Reactance: coping=avoidant + negative relation → NPC pushes back."""
    coping = ctx["psyche"].get("coping")
    rel_val = _as_num(ctx["relation"].get("value"))
    if coping == "avoidant" and rel_val < -10:
        return TriggerResult(
            "reactance", ctx["name"],
            f"{ctx['name']} pushes back — resisting or refusing what's expected",
            priority=6,
        )
    return None


def _check_info_gap(ctx: Dict) -> TriggerResult | None:
    """Information Gap: NPC has false_beliefs → investigate or conflict.
    High tension + false_belief → belief collision (defensive/confrontational)."""
    false_beliefs = [b for b in _as_list(ctx["knowledge"].get("false_beliefs"))
                     if isinstance(b, str) and b.strip()]
    if not false_beliefs:
        return None

    tension = _as_num(_as_dict(ctx.get("attitude")).get("tension"))
    belief = false_beliefs[0][:40]

    # High tension → belief collision (5-7: False Belief→Conflict)
    if tension >= 50:
        return TriggerResult(
            "information_gap_fill", ctx["name"],
            f"{ctx['name']}'s belief wavers — '{belief}' collides with reality; defending, doubting, or dismissing it",
            priority=5,
        )

    return TriggerResult(
        "information_gap_fill", ctx["name"],
        f"{ctx['name']} senses something off — checking or pressing about '{belief}'",
        priority=4,
    )


def _check_secret_pressure(ctx: Dict) -> TriggerResult | None:
    """Secret pressure: leak_risk medium+ → NPC may slip. high+tension≥60 → concrete fragment leak."""
    leak_risk = _as_str(ctx["knowledge"].get("leak_risk"), "none")
    secrets = [s for s in _as_list(ctx["knowledge"].get("secrets_held"))
               if isinstance(s, str) and s.strip()]
    if not secrets or leak_risk not in ("medium", "high"):
        return None

    tension = _as_num(_as_dict(ctx.get("attitude")).get("tension"))
    secret = secrets[0]

    # high risk + high tension → concrete secret fragment in directive
    if leak_risk == "high" and tension >= 60:
        fragment = secret[:30] if len(secret) > 30 else secret
        return TriggerResult(
            "secret_pressure", ctx["name"],
            f"{ctx['name']} at the pressure limit — a fragment of '{fragment}' slips out through action or a misspoken word",
            priority=6,
        )

    return TriggerResult(
        "secret_pressure", ctx["name"],
        f"{ctx['name']} struggles to keep the secret — a clue may slip out through a misstep or action",
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
        other_soma = _as_dict(other_state.get("soma"))
        other_polyvagal = other_soma.get("polyvagal", "ventral")
        if other_polyvagal in ("sympathetic", "dorsal"):
            other_relation = _as_dict(other_state.get("relation"))
            other_val = other_relation.get("value", 0)
            if isinstance(other_val, (int, float)) and other_val < -30:
                if current_polyvagal == "sympathetic":
                    return TriggerResult(
                        "emotional_contagion", ctx["name"],
                        f"{ctx['name']} senses {other_name}'s distress — already-taut nerves sharpen further",
                        priority=1,
                    )
                # ventral (또는 기타): 기본 어조
                return TriggerResult(
                    "emotional_contagion", ctx["name"],
                    f"{ctx['name']} senses {other_name}'s distress — the calm begins to waver",
                    priority=2,
                )
    return None


def _check_moral_disengagement(ctx: Dict) -> TriggerResult | None:
    """Moral disengagement: NPC with hostile attitude maintains harmful patterns."""
    attitude = _as_str(_as_dict(ctx["attitude"]).get("attitude"), "neutral")
    self_opacity = ctx["psyche"].get("self_opacity")
    if attitude == "hostile" and self_opacity:
        return TriggerResult(
            "moral_disengagement_stable", ctx["name"],
            f"{ctx['name']} doubles down on the harmful behavior — rationalizing it to themselves",
            priority=4,
        )
    return None


def _check_desistance(ctx: Dict) -> TriggerResult | None:
    """Incremental Desistance: 4조건 게이트 기반 (Maruna).
    Uses check_desistance_gate() for structured condition checking.
    4/4 met → full transition eligible, priority 5
    2-3/4 met → micro-cracks, priority 2
    0-1/4 met → denied"""
    attitude_data = _as_dict(ctx["attitude"])
    attitude = _as_str(attitude_data.get("attitude"), "neutral")

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

def leak_pressure_score(
    tension: int,
    depth: int,
    turns_since_secret: int,
    moral_stance: str = "neutral",
) -> int:
    """비밀 압력 0-100 스코어. [V10 Secret Ledger 2026-07-14] calculate_leak_risk의
    내부 공식을 노출 — 원장 leak_pressure 엔진으로 승격(기존 함수는 호출자 0 죽은 배선이었음)."""
    time_pressure = min(turns_since_secret * 5, 30)
    tension_factor = (tension or 0) * 0.4
    depth_factor = max(0, ((depth or 0) - 40) * 0.3)
    moral_mod = {"disengaged": -15, "conflicted": 15, "principled": 5, "neutral": 0}
    moral_factor = moral_mod.get(moral_stance, 0)
    return max(0, min(100, int(time_pressure + tension_factor + depth_factor + moral_factor)))


def leak_risk_label(score: int) -> str:
    """압력 스코어 → none/low/medium/high 라벨 (calculate_leak_risk 임계 보존)."""
    if score >= 60:
        return "high"
    if score >= 30:
        return "medium"
    return "low"


# [2026-07-15 정리] calculate_leak_risk 제거 — 07-14 E3(secret_ledger)가 남긴 껍데기.
#
# E3가 이 함수의 **로직을 둘로 쪼개 꺼냈고**(leak_pressure_score = 압력 0-100 스코어,
# leak_risk_label = 스코어→라벨, 임계 보존), domain_manager L1045가 그 **둘만 import**해
# 원장 행마다 직접 호출한다. 원 함수는 `return leak_risk_label(leak_pressure_score(...))`
# 얇은 래퍼로 남아 아무도 안 불렀다(dead_scan A, 2026-07-15).
#
# 유일하게 래퍼만 갖던 것 = `if not secrets_held: return "none"` 가드. 이건 소실되지 않는다:
# domain_manager.sync_secret_ledger는 **secrets_held에서 파생된 원장 행을 순회**하므로
# 비밀이 없으면 행이 없고 → 계산 자체가 안 돈다. 가드가 구조로 대체됨.
#
# 승격된 건 함수가 아니라 그 안의 계산이었다. MEMORY의 "calculate_leak_risk 죽은 배선
# 승격"은 그런 뜻이고, 껍데기 회수가 07-14에 누락됐다.


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
    attitude = _as_dict(attitude)
    psyche = _as_dict(psyche)
    att_label = _as_str(attitude.get("attitude"), "neutral")
    if att_label not in ("hostile", "unfriendly"):
        return {"met": 0, "total": 4, "eligible": False,
                "directive": ""}

    # Build context dict for lambda evaluation
    gate_ctx = {
        "depth": _as_num(attitude.get("depth")),
        "trajectory": _as_str(attitude.get("trajectory"), "stable"),
        "attitude": att_label,
        "active_needs": _as_list(psyche.get("active_needs")),
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
            f"{npc_name} — turning point: 4 conditions met ({', '.join(met_names)}). "
            f"the old pattern collapses and a new behavior emerges"
        )
        return {"met": met_count, "total": 4, "eligible": True,
                "directive": directive}
    elif met_count >= 2:
        directive = (
            f"{npc_name} — signs of cracking ({met_count}/4: {', '.join(met_names)}). "
            f"fewer hostile remarks, brief silences, eyes averted"
        )
        return {"met": met_count, "total": 4, "eligible": True,
                "directive": directive}
    else:
        return {"met": met_count, "total": 4, "eligible": False,
                "directive": ""}


def _check_agenda_manifest(ctx: Dict) -> Optional[TriggerResult]:
    """NPC 개인 어젠다가 인씬에서 드러남."""
    needs = [n for n in _as_list(ctx["psyche"].get("active_needs")) if isinstance(n, str) and n.strip()]
    scene_type = _as_str(ctx.get("scene_type"), "normal")
    if scene_type not in ("social", "normal", "intimate"):
        return None
    if not needs or len(needs) >= 2:  # 2+ needs → henderson이 처리
        return None
    need = needs[0]
    secrets = _as_list(_as_dict(ctx.get("knowledge")).get("secrets_held"))
    agenda_needs = ("autonomy", "esteem", "self-actualization", "belonging", "intimacy")
    if secrets or need in agenda_needs:
        need_kr = {"autonomy": "autonomy", "esteem": "recognition", "self-actualization": "self-actualization",
                   "belonging": "belonging", "intimacy": "intimacy", "safety": "safety"}.get(need, need)
        return TriggerResult(
            "agenda_manifest", ctx["name"],
            f"{ctx['name']}'s personal aim shows — the want for {need_kr} bleeds into their words and actions",
            priority=2,
        )
    return None


def _check_ethical_arrest(ctx: Dict, all_psyche: Dict) -> Optional[TriggerResult]:
    """Ethical Arrest (Lévinas): NPC witnesses vulnerability → pre-rational ethical call.
    Hostile/unfriendly NPC + another character in dorsal/severe state → 3 possible responses:
    stops, looks away, or becomes MORE cruel. The outcome depends on NPC's initial conditions."""
    attitude = _as_str(_as_dict(ctx.get("attitude")).get("attitude"), "neutral")
    if attitude not in ("hostile", "unfriendly", "neutral"):
        return None  # friendly+ NPCs don't need this trigger

    for other_name, other_state in all_psyche.items():
        if other_name == ctx["name"] or not isinstance(other_state, dict):
            continue
        other_soma = _as_dict(other_state.get("soma"))
        other_polyvagal = other_soma.get("polyvagal", "ventral")
        other_psyche = _as_dict(other_state.get("psyche"))
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
                f"another's suffering became visible — {ctx['name']} stops, looks away, or turns crueler",
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
            f"{ctx['name']} swallows the dissent — their words bend toward the group's conclusion",
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
            f"{ctx['name']} follows the many eyes — holding their own judgment in reserve",
            priority=3,
        )
    return None


def _check_cost_of_inaction(ctx: Dict) -> TriggerResult | None:
    """Cost of Inaction (에로스 타워 E1, 2026-07-14): 멈춤의 비용이 행동 리스크를
    넘어설 때 계산된 이니셔티브 또는 능동 대기.

    게이트 3중 AND:
      (a) 판돈 존재 — active_needs 또는 secrets_held (지킬/얻을 것이 있는 NPC만)
      (b) 압력 실측 — attitude.tension >= 40 (무행동 비용의 근사 계기.
          클록/퀘스트 신호는 이 레이어에 미공급 — v2에서 배선 검토)
      (c) 멈춤이 '회피성'일 것 — coping == "avoidant" AND polyvagal != "dorsal".
          dorsal(freeze)은 위기=드러냄 원칙상 강제 기동 금지(freezer freezes).
          relation.value < -10 구간은 reactance 트리거 소유 — 중복 발화 방지 양보.

    장면 게이트: agenda_manifest와 동일(social/normal/intimate) — 전투/위기 씬은
    이미 움직이는 중이라 정체 처방 불요.

    Directive는 두 갈래 모두 제시(행동 or 능동 대기) — 에로스 타워 momentum 원문의
    "waiting is valid only when it is active" 보존. 계산 낭독 금지 단서는
    build_autonomous_directive 공통 헤더가 커버.
    """
    if _as_str(ctx.get("scene_type"), "normal") not in ("social", "normal", "intimate"):
        return None
    needs = _as_list(ctx["psyche"].get("active_needs"))
    secrets = _as_list(_as_dict(ctx.get("knowledge")).get("secrets_held"))
    if not needs and not secrets:
        return None  # (a) 판돈 없음
    tension = _as_dict(ctx.get("attitude")).get("tension")
    if not isinstance(tension, (int, float)) or tension < 40:
        return None  # (b) 압력 미달
    coping = ctx["psyche"].get("coping")
    polyvagal = ctx["soma"].get("polyvagal", "ventral")
    rel_val = ctx["relation"].get("value", 0)
    if coping != "avoidant" or polyvagal == "dorsal":
        return None  # (c) 회피성 멈춤 아님 (freeze는 보호)
    if isinstance(rel_val, (int, float)) and rel_val < -10:
        return None  # reactance 소유 구간
    return TriggerResult(
        "cost_of_inaction", ctx["name"],
        f"staying still now costs {ctx['name']} more than moving — "
        f"they take one calculated step, or make the waiting itself active: "
        f"preparing, repositioning, setting terms, buying time",
        priority=4,
    )


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
            f"{ctx['name']} yields to the flow of authority — believing the responsibility lies above",
            priority=4,
        )
    return None
