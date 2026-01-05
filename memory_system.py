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
        "      \"type\": \"Add\" | \"Remove\" | \"Complete\",\n"
        "      \"content\": \"Summary of the item to add/remove\"\n"
        "  }\n"
        "}\n\n"
        "### GUIDELINES\n"
        "1. **CurrentLocation/Risk**: Infer location and safety based on context (High=Combat/Monster).\n"
        "2. **InteractionType**: Detect if user is silent/ignoring (passive) or acting (active).\n"
        "3. **SystemAction**:\n"
        "   - **NPC**: Add when a NEW named character appears.\n"
        "   - **Memo/Quest**: Update clues and objectives.\n"
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
    """
    [수정] 로어 텍스트를 분석하여 가장 적합한 장르 태그와 커스텀 톤을 추출합니다.
    """
    system_instruction = (
        "You are a Genre & Atmosphere Analyzer. Read the provided World Lore.\n"
        "1. **Genres**: Select 1-3 tags from the list below that best match the world.\n"
        "   ['noir', 'sf', 'wuxia', 'cyberpunk', 'high_fantasy', 'low_fantasy', "
        "    'cosmic_horror', 'post_apocalypse', 'urban_fantasy', 'steampunk', 'school_life']\n\n"
        "2. **Custom Tone**: If the lore contains specific subculture elements (e.g., Magical Girls, Superheroes, Isekai, Vampire Romance) that are NOT fully covered by the tags, summarize the unique atmosphere in one English sentence.\n"
        "   - If no special subculture is found, set 'custom_tone': null.\n\n"
        "Respond ONLY with a JSON object: {\"genres\": [\"tag1\", \"tag2\"], \"custom_tone\": \"string\"}"
    )

    user_prompt = f"### WORLD LORE\n{lore_text}\n\nAnalyze the genre and unique tone."

    for i in range(3):
        try:
            response = client.models.generate_content(
                model=model_id,
                contents=[types.Content(role="user", parts=[types.Part(text=user_prompt)])],
                config=types.GenerateContentConfig(
                    system_instruction=system_instruction,
                    response_mime_type="application/json",
                )
            )
            result = json.loads(response.candidates[0].content.parts[0].text)
            
            genres = result.get("genres", ["noir"])
            custom_tone = result.get("custom_tone")
            return {"genres": genres, "custom_tone": custom_tone}
            
        except Exception as e:
            await asyncio.sleep(1)
            
    return {"genres": ["noir"], "custom_tone": None}

async def analyze_npcs_from_lore(client, model_id, lore_text):
    """
    [신규] 로어 텍스트에서 NPC 정보를 추출합니다.
    """
    system_instruction = (
        "You are an NPC Profiler. Read the provided World Lore.\n"
        "Identify key NPCs (names, roles, brief descriptions) mentioned in the text.\n"
        "Ignore generic groups. Focus on specific named or titled individuals.\n\n"
        "Respond ONLY with a JSON object: {\"npcs\": [{\"name\": \"String\", \"description\": \"String (Role & Trait)\"}]}"
    )

    user_prompt = f"### WORLD LORE\n{lore_text}\n\nExtract key NPCs."

    for i in range(3):
        try:
            response = client.models.generate_content(
                model=model_id,
                contents=[types.Content(role="user", parts=[types.Part(text=user_prompt)])],
                config=types.GenerateContentConfig(
                    system_instruction=system_instruction,
                    response_mime_type="application/json",
                )
            )
            result = json.loads(response.candidates[0].content.parts[0].text)
            return result.get("npcs", [])
        except Exception as e:
            await asyncio.sleep(1)
    return []

async def analyze_location_rules_from_lore(client, model_id, lore_text):
    """
    [신규] 로어 텍스트에서 '장소별 위험 규칙'을 추출합니다.
    """
    system_instruction = (
        "You are a World Analyst. Read the Lore and identify specific locations with special environmental rules or risks.\n"
        "Focus on: Dangerous places, restricted areas, or locations that change properties based on time/condition.\n\n"
        "Respond ONLY with a JSON object:\n"
        "{\n"
        "  \"rules\": {\n"
        "    \"Location Name\": {\"risk\": \"High/Medium/Low\", \"condition\": \"Night/Rain/Always\", \"effect\": \"Description of threat\"}\n"
        "  }\n"
        "}\n"
        "Example: {\"rules\": {\"Western Ruins\": {\"risk\": \"High\", \"condition\": \"Night\", \"effect\": \"Ghosts appear\"}}}"
    )

    user_prompt = f"### WORLD LORE\n{lore_text}\n\nExtract location-based rules."

    for i in range(3):
        try:
            response = client.models.generate_content(
                model=model_id,
                contents=[types.Content(role="user", parts=[types.Part(text=user_prompt)])],
                config=types.GenerateContentConfig(
                    system_instruction=system_instruction,
                    response_mime_type="application/json",
                )
            )
            result = json.loads(response.candidates[0].content.parts[0].text)
            return result.get("rules", {})
        except Exception as e:
            await asyncio.sleep(1)
    return {}