"""
Lorekeeper TRPG Bot - System Handler Module
AI가 제안한 시스템 액션(퀘스트, 메모, NPC 자동 등록 등)을 처리합니다.
"""

import logging
from typing import Optional

# 모듈 임포트
import quest_manager
import character_sheet

async def process_ai_system_action(channel_id: str, sys_action: dict) -> Optional[str]:
    """
    AI가 제안한 시스템 액션을 처리합니다.
    
    Args:
        channel_id: 디스코드 채널 ID
        sys_action: AI가 생성한 시스템 액션 딕셔너리
            { "tool": "Quest", "type": "Add", "content": "..." }
            
    Returns:
        처리 결과 메시지 (사용자에게 표시할 내용) 또는 None
    """
    if not sys_action or not isinstance(sys_action, dict):
        return None
    
    tool = sys_action.get("tool")
    atype = sys_action.get("type")
    content = sys_action.get("content")
    
    if not all([tool, atype, content]):
        return None
    
    auto_msg = None
    
    if tool == "Memo":
        if atype == "Add":
            auto_msg = quest_manager.add_memo(channel_id, content)
        elif atype == "Remove":
            auto_msg = quest_manager.remove_memo(channel_id, content)
        elif atype == "Archive":
            auto_msg = quest_manager.resolve_memo_auto(channel_id, content)
    
    elif tool == "Quest":
        if atype == "Add":
            auto_msg = quest_manager.add_quest(channel_id, content)
        elif atype == "Complete":
            auto_msg = quest_manager.complete_quest(channel_id, content)
    
    elif tool == "NPC" and atype == "Add":
        if ":" in content:
            name, desc = content.split(":", 1)
            character_sheet.npc_memory.add_npc(channel_id, name.strip(), desc.strip(), source="session")
            auto_msg = f"🎭 NPC: {name.strip()}"
        else:
            character_sheet.npc_memory.add_npc(channel_id, content, "Auto", source="session")
            auto_msg = f"🎭 NPC: {content}"
    
    # XP Award 제거됨 - 성과는 패시브/칭호로 표현
    elif tool == "XP" and atype == "Award":
        logging.info(f"[Achievement] {content}")
    
    return auto_msg
