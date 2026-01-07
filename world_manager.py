"""
Lorekeeper TRPG Bot - World Manager Module
시간, 날씨, 위기 수치 등 세계 상태를 관리합니다.
"""

import random
from typing import List, Dict, Any, Optional

import domain_manager

# =========================================================
# 상수 정의
# =========================================================
DEFAULT_TIME_SLOTS = ["새벽", "오전", "오후", "황혼", "저녁", "심야"]
DEFAULT_WEATHER_TYPES = ["맑음", "구름 조금", "흐림", "비", "안개", "폭풍우"]

# 위기 수치 임계값
DOOM_THRESHOLD_WARNING = 30
DOOM_THRESHOLD_DANGER = 70
DOOM_THRESHOLD_CRITICAL = 90
DOOM_MAX = 100

# 위험도별 doom 증가량
DOOM_INCREASE_NIGHT = 1
DOOM_INCREASE_NEMESIS_MIN = 1
DOOM_INCREASE_NEMESIS_MAX = 2
DOOM_INCREASE_HIGH_RISK = 3
DOOM_INCREASE_MEDIUM_RISK = 2
DOOM_INCREASE_LORE_RULE = 1

# 적대 관계 임계값
NEMESIS_THRESHOLD = -10


# =========================================================
# 시간 및 날씨 설정 함수
# =========================================================
def get_time_slots(channel_id: str) -> List[str]:
    """시간대 목록을 반환합니다."""
    return DEFAULT_TIME_SLOTS


def get_weather_types(channel_id: str) -> List[str]:
    """날씨 타입 목록을 반환합니다."""
    return DEFAULT_WEATHER_TYPES


# =========================================================
# 시간 진행
# =========================================================
def advance_time(channel_id: str) -> str:
    """
    시간을 한 단계 진행합니다.
    밤이 되면 다음 날로 넘어가고 날씨가 변경됩니다.
    
    Args:
        channel_id: 채널 ID
    
    Returns:
        상태 메시지
    """
    world = domain_manager.get_world_state(channel_id)
    if not world:
        return "⚠️ 데이터 없음"
    
    time_slots = get_time_slots(channel_id)
    weather_types = get_weather_types(channel_id)
    
    # 현재 시간대 인덱스 찾기
    current_slot = world.get("time_slot", time_slots[1])
    try:
        current_idx = time_slots.index(current_slot)
    except ValueError:
        current_idx = 0
    
    msg = ""
    next_idx = current_idx + 1
    
    # 자정이 지나면 다음 날로
    if next_idx >= len(time_slots):
        world["time_slot"] = time_slots[0]
        world["day"] = world.get("day", 1) + 1
        new_weather = random.choice(weather_types)
        world["weather"] = new_weather
        msg = f"🌙 밤이 지나고 **{world['day']}일차 {time_slots[0]}**이 되었습니다. (날씨: {new_weather})"
    else:
        world["time_slot"] = time_slots[next_idx]
        msg = f"🕰️ 시간이 흘러 **{world['time_slot']}**가 되었습니다."
    
    # Doom 증가 계산
    doom_increase = 0
    doom_reasons = []
    
    # 1. 시간대 체크 (밤/황혼)
    is_night_time = next_idx >= len(time_slots) - 2
    if "황혼" in world["time_slot"]:
        is_night_time = True
    
    if is_night_time:
        doom_increase += DOOM_INCREASE_NIGHT
        if "황혼" in world["time_slot"]:
            msg += " (🌅 해가 저물며 그림자가 길어집니다...)"
        else:
            msg += " (🌑 어둠이 짙어집니다...)"
    
    # 2. 관계도 체크 (적대적 관계)
    domain = domain_manager.get_domain(channel_id)
    participants = domain.get("participants", {})
    nemesis_detected = False
    
    for uid, p in participants.items():
        if p.get("status") == "left":
            continue
        
        rels = p.get("relations", {})
        for npc_name, score in rels.items():
            if score <= NEMESIS_THRESHOLD:
                nemesis_detected = True
                break
        
        if nemesis_detected:
            break
    
    if nemesis_detected:
        doom_increase += random.randint(DOOM_INCREASE_NEMESIS_MIN, DOOM_INCREASE_NEMESIS_MAX)
        doom_reasons.append("👿 적대 세력")
    
    # 3. 실시간 위험도 (AI 판단)
    ai_risk = world.get("risk_level", "None").lower()
    location = world.get("current_location", "Unknown")
    
    if "high" in ai_risk or "extreme" in ai_risk:
        doom_increase += DOOM_INCREASE_HIGH_RISK
        doom_reasons.append(f"💀 위험 지역({location}): 고위험 감지")
    elif "medium" in ai_risk:
        doom_increase += DOOM_INCREASE_MEDIUM_RISK
        doom_reasons.append(f"⚠️ 위험 지역({location}): 주의 필요")
    
    # 4. 정적 규칙 (Lore 기반)
    loc_rules = world.get("location_rules", {})
    for loc_name, rule in loc_rules.items():
        if loc_name.lower() in location.lower():
            condition = rule.get("condition", "").lower()
            
            should_apply = False
            if "night" in condition and is_night_time:
                should_apply = True
            elif "always" in condition:
                should_apply = True
            
            # 이미 AI 위험도에서 처리된 경우 중복 방지
            if should_apply and "high" not in ai_risk:
                doom_increase += DOOM_INCREASE_LORE_RULE
                doom_reasons.append(f"📜 로어 규칙({loc_name})")
    
    # Doom 업데이트
    if doom_increase > 0:
        current_doom = world.get("doom", 0)
        world["doom"] = min(DOOM_MAX, current_doom + doom_increase)
        
        for reason in doom_reasons:
            if "위험 지역" in reason or "로어 규칙" in reason:
                msg += f"\n⚠️ **경고:** {reason}"
    
    domain_manager.update_world_state(channel_id, world)
    return msg


# =========================================================
# 위기 수치 관리
# =========================================================
def change_doom(channel_id: str, amount: int) -> str:
    """
    위기 수치를 변경합니다.
    
    Args:
        channel_id: 채널 ID
        amount: 변화량 (양수: 증가, 음수: 감소)
    
    Returns:
        상태 메시지
    """
    world = domain_manager.get_world_state(channel_id)
    current = world.get("doom", 0)
    new_val = max(0, min(DOOM_MAX, current + amount))
    world["doom"] = new_val
    domain_manager.update_world_state(channel_id, world)
    
    # 위기 단계 설명
    doom_desc = _get_doom_description(new_val)
    
    return f"📉 **위기 수치 변경:** {current}% -> {new_val}% ({doom_desc})"


def _get_doom_description(doom_value: int) -> str:
    """위기 수치에 따른 설명을 반환합니다."""
    if doom_value >= DOOM_MAX:
        return "💥 파멸 💥"
    elif doom_value >= DOOM_THRESHOLD_CRITICAL:
        return "절망적"
    elif doom_value >= DOOM_THRESHOLD_DANGER:
        return "임박한 위협"
    elif doom_value >= DOOM_THRESHOLD_WARNING:
        return "불길한 징조"
    else:
        return "평온함"


# =========================================================
# 세계 상태 컨텍스트
# =========================================================
def get_world_context(channel_id: str) -> str:
    """
    AI에게 전달할 세계 상태 컨텍스트를 생성합니다.
    
    Args:
        channel_id: 채널 ID
    
    Returns:
        세계 상태 컨텍스트 문자열
    """
    world = domain_manager.get_world_state(channel_id)
    if not world:
        return ""
    
    party_context = domain_manager.get_party_status_context(channel_id)
    
    # 기본값 처리
    location = world.get("current_location", "Unknown")
    day = world.get("day", 1)
    time_slot = world.get("time_slot", "오후")
    weather = world.get("weather", "맑음")
    doom = world.get("doom", 0)
    doom_name = world.get("doom_name", "위기")
    risk_level = world.get("risk_level", "None")
    
    doom_desc = _get_doom_description(doom)
    
    return (
        f"[Current World State]\n"
        f"- Location: {location}\n"
        f"- Risk Level: {risk_level}\n"
        f"- Time: Day {day}, {time_slot}\n"
        f"- Weather: {weather}\n"
        f"- Doom Level: {doom}% ({doom_desc})\n"
        f"- **Atmosphere Context**: {party_context}\n"
        f"*Instruction: Adjust the narrative tone based on Location, Time, Doom, and Party Condition.*"
    )


# =========================================================
# 추가 유틸리티
# =========================================================
def get_current_time_info(channel_id: str) -> Dict[str, Any]:
    """현재 시간 정보를 딕셔너리로 반환합니다."""
    world = domain_manager.get_world_state(channel_id)
    
    return {
        "day": world.get("day", 1),
        "time_slot": world.get("time_slot", "오후"),
        "weather": world.get("weather", "맑음"),
        "is_night": world.get("time_slot", "오후") in ["황혼", "저녁", "심야"]
    }


def get_doom_status(channel_id: str) -> Dict[str, Any]:
    """위기 수치 상태를 딕셔너리로 반환합니다."""
    world = domain_manager.get_world_state(channel_id)
    doom = world.get("doom", 0)
    
    return {
        "value": doom,
        "description": _get_doom_description(doom),
        "is_critical": doom >= DOOM_THRESHOLD_CRITICAL,
        "is_danger": doom >= DOOM_THRESHOLD_DANGER
    }
