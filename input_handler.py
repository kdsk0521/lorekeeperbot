"""
Lorekeeper TRPG Bot - Input Handler Module
사용자 입력을 파싱하고 명령어를 매핑합니다.
"""

import re
import random
from typing import Optional, Dict, Any, Tuple, List

# =========================================================
# 상수 정의
# =========================================================
MAX_DICE_COUNT = 100  # 최대 주사위 개수
MAX_DICE_SIDES = 1000  # 최대 주사위 면 수

# 정규식 패턴을 미리 컴파일 (성능 최적화)
_MARKDOWN_PATTERNS = [
    re.compile(r'\*\*\*'),
    re.compile(r'\*\*'),
    re.compile(r'___'),
    re.compile(r'__'),
    re.compile(r'~~'),
    re.compile(r'\|\|'),
    re.compile(r'`')
]

# OOC 및 주사위 패턴 미리 컴파일
_OOC_PATTERN = re.compile(r'\((?:(?:OOC|ooc)[:\s]+)?(.+?)\)', re.IGNORECASE | re.DOTALL)



def strip_discord_markdown(text: str) -> str:
    """메시지 앞뒤 및 내부의 디스코드 마크다운 기호를 제거합니다."""
    if not text:
        return ""

    clean_text = text

    for pattern in _MARKDOWN_PATTERNS:
        clean_text = pattern.sub('', clean_text)

    return clean_text.strip()


def analyze_style(text: str, clean_text: str) -> str:
    """사용자의 입력 스타일(대화/행동/설명)을 분석합니다."""
    # 대화문 감지 (따옴표로 시작)
    if clean_text.startswith('"') or clean_text.startswith('"') or clean_text.startswith("'"):
        return "Dialogue"
    
    # 행동 감지 (별표로 감싸짐)
    stripped = text.strip()
    if stripped.startswith('*') and stripped.endswith('*'):
        return "Action"
    
    return "Description"




def parse_input(content: str) -> Optional[Dict[str, Any]]:
    """
    마크다운을 무시하고 한국어 명령어를 시스템 키워드로 매핑합니다.
    
    Args:
        content: 사용자 입력 문자열
    
    Returns:
        파싱된 결과 딕셔너리 또는 None
        - type: 'command', 'dice', 'chat'
        - command: (command 타입일 때) 매핑된 명령어
        - content: 인자 또는 내용
        - style: (chat 타입일 때) 'Dialogue', 'Action', 'Description'
    """
    raw_content = content.strip()
    clean_content = strip_discord_markdown(raw_content)
    
    if not clean_content:
        return None
    
    # 1. 명령어 인식 (! 로 시작)
    if clean_content.startswith('!'):
        parts = clean_content[1:].split(maxsplit=1)

        # 빈 명령어 체크 (예: "!" 단독 입력)
        if not parts:
            return None

        command = parts[0].lower()
        args = parts[1] if len(parts) > 1 else ""
        
        # 시스템 명령어 매핑 사전 (한국어 별칭 포함)
        mapping = {
            # === 세션 및 준비 ===
            '준비': 'ready',
            'ready': 'ready',
            '리셋': 'reset',
            '초기화': 'reset',
            'reset': 'reset',
            '클리어': 'clear',
            'clear': 'clear',
            '시작': 'start',
            'start': 'start',
            '잠금해제': 'unlock',
            'unlock': 'unlock',
            '잠금': 'lock',
            'lock': 'lock',
            
            # === 진행 및 모드 ===
            '진행': 'next',
            '건너뛰기': 'next',
            'next': 'next',
            '턴': 'turn',
            'turn': 'turn',
            '모드': 'mode',
            'mode': 'mode',
            
            # === 참가자 관리 ===
            '가면': 'mask',
            'mask': 'mask',
            '설명': 'desc',
            'desc': 'desc',
            '정보': 'info',
            '내정보': 'info',
            'info': 'info',
            'info': 'info',
            
            # === 세계관 설정 ===
            '로어': 'lore',
            'lore': 'lore',
            '룰': 'rule',
            'rule': 'rule',
            
            # === 퀘스트/메모 (직접 추가용) ===
            '퀘스트': 'quest',
            'quest': 'quest',
            '메모': 'memo',
            'memo': 'memo',
            '연대기': 'lores',
            'lores': 'lores',
            
            # === NPC 정보 ===
            'npc': 'npc',
            'npc정보': 'npc',
            '엔피씨': 'npc',
            'npc추가': 'addnpc',
            'addnpc': 'addnpc',
            
            # === AI 분석 도구 ===
            '분석': 'analyze',
            'analyze': 'analyze',
            'ooc': 'ooc',
            '일관성': 'consistency',
            'consistency': 'consistency',
            '세계규칙': 'worldrules',
            'worldrules': 'worldrules',
            '예측': 'forecast',
            'forecast': 'forecast',
            '둠': 'doom',
            'doom': 'doom',
            
            # === 장면 유형 전환 ===
            '장면': 'scene',
            'scene': 'scene',
            
            # === 비일상 감지 설정 ===
            '비일상': 'abnormal',
            'abnormal': 'abnormal',
            
            'abnormal': 'abnormal',
            
            # === 도움말 ===
            '도움': 'help',
            '도움말': 'help',
            'help': 'help',
            '명령어': 'help',
        }
        
        # 매핑 확인
        if command in mapping:
            command = mapping[command]
        
        # 주사위 특수 처리 (!r, !주사위 등)
        return {'type': 'command', 'command': command, 'content': args}
        
        return {'type': 'command', 'command': command, 'content': args}
    
    # 2. OOC 감지 - 메시지 내 (OOC: 내용) 패턴 추출
    # 메시지 어디에든 (OOC: ...) 가 있으면 추출
    ooc_match = _OOC_PATTERN.search(clean_content)

    if ooc_match:
        ooc_content = ooc_match.group(1).strip()
        # OOC 부분을 제거한 나머지 텍스트
        remaining_text = _OOC_PATTERN.sub('', clean_content).strip()
        
        if remaining_text:
            # OOC + 행동/대사가 함께 있음 → 둘 다 처리
            style = analyze_style(raw_content, remaining_text)
            return {
                'type': 'chat_with_ooc',
                'ooc_content': ooc_content,
                'chat_content': remaining_text,
                'style': style
            }
        else:
            # OOC만 있음
            return {'type': 'ooc', 'content': ooc_content}
    
    # 3. 일반 채팅
    style = analyze_style(raw_content, clean_content)
    return {'type': 'chat', 'style': style, 'content': clean_content}
