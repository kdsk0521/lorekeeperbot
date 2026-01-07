"""
Lorekeeper TRPG Bot - Domain Manager Module
세션 데이터, 로어, 룰북 등의 영구 저장을 담당합니다.
"""

import os
import json
import logging
from typing import Optional, Dict, Any, List

# =========================================================
# 상수 정의
# =========================================================
MAX_HISTORY_LENGTH = 40  # 히스토리 최대 보관 개수
MAX_DESC_LENGTH = 50  # 설명 요약 시 최대 길이

DEFAULT_LORE = "[장르: 설정되지 않음]"
DEFAULT_RULES = """
[게임 규칙: 표준 TRPG 시스템]
1. 판정: 모든 행동은 !r 1d20 (20면체 주사위)을 통해 결정됩니다.
2. 난이도(DC): 보통 10 / 어려움 15 / 매우 어려움 20 / 불가능 25
3. 전투: 주사위 값이 높을수록 더 효율적이고 치명적인 공격을 성공시킵니다.
4. 성장: 캐릭터는 행동과 선택을 통해 경험치를 얻고 레벨업하며 스탯을 올립니다.
"""

# 디렉토리 경로
DATA_DIR = "data"
SESSIONS_DIR = os.path.join(DATA_DIR, "sessions")
LORE_DIR = os.path.join(DATA_DIR, "lores")
LORE_SUMMARY_DIR = os.path.join(DATA_DIR, "lore_summaries")
RULES_DIR = os.path.join(DATA_DIR, "rules")

# 기본 참가자 스탯
DEFAULT_STATS = {
    "근력": 10,
    "민첩": 10,
    "지능": 10,
    "매력": 10,
    "스트레스": 0
}

# 기본 월드 스테이트 (누락 키 추가됨)
DEFAULT_WORLD_STATE = {
    "time_slot": "오후",
    "weather": "맑음",
    "day": 1,
    "doom": 0,
    "doom_name": "위기",
    "risk_level": "None",  # AI 분석용
    "current_location": "Unknown",  # AI 분석용
    "location_rules": {},  # 위치별 규칙
    "world_constraints": {},  # 추출된 세계 규칙
    "active_threads": [],  # 활성 플롯 스레드
    "last_temporal_context": {}  # 마지막 Temporal Orientation
}


# =========================================================
# 초기화
# =========================================================
def initialize_folders() -> None:
    """봇 실행에 필요한 데이터 폴더들을 초기화합니다."""
    folders = [SESSIONS_DIR, LORE_DIR, LORE_SUMMARY_DIR, RULES_DIR]
    
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
    return os.path.join(SESSIONS_DIR, f"{channel_id}.json")


def get_lore_file_path(channel_id: str) -> str:
    return os.path.join(LORE_DIR, f"{channel_id}.txt")


def get_lore_summary_file_path(channel_id: str) -> str:
    """요약된 로어 파일 경로"""
    return os.path.join(LORE_SUMMARY_DIR, f"{channel_id}_summary.txt")


def get_rules_file_path(channel_id: str) -> str:
    return os.path.join(RULES_DIR, f"{channel_id}.txt")


# =========================================================
# 데이터 로드 및 저장 (I/O)
# =========================================================
def load_json(filepath: str, default_val: Any) -> Any:
    """JSON 파일을 로드합니다."""
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
    """JSON 파일을 저장합니다."""
    try:
        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        return True
    except Exception as e:
        logging.error(f"JSON 저장 실패 {filepath}: {e}")
        return False


def load_text(filepath: str, default_val: str) -> str:
    """텍스트 파일을 로드합니다."""
    if not os.path.exists(filepath):
        return default_val
    
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            return f.read()
    except Exception as e:
        logging.error(f"텍스트 로드 실패 {filepath}: {e}")
        return default_val


def save_text(filepath: str, text: str) -> bool:
    """텍스트 파일을 저장합니다."""
    try:
        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(text)
        return True
    except Exception as e:
        logging.error(f"텍스트 저장 실패 {filepath}: {e}")
        return False


# =========================================================
# 도메인(세션) 관리
# =========================================================
def _get_default_session() -> Dict[str, Any]:
    """기본 세션 데이터 구조를 반환합니다."""
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
        "world_state": DEFAULT_WORLD_STATE.copy(),
        "settings": {
            "response_mode": "auto",
            "session_locked": False,
            "growth_system": "standard"
        },
        "active_genres": ["noir"],
        "custom_tone": None,
        "prepared": False,
        "disabled": False,
        "last_export_idx": 0
    }


def get_domain(channel_id: str) -> Dict[str, Any]:
    """채널의 도메인 데이터를 가져옵니다."""
    default_session = _get_default_session()
    data = load_json(get_session_file_path(channel_id), default_session)
    
    # 누락된 키 보정
    for key, default_value in default_session.items():
        if key not in data:
            data[key] = default_value
    
    # world_state 내부 키 보정
    if "world_state" in data:
        for ws_key, ws_default in DEFAULT_WORLD_STATE.items():
            if ws_key not in data["world_state"]:
                data["world_state"][ws_key] = ws_default
    
    return data


def save_domain(channel_id: str, data: Dict[str, Any]) -> bool:
    """채널의 도메인 데이터를 저장합니다."""
    return save_json(get_session_file_path(channel_id), data)


# =========================================================
# 참가자 관리
# =========================================================
def _create_default_participant(display_name: str) -> Dict[str, Any]:
    """기본 참가자 데이터 구조를 생성합니다."""
    return {
        "mask": display_name,
        "description": "상세 설정 없음",
        "stats": DEFAULT_STATS.copy(),
        "inventory": {},
        "status_effects": [],
        "relations": {},
        "level": 1,
        "xp": 0,
        "next_xp": 100,
        "status": "active",
        "summary_data": {}
    }


def update_participant(channel_id: str, user, reset: bool = False) -> bool:
    """
    참가자를 등록하거나 업데이트합니다.
    
    Args:
        channel_id: 채널 ID
        user: Discord 유저 객체
        reset: True면 기존 데이터를 초기화
    
    Returns:
        성공 여부
    """
    d = get_domain(channel_id)
    uid = str(user.id)
    
    if reset or uid not in d["participants"]:
        d["participants"][uid] = _create_default_participant(user.display_name)
    else:
        # 기존 참가자는 상태만 활성화
        d["participants"][uid]["status"] = "active"
    
    save_domain(channel_id, d)
    return True


def get_participant_data(channel_id: str, user_id: str) -> Optional[Dict[str, Any]]:
    """참가자 데이터를 가져옵니다."""
    d = get_domain(channel_id)
    return d["participants"].get(str(user_id))


def save_participant_data(channel_id: str, user_id: str, data: Dict[str, Any]) -> None:
    """참가자 데이터를 저장합니다."""
    d = get_domain(channel_id)
    d["participants"][str(user_id)] = data
    save_domain(channel_id, d)


def save_participant_summary(channel_id: str, user_id: str, summary_data: Dict[str, Any]) -> None:
    """참가자 요약 정보를 저장합니다 (AI 분석 결과)."""
    d = get_domain(channel_id)
    uid = str(user_id)
    
    if uid in d["participants"]:
        d["participants"][uid]["summary_data"] = summary_data
        save_domain(channel_id, d)


def get_participant_status(channel_id: str, uid: str) -> str:
    """참가자의 상태를 가져옵니다."""
    d = get_domain(channel_id)
    return d["participants"].get(str(uid), {}).get("status", "active")


def set_participant_status(channel_id: str, uid: str, status: str, reason: str = "") -> None:
    """참가자의 상태를 변경합니다."""
    d = get_domain(channel_id)
    uid = str(uid)
    
    if uid in d["participants"]:
        d["participants"][uid]["status"] = status
        
        if status == "left" and reason:
            mask = d["participants"][uid].get("mask", "Unknown")
            append_history(channel_id, "System", f"[{mask}] 님이 {reason}로 인해 퇴장했습니다.")
    
    save_domain(channel_id, d)


# =========================================================
# 로어 관리
# =========================================================
def get_lore(channel_id: str) -> str:
    """로어를 가져옵니다."""
    return load_text(get_lore_file_path(channel_id), DEFAULT_LORE)


def append_lore(channel_id: str, text: str) -> None:
    """로어를 추가합니다."""
    current = get_lore(channel_id)
    new_text = text if current == DEFAULT_LORE else f"{current}\n\n{text}"
    save_text(get_lore_file_path(channel_id), new_text)


def reset_lore(channel_id: str) -> None:
    """로어와 요약본을 초기화합니다."""
    lore_path = get_lore_file_path(channel_id)
    summary_path = get_lore_summary_file_path(channel_id)
    
    if os.path.exists(lore_path):
        os.remove(lore_path)
    if os.path.exists(summary_path):
        os.remove(summary_path)


def get_lore_summary(channel_id: str) -> Optional[str]:
    """요약된 로어를 가져옵니다."""
    path = get_lore_summary_file_path(channel_id)
    if os.path.exists(path):
        content = load_text(path, "")
        return content if content else None
    return None


def save_lore_summary(channel_id: str, summary_text: str) -> None:
    """요약된 로어를 저장합니다."""
    save_text(get_lore_summary_file_path(channel_id), summary_text)


# =========================================================
# 룰 관리
# =========================================================
def get_rules(channel_id: str) -> str:
    """룰을 가져옵니다."""
    return load_text(get_rules_file_path(channel_id), DEFAULT_RULES)


def append_rules(channel_id: str, text: str) -> None:
    """룰을 추가합니다."""
    current = get_rules(channel_id)
    new_text = text if current == DEFAULT_RULES else f"{current}\n\n{text}"
    save_text(get_rules_file_path(channel_id), new_text)


def reset_rules(channel_id: str) -> None:
    """룰을 초기화합니다."""
    path = get_rules_file_path(channel_id)
    if os.path.exists(path):
        os.remove(path)


# =========================================================
# 장르 및 톤 관리
# =========================================================
def get_active_genres(channel_id: str) -> List[str]:
    """활성 장르 목록을 가져옵니다."""
    return get_domain(channel_id).get("active_genres", ["noir"])


def set_active_genres(channel_id: str, genres: List[str]) -> None:
    """활성 장르 목록을 설정합니다."""
    d = get_domain(channel_id)
    d["active_genres"] = genres
    save_domain(channel_id, d)


def get_custom_tone(channel_id: str) -> Optional[str]:
    """커스텀 톤을 가져옵니다."""
    return get_domain(channel_id).get("custom_tone")


def set_custom_tone(channel_id: str, tone: Optional[str]) -> None:
    """커스텀 톤을 설정합니다."""
    d = get_domain(channel_id)
    d["custom_tone"] = tone
    save_domain(channel_id, d)


# =========================================================
# 설정 관리
# =========================================================
def is_bot_disabled(channel_id: str) -> bool:
    """봇이 비활성화되었는지 확인합니다."""
    return get_domain(channel_id).get("disabled", False)


def set_bot_disabled(channel_id: str, disabled: bool) -> None:
    """봇 비활성화 상태를 설정합니다."""
    d = get_domain(channel_id)
    d["disabled"] = disabled
    save_domain(channel_id, d)


def is_prepared(channel_id: str) -> bool:
    """세션이 준비되었는지 확인합니다."""
    return get_domain(channel_id).get("prepared", False)


def set_prepared(channel_id: str, prepared: bool) -> None:
    """세션 준비 상태를 설정합니다."""
    d = get_domain(channel_id)
    d["prepared"] = prepared
    save_domain(channel_id, d)


def get_response_mode(channel_id: str) -> str:
    """응답 모드를 가져옵니다 (auto/manual)."""
    return get_domain(channel_id)["settings"].get("response_mode", "auto")


def set_response_mode(channel_id: str, mode: str) -> None:
    """응답 모드를 설정합니다."""
    d = get_domain(channel_id)
    d["settings"]["response_mode"] = mode
    save_domain(channel_id, d)


def get_growth_system(channel_id: str) -> str:
    """성장 시스템을 가져옵니다."""
    return get_domain(channel_id)["settings"].get("growth_system", "standard")


def set_growth_system(channel_id: str, mode: str) -> None:
    """성장 시스템을 설정합니다."""
    d = get_domain(channel_id)
    d["settings"]["growth_system"] = mode
    save_domain(channel_id, d)


def set_session_lock(channel_id: str, locked: bool) -> None:
    """세션 잠금 상태를 설정합니다."""
    d = get_domain(channel_id)
    d["settings"]["session_locked"] = locked
    save_domain(channel_id, d)


# =========================================================
# 월드 스테이트 관리
# =========================================================
def get_world_state(channel_id: str) -> Dict[str, Any]:
    """월드 스테이트를 가져옵니다."""
    return get_domain(channel_id).get("world_state", DEFAULT_WORLD_STATE.copy())


def update_world_state(channel_id: str, state: Dict[str, Any]) -> None:
    """월드 스테이트를 업데이트합니다."""
    d = get_domain(channel_id)
    d["world_state"] = state
    save_domain(channel_id, d)


def set_current_location(channel_id: str, location: str) -> None:
    """현재 위치를 설정합니다."""
    d = get_domain(channel_id)
    d["world_state"]["current_location"] = location
    save_domain(channel_id, d)


def set_current_risk(channel_id: str, risk: str) -> None:
    """현재 위험도를 설정합니다."""
    d = get_domain(channel_id)
    d["world_state"]["risk_level"] = risk
    save_domain(channel_id, d)


def set_location_rules(channel_id: str, rules: Dict[str, Any]) -> None:
    """위치별 규칙을 설정합니다."""
    d = get_domain(channel_id)
    d["world_state"]["location_rules"] = rules
    save_domain(channel_id, d)


# =========================================================
# 히스토리 관리
# =========================================================
def append_history(channel_id: str, role: str, content: str) -> None:
    """대화 히스토리에 항목을 추가합니다."""
    d = get_domain(channel_id)
    d["history"].append({"role": role, "content": content})
    
    # 최대 길이 초과 시 오래된 항목 제거
    if len(d["history"]) > MAX_HISTORY_LENGTH:
        d["history"] = d["history"][-MAX_HISTORY_LENGTH:]
    
    save_domain(channel_id, d)


# =========================================================
# 도메인 리셋
# =========================================================
def reset_domain(channel_id: str) -> None:
    """채널의 모든 데이터를 초기화합니다."""
    files_to_remove = [
        get_session_file_path(channel_id),
        get_lore_file_path(channel_id),
        get_rules_file_path(channel_id),
        get_lore_summary_file_path(channel_id)
    ]
    
    for filepath in files_to_remove:
        if os.path.exists(filepath):
            try:
                os.remove(filepath)
            except Exception as e:
                logging.error(f"파일 삭제 실패 {filepath}: {e}")


# =========================================================
# NPC 관리
# =========================================================
def get_npcs(channel_id: str) -> Dict[str, Dict[str, Any]]:
    """NPC 목록을 가져옵니다."""
    return get_domain(channel_id).get("npcs", {})


def update_npc(channel_id: str, name: str, data: Dict[str, Any]) -> None:
    """NPC 정보를 업데이트합니다."""
    d = get_domain(channel_id)
    d["npcs"][name] = data
    save_domain(channel_id, d)


# =========================================================
# 퀘스트 보드 관리
# =========================================================
def get_quest_board(channel_id: str) -> Optional[Dict[str, Any]]:
    """퀘스트 보드를 가져옵니다."""
    return get_domain(channel_id).get("quest_board")


def update_quest_board(channel_id: str, board: Dict[str, Any]) -> None:
    """퀘스트 보드를 업데이트합니다."""
    d = get_domain(channel_id)
    d["quest_board"] = board
    save_domain(channel_id, d)


# =========================================================
# 유저 정보 관리
# =========================================================
def get_user_mask(channel_id: str, uid: str) -> str:
    """유저의 가면(닉네임)을 가져옵니다."""
    d = get_domain(channel_id)
    return d["participants"].get(str(uid), {}).get("mask", "Unknown")


def set_user_mask(channel_id: str, uid: str, mask: str) -> None:
    """유저의 가면(닉네임)을 설정합니다."""
    d = get_domain(channel_id)
    uid = str(uid)
    
    if uid in d["participants"]:
        d["participants"][uid]["mask"] = mask
        save_domain(channel_id, d)


def set_user_description(channel_id: str, uid: str, desc: str) -> None:
    """유저의 설명을 설정합니다."""
    d = get_domain(channel_id)
    uid = str(uid)
    
    if uid in d["participants"]:
        d["participants"][uid]["description"] = desc
        save_domain(channel_id, d)


# =========================================================
# 파티 상태 컨텍스트
# =========================================================
def get_party_status_context(channel_id: str) -> str:
    """
    현재 참가자들의 상세 상태를 요약하여 반환합니다.
    AI에게 컨텍스트로 제공됩니다.
    """
    d = get_domain(channel_id)
    participants = d.get("participants", {})
    
    if not participants:
        return "파티 상태: 참가자 없음"
    
    summary_parts = []
    
    for uid, p_data in participants.items():
        # 비활성 참가자 제외
        status = p_data.get("status", "active")
        if status != "active":
            continue
        
        mask = p_data.get("mask", "Unknown")
        desc = p_data.get("description", "특이사항 없음")
        level = p_data.get("level", 1)
        xp = p_data.get("xp", 0)
        next_xp = p_data.get("next_xp", 100)
        stats = p_data.get("stats", {})
        
        # 스탯 문자열
        stats_str = ", ".join([f"{k}: {v}" for k, v in stats.items()])
        
        # 저장된 요약 정보
        summary_data = p_data.get("summary_data", {})
        relations = summary_data.get("relationships", [])
        appearance = summary_data.get("appearance_summary", "")
        
        # 종합 텍스트 생성
        status_text = (
            f"--- [{mask}] ---\n"
            f"   Level: {level} (XP: {xp}/{next_xp})\n"
            f"   Stats: {stats_str}\n"
        )
        
        # 외형 정보 (요약 우선)
        if appearance:
            status_text += f"   Appearance: {appearance}\n"
        else:
            short_desc = desc[:MAX_DESC_LENGTH] + "..." if len(desc) > MAX_DESC_LENGTH else desc
            status_text += f"   Desc: {short_desc}\n"
        
        # 관계 정보
        if relations:
            rel_str = ", ".join(relations[:3])
            status_text += f"   Relations: {rel_str}\n"
        
        summary_parts.append(status_text)
    
    return "\n".join(summary_parts) if summary_parts else "파티 상태: 활성 참가자 없음"


# =========================================================
# 세계 상태 확장 함수
# =========================================================
def set_world_constraints(channel_id: str, constraints: Dict[str, Any]) -> None:
    """추출된 세계 규칙을 저장합니다."""
    d = get_domain(channel_id)
    d["world_state"]["world_constraints"] = constraints
    save_domain(channel_id, d)


def get_world_constraints(channel_id: str) -> Dict[str, Any]:
    """저장된 세계 규칙을 반환합니다."""
    return get_domain(channel_id).get("world_state", {}).get("world_constraints", {})


def set_active_threads(channel_id: str, threads: List[str]) -> None:
    """활성 플롯 스레드를 저장합니다."""
    d = get_domain(channel_id)
    d["world_state"]["active_threads"] = threads
    save_domain(channel_id, d)


def get_active_threads(channel_id: str) -> List[str]:
    """활성 플롯 스레드를 반환합니다."""
    return get_domain(channel_id).get("world_state", {}).get("active_threads", [])


def set_temporal_context(channel_id: str, context: Dict[str, Any]) -> None:
    """마지막 Temporal Orientation을 저장합니다."""
    d = get_domain(channel_id)
    d["world_state"]["last_temporal_context"] = context
    save_domain(channel_id, d)


def get_temporal_context(channel_id: str) -> Dict[str, Any]:
    """마지막 Temporal Orientation을 반환합니다."""
    return get_domain(channel_id).get("world_state", {}).get("last_temporal_context", {})
