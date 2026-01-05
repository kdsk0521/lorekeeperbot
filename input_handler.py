import re
import random

def strip_discord_markdown(text):
    """메시지 앞뒤 및 내부의 디스코드 마크다운 기호를 제거합니다."""
    if not text: return ""
    patterns = [r'\*\*\*', r'\*\*', r'___', r'__', r'~~', r'\|\|', r'`']
    clean_text = text
    for p in patterns:
        clean_text = re.sub(p, '', clean_text)
    return clean_text.strip()

def analyze_style(text, clean_text):
    """사용자의 입력 스타일(대화/행동/설명)을 분석합니다."""
    if clean_text.startswith('"') or clean_text.startswith('“') or clean_text.startswith("'"):
        return "Dialogue"
    if text.strip().startswith('*') and text.strip().endswith('*'):
        return "Action"
    return "Description"

def roll_dice(dice_str, mode="normal"):
    """
    주사위 식(예: 1d20+3)을 파싱하여 결과를 계산합니다.
    mode: 'normal', 'adv' (유리함), 'dis' (불리함)
    """
    # 공백 제거 및 소문자 변환
    # 정규식: 숫자d숫자(+/-숫자)
    match = re.search(r"(\d+)d(\d+)([+-]\d+)?", dice_str)
    if not match: return None
    
    count, sides = int(match.group(1)), int(match.group(2))
    mod = int(match.group(3)) if match.group(3) else 0
    if count > 100: return None # 시스템 부하 방지
    
    def _roll_once():
        rolls = [random.randint(1, sides) for _ in range(count)]
        return sum(rolls), rolls

    # 유리함/불리함 처리 (D&D 5e 방식: 2번 굴려서 선택)
    if mode in ['adv', 'dis']:
        val1, rolls1 = _roll_once()
        val2, rolls2 = _roll_once()
        
        if mode == 'adv':
            final_val = max(val1, val2)
            detail = f"[{val1}, {val2}] ➔ **{final_val}** (유리함)"
            rolls = f"{rolls1} vs {rolls2}"
        else: # dis
            final_val = min(val1, val2)
            detail = f"[{val1}, {val2}] ➔ **{final_val}** (불리함)"
            rolls = f"{rolls1} vs {rolls2}"
            
        return final_val + mod, rolls, mod, detail

    # 일반 굴림
    total, rolls = _roll_once()
    return total + mod, rolls, mod, None

def parse_input(content):
    """마크다운을 무시하고 한국어 명령어를 시스템 키워드로 매핑합니다."""
    raw_content = content.strip()
    clean_content = strip_discord_markdown(raw_content)
    if not clean_content: return None

    # 1. 명령어 인식
    if clean_content.startswith('!'):
        parts = clean_content[1:].split(maxsplit=1)
        command = parts[0].lower()
        args = parts[1] if len(parts) > 1 else ""
        
        # 시스템 명령어 매핑 사전 (한국어 별칭 포함)
        mapping = {
            # 세션 및 준비
            '준비': 'ready', 'ready': 'ready',
            '리셋': 'reset', '초기화': 'reset', 'reset': 'reset',
            '시작': 'start', 'start': 'start',
            '잠금해제': 'unlock', 'unlock': 'unlock',
            '잠금': 'lock', 'lock': 'lock',
            
            # 진행 및 모드
            '진행': 'next', '건너뛰기': 'next', 'next': 'next',
            '턴': 'turn', 
            '모드': 'mode', 'mode': 'mode',
            
            # 참가자 관리
            '가면': 'mask', 'mask': 'mask',
            '설명': 'desc', 'desc': 'desc',
            '정보': 'info', '내정보': 'info', 'info': 'info',
            '잠수': 'afk', 'afk': 'afk',
            '이탈': 'leave', '퇴장': 'leave', 'leave': 'leave',
            '복귀': 'back', '컴백': 'back', 'back': 'back',
            
            # 시스템 및 성장 (신규)
            '시스템': 'system', '설정': 'system', 'system': 'system',
            '경험치': 'xp', 'xp': 'xp',

            # 세계관 설정
            '로어': 'lore', 'lore': 'lore',
            '룰': 'rule', 'rule': 'rule',
            
            # 퀘스트 및 로어 박제 시스템
            '상태': 'status', 'status': 'status',
            '퀘스트': 'quest', 'quest': 'quest',
            '메모': 'memo', 'memo': 'memo',
            '완료': 'complete', 'complete': 'complete',
            '보관': 'archive', 'archive': 'archive',
            '연대기': 'lores', 'lores': 'lores',
            
            # 내보내기 기능
            '추출': 'export', '내보내기': 'export', 'export': 'export',

            # 주사위 (통합)
            '주사위': 'roll', '굴림': 'roll', 'r': 'roll', 'roll': 'roll'
        }
        
        # 매핑 확인
        if command in mapping:
            command = mapping[command]
        
        # 주사위 특수 처리 (!r, !주사위 등)
        if command == 'roll':
            # 모드 판별
            mode = "normal"
            if "adv" in args.lower() or "유리" in args: mode = "adv"
            elif "dis" in args.lower() or "불리" in args: mode = "dis"
            
            res = roll_dice(args, mode)
            if res:
                total, rolls, mod, detail = res
                mod_txt = f"{mod:+}" if mod != 0 else ""
                
                # 출력 메시지 구성
                if detail:
                    msg = f"🎲 **Roll ({mode.upper()})**: `{args}`\nProcess: {detail} {mod_txt}\n**Final Result:** {total}"
                else:
                    msg = f"🎲 **Roll**: `{args}`\nResult: {total} (Dice: {rolls} {mod_txt})"
                    
                return {'type': 'dice', 'content': msg}
            return {'type': 'dice', 'content': "❌ 주사위 형식 오류 (예: !r 1d20+5, !r 1d20 유리)"}
            
        return {'type': 'command', 'command': command, 'content': args}

    # 2. 일반 채팅
    style = analyze_style(raw_content, clean_content)
    return {'type': 'chat', 'style': style, 'content': clean_content}