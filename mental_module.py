"""
Lorekeeper UNE - Mental Module
Manages player mental health and adaptation logic.
"""

from typing import Dict, Any
from orchestration_context import GameContext

class MentalModule:
    def __init__(self):
        pass

    async def process(self, context: GameContext) -> GameContext:
        bus = context.shared_bus
        delta = bus.mental.get("delta", 0)
        
        if delta == 0:
            return context
            
        # 1. Update Mental Value
        current_mental = bus.mental.get("value", 100)
        new_mental = max(0, min(100, current_mental + delta))
        
        bus.mental["value"] = new_mental
        bus.mental["active"] = True # Mark for sync
        
        if delta < 0:
            bus.mental["log"] = f"🧠 정신력 감소 ({delta})"
        else:
            bus.mental["log"] = f"🧠 정신력 회복 (+{delta})"
            
        return context
