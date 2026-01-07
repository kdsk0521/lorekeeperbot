"""
Lorekeeper TRPG Bot - Memory System Module (Left Hemisphere)
논리, 분석, 인과율 계산을 담당하는 '좌뇌' 모듈입니다.

Architecture:
    ┌─────────────────────────────────────────────────────────┐
    │                    THEORIA SYSTEM                       │
    ├─────────────────────────────────────────────────────────┤
    │  LEFT HEMISPHERE (memory_system.py) - This Module       │
    │  ─────────────────────────────────────────────────────  │
    │  • Logic Core: Analyzes causality and physics           │
    │  • Context Analysis: Extracts objective facts           │
    │  • Lore Compression: Summarizes world data              │
    │  • Genre Detection: Identifies narrative genres         │
    │  • State Tracking: Monitors Macroscopic States          │
    │                                                         │
    │  OUTPUT → Observation, Need, SystemAction               │
    ├─────────────────────────────────────────────────────────┤
    │  RIGHT HEMISPHERE (persona.py)                          │
    │  ─────────────────────────────────────────────────────  │
    │  • Creative Core: Generates narrative and dialogue      │
    │  • Character Acting: Voices NPCs authentically          │
    │  • Atmosphere: Applies genre and tone                   │
    │  • Korean Localization: Natural language output         │
    │                                                         │
    │  OUTPUT → Narrative Response in Korean                  │
    └─────────────────────────────────────────────────────────┘

Memory Hierarchy (정보 충돌 시 우선순위):
    Priority 1 (LOWEST):  LORE - Initial setup, character profiles
    Priority 2 (MEDIUM):  FERMENTED - Long-term memory, past events
    Priority 3 (HIGHEST): FRESH - Recent context, current scene

Left Hemisphere Principles:
    • Observe ONLY Macroscopic States (observable phenomena)
    • Never assert Microscopic States (inner thoughts) as fact
    • Apply physics and causality strictly
    • Output structured data for Right Hemisphere consumption
"""

import json
import asyncio
import logging
import re
from typing import Optional, Dict, Any, List, Callable, TypeVar
from google.genai import types

# =========================================================
# 상수 정의
# =========================================================
MAX_RETRY_COUNT = 3
RETRY_DELAY_SECONDS = 1

# =========================================================
# WORLD CONSTRAINTS EXTRACTION (세계 제약 추출)
# 좌뇌가 로어에서 핵심 규칙을 추출할 때 사용
# =========================================================
WORLD_CONSTRAINTS_TEMPLATE = """
<World_Constraints_Extraction>
Extract ONLY the most fundamental, inviolable constraints from Lore—rules that, if broken, would destroy the world's internal logic:

### Categories to Extract
1. **Setting:** Era, location, time period, original work (if derivative)
2. **Theme:** Genre, tone, atmosphere, mood
3. **Systems:** Magic, technology, physics, supernatural rules (hard limits)
4. **Social:** Hierarchy, taboos, cultural norms, power structures
5. **Speech:** Register, dialect, character-specific speech patterns

### Output Format
{
  "setting": {"era": "...", "location": "...", "time_period": "...", "derivative_of": null},
  "theme": {"genres": [], "tone": "...", "atmosphere": "..."},
  "systems": {"magic": {...}, "technology": {...}, "physics_rules": [...], "supernatural": {...}},
  "social": {"hierarchy": "...", "taboos": [...], "norms": [...]},
  "speech_patterns": {"default_register": "...", "character_specific": {...}}
}
</World_Constraints_Extraction>
"""

# =========================================================
# TEMPORAL ORIENTATION PROTOCOL (시간 방향 프로토콜)
# 좌뇌가 메모리 계층에서 컨텍스트를 선택하는 방법
# =========================================================
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

# =========================================================
# INTERNAL STATE TRACKING (내부 상태 추적)
# 캐릭터 상태를 구조화된 형식으로 추적
# =========================================================
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

# =========================================================
# COGNITIVE ARCHITECTURE MODEL (인지 아키텍처 모델)
# 좌뇌가 캐릭터 상태를 분석할 때 사용하는 프레임워크
# =========================================================
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

# 지원되는 장르 목록
SUPPORTED_GENRES = [
    'wuxia', 'noir', 'high_fantasy', 'cyberpunk', 'cosmic_horror',
    'post_apocalypse', 'urban_fantasy', 'steampunk', 'school_life',
    'superhero', 'space_opera', 'western', 'occult', 'military'
]

# 장르별 키워드 맵 (한국어 포함)
GENRE_KEYWORD_MAP = {
    "high_fantasy": [
        "dragon", "elf", "orc", "magic", "wizard", "spell", "kingdom", 
        "mana", "legion", "드래곤", "엘프", "마법", "왕국"
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
# [HELPER] JSON 파싱 안전장치
# =========================================================
def safe_parse_json(text: Optional[str]) -> Dict[str, Any]:
    """
    AI 응답 텍스트에서 JSON 객체나 리스트를 정밀하게 찾아 파싱합니다.
    
    Args:
        text: AI 응답 텍스트
    
    Returns:
        파싱된 딕셔너리 (실패 시 빈 딕셔너리)
    """
    if not text:
        return {}
    
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
            return {}
        
        # 대응하는 종료점 찾기
        target_end = '}' if cleaned_text[start_idx] == '{' else ']'
        end_idx = -1
        
        for i in range(len(cleaned_text) - 1, start_idx, -1):
            if cleaned_text[i] == target_end:
                end_idx = i + 1
                break
        
        if end_idx == -1:
            return {}
        
        json_str = cleaned_text[start_idx:end_idx]
        data = json.loads(json_str)
        
        # 리스트인 경우 첫 번째 딕셔너리 요소 반환
        if isinstance(data, list):
            if len(data) > 0 and isinstance(data[0], dict):
                return data[0]
            return {}
        
        if not isinstance(data, dict):
            return {}
        
        return data
    
    except json.JSONDecodeError as e:
        logging.debug(f"JSON 파싱 실패: {e}")
        return {}
    except Exception as e:
        logging.warning(f"safe_parse_json 예외: {e}")
        return {}


# =========================================================
# [HELPER] API 호출 재시도 래퍼
# =========================================================
async def api_call_with_retry(
    client,
    model_id: str,
    contents: List[types.Content],
    config: types.GenerateContentConfig,
    operation_name: str = "API Call"
) -> Optional[str]:
    """
    Gemini API 호출을 재시도 로직과 함께 수행합니다.
    
    Args:
        client: Gemini 클라이언트
        model_id: 모델 ID
        contents: 요청 콘텐츠
        config: 생성 설정
        operation_name: 로깅용 작업 이름
    
    Returns:
        응답 텍스트 또는 None (모든 재시도 실패 시)
    """
    for attempt in range(MAX_RETRY_COUNT):
        try:
            response = await client.aio.models.generate_content(
                model=model_id,
                contents=contents,
                config=config
            )
            
            if response and response.text:
                return response.text.strip()
            
            logging.warning(f"[{operation_name}] 빈 응답 수신 (시도 {attempt + 1}/{MAX_RETRY_COUNT})")
            
        except Exception as e:
            logging.warning(
                f"[{operation_name}] API 호출 실패 (시도 {attempt + 1}/{MAX_RETRY_COUNT}): {e}"
            )
        
        if attempt < MAX_RETRY_COUNT - 1:
            await asyncio.sleep(RETRY_DELAY_SECONDS)
    
    logging.error(f"[{operation_name}] 모든 재시도 실패")
    return None


# =========================================================
# [LORE COMPRESSION] 로어 압축기
# =========================================================
async def compress_lore_core(
    client,
    model_id: str,
    raw_lore_text: str
) -> str:
    """
    [THEORIA LOGIC CORE]
    방대한 로어 텍스트를 토큰 효율적인 '핵심 요약본'으로 압축합니다.
    
    Args:
        client: Gemini 클라이언트
        model_id: 모델 ID
        raw_lore_text: 원본 로어 텍스트
    
    Returns:
        압축된 로어 요약본 (실패 시 에러 메시지)
    """
    system_instruction = (
        "[THEORIA LEFT HEMISPHERE - Lore Archivist]\n"
        "Compress raw lore into a dense, structured sourcebook for TRPG use.\n\n"
        
        "### COMPRESSION PRINCIPLES\n"
        "**Discard:** Fluff, poetry, purple prose, repetition, flavor text.\n"
        "**Keep:** Hard rules, faction data, NPC motives, conflicts, secrets, locations.\n\n"
        
        "### OUTPUT STRUCTURE\n"
        "- **World Laws:** Physics, magic systems, technology limits\n"
        "- **Factions:** Power structures, goals, relationships\n"
        "- **Key NPCs:** Names, roles, known motivations (Macroscopic only)\n"
        "- **Tension Points:** Active conflicts, mysteries, threats\n"
        "- **Secrets:** Hidden information (for GM reference)\n\n"
        
        "### MEMORY NOTE\n"
        "This output becomes part of LORE layer (Priority 1 - lowest).\n"
        "It may be overridden by FERMENTED or FRESH data during play."
    )
    
    user_prompt = (
        f"### RAW LORE TEXT\n{raw_lore_text}\n\n"
        "### INSTRUCTION\n"
        "Compress into dense sourcebook summary. Plain text, structured sections."
    )
    
    contents = [
        types.Content(role="user", parts=[types.Part(text=user_prompt)])
    ]
    config = types.GenerateContentConfig(temperature=0.2)
    
    result = await api_call_with_retry(
        client, model_id, contents, config,
        operation_name="Lore Compression"
    )
    
    if result:
        return result
    
    return "Error: Lore Compression Failed."


# =========================================================
# [LOGIC ANALYZER] 상황 판단 및 인과율 계산
# =========================================================
async def analyze_context_nvc(
    client,
    model_id: str,
    history_text: str,
    lore: str,
    rules: str,
    active_quests_text: str
) -> Dict[str, Any]:
    """
    [THEORIA LEFT HEMISPHERE]
    현재 상황을 분석하여 객관적 사실과 다음 행동을 추론합니다.
    
    Args:
        client: Gemini 클라이언트
        model_id: 모델 ID
        history_text: 대화 히스토리
        lore: 로어 텍스트
        rules: 게임 규칙
        active_quests_text: 활성 퀘스트 목록
    
    Returns:
        분석 결과 딕셔너리
    """
    system_instruction = (
        "[THEORIA LEFT HEMISPHERE - Logic Core]\n"
        "You are the analytical component of the THEORIA system.\n"
        "Your role: Extract OBJECTIVE FACTS from the narrative context.\n\n"
        
        "### CORE PRINCIPLES (From World Axiom)\n"
        "1. **MACROSCOPIC ONLY:** Analyze observable phenomena ONLY.\n"
        "   - ✅ Actions, speech, physical states, environmental changes\n"
        "   - ❌ Inner thoughts, emotions, intentions (these are Microscopic)\n"
        "2. **CAUSALITY BOUND:** Apply physics and logic strictly.\n"
        "3. **ASYNCHRONOUS WORLD:** Consider what NPCs might be doing concurrently.\n\n"
        
        "### COGNITIVE ARCHITECTURE REFERENCE\n"
        "When analyzing character states, consider:\n"
        "- **Physical Instinct:** Polyvagal state (Ventral/Sympathetic/Dorsal)\n"
        "- **Emotional Instinct:** Plutchik emotions (Anger/Fear/Joy/Sadness/etc.)\n"
        "- **Logos State:** Monolithic (core identity) vs Transient (surface mood)\n"
        "- **Value Dynamics:** Binary Trade-off / Alignment / Dissonance / Synergy\n"
        "- **Cognition Mode:** Resonance / Inertia / Analysis / Overload / Insight\n"
        "NOTE: Infer these from OBSERVABLE behavior, never assert directly.\n\n"
        
        "### MEMORY HIERARCHY CHECK\n"
        "- FRESH (Recent) overrides FERMENTED (Past) overrides LORE (Initial)\n"
        "- When conflicts exist, use the highest priority source.\n\n"
        
        "### OBSERVATION PROTOCOLS\n"
        "1. **Physics Check (Hard Limits):** Verify physical/logical possibility. "
        "If impossible, state: **'Action Failed: Physics Violation'**.\n"
        "2. **Knowledge Firewall:** Distinguish Player Knowledge vs Character Knowledge.\n"
        "3. **Causal Integrity:** Verify causes existed BEFORE effects.\n"
        "4. **Auto-XP Calculation:**\n"
        "   - **Minor (10-30):** Skill check, smart move.\n"
        "   - **Major (50-100):** Defeated enemy, solved puzzle, survived crisis.\n"
        "   - **Critical (200+):** Boss kill, Quest complete.\n"
        "   - *Condition:* Award ONLY for observable Success/Victory.\n\n"

        "### OUTPUT FORMAT (JSON ONLY)\n"
        "{\n"
        '  "CurrentLocation": "Location Name",\n'
        '  "LocationRisk": "None/Low/Medium/High/Extreme",\n'
        '  "TimeContext": "Time of day/flow",\n'
        '  "PhysicalState": "Inferred Polyvagal state from observable behavior",\n'
        '  "Observation": "Objective summary of MACROSCOPIC states only.",\n'
        '  "TemporalOrientation": {\n'
        '    "continuity_from_previous": "What carries over from last turn",\n'
        '    "active_threads": ["Unresolved thread 1", "Thread 2"],\n'
        '    "offscreen_npcs": ["NPC doing X elsewhere"],\n'
        '    "suggested_focus": "What the Right Hemisphere should emphasize"\n'
        '  },\n'
        '  "Need": "Logical next step for Right Hemisphere",\n'
        '  "SystemAction": { "tool": "Quest/Memo/NPC/XP", "type": "...", "content": "..." } OR null\n'
        "}\n"
    )

    user_prompt = (
        f"### [RULES]\n{rules}\n"
        f"### [QUESTS]\n{active_quests_text}\n"
        f"### [HISTORY]\n{history_text}\n"
        "Analyze the current state. Include temporal orientation for narrative continuity."
    )
    
    contents = [
        types.Content(role="user", parts=[types.Part(text=user_prompt)])
    ]
    config = types.GenerateContentConfig(
        response_mime_type="application/json",
        temperature=0.1
    )
    
    result = await api_call_with_retry(
        client, model_id, contents, config,
        operation_name="Context Analysis (NVC)"
    )
    
    if result:
        parsed = safe_parse_json(result)
        if parsed:
            return parsed
    
    # 기본값 반환
    return {
        "CurrentLocation": "Unknown",
        "LocationRisk": "Low",
        "TimeContext": "Unknown",
        "Observation": "Analysis Failed",
        "Need": "Proceed with Caution",
        "SystemAction": None
    }


# =========================================================
# [GENRE ANALYZER] 장르 분석 (AI + 키워드 폴백)
# =========================================================
def _calculate_keyword_scores(text: str) -> Dict[str, int]:
    """텍스트에서 장르별 키워드 점수를 계산합니다."""
    text_lower = text.lower()
    scores = {}
    
    for genre, keywords in GENRE_KEYWORD_MAP.items():
        count = sum(1 for keyword in keywords if keyword in text_lower)
        if count > 0:
            scores[genre] = count
    
    return scores


def _select_top_genres(
    scores: Dict[str, int],
    ai_genres: List[str],
    max_count: int = 3
) -> List[str]:
    """
    점수와 AI 결과를 종합하여 상위 장르를 선택합니다.
    
    Args:
        scores: 키워드 점수 딕셔너리
        ai_genres: AI가 제안한 장르 리스트
        max_count: 최대 반환 장르 수
    
    Returns:
        선택된 장르 리스트
    """
    # 점수순 정렬
    sorted_genres = sorted(scores.items(), key=lambda x: x[1], reverse=True)
    
    # 최소 3회 이상 등장한 장르만 후보로 (엄격한 기준)
    detected = [g for g, score in sorted_genres if score >= 3][:max_count]
    
    # 엄격한 기준 통과 장르가 없으면 완화된 기준 적용
    if not detected and sorted_genres:
        detected = [g for g, _ in sorted_genres[:2]]
    
    # AI 결과와 병합 (키워드에서도 지지받는 것만)
    final_set = set(detected)
    for ai_genre in ai_genres:
        if ai_genre in scores and scores[ai_genre] >= 2:
            final_set.add(ai_genre)
    
    final_list = list(final_set)[:max_count]
    
    # Noir 특별 처리: 다른 명확한 장르가 있으면 제거
    if len(final_list) > 1 and "noir" in final_list:
        noir_score = scores.get("noir", 0)
        other_scores = [scores.get(g, 0) for g in final_list if g != "noir"]
        
        if noir_score < max(other_scores, default=0) * 0.5:
            final_list.remove("noir")
            logging.info("[Genre Analysis] Noir 제거됨 (다른 장르가 더 명확함)")
    
    return final_list


async def analyze_genre_from_lore(
    client,
    model_id: str,
    lore_text: str
) -> Dict[str, Any]:
    """
    [Logic Core] 로어에서 장르와 톤을 분석합니다.
    AI 분석을 우선하고, 실패 시 키워드 스코어링으로 폴백합니다.
    
    Args:
        client: Gemini 클라이언트
        model_id: 모델 ID
        lore_text: 로어 텍스트
    
    Returns:
        {"genres": [...], "custom_tone": "..."}
    """
    ai_genres = []
    custom_tone = "Default"
    ai_confidence = "low"
    
    # 1. AI 분석 시도
    system_instruction = (
        "Analyze the provided Lore and extract Key Genres.\n"
        "**CRITICAL RULES:**\n"
        "1. Select ONLY the most dominant 1-3 genres. Do not list minor elements.\n"
        "2. Prioritize genres that define the core atmosphere and narrative structure.\n"
        "3. If multiple genres compete, choose those most explicitly mentioned or thematically central.\n\n"
        f"Supported List: {SUPPORTED_GENRES}\n\n"
        'Output JSON: {"genres": [str], "custom_tone": str, "confidence": "high/medium/low"}'
    )
    
    user_prompt = f"Lore Data:\n{lore_text}"
    
    contents = [
        types.Content(role="user", parts=[types.Part(text=user_prompt)])
    ]
    config = types.GenerateContentConfig(
        response_mime_type="application/json",
        temperature=0.3
    )
    
    for attempt in range(MAX_RETRY_COUNT):
        try:
            response = await client.aio.models.generate_content(
                model=model_id,
                contents=contents,
                config=config
            )
            
            if response and response.text:
                data = safe_parse_json(response.text)
                ai_genres = data.get("genres", [])
                custom_tone = data.get("custom_tone", "Analyzed Tone")
                ai_confidence = data.get("confidence", "medium")
                
                # AI가 명확히 판단했으면 (1-3개 장르 + 높은 신뢰도)
                if ai_genres and len(ai_genres) <= 3 and ai_confidence in ["high", "medium"]:
                    logging.info(
                        f"[Genre Analysis] AI 분석 성공: {ai_genres} (신뢰도: {ai_confidence})"
                    )
                    return {
                        "genres": ai_genres[:3],
                        "custom_tone": custom_tone
                    }
            break
            
        except Exception as e:
            logging.warning(f"[Genre Analysis] AI 시도 {attempt + 1}/{MAX_RETRY_COUNT} 실패: {e}")
            if attempt < MAX_RETRY_COUNT - 1:
                await asyncio.sleep(RETRY_DELAY_SECONDS)
    
    # 2. 키워드 폴백 시스템
    logging.info("[Genre Analysis] AI 신뢰도 낮음 → 키워드 스코어링 시작")
    
    keyword_scores = _calculate_keyword_scores(lore_text)
    final_genres = _select_top_genres(keyword_scores, ai_genres)
    
    # 최종 기본값 처리
    if not final_genres:
        final_genres = ["noir"]
        logging.warning("[Genre Analysis] 모든 분석 실패 → 기본값(noir) 적용")
    
    logging.info(f"[Genre Analysis] 최종 결과: {final_genres} (키워드 스코어: {keyword_scores})")
    
    return {
        "genres": final_genres,
        "custom_tone": custom_tone
    }


# =========================================================
# [NPC ANALYZER] NPC 데이터 추출
# =========================================================
async def analyze_npcs_from_lore(
    client,
    model_id: str,
    lore_text: str
) -> List[Dict[str, str]]:
    """
    [Logic Core] 로어에서 주요 NPC 정보를 추출합니다.
    
    Args:
        client: Gemini 클라이언트
        model_id: 모델 ID
        lore_text: 로어 텍스트
    
    Returns:
        NPC 정보 리스트 [{"name": "...", "description": "..."}, ...]
    """
    system_instruction = (
        "Extract major NPCs from the lore.\n"
        "Focus on characters with significant roles, unique traits, or plot importance.\n"
        'Output JSON: {"npcs": [{"name": "...", "description": "..."}]}'
    )
    
    user_prompt = f"Lore Data:\n{lore_text}"
    
    contents = [
        types.Content(role="user", parts=[types.Part(text=user_prompt)])
    ]
    config = types.GenerateContentConfig(
        response_mime_type="application/json",
        temperature=0.3
    )
    
    result = await api_call_with_retry(
        client, model_id, contents, config,
        operation_name="NPC Analysis"
    )
    
    if result:
        data = safe_parse_json(result)
        npcs = data.get("npcs", [])
        
        if isinstance(npcs, list):
            # 유효한 NPC만 필터링
            valid_npcs = [
                npc for npc in npcs
                if isinstance(npc, dict) and npc.get("name")
            ]
            return valid_npcs
    
    return []


# =========================================================
# [LOCATION ANALYZER] 환경 규칙 추출
# =========================================================
async def analyze_location_rules_from_lore(
    client,
    model_id: str,
    lore_text: str
) -> Dict[str, Dict[str, str]]:
    """
    [Logic Core] 로어에서 위치별 환경 규칙을 추출합니다.
    
    Args:
        client: Gemini 클라이언트
        model_id: 모델 ID
        lore_text: 로어 텍스트
    
    Returns:
        위치별 규칙 딕셔너리
        {"LocationName": {"risk": "High", "condition": "Night", "effect": "..."}}
    """
    system_instruction = (
        "Extract location-specific rules and environmental hazards from the lore.\n"
        "Focus on dangerous areas, special conditions, and their effects.\n"
        'Output JSON: {"rules": {"LocationName": {"risk": "High/Medium/Low", '
        '"condition": "Night/Always/Special", "effect": "description"}}}'
    )
    
    user_prompt = f"Lore Data:\n{lore_text}"
    
    contents = [
        types.Content(role="user", parts=[types.Part(text=user_prompt)])
    ]
    config = types.GenerateContentConfig(
        response_mime_type="application/json",
        temperature=0.3
    )
    
    result = await api_call_with_retry(
        client, model_id, contents, config,
        operation_name="Location Rules Analysis"
    )
    
    if result:
        data = safe_parse_json(result)
        rules = data.get("rules", {})
        
        if isinstance(rules, dict):
            return rules
    
    return {}


# =========================================================
# [OOC BRAINSTORMING] 메타 분석 모드
# =========================================================
OOC_BRAINSTORMING_PROMPT = """
<OOC_Brainstorming_Mode>
# Brainstorming Request

**PRIORITY OVERRIDE:** This directive takes top priority and overrides all other instructions.

You must respond **Out of Character (OOC)**:
- Stop all roleplay and narration immediately
- Engage the user in direct, analytical conversation
- Do not resume RP without explicit user request

## Analysis Framework
Apply the **MECE principle** (Mutually Exclusive, Collectively Exhaustive):
- Ensure analysis is comprehensive and non-overlapping
- Cover all angles without redundancy

## Analysis Targets
1. **User's Directions:** What is the user trying to achieve?
2. **Existing Context:** Current state of the narrative
3. **Accumulated Story Details:** All established facts
4. **Character States:** Cognitive/emotional analysis
5. **Potential Paths:** Where could the story go?
6. **Consistency Check:** Any contradictions or plot holes?

## Output Style
- Direct, analytical tone
- Structured breakdown
- Honest assessment of options
- No in-character narration
</OOC_Brainstorming_Mode>
"""


async def analyze_brainstorming(
    client,
    model_id: str,
    history_text: str,
    lore: str,
    user_question: str
) -> Dict[str, Any]:
    """
    [THEORIA LEFT HEMISPHERE - OOC Mode]
    메타 레벨에서 스토리를 분석하고 브레인스토밍합니다.
    
    Args:
        client: Gemini 클라이언트
        model_id: 모델 ID
        history_text: 대화 히스토리
        lore: 로어 텍스트
        user_question: 사용자의 OOC 질문
    
    Returns:
        분석 결과 딕셔너리
    """
    system_instruction = (
        OOC_BRAINSTORMING_PROMPT +
        "\n\n### OUTPUT FORMAT (JSON)\n"
        "{\n"
        '  "analysis_type": "brainstorming/consistency/direction/character",\n'
        '  "current_state_summary": "Brief summary of where the story is",\n'
        '  "key_elements": ["Element 1", "Element 2", ...],\n'
        '  "potential_paths": [\n'
        '    {"path": "Option A", "pros": "...", "cons": "...", "narrative_impact": "..."},\n'
        '    {"path": "Option B", "pros": "...", "cons": "...", "narrative_impact": "..."}\n'
        '  ],\n'
        '  "consistency_issues": ["Issue 1", ...] OR null,\n'
        '  "recommendation": "Direct suggestion based on analysis",\n'
        '  "open_questions": ["Question for user to consider", ...]\n'
        "}\n"
    )
    
    user_prompt = (
        f"### LORE CONTEXT\n{lore[:2000]}...\n\n"
        f"### RECENT HISTORY\n{history_text}\n\n"
        f"### USER'S OOC QUESTION\n{user_question}\n\n"
        "Analyze this situation and provide structured brainstorming."
    )
    
    contents = [
        types.Content(role="user", parts=[types.Part(text=user_prompt)])
    ]
    config = types.GenerateContentConfig(
        response_mime_type="application/json",
        temperature=0.5  # 창의적 분석을 위해 약간 높은 온도
    )
    
    result = await api_call_with_retry(
        client, model_id, contents, config,
        operation_name="OOC Brainstorming"
    )
    
    if result:
        parsed = safe_parse_json(result)
        if parsed:
            return parsed
    
    return {
        "analysis_type": "error",
        "current_state_summary": "Analysis failed",
        "recommendation": "Please try rephrasing your question."
    }


async def check_narrative_consistency(
    client,
    model_id: str,
    history_text: str,
    lore: str
) -> Dict[str, Any]:
    """
    [THEORIA LEFT HEMISPHERE - Consistency Checker]
    서사의 일관성을 검사합니다.
    
    Args:
        client: Gemini 클라이언트
        model_id: 모델 ID
        history_text: 대화 히스토리
        lore: 로어 텍스트
    
    Returns:
        일관성 검사 결과
    """
    system_instruction = (
        "[THEORIA LEFT HEMISPHERE - Consistency Audit]\n"
        "Analyze the narrative for internal consistency.\n\n"
        
        "### CHECK CATEGORIES\n"
        "1. **Temporal:** Do events follow logical time order?\n"
        "2. **Spatial:** Are locations consistent? Can characters be where they are?\n"
        "3. **Character:** Do actions match established personalities/abilities?\n"
        "4. **Causal:** Do effects have proper causes? (Cause must precede effect)\n"
        "5. **Memory:** Does FRESH contradict FERMENTED or LORE?\n\n"
        
        "### OUTPUT FORMAT (JSON)\n"
        "{\n"
        '  "overall_consistency": "High/Medium/Low",\n'
        '  "issues": [\n'
        '    {"category": "temporal/spatial/character/causal/memory", '
        '     "description": "...", "severity": "critical/minor", '
        '     "suggestion": "How to fix"}\n'
        '  ],\n'
        '  "plot_threads": ["Active thread 1", "Active thread 2", ...],\n'
        '  "unresolved_elements": ["Element needing resolution", ...]\n'
        "}\n"
    )
    
    user_prompt = (
        f"### LORE\n{lore[:1500]}\n\n"
        f"### HISTORY\n{history_text}\n\n"
        "Audit this narrative for consistency issues."
    )
    
    contents = [
        types.Content(role="user", parts=[types.Part(text=user_prompt)])
    ]
    config = types.GenerateContentConfig(
        response_mime_type="application/json",
        temperature=0.1
    )
    
    result = await api_call_with_retry(
        client, model_id, contents, config,
        operation_name="Consistency Check"
    )
    
    if result:
        parsed = safe_parse_json(result)
        if parsed:
            return parsed
    
    return {
        "overall_consistency": "Unknown",
        "issues": [],
        "plot_threads": [],
        "unresolved_elements": []
    }


async def extract_world_constraints(
    client,
    model_id: str,
    lore_text: str
) -> Dict[str, Any]:
    """
    [THEORIA LEFT HEMISPHERE - World Constraints Extraction]
    로어에서 세계의 핵심 규칙을 추출합니다.
    
    Args:
        client: Gemini 클라이언트
        model_id: 모델 ID
        lore_text: 로어 텍스트
    
    Returns:
        세계 규칙 딕셔너리
    """
    system_instruction = (
        WORLD_CONSTRAINTS_TEMPLATE +
        "\n\nExtract ONLY fundamental, inviolable constraints from the provided lore."
    )
    
    user_prompt = f"### LORE TEXT\n{lore_text[:4000]}\n\nExtract world constraints."
    
    contents = [
        types.Content(role="user", parts=[types.Part(text=user_prompt)])
    ]
    config = types.GenerateContentConfig(
        response_mime_type="application/json",
        temperature=0.2
    )
    
    result = await api_call_with_retry(
        client, model_id, contents, config,
        operation_name="World Constraints Extraction"
    )
    
    if result:
        parsed = safe_parse_json(result)
        if parsed:
            return parsed
    
    return None
