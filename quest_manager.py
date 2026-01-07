"""
Lorekeeper TRPG Bot - Quest Manager Module
퀘스트, 메모, 연대기 관리를 담당합니다.
"""

import json
import time
import asyncio
import logging
import re
from typing import Optional, Dict, Any, List, Tuple

import domain_manager
from google.genai import types

# =========================================================
# 상수 정의
# =========================================================
MAX_RETRY_COUNT = 3
RETRY_DELAY_SECONDS = 1
MAX_ARCHIVE_DISPLAY = 3  # 보관함에서 표시할 최대 항목 수
MAX_HISTORY_FOR_CHRONICLE = 50  # 연대기 생성 시 사용할 최대 히스토리


# =========================================================
# AI 유틸리티
# =========================================================
async def call_gemini_api(
    client,
    model_id: str,
    prompt: str,
    system_instruction: str = ""
) -> Optional[Dict[str, Any]]:
    """
    Gemini API를 호출하여 JSON 응답을 받습니다.
    
    Args:
        client: Gemini 클라이언트
        model_id: 모델 ID
        prompt: 사용자 프롬프트
        system_instruction: 시스템 지시문
    
    Returns:
        파싱된 JSON 딕셔너리 또는 None
    """
    if not client:
        return None
    
    # system_instruction을 프롬프트에 포함시킴
    full_prompt = f"{system_instruction}\n\n{prompt}" if system_instruction else prompt
    
    config = types.GenerateContentConfig(
        response_mime_type="application/json",
        temperature=0.1
    )
    
    for attempt in range(MAX_RETRY_COUNT):
        try:
            response = await client.aio.models.generate_content(
                model=model_id,
                contents=[types.Content(role="user", parts=[types.Part(text=full_prompt)])],
                config=config
            )
            
            if response and response.text:
                # JSON 파싱
                clean_text = re.sub(r"```(json)?", "", response.text).strip()
                clean_text = clean_text.strip("`")
                return json.loads(clean_text)
                
        except json.JSONDecodeError as e:
            logging.warning(f"[Quest API] JSON 파싱 실패 (시도 {attempt + 1}/{MAX_RETRY_COUNT}): {e}")
        except Exception as e:
            logging.warning(f"[Quest API] API 호출 실패 (시도 {attempt + 1}/{MAX_RETRY_COUNT}): {e}")
        
        if attempt < MAX_RETRY_COUNT - 1:
            await asyncio.sleep(RETRY_DELAY_SECONDS)
    
    return None


# =========================================================
# 컨텍스트 생성 (Context Generation)
# =========================================================
def get_objective_context(channel_id: str) -> str:
    """
    현재 퀘스트와 메모 상태를 AI가 읽기 좋은 텍스트로 변환합니다.
    """
    board = domain_manager.get_quest_board(channel_id)
    if not board:
        return "No active quests or memos."
    
    active = board.get("active", [])
    memos = board.get("memos", [])
    archives = board.get("archive", [])
    
    txt = "### [QUESTS & MEMOS]\n"
    
    # 활성 퀘스트
    if active:
        txt += "**Active Objectives:**\n"
        txt += "\n".join([f"- {q}" for q in active])
        txt += "\n"
    else:
        txt += "- No active quests.\n"
    
    # 활성 메모
    if memos:
        txt += "**Active Memos:**\n"
        txt += "\n".join([f"- {m}" for m in memos])
        txt += "\n"
    else:
        txt += "- No active memos.\n"
    
    # 보관된 메모 (최근 항목만)
    if archives:
        txt += "**Archived Info (Reference):**\n"
        txt += "\n".join([f"- {m}" for m in archives[-MAX_ARCHIVE_DISPLAY:]])
    
    return txt


def get_active_quests_text(channel_id: str) -> str:
    """활성 퀘스트 목록을 텍스트로 반환합니다."""
    board = domain_manager.get_quest_board(channel_id) or {}
    active = board.get("active", [])
    
    if not active:
        return "📭 현재 진행 중인 퀘스트가 없습니다."
    
    lines = [f"{i + 1}. {q}" for i, q in enumerate(active)]
    return "🔥 **진행 중인 퀘스트:**\n" + "\n".join(lines)


def get_memos_text(channel_id: str) -> str:
    """메모 목록을 텍스트로 반환합니다."""
    board = domain_manager.get_quest_board(channel_id) or {}
    memos = board.get("memos", [])
    
    if not memos:
        return "📭 저장된 메모가 없습니다."
    
    lines = [f"- {m}" for m in memos]
    return "📝 **메모 목록:**\n" + "\n".join(lines)


def get_status_message(channel_id: str) -> str:
    """퀘스트와 메모 상태를 한 번에 보여줍니다."""
    q_text = get_active_quests_text(channel_id)
    m_text = get_memos_text(channel_id)
    return f"{q_text}\n\n{m_text}"


# =========================================================
# 퀘스트 보드 헬퍼 함수
# =========================================================
def _get_board(channel_id: str) -> Dict[str, Any]:
    """퀘스트 보드 데이터를 가져오고 필수 키를 보장합니다."""
    d = domain_manager.get_domain(channel_id)
    
    if "quest_board" not in d or not isinstance(d["quest_board"], dict):
        d["quest_board"] = {
            "active": [],
            "completed": [],
            "memos": [],
            "archive": [],
            "lore": []
        }
    
    # 키가 없을 경우 보정
    required_keys = ["active", "completed", "memos", "archive", "lore"]
    for key in required_keys:
        if key not in d["quest_board"]:
            d["quest_board"][key] = []
    
    return d["quest_board"]


def _save_board(channel_id: str, board: Dict[str, Any]) -> None:
    """퀘스트 보드를 저장합니다."""
    domain_manager.update_quest_board(channel_id, board)


def _add_item_to_list(
    channel_id: str,
    list_key: str,
    content: str,
    emoji: str = "📌",
    display_name: str = "항목"
) -> Optional[str]:
    """
    리스트에 항목을 추가하는 범용 함수입니다.
    
    Args:
        channel_id: 채널 ID
        list_key: 보드 내 키 이름 ("active", "memos" 등)
        content: 추가할 내용
        emoji: 성공 메시지에 표시할 이모지
        display_name: 사용자에게 보여줄 항목명
    
    Returns:
        성공/실패 메시지 문자열
    """
    if not content:
        return None
    
    board = _get_board(channel_id)
    target_list = board.get(list_key, [])
    
    if content not in target_list:
        target_list.append(content)
        board[list_key] = target_list
        _save_board(channel_id, board)
        return f"{emoji} **{display_name} 등록:** {content}"
    
    return f"⚠️ 이미 등록된 {display_name}입니다."


def _remove_item_from_list(
    channel_id: str,
    list_key: str,
    search_content: str,
    emoji: str = "🗑️",
    display_name: str = "항목"
) -> Optional[str]:
    """
    리스트에서 항목을 제거하는 범용 함수입니다 (부분 일치 지원).
    
    Args:
        channel_id: 채널 ID
        list_key: 보드 내 키 이름
        search_content: 검색할 내용 (부분 일치 허용)
        emoji: 성공 메시지에 표시할 이모지
        display_name: 사용자에게 보여줄 항목명
    
    Returns:
        성공/실패 메시지 문자열
    """
    if not search_content:
        return None
    
    board = _get_board(channel_id)
    target_list = board.get(list_key, [])
    
    # 부분 일치 검색
    found_item = None
    for item in target_list:
        if search_content in item:
            found_item = item
            break
    
    if found_item:
        target_list.remove(found_item)
        board[list_key] = target_list
        _save_board(channel_id, board)
        return f"{emoji} **{display_name} 제거:** {found_item}"
    
    return f"⚠️ 해당 {display_name}를 찾을 수 없습니다."


def _move_item_between_lists(
    channel_id: str,
    from_key: str,
    to_key: str,
    search_content: str,
    emoji: str = "📦",
    display_name: str = "항목",
    action_name: str = "이동"
) -> Optional[str]:
    """
    한 리스트에서 다른 리스트로 항목을 이동하는 범용 함수입니다.
    
    Args:
        channel_id: 채널 ID
        from_key: 원본 리스트 키
        to_key: 대상 리스트 키
        search_content: 검색할 내용 (부분 일치 허용)
        emoji: 성공 메시지에 표시할 이모지
        display_name: 사용자에게 보여줄 항목명
        action_name: 동작 설명 ("이동", "완료", "보관" 등)
    
    Returns:
        성공/실패 메시지 문자열
    """
    if not search_content:
        return None
    
    board = _get_board(channel_id)
    from_list = board.get(from_key, [])
    to_list = board.get(to_key, [])
    
    # 부분 일치 검색
    found_item = None
    for item in from_list:
        if search_content in item:
            found_item = item
            break
    
    if found_item:
        from_list.remove(found_item)
        to_list.append(found_item)
        board[from_key] = from_list
        board[to_key] = to_list
        _save_board(channel_id, board)
        return f"{emoji} **{display_name} {action_name}:** {found_item}"
    
    return f"⚠️ 해당 {display_name}를 찾을 수 없습니다."


# =========================================================
# 퀘스트 관리 함수
# =========================================================
def add_quest(channel_id: str, content: str) -> Optional[str]:
    """퀘스트를 추가합니다."""
    return _add_item_to_list(channel_id, "active", content, "🔥", "퀘스트")


def complete_quest(channel_id: str, content: str) -> Optional[str]:
    """퀘스트를 완료 처리합니다 (active → completed)."""
    return _move_item_between_lists(
        channel_id, "active", "completed", content,
        "✅", "퀘스트", "완료"
    )


# =========================================================
# 메모 관리 함수
# =========================================================
def add_memo(channel_id: str, content: str) -> Optional[str]:
    """메모를 추가합니다."""
    return _add_item_to_list(channel_id, "memos", content, "📝", "메모")


def remove_memo(channel_id: str, content: str) -> Optional[str]:
    """메모를 삭제합니다 (수동 삭제용)."""
    return _remove_item_from_list(channel_id, "memos", content, "🗑️", "메모")


def resolve_memo_auto(channel_id: str, content: str) -> Optional[str]:
    """
    AI(좌뇌)가 'Memo Remove' 명령을 내렸을 때 호출됩니다.
    안전을 위해 바로 삭제하지 않고 '보관함'으로 보냅니다.
    """
    return _move_item_between_lists(
        channel_id, "memos", "archive", content,
        "🗄️", "메모", "해결 (보관함 이동)"
    )


# =========================================================
# AI 연동 기능
# =========================================================
async def archive_memo_with_ai(
    client,
    model_id: str,
    channel_id: str,
    content_or_index: str
) -> str:
    """
    [AI] 메모의 내용을 분석하여 '영구 보관(장비/관계)'할지 '완전 삭제(소모품)'할지 결정합니다.
    """
    board = _get_board(channel_id)
    memos = board.get("memos", [])
    
    # 인덱스 또는 내용으로 검색
    target = None
    if str(content_or_index).isdigit():
        idx = int(content_or_index) - 1
        if 0 <= idx < len(memos):
            target = memos[idx]
    else:
        for m in memos:
            if content_or_index in m:
                target = m
                break
    
    if not target:
        return "❌ 해당 메모를 찾을 수 없습니다."
    
    system_prompt = (
        "You are a Data Librarian. Analyze the memo content and categorize it.\n"
        "**Rules:**\n"
        "1. **DELETE:** Consumables, temporary status, trivial noise.\n"
        "2. **ARCHIVE:** Equipment, Appearance changes, Relationships, Story Clues.\n\n"
        'Output JSON: {"action": "DELETE" or "ARCHIVE", "reason": "Short explanation in Korean"}'
    )
    user_prompt = f"Memo Content: {target}"
    
    decision = await call_gemini_api(client, model_id, user_prompt, system_prompt)
    
    # 메모 제거
    memos.remove(target)
    board["memos"] = memos
    
    msg = ""
    if decision and decision.get("action") == "ARCHIVE":
        if "archive" not in board:
            board["archive"] = []
        board["archive"].append(target)
        msg = f"🗄️ **[보관됨]** {target}\n(사유: {decision.get('reason', '중요 정보')})"
    else:
        reason = decision.get("reason") if decision else "소모성/임시 데이터"
        msg = f"🗑️ **[삭제됨]** {target}\n(사유: {reason})"
    
    _save_board(channel_id, board)
    return msg


async def generate_character_info_view(
    client,
    model_id: str,
    channel_id: str,
    user_id: str,
    current_desc: str,
    inventory_dict: Dict[str, int]
) -> Optional[Dict[str, Any]]:
    """[AI] 캐릭터 요약 정보를 생성합니다."""
    # 인벤토리 텍스트
    if inventory_dict:
        inv_text = ", ".join([f"{k} x{v}" for k, v in inventory_dict.items()])
    else:
        inv_text = "(빈 인벤토리)"
    
    # 히스토리 텍스트
    history_logs = domain_manager.get_domain(channel_id).get('history', [])[-20:]
    history_text = "\n".join([f"{h['role']}: {h['content']}" for h in history_logs])
    
    system_prompt = (
        "You are a UI Generator for a TRPG status window.\n"
        "Analyze the character's description, inventory, and recent history.\n"
        'Output JSON: {'
        '  "appearance_summary": "Concise 1-sentence visual summary.",'
        '  "assets_summary": "Summarize wealth/power based on inventory.",'
        '  "relationships": ["NPC_Name: Relationship_Keyword (max 3 words)"]'
        '}'
    )
    user_prompt = f"Desc:\n{current_desc}\n\nInv:\n{inv_text}\n\nHistory:\n{history_text}"
    
    return await call_gemini_api(client, model_id, user_prompt, system_prompt)


async def generate_chronicle_from_history(
    client,
    model_id: str,
    channel_id: str
) -> str:
    """[AI] 연대기(요약본)를 생성합니다."""
    domain = domain_manager.get_domain(channel_id)
    board = _get_board(channel_id)
    history = domain.get('history', [])
    
    if not history:
        return "기록된 역사가 없습니다."
    
    # 최근 히스토리만 사용
    full_text = "\n".join([
        f"{h['role']}: {h['content']}" 
        for h in history[-MAX_HISTORY_FOR_CHRONICLE:]
    ])
    
    system_prompt = (
        "You are the Chronicler. Summarize the provided RPG session log into a compelling narrative summary.\n"
        "Focus on key events, decisions, and outcomes. Write in Korean.\n"
        'Output JSON: {"title": "Title", "summary": "Content..."}'
    )
    user_prompt = f"Log:\n{full_text}"
    
    res = await call_gemini_api(client, model_id, user_prompt, system_prompt)
    
    if res and "summary" in res:
        entry = {
            "title": res.get("title", "기록"),
            "content": res.get("summary"),
            "timestamp": time.time()
        }
        board["lore"].append(entry)
        _save_board(channel_id, board)
        
        preview = entry['content'][:100] + "..." if len(entry['content']) > 100 else entry['content']
        return f"📜 **[연대기 기록됨]** {entry['title']}\n{preview}"
    
    return "연대기 생성 실패"


def get_lore_book(channel_id: str) -> str:
    """채팅창에 연대기 목록을 간략히 표시합니다."""
    board = _get_board(channel_id)
    lore = board.get("lore", [])
    
    if not lore:
        return "📖 기록된 연대기가 없습니다."
    
    msg = "📖 **[연대기 목록]**\n"
    
    for i, entry in enumerate(lore):
        timestamp = entry.get('timestamp', 0)
        date_str = time.strftime('%Y-%m-%d', time.localtime(timestamp))
        title = entry.get('title', 'Untitled')
        msg += f"{i + 1}. [{date_str}] {title}\n"
    
    msg += "\n💡 `!추출`은 대화 로그를, `!연대기 추출`은 이 요약본을 파일로 저장합니다."
    return msg


async def evaluate_custom_growth(
    client,
    model_id: str,
    current_level: int,
    current_xp: int,
    rules_text: str
) -> Optional[Dict[str, Any]]:
    """[AI] 레벨업 판정을 수행합니다."""
    system_prompt = (
        "Evaluate level up based on custom rules.\n"
        'Output JSON: {"leveled_up": bool, "new_level": int, "reason": "str"}'
    )
    user_prompt = f"Rules:\n{rules_text}\n\nCurrent Level: {current_level}, XP: {current_xp}"
    
    return await call_gemini_api(client, model_id, user_prompt, system_prompt)


# =========================================================
# 추출 시스템 (로그 vs 연대기)
# =========================================================
def export_chronicles_incremental(
    channel_id: str,
    mode: str = ""
) -> Tuple[Optional[str], str]:
    """
    [로그 추출] 대화 내역(History)을 텍스트 파일로 추출합니다.
    
    Args:
        channel_id: 채널 ID
        mode: "전체"/"full" - 처음부터 끝까지, "" - 마지막 추출 이후만 (증분)
    
    Returns:
        (추출 텍스트, 상태 메시지) 튜플
    """
    domain = domain_manager.get_domain(channel_id)
    history = domain.get('history', [])
    
    if not history:
        return None, "❌ 기록된 대화가 없습니다."
    
    last_idx = domain.get('last_export_idx', 0)
    current_len = len(history)
    
    # 모드 결정
    if mode.lower() in ["전체", "full", "all"]:
        start_idx = 0
        export_type = "전체(Full)"
    else:
        start_idx = last_idx
        export_type = "증분(New Only)"
    
    # 새 내용 없음 체크
    if start_idx >= current_len and export_type != "전체(Full)":
        return None, (
            "✅ 새로운 대화 내용이 없습니다. (이미 최신 상태입니다)\n"
            "처음부터 다시 뽑으려면 `!추출 전체`를 입력하세요."
        )
    
    # 헤더 생성
    export_lines = [
        f"=== Lorekeeper Session Log [{export_type}] ===",
        f"Export Time: {time.strftime('%Y-%m-%d %H:%M:%S')}",
        f"Range: Msg {start_idx + 1} ~ {current_len}",
        "",
        "-" * 40
    ]
    
    # 내용 추가
    target_history = history[start_idx:]
    for entry in target_history:
        role = entry.get('role', 'Unknown')
        content = entry.get('content', '')
        
        if role == 'User':
            export_lines.append(f"[Player]: {content}")
        elif role == 'Char':
            export_lines.append(f"[Story]: {content}")
        elif role == 'System':
            export_lines.append(f"[System]: {content}")
        else:
            export_lines.append(f"[{role}]: {content}")
        
        export_lines.append("")
    
    # 마지막 추출 인덱스 업데이트
    domain['last_export_idx'] = current_len
    domain_manager.save_domain(channel_id, domain)
    
    result_text = "\n".join(export_lines)
    msg = f"📜 **대화 로그 추출 완료 ({export_type})**\n(총 {len(target_history)}개의 메시지를 저장했습니다.)"
    
    return result_text, msg


def export_lore_book_file(channel_id: str) -> Tuple[Optional[str], str]:
    """
    [연대기 추출] 요약된 연대기(Lore) 목록을 텍스트 파일로 추출합니다.
    
    Returns:
        (추출 텍스트, 상태 메시지) 튜플
    """
    board = _get_board(channel_id)
    lore = board.get("lore", [])
    
    if not lore:
        return None, "❌ 기록된 연대기가 없습니다. `!연대기 생성`을 먼저 진행해주세요."
    
    # 헤더 생성
    export_lines = [
        "=== Lorekeeper Chronicles (Summary) ===",
        f"Export Time: {time.strftime('%Y-%m-%d %H:%M:%S')}",
        f"Total Entries: {len(lore)}",
        "",
        "-" * 40
    ]
    
    # 내용 추가
    for i, entry in enumerate(lore):
        title = entry.get("title", "Untitled")
        content = entry.get("content", "")
        timestamp = entry.get("timestamp", 0)
        date_str = time.strftime('%Y-%m-%d %H:%M', time.localtime(timestamp))
        
        export_lines.append(f"#{i + 1}. {title} [{date_str}]")
        export_lines.append(content)
        export_lines.append("-" * 20)
        export_lines.append("")
    
    result_text = "\n".join(export_lines)
    msg = f"📖 **연대기 추출 완료** (총 {len(lore)}개의 기록)"
    
    return result_text, msg
