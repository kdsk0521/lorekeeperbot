import random
import domain_manager

DEFAULT_TIME_SLOTS = ["새벽", "오전", "오후", "황혼", "저녁", "심야"]
DEFAULT_WEATHER_TYPES = ["맑음", "구름 조금", "흐림", "비", "안개", "폭풍우"]

def get_time_slots(channel_id): return DEFAULT_TIME_SLOTS
def get_weather_types(channel_id): return DEFAULT_WEATHER_TYPES

def advance_time(channel_id):
    world = domain_manager.get_world_state(channel_id)
    if not world: return "⚠️ 데이터 없음"

    time_slots = get_time_slots(channel_id)
    weather_types = get_weather_types(channel_id)

    current_slot = world.get("time_slot", time_slots[1])
    try: current_idx = time_slots.index(current_slot)
    except ValueError: current_idx = 0
    
    msg = ""
    next_idx = current_idx + 1
    
    if next_idx >= len(time_slots):
        world["time_slot"] = time_slots[0]; world["day"] += 1
        new_weather = random.choice(weather_types)
        world["weather"] = new_weather
        msg = f"🌙 밤이 지나고 **{world['day']}일차 {time_slots[0]}**이 되었습니다. (날씨: {new_weather})"
    else:
        world["time_slot"] = time_slots[next_idx]
        msg = f"🕰️ 시간이 흘러 **{world['time_slot']}**가 되었습니다."

    doom_increase = 0
    doom_reasons = []
    
    # 1. 시간대 (밤/황혼)
    is_night_time = next_idx >= len(time_slots) - 2
    if "황혼" in world["time_slot"]: is_night_time = True

    if is_night_time:
        doom_increase += 1
        msg += " (🌅 해가 저물며 그림자가 길어집니다...)" if "황혼" in world["time_slot"] else " (🌑 어둠이 짙어집니다...)"

    # 2. 관계도 (적대적 관계)
    domain = domain_manager.get_domain(channel_id)
    participants = domain.get("participants", {})
    nemesis_detected = False
    
    for uid, p in participants.items():
        if p.get("status") == "left": continue
        rels = p.get("relations", {})
        for npc_name, score in rels.items():
            if score <= -10: nemesis_detected = True; break
        if nemesis_detected: break
    
    if nemesis_detected:
        doom_increase += random.randint(1, 2)
        doom_reasons.append("👿 적대 세력")

    # 3. [신규] 실시간 위험도 (AI 판단 우선)
    ai_risk = world.get("current_risk_level", "None").lower()
    location = world.get("current_location", "Unknown")
    
    if "high" in ai_risk:
        doom_increase += 3
        doom_reasons.append(f"💀 위험 지역({location}): 고위험 감지")
    elif "medium" in ai_risk:
        doom_increase += 2
        doom_reasons.append(f"⚠️ 위험 지역({location}): 주의 필요")
    
    # 4. 정적 규칙 (Lore 기반)
    loc_rules = world.get("location_rules", {})
    for loc_name, rule in loc_rules.items():
        if loc_name in location:
            condition = rule.get("condition", "").lower()
            if ("night" in condition and is_night_time) or "always" in condition:
                if "high" not in ai_risk: 
                    doom_increase += 1
                    doom_reasons.append(f"📜 로어 규칙({loc_name})")

    if doom_increase > 0:
        world["doom"] = min(100, world.get("doom", 0) + doom_increase)
        for reason in doom_reasons:
            if "위험 지역" in reason or "로어 규칙" in reason:
                msg += f"\n⚠️ **경고:** {reason}"

    domain_manager.update_world_state(channel_id, world)
    return msg

def change_doom(channel_id, amount):
    world = domain_manager.get_world_state(channel_id)
    current = world.get("doom", 0)
    new_val = max(0, min(100, current + amount))
    world["doom"] = new_val
    domain_manager.update_world_state(channel_id, world)
    
    doom_desc = "평온함"
    if new_val >= 30: doom_desc = "불길한 징조"
    if new_val >= 70: doom_desc = "임박한 위협"
    if new_val >= 90: doom_desc = "절망적"
    if new_val >= 100: doom_desc = "💥 파멸 💥"
    
    return f"📉 **위기 수치 변경:** {current}% -> {new_val}% ({doom_desc})"

def get_world_context(channel_id):
    world = domain_manager.get_world_state(channel_id)
    if not world: return ""
    party_context = domain_manager.get_party_status_context(channel_id)
    
    loc = world.get("current_location", "Unknown")
    
    return (
        f"[Current World State]\n"
        f"- Location: {loc}\n"
        f"- Time: Day {world['day']}, {world['time_slot']}\n"
        f"- Weather: {world['weather']}\n"
        f"- Doom Level: {world['doom']}% ({world.get('doom_name', '위기')})\n"
        f"- **Atmosphere Context**: {party_context}\n"
        f"*Instruction: Adjust the narrative tone based on Location, Time, Doom, and Party Condition.*"
    )