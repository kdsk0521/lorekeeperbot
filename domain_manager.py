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

DEFAULT_LORE = ""  # 로어는 반드시 사용자가 설정해야 함
DEFAULT_RULES = """
[Lorekeeper 기본 룰: 서사 중심 TRPG]

## 📜 판정 시스템
**기본 원칙: 주사위 없이 서사적 판정**
- AI가 캐릭터의 패시브, 칭호, 현재 상황을 종합하여 성공/실패를 판단
- 캐릭터의 능력, 경험, 상황적 유불리가 결과에 영향

**선택적 주사위 사용 (플레이어 요청 시)**
- `!r 1d20` 또는 `!r 1d100`: 높을수록 좋은 결과
- 주사위 결과는 참고 자료이며, AI가 서사적으로 해석
- 커스텀 룰에서 별도 판정 규칙을 정의할 수 있음

## 🎭 캐릭터 성장
성장은 경험치나 레벨이 아닌 **서사적 성취**를 통해 이루어집니다:
- **패시브**: 반복된 행동이나 경험을 통해 습득하는 특성
  예) "독 내성" - 독에 여러 번 노출된 후 획득
  예) "야간 시야" - 어둠 속에서 오래 활동한 후 획득
- **칭호**: 특별한 업적이나 인정을 통해 얻는 명예
  예) "드래곤 슬레이어" - 드래곤을 처치한 후 획득
  예) "숲의 친구" - 엘프들에게 인정받은 후 획득

## 🌓 비일상 적응
초자연적/비일상적 존재나 현상에 반복 노출되면 점차 익숙해집니다:
- 처음: 공포, 혼란, 패닉
- 적응 중: 경계하지만 대처 가능
- 일상화: 담담하게 받아들임
AI가 캐릭터의 노출 횟수와 반응을 추적하여 자연스럽게 적응도를 부여합니다.

## ⚔️ 전투
- 선제권: 상황과 캐릭터 특성에 따라 판단
- 성공/실패: 캐릭터 능력, 패시브, 상황을 종합하여 서사적으로 결정
- 피해: 서사적으로 묘사 (HP 수치 없음)
- 상태이상: 부상, 중독, 공포 등이 행동에 영향

## 💰 소지품
- 화폐: 세계관에 맞는 단위 사용 (골드, 은화, 크레딧 등)
- 인벤토리: 소지품 목록
- 거래: 협상과 상황에 따라 가격 변동
- AI가 세계관에 맞게 화폐 단위를 자동 판단

## 📝 특수 규칙
- OOC 수정: `(OOC: 요청)` 형식으로 캐릭터 정보 수정 가능
- 정보 확인: `!정보`로 캐릭터 상태 조회
"""

# 디렉토리 경로
DATA_DIR = "data"
SESSIONS_DIR = os.path.join(DATA_DIR, "sessions")
LORE_DIR = os.path.join(DATA_DIR, "lores")
LORE_SUMMARY_DIR = os.path.join(DATA_DIR, "lore_summaries")
RULES_DIR = os.path.join(DATA_DIR, "rules")

# 기본 참가자 스탯 (레거시 - 제거 예정)
# DEFAULT_STATS = {} # 스탯 시스템 제거됨

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
            "growth_system": "default"  # "default" 또는 "custom"
        },
        "active_genres": ["noir"],
        "custom_tone": None,
        "prepared": False,
        "disabled": False,
        "last_export_idx": 0,
        
        # === 세션 레벨 AI 메모리 (서사 중심) ===
        "ai_session_memory": {
            "world_summary": "",  # "혼란의 시대, 마왕 부활 임박"
            "current_arc": "",  # "마왕의 부하 추적 중"
            "active_threads": [],  # ["리엘의 비밀", "상인 길드 음모"]
            "resolved_threads": [],  # ["첫 번째 봉인 해제"]
            "key_events": [],  # ["3일차: 드래곤과 조우", "5일차: 리엘 합류"]
            "foreshadowing": [],  # ["봉인된 편지", "검은 로브의 남자"]
            "world_changes": [],  # ["마을에 경비 강화됨", "상인 길드 적대적"]
            "npc_summaries": {},  # {"리엘": "엘프 궁수, 비밀이 있음", "길드장": "탐욕스러움"}
            "party_dynamics": "",  # "서로 신뢰 쌓는 중, 갈등 요소 없음"
            "last_updated": ""  # 마지막 갱신 시점
        }
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
    """
    기본 참가자 데이터 구조를 생성합니다.
    
    [서사 중심 구조]
    - economy: 골드, 인벤토리 (코드 관리)
    - ai_memory: AI가 관리 (유연한 서사, 패시브/칭호 포함)
    """
    return {
        # === 기본 정보 ===
        "mask": display_name,
        "status": "active",
        
        # === 경제 시스템 (코드 관리) ===
        "economy": {
            "gold": 0
        },
        "inventory": {},  # {"검": 1, "포션": 3}
        "status_effects": [],  # ["중독", "출혈"] - 상태이상은 유지
        
        # === AI 관리 영역 (서사, 유연해야 함) ===
        "ai_memory": {
            "appearance": "",  # "검은 머리, 날카로운 눈, 낡은 가죽 갑옷"
            "personality": "",  # "과묵하지만 정의로움"
            "background": "",  # "고향이 불탄 뒤 복수를 다짐"
            "relationships": {},  # {"리엘": "서로 호감, 신뢰 쌓는 중", "상인 길드장": "적대적"}
            "passives": [],  # ["독 내성", "엘프의 친구"] - 패시브/칭호
            "known_info": [],  # ["마왕의 부하가 북쪽에 있다", "비밀 통로 위치"]
            "foreshadowing": [],  # ["봉인된 편지의 내용", "리엘의 과거"]
            "normalization": {},  # {"드래곤": "이제 익숙함", "마법": "아직 신기함"}
            "notes": ""  # 자유 형식 메모
        },
        
        # === 호환성 (기존 코드용, 점진적 제거 예정) ===
        "description": "",
        "relations": {},  # 숫자 기반 → ai_memory.relationships로 이전
        "summary_data": {},
        "abnormal_exposure": {},
        "passives": [],
        "experience_counters": {}
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
        
        # 기존 데이터에 ai_memory 필드가 없으면 추가 (마이그레이션)
        if "ai_memory" not in d["participants"][uid]:
            d["participants"][uid]["ai_memory"] = {
                "appearance": d["participants"][uid].get("description", ""),
                "personality": "",
                "background": "",
                "relationships": {},
                "passives": [p.get("name", "") for p in d["participants"][uid].get("passives", [])],
                "known_info": [],
                "foreshadowing": [],
                "normalization": {},
                "notes": ""
            }
        
        # economy 필드 없으면 추가 (마이그레이션)
        if "economy" not in d["participants"][uid]:
            # 기존 core_stats나 gold에서 가져오기
            old_core = d["participants"][uid].get("core_stats", {})
            old_gold = old_core.get("gold", 0) if old_core else 0
            d["participants"][uid]["economy"] = {
                "gold": old_gold
            }
    
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
# 룰 관리 (3가지 모드)
# 1. 기본룰만: 아무 설정 없음 → DEFAULT_RULES 반환
# 2. 기본룰+커스텀: !룰 추가 → 기본룰 + 커스텀룰 병합
# 3. 완전 커스텀: txt 파일 업로드 → 파일 내용만 사용
# =========================================================

def get_rules(channel_id: str) -> str:
    """룰을 가져옵니다."""
    return load_text(get_rules_file_path(channel_id), DEFAULT_RULES)


def get_rules_mode(channel_id: str) -> str:
    """
    현재 룰 모드를 반환합니다.
    
    Returns:
        'default': 기본룰만
        'hybrid': 기본룰 + 커스텀룰
        'custom': 완전 커스텀 (파일 업로드)
    """
    return get_domain(channel_id).get("rules_mode", "default")


def set_rules_mode(channel_id: str, mode: str) -> None:
    """룰 모드를 설정합니다."""
    d = get_domain(channel_id)
    d["rules_mode"] = mode
    save_domain(channel_id, d)


def append_rules(channel_id: str, text: str) -> None:
    """
    룰을 추가합니다 (기본룰 + 커스텀룰 병합).
    """
    current_mode = get_rules_mode(channel_id)
    
    if current_mode == "custom":
        # 완전 커스텀 모드면 기존 커스텀에 추가
        current = get_rules(channel_id)
        new_text = f"{current}\n\n{text}"
    else:
        # 기본룰 또는 하이브리드 모드
        # 기본룰은 항상 유지하고 커스텀 파트만 관리
        d = get_domain(channel_id)
        custom_rules = d.get("custom_rules", "")
        
        if custom_rules:
            custom_rules = f"{custom_rules}\n\n{text}"
        else:
            custom_rules = text
        
        d["custom_rules"] = custom_rules
        save_domain(channel_id, d)
        
        # 하이브리드 모드로 전환
        set_rules_mode(channel_id, "hybrid")
        
        # 병합된 룰 저장
        new_text = f"{DEFAULT_RULES}\n\n[커스텀 추가 규칙]\n{custom_rules}"
    
    save_text(get_rules_file_path(channel_id), new_text)


def set_custom_rules_from_file(channel_id: str, file_content: str) -> None:
    """
    파일에서 완전 커스텀 룰을 설정합니다.
    기본룰을 완전히 대체합니다.
    """
    save_text(get_rules_file_path(channel_id), file_content)
    set_rules_mode(channel_id, "custom")
    
    # 커스텀 추가분 초기화
    d = get_domain(channel_id)
    d["custom_rules"] = ""
    save_domain(channel_id, d)


def reset_rules(channel_id: str) -> None:
    """룰을 초기화합니다 (기본룰로 복귀)."""
    path = get_rules_file_path(channel_id)
    if os.path.exists(path):
        os.remove(path)
    
    set_rules_mode(channel_id, "default")
    
    # 커스텀 추가분도 초기화
    d = get_domain(channel_id)
    d["custom_rules"] = ""
    save_domain(channel_id, d)


def get_custom_rules_part(channel_id: str) -> str:
    """커스텀 추가된 룰 부분만 반환합니다."""
    return get_domain(channel_id).get("custom_rules", "")


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


# =========================================================
# 성장 시스템 설정 (v4.0: default / custom만 지원)
# =========================================================

def get_growth_system(channel_id: str) -> str:
    """
    성장 시스템 모드를 가져옵니다.
    
    Returns:
        'default': 기본 룰북 + AI 패시브/칭호 자동 부여
        'custom': 사용자 룰북만 적용 (AI가 룰에 따라 판단)
    """
    return get_domain(channel_id)["settings"].get("growth_system", "default")


def set_growth_system(channel_id: str, mode: str) -> None:
    """
    성장 시스템 모드를 설정합니다.
    
    Args:
        mode: 'default' 또는 'custom'
    """
    if mode not in ("default", "custom"):
        mode = "default"
    d = get_domain(channel_id)
    d["settings"]["growth_system"] = mode
    save_domain(channel_id, d)


# =========================================================
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
    다중 플레이어를 명확하게 구분합니다.
    서사 중심 - 패시브/칭호, 관계, 상태이상 중심
    """
    d = get_domain(channel_id)
    participants = d.get("participants", {})
    
    if not participants:
        return "Active Players: None"
    
    active_players = []
    inactive_players = []
    
    for uid, p_data in participants.items():
        mask = p_data.get("mask", "Unknown")
        status = p_data.get("status", "active")
        
        if status != "active":
            inactive_players.append(f"{mask} ({status})")
            continue
        
        # AI 메모리에서 서사 정보 가져오기
        ai_mem = p_data.get("ai_memory", {})
        appearance = ai_mem.get("appearance", "")
        personality = ai_mem.get("personality", "")
        passives = ai_mem.get("passives", [])
        relationships = ai_mem.get("relationships", {})
        
        # 상태이상
        status_effects = p_data.get("status_effects", [])
        effects_str = ", ".join(status_effects[:3]) if status_effects else "정상"
        
        # 외형 (짧게)
        look = appearance[:50] + "..." if len(appearance) > 50 else appearance if appearance else "미설정"
        
        # 패시브/칭호 (최대 3개)
        passives_str = ", ".join(passives[:3]) if passives else "없음"
        if len(passives) > 3:
            passives_str += f" 외 {len(passives)-3}개"
        
        # 주요 관계 (최대 2개)
        rel_list = [f"{k}: {v}" for k, v in list(relationships.items())[:2]]
        rel_str = " | ".join(rel_list) if rel_list else "없음"
        
        # 플레이어 정보 (AI가 [이름] 형식으로 인식하도록)
        player_info = (
            f"**[{mask}]**\n"
            f"  Look: {look}\n"
            f"  Passives: {passives_str}\n"
            f"  Relations: {rel_str}\n"
            f"  Conditions: {effects_str}"
        )
        active_players.append(player_info)
    
    # 결과 조합
    result = f"### ACTIVE PLAYERS ({len(active_players)}명)\n"
    result += "**Important:** Each [Name] is a separate player. Track actions individually.\n\n"
    
    if active_players:
        result += "\n\n".join(active_players)
    else:
        result += "(없음)"
    
    if inactive_players:
        result += f"\n\n### INACTIVE: {', '.join(inactive_players)}"
    
    return result


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


# =========================================================
# AI 메모리 관리 시스템
# =========================================================

def get_ai_memory(channel_id: str, user_id: str) -> Dict[str, Any]:
    """플레이어의 AI 메모리를 가져옵니다."""
    p_data = get_participant_data(channel_id, user_id)
    if not p_data:
        return {}
    return p_data.get("ai_memory", {})


def update_ai_memory(channel_id: str, user_id: str, updates: Dict[str, Any]) -> None:
    """
    플레이어의 AI 메모리를 업데이트합니다.
    
    Args:
        channel_id: 채널 ID
        user_id: 유저 ID
        updates: 업데이트할 필드들 (부분 업데이트 지원)
    """
    d = get_domain(channel_id)
    uid = str(user_id)
    
    if uid not in d["participants"]:
        return
    
    if "ai_memory" not in d["participants"][uid]:
        d["participants"][uid]["ai_memory"] = {}
    
    ai_mem = d["participants"][uid]["ai_memory"]
    
    for key, value in updates.items():
        if key in ai_mem:
            # 리스트 필드는 병합
            if isinstance(ai_mem[key], list) and isinstance(value, list):
                # 중복 제거하면서 추가
                existing = set(ai_mem[key])
                for item in value:
                    if item not in existing:
                        ai_mem[key].append(item)
            # 딕셔너리 필드는 병합
            elif isinstance(ai_mem[key], dict) and isinstance(value, dict):
                ai_mem[key].update(value)
            # 그 외는 덮어쓰기
            else:
                ai_mem[key] = value
        else:
            ai_mem[key] = value
    
    d["participants"][uid]["ai_memory"] = ai_mem
    save_domain(channel_id, d)


def set_ai_memory_field(channel_id: str, user_id: str, field: str, value: Any) -> None:
    """AI 메모리의 특정 필드를 설정합니다."""
    d = get_domain(channel_id)
    uid = str(user_id)
    
    if uid not in d["participants"]:
        return
    
    if "ai_memory" not in d["participants"][uid]:
        d["participants"][uid]["ai_memory"] = {}
    
    d["participants"][uid]["ai_memory"][field] = value
    save_domain(channel_id, d)


def add_to_ai_memory_list(channel_id: str, user_id: str, field: str, item: str) -> bool:
    """AI 메모리의 리스트 필드에 항목을 추가합니다."""
    d = get_domain(channel_id)
    uid = str(user_id)
    
    if uid not in d["participants"]:
        return False
    
    if "ai_memory" not in d["participants"][uid]:
        d["participants"][uid]["ai_memory"] = {}
    
    if field not in d["participants"][uid]["ai_memory"]:
        d["participants"][uid]["ai_memory"][field] = []
    
    target_list = d["participants"][uid]["ai_memory"][field]
    if isinstance(target_list, list) and item not in target_list:
        target_list.append(item)
        save_domain(channel_id, d)
        return True
    
    return False


def remove_from_ai_memory_list(channel_id: str, user_id: str, field: str, item: str) -> bool:
    """AI 메모리의 리스트 필드에서 항목을 제거합니다."""
    d = get_domain(channel_id)
    uid = str(user_id)
    
    if uid not in d["participants"]:
        return False
    
    ai_mem = d["participants"][uid].get("ai_memory", {})
    target_list = ai_mem.get(field, [])
    
    if isinstance(target_list, list) and item in target_list:
        target_list.remove(item)
        save_domain(channel_id, d)
        return True
    
    return False


def get_economy(channel_id: str, user_id: str) -> Dict[str, Any]:
    """플레이어의 경제 정보(골드)를 가져옵니다."""
    p_data = get_participant_data(channel_id, user_id)
    if not p_data:
        return {}
    return p_data.get("economy", {})


def update_economy(channel_id: str, user_id: str, updates: Dict[str, Any]) -> None:
    """플레이어의 경제 정보를 업데이트합니다."""
    d = get_domain(channel_id)
    uid = str(user_id)
    
    if uid not in d["participants"]:
        return
    
    if "economy" not in d["participants"][uid]:
        d["participants"][uid]["economy"] = {}
    
    d["participants"][uid]["economy"].update(updates)
    save_domain(channel_id, d)


def get_unified_player_info(channel_id: str, user_id: str) -> str:
    """
    통합된 플레이어 정보를 반환합니다.
    서사 중심 - 골드/인벤토리 + AI 메모리
    """
    p_data = get_participant_data(channel_id, user_id)
    if not p_data:
        return "❌ 캐릭터 정보가 없습니다."
    
    mask = p_data.get("mask", "Unknown")
    economy = p_data.get("economy", {})
    inventory = p_data.get("inventory", {})
    effects = p_data.get("status_effects", [])
    ai_mem = p_data.get("ai_memory", {})
    
    result = f"## 🎭 **{mask}**\n\n"
    
    # === 경제/소지품 ===
    gold = economy.get('gold', 0)
    result += f"**💰 골드:** {gold}\n"
    
    # 인벤토리
    if inventory:
        inv_str = ", ".join([f"{k} x{v}" for k, v in inventory.items()])
        result += f"**🎒 인벤토리:** {inv_str}\n"
    else:
        result += "**🎒 인벤토리:** (비어있음)\n"
    
    # 상태이상
    if effects:
        result += f"**⚠️ 상태:** {', '.join(effects)}\n"
    
    result += "\n---\n"
    
    # === 서사 영역 (AI 관리) ===
    result += "**📝 캐릭터 서사**\n\n"
    
    # 외형
    appearance = ai_mem.get("appearance", "")
    if appearance:
        result += f"**👤 외형:** {appearance}\n"
    
    # 성격
    personality = ai_mem.get("personality", "")
    if personality:
        result += f"**💭 성격:** {personality}\n"
    
    # 배경
    background = ai_mem.get("background", "")
    if background:
        result += f"**📖 배경:** {background}\n"
    
    # 관계
    relationships = ai_mem.get("relationships", {})
    if relationships:
        result += "**🤝 관계:**\n"
        for name, desc in relationships.items():
            result += f"  • {name}: {desc}\n"
    
    # 패시브/칭호
    passives = ai_mem.get("passives", [])
    if passives:
        result += f"**🏆 패시브/칭호:** {', '.join(passives)}\n"
    
    # 알고 있는 정보
    known_info = ai_mem.get("known_info", [])
    if known_info:
        result += "**💡 알고 있는 것:**\n"
        for info in known_info[:5]:  # 최대 5개
            result += f"  • {info}\n"
    
    # 복선
    foreshadowing = ai_mem.get("foreshadowing", [])
    if foreshadowing:
        result += "**🔮 미해결 복선:**\n"
        for fs in foreshadowing[:3]:  # 최대 3개
            result += f"  • {fs}\n"
    
    # 비일상 적응
    normalization = ai_mem.get("normalization", {})
    if normalization:
        result += "**🌓 비일상 적응:**\n"
        for thing, status in normalization.items():
            result += f"  • {thing}: {status}\n"
    
    # 메모
    notes = ai_mem.get("notes", "")
    if notes:
        result += f"**📋 메모:** {notes}\n"
    
    return result


def get_ai_memory_for_prompt(channel_id: str, user_id: str) -> str:
    """
    AI에게 전달할 메모리 컨텍스트를 생성합니다.
    """
    ai_mem = get_ai_memory(channel_id, user_id)
    if not ai_mem:
        return ""
    
    parts = []
    
    if ai_mem.get("relationships"):
        rel_str = ", ".join([f"{k}({v})" for k, v in ai_mem["relationships"].items()])
        parts.append(f"관계: {rel_str}")
    
    if ai_mem.get("passives"):
        parts.append(f"패시브: {', '.join(ai_mem['passives'])}")
    
    if ai_mem.get("known_info"):
        parts.append(f"알고 있는 것: {', '.join(ai_mem['known_info'][:3])}")
    
    if ai_mem.get("normalization"):
        norm_str = ", ".join([f"{k}={v}" for k, v in ai_mem["normalization"].items()])
        parts.append(f"적응도: {norm_str}")
    
    if not parts:
        return ""
    
    return "### [PLAYER AI MEMORY]\n" + "\n".join(parts) + "\n"


# =========================================================
# 세션 레벨 AI 메모리 관리
# =========================================================
def get_session_ai_memory(channel_id: str) -> Dict[str, Any]:
    """세션 레벨 AI 메모리를 가져옵니다."""
    d = get_domain(channel_id)
    
    # 기본값 보정
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
    """세션 레벨 AI 메모리를 업데이트합니다."""
    d = get_domain(channel_id)
    
    if "ai_session_memory" not in d:
        d["ai_session_memory"] = {}
    
    # 업데이트 적용
    for key, value in updates.items():
        if isinstance(value, list) and isinstance(d["ai_session_memory"].get(key), list):
            # 리스트는 병합 (중복 제거)
            existing = d["ai_session_memory"].get(key, [])
            combined = existing + [v for v in value if v not in existing]
            d["ai_session_memory"][key] = combined[-20:]  # 최대 20개 유지
        elif isinstance(value, dict) and isinstance(d["ai_session_memory"].get(key), dict):
            # 딕셔너리는 병합
            d["ai_session_memory"][key].update(value)
        else:
            # 그 외는 덮어쓰기
            d["ai_session_memory"][key] = value
    
    import time
    d["ai_session_memory"]["last_updated"] = time.strftime('%Y-%m-%d %H:%M')
    save_domain(channel_id, d)


def get_full_ai_context(channel_id: str, user_id: str) -> str:
    """
    AI에게 전달할 통합 컨텍스트를 생성합니다.
    세션 메모리 + 플레이어 메모리 결합
    """
    result = ""
    
    # 1. 세션 레벨 메모리
    session_mem = get_session_ai_memory(channel_id)
    
    if session_mem.get("world_summary"):
        result += f"**세계 상황:** {session_mem['world_summary']}\n"
    
    if session_mem.get("current_arc"):
        result += f"**현재 스토리:** {session_mem['current_arc']}\n"
    
    if session_mem.get("active_threads"):
        result += f"**진행 중 이야기:** {', '.join(session_mem['active_threads'][:5])}\n"
    
    if session_mem.get("foreshadowing"):
        result += f"**미해결 복선:** {', '.join(session_mem['foreshadowing'][:3])}\n"
    
    if session_mem.get("npc_summaries"):
        npc_str = ", ".join([f"{k}({v})" for k, v in list(session_mem['npc_summaries'].items())[:5]])
        result += f"**주요 NPC:** {npc_str}\n"
    
    # 2. 플레이어 레벨 메모리
    player_mem = get_ai_memory(channel_id, user_id)
    if player_mem:
        if player_mem.get("relationships"):
            rel_str = ", ".join([f"{k}({v})" for k, v in player_mem["relationships"].items()])
            result += f"**관계:** {rel_str}\n"
        
        if player_mem.get("passives"):
            result += f"**패시브:** {', '.join(player_mem['passives'])}\n"
        
        if player_mem.get("known_info"):
            result += f"**알고 있는 정보:** {', '.join(player_mem['known_info'][:5])}\n"
        
        if player_mem.get("normalization"):
            norm_str = ", ".join([f"{k}={v}" for k, v in player_mem["normalization"].items()])
            result += f"**비일상 적응:** {norm_str}\n"
    
    if not result:
        return ""
    
    return "### [AI MEMORY CONTEXT]\n" + result + "\n"


def get_integrated_status(channel_id: str, user_id: str) -> str:
    """
    !정보 명령어용 통합 상태 출력
    서사 중심 - 경제/인벤토리 + AI 메모리
    """
    d = get_domain(channel_id)
    p_data = d["participants"].get(str(user_id))
    
    if not p_data:
        return "❌ 캐릭터 정보가 없습니다."
    
    result = f"# 📋 [{p_data.get('mask', 'Unknown')}] 상태\n\n"
    
    # === 1. 경제/소지품 ===
    result += "## 💰 소지품\n"
    
    economy = p_data.get("economy", {})
    gold = economy.get("gold", 0)
    result += f"  • 골드: {gold}\n"
    
    # 인벤토리
    inv = p_data.get("inventory", {})
    if inv:
        inv_str = ", ".join([f"{k} x{v}" for k, v in inv.items()])
        result += f"  • 인벤토리: {inv_str}\n"
    else:
        result += "  • 인벤토리: (비어있음)\n"
    
    # 상태이상
    effects = p_data.get("status_effects", [])
    if effects:
        result += f"\n## ⚠️ 상태이상\n"
        result += f"  {', '.join(effects)}\n"
    
    # === 2. AI 관리 영역 (서사) ===
    ai_mem = p_data.get("ai_memory", {})
    
    result += "\n## 🎭 캐릭터\n"
    if ai_mem.get("appearance"):
        result += f"  **외형:** {ai_mem['appearance']}\n"
    if ai_mem.get("personality"):
        result += f"  **성격:** {ai_mem['personality']}\n"
    if ai_mem.get("background"):
        result += f"  **배경:** {ai_mem['background']}\n"
    
    # 관계
    relationships = ai_mem.get("relationships", {})
    if relationships:
        result += "\n## 💞 관계\n"
        for name, desc in relationships.items():
            result += f"  • **{name}:** {desc}\n"
    
    # 패시브/칭호
    passives = ai_mem.get("passives", [])
    if passives:
        result += "\n## 🏆 패시브/칭호\n"
        for p in passives:
            result += f"  • {p}\n"
    
    # 알고 있는 정보
    known_info = ai_mem.get("known_info", [])
    if known_info:
        result += "\n## 💡 알고 있는 정보\n"
        for info in known_info[:5]:
            result += f"  • {info}\n"
    
    # 비일상 적응
    normalization = ai_mem.get("normalization", {})
    if normalization:
        result += "\n## 🌓 비일상 적응\n"
        for thing, status in normalization.items():
            result += f"  • **{thing}:** {status}\n"
    
    # 복선
    foreshadowing = ai_mem.get("foreshadowing", [])
    if foreshadowing:
        result += "\n## 🔮 미해결 복선\n"
        for fs in foreshadowing[:3]:
            result += f"  • {fs}\n"
    
    # 메모
    notes = ai_mem.get("notes", "")
    if notes:
        result += f"\n## 📝 메모\n  {notes}\n"
    
    return result


# =========================================================
# 세션 메모리 추가 함수
# =========================================================

def resolve_thread(channel_id: str, thread: str) -> bool:
    """스토리 스레드를 해결됨으로 이동합니다."""
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
    """주요 이벤트를 기록합니다."""
    d = get_domain(channel_id)
    world_state = d.get("world_state", {})
    day = world_state.get("day", 1)
    
    event_with_day = f"{day}일차: {event}"
    
    update_session_ai_memory(channel_id, {
        "key_events": [event_with_day]
    })
    return True


def get_session_ai_memory_for_prompt(channel_id: str) -> str:
    """AI 프롬프트에 전달할 형태로 세션 AI 메모리를 문자열로 변환합니다."""
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
