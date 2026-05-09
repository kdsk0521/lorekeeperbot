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


def _get_doom_stage(doom_val: int) -> int:
    """Doom value → stage index (0-5)."""
    if doom_val >= 95: return 5
    if doom_val >= 80: return 4
    if doom_val >= 60: return 3
    if doom_val >= 40: return 2
    if doom_val >= 20: return 1
    return 0


class DoomModule:
    def __init__(self):
        pass

    async def process(self, context: "GameContext") -> "GameContext":
        bus = context.shared_bus
        bus.doom["active"] = True  # 모듈 실행 = active (delta 유무 무관)
        current_doom = bus.doom.get("value", 0)
        delta = bus.doom.get("delta", 0)
        clocks = bus.doom.get("clocks", [])
        if not isinstance(clocks, list):
            clocks = []
        clock_events = []

        # ── 씬타입별 시계 라이프사이클 억제 ──────────────────
        scene_type = bus.dai.get("scene_type", "normal")
        clock_rules = config.CLOCK_SCENE_RULES.get(scene_type, config.CLOCK_SCENE_RULES.get("normal", {}))

        # ── 0. Storyteller가 fire 신호 보낸 pending 시계 처리 ──
        fired_names = set(bus.doom.get("_fire_completions", []))
        for clock in clocks:
            if not clock.get("pending_completion") or clock.get("resolved"):
                continue
            if clock.get("name") in fired_names:
                clock["resolved"] = True
                clock.pop("pending_completion", None)
                clock.pop("pending_turn", None)
                complete_doom = _get_clock_doom(clock)
                delta += complete_doom
                sign = f"+{complete_doom}" if complete_doom > 0 else str(complete_doom)
                clock_events.append(f"COMPLETE: {clock['name']} ({sign} doom)")
                if complete_doom > 0:
                    linked_quest = clock.get("linked_quest")
                    if linked_quest:
                        _fail_linked_quest(context, linked_quest, clock.get("name", "?"))

        # ── 1. Flash 시계 소비 ──────────────────────────────
        flash_new = bus.doom.pop("flash_clock_new", None)
        flash_updates = bus.doom.pop("flash_clock_updates", [])
        flash_resolved = bus.doom.pop("flash_clock_resolved", [])

        # 1a. 새 시계 생성 (씬타입 억제: create)
        if isinstance(flash_new, dict) and flash_new.get("name") and clock_rules.get("create", True):
            new_name = flash_new["name"]
            active_count = sum(1 for c in clocks if not c.get("resolved"))
            if active_count >= config.DOOM_CLOCK_CAP:
                clock_events.append(f"⚠️ Cap({config.DOOM_CLOCK_CAP}) — '{new_name}' blocked")
            else:
                new_clock = {
                    "name": new_name,
                    "segments": int(flash_new.get("segments", 6) or 6),
                    "filled": 0,
                    "tick_mode": str(flash_new.get("tick_mode", "action")).lower(),
                    "source": flash_new.get("source", "narrative"),
                    "threat": flash_new.get("threat", ""),
                    "linked_entity": flash_new.get("linked_entity"),
                    "defense_action": flash_new.get("defense_action", ""),
                    "doom_on_complete": flash_new.get("doom_on_complete"),
                    "linked_quest": None,
                    "tags": flash_new.get("tags", []),
                    "turn_created": bus.dai.get("turn_index", 0),
                    "last_progress_turn": bus.dai.get("turn_index", 0),
                    "last_context_turn": bus.dai.get("turn_index", 0),
                    "resolved": False,
                }
                # Fast-track: pre-fill at high doom
                if bus.doom.get("value", 0) >= config.DOOM_FAST_TRACK_THRESHOLD:
                    pre_fill = config.DOOM_FAST_TRACK_FILL.get(new_clock["segments"], 0)
                    if pre_fill > 0:
                        new_clock["filled"] = pre_fill
                        clock_events.append(f"⚡ Fast-track: {new_name} {pre_fill}/{new_clock['segments']}")
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
                        # Defense reward: vigor/composure recovery on clock resolution
                        resolve_reward = config.CLOCK_RESOLVE_REWARD.get(seg, 3)
                        bus.doom["resolve_reward"] = bus.doom.get("resolve_reward", 0) + resolve_reward
                        clock_events.append(f"RESOLVED: {resolved_name} ({resolve_doom} doom, +{resolve_reward} recovery)")
                        break

        # ── 2. Flash clock_updates (action/hybrid delta, 씬타입 억제: flash_tick) ──
        mitigation_count = 0
        flash_updated_names = set()
        if isinstance(flash_updates, list) and clock_rules.get("flash_tick", True):
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
                        flash_updated_names.add(name)
                        if new_filled != old_filled:
                            clock["last_progress_turn"] = bus.dai.get("turn_index", 0)
                            clock_events.append(
                                f"{name}: {old_filled}→{new_filled}/{clock['segments']}"
                            )
                            # Track mitigation (defense success)
                            if upd_delta < 0 and new_filled < old_filled:
                                mitigation_count += 1
                        break
        # Defense reward: vigor/composure recovery per mitigated clock
        if mitigation_count > 0:
            bus.doom["defense_reward"] = mitigation_count * config.CLOCK_MITIGATE_REWARD

        # ── 3. Time/Hybrid 자동 틱 (씬타입 억제: auto_tick) ──
        doom_stage = _get_doom_stage(bus.doom.get("value", 0))
        extra_tick = config.DOOM_CLOCK_ACCELERATION.get(doom_stage, 0)
        turn_idx = bus.dai.get("turn_index", 0)
        auto_tick_allowed = clock_rules.get("auto_tick", True)

        for clock in clocks:
            if clock.get("resolved"):
                continue
            tick_mode = str(clock.get("tick_mode", "action")).lower()
            clock_name = clock.get("name", "")

            should_auto_tick = False
            if not auto_tick_allowed:
                pass  # 씬타입 억제
            elif tick_mode == "time":
                should_auto_tick = True
            elif tick_mode == "hybrid" and clock_name not in flash_updated_names:
                should_auto_tick = True  # Flash가 안 건드린 hybrid만

            # Deceleration: stage 0에서 2턴에 1번
            if should_auto_tick and doom_stage <= config.DOOM_DECELERATION_STAGE:
                if turn_idx % 2 != 0:
                    should_auto_tick = False

            if should_auto_tick:
                segments = int(clock.get("segments", 4) or 4)
                old_filled = int(clock.get("filled", clock.get("progress", 0)) or 0)
                tick_amount = 1 + extra_tick
                new_filled = min(segments, old_filled + tick_amount)
                if new_filled != old_filled:
                    clock["filled"] = new_filled
                    clock["last_progress_turn"] = turn_idx
                    suffix = f" (accel +{extra_tick})" if extra_tick else ""
                    clock_events.append(
                        f"{clock_name}: {old_filled}→{new_filled}/{segments} (auto{suffix})"
                    )

        # ── 3.5. Staleness Fade ─────────────────────────────
        # 시계가 segments + CLOCK_STALE_BONUS_TURNS 동안 진행 0이면 silent resolve.
        # pending_completion / do_not_resolve_yet 시계는 제외 (이미 발사 대기 또는 명시적 보류).
        # Doom delta 0, 보상 없음 — "잊혀짐" 처리.
        _stale_current = bus.dai.get("turn_index", 0)
        for clock in clocks:
            if clock.get("resolved") or clock.get("pending_completion") or clock.get("do_not_resolve_yet"):
                continue
            segments = int(clock.get("segments", 6) or 6)
            stale_threshold = segments + config.CLOCK_STALE_BONUS_TURNS
            # backward compat: 패치 이전 시계는 last_progress_turn 없음 → turn_created 사용
            last_prog = int(clock.get("last_progress_turn", clock.get("turn_created", _stale_current)) or 0)
            stale_turns = _stale_current - last_prog
            if stale_turns >= stale_threshold:
                clock["resolved"] = True
                clock["fade_reason"] = f"stale_{stale_turns}turns"
                clock_events.append(
                    f"FADE: {clock.get('name', '?')} (stale {stale_turns}t, no doom)"
                )

        # ── 3.6. Context Drift Fade ─────────────────────────
        # linked_entity / tags가 현재 씬의 relevant_npcs / current_location과
        # N턴 동안 한 번도 매칭 안 되면 silent fade. 진행 중이지만 맥락 떠난 시계 정리.
        # 앵커 없는 시계(linked 빈 + tags 빈)는 drift 면제 — staleness만 처리.
        # in-context 매칭되면 last_context_turn 갱신.
        _drift_current = _stale_current
        _relevant_npcs = bus.dai.get("relevant_npcs", []) or []
        _npc_names = set()
        for _n in _relevant_npcs:
            if isinstance(_n, str):
                if _n.strip():
                    _npc_names.add(_n.strip().lower())
            elif isinstance(_n, dict):
                _nm = (_n.get("name") or "").strip()
                if _nm:
                    _npc_names.add(_nm.lower())
        _current_loc = (bus.dai.get("current_location") or "").strip().lower()

        for clock in clocks:
            if clock.get("resolved") or clock.get("pending_completion") or clock.get("do_not_resolve_yet"):
                continue
            _linked = (clock.get("linked_entity") or "").strip().lower()
            _tags_raw = clock.get("tags") or []
            _tags = [str(t).strip().lower() for t in _tags_raw if str(t).strip()]
            _has_anchor = bool(_linked) or bool(_tags)
            if not _has_anchor:
                # 앵커 없는 시계는 drift 면제 (staleness 경로로만 처리)
                continue

            in_context = False
            if _linked and _linked in _npc_names:
                in_context = True
            elif _tags and any(t in _npc_names for t in _tags):
                in_context = True
            elif _current_loc:
                if _linked and (_current_loc in _linked or _linked in _current_loc):
                    in_context = True
                elif _tags and any((_current_loc in t) or (t in _current_loc) for t in _tags):
                    in_context = True

            if in_context:
                clock["last_context_turn"] = _drift_current
                continue

            _last_ctx = int(clock.get("last_context_turn", clock.get("turn_created", _drift_current)) or 0)
            drift_turns = _drift_current - _last_ctx
            if drift_turns >= config.CLOCK_DRIFT_TURNS:
                clock["resolved"] = True
                clock["fade_reason"] = f"drift_{drift_turns}turns"
                clock_events.append(
                    f"DRIFT: {clock.get('name', '?')} (out of context {drift_turns}t)"
                )

        # ── 4. 완성 체크 → 극성별 분기 ──────────────────────
        completed_this_turn = []
        current_turn = bus.dai.get("turn_index", 0)
        for clock in clocks:
            if clock.get("resolved") or clock.get("pending_completion"):
                continue
            segments = int(clock.get("segments", 4) or 4)
            filled = int(clock.get("filled", clock.get("progress", 0)) or 0)
            if filled >= segments:
                complete_doom = _get_clock_doom(clock)
                if complete_doom >= 0:
                    # 위협/타이머 시계: pending → Storyteller 타이밍 위임
                    clock["pending_completion"] = True
                    clock["pending_turn"] = current_turn
                    clock_events.append(
                        f"⏸️ READY: {clock.get('name', '?')} ({filled}/{segments} — Storyteller 대기)"
                    )
                    _push_clock_to_storyteller(context, clock, current_turn)
                else:
                    # 기회/타이머 시계: 즉시 완성 (보상은 바로)
                    clock["resolved"] = True
                    delta += complete_doom
                    completed_this_turn.append(clock)
                    sign = f"+{complete_doom}" if complete_doom > 0 else str(complete_doom)
                    clock_events.append(f"COMPLETE: {clock.get('name', '?')} ({sign} doom)")
                    if complete_doom < 0:
                        seg = int(clock.get("segments", 6) or 6)
                        resolve_reward = config.CLOCK_RESOLVE_REWARD.get(seg, 3)
                        bus.doom["resolve_reward"] = bus.doom.get("resolve_reward", 0) + resolve_reward

        # ── 4b. Status severity → doom_impact ─────────────────
        from game_character import normalize_status_effects
        raw_effects = (context.narrative_anchors or {}).get("status_effects", [])
        status_effects = normalize_status_effects(raw_effects)
        for eff in status_effects:
            sev = eff.get("severity", 0)
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
            if delta > 0:
                bus.doom["log"] = f"📈 긴장도 증가 (+{delta})"
            else:
                bus.doom["log"] = f"📉 긴장도 감소 ({delta})"
                bus.doom["narrative_space"] = abs(delta)

        # ── 7. Stage 5 클라이맥스 체크 (doom ≥ threshold) ────
        if bus.doom.get("value", 0) >= config.DOOM_CLIMAX_THRESHOLD:
            _trigger_climax(context, bus, clocks, clock_events)

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
            # 퍼지 매칭: 시계 이름이 퀘스트에 포함 (단방향 — 퀘스트가 상위 개념)
            if clock_name in quest_name:
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


def _get_clock_doom(clock: dict) -> int:
    """시계의 doom_on_complete 값을 반환 (극성 반영)."""
    custom_doom = clock.get("doom_on_complete")
    if custom_doom is not None:
        return int(custom_doom)
    segments = int(clock.get("segments", 6) or 6)
    return config.CLOCK_COMPLETE_DOOM.get(segments, 15)


def _push_clock_to_storyteller(context: "GameContext", clock: dict, current_turn: int) -> None:
    """시계 완성 이벤트를 Storyteller event_queue에 푸시."""
    channel_id = (context.narrative_anchors or {}).get("channel_id", "")
    if not channel_id:
        return
    import domain_manager
    st_state = domain_manager.get_storyteller_state(channel_id)
    queue = st_state.get("event_queue", [])
    clock_name = clock.get("name", "")
    # 중복 방지
    if any(e.get("type") == "clock_completion" and e.get("clock_name") == clock_name for e in queue):
        return
    doom_val = _get_clock_doom(clock)
    queue.append({
        "type": "clock_completion",
        "tag": clock_name,
        "clock_name": clock_name,
        "category": "clock",
        "intensity": "High",
        "polarity": "negative" if doom_val > 0 else ("positive" if doom_val < 0 else "mixed"),
        "queued_turn": current_turn,
        "non_intimate_turns": 0,
    })
    st_state["event_queue"] = queue
    domain_manager.update_storyteller_state(channel_id, st_state)
    logger.info("[Doom] Clock '%s' → Storyteller queue (pending completion)", clock_name)


def _trigger_climax(context, bus, clocks: list, clock_events: list) -> None:
    """Stage 5: 모든 미해결 시계 즉시 완성 + 클라이맥스 이벤트를 스토리텔러 큐에 push.

    do_not_resolve_yet 플래그가 True인 clock은 강제 완성에서 제외 (체호프의 미발사된 총).
    인간 기억 모델 — 모든 약속이 발사되지 않음. 명시적 보류는 unresolved 유지.
    """
    for clock in clocks:
        if clock.get("resolved"):
            continue
        if clock.get("do_not_resolve_yet"):
            clock_events.append(f"CLIMAX HOLD: {clock.get('name', '?')} 발사 보류 (do_not_resolve_yet)")
            continue
        clock["filled"] = clock.get("segments", 4)
        clock["resolved"] = True
        clock_events.append(f"CLIMAX: {clock.get('name', '?')} forced complete")
    bus.doom["climax_triggered"] = True

    # Doom runs after Storyteller in pipeline → push climax event to next-turn queue
    channel_id = (context.narrative_anchors or {}).get("channel_id", "")
    if channel_id:
        import domain_manager
        st_state = domain_manager.get_storyteller_state(channel_id)
        queue = st_state.get("event_queue", [])
        ws = domain_manager.get_world_state(channel_id)
        current_turn = ws.get("turn_index", 0)
        queue.insert(0, {
            "tag": "클라이맥스",
            "category": "supernatural",
            "intensity": "Extreme",
            "polarity": "negative",
            "line": "모든 시계가 완성된다 — 세계가 임계점에 도달했다.",
            "reason": "doom_climax",
            "queued_turn": current_turn,
        })
        st_state["event_queue"] = queue
        domain_manager.update_storyteller_state(channel_id, st_state)

    logger.info("[Doom] CLIMAX TRIGGERED — all clocks forced, event queued for next turn")


def _apply_pressure(context: "GameContext", bus) -> None:
    """Vigor/Composure Pressure/Recovery from global doom level (FitD 8-segment)."""
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

    mechanic = context.request.genres.get("mechanic", {})
    primary = mechanic.get("primary_resource") or "vigor"
    secondary = "composure" if primary == "vigor" else "vigor"
    primary_bus = getattr(bus, primary)
    secondary_bus = getattr(bus, secondary)

    if pressure != 0:
        primary_bus["delta"] = primary_bus.get("delta", 0) + pressure
        secondary_bus["delta"] = secondary_bus.get("delta", 0) + int(pressure * 0.5)
        bus.doom["mental_pressure_log"] = label

    # Defense rewards → primary axis recovery
    defense_reward = bus.doom.get("defense_reward", 0)
    resolve_reward = bus.doom.get("resolve_reward", 0)
    total_reward = defense_reward + resolve_reward
    if total_reward > 0:
        primary_bus["delta"] = primary_bus.get("delta", 0) + total_reward
        reward_parts = []
        if defense_reward > 0:
            reward_parts.append(f"완화 +{defense_reward}")
        if resolve_reward > 0:
            reward_parts.append(f"해소 +{resolve_reward}")
        bus.doom["defense_log"] = f"🛡️ {' | '.join(reward_parts)}"
