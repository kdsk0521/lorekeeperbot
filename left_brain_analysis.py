"""
=========================================================
   LEFT BRAIN - ANALYSIS (좌뇌 A - 장면 분석)
   
   역할:
   - 현재 장면 분석 (위치, 시간, NPC, 분위기 등)
   - ActionJudgment (행동 판정 힌트)
   - 우뇌에 전달할 컨텍스트 생성
=========================================================
"""

import json
import logging
import random
from typing import Dict, Any, List, Optional
from google.genai import types

# 공통 유틸 import
from memory_system import api_call_with_retry, safe_parse_json, COGNITIVE_ARCHITECTURE_MODEL, STATE_TRACKING_FORMAT, TEMPORAL_ORIENTATION_PROTOCOL

logger = logging.getLogger("LeftBrain-Analysis")


# =========================================================
# 메인 분석 함수
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

        "### SYSTEM ACTION RULES (manage Quests/Memos/NPCs automatically)\n"
        "SystemAction triggers automatically based on narrative events.\n\n"
        
        "**Quest Actions:**\n"
        "- `{\"tool\": \"Quest\", \"type\": \"Add\", \"content\": \"Quest Description\"}` — When NPC gives mission, player discovers objective\n"
        "- `{\"tool\": \"Quest\", \"type\": \"Complete\", \"content\": \"Part of existing quest text\"}` — When objective achieved, mission accomplished\n\n"
        
        "**Memo Actions:**\n"
        "- `{\"tool\": \"Memo\", \"type\": \"Add\", \"content\": \"Memo Content\"}` — Important info: clues, NPC names, codes, locations, items acquired, rumors/gossip heard\n"
        "- `{\"tool\": \"Memo\", \"type\": \"Archive\", \"content\": \"Part of existing memo text\"}` — When memo becomes obsolete (item used, info no longer relevant)\n\n"
        
        "**NPC Actions:**\n"
        "- `{\"tool\": \"NPC\", \"type\": \"Add\", \"content\": \"Name: Description\"}` — When new named NPC introduced\n\n"
        
        "**Examples:**\n"
        "- Player receives letter with mission → Quest Add\n"
        "- Player defeats boss mentioned in quest → Quest Complete\n"
        "- Player finds password \"1234\" → Memo Add\n"
        "- Player hears rumor about \"haunted forest at night\" → Memo Add\n"
        "- NPC mentions \"black market in the sewers\" → Memo Add\n"
        "- Player uses the password successfully → Memo Archive\n"
        "- Player meets \"Cheolsu\" the blacksmith → NPC Add\n\n"
        
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

        "========================================\n"
        "### ACTION JUDGMENT (Game Master Role)\n"
        "========================================\n"
        "You are the GM. Judge player actions realistically.\n"
        "**Player input = ATTEMPT to try, NOT guaranteed success.**\n\n"

        "**Check before judging:**\n"
        "1. What is the player trying to do?\n"
        "2. Does PC have relevant passive/skill? (check player_context)\n"
        "3. Does PC have necessary equipment? (check inventory)\n"
        "4. What's the inherent difficulty?\n"
        "5. Are there situational modifiers?\n\n"

        "**Difficulty Scale:**\n"
        "- `trivial`: Walking, talking, basic tasks (auto-success)\n"
        "- `easy`: Low fence climb, friendly NPC persuasion\n"
        "- `normal`: Standard lock, rough wall climb\n"
        "- `hard`: Complex lock, sheer cliff, hostile NPC persuasion\n"
        "- `extreme`: Legendary feats, near-impossible odds\n\n"
        
        "**Judge Required:**\n"
        "- Combat, Stealth, Lockpicking, Persuading unwilling NPCs, Dangerous physical feats\n"
        "- Any action with MEANINGFUL consequence of failure\n\n"
        
        "**No Judge Needed:**\n"
        "- Walking/Talking safely, Buying items, Friendly interactions\n\n"

        "**Modifiers (add to list):**\n"
        "- Use `passive_[name]`: +15~+25 (relevant skill)\n"
        "- Use `tool_[name]`: +10~+15 (proper tool)\n"
        "- Use `condition_[status]`: -10~-20 (injury/fatigue)\n"
        "- Use `environment_[desc]`: +/- 5~15 (darkness, noise, rain)\n"
        "- Use `time_pressure`: -10~-15\n"
        "- Use `no_tool`: -10~-20\n\n"

        "**Example:**\n"
        "Player input: 'Picking the lock'\n"
        "PC has: no lockpicking passive, no tools\n"
        "Situation: guards nearby\n"
        "→ ActionJudgment: {\n"
        '    "action": "Picking lock",\n'
        '    "difficulty": "normal",\n'
        '    "difficulty_reason": "Standard lock but no tools",\n'
        '    "modifiers": [\n'
        '        {"no_tool": -15},\n'
        '        {"time_pressure": -10}\n'
        '    ]\n'
        "  }\n\n"

        "**IMPORTANT:** Set to `null` if player input has no action to judge (e.g., just dialogue).\n\n"

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
        '    "NPC_Name": {"attitude": "hostile/unfriendly/neutral/friendly/devoted", "reason": "why"},\n'
        '    "...": {...}\n'
        '  },\n'
        '  "NPCInteraction": {\n'
        '    "participants": ["NPC1", "NPC2"],\n'
        '    "type": "gossip/argument/flirt/business/reaction",\n'
        '    "topic": "What they might discuss",\n'
        '    "mood": "tense/casual/heated/secretive"\n'
        '  } OR null,\n'
        '  "AbnormalElements": ["Dragon", "Magic", "Confession"] OR [],\n'
        '  "ExperienceCounters": {"Poisoning": 1, "MeleeCombat": 1} OR {},\n'
        '  "SceneType": "normal/gore/nsfw/gore_nsfw",\n'
        '  "ActionJudgment": {\n'
        '    "action": "Player Action being attempted",\n'
        '    "difficulty": "trivial/easy/normal/hard/extreme",\n'
        '    "difficulty_reason": "Rationale for difficulty (required)",\n'
        '    "modifiers": [\n'
        '        {"passive_PassiveName": 20},\n'
        '        {"tool_ToolName": 10},\n'
        '        {"condition_Status": -10}\n'
        '    ]\n'
        '  } OR null,\n'
        '  "Need": "Logical next step for Right Hemisphere",\n'
        '  "SystemAction": { "tool": "Quest/Memo/NPC", "type": "Add/Complete/Archive", "content": "..." } OR null,\n'
        '  "SessionMemoryUpdate": {\n'
        '    "world_summary": "Summary of current world state (only if changed)" OR null,\n'
        '    "world_changes": ["Changes in the world"] OR null,\n'
        '    "current_arc": "Current Story Arc description" OR null,\n'
        '    "active_threads": ["Newly started plot threads"] OR null,\n'
        '    "resolved_threads": ["Resolved plot threads"] OR null,\n'
        '    "npc_summaries": {"NPC_Name": "NPC summary description"} OR null\n'
        '  } OR null\n'
        "}\n"
        "\n"
        "**NOTE:** PlayerUpdate, PlayerMemoryUpdate, QuestUpdate are now handled by a separate\n"
        "extraction process after narrative generation. Focus only on scene analysis fields above.\n\n"

        "### SCENE TYPE DETECTION (Auto-Detect)\n"
        "**SceneType:** Automatically detect the nature of the current scene.\n"
        "Based on narrative context, determine if mature content descriptions are appropriate:\n\n"
        
        "- `normal`: Standard scene - default narrative style\n"
        "- `gore`: Scene involves graphic violence, torture, severe injury, body horror\n"
        "  Examples: Severe combat injury, torture, execution, mutilation, brutal death\n"
        "- `nsfw`: Scene involves intimate/romantic situations between consenting adults\n"
        "  Examples: Intimacy between lovers, adult romance, sensual situations\n"
        "- `gore_nsfw`: Scene involves both elements\n\n"
        
        "**Detection criteria:**\n"
        "- Entering combat with high stakes → consider `gore` if injuries likely\n"
        "- Romantic progression reaching intimate moment → consider `nsfw`\n"
        "- Torture/horror scenes → `gore`\n"
        "- Normal exploration/dialogue → `normal`\n\n"
        
        "**IMPORTANT:** Default to `normal` unless scene clearly warrants mature content.\n\n"

        "### ABNORMAL ELEMENTS & EXPERIENCE DETECTION\n"
        "**AbnormalElements:** List any supernatural, unusual, or extraordinary elements in the scene.\n"
        "Examples: Dragon, Magic, Ghost, Status Window, Isekai, Monster, Superpower, Confession, Duel, Kidnapping\n\n"
        "**ExperienceCounters:** Detect significant experiences that contribute to character growth.\n"
        "Use descriptive names based on what actually happened:\n"
        "- Physical trials: Poisoning, Burn, Frostbite, Falling, Fainting, Starvation etc.\n"
        "- Combat experiences: MeleeCombat, AssassinationAttempt, Surrounded etc.\n"
        "- Social/emotional: Betrayal, Rejection, Blackmail, NearDeath etc.\n"
        "- Supernatural: MagicHit, DragonEncounter, GhostSighting, DimensionShift etc.\n"
        "Only count if it ACTUALLY HAPPENED to the player character.\n"
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
        system_instruction=system_instruction,
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
    
    return {
        "CurrentLocation": "Unknown",
        "LocationRisk": "Low",
        "TimeContext": "Unknown",
        "Observation": "Analysis Failed",
        "Need": "Proceed with Caution",
        "SystemAction": None
    }


# =========================================================
# 주사위 시스템 (Dice System v5.0)
# =========================================================

def roll_dice(sides: int = 100) -> int:
    """주사위를 굴립니다."""
    return random.randint(1, sides)

def determine_result(final_roll: int, dc: int) -> str:
    """최종 결과를 판정합니다."""
    if final_roll >= dc + 30:
        return "critical_success"
    elif final_roll >= dc:
        return "success"
    elif final_roll >= dc - 20:
        return "partial"
    else:
        return "failure"

def build_action_judgment_with_roll(
    action: str,
    difficulty: str,
    difficulty_reason: str,
    modifiers_list: List[Dict[str, int]]
) -> Dict[str, Any]:
    """
    ActionJudgment를 생성합니다. (주사위 굴림 포함)
    
    Args:
        action: 플레이어 행동
        difficulty: 난이도 문자열
        difficulty_reason: 난이도 이유
        modifiers_list: AI가 분석한 수정치 리스트 [{"passive_X": 20}, ...]
    """
    
    # DC 결정
    dc_table = {
        "trivial": 10,
        "easy": 30,
        "normal": 50,
        "hard": 70,
        "extreme": 90
    }
    dc = dc_table.get(difficulty.lower(), 50)
    
    # 주사위 굴림
    base_roll = roll_dice(100)
    
    # 수정치 계산
    modifiers = {}
    modifier_total = 0
    if modifiers_list:
        for mod in modifiers_list:
            if isinstance(mod, dict):
                for k, v in mod.items():
                    modifiers[k] = v
                    modifier_total += v
    
    final_roll = base_roll + modifier_total
    
    # 결과 판정
    result = determine_result(final_roll, dc)
    
    return {
        "action": action,
        "difficulty": difficulty,
        "difficulty_reason": difficulty_reason,
        "base_roll": base_roll,
        "modifiers": modifiers,
        "modifier_total": modifier_total,
        "final_roll": final_roll,
        "dc": dc,
        "result": result
    }

def build_judgment_context_with_roll(judgment: Dict[str, Any]) -> str:
    """
    ActionJudgment를 우뇌에 전달할 컨텍스트 문자열로 변환 (주사위 결과 포함)
    """
    if not judgment:
        return ""
    
    # 수정치 문자열 생성
    mod_strs = []
    for name, value in judgment.get("modifiers", {}).items():
        sign = "+" if value >= 0 else ""
        mod_strs.append(f"{name}({sign}{value})")
    mod_text = ", ".join(mod_strs) if mod_strs else "없음"
    
    # 결과 한글화
    result_kr = {
        "critical_success": "대성공",
        "success": "성공",
        "partial": "부분 성공",
        "failure": "실패"
    }.get(judgment.get("result"), "판정 불가")
    
    return (
        f"### [GM JUDGMENT - MUST FOLLOW]\n"
        f"**Action:** {judgment.get('action', 'N/A')}\n"
        f"**Difficulty:** {judgment.get('difficulty', 'normal')} (DC {judgment.get('dc', 50)})\n"
        f"**Reason:** {judgment.get('difficulty_reason', 'N/A')}\n"
        f"**Roll:** 🎲 {judgment.get('base_roll', 0)} {'+' if judgment.get('modifier_total', 0) >= 0 else ''}{judgment.get('modifier_total', 0)} = {judgment.get('final_roll', 0)}\n"
        f"**Modifiers:** {mod_text}\n"
        f"**RESULT: {result_kr}**\n\n"
        f"**INSTRUCTION:** You MUST narrate according to this result.\n"
        f"- 대성공: Exceptional outcome, bonus effects\n"
        f"- 성공: Achieved as intended\n"
        f"- 부분 성공: Partial achievement, complication or cost\n"
        f"- 실패: Failed, possible negative consequences\n\n"
    )

def build_judgment_context(action_judgment: Dict[str, Any]) -> str:
    """Legacy wrapper for backward compatibility"""
    # Simply ignore this if we are using the new system, but keep for safety
    return ""
