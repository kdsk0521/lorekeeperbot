"""
Lorekeeper TRPG Bot - Character Sheet Module
NPC 및 플레이어 캐릭터 데이터를 관리합니다.
"""

import logging
from typing import Optional, Dict, Any, List

import domain_manager

# =========================================================
# 상수 정의
# =========================================================
MAX_DESC_PREVIEW_LENGTH = 20
DEFAULT_NPC_STATUS = "Active"

# =========================================================
# 유틸리티 함수
# =========================================================
def find_similar_key(existing_keys: list, target: str) -> str:
    """
    유사한 키를 찾습니다. (중복 방지)
    - 대소문자 무시, 공백 무시
    - 포함 관계 (e.g. 'Stethoscope' <-> 'Old Stethoscope')
    """
    if not target:
        return None
        
    target_clean = target.strip().lower()
    if not target_clean:
        return None
        
    # 1. Exact match (insensitive)
    for key in existing_keys:
        if key.strip().lower() == target_clean:
            return key
            
    # 2. Containment match (Riskier, but solves descriptions causing dupes)
    if len(target_clean) >= 3:
        for key in existing_keys:
            existing_clean = key.strip().lower()
            if target_clean in existing_clean or existing_clean in target_clean:
                return key
                
    return None

# =========================================================
# 플레이어 캐릭터 관리자 (NEW)
# =========================================================
class PlayerCharacterManager:
    """
    플레이어 캐릭터 데이터를 관리합니다.
    좌뇌 분석 결과를 받아 저장하고, AI 프롬프트용 컨텍스트를 생성합니다.
    """

    def get_character(self, channel_id: str, user_id: str) -> Dict[str, Any]:
        """
        플레이어 캐릭터 전체 데이터를 가져옵니다.

        Returns:
            {
                "mask": "캐릭터이름",
                "appearance": "외형",
                "personality": "성격",
                "background": "배경",
                "relationships": {"NPC": "관계"},
                "passives": ["패시브"],
                "known_info": ["정보"],
                "foreshadowing": ["복선"],
                "normalization": {"요소": "적응도"},
                "inventory": {"아이템": 수량},
                "economy": {"gold": 100},
                "status_effects": ["상태이상"]
            }
        """
        p_data = domain_manager.get_participant_data(channel_id, user_id)
        if not p_data:
            return {}

        ai_mem = p_data.get("ai_memory", {})

        return {
            "mask": p_data.get("mask", "Unknown"),
            "appearance": ai_mem.get("appearance", ""),
            "personality": ai_mem.get("personality", ""),
            "background": ai_mem.get("background", ""),
            "relationships": ai_mem.get("relationships", {}),
            "passives": ai_mem.get("passives", []),
            "abilities": ai_mem.get("abilities", ""),
            "sexual_characteristics": ai_mem.get("sexual_characteristics", ""),
            "normalization": ai_mem.get("normalization", {}),
            "inventory": p_data.get("inventory", {}),
            "economy": p_data.get("economy", {"gold": 0}),
            "status_effects": p_data.get("status_effects", [])
        }

    def apply_memory_updates(
        self,
        channel_id: str,
        user_id: str,
        player_mem_update: Dict[str, Any]
    ) -> List[str]:
        """
        좌뇌의 PlayerMemoryUpdate를 적용합니다.

        Args:
            channel_id: 채널 ID
            user_id: 유저 ID
            player_mem_update: 좌뇌 분석 결과의 PlayerMemoryUpdate

        Returns:
            업데이트 메시지 리스트 (알림용)
        """
        if not player_mem_update:
            return []

        messages = []
        current_mem = domain_manager.get_ai_memory(channel_id, user_id) or {}
        mem_updated = False


        # We will collect successful updates here to save them
        updates_to_save = {}

        # appearance (외형)
        if player_mem_update.get("appearance"):
            updates_to_save["appearance"] = player_mem_update["appearance"]
            short = player_mem_update['appearance'][:30]
            messages.append(f"👤 **외형:** {short}...")
            mem_updated = True

        # relationships (관계) - domain.npcs로 통합 저장
        if player_mem_update.get("relationships"):
            for name, desc in player_mem_update["relationships"].items():
                if name and desc:
                    # Rename Logic (Identity Reveal)
                    if " > " in name:
                        old_name, new_name = name.split(" > ", 1)
                        if domain_manager.rename_npc(channel_id, old_name.strip(), new_name.strip()):
                            messages.append(f"🔄 **Identity Revealed:** {old_name} ➔ {new_name}")
                            name = new_name.strip() # Update target name for relationship update logic
                    
                    # domain.npcs에 통합 저장 (NPC relationship 필드 업데이트)
                    npc_memory.update_npc_relationship(channel_id, name, desc)
                    messages.append(f"💞 **{name}**: {desc}")

        # passives (패시브/칭호)
        if player_mem_update.get("passives"):
            # Load current valid passives from domain (to check duplicates)
            # changes to updates_to_save['passives'] will be merged by update_ai_memory later
            current_passives = current_mem.get("passives", [])
            current_passives_norm = {p.strip().lower() for p in current_passives}
            
            new_passives = []
            
            for passive in player_mem_update["passives"]:
                if not passive:
                    continue
                
                # Check normalized to prevent "Skill" vs "Skill " duplication
                p_clean = passive.strip()
                p_norm = p_clean.lower()
                
                # Check against CURRENT memory
                if p_norm not in current_passives_norm:
                    new_passives.append(p_clean)
                    current_passives_norm.add(p_norm) 
                    messages.append(f"🏆 **{p_clean}**")
                    mem_updated = True
            
            if new_passives:
                updates_to_save["passives"] = new_passives

        # normalization (비일상 적응)
        if player_mem_update.get("normalization"):
            if "normalization" not in updates_to_save:
                updates_to_save["normalization"] = {}
                
            for thing, status in player_mem_update["normalization"].items():
                if thing and status:
                    updates_to_save["normalization"][thing] = status
                    messages.append(f"🌓 **{thing}**: {status}")
                    mem_updated = True
        
        # companions (동행자)
        if player_mem_update.get("companions"):
            new_companions = []
            current_companions = current_mem.get("companions", [])
            
            for companion in player_mem_update["companions"]:
                if companion and companion not in current_companions:
                     new_companions.append(companion)
                     messages.append(f"🐾 **{companion}**")
                     mem_updated = True
            
            if new_companions:
                updates_to_save["companions"] = new_companions

        # info_archive (NEW)
        if player_mem_update.get("info_archive"):
            new_info = []
            current_info = current_mem.get("archived_info", [])
            
            for info in player_mem_update["info_archive"]:
                if info and info not in current_info:
                    new_info.append(info)
                    mem_updated = True
            
            if new_info:
                updates_to_save["archived_info"] = new_info

        # foreshadowing_archive (NEW)
        if player_mem_update.get("foreshadowing_archive"):
            new_items = []
            current_fore = current_mem.get("archived_foreshadowing", [])
            
            for item in player_mem_update["foreshadowing_archive"]:
                if item and item not in current_fore:
                    new_items.append(item)
                    mem_updated = True
                    
            if new_items:
                updates_to_save["archived_foreshadowing"] = new_items

        # 저장
        if updates_to_save:
            domain_manager.update_ai_memory(channel_id, user_id, updates_to_save)

        if mem_updated:
            domain_manager.update_ai_memory(channel_id, user_id, current_mem)
            logging.info(f"[CharacterSheet] 메모리 업데이트: {messages}")

        return messages

    def apply_player_updates(
        self,
        channel_id: str,
        user_id: str,
        player_update: Dict[str, Any]
    ) -> List[str]:
        """
        좌뇌의 PlayerUpdate (인벤토리/골드/상태이상)를 적용합니다.
        """
        if not player_update:
            return []

        messages = []
        p_data = domain_manager.get_participant_data(channel_id, user_id)
        if not p_data:
            return messages

        updated = False

        # inventory_add
        if player_update.get("inventory_add"):
            if "inventory" not in p_data:
                p_data["inventory"] = {}
            for item, amount in player_update["inventory_add"].items():
                # Check for similar item
                similar = find_similar_key(p_data["inventory"].keys(), item)
                target_item = similar if similar else item
                
                p_data["inventory"][target_item] = p_data["inventory"].get(target_item, 0) + int(amount)
                messages.append(f"🎒 **+{target_item}** x{amount}")
            updated = True

        # inventory_remove
        if player_update.get("inventory_remove"):
            if "inventory" not in p_data:
                p_data["inventory"] = {}
            for item, amount in player_update["inventory_remove"].items():
                if item in p_data["inventory"]:
                    p_data["inventory"][item] = max(0, p_data["inventory"][item] - int(amount))
                    if p_data["inventory"][item] <= 0:
                        del p_data["inventory"][item]
                    messages.append(f"🎒 **-{item}** x{amount}")
            updated = True

        # gold_change
        if player_update.get("gold_change") is not None:
            if "economy" not in p_data:
                p_data["economy"] = {"gold": 0}
            change = int(player_update["gold_change"])
            p_data["economy"]["gold"] = max(0, p_data["economy"].get("gold", 0) + change)
            if change > 0:
                messages.append(f"💰 **+{change}**")
            elif change < 0:
                messages.append(f"💰 **{change}**")
            updated = True

        # status_add
        if player_update.get("status_add"):
            if "status_effects" not in p_data:
                p_data["status_effects"] = []
            for status in player_update["status_add"]:
                if status not in p_data["status_effects"]:
                    p_data["status_effects"].append(status)
                    messages.append(f"💫 **{status}**")
            updated = True

        # status_remove
        if player_update.get("status_remove"):
            if "status_effects" not in p_data:
                p_data["status_effects"] = []
            for status in player_update["status_remove"]:
                if status in p_data["status_effects"]:
                    p_data["status_effects"].remove(status)
                    messages.append(f"✨ **{status} 해제**")
            updated = True

        # 저장
        if updated:
            domain_manager.save_participant_data(channel_id, user_id, p_data)
            logging.info(f"[CharacterSheet] 플레이어 데이터 업데이트: {messages}")

        return messages

    def get_for_prompt(self, channel_id: str, user_id: str) -> str:
        """
        AI 프롬프트에 주입할 캐릭터 컨텍스트를 생성합니다.
        """
        char = self.get_character(channel_id, user_id)
        if not char or not char.get("mask"):
            return ""

        parts = [f"### [PLAYER CHARACTER: {char['mask']}]"]

        if char.get("appearance"):
            parts.append(f"외형: {char['appearance']}")

        if char.get("relationships"):
            rel_str = ", ".join([f"{k}({v})" for k, v in char["relationships"].items()])
            parts.append(f"관계: {rel_str}")

        if char.get("passives"):
            parts.append(f"패시브: {', '.join(char['passives'])}")

        if char.get("known_info"):
            info_only = [i for i in char["known_info"] if not i.startswith("동행자:")]
            companions = [i.replace("동행자: ", "") for i in char["known_info"] if i.startswith("동행자:")]
            if companions:
                parts.append(f"동행자: {', '.join(companions)}")
            if info_only:
                parts.append(f"알고 있는 것: {', '.join(info_only[:5])}")

        if char.get("foreshadowing"):
            parts.append(f"복선: {', '.join(char['foreshadowing'][:3])}")

        if char.get("normalization"):
            norm_str = ", ".join([f"{k}={v}" for k, v in char["normalization"].items()])
            parts.append(f"비일상 적응: {norm_str}")

        if char.get("abilities"):
            parts.append(f"능력/기술: {char['abilities']}")

        if char.get("sexual_characteristics"):
            parts.append(f"성적 특성 (NSFW): {char['sexual_characteristics']}")

        return "\n".join(parts) + "\n"


# 싱글톤 인스턴스
player_manager = PlayerCharacterManager()


# =========================================================
# 퀘스트/메모 업데이트 (NEW)
# =========================================================
def apply_quest_updates(channel_id: str, quest_update: Dict[str, Any]) -> List[str]:
    """
    좌뇌의 QuestUpdate를 적용합니다.

    Args:
        channel_id: 채널 ID
        quest_update: 좌뇌 분석 결과의 QuestUpdate
            - quest_add: 새 퀘스트 리스트
            - quest_complete: 완료된 퀘스트 리스트
            - memo_add: 새 메모 리스트

    Returns:
        업데이트 메시지 리스트 (알림용)
    """
    if not quest_update:
        return []

    messages = []

    # 퀘스트 보드 가져오기
    d = domain_manager.get_domain(channel_id)
    if "quest_board" not in d or not isinstance(d["quest_board"], dict):
        d["quest_board"] = {"active": [], "completed": [], "memos": [], "archive": [], "lore": []}
    board = d["quest_board"]

    updated = False

    # quest_add - 새 퀘스트 추가
    if quest_update.get("quest_add"):
        if "active" not in board:
            board["active"] = []
        for quest in quest_update["quest_add"]:
            if quest and quest not in board["active"]:
                board["active"].append(quest)
                messages.append(f"📜 **퀘스트:** {quest}")
                updated = True

    # quest_complete - 퀘스트 완료
    if quest_update.get("quest_complete"):
        if "active" not in board:
            board["active"] = []
        if "completed" not in board:
            board["completed"] = []
        for quest in quest_update["quest_complete"]:
            if quest:
                # 정확히 일치하거나 부분 일치하는 퀘스트 찾기
                matched = None
                for active_quest in board["active"]:
                    if quest in active_quest or active_quest in quest:
                        matched = active_quest
                        break

                if matched:
                    board["active"].remove(matched)
                    board["completed"].append(matched)
                    messages.append(f"✅ **퀘스트 완료:** {matched}")
                    updated = True

    # memo_add - 메모 추가
    if quest_update.get("memo_add"):
        if "memos" not in board:
            board["memos"] = []
        for memo in quest_update["memo_add"]:
            if memo and memo not in board["memos"]:
                board["memos"].append(memo)
                messages.append(f"📝 **메모:** {memo}")
                updated = True

    # memo_remove - 메모 삭제 (NEW)
    if quest_update.get("memo_remove"):
        for memo in quest_update["memo_remove"]:
            if memo:
                # quest_manager의 remove_memo 함수 활용 (직접 board 수정하지 않고 관리자 통해)
                # 여기서는 board를 직접 수정하는 방식 유지 (일관성 위해)
                # 또는 quest_manager 함수 호출로 변경 가능하나, 위 로직(직접 수정)과 통일성 유지.
                # 하지만, quest_manager에 이미 로직이 있으니 함수 호출이 더 안전할 수 있음.
                # 기존 코드가 직접 board 수정을 하고 있으므로 여기서도 직접 board 수정을 하되,
                # quest_manager 모듈이 관리하는 데이터 구조를 따름.
                if "memos" in board and memo in board["memos"]:
                    board["memos"].remove(memo)
                    messages.append(f"🗑️ **메모 삭제:** {memo}")
                    updated = True

    # memo_archive - 메모 보관 (NEW)
    if quest_update.get("memo_archive"):
        for memo in quest_update["memo_archive"]:
            if memo:
                # 메모 목록에서 제거하고 보관함으로 이동
                if "memos" in board and memo in board["memos"]:
                    board["memos"].remove(memo)
                
                if "archive" not in board:
                    board["archive"] = []
                
                if memo not in board["archive"]:
                    board["archive"].append(memo)
                    messages.append(f"🗄️ **메모 보관:** {memo}")
                    updated = True

    # 저장
    if updated:
        domain_manager.update_quest_board(channel_id, board)
        logging.info(f"[CharacterSheet] 퀘스트/메모 업데이트: {messages}")

    return messages


# =========================================================
# 외부 호출용 래퍼 함수 (NEW)
# =========================================================
def get_player_character(channel_id: str, user_id: str) -> Dict[str, Any]:
    """플레이어 캐릭터 전체 데이터를 반환합니다."""
    return player_manager.get_character(channel_id, user_id)


def apply_memory_updates(channel_id: str, user_id: str, updates: Dict[str, Any]) -> List[str]:
    """PlayerMemoryUpdate를 적용합니다."""
    return player_manager.apply_memory_updates(channel_id, user_id, updates)


def apply_player_updates(channel_id: str, user_id: str, updates: Dict[str, Any]) -> List[str]:
    """PlayerUpdate (인벤토리/골드)를 적용합니다."""
    return player_manager.apply_player_updates(channel_id, user_id, updates)


def get_player_for_prompt(channel_id: str, user_id: str) -> str:
    """AI 프롬프트용 캐릭터 컨텍스트를 반환합니다."""
    return player_manager.get_for_prompt(channel_id, user_id)


# =========================================================
# 기존 NPC 관리자 (그대로 유지)
# =========================================================
class NPCManager:
    """
    NPC 데이터를 domain_manager를 통해 파일에 영구 저장/관리합니다.

    NPC 스키마:
    {
        "desc": "NPC 설명",
        "status": "Active" | "Dead" | "Missing" | "Away",
        "source": "lore" | "session",      # 출처 구분
        "relationship": "관계 설명" | None, # 플레이어와의 관계
        "last_seen": "ISO timestamp" | None # 마지막 등장 시간
    }
    """

    def add_npc(
        self,
        channel_id: str,
        name: str,
        description: str,
        source: str = "session",
        relationship: str = None,
        appearance: str = None,
        personality: str = None,
        sexual_characteristics: str = None,
        abilities: str = None,
        passives: List[str] = None
    ) -> None:
        """
        NPC 추가 또는 업데이트 (기존 데이터 보존)
        """
        if not name:
            logging.warning("NPC 이름이 비어있어 추가하지 않음")
            return

        npcs = domain_manager.get_npcs(channel_id)
        
        # Check for similar existing NPC to prevent duplication
        similar_name = find_similar_key(npcs.keys(), name)
        target_name = similar_name if similar_name else name
        
        existing = npcs.get(target_name, {})

        # 기존 데이터가 있으면 보존, 없으면 새로 설정
        npc_data = {
            "desc": description or existing.get("desc", "설명 없음"),
            "status": existing.get("status", DEFAULT_NPC_STATUS),
            "source": existing.get("source", source),  # 기존 출처 유지
            "relationship": relationship or existing.get("relationship"),
            "last_seen": existing.get("last_seen"),
            
            # Detailed Fields (v5.0)
            "appearance": appearance or existing.get("appearance"),
            "personality": personality or existing.get("personality"),
            "sexual_characteristics": sexual_characteristics or existing.get("sexual_characteristics"),
            "abilities": abilities or existing.get("abilities"),
            "passives": passives or existing.get("passives", [])
        }

        domain_manager.update_npc(channel_id, target_name, npc_data)
        logging.info(f"NPC 추가/업데이트: {target_name} (source: {npc_data['source']})")

    def update_npc_status(
        self,
        channel_id: str,
        name: str,
        status: str
    ) -> bool:
        npcs = domain_manager.get_npcs(channel_id)
        if name not in npcs:
            logging.warning(f"NPC를 찾을 수 없음: {name}")
            return False

        npc_data = npcs[name].copy()
        npc_data["status"] = status
        domain_manager.update_npc(channel_id, name, npc_data)
        logging.info(f"NPC 상태 변경: {name} -> {status}")
        return True

    def update_npc_relationship(
        self,
        channel_id: str,
        name: str,
        relationship: str,
        description: str = None
    ) -> bool:
        """
        NPC 관계 업데이트 (없으면 새로 생성)

        Args:
            channel_id: 채널 ID
            name: NPC 이름
            relationship: 관계 설명
            description: NPC 설명 (새로 생성 시에만 사용)

        Returns:
            성공 여부
        """
        import time

        if not name or not relationship:
            return False

        npcs = domain_manager.get_npcs(channel_id)

        if name in npcs:
            # 기존 NPC 업데이트
            npc_data = npcs[name].copy()
            npc_data["relationship"] = relationship
            npc_data["last_seen"] = time.strftime("%Y-%m-%dT%H:%M:%S")
        else:
            # 새 NPC 생성 (세션 출처)
            npc_data = {
                "desc": description or "세션 중 만난 NPC",
                "status": DEFAULT_NPC_STATUS,
                "source": "session",
                "relationship": relationship,
                "last_seen": time.strftime("%Y-%m-%dT%H:%M:%S")
            }

        domain_manager.update_npc(channel_id, name, npc_data)
        logging.info(f"NPC 관계 업데이트: {name} → {relationship}")
        return True

    def get_npc(
        self,
        channel_id: str,
        name: str
    ) -> Optional[Dict[str, Any]]:
        npcs = domain_manager.get_npcs(channel_id)
        return npcs.get(name)

    def get_npc_summary(self, channel_id: str) -> Optional[str]:
        npcs = domain_manager.get_npcs(channel_id)
        if not npcs:
            return None

        summary_list = []
        for name, data in npcs.items():
            status = data.get('status', DEFAULT_NPC_STATUS)
            desc = data.get('desc', '')
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
        d = domain_manager.get_domain(channel_id)
        if name not in d.get("npcs", {}):
            return False

        del d["npcs"][name]
        domain_manager.save_domain(channel_id, d)
        logging.info(f"NPC 삭제: {name}")
        return True

    def clear(self, channel_id: str) -> None:
        d = domain_manager.get_domain(channel_id)
        d["npcs"] = {}
        domain_manager.save_domain(channel_id, d)
        logging.info(f"채널 {channel_id}의 모든 NPC 초기화됨")

    def clear_npcs_by_source(self, channel_id: str, source: str = None) -> int:
        """
        출처별 NPC 삭제

        Args:
            channel_id: 채널 ID
            source: "lore" | "session" | None (None이면 전체 삭제)

        Returns:
            삭제된 NPC 수
        """
        d = domain_manager.get_domain(channel_id)
        npcs = d.get("npcs", {})

        if source is None:
            # 전체 삭제
            count = len(npcs)
            d["npcs"] = {}
        else:
            # 특정 출처만 삭제
            to_delete = [name for name, data in npcs.items() if data.get("source") == source]
            for name in to_delete:
                del npcs[name]
            count = len(to_delete)

        domain_manager.save_domain(channel_id, d)
        logging.info(f"NPC 삭제: {count}명 (source: {source or 'all'})")
        return count

    def get_relationships(self, channel_id: str) -> Dict[str, str]:
        """
        모든 NPC 관계를 가져옵니다 (!정보 관계용)

        Returns:
            {"NPC이름": "관계 설명", ...}
        """
        npcs = domain_manager.get_npcs(channel_id)
        relationships = {}

        for name, data in npcs.items():
            rel = data.get("relationship")
            if rel:
                relationships[name] = rel

        return relationships


# 싱글톤 인스턴스 생성
npc_memory = NPCManager()


# =========================================================
# 기존 외부 호출용 래퍼 함수 (유지)
# =========================================================
def get_npc_summary(channel_id: str) -> Optional[str]:
    return npc_memory.get_npc_summary(channel_id)


def reset_npc_status(channel_id: str) -> None:
    npc_memory.clear(channel_id)


def add_npc(
    channel_id: str, 
    name: str, 
    description: str, 
    appearance: str = None,
    personality: str = None,
    sexual_characteristics: str = None,
    abilities: str = None,
    passives: List[str] = None
) -> None:
    npc_memory.add_npc(
        channel_id, 
        name, 
        description, 
        appearance=appearance,
        personality=personality,
        sexual_characteristics=sexual_characteristics,
        abilities=abilities,
        passives=passives
    )


def get_npc(channel_id: str, name: str) -> Optional[Dict[str, Any]]:
    return npc_memory.get_npc(channel_id, name)


def update_npc_status(channel_id: str, name: str, status: str) -> bool:
    return npc_memory.update_npc_status(channel_id, name, status)
