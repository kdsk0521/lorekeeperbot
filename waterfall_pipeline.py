"""
Lorekeeper UNE - Waterfall Pipeline
Orchestrates the sequence of narrative analysis and mechanical updates.
"""

import logging
import random
from typing import Dict, Any
from orchestration_context import GameContext, SharedBus
from theoria_analyzer import TheoriaAnalyzer
from vigor_composure_module import VigorComposureModule
from judgment_engine import JudgmentEngine
from anomaly_module import AnomalyModule
from doom_module import DoomModule
from judgment_gate import gate_judgment
from emotion_engine import EmotionEngine
from story_director import StoryDirector
import domain_manager

logger = logging.getLogger("Waterfall")


# =========================================================
# P6: 인과 구속 힌트 (Flash에 능력 범위 주입)
# =========================================================
def _inject_capability_hints(npc_profiles: dict) -> str:
    """NPC별 능력 범위 요약을 Flash 프롬프트에 주입.
    하드블록 아님. Flash가 참조할 수 있는 힌트만."""
    hints = []
    for name, profile in npc_profiles.items():
        if not isinstance(profile, dict):
            continue
        caps = profile.get("capabilities", {})
        static_traits = profile.get("static_traits", {})

        parts = []
        if caps:
            strengths = caps.get("strengths", "?")
            limits = caps.get("limits", "?")
            parts.append(f"can={strengths} | cannot={limits}")
        if static_traits:
            coping = static_traits.get("coping_style", "")
            moral = static_traits.get("moral_stance", "")
            if coping or moral:
                parts.append(f"coping={coping}, moral={moral}")

        if parts:
            hints.append(f"[{name}] {' | '.join(parts)}")

    return "\n".join(hints) if hints else ""


def _degrade_stage(bus, stage_name: str, error: Exception) -> None:
    """W5: Record pipeline degradation and apply fallback from config table."""
    try:
        import config as _cfg
        rule = _cfg.PIPELINE_DEGRADATION.get(stage_name, {})
        behavior = rule.get("absent_behavior", "skip")
        logger.warning("[Degradation] %s → %s: %s", stage_name, behavior, error)
        degraded = bus.dai.setdefault("_degraded_stages", [])
        degraded.append({"stage": stage_name, "behavior": behavior, "error": str(error)[:200]})
        # Apply fallback DAI values if present
        for k, v in rule.get("fallback_dai", {}).items():
            if k not in bus.dai or not bus.dai[k]:
                bus.dai[k] = v
    except Exception as inner:
        logger.error("[Degradation] Handler itself failed for %s: %s", stage_name, inner)


class WaterfallPipeline:
    def __init__(self, client, model_id: str):
        self.theoria = TheoriaAnalyzer(client, model_id)
        # These will be lazy loaded or injected
        self.judgment = None
        self.doom = None
        self.anomaly = None
        self.vigor_composure = None

    def _ensure_bus_schema(self, bus: SharedBus) -> None:
        """
        Ensure SharedBus has required dict fields without overwriting existing values.
        This is a schema guard for DLC on/off during runtime.
        """
        defaults = SharedBus()
        for key in ("dai", "judgment", "doom", "anomaly", "vigor", "composure", "emotion"):
            current = getattr(bus, key, None)
            if current is None or not isinstance(current, dict):
                setattr(bus, key, getattr(defaults, key))

    async def execute(self, context: GameContext) -> GameContext:
        """Analysis -> Mental(pre) -> Judgment -> Storyteller -> Doom -> Mental(sync)

        All core modules (Doom, Anomaly, Mental, Judgment) are always active.
        Judgment trigger is gated by judgment_gate (N1).

        Data-flow map (SharedBus ownership):
        - Theoria: bus.dai (all analysis), bus.judgment, bus.doom, bus.vigor/composure.impact
        - Mental(pre): stage snapshot only (no delta consumption)
        - Judgment: resolves action, writes consequences (doom.delta, primary axis delta, clock effects, momentum)
        - Storyteller: bus.anomaly.triggered/tag/decision (narrative only, no deltas)
        - Doom: bus.doom.value/delta/log, consumes judgment doom_delta, may write vigor/composure pressure delta
        - Mental(sync): consumes ALL accumulated deltas (judgment + doom pressure + rest + status)
        """

        self._ensure_bus_schema(context.shared_bus)
        
        # 1. [A안 v2 2026-07-02 직렬] 추출 콜(냉, 기계 필드) → 서사 콜(온, 방향+심리해석 필드).
        # 병렬→직렬 전환(레티어스 "지연 감수"): 서사 콜이 이번 턴 추출 다이제스트를 입력으로 받아
        # 동턴 정합 확보 — 2차 이사(deep_read 등 심리 해석층)의 전제. W5 강하는 콜별 독립 유지.
        bus = context.shared_bus
        try:
            analysis = await self.theoria.analyze_input(context)
        except Exception as e:
            _degrade_stage(bus, "theoria_analysis", e)
            analysis = {}

        # Safety: Gemini가 JSON 배열을 반환하면 첫 번째 요소를 사용
        if isinstance(analysis, list):
            logger.warning(f"[Theoria] Returned list instead of dict, extracting first element")
            analysis = analysis[0] if analysis and isinstance(analysis[0], dict) else {}
        if not isinstance(analysis, dict):
            logger.error(f"[Theoria] Invalid response type: {type(analysis)}")
            analysis = {}

        try:
            narrative = await self.theoria.analyze_narrative(context, extract=analysis)
        except Exception as e:
            _degrade_stage(bus, "narrative_analysis", e)
            narrative = {}
        if not isinstance(narrative, dict):
            narrative = {}

        # [A안] 서사 콜 결과 합류 — 서사 필드는 narrative 콜이 소유 (추출 스키마에서 제거됨).
        # 서사 콜 실패 시 키 부재 → 아래 전개가 기존 디폴트({}/[]/None) 적용 = 현행 강하와 동일 동작.
        if narrative:
            _merge_keys = ("narrative_chain", "suggested_beats", "narrative_hook",
                           "offscreen_trace", "scene_register", "trait_connections")
            for _nk in _merge_keys:
                _nv = narrative.get(_nk)
                if _nv is not None:
                    analysis[_nk] = _nv

            # [2차 이사] psyche_narrative → psyche_states per-NPC 병합
            # (deep_read/resurfacing=톱레벨, value_conflict=relation 내부 — 하류 소비 형태 그대로)
            _pn = narrative.get("psyche_narrative")
            _ps = analysis.get("psyche_states")
            if isinstance(_pn, dict) and isinstance(_ps, dict) and _ps:
                def _match_npc(name: str):
                    if name in _ps:
                        return name
                    _b = name.split("(")[0].strip().lower()
                    for _k in _ps:
                        _kb = _k.split("(")[0].strip().lower()
                        if _kb == _b or _b in _k.lower() or _kb in name.lower():
                            return _k
                    return None
                for _pn_name, _pn_blk in _pn.items():
                    if not isinstance(_pn_blk, dict):
                        continue
                    _tgt = _match_npc(str(_pn_name))
                    if not _tgt or not isinstance(_ps.get(_tgt), dict):
                        continue
                    if _pn_blk.get("deep_read"):
                        _ps[_tgt]["deep_read"] = _pn_blk["deep_read"]
                    if _pn_blk.get("resurfacing") is not None:
                        _ps[_tgt]["resurfacing"] = _pn_blk["resurfacing"]
                    if _pn_blk.get("value_conflict") is not None:
                        _rel = _ps[_tgt].setdefault("relation", {})
                        if isinstance(_rel, dict):
                            _rel["value_conflict"] = _pn_blk["value_conflict"]

            logger.info("[Narrative] merged: "
                        + ", ".join(k for k in _merge_keys if narrative.get(k) is not None)
                        + (f" + psyche_narrative({len(_pn)})" if isinstance(_pn, dict) and _pn else ""))

        # Store ALL Theoria results in SharedBus.dai (replaces nvc_result)
        bus.dai["input_analysis"] = analysis.get("InputAnalysis", {})
        bus.dai["observation"] = analysis.get("Observation", "")
        bus.dai["user_intent"] = analysis.get("UserIntent", "")
        bus.dai["current_location"] = analysis.get("CurrentLocation", "")
        bus.dai["location_risk"] = analysis.get("LocationRisk", "Low")
        bus.dai["time_context"] = analysis.get("TimeContext", "")
        bus.dai["scene_type"] = analysis.get("SceneType", "normal")
        bus.dai["energy_direction"] = analysis.get("EnergyDirection", "idle")  # 2026-06-25: 디폴트 rising→idle. omission/불확실 시 긴장을 만들지 않음(둠·톤·페이싱 안전쪽). Theoria가 보통 채움.
        bus.dai["quality_flags"] = analysis.get("QualityFlags", {})
        bus.dai["position"] = analysis.get("Position", {})
        bus.dai["effect"] = analysis.get("Effect", {})
        bus.dai["aspects"] = analysis.get("Aspects", [])
        bus.dai["psyche_states"] = analysis.get("psyche_states", {})
        bus.dai["narrative_chain"] = analysis.get("narrative_chain", {})
        # SD-Bb2 (2026-04-22): Theoria author-hint beats (휴리스틱 비트 보강용, 필수 아님)
        _sb_raw = analysis.get("suggested_beats", [])
        if isinstance(_sb_raw, list):
            bus.dai["suggested_beats"] = [str(b).strip() for b in _sb_raw if isinstance(b, str) and b.strip()]
        else:
            bus.dai["suggested_beats"] = []
        # [2026-07-02 Offscreen Motion — 뮈토스 이식] 부재 캐스트 흔적 (dict or null, null이 상례)
        _ot_raw = analysis.get("offscreen_trace")
        bus.dai["offscreen_trace"] = _ot_raw if isinstance(_ot_raw, dict) else None
        bus.dai["scene_register"] = analysis.get("scene_register")
        bus.dai["input_mode"] = analysis.get("input_mode", "decree")
        bus.dai["memory_triggers"] = analysis.get("memory_triggers", [])
        bus.dai["narrative_hook"] = analysis.get("narrative_hook", "")
        bus.dai["time_flow"] = analysis.get("TimeFlow", analysis.get("time_flow", {}))
        bus.dai["doom_clocks"] = analysis.get("doom_clocks", {})
        # doom_relief 제거 (2026-05-23) — legacy 위기진폭 잔재
        bus.dai["mental_impact"] = analysis.get("mental_impact", {})
        bus.dai["anomaly_profile"] = analysis.get("anomaly_profile", {})
        bus.dai["pc_autonomy_check"] = analysis.get("PCAutonomyCheck", {})
        bus.dai["temporal_orientation"] = analysis.get("TemporalOrientation", {})
        bus.dai["npc_attitudes"] = analysis.get("NPCAttitudes", {})
        bus.dai["npc_knowledge"] = analysis.get("NPCKnowledge", {})
        bus.dai["sensory_anchors"] = analysis.get("SensoryAnchors", [])
        bus.dai["habitus_analysis"] = analysis.get("HabitusAnalysis", {})
        bus.dai["intimacy_analysis"] = analysis.get("IntimacyAnalysis")
        bus.dai["relevant_context"] = analysis.get("RelevantContext", [])
        bus.dai["relevant_npcs"] = analysis.get("RelevantNPCs", [])
        bus.dai["relevant_chunks"] = analysis.get("relevant_chunks", [])
        bus.dai["needs_judgment"] = analysis.get("needs_judgment", False)
        bus.dai["action_meta"] = analysis.get("action_meta", {})
        bus.dai["asset_evaluation"] = analysis.get("asset_evaluation", {})
        bus.dai["flashback_eval"] = analysis.get("flashback_eval")
        bus.dai["rest_eval"] = analysis.get("rest_eval")
        bus.dai["item_usage"] = analysis.get("item_usage")
        # [2026-06-11 소비자 감사 #2~4] 운송 누락 복구 — Theoria 스키마에 실재(=Flash가 매 턴 생산)
        # 했으나 매핑이 빠져 슬롯 번역기 3종(trait_connections/spatial_inscription/continuity_check)이
        # 영구 빈손이었음 (dai_consumer_audit.md). 번역기들은 빈값 관용이라 연결만으로 안전.
        bus.dai["trait_connections"] = analysis.get("trait_connections", {})
        bus.dai["spatial_read"] = analysis.get("spatial_read", {})
        bus.dai["continuity_check"] = analysis.get("continuity_check")

        # [2026-06-11 소비자 감사 #6] 죽은 저장 제거 — capability hints는 이제 anchors 경유로
        # Theoria *입력*에 배달됨 (une_facade에서 계산, theoria_analyzer 로스터 옆 렌더 — 원설계).
        # 기존 이 자리 코드는 Flash 콜 후 저장 + 독자 0 + npc_roster가 str이라 isinstance(dict)
        # 가드에 막혀 사실상 한 번도 실행 안 됨 (이중 사망 확인).

        # N1: Judgment Gate — Flash의 needs_judgment를 코드 게이트로 검증
        raw_needs = analysis.get("needs_judgment", False)
        resolve = (analysis.get("action_meta") or {}).get("resolve", "none")
        last_j_turn = bus.judgment.get("last_judgment_turn", -10)
        current_turn = (context.narrative_anchors or {}).get(
            "session_memory", {}
        ).get("turn_count", 0) or domain_manager.get_world_state(
            (context.narrative_anchors or {}).get("channel_id", "")
        ).get("turn_index", 0)

        # [2026-06-11 소비자 감사 #1] turn_index 배선 — bus.dai["turn_index"]를 아무도 안 실어
        # 항상 0이었음 → doom 시계 fade 7개 읽기 + une_facade 퀘스트 stale archive가 0 기반 동작
        # (staleness 트리거 사망). 게이트 계산용 current_turn을 그대로 적재.
        # 주의: 부활 첫 턴에 묵은 퀘스트 일괄 archive는 정상 동작.
        bus.dai["turn_index"] = current_turn

        final_needs, gate_reason = gate_judgment(
            user_input=context.request.user_input,
            flash_needs_judgment=raw_needs,
            last_judgment_turn=last_j_turn,
            current_turn=current_turn,
            resolve=resolve,
        )

        bus.judgment["active"] = final_needs
        bus.judgment["gate_reason"] = gate_reason
        if final_needs:
            bus.judgment["last_judgment_turn"] = current_turn
            bus.judgment["meta"] = analysis.get("action_meta", {})
            eval_data = analysis.get("asset_evaluation", {})
            bus.judgment["eval"] = eval_data
            bus.judgment["modifications"] = eval_data.get("modifications", [])
            bus.judgment["narrative_hook"] = analysis.get("narrative_hook", "")

        # [V10] DAI 스냅샷 롤링 보존 — bus.dai 완성 직후, 코드만(콜 0)·실패 무해.
        # 용도: ①관측 — 필드 비대/모델 JSON 버릇을 실데이터로 ②Sprint 4 동적 NPC 원재료
        # (턴별 심리·사회 이력 질의). 읽기: sqlite_store.read_dai_logs(channel_id, n).
        try:
            import sqlite_store
            _dai_ch = (context.narrative_anchors or {}).get("channel_id", "")
            if _dai_ch:
                sqlite_store.append_dai_log(_dai_ch, current_turn, bus.dai)
        except Exception as _e_dai:
            logger.debug(f"[V10] dai log skipped: {_e_dai}")
        
        # Doom Clocks v3 연동 (clock_updates, clock_new, clock_resolved)
        # relief 제거 (2026-05-23) — legacy 위기진폭 잔재. 둠은 서사 진행도라 평화 장면 자동 감소는 의미 충돌.
        doom_clocks_output = analysis.get("doom_clocks") or {}
        if isinstance(doom_clocks_output, dict):
            bus.doom["flash_clock_updates"] = doom_clocks_output.get("clock_updates", [])
            bus.doom["flash_clock_new"] = doom_clocks_output.get("clock_new")
            bus.doom["flash_clock_resolved"] = doom_clocks_output.get("clock_resolved", [])

        # Vigor/Composure Impact 연동
        mental_impact = analysis.get("mental_impact") or {}
        if mental_impact.get("applicable", False):
            reason = mental_impact.get("reason", "")
            # Phase 2 F: severity enum 형식 우선 (none/mild/heavy/extreme)
            if "vigor_severity" in mental_impact or "composure_severity" in mental_impact:
                v_sev = mental_impact.get("vigor_severity", "none")
                c_sev = mental_impact.get("composure_severity", "none")
                bus.vigor["impact"] = {"applicable": True, "severity": v_sev, "reason": reason}
                bus.composure["impact"] = {"applicable": True, "severity": c_sev, "reason": reason}
            # 레거시 호환: 직접 delta 수치 (v3 schema)
            elif "vigor_delta" in mental_impact or "composure_delta" in mental_impact:
                v_delta = int(mental_impact.get("vigor_delta", 0) or 0)
                c_delta = int(mental_impact.get("composure_delta", 0) or 0)
                bus.vigor["impact"] = {"applicable": True, "delta": v_delta, "reason": reason}
                bus.composure["impact"] = {"applicable": True, "delta": c_delta, "reason": reason}
            else:
                # Legacy fallback: single delta -> route to primary axis
                mechanic = context.request.genres.get("mechanic", {})
                primary = mechanic.get("primary_resource") or "vigor"
                getattr(bus, primary)["impact"] = mental_impact

        # Anomaly Profile 연동
        anomaly_profile = analysis.get("anomaly_profile") or {}
        if isinstance(anomaly_profile, dict):
            tag = anomaly_profile.get("trigger") or ""
            category = anomaly_profile.get("category") or ""
            intensity = anomaly_profile.get("intensity") or ""
            polarity = anomaly_profile.get("polarity") or ""
            line = anomaly_profile.get("line") or ""
            reason = anomaly_profile.get("reason") or ""

            if tag:
                bus.anomaly["tag"] = tag
            if category:
                bus.anomaly["category"] = category
            if intensity:
                bus.anomaly["intensity"] = intensity
            if polarity:
                bus.anomaly["polarity"] = polarity
            if line:
                bus.anomaly["line"] = line
            if reason:
                bus.anomaly["reason"] = reason

        # Condition resolved (from Flash analysis)
        cond_resolved = analysis.get("condition_resolved")
        if isinstance(cond_resolved, list) and cond_resolved:
            bus.anomaly["condition_resolved"] = cond_resolved

        # Condition updates — severity transition (from Flash analysis)
        cond_updates = analysis.get("condition_updates")
        if isinstance(cond_updates, list) and cond_updates:
            bus.anomaly["condition_updates"] = cond_updates

        # Event location override (from Flash anomaly_profile)
        if isinstance(anomaly_profile, dict):
            event_location = (anomaly_profile.get("location") or "").strip()
            if event_location:
                bus.anomaly["location"] = event_location

        # M3 제거 (2026-07-02): "chain CLOSED → anomaly 강제" 컷.
        # ① 키 드리프트('status' → 'chain_status')로 장기간 사망 상태였고 부재가 관측된 적 없음.
        # ② 현 자세와 충돌 — quiet resolution 허용(Scheherazade 완화) + 페이싱은 doom 起承轉結/storyteller 결정이 담당.
        # 부활 시: bus.dai["narrative_chain"].get("chain_status") == "CLOSED" 게이트로 재작성할 것.

        # Fallback: if no anomaly tag was proposed, pick from lore seeds
        if not bus.anomaly.get("tag"):
            seeds = context.request.lore_summary.get("anomaly_seeds", [])
            if isinstance(seeds, list) and seeds:
                seed = random.choice(seeds)
                if isinstance(seed, dict):
                    bus.anomaly["tag"] = seed.get("name", "기이한 현상")
                    if not bus.anomaly.get("category"):
                        bus.anomaly["category"] = seed.get("name", "")
                else:
                    bus.anomaly["tag"] = str(seed)

        # Normalize defaults for downstream use
        if bus.anomaly.get("tag") and not bus.anomaly.get("category"):
            bus.anomaly["category"] = bus.anomaly.get("tag")
        if not bus.anomaly.get("intensity"):
            bus.anomaly["intensity"] = "Mid"
        if not bus.anomaly.get("polarity"):
            bus.anomaly["polarity"] = "mixed"
        # Fallback line for seed-based anomalies (Theoria didn't generate one)
        if bus.anomaly.get("tag") and not bus.anomaly.get("line"):
            bus.anomaly["line"] = f"{bus.anomaly['tag']}의 기운이 감돈다."

        # 1.5 Emotion Engine: psyche_states → normalized emotion tracking
        channel_id = (context.narrative_anchors or {}).get("channel_id", "")
        current_turn = 0
        if channel_id:
            current_turn = domain_manager.get_world_state(channel_id).get("turn_index", 0)

        try:
            psyche_states = bus.dai.get("psyche_states", {})
            # [2026-06-12] PC 혼입 차단 (4호) — Theoria가 psyche_states에 PC를 포함시키는데
            # 감정엔진과 하류 3기관(스토리디렉터 focus/NPC자율 집단게이트/iceberg Slot 14·16)은
            # 전부 NPC 전용. PC가 흘러들면: 디렉터가 PC를 연출 대상으로(focus=도만 관측됨),
            # 집단 게이트 인원 수 부풀림, PC 내면 힌트가 Pro에 주입(사칭 압력). PC=카메라 원칙.
            _pc_masks_em = {
                p.get("mask") for p in (context.narrative_anchors or {}).get("all_pcs", {}).values()
                if isinstance(p, dict) and p.get("mask")
            }
            if psyche_states and _pc_masks_em:
                _removed = [n for n in psyche_states if n in _pc_masks_em]
                if _removed:
                    psyche_states = {k: v for k, v in psyche_states.items() if k not in _pc_masks_em}
                    logger.debug(f"[EmotionEngine] PC 제외: {', '.join(_removed)}")
            if psyche_states:
                prev_emotions = {}
                if channel_id:
                    prev_emotions = domain_manager.get_world_state(channel_id).get(
                        "npc_emotion_states", {}
                    )
                # P2: scene 컨텍스트와 memory_triggers 전달 (_derive_relational Tier 4/6/7 입력)
                _narrative_chain = bus.dai.get("narrative_chain", {}) or {}
                _scene_ctx = {
                    "register": bus.dai.get("scene_register"),
                    "silence_type": _narrative_chain.get("silence_type"),
                }
                emotion_results = EmotionEngine.process_turn(
                    psyche_states=psyche_states,
                    previous_emotions=prev_emotions,
                    current_turn=current_turn,
                    npc_attitudes=bus.dai.get("npc_attitudes", {}),
                    scene_ctx=_scene_ctx,
                    memory_triggers=bus.dai.get("memory_triggers", []),
                )
                bus.emotion = EmotionEngine.to_bus_dict(emotion_results)
                # 6.2 (2026-05-20): slot_manager fast-path 배선. slot_manager가
                # `dai._emotion_states_for_slot`를 우선 보고 있었으나 어디서도 set
                # 안 해 항상 world_state round-trip으로 폴백했음. 같은 턴 내에서는
                # 이 라이브 dict가 가장 신선하므로 직접 주입한다.
                # 값 타입: Dict[str, EmotionState] — slot_manager가 그대로 소비.
                bus.dai["_emotion_states_for_slot"] = emotion_results
                if channel_id and emotion_results:
                    world = domain_manager.get_world_state(channel_id)
                    world["npc_emotion_states"] = {
                        name: state.to_dict()
                        for name, state in emotion_results.items()
                    }
                    domain_manager.update_world_state(channel_id, world)
                    # [V10 적립] emotion_log 적립 — bus.emotion 완성 직후, 코드만(콜0)·실패 무해.
                    # 턴별 per-NPC 감정 스냅샷 → 궤적/스파이크 질의(독자: sqlite_store.read_emotion_*).
                    try:
                        import sqlite_store
                        sqlite_store.append_emotion_log(channel_id, current_turn, bus.emotion)
                    except Exception as _e_emolog:
                        logger.debug(f"[V10] emotion log skipped: {_e_emolog}")
                spikes = [
                    f"{n}({s.spike_detail})"
                    for n, s in emotion_results.items()
                    if s.spike_detected
                ]
                if spikes:
                    logger.info(f"[EmotionEngine] Spikes: {', '.join(spikes)}")
        except Exception as e:
            _degrade_stage(bus, "emotion_engine", e)

        # 2. Mental Pre-pass: annotate current stage for downstream modules.
        try:
            self.vigor_composure = VigorComposureModule()
            context = await self.vigor_composure.prime(context)
        except Exception as e:
            _degrade_stage(bus, "vigor_composure", e)

        # 3. Judgment (gated by Flash needs_judgment + judgment_gate)
        if bus.judgment["active"]:
            try:
                self.judgment = JudgmentEngine(self.theoria.client, self.theoria.model_id)
                context = await self.judgment.process(context)
            except Exception as e:
                bus.judgment["active"] = False
                _degrade_stage(bus, "judgment_engine", e)

        # 4. Storyteller: inject state + set potential
        bus.anomaly["potential"] = True
        channel_id = (context.narrative_anchors or {}).get("channel_id", "")
        if channel_id:
            st_state = domain_manager.get_storyteller_state(channel_id)
            bus.anomaly["_storyteller_state"] = st_state
            bus.anomaly["_current_turn"] = domain_manager.get_world_state(channel_id).get("turn_index", 0)
            bus.anomaly["_channel_id"] = channel_id

        # 5. Storyteller Decision
        if bus.anomaly.get("potential"):
            try:
                self.anomaly = AnomalyModule(self.theoria.client, self.theoria.model_id)
                context = await self.anomaly.process(context)
            except Exception as e:
                bus.anomaly["triggered"] = False
                _degrade_stage(bus, "anomaly_module", e)

        # 5.5 Story Director: pacing, plot hints, idle handling, transition mood
        try:
            context = StoryDirector.process(context)
        except Exception as e:
            _degrade_stage(bus, "story_director", e)

        # 5.6 Seven Dice persistence → DiceEngine가 자체 처리 (dice_engine.py)

        # 6. Doom Update — consumes judgment doom_delta naturally
        try:
            self.doom = DoomModule()
            context = await self.doom.process(context)
        except Exception as e:
            _degrade_stage(bus, "doom_module", e)

        # 7. Vigor/Composure Sync (LAST — consumes all accumulated deltas)
        try:
            self.vigor_composure = VigorComposureModule()
            context = await self.vigor_composure.process(context)
        except Exception as e:
            _degrade_stage(bus, "vigor_composure", e)

        # ===== Pipeline Summary Log =====
        self._log_pipeline_summary(bus)

        # [V10 적립] turn_snapshot — 파이프라인 말미, 모든 신호 최종. 콜0·append-only·실패무해.
        try:
            import sqlite_store
            _ts_ch = (context.narrative_anchors or {}).get("channel_id", "")
            if _ts_ch:
                _sd = bus.dai.get("story_direction", {}) if isinstance(bus.dai, dict) else {}
                _snap = {
                    "doom_value": bus.doom.get("value"),
                    "doom_phase": bus.doom.get("chapter_phase", ""),
                    "vigor": bus.vigor.get("value"),
                    "vigor_delta": bus.vigor.get("delta_applied", 0) or 0,
                    "composure": bus.composure.get("value"),
                    "composure_delta": bus.composure.get("delta_applied", 0) or 0,
                    "sd_pacing": _sd.get("pacing", ""),
                    "sd_tension": _sd.get("tension_axis", ""),
                    "sd_focus": (_sd.get("focus") or {}).get("spotlight", ""),
                    "sd_beat": bool(_sd.get("next_beat")),
                    "sd_idle": bool(_sd.get("is_idle_input")),
                    "judgment_active": bool(bus.judgment.get("active")),
                    "anomaly_triggered": bool(bus.anomaly.get("triggered")),
                }
                sqlite_store.append_turn_snapshot(_ts_ch, current_turn, _snap)
        except Exception as _e_ts:
            logger.debug(f"[V10] turn_snapshot skipped: {_e_ts}")

        return context

    def _log_pipeline_summary(self, bus: SharedBus) -> None:
        """파이프라인 실행 결과 한눈에 볼 수 있는 요약 로그."""
        parts = ["[Pipeline Summary] (all modules always active)"]

        # Judgment
        if bus.judgment.get("active"):
            j = bus.judgment
            result = j.get("result", "N/A")
            roll = j.get("final_roll", "?")
            dc = j.get("dc", "?")
            gate = j.get("gate_reason", "")
            parts.append(f"  Judgment: {result} (roll={roll} vs DC={dc}) [{gate}]")
        else:
            parts.append(f"  Judgment: skipped (no action requiring roll)")

        # Doom
        doom_val = bus.doom.get("value", "?")
        doom_log = bus.doom.get("log", "")
        parts.append(f"  Doom: {doom_val}/100 — {doom_log[:100]}" if doom_log else f"  Doom: {doom_val}/100 (no change)")

        # Storyteller
        anomaly_triggered = bus.anomaly.get("triggered", False)
        if anomaly_triggered:
            a_tag = bus.anomaly.get("tag", "?")
            a_int = bus.anomaly.get("intensity", "?")
            a_dec = bus.anomaly.get("decision", "?")
            a_reason = bus.anomaly.get("decision_reason", "")
            parts.append(f"  Storyteller: ACT [{a_tag}] intensity={a_int} ({a_dec}: {a_reason})")
        else:
            a_dec = bus.anomaly.get("decision", "skip")
            a_reason = bus.anomaly.get("decision_reason", "")
            parts.append(f"  Storyteller: {a_dec} ({a_reason})" if a_reason else f"  Storyteller: {a_dec}")

        # Vigor / Composure
        v_val = bus.vigor.get("value", "?")
        c_val = bus.composure.get("value", "?")
        v_delta = bus.vigor.get("delta_applied", 0) or 0
        c_delta = bus.composure.get("delta_applied", 0) or 0
        v_sign = f"+{v_delta}" if v_delta > 0 else str(v_delta)
        c_sign = f"+{c_delta}" if c_delta > 0 else str(c_delta)
        parts.append(f"  Vigor: {v_val} ({v_sign}) | Composure: {c_val} ({c_sign})")

        # Story Director (SD-A4 새 스키마 — plot_hints/transition.mood 제거, focus.spotlight/next_beat 사용)
        sd = bus.dai.get("story_direction", {})
        if sd.get("active"):
            sd_pacing = sd.get("pacing", "?")
            sd_tension = sd.get("tension_axis", "?")
            sd_idle = sd.get("is_idle_input", False)
            sd_focus = (sd.get("focus") or {}).get("spotlight", "none")
            sd_cut = (sd.get("transition") or {}).get("cut", "?")
            sd_beat = "Y" if sd.get("next_beat") else "N"
            parts.append(
                f"  StoryDir: pacing={sd_pacing} tension={sd_tension} idle={sd_idle} "
                f"focus={sd_focus} cut={sd_cut} beat={sd_beat}"
            )
            # Seven Dice
            sd_dice = sd.get("dice", {})
            if sd_dice:
                parts.append(f"  Dice: {sd_dice.get('name','?')} (state={sd_dice.get('scene_state','?')}, visible={sd_dice.get('visible',False)})")
        else:
            parts.append("  StoryDir: inactive")

        # Degradation
        _deg = bus.dai.get("_degraded_stages", [])
        if _deg:
            _deg_str = ", ".join(d.get("stage", "?") for d in _deg if isinstance(d, dict))
            parts.append(f"  ⚠ Degraded: {_deg_str}")

        # Emotion — pair 스키마 v2: 'dominant' → 'base' + 'modifier' (to_bus_dict summary)
        emotion_data = bus.emotion
        if emotion_data.get("active"):
            summaries = emotion_data.get("summary", {})
            emo_parts = []
            for n, s in summaries.items():
                if s.get("intensity", 0) <= 0.05:
                    continue
                _base = s.get("base", "?") or "?"
                _mod = s.get("modifier", "")
                _pair = f"{_base}×{_mod}" if _mod else _base
                emo_parts.append(
                    f"{n}={_pair}({s.get('intensity',0):.1f})"
                    + (" ⚡" if s.get("spike") else "")
                )
            if emo_parts:
                parts.append(f"  Emotion: {', '.join(emo_parts)}")
        else:
            parts.append("  Emotion: inactive")

        logger.info("\n".join(parts))
