"""
Lorekeeper TRPG Bot - Response Processor Module
AI 응답 후처리를 담당하는 모듈입니다.

역할:
- Scene type 키워드 감지
- PC 사칭 패턴 검출 및 필터링
- 응답 유효성 검증

persona.py에서 분리된 응답 처리 로직입니다.
프롬프트 생성 로직과 분리하여 유지보수성을 높입니다.
"""

import re
import logging
from typing import Optional, List, Dict, Tuple

logger = logging.getLogger(__name__)


# =========================================================
# Scene Type Detection (씬 타입 감지)
# =========================================================

def detect_scene_type_keywords(text: str) -> Optional[str]:
    """
    텍스트에서 씬 타입 전환 키워드를 감지합니다.

    사용자가 특정 명령어를 입력하면 씬 타입이 변경됩니다.

    Args:
        text: 입력 텍스트

    Returns:
        감지된 씬 타입 또는 None
        - 'gore': 고어 모드
        - 'nsfw': NSFW 모드
        - 'gore_nsfw': 고어+NSFW 모드
        - 'normal': 일반 모드
    """
    # Scene type transition patterns
    patterns = {
        # Gore mode entry
        'gore': [
            r'\(scene:\s*gore\)',
            r'\[gore\s*mode\]',
        ],
        # NSFW mode entry
        'nsfw': [
            r'\(scene:\s*nsfw\)',
            r'\[nsfw\s*mode\]',
        ],
        # Gore+NSFW mode entry
        'gore_nsfw': [
            r'\(scene:\s*gore\+nsfw\)',
            r'\[gore\+nsfw\s*mode\]',
            r'\[all\s*mode\]',
        ],
        # Normal mode return
        'normal': [
            r'\(scene:\s*normal\)',
            r'\[normal\s*mode\]',
            r'\(scene\s*end\)',
            r'\[scene\s*end\]',
        ],
    }

    text_lower = text.lower()

    for scene_type, pattern_list in patterns.items():
        for pattern in pattern_list:
            if re.search(pattern, text_lower, re.IGNORECASE):
                return scene_type

    return None


# =========================================================
# PC Impersonation Detection (PC 사칭 감지)
# =========================================================

def detect_pc_impersonation(response: str, pc_names: List[str]) -> List[Dict]:
    """
    AI 응답에서 PC 사칭 패턴을 검출합니다.

    PC(플레이어 캐릭터)의 대사, 행동, 내면 묘사 등을
    AI가 임의로 생성한 경우를 감지합니다.

    Args:
        response: AI 응답 텍스트
        pc_names: PC 이름 목록

    Returns:
        위반 목록 (각 항목: {pc, type, matched})
        - type: 'dialogue', 'action', 'reaction', 'thought'
    """
    violations = []
    if not pc_names:
        return violations

    for pc in pc_names:
        if not pc or pc == "Unknown":
            continue

        # Escape PC name for regex
        safe_pc = re.escape(pc)

        patterns = [
            # 1. Dialogue (대사)
            # "말했다", "대답했다" referring to PC
            (rf'{safe_pc}[이가은는]?\s*["\'].*?["\'].*?(?:말했다|대답했다|중얼거렸다|외쳤다|물었다)', 'dialogue'),
            # "..." 라고 PC가...
            (rf'["\'].*?["\'].*?(?:라고|하고)\s*{safe_pc}', 'dialogue'),

            # 2. Action (행동) - Common narrative patterns
            (rf'{safe_pc}[이가은는]?\s*(?:고개를|손을|몸을).*?(?:끄덕|흔들|돌렸|뻗었)', 'action'),
            (rf'{safe_pc}[이가은는]?\s*(?:일어났다|앉았다|걸었다|뛰었다|멈췄다|바라보았다)', 'action'),

            # 3. Reaction/Emotion (반응/내면)
            (rf'{safe_pc}[의]?\s*(?:표정|눈|얼굴)[이가]?\s*(?:굳|밝|어두|놀)', 'reaction'),
            (rf'{safe_pc}[은는이가]?\s*(?:생각했다|느꼈다|깨달았다|결심했다)', 'thought'),
        ]

        for pattern, vtype in patterns:
            matches = re.findall(pattern, response, re.IGNORECASE)
            for match in matches:
                violations.append({
                    'pc': pc,
                    'type': vtype,
                    'matched': match[:50]
                })

    return violations


def filter_pc_impersonation(response: str, pc_names: List[str]) -> Tuple[str, List[str]]:
    """
    PC 사칭 부분을 검출하고 경고를 반환합니다.

    현재는 텍스트를 직접 수정하지 않고 경고만 생성합니다.
    문장 단위 제거는 문맥 손상 위험이 있어 보류합니다.

    Args:
        response: AI 응답 텍스트
        pc_names: PC 이름 목록

    Returns:
        (필터링된 응답, 경고 메시지 목록)
    """
    warnings = []
    filtered = response

    violations = detect_pc_impersonation(response, pc_names)

    if violations:
        # For now, we return existing text but WARN heavily.
        # Removing sentences is complex without tearing logic.
        for v in violations:
            warnings.append(f"⚠️ **PC 사칭 검출 [{v['type']}]:** `{v['matched']}...`")

    return filtered, warnings


# =========================================================
# Response Validation (응답 유효성 검증)
# =========================================================

def validate_response_length(response: str, min_length: int = 100, max_length: int = 8000) -> Dict:
    """
    응답 길이 유효성을 검증합니다.

    Args:
        response: 응답 텍스트
        min_length: 최소 길이
        max_length: 최대 길이

    Returns:
        {valid: bool, length: int, message: str}
    """
    length = len(response)

    if length < min_length:
        return {
            'valid': False,
            'length': length,
            'message': f'응답이 너무 짧습니다 ({length}/{min_length}자)'
        }

    if length > max_length:
        return {
            'valid': False,
            'length': length,
            'message': f'응답이 너무 깁니다 ({length}/{max_length}자)'
        }

    return {
        'valid': True,
        'length': length,
        'message': 'OK'
    }


def extract_code_blocks(response: str) -> List[Dict]:
    """
    응답에서 코드 블록을 추출합니다.

    AI가 가끔 마크다운 코드 블록을 포함할 수 있습니다.

    Args:
        response: 응답 텍스트

    Returns:
        코드 블록 목록 [{language, code, start, end}]
    """
    blocks = []
    pattern = r'```(\w*)\n(.*?)```'

    for match in re.finditer(pattern, response, re.DOTALL):
        blocks.append({
            'language': match.group(1) or 'text',
            'code': match.group(2).strip(),
            'start': match.start(),
            'end': match.end()
        })

    return blocks
