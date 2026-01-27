"""
Lorekeeper TRPG Bot - Game World Module
Handles World State, Time Flow, Weather, and Doom mechanics.
Extracted from game_system.py
"""

import logging
import random
import time
from typing import List, Tuple, Dict, Any

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
    try:
        current_idx = time_slots.index(current_slot)
    except ValueError:
        current_idx = 2 # Default to Afternoon

    # Time Tick Duration (7-10 mins) handling could be done here if we tracked real time, 
    # but this function advances the *slot*. The "Tick" logic usually calls this.
    # For now, we just advance the slot.

    next_idx = current_idx + 1
    
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
    
    # Doom 체크
    doom_increase, doom_reasons = calculate_doom_increase(channel_id, world, next_idx, time_slots)
    if doom_increase > 0:
        current_doom = world.get("doom", 0)
        world["doom"] = min(config.DOOM_MAX, current_doom + doom_increase)
        for reason in doom_reasons:
            if "위험 지역" in reason or "로어 규칙" in reason:
                 msg += f"\n⚠️ **경고:** {reason}"
            else:
                 msg += f"\n⚠️ {reason}"

    domain_manager.update_world_state(channel_id, world)
    return msg

# =========================================================
# DOOM SYSTEM
# =========================================================

def calculate_doom_increase(channel_id: str, world: dict, next_idx: int, time_slots: list) -> Tuple[int, List[str]]:
    doom_increase = 0
    doom_reasons = []
    
    # 1. Time Check
    is_night_time = next_idx >= len(time_slots) - 2
    if "황혼" in world.get("time_slot", ""):
        is_night_time = True
    
    if is_night_time:
        doom_increase += config.DOOM_INCREASE_NIGHT
    
    # 2. Nemesis Check
    domain = domain_manager.get_domain(channel_id)
    participants = domain.get("participants", {})
    nemesis_detected = False
    for uid, p in participants.items():
        if p.get("status") == "left": continue
        ai_mem = p.get("ai_memory", {})
        rels = ai_mem.get("relationships", {})
        for npc_name, score in rels.items():
            try:
                score_val = int(score)
            except (ValueError, TypeError):
                continue # Skip invalid scores
                
            if score_val <= config.NEMESIS_THRESHOLD:
                nemesis_detected = True; break
        if nemesis_detected: break
    
    if nemesis_detected:
        doom_increase += random.randint(config.DOOM_INCREASE_NEMESIS_MIN, config.DOOM_INCREASE_NEMESIS_MAX)
        doom_reasons.append("👿 적대 세력")
    
    
    # 3. AI Risk Level
    ai_risk = world.get("risk_level", "None").lower()
    location = world.get("current_location", "Unknown")
    
    if "high" in ai_risk or "extreme" in ai_risk:
        doom_increase += config.DOOM_INCREASE_HIGH_RISK
        doom_reasons.append(f"💀 위험 지역({location}): 고위험 감지")
    elif "medium" in ai_risk:
        doom_increase += config.DOOM_INCREASE_MEDIUM_RISK
        doom_reasons.append(f"⚠️ 위험 지역({location}): 주의 필요")

    # 4. Participant Status Severity (Restored V2 Feature)
    import game_character
    participants = domain.get("participants", {})
    for uid, p in participants.items():
        if p.get("status") != "active": continue
        
        severity_doom, sev_reasons = game_character.calculate_status_doom_contribution(p)
        if severity_doom > 0:
            doom_increase += severity_doom
            p_name = p.get("mask", "Unknown")
            doom_reasons.append(f"🩸 {p_name}: {', '.join(sev_reasons)}")
        
    # 4. Lore Rules
    loc_rules = world.get("location_rules", {})
    for loc_name, rule in loc_rules.items():
        if loc_name.lower() in location.lower():
            condition = rule.get("condition", "").lower()
            should_apply = False
            if "night" in condition and is_night_time: should_apply = True
            elif "always" in condition: should_apply = True
            
            if should_apply and "high" not in ai_risk:
                doom_increase += config.DOOM_INCREASE_LORE_RULE
                doom_reasons.append(f"📜 로어 규칙({loc_name})")
                
    return doom_increase, doom_reasons

def reduce_doom(channel_id: str, amount: int, reason: str = "") -> str:
    """Doom 수치 감소 (최소 0)"""
    return change_doom(channel_id, -amount)

def change_doom(channel_id: str, delta: int) -> str:
    world = domain_manager.get_world_state(channel_id)
    old = world.get("doom", 0)
    new_val = max(0, min(config.DOOM_MAX, old + delta))
    
    if old == new_val:
        return ""
        
    world["doom"] = new_val
    domain_manager.update_world_state(channel_id, world)
    
    icon = "📈" if delta > 0 else "📉"
    desc = _get_doom_description(new_val)
    return f"{icon} **위기 수치 변경:** {old}% -> {new_val}% ({desc})"

def _get_doom_description(doom: int) -> str:
    if doom >= config.DOOM_MAX: return "💥 파멸 💥"
    elif doom >= config.DOOM_THRESHOLD_CRITICAL: return "절망적"
    elif doom >= config.DOOM_THRESHOLD_DANGER: return "임박한 위협"
    elif doom >= config.DOOM_THRESHOLD_WARNING: return "불길한 징조"
    else: return "평온함"


def get_world_context(channel_id: str) -> str:
    world = domain_manager.get_world_state(channel_id)
    if not world: return ""
    
    party_context = domain_manager.get_party_status_context(channel_id)
    location = world.get("current_location", "Unknown")
    
    return (
        f"[현재 세계 상태]\n"
        f"- 위치: {location}\n"
        f"- 위험도: {world.get('risk_level', 'None')}\n"
        f"- 시간: {world.get('day', 1)}일차, {world.get('time_slot', '오후')}\n"
        f"- 날씨: {world.get('weather', '맑음')}\n"
        f"- 위기 수치: {world.get('doom', 0)}% ({_get_doom_description(world.get('doom', 0))})\n"
        f"- **파티 분위기**: {party_context}\n"
        f"*지침: 이 위치, 시간, 위기 수치, 파티 상태를 반영하여 서술 톤을 조절하십시오.*"
    )

def _get_doom_bar(value: int, length: int = 10) -> str:
    # [████░░░░░░]
    fill = int(value / config.DOOM_MAX * length)
    bar = "█" * fill + "░" * (length - fill)
    return f"[{bar}]"

def get_doom_forecast(channel_id: str) -> str:
    world = domain_manager.get_world_state(channel_id)
    current = world.get("doom", 0)
    desc = _get_doom_description(current)
    bar = _get_doom_bar(current)
    
    msg = f"🛡️ **위기 예보**\n{bar} {current}% ({desc})\n"
    
    if current >= config.DOOM_THRESHOLD_CRITICAL:
        msg += "⚠️ **경고:** 파멸이 임박했습니다. 모든 행동에 위험이 따릅니다."
    elif current >= config.DOOM_THRESHOLD_DANGER:
        msg += "⚠️ **주의:** 세계의 적의가 느껴집니다."
    else:
        msg += "✅ 아직은 안전합니다."
        
    return msg
