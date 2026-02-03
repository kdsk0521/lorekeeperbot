"""
Lorekeeper UNE - Anomaly Module
Handles supernatural events and their mental impact.
"""

import logging
from typing import Dict, Any
from orchestration_context import GameContext

logger = logging.getLogger("Anomaly")

class AnomalyModule:
    def __init__(self, client, model_id: str):
        self.client = client
        self.model_id = model_id

    async def process(self, context: GameContext) -> GameContext:
        bus = context.shared_bus
        if not bus.anomaly.get("potential"):
            return context

        # [Rule] Trigger Anomaly based on Doom probability
        import random
        doom_val = bus.doom.get("value", 0)
        # Prob = Doom / 2 (e.g. 100 Doom = 50% chance, 50 Doom = 25% chance)
        if random.randint(1, 100) > (doom_val / 2):
            return context

        # 1. Generate Anomaly Content
        # We can use game_world helper or Call LLM here.
        # For now, we'll mark it as triggered and let LLM generate text via Directive.
        bus.anomaly["triggered"] = True
        bus.anomaly["tag"] = "기이한 현상"
        bus.anomaly["intensity"] = "Mid"
        
        # 2. Add impact to Mental
        bus.mental["delta"] = bus.mental.get("delta", 0) - 10 # Default anomaly impact
        
        return context
