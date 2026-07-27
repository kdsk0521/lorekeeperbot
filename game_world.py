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


def _slot_for_hour(hour: int) -> str:
    """시각(0-23)에 해당하는 시간 슬롯 반환."""
    for slot, (start, end) in config.TIME_SLOT_HOURS.items():
        if start <= end:
            if start <= hour <= end:
                return slot
        else:  # 심야 wrap (23~3)
            if hour >= start or hour <= end:
                return slot
    return "오후"


def _init_clock(world: dict) -> None:
    """hour/minute/year/month 미초기화 시 time_slot 기반으로 설정.
    V8.5 (2026-05-23): 캘린더 확장. day=N → year/month/day 자동 마이그레이션."""
    # 1. hour/minute 초기화
    if "hour" not in world:
        slot = world.get("time_slot", "오후")
        hours = config.TIME_SLOT_HOURS.get(slot, (12, 16))
        start = hours[0]
        world["hour"] = start
        world["minute"] = 0

    # 2. 캘린더 마이그레이션 (year/month 미설정 시 day=N → year/month/day_in_month 자동 분해)
    if "year" not in world or "month" not in world:
        legacy_day = world.get("day", 1)  # 기존 *N일차*
        # day=N → year, month, day_in_month
        # day=1 → 1년 1월 1일 / day=31 → 1년 2월 1일 / day=361 → 2년 1월 1일
        zero_idx = max(0, legacy_day - 1)  # 0-based
        days_per_year = config.CALENDAR_DAYS_PER_YEAR  # 360
        days_per_month = config.CALENDAR_DAYS_PER_MONTH  # 30
        year_offset = zero_idx // days_per_year
        rem_after_year = zero_idx % days_per_year
        month_offset = rem_after_year // days_per_month
        day_in_month = rem_after_year % days_per_month + 1  # 1-based
        world["year"] = 1 + year_offset
        world["month"] = 1 + month_offset
        world["day"] = day_in_month  # 이제 day = day_in_month (1~30)


def _wrap_calendar(world: dict) -> None:
    """day가 CALENDAR_DAYS_PER_MONTH 초과 시 month/year로 wrap. advance_minutes 후 호출."""
    days_per_month = config.CALENDAR_DAYS_PER_MONTH
    months_per_year = config.CALENDAR_MONTHS_PER_YEAR
    while world.get("day", 1) > days_per_month:
        world["day"] -= days_per_month
        world["month"] = world.get("month", 1) + 1
    while world.get("month", 1) > months_per_year:
        world["month"] -= months_per_year
        world["year"] = world.get("year", 1) + 1


def format_calendar(world: dict) -> str:
    """한국식 캘린더 표시 (N년 M월 D일). V8.5."""
    return f"{world.get('year', 1)}년 {world.get('month', 1)}월 {world.get('day', 1)}일"


def get_formatted_time(channel_id: str) -> str:
    """현재 시각을 'HH:MM' 형식으로 반환."""
    world = domain_manager.get_world_state(channel_id)
    _init_clock(world)
    return f"{world['hour']:02d}:{world['minute']:02d}"


def advance_minutes(channel_id: str, minutes: int) -> str:
    """지정 분만큼 시간 경과. 슬롯 전환/날짜 변경 자동 처리.

    [2026-07-15 D1] 여기서 장면 경과(scene_elapsed_min)도 누적한다. 세계시계의
    단일 깔때기이기 때문(advance_tick·process_time_flow 전부 여기로 위임).

    ★왜 턴이 아니라 분인가 (레티어스: "세계의 시간과 사람의 시간이 다르다"):
      사람의 시간 = turn_index, 매 턴 무조건 +1.
      세계의 시간 = 여기. theoria의 time_flow.ticks가 **유저 입력에서만** 추출돼
                    (SOURCE GATE) 흘러든다. 1 tick = 5~10분.
      → 칼싸움 20턴 = 20턴/ticks≈0 → 세계시계 정지 → 노화 없음(자동으로 옳다).
        "한참 후" 1턴 = 1턴/ticks 20 → 100~200분 → 한 턴에 크게 늙는다.
      턴 기반이면 3분짜리 칼싸움 도중에 찻물이 식는다. 분 기반이면 예외 처리가
      아예 필요 없다. 팽창 케이스가 공짜로 딸려온다.
    """
    world = domain_manager.get_world_state(channel_id)
    _init_clock(world)

    try:
        world["scene_elapsed_min"] = int(world.get("scene_elapsed_min", 0) or 0) + max(0, int(minutes))
    except (TypeError, ValueError):
        world["scene_elapsed_min"] = max(0, int(minutes or 0))

    old_hour = world["hour"]
    old_slot = world.get("time_slot", _slot_for_hour(old_hour))
    old_weather = world.get("weather", "맑음")

    # 게임 날짜 경계 = 첫 슬롯 시작 시각 (새벽 04:00)
    time_slots = get_time_slots(channel_id)
    day_start_hour = config.TIME_SLOT_HOURS.get(time_slots[0], (4, 6))[0]

    game_min = ((world["hour"] - day_start_hour) % 24) * 60 + world["minute"]
    total_game_min = game_min + minutes
    new_day_offset = total_game_min // 1440
    remainder = total_game_min % 1440

    world["hour"] = (remainder // 60 + day_start_hour) % 24
    world["minute"] = remainder % 60

    new_slot = _slot_for_hour(world["hour"])
    world["time_slot"] = new_slot

    if new_day_offset > 0:
        world["day"] = world.get("day", 1) + new_day_offset
        world["weather"] = random.choice(get_weather_types(channel_id))
        # V8.5: day→month→year wrap (캘린더 확장)
        _wrap_calendar(world)

    # 슬롯 전환 시 last_temporal_context 기록
    if old_slot != new_slot:
        world["last_temporal_context"] = {
            "prev_time_slot": old_slot,
            "prev_day": world.get("day", 1) - new_day_offset,
            "prev_weather": old_weather,
        }

    domain_manager.update_world_state(channel_id, world)

    time_str = f"{world['hour']:02d}:{world['minute']:02d}"
    if new_day_offset > 0:
        return f"📅 {format_calendar(world)} {time_str} ({new_slot})"
    if old_slot != new_slot:
        return f"⏰ {time_str} ({new_slot})"
    return f"⏳ {time_str}"


def advance_to_slot(channel_id: str, target_slot: str, day_offset: int = 0,
                     target_hour: int = None, target_minute: int = None) -> str:
    """특정 시간대+일차로 시간을 설정. target_hour가 있으면 슬롯 내 정확한 시각으로.
    target_minute가 있으면 분까지 정확히 (Theoria 자동 시간 동기화용, 2026-05-23)."""
    world = domain_manager.get_world_state(channel_id)
    _init_clock(world)
    time_slots = get_time_slots(channel_id)

    old_slot = world.get("time_slot", "오후")
    old_weather = world.get("weather", "맑음")
    old_day = world.get("day", 1)

    if target_slot not in time_slots:
        target_slot = time_slots[0]

    slot_range = config.TIME_SLOT_HOURS.get(target_slot, (12, 16))
    start_h = slot_range[0]

    # target_hour가 유효하고 슬롯 범위 내이면 사용, 아니면 슬롯 시작
    if target_hour is not None:
        try:
            target_hour = int(target_hour)
            end_h = slot_range[1]
            # wrap 처리 (심야 23~3)
            if start_h <= end_h:
                if start_h <= target_hour <= end_h:
                    start_h = target_hour
            else:
                if target_hour >= start_h or target_hour <= end_h:
                    start_h = target_hour
        except (ValueError, TypeError):
            pass

    # target_minute 처리 (0-59 범위 클램프). None이면 0.
    final_minute = 0
    if target_minute is not None:
        try:
            tm = int(target_minute)
            if 0 <= tm <= 59:
                final_minute = tm
        except (ValueError, TypeError):
            pass

    world["hour"] = start_h
    world["minute"] = final_minute
    world["time_slot"] = target_slot

    # day_offset이 0이어도 슬롯이 "과거"면 자동 +1
    old_idx = time_slots.index(old_slot) if old_slot in time_slots else 0
    new_idx = time_slots.index(target_slot)
    if day_offset == 0 and new_idx <= old_idx and target_slot != old_slot:
        day_offset = 1

    if day_offset > 0:
        world["day"] = old_day + day_offset
        world["weather"] = random.choice(get_weather_types(channel_id))
        # V8.5: day→month→year wrap (캘린더 확장)
        _wrap_calendar(world)

    if old_slot != target_slot:
        world["last_temporal_context"] = {
            "prev_time_slot": old_slot,
            "prev_day": old_day,
            "prev_weather": old_weather,
        }

    domain_manager.update_world_state(channel_id, world)

    time_emoji = {
        "새벽": "🌅", "오전": "☀️", "오후": "🌤️",
        "황혼": "🌆", "저녁": "🌙", "심야": "🌑"
    }
    emoji = time_emoji.get(target_slot, "⏰")
    time_str = f"{start_h:02d}:{final_minute:02d}"

    if day_offset > 0:
        return (
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"📅 **{format_calendar(world)}** {emoji} **{target_slot}** ({time_str})\n"
            f"🌤️ 날씨: {world['weather']}\n"
            f"━━━━━━━━━━━━━━━━━━━━"
        )
    return f"{emoji} **{target_slot}** ({time_str})"


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
        # V8.5: day → month → year wrap
        _wrap_calendar(world)
        new_weather = random.choice(get_weather_types(channel_id))
        world["weather"] = new_weather

        # 시각 동기화 — 새 슬롯 시작 시각으로 설정
        start_h = config.TIME_SLOT_HOURS.get(time_slots[0], (4, 6))[0]
        world["hour"] = start_h
        world["minute"] = 0

        emoji = time_emoji.get(time_slots[0], "🌅")
        time_str = f"{start_h:02d}:00"
        msg = (
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"🌙 **밤이 지나고...**\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"📅 **{format_calendar(world)}** {emoji} **{time_slots[0]}** ({time_str})\n"
            f"🌤️ 날씨: {new_weather}\n"
            f"━━━━━━━━━━━━━━━━━━━━"
        )
    else:
        world["time_slot"] = time_slots[next_idx]

        # 시각 동기화
        start_h = config.TIME_SLOT_HOURS.get(time_slots[next_idx], (12, 16))[0]
        world["hour"] = start_h
        world["minute"] = 0

        emoji = time_emoji.get(time_slots[next_idx], "⏰")
        time_str = f"{start_h:02d}:00"

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

        msg = f"{emoji} **{time_slots[next_idx]}** ({time_str}) — {atm}"

    domain_manager.update_world_state(channel_id, world)
    return msg

def advance_tick(channel_id: str, ticks: int = 1) -> str:
    """1틱(5-10분) 단위 시간 경과. advance_minutes로 위임."""
    minutes = ticks * random.randint(5, 10)
    return advance_minutes(channel_id, minutes)

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
    OOC `!둠` 명령 / quest 보상 — 직접 amount 반영 (페이즈 multiplier 미적용, 사용자 의도 보존).
    """
    world = domain_manager.get_world_state(channel_id)
    old_val = world.get("doom", 0)
    new_val = max(0, min(100, old_val + amount))

    if old_val == new_val:
        return ""  # No change

    world["doom"] = new_val
    domain_manager.update_world_state(channel_id, world)

    emoji = "📈" if amount > 0 else "📉"

    # 페이즈 전환 감지 (default boundary 기준 — OOC 메시지는 lens 정보 부족하니 default)
    diff_msg = ""
    old_phase = config.get_lens_phase(old_val, "default")
    new_phase = config.get_lens_phase(new_val, "default")
    if old_phase != new_phase:
        diff_msg = f"\n📖 **페이즈 전환:** {old_phase} → {new_phase}"

    return f"{emoji} **활성도:** {old_val}% → **{new_val}%**{diff_msg}"


def get_doom_info(value: int, lens: str = "default") -> Dict[str, Any]:
    """페이즈 + atmosphere 정보 반환. legacy genre 인자는 lens로 동작."""
    phase = config.get_lens_phase(value, lens or "default")
    return {
        "phase": phase,
        "atmosphere": config.get_lens_atmosphere(lens or "default", phase),
        "lens": lens or "default",
    }


def _get_doom_description(doom: int) -> str:
    info = get_doom_info(doom)
    return f"📖 {info['phase']}"

def _get_clock_emoji(clock: dict) -> str:
    """Clock polarity → emoji prefix."""
    doom_on = clock.get("doom_on_complete")
    if doom_on is None or (isinstance(doom_on, (int, float)) and doom_on > 0):
        return "⏰"   # threat
    elif doom_on == 0:
        return "📅"   # timer
    else:
        return "⭐"   # opportunity


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
        emoji = _get_clock_emoji(clock)
        name = clock.get("name", "Unnamed")
        seg = int(clock.get("segments", 4) or 4)
        prog = int(clock.get("filled", clock.get("progress", 0)) or 0)
        items.append(f"{emoji}{name} ({prog}/{seg})")
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
    _init_clock(world)  # V8.5: year/month 마이그레이션 + hour/minute 초기화
    time_slot = world.get("time_slot", "Unknown")
    hour = world.get("hour", 12)
    minute = world.get("minute", 0)
    time_str = f"{hour:02d}:{minute:02d}"
    cal_str = format_calendar(world)  # "N년 M월 D일"
    present = _get_active_player_masks(channel_id)
    present_text = ", ".join(present) if present else "None"
    lines.append(f"위치 {location} | 시간 {cal_str} {time_str} ({time_slot}) | 인물 {present_text}")

    # All core modules always active — no module_set checks needed
    target = _get_status_target_participant(channel_id, user_id)
    mem = target.get("ai_memory", {}) if isinstance(target, dict) else {}
    legacy_mental = mem.get("mental", {}) if isinstance(mem, dict) else {}
    vigor_src = mem.get("vigor", legacy_mental) if isinstance(mem, dict) else {}
    composure_src = mem.get("composure", legacy_mental) if isinstance(mem, dict) else {}
    vigor_val = int(vigor_src.get("value", 100) or 100)
    composure_val = int(composure_src.get("value", 100) or 100)
    doom_val = int(world.get("doom", 0) or 0)
    lines.append(f"활력 {vigor_val} | 평형 {composure_val} | Doom {doom_val}")

    if True:  # doom clocks always active
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
        f"- 시간: {format_calendar(world)} {world.get('hour', 12):02d}:{world.get('minute', 0):02d} ({world.get('time_slot', '오후')})",
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

    bar = _get_doom_bar(current)
    msg = f"📖 **챕터 활성도**\n{bar} 페이즈 **{info['phase']}**\n"
    clocks_txt = _format_doom_clocks(world, limit=5)
    if clocks_txt and clocks_txt != "None":
        msg += f"\n⏰ **활성 시계**: {clocks_txt}\n"
        for c in world.get("doom_clocks", []):
            if not isinstance(c, dict) or c.get("resolved"):
                continue
            seg = int(c.get("segments", 4) or 4)
            filled = int(c.get("filled", c.get("progress", 0)) or 0)
            remaining = seg - filled
            if 0 < remaining <= 2:
                msg += f"⏳ **임박: {c.get('name', '?')}** — {remaining}칸 남음\n"

    # 페이즈 기반 메시지 (패널티 라벨 X)
    phase = info['phase']
    phase_msg = {
        "起": "이야기가 천천히 시작합니다.",
        "承": "사건들이 누적되고 있습니다.",
        "轉": "결정적 변화가 다가옵니다.",
        "結": "절정이 가까워졌습니다.",
        "間": "후일담의 여운입니다.",
    }
    msg += phase_msg.get(phase, "")
    return msg


def unlink_clock(channel_id: str, clock_name: str) -> None:
    """[Q-3] 퀘스트 제거 시 연결 시계의 linked_quest 포인터만 청소(orphan 방지).
    시계 자체는 독립 위협으로 유지 — 퀘스트 포기가 위협 소멸을 뜻하진 않음.
    이렇게 해야 나중에 시계 완성 시 `_fail_linked_quest`가 이미 사라진 퀘스트를 가리키지 않는다."""
    world = domain_manager.get_world_state(channel_id)
    clocks = world.get("doom_clocks", [])
    if not isinstance(clocks, list):
        return
    changed = False
    for clock in clocks:
        if isinstance(clock, dict) and clock.get("name") == clock_name and clock.get("linked_quest"):
            clock["linked_quest"] = None
            changed = True
    if changed:
        world["doom_clocks"] = clocks
        domain_manager.update_world_state(channel_id, world)


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
            # 0626: 시계 해결 = doom NEUTRAL (해결도 능동 beat, fall은 間만 — 옛 bonus_doom 하락 cut)
            world["doom_clocks"] = clocks
            domain_manager.update_world_state(channel_id, world)
            return f"✅ **시계 해결: {clock_name}**"
    return ""


# =========================================================
# [2026-06-12] 명시 시간 선언(Time Decree) 파서
# W3 Decree 원칙: 유저의 시간 선언 = 확립된 사실 — 클램프가 누를 대상이 아님.
# 원래 Theoria의 explicit_hours 신호에 의존했으나(2026-05-23) 모델 교체로 미발화 관측
# ("2시간 뒤" 인풋 → TimeSync가 120→4분 클램프) → 코드 regex 판정으로 보강.
# =========================================================

import re as _re_td

_TD_KOR_NUM = {"한": 1, "두": 2, "세": 3, "네": 4, "다섯": 5,
               "여섯": 6, "일곱": 7, "여덟": 8, "아홉": 9, "열": 10}
_TD_PATTERN = _re_td.compile(
    r'(\d{1,3}|한|두|세|네|다섯|여섯|일곱|여덟|아홉|열)\s*(분|시간)\s*(?:쯤|정도|가량|반)?\s*(?:뒤|후|이?\s*지나)'
)
# 대사 제외용 — 인물의 "2시간 뒤에 올게"는 선언이 아니라 발화
_TD_QUOTES = _re_td.compile(r'"[^"]*"|“[^”]*”|『[^』]*』|「[^」]*」')


def parse_time_decree(text) -> int:
    """유저 인풋의 명시 시간 점프 선언 → 분 (없으면 0).

    - 따옴표 안(대사)은 제외: 지문/선언부의 "N분/시간 뒤·후"만 Decree
    - 상한 24시간 (그 이상의 점프는 !시간 설정 영역 — 오인 방지)
    """
    if not text or not isinstance(text, str):
        return 0
    stripped = _TD_QUOTES.sub(" ", text)
    m = _TD_PATTERN.search(stripped)
    if not m:
        return 0
    num, unit = m.group(1), m.group(2)
    n = _TD_KOR_NUM.get(num)
    if n is None:
        try:
            n = int(num)
        except ValueError:
            return 0
    if n <= 0:
        return 0
    minutes = n * 60 if unit == "시간" else n
    return max(1, min(minutes, 24 * 60))

