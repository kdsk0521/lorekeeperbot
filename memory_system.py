import json
import asyncio
import logging
from google.genai import types

async def analyze_context_nvc(client, model_id, history_text, lore, rules):
    """
    Gemini API를 사용하여 현재 대화 맥락을 NVC(Observation, Feeling, Need) 프레임워크로 분석합니다.
    제공된 NVC_ANALYSIS_PROMPT를 기반으로 시스템 액션을 감지하고 제안합니다.
    """
    
    # [제공된 프롬프트 반영] GM의 내부 사고 및 액션 감지 로직
    system_instruction = (
        "[GM Internal Brain: NVC & Action Detection]\n\n"
        "You are analyzing the narrative to manage game state and NPC logic.\n\n"
        "1. Observation: What is the current narrative situation?\n"
        "2. Feeling: Your emotional tone as a GM.\n"
        "3. Need: What narrative goal are you pursuing?\n"
        "4. SystemAction: Detect and trigger game mechanics.\n"
        "   - None: No mechanic.\n"
        "   - MemoAction: AUTOMATICALLY manage the Memo Pad.\n"
        "     * Add: If a new important clue, NPC name, or task is discovered.\n"
        "     * Remove: If a task is COMPLETED, a clue is solved, or a note is no longer relevant.\n"
        "     * Format: `MemoAction | Type: <Add/Remove> | Content: <Text>`\n"
        "   - QuestAction: Manage major story goals.\n"
        "     * Format: `QuestAction | Type: <Add/Complete> | Content: <Text>`\n"
        "   - StatusAction: Add/Remove status effects like 'Injured', 'Exhausted'.\n"
        "     * Format: `StatusAction | Type: <Add/Remove> | Effect: <Name>`\n\n"
        "IMPORTANT: Your response must be a valid JSON object with keys: "
        "\"Observation\", \"Feeling\", \"Need\", \"SystemAction\"."
    )

    user_prompt = (
        f"### WORLD LORE\n{lore}\n\n"
        f"### GAME RULES\n{rules}\n\n"
        f"### RECENT HISTORY\n{history_text}\n\n"
        "Analyze the context above and provide the NVC analysis in JSON format."
    )

    # 지수 백오프 기반 재시도 로직 (최대 5회)
    for i in range(5):
        try:
            response = client.models.generate_content(
                model=model_id,
                contents=[types.Content(role="user", parts=[types.Part(text=user_prompt)])],
                config=types.GenerateContentConfig(
                    system_instruction=system_instruction,
                    response_mime_type="application/json",
                )
            )
            
            result_text = response.candidates[0].content.parts[0].text
            return json.loads(result_text)
            
        except Exception as e:
            delay = 2 ** i
            if i == 4:
                logging.error(f"NVC 분석 실패 (최종): {e}")
                # 분석 실패 시 기본값 반환 (키 구조 유지)
                return {
                    "Observation": "Error during analysis.",
                    "Feeling": "Neutral",
                    "Need": "Maintain stability",
                    "SystemAction": "None"
                }
            await asyncio.sleep(delay)

    return None