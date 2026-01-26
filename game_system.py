"""
Lorekeeper TRPG Bot - Game System Module (Facade)
This module is now a facade for game_world, game_character, and npc_manager.
Maintained for backward compatibility.
"""

# Re-export symbols from new modules
from game_world import *
from game_character import *
from npc_manager import *

import game_world
import game_character
import npc_manager
import domain_manager

# Ensure all original functions are available
# World
get_time_slots = game_world.get_time_slots
get_weather_types = game_world.get_weather_types
advance_time = game_world.advance_time
calculate_doom_increase = game_world.calculate_doom_increase
get_random_doom_event = game_world.get_random_doom_event
change_doom = game_world.change_doom
_get_doom_description = game_world._get_doom_description
get_world_context = game_world.get_world_context
get_doom_forecast = game_world.get_doom_forecast

# Character / Quest
get_quest_board = domain_manager.get_quest_board
get_active_quests = game_character.get_active_quests
get_memos = game_character.get_memos

# Character / Quest
add_quest = game_character.add_quest
complete_quest = game_character.complete_quest
remove_quest = game_character.remove_quest
add_memo = game_character.add_memo
remove_memo = game_character.remove_memo
resolve_memo_auto = game_character.resolve_memo_auto
get_active_quests_text = game_character.get_active_quests_text
get_memos_text = game_character.get_memos_text
get_status_message = game_character.get_status_message
get_objective_context = game_character.get_objective_context
update_inventory = game_character.update_inventory
update_status_effect = game_character.update_status_effect
perform_check = game_character.perform_check
get_status_summary = game_character.get_status_summary
calculate_status_doom_contribution = game_character.calculate_status_doom_contribution
calculate_normality = game_character.calculate_normality
expose_to_abnormal = game_character.expose_to_abnormal
get_abnormal_context = game_character.get_abnormal_context
export_session_history = game_character.export_session_history
export_chronicle_book = game_character.export_chronicle_book
generate_chronicle_from_history = game_character.generate_chronicle_from_history
export_lore_data = game_character.export_lore_data
get_lore_book = game_character.get_lore_book

# NPC
get_npc_time_progression = npc_manager.get_npc_time_progression
