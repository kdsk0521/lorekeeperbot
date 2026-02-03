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
        import math
        doom_val = bus.doom.get("value", 0)
        # Prob = Doom / 2 (e.g. 100 Doom = 50% chance)
        roll = random.randint(1, 100)
        if roll > (doom_val / 2):
            return context

        # 1. Generate Anomaly Info
        bus.anomaly["triggered"] = True
        tag = bus.anomaly.get("tag", "기이한 현상")
        intensity = bus.anomaly.get("intensity", "Mid")
        
        # 2. Adaptation & Mitigation (Legacy Logic)
        adaptation_data = bus.mental.get("adaptation", {})
        tag_exposure = adaptation_data.get(tag, {"count": 0})
        count = tag_exposure.get("count", 0)
        
        # Log-scale Adaptation: math.log(count + 1) * 25
        adapt_pct = min(100, int(math.log(count + 1) * 25))
        
        damage_map = {"Low": 5, "Mid": 10, "High": 20, "Extreme": 35}
        base_dmg = damage_map.get(intensity, 10)
        
        # Special Outcomes based on Anomaly Trigger Roll
        outcome_msg = ""
        if roll >= 90: # Inspiration
            outcome_msg = " [✨영감: 정신력 회복 및 통찰력 증가]"
            base_dmg = -10 # Recovery
            bus.doom["delta"] = bus.doom.get("delta", 0) - 3
        elif roll <= 10: # Shock
            outcome_msg = " [⚠️쇼크: 정신적 충격]"
            base_dmg += 15
            bus.doom["delta"] = bus.doom.get("delta", 0) + 2

        # Damage Mitigation (Max 50% at 100% Adapt)
        mitigation = adapt_pct / 200.0
        final_dmg = int(base_dmg * (1.0 - mitigation)) if base_dmg > 0 else base_dmg
        
        # 3. Update Bus
        bus.mental["delta"] = bus.mental.get("delta", 0) - final_dmg
        bus.anomaly["output"] = f"⚡ **이변 발생: [{tag}]**{outcome_msg} (적응도 {adapt_pct}%)"
        
        # 4. Prepare Adaptation Update for Sync
        bus.mental.setdefault("adaptation_update", {})[tag] = {"count": count + 1}
        
        return context
