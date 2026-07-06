"""
Lorekeeper UNE - Story Director (v1.0)
Code-driven narrative pacing & direction engine.
Fills gaps: proactive plot advancement, idle input handling, scene transition direction.

NOT a prompt generator — outputs structured hints consumed by the response builder.
Sits at Stage 4.5 (after Anomaly, before Doom).

Design: AnomalyModule 패턴 — 점수 테이블 + 코드 결정, LLM 호출 없음.
"""

import logging
from typing import Any, Dict, List, Optional, Tuple
from orchestration_context import GameContext

logger = logging.getLogger("StoryDirector")

# =========================================================
# Pacing Table: scene_type × energy_direction → pacing advice
# Values: "push" (accelerate), "hold" (maintain), "breathe" (slow down), "pivot" (shift direction)
# =========================================================
PACING_TABLE: Dict[str, Dict[str, str]] = {
    "normal":      {"idle": "push",  "stagnant": "push",  "rising": "hold",    "detonation": "breathe", "aftershock": "hold"},
    "combat":      {"idle": "hold",  "stagnant": "hold",  "rising": "hold",    "detonation": "hold",    "aftershock": "breathe"},
    "social":      {"idle": "push",  "stagnant": "pivot", "rising": "hold",    "detonation": "breathe", "aftershock": "pivot"},
    "exploration": {"idle": "push",  "stagnant": "push",  "rising": "hold",    "detonation": "breathe", "aftershock": "hold"},
    "rest":        {"idle": "hold",  "stagnant": "hold",  "rising": "breathe", "detonation": "breathe", "aftershock": "hold"},
}

# Thread priority by chain status
# chain_status enum = OPEN/CLOSED/DORMANT (Theoria 현행). 옛 극아크 RISING/CLIMAX/FALLING은
# doom→챕터볼륨 起承轉結으로 이사 → 여기선 스레드 개폐만 가중.
# ※ 값은 _generate_beats 인라인 임계(L757~: else<0.35=steps / >=0.55=draws up)에 캘리브레이션됨.
#   OPEN=0.3(차분 steps forward, 정체 시 +0.3=0.6→draws up), DORMANT=0.2. 임계 바꾸면 여기도 동반 조정.
CHAIN_PRIORITY: Dict[str, float] = {
    "OPEN": 0.3,
    "DORMANT": 0.2,
    "CLOSED": 0.0,
}

# Idle input detection keywords (short/empty/reactive-only inputs)
IDLE_INPUT_MARKERS = {
    "계속", "다음", "진행", "어떻게", "뭐해", "주변",
    "기다린다", "기다려", "지켜본다", "관찰", "둘러본다",
    "...", "…", "ㅇㅇ", "ㅇㅋ", "넹", "음",
}

# Scene transition mood mapping
TRANSITION_MOOD: Dict[str, Dict[str, str]] = {
    # energy → {doom_level_bucket → mood_hint}
    "idle":       {"low": "contemplative", "mid": "uneasy", "high": "dread"},
    "stagnant":   {"low": "listless", "mid": "tense", "high": "suffocating"},
    "rising":     {"low": "hopeful", "mid": "charged", "high": "desperate"},
    "detonation": {"low": "cathartic", "mid": "overwhelming", "high": "apocalyptic"},
    "aftershock": {"low": "reflective", "mid": "haunted", "high": "numb"},
}


# =========================================================
# Emotion → Spotlight Composite Score
# =========================================================
# 6.1 + 6.4 (2026-05-20): _determine_focus와 NPC initiative direction 두 사이트가
# 같은 공식을 쓰도록 helper로 통합. 이전엔 intensity + spike만 봤음.
#
# 공식:
#   composite = intensity
#             + (0.5 if spike else 0)         # 감정 급변
#             + (0.3 if drift else 0)          # turn_pair ≠ scene_pair (감정 전환 중)
#             + 0.15 * pair_confidence         # 관계 라벨 매치 강도 (Tier 1=1.0 → +0.15, Tier 9=0.0 → +0)
#
# 설계 근거:
# - spike(0.5): raw 도메인 |Δ| ≥ 0.25 = 명백한 dramatic jolt. 최우선 신호.
# - drift(0.3): scene_pair가 누적 평균이라 turn과 어긋나면 NPC가 감정적 전환 중. 강한 narrative pull.
# - confidence(0.15): 가산만 — Tier 9(solo plutchik) NPC를 spotlight에서 영구 배제하지 않기 위함.
def _emotion_composite_score(emo: Dict[str, Any]) -> float:
    """to_bus_dict.summary 한 NPC 엔트리에 대한 composite score 계산."""
    try:
        score = float(emo.get("intensity", 0))
    except (TypeError, ValueError):
        score = 0.0
    if emo.get("spike"):
        score += 0.5
    # Drift: scene_pair 존재하고 turn_pair와 어긋날 때
    scene_base = emo.get("scene_base", "") or ""
    if scene_base:
        turn_pair = (emo.get("base", "") or "", emo.get("modifier", "") or "")
        scene_pair = (scene_base, emo.get("scene_mod", "") or "")
        if turn_pair != scene_pair:
            score += 0.3
    # Confidence weight
    try:
        conf = float(emo.get("pair_confidence", 0.0))
    except (TypeError, ValueError):
        conf = 0.0
    conf = max(0.0, min(1.0, conf))
    score += 0.15 * conf
    return score


class StoryDirector:
    """Stateless narrative direction engine. All state comes from SharedBus."""

    @staticmethod
    def process(context: GameContext) -> GameContext:
        """
        Main entry point. Reads bus data, computes direction hints.
        Writes to bus.dai["story_direction"].
        """
        bus = context.shared_bus
        dai = bus.dai

        # Gather inputs
        energy = dai.get("energy_direction", "idle")
        scene_type = dai.get("scene_type", "normal")
        narrative_chain = dai.get("narrative_chain", {})
        memory_triggers = dai.get("memory_triggers", [])
        user_input = context.request.user_input.strip()
        try:
            doom_value = int(bus.doom.get("value", 0))
        except (TypeError, ValueError):
            doom_value = 0
        emotion_summary = bus.emotion.get("summary", {})
        anomaly_triggered = bus.anomaly.get("triggered", False)
        anomaly_decision = bus.anomaly.get("decision", "skip")
        active_conditions = bus.anomaly.get("_storyteller_state", {}).get("active_conditions", [])
        quality_flags = dai.get("quality_flags", {})

        # 1. Pacing decision
        pacing = StoryDirector._decide_pacing(scene_type, energy)

        # 2. Idle input detection
        is_idle = StoryDirector._detect_idle_input(user_input)
        # [Reader-GM R4] 독자 거부권 — 직전 턴을 독자가 rising으로 수신했으면 idle 강등 방지.
        # READER_GM_FEED=0(기본)이면 완전 무동작. 결정론 유지: enum 하나 → 분기 하나. spec §6b.
        if is_idle:
            try:
                import config as _cfg
                if getattr(_cfg, "READER_GM_FEED", 0):
                    import sqlite_store as _ss
                    _ch = context.narrative_anchors.get("channel_id", "")
                    _rows = _ss.read_reader_log_tail(_ch, limit=1) if _ch else []
                    _tr = (_rows[-1][1].get("tension_read") or {}).get("value", "") if _rows else ""
                    if _tr == "rising":
                        is_idle = False
                        logger.info("[ReaderVeto] idle demotion blocked (reader received tension=rising)")
            except Exception:
                pass
        idle_direction = None
        if is_idle:
            idle_direction = StoryDirector._generate_idle_direction(
                energy, scene_type, active_conditions,
                narrative_chain, emotion_summary, doom_value
            )

        # 3. Plot thread tracking & advancement hints
        #    [SD-A4] 생산자 유지(축 B 재활용 후보) — 현재는 bus 미송출.
        _plot_hints_reserved = StoryDirector._analyze_plot_threads(
            narrative_chain, memory_triggers, active_conditions,
            quality_flags, energy
        )

        # 4. Scene transition guidance
        transition_full = StoryDirector._compute_transition(
            energy, doom_value, scene_type,
            anomaly_triggered, anomaly_decision, pacing
        )

        # 5. Focus guidance (who/what to spotlight)
        focus_full = StoryDirector._determine_focus(
            emotion_summary, active_conditions,
            narrative_chain, dai.get("relevant_npcs", [])
        )

        # 6. Tension axis (rising/falling/plateau)
        try:
            _vigor_val = int(bus.vigor.get("value", 70))
        except (TypeError, ValueError):
            _vigor_val = 70
        try:
            _composure_val = int(bus.composure.get("value", 70))
        except (TypeError, ValueError):
            _composure_val = 70
        tension_axis = StoryDirector._compute_tension_axis(
            energy, doom_value, narrative_chain,
            _vigor_val, _composure_val
        )

        # 7. Seven Dice (W9) — delegated to dice_engine
        dice_result = None
        try:
            from dice_engine import DiceEngine
            channel_id = (context.narrative_anchors or {}).get("channel_id", "")
            dice_result = DiceEngine.roll(channel_id, energy, quality_flags)
        except Exception as e:
            logger.warning("[StoryDirector] Dice roll failed, skipping: %s", e)

        # 8. Beat Queue (SD-Ba3, 2026-04-22) — LIBRA StoryAuthor nextBeats 최소 이식
        #    트리거: (turn - last_planned_turn >= 2) OR (queue empty) OR anomaly_triggered
        #    idle 입력은 강제 재계획 아님 — 비트는 원래 이런 때 쓰려고 쌓는 것.
        active_beat = None
        beats_replanned = False
        try:
            _chan_beat = (context.narrative_anchors or {}).get("channel_id", "")
            _cur_turn = int(bus.anomaly.get("_current_turn", 0) or 0)
            _st_state = bus.anomaly.get("_storyteller_state", {}) or {}
            _beats = list(_st_state.get("next_beats", []) or [])
            _last_planned = int(_st_state.get("last_planned_turn", 0) or 0)
            _cap = int(_st_state.get("beats_cap", 6) or 6)

            _need_replan = (
                (not _beats)
                or (_cur_turn - _last_planned >= 2)
                or anomaly_triggered
            )

            if _need_replan:
                # SD-Bb3: Theoria author-hint beats (휴리스틱 비트 보강용)
                _llm_hints = dai.get("suggested_beats", []) or []
                if not isinstance(_llm_hints, list):
                    _llm_hints = []
                _beats = StoryDirector._generate_beats(
                    _plot_hints_reserved, energy, is_idle,
                    anomaly_triggered, emotion_summary,
                    suggested_beats=_llm_hints, cap=_cap, pacing=pacing,
                    doom_value=doom_value
                )
                _last_planned = _cur_turn
                beats_replanned = True

            # head 소비
            if _beats:
                active_beat = _beats.pop(0)

            # 상태 영속화
            if _chan_beat:
                _st_state["next_beats"] = _beats
                _st_state["last_planned_turn"] = _last_planned
                try:
                    import domain_manager as _dm_beat
                    _dm_beat.update_storyteller_state(_chan_beat, _st_state)
                except Exception as _e_persist:
                    logger.warning("[StoryDirector] Beat queue persist failed: %s", _e_persist)
                # bus 쪽 복제본도 최신화 (같은 턴 내 다른 단계가 읽을 수 있음)
                bus.anomaly["_storyteller_state"] = _st_state
        except Exception as e:
            logger.warning("[StoryDirector] Beat queue failed, skipping: %s", e)

        # Assemble direction output — 소비자가 실제 읽는 키만 bus에 송출 (SD-A4 감사 결과)
        # 유령 필드 제거: plot_hints, transition.mood/scene_type, focus.elements,
        #                idle_direction.{type,tag,emotion,chain_status}
        direction: Dict[str, Any] = {
            "active": True,
            "pacing": pacing,
            "tension_axis": tension_axis,
            "is_idle_input": is_idle,
        }

        # transition: cut + suggest_shift만 소비됨 (iceberg)
        _transition_out: Dict[str, Any] = {"cut": transition_full.get("cut", "natural")}
        if transition_full.get("suggest_shift"):
            _transition_out["suggest_shift"] = transition_full["suggest_shift"]
        direction["transition"] = _transition_out

        # focus: spotlight + reason만 소비됨 (iceberg, story_director self-ref)
        _focus_out: Dict[str, Any] = {"spotlight": focus_full.get("spotlight", "none")}
        if focus_full.get("reason"):
            _focus_out["reason"] = focus_full["reason"]
        direction["focus"] = _focus_out

        if idle_direction:
            # idle_direction: source/hint/npc만 소비됨 (slot_manager, iceberg)
            _idle_out: Dict[str, Any] = {"source": idle_direction.get("source", "ambient")}
            if idle_direction.get("hint"):
                _idle_out["hint"] = idle_direction["hint"]
            if idle_direction.get("npc"):
                _idle_out["npc"] = idle_direction["npc"]
            direction["idle_direction"] = _idle_out

        if dice_result:
            direction["dice"] = dice_result

        # SD-Ba3: 현재 턴용 활성 비트 (Slot 33에서 소비)
        if active_beat:
            direction["next_beat"] = active_beat

        # [latent relations] conflict/alliance 그래프 → "생길 수 있는 사건" 잠재 힌트.
        # anti-railroad: 강제 아닌 가능성. 씬에 있는 NPC(relevant_npcs) + 현저(함수가 intensity 게이트)만, 최대 3.
        try:
            import entity_relations as _er_l
            _scene_npcs = set(dai.get("relevant_npcs", []) or [])
            _chan_l = (context.narrative_anchors or {}).get("channel_id", "")
            if _chan_l and len(_scene_npcs) >= 2:
                _latent = []
                for _s, _t, _e in _er_l.get_conflict_pairs(_chan_l):
                    if _s in _scene_npcs and _t in _scene_npcs:
                        _latent.append(f"{_s}↔{_t} {_e.get('type','tension')} could surface")
                for _cl in _er_l.get_alliance_clusters(_chan_l):
                    _present = [n for n in _cl if n in _scene_npcs]
                    if len(_present) >= 2:
                        _latent.append(f"{{{', '.join(_present)}}} could move together")
                if _latent:
                    direction["latent_relations"] = _latent[:3]
        except Exception:
            pass

        # Write to bus
        dai["story_direction"] = direction

        # Log — 감사 후 bus가 아닌 reserved local 기준으로 로깅
        logger.info(
            "[StoryDirector] pacing=%s tension=%s idle=%s hints=%d focus=%s beat=%s replan=%s",
            pacing, tension_axis, is_idle,
            len(_plot_hints_reserved), _focus_out.get("spotlight", "none"),
            "Y" if active_beat else "N",
            "Y" if beats_replanned else "N",
        )

        return context

    # ----- Pacing -----

    @staticmethod
    def _decide_pacing(scene_type: str, energy: str) -> str:
        """Pacing table lookup."""
        row = PACING_TABLE.get(scene_type, PACING_TABLE["normal"])
        return row.get(energy, "hold")

    # ----- Idle Input Detection -----

    @staticmethod
    def _detect_idle_input(user_input: str) -> bool:
        """Detect short/passive/idle inputs that need proactive direction."""
        if not user_input:
            return True
        cleaned = user_input.strip().lower()
        # Very short input (≤ 5 chars, excluding OOC)
        if len(cleaned) <= 5 and not cleaned.startswith(("(", "[")):
            return True
        # Known idle markers
        if cleaned in IDLE_INPUT_MARKERS:
            return True
        # Ellipsis-heavy
        if cleaned.replace(".", "").replace("…", "").strip() == "":
            return True
        return False

    @staticmethod
    def _generate_idle_direction(
        energy: str, scene_type: str,
        active_conditions: List[dict],
        narrative_chain: dict,
        emotion_summary: dict,
        doom_value: int
    ) -> Dict[str, Any]:
        """Generate proactive direction when input is idle/passive."""
        direction: Dict[str, Any] = {"type": "proactive"}

        # Priority 1: Active conditions with escalation potential
        if active_conditions:
            # Pick highest-intensity condition
            best = max(
                active_conditions,
                key=lambda c: {"Low": 1, "Mid": 2, "High": 3, "Extreme": 4}.get(
                    c.get("intensity", "Mid"), 2
                )
            )
            direction["source"] = "active_condition"
            direction["tag"] = best.get("tag", "")
            direction["hint"] = "condition_escalation"
            return direction

        # Priority 2: Unresolved narrative chain still escalating.
        # chain_status enum 진화(극아크 RISING/CLIMAX → 개폐 OPEN/CLOSED/DORMANT, 상승은 doom 起承轉結으로 이사).
        # → OPEN(체인 살아있음) + 장면 energy 상승국면(rising/detonation)일 때만 이어감.
        #   energy 게이트가 옛 RISING/CLIMAX 희소성을 대체(매 OPEN턴 과발화 방지, P3/P4 우선순위 보존).
        chain_status = narrative_chain.get("chain_status", "OPEN")
        if chain_status == "OPEN" and energy in ("rising", "detonation"):
            direction["source"] = "narrative_chain"
            direction["hint"] = "chain_continuation"
            direction["chain_status"] = chain_status
            return direction

        # Priority 3: Emotional spike in an NPC → NPC-driven scene
        # Pass D-2 (2026-04-21): 기존 first-match 루프는 dict insertion order 의존이라
        #   같은 상태 집합이라도 NPC 이름 정렬/삽입 순서만 바뀌어도 direction.npc가
        #   달라졌다 (T1/T2/T3 smoke 비결정성 확인). spotlight 선정과 동일 공식
        #   (intensity + 0.5_if_spike) 기반 composite score primary + npc_name
        #   alphabetical secondary로 결정적 선정. smoke_direction_tiebreak.py 5/5 pass.
        # 6.1/6.4 (2026-05-20): composite score 공식을 _emotion_composite_score helper로 통합.
        # 게이트(spike OR intensity > 0.6)는 그대로 유지 — 낮은 강도 NPC가 drift/confidence
        # bonus만으로 npc_initiative 트리거하는 걸 막기 위함.
        candidates = []
        for npc_name, emo in emotion_summary.items():
            if not isinstance(emo, dict):
                continue
            try:
                _emo_intensity = float(emo.get("intensity", 0))
            except (TypeError, ValueError):
                _emo_intensity = 0.0
            _emo_spike = bool(emo.get("spike"))
            if _emo_spike or _emo_intensity > 0.6:
                _composite = _emotion_composite_score(emo)
                candidates.append((_composite, npc_name, emo))

        if candidates:
            # 내림차순 composite, 오름차순 name (결정적 tiebreak)
            candidates.sort(key=lambda x: (-x[0], x[1]))
            _, _best_name, _best_emo = candidates[0]
            direction["source"] = "emotion"
            direction["hint"] = "npc_initiative"
            direction["npc"] = _best_name
            # pair 스키마 v2: 'dominant' → 'base' (to_bus_dict summary)
            direction["emotion"] = _best_emo.get("base", "neutral") or "neutral"
            return direction

        # Priority 4: High doom → environmental pressure
        if doom_value >= 60:
            direction["source"] = "doom"
            direction["hint"] = "environmental_pressure"
            return direction

        # Default: world-driven ambient progression
        direction["source"] = "ambient"
        direction["hint"] = "world_progression"
        return direction

    # ----- Plot Thread Analysis -----

    @staticmethod
    def _analyze_plot_threads(
        narrative_chain: dict,
        memory_triggers: List[Any],
        active_conditions: List[dict],
        quality_flags: dict,
        energy: str
    ) -> List[Dict[str, Any]]:
        """Score and rank unresolved plot threads for advancement hints."""
        threads: List[Dict[str, Any]] = []

        # Thread from narrative chain
        chain_status = narrative_chain.get("chain_status", "OPEN")
        chain_priority = CHAIN_PRIORITY.get(chain_status, 0.3)
        if chain_status not in ("CLOSED",) and chain_priority > 0:
            thread = {
                "source": "narrative_chain",
                "label": narrative_chain.get("current_thread", "main_plot"),
                "priority": chain_priority,
                "type": "continuation",
                "chain_status": chain_status,
            }
            # Stagnation boost
            if quality_flags.get("stagnation_warning"):
                thread["priority"] = min(1.0, thread["priority"] + 0.3)
                thread["urgency"] = "stagnation"
            # Convergence boost
            if quality_flags.get("convergence_warning"):
                thread["priority"] = min(1.0, thread["priority"] + 0.2)
                thread["urgency"] = "convergence"
            threads.append(thread)

        # Threads from active conditions
        for cond in active_conditions:
            intensity_score = {"Low": 0.2, "Mid": 0.4, "High": 0.7, "Extreme": 1.0}.get(
                cond.get("intensity", "Mid"), 0.4
            )
            threads.append({
                "source": "active_condition",
                "label": cond.get("tag", "unknown"),
                "priority": intensity_score,
                "type": "condition_thread",
                "polarity": cond.get("polarity", "mixed"),
                "intensity": cond.get("intensity", "Mid"),
            })

        # Threads from memory triggers
        for trigger in memory_triggers[:3]:  # Cap to avoid noise
            if isinstance(trigger, dict):
                threads.append({
                    "source": "memory",
                    "label": trigger.get("tag", trigger.get("key", "memory")),
                    "priority": 0.3,
                    "type": "memory_callback",
                })
            elif isinstance(trigger, str) and trigger:
                threads.append({
                    "source": "memory",
                    "label": trigger,
                    "priority": 0.3,
                    "type": "memory_callback",
                })

        # Sort by priority descending
        threads.sort(key=lambda t: t["priority"], reverse=True)

        # Return top threads (cap at 3 to avoid information overload)
        return threads[:3]

    # ----- Scene Transition -----

    @staticmethod
    def _compute_transition(
        energy: str, doom_value: int, scene_type: str,
        anomaly_triggered: bool, anomaly_decision: str, pacing: str
    ) -> Dict[str, Any]:
        """Compute scene transition mood and cut guidance."""
        # Doom bucket
        if doom_value < 30:
            doom_bucket = "low"
        elif doom_value < 65:
            doom_bucket = "mid"
        else:
            doom_bucket = "high"

        # Mood from table
        mood_row = TRANSITION_MOOD.get(energy, TRANSITION_MOOD["rising"])
        mood = mood_row.get(doom_bucket, "neutral")

        # Cut type: how to transition between beats
        if pacing == "push":
            cut = "hard_cut"  # Jump to next beat quickly
        elif pacing == "breathe":
            cut = "fade"  # Slow transition, room to settle
        elif pacing == "pivot":
            cut = "contrast_cut"  # Shift tone/location
        else:
            cut = "natural"  # Smooth continuation

        # If anomaly just triggered, override to dramatic entrance
        if anomaly_triggered:
            cut = "dramatic_entrance"

        transition = {
            "mood": mood,
            "cut": cut,
            "scene_type": scene_type,
        }

        # Suggest scene type shift if energy/pacing warrant it
        if pacing == "pivot" and scene_type in ("normal", "social"):
            transition["suggest_shift"] = "exploration"
        elif pacing == "breathe" and scene_type == "combat":
            transition["suggest_shift"] = "normal"

        return transition

    # ----- Focus Guidance -----

    @staticmethod
    def _determine_focus(
        emotion_summary: dict,
        active_conditions: List[dict],
        narrative_chain: dict,
        relevant_npcs: list
    ) -> Dict[str, Any]:
        """Determine who/what to spotlight this beat."""
        focus: Dict[str, Any] = {"spotlight": "none", "elements": []}

        # 1. Emotional NPC spotlight
        # 6.1/6.4 (2026-05-20): _emotion_composite_score helper로 통합 (intensity + spike
        # + drift + pair_confidence). 결정적 tiebreak를 위해 candidates 리스트 + sort 패턴
        # 채용 — 이전엔 first-match 루프라 dict insertion order 의존 비결정성이 있었음.
        candidates = []
        for npc_name, emo in emotion_summary.items():
            if not isinstance(emo, dict):
                continue
            score = _emotion_composite_score(emo)
            if score > 0.3:
                candidates.append((score, npc_name, emo))

        if candidates:
            # 내림차순 composite, 오름차순 name (결정적 tiebreak)
            candidates.sort(key=lambda x: (-x[0], x[1]))
            best_score, best_npc, best_emo = candidates[0]
            focus["spotlight"] = best_npc
            focus["reason"] = "emotional_intensity"
            # pair 스키마 v2: 'dominant' → 'base' (to_bus_dict summary)
            focus["elements"].append({
                "type": "npc", "name": best_npc,
                "emotion": best_emo.get("base", ""),
                "intensity": best_score,
            })

        # 2. Active condition elements (environmental focus)
        for cond in active_conditions[:2]:
            focus["elements"].append({
                "type": "condition",
                "tag": cond.get("tag", ""),
                "intensity": cond.get("intensity", "Mid"),
                "location": cond.get("location", ""),
            })

        # 3. Chain thread element
        chain_thread = narrative_chain.get("current_thread", "")
        if chain_thread:
            focus["elements"].append({
                "type": "thread",
                "label": chain_thread,
                "status": narrative_chain.get("chain_status", "OPEN"),
            })

        # 4. Relevant NPCs from Theoria (non-overlapping with spotlight)
        for npc in relevant_npcs[:3]:
            npc_name = npc if isinstance(npc, str) else npc.get("name", "")
            if npc_name and npc_name != focus.get("spotlight"):
                focus["elements"].append({
                    "type": "npc_mention",
                    "name": npc_name,
                })

        return focus

    # ----- Tension Axis -----

    @staticmethod
    def _compute_tension_axis(
        energy: str, doom_value: int,
        narrative_chain: dict,
        vigor_value: int, composure_value: int
    ) -> str:
        """
        Compute aggregate tension direction: rising / falling / plateau / critical.
        Used by response builder for prose intensity calibration.
        """
        score = 0.0

        # Energy contribution
        energy_scores = {
            "idle": -0.2,
            "stagnant": 0.0,
            "rising": 0.3,
            "detonation": 0.8,
            "aftershock": -0.1,
        }
        score += energy_scores.get(energy, 0.0)

        # Doom contribution (normalized to 0-1)
        score += (doom_value / 100.0) * 0.4

        # Chain status contribution
        # chain 기여(개폐만). 극적 상승 텐션은 위 doom_value 기여가 담당 — 옛 RISING/CLIMAX는 doom 起承轉結으로 이사.
        chain_score = {
            "OPEN": 0.1,
            "DORMANT": -0.1,
            "CLOSED": -0.3,
        }
        score += chain_score.get(narrative_chain.get("chain_status", "OPEN"), 0.0)

        # Mental state contribution (low vigor/composure = higher tension)
        avg_mental = (vigor_value + composure_value) / 2
        if avg_mental < 30:
            score += 0.3
        elif avg_mental < 50:
            score += 0.1

        # Classify
        if score >= 0.8:
            return "critical"
        elif score >= 0.4:
            return "rising"
        elif score <= -0.1:
            return "falling"
        else:
            return "plateau"

    # ----- W9: Seven Dice -----
    # Moved to dice_engine.py (DiceEngine.roll). See step 7 in process().

    # ----- SD-Ba2 (2026-04-22): Beat Generator -----
    # LIBRA StoryAuthor nextBeats 최소 이식.
    # _analyze_plot_threads 결과(dict 리스트) + 장면 맥락을 자연어 지시문 리스트로 변환.
    # 영어 state-form Directive 스타일 (Slot 33 recency zone에 주입됨).
    # LLM 호출 없음 — 휴리스틱만으로 템플릿 렌더.
    # =========================================================

    # 극적 강도 동사 — doom(챕터볼륨 활성도, 起承轉結)에서 읽음.
    # 옛 _CHAIN_VERB(은퇴 chain 극아크 enum) 재설계: 드라마가 doom으로 이사 → 동사도 doom_value가 결정.
    # chain_status(OPEN/DORMANT)는 전진/휴면만 결정. 정확한 起承轉結 boundary는 lens별(doom_module)이나 beat 힌트엔 이 휴리스틱으로 충분.
    _DOOM_BEAT_VERB: Dict[str, str] = {
        "climax": "pushes into the decisive turn",
        "rising": "draws the tension up another notch",
        "intro":  "steps forward one careful pace",
    }

    # 강도별 수식어 매핑
    _INTENSITY_ADJ: Dict[str, str] = {
        "Low":     "faint",
        "Mid":     "vivid",
        "High":    "thick",
        "Extreme": "overwhelming",
    }

    @staticmethod
    def _normalize_llm_beat(raw: str) -> Optional[str]:
        """
        Theoria suggested_beats 1건을 정규화.
        - 공백 정리
        - 길이 가드 (>= 6 chars, <= 200 chars)
        - 프리픽스 강제: "Next beat:"가 없으면 붙여줌
        - 빈 값/금지 토큰 있으면 None
        """
        if not isinstance(raw, str):
            return None
        s = raw.strip().strip('"\'').strip()
        if not s:
            return None
        if len(s) < 6 or len(s) > 200:
            return None
        # 렌더링 오염 방지: 기본 대사/내레이션 톤은 컷
        if '"' in s or '\n' in s:
            return None
        # Flash 제안 beat는 한국어 "다음 비트:" 프리픽스로 올 수 있음 → 둘 다 벗기고 영어로 통일(이중 프리픽스 방지)
        for _pfx in ("Next beat:", "다음 비트:"):
            if s.startswith(_pfx):
                s = s[len(_pfx):].strip()
        s = f"Next beat: {s}"
        return s

    @staticmethod
    def _merge_llm_beats(
        heuristic_beats: List[str],
        llm_beats: List[str],
        cap: int,
    ) -> List[str]:
        """
        휴리스틱 비트와 LLM 힌트 비트를 **weave** (교차 배치).
        - 휴리스틱이 head(결정론적 우선순위). LLM은 그 사이에 섞여 향미 보강.
        - 완전 동일 문자열 중복 제거.
        - LLM 비트가 없으면 휴리스틱 그대로 반환.
        """
        if not llm_beats:
            return heuristic_beats[:cap]

        woven: List[str] = []
        seen: set = set()

        def _push(b: str) -> None:
            if not b:
                return
            key = b.strip()
            if key in seen:
                return
            seen.add(key)
            woven.append(b)

        max_pairs = max(len(heuristic_beats), len(llm_beats))
        for i in range(max_pairs):
            if i < len(heuristic_beats):
                _push(heuristic_beats[i])
                if len(woven) >= cap:
                    break
            if i < len(llm_beats):
                _push(llm_beats[i])
                if len(woven) >= cap:
                    break

        return woven[:cap]

    @staticmethod
    def _generate_beats(
        threads: List[Dict[str, Any]],
        energy: str,
        is_idle: bool,
        anomaly_triggered: bool,
        emotion_summary: dict,
        suggested_beats: Optional[List[str]] = None,
        cap: int = 6,
        pacing: str = "hold",
        doom_value: int = 0
    ) -> List[str]:
        """
        Convert scored threads → natural-language beat directives (English state-form).
        LIBRA buildHeuristicPlan 상당. 순서 = priority 내림차순 (threads가 이미 정렬됨).

        SD-Bb3 (2026-04-22): suggested_beats 보강.
        - Theoria Flash가 observation 기반으로 제안한 한국어 Directive 비트 0~3개
        - 휴리스틱 비트와 weave 되어 향미 보강 (override 아님)
        - 존재하지 않거나 [] 이면 기존 휴리스틱 경로와 동일
        """
        beats: List[str] = []

        for t in threads:
            src = t.get("source", "")
            label = t.get("label", "")
            if not label:
                continue

            if src == "narrative_chain":
                # chain_status(개폐) → 전진(OPEN)/휴면(DORMANT). 극적 강도 → doom_value(챕터볼륨 起承轉結, 드라마 출처).
                urgency = t.get("urgency")
                cs = t.get("chain_status", "OPEN")
                if urgency == "stagnation":
                    beats.append(f"Next beat: the main plot '{label}' has stalled — one fresh external stimulus enters and moves it forward.")
                elif urgency == "convergence":
                    beats.append(f"Next beat: several threads converge on '{label}' — they cross in a single scene.")
                elif cs == "DORMANT":
                    beats.append(f"Next beat: a quiet reminder of '{label}' surfaces, not yet pressed.")
                else:
                    # OPEN — advance; 극적 동사는 doom(챕터볼륨 활성도)에서 읽음
                    _dk = "climax" if doom_value >= 75 else ("rising" if doom_value >= 45 else "intro")
                    beats.append(f"Next beat: '{label}' {StoryDirector._DOOM_BEAT_VERB[_dk]}.")

            elif src == "active_condition":
                # 강도는 조건의 실제 intensity → _INTENSITY_ADJ 직결 (priority 역산 프록시 제거, dict 부활).
                adj = StoryDirector._INTENSITY_ADJ.get(t.get("intensity", "Mid"), "vivid")
                polarity = t.get("polarity", "mixed")
                pol_hint = {"positive": ", leaned on,", "negative": ", bearing down,", "mixed": ", a double edge,"}.get(polarity, "")
                # O축: pacing이 push/pivot이면 dwell이 아니라 전진(슬롯16 pacing과 충돌 방지).
                if pacing in ("push", "pivot"):
                    beats.append(f"Next beat: the state '{label}'{pol_hint} advances in one motion.")
                else:
                    beats.append(f"Next beat: the state '{label}'{pol_hint} spreads through one moment as a {adj} sensation.")

            elif src == "memory":
                beats.append(f"Next beat: the past memory of '{label}' seeps into a gap in the present scene.")

            if len(beats) >= cap:
                break

        # 감정 스파이크 NPC가 있으면 하단에 보조 비트 추가 (중복 방지: 이미 threads에 NPC 관련 없으면)
        spike_npcs = [
            n for n, e in emotion_summary.items()
            if isinstance(e, dict) and (e.get("spike") or float(e.get("intensity", 0) or 0) > 0.7)
        ]
        if spike_npcs and len(beats) < cap:
            top_name = sorted(spike_npcs)[0]  # 결정적 tiebreak
            beats.append(f"Next beat: the emotional surge of '{top_name}' colors the texture of the scene.")

        # SD-Bb3: LLM 힌트 비트 정규화 + weave (휴리스틱 비트와 교차 배치)
        llm_beats: List[str] = []
        if suggested_beats:
            for raw in suggested_beats:
                nb = StoryDirector._normalize_llm_beat(raw)
                if nb:
                    llm_beats.append(nb)
        # cap-1 여유는 anomaly prepend를 위해 (anomaly 헤드 1칸 예약)
        _weave_cap = cap - 1 if anomaly_triggered else cap
        beats = StoryDirector._merge_llm_beats(beats, llm_beats, _weave_cap)

        # Anomaly가 막 터졌으면 최우선 비트 prepend
        if anomaly_triggered:
            beats.insert(0, "Next beat: the shockwave of the anomaly that just struck etches itself into the air of the scene.")
            beats = beats[:cap]

        # Idle 에너지 + 비트 없음 → ambient 진행 비트 보강
        if not beats:
            if energy in ("idle", "stagnant"):
                beats.append("Next beat: the surroundings (time / weather / NPC routine) move one breath forward, giving the scene air.")
            elif energy in ("detonation", "aftershock"):
                beats.append("Next beat: the aftershock of the recent upheaval lingers in the texture of the scene.")
            else:
                beats.append("Next beat: the fine axis of tension in the present scene tightens one degree.")

        return beats[:cap]
