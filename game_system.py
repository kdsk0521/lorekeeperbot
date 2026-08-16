"""
Lorekeeper TRPG Bot - Game System Module (Facade & Logic Center)
This module acts as the central hub for Game Mechanics and Rules.
It aggregates logic from game_world, game_character, and npc_manager,
and implements high-level rule processing (Time, Anomaly, Judgment).
"""

import asyncio
import logging
from typing import List, Dict, Any, Optional

# Internal Modules
import config
import domain_manager
import game_world
import game_character
import npc_manager

logger = logging.getLogger("GameSystem")

# =========================================================
# Facade Exports (Backward Compatibility)
# =========================================================

# Explicit Re-exports for clarity (optional but good for IDEs)
# World
get_time_slots = game_world.get_time_slots
get_weather_types = game_world.get_weather_types
advance_time = game_world.advance_time
advance_tick = game_world.advance_tick
advance_minutes = game_world.advance_minutes
advance_to_slot = game_world.advance_to_slot
get_formatted_time = game_world.get_formatted_time
change_doom = game_world.change_doom
_get_doom_description = game_world._get_doom_description
get_world_context = game_world.get_world_context
get_doom_forecast = game_world.get_doom_forecast

# Character / Quest
get_quest_board = domain_manager.get_quest_board
get_active_quests = game_character.get_active_quests
get_notebook_text = game_character.get_notebook_text
update_notebook_text = game_character.update_notebook_text
add_item_to_sojipin = game_character.add_item_to_sojipin
remove_item_from_sojipin = game_character.remove_item_from_sojipin
sync_notebook_to_inventory = game_character.sync_notebook_to_inventory
add_quest = game_character.add_quest
complete_quest = game_character.complete_quest
remove_quest = game_character.remove_quest
advance_quest_progress = game_character.advance_quest_progress
get_quest_progress_bar = game_character.get_quest_progress_bar
get_active_quests_text = game_character.get_active_quests_text
get_status_message = game_character.get_status_message
get_objective_context = game_character.get_objective_context
update_status_effect = game_character.update_status_effect
get_status_summary = game_character.get_status_summary

# Memo
add_memo = game_character.add_memo
edit_memo = game_character.edit_memo
remove_memo = game_character.remove_memo
resolve_memo_auto = game_character.resolve_memo_auto

# Mental (Legacy removed)
# [2026-08-11 비일상적응도 삭제] calculate_adaptation_percentage / get_abnormal_context 재수출 제거
get_mental_status_text = game_character.get_mental_status_text

# Export
export_session_history = game_character.export_session_history
export_chronicle_book = game_character.export_chronicle_book
generate_chronicle_from_history = game_character.generate_chronicle_from_history
export_lore_data = game_character.export_lore_data
get_lore_book = game_character.get_lore_book

# NPC
get_npc_time_progression = npc_manager.get_npc_time_progression


# =========================================================
# High-Level Game Logic (Migrated from Orchestration)
# =========================================================

def build_time_directive(ticks: int, scene_type: str = "normal") -> str:
    """Pro에 전달할 시간 범위 디렉티브. 서사 범위를 제한한다."""
    if scene_type in ("combat", "intimate"):
        return (
            "[TIME] Scene frozen. Describe ONE moment/action only. "
            "Do NOT advance time or skip ahead."
        )
    minutes = ticks * 2
    if minutes <= 3:
        return (
            f"[TIME] ~{minutes:.0f}min. Describe only what happens in this brief moment. "
            "Do NOT compress multiple events or skip time."
        )
    if minutes <= 10:
        return f"[TIME] ~{minutes:.0f}min. One focused interaction or action."
    return f"[TIME] ~{minutes:.0f}min. Describe the passage of time naturally."


async def process_time_flow(channel_id: str, time_flow: Dict, scene_type: str = "normal") -> Optional[str]:
    """
    시간 흐름을 처리합니다. (Ticks 증가, Slot 변경, Doom 체크)
    
    Args:
        channel_id: 채널 ID
        time_flow: NVC 분석 결과의 TimeFlow 데이터
        scene_type: 현재 씬 타입 ('normal', 'combat', 'intimate' 등)
    """
    if not time_flow:
        return None

    # 절대 시간 점프 (target) — 유저가 명시적으로 시간을 언급한 경우만
    target = time_flow.get("target")
    if target and target.get("slot"):
        # 안전장치: 현재 시각에서 target까지 과도 점프 방지
        # 1슬롯 이동은 ticks로 처리하는 게 자연스러움
        world = domain_manager.get_world_state(channel_id)
        game_world._init_clock(world)
        current_slot = world.get("time_slot", "오후")
        target_slot = target["slot"]
        _slots = config.DEFAULT_TIME_SLOTS
        try:
            cur_idx = _slots.index(current_slot)
            tgt_idx = _slots.index(target_slot)
        except ValueError:
            cur_idx, tgt_idx = 0, 0
        slot_distance = (tgt_idx - cur_idx) % len(_slots)
        day_offset = target.get("day_offset", 0)
        # V8.5: 절대 캘린더 target (year/month/day_in_month) — 직접 world 설정 후 return
        abs_year = target.get("year")
        abs_month = target.get("month")
        abs_day = target.get("day_in_month")
        if abs_month or abs_day or abs_year:
            world["year"] = int(abs_year) if abs_year else world.get("year", 1)
            world["month"] = int(abs_month) if abs_month else world.get("month", 1)
            if abs_day:
                world["day"] = int(abs_day)
            if target.get("hour") is not None:
                world["hour"] = int(target.get("hour"))
            if target.get("minute") is not None:
                world["minute"] = int(target.get("minute"))
            world["time_slot"] = target_slot
            domain_manager.update_world_state(channel_id, world)
            return f"📅 {game_world.format_calendar(world)} {world['hour']:02d}:{world['minute']:02d} ({target_slot})"
        # 같은 슬롯 + day_offset 0 + hour/minute 없음 → 시간 점프 불필요
        if slot_distance == 0 and day_offset == 0 and not target.get("hour") and not target.get("minute"):
            pass  # target 무시, ticks로 처리
        else:
            msg = game_world.advance_to_slot(
                channel_id,
                target_slot,
                day_offset,
                target_hour=target.get("hour"),
                target_minute=target.get("minute"),  # 2026-05-23: 분 단위 정확 동기화
            )
            return msg

    duration = time_flow.get("duration", "instant")
    ticks = time_flow.get("ticks", 0)
    explicit_hours = time_flow.get("explicit_hours")

    messages = []

    # 2026-05-23: explicit_hours > 0 이면 자동으로 explicit 취급 (Theoria가 상대 명시 잡았다는 신호)
    explicit = (
        time_flow.get("explicit", False)
        or duration == "explicit"
        or (explicit_hours is not None and explicit_hours > 0)
    )

    # 2026-05-23 (버그 수정): explicit + explicit_hours → 분 단위 직접 변환.
    # 이전: ticks = int(explicit_hours * 5) → 1 tick=2분 가정과 충돌하여 1/6만 진행 버그.
    # 현재: minutes = int(explicit_hours * 60) 직접 사용, ticks 단계 건너뜀.
    # explicit이라 SCENE_TIME_RULES 클램프 면제 (사용자 명시 권위).
    if explicit and explicit_hours and explicit_hours > 0:
        minutes = int(round(explicit_hours * 60))
        if minutes > 0:
            msg = game_world.advance_minutes(channel_id, minutes)
            if msg:
                messages.append(msg)
            return "\n".join(messages) if messages else None

    # v3: SCENE_TIME_RULES 기반 클램핑 (Anti-Gravity Fix 통합)
    rules = config.SCENE_TIME_RULES.get(scene_type, config.SCENE_TIME_RULES["normal"])
    # (참고 2026-06-12: explicit 신호는 원래 Theoria 의존이었으나 모델 교체로 미발화 관측 —
    #  유저 인풋 regex 판정(parse_time_decree)이 orchestration에서 선행 주입됨. 코드 결정론.)
    if not explicit:
        if ticks > rules["max_ticks"]:
            logger.info("[TimeFlow] Clamped %d → %d (scene=%s)", ticks, rules["max_ticks"], scene_type)
            ticks = rules["max_ticks"]
        if ticks <= 0 and rules["base_ticks"] > 0:
            ticks = rules["base_ticks"]

    if ticks <= 0:
        return None

    # 틱 → 분 변환 후 advance_minutes로 시각 갱신
    minutes = ticks * 2  # 1틱 = 2분
    msg = game_world.advance_minutes(channel_id, minutes)
    if msg:
        messages.append(msg)

    return "\n".join(messages) if messages else None
