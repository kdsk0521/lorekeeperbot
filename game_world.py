"""
Lorekeeper TRPG Bot - Game World Module
Handles World State, Time Flow, Weather, and Doom mechanics.
Extracted from game_system.py
"""

import logging
import random
import time
import json
import re
from typing import List, Tuple, Dict, Any, Optional
from google.genai import types

import config
import domain_manager
import bot_utils

# =========================================================
# WORLD TIME & WEATHER
# =========================================================

def get_time_slots(channel_id: str) -> List[str]:
    return config.DEFAULT_TIME_SLOTS

def get_weather_types(channel_id: str) -> List[str]:
    return config.DEFAULT_WEATHER_TYPES

def advance_time(channel_id: str) -> str:
    """시간을 다음 슬롯으로 진행하고 세계 변화를 반환"""
    world = domain_manager.get_world_state(channel_id)
    time_slots = get_time_slots(channel_id)
    
    current_slot = world.get("time_slot", "오후")
    try:
        current_idx = time_slots.index(current_slot)
    except ValueError:
        current_idx = 2 # Default to Afternoon

    # Time Tick Duration (7-10 mins) handling could be done here if we tracked real time, 
    # but this function advances the *slot*. The "Tick" logic usually calls this.
    # For now, we just advance the slot.

    next_idx = current_idx + 1
    
    # 이모지 매핑
    time_emoji = {
        "새벽": "🌅", "오전": "☀️", "오후": "🌤️",
        "황혼": "🌆", "저녁": "🌙", "심야": "🌑"
    }
    
    msg = ""
    
    if next_idx >= len(time_slots):
        # 날짜 변경
        world["time_slot"] = time_slots[0]
        world["day"] = world.get("day", 1) + 1
        new_weather = random.choice(get_weather_types(channel_id))
        world["weather"] = new_weather
        
        emoji = time_emoji.get(time_slots[0], "🌅")
        msg = (
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"🌙 **밤이 지나고...**\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"📅 **{world['day']}일차** {emoji} **{time_slots[0]}**\n"
            f"🌤️ 날씨: {new_weather}\n"
            f"━━━━━━━━━━━━━━━━━━━━"
        )
    else:
        world["time_slot"] = time_slots[next_idx]
        emoji = time_emoji.get(time_slots[next_idx], "⏰")
        
        # 시간대별 분위기 메시지
        atmosphere = {
            "새벽": "동이 트기 시작합니다...",
            "오전": "아침 햇살이 비춥니다.",
            "오후": "태양이 중천에 떠 있습니다.",
            "황혼": "해가 저물어갑니다...",
            "저녁": "어둠이 내려앉습니다.",
            "심야": "깊은 밤이 찾아왔습니다..."
        }
        atm = atmosphere.get(time_slots[next_idx], "")
        
        msg = f"{emoji} **{time_slots[next_idx]}** — {atm}"
    
    domain_manager.update_world_state(channel_id, world)
    return msg

# =========================================================
# DOOM SYSTEM
# =========================================================

def change_doom(channel_id: str, amount: int) -> str:
    """
    위기 수치를 조정하고 메시지를 반환합니다.
    """
    world = domain_manager.get_world_state(channel_id)
    old_val = world.get("doom", 0)
    new_val = max(0, min(100, old_val + amount))
    
    if old_val == new_val:
        return "" # No change
        
    world["doom"] = new_val
    domain_manager.update_world_state(channel_id, world)
    
    # Emoji feedback
    emoji = "📈" if amount > 0 else "📉"
    
    # Check Thresholds
    diff_msg = ""
    # Critical Transition
    if old_val < config.DOOM_THRESHOLD_CRITICAL <= new_val:
        diff_msg = "\n⚠️ **[경고] 파멸이 임박했습니다!**"
    elif old_val < config.DOOM_THRESHOLD_DANGER <= new_val:
        diff_msg = "\n⚠️ **[주의] 위험도가 상승했습니다.**"
        
    return f"{emoji} **위기 수치:** {old_val}% → **{new_val}%** {diff_msg}"


def calculate_doom_increase(channel_id: str, world: dict) -> Tuple[int, List[str]]:
    doom_increase = 0
    doom_reasons = []
    
    # 1. Time Check (Night logic)
    time_slots = get_time_slots(channel_id)
    current_slot = world.get("time_slot", "오후")
    try:
        idx = time_slots.index(current_slot)
    except ValueError:
        idx = 2

    is_night_time = idx >= len(time_slots) - 2 # "저녁", "심야" or "황혼" onwards
    if "황혼" in current_slot:
        is_night_time = True
    
    if is_night_time:
        doom_increase += config.DOOM_INCREASE_NIGHT
    
    # [V6.1] Rubber-banding Up (Entropy Check)
    current_doom = world.get("doom", 0)
    if current_doom < config.DOOM_FLOOR:
        doom_increase += config.DOOM_FLOOR_RECOVERY
        doom_reasons.append(f"🌌 세계의 엔트로피 (수치 {config.DOOM_FLOOR}% 미만 보정)")
    
    # 2. Nemesis Check
    domain = domain_manager.get_domain(channel_id)
    participants = domain.get("participants", {})
    nemesis_detected = False
    for uid, p in participants.items():
        if p.get("status") == "left": continue
        ai_mem = p.get("ai_memory", {})
        rels = ai_mem.get("relationships", {})
        for npc_name, score in rels.items():
            try:
                score_val = int(score)
            except (ValueError, TypeError):
                continue # Skip invalid scores
                
            if score_val <= config.NEMESIS_THRESHOLD:
                nemesis_detected = True; break
        if nemesis_detected: break
    
    if nemesis_detected:
        doom_increase += random.randint(config.DOOM_INCREASE_NEMESIS_MIN, config.DOOM_INCREASE_NEMESIS_MAX)
        doom_reasons.append("👿 적대 세력")
    
    
    # 3. AI Risk Level
    ai_risk = world.get("risk_level", "None").lower()
    location = world.get("current_location", "Unknown")
    
    if "high" in ai_risk or "extreme" in ai_risk:
        doom_increase += config.DOOM_INCREASE_HIGH_RISK
        doom_reasons.append(f"💀 위험 지역({location}): 고위험 감지")
    elif "medium" in ai_risk:
        doom_increase += config.DOOM_INCREASE_MEDIUM_RISK
        doom_reasons.append(f"⚠️ 위험 지역({location}): 주의 필요")

    # 4. Participant Status Severity (Restored V2 Feature)
    import game_character
    participants = domain.get("participants", {})
    for uid, p in participants.items():
        if p.get("status") != "active": continue
        
        severity_doom, sev_reasons = game_character.calculate_status_doom_contribution(p)
        if severity_doom > 0:
            doom_increase += severity_doom
            p_name = p.get("mask", "Unknown")
            doom_reasons.append(f"🩸 {p_name}: {', '.join(sev_reasons)}")
        
    # 4. Lore Rules
    loc_rules = world.get("location_rules", {})
    for loc_name, rule in loc_rules.items():
        if loc_name.lower() in location.lower():
            condition = rule.get("condition", "").lower()
            should_apply = False
            if "night" in condition and is_night_time: should_apply = True
            elif "always" in condition: should_apply = True
            
            if should_apply and "high" not in ai_risk:
                doom_increase += config.DOOM_INCREASE_LORE_RULE
                doom_reasons.append(f"📜 로어 규칙({loc_name})")
                
    # [V6.2] Item 7: Adaptive Calm (Mitigate based on party experience)
    if doom_increase > 0:
        total_adapt = 0
        p_count = 0
        import game_character
        for uid, p in participants.items():
            if p.get("status") == "active":
                exp_data = p.get("abnormal_exposure", {})
                for tag, data in exp_data.items():
                    total_adapt += game_character.calculate_adaptation_percentage(data.get("count", 0))
                p_count += 1
        
        if p_count > 0:
            avg_adapt = total_adapt / p_count
            if avg_adapt >= 50: # High average adaptation
                mitigation = 1 if avg_adapt < 80 else 2
                before = doom_increase
                doom_increase = max(0, doom_increase - mitigation)
                if before > doom_increase:
                    doom_reasons.append(f"🛡️ 적응형 평화 (-{mitigation})")

    return doom_increase, doom_reasons

def reduce_doom(channel_id: str, amount: int, reason: str = "") -> str:
    """Doom 수치 감소 (최소 0)"""
    return change_doom(channel_id, -amount)

def get_doom_info(value: int) -> Dict[str, Any]:
    for stage_id, info in config.DOOM_STAGES.items():
        low, high = info["range"]
        if low <= value < high:
            return info
    return config.DOOM_STAGES[5] # Default to Max

def _get_doom_description(doom: int) -> str:
    # Wrapper for legacy compatibility if needed, or internal use
    info = get_doom_info(doom)
    return f"{info['emoji']} {info['name']}"

def process_doom_tick(channel_id: str) -> Optional[str]:
    """매 5틱 또는 특정 주기마다 실행되는 둠 계산 및 적용"""
    world = domain_manager.get_world_state(channel_id)
    inc, reasons = calculate_doom_increase(channel_id, world)
    
    if inc > 0:
        fb = change_doom(channel_id, inc)
        if fb:
            reason_text = "\n".join([f"• {r}" for r in reasons])
            return f"{fb}\n{reason_text}"
    return None


def get_world_context(channel_id: str) -> str:
    world = domain_manager.get_world_state(channel_id)
    if not world: return ""
    
    party_context = domain_manager.get_party_status_context(channel_id)
    location = world.get("current_location", "Unknown")
    
    return (
        f"[현재 세계 상태]\n"
        f"- 위치: {location}\n"
        f"- 위험도: {world.get('risk_level', 'None')}\n"
        f"- 시간: {world.get('day', 1)}일차, {world.get('time_slot', '오후')}\n"
        f"- 날씨: {world.get('weather', '맑음')}\n"
        f"- 위기 수치: {world.get('doom', 0)}% ({_get_doom_description(world.get('doom', 0))})\n"
        f"- **파티 분위기**: {party_context}\n"
        f"*지침: 이 위치, 시간, 위기 수치, 파티 상태를 반영하여 서술 톤을 조절하십시오.*"
    )

def _get_doom_bar(value: int, length: int = 10) -> str:
    # [████░░░░░░]
    fill = int(value / config.DOOM_MAX * length)
    bar = "█" * fill + "░" * (length - fill)
    return f"[{bar}]"

def get_doom_forecast(channel_id: str) -> str:
    world = domain_manager.get_world_state(channel_id)
    current = world.get("doom", 0)
    info = get_doom_info(current)
    
    # Hide Numbers, Show Bar + Description
    bar = _get_doom_bar(current)
    
    msg = f"🛡️ **위기 예보**\n{bar} {info['emoji']} **{info['name']}**\n"
    
    if current >= config.DOOM_THRESHOLD_CRITICAL:
        msg += "⚠️ **경고:** 파멸이 임박했습니다. 모든 행동에 위험이 따릅니다."
    elif current >= config.DOOM_THRESHOLD_DANGER:
        msg += "⚠️ **주의:** 세계의 적의가 느껴집니다."
    else:
        msg += "✅ 아직은 안전합니다."
        
    return msg

# =========================================================
# V7: ABNORMAL SYSTEM HUB (Pre-calculation)
# =========================================================

def process_abnormal_turn(channel_id: str, context_tags: list) -> str:
    """
    턴 처리 시 호출되는 중앙 허브 함수 (The Hub).
    확률에 따라 비일상 이벤트를 '선 판정(Pre-calc)'하고, AI에게 묘사 지침(Directive)을 내립니다.
    """
    doom = domain_manager.get_world_state(channel_id).get("doom", 0)
    
    # 1. 확률 체크
    # Prob = max(10, doom * 0.5)
    prob = max(config.ABNORMAL_MIN_PROB, doom * config.ABNORMAL_DOOM_COEFF)
    
    if random.randint(1, 100) > prob:
        return "" # No event
        
    # 2. 태그 및 강도 선정 (Doom Based)
    intensity = "Low"
    tone_keyword = "Miracle/Fortune"
    
    if doom > 70:
        intensity = "High"
        tone_keyword = "Horror/Disaster"
    elif doom > 30:
        intensity = "Mid"
        tone_keyword = "Mystery/Bizarre"
        
    # Tag Selection: Use context (e.g. current location tags) or "Unknown"
    # In V7, context_tags should be passed from main loop (e.g. location logic).
    # If empty, we can use a generic fallback.
    tag = random.choice(context_tags) if context_tags else "Unknown"
    
    # 3. 미리 계산 (Pre-calculation) & 적용
    results = []
    import game_character
    
    # V7 Active Participants
    participants = domain_manager.get_active_participants(channel_id)
    
    for uid, p in participants.items():
        # Apply Impact (Update Mental/Adapt in DB immediately)
        # We assume doom_stage based on doom value
        doom_info = get_doom_info(doom)
        # Extract stage ID from range loop? Or just trust tone logic?
        # game_character doesn't need stage ID, just intensity/value?
        # Actually update_mental uses doom to penalty recovery. But here we deal damage.
        
        # We need to map doom *value* to a stage *index* if game_character needs it.
        # But apply_abnormal_impact takes (tag, intensity, doom_stage).
        # Let's verify game_character signature I wrote: `apply_abnormal_impact(user_data, tag, intensity, doom_stage)`
        
        # Calculate Stage Index
        current_doom_stage = 0
        for sid, info in config.DOOM_STAGES.items():
            l, h = info["range"]
            if l <= doom < h:
                 current_doom_stage = sid
                 break
                 
        start_mental = p.get("ai_memory", {}).get("mental", {}).get("value", 100)
        
        # EXECUTE LOGIC
        res_str, new_adapt = game_character.apply_abnormal_impact(p, tag, intensity, current_doom_stage)
        
        domain_manager.save_participant_data(channel_id, uid, p) # Save changes
        
        results.append(f"- {p['mask']}: {res_str}")
        
    # 4. Directive 생성 (Return to AI)
    # The Directive tells AI *what happened* so it can describe it.
    directive = (
        f"\n[SYSTEM EVENT: Abnormal Phenomenon '{tag}' occurred!]\n"
        f"- Intensity: {intensity} ({tone_keyword})\n"
        f"- Outcomes:\n" + "\n".join(results) + "\n"
        f"- Instruction: Describe this event naturally based on the outcomes. Focus on the sensory details and characters' reactions."
    )
    
    return directive
def _get_anomaly_tone(doom_val: int) -> str:
    """Selects a tone category based on Doom value."""
    if doom_val <= 30: return "low"
    elif doom_val <= 70: return "mid"
    else: return "high"

async def generate_anomaly_event(
    client, 
    channel_id: str,
    doom_val: int, 
    lore_text: str, 
    location: str,
    active_genres: list,
    model_id: str = config.MODEL_ID_FLASH
) -> Optional[Dict[str, Any]]:
    """
    Generates an Anomaly Event using AI.
    Returns Dict with keys: type, tag, description, effect_hint
    """
    if not client: return None

    tone_cat = _get_anomaly_tone(doom_val)
    tone_keywords = ANOMALY_TONE_MAP.get(tone_cat, ["Mystery"])
    
    # Dynamic Prompt Construction
    system_prompt = (
        "You are a 'Random Event Generator' for a TRPG.\n"
        "Generate a brief, atmospheric event based on the current World State and Doom Level.\n\n"
        
        f"### Current Context\n"
        f"- World Lore Summary: {lore_text}\n"
        f"- Location: {location}\n"
        f"- Helper Genres: {', '.join(active_genres)}\n"
        f"- Doom Level: {doom_val}/100 ({tone_cat.upper()} Tension)\n"
        f"- Target Tone: {', '.join(tone_keywords)} (Pick one that fits)\n\n"
        
        "### Instructions\n"
        "1. **Tag**: ONE WORD Category ONLY. (e.g., [Machine], [Ghost], [Bio], [Psychic], [Magic]).\n"
        "   - ❌ BAD: [Ghost Signal detected], [Strange Echo], [Red Light]\n"
        "   - ✅ GOOD: [Ghost], [Sound], [Light]\n"
        "   - **CRITICAL**: Do NOT describe the event in the tag. Classify it.\n"
        "2. **Description**: 1-2 sentences describing the event. vivid and sensory.\n"
        "3. **Effect Hint**: A short hint on what happens (e.g., 'Mental Check', 'Gain Item', 'Social Encounter').\n"
        "4. **Language**: **Tag MUST be in ENGLISH** (e.g. [Soul], [Bio]). Description/Hint MUST be in KOREAN.\n\n"

        "### Output Format (JSON Only)\n"
        "{\n"
        "  \"tag\": \"[TagName]\",\n"
        "  \"tone\": \"Horror/Romance/Comedy/etc\",\n"
        "  \"description\": \"The event description...\",\n"
        "  \"effect_hint\": \"What player should do or feel\"\n"
        "}"
    )

    try:
        gen_config = types.GenerateContentConfig(response_mime_type="application/json", temperature=0.7)
        contents = [types.Content(role="user", parts=[types.Part(text=system_prompt)])]
        
        response = await client.aio.models.generate_content(
            model=model_id,
            contents=contents,
            config=gen_config
        )
        
        if response.text:
            cleaned = bot_utils.clean_json_text(response.text)
            data = json.loads(cleaned)
            
            # [Sanitize Tag] Force Single Word Format
            if "tag" in data:
                raw = data["tag"].replace("[", "").replace("]", "").strip()
                # Remove (...) parenthesis content
                raw = re.sub(r'\(.*?\)', '', raw).strip()
                # Take first word only (skip articles 'The', 'A', 'An')
                words = raw.split()
                if words:
                    if words[0].lower() in ["the", "a", "an"] and len(words) > 1:
                        raw = words[1]
                    else:
                        raw = words[0]
                
                # Check for empty result
                if not raw: raw = "Unknown"
                
                data["tag"] = f"[{raw}]"
                
            return data
            
    except Exception as e:
        logging.error(f"[Anomaly] Generation Failed: {e}")
        return None
