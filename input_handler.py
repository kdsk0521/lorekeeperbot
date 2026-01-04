import re
import random

def analyze_style(text):
    """
    사용자의 입력 텍스트 스타일을 분석하여 태그를 반환합니다.
    - " " 또는 ' ' : Dialogue (직접 대화)
    - * * : Action (행동/감정 묘사)
    - 그 외 : Description (일반 지문/설명)
    """
    text = text.strip()
    if not text: return "Description"
    
    if text.startswith('"') or text.startswith('“') or text.startswith("'") or text.startswith("‘"):
        return "Dialogue"
    elif text.startswith('*'):
        return "Action"
    else:
        return "Description"

def roll_dice(dice_str):
    """
    주사위 텍스트(예: 2d6+3)를 파싱하고 결과를 계산합니다.
    Returns: (총합, [개별 주사위 값 리스트], 보정치)
    """
    # 정규식: (숫자)d(숫자) +/-(숫자(선택사항))
    match = re.match(r"(\d+)d(\d+)([+-]\d+)?", dice_str.lower().replace(" ", ""))
    if not match: return None
    
    count = int(match.group(1)) # 주사위 개수
    sides = int(match.group(2)) # 주사위 면 수
    mod_str = match.group(3)    # 보정치 (+3, -1 등)
    mod = int(mod_str) if mod_str else 0
    
    # 너무 많은 주사위 굴림 방지 (서버 부하/스팸 방지)
    if count > 100: return None
    
    rolls = [random.randint(1, sides) for _ in range(count)]
    return sum(rolls) + mod, rolls, mod

def format_dice_result(name, dice_str, total, rolls, mod):
    """주사위 결과를 보기 좋은 문자열로 포맷팅합니다."""
    mod_text = f"{mod:+}" if mod != 0 else ""
    return f"🎲 **{name}** Roll: `{dice_str}`\nResult: {total} (Dice: {rolls} {mod_text})"

def parse_input(content):
    """
    사용자 메시지를 분석하여 명령어, 주사위, 일반 대화로 분류합니다.
    Returns: {'type': 'command'|'dice'|'chat', 'content': ...}
    """
    content = content.strip()
    
    # 1. 명령어 (!로 시작)
    if content.startswith('!'):
        parts = content[1:].split()
        command = parts[0].lower()
        args = " ".join(parts[1:])
        
        # !roll 같은 명령어는 여기서 바로 주사위 로직으로 연결
        if command in ['roll', '굴림', 'r']:
            result = roll_dice(args)
            if result:
                total, rolls, mod = result
                formatted = format_dice_result("Player", args, total, rolls, mod)
                return {'type': 'dice', 'content': formatted}
            else:
                return {'type': 'dice', 'content': "❌ 형식 오류 (예: !r 2d6)"}
        
        # 그 외 명령어는 main.py에서 처리하도록 전달
        return {'type': 'command', 'command': command, 'content': args}

    # 2. 인라인 주사위 (텍스트 자체가 주사위 식인 경우)
    dice_match = roll_dice(content)
    if dice_match:
        total, rolls, mod = dice_match
        formatted = format_dice_result("Player", content, total, rolls, mod)
        return {'type': 'dice', 'content': formatted}

    # 3. 일반 대화
    return {'type': 'chat', 'content': content}