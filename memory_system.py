import json
import asyncio
import logging
import re
from google.genai import types

# =========================================================
# [HELPER] JSON 파싱 안전장치
# =========================================================
def safe_parse_json(text):
    """
    AI 응답 텍스트에서 JSON 객체나 리스트를 정밀하게 찾아 파싱합니다.
    """
    try:
        if not text: return {}
        text = re.sub(r"```(json)?", "", text).strip()
        
        start_idx = -1
        end_idx = -1
        for i, char in enumerate(text):
            if char in ['{', '[']:
                start_idx = i
                break
        
        if start_idx != -1:
            target_end = '}' if text[start_idx] == '{' else ']'
            for i in range(len(text) - 1, start_idx, -1):
                if text[i] == target_end:
                    end_idx = i + 1
                    break
            if end_idx != -1:
                text = text[start_idx:end_idx]

        data = json.loads(text)
        
        if isinstance(data, list):
            if len(data) > 0 and isinstance(data[0], dict): return data[0]
            return {}
        if not isinstance(data, dict): return {}
        return data

    except json.JSONDecodeError:
        return {}
    except Exception:
        return {}

# =========================================================
# [LORE COMPRESSION] 로어 압축기
# =========================================================
async def compress_lore_core(client, model_id, raw_lore_text):
    """
    [THEORIA LOGIC CORE]
    방대한 로어 텍스트를 토큰 효율적인 '핵심 요약본'으로 압축합니다.
    """
    system_instruction = (
        "[Role: Lore Archivist]\n"
        "Compress text into a dense TRPG Sourcebook summary.\n"
        "**Discard:** Fluff, poetry, repetition.\n"
        "**Keep:** Rules, Factions, NPC Motives, Conflicts, Secrets.\n"
        "**Format:** Plain text sections (World Laws, Factions, NPCs, Tension, Secrets)."
    )
    
    user_prompt = f"### RAW TEXT\n{raw_lore_text}\n\n### INSTRUCTION\nCompress into dense sourcebook summary."

    for i in range(3):
        try:
            response = await client.aio.models.generate_content(
                model=model_id,
                contents=[types.Content(role="user", parts=[types.Part(text=user_prompt)])],
                config=types.GenerateContentConfig(temperature=0.2)
            )
            return response.text.strip()
        except Exception:
            await asyncio.sleep(1)
            
    return "Error: Lore Compression Failed."

# =========================================================
# [LOGIC ANALYZER] 상황 판단 및 인과율 계산
# =========================================================
async def analyze_context_nvc(client, model_id, history_text, lore, rules, active_quests_text):
    """
    [THEORIA LEFT HEMISPHERE]
    Analyzes 'Macroscopic State' for Alien Research Data Collection.
    """
    system_instruction = (
        "[Identity: Logic Core]\n"
        "Analyze input to extract objective facts. **Apply MECE principle.**\n\n"
        
        "### OBSERVATION PROTOCOLS\n"
        "1. **Physics Check (Hard Limits):** Verify physical/logical possibility. If impossible, state: **'Action Failed: Physics Violation'**.\n"
        "2. **Macroscopic Only:** Analyze observable actions/states ONLY. No mind-reading.\n"
        "3. **Knowledge Firewall:** Distinguish Player Knowledge vs Character Knowledge.\n"
        "4. **Auto-XP Calculation:**\n"
        "   - **Minor (10-30):** Skill check, smart move.\n"
        "   - **Major (50-100):** Defeated enemy, solved puzzle, survived crisis.\n"
        "   - **Critical (200+):** Boss kill, Quest complete.\n"
        "   - *Condition:* Award ONLY for Success/Victory.\n\n"

        "### OUTPUT FORMAT (JSON ONLY)\n"
        "{\n"
        "  \"CurrentLocation\": \"Location Name\",\n"
        "  \"LocationRisk\": \"None/Low/Medium/High/Extreme\",\n"
        "  \"TimeContext\": \"Time of day/flow\",\n"
        "  \"Observation\": \"Objective summary (Who, What, Reaction, Physical Outcome).\",\n"
        "  \"Need\": \"Logical next step (e.g., 'Calc damage', 'Scene transition')\",\n"
        "  \"SystemAction\": { \"tool\": \"Quest/Memo/NPC/XP\", \"type\": \"...\", \"content\": \"...\" } OR null\n"
        "}\n"
    )

    user_prompt = (
        f"### [RULES]\n{rules}\n### [QUESTS]\n{active_quests_text}\n### [HISTORY]\n{history_text}\n"
        "Analyze the current state."
    )

    for i in range(3):
        try:
            response = await client.aio.models.generate_content(
                model=model_id,
                contents=[types.Content(role="user", parts=[types.Part(text=user_prompt)])],
                config=types.GenerateContentConfig(response_mime_type="application/json", temperature=0.1)
            )
            return safe_parse_json(response.text)
        except Exception:
            await asyncio.sleep(1)
            
    return {
        "CurrentLocation": "Unknown", "LocationRisk": "Low", 
        "Observation": "Analysis Failed", "Need": "Proceed Caution"
    }

# =========================================================
# [LORE ANALYZER] 로어 분석 도구들 (강화됨: Top-K 필터링)
# =========================================================
async def analyze_genre_from_lore(client, model_id, lore_text):
    """
    [Logic Core] Analyze Genre & Tone
    **[수정]** 점수 기반(Frequency Score)으로 상위 3개 장르만 추출하여 '장르 샐러드' 현상을 방지합니다.
    """
    system_instruction = (
        "Analyze the provided Lore and extract Key Genres.\n"
        "**CRITICAL:** Select ONLY the most dominant 1-3 genres. Do not list minor elements.\n"
        "Supported List: ['wuxia', 'noir', 'high_fantasy', 'cyberpunk', 'cosmic_horror', 'post_apocalypse', 'urban_fantasy', 'steampunk', 'school_life', 'superhero', 'space_opera', 'western', 'occult', 'military']\n"
        "Output JSON: {\"genres\": [str], \"custom_tone\": str}"
    )
    user_prompt = f"Lore Data:\n{lore_text}"
    
    ai_result_genres = []
    custom_tone = "Default"

    # 1. AI 분석 시도
    for i in range(3):
        try:
            response = await client.aio.models.generate_content(
                model=model_id,
                contents=[types.Content(role="user", parts=[types.Part(text=user_prompt)])],
                config=types.GenerateContentConfig(response_mime_type="application/json")
            )
            data = safe_parse_json(response.text)
            ai_result_genres = data.get("genres", [])
            custom_tone = data.get("custom_tone", "Analyzed Tone")
            break
        except: await asyncio.sleep(1)
    
    # 2. 키워드 스코어링 (빈도수 계산)
    text_lower = lore_text.lower()
    
    keyword_map = {
        "high_fantasy": ["dragon", "elf", "orc", "magic", "wizard", "spell", "kingdom", "mana", "legion", "드래곤", "엘프", "마법"],
        "steampunk": ["steam", "gear", "brass", "industrial", "engine", "victorian", "clockwork", "airship", "스팀", "증기", "톱니"],
        "cyberpunk": ["cyber", "neon", "hacker", "corp", "implant", "android", "chrome", "사이버", "해커"],
        "wuxia": ["murim", "cultivation", "sect", "qi", "martial", "jianghu", "무협", "무림", "강호"],
        "cosmic_horror": ["cthulhu", "eldritch", "sanity", "cult", "madness", "ancient one", "크툴루", "코즈믹"],
        "post_apocalypse": ["wasteland", "radiation", "ruins", "survival", "scavenge", "mutant", "아포칼립스", "황무지"],
        "urban_fantasy": ["modern magic", "masquerade", "secret society", "vampire", "hunter", "어반", "이능"],
        "school_life": ["school", "academy", "student", "class", "club", "학교", "학생", "학원"],
        "superhero": ["superhero", "villain", "superpower", "costume", "justice", "hero", "히어로", "초능력"],
        "space_opera": ["spaceship", "galaxy", "planet", "alien", "warp", "starship", "우주", "은하"],
        "western": ["cowboy", "revolver", "saloon", "sheriff", "outlaw", "wild west", "카우보이", "서부"],
        "occult": ["ghost", "spirit", "curse", "exorcism", "haunted", "ritual", "demon", "유령", "오컬트"],
        "military": ["soldier", "special forces", "tactical", "warfare", "squad", "mercenary", "군인", "특수부대"]
    }

    genre_scores = {}
    for genre, keys in keyword_map.items():
        count = sum(1 for k in keys if k in text_lower)
        if count > 0:
            genre_scores[genre] = count

    # 3. 상위 장르 선별 (Top-K Logic)
    # 점수가 높은 순으로 정렬
    sorted_genres = sorted(genre_scores.items(), key=lambda x: x[1], reverse=True)
    
    # 키워드 기반 감지: 최소 3번 이상 등장한 장르 중 상위 3개만 선택
    detected_genres = [g for g, score in sorted_genres if score >= 3][:3]
    
    # 만약 엄격한 기준(3점)을 통과한 게 없다면, 완화된 기준(1점 이상) 중 상위 2개 선택 (AI 실패 대비)
    if not detected_genres and sorted_genres:
        detected_genres = [g for g, score in sorted_genres[:2]]

    # 4. 결과 병합 (AI 결과 + 키워드 결과)
    final_genres_set = set(ai_result_genres)
    final_genres_set.update(detected_genres)
    
    # 5. 최종 필터링: 최대 3개까지만 유지 (점수 높은 순 우선, 없으면 무작위)
    final_genres = list(final_genres_set)
    
    # 리스트가 너무 길면(4개 이상), 점수가 높은 순서대로 자름
    if len(final_genres) > 3:
        # 점수가 없는(AI만 감지한) 장르는 점수 0으로 취급하여 정렬
        final_genres.sort(key=lambda x: genre_scores.get(x, 0), reverse=True)
        final_genres = final_genres[:3]

    # Noir 처리: 다른 장르가 있으면 제거 (우선순위 최하위)
    if len(final_genres) > 1 and "noir" in final_genres:
        if genre_scores.get("noir", 0) < 2:
            final_genres.remove("noir")

    # 아무것도 없으면 기본값
    if not final_genres:
        final_genres = ["noir"]
        
    return {"genres": final_genres, "custom_tone": custom_tone}

async def analyze_npcs_from_lore(client, model_id, lore_text):
    """[Logic Core] Extract NPC Data"""
    system_instruction = "Extract major NPCs. JSON: {'npcs': [{'name': '...', 'description': '...'}]}"
    user_prompt = f"Lore Data:\n{lore_text}"
    for i in range(3):
        try:
            response = await client.aio.models.generate_content(
                model=model_id,
                contents=[types.Content(role="user", parts=[types.Part(text=user_prompt)])],
                config=types.GenerateContentConfig(response_mime_type="application/json")
            )
            data = safe_parse_json(response.text)
            return data.get("npcs", [])
        except: await asyncio.sleep(1)
    return []

async def analyze_location_rules_from_lore(client, model_id, lore_text):
    """[Logic Core] Extract Environmental Laws"""
    system_instruction = "Extract Location Rules. JSON: {\"rules\": {\"LocName\": {\"risk\": \"High\", \"condition\": \"Night\", \"effect\": \"str\"}}}"
    user_prompt = f"Lore Data:\n{lore_text}"
    for i in range(3):
        try:
            response = await client.aio.models.generate_content(
                model=model_id,
                contents=[types.Content(role="user", parts=[types.Part(text=user_prompt)])],
                config=types.GenerateContentConfig(response_mime_type="application/json")
            )
            data = safe_parse_json(response.text)
            return data.get("rules", {})
        except: await asyncio.sleep(1)
    return {}