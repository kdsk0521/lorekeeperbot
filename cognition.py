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
    Analyzes current situation to extract Objective Facts and deduce Next Actions.
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
        "1. **Physics Check:** Verify physical possibility.\n"
        "2. **Knowledge Firewall:** Distinguish Player vs Character Knowledge.\n"
        "3. **Causal Integrity:** Verify causes existed BEFORE effects.\n\n"

        "### SYSTEM ACTION RULES (Auto-trigger)\n"
        "**Quest:** Add/Complete based on narrative events.\n"
        "**Memo:** Add clues/names/codes. Archive obsolete info.\n"
        "**Memo:** Add clues/names/codes. Archive obsolete info.\n"
        "**NPC:** Add new named characters. WARNING: CHECK EXISTING NPCS FIRST. Do not add 'Merchant' if 'Arthur' is already a known merchant. Link role to name.\n"
        "Important: Return `null` if no action needed.\n\n"

        "### NPC INTERACTION SYSTEM\n"
        "Analyze NPCs present. Determine attitudes (hostile/unfriendly/neutral/friendly/devoted).\n"
        "Suggest interaction between NPCs if appropriate.\n\n"

        "========================================\n"
        "### ACTION JUDGMENT (Game Master Role)\n"
        "========================================\n"
        "Judge player actions realistically based on difficulty and modifiers.\n"
        "**Difficulty:** trivial, easy, normal, hard, extreme\n"
        "**Modifiers:** passive_X (+15-25), tool_X (+10-15), condition_X (-10-20)\n\n"

        "### OUTPUT FORMAT (JSON ONLY)\n"
        "{\n"
        '  "CurrentLocation": "String",\n'
        '  "LocationRisk": "None/Low/Medium/High/Extreme",\n'
        '  "TimeContext": "String",\n'
        '  "Observation": "Objective summary",\n'
        '  "TemporalOrientation": {"continuity...": "...", "active_threads": [], "offscreen_npcs": []},\n'
        '  "NPCAttitudes": {"Name": {"attitude": "Type", "reason": "..."}},\n'
        '  "NPCInteraction": {"participants": [], "type": "...", "topic": "..."} OR null,\n'
        '  "AbnormalElements": ["List"] OR [],\n'
        '  "ExperienceCounters": {"Type": Count} OR {},\n'
        '  "SceneType": "normal/gore/nsfw/gore_nsfw",\n'
        '  "ActionJudgment": {"action": "...", "difficulty": "...", "reason": "...", "modifiers": []} OR null,\n'
        '  "Need": "Logical next step",\n'
        '  "SystemAction": {"tool": "...", "type": "...", "content": "..."} OR null,\n'
        '  "SessionMemoryUpdate": {"world_summary": "...", "world_changes": []} OR null\n'
        "}\n"
    )

    player_info = f"### [PLAYER STATUS]\n{player_context}\n" if player_context else ""

    user_prompt = (
        f"### [RULES]\n{rules}\n"
        f"### [QUESTS]\n{active_quests_text}\n"
        f"{player_info}"
        f"### [HISTORY]\n{history_text}\n"
        "Analyze the current state. Include temporal orientation for narrative continuity."
    )
    
    contents = [types.Content(role="user", parts=[types.Part(text=user_prompt)])]
    config = types.GenerateContentConfig(system_instruction=system_instruction, response_mime_type="application/json", temperature=0.2)
    
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
    dc_table = {"trivial": 10, "easy": 30, "normal": 50, "hard": 70, "extreme": 90}
    dc = dc_table.get(difficulty.lower(), 50)
    
    base_roll = roll_dice(100)
    
    modifiers = {}
    modifier_total = 0
    if modifiers_list:
        for mod in modifiers_list:
            if isinstance(mod, dict):
                for k, v in mod.items():
                    modifiers[k] = v
                    modifier_total += v
    
    final_roll = base_roll + modifier_total
    result = determine_result(final_roll, dc)
    
    return {
        "action": action, "difficulty": difficulty, "difficulty_reason": difficulty_reason,
        "base_roll": base_roll, "modifiers": modifiers, "modifier_total": modifier_total,
        "final_roll": final_roll, "dc": dc, "result": result
    }

def build_judgment_context_with_roll(judgment: Dict[str, Any]) -> str:
    if not judgment: return ""
    
    mod_strs = [f"{n}({'+' if v>=0 else ''}{v})" for n, v in judgment.get("modifiers", {}).items()]
    mod_text = ", ".join(mod_strs) if mod_strs else "None"
    
    result_kr = {"critical_success": "대성공", "success": "성공", "partial": "부분 성공", "failure": "실패"}.get(judgment.get("result"), "N/A")
    
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
    current_inventory: Dict[str, int] = None, current_gold: int = 0, current_status: List[str] = None,
    current_relationships: Dict[str, str] = None, current_companions: List[str] = None,
    lore_npc_names: List[str] = None, scene_npc_names: List[str] = None,
    current_passives: List[str] = None, current_quests: List[str] = None, current_memos: List[str] = None,
    fermented_context: str = ""
) -> Dict[str, Any]:
    
    # Run specialized extractors in parallel
    results = await asyncio.gather(
        _extract_physical(client, model_id_flash, player_input, ai_response, current_inventory, current_gold, current_status),
        _extract_social(client, model_id_flash, player_input, ai_response, current_relationships, current_companions, lore_npc_names, scene_npc_names),
        _extract_narrative(client, model_id_flash, player_input, ai_response, current_passives, fermented_context),
        _extract_quest(client, model_id_flash, player_input, ai_response, current_quests, current_memos),
        return_exceptions=True
    )
    
    phys, soc, nar, qst = [r if not isinstance(r, Exception) else {} for r in results]
    
    # Consolidate
    return {
        "PlayerUpdate": {
            "inventory_add": phys.get("inventory_add"), "inventory_remove": phys.get("inventory_remove"),
            "gold_change": phys.get("gold_change"), "status_add": phys.get("status_add"), "status_remove": phys.get("status_remove")
        } if phys else None,
        
        "PlayerMemoryUpdate": {
            "relationships": soc.get("relationships"), "companions": soc.get("companions"), "passives": nar.get("passives")
        } if soc or nar.get("passives") else None,
        
        "PassiveSuggestion": nar.get("passive_suggestion"),
        "AbnormalTrigger": nar.get("abnormal_trigger"),
        
        "QuestUpdate": {
            "quest_add": qst.get("quest_add"), "quest_complete": qst.get("quest_complete"),
            "memo_add": qst.get("memo_add"), "memo_remove": qst.get("memo_remove"), "memo_archive": qst.get("memo_archive")
        } if qst else None
    }

# Internal Extractors (Private)

async def _extract_physical(client, model_id, p_in, ai_out, inv, gold, status):
    sys = (
        "EXTRACT PHYSICAL CHANGES.\n"
        "Return JSON with keys: inventory_add {name: count}, inventory_remove {name: count}, gold_change (int), status_add [list], status_remove [list].\n"
        "Rules: Add ONLY significant items (Not trivial food). Track ALL gold info.\n"
        'Example: {"inventory_add": {"Sword": 1}, "gold_change": -50, "status_add": [], "inventory_remove": {}, "status_remove": []}'
    )
    ctx = f"Inv:{inv}, Gold:{gold}, Status:{status}"
    usr = f"State:\n{ctx}\nIn:\n{p_in}\nAI:\n{ai_out}\nOutput JSON."
    return await _call_extract(client, model_id, sys, usr, "B-1 Physical")

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

async def _extract_narrative(client, model_id, p_in, ai_out, passives, fermented):
    sys = (
        "EXTRACT NARRATIVE CHANGES.\n"
        "Return JSON with keys: passives [list], passive_suggestion {name, reason}, abnormal_trigger (string or null).\n"
        "Rules: Passives for REPEATED(3+) or MAJOR events only. Abnormal Trigger for Genre Shift/Monsters.\n"
        'Example: {"passives": ["Dragonslayer"], "passive_suggestion": null, "abnormal_trigger": "Zombie"}'
    )
    ctx = f"Passives:{passives}, FermentedSnippet:{fermented[:2000]}"
    usr = f"State:\n{ctx}\nIn:\n{p_in}\nAI:\n{ai_out}\nOutput JSON."
    return await _call_extract(client, model_id, sys, usr, "B-3 Narrative")

async def _extract_quest(client, model_id, p_in, ai_out, quests, memos):
    sys = (
        "EXTRACT QUEST/MEMO CHANGES.\n"
        "Return JSON with keys: quest_add [list], quest_complete [list], memo_add [list], memo_remove [list], memo_archive [list].\n"
        "Rules: precise quest strings. simple memos.\n"
        'Example: {"quest_add": ["Find the key"], "quest_complete": [], "memo_add": ["Code is 1234"], "memo_remove": [], "memo_archive": []}'
    )
    ctx = f"Quests:{quests}, Memos:{memos}"
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
