"""
Notation Compositor (N8) — 장면 연출 합성 엔진.
⚗️ EXPERIMENTAL — A/B 테스트 필수. 기존 방식 rollback 가능.

여러 레이어의 3-Axis Notation (♪음악 | ▶카메라 | ◎사진)을
관계 정리하여 중복 제거, 충돌 감지, 우선순위 적용.
"""
import re
import logging
from typing import Dict, List, Tuple, Optional
from dataclasses import dataclass, field

logger = logging.getLogger("NotationCompositor")

# 장면 유형별 레이어 우선순위
LAYER_PRIORITY = {
    "combat":   ["body", "situation", "scene", "soma"],
    "social":   ["soma", "scene", "psyche", "situation"],
    "intimate": ["soma", "psyche", "scene", "body"],
    "normal":   ["scene", "soma", "body", "psyche"],
    "summary":  ["scene"],
    "gore":     ["body", "scene", "soma", "psyche"],
}

# 장르별 Notation 가중 — theory_emphasis_engine.py GENRE_THEORY_WEIGHTS와 동기화
GENRE_NOTATION_WEIGHTS = {
    "high_fantasy": {
        "dominant": "scene", "emphasize": ["world", "body"],
        "suppress": ["affect"],
        "reframe": {"soma": "caste signal", "situation": "honor"},
    },
    "wuxia": {
        "dominant": "body", "emphasize": ["soma", "situation"],
        "suppress": ["psyche"],
        "reframe": {"scene": "qi flow", "affect": "loyalty"},
    },
    "cyberpunk": {
        "dominant": "scene", "emphasize": ["world", "situation"],
        "suppress": ["affect"],
        "reframe": {"soma": "implant noise", "body": "augmented"},
    },
    "modern": {
        "dominant": "soma", "emphasize": ["affect", "psyche"],
        "suppress": ["situation", "world"],
        "reframe": {"body": "comfort", "scene": "mundane detail"},
    },
    "cosmic_horror": {
        "dominant": "world", "emphasize": ["consciousness", "scene"],
        "suppress": ["body"],
        "reframe": {"soma": "dissolution", "affect": "cosmic dread"},
    },
    "noir": {
        "dominant": "scene", "emphasize": ["world", "soma"],
        "suppress": ["psyche"],
        "reframe": {"body": "fatigue", "scene": "chiaroscuro"},
    },
    "comedy": {
        "dominant": "body", "emphasize": ["psyche", "situation"],
        "suppress": ["world", "consciousness"],
        "reframe": {"soma": "overreaction", "scene": "timing"},
    },
    "romance": {
        "dominant": "soma", "emphasize": ["psyche", "affect"],
        "suppress": ["situation"],
        "reframe": {"scene": "intimacy lens", "body": "vulnerability"},
    },
    "drama": {
        "dominant": "soma", "emphasize": ["affect", "consciousness"],
        "suppress": [],
        "reframe": {"scene": "power geometry", "situation": "stakes"},
    },
    "horror": {
        "dominant": "scene", "emphasize": ["soma", "consciousness"],
        "suppress": ["psyche"],
        "reframe": {"body": "visceral", "affect": "dread"},
    },
    "action": {
        "dominant": "body", "emphasize": ["situation", "scene"],
        "suppress": ["affect", "psyche"],
        "reframe": {"soma": "adrenaline", "scene": "kinetic"},
    },
    "slice_of_life": {
        "dominant": "soma", "emphasize": ["affect", "psyche"],
        "suppress": ["world", "situation"],
        "reframe": {"scene": "quiet detail", "body": "habitual"},
    },
    "post_apocalypse": {
        "dominant": "scene", "emphasize": ["body", "world"],
        "suppress": ["psyche"],
        "reframe": {"soma": "survival reflex", "affect": "numb"},
    },
    "space_opera": {
        "dominant": "scene", "emphasize": ["world", "situation"],
        "suppress": ["affect"],
        "reframe": {"soma": "zero-g", "body": "suit feedback"},
    },
}


@dataclass
class ParsedNotation:
    """파싱된 Notation 레이어."""
    layer: str           # e.g., "soma", "scene", "body", "psyche"
    music: str = ""      # ♪ 축
    camera: str = ""     # ▶ 축
    photo: str = ""      # ◎ 축
    raw: str = ""        # 원본 텍스트
    abbreviated: bool = False
    lens: str = ""       # reframe 렌즈 주석


def _parse_notation(layer: str, notation: str) -> ParsedNotation:
    """Notation 문자열을 3축으로 파싱."""
    p = ParsedNotation(layer=layer, raw=notation)

    # ♪ 추출
    m = re.search(r'♪\s*([^|▶◎]+)', notation)
    if m:
        p.music = m.group(1).strip()

    # ▶ 추출
    m = re.search(r'▶\s*([^|♪◎]+)', notation)
    if m:
        p.camera = m.group(1).strip()

    # ◎ 추출
    m = re.search(r'◎\s*([^|♪▶]+)', notation)
    if m:
        p.photo = m.group(1).strip()

    # 축 기호 없는 단순 텍스트
    if not p.music and not p.camera and not p.photo:
        p.music = notation.strip()

    return p


def _extract_consensus(parsed: List[ParsedNotation]) -> str:
    """모든 레이어에서 공통인 값 추출 -> 전체 톤."""
    if len(parsed) < 2:
        return ""

    # 음악 축 합의
    music_vals = [p.music for p in parsed if p.music and not p.abbreviated]
    if music_vals and len(set(music_vals)) == 1:
        return f"♪ {music_vals[0]}"

    return ""


def _detect_conflicts(parsed: List[ParsedNotation]) -> List[str]:
    """같은 축에서 레이어 간 충돌 감지."""
    conflicts = []

    # 음악 축 충돌
    music_map = {p.layer: p.music for p in parsed if p.music and not p.abbreviated}
    music_vals = list(set(music_map.values()))
    if len(music_vals) > 1:
        pairs = [f"{k}={v}" for k, v in music_map.items()]
        conflicts.append(f"♪ 충돌: {' vs '.join(pairs)}")

    # 사진 축 충돌 (시간밀도)
    photo_map = {p.layer: p.photo for p in parsed if p.photo and not p.abbreviated}
    photo_vals = list(set(photo_map.values()))
    if len(photo_vals) > 1:
        pairs = [f"{k}={v}" for k, v in photo_map.items()]
        conflicts.append(f"◎ 충돌: {' vs '.join(pairs)}")

    return conflicts


def _get_dominant(parsed: List[ParsedNotation], priority: List[str]) -> Optional[ParsedNotation]:
    """우선순위에 따라 지배 레이어 선택."""
    layer_map = {p.layer: p for p in parsed if not p.abbreviated}
    for layer_name in priority:
        if layer_name in layer_map:
            return layer_map[layer_name]
    # Fallback: first non-abbreviated
    for p in parsed:
        if not p.abbreviated:
            return p
    return parsed[0] if parsed else None


def _diff_from_dominant(notation: ParsedNotation, dominant: ParsedNotation) -> str:
    """지배 레이어와의 차이만 추출."""
    if notation.layer == dominant.layer:
        return ""

    diffs = []
    if notation.music and notation.music != dominant.music:
        diffs.append(f"♪ {notation.music}")
    if notation.camera and notation.camera != dominant.camera:
        diffs.append(f"▶ {notation.camera}")
    if notation.photo and notation.photo != dominant.photo:
        diffs.append(f"◎ {notation.photo}")

    return " | ".join(diffs) if diffs else ""


def compose_notations(
    notations: List[Tuple[str, str]],
    scene_type: str = "normal",
    genre: str = "",
) -> str:
    """여러 레이어의 Notation을 관계 정리.

    Args:
        notations: [(layer_name, notation_string), ...]
        scene_type: combat/social/intimate/normal/summary/gore
        genre: primary genre key for GENRE_NOTATION_WEIGHTS

    Returns: 합성된 Notation 디렉티브 문자열
    """
    if not notations:
        return ""

    parsed = [_parse_notation(layer, notation) for layer, notation in notations if notation]
    if not parsed:
        return ""

    # 장르 가중 적용
    weights = GENRE_NOTATION_WEIGHTS.get(genre, {})
    suppress_layers = weights.get("suppress", [])
    reframe_map = weights.get("reframe", {})
    dominant_layer = weights.get("dominant")

    # 1. suppress 레이어 -> 축약
    for p in parsed:
        if p.layer in suppress_layers:
            p.abbreviated = True

    # 2. reframe 적용 -> 렌즈 주석
    for p in parsed:
        if p.layer in reframe_map:
            p.lens = reframe_map[p.layer]

    # 3. 우선순위 결정
    if dominant_layer:
        # 장르 지배 레이어를 최우선으로
        base_priority = LAYER_PRIORITY.get(scene_type, LAYER_PRIORITY["normal"])
        priority = [dominant_layer] + [l for l in base_priority if l != dominant_layer]
    else:
        priority = LAYER_PRIORITY.get(scene_type, LAYER_PRIORITY["normal"])

    # 4. 중복 제거 (전체 톤 추출)
    global_tone = _extract_consensus(parsed)

    # 5. 충돌 감지
    conflicts = _detect_conflicts(parsed)

    # 6. 지배 레이어 선택
    dominant = _get_dominant(parsed, priority)
    if not dominant:
        return ""

    # 7. 출력 합성
    lines = []
    if global_tone:
        lines.append(f"[전체 톤] {global_tone}")

    # 지배 레이어 출력
    dom_parts = []
    if dominant.music:
        dom_parts.append(f"♪ {dominant.music}")
    if dominant.camera:
        dom_parts.append(f"▶ {dominant.camera}")
    if dominant.photo:
        dom_parts.append(f"◎ {dominant.photo}")
    dom_lens = f" ({dominant.lens})" if dominant.lens else ""
    lines.append(f"[지배:{dominant.layer}{dom_lens}] {' | '.join(dom_parts)}")

    # 충돌 디렉티브
    for conflict in conflicts:
        lines.append(f"[갈등] {conflict}")

    # 차이 레이어
    for p in parsed:
        if p.abbreviated:
            continue
        diff = _diff_from_dominant(p, dominant)
        if diff:
            lens_tag = f" ({p.lens})" if p.lens else ""
            lines.append(f"[{p.layer}{lens_tag}] {diff}")

    return "\n".join(lines)


def compose_transition(prev_frame: dict, current_energy: str) -> str:
    """이전 프레임과 현재 에너지 방향의 차이 -> 전환 디렉티브.

    Args:
        prev_frame: previous DAI snapshot dict
        current_energy: current energy_direction string
    """
    prev_energy = prev_frame.get("dai_snapshot", {}).get("energy_direction", "idle")

    if prev_energy == current_energy:
        return ""

    transitions = {
        ("idle", "rising"):       "[전환] 고요함이 깨진다 — 감각 전환점",
        ("rising", "detonation"): "[전환] 폭발 직전 — 모든 감각이 수렴한다",
        ("detonation", "aftershock"): "[전환] 폭발 이후 — 잔향이 공간을 채운다",
        ("aftershock", "falling"): "[전환] 여진이 잦아든다 — 공간이 숨을 쉰다",
        ("falling", "idle"):      "[전환] 정적이 돌아온다 — 새로운 균형",
        ("rising", "idle"):       "[전환] 긴장이 허탈하게 풀린다",
        ("detonation", "idle"):   "[전환] 폭풍 후의 고요",
        ("idle", "detonation"):   "[전환] 기습적 폭발 — 전조 없는 충격",
    }

    return transitions.get((prev_energy, current_energy), "")
