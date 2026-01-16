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
    │  • NPC Extraction: Separates NPCs from lore             │
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
from typing import Optional, Dict, Any, List, Callable, TypeVar, Tuple
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
# [HELPER] NPC 설명 압축
# =========================================================
async def summarize_npc_description(
    client,
    model_id: str,
    npc_name: str,
    description: str
) -> str:
    """
    긴 NPC 설명을 2줄 이내로 압축합니다.
    
    Args:
        client: Gemini 클라이언트
        model_id: 모델 ID
        npc_name: NPC 이름
        description: 원본 설명
    
    Returns:
        압축된 설명 (2줄 이내)
    """
    # 이미 짧으면 그대로 반환
    if len(description) <= 150:
        return description
    
    system_instruction = (
        "You are a brief summarizer. Compress the given NPC description into 1-2 lines (max 100 characters).\n"
        "Keep only the most essential traits: role, personality, key feature.\n"
        "Output ONLY the compressed description in Korean. No explanation."
    )
    
    user_prompt = f"NPC 이름: {npc_name}\n설명:\n{description}"
    
    contents = [
        types.Content(role="user", parts=[types.Part(text=user_prompt)])
    ]
    
    config = types.GenerateContentConfig(
        system_instruction=system_instruction,
        temperature=0.1,
        max_output_tokens=150
    )
    
    result = await api_call_with_retry(
        client, model_id, contents, config,
        operation_name="NPC Summarize"
    )
    
    if result:
        # 줄바꿈 제거하고 2줄 이내로 제한
        lines = [l.strip() for l in result.split('\n') if l.strip()]
        return ' '.join(lines[:2])[:150]
    
    # 실패 시 원본의 처음 150자만 반환
    return description[:150] + "..." if len(description) > 150 else description


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
# [LOGIC ANALYZER] 상황 판단 및 인과율 계산
# =========================================================
async def analyze_context_nvc(
    client,
    model_id: str,
    history_text: str,
    lore: str,
    rules: str,
    active_quests_text: str,
    player_context: str = ""
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
        player_context: 플레이어 상태 (보유 패시브 등)
    
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
        
        f"{COGNITIVE_ARCHITECTURE_MODEL}\n\n"
        
        f"{STATE_TRACKING_FORMAT}\n\n"
        
        f"{TEMPORAL_ORIENTATION_PROTOCOL}\n\n"
        
        "### OBSERVATION PROTOCOLS\n"
        "1. **Physics Check (Hard Limits):** Verify physical/logical possibility. "
        "If impossible, state: **'Action Failed: Physics Violation'**.\n"
        "2. **Knowledge Firewall:** Distinguish Player Knowledge vs Character Knowledge.\n"
        "3. **Causal Integrity:** Verify causes existed BEFORE effects.\n"
        "4. **Experience Recognition:** Note significant achievements, repeated experiences, and growth moments.\n\n"

        "### SYSTEM ACTION RULES (자동 퀘스트/메모/NPC 관리)\n"
        "SystemAction triggers automatically based on narrative events.\n\n"
        
        "**Quest Actions:**\n"
        "- `{\"tool\": \"Quest\", \"type\": \"Add\", \"content\": \"퀘스트 내용\"}` — When NPC gives mission, player discovers objective\n"
        "- `{\"tool\": \"Quest\", \"type\": \"Complete\", \"content\": \"기존 퀘스트의 일부 텍스트\"}` — When objective achieved, mission accomplished\n\n"
        
        "**Memo Actions:**\n"
        "- `{\"tool\": \"Memo\", \"type\": \"Add\", \"content\": \"메모 내용\"}` — Important info: clues, NPC names, codes, locations, items acquired, rumors/gossip heard\n"
        "- `{\"tool\": \"Memo\", \"type\": \"Archive\", \"content\": \"기존 메모의 일부 텍스트\"}` — When memo becomes obsolete (item used, info no longer relevant)\n\n"
        
        "**NPC Actions:**\n"
        "- `{\"tool\": \"NPC\", \"type\": \"Add\", \"content\": \"이름: 설명\"}` — When new named NPC introduced\n\n"
        
        "**Examples:**\n"
        "- Player receives letter with mission → Quest Add\n"
        "- Player defeats boss mentioned in quest → Quest Complete\n"
        "- Player finds password \"1234\" → Memo Add\n"
        "- Player hears rumor about \"haunted forest at night\" → Memo Add\n"
        "- NPC mentions \"black market in the sewers\" → Memo Add\n"
        "- Player uses the password successfully → Memo Archive\n"
        "- Player meets \"철수\" the blacksmith → NPC Add\n\n"
        
        "**IMPORTANT:** Return `null` if no action needed. Don't force actions.\n\n"

        "### NPC INTERACTION SYSTEM\n"
        "Analyze NPCs present in the scene and their attitudes toward players.\n\n"
        
        "**NPCAttitudes:** For each NPC interacting with players, determine attitude based on context:\n"
        "- `hostile`: Aggressive, threatening, may lie or attack\n"
        "- `unfriendly`: Cold, short answers, uncooperative\n"
        "- `neutral`: Polite, businesslike, will trade\n"
        "- `friendly`: Warm, helpful, shares information\n"
        "- `devoted`: Loyal, shares secrets, willing to sacrifice\n\n"
        
        "**NPCInteraction:** When 2+ NPCs are present, suggest ambient dialogue between them:\n"
        "- Tavern scene: NPCs gossiping, arguing, flirting\n"
        "- Market: Merchants competing, customers complaining\n"
        "- Combat aftermath: NPCs reacting to events\n"
        "- Set to `null` if no NPC interaction is appropriate.\n\n"

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
        '  "NPCAttitudes": {\n'
        '    "NPC이름": {"attitude": "hostile/unfriendly/neutral/friendly/devoted", "reason": "why"},\n'
        '    "...": {...}\n'
        '  },\n'
        '  "NPCInteraction": {\n'
        '    "participants": ["NPC1", "NPC2"],\n'
        '    "type": "gossip/argument/flirt/business/reaction",\n'
        '    "topic": "What they might discuss",\n'
        '    "mood": "tense/casual/heated/secretive"\n'
        '  } OR null,\n'
        '  "AbnormalElements": ["드래곤", "마법", "고백"] OR [],\n'
        '  "ExperienceCounters": {"독중독": 1, "백병전": 1} OR {},\n'
        '  "SceneType": "normal/gore/nsfw/gore_nsfw",\n'
        '  "Need": "Logical next step for Right Hemisphere",\n'
        '  "SystemAction": { "tool": "Quest/Memo/NPC", "type": "Add/Complete/Archive", "content": "..." } OR null,\n'
        '  "PlayerUpdate": {\n'
        '    "inventory_add": {"아이템이름": 수량} OR null,\n'
        '    "inventory_remove": {"아이템이름": 수량} OR null,\n'
        '    "gold_change": +100 OR -50 OR null,\n'
        '    "status_add": ["중독", "피로"] OR null,\n'
        '    "status_remove": ["출혈"] OR null\n'
        '  } OR null,\n'
        '  "PlayerMemoryUpdate": {\n'
        '    "appearance": "외형 설명 (변경시에만)" OR null,\n'
        '    "relationships": {"NPC이름": "관계 설명"} OR null,\n'
        '    "passives": ["새 패시브/칭호"] OR null,\n'
        '    "known_info": ["새로 알게 된 정보/소문/단서"] OR null,\n'
        '    "foreshadowing": ["복선/떡밥"] OR null,\n'
        '    "normalization": {"비일상요소": "적응 단계"} OR null,\n'
        '    "companions": ["동행자이름: 설명"] OR null\n'
        '  } OR null,\n'
        '  "SessionMemoryUpdate": {\n'
        '    "world_summary": "현재 세계 상황 요약 (변경시에만)" OR null,\n'
        '    "world_changes": ["세계에 일어난 변화"] OR null,\n'
        '    "current_arc": "현재 스토리 아크 설명" OR null,\n'
        '    "active_threads": ["새로 시작된 플롯 스레드"] OR null,\n'
        '    "resolved_threads": ["해결된 플롯 스레드"] OR null,\n'
        '    "npc_summaries": {"NPC이름": "NPC 요약 설명"} OR null\n'
        '  } OR null\n'
        "}\n"
        "\n"
        "### PLAYER UPDATE SYSTEM (자동 인벤토리/골드/상태이상 관리)\n"
        "PlayerUpdate triggers automatically based on narrative events.\n\n"
        
        "**When to update:**\n"
        "- Player picks up item → inventory_add\n"
        "- Player uses/loses item → inventory_remove\n"
        "- Player receives payment/reward → gold_change: +amount\n"
        "- Player pays/loses money → gold_change: -amount\n"
        "- Player gets poisoned/injured/cursed → status_add\n"
        "- Player heals/recovers/cured → status_remove\n\n"
        
        "**Examples:**\n"
        "- Player finds a sword → inventory_add: {\"검\": 1}\n"
        "- Player drinks potion → inventory_remove: {\"포션\": 1}\n"
        "- Player sells item for 50 gold → gold_change: 50\n"
        "- Player gets bitten by snake → status_add: [\"중독\"]\n"
        "- Player rests at inn → status_remove: [\"피로\"]\n\n"
        
        "**IMPORTANT:** Return null if no update needed. Don't force updates.\n\n"

        "### SCENE TYPE DETECTION (자동 장면 유형 감지)\n"
        "**SceneType:** Automatically detect the nature of the current scene.\n"
        "Based on narrative context, determine if mature content descriptions are appropriate:\n\n"
        
        "- `normal`: Standard scene - default narrative style\n"
        "- `gore`: Scene involves graphic violence, torture, severe injury, body horror\n"
        "  Examples: 전투 중 심각한 부상, 고문, 처형, 신체 훼손, 잔혹한 죽음\n"
        "- `nsfw`: Scene involves intimate/romantic situations between consenting adults\n"
        "  Examples: 연인 간 친밀한 장면, 성인 로맨스, 관능적 상황\n"
        "- `gore_nsfw`: Scene involves both elements\n\n"
        
        "**Detection criteria:**\n"
        "- Entering combat with high stakes → consider `gore` if injuries likely\n"
        "- Romantic progression reaching intimate moment → consider `nsfw`\n"
        "- Torture/horror scenes → `gore`\n"
        "- Normal exploration/dialogue → `normal`\n\n"
        
        "**IMPORTANT:** Default to `normal` unless scene clearly warrants mature content.\n\n"

        "### ABNORMAL ELEMENTS & EXPERIENCE DETECTION\n"
        "**AbnormalElements:** List any supernatural, unusual, or extraordinary elements in the scene.\n"
        "Examples: 드래곤, 마법, 귀신, 상태창, 이세계, 몬스터, 초능력, 고백, 결투, 납치\n\n"
        "**ExperienceCounters:** Detect significant experiences that contribute to character growth.\n"
        "Use descriptive names based on what actually happened:\n"
        "- Physical trials: 독중독, 화상, 동상, 낙하, 기절, 굶주림 등\n"
        "- Combat experiences: 백병전, 암살시도, 포위당함 등\n"
        "- Social/emotional: 배신당함, 거절당함, 협박당함, 죽을고비 등\n"
        "- Supernatural: 마법피격, 드래곤조우, 귀신목격, 차원이동 등\n"
        "Only count if it ACTUALLY HAPPENED to the player character.\n"
        "\n"
        "### PASSIVE SUGGESTION SYSTEM (AI-DRIVEN)\n"
        "Analyze the player's cumulative experiences and suggest a NEW passive/title if warranted.\n\n"
        
        "**When to suggest a passive:**\n"
        "- Repeated similar experiences (5+ times): 독에 자주 중독 → [독 내성]\n"
        "- Significant relationship milestone: 엘프와 10+ 우호 상호작용 → [엘프의 친구]\n"
        "- Survival of extreme situation: 죽을 고비 3회 → [구사일생]\n"
        "- Unique achievement: 드래곤 처치 → [용 사냥꾼]\n"
        "- Behavioral pattern: 항상 협상 선택 → [외교관의 혀]\n"
        "- World-specific adaptation: 던전 50층 돌파 → [심연의 주민]\n\n"
        
        "**Passive structure:**\n"
        "- name: Creative Korean title (e.g., '엘프의 친구', '불굴의 정신')\n"
        "- trigger: What earned this (e.g., '엘프와 우호적 상호작용 10회')\n"
        "- effect: Concrete in-world effect (e.g., '엘프에게 호감도 보너스, 엘프어 기초 이해')\n"
        "- category: 생존/전투/사회/초자연/지식/기타\n\n"
        
        "**Rules:**\n"
        "- Only suggest if TRULY earned through gameplay, not arbitrary\n"
        "- Be creative but grounded in what actually happened\n"
        "- Don't repeat passives player already has (check context)\n"
        "- Suggest at most 1 passive per analysis\n"
        "- Set to null if no passive is warranted\n\n"
        
        '  "PassiveSuggestion": {\n'
        '    "name": "패시브/칭호 이름",\n'
        '    "trigger": "획득 조건 설명",\n'
        '    "effect": "구체적 효과",\n'
        '    "category": "카테고리",\n'
        '    "reasoning": "왜 이 패시브를 제안하는지 간단 설명"\n'
        '  } OR null,\n'
    )

    # player_context가 있으면 추가 (중복 패시브 방지용)
    player_info = ""
    if player_context:
        player_info = f"### [PLAYER STATUS]\n{player_context}\n"

    user_prompt = (
        f"### [RULES]\n{rules}\n"
        f"### [QUESTS]\n{active_quests_text}\n"
        f"{player_info}"
        f"### [HISTORY]\n{history_text}\n"
        "Analyze the current state. Include temporal orientation for narrative continuity.\n"
        "Consider if player deserves a new passive based on their cumulative experiences."
    )
    
    contents = [
        types.Content(role="user", parts=[types.Part(text=user_prompt)])
    ]
    
    config = types.GenerateContentConfig(
        response_mime_type="application/json",
        temperature=0.2  # 약간의 창의성 허용
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
        system_instruction=system_instruction,
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
# [NPC TEXT EXTRACTOR] NPC 정보 추출 및 텍스트 분리
# =========================================================
async def extract_npcs_with_segments(
    client,
    model_id: str,
    lore_text: str
) -> Tuple[List[Dict[str, str]], str]:
    """
    [Logic Core] 로어에서 NPC 정보를 추출하고, NPC 설명을 제거한 순수 로어를 반환합니다.
    
    Args:
        client: Gemini 클라이언트
        model_id: 모델 ID
        lore_text: 로어 텍스트
    
    Returns:
        Tuple[NPC 리스트, NPC가 제거된 로어 텍스트]
        NPCs: [{"name": "...", "description": "..."}, ...]
        Cleaned Lore: NPC 설명이 제거된 순수 세계관 로어
    """
    user_prompt = (
        "You are a lore analyzer. Your task is to:\n"
        "1. Extract all NPCs/characters with their descriptions\n"
        "2. Identify which text segments describe NPCs\n"
        "3. Provide the lore text with NPC descriptions removed\n\n"
        "NPC descriptions include: character backstory, personality, appearance, "
        "motivations, relationships. Keep world-building, locations, history, "
        "factions, and plot information in the cleaned lore.\n\n"
        'Output JSON format:\n'
        '{\n'
        '  "npcs": [{"name": "character name", "description": "full description"}],\n'
        '  "cleaned_lore": "lore text with NPC descriptions removed"\n'
        '}\n\n'
        f"Lore Data:\n{lore_text}"
    )
    
    contents = [
        types.Content(role="user", parts=[types.Part(text=user_prompt)])
    ]
    config = types.GenerateContentConfig(
        response_mime_type="application/json",
        temperature=0.3
    )
    
    result = await api_call_with_retry(
        client, model_id, contents, config,
        operation_name="NPC Extraction with Segment Removal"
    )
    
    if result:
        data = safe_parse_json(result)
        npcs = data.get("npcs", [])
        cleaned_lore = data.get("cleaned_lore", lore_text)
        
        # 유효한 NPC만 필터링 (이름과 설명이 비어있지 않은 것만)
        if isinstance(npcs, list):
            valid_npcs = [
                {
                    "name": npc.get("name", "").strip(),
                    "description": npc.get("description", "설명 없음").strip()
                }
                for npc in npcs
                if isinstance(npc, dict) and npc.get("name") and npc.get("name").strip()
            ]
            return valid_npcs, cleaned_lore
    
    # 실패 시 원본 반환
    return [], lore_text


async def extract_npcs_only(
    client,
    model_id: str,
    lore_text: str
) -> List[Dict[str, str]]:
    """
    [Logic Core] 로어에서 NPC 정보만 추출합니다. (로어 자체는 수정하지 않음)
    PC(플레이어 캐릭터)는 제외합니다.

    Args:
        client: Gemini 클라이언트
        model_id: 모델 ID
        lore_text: 로어 텍스트

    Returns:
        NPC 리스트: [{"name": "...", "description": "..."}, ...]
    """
    user_prompt = """You are an NPC extractor for a TRPG lore document.

### TASK
Extract all NPCs (Non-Player Characters) from the lore.

### CRITICAL RULES
1. **EXCLUDE Player Characters (PC)** - Characters marked as:
   - [PLAYER CHARACTER]
   - "PC"
   - "PLAYER CHARACTER"
   - Characters that players control
   - Characters in sections labeled "PLAYER CHARACTER"

2. **INCLUDE only NPCs** - Characters that:
   - The game master/AI controls
   - Have defined personalities, roles, or descriptions
   - Interact with player characters
   - Are in sections labeled "NPC" or "NPCs"

3. **For each NPC, extract:**
   - name: Character name (including any aliases/titles)
   - description: Full description including species, role, personality, background, abilities

### OUTPUT FORMAT (JSON only)
{
  "npcs": [
    {"name": "캐릭터명", "description": "전체 설명..."},
    ...
  ],
  "excluded_pcs": ["PC로 판단하여 제외한 캐릭터명"]
}

### LORE DATA
""" + lore_text

    contents = [
        types.Content(role="user", parts=[types.Part(text=user_prompt)])
    ]
    config = types.GenerateContentConfig(
        response_mime_type="application/json",
        temperature=0.3
    )

    result = await api_call_with_retry(
        client, model_id, contents, config,
        operation_name="NPC Extraction (PC Excluded)"
    )

    if result:
        data = safe_parse_json(result)
        npcs = data.get("npcs", [])
        excluded_pcs = data.get("excluded_pcs", [])

        # 제외된 PC 로깅
        if excluded_pcs:
            logging.info(f"[NPC Extraction] Excluded PCs: {excluded_pcs}")

        # 유효한 NPC만 필터링 (이름과 설명이 비어있지 않은 것만)
        if isinstance(npcs, list):
            valid_npcs = [
                {
                    "name": npc.get("name", "").strip(),
                    "description": npc.get("description", "설명 없음").strip()
                }
                for npc in npcs
                if isinstance(npc, dict) and npc.get("name") and npc.get("name").strip()
            ]
            return valid_npcs

    # 실패 시 빈 리스트 반환
    return []


async def extract_pc_info(
    client,
    model_id: str,
    lore_text: str
) -> Optional[Dict[str, Any]]:
    """
    [Logic Core] 로어에서 PC(플레이어 캐릭터) 정보를 추출합니다.

    Args:
        client: Gemini 클라이언트
        model_id: 모델 ID
        lore_text: 로어 텍스트

    Returns:
        PC 정보가 있으면 dict, 없으면 None
        {
            "name": "케인 (Kain)",
            "appearance": "외형 설명...",
            "personality": "성격 설명...",
            "background": "배경 설명...",
            "known_info": ["알고 있는 정보1", "정보2"],
            "relationships": {"NPC이름": "관계 설명"},
            "species": "Human",
            "role": "Landlord"
        }
    """
    user_prompt = """You are a PC (Player Character) info extractor for TRPG lore.

### TASK
Extract the Player Character's information from the lore document.

### HOW TO IDENTIFY PC
Look for these markers:
- Section headers: "[PLAYER CHARACTER]", "PLAYER CHARACTER -", "PC:"
- Explicit statements: "controlled by player", "플레이어 캐릭터"
- Protection rules mentioning a specific character name

### IMPORTANT
- If NO Player Character is defined in the lore, return {"pc_found": false}
- Only extract info that is EXPLICITLY stated, do not invent details
- Some fields may be empty if not mentioned in lore

### OUTPUT FORMAT (JSON)
If PC found:
{
  "pc_found": true,
  "name": "캐릭터명",
  "species": "종족 (if mentioned)",
  "role": "역할/직업 (if mentioned)",
  "appearance": "외형 설명 (if mentioned)",
  "personality": "성격 설명 (if mentioned)",
  "background": "배경 스토리 (if mentioned)",
  "known_info": ["PC가 알고 있는 정보들"],
  "relationships": {
    "NPC이름": "관계 설명"
  },
  "secret_info": "다른 캐릭터가 모르는 PC의 비밀 (if mentioned)"
}

If NO PC found:
{
  "pc_found": false
}

### LORE DATA
""" + lore_text

    contents = [
        types.Content(role="user", parts=[types.Part(text=user_prompt)])
    ]
    config = types.GenerateContentConfig(
        response_mime_type="application/json",
        temperature=0.3
    )

    result = await api_call_with_retry(
        client, model_id, contents, config,
        operation_name="PC Info Extraction"
    )

    if result:
        data = safe_parse_json(result)
        if data.get("pc_found") == True:
            # pc_found 필드 제거하고 반환
            data.pop("pc_found", None)
            logging.info(f"[PC Extraction] Found PC: {data.get('name', 'Unknown')}")
            return data
        else:
            logging.info("[PC Extraction] No PC found in lore")

    return None


def parse_bulk_npcs_from_text(text: str) -> List[Dict[str, str]]:
    """
    텍스트 파일에서 여러 NPC를 파싱합니다.
    
    지원하는 형식:
    1. "이름: 설명" (각 줄)
    2. "# 이름\n설명" (마크다운 스타일)
    3. "이름 - 설명" (각 줄)
    4. JSON 형식: [{"name": "...", "description": "..."}]
    
    Args:
        text: NPC 정보가 포함된 텍스트
    
    Returns:
        NPC 리스트 [{"name": "...", "description": "..."}, ...]
    """
    npcs = []
    
    # JSON 형식 시도
    try:
        data = json.loads(text)
        if isinstance(data, list):
            for item in data:
                if isinstance(item, dict) and item.get("name"):
                    npcs.append({
                        "name": item.get("name", "").strip(),
                        "description": item.get("description", "").strip()
                    })
            if npcs:
                return npcs
    except json.JSONDecodeError:
        pass
    
    # 라인별 파싱
    lines = text.split('\n')
    current_npc_name = None
    current_npc_desc = []
    
    for line in lines:
        line = line.strip()
        if not line:
            # 빈 줄이면 현재 NPC 저장
            if current_npc_name and current_npc_desc:
                npcs.append({
                    "name": current_npc_name,
                    "description": " ".join(current_npc_desc)
                })
                current_npc_name = None
                current_npc_desc = []
            continue
        
        # 패턴 1: "이름: 설명" 또는 "이름 - 설명"
        if ':' in line or ' - ' in line:
            # 이전 NPC 저장
            if current_npc_name and current_npc_desc:
                npcs.append({
                    "name": current_npc_name,
                    "description": " ".join(current_npc_desc)
                })
                current_npc_name = None
                current_npc_desc = []
            
            # 새 NPC 파싱
            if ':' in line:
                parts = line.split(':', 1)
            else:
                parts = line.split(' - ', 1)
            
            current_npc_name = parts[0].strip()
            if len(parts) > 1:
                current_npc_desc = [parts[1].strip()]
        
        # 패턴 2: "# 이름" (마크다운 헤더)
        elif line.startswith('#'):
            # 이전 NPC 저장
            if current_npc_name and current_npc_desc:
                npcs.append({
                    "name": current_npc_name,
                    "description": " ".join(current_npc_desc)
                })
                current_npc_name = None
                current_npc_desc = []
            
            current_npc_name = line.lstrip('#').strip()
        
        # 현재 NPC의 설명 계속
        elif current_npc_name:
            current_npc_desc.append(line)
    
    # 마지막 NPC 저장
    if current_npc_name and current_npc_desc:
        npcs.append({
            "name": current_npc_name,
            "description": " ".join(current_npc_desc)
        })
    
    return npcs


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


# =========================================================
# OOC 명령 처리 및 AI 메모리 갱신
# =========================================================

def detect_ooc_command(text: str) -> Optional[Dict[str, str]]:
    """
    텍스트에서 OOC 명령을 감지합니다.
    
    지원 형식:
    - (OOC: 내용)
    - [OOC: 내용]
    - ((내용))
    - OOC: 내용
    
    Returns:
        {"type": "ooc", "content": "명령 내용"} 또는 None
    """
    import re
    
    patterns = [
        r'\(OOC[:\s]+(.+?)\)',      # (OOC: 내용)
        r'\[OOC[:\s]+(.+?)\]',      # [OOC: 내용]
        r'\(\((.+?)\)\)',            # ((내용))
        r'^OOC[:\s]+(.+)$',          # OOC: 내용 (줄 시작)
        r'\(메타[:\s]+(.+?)\)',      # (메타: 내용)
        r'\(시스템[:\s]+(.+?)\)',    # (시스템: 내용)
    ]
    
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            return {"type": "ooc", "content": match.group(1).strip()}
    
    return None


async def process_ooc_memory_update(
    client,
    model_id: str,
    ooc_content: str,
    current_memory: Dict[str, Any]
) -> Optional[Dict[str, Any]]:
    """
    OOC 명령을 해석하여 AI 메모리 업데이트 내용을 생성합니다.
    
    Args:
        client: Gemini 클라이언트
        model_id: 모델 ID
        ooc_content: OOC 명령 내용
        current_memory: 현재 AI 메모리 상태
    
    Returns:
        업데이트할 필드들 딕셔너리 또는 None
    """
    if not client:
        return None
    
    system_instruction = (
        "You are an AI Memory Manager for a TRPG system.\n"
        "The user has given an OOC (Out of Character) instruction to modify their character's memory.\n\n"
        
        "### CURRENT MEMORY STRUCTURE\n"
        "- appearance: 외형 설명\n"
        "- personality: 성격\n"
        "- background: 배경 스토리\n"
        "- relationships: {NPC이름: 관계설명}\n"
        "- passives: [패시브/칭호 이름들]\n"
        "- known_info: [알고 있는 정보들]\n"
        "- foreshadowing: [미해결 복선들]\n"
        "- normalization: {비일상요소: 적응상태}\n"
        "- notes: 자유 메모\n\n"
        
        "### YOUR TASK\n"
        "Parse the OOC instruction and determine what memory fields to update.\n"
        "Only return fields that need to be changed.\n\n"
        
        "### EXAMPLES\n"
        '- "리엘이랑 사이 안 좋아진 걸로" → {"relationships": {"리엘": "관계 악화, 서먹함"}}\n'
        '- "마법에 익숙해진 걸로 해줘" → {"normalization": {"마법": "이제 익숙함"}}\n'
        '- "봉인된 편지 복선으로 기억해둬" → {"foreshadowing": ["봉인된 편지의 비밀"]}\n'
        '- "외형에 흉터 추가해줘" → {"appearance": "...기존 외형 + 왼쪽 뺨에 흉터"}\n'
        '- "도적 길드 연락처 알게 됐어" → {"known_info": ["도적 길드 연락처"]}\n\n'
        
        "### OUTPUT FORMAT (JSON ONLY)\n"
        "{\n"
        '  "updates": { field: new_value, ... },\n'
        '  "message": "변경 사항 요약 (한국어)"\n'
        "}\n"
        "If the instruction is unclear or invalid, return:\n"
        '{"updates": null, "message": "이해하지 못했습니다. 다시 말씀해주세요."}'
    )
    
    user_prompt = (
        f"### CURRENT MEMORY\n{json.dumps(current_memory, ensure_ascii=False, indent=2)}\n\n"
        f"### OOC INSTRUCTION\n{ooc_content}\n\n"
        "Parse this instruction and return the memory updates."
    )
    
    contents = [
        types.Content(role="user", parts=[types.Part(text=user_prompt)])
    ]
    
    config = types.GenerateContentConfig(
        system_instruction=system_instruction,
        response_mime_type="application/json",
        temperature=0.1
    )
    
    result = await api_call_with_retry(
        client, model_id, contents, config,
        operation_name="OOC Memory Update"
    )
    
    if result:
        parsed = safe_parse_json(result)
        if parsed:
            return parsed
    
    return None


async def auto_update_ai_memory(
    client,
    model_id: str,
    history_text: str,
    current_memory: Dict[str, Any],
    nvc_result: Dict[str, Any]
) -> Optional[Dict[str, Any]]:
    """
    게임 진행에 따라 AI 메모리를 자동으로 갱신합니다.
    
    매 턴 호출되어 서사에서 중요한 변화를 감지하고 메모리에 반영합니다.
    
    Args:
        client: Gemini 클라이언트
        model_id: 모델 ID
        history_text: 최근 대화 히스토리
        current_memory: 현재 AI 메모리
        nvc_result: 좌뇌 분석 결과
    
    Returns:
        업데이트할 필드들 딕셔너리 또는 None
    """
    if not client:
        return None
    
    system_instruction = (
        "You are monitoring a TRPG session to update the player's AI memory.\n"
        "Based on recent events, determine if any memory fields need updating.\n\n"
        
        "### WATCH FOR\n"
        "1. **Relationship changes:** New NPC met, relationship improved/worsened\n"
        "2. **New information:** Secrets discovered, clues found\n"
        "3. **Passives/Titles earned:** Through repeated actions or achievements\n"
        "4. **Abnormal normalization:** Getting used to supernatural things\n"
        "5. **Foreshadowing:** Important hints that should be tracked\n\n"
        
        "### RULES\n"
        "- Only update if something ACTUALLY changed\n"
        "- Be conservative - don't update on minor events\n"
        "- Passives require significant repeated experience\n"
        "- Keep descriptions concise\n\n"
        
        "### OUTPUT FORMAT (JSON ONLY)\n"
        "{\n"
        '  "should_update": true/false,\n'
        '  "updates": { field: new_value } OR null,\n'
        '  "reason": "Why updating (or why not)"\n'
        "}"
    )
    
    user_prompt = (
        f"### CURRENT MEMORY\n{json.dumps(current_memory, ensure_ascii=False)}\n\n"
        f"### LEFT BRAIN ANALYSIS\n"
        f"Location: {nvc_result.get('CurrentLocation', 'Unknown')}\n"
        f"Observation: {nvc_result.get('Observation', 'N/A')}\n"
        f"Abnormal Elements: {nvc_result.get('AbnormalElements', [])}\n\n"
        f"### RECENT HISTORY\n{history_text[-2000:]}\n\n"  # 최근 2000자만
        "Determine if memory should be updated."
    )
    
    contents = [
        types.Content(role="user", parts=[types.Part(text=user_prompt)])
    ]
    
    config = types.GenerateContentConfig(
        system_instruction=system_instruction,
        response_mime_type="application/json",
        temperature=0.1
    )
    
    result = await api_call_with_retry(
        client, model_id, contents, config,
        operation_name="Auto Memory Update"
    )
    
    if result:
        parsed = safe_parse_json(result)
        if parsed and parsed.get("should_update") and parsed.get("updates"):
            return parsed
    
    return None


# =========================================================
# OOC 자연어 메모리 수정 (유저 요청)
# =========================================================

async def process_ooc_memory_edit(
    client,
    model_id: str,
    user_request: str,
    current_ai_memory: Dict[str, Any],
    current_participant_data: Dict[str, Any] = None
) -> Dict[str, Any]:
    """
    유저의 OOC 자연어 요청을 파싱하여 AI 메모리 수정 명령으로 변환합니다.
    
    Args:
        client: Gemini 클라이언트
        model_id: 모델 ID
        user_request: 유저의 OOC 요청 (예: "리엘이랑 사이 안 좋아진 걸로 해줘")
        current_ai_memory: 현재 AI 메모리 상태
        current_participant_data: 현재 참가자 데이터 (골드, 인벤토리, 상태이상)
    
    Returns:
        수정 명령 딕셔너리 또는 None
    """
    system_instruction = (
        "[AI Memory Editor - OOC Request Parser]\n"
        "Parse the user's natural language request and convert to memory edit commands.\n"
        "The user speaks Korean. Be generous in interpretation.\n\n"
        
        "### EDITABLE FIELDS (AI Memory)\n"
        "- appearance: 외모 설명 (string)\n"
        "- personality: 성격 (string)\n"
        "- background: 배경 스토리 (string)\n"
        "- relationships: NPC와의 관계 (dict: {NPC이름: 관계설명})\n"
        "- passives: 패시브/칭호 (list)\n"
        "- known_info: 알고 있는 정보 (list)\n"
        "- foreshadowing: 복선/떡밥 (list)\n"
        "- normalization: 비일상 적응 (dict: {요소: 적응상태})\n"
        "- notes: 자유 메모 (string)\n\n"
        
        "### EDITABLE FIELDS (Participant Data)\n"
        "- inventory: 소지품 (dict: {아이템이름: 수량})\n"
        "- economy.gold: 화폐 수량 (int)\n"
        "- economy.currency_name: 화폐 단위 이름 (string, 예: '골드', '은화', '크레딧')\n"
        "- status_effects: 상태이상 목록 (list: ['중독', '피로', ...])\n\n"
        
        "### OPERATIONS\n"
        "- set: 필드 값을 완전히 교체\n"
        "- add: 리스트/딕셔너리에 항목 추가 (숫자면 더하기)\n"
        "- remove: 리스트/딕셔너리에서 항목 제거 (숫자면 빼기)\n"
        "- update: 딕셔너리의 특정 키만 수정\n\n"
        
        "### INTERPRETATION EXAMPLES\n"
        "User: '리엘이랑 친해진 걸로' → relationships.update('리엘', '친밀한 동료')\n"
        "User: '독 내성 얻었어' → passives.add('독 내성')\n"
        "User: '드래곤 이제 익숙해' → normalization.update('드래곤', '이제 익숙함')\n"
        "User: '골드 500 줘' → economy.gold.add(500)\n"
        "User: '돈 200 잃었어' → economy.gold.remove(200)\n"
        "User: '화폐 단위 은화로' → economy.currency_name.set('은화')\n"
        "User: '마법검 얻었어' → inventory.add('마법검', 1)\n"
        "User: '포션 2개 썼어' → inventory.remove('포션', 2)\n"
        "User: '중독 상태야' → status_effects.add('중독')\n"
        "User: '피로 풀렸어' → status_effects.remove('피로')\n"
        "User: '상태이상 전부 해제' → status_effects.set([])\n\n"
        
        "### OUTPUT FORMAT (JSON)\n"
        "{\n"
        '  "understood": true,\n'
        '  "interpretation": "요청 해석 (간결하게)",\n'
        '  "edits": [\n'
        '    {"field": "...", "operation": "...", "key": "...(optional)", "value": "..."}\n'
        '  ],\n'
        '  "confirmation_message": "✅ 이모지와 함께 수정 내용 요약"\n'
        "}\n\n"
        
        "If unclear, return {\"understood\": false, \"interpretation\": \"이해 못한 이유\"}.\n"
        "Be generous - try to understand casual Korean expressions."
    )
    
    current_mem_str = json.dumps(current_ai_memory, ensure_ascii=False, indent=2)
    
    # participant 데이터 포함
    participant_str = ""
    if current_participant_data:
        participant_info = {
            "economy": current_participant_data.get("economy", {"gold": 0}),
            "inventory": current_participant_data.get("inventory", {}),
            "status_effects": current_participant_data.get("status_effects", [])
        }
        participant_str = f"### CURRENT PARTICIPANT DATA\n{json.dumps(participant_info, ensure_ascii=False, indent=2)}\n\n"
    
    user_prompt = (
        f"### CURRENT AI MEMORY\n{current_mem_str}\n\n"
        f"{participant_str}"
        f"### USER OOC REQUEST\n\"{user_request}\"\n\n"
        "Parse and generate edit commands."
    )
    
    contents = [
        types.Content(role="user", parts=[types.Part(text=user_prompt)])
    ]
    config = types.GenerateContentConfig(
        system_instruction=system_instruction,
        response_mime_type="application/json",
        temperature=0.1
    )
    
    result = await api_call_with_retry(
        client, model_id, contents, config,
        operation_name="OOC Memory Edit"
    )
    
    if result:
        parsed = safe_parse_json(result)
        if parsed and parsed.get("understood"):
            return parsed
    
    return None


def apply_memory_edits(
    ai_memory: Dict[str, Any], 
    edits: List[Dict],
    participant_data: Dict[str, Any] = None
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """
    파싱된 수정 명령을 AI 메모리와 참가자 데이터에 적용합니다.
    
    Args:
        ai_memory: 현재 AI 메모리
        edits: 수정 명령 리스트
        participant_data: 현재 참가자 데이터 (골드, 인벤토리, 상태이상)
    
    Returns:
        (수정된 AI 메모리, 수정된 참가자 데이터) 튜플
    """
    import copy
    updated_mem = copy.deepcopy(ai_memory)
    updated_participant = copy.deepcopy(participant_data) if participant_data else {}
    
    # participant 데이터 필드 목록
    participant_fields = {'inventory', 'economy', 'economy.gold', 'economy.currency_name', 'status_effects'}
    
    for edit in edits:
        field = edit.get("field", "")
        operation = edit.get("operation")
        value = edit.get("value")
        key = edit.get("key")
        
        # economy.gold, economy.currency_name 처리
        if field.startswith("economy."):
            sub_field = field.split(".", 1)[1]
            if "economy" not in updated_participant:
                updated_participant["economy"] = {"gold": 0}
            
            if sub_field == "gold":
                current_gold = updated_participant["economy"].get("gold", 0)
                if operation == "set":
                    updated_participant["economy"]["gold"] = int(value) if value else 0
                elif operation == "add":
                    updated_participant["economy"]["gold"] = current_gold + int(value)
                elif operation == "remove":
                    updated_participant["economy"]["gold"] = max(0, current_gold - int(value))
            elif sub_field == "currency_name":
                updated_participant["economy"]["currency_name"] = value
            continue
        
        # inventory 처리
        if field == "inventory":
            if "inventory" not in updated_participant:
                updated_participant["inventory"] = {}
            inv = updated_participant["inventory"]
            
            if operation == "set":
                updated_participant["inventory"] = value if isinstance(value, dict) else {}
            elif operation == "add":
                item_name = key if key else value
                amount = int(value) if key and isinstance(value, (int, str)) else 1
                inv[item_name] = inv.get(item_name, 0) + amount
            elif operation == "remove":
                item_name = key if key else value
                amount = int(value) if key and isinstance(value, (int, str)) else 1
                if item_name in inv:
                    inv[item_name] = max(0, inv[item_name] - amount)
                    if inv[item_name] <= 0:
                        del inv[item_name]
            continue
        
        # status_effects 처리
        if field == "status_effects":
            if "status_effects" not in updated_participant:
                updated_participant["status_effects"] = []
            effects = updated_participant["status_effects"]
            
            if operation == "set":
                updated_participant["status_effects"] = value if isinstance(value, list) else []
            elif operation == "add":
                if value and value not in effects:
                    effects.append(value)
            elif operation == "remove":
                if value in effects:
                    effects.remove(value)
            continue
        
        # AI 메모리 필드 처리 (기존 로직)
        # 필드가 없으면 기본값으로 초기화
        if field not in updated_mem:
            if operation == "set":
                # set 연산이면 새로 생성
                updated_mem[field] = value
                continue
            elif operation in ("add", "update"):
                # add/update면 적절한 기본값으로 초기화
                if isinstance(value, dict) or key:
                    updated_mem[field] = {}
                else:
                    updated_mem[field] = []
            else:
                continue
        
        current_value = updated_mem[field]
        
        if operation == "set":
            updated_mem[field] = value
            
        elif operation == "add":
            if isinstance(current_value, list):
                if value not in current_value:
                    current_value.append(value)
            elif isinstance(current_value, dict) and key:
                current_value[key] = value
                
        elif operation == "remove":
            if isinstance(current_value, list) and value in current_value:
                current_value.remove(value)
            elif isinstance(current_value, dict) and key and key in current_value:
                del current_value[key]
                
        elif operation == "update":
            if isinstance(current_value, dict) and key:
                current_value[key] = value
    
    return updated_mem, updated_participant


def apply_ai_memory_updates(
    channel_id: str,
    user_id: str,
    nvc_result: Dict[str, Any],
    domain_manager_module
) -> List[str]:
    """
    좌뇌 분석 결과에서 PlayerMemoryUpdate, SessionMemoryUpdate를 추출하여 적용합니다.
    
    Args:
        channel_id: 채널 ID
        user_id: 사용자 ID
        nvc_result: 좌뇌 분석 결과
        domain_manager_module: domain_manager 모듈 참조
    
    Returns:
        변경 알림 메시지 리스트
    """
    messages = []
    
    if not nvc_result:
        return messages
    
    # === AbnormalElements → normalization + abnormal_exposure 자동 업데이트 ===
    # 비일상 감지가 활성화된 경우에만 처리
    abnormal_detection_enabled = domain_manager_module.is_abnormal_detection_enabled(channel_id)
    abnormal_elements = nvc_result.get("AbnormalElements", [])
    if abnormal_elements and abnormal_detection_enabled:
        # AI 메모리 normalization 업데이트
        current_mem = domain_manager_module.get_ai_memory(channel_id, user_id)
        normalization = current_mem.get("normalization", {})
        
        # 참가자 데이터 abnormal_exposure 업데이트
        p_data = domain_manager_module.get_participant_data(channel_id, user_id)
        if not p_data:
            p_data = {}
        exposure = p_data.get("abnormal_exposure", {})
        
        for element in abnormal_elements:
            if not element:
                continue
            
            # 노출 카운트 업데이트
            if element not in exposure:
                exposure[element] = {"count": 0, "normality": 0}
            exposure[element]["count"] += 1
            count = exposure[element]["count"]
            
            # 적응도 계산 (간단 버전: 10회당 10%)
            normality = min(100, count * 10)
            exposure[element]["normality"] = normality
            
            # normalization 텍스트 업데이트 (단계별)
            if count == 1:
                normalization[element] = "처음 접함"
                messages.append(f"👁️ **비일상 접촉:** {element}")
            elif normality < 30:
                normalization[element] = "아직 낯섦"
            elif normality < 60:
                normalization[element] = "익숙해지는 중"
            elif normality < 100:
                normalization[element] = "거의 익숙함"
            else:
                if normalization.get(element) != "완전히 일상":
                    normalization[element] = "완전히 일상"
                    messages.append(f"🌙 **[{element}]** 이제 일상이 되었다.")
        
        # 저장
        current_mem["normalization"] = normalization
        domain_manager_module.update_ai_memory(channel_id, user_id, current_mem)
        
        p_data["abnormal_exposure"] = exposure
        domain_manager_module.save_participant_data(channel_id, user_id, p_data)
    
    # === ExperienceCounters 누적 ===
    experience_counters = nvc_result.get("ExperienceCounters", {})
    if experience_counters:
        p_data = domain_manager_module.get_participant_data(channel_id, user_id)
        if p_data:
            if "experience_counters" not in p_data:
                p_data["experience_counters"] = {}
            
            for exp_type, count in experience_counters.items():
                if exp_type and count:
                    p_data["experience_counters"][exp_type] = \
                        p_data["experience_counters"].get(exp_type, 0) + int(count)
            
            domain_manager_module.save_participant_data(channel_id, user_id, p_data)
    
    # === 비일상 발생 카운터 업데이트 ===
    # 시간 또는 장소가 변경되면 카운터 증가
    import random
    
    current_location = nvc_result.get("CurrentLocation", "")
    temporal = nvc_result.get("TemporalOrientation", {})
    current_time = temporal.get("continuity_from_previous", "")
    
    last_location = domain_manager_module.get_last_location(channel_id)
    last_time = domain_manager_module.get_last_time(channel_id)
    
    counter_increment = 0
    
    # 장소가 변경되면 +2
    if current_location and current_location != last_location:
        counter_increment += 2
        domain_manager_module.set_last_location(channel_id, current_location)
    
    # 시간이 변경되면 +1 (시간 경과)
    if current_time and current_time != last_time:
        counter_increment += 1
        domain_manager_module.set_last_time(channel_id, current_time)
    
    # 카운터 증가
    if counter_increment > 0:
        new_counter = domain_manager_module.increment_abnormal_trigger(channel_id, counter_increment)
        
        # 100이 되면 0.1% 확률로 비일상 이벤트 트리거
        if new_counter >= 100:
            if domain_manager_module.check_abnormal_trigger_conditions(channel_id, user_id):
                # 0.1% = 1/1000
                if random.randint(1, 1000) == 1:
                    messages.append("⚡ **[비일상 조짐]** 일상 속에서 무언가 이상한 기운이 감지됩니다...")
                    domain_manager_module.reset_abnormal_trigger(channel_id)
    
    # === 플레이어 메모리 업데이트 ===
    player_update = nvc_result.get("PlayerMemoryUpdate", {})
    if player_update:
        current_mem = domain_manager_module.get_ai_memory(channel_id, user_id)
        
        # appearance (외형) 업데이트
        if player_update.get("appearance"):
            current_mem["appearance"] = player_update["appearance"]
            messages.append(f"👤 **외형 업데이트:** {player_update['appearance'][:50]}...")
        
        # relationships 업데이트
        if player_update.get("relationships"):
            for name, desc in player_update["relationships"].items():
                if name and desc:
                    current_mem.setdefault("relationships", {})[name] = desc
                    messages.append(f"💞 **{name}**: {desc}")
        
        # passives 추가
        if player_update.get("passives"):
            for passive in player_update["passives"]:
                if passive and passive not in current_mem.get("passives", []):
                    current_mem.setdefault("passives", []).append(passive)
                    messages.append(f"🏆 **패시브 획득:** {passive}")
        
        # known_info 추가
        if player_update.get("known_info"):
            for info in player_update["known_info"]:
                if info and info not in current_mem.get("known_info", []):
                    current_mem.setdefault("known_info", []).append(info)
                    messages.append(f"💡 **새로운 정보:** {info}")
        
        # foreshadowing 추가
        if player_update.get("foreshadowing"):
            for fs in player_update["foreshadowing"]:
                if fs and fs not in current_mem.get("foreshadowing", []):
                    current_mem.setdefault("foreshadowing", []).append(fs)
                    messages.append(f"🔮 **복선:** {fs}")
        
        # normalization 업데이트
        if player_update.get("normalization"):
            for thing, status in player_update["normalization"].items():
                if thing and status:
                    current_mem.setdefault("normalization", {})[thing] = status
                    messages.append(f"🌓 **[{thing}]** {status}")
        
        # notes 업데이트
        if player_update.get("notes"):
            current_mem["notes"] = player_update["notes"]
        
        # 저장
        if player_update:
            domain_manager_module.update_ai_memory(channel_id, user_id, current_mem)
    
    # === 세션 메모리 업데이트 ===
    session_update = nvc_result.get("SessionMemoryUpdate", {})
    if session_update:
        current_session = domain_manager_module.get_session_ai_memory(channel_id)
        
        # world_summary 업데이트
        if session_update.get("world_summary"):
            current_session["world_summary"] = session_update["world_summary"]
            messages.append(f"🌐 **세계 상황 갱신:** {session_update['world_summary'][:50]}...")
        
        # current_arc 업데이트
        if session_update.get("current_arc"):
            current_session["current_arc"] = session_update["current_arc"]
        
        # active_threads 업데이트
        if session_update.get("active_threads"):
            for thread in session_update["active_threads"]:
                if thread and thread not in current_session.get("active_threads", []):
                    current_session.setdefault("active_threads", []).append(thread)
        
        # resolved_threads 처리 (active에서 제거)
        if session_update.get("resolved_threads"):
            for thread in session_update["resolved_threads"]:
                if thread in current_session.get("active_threads", []):
                    current_session["active_threads"].remove(thread)
                    messages.append(f"✅ **스토리 해결:** {thread}")
        
        # key_events 추가
        if session_update.get("key_events"):
            for event in session_update["key_events"]:
                if event and event not in current_session.get("key_events", []):
                    current_session.setdefault("key_events", []).append(event)
        
        # world_changes 추가
        if session_update.get("world_changes"):
            for change in session_update["world_changes"]:
                if change and change not in current_session.get("world_changes", []):
                    current_session.setdefault("world_changes", []).append(change)
                    messages.append(f"🌍 **세계 변화:** {change}")
        
        # npc_summaries 업데이트
        if session_update.get("npc_summaries"):
            for name, summary in session_update["npc_summaries"].items():
                if name and summary:
                    current_session.setdefault("npc_summaries", {})[name] = summary
        
        # 저장
        if session_update:
            domain_manager_module.update_session_ai_memory(channel_id, current_session)
    
    # === 플레이어 데이터 업데이트 (인벤토리, 골드, 상태이상) ===
    player_data_update = nvc_result.get("PlayerUpdate", {})
    if player_data_update:
        p_data = domain_manager_module.get_participant_data(channel_id, user_id)
        if p_data:
            updated = False
            
            # 인벤토리 추가
            if player_data_update.get("inventory_add"):
                if "inventory" not in p_data:
                    p_data["inventory"] = {}
                for item, amount in player_data_update["inventory_add"].items():
                    if item and amount:
                        p_data["inventory"][item] = p_data["inventory"].get(item, 0) + int(amount)
                        messages.append(f"🎒 **획득:** {item} x{amount}")
                        updated = True
            
            # 인벤토리 제거
            if player_data_update.get("inventory_remove"):
                if "inventory" not in p_data:
                    p_data["inventory"] = {}
                for item, amount in player_data_update["inventory_remove"].items():
                    if item and amount and item in p_data["inventory"]:
                        p_data["inventory"][item] = max(0, p_data["inventory"][item] - int(amount))
                        if p_data["inventory"][item] <= 0:
                            del p_data["inventory"][item]
                        messages.append(f"🎒 **사용/소실:** {item} x{amount}")
                        updated = True
            
            # 골드 변경
            if player_data_update.get("gold_change") is not None:
                if "economy" not in p_data:
                    p_data["economy"] = {"gold": 0}
                change = int(player_data_update["gold_change"])
                p_data["economy"]["gold"] = max(0, p_data["economy"].get("gold", 0) + change)
                if change > 0:
                    messages.append(f"💰 **획득:** +{change}")
                elif change < 0:
                    messages.append(f"💰 **지출:** {change}")
                updated = True
            
            # 상태이상 추가
            if player_data_update.get("status_add"):
                if "status_effects" not in p_data:
                    p_data["status_effects"] = []
                for status in player_data_update["status_add"]:
                    if status and status not in p_data["status_effects"]:
                        p_data["status_effects"].append(status)
                        messages.append(f"💫 **상태이상:** {status}")
                        updated = True
            
            # 상태이상 제거
            if player_data_update.get("status_remove"):
                if "status_effects" not in p_data:
                    p_data["status_effects"] = []
                for status in player_data_update["status_remove"]:
                    if status and status in p_data["status_effects"]:
                        p_data["status_effects"].remove(status)
                        messages.append(f"✨ **회복:** {status} 해제")
                        updated = True
            
            # 저장
            if updated:
                domain_manager_module.save_participant_data(channel_id, user_id, p_data)
    
    # === 플레이어 메모리 업데이트 (좌뇌 분석 결과에서) ===
    player_mem_update = nvc_result.get("PlayerMemoryUpdate", {})
    if player_mem_update:
        current_mem = domain_manager_module.get_ai_memory(channel_id, user_id)
        mem_updated = False
        
        # appearance (외형) 업데이트
        if player_mem_update.get("appearance"):
            current_mem["appearance"] = player_mem_update["appearance"]
            messages.append(f"👤 **외형 업데이트:** {player_mem_update['appearance'][:50]}...")
            mem_updated = True
        
        # relationships 업데이트
        if player_mem_update.get("relationships"):
            for name, desc in player_mem_update["relationships"].items():
                if name and desc:
                    current_mem.setdefault("relationships", {})[name] = desc
                    messages.append(f"💞 **{name}**: {desc}")
                    mem_updated = True
        
        # passives 추가
        if player_mem_update.get("passives"):
            for passive in player_mem_update["passives"]:
                if passive and passive not in current_mem.get("passives", []):
                    current_mem.setdefault("passives", []).append(passive)
                    messages.append(f"🏆 **패시브 획득:** {passive}")
                    mem_updated = True
        
        # known_info 추가
        if player_mem_update.get("known_info"):
            for info in player_mem_update["known_info"]:
                if info and info not in current_mem.get("known_info", []):
                    current_mem.setdefault("known_info", []).append(info)
                    messages.append(f"💡 **새로운 정보:** {info}")
                    mem_updated = True
        
        # foreshadowing 추가
        if player_mem_update.get("foreshadowing"):
            for fs in player_mem_update["foreshadowing"]:
                if fs and fs not in current_mem.get("foreshadowing", []):
                    current_mem.setdefault("foreshadowing", []).append(fs)
                    messages.append(f"🔮 **복선:** {fs}")
                    mem_updated = True
        
        # normalization 업데이트
        if player_mem_update.get("normalization"):
            for thing, status in player_mem_update["normalization"].items():
                if thing and status:
                    current_mem.setdefault("normalization", {})[thing] = status
                    messages.append(f"🌓 **[{thing}]** {status}")
                    mem_updated = True
        
        # companions 처리 (동행자/펫 - known_info에 저장)
        if player_mem_update.get("companions"):
            for companion in player_mem_update["companions"]:
                if companion:
                    companion_info = f"동행자: {companion}"
                    if companion_info not in current_mem.get("known_info", []):
                        current_mem.setdefault("known_info", []).append(companion_info)
                        messages.append(f"🐾 **동행자:** {companion}")
                        mem_updated = True
        
        # 저장
        if mem_updated:
            domain_manager_module.update_ai_memory(channel_id, user_id, current_mem)
    
    return messages


# =========================================================
# 세션 레벨 AI 메모리 자동 갱신
# =========================================================

async def auto_update_session_memory(
    client,
    model_id: str,
    history_text: str,
    current_session_memory: Dict[str, Any],
    nvc_result: Dict[str, Any]
) -> Optional[Dict[str, Any]]:
    """
    세션 레벨 AI 메모리를 자동으로 갱신합니다.
    
    Args:
        client: Gemini 클라이언트
        model_id: 모델 ID
        history_text: 최근 대화 히스토리
        current_session_memory: 현재 세션 AI 메모리
        nvc_result: 좌뇌 분석 결과
    
    Returns:
        업데이트할 필드들 딕셔너리 또는 None
    """
    if not client:
        return None
    
    system_instruction = (
        "You monitor a TRPG session to update the SESSION-LEVEL AI memory.\n"
        "This is for tracking world state, story arcs, and NPC information.\n\n"
        
        "### MEMORY FIELDS\n"
        "- world_summary: Overall world situation (1-2 sentences)\n"
        "- current_arc: Current story arc or main quest\n"
        "- active_threads: Ongoing plot threads (list)\n"
        "- resolved_threads: Completed plot threads (list)\n"
        "- key_events: Important events with day number (list)\n"
        "- foreshadowing: Unresolved plot hooks (list)\n"
        "- world_changes: Changes to the world state (list)\n"
        "- npc_summaries: {NPC name: brief description}\n"
        "- party_dynamics: Party relationship summary\n\n"
        
        "### RULES\n"
        "- Only update on SIGNIFICANT changes\n"
        "- Move completed threads from active to resolved\n"
        "- Track new NPCs encountered\n"
        "- Note world state changes (new dangers, political shifts)\n"
        "- Keep summaries brief and useful\n\n"
        
        "### OUTPUT FORMAT (JSON ONLY)\n"
        "{\n"
        '  "should_update": true/false,\n'
        '  "updates": {\n'
        '    "field_name": new_value,\n'
        '    ...\n'
        '  } OR null,\n'
        '  "reason": "Brief explanation"\n'
        "}"
    )
    
    user_prompt = (
        f"### CURRENT SESSION MEMORY\n{json.dumps(current_session_memory, ensure_ascii=False)}\n\n"
        f"### LEFT BRAIN ANALYSIS\n"
        f"Location: {nvc_result.get('CurrentLocation', 'Unknown')}\n"
        f"Risk: {nvc_result.get('LocationRisk', 'Unknown')}\n"
        f"Observation: {nvc_result.get('Observation', 'N/A')}\n"
        f"Threads: {nvc_result.get('TemporalOrientation', {}).get('active_threads', [])}\n\n"
        f"### RECENT HISTORY\n{history_text[-2000:]}\n\n"
        "Determine if session memory should be updated."
    )
    
    contents = [
        types.Content(role="user", parts=[types.Part(text=user_prompt)])
    ]
    
    config = types.GenerateContentConfig(
        system_instruction=system_instruction,
        response_mime_type="application/json",
        temperature=0.1
    )
    
    result = await api_call_with_retry(
        client, model_id, contents, config,
        operation_name="Session Memory Update"
    )
    
    if result:
        parsed = safe_parse_json(result)
        if parsed and parsed.get("should_update") and parsed.get("updates"):
            return parsed
    
    return None


async def process_full_memory_update(
    client,
    model_id: str,
    channel_id: str,
    user_id: str,
    history_text: str,
    nvc_result: Dict[str, Any],
    domain_manager_module
) -> List[str]:
    """
    플레이어 메모리 + 세션 메모리를 한 번에 갱신합니다.
    
    Args:
        client: Gemini 클라이언트
        model_id: 모델 ID
        channel_id: 채널 ID
        user_id: 유저 ID
        history_text: 최근 대화 히스토리
        nvc_result: 좌뇌 분석 결과
        domain_manager_module: domain_manager 모듈 참조
    
    Returns:
        갱신 메시지 리스트
    """
    messages = []
    
    # 1. 플레이어 메모리 갱신
    current_player_memory = domain_manager_module.get_ai_memory(channel_id, user_id) or {}
    
    player_update = await auto_update_ai_memory(
        client, model_id, history_text, current_player_memory, nvc_result
    )
    
    if player_update and player_update.get("updates"):
        updates = player_update["updates"]
        domain_manager_module.update_ai_memory(channel_id, user_id, updates)
        
        # 패시브 획득 알림
        if "passives" in updates:
            new_passives = updates["passives"]
            if isinstance(new_passives, list):
                for p in new_passives:
                    if p not in current_player_memory.get("passives", []):
                        messages.append(f"🏆 **패시브 획득:** {p}")
        
        # 관계 변화 알림
        if "relationships" in updates:
            for npc, status in updates["relationships"].items():
                old_status = current_player_memory.get("relationships", {}).get(npc, "")
                if status != old_status:
                    messages.append(f"💞 **관계 변화:** {npc} - {status}")
    
    # 2. 세션 메모리 갱신
    current_session_memory = domain_manager_module.get_session_ai_memory(channel_id) or {}
    
    session_update = await auto_update_session_memory(
        client, model_id, history_text, current_session_memory, nvc_result
    )
    
    if session_update and session_update.get("updates"):
        domain_manager_module.update_session_ai_memory(channel_id, session_update["updates"])
        
        # 복선 추가 알림
        if "foreshadowing" in session_update["updates"]:
            new_fs = session_update["updates"]["foreshadowing"]
            if isinstance(new_fs, list):
                for fs in new_fs:
                    if fs not in current_session_memory.get("foreshadowing", []):
                        messages.append(f"🔮 **복선 감지:** {fs}")
        
        # 스레드 해결 알림
        if "resolved_threads" in session_update["updates"]:
            new_resolved = session_update["updates"]["resolved_threads"]
            if isinstance(new_resolved, list):
                for thread in new_resolved:
                    if thread not in current_session_memory.get("resolved_threads", []):
                        messages.append(f"✅ **스토리 진행:** {thread} 해결!")
    
    return messages
