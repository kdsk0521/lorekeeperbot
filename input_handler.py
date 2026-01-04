import re
import random

def strip_discord_markdown(text):
    if not text: return ""
    patterns = [r'\*\*\*', r'\*\*', r'___', r'__', r'~~', r'\|\|', r'`']
    clean_text = text
    for p in patterns:
        clean_text = re.sub(p, '', clean_text)
    return clean_text.strip()

def analyze_style(text, clean_text):
    if clean_text.startswith('"') or clean_text.startswith('“') or clean_text.startswith("'"): return "Dialogue"
    if text.strip().startswith('*') and text.strip().endswith('*'): return "Action"
    return "Description"

def roll_dice(dice_str):
    match = re.match(r"(\d+)d(\d+)([+-]\d+)?", dice_str.lower().replace(" ", ""))
    if not match: return None
    
    count, sides = int(match.group(1)), int(match.group(2))
    mod = int(match.group(3)) if match.group(3) else 0
    if count > 100: return None
    
    rolls = [random.randint(1, sides) for _ in range(count)]
    return sum(rolls) + mod, rolls, mod

def parse_input(content):
    raw_content = content.strip()
    clean_content = strip_discord_markdown(raw_content)
    if not clean_content: return None

    if clean_content.startswith('!'):
        parts = clean_content[1:].split(maxsplit=1)
        command = parts[0].lower()
        args = parts[1] if len(parts) > 1 else ""
        
        mapping = {
            '준비': 'ready', 'ready': 'ready',
            '리셋': 'reset', '초기화': 'reset', 'reset': 'reset',
            '시작': 'start', 'start': 'start',
            '잠금해제': 'unlock', 'unlock': 'unlock',
            '잠금': 'lock', 'lock': 'lock',
            '진행': 'next', '건너뛰기': 'next', 'next': 'next',
            '가면': 'mask', 'mask': 'mask',
            '설명': 'desc', 'desc': 'desc',
            '정보': 'info', '내정보': 'info', 'info': 'info',
            '잠수': 'afk', 'afk': 'afk',
            '이탈': 'leave', '퇴장': 'leave', 'leave': 'leave',
            '복귀': 'back', '컴백': 'back', 'back': 'back',
            '로어': 'lore', 'lore': 'lore',
            '룰': 'rule', 'rule': 'rule',
            '상태': 'status', 'status': 'status',
            '퀘스트': 'quest', 'quest': 'quest',
            '메모': 'memo', 'memo': 'memo',
            '완료': 'complete', 'complete': 'complete',
            '보관': 'archive', 'archive': 'archive',
            '연대기': 'lores', 'lores': 'lores',
            '추출': 'export', '내보내기': 'export', 'export': 'export'
        }
        
        if command in mapping: command = mapping[command]
        
        if command in ['r', 'roll', '굴림']:
            res = roll_dice(args)
            if res:
                total, rolls, mod = res
                mod_txt = f"{mod:+}" if mod != 0 else ""
                msg = f"🎲 **Roll**: `{args}`\nResult: {total} (Dice: {rolls} {mod_txt})"
                return {'type': 'dice', 'content': msg}
            return {'type': 'dice', 'content': "❌ 주사위 형식 오류 (예: !r 1d20+5)"}
            
        return {'type': 'command', 'command': command, 'content': args}

    style = analyze_style(raw_content, clean_content)
    return {'type': 'chat', 'style': style, 'content': clean_content}