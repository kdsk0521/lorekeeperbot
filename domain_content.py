"""
Lorekeeper TRPG Bot - Domain Content Module
Handles lore, NPCs, rules, genres, and world settings.
Merges previous domain_lore and domain_rules modules.
"""

import os
import logging
import config
from typing import Dict, Any, Optional, List
from domain_io import (
    get_lore_file_path, get_lore_original_file_path, get_rules_file_path,
    load_text, save_text, _lore_cache, _lore_original_cache, _rules_cache,
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

# =========================================================
# 룰 관리
# =========================================================
def get_rules(channel_id: str) -> str:
    if channel_id in _rules_cache:
        return _rules_cache[channel_id]
    text = load_text(get_rules_file_path(channel_id), config.DEFAULT_RULES)
    _rules_cache[channel_id] = text
    return text

def get_rules_mode(channel_id: str) -> str:
    return get_domain(channel_id).get("rules_mode", "default")

def set_rules_mode(channel_id: str, mode: str) -> None:
    d = get_domain(channel_id)
    d["rules_mode"] = mode
    save_domain(channel_id, d)

def append_rules(channel_id: str, text: str) -> None:
    current_mode = get_rules_mode(channel_id)
    
    if current_mode == "custom":
        current = get_rules(channel_id)
        new_text = f"{current}\n\n{text}"
    else:
        d = get_domain(channel_id)
        custom_rules = d.get("custom_rules", "")
        if custom_rules:
            custom_rules = f"{custom_rules}\n\n{text}"
        else:
            custom_rules = text
        d["custom_rules"] = custom_rules
        save_domain(channel_id, d)
        set_rules_mode(channel_id, "hybrid")
        new_text = f"{config.DEFAULT_RULES}\n\n[커스텀 추가 규칙]\n{custom_rules}"
    
    _rules_cache[channel_id] = new_text
    save_text(get_rules_file_path(channel_id), new_text)

def set_custom_rules_from_file(channel_id: str, file_content: str) -> None:
    _rules_cache[channel_id] = file_content
    save_text(get_rules_file_path(channel_id), file_content)
    set_rules_mode(channel_id, "custom")
    
    d = get_domain(channel_id)
    d["custom_rules"] = ""
    if "settings" not in d:
        d["settings"] = {}
    d["settings"]["growth_system"] = "custom"
    save_domain(channel_id, d)

def reset_rules(channel_id: str) -> None:
    path = get_rules_file_path(channel_id)
    if os.path.exists(path):
        try:
            os.remove(path)
        except Exception as e:
            logging.error(f"파일 삭제 실패 {path}: {e}")
            
    if channel_id in _rules_cache:
        del _rules_cache[channel_id]
    
    set_rules_mode(channel_id, "default")
    
    d = get_domain(channel_id)
    d["custom_rules"] = ""
    if "settings" not in d:
        d["settings"] = {}
    d["settings"]["growth_system"] = "default"
    save_domain(channel_id, d)

def get_custom_rules_part(channel_id: str) -> str:
    return get_domain(channel_id).get("custom_rules", "")

# =========================================================
# 장르 및 톤 관리
# =========================================================
def get_active_genres(channel_id: str) -> List[str]:
    return get_domain(channel_id).get("active_genres", ["noir"])

def set_active_genres(channel_id: str, genres: List[str]) -> None:
    d = get_domain(channel_id)
    d["active_genres"] = genres
    save_domain(channel_id, d)

def get_custom_tone(channel_id: str) -> Optional[str]:
    return get_domain(channel_id).get("custom_tone")

def set_custom_tone(channel_id: str, tone: Optional[str]) -> None:
    d = get_domain(channel_id)
    d["custom_tone"] = tone
    save_domain(channel_id, d)

# =========================================================
# 장면 유형 관리
# =========================================================
def get_scene_type(channel_id: str) -> str:
    return get_domain(channel_id).get("scene_type", "normal")

def set_scene_type(channel_id: str, scene_type: str) -> None:
    valid_types = ['normal', 'gore', 'nsfw', 'gore_nsfw']
    if scene_type not in valid_types:
        scene_type = 'normal'
    d = get_domain(channel_id)
    d['scene_type'] = scene_type
    save_domain(channel_id, d)

# =========================================================
# 성장 시스템 (규칙 연동)
# =========================================================
def get_growth_system(channel_id: str) -> str:
    return get_domain(channel_id).get("settings", {}).get("growth_system", "default")
