"""
Lorekeeper TRPG Bot - NPC Manager
Handles NPC creation, updates, attitude system, and extraction from domain.
Extracted from domain_manager.py and game_system.py
"""

import time
import random
from typing import Dict, Any, Optional, List
import domain_manager

# =========================================================
# NPC CRUD operations (Wraps domain_manager for now)
# =========================================================

def get_npcs(channel_id: str) -> Dict[str, Dict[str, Any]]:
    return domain_manager.get_npcs(channel_id)

def get_npc(channel_id: str, name: str) -> Optional[Dict[str, Any]]:
    return domain_manager.get_npc(channel_id, name)

def update_npc(channel_id: str, name: str, data: Dict[str, Any]) -> None:
    domain_manager.update_npc(channel_id, name, data)

def delete_npc(channel_id: str, name: str) -> bool:
    return domain_manager.delete_npc(channel_id, name)

def handle_identity_reveal(channel_id: str, old_name: str, new_name: str, reason: str = "") -> str:
    """
    NPC 정체 발각 (OldName -> NewName) 처리
    """
    if old_name == new_name: return "⚠️ 이름이 동일합니다."
    
    npc_data = get_npc(channel_id, old_name)
    if not npc_data:
        # 혹시 이미 바뀌었거나 로어 NPC일 수 있음.
        # 로어 NPC라면 새 세션 NPC 항목을 생성?
        # 여기서는 세션 데이터 내에서만 처리한다고 가정.
        return f"⚠️ NPC '{old_name}' 데이터가 없습니다."
        
    # 데이터 복사 및 메타데이터 추가
    new_data = npc_data.copy()
    
    # [FIX] Source 보존 (기본값 보존)
    if "source" not in new_data:
        new_data["source"] = "session"
        
    new_data["identity_history"] = new_data.get("identity_history", [])
    new_data["identity_history"].append({
        "old_name": old_name,
        "revealed_at": time.strftime('%Y-%m-%d %H:%M'),
        "reason": reason
    })
    
    # 새 항목 생성
    update_npc(channel_id, new_name, new_data)
    
    # 구 항목 제거 (선택적: Redirect를 남길 수도 있으나, 혼동 방지 위해 제거가 깔끔)
    delete_npc(channel_id, old_name)
    
    # 태도 정보도 이동
    att = get_npc_attitude(channel_id, old_name)
    if att:
        # 기존 태도 삭제하고 새 이름으로 등록 (domain_manager 기능 한계로 직접 조작 필요할 수 있으나, update_npc_attitude 사용)
        update_npc_attitude(channel_id, new_name, att.get("attitude", "neutral"), att.get("reason", "") + " (Identity Reveal)")
        # 태도 삭제 API가 없으므로... (TODO: Add delete attitude support if needed, or leave it orphaned)
    
    return f"🎭 **정체 드러남:** {old_name} ➔ {new_name}"

# =========================================================
# NPC ATTITUDE SYSTEM
# =========================================================

def update_npc_attitude(channel_id: str, npc_name: str, attitude: str, reason: str = "") -> None:
    """NPC의 PC에 대한 태도 업데이트"""
    domain_manager.update_npc_attitude(channel_id, npc_name, attitude, reason)

def get_npc_attitudes(channel_id: str) -> Dict[str, Dict]:
    """저장된 NPC 태도 조회"""
    return domain_manager.get_npc_attitudes(channel_id)

def get_npc_attitude(channel_id: str, npc_name: str) -> Optional[Dict]:
    """특정 NPC의 태도 조회"""
    return domain_manager.get_npc_attitude(channel_id, npc_name)

# =========================================================
# NPC SIMULATION
# =========================================================

def get_npc_time_progression(channel_id: str) -> List[str]:
    """
    시간 경과에 따른 NPC 상태 변화 힌트 생성
    """
    npcs = get_npcs(channel_id)
    world = domain_manager.get_world_state(channel_id)
    time_slot = world.get("time_slot", "오후")
    
    hints = []
    
    # 시간대별 일반적 NPC 활동
    time_activities = {
        "새벽": ["잠들어 있다", "이른 기상 준비", "야간 근무 마무리", "깊은 잠에 빠져 있다"],
        "오전": ["아침 식사", "일과 시작", "청소/정리", "분주하게 움직임"],
        "오후": ["업무 중", "점심 후 활동", "외출", "나른하게 휴식"],
        "황혼": ["퇴근 준비", "저녁 준비", "휴식", "하루를 정리함"],
        "저녁": ["저녁 식사", "여가 활동", "TV 시청", "술자리"],
        "심야": ["잠자리 준비", "야식", "늦은 작업", "비밀스러운 만남"]
    }
    
    activities = time_activities.get(time_slot, ["활동 중"])
    
    for npc_name, npc_data in npcs.items():
        activity = random.choice(activities)
        hints.append(f"{npc_name}: {activity}")
    
    return hints

def clear_session_npcs(channel_id: str) -> int:
    """
    세션 전용 NPC (source != 'lore') 일괄 삭제
    Returns: 삭제된 NPC 수
    """
    d = domain_manager.get_domain(channel_id)
    npcs = d.get("npcs", {})
    to_delete = []
    
    for name, data in npcs.items():
        if data.get("source", "session") != "lore":
            to_delete.append(name)
            
    for name in to_delete:
        del npcs[name]
        
    domain_manager.save_domain(channel_id, d)
    return len(to_delete)
