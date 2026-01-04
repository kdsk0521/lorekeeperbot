import random
import domain_manager

# [기본값] 설정이 없을 때 사용할 기본 시간표와 날씨
DEFAULT_TIME_SLOTS = ["새벽", "오전", "오후", "황혼", "저녁", "심야"]
DEFAULT_WEATHER_TYPES = ["맑음", "구름 조금", "흐림", "비", "안개", "폭풍우"]

def get_time_slots(channel_id):
    return DEFAULT_TIME_SLOTS

def get_weather_types(channel_id):
    return DEFAULT_WEATHER_TYPES

def advance_time(channel_id):
    """
    시간을 한 단계 진행시키고, 환경 및 관계도에 따라 위기(Doom)를 변화시킵니다.
    """
    world = domain_manager.get_world_state(channel_id)
    if not world: return "⚠️ 데이터 없음"

    time_slots = get_time_slots(channel_id)
    weather_types = get_weather_types(channel_id)

    # 1. 시간 흐름 처리
    current_slot = world.get("time_slot", time_slots[1])
    try:
        current_idx = time_slots.index(current_slot)
    except ValueError:
        current_idx = 0
    
    msg = ""
    next_idx = current_idx + 1
    
    if next_idx >= len(time_slots):
        world["time_slot"] = time_slots[0]
        world["day"] += 1
        new_weather = random.choice(weather_types)
        world["weather"] = new_weather
        msg = f"🌙 밤이 지나고 **{world['day']}일차 {time_slots[0]}**이 되었습니다. (날씨: {new_weather})"
    else:
        world["time_slot"] = time_slots[next_idx]
        msg = f"🕰️ 시간이 흘러 **{world['time_slot']}**가 되었습니다."

    # 2. 위기(Doom) 수치 변동 로직
    doom_increase = 0
    
    # (1) 시간대에 따른 위기 상승 (밤/황혼)
    is_night_time = next_idx >= len(time_slots) - 2
    if "황혼" in world["time_slot"]: is_night_time = True

    if is_night_time:
        doom_increase += 1
        if "황혼" in world["time_slot"]:
            msg += " (🌅 해가 저물며 그림자가 길어집니다...)"
        else:
            msg += " (🌑 어둠이 짙어집니다...)"

    # (2) [기능] 관계도(적대적 관계)에 따른 위기 상승
    domain = domain_manager.get_domain(channel_id)
    participants = domain.get("participants", {})
    nemesis_detected = False
    
    for uid, p in participants.items():
        if p.get("status") == "left": continue
        rels = p.get("relations", {})
        
        for npc_name, score in rels.items():
            if score <= -10: # 원수지간 기준점
                nemesis_detected = True
                break
        if nemesis_detected: break
    
    if nemesis_detected:
        doom_increase += random.randint(1, 2)
        
    # 최종 위기 적용
    if doom_increase > 0:
        world["doom"] = min(100, world.get("doom", 0) + doom_increase)
        if nemesis_detected:
            msg += f"\n📉 **위기 상승 (+{doom_increase}):** 누군가 당신들을 노리고 있습니다..."

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
    
    return (
        f"[Current World State]\n"
        f"- Time: Day {world['day']}, {world['time_slot']}\n"
        f"- Weather: {world['weather']}\n"
        f"- Doom Level: {world['doom']}% ({world.get('doom_name', '위기')})\n"
        f"- **Atmosphere Context**: {party_context}\n"
        f"*Instruction: Adjust the narrative tone based on Time, Doom, and Party Condition (Injuries/Hostility).*"
    )