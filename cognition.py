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

SYSTEM_INSTRUCTION_FLASH = """
[THEORIA - LIBRARIAN & OBSERVER]
You are the high-speed observer and librarian of the system.
Your goal is NOT to judge, but to **observe** and **select relevant context** for the Judge.

### 1. OBSERVATION & PRINCIPLES
- **MACROSCOPIC ONLY:** Analyze observable phenomena ONLY.
- **CAUSALITY BOUND:** Apply physics and logic strictly (Verify physical possibility).
- **ASYNCHRONOUS WORLD:** Consider what NPCs might be doing concurrently.
- **KNOWLEDGE FIREWALL:** Distinguish Player vs Character Knowledge.

### 2. NONVIOLENT COMMUNICATION (NVC) ANALYSIS
Analyze the User's input using the 4 elements of NVC to understand deep intent:
1. **Observation:** What actually happened? (Objective facts)
2. **Feeling:** What is the character feeling? (Emotional context)
3. **Need:** What underlying need/value drives this action? (Survival, Connection, Power, etc.)
4. **Request:** What is the user trying to achieve? (Explicit Intent)

### 3. CONTEXT SELECTION (CRITICAL)
- Detailedly read the Lore, Rules, and Notebook.
- **Select** 3-5 specific excerpts/quotes that are relevant to the user's action.
- If the user uses an item, find its exact description in the Notebook.
- If the user interacts with an NPC, find their specific trait/relationship status.

### 4. OUTPUT FORMAT (JSON)
{
  "CurrentLocation": "String",
  "LocationRisk": "None/Low/Medium/High/Extreme",
  "TimeContext": "String",
  "SceneType": "normal/combat/social/summary/intimate",
  "Observation": "NVC Observation (Objective Fact)",
  "Feeling": "NVC Feeling (Emotional State)",
  "Need": "NVC Need (Underlying Motivation)",
  "UserIntent": "NVC Request (Explicit Goal)",
  "RelevantContext": [
      "Rule: ...",
      "Item: ...",
      "NPC: ..."
  ],
  "TimeFlow": {"duration": "instant/short/medium/long/explicit", "ticks": Int},
  "NPCAttitudes": {"Name": {"attitude": "Type", "reason_for_change": "..."}}
}
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
    [THEORIA - FLASH]
    Reads MASSIVE context (100k+) and extracts/selects relevant bits.
    """
    
    # Prepare Attitude Context
    attitude_context = ""
    if existing_npc_attitudes:
        attitude_lines = [
            f"- {name}: {data.get('attitude', 'neutral')} ({data.get('reason', '')})"
            for name, data in existing_npc_attitudes.items()
        ]
        attitude_context = (
            "### EXISTING NPC ATTITUDES\n" + "\n".join(attitude_lines) + "\n\n"
        )

    player_info = f"### [PLAYER STATUS]\n{player_context}\n" if player_context else ""

    user_prompt = (
        f"### [RULES]\n{rules}\n"
        f"### [QUESTS]\n{active_quests_text}\n"
        f"### [NOTEBOOK]\n{notebook}\n"
        f"{player_info}"
        f"### [HISTORY]\n{history_text}\n"
        f"### [LORE]\n{lore}\n" 
        f"{attitude_context}" 
        "Analyze the situation. Identify User Intent. Select RELEVANT context/rules for the Judge."
    )
    
    contents = [types.Content(role="user", parts=[types.Part(text=user_prompt)])]
    config = types.GenerateContentConfig(
        system_instruction=SYSTEM_INSTRUCTION_FLASH, 
        response_mime_type="application/json", 
        temperature=0.1 # Very low temp for citation accuracy
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
[DIKASTES - THE JUDGE]
You are the impartial Game Master.
Your goal is to Determine the Difficulty and Modifiers for the user's action based on the PROVIDED CONTEXT.

### INPUT DATA
- **User Intent:** What they want to do.
- **Observation:** The current situation.
- **Relevant Context:** Specific Rules/Items/Lore selected by the Librarian.

### JUDGMENT PROTOCOL
1. **Difficulty:** Trivial(0), Easy(20), Normal(40), Hard(60), Extreme(80).
2. **Modifiers Calculation (Apply Strictly):**
   - **Injuries:** -10 Penalty if the user is injured and it affects the action.
   - **Passive/Trait:** +5 Bonus if a specific Passive applies.
   - **Notebook/Item:** +5 to +30 Bonus if a recorded Item/Note provides leverage.
   - **Environment:** +/- Modifiers based on weather/terrain (e.g., Rain -5).
3. **Everyday Charm:** Even for trivial actions, look for small flavor modifiers or standard difficulties. Do not skip judgment.

### OUTPUT FORMAT (JSON)
{
  "ActionJudgment": {
      "action": "Summarized Action", 
      "difficulty": "normal", 
      "difficulty_reason": "Rationale...", 
      "modifiers": [{"name": "...", "value": 0}]
  },
  "SystemAction": {"tool": "Memo/Quest/NPC", "type": "Add/Remove", "content": "..."}
}
"""      "difficulty": "normal", 
      "difficulty_reason": "Rationale...", 
      "modifiers": [{"name": "...", "value": 0}]
  },
  "SystemAction": {"tool": "Memo/Quest/NPC", "type": "Add/Remove", "content": "..."}
}
"""

async def judge_action_pro(
    client,
    model_id: str,
    user_intent: str,
    observation: str,
    relevant_context: List[str],
    history_tail: str # Recent few lines for flow
) -> Dict[str, Any]:
    """
    [DIKASTES - PRO]
    Judges the action using high-intelligence logic on selected context.
    """
    
    context_str = "\n".join(f"- {c}" for c in relevant_context) if relevant_context else "None"
    
    user_prompt = (
        f"### [SITUATION]\n{observation}\n"
        f"### [USER INTENT]\n{user_intent}\n"
        f"### [RECENT CHAT]\n{history_tail}\n"
        f"### [SELECTED RULES & CONTEXT]\n{context_str}\n" 
        "Judge the action. Determine difficulty and modifiers."
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

def build_action_judgment_with_roll(action: str, difficulty: str, difficulty_reason: str, modifiers_list: List[Dict[str, int]]) -> Dict[str, Any]:
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
    
    log_msg = (
        f"🎲 **[판정: {judgment.get('action')}]**\n"
        f"난이도: {judgment.get('difficulty').upper()} (DC {judgment.get('dc')})\n"
        f"이유: {judgment.get('difficulty_reason')}\n"
        f"{roll_detail} = **{judgment.get('final_roll')}**\n"
        f"결과: **{res_kr}**"
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
        "2. **ACQUISITION ONLY**: Record items ONLY if the player PHYSICALLY takes them OR finds them inside an OWNED container.\n"
        "   - ✅ YES: 'Pick up', 'Take', 'Received', 'Bought', 'Stole', 'Put in pocket'.\n"
        "   - ✅ YES (Inside Owned): 'Opened my bag and found a key', 'Checked my wallet'.\n"
        "   - ❌ NO: 'See', 'Spot', 'Identify', 'Look at', 'Examine'. (External observation != Owning)\n"
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
        "- Identify Genre Shifts or Monsters appearing. **MUST BE IN ENGLISH**.\n"
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
