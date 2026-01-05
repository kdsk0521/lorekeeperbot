import domain_manager
import json
import requests
import time
import os
import re

API_KEY = os.getenv("GEMINI_API_KEY", "")

def call_gemini_api(prompt, system_instruction=""):
    if not API_KEY: return None
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash-preview-09-2025:generateContent?key={API_KEY}"
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "systemInstruction": {"parts": [{"text": system_instruction}]},
        "generationConfig": {"responseMimeType": "application/json"}
    }
    for i in range(3):
        try:
            response = requests.post(url, json=payload, timeout=10)
            if response.status_code == 200:
                result = response.json()
                if 'candidates' in result and result['candidates']:
                    raw_text = result['candidates'][0]['content']['parts'][0]['text']
                    clean_text = re.sub(r"```(json)?", "", raw_text).strip()
                    return json.loads(clean_text)
            time.sleep(1)
        except Exception: time.sleep(1)
    return None

def get_objective_context(channel_id):
    board = domain_manager.get_quest_board(channel_id)
    active_quests = board.get("active", [])
    memos = board.get("memo", [])
    lore = board.get("lore", []) 
    context = "### [SYSTEM MEMORY: QUEST BOARD & ARCHIVES]\n"
    if lore:
        context += "\n[Chronicles (Long-term Memory)]\n"
        for entry in lore[-5:]: context += f"- {entry.get('title')}: {entry.get('content')}\n"
    if active_quests:
        context += "\n[Active Quests (Objectives)]\n"
        for q in active_quests: context += f"- [QUEST] {q}\n"
    if memos:
        context += "\n[Memos (Clues & Notes)]\n"
        for m in memos: context += f"- [NOTE] {m}\n"
    return context

def add_quest(channel_id, content):
    board = domain_manager.get_quest_board(channel_id)
    if content not in board["active"]:
        board["active"].append(content)
        domain_manager.update_quest_board(channel_id, board)
        return f"⚔️ **[퀘스트 수주]** {content}"
    return None

def complete_quest(channel_id, content):
    """퀘스트 완료 및 자동 연대기 박제"""
    board = domain_manager.get_quest_board(channel_id)
    target = None
    
    # 부분 일치로 찾기
    for q in board["active"]:
        if content in q: target = q; break
            
    if target:
        board["active"].remove(target)
        lore_entry = {
            "title": f"달성: {target}",
            "content": f"파티는 '{target}'의 과업을 완수하였다.",
            "timestamp": time.time()
        }
        if "lore" not in board: board["lore"] = []
        board["lore"].append(lore_entry)
        domain_manager.update_quest_board(channel_id, board)
        return f"🏆 **[퀘스트 완료]** {target} (연대기에 기록됨)"
    return None

def add_memo(channel_id, content):
    board = domain_manager.get_quest_board(channel_id)
    if "memo" not in board: board["memo"] = []
    if content not in board["memo"]:
        board["memo"].append(content)
        domain_manager.update_quest_board(channel_id, board)
        return f"📝 **[메모 기록]** {content}"
    return None

def remove_memo(channel_id, content):
    """메모 단순 삭제"""
    board = domain_manager.get_quest_board(channel_id)
    memos = board.get("memo", [])
    target = None
    for m in memos:
        if content in m: target = m; break
    if target:
        memos.remove(target)
        board["memo"] = memos
        domain_manager.update_quest_board(channel_id, board)
        return f"🗑️ **[메모 삭제]** {target}"
    return None

def resolve_memo_auto(channel_id, content):
    """
    [신규] AI 판단에 의해 메모를 해결 처리하고 연대기에 기록
    """
    board = domain_manager.get_quest_board(channel_id)
    memos = board.get("memo", [])
    target = None
    
    # 텍스트 유사도로 대상 메모 찾기
    for m in memos:
        if content in m or m in content:
            target = m
            break
    
    if target:
        memos.remove(target)
        board["memo"] = memos
        
        # 연대기에 자동 기록
        lore_entry = {
            "title": "사건의 해결",
            "content": f"단서 해결: {target}",
            "timestamp": time.time()
        }
        if "lore" not in board: board["lore"] = []
        board["lore"].append(lore_entry)
        
        domain_manager.update_quest_board(channel_id, board)
        return f"📂 **[메모 해결]** '{target}' -> 연대기로 이동됨."
    return None

def archive_memo_with_ai(channel_id, content_or_index):
    """수동 보관용 함수 (기존 유지)"""
    board = domain_manager.get_quest_board(channel_id)
    memos = board.get("memo", [])
    target = None
    if str(content_or_index).isdigit():
        idx = int(content_or_index) - 1
        if 0 <= idx < len(memos): target = memos.pop(idx)
    else:
        for m in memos:
            if content_or_index in m: target = m; memos.remove(m); break
    if not target: return "❌ 메모 없음"

    current_genres = domain_manager.get_active_genres(channel_id)
    current_lore = domain_manager.get_lore(channel_id)
    
    system_prompt = (
        "Chronicler Task. 1.Archive(worthy=true) 2.GenreShift(Fundamentally alters genre?). JSON only."
        f"Current: {current_genres}"
    )
    user_prompt = f"Lore: {current_lore[:200]}...\nMemo: {target}"
    analysis = call_gemini_api(user_prompt, system_prompt)
    
    msg = f"📂 **보관:** {target}"
    if analysis:
        if analysis.get("genres"):
            new_g = [g for g in analysis["genres"] if g in ['noir', 'sf', 'wuxia', 'cyberpunk', 'high_fantasy', 'low_fantasy', 'cosmic_horror', 'post_apocalypse', 'urban_fantasy', 'steampunk', 'school_life']]
            if new_g and set(new_g) != set(current_genres):
                domain_manager.set_active_genres(channel_id, new_g)
                msg += f"\n🎨 **분위기 전환:** {new_g}"
        
        if analysis.get("worthy"):
            if "lore" not in board: board["lore"] = []
            board["lore"].append({"title": "기록", "content": analysis.get("summary", target), "timestamp": time.time()})
            msg += "\n✨ **연대기 등재됨**"
        else:
            if "archive" not in board: board["archive"] = []
            board["archive"].append(target)
    
    domain_manager.update_quest_board(channel_id, board)
    return msg

def get_status_message(channel_id):
    board = domain_manager.get_quest_board(channel_id)
    msg = ""
    if board.get("active"): msg += "⚔️ **퀘스트**\n" + "\n".join([f"- {q}" for q in board["active"]]) + "\n\n"
    if board.get("memo"): msg += "📝 **메모**\n" + "\n".join([f"- {m}" for m in board["memo"]])
    return msg if msg else "📭 비어있음"

def get_lore_book(channel_id):
    board = domain_manager.get_quest_board(channel_id)
    lore = board.get("lore", [])
    if not lore: return "📖 기록 없음"
    return "📖 **[연대기]**\n" + "\n".join([f"{i+1}. {l['content']}" for i, l in enumerate(lore)])

def generate_chronicle_from_history(channel_id):
    domain = domain_manager.get_domain(channel_id)
    history = domain.get('history', [])
    if not history: return "❌ 대화 기록 없음"
    history_text = "\n".join([f"{h['role']}: {h['content']}" for h in history[-20:]])
    system_prompt = "Summarize session to lore entry. JSON: {title, content}"
    res = call_gemini_api(f"History:\n{history_text}", system_prompt)
    if res:
        board = domain_manager.get_quest_board(channel_id)
        if "lore" not in board: board["lore"] = []
        board["lore"].append({"title": res.get("title"), "content": res.get("content"), "timestamp": time.time()})
        domain_manager.update_quest_board(channel_id, board)
        return f"✨ **연대기 생성:** {res.get('title')}"
    return "⚠️ 생성 실패"

def export_chronicles_incremental(channel_id, mode="new"):
    board = domain_manager.get_quest_board(channel_id)
    lore = board.get("lore", [])
    last_export = board.get("last_export_time", 0.0)
    target = lore if mode in ["all", "전체"] else [e for e in lore if e.get('timestamp', 0) > last_export]
    
    if not target: return None, "🚫 신규 기록 없음"
    txt = "[ 연대기 ]\n\n" + "\n\n".join([f"[{time.strftime('%Y-%m-%d %H:%M', time.localtime(e.get('timestamp',0)))}] {e.get('content')}" for e in target])
    
    if mode not in ["all", "전체"]:
        board["last_export_time"] = time.time()
        domain_manager.update_quest_board(channel_id, board)
    return txt, "📜 추출 완료"

def evaluate_custom_growth(lvl, xp, rule):
    return call_gemini_api(f"Lv:{lvl}, XP:{xp}\nRule:{rule}", "Judge level up. JSON: {leveled_up:bool, new_level:int, reason:str}") or {"leveled_up": False}