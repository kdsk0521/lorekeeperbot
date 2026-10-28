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

logger = logging.getLogger(__name__)

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


def _add_scene_elapsed(world: dict, old_h: int, old_m: int, day_delta: int) -> None:
    """[2026-09-24 감사] 슬롯·날짜 점프도 장면 경과(scene_elapsed_min)에 넣는다 — 누적이 advance_minutes 에만 있어
    "다음날 아침" 같은 점프(advance_to_slot·advance_time·절대 시각)에 환경 노화 힌트가 안 떴다."""
    try:
        d = int(day_delta or 0) * 24 * 60 + (int(world.get("hour", 0)) * 60 + int(world.get("minute", 0))) \
            - (int(old_h) * 60 + int(old_m))
        if d > 0:
            world["scene_elapsed_min"] = int(world.get("scene_elapsed_min", 0) or 0) + d
    except (TypeError, ValueError):
        pass


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
    _old_h, _old_m = world.get("hour", 0), world.get("minute", 0)

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
    _add_scene_elapsed(world, _old_h, _old_m, day_offset)

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
    _old_h, _old_m = world.get("hour", 0), world.get("minute", 0)

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

    _add_scene_elapsed(world, _old_h, _old_m, 1 if next_idx >= len(time_slots) else 0)
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
    # [2026-10-24 C74 판2 W1-8] 진전 없음 멈춤(목표) — 턴 번호를 올린 직후 한 번. 옛 자리(렌더 앞 매 턴 archive)는 지웠다.
    #   !다시 스냅샷은 이 앞에 찍히므로 이 pause 행은 되감기에 같이 걷힌다.
    if step > 0:
        try:
            import thread_ledger
            thread_ledger.pause_stale_goals(channel_id, world["turn_index"])
        except Exception as _e_st:
            logger.debug(f"[Thread] stale pause skip: {_e_st}")
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

    # 페이즈 전환 감지 — [C90 10-28] 채널 장르로 doom_module 과 같은 경계(옛: 늘 default 표)
    diff_msg = ""
    old_phase = channel_phase(channel_id, old_val)
    new_phase = channel_phase(channel_id, new_val)
    if old_phase != new_phase:
        diff_msg = f"\n📖 **페이즈 전환:** {old_phase} → {new_phase}"

    return f"{emoji} **활성도:** {old_val}% → **{new_val}%**{diff_msg}"


def channel_phase(channel_id: str, value) -> str:
    """[C90 10-28] 채널 장르(렌즈 · 플레이버) 기준 페이즈 — doom_module 과 같은 셈(config.chapter_phase)."""
    try:
        import doom_module
        ag = domain_manager.get_active_genres(channel_id)
        return config.chapter_phase(value, doom_module._extract_lens_tags(ag), doom_module._extract_flavor_tags(ag))
    except Exception:
        return config.chapter_phase(value)


def get_doom_info(value: int, lens: str = "default", channel_id: str = "") -> Dict[str, Any]:
    """페이즈 + atmosphere 정보 반환. legacy genre 인자는 lens로 동작. [C90] channel_id 를 주면 채널 장르로 경계."""
    if channel_id:
        phase = channel_phase(channel_id, value)
    else:
        phase = config.chapter_phase(value, [lens] if lens and lens != "default" else [])
    return {
        "phase": phase,
        "atmosphere": config.get_lens_atmosphere(lens or "default", phase),
        "lens": lens or "default",
    }


def _get_doom_description(doom: int, channel_id: str = "") -> str:
    info = get_doom_info(doom, channel_id=channel_id)
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


def _get_status_target_uid(channel_id: str, user_id: str = "") -> str:
    """위 함수와 **같은 고르기**의 user_id 판. per-PC 레지스트리 값(활력) 조회에 필요하다."""
    participants = domain_manager.get_domain(channel_id).get("participants", {})
    if not isinstance(participants, dict):
        return ""
    if user_id:
        direct = participants.get(user_id)
        if isinstance(direct, dict) and direct.get("status") == "active":
            return str(user_id)
    for uid, p_data in participants.items():
        if isinstance(p_data, dict) and p_data.get("status") == "active":
            return str(uid)
    return ""


def _recent_turn_text(channel_id: str) -> str:
    """이번 턴 입력 + 직전 산문(히스토리 꼬리 2행). 급식 mentioned 게이트의 재료.

    ★어휘 게이트의 판정면은 `select_mentioned` 와 같아야 한다 — 추출이 보는 낱말과
      렌더가 보는 낱말이 다르면 "모델이 움직인 값이 산문엔 안 보이는" 갈래가 생긴다.
    """
    try:
        rows = (domain_manager.get_domain(channel_id) or {}).get("history") or []
    except Exception:
        return ""
    if not isinstance(rows, list):
        return ""
    out = []
    for r in rows[-2:]:
        if isinstance(r, dict):
            out.append(str(r.get("content", "") or ""))
        else:
            out.append(str(r or ""))
    return " ".join(out)[-2000:]


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
    # [2026-08-18 Phase 2.5] 활력 = 레지스트리 값. 이 줄의 모양은 그대로다(표시 무변경).
    try:
        import custom_vars as _cv_v
        vigor_val = int(_cv_v.vigor_value(channel_id, _get_status_target_uid(channel_id, user_id), mem))
    except Exception as _e_cvv:
        logger.debug(f"[CustomVar] 활력 표시 폴백: {_e_cvv}")
        vigor_val = int(vigor_src.get("value") if vigor_src.get("value") is not None else 100)  # [2026-09-24 감사] 0 보존
    # [2026-09-06 P8b] 평형도 레지스트리 소유 — 활력과 같은 문을 쓴다(표기 무변경).
    try:
        composure_val = int(_cv_v.composure_value(channel_id, _get_status_target_uid(channel_id, user_id), mem))
    except Exception:
        composure_val = int(composure_src.get("value") if composure_src.get("value") is not None else 100)  # [2026-09-24 감사] 0 보존
    doom_val = int(world.get("doom", 0) or 0)
    # [2026-08-28 중복 제거] `Doom N`을 뺀다.
    #   ⚠초판 사유("읽는 법이 프롬프트에 없다")는 **오진이었다** — 레티어스 지적으로 정정.
    #   읽는 법은 있다: `une_facade._build_atmosphere_layer`가 매 턴 directive 층에
    #   `[Tension {doom}% — phase {起承轉結間}, {lens}]` + `LENS_DOOM_ATMOSPHERE` 블록을 넣는다
    #   (그 함수 주석 자체가 "Doom = Chapter Volume Gauge — 둠 리브랜드 산문 주입").
    #   ★진짜 결함은 **같은 값이 두 이름으로 두 번 도착하는 것**이다:
    #     directive=`Tension 40%`(읽는 법 동반) / 여기=`Doom 40`(벌거벗음).
    #     모델이 별개의 두 게이지로 읽을 수 있고, `Doom`은 챕터 볼륨 리브랜드의 **폐기어**다.
    #     출처도 갈린다 — 저쪽은 `bus.doom["value"]`(이번 턴), 여기는 `world["doom"]`(영속).
    #   활력·평형은 남긴다: 저쪽에도 오지만 여기 수치는 `translate_vigor_composure`가 번역하는
    #   **같은 이름의 같은 축**이라 두 게이지 오인 위험이 없다.
    # [2026-10-15 C42] 타격칸(마이너스 상태 전용 4칸)을 같은 줄에 — 이름까지 넘겨야 다음 턴 산문이 같은 상처를
    #   같은 말로 잇고, 4칸이 다 찬 걸 렌더가 보고 몸 줄("collapse only when the body truly gives out")로 판단한다.
    try:
        import game_character as _gc_sk
        _sk_txt = _gc_sk.format_strikes(target)
    except Exception as _e_sk:
        logger.debug(f"[Strike] 시스템 줄 skip: {_e_sk}")
        _sk_txt = ""
    lines.append(f"활력 {vigor_val} | 평형 {composure_val}" + (f" | {_sk_txt}" if _sk_txt else ""))

    # [2026-10-25 C74 판4] 시계 줄 `[이름 f/s]` 은 Slot 15 `<In_Motion>`(in_motion.py)으로 갔다 — 그 시계가 걸린 목표 ·
    #   우물 밑에 선다. 여기(Slot 29)엔 안 싣는다.

    # [2026-08-18 대형식화] 선언 변수 현재값 → 산문 재료. 스펙 §3-5 "값을 재료로만 급식".
    #   같은 자리(Slot 29 <Real_Time_Status>)·같은 문법(코드 소유 수치를 한 줄로) —
    #   위 `활력 | 평형 | Doom` 줄의 유저-정의판이다. 값의 주인은 코드고, 패널·헤더·산문
    #   셋이 같은 소스(custom_vars.format_value)를 읽으므로 표시가 어긋나지 않는다.
    #   블록 머리 1절이 처분(낭독 금지)을 확정한다 — custom_vars.PROSE_FEED_HEADER.
    #   킬스위치 off / 선언 0 / 표시할 값 0 이면 "" 이라 줄 자체가 없다(순증 0).
    #   [2026-09-06 P3] 급식 기본이 **mentioned** 로 바뀌었다 — 이번 턴 입력·직전 산문을
    #   같이 넘겨야 가변부가 산다(척추=always 는 이 텍스트와 무관하게 매턴 실린다).
    #   재료는 히스토리 꼬리 두 줄: 방금 들어온 PC 행동과 직전 모델 산문이 정확히 그 둘이다.
    try:
        import custom_vars as _cv_prose
        _cv_block = _cv_prose.build_prose_feed(
            channel_id, mentioned_text=_recent_turn_text(channel_id),
            user_id=_get_status_target_uid(channel_id, user_id))  # [2026-09-24 감사] 조건부 지시의 행위자 = 행동 PC
        if _cv_block:
            lines.append(_cv_block)
    except Exception as _e_cv:
        logger.debug(f"[CustomVar] 산문 급식 skip: {_e_cv}")

    return "\n".join(lines).strip()


# ⚰ [2026-09-07 P9] `build_status_header` 삭제 — 상태창은 산문 **머리 텍스트**가 아니라
#   **꼬리 임베드**다. 이 함수가 그리던 3줄(위치·시간·인물 / 유저 헤더 템플릿 / 둠 시계)은
#   status_panel.build_turn_embed_data 로 통째로 이사했고, 거기서 패널 1장째와 합집합이
#   된다(같은 값 두 번 금지 — 인물은 PC 섹션과, 템플릿은 세계 섹션과 중복 제거).
#   08-16 이관 계약(값의 주인이 그린다 · 읽기 전용 · 표시 전용)은 새 자리에 그대로 살아 있고,
#   렌더 관성 대비 strip(response_processor.strip_status_header)도 그대로 남는다.
#   부활 금지: 머리 텍스트가 돌아오면 임베드와 이중 표기가 되고 임베드 예산 밖 텍스트가 생긴다.
#   `_get_active_player_masks`·`format_calendar`·`_init_clock` 은 소비자가 남아 존치.


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
        f"- 위기 수치: {world.get('doom', 0)}% ({_get_doom_description(world.get('doom', 0), channel_id)})",
        f"- 위협 시계: {_format_doom_clocks(world)}",
        f"- **파티 분위기**: {party_context}",
    ]

    # 시간 전환 컨텍스트 (방금 시간이 바뀌었을 때 서술 참고용)
    ltc = world.get("last_temporal_context", {})
    if ltc and ltc.get("prev_time_slot"):
        current_slot = world.get("time_slot", "")
        if ltc["prev_time_slot"] != current_slot:
            lines.append(f"- **시간 전환**: {ltc['prev_time_slot']} → {current_slot} (전환 직후, 분위기 변화를 자연스럽게 묘사)")

    # [2026-10-24 C76 §5] 옛 "세계 규칙" 줄(`world_constraints` — 로어 분석이 산문용으로 다시 쓴 체계 · 사회 · 금기) 은퇴.
    #   산문에 늘 실을 월드북은 `!룰`(Slot 23), `!로어` 분석은 코드 재료(레티어스 10-24).

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
    info = get_doom_info(current, channel_id=channel_id)

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


def _safe_int(v, d=0) -> int:
    try:
        return int(v)
    except (TypeError, ValueError):
        return d


def extraction_clock_line(channel_id: str, world: Dict[str, Any] = None, limit: int = 8) -> str:
    """[2026-10-28 C89 ㄹ · ㅁ] 배치 world_state 입력 줄 — 지금 선 시계 이름(+ 종류 · 칸 · 위협 한 줄). 없으면 ''.
    두 몫: 같은 일을 사안으로 또 세우지 않게(ㄹ-4) · 산문이 위협이 끝났음을 보이면 clocks_ended 로 이름을 받는다(ㅁ).
    시계 하나가 꼴이 깨져도(칸이 글) 그 시계만 기본값으로 — 줄째 사라지지 않게(검토 C89-2)."""
    if not channel_id:
        return ""
    world = world if isinstance(world, dict) else (domain_manager.get_world_state(channel_id) or {})
    parts = []
    for c in world.get("doom_clocks") or []:
        if not isinstance(c, dict) or c.get("resolved") or not str(c.get("name") or "").strip():
            continue
        try:
            import doom_module as _dmod
            dv = _dmod._get_clock_doom(c)
        except Exception:
            dv = 1
        kind = "위협" if dv > 0 else ("타이머" if dv == 0 else "기회")
        seg = _safe_int(c.get("segments", 4), 4) or 4
        filled = _safe_int(c.get("filled", c.get("progress", 0)), 0)
        threat = str(c.get("threat") or "").strip()[:80]
        parts.append(f"{str(c.get('name')).strip()} ({kind} {filled}/{seg}" + (f" — {threat})" if threat else ")"))
        if len(parts) >= limit:
            break
    return ("Clocks: " + " | ".join(parts)) if parts else ""


def _find_live_clock(clocks: List[Dict[str, Any]], name: str, norm) -> "Dict[str, Any] | None":
    """이름으로 산 시계 찾기 — 정규화 일치, 안 되면 모델이 시계 줄의 꼬리 "(위협 2/6 — …)"까지 베껴 온 꼴을 벗겨 다시."""
    if not name:
        return None
    keys = [norm(name)]
    if " (" in name:
        keys.append(norm(name.split(" (", 1)[0]))
    for k in keys:
        for c in clocks:
            if isinstance(c, dict) and not c.get("resolved") and norm(str(c.get("name") or "").strip()) == k:
                return c
    return None


def end_clocks_on_page(channel_id: str, items: Any, source_text: str, turn: int = 0,
                       actor: str = "") -> Dict[str, Any]:
    """[2026-10-28 C89 ㅁ] 렌더 뒤 배치 `clocks_ended` → 그 시계를 averted 로 끝낸다(doom §0-앞 Theoria 해결과 같은 규칙).
    인용 관문 = 스레드와 같은 정규화(thread_ledger._norm) — 이번 턴 입력 + 산문에 그 말이 있어야. 이름은 지금 선 시계와 맞춘다.
    위협(doom>0)에 걸린 목표 +1(code:averted, 연결이 살아 있을 때만 · 이 턴에 그 목표 진행 행이 이미 있으면 건너뜀 — 한 사건
    두 번 밀기 막음, 검토 C89-2) · 해소 보상(평형, 캡 면제 — doom 의 resolve_reward 와 같은 값). 타이머는 끝만.
    ★저장 순서(검토 C89-1): 시계 끝(연결 풂 포함)을 **먼저 저장**하고 나서 목표를 민다 — 목표가 그걸로 다 차 닫히면 그 보상
    (pending_doom_gain · 같은 목표의 다른 시계 해결)을 장부 쪽이 세계에 쓰는데, 그 뒤에 처음 읽은 사본을 저장하면 지워졌다.
    항목마다 세계를 새로 읽는다. 다음 턴 통째 저장 · 발동은 '끝남은 이긴다'(une_facade · doom §0)가 지킨다.
    돌려주는 것: {"ended": [이름], "receipts": [목표 영수증], "rejected": n}."""
    out: Dict[str, Any] = {"ended": [], "receipts": [], "rejected": 0}
    if not channel_id or not isinstance(items, list) or not items:
        return out
    if not getattr(config, "CLOCKS_ENDED_ON_PAGE", True):
        return out
    try:
        import thread_ledger
        import doom_module as _dmod
        import sqlite_store
    except Exception:
        return out
    src = thread_ledger._norm(source_text or "")
    rewards = 0
    for it in items[:8]:
        if not isinstance(it, dict):
            out["rejected"] += 1
            continue
        name = str(it.get("name") or "").strip()
        q = thread_ledger._norm(it.get("quote") if isinstance(it.get("quote"), str) else "")
        world = domain_manager.get_world_state(channel_id) or {}
        clocks = world.get("doom_clocks") or []
        if not isinstance(clocks, list):
            break
        clock = _find_live_clock(clocks, name, thread_ledger._norm)
        if not clock or not q or q not in src:
            out["rejected"] += 1
            continue
        try:
            clock["resolved"] = True
            clock.pop("pending_completion", None)
            clock.pop("pending_turn", None)
            _dmod._mark_end(clock, "averted", turn)
            try:
                doom_v = _dmod._get_clock_doom(clock)
            except Exception:   # 칸이 글로 깨진 옛 시계 — 위협으로 본다(doom 기본 쪽)
                doom_v = 1
            gid = clock.get("linked_goal")
            clock["linked_goal"] = None
            key = (clock.get("name"), clock.get("turn_created"))
            world["doom_clocks"] = clocks
            domain_manager.update_world_state(channel_id, world)   # ← 먼저 저장(검토 C89-1)
        except Exception as e:
            logger.warning(f"[ClockEnd] end skip ({name}): {e}")
            out["rejected"] += 1
            continue
        out["ended"].append(clock.get("name"))
        if doom_v > 0:
            rewards += int(config.CLOCK_RESOLVE_REWARD.get(_safe_int(clock.get("segments", 6), 6) or 6, 3))
        if not (gid and doom_v > 0):
            continue
        try:
            rows = sqlite_store.read_thread_log(channel_id)
            g = thread_ledger.load_states(channel_id).get(gid)
            if not _dmod._goal_link_live(channel_id, clock, gid, g, rows=rows):
                continue
            if any(str(r.get("thread_id")) == gid and r.get("op") == "progress" and _safe_int(r.get("turn")) == int(turn or 0)
                   for r in rows):
                logger.info("[ClockEnd] goal advance skip: %s already moved this turn (clock '%s')", gid, clock.get("name"))
                continue
            n = thread_ledger.code_events(channel_id, int(turn or 0), [{"op": "progress", "id": gid, "step": 1}],
                                          origin="code:averted", receipts=out["receipts"])
            if n:   # advanced_goal 은 다시 읽은 세계에 얹는다(목표 끝 보상이 쓴 것 위에)
                w2 = domain_manager.get_world_state(channel_id) or {}
                for c2 in w2.get("doom_clocks") or []:
                    if isinstance(c2, dict) and (c2.get("name"), c2.get("turn_created")) == key:
                        c2["advanced_goal"] = gid
                        domain_manager.update_world_state(channel_id, w2)
                        break
        except Exception as e:
            logger.debug(f"[ClockEnd] goal advance skip: {e}")
    if out["ended"]:
        if rewards > 0:
            try:
                import custom_vars as _cv
                _cv.apply_system_delta(channel_id, "평형", rewards, "clock averted", actor=actor,
                                       exempt_cap=True, source="doom.resolve_reward")
            except Exception as e:
                logger.debug(f"[ClockEnd] reward skip: {e}")
        logger.info("[ClockEnd] t=%s ended=%s rejected=%s reward=%s", turn, out["ended"], out["rejected"], rewards)
    elif out["rejected"]:
        logger.info("[ClockEnd] t=%s rejected=%s (이름 · 인용 안 맞음)", turn, out["rejected"])
    return out


def resolve_clocks_by_goal(channel_id: str, goal_id: str, goal_title: str = "", turn: int = 0) -> List[str]:
    """[2026-10-24 C74 판2] 목표 close done → 그 목표에 걸린 시계(`linked_goal`)를 서사적 해결로 끝낸다(옛
    resolve_clock_by_quest — 이름 대신 장부 id). doom NEUTRAL(0626). end="quest" · end_turn(이 일이 속한 턴) ·
    end_quest(목표 제목 — 도착물 카드 "'목표'(끝남)"). 되비침 줄 목록.
    다음 턴 통째 저장에 덮이지 않는 것은 une_facade.sync_from_game_context 의 "끝남은 이긴다"(§2-⑭)가 맡는다."""
    if not channel_id or not goal_id:
        return []
    world = domain_manager.get_world_state(channel_id)
    clocks = world.get("doom_clocks", [])
    if not isinstance(clocks, list):
        return []
    out: List[str] = []
    for clock in clocks:
        if not isinstance(clock, dict) or clock.get("resolved") or clock.get("linked_goal") != goal_id:
            continue
        clock["resolved"] = True
        clock["end"] = "quest"
        try:
            clock["end_turn"] = int(turn or world.get("turn_index", 0) or 0)
        except (TypeError, ValueError):
            clock["end_turn"] = 0
        if goal_title:
            clock["end_quest"] = str(goal_title)
        out.append(f"✅ **시계 해결: {clock.get('name', '?')}**")
    if out:
        world["doom_clocks"] = clocks
        domain_manager.update_world_state(channel_id, world)
    return out


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

