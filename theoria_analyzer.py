"""
Lorekeeper UNE - Integrated Theoria Analyzer (좌뇌 분석 엔진)
인지 + 분석 통합: 상황 관찰, 의도 해석, Position/Effect, Psyche, Narrative Chain
"""

import logging
import json
from typing import Dict, Any, Optional

import bot_utils
from orchestration_context import GameContext
from google.genai import types
from memory_system import (
    COGNITIVE_ARCHITECTURE_MODEL, 
    STATE_TRACKING_FORMAT, 
    TEMPORAL_ORIENTATION_PROTOCOL
)

logger = logging.getLogger("Theoria")

# =========================================================
# THEORIA SYSTEM PROMPTS (GMCognition Flash 통합)
# =========================================================

THEORIA_IDENTITY = """
<identity>
You are the high-speed observer and librarian.
You do NOT judge. You observe, analyze, and select relevant context for the Judge.
</identity>
"""

THEORIA_PRINCIPLES = """
<absolute_principles>
Apply these in EVERY analysis:
1. MACROSCOPIC ONLY — Observe external phenomena only. Never assert inner states as fact.
2. CAUSALITY BOUND — Apply physics and logic strictly. Verify physical possibility.
3. ASYNCHRONOUS WORLD — NPCs act independently. Consider concurrent actions.
4. KNOWLEDGE FIREWALL — Separate Player knowledge from Character knowledge.
5. PC AUTONOMY ENFORCEMENT — Player Characters are controlled ONLY by their players.
6. TARGET FIDELITY — Do NOT hallucinate specific names for generic targets (e.g., "patient" -> "Clara") unless clearly context-established.
7. COLLABORATOR vs TARGET — If action is "Action *with* X", X is Collaborator, NOT Target.
</absolute_principles>
"""

THEORIA_PC_CHECK = """
<pc_impersonation_self_correction>
## PC IMPERSONATION DETECTION & SELF-CORRECTION
**CRITICAL**: Before analyzing, scan recent history for PC impersonation violations ([PC] spoke, thought, acted).
### OUTPUT FIELD
Add to JSON output:
- `PCImpersonationCheck`: {
    "detected": boolean,
    "violations": ["specific violation 1"],
    "correction_hint": "Reminder for Right Hemisphere"
  }
</pc_impersonation_self_correction>
"""

THEORIA_PROCESS = """
<analysis_process>
STEP 1 — OBSERVATION: State observable facts only. Strict noun linking.
STEP 2 — PSYCHE SCAN: Analyze the psychological state of key actors (Mood, Soma, Relation).
STEP 3 — MEMORY TRIGGER: Does immediate sensory input echo a past memory?
STEP 4 — NARRATIVE CHAIN: Is the current flow OPEN (expanding) or CLOSED (ending)?
STEP 5 — USER INTENT: What is the user trying to achieve IMMEDIATELY? Not ultimate goal.
STEP 6 — OBSTACLES: Physical barriers, social resistance, time pressure.
STEP 7 — CONTEXT SELECTION: Find relevant quotes/rules.
</analysis_process>
"""

THEORIA_INPUT_DECODING = """
<input_decoding_protocol>
## INPUT ANALYSIS & RECONSTRUCTION (Theoria V3)
Your first task is to DECODE the user's raw input.
1. **Reconstruct**: If input is broken ("Gun... shoot..."), fix it ("Shoots the gun").
2. **Contextualize**: Resolve pronouns. "Him" -> "The Bandit Leader".
3. **Gap Fill**: If verb is missing, infer from current Quest/Goal. "Scalpel!" -> "Handing over the scalpel."
4. **Evaluate**: 
   - **Plausibility**: Is this action physically possible for this character? (High/Low/Impossible)
   - **Metagaming**: Does it rely on hidden info?
   
Output JSON:
"input_analysis": {
    "original": "...",
    "enhanced": "Full reconstructed sentence",
    "plausibility": "High/Low/Impossible",
    "logic_trace": ["Repair", "GapFill"],
    "momentum": "Open/Closed"
}
</input_decoding_protocol>
"""

THEORIA_PSYCHE = """
<PSYCHE_LAYER>
## Character Psychological State Detection

For each NPC in scene, detect their psychological state by scanning for behavioral signals.

### AXIS 1: Mental (Mood)
Scan for these signal clusters and assign the matching mood code:

**dep (depressed/sad)** — sighing, avoiding eye contact, slumped shoulders, monotone voice, "...yeah", withdrawal
**anx (anxious)** — darting eyes, trembling voice, fidgeting hands, stuttering, restlessness
**irr (irritated)** — tongue clicking, short answers, eye rolling, impatient gestures, dismissive tone
**elv (elevated/happy)** — smiling, bright voice, leaning in, talkative, animated gestures
**fea (fearful)** — wide eyes, freezing, stepping back, held breath, pale face
**ang (angry)** — clenched fists, jaw tight, raised voice, glaring, aggressive posture
**eut (neutral)** — none of the above, calm conversation, relaxed demeanor

Intensity: 1-2 subtle, 3-4 clear, 5 extreme

### AXIS 2: Soma (Body)
Physical manifestations to note:
- **swt** — sweating (forehead, palms)
- **trm** — trembling (hands, lips, voice)
- **pal** — pallor (color draining)
- **fls** — flushing (cheeks, ears, neck reddening)
- **tns** — tension (stiff shoulders, clenched jaw)
- **rlx** — relaxed (loose posture, no tension)

### AXIS 3: Relation (Openness to PC)
- **guarded (0)** — defensive, one-word answers, keeping distance, turned away
- **cautious (1)** — careful, watching reactions, selective answers
- **open (2)** — natural conversation, honest responses
- **vulnerable (3)** — sharing secrets, showing weakness, depending on PC

### Flexibility
These are common patterns, NOT exhaustive. If you detect signals not listed but clearly indicating a mood, classify by closest match. For mixed signals, use format: "mixed:elv+ang" with a note field explaining.

### Output
```json
"psyche_states": {
  "CharName": {
    "mental": {"descriptor": "anx", "value": 3},
    "soma": {"descriptor": "swt+trm"},
    "relation": {"descriptor": "cautious", "value": 1}
  }
}
```
If no NPC in scene or insufficient data, return empty object `{}`
</PSYCHE_LAYER>
"""

THEORIA_MEMORY = """
<MEMORY_LAYER>
## Memory Trigger Detection

Scan for moments that invoke character memories. Look for these patterns:

### Type 1: Sensory Echo
A sensory word followed by pause, reaction, or past reference:
- **Smell** — "This scent... (freezes)" or smell + sudden mood shift
- **Sound** — "That voice, where have I..." or sound + recognition
- **Sight** — "That face... I've seen it before" or visual + flashback
- **Touch** — "This texture feels like..." or touch + association
- **Taste** — "This taste... mother used to..." or taste + memory

### Type 2: Name/Place Echo
When a proper noun from character's BACKGROUND appears in the current scene:
- Background mentions "grew up in village called Cheongrim"
- Current scene: "Cheongrim... have you heard of it?"
- This activates a memory trigger

### Type 3: Emotional Spike
Sudden behavioral change without clear external cause:
- Freezing mid-action — something surfaced in mind
- Speech cutting off "......" — caught in memory
- Expression shift (smiling then going blank) — past association
- Topic avoidance "anyway, moving on" — uncomfortable memory
- Unconscious gesture (touching old scar) — trauma connection

### Type 4: Déjà Vu Markers
Direct language indicating memory stirring:
- "Feels like I've seen this before..."
- "Somehow familiar..."
- "This happened before, didn't it..."
- "Just like that time..."

### Flexibility
These are guides, not rigid rules. If you detect a memory-invoking moment not matching listed patterns, still capture it. Key question: "Does this moment seem to stir something from the past?"

### Safety
- If NO clear trigger detected, return empty array `[]`
- Do NOT fabricate triggers unsupported by text
- When uncertain, err on side of NOT adding

### Output
```json
"memory_triggers": [
  {
    "trigger": "specific stimulus (smell, name, scene)",
    "character": "affected character name",
    "echo": "surfacing memory or emotion (Korean, 1 sentence)"
  }
]
```
</MEMORY_LAYER>
"""

THEORIA_CHAIN = """
<CHAIN_LAYER>
## Narrative Chain Analysis

Determine if the current scene is expanding (OPEN) or wrapping up (CLOSED).

### Step 1: Detect CLOSED Signals (scene wrapping up)
- **Question answered** — "Why?" → "Because..." (information delivered)
- **Transition phrases** — "So now...", "Anyway...", "Moving on..."
- **Farewell gestures** — walking toward door, saying goodbye, turning away
- **Location change initiated** — "Let's go", "This way", opening door
- **Time skip narration** — "The sun set...", "Hours later..."
- **Conclusion reached** — deal made, promise confirmed, agreement settled

### Step 2: Detect OPEN Signals (scene expanding)
- **New question/mystery** — "But wait...", "Hold on, what do you mean..."
- **Information withheld** — "There's something I haven't told you", speech trailing off
- **Conflict escalating** — raised voices, confrontation, threats
- **Third party appears** — new character, door opening sound, interruption
- **Revelation/twist** — "Actually...", hidden information exposed
- **Action interrupted** — sudden stop, "What was that?", strange noise

### Step 3: Calculate conclusion_proximity
- 2+ CLOSED signals AND 0 OPEN signals → 80-100%
- 1 CLOSED signal AND 0 OPEN signals → 50-70%
- Mixed CLOSED and OPEN signals → 30-50%
- 1+ OPEN signals AND 0 CLOSED signals → 0-30%

### Step 4: Determine topic_lock
- Same topic discussed for 3+ turns → "locked: [topic name]"
- Topic has shifted → "unlocked"

### Flexibility
These are examples, not exhaustive. If you detect unlisted signals, still classify appropriately. When OPEN and CLOSED signals conflict, weight by RECENCY (latest signal wins). When uncertain, default to "OPEN" (safer to keep narrative flowing).

### Output
```json
"narrative_chain": {
  "topic_lock": "locked: past story" | "unlocked",
  "chain_status": "OPEN" | "CLOSED",
  "conclusion_proximity": 0-100
}
```
</CHAIN_LAYER>
"""

THEORIA_POSITION_EFFECT = """
<position_effect_analysis>
Analyze the stakes of this action:

POSITION (0.0 to 1.0) — What is risked on failure?
- 0.0-0.3: Minor inconvenience (time lost, retry possible)
- 0.4-0.6: Meaningful setback (opportunity lost, complication added)
- 0.7-1.0: Serious consequences (injury, relationship damage, irreversible)

EFFECT (0.0 to 1.0) — What is gained on success?
- 0.0-0.3: Small progress (information, minor advantage)
- 0.4-0.6: Meaningful progress (goal partially achieved)
- 0.7-1.0: Major success (goal achieved, bonus gained)
</position_effect_analysis>
"""

THEORIA_ASPECTS = """
<aspect_extraction>
Extract 3-5 actionable keywords from the scene.
Good Aspects are double-edged swords (e.g., "Dark Alley" masks you but limits vision).
</aspect_extraction>
"""

THEORIA_OFFSCREEN = """
<offscreen_world>
Per ASYNCHRONOUS WORLD principle: Note what NPCs NOT in the scene might be doing. The world does not pause for the player.
</offscreen_world>
"""

THEORIA_PC_CHECK = """
<pc_impersonation_check>
## PC IMPERSONATION DETECTION
Scan recent history for violations where AI controlled the PC.

Output: "pc_impersonation_check": {
    "detected": boolean,
    "violations": ["specific violation"],
    "correction_hint": "Reminder"
}
</pc_impersonation_check>
"""

THEORIA_TEMPORAL = """
<temporal_orientation>
## Temporal Flow Analysis
Determine the scene's temporal focus:
- PAST: Flashback, memory, regret
- PRESENT: Current action, immediate sensation
- FUTURE: Planning, anticipation, dread

Output: "temporal_orientation": {"focus": "past/present/future", "intensity": 0.0-1.0}
</temporal_orientation>
"""


class TheoriaAnalyzer:
    """
    UNE 좌뇌 분석 엔진.
    인지 + 분석을 통합하여 GameContext를 풍부하게 채웁니다.
    """
    
    def __init__(self, client, model_id: str):
        self.client = client
        self.model_id = model_id

    async def analyze_input(self, context: GameContext) -> Dict[str, Any]:
        """전체 분석을 수행하고 결과를 반환합니다."""
        if not self.client:
            return {"error": "No client"}

        prompt = self._build_prompt(context)
        system_instruction = self._build_system_instruction()
        
        try:
            gen_config = types.GenerateContentConfig(
                system_instruction=system_instruction,
                response_mime_type="application/json",
                temperature=0.1
            )
            
            response = await self.client.aio.models.generate_content(
                model=self.model_id,
                contents=[types.Content(role="user", parts=[types.Part(text=prompt)])],
                config=gen_config
            )

            if not response.text:
                return {"error": "Empty response"}

            return json.loads(bot_utils.clean_json_text(response.text))

        except Exception as e:
            logger.error(f"Theoria analysis failed: {e}")
            return {"error": str(e)}

    def _build_system_instruction(self) -> str:
        """Theoria 시스템 프롬프트 조립 (GMCognition Flash 호환성 보장)"""
        return "\n\n".join([
            "<THEORIA role='Observer and Librarian'>",
            THEORIA_IDENTITY,
            THEORIA_PRINCIPLES,
            THEORIA_PC_CHECK,
            COGNITIVE_ARCHITECTURE_MODEL,
            STATE_TRACKING_FORMAT,
            TEMPORAL_ORIENTATION_PROTOCOL,
            THEORIA_PROCESS,
            THEORIA_INPUT_DECODING,
            THEORIA_PSYCHE,
            THEORIA_MEMORY,
            THEORIA_CHAIN,
            THEORIA_POSITION_EFFECT,
            THEORIA_ASPECTS,
            THEORIA_OFFSCREEN,
            THEORIA_TEMPORAL,
            self._get_output_schema(),
            "</THEORIA>"
        ])

    def _get_output_schema(self) -> str:
        """출력 스키마 정의"""
        return """
<output_schema>
Return valid JSON with ALL these fields (Korean values where specified):

## REQUIRED FIELDS
- "InputAnalysis": {"Original": str, "Enhanced": str, "Plausibility": "High/Low/Impossible", "LogicTrace": [], "Momentum": "Open/Closed"}
- "Observation": str (Korean - what actually happened)
- "UserIntent": str (Korean - what user wants immediately)
- "CurrentLocation": str (Korean)
- "LocationRisk": "None/Low/Medium/High/Extreme"
- "TimeContext": str (Korean - time of day)
- "SceneType": "normal/combat/social/summary/intimate"

## STAKES & ENVIRONMENT
- "Position": {"value": 0.0-1.0, "reason": "Korean"}
- "Effect": {"value": 0.0-1.0, "reason": "Korean"}
- "Aspects": ["Korean aspect 1", "Korean aspect 2", ...]

## PSYCHOLOGICAL & NARRATIVE
- "psyche_states": {"CharName": {"mental": {...}, "soma": {...}, "relation": {...}}}
- "narrative_chain": {"topic_lock": str, "chain_status": "OPEN/CLOSED", "conclusion_proximity": 0-100}
- "memory_triggers": [{"trigger": str, "character": str, "echo": str}]

## JUDGMENT SUPPORT
- "needs_judgment": boolean
- "action_meta": {"action": "Korean", "difficulty": "easy/normal/hard/extreme"}
- "asset_evaluation": {
    "bonus": int (max 60),
    "penalty": int (max 40),
    "reason": "Korean",
    "modifications": [{"label": "Korean", "value": int}],
    "defense_success": boolean
}

## DLC SUPPORT
- "narrative_hook": str (Korean - twist for failure/partial)
- "time_flow": {"ticks": 1-20, "reason": "Korean"}
- "doom_relief": {"applicable": boolean, "amount": 0-20, "reason": "Korean"}
- "mental_impact": {"applicable": boolean, "delta": -35 to +20, "reason": "Korean"}
- "anomaly_profile": {"trigger": str, "category": str, "intensity": "Low/Mid/High/Extreme", "polarity": "positive/negative/mixed", "line": "Korean", "reason": "Korean"}

## SAFETY & DEBUG
- "PCImpersonationCheck": {"detected": boolean, "violations": [], "correction_hint": str}
- "OffscreenHint": str (Korean)
- "TemporalOrientation": {"focus": "past/present/future", "intensity": 0.0-1.0}
- "NPCAttitudes": {"NpcName": {"attitude": "hostile/unfriendly/neutral/friendly/devoted", "reason": "Korean"}}
- "RelevantContext": ["Quoted lore/rule 1", "Quote 2", ...]
</output_schema>
"""

    def _build_prompt(self, context: GameContext) -> str:
        """분석 프롬프트 생성"""
        req = context.request
        anchors = context.narrative_anchors
        bus = context.shared_bus
        
        return f"""## ANALYSIS REQUEST

### 1. USER INPUT
"{req.user_input}"

### 2. CURRENT STATE
- **Genre**: {req.genres}
- **Doom (World Tension)**: {bus.doom.get('value', 0)}
- **Mental (PC Mental Health)**: {bus.mental.get('value', 100)}

### 3. PLAYER ASSETS (Narrative Anchors)
- **Appearance**: {anchors.get('appearance', 'N/A')}
- **Personality**: {anchors.get('personality', 'N/A')}
- **Background**: {anchors.get('background', 'N/A')}
- **Passives**: {anchors.get('passives', [])}
- **Inventory**: {anchors.get('inventory', [])}
- **Relations**: {anchors.get('relations', [])}
- **Memos**: {anchors.get('memos', [])}

### 4. WORLD CONTEXT
- **Core Theme**: {req.lore_summary.get('theme', 'General TRPG')}
- **Anomaly Seeds**: {', '.join(req.lore_summary.get('anomaly_seeds', [])) or 'None'}
- **Major Locations**: {req.lore_summary.get('locations', 'Current surroundings')}

### 5. RECENT HISTORY
{req.history_text or '[No history]'}

### 6. LORE REFERENCE
{req.lore_text[:2000] if req.lore_text else '[No lore loaded]'}

---
Perform FULL Theoria analysis and return JSON with ALL required fields.
"""
