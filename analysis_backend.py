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

class _Cand:
    """candidates[0] 흉내 — finish_reason만 나른다."""
    __slots__ = ("finish_reason", "safety_ratings")

    def __init__(self, finish_reason=None):
        self.finish_reason = finish_reason
        self.safety_ratings = []


class _RespShim:
    """generate_content 응답 흉내 — 호출부가 쓰는 .text 와 finish_reason 보장.

    [2026-09-02] **죽은 배선 수리.** 구 shim은 `candidates=[object()]`라 finish_reason이
    없었고, `memory_system.api_call_with_retry`의 `'MAX_TOKENS' in fr_str` 갈래가
    **OpenAI 라우트에서 통째로 사문**이었다 → 제공자가 출력을 잘라도 봇은 그 사실을
    알 길이 없고, 잘린 JSON이 그대로 파서로 내려가 "분석 결과 비어있음"으로만 보였다.
    (`max_output_tokens`를 명시 해제해 **제공자 기본값**이 적용되는 구조라 더 잘 걸린다.)
    OpenAI 호환 응답의 finish_reason "length" = 토큰 한도 도달 → Gemini 표기로 번역해
    기존 갈래를 되살린다.
    """
    __slots__ = ("text", "candidates", "prompt_feedback", "usage_metadata")

    _FR_MAP = {"length": "MAX_TOKENS", "content_filter": "SAFETY", "stop": "STOP"}

    def __init__(self, text: Optional[str], finish_reason: Optional[str] = None):
        self.text = text or ""
        _fr = self._FR_MAP.get(str(finish_reason or "").lower()) if finish_reason else None
        # 잘림은 본문이 있어도 보고돼야 한다 — candidates를 비우면 그 사유가 사라진다.
        self.candidates = [_Cand(_fr)] if (text or _fr) else []
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


def _norm(s: Optional[str]) -> str:
    """모델 이름표 정규화 — 대소문자·양끝 공백만. 정체 비교용(변형 아님)."""
    return (s or "").strip().lower()


_LEGACY_WARNED = set()


def _legacy_warn(kind: str, label: str) -> None:
    """하위 호환 층 진입 1회 경고. 관측용 — 다음 패스에서 그 층을 걷을 근거가 된다."""
    _key = (kind, (label or "")[:64])
    if _key in _LEGACY_WARNED:
        return
    _LEGACY_WARNED.add(_key)
    logger.warning("[model-route] legacy label (%s): %r — 콜사이트를 config.role_model()로 전환할 것",
                   kind, label)


def _resolve_model_route(model: Optional[str]) -> tuple:
    """(해석된 모델ID, 라우트 라벨). _map_model 의 본체 — 라벨은 로그 표기 전용.

    라벨을 따로 만들지 않고 여기서 같이 내는 이유: 사다리를 두 벌 쓰면 반드시 갈라진다
    (자매 자리 소급 안 함 병). 로그도 판정도 이 함수 하나만 본다.

    [2026-08-18 라우팅 전면 개편] 판정 순서:
      0) **역할 토큰**("role:reader") — 콜사이트가 config.role_model() 로 선언한 역할.
         최우선이자 유일한 정규 입력. config._ROLE_CHAINS 테이블이 env 슬롯을 지목한다.
      1) contextvar 사다리(heavy>narrative>extract>reader>light) — **하위 호환 층**.
         역할 토큰이 전면화되면 잉여지만 이번 패스에선 남긴다(경고 로그 1줄).
         ※ heavy/reader tier 결정에는 여전히 contextvar 가 쓰인다(모델 판정만 토큰으로 이관).
      2) 구 제미니 이름표 정체 비교 — **하위 호환 층**(경고).
      3) 미지 문자열 — 모델 실명 직접 지정으로 보고 **그대로 통과**(경고).
    구 부분문자열 판정(모델명에 pro 라는 글자가 들었나 보던 줄)은 **삭제**됐다. 그게 병의 뿌리 —
    제미니 이름이 라우팅 라벨을 겸했다.
    """
    # ── 0. 역할 토큰 (정규 경로) ──
    _role = None
    try:
        _role = _appconfig.parse_role_token(model)
    except AttributeError:  # config 가 구버전이면 조용히 건너뜀
        _role = None
    if _role:
        return _appconfig.resolve_role_chain(_role), "role:" + _role

    # ── 1. contextvar 사다리 (하위 호환) ──
    _heavy_var = getattr(_appconfig, "ANALYSIS_HEAVY_EFFORT_VAR", None)
    if _heavy_var is not None and _heavy_var.get():
        heavy_model = getattr(_appconfig, "ANALYSIS_OPENAI_MODEL_HEAVY", "") or ""
        if heavy_model:
            return heavy_model, "heavy"
    # [2026-07-05 GLM 스왑] 서사 콜 컨텍스트면 전용 모델(생성계=GLM 잔류, 추출=FLASH ds-flash).
    # env(ANALYSIS_OPENAI_MODEL_NARRATIVE) 미설정("")이면 무효과 — FLASH 폴스루. heavy 우선(서사와 안 겹침).
    _narr_var = getattr(_appconfig, "ANALYSIS_NARRATIVE_VAR", None)
    if _narr_var is not None and _narr_var.get():
        narrative_model = getattr(_appconfig, "ANALYSIS_OPENAI_MODEL_NARRATIVE", "") or ""
        if narrative_model:
            return narrative_model, "narrative"
    # [2026-07-05 후속] per-turn 추출 콜 컨텍스트 → 전용 모델(V4-Pro 승격: 기계 읽기=V4 약점 무해 자리,
    # 오독의 영속층 유입 상류 방어). env 미설정("")이면 FLASH 폴스루. FLASH=배경 콜 전용 잔류.
    _ext_var = getattr(_appconfig, "ANALYSIS_EXTRACT_VAR", None)
    if _ext_var is not None and _ext_var.get():
        extract_model = getattr(_appconfig, "ANALYSIS_OPENAI_MODEL_EXTRACT", "") or ""
        if extract_model:
            return extract_model, "extract"
    # [Reader-GM] 독자 콜 컨텍스트 → 전용 모델(Gemma 후보 등). env 미설정=이름 폴스루(pro→V4-Pro).
    _rdr_var = getattr(_appconfig, "ANALYSIS_READER_VAR", None)
    if _rdr_var is not None and _rdr_var.get():
        reader_model = getattr(_appconfig, "ANALYSIS_OPENAI_MODEL_READER", "") or ""
        if reader_model:
            return reader_model, "reader"
    # [2026-08-17 light 라우트] 단문 배경 콜 3종(게시판·상태 패널·속마음) 전용 경량 모델.
    # 사다리 **맨 끝**에 붙는다 — 기존 4단이 전부 먼저 평가되므로 기존 동작 무변경이 구조적으로 보장된다
    # (실제로 light 는 heavy/narrative/extract/reader 와 겹칠 일이 없다: 배경 큐 전용 자리).
    # env(ANALYSIS_OPENAI_MODEL_LIGHT) 미설정("")이면 무효과 → 이름 폴스루(flash) = 현행 그대로.
    _light_var = getattr(_appconfig, "ANALYSIS_LIGHT_VAR", None)
    if _light_var is not None and _light_var.get():
        light_model = getattr(_appconfig, "ANALYSIS_OPENAI_MODEL_LIGHT", "") or ""
        if light_model:
            return light_model, "light"
    # ── 2. 구 제미니 이름표 정체 비교 (하위 호환 층) ──
    # 콜사이트 전수 전환 후엔 여기 닿을 일이 없다. 닿으면 그 콜사이트가 미전환이라는 뜻 —
    # 경고 1줄로 잡아낸다. 정규화는 대소문자·양끝 공백까지만(그 이상은 정체 비교가 아니다).
    m = _norm(model)
    if not m:
        return _appconfig.ANALYSIS_OPENAI_MODEL_FLASH, "empty"
    _pro_label = _norm(getattr(_appconfig, "MODEL_ID_PRO", None))
    _flash_label = _norm(getattr(_appconfig, "MODEL_ID_FLASH", None))
    _main_label = _norm(getattr(_appconfig, "MODEL_ID", None))
    _distinct = bool(_pro_label) and bool(_flash_label) and _pro_label != _flash_label
    if _distinct and m == _pro_label:
        _legacy_warn("legacy-name", model)
        return _appconfig.ANALYSIS_OPENAI_MODEL_PRO, "legacy:pro"
    if _distinct and m == _flash_label:
        _legacy_warn("legacy-name", model)
        return _appconfig.ANALYSIS_OPENAI_MODEL_FLASH, "legacy:flash"
    # MODEL_ID(주 모델, 기본=PRO 파생)가 별개 값이면 그것도 PRO 슬롯.
    if _main_label and m == _main_label and _main_label != _flash_label:
        _legacy_warn("legacy-name", model)
        return _appconfig.ANALYSIS_OPENAI_MODEL_PRO, "legacy:pro"

    # ── 3. 미지 문자열 = 모델 실명 직접 지정 → 그대로 통과 ──
    # 구 부분문자열 판정(모델명 안에 pro 라는 글자가 있나 냄새 맡던 줄)을 여기서 **삭제**했다.
    # 이름이 라우팅을 겸하지 않는다 — 그게 병의 뿌리였다.
    # 실명이 틀렸으면 백엔드가 시끄럽게 죽는다 — 조용히 엉뚱한 슬롯으로 가는 것보다 낫다.
    _legacy_warn("raw-model-id", model)
    return model, "raw"


def _map_model(model: Optional[str]) -> str:
    """Gemini 모델ID -> wellspring 모델ID. flash->flash, pro->pro, 기본 flash.

    사다리 본체는 _resolve_model_route (라벨 동반). 이 얇은 래퍼는 모델만 필요한
    호출부·스모크용으로 남긴다.
    """
    return _resolve_model_route(model)[0]


def _dsh_anchor(resolved_model: str, tier: str) -> str:
    """[2026-08-16 DSH 앵커 — 분석 관문] deepseek 로 **해석된** 콜에만 붙는 추론 레지스터 앵커.

    렌더 경로(persona) 실측: V4 0813 이 캡 지시를 무시하고 12,867자 사고 → 앵커 투입 후 4,268자(-67%).
    기전 = DSH(네이티브 하네스) 밖에서는 학습 분포 앵커("We need" 개시 관성)가 추론을 훈련된
    레지스터로 접는다. "We need" 토큰은 불변(번역·의역 금지 — RL 코퍼스 관성이 본체).
    단계 내용물은 **분석 콜용**(작가 3단 변환 파이프라인이 아니라 스키마 채우기).

    순수 함수(I/O 0): 모델·tier 만 보고 문자열을 만든다. deepseek 아니면 "" (GLM·기타 무접촉),
    tier 의 추론 캡이 0(=off/none)이면 "" — 추론 안 하는 콜에 추론 지시를 넣지 않는다.
    마지막 1절(format stays as specified below)은 json_object 모드 콜 방어 — 앵커가 시스템
    머리에 서면서 "4) the answer itself" 가 서술형 서두를 유도하지 않도록 못을 박는다.
    """
    if "deepseek" not in (resolved_model or "").lower():
        return ""
    cap = reasoning_policy.reasoning_cap_chars(tier)
    if not cap:
        return ""
    return (
        'Private reasoning register: open your thinking with "We need" and count the steps. '
        "We need: 1) what this task asks; 2) the fields owed; 3) the evidence lines; "
        f"4) the answer itself. Land near {cap} characters of thought and answer. "
        "The answer format stays exactly as specified below."
    )


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
        resolved, _route = _resolve_model_route(model)
        kwargs["model"] = resolved
        kwargs["messages"] = messages
        # reasoning tier: heavy(1회성 추출)=DEEP > per-turn 추출=OFF(수처1 실측: V4-Pro 캡 무시
        # 7.6~13.6k자/턴=지연 주범, 기계 읽기라 원설계 복귀) > 그 외 per-turn=LIGHT.
        # resolved 모델의 허용 knob 으로 매핑(GLM=high/max, deepseek=none/high/max 등).
        _heavy_var = getattr(_appconfig, "ANALYSIS_HEAVY_EFFORT_VAR", None)
        _heavy = bool(_heavy_var.get()) if _heavy_var is not None else False
        _ext_var = getattr(_appconfig, "ANALYSIS_EXTRACT_VAR", None)
        _extract = bool(_ext_var.get()) if _ext_var is not None else False
        # [2026-08-11 리더 §7] 독자 콜 tier 분리 — env 미설정("")이면 공용 tier 폴스루 = 무변화.
        _rdr_var = getattr(_appconfig, "ANALYSIS_READER_VAR", None)
        _reader_tier = (getattr(_appconfig, "ANALYSIS_REASONING_TIER_READER", "") or "") \
            if (_rdr_var is not None and _rdr_var.get()) else ""
        # [2026-09-02] 로어 분석은 heavy 블록 안에서 돌지만 **로어가 heavy를 이긴다** —
        #   추론 폭주(851→13,562자)가 출력 예산을 먹어 JSON이 잘리던 자리. 사다리 최상단.
        _lore_var = getattr(_appconfig, "ANALYSIS_LORE_VAR", None)
        _lore_tier = (getattr(_appconfig, "ANALYSIS_REASONING_TIER_LORE", "") or "") \
            if (_lore_var is not None and _lore_var.get()) else ""
        if _lore_tier:
            _tier = _lore_tier
        elif _heavy:
            _tier = _appconfig.ANALYSIS_REASONING_TIER_HEAVY
        elif _extract:
            _tier = getattr(_appconfig, "ANALYSIS_REASONING_TIER_EXTRACT", "off")
        elif _reader_tier:
            _tier = _reader_tier
        else:
            _tier = _appconfig.ANALYSIS_REASONING_TIER
        _extra = dict(kwargs.get("extra_body") or {})
        _extra.update(reasoning_policy.build_reasoning_params(resolved, _tier))
        kwargs["extra_body"] = _extra
        # [2026-08-16 DSH 앵커 — 분석 관문] 콜사이트마다 심지 않는다(자매 소급 병). 해석된 실모델이
        # deepseek 일 때만, 시스템 메시지 **머리(위치 0)**에 접합 — 위치도 기전이다(꼬리 배치 실패 이력).
        # 렌더 경로(persona)는 이미 배포됨 = 무접촉. GLM·기타 모델은 _dsh_anchor 가 "" 반환.
        _anchor = _dsh_anchor(resolved, _tier)
        if _anchor:
            if messages and messages[0].get("role") == "system":
                messages[0]["content"] = _anchor + "\n\n" + messages[0]["content"]
            else:
                messages.insert(0, {"role": "system", "content": _anchor})
        # 추론 ON 이면 추론 길이 캡 주입(DTG THOUGHTS_LIMIT 이식) — 추론만 조이고 JSON 출력은 유지.
        _cap = reasoning_policy.reasoning_cap_instruction(_tier)
        if _cap:
            messages.append({"role": "system", "content": _cap})
        try:
            resp = await self._client.chat.completions.create(**kwargs)
            _msg = resp.choices[0].message if getattr(resp, "choices", None) else None
            text = _msg.content if _msg else ""
            # [2026-08-17 light 라우트] route= 는 사다리 어느 단이 이겼는지(heavy/narrative/extract/
            # reader/light/name:*). 신규 줄을 파지 않고 기존 한 줄만 넓힌다.
            logger.info("[reasoning-trace] analysis model=%s route=%s tier=%s anchor=%s reasoning_chars=%d",
                        resolved, _route, _tier, "on" if _anchor else "off",
                        reasoning_policy.reasoning_trace_len(_msg))
            return _RespShim(text, getattr(resp.choices[0], "finish_reason", None)
                             if getattr(resp, "choices", None) else None)
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
