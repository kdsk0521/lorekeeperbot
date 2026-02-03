"""
Lorekeeper UNE - Waterfall Pipeline
Orchestrates the sequence of narrative analysis and mechanical updates.
"""

import logging
from typing import Dict, Any, List
from orchestration_context import GameContext
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

    async def execute(self, context: GameContext) -> GameContext:
        """Analysis -> Judgment -> Doom -> Anomaly -> Mental"""
        
        active_modules = context.request.active_modules
        
        # 1. Call 1: Analysis (Theoria) - Always Execute
        analysis = await self.theoria.analyze_input(context)
        bus = context.shared_bus
        bus.judgment["active"] = analysis.get("needs_judgment", False)
        if bus.judgment["active"]:
            bus.judgment["meta"] = analysis.get("action_meta", {})
            eval_data = analysis.get("asset_evaluation", {})
            bus.judgment["eval"] = eval_data
            bus.judgment["modifications"] = eval_data.get("modifications", [])
            bus.judgment["narrative_hook"] = analysis.get("narrative_hook", "")
        
        # Store Doom Relief data for Doom Module
        doom_relief = analysis.get("doom_relief", {})
        if doom_relief.get("applicable", False):
            bus.doom["relief"] = doom_relief
        
        # Store Mental Impact data for Mental Module
        mental_impact = analysis.get("mental_impact", {})
        if mental_impact.get("applicable", False):
            bus.mental["impact"] = mental_impact

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

        # 4. Anomaly Trigger (Conditional)
        if "anomaly" in active_modules and context.shared_bus.anomaly.get("potential"):
            from anomaly_module import AnomalyModule
            self.anomaly = AnomalyModule(self.theoria.client, self.theoria.model_id)
            context = await self.anomaly.process(context)

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
