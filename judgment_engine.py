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
        
        # 2. Roll Dice
        roll = random.randint(1, 100)
        bonus = eval_data.get("bonus", 0)
        penalty = eval_data.get("penalty", 0)
        final_roll = roll + bonus - penalty
        
        # 3. Determine Result
        result = "failure"
        if roll >= 96: result = "critical_success"
        elif roll <= 5: result = "critical_failure"
        elif final_roll >= dc: result = "success"
        elif final_roll >= dc - 20: result = "partial"
        
        # 4. Store Result
        bus.judgment["roll"] = roll
        bus.judgment["final_roll"] = final_roll
        bus.judgment["dc"] = dc
        bus.judgment["result"] = result
        
        res_kr = {
            "critical_success": "✨대성공", "success": "✅성공", 
            "partial": "🟠부분 성공", "failure": "❌실패", 
            "critical_failure": "⚠️대실패"
        }.get(result, result)
        
        bus.judgment["output"] = f"🎲 **판정 ({action})**: {roll} + {bonus}(보정) - {penalty}(페널티) = **{final_roll}** (DC {dc}) -> **{res_kr}**"
        
        return context
