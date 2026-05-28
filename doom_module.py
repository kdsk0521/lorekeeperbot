"""
Lorekeeper UNE - Doom Module (v4 — Chapter Volume Gauge)
Doom = 이야기 활성도 + 챕터 볼륨. 起承轉結 + 間 4+1단.
Phase × Lens × Scene 결합. intense 자동 climax / intimate spike 발화.
"""

import logging
from typing import TYPE_CHECKING, List, Set, Tuple

import config

if TYPE_CHECKING:
    from orchestration_context import GameContext

logger = logging.getLogger("Doom")


def _get_doom_stage(doom_val: int) -> int:
    """Doom value → 6단 stage index (0-5). DOOM_CLOCK_ACCELERATION/DECELERATION_STAGE 룩업용 legacy."""
    if doom_val >= 95: return 5
    if doom_val >= 80: return 4
    if doom_val >= 60: return 3
    if doom_val >= 40: return 2
    if doom_val >= 20: return 1
    return 0


# =========================================================
# Phase / Lens / Scene 결합 헬퍼
# =========================================================

def _extract_lens_tags(active_genres) -> List[str]:
    """active_genres에서 C-Lens 태그(noir/comedy/romance/drama)만 추출.
    여러 형식 정규화: str / list / dict({stage,flavor,lens} 또는 {layers}).
    """
    KNOWN_LENS = {"noir", "comedy", "romance", "drama"}
    tags = set()
    if isinstance(active_genres, str):
        if active_genres in KNOWN_LENS:
            return [active_genres]
        return []
    if isinstance(active_genres, list):
        for g in active_genres:
            if isinstance(g, str) and g in KNOWN_LENS:
                tags.add(g)
        return list(tags)
    if isinstance(active_genres, dict):
        # layers 형식
        layers = active_genres.get("layers", {})
        if isinstance(layers, dict):
            for v in layers.get("narrative_tone", []):
                if isinstance(v, str) and v in KNOWN_LENS:
                    tags.add(v)
        # stage/flavor/lens 형식
        for v in active_genres.get("lens", []):
            if isinstance(v, str) and v in KNOWN_LENS:
                tags.add(v)
        if tags:
            return list(tags)
        # fallback: 모든 값 순회
        for v in active_genres.values():
            if isinstance(v, list):
                for item in v:
                    if isinstance(item, str) and item in KNOWN_LENS:
                        tags.add(item)
            elif isinstance(v, str) and v in KNOWN_LENS:
                tags.add(v)
    return list(tags)


def _extract_flavor_tags(active_genres) -> List[str]:
    """active_genres에서 B-Flavor 태그(urban_fantasy/steampunk/cosmic_horror/game_system) 추출."""
    KNOWN_FLAVOR = set(config.FLAVOR_DOOM_MODIFIER.keys())
    tags = set()
    if isinstance(active_genres, str):
        if active_genres in KNOWN_FLAVOR:
            return [active_genres]
        return []
    if isinstance(active_genres, list):
        for g in active_genres:
            if isinstance(g, str) and g in KNOWN_FLAVOR:
                tags.add(g)
        return list(tags)
    if isinstance(active_genres, dict):
        layers = active_genres.get("layers", {})
        if isinstance(layers, dict):
            for v in layers.get("style_tech", []):
                if isinstance(v, str) and v in KNOWN_FLAVOR:
                    tags.add(v)
        for v in active_genres.get("flavor", []):
            if isinstance(v, str) and v in KNOWN_FLAVOR:
                tags.add(v)
        if tags:
            return list(tags)
        for v in active_genres.values():
            if isinstance(v, list):
                for item in v:
                    if isinstance(item, str) and item in KNOWN_FLAVOR:
                        tags.add(item)
            elif isinstance(v, str) and v in KNOWN_FLAVOR:
                tags.add(v)
    return list(tags)


def _get_doom_phase(doom_val: int, lens_tags: List[str]) -> str:
    """Doom value + 활성 lens → 페이즈 라벨(起/承/轉/結/間).
    다중 lens면 첫 번째 lens의 boundary 기준 (단순화 — 곡선만 평균, 페이즈는 단일 기준).
    """
    primary = lens_tags[0] if lens_tags else "default"
    return config.get_lens_phase(doom_val, primary)


def _compute_phase_multiplier(phase: str, lens_tags: List[str]) -> float:
    """페이즈 + 활성 lens → multiplier. 다중 lens는 가중평균. 間 페이즈는 별도 처리(자연 감쇠)."""
    if phase == "間":
        return 0.0  # 間은 gain 적용 안 함, 별도 자연 감쇠 처리
    if not lens_tags:
        return config.LENS_DOOM_CURVE["default"].get(phase, 1.0)
    mults = [config.LENS_DOOM_CURVE.get(lens, config.LENS_DOOM_CURVE["default"]).get(phase, 1.0)
             for lens in lens_tags]
    return sum(mults) / len(mults)


def _compute_flavor_modifier(flavor_tags: List[str]) -> Tuple[float, int]:
    """B-Flavor → (gain_mult 곱, threshold_offset 합)."""
    gain = 1.0
    offset = 0
    for f in flavor_tags:
        mod = config.FLAVOR_DOOM_MODIFIER.get(f)
        if mod:
            gain *= mod.get("gain_mult", 1.0)
            offset += mod.get("threshold_offset", 0)
    return gain, offset


def _compute_climax_threshold(lens_tags: List[str], flavor_tags: List[str]) -> int:
    """렌즈 climax_threshold 가중평균 + flavor offset."""
    if not lens_tags:
        base = config.LENS_DOOM_CURVE["default"]["climax"]
    else:
        thresholds = [config.LENS_DOOM_CURVE.get(l, config.LENS_DOOM_CURVE["default"]).get("climax", 80)
                      for l in lens_tags]
        base = sum(thresholds) / len(thresholds)
    _, offset = _compute_flavor_modifier(flavor_tags)
    return int(base + offset)


def _compute_scene_modifier(scene_type: str) -> float:
    """씬 타입 modifier 룩업."""
    return config.SCENE_DOOM_MODIFIER.get(scene_type, 1.0)


def _apply_doom_multipliers(raw_delta: int, phase: str, lens_tags: List[str],
                             flavor_tags: List[str], scene_type: str) -> int:
    """raw doom delta에 phase × lens_curve × flavor × scene 모두 적용."""
    if raw_delta == 0:
        return 0
    phase_mult = _compute_phase_multiplier(phase, lens_tags)
    flavor_mult, _ = _compute_flavor_modifier(flavor_tags)
    scene_mult = _compute_scene_modifier(scene_type)
    final = raw_delta * phase_mult * flavor_mult * scene_mult
    # 부호 보존 + 정수화
    if raw_delta > 0:
        return max(0, int(round(final)))
    else:
        return min(0, int(round(final)))


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

        # ── 페이즈/렌즈/플레이버 추출 (이번 턴 결합 multiplier 결정) ──
        active_genres = (context.narrative_anchors or {}).get("active_genres", [])
        lens_tags = _extract_lens_tags(active_genres)
        flavor_tags = _extract_flavor_tags(active_genres)
        current_phase = _get_doom_phase(current_doom, lens_tags)
        climax_threshold = _compute_climax_threshold(lens_tags, flavor_tags)
        bus.doom["chapter_phase"] = current_phase
        bus.doom["lens_tags"] = lens_tags
        bus.doom["flavor_tags"] = flavor_tags
        bus.doom["climax_threshold"] = climax_threshold

        # 間 페이즈 진입 여부 — climax_triggered가 직전 턴에 set됐고 doom 자연 감쇠 중
        in_intermission = bool(bus.doom.get("intermission_active"))

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
                    "filled_history": [0],  # 등락 감지용 — 매 턴 filled 변화 시 append
                    "resolved": False,
                }
                # 間 페이즈: 새 시계는 자동 do_not_resolve_yet (다음 챕터 떡밥)
                if in_intermission:
                    new_clock["do_not_resolve_yet"] = True
                    clock_events.append(f"📖 {new_name} — 다음 챕터 떡밥 (do_not_resolve_yet)")
                # Fast-track: pre-fill at high doom (間 페이즈에선 정지)
                if not in_intermission and bus.doom.get("value", 0) >= config.DOOM_FAST_TRACK_THRESHOLD:
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
                            # 등락 감지용 history 갱신 (window 크기로 잘림)
                            hist = clock.setdefault("filled_history", [old_filled])
                            hist.append(new_filled)
                            if len(hist) > config.CLOCK_OSCILLATION_WINDOW:
                                clock["filled_history"] = hist[-config.CLOCK_OSCILLATION_WINDOW:]
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
        # 間 페이즈: acceleration 정지 (extra_tick=0)
        extra_tick = 0 if in_intermission else config.DOOM_CLOCK_ACCELERATION.get(doom_stage, 0)
        turn_idx = bus.dai.get("turn_index", 0)
        # 間 페이즈: auto_tick 정지
        auto_tick_allowed = (not in_intermission) and clock_rules.get("auto_tick", True)

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
                    # 등락 감지용 history 갱신
                    hist = clock.setdefault("filled_history", [old_filled])
                    hist.append(new_filled)
                    if len(hist) > config.CLOCK_OSCILLATION_WINDOW:
                        clock["filled_history"] = hist[-config.CLOCK_OSCILLATION_WINDOW:]
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

        # ── 3.7. Oscillation Fade ───────────────────────────
        # 시계 filled이 등락(+1, -1, +1, -1)만 반복하면 silent fade.
        # 알고리즘: direction change(non-zero diff sign 변화) ≥ DIR_MIN AND net change ≤ NET_THRESHOLD.
        # pending_completion / do_not_resolve_yet 시계는 면제.
        for clock in clocks:
            if clock.get("resolved") or clock.get("pending_completion") or clock.get("do_not_resolve_yet"):
                continue
            hist = clock.get("filled_history", [])
            if len(hist) < config.CLOCK_OSCILLATION_WINDOW:
                continue
            window = hist[-config.CLOCK_OSCILLATION_WINDOW:]
            net_change = abs(window[-1] - window[0])
            # non-zero diff의 sign 변화 카운트
            diffs = [window[i+1] - window[i] for i in range(len(window)-1)]
            nonzero = [d for d in diffs if d != 0]
            dir_changes = sum(
                1 for i in range(1, len(nonzero))
                if (nonzero[i-1] > 0) != (nonzero[i] > 0)
            )
            if dir_changes >= config.CLOCK_OSCILLATION_DIRECTION_CHANGES_MIN and \
               net_change <= config.CLOCK_OSCILLATION_NET_THRESHOLD:
                clock["resolved"] = True
                clock["fade_reason"] = f"oscillation_dir{dir_changes}_net{net_change}"
                clock_events.append(
                    f"OSCILLATE: {clock.get('name', '?')} (dir-changes {dir_changes}, net {net_change})"
                )

        # ── 3.8. Discharge Fade ─────────────────────────────
        # 시계 filled가 양수에서 0으로 떨어지면 silent fade.
        # completion(filled≥segments)의 대칭 — 채워서 터지든 빠져서 사라지든 둘 중 하나만 의미.
        # 신생/잠복 시계(filled_history에 양수 한 번도 없음)는 면제 — staleness 경로로 처리.
        # pending_completion / do_not_resolve_yet 면제.
        for clock in clocks:
            if clock.get("resolved") or clock.get("pending_completion") or clock.get("do_not_resolve_yet"):
                continue
            current_filled = int(clock.get("filled", clock.get("progress", 0)) or 0)
            if current_filled != 0:
                continue
            hist = clock.get("filled_history", [])
            if not hist or max(hist) <= 0:
                continue
            peak = max(hist)
            clock["resolved"] = True
            clock["fade_reason"] = f"discharge_peak{peak}"
            clock_events.append(
                f"DISCHARGE: {clock.get('name', '?')} (peak {peak} → 0)"
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

        # ── 5. Doom Relief 제거됨 (2026-05-23) ───────────────
        # legacy 위기진폭 doom 잔재. 둠 = 서사 진행도/챕터 볼륨 리브랜드 정규편입 이후
        # 평화 장면 → 자동 doom 감소는 의미 충돌. 진정/해소는 間 페이즈로만 흐름.

        # ── 5.5. Phase × Lens × Scene multiplier 적용 ───────
        # 누적된 raw delta(시계 완성/해결, status, judgment)에 결합 multiplier.
        # 間 페이즈는 multiplier=0이라 자동으로 자연 감쇠로 넘어감 (별도 처리).
        raw_delta = delta
        if not in_intermission:
            delta = _apply_doom_multipliers(raw_delta, current_phase, lens_tags, flavor_tags, scene_type)
        else:
            # 間 페이즈: raw delta 무시, 자연 감쇠만 적용
            delta = -config.CHAPTER_INTERMISSION_DECAY

        # ── 6. 글로벌 둠 갱신 ────────────────────────────────
        bus.doom["delta"] = 0  # Consumed — Anomaly/Judgment can write fresh delta after this
        if delta != 0:
            # intimate climax armed 상태: doom threshold 천장 cap (못 더 오름)
            new_doom = current_doom + delta
            climax_armed = bus.doom.get("climax_armed", False)
            if climax_armed and new_doom > climax_threshold:
                new_doom = climax_threshold
            new_doom = max(0, min(100, new_doom))
            bus.doom["value"] = new_doom
            if delta > 0:
                bus.doom["log"] = f"📈 긴장도 증가 (+{delta}, raw={raw_delta}, phase={current_phase})"
            else:
                bus.doom["log"] = f"📉 긴장도 감소 ({delta}, phase={current_phase})"
                bus.doom["narrative_space"] = abs(delta)

        # ── 7. Climax 분기 (intense 자동 / intimate spike 발화) ────
        new_doom = bus.doom.get("value", 0)
        is_intimate = bool(set(lens_tags) & config.INTIMATE_LENS_GROUP)
        already_climaxed = bus.doom.get("climax_triggered", False)

        if not in_intermission and not already_climaxed:
            if is_intimate:
                # intimate: doom ≥ threshold이면 armed=True. spike 발생 시 발화.
                if new_doom >= climax_threshold:
                    bus.doom["climax_armed"] = True
                else:
                    bus.doom["climax_armed"] = False  # threshold 아래 떨어지면 unset
                # spike 검색
                if bus.doom.get("climax_armed"):
                    emotion_summary = bus.emotion.get("summary", {}) if isinstance(bus.emotion, dict) else {}
                    spike_detected = any(
                        isinstance(e, dict) and e.get("spike")
                        for e in emotion_summary.values()
                    )
                    if spike_detected:
                        _trigger_climax(context, bus, clocks, clock_events)
                        bus.doom["intermission_active"] = True  # 다음 턴 間 페이즈 진입
            else:
                # intense (cosmic_horror 활성 또는 default): 자동 발화
                if new_doom >= climax_threshold:
                    _trigger_climax(context, bus, clocks, clock_events)
                    bus.doom["intermission_active"] = True

        # ── 7.5. 間 페이즈 → 새 챕터 起 진입 체크 ─────────────
        if in_intermission and bus.doom.get("value", 0) <= config.CHAPTER_RESET_FLOOR:
            bus.doom["intermission_active"] = False
            bus.doom["climax_triggered"] = False
            bus.doom["climax_armed"] = False
            bus.doom["chapter_reset_log"] = f"📖 새 챕터 起 진입 (doom {bus.doom['value']})"
            logger.info("[Doom] 챕터 reset → 새 챕터 起 진입")

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
        clock["fade_reason"] = "climax_close"
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
    """Vigor/Composure Recovery — 활성도 페이즈 기반.
    리브랜드: 마이너스 패널티 제거. 起 페이즈(저-doom) 회복만 유지.
    "조용한 세계 = 쉴 시간 있음" / "분주한 세계 = 쉴 틈은 없지만 깎이지도 않음".
    """
    dv = bus.doom.get("value", 0)
    # 페이즈 기반 회복 (起 영역만 +1/+2). 그 외 모두 0.
    if dv < 13:
        pressure, label = 2, "😌 이완"
    elif dv < 25:
        pressure, label = 1, "😌 안정"
    else:
        # 承/轉/結/間 — 페이즈 라벨만 노출, pressure 0
        phase = bus.doom.get("chapter_phase", "")
        pressure = 0
        label = f"📖 페이즈 {phase}" if phase else ""

    mechanic = context.request.genres.get("mechanic", {})
    primary = mechanic.get("primary_resource") or "vigor"
    secondary = "composure" if primary == "vigor" else "vigor"
    primary_bus = getattr(bus, primary)
    secondary_bus = getattr(bus, secondary)

    if pressure > 0:
        primary_bus["delta"] = primary_bus.get("delta", 0) + pressure
        secondary_bus["delta"] = secondary_bus.get("delta", 0) + int(pressure * 0.5)
    if label:
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
