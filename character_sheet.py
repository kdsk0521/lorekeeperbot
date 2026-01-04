import os
import logging

# 캐릭터 데이터가 저장될 디렉토리 경로
CHAR_DIR = os.path.join("data", "characters")

class NPCManager:
    """
    채널별 NPC의 상태(생존 여부, 현재 상황 등)를 관리하는 클래스입니다.
    현재는 메모리상에만 저장되지만, 필요 시 파일 저장 로직으로 확장 가능합니다.
    """
    def __init__(self):
        # {channel_id: {npc_name: {'desc': 설명, 'status': 상태}}} 구조로 저장
        self.npcs = {} 
    
    def add_npc(self, cid, name, desc):
        """새로운 NPC를 등록합니다."""
        if cid not in self.npcs:
            self.npcs[cid] = {}
        # 기본 상태는 'Active(활동 중)'으로 설정
        self.npcs[cid][name] = {"desc": desc, "status": "Active"}

    def update_npc_status(self, cid, name, status):
        """특정 NPC의 상태를 변경합니다. (예: 사망, 실종, 동행 중)"""
        if cid in self.npcs and name in self.npcs[cid]:
            self.npcs[cid][name]["status"] = status

    def get_npc_summary(self, cid):
        """
        AI 프롬프트에 주입하기 위해 해당 채널의 모든 NPC 정보를 문자열로 요약합니다.
        Return 예시: "엘프(숲의 파수꾼, Active) | 촌장(마을 대표, Dead)"
        """
        if cid not in self.npcs or not self.npcs[cid]:
            return None
        return " | ".join([f"{n}({i['desc']}, {i['status']})" for n, i in self.npcs[cid].items()])
    
    def clear(self, cid):
        """해당 채널의 NPC 데이터를 모두 초기화합니다."""
        if cid in self.npcs:
            del self.npcs[cid]

# 전역 NPC 매니저 인스턴스 생성
npc_memory = NPCManager()

def initialize_folders():
    """캐릭터 데이터 저장을 위한 폴더가 없으면 생성합니다."""
    if not os.path.exists(CHAR_DIR):
        try:
            os.makedirs(CHAR_DIR)
        except Exception as e:
            logging.error(f"캐릭터 폴더 생성 실패: {e}")

def get_character(char_name):
    """
    캐릭터 이름에 해당하는 설정 데이터를 반환합니다.
    (현재는 GM/Narrator 기본값만 반환하지만, 추후 확장 가능)
    """
    return {"name": "Narrator", "description": "GM"}

def get_npc_summary(channel_id):
    """NPC 매니저의 요약 기능을 외부에서 호출하기 위한 래퍼 함수"""
    return npc_memory.get_npc_summary(channel_id)

def reset_npc_status(channel_id):
    """세션 리셋 시 NPC 정보도 함께 초기화하는 함수"""
    npc_memory.clear(channel_id)