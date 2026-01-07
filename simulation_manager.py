"""
Lorekeeper TRPG Bot - Simulation Manager Module
경험치, 성장, 훈련, 인벤토리, 상태이상 관리를 담당합니다.
"""

import random
from typing import Dict, Any, Tuple, List, Union

# =========================================================
# 상수 정의
# =========================================================
# D&D 스타일 레벨업 XP 테이블
DND_XP_TABLE = {
    1: 300,
    2: 900,
    3: 2700,
    4: 6500,
    5: 14000,
    6: 23000,
    7: 34000,
    8: 48000,
    9: 64000,
    10: 85000,
    11: 100000,
    12: 120000,
    13: 140000,
    14: 165000,
    15: 195000,
    16: 225000,
    17: 265000,
    18: 305000,
    19: 355000,
    20: 999999  # 만렙
}

# 헌터 랭크 테이블
HUNTER_RANK_TABLE = [
    (5, "F급 (일반인)"),
    (10, "E급 (하급 헌터)"),
    (20, "D급 (중급 헌터)"),
    (30, "C급 (숙련 헌터)"),
    (40, "B급 (정예 헌터)"),
    (50, "A급 (초인)"),
    (999, "S급 (국가권력급)")
]

# 성장 시스템 타입
GROWTH_SYSTEM_STANDARD = "standard"
GROWTH_SYSTEM_DND = "dnd"
GROWTH_SYSTEM_HUNTER = "hunter"
GROWTH_SYSTEM_CUSTOM = "custom"

# 기본 성장 배율
STANDARD_GROWTH_MULTIPLIER = 1.2

# 훈련 관련 상수
BASE_TRAINING_FAIL_CHANCE = 0.1
STRESS_FAIL_MODIFIER = 0.005
TRAINING_STRESS_SUCCESS_MIN = 5
TRAINING_STRESS_SUCCESS_MAX = 10
TRAINING_STRESS_FAIL_MIN = 10
TRAINING_STRESS_FAIL_MAX = 20

# 휴식 관련 상수
REST_RECOVERY_MIN = 20
REST_RECOVERY_MAX = 40

# 휴식으로 회복 가능한 상태이상
RECOVERABLE_CONDITIONS = ["지침", "피로", "가벼운 부상"]

# 레벨업 시 보너스 스탯 후보
LEVEL_UP_BONUS_STATS = ["근력", "지능", "매력"]


# =========================================================
# 헌터 랭크 시스템
# =========================================================
def get_hunter_rank(level: int) -> str:
    """레벨 숫자를 헌터 등급으로 변환합니다."""
    for threshold, rank_name in HUNTER_RANK_TABLE:
        if level < threshold:
            return rank_name
    return HUNTER_RANK_TABLE[-1][1]


# =========================================================
# 성장 시스템
# =========================================================
def _apply_level_up_bonus(user_data: Dict[str, Any]) -> None:
    """레벨업 시 랜덤 스탯 보너스를 적용합니다."""
    bonus_stat = random.choice(LEVEL_UP_BONUS_STATS)
    
    if "stats" not in user_data:
        user_data["stats"] = {}
    
    if bonus_stat in user_data["stats"]:
        user_data["stats"][bonus_stat] += 1


def _calc_standard_growth(
    user_data: Dict[str, Any],
    amount: int
) -> Tuple[Dict[str, Any], bool]:
    """
    표준 성장: 경험치통이 1.2배씩 늘어나는 방식입니다.
    
    Args:
        user_data: 사용자 데이터
        amount: 획득 경험치
    
    Returns:
        (업데이트된 사용자 데이터, 레벨업 여부)
    """
    user_data["xp"] += amount
    leveled_up = False
    
    if not isinstance(user_data.get("level"), int):
        return user_data, False
    
    while user_data["xp"] >= user_data["next_xp"]:
        user_data["xp"] -= user_data["next_xp"]
        user_data["level"] += 1
        user_data["next_xp"] = int(user_data["next_xp"] * STANDARD_GROWTH_MULTIPLIER)
        leveled_up = True
        _apply_level_up_bonus(user_data)
    
    return user_data, leveled_up


def _calc_dnd_growth(
    user_data: Dict[str, Any],
    amount: int
) -> Tuple[Dict[str, Any], bool]:
    """
    D&D 스타일 성장: 고정된 XP 테이블을 사용합니다.
    
    Args:
        user_data: 사용자 데이터
        amount: 획득 경험치
    
    Returns:
        (업데이트된 사용자 데이터, 레벨업 여부)
    """
    user_data["xp"] += amount
    
    if not isinstance(user_data.get("level"), int):
        return user_data, False
    
    current_lv = user_data["level"]
    target_xp = DND_XP_TABLE.get(current_lv, 999999)
    
    leveled_up = False
    if user_data["xp"] >= target_xp:
        user_data["xp"] -= target_xp
        user_data["level"] += 1
        user_data["next_xp"] = DND_XP_TABLE.get(
            user_data["level"],
            int(target_xp * STANDARD_GROWTH_MULTIPLIER)
        )
        leveled_up = True
        _apply_level_up_bonus(user_data)
    
    return user_data, leveled_up


def gain_experience(
    user_data: Dict[str, Any],
    amount: int,
    system_type: str = GROWTH_SYSTEM_STANDARD
) -> Tuple[Dict[str, Any], str, Union[bool, str]]:
    """
    경험치 획득 통합 함수입니다.
    
    Args:
        user_data: 사용자 데이터
        amount: 획득 경험치
        system_type: 성장 시스템 타입 ('standard', 'dnd', 'hunter', 'custom')
    
    Returns:
        (업데이트된 사용자 데이터, 결과 메시지, 레벨업 여부 또는 "CheckAI")
    """
    # 기본값 보정
    if "level" not in user_data:
        user_data["level"] = 1
    if "xp" not in user_data:
        user_data["xp"] = 0
    if "next_xp" not in user_data:
        user_data["next_xp"] = 100
    
    mask = user_data.get("mask", "Unknown")
    
    # 커스텀 모드: 계산은 AI에게 맡김
    if system_type == GROWTH_SYSTEM_CUSTOM:
        user_data["xp"] += amount
        msg = (
            f"🆙 **경험치 획득:** {mask} +{amount} XP "
            f"(현재: {user_data['xp']}, 룰에 따른 레벨업 판정 중...)"
        )
        return user_data, msg, "CheckAI"
    
    # D&D 성장
    if system_type == GROWTH_SYSTEM_DND:
        user_data, leveled_up = _calc_dnd_growth(user_data, amount)
        level_display = f"Lv.{user_data['level']}"
    
    # 표준/헌터 성장
    else:
        user_data, leveled_up = _calc_standard_growth(user_data, amount)
        
        if system_type == GROWTH_SYSTEM_HUNTER:
            level_display = f"[{get_hunter_rank(user_data['level'])}]"
        else:
            level_display = f"Lv.{user_data['level']}"
    
    # 결과 메시지 생성
    if leveled_up:
        msg = f"🎉 **레벨 업!** {mask}님이 **{level_display}**가 되었습니다!"
    else:
        msg = (
            f"🆙 **경험치 획득:** {mask} +{amount} XP "
            f"(현재: {level_display}, XP: {user_data['xp']}/{user_data['next_xp']})"
        )
    
    return user_data, msg, leveled_up


# =========================================================
# 훈련 및 휴식 (스탯 & 스트레스 관리)
# =========================================================
def train_character(
    user_data: Dict[str, Any],
    stat_type: str
) -> Tuple[Dict[str, Any], str]:
    """
    캐릭터 훈련을 수행합니다.
    
    Args:
        user_data: 사용자 데이터
        stat_type: 훈련할 스탯 종류
    
    Returns:
        (업데이트된 사용자 데이터, 결과 메시지)
    """
    stats = user_data.get("stats", {})
    
    if stat_type not in stats:
        stats[stat_type] = 0
    
    current_val = stats.get(stat_type, 0)
    stress = stats.get("스트레스", 0)
    
    # 실패 확률 계산 (스트레스가 높을수록 실패 확률 증가)
    fail_chance = BASE_TRAINING_FAIL_CHANCE + (stress * STRESS_FAIL_MODIFIER)
    is_success = random.random() > fail_chance
    
    if is_success:
        gain = random.randint(1, 2)
        stats[stat_type] = current_val + gain
        stats["스트레스"] = stress + random.randint(
            TRAINING_STRESS_SUCCESS_MIN,
            TRAINING_STRESS_SUCCESS_MAX
        )
        result_msg = f"✨ **훈련 성공!** {stat_type} +{gain} (현재: {stats[stat_type]})"
    else:
        stats["스트레스"] = stress + random.randint(
            TRAINING_STRESS_FAIL_MIN,
            TRAINING_STRESS_FAIL_MAX
        )
        result_msg = "💦 **훈련 실패...** 집중력이 흐트러졌습니다. (스트레스 대폭 상승)"
    
    user_data["stats"] = stats
    return user_data, result_msg


def rest_character(user_data: Dict[str, Any]) -> Tuple[Dict[str, Any], str]:
    """
    캐릭터 휴식을 수행합니다.
    
    Args:
        user_data: 사용자 데이터
    
    Returns:
        (업데이트된 사용자 데이터, 결과 메시지)
    """
    stats = user_data.get("stats", {})
    stress = stats.get("스트레스", 0)
    
    # 스트레스 회복
    recovery = random.randint(REST_RECOVERY_MIN, REST_RECOVERY_MAX)
    new_stress = max(0, stress - recovery)
    stats["스트레스"] = new_stress
    user_data["stats"] = stats
    
    # 상태이상 회복
    status_list = user_data.get("status_effects", [])
    recovered_effects = []
    
    for condition in RECOVERABLE_CONDITIONS:
        if condition in status_list:
            status_list.remove(condition)
            recovered_effects.append(condition)
    
    user_data["status_effects"] = status_list
    
    # 결과 메시지
    msg = f"💤 **휴식:** 스트레스가 {recovery}만큼 회복되었습니다. (현재: {new_stress})"
    
    if recovered_effects:
        msg += f"\n✨ **상태 회복:** {', '.join(recovered_effects)}"
    
    return user_data, msg


# =========================================================
# 인벤토리 관리
# =========================================================
def update_inventory(
    user_data: Dict[str, Any],
    action: str,
    item_name: str,
    count: int = 1
) -> Tuple[Dict[str, Any], str]:
    """
    인벤토리를 업데이트합니다.
    
    Args:
        user_data: 사용자 데이터
        action: "add" 또는 "remove"
        item_name: 아이템 이름
        count: 수량 (기본값: 1)
    
    Returns:
        (업데이트된 사용자 데이터, 결과 메시지)
    """
    inv = user_data.get("inventory", {})
    current_qty = inv.get(item_name, 0)
    
    if action == "add":
        inv[item_name] = current_qty + count
        msg = f"🎒 **획득:** {item_name} x{count} (현재: {inv[item_name]})"
    
    elif action == "remove":
        if current_qty < count:
            msg = f"❌ **사용 실패:** {item_name} 부족 (보유: {current_qty})"
        else:
            inv[item_name] = current_qty - count
            
            if inv[item_name] <= 0:
                del inv[item_name]
                msg = f"🗑️ **사용/버림:** {item_name} x{count} (남음: 0)"
            else:
                msg = f"📉 **사용:** {item_name} x{count} (남음: {inv[item_name]})"
    else:
        msg = "⚠️ 알 수 없는 동작"
    
    user_data["inventory"] = inv
    return user_data, msg


# =========================================================
# 상태이상 관리
# =========================================================
def update_status_effect(
    user_data: Dict[str, Any],
    action: str,
    effect_name: str
) -> Tuple[Dict[str, Any], str]:
    """
    상태이상을 업데이트합니다.
    
    Args:
        user_data: 사용자 데이터
        action: "add" 또는 "remove"
        effect_name: 상태이상 이름
    
    Returns:
        (업데이트된 사용자 데이터, 결과 메시지)
    """
    effects = user_data.get("status_effects", [])
    
    if action == "add":
        if effect_name not in effects:
            effects.append(effect_name)
            msg = f"💀 **상태이상 발생:** [{effect_name}]"
        else:
            msg = f"⚠️ 이미 [{effect_name}] 상태입니다."
    
    elif action == "remove":
        if effect_name in effects:
            effects.remove(effect_name)
            msg = f"✨ **상태 회복:** [{effect_name}] 제거됨"
        else:
            msg = f"⚠️ [{effect_name}] 상태가 아닙니다."
    else:
        msg = "⚠️ 알 수 없는 동작"
    
    user_data["status_effects"] = effects
    return user_data, msg


# =========================================================
# 관계도 관리
# =========================================================
def modify_relationship(
    user_data: Dict[str, Any],
    target_name: str,
    amount: int
) -> Tuple[Dict[str, Any], str]:
    """
    NPC와의 관계도를 수정합니다.
    
    Args:
        user_data: 사용자 데이터
        target_name: 대상 NPC 이름
        amount: 변화량 (양수: 호감도 상승, 음수: 하락)
    
    Returns:
        (업데이트된 사용자 데이터, 결과 메시지)
    """
    rels = user_data.get("relations", {})
    current = rels.get(target_name, 0)
    new_val = current + amount
    rels[target_name] = new_val
    user_data["relations"] = rels
    
    emoji = "💖" if amount > 0 else "💔"
    msg = f"{emoji} **{target_name}** 관계: {amount:+} ({new_val})"
    
    return user_data, msg
