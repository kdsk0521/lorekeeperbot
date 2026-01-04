import domain_manager
import datetime

def add_quest(channel_id, content):
    """퀘스트 보드에 새로운 목표를 추가합니다."""
    board = domain_manager.get_quest_board(channel_id)
    board["active"].append(content)
    domain_manager.update_quest_board(channel_id, board)
    return f"📌 **퀘스트 등록:** {content}"

def complete_quest(channel_id, target):
    """
    퀘스트를 완료 처리합니다.
    target: 숫자(인덱스 문자열) 또는 퀘스트 내용(키워드)
    """
    board = domain_manager.get_quest_board(channel_id)
    active_quests = board["active"]
    
    completed_item = None
    
    # 1. 숫자로 시도 (!퀘스트 완료 1)
    if target.isdigit():
        idx = int(target) - 1
        if 0 <= idx < len(active_quests):
            completed_item = active_quests.pop(idx)
    # 2. 텍스트 매칭 시도 (AI 자동 처리)
    else:
        for i, q in enumerate(active_quests):
            if target in q: # 부분 일치 (예: "슬라임" -> "슬라임 토벌")
                completed_item = active_quests.pop(i)
                break
    
    if completed_item:
        domain_manager.update_quest_board(channel_id, board)
        # 자동 기록
        world = domain_manager.get_world_state(channel_id)
        day = world.get('day', 1) if world else 1
        log_entry = f"\n[History - Day {day}] 퀘스트 완료: {completed_item}"
        domain_manager.append_lore(channel_id, log_entry)
        return f"✅ **퀘스트 완료!** 역사에 기록되었습니다.\n(내용: {completed_item})"
    else:
        return "❌ 해당 퀘스트를 찾을 수 없습니다."

def add_memo(channel_id, content):
    """단기 메모장에 내용을 적습니다."""
    board = domain_manager.get_quest_board(channel_id)
    board["memo"].append(content)
    domain_manager.update_quest_board(channel_id, board)
    return f"📝 **메모 추가:** {content}"

def archive_memo(channel_id, target):
    """
    메모를 보관/삭제합니다.
    target: 숫자 또는 내용 키워드
    """
    board = domain_manager.get_quest_board(channel_id)
    memos = board["memo"]
    
    archived_item = None
    
    if target.isdigit():
        idx = int(target) - 1
        if 0 <= idx < len(memos):
            archived_item = memos.pop(idx)
    else:
        for i, m in enumerate(memos):
            if target in m:
                archived_item = memos.pop(i)
                break
                
    if archived_item:
        domain_manager.update_quest_board(channel_id, board)
        log_entry = f"\n[Memo Archived] {archived_item}"
        domain_manager.append_lore(channel_id, log_entry)
        return f"🗄️ **메모 보관 완료.** (목록에서 제거됨)"
    else:
        return "❌ 해당 메모를 찾을 수 없습니다."

def get_objective_context(channel_id):
    """AI 프롬프트용 문자열 생성"""
    board = domain_manager.get_quest_board(channel_id)
    if not board: return ""
    
    quests = board.get("active", [])
    memos = board.get("memo", [])
    
    q_str = "\n".join([f"- {q}" for q in quests]) if quests else "None"
    m_str = "\n".join([f"- {m}" for m in memos]) if memos else "None"
    
    return (
        f"[Current Objectives & Notes]\n"
        f"**Active Quests** (PRIORITY):\n{q_str}\n\n"
        f"**Memo Pad** (Context):\n{m_str}\n"
        f"*Instruction: Keep these objectives in mind. Use memos as hints.*"
    )

def get_status_message(channel_id):
    """상태 메시지 생성"""
    board = domain_manager.get_quest_board(channel_id)
    quests = board.get("active", [])
    memos = board.get("memo", [])
    
    msg = "**📋 [퀘스트 보드]**\n"
    if not quests: msg += "(진행 중인 퀘스트 없음)\n"
    else:
        for i, q in enumerate(quests):
            msg += f"{i+1}. {q}\n"
            
    msg += "\n**📝 [메모장]**\n"
    if not memos: msg += "(메모 없음)\n"
    else:
        for i, m in enumerate(memos):
            msg += f"{i+1}. {m}\n"
            
    return msg