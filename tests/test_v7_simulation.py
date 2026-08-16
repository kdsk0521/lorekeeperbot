
import sys
import os
import math
import io

# Force UTF-8 Output
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

# Add project root to path
sys.path.append(r"c:\Users\kdsk\Desktop\lorekeeperbot\lorekeeperbot")

# Mock Domain Manager
class MockDomainManager:
    def __init__(self):
        self.data = {}
        self.world = {"doom": 0}

    def get_participant_data(self, cid, uid):
        return self.data.get(uid, {})
    
    def save_participant_data(self, cid, uid, data):
        self.data[uid] = data
        
    def add_to_ai_memory_list(self, cid, uid, key, item):
        p = self.data.get(uid, {})
        mem = p.setdefault("ai_memory", {})
        lst = mem.setdefault(key, [])
        lst.append(item)
        
    def get_world_state(self, cid):
        return self.world
    
    def update_world_state(self, cid, val):
        self.world = val

# Monkey Patch
import domain_manager
mock_dm = MockDomainManager()
domain_manager.get_participant_data = mock_dm.get_participant_data
domain_manager.save_participant_data = mock_dm.save_participant_data
domain_manager.add_to_ai_memory_list = mock_dm.add_to_ai_memory_list
domain_manager.get_world_state = mock_dm.get_world_state
domain_manager.update_world_state = mock_dm.update_world_state

import game_character
import game_world
import config

def test_v7_mental_system():
    print("=== Test 1: Mental System ===")
    
    # Init User
    uid = "user1"
    user_data = {
        "mask": "TestChar", 
        "ai_memory": {
            "mental": {"value": 100, "last_delta": 0}
        }
    }
    mock_dm.save_participant_data("test", uid, user_data)
    
    # 1. Damage (Calm -> Shake)
    print("\n[Step 1] Taking 40 Mental Damage...")
    msg = game_character.update_mental(user_data, -40, "Shock", "test", uid)
    print(f"Result: {msg}")
    val = user_data["ai_memory"]["mental"]["value"]
    print(f"Current Value: {val} (Expected 60)")
    assert val == 60
    assert "동요" in msg
    
    # 2. Damage (Shake -> Panic -> Collapse)
    print("\n[Step 2] Taking 55 Mental Damage (To Collapse)...")
    msg = game_character.update_mental(user_data, -55, "Terror", "test", uid)
    print(f"Result: {msg}")
    val = user_data["ai_memory"]["mental"]["value"]
    print(f"Current Value: {val} (Expected 5)")
    assert val == 5
    assert "붕괴" in msg
    
    # 3. Trauma Awakening
    print("\n[Step 3] Healing 10 (Trauma Awakening)...")
    msg = game_character.update_mental(user_data, 10, "Epiphany", "test", uid)
    print(f"Result: {msg}")
    val = user_data["ai_memory"]["mental"]["value"]
    print(f"Current Value: {val} (Expected 90)")
    assert val == 90
    assert "각성" in msg
    
    # Check Passive
    passives = user_data["ai_memory"].get("passives", [])
    print(f"Passives: {passives}")
    assert any("Trauma" in p["name"] for p in passives)
    print("✅ Mental System Verified")

# [2026-08-11 비일상적응도 삭제] Test 2(Adaptation Formula) / Test 3(Abnormal Encounter) 제거 —
# 검사 대상 calculate_adaptation_pct·apply_abnormal_impact가 모두 사라짐
# (apply_abnormal_impact는 이 패스 이전에 이미 삭제되어 Test 3은 깨진 상태였음).

def test_v7_doom_update():
    print("\n=== Test 2: Doom Update (Hidden) ===")
    
    channel_id = "test_ch"
    mock_dm.world["doom"] = 10
    
    # 1. Small Update (No Stage Change)
    msg = game_world.change_doom(channel_id, 5)
    print(f"Doom 10 -> 15: '{msg}' (Expected non-empty)")
    assert msg != ""
    assert mock_dm.world["doom"] == 15
    
    # 2. Stage Change (15 -> 25) [Stage 0(0-20) -> Stage 1(20-40)]
    msg = game_world.change_doom(channel_id, 10)
    print(f"Doom 15 -> 25: '{msg}'")
    assert msg != ""
    assert mock_dm.world["doom"] == 25
    
    print("✅ Doom Update Verified")

if __name__ == "__main__":
    test_v7_mental_system()
    test_v7_doom_update()
