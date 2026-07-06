"""
Analysis Backend — genai.Client 호환 facade (좌뇌/Flash 분석을 OpenAI 호환 백엔드로 라우팅).

목적:
    Gemini 키가 죽었을 때, cognition/theoria/memory/fermentation 등 좌뇌 호출부 15+곳을
    *건드리지 않고* wellspring(OpenAI 호환, DeepSeek)으로 넘긴다.

방법:
    google.genai.Client 의 사용 표면만 흉내낸다:
        - client.aio.models.generate_content(model, contents, config) -> .text 보장
        - client.aio.models.embed_content(model, contents)            -> .embeddings[].values
        - client.caches.create / delete                              -> 명시적 캐시 차단(암묵 캐싱 사용)
    main.py 의 클라이언트 생성부 한 곳만 이걸로 스왑하면 전체가 라우팅된다.

핵심 번역:
    - types.Content(role=user|model, parts=[Part(text)])  -> OpenAI messages(role=user|assistant)
    - config.system_instruction                            -> system 메시지
    - config.response_mime_type == "application/json"      -> response_format={"type":"json_object"}
    - config.safety_settings                               -> 버림 (OpenAI 무대응)
    - config.top_k                                         -> extra_body.top_k
    - config.temperature/top_p/max_output_tokens           -> 표준 파라미터

호출부는 response_schema 를 안 쓰고 json_object 모드 + 수동 파싱(json.loads/repair_json)을 쓰므로
스키마 변환은 불필요하다.
"""

from __future__ import annotations

import logging
from typing import Any, List, Optional

import config as _appconfig
import reasoning_policy

logger = logging.getLogger(__name__)

try:
    from openai import AsyncOpenAI
    _HAS_OPENAI = True
except ImportError:  # pragma: no cover
    AsyncOpenAI = None  # type: ignore
    _HAS_OPENAI = False


# ── 응답 shim (Gemini 응답 인터페이스 흉내) ──────────────────────────────────

class _RespShim:
    """generate_content 응답 흉내 — 호출부가 쓰는 .text 만 보장."""
    __slots__ = ("text", "candidates", "prompt_feedback", "usage_metadata")

    def __init__(self, text: Optional[str]):
        self.text = text or ""
        self.candidates = [object()] if text else []
        self.prompt_feedback = None
        self.usage_metadata = None


class _Embedding:
    __slots__ = ("values",)

    def __init__(self, values):
        self.values = values


class _EmbResShim:
    """embed_content 응답 흉내 — response.embeddings[i].values."""
    __slots__ = ("embeddings",)

    def __init__(self, vectors: List[list]):
        self.embeddings = [_Embedding(v) for v in vectors]


# ── 입력 번역 ────────────────────────────────────────────────────────────────

def _flatten_text(obj: Any) -> str:
    """str / Content / Part / list 를 평문 텍스트로."""
    if obj is None:
        return ""
    if isinstance(obj, str):
        return obj
    parts = getattr(obj, "parts", None)
    if parts:
        return "".join(getattr(p, "text", "") or "" for p in parts)
    if isinstance(obj, (list, tuple)):
        return "\n".join(_flatten_text(x) for x in obj)
    text_attr = getattr(obj, "text", None)
    if isinstance(text_attr, str):
        return text_attr
    return str(obj)


def _contents_to_messages(contents: Any, system_instruction: Any = None) -> List[dict]:
    """Gemini contents(+system_instruction) -> OpenAI messages.

    role 매핑: model -> assistant, 그 외 -> user. system_instruction -> system.
    """
    messages: List[dict] = []
    si = _flatten_text(system_instruction).strip() if system_instruction is not None else ""
    if si:
        messages.append({"role": "system", "content": si})

    if contents is None:
        return messages
    if isinstance(contents, str):
        messages.append({"role": "user", "content": contents})
        return messages
    if not isinstance(contents, (list, tuple)):
        contents = [contents]

    for c in contents:
        role = (getattr(c, "role", None) or "user")
        out_role = "assistant" if role == "model" else "user"
        messages.append({"role": out_role, "content": _flatten_text(c)})
    return messages


def _map_model(model: Optional[str]) -> str:
    """Gemini 모델ID -> wellspring 모델ID. flash->flash, pro->pro, 기본 flash.

    단 heavy_analysis() 컨텍스트(1회성 추출)면 추론-가능 HEAVY 모델로 강제 라우팅한다.
    Flash가 V3.2 같은 비추론 모델이면 reasoning_effort 격상이 그냥 죽기 때문.
    """
    _heavy_var = getattr(_appconfig, "ANALYSIS_HEAVY_EFFORT_VAR", None)
    if _heavy_var is not None and _heavy_var.get():
        heavy_model = getattr(_appconfig, "ANALYSIS_OPENAI_MODEL_HEAVY", "") or ""
        if heavy_model:
            return heavy_model
    # [2026-07-05 GLM 스왑] 서사 콜 컨텍스트면 전용 모델(생성계=GLM 잔류, 추출=FLASH ds-flash).
    # env(ANALYSIS_OPENAI_MODEL_NARRATIVE) 미설정("")이면 무효과 — FLASH 폴스루. heavy 우선(서사와 안 겹침).
    _narr_var = getattr(_appconfig, "ANALYSIS_NARRATIVE_VAR", None)
    if _narr_var is not None and _narr_var.get():
        narrative_model = getattr(_appconfig, "ANALYSIS_OPENAI_MODEL_NARRATIVE", "") or ""
        if narrative_model:
            return narrative_model
    # [2026-07-05 후속] per-turn 추출 콜 컨텍스트 → 전용 모델(V4-Pro 승격: 기계 읽기=V4 약점 무해 자리,
    # 오독의 영속층 유입 상류 방어). env 미설정("")이면 FLASH 폴스루. FLASH=배경 콜 전용 잔류.
    _ext_var = getattr(_appconfig, "ANALYSIS_EXTRACT_VAR", None)
    if _ext_var is not None and _ext_var.get():
        extract_model = getattr(_appconfig, "ANALYSIS_OPENAI_MODEL_EXTRACT", "") or ""
        if extract_model:
            return extract_model
    # [Reader-GM] 독자 콜 컨텍스트 → 전용 모델(Gemma 후보 등). env 미설정=이름 폴스루(pro→V4-Pro).
    _rdr_var = getattr(_appconfig, "ANALYSIS_READER_VAR", None)
    if _rdr_var is not None and _rdr_var.get():
        reader_model = getattr(_appconfig, "ANALYSIS_OPENAI_MODEL_READER", "") or ""
        if reader_model:
            return reader_model
    m = (model or "").lower()
    if "pro" in m and "flash" not in m:
        return _appconfig.ANALYSIS_OPENAI_MODEL_PRO
    return _appconfig.ANALYSIS_OPENAI_MODEL_FLASH


def _config_to_kwargs(cfg: Any) -> dict:
    """GenerateContentConfig -> chat.completions.create kwargs (safety_settings 무시)."""
    kwargs: dict = {}
    if cfg is None:
        return kwargs
    temp = getattr(cfg, "temperature", None)
    if temp is not None:
        kwargs["temperature"] = temp
    top_p = getattr(cfg, "top_p", None)
    if top_p is not None:
        kwargs["top_p"] = top_p
    max_out = getattr(cfg, "max_output_tokens", None)
    if max_out:
        kwargs["max_tokens"] = max_out
    if getattr(cfg, "response_mime_type", None) == "application/json":
        kwargs["response_format"] = {"type": "json_object"}
    # reasoning(extra_body)은 generate_content 에서 resolved 모델 + tier 로 결정한다.
    # 모델별 허용값이 달라(여기선 모델을 모름) reasoning_policy 로 매핑. top_k 는 Ollama /v1 미지원 → 드롭.
    return kwargs


# ── facade ───────────────────────────────────────────────────────────────────

class _NoCaches:
    """Gemini 명시적 컨텍스트 캐시 차단. wellspring 은 암묵 prefix 캐싱이라 불필요.
    호출부(fermentation)는 ANALYSIS_BACKEND=openai 가드로 비캐시 경로를 타야 한다."""

    def create(self, *args, **kwargs):
        raise RuntimeError(
            "explicit caching unsupported on openai analysis backend "
            "(wellspring uses implicit prefix caching; fermentation must use non-cached path)"
        )

    def delete(self, *args, **kwargs):
        return None


class _Models:
    def __init__(self, client: "AsyncOpenAI", embed_client: Optional["AsyncOpenAI"] = None):  # type: ignore
        self._client = client
        self._embed_client = embed_client or client  # 임베딩은 별도 엔드포인트(Voyage) — 미지정 시 메인 재사용

    async def generate_content(self, model=None, contents=None, config=None):
        messages = _contents_to_messages(contents, getattr(config, "system_instruction", None))
        kwargs = _config_to_kwargs(config)
        resolved = _map_model(model)
        kwargs["model"] = resolved
        kwargs["messages"] = messages
        # reasoning tier: heavy(1회성 추출)=DEEP > per-turn 추출=OFF(수처1 실측: V4-Pro 캡 무시
        # 7.6~13.6k자/턴=지연 주범, 기계 읽기라 원설계 복귀) > 그 외 per-turn=LIGHT.
        # resolved 모델의 허용 knob 으로 매핑(GLM=high/max, deepseek=none/high/max 등).
        _heavy_var = getattr(_appconfig, "ANALYSIS_HEAVY_EFFORT_VAR", None)
        _heavy = bool(_heavy_var.get()) if _heavy_var is not None else False
        _ext_var = getattr(_appconfig, "ANALYSIS_EXTRACT_VAR", None)
        _extract = bool(_ext_var.get()) if _ext_var is not None else False
        if _heavy:
            _tier = _appconfig.ANALYSIS_REASONING_TIER_HEAVY
        elif _extract:
            _tier = getattr(_appconfig, "ANALYSIS_REASONING_TIER_EXTRACT", "off")
        else:
            _tier = _appconfig.ANALYSIS_REASONING_TIER
        _extra = dict(kwargs.get("extra_body") or {})
        _extra.update(reasoning_policy.build_reasoning_params(resolved, _tier))
        kwargs["extra_body"] = _extra
        # 추론 ON 이면 추론 길이 캡 주입(DTG THOUGHTS_LIMIT 이식) — 추론만 조이고 JSON 출력은 유지.
        _cap = reasoning_policy.reasoning_cap_instruction(_tier)
        if _cap:
            messages.append({"role": "system", "content": _cap})
        try:
            resp = await self._client.chat.completions.create(**kwargs)
            _msg = resp.choices[0].message if getattr(resp, "choices", None) else None
            text = _msg.content if _msg else ""
            logger.info("[reasoning-trace] analysis model=%s tier=%s reasoning_chars=%d",
                        resolved, _tier, reasoning_policy.reasoning_trace_len(_msg))
            return _RespShim(text)
        except Exception as e:
            logger.error("[analysis-openai] generate_content failed (%s): %s", kwargs.get("model"), e)
            return _RespShim("")

    async def embed_content(self, model=None, contents=None, **_ignore):
        texts = contents if isinstance(contents, list) else [contents]
        texts = [t if isinstance(t, str) else _flatten_text(t) for t in texts]
        try:
            resp = await self._embed_client.embeddings.create(
                model=_appconfig.ANALYSIS_OPENAI_EMBED_MODEL,
                input=texts,
            )
            vectors = [d.embedding for d in resp.data]
            return _EmbResShim(vectors)
        except Exception as e:
            logger.warning("[analysis-openai] embed_content failed: %s", e)
            return _EmbResShim([[] for _ in texts])


class _Aio:
    def __init__(self, models: _Models):
        self.models = models


class GenaiCompatClient:
    """google.genai.Client 호환 facade (분석측 전용)."""

    def __init__(self, api_key: str, base_url: str):
        if not _HAS_OPENAI:
            raise ImportError("openai 패키지 필요: pip install openai")
        self._client = AsyncOpenAI(api_key=api_key, base_url=base_url, max_retries=0)  # type: ignore  (SDK 재시도 OFF — cognition api_call_with_retry가 유일 재시도층, 중첩 방지)
        # 임베딩 전용 클라이언트 — LLM(Ollama Cloud)은 임베딩 서빙 X → Voyage 등 별도 엔드포인트/키로 분리
        self._embed_client = AsyncOpenAI(  # type: ignore
            api_key=_appconfig.ANALYSIS_OPENAI_EMBED_API_KEY or api_key,
            base_url=_appconfig.ANALYSIS_OPENAI_EMBED_BASE_URL or base_url,
            max_retries=0,
        )
        _models = _Models(self._client, self._embed_client)
        self.aio = _Aio(_models)
        self.models = _models          # 일부 코드가 client.models.* 를 쓸 경우 대비
        self.caches = _NoCaches()


def build_analysis_client() -> "GenaiCompatClient":
    """config 의 ANALYSIS_OPENAI_* 로 facade 생성."""
    return GenaiCompatClient(
        api_key=_appconfig.ANALYSIS_OPENAI_API_KEY,
        base_url=_appconfig.ANALYSIS_OPENAI_BASE_URL,
    )
