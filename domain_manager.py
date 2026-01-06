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
LORE_SUMMARY_DIR = os.path.join(DATA_DIR, "lore_summaries") # 요약본 저장 경로
RULES_DIR = os.path.join(DATA_DIR, "rules")

def initialize_folders():
    """봇 실행에 필요한 데이터 폴더들을 초기화합니다."""
    for path in [SESSIONS_DIR, LORE_DIR, LORE_SUMMARY_DIR, RULES_DIR]:
        if not os.path.exists(path):
            try:
                os.makedirs(path)
            except Exception as e:
                logging.error(f"폴더 생성 실패 {path}: {e}")

def get_session_file_path(channel_id):
    return os.path.join(SESSIONS_DIR, f"{channel_id}.json")

def get_lore_file_path(channel_id):
    return os.path.join(LORE_DIR, f"{channel_id}.txt")

def get_lore_summary_file_path(channel_id):
    """요약된 로어 파일 경로"""
    return os.path.join(LORE_SUMMARY_DIR, f"{channel_id}_summary.txt")

def get_rules_file_path(channel_id):
    return os.path.join(RULES_DIR, f"{channel_id}.txt")

# =========================================================
# 데이터 로드 및 저장 (I/O)
# =========================================================
def load_json(filepath, default_val):
    if not os.path.exists(filepath): return default_val
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return default_val

def save_json(filepath, data):
    try:
        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logging.error(f"JSON 저장 실패 {filepath}: {e}")

def load_text(filepath, default_val):
    if not os.path.exists(filepath): return default_val
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            return f.read()
    except Exception:
        return default_val

def save_text(filepath, text):
    try:
        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(text)
    except Exception as e:
        logging.error(f"Text 저장 실패 {filepath}: {e}")

# =========================================================
# 도메인(세션) 관리
# =========================================================
def get_domain(channel_id):
    default_session = {
        "participants": {},
        "npcs": {},
        "history": [],
        "quest_board": {"active": [], "completed": [], "memos": []},
        "world_state": {"time_slot": "오후", "weather": "맑음", "day": 1, "doom": 0},
        "settings": {"response_mode": "auto", "session_locked": False, "growth_system": "standard"},
        "active_genres": ["noir"],
        "custom_tone": None
    }
    return load_json(get_session_file_path(channel_id), default_session)

def save_domain(channel_id, data):
    save_json(get_session_file_path(channel_id), data)

# 참가자 관리
def update_participant(channel_id, user, reset=False):
    d = get_domain(channel_id)
    uid = str(user.id)
    if reset or uid not in d["participants"]:
        d["participants"][uid] = {
            "mask": user.display_name,
            "description": "상세 설정 없음",
            "stats": {"근력": 10, "민첩": 10, "지능": 10, "매력": 10, "스트레스": 0},
            "inventory": {},
            "status_effects": [],
            "level": 1, "xp": 0, "next_xp": 100,
            "status": "active"
        }
    else:
        d["participants"][uid]["mask"] = user.display_name
        d["participants"][uid]["status"] = "active"
    save_domain(channel_id, d)
    return True

def get_participant_data(channel_id, user_id):
    d = get_domain(channel_id)
    return d["participants"].get(str(user_id))

def save_participant_data(channel_id, user_id, data):
    d = get_domain(channel_id)
    d["participants"][str(user_id)] = data
    save_domain(channel_id, d)

# 설정/규칙 관리
def get_lore(channel_id): return load_text(get_lore_file_path(channel_id), DEFAULT_LORE)
def append_lore(channel_id, text):
    current = get_lore(channel_id)
    # 기존 내용이 기본값이면 덮어쓰기, 아니면 이어쓰기
    new_text = text if current == DEFAULT_LORE else f"{current}\n\n{text}"
    save_text(get_lore_file_path(channel_id), new_text)

def reset_lore(channel_id):
    if os.path.exists(get_lore_file_path(channel_id)):
        os.remove(get_lore_file_path(channel_id))
    # 요약본도 함께 삭제
    if os.path.exists(get_lore_summary_file_path(channel_id)):
        os.remove(get_lore_summary_file_path(channel_id))

# 로어 요약 관리 함수
def get_lore_summary(channel_id):
    """압축된 로어 요약본을 가져옵니다. 없으면 None 반환."""
    path = get_lore_summary_file_path(channel_id)
    if os.path.exists(path):
        return load_text(path, "")
    return None

def save_lore_summary(channel_id, summary_text):
    """압축된 로어 요약본을 저장합니다."""
    save_text(get_lore_summary_file_path(channel_id), summary_text)

def get_rules(channel_id): return load_text(get_rules_file_path(channel_id), DEFAULT_RULES)
def append_rules(channel_id, text):
    current = get_rules(channel_id)
    new_text = text if current == DEFAULT_RULES else f"{current}\n\n{text}"
    save_text(get_rules_file_path(channel_id), new_text)
def reset_rules(channel_id):
    if os.path.exists(get_rules_file_path(channel_id)): os.remove(get_rules_file_path(channel_id))

def get_active_genres(channel_id): return get_domain(channel_id).get("active_genres", ["noir"])
def set_active_genres(channel_id, genres): d = get_domain(channel_id); d["active_genres"] = genres; save_domain(channel_id, d)

def get_custom_tone(channel_id): return get_domain(channel_id).get("custom_tone")
def set_custom_tone(channel_id, tone): d = get_domain(channel_id); d["custom_tone"] = tone; save_domain(channel_id, d)

# 유틸리티
def is_bot_disabled(channel_id): return get_domain(channel_id).get("disabled", False)
def set_bot_disabled(channel_id, disabled): d = get_domain(channel_id); d["disabled"] = disabled; save_domain(channel_id, d)

def is_prepared(channel_id): return get_domain(channel_id).get("prepared", False)
def set_prepared(channel_id, prepared): d = get_domain(channel_id); d["prepared"] = prepared; save_domain(channel_id, d)

def get_response_mode(channel_id): return get_domain(channel_id)["settings"].get("response_mode", "auto")
def set_response_mode(channel_id, mode): d = get_domain(channel_id); d["settings"]["response_mode"] = mode; save_domain(channel_id, d)

def get_growth_system(channel_id): return get_domain(channel_id)["settings"].get("growth_system", "standard")
def set_growth_system(channel_id, mode): d = get_domain(channel_id); d["settings"]["growth_system"] = mode; save_domain(channel_id, d)

def set_session_lock(channel_id, locked): d = get_domain(channel_id); d["settings"]["session_locked"] = locked; save_domain(channel_id, d)

def set_current_location(channel_id, loc): d = get_domain(channel_id); d["current_location"] = loc; save_domain(channel_id, d)
def set_current_risk(channel_id, risk): d = get_domain(channel_id); d["risk_level"] = risk; save_domain(channel_id, d)

def get_participant_status(channel_id, uid): return get_domain(channel_id)["participants"].get(str(uid), {}).get("status", "active")
def set_participant_status(channel_id, uid, status, reason=""): 
    d = get_domain(channel_id)
    if str(uid) in d["participants"]:
        d["participants"][str(uid)]["status"] = status
        if status == "left":
            mask = d["participants"][str(uid)]["mask"]
            append_history(channel_id, "System", f"[{mask}] 님이 {reason}로 인해 퇴장했습니다.")
    save_domain(channel_id, d)

def append_history(channel_id, role, content):
    d = get_domain(channel_id)
    d["history"].append({"role": role, "content": content})
    if len(d['history']) > 40: d['history'] = d['history'][-40:]
    save_domain(channel_id, d)

def reset_domain(channel_id):
    for f in [get_session_file_path(channel_id), get_lore_file_path(channel_id), get_rules_file_path(channel_id), get_lore_summary_file_path(channel_id)]:
        if os.path.exists(f): os.remove(f)

# NPC 및 기타 데이터
def update_npc(cid, name, data): d = get_domain(cid); d["npcs"][name] = data; save_domain(cid, d)
def get_npcs(cid): return get_domain(cid).get("npcs", {})

def get_quest_board(cid): return get_domain(cid).get("quest_board")
def update_quest_board(cid, b): d = get_domain(cid); d["quest_board"] = b; save_domain(cid, d)
def get_world_state(cid): return get_domain(cid).get("world_state")
def update_world_state(cid, s): d = get_domain(cid); d["world_state"] = s; save_domain(cid, d)
def get_user_mask(cid, uid): return get_domain(cid)["participants"].get(str(uid), {}).get("mask", "Unknown")
def set_user_mask(cid, uid, mask): d = get_domain(cid); uid=str(uid); d["participants"][uid]["mask"] = mask; save_domain(cid, d)
def set_user_description(cid, uid, desc): d = get_domain(cid); uid=str(uid); d["participants"][uid]["description"] = desc; save_domain(cid, d)
def set_location_rules(cid, rules): d = get_domain(cid); d["location_rules"] = rules; save_domain(cid, d)

# [필수 추가 함수] 파티 상태 컨텍스트 반환 (world_manager에서 사용)
def get_party_status_context(channel_id):
    """
    현재 참가자들의 상태(체력, 스트레스, 위치 등)를 요약하여 텍스트로 반환합니다.
    """
    d = get_domain(channel_id)
    participants = d.get("participants", {})
    if not participants:
        return "파티 상태: 참가자 없음"
    
    summary = []
    for uid, p_data in participants.items():
        mask = p_data.get("mask", "Unknown")
        status = p_data.get("status", "active")
        if status != "active": continue # 활성 참가자만 표시
        
        stats = p_data.get("stats", {})
        stress = stats.get("스트레스", 0)
        effects = p_data.get("status_effects", [])
        
        status_text = f"[{mask}] 스트레스: {stress}"
        if effects:
            status_text += f" | 상태이상: {', '.join(effects)}"
        summary.append(status_text)
        
    return "\n".join(summary)