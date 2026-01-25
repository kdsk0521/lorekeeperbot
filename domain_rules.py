"""
Lorekeeper TRPG Bot - Domain Rules Module
Handles rules, growth systems, and scene types.
"""

import os
import logging
import config
from typing import List, Optional
from domain_io import (
    get_rules_file_path, load_text, save_text, 
    _rules_cache, get_domain, save_domain
)

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
