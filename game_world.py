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


def calculate_doom_increase(channel_id: str, world: Dict[str, Any]) -> Tuple[int, List[str]]:
    """
    [LEGACY] UNE DoomModule에 의해 대체되었습니다. 
    이 함수는 더 이상 비즈니스 로직을 수행하지 않으며, 점진적 마이그레이션을 위해 빈 결과를 반환할 수 있습니다.
    필요 시 DoomModule.process()를 호출하세요.
    """
    return 0, []

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

ANOMALY_TONE_MAP = {
    "low": ["Mystery", "Unease", "Curiosity"],
    "mid": ["Bizarre", "Surreal", "Tension", "Omen"],
    "high": ["Horror", "Disaster", "Fear", "Despair"]
}

def should_trigger_anomaly(doom_val: int) -> bool:
    """
    [LEGACY] UNE AnomalyModule에 의해 대체되었습니다. 
    로직 중복을 방지하기 위해 여기서는 항상 False 혹은 최소 확률만 반환합니다.
    """
    return False

def process_abnormal_turn(channel_id: str, context_tags: list) -> str:
    """
    턴 처리 시 호출되는 중앙 허브 함수 (The Hub).
    확률에 따라 비일상 이벤트를 '선 판정(Pre-calc)'하고, AI에게 묘사 지침(Directive)을 내립니다.
    """
    doom = domain_manager.get_world_state(channel_id).get("doom", 0)
    
    # 1. 확률 체크
    if not should_trigger_anomaly(doom):
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
                 
        ai_mem_world: Dict[str, Any] = p.get("ai_memory", {})
        mental_world: Dict[str, Any] = ai_mem_world.get("mental", {})
        start_mental = mental_world.get("value", 100)
        
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
    Returns Dict with keys: type, tag, category, description, effect_hint
    """
    if not client: return None

    tone_cat = _get_anomaly_tone(doom_val)
    tone_keywords = ANOMALY_TONE_MAP.get(tone_cat, ["Mystery"])

    # 장르별 이변 카테고리 힌트
    genre_category_hints = {
        "cosmic_horror": ["Void", "Entity", "Distortion", "Whisper", "Flesh"],
        "urban_fantasy": ["Spirit", "Curse", "Omen", "Awakening", "Breach"],
        "cyberpunk": ["Glitch", "Signal", "AI", "Virus", "Blackout"],
        "high_fantasy": ["Magic", "Beast", "Prophecy", "Ruin", "Divine"],
        "post_apocalypse": ["Mutation", "Storm", "Relic", "Swarm", "Collapse"],
        "noir": ["Shadow", "Paranoia", "Fate", "Secret", "Dread"],
        "wuxia": ["Qi", "Demon", "Heaven", "Fate", "Spirit"],
    }

    # 활성 장르에 맞는 카테고리 힌트 수집
    category_hints = []
    for genre in active_genres:
        if genre.lower() in genre_category_hints:
            category_hints.extend(genre_category_hints[genre.lower()])
    if not category_hints:
        category_hints = ["Unknown", "Strange", "Anomaly", "Phenomenon"]

    # Dynamic Prompt Construction - 한국어 중심, 세계관 맥락 강화
    system_prompt = f"""You are the 'Anomaly Generator' for a TRPG.
Generate an **extraordinary** event based on the current world state.

## Core Principles
**An anomaly is neither good nor bad.** It is simply an 'extraordinary phenomenon'.
- It can be an opportunity, a danger, or just a bizarre occurrence.
- The outcome depends on how the player reacts.

## Current Context
### World Lore Summary
{lore_text}

### Current Situation
- **Location**: {location}
- **Active Genres**: {', '.join(active_genres)}
- **World Tension (Doom)**: {doom_val}/100 ({tone_cat.upper()})
- **Atmosphere Keywords**: {', '.join(tone_keywords)}

## Anomaly Generation Rules
**IMPORTANT: All string values (tag, description, effect_hint) must be in KOREAN.**

### 1. category
A classification of the anomaly fitting the world. Select from or create one fitting the lore:
Recommended: {', '.join(category_hints[:5])}

### 2. tag
A one-word tag representing the identity of the anomaly (KOREAN). Using world-specific terminology is better.
- ✅ Good Examples: [균열], [속삭임], [변이], [침묵], [그림자], [빛], [울림]
- ❌ Bad Examples: [Hear a strange sound], [Suddenly gets dark]

### 3. description
**IMPORTANT**: An anomaly is a 'phenomenon', not a 'judgment'.
- Describe vividly in 2-3 sentences in KOREAN.
- Include sensory details (sight, sound, touch, smell, etc.).
- **Maintain Neutral Tone**: Avoid subjective judgments like "scary" or "dangerous".
- Describe the phenomenon objectively.

### 4. effect_hint
Hints about player choices or possible reactions in KOREAN.
- Examples: "조사할 수 있다", "무시할 수도 있다", "기회일지도", "주의 필요"

### 5. Tone Control (Based on World Tension)
- Low Tension (~30%): Mysterious, curiosity-inducing phenomena.
- Mid Tension (30~70%): Tense, uncertain phenomena.
- High Tension (70%+): Intense, difficult-to-ignore phenomena.

## Output Format (JSON Only)
{{
  "category": "Classification (English)",
  "tag": "[Korean Tag]",
  "tone": "Mystery/Surreal/Ominous/Eerie/Wonder/etc (English)",
  "description": "Objective description in KOREAN...",
  "effect_hint": "Player choice hint in KOREAN",
  "nature": "neutral"
}}"""

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

            # [Sanitize Tag] Robust cleaning to get a clean single word/tag
            if "tag" in data:
                raw = data["tag"]
                # 1. Remove brackets and common separators
                raw = raw.replace("[", "").replace("]", "").strip()
                
                # 2. Remove anything after a colon or hyphen/dash (often used for descriptions)
                raw = re.split(r'[:\-—]', raw)[0].strip()
                
                # 3. Remove (...) parenthesis content
                raw = re.sub(r'\(.*?\)', '', raw).strip()
                
                # 4. Extract first meaningful word/concept
                if ' ' in raw:
                    words = raw.split()
                    # Skip English articles
                    if words[0].lower() in ["the", "a", "an"] and len(words) > 1:
                        raw = words[1]
                    else:
                        raw = words[0]
                
                # 5. Remove lingering punctuation at the end (.,!?)
                raw = re.sub(r'[.,!?]$', '', raw).strip()

                # 6. Length limit (max 10 chars)
                if len(raw) > 10:
                    raw = raw[:10]

                # Final Fallback
                data["tag"] = raw if raw else "이변"

            return data
            
    except Exception as e:
        logging.error(f"[Anomaly] Generation Failed: {e}")
        return None
