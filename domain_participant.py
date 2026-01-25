"""
Lorekeeper TRPG Bot - Domain Participant Module
Handles participant data and PC information management.
"""

from typing import Dict, Any, Optional
import logging
from domain_io import get_domain, save_domain

# =========================================================
# 참가자 관리
# =========================================================
def _create_default_participant(display_name: str) -> Dict[str, Any]:
    return {
        "mask": display_name,
        "status": "active",
        "economy": {
            "gold": 0
        },
        "inventory": {},
        "status_effects": [],
        "ai_memory": {
            "appearance": "",
            "personality": "",
            "background": "",
            "relationships": {},
            "passives": [],
            "normalization": {},
            "notes": "",
            "archived_info": [],
            "archived_foreshadowing": []
        },
        # Legacy
        "description": "",
        "relations": {},
        "summary_data": {},
        "abnormal_exposure": {},
        "passives": [],
        "experience_counters": {}
    }

def update_participant(channel_id: str, user, reset: bool = False) -> bool:
    d = get_domain(channel_id)
    uid = str(user.id)
    
    if reset or uid not in d["participants"]:
        d["participants"][uid] = _create_default_participant(user.display_name)
    else:
        d["participants"][uid]["status"] = "active"
        if "ai_memory" not in d["participants"][uid]:
            d["participants"][uid]["ai_memory"] = {
                "appearance": d["participants"][uid].get("description", ""),
                "personality": "",
                "background": "",
                "relationships": {},
                "passives": [p.get("name", "") for p in d["participants"][uid].get("passives", [])],
                "normalization": {},
                "notes": "",
                "archived_info": [],
                "archived_foreshadowing": []
            }
        
        if "economy" not in d["participants"][uid]:
            old_core = d["participants"][uid].get("core_stats", {})
            old_gold = old_core.get("gold", 0) if old_core else 0
            d["participants"][uid]["economy"] = {
                "gold": old_gold
            }
    
    save_domain(channel_id, d)
    return True

def get_participant_data(channel_id: str, user_id: str) -> Optional[Dict[str, Any]]:
    d = get_domain(channel_id)
    if not d or "participants" not in d:
        logging.warning(f"도메인 데이터가 올바르지 않습니다: {channel_id}")
        return None
    return d["participants"].get(str(user_id))

def save_participant_data(channel_id: str, user_id: str, data: Dict[str, Any]) -> None:
    d = get_domain(channel_id)
    if not d or "participants" not in d:
        logging.error(f"도메인 데이터가 올바르지 않습니다: {channel_id}")
        return
    d["participants"][str(user_id)] = data
    save_domain(channel_id, d)

def save_participant_summary(channel_id: str, user_id: str, summary_data: Dict[str, Any]) -> None:
    d = get_domain(channel_id)
    uid = str(user_id)
    if uid in d["participants"]:
        d["participants"][uid]["summary_data"] = summary_data
        save_domain(channel_id, d)

def get_participant_status(channel_id: str, uid: str) -> str:
    d = get_domain(channel_id)
    return d["participants"].get(str(uid), {}).get("status", "active")

def set_participant_status(channel_id: str, uid: str, status: str, reason: str = "") -> None:
    d = get_domain(channel_id)
    uid = str(uid)
    if uid in d["participants"]:
        d["participants"][uid]["status"] = status
    save_domain(channel_id, d)

# =========================================================
# 경제 시스템
# =========================================================
def get_economy(channel_id: str, user_id: str) -> Dict[str, Any]:
    """플레이어의 경제 정보(골드)를 가져옵니다."""
    p_data = get_participant_data(channel_id, user_id)
    if not p_data:
        return {}
    return p_data.get("economy", {})

def update_economy(channel_id: str, user_id: str, updates: Dict[str, Any]) -> None:
    """플레이어의 경제 정보를 업데이트합니다."""
    d = get_domain(channel_id)
    uid = str(user_id)
    
    if uid not in d["participants"]:
        return
    
    if "economy" not in d["participants"][uid]:
        d["participants"][uid]["economy"] = {}
    
    d["participants"][uid]["economy"].update(updates)
    save_domain(channel_id, d)


# =========================================================
# PC(플레이어 캐릭터) 정보 관리
# =========================================================
def set_default_pc_info(channel_id: str, pc_info: Dict[str, Any]) -> None:
    d = get_domain(channel_id)
    d["default_pc_info"] = pc_info
    save_domain(channel_id, d)

def get_default_pc_info(channel_id: str) -> Optional[Dict[str, Any]]:
    return get_domain(channel_id).get("default_pc_info")

def clear_default_pc_info(channel_id: str) -> None:
    d = get_domain(channel_id)
    d.pop("default_pc_info", None)
    save_domain(channel_id, d)

def apply_pc_info_to_user(channel_id: str, user_id: str) -> bool:
    pc_info = get_default_pc_info(channel_id)
    if not pc_info:
        return False

    updates = {}
    base_desc = []
    if pc_info.get('species'):
        base_desc.append(f"종족: {pc_info['species']}")
    if pc_info.get('role'):
        base_desc.append(f"역할: {pc_info['role']}")
    
    appearance_text = pc_info.get('appearance', '')
    if base_desc:
        appearance_text = f"{' / '.join(base_desc)}\n{appearance_text}".strip()
    
    if appearance_text:
        updates['appearance'] = appearance_text

    if pc_info.get('personality'):
        updates['personality'] = pc_info['personality']
    if pc_info.get('background'):
        updates['background'] = pc_info['background']

    if pc_info.get('sexual_characteristics'):
        updates['sexual_characteristics'] = pc_info['sexual_characteristics']
    if pc_info.get('abilities'):
        updates['abilities'] = pc_info['abilities']

    if pc_info.get('relationships'):
        updates['relationships'] = pc_info['relationships']
    
    known_info_list = pc_info.get('known_info', [])
    if pc_info.get('secret_info'):
        known_info_list.append(f"[비밀] {pc_info['secret_info']}")
    
    if known_info_list:
        updates['known_info'] = known_info_list

    if pc_info.get('passives'):
        updates['passives'] = pc_info['passives']

    if updates:
        update_ai_memory(channel_id, user_id, updates)

        if pc_info.get('inventory'):
            p_data = get_participant_data(channel_id, user_id)
            if p_data:
                current_inv = p_data.get('inventory', {})
                for item, qty in pc_info['inventory'].items():
                    current_inv[str(item)] = qty
                p_data['inventory'] = current_inv
                save_participant_data(channel_id, user_id, p_data)

        return True

    return False


# =========================================================
# AI 메모리 관리 시스템 (Participant)
# =========================================================

def get_ai_memory(channel_id: str, user_id: str) -> Dict[str, Any]:
    """플레이어의 AI 메모리를 가져옵니다."""
    p_data = get_participant_data(channel_id, user_id)
    if not p_data:
        return {}
    return p_data.get("ai_memory", {})

def update_ai_memory(channel_id: str, user_id: str, updates: Dict[str, Any]) -> None:
    """플레이어의 AI 메모리를 업데이트합니다."""
    d = get_domain(channel_id)
    uid = str(user_id)
    
    if uid not in d["participants"]:
        return
    
    if "ai_memory" not in d["participants"][uid]:
        d["participants"][uid]["ai_memory"] = {}
    
    ai_mem = d["participants"][uid]["ai_memory"]
    
    for key, value in updates.items():
        if key in ai_mem:
            if isinstance(ai_mem[key], list) and isinstance(value, list):
                existing = set(ai_mem[key])
                for item in value:
                    if item not in existing:
                        ai_mem[key].append(item)
            elif isinstance(ai_mem[key], dict) and isinstance(value, dict):
                ai_mem[key].update(value)
            else:
                ai_mem[key] = value
        else:
            ai_mem[key] = value
    
    d["participants"][uid]["ai_memory"] = ai_mem
    save_domain(channel_id, d)

def set_ai_memory_field(channel_id: str, user_id: str, field: str, value: Any) -> None:
    """AI 메모리의 특정 필드를 설정합니다."""
    d = get_domain(channel_id)
    uid = str(user_id)
    
    if uid not in d["participants"]:
        return
    
    if "ai_memory" not in d["participants"][uid]:
        d["participants"][uid]["ai_memory"] = {}
    
    d["participants"][uid]["ai_memory"][field] = value
    save_domain(channel_id, d)

def add_to_ai_memory_list(channel_id: str, user_id: str, field: str, item: str) -> bool:
    """AI 메모리의 리스트 필드에 항목을 추가합니다."""
    d = get_domain(channel_id)
    uid = str(user_id)
    
    if uid not in d["participants"]:
        return False
        
    if "ai_memory" not in d["participants"][uid]:
        d["participants"][uid]["ai_memory"] = {}
        
    ai_mem = d["participants"][uid]["ai_memory"]
    
    if field not in ai_mem:
        ai_mem[field] = []
        
    if not isinstance(ai_mem[field], list):
        return False
        
    if item not in ai_mem[field]:
        ai_mem[field].append(item)
        d["participants"][uid]["ai_memory"] = ai_mem
        save_domain(channel_id, d)
        return True
        
    return False

def remove_from_ai_memory_list(channel_id: str, user_id: str, field: str, item: str) -> bool:
    """AI 메모리의 리스트 필드에서 항목을 제거합니다."""
    d = get_domain(channel_id)
    uid = str(user_id)
    
    if uid not in d["participants"]:
        return False
    
    ai_mem = d["participants"][uid].get("ai_memory", {})
    target_list = ai_mem.get(field, [])
    
    if isinstance(target_list, list) and item in target_list:
        target_list.remove(item)
        save_domain(channel_id, d)
        return True
    
    return False

def get_full_ai_context(channel_id: str, user_id: str) -> str:
    """
    AI에게 전달할 플레이어의 전체 컨텍스트를 생성합니다.
    (Appearance, Personality, Background, Relationships, Passives, etc.)
    """
    mem = get_ai_memory(channel_id, user_id)
    if not mem:
        return ""
    
    parts = []
    
    app = mem.get("appearance", "")
    per = mem.get("personality", "")
    if app: parts.append(f"**[Appearance]** {app}")
    if per: parts.append(f"**[Personality]** {per}")
    
    bg = mem.get("background", "")
    if bg: parts.append(f"**[Background]** {bg}")
    
    rels = mem.get("relationships", {})
    if rels:
        r_list = [f"- {name}: {rel}" for name, rel in rels.items()]
        parts.append("**[Relationships]**\n" + "\n".join(r_list))
    
    passives = mem.get("passives", [])
    if passives:
        parts.append(f"**[Passives/Titles]** {', '.join(passives)}")
    
    norms = mem.get("normalization", {})
    if norms:
        n_list = [f"- {k}: {v}" for k, v in norms.items()]
        parts.append("**[Normalization]**\n" + "\n".join(n_list))
    
    return "\n\n".join(parts)

def get_ai_memory_for_prompt(channel_id: str, user_id: str) -> str:
    """
    AI에게 전달할 메모리 컨텍스트를 생성합니다. (Simplified)
    """
    ai_mem = get_ai_memory(channel_id, user_id)
    if not ai_mem:
        return ""
    
    parts = []
    
    if ai_mem.get("relationships"):
        rel_str = ", ".join([f"{k}({v})" for k, v in ai_mem["relationships"].items()])
        parts.append(f"관계: {rel_str}")
    
    if ai_mem.get("passives"):
        parts.append(f"패시브: {', '.join(ai_mem['passives'])}")
    
    if ai_mem.get("known_info"):
        parts.append(f"알고 있는 것: {', '.join(ai_mem['known_info'][:3])}")
    
    if ai_mem.get("normalization"):
        norm_str = ", ".join([f"{k}={v}" for k, v in ai_mem["normalization"].items()])
        parts.append(f"적응도: {norm_str}")
    
    if not parts:
        return ""
    
    return "### [PLAYER AI MEMORY]\n" + "\n".join(parts) + "\n"

# =========================================================
# 유저 정보 관리
# =========================================================
def get_user_mask(channel_id: str, uid: str) -> str:
    d = get_domain(channel_id)
    return d["participants"].get(str(uid), {}).get("mask", "Unknown")

def set_user_mask(channel_id: str, uid: str, mask: str) -> None:
    d = get_domain(channel_id)
    uid = str(uid)
    if uid in d["participants"]:
        d["participants"][uid]["mask"] = mask
        save_domain(channel_id, d)

def set_user_description(channel_id: str, uid: str, desc: str) -> None:
    d = get_domain(channel_id)
    uid = str(uid)
    if uid in d["participants"]:
        d["participants"][uid]["description"] = desc
        save_domain(channel_id, d)

def get_user_description(channel_id: str, uid: str) -> str:
    d = get_domain(channel_id)
    return d["participants"].get(str(uid), {}).get("description", "")

# =========================================================
# 통합 정보 표시 (UI)
# =========================================================
def get_unified_player_info(channel_id: str, user_id: str) -> str:
    p_data = get_participant_data(channel_id, user_id)
    if not p_data:
        return "❌ 캐릭터 정보가 없습니다."
    
    mask = p_data.get("mask", "Unknown")
    economy = p_data.get("economy", {})
    inventory = p_data.get("inventory", {})
    effects = p_data.get("status_effects", [])
    ai_mem = p_data.get("ai_memory", {})
    
    result = f"## 🎭 **{mask}**\n\n"
    
    gold = economy.get('gold', 0)
    result += f"**💰 골드:** {gold}\n"
    
    if inventory:
        inv_str = ", ".join([f"{k} x{v}" for k, v in inventory.items()])
        result += f"**🎒 인벤토리:** {inv_str}\n"
    else:
        result += "**🎒 인벤토리:** (비어있음)\n"
    
    if effects:
        result += f"**⚠️ 상태:** {', '.join(effects)}\n"
    
    result += "\n---\n**📝 캐릭터 서사**\n\n"
    
    appearance = ai_mem.get("appearance", "")
    if appearance:
        result += f"**👤 외형:** {appearance}\n"
    
    personality = ai_mem.get("personality", "")
    if personality:
        result += f"**💭 성격:** {personality}\n"
    
    background = ai_mem.get("background", "")
    if background:
        result += f"**📖 배경:** {background}\n"
    
    relationships = ai_mem.get("relationships", {})
    if relationships:
        result += "**🤝 관계:**\n"
        for name, desc in relationships.items():
            result += f"  • {name}: {desc}\n"
    
    passives = ai_mem.get("passives", [])
    if passives:
        result += f"**🏆 패시브/칭호:** {', '.join(passives)}\n"
    
    known_info = ai_mem.get("known_info", [])
    if known_info:
        result += "**💡 알고 있는 것:**\n"
        for info in known_info[:5]:
            result += f"  • {info}\n"
    
    foreshadowing = ai_mem.get("foreshadowing", [])
    if foreshadowing:
        result += "**🔮 미해결 복선:**\n"
        for fs in foreshadowing[:3]:
            result += f"  • {fs}\n"
    
    normalization = ai_mem.get("normalization", {})
    if normalization:
        result += "**🌓 비일상 적응:**\n"
        for thing, status in normalization.items():
            result += f"  • {thing}: {status}\n"
    
    notes = ai_mem.get("notes", "")
    if notes:
        result += f"**📋 메모:** {notes}\n"
    
    return result

def get_integrated_status(channel_id: str, user_id: str) -> str:
    """Alias/Alternative format for !info"""
    return get_unified_player_info(channel_id, user_id)


# =========================================================
# 파티 상태 컨텍스트
# =========================================================
def get_party_status_context(channel_id: str) -> str:
    d = get_domain(channel_id)
    participants = d.get("participants", {})
    
    if not participants:
        return "Active Players: None"
    
    active_players = []
    inactive_players = []
    
    for uid, p_data in participants.items():
        mask = p_data.get("mask", "Unknown")
        status = p_data.get("status", "active")
        
        if status != "active":
            inactive_players.append(f"{mask} ({status})")
            continue
        
        ai_mem = p_data.get("ai_memory", {})
        appearance = ai_mem.get("appearance", "")
        passives = ai_mem.get("passives", [])
        relationships = ai_mem.get("relationships", {})
        status_effects = p_data.get("status_effects", [])
        
        effects_str = ", ".join(status_effects[:3]) if status_effects else "정상"
        look = appearance[:50] + "..." if len(appearance) > 50 else appearance if appearance else "미설정"
        
        passives_str = ", ".join(passives[:3]) if passives else "없음"
        if len(passives) > 3:
            passives_str += f" 외 {len(passives)-3}개"
        
        rel_list = [f"{k}: {v}" for k, v in list(relationships.items())[:2]]
        rel_str = " | ".join(rel_list) if rel_list else "없음"
        
        player_info = (
            f"**[{mask}]**\n"
            f"  Look: {look}\n"
            f"  Passives: {passives_str}\n"
            f"  Relations: {rel_str}\n"
            f"  Conditions: {effects_str}"
        )
        active_players.append(player_info)
    
    result = f"### ACTIVE PLAYERS ({len(active_players)}명)\n"
    result += "**Important:** Each [Name] is a separate player. Track actions individually.\n\n"
    
    if active_players:
        result += "\n\n".join(active_players)
    else:
        result += "(없음)"
    
    if inactive_players:
        result += f"\n\n### INACTIVE: {', '.join(inactive_players)}"
    
    return result
