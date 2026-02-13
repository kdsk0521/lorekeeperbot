"""
Lorekeeper UNE - Doom Module
Manages world tension updates and Doom-side pressures.
"""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from orchestration_context import GameContext

class DoomModule:
    def __init__(self):
        pass

    @staticmethod
    def _clock_complete_delta(segments: int) -> int:
        """완성 시 글로벌 둠 변화량(단순 매핑)."""
        if segments <= 4:
            return 10
        if segments <= 6:
            return 15
        return 20

    async def process(self, context: "GameContext") -> "GameContext":
        bus = context.shared_bus
        current_doom = bus.doom.get("value", 0)
        
        # 1. Consume pre-existing doom delta
        delta = bus.doom.get("delta", 0)

        # 2. AI-Analyzed Doom Relief
        relief_data = bus.doom.get("relief", {})
        if relief_data.get("applicable", False):
            relief_amount = relief_data.get("amount", 0)
            relief_reason = relief_data.get("reason", "")
            delta -= relief_amount  # Reduce Doom
            bus.doom["relief_log"] = f"🌿 긴장 완화: -{relief_amount} ({relief_reason})"

        # 2a. Local doom clocks (time/hybrid auto-tick)
        clocks = bus.doom.get("clocks", [])
        clock_events = []
        if isinstance(clocks, list):
            for clock in clocks:
                if not isinstance(clock, dict):
                    continue
                if clock.get("resolved"):
                    continue
                segments = int(clock.get("segments", 4) or 4)
                progress = int(clock.get("progress", 0) or 0)
                tick_mode = str(clock.get("tick_mode", "action")).lower()

                if tick_mode in ("time", "hybrid"):
                    new_progress = min(segments, progress + 1)
                    if new_progress != progress:
                        clock["progress"] = new_progress
                        name = clock.get("name", "Unnamed Clock")
                        clock_events.append(f"{name}: {progress}->{new_progress}/{segments}")

                if clock.get("progress", 0) >= segments:
                    clock["resolved"] = True
                    name = clock.get("name", "Unnamed Clock")
                    delta += self._clock_complete_delta(segments)
                    clock_events.append(f"{name}: COMPLETE (+doom)")

            bus.doom["clocks"] = clocks
            if clock_events:
                bus.doom["clock_log"] = " | ".join(clock_events)
            
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
                
        # 4. Vigor/Composure Pressure/Recovery from 8-Segment Doom Clock (FitD)
        if "mental" in context.request.active_modules:
            dv = bus.doom["value"]
            if dv >= 88:
                pressure, label = -3, "⚠️ 긴장 시계 [임박] (-3)"
            elif dv >= 76:
                pressure, label = -2, "⚠️ 긴장 시계 [위기] (-2)"
            elif dv >= 63:
                pressure, label = -1, "😰 긴장 시계 [위협] (-1)"
            elif dv >= 50:
                pressure, label = -1, "😰 긴장 시계 [긴장] (-1)"
            elif dv >= 38:
                pressure, label = 0, ""
            elif dv >= 25:
                pressure, label = 0, ""
            elif dv >= 13:
                pressure, label = 1, "😌 긴장 이완 [안정] (+1)"
            else:
                pressure, label = 2, "😌 긴장 이완 [이완] (+2)"

            if pressure != 0:
                # Primary axis: 100%, Secondary axis: 50%
                mechanic = context.request.genres.get("mechanic", {})
                primary = mechanic.get("primary_resource") or "vigor"
                secondary = "composure" if primary == "vigor" else "vigor"
                primary_bus = getattr(bus, primary)
                secondary_bus = getattr(bus, secondary)
                primary_bus["delta"] = primary_bus.get("delta", 0) + pressure
                secondary_bus["delta"] = secondary_bus.get("delta", 0) + int(pressure * 0.5)
                bus.doom["mental_pressure_log"] = label

        return context
