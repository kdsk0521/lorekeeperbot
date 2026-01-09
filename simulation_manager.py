"""
Lorekeeper TRPG Bot - Simulation Manager Module
인벤토리, 상태이상, AI 패시브 관리를 담당합니다.

[v4.0 최종본]
경험치/레벨/스탯/훈련 시스템이 완전히 제거되었습니다.
성장은 AI 메모리의 패시브/칭호로 표현됩니다.

=== 성장 시스템 ===
- "기본": 기본 룰북 + AI 패시브/칭호 자동 부여
- "커스텀": 사용자 룰북만 적용 (AI가 룰에 따라 판단)

=== 사용 중인 함수 ===
상태이상:
- STATUS_EFFECTS: 상태이상 상수
- get_status_summary(): 상태 요약
- update_status_effect(): 상태이상 추가/제거
- get_all_status_effects_by_category(): 카테고리별 상태이상

인벤토리:
- update_inventory(): 인벤토리 관리

AI 패시브:
- get_passives_for_context(): AI용 패시브 컨텍스트
- grant_ai_passive(): AI 패시브 부여
- get_passive_list(): 패시브 목록
- get_passive_context(): 패시브 컨텍스트

비일상 적응:
- expose_to_abnormal(): 비일상 노출 처리
- get_normality_stage(): 적응 단계 확인
- get_abnormal_context(): 적응도 컨텍스트
"""

import random
from typing import Dict, Any, Tuple, List, Optional

# =========================================================
# 성장 시스템 타입 (v4.0: 두 가지만 지원)
# =========================================================
GROWTH_SYSTEM_DEFAULT = "default"  # 기본 룰북 + AI 패시브
GROWTH_SYSTEM_CUSTOM = "custom"    # 커스텀 룰북만 사용

# =========================================================
# 상태이상 분류 시스템
# world_manager의 doom 계산에 사용됨
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


def get_status_doom_modifier(status_effects: List[str]) -> Tuple[int, int, List[str], List[str]]:
    """
    상태이상 목록에서 doom 수정치를 계산합니다.
    
    Args:
        status_effects: 현재 상태이상 목록
    
    Returns:
        (increase, decrease, negative_list, positive_list)
    """
    increase = 0
    decrease = 0
    negative_found = []
    positive_found = []
    
    for effect in status_effects:
        effect_lower = effect.lower()
        
        # 부정적 상태 체크
        for neg_effect, value in NEGATIVE_STATUS_EFFECTS.items():
            if neg_effect in effect_lower or effect_lower in neg_effect:
                increase += value
                negative_found.append(f"{effect} (+{value})")
                break
        else:
            # 긍정적 상태 체크
            for pos_effect, value in POSITIVE_STATUS_EFFECTS.items():
                if pos_effect in effect_lower or effect_lower in pos_effect:
                    decrease += value
                    positive_found.append(f"{effect} (-{value})")
                    break
    
    return increase, decrease, negative_found, positive_found


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


def get_status_effect_info(effect_name: str) -> Optional[Dict[str, Any]]:
    """상태이상 정보를 반환합니다."""
    return STATUS_EFFECTS.get(effect_name)


def get_all_status_effects_by_category(category: str) -> List[str]:
    """특정 카테고리의 모든 상태이상 이름을 반환합니다."""
    return [
        name for name, data in STATUS_EFFECTS.items()
        if data.get("category") == category
    ]


def get_active_debuffs(user_data: Dict[str, Any]) -> List[str]:
    """현재 활성화된 디버프 목록을 반환합니다."""
    effects = user_data.get("status_effects", [])
    return [e for e in effects if STATUS_EFFECTS.get(e, {}).get("type") == "debuff"]


def get_active_buffs(user_data: Dict[str, Any]) -> List[str]:
    """현재 활성화된 버프 목록을 반환합니다."""
    effects = user_data.get("status_effects", [])
    return [e for e in effects if STATUS_EFFECTS.get(e, {}).get("type") == "buff"]


def calculate_status_doom_contribution(user_data: Dict[str, Any]) -> Tuple[int, List[str]]:
    """
    상태이상이 Doom에 미치는 영향을 계산합니다.
    
    Returns:
        (doom_delta, reasons)
    """
    effects = user_data.get("status_effects", [])
    total_doom = 0
    reasons = []
    
    for effect_name in effects:
        effect_data = STATUS_EFFECTS.get(effect_name, {})
        if effect_data.get("type") == "debuff":
            severity = effect_data.get("severity", 1)
            doom_impact = SEVERITY_DOOM_IMPACT.get(severity, 0)
            
            if doom_impact > 0:
                total_doom += doom_impact
                reasons.append(f"💀 {effect_name} (심각도 {severity})")
    
    return total_doom, reasons


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
    effect_info = STATUS_EFFECTS.get(effect_name, {})
    
    if action == "add":
        if effect_name not in effects:
            effects.append(effect_name)
            
            # 상태이상 타입에 따른 메시지
            if effect_info.get("type") == "buff":
                msg = f"✨ **버프 획득:** [{effect_name}]"
                if effect_info.get("description"):
                    msg += f" - {effect_info['description']}"
            else:
                severity = effect_info.get("severity", 1)
                severity_icon = "⚠️" if severity == 1 else "🔴" if severity == 2 else "💀"
                msg = f"{severity_icon} **상태이상 발생:** [{effect_name}]"
                if effect_info.get("description"):
                    msg += f" - {effect_info['description']}"
        else:
            msg = f"⚠️ 이미 [{effect_name}] 상태입니다."
    
    elif action == "remove":
        if effect_name in effects:
            effects.remove(effect_name)
            msg = f"✨ **상태 해제:** [{effect_name}] 제거됨"
        else:
            msg = f"⚠️ [{effect_name}] 상태가 아닙니다."
    else:
        msg = "⚠️ 알 수 없는 동작"
    
    user_data["status_effects"] = effects
    return user_data, msg


def process_tick_effects(user_data: Dict[str, Any]) -> Tuple[Dict[str, Any], List[str]]:
    """
    턴/시간 경과 시 상태이상 효과를 처리합니다.
    
    Returns:
        (업데이트된 사용자 데이터, 메시지 목록)
    """
    effects = user_data.get("status_effects", [])
    messages = []
    effects_to_remove = []
    
    for effect_name in effects:
        effect_info = STATUS_EFFECTS.get(effect_name, {})
        
        # 틱 데미지 처리
        tick_damage = effect_info.get("tick_damage", 0)
        if tick_damage > 0:
            # 스트레스로 데미지 적용 (체력 시스템이 없으므로)
            stats = user_data.get("stats", {})
            current_stress = stats.get("스트레스", 0)
            stats["스트레스"] = current_stress + tick_damage * 5
            user_data["stats"] = stats
            messages.append(f"💔 [{effect_name}] 효과: 스트레스 +{tick_damage * 5}")
        
        # 지속시간 처리
        duration = effect_info.get("duration")
        if duration is not None:
            # duration 카운터 관리 (user_data에 별도 저장)
            duration_key = f"_duration_{effect_name}"
            remaining = user_data.get(duration_key, duration)
            remaining -= 1
            
            if remaining <= 0:
                effects_to_remove.append(effect_name)
                messages.append(f"⏰ [{effect_name}] 효과 종료")
                if duration_key in user_data:
                    del user_data[duration_key]
            else:
                user_data[duration_key] = remaining
                messages.append(f"⏳ [{effect_name}] 남은 시간: {remaining}턴")
    
    # 만료된 효과 제거
    for effect_name in effects_to_remove:
        if effect_name in effects:
            effects.remove(effect_name)
    
    user_data["status_effects"] = effects
    return user_data, messages


def get_status_summary(user_data: Dict[str, Any]) -> str:
    """
    캐릭터의 상태이상 요약을 반환합니다.
    """
    effects = user_data.get("status_effects", [])
    
    if not effects:
        return "✅ **상태:** 정상"
    
    buffs = []
    debuffs = []
    
    for effect_name in effects:
        effect_info = STATUS_EFFECTS.get(effect_name, {"type": "unknown"})
        if effect_info.get("type") == "buff":
            buffs.append(effect_name)
        else:
            debuffs.append(effect_name)
    
    result = ""
    
    if debuffs:
        result += "💀 **디버프:** " + ", ".join(debuffs) + "\n"
    
    if buffs:
        result += "✨ **버프:** " + ", ".join(buffs)
    
    return result.strip() if result else "✅ **상태:** 정상"


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


# =========================================================
# 비일상의 일상화 시스템 (Abnormal Normalization System)
# =========================================================

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

def get_normality_stage(normality: int) -> Dict[str, str]:
    """적응도에 따른 단계 정보를 반환합니다."""
    for (low, high), stage_info in NORMALITY_STAGES.items():
        if low <= normality < high:
            return stage_info
    return NORMALITY_STAGES[(80, 101)]  # 기본값: 일상화


def calculate_normality(count: int, base_threshold: int = 10) -> int:
    """
    노출 횟수에 따른 적응도를 계산합니다.
    
    Args:
        count: 노출 횟수
        base_threshold: 100% 도달에 필요한 기본 횟수
    
    Returns:
        적응도 (0-100)
    """
    if count <= 0:
        return 0
    
    # 로그 스케일로 빠르게 적응하다가 후반에 느려짐
    # 1회: ~20%, 3회: ~50%, 5회: ~70%, 10회: ~100%
    import math
    normality = min(100, int((math.log(count + 1) / math.log(base_threshold + 1)) * 100))
    return normality


def expose_to_abnormal(
    user_data: Dict[str, Any],
    abnormal_type: str,
    current_day: int = 1
) -> Tuple[Dict[str, Any], Optional[str], Optional[Dict]]:
    """
    비일상 요소에 노출되었을 때 호출합니다.
    
    Args:
        user_data: 사용자 데이터
        abnormal_type: 비일상 요소 이름 (예: "드래곤", "마법", "고백")
        current_day: 현재 게임 내 일차
    
    Returns:
        (업데이트된 user_data, 시스템 메시지 또는 None, 단계 정보)
    """
    exposure = user_data.get("abnormal_exposure", {})
    
    if abnormal_type not in exposure:
        exposure[abnormal_type] = {"count": 0, "normality": 0, "first_day": current_day}
    
    # 노출 횟수 증가
    exposure[abnormal_type]["count"] += 1
    count = exposure[abnormal_type]["count"]
    
    # 적응도 계산
    old_normality = exposure[abnormal_type]["normality"]
    new_normality = calculate_normality(count)
    exposure[abnormal_type]["normality"] = new_normality
    
    user_data["abnormal_exposure"] = exposure
    
    # 단계 변화 감지
    old_stage = get_normality_stage(old_normality)
    new_stage = get_normality_stage(new_normality)
    
    msg = None
    if old_stage["stage"] != new_stage["stage"]:
        msg = f"🌓 **[{abnormal_type}]** 적응 단계 변화: {old_stage['name']} → {new_stage['name']}"
    
    # 100% 도달 시 특별 메시지
    if old_normality < 100 and new_normality >= 100:
        msg = f"🌙 **[{abnormal_type}]** 이제 일상이 되었다. (적응도 100%)"
    
    return user_data, msg, new_stage


def get_abnormal_context(user_data: Dict[str, Any], abnormal_types: List[str]) -> str:
    """
    현재 장면의 비일상 요소들에 대한 적응 컨텍스트를 생성합니다.
    AI에게 전달할 톤 힌트를 반환합니다.
    
    Args:
        user_data: 사용자 데이터
        abnormal_types: 현재 장면에 등장하는 비일상 요소 리스트
    
    Returns:
        AI용 컨텍스트 문자열
    """
    if not abnormal_types:
        return ""
    
    exposure = user_data.get("abnormal_exposure", {})
    contexts = []
    
    for ab_type in abnormal_types:
        if ab_type in exposure:
            normality = exposure[ab_type]["normality"]
            stage = get_normality_stage(normality)
            contexts.append(
                f"- {ab_type}: 적응도 {normality}% ({stage['name']}) → {stage['reaction_hint']}"
            )
        else:
            # 첫 노출
            contexts.append(
                f"- {ab_type}: 적응도 0% (첫 노출!) → 경악, 공포, 믿을 수 없다는 반응"
            )
    
    return "### [비일상 적응도]\n" + "\n".join(contexts) + "\n"


# =========================================================
# AI 패시브 시스템 (AI-Driven Passive System)
# =========================================================
# v4.0: 하드코딩된 트리거 제거, AI가 서사적으로 패시브 부여

def get_passive_list(user_data: Dict[str, Any]) -> str:
    """보유 패시브 목록을 문자열로 반환합니다."""
    passives = user_data.get("passives", [])
    ai_mem = user_data.get("ai_memory", {})
    ai_passives = ai_mem.get("passives", [])
    
    # 두 소스 통합
    all_passives = passives + [{"name": p, "effect": "", "category": "AI"} for p in ai_passives if isinstance(p, str)]
    
    if not all_passives:
        return "📋 **보유 패시브:** 없음\n(경험을 쌓으면 AI가 패시브를 부여합니다)"
    
    result = "📋 **보유 패시브:**\n"
    for p in all_passives:
        if isinstance(p, dict):
            name = p.get("name", "???")
            effect = p.get("effect", "")
            if effect:
                result += f"  • **{name}**: {effect}\n"
            else:
                result += f"  • **{name}**\n"
        else:
            result += f"  • **{p}**\n"
    
    return result


def get_passive_context(user_data: Dict[str, Any]) -> str:
    """AI에게 전달할 패시브 컨텍스트를 생성합니다."""
    passives = user_data.get("passives", [])
    ai_mem = user_data.get("ai_memory", {})
    ai_passives = ai_mem.get("passives", [])
    
    all_names = []
    for p in passives:
        if isinstance(p, dict):
            all_names.append(p.get("name", ""))
        else:
            all_names.append(str(p))
    all_names.extend([p for p in ai_passives if isinstance(p, str)])
    
    if not all_names:
        return ""
    
    return (
        "### [캐릭터 패시브]\n"
        f"{', '.join(all_names)}\n"
        "*패시브 효과를 서사에 자연스럽게 반영하세요.*\n\n"
    )


def grant_ai_passive(
    user_data: Dict[str, Any],
    passive_suggestion: Dict[str, Any],
    current_day: int = 1
) -> Tuple[Dict[str, Any], Optional[str]]:
    """
    AI가 제안한 패시브를 부여합니다.
    
    Args:
        user_data: 사용자 데이터
        passive_suggestion: AI가 제안한 패시브 정보
            {
                "name": "엘프의 친구",
                "trigger": "엘프와 우호적 상호작용 10회",
                "effect": "엘프에게 호감도 보너스, 엘프어 기초 이해",
                "category": "사회",
                "reasoning": "플레이어가 엘프 NPC들과 지속적으로..."
            }
        current_day: 현재 게임 내 일차
    
    Returns:
        (업데이트된 user_data, 획득 메시지 또는 None)
    """
    if not passive_suggestion:
        return user_data, None
    
    name = passive_suggestion.get("name")
    if not name:
        return user_data, None
    
    passives = user_data.get("passives", [])
    
    # 이미 보유 중인지 확인
    if any(p["name"] == name for p in passives):
        return user_data, None
    
    # 새 패시브 생성
    new_passive = {
        "name": name,
        "effect": passive_suggestion.get("effect", "효과 미정"),
        "category": passive_suggestion.get("category", "기타"),
        "trigger": passive_suggestion.get("trigger", "AI 판단"),
        "acquired_day": current_day,
        "source": "AI",  # AI가 부여했음을 표시
        "reasoning": passive_suggestion.get("reasoning", "")
    }
    
    passives.append(new_passive)
    user_data["passives"] = passives
    
    msg = (
        f"🏆 **패시브 획득!**\n"
        f"**[{name}]** ({new_passive['category']})\n"
        f"_{new_passive['effect']}_\n"
        f"(조건: {new_passive['trigger']})"
    )
    
    return user_data, msg


def get_passives_for_context(user_data: Dict[str, Any]) -> str:
    """
    AI 분석에 전달할 현재 보유 패시브 목록을 생성합니다.
    중복 부여 방지용.
    """
    # 레거시 passives (List[Dict] 또는 List[str])
    passives = user_data.get("passives", [])
    # 새 시스템 ai_memory.passives (List[str])
    ai_mem = user_data.get("ai_memory", {})
    ai_passives = ai_mem.get("passives", [])
    
    # 모든 패시브 이름 수집
    all_names = []
    for p in passives:
        if isinstance(p, dict):
            all_names.append(p.get("name", ""))
        elif isinstance(p, str):
            all_names.append(p)
    
    for p in ai_passives:
        if isinstance(p, str) and p not in all_names:
            all_names.append(p)
    
    if not all_names:
        return "보유 패시브: 없음"
    
    return f"보유 패시브: {', '.join(all_names)}"
