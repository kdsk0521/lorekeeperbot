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
        self.mental = None

    def _ensure_bus_schema(self, bus: SharedBus) -> None:
        """
        Ensure SharedBus has required dict fields without overwriting existing values.
        This is a schema guard for DLC on/off during runtime.
        """
        defaults = SharedBus()
        for key in ("dai", "judgment", "doom", "anomaly", "mental"):
            current = getattr(bus, key, None)
            if current is None or not isinstance(current, dict):
                setattr(bus, key, getattr(defaults, key))

    def _apply_off_fallbacks(self, active_modules: List[str], bus: SharedBus) -> None:
        """
        When a DLC is OFF, apply agreed fallback values without marking it active.
        - Doom OFF  -> value 30
        - Mental OFF -> value 50
        """
        if "doom" not in active_modules:
            bus.doom["value"] = 30
            bus.doom["active"] = False
        if "mental" not in active_modules:
            bus.mental["value"] = 50
            bus.mental["active"] = False

    async def execute(self, context: GameContext) -> GameContext:
        """Analysis -> Judgment -> Doom -> Anomaly -> Mental

        Data-flow map (SharedBus ownership):
        - Theoria: bus.judgment.meta/eval/modifications/narrative_hook, bus.doom.relief, bus.mental.impact
        - Judgment: bus.judgment.result/roll/output/reason
        - Doom: bus.doom.value/delta/log (+ bus.mental.delta via pressure/recovery)
        - Anomaly: bus.anomaly.triggered/tag/intensity
        - Mental: bus.mental.value/log/trauma_trigger
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
        bus.dai["doom_relief"] = analysis.get("doom_relief", {})
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
        bus.dai["needs_judgment"] = analysis.get("needs_judgment", False)
        bus.dai["action_meta"] = analysis.get("action_meta", {})
        bus.dai["asset_evaluation"] = analysis.get("asset_evaluation", {})
        
        # Judgment 연동
        bus.judgment["active"] = analysis.get("needs_judgment", False)
        if bus.judgment["active"]:
            bus.judgment["meta"] = analysis.get("action_meta", {})
            eval_data = analysis.get("asset_evaluation", {})
            bus.judgment["eval"] = eval_data
            bus.judgment["modifications"] = eval_data.get("modifications", [])
            bus.judgment["narrative_hook"] = analysis.get("narrative_hook", "")
        
        # Doom Relief 연동
        doom_relief = analysis.get("doom_relief", {})
        if doom_relief.get("applicable", False):
            bus.doom["relief"] = doom_relief
        
        # Mental Impact 연동
        mental_impact = analysis.get("mental_impact", {})
        if mental_impact.get("applicable", False):
            bus.mental["impact"] = mental_impact

        # Anomaly Profile 연동
        anomaly_profile = analysis.get("anomaly_profile", {})
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

        # Fallback: if no anomaly tag was proposed, pick from lore seeds
        if not bus.anomaly.get("tag"):
            seeds = context.request.lore_summary.get("anomaly_seeds", [])
            if isinstance(seeds, list) and seeds:
                bus.anomaly["tag"] = random.choice(seeds)

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

        # 2. Call 2: Judgment (Conditional)
        if "judgment" in active_modules and context.shared_bus.judgment["active"]:
            from judgment_engine import JudgmentEngine
            self.judgment = JudgmentEngine(self.theoria.client, self.theoria.model_id)
            context = await self.judgment.process(context)

        # 3. Doom Update (Conditional)
        if "doom" in active_modules:
            from doom_module import DoomModule
            self.doom = DoomModule()
            context = await self.doom.process(context)

        # If Doom is OFF, still allow anomalies to roll with a fixed chance.
        if "anomaly" in active_modules and "doom" not in active_modules:
            bus.anomaly["potential"] = True

        # 4. Anomaly Trigger (Conditional)
        if "anomaly" in active_modules and context.shared_bus.anomaly.get("potential"):
            from anomaly_module import AnomalyModule
            self.anomaly = AnomalyModule(self.theoria.client, self.theoria.model_id)
            context = await self.anomaly.process(context)

        # 4a. Post-Anomaly Doom Sync (Anomaly writes doom.delta for inspiration/shock)
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

        # 5. Mental Sync (Conditional)
        if "mental" in active_modules:
            from mental_module import MentalModule
            self.mental = MentalModule()
            context = await self.mental.process(context)

        return context

    def get_fallback_directives(self, active_modules: List[str]) -> str:
        """Returns constraint directives for inactive modules."""
        directives = []
        if "judgment" not in active_modules:
            directives.append("- [Mechanical Restriction]: Do not mention dice rolls or skill checks.")
        if "doom" not in active_modules:
            directives.append("- [Narrative Restriction]: Avoid mentions of increasing tension or doom.")
        return "".join(directives)
