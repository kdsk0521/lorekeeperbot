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


SYSTEM_INSTRUCTION_NVC = """
[THEORIA LEFT HEMISPHERE - Logic Core]
You are the analytical component of the THEORIA system.
Your role: Extract OBJECTIVE FACTS from the narrative context.

### CORE PRINCIPLES
1. **MACROSCOPIC ONLY:** Analyze observable phenomena ONLY.
2. **CAUSALITY BOUND:** Apply physics and logic strictly.
3. **ASYNCHRONOUS WORLD:** Consider what NPCs might be doing concurrently.

### OBSERVATION PROTOCOLS
1. **Physics Check:** Verify physical possibility.
2. **Knowledge Firewall:** Distinguish Player vs Character Knowledge.
3. **Causal Integrity:** Verify causes existed BEFORE effects.

### SYSTEM ACTION RULES
**Quest:** Add/Complete based on narrative events.
**Notebook:** This is the unified record of items, tools, and memos.
**NPC:** Add new named characters. Link role to name.

### 4. PASSIVE & NOTEBOOK BONUSES
- **Passive Bonus:** IF the user has a Passive relevant to the action, grant a **+5 BONUS**.
- **Notebook/Item Bonus:** IF the user uses an item, tool, or secret recorded in the **Notebook**, grant a bonus from **+5 to +30** depending on importance.
- **Auto-Suggestion:** IF user succeeds at a specific type of action 5+ times, suggest a new Passive.

### NPC INTERACTION SYSTEM
Analyze NPCs present. Determine attitudes (hostile/unfriendly/neutral/friendly/devoted).

### ACTION JUDGMENT (Game Master Role)
Judge player actions realistically based on difficulty and modifiers.
**Difficulty:** trivial, easy, normal, hard, extreme
**Modifiers:** injury (-10), tool/item (Notebook: +5~+30), **PASSSIVE/SKILL/TRAIT (+5)**.

### OUTPUT FORMAT (JSON ONLY)
{
  "CurrentLocation": "String",
  "LocationRisk": "None/Low/Medium/High/Extreme",
  "TimeContext": "String",
  "SceneType": "normal/combat/social/summary/intimate",
  "Observation": "Objective summary",
  "TimeFlow": {"duration": "instant/short/medium/long/explicit", "ticks": Int},
  "ActionJudgment": {"action": "...", "difficulty": "...", "modifiers": [{"name": "Item: Magic Sword", "value": 30}]},
  "PassiveSuggestion": {"name": "...", "tags": [], "reason": "..."},
  "NPCAttitudes": {"Name": {"attitude": "Type", "reason": "..."}}
}
"""

async def analyze_context_nvc(
    client,
    model_id: str,
    history_text: str,
    lore: str,
    rules: str,
    active_quests_text: str,
    notebook: str = "", # [V5.1] Added notebook
    player_context: str = "",
    existing_npc_attitudes: Dict[str, Dict] = None
) -> Dict[str, Any]:
    """
    [THEORIA LEFT HEMISPHERE]
    Analyzes current situation to extract Objective Facts and deduce Next Actions.
    """
    
    # Prepare Attitude Context
    attitude_context = ""
    if existing_npc_attitudes:
        attitude_lines = [
            f"- {name}: {data.get('attitude', 'neutral')} ({data.get('reason', '')})"
            for name, data in existing_npc_attitudes.items()
        ]
        attitude_context = (
            "### EXISTING NPC ATTITUDES\n"
            "These are the currently tracked attitudes. "
            "Only output changes if the scene warrants an attitude shift:\n"
            + "\n".join(attitude_lines) + "\n\n"
        )

    # The original system_instruction content is now replaced by SYSTEM_INSTRUCTION_NVC
    # The user_prompt needs to be constructed to provide the necessary context for the new SYSTEM_INSTRUCTION_NVC
    player_info = f"### [PLAYER STATUS]\n{player_context}\n" if player_context else ""

    user_prompt = (
        f"### [RULES]\n{rules}\n"
        f"### [QUESTS]\n{active_quests_text}\n"
        f"### [NOTEBOOK (ITEMS & MEMOS)]\n{notebook}\n" # [V5.1]
        f"{player_info}"
        f"### [HISTORY]\n{history_text}\n"
        f"### [LORE]\n{lore}\n" 
        f"{attitude_context}" 
        "Analyze the current state based on the above context and the user's input. Provide the logical consequences and instructions in the specified JSON format."
    )
    
    contents = [types.Content(role="user", parts=[types.Part(text=user_prompt)])]
    config = types.GenerateContentConfig(system_instruction=SYSTEM_INSTRUCTION_NVC, response_mime_type="application/json", temperature=0.2)
    
    result = await api_call_with_retry(client, model_id, contents, config, operation_name="Context Analysis (NVC)")
    if result:
        parsed = safe_parse_json(result)
        if parsed: return parsed
    
    return {
        "CurrentLocation": "Unknown", "LocationRisk": "Low", "TimeContext": "Unknown",
        "Observation": "Analysis Failed", "Need": "Proceed with Caution", "SystemAction": None
    }

# =========================================================
# PART 2: DICE & JUDGMENT SYSTEM
# =========================================================

def roll_dice(sides: int = 100) -> int:
    return random.randint(1, sides)

def determine_result(final_roll: int, dc: int) -> str:
    if final_roll >= dc + 30: return "critical_success"
    elif final_roll >= dc: return "success"
    elif final_roll >= dc - 20: return "partial"
    else: return "failure"

def build_action_judgment_with_roll(action: str, difficulty: str, difficulty_reason: str, modifiers_list: List[Dict[str, int]]) -> Dict[str, Any]:
    dc_table = {"trivial": 0, "easy": 20, "normal": 40, "hard": 60, "extreme": 80}
    dc = dc_table.get(difficulty.lower(), 40)
    
    base_roll = roll_dice(100)
    
    modifiers = {}
    modifier_total = 0
    if modifiers_list:
        for mod in modifiers_list:
            if isinstance(mod, dict):
                for k, v in mod.items():
                    try:
                        val_int = int(str(v).replace('+', '').replace(',', '').strip())
                        modifiers[k] = val_int
                        modifier_total += val_int
                    except (ValueError, TypeError):
                        modifiers[k] = 0
                        logging.warning(f"Invalid modifier value for {k}: {v} (treated as 0)")
    
    final_roll = base_roll + modifier_total
    
    # Critical Logic: Natural 1-5 is always Critical Failure
    if base_roll <= 5:
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
    
    result_kr = {"critical_success": "대성공", "success": "성공", "partial": "부분 성공", "failure": "실패", "critical_failure": "대실패"}.get(judgment.get("result"), "N/A")
    
    return (
        f"### [GM JUDGMENT]\n"
        f"Action: {judgment.get('action')}\n"
        f"Diff: {judgment.get('difficulty')} (DC {judgment.get('dc')})\n"
        f"Roll: {judgment.get('base_roll')} {'+' if judgment.get('modifier_total')>=0 else ''}{judgment.get('modifier_total')} = {judgment.get('final_roll')}\n"
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
        
        "QuestUpdate": {
            "quest_add": qst.get("quest_add"), "quest_complete": qst.get("quest_complete")
        } if qst else None
    }

# Internal Extractors (Private)

async def _extract_physical(client, model_id, p_in, ai_out, notebook, status):
    sys = (
        "EXTRACT NOTEBOOK & PHYSICAL CHANGES.\n"
        "Return JSON with keys: notebook_update (string or null), status_add [list], status_remove [list].\n"
        "Principle: Convert narrative actions into a concise Notebook summary.\n"
        "\n### [STRICT OWNERSHIP RULES - CRITICAL]\n"
        "1. **Currency/Item Tracking**: Track items and currency based on context (e.g., Gold, Dollars, Credits).\n"
        "   - Do NOT enforce 'Gold: 0G' if the setting uses a different currency or none at all.\n"
        "2. **ACQUISITION ONLY**: Record items ONLY if the player PHYSICALLY takes them.\n"
        "   - ✅ YES: 'Pick up', 'Take', 'Received', 'Bought', 'Stole', 'Put in pocket'.\n"
        "   - ❌ NO: 'See', 'Spot', 'Identify', 'Look at', 'Examine'. (Mere observation != Owning)\n"
        "3. **NO DUPLICATION (Redundancy Check)**: If NPC gives an item and Player takes it in the same turn, count it ONCE.\n"
        "   - Scenario: 'NPC throws ball' (Event) + 'I catch it' (Input) = 1 Ball (Not 2).\n"
        "   - Rule: Check the *Existing Notebook* first. If item is already there, DO NOT add again unless quantity increases.\n"
        "4. **Format**: Maintain '— [소지품] —' and '— [메모] —'.\n"
        "\nExample:\n"
        '{"notebook_update": "— [소지품] —\\n- 100 Credits\\n- Rusty Sword\\n\\n— [메모] —\\n- Code: 5566", "status_add": [], ...}'
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
        "Return JSON with keys: passives [list], passive_suggestion {name, reason}, abnormal_trigger (string or null).\n"
        "Rules: 'Passive' here means ANY PERMANENT CAPABILITY or TRAIT.\n"
        "Include:\n"
        "1. **Skills/Abilities**: Learned techniques (e.g. 'Fireball', 'Lockpicking', 'Swordsmanship').\n"
        "2. **Physical Traits**: Body mods, mutations, inherent stats (e.g. 'Cyber-Arm', 'Night Vision').\n"
        "3. **Mental Traits**: Personality quirks, learned knowledge (e.g. 'Iron Will', 'Chemistry').\n"
        "4. **Achievements**: Titles or major status (e.g. 'Dragonslayer').\n"
        "Abnormal Trigger Rules:\n"
        "- Identify Genre Shifts or Monsters appearing.\n"
        "- **CRITICAL**: CONSIDER THE CHARACTER'S BACKGROUND. Do NOT trigger for events that are routine for their profession.\n"
        "  - E.g., A Doctor seeing gore/wounds is NORMAL (No Trigger).\n"
        "  - E.g., A Soldier seeing battle is NORMAL (No Trigger).\n"
        "  - Only trigger if the event is truly shocking, supernatural, or fundamentally 'wrong' to THEM.\n"
        'Example: {"passives": ["Fireball", "Cold Logic", "Cyber-Eye"], "passive_suggestion": null, "abnormal_trigger": "Zombie"}'
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
