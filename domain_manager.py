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
from cache_manager import cache

# =========================================================
# 1. FILE I/O & CACHING (Formerly domain_io.py)
# =========================================================

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
        "settings": {
            "response_mode": "auto", 
            "session_locked": False, 
            "growth_system": "default", 
            "abnormal_mode": True,
            "scene_type": "normal"  # normal / gore / nsfw / gore_nsfw
        },
        "active_genres": ["noir"],
        "custom_tone": None,
        "ai_session_memory": {
            "world_summary": "", "current_arc": "", "active_threads": [], "resolved_threads": [],
            "key_events": [], "foreshadowing": [], "world_changes": [], "npc_summaries": {},
            "party_dynamics": "", "last_updated": ""
        },
        "fermented_history": [],
        "deep_memory": "",
        "last_export_idx": 0,
        "last_chronicle_idx": 0,
        "bot_active": True,  # Default: Bot is ON
        "notebook": "— [소지품] —\n\n— [메모] —" # [V5.1] Unified Notebook
    }

def get_notebook(channel_id: str) -> str:
    return get_domain(channel_id).get("notebook", "— [소지품] —\n\n— [메모] —")

def update_notebook(channel_id: str, text: str) -> None:
    d = get_domain(channel_id)
    d["notebook"] = text
    save_domain(channel_id, d)

# =========================================================
# MATURE MODE MANAGEMENT (via settings.scene_type)
# =========================================================
VALID_MATURE_MODES = {"normal", "gore", "nsfw", "gore_nsfw"}

def get_mature_mode(channel_id: str) -> str:
    """현재 채널의 성인 콘텐츠 모드를 반환합니다. (settings.scene_type 사용)"""
    return get_domain(channel_id).get("settings", {}).get("scene_type", "normal")

def set_mature_mode(channel_id: str, mode: str) -> bool:
    """
    채널의 성인 콘텐츠 모드를 설정합니다.
    
    Args:
        mode: 'normal', 'gore', 'nsfw', 'gore_nsfw' 중 하나
    
    Returns:
        성공 여부
    """
    mode = mode.lower().strip()
    if mode not in VALID_MATURE_MODES:
        return False
    
    update_settings(channel_id, scene_type=mode)
    return True

def get_domain(channel_id: str) -> Dict[str, Any]:
    # 캐시에서 먼저 조회
    cached = cache.get_session(channel_id)
    if cached is not None:
        return cached

    # 캐시 미스: 파일에서 로드
    default = _get_default_session()
    data = load_json(get_session_file_path(channel_id), default)

    if not isinstance(data, dict):
        data = default

    # Ensure keys
    for k in default:
        if k not in data:
            data[k] = default[k]

    # 캐시에 저장
    cache.set_session(channel_id, data)
    return data

def save_domain(channel_id: str, data: Dict[str, Any]) -> bool:
    """세션 데이터 저장 (파일 + 캐시 동기화)"""
    # 파일 저장 성공 후 캐시 업데이트 (동기화 안전성)
    if not save_json(get_session_file_path(channel_id), data):
        return False
    cache.set_session(channel_id, data)
    return True

def reset_domain(channel_id: str) -> None:
    """채널의 모든 데이터 초기화 (파일 삭제 + 캐시 무효화)"""
    paths = [get_session_file_path(channel_id), get_lore_file_path(channel_id),
             get_lore_original_file_path(channel_id), get_rules_file_path(channel_id)]
    for p in paths:
        if os.path.exists(p):
            try:
                os.remove(p)
            except (OSError, PermissionError) as e:
                logging.warning(f"Failed to delete {p}: {e}")

    # 모든 캐시 무효화
    cache.invalidate_all(channel_id)

# Export Indices
def get_last_export_idx(channel_id: str) -> int:
    return get_domain(channel_id).get("last_export_idx", 0)

def set_last_export_idx(channel_id: str, idx: int) -> None:
    d = get_domain(channel_id)
    d["last_export_idx"] = idx
    save_domain(channel_id, d)

def get_last_chronicle_idx(channel_id: str) -> int:
    return get_domain(channel_id).get("last_chronicle_idx", 0)

def set_last_chronicle_idx(channel_id: str, idx: int) -> None:
    d = get_domain(channel_id)
    d["last_chronicle_idx"] = idx
    save_domain(channel_id, d)

# =========================================================
# 3. LORE & CONTENT MANAGEMENT (Formerly domain_content.py)
# =========================================================

def get_lore(channel_id: str) -> str:
    """로어 텍스트 조회 (캐시 우선)"""
    cached = cache.get_lore(channel_id)
    if cached is not None:
        return cached
    text = load_text(get_lore_file_path(channel_id), config.DEFAULT_LORE)
    cache.set_lore(channel_id, text)
    return text

def append_lore(channel_id: str, text: str) -> None:
    """로어에 텍스트 추가"""
    cur = get_lore(channel_id)
    new_t = text if cur.strip() == config.DEFAULT_LORE.strip() else f"{cur}\n\n{text}"
    cache.set_lore(channel_id, new_t)
    save_text(get_lore_file_path(channel_id), new_t)

def reset_lore(channel_id: str) -> None:
    reset_domain(channel_id) # Simplify: reset all if lore reset requested usually implies restart

def save_lore_original(channel_id: str, text: str) -> None:
    """원본 로어 저장"""
    cache.set_lore_original(channel_id, text)
    save_text(get_lore_original_file_path(channel_id), text)

def get_lore_original(channel_id: str) -> Optional[str]:
    """원본 로어 조회"""
    cached = cache.get_lore_original(channel_id)
    if cached is not None:
        return cached
    path = get_lore_original_file_path(channel_id)
    if os.path.exists(path):
        t = load_text(path, "")
        cache.set_lore_original(channel_id, t)
        return t
    return None

def get_event_lore_summary_file_path(channel_id: str) -> str:
    return os.path.join(config.LORE_DIR, f"{channel_id}_summary.txt")

def get_event_lore_summary(channel_id: str) -> str:
    path = get_event_lore_summary_file_path(channel_id)
    return load_text(path, "")

def set_event_lore_summary(channel_id: str, text: str) -> None:
    path = get_event_lore_summary_file_path(channel_id)
    save_text(path, text)

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
    npcs = d.setdefault("npcs", {})
    
    # [Restored Logic] Identity Reveal Tracking
    # If name changes (OldName > NewName), handle it (Logic usually in higher layer, 
    # but we support preserving 'source' and 'identity' fields here).
    
    # Ensure source field exists (Default: 'session' if created dynamically, 'lore' if loaded initially?)
    # Callers should specify source. If not present and updating, keep existing.
    if name in npcs:
        existing_source = npcs[name].get("source", "session")
        if "source" not in data: data["source"] = existing_source
        
    npcs[name] = data
    save_domain(channel_id, d)

def delete_npc(channel_id: str, name: str) -> bool:
    d = get_domain(channel_id)
    if name in d.get("npcs", {}):
        del d["npcs"][name]
        save_domain(channel_id, d)
        return True
    return False

# NPC Attitude System
def update_npc_attitude(channel_id: str, npc_name: str, attitude: str, reason: str = "") -> None:
    """NPC의 PC에 대한 태도 업데이트"""
    d = get_domain(channel_id)
    if "npc_attitudes" not in d:
        d["npc_attitudes"] = {}
    
    d["npc_attitudes"][npc_name] = {
        "attitude": attitude,
        "reason": reason,
        "depth": 0,    # [Phase 2] Helena Depth (0-100)
        "tension": 0,  # [Phase 2] Helena Tension (0-100)
        "last_updated": time.strftime('%Y-%m-%d %H:%M')
    }
    save_domain(channel_id, d)

def get_npc_attitudes(channel_id: str) -> Dict[str, Dict]:
    """저장된 NPC 태도 조회"""
    d = get_domain(channel_id)
    return d.get("npc_attitudes", {})

def get_npc_attitude(channel_id: str, npc_name: str) -> Optional[Dict]:
    """특정 NPC의 태도 조회"""
    attitudes = get_npc_attitudes(channel_id)
    return attitudes.get(npc_name)

# Rules & Genres
def get_rules(channel_id: str) -> str:
    """룰 텍스트 조회 (캐시 우선)"""
    cached = cache.get_rules(channel_id)
    if cached is not None:
        return cached
    text = load_text(get_rules_file_path(channel_id), config.DEFAULT_RULES)
    cache.set_rules(channel_id, text)
    return text

def append_rules(channel_id: str, text: str) -> None:
    """룰에 텍스트 추가"""
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

    cache.set_rules(channel_id, new_t)
    save_text(get_rules_file_path(channel_id), new_t)

def reset_rules(channel_id: str) -> None:
    """룰 초기화"""
    path = get_rules_file_path(channel_id)
    if os.path.exists(path):
        os.remove(path)
    cache.invalidate_rules(channel_id)
    d = get_domain(channel_id)
    d["custom_rules"] = ""
    d["rules_mode"] = "default"
    save_domain(channel_id, d)

def set_custom_rules_from_file(channel_id: str, content: str) -> None:
    """파일에서 커스텀 룰 설정"""
    cache.set_rules(channel_id, content)
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
        "status_effects": [],
        "ai_memory": {
            "appearance": "", "personality": "", "background": "", "relationships": {},
            "passives": [], "normalization": {}, "notes": "", "archived_info": [],
            # [V7] Core Systems
            "mental": {"value": 100, "last_delta": 0}, # 0-100 Scale
            "abnormal_exposure": {}, # {Tag: {count: N, level: N}}
            
            # [Phase 2] Mnemosyne: PsychProfile
            "psych_profile": {
                "needs": {"survival": 50, "safety": 50, "love": 50, "esteem": 50, "self_actualization": 50},
                "values": ["security", "conformity"], # Default safe values
                "instinct": "neutral"
            }
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
             
    save_domain(channel_id, d)
    return True

def get_participant_data(channel_id: str, user_id: str) -> Optional[Dict[str, Any]]:
    p = get_domain(channel_id).get("participants", {}).get(str(user_id))
    if p is not None and not isinstance(p, dict):
        raise ValueError(f"Corrupted Participant Data for {user_id}: Expected dict, got {type(p).__name__} ({p})")
    return p

def get_active_participants(channel_id: str) -> Dict[str, Any]:
    """[V7] 활성 상태인 플레이어 데이터만 반환"""
    d = get_domain(channel_id)
    active = {}
    for uid, p in d.get("participants", {}).items():
        if p.get("status") == "active":
            active[uid] = p
    return active

def get_participant_status(channel_id: str, uid: str) -> str:
    p = get_participant_data(channel_id, uid)
    return p.get("status", "active") if p else "unknown"

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
    p = get_participant_data(channel_id, uid)
    return p.get("mask", "Unknown") if p else "Unknown"

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
    if pc_info.get("relationships"): mem["relationships"] = pc_info["relationships"]
    if pc_info.get("passives"): mem["passives"] = pc_info["passives"]
    
    # Notes/Memos Merge (Notebook Integration)
    if pc_info.get("notes") or pc_info.get("memos"):
        notes = pc_info.get("notes") or pc_info.get("memos")
        if notes:
            # Append to Notebook [메모] section
            import game_system
            if isinstance(notes, list):
                for n in notes: game_system.add_memo(channel_id, n)
            else:
                game_system.add_memo(channel_id, str(notes))
    
    # Inventory Merge (Notebook Integration)
    if pc_info.get("inventory"):
        # Convert legacy dict inventory to Notebook format
        items = []
        for k, v in pc_info["inventory"].items():
            try:
                count = int(v)
            except (ValueError, TypeError):
                count = 1
            
            if count > 1: items.append(f"{k} x{count}")
            else: items.append(k)
            
        if items:
            import game_system # Avoid circular import if possible, or use domain_manager helper
            current_nb = get_notebook(channel_id)
            
            # Simple Append
            new_lines = [f"- {i} (초기 장비)" for i in items]
            
            if "— [소지품] —" in current_nb:
                # Insert after header if possible
                parts = current_nb.split("— [소지품] —")
                new_nb = parts[0] + "— [소지품] —\n" + "\n".join(new_lines) + parts[1]
            else:
                 new_nb = current_nb + "\n\n— [소지품] —\n" + "\n".join(new_lines)
            
            update_notebook(channel_id, new_nb)
            
    save_participant_data(channel_id, user_id, p)
    return True

def get_ai_memory(channel_id: str, uid: str) -> Dict[str, Any]:
    p = get_participant_data(channel_id, uid)
    return p.get("ai_memory", {}) if p else {}

def update_ai_memory(channel_id: str, uid: str, updates: Dict[str, Any]) -> None:
    p = get_participant_data(channel_id, uid)
    if not p: return
    
    mem = p.get("ai_memory", {})
    
    # Special handling for dictionaries (deep merge)
    for k, v in updates.items():
        if k == "relationships" and isinstance(v, dict):
            current_rels = mem.get("relationships", {})
            current_rels.update(v)
            mem[k] = current_rels
        else:
            mem[k] = v
            
    p["ai_memory"] = mem
    save_participant_data(channel_id, uid, p)

def update_npc_relationship(channel_id: str, uid: str, npc_name: str, rel_text: str) -> str:
    """[Extracted from Memory] Update specific NPC relationship in Player AI Memory"""
    update_ai_memory(channel_id, uid, {"relationships": {npc_name: rel_text}})
    return rel_text

def add_to_ai_memory_list(channel_id: str, uid: str, key: str, item: str) -> None:
    p = get_participant_data(channel_id, uid)
    if not p: return
    
    mem = p.get("ai_memory", {})
    if key not in mem: mem[key] = []
    
    if isinstance(mem[key], list):
        # [Fix] Deep Deduplication for Passives (Dict)
        is_duplicate = False
        if key == "passives" and isinstance(item, dict):
            new_name = item.get("name", "Unknown")
            for existing in mem[key]:
                if isinstance(existing, dict) and existing.get("name") == new_name:
                    is_duplicate = True
                    break
                elif isinstance(existing, str) and existing == new_name:
                    is_duplicate = True
                    break
        elif item in mem[key]:
             is_duplicate = True
             
        if not is_duplicate:
            mem[key].append(item)
        
    p["ai_memory"] = mem
    save_participant_data(channel_id, uid, p)

# [Phase 2] PsychProfile Accessors
def get_psych_profile(channel_id: str, uid: str) -> Dict[str, Any]:
    p = get_participant_data(channel_id, uid)
    if not p: return {}
    return p.get("ai_memory", {}).get("psych_profile", {})

def update_psych_profile(channel_id: str, uid: str, profile_updates: Dict[str, Any]) -> None:
    p = get_participant_data(channel_id, uid)
    if not p: return
    
    if "psych_profile" not in p["ai_memory"]:
        p["ai_memory"]["psych_profile"] = {
             "needs": {"survival": 50, "safety": 50, "love": 50, "esteem": 50, "self_actualization": 50},
             "values": ["security"], "instinct": "neutral"
        }
    
    current = p["ai_memory"]["psych_profile"]
    
    # Deep merge for 'needs'
    if "needs" in profile_updates:
        current["needs"].update(profile_updates["needs"])
        del profile_updates["needs"]
        
    current.update(profile_updates)
    p["ai_memory"]["psych_profile"] = current
    save_participant_data(channel_id, uid, p)

def update_helena_metric(channel_id: str, npc_name: str, depth_delta: int = 0, tension_delta: int = 0) -> None:
    """[Phase 2] Update Helena metrics (Depth/Tension) for an NPC relation"""
    d = get_domain(channel_id)
    if "npc_attitudes" not in d: d["npc_attitudes"] = {}
    if npc_name not in d["npc_attitudes"]: return # Must exist first
    
    target = d["npc_attitudes"][npc_name]
    
    # Initialize if missing (Migration support)
    if "depth" not in target: target["depth"] = 0
    if "tension" not in target: target["tension"] = 0
    
    # Update and Clamp (0-100)
    target["depth"] = max(0, min(100, target["depth"] + depth_delta))
    target["tension"] = max(0, min(100, target["tension"] + tension_delta))
    target["last_updated"] = time.strftime('%Y-%m-%d %H:%M')
    
    save_domain(channel_id, d)

def find_participant_id_by_name(channel_id: str, name: str) -> Optional[str]:
    d = get_domain(channel_id)
    target = name.strip().lower()
    for uid, p in d.get("participants", {}).items():
        if p.get("mask", "").lower() == target:
            return uid
    return None

# UI Helpers
def get_unified_player_info(channel_id: str, user_id: str) -> str:
    """
    [V7] 통합 플레이어 정보 반환 (프롬프트 주입용)
    - 캐릭터 이름/외모 (Description)
    - 상태 이상 (Status Effects)
    - 패시브 (Traits)
    - 관계 (Relationships)
    - 배경 (Background)
    - 멘탈 (Mental)
    - 노트북 (Notebook)
    """
    p = get_participant_data(channel_id, user_id)
    if not p:
        return "## 🎭 Unknown Player\n(No data available)"

    name = p.get("mask", "Unknown")
    mem = p.get("ai_memory", {})
    
    # 1. Description (Appearance + Personality + Background)
    desc_parts = []
    if mem.get("appearance"): desc_parts.append(f"Appearance: {mem['appearance']}")
    if mem.get("personality"): desc_parts.append(f"Personality: {mem['personality']}")
    # [Restored] Background
    if mem.get("background"): desc_parts.append(f"Background: {mem['background']}")
    
    desc_text = "\n".join(desc_parts) if desc_parts else "No description available."

    # 2. Status Effects
    status_effects = p.get("status_effects", [])
    status_text = ", ".join(status_effects) if status_effects else "Healthy (Normal)"

    # 3. Passives (Traits)
    passives = mem.get("passives", [])
    p_names = []
    for pas in passives:
        if isinstance(pas, dict): p_names.append(pas.get("name", "Unknown"))
        else: p_names.append(str(pas))
    passive_text = ", ".join(p_names) if p_names else "None"
    
    # 4. Adaptation Stats
    abnormal = mem.get("abnormal_exposure", {})
    abnormal_text = ""
    if abnormal:
        items = []
        for tag, data in abnormal.items():
            count = data.get("count", 0)
            items.append(f"{tag}({count})")
        if items:
            abnormal_text = ", ".join(items)

    # 5. [Restored] Relationships
    rels = mem.get("relationships", {})
    rel_text = "None"
    if rels:
        rel_text = ", ".join([f"{k}: {v}" for k, v in rels.items()])

    # 6. [Added] Mental Status
    mental = mem.get("mental", {})
    mental_val = mental.get("value", 100)
    mental_text = f"{mental_val}/100"

    # 7. [Added] Notebook
    notebook = get_notebook(channel_id)

    # 8. Construct Block
    return f"""## 🎭 {name} (Player Character)
- **Status Condition**: {status_text}
- **Mental Status**: {mental_text}
- **Passives**: {passive_text}
- **Abnormal Adaptation**: {abnormal_text if abnormal_text else "None"}
- **Relationships**: {rel_text}
- **Description**:
{desc_text}

### 📓 Player Notebook (Inventory & Memos)
{notebook}

⚠️ CRITICAL: YOU ARE THE GM. {name} IS THE PLAYER.
DO NOT speak for {name}. DO NOT describe {name}'s actions.
Only describe the world's reaction to {name}.
"""

# =========================================================
# 5. STATE ACCESSORS (From legacy domain_manager)
# =========================================================

def get_world_state(channel_id: str) -> Dict[str, Any]:
    return get_domain(channel_id).get("world_state", config.DEFAULT_WORLD_STATE.copy())

def update_world_state(channel_id: str, state: Dict[str, Any]) -> None:
    d = get_domain(channel_id)
    d["world_state"] = state
    save_domain(channel_id, d)

def get_current_location(channel_id: str) -> str:
    ws = get_world_state(channel_id)
    return ws.get("current_location") or ws.get("location", "Unknown")

def set_current_location(channel_id: str, location: str) -> None:
    ws = get_world_state(channel_id)
    ws["current_location"] = location
    update_world_state(channel_id, ws)

def get_current_risk(channel_id: str) -> str:
    ws = get_world_state(channel_id)
    return ws.get("risk_level", "Low")

def set_current_risk(channel_id: str, risk: str) -> None:
    ws = get_world_state(channel_id)
    ws["risk_level"] = risk
    update_world_state(channel_id, ws)

def get_quest_board(channel_id: str) -> Dict[str, Any]:
    return get_domain(channel_id).get("quest_board")

def update_quest_board(channel_id: str, board: Dict[str, Any]) -> None:
    d = get_domain(channel_id)
    d["quest_board"] = board
    save_domain(channel_id, d)

# Settings
def is_session_locked(channel_id: str) -> bool:
    d = get_domain(channel_id)
    return d.get("settings", {}).get("session_locked", False)

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

def get_abnormal_mode(channel_id: str) -> bool:
    """비일상 적응도 시스템 활성화 여부 (Default: True)"""
    return get_domain(channel_id).get("settings", {}).get("abnormal_mode", True)

def set_abnormal_mode(channel_id: str, enabled: bool) -> None:
    d = get_domain(channel_id)
    d["settings"]["abnormal_mode"] = enabled
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

def set_session_ai_memory(channel_id: str, data: Dict[str, Any]) -> None:
    d = get_domain(channel_id)
    d["ai_session_memory"] = data
    save_domain(channel_id, d)

# Bot Active State
def get_bot_active(channel_id: str) -> bool:
    return get_domain(channel_id).get("bot_active", True)

def set_bot_active(channel_id: str, active: bool) -> None:
    d = get_domain(channel_id)
    d["bot_active"] = active
    save_domain(channel_id, d)
def reset_session_state(channel_id: str) -> None:
    """
    세션을 '준비 완료' 상태로 초기화합니다.
    - 로어, 룰, 참가자 명단 유지
    - 히스토리, 발효 기억, 심층 기억 삭제
    - 월드 상태 초기화 (1일차, 오후)
    - 세션 NPC 및 퀘스트 초기화
    """
    d = get_domain(channel_id)
    
    # 1. Reset History
    d["history"] = []
    d["fermented_history"] = []
    d["deep_memory"] = ""
    d["ai_session_memory"] = _get_default_session()["ai_session_memory"]
    
    # 2. Reset World State
    d["world_state"] = config.DEFAULT_WORLD_STATE.copy()
    d["settings"]["session_locked"] = False # Unlock for re-start
    
    # 3. Reset Quests & Notebook (Keep Lore Items if any? No, reset all dynamic)
    d["quest_board"] = {"active": [], "completed": [], "memos": [], "archive": [], "lore": []}
    d["notebook"] = "— [소지품] —\n\n— [메모] —"
    
    # 4. Reset Session NPCs (Keep 'lore' NPCs)
    # Filter NPCs ensuring we keep only source="lore"
    # Note: 'npcs' dict keys are names.
    if "npcs" in d:
        kept_npcs = {}
        for name, data in d["npcs"].items():
            if data.get("source") == "lore":
                kept_npcs[name] = data
        d["npcs"] = kept_npcs
        
    save_domain(channel_id, d)



