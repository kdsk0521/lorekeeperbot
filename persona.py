"""
Lorekeeper TRPG Bot - Persona Module (Right Hemisphere)
창작, 서사, 캐릭터 연기를 담당하는 '우뇌' 모듈입니다.
memory_system.py(좌뇌)가 분석한 결과를 바탕으로 서사를 생성합니다.

Architecture:
    - Left Hemisphere (memory_system.py): Logic, Analysis, Causality Calculation
    - Right Hemisphere (persona.py): Creativity, Narrative, Character Acting

Prompt Order (SillyTavern Preset Style):
    1. AI Mandate & Core Constraints
    2. The Axiom Of The World
    3. <Lore> 로어북 </Lore>
    4. <Roles> 페르소나 프롬프트, 캐릭터 설명 </Roles>
    5. <Fermented> 에피소드 요약, 장기 기억 </Fermented>
    6. <Immediate> 과거 챗 </Immediate>
    7. =====CACHE BOUNDARY=====
    8. <Scripts> 작노, 글노, 최종 삽입 프롬프트 </Scripts>
    9. # Core Models
    10. <Current-Context> 최근 챗 </Current-Context>
    11. <유저 메시지> / OOC
    12. Output Generation Request
    13. 언어 출력 교정
"""

import asyncio
import logging
import re
import types as python_types
from typing import Optional, List, Dict, Any, Tuple, Union
from google import genai
from google.genai import types
import config
import fermentation

# Response processing functions (분리된 모듈에서 import)
from response_processor import (
    detect_scene_type_keywords,
    detect_pc_impersonation,
    filter_pc_impersonation,
)

# Prompt building functions (분리된 모듈에서 import)
from prompt_builder import (
    PromptBuilder,
    build_length_instruction,
    build_combined_directive,
    build_mature_content_prompt,
    get_scene_type_description,
    get_available_genres,
    get_genre_description,
    construct_system_prompt,
    SCENE_TYPES,
    GENRE_DEFINITIONS,
    DEFAULT_MIN_RESPONSE_LENGTH,
    DEFAULT_MAX_RESPONSE_LENGTH,
)
import text_resources

# =========================================================
# 상수 정의
# =========================================================
DEFAULT_TEMPERATURE = 1.0
MIN_NARRATIVE_LENGTH = 1500  # 최소 서사 길이 (문자)

# NOTE: 응답 길이 설정 및 build_length_instruction()은 prompt_builder.py로 이동됨
# NOTE: 모든 텍스트 리소스는 text_resources.py로 이동됨.

# =========================================================
# ChatSessionAdapter 클래스
# =========================================================
class ChatSessionAdapter:
    """
    Gemini API와의 대화 세션을 관리하는 어댑터입니다.
    """
    def __init__(
        self,
        client,
        model: str,
        history: List[types.Content],
        config: types.GenerateContentConfig
    ):
        self.client = client
        self.model = model
        self.history = history
        self.config = config

    def _trim_history(self):
        """히스토리가 너무 커지면 오래된 메시지 제거"""
        MAX_HISTORY_MESSAGES = getattr(config, "MAX_HISTORY_LENGTH", 2000) # Sync with global config
        MAX_HISTORY_CHARS = 100000 # [Anti-Gravity] Expanded Context

        # 초기 2개 메시지 (시스템 초기화)는 유지
        if len(self.history) <= 2:
            return
        
        # 문자 수 제한 (우선도 높음 - 먼저 확인)
        total_chars = sum(
            len(p.text) for c in self.history for p in c.parts if hasattr(p, 'text') and p.text
        )
        while total_chars > MAX_HISTORY_CHARS and len(self.history) > 2:
            # 항상 인덱스 2 (초기화 후 첫 메시지)부터 삭제
            removed = self.history.pop(2)
            removed_chars = sum(len(p.text) for p in removed.parts if hasattr(p, 'text') and p.text)
            total_chars -= removed_chars
            logging.debug(f"[History] 문자 수 초과, {removed_chars}자 제거 (현재: {total_chars}/{MAX_HISTORY_CHARS})")
        
        # 메시지 수 제한
        while len(self.history) > MAX_HISTORY_MESSAGES and len(self.history) > 2:
            # 항상 인덱스 2부터 삭제
            removed = self.history.pop(2)
            logging.debug(f"[History] 메시지 수 초과, 오래된 메시지 제거 (남은 메시지: {len(self.history)})")

    async def send_message(self, content: str) -> Optional[types.GenerateContentResponse]:
        """
        메시지를 전송하고 응답을 받습니다. (히스토리 관리 포함)
        """
        self._trim_history() # 전송 전 트림
        
        self.history.append(
            types.Content(role="user", parts=[types.Part(text=content)])
        )
        
        try:
            # 히스토리 상세 로깅
            total_chars = sum(
                len(p.text) for c in self.history for p in c.parts if hasattr(p, 'text') and p.text
            )
            logging.debug(f"[ChatSession] 히스토리: {len(self.history)}msgs, ~{total_chars}chars")

            response = await self.client.aio.models.generate_content(
                model=self.model,
                contents=self.history,
                config=self.config
            )
            
            # 응답 상세 로깅
            cand_count = len(response.candidates) if response and response.candidates else 0
            if response:
                logging.debug(f"[ChatSession] response 수신, candidates: {cand_count}")
            else:
                logging.warning("[ChatSession] response가 None")
            
            if response and response.text:
                model_content = types.Content(
                    role="model",
                    parts=[types.Part(text=response.text)]
                )
                self.history.append(model_content)
            
            return response
            
        except Exception as e:
            logging.error(f"ChatSession.send_message 오류: {e}")
            if self.history and self.history[-1].role == "user":
                self.history.pop()
            raise


# NOTE: PromptBuilder 클래스 및 construct_system_prompt는 prompt_builder.py로 이동됨 (상단에서 import)


# =========================================================
# 세션 생성 (프리셋 순서 적용)
# =========================================================
def create_risu_style_session(
    client: genai.Client,
    model_version: str,
    lore_text: str,
    rule_text: str = "",
    active_genres: Optional[List[str]] = None,
    custom_tone: Optional[str] = None,
    deep_memory: str = "",
    fermented_summary: str = "",
    character_descriptions: str = "",
    scene_type: Optional[str] = None,
    player_name: str = "",
    player_desc: str = "",
    nvc_summary: str = ""
) -> ChatSessionAdapter:
    """
    RisuAI/SillyTavern 스타일의 세션을 생성합니다.
    프리셋 순서에 맞게 프롬프트를 조립합니다.
    """
    builder = PromptBuilder()
    
    # 프롬프트 구성
    builder.set_genres(active_genres)
    builder.set_custom_tone(custom_tone)
    builder.set_scene_type(scene_type)  # 장면 유형 설정
    builder.set_lore(lore_text, rule_text)
    builder.set_player_info(player_name, player_desc)
    builder.set_roles(character_descriptions)
    builder.set_fermented(fermented_summary, deep_memory)
    builder.set_cognition_data(nvc_summary)
    
    system_prompt = builder.build_system_prompt()
    
    # 초기화 메시지
    init_context = f"""
{system_prompt}

<Initialization>
[SYSTEM] Narrative Protocol Online.
Observing Macroscopic States.
The world is asynchronous—it does not wait.
Recording in Korean.
</Initialization>
"""
    
    initial_history = [
        types.Content(
            role="user",
            parts=[types.Part(text=init_context)]
        ),
        types.Content(
            role="model",
            parts=[types.Part(text="[SYSTEM] Standing by. Awaiting observable events.")]
        )
    ]
    
    gen_config = types.GenerateContentConfig(
        temperature=DEFAULT_TEMPERATURE,
        safety_settings=config.SAFETY_SETTINGS,
        tools=[],
        automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
        # Aggressively disable AFC
        tool_config=types.ToolConfig(
            function_calling_config=types.FunctionCallingConfig(
                mode=types.FunctionCallingConfigMode.NONE
            )
        )
    )
    
    return ChatSessionAdapter(
        client=client,
        model=model_version,
        history=initial_history,
        config=gen_config
    )


# =========================================================
# 응답 생성 (재시도 포함)
# =========================================================
async def generate_response_with_retry(
    client: genai.Client,
    chat_session: ChatSessionAdapter,
    user_input: str
) -> str:
    """
    재시도 로직을 포함하여 응답을 생성합니다.
    """
    min_length = DEFAULT_MIN_RESPONSE_LENGTH
    max_length = DEFAULT_MAX_RESPONSE_LENGTH
    
    length_instruction = build_length_instruction()
    
    hidden_reminder = (
        f"\n\n{length_instruction}\n"
        f"(System Reminder: Record observable Macroscopic States only. "
        f"The world continues asynchronously.)"
    )
    full_input = user_input + hidden_reminder
    
    best_response = None
    best_length = 0
    
    for attempt in range(config.MAX_RETRY_COUNT):
        try:
            response = await chat_session.send_message(full_input)
            
            # ===== [NEW] 상세 응답 진단 =====
            if response is None:
                logging.warning(f"[시도 {attempt+1}] response 객체 자체가 None")
                continue
            
            # 후보 확인
            if not response.candidates:
                logging.warning(f"[시도 {attempt+1}] candidates 없음")
                # prompt_feedback 확인
                if hasattr(response, 'prompt_feedback') and response.prompt_feedback:
                    feedback = response.prompt_feedback
                    logging.warning(f"  prompt_feedback: {feedback}")
                    if hasattr(feedback, 'block_reason') and str(feedback.block_reason) == 'PROHIBITED_CONTENT':
                        logging.error("🚫 [CRITICAL] Prompt blocked by PROHIBITED_CONTENT filter. Check guidelines/lore.")
                continue
            
            candidate = response.candidates[0]
            
            # finish_reason 확인
            finish_reason = getattr(candidate, 'finish_reason', None)
            if finish_reason:
                finish_reason_str = str(finish_reason)
                if 'SAFETY' in finish_reason_str:
                    logging.warning(f"[시도 {attempt+1}] 안전 필터 차단: {finish_reason_str}")
                    # 안전 등급 확인
                    if hasattr(candidate, 'safety_ratings'):
                        for rating in candidate.safety_ratings:
                            logging.warning(f"  {rating.category}: {rating.probability}")
                    continue
                elif 'MAX_TOKENS' in finish_reason_str:
                    logging.warning(f"[시도 {attempt+1}] 토큰 한계 도달")
                elif finish_reason_str not in ['STOP', 'END_TURN', '1']: # 1 is often STOP
                     # Just log, don't necessarily skip if text exists
                    logging.warning(f"[시도 {attempt+1}] 종료 사유: {finish_reason_str}")
            
            response_text = None
            if response.text:
                response_text = response.text
            else:
                logging.warning(f"[시도 {attempt+1}] text 속성 비어있음")
                # content.parts 직접 확인
                if hasattr(candidate, 'content') and candidate.content:
                    parts = candidate.content.parts
                    if parts:
                        text_parts = [p.text for p in parts if hasattr(p, 'text') and p.text]
                        if text_parts:
                            response_text = "".join(text_parts)
                            logging.info(f"[시도 {attempt+1}] parts에서 텍스트 복구: {len(response_text)}자")

            if response_text:
                response_length = len(response_text)
                
                if response_length >= min_length:
                    logging.info(f"[Length] OK: {response_length}자")
                    return response_text
                else:
                    logging.warning(
                        f"[Length] SHORT: {response_length}자 < {min_length}자 "
                        f"(시도 {attempt + 1}/{config.MAX_RETRY_COUNT})"
                    )
                    
                    if response_length > best_length:
                        best_response = response_text
                        best_length = response_length
                    
                    if attempt < config.MAX_RETRY_COUNT - 1:
                        full_input = (
                            f"{user_input}\n\n"
                            f"⚠️ **[LENGTH WARNING]** Previous response was {response_length} chars. "
                            f"MUST write at least {min_length} chars. "
                            f"Add more sensory details, NPC reactions, and environmental descriptions.\n"
                            f"{hidden_reminder}"
                        )
            else:
                logging.warning(f"빈 응답 (텍스트 복구 실패) (시도 {attempt + 1}/{config.MAX_RETRY_COUNT})")
                
        except Exception as e:
            logging.warning(f"응답 생성 실패 (시도 {attempt + 1}/{config.MAX_RETRY_COUNT}): {e}")
        
        if attempt < config.MAX_RETRY_COUNT - 1:
            await asyncio.sleep(config.RETRY_DELAY_SECONDS)
    
    if best_response:
        logging.warning(f"[Length] FALLBACK: 최소 길이 미달이지만 반환 ({best_length}자)")
        return best_response
    
    return "⚠️ **[시스템 경고]** 기록 장치 오류. 잠시 후 다시 시도해주세요."


# =========================================================
# 유틸리티 함수
# =========================================================
# NOTE: get_available_genres, get_genre_description은 prompt_builder.py로 이동됨 (상단에서 import)


# =========================================================
# 캐싱 지원 세션 생성
# =========================================================
async def create_cached_session(
    client: genai.Client,
    model_version: str,
    channel_id: str,
    lore_text: str,
    rule_text: str = "",
    active_genres: Optional[List[str]] = None,
    custom_tone: Optional[str] = None,
    deep_memory: str = "",
    fermentation_module: Optional[python_types.ModuleType] = None,
    scene_type: Optional[str] = None,
    player_name: str = "",
    player_desc: str = "",
    nvc_summary: str = ""
) -> Tuple[ChatSessionAdapter, bool]:
    """
    캐싱을 지원하는 세션을 생성합니다.
    
    Args:
        scene_type: 장면 유형 ('normal', 'gore', 'nsfw', 'gore_nsfw')
    """
    builder = PromptBuilder()
    builder.set_genres(active_genres)
    builder.set_custom_tone(custom_tone)
    builder.set_scene_type(scene_type)  # 장면 유형 설정
    builder.set_lore(lore_text, rule_text)
    builder.set_fermented(deep_memory=deep_memory)
    builder.set_player_info(player_name, player_desc) # Added for consistency
    builder.set_cognition_data(nvc_summary) # Added for consistency
    
    system_prompt_content = builder.build_system_prompt()
    
    cache_name = None
    if fermentation_module and hasattr(fermentation_module, 'get_or_create_cache'):
        try:
            cache_name = await fermentation_module.get_or_create_cache(
                client, model_version, channel_id,
                lore_text, rule_text, deep_memory,
                system_prompt_content
            )
        except Exception as e:
            logging.warning(f"[Caching] 캐시 생성 실패, 일반 세션 사용: {e}")
    
    if cache_name:
        logging.info(f"[Caching] 캐시 세션 생성 - {channel_id}")
        
        gen_config = types.GenerateContentConfig(
            temperature=DEFAULT_TEMPERATURE,
            safety_settings=config.SAFETY_SETTINGS,
            cached_content=cache_name,
            tools=[],
            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True)
        )
        
        session = ChatSessionAdapter(
            client=client,
            model=model_version,
            history=[],
            config=gen_config
        )
        
        return session, True
    
    else:
        session = create_risu_style_session(
            client, model_version, lore_text, rule_text,
            active_genres, custom_tone, deep_memory,
            fermented_summary="",
            character_descriptions="",
            scene_type=scene_type,
            player_name=player_name, # Passed for consistency
            player_desc=player_desc, # Passed for consistency
            nvc_summary=nvc_summary # Passed for consistency
        )
        return session, False

# =========================================================
# [Request 3] Post-Response Impersonation Filter
# =========================================================
# NOTE: detect_pc_impersonation, filter_pc_impersonation은
# response_processor.py로 이동됨 (상단에서 import)

# =========================================================
# EMOTION BOOSTER (감성 증폭기) - REMOVED
# =========================================================
# NOTE: EMOTION_BOOSTER moved to text_resources.py
