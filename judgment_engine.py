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
            mod += 10 if is_combat else -10
        elif polyvagal == "sympathetic":
            mod -= 5
        elif polyvagal == "ventral":
            mod += 5 if is_social else 0

        # 2. Decision mode × action_type
        if decision_mode == "reactive":
            mod += 5 if is_combat else -5
        elif decision_mode == "deliberate":
            mod += 5 if is_social else -5

        # 3. Cultural affect
        if cultural_affect == "hwabyung" and is_social:
            mod -= 5
        elif cultural_affect == "gi" and is_combat:
            mod += 5
        elif cultural_affect == "nunchi" and is_social:
            mod -= 5
        elif cultural_affect == "chaemyeon" and is_social:
            mod += 5
        elif cultural_affect == "han" and is_social:
            mod += 5

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
            
        # 2.2 Doom Modifiers (Baseline 50, Step 5)
        current_doom = bus.doom.get("value", 0)
        doom_mod = ((50 - current_doom) // 10) * 5
        
        # 2.3 DAI Modifiers (from Anomaly defense success/failure)
        dai_bonus = bus.dai.get("bonus", 0)
        dai_penalty = bus.dai.get("penalty", 0)

        # 2.4 Theory Modifier (Flash psyche → ±20)
        theory_mod = self._calculate_theory_mod(context)

        # 2.5 Passive Modifiers (theory tag based)
        import config as _cfg2
        passives = (context.narrative_anchors or {}).get("passives", [])
        passive_mod = 0
        scene_type = bus.dai.get("scene_type", "normal")
        is_combat_scene = scene_type == "combat"
        is_social_scene = scene_type in ("social", "normal", "intimate")
        for passive in passives:
            mods = _cfg2.get_passive_modifiers(passive)
            if not mods:
                continue
            # Scene-type specific key first, then generic
            if is_combat_scene and "judgment_combat" in mods:
                passive_mod += mods["judgment_combat"]
            elif is_social_scene and "judgment_social" in mods:
                passive_mod += mods["judgment_social"]
            elif "judgment" in mods:
                passive_mod += mods["judgment"]
        passive_mod = max(-20, min(20, passive_mod))

        # 2.6 Status Modifiers (status_effects)
        status_mod = self._calculate_status_mod(context)

        # 3. Roll Dice
        roll = random.randint(1, 100)
        aspect_mod = self._calculate_aspect_mod(context)
        final_roll = roll + mental_mod + doom_mod + theory_mod + passive_mod + status_mod + aspect_mod + dai_bonus - dai_penalty
        
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
        elif final_roll >= dc - 15: 
            result = "partial"
        
        # 5. Store Result
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
        if doom_mod != 0:
            modifications.append({"label": "월드긴장", "value": doom_mod})
        if dai_bonus > 0:
            modifications.append({"label": "이변대응성공", "value": dai_bonus})
        if dai_penalty > 0:
            modifications.append({"label": "이변대응실패", "value": -dai_penalty})
        if theory_mod != 0:
            modifications.append({"label": "심리상태", "value": theory_mod})
        if passive_mod != 0:
            modifications.append({"label": "특질", "value": passive_mod})
        if status_mod != 0:
            modifications.append({"label": "상태", "value": status_mod})
        if aspect_mod != 0:
            modifications.append({"label": "면모", "value": aspect_mod})

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
            
        bus.judgment["output"] = "\n".join(output)

        # 7. Clear DAI bonus/penalty after consumption (1회성)
        if dai_bonus or dai_penalty:
            bus.dai["bonus"] = 0
            bus.dai["penalty"] = 0
            bus.dai["reason"] = ""

        return context
