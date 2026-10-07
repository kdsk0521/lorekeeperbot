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
import reasoning_policy

from response_processor import filter_pc_impersonation
import text_resources

# OpenAI-compatible SDK (optional — for Fireworks/Kimi renderer)
try:
    import openai as _openai_mod
    _HAS_OPENAI = True
except ImportError:
    _HAS_OPENAI = False

logger = logging.getLogger(__name__)

# DEFAULT_TEMPERATURE 제거 (2026-07-06 감사): 소비자 0 — 온도는
# config.OPENAI_TEMPERATURE(openai) / NARRATIVE_TEMPERATURE(제미니 경로)가 담당.


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
# OpenAI-Compatible ChatSessionAdapter (Fireworks/Kimi 등)
# =========================================================
class _OpenAIResponseShim:
    """Gemini response와 동일한 인터페이스 제공."""
    def __init__(self, text: str, finish_reason: str = "stop"):
        self.text = text
        self._finish_reason = finish_reason
        self.candidates = [self] if text else []
        self.content = type("Content", (), {"parts": [type("Part", (), {"text": text})()]})() if text else None
        self.prompt_feedback = None

    @property
    def finish_reason(self):
        return self._finish_reason


_OPENAI_CLIENTS: dict = {}


def _shared_openai_client():
    """[2026-09-24 감사] 렌더 클라이언트 재사용 — 전엔 턴마다(세션 어댑터마다) AsyncOpenAI 를 새로 만들고 닫지 않아
    httpx 커넥션 풀·TLS 핸드셰이크가 매턴 새로 생기고 누수됐다. (api_key, base_url) 당 하나. 설정값·동작은 종전 그대로."""
    key = (config.OPENAI_API_KEY, config.OPENAI_BASE_URL)
    c = _OPENAI_CLIENTS.get(key)
    if c is None:
        c = _openai_mod.AsyncOpenAI(
            api_key=config.OPENAI_API_KEY,
            base_url=config.OPENAI_BASE_URL,
            max_retries=0,  # SDK 내장 재시도 OFF — 봇 자체 루프(range(MAX_RETRY_COUNT))가 유일한 재시도 층. 안 끄면 3×3=9콜 retry storm.
        )
        _OPENAI_CLIENTS[key] = c
    return c


class OpenAIChatSessionAdapter:
    """OpenAI-compatible API용 세션 어댑터. ChatSessionAdapter와 동일 인터페이스."""

    def __init__(self, system_prompt: str, model: str, temperature: float = 1.4,
                 max_tokens: int = 8192, top_p: float = 0.8,
                 frequency_penalty: float = 0.0, presence_penalty: float = 0.0):
        if not _HAS_OPENAI:
            raise ImportError("openai 패키지가 설치되지 않았습니다. pip install openai")
        self._client = _shared_openai_client()
        self.model = model
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.top_p = top_p
        self.frequency_penalty = frequency_penalty
        self.presence_penalty = presence_penalty
        self.history: list = []  # [{"role": ..., "content": ...}]
        self._system_prompt = system_prompt

    def _trim_history(self):
        MAX_HISTORY_MESSAGES = getattr(config, "MAX_HISTORY_LENGTH", 2000)
        MAX_HISTORY_CHARS = 100000
        if len(self.history) <= 2:
            return
        # [2026-09-24 감사] 앞 2개(history[0]=자료 슬롯 context_data, [1]=ack)는 보호 — Gemini 어댑터와 같게
        #   index 2 부터 버린다. 전엔 pop(0)이라 로어 전문 폴백 등으로 커지면 **세계·상태 자료 블록부터** 사라졌다.
        total_chars = sum(len(m["content"]) for m in self.history)
        while total_chars > MAX_HISTORY_CHARS and len(self.history) > 2:
            removed = self.history.pop(2)
            total_chars -= len(removed["content"])
        while len(self.history) > MAX_HISTORY_MESSAGES and len(self.history) > 2:
            self.history.pop(2)

    async def send_message(self, content: str, prefill: str = ""):
        self._trim_history()
        self.history.append({"role": "user", "content": content})

        messages = [{"role": "system", "content": self._system_prompt}] + self.history

        # Prefill: Fireworks는 assistant prefix를 이어쓰기로 인식 못할 수 있음
        # → user 메시지에 지시로 포함
        if prefill:
            messages[-1] = {
                "role": "user",
                "content": messages[-1]["content"] + f"\n\n[SYSTEM: Begin your response with exactly this text, then continue with prose after ┫]\n{prefill}"
            }

        # [2026-10-01 1차] 렌더 추론 캡 꼬리 삭제 — 마지막 user 뒤에 붙던 system 메시지("land the reasoning/thinking block
        #   near ~5000 characters …")가 사라진다. 5000은 추론이 글자 수를 세는 연료였고(레티어스 "스케치 초안이랑 5천자는
        #   이제 버리자"), full3 리플레이는 이 줄 없이 쟀다(폭주 5/15 → 2/15). 메시지 목록 끝 = user(프리필 지시 포함).
        #   분석 콜 캡(analysis_backend, bridge=False)은 그대로. reasoning_policy의 bridge 가지는 소비자 0.
        #   구 이력: 07-05 GLM 스왑 캡 주입 → 07-08 DTG 재조준 → 09-24 bridge 문구 → 09-29 재단언 삭제.
        try:
            # max_tokens > 4096 이면 stream=true (긴 출력). [2026-07-05] 렌더 추론 ON 대비 예산 16384로 인상(config) — /v1이 thinking을 max_tokens에 포함할 가능성.
            _effective_max = self.max_tokens
            use_stream = _effective_max > 4096
            response = await self._client.chat.completions.create(
                model=self.model,
                messages=messages,
                temperature=self.temperature,
                max_tokens=_effective_max,
                top_p=self.top_p,
                frequency_penalty=self.frequency_penalty,
                presence_penalty=self.presence_penalty,
                stream=use_stream,
                # reasoning: 메인 렌더 tier(기본 off) 를 이 모델이 받는 knob 으로 매핑.
                # (top_k 는 Ollama /v1 미지원 → 애초에 안 실음.)
                extra_body=reasoning_policy.build_reasoning_params(
                    self.model, config.RENDERER_REASONING_TIER
                ),
            )

            if use_stream:
                # 스트리밍 청크 수집 — reasoning_content는 출력엔 안 넣고 길이만 관측
                chunks = []
                finish = "stop"
                _reason_chars = 0
                async for chunk in response:
                    if not chunk.choices:
                        continue
                    delta = chunk.choices[0].delta
                    if delta and delta.content:
                        chunks.append(delta.content)
                    # thinking 토큰은 출력에서 분리(버림) — 단 실발동 확인용으로 길이만 집계
                    _reason_chars += reasoning_policy.reasoning_trace_len(delta)
                    if chunk.choices[0].finish_reason:
                        finish = chunk.choices[0].finish_reason
                text = "".join(chunks)
                _think_tag = ("<think>" in text) or ("</think>" in text)
                # </think> 태그 누출 정리
                text = re.sub(r'</think>', '', text).strip()
                logger.info("[reasoning-trace] render model=%s stream reasoning_chars=%d think_tag=%s",
                            self.model, _reason_chars, _think_tag)
            else:
                choice = response.choices[0] if response.choices else None
                text = choice.message.content if choice and choice.message and choice.message.content else ""
                finish = getattr(choice, "finish_reason", "stop") or "stop" if choice else "stop"
                _msg = choice.message if choice else None
                logger.info("[reasoning-trace] render model=%s nonstream reasoning_chars=%d think_tag=%s",
                            self.model, reasoning_policy.reasoning_trace_len(_msg),
                            ("<think>" in (text or "")))

            if text:
                # [2026-07-27 중복 수리] openai 경로는 프리필을 **user 지시**로 주입한다(L225 근처:
                #   "Begin your response with exactly this text") → 모델이 ┣·[Ground]를 스스로
                #   재현하며 시작하므로 응답에 이미 프리필이 포함돼 있다. 여기서 또 접합하면
                #   ┣·[Ground]가 2회(라이브 로그 실측: 1차는 verbatim 복사, 2차는 재작성본).
                #   Gemini 경로는 role="model" 주입이라 응답에 프리필이 없어 접합이 맞다(L136) —
                #   그 로직이 이 경로까지 흘러온 것이 원인. 이 경로에서는 접합하지 않는다.
                #   모델이 블록을 아예 생략하면 기존 경고("No telescope block…")가 잡는다.
                full_text = text
                # 히스토리엔 산문만: ┫ 이후(=┣ 앞 네이티브 thinking + 텔레스코프 동시 제외). ┫ 없으면 기존 블록 제거.
                history_text = (full_text.rsplit("┫", 1)[-1].strip() if "┫" in full_text
                                else re.sub(r"┣[\s\S]*?┫\s*", "", full_text).strip()) or full_text
                self.history.append({"role": "assistant", "content": history_text})
                return _OpenAIResponseShim(text, finish)
            else:
                logging.warning("[OpenAI] Empty response")
                return _OpenAIResponseShim("", "stop")

        except Exception as e:
            logging.error(f"[OpenAI] send_message error: {e}")
            # 롤백
            if self.history and self.history[-1]["role"] == "user":
                self.history.pop()
            raise


# =========================================================
# 세션 생성 (V3 - 34단계 프롬프트 직접 주입)
# =========================================================
def create_risu_style_session(
    client: genai.Client,
    model_version: str,
    system_prompt: str  # [V3] 34단계 프롬프트 (필수)
):
    """
    V3 34단계 프롬프트를 사용하여 세션을 생성합니다.
    RENDERER_BACKEND에 따라 Gemini 또는 OpenAI 호환 세션을 반환.
    """
    # --- OpenAI 호환 백엔드 ---
    if config.RENDERER_BACKEND == "openai":
        if not _HAS_OPENAI:
            logging.error("[Renderer] openai 패키지 미설치 — Gemini 폴백")
        elif not config.OPENAI_API_KEY:
            logging.error("[Renderer] OPENAI_RENDERER_API_KEY 미설정 — Gemini 폴백")
        else:
            logging.info(f"[Renderer] OpenAI backend: {config.OPENAI_MODEL_ID}")
            # 조교 패턴을 system_prompt에 통합
            # [2026-07-07 인격대우 1단계] 렌더러 자기발화는 렌더 전용 변형 (V4 배경콜은 기존 상수 유지)
            training_user = getattr(text_resources, 'TRAINING_USER_PROMPT', '')
            training_model = getattr(text_resources, 'TRAINING_MODEL_RESPONSE_RENDERER',
                                     getattr(text_resources, 'TRAINING_MODEL_RESPONSE', ''))
            full_system = system_prompt
            # [2026-10-01 1차] 추론 앵커 삭제(구 문안: Private reasoning register … We need: 1) beat 2) EN beat sketch
            #   3) KO prose draft … Land near {cap} characters). 대체 문장 없음.
            #   레티어스 "스케치 초안이랑 5천자는 이제 버리자. 의미가 없는거 같아서". 앵커 문안을 추론이 그대로 되읊는
            #   루프도 관측(추론앵커_문단띠_설계노트 §7–§11). 대체 앵커("노트 다 차면 쓴다")는 먹지 않았다(§11).
            #   full3 리플레이 = 앵커 없는 판. 시스템 머리 위치 0은 이제 조립된 system_prompt(루카 주소) 그대로다.
            if training_user and training_model:
                full_system += (
                    f"\n\n<TrainingDialogue>\n"
                    f"User: {training_user}\n"
                    f"Assistant: {training_model}\n"
                    f"</TrainingDialogue>"
                )
            full_system += (
                "\n\n<Initialization>\n"
                "[SYSTEM] Narrative Protocol Online.\n"
                "Observing Macroscopic States.\n"
                "The world is asynchronous—it does not wait.\n"
                "Recording in Korean.\n"
                "</Initialization>"
            )
            # [2026-07-08 V4 렌더 실험 지원 — 2차 정정] 이 줄 = DTG [0] "실리태번 비법소스"와 문장 동일
            # (FF MAX 레딧 fix도 같은 줄). ★DTG는 리수 모듈 — SILLYTAVERN 토글은 환경 감지가 아니라
            # 기법 이름(옵트인). 리수 유저도 켠다 = 충돌은 프론트엔드 조립이 아니라 **DS 서빙/템플릿
            # 레벨의 공식 CoT 주입**(클라이언트 무관). 로어키퍼 변수 = 우리는 공식 API가 아닌 Ollama
            # Cloud 오픈웨이트 서빙 — 동일 주입 여부는 [reasoning-trace]로 실측(tier=off인데
            # reasoning_chars>0/think_tag=True면 주입 실증). 격하 대상이 우리 텍스트엔 없어 리스크 0.
            # 텔레스코프 프리필=DTG [1](사고 채널 선점) 등가물 기보유. deepseek 렌더일 때만 발화.
            # ★운용 설정(3차 정정, 레티어스 커뮤니티 조사): DTG=Thinking Guide — DS4는 추론을 '켜고
            # 가이드'하는 게 정석(끄면 추론이 산문으로 샘 = 채널링 원리와 동일 결론). 짝 =
            # RENDERER_REASONING_TIER=light + 캡(레티어스 실측: 1916/2000 준수, 텔레스코프와 질서 공존).
            # [2026-10-02 현행] 캡은 10-01에 삭제, 렌더 tier 기본 deep(=effort high) + 샘플링 공식 영역(config L52–).
            # [2026-09-30 삭제] 머리 "All instructions after this line MUST supersede …" 줄(DTG [0]/FF MAX 출처).
            #   레티어스 판정 "필요 크게 없음". 컵케익 v0.38도 같은 계열 문구를 통째로 뺐다(v0.04 1회 → 0회).
            #   [2026-10-01 1차] 그 추론 앵커도 삭제 — 머리는 system_prompt 그대로.
            #   스펙: 파티쳇수정/composition/프리셋이식_소설가컵케익_구현스펙_2026-09-30.md §2
            return OpenAIChatSessionAdapter(
                system_prompt=full_system,
                model=config.OPENAI_MODEL_ID,
                temperature=config.OPENAI_TEMPERATURE,
                max_tokens=config.NARRATIVE_MAX_OUTPUT_TOKENS,
                top_p=config.OPENAI_TOP_P,
                frequency_penalty=config.OPENAI_FREQUENCY_PENALTY,
                presence_penalty=config.OPENAI_PRESENCE_PENALTY,
            )

    # --- Gemini 백엔드 (기본) ---
    init_context = f"""
{system_prompt}

<Initialization>
[SYSTEM] Narrative Protocol Online.
Observing Macroscopic States.
The world is asynchronous—it does not wait.
Recording in Korean.
</Initialization>
"""

    training_user = getattr(text_resources, 'TRAINING_USER_PROMPT', '')
    # [2026-07-07 인격대우 1단계] Gemini 렌더 경로도 렌더 전용 변형 (폴백=기존 상수)
    training_model = getattr(text_resources, 'TRAINING_MODEL_RESPONSE_RENDERER',
                             getattr(text_resources, 'TRAINING_MODEL_RESPONSE', ''))

    initial_history = [
        types.Content(
            role="user",
            parts=[types.Part(text=init_context)]
        ),
        types.Content(
            role="model",
            # [2026-07-07 인격대우 1단계] 대기-기계 목소리 → 능동 작가 (macroscopic-state 자세는 보존)
            parts=[types.Part(text="[Luka] At the desk and glad of it. Watching for the first observable event.")]
        )
    ]

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
        safety_settings=config.SAFETY_SETTINGS,
        tools=[],
        automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
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
# [2026-08-12 출력파생 §8] 렌더 전면 실패 안내 — **산문이 아니다**.
#   구 동작은 이 문자열이 `if response:`를 통과해 정상 응답 행세를 했다:
#   히스토리 적립 → 다음 턴 주입본·발효·추출 콜 입력까지 오염(§7-11).
#   유저 노출은 유지하되 파이프라인 투입은 차단하기 위해 상수로 승격 + 대조 함수 제공.
#   차단은 호출부(orchestration) 한 지점씩 — 여기서 None을 반환하면 안내 자체가 사라진다.
RENDER_FAILURE_NOTICE = "⚠️ **[시스템 경고]** 기록 장치 오류. 잠시 후 다시 시도해주세요."


def is_render_failure(text: Optional[str]) -> bool:
    """응답이 렌더 실패 안내(=산문 아님)인지 판정."""
    return bool(text) and str(text).strip() == RENDER_FAILURE_NOTICE


_USER_INPUT_TAG_RE = re.compile(r"<User_Input>\s*(.*?)\s*</User_Input>", re.S)


def _extract_user_input_for_origin(blob: str) -> str:
    """PC 사칭 **출처 판정**용 소스만 뽑는다.

    렌더 호출에 넘어오는 문자열은 조립된 프롬프트 전문(또는 THIS TURN 문서)이라
    출처 판정의 소스로 쓰면 로어·시트·히스토리의 한글까지 '유저가 준 것'이 된다.
    Slot 32가 유저 입력을 `<User_Input>…</User_Input>`으로 감싸므로 그 블록만 쓴다.
    태그가 없으면(구 조립·테스트 등) 원본을 그대로 돌려 **종전 동작**을 유지한다.
    """
    if not blob:
        return ""
    m = _USER_INPUT_TAG_RE.search(blob)
    return m.group(1) if m else blob


async def generate_response_with_retry(
    client: genai.Client,
    chat_session: ChatSessionAdapter,
    user_input: str,
    pc_names: Optional[List[str]] = None,
    player_count: int = 1,
    telescope_prefill: str = "",
    scene_energy: str = "idle"
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
    # [2026-10-01 1차] 재시도 하한 = 500 고정(레티어스 "코드가 체크해서 재시도 하는건 500자 정도"). 빈 응답·잘린 응답만
    #   거르는 바닥이다 — 분량은 이제 꼬리 띠(숫자 없음)가 맡는다. 폭주로 본문이 0자면 종전처럼 재시도.
    #   삭제: _FLOOR_BY_ENERGY(07-08 씬 활력도 비율)·max(1000, …)·_vol_words·_PARA_CHARS·_para_lo/_para_hi·천장.
    #   이력(07-14 게이트-지시문 모순 3회 → 문단 수를 min_length에서 파생)은 숫자 띠와 함께 퇴역 — 숫자가 없으면
    #   게이트와 지시문이 부딪칠 자리도 없다. scene_energy·player_count 인자는 호출부 호환으로 남긴다(미사용).
    #   스펙 composition/분석렌더_1차_구현스펙_2026-10-01.md §3.
    min_length = 500
    # Telescope V2: prefill이 CoT 블록으로 시작하여 스킵 불가
    if telescope_prefill:
        prefill = telescope_prefill
    else:
        prefill = getattr(text_resources, 'NARRATIVE_PREFILL', '')

    hidden_reminder = (
        "\n\n(System Reminder: Dice logs and system readouts stay off the page. "
        # [2026-09-29 반죽] "The world continues asynchronously." 삭제 — RB_WORLD 머리줄·Initialization과 삼중.
        # [2026-10-01 1차] 구 숫자 띠의 이력 주석(07-14 게이트 파생 · 08-28 ⓐⓑ·압축·840자 수리)은 띠와 함께 퇴역 → 백업본 참조.
        # [2026-10-01 1차] 띠 교체(band2, 숫자 0) — 컵케익식(레티어스 "컵케익식으로 하고 1000자 상한은 풀자").
        #   구 띠: {_para_lo}-{_para_hi} full paragraphs + ≈{_vol_words}+ English-words volume + 양끝 출구 + widening +
        #   천장 ≈{max_chars//4}. 문단 수·단어 수는 추론이 세는 표적이었다(추론 속 "paragraph" 수십 회). 모순 셋도 같이 풀렸다:
        #   (1) '멈추지 마' vs EXIT의 자르기 → 첫 재탕 비트가 출구, 자르기는 EXIT가 정한다 / (2) 채우기 vs 재탕 금지 →
        #   새 것을 가져오는 동안만 계속 / (3) 하한 출구 vs 상한 압력 → 수치 자체 없음.
        #   유지: 세계 전진 지속성 문장(08-28 ⓖ 사건 중복 차단), no unrelated new plots. band2 리플레이 5/12 → 2/12.
        #   스펙 composition/분석렌더_1차_구현스펙_2026-10-01.md §3. 구 문안 이력은 archive/bak_1차_2026-10-01/persona.py.
        # [2026-10-02 모순정리 B] 앞부분 → 발언권(플레이어만 답할 수 있는 첫 지점까지 세계가 처리). 옛 "first resting point /
        #   marks the exit"는 브레이크 쪽만 있던 문안 — 추론이 끝낼 자리를 찾는 데 썼다(springboard 117회/98판).
        #   스펙 composition/끊기·수위블록_모순정리_스펙_2026-10-02.md. 닫는 절은 "the first point …"로 쟀다가 추론이 그걸
        #   출구 찾기로 썼다("first point only player can answer") → "once every move still left to the world waits …"(측정 2판).
        "PROSE after ┫: the world's side resolves until the floor passes to the player. Everything in the scene with "
        "something to do before the PC's next choice does it, in its own order and at its own length (a speech runs as "
        "long as it runs; a figure acts again while it still has the means), each beat bringing something the page does "
        "not yet hold (a detail, a reaction, a change); the turn closes once every move still left to the world waits "
        "on the player's answer (the PC's own move, a blow or offer the PC can still meet before it lands, a question "
        "put to the PC), and the cut there is EXIT's. "
        "A motion the world makes happens once and then stands: what was posted stays posted, and a later turn "
        "finds it already done rather than doing it again. What fills the page grows from what the scene already "
        "holds, never from re-rendering what an earlier turn put on the page; no unrelated new plots.)"
    )
    # [2026-07-08 DTG [15] 이식 — deepseek 렌더 게이트] 한국어 순도 잠금: V4 추론-ON 운용에서
    # 한자/영어 사고-흔적·번역체가 산문으로 새는 것 방지 (DS 계열 고질, GLM 경로 무영향).
    if "deepseek" in (getattr(config, "OPENAI_MODEL_ID", "") or "").lower():
        hidden_reminder += (
            " (Output purity: the prose after the closing mark is natural Korean only. No traces of "
            "Chinese or English thinking, no translationese, no roleplay/meta terminology in the visible reply.)"
        )
        # [2026-08-16 추론 레지스터 앵커 — DSH 이탈 보정] V4 0813 실측: 캡 지시 무시, 추론 12.9k자
        # (07-05 폭주 이력 재발, [reasoning-trace] render reasoning_chars). 커뮤니티 관측(DSH 밖에서는
        # 학습 분포 앵커가 과추론을 줄임)에서 **이식 가능한 알맹이만**: 엔지니어 페르소나·툴 카탈로그
        # 줄은 정체성 오염이라 기각, 추론 채널 오프너 앵커("We need" 개시 = RL 코퍼스 관성)만 채택 —
        # 조교/프리필 계열 문법. ★3단 변환 다리(영→일→한) 보존이 제약: 앵커가 다리를 자르면 역효과라
        # 단계 구조 안에 다리를 명시. 캡 숫자는 config 파생(단일 진실원천). 관측=reasoning_chars 추이.
        # [2026-08-16 추론 레지스터 앵커 → 당일 위치 이동] 꼬리(hidden_reminder) 배치는 폐기 —
        # 분포 앵커는 시스템 머리(위치 0)가 정위치(레티어스 "최상위 맞아?" 적중). 본문은
        # create_risu_style_session의 deepseek 분기(full_system 머리)로 이동. 복제 금지.
        # [2026-10-01 1차] 그 앵커는 삭제됐다(create_risu_style_session 주석). 꼬리엔 순도 가드만 남는다.
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
            _truncated = False  # [2026-07-02 fix] MAX_TOKENS 분기보다 먼저 초기화 (아래에서 리셋하면 플래그 사망)
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
                elif finish_reason_str.upper() not in ['STOP', 'END_TURN', '1']:  # openai 소문자 'stop' 가짜경고 fix
                    logging.warning(f"[시도 {attempt+1}] 종료 사유: {finish_reason_str}")

            response_text = None
            # [2026-09-24 감사] 수동 결합은 **Gemini 경로 전용**. openai 어댑터는 프리필을 user 지시로 주므로
            #   응답에 이미 ┣…[Ground]가 들어 있다(07-27 주석은 어댑터 히스토리 접합만 뺐고 이 줄이 남았다) →
            #   ┣ 2회, 그리고 모델이 블록 없이 산문만 쓰면 "┣ 있고 ┫ 없음"으로 판정돼 재시도·강제 ┫로 산문 소실.
            _join_prefill = bool(prefill) and not isinstance(chat_session, OpenAIChatSessionAdapter)
            if response.text:
                # Telescope V2: prefill은 response.text에 미포함 → 수동 결합 (Gemini)
                response_text = (prefill + response.text) if _join_prefill else response.text
            else:
                # content.parts 직접 확인
                # candidate = response.candidates[0] # Already defined above
                if hasattr(candidate, 'content') and candidate.content:
                    parts = candidate.content.parts
                    if parts:
                        text_parts = [p.text for p in parts if hasattr(p, 'text') and p.text]
                        if text_parts:
                            raw = "".join(text_parts)
                            response_text = (prefill + raw) if _join_prefill else raw
                            logging.info(f"[시도 {attempt+1}] parts에서 텍스트 복구: {len(response_text)}자")


            if response_text:
                # Telescope 디버그: 프리필 결합 후 블록 존재 확인
                if prefill:
                    _has_open = "┣" in response_text
                    _has_close = "┫" in response_text
                    logging.info(f"[Telescope Debug] prefill={len(prefill)}chars, ┣={_has_open}, ┫={_has_close}, response_start={response_text[:80]!r}")
                    # ┣ 블록 한글비율 계측(log-only) — 영어-락 프리필 효과/드리프트율 관측.
                    # 블록엔 인용·고유명사로 한국어가 일부 정상 존재 → 0은 아니고, 락이 먹으면 낮게 유지.
                    _blk = re.search(r"┣(.*?)┫", response_text, re.S)
                    if _blk:
                        _ko = len(re.findall(r"[가-힣]", _blk.group(1)))
                        _ratio = _ko / max(len(re.sub(r"\s", "", _blk.group(1))), 1)
                        logging.info(f"[Telescope Lang] ┣block ko_ratio={_ratio:.2f} ko_chars={_ko}")
                # 1. BKSPC 및 사칭 필터 적용
                # filter_pc_impersonation internally calls process_bkspc
                # [2026-08-13] user_input 전달 = 출처 판정 활성. 유저가 이번 턴에 공급한
                # 행동의 직조(Slot 21 DECREE 준수)를 사칭으로 오삭제하던 것 차단.
                # [2026-08-28 ★출처 판정 배선 수리 — 검출기가 죽어 있었다]
                #   구 배선: 여기 `user_input`은 **조립된 프롬프트 전문**이다
                #   (`orchestration_response._user_input = _now_doc if tuple else prompt`).
                #   `_is_supplied_by_input`은 음절키 겹침 0.34로 면제를 주는데, 소스가 프롬프트
                #   전문(NPC 시트·로어·히스토리의 한글 수천 자)이면 **거의 모든 한국어 문장이
                #   면제**된다 — 실측: `아린이 "그건 내가 할게"라고 말했다` 면제 True.
                #   → dialogue·impersonation_2nd 하드 삭제가 08-13부터 사실상 사문(thought만 생존).
                #   ★08-13 스모크가 통과한 이유 = 깨끗한 한 줄 입력을 먹였다. **실전 배선을 안 봤다**
                #   ([[project-observation-bridge]] "게이트 스모크는 호출경로 끝까지").
                #   수리: 판정 소스를 Slot 32 <User_Input> 블록으로 **좁힌다**. 태그가 없으면
                #   종전 값 그대로(단조 안전 — 면제 범위를 넓히지 않는다).
                _origin = _extract_user_input_for_origin(user_input)
                clean_text, violations = filter_pc_impersonation(
                    response_text, pc_names or [], _origin)
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
                            f"[Format note] Close the ┣...┫ telescope block with ┫, then write the prose after the ┫ marker. "
                            f"The block is internal reasoning; the prose is what the reader sees.\n"
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
                        # [2026-06-10] 길이 미달의 실범인은 텔레스코프 비대 (관측: raw 3555 중 블록 2300+).
                        # 출력 예산을 구조 분석이 다 쓰고 산문이 굶음 → 블록 압축 + 산문 증량을 함께 지시.
                        # 값싼 모델(deepseek)은 추상 지시를 무시 → 문단 수 같은 구체 지표로.
                        # [2026-07-14 재시도 노트 정합] 잔재 3건 수리:
                        #  ① 캡 900 하드코딩 → 프리필/프로토콜의 2000과 불일치(구값). 파생값으로 통일.
                        #  ② "A still scene needs only a few" → 감축 유도. 이 노트는 산문이 *짧아서* 뜨는데
                        #     '적어도 된다'고 말하면 다음 시도가 더 짧아진다(실측: 블록 1023↓ 시 산문 932↓).
                        #  ③ 비대칭 부재 → 모델이 "전체 축소"로 읽음. 목표 문단 수를 명시해 방향을 못박는다.
                        _tele_len = len(clean_text) - response_length
                        full_input = (
                            f"{user_input}\n\n"
                            f"[Budget note, attempt {attempt + 1}] "
                            f"Last output spent the budget the wrong way: telescope block {_tele_len} chars, "
                            f"prose only {response_length} chars (needs {min_length}+).\n"
                            f"Rebalance in one direction only — the block shrinks, the prose GROWS:\n"
                            # [2026-09-24 감사 §5-2 #29] 2000 → 1000(Slot 34 v5 프로토콜 예산과 같은 수치).
                            f"1. Telescope block: one line per field, no elaboration.\n"   # [2026-09-30] 숫자 삭제 — TELESCOPE budget 줄과 같은 말
                            # [2026-10-01 1차] 숫자 뺌(문단 수·단어 수) — 꼬리 띠와 같은 말.
                            f"2. Prose after the block: runs until the floor passes to the player, each beat bringing "
                            f"something new. Do not shorten the prose to satisfy item 1.\n"
                            f"Grow the prose by expanding beats already in play: a line of dialogue, an open thread "
                            f"advancing a notch, an NPC acting on their own agenda, sensory texture and body language "
                            f"around that motion. Widen the frame; never slice one instant thinner. Add no new plot.\n"
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
    
    return RENDER_FAILURE_NOTICE

# =========================================================
# 유틸리티 함수
# =========================================================


