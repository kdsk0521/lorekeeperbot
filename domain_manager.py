import os
import json
import logging
import time

DEFAULT_LORE = "[장르: 설정되지 않음]"
DEFAULT_RULES = """
[게임 규칙: 표준 TRPG 시스템]
1. 판정: 모든 행동은 !r 1d20 (20면체 주사위)을 통해 결정됩니다.
2. 난이도(DC): 보통 10 / 어려움 15 / 매우 어려움 20 / 불가능 25
3. 전투: 주사위 값이 높을수록 더 효율적이고 치명적인 공격을 성공시킵니다.
4. 성장: 캐릭터는 행동과 선택을 통해 경험치를 얻고 레벨업하며 스탯을 올립니다.
"""

DATA_DIR = "data"
SESSIONS_DIR = os.path.join(DATA_DIR, "sessions")
LORE_DIR = os.path.join(DATA_DIR, "lores")
RULES_DIR = os.path.join(DATA_DIR, "rules")

def initialize_folders():
    for path in [SESSIONS_DIR, LORE_DIR, RULES_DIR]:
        if not os.path.exists(path):
            try: os.makedirs(path)
            except Exception as e: logging.error(f"폴더 생성 실패 {path}: {e}")

def get_session_file_path(channel_id):
    return os.path.join(SESSIONS_DIR, f"{channel_id}.json")

def get_lore_file_path(channel_id):
    return os.path.join(LORE_DIR, f"lore_{channel_id}.txt")

def get_rules_file_path(channel_id):
    return os.path.join(RULES_DIR, f"rules_{channel_id}.txt")

def record_historical_event(channel_id, event_text):
    domain = get_domain(channel_id)
    world = domain.get("world_state", {})
    day = world.get("day", 1)
    time_slot = world.get("time_slot", "알 수 없음")
    formatted_log = f"\n[역사적 기록 - 제 {day}일 {time_slot}] {event_text}"
    append_lore(channel_id, formatted_log)
    logging.info(f"Event recorded: {event_text}")

def _create_new_domain():
    return {
        "history": [],        
        "participants": {},
        "npcs": {}, 
        "settings": {
            "response_mode": "auto",
            "active_genres": ["noir"], 
            "custom_tone": None,
            "session_locked": False,
            "bot_disabled": False,
            "is_prepared": False
        },
        "world_state": {
            "day": 1, 
            "time_slot": "오전", 
            "weather": "맑음", 
            "doom": 0,
            "current_location": "Unknown",
            "current_risk_level": "None",
            "location_rules": {}
        },
        "quest_board": {"active": [], "memo": [], "archive": [], "lore": [], "last_export_time": 0.0}
    }

def get_domain(channel_id):
    path = get_session_file_path(channel_id)
    if os.path.exists(path):
        try:
            with open(path, 'r', encoding='utf-8') as f:
                data = json.load(f)
                if "settings" not in data: data["settings"] = _create_new_domain()["settings"]
                if "active_genres" not in data["settings"]: data["settings"]["active_genres"] = ["noir"]
                if "custom_tone" not in data["settings"]: data["settings"]["custom_tone"] = None
                
                if "world_state" not in data: data["world_state"] = _create_new_domain()["world_state"]
                if "current_location" not in data["world_state"]: data["world_state"]["current_location"] = "Unknown"
                if "current_risk_level" not in data["world_state"]: data["world_state"]["current_risk_level"] = "None"
                if "location_rules" not in data["world_state"]: data["world_state"]["location_rules"] = {}

                if "quest_board" not in data: data["quest_board"] = _create_new_domain()["quest_board"]
                elif "lore" not in data["quest_board"]: data["quest_board"]["lore"] = []
                elif "last_export_time" not in data["quest_board"]: data["quest_board"]["last_export_time"] = 0.0
                if "npcs" not in data: data["npcs"] = {}
                return data
        except Exception as e:
            logging.error(f"로드 실패 ({channel_id}): {e}")
            return _create_new_domain()
    return _create_new_domain()

def save_domain(channel_id, data):
    try:
        with open(get_session_file_path(channel_id), 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e: logging.error(f"저장 실패: {e}")

def update_participant(channel_id, user, is_new_char=False):
    d = get_domain(channel_id); uid = str(user.id)
    if is_new_char and uid in d["participants"]:
        old_mask = d["participants"][uid].get("mask", "Unknown")
        record_historical_event(channel_id, f"운명의 수레바퀴가 돌며, 새로운 영웅이 등장했습니다. (이전 캐릭터: {old_mask} -> 은퇴/사망)")
        del d["participants"][uid]
    if uid in d["participants"]:
        p = d["participants"][uid]; p["name"] = user.display_name
        if p.get("status") in ["afk", "left"]: p["status"] = "active"
        save_domain(channel_id, d); return True
    if d["settings"].get("session_locked", False) and not is_new_char: return False
    d["participants"][uid] = {
        "name": user.display_name, "mask": user.display_name, "level": 1, "xp": 0, "next_xp": 100, 
        "stats": {"근력":10,"지능":10,"매력":10,"스트레스":0}, "relations":{}, "inventory":{}, "status_effects": [], "description": "", "status":"active"
    }
    save_domain(channel_id, d); return True

def set_participant_status(channel_id, uid, status, reason=None):
    d = get_domain(channel_id); uid = str(uid)
    if uid in d["participants"]:
        p = d["participants"][uid]; p["status"] = status; mask = p["mask"]
        if status == "left" and reason: record_historical_event(channel_id, f"{mask}님이 {reason}(으)로 인해 대열을 이탈했습니다.")
        save_domain(channel_id, d); return mask
    return None

def leave_participant(channel_id, uid): return set_participant_status(channel_id, uid, "left", "자발적 휴식")
def get_participant_status(channel_id, uid):
    p = get_domain(channel_id)["participants"].get(str(uid))
    return p.get("status", "unknown") if p else "unknown"

def get_participant_data(channel_id, uid):
    return get_domain(channel_id)["participants"].get(str(uid), None)

def get_active_participants_summary(channel_id):
    d = get_domain(channel_id); summary_parts = []
    for p in d["participants"].values():
        if p.get("status") == "left": continue
        status_tag = " [잠수 중]" if p.get("status") == "afk" else ""
        summary_parts.append(f"{p['mask']}{status_tag}")
    return ", ".join(summary_parts) if summary_parts else "None"

def get_party_status_context(channel_id):
    d = get_domain(channel_id); parts = []
    for uid, p in d["participants"].items():
        if p.get("status") == "left": continue
        mask = p.get("mask", "Unknown"); stats = p.get("stats", {}); stress = stats.get("스트레스", 0)
        effects = p.get("status_effects", []); relations = p.get("relations", {})
        status_str = f"{mask}(Stress: {stress})"
        if effects: status_str += f" [Effects: {', '.join(effects)}]"
        if relations: rel_str = ", ".join([f"{npc}:{val:+}" for npc, val in relations.items()]); status_str += f" [Rel: {rel_str}]"
        parts.append(status_str)
    return " | ".join(parts) if parts else "No active party members."

def get_user_mask(channel_id, uid):
    p = get_domain(channel_id)["participants"].get(str(uid))
    return p.get("mask", "Unknown") if p else "Unknown"
def set_user_mask(channel_id, uid, mask):
    d = get_domain(channel_id); uid = str(uid)
    if uid in d["participants"]: d["participants"][uid]["mask"] = mask; save_domain(channel_id, d)
def get_user_description(channel_id, uid):
    return get_domain(channel_id)["participants"].get(str(uid), {}).get("description", "")
def set_user_description(channel_id, uid, desc):
    d = get_domain(channel_id); uid = str(uid)
    if uid in d["participants"]:
        if desc.strip() in ["초기화", "reset", "clear"]: d["participants"][uid]["description"] = ""
        else:
            current_desc = d["participants"][uid].get("description", "")
            if current_desc: d["participants"][uid]["description"] = f"{current_desc}\\n{desc}".strip()
            else: d["participants"][uid]["description"] = desc.strip()
        save_domain(channel_id, d)

# NPC 관련
def update_npc(channel_id, name, data):
    d = get_domain(channel_id); d["npcs"][name] = data; save_domain(channel_id, d)
def get_npcs(channel_id): return get_domain(channel_id).get("npcs", {})
def remove_npc(channel_id, name):
    d = get_domain(channel_id)
    if name in d["npcs"]: del d["npcs"][name]; save_domain(channel_id, d)

# 장소 및 규칙 관련
def set_current_location(channel_id, loc_name):
    d = get_domain(channel_id)
    if d["world_state"].get("current_location") != loc_name:
        d["world_state"]["current_location"] = loc_name
        save_domain(channel_id, d)

def get_current_location(channel_id):
    return get_domain(channel_id)["world_state"].get("current_location", "Unknown")

def set_current_risk(channel_id, risk_level):
    d = get_domain(channel_id)
    d["world_state"]["current_risk_level"] = risk_level
    save_domain(channel_id, d)

def get_current_risk(channel_id):
    return get_domain(channel_id)["world_state"].get("current_risk_level", "None")

def set_location_rules(channel_id, rules):
    d = get_domain(channel_id); d["world_state"]["location_rules"] = rules; save_domain(channel_id, d)
def get_location_rules(channel_id):
    return get_domain(channel_id)["world_state"].get("location_rules", {})

def get_quest_board(channel_id): return get_domain(channel_id).get("quest_board")
def update_quest_board(channel_id, new_board): d = get_domain(channel_id); d["quest_board"] = new_board; save_domain(channel_id, d)
def get_world_state(channel_id): return get_domain(channel_id).get("world_state")
def update_world_state(channel_id, new_state): d = get_domain(channel_id); d["world_state"] = new_state; save_domain(channel_id, d)

def is_prepared(channel_id): return get_domain(channel_id)["settings"].get("is_prepared", False)
def set_prepared(channel_id, status: bool): d = get_domain(channel_id); d["settings"]["is_prepared"] = status; save_domain(channel_id, d)
def is_bot_disabled(channel_id): return get_domain(channel_id)["settings"].get("bot_disabled", False)
def set_bot_disabled(channel_id, disabled: bool): d = get_domain(channel_id); d["settings"]["bot_disabled"] = disabled; save_domain(channel_id, d)
def set_session_lock(channel_id, status: bool): d = get_domain(channel_id); d["settings"]["session_locked"] = status; save_domain(channel_id, d)
def set_response_mode(channel_id, mode): d = get_domain(channel_id); d["settings"]["response_mode"] = mode; save_domain(channel_id, d)
def get_response_mode(channel_id): return get_domain(channel_id)["settings"].get("response_mode", "auto")

def set_active_genres(channel_id, genres):
    d = get_domain(channel_id); d["settings"]["active_genres"] = genres; save_domain(channel_id, d)
def get_active_genres(channel_id):
    return get_domain(channel_id)["settings"].get("active_genres", ["noir"])
def set_custom_tone(channel_id, tone_text):
    d = get_domain(channel_id); d["settings"]["custom_tone"] = tone_text; save_domain(channel_id, d)
def get_custom_tone(channel_id):
    return get_domain(channel_id)["settings"].get("custom_tone", None)

def get_lore(channel_id):
    path = get_lore_file_path(channel_id)
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f: return f.read().strip()
    return DEFAULT_LORE
def append_lore(channel_id, text):
    path = get_lore_file_path(channel_id)
    cur = get_lore(channel_id) if os.path.exists(path) else ""
    with open(path, 'w', encoding='utf-8') as f: f.write(f"{cur}\\n{text}".strip())
def reset_lore(channel_id):
    path = get_lore_file_path(channel_id)
    if os.path.exists(path): os.remove(path)

def get_rules(channel_id):
    path = get_rules_file_path(channel_id)
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f: return f.read().strip()
    return DEFAULT_RULES
def append_rules(channel_id, text):
    path = get_rules_file_path(channel_id)
    cur = get_rules(channel_id) if os.path.exists(path) else ""
    with open(path, 'w', encoding='utf-8') as f: f.write(f"{cur}\\n{text}".strip())
def reset_rules(channel_id):
    path = get_rules_file_path(channel_id)
    if os.path.exists(path): os.remove(path)

def append_history(channel_id, role, content):
    d = get_domain(channel_id); d['history'].append({"role": role, "content": content})
    if len(d['history']) > 40: d['history'] = d['history'][-40:]
    save_domain(channel_id, d)

def reset_domain(channel_id):
    for f in [get_session_file_path(channel_id), get_lore_file_path(channel_id), get_rules_file_path(channel_id)]:
        if os.path.exists(f): os.remove(f)