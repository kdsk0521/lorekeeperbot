"""
Lorekeeper UNE - Doom Module (v3 — Dual-Layer Situation Clocks)
Layer 1: Individual situation clocks (Flash creates/updates/resolves)
Layer 2: Global doom gauge (0-100 climax meter)
Backward compatible: empty doom_clocks → same behavior as v2.
"""

import logging
from typing import TYPE_CHECKING

import config

if TYPE_CHECKING:
    from orchestration_context import GameContext

logger = logging.getLogger("Doom")


class DoomModule:
    def __init__(self):
        pass

    async def process(self, context: "GameContext") -> "GameContext":
        bus = context.shared_bus
        current_doom = bus.doom.get("value", 0)
        delta = bus.doom.get("delta", 0)
        clocks = bus.doom.get("clocks", [])
        if not isinstance(clocks, list):
            clocks = []
        clock_events = []

        # ── 1. Flash 시계 소비 ──────────────────────────────
        flash_new = bus.doom.pop("flash_clock_new", None)
        flash_updates = bus.doom.pop("flash_clock_updates", [])
        flash_resolved = bus.doom.pop("flash_clock_resolved", [])

        # 1a. 새 시계 생성
        if isinstance(flash_new, dict) and flash_new.get("name"):
            new_clock = {
                "name": flash_new["name"],
                "segments": int(flash_new.get("segments", 6) or 6),
                "filled": 0,
                "tick_mode": str(flash_new.get("tick_mode", "action")).lower(),
                "source": flash_new.get("source", "narrative"),
                "threat": flash_new.get("threat", ""),
                "linked_entity": flash_new.get("linked_entity"),
                "linked_quest": None,
                "tags": flash_new.get("tags", []),
                "turn_created": bus.dai.get("turn_index", 0),
                "resolved": False,
            }
            # 중복 방지 (같은 이름 미해결 시계)
            existing_names = {c.get("name") for c in clocks if not c.get("resolved")}
            if new_clock["name"] not in existing_names:
                clocks.append(new_clock)
                clock_events.append(
                    f"NEW: {new_clock['name']} ({new_clock['segments']}seg, {new_clock['tick_mode']})"
                )
                # 퀘스트 자동 연결 시도
                _auto_link_quest_clock(context, new_clock)

        # 1b. 서사적 해결
        if isinstance(flash_resolved, list):
            for resolved_name in flash_resolved:
                if not isinstance(resolved_name, str):
                    continue
                for clock in clocks:
                    if clock.get("name") == resolved_name and not clock.get("resolved"):
                        clock["resolved"] = True
                        seg = int(clock.get("segments", 6) or 6)
                        resolve_doom = config.CLOCK_RESOLVE_DOOM.get(seg, -10)
                        delta += resolve_doom
                        clock_events.append(f"RESOLVED: {resolved_name} ({resolve_doom} doom)")
                        break

        # ── 2. Flash clock_updates (action/hybrid delta) ────
        if isinstance(flash_updates, list):
            for update in flash_updates:
                if not isinstance(update, dict):
                    continue
                name = update.get("name", "")
                upd_delta = int(update.get("delta", 0) or 0)
                if not name or upd_delta == 0:
                    continue
                for clock in clocks:
                    if clock.get("name") == name and not clock.get("resolved"):
                        old_filled = int(clock.get("filled", clock.get("progress", 0)) or 0)
                        new_filled = max(0, min(clock["segments"], old_filled + upd_delta))
                        clock["filled"] = new_filled
                        if new_filled != old_filled:
                            clock_events.append(
                                f"{name}: {old_filled}→{new_filled}/{clock['segments']}"
                            )
                        break

        # ── 3. Time/Hybrid 자동 틱 ──────────────────────────
        for clock in clocks:
            if clock.get("resolved"):
                continue
            tick_mode = str(clock.get("tick_mode", "action")).lower()
            if tick_mode in ("time", "hybrid"):
                segments = int(clock.get("segments", 4) or 4)
                old_filled = int(clock.get("filled", clock.get("progress", 0)) or 0)
                new_filled = min(segments, old_filled + 1)
                if new_filled != old_filled:
                    clock["filled"] = new_filled
                    clock_events.append(
                        f"{clock.get('name', '?')}: {old_filled}→{new_filled}/{segments} (auto)"
                    )

        # ── 4. 완성 체크 → 글로벌 둠 상승 ───────────────────
        completed_this_turn = []
        for clock in clocks:
            if clock.get("resolved"):
                continue
            segments = int(clock.get("segments", 4) or 4)
            filled = int(clock.get("filled", clock.get("progress", 0)) or 0)
            if filled >= segments:
                clock["resolved"] = True
                complete_doom = config.CLOCK_COMPLETE_DOOM.get(segments, 15)
                delta += complete_doom
                completed_this_turn.append(clock)
                clock_events.append(f"COMPLETE: {clock.get('name', '?')} (+{complete_doom} doom)")
                # 연결된 퀘스트 자동 실패
                linked_quest = clock.get("linked_quest")
                if linked_quest:
                    _fail_linked_quest(context, linked_quest, clock.get("name", "?"))

        # ── 4b. Status severity → doom_impact ─────────────────
        status_effects = (context.narrative_anchors or {}).get("status_effects", [])
        if isinstance(status_effects, list):
            for eff in status_effects:
                if not isinstance(eff, dict):
                    continue
                sev = int(eff.get("severity", 0) or 0)
                sev_cfg = config.SEVERITY_EFFECTS.get(sev, {})
                doom_impact = sev_cfg.get("doom_impact", 0)
                if doom_impact > 0:
                    delta += doom_impact

        # ── 5. Doom Relief ───────────────────────────────────
        relief_data = bus.doom.get("relief", {})
        if relief_data.get("applicable", False):
            relief_amount = int(relief_data.get("amount", 0) or 0)
            relief_reason = relief_data.get("reason", "")
            delta -= relief_amount
            bus.doom["relief_log"] = f"🌿 긴장 완화: -{relief_amount} ({relief_reason})"

        # ── 6. 글로벌 둠 갱신 ────────────────────────────────
        bus.doom["delta"] = 0  # Consumed — Anomaly/Judgment can write fresh delta after this
        if delta != 0:
            new_doom = max(0, min(100, current_doom + delta))
            bus.doom["value"] = new_doom
            bus.doom["active"] = True
            if delta > 0:
                bus.doom["log"] = f"📈 긴장도 증가 (+{delta})"
            else:
                bus.doom["log"] = f"📉 긴장도 감소 ({delta})"

        # ── 7. Stage 5 클라이맥스 체크 (doom ≥ threshold) ────
        if bus.doom.get("value", 0) >= config.DOOM_CLIMAX_THRESHOLD:
            _trigger_climax(bus, clocks, clock_events)

        # ── 8. 시계 저장 ─────────────────────────────────────
        bus.doom["clocks"] = clocks
        bus.doom["completed_this_turn"] = completed_this_turn
        if clock_events:
            bus.doom["clock_log"] = " | ".join(clock_events)

        # ── 9. Vigor/Composure Pressure (FitD 8-segment) ────
        _apply_pressure(context, bus)

        # ── 10. Summary log ──────────────────────────────────
        final_doom = bus.doom.get("value", 0)
        parts = [f"[Doom] {current_doom}→{final_doom}"]
        if delta != 0:
            parts.append(f"delta={'+' + str(delta) if delta > 0 else str(delta)}")
        if relief_data.get("applicable"):
            parts.append(f"relief=-{relief_data.get('amount', 0)}")
        if clock_events:
            parts.append(f"clocks: {', '.join(clock_events)}")
        pressure_log = bus.doom.get("mental_pressure_log", "")
        if pressure_log:
            parts.append(f"pressure→{pressure_log}")
        logger.info(" | ".join(parts))

        return context


# ── Helper Functions ─────────────────────────────────────────

def _auto_link_quest_clock(context: "GameContext", clock: dict) -> None:
    """시계 생성 시 기존 퀘스트와 이름 퍼지 매칭하여 양방향 연결."""
    try:
        import game_character
        channel_id = (context.narrative_anchors or {}).get("channel_id", "")
        if not channel_id:
            return
        board = game_character._get_board(channel_id)
        active_quests = board.get("active", [])
        clock_name = clock.get("name", "").strip().lower()
        for quest in active_quests:
            if not isinstance(quest, dict):
                continue
            quest_name = quest.get("content", "").strip().lower()
            # 퍼지 매칭: 시계 이름이 퀘스트에 포함되거나 그 반대
            if clock_name in quest_name or quest_name in clock_name:
                clock["linked_quest"] = quest.get("content", "")
                quest["linked_clock"] = clock.get("name", "")
                game_character._save_board(channel_id, board)
                logger.info("[Doom] Auto-linked clock '%s' ↔ quest '%s'",
                            clock.get("name"), quest.get("content"))
                return
    except Exception as e:
        logger.warning("[Doom] Auto-link quest-clock failed: %s", e)


def _fail_linked_quest(context: "GameContext", quest_name: str, clock_name: str) -> None:
    """시계 완성 → 연결 퀘스트 자동 실패."""
    try:
        import game_character
        channel_id = (context.narrative_anchors or {}).get("channel_id", "")
        if not channel_id:
            return
        result = game_character.remove_quest(channel_id, quest_name)
        context.shared_bus.doom.setdefault("quest_failed", []).append(
            {"quest": quest_name, "reason": f"시계 '{clock_name}' 완성"}
        )
        logger.info("[Doom] Quest failed: %s (clock '%s' completed) → %s",
                     quest_name, clock_name, result)
    except Exception as e:
        logger.warning("[Doom] Failed to remove linked quest: %s", e)


def _trigger_climax(bus, clocks: list, clock_events: list) -> None:
    """Stage 5: 모든 미해결 시계 즉시 완성 + 이변 강제 발동."""
    for clock in clocks:
        if not clock.get("resolved"):
            clock["filled"] = clock.get("segments", 4)
            clock["resolved"] = True
            clock_events.append(f"CLIMAX: {clock.get('name', '?')} forced complete")
    bus.anomaly["skip_trigger"] = True
    bus.anomaly["potential"] = True
    bus.doom["climax_triggered"] = True
    logger.info("[Doom] CLIMAX TRIGGERED — all clocks forced complete, anomaly forced")


def _apply_pressure(context: "GameContext", bus) -> None:
    """Vigor/Composure Pressure/Recovery from global doom level (FitD 8-segment)."""
    if "mental" not in context.request.active_modules:
        return

    dv = bus.doom.get("value", 0)
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
        mechanic = context.request.genres.get("mechanic", {})
        primary = mechanic.get("primary_resource") or "vigor"
        secondary = "composure" if primary == "vigor" else "vigor"
        primary_bus = getattr(bus, primary)
        secondary_bus = getattr(bus, secondary)
        primary_bus["delta"] = primary_bus.get("delta", 0) + pressure
        secondary_bus["delta"] = secondary_bus.get("delta", 0) + int(pressure * 0.5)
        bus.doom["mental_pressure_log"] = label
