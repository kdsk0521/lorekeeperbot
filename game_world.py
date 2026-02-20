"""
Lorekeeper TRPG Bot - Game World Module
Handles World State, Time Flow, Weather, and Doom mechanics.
Extracted from game_system.py
"""

import logging
import random
from typing import List, Dict, Any, Optional

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

def increment_turn_index(channel_id: str, delta: int = 1) -> int:
    """Turn index increments once per UNE run/batch/observation."""
    world = domain_manager.get_world_state(channel_id)
    current = world.get("turn_index", 0)
    try:
        current = int(current)
    except (TypeError, ValueError):
        current = 0
    step = max(0, int(delta))
    world["turn_index"] = current + step
    domain_manager.update_world_state(channel_id, world)
    return world["turn_index"]

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

def _format_doom_clocks(world: Dict[str, Any], limit: int = 3) -> str:
    clocks = world.get("doom_clocks", [])
    if not isinstance(clocks, list) or not clocks:
        return "None"

    items = []
    for clock in clocks:
        if not isinstance(clock, dict):
            continue
        if clock.get("resolved"):
            continue
        name = clock.get("name", "Unnamed")
        seg = int(clock.get("segments", 4) or 4)
        prog = int(clock.get("filled", clock.get("progress", 0)) or 0)
        items.append(f"{name} ({prog}/{seg})")
        if len(items) >= limit:
            break

    return ", ".join(items) if items else "None"


def _get_active_player_masks(channel_id: str) -> List[str]:
    participants = domain_manager.get_domain(channel_id).get("participants", {})
    if not isinstance(participants, dict):
        return []
    masks: List[str] = []
    for p_data in participants.values():
        if not isinstance(p_data, dict):
            continue
        if p_data.get("status") != "active":
            continue
        mask = str(p_data.get("mask", "")).strip()
        if mask:
            masks.append(mask)
    return masks


def _get_status_target_participant(channel_id: str, user_id: str = "") -> Dict[str, Any]:
    participants = domain_manager.get_domain(channel_id).get("participants", {})
    if not isinstance(participants, dict):
        return {}

    if user_id:
        direct = participants.get(user_id)
        if isinstance(direct, dict) and direct.get("status") == "active":
            return direct

    for p_data in participants.values():
        if isinstance(p_data, dict) and p_data.get("status") == "active":
            return p_data
    return {}


def build_real_time_display(
    channel_id: str,
    user_id: str = "",
    active_modules: Optional[List[str]] = None,
) -> str:
    """Build compact v3 real-time status lines for prompt slot 29."""
    world = domain_manager.get_world_state(channel_id)
    modules = active_modules if isinstance(active_modules, list) else domain_manager.get_active_modules(channel_id)
    module_set = set(modules or [])
    lines: List[str] = []

    location = world.get("current_location") or world.get("location", "Unknown")
    day = world.get("day", "?")
    time_slot = world.get("time_slot", "Unknown")
    present = _get_active_player_masks(channel_id)
    present_text = ", ".join(present) if present else "None"
    lines.append(f"위치 {location} | 시간 {day}일차 {time_slot} | 인물 {present_text}")

    line2_parts: List[str] = []
    if "mental" in module_set:
        target = _get_status_target_participant(channel_id, user_id)
        mem = target.get("ai_memory", {}) if isinstance(target, dict) else {}
        legacy_mental = mem.get("mental", {}) if isinstance(mem, dict) else {}
        vigor_src = mem.get("vigor", legacy_mental) if isinstance(mem, dict) else {}
        composure_src = mem.get("composure", legacy_mental) if isinstance(mem, dict) else {}
        vigor_val = int(vigor_src.get("value", 100) or 100)
        composure_val = int(composure_src.get("value", 100) or 100)
        line2_parts.append(f"기력 {vigor_val}")
        line2_parts.append(f"평정 {composure_val}")

    if "doom" in module_set:
        doom_val = int(world.get("doom", 0) or 0)
        line2_parts.append(f"Doom {doom_val}")

    if line2_parts:
        lines.append(" | ".join(line2_parts))

    if "doom" in module_set:
        clocks = world.get("doom_clocks", [])
        if isinstance(clocks, list):
            clock_parts: List[str] = []
            for clock in clocks:
                if not isinstance(clock, dict):
                    continue
                if clock.get("resolved"):
                    continue
                name = str(clock.get("name", "Clock")).strip()
                segments = int(clock.get("segments", 4) or 4)
                filled = int(clock.get("filled", clock.get("progress", 0)) or 0)
                tick_mode = str(clock.get("tick_mode", "")).lower()
                tick_mark = " ⏱" if tick_mode in ("time", "hybrid") else ""
                clock_parts.append(f"[{name} {filled}/{segments}{tick_mark}]")
            if clock_parts:
                lines.append(" ".join(clock_parts))

    return "\n".join(lines).strip()

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
        f"- 위협 시계: {_format_doom_clocks(world)}",
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
    clocks_txt = _format_doom_clocks(world, limit=5)
    if clocks_txt and clocks_txt != "None":
        msg += f"\n⏰ **위협 시계**: {clocks_txt}\n"
        # 임박한 시계 경고
        for c in world.get("doom_clocks", []):
            if not isinstance(c, dict) or c.get("resolved"):
                continue
            seg = int(c.get("segments", 4) or 4)
            filled = int(c.get("filled", c.get("progress", 0)) or 0)
            remaining = seg - filled
            if 0 < remaining <= 2:
                msg += f"⚠️ **임박: {c.get('name', '?')}** — {remaining}칸 남음!\n"

    if current >= config.DOOM_THRESHOLD_CRITICAL:
        msg += "⚠️ **경고:** 파멸이 임박했습니다. 모든 행동에 위험이 따릅니다."
    elif current >= config.DOOM_THRESHOLD_DANGER:
        msg += "⚠️ **주의:** 세계의 적의가 느껴집니다."
    else:
        msg += "✅ 아직은 안전합니다."

    return msg


def resolve_clock_by_quest(channel_id: str, clock_name: str) -> str:
    """퀘스트 완료 → 연결된 시계 서사적 해결 + doom 하강."""
    world = domain_manager.get_world_state(channel_id)
    clocks = world.get("doom_clocks", [])
    if not isinstance(clocks, list):
        return ""
    for clock in clocks:
        if not isinstance(clock, dict):
            continue
        if clock.get("name") == clock_name and not clock.get("resolved"):
            clock["resolved"] = True
            seg = int(clock.get("segments", 6) or 6)
            bonus_doom = config.CLOCK_RESOLVE_DOOM.get(seg, -10)
            world["doom"] = max(0, min(100, world.get("doom", 0) + bonus_doom))
            world["doom_clocks"] = clocks
            domain_manager.update_world_state(channel_id, world)
            return f"✅ **시계 해결: {clock_name}** (긴장도 {bonus_doom})"
    return ""

