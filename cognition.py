"""
Lorekeeper TRPG Bot - Cognition Module
Consolidates "Left Brain" functions: Analysis (Theoria) and Extraction (Logos).
Replaces: left_brain_analysis.py, left_brain_extraction.py
"""

import json
import logging
import random
import asyncio
from typing import Dict, Any, List, Optional, TypedDict, Union, Literal
import re
from google import genai
from google.genai import types

# =========================================================
# DATA MODELS (Type Definitions)
# =========================================================

class InputAnalysis(TypedDict):
    """
    [Theoria V3] Structural Analysis of User Input
    """
    Original: str
    Enhanced: str
    LogicTrace: List[str]  # e.g., ["Repair", "GapFill"]
    Plausibility: Literal["High", "Medium", "Low", "Impossible"]
    Momentum: Literal["Open", "Closed"]
    MetagamingDetected: bool
    Confidence: Literal["High", "Medium", "Low"]

class PsycheAxis(TypedDict):
    """[Theoria V3] Single Axis of Psyche State"""
    value: int          # -10 to +10 or specific scale
    descriptor: str     # e.g., "anx" (Anxiety), "flu" (Fluid)
    intensity: str      # e.g., "High", "Low"

class PsycheState(TypedDict):
    """[Theoria V3] 6-Axis Psychological State"""
    character_name: str
    mental: PsycheAxis  # Μ (Mental)
    soma: PsycheAxis    # Φ (Soma)
    relation: PsycheAxis # Ι (Relation)
    # Optional extended axes
    coping: Optional[PsycheAxis] = None
    eastern: Optional[PsycheAxis] = None
    image: Optional[str] = None

class WantDoCan(TypedDict):
    """[Dikastes V3] Action Simulation Model"""
    Want: str   # Intention
    Do: str     # Attempt
    Can: str    # Capability/Constraints
    ResultPrediction: Literal["Success", "Failure", "Complication"]
    Discrepancy: Optional[str] = None

class CognitiveContext(TypedDict):
    """[Logos V3] Consolidated Context for Analysis"""
    history_tail: str
    lore_chunks: List[str]
    rule_chunks: List[str]
    quest_status: List[str]
    inventory: List[str]
    psyche_states: Dict[str, PsycheState]


# Shared utilities from memory_system (Assuming this file remains external for now)
from memory_system import (
    api_call_with_retry, 
    safe_parse_json, 
    COGNITIVE_ARCHITECTURE_MODEL, 
    STATE_TRACKING_FORMAT, 
    TEMPORAL_ORIENTATION_PROTOCOL
)

logger = logging.getLogger("Cognition")

# =========================================================
# PART 1: CONTEXT ANALYSIS (THEORIA)
# =========================================================


# =========================================================
# PART 1: CONTEXT ANALYSIS & SELECTION (THEORIA - FLASH)
# =========================================================

# =========================================================
# PROMPT COMPONENTS (Modularized)
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
STEP 1 — OBSERVATION
What actually happened? State observable facts only.
- Strict noun linking: If user says "patient", verify WHO that is from context. If unknown, use "the patient". Do NOT guess.

STEP 2 — USER INTENT  
What is the user trying to achieve IMMEDIATELY? 
- Focus on the direct action (e.g., "Walk to chair"), NOT the ultimate goal (e.g., "Finish work"). 
- Do NOT Lookahead. Only analyze the current verb.

STEP 3 — OBSTACLES
What makes this difficult? Physical barriers, social resistance, time pressure, etc.

STEP 4 — RESOURCES & COLLABORATORS
Check [PLAYER STATUS] for "Companions".
- If user says "...with Clara" and Clara is a companion, she is a RESOURCE/HELPER.
STEP 1 — OBSERVATION: State observable facts only. Strict noun linking.
STEP 2 — PSYCHE SCAN: Analyze the psychological state of key actors (Mood, Soma, Relation).
STEP 3 — MEMORY TRIGGER: Does immediate sensory input echo a past memory?
STEP 4 — NARRATIVE CHAIN: Is the current flow OPEN (expanding) or CLOSED (ending)?
STEP 5 — USER INTENT: What is the user trying to achieve IMMEDIATELY? Not ultimate goal.
STEP 6 — OBSTACLES: Physical barriers, social resistance, time pressure.
STEP 7 — CONTEXT SELECTION: Find relevant quotes/rules.
</analysis_process>
"""

THEORIA_PSYCHE = """
<PSYCHE_LAYER>
## Character State Tracking (6-Axis PSYCHE Model)
For each NPC in the scene, output psychological state:
Format: Ψ{name}[Μ:mood±.affect.insight][Φ:obj±,assess][Ι:phase.open±]

Dimensions:
- Μ (Mental): Mood(eut/dep/anx/irr/elv/fea/ang) ± Intensity(1-5)
- Φ (Soma): Body signals(sweat/tremble/pallor/flush)
- Ι (Relation): Phase(orient/identity/explor/resolution) . Openness(guarded/cautious/open/vulnerable)

Example: Ψ{Clara}[Μ:anx+3.rst.par][Φ:swt+1,tns][Ι:idn.opn+2]
</PSYCHE_LAYER>
"""

THEORIA_MEMORY = """
<MEMORY_LAYER>
## Memory Alchemy (Trigger Detection)
Scan current input for sensory triggers that invoke character memories.
- Triggers: Smells, sounds, specific phrases, déjà vu.
- Echo: How the past serves the present emotion.
Output JSON: "memory_triggers": [{"trigger": "...", "character": "...", "echo": "..."}]
</MEMORY_LAYER>
"""

THEORIA_CHAIN = """
<CHAIN_LAYER>
## Narrative Chain Analysis
Evaluate narrative flow state:
1. Topic Lock: Is the current topic exhausted or still active?
2. Pending Decisions: Are NPCs hesitating or deciding?
3. Conclusion Proximity: 0-100% (How close to scene end?)
4. Chain Status: OPEN (Expanding) vs CLOSED (Resolving)
Output JSON: "narrative_chain": {"topic_lock": "...", "chain_status": "OPEN|CLOSED", ...}
</CHAIN_LAYER>
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
"InputAnalysis": {
    "Original": "...",
    "Enhanced": "Full reconstructed sentence",
    "Plausibility": "High/Low/Impossible",
    "LogicTrace": ["Repair", "GapFill"],
    "Momentum": "Open/Closed"
}
</input_decoding_protocol>
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

THEORIA_OUTPUT_FORMAT = """
<output_format>
Return valid JSON with these fields:

REQUIRED:
- InputAnalysis: {
    "Original": "...", "Enhanced": "...", 
    "Plausibility": "...", "LogicTrace": [], "Momentum": "..."
  }
- CurrentLocation: String
- LocationRisk: None/Low/Medium/High/Extreme
- TimeContext: String  
- SceneType: normal/combat/social/summary/intimate
- Observation: Macroscopic fact of what happened
- UserIntent: What user wants to achieve (Short summary)
- RelevantContext: Array of 3-5 relevant quotes
- TimeFlow: {"duration": "...", "ticks": N}

OPTIONAL:
- psyche_states: {"Name": "Ψ{...}..."}
- memory_triggers: [{"trigger": str, ...}]
- narrative_chain: {"chain_status": str, ...}
- PCImpersonationCheck: {"detected": bool, ...}
- Position: {"value": 0.0-1.0, "reason": "..."}
- Effect: {"value": 0.0-1.0, "reason": "..."}
</output_format>
"""

def get_system_instruction_flash(features: Dict[str, bool] = None) -> str:
    """
    [Theoria V3 Builder]
    Constructs the system instruction dynamically based on enabled features.
    """
    if features is None: features = {"psyche": True, "memory": True, "chain": True, "decoding": True}
    
    # Base Components
    components = [
        "<THEORIA role=\"Observer and Librarian\">",
        THEORIA_IDENTITY,
        THEORIA_PRINCIPLES,
        THEORIA_PC_CHECK,
        COGNITIVE_ARCHITECTURE_MODEL,
        STATE_TRACKING_FORMAT,
        TEMPORAL_ORIENTATION_PROTOCOL
    ]
    
    # [V3 Modules]
    if features.get("decoding", True):
        components.append(THEORIA_INPUT_DECODING)
        
    components.append(THEORIA_PROCESS)
    
    if features.get("psyche", True):
        components.append(THEORIA_PSYCHE)
    if features.get("memory", True):
        components.append(THEORIA_MEMORY)
    if features.get("chain", True):
        components.append(THEORIA_CHAIN)

    components.extend([
        THEORIA_POSITION_EFFECT,
        THEORIA_ASPECTS,
        THEORIA_OFFSCREEN,
        THEORIA_OUTPUT_FORMAT,
        "</THEORIA>"
    ])
    
    return "\n\n".join(components)

# Backward Compatibility (Deprecate later)
SYSTEM_INSTRUCTION_FLASH = get_system_instruction_flash()


async def analyze_context_flash(
    client: genai.Client,
    model_id: str,
    history_text: str,
    lore: str,
    rules: str,
    active_quests_text: str,
    notebook: str = "",
    player_context: str = "",
    existing_npc_attitudes: Optional[Dict[str, Dict]] = None
) -> Dict[str, Any]:
    """
    [THEORIA - FLASH V2.5]
    Implementation Request #1 v3 Compliant.
    """
    
    # Prepare Attitude Context
    attitude_context = ""
    if existing_npc_attitudes:
        attitude_lines = [
            f"- {name}: {data.get('attitude', 'neutral')} ({data.get('reason', '')})"
            for name, data in existing_npc_attitudes.items()
        ]
        attitude_context = "### EXISTING NPC ATTITUDES\n" + "\n".join(attitude_lines) + "\n\n"

    player_info = f"### [PLAYER STATUS]\n{player_context}\n" if player_context else ""

    # [V3 Refactor] Use Dynamic Prompt Builder
    features = {"psyche": True, "memory": True, "chain": True, "decoding": True}
    system_instruction = get_system_instruction_flash(features)
    
    user_prompt = (
        f"### [RULES]\n{rules}\n"
        f"### [QUESTS]\n{active_quests_text}\n"
        f"### [NOTEBOOK]\n{notebook}\n"
        f"{player_info}"
        f"### [HISTORY]\n{history_text}\n"
        f"### [LORE]\n{lore}\n" 
        f"{attitude_context}" 
        "Perform ENHANCED Theoria analysis:\n"
        "1. Decode User Input (Repair/GapFill)\n"
        "2. Scan Psyche (6-Axis)\n"
        "3. Check Memory Triggers\n"
        "4. Analyze Narrative Chain\n"
        "5. Select Context"
    )
    
    contents = [types.Content(role="user", parts=[types.Part(text=user_prompt)])]
    config = types.GenerateContentConfig(
        system_instruction=system_instruction, 
        response_mime_type="application/json", 
        temperature=0.1
    )
    
    result = await api_call_with_retry(client, model_id, contents, config, operation_name="Context Analysis (Flash)")
    if result:
        parsed = safe_parse_json(result)
        if parsed:
            # [Legacy Adapter]
            # Map V3 fields to V2 fields to prevent crashes until Phase 1-4
            if "Instincts" not in parsed: parsed["Instincts"] = "See Psyche"
            if "Values" not in parsed: parsed["Values"] = "See Psyche"
            return parsed
    
    # Fallback
    return {
        "CurrentLocation": "Unknown", "UserIntent": "Unknown",
        "RelevantContext": [], "Observation": "Analysis Failed",
        "InputAnalysis": {"Original": "Error", "Enhanced": "Analysis Failed", "Plausibility": "Low"}
    }


# =========================================================
# PART 2: ACTION JUDGMENT (DIKASTES - PRO)
# =========================================================

DIKASTES_IDENTITY = """
<identity>
You are the impartial Game Master.
You receive analyzed context from THEORIA and determine:
1. The Difficulty of the action
2. All applicable Modifiers  
3. What happens if the action fails (GM Move)
</identity>
"""

DIKASTES_PROCESS = """
<judgment_process>
STEP 1 — WANT/DO/CAN SIMULATION
- Want: What is the true intent? (Enhanced Input)
- Do: What is the specific attempt?
- Can: Is it plausible? (Check Theoria Plausibility)
  - If Plausibility = Impossible -> AUTO FAIL.
  - If Plausibility = Low -> DC must be HARD(60) or higher.

STEP 2 — BASE DIFFICULTY (0-100 spectrum)
STEP 3 — APPLY ASPECTS & MODIFIERS (FATE System)
- Check [SCENE ASPECTS] from Theoria.
- Apply Bonus (+10) or Penalty (-10) if Aspect is invoked/compelled.

STEP 4 — CALCULATE Final DC
STEP 5 — FAILURE CONSEQUENCE (GM Move)
- Use [POSITION] to determine severity of failure.
- Use [EFFECT] to determine scale of success.
</judgment_process>
"""

DIKASTES_WANT_DO_CAN = """
<want_do_can_model>
## ACTION SIMULATION (Want/Do/Can)
Determine the outcome availability before rolling dice.
1. **Want**: Defined by [USER INTENT] (Enhanced).
2. **Do**: The physical/social attempt.
3. **Can**: The capability check. Matches Player Stats/Lore.

**Logic Gate**:
- If `Plausibility` is "Impossible": The action fails before it starts. Output 'automatic_failure'.
- If `Plausibility` is "Low": The action is strained. Minimum DC is HARD (60).
- If `Plausibility` is "High": The action is solid. Proceed with standard DC.
</want_do_can_model>
"""

DIKASTES_DIFFICULTY = """
<difficulty_spectrum>
| Label | Range | Examples |
|-------|-------|----------|
| trivial | 0-15 | Opening door |
| easy | 16-30 | Simple cooking |
| normal | 31-50 | Pick simple lock |
| hard | 51-70 | Complex acrobatics |
| extreme | 71-90 | Expert disguise |
| legendary | 91-100 | Near-impossible |
</difficulty_spectrum>
"""

DIKASTES_GM_MOVES = """
<gm_moves>
When a roll fails, the world responds. Match severity to THEORIA's Position:
- Low Position (< 0.3): Minor consequence (Soft Move).
- Medium Position (0.4-0.6): Meaningful setback (Hard Move).
- High Position (> 0.7): Serious consequence (Irreversible/Lethal).

Types: [worse_position, resource_loss, unwanted_attention, hard_choice, truth_revealed, separation].
</gm_moves>
"""

DIKASTES_MODIFIERS = """
<modifier_rules>
| Type | Range |
|------|-------|
| Injury | -5 to -20 |
| Passive | +5 to +15 |
| Item | +5 to +30 |
| Environment | ±5 to ±15 |
| **Scene Aspect** | **±10 (Contextual)** |
</modifier_rules>

<fate_aspect_logic>
**CRITICAL**: Aspects are double-edged swords. Judge based on CONTEXT.
- If the Aspect *helps* the action (e.g. "Dark Alley" + Hiding) -> **INVOKE (+10)**.
- If the Aspect *hinders* the action (e.g. "Dark Alley" + Lockpicking) -> **COMPEL (-10)**.
- If unrelated, ignore.
</fate_aspect_logic>

<everyday_charm>
Even trivial actions deserve attention. "Small actions create ripples."
Include flavor modifiers even for standard tasks.
</everyday_charm>
"""

DIKASTES_OUTPUT_FORMAT = """
<output_format>
Return valid JSON:

REQUIRED (existing):
- ActionJudgment: {
    "action": "Summarized action",
    "difficulty": "trivial/easy/normal/hard/extreme",
    "difficulty_reason": "Why this difficulty",
    "modifiers": [{"name": "...", "value": N, "reason": "..."}]
  }
- SystemAction: {"tool": "...", "type": "...", "content": "..."} or null

OPTIONAL (new):
- GMMove: {"type": "...", "description": "What happens on failure"}
</output_format>
"""

def get_system_instruction_dikastes(features: Dict[str, bool] = None) -> str:
    """
    [Dikastes V3 Builder]
    Constructs the Judge prompt dynamically.
    """
    if features is None: features = {"want_do_can": True}
    
    components = [
        "<DIKASTES role=\"Impartial Judge\">",
        DIKASTES_IDENTITY,
        DIKASTES_PROCESS
    ]
    
    if features.get("want_do_can", True):
        components.append(DIKASTES_WANT_DO_CAN)
        
    components.extend([
        DIKASTES_DIFFICULTY,
        DIKASTES_GM_MOVES,
        DIKASTES_MODIFIERS,
        "<!-- ASPECTS_INJECTION_HERE -->",
        DIKASTES_OUTPUT_FORMAT,
        "</DIKASTES>"
    ])
    return "\n\n".join(components)

# Backward Compatibility
SYSTEM_INSTRUCTION_PRO_JUDGE = get_system_instruction_dikastes()

async def judge_action_pro(
    client: genai.Client,
    model_id: str,
    user_intent: str,
    observation: str,
    relevant_context: List[str],
    history_tail: str,
    input_analysis: Optional[Dict[str, Any]] = None,
    cognitive_data: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    """
    [DIKASTES - PRO V3.0]
    Powered by Want/Do/Can Model.
    """
    # Prepare V3 Logic
    features = {"want_do_can": True}
    base_intent = user_intent
    plausibility_info = ""
    aspect_info = ""
    position_info = ""
    
    if input_analysis:
        # Override intent with Enhanced version if available
        if input_analysis.get("Enhanced"):
            base_intent = input_analysis["Enhanced"]
            
        p_val = input_analysis.get("Plausibility", "Medium")
        p_reason = input_analysis.get("Reasoning", "None") 
        plausibility_info = f"### [PLAUSIBILITY CHECK]\nRating: {p_val}\nNote: {p_reason}\n"

        # [V3.5] Grandmaster DNA Injection (Aspects & Position)
        # We need to pass 'observation_result' features here ideally, 
        # but 'input_analysis' is the current pipe. 
        # Refactoring to accept kwargs or extracting from input_analysis if bundled.
        # Ideally, we should pass 'cognitive_data' dict.
        pass

    # [Patch] We assume input_analysis might carry these or we inject via 'relevant_context'
    # Actually, I will add a new argument to `judge_action_pro` in the next chunk,
    # but for now let's modify the signature to be robust.
    
    context_str = "\n".join(f"- {c}" for c in relevant_context) if relevant_context else "None"
    
    # Build Cognitive Context (Position/Aspects)
    if cognitive_data:
        pos = cognitive_data.get("Position", {})
        eff = cognitive_data.get("Effect", {})
        asp = cognitive_data.get("Aspects", [])
        
        position_info = (
            f"### [RISK & REWARD (BitD)]\n"
            f"- Position (Risk): {pos.get('value')} ({pos.get('reason')})\n"
            f"- Effect (Gain): {eff.get('value')} ({eff.get('reason')})\n"
        )
        if asp:
            aspect_info = f"### [SCENE ASPECTS (FATE)]\nConsider these tags for modifiers:\n" + ", ".join(f"[{a}]" for a in asp) + "\n"

    user_prompt = (
        f"### [SITUATION]\n{observation}\n"
        f"### [USER INTENT (Optimized)]\n{base_intent}\n"
        f"{plausibility_info}"
        f"{position_info}"
        f"{aspect_info}"
        f"### [RECENT CHAT]\n{history_tail}\n"
        f"### [SELECTED RULES & CONTEXT]\n{context_str}\n" 
        "Perform Dikastes judgment. Apply Want/Do/Can logic."
    )
    
    contents = [types.Content(role="user", parts=[types.Part(text=user_prompt)])]
    config = types.GenerateContentConfig(
        system_instruction=get_system_instruction_dikastes(features), 
        response_mime_type="application/json", 
        temperature=0.3
    )
    
    result = await api_call_with_retry(client, model_id, contents, config, operation_name="Action Judgment (Pro)")
    parsed = safe_parse_json(result)
    
    if parsed: return parsed
    
    return {"ActionJudgment": None, "SystemAction": None}


# =========================================================
# PART 3: DICE & UTIL
# =========================================================

def roll_dice(sides: int = 100) -> int:
    return random.randint(1, sides)

def determine_result(final_roll: int, dc: int) -> str:
    if final_roll >= dc + 30: return "critical_success"
    elif final_roll >= dc: return "success"
    elif final_roll >= dc - 20: return "partial"
    else: return "failure"

# [DEPRECATED] Judgment builders moved to UNE JudgmentEngine

# =========================================================
# PART 3: EXTRACTION (LOGOS)
# =========================================================

async def extract_all_updates(
    client: genai.Client, 
    model_id_flash: str, 
    player_input: str, 
    ai_response: str,
    # Contexts
    notebook: str = "",
    current_status: Optional[List[str]] = None,
    current_relationships: Optional[Dict[str, str]] = None, 
    current_companions: Optional[List[str]] = None,
    lore_npc_names: Optional[List[str]] = None, 
    scene_npc_names: Optional[List[str]] = None,
    current_passives: Optional[List[str]] = None, 
    current_quests: Optional[List[str]] = None, 
    current_memos: Optional[List[str]] = None,
    fermented_context: str = "",
    player_context: str = "",
    extraction_hints: Optional[Dict[str, bool]] = None
) -> Dict[str, Any]:
    
    # Default: Run ALL if no hints provided
    if extraction_hints is None:
        extraction_hints = {"physical": True, "social": True, "narrative": True, "quest": True}

    tasks = []
    task_keys = []

    if extraction_hints.get("physical", False):
        tasks.append(_extract_physical(client, model_id_flash, player_input, ai_response, notebook, current_status))
        task_keys.append("physical")
    
    if extraction_hints.get("social", False):
        tasks.append(_extract_social(client, model_id_flash, player_input, ai_response, current_relationships, current_companions, lore_npc_names, scene_npc_names))
        task_keys.append("social")

    if extraction_hints.get("narrative", False):
        tasks.append(_extract_narrative(client, model_id_flash, player_input, ai_response, current_passives, fermented_context, player_context))
        task_keys.append("narrative")

    if extraction_hints.get("quest", False):
        tasks.append(_extract_quest(client, model_id_flash, player_input, ai_response, current_quests, current_memos))
        task_keys.append("quest")

    # If nothing to extract
    if not tasks:
        return {"PlayerUpdate": None, "PlayerMemoryUpdate": None, "PassiveSuggestion": None, "AbnormalTrigger": None, "QuestUpdate": None}

    # Run selected in parallel
    results = await asyncio.gather(*tasks, return_exceptions=True)
    
    # Map results back to keys
    result_map = {}
    for key, res in zip(task_keys, results):
        result_map[key] = res if not isinstance(res, Exception) else {}

    phys: Dict[str, Any] = result_map.get("physical", {})
    soc: Dict[str, Any] = result_map.get("social", {})
    nar: Dict[str, Any] = result_map.get("narrative", {})
    qst: Dict[str, Any] = result_map.get("quest", {})
    
    # Sanitize Physical (Now Notebook + Gold + Status)
    p_upd = None
    if phys:
        def _safe_int(v):
            try:
                return int(str(v).replace(',', '').replace('+', '').strip())
            except (ValueError, TypeError):
                return 0
            
        p_upd = {
            "notebook_update": phys.get("notebook_update"), # [V5.1]
            "status_add": phys.get("status_add"), 
            "status_remove": phys.get("status_remove")
        }

    # Sanitize/Map Social (Relationships: String to Int for Nemesis System)
    rels_processed = {}
    if soc and soc.get("relationships"):
        rel_map = {
            "nemesis": -20, "hostile": -15, "enemy": -15, "unfriendly": -5,
            "neutral": 0, "friendly": 10, "buddy": 10, "loyal": 20, "devoted": 25,
            "적대": -15, "경계": -5, "친밀": 10, "충성": 20
        }
        for n, v in soc["relationships"].items():
            if isinstance(v, (int, float)):
                rels_processed[n] = int(v)
            else:
                # String to Int Mapping
                v_low = str(v).lower()
                matched = False
                for key, score in rel_map.items():
                    if key in v_low:
                        rels_processed[n] = score
                        matched = True
                        break
                if not matched:
                    rels_processed[n] = 0 # Default to neutral if unknown string
    
    # Consolidate
    return {
        "PlayerUpdate": p_upd,
        
        "PlayerMemoryUpdate": {
            "relationships": rels_processed if rels_processed else soc.get("relationships"), 
            "companions": soc.get("companions"), 
            "passives": nar.get("passives")
        } if soc or nar.get("passives") else None,
        
        "PassiveSuggestion": nar.get("passive_suggestion"),
        "AbnormalTrigger": nar.get("abnormal_trigger"),
        "AbnormalCategory": nar.get("abnormal_category"),
        "MentalSuggestion": nar.get("mental_suggestion"),
        "MentalDelta": nar.get("mental_delta", 0),
        "DoomDelta": nar.get("doom_delta", 0),
        
        "QuestUpdate": {
            "quest_add": qst.get("quest_add"), "quest_complete": qst.get("quest_complete")
        } if qst else None
    }

# Internal Extractors (Private)

async def _extract_physical(
    client: genai.Client, 
    model_id: str, 
    p_in: str, 
    ai_out: str, 
    notebook: str, 
    status: Optional[List[str]]
) -> Dict[str, Any]:
    sys = (
        "EXTRACT NOTEBOOK & PHYSICAL CHANGES.\n"
        "Return JSON with keys: notebook_update (string or null), status_add [list], status_remove [list].\n"
        "Principle: Maintain a concise persistent state of the player's inventory and relevant memos.\n"
        "\n### [NOTEBOOK MANAGEMENT RULES - CRITICAL]\n"
        "1. **ACQUISITION vs OBSERVATION**: Record items ONLY if the player physically takes, receives, or buys them. Simply 'seeing', 'identifying', or 'inspecting' an item does NOT grant ownership. Unless they actively 'take' it, do NOT add to [소지품].\n"
        "2. **LOSS & DESTRUCTION**: If an item is lost, stolen, or destroyed, REMOVE it from the Notebook.\n"
        "3. **CONSUMPTION**: If a consumable (food, potion, ammo) is used, update its quantity or REMOVE if empty.\n"
        "4. **STATE UPDATE**: If an item's condition changes (e.g. 'Sword' becomes 'Broken Sword'), update the description.\n"
        "5. **DE-CLUTTER (Memos)**: Proactively REMOVE resolved tasks or information that is no longer relevant (e.g., 'Reached the room' is done; remove it) to prevent information overload.\n"
        "6. **EXCLUSION (Transient Logs)**: Do NOT record one-off actions or movement logs that have no long-term impact.\n"
        "7. **HYGIENE (No Duplication)**: Do NOT re-list items/memos already present in the [Current Notebook] unless the quantity/status changes.\n"
        "\n### [DATA RULES]\n"
        "- **Currency**: Track currency based on setting.\n"
        "- **Deduplication**: If an item is given and taken in one turn, list it ONCE.\n"
        "- **Format**: ALWAYS maintain '— [소지품] —' and '— [메모] —' headers.\n"
        "\nExample Output:\n"
        '{"notebook_update": "— [소지품] —\\n- 50 Credits\\n- Dull Combat Knife\\n\\n— [메모] —\\n- Code for Vault: 1234", "status_add": [], "status_remove": []}'
    )
    ctx = f"Notebook Content:\n{notebook}\nStatus:{status}"
    usr = f"State:\n{ctx}\nIn:\n{p_in}\nAI:\n{ai_out}\nOutput FULL UPDATED Notebook JSON."
    return await _call_extract(client, model_id, sys, usr, "B-1 Notebook")

async def _extract_social(
    client: genai.Client, 
    model_id: str, 
    p_in: str, 
    ai_out: str, 
    rels: Optional[Dict[str, str]], 
    comps: Optional[List[str]], 
    lore_npcs: Optional[List[str]], 
    scene_npcs: Optional[List[str]]
) -> Dict[str, Any]:
    sys = (
        "EXTRACT SOCIAL CHANGES.\n"
        "Return JSON with keys: relationships {Name: Level}, companions [list].\n"
        "Rules: Deduplicate Names. Only output changes.\n"
        'Example: {"relationships": {"Arthur": "Friendly"}, "companions": ["Arthur"]}'
    )
    ctx = f"Rels:{rels}, Comps:{comps}, LoreNPCs:{lore_npcs}, SceneNPCs:{scene_npcs}"
    usr = f"State:\n{ctx}\nIn:\n{p_in}\nAI:\n{ai_out}\nOutput JSON."
    return await _call_extract(client, model_id, sys, usr, "B-2 Social")

async def _extract_narrative(
    client: genai.Client, 
    model_id: str, 
    p_in: str, 
    ai_out: str, 
    passives: Optional[List[str]], 
    fermented: str, 
    player_context: str = ""
) -> Dict[str, Any]:
    sys = (
        "EXTRACT NARRATIVE CHANGES.\n"
        "Return JSON with keys: passives [list], passive_suggestion {name, reason}, abnormal_trigger (string or null), abnormal_category (string or null), mental_suggestion (string or null).\n"
        "Rules: 'Passive' here means ANY PERMANENT CAPABILITY or TRAIT.\n"
        "Include:\n"
        "1. **Skills/Abilities**: Learned techniques (e.g. 'Fireball', 'Lockpicking', 'Swordsmanship').\n"
        "2. **Physical Traits**: Body mods, mutations, inherent stats (e.g. 'Cyber-Arm', 'Night Vision').\n"
        "3. **Mental Traits**: Personality quirks, learned knowledge (e.g. 'Iron Will', 'Chemistry').\n"
        "4. **Achievements**: Titles or major status (e.g. 'Dragonslayer').\n"
        "5. **HYGIENE**: Do NOT list passives already in the [Passives] list. Only return NEW ones.\n"
        "Abnormal Trigger Rules:\n"
        "- Identify Genre Shifts or Monsters appearing. **MUST BE IN ENGLISH**.\n"
        "- **abnormal_trigger**: The specific name of the anomaly (e.g., 'The Crimson Slime').\n"
        "- **abnormal_category**: The general species or type for the adaptation system (e.g., 'Slime', 'Machine', 'Ghost', 'Silence').\n"
        "- **mental_suggestion**: If the AI narration explicitly depicts the PC breaking down, panicking, or stabilizing, suggest a stage name (e.g. 'Panic', 'Calm'). Default null.\n"
        "- **mental_delta**: If the scene depicts significant mental recovery (rest, comfort, therapy) or trauma, provide an integer delta (e.g., +20 for relief, -5 for stress). Use +10~+30 for 'healing' scenes. Default 0.\n"
        "- **doom_delta**: If the scene depicts a reduction in global tension/threat (securing a safe zone, clearing an area, resting peacefully) or a spike in danger, provide an integer (e.g., -5 for relief, +2 for escalation). Default 0.\n"
        "- **CRITICAL**: CONSIDER THE CHARACTER'S BACKGROUND. Do NOT trigger for events that are routine for their profession.\n"
        "  - E.g., A Doctor seeing gore/wounds is NORMAL (No Trigger).\n"
        "  - E.g., A Soldier seeing battle is NORMAL (No Trigger).\n"
        "  - Only trigger if the event is truly shocking, supernatural, or fundamentally 'wrong' to THEM.\n"
        'Example: {"passives": ["Fireball"], "passive_suggestion": null, "abnormal_trigger": "Zombie Dragon", "abnormal_category": "Zombie", "mental_suggestion": "Panic", "mental_delta": -15, "doom_delta": 0}'
    )
    ctx = f"Passives:{passives}, PlayerContext:{player_context}, FermentedSnippet:{fermented[:2000]}"
    usr = f"State:\n{ctx}\nIn:\n{p_in}\nAI:\n{ai_out}\nOutput JSON."
    return await _call_extract(client, model_id, sys, usr, "B-3 Narrative")

async def _extract_quest(
    client: genai.Client, 
    model_id: str, 
    p_in: str, 
    ai_out: str, 
    quests: Optional[List[str]], 
    memos: Optional[List[str]]
) -> Dict[str, Any]:
    sys = (
        "EXTRACT QUEST CHANGES.\n"
        "Return JSON with keys: quest_add [list], quest_complete [list].\n"
        "Rules: precise quest strings.\n"
        "1. **ADD**: Only add NEW quests. Do not duplicate quests already in [Quests] list.\n"
        "2. **COMPLETE**: Mark as complete ONLY if explicitly resolved. Be precise with the string match.\n"
        "3. **Memos**: Managed via Notebook; DO NOT output them here.\n"
        'Example: {"quest_add": ["Find the key"], "quest_complete": []}'
    )
    ctx = f"Quests:{quests}"
    usr = f"State:\n{ctx}\nIn:\n{p_in}\nAI:\n{ai_out}\nOutput JSON."
    return await _call_extract(client, model_id, sys, usr, "B-4 Quest")

async def _call_extract(
    client: genai.Client, 
    model_id: str, 
    sys: str, 
    usr: str, 
    op_name: str
) -> Dict[str, Any]:
    try:
        cfg = types.GenerateContentConfig(response_mime_type="application/json", temperature=0.1)
        cnt = [types.Content(role="user", parts=[types.Part(text=f"{sys}\n\n{usr}")])]
        res = await api_call_with_retry(client, model_id, cnt, cfg, operation_name=op_name)
        if res: return safe_parse_json(res)
    except Exception as e:
        logger.warning(f"[{op_name}] Error: {e}")
    return {}

# =========================================================
# PART 4: GM COGNITION (REACT ENGINE)
# =========================================================

SYSTEM_INSTRUCTION_CRISIS = """
<CRISIS_JUDGE>
Analyze the current situation for FATAL RISKS.
Your job is to STOP the game if the player is about to die or face irreversible ruin, preventing "accidental" bad endings.

Criteria for CRISIS_HALT (Score >= 8):
1. **Lethality**: Incoming attack/situation will kill or permanently maim.
2. **No Return**: The decision made now is irreversible.
3. **Player Blindness**: The player seems unaware of the danger.

Output JSON:
{
    "crisis_score": 0-10,
    "reason": "Why is this dangerous?",
    "halt_signal": boolean
}
</CRISIS_JUDGE>
"""

SYSTEM_INSTRUCTION_NARRATIVE_FLOW = """
<NARRATIVE_PLANNER>
Analyze the narrative flow based on the "Chain Principle" and "Spotlight".

1. **Chain Principle**: Does the outcome CLOSE the loop (boring) or OPEN a new one (fun)?
2. **Spotlight**: Which character has been silent?

Output JSON:
{
    "chain_status": "OPEN" or "CLOSED",
    "narrative_hook": "Suggestion for opening a new loop",
    "spotlight_suggestion": "Ask Player B what they are doing"
}
</NARRATIVE_PLANNER>
"""

class GMCognition:
    """
    Skilled GM Brain implementing ReAct Loop.
    Observation -> Thought -> Action
    """
    def __init__(self, client: genai.Client, model_id: str, model_id_flash: str) -> None:
        self.client = client
        self.model_id = model_id
        self.model_id_flash = model_id_flash

    async def process_turn(
        self, 
        history_text: str, 
        lore: str, 
        rules: str, 
        quests: str, 
        player_context: str,
        user_input: str
    ) -> Dict[str, Any]:
        """
        Executes the GM ReAct Loop.
        """
        # 1. IDENTIFY ACTORS (Who is speaking?)
        actors = self._identify_actors(user_input, history_text)
        
        # 2. OBSERVATION (Theoria Flash)
        # Use existing Flash engine for observation
        observation_result = await analyze_context_flash(
            self.client, self.model_id_flash, 
            history_text, lore, rules, quests, 
            player_context=player_context
        )
        
        # 3. CRISIS CHECK & SYSTEM EVENT BYPASS
        is_system_event = user_input.startswith("[System Event]")
        
        # Crisis Check: Only run if risk seems high and NOT a system event
        crisis_result = {"halt_signal": False}
        if not is_system_event and observation_result.get("LocationRisk", "Low") in ["High", "Extreme"]:
            crisis_result = await self._evaluate_crisis_level(
                user_input, observation_result.get("Observation", "")
            )
        
        if crisis_result.get("halt_signal"):
            return {
                "type": "CRISIS_HALT",
                "reason": crisis_result.get("reason"),
                "observation": observation_result
            }

        # 4. JUDGMENT (Dikastes Pro)
        # Bypassed for System Events to reduce latency
        if is_system_event:
            judgment_result = {
                "ActionJudgment": {
                    "action": "Admin Action",
                    "result": "automatic_success",
                    "difficulty": "trivial",
                    "dc": 0,
                    "modifiers": {},
                    "difficulty_reason": "System/Opening Event"
                },
                "SystemAction": None
            }
        else:
            # [V3 Integration]
            input_analysis = observation_result.get("InputAnalysis")
            
            # [V3.5] Grandmaster DNA: Extract Position, Effect, Aspects
            cognitive_data = {
                "Position": observation_result.get("Position", {}),
                "Effect": observation_result.get("Effect", {}),
                "Aspects": observation_result.get("Aspects", [])
            }
            
            judgment_result = await judge_action_pro(
                self.client, self.model_id,
                observation_result.get("UserIntent", ""),
                observation_result.get("Observation", ""),
                observation_result.get("RelevantContext", []),
                history_text[-500:],
                input_analysis=input_analysis,
                cognitive_data=cognitive_data
            )

        # 5. NARRATIVE PLANNING (Man in the Mirror)
        # Simplified for System Events
        if is_system_event:
            flow_plan = {"chain_status": "OPEN", "narrative_hook": "Session Start"}
        else:
            flow_plan = await self._plan_narrative_flow(
                history_text[-1000:], 
                str(judgment_result.get("ActionJudgment"))
            )
        
        # Consolidate
        return {
            "type": "CONTINUE",
            "observation": observation_result,
            "judgment": judgment_result,
            "flow_plan": flow_plan,
            "actors": actors
        }

    def _identify_actors(self, user_input: str, history: str) -> List[str]:
        # Simple regex heuristic for now, can be improved with Named Entity Recognition later
        # Searching for names in brackets or standard RP formats
        potential_names = re.findall(r"([A-Z][a-z]+)", user_input)
        return list(set(potential_names))

    async def _evaluate_crisis_level(self, user_input: str, observation: str) -> Dict[str, Any]:
        """Runs the Crisis Judge prompt."""
        try:
            prompt = f"Situation: {observation}\nAction: {user_input}\nEvaluate Crisis Score."
            contents = [types.Content(role="user", parts=[types.Part(text=prompt)])]
            config = types.GenerateContentConfig(
                system_instruction=SYSTEM_INSTRUCTION_CRISIS,
                response_mime_type="application/json",
                temperature=0.0
            )
            res = await api_call_with_retry(self.client, self.model_id_flash, contents, config, "Crisis Check")
            return safe_parse_json(res) or {"halt_signal": False}
        except Exception:
            return {"halt_signal": False}

    async def _plan_narrative_flow(self, recent_history: str, outcome: str) -> Dict[str, Any]:
        """Runs the Narrative Planner prompt."""
        try:
            prompt = f"Recent History: {recent_history}\nOutcome: {outcome}\nPlan Narrative Flow."
            contents = [types.Content(role="user", parts=[types.Part(text=prompt)])]
            config = types.GenerateContentConfig(
                system_instruction=SYSTEM_INSTRUCTION_NARRATIVE_FLOW,
                response_mime_type="application/json",
                temperature=0.5
            )
            res = await api_call_with_retry(self.client, self.model_id_flash, contents, config, "Narrative Plan")
            return safe_parse_json(res) or {}
        except Exception:
            return {}
