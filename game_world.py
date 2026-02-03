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
    system_prompt = f"""당신은 TRPG의 '이변(Anomaly) 생성기'입니다.
현재 세계 상태에 기반하여 **비일상적인** 이벤트를 생성하세요.

## 핵심 원칙
**이변은 좋고 나쁨이 없습니다.** 단지 '일상적이지 않은 현상'일 뿐입니다.
- 기회일 수도, 위험일 수도, 단순한 기이함일 수도 있습니다.
- 플레이어가 어떻게 반응하느냐에 따라 결과가 달라집니다.

## 현재 컨텍스트
### 세계관 요약
{lore_text}

### 현재 상황
- **위치**: {location}
- **활성 장르**: {', '.join(active_genres)}
- **세계 긴장도**: {doom_val}/100 ({tone_cat.upper()})
- **분위기 키워드**: {', '.join(tone_keywords)}

## 이변 생성 규칙

### 1. 카테고리 (category)
세계관에 맞는 이변 분류입니다. 다음 중 선택하거나 세계관에 맞게 생성:
추천: {', '.join(category_hints[:5])}

### 2. 태그 (tag)
한 단어로 된 이변의 정체. 세계관 용어를 사용하면 더 좋습니다.
- ✅ 좋은 예: [균열], [속삭임], [변이], [침묵], [그림자], [빛], [울림]
- ❌ 나쁜 예: [이상한 소리가 들림], [갑자기 어두워짐]

### 3. 설명 (description)
**중요**: 이변은 '판단'이 아닌 '현상'입니다.
- 2-3문장으로 생생하게 묘사
- 감각적 디테일 포함 (시각, 청각, 촉각, 냄새 등)
- **중립적 톤 유지**: "무섭다", "위험하다" 같은 판단 금지
- 현상 자체만 객관적으로 묘사

### 4. 효과 힌트 (effect_hint)
플레이어의 선택지나 가능한 반응에 대한 힌트.
- 예: "조사할 수 있다", "무시할 수도 있다", "기회일지도", "주의 필요"

### 5. 톤 조절 (세계 긴장도 기반)
- 낮은 긴장(~30%): 기묘함, 호기심을 자극하는 현상
- 중간 긴장(30~70%): 긴장감, 불확실성이 있는 현상
- 높은 긴장(70%+): 강렬함, 무시하기 어려운 현상

## 출력 형식 (JSON만 출력)
{{
  "category": "이변 분류 (영어)",
  "tag": "[한글 태그]",
  "tone": "Mystery/Surreal/Ominous/Eerie/Wonder/etc",
  "description": "이변에 대한 객관적 묘사...",
  "effect_hint": "플레이어 선택지 힌트",
  "nature": "neutral"
}}
"""

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

            # [Sanitize Tag] Clean up and format
            if "tag" in data:
                raw = data["tag"].replace("[", "").replace("]", "").strip()
                # Remove (...) parenthesis content
                raw = re.sub(r'\(.*?\)', '', raw).strip()
                # Remove excessive description (take only first meaningful part)
                # For Korean: 첫 번째 단어나 의미 단위만 취함
                if ' ' in raw:
                    # 한글이 포함된 경우 첫 단어만
                    words = raw.split()
                    # Skip English articles if mixed
                    if words[0].lower() in ["the", "a", "an"] and len(words) > 1:
                        raw = words[1]
                    else:
                        raw = words[0]

                # 너무 긴 태그 자르기 (최대 10자)
                if len(raw) > 10:
                    raw = raw[:10]

                # Check for empty result
                if not raw:
                    raw = "이변"

                data["tag"] = raw  # 대괄호 없이 저장 (표시할 때 추가)

            return data
            
    except Exception as e:
        logging.error(f"[Anomaly] Generation Failed: {e}")
        return None
