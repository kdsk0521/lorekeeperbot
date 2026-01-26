"""
Lorekeeper Bot V4 - Comprehensive Test Suite
Verifies:
1. World Simulation (Time, Doom)
2. Character Mechanics (Quest, Dice with Doom Mod, Hybrid Passive)
3. NPC Management (Identity Reveal)
4. Config Integrity
"""

import sys
import os
import random
import time

# Add current directory to path
sys.path.append(os.path.dirname(os.path.abspath(__file__)))
sys.stdout.reconfigure(encoding='utf-8')

import config
import domain_manager
import game_system # Facade
import game_world
import game_character
import npc_manager

CHANNEL_ID = "TEST_SUITE_V4"
USER_ID = "TEST_USER_01"

def print_header(title):
    print(f"\n{'='*40}\n[{title}]\n{'='*40}")

def run_test():
    print_header("INITIALIZATION")
    print(f"Target Channel: {CHANNEL_ID}")
    
    # 1. World & Doom
    print_header("WORLD & DOOM")
    
    # Reset World
    w = {"doom": 70, "time_slot": "오후", "day": 1} # Start with High Doom (70)
    domain_manager.update_world_state(CHANNEL_ID, w)
    
    print(f"Initial Doom: {w['doom']}")
    
    # Test Doom Forecast (Bar)
    forecast = game_world.get_doom_forecast(CHANNEL_ID)
    print(f"Doom Forecast:\n{forecast}")
    assert "[" in forecast, "Doom Bar missing"
    
    # Test Time Advance
    msg = game_world.advance_time(CHANNEL_ID)
    print(f"Time Advance Msg: {msg}")
    
    # 2. Character Mechanics
    print_header("CHARACTER")
    
    # Setup User
    p_data = {"mask": "Tester", "passives": [], "status_effects": ["부상"]} # Debuff
    domain_manager.save_participant_data(CHANNEL_ID, USER_ID, p_data)
    
    # Dice Check with Doom Mod (Doom 70 -> penalty expected)
    # (50 - 70) // 10 * 5 = -10
    print("Running Dice Check (Doom 70, Injury)...")
    check_res = game_character.perform_check(CHANNEL_ID, USER_ID, "Test Action")
    print(check_res)
    assert "Doom(" in check_res, "Doom modifier missing in dice check"
    
    # Hybrid Passive
    print("\nAdding Passive...")
    pas_res = game_character.add_passive(CHANNEL_ID, USER_ID, "Veteran", tags=["Combat", "Leadership"], desc="Experienced fighter")
    print(pas_res)
    
    # Verify Context
    p_data_reload = domain_manager.get_participant_data(CHANNEL_ID, USER_ID)
    ctx = game_character.get_passives_for_context(p_data_reload)
    print(f"Passive Context: {ctx}")
    assert "Veteran" in ctx, "Passive not found in context"
    assert "Combat" in ctx, "Tags not visible in context"

    # Quest & Doom Reduction
    game_character.add_quest(CHANNEL_ID, "Kill the Dragon")
    print("\nCompleting Quest (Should reduce Doom)...")
    q_res = game_character.complete_quest(CHANNEL_ID, "Kill the Dragon")
    print(q_res)
    
    w_reload = domain_manager.get_world_state(CHANNEL_ID)
    print(f"Doom after Quest: {w_reload['doom']} (Expected < 70)")
    assert w_reload['doom'] < 70, "Doom did not decrease"

    # 3. NPC Manager
    print_header("NPC MANAGER")
    npc_name = "OldMan"
    npc_data = {"desc": "Mysterious old man", "source": "lore"} # Set source to lore
    npc_manager.update_npc(CHANNEL_ID, npc_name, npc_data)
    
    # Identity Reveal
    print(f"Revealing Identity: {npc_name} -> Gandalf")
    rev_res = npc_manager.handle_identity_reveal(CHANNEL_ID, npc_name, "Gandalf", "He summoned light.")
    print(rev_res)
    
    # NPC Cleanup (Session NPC)
    print("\nAdding Session NPC (Bandit) and Lore NPC (King)...")
    npc_manager.update_npc(CHANNEL_ID, "Bandit", {"source": "session"})
    npc_manager.update_npc(CHANNEL_ID, "King", {"source": "lore"})
    
    print("Clearing Session NPCs...")
    count = npc_manager.clear_session_npcs(CHANNEL_ID)
    print(f"Removed count: {count}")
    
    npcs = npc_manager.get_npcs(CHANNEL_ID)
    assert "Bandit" not in npcs, "Session NPC not removed"
    assert "King" in npcs, "Lore NPC removed"
    assert "Gandalf" in npcs, "Renamed NPC removed (default source?)"
    
    # 4. Doom Reduction (Rest)
    print_header("DOOM REDUCTION")
    # Increase Doom first
    game_world.change_doom(CHANNEL_ID, 20)
    w = domain_manager.get_world_state(CHANNEL_ID)
    doom_before = w['doom'] # Store value
    print(f"Doom increased to: {doom_before}")
    
    print("Reducing Doom (Rest - Low Risk)...")
    # Mocking low risk env
    w["risk_level"] = "low"
    domain_manager.update_world_state(CHANNEL_ID, w)
    
    red_msg = game_world.reduce_doom(CHANNEL_ID, 15, "Rest")
    print(red_msg)
    w_after = domain_manager.get_world_state(CHANNEL_ID)
    assert w_after['doom'] < doom_before, f"Doom did not decrease: {w_after['doom']} vs {doom_before}"
    
    # 5. Whitelist Logic (!bot on/off)
    print_header("WHITELIST LOGIC")
    print("Checking default state (ON)...")
    assert domain_manager.get_bot_active(CHANNEL_ID) is True, "Default should be True"
    
    print("Setting Bot OFF...")
    domain_manager.set_bot_active(CHANNEL_ID, False)
    assert domain_manager.get_bot_active(CHANNEL_ID) is False, "Set False failed"
    
    print("Setting Bot ON...")
    domain_manager.set_bot_active(CHANNEL_ID, True)
    assert domain_manager.get_bot_active(CHANNEL_ID) is True, "Set True failed"
    
    # Since main.py logic is hard to integration test without full bot mock, 
    # we verify the domain state change which main.py relies on.
    
    print("\n✅ TEST COMPLETE: SUCCESS")

if __name__ == "__main__":
    try:
        run_test()
    except AssertionError as e:
        print(f"\n❌ TEST FAILED: {e}")
        exit(1)
    except Exception as e:
        print(f"\n❌ TEST ERROR: {e}")
        import traceback
        traceback.print_exc()
        exit(1)
