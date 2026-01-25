"""
Lorekeeper TRPG Bot - Domain Manager (Unified)
Centralizes Data Access, caching, and core entity management.
Consolidates: domain_io, domain_participant, domain_content, character_sheet
"""

import os
import json
import logging
import time
import math
from typing import Dict, Any, Optional, List, Set

import config

# =========================================================
# 1. FILE I/O & CACHING (Formerly domain_io.py)
# =========================================================

_session_cache: Dict[str, Dict[str, Any]] = {}
_lore_cache: Dict[str, str] = {}
_lore_original_cache: Dict[str, str] = {}
_rules_cache: Dict[str, str] = {}

def initialize_folders() -> None:
    for path in [config.SESSIONS_DIR, config.LORE_DIR, config.RULES_DIR]:
        if not os.path.exists(path):
            try:
                os.makedirs(path)
                logging.info(f"Created directory: {path}")
            except Exception as e:
                logging.error(f"Failed to create {path}: {e}")

def get_session_file_path(channel_id: str) -> str: return os.path.join(config.SESSIONS_DIR, f"{channel_id}.json")
def get_lore_file_path(channel_id: str) -> str: return os.path.join(config.LORE_DIR, f"{channel_id}.txt")
def get_lore_original_file_path(channel_id: str) -> str: return os.path.join(config.LORE_DIR, f"{channel_id}_original.txt")
def get_rules_file_path(channel_id: str) -> str: return os.path.join(config.RULES_DIR, f"{channel_id}.txt")

def load_json(filepath: str, default_val: Any) -> Any:
    if not os.path.exists(filepath): return default_val
    try:
        with open(filepath, 'r', encoding='utf-8') as f: return json.load(f)
    except Exception as e:
        logging.error(f"JSON load error {filepath}: {e}")
        return default_val

def save_json(filepath: str, data: Any) -> bool:
    try:
        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        return True
    except Exception as e:
        logging.error(f"JSON save error {filepath}: {e}")
        return False

def load_text(filepath: str, default_val: str) -> str:
    if not os.path.exists(filepath): return default_val
    try:
        with open(filepath, 'r', encoding='utf-8') as f: return f.read()
    except Exception as e:
        logging.error(f"Text load error {filepath}: {e}")
        return default_val

def save_text(filepath: str, text: str) -> bool:
    try:
        with open(filepath, 'w', encoding='utf-8') as f: f.write(text)
        return True
    except Exception as e:
        logging.error(f"Text save error {filepath}: {e}")
        return False

# =========================================================
# 2. CORE SESSION ACCESS
# =========================================================

def _get_default_session() -> Dict[str, Any]:
    return {
        "participants": {},
        "npcs": {},
        "history": [],
        "quest_board": {"active": [], "completed": [], "memos": [], "archive": [], "lore": []},
        "world_state": config.DEFAULT_WORLD_STATE.copy(),
        "settings": {"response_mode": "auto", "session_locked": False, "growth_system": "default"},
        "active_genres": ["noir"],
        "custom_tone": None,
        "ai_session_memory": {
            "world_summary": "", "current_arc": "", "active_threads": [], "resolved_threads": [],
            "key_events": [], "foreshadowing": [], "world_changes": [], "npc_summaries": {},
            "party_dynamics": "", "last_updated": ""
        },
        "fermented_history": [],
        "deep_memory": "",
        "last_export_idx": 0
    }

def get_domain(channel_id: str) -> Dict[str, Any]:
    if channel_id in _session_cache: return _session_cache[channel_id]
    
    default = _get_default_session()
    data = load_json(get_session_file_path(channel_id), default)
    
    if not isinstance(data, dict): data = default
    
    # Ensure keys
    for k in default:
        if k not in data: data[k] = default[k]
        
    _session_cache[channel_id] = data
    return data

def save_domain(channel_id: str, data: Dict[str, Any]) -> bool:
    _session_cache[channel_id] = data
    return save_json(get_session_file_path(channel_id), data)

def reset_domain(channel_id: str) -> None:
    paths = [get_session_file_path(channel_id), get_lore_file_path(channel_id),
             get_lore_original_file_path(channel_id), get_rules_file_path(channel_id)]
    for p in paths:
        if os.path.exists(p): 
            try: os.remove(p)
            except: pass
            
    _session_cache.pop(channel_id, None)
    _lore_cache.pop(channel_id, None)
    _lore_original_cache.pop(channel_id, None)
    _rules_cache.pop(channel_id, None)

# =========================================================
# 3. LORE & CONTENT MANAGEMENT (Formerly domain_content.py)
# =========================================================

def get_lore(channel_id: str) -> str:
    if channel_id in _lore_cache: return _lore_cache[channel_id]
    text = load_text(get_lore_file_path(channel_id), config.DEFAULT_LORE)
    _lore_cache[channel_id] = text
    return text

def append_lore(channel_id: str, text: str) -> None:
    cur = get_lore(channel_id)
    new_t = text if cur.strip() == config.DEFAULT_LORE.strip() else f"{cur}\n\n{text}"
    _lore_cache[channel_id] = new_t
    save_text(get_lore_file_path(channel_id), new_t)

def reset_lore(channel_id: str) -> None:
    reset_domain(channel_id) # Simplify: reset all if lore reset requested usually implies restart

def save_lore_original(channel_id: str, text: str) -> None:
    _lore_original_cache[channel_id] = text
    save_text(get_lore_original_file_path(channel_id), text)

def get_lore_original(channel_id: str) -> Optional[str]:
    if channel_id in _lore_original_cache: return _lore_original_cache[channel_id]
    path = get_lore_original_file_path(channel_id)
    if os.path.exists(path):
        t = load_text(path, "")
        _lore_original_cache[channel_id] = t
        return t
    return None

def get_lore_with_npcs(channel_id: str) -> str:
    lore = get_lore(channel_id)
    npcs = get_npcs(channel_id)
    if not npcs: return lore
    sec = "\n\n### 📋 NPC 정보\n\n"
    for n, d in npcs.items():
        sec += f"**{n}** ({d.get('status','Active')})\n{d.get('desc','-')}\n\n"
    return lore + sec

# NPCs
def get_npcs(channel_id: str) -> Dict[str, Dict[str, Any]]:
    return get_domain(channel_id).get("npcs", {})

def get_npc(channel_id: str, name: str) -> Optional[Dict[str, Any]]:
    return get_npcs(channel_id).get(name)

def update_npc(channel_id: str, name: str, data: Dict[str, Any]) -> None:
    d = get_domain(channel_id)
    d.setdefault("npcs", {})[name] = data
    save_domain(channel_id, d)

def delete_npc(channel_id: str, name: str) -> bool:
    d = get_domain(channel_id)
    if name in d.get("npcs", {}):
        del d["npcs"][name]
        save_domain(channel_id, d)
        return True
    return False

# Rules & Genres
def get_rules(channel_id: str) -> str:
    if channel_id in _rules_cache: return _rules_cache[channel_id]
    text = load_text(get_rules_file_path(channel_id), config.DEFAULT_RULES)
    _rules_cache[channel_id] = text
    return text

def append_rules(channel_id: str, text: str) -> None:
    d = get_domain(channel_id)
    if d.get("rules_mode") == "custom":
        cur = get_rules(channel_id)
        new_t = f"{cur}\n\n{text}"
    else:
        cust = d.get("custom_rules", "")
        cust = f"{cust}\n\n{text}" if cust else text
        d["custom_rules"] = cust
        d["rules_mode"] = "hybrid"
        save_domain(channel_id, d)
        new_t = f"{config.DEFAULT_RULES}\n\n[커스텀 추가]\n{cust}"
    
    _rules_cache[channel_id] = new_t
    save_text(get_rules_file_path(channel_id), new_t)

def reset_rules(channel_id: str) -> None:
    path = get_rules_file_path(channel_id)
    if os.path.exists(path): os.remove(path)
    _rules_cache.pop(channel_id, None)
    d = get_domain(channel_id)
    d["custom_rules"] = ""
    d["rules_mode"] = "default"
    save_domain(channel_id, d)

def set_custom_rules_from_file(channel_id: str, content: str) -> None:
    _rules_cache[channel_id] = content
    save_text(get_rules_file_path(channel_id), content)
    d = get_domain(channel_id)
    d["rules_mode"] = "custom"
    d["settings"]["growth_system"] = "custom"
    save_domain(channel_id, d)

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

def get_rules_mode(channel_id: str) -> str: return get_domain(channel_id).get("rules_mode", "default")
def get_growth_system(channel_id: str) -> str: return get_domain(channel_id).get("settings", {}).get("growth_system", "default")


# =========================================================
# 4. PARTICIPANT & PC MANAGEMENT (Formerly domain_participant.py)
# =========================================================

def _create_default_participant(display_name: str) -> Dict[str, Any]:
    return {
        "mask": display_name, "status": "active",
        "economy": {"gold": 0}, "inventory": {}, "status_effects": [],
        "ai_memory": {
            "appearance": "", "personality": "", "background": "", "relationships": {},
            "passives": [], "normalization": {}, "notes": "", "archived_info": []
        }
    }

def update_participant(channel_id: str, user, reset: bool = False) -> bool:
    d = get_domain(channel_id)
    uid = str(user.id)
    
    if reset or uid not in d["participants"]:
        d["participants"][uid] = _create_default_participant(user.display_name)
    else:
        d["participants"][uid]["status"] = "active"
        # Ensure schema
        if "ai_memory" not in d["participants"][uid]:
             d["participants"][uid]["ai_memory"] = _create_default_participant("")["ai_memory"]
        if "economy" not in d["participants"][uid]:
             d["participants"][uid]["economy"] = {"gold": 0}
             
    save_domain(channel_id, d)
    return True

def get_participant_data(channel_id: str, user_id: str) -> Optional[Dict[str, Any]]:
    return get_domain(channel_id).get("participants", {}).get(str(user_id))

def get_participant_status(channel_id: str, uid: str) -> str:
    return get_participant_data(channel_id, uid).get("status", "active")

def set_participant_status(channel_id: str, uid: str, status: str, reason: str = "") -> None:
    d = get_domain(channel_id)
    if str(uid) in d["participants"]:
        d["participants"][str(uid)]["status"] = status
        save_domain(channel_id, d)

def save_participant_data(channel_id: str, user_id: str, data: Dict[str, Any]) -> None:
    d = get_domain(channel_id)
    d["participants"][str(user_id)] = data
    save_domain(channel_id, d)

# PC Info & Masks
def set_default_pc_info(channel_id: str, pc_info: Dict[str, Any]) -> None:
    d = get_domain(channel_id)
    d["default_pc_info"] = pc_info
    save_domain(channel_id, d)

def get_default_pc_info(channel_id: str) -> Optional[Dict[str, Any]]:
    return get_domain(channel_id).get("default_pc_info")

def clear_default_pc_info(channel_id: str) -> None:
    d = get_domain(channel_id)
    d.pop("default_pc_info", None)
    save_domain(channel_id, d)

def get_user_mask(channel_id: str, uid: str) -> str:
    return get_participant_data(channel_id, uid).get("mask", "Unknown")

def set_user_mask(channel_id: str, uid: str, mask: str) -> None:
    d = get_domain(channel_id)
    if str(uid) in d["participants"]:
        d["participants"][str(uid)]["mask"] = mask
        save_domain(channel_id, d)

def set_user_description(channel_id: str, uid: str, desc: str) -> None:
    # Used for simple storage
    p = get_participant_data(channel_id, uid)
    if p:
        p["ai_memory"]["appearance"] = desc # Map to AI memory
        save_participant_data(channel_id, uid, p)

def apply_pc_info_to_user(channel_id: str, user_id: str) -> bool:
    pc_info = get_default_pc_info(channel_id)
    if not pc_info: return False
    
    p = get_participant_data(channel_id, user_id)
    if not p: return False
    
    mem = p["ai_memory"]
    
    # Map PC info to Memory
    if pc_info.get("appearance"): mem["appearance"] = pc_info["appearance"]
    if pc_info.get("personality"): mem["personality"] = pc_info["personality"]
    if pc_info.get("background"): mem["background"] = pc_info["background"]
    if pc_info.get("relationships"): mem["relationships"] = pc_info["relationships"]
    if pc_info.get("passives"): mem["passives"] = pc_info["passives"]
    
    # Inventory Merge
    if pc_info.get("inventory"):
        for k, v in pc_info["inventory"].items():
            p["inventory"][k] = v
            
    save_participant_data(channel_id, user_id, p)
    return True

# UI Helpers
def get_unified_player_info(channel_id: str, user_id: str) -> str:
    p = get_participant_data(channel_id, user_id)
    if not p: return "❌ 정보 없음"
    
    mem = p["ai_memory"]
    res = f"## 🎭 **{p.get('mask')}**\n\n"
    res += f"**💰 Gold:** {p['economy'].get('gold', 0)}\n"
    
    inv = p.get('inventory', {})
    res += f"**🎒 Inv:** {', '.join([f'{k}x{v}' for k,v in inv.items()]) if inv else '(Empty)'}\n"
    
    eff = p.get('status_effects', [])
    if eff: res += f"**⚠️ Status:** {', '.join(eff)}\n"
    
    res += "\n---\n"
    if mem.get("appearance"): res += f"**👤 Look:** {mem['appearance']}\n"
    if mem.get("personality"): res += f"**💭 Mind:** {mem['personality']}\n"
    if mem.get("background"): res += f"**📖 BG:** {mem['background']}\n"
    
    rels = mem.get("relationships", {})
    if rels: 
        res += "**🤝 Rels:**\n" + "\n".join([f"• {k}: {v}" for k,v in rels.items()]) + "\n"
        
    passives = mem.get("passives", [])
    if passives: res += f"**🏆 Passives:** {', '.join(passives)}\n"
    
    return res

# =========================================================
# 5. STATE ACCESSORS (From legacy domain_manager)
# =========================================================

def get_world_state(channel_id: str) -> Dict[str, Any]:
    return get_domain(channel_id).get("world_state", config.DEFAULT_WORLD_STATE.copy())

def update_world_state(channel_id: str, state: Dict[str, Any]) -> None:
    d = get_domain(channel_id)
    d["world_state"] = state
    save_domain(channel_id, d)

def get_quest_board(channel_id: str) -> Dict[str, Any]:
    return get_domain(channel_id).get("quest_board")

def update_quest_board(channel_id: str, board: Dict[str, Any]) -> None:
    d = get_domain(channel_id)
    d["quest_board"] = board
    save_domain(channel_id, d)

# Settings
def set_session_lock(channel_id: str, locked: bool) -> None:
    d = get_domain(channel_id)
    d["settings"]["session_locked"] = locked
    save_domain(channel_id, d)

def update_settings(channel_id: str, **kwargs) -> None:
    d = get_domain(channel_id)
    if "settings" not in d: d["settings"] = {}
    for k, v in kwargs.items():
        d["settings"][k] = v
    save_domain(channel_id, d)

def get_response_mode(channel_id: str) -> str:
    return get_domain(channel_id).get("settings", {}).get("response_mode", "auto")

def set_response_mode(channel_id: str, mode: str) -> None:
    d = get_domain(channel_id)
    d["settings"]["response_mode"] = mode
    save_domain(channel_id, d)

# History
def append_history(channel_id: str, role: str, content: str) -> None:
    d = get_domain(channel_id)
    d["history"].append({"role": role, "content": content})
    if len(d["history"]) > config.MAX_HISTORY_LENGTH:
        d["history"] = d["history"][-config.MAX_HISTORY_LENGTH:]
    save_domain(channel_id, d)

def get_history(channel_id: str) -> List[Dict[str, str]]:
    return get_domain(channel_id).get("history", [])

# =========================================================
# 6. CONTEXT GENERATORS (For AI)
# =========================================================

def get_party_status_context(channel_id: str) -> str:
    participants = get_domain(channel_id).get("participants", {})
    if not participants: return "Active Players: None"
    
    active = []
    for uid, p in participants.items():
        if p.get("status") != "active": continue
        
        mem = p.get("ai_memory", {})
        mask = p.get("mask", "Unknown")
        look = mem.get("appearance", "Unknown")[:50]
        cond = ", ".join(p.get("status_effects", [])) or "Normal"
        active.append(f"[{mask}] Look:{look}, Cond:{cond}")
        
    return "### PARTY\n" + "\n".join(active) if active else "All players inactive."

def get_ai_memory_for_prompt(channel_id: str, user_id: str) -> str:
    p = get_participant_data(channel_id, user_id)
    if not p: return ""
    mem = p["ai_memory"]
    
    parts = []
    if mem.get("relationships"): parts.append(f"Rels: {mem['relationships']}")
    if mem.get("passives"): parts.append(f"Passives: {mem['passives']}")
    if mem.get("known_info"): parts.append(f"Known: {mem['known_info'][:3]}")
    
    return "### PLAYER MEMORY\n" + "\n".join(parts) + "\n" if parts else ""

# NPC Memory
def get_session_ai_memory(channel_id: str) -> Dict[str, Any]:
    return get_domain(channel_id).get("ai_session_memory", {})

def update_session_ai_memory(channel_id: str, updates: Dict[str, Any]) -> None:
    d = get_domain(channel_id)
    mem = d.get("ai_session_memory", {})
    mem.update(updates)
    mem["last_updated"] = time.strftime('%Y-%m-%d %H:%M')
    d["ai_session_memory"] = mem
    save_domain(channel_id, d)
