"""
NPC Profile Harness (N6) — 정적 심리 특성 추출.
프로필 텍스트에서 변하지 않는 심리 특성을 코드로 1회 추출 → domain 영속 저장.
Flash가 매턴 판단할 필요 없음.
"""
import re
from typing import Dict

# ─────────────────────────────────────────────────────────────────────
# [2026-07-28 재작성] 임계 2 → 1, 대신 **강한 신호만** 남긴다.
#
# 구 상태: 카테고리당 정규식 1개뿐인데 임계는 `score >= 2` → 구조적 도달 불가
#   (coping_style은 어떤 시트를 넣어도 항상 "adaptive"였다).
# 1차 시도(갈래 추가)는 반쪽이었다 — 실측 결과 영어 시트 5인 중 1명만 잡혔다.
#   잘 쓴 산문은 "suppresses"라고 쓰지 않고 **행동으로** 보여주기 때문.
# 그래서 두 방향으로 정리한다:
#   ① 확실히 하고 싶으면 시트에 명시 라벨(`- Coping: avoidant`) — _extract_explicit_traits
#   ② 자동 추출은 **보조**로 격하: 임계 1 + 약한 키워드 제거
#      "무시/도발/aggressive" 같은 범용어는 사건 서술에도 나와 오탐을 만들었다.
#      남긴 건 그 카테고리를 **명시적으로 가리키는** 표현뿐이라 1개만 걸려도 신뢰할 만하다.
#
# 언어: 시트는 영어로 쓰는 것이 이 봇의 방침(입력·출력만 한국어). 신규 패턴은 영어로 쓰고,
#   기존 한국어 키워드는 회귀 위험 0이라 그대로 둔다(한글 시트도 계속 동작).
# ★한글 정규식 주의: 음절 단위라 어간을 중간에서 자르면 안 된다 —
#   "돌린다"에 "돌리"는 없다(돌+린+다). 활용형을 나열할 것.
# ─────────────────────────────────────────────────────────────────────

ATTACHMENT_PATTERNS = {
    "anxious": [
        r"(?i)\b(anxious|clingy|needy|abandonment|fear\s+of\s+being\s+left"
        r"|seeks?\s+reassurance|approval[-\s]seeking|불안[한형]|집착|버림받|확인\s*받)",
    ],
    "avoidant": [
        r"(?i)\b(avoidant|walls?\s+up|emotional\s+distance|keeps?\s+\w+\s+at\s+arm'?s\s+length"
        r"|pushes?\s+(people\s+)?away|shuts?\s+(people\s+)?out|회피[적형]|거리를?\s*둔)",
    ],
    "disorganized": [
        r"(?i)\b(disorganized|push[-\s]pull|hot[-\s]and[-\s]cold|mixed\s+signals"
        r"|pulls?\s+away\s+then|close\s+then\s+suddenly|혼란[형적]|다가갔다\s*밀어)",
    ],
    "secure": [],  # default
}

MORAL_STANCE_PATTERNS = {
    "disengaged": [
        r"(?i)\b(machiavellian|narcissis[mt]|psychopath|sociopath|dark\s*triad"
        r"|manipulat|자기합리화|냉혈|비도덕)",
    ],
    "conflicted": [
        r"(?i)\b(conflicted|torn\s+between|guilt|remorse|second[-\s]guess"
        r"|can'?t\s+forgive\s+(himself|herself|themselves)|죄책감|양심|자책)",
    ],
    "principled": [
        r"(?i)\b(principled|on\s+principle|won'?t\s+compromise|code\s+of\s+(honor|conduct)"
        r"|keeps?\s+(his|her|their)\s+word|honorable|신념|의리|원칙)",
    ],
    "neutral": [],  # default
}

COPING_PATTERNS = {
    "avoidant": [
        r"(?i)\b(suppress|bottles?\s+(it\s+)?up|deflect|changes?\s+the\s+subject"
        r"|shuts?\s+down|stonewall|won'?t\s+talk\s+about|avoids?\s+the\s+(question|subject)"
        r"|억압|입을?\s*다(물|문)|말[을머]?\s*돌(리|린|려|렸))",
    ],
    "confrontational": [
        r"(?i)\b(confrontational|snaps?\s+back|lashes?\s+out|pushes?\s+back"
        r"|doesn'?t\s+back\s+down|meets?\s+it\s+head[-\s]on|공격적|대립|맞(서|선|섰))",
    ],
    "intellectual": [
        r"(?i)\b(intellectualiz|rationaliz|overthink|explains?\s+it\s+away"
        r"|reasons?\s+it\s+out|analyzes?\s+instead|합리화|분석적)",
    ],
    "adaptive": [],  # default
}

_NEED_KEYWORDS = {
    "safety":          [r"(?i)(safety|안전|보호|protect|shelter|위험.*회피)"],
    "autonomy":        [r"(?i)(autonom|자율|independence|자유|통제.*거부)"],
    "belonging":       [r"(?i)(belong|소속|accepted|인정.*받|group|무리)"],
    "esteem":          [r"(?i)(esteem|존경|pride|자존|reputation|명예|체면)"],
    "intimacy":        [r"(?i)(intimacy|친밀|closeness|사랑|affection|온기)"],
    "self-actualization": [r"(?i)(self.actual|자아실현|purpose|사명|calling|꿈)"],
}

# Sections to exclude from matching (combat, appearance, etc.)
_EXCLUDE_SECTIONS = re.compile(
    r'(?i)(?:combat\s*profile|battle\s*style|appearance|외형|전투\s*스타일|무기)'
)


# ─────────────────────────────────────────────────────────────────────
# [2026-07-28] 명시 라벨 — 키워드 사냥의 한계에 대한 답
#   패턴을 아무리 늘려도 **잘 쓴 시트일수록 안 잡힌다**. 좋은 산문은 "suppress"라고
#   쓰지 않고 행동으로 보여주기 때문이다(실측: 5인 영어 시트 중 1명만 자동 인식).
#   패턴을 계속 붙이면 오탐만 늘고, 시트 저자가 특정 단어를 쓰도록 강요하는 역전이 된다.
#   → 로어북 가이드 원칙 그대로 간다: **쓴 만큼 정확히 뽑힌다.**
#   시트에 `- Attachment: avoidant` 한 줄이면 확정. 안 쓰면 자동 추출이 보조로 돈다.
# ─────────────────────────────────────────────────────────────────────
_EXPLICIT_TRAIT_LABELS = {
    "attachment_style": (("attachment", "애착", "attachment style", "애착유형", "애착 유형"),
                         ("secure", "anxious", "avoidant", "disorganized")),
    "moral_stance":     (("moral", "morality", "moral stance", "도덕", "도덕성", "윤리"),
                         ("neutral", "disengaged", "conflicted", "principled")),
    "coping_style":     (("coping", "coping style", "대처", "대처방식", "대처 방식"),
                         ("adaptive", "avoidant", "confrontational", "intellectual")),
}
# 한국어 값 표기도 받는다 (시트를 한글로 쓰는 경우)
_TRAIT_VALUE_ALIASES = {
    "안정": "secure", "불안": "anxious", "회피": "avoidant", "혼란": "disorganized",
    "중립": "neutral", "이탈": "disengaged", "갈등": "conflicted", "원칙": "principled",
    "적응": "adaptive", "대립": "confrontational", "지성": "intellectual", "합리화": "intellectual",
}


def _extract_explicit_traits(text: str) -> Dict[str, str]:
    """`- Attachment: avoidant` 류 명시 라벨을 읽는다. 값이 enum에 없으면 무시."""
    found = {}
    for raw in (text or "").split("\n"):
        line = raw.strip().lstrip("-*># ").strip()
        if ":" not in line:
            continue
        key, val = line.split(":", 1)
        # `**Coping:** x` 처럼 볼드가 콜론 바깥/안쪽 어디에 붙어도 벗겨지도록 반복 strip
        key = key.strip().strip("*_").strip().lower()
        val = val.strip().strip("*_").strip().lower()
        if not val:
            continue
        val = _TRAIT_VALUE_ALIASES.get(val, val)
        for field, (labels, allowed) in _EXPLICIT_TRAIT_LABELS.items():
            if key in labels and val in allowed and field not in found:
                found[field] = val
                break
    return found


def extract_static_traits(npc_name: str, profile_text: str) -> Dict[str, str]:
    """프로필에서 정적 심리 특성 추출. Flash 없이 코드만으로.

    우선순위: ① 시트의 명시 라벨 ② 키워드 자동 추출 ③ 기본값.
    """
    if not profile_text or len(profile_text) < 100:
        return {}

    # Filter out combat/appearance sections for psychological matching
    filtered = _filter_psychological_sections(profile_text)
    explicit = _extract_explicit_traits(profile_text)   # 라벨은 필터 전 원문에서

    traits = {}
    traits["attachment_style"] = explicit.get(
        "attachment_style") or _match_category(filtered, ATTACHMENT_PATTERNS, "secure")
    traits["moral_stance"] = explicit.get(
        "moral_stance") or _match_category(filtered, MORAL_STANCE_PATTERNS, "neutral")
    traits["coping_style"] = explicit.get(
        "coping_style") or _match_category(filtered, COPING_PATTERNS, "adaptive")
    traits["core_needs"] = _extract_core_needs(filtered)
    return {k: v for k, v in traits.items() if v}


def _filter_psychological_sections(text: str) -> str:
    """Remove combat/appearance sections that could cause false positives."""
    lines = text.split('\n')
    filtered = []
    skip = False
    for line in lines:
        if _EXCLUDE_SECTIONS.search(line):
            skip = True
            continue
        # New section header resets skip
        if skip and (line.startswith('#') or line.startswith('##') or line.startswith('[')):
            skip = False
        if not skip:
            filtered.append(line)
    return '\n'.join(filtered)


def _match_category(text: str, patterns: Dict[str, list], default: str) -> str:
    """패턴 매칭 점수가 가장 높은 카테고리 반환.

    [2026-07-28] 임계 2 → 1. 구 임계는 카테고리당 패턴이 1개뿐인 항목들을
    **구조적으로 도달 불가**로 만들었다. 패턴에서 범용어를 걷어내고 그 카테고리를
    명시적으로 가리키는 표현만 남겼으므로 1개 매칭으로 확정한다.
    동점이면 default — 서로 다른 두 성향이 같은 강도로 잡히면 판단을 보류하는 쪽이 안전하다
    (그런 인물은 시트에 명시 라벨을 쓰면 된다).
    """
    scores = {}
    for category, pats in patterns.items():
        score = sum(1 for p in pats if re.search(p, text))
        if score >= 1:
            scores[category] = score
    if not scores:
        return default
    _best = max(scores.values())
    _top = [c for c, s in scores.items() if s == _best]
    return _top[0] if len(_top) == 1 else default


def _extract_core_needs(text: str) -> str:
    """프로필에서 핵심 욕구 추출 (최대 2개)."""
    scores = {}
    for need, pats in _NEED_KEYWORDS.items():
        score = sum(len(re.findall(p, text)) for p in pats)
        if score > 0:
            scores[need] = score
    if scores:
        top = sorted(scores, key=scores.get, reverse=True)[:2]
        return ",".join(top)
    return ""
