"""
Lorekeeper UNE - Story Director (v1.0)
Code-driven narrative pacing & direction engine.
Fills gaps: proactive plot advancement, idle input handling, scene transition direction.

NOT a prompt generator — outputs structured hints consumed by the response builder.
Sits at Stage 4.5 (after Anomaly, before Doom).

Design: AnomalyModule 패턴 — 점수 테이블 + 코드 결정, LLM 호출 없음.

[2026-10-09 SD개편] 착지 비트 큐(SD-Ba/Bb, LIBRA buildHeuristicPlan 이식) 은퇴. 타이밍(언제 세계가 던지나)의 주인은
Storyteller(anomaly_module) 하나, 평소 턴의 "무엇이 오나"는 Mira(response·hook). 여기는 페이싱·긴장·전환·focus·
dice·무입력만 낸다. 근거: 파티쳇수정/analysis_line/스토리디렉팅_개편_스펙_2026-10-09.md
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



# [2026-10-09 SD개편] ambient 진행 비트(08-28 세계 전진 단일 공급원)는 Slot 33 착지 줄과 함께 은퇴했다.


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
        # [2026-08-11 리더 §7] 현행 READER_GM_FEED=1(라이브) — 구 주석의 "0(기본)이면 무동작"은
        # Stage 0 시절 기술. 끄려면 env로 0. 결정론 유지: enum 하나 → 분기 하나. spec §6b.
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
                narrative_chain, emotion_summary, doom_value,
                # [2026-08-11 리더 소비자] C4 — momentum 후보 조회용. ""면 기존 사다리 그대로.
                channel_id=(context.narrative_anchors or {}).get("channel_id", ""),
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

        # [2026-10-09 SD개편] 8. Beat Queue(SD-Ba3, 04-22) 은퇴 — 스레드 라벨 틀 비트·Mira 비트를 매 턴 Slot 33
        #   "This turn lands"로 꽂던 두 번째 타이밍 채널이었다(이변 안 터진 턴 170/170). 3. Plot thread 분석·아크 읽기도
        #   이 큐 전용이라 함께 걷었다. 정체·수렴은 Storyteller 승격(stagnation/convergence_promotion)이 쥔다.

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
            "[StoryDirector] pacing=%s tension=%s idle=%s focus=%s",
            pacing, tension_axis, is_idle, _focus_out.get("spotlight", "none"),
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
        doom_value: int,
        channel_id: str = ""
    ) -> Dict[str, Any]:
        """Generate proactive direction when input is idle/passive.

        [2026-08-11 리더 소비자] channel_id는 C4(독자 momentum) 후보 조회용 — 기본 ""=기존 동작."""
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

        # [2026-08-11 리더 소비자 → 당일 정정(레티어스 "렌더 직행 애매")] Priority 5 (폴백 직전):
        # 독자 momentum은 **선택자이지 텍스트 소스가 아니다.** 구판은 note 원문을 hint에 실었고
        # hint는 iceberg translate_idle_direction의 `what stirs:`로 **렌더 프롬프트에 그대로**
        # 떨어졌다 = 렌더 직행(독자가 저자 펜을 쥠). 정정: hint를 비워 iceberg의 고정 문장
        # ("the motion already read at the table carries forward")만 렌더에 가고, note 원문은
        # 서사 콜 블록(narrative_queries.reader_signal_block — 좌뇌 재해석 경유)이 나른다.
        # 거부권(L160~)과 같은 문법: 독자는 행동을 바꾸되 문장을 못 싣는다.
        try:
            import config as _cfg_rm
            _rm_cap = int(getattr(_cfg_rm, "READER_MOMENTUM_CAP", 120))
            if channel_id and _rm_cap > 0 and getattr(_cfg_rm, "READER_GM_FEED", 0):
                import sqlite_store as _ss_rm
                _rm_rows = _ss_rm.read_reader_log_tail(channel_id, limit=1)
                _rm_list = (_rm_rows[-1][1].get("momentum") or []) if _rm_rows else []
                _has_rm = any(isinstance(_it, dict) and str(_it.get("note", "") or "").strip()
                              for _it in _rm_list)
                if _has_rm:
                    direction["source"] = "reader_momentum"
                    direction["hint"] = ""  # 텍스트 금지 — 고정 문장은 iceberg _IDLE_SOURCE_KR 몫
                    logger.info("[ReaderMomentum] idle slot claimed (text withheld — selector only)")
                    return direction
        except Exception:
            pass

        # Default: world-driven ambient progression
        direction["source"] = "ambient"
        direction["hint"] = "world_progression"
        return direction

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
