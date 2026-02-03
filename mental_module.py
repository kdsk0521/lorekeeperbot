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
            
        # 1. Inertia (Successive changes amplification)
        last_delta = bus.mental.get("last_delta", 0)
        actual_delta = delta
        if (delta > 0 and last_delta > 0) or (delta < 0 and last_delta < 0):
            actual_delta = int(delta * 1.1)
            
        # 2. Clamping (Max 2 stage drop per turn)
        current_mental = bus.mental.get("value", 100)
        
        def get_stage(val):
            if val >= 70: return 0
            if val >= 40: return 1
            if val >= 15: return 2
            return 3
            
        current_stage = get_stage(current_mental)
        target_mental = max(0, min(100, current_mental + actual_delta))
        target_stage = get_stage(target_mental)
        
        clamped = False
        if actual_delta < 0 and target_stage > current_stage + 2:
            limit_stage = current_stage + 2
            # Set to minimum of the limit stage
            floors = {0: 70, 1: 40, 2: 15, 3: 0}
            target_mental = floors.get(limit_stage, 0)
            target_stage = limit_stage
            clamped = True

        # 3. Trauma Awakening (Collapse -> Recovery)
        trauma_triggered = False
        if current_stage == 3 and actual_delta > 0:
            target_mental = 90 # High Calm reset
            target_stage = 0
            trauma_triggered = True
            bus.mental["trauma_trigger"] = True
        
        # 4. Update Bus
        bus.mental["value"] = target_mental
        bus.mental["active"] = True
        
        log_parts = []
        if actual_delta < 0:
            log_parts.append(f"🧠 정신력 감소 ({actual_delta})")
        else:
            log_parts.append(f"🧠 정신력 회복 (+{actual_delta})")
            
        if clamped:
            log_parts.append(" ❗충격 완화(Clamping)")
        if trauma_triggered:
            log_parts.append("\n✨ **트라우마 각성(Awakening)**: 정신을 가다듬었으나 깊은 흉터가 남았습니다. (-5 패시브)")
            
        bus.mental["log"] = "".join(log_parts)
            
        return context
