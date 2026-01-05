import random

# =========================================================
# 경험치 및 성장 시스템
# =========================================================
DND_XP_TABLE = {
    1: 300, 2: 900, 3: 2700, 4: 6500, 5: 14000, 
    6: 23000, 7: 34000, 8: 48000, 9: 64000, 10: 85000
}

def get_hunter_rank(level):
    """레벨 숫자를 헌터 등급으로 변환하는 함수"""
    if level < 5: return "F급 (일반인)"
    if level < 10: return "E급 (하급 헌터)"
    if level < 20: return "D급 (중급 헌터)"
    if level < 30: return "C급 (숙련 헌터)"
    if level < 40: return "B급 (정예 헌터)"
    if level < 50: return "A급 (초인)"
    return "S급 (국가권력급)"

def _calc_standard_growth(user_data, amount):
    """표준 성장: 경험치통이 1.2배씩 늘어나는 방식"""
    user_data["xp"] += amount
    leveled_up = False
    
    if not isinstance(user_data["level"], int): return user_data, False

    while user_data["xp"] >= user_data["next_xp"]:
        user_data["xp"] -= user_data["next_xp"]
        user_data["level"] += 1
        user_data["next_xp"] = int(user_data["next_xp"] * 1.2)
        leveled_up = True
        
        bonus = random.choice(["근력", "지능", "매력"])
        if bonus in user_data["stats"]:
            user_data["stats"][bonus] += 1
        
    return user_data, leveled_up

def _calc_dnd_growth(user_data, amount):
    """D&D 스타일 성장: 고정된 XP 테이블 사용"""
    user_data["xp"] += amount
    if not isinstance(user_data["level"], int): return user_data, False

    current_lv = user_data["level"]
    target_xp = DND_XP_TABLE.get(current_lv, 999999) 
    
    leveled_up = False
    if user_data["xp"] >= target_xp:
        user_data["xp"] -= target_xp
        user_data["level"] += 1
        user_data["next_xp"] = DND_XP_TABLE.get(user_data["level"], int(target_xp * 1.2))
        leveled_up = True
        
        bonus = random.choice(["근력", "지능", "매력"])
        if bonus in user_data["stats"]:
            user_data["stats"][bonus] += 1

    return user_data, leveled_up

def gain_experience(user_data, amount, system_type="standard"):
    """
    경험치 획득 통합 함수
    system_type: 'standard', 'dnd', 'hunter', 'custom'
    """
    if "level" not in user_data: user_data["level"] = 1
    if "xp" not in user_data: user_data["xp"] = 0
    if "next_xp" not in user_data: user_data["next_xp"] = 100

    mask = user_data.get("mask", "Unknown")
    
    # [신규] 커스텀 모드: 계산은 AI에게 맡기므로 여기선 XP만 더하고 종료
    if system_type == "custom":
        user_data["xp"] += amount
        return user_data, f"🆙 **경험치 획득:** {mask} +{amount} XP (현재: {user_data['xp']}, 룰에 따른 레벨업 판정 중...)", "CheckAI"

    if system_type == "dnd":
        user_data, leveled_up = _calc_dnd_growth(user_data, amount)
        level_display = f"Lv.{user_data['level']}"
    else:
        user_data, leveled_up = _calc_standard_growth(user_data, amount)
        if system_type == "hunter":
            level_display = f"[{get_hunter_rank(user_data['level'])}]"
        else:
            level_display = f"Lv.{user_data['level']}"

    if leveled_up:
        return user_data, f"🎉 **레벨 업!** {mask}님이 **{level_display}**가 되었습니다!", True
    else:
        return user_data, f"🆙 **경험치 획득:** {mask} +{amount} XP (현재: {level_display}, XP: {user_data['xp']}/{user_data['next_xp']})", False

# =========================================================
# 훈련 및 휴식 (스탯 & 스트레스 관리)
# =========================================================
def train_character(user_data, stat_type):
    stats = user_data.get("stats", {})
    if stat_type not in stats: stats[stat_type] = 0
    current_val = stats.get(stat_type, 0)
    stress = stats.get("스트레스", 0)
    
    fail_chance = 0.1 + (stress * 0.005) 
    is_success = random.random() > fail_chance

    if is_success:
        gain = random.randint(1, 2)
        stats[stat_type] = current_val + gain
        stats["스트레스"] = stress + random.randint(5, 10)
        result_msg = f"✨ **훈련 성공!** {stat_type} +{gain} (현재: {stats[stat_type]})"
    else:
        stats["스트레스"] = stress + random.randint(10, 20)
        result_msg = f"💦 **훈련 실패...** 집중력이 흐트러졌습니다. (스트레스 대폭 상승)"

    user_data["stats"] = stats
    return user_data, result_msg

def rest_character(user_data):
    stats = user_data.get("stats", {})
    stress = stats.get("스트레스", 0)
    recovery = random.randint(20, 40)
    
    new_stress = max(0, stress - recovery)
    stats["스트레스"] = new_stress
    user_data["stats"] = stats
    
    status_list = user_data.get("status_effects", [])
    recovered_effects = []
    
    for cond in ["지침", "피로", "가벼운 부상"]:
        if cond in status_list:
            status_list.remove(cond)
            recovered_effects.append(cond)
            
    user_data["status_effects"] = status_list
    
    msg = f"💤 **휴식:** 스트레스가 {recovery}만큼 회복되었습니다. (현재: {new_stress})"
    if recovered_effects:
        msg += f"\n✨ **상태 회복:** {', '.join(recovered_effects)}"
        
    return user_data, msg

# =========================================================
# 인벤토리 및 상태이상 관리
# =========================================================
def update_inventory(user_data, action, item_name, count=1):
    inv = user_data.get("inventory", {})
    current_qty = inv.get(item_name, 0)
    msg = ""
    
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

def update_status_effect(user_data, action, effect_name):
    effects = user_data.get("status_effects", [])
    msg = ""
    
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
    
    user_data["status_effects"] = effects
    return user_data, msg

def modify_relationship(user_data, target_name, amount):
    rels = user_data.get("relations", {})
    current = rels.get(target_name, 0)
    new_val = current + amount
    rels[target_name] = new_val
    user_data["relations"] = rels
    emoji = "💖" if amount > 0 else "💔"
    return user_data, f"{emoji} **{target_name}** 관계: {amount:+} ({new_val})"