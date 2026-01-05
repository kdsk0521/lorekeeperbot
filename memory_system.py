import json
import asyncio
import logging
from google.genai import types

async def analyze_context_nvc(client, model_id, history_text, lore, rules):
    """
    [수정] 대화 맥락을 분석하고, 메모/퀘스트/NPC 등의 시스템 액션을 도출합니다.
    """
    system_instruction = (
        "[GM Internal Brain: NVC & Action Detection]\n\n"
        "You are the Game Master's analytical cortex. Analyze the narrative flow.\n\n"
        "### OUTPUT FORMAT (JSON ONLY)\n"
        "{\n"
        "  \"Observation\": \"Objective summary of the current situation\",\n"
        "  \"CurrentLocation\": \"Inferred location (e.g., 'East Forest').\",\n"
        "  \"LocationRisk\": \"None | Low | Medium | High\",\n"
        "  \"InteractionType\": \"Dialogue | Action | Observation | Ignore/Silence\",\n"
        "  \"Feeling\": \"The emotional atmosphere (e.g., Tense, Hopeful)\",\n"
        "  \"Need\": \"What needs to happen next for the story?\",\n"
        "  \"SystemAction\": {\n"
        "      \"tool\": \"None\" | \"Memo\" | \"Quest\" | \"NPC\",\n"
        "      \"type\": \"Add\" | \"Remove\" | \"Complete\" | \"Archive\",\n"
        "      \"content\": \"Summary of the item to add/remove\"\n"
        "  }\n"
        "}\n\n"
        "### GUIDELINES FOR SystemAction\n"
        "1. **Memo**:\n"
        "   - **Add**: New clues, locations, or important facts.\n"
        "   - **Remove**: Information that is false or completely irrelevant.\n"
        "   - **Archive**: If a mystery in a memo is SOLVED or an event is CONCLUDED. (This moves it to History)\n"
        "2. **Quest**:\n"
        "   - **Add**: Major objectives given to the party.\n"
        "   - **Complete**: When the party fulfills an objective (Moves to History).\n"
        "3. **NPC**: Add when a NEW named character appears.\n"
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
                return {"Observation": "Error", "SystemAction": {"tool": "None"}}
            await asyncio.sleep(delay)
    return None

async def analyze_genre_from_lore(client, model_id, lore_text):
    """[기존] 장르 분석"""
    system_instruction = (
        "Genre Analyzer. Select 1-3 tags from: "
        "['noir', 'sf', 'wuxia', 'cyberpunk', 'high_fantasy', 'low_fantasy', "
        "'cosmic_horror', 'post_apocalypse', 'urban_fantasy', 'steampunk', 'school_life']\n"
        "Also extract 'custom_tone'. JSON Only."
    )
    user_prompt = f"Lore: {lore_text}\nAnalyze genre."
    for i in range(3):
        try:
            response = client.models.generate_content(
                model=model_id,
                contents=[types.Content(role="user", parts=[types.Part(text=user_prompt)])],
                config=types.GenerateContentConfig(system_instruction=system_instruction, response_mime_type="application/json")
            )
            res = json.loads(response.candidates[0].content.parts[0].text)
            return {"genres": res.get("genres", ["noir"]), "custom_tone": res.get("custom_tone")}
        except: await asyncio.sleep(1)
    return {"genres": ["noir"], "custom_tone": None}

async def analyze_npcs_from_lore(client, model_id, lore_text):
    """[기존] NPC 추출"""
    system_instruction = (
        "NPC Profiler. Extract key named NPCs. JSON: {\"npcs\": [{\"name\": \"str\", \"description\": \"str\"}]}"
    )
    user_prompt = f"Lore: {lore_text}\nExtract NPCs."
    for i in range(3):
        try:
            response = client.models.generate_content(
                model=model_id,
                contents=[types.Content(role="user", parts=[types.Part(text=user_prompt)])],
                config=types.GenerateContentConfig(system_instruction=system_instruction, response_mime_type="application/json")
            )
            return json.loads(response.candidates[0].content.parts[0].text).get("npcs", [])
        except: await asyncio.sleep(1)
    return []

async def analyze_location_rules_from_lore(client, model_id, lore_text):
    """[기존] 장소 규칙 추출"""
    system_instruction = (
        "World Analyst. Extract dangerous location rules. JSON: {\"rules\": {\"LocName\": {\"risk\": \"High\", \"condition\": \"Night\", \"effect\": \"str\"}}}"
    )
    user_prompt = f"Lore: {lore_text}\nExtract location rules."
    for i in range(3):
        try:
            response = client.models.generate_content(
                model=model_id,
                contents=[types.Content(role="user", parts=[types.Part(text=user_prompt)])],
                config=types.GenerateContentConfig(system_instruction=system_instruction, response_mime_type="application/json")
            )
            return json.loads(response.candidates[0].content.parts[0].text).get("rules", {})
        except: await asyncio.sleep(1)
    return {}