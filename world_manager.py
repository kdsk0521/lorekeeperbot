import random
import domain_manager

# [기본값] 설정이 없을 때 사용할 기본 시간표와 날씨 (하드코딩 아님, 백업용)
DEFAULT_TIME_SLOTS = ["새벽", "오전", "오후", "황혼", "저녁", "심야"]
DEFAULT_WEATHER_TYPES = ["맑음", "구름 조금", "흐림", "비", "안개", "폭풍우"]

def get_time_slots(channel_id):
    """해당 채널의 시간표 설정을 가져옵니다."""
    return DEFAULT_TIME_SLOTS

def get_weather_types(channel_id):
    """해당 채널의 날씨 목록을 가져옵니다."""
    return DEFAULT_WEATHER_TYPES

def advance_time(channel_id):
    """시간을 한 단계 진행시킵니다."""
    world = domain_manager.get_world_state(channel_id)
    if not world: return "⚠️ 데이터 없음"

    time_slots = get_time_slots(channel_id)
    weather_types = get_weather_types(channel_id)

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

    is_night_time = next_idx >= len(time_slots) - 2
    if "황혼" in world["time_slot"]: is_night_time = True

    if is_night_time:
        world["doom"] = min(100, world.get("doom", 0) + 1)
        if "황혼" in world["time_slot"]:
            msg += " (🌅 해가 저물며 그림자가 길어집니다...)"
        else:
            msg += " (🌑 어둠이 짙어집니다...)"

    domain_manager.update_world_state(channel_id, world)
    return msg

def change_doom(channel_id, amount):
    """위기(Doom) 수치를 조정합니다."""
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
    """
    [수정] AI 프롬프트에 주입할 '현재 세계 상태'를 생성합니다.
    시간/날씨/위기 뿐만 아니라, 파티의 상태이상과 관계도 요약도 포함합니다.
    """
    world = domain_manager.get_world_state(channel_id)
    if not world: return ""
    
    # [신규] 파티 상태 및 관계 요약 가져오기
    party_context = domain_manager.get_party_status_context(channel_id)
    
    return (
        f"[Current World State]\n"
        f"- Time: Day {world['day']}, {world['time_slot']}\n"
        f"- Weather: {world['weather']}\n"
        f"- Doom Level: {world['doom']}% ({world.get('doom_name', '위기')})\n"
        f"- **Atmosphere Context**: {party_context}\n"
        f"*Instruction: Adjust the narrative tone based on Time, Doom, and Party Condition (Injuries/Hostility).*"
    )