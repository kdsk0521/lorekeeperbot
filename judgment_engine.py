"""
Lorekeeper UNE - Judgment Engine (Call 2)
Handles dice rolls and narrative outcomes based on Call 1 analysis.
"""

import logging
import random
from orchestration_context import GameContext

logger = logging.getLogger("Judgment")

class JudgmentEngine:
    def __init__(self, client, model_id: str):
        self.client = client
        self.model_id = model_id

    @staticmethod
    def _calculate_theory_mod(context: GameContext) -> int:
        """Flash psyche → ±20 보정. NPC의 심리 상태가 행동 결과에 미치는 영향."""
        bus = context.shared_bus
        scene_type = bus.dai.get("scene_type", "normal")
        psyche_states = bus.dai.get("psyche_states", {})

        if not psyche_states:
            return 0

        # Find target NPC (relevant NPCs first, then any available)
        target_npc = None
        for npc_name in bus.dai.get("relevant_npcs", []):
            if npc_name in psyche_states:
                target_npc = psyche_states[npc_name]
                break
        if not target_npc:
            for state in psyche_states.values():
                if isinstance(state, dict):
                    target_npc = state
                    break
        if not target_npc:
            return 0

        soma = target_npc.get("soma", {})
        psyche = target_npc.get("psyche", {})
        relation = target_npc.get("relation", {})

        polyvagal = soma.get("polyvagal", "ventral")
        decision_mode = psyche.get("decision_mode", "deliberate")
        cultural_affect = soma.get("cultural_affect")
        attachment = relation.get("attachment", "secure")

        is_combat = scene_type == "combat"
        is_social = scene_type in ("social", "normal", "intimate")

        mod = 0

        # 1. Polyvagal × action_type
        if polyvagal == "dorsal":
            mod += (-15 if is_combat else -10)  # 셧다운: 전투/사회 모두 불리
        elif polyvagal == "sympathetic":
            mod += (5 if is_combat else -5)     # 투쟁도주: 전투+, 사회-
        elif polyvagal == "ventral":
            mod += (5 if is_social else 0)      # 안전: 사회+

        # 2. Decision mode × action_type
        if decision_mode == "reactive":
            mod += 5 if is_combat else -5
        elif decision_mode == "deliberate":
            mod += 5 if is_social else -5

        # 3. Cultural affect
        if cultural_affect == "han":
            mod += (-3 if is_social else 0)   # 한: 사회적 위축
        elif cultural_affect == "jeong" and is_social:
            mod += 5                          # 정: 사회적 유대
        elif cultural_affect == "hwabyung":
            mod += (-8 if is_social else -3)  # 화병: 폭발 위험
        elif cultural_affect == "nunchi" and is_social:
            mod += 3                          # 눈치: 상황 파악 이점
        elif cultural_affect == "chaemyeon" and is_social:
            mod += 5                          # 체면 유지
        elif cultural_affect == "gi" and is_combat:
            mod += 5                          # 기 충전

        # 4. Attachment × interpersonal
        if is_social:
            attachment_mod = {"secure": 5, "anxious": 3, "avoidant": -5, "disorganized": -3}
            mod += attachment_mod.get(attachment, 0)

        return max(-20, min(20, mod))

    @staticmethod
    def _calculate_aspect_mod(context: GameContext) -> int:
        """Aspects(for/against) 기반 보정. 구조화 데이터 없으면 0."""
        import config as _cfg
        aspects = context.shared_bus.dai.get("aspects", [])
        if not isinstance(aspects, list):
            return 0

        for_count = 0
        against_count = 0
        for aspect in aspects:
            if not isinstance(aspect, dict):
                continue
            stance = str(aspect.get("for_or_against", aspect.get("stance", ""))).strip().lower()
            if stance in ("for", "support", "positive", "pro"):
                for_count += 1
            elif stance in ("against", "oppose", "negative", "con"):
                against_count += 1

        raw = (for_count - against_count) * int(getattr(_cfg, "ASPECT_VALUE", 5))
        cap = int(getattr(_cfg, "MOD_SOURCE_CAPS", {}).get("aspect", 20))
        return max(-cap, min(cap, raw))

    @staticmethod
    def _calculate_status_mod(context: GameContext) -> int:
        """Status effect modifiers based on action_meta.type."""
        import config as _cfg
        from game_character import normalize_status_effects

        status_effects = (context.narrative_anchors or {}).get("status_effects", [])
        effects = normalize_status_effects(status_effects)

        action_meta = context.shared_bus.dai.get("action_meta", {})
        action_type = str(action_meta.get("type") or action_meta.get("action_type") or "").strip().lower()

        total = 0
        for eff in effects:
            if not isinstance(eff, dict):
                continue
            mods = eff.get("modifiers")
            if not isinstance(mods, dict):
                continue
            if action_type:
                type_key = f"judgment_{action_type}"
                if type_key in mods:
                    total += mods[type_key]
                elif "judgment" in mods:
                    total += mods["judgment"]
            else:
                if "judgment" in mods:
                    total += mods["judgment"]

        cap = int(getattr(_cfg, "MOD_SOURCE_CAPS", {}).get("status", 20))
        return max(-cap, min(cap, total))

    async def process(self, context: GameContext) -> GameContext:
        bus = context.shared_bus
        if not bus.judgment.get("active"):
            return context

        # 1. Gather Params
        meta = bus.judgment.get("meta", {})
        eval_data = bus.judgment.get("eval", {})
        action = meta.get("action", "행동")
        difficulty = meta.get("difficulty", "normal")
        
        # DC Table
        dc_table = {"trivial": 0, "easy": 20, "normal": 40, "hard": 60, "extreme": 80}
        dc = dc_table.get(difficulty.lower(), 40)

        # 1b. Position → DC Modifier (DAI position = PC의 통제력)
        position_data = bus.dai.get("position", {})
        pos_value = float(position_data.get("value", 0.5)) if isinstance(position_data, dict) else 0.5
        # Position 0.5 = neutral (no modifier). Each 0.1 away = ±5 DC
        # 0.0 (desperate) → DC+25, 0.5 (neutral) → DC+0, 1.0 (dominant) → DC-25
        position_dc_mod = int((0.5 - pos_value) * 50)
        dc = max(0, dc + position_dc_mod)

        # 2. Dynamic Modifiers
        # 2.1 Vigor/Composure Modifiers — 장르 primary axis 기반
        import config as _cfg
        mechanic = context.request.genres.get("mechanic", {})
        primary_axis = mechanic.get("primary_resource") or "vigor"
        primary_val = getattr(bus, primary_axis).get("value", 100)
        mental_mod = 0
        mental_label = "충만"
        if primary_val >= 70:
            mental_mod = 20
            mental_label = "충만"
        elif primary_val >= 40:
            mental_mod = 10
            mental_label = "동요"
        elif primary_val >= 15:
            mental_mod = 0
            mental_label = "고갈"
        else:
            mental_mod = -10
            mental_label = "붕괴"
            
        # 2.2 Memo Bonus (Flash가 관련 메모/단서 발견 시 +0~10)
        memo_relevant = eval_data.get("memo_relevant")
        memo_mod = 0
        memo_label = ""
        if isinstance(memo_relevant, dict):
            memo_mod = max(0, min(10, int(memo_relevant.get("bonus", 0) or 0)))
            memo_label = str(memo_relevant.get("content", ""))[:20]
        elif isinstance(memo_relevant, list) and memo_relevant:
            # 레거시 호환: 문자열 리스트면 개당 +3 (최대 +10)
            memo_mod = min(10, len(memo_relevant) * 3)
            memo_label = str(memo_relevant[0])[:20]

        # 2.4b Theory Modifier (Flash psyche → ±20)
        theory_mod = self._calculate_theory_mod(context)

        # 2.5 Passive Modifiers (action_type 기반)
        import config as _cfg2
        from game_character import get_inventory_items as _get_inv
        action_meta = bus.judgment.get("meta", {})
        action_type = str(action_meta.get("type") or action_meta.get("action_type") or "").strip().lower()
        passives = (context.narrative_anchors or {}).get("passives", [])
        passive_mod = 0
        for passive in passives:
            mods = _cfg2.get_passive_modifiers(passive)
            if not mods:
                continue
            if action_type and f"judgment_{action_type}" in mods:
                passive_mod += mods[f"judgment_{action_type}"]
            elif "judgment" in mods:
                passive_mod += mods["judgment"]
        passive_mod = max(-20, min(20, passive_mod))

        # 2.5b Inventory Modifiers (action_type 기반)
        inv_items = _get_inv(context.narrative_anchors)
        inv_mod = 0
        for item in inv_items:
            mods = _cfg2.get_item_modifiers(item)
            if not mods:
                continue
            if action_type and f"judgment_{action_type}" in mods:
                inv_mod += mods[f"judgment_{action_type}"]
            elif "judgment" in mods:
                inv_mod += mods["judgment"]
        inv_mod = max(-10, min(15, inv_mod))

        # 2.6 Status Modifiers (status_effects)
        status_mod = self._calculate_status_mod(context)

        # 2.7 Momentum (이전 턴 판정 결과의 여운)
        momentum_mod = int(bus.judgment.get("momentum_carry", 0) or 0)
        momentum_mod = max(-10, min(10, momentum_mod))
        bus.judgment["momentum_carry"] = 0  # 1회성 소비

        # 2.8 Condition Modifier (Active Conditions at PC's location)
        condition_mod = 0
        _j_ch = (context.narrative_anchors or {}).get("channel_id", "")
        if _j_ch:
            import domain_manager as _dm_j
            _j_st = _dm_j.get_storyteller_state(_j_ch)
            _j_pc_loc = (bus.dai.get("current_location", "") if isinstance(bus.dai, dict) else "").strip()
            for _jc in _j_st.get("active_conditions", []):
                _jc_loc = (_jc.get("location") or "").strip()
                if _jc_loc and _jc_loc != _j_pc_loc:
                    continue  # Distant — skip
                pol = _jc.get("polarity", "mixed")
                if pol == "mixed":
                    continue
                base = _cfg.CONDITION_MOD_SCALE.get(_jc.get("intensity", "Mid"), 4)
                if pol == "negative":
                    condition_mod -= base
                elif pol == "positive":
                    condition_mod += base
            condition_mod = max(-_cfg.CONDITION_MOD_CAP, min(_cfg.CONDITION_MOD_CAP, condition_mod))

        # 2.9 Effort Modifier (각오 선불 — Cypher Effort)
        effort_mod = 0
        action_meta = bus.dai.get("action_meta", {}) if isinstance(bus.dai, dict) else {}
        resolve = action_meta.get("resolve", "none")
        if resolve == "desperate" and bus.judgment.get("active"):
            effort_cost = _cfg.EFFORT_COST
            axis_choice = action_meta.get("resource_axis", "vigor")
            if axis_choice == "both":
                mechanic = context.request.genres.get("mechanic", {})
                axis_choice = mechanic.get("primary_resource") or "vigor"
            axis_bus = bus.vigor if axis_choice == "vigor" else bus.composure
            if axis_bus.get("value", 0) >= effort_cost:
                axis_bus["value"] = max(0, axis_bus["value"] - effort_cost)
                effort_mod = _cfg.EFFORT_BONUS
                bus.judgment["effort_used"] = {
                    "axis": axis_choice, "cost": effort_cost, "bonus": effort_mod,
                    "action": action_meta.get("action", ""),
                }
            else:
                bus.dai["effort_failed"] = True

        # 2.10 Context Modifiers (Flash 분석 보정 — 특질/상황)
        _flash_mods = bus.judgment.get("modifications", [])
        context_mod = 0
        for _fm in _flash_mods:
            if isinstance(_fm, dict):
                try:
                    context_mod += int(_fm.get("value", 0) or 0)
                except (ValueError, TypeError):
                    pass
        context_mod = max(-40, min(40, context_mod))

        # 3. Roll Dice
        roll = random.randint(1, 100)
        aspect_mod = self._calculate_aspect_mod(context)
        final_roll = roll + mental_mod + theory_mod + memo_mod + passive_mod + inv_mod + status_mod + aspect_mod + momentum_mod + condition_mod + effort_mod + context_mod
        
        # 4. Determine Result
        result = "failure"
        # Success/Failure/Critical logic
        if roll >= 96: 
            result = "critical_success"
        elif roll <= 5:
            # Safeguard: DC <= 20 (Trivial, Easy) only crit fail on 1
            if dc <= 20 and roll > 1:
                result = "failure"
            else:
                result = "critical_failure"
        elif final_roll >= dc + 20:
            result = "critical_success"
        elif final_roll >= dc: 
            result = "success"
        elif final_roll >= dc - 20:
            result = "partial"

        # 4b. Effect → Result Tier Shift (DAI effect = 결과 임팩트)
        effect_data = bus.dai.get("effect", {})
        eff_value = float(effect_data.get("value", 0.5)) if isinstance(effect_data, dict) else 0.5
        # High effect (>=0.7) can upgrade partial→success or success→critical_success
        # Low effect (<=0.3) can downgrade success→partial or partial→failure
        if result not in ("critical_success", "critical_failure"):
            if eff_value >= 0.7 and result in ("partial", "success"):
                _tier_order = ["failure", "partial", "success", "critical_success"]
                _idx = _tier_order.index(result)
                if _idx < len(_tier_order) - 1:
                    result = _tier_order[_idx + 1]
                    bus.judgment["effect_shift"] = "upgrade"
            elif eff_value <= 0.3 and result in ("partial", "success"):
                _tier_order = ["failure", "partial", "success", "critical_success"]
                _idx = _tier_order.index(result)
                if _idx > 0:
                    result = _tier_order[_idx - 1]
                    bus.judgment["effect_shift"] = "downgrade"

        # 5. Store Result
        bus.judgment["position_value"] = pos_value
        bus.judgment["effect_value"] = eff_value
        bus.judgment["position_dc_mod"] = position_dc_mod
        bus.judgment["roll"] = roll
        bus.judgment["final_roll"] = final_roll
        bus.judgment["dc"] = dc
        bus.judgment["result"] = result
        bus.judgment["reason"] = eval_data.get("reason", "")
        
        # 6. Format Output (Multi-line)
        res_map = {
            "critical_success": ("✨대성공", "Critical Success"),
            "success": ("✅성공", "Success"),
            "partial": ("🟠부분 성공", "Partial Success"),
            "failure": ("❌실패", "Failure"),
            "critical_failure": ("⚠️대실패", "Critical Failure")
        }
        res_kr, res_en = res_map.get(result, (result, result))
        
        diff_name = difficulty.upper()
        
        # Breakdown of modifications: Label(+Val)
        modifications = bus.judgment.get("modifications", [])
        # Append System Mods for visibility
        if mental_mod != 0:
            modifications.append({"label": f"기력({mental_label})", "value": mental_mod})
        if memo_mod > 0:
            modifications.append({"label": f"메모({memo_label})", "value": memo_mod})
        if theory_mod != 0:
            modifications.append({"label": "심리상태", "value": theory_mod})
        if passive_mod != 0:
            modifications.append({"label": "특질", "value": passive_mod})
        if inv_mod != 0:
            modifications.append({"label": "장비", "value": inv_mod})
        if status_mod != 0:
            modifications.append({"label": "상태", "value": status_mod})
        if aspect_mod != 0:
            modifications.append({"label": "면모", "value": aspect_mod})
        if momentum_mod != 0:
            modifications.append({"label": "기세", "value": momentum_mod})
        if condition_mod != 0:
            modifications.append({"label": "세계상황", "value": condition_mod})
        if effort_mod != 0:
            modifications.append({"label": "각오", "value": effort_mod})
        if position_dc_mod != 0:
            pos_label = "유리" if position_dc_mod < 0 else "불리"
            modifications.append({"label": f"포지션({pos_label})", "value": -position_dc_mod})  # Inverted: lower DC = better
        if bus.judgment.get("effect_shift"):
            shift_label = "효과↑" if bus.judgment["effect_shift"] == "upgrade" else "효과↓"
            modifications.append({"label": shift_label, "value": 0})

        mod_parts = []
        for m in modifications:
            label = m.get("label", "Unknown")
            val = m.get("value", 0)
            sign = "+" if val >= 0 else ""
            mod_parts.append(f"{label}({sign}{val})")
        
        mod_details = ", ".join(mod_parts)
        if mod_details:
            mod_details = f", {mod_details}"
        
        mask = context.get_acting_mask()
        bus.judgment["mask"] = mask

        output = [
            f"🎲 **[{mask}의 판정: {action}]**",
            f"난이도: **{diff_name}** (DC {dc})",
            f"이유: *{bus.judgment['reason']}*",
            f"주사위: **{roll}** {mod_details} = **{final_roll}**",
            f"결과: **{res_kr} ({res_en})**"
        ]
        
        # Narrative Hook (Only for Partial or Failure)
        hook = bus.judgment.get("narrative_hook")
        if hook and result in ["partial", "failure", "critical_failure"]:
            output.append(f"\n⚠️ **잠재적 위기 (Narrative Hook)**: {hook}")
            if result == "critical_failure":
                bus.judgment["party_wide_hook"] = True

        # 7. Apply Consequences (doom, primary axis, clocks, momentum)
        _apply_consequences(context, result)

        # Consequence log → Discord output
        consequence_log = bus.judgment.get("consequence_log", "")
        if consequence_log:
            output.append(f"\n📋 **결과 영향**: {consequence_log}")

        bus.judgment["output"] = "\n".join(output)

        logger.info("[Judgment] %s → %s | roll=%d %s = %d vs DC=%d (%s)",
                     action, res_kr, roll, mod_details.strip(", "), final_roll, dc, difficulty)

        return context


# ── Consequence Helpers ──────────────────────────────────────

def _apply_consequences(context, result: str) -> None:
    """판정 결과 → 기계적 세계 변경. BitD/PbtA/Cypher 영감."""
    import config as _cfg
    bus = context.shared_bus
    cons = getattr(_cfg, "JUDGMENT_CONSEQUENCES", {}).get(result, {})
    if not cons:
        return

    consequence_log = []

    # 0. Absorb: effort 선언 + 실패 → doom/clock consequence 자동 경감
    doom_delta = cons.get("doom_delta", 0)
    clock_effect = cons.get("clock_effect", 0)
    effort_used = bus.judgment.get("effort_used")
    if effort_used and result in ("failure", "critical_failure"):
        doom_reduction = doom_delta // 2
        clock_cancelled = clock_effect > 0
        if doom_reduction > 0:
            doom_delta -= doom_reduction
        if clock_cancelled:
            clock_effect = 0
        bus.judgment["absorb_applied"] = {
            "doom_reduced": doom_reduction,
            "clock_cancelled": clock_cancelled,
        }

    # A. Doom Delta → bus.doom["delta"]에 누적 (Doom 모듈이 자연 소비)
    if doom_delta != 0:
        bus.doom["delta"] = bus.doom.get("delta", 0) + doom_delta

    # B. Primary Axis Direct Impact
    primary_delta = cons.get("primary_delta", 0)
    if primary_delta != 0:
        mechanic = context.request.genres.get("mechanic", {})
        primary_axis = mechanic.get("primary_resource") or "vigor"
        p_bus = getattr(bus, primary_axis)
        p_bus["delta"] = p_bus.get("delta", 0) + primary_delta
        sign = "+" if primary_delta > 0 else ""
        consequence_log.append(f"{'회복' if primary_delta > 0 else '소모'} {sign}{primary_delta}")

    # C. Clock Effect (DLC 안전: clocks 없으면 스킵)
    if clock_effect != 0:
        clocks = bus.doom.get("clocks", [])
        if isinstance(clocks, list) and clocks:
            clock_all = cons.get("clock_all", False)
            affected = _apply_clock_consequence(clocks, clock_effect, clock_all)
            consequence_log.extend(affected)

    # D. Momentum (다음 턴 보너스/페널티)
    momentum = cons.get("momentum", 0)
    if momentum != 0:
        bus.judgment["momentum_next"] = momentum
        sign = "+" if momentum > 0 else ""
        consequence_log.append(f"기세 {sign}{momentum}")

    if consequence_log:
        bus.judgment["consequence_log"] = " | ".join(consequence_log)


def _apply_clock_consequence(clocks: list, effect: int, apply_all: bool) -> list:
    """판정 결과 → 시계 변경. effect>0: 전진, effect<0: 후퇴."""
    log = []
    active = [c for c in clocks if isinstance(c, dict) and not c.get("resolved")]
    if not active:
        return log

    if apply_all:
        for clock in active:
            segments = int(clock.get("segments", 4) or 4)
            old = int(clock.get("filled", 0) or 0)
            new = max(0, min(segments, old + effect))
            if new != old:
                clock["filled"] = new
                log.append(f"⏰ {clock.get('name', '?')} {old}→{new}/{segments}")
    else:
        # 가장 위험한(채워진 비율 높은) 시계 선택
        target = max(active, key=lambda c: int(c.get("filled", 0) or 0) / max(int(c.get("segments", 4) or 4), 1))
        segments = int(target.get("segments", 4) or 4)
        old = int(target.get("filled", 0) or 0)
        new = max(0, min(segments, old + effect))
        if new != old:
            target["filled"] = new
            direction = "⬇️" if effect < 0 else "⬆️"
            log.append(f"{direction} {target.get('name', '?')} {old}→{new}/{segments}")
    return log
