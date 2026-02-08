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


def process_bkspc(text: str) -> str:
    """
    BKSPC 키워드를 처리하여 이전 단어(공백 포함)를 삭제합니다.
    공백과 줄바꿈을 보존하기 위해 정규표현식을 사용합니다.
    """
    if not text or "BKSPC" not in text:
        return text

    # "단어+공백" 패턴을 찾아서 리스트화 (줄바꿈 포함)
    # \S+ : 공백이 아닌 문자들 (단어)
    # \s* : 뒤따르는 공백들
    tokens = re.findall(r'\S+\s*|[\n\r]+', text)
    result = []
    
    for token in tokens:
        if "BKSPC" in token:
            # BKSPC가 포함된 토큰이면 이전 토큰 삭제
            # "BKSPC" 자체만 있는 경우와 "BKSPC "와 같이 공백이 포함된 경우 모두 처리
            if result:
                result.pop()
        else:
            result.append(token)
            
    return "".join(result).strip()


# =========================================================
# PC Impersonation Detection (PC 사칭 감지)
# =========================================================

def detect_pc_impersonation(response: str, pc_names: List[str]) -> List[Dict]:
    """
    AI 응답에서 PC 사칭 패턴을 검출합니다.

    V2: 범용 한국어 동사 어미 패턴으로 확장.
    예외: 따옴표 내 대사, NPC가 PC를 언급하는 경우는 허용.
    """
    violations = []

    # 한국어 과거형 동사 어미 (범용)
    VERB_ENDING = r'(?:했다|었다|았다|였다|셨다|ㅆ다)'
    # 내면/사고 동사
    THOUGHT_VERBS = r'(?:생각했다|느꼈다|깨달았다|결심했다|기억했다|떠올렸다|추측했다|알았다|몰랐다|원했다|바랐다|후회했다|의심했다|확신했다|짐작했다)'

    # 1. PC 이름 기반 탐지
    if pc_names:
        for pc in pc_names:
            if not pc or pc == "Unknown":
                continue
            safe_pc = re.escape(pc)

            patterns = [
                # [광범위] PC가 주어 + 임의 동사 (40자 이내)
                (rf'{safe_pc}[이가은는]\s+.{{1,40}}{VERB_ENDING}', 'action'),

                # PC 대사 생성
                (rf'{safe_pc}[이가은는]?\s*[""\u201C\u300C].*?[""\u201D\u300D]', 'dialogue'),
                (rf'[""\u201C\u300C].*?[""\u201D\u300D].*?(?:라고|하고|이라며)\s*{safe_pc}', 'dialogue'),

                # PC 신체/감정 반응 (소유격 + 주격만 — NPC→PC 행동은 허용)
                (rf'{safe_pc}[의]\s*(?:표정|눈|얼굴|심장|호흡|손|몸|입|다리|팔|어깨|등|가슴|목|머리|시선|목소리|숨결)[이가]\s*.{{1,25}}{VERB_ENDING}', 'reaction'),

                # PC 내면 묘사
                (rf'{safe_pc}[은는이가]?\s*.{{0,15}}{THOUGHT_VERBS}', 'thought'),
            ]

            for pattern, vtype in patterns:
                for match in re.finditer(pattern, response, re.IGNORECASE):
                    violations.append({
                        'pc': pc,
                        'type': vtype,
                        'matched': match.group()[:60],
                        'start': match.start(),
                        'end': match.end()
                    })

    # 2. 2인칭 지칭 행동 강제 탐지
    second_person_patterns = [
        # 당신/너 + 주격조사 + 동사
        (rf'(?:당신|너|그대|플레이어)[은는이가]\s+.{{1,40}}{VERB_ENDING}', 'impersonation_2nd'),
        # 당신의 신체/감정 + 주격 동사 (NPC→PC 행동 허용)
        (rf'(?:당신|너|그대)(?:의|이)\s*(?:눈|손|몸|기억|생각|가슴|심장|호흡|얼굴|표정|시선|발|다리|팔|목소리|숨결)[이가]\s*.{{1,25}}{VERB_ENDING}', 'impersonation_2nd'),
    ]

    for pattern, vtype in second_person_patterns:
        for match in re.finditer(pattern, response, re.IGNORECASE):
            violations.append({
                'pc': "Player",
                'type': vtype,
                'matched': match.group()[:60],
                'start': match.start(),
                'end': match.end()
            })

    # 3. 따옴표(대사) 예외 필터링
    def is_inside_quotes(text, pos):
        sub = text[:pos]
        # 큰따옴표 (일반 + 유니코드)
        dq = sub.count('"') + sub.count('\u201C') + sub.count('\u201D')
        return dq % 2 == 1

    filtered_violations = []
    for v in violations:
        if not is_inside_quotes(response, v['start']):
            filtered_violations.append(v)

    return filtered_violations


def _remove_violation_sentences(text: str, violations: List[Dict]) -> str:
    """사칭이 감지된 문장을 텍스트에서 제거합니다."""
    if not violations:
        return text

    # 각 violation이 속한 문장의 범위를 찾음
    remove_ranges = []
    for v in violations:
        start, end = v['start'], v['end']
        # 문장 시작 찾기 (마침표/줄바꿈 뒤)
        sent_start = start
        while sent_start > 0 and text[sent_start - 1] not in '.\n!?。':
            sent_start -= 1
        # 문장 끝 찾기
        sent_end = end
        while sent_end < len(text) and text[sent_end] not in '.\n!?。':
            sent_end += 1
        if sent_end < len(text):
            sent_end += 1  # 구두점 포함
        remove_ranges.append((sent_start, sent_end))

    # 겹치는 범위 병합
    remove_ranges.sort()
    merged = [list(remove_ranges[0])]
    for start, end in remove_ranges[1:]:
        if start <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])

    # 제거 후 텍스트 재조립
    result = []
    last_end = 0
    for start, end in merged:
        chunk = text[last_end:start].strip()
        if chunk:
            result.append(chunk)
        last_end = end
    trailing = text[last_end:].strip()
    if trailing:
        result.append(trailing)

    return ' '.join(result)


def filter_pc_impersonation(response: str, pc_names: List[str]) -> Tuple[str, List[Dict]]:
    """
    PC 사칭 부분을 검출하고 제거한 최종 텍스트를 반환합니다.

    Returns:
        (cleaned_text, violations): violations는 Dict 리스트 (type, matched, pc 등)
    """
    # 1. BKSPC 먼저 처리
    clean_text = process_bkspc(response)

    # 2. 사칭 검출
    violations = detect_pc_impersonation(clean_text, pc_names)

    # 3. 사칭 문장 실제 제거
    if violations:
        import logging
        for v in violations:
            logging.warning(f"[PC사칭 제거] [{v['type']}] \"{v['matched']}\"")
        clean_text = _remove_violation_sentences(clean_text, violations)

    return clean_text, violations


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


# =========================================================
# Mob Tag Cleaning (System-Level Hiding)
# =========================================================

def clean_mob_tags(text: str) -> str:
    """
    텍스트에서 모브 태그(예: #1A, #9Z)를 제거하여 사용자에게 숨깁니다.
    
    Pattern: `#` followed by exactly 2 alphanumeric characters.
    Avoids removing Markdown headers (# Title).
    """
    if not text:
        return text
        
    # Regex: Match '#' followed by 2 alphanumeric chars, ensuring word boundary or end of string?
    # Case 1: "Patient #1A says" -> "Patient says" (remove " #1A")
    # Case 2: "Patient #1A: Hello" -> "Patient: Hello" (remove " #1A")
    # Case 3: "List #1." -> "List #1." (Keep?) User said random chars.
    # Our tags are roughly 2 chars.
    # Let's match space + # + 2 alphanumeric. `\s#[A-Za-z0-9]{2}\b`?
    
    # We want to remove the tag AND the preceding space if it exists.
    # Pattern: `(\s?)#[a-zA-Z0-9]{2}(?![a-zA-Z0-9])`
    # (?![a-zA-Z0-9]) ensures we don't match #1AB (3 chars).
    
    pattern = r'(\s?)#[a-zA-Z0-9]{2}(?![a-zA-Z0-9])'
    
    def repl(match):
        # If it was "Name #1A", match group 1 is space. We remove both.
        # If it was "#1A" (start of line), match group 1 is empty. We remove tag.
        return "" 
        
    cleaned_text = re.sub(pattern, repl, text)
    return cleaned_text
