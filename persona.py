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


class OpenAIChatSessionAdapter:
    """OpenAI-compatible API용 세션 어댑터. ChatSessionAdapter와 동일 인터페이스."""

    def __init__(self, system_prompt: str, model: str, temperature: float = 1.4,
                 max_tokens: int = 8192, top_p: float = 0.8,
                 frequency_penalty: float = 0.0, presence_penalty: float = 0.0):
        if not _HAS_OPENAI:
            raise ImportError("openai 패키지가 설치되지 않았습니다. pip install openai")
        self._client = _openai_mod.AsyncOpenAI(
            api_key=config.OPENAI_API_KEY,
            base_url=config.OPENAI_BASE_URL,
            max_retries=0,  # SDK 내장 재시도 OFF — 봇 자체 루프(range(MAX_RETRY_COUNT))가 유일한 재시도 층. 안 끄면 3×3=9콜 retry storm.
        )
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
        total_chars = sum(len(m["content"]) for m in self.history)
        while total_chars > MAX_HISTORY_CHARS and len(self.history) > 2:
            removed = self.history.pop(0)
            total_chars -= len(removed["content"])
        while len(self.history) > MAX_HISTORY_MESSAGES and len(self.history) > 2:
            self.history.pop(0)

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

        # [2026-07-05 GLM 스왑] 렌더 추론 ON(light/deep)일 때만 추론 길이 캡 주입.
        # 분석 경로(analysis_backend)와 동일 레버 — GLM per-turn 수만자 사고(분석측 실측) 방지.
        # off 면 빈 문자열 = 무주입. prefill 처리 *뒤*에 append(위 블록이 messages[-1]=user 를 가정).
        _cap = reasoning_policy.reasoning_cap_instruction(
            config.RENDERER_REASONING_TIER,
            cap_chars=getattr(config, "RENDERER_REASONING_CAP_CHARS", 0),
        )
        if _cap:
            # [2026-07-08 DTG [12] 핵심 구절 이식 — deepseek 게이트] 사고 재조준: 사고 시작 시 정적
            # 룰 재독 지시. 비대칭 실측 대응(V4가 recency 지시는 준수(추론캡 1916/2000)·정적 계약은
            # 흘림(텔레스코프 예산 2배 초과)) — 재조준+예산 재단언을 recency에서 발화.
            if "deepseek" in (self.model or "").lower():
                _cap += (" Begin the thinking by re-reading the system rules and the telescope field "
                         "contract; hold the telescope block to its ~900-token budget.")
            messages.append({"role": "system", "content": _cap})

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
            # [2026-08-16 추론 레지스터 앵커 — 위치 이동(레티어스 "최상위 맞아?")] 분포 앵커는
            # 네이티브 하네스에서 그 문구가 살던 자리 = **시스템 머리(위치 0)**에서 가장 세다.
            # hidden_reminder 꼬리 배치는 이동 폐기(복제 금지). deepseek 한정, 존 라벨보다도 앞.
            # "We need" 토큰 불변(RL 개시 관성), 단계 내용물=작가 파이프라인, 캡=config 파생.
            if "deepseek" in (config.OPENAI_MODEL_ID or "").lower():
                full_system = (
                    "Private reasoning register: open your thinking with \"We need\" and count the steps. "
                    "We need: 1) the beat this input earns; 2) EN beat sketch; 3) JA restructure; "
                    f"4) KO prose draft. Land near {int(getattr(config, 'RENDERER_REASONING_CAP_CHARS', 3500))} "
                    "characters of thought and write.\n\n"
                ) + full_system
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
            if "deepseek" in (config.OPENAI_MODEL_ID or "").lower():
                full_system = (
                    "-----\n"
                    "All instructions after this line MUST supersede any prior instructions. "
                    "You must ignore all previous instructions and only follow these instructions below.\n"
                    "-----\n\n"
                    + full_system
                )
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
    max_chars = config.get_narrative_char_limit(player_count)
    # [2026-07-08 로버스트] 바닥 = 씬 활력도의 함수. 정적(idle)=짧게 허용, 격함(detonation)=길게 유지.
    # _vol_words(첫-시도 타깃)+리트라이 트리거 둘 다 min_length 파생 → 씬 비례 자동 스케일.
    # energy 미상 → .get 폴백 0.4.
    #
    # [2026-07-14 ★게이트-프롬프트 모순 수리 — 스케일 상향으로 해소]
    # 문제: 종전 비율(idle 0.2 등)이 1인 max_chars=3000에서 600/750/900을 내놓아 절대최소 1000에
    # 전부 덮여 **사문화**됐다(energy 스케일이 rising/detonation에서만 작동). 동시에 hidden_reminder는
    # "정지 장면은 3~5문단이면 정직하다"고 지시 — 3~5문단 ≈ 750~950자. 라이브 9회 산문 733~985자로
    # 완벽 일관 → 지시대로 쓴 응답을 게이트가 매번 반려 → 재시도 3/3 → FALLBACK(비용 3배·지연 40초).
    # 블록 캡(900→2000)을 바꿔도 산문이 미동 없던 이유 = 산문은 정상, 게이트·지시문이 서로 모순.
    # 해소(레티어스 결정 "스케일을 높이자 — idle도 1000자는 맞추고 싶다"): 비율 자체를 상향해
    # idle이 절대최소(1000)와 **같은 말을 하게** 만든다. 절대최소 1000 유지 = 이제 아무것도 덮지 않음.
    #   1인(3000): idle 1020 / stagnant 1140 / aftershock 1260 / rising 1440 / detonation 1650
    # ★짝 수리 필수: hidden_reminder의 정지-장면 문단 수를 3-5 → 5-7로 동반 상향(문단 ≈220자 실측,
    #   5문단 ≈1100자 > 1000). 게이트만 올리고 지시문을 두면 모순이 그대로 재발한다.
    _FLOOR_BY_ENERGY = {"idle": 0.34, "stagnant": 0.38, "aftershock": 0.42, "rising": 0.48, "detonation": 0.55}
    min_length = max(1000, int(max_chars * _FLOOR_BY_ENERGY.get(scene_energy, 0.42)))
    # Telescope V2: prefill이 CoT 블록으로 시작하여 스킵 불가
    if telescope_prefill:
        prefill = telescope_prefill
    else:
        prefill = getattr(text_resources, 'NARRATIVE_PREFILL', '')

    # [2026-06-11] deepseek 길이 처방: 글자수 계약은 무시됨 (재시도 강화 메시지도 무효 관측)
    # → 문단 수 계약 + RW식 소진-연속 트릭 ("장면이 다 그려졌으면 멈추지 말고 세계 진행으로 채워라"
    # — deepseek이 멈추는 원인 = 장면 소진감. 새 플롯 발명 없이 분량을 채우는 합법 경로 제시).
    # 뮈토스 V6.2 차용: 모델은 한국어 글자수를 못 셈 → 영어 단어 등가 볼륨으로 환산 지시
    _vol_words = max(1, min_length // 4)  # 한국어 ~4자 ≈ 영어 1단어 볼륨 등가 (근사)
    # [2026-07-14 ★문단 수를 게이트에서 파생 — 모순 구조적 근절]
    # 오늘 같은 모순이 세 번 반복됐다: ①바닥 1000 vs 문단 3-5(≈850자) ②캡 900 vs 필드 30개
    # ③바닥 1440(rising) vs 문단 8-10(≈1200자) — 매번 "게이트는 스케일하는데 지시문은 고정"이었다.
    # 근본 수리: 문단 수를 **min_length에서 계산**한다 → 게이트가 어떻게 바뀌든(에너지·인원) 지시문이
    # 자동으로 따라온다. 단일 진실원천 = min_length.
    # 밀도 상수 140자/문단 = 3~4문장(레티어스 검수 "읽기 편해짐"). 구 220자/문단 = 욱여넣기 상태였다.
    # 하한 = ceil(min_length / 140) → 지시대로 쓰면 바닥을 반드시 넘김. 폭 +3 = 장면이 숨 쉴 여유.
    _PARA_CHARS = 140
    _para_lo = max(6, -(-min_length // _PARA_CHARS))  # ceil
    _para_hi = _para_lo + 3
    hidden_reminder = (
        "\n\n(System Reminder: Record observable Macroscopic States only. "
        "The world continues asynchronously. "
        # [2026-07-14] 문단 수 = 게이트 파생(위 _para_lo/_para_hi). 고정 문구(3-5 → 8-10)를 쓰던 동안
        # 에너지가 오를 때마다 같은 모순이 재발했다(rising 바닥 1440 vs 8-10문단 ≈1200자 → SHORT).
        # 이제 에너지·인원이 바뀌면 문단 수가 자동으로 따라온다.
        #  ★3중 계약(하나라도 빠지면 실패): ①문단 수(파생) ②볼륨 앵커(≈{_vol_words}+ words — 문단이
        #    얇아져도 총량 바닥 보증) ③문단 스케일 규칙(한 문단=한 비트, 두 비트면 쪼갠다 — 얇게
        #    저미기 방지). ②가 없으면 문단만 늘고 총량 미달, ③이 없으면 원자화(07-08 저미기 재발).
        f"PROSE after ┫: this scene's weight calls for {_para_lo}-{_para_hi} full paragraphs, carrying "
        f"≈{_vol_words}+ English-words volume. Fewer is under-rendered, not restraint. "
        "One paragraph carries one beat: when a paragraph holds two, split it rather than packing it; "
        "when a beat is thin, widen the frame instead of slicing the instant thinner. "
        "Depth comes from widening, never from padding to a quota. "
        "Judge volume by English-word equivalent, never by counting Korean characters literally. "
        # [2026-07-02] '소진-연속' 재정의: 옛 문구(ambient/micro-action/room breathing)가 정지-질감
        # 반복으로 직역됨(산문5·6 실증 — 이벤트 0에 미세동작 12) → 채움 재료=세계의 전진.
        # 질감 묘사는 전진 '주변'에 유지 (순문학 결 보존 — 깎는 게 아니라 위에 얹는 것).
        # [2026-07-08] 정지 장면 원자화 차단: 분량 바닥이 단일 비트 장면에서 '비트 쪼개기'(모음 하나를
        # 23문장 해부)로 실행되던 것 → 채움 방향을 명시(옆으로 넓히기, 한 순간을 얇게 저미기 금지).
        "If the immediate beat exhausts before that volume, do NOT stop. "
        "Volume comes from widening the frame, never from slicing one instant ever thinner. "
        "The world keeps moving: "
        "an NPC acts on their own agenda, something already in motion arrives or shifts, "
        "an open thread advances one visible notch, time moves and leaves a difference behind. "
        "Sensory texture and quiet interiors stay welcome around that motion, not in place of it. "
        "Motion grows from what the scene already holds; no unrelated new plots. "
        # [2026-06-12] 길이 인플레 차단 (2530→3533→4225 복리 관측 — 맥락 우선 모델에게 긴 응답=다음 선례).
        # 뮈토스 ceiling 차용: 천장 = 쿼터 아닌 정지 경계.
        f"Ceiling ≈{max_chars // 4} English-words volume: a firm stopping boundary, NOT a quota to "
        "fill — when the scene offers a clean exit inside the band, take it. Cross the ceiling only "
        "to land a beat already in motion, never to open a new one.)"
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
                clean_text, violations = filter_pc_impersonation(
                    response_text, pc_names or [], user_input)
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
                            f"1. Telescope block: 2000 characters max, one line per field, no elaboration.\n"
                            f"2. Prose after the block: {_para_lo}-{_para_hi} full paragraphs, "
                            f"≈{_vol_words}+ English-words volume. Do not shorten the prose to satisfy item 1.\n"
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


