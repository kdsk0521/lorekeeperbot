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


def strip_discord_markdown(text: str) -> str:
    """메시지 앞뒤 및 내부의 디스코드 마크다운 기호를 제거합니다."""
    if not text:
        return ""
    
    patterns = [r'\*\*\*', r'\*\*', r'___', r'__', r'~~', r'\|\|', r'`']
    clean_text = text
    
    for p in patterns:
        clean_text = re.sub(p, '', clean_text)
    
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


def roll_dice(dice_str: str, mode: str = "normal") -> Optional[Tuple[int, Any, int, Optional[str]]]:
    """
    주사위 식(예: 1d20+3)을 파싱하여 결과를 계산합니다.
    
    Args:
        dice_str: 주사위 식 문자열 (예: "2d6+3", "1d20")
        mode: 굴림 모드 - 'normal', 'adv' (유리함), 'dis' (불리함)
    
    Returns:
        Tuple[최종값, 굴림결과, 수정치, 상세설명] 또는 None (파싱 실패 시)
    """
    # 정규식: 숫자d숫자(+/-숫자)
    match = re.search(r"(\d+)d(\d+)([+-]\d+)?", dice_str.lower())
    if not match:
        return None
    
    count = int(match.group(1))
    sides = int(match.group(2))
    mod = int(match.group(3)) if match.group(3) else 0
    
    # 유효성 검사: 시스템 부하 및 비정상 입력 방지
    if count > MAX_DICE_COUNT:
        return None
    if sides > MAX_DICE_SIDES or sides < 1:
        return None
    if count < 1:
        return None
    
    def _roll_once() -> Tuple[int, List[int]]:
        rolls = [random.randint(1, sides) for _ in range(count)]
        return sum(rolls), rolls
    
    # 유리함/불리함 처리 (D&D 5e 방식: 2번 굴려서 선택)
    if mode in ['adv', 'dis']:
        val1, rolls1 = _roll_once()
        val2, rolls2 = _roll_once()
        
        if mode == 'adv':
            final_val = max(val1, val2)
            detail = f"[{val1}, {val2}] ➔ **{final_val}** (유리함)"
        else:  # dis
            final_val = min(val1, val2)
            detail = f"[{val1}, {val2}] ➔ **{final_val}** (불리함)"
        
        rolls_str = f"{rolls1} vs {rolls2}"
        return final_val + mod, rolls_str, mod, detail
    
    # 일반 굴림
    total, rolls = _roll_once()
    return total + mod, rolls, mod, None


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
            '잠수': 'afk',
            'afk': 'afk',
            '이탈': 'leave',
            '퇴장': 'leave',
            'leave': 'leave',
            '복귀': 'back',
            '컴백': 'back',
            'back': 'back',
            
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
            
            # === AI 분석 도구 ===
            '분석': 'analyze',
            'analyze': 'analyze',
            '일관성': 'consistency',
            'consistency': 'consistency',
            '세계규칙': 'worldrules',
            'worldrules': 'worldrules',
            '예측': 'forecast',
            'forecast': 'forecast',
            '둠': 'doom',
            'doom': 'doom',
            
            # === 주사위 ===
            '주사위': 'roll',
            '굴림': 'roll',
            'r': 'roll',
            'roll': 'roll',
            
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
        if command == 'roll':
            # 모드 판별
            mode = "normal"
            args_lower = args.lower()
            
            if "adv" in args_lower or "유리" in args:
                mode = "adv"
            elif "dis" in args_lower or "불리" in args:
                mode = "dis"
            
            res = roll_dice(args, mode)
            
            if res:
                total, rolls, mod, detail = res
                mod_txt = f"{mod:+}" if mod != 0 else ""
                
                # 출력 메시지 구성
                if detail:
                    msg = (
                        f"🎲 **Roll ({mode.upper()})**: `{args}`\n"
                        f"Process: {detail} {mod_txt}\n"
                        f"**Final Result:** {total}"
                    )
                else:
                    msg = (
                        f"🎲 **Roll**: `{args}`\n"
                        f"Result: {total} (Dice: {rolls} {mod_txt})\n"
                        f"_💡 높을수록 좋은 결과 - AI가 상황에 맞게 해석합니다_"
                    )
                
                return {'type': 'dice', 'content': msg}
            
            return {
                'type': 'dice',
                'content': (
                    "❌ 주사위 형식 오류\n"
                    "예시: `!r 1d20`, `!r 1d100`, `!r 2d6+3`, `!r 1d20 유리`\n"
                    "_💡 주사위는 선택 사항입니다. AI가 상황을 보고 판정합니다._"
                )
            }
        
        return {'type': 'command', 'command': command, 'content': args}
    
    # 2. OOC 감지 - 메시지 내 (OOC: 내용) 패턴 추출
    # 메시지 어디에든 (OOC: ...) 가 있으면 추출
    ooc_pattern = r'\((?:OOC|ooc)[:\s]+(.+?)\)'
    ooc_match = re.search(ooc_pattern, clean_content, re.IGNORECASE | re.DOTALL)
    
    if ooc_match:
        ooc_content = ooc_match.group(1).strip()
        # OOC 부분을 제거한 나머지 텍스트
        remaining_text = re.sub(ooc_pattern, '', clean_content, flags=re.IGNORECASE | re.DOTALL).strip()
        
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
