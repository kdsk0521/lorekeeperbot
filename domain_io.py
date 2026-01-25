"""
Lorekeeper TRPG Bot - Domain I/O Module
Handles file I/O, caching, and core session access.
"""

import os
import json
import logging
from typing import Dict, Any, Optional, List
import time

import config

# =========================================================
# In-Memory Cache
# =========================================================
_session_cache: Dict[str, Dict[str, Any]] = {}
_lore_cache: Dict[str, str] = {}
_lore_original_cache: Dict[str, str] = {}
_rules_cache: Dict[str, str] = {}

# =========================================================
# 초기화
# =========================================================
def initialize_folders() -> None:
    """봇 실행에 필요한 데이터 폴더들을 초기화합니다."""
    folders = [config.SESSIONS_DIR, config.LORE_DIR, config.RULES_DIR]
    
    for path in folders:
        if not os.path.exists(path):
            try:
                os.makedirs(path)
                logging.info(f"폴더 생성됨: {path}")
            except Exception as e:
                logging.error(f"폴더 생성 실패 {path}: {e}")

# =========================================================
# 파일 경로 함수
# =========================================================
def get_session_file_path(channel_id: str) -> str:
    return os.path.join(config.SESSIONS_DIR, f"{channel_id}.json")

def get_lore_file_path(channel_id: str) -> str:
    return os.path.join(config.LORE_DIR, f"{channel_id}.txt")

def get_lore_original_file_path(channel_id: str) -> str:
    return os.path.join(config.LORE_DIR, f"{channel_id}_original.txt")

def get_rules_file_path(channel_id: str) -> str:
    return os.path.join(config.RULES_DIR, f"{channel_id}.txt")

# =========================================================
# 데이터 로드 및 저장 (I/O)
# =========================================================
def load_json(filepath: str, default_val: Any) -> Any:
    if not os.path.exists(filepath):
        return default_val
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            return json.load(f)
    except json.JSONDecodeError as e:
        logging.warning(f"JSON 파싱 실패 {filepath}: {e}")
        return default_val
    except Exception as e:
        logging.error(f"JSON 로드 실패 {filepath}: {e}")
        return default_val

def save_json(filepath: str, data: Any) -> bool:
    try:
        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        return True
    except Exception as e:
        logging.error(f"JSON 저장 실패 {filepath}: {e}")
        return False

def load_text(filepath: str, default_val: str) -> str:
    if not os.path.exists(filepath):
        return default_val
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            return f.read()
    except Exception as e:
        logging.error(f"텍스트 로드 실패 {filepath}: {e}")
        return default_val

def save_text(filepath: str, text: str) -> bool:
    try:
        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(text)
        return True
    except Exception as e:
        logging.error(f"텍스트 저장 실패 {filepath}: {e}")
        return False

# =========================================================
# 도메인(세션) 접근
# =========================================================
def _get_default_session() -> Dict[str, Any]:
    return {
        "participants": {},
        "npcs": {},
        "history": [],
        "quest_board": {
            "active": [],
            "completed": [],
            "memos": [],
            "archive": [],
            "lore": []
        },
        "world_state": config.DEFAULT_WORLD_STATE.copy(),
        "settings": {
            "response_mode": "auto",
            "session_locked": False,
            "growth_system": "default"
        },
        "active_genres": ["noir"],
        "custom_tone": None,
        "prepared": False,
        "disabled": False,
        "last_export_idx": 0,
        "ai_session_memory": {
            "world_summary": "",
            "current_arc": "",
            "active_threads": [],
            "resolved_threads": [],
            "key_events": [],
            "foreshadowing": [],
            "world_changes": [],
            "npc_summaries": {},
            "party_dynamics": "",
            "last_updated": ""
        },
        "fermented_history": [],
        "deep_memory": ""
    }

def get_domain(channel_id: str) -> Dict[str, Any]:
    # 1. 캐시 확인
    if channel_id in _session_cache:
        return _session_cache[channel_id]

    # 2. 파일 로드
    default_session = _get_default_session()
    data = load_json(get_session_file_path(channel_id), default_session)

    # 누락된 키 보정
    if not isinstance(data, dict):
        data = default_session

    missing_keys = set(default_session.keys()) - set(data.keys())
    for key in missing_keys:
        data[key] = default_session[key]

    if "world_state" in data and isinstance(data["world_state"], dict):
        missing_ws_keys = set(config.DEFAULT_WORLD_STATE.keys()) - set(data["world_state"].keys())
        for ws_key in missing_ws_keys:
            data["world_state"][ws_key] = config.DEFAULT_WORLD_STATE[ws_key]

    if "settings" not in data or not isinstance(data["settings"], dict):
        data["settings"] = default_session["settings"]

    # 3. 캐시 업데이트
    _session_cache[channel_id] = data
    return data

def save_domain(channel_id: str, data: Dict[str, Any]) -> bool:
    # Cache Write-Through
    _session_cache[channel_id] = data
    return save_json(get_session_file_path(channel_id), data)

def reset_domain(channel_id: str) -> None:
    """채널의 모든 데이터를 초기화합니다."""
    files_to_remove = [
        get_session_file_path(channel_id),
        get_lore_file_path(channel_id),
        get_lore_original_file_path(channel_id),
        get_rules_file_path(channel_id)
    ]
    
    for filepath in files_to_remove:
        if os.path.exists(filepath):
            try:
                os.remove(filepath)
            except Exception as e:
                logging.error(f"파일 삭제 실패 {filepath}: {e}")

    # Clear caches
    if channel_id in _session_cache: del _session_cache[channel_id]
    if channel_id in _lore_cache: del _lore_cache[channel_id]
    if channel_id in _lore_cache: del _lore_cache[channel_id] # typo in original but safe
    if channel_id in _rules_cache: del _rules_cache[channel_id]


def reset_session_data(channel_id: str) -> None:
    """세션 데이터만 초기화합니다."""
    d = get_domain(channel_id)
    d["history"] = []
    d["fermented_history"] = []
    d["deep_memory"] = ""
    d["participants"] = {}
    if "settings" in d:
        d["settings"]["session_locked"] = False
    else:
        d["settings"] = {"session_locked": False}
    if "session_locked" in d:
        del d["session_locked"]
    
    d["quest_board"] = {
        "active": [],
        "completed": [],
        "memos": [],
        "archive": [],
        "lore": []
    }
    d["ai_session_memory"] = {}
    save_domain(channel_id, d)


# =========================================================
# 히스토리 관리
# =========================================================
def append_history(channel_id: str, role: str, content: str) -> None:
    d = get_domain(channel_id)
    d["history"].append({"role": role, "content": content})
    
    if len(d["history"]) > config.MAX_HISTORY_LENGTH:
        d["history"] = d["history"][-config.MAX_HISTORY_LENGTH:]
    
    save_domain(channel_id, d)

def get_history(channel_id: str) -> List[Dict[str, str]]:
    return get_domain(channel_id).get("history", [])

# =========================================================
# 세션 설정 관리
# =========================================================
def set_session_lock(channel_id: str, locked: bool) -> None:
    d = get_domain(channel_id)
    d["settings"]["session_locked"] = locked
    save_domain(channel_id, d)

def is_session_locked(channel_id: str) -> bool:
    return get_domain(channel_id).get("settings", {}).get("session_locked", False)

def get_response_mode(channel_id: str) -> str:
    return get_domain(channel_id).get("settings", {}).get("response_mode", "auto")

def set_response_mode(channel_id: str, mode: str) -> None:
    d = get_domain(channel_id)
    d["settings"]["response_mode"] = mode
    save_domain(channel_id, d)

# =========================================================
# 월드 스테이트 관리
# =========================================================
def get_world_state(channel_id: str) -> Dict[str, Any]:
    return get_domain(channel_id).get("world_state", config.DEFAULT_WORLD_STATE.copy())

def update_world_state(channel_id: str, state: Dict[str, Any]) -> None:
    d = get_domain(channel_id)
    d["world_state"] = state
    save_domain(channel_id, d)

def set_current_location(channel_id: str, location: str) -> None:
    d = get_domain(channel_id)
    d["world_state"]["current_location"] = location
    save_domain(channel_id, d)

def set_current_risk(channel_id: str, risk: str) -> None:
    d = get_domain(channel_id)
    d["world_state"]["risk_level"] = risk
    save_domain(channel_id, d)

def set_location_rules(channel_id: str, rules: Dict[str, Any]) -> None:
    d = get_domain(channel_id)
    d["world_state"]["location_rules"] = rules
    save_domain(channel_id, d)

def set_world_constraints(channel_id: str, constraints: Dict[str, Any]) -> None:
    d = get_domain(channel_id)
    d["world_state"]["world_constraints"] = constraints
    save_domain(channel_id, d)

def get_world_constraints(channel_id: str) -> Dict[str, Any]:
    return get_domain(channel_id).get("world_state", {}).get("world_constraints", {})

def set_active_threads(channel_id: str, threads: List[str]) -> None:
    d = get_domain(channel_id)
    d["world_state"]["active_threads"] = threads
    save_domain(channel_id, d)

def get_active_threads(channel_id: str) -> List[str]:
    return get_domain(channel_id).get("world_state", {}).get("active_threads", [])

def set_temporal_context(channel_id: str, context: Dict[str, Any]) -> None:
    d = get_domain(channel_id)
    d["world_state"]["last_temporal_context"] = context
    save_domain(channel_id, d)

def get_temporal_context(channel_id: str) -> Dict[str, Any]:
    return get_domain(channel_id).get("world_state", {}).get("last_temporal_context", {})

# =========================================================
# 퀘스트 보드 관리
# =========================================================
def get_quest_board(channel_id: str) -> Optional[Dict[str, Any]]:
    return get_domain(channel_id).get("quest_board")

def update_quest_board(channel_id: str, board: Dict[str, Any]) -> None:
    d = get_domain(channel_id)
    d["quest_board"] = board
    save_domain(channel_id, d)


# =========================================================
# 세션 레벨 AI 메모리 관리
# =========================================================
def get_session_ai_memory(channel_id: str) -> Dict[str, Any]:
    d = get_domain(channel_id)
    if "ai_session_memory" not in d:
        d["ai_session_memory"] = {
            "world_summary": "",
            "current_arc": "",
            "active_threads": [],
            "resolved_threads": [],
            "key_events": [],
            "foreshadowing": [],
            "world_changes": [],
            "npc_summaries": {},
            "party_dynamics": "",
            "last_updated": ""
        }
        save_domain(channel_id, d)
    return d["ai_session_memory"]

def update_session_ai_memory(channel_id: str, updates: Dict[str, Any]) -> None:
    d = get_domain(channel_id)
    if "ai_session_memory" not in d:
        d["ai_session_memory"] = {}
    
    for key, value in updates.items():
        if isinstance(value, list) and isinstance(d["ai_session_memory"].get(key), list):
            existing = d["ai_session_memory"].get(key, [])
            combined = existing + [v for v in value if v not in existing]
            d["ai_session_memory"][key] = combined[-20:]
        elif isinstance(value, dict) and isinstance(d["ai_session_memory"].get(key), dict):
            d["ai_session_memory"][key].update(value)
        else:
            d["ai_session_memory"][key] = value
            
    d["ai_session_memory"]["last_updated"] = time.strftime('%Y-%m-%d %H:%M')
    save_domain(channel_id, d)

def resolve_thread(channel_id: str, thread: str) -> bool:
    d = get_domain(channel_id)
    session_mem = d.get("ai_session_memory", {})
    active = session_mem.get("active_threads", [])
    resolved = session_mem.get("resolved_threads", [])
    
    if thread in active:
        active.remove(thread)
        resolved.append(thread)
        session_mem["active_threads"] = active
        session_mem["resolved_threads"] = resolved
        d["ai_session_memory"] = session_mem
        save_domain(channel_id, d)
        return True
    return False

def add_key_event(channel_id: str, event: str) -> bool:
    d = get_domain(channel_id)
    world_state = d.get("world_state", {})
    day = world_state.get("day", 1)
    event_with_day = f"{day}일차: {event}"
    update_session_ai_memory(channel_id, {
        "key_events": [event_with_day]
    })
    return True

def get_session_ai_memory_for_prompt(channel_id: str) -> str:
    session_mem = get_session_ai_memory(channel_id)
    if not session_mem:
        return ""
    
    lines = ["### [SESSION AI MEMORY]"]
    
    if session_mem.get("world_summary"):
        lines.append(f"세계 상황: {session_mem['world_summary']}")
    if session_mem.get("current_arc"):
        lines.append(f"현재 스토리: {session_mem['current_arc']}")
    
    threads = session_mem.get("active_threads", [])
    if threads:
        lines.append(f"진행 중인 이야기: {', '.join(threads)}")
    
    foreshadow = session_mem.get("foreshadowing", [])
    if foreshadow:
        lines.append(f"미해결 복선: {', '.join(foreshadow[:5])}")
    
    changes = session_mem.get("world_changes", [])
    if changes:
        lines.append(f"세계 변화: {'; '.join(changes[-3:])}")
    
    npc_sums = session_mem.get("npc_summaries", {})
    if npc_sums:
        npc_strs = [f"{k}({v})" for k, v in list(npc_sums.items())[:5]]
        lines.append(f"주요 NPC: {', '.join(npc_strs)}")
    
    if session_mem.get("party_dynamics"):
        lines.append(f"파티 상황: {session_mem['party_dynamics']}")
    
    return "\n".join(lines)
