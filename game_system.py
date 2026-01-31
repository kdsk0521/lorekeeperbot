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
import cognition

logger = logging.getLogger("GameSystem")

# =========================================================
# Facade Exports (Backward Compatibility)
# =========================================================
from game_world import *
from game_character import *
from npc_manager import *

# Explicit Re-exports for clarity (optional but good for IDEs)
# World
get_time_slots = game_world.get_time_slots
get_weather_types = game_world.get_weather_types
advance_time = game_world.advance_time
calculate_doom_increase = game_world.calculate_doom_increase
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
perform_check = game_character.perform_check
get_status_summary = game_character.get_status_summary
calculate_status_doom_contribution = game_character.calculate_status_doom_contribution

# Memo
add_memo = game_character.add_memo
edit_memo = game_character.edit_memo
remove_memo = game_character.remove_memo
resolve_memo_auto = game_character.resolve_memo_auto

# Mental & Adaptation
calculate_adaptation_percentage = game_character.calculate_adaptation_pct
check_adaptation_roll = game_character.check_adaptation_roll
get_mental_status_text = game_character.get_mental_status_text
get_abnormal_context = game_character.get_abnormal_context
expose_to_abnormal = game_character.apply_abnormal_impact

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
    old_doom_period = current_ticks // 5
    new_doom_period = new_ticks_total // 5

    if new_doom_period > old_doom_period:
        for _ in range(new_doom_period - old_doom_period):
            game_world.process_doom_tick(channel_id)

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
    """
    이변 발생 로직을 처리합니다.
    """
    # 이변 발생 조건 체크 (Summary/Intimate 제외)
    if scene_type in ["summary", "intimate"] or not game_world.should_trigger_anomaly(current_doom):
        return []

    logger.info(f"[GameSystem] Anomaly Triggered at Doom {current_doom}")

    messages = []
    anom_lore = domain_manager.get_event_lore_summary(channel_id) or domain_manager.get_lore(channel_id)[:1000]
    anom_loc = domain_manager.get_current_location(channel_id)
    
    # 이변 이벤트 생성
    anom_evt = await game_world.generate_anomaly_event(
        client, channel_id, current_doom, anom_lore, anom_loc, active_genres,
        model_id=model_id_flash
    )

    if anom_evt:
        evt_msg = (
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"⚡ **이변 발생: [{anom_evt.get('tag', 'Unknown')}]**\n"
            f"{anom_evt.get('description', '...')}\n"
            f"💡 *{anom_evt.get('effect_hint', '대처하십시오.')}*\n"
            f"━━━━━━━━━━━━━━━━━━━━"
        )
        messages.append(evt_msg)

        # Doom Cost 적용
        game_world.change_doom(channel_id, config.ANOMALY_DOOM_COST)

        # 적응(Mental) 판정
        adapt_results = []
        for uid, p_data in participants.items():
            # [Debug Strict Mode] If data is corrupted, halt and report.
            if not isinstance(p_data, dict):
                error_msg = f"[Critical Error] Participant Data Corruption for User {uid}. Expected dict, got {type(p_data)}: {p_data}"
                logging.error(error_msg)
                raise ValueError(error_msg)

            if p_data.get("status") == "active":
                p_data, adapt_msg = game_character.check_adaptation_roll(
                    p_data,
                    tag=anom_evt.get('tag', 'Unknown'),
                    category=anom_evt.get('category')
                )
                domain_manager.save_participant_data(channel_id, uid, p_data)

                user_name = p_data.get("mask") or p_data.get("name", "Unknown")
                adapt_results.append(f"**{user_name}**: {adapt_msg.strip()}")

        if adapt_results:
            tag = anom_evt.get('tag', 'Unknown')
            adapt_msg = (
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"🎲 **적응 판정 결과: [{tag}]**\n" +
                "\n".join(adapt_results) +
                "\n━━━━━━━━━━━━━━━━━━━━"
            )
            messages.append(adapt_msg)

    return messages


async def process_judgment(
    channel_id: str,
    user_id: str,
    player_data: Dict[str, Any],
    nvc_result: Dict[str, Any],
    scene_type: str = "normal"
) -> Tuple[Optional[str], Optional[str]]:
    """
    GM 판정을 처리합니다.
    
    Returns:
        (log_message, judgment_context_string)
    """
    action_judgment = nvc_result.get("ActionJudgment")
    if not action_judgment or not isinstance(action_judgment, dict):
        return None, None

    try:
        act = action_judgment.get("action", "Unknown Action")
        diff = action_judgment.get("difficulty", "normal")
        reason = action_judgment.get("difficulty_reason", "")
        mods = action_judgment.get("modifiers", [])

        # [V7 Feature] Mental Dice Modifier injection
        if player_data:
            mem = player_data.get("ai_memory", {})
            ment_val = mem.get("mental", {}).get("value", 100)
            ment_mod = game_character.get_mental_dice_modifier(ment_val)
            
            if ment_mod != 0:
                mods.append({"name": "Mental State", "value": ment_mod, "reason": "Psychological Impact"})

        # 보너스 다이스 적용
        b_dice = player_data.get("temp_bonus_dice", 0) if player_data else 0
        judgment_data = cognition.build_action_judgment_with_roll(act, diff, reason, mods, bonus_dice=b_dice)

        # 보너스 다이스 사용 후 리셋
        if b_dice > 0 and player_data:
            player_data["temp_bonus_dice"] = 0
            domain_manager.save_participant_data(channel_id, user_id, player_data)

        # GM Move 추가
        gm_m = nvc_result.get("GMMove", {})
        judgment_data["potential_gm_move"] = gm_m.get("type")
        judgment_data["gm_move_description"] = gm_m.get("description")

        # Intimate 씬 치명적 실패 다운그레이드
        if scene_type == "intimate" and judgment_data.get("result") == "critical_failure":
            judgment_data["result"] = "failure"
            judgment_data["final_roll"] = max(2, judgment_data["final_roll"])
            logger.info("Downgraded Critical Failure due to Intimate Scene.")

        # 로그 구성
        roll_log = cognition.build_judgment_context_with_roll(judgment_data)

        # Doom 처리
        res_key = judgment_data.get("result")
        if res_key == "failure":
            game_world.change_doom(channel_id, 1)
        elif res_key == "critical_failure":
            game_world.change_doom(channel_id, 4)
        elif res_key == "critical_success":
            game_world.change_doom(channel_id, -1)
        elif res_key in ["success", "partial"]:
            # 휴식/친밀 장면이 아니면 행동 세금(Tax) 부과 (성공해도 둠 증가)
            if scene_type not in ["rest", "intimate"]:
                game_world.change_doom(channel_id, config.DOOM_ACTION_TAX)

        return roll_log, roll_log

    except Exception as e:
        logger.error(f"Failed to process judgment: {e}")
        return None, None
