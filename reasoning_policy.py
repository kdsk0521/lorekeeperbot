"""
Reasoning Policy — 추론 on/off 를 모델별로 올바르게 매핑하는 단일 지점.

.env 는 자격증명(키/URL) + 모델 ID 만 담는다. "어느 role 이 어느 tier 인지"는
config.py 코드 기본값(RENDERER/ANALYSIS_REASONING_TIER*)이 정하고, "그 tier 가
실제로 어떤 요청 파라미터가 되는지"는 여기서 정한다. 추론 모델마다 받는 knob 과
허용값 집합이 다르기 때문이다:

  - Generic (OpenAI o-series 계열): reasoning_effort = none | low | medium | high
  - DeepSeek-V4 (Ollama):          reasoning_effort = none | high | max     (low/medium 없음)
  - GLM-5.2 (Ollama / OpenAI 호환): reasoning_effort = high | max 만 (thinking 기본 ON,
                                    미지정/무효값 → 기본 max 로 폴백). OFF 는 reasoning_effort 로
                                    안 되고 think=false 로 꺼야 한다.

모델이 안 받는 값을 그대로 던지면 조용히 그 모델의 기본값(GLM → max = 가장 무거움)으로
폴백되므로, raw 문자열을 흘려보내지 않고 여기서 패밀리별로 매핑한다.

Tier (모델 불문 의미):
    "off"   — 확장 추론 없음
    "light" — 최소/가벼운 추론 한 패스 (GLM·DeepSeek 은 실질 최소치가 high)
    "deep"  — 최대 추론 (1회성 heavy 추출 등)

⚠ Ollama /v1 은 reasoning_effort 전달 이슈 이력이 있다. 네이티브 knob 은 think(bool/level).
   실발동 여부는 경험적으로 확인할 것(응답에 reasoning/think 토큰이 실제로 오는지). 안 넘어가면
   해당 패밀리의 param 을 "think" 로 바꾸면 된다(아래 _POLICY 한 곳만 수정).
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

OFF = "off"
LIGHT = "light"
DEEP = "deep"

# family -> tier -> (param_name, value)
# param_name 은 extra_body 키. value 그대로 실린다.
_POLICY = {
    # GLM-5.2: reasoning_effort high/max 만. OFF 는 think=false 로.
    "glm": {
        OFF:   ("think", False),
        LIGHT: ("reasoning_effort", "high"),
        DEEP:  ("reasoning_effort", "max"),
    },
    # DeepSeek-V4 (Ollama): none/high/max (low/medium 미지원).
    "deepseek": {
        OFF:   ("reasoning_effort", "none"),
        LIGHT: ("reasoning_effort", "high"),
        DEEP:  ("reasoning_effort", "max"),
    },
    # 기타 OpenAI 호환 추론 모델(o-series 등): 표준 none/low/high.
    "generic": {
        OFF:   ("reasoning_effort", "none"),
        LIGHT: ("reasoning_effort", "low"),
        DEEP:  ("reasoning_effort", "high"),
    },
}


def family_of(model_id: str) -> str:
    """모델 ID 문자열에서 패밀리 추정 (기본 generic)."""
    m = (model_id or "").lower()
    if "glm" in m:
        return "glm"
    if "deepseek" in m:
        return "deepseek"
    return "generic"


def build_reasoning_params(model_id: str, tier: str) -> dict:
    """`model_id` 를 추론 `tier` 로 두는 extra_body 조각을 반환.

    예) build_reasoning_params("deepseek-v4-pro:cloud", "off") -> {"reasoning_effort": "none"}
        build_reasoning_params("glm-5.2:cloud", "light")       -> {"reasoning_effort": "high"}
        build_reasoning_params("glm-5.2:cloud", "off")         -> {"think": False}
    """
    fam = family_of(model_id)
    table = _POLICY.get(fam, _POLICY["generic"])
    t = (tier or LIGHT).lower()
    if t not in table:
        logger.warning("[reasoning] unknown tier %r for %s → light", tier, model_id)
        t = LIGHT
    param, value = table[t]
    return {param: value}


def reasoning_trace_len(obj) -> int:
    """delta/message 에서 추론(thinking) 토큰 길이를 견고하게 추출 (관측 전용, 출력엔 안 씀).

    reasoning_content / reasoning 속성 + pydantic model_extra 의 reasoning/thinking 키를 훑는다.
    0 이면 이 콜에서 추론이 발동 안 했다(또는 게이트웨이가 안 실어보냈다)는 신호.
    """
    if obj is None:
        return 0
    for attr in ("reasoning_content", "reasoning"):
        v = getattr(obj, attr, None)
        if v:
            return len(v)
    extra = getattr(obj, "model_extra", None) or {}
    if isinstance(extra, dict):
        for k in ("reasoning_content", "reasoning", "thinking"):
            v = extra.get(k)
            if v:
                return len(str(v))
    return 0
