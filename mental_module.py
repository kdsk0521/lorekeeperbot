"""
Lorekeeper UNE - Mental Module
Manages player mental health and adaptation logic.
"""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from orchestration_context import GameContext

class MentalModule:
    def __init__(self):
        pass

    async def process(self, context: "GameContext") -> "GameContext":
        bus = context.shared_bus
        
        # 1. Collect Delta from multiple sources
        delta = bus.mental.get("delta", 0)  # From Anomaly Module
        
        # 2. AI-Analyzed Mental Impact
        impact_data = bus.mental.get("impact", {})
        if impact_data.get("applicable", False):
            impact_delta = impact_data.get("delta", 0)
            impact_reason = impact_data.get("reason", "")
            delta += impact_delta  # Add to total delta
            bus.mental["impact_log"] = f"🧠 정신적 영향: {impact_delta:+d} ({impact_reason})"
        
        if delta == 0:
            return context
            
        # 3. Inertia (Successive changes amplification)
        last_delta = bus.mental.get("last_delta", 0)
        actual_delta = delta
        if (delta > 0 and last_delta > 0) or (delta < 0 and last_delta < 0):
            actual_delta = int(delta * 1.1)
            
        # 4. Clamping (Max 2 stage drop per turn)
        current_mental = bus.mental.get("value", 100)
        
        def get_stage(val):
            if val >= 70: return 0
            if val >= 40: return 1
            if val >= 15: return 2
            return 3
            
        current_stage = get_stage(current_mental)

        # Clamp floor based on base delta (pre-inertia)
        base_target = max(0, min(100, current_mental + delta))
        base_stage = get_stage(base_target)
        clamp_floor = base_target

        if base_stage > current_stage + 2:
            limit_stage = current_stage + 2
            floors = {0: 70, 1: 40, 2: 15, 3: 0}
            clamp_floor = floors.get(limit_stage, 0)

        target_mental = max(0, min(100, current_mental + actual_delta))
        clamped = False
        if actual_delta < 0:
            if target_mental < clamp_floor:
                target_mental = clamp_floor
                clamped = True

        # 5. Trauma Awakening (Collapse -> Recovery)
        trauma_triggered = False
        if current_stage == 3 and actual_delta > 0:
            target_mental = 90  # High Calm reset
            trauma_triggered = True
            bus.mental["trauma_trigger"] = True
        
        # 6. Update Bus
        bus.mental["value"] = target_mental
        bus.mental["active"] = True
        bus.mental["last_delta"] = delta
        
        mask = context.get_acting_mask()

        # Compact log format
        log_parts = []

        # Base change with mask and current value
        if actual_delta < 0:
            log_parts.append(f"{mask}: 🧠 정신력 {actual_delta} → {target_mental}/100")
        else:
            log_parts.append(f"{mask}: 🧠 정신력 +{actual_delta} → {target_mental}/100")
        
        # Modifiers with emphasis
        if clamped:
            log_parts.append("\n❗ **충격 완화** (Clamping)")
        if trauma_triggered:
            log_parts.append("\n✨ **트라우마 각성** (Awakening)")
        
        bus.mental["log"] = "".join(log_parts)
            
        return context
