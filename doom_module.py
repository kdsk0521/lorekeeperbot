"""
Lorekeeper UNE - Doom Module
Manages world tension and mechanical side-effects of judgment results.
"""

from typing import Dict, Any, TYPE_CHECKING

if TYPE_CHECKING:
    from orchestration_context import GameContext

class DoomModule:
    def __init__(self):
        pass

    async def process(self, context: "GameContext") -> "GameContext":
        bus = context.shared_bus
        current_doom = bus.doom.get("value", 0)
        
        # 1. Automatic Doom Change based on Judgment
        judgment = bus.judgment
        delta = 0
        if judgment.get("active"):
            res = judgment.get("result")
            if res == "critical_failure": delta = 5
            elif res == "failure": delta = 2
            elif res == "critical_success": delta = -3
        
        # 2. AI-Analyzed Doom Relief (NEW!)
        # If Theoria detected a calming/restorative action
        relief_data = bus.doom.get("relief", {})
        if relief_data.get("applicable", False):
            relief_amount = relief_data.get("amount", 0)
            relief_reason = relief_data.get("reason", "")
            delta -= relief_amount  # Reduce Doom
            bus.doom["relief_log"] = f"🌿 긴장 완화: -{relief_amount} ({relief_reason})"
            
        # 3. Update Bus
        if delta != 0:
            new_doom = max(0, min(100, current_doom + delta))
            bus.doom["value"] = new_doom
            bus.doom["active"] = True # Mark for sync
            
            if delta > 0:
                bus.doom["log"] = f"📈 긴장도 증가 (+{delta})"
            else:
                bus.doom["log"] = f"📉 긴장도 감소 ({delta})"
                
        # 4. Entropy & Rubber-banding (Minimum Tension Floor)
        # If doom is below 20, it naturally rises (+2) to maintain tension
        if bus.doom["value"] < 20:
            old_val = bus.doom["value"]
            bus.doom["value"] = min(20, bus.doom["value"] + 2)
            entropy_delta = bus.doom["value"] - old_val
            
            # Integrate entropy into existing log or create new one
            if bus.doom.get("log"):
                bus.doom["log"] += f" (엔트로피 +{entropy_delta})"
            else:
                bus.doom["log"] = f"📈 긴장도 증가 (+{entropy_delta}, 엔트로피)"
            
            bus.doom["active"] = True

        # 5. Mental Pressure/Recovery from Doom (Only if Mental module is active)
        if "mental" in context.request.active_modules:
            if bus.doom["value"] >= 80:
                # Extreme pressure
                mental_pressure = -2
                bus.mental["delta"] = bus.mental.get("delta", 0) + mental_pressure
                bus.doom["mental_pressure_log"] = "⚠️ 극심한 긴장감"
            elif bus.doom["value"] >= 60:
                # High pressure
                mental_pressure = -1
                bus.mental["delta"] = bus.mental.get("delta", 0) + mental_pressure
                bus.doom["mental_pressure_log"] = "😰 높은 긴장감"
            elif bus.doom["value"] <= 40:
                # Low tension recovery
                mental_recovery = 1
                bus.mental["delta"] = bus.mental.get("delta", 0) + mental_recovery
                bus.doom["mental_pressure_log"] = "😌 평온한 분위기"
        
        # 6. Check for Anomaly Potential
        # Trigger anomaly if doom > 50 or on critical failure
        if bus.doom["value"] > 50 or judgment.get("result") == "critical_failure":
            bus.anomaly["potential"] = True
            
        return context
