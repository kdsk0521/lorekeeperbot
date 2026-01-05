import os
import json
import logging
import time

# =========================================================
# 기본값 및 경로 설정
# =========================================================
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
    """봇 실행에 필요한 데이터 폴더들을 초기화합니다."""
    for path in [SESSIONS_DIR, LORE_DIR, RULES_DIR]:
        if not os.path.exists(path):
            try:
                os.makedirs(path)
            except Exception as e:
                logging.error(f"폴더 생성 실패 {path}: {e}")

def get_session_file_path(channel_id):
    return os.path.join(SESSIONS_DIR, f"{channel_id}.json")

def get_lore_file_path(channel_id):
    return os.path.join(LORE_DIR, f"lore_{channel_id}.txt")

def get_rules_file_path(channel_id):
    return os.path.join(RULES_DIR, f"rules_{channel_id}.txt")

# =========================================================
# 역사 기록 시스템 (AI의 장기 기억)
# =========================================================
def record_historical_event(channel_id, event_text):
    domain = get_domain(channel_id)
    world = domain.get("world_state", {})
    day = world.get("day", 1)
    time_slot = world.get("time_slot", "알 수 없음")
    formatted_log = f"\n[역사적 기록 - 제 {day}일 {time_slot}] {event_text}"
    append_lore(channel_id, formatted_log)

# =========================================================
# 데이터 관리 (Load / Save)
# =========================================================
def _create_new_domain():
    return {
        "history": [],        
        "participants": {},
        "npcs": {}, 
        "settings": {
            "response_mode": "auto",
            "active_genres": ["noir"], 
            "custom_tone": None,
            "growth_system": "standard", # 성장 시스템 저장 공간
            "session_locked": False,
            "bot_disabled": False,
            "is_prepared": False
        },
        "world_state": {
            "day": 1, "time_slot": "오전", "weather": "맑음", "doom": 0,
            "current_location": "Unknown", "current_risk_level": "None", "location_rules": {}
        },
        "quest_board": {"active": [], "memo": [], "archive": [], "lore": [], "last_export_time": 0.0}
    }

def get_domain(channel_id):
    path = get_session_file_path(channel_id)
    if os.path.exists(path):
        try:
            with open(path, 'r', encoding='utf-8') as f:
                data = json.load(f)
                # 데이터 마이그레이션 (누락된 필드 자동 추가)
                if "settings" not in data: data["settings"] = _create_new_domain()["settings"]
                if "growth_system" not in data["settings"]: data["settings"]["growth_system"] = "standard"
                if "active_genres" not in data["settings"]: data["settings"]["active_genres"] = ["noir"]
                if "world_state" not in data: data["world_state"] = _create_new_domain()["world_state"]
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

# =========================================================
# 참가자 데이터 관리
# =========================================================
def update_participant(channel_id, user, is_new_char=False):
    d = get_domain(channel_id); uid = str(user.id)
    if is_new_char and uid in d["participants"]:
        old_mask = d["participants"][uid].get("mask", "Unknown")
        record_historical_event(channel_id, f"운명의 수레바퀴가 돌며, 새로운 영웅이 등장했습니다. (이전 캐릭터: {old_mask})")
        del d["participants"][uid]
    if uid in d["participants"]:
        d["participants"][uid]["name"] = user.display_name
        if d["participants"][uid].get("status") in ["afk", "left"]: d["participants"][uid]["status"] = "active"
        save_domain(channel_id, d); return True
    if d["settings"].get("session_locked", False) and not is_new_char: return False
    d["participants"][uid] = {
        "name": user.display_name, "mask": user.display_name, "level": 1, "xp": 0, "next_xp": 100, 
        "stats": {"근력":10,"지능":10,"매력":10,"스트레스":0}, "relations":{}, "inventory":{}, "status_effects": [], "description": "", "status":"active"
    }
    save_domain(channel_id, d); return True

def save_participant_data(channel_id, uid, data):
    """[수정] XP 획득 시 참가자 데이터를 덮어씌우는 필수 함수"""
    d = get_domain(channel_id)
    d["participants"][str(uid)] = data
    save_domain(channel_id, d)

def get_participant_data(channel_id, uid):
    return get_domain(channel_id)["participants"].get(str(uid), None)

def set_participant_status(channel_id, uid, status, reason=None):
    d = get_domain(channel_id); uid = str(uid)
    if uid in d["participants"]:
        p = d["participants"][uid]; p["status"] = status
        if status == "left" and reason: record_historical_event(channel_id, f"{p['mask']}님이 이탈했습니다.")
        save_domain(channel_id, d); return p["mask"]
    return None

def get_participant_status(channel_id, uid):
    p = get_domain(channel_id)["participants"].get(str(uid))
    return p.get("status", "unknown") if p else "unknown"

# =========================================================
# 설정 관련 Getters/Setters (에러 원인 해결 부분)
# =========================================================
def set_growth_system(channel_id, system):
    """[추가] !시스템 성장 명령어를 위한 함수"""
    d = get_domain(channel_id)
    d["settings"]["growth_system"] = system
    save_domain(channel_id, d)

def get_growth_system(channel_id):
    """[추가] 현재 설정된 성장 시스템을 확인하는 함수"""
    return get_domain(channel_id)["settings"].get("growth_system", "standard")

def set_response_mode(channel_id, mode):
    d = get_domain(channel_id); d["settings"]["response_mode"] = mode; save_domain(channel_id, d)

def get_response_mode(channel_id):
    return get_domain(channel_id)["settings"].get("response_mode", "auto")

# 장르 및 장소
def set_active_genres(channel_id, genres):
    d = get_domain(channel_id); d["settings"]["active_genres"] = genres; save_domain(channel_id, d)
def get_active_genres(channel_id): return get_domain(channel_id)["settings"].get("active_genres", ["noir"])
def set_custom_tone(channel_id, tone):
    d = get_domain(channel_id); d["settings"]["custom_tone"] = tone; save_domain(channel_id, d)
def get_custom_tone(channel_id): return get_domain(channel_id)["settings"].get("custom_tone", None)

def set_current_location(channel_id, loc):
    d = get_domain(channel_id); d["world_state"]["current_location"] = loc; save_domain(channel_id, d)
def get_current_location(channel_id): return get_domain(channel_id)["world_state"].get("current_location", "Unknown")
def set_current_risk(channel_id, risk):
    d = get_domain(channel_id); d["world_state"]["current_risk_level"] = risk; save_domain(channel_id, d)
def get_current_risk(channel_id): return get_domain(channel_id)["world_state"].get("current_risk_level", "None")
def set_location_rules(channel_id, rules):
    d = get_domain(channel_id); d["world_state"]["location_rules"] = rules; save_domain(channel_id, d)

# 로어 및 룰 파일
def get_lore(channel_id):
    path = get_lore_file_path(channel_id)
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f: return f.read().strip()
    return DEFAULT_LORE

def append_lore(channel_id, text):
    path = get_lore_file_path(channel_id)
    cur = get_lore(channel_id) if os.path.exists(path) else ""
    with open(path, 'w', encoding='utf-8') as f: f.write(f"{cur}\n{text}".strip())

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
    with open(path, 'w', encoding='utf-8') as f: f.write(f"{cur}\n{text}".strip())

def reset_rules(channel_id):
    path = get_rules_file_path(channel_id)
    if os.path.exists(path): os.remove(path)

# 기타 유틸
def is_prepared(channel_id): return get_domain(channel_id)["settings"].get("is_prepared", False)
def set_prepared(channel_id, status): d = get_domain(channel_id); d["settings"]["is_prepared"] = status; save_domain(channel_id, d)
def is_bot_disabled(channel_id): return get_domain(channel_id)["settings"].get("bot_disabled", False)
def set_bot_disabled(channel_id, status): d = get_domain(channel_id); d["settings"]["bot_disabled"] = status; save_domain(channel_id, d)
def set_session_lock(channel_id, status): d = get_domain(channel_id); d["settings"]["session_locked"] = status; save_domain(channel_id, d)

def append_history(channel_id, role, content):
    d = get_domain(channel_id); d['history'].append({"role": role, "content": content})
    if len(d['history']) > 40: d['history'] = d['history'][-40:]
    save_domain(channel_id, d)

def reset_domain(channel_id):
    for f in [get_session_file_path(channel_id), get_lore_file_path(channel_id), get_rules_file_path(channel_id)]:
        if os.path.exists(f): os.remove(f)

# NPC 데이터
def update_npc(cid, name, data): d = get_domain(cid); d["npcs"][name] = data; save_domain(cid, d)
def get_npcs(cid): return get_domain(cid).get("npcs", {})

def get_quest_board(cid): return get_domain(cid).get("quest_board")
def update_quest_board(cid, b): d = get_domain(cid); d["quest_board"] = b; save_domain(cid, d)
def get_world_state(cid): return get_domain(cid).get("world_state")
def update_world_state(cid, s): d = get_domain(cid); d["world_state"] = s; save_domain(cid, d)
def get_user_mask(cid, uid): return get_domain(cid)["participants"].get(str(uid), {}).get("mask", "Unknown")
def set_user_mask(cid, uid, mask): d = get_domain(cid); uid=str(uid); d["participants"][uid]["mask"]=mask; save_domain(cid, d)
def set_user_description(cid, uid, desc):
    d = get_domain(cid); uid=str(uid)
    if uid in d["participants"]: d["participants"][uid]["description"] = desc; save_domain(cid, d)
def get_party_status_context(cid):
    d = get_domain(cid); parts = []
    for p in d["participants"].values():
        if p.get("status") == "left": continue
        parts.append(f"{p['mask']}(Stress: {p['stats'].get('스트레스',0)})")
    return " | ".join(parts)