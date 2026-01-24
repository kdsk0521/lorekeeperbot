"""
=========================================================
   LEFT BRAIN - EXTRACTION (좌뇌 B - 업데이트 추출)
   
   역할:
   - 서사에서 변화 추출
   - 4개 분리 추출기 (병렬 실행)
     - B-1: 물리적 변화 (inventory, gold, status)
     - B-2: 사회적 변화 (relationships, companions)
     - B-3: 서사적 변화 (known_info, foreshadowing, passives)
     - B-4: 퀘스트/메모 (quest, memo)
=========================================================
"""

import json
import logging
import asyncio
from typing import Dict, Any, List, Optional
from google.genai import types

# 공통 유틸 import
from memory_system import api_call_with_retry, safe_parse_json

logger = logging.getLogger("LeftBrain-Extraction")


# =========================================================
# B-1: 물리적 변화 추출
# =========================================================

async def extract_physical_updates(
    client,
    model_id_flash: str,
    player_input: str,
    ai_response: str,
    current_inventory: Dict[str, int] = None,
    current_gold: int = 0,
    current_status: List[str] = None
) -> Dict[str, Any]:
    """
    [좌뇌 B-1] 물리적 변화 추출 - 인벤토리, 골드, 상태이상
    """
    
    system_prompt = (
        "You are a PHYSICAL CHANGE extractor for TRPG.\n"
        "Extract ONLY inventory, gold, and status changes.\n\n"
        
        "### OUTPUT FORMAT (JSON ONLY)\n"
        "{\n"
        '  "inventory_add": {"아이템": 수량} OR null,\n'
        '  "inventory_remove": {"아이템": 수량} OR null,\n'
        '  "gold_change": +100 OR -50 OR null,\n'
        '  "status_add": ["상태"] OR null,\n'
        '  "status_remove": ["상태"] OR null\n'
        "}\n\n"
        
        "========================================\n"
        "### INVENTORY: Single Principle\n"
        "========================================\n"
        "**ONE TEST:** Can the player TAKE this item to the NEXT scene?\n\n"
        
        "- YES (possesses it) → add/remove inventory\n"
        "- NO (consumed, service, others' property) → null\n\n"
        
        "Examples:\n"
        "- '검을 받았다' → Can take to next scene → ✅ add\n"
        "- '급식을 받았다' → Eaten, can't take → ❌ null\n"
        "- '치료를 받았다' → Service, can't take → ❌ null\n"
        "- '동료가 대신 챙겼다' → Party can access → ✅ add\n"
        "- 'NPC가 자기 주머니에' → Can't access → ❌ null\n"
        "- '물약을 마셨다' → Used, gone → ✅ remove\n\n"
        
        "========================================\n"
        "### GOLD: Single Principle\n"
        "========================================\n"
        "**ONE TEST:** Did gold enter/leave the PLAYER'S wallet?\n\n"
        
        "- YES → gold_change (+/-)\n"
        "- NO (quoted price, NPC's money) → null\n\n"
        
        "========================================\n"
        "### STATUS: Single Principle\n"
        "========================================\n"
        "**ONE TEST:** Will this condition PERSIST to the next scene?\n\n"
        
        "- YES (ongoing effect) → status_add/remove\n"
        "- NO (momentary, already over) → null\n"
    )
    
    context = (
        f"현재 인벤토리: {current_inventory}\n"
        f"현재 골드: {current_gold}\n"
        f"현재 상태: {current_status}"
    )
    
    user_prompt = (
        f"### 현재 상태\n{context}\n\n"
        f"### 플레이어 입력\n{player_input}\n\n"
        f"### AI 서사\n{ai_response[:1500]}\n\n"
        "물리적 변화만 추출. JSON만 출력."
    )
    
    try:
        config = types.GenerateContentConfig(response_mime_type="application/json", temperature=0.1)
        contents = [types.Content(role="user", parts=[types.Part(text=f"{system_prompt}\n\n{user_prompt}")])]
        
        result = await api_call_with_retry(
            client, model_id_flash, contents, config, operation_name="B-1 Physical"
        )
        if result:
            return safe_parse_json(result)
    except Exception as e:
        logger.warning(f"[B-1 Physical] Error: {e}")
        
    return {}


# =========================================================
# B-2: 사회적 변화 추출
# =========================================================

async def extract_social_updates(
    client,
    model_id_flash: str,
    player_input: str,
    ai_response: str,
    current_relationships: Dict[str, str] = None,
    current_companions: List[str] = None,
    lore_npc_names: List[str] = None,
    scene_npc_names: List[str] = None
) -> Dict[str, Any]:
    """
    [좌뇌 B-2] 사회적 변화 추출 - 관계, 동행자
    """
    
    system_prompt = (
        "You are a SOCIAL CHANGE extractor for TRPG.\n"
        "Extract ONLY relationship and companion changes.\n\n"
        
        "### OUTPUT FORMAT (JSON ONLY)\n"
        "{\n"
        '  "relationships": {"NPC이름": "관계단계(이유)"} OR null,\n'
        '  "companions": ["동행자: 설명"] OR null\n'
        "}\n\n"
        
        "### NPC IDENTITY RULES\n"
        "- [LORE NPCs]: Use EXACT name from lore\n"
        "- [SCENE NPCs]: Same person throughout scene\n"
        "- Multiple references to same role = ONE person\n\n"
        
        "========================================\n"
        "### RELATIONSHIPS: Single Principle\n"
        "========================================\n"
        "**ONE TEST:** If they meet again, will the relationship be DIFFERENT?\n\n"
        
        "- YES (level changed, major event) → update relationship\n"
        "- NO (same as before, just talked) → null\n\n"
        
        "Levels: hostile → unfriendly → neutral → friendly → friendly(trusted) → devoted\n\n"
        
        "========================================\n"
        "### COMPANIONS: Single Principle\n"
        "========================================\n"
        "**ONE TEST:** Will this person TRAVEL with the player to the next scene?\n\n"
        
        "- YES (joined party) → add companion\n"
        "- NO (staying behind, just met) → null\n"
    )
    
    context_parts = []
    if lore_npc_names:
        context_parts.append(f"[LORE NPCs]: {', '.join(lore_npc_names[:15])}")
    if scene_npc_names:
        context_parts.append(f"[SCENE NPCs]: {', '.join(scene_npc_names)}")
    if current_relationships:
        # 딕셔너리를 리스트로 변환 후 슬라이싱
        rel_list = [f"{k}({v})" for k, v in current_relationships.items()]
        context_parts.append(f"[현재 관계]: {', '.join(rel_list[:10])}")
    if current_companions:
        context_parts.append(f"[현재 동행자]: {', '.join(current_companions)}")
    
    context = "\n".join(context_parts) if context_parts else "없음"
    
    user_prompt = (
        f"### 현재 상태\n{context}\n\n"
        f"### 플레이어 입력\n{player_input}\n\n"
        f"### AI 서사\n{ai_response[:1500]}\n\n"
        "관계/동행자 변화만 추출. JSON만 출력."
    )
    
    try:
        config = types.GenerateContentConfig(response_mime_type="application/json", temperature=0.1)
        contents = [types.Content(role="user", parts=[types.Part(text=f"{system_prompt}\n\n{user_prompt}")])]
        
        result = await api_call_with_retry(
            client, model_id_flash, contents, config, operation_name="B-2 Social"
        )
        if result:
            return safe_parse_json(result)
    except Exception as e:
        logger.warning(f"[B-2 Social] Error: {e}")
        
    return {}


# =========================================================
# B-3: 서사적 변화 추출
# =========================================================

async def extract_narrative_updates(
    client,
    model_id_flash: str,
    player_input: str,
    ai_response: str,
    current_passives: List[str] = None,
    current_known_info: List[str] = None,
    current_foreshadowing: List[str] = None
) -> Dict[str, Any]:
    """
    [좌뇌 B-3] 서사적 변화 추출 - 정보, 복선, 패시브
    """
    
    system_prompt = (
        "You are a NARRATIVE CHANGE extractor for TRPG.\n"
        "Extract ONLY knowledge, foreshadowing, and passive changes.\n\n"
        
        "### OUTPUT FORMAT (JSON ONLY)\n"
        "{\n"
        '  "known_info": ["중요 정보"] OR null,\n'
        '  "foreshadowing": ["복선"] OR null,\n'
        '  "passives": ["패시브"] OR null,\n'
        '  "info_archive": ["보관할 정보 (더 이상 안 중요함)"] OR null,\n'
        '  "foreshadowing_archive": ["보관할 복선 (해결됨/지나감)"] OR null,\n'
        '  "passive_suggestion": {\n'
        '    "name": "패시브/칭호 이름",\n'
        '    "trigger": "획득 조건 설명",\n'
        '    "effect": "구체적 효과",\n'
        '    "category": "카테고리",\n'
        '    "reasoning": "왜 이 패시브를 제안하는지 간단 설명"\n'
        '  } OR null\n'
        "}\n\n"
        
        "### PASSIVE SUGGESTION SYSTEM (NEW)\n"
        "If the player has achieved something SIGNIFICANT or REPEATED (5+ times), suggest a NEW passive.\n"
        "This is different from 'passives' list (which tracks usage of EXISTING passives).\n\n"
        
        "**When to suggest:**\n"
        "- Repeated experiences: 독에 자주 중독(5회) → [독 내성]\n"
        "- Relationship milestone: 엘프와 10회 우호적 → [엘프의 친구]\n"
        "- Survival: 죽을 고비 3회 넘김 → [구사일생]\n"
        "- Unique feat: 드래곤 처치 → [용 사냥꾼]\n\n"
        
        "========================================\n"
        "### KNOWN_INFO: Single Principle\n"
        "========================================\n"
        "**ONE TEST:** Does knowing this UNLOCK NEW OPTIONS for the player?\n\n"
        
        "- YES (enables new action/path) → add to known_info\n"
        "- NO (trivial, obvious, already known) → null\n\n"
        
        "Examples:\n"
        "- '금고 비밀번호는 1234' → Unlocks safe → ✅ add\n"
        "- '시장이 뱀파이어다' → Changes approach → ✅ add\n"
        "- '오늘 날씨가 좋다' → No new options → ❌ null\n"
        "- '상인이 친절했다' → No new options → ❌ null\n"
        "- Player's own skill mentioned → That's passive, not info → ❌ null\n\n"
        
        "========================================\n"
        "### FORESHADOWING: Single Principle\n"
        "========================================\n"
        "**ONE TEST:** Will this matter LATER in the plot?\n\n"
        
        "- YES (hints at future events) → add foreshadowing\n"
        "- NO (just atmosphere, already resolved) → null\n\n"
        
        "Examples:\n"
        "- '팔에 이상한 문양이 나타났다' → Mystery for later → ✅ add\n"
        "- '예언에서 선택받은 자를 언급' → Future plot → ✅ add\n"
        "- 'NPC가 긴장해 보였다' → Just mood → ❌ null\n"
        "- '비가 내리기 시작했다' → Just weather → ❌ null\n\n"
        
        "========================================\n"
        "### PASSIVES: Single Principle\n"
        "========================================\n"
        "**ONE TEST:** Has the player REPEATEDLY PROVEN this ability?\n\n"
        
        "- YES (3+ successes, exceptional display) → add passive\n"
        "- NO (first try, luck, failed) → null\n\n"
        
        "Passives are RARE achievements, like titles.\n\n"
        
        "Examples:\n"
        "- First time picking a lock → ❌ null (just once)\n"
        "- Third successful lockpick → Maybe ✅ (pattern)\n"
        "- Exceptional combat display → ✅ add (proven)\n"
        "- Failed attempt → ❌ null (not proven)\n"
        "- Skill already in [EXISTING PASSIVES] → ❌ null (duplicate)\n"
    )
    
    context_parts = []
    if current_passives:
        context_parts.append(f"[기존 패시브 - 중복 금지]: {', '.join(current_passives)}")
    if current_known_info:
        context_parts.append(f"[이미 아는 정보]: {', '.join(current_known_info[:5])}")
    if current_foreshadowing:
        context_parts.append(f"[기존 복선]: {', '.join(current_foreshadowing[:3])}")
    
    context = "\n".join(context_parts) if context_parts else "없음"
    
    user_prompt = (
        f"### 현재 상태\n{context}\n\n"
        f"### 플레이어 입력\n{player_input}\n\n"
        f"### AI 서사\n{ai_response[:1500]}\n\n"
        "정보/복선/패시브 변화만 추출. JSON만 출력."
    )
    
    try:
        config = types.GenerateContentConfig(response_mime_type="application/json", temperature=0.1)
        contents = [types.Content(role="user", parts=[types.Part(text=f"{system_prompt}\n\n{user_prompt}")])]
        
        result = await api_call_with_retry(
            client, model_id_flash, contents, config, operation_name="B-3 Narrative"
        )
        if result:
            return safe_parse_json(result)
    except Exception as e:
        logger.warning(f"[B-3 Narrative] Error: {e}")
        
    return {}


# =========================================================
# B-4: 퀘스트/메모 추출
# =========================================================

async def extract_quest_updates(
    client,
    model_id_flash: str,
    player_input: str,
    ai_response: str,
    current_quests: List[str] = None,
    current_memos: List[str] = None
) -> Dict[str, Any]:
    """
    [좌뇌 B-4] 퀘스트/메모 변화 추출
    """
    
    system_prompt = (
        "You are a QUEST/MEMO extractor for TRPG.\n"
        "Extract quest and memo changes.\n\n"
        
        "### OUTPUT FORMAT (JSON ONLY)\n"
        "{\n"
        '  "quest_add": ["새 퀘스트"] OR null,\n'
        '  "quest_complete": ["완료 퀘스트"] OR null,\n'
        '  "memo_add": ["새 메모"] OR null,\n'
        '  "memo_remove": ["삭제할 메모"] OR null,\n'
        '  "memo_archive": ["보관할 메모"] OR null\n'
        "}\n\n"
        
        "========================================\n"
        "### QUEST_ADD: Single Principle\n"
        "========================================\n"
        "**ONE TEST:** Does the player now have a GOAL to pursue?\n\n"
        
        "- YES (task given, objective discovered) → add quest\n"
        "- NO (just information, no action needed) → null\n\n"
        
        "Examples:\n"
        "- 'NPC가 고블린 소탕을 부탁했다' → Goal exists → ✅ add\n"
        "- '던전이 있다는 소문을 들었다' → No explicit goal → ❌ null\n"
        "- '보상을 약속했다' → Motivation, but what's the task? → needs explicit goal\n\n"
        
        "========================================\n"
        "### QUEST_COMPLETE: Single Principle\n"
        "========================================\n"
        "**ONE TEST:** Has the OBJECTIVE been ACHIEVED?\n\n"
        
        "- YES (goal met, task done) → complete quest\n"
        "- NO (in progress, partially done) → null\n\n"
        
        "Examples:\n"
        "- '고블린을 모두 처치했다' (objective was extermination) → ✅ complete\n"
        "- '고블린 3마리를 잡았다' (objective was 10) → ❌ null (not done)\n\n"
        
        "========================================\n"
        "### MEMO: Single Principle\n"
        "========================================\n"
        "**ONE TEST:** Is this worth REFERRING BACK to later?\n\n"
        
        "- YES (useful reference) → memo_add\n"
        "- NO (trivial, one-time) → null\n\n"
        
        "**memo_remove vs memo_archive:**\n"
        "- remove: Information no longer relevant (consumed, outdated, wrong)\n"
        "- archive: Important to KEEP permanently (equipment, key relationships)\n\n"
        
        "Examples:\n"
        "- '열쇠를 찾아야 한다' → Useful reminder → ✅ memo_add\n"
        "- '열쇠를 사용했다' → No longer needed → ✅ memo_remove\n"
        "- '전설의 검을 획득' → Keep forever → ✅ memo_archive\n"
    )
    
    context_parts = []
    if current_quests:
        context_parts.append(f"[활성 퀘스트]: {', '.join(current_quests[:5])}")
    if current_memos:
        context_parts.append(f"[현재 메모]: {', '.join(current_memos[:5])}")
    
    context = "\n".join(context_parts) if context_parts else "없음"
    
    user_prompt = (
        f"### 현재 상태\n{context}\n\n"
        f"### 플레이어 입력\n{player_input}\n\n"
        f"### AI 서사\n{ai_response[:1500]}\n\n"
        "퀘스트/메모 변화만 추출. JSON만 출력."
    )
    
    try:
        config = types.GenerateContentConfig(response_mime_type="application/json", temperature=0.1)
        contents = [types.Content(role="user", parts=[types.Part(text=f"{system_prompt}\n\n{user_prompt}")])]
        
        result = await api_call_with_retry(
            client, model_id_flash, contents, config, operation_name="B-4 Quest"
        )
        if result:
            return safe_parse_json(result)
    except Exception as e:
        logger.warning(f"[B-4 Quest] Error: {e}")
        
    return {}


# =========================================================
# 통합 추출 함수 (4개 병렬 실행)
# =========================================================

async def extract_all_updates(
    client,
    model_id_flash: str,
    player_input: str,
    ai_response: str,
    # 물리적
    current_inventory: Dict[str, int] = None,
    current_gold: int = 0,
    current_status: List[str] = None,
    # 사회적
    current_relationships: Dict[str, str] = None,
    current_companions: List[str] = None,
    lore_npc_names: List[str] = None,
    scene_npc_names: List[str] = None,
    # 서사적
    current_passives: List[str] = None,
    current_known_info: List[str] = None,
    current_foreshadowing: List[str] = None,
    # 퀘스트/메모
    current_quests: List[str] = None,
    current_memos: List[str] = None
) -> Dict[str, Any]:
    """
    [좌뇌 B 통합] 4개의 분리된 추출 함수를 병렬 호출하고 결과를 통합합니다.
    """
    
    # 4개 호출을 병렬로 실행
    results = await asyncio.gather(
        extract_physical_updates(
            client, model_id_flash, player_input, ai_response,
            current_inventory, current_gold, current_status
        ),
        extract_social_updates(
            client, model_id_flash, player_input, ai_response,
            current_relationships, current_companions,
            lore_npc_names, scene_npc_names
        ),
        extract_narrative_updates(
            client, model_id_flash, player_input, ai_response,
            current_passives, current_known_info, current_foreshadowing
        ),
        extract_quest_updates(
            client, model_id_flash, player_input, ai_response,
            current_quests, current_memos
        ),
        return_exceptions=True
    )
    
    # 결과 통합
    physical, social, narrative, quest = results
    
    # 에러 처리 (결과가 Exception인 경우)
    if isinstance(physical, Exception):
        logger.warning(f"[B-1 Physical] 실패: {physical}")
        physical = {}
    if isinstance(social, Exception):
        logger.warning(f"[B-2 Social] 실패: {social}")
        social = {}
    if isinstance(narrative, Exception):
        logger.warning(f"[B-3 Narrative] 실패: {narrative}")
        narrative = {}
    if isinstance(quest, Exception):
        logger.warning(f"[B-4 Quest] 실패: {quest}")
        quest = {}
    
    # None 체크 (각 함수가 None을 반환할 수 있음)
    physical = physical or {}
    social = social or {}
    narrative = narrative or {}
    quest = quest or {}

    return {
        "PlayerUpdate": {
            "inventory_add": physical.get("inventory_add"),
            "inventory_remove": physical.get("inventory_remove"),
            "gold_change": physical.get("gold_change"),
            "status_add": physical.get("status_add"),
            "status_remove": physical.get("status_remove")
        } if any([physical.get("inventory_add"), physical.get("inventory_remove"),
                  physical.get("gold_change"), physical.get("status_add"),
                  physical.get("status_remove")]) else None,
        
        "PlayerMemoryUpdate": {
            "relationships": social.get("relationships"),
            "companions": social.get("companions"),
            "passives": narrative.get("passives"),
            "known_info": narrative.get("known_info"),
            "foreshadowing": narrative.get("foreshadowing"),
            "info_archive": narrative.get("info_archive"),
            "foreshadowing_archive": narrative.get("foreshadowing_archive")
        } if any([social.get("relationships"), social.get("companions"),
                  narrative.get("passives"), narrative.get("known_info"),
                  narrative.get("foreshadowing"),
                  narrative.get("info_archive"), narrative.get("foreshadowing_archive")]) else None,
        
        "PassiveSuggestion": narrative.get("passive_suggestion"),
        
        "QuestUpdate": {
            "quest_add": quest.get("quest_add"),
            "quest_complete": quest.get("quest_complete"),
            "memo_add": quest.get("memo_add"),
            "memo_remove": quest.get("memo_remove"),
            "memo_archive": quest.get("memo_archive")
        } if any([quest.get("quest_add"), quest.get("quest_complete"),
                  quest.get("memo_add"), quest.get("memo_remove"),
                  quest.get("memo_archive")]) else None
    }
