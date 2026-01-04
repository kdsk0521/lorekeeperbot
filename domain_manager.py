import os
import json
import logging

# =========================================================
# 기본값 및 경로 설정
# =========================================================
DEFAULT_LORE = "[장르: 잔혹한 다크 판타지 느와르]"
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
            os.makedirs(path)

def get_session_file_path(channel_id):
    return os.path.join(SESSIONS_DIR, f"{channel_id}.json")

def get_lore_file_path(channel_id):
    return os.path.join(LORE_DIR, f"lore_{channel_id}.txt")

def get_rules_file_path(channel_id):
    return os.path.join(RULES_DIR, f"rules_{channel_id}.txt")

# =========================================================
# [핵심] 역사 기록 시스템 (AI의 장기 기억)
# =========================================================
def record_historical_event(channel_id, event_text):
    """중요 사건을 영구 로어 파일(.txt)에 기록하여 AI의 장기 기억으로 활용합니다."""
    domain = get_domain(channel_id)
    world = domain.get("world_state", {})
    day = world.get("day", 1)
    time_slot = world.get("time_slot", "알 수 없음")
    
    formatted_log = f"\n[역사적 기록 - 제 {day}일 {time_slot}] {event_text}"
    append_lore(channel_id, formatted_log)
    logging.info(f"Event recorded: {event_text}")

# =========================================================
# 데이터 관리 (Load / Save)
# =========================================================
def _create_new_domain():
    """새로운 채널을 위한 기본 데이터 구조를 생성합니다."""
    return {
        "history": [],        
        "participants": {},   
        "settings": {
            "mode": "auto",              
            "growth_system": "standard", 
            "session_locked": False,
            "bot_disabled": False,
            "is_prepared": False
        },
        "world_state": {
            "day": 1, "time_slot": "오전", "weather": "맑음", "doom": 0
        },
        "quest_board": {
            "active": [], 
            "memo": [], 
            "archive": [],
            "lore": []  # AI 분석을 통해 박제된 기록 (JSON 데이터)
        }
    }

def get_domain(channel_id):
    """채널의 세션 데이터를 로드하고 데이터 무결성을 검사합니다."""
    path = get_session_file_path(channel_id)
    if os.path.exists(path):
        try:
            with open(path, 'r', encoding='utf-8') as f:
                data = json.load(f)
                # 데이터 구조 마이그레이션 (필수 키 확인 및 업데이트)
                if "settings" not in data: data["settings"] = _create_new_domain()["settings"]
                if "world_state" not in data: data["world_state"] = _create_new_domain()["world_state"]
                if "quest_board" not in data: 
                    data["quest_board"] = _create_new_domain()["quest_board"]
                elif "lore" not in data["quest_board"]:
                    data["quest_board"]["lore"] = []
                return data
        except Exception as e:
            logging.error(f"로드 실패 ({channel_id}): {e}")
            return _create_new_domain()
    return _create_new_domain()

def save_domain(channel_id, data):
    """데이터를 JSON 파일로 저장합니다."""
    try:
        with open(get_session_file_path(channel_id), 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logging.error(f"저장 실패: {e}")

# =========================================================
# 참가자 및 상태 관리
# =========================================================
def update_participant(channel_id, user, is_new_char=False):
    """참가자의 정보를 업데이트하거나 새로 등록합니다."""
    d = get_domain(channel_id)
    uid = str(user.id)
    
    if is_new_char and uid in d["participants"]:
        old_mask = d["participants"][uid].get("mask", "Unknown")
        record_historical_event(channel_id, f"새로운 운명이 시작되었습니다. (이전 캐릭터: {old_mask})")
        del d["participants"][uid]

    if uid in d["participants"]:
        p = d["participants"][uid]
        p["name"] = user.display_name
        # 잠수나 이탈 상태였다면 자동으로 활동 상태로 복귀
        if p.get("status") in ["afk", "left"]:
            p["status"] = "active"
        save_domain(channel_id, d)
        return True
    
    # 세션이 잠겨있으면 신규 참가 불가
    if d["settings"].get("session_locked", False): return False
    
    # 신규 참가자 초기화
    d["participants"][uid] = {
        "name": user.display_name, "mask": user.display_name,
        "level": 1, "xp": 0, "next_xp": 100, "stats": {"근력":10,"지능":10,"매력":10,"스트레스":0},
        "relations":{}, "inventory":{}, "status_effects": [], "description": "", "status":"active"
    }
    save_domain(channel_id, d)
    return True

def set_participant_status(channel_id, uid, status, reason=None):
    """참가자의 상태(afk, active, left)를 설정합니다."""
    d = get_domain(channel_id)
    uid = str(uid)
    if uid in d["participants"]:
        p = d["participants"][uid]
        p["status"] = status
        mask = p["mask"]
        if status == "left" and reason:
            record_historical_event(channel_id, f"{mask}님이 {reason}(으)로 인해 대열을 이탈했습니다.")
        save_domain(channel_id, d)
        return mask
    return None

def leave_participant(channel_id, uid):
    """참가자를 이탈 상태(left)로 설정합니다."""
    return set_participant_status(channel_id, uid, "left", "자발적 휴식")

def get_active_participants_summary(channel_id):
    """AI 서사 주입용 요약. 이탈자는 제외, 잠수자는 태그 표시."""
    d = get_domain(channel_id)
    summary_parts = []
    for p in d["participants"].values():
        if p.get("status") == "left": continue
        status_tag = " [잠수 중]" if p.get("status") == "afk" else ""
        summary_parts.append(f"{p['mask']}{status_tag}")
    return ", ".join(summary_parts) if summary_parts else "None"

# =========================================================
# 데이터 접근자 (Getters & Setters)
# =========================================================
def get_user_mask(channel_id, uid):
    p = get_domain(channel_id)["participants"].get(str(uid))
    return p.get("mask", "Unknown") if p else "Unknown"

def set_user_mask(channel_id, uid, mask):
    d = get_domain(channel_id); uid = str(uid)
    if uid in d["participants"]: d["participants"][uid]["mask"] = mask; save_domain(channel_id, d)

def get_user_description(channel_id, uid):
    return get_domain(channel_id)["participants"].get(str(uid), {}).get("description", "")

def set_user_description(channel_id, uid, desc):
    """설명을 업데이트합니다. '초기화' 입력 시 내용을 지우고, 아닐 시 덧붙입니다."""
    d = get_domain(channel_id); uid = str(uid)
    if uid in d["participants"]:
        if desc.strip() in ["초기화", "reset", "clear"]:
            d["participants"][uid]["description"] = ""
        else:
            current_desc = d["participants"][uid].get("description", "")
            if current_desc:
                d["participants"][uid]["description"] = f"{current_desc}\n{desc}".strip()
            else:
                d["participants"][uid]["description"] = desc.strip()
        save_domain(channel_id, d)

def get_quest_board(channel_id):
    return get_domain(channel_id).get("quest_board")

def update_quest_board(channel_id, new_board):
    d = get_domain(channel_id); d["quest_board"] = new_board; save_domain(channel_id, d)

def get_world_state(channel_id):
    return get_domain(channel_id).get("world_state")

def update_world_state(channel_id, new_state):
    d = get_domain(channel_id); d["world_state"] = new_state; save_domain(channel_id, d)

# =========================================================
# 설정 관리
# =========================================================
def is_prepared(channel_id):
    return get_domain(channel_id)["settings"].get("is_prepared", False)

def set_prepared(channel_id, status: bool):
    d = get_domain(channel_id); d["settings"]["is_prepared"] = status; save_domain(channel_id, d)

def is_bot_disabled(channel_id):
    return get_domain(channel_id)["settings"].get("bot_disabled", False)

def set_bot_disabled(channel_id, disabled: bool):
    d = get_domain(channel_id); d["settings"]["bot_disabled"] = disabled; save_domain(channel_id, d)

def set_session_lock(channel_id, status: bool):
    d = get_domain(channel_id); d["settings"]["session_locked"] = status; save_domain(channel_id, d)

# =========================================================
# 로어 및 룰 관리 (파일 기반 영구 저장)
# =========================================================
def get_lore(channel_id):
    path = get_lore_file_path(channel_id)
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            return f.read().strip()
    return DEFAULT_LORE

def append_lore(channel_id, text):
    path = get_lore_file_path(channel_id)
    cur = get_lore(channel_id) if os.path.exists(path) else ""
    with open(path, 'w', encoding='utf-8') as f:
        f.write(f"{cur}\n{text}".strip())

def get_rules(channel_id):
    path = get_rules_file_path(channel_id)
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            return f.read().strip()
    return DEFAULT_RULES

def append_rules(channel_id, text):
    path = get_rules_file_path(channel_id)
    cur = get_rules(channel_id) if os.path.exists(path) else ""
    with open(path, 'w', encoding='utf-8') as f:
        f.write(f"{cur}\n{text}".strip())

def append_history(channel_id, role, content):
    """대화 기록을 세션 데이터에 추가합니다 (최근 40개 유지)."""
    d = get_domain(channel_id)
    d['history'].append({"role": role, "content": content})
    if len(d['history']) > 40:
        d['history'] = d['history'][-40:]
    save_domain(channel_id, d)

def reset_domain(channel_id):
    """모든 채널 데이터 및 파일을 삭제하여 초기화합니다."""
    for f in [get_session_file_path(channel_id), get_lore_file_path(channel_id), get_rules_file_path(channel_id)]:
        if os.path.exists(f):
            try:
                os.remove(f)
            except Exception as e:
                logging.error(f"파일 삭제 실패 ({f}): {e}")