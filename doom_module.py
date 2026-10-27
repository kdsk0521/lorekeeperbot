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
                             flavor_tags: List[str], scene_type: str) -> float:
    """raw doom delta에 phase × lens_curve × flavor × scene 모두 적용. **float 반환**.
    정수화/반올림은 호출부(process)에서 carry 누적과 함께 처리 — 작은 델타가 round로
    0이 되어 증발하던 손실(轉 평원 정체)을 막기 위함. mult는 모두 ≥0이라 부호는 보존됨."""
    if raw_delta == 0:
        return 0.0
    phase_mult = _compute_phase_multiplier(phase, lens_tags)
    flavor_mult, _ = _compute_flavor_modifier(flavor_tags)
    scene_mult = _compute_scene_modifier(scene_type)
    return float(raw_delta) * phase_mult * flavor_mult * scene_mult


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

        # [2026-10-17 C46 R5] 이번 턴에 끝난 시계 목록은 §0 **앞**에서 연다. 전엔 §4 에서 새로 만들어 §0 발화
        #   (위협·타이머)가 빠졌다 — 렌더 사실 줄 "X has become real"(지금은 in_motion "This turn" — C74 판4)과 aspects 의 clock_fired 가
        #   기회 시계에만 섰고, 위협이 터진 턴을 렌더가 몰랐다(처음 말하는 자리가 나중의 도착물이었다).
        completed_this_turn = []
        _end_turn = bus.dai.get("turn_index", 0)

        # ── 0-앞. 서사적 해결(옛 1b) — [2026-10-24 C74 W1-1b] 발동(§0)보다 먼저 ──
        #   같은 턴에 Theoria 가 "해결"을 말하고 큐가 "발동"을 고르면 옛 순서(§0 → §1b)에선 발동이 이겼다 —
        #   장면에서 막아낸 위협이 렌더엔 "X has become real" 로 갔다. 해결을 먼저 세우면 §0 은 resolved 를 건너뛴다.
        #   (이변 모듈은 doom 보다 앞이라 그 턴 이변 defer 는 한 번 남는다 — 드문 일, 받아들임. 스펙 C74 W1-1b.)
        flash_resolved = bus.doom.pop("flash_clock_resolved", [])
        # [판1 검토 #3] 이 턴 여기서 막은 시계 — §1a(새 시계)의 상한 · 같은 이름 검사는 옛 순서(만들기 → 해결)대로
        #   이것들을 아직 서 있는 것으로 센다(발동으로 끝난 시계가 칸을 비우는 건 옛 그대로).
        _averted_now = set()
        if isinstance(flash_resolved, list):
            for resolved_name in flash_resolved:
                if not isinstance(resolved_name, str):
                    continue
                for clock in clocks:
                    if clock.get("name") == resolved_name and not clock.get("resolved"):
                        clock["resolved"] = True
                        _averted_now.add(id(clock))
                        _mark_end(clock, "averted", _end_turn)
                        seg = int(clock.get("segments", 6) or 6)
                        # 0626: 시계 해결 = doom NEUTRAL (fall은 間만, 해결도 능동 beat). resolve_reward(vigor/composure 회복)는 유지.
                        resolve_reward = config.CLOCK_RESOLVE_REWARD.get(seg, 3)
                        bus.doom["resolve_reward"] = bus.doom.get("resolve_reward", 0) + resolve_reward
                        clock_events.append(f"RESOLVED: {resolved_name} (neutral doom, +{resolve_reward} recovery)")
                        # [2026-10-24 C74 판3 W2-2] 위협을 막아냄 → 걸린 목표 +1. 타이머 · 기회의 "해결"은 목표에 없음.
                        if clock.get("linked_goal"):
                            if _get_clock_doom(clock) > 0:
                                _advance_linked_goal(context, clock, _end_turn, 1,
                                                     f"{resolved_name} was turned aside", "code:averted")
                            else:
                                clock["linked_goal"] = None
                        break

        # ── 0. Storyteller가 fire 신호 보낸 pending 시계 처리 ──
        fired_names = set(bus.doom.get("_fire_completions", []))
        # [2026-10-24 C74 판2 검토 #6] 끝남은 이긴다(발동 쪽) — 이 턴 판은 턴 앞에 읽은 사본이라, 그 사이 배경이
        #   (목표 done → resolve_clocks_by_goal) 끝낸 시계를 다시 발동할 수 있다. 발동 후보가 있을 때만 세계를 한 번
        #   새로 읽어, 같은 시계(이름 + 만든 턴)가 거기서 끝났으면 끝 칸을 판으로 옮긴다(→ 아래에서 resolved 라 건너뜀).
        if fired_names:
            try:
                import domain_manager as _dm_fw
                _cid_fw = (context.narrative_anchors or {}).get("channel_id", "")
                _ended_fw = {(c.get("name"), c.get("turn_created")): c
                             for c in ((_dm_fw.get_world_state(_cid_fw) or {}).get("doom_clocks") or [])
                             if isinstance(c, dict) and c.get("resolved")} if _cid_fw else {}
                for clock in clocks:
                    _w_fw = _ended_fw.get((clock.get("name"), clock.get("turn_created")))
                    if _w_fw and not clock.get("resolved") and clock.get("name") in fired_names:
                        for _k in ("resolved", "end", "end_turn", "end_quest"):
                            if _k in _w_fw:
                                clock[_k] = _w_fw[_k]
                        clock.pop("pending_completion", None)
                        clock.pop("pending_turn", None)
                        clock_events.append(f"ENDED-ELSEWHERE: {clock.get('name')} ({_w_fw.get('end', '?')})")
            except Exception as _e_fw:
                logger.debug(f"[Doom] fire guard skip: {_e_fw}")
        for clock in clocks:
            if not clock.get("pending_completion") or clock.get("resolved"):
                continue
            if clock.get("name") in fired_names:
                clock["resolved"] = True
                _mark_end(clock, "fired", _end_turn)
                completed_this_turn.append(clock)
                clock.pop("pending_completion", None)
                clock.pop("pending_turn", None)
                complete_doom = _get_clock_doom(clock)
                delta += complete_doom
                sign = f"+{complete_doom}" if complete_doom > 0 else str(complete_doom)
                clock_events.append(f"COMPLETE: {clock['name']} ({sign} doom)")
                if complete_doom > 0 and clock.get("linked_goal"):
                    # [2026-10-24 C74 판2] 발동 = 걸린 목표 깎임(옛: 퀘스트 제거). 도착물 카드 "'목표'(차질)".
                    _setback_linked_goal(context, clock, _end_turn)
                elif clock.get("linked_goal"):
                    clock["linked_goal"] = None   # [C74 판3 W2-2] 타이머 마감 — 목표에 없음(기한은 장부 기한이 맡는다)

        # ── 1. Flash 시계 소비 ──────────────────────────────
        flash_new = bus.doom.pop("flash_clock_new", None)
        # [Reader-GM Stage 3-A] 間 시드 콜이 적립한 수신형 시계 후보 — Flash 제안 없을 때만 폴백 소비
        # (기존 캡·중복·間 do_not_resolve_yet 규칙을 그대로 통과 = 기존 기관의 규칙으로 생성).
        if not (isinstance(flash_new, dict) and flash_new.get("name")):
            _pending_ck = bus.doom.pop("pending_clock_new", None)
            if isinstance(_pending_ck, dict) and _pending_ck.get("name"):
                flash_new = _pending_ck
        flash_updates = bus.doom.pop("flash_clock_updates", [])

        # 1a. 새 시계 생성 (씬타입 억제: create)
        if isinstance(flash_new, dict) and flash_new.get("name") and clock_rules.get("create", True):
            new_name = flash_new["name"]
            active_count = sum(1 for c in clocks if not c.get("resolved") or id(c) in _averted_now)
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
                    "linked_quest": None,   # [C74 판2] 카드 글(걸린 목표 제목) — 연결 자체는 linked_goal(장부 id)
                    "linked_goal": None,
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
                existing_names = {c.get("name") for c in clocks if not c.get("resolved") or id(c) in _averted_now}
                if new_clock["name"] not in existing_names:
                    # [2026-10-24 C75 G2] 시계가 떨어지는 자리 — linked_entity 장소 → 사람 거처 → 그 사람이 든 우물 중심 →
                    #   이 턴 PC 노드(레티어스 10-24 밤). 우물 강착은 배경 tick 의 시계 맞춤이 이 칸을 읽는다(여기선 안 씀).
                    try:
                        import well_store as _ws_bn
                        new_clock["born_node"] = _ws_bn.clock_place(
                            (context.narrative_anchors or {}).get("channel_id", ""), new_clock,
                            pc_location=bus.dai.get("current_location"))
                    except Exception as _e_bn:
                        logger.debug(f"[Doom] born_node skip: {_e_bn}")
                    clocks.append(new_clock)
                    clock_events.append(
                        f"NEW: {new_clock['name']} ({new_clock['segments']}seg, {new_clock['tick_mode']})"
                    )
                    # [2026-10-24 C74 판3 W2-2] 연결: Theoria `for_goal` 이 있으면 그것만(열린 목표면 잇고 아니면 안 잇는다),
                    #   비었을 때만 이름 부분 일치(스펙 W2-2 "2는 1이 null 일 때만").
                    _fg = _for_goal_id(flash_new.get("for_goal"))
                    if _fg:
                        _link_clock_for_goal(context, new_clock, _fg)
                    else:
                        _auto_link_quest_clock(context, new_clock)
                    if new_clock.get("linked_goal"):
                        new_clock["linked_turn"] = bus.dai.get("turn_index", 0)

        # 1b. 서사적 해결 → [2026-10-24 C74 W1-1b] §0 앞으로 옮겼다(위 「0-앞」).

        # ── 2. Flash clock_updates (action/hybrid delta, 씬타입 억제: flash_tick) ──
        mitigation_count = 0
        flash_updated_names = set()
        if isinstance(flash_updates, list) and clock_rules.get("flash_tick", True):
            for update in flash_updates:
                if not isinstance(update, dict):
                    continue
                name = update.get("name", "")
                upd_delta = int(update.get("delta", 0) or 0)
                # [C2 2026-08-01] 선언(-1~+2) 집행. 아래 min(_seg, ...)는 세그먼트 **범위**
                # 클램프라, 모델이 크게 뱉으면 6칸 시계가 한 턴에 다 찼다
                # (= 위기가 예고 없이 완성됨). 선언만 있고 집행이 없던 자리.
                import bot_utils as _bu_cap
                upd_delta, _ = _bu_cap.cap_llm_delta(upd_delta, "doom.clock", "delta", subject=name)
                if not name or upd_delta == 0:
                    continue
                for clock in clocks:
                    if clock.get("name") == name and not clock.get("resolved"):
                        old_filled = int(clock.get("filled", clock.get("progress", 0)) or 0)
                        _seg = int(clock.get("segments", 6) or 6)  # D-5: 구버전/수기편집 string seg 방어
                        new_filled = max(0, min(_seg, old_filled + upd_delta))
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
                _mark_end(clock, "faded", _stale_current)
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
                _mark_end(clock, "faded", _drift_current)
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
                _mark_end(clock, "faded", _stale_current)
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
            _mark_end(clock, "faded", _stale_current)
            clock_events.append(
                f"DISCHARGE: {clock.get('name', '?')} (peak {peak} → 0)"
            )

        # ── 4. 완성 체크 → 극성별 분기 ──────────────────────
        # [2026-10-17 C46 R5] completed_this_turn 은 §0 앞에서 열었다(§0 발화분이 이미 들어 있다).
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
                    _mark_end(clock, "achieved", current_turn)
                    delta += complete_doom
                    completed_this_turn.append(clock)
                    sign = f"+{complete_doom}" if complete_doom > 0 else str(complete_doom)
                    clock_events.append(f"COMPLETE: {clock.get('name', '?')} ({sign} doom)")
                    if clock.get("linked_goal"):
                        # [2026-10-24 C74 판3 W2-2] 기회 달성 → 걸린 목표 +k(깎임과 같은 표)
                        _advance_linked_goal(context, clock, current_turn, setback_steps(clock.get("segments")),
                                             f"{clock.get('name', '?')} came within reach", "code:achieved")
                    if complete_doom < 0:
                        seg = int(clock.get("segments", 6) or 6)
                        resolve_reward = config.CLOCK_RESOLVE_REWARD.get(seg, 3)
                        bus.doom["resolve_reward"] = bus.doom.get("resolve_reward", 0) + resolve_reward

        # ── 4b. Status severity → doom_impact 제거됨 (D-1, 2026-06-18) ──────
        # 부상/상태이상은 PC 상태지 "이야기 활성도"가 아님 — 매 턴 재주입은 자동 점화.
        # 과거 이 +1/+3/+5가 轉 평원의 round 증발을 견디는 유일한 큰 상시 입력이라 climb을
        # 떠받쳤으나, 그 역할은 아래 소수 carry 누적(작은 escalation 보존)이 대체한다.

        # ── 5. Doom Relief 제거됨 (2026-05-23) ───────────────
        # legacy 위기진폭 doom 잔재. 둠 = 서사 진행도/챕터 볼륨 리브랜드 정규편입 이후
        # 평화 장면 → 자동 doom 감소는 의미 충돌. 진정/해소는 間 페이즈로만 흐름.

        # ── 5.4. per-turn 활성도 base gain (2026-06-25 배선) ──
        # DOOM_RAW_GAIN_BASE(의도됐으나 미배선이던 상수)를 energy 기반 tension으로 변조 → raw_delta 합류.
        # calm=거의정지(起 유지), 긴장(rising/detonation)=상승. carry(소수누적)+phase가 셰이핑. 판정→doom 폐기 후 주 상승원.
        energy_dir = bus.dai.get("energy_direction", "idle")
        tension_factor = config.DOOM_TENSION_FACTOR.get(energy_dir, 0.15)
        base_gain = config.DOOM_RAW_GAIN_BASE * tension_factor

        # ── 5.5. Phase × Lens × Scene multiplier 적용 ───────
        # 누적된 raw delta(시계 완성/해결 + base gain)에 결합 multiplier.
        # 間 페이즈는 multiplier=0이라 자동으로 자연 감쇠로 넘어감 (별도 처리).
        raw_delta = delta + base_gain
        if not in_intermission:
            # 소수 carry 누적: multiplier 적용 결과(float)를 이월 잔여와 합쳐 정수부만 적용하고
            # 소수부는 다음 턴으로 이월. round로 작은 escalation(+1×0.4=0.4 등)이 0이 되어
            # 증발하던 轉 평원 정체를 제거하되, 비례성은 유지(큰 델타는 빨리, 작은 건 누적).
            final_float = _apply_doom_multipliers(raw_delta, current_phase, lens_tags, flavor_tags, scene_type)
            carry = float(bus.doom.get("carry", 0.0) or 0.0)
            total = final_float + carry
            delta = int(total)                  # 0 방향 절단
            bus.doom["carry"] = total - delta   # 잔여 소수 이월 (-1, 1)
        else:
            # 間 페이즈: raw delta 무시, 자연 감쇠만 적용. 챕터 경계라 carry 청산.
            delta = -config.CHAPTER_INTERMISSION_DECAY
            bus.doom["carry"] = 0.0

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
        apply_defense_reward(context, bus)

        # ── 10. Summary log ──────────────────────────────────
        final_doom = bus.doom.get("value", 0)
        parts = [f"[Doom] {current_doom}→{final_doom}"]
        if delta != 0:
            parts.append(f"delta={'+' + str(delta) if delta > 0 else str(delta)}")
        if clock_events:
            parts.append(f"clocks: {', '.join(clock_events)}")
        logger.info(" | ".join(parts))

        return context


# ── Helper Functions ─────────────────────────────────────────

def _auto_link_quest_clock(context: "GameContext", clock: dict) -> None:
    """시계 생성 시 열린 목표와 이름 매칭하여 연결. [2026-10-24 C74 판2] 출처 = 장부 목표(옛 퀘스트 보드).
    연결은 시계 쪽 한 칸(linked_goal = 장부 id)만 — 목표 쪽엔 안 적는다(목표 상태는 쓸 때 장부에서 읽는다).
    linked_quest 는 도착물 카드가 읽는 글(목표 제목)로 남긴다."""
    try:
        import thread_ledger
        channel_id = (context.narrative_anchors or {}).get("channel_id", "")
        if not channel_id:
            return
        clock_name = (clock.get("name") or "").strip().lower()
        if not clock_name:
            return
        for g in thread_ledger.open_goals(thread_ledger.load_states(channel_id)):
            goal_name = (g.get("title") or "").strip().lower()
            # 퍼지 매칭: 시계 이름이 목표에 포함 (단방향 — 목표가 상위 개념)
            if clock_name in goal_name:
                clock["linked_goal"] = g.get("id")
                clock["linked_quest"] = g.get("title", "")
                clock.setdefault("linked_turn", clock.get("turn_created", 0))   # [C74 판3] 연결한 턴(느리게 풀기 기준)
                logger.info("[Doom] Auto-linked clock '%s' ↔ goal %s '%s'",
                            clock.get("name"), g.get("id"), g.get("title"))
                return
    except Exception as e:
        logger.warning("[Doom] Auto-link goal-clock failed: %s", e)


def _for_goal_id(v) -> str:
    """[C74 판3 검토 #8] Theoria `for_goal` → 'T3'. 'T3' · 't3 ' · 'T3 도둑 잡기' · 3 · '3' 을 받는다. null · 빈 칸 · 꼴 밖 = ''."""
    import re as _re_fg
    if isinstance(v, bool) or v is None:
        return ""
    if isinstance(v, int):
        return f"T{v}" if v > 0 else ""
    m = _re_fg.match(r"\s*[Tt]?\s*(\d+)\b", str(v))
    return f"T{int(m.group(1))}" if m and int(m.group(1)) > 0 else ""


def _goal_link_live(channel_id: str, clock: dict, gid: str, g: "dict | None", rows: "list | None" = None) -> bool:
    """[C74 판3 검토 #4] 스펙 W2-2 "목표 멈춤 · 지움 → 시계 연결만 푼다". 연결은 쓸 때 판정한다(느리게 풀기) —
    목표가 지금 열려 있어도 연결한 턴 뒤에 멈춤 · 잠 · 닫힘 · 없던 일 행이 있으면 그 연결은 끝난 것이다
    (다시 열린 목표를 옛 시계가 몰래 밀거나 깎지 않게). 세계 저장을 안 건드려 판 사본과 다툴 일이 없다."""
    if not g or g.get("kind") != "goal" or g.get("status") != "open":
        return False
    try:
        import sqlite_store
        since = int(clock.get("linked_turn", clock.get("turn_created", 0)) or 0)
        for r in (rows if rows is not None else sqlite_store.read_thread_log(channel_id)):   # [C74 판4 검토 L2] 읽는 쪽이 한 번 읽어 넘김
            if str(r.get("thread_id")) == gid and r.get("op") in ("pause", "void", "sleep", "close") \
                    and int(r.get("turn") or 0) >= since:
                return False
    except Exception:
        pass
    return True


def _link_clock_for_goal(context: "GameContext", clock: dict, for_goal) -> bool:
    """[2026-10-24 C74 판3 W2-2] Theoria `clock_new.for_goal` → 열린 목표면 연결(linked_goal = 장부 id, linked_quest = 제목).
    없음 · 꼴이 틀림 · 열린 목표가 아님이면 False — 이름 부분 일치로 넘어가지 않는다(for_goal 을 적었으면 그것만, 검토 #8)."""
    try:
        gid = _for_goal_id(for_goal)
        if not gid:
            return False
        import thread_ledger
        channel_id = (context.narrative_anchors or {}).get("channel_id", "")
        if not channel_id:
            return False
        g = thread_ledger.load_states(channel_id).get(gid)
        if not g or g.get("kind") != "goal" or g.get("status") != "open":
            logger.info("[Doom] for_goal skip: clock '%s' → %s (%s)", clock.get("name"), gid,
                        (g or {}).get("status", "none"))
            return False
        clock["linked_goal"] = gid
        clock["linked_quest"] = g.get("title", "")
        logger.info("[Doom] Linked clock '%s' → goal %s '%s' (for_goal)", clock.get("name"), gid, g.get("title"))
        return True
    except Exception as e:
        logger.warning("[Doom] for_goal link failed: %s", e)
        return False


def _advance_linked_goal(context: "GameContext", clock: dict, turn, k: int, reason: str, origin: str) -> None:
    """[2026-10-24 C74 판3 W2-2] 위협 막아냄(+1) · 기회 달성(+k) → 걸린 목표 진행도 +k(origin 행). 목표가 열려 있지
    않으면 올리지 않는다. 렌더 사실 줄 = goal_advance(une_facade). max 에 닿으면 장부가 목표를 끝낸다(code:auto — 판2 끝 훅).
    영수증은 bus.doom["goal_receipts"] → goal 모듈 알림(완료 · 깸만 — 진행 칸 자체는 사실 줄이 말한다). 연결은 여기서 끝."""
    try:
        import thread_ledger
        channel_id = (context.narrative_anchors or {}).get("channel_id", "")
        gid = clock.get("linked_goal")
        clock["linked_goal"] = None
        if not channel_id or not gid or int(k or 0) <= 0:
            return
        g = thread_ledger.load_states(channel_id).get(gid)
        if not _goal_link_live(channel_id, clock, gid, g):
            logger.info("[Doom] advance skip: clock '%s' → %s (%s · link off)", clock.get("name"), gid,
                        (g or {}).get("status", "none"))
            return
        _rc = []
        n = thread_ledger.code_events(channel_id, int(turn or 0), [{"op": "progress", "id": gid, "step": int(k)}],
                                      origin=origin, receipts=_rc)
        if not n:
            return
        bus = context.shared_bus
        clock["advanced_goal"] = gid   # [C74 판3 검토 #5] 도착물 스캐너 11 — 이 시계가 그 목표를 끝냈으면 목표 카드를 따로 안 낸다
        bus.doom.setdefault("goal_receipts", []).extend(_rc)
        bus.doom.setdefault("goal_advance", []).append({"goal": g.get("title", ""), "reason": reason})
        logger.info("[Doom] Goal advance: %s 「%s」 +%d (clock '%s', %s) rows=%d", gid, g.get("title"), k,
                    clock.get("name"), origin, n)
    except Exception as e:
        logger.warning("[Doom] goal advance failed: %s", e)


def setback_steps(segments) -> int:
    """[C74 판2] 시계 발동이 목표를 깎는 칸 — 4칸 1 · 6칸 1 · 8칸 이상 2."""
    try:
        seg = int(segments or 6)
    except (TypeError, ValueError):
        seg = 6
    return 2 if seg >= 8 else 1


def _setback_linked_goal(context: "GameContext", clock: dict, turn) -> None:
    """[2026-10-24 C74 판2] 시계 완성(위협 발동) → 걸린 목표 진행도 −k(code:setback 행). 목표가 열려 있지 않으면
    (멈춤 · 잠 · 끝 · 없던 일) 깎지 않는다. 렌더 사실 줄 = goal_setback(une_facade). 연결은 여기서 끝(linked_goal 비움)."""
    try:
        import thread_ledger
        channel_id = (context.narrative_anchors or {}).get("channel_id", "")
        gid = clock.get("linked_goal")
        clock["linked_goal"] = None
        if not channel_id or not gid:
            return
        g = thread_ledger.load_states(channel_id).get(gid)
        if not _goal_link_live(channel_id, clock, gid, g):   # [C74 판3 검토 #4] 멈춤 · 잠 · 지움 뒤의 옛 연결은 끝
            logger.info("[Doom] setback skip: clock '%s' → %s (%s · link off)", clock.get("name"), gid,
                        (g or {}).get("status", "none"))
            return
        k = setback_steps(clock.get("segments"))
        n = thread_ledger.code_events(channel_id, int(turn or 0), [{"op": "progress", "id": gid, "step": -k}],
                                      origin="code:setback")
        if not n:
            return   # 장부에 안 섰으면(쓰기 실패 · 전이 표 밖) 사실 줄도 카드 글도 없다
        clock["setback_quest"] = g.get("title", "")   # 도착물 카드 "'목표'(차질)"
        context.shared_bus.doom.setdefault("goal_setback", []).append(
            {"goal": g.get("title", ""), "reason": f"{clock.get('name', '?')} came to pass"}   # [C46 R6] 기계 낱말 없이
        )
        logger.info("[Doom] Goal setback: %s 「%s」 −%d (clock '%s') rows=%d", gid, g.get("title"), k, clock.get("name"), n)
    except Exception as e:
        logger.warning("[Doom] goal setback failed: %s", e)


def _mark_end(clock: dict, kind: str, turn) -> None:
    """[2026-10-17 C46 R1] 시계가 끝난 **사유**와 **턴**. `resolved = True` 를 세우는 자리마다 같이 적는다.

    사유: fired(스토리텔러 발화) · achieved(기회 완성) · averted(서사 해결) · quest(퀘스트 완료,
          game_world.resolve_clocks_by_goal — C74 판2) · faded(정체·이탈·등락·방전 — 조용히 잊힘) · climax(클라이맥스 강제 완성).
    읽는 쪽은 도착물 스캐너(world_board Scanner 4) 하나다 — 잊힘·클라이맥스는 소재가 아니고 끝난 지 오래된 것도
    아니다. 전엔 `resolved` 하나라 아홉 가지 끝이 전부 "시계 완성" 도착물이 됐다. 옛 시계(키 없음)는 소재에서 빠진다.
    """
    clock["end"] = kind
    try:
        clock["end_turn"] = int(turn or 0)
    except (TypeError, ValueError):
        clock["end_turn"] = 0


def _get_clock_doom(clock: dict) -> int:
    """시계의 doom_on_complete 값을 반환 (극성 반영)."""
    custom_doom = clock.get("doom_on_complete")
    if custom_doom is not None:
        # [C74 판3 검토 #10] Theoria 가 글("null" · "-10 (opportunity)")을 넣어도 doom 단계가 통째로 죽지 않게
        try:
            return int(custom_doom)
        except (TypeError, ValueError):
            import re as _re_dc
            _m = _re_dc.match(r"\s*(-?\d+)", str(custom_doom))
            if _m:
                return int(_m.group(1))
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
        _mark_end(clock, "climax", bus.dai.get("turn_index", 0))
        clock_events.append(f"CLIMAX: {clock.get('name', '?')} forced complete")
    bus.doom["climax_triggered"] = True

    # Doom runs after Storyteller in pipeline → push climax event to next-turn queue
    channel_id = (context.narrative_anchors or {}).get("channel_id", "")
    if channel_id:
        import domain_manager
        st_state = domain_manager.get_storyteller_state(channel_id)
        queue = st_state.get("event_queue", [])
        current_turn = bus.dai.get("turn_index", 0)  # D-3: 모듈 전체와 turn source 통일(ws 아님)
        queue.insert(0, {
            "tag": "climax",
            "category": "supernatural",
            "intensity": "Extreme",
            "polarity": "negative",
            "line": "every clock completes — the world reaches its critical point.",
            "reason": "doom_climax",
            "queued_turn": current_turn,
        })
        st_state["event_queue"] = queue
        domain_manager.update_storyteller_state(channel_id, st_state)

    logger.info("[Doom] CLIMAX TRIGGERED — all clocks forced, event queued for next turn")


def apply_defense_reward(context: "GameContext", bus, reward: int = 0) -> int:
    """시계 완화·해소 보상 → 평형 회복 (**코드 소유 쓰기**).

    [2026-09-06 P8b] 이 함수가 옛 `_apply_pressure` 를 대신한다. 지운 것과 남긴 것의 경계가
    여기서 제일 또렷하다:
      - **삭제**: 페이즈 기반 회복(doom<13 → +2, <25 → +1). "조용한 세계 = 쉴 시간 있음"은
        장면을 분류해서 숫자를 정하는 코드다 — 정확히 rule 의 몫이고, 회복은 산문이 보여줬을 때
        관측 델타로 들어온다. 압력 라벨(mental_pressure_log)도 그 공식의 표시라 함께 삭제.
      - **유지**: 시계 완화/해소 보상. 시계가 몇 칸 줄었는지는 코드가 이미 확정한 **사건**이고,
        이 회복은 그 사건의 대가다(judgment Effort 선불과 같은 부류). 그래서 캡 면제 문을 쓴다.
    Returns: 실제로 움직인 평형 델타(없으면 0).
    """
    total = int(reward or 0) + int(bus.doom.get("defense_reward", 0)) + int(bus.doom.get("resolve_reward", 0))
    if total <= 0:
        return 0
    moved = 0
    try:
        import custom_vars as _cv_dm
        rec = _cv_dm.apply_system_delta(
            (context.narrative_anchors or {}).get("channel_id", ""), "평형", total,
            "clock mitigated", actor=(context.narrative_anchors or {}).get("acting_user_id", ""),
            exempt_cap=True, source="doom.defense_reward",
        )
        if rec:
            moved = int(rec.get("delta", 0) or 0)
            if isinstance(bus.composure, dict):
                bus.composure["value"] = int(rec.get("to", bus.composure.get("value", 100)))
    except Exception as e:
        logger.debug("[Doom] 평형 보상 skip: %s", e)

    reward_parts = []
    if bus.doom.get("defense_reward", 0) > 0:
        reward_parts.append(f"완화 +{bus.doom['defense_reward']}")
    if bus.doom.get("resolve_reward", 0) > 0:
        reward_parts.append(f"해소 +{bus.doom['resolve_reward']}")
    if reward_parts:
        bus.doom["defense_log"] = f"🛡️ {' | '.join(reward_parts)}"
    return moved
