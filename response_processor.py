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
    
    확장: 2인칭 지칭(당신, 너) 및 서술형 행동 강제 탐지 추가.
    예외: 따옴표 내 대사는 허용.
    """
    violations = []
    
    # 1. PC 이름 기반 탐지 (기존)
    if pc_names:
        for pc in pc_names:
            if not pc or pc == "Unknown":
                continue
            safe_pc = re.escape(pc)
            
            patterns = [
                # Dialogue
                (rf'{safe_pc}[이가은는]?\s*["\'].*?["\'].*?(?:말했다|대답했다|중얼거렸다|외쳤다|물었다|진술했다)', 'dialogue'),
                (rf'["\'].*?["\'].*?(?:라고|하고|이라며)\s*{safe_pc}', 'dialogue'),
                
                # Action (Expanded)
                (rf'{safe_pc}[이가은는]?\s*(?:고개를|손을|몸을|시선을).*?(?:끄덕|흔들|돌렸|뻗었|응시|바라)', 'action'),
                (rf'{safe_pc}[이가은는]?\s*(?:일어났다|앉았다|걸었다|뛰었다|멈췄다|바라보았다|웃었다|울었다|미소지었다|한숨을|소리쳤다)', 'action'),
                
                # Reaction (Expanded)
                (rf'{safe_pc}[의]?\s*(?:표정|눈|얼굴|심장|호흡)[이가]?\s*(?:굳|밝|어두|놀|떨|차갑|뜨겁)', 'reaction'),
                (rf'{safe_pc}[은는이가]?\s*(?:생각했다|느꼈다|깨달았다|결심했다|기억했다|떠올렸다|추측했다)', 'thought'),
            ]
            
            for pattern, vtype in patterns:
                for match in re.finditer(pattern, response, re.IGNORECASE):
                    violations.append({
                        'pc': pc,
                        'type': vtype,
                        'matched': match.group(),
                        'start': match.start(),
                        'end': match.end()
                    })

    # 2. 2인칭 지칭 및 행동 강제 탐지 (신규)
    # 당신/너 가 주어로 쓰이고 뒤에 서술어(다/음/함)로 끝나는 경우
    second_person_patterns = [
        # 일반적인 2인칭 행동 강제 (은/는/이/가 + ~다)
        (r'(?:당신|너|플레이어)(?:은|는|이|가)\s*.*?(?:했다|켰다|껐다|느꼈다|생각했다|말했다|보았다|멈췄다|끄덕였다|웃었다|바라보았다|앉았다|일어났다|걸었다|뛰었다)', 'impersonation_2nd'),
        # 소유격 또는 신체 부위 지칭 후 상태 변화 (눈이 빛났다, 가슴이 떨렸다 등)
        (r'당신(?:의|이)\s*(?:눈|손|몸|기억|생각|가슴|심장|호흡)(?:이|은|는|을)?\s*.*?(?:했다|느꼈다|떠올랐다|움직였다|굳었다|빛났다|떨렸다|가냘퍼졌다|거칠어졌다)', 'impersonation_2nd'),
    ]
    
    for pattern, vtype in second_person_patterns:
        for match in re.finditer(pattern, response, re.IGNORECASE):
            violations.append({
                'pc': "Player",
                'type': vtype,
                'matched': match.group()[:50],
                'start': match.start(),
                'end': match.end()
            })

    # 3. 따옴표(대사) 예외 필터링
    # 현재 위반 위치가 따옴표 내부인지 확인
    def is_inside_quotes(text, pos):
        # 텍스트의 처음부터 해당 위치까지 따옴표 개수 홀수면 내부로 간주 (간이 방식)
        # 더 정확하려면 큰따옴표의 쌍을 추적해야 함
        sub = text[:pos]
        double_quotes = sub.count('"')
        single_quotes = sub.count("'")
        return (double_quotes % 2 == 1) or (single_quotes % 2 == 1)

    filtered_violations = []
    for v in violations:
        if not is_inside_quotes(response, v['start']):
            filtered_violations.append(v)
            
    return filtered_violations


def filter_pc_impersonation(response: str, pc_names: List[str]) -> Tuple[str, List[str]]:
    """
    PC 사칭 부분을 검출하고 BKSPC를 처리한 최종 텍스트를 반환합니다.
    """
    # 1. BKSPC 먼저 처리
    clean_text = process_bkspc(response)
    
    # 2. 사칭 검출 (정제된 텍스트에서 실행)
    warnings = []
    violations = detect_pc_impersonation(clean_text, pc_names)

    if violations:
        for v in violations:
            warnings.append(f"⚠️ **PC 사칭 검출 [{v['type']}]:** `{v['matched']}...`")

    return clean_text, warnings


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
