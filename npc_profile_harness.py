"""
NPC Profile Harness (N6) — 정적 심리 특성 추출.
프로필 텍스트에서 변하지 않는 심리 특성을 코드로 1회 추출 → domain 영속 저장.
Flash가 매턴 판단할 필요 없음.
"""
import re
from typing import Dict

# Attachment style patterns (2+ matches required for non-default)
ATTACHMENT_PATTERNS = {
    "anxious": [
        r"(?i)\b(anxious|불안[한형]|집착|clingy|needy|abandonment|버림받)",
        r"(?i)\b(seeks?\s+reassurance|확인\s*받|approval[-\s]seeking)",
    ],
    "avoidant": [
        r"(?i)\b(avoidant|회피[적형]|distant|walls?\s+up|emotional\s+distance)",
        r"(?i)\b(pushes?\s+away|거리를?\s*둔?|혼자\s*있)",
    ],
    "disorganized": [
        r"(?i)\b(disorganized|혼란[형적]|push[-\s]pull|다가갔다\s*밀어)",
    ],
    "secure": [],  # default
}

MORAL_STANCE_PATTERNS = {
    "disengaged": [
        r"(?i)\b(machiavellis[mt]|narcissis[mt]|psychopath|sociopath|dark\s*triad)",
        r"(?i)\b(자기합리화|냉혈|비도덕|이기적.*극단|조종|manipulat)",
    ],
    "conflicted": [
        r"(?i)\b(guilt|죄책감|갈등|내적\s*충돌|양심|remorse|자기\s*혐오)",
    ],
    "principled": [
        r"(?i)\b(justice|정의|도덕|honorable|principled|신념|의리)",
    ],
    "neutral": [],  # default
}

COPING_PATTERNS = {
    "avoidant": [
        r"(?i)\b(suppress|억압|회피|denial|무시|bottl.*up|감정.*숨)",
    ],
    "confrontational": [
        r"(?i)\b(aggressive|공격적|대립|confrontat|직설|도발|lash.*out)",
    ],
    "intellectual": [
        r"(?i)\b(rationaliz|합리화|분석적|overthink|intellectualiz|논리로)",
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


def extract_static_traits(npc_name: str, profile_text: str) -> Dict[str, str]:
    """프로필에서 정적 심리 특성 추출. Flash 없이 코드만으로."""
    if not profile_text or len(profile_text) < 100:
        return {}

    # Filter out combat/appearance sections for psychological matching
    filtered = _filter_psychological_sections(profile_text)

    traits = {}
    traits["attachment_style"] = _match_category(filtered, ATTACHMENT_PATTERNS, "secure")
    traits["moral_stance"] = _match_category(filtered, MORAL_STANCE_PATTERNS, "neutral")
    traits["coping_style"] = _match_category(filtered, COPING_PATTERNS, "adaptive")
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
    """패턴 매칭 점수가 가장 높은 카테고리 반환. 2개 이상 매칭 필요."""
    scores = {}
    for category, pats in patterns.items():
        score = sum(1 for p in pats if re.search(p, text))
        if score >= 2:  # 임계값: 2개 이상이어야 확정
            scores[category] = score
    if scores:
        return max(scores, key=scores.get)
    return default


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
