"""
Lorekeeper UNE - Doom Module
Manages world tension and mechanical side-effects of judgment results.
"""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from orchestration_context import GameContext

class DoomModule:
    def __init__(self):
        pass

    async def process(self, context: "GameContext") -> "GameContext":
        bus = context.shared_bus
        current_doom = bus.doom.get("value", 0)
        
        # 1. Consume pre-existing doom delta (Judgment heroism/calamity: ±5)
        delta = bus.doom.get("delta", 0)

        # 1a. Judgment result-based doom change
        judgment = bus.judgment
        if judgment.get("active"):
            res = judgment.get("result")
            if res == "critical_failure": delta += 5
            elif res == "failure": delta += 2
            elif res == "critical_success": delta -= 3
        
        # 2. AI-Analyzed Doom Relief
        relief_data = bus.doom.get("relief", {})
        if relief_data.get("applicable", False):
            relief_amount = relief_data.get("amount", 0)
            relief_reason = relief_data.get("reason", "")
            delta -= relief_amount  # Reduce Doom
            bus.doom["relief_log"] = f"🌿 긴장 완화: -{relief_amount} ({relief_reason})"
            
        # 3. Update Bus
        bus.doom["delta"] = 0  # Consumed — Anomaly can write fresh delta after this
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

        # 5. 기력 Pressure/Recovery from 8-Segment Doom Clock (FitD)
        if "mental" in context.request.active_modules:
            dv = bus.doom["value"]
            if dv >= 88:
                pressure, label = -3, "⚠️ 위협 시계 [임박] (기력 -3)"
            elif dv >= 76:
                pressure, label = -2, "⚠️ 위협 시계 [위기] (기력 -2)"
            elif dv >= 63:
                pressure, label = -1, "😰 위협 시계 [위협] (기력 -1)"
            elif dv >= 50:
                pressure, label = -1, "😰 위협 시계 [긴장] (기력 -1)"
            elif dv >= 38:
                pressure, label = 0, ""   # 경계 — 중립
            elif dv >= 25:
                pressure, label = 0, ""   # 중립 — 자연회복 구간
            elif dv >= 13:
                pressure, label = 1, "😌 위협 이완 [안정] (기력 +1)"
            else:
                pressure, label = 2, "😌 위협 이완 [이완] (기력 +2)"

            if pressure != 0:
                bus.mental["delta"] = bus.mental.get("delta", 0) + pressure
                bus.doom["mental_pressure_log"] = label
        
        return context
