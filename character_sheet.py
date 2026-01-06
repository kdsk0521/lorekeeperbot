import domain_manager

class NPCManager:
    """
    NPC 데이터를 domain_manager를 통해 파일에 영구 저장/관리합니다.
    """
    def add_npc(self, cid, name, desc):
        """새로운 NPC를 추가하거나 기존 정보를 업데이트합니다."""
        npc_data = {"desc": desc, "status": "Active"}
        domain_manager.update_npc(cid, name, npc_data)

    def update_npc_status(self, cid, name, status):
        """NPC의 상태(Active, Dead, Missing 등)를 변경합니다."""
        npcs = domain_manager.get_npcs(cid)
        if name in npcs:
            npcs[name]["status"] = status
            domain_manager.update_npc(cid, name, npcs[name])

    def get_npc_summary(self, cid):
        """현재 활성화된 NPC 목록을 요약하여 문자열로 반환합니다."""
        npcs = domain_manager.get_npcs(cid)
        if not npcs: return None
        
        # 상태가 Active인 NPC만 보여주거나, 상태를 함께 표기
        summary_list = []
        for n, data in npcs.items():
            status = data.get('status', 'Active')
            desc = data.get('desc', '')
            # 너무 긴 설명은 잘라서 표시
            short_desc = desc[:20] + "..." if len(desc) > 20 else desc
            summary_list.append(f"{n} ({status}): {short_desc}")
            
        return " | ".join(summary_list)
    
    def clear(self, cid):
        """[수정됨] 해당 채널의 모든 NPC 데이터를 삭제합니다."""
        # domain_manager에는 npc만 따로 지우는 기능이 없으므로
        # 전체 도메인 데이터를 가져와서 npc 필드만 초기화 후 저장해야 함.
        # 하지만 reset_domain이 호출되면 파일 자체가 삭제되므로,
        # 여기서는 메모리 상의 처리나 특정 로직이 필요할 때 사용.
        # 현재 구조상 domain_manager.reset_domain()이 더 강력하므로 pass로 두어도 무방하나,
        # 명시적인 초기화가 필요하다면 아래 로직을 사용.
        d = domain_manager.get_domain(cid)
        d["npcs"] = {}
        domain_manager.save_domain(cid, d)

npc_memory = NPCManager()

# 외부에서 호출하기 편하게 래퍼 함수 제공
def get_npc_summary(channel_id): 
    return npc_memory.get_npc_summary(channel_id)

def reset_npc_status(channel_id): 
    """[수정됨] NPC 데이터 완전 초기화"""
    npc_memory.clear(channel_id)