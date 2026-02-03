"""
Lorekeeper UNE - Integrated Theoria Analyzer (Call 1)
Combines Intent Analysis and Asset Evaluation into a single API call for efficiency.
"""

import logging
import json
from typing import Dict, Any, Optional

import bot_utils
from orchestration_context import GameContext
from google.genai import types

logger = logging.getLogger("Theoria")

class TheoriaAnalyzer:
    def __init__(self, client, model_id: str):
        self.client = client
        self.model_id = model_id

    async def analyze_input(self, context: GameContext) -> Dict[str, Any]:
        """사용자 입력과 자산을 동시에 분석하여 결과를 반환합니다."""
        if not self.client:
            return {"needs_judgment": False}

        prompt = self._build_prompt(context)
        
        try:
            gen_config = types.GenerateContentConfig(
                response_mime_type="application/json",
                temperature=0.1
            )
            
            response = await self.client.aio.models.generate_content(
                model=self.model_id,
                contents=[types.Content(role="user", parts=[types.Part(text=prompt)])],
                config=gen_config
            )

            if not response.text:
                return {"needs_judgment": False}

            return json.loads(bot_utils.clean_json_text(response.text))

        except Exception as e:
            logger.error(f"Integrated Theoria analysis failed: {e}")
            return {"needs_judgment": False, "error": str(e)}

    def _build_prompt(self, context: GameContext) -> str:
        req = context.request
        anchors = context.narrative_anchors
        
        prompt = f"""You are the 'Integrated Analyzer (Theoria)' of the TRPG engine.
Analyze the user's input intent and the player character's assets comprehensively to generate data for system processing.

## 1. Current Situation & Context
- **User Input**: {req.user_input}
- **Genre Settings**: {req.genres}
- **Current World Tension (Doom)**: {context.shared_bus.doom.get('value', 0)}

## 2. Player Assets (Narrative Anchors)
- **Passives**: {anchors.get('passives', [])}
- **Inventory**: {anchors.get('inventory', [])}
- **Relations**: {anchors.get('relations', {})}

## 3. World Context & Lore
- **Core Theme**: {req.lore_summary.get('theme', 'General TRPG World')}
- **Anomaly Seeds**: {', '.join(req.lore_summary.get('anomaly_seeds', [])) or 'Unknown'}
- **Major Locations**: {req.lore_summary.get('locations', 'Current surroundings')}

## 4. Analysis & Output Guidelines (JSON)
**IMPORTANT: All string values in the JSON output must be in KOREAN.**

- **intent**: Summary of the user's core intent (Korean).
- **needs_judgment**: Is this a challenging action requiring a Dice Roll? (true/false)
- **action_meta**: If judgment is needed, {{ "action": "korean_word", "difficulty": "easy/normal/hard/extreme" }}
- **asset_evaluation**: 
    - **bonus**: Total bonus (Passives/Items: Max 40, Situational: Max 20).
    - **penalty**: Total penalty (Passives/Items: Max 20, Situational: Max 20).
    - **reason**: Rationale for modifiers (1 sentence in Korean, including compliance with caps).
    - **modifications**: [ {{ "label": "itemName_Korean", "value": number }}, ... ] (Detailed breakdown)
    - **defense_success**: If countering a threat/anomaly, can assets perfectly block it? (true/false)
- **narrative_hook**: Suggested 'potential crisis' or 'twist' for failure/partial success (1 sentence in Korean).
- **time_flow**: {{ "ticks": number(1-20), "reason": "reason_Korean" }}
- **doom_relief**: Analyze if the action provides relief from tension/stress.
    - **applicable**: Is this a calming/restorative action? (true/false)
    - **amount**: Suggested Doom reduction (0-20). Consider: rest/meditation (10-15), healing items (5-10), safe haven (15-20), brief respite (3-5)
    - **reason**: Why this action reduces tension (1 sentence in Korean)
- **mental_impact**: Analyze if the action affects mental health. **IMPORTANT**: Consider player's passives, items, and companion NPCs.
    - **applicable**: Does this action impact mental state? (true/false)
    - **delta**: Mental health change (-35 to +20). Negative = damage, Positive = recovery. Consider: 
        - Base impact: witnessing horror (-15~-25), mild fear (-5~-10), comfort/healing (+5~+15), triumph (+10~+20)
        - **Passives**: Mental-related traits can amplify or reduce impact (e.g., "Brave" reduces fear, "Fragile" amplifies shock)
        - **Items**: Calming items, protective charms, or comfort objects can mitigate negative impact
        - **Companion NPCs**: Trusted allies nearby can reduce fear (-3~-5), isolation amplifies it (+3~+5)
    - **reason**: Why this affects mental health, mentioning relevant modifiers if any (1 sentence in Korean)

## 5. Output Example (Values must be Korean)
{{
  "intent": "경비병 몰래 뒤로 돌아가 기습 시도",
  "needs_judgment": true,
  "action_meta": {{ "action": "은신", "difficulty": "hard" }},
  "asset_evaluation": {{
    "bonus": 15,
    "penalty": 5,
    "reason": "'어둠 속의 암살자' 특성이 보너스를 주지만, 갑옷의 소음으로 페널티 발생",
    "modifications": [
      {{ "label": "어둠 속의 암살자", "value": 15 }},
      {{ "label": "갑옷의 소음", "value": -5 }}
    ],
    "defense_success": false
  }},
  "narrative_hook": "잠입에는 성공했으나, 도중에 떨어뜨린 동전 소리에 경비병이 의구심을 품고 다가옵니다.",
  "time_flow": {{ "ticks": 3, "reason": "조심스러운 이동" }},
  "doom_relief": {{ "applicable": false, "amount": 0, "reason": "긴장된 상황" }},
  "mental_impact": {{ "applicable": false, "delta": 0, "reason": "정신적 영향 없음" }}
}}"""
        return prompt
