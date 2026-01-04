import domain_manager
import json
import requests
import time
import os

# 메인에서 .env를 로드했다면 os.getenv로 가져올 수 있습니다.
API_KEY = os.getenv("GEMINI_API_KEY", "")

def call_gemini_api(prompt, system_instruction=""):
    """Gemini API를 호출하여 구조화된 JSON 응답을 받습니다."""
    if not API_KEY:
        print("⚠️ API_KEY가 설정되지 않았습니다. .env 파일이나 환경 변수를 확인하세요.")
        return None

    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash-preview-09-2025:generateContent?key={API_KEY}"
    
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "systemInstruction": {"parts": [{"text": system_instruction}]},
        "generationConfig": {
            "responseMimeType": "application/json"
        }
    }
    
    # 지수 백오프 적용된 리트라이 로직 (최대 5회)
    for i in range(5):
        try:
            response = requests.post(url, json=payload, timeout=10)
            if response.status_code == 200:
                result = response.json()
                return json.loads(result['candidates'][0]['content']['parts'][0]['text'])
            elif response.status_code == 403:
                print("❌ API Key 권한 오류. 키가 유효한지 확인하세요.")
                break
            time.sleep(2 ** i)
        except Exception as e:
            time.sleep(2 ** i)
    return None

def get_objective_context(channel_id):
    """
    현재 세계관의 기록(Lore)과 진행 중인 퀘스트/메모 정보를 취합하여 
    AI가 참조할 수 있는 '기억(Memory)' 컨텍스트를 생성합니다.
    """
    board = domain_manager.get_quest_board(channel_id)
    active_quests = board.get("active", [])
    memos = board.get("memo", [])
    lore = board.get("lore", []) # 영구 박제된 기록

    context = "### CURRENT WORLD MEMORY & LORE\n"
    
    # 1. 고정된 역사 기록 (가장 중요)
    if lore:
        context += "[The Great Lore (Established History)]\n"
        for entry in lore:
            context += f"- {entry.get('title', 'Unknown')}: {entry.get('content', '')}\n"

    # 2. 현재 진행 중인 사건들
    if active_quests:
        context += "\n[Ongoing Quests (Current Conflict)]\n"
        for q in active_quests:
            context += f"- {q}\n"
    
    # 3. 최근의 사념들
    if memos:
        context += "\n[Active Memos (Current Thoughts)]\n"
        for m in memos:
            context += f"- {m}\n"

    return context

def add_memo(channel_id, content):
    """새로운 메모를 추가합니다."""
    if not content: return "❌ 메모 내용을 입력하세요."
    board = domain_manager.get_quest_board(channel_id)
    if "memo" not in board: board["memo"] = []
    
    if content in board["memo"]:
        return "⚠️ 이미 기록된 내용입니다."
        
    board["memo"].append(content)
    domain_manager.update_quest_board(channel_id, board)
    return f"📌 **메모 추가됨:** {content}"

def archive_memo_with_ai(channel_id, content_or_index):
    """
    메모를 보관 처리하며, AI가 영어 프롬프트를 통해 세계관 컨텍스트를 참조하여 
    역사적 가치를 평가한 뒤 '로어'에 박제합니다.
    """
    board = domain_manager.get_quest_board(channel_id)
    memos = board.get("memo", [])
    
    target = None
    if str(content_or_index).isdigit():
        idx = int(content_or_index) - 1
        if 0 <= idx < len(memos):
            target = memos.pop(idx)
    elif content_or_index in memos:
        memos.remove(content_or_index)
        target = content_or_index
        
    if not target:
        return "❌ 해당 메모를 찾을 수 없습니다."

    # 메모리(컨텍스트) 정보 가져오기
    world_context = get_objective_context(channel_id)

    # 토큰 절약을 위해 프롬프트를 영어로 구성
    system_prompt = (
        "You are the Chronicler of the Eternal Archives. "
        "Evaluate the significance of new information based on the [World Memory] provided below.\n\n"
        f"{world_context}"
    )
    
    user_prompt = (
        f"Analyze this new memo: '{target}'\n\n"
        "Determine if this content is worth being permanently archived as 'Lore'. "
        "Look for important conclusions, world-building lore, or profound insights that connect to existing history or quests.\n"
        "If it is worthy, set 'worthy': true and write a 'summary' in an archaic, grand, and formal style (in Korean). "
        "If it is just a mundane record, set 'worthy': false.\n"
        "Respond ONLY in JSON format: {'worthy': bool, 'summary': str}"
    )
    
    analysis = call_gemini_api(user_prompt, system_prompt)
    
    if "lore" not in board: board["lore"] = []
    if "archive" not in board: board["archive"] = []

    msg = f"📂 **보관 처리 완료:** {target}"
    
    if analysis and analysis.get("worthy"):
        lore_entry = {
            "title": "기록된 세계의 파편",
            "content": analysis["summary"],
            "original_memo": target,
            "timestamp": time.time()
        }
        board["lore"].append(lore_entry)
        msg += f"\n✨ **기록관의 선택:** 이 사념은 역사의 한 페이지가 될 자격이 충분합니다. 로어 북에 박제되었습니다."
    else:
        board["archive"].append(target)
        if len(board["archive"]) > 20: board["archive"].pop(0)

    domain_manager.update_quest_board(channel_id, board)
    return msg

def resolve_quest_to_lore(channel_id, quest_index_or_name):
    """
    퀘스트를 완료 처리하고 자동으로 로어(연대기)에 기록합니다.
    """
    board = domain_manager.get_quest_board(channel_id)
    active = board.get("active", [])
    
    target = None
    if str(quest_index_or_name).isdigit():
        idx = int(quest_index_or_name) - 1
        if 0 <= idx < len(active):
            target = active.pop(idx)
    elif quest_index_or_name in active:
        active.remove(quest_index_or_name)
        target = quest_index_or_name

    if not target:
        return "❌ 완료할 퀘스트를 찾을 수 없습니다."

    if "lore" not in board: board["lore"] = []
    
    # 퀘스트 완료는 역사적 사실이므로 즉시 박제
    lore_entry = {
        "title": f"과업의 완수: {target}",
        "content": f"기나긴 여정 끝에 '{target}'의 과업이 마침내 종지부를 찍었노라. 이는 영원히 기억될 승리로 기록될 것이다.",
        "timestamp": time.time()
    }
    board["lore"].append(lore_entry)
    domain_manager.update_quest_board(channel_id, board)
    
    return f"🏆 **퀘스트 달성:** '{target}' (연대기에 공식 기록되었습니다.)"

def get_lore_book(channel_id):
    """박제된 로어 기록들을 가져옵니다."""
    board = domain_manager.get_quest_board(channel_id)
    lore = board.get("lore", [])
    if not lore:
        return "📖 **로어 북:** 아직 기록된 역사가 없습니다."
    
    msg = "📖 **영겁의 연대기 (Lore Book)**\n"
    for i, entry in enumerate(lore, 1):
        msg += f"{i}. **{entry.get('title', '사건')}**\n   - {entry.get('content', '')}\n"
    return msg

def get_status_message(channel_id):
    """현재 상태 요약"""
    board = domain_manager.get_quest_board(channel_id)
    active = board.get("active", [])
    memos = board.get("memo", [])
    
    msg = ""
    if active:
        msg += "⚔️ **진행 중인 퀘스트**\n" + "\n".join([f"- {q}" for q in active]) + "\n\n"
    if memos:
        msg += "📝 **현재 메모**\n" + "\n".join([f"{i+1}. {m}" for i, m in enumerate(memos)])
    else:
        msg += "📝 **메모장:** 비어있음"
        
    return msg