"""
Lorekeeper UNE - Integrated Theoria Analyzer (좌뇌 분석 엔진)
인지 + 분석 통합: 상황 관찰, 의도 해석, Position/Effect, Psyche, Narrative Chain
"""

import logging
import json
from typing import Dict, Any, Optional

import config
import bot_utils
import text_resources
import analysis_resources
from orchestration_context import GameContext
from google.genai import types

# [SYSTEM NOTE] Flash tends to hallucinate in complex models. 
# We maintain the original complexity but wrap them in clear instructional tags.

logger = logging.getLogger("Theoria")

# =========================================================
# THEORIA SYSTEM PROMPTS (UNE 통합 분석 엔진)
# =========================================================


class TheoriaAnalyzer:
    """
    UNE 좌뇌 분석 엔진.
    인지 + 분석을 통합하여 GameContext를 풍부하게 채웁니다.
    """
    
    def __init__(self, client, model_id: str):
        self.client = client
        self.model_id = model_id

    async def analyze_input(self, context: GameContext) -> Dict[str, Any]:
        """전체 분석을 수행하고 결과를 반환합니다."""
        if not self.client:
            return {"error": "No client"}

        prompt = self._build_prompt(context)
        system_instruction = self._build_system_instruction()
        
        try:
            gen_config = types.GenerateContentConfig(
                system_instruction=system_instruction,
                response_mime_type="application/json",
                temperature=config.ANALYSIS_TEMPERATURE,
                top_k=config.ANALYSIS_TOP_K,
                top_p=config.ANALYSIS_TOP_P,
                presence_penalty=config.ANALYSIS_PRESENCE_PENALTY,
                frequency_penalty=config.ANALYSIS_FREQUENCY_PENALTY
            )
            
            response = await self.client.aio.models.generate_content(
                model=self.model_id,
                contents=[types.Content(role="user", parts=[types.Part(text=prompt)])],
                config=gen_config
            )

            if not response.text:
                return {"error": "Empty response"}

            return json.loads(bot_utils.clean_json_text(response.text))

        except Exception as e:
            logger.error(f"Theoria analysis failed: {e}")
            return {"error": str(e)}

    def _build_system_instruction(self) -> str:
        """Theoria 시스템 프롬프트 조립 (GMCognition Flash 호환성 보장)"""
        return "\n\n".join([
            text_resources.CONTENT_AUTHORIZATION_MANDATE,
            "<THEORIA role='Observer and Librarian'>",
            analysis_resources.THEORIA_IDENTITY,
            analysis_resources.THEORIA_PRINCIPLES,
            analysis_resources.THEORIA_PC_CHECK,
            analysis_resources.COGNITIVE_ARCHITECTURE_MODEL,
            analysis_resources.STATE_TRACKING_FORMAT,
            analysis_resources.TEMPORAL_ORIENTATION_PROTOCOL,
            analysis_resources.THEORIA_PROCESS,
            analysis_resources.THEORIA_INPUT_DECODING,
            analysis_resources.THEORIA_PSYCHE,
            analysis_resources.THEORIA_MEMORY,
            analysis_resources.THEORIA_CHAIN,
            analysis_resources.THEORIA_POSITION_EFFECT,
            analysis_resources.THEORIA_ASPECTS,
            analysis_resources.THEORIA_TEMPORAL,
            self._get_output_schema(),
            "</THEORIA>"
        ])

    def _get_output_schema(self) -> str:
        """출력 스키마 정의"""
        return """
<output_schema>
Return valid JSON with ALL these fields (Korean values where specified):

## REQUIRED FIELDS
- "InputAnalysis": {"Original": str, "Enhanced": str, "Plausibility": "High/Low/Impossible", "LogicTrace": [], "Momentum": "Open/Closed"}
- "Observation": str (Korean - what actually happened)
- "UserIntent": str (Korean - what user wants immediately)
- "CurrentLocation": str (Korean)
- "LocationRisk": "None/Low/Medium/High/Extreme"
- "TimeContext": str (Korean - time of day)
- "SceneType": "normal/combat/social/summary/intimate"

## STAKES & ENVIRONMENT
- "Position": {"value": 0.0-1.0, "reason": "Korean"}
- "Effect": {"value": 0.0-1.0, "reason": "Korean"}
- "Aspects": ["Korean aspect 1", "Korean aspect 2", ...]

## PSYCHOLOGICAL & NARRATIVE
- "psyche_states": {"CharName": {"mental": {...}, "soma": {...}, "relation": {...}}}
- "narrative_chain": {"topic_lock": str, "chain_status": "OPEN/CLOSED", "conclusion_proximity": 0-100}
- "memory_triggers": [{"trigger": str, "character": str, "echo": str}]

## JUDGMENT SUPPORT
- "needs_judgment": boolean
- "action_meta": {"action": "Korean", "difficulty": "easy/normal/hard/extreme"}
- "asset_evaluation": {
    "bonus": int (max 60),
    "penalty": int (max 40),
    "reason": "Korean",
    "modifications": [{"label": "Korean", "value": int}],
    "defense_success": boolean
}

## DLC SUPPORT
- "narrative_hook": str (Korean - twist for failure/partial)
- "time_flow": {"ticks": 1-20, "reason": "Korean"}
- "doom_relief": {"applicable": boolean, "amount": 0-20, "reason": "Korean"}
- "mental_impact": {"applicable": boolean, "delta": -35 to +20, "reason": "Korean"}
- "anomaly_profile": {"trigger": str, "category": str, "intensity": "Low/Mid/High/Extreme", "polarity": "positive/negative/mixed", "line": "Korean", "reason": "Korean"}

## COGNITIVE ENHANCEMENT (English Logic Blocks)
- "HabitusAnalysis": {
    "Economic": str (Brief English description of physical standing),
    "Cultural": str (Brief English description of linguistic/knowledge standing),
    "Social": str (Brief English description of perceived authority)
  }
- "SensoryAnchors": [
    {"anchor": "Physical sensation", "memory_link": "English description of related memory"}
  ]

## SAFETY & DEBUG
- "PCImpersonationCheck": {"detected": boolean, "violations": [], "correction_hint": str}
- "TemporalOrientation": {"focus": "past/present/future", "intensity": 0.0-1.0}
- "NPCAttitudes": {"NpcName": {"attitude": "hostile/unfriendly/neutral/friendly/devoted", "reason": "Korean"}}
- "RelevantContext": ["Quoted lore/rule 1", "Quote 2", ...]
</output_schema>
"""

    def _build_prompt(self, context: GameContext) -> str:
        """분석 프롬프트 생성"""
        req = context.request
        anchors = context.narrative_anchors
        bus = context.shared_bus
        
        return f"""## ANALYSIS REQUEST

### 1. USER INPUT
"{req.user_input}"

### 2. CURRENT STATE
- **Genre**: {req.genres}
- **Doom (World Tension)**: {bus.doom.get('value', 0)}
- **Mental (PC Mental Health)**: {bus.mental.get('value', 100)}

### 3. PLAYER ASSETS (Narrative Anchors)
- **Appearance**: {anchors.get('appearance', 'N/A')}
- **Personality**: {anchors.get('personality', 'N/A')}
- **Background**: {anchors.get('background', 'N/A')}
- **Passives**: {anchors.get('passives', [])}
- **Inventory**: {anchors.get('inventory', [])}
- **Relations**: {anchors.get('relations', [])}
- **Memos**: {anchors.get('memos', [])}

### 4. WORLD CONTEXT
- **Core Theme**: {req.lore_summary.get('theme', 'General TRPG')}
- **Anomaly Seeds**: {', '.join(req.lore_summary.get('anomaly_seeds', [])) or 'None'}
- **Major Locations**: {req.lore_summary.get('locations', 'Current surroundings')}

### 5. RECENT HISTORY
{req.history_text or '[No history]'}

### 6. LORE REFERENCE
{req.lore_text[:2000] if req.lore_text else '[No lore loaded]'}

---
Perform FULL Theoria analysis and return JSON with ALL required fields.
"""
