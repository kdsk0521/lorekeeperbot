"""
Lorekeeper TRPG Bot - Domain Lore Module
Handles lore and NPC data management.
"""

import os
import logging
import config
from typing import Dict, Any, Optional
from domain_io import (
    get_lore_file_path, get_lore_original_file_path,
    load_text, save_text, _lore_cache, _lore_original_cache,
    get_domain, save_domain
)

# =========================================================
# 로어 관리
# =========================================================
def get_lore(channel_id: str) -> str:
    if channel_id in _lore_cache:
        return _lore_cache[channel_id]
    text = load_text(get_lore_file_path(channel_id), config.DEFAULT_LORE)
    _lore_cache[channel_id] = text
    return text

def append_lore(channel_id: str, text: str) -> None:
    current = get_lore(channel_id)
    new_text = text if current.strip() == config.DEFAULT_LORE.strip() else f"{current}\n\n{text}"
    _lore_cache[channel_id] = new_text
    save_text(get_lore_file_path(channel_id), new_text)

def reset_lore(channel_id: str) -> None:
    lore_path = get_lore_file_path(channel_id)
    original_path = get_lore_original_file_path(channel_id)

    if channel_id in _lore_cache:
        del _lore_cache[channel_id]
    if channel_id in _lore_original_cache:
        del _lore_original_cache[channel_id]

    if os.path.exists(lore_path):
        try:
            os.remove(lore_path)
        except Exception as e:
            logging.error(f"파일 삭제 실패 {lore_path}: {e}")
    if os.path.exists(original_path):
        try:
            os.remove(original_path)
        except Exception as e:
            logging.error(f"파일 삭제 실패 {original_path}: {e}")

def save_lore_original(channel_id: str, original_text: str) -> None:
    _lore_original_cache[channel_id] = original_text
    save_text(get_lore_original_file_path(channel_id), original_text)

def get_lore_original(channel_id: str) -> Optional[str]:
    if channel_id in _lore_original_cache:
        return _lore_original_cache[channel_id]
    path = get_lore_original_file_path(channel_id)
    if os.path.exists(path):
        text = load_text(path, "")
        _lore_original_cache[channel_id] = text
        return text
    return None

def get_lore_with_npcs(channel_id: str) -> str:
    lore = get_lore(channel_id)
    d = get_domain(channel_id)
    npcs = d.get("npcs", {})
    
    if not npcs:
        return lore
    
    npc_section = "\n\n### 📋 NPC 정보 (캐릭터들)\n\n"
    for name, data in npcs.items():
        desc = data.get("desc", "설명 없음")
        status = data.get("status", "Active")
        status_emoji = "✅" if status == "Active" else "💀" if status == "Dead" else "❓"
        npc_section += f"**{name}** ({status_emoji} {status})\n{desc}\n\n"
    
    return lore + npc_section

# =========================================================
# NPC 관리
# =========================================================
def get_npcs(channel_id: str) -> Dict[str, Dict[str, Any]]:
    """NPC 목록을 가져옵니다."""
    return get_domain(channel_id).get("npcs", {})

def update_npc(channel_id: str, name: str, data: Dict[str, Any]) -> None:
    """NPC 정보를 업데이트합니다."""
    d = get_domain(channel_id)
    if "npcs" not in d:
        d["npcs"] = {}
    d["npcs"][name] = data
    save_domain(channel_id, d)

def delete_npc(channel_id: str, name: str) -> bool:
    """NPC를 삭제합니다."""
    d = get_domain(channel_id)
    if name in d.get("npcs", {}):
        del d["npcs"][name]
        save_domain(channel_id, d)
        return True
    return False

def rename_npc(channel_id: str, old_name: str, new_name: str) -> bool:
    """
    NPC의 이름을 변경합니다 (Key Rename).
    기존 데이터는 유지되며, 새로운 이름으로 이동합니다.
    """
    if not old_name or not new_name or old_name == new_name:
        return False
        
    d = get_domain(channel_id)
    npcs = d.get("npcs", {})
    
    # 1. Check if old exists
    if old_name not in npcs:
        return False
        
    data = npcs.pop(old_name)
    
    if new_name in npcs:
        # Merge old history into new
        existing_new = npcs[new_name]
        existing_new.update(data) 
        npcs[new_name] = existing_new
    else:
        npcs[new_name] = data
        
    d["npcs"] = npcs
    save_domain(channel_id, d)
    return True
