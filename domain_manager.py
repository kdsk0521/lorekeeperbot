"""
Lorekeeper TRPG Bot - Domain Manager Facade
This module acts as a facade, delegating functionality to specialized modules.
"""

# Common imports
from domain_io import (
    initialize_folders, 
    get_domain, save_domain, reset_domain, reset_session_data,
    append_history, get_history,
    set_session_lock, is_session_locked,
    get_response_mode, set_response_mode,
    get_world_state, update_world_state,
    set_current_location, set_current_risk, set_location_rules,
    set_world_constraints, get_world_constraints,
    set_active_threads, get_active_threads,
    set_temporal_context, get_temporal_context,
    get_quest_board, update_quest_board,
    get_session_ai_memory, update_session_ai_memory,
    get_session_ai_memory_for_prompt,
    resolve_thread, add_key_event,
    get_session_file_path, get_lore_file_path, get_rules_file_path, get_lore_original_file_path,
    load_json, save_json, load_text, save_text # I/O exposure
)

from domain_participant import (
    update_participant, get_participant_data, save_participant_data, save_participant_summary,
    get_participant_status, set_participant_status,
    get_economy, update_economy,
    set_default_pc_info, get_default_pc_info, clear_default_pc_info, apply_pc_info_to_user,
    get_ai_memory, update_ai_memory, set_ai_memory_field, add_to_ai_memory_list, remove_from_ai_memory_list,
    get_full_ai_context, get_ai_memory_for_prompt,
    get_user_mask, set_user_mask, get_user_description, set_user_description,
    get_unified_player_info, get_integrated_status,
    get_party_status_context
)

from domain_content import (
    get_lore, append_lore, reset_lore,
    get_lore_original, save_lore_original,
    get_lore_with_npcs,
    get_npcs, update_npc, delete_npc, rename_npc,
    get_rules, append_rules, reset_rules,
    get_rules_mode, set_rules_mode,
    set_custom_rules_from_file, get_custom_rules_part,
    get_active_genres, set_active_genres,
    get_custom_tone, set_custom_tone,
    get_scene_type, set_scene_type,
    get_growth_system
)

import config

# Re-export config constants for compatibility
DEFAULT_LORE = config.DEFAULT_LORE
DEFAULT_RULES = config.DEFAULT_RULES
DEFAULT_WORLD_STATE = config.DEFAULT_WORLD_STATE
DATA_DIR = config.DATA_DIR
SESSIONS_DIR = config.SESSIONS_DIR
LORE_DIR = config.LORE_DIR
RULES_DIR = config.RULES_DIR
MAX_HISTORY_LENGTH = config.MAX_HISTORY_LENGTH
MAX_DESC_LENGTH = config.MAX_DESC_LENGTH
