"""
Lorekeeper TRPG Bot - Character Sheet Module
NPC 데이터를 관리합니다.
"""

import logging
from typing import Optional, Dict, Any, List

import domain_manager

# =========================================================
# 상수 정의
# =========================================================
MAX_DESC_PREVIEW_LENGTH = 20  # NPC 설명 미리보기 최대 길이
DEFAULT_NPC_STATUS = "Active"


class NPCManager:
    """
    NPC 데이터를 domain_manager를 통해 파일에 영구 저장/관리합니다.
    """
    
    def add_npc(
        self,
        channel_id: str,
        name: str,
        description: str
    ) -> None:
        """
        새로운 NPC를 추가하거나 기존 정보를 업데이트합니다.
        
        Args:
            channel_id: 채널 ID
            name: NPC 이름
            description: NPC 설명
        """
        if not name:
            logging.warning("NPC 이름이 비어있어 추가하지 않음")
            return
        
        npc_data = {
            "desc": description or "설명 없음",
            "status": DEFAULT_NPC_STATUS
        }
        domain_manager.update_npc(channel_id, name, npc_data)
        logging.info(f"NPC 추가/업데이트: {name}")
    
    def update_npc_status(
        self,
        channel_id: str,
        name: str,
        status: str
    ) -> bool:
        """
        NPC의 상태를 변경합니다.
        
        Args:
            channel_id: 채널 ID
            name: NPC 이름
            status: 새 상태 (Active, Dead, Missing 등)
        
        Returns:
            성공 여부
        """
        npcs = domain_manager.get_npcs(channel_id)
        
        if name not in npcs:
            logging.warning(f"NPC를 찾을 수 없음: {name}")
            return False
        
        npcs[name]["status"] = status
        domain_manager.update_npc(channel_id, name, npcs[name])
        logging.info(f"NPC 상태 변경: {name} -> {status}")
        return True
    
    def get_npc(
        self,
        channel_id: str,
        name: str
    ) -> Optional[Dict[str, Any]]:
        """
        특정 NPC의 정보를 가져옵니다.
        
        Args:
            channel_id: 채널 ID
            name: NPC 이름
        
        Returns:
            NPC 데이터 딕셔너리 또는 None
        """
        npcs = domain_manager.get_npcs(channel_id)
        return npcs.get(name)
    
    def get_npc_summary(self, channel_id: str) -> Optional[str]:
        """
        현재 NPC 목록을 요약하여 문자열로 반환합니다.
        
        Args:
            channel_id: 채널 ID
        
        Returns:
            NPC 요약 문자열 또는 None (NPC가 없을 경우)
        """
        npcs = domain_manager.get_npcs(channel_id)
        
        if not npcs:
            return None
        
        summary_list = []
        for name, data in npcs.items():
            status = data.get('status', DEFAULT_NPC_STATUS)
            desc = data.get('desc', '')
            
            # 너무 긴 설명은 잘라서 표시
            if len(desc) > MAX_DESC_PREVIEW_LENGTH:
                short_desc = desc[:MAX_DESC_PREVIEW_LENGTH] + "..."
            else:
                short_desc = desc
            
            summary_list.append(f"{name} ({status}): {short_desc}")
        
        return " | ".join(summary_list)
    
    def get_npc_list(
        self,
        channel_id: str,
        status_filter: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """
        NPC 목록을 리스트로 반환합니다.
        
        Args:
            channel_id: 채널 ID
            status_filter: 특정 상태만 필터링 (None이면 전체)
        
        Returns:
            NPC 정보 리스트
        """
        npcs = domain_manager.get_npcs(channel_id)
        result = []
        
        for name, data in npcs.items():
            npc_info = {
                "name": name,
                "desc": data.get("desc", ""),
                "status": data.get("status", DEFAULT_NPC_STATUS)
            }
            
            if status_filter is None or npc_info["status"] == status_filter:
                result.append(npc_info)
        
        return result
    
    def remove_npc(self, channel_id: str, name: str) -> bool:
        """
        NPC를 삭제합니다.
        
        Args:
            channel_id: 채널 ID
            name: NPC 이름
        
        Returns:
            성공 여부
        """
        d = domain_manager.get_domain(channel_id)
        
        if name not in d.get("npcs", {}):
            return False
        
        del d["npcs"][name]
        domain_manager.save_domain(channel_id, d)
        logging.info(f"NPC 삭제: {name}")
        return True
    
    def clear(self, channel_id: str) -> None:
        """
        해당 채널의 모든 NPC 데이터를 삭제합니다.
        
        Args:
            channel_id: 채널 ID
        """
        d = domain_manager.get_domain(channel_id)
        d["npcs"] = {}
        domain_manager.save_domain(channel_id, d)
        logging.info(f"채널 {channel_id}의 모든 NPC 초기화됨")


# 싱글톤 인스턴스 생성
npc_memory = NPCManager()


# =========================================================
# 외부 호출용 래퍼 함수
# =========================================================
def get_npc_summary(channel_id: str) -> Optional[str]:
    """NPC 요약 정보를 반환합니다."""
    return npc_memory.get_npc_summary(channel_id)


def reset_npc_status(channel_id: str) -> None:
    """NPC 데이터를 완전 초기화합니다."""
    npc_memory.clear(channel_id)


def add_npc(channel_id: str, name: str, description: str) -> None:
    """NPC를 추가합니다."""
    npc_memory.add_npc(channel_id, name, description)


def get_npc(channel_id: str, name: str) -> Optional[Dict[str, Any]]:
    """특정 NPC 정보를 가져옵니다."""
    return npc_memory.get_npc(channel_id, name)


def update_npc_status(channel_id: str, name: str, status: str) -> bool:
    """NPC 상태를 변경합니다."""
    return npc_memory.update_npc_status(channel_id, name, status)
