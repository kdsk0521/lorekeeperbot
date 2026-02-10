"""
Lorekeeper UNE - Mental Module
Manages player mental health and adaptation logic.
"""

from typing import TYPE_CHECKING
import config

if TYPE_CHECKING:
    from orchestration_context import GameContext

class MentalModule:
    def __init__(self):
        pass

    async def process(self, context: "GameContext") -> "GameContext":
        bus = context.shared_bus
        
        # 1. Collect Delta from multiple sources
        delta = bus.mental.get("delta", 0)  # From Doom pressure/recovery + Anomaly damage

        # 1a. Rest Recovery (휴식 회복)
        rest_eval = bus.dai.get("rest_eval")
        if rest_eval and rest_eval.get("detected"):
            quality = rest_eval.get("quality", "brief")
            base_recovery = config.REST_RECOVERY.get(quality, 10)
            if not rest_eval.get("safe_location", True):
                base_recovery = int(base_recovery * config.REST_UNSAFE_MODIFIER)
            delta += base_recovery
            safe_tag = "안전" if rest_eval.get("safe_location", True) else "위험"
            bus.mental["rest_log"] = f"💤 휴식({quality}) +{base_recovery} ({safe_tag})"

        # 1b. Judgment Emotional Impact (직접적 감정 효과)
        if bus.judgment.get("active"):
            j_result = bus.judgment.get("result", "")
            j_emotion = {
                "critical_success": 3,   # 고양감
                "success": 1,            # 자신감
                "partial": 0,            # 긴장 유지
                "failure": -2,           # 좌절
                "critical_failure": -4,  # 절망
            }.get(j_result, 0)
            if j_emotion != 0:
                delta += j_emotion
                bus.mental["judgment_emotion"] = j_emotion

        # 2. AI-Analyzed Mental Impact
        impact_data = bus.mental.get("impact", {})
        if impact_data.get("applicable", False):
            impact_delta = impact_data.get("delta", 0)
            impact_reason = impact_data.get("reason", "")
            delta += impact_delta  # Add to total delta
            bus.mental["impact_log"] = f"🧠 기력 영향: {impact_delta:+d} ({impact_reason})"
        
        # 2a. Natural Recovery (평온한 턴: 외부 자극 없음)
        if delta == 0:
            current_mental = bus.mental.get("value", 100)
            if current_mental < 100:
                recovery = 2
                new_val = min(100, current_mental + recovery)
                actual_recovery = new_val - current_mental
                bus.mental["value"] = new_val
                bus.mental["active"] = True
                bus.mental["last_delta"] = 0
                mask = context.get_acting_mask()
                bus.mental["log"] = f"{mask}: 🧠 기력 +{actual_recovery} → {new_val}/100 (자연 회복)"
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
            log_parts.append(f"{mask}: 🧠 기력 {actual_delta} → {target_mental}/100")
        else:
            log_parts.append(f"{mask}: 🧠 기력 +{actual_delta} → {target_mental}/100")
        
        # Modifiers with emphasis
        j_emotion = bus.mental.get("judgment_emotion", 0)
        if j_emotion > 0:
            log_parts.append(f" (판정 고양 +{j_emotion})")
        elif j_emotion < 0:
            log_parts.append(f" (판정 절망 {j_emotion})")
        rest_log = bus.mental.get("rest_log")
        if rest_log:
            log_parts.append(f"\n{rest_log}")
        if clamped:
            log_parts.append("\n❗ **충격 완화** (Clamping)")
        if trauma_triggered:
            log_parts.append("\n✨ **트라우마 각성** (Awakening)")
        
        bus.mental["log"] = "".join(log_parts)
            
        return context
