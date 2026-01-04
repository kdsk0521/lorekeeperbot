import domain_manager
import json
import requests
import time
import os

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
                    return json.loads(result['candidates'][0]['content']['parts'][0]['text'])
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
        for entry in lore[-5:]:
            context += f"- {entry.get('title')}: {entry.get('content')}\n"
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
    board = domain_manager.get_quest_board(channel_id)
    target = None
    for q in board["active"]:
        if content in q or q in content: target = q; break
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
    board = domain_manager.get_quest_board(channel_id)
    memos = board.get("memo", [])
    target = None
    for m in memos:
        if content in m or m in content: target = m; break
    if target:
        memos.remove(target)
        board["memo"] = memos
        domain_manager.update_quest_board(channel_id, board)
        return f"🗑️ **[메모 삭제]** {target}"
    return None

def archive_memo_with_ai(channel_id, content_or_index):
    board = domain_manager.get_quest_board(channel_id)
    memos = board.get("memo", [])
    target = None
    if str(content_or_index).isdigit():
        idx = int(content_or_index) - 1
        if 0 <= idx < len(memos): target = memos.pop(idx)
    elif content_or_index in memos:
        memos.remove(content_or_index)
        target = content_or_index
        
    if not target: return "❌ 메모를 찾을 수 없습니다."

    system_prompt = "You are the Chronicler. Summarize this event for the history books if it is significant. JSON: {worthy: bool, summary: str}"
    user_prompt = f"Memo: {target}"
    analysis = call_gemini_api(user_prompt, system_prompt)
    
    msg = f"📂 **보관 처리:** {target}"
    if analysis and analysis.get("worthy"):
        if "lore" not in board: board["lore"] = []
        board["lore"].append({
            "title": "기록된 단편",
            "content": analysis.get("summary", target),
            "timestamp": time.time()
        })
        msg += "\n✨ **[연대기 등재]** 역사의 한 페이지로 기록되었습니다."
    else:
        if "archive" not in board: board["archive"] = []
        board["archive"].append(target)
        msg += " (일반 보관소로 이동됨)"

    domain_manager.update_quest_board(channel_id, board)
    return msg

def get_status_message(channel_id):
    board = domain_manager.get_quest_board(channel_id)
    msg = ""
    if board.get("active"): msg += "⚔️ **퀘스트**\n" + "\n".join([f"- {q}" for q in board["active"]]) + "\n\n"
    if board.get("memo"): msg += "📝 **메모**\n" + "\n".join([f"- {m}" for m in board["memo"]])
    return msg if msg else "📭 퀘스트 보드가 비어있습니다."

def get_lore_book(channel_id):
    board = domain_manager.get_quest_board(channel_id)
    lore = board.get("lore", [])
    if not lore: return "📖 기록된 역사가 없습니다."
    return "📖 **[연대기]**\n" + "\n".join([f"{i+1}. {l['content']}" for i, l in enumerate(lore)])

def export_chronicles_incremental(channel_id, mode="new"):
    """
    [기능] 연대기 파일 추출 (증분 백업 지원)
    mode="new": 마지막 추출 이후만, mode="all": 전체
    """
    board = domain_manager.get_quest_board(channel_id)
    lore = board.get("lore", [])
    last_export = board.get("last_export_time", 0.0)
    
    target_entries = []
    
    if mode == "all" or mode == "전체":
        target_entries = lore
        title_prefix = "[ 전체 연대기 (All Chronicles) ]"
        status_msg = "📜 **전체 기록 추출 완료**"
    else:
        target_entries = [entry for entry in lore if entry.get('timestamp', 0) > last_export]
        title_prefix = f"[ 신규 연대기 (Since {time.strftime('%Y-%m-%d %H:%M', time.localtime(last_export))}) ]"
        status_msg = "📜 **신규 기록 추출 완료**"

    if not target_entries:
        return None, "🚫 **새로 기록된 연대기가 없습니다.** (변동 사항 없음)\n전체를 받고 싶다면 `!추출 전체`를 입력하세요."

    txt = f"{title_prefix}\n\n"
    for i, entry in enumerate(target_entries, 1):
        title = entry.get('title', '무제')
        content = entry.get('content', '')
        date = time.strftime('%Y-%m-%d %H:%M', time.localtime(entry.get('timestamp', time.time())))
        txt += f"[{date}] {title}\n   {content}\n\n"
        
    if mode != "all" and mode != "전체":
        board["last_export_time"] = time.time()
        domain_manager.update_quest_board(channel_id, board)
        
    return txt, status_msg