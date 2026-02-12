"""
Lorekeeper TRPG Bot - Game World Module
Handles World State, Time Flow, Weather, and Doom mechanics.
Extracted from game_system.py
"""

import logging
import random
from typing import List, Dict, Any

import config
import domain_manager

# =========================================================
# WORLD TIME & WEATHER
# =========================================================

def get_time_slots(channel_id: str) -> List[str]:
    return config.DEFAULT_TIME_SLOTS

def get_weather_types(channel_id: str) -> List[str]:
    return config.DEFAULT_WEATHER_TYPES

def advance_time(channel_id: str) -> str:
    """시간을 다음 슬롯으로 진행하고 세계 변화를 반환"""
    world = domain_manager.get_world_state(channel_id)
    time_slots = get_time_slots(channel_id)
    
    current_slot = world.get("time_slot", "오후")
    current_day = world.get("day", 1)
    current_weather = world.get("weather", "맑음")

    try:
        current_idx = time_slots.index(current_slot)
    except ValueError:
        current_idx = 2 # Default to Afternoon

    next_idx = current_idx + 1

    # 이전 시간 상태 기록 (AI 시간 전환 서술에 활용)
    world["last_temporal_context"] = {
        "prev_time_slot": current_slot,
        "prev_day": current_day,
        "prev_weather": current_weather
    }
    
    # 이모지 매핑
    time_emoji = {
        "새벽": "🌅", "오전": "☀️", "오후": "🌤️",
        "황혼": "🌆", "저녁": "🌙", "심야": "🌑"
    }
    
    msg = ""
    
    if next_idx >= len(time_slots):
        # 날짜 변경
        world["time_slot"] = time_slots[0]
        world["day"] = world.get("day", 1) + 1
        new_weather = random.choice(get_weather_types(channel_id))
        world["weather"] = new_weather
        
        emoji = time_emoji.get(time_slots[0], "🌅")
        msg = (
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"🌙 **밤이 지나고...**\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"📅 **{world['day']}일차** {emoji} **{time_slots[0]}**\n"
            f"🌤️ 날씨: {new_weather}\n"
            f"━━━━━━━━━━━━━━━━━━━━"
        )
    else:
        world["time_slot"] = time_slots[next_idx]
        emoji = time_emoji.get(time_slots[next_idx], "⏰")
        
        # 시간대별 분위기 메시지
        atmosphere = {
            "새벽": "동이 트기 시작합니다...",
            "오전": "아침 햇살이 비춥니다.",
            "오후": "태양이 중천에 떠 있습니다.",
            "황혼": "해가 저물어갑니다...",
            "저녁": "어둠이 내려앉습니다.",
            "심야": "깊은 밤이 찾아왔습니다..."
        }
        atm = atmosphere.get(time_slots[next_idx], "")
        
        msg = f"{emoji} **{time_slots[next_idx]}** — {atm}"
    
    domain_manager.update_world_state(channel_id, world)
    return msg

def advance_tick(channel_id: str, ticks: int = 1) -> str:
    """1틱(5-10분) 단위 시간 경과. 시간 슬롯은 변경하지 않음."""
    world = domain_manager.get_world_state(channel_id)
    current_ticks = world.get("ticks_in_slot", 0)
    world["ticks_in_slot"] = current_ticks + ticks

    minutes = ticks * random.randint(5, 10)
    domain_manager.update_world_state(channel_id, world)
    return f"⏳ {minutes}분이 흘렀다..."

# =========================================================
# DOOM SYSTEM
# =========================================================

def change_doom(channel_id: str, amount: int) -> str:
    """
    위기 수치를 조정하고 메시지를 반환합니다.
    """
    world = domain_manager.get_world_state(channel_id)
    old_val = world.get("doom", 0)
    new_val = max(0, min(100, old_val + amount))
    
    if old_val == new_val:
        return "" # No change
        
    world["doom"] = new_val
    domain_manager.update_world_state(channel_id, world)
    
    # Emoji feedback
    emoji = "📈" if amount > 0 else "📉"
    
    # Check Thresholds
    diff_msg = ""
    # Critical Transition
    if old_val < config.DOOM_THRESHOLD_CRITICAL <= new_val:
        diff_msg = "\n⚠️ **[경고] 파멸이 임박했습니다!**"
    elif old_val < config.DOOM_THRESHOLD_DANGER <= new_val:
        diff_msg = "\n⚠️ **[주의] 위험도가 상승했습니다.**"
        
    return f"{emoji} **위기 수치:** {old_val}% → **{new_val}%** {diff_msg}"


def get_doom_info(value: int, genre: str = None) -> Dict[str, Any]:
    stages = config.get_genre_doom_stages(genre) if genre else config.DOOM_STAGES
    for stage_id, info in stages.items():
        low, high = info["range"]
        if low <= value < high:
            return info
    return stages[max(stages.keys())]

def _get_doom_description(doom: int) -> str:
    # Wrapper for legacy compatibility if needed, or internal use
    info = get_doom_info(doom)
    return f"{info['emoji']} {info['name']}"

def get_world_context(channel_id: str) -> str:
    world = domain_manager.get_world_state(channel_id)
    if not world: return ""
    
    party_context = domain_manager.get_party_status_context(channel_id)
    location = world.get("current_location", "Unknown")
    
    lines = [
        f"[현재 세계 상태]",
        f"- 위치: {location}",
        f"- 위험도: {world.get('risk_level', 'None')}",
        f"- 시간: {world.get('day', 1)}일차, {world.get('time_slot', '오후')}",
        f"- 날씨: {world.get('weather', '맑음')}",
        f"- 위기 수치: {world.get('doom', 0)}% ({_get_doom_description(world.get('doom', 0))})",
        f"- **파티 분위기**: {party_context}",
    ]

    # 시간 전환 컨텍스트 (방금 시간이 바뀌었을 때 서술 참고용)
    ltc = world.get("last_temporal_context", {})
    if ltc and ltc.get("prev_time_slot"):
        current_slot = world.get("time_slot", "")
        if ltc["prev_time_slot"] != current_slot:
            lines.append(f"- **시간 전환**: {ltc['prev_time_slot']} → {current_slot} (전환 직후, 분위기 변화를 자연스럽게 묘사)")

    # 세계 제약 (로어에서 추출된 시스템/사회 규칙)
    wc = world.get("world_constraints", {})
    if wc:
        wc_parts = []
        if wc.get("systems"):
            wc_parts.append(f"체계: {wc['systems']}")
        if wc.get("social"):
            wc_parts.append(f"사회: {wc['social']}")
        taboos = wc.get("taboos", [])
        if taboos:
            wc_parts.append(f"금기: {', '.join(taboos)}")
        if wc_parts:
            lines.append(f"- **세계 규칙**: {' | '.join(wc_parts)}")

    lines.append("*지침: 이 위치, 시간, 위기 수치, 파티 상태를 반영하여 서술 톤을 조절하십시오.*")
    return "\n".join(lines)

def _get_doom_bar(value: int, length: int = 10) -> str:
    # [████░░░░░░]
    fill = int(value / config.DOOM_MAX * length)
    bar = "█" * fill + "░" * (length - fill)
    return f"[{bar}]"

def get_doom_forecast(channel_id: str) -> str:
    world = domain_manager.get_world_state(channel_id)
    current = world.get("doom", 0)
    info = get_doom_info(current)
    
    # Hide Numbers, Show Bar + Description
    bar = _get_doom_bar(current)
    
    msg = f"🛡️ **위기 예보**\n{bar} {info['emoji']} **{info['name']}**\n"
    
    if current >= config.DOOM_THRESHOLD_CRITICAL:
        msg += "⚠️ **경고:** 파멸이 임박했습니다. 모든 행동에 위험이 따릅니다."
    elif current >= config.DOOM_THRESHOLD_DANGER:
        msg += "⚠️ **주의:** 세계의 적의가 느껴집니다."
    else:
        msg += "✅ 아직은 안전합니다."
        
    return msg

