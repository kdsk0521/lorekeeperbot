import domain_manager

class NPCManager:
    """NPC 데이터를 domain_manager를 통해 파일에 영구 저장/관리합니다."""
    def add_npc(self, cid, name, desc):
        npc_data = {"desc": desc, "status": "Active"}
        domain_manager.update_npc(cid, name, npc_data)

    def update_npc_status(self, cid, name, status):
        npcs = domain_manager.get_npcs(cid)
        if name in npcs:
            npcs[name]["status"] = status
            domain_manager.update_npc(cid, name, npcs[name])

    def get_npc_summary(self, cid):
        npcs = domain_manager.get_npcs(cid)
        if not npcs: return None
        return " | ".join([f"{n}({data['desc']}, {data['status']})" for n, data in npcs.items()])
    
    def clear(self, cid): pass

npc_memory = NPCManager()

def get_npc_summary(channel_id): return npc_memory.get_npc_summary(channel_id)
def reset_npc_status(channel_id): pass