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
# [MASSIVE LORE PROCESSOR] 대용량 로어 처리 시스템
# 소설책 분량(10만자+)도 처리 가능
# =========================================================

# 상수 정의
CHUNK_SIZE = 15000  # 청크당 글자 수 (약 5000 토큰)
MAX_CHUNK_SUMMARY_LENGTH = 2000  # 청크 요약 최대 길이
FINAL_SUMMARY_TARGET = 8000  # 최종 요약 목표 길이


def split_text_into_chunks(text: str, chunk_size: int = CHUNK_SIZE) -> List[str]:
    """
    텍스트를 의미 단위로 분할합니다.
    문단/장 경계를 존중하여 분할합니다.
    """
    if len(text) <= chunk_size:
        return [text]
    
    chunks = []
    current_pos = 0
    
    while current_pos < len(text):
        # 청크 끝 위치 계산
        end_pos = min(current_pos + chunk_size, len(text))
        
        # 문단 경계 찾기 (청크 크기의 80% 이후에서)
        if end_pos < len(text):
            search_start = current_pos + int(chunk_size * 0.8)
            
            # 우선순위: 장 구분자 > 빈 줄 > 문장 끝
            chapter_markers = ['\n\n\n', '\n---\n', '\n***\n', '\n# ', '\n## ']
            best_break = -1
            
            for marker in chapter_markers:
                pos = text.find(marker, search_start, end_pos)
                if pos != -1:
                    best_break = pos + len(marker)
                    break
            
            # 장 구분자 없으면 문단 경계
            if best_break == -1:
                para_break = text.find('\n\n', search_start, end_pos)
                if para_break != -1:
                    best_break = para_break + 2
            
            # 문단 경계도 없으면 문장 끝
            if best_break == -1:
                for punct in ['. ', '.\n', '? ', '?\n', '! ', '!\n']:
                    pos = text.rfind(punct, search_start, end_pos)
                    if pos != -1:
                        best_break = pos + len(punct)
                        break
            
            if best_break != -1:
                end_pos = best_break
        
        chunk = text[current_pos:end_pos].strip()
        if chunk:
            chunks.append(chunk)
        
        current_pos = end_pos
    
    return chunks


async def compress_chunk(
    client,
    model_id: str,
    chunk_text: str,
    chunk_index: int,
    total_chunks: int
) -> str:
    """
    개별 청크를 압축합니다.
    """
    system_instruction = (
        f"[Lore Chunk Compressor - Part {chunk_index + 1}/{total_chunks}]\n"
        "Extract ONLY essential TRPG-relevant information:\n"
        "- Character names, roles, relationships\n"
        "- Location names and characteristics\n"
        "- Rules, powers, limitations\n"
        "- Plot-critical events\n"
        "- Conflicts and tensions\n\n"
        "Discard: Descriptions, emotions, internal monologue, prose style.\n"
        "Output: Bullet points, max 2000 characters."
    )
    
    user_prompt = f"### CHUNK {chunk_index + 1}\n{chunk_text}\n\nCompress to essentials."
    
    contents = [
        types.Content(role="user", parts=[types.Part(text=user_prompt)])
    ]
    config = types.GenerateContentConfig(temperature=0.1)
    
    result = await api_call_with_retry(
        client, model_id, contents, config,
        operation_name=f"Chunk Compression {chunk_index + 1}/{total_chunks}"
    )
    
    if result:
        # 최대 길이 제한
        return result[:MAX_CHUNK_SUMMARY_LENGTH]
    
    return f"[Chunk {chunk_index + 1} compression failed]"


async def merge_chunk_summaries(
    client,
    model_id: str,
    chunk_summaries: List[str]
) -> str:
    """
    청크 요약들을 최종 통합 요약으로 병합합니다.
    """
    combined = "\n\n---\n\n".join([
        f"[Part {i + 1}]\n{summary}" 
        for i, summary in enumerate(chunk_summaries)
    ])
    
    system_instruction = (
        "[THEORIA - Final Lore Synthesis]\n"
        "Merge all chunk summaries into ONE coherent sourcebook.\n\n"
        
        "### REQUIRED SECTIONS\n"
        "1. **WORLD OVERVIEW** (1-2 paragraphs): Setting, era, tone\n"
        "2. **CORE RULES**: Magic/tech/physics systems, hard limits\n"
        "3. **MAJOR FACTIONS**: Names, goals, relationships\n"
        "4. **KEY CHARACTERS**: Name, role, motivation (max 10)\n"
        "5. **LOCATIONS**: Important places and their features\n"
        "6. **ACTIVE CONFLICTS**: Current tensions and mysteries\n"
        "7. **SECRETS/SPOILERS**: Hidden information for GM\n\n"
        
        "### CONSTRAINTS\n"
        f"- Target length: {FINAL_SUMMARY_TARGET} characters\n"
        "- Remove ALL redundancy\n"
        "- Prioritize actionable TRPG information\n"
        "- Plain text, no markdown (except section headers)"
    )
    
    user_prompt = f"### CHUNK SUMMARIES\n{combined}\n\nSynthesize into final sourcebook."
    
    contents = [
        types.Content(role="user", parts=[types.Part(text=user_prompt)])
    ]
    config = types.GenerateContentConfig(temperature=0.2)
    
    result = await api_call_with_retry(
        client, model_id, contents, config,
        operation_name="Final Lore Synthesis"
    )
    
    if result:
        return result
    
    # 실패 시 청크 요약 단순 연결
    return "\n\n".join(chunk_summaries)


async def process_massive_lore(
    client,
    model_id: str,
    raw_lore_text: str,
    progress_callback=None
) -> Tuple[str, Dict[str, Any]]:
    """
    [MAIN FUNCTION] 대용량 로어를 계층적으로 처리합니다.
    
    Args:
        client: Gemini 클라이언트
        model_id: 모델 ID
        raw_lore_text: 원본 로어 텍스트 (제한 없음)
        progress_callback: 진행 상황 콜백 (async def callback(stage, current, total))
    
    Returns:
        (final_summary, metadata) 튜플
        - final_summary: 최종 압축된 로어
        - metadata: 처리 통계 정보
    """
    import time
    start_time = time.time()
    
    text_length = len(raw_lore_text)
    
    # 메타데이터 초기화
    metadata = {
        "original_length": text_length,
        "chunks_count": 0,
        "final_length": 0,
        "compression_ratio": 0,
        "processing_time": 0,
        "method": "single"  # single, chunked, hierarchical
    }
    
    # 1. 짧은 텍스트는 기존 방식 사용
    if text_length <= CHUNK_SIZE:
        if progress_callback:
            await progress_callback("compressing", 1, 1)
        
        result = await compress_lore_core(client, model_id, raw_lore_text)
        
        metadata["final_length"] = len(result)
        metadata["compression_ratio"] = round(text_length / max(len(result), 1), 2)
        metadata["processing_time"] = round(time.time() - start_time, 2)
        
        return result, metadata
    
    # 2. 대용량 텍스트는 청크 처리
    metadata["method"] = "chunked"
    
    # 청크 분할
    chunks = split_text_into_chunks(raw_lore_text, CHUNK_SIZE)
    metadata["chunks_count"] = len(chunks)
    
    if progress_callback:
        await progress_callback("splitting", len(chunks), len(chunks))
    
    # 청크별 압축
    chunk_summaries = []
    for i, chunk in enumerate(chunks):
        if progress_callback:
            await progress_callback("compressing", i + 1, len(chunks))
        
        summary = await compress_chunk(client, model_id, chunk, i, len(chunks))
        chunk_summaries.append(summary)
        
        # API 레이트 리밋 방지
        await asyncio.sleep(0.5)
    
    # 3. 청크 요약이 너무 많으면 계층적 병합
    if len(chunk_summaries) > 10:
        metadata["method"] = "hierarchical"
        
        # 5개씩 묶어서 중간 요약 생성
        mid_summaries = []
        batch_size = 5
        
        for i in range(0, len(chunk_summaries), batch_size):
            batch = chunk_summaries[i:i + batch_size]
            
            if progress_callback:
                await progress_callback("merging", i // batch_size + 1, 
                                        (len(chunk_summaries) + batch_size - 1) // batch_size)
            
            mid_summary = await merge_chunk_summaries(client, model_id, batch)
            mid_summaries.append(mid_summary)
            await asyncio.sleep(0.5)
        
        chunk_summaries = mid_summaries
    
    # 4. 최종 통합
    if progress_callback:
        await progress_callback("finalizing", 1, 1)
    
    final_summary = await merge_chunk_summaries(client, model_id, chunk_summaries)
    
    # 메타데이터 업데이트
    metadata["final_length"] = len(final_summary)
    metadata["compression_ratio"] = round(text_length / max(len(final_summary), 1), 2)
    metadata["processing_time"] = round(time.time() - start_time, 2)
    
    return final_summary, metadata


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
        "- `{\"tool\": \"Memo\", \"type\": \"Add\", \"content\": \"메모 내용\"}` — Important info: clues, NPC names, codes, locations, items acquired\n"
        "- `{\"tool\": \"Memo\", \"type\": \"Archive\", \"content\": \"기존 메모의 일부 텍스트\"}` — When memo becomes obsolete (item used, info no longer relevant)\n\n"
        
        "**NPC Actions:**\n"
        "- `{\"tool\": \"NPC\", \"type\": \"Add\", \"content\": \"이름: 설명\"}` — When new named NPC introduced\n\n"
        
        "**Examples:**\n"
        "- Player receives letter with mission → Quest Add\n"
        "- Player defeats boss mentioned in quest → Quest Complete\n"
        "- Player finds password \"1234\" → Memo Add\n"
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
        '  "Need": "Logical next step for Right Hemisphere",\n'
        '  "SystemAction": { "tool": "Quest/Memo/NPC", "type": "Add/Complete/Archive", "content": "..." } OR null\n'
        "}\n"
        "\n"
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
    current_ai_memory: Dict[str, Any]
) -> Dict[str, Any]:
    """
    유저의 OOC 자연어 요청을 파싱하여 AI 메모리 수정 명령으로 변환합니다.
    
    Args:
        client: Gemini 클라이언트
        model_id: 모델 ID
        user_request: 유저의 OOC 요청 (예: "리엘이랑 사이 안 좋아진 걸로 해줘")
        current_ai_memory: 현재 AI 메모리 상태
    
    Returns:
        수정 명령 딕셔너리 또는 None
    """
    system_instruction = (
        "[AI Memory Editor - OOC Request Parser]\n"
        "Parse the user's natural language request and convert to memory edit commands.\n"
        "The user speaks Korean. Be generous in interpretation.\n\n"
        
        "### EDITABLE FIELDS\n"
        "- appearance: 외모 설명 (string)\n"
        "- personality: 성격 (string)\n"
        "- background: 배경 스토리 (string)\n"
        "- relationships: NPC와의 관계 (dict: {NPC이름: 관계설명})\n"
        "- passives: 패시브/칭호 (list)\n"
        "- known_info: 알고 있는 정보 (list)\n"
        "- foreshadowing: 복선/떡밥 (list)\n"
        "- normalization: 비일상 적응 (dict: {요소: 적응상태})\n"
        "- notes: 자유 메모 (string)\n\n"
        
        "### OPERATIONS\n"
        "- set: 필드 값을 완전히 교체\n"
        "- add: 리스트/딕셔너리에 항목 추가\n"
        "- remove: 리스트/딕셔너리에서 항목 제거\n"
        "- update: 딕셔너리의 특정 키만 수정\n\n"
        
        "### INTERPRETATION EXAMPLES\n"
        "User: '리엘이랑 친해진 걸로' → relationships.update('리엘', '친밀한 동료')\n"
        "User: '독 내성 얻었어' → passives.add('독 내성')\n"
        "User: '드래곤 이제 익숙해' → normalization.update('드래곤', '이제 익숙함')\n"
        "User: '마왕 약점이 빛이래' → known_info.add('마왕의 약점은 빛')\n"
        "User: '비밀통로 잊어버렸어' → known_info.remove('비밀 통로...')\n"
        "User: '흉터 생긴 걸로' → appearance.set('...흉터가 있다')\n"
        "User: '엘프의 친구 칭호!' → passives.add('엘프의 친구')\n"
        "User: '그 편지 복선으로 기억해' → foreshadowing.add('봉인된 편지')\n\n"
        
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
    
    user_prompt = (
        f"### CURRENT AI MEMORY\n{current_mem_str}\n\n"
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


def apply_memory_edits(ai_memory: Dict[str, Any], edits: List[Dict]) -> Dict[str, Any]:
    """
    파싱된 수정 명령을 AI 메모리에 적용합니다.
    
    Args:
        ai_memory: 현재 AI 메모리
        edits: 수정 명령 리스트
    
    Returns:
        수정된 AI 메모리
    """
    import copy
    updated = copy.deepcopy(ai_memory)
    
    for edit in edits:
        field = edit.get("field")
        operation = edit.get("operation")
        value = edit.get("value")
        key = edit.get("key")
        
        if field not in updated:
            continue
        
        current_value = updated[field]
        
        if operation == "set":
            updated[field] = value
            
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
    
    return updated


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
    
    # === 플레이어 메모리 업데이트 ===
    player_update = nvc_result.get("PlayerMemoryUpdate", {})
    if player_update:
        current_mem = domain_manager_module.get_ai_memory(channel_id, user_id)
        
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
