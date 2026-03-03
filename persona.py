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
from typing import Optional, List
from google import genai
from google.genai import types
import config

from response_processor import filter_pc_impersonation
import text_resources

# =========================================================
# 상수 정의
# =========================================================
DEFAULT_TEMPERATURE = 1.0


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

    async def send_message(self, content: str, prefill: str = "") -> Optional[types.GenerateContentResponse]:
        """
        메시지를 전송하고 응답을 받습니다. (히스토리 관리 포함)
        prefill이 있으면 role="model" 메시지로 주입하여 모델이 이어서 생성하도록 합니다.
        """
        self._trim_history() # 전송 전 트림

        self.history.append(
            types.Content(role="user", parts=[types.Part(text=content)])
        )

        # 프리필 주입: role="model" 메시지를 추가하여 모델이 이어서 생성
        if prefill:
            self.history.append(
                types.Content(role="model", parts=[types.Part(text=prefill)])
            )

        try:
            # 히스토리 상세 로깅
            total_chars = sum(
                len(p.text) for c in self.history for p in c.parts if hasattr(p, 'text') and p.text
            )
            logging.info(f"[ChatSession] 히스토리: {len(self.history)}msgs, ~{total_chars}chars")

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
                if prefill:
                    # 프리필 + 생성된 연속분 = 전체 model 응답으로 교체
                    full_text = prefill + response.text
                    # 히스토리에는 텔레스코프 CoT 제거 — 이전 턴 CoT가 남으면
                    # 모델이 "텔레스코프만 쓰면 된다"고 학습하여 산문 생략
                    import re as _re
                    history_text = _re.sub(r"┣[\s\S]*?┫\s*", "", full_text).strip()
                    if not history_text:
                        history_text = full_text  # strip 후 빈 문자열이면 원본 유지
                    self.history[-1] = types.Content(
                        role="model",
                        parts=[types.Part(text=history_text)]
                    )
                else:
                    model_content = types.Content(
                        role="model",
                        parts=[types.Part(text=response.text)]
                    )
                    self.history.append(model_content)

            return response

        except Exception as e:
            logging.error(f"ChatSession.send_message 오류: {e}")
            # 에러 시 프리필 메시지도 롤백
            if prefill and self.history and self.history[-1].role == "model":
                self.history.pop()
            if self.history and self.history[-1].role == "user":
                self.history.pop()
            raise




# =========================================================
# 세션 생성 (V3 - 34단계 프롬프트 직접 주입)
# =========================================================
def create_risu_style_session(
    client: genai.Client,
    model_version: str,
    system_prompt: str  # [V3] 34단계 프롬프트 (필수)
) -> ChatSessionAdapter:
    """
    V3 34단계 프롬프트를 사용하여 세션을 생성합니다.
    
    Args:
        client: Gemini API 클라이언트
        model_version: 모델 ID
        system_prompt: 34단계 슬롯 시스템으로 생성된 프롬프트
    
    Returns:
        ChatSessionAdapter: 초기화된 세션
    """
    
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
    
    # 조교 패턴 (Training Dialogue) - 지시이행력 강화
    training_user = getattr(text_resources, 'TRAINING_USER_PROMPT', '')
    training_model = getattr(text_resources, 'TRAINING_MODEL_RESPONSE', '')

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

    # 조교 턴 삽입 (시스템 프롬프트 확인 후 자기확인)
    if training_user and training_model:
        initial_history.extend([
            types.Content(
                role="user",
                parts=[types.Part(text=training_user)]
            ),
            types.Content(
                role="model",
                parts=[types.Part(text=training_model)]
            )
        ])
    
    gen_config = types.GenerateContentConfig(
        temperature=config.NARRATIVE_TEMPERATURE,
        top_k=config.NARRATIVE_TOP_K,
        top_p=config.NARRATIVE_TOP_P,
        max_output_tokens=config.NARRATIVE_MAX_OUTPUT_TOKENS,
        # [Gemini 3] presence_penalty/frequency_penalty not supported
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
    user_input: str,
    pc_names: Optional[List[str]] = None,
    player_count: int = 1,
    telescope_prefill: str = ""
) -> str:
    """
    재시도 로직을 포함하여 응답을 생성합니다.
    [Anti-Gravity Update]
    - BKSPC 자가 교정 처리
    - PC 사칭 실시간 탐지 및 자동 재시도

    [Telescope V2]
    - telescope_prefill이 있으면 모델 응답이 ┣ 블록으로 시작하도록 강제
    - 모델은 [What][Why][How]를 채운 뒤 ┫ 닫고 산문으로 전환
    """
    max_chars = config.get_narrative_char_limit(player_count)
    min_length = int(max_chars * 0.6)  # 최대의 60%를 최소 기준으로
    # Telescope V2: prefill이 CoT 블록으로 시작하여 스킵 불가
    if telescope_prefill:
        prefill = telescope_prefill
    else:
        prefill = getattr(text_resources, 'NARRATIVE_PREFILL', '')

    hidden_reminder = (
        "\n\n(System Reminder: Record observable Macroscopic States only. "
        "The world continues asynchronously.)"
    )
    full_input = user_input + hidden_reminder

    best_response = None
    best_length = 0

    for attempt in range(config.MAX_RETRY_COUNT):
        try:
            response = await chat_session.send_message(full_input, prefill=prefill)
            
            if response is None or not response.candidates:
                logging.warning(f"[시도 {attempt+1}] 응답 또는 후보 없음")
                # prompt_feedback 확인 (기존 로직 유지)
                if response and hasattr(response, 'prompt_feedback') and response.prompt_feedback:
                    feedback = response.prompt_feedback
                    logging.warning(f"  prompt_feedback: {feedback}")
                    if hasattr(feedback, 'block_reason') and str(feedback.block_reason) == 'PROHIBITED_CONTENT':
                        logging.error("🚫 [CRITICAL] Prompt blocked by PROHIBITED_CONTENT filter. Check guidelines/lore.")
                continue
            
            # 기존 finish_reason 확인 로직 (기존 로직 유지)
            candidate = response.candidates[0]
            finish_reason = getattr(candidate, 'finish_reason', None)
            if finish_reason:
                finish_reason_str = str(finish_reason)
                if 'SAFETY' in finish_reason_str:
                    logging.warning(f"[시도 {attempt+1}] 안전 필터 차단: {finish_reason_str}")
                    if hasattr(candidate, 'safety_ratings'):
                        for rating in candidate.safety_ratings:
                            logging.warning(f"  {rating.category}: {rating.probability}")
                    continue
                elif 'MAX_TOKENS' in finish_reason_str:
                    logging.warning(f"[시도 {attempt+1}] 토큰 한계 도달 — 잘린 응답 보충 시도")
                    _truncated = True
                elif finish_reason_str not in ['STOP', 'END_TURN', '1']:
                    logging.warning(f"[시도 {attempt+1}] 종료 사유: {finish_reason_str}")

            _truncated = False
            response_text = None
            if response.text:
                # Telescope V2: prefill은 response.text에 미포함 → 수동 결합
                response_text = (prefill + response.text) if prefill else response.text
            else:
                # content.parts 직접 확인
                # candidate = response.candidates[0] # Already defined above
                if hasattr(candidate, 'content') and candidate.content:
                    parts = candidate.content.parts
                    if parts:
                        text_parts = [p.text for p in parts if hasattr(p, 'text') and p.text]
                        if text_parts:
                            raw = "".join(text_parts)
                            response_text = (prefill + raw) if prefill else raw
                            logging.info(f"[시도 {attempt+1}] parts에서 텍스트 복구: {len(response_text)}자")


            if response_text:
                # Telescope 디버그: 프리필 결합 후 블록 존재 확인
                if prefill:
                    _has_open = "┣" in response_text
                    _has_close = "┫" in response_text
                    logging.info(f"[Telescope Debug] prefill={len(prefill)}chars, ┣={_has_open}, ┫={_has_close}, response_start={response_text[:80]!r}")
                # 1. BKSPC 및 사칭 필터 적용
                # filter_pc_impersonation internally calls process_bkspc
                clean_text, violations = filter_pc_impersonation(response_text, pc_names or [])
                response_length = len(clean_text)
                
                # 2. 사칭 검출 → 경고 로그만 (재시도 없음)
                if violations:
                    violation_types = ", ".join(set(v['type'] for v in violations))
                    logging.warning(f"[Impersonation] 검출됨 ({violation_types}): 필터 적용 후 통과")

                # 3. 텔레스코프: 정식 파싱/제거는 orchestration_response.py에서 수행
                # 여기서는 블록이 깨진 경우(┣ 열고 ┫ 안 닫음)를 처리
                if prefill and "┣" in clean_text and "┫" not in clean_text:
                    if attempt == 0 and not _truncated:
                        # 첫 시도 + 정상 종료(STOP) → 1회만 재시도
                        logging.warning(f"[Telescope] ┣ 열었으나 ┫ 미닫힘: 재시도 {attempt + 1}")
                        full_input = (
                            f"{user_input}\n\n"
                            f"⚠️ **[FORMAT WARNING]** The ┣...┫ telescope block must be properly closed with ┫. "
                            f"Write prose AFTER the ┫ marker.\n"
                            f"{hidden_reminder}"
                        )
                        continue
                    else:
                        # MAX_TOKENS 잘림 또는 재시도 후에도 미닫힘 → ┫ 강제 보충
                        logging.warning(f"[Telescope] ┫ 강제 보충 (truncated={_truncated}, attempt={attempt+1})")
                        clean_text = clean_text + "\n┫"

                # 4. 길이 검사 — 텔레스코프 블록 제외하고 서사 부분만 측정
                _narrative_only = re.sub(r"┣[\s\S]*?┫\s*", "", clean_text)
                response_length = len(_narrative_only)

                if response_length >= min_length:
                    logging.info(f"[Length] OK: {response_length}자 (raw={len(clean_text)}자)")
                    return clean_text
                else:
                    logging.warning(
                        f"[Length] SHORT: {response_length}자(서사) < {min_length}자 "
                        f"(raw={len(clean_text)}자, 시도 {attempt + 1}/{config.MAX_RETRY_COUNT})"
                    )

                    if response_length > best_length:
                        best_response = clean_text
                        best_length = response_length

                    if attempt < config.MAX_RETRY_COUNT - 1:
                        full_input = (
                            f"{user_input}\n\n"
                            f"⚠️ **[LENGTH WARNING]** Previous PROSE (excluding ┣...┫) was {response_length} chars. "
                            f"MUST write at least {min_length} chars of prose AFTER the ┫ marker. "
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
        logging.warning(f"[Retry] FALLBACK: 최선의 응답 반환 ({len(best_response)}자)")
        return best_response
    
    return "⚠️ **[시스템 경고]** 기록 장치 오류. 잠시 후 다시 시도해주세요."

# =========================================================
# 유틸리티 함수
# =========================================================


