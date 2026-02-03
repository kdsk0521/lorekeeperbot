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
        
        prompt = f"""당신은 TRPG 엔진의 '통합 분석기(Theoria)'입니다. 
사용자의 입력 의도와 플레이어 캐릭터의 자산을 종합적으로 분석하여 시스템 처리를 위한 데이터를 생성하세요.

## 1. 현재 상황 및 맥락
- **유저 입력**: {req.user_input}
- **장르 설정**: {req.genres}
- **현재 긴장도(Doom)**: {context.shared_bus.doom.get('value', 0)}

## 2. 플레이어 자산 (Narrative Anchors)
- **특성(Passives)**: {anchors.get('passives', [])}
- **소지품(Inventory)**: {anchors.get('inventory', [])}
- **관계(Relations)**: {anchors.get('relations', {})}

## 3. 분석 및 출력 지침 (JSON)
- **intent**: 유저의 핵심 의도 요약.
- **needs_judgment**: 판정(Dice Roll)이 필요한 도전적인 행동인가? (true/false)
- **action_meta**: 판정 필요시 {{ "action": "단어", "difficulty": "easy/normal/hard..." }}
- **asset_evaluation**: 
    - **bonus**: 자산이 주는 보너스 합계 (0~40)
    - **penalty**: 상황적 페널티 합계 (0~40)
    - **reason**: 보정치 부여 근거 (1문장)
    - **modifications**: [ {{ "label": "항목명", "value": 숫자 }}, ... ] (개별 보정치 상세 내역)
    - **defense_success**: 만약 현재 상황이 '위협/이변'에 대응하는 것이라면, 자산으로 이를 완벽히 방어 가능한지 (true/false)
- **narrative_hook**: 판정 실패나 부분 성공 시 발생할 수 있는 '잠재적 위기' 또는 '서사적 반전' 제안 (1문장)
- **time_flow**: {{ "ticks": 숫자(1~20), "reason": "근거" }}

## 4. 출력 예시
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
  "time_flow": {{ "ticks": 3, "reason": "조심스러운 이동" }}
}}"""
        return prompt
