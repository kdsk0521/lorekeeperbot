import json
import asyncio
import logging
from google.genai import types

async def analyze_context_nvc(client, model_id, history_text, lore, rules):
    """
    Gemini API를 사용하여 현재 대화 맥락을 분석하고,
    서사적 필요(Need)와 시스템 액션(Action)을 도출합니다.
    """
    system_instruction = (
        "[GM Internal Brain: NVC & Action Detection]\n\n"
        "You are the Game Master's analytical cortex. Analyze the narrative flow.\n\n"
        "### OUTPUT FORMAT (JSON ONLY)\n"
        "{\n"
        "  \"Observation\": \"Objective summary of the current situation\",\n"
        "  \"Feeling\": \"The emotional atmosphere (e.g., Tense, Hopeful)\",\n"
        "  \"Need\": \"What needs to happen next for the story?\",\n"
        "  \"SystemAction\": {\n"
        "      \"tool\": \"None\" | \"Memo\" | \"Quest\",\n"
        "      \"type\": \"Add\" | \"Remove\" | \"Complete\",\n"
        "      \"content\": \"Summary of the item to add/remove\"\n"
        "  }\n"
        "}\n\n"
        "### GUIDELINES FOR SystemAction\n"
        "1. **Memo**:\n"
        "   - Add: When a NEW generic clue, NPC name, or location is revealed.\n"
        "   - Remove: When a note is no longer relevant.\n"
        "2. **Quest**:\n"
        "   - Add: When a clear, major objective is given to the party.\n"
        "   - Complete: When the party fulfills an objective.\n"
        "3. **None**:\n"
        "   - Default state. Use this if no database update is needed.\n"
    )

    user_prompt = (
        f"### WORLD LORE\n{lore}\n\n"
        f"### GAME RULES\n{rules}\n\n"
        f"### RECENT HISTORY\n{history_text}\n\n"
        "Analyze the context and determine if a SystemAction is required."
    )

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
                logging.error(f"NVC 분석 실패: {e}")
                return {
                    "Observation": "Error",
                    "Feeling": "Neutral",
                    "Need": "Safety",
                    "SystemAction": {"tool": "None"}
                }
            await asyncio.sleep(delay)
    return None