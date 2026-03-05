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

# Mental & Adaptation (Legacy removed)
calculate_adaptation_percentage = game_character.calculate_adaptation_pct
get_mental_status_text = game_character.get_mental_status_text
get_abnormal_context = game_character.get_abnormal_context

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
    minutes = ticks * 1.5
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

    duration = time_flow.get("duration", "instant")
    ticks = time_flow.get("ticks", 0)
    explicit_hours = time_flow.get("explicit_hours")

    messages = []

    explicit = time_flow.get("explicit", False) or duration == "explicit"

    if explicit and explicit_hours:
        ticks = int(explicit_hours * 5)

    # v3: SCENE_TIME_RULES 기반 클램핑 (Anti-Gravity Fix 통합)
    rules = config.SCENE_TIME_RULES.get(scene_type, config.SCENE_TIME_RULES["normal"])
    if not explicit:
        if ticks > rules["max_ticks"]:
            logger.info("[TimeFlow] Clamped %d → %d (scene=%s)", ticks, rules["max_ticks"], scene_type)
            ticks = rules["max_ticks"]
        if ticks <= 0 and rules["base_ticks"] > 0:
            ticks = rules["base_ticks"]

    if ticks <= 0:
        return None

    # 틱 → 분 변환 후 advance_minutes로 시각 갱신
    minutes = ticks * 3  # 1틱 = ~3분
    msg = game_world.advance_minutes(channel_id, minutes)
    if msg:
        messages.append(msg)

    return "\n".join(messages) if messages else None
