"""
Lorekeeper TRPG Bot - Memory System Module (Common Utilities)
공통 유틸리티 및 레거시 호환 래퍼를 제공합니다.

Refactored Structure:
- cognition.py: Cognition Module (Theoria & Logos)
"""

import json
import asyncio
import logging
import config
import re
from typing import Optional, Dict, Any, List, Tuple
from google import genai
from google.genai import types
import text_resources  # [NEW] Import text resources

logger = logging.getLogger(__name__)

# Constants now imported from config

# =========================================================
# Shared Prompts / Constants
# =========================================================

# 분석 관련 지사서(Cognitive Architecture, State Tracking 등)는 
# 이제 analysis_resources.py에서 집중 관리됩니다.

# =========================================================
# Lorekeeper Genre System (The "Essential 12")
# =========================================================

# 1. 정예 장르 리스트 (12개)
# =========================================================
# Lorekeeper Genre System (The "Perfect 14")
# =========================================================

# 1. 최종 정예 장르 리스트 (14개)
SUPPORTED_GENRES = [
    # [A. The Stage] 배경과 무대 (6개)
    'high_fantasy',    # 정통 판타지
    'wuxia',           # 무협
    'cyberpunk',       # 사이버펑크
    'post_apocalypse', # 아포칼립스
    'space_opera',     # 스페이스 오페라
    'modern',          # [CHANGE] 현대물 (학교, 직장, 일상)

    # [B. The Flavor] 스타일과 기술 (4개)
    'urban_fantasy',   # 어반 판타지 (히어로/오컬트 통합)
    'steampunk',       # 스팀펑크
    'cosmic_horror',   # 코즈믹 호러
    'game_system',     # 게임 시스템 (성좌/인방/루프 통합)

    # [C. The Lens] 톤과 감정선 (4대장)
    'noir',            # 쿨함/냉소 (Cool)
    'comedy',          # 웃음/가벼움 (Fun)
    'romance',         # 사랑/설렘 (Love)
    'drama'            # 아픔/진지함 (Pain/Weight)
]

# 2. 키워드 매핑 (Drama 추가)
GENRE_KEYWORD_MAP = {
    # --- Existing Mappings ---
    "high_fantasy": ["dragon", "magic", "wizard", "elf", "orc", "kingdom", "드래곤", "마법", "판타지"],
    "wuxia": ["murim", "qi", "martial", "sect", "jianghu", "무협", "무림", "내공", "강호"],
    "cyberpunk": ["cyber", "neon", "corp", "implant", "android", "dystopia", "사이버", "네온", "디스토피아"],
    "post_apocalypse": ["wasteland", "survival", "zombie", "ruins", "military", "아포칼립스", "생존", "폐허"],
    "space_opera": ["spaceship", "galaxy", "alien", "warp", "우주", "SF", "함선"],
    
    # [CHANGE] Modern (School Life + Office + Daily Life)
    "modern": [
        "school", "academy", "student", "office", "company", "salaryman", 
        "hospital", "doctor", "modern day", "realistic", "slice of life",
        "학교", "학원", "학생", "회사", "직장", "오피스", "현대", "리얼리즘", "청춘"
    ],

    "urban_fantasy": ["modern magic", "vampire", "hunter", "ghost", "superhero", "villain", "myth", "어반", "오컬트", "히어로"],
    "steampunk": ["steam", "gear", "brass", "engine", "victorian", "스팀", "증기", "태엽"],
    "cosmic_horror": ["ancient one", "madness", "fear", "tentacle", "eldritch", "코즈믹", "광기", "공포", "기괴"],
    "game_system": ["status window", "level up", "quest", "system", "streaming", "time loop", "상태창", "회귀", "인방", "성좌"],
    
    # --- Tone Quartet ---
    "noir": ["detective", "shadow", "crime", "hardboiled", "mystery", "thriller", "느와르", "탐정", "하드보일드", "추리"],
    "comedy": ["sitcom", "gag", "humor", "lighthearted", "daily life", "healing", "코미디", "개그", "시트콤", "일상", "힐링"],
    "romance": ["love", "dating", "harem", "kiss", "relationship", "seducer", "로맨스", "연애", "사랑", "하렘", "썸"],
    
    # [NEW] Drama (The Emotional Weight)
    "drama": [
        "tragedy", "angst", "emotional", "politics", "growth", "conflict", "tearjerker", "serious",
        "드라마", "피폐", "서사", "비극", "성장", "정치", "암투", "감동", "신파"
    ]
}

# =========================================================
# Common Utilities
# =========================================================

from google.api_core import exceptions as google_exceptions

async def api_call_with_retry(
    client: genai.Client,
    model_id: str,
    contents: List[types.Content],
    gen_config: types.GenerateContentConfig,
    operation_name: str = "API Call",
    allow_truncated: bool = False
) -> Optional[str]:
    """
    Gemini API 호출을 재시도 로직과 함께 수행합니다.
    ResourceExhausted(429) 등 특정 에러를 우아하게 처리합니다.
    """
    # [Patch] Enforce Safety Settings & Disable AFC
    if not gen_config.safety_settings:
        gen_config.safety_settings = config.SAFETY_SETTINGS
    
    if gen_config.tools is None:
        gen_config.tools = [] # Explicitly disable AFC
    
    gen_config.automatic_function_calling = types.AutomaticFunctionCallingConfig(disable=True)
    
    # Aggressively disable AFC
    if not gen_config.tool_config:
        gen_config.tool_config = types.ToolConfig(
            function_calling_config=types.FunctionCallingConfig(
                mode=types.FunctionCallingConfigMode.NONE
            )
        )
        
    for attempt in range(config.MAX_RETRY_COUNT):
        try:
            response = await client.aio.models.generate_content(
                model=model_id,
                contents=contents,
                config=gen_config
            )
            
            # ===== [NEW] 상세 진단 =====
            if response is None:
                logging.warning(f"[{operation_name}] response None (시도 {attempt+1})")
                continue
            
            if not response.candidates:
                logging.warning(f"[{operation_name}] candidates 없음 (시도 {attempt+1})")
                if hasattr(response, 'prompt_feedback') and response.prompt_feedback:
                    feedback = response.prompt_feedback
                    logging.warning(f"  feedback: {feedback}")
                    if hasattr(feedback, 'block_reason') and str(feedback.block_reason) == 'PROHIBITED_CONTENT':
                        logging.error(f"🚫 [{operation_name}] 차단됨: PROHIBITED_CONTENT. 프롬프트를 확인하세요.")
                continue
            
            candidate = response.candidates[0]
            finish_reason = getattr(candidate, 'finish_reason', None)
            
            if finish_reason:
                fr_str = str(finish_reason)
                if 'SAFETY' in fr_str:
                    logging.warning(f"[{operation_name}] 안전 필터 (시도 {attempt+1}): {fr_str}")
                    if hasattr(candidate, 'safety_ratings'):
                         for rating in candidate.safety_ratings:
                             logging.warning(f"  {rating.category}: {rating.probability}")
                    continue
                elif 'MAX_TOKENS' in fr_str:
                     if allow_truncated and response.text and len(response.text.strip()) > 50:
                         logging.info(f"[{operation_name}] 출력 잘림 (시도 {attempt+1}) — 잘린 응답 허용")
                         return response.text.strip()
                     logging.warning(f"[{operation_name}] 출력 토큰 한도 초과 (시도 {attempt+1}) — 재시도 불가, 잘린 응답 폐기")
                     return None
                elif 'STOP' not in fr_str and 'END_TURN' not in fr_str and fr_str != '1':
                     logging.warning(f"[{operation_name}] 비정상 종료 (시도 {attempt+1}): {fr_str}")

            if response.text:
                return response.text.strip()
            
            # text 없으면 parts 직접 확인
            if hasattr(candidate, 'content') and candidate.content:
                parts = candidate.content.parts
                if parts:
                    text_parts = [p.text for p in parts if hasattr(p, 'text') and p.text]
                    if text_parts:
                        return "".join(text_parts).strip()
            
            logging.warning(f"[{operation_name}] 빈 응답 (시도 {attempt+1})")
            
        except google_exceptions.ResourceExhausted as e:
            logging.error(f"[{operation_name}] 쿼터 초과 (ResourceExhausted): {e}")
            return None
            
        except google_exceptions.ServiceUnavailable as e:
            logging.warning(f"[{operation_name}] 서비스 일시적 불가 (503): {e} - 재시도 중...")
            await asyncio.sleep(config.RETRY_DELAY_SECONDS * (attempt + 1))
            continue

        except Exception as e:
            logging.warning(
                f"[{operation_name}] API 호출 실패 (시도 {attempt + 1}/{config.MAX_RETRY_COUNT}): {e}"
            )
        
        if attempt < config.MAX_RETRY_COUNT - 1:
            await asyncio.sleep(config.RETRY_DELAY_SECONDS)
    
    logging.error(f"[{operation_name}] 모든 재시도 실패")
    return None

def safe_parse_json(text: Optional[str], expect_list: bool = False) -> Any:
    """
    AI 응답 텍스트에서 JSON 객체나 리스트를 정밀하게 찾아 파싱합니다.
    
    Args:
        text: JSON 문자열
        expect_list: True면 리스트 반환을 허용 (기본값: False - 딕셔너리 강제)
    """
    if not text:
        return [] if expect_list else {}
    
    try:
        # 마크다운 코드 블록 제거
        cleaned_text = re.sub(r"```(json)?", "", text).strip()
        cleaned_text = cleaned_text.strip("`")
        
        # JSON 시작점 찾기 ({ 또는 [)
        start_idx = -1
        for i, char in enumerate(cleaned_text):
            if char in ['{', '[']:
                start_idx = i
                break
        
        if start_idx == -1:
            return [] if expect_list else {}
        
        # 대응하는 종료점 찾기
        target_end = '}' if cleaned_text[start_idx] == '{' else ']'
        end_idx = -1
        
        for i in range(len(cleaned_text) - 1, start_idx, -1):
            if cleaned_text[i] == target_end:
                end_idx = i + 1
                break
        
        if end_idx == -1:
            return [] if expect_list else {}
        
        json_str = cleaned_text[start_idx:end_idx]
        try:
            data = json.loads(json_str)
        except json.JSONDecodeError:
            # [2026-08-01] 공용 수리기 경유. 위 슬라이싱은 바깥 괄호만 맞출 뿐,
            # 값 뒤 해설(V4=괄호 / GLM=엠대쉬 / 스트레이 콜론)은 그대로 통과시켰다.
            import bot_utils as _bu
            data = json.loads(_bu.repair_json(json_str))
        
        if expect_list:
            if isinstance(data, list):
                return data
            if isinstance(data, dict):
                # 딕셔너리로 왔지만 리스트를 기대하는 경우 감싸줌 (또는 호출처 처리 맡김)
                return [data]
            return []
            
        # 기본 모드: 딕셔너리 반환 보장
        # 리스트인 경우 첫 번째 딕셔너리 요소 반환 (LLM이 [dict]로 줄 때가 많음)
        if isinstance(data, list):
            if len(data) > 0 and isinstance(data[0], dict):
                return data[0]
            return {}
        
        if not isinstance(data, dict):
            return {}
        
        return data
    
    except json.JSONDecodeError as e:
        logging.debug(f"JSON 파싱 실패: {e}")
        return [] if expect_list else {}
    except Exception as e:
        logging.warning(f"safe_parse_json 예외: {e}")
        return [] if expect_list else {}




# =========================================================
# OTHER SYSTEM FUNCTIONS (Still in memory_system.py if needed)
# =========================================================
# (Removed large logic blocks, keeping small utilities if any were not moved)
# Assuming analyze_context_nvc and extract_updates were the main bulk.

# =========================================================
# Lore Analysis Functions (Restored)
# =========================================================

# [2026-07-18 고아 삭제] summarize_lore_for_events — 구 OOC 세대 유물(LLM 콜 3종 포함) — 현행 명령어/OOC 시스템이 대체, 재활성 계획 없음 (dead_scan 참조0 확인, git 이력 복원 가능)




        

# =========================================================
# OOC & Analysis Functions (Restored)
# =========================================================

# [2026-07-18 고아 삭제] analyze_brainstorming — 구 OOC 세대 유물(LLM 콜 3종 포함) — 현행 명령어/OOC 시스템이 대체, 재활성 계획 없음 (dead_scan 참조0 확인, git 이력 복원 가능)


# [2026-07-18 고아 삭제] check_narrative_consistency — 구 OOC 세대 유물(LLM 콜 3종 포함) — 현행 명령어/OOC 시스템이 대체, 재활성 계획 없음 (dead_scan 참조0 확인, git 이력 복원 가능)



# =========================================================
# OOC Memory Edit Functions
# =========================================================

async def process_ooc_memory_edit(
    client: genai.Client, 
    model_id: str, 
    ooc_content: str, 
    ai_mem: Dict[str, Any], 
    p_data: Dict[str, Any],
    notebook_text: str = "",
    declared_block: str = "",
    relations_state: Optional[Dict[str, Any]] = None,
    sheet_sections: Optional[Dict[str, str]] = None,
    roster: Optional[List[Dict[str, Any]]] = None,
    edit_part: str = "",
    world_state: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    사용자의 OOC 요청을 해석하여 캐릭터 메모리 수정 명령을 생성합니다.
    (Notebook 지원 추가)
    """
    # [2026-09-16 시트 2차] 서술 편집 = PC 페이지 lore 절 편집(field = 절 이름). ai_memory 서술 키 없음.
    # [2026-10-16 C44 O1] 상태는 둘 — 좋은 상태(status_effects)·타격칸(strikes, C42). 옛: 타격칸이 안 보여
    #   "출혈 지워줘"가 status_effects 로 나가 아무것도 안 지웠다.
    from game_character import buff_effects as _bf_ooc, strike_names as _sk_ooc
    current_state = {
        "sheet": sheet_sections or {},
        "passives": ai_mem.get("passives", []),
        "status_effects": [e.get("name") for e in _bf_ooc(p_data or {}) if isinstance(e, dict)],
        "strikes": _sk_ooc(p_data or {}),
        # [2026-09-15 관계 통합] ai_memory.relationships 삭제 — 관계는 NPC→이 PC 엣지(bond/tension/stance).
        "relations": relations_state or {},
        # [2026-09-26 O5] 명부 — 별명·호칭("아빠")을 등록 이름으로 잇는 재료(시트에서 PC 를 가리키는 문장 1개).
        "roster": roster or [],
    }
    # [2026-10-10 OOC 확장] 지금 장소·시간·날씨, 장소 이름들, 본문에 나온 인물이 아는 것(ooc_edits.world_state_for_edit).
    if isinstance(world_state, dict):
        for _wk in ("world", "places", "npc_knows", "threads", "quests", "corrections"):   # [2026-10-10 O-E] +스레드·퀘스트 · [C50] +정정
            if world_state.get(_wk):
                current_state[_wk] = world_state[_wk]
    
    system_prompt = (
        "You are a Game Master Assistant handling OOC (Out-Of-Character) requests.\n"
        "Interpret the user's request and generate specific edits to the character data or Notebook.\n"
        "Supports items, memo lines, relationships, passives, conditions, descriptions, etc.\n\n"
        # [2026-10-16 C44 O2] 소지품 = [소지품] 칸(field=item), 메모 = [메모] 줄(append/remove/edit). 옛 문안은 아이템을
        #   notebook append 로 보내 [메모]에 영어 줄로 쌓였고(판정·상태창이 못 봄), 폐지된 replace 를 권했다.
        "### Items, memo, gold\n"
        "Items the PC carries are the notebook's [소지품] list: field='item', action add|remove, value = the item name in Korean "
        "(when removing, the name as written in [소지품]), qty = a number when the user says how many "
        "(remove without qty takes the whole item).\n"
        "Memo lines are the notebook's [메모]: field='notebook', action append (value = one Korean line) | remove "
        "(value = words from the line) | edit (old = the words to replace, new = what replaces them). "
        "A misspoken name inside memo lines is fixed with edit.\n"
        "Gold or money is the declared value when the Declared State block has one (field='declared'); "
        "otherwise it is an item with qty.\n\n"
        
        "Output Format (JSON):\n"
        "{\n"
        "  \"interpretation\": \"What the user wants (Korean)\",\n"
        "  \"edits\": [\n"
        "    {\"field\": \"item\", \"action\": \"add\", \"value\": \"성검\", \"qty\": 1},\n"
        "    {\"field\": \"relation\", \"action\": \"set\", \"key\": \"RosterName\", \"stands\": \"friendly\", \"friction\": \"low\", \"baseline\": false},\n"
        "    {\"field\": \"strikes\", \"action\": \"remove\", \"value\": \"출혈\"}\n"
        "  ],\n"
        "  \"confirmation_message\": \"Response to user (Korean)\"\n"
        "}\n"
        "Valid fields: relation, npc, place, time, fact, thread, quest, passives, status_effects, strikes, item, notebook, or a character-sheet section name "
        "(Identity, Core Traits, Aside, Direction, Relationships, Secrets, Background, Notes — the keys of "
        "Current State.sheet) with action set|append|remove and value = that section's text.\n"
        "passives = sheet fragments: action add|set|remove. add/set value = {\"name\", \"desc\" (Korean, conditions in words), "
        "\"value\": {\"edge\": 1} for a help or {\"edge\": -1} for a hindrance}. "   # [2026-10-15 C42] 조각 = 이름 판단, 부호 하나
        "remove value = name.\n"
        # [2026-10-16 C44 O1] 타격칸(C42) — 나쁜 상태 네 칸. status_effects 는 좋은 상태만.
        "strikes = the PC's four strike slots for bad conditions (wounds, bleeding, poison, fear, panic…), listed in "
        "Current State.strikes: action add|remove, value = the condition name in Korean. "
        "status_effects = good conditions only (blessing, focus…): action add|remove, value = the name.\n"
        "relation = how an NPC stands toward this PC: action set|remove, key = the NPC's name exactly as in Current State.roster "
        "(map nicknames and kinship words like '아빠' through the roster's sheet_on_pc lines; never invent a name). "
        "stands = devoted/friendly/neutral/unfriendly/hostile and friction = low/strained/high/breaking — use these words "
        "unless the user gave numbers, then bond -100~+100 / tension 0~100. baseline = true when the user says this is how "
        "they have always stood (the starting relation), not a change now. stance (English telegraphic: what they want "
        # [2026-10-11 청소 C11-D] stance = PC에게서 원하는 것(10-08 Theoria stance 정의와 같은 말). 옛 문안은 "관찰 가능한 행동만".
        "from the PC). Include only the parts the user asked to change.\n"
        "Relationships (a sheet section) is the player's own written text about the PC's ties; how an NPC stands toward "
        "the PC is field=relation, never that section.\n"
        # [2026-10-10 OOC 확장] 인물끼리 관계·인물·자리·시간 — 적용은 ooc_edits(명령어와 같은 함수). 스펙 state_v10/OOC확장_스펙_2026-10-10.md
        "relation with toward = how one NPC stands toward ANOTHER NPC: {\"field\":\"relation\",\"action\":\"set|remove\","
        "\"key\":\"A\",\"toward\":\"B\",\"kind\":\"alliance|rivalry|fear|respect|distrust|affection|debt|mentor|grudge|neutral\","
        "\"strength\":\"faint|clear|strong\",\"stance\":\"...\"}. Without toward, relation is toward this PC.\n"
        "npc = a fact about an NPC on the roster, one op per edit, key = the roster name:\n"
        "- merge {\"key\":\"the duplicate entry\",\"into\":\"the person it really is\"} when two roster entries are the same person\n"
        "- alias {\"key\",\"value\":\"another name for them\"} · rename {\"key\",\"value\":\"their new name\"}\n"
        "- status {\"key\",\"value\":\"active|down|dead\"} (alive again = active)\n"
        "- sheet {\"key\",\"section\":<a section name above>,\"action\":\"set|append|remove\",\"value\"} for that NPC's sheet, "
        "what they hide included (Secrets)\n"
        "- knows {\"key\",\"action\":\"add|remove\",\"value\"} what the NPC knows; remove = a line from Current State.npc_knows\n"
        "A bare section name is the PC's own sheet; an NPC's sheet goes through field=npc op=sheet.\n"
        "place = where people are now: {\"field\":\"place\",\"op\":\"pc\",\"value\":\"place\"} the PC; "
        "{\"op\":\"npc_in\",\"key\",\"value\":\"place or here\"} an NPC is there; {\"op\":\"npc_out\",\"key\"} an NPC is not in this scene. "
        "Use a name from Current State.places when one fits.\n"
        "time = the clock as it stands now: {\"field\":\"time\",\"slot\"?,\"hour\"?,\"minute\"?,\"year\"?,\"month\"?,\"day\"?,\"weather\"?} "
        "for a correction of the current time or weather; moving the story forward in time belongs to the scene, not here.\n"
        # [2026-10-10 기록 정정] 지난 일 — 정오표(corrections). 상태 칸과 함께 낼 수 있다.
        "fact = a correction of something that already happened in the story record: "
        "{\"field\":\"fact\",\"about\":[\"roster names or key nouns\"],\"value\":\"what is true (Korean, one sentence)\","
        "\"wrong\":\"the recorded version being corrected\"}. "
        # [2026-10-18 C50 R5] 서 있는 정정 목록(Current State.corrections)을 보고 번호로 바꾸고 거둔다 — 레티어스 "ooc는 고치라고 하는거라".
        "The player's correction is the instruction. Current State.corrections lists the corrections already standing (id, turn, about, text): "
        "when the player restates or changes one of them, add \"replaces\":id (also when the names differ); "
        "when it is another matter about the same person, add \"new\":true; "
        "withdraw one with {\"field\":\"fact\",\"action\":\"remove\",\"id\":N} "
        "(with no list shown, {\"field\":\"fact\",\"action\":\"remove\",\"value\"} and its words). "
        "The current state goes to its own field (npc status, place, time, relation, thread, quest); when the player corrects both "
        "what happened and how things stand now, emit both.\n"
        # [2026-10-10 이야기 실 O-E] 스레드 장부(약속·사안)·퀘스트 보드. 스펙 state_v10/이야기실_지도·스펙_2026-10-10.md
        "thread = a promise or open matter standing between people (Current State.threads, named by id): "
        "{\"field\":\"thread\",\"op\":\"open|close|pause|resume|reschedule|progress|void\",\"id\":\"T3\"}. "
        "open a new one with {\"title\":\"짧은 한국어 라벨\",\"kind\":\"promise|matter\",\"parties\":[names],\"due\":{...}|null}; "
        "close with \"outcome\": done (kept, finished) | dropped (called off) | broken (breached); "
        "reschedule with \"due\": {\"day_offset\":1,\"slot\":\"저녁\",\"hour\":null,\"minute\":null} or "
        "{\"year\",\"month\",\"day_in_month\"}; progress with \"remaining\" (what is still owed, Korean); "
        "void = it never existed (a wrong entry); a \"title\" next to an id renames it.\n"
        "quest = the quest board, goals the PC pursues (Current State.quests; not threads): "
        "{\"field\":\"quest\",\"op\":\"add|complete|remove|progress\",\"value\":\"the quest name as listed\",\"rank\"?,\"delta\"?}; "
        "complete = it is finished, remove = it is not a quest at all.\n"
    )

    # [2026-09-10 P13] 선언 칸 한 줄. 옛 7필드 문안은 위에서 한 글자도 안 바뀌었다 —
    #   이 블록은 그 채널에 **선언이 있을 때만** 붙는다(없으면 프롬프트도 종전 그대로).
    if declared_block:
        system_prompt += (
            "\n### Declared State (this channel)\n"
            "The block below lists what this world declares. To change a declared value, use\n"
            "field='declared':\n"
            '{"field":"declared","name":"금","op":"set|delta|add|remove|rename|append",'
            '"value":50,"item":"양파","to":"당근","record":"수분"}\n'
            "- op set/delta: a number (gauge/counter) or a stage name (enum).\n"
            "- A value kept per person (several names under 현재) takes \"npc\": the roster name for an NPC's own value.\n"
            "- Lists hold records: use item=<record name>, record=<field name> for a field,\n"
            "  add/remove to create or destroy the record itself.\n"
            "- **rename vs remove+add**: if the user is CORRECTING a name for the same thing\n"
            "  (\"the carrot was actually an onion\"), use rename (item -> to) — the record keeps\n"
            "  every field. If it is a NEW thing in that slot (\"replanted\"), emit remove then\n"
            "  add — the new record starts from its declared initial values.\n"
            "- op append: add one line to a log section (record-keeping sections only).\n"
            "- Values only. Rules, ranges, formats and transitions are edited in files, not OOC.\n"
            "- Never invent a name that is not in the block.\n"
            + declared_block + "\n"
        )
    
    user_prompt = f"Current State: {json.dumps(current_state, ensure_ascii=False)}\nNotebook:\n{notebook_text}\n\nOOC Request: {ooc_content}"
    # [2026-09-26 O2] 판정(루카 표지·분류 콜)이 떼어 준 수정 조각 — 해석의 권위는 원문, 조각은 어디까지가 수정인지의 선.
    if edit_part and edit_part.strip() and edit_part.strip() != (ooc_content or "").strip():
        user_prompt += f"\nEdit part (only this portion changes data; the rest is not an edit): {edit_part.strip()}"

    try:
        config = types.GenerateContentConfig(
            system_instruction=text_resources.ANALYSIS_FRAME,   # [2026-10-06 분석 틀] 원본 mandate·지어낸 대화 삭제
            response_mime_type="application/json",
            temperature=0.1
        )
        contents = [
            types.Content(role="user", parts=[types.Part(text=f"{system_prompt}\n\n{user_prompt}")])
        ]

        result = await api_call_with_retry(client, model_id, contents, config, operation_name="OOC Edit")
        if result:
            return safe_parse_json(result)
    except Exception as e:
        logging.error(f"[OOC Edit] Failed: {e}")
        
    return {"interpretation": "Error", "edits": []}


async def route_ooc_request(client, model_id: str, ooc_text: str,
                            names: Optional[List[str]] = None) -> Optional[Dict[str, str]]:
    """[2026-09-26 O1] 행동+OOC 전용 짧은 분류 콜 → {"edit": 수정 조각, "directive": 장면 지시 조각}. 실패 None.
    (단독 OOC·루카 모드는 루카 콜이 표지로 판정을 겸한다 — 이 콜은 루카가 안 도는 자리 하나만.)"""
    if not ooc_text or not str(ooc_text).strip():
        return None
    _names = ", ".join([str(n) for n in (names or []) if n][:40]) or "(none)"
    prompt = (
        "Split one OOC note a TRPG player attached to their in-character turn.\n"
        "edit = the part asking to change stored data: how an NPC stands toward the PC or toward another NPC, "
        "who an NPC is (the same person as another entry, another name, alive or dead, their sheet, what they know), "
        "where the PC or an NPC is right now, the current time or weather, the PC sheet, sheet fragments, "
        "status effects or strike slots (wounds, conditions), notebook/items/gold, a declared value, a promise, an open matter or a quest that is open, "
        "done, called off or never existed, or a correction of what already happened in the story "
        "record. A statement of how a relation or fact stands "
        "(\"X has always cared for me\", \"it is night now\", \"X never died, he passed out\") is an edit. Keep the player's words.\n"
        "directive = the part asking this scene or the narration to do something (skip, move, describe, assume).\n"
        "Anything else (questions, chat) belongs to neither.\n"
        f"Known names: {_names}\n"
        f"OOC note: {str(ooc_text).strip()}\n"
        'Return JSON only: {"edit": "", "directive": ""}'
    )
    try:
        cfg = types.GenerateContentConfig(
            system_instruction=text_resources.ANALYSIS_FRAME,   # [2026-10-06 분석 틀] 
            response_mime_type="application/json",
            temperature=0.1,
        )
        contents = [types.Content(role="user", parts=[types.Part(text=prompt)])]
        result = await api_call_with_retry(client, model_id, contents, cfg, operation_name="OOC Route")
        parsed = safe_parse_json(result) if result else None
    except Exception as e:
        logging.warning(f"[OOC Route] Failed: {e}")
        return None
    if not isinstance(parsed, dict):
        return None
    return {"edit": str(parsed.get("edit") or "").strip(), "directive": str(parsed.get("directive") or "").strip()}


def apply_memory_edits(
    ai_mem: Dict[str, Any],
    edits: List[Dict[str, Any]],
    p_data: Dict[str, Any],
    report: Optional[List[str]] = None,
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """
    실제로 메모리 수정 사항을 적용합니다.
    ai_mem과 p_data(status_effects·strikes 등)를 모두 수정하여 반환합니다.

    [2026-10-16 C44 O3] report(list)를 주면 편집마다 되비침 한 줄을 붙인다 — 실패·변화 없음은 ⚠ 로 시작.
      호출부(run_ooc_edit)는 ⚠ 아닌 줄만 적용 수로 센다(옛: len(edits)를 그대로 세어, 아무것도 안 바뀐 편집에도
      모델이 미리 쓴 "수정 완료"가 나갔다). 모르는 칸도 ⚠ 줄(옛: 조용히 무시 + 적용 1).
    [2026-10-16 C44 O1] 상태는 C42 이후 둘 — 나쁜 상태 = 타격칸(p_data["strikes"] 4칸), 좋은 상태 = status_effects.
      field="strikes" = 타격칸 add/remove. field="status_effects" add 는 알려진 디버프면 타격칸으로, 아니면 좋은 상태(buff)로
      (출력물 콜 orchestration._apply_status_changes 와 같은 규칙 — 옛 OOC 는 모르는 이름을 debuff 로 받아 아무 효과 없이
      남았다가 다음 타격칸 변화 때 걷혔다). remove 는 타격칸에 그 이름이 있으면 거기서 뺀다.
    """
    import copy
    from game_character import (normalize_status_effects, normalize_fragment, is_known_debuff,
                                apply_strike_changes, strike_names)
    new_mem = copy.deepcopy(ai_mem)
    new_p_data = copy.deepcopy(p_data)
    rep = report if isinstance(report, list) else []

    def _name_of(v, k=None) -> str:
        if isinstance(v, dict):
            v = v.get("name") or v.get("tag") or k
        return str(v if v is not None else (k or "")).strip()

    def _strike_add(nm: str) -> str:
        nonlocal new_p_data
        if nm in strike_names(new_p_data):
            return f"⚠ 타격: {nm}은 이미 칸에 있어"
        new_p_data, _ch, _over = apply_strike_changes(new_p_data, add=[nm])
        return f"⚠ 타격: 칸이 꽉 찼어({nm} 못 넣음)" if _over else f"타격: +{nm}"

    def _strike_clear(nm: str) -> str:
        nonlocal new_p_data
        new_p_data, _ch, _over = apply_strike_changes(new_p_data, clear=[nm])
        return f"타격: −{nm}"

    for edit in edits or []:
        if not isinstance(edit, dict):
            continue
        field = edit.get("field")
        action = str(edit.get("action") or "").strip().lower()
        value = edit.get("value")
        key = edit.get("key")

        # [2026-09-16 시트 2차] 시트 절 편집(field=절 이름)은 호출부가 페이지 편집으로 분리한다 — 여기 안 온다.
        # [2026-09-15 관계 통합] field="relation"은 호출부(command_handler)가 엣지 편집으로 분리한다 — 여기 안 온다.
        if field in ("strikes", "strike"):
            nm = _name_of(value, key)
            if not nm:
                rep.append("⚠ 타격: 이름이 없어")
            elif action in ("add", "set", "append"):
                rep.append(_strike_add(nm))
            elif action in ("remove", "clear", "delete"):
                rep.append(_strike_clear(nm) if nm in strike_names(new_p_data) else f"⚠ 타격: {nm}은 칸에 없어")
            else:
                rep.append(f"⚠ 타격: '{action or '?'}'은 못 해(add·remove)")
            continue

        if field == "status_effects":
            target = normalize_status_effects(new_p_data.get("status_effects", []))

            def _find_idx(name_or_tag: str):
                for i, item in enumerate(target):
                    if item.get("tag") == name_or_tag or item.get("name") == name_or_tag:
                        return i
                return None

            nm = _name_of(value, key)
            if action == "add":
                if not nm:
                    rep.append("⚠ 상태: 이름이 없어")
                    continue
                if is_known_debuff(nm):
                    _r = _strike_add(nm)
                    rep.append(_r + (" (나쁜 상태라 타격칸으로)" if not _r.startswith("⚠") else ""))
                    continue
                _known = (nm in getattr(config, "STATUS_EFFECTS", {}) or nm in getattr(config, "LEGACY_TAG_MAP", {})
                          or nm in getattr(config, "STATUS_TAGS", {}))
                _src = (value if isinstance(value, dict) else nm) if _known else \
                    {"name": nm, "tag": nm, "type": "buff", "severity": 0, "modifiers": {"judgment": 5}}
                new_effects = normalize_status_effects([_src])
                if not new_effects:
                    rep.append(f"⚠ 상태: {nm} — 넣지 못했어")
                    continue
                new_eff = new_effects[0]
                idx = _find_idx(new_eff.get("tag") or new_eff.get("name", ""))
                if idx is None:
                    target.append(new_eff)
                else:
                    target[idx] = new_eff
                new_p_data["status_effects"] = target
                rep.append(f"상태: +{new_eff.get('name') or nm}")
            elif action == "remove":
                if nm and nm in strike_names(new_p_data):
                    rep.append(_strike_clear(nm))
                    continue
                idx = _find_idx(nm)
                if idx is None and nm in getattr(config, "LEGACY_TAG_MAP", {}):
                    idx = _find_idx(config.LEGACY_TAG_MAP[nm])
                if idx is None:
                    rep.append(f"⚠ 상태: {nm or '?'}은 없어")
                    continue
                target.pop(idx)
                new_p_data["status_effects"] = target
                rep.append(f"상태: −{nm}")
            elif action in ["set", "update"] and key:
                idx = _find_idx(str(key))
                new_effects = normalize_status_effects([value]) if value is not None else []
                if not new_effects:
                    rep.append(f"⚠ 상태: {key} — 바꿀 값이 없어")
                    continue
                if idx is not None:
                    target[idx] = new_effects[0]
                else:
                    target.append(new_effects[0])
                new_p_data["status_effects"] = target
                rep.append(f"상태: {key} 고침")
            else:
                rep.append(f"⚠ 상태: '{action or '?'}'은 못 해(add·remove)")

        elif field == "passives":
            # [2026-09-16 3차] 조각 편집 — add/set 은 새 모양으로 접어 이름 기준 교체, remove 는 이름.
            #   OOC 로 넣은 조각은 작가 편집이라 기본 origin=sheet(정리 콜이 못 덮는다).
            target = [f for f in (normalize_fragment(x, "sheet") for x in (new_mem.get("passives") or [])) if f]
            if action in ("add", "set", "update", "replace"):
                _raw = value if isinstance(value, dict) else ({"name": value} if isinstance(value, str) else None)
                if isinstance(_raw, dict) and key and not _raw.get("name"):
                    _raw = dict(_raw, name=key)
                frag = normalize_fragment(_raw, "sheet") if _raw is not None else None
                if not frag:
                    rep.append("⚠ 조각: 이름이 없어")
                    continue
                _old = key if (action != "add" and key) else frag["name"]
                idx = next((i for i, x in enumerate(target) if x["name"] in (_old, frag["name"])), None)
                if idx is None:
                    target.append(frag)
                    rep.append(f"조각: +{frag['name']}")
                else:
                    target[idx] = frag
                    rep.append(f"조각: {frag['name']} 고침")
            elif action == "remove":
                _nm = value.get("name") if isinstance(value, dict) else (value if value is not None else key)
                _nm = str(_nm or "").strip()
                _before = len(target)
                target = [x for x in target if x["name"] != _nm]
                rep.append(f"조각: −{_nm}" if len(target) < _before else f"⚠ 조각: '{_nm or '?'}'은 없어")
            else:
                rep.append(f"⚠ 조각: '{action or '?'}'은 못 해(add·set·remove)")
                continue
            new_mem["passives"] = target

        elif field in ["known_info", "foreshadowing"]:
            target = new_mem.get(field, [])
            if field not in new_mem:
                new_mem[field] = []
                target = new_mem[field]
            _snap = copy.deepcopy(target)

            if action == "add":
                # [V6.1 Fix] Deduplication for dict-based passives
                if isinstance(value, dict) and "name" in value:
                    name = value["name"]
                    exists = False
                    for i, item in enumerate(target):
                        if isinstance(item, dict) and item.get("name") == name:
                            target[i] = value # Update
                            exists = True; break
                        elif str(item) == name:
                            target[i] = value # Replace legacy string with dict
                            exists = True; break
                    if not exists: target.append(value)
                elif value not in target:
                    target.append(value)

            elif action == "remove":
                if value in target:
                    target.remove(value)
                else:
                    # [V6.1 Fix] Name-based removal for dict items
                    for i, item in enumerate(target):
                        if isinstance(item, dict) and item.get("name") == value:
                            target.pop(i); break

            elif action in ["set", "update"] and key:
                # [V6.1 Fix] Update specific item by name/original value
                for i, item in enumerate(target):
                    if (isinstance(item, dict) and item.get("name") == key) or (str(item) == key):
                        target[i] = value
                        break

            new_mem[field] = target
            rep.append(f"{field}: {action}" if target != _snap else f"⚠ {field}: 바뀐 게 없어")

        else:
            rep.append(f"⚠ '{field or '?'}'은 여기서 못 고쳐")

    # [2026-08-11 비일상적응도 삭제] abnormal_exposure 편집 분기 + 레거시 normalization
    # 리다이렉트 분기 제거. OOC로만 값이 들어가고 플레이 중 갱신하는 코드는 없던 필드라
    # 스키마·프롬프트 필드 목록·예시 JSON도 같이 철거. 복원은 git 이력.
    return new_mem, new_p_data


def removed_play_fragments(old_mem: Dict[str, Any], new_mem: Dict[str, Any]) -> List[Dict[str, Any]]:
    """[2026-09-16 3차] OOC 편집 전후 비교 — 사라진 origin=play 조각(이름 기준). 가역 되돌림 재료."""
    new_names = {str(p.get("name")) for p in (new_mem or {}).get("passives", []) or [] if isinstance(p, dict)}
    return [p for p in (old_mem or {}).get("passives", []) or []
            if isinstance(p, dict) and p.get("origin") == "play" and str(p.get("name")) not in new_names]


# =========================================================
# Session Memory Update (Left Brain to World State)
# ⚠ 미배선 (2026-07-06 감사): apply_ai_memory_updates 호출자 0 — 이름 유사한
# apply_memory_edits(OOC 기억 편집)가 실사용 함수. 혼동 주의.
# =========================================================

# [2026-07-18 고아 삭제] apply_ai_memory_updates — 구 OOC 세대 유물(LLM 콜 3종 포함) — 현행 명령어/OOC 시스템이 대체, 재활성 계획 없음 (dead_scan 참조0 확인, git 이력 복원 가능)


# =========================================================
# ENTITY EXTRACTION (Restored/New)
# =========================================================






