"""
Lorekeeper UNE - Judgment Engine (Call 2)
Handles dice rolls and narrative outcomes based on Call 1 analysis.
"""

import logging
import random
from typing import Dict, Any
from orchestration_context import GameContext

logger = logging.getLogger("Judgment")

class JudgmentEngine:
    def __init__(self, client, model_id: str):
        self.client = client
        self.model_id = model_id

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
        
        # 2. Dynamic Modifiers (Legacy Restoration)
        # 2.1 Mental Modifiers (+20 / +10 / 0 / -10)
        mental_val = bus.mental.get("value", 100)
        mental_mod = 0
        mental_label = "평정"
        if mental_val >= 70: 
            mental_mod = 20
            mental_label = "평정"
        elif mental_val >= 40: 
            mental_mod = 10
            mental_label = "동요"
        elif mental_val >= 15: 
            mental_mod = 0
            mental_label = "공황"
        else: 
            mental_mod = -10
            mental_label = "붕괴"
            
        # 2.2 Doom Modifiers (Baseline 50, Step 5)
        current_doom = bus.doom.get("value", 0)
        doom_mod = ((50 - current_doom) // 10) * 5
        
        # 2.3 DAI Modifiers (from Anomaly defense success/failure)
        dai_bonus = bus.dai.get("bonus", 0)
        dai_penalty = bus.dai.get("penalty", 0)
        dai_reason = bus.dai.get("reason", "")
        
        # 3. Roll Dice
        roll = random.randint(1, 100)
        eval_bonus = eval_data.get("bonus", 0)
        eval_penalty = eval_data.get("penalty", 0)
        
        final_roll = roll + eval_bonus - eval_penalty + mental_mod + doom_mod + dai_bonus - dai_penalty
        
        # 4. Determine Result
        result = "failure"
        # Heroism/Calamity Impact (Natural Roll)
        if roll >= 96:
            bus.doom["delta"] = bus.doom.get("delta", 0) - 5
        elif roll <= 5:
            bus.doom["delta"] = bus.doom.get("delta", 0) + 5

        # Success/Failure/Critical logic
        if roll >= 96: 
            result = "critical_success"
        elif roll <= 5:
            # Safeguard: DC <= 20 (Trivial, Easy) only crit fail on 1
            if dc <= 20 and roll > 1:
                result = "failure"
            else:
                result = "critical_failure"
        elif final_roll >= dc + 30:
            result = "critical_success"
        elif final_roll >= dc: 
            result = "success"
        elif final_roll >= dc - 30: 
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
            modifications.append({"label": f"정신({mental_label})", "value": mental_mod})
        if doom_mod != 0:
            modifications.append({"label": "월드긴장", "value": doom_mod})
        if dai_bonus > 0:
            modifications.append({"label": "이변대응성공", "value": dai_bonus})
        if dai_penalty > 0:
            modifications.append({"label": "이변대응실패", "value": -dai_penalty})
            
        mod_parts = []
        for m in modifications:
            label = m.get("label", "Unknown")
            val = m.get("value", 0)
            sign = "+" if val >= 0 else ""
            mod_parts.append(f"{label}({sign}{val})")
        
        mod_details = ", ".join(mod_parts)
        if mod_details:
            mod_details = f", {mod_details}"
        
        # Resolve acting PC mask
        anchors = context.narrative_anchors or {}
        acting_uid = anchors.get("acting_user_id", "")
        all_pcs = anchors.get("all_pcs", {})
        if acting_uid and acting_uid in all_pcs:
            mask = all_pcs[acting_uid].get("mask", "PC")
        else:
            mask = "PC"
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
        
        return context
