"""
Lorekeeper UNE - Waterfall Pipeline
Orchestrates the sequence of narrative analysis and mechanical updates.
"""

import logging
import random
from typing import Dict, Any, List
from orchestration_context import GameContext, SharedBus
from theoria_analyzer import TheoriaAnalyzer

logger = logging.getLogger("Waterfall")

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
        for key in ("dai", "judgment", "doom", "anomaly", "vigor", "composure"):
            current = getattr(bus, key, None)
            if current is None or not isinstance(current, dict):
                setattr(bus, key, getattr(defaults, key))

    def _apply_off_fallbacks(self, active_modules: List[str], bus: SharedBus) -> None:
        """
        When a DLC is OFF, apply agreed fallback values without marking it active.
        - Doom OFF  -> value 30
        - Mental OFF -> vigor 50, composure 50
        """
        if "doom" not in active_modules:
            bus.doom["value"] = 30
            bus.doom["active"] = False
        if "mental" not in active_modules:
            bus.vigor["value"] = 50
            bus.vigor["active"] = False
            bus.composure["value"] = 50
            bus.composure["active"] = False

    async def execute(self, context: GameContext) -> GameContext:
        """Analysis -> Mental(pre) -> Anomaly -> Doom -> Mental(sync) -> Judgment

        Data-flow map (SharedBus ownership):
        - Theoria: bus.judgment.meta/eval/modifications/narrative_hook, bus.doom.relief, bus.vigor/composure.impact
        - Mental(pre): stage snapshot only (no delta consumption)
        - Anomaly: bus.anomaly.triggered/tag/intensity, writes vigor/composure delta
        - Doom: bus.doom.value/delta/log, may write vigor/composure pressure delta
        - Mental(sync): consumes accumulated deltas and syncs axis values
        - Judgment(last): resolves action from updated state
        """
        
        active_modules = context.request.active_modules
        self._ensure_bus_schema(context.shared_bus)
        self._apply_off_fallbacks(active_modules, context.shared_bus)
        
        # 1. Call 1: Analysis (Theoria) - Always Execute
        analysis = await self.theoria.analyze_input(context)
        bus = context.shared_bus

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
        bus.dai["memory_triggers"] = analysis.get("memory_triggers", [])
        bus.dai["narrative_hook"] = analysis.get("narrative_hook", "")
        bus.dai["time_flow"] = analysis.get("TimeFlow", analysis.get("time_flow", {}))
        bus.dai["doom_clocks"] = analysis.get("doom_clocks", {})
        bus.dai["doom_relief"] = analysis.get("doom_relief", {})  # legacy fallback
        bus.dai["mental_impact"] = analysis.get("mental_impact", {})
        bus.dai["anomaly_profile"] = analysis.get("anomaly_profile", {})
        bus.dai["pc_impersonation_check"] = analysis.get("PCImpersonationCheck", {})
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

        # Judgment 연동
        bus.judgment["active"] = analysis.get("needs_judgment", False)
        if bus.judgment["active"]:
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
            # v3 schema: explicit 2-axis deltas
            if "vigor_delta" in mental_impact or "composure_delta" in mental_impact:
                v_delta = int(mental_impact.get("vigor_delta", 0) or 0)
                c_delta = int(mental_impact.get("composure_delta", 0) or 0)
                bus.vigor["impact"] = {"applicable": True, "delta": v_delta, "reason": mental_impact.get("reason", "")}
                bus.composure["impact"] = {"applicable": True, "delta": c_delta, "reason": mental_impact.get("reason", "")}
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

            # protective_item removed (Phase 4-1b): anomaly_module reads inventory modifiers directly

            # adaptation_group from Flash (2-level taxonomy)
            adapt_groups = anomaly_profile.get("adaptation_group")
            if isinstance(adapt_groups, list) and adapt_groups:
                bus.anomaly["adaptation_group"] = adapt_groups

        # Fallback: if no anomaly tag was proposed, pick from lore seeds
        if not bus.anomaly.get("tag"):
            seeds = context.request.lore_summary.get("anomaly_seeds", [])
            if isinstance(seeds, list) and seeds:
                seed = random.choice(seeds)
                if isinstance(seed, dict):
                    bus.anomaly["tag"] = seed.get("name", "기이한 현상")
                    if seed.get("adaptation_group"):
                        bus.anomaly.setdefault("adaptation_group", seed["adaptation_group"])
                    if not bus.anomaly.get("category"):
                        bus.anomaly["category"] = seed.get("name", "")
                    if seed.get("axis"):
                        bus.anomaly.setdefault("disruption_axis", seed["axis"])
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

        # 2. Mental Pre-pass (Conditional): annotate current stage for downstream modules.
        if "mental" in active_modules:
            from vigor_composure_module import VigorComposureModule
            self.vigor_composure = VigorComposureModule()
            context = await self.vigor_composure.prime(context)

        # 3. Anomaly Potential (Conditional)
        if "anomaly" in active_modules:
            bus.anomaly["potential"] = True

        # 4. Anomaly Trigger (Conditional)
        if "anomaly" in active_modules and context.shared_bus.anomaly.get("potential"):
            from anomaly_module import AnomalyModule
            self.anomaly = AnomalyModule(self.theoria.client, self.theoria.model_id)
            context = await self.anomaly.process(context)

        # 4a. Post-Anomaly Doom Sync (reserved hook: anomaly may write doom delta)
        post_delta = bus.doom.get("delta", 0)
        if post_delta != 0:
            old_val = bus.doom.get("value", 0)
            bus.doom["value"] = max(0, min(100, old_val + post_delta))
            bus.doom["delta"] = 0
            bus.doom["active"] = True
            sign = f"+{post_delta}" if post_delta > 0 else str(post_delta)
            existing_log = bus.doom.get("log", "")
            if existing_log:
                bus.doom["log"] = existing_log + f" (이변 {sign})"
            else:
                bus.doom["log"] = f"📈 긴장도 변동 (이변 {sign})" if post_delta > 0 else f"📉 긴장도 변동 (이변 {sign})"

        # 5. Doom Update (Conditional)
        if "doom" in active_modules:
            from doom_module import DoomModule
            self.doom = DoomModule()
            context = await self.doom.process(context)

        # 6. Vigor/Composure Sync (Conditional)
        if "mental" in active_modules:
            from vigor_composure_module import VigorComposureModule
            self.vigor_composure = VigorComposureModule()
            context = await self.vigor_composure.process(context)

        # 7. Call 2: Judgment (Conditional, LAST)
        if "judgment" in active_modules and context.shared_bus.judgment["active"]:
            from judgment_engine import JudgmentEngine
            self.judgment = JudgmentEngine(self.theoria.client, self.theoria.model_id)
            context = await self.judgment.process(context)

            # 7a. Judgment → Doom post-sync (같은 턴 반영)
            j_doom = bus.judgment.get("doom_delta", 0)
            if j_doom != 0 and "doom" in active_modules:
                old_val = bus.doom.get("value", 0)
                bus.doom["value"] = max(0, min(100, old_val + j_doom))
                bus.doom["active"] = True

        # ===== Pipeline Summary Log =====
        self._log_pipeline_summary(bus, active_modules)

        return context

    def _log_pipeline_summary(self, bus: SharedBus, active_modules: List[str]) -> None:
        """파이프라인 실행 결과 한눈에 볼 수 있는 요약 로그."""
        parts = ["[Pipeline Summary]"]
        parts.append(f"  Modules ON: {', '.join(active_modules)}")

        # Judgment
        if bus.judgment.get("active"):
            j = bus.judgment
            result = j.get("result", "N/A")
            roll = j.get("final_roll", "?")
            dc = j.get("dc", "?")
            parts.append(f"  Judgment: {result} (roll={roll} vs DC={dc})")
        else:
            parts.append(f"  Judgment: skipped (no action requiring roll)")

        # Doom
        doom_val = bus.doom.get("value", "?")
        doom_log = bus.doom.get("log", "")
        doom_active = bus.doom.get("active", False)
        if doom_active:
            parts.append(f"  Doom: {doom_val}/100 — {doom_log[:100]}" if doom_log else f"  Doom: {doom_val}/100")
        else:
            parts.append(f"  Doom: OFF (fallback={doom_val})")

        # Anomaly
        anomaly_triggered = bus.anomaly.get("triggered", False)
        if anomaly_triggered:
            a_tag = bus.anomaly.get("tag", "?")
            a_int = bus.anomaly.get("intensity", "?")
            a_def = bus.anomaly.get("defense_result", "?")
            parts.append(f"  Anomaly: TRIGGERED [{a_tag}] intensity={a_int} defense={a_def}")
        else:
            parts.append(f"  Anomaly: not triggered")

        # Vigor / Composure
        v_val = bus.vigor.get("value", "?")
        v_active = bus.vigor.get("active", False)
        c_val = bus.composure.get("value", "?")
        c_active = bus.composure.get("active", False)
        if v_active or c_active:
            v_delta = bus.vigor.get("delta_applied", 0) or 0
            c_delta = bus.composure.get("delta_applied", 0) or 0
            v_sign = f"+{v_delta}" if v_delta > 0 else str(v_delta)
            c_sign = f"+{c_delta}" if c_delta > 0 else str(c_delta)
            parts.append(f"  Vigor: {v_val} ({v_sign}) | Composure: {c_val} ({c_sign})")
        else:
            parts.append(f"  Vigor/Composure: OFF (fallback={v_val}/{c_val})")

        logger.info("\n".join(parts))

    def get_fallback_directives(self, active_modules: List[str]) -> str:
        """Returns constraint directives for inactive modules (genre-aware)."""
        directives = []
        if "judgment" not in active_modules:
            directives.append("- [Mechanical Restriction]: Do not mention dice rolls or skill checks.")
        if "doom" not in active_modules:
            directives.append("- [Narrative Restriction]: Avoid mentions of increasing tension or doom clock.")
        if "mental" not in active_modules:
            directives.append("- [State Restriction]: Do not explicitly reference vigor/composure values. Use Flash polyvagal cues for physical/emotional tone instead.")
        return "\n".join(directives)
