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
        
        # Probability Formula: Base 25% + (Doom / 2)
        # - If Doom Module is DISABLED: Doom stays at 0 → Fixed 25% chance
        # - If Doom Module is ENABLED: Min 25%, Max 75% at Doom 100
        trigger_chance = 25 + (doom_val / 2)
        
        roll = random.randint(1, 100)
        if roll > trigger_chance:
            return context


        # 1. Generate Anomaly Info
        bus.anomaly["triggered"] = True
        tag = bus.anomaly.get("tag", "기이한 현상")
        intensity = bus.anomaly.get("intensity", "Mid")
        
        # 2. Adaptation & Mitigation
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
            outcome_msg = " [✨영감]"
            base_dmg = -10 # Recovery
            bus.doom["delta"] = bus.doom.get("delta", 0) - 3
        elif roll <= 10: # Shock
            outcome_msg = " [⚠️쇼크]"
            base_dmg += 15
            bus.doom["delta"] = bus.doom.get("delta", 0) + 2

        # Damage Mitigation from Adaptation (100% Adapt = 0 Damage)
        mitigation = adapt_pct / 100.0
        final_dmg = int(base_dmg * (1.0 - mitigation)) if base_dmg > 0 else base_dmg
        
        # 3. Internal Defense Roll (Only if Judgment is OFF)
        if "judgment" not in context.request.active_modules and base_dmg > 0:
            # Difficulty based on anomaly intensity
            dc_map = {"Low": 30, "Mid": 50, "High": 70, "Extreme": 90}
            dc = dc_map.get(intensity, 50)
            
            # Base success rate = 100 - DC
            success_rate = 100 - dc
            
            # Analyze passives for mental-related bonuses
            passives = context.narrative_anchors.get("passives", [])
            for passive in passives:
                if isinstance(passive, dict):
                    p_name = passive.get("name", "").lower()
                    # Positive traits
                    if any(kw in p_name for kw in ["용감", "냉정", "강인", "침착"]):
                        success_rate += 15
                    # Negative traits
                    elif any(kw in p_name for kw in ["겁쟁이", "나약", "불안", "공포"]):
                        success_rate -= 15
            
            # Clamp success rate
            success_rate = max(10, min(90, success_rate))
            
            # Roll defense
            defense_roll = random.randint(1, 100)
            
            if defense_roll <= success_rate:
                # Success: Reduce damage by 50%
                final_dmg = int(final_dmg * 0.5)
                outcome_msg += " [🛡️대응 성공]"
            else:
                # Failure: Normal damage
                outcome_msg += " [❌대응 실패]"
        
        # 3-2. If Judgment is ON, provide bonus/penalty for next judgment
        elif "judgment" in context.request.active_modules and base_dmg > 0:
            # Difficulty based on anomaly intensity
            dc_map = {"Low": 30, "Mid": 50, "High": 70, "Extreme": 90}
            dc = dc_map.get(intensity, 50)
            
            # Base success rate = 100 - DC
            success_rate = 100 - dc
            
            # Analyze passives
            passives = context.narrative_anchors.get("passives", [])
            for passive in passives:
                if isinstance(passive, dict):
                    p_name = passive.get("name", "").lower()
                    if any(kw in p_name for kw in ["용감", "냉정", "강인", "침착"]):
                        success_rate += 15
                    elif any(kw in p_name for kw in ["겁쟁이", "나약", "불안", "공포"]):
                        success_rate -= 15
            
            success_rate = max(10, min(90, success_rate))
            
            # Roll defense
            defense_roll = random.randint(1, 100)
            
            if defense_roll <= success_rate:
                # Success: Provide bonus to next judgment
                bus.dai["bonus"] = bus.dai.get("bonus", 0) + 10
                bus.dai["reason"] = bus.dai.get("reason", "") + f" [이변 대응 성공 +10]"
                outcome_msg += " [🛡️대응 성공: 다음 판정 +10]"
            else:
                # Failure: Provide penalty to next judgment
                bus.dai["penalty"] = bus.dai.get("penalty", 0) + 10
                bus.dai["reason"] = bus.dai.get("reason", "") + f" [이변 대응 실패 -10]"
                outcome_msg += " [❌대응 실패: 다음 판정 -10]"
        
        # 4. Update Bus
        bus.mental["delta"] = bus.mental.get("delta", 0) - final_dmg
        bus.anomaly["output"] = f"⚡ **이변 발생: [{tag}]**{outcome_msg} (적응도 {adapt_pct}%)"
        
        # 5. Prepare Adaptation Update for Sync
        bus.mental.setdefault("adaptation_update", {})[tag] = {"count": count + 1}
        
        return context
