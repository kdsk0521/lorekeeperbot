"""
Lorekeeper UNE - Vigor/Composure Module (v3.0)
Manages 2-axis PC state: Vigor (physical+will) and Composure (mental+social).
Replaces mental_module.py.
"""

from typing import TYPE_CHECKING
import config

if TYPE_CHECKING:
    from orchestration_context import GameContext


def _get_stage(val: int) -> int:
    if val >= 70: return 0
    if val >= 40: return 1
    if val >= 15: return 2
    return 3


def _get_primary_axis(context: "GameContext") -> str:
    mechanic = context.request.genres.get("mechanic", {})
    return mechanic.get("primary_resource") or "vigor"


class VigorComposureModule:
    def __init__(self):
        pass

    async def prime(self, context: "GameContext") -> "GameContext":
        """Pre-pass for pipeline order: annotate current stage without consuming deltas."""
        bus = context.shared_bus
        bus.vigor["stage"] = _get_stage(int(bus.vigor.get("value", 100)))
        bus.composure["stage"] = _get_stage(int(bus.composure.get("value", 100)))
        return context

    async def process(self, context: "GameContext") -> "GameContext":
        bus = context.shared_bus
        primary_axis = _get_primary_axis(context)

        # Process each axis
        self._process_axis(context, bus.vigor, "vigor", primary_axis)
        self._process_axis(context, bus.composure, "composure", primary_axis)

        # Combine logs
        mask = context.get_acting_mask()
        v_val = bus.vigor["value"]
        c_val = bus.composure["value"]
        v_delta = bus.vigor.get("_final_delta", 0)
        c_delta = bus.composure.get("_final_delta", 0)

        log_parts = []
        if v_delta != 0 or c_delta != 0:
            v_sign = f"+{v_delta}" if v_delta > 0 else str(v_delta)
            c_sign = f"+{c_delta}" if c_delta > 0 else str(c_delta)
            log_parts.append(f"{mask}: 💪 기력 {v_sign} → {v_val}/100 | 😌 평정 {c_sign} → {c_val}/100")
        elif bus.vigor.get("active") or bus.composure.get("active"):
            log_parts.append(f"{mask}: 💪 기력 {v_val}/100 | 😌 평정 {c_val}/100 (자연 회복)")

        # Judgment emotion
        v_emo = bus.vigor.get("judgment_emotion", 0)
        c_emo = bus.composure.get("judgment_emotion", 0)
        if v_emo > 0:
            log_parts.append(f" (판정 고양 +{v_emo})")
        elif v_emo < 0:
            log_parts.append(f" (판정 절망 {v_emo})")
        if c_emo and c_emo != v_emo:
            log_parts.append(f" (평정 판정 {'+' if c_emo > 0 else ''}{c_emo})")

        # Rest log
        rest_log = bus.vigor.get("rest_log")
        if rest_log:
            log_parts.append(f"\n{rest_log}")

        # Clamping/Trauma
        if bus.vigor.get("_clamped"):
            log_parts.append("\n❗ **충격 완화** (기력 Clamping)")
        if bus.composure.get("_clamped"):
            log_parts.append("\n❗ **충격 완화** (평정 Clamping)")
        if bus.vigor.get("trauma_trigger"):
            log_parts.append("\n✨ **트라우마 각성** (기력 Awakening)")
        if bus.composure.get("trauma_trigger"):
            log_parts.append("\n✨ **트라우마 각성** (평정 Awakening)")

        combined_log = "".join(log_parts)
        bus.vigor["log"] = combined_log
        bus.composure["log"] = combined_log  # Same log for both

        # Cleanup temp keys
        for axis in (bus.vigor, bus.composure):
            axis.pop("_final_delta", None)
            axis.pop("_clamped", None)
            axis.pop("trauma_trigger", None)

        return context

    def _process_axis(self, context: "GameContext", axis: dict, axis_name: str, primary_axis: str):
        """Process a single axis (vigor or composure)."""
        bus = context.shared_bus

        # 1. Collect Delta
        delta = axis.get("delta", 0)

        # 1a. Rest Recovery (vigor만 적용)
        if axis_name == "vigor":
            rest_eval = bus.dai.get("rest_eval")
            if rest_eval and rest_eval.get("detected"):
                quality = rest_eval.get("quality", "brief")
                base_recovery = config.REST_RECOVERY.get(quality, 10)
                if not rest_eval.get("safe_location", True):
                    base_recovery = int(base_recovery * config.REST_UNSAFE_MODIFIER)
                delta += base_recovery
                safe_tag = "안전" if rest_eval.get("safe_location", True) else "위험"
                axis["rest_log"] = f"💤 휴식({quality}) +{base_recovery} ({safe_tag})"

        # 1b. Judgment Emotional Impact (primary axis만 적용)
        if axis_name == primary_axis and bus.judgment.get("active"):
            j_result = bus.judgment.get("result", "")
            j_emotion = {
                "critical_success": 3,
                "success": 1,
                "partial": 0,
                "failure": -2,
                "critical_failure": -4,
            }.get(j_result, 0)
            if j_emotion != 0:
                delta += j_emotion
                axis["judgment_emotion"] = j_emotion

        # 2. AI-Analyzed Impact
        impact_data = axis.get("impact", {})
        if impact_data.get("applicable", False):
            impact_delta = impact_data.get("delta", 0)
            delta += impact_delta

        # 2b. Passive Drain Modifiers (theory tag system)
        if delta < 0:
            drain_key = f"{axis_name}_drain"
            passives = (context.narrative_anchors or {}).get("passives", [])
            drain_mult = 1.0
            for passive in passives:
                mods = config.get_passive_modifiers(passive)
                if drain_key in mods:
                    drain_mult *= mods[drain_key]
            drain_mult = max(0.5, min(1.5, drain_mult))  # 극단값 방지
            if drain_mult != 1.0:
                delta = int(delta * drain_mult)

        # 2a. Natural Recovery (평온한 턴: 외부 자극 없음)
        if delta == 0:
            current_val = axis.get("value", 100)
            if current_val < 100:
                recovery = 2
                new_val = min(100, current_val + recovery)
                axis["value"] = new_val
                axis["active"] = True
                axis["last_delta"] = 0
                axis["_final_delta"] = 0
            return

        # 3. Inertia (Successive changes amplification)
        last_delta = axis.get("last_delta", 0)
        actual_delta = delta
        if (delta > 0 and last_delta > 0) or (delta < 0 and last_delta < 0):
            actual_delta = int(delta * 1.1)

        # 4. Clamping (Max 2 stage drop per turn)
        current_val = axis.get("value", 100)
        current_stage = _get_stage(current_val)

        base_target = max(0, min(100, current_val + delta))
        base_stage = _get_stage(base_target)
        clamp_floor = base_target

        if base_stage > current_stage + 2:
            limit_stage = current_stage + 2
            floors = {0: 70, 1: 40, 2: 15, 3: 0}
            clamp_floor = floors.get(limit_stage, 0)

        target_val = max(0, min(100, current_val + actual_delta))
        clamped = False
        if actual_delta < 0:
            if target_val < clamp_floor:
                target_val = clamp_floor
                clamped = True

        # 5. Trauma Awakening (Collapse -> Recovery)
        trauma_triggered = False
        if current_stage == 3 and actual_delta > 0:
            target_val = 90
            trauma_triggered = True
            axis["trauma_trigger"] = True

        # 6. Update
        axis["value"] = target_val
        axis["active"] = True
        axis["last_delta"] = delta
        axis["_final_delta"] = actual_delta
        axis["_clamped"] = clamped
