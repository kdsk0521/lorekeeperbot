"""
Lorekeeper TRPG Bot - NPC Manager
NPC의 완벽한 관리를 담당하는 비즈니스 로직 모듈.

책임:
- 로어 NPC / 수동 추가 NPC / AI 생성 NPC 구분 관리
- NPC 이름 변경 추적 (정체 발각)
- PC와의 관계(태도) 관리
- NPC 추출 및 등록

domain_manager.py는 저장소 역할만 담당.
"""

import time
import random
import logging
from typing import Dict, Any, Optional, List
import difflib
import domain_manager

# =========================================================
# NPC SOURCE TYPES (출처 구분)
# =========================================================
SOURCE_LORE = "lore"              # 로어 분석으로 추출된 NPC
SOURCE_MANUAL = "manual"          # !npc add 등 수동 추가
SOURCE_AI_GENERATED = "ai_generated"  # 세션 중 AI가 생성한 NPC

VALID_SOURCES = {SOURCE_LORE, SOURCE_MANUAL, SOURCE_AI_GENERATED}

# 태도 레벨 정의
ATTITUDE_LEVELS = {
    "hostile": -2,
    "unfriendly": -1,
    "neutral": 0,
    "friendly": 1,
    "loyal": 2
}

logger = logging.getLogger(__name__)

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



def find_similar_npc(channel_id: str, new_name: str, threshold: float = 0.85) -> Optional[str]:
    """
    유사한 이름을 가진 NPC가 있는지 확인합니다.
    (Exact -> Containment -> Fuzzy)
    """
    existing_npcs = get_npcs(channel_id)
    if not existing_npcs: return None
    
    n_lower = new_name.lower()
    
    # 1. Exact Match (Case-insensitive)
    for name in existing_npcs:
        if name.lower() == n_lower:
            return name
            
    # 2. Containment Check (Only for names >= 3 chars)
    # "Dr. Strange" vs "Strange", "Arthur" vs "King Arthur"
    if len(n_lower) >= 3:
        for name in existing_npcs:
            e_lower = name.lower()
            if n_lower in e_lower or e_lower in n_lower:
                # Check if the overlap is significant?
                # For now, strict containment is usually a sign of identity or relation.
                return name

    # 3. Fuzzy Match
    matches = difflib.get_close_matches(new_name, existing_npcs.keys(), n=1, cutoff=threshold)
    if matches:
        return matches[0]
        
    return None


def add_lore_npcs(channel_id: str, npc_list: List[Dict[str, Any]]) -> int:
    """
    로어 분석 결과로 NPC 일괄 등록.
    [Deduplication Added] 유사한 이름이 있으면 스킵하거나 병합합니다.
    """
    count = 0
    for npc in npc_list:
        name = npc.get("name", "").strip()
        if not name: continue
        
        # [Check Duplicate]
        sim_name = find_similar_npc(channel_id, name)
        if sim_name:
             logger.info(f"[NPC] 로어 NPC '{name}' -> 유사한 기존 NPC '{sim_name}' 발견. 병합/스킵 처리.")
             # Merge logic: Append description if source matches or just update timestamps?
             # For Lore extraction, usually we want to enrich existing if possible, 
             # but often extract might be repetitive.
             # Simple Strategy: Update description only if new one is longer?
             # OR just skip to preserve manually edited data.
             # Existing logic blindly overwrote.
             # NEW Logic: Skip overwrite if manual source, strictly update if lore source.
             
             existing = get_npc(channel_id, sim_name)
             if existing and existing.get("source") == SOURCE_MANUAL:
                 continue # Manual overrides Lore usually
             
             # If both are Lore/AI, we merge descriptions?
             # For now, let's just Log and Skip to prevent duplicates cluttering.
             # Or maybe we DO want to update if the Lore has changed?
             # Let's Skip for now to be safe against "Infinite Extraction Loop".
             continue

        data = {
            "description": npc.get("description", ""),
            "source": SOURCE_LORE,
            "registered_at": time.strftime('%Y-%m-%d %H:%M'),
        }

        # 추가 필드 복사 (role, personality, schedule 등)
        for key in ["role", "personality", "appearance", "location", "gender", "race", "schedule"]:
            if key in npc:
                data[key] = npc[key]

        update_npc(channel_id, name, data)
        count += 1
        logger.debug(f"[NPC] 로어 NPC 등록: {name}")

    return count



def add_manual_npc(channel_id: str, name: str, description: str, gender: str = None, race: str = None, **kwargs) -> bool:
    """
    수동으로 NPC 추가 (!npc add 명령 등).

    Args:
        channel_id: 채널 ID
        name: NPC 이름
        description: NPC 설명
        **kwargs: 추가 필드 (role, personality 등)

    Returns:
        성공 여부
    """
    if not name.strip():
        return False

    data = {
        "description": description,
        "source": SOURCE_MANUAL,
        "registered_at": time.strftime('%Y-%m-%d %H:%M'),
    }
    if gender: data["gender"] = gender
    if race: data["race"] = race
    data.update(kwargs)

    real_name = name.strip()
    
    # [Check Duplicate]
    sim_name = find_similar_npc(channel_id, real_name)
    if sim_name and sim_name.lower() != real_name.lower():
        # Warn user? Currently returns bool. 
        # But we assume the user intends to overwrite if they type exact name.
        # If they type SIMILAR name, they might mean the existing one or a new one.
        # Strict deduplication: prevent similar.
        # But maybe they want "Guard A" and "Guard B".
        # Let's only block if very high similarity or warn?
        # For manual add, we should probably allow it BUT maybe standardise name?
        pass

    update_npc(channel_id, real_name, data)
    logger.info(f"[NPC] 수동 NPC 추가: {name}")
    return True


def register_ai_npc(channel_id: str, name: str, description: str = "", context: str = "", gender: str = None, race: str = None) -> bool:
    """
    세션 중 AI가 생성한 NPC 등록.
    예: 이름 없던 '상인'이 '한스'로 이름이 밝혀졌을 때

    Args:
        channel_id: 채널 ID
        name: NPC 이름
        description: 간단한 설명
        context: 등장 맥락 (어떤 상황에서 등장했는지)

    Returns:
        성공 여부
    """
    if not name.strip():
        return False

    # [Anti-Gravity] Mob Tagging Logic
    # 1. Check for Exact Collision in Session NPCs
    existing = get_npc(channel_id, name)
    
    # If it exists, we have a dilemma: Is it an update or a new mob?
    # If the AI explicitly provides a generic name like "Soldier" that already exists,
    # and the descriptions significantly differ, it's likely a new mob.
    # However, for safety and user request, we will assume 'generic' names might need tagging.
    # But we don't want to break updates to "John".
    
    # Heuristic: If it exists, and the source is technically ours (AI/Session), 
    # AND we want to support multiple mobs... 
    # Actually, the user wants distinction.
    # Let's check if the name already HAS a tag.
    if is_mob_tag(name):
        # Update existing tagged mob
        pass
    elif existing:
        # Collision! It's likely a generic mob collision (or a persistent NPC).
        # We will generate a NEW tagged name for this NEW entry.
        # But wait, what if it's just an update?
        # We can't know for sure. 
        # But usually `register_ai_npc` is called when a *new* entity is detected or explicitly named.
        # Let's try to TAG the NEW one if the name is "simple" or collision happens.
        
        # Exception: Identity Reveal handled elsewhere.
        
        # Logic: Auto-tag the NEW one.
        tag = generate_mob_tag()
        tagged_name = f"{name} {tag}"
        
        # Recursive uniqueness check
        while get_npc(channel_id, tagged_name):
            tag = generate_mob_tag()
            tagged_name = f"{name} {tag}"
            
        logger.info(f"[NPC] Name Collision '{name}' -> Auto-tagged as '{tagged_name}'")
        name = tagged_name
        # Proceed to register as NEW entry (data below)

    data = {
        "description": description,
        "source": SOURCE_AI_GENERATED,
        "registered_at": time.strftime('%Y-%m-%d %H:%M'),
        "appearances": [{"context": context, "at": time.strftime('%Y-%m-%d %H:%M')}] if context else []
    }
    if gender: data["gender"] = gender
    if race: data["race"] = race

    update_npc(channel_id, name.strip(), data)
    logger.info(f"[NPC] AI 생성 NPC 등록: {name}")
    return True


def generate_mob_tag() -> str:
    """
    Generates a random mob tag (e.g., #1A, #B7).
    User Requirement: Random Number + Letter (Random Order).
    Format: #{Char1}{Char2}
    """
    chars = "0123456789"
    alphas = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    
    # Randomly decide order: (Digit, Letter) or (Letter, Digit)
    if random.choice([True, False]):
        c1 = random.choice(chars)
        c2 = random.choice(alphas)
    else:
        c1 = random.choice(alphas)
        c2 = random.choice(chars)
        
    return f"#{c1}{c2}"

def is_mob_tag(name: str) -> bool:
    """Checks if the name ends with a mob tag pattern."""
    return "#" in name and len(name.split("#")[-1]) == 2


# =========================================================
# NPC 조회 (소스별 필터링)
# =========================================================

def get_npcs_by_source(channel_id: str, source: str) -> Dict[str, Dict[str, Any]]:
    """특정 소스의 NPC만 조회"""
    all_npcs = get_npcs(channel_id)
    return {
        name: data for name, data in all_npcs.items()
        if data.get("source", SOURCE_AI_GENERATED) == source
    }


def get_lore_npcs(channel_id: str) -> Dict[str, Dict[str, Any]]:
    """로어 NPC만 조회"""
    return get_npcs_by_source(channel_id, SOURCE_LORE)

def get_lore_npc_names(channel_id: str) -> List[str]:
    """로어 NPC 이름 목록 반환 (Background Extraction용)"""
    return list(get_lore_npcs(channel_id).keys())


def get_session_npcs(channel_id: str) -> Dict[str, Dict[str, Any]]:
    """세션 중 생성된 NPC (manual + ai_generated)"""
    all_npcs = get_npcs(channel_id)
    return {
        name: data for name, data in all_npcs.items()
        if data.get("source", SOURCE_AI_GENERATED) != SOURCE_LORE
    }

def get_scene_npc_names(channel_id: str) -> List[str]:
    """세션(Scene) NPC 이름 목록 반환 (Background Extraction용)"""
    return list(get_session_npcs(channel_id).keys())


def get_npc_roster(channel_id: str) -> str:
    """전체 NPC 이름+역할 1줄 요약 목록 (Theoria용)."""
    npcs = get_npcs(channel_id)
    if not npcs:
        return ""
    lines = []
    for name, data in npcs.items():
        desc = data.get("desc", "")
        # desc 첫 줄에서 50자까지만 추출
        first_line = desc.split("\n")[0][:50] if desc else ""
        lines.append(f"- {name}: {first_line}")
    return "\n".join(lines)


def get_npc_full_profiles(channel_id: str, names: list) -> str:
    """지정된 NPC들의 풀 프로필 반환."""
    npcs = get_npcs(channel_id)
    parts = []
    for name in names:
        data = npcs.get(name)
        if data:
            parts.append(f"### {name}\n{data.get('desc', '')}")
    return "\n\n".join(parts)


def get_npc_names_only(channel_id: str, exclude: list) -> str:
    """지정된 NPC 제외한 나머지의 이름만 반환."""
    npcs = get_npcs(channel_id)
    remaining = [name for name in npcs if name not in exclude]
    if not remaining:
        return ""
    return "기타 NPC: " + ", ".join(remaining)


# =========================================================
# NPC 정체 발각 (이름 변경 추적)
# =========================================================

def handle_identity_reveal(channel_id: str, old_name: str, new_name: str, reason: str = "") -> str:
    """
    NPC 정체 발각 (OldName -> NewName) 처리
    """
    if old_name == new_name: return "⚠️ 이름이 동일합니다."
    
    # [Anti-Gravity] Mob Tag Handling
    # If the user tries to rename "Patient" to "John", but we only have "Patient #1A",
    # we might want to auto-detect. 
    # But safer is to assume Exact Match first.
    
    npc_data = get_npc(channel_id, old_name)
    if not npc_data:
        # Fallback: Check if there's a unique tagged version?
        # (Optional, skipping for safety)
        pass
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


def delete_npc_attitude(channel_id: str, npc_name: str) -> bool:
    """NPC 태도 정보 삭제"""
    d = domain_manager.get_domain(channel_id)
    attitudes = d.get("npc_attitudes", {})
    if npc_name in attitudes:
        del attitudes[npc_name]
        domain_manager.save_domain(channel_id, d)
        return True
    return False


def get_relationship_summary(channel_id: str) -> str:
    """
    전체 NPC-PC 관계 요약 문자열 생성.
    AI 프롬프트에 삽입하기 좋은 형태.
    """
    npcs = get_npcs(channel_id)
    attitudes = get_npc_attitudes(channel_id)

    if not npcs:
        return "[No NPCs registered]"

    lines = []
    for npc_name, npc_data in npcs.items():
        source = npc_data.get("source", "unknown")
        source_tag = {"lore": "📜", "manual": "✏️", "ai_generated": "🤖"}.get(source, "❓")

        att_info = attitudes.get(npc_name, {})
        attitude = att_info.get("attitude", "neutral")
        reason = att_info.get("reason", "")

        # 태도 이모지
        att_emoji = {
            "hostile": "🔴",
            "unfriendly": "🟠",
            "neutral": "⚪",
            "friendly": "🟢",
            "loyal": "💚"
        }.get(attitude, "⚪")

        desc = npc_data.get("description", "")[:50]
        if len(npc_data.get("description", "")) > 50:
            desc += "..."

        # [Gender/Race Display]
        meta_info = []
        if npc_data.get("race"): meta_info.append(npc_data["race"])
        if npc_data.get("gender"): meta_info.append(npc_data["gender"])
        meta_str = f"[{'/'.join(meta_info)}] " if meta_info else ""

        line = f"{source_tag} **{npc_name}** {att_emoji}{attitude} - {meta_str}{desc}"

        lines.append(line)

    return "\n".join(lines) if lines else "[No NPCs]"


def get_attitude_for_prompt(channel_id: str) -> str:
    """
    AI 프롬프트용 태도 요약 (간결한 형태).
    """
    attitudes = get_npc_attitudes(channel_id)
    if not attitudes:
        return ""

    lines = ["[NPC ATTITUDES TOWARD PC]"]
    for npc_name, att_info in attitudes.items():
        attitude = att_info.get("attitude", "neutral")
        reason = att_info.get("reason", "")
        line = f"- {npc_name}: {attitude}"
        if reason:
            line += f" ({reason})"
        lines.append(line)

    return "\n".join(lines)


# =========================================================
# NPC SIMULATION
# =========================================================

def get_npc_time_progression(channel_id: str) -> List[str]:
    """
    시간 경과에 따른 NPC 상태 변화 힌트 생성 (3-Tier Hybrid)

    Priority:
      P1: ai_session_memory.npc_summaries — AI가 세션 중 관찰/추론한 활동
      P2: NPC data의 schedule[time_slot] — 로어북/수동 등록 시 프리셋 루틴
      P3: 시간대별 일반 활동 랜덤 폴백
    """
    npcs = get_npcs(channel_id)
    if not npcs:
        return []

    world = domain_manager.get_world_state(channel_id)
    time_slot = world.get("time_slot", "오후")

    # P1: AI-observed activity from session memory
    ai_mem = domain_manager.get_session_ai_memory(channel_id)
    npc_summaries = ai_mem.get("npc_summaries", {})

    # P3 fallback: 시간대별 일반 활동
    _generic_activities = {
        "새벽": ["잠들어 있다", "이른 기상 준비", "야간 근무 마무리", "깊은 잠에 빠져 있다"],
        "오전": ["아침 식사", "일과 시작", "청소/정리", "분주하게 움직임"],
        "오후": ["업무 중", "점심 후 활동", "외출", "나른하게 휴식"],
        "황혼": ["퇴근 준비", "저녁 준비", "휴식", "하루를 정리함"],
        "저녁": ["저녁 식사", "여가 활동", "TV 시청", "술자리"],
        "심야": ["잠자리 준비", "야식", "늦은 작업", "비밀스러운 만남"]
    }
    fallback_pool = _generic_activities.get(time_slot, ["활동 중"])

    hints = []
    for npc_name, npc_data in npcs.items():
        # P1: session memory에 AI가 기록한 최근 활동/상태
        summary = npc_summaries.get(npc_name, {})
        if isinstance(summary, str) and summary:
            hints.append(f"{npc_name}: {summary}")
            continue
        if isinstance(summary, dict) and summary.get("activity"):
            hints.append(f"{npc_name}: {summary['activity']}")
            continue

        # P2: 프리셋 스케줄 (lore/manual NPC에 schedule 필드가 있을 때)
        schedule = npc_data.get("schedule", {})
        if isinstance(schedule, dict) and time_slot in schedule:
            hints.append(f"{npc_name}: {schedule[time_slot]}")
            continue

        # P3: 일반 랜덤 폴백
        hints.append(f"{npc_name}: {random.choice(fallback_pool)}")

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


