"""
Lorekeeper TRPG Bot - Game System Module (Facade & Logic Center)
This module acts as the central hub for Game Mechanics and Rules.
It aggregates logic from game_world, game_character, and npc_manager,
and implements high-level rule processing (Time, Anomaly, Judgment).
"""

import asyncio
import logging
from typing import List, Dict, Any, Optional, Tuple

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

    if duration == "explicit" and explicit_hours:
        ticks = int(explicit_hours * 5)
        
    # [Anti-Gravity Fix] Premature Turn Prevention
    # 성인/전투 장면에서는 명시적인 시간 경과가 아닌 한, 자동 시간 진행을 막는다.
    if scene_type in ["intimate", "combat"] and duration != "explicit":
        if ticks > 0:
            logger.info(f"[{scene_type}] Suppressing time flow ({ticks} ticks) to prevent premature turn advancement.")
            ticks = 0

    if ticks <= 0:
        return None

    world = domain_manager.get_world_state(channel_id)
    current_ticks = world.get("time_ticks", 0)
    new_ticks_total = current_ticks + ticks

    # 둠 체크 (5틱마다 1회)
    # Legacy Doom tick removed (handled by UNE DoomModule)

    # 시간대(Slot) 진행
    if new_ticks_total >= config.TIME_TICKS_PER_SLOT:
        slots_to_advance = new_ticks_total // config.TIME_TICKS_PER_SLOT
        remaining_ticks = new_ticks_total % config.TIME_TICKS_PER_SLOT

        world["time_ticks"] = remaining_ticks
        domain_manager.update_world_state(channel_id, world)

        for _ in range(slots_to_advance):
            msg = game_world.advance_time(channel_id)
            if msg:
                messages.append(msg)
    else:
        world["time_ticks"] = new_ticks_total
        domain_manager.update_world_state(channel_id, world)

    return "\n".join(messages) if messages else None


async def process_anomaly(
    client, 
    model_id_flash: str, 
    channel_id: str, 
    current_doom: int, 
    scene_type: str,
    active_genres: List[str],
    participants: Dict[str, Any]
) -> List[str]:
    """[LEGACY] UNE AnomalyModule에 의해 대체되었습니다."""
    return []

async def process_judgment(
    channel_id: str,
    user_id: str,
    player_data: Dict[str, Any],
    nvc_result: Dict[str, Any],
    scene_type: str = "normal"
) -> Tuple[Optional[str], Optional[str]]:
    """
    [LEGACY] UNE JudgmentEngine에 의해 대체되었습니다.
    데이터 충돌 방지를 위해 빈 로그를 반환합니다.
    """
    return None, None
