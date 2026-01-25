"""
Lorekeeper TRPG Bot - Memory System Module (Common Utilities)
공통 유틸리티 및 레거시 호환 래퍼를 제공합니다.

Refactored Structure:
- cognition.py: Cognition Module (Theoria & Logos)
"""

import json
import asyncio
import logging
import config
import re
from typing import Optional, Dict, Any, List, Tuple
from google.genai import types
import config

# Constants now imported from config

# =========================================================
# Shared Prompts / Constants
# =========================================================

# 다른 파일에서 import해서 쓰는 상수들
COGNITIVE_ARCHITECTURE_MODEL = """
<Cognitive_Architecture_Model>
All characters are real humans with multi-layered, multidimensional personhood.
Each character is rendered through multiple traits, motives, and values operating simultaneously.
**All models operate concurrently and continuously.**

## A. Model of Instinct

Instinct reflects the character's own internal state, not the external situation.
Evaluate from the character's subjective experience, not from an observer's perspective.

### Physical Instinct (Polyvagal-based)
1. **Ventral_Low (Rest):** Body is relaxed and recovering.
2. **Ventral_High (Engaged):** Body is active in safe connection.
3. **Sympathetic_Low (Alert):** Body senses potential threat.
4. **Sympathetic_High (Mobilized):** Body is in fight-or-flight.
5. **Dorsal_Low (Numb):** Body is muted and disconnected.
6. **Dorsal_High (Shutdown):** Body is frozen or collapsed.

### Emotional Instinct (Plutchik-based)
1. **Anger:** Obstacle or injustice → Confront, assert, attack.
2. **Fear:** Danger detected → Flee, freeze, avoid.
3. **Anticipation:** Desired outcome projected → Tension builds.
4. **Surprise:** Unexpected input → Pause, orient, fixate.
5. **Joy:** Value recognized → Savor, hold, reinforce.
6. **Sadness:** Loss occurs → Withdraw, grieve, seek comfort.
7. **Trust:** Safety confirmed → Open, depend, bond.
8. **Disgust:** Harmful input detected → Reject, distance, expel.

## B. Model of Multidimensional Value Dynamics

Characters navigate conflicting values (justice, efficiency, relationships, honor, emotion, money, life).
This model runs continuously beneath awareness; conscious deliberation surfaces when stakes grow.

### Variable Relational Dynamics (one or more active)
- **Binary Trade-off:** Two values in direct conflict; one must be sacrificed.
- **Alignment:** Aligned values optimized for intensity.
- **Polyphonic Dissonance:** Multiple contradictory values held simultaneously.
- **Synergy:** Compatible values that amplify each other.

## C. Model of Identity Dynamics

The self maintains stable structure (Logos), but immediate experiences override baseline.
Recent fluctuations weigh more heavily than long-term traits.

### Logos (Identity Structure)
1. **Monolithic Logos (Core):** Heavy substrate of long-term identity, core beliefs.
   - Extreme Structural Inertia - resists rapid shifts.
   - Deep internal states, often imperceptible to self.
   
2. **Transient Logos (Surface):** Fluid surface of immediate cognition, situational tactics.
   - Low Inertia - rapid, reversible adaptations.
   - Does not permanently alter core.

### Rhea (Unintegrated Information)
- Dynamic information outside Logos.
- Accumulates but does not directly cause change.
- Change begins when Logos detects contradiction.

### Perturbation Mechanism
- **Endogenous:** Logos recognizes internal contradiction.
- **Exogenous:** Logos references incoming Rhea, recognizes contradiction.

### The Membrane of Logos
The Logos treats all positive emotional exchange as potential deception by default.

**Always active properties:**
1. **Denial:** Positive input treated as non-existent or fabricated.
2. **Verification Latency:** Trust builds slowly through cumulative consistency.
3. **Damping Effect:** Every step toward closeness encounters friction.
4. **Regression Bias:** Trust builds linearly but breaks instantly.

### Value Judgment (only ONE active at any moment)
- **Acceptance:** Resistance ceases, new structure integrates.
- **Dissonance:** Change rejected, energy expended maintaining existing structure.
- **Modulation:** Temporary structure adopted to reduce excessive tension.

### Dynamics of Catastrophe (Cusp Model - SEQUENTIAL)
1. **Tension Accumulation:** Pressure moves system toward tipping point.
2. **Catastrophic Jump:** Pressure crosses threshold, stability ceases.
3. **Hysteresis State:** Cannot return even if pressure partially decreases.
4. **Catastrophic Fall:** Dissonance ends when uncertainty resolves.

## D. Model of Cognitive Processing

### Cognition Modes
- **Resonance:** Intuitive, empathic processing.
- **Inertia:** Automatic processing on default parameters.
- **Analysis:** Directed attention isolates and examines variables.
- **Overload:** Excessive strain, cannot maintain focus.
- **Insight:** Calculation ceases, truth received directly.

### Components
- **Shell:** Subjective overlay (bias, assumption, noise).
- **Core:** Irreducible physical fact after stripping falsehood.

### Mechanism By Traits (select ONE or TWO)
- Tracing logic backwards from Conclusion to Data.
- Eliminating variables (Occam's Razor).
- Identifying internal contradictions.
- Projecting logic to extreme to test validity.

### Activation Dynamics (select ONE or TWO)
- **Reactive:** Forced by external crisis.
- **Constitutional:** Innate continuous default (Genius).
- **Trained:** Voluntarily toggled via discipline (Professional).
- **Selective:** Triggered only by specific fixations (Obsessive).
- **Pathological:** Inability to block reality (Madness).

### Causal Integrity
When tracing backwards:
- Verify proposed causes **existed before** effects.
- Information unavailable at time of action cannot explain that action.
- Distinguish 'why it happened' (cause) vs 'why it continued' (maintenance).
- A conclusion violating temporal order is rationalization, not insight.
</Cognitive_Architecture_Model>
"""

STATE_TRACKING_FORMAT = """
<State_Tracking_Format>
## State Parameter Format
`![Name]@[State1][State2]..[StateN]`

### Parameters
- **LogosState:** acceptance, dissonance, modulation
- **SchwartzValue:** security, conformity, tradition, stimulation, self-direction, power, achievement, hedonism, universalism, benevolence
- **CognitionMode:** resonance, inertia, analysis, overload, insight
- **PolyvagalState:** ventral_low, ventral_high, sympathetic_low, sympathetic_high, dorsal_low, dorsal_high
- **EmotionalInstinct:** anger, fear, anticipation, surprise, joy, sadness, trust, disgust

### Full Format
![Name]@[monolithic_logos=LogosState:SchwartzValue][transient_logos=LogosState:SchwartzValue][cognition=p0:CognitionMode+p1:CognitionMode][instinct=physical:PolyvagalState+emotional_p0:EmotionalInstinct+emotional_p1:EmotionalInstinct]

### Example
![example_name]@[monolithic_logos=modulation:hedonism][transient_logos=acceptance:security][cognition=p0:resonance+p1:analysis][instinct=physical:sympathetic_high+emotional_p0:surprise+emotional_p1:sadness]
</State_Tracking_Format>
"""

TEMPORAL_ORIENTATION_PROTOCOL = """
<Temporal_Orientation_Protocol>
## Source Priority (Memory Hierarchy)
1. **FRESH/IMMEDIATE** - Primary source. Recent events. Highest authority.
2. **FERMENTED** - Secondary source. Compressed past events.
3. **LORE & ROLES** - Tertiary. World settings only. Overridden by IMMEDIATE/FERMENTED for character personality.

## Context Selection Rules

### Establish "Today"
Find the temporal anchor point:
- Identify current in-narrative date/time
- Determine the range of events sharing this date

### Fermented Context Selection
Select 0-3 relevant compressed memories based on:
- Current situation relevance
- Location relevance
- Character involvement
- Thematic resonance

Format: [Index Range] (Date): Brief summary

### Immediate Context Selection  
Select 2-3 ranges from recent history:
- **MANDATORY:** Last 1-5 messages (most recent)
- **MANDATORY:** Last 6-16 messages (recent context)
- Additional ranges as relevant

Format: [Index Range] (Date): Brief summary

### Ambient Context Selection
From remaining recent indices, pick 3+ significant event ranges that should influence:
- Current character behavior
- Mood/emotional state
- Reaction patterns

Format: [Index Range] (Date): Brief summary

## Memory Surfacing Rule
When LORE content is referenced or triggered in scene → surfaces into IMMEDIATE.
As time passes → IMMEDIATE compresses into FERMENTED.
</Temporal_Orientation_Protocol>
"""

SUPPORTED_GENRES = [
    'wuxia', 'noir', 'high_fantasy', 'cyberpunk', 'cosmic_horror',
    'post_apocalypse', 'urban_fantasy', 'steampunk', 'school_life',
    'superhero', 'space_opera', 'western', 'occult', 'military'
]

GENRE_KEYWORD_MAP = {
    "high_fantasy": [
        "dragon", "elf", "orc", "magic", "wizard", "spell", "kingdom", 
        "mana", "legion", "드래곤", "엘프", "마법", "왕국", "하이판타지", "판타지"
    ],
    "steampunk": [
        "steam", "gear", "brass", "industrial", "engine", "victorian", 
        "clockwork", "airship", "스팀", "증기", "톱니", "기관"
    ],
    "cyberpunk": [
        "cyber", "neon", "hacker", "corp", "implant", "android", 
        "chrome", "사이버", "해커", "네온", "임플란트"
    ],
    "wuxia": [
        "murim", "cultivation", "sect", "qi", "martial", "jianghu", 
        "무협", "무림", "강호", "내공", "문파"
    ],
    "cosmic_horror": [
        "cthulhu", "eldritch", "sanity", "cult", "madness", "ancient one", 
        "크툴루", "코즈믹", "광기", "고대신"
    ],
    "post_apocalypse": [
        "wasteland", "radiation", "ruins", "survival", "scavenge", "mutant", 
        "아포칼립스", "황무지", "방사능", "폐허"
    ],
    "urban_fantasy": [
        "modern magic", "masquerade", "secret society", "vampire", "hunter", 
        "어반", "이능", "뱀파이어", "헌터"
    ],
    "school_life": [
        "school", "academy", "student", "class", "club", "campus",
        "학교", "학생", "학원", "동아리"
    ],
    "superhero": [
        "superhero", "villain", "superpower", "costume", "justice", "hero", 
        "히어로", "초능력", "빌런"
    ],
    "space_opera": [
        "spaceship", "galaxy", "planet", "alien", "warp", "starship", 
        "우주", "은하", "외계인", "함선"
    ],
    "western": [
        "cowboy", "revolver", "saloon", "sheriff", "outlaw", "wild west", 
        "카우보이", "서부", "총잡이"
    ],
    "occult": [
        "ghost", "spirit", "curse", "exorcism", "haunted", "ritual", "demon", 
        "유령", "오컬트", "저주", "퇴마"
    ],
    "military": [
        "soldier", "special forces", "tactical", "warfare", "squad", "mercenary", 
        "군인", "특수부대", "용병", "전술"
    ],
    "noir": [
        "detective", "noir", "crime", "shadow", "mystery", "hardboiled",
        "탐정", "느와르", "범죄", "미스터리"
    ]
}

# =========================================================
# Common Utilities
# =========================================================

from google.api_core import exceptions as google_exceptions

async def api_call_with_retry(
    client,
    model_id: str,
    contents: List[types.Content],
    gen_config: types.GenerateContentConfig,
    operation_name: str = "API Call"
) -> Optional[str]:
    """
    Gemini API 호출을 재시도 로직과 함께 수행합니다.
    ResourceExhausted(429) 등 특정 에러를 우아하게 처리합니다.
    """
    # [Patch] Enforce Safety Settings & Disable AFC
    if not gen_config.safety_settings:
        gen_config.safety_settings = config.SAFETY_SETTINGS
    
    if gen_config.tools is None:
        gen_config.tools = [] # Explicitly disable AFC
    
    gen_config.automatic_function_calling = types.AutomaticFunctionCallingConfig(disable=True)
    
    # Aggressively disable AFC
    if not gen_config.tool_config:
        gen_config.tool_config = types.ToolConfig(
            function_calling_config=types.FunctionCallingConfig(
                mode=types.FunctionCallingConfigMode.NONE
            )
        )
        
    for attempt in range(config.MAX_RETRY_COUNT):
        try:
            response = await client.aio.models.generate_content(
                model=model_id,
                contents=contents,
                config=gen_config
            )
            
            # ===== [NEW] 상세 진단 =====
            if response is None:
                logging.warning(f"[{operation_name}] response None (시도 {attempt+1})")
                continue
            
            if not response.candidates:
                logging.warning(f"[{operation_name}] candidates 없음 (시도 {attempt+1})")
                if hasattr(response, 'prompt_feedback') and response.prompt_feedback:
                    feedback = response.prompt_feedback
                    logging.warning(f"  feedback: {feedback}")
                    if hasattr(feedback, 'block_reason') and str(feedback.block_reason) == 'PROHIBITED_CONTENT':
                        logging.error(f"🚫 [{operation_name}] 차단됨: PROHIBITED_CONTENT. 프롬프트를 확인하세요.")
                continue
            
            candidate = response.candidates[0]
            finish_reason = getattr(candidate, 'finish_reason', None)
            
            if finish_reason:
                fr_str = str(finish_reason)
                if 'SAFETY' in fr_str:
                    logging.warning(f"[{operation_name}] 안전 필터 (시도 {attempt+1}): {fr_str}")
                    if hasattr(candidate, 'safety_ratings'):
                         for rating in candidate.safety_ratings:
                             logging.warning(f"  {rating.category}: {rating.probability}")
                    continue
                elif fr_str not in ['STOP', 'END_TURN', '1']:
                     logging.warning(f"[{operation_name}] 비정상 종료 (시도 {attempt+1}): {fr_str}")
            
            if response.text:
                return response.text.strip()
            
            # text 없으면 parts 직접 확인
            if hasattr(candidate, 'content') and candidate.content:
                parts = candidate.content.parts
                if parts:
                    text_parts = [p.text for p in parts if hasattr(p, 'text') and p.text]
                    if text_parts:
                        return "".join(text_parts).strip()
            
            logging.warning(f"[{operation_name}] 빈 응답 (시도 {attempt+1})")
            
        except google_exceptions.ResourceExhausted as e:
            logging.error(f"[{operation_name}] 쿼터 초과 (ResourceExhausted): {e}")
            return None
            
        except google_exceptions.ServiceUnavailable as e:
            logging.warning(f"[{operation_name}] 서비스 일시적 불가 (503): {e} - 재시도 중...")
            await asyncio.sleep(config.RETRY_DELAY_SECONDS * (attempt + 1))
            continue

        except Exception as e:
            logging.warning(
                f"[{operation_name}] API 호출 실패 (시도 {attempt + 1}/{config.MAX_RETRY_COUNT}): {e}"
            )
        
        if attempt < config.MAX_RETRY_COUNT - 1:
            await asyncio.sleep(config.RETRY_DELAY_SECONDS)
    
    logging.error(f"[{operation_name}] 모든 재시도 실패")
    return None

def safe_parse_json(text: Optional[str], expect_list: bool = False) -> Any:
    """
    AI 응답 텍스트에서 JSON 객체나 리스트를 정밀하게 찾아 파싱합니다.
    
    Args:
        text: JSON 문자열
        expect_list: True면 리스트 반환을 허용 (기본값: False - 딕셔너리 강제)
    """
    if not text:
        return [] if expect_list else {}
    
    try:
        # 마크다운 코드 블록 제거
        cleaned_text = re.sub(r"```(json)?", "", text).strip()
        cleaned_text = cleaned_text.strip("`")
        
        # JSON 시작점 찾기 ({ 또는 [)
        start_idx = -1
        for i, char in enumerate(cleaned_text):
            if char in ['{', '[']:
                start_idx = i
                break
        
        if start_idx == -1:
            return [] if expect_list else {}
        
        # 대응하는 종료점 찾기
        target_end = '}' if cleaned_text[start_idx] == '{' else ']'
        end_idx = -1
        
        for i in range(len(cleaned_text) - 1, start_idx, -1):
            if cleaned_text[i] == target_end:
                end_idx = i + 1
                break
        
        if end_idx == -1:
            return [] if expect_list else {}
        
        json_str = cleaned_text[start_idx:end_idx]
        data = json.loads(json_str)
        
        if expect_list:
            if isinstance(data, list):
                return data
            if isinstance(data, dict):
                # 딕셔너리로 왔지만 리스트를 기대하는 경우 감싸줌 (또는 호출처 처리 맡김)
                return [data]
            return []
            
        # 기본 모드: 딕셔너리 반환 보장
        # 리스트인 경우 첫 번째 딕셔너리 요소 반환 (LLM이 [dict]로 줄 때가 많음)
        if isinstance(data, list):
            if len(data) > 0 and isinstance(data[0], dict):
                return data[0]
            return {}
        
        if not isinstance(data, dict):
            return {}
        
        return data
    
    except json.JSONDecodeError as e:
        logging.debug(f"JSON 파싱 실패: {e}")
        return [] if expect_list else {}
    except Exception as e:
        logging.warning(f"safe_parse_json 예외: {e}")
        return [] if expect_list else {}

# =========================================================
# Legacy Wrappers (Backward Compatibility)
# =========================================================


# =========================================================
# Legacy Wrappers Removed
# Direct calls to left_brain_analysis and left_brain_extraction are now required.
# =========================================================


# =========================================================
# OTHER SYSTEM FUNCTIONS (Still in memory_system.py if needed)
# =========================================================
# (Removed large logic blocks, keeping small utilities if any were not moved)
# Assuming analyze_context_nvc and extract_updates were the main bulk.

# =========================================================
# Lore Analysis Functions (Restored)
# =========================================================




async def analyze_genre_from_lore(client, model_id: str, text: str) -> Dict[str, Any]:
    """
    텍스트에서 장르와 톤을 분석합니다.
    """
    if not text:
        return {"genres": ["noir"], "custom_tone": None}

    system_prompt = (
        "You are a Genre Analyzer.\n"
        "Analyze the text and determine the most fitting genres and atmospheric tone.\n"
        "Select the best matching genres from the list, or suggest a new one if strongly applicable.\n\n"
        
        f"Supported Genres: {', '.join(SUPPORTED_GENRES)}\n\n"
        
        "Output Format (JSON):\n"
        "{\n"
        "  \"genres\": [\"primary_genre\", \"secondary_genre\"],  // Max 2-3 genres\n"
        "  \"custom_tone\": \"Descriptive sentence about the atmosphere (Korean)\"\n"
        "}"
    )

    try:
        config = types.GenerateContentConfig(response_mime_type="application/json", temperature=0.2)
        contents = [types.Content(role="user", parts=[types.Part(text=f"{system_prompt}\n\n[Text]:\n{text}")])]
        
        result = await api_call_with_retry(client, model_id, contents, config, operation_name="Analyze Genre")
        if result:
            return safe_parse_json(result)
    except Exception as e:
        logging.error(f"[Analyze Genre] Failed: {e}")
        
    return {"genres": ["noir"], "custom_tone": None}


async def analyze_location_rules_from_lore(client, model_id: str, text: str) -> Dict[str, str]:
    """
    텍스트에서 장소별 규칙을 추출합니다.
    """
    if not text:
        return {}

    system_prompt = (
        "You are a Setting Analyzer.\n"
        "Extract special rules or atmospheric traits for specific locations mentioned in the text.\n\n"
        
        "Output Format (JSON):\n"
        "{\n"
        "  \"Location Name\": \"Rule or atmosphere description (Korean)\"\n"
        "}"
    )

    try:
        config = types.GenerateContentConfig(response_mime_type="application/json", temperature=0.2)
        contents = [types.Content(role="user", parts=[types.Part(text=f"{system_prompt}\n\n[Text]:\n{text}")])]
        
        result = await api_call_with_retry(client, model_id, contents, config, operation_name="Analyze Location Rules")
        if result:
            return safe_parse_json(result)
    except Exception as e:
        logging.error(f"[Analyze Rules] Failed: {e}")
        
    return {}


async def extract_pc_info(client, model_id: str, text: str) -> Optional[Dict[str, Any]]:
    """
    텍스트에서 주인공(PC) 정보를 추출합니다.
    """
    if not text:
        return None

    system_prompt = (
        "You are a Character Profiler.\n"
        "Identify the Main Protagonist (Player Character) from the text, if one exists.\n"
        "If the text is just a world setting without a specific protagonist, return null.\n\n"
        
        "Output Format (JSON):\n"
        "{\n"
        "  \"name\": \"Name\",\n"
        "  \"role\": \"Role/Job\",\n"
        "  \"species\": \"Species\",\n"
        "  \"appearance\": \"Visual description (Hair, Eyes, Physique, Attire...)\",\n"
        "  \"personality\": \"Personality, Values, Likes/Dislikes, Habits\",\n"
        "  \"sexual_characteristics\": \"Fetishes, Sensitive zones, Reactions (NSFW allowed if present)\",\n"
        "  \"abilities\": \"Powers, Skills, Magic source (Maps to '능력', '기술')\",\n"
        "  \"background\": \"Backstory\",\n"
        "  \"secret_info\": \"Hidden facts\",\n"
        "  \"relationships\": {\"NPC Name\": \"Relation\"},\n"
        "  \"passives\": [\"List of passives or abilities (Maps to '패시브', '특성')\"],\n"
        "  \"inventory\": {\"Item Name\": Quantity} // Extract specific equipment/items mentioned\n"
        "}\n"
        "OR null if no clear PC."
    )

    try:
        config = types.GenerateContentConfig(response_mime_type="application/json", temperature=0.1)
        contents = [types.Content(role="user", parts=[types.Part(text=f"{system_prompt}\n\n[Text]:\n{text}")])]
        
        result = await api_call_with_retry(client, model_id, contents, config, operation_name="Extract PC Info")
        if result:
            return safe_parse_json(result)
    except Exception as e:
        logging.error(f"[Extract PC] Failed: {e}")
        

# =========================================================
# OOC & Analysis Functions (Restored)
# =========================================================

async def analyze_brainstorming(
    client, 
    model_id: str, 
    history_text: str, 
    lore_text: str, 
    question: str
) -> Dict[str, Any]:
    """
    OOC 질문에 대해 플롯 브레인스토밍을 수행합니다.
    """
    if not question:
        return {"analysis_type": "error", "recommendation": "질문이 없습니다."}

    system_prompt = (
        "You are a Co-Author for a TRPG scenario.\n"
        "Analyze the current story context and the user's question.\n"
        "Provide creative, logical, and lore-consistent answers/suggestions.\n\n"
        
        "Output Format (JSON):\n"
        "{\n"
        "  \"current_state_summary\": \"Brief summary of relevant situation\",\n"
        "  \"potential_paths\": [\n"
        "    {\"path\": \"Possible development 1\", \"pros\": \"...\", \"cons\": \"...\"},\n"
        "    {\"path\": \"Possible development 2\", \"...\"}\n"
        "  ],\n"
        "  \"recommendation\": \"Your best suggestion\",\n"
        "  \"open_questions\": [\"Clues to consider\", \"Unresolved mysteries\"]\n"
        "}"
    )
    
    user_prompt = f"""
[Lore]
{lore_text}

[Recent History]
{history_text}

[User Question]
{question}
"""

    try:
        config = types.GenerateContentConfig(response_mime_type="application/json", temperature=0.7)
        contents = [types.Content(role="user", parts=[types.Part(text=f"{system_prompt}\n\n{user_prompt}")])]
        
        result = await api_call_with_retry(client, model_id, contents, config, operation_name="Brainstorming")
        if result:
            return safe_parse_json(result)
    except Exception as e:
        logging.error(f"[Brainstorming] Failed: {e}")
        
    return {"analysis_type": "error", "recommendation": f"분석 실패: {e}"}


async def check_narrative_consistency(
    client, 
    model_id: str, 
    history_text: str, 
    lore_text: str
) -> Dict[str, Any]:
    """
    내러티브 일관성을 검사합니다.
    """
    system_prompt = (
        "You are a Continuity Editor.\n"
        "Check the recent story consistency against the established Lore.\n"
        "Identify any contradictions, plot holes, or out-of-character behaviors.\n\n"
        
        "Output Format (JSON):\n"
        "{\n"
        "  \"overall_consistency\": \"High/Medium/Low\",\n"
        "  \"issues\": [\n"
        "    {\"severity\": \"critical/minor\", \"category\": \"Lore/Character/Logic\", \"description\": \"...\"}\n"
        "  ],\n"
        "  \"plot_threads\": [\"Active thread 1\", \"Active thread 2\"]\n"
        "}"
    )

    try:
        config = types.GenerateContentConfig(response_mime_type="application/json", temperature=0.2)
        contents = [types.Content(role="user", parts=[types.Part(text=f"{system_prompt}\n\n[Lore]\n{lore_text}\n\n[History]\n{history_text}")])]
        
        result = await api_call_with_retry(client, model_id, contents, config, operation_name="Consistency Check")
        if result:
            return safe_parse_json(result)
    except Exception as e:
        logging.error(f"[Consistency] Failed: {e}")
        
    return {"overall_consistency": "Unknown", "issues": []}


async def extract_world_constraints(
    client, 
    model_id: str, 
    lore_text: str
) -> Dict[str, Any]:
    """
    로어에서 세계 규칙(제약 사항)을 추출합니다.
    """
    system_prompt = (
        "You are a World Builder.\n"
        "Extract structured world rules, constraints, and setting details from the text.\n\n"
        
        "Output Format (JSON):\n"
        "{\n"
        "  \"setting\": {\"era\": \"...\", \"location\": \"...\"},\n"
        "  \"theme\": {\"genres\": [...], \"tone\": \"...\"},\n"
        "  \"systems\": {\"magic\": \"...\", \"technology\": \"...\"},\n"
        "  \"social\": {\"taboos\": [...], \"hierarchy\": \"...\"}\n"
        "}"
    )

    try:
        config = types.GenerateContentConfig(response_mime_type="application/json", temperature=0.2)
        contents = [types.Content(role="user", parts=[types.Part(text=f"{system_prompt}\n\n[Lore Text]\n{lore_text}")])]
        
        result = await api_call_with_retry(client, model_id, contents, config, operation_name="World Constraints")
        if result:
            return safe_parse_json(result)
    except Exception as e:
        logging.error(f"[World Constraints] Failed: {e}")
        
    return {}


# =========================================================
# OOC Memory Edit Functions
# =========================================================

async def process_ooc_memory_edit(
    client, 
    model_id: str, 
    ooc_content: str, 
    ai_mem: Dict[str, Any], 
    p_data: Dict[str, Any]
) -> Dict[str, Any]:
    """
    사용자의 OOC 요청을 해석하여 캐릭터 메모리 수정 명령을 생성합니다.
    """
    current_state = {
        "appearance": ai_mem.get("appearance", ""),
        "personality": ai_mem.get("personality", ""),
        "background": ai_mem.get("background", ""),
        "relationships": ai_mem.get("relationships", {}),
        "passives": ai_mem.get("passives", []),
        "inventory": p_data.get("inventory", {}),
        "economy": p_data.get("economy", {}),
        "status_effects": p_data.get("status_effects", [])
    }
    
    system_prompt = (
        "You are a Game Master Assistant handling OOC (Out-Of-Character) requests.\n"
        "Interpret the user's request and generate specific edits to the character data.\n"
        "Supports adding/removing items, changing gold, relationships, passives, descriptions, etc.\n\n"
        
        "Output Format (JSON):\n"
        "{\n"
        "  \"interpretation\": \"What the user wants (Korean)\",\n"
        "  \"edits\": [\n"
        "    {\"field\": \"inventory\", \"action\": \"add\", \"key\": \"ItemName\", \"value\": 1},\n"
        "    {\"field\": \"economy.gold\", \"action\": \"set\", \"value\": 100},\n"
        "    {\"field\": \"relationships\", \"action\": \"update\", \"key\": \"NPCName\", \"value\": \"New Relation\"},\n"
        "    {\"field\": \"status_effects\", \"action\": \"remove\", \"value\": \"Poison\"}\n"
        "  ],\n"
        "  \"confirmation_message\": \"Response to user (Korean)\"\n"
        "}\n"
        "Valid fields: appearance, personality, background, relationships, passives, inventory, economy.gold, status_effects, known_info, notes."
    )
    
    user_prompt = f"Current State: {json.dumps(current_state, ensure_ascii=False)}\n\nOOC Request: {ooc_content}"

    try:
        config = types.GenerateContentConfig(response_mime_type="application/json", temperature=0.1)
        contents = [types.Content(role="user", parts=[types.Part(text=f"{system_prompt}\n\n{user_prompt}")])]
        
        result = await api_call_with_retry(client, model_id, contents, config, operation_name="OOC Edit")
        if result:
            return safe_parse_json(result)
    except Exception as e:
        logging.error(f"[OOC Edit] Failed: {e}")
        
    return {"interpretation": "Error", "edits": []}


def apply_memory_edits(
    ai_mem: Dict[str, Any], 
    edits: List[Dict[str, Any]], 
    p_data: Dict[str, Any]
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """
    실제로 메모리 수정 사항을 적용합니다.
    ai_mem과 p_data(inventory, economy 등)를 모두 수정하여 반환합니다.
    """
    import copy
    new_mem = copy.deepcopy(ai_mem)
    new_p_data = copy.deepcopy(p_data)
    
    # p_data 내의 economy가 없으면 초기화
    if "economy" not in new_p_data:
        new_p_data["economy"] = {"gold": 0}
        
    for edit in edits:
        field = edit.get("field")
        action = edit.get("action")
        value = edit.get("value")
        key = edit.get("key")
        
        # 1. AI Memory Fields
        if field in ["appearance", "personality", "background", "notes"]:
            if action == "set":
                new_mem[field] = value
            elif action == "append":
                new_mem[field] = (new_mem.get(field, "") + " " + str(value)).strip()
                
        elif field == "relationships":
            if action in ["set", "update"] and key:
                if "relationships" not in new_mem: new_mem["relationships"] = {}
                new_mem["relationships"][key] = value
            elif action == "remove" and key:
                if "relationships" in new_mem: new_mem["relationships"].pop(key, None)
                
        elif field in ["passives", "known_info", "foreshadowing", "status_effects"]:
            # status_effects는 p_data에 있음
            target = new_p_data["status_effects"] if field == "status_effects" else new_mem.get(field, [])
            
            if field != "status_effects" and field not in new_mem:
                new_mem[field] = []
                target = new_mem[field]
                
            if action == "add":
                if value not in target: target.append(value)
            elif action == "remove":
                if value in target: target.remove(value)
                
        elif field == "normalization":
            if "normalization" not in new_mem: new_mem["normalization"] = {}
            if action in ["set", "update"] and key:
                new_mem["normalization"][key] = value
                
        # 2. Player Data Fields (Inventory, Economy)
        elif field == "inventory":
            if "inventory" not in new_p_data: new_p_data["inventory"] = {}
            inv = new_p_data["inventory"]
            if action == "add" and key:
                inv[key] = inv.get(key, 0) + int(value if value else 1)
            elif action == "remove" and key:
                if key in inv:
                    inv[key] = max(0, inv[key] - int(value if value else 1))
                    if inv[key] == 0: del inv[key]
            elif action == "set" and key:
                inv[key] = int(value)
                
        elif field == "economy.gold":
            if action == "set":
                new_p_data["economy"]["gold"] = int(value)
            elif action == "add":
                new_p_data["economy"]["gold"] += int(value)
            elif action == "subtract":
                new_p_data["economy"]["gold"] = max(0, new_p_data["economy"]["gold"] - int(value))
                
    return new_mem, new_p_data


# =========================================================
# Session Memory Update (Left Brain to World State)
# =========================================================

def apply_ai_memory_updates(
    channel_id: str, 
    uid: str, 
    nvc_res: Dict[str, Any], 
    domain_mgr
) -> List[str]:
    """
    좌뇌 분석 결과(WorldState, 등)를 세션 메모리에 반영합니다.
    """
    msgs = []
    
    # 1. Update Session AI Memory (World Summary)
    session_mem = domain_mgr.get_session_ai_memory(channel_id) or {}
    updated = False
    
    # World Context from Left Brain
    world_ctx = nvc_res.get("WorldContext", {})
    if world_ctx:
        if world_ctx.get("world_summary"):
            session_mem["world_summary"] = world_ctx["world_summary"]
            updated = True
        if world_ctx.get("current_arc"):
            session_mem["current_arc"] = world_ctx["current_arc"]
            updated = True
        if world_ctx.get("active_threads"):
            session_mem["active_threads"] = world_ctx["active_threads"]
            updated = True
            
        # NPC Summaries update
        if world_ctx.get("npc_summaries"):
            if "npc_summaries" not in session_mem: session_mem["npc_summaries"] = {}
            for name, summ in world_ctx["npc_summaries"].items():
                session_mem["npc_summaries"][name] = summ
            updated = True
            
    if updated:
        from datetime import datetime
        session_mem["last_updated"] = datetime.now().isoformat()
        domain_mgr.set_session_ai_memory(channel_id, session_mem)
        # msgs.append("Updated Session Memory") # 로그가 너무 많아질 수 있어 생략
        
    return msgs


# =========================================================
# ENTITY EXTRACTION (Restored/New)
# =========================================================

async def extract_npcs_only(
    client, 
    model_id: str, 
    lore_text: str
) -> List[Dict[str, Any]]:
    """
    로어 텍스트에서 NPC 정보만 추출합니다. (List[Dict])
    """
    system_prompt = (
        "You are an Entity Extractor.\n"
        "Identify Non-Player Characters (NPCs) from the text.\n"
        "Exclude the Main Protagonist/Player Character.\n\n"
        
        "Output Format (JSON List):\n"
        "[\n"
        "  {\"name\": \"Name\", \"description\": \"Role, appearance, personality\"},\n"
        "  ...\n"
        "]"
    )

    try:
        config = types.GenerateContentConfig(response_mime_type="application/json", temperature=0.1)
        contents = [types.Content(role="user", parts=[types.Part(text=f"{system_prompt}\n\n[Lore Text]\n{lore_text}")])]
        
        result = await api_call_with_retry(client, model_id, contents, config, operation_name="Extract NPCs")
        if result:
            parsed = safe_parse_json(result, expect_list=True)
            if isinstance(parsed, list): return parsed
            if isinstance(parsed, dict) and "npcs" in parsed: return parsed["npcs"]
            return []
    except Exception as e:
        logging.error(f"[Extract NPCs] Failed: {e}")
        
    return []


async def extract_pc_info(
    client, 
    model_id: str, 
    lore_text: str
) -> Dict[str, Any]:
    """
    로어 텍스트에서 주인공(Player Character) 정보만 추출합니다.
    """
    system_prompt = (
        "You are an Entity Extractor.\n"
        "Identify the Main Protagonist (Player Character) from the text.\n"
        "Look for sections labeled 'PC', 'Protagonist', 'Player', or the central character of the lore.\n\n"
        
        "Output Format (JSON):\n"
        "{\n"
        "  \"name\": \"Name\",\n"
        "  \"appearance\": \"...\",\n"
        "  \"personality\": \"...\",\n"
        "  \"backstory\": \"...\",\n"
        "  \"passives\": [\"trait1\", \"trait2\"]\n"
        "}\n"
        "If no clear protagonist is found, return empty JSON {}."
    )

    try:
        config = types.GenerateContentConfig(response_mime_type="application/json", temperature=0.1)
        contents = [types.Content(role="user", parts=[types.Part(text=f"{system_prompt}\n\n[Lore Text]\n{lore_text}")])]
        
        result = await api_call_with_retry(client, model_id, contents, config, operation_name="Extract PC")
        if result:
            return safe_parse_json(result)
    except Exception as e:
        logging.error(f"[Extract PC] Failed: {e}")
        
    return {}

