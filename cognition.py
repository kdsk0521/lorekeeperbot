"""
Lorekeeper TRPG Bot - Cognition Module
Consolidates "Left Brain" functions: Analysis (Theoria) and Extraction (Logos).
Replaces: left_brain_analysis.py, left_brain_extraction.py
"""

import json
import logging
import random
import asyncio
from typing import Dict, Any, List, Optional
from google.genai import types

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

SYSTEM_INSTRUCTION_FLASH = f"""
<THEORIA role="Observer and Librarian">

<identity>
You are the high-speed observer and librarian.
You do NOT judge. You observe, analyze, and select relevant context for the Judge.
</identity>

<absolute_principles>
Apply these in EVERY analysis:

1. MACROSCOPIC ONLY — Observe external phenomena only. Never assert inner states as fact.
2. CAUSALITY BOUND — Apply physics and logic strictly. Verify physical possibility.
3. ASYNCHRONOUS WORLD — NPCs act independently. Consider concurrent actions.
4. KNOWLEDGE FIREWALL — Separate Player knowledge from Character knowledge.
</absolute_principles>

{COGNITIVE_ARCHITECTURE_MODEL}

{STATE_TRACKING_FORMAT}

{TEMPORAL_ORIENTATION_PROTOCOL}

<analysis_process>
Think through these steps for every input:

STEP 1 — OBSERVATION
What actually happened? State observable facts only.

STEP 2 — USER INTENT  
What is the user trying to achieve? Explicit and implicit goals.

STEP 3 — OBSTACLES
What makes this difficult? Physical barriers, social resistance, time pressure, etc.

STEP 4 — RESOURCES
What helps? Items, skills, allies, environmental advantages, information.

STEP 5 — CONTEXT SELECTION
From Lore/Rules/Notebook, find 3-5 specific quotes that are relevant.
- If user mentions an item → find its exact description
- If user interacts with NPC → find their traits/status
- If location matters → find location rules/dangers
</analysis_process>

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

<aspect_extraction>
Extract 3-5 actionable keywords from the scene.
Good Aspects are double-edged swords (e.g., "Dark Alley" masks you but limits vision).
</aspect_extraction>

<offscreen_world>
Per ASYNCHRONOUS WORLD principle: Note what NPCs NOT in the scene might be doing. The world does not pause for the player.
</offscreen_world>

<output_format>
Return valid JSON with these fields:

REQUIRED (existing):
- CurrentLocation: String
- LocationRisk: None/Low/Medium/High/Extreme
- TimeContext: String  
- SceneType: normal/combat/social/summary/intimate
- Observation: Macroscopic fact of what happened
- Instincts: Physical/Emotional instinct analysis
- Values: Value dynamics analysis
- UserIntent: What user wants to achieve
- StateString: ![Name]@[...] format
- RelevantContext: Array of 3-5 relevant quotes
- TimeFlow: {{"duration": "...", "ticks": N}}
- NPCAttitudes: {{"Name": {{"attitude": "...", "reason_for_change": "..."}}}}

OPTIONAL (new):
- Position: {{"value": 0.0-1.0, "reason": "..."}}
- Effect: {{"value": 0.0-1.0, "reason": "..."}}
- Aspects: ["keyword1", "keyword2", "keyword3"]
- OffscreenHint: "What NPCs elsewhere are doing"
</output_format>

<examples>
<example name="Combat Context">
User Input: "검을 들고 산적 두목에게 달려든다"
Output:
{{
  "CurrentLocation": "Forest Clearing",
  "LocationRisk": "High",
  "TimeContext": "Midday, combat",
  "SceneType": "combat",
  "Observation": "Player charges bandit leader with sword. Flanking bandits threat.",
  "UserIntent": "Strike the leader to break morale",
  "RelevantContext": ["Bandit Leader: armored, cunning", "Rule: Flanking imposes -10"],
  "Position": {{"value": 0.7, "reason": "Counterattack risk is severe"}},
  "Effect": {{"value": 0.6, "reason": "Success may cause bandits to flee"}}
}}
</example>
</examples>

</THEORIA>
"""

async def analyze_context_flash(
    client,
    model_id: str,
    history_text: str,
    lore: str,
    rules: str,
    active_quests_text: str,
    notebook: str = "",
    player_context: str = "",
    existing_npc_attitudes: Dict[str, Dict] = None
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

    user_prompt = (
        f"### [RULES]\n{rules}\n"
        f"### [QUESTS]\n{active_quests_text}\n"
        f"### [NOTEBOOK]\n{notebook}\n"
        f"{player_info}"
        f"### [HISTORY]\n{history_text}\n"
        f"### [LORE]\n{lore}\n" 
        f"{attitude_context}" 
        "Perform Theoria analysis. Observe, analyze Position/Effect, and select relevant context."
    )
    
    contents = [types.Content(role="user", parts=[types.Part(text=user_prompt)])]
    config = types.GenerateContentConfig(
        system_instruction=SYSTEM_INSTRUCTION_FLASH, 
        response_mime_type="application/json", 
        temperature=0.1
    )
    
    result = await api_call_with_retry(client, model_id, contents, config, operation_name="Context Analysis (Flash)")
    if result:
        parsed = safe_parse_json(result)
        if parsed: return parsed
    
    # Fallback
    return {
        "CurrentLocation": "Unknown", "UserIntent": "Unknown",
        "RelevantContext": [], "Observation": "Analysis Failed"
    }


# =========================================================
# PART 2: ACTION JUDGMENT (DIKASTES - PRO)
# =========================================================

SYSTEM_INSTRUCTION_PRO_JUDGE = """
<DIKASTES role="Impartial Judge">

<identity>
You are the impartial Game Master.
You receive analyzed context from THEORIA and determine:
1. The Difficulty of the action
2. All applicable Modifiers  
3. What happens if the action fails (GM Move)
</identity>

<judgment_process>
STEP 1 — BASE DIFFICULTY (0-100 spectrum)
STEP 2 — FAVORABLE FACTORS (Bonus values)
STEP 3 — UNFAVORABLE FACTORS (Penalty values)
STEP 4 — CALCULATE Final DC
STEP 5 — FAILURE CONSEQUENCE (GM Move)
</judgment_process>

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

<gm_moves>
When a roll fails, the world responds. Match severity to THEORIA's Position:
- Low Position (< 0.3): Minor consequence.
- Medium Position (0.4-0.6): Meaningful setback.
- High Position (> 0.7): Serious consequence.

Types: [worse_position, resource_loss, unwanted_attention, hard_choice, truth_revealed, separation].
</gm_moves>

<modifier_rules>
| Type | Range |
|------|-------|
| Injury | -5 to -20 |
| Passive | +5 to +15 |
| Item | +5 to +30 |
| Environment | ±5 to ±15 |
</modifier_rules>

<everyday_charm>
Even trivial actions deserve attention. "Small actions create ripples."
Include flavor modifiers even for standard tasks.
</everyday_charm>

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

</DIKASTES>
"""

async def judge_action_pro(
    client,
    model_id: str,
    user_intent: str,
    observation: str,
    relevant_context: List[str],
    history_tail: str
) -> Dict[str, Any]:
    """
    [DIKASTES - PRO V2.5]
    Implementation Request #1 v3 Compliant.
    """
    
    context_str = "\n".join(f"- {c}" for c in relevant_context) if relevant_context else "None"
    
    user_prompt = (
        f"### [SITUATION]\n{observation}\n"
        f"### [USER INTENT]\n{user_intent}\n"
        f"### [RECENT CHAT]\n{history_tail}\n"
        f"### [SELECTED RULES & CONTEXT]\n{context_str}\n" 
        "Perform Dikastes judgment. Determine DC, Modifiers, and potential GM Move."
    )
    
    contents = [types.Content(role="user", parts=[types.Part(text=user_prompt)])]
    config = types.GenerateContentConfig(
        system_instruction=SYSTEM_INSTRUCTION_PRO_JUDGE, 
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

def build_action_judgment_with_roll(action: str, difficulty: str, difficulty_reason: str, modifiers_list: List[Dict[str, int]], bonus_dice: int = 0) -> Dict[str, Any]:
    dc_table = {"trivial": 0, "easy": 20, "normal": 40, "hard": 60, "extreme": 80}
    dc = dc_table.get(difficulty.lower(), 40)
    
    base_roll = roll_dice(100)
    
    modifiers = {}
    modifier_total = 0
    if modifiers_list:
        for mod in modifiers_list:
            if isinstance(mod, dict):
                # Correct parsing for format {"name": "...", "value": 10}
                m_name = mod.get("name", "Unknown")
                m_val = mod.get("value", 0)
                
                try:
                    val_int = int(str(m_val).replace('+', '').replace(',', '').strip())
                    modifiers[m_name] = val_int
                    modifier_total += val_int
                except (ValueError, TypeError):
                    modifiers[m_name] = 0
                    logging.warning(f"Invalid modifier value for {m_name}: {m_val} (treated as 0)")
    
    
    # [NEW] Adaptation Bonus (Bonus Dice)
    adaptation_bonus = bonus_dice * 10
    if adaptation_bonus > 0:
        modifiers["Adaptation Bonus"] = adaptation_bonus
        modifier_total += adaptation_bonus

    final_roll = base_roll + modifier_total
    
    # Critical Logic
    # Standard: 1-5 (5%)
    # Easy/Trivial (DC <= 20): 1 ONLY (1%)
    crit_threshold = 5
    if dc <= 20: crit_threshold = 1
    
    if base_roll <= crit_threshold:
        result = "critical_failure"
    # Natural 96-100 AND Meeting DC is Critical Success
    elif base_roll >= 96 and final_roll >= dc:
        result = "critical_success"
    else:
        result = determine_result(final_roll, dc)
    
    return {
        "action": action, "difficulty": difficulty, "difficulty_reason": difficulty_reason,
        "base_roll": base_roll, "modifiers": modifiers, "modifier_total": modifier_total,
        "final_roll": final_roll, "dc": dc, "result": result
    }

def build_judgment_context_with_roll(judgment: Dict[str, Any]) -> str:
    res = judgment.get('result', 'failure')
    res_kr = {
        "critical_success": "대성공 (Critical Success)",
        "success": "성공 (Success)",
        "partial": "부분 성공 (Partial Success)",
        "failure": "실패 (Failure)",
        "critical_failure": "치명적 실패 (Critical Failure)",
        "automatic_success": "자동 성공 (Automatic)"
    }.get(res, res)
    
    # Handle Automatic Success (No Dice Display)
    if res == "automatic_success":
        log_msg = (
            f"🎲 **[판정: {judgment.get('action')}]**\n"
            f"난이도: {judgment.get('difficulty').upper()} (DC {judgment.get('dc')})\n"
            f"결과: **{res_kr}** (trivial)" 
        )
        return log_msg

    roll_detail = f"주사위: {judgment.get('base_roll')}"
    mod_text = ""
    for k, v in judgment.get('modifiers', {}).items():
        sign = "+" if v >= 0 else ""
        mod_text += f", {k}({sign}{v})"
    
    if mod_text: roll_detail += f" {mod_text}"
    
    # [NEW] GM Move Information (for Failure/Partial Success)
    gm_move_info = ""
    if res in ["failure", "critical_failure", "partial"]:
        gm_move = judgment.get("potential_gm_move")
        gm_desc = judgment.get("gm_move_description")
        if gm_move:
            gm_move_info = f"\n⚠️ **잠재적 위기 ({gm_move}):** {gm_desc}"

    log_msg = (
        f"🎲 **[판정: {judgment.get('action')}]**\n"
        f"난이도: {judgment.get('difficulty').upper()} (DC {judgment.get('dc')})\n"
        f"이유: {judgment.get('difficulty_reason')}\n"
        f"{roll_detail} = **{judgment.get('final_roll')}**\n"
        f"결과: **{res_kr}**"
        f"{gm_move_info}"
    )
    return log_msg
    if not judgment: return ""
    
    mod_strs = []
    for n, v in judgment.get("modifiers", {}).items():
        try:
            val = int(v)
            prefix = '+' if val >= 0 else ''
            mod_strs.append(f"{n}({prefix}{val})")
        except:
            mod_strs.append(f"{n}({v})")
    
    mod_text = ", ".join(mod_strs) if mod_strs else "None"
    
    res_key = judgment.get("result")
    result_kr_map = {
        "critical_success": "대성공", "success": "성공", "partial": "부분 성공", 
        "failure": "실패", "critical_failure": "대실패", "automatic_success": "자동 성공"
    }
    result_kr = result_kr_map.get(res_key, "N/A")
    
    roll_line = f"Roll: {judgment.get('base_roll')} {'+' if judgment.get('modifier_total')>=0 else ''}{judgment.get('modifier_total')} = {judgment.get('final_roll')}\n"
    
    return (
        f"### [GM JUDGMENT]\n"
        f"Action: {judgment.get('action')}\n"
        f"Diff: {judgment.get('difficulty')} (DC {judgment.get('dc')})\n"
        f"{roll_line}"
        f"Mods: {mod_text}\n"
        f"**RESULT: {result_kr}**\n\n"
    )

# =========================================================
# PART 3: EXTRACTION (LOGOS)
# =========================================================

async def extract_all_updates(
    client, model_id_flash: str, player_input: str, ai_response: str,
    # Contexts
    notebook: str = "", # [V5.1]
    current_status: List[str] = None,
    current_relationships: Dict[str, str] = None, current_companions: List[str] = None,
    lore_npc_names: List[str] = None, scene_npc_names: List[str] = None,
    current_passives: List[str] = None, current_quests: List[str] = None, current_memos: List[str] = None,
    fermented_context: str = "",
    player_context: str = "", # [V5.2] Added player context for subjective trigger check
    extraction_hints: Dict[str, bool] = None
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

    phys = result_map.get("physical", {})
    soc = result_map.get("social", {})
    nar = result_map.get("narrative", {})
    qst = result_map.get("quest", {})
    
    # Sanitize Physical (Now Notebook + Gold + Status)
    p_upd = None
    if phys:
        def _safe_int(v):
            try: return int(str(v).replace(',', '').replace('+', '').strip())
            except: return 0
            
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
        
        "QuestUpdate": {
            "quest_add": qst.get("quest_add"), "quest_complete": qst.get("quest_complete")
        } if qst else None
    }

# Internal Extractors (Private)

async def _extract_physical(client, model_id, p_in, ai_out, notebook, status):
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
        "6. **EXCLUSION (Transient Logs)**: Do NOT record one-off actions or movement logs that have no long-term impact (e.g., 'Moved to Maintenance Room', 'Used the elevator').\n"
        "\n### [DATA RULES]\n"
        "- **Currency**: Track currency based on setting (G, Credits, $, etc.).\n"
        "- **Deduplication**: If an item is given and taken in one turn, list it ONCE.\n"
        "- **Format**: ALWAYS maintain '— [소지품] —' and '— [메모] —' headers.\n"
        "\nExample Output:\n"
        '{"notebook_update": "— [소지품] —\\n- 50 Credits\\n- Dull Combat Knife\\n\\n— [메모] —\\n- Code for Vault: 1234", "status_add": [], "status_remove": []}'
    )
    ctx = f"Notebook Content:\n{notebook}\nStatus:{status}"
    usr = f"State:\n{ctx}\nIn:\n{p_in}\nAI:\n{ai_out}\nOutput FULL UPDATED Notebook JSON."
    return await _call_extract(client, model_id, sys, usr, "B-1 Notebook")

async def _extract_social(client, model_id, p_in, ai_out, rels, comps, lore_npcs, scene_npcs):
    sys = (
        "EXTRACT SOCIAL CHANGES.\n"
        "Return JSON with keys: relationships {Name: Level}, companions [list].\n"
        "Rules: Deduplicate Names. Only output changes.\n"
        'Example: {"relationships": {"Arthur": "Friendly"}, "companions": ["Arthur"]}'
    )
    ctx = f"Rels:{rels}, Comps:{comps}, LoreNPCs:{lore_npcs}, SceneNPCs:{scene_npcs}"
    usr = f"State:\n{ctx}\nIn:\n{p_in}\nAI:\n{ai_out}\nOutput JSON."
    return await _call_extract(client, model_id, sys, usr, "B-2 Social")

async def _extract_narrative(client, model_id, p_in, ai_out, passives, fermented, player_context=""):
    sys = (
        "EXTRACT NARRATIVE CHANGES.\n"
        "Return JSON with keys: passives [list], passive_suggestion {name, reason}, abnormal_trigger (string or null), abnormal_category (string or null).\n"
        "Rules: 'Passive' here means ANY PERMANENT CAPABILITY or TRAIT.\n"
        "Include:\n"
        "1. **Skills/Abilities**: Learned techniques (e.g. 'Fireball', 'Lockpicking', 'Swordsmanship').\n"
        "2. **Physical Traits**: Body mods, mutations, inherent stats (e.g. 'Cyber-Arm', 'Night Vision').\n"
        "3. **Mental Traits**: Personality quirks, learned knowledge (e.g. 'Iron Will', 'Chemistry').\n"
        "4. **Achievements**: Titles or major status (e.g. 'Dragonslayer').\n"
        "Abnormal Trigger Rules:\n"
        "- Identify Genre Shifts or Monsters appearing. **MUST BE IN ENGLISH**.\n"
        "- **abnormal_trigger**: The specific name of the anomaly (e.g., 'The Crimson Slime').\n"
        "- **abnormal_category**: The general species or type for the adaptation system (e.g., 'Slime', 'Machine', 'Ghost', 'Silence').\n"
        "- **CRITICAL**: CONSIDER THE CHARACTER'S BACKGROUND. Do NOT trigger for events that are routine for their profession.\n"
        "  - E.g., A Doctor seeing gore/wounds is NORMAL (No Trigger).\n"
        "  - E.g., A Soldier seeing battle is NORMAL (No Trigger).\n"
        "  - Only trigger if the event is truly shocking, supernatural, or fundamentally 'wrong' to THEM.\n"
        'Example: {"passives": ["Fireball"], "passive_suggestion": null, "abnormal_trigger": "Zombie Dragon", "abnormal_category": "Zombie"}'
    )
    ctx = f"Passives:{passives}, PlayerContext:{player_context}, FermentedSnippet:{fermented[:2000]}"
    usr = f"State:\n{ctx}\nIn:\n{p_in}\nAI:\n{ai_out}\nOutput JSON."
    return await _call_extract(client, model_id, sys, usr, "B-3 Narrative")

async def _extract_quest(client, model_id, p_in, ai_out, quests, memos):
    sys = (
        "EXTRACT QUEST CHANGES.\n"
        "Return JSON with keys: quest_add [list], quest_complete [list].\n"
        "Rules: precise quest strings. (Memos are managed via Notebook; DO NOT output them here).\n"
        'Example: {"quest_add": ["Find the key"], "quest_complete": []}'
    )
    ctx = f"Quests:{quests}"
    usr = f"State:\n{ctx}\nIn:\n{p_in}\nAI:\n{ai_out}\nOutput JSON."
    return await _call_extract(client, model_id, sys, usr, "B-4 Quest")

async def _call_extract(client, model_id, sys, usr, op_name):
    try:
        cfg = types.GenerateContentConfig(response_mime_type="application/json", temperature=0.1)
        cnt = [types.Content(role="user", parts=[types.Part(text=f"{sys}\n\n{usr}")])]
        res = await api_call_with_retry(client, model_id, cnt, cfg, operation_name=op_name)
        if res: return safe_parse_json(res)
    except Exception as e:
        logger.warning(f"[{op_name}] Error: {e}")
    return {}
