import sys
import os
import asyncio
from unittest.mock import MagicMock

# Add current directory to path
sys.path.append(os.path.dirname(os.path.abspath(__file__)))
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')

import config
import domain_manager
import game_world
import main

async def test_doom_progression():
    print("Testing Doom Progression Mechanics...")
    channel_id = "TEST_CH_DOOM"
    
    # 1. Setup Initial State
    domain_manager.reset_session_state(channel_id)
    world = domain_manager.get_world_state(channel_id)
    world["doom"] = 10
    world["time_ticks"] = 0
    world["time_slot"] = "오후"
    world["risk_level"] = "Extreme" # Should trigger +4 doom per check
    domain_manager.update_world_state(channel_id, world)
    
    print(f"Initial Doom: {world['doom']}%")
    
    # 2. Test 5-Tick Logic in process_time_flow
    print("\n[Test 1] Adding 6 ticks (Should trigger 1 Doom check)...")
    time_flow = {"ticks": 6, "duration": "short"}
    msg = await main.process_time_flow(channel_id, time_flow)
    
    world = domain_manager.get_world_state(channel_id)
    print(f"Update Message: {msg}")
    print(f"New Doom: {world['doom']}% (Expected 14% if Extreme risk applied +4)")
    print(f"Remaining Ticks: {world['time_ticks']}")
    
    assert world['doom'] == 14, f"Doom mismatch: {world['doom']} != 14"
    assert world['time_ticks'] == 6, f"Tick mismatch: {world['time_ticks']} != 6"

    # 3. Test Failure Penalty logic is harder to test directly without mocking Discord message
    # but we can check the change_doom function directly
    print("\n[Test 2] Simulating Failure Penalty (+1)...")
    fb = game_world.change_doom(channel_id, 1)
    print(f"Feedback: {fb}")
    world = domain_manager.get_world_state(channel_id)
    print(f"New Doom: {world['doom']}% (Expected 15%)")
    assert world['doom'] == 15

    print("\n[Test 3] Simulating Critical Failure Penalty (+4)...")
    fb = game_world.change_doom(channel_id, 4)
    print(f"Feedback: {fb}")
    world = domain_manager.get_world_state(channel_id)
    print(f"New Doom: {world['doom']}% (Expected 19%)")
    assert world['doom'] == 19

    # 4. Test Anomaly Release (-3)
    print("\n[Test 4] Simulating Anomaly Release (-3)...")
    fb = game_world.change_doom(channel_id, config.ANOMALY_DOOM_COST)
    print(f"Feedback: {fb}")
    world = domain_manager.get_world_state(channel_id)
    print(f"New Doom: {world['doom']}% (Expected 16%)")
    assert world['doom'] == 16

    print("\n✅ SUCCESS: Doom progression and penalty logic verified.")

if __name__ == "__main__":
    asyncio.run(test_doom_progression())
