"""
Lorekeeper TRPG Bot - Cognition Module
Extraction (Logos) and structured analysis utilities.
"""

import json
import logging
import asyncio
from typing import Dict, Any, List, Optional
from google import genai
from google.genai import types

from memory_system import (
    api_call_with_retry,
    safe_parse_json
)

logger = logging.getLogger("Cognition")

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
    extraction_hints: Optional[Dict[str, bool]] = None,
    current_session_memory: Optional[Dict[str, Any]] = None
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

    if extraction_hints.get("world_state", False):
        tasks.append(_extract_world_state(client, model_id_flash, player_input, ai_response, current_session_memory))
        task_keys.append("world_state")

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
    wst: Dict[str, Any] = result_map.get("world_state", {})
    
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
        } if qst else None,

        "WorldStateUpdate": wst if wst else None
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
        "## [EXTRACT NOTEBOOK & PHYSICAL CHANGES - V3.6]\n"
        "Return JSON with keys: notebook_update (string or null), status_add [list], status_remove [list].\n\n"
        "### [STRICT SAFETY GUARDS]\n"
        "1. **ACQUISITION vs OBSERVATION (CRITICAL)**: Record items ONLY if player physically TAKES, RECEIVES, or BUYS them. Simply 'seeing' or 'inspecting' does NOT grant ownership. If no item was taken, `notebook_update` MUST be `null`.\n"
        "2. **NO CHANGE -> NULL**: If there are no physical acquisitions, losses, or status changes, return `null` for `notebook_update`.\n\n"
        "### [FEW-SHOT EXAMPLES]\n"
        "- **Input**: 'I see a rusty sword on the wall and keep walking.'\n"
        "  - Output: `{\"notebook_update\": null, \"status_add\": [], \"status_remove\": []}` (Observation only)\n"
        "- **Input**: 'I pick up the rusty sword and put it in my bag.'\n"
        "  - Output: `{\"notebook_update\": \"— [소지품] —\\n- Rusty Sword\", \"status_add\": [], \"status_remove\": []}` (Acquisition!)\n\n"
        "### [DETAILED MANAGEMENT RULES]\n"
        "1. **LOSS & DESTRUCTION**: If an item is lost, stolen, or destroyed, REMOVE it from the Notebook.\n"
        "2. **CONSUMPTION**: If a consumable (food, potion, ammo) is used, update its quantity or REMOVE if empty.\n"
        "3. **STATE UPDATE**: If an item's condition changes (e.g. 'Sword' becomes 'Broken Sword'), update the description.\n"
        "4. **DE-CLUTTER (Memos)**: Proactively REMOVE resolved tasks or information that is no longer relevant (e.g., 'Reached the room' is done) to prevent information overload.\n"
        "5. **EXCLUSION**: Do NOT record one-off transient actions or movement logs that have no long-term impact on the persistent state.\n"
        "6. **HYGIENE**: Do NOT re-list items/memos already present in the [Current Notebook] unless the quantity or status has changed.\n\n"
        "### [FORMAT]\n"
        "- ALWAYS maintain '— [소지품] —' and '— [메모] —' headers."
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
        "## [EXTRACT SOCIAL CHANGES - V3.7]\n"
        "Return JSON: `{\"relationships\": {Name: Status}, \"companions\": [list]}`\n\n"
        "### [FEW-SHOT EXAMPLE]\n"
        "- **Input**: 'NPC Arthur nods and offers his hand in friendship.'\n"
        "  - Output: `{\"relationships\": {\"Arthur\": \"Friendly\"}, \"companions\": [\"Arthur\"]}`\n"
        "### [RULES]\n"
        "1. **Only record SIGNIFICANT changes** in attitude (e.g., Neutral -> Friendly, Friendly -> Hostile).\n"
        "2. **Deduplicate names**: Only use names explicitly present in recent history or lore NPCs.\n"
        "3. **Safety Guard**: If no social change occurred, return `{\"relationships\": {}, \"companions\": []}`. Never fabricate trust or enmity without clear textual evidence."
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
        "## [EXTRACT NARRATIVE CHANGES - V3.8]\n"
        "Return JSON: `{\"passives\": [], \"passive_suggestion\": null, \"abnormal_trigger\": null, \"abnormal_category\": null, \"mental_suggestion\": null, \"mental_delta\": 0, \"doom_delta\": 0}`\n\n"
        "### [FEW-SHOT EXAMPLE]\n"
        "- **Input**: 'A faceless entity appears from the shadows. I feel a chill of cosmic horror.'\n"
        "  - Output: `{\"abnormal_trigger\": \"Faceless Entity\", \"abnormal_category\": \"Ghost\", \"mental_delta\": -15, \"doom_delta\": +3}`\n\n"
        "### [DETAILED PASSIVE RULES]\n"
        "'Passive' means ANY permanent capability. Include:\n"
        "1. **Skills/Abilities**: Learned techniques (e.g., 'Fireball', 'Lockpicking').\n"
        "2. **Physical Traits**: Body mods, mutations (e.g., 'Cyber-Arm', 'Night Vision').\n"
        "3. **Mental Traits**: Personality quarks, specialized knowledge (e.g., 'Iron Will').\n"
        "4. **Achievements**: Significant titles or status (e.g., 'Dragonslayer').\n"
        "5. **HYGIENE**: Only return NEW ones not in the [Passives] list.\n\n"
        "### [NARRATIVE SIGNALS & ANOMALY RULES]\n"
        "1. **Anomaly Trigger**: Genre shifts or monsters. **MUST BE IN ENGLISH**.\n"
        "2. **Mental Delta**: Rest (+10~+30), Trauma (-10~-25). Use integers.\n"
        "3. **Doom Delta**: Global threat reduction (-5~-10) or escalation (+2~+5).\n"
        "4. **Professional Bias (CRITICAL)**: Gore is NORMAL for a Doctor. Combat is NORMAL for a Soldier. Only trigger for events truly wrong to THEM.\n\n"
        "### [SAFETY GUARD]\n"
        "If no significant narrative adaptation happens, keep fields `null` or `0`."
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
        "## [EXTRACT QUEST CHANGES - V3.6]\n"
        "Return JSON with keys: quest_add [list], quest_complete [list].\n\n"
        "### [RULES]\n"
        "1. **ADD**: Only add NEW quests. Do not duplicate quests already in [Quests].\n"
        "2. **COMPLETE**: Mark as complete ONLY if explicitly resolved.\n\n"
        "### [SAFETY GUARD]\n"
        "If no quest update, return `{\"quest_add\": [], \"quest_complete\": []}`."
    )
    ctx = f"Quests:{quests}"
    usr = f"State:\n{ctx}\nIn:\n{p_in}\nAI:\n{ai_out}\nOutput JSON."
    return await _call_extract(client, model_id, sys, usr, "B-4 Quest")

async def _extract_world_state(
    client: genai.Client,
    model_id: str,
    p_in: str,
    ai_out: str,
    current_session_memory: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    """
    [World State Updater] +1 Flash call.
    AI 응답 후 세계 상태를 추출하여 ai_session_memory를 갱신합니다.
    active_threads, world_changes, npc_schedule_hints, basic_needs_flags 추출.
    """
    mem = current_session_memory or {}
    existing_threads = mem.get("active_threads", [])
    existing_arc = mem.get("current_arc", "")

    sys = (
        "## [WORLD STATE UPDATER - V1.0]\n"
        "You are a TRPG session tracker. Analyze the latest exchange and extract world state changes.\n"
        "Return JSON with these keys:\n\n"
        "### FIELDS\n"
        "- `active_threads`: [list of str] Currently open narrative threads/plotlines. "
        "Merge with existing, remove resolved ones. Max 10. Korean.\n"
        "- `resolved_threads`: [list of str] Threads that were resolved THIS turn. Korean.\n"
        "- `world_changes`: [list of str] Significant environmental/world changes from this turn. "
        "Only NEW changes (not already known). Max 5. Korean.\n"
        "- `npc_schedule_hints`: {NpcName: str} Where each active NPC likely is or what they're doing RIGHT NOW "
        "based on context. Only NPCs mentioned or implied. Korean.\n"
        "- `basic_needs_flags`: {str: bool} Physical state flags for the PC. "
        "Keys: hungry, thirsty, tired, injured, cold, hot. Only set true if evidence exists.\n"
        "- `current_arc`: str - One-line summary of the current narrative arc. Korean.\n\n"
        "### RULES\n"
        "1. **CONSERVATIVE**: Only extract what is clearly evidenced in the text.\n"
        "2. **NO FABRICATION**: Do not invent threads or NPC activities not implied by context.\n"
        "3. **MERGE**: active_threads should combine existing + new - resolved.\n"
        "4. **HYGIENE**: Remove stale threads that are clearly no longer relevant.\n"
    )
    ctx_lines = [f"Current Arc: {existing_arc}" if existing_arc else "Current Arc: (none)"]
    if existing_threads:
        ctx_lines.append(f"Existing Threads: {existing_threads[:10]}")
    ctx = "\n".join(ctx_lines)
    usr = f"State:\n{ctx}\nIn:\n{p_in}\nAI:\n{ai_out}\nOutput JSON."
    return await _call_extract(client, model_id, sys, usr, "B-5 WorldState")


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
# PART 4: UNIFIED LORE ANALYSIS (LORE ANALYZER)
# =========================================================

async def analyze_lore_unified(
    client: genai.Client,
    model_id: str,
    lore_text: str
) -> Dict[str, Any]:
    """
    [LoreAnalyzer V1]
    로어북을 전체적으로 분석하여 장르, NPC, PC, 세계관 테마 및 이변 징후를 통합 추출합니다.
    """
    if not lore_text:
        return {}

    system_prompt = f"""You are an experienced TRPG Campaign Designer and 'Lore Analysis Engine (LoreAnalyzer)'.
Analyze the provided lorebook precisely to extract all metadata required for game operations.

## Analysis Principles (Absolute Principles)
1. **Holistic Consistency**: Clearly distinguish between NPCs and the PC (Player Character/Protagonist).
2. **Genre Alignment**: Match lore themes with existing system genre keywords.
3. **Narrative Anomaly Extraction**: Summarize themes that serve as the root of ruptures or supernatural phenomena as 'Anomaly Seeds'.
4. **Optimization**: Write descriptions concisely and powerfully. (Follow the optimization guide in text_resources)

## Output Schema
**IMPORTANT: All string descriptions and guides must be in KOREAN.**

1. **genres**: 3-Layer Genre structure (List up to 2 tags per layer)
   - world_setting: Time/Setting backdrop (Choose 1-2 from: high_fantasy, wuxia, cyberpunk, post_apocalypse, space_opera, modern)
   - style_tech: Narrative gimmicks (Choose 0-2 from: urban_fantasy, steampunk, cosmic_horror, game_system)
   - narrative_tone: Atmosphere/Tone (Choose 1-2 from: noir, comedy, romance, drama)
   - atmosphere_guide: Short atmosphere guide for the narrator (Korean)
2. **npcs**: List of NPCs (Name, Gender, Race, Detailed Description (Personality/Appearance/Role integrated - Korean))
3. **pc_info**: Identification of the Protagonist. null if no clear protagonist.
   - Fields: name, role, species, appearance, description (integrated personality/traits - Korean), sexual_characteristics, background, secret_info, passives(name, desc - Korean), inventory
4. **lore_summary**:
   - theme: Core theme of the world (1-2 sentences in Korean)
   - anomaly_seeds: List of anomaly/supernatural themes possible in this world (e.g., '그림자 침식', '기계 광증' etc. - Korean)
   - locations: Key locations and their characteristics (Korean)
5. **world_constraints**: World rules extracted from lore (Korean)
   - systems: Magic/technology/power systems described in the lore (1-2 sentences)
   - social: Social hierarchy, taboos, cultural norms (1-2 sentences)

## Output Format (JSON Only)
{{
  "genres": {{
    "world_setting": ["..."],
    "style_tech": ["..."],
    "narrative_tone": ["..."],
    "atmosphere_guide": "..."
  }},
  "npcs": [ {{ "name": "...", "gender": "...", "race": "...", "description": "..." }} ],
  "pc_info": {{
    "name": "...",
    "role": "...",
    "species": "...",
    "appearance": "...",
    "description": "성격 및 전반적인 특징 설명",
    "sexual_characteristics": "...",
    "background": "...",
    "secret_info": "...",
    "passives": [ {{ "name": "...", "desc": "..." }} ],
    "inventory": {{ "Item": "Quantity" }}
  }},
  "lore_summary": {{
    "theme": "...",
    "anomaly_seeds": ["징후1", "징후2"],
    "locations": "..."
  }},
  "world_constraints": {{
    "systems": "마법/기술 체계 설명",
    "social": "사회 구조/금기 설명"
  }}
}}"""

    try:
        gen_config = types.GenerateContentConfig(
            response_mime_type="application/json",
            temperature=0.1
        )
        contents = [
            types.Content(
                role="user",
                parts=[types.Part(text=f"{system_prompt}\n\n[LORE TEXT]\n{lore_text}")]
            )
        ]

        result = await api_call_with_retry(
            client, model_id, contents, gen_config, 
            operation_name="Unified Lore Analysis"
        )
        
        if result:
            return safe_parse_json(result)

    except Exception as e:
        logger.error(f"[LoreAnalyzer] Analysis failed: {e}")

    return {}


async def analyze_character_sheet(
    client: genai.Client,
    model_id: str,
    sheet_text: str
) -> Dict[str, Any]:
    """
    [Logos - CharacterExtractor]
    단일 캐릭터 설정 텍스트를 분석하여 구조화된 PC 데이터를 추출합니다.
    """
    if not sheet_text:
        return {}

    system_prompt = """You are an expert TRPG Character Designer.
Extract detailed character information from the provided text to create a structured character sheet.

## Extraction Rules:
1. **Name/Role/Species**: Identify the basic identity.
2. **Appearance/Personality/Background**: Integrate provided details into concise Korean descriptions.
3. **Passives (Traits)**: Identify permanent skills, traits, or abilities. 
   - Return a list of objects: {"name": "...", "desc": "..."}.
4. **Inventory**: Identify items and equipment. 
   - Return a dict: {"Item": "Quantity"}.
5. **Language**: All descriptions must be in KOREAN.

## Output JSON Schema:
{
  "name": "...",
  "role": "...",
  "species": "...",
  "appearance": "기계 의수, 흉터 등 외양 묘사",
  "description": "성격, 말투, 특징 요약",
  "background": "과거 이력 및 배경 설정",
  "passives": [ {"name": "특성1", "desc": "효과 설명"} ],
  "inventory": { "아이템1": "1개" }
}"""

    try:
        gen_config = types.GenerateContentConfig(
            response_mime_type="application/json",
            temperature=0.1
        )
        contents = [
            types.Content(
                role="user",
                parts=[types.Part(text=f"{system_prompt}\n\n[CHARACTER SHEET TEXT]\n{sheet_text}")]
            )
        ]

        result = await api_call_with_retry(
            client, model_id, contents, gen_config, 
            operation_name="Character Sheet Analysis"
        )
        
        if result:
            return safe_parse_json(result)

    except Exception as e:
        logging.error(f"[CharacterAnalyzer] Analysis failed: {e}")

    return {}
