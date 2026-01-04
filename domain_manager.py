import os
import json
import logging

# =========================================================
# 파일 저장 경로 설정
# =========================================================
DATA_DIR = "data"
SESSIONS_DIR = os.path.join(DATA_DIR, "sessions") # 세션 정보 (JSON)
LORE_DIR = os.path.join(DATA_DIR, "lores")       # 세계관 (TXT)
RULES_DIR = os.path.join(DATA_DIR, "rules")      # 룰북 (TXT)

# =========================================================
# [클래스] 세션 매니저 (메모리 버퍼 - 대화 턴 관리용)
# =========================================================
class SessionManager:
    def __init__(self):
        self.sessions = {} 

    def _ensure(self, cid):
        if cid not in self.sessions:
            self.sessions[cid] = {"buffer": [], "acted": set()}

    def add_message(self, cid, user_mask, text):
        self._ensure(cid)
        self.sessions[cid]["buffer"].append(f"[{user_mask}]: {text}")

    def get_and_clear_buffer(self, cid):
        self._ensure(cid)
        if not self.sessions[cid]["buffer"]:
            return None
        res = "\n".join(self.sessions[cid]["buffer"])
        self.sessions[cid]["buffer"] = []
        return res
    
    def has_buffer(self, cid):
        self._ensure(cid)
        return len(self.sessions[cid]["buffer"]) > 0

turn_manager = SessionManager()

# =========================================================
# [함수] 파일 시스템 관리
# =========================================================
def initialize_folders():
    """필요한 데이터 폴더들을 모두 생성합니다."""
    for path in [SESSIONS_DIR, LORE_DIR, RULES_DIR]:
        if not os.path.exists(path):
            try:
                os.makedirs(path)
            except Exception as e:
                logging.error(f"폴더 생성 실패: {e}")

def get_session_file_path(channel_id):
    return os.path.join(SESSIONS_DIR, f"{channel_id}.json")

def get_lore_file_path(channel_id):
    return os.path.join(LORE_DIR, f"lore_{channel_id}.txt")

def get_rules_file_path(channel_id):
    return os.path.join(RULES_DIR, f"rules_{channel_id}.txt")

def get_domain(channel_id):
    """세션 JSON 데이터를 로드합니다 (로어/룰 텍스트는 제외)."""
    file_path = get_session_file_path(channel_id)
    if os.path.exists(file_path):
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
                defaults = _create_new_domain()
                # 새 버전에서 추가된 키가 없으면 기본값으로 채움
                for key, val in defaults.items():
                    if key not in data:
                        data[key] = val
                return data
        except Exception as e:
            logging.error(f"세션 로드 실패 ({channel_id}): {e}")
            return _create_new_domain()
    else:
        return _create_new_domain()

def _create_new_domain():
    return {
        "history": [],        
        "participants": {},   
        "settings": {
            "mode": "auto",              
            "growth_system": "standard", 
            "session_locked": False,
            "bot_disabled": False  # [신규] 해당 채널에서 봇 비활성화 여부
        },
        "fief": {             
            "name": "시작의 영지", "gold": 1000, "supplies": 100, 
            "population": 50, "buildings": [], "security": 60
        },
        "ai_notes": {         
            "observations": [], 
            "requests": []
        },
        "world_state": {
            "day": 1,
            "time_slot": "오전",
            "weather": "맑음",
            "doom": 0,
            "doom_name": "평온함"
        },
        "quest_board": {
            "active": [],
            "memo": []
        }
    }

def save_domain(channel_id, data):
# --- [신규] 봇 활성화/비활성화 설정 ---
def set_bot_disabled(channel_id, disabled):
    """
    해당 채널에서 봇의 응답을 활성화(False)하거나 비활성화(True)합니다.
    """
    d = get_domain(channel_id)
    d["settings"]["bot_disabled"] = disabled
    save_domain(channel_id, d)
    return disabled

def is_bot_disabled(channel_id):
    """
    해당 채널이 비활성화 상태인지 확인합니다.
    """
    return get_domain(channel_id)["settings"].get("bot_disabled", False)

        with open(get_session_file_path(channel_id), 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logging.error(f"세션 저장 실패: {e}")

# =========================================================
# [함수] 데이터 조작
# =========================================================

# --- 로어/룰북 (TXT) ---
def set_lore(channel_id, text):
    try:
        with open(get_lore_file_path(channel_id), 'w', encoding='utf-8') as f:
            f.write(text)
    except Exception as e: logging.error(f"로어 저장 실패: {e}")

def append_lore(channel_id, text):
    try:
        with open(get_lore_file_path(channel_id), 'a', encoding='utf-8') as f:
            f.write("\n" + text)
    except Exception as e: set_lore(channel_id, text)

def get_lore(channel_id):
    path = get_lore_file_path(channel_id)
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f: return f.read()
        except: return "Error reading lore."
    return "Dark Fantasy World: A grim realm where survival is the only virtue."

def set_rules(channel_id, text):
    try:
        with open(get_rules_file_path(channel_id), 'w', encoding='utf-8') as f:
            f.write(text)
    except Exception as e: logging.error(f"룰북 저장 실패: {e}")

def append_rules(channel_id, text):
    try:
        with open(get_rules_file_path(channel_id), 'a', encoding='utf-8') as f:
            f.write("\n" + text)
    except Exception as e: set_rules(channel_id, text)

def get_rules(channel_id):
    path = get_rules_file_path(channel_id)
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f: return f.read()
        except: return "Error reading rules."
    return "Basic TRPG Rules: D20 system, Success check."

# --- 시스템 설정 ---
def set_growth_system(channel_id, system_name):
    d = get_domain(channel_id); d["settings"]["growth_system"] = system_name; save_domain(channel_id, d)
def get_growth_system(channel_id): return get_domain(channel_id)["settings"].get("growth_system", "standard")
def toggle_session_lock(channel_id):
    d = get_domain(channel_id); cur = d["settings"].get("session_locked", False)
    d["settings"]["session_locked"] = not cur; save_domain(channel_id, d); return d["settings"]["session_locked"]
def is_session_locked(channel_id): return get_domain(channel_id)["settings"].get("session_locked", False)

# --- 참가자 관리 ---
def update_participant(channel_id, user):
    d = get_domain(channel_id); uid = str(user.id)
    
    if uid in d["participants"]:
        p = d["participants"][uid]
        p["count"] += 1; p["name"] = user.display_name
        if p.get("status") == "away": p["status"] = "active"
        if "inventory" not in p: p["inventory"] = {}
        # [신규] 상태이상 리스트 추가
        if "status_effects" not in p: p["status_effects"] = []
        save_domain(channel_id, d); return True

    if d["settings"].get("session_locked", False): return False
    
    d["participants"][uid] = {
        "name": user.display_name, "mask": user.display_name, "count": 1, 
        "level": 1, "xp": 0, "next_xp": 100, 
        "stats": {"근력":10,"지능":10,"매력":10,"스트레스":0}, 
        "relations":{}, 
        "inventory":{}, 
        "status_effects": [], # [신규] 상태이상 리스트
        "status":"active"
    }
    save_domain(channel_id, d); return True

def leave_participant(channel_id, uid):
    d = get_domain(channel_id); uid = str(uid)
    if uid in d["participants"]: d["participants"][uid]["status"] = "away"; save_domain(channel_id, d); return d["participants"][uid]["name"], d["participants"][uid]["mask"]
    return None, None

def join_participant(channel_id, uid, user_name):
    d = get_domain(channel_id); uid = str(uid)
    if uid in d["participants"]:
        d["participants"][uid]["status"] = "active"; d["participants"][uid]["name"] = user_name
        if "inventory" not in d["participants"][uid]: d["participants"][uid]["inventory"] = {}
        if "status_effects" not in d["participants"][uid]: d["participants"][uid]["status_effects"] = []
        save_domain(channel_id, d); return d["participants"][uid]["mask"], False
    else:
        d["participants"][uid] = {
            "name": user_name, "mask": user_name, "count": 0, "level": 1, "xp": 0, "next_xp": 100,
            "stats": {"근력":10,"지능":10,"매력":10,"스트레스":0}, "relations":{}, "inventory":{}, "status_effects": [], "status":"active"
        }
        save_domain(channel_id, d); return user_name, True

def remove_participant(channel_id, uid):
    d = get_domain(channel_id); uid = str(uid)
    if uid in d["participants"]:
        data = d["participants"][uid]; del d["participants"][uid]; save_domain(channel_id, d); return data["name"], data["mask"]
    return None, None

def set_user_mask(channel_id, uid, mask):
    d = get_domain(channel_id); uid = str(uid)
    if uid not in d["participants"]: return
    d["participants"][uid]["mask"] = mask; save_domain(channel_id, d)

def get_user_mask(channel_id, uid):
    d = get_domain(channel_id); p = d["participants"].get(str(uid))
    return p.get("mask", "Unknown") if p else "Unknown"

def get_active_participants_summary(channel_id):
    """
    [수정] 참가자 목록을 반환할 때 상태이상 정보도 같이 반환
    """
    d = get_domain(channel_id)
    active = []
    for p in d["participants"].values():
        if p.get("status") == "active":
            info = f"{p['mask']}"
            # 상태이상이 있다면 표시 (예: 아서[중독, 출혈])
            effects = p.get("status_effects", [])
            if effects:
                info += f"[{', '.join(effects)}]"
            active.append(info)
    return ", ".join(active)

# --- [신규] 월드 로직용 파티 정보 요약 ---
def get_party_status_context(channel_id):
    """
    월드 매니저가 분위기를 파악하기 위해 파티 전체의 상태와 관계도를 요약합니다.
    """
    d = get_domain(channel_id)
    participants = [p for p in d["participants"].values() if p.get("status") == "active"]
    
    if not participants: return "No active players."

    # 1. 상태이상 요약
    all_effects = []
    for p in participants:
        if p.get("status_effects"):
            all_effects.extend(p["status_effects"])
    
    status_summary = "Healthy"
    if all_effects:
        status_summary = f"Suffering from {', '.join(set(all_effects))}"

    # 2. 관계도 요약 (평판)
    # 모든 플레이어의 관계도를 합쳐서 주요 NPC/세력과의 관계를 도출
    all_relations = {}
    for p in participants:
        for npc, val in p.get("relations", {}).items():
            all_relations[npc] = all_relations.get(npc, 0) + val
    
    relation_summary = "Neutral"
    notable_relations = []
    for npc, score in all_relations.items():
        if score >= 10: notable_relations.append(f"{npc}(Friendly)")
        elif score <= -10: notable_relations.append(f"{npc}(Hostile)")
    
    if notable_relations:
        relation_summary = ", ".join(notable_relations)

    return f"Party Condition: {status_summary} | Reputation: {relation_summary}"

# --- 유저/영지 데이터 ---
def set_user_stat(cid, uid, key, val):
    d = get_domain(cid); u = str(uid)
    if u in d["participants"]: 
        try: v = int(val)
        except: v = val
        d["participants"][u]["stats"][key] = v; save_domain(cid, d); return v
    return None
def set_user_level(cid, uid, val):
    d = get_domain(cid); u = str(uid)
    if u in d["participants"]:
        try: v = int(val)
        except: v = val
        d["participants"][u]["level"] = v; save_domain(cid, d); return v
    return None
def get_fief_data(cid): return get_domain(cid).get("fief")
def update_fief_data(cid, data): d = get_domain(cid); d["fief"] = data; save_domain(cid, d)
def get_user_data(cid, uid): return get_domain(cid)["participants"].get(str(uid))
def update_user_data(cid, uid, data): d = get_domain(cid); d["participants"][str(uid)] = data; save_domain(cid, d)

# --- AI 노트/모드 ---
def get_ai_notes(cid): return get_domain(cid).get("ai_notes", {"observations": [], "requests": []})
def update_ai_notes(cid, obs=None, req=None):
    d = get_domain(cid); notes = d.get("ai_notes", {"observations": [], "requests": []})
    if obs: notes["observations"] = (notes["observations"] + [obs])[-20:]
    if req: notes["requests"] = (notes["requests"] + [req])[-5:]
    d["ai_notes"] = notes; save_domain(cid, d)
def toggle_mode(cid):
    d = get_domain(cid); new = "manual" if d["settings"].get("mode") == "auto" else "auto"
    d["settings"]["mode"] = new; save_domain(cid, d); return new
def get_mode(cid): return get_domain(cid)["settings"].get("mode", "auto")
def add_to_buffer(cid, uid, text): turn_manager.add_message(cid, get_user_mask(cid, uid), text)
def flush_buffer(cid): return turn_manager.get_and_clear_buffer(cid)
def append_history(cid, role, content):
    d = get_domain(cid); d['history'].append({"role": role, "content": content})
    if len(d['history']) > 60: d['history'] = d['history'][-60:]
    save_domain(cid, d)

# --- World & Quest Getter/Setter ---
def get_world_state(cid):
    return get_domain(cid).get("world_state")

def update_world_state(cid, new_state):
    d = get_domain(cid)
    d["world_state"] = new_state
    save_domain(cid, d)

def get_quest_board(cid):
    return get_domain(cid).get("quest_board")

def update_quest_board(cid, new_board):
    d = get_domain(cid)
    d["quest_board"] = new_board
    save_domain(cid, d)

def reset_domain(cid):
    path = get_session_file_path(cid)
    if os.path.exists(path):
        try: os.remove(path)
        except: pass
    try: os.remove(get_lore_file_path(cid))
    except: pass
    try: os.remove(get_rules_file_path(cid))
    except: pass
    if cid in turn_manager.sessions: del turn_manager.sessions[cid]