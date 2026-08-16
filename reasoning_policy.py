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

# Ollama /v1 reasoning_effort 계약(공식 문서 확인 2026-07): none / low / medium / high.
#   ⚠ "max" 및 top-level "think" 는 /v1 미지원 → 보내면 무시/거부. 그래서 패밀리 분기 불필요:
#   모든 Ollama-서빙 모델(GLM/DeepSeek/generic)이 이 계약을 공유하고 Ollama 가 모델별 thinking
#   제어로 정규화한다. GLM-5.2 네이티브는 high/max뿐이지만 /v1 계층이 low/medium/none 을 받아 번역.
#   off="none"(Ollama 가 no-think 삽입). deep="high"(=/v1 상한, max 는 스펙 밖).
_TIER_EFFORT = {
    OFF:   "none",
    LIGHT: "low",     # per-turn 보조 최소 추론. GLM 5만자 과추론 억제 레버(실측으로 확인).
    DEEP:  "high",    # 1회성 heavy 추출 최대치(/v1 상한).
}


def build_reasoning_params(model_id: str, tier: str) -> dict:
    """추론 `tier` 를 Ollama /v1 extra_body(reasoning_effort)로 매핑.

    Ollama /v1 은 none/low/medium/high 만 받고 모델별 thinking 제어로 번역하므로 모델 무관.
    예) "off"->{"reasoning_effort":"none"}, "light"->"low", "deep"->"high".
    """
    t = (tier or LIGHT).lower()
    effort = _TIER_EFFORT.get(t)
    if effort is None:
        logger.warning("[reasoning] unknown tier %r for %s → light", tier, model_id)
        effort = _TIER_EFFORT[LIGHT]
    return {"reasoning_effort": effort}


# tier 별 추론 길이 캡(문자). off=제한없음(추론 off). DTG THOUGHTS_LIMIT 이식 — 추론만 조이고 출력은 무관.
_TIER_CAP_CHARS = {LIGHT: 1200, DEEP: 3000}


def reasoning_cap_chars(tier: str) -> int:
    """tier → 추론 길이 캡(문자). 미지정/off = 0 (추론 OFF = 캡 대상 아님).

    [2026-08-16 DSH 앵커 — 분석 관문] _TIER_CAP_CHARS 의 유일한 외부 접근점.
    앵커 문안의 {cap} 이 캡 지시문과 같은 수치를 쓰도록(하드코딩 금지) 게터로 노출.
    """
    return _TIER_CAP_CHARS.get((tier or "").lower(), 0)


def reasoning_cap_instruction(tier: str, cap_chars: int = 0) -> str:
    """추론 길이 캡 지시문(DTG THOUGHTS_LIMIT 이식). reasoning on(light/deep)일 때만 문자열 반환.

    소프트 레버(모델이 문자수를 정확히 세진 않지만 방향으로 조임). GLM 등이 per-turn 에서
    추론을 수만 자 쏟는 것(관측됨)을 억제. 출력(JSON/산문)은 절대 줄이지 말라고 명시.
    cap_chars>0 이면 tier 기본값 대신 사용 (역할별 캡 — 렌더는 config.RENDERER_REASONING_CAP_CHARS).
    """
    cap = reasoning_cap_chars(tier)
    if not cap:
        return ""
    if cap_chars and cap_chars > 0:
        cap = cap_chars
    # [2026-07-08] DTG [4] 이식 확장: 분석-전용 규율 — thinking 안에서 산문/대사 드래프트 금지.
    # 근거: 영어 추론 traces의 산문 초안이 한국어 출력에 문장단위 전사(원자화·역학-해석체의 seeder).
    # 참조: session_summary_2026-07-08.md §3-1(Reasoning Lingua Franca) + §4(DTG [4]).
    return (
        f"Constraint on internal reasoning only: keep the reasoning/thinking block under "
        f"~{cap} characters — a few short analytical bullets, not prose. Analytical planning "
        f"only: inside thinking, draft no prose, no dialogue, no narration in any language; "
        f"plan in points, compose sentences only in the final output. This limit applies "
        f"ONLY to the reasoning block; do NOT shorten, summarize, or truncate the actual output."
    )


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
