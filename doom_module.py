"""
Lorekeeper UNE - Doom Module
Manages world tension and mechanical side-effects of judgment results.
"""

from typing import Dict, Any
from orchestration_context import GameContext

class DoomModule:
    def __init__(self):
        pass

    async def process(self, context: GameContext) -> GameContext:
        bus = context.shared_bus
        current_doom = bus.doom.get("value", 0)
        
        # 1. Automatics Doom Change based on Judgment
        judgment = bus.judgment
        delta = 0
        if judgment.get("active"):
            res = judgment.get("result")
            if res == "critical_failure": delta = 5
            elif res == "failure": delta = 2
            elif res == "critical_success": delta = -3
            
        # 2. Update Bus
        if delta != 0:
            new_doom = max(0, min(100, current_doom + delta))
            bus.doom["value"] = new_doom
            bus.doom["active"] = True # Mark for sync
            
            if delta > 0:
                bus.doom["log"] = f"📈 긴장도 증가 (+{delta})"
            else:
                bus.doom["log"] = f"📉 긴장도 감소 ({delta})"
                
        # 3. Entropy & Rubber-banding (Legacy: Floor 20)
        # If doom is below 20, it naturally rises (+2) to maintain tension
        if bus.doom["value"] < 20:
            bus.doom["value"] = min(20, bus.doom["value"] + 2)
            entropy_msg = "🌓 월드 엔트로피 (긴장도 하한선 유지 +2)"
            if bus.doom.get("log"):
                bus.doom["log"] += f"\n{entropy_msg}"
            else:
                bus.doom["log"] = entropy_msg
            bus.doom["active"] = True

        # 4. Check for Anomaly Potential
        # Trigger anomaly if doom > 50 or on critical failure
        if bus.doom["value"] > 50 or judgment.get("result") == "critical_failure":
            bus.anomaly["potential"] = True
            
        return context
