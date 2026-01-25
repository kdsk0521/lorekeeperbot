"""
Lorekeeper TRPG Bot - Domain Content Module
Handles lore, NPCs, rules, genres, and world settings.
Merges previous domain_lore and domain_rules modules.
Includes Simulation Data (Status Effects, Normality Stages) [NEW]
"""

import os
import logging
import config
from typing import Dict, Any, Optional, List, Tuple
from domain_io import (
    get_lore_file_path, get_lore_original_file_path, get_rules_file_path,
    load_text, save_text, _lore_cache, _lore_original_cache, _rules_cache,
    get_domain, save_domain
)

# =========================================================
# 로어 관리
# =========================================================
def get_lore(channel_id: str) -> str:
    if channel_id in _lore_cache:
        return _lore_cache[channel_id]
    text = load_text(get_lore_file_path(channel_id), config.DEFAULT_LORE)
    _lore_cache[channel_id] = text
    return text

def append_lore(channel_id: str, text: str) -> None:
    current = get_lore(channel_id)
    new_text = text if current.strip() == config.DEFAULT_LORE.strip() else f"{current}\n\n{text}"
    _lore_cache[channel_id] = new_text
    save_text(get_lore_file_path(channel_id), new_text)

def reset_lore(channel_id: str) -> None:
    lore_path = get_lore_file_path(channel_id)
    original_path = get_lore_original_file_path(channel_id)

    if channel_id in _lore_cache:
        del _lore_cache[channel_id]
    if channel_id in _lore_original_cache:
        del _lore_original_cache[channel_id]

    if os.path.exists(lore_path):
        try:
            os.remove(lore_path)
        except Exception as e:
            logging.error(f"파일 삭제 실패 {lore_path}: {e}")
    if os.path.exists(original_path):
        try:
            os.remove(original_path)
        except Exception as e:
            logging.error(f"파일 삭제 실패 {original_path}: {e}")

def save_lore_original(channel_id: str, original_text: str) -> None:
    _lore_original_cache[channel_id] = original_text
    save_text(get_lore_original_file_path(channel_id), original_text)

def get_lore_original(channel_id: str) -> Optional[str]:
    if channel_id in _lore_original_cache:
        return _lore_original_cache[channel_id]
    path = get_lore_original_file_path(channel_id)
    if os.path.exists(path):
        text = load_text(path, "")
        _lore_original_cache[channel_id] = text
        return text
    return None

def get_lore_with_npcs(channel_id: str) -> str:
    lore = get_lore(channel_id)
    d = get_domain(channel_id)
    npcs = d.get("npcs", {})
    
    if not npcs:
        return lore
    
    npc_section = "\n\n### 📋 NPC 정보 (캐릭터들)\n\n"
    for name, data in npcs.items():
        desc = data.get("desc", "설명 없음")
        status = data.get("status", "Active")
        status_emoji = "✅" if status == "Active" else "💀" if status == "Dead" else "❓"
        npc_section += f"**{name}** ({status_emoji} {status})\n{desc}\n\n"
    
    return lore + npc_section

# =========================================================
# NPC 관리
# =========================================================
def get_npcs(channel_id: str) -> Dict[str, Dict[str, Any]]:
    """NPC 목록을 가져옵니다."""
    return get_domain(channel_id).get("npcs", {})

def update_npc(channel_id: str, name: str, data: Dict[str, Any]) -> None:
    """NPC 정보를 업데이트합니다."""
    d = get_domain(channel_id)
    if "npcs" not in d:
        d["npcs"] = {}
    d["npcs"][name] = data
    save_domain(channel_id, d)

def delete_npc(channel_id: str, name: str) -> bool:
    """NPC를 삭제합니다."""
    d = get_domain(channel_id)
    if name in d.get("npcs", {}):
        del d["npcs"][name]
        save_domain(channel_id, d)
        return True
    return False

def rename_npc(channel_id: str, old_name: str, new_name: str) -> bool:
    """
    NPC의 이름을 변경합니다 (Key Rename).
    기존 데이터는 유지되며, 새로운 이름으로 이동합니다.
    """
    if not old_name or not new_name or old_name == new_name:
        return False
        
    d = get_domain(channel_id)
    npcs = d.get("npcs", {})
    
    # 1. Check if old exists
    if old_name not in npcs:
        return False
        
    data = npcs.pop(old_name)
    
    if new_name in npcs:
        # Merge old history into new
        existing_new = npcs[new_name]
        existing_new.update(data) 
        npcs[new_name] = existing_new
    else:
        npcs[new_name] = data
        
    d["npcs"] = npcs
    save_domain(channel_id, d)
    return True

# =========================================================
# 룰 관리
# =========================================================
def get_rules(channel_id: str) -> str:
    if channel_id in _rules_cache:
        return _rules_cache[channel_id]
    text = load_text(get_rules_file_path(channel_id), config.DEFAULT_RULES)
    _rules_cache[channel_id] = text
    return text

def get_rules_mode(channel_id: str) -> str:
    return get_domain(channel_id).get("rules_mode", "default")

def set_rules_mode(channel_id: str, mode: str) -> None:
    d = get_domain(channel_id)
    d["rules_mode"] = mode
    save_domain(channel_id, d)

def append_rules(channel_id: str, text: str) -> None:
    current_mode = get_rules_mode(channel_id)
    
    if current_mode == "custom":
        current = get_rules(channel_id)
        new_text = f"{current}\n\n{text}"
    else:
        d = get_domain(channel_id)
        custom_rules = d.get("custom_rules", "")
        if custom_rules:
            custom_rules = f"{custom_rules}\n\n{text}"
        else:
            custom_rules = text
        d["custom_rules"] = custom_rules
        save_domain(channel_id, d)
        set_rules_mode(channel_id, "hybrid")
        new_text = f"{config.DEFAULT_RULES}\n\n[커스텀 추가 규칙]\n{custom_rules}"
    
    _rules_cache[channel_id] = new_text
    save_text(get_rules_file_path(channel_id), new_text)

def set_custom_rules_from_file(channel_id: str, file_content: str) -> None:
    _rules_cache[channel_id] = file_content
    save_text(get_rules_file_path(channel_id), file_content)
    set_rules_mode(channel_id, "custom")
    
    d = get_domain(channel_id)
    d["custom_rules"] = ""
    if "settings" not in d:
        d["settings"] = {}
    d["settings"]["growth_system"] = "custom"
    save_domain(channel_id, d)

def reset_rules(channel_id: str) -> None:
    path = get_rules_file_path(channel_id)
    if os.path.exists(path):
        try:
            os.remove(path)
        except Exception as e:
            logging.error(f"파일 삭제 실패 {path}: {e}")
            
    if channel_id in _rules_cache:
        del _rules_cache[channel_id]
    
    set_rules_mode(channel_id, "default")
    
    d = get_domain(channel_id)
    d["custom_rules"] = ""
    if "settings" not in d:
        d["settings"] = {}
    d["settings"]["growth_system"] = "default"
    save_domain(channel_id, d)

def get_custom_rules_part(channel_id: str) -> str:
    return get_domain(channel_id).get("custom_rules", "")

# =========================================================
# 장르 및 톤 관리
# =========================================================
def get_active_genres(channel_id: str) -> List[str]:
    return get_domain(channel_id).get("active_genres", ["noir"])

def set_active_genres(channel_id: str, genres: List[str]) -> None:
    d = get_domain(channel_id)
    d["active_genres"] = genres
    save_domain(channel_id, d)

def get_custom_tone(channel_id: str) -> Optional[str]:
    return get_domain(channel_id).get("custom_tone")

def set_custom_tone(channel_id: str, tone: Optional[str]) -> None:
    d = get_domain(channel_id)
    d["custom_tone"] = tone
    save_domain(channel_id, d)

# =========================================================
# 장면 유형 관리
# =========================================================
def get_scene_type(channel_id: str) -> str:
    return get_domain(channel_id).get("scene_type", "normal")

def set_scene_type(channel_id: str, scene_type: str) -> None:
    valid_types = ['normal', 'gore', 'nsfw', 'gore_nsfw']
    if scene_type not in valid_types:
        scene_type = 'normal'
    d = get_domain(channel_id)
    d['scene_type'] = scene_type
    save_domain(channel_id, d)

# =========================================================
# 성장 시스템 (규칙 연동)
# =========================================================
def get_growth_system(channel_id: str) -> str:
    return get_domain(channel_id).get("settings", {}).get("growth_system", "default")


# =========================================================
# 상태이상 및 적응도 데이터 (Simulation Data) [NEW]
# =========================================================

# 부정적 상태이상 (doom 증가 요인)
NEGATIVE_STATUS_EFFECTS = {
    # 신체적 부상 (심각도별)
    "중상": 3,
    "부상": 2,
    "가벼운 부상": 1,
    "출혈": 2,
    "골절": 3,
    "화상": 2,
    "동상": 2,
    "중독": 2,
    "질병": 2,
    "감염": 2,
    
    # 정신적/심리적
    "공포": 2,
    "패닉": 3,
    "혼란": 1,
    "광기": 3,
    "절망": 2,
    "트라우마": 2,
    "악몽": 1,
    
    # 신체 상태
    "피로": 1,
    "탈진": 2,
    "지침": 1,
    "굶주림": 2,
    "갈증": 2,
    "수면 부족": 1,
    "기절": 2,
    "마비": 2,
    "실명": 3,
    "청각 상실": 2,
    
    # 저주/마법적 (판타지용)
    "저주": 2,
    "마력 고갈": 1,
    "영혼 손상": 3,
    "빙의": 3,
    
    # 사회적
    "수배": 2,
    "추적당함": 2,
    "배신당함": 1,
}

# 긍정적 상태 (doom 감소 요인)
POSITIVE_STATUS_EFFECTS = {
    # 신체적 버프
    "치료됨": 1,
    "회복 중": 1,
    "강화": 1,
    "축복": 2,
    "보호막": 1,
    "재생": 2,
    
    # 정신적/심리적
    "집중": 1,
    "평온": 1,
    "용기": 1,
    "결의": 1,
    "영감": 1,
    "희망": 2,
    
    # 신체 상태
    "휴식함": 1,
    "포만감": 1,
    "숙면": 1,
    "활력": 1,
    
    # 마법적 (판타지용)
    "마력 충전": 1,
    "신의 가호": 2,
    "투명화": 1,
    
    # 사회적
    "은신 중": 1,
    "보호받음": 2,
    "동맹": 1,
}

# 상태이상 정의
STATUS_EFFECTS = {
    # === 부정적 상태 (Debuff) ===
    # 물리적 상태
    "부상": {"type": "debuff", "category": "physical", "severity": 1, "recoverable": True, "description": "가벼운 부상"},
    "중상": {"type": "debuff", "category": "physical", "severity": 2, "recoverable": False, "description": "심각한 부상, 치료 필요"},
    "출혈": {"type": "debuff", "category": "physical", "severity": 2, "tick_damage": 1, "description": "매 턴 체력 감소"},
    "골절": {"type": "debuff", "category": "physical", "severity": 3, "recoverable": False, "description": "이동/전투 불가"},
    "피로": {"type": "debuff", "category": "physical", "severity": 1, "recoverable": True, "description": "행동력 저하"},
    "지침": {"type": "debuff", "category": "physical", "severity": 1, "recoverable": True, "description": "집중력 저하"},
    "기절": {"type": "debuff", "category": "physical", "severity": 2, "duration": 1, "description": "행동 불가"},
    
    # 정신적 상태
    "공포": {"type": "debuff", "category": "mental", "severity": 2, "description": "특정 대상/상황 회피"},
    "공황": {"type": "debuff", "category": "mental", "severity": 3, "description": "판단력 상실"},
    "혼란": {"type": "debuff", "category": "mental", "severity": 2, "duration": 2, "description": "행동 예측 불가"},
    "분노": {"type": "debuff", "category": "mental", "severity": 1, "description": "이성적 판단 저하"},
    "절망": {"type": "debuff", "category": "mental", "severity": 2, "description": "의지력 저하"},
    "트라우마": {"type": "debuff", "category": "mental", "severity": 3, "recoverable": False, "description": "영구적 정신적 상처"},
    
    # 환경적 상태
    "중독": {"type": "debuff", "category": "environmental", "severity": 2, "tick_damage": 2, "description": "매 턴 피해"},
    "화상": {"type": "debuff", "category": "environmental", "severity": 2, "tick_damage": 1, "description": "화상 피해"},
    "동상": {"type": "debuff", "category": "environmental", "severity": 2, "description": "행동 둔화"},
    "질식": {"type": "debuff", "category": "environmental", "severity": 3, "tick_damage": 3, "description": "긴급 상황"},
    "실명": {"type": "debuff", "category": "environmental", "severity": 2, "description": "시야 상실"},
    "청각상실": {"type": "debuff", "category": "environmental", "severity": 1, "description": "소리 인식 불가"},
    
    # 사회적 상태
    "수배": {"type": "debuff", "category": "social", "severity": 2, "description": "당국에 추적당함"},
    "오명": {"type": "debuff", "category": "social", "severity": 1, "description": "평판 하락"},
    "빚": {"type": "debuff", "category": "social", "severity": 1, "description": "경제적 압박"},
    
    # === 긍정적 상태 (Buff) ===
    "집중": {"type": "buff", "category": "mental", "severity": 1, "description": "판정 보너스"},
    "영감": {"type": "buff", "category": "mental", "severity": 2, "duration": 3, "description": "창의적 행동 보너스"},
    "보호": {"type": "buff", "category": "physical", "severity": 2, "description": "피해 감소"},
    "은신": {"type": "buff", "category": "physical", "severity": 1, "description": "발견되기 어려움"},
    "가속": {"type": "buff", "category": "physical", "severity": 1, "duration": 2, "description": "행동 속도 증가"},
    "행운": {"type": "buff", "category": "special", "severity": 2, "duration": 1, "description": "다음 판정 유리"},
}

# 심각도별 Doom 영향
SEVERITY_DOOM_IMPACT = {
    1: 0,   # 경미: Doom 영향 없음
    2: 1,   # 중간: Doom +1
    3: 2,   # 심각: Doom +2
}

# 적응 단계 정의
NORMALITY_STAGES = {
    (0, 20): {
        "stage": "shock",
        "name": "충격",
        "reaction_hint": "경악, 공포, 믿을 수 없다는 반응",
        "tone": "dramatic"
    },
    (20, 40): {
        "stage": "confusion",
        "name": "당황",
        "reaction_hint": "혼란, '이게 뭐지?', 어찌할 바를 모름",
        "tone": "uncertain"
    },
    (40, 60): {
        "stage": "acceptance",
        "name": "체념",
        "reaction_hint": "'...또야?', 한숨, 피로감",
        "tone": "resigned"
    },
    (60, 80): {
        "stage": "adaptation",
        "name": "적응",
        "reaction_hint": "담담함, '알았어', 별 감흥 없음",
        "tone": "calm"
    },
    (80, 101): {
        "stage": "normalized",
        "name": "일상화",
        "reaction_hint": "아무 반응 없음, 자연스럽게 처리",
        "tone": "mundane"
    }
}

# =========================================================
# 유틸리티 함수 (Simulation Data Logic)
# =========================================================
def get_normality_stage_info(normality: int) -> Dict[str, str]:
    """적응도에 따른 단계 정보를 반환합니다."""
    for (low, high), stage_info in NORMALITY_STAGES.items():
        if low <= normality < high:
            return stage_info
    return NORMALITY_STAGES[(80, 101)]  # 기본값: 일상화

def get_status_effect_info(effect_name: str) -> Optional[Dict[str, Any]]:
    """상태이상 정보를 반환합니다."""
    return STATUS_EFFECTS.get(effect_name)
