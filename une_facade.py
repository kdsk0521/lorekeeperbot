"""
Lorekeeper - Universal Narrative Engine (UNE) Facade
The main entry point for the UNE engine.
"""

import logging
from typing import Dict, Any

from orchestration_context import GameContext
from waterfall_pipeline import WaterfallPipeline
import domain_manager

logger = logging.getLogger("UNE")

class UniversalNarrativeEngine:
    def __init__(self, client, model_id: str):
        self.pipeline = WaterfallPipeline(client, model_id)

    async def run(self, channel_id: str, user_id: str, user_input: str) -> Dict[str, Any]:
        """
        엔진을 실행하여 서사적 결과물(Directive)을 반환합니다.
        
        Args:
            channel_id: 디스코드 채널 ID
            user_id: 유저 ID
            user_input: 사용자 입력 텍스트
            
        Returns:
            Dict: {
                "game_context": object,
                "directive": str,      # LLM 지시문
                "system_message": str  # 유저에게 보여줄 시스템 로그 (판정 결과 등)
            }
        """
        # 1. Convert legacy data to GameContext
        context_dict = domain_manager.convert_to_game_context(channel_id, user_id, user_input)
        context = GameContext.from_dict(context_dict)
        
        # 2. Execute Waterfall Pipeline
        updated_context = await self.pipeline.execute(context)
        
        # 3. Sync result back to legacy storage
        domain_manager.sync_from_game_context(channel_id, user_id, updated_context.to_dict())
        
        # 4. Generate Directive for Final LLM
        bus = updated_context.shared_bus
        directive_parts = []
        
        # Judgment result in directive
        if bus.judgment and bus.judgment.get("active"):
            reason_txt = f" (근거: {bus.judgment.get('reason')})" if bus.judgment.get('reason') else ""
            directive_parts.append(f"[판정 결과]: {bus.judgment.get('result')} ({bus.judgment.get('roll')}){reason_txt}")
        
        # Anomaly outcome in directive
        if bus.anomaly and bus.anomaly.get("triggered"):
            directive_parts.append(f"[이변 활성화]: {bus.anomaly.get('tag')} - {bus.anomaly.get('intensity')} 강도")
            
        # Fallbacks
        fallback_msg = self.pipeline.get_fallback_directives(context.request.active_modules)
        if fallback_msg:
            directive_parts.append(f"\n[모듈 제약 지침]:\n{fallback_msg}")

        # System message for UI
        system_msg = ""
        if bus.judgment and bus.judgment.get("active"):
            system_msg += bus.judgment.get("output", "")
        if bus.anomaly and bus.anomaly.get("triggered"):
            system_msg += f"\n⚡ **이변 발생: [{bus.anomaly.get('tag')}]**"
        if bus.doom and bus.doom.get("relief_log"):
            system_msg += f"\n{bus.doom.get('relief_log')}"
        if bus.doom and bus.doom.get("mental_pressure_log"):
            system_msg += f"\n{bus.doom.get('mental_pressure_log')}"
        
        # Integrate Mental impact and log into one line
        if bus.mental:
            mental_parts = []
            if bus.mental.get("impact_log"):
                impact_log = bus.mental.get("impact_log")
                # Extract reason from impact_log: "🧠 정신적 영향: -10 (reason)"
                if "(" in impact_log and ")" in impact_log:
                    reason = impact_log.split("(", 1)[1].rsplit(")", 1)[0]
                    mental_parts.append(reason)
            
            if bus.mental.get("log"):
                mental_parts.append(bus.mental.get("log"))
            
            if mental_parts:
                system_msg += f"\n{' → '.join(mental_parts)}"

        return {
            "game_context": updated_context,
            "directive": "\n".join(directive_parts),
            "system_message": system_msg
        }
