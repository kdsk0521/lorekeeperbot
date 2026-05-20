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
        
        # 1. Call 1: Analysis (Theoria) - Always Execute
        bus = context.shared_bus
        try:
            analysis = await self.theoria.analyze_input(context)
        except Exception as e:
            analysis = {}
            _degrade_stage(bus, "theoria_analysis", e)

        # Safety: Gemini가 JSON 배열을 반환하면 첫 번째 요소를 사용
        if isinstance(analysis, list):
            logger.warning(f"[Theoria] Returned list instead of dict, extracting first element")
            analysis = analysis[0] if analysis and isinstance(analysis[0], dict) else {}
        if not isinstance(analysis, dict):
            logger.error(f"[Theoria] Invalid response type: {type(analysis)}")
            analysis = {}

        # Store ALL Theoria results in SharedBus.dai (replaces nvc_result)
        bus.dai["active"] = True
        bus.dai["input_analysis"] = analysis.get("InputAnalysis", {})
        bus.dai["observation"] = analysis.get("Observation", "")
        bus.dai["user_intent"] = analysis.get("UserIntent", "")
        bus.dai["current_location"] = analysis.get("CurrentLocation", "")
        bus.dai["location_risk"] = analysis.get("LocationRisk", "Low")
        bus.dai["time_context"] = analysis.get("TimeContext", "")
        bus.dai["scene_type"] = analysis.get("SceneType", "normal")
        bus.dai["energy_direction"] = analysis.get("EnergyDirection", "rising")
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
        bus.dai["scene_register"] = analysis.get("scene_register")
        bus.dai["input_mode"] = analysis.get("input_mode", "decree")
        bus.dai["memory_triggers"] = analysis.get("memory_triggers", [])
        bus.dai["narrative_hook"] = analysis.get("narrative_hook", "")
        bus.dai["time_flow"] = analysis.get("TimeFlow", analysis.get("time_flow", {}))
        bus.dai["doom_clocks"] = analysis.get("doom_clocks", {})
        bus.dai["doom_relief"] = analysis.get("doom_relief", {})  # legacy fallback
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

        # P6: Inject capability hints for Theoria reference
        _npc_roster = (context.narrative_anchors or {}).get("npc_roster", {})
        if _npc_roster and isinstance(_npc_roster, dict):
            cap_hints = _inject_capability_hints(_npc_roster)
            if cap_hints:
                bus.dai["capability_hints"] = cap_hints

        # N1: Judgment Gate — Flash의 needs_judgment를 코드 게이트로 검증
        raw_needs = analysis.get("needs_judgment", False)
        resolve = (analysis.get("action_meta") or {}).get("resolve", "none")
        last_j_turn = bus.judgment.get("last_judgment_turn", -10)
        current_turn = (context.narrative_anchors or {}).get(
            "session_memory", {}
        ).get("turn_count", 0) or domain_manager.get_world_state(
            (context.narrative_anchors or {}).get("channel_id", "")
        ).get("turn_index", 0)

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
        
        # Doom Clocks v3 연동 (clock_updates, clock_new, clock_resolved, relief)
        doom_clocks_output = analysis.get("doom_clocks") or {}
        if isinstance(doom_clocks_output, dict):
            bus.doom["flash_clock_updates"] = doom_clocks_output.get("clock_updates", [])
            bus.doom["flash_clock_new"] = doom_clocks_output.get("clock_new")
            bus.doom["flash_clock_resolved"] = doom_clocks_output.get("clock_resolved", [])
            # Relief: doom_clocks.relief 우선, 없으면 legacy doom_relief fallback
            relief = doom_clocks_output.get("relief") or analysis.get("doom_relief") or {}
            if relief.get("applicable", False):
                bus.doom["relief"] = relief
        else:
            # Legacy fallback: doom_relief 직접 사용
            doom_relief = analysis.get("doom_relief") or {}
            if doom_relief.get("applicable", False):
                bus.doom["relief"] = doom_relief

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

        # M3: Chain CLOSED → force anomaly trigger
        _nc = bus.dai.get('narrative_chain', {})
        chain_status = _nc.get('status', 'OPEN') if isinstance(_nc, dict) else 'OPEN'
        if chain_status == 'CLOSED' and not bus.anomaly.get('triggered'):
            bus.anomaly['triggered'] = True
            bus.anomaly['tag'] = bus.anomaly.get('tag') or 'chain_closure'
            bus.anomaly['decision_reason'] = 'Narrative chain reached CLOSED state'
            logger.info("[M3] Chain CLOSED → anomaly auto-triggered")

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
