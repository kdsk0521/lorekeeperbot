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

        "========================================\n"
        "### ACTION JUDGMENT (행동 판정 - GM 역할)\n"
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

        "**Suggested Outcome Logic:**\n"
        "- trivial → success\n"
        "- easy + no negative modifier → success\n"
        "- easy + negative modifier → partial\n"
        "- normal + relevant passive → success\n"
        "- normal + no passive → partial or failure\n"
        "- hard + passive + proper tools → partial or success\n"
        "- hard + no passive → failure\n"
        "- extreme → usually failure, critical_success only with perfect conditions\n\n"

        "**Modifiers (add to list):**\n"
        "+ (increase chance): 관련 패시브 보유, 적절한 도구, 충분한 시간, 유리한 환경\n"
        "- (decrease chance): 도구 없음, 시간 압박, 적대적 환경, 부상 상태, 첫 시도\n\n"

        "**Example:**\n"
        "Player input: '자물쇠를 딴다'\n"
        "PC has: no lockpicking passive, no tools\n"
        "Situation: guards nearby\n"
        "→ ActionJudgment: {\n"
        '    "action": "자물쇠 따기",\n'
        '    "difficulty": "normal",\n'
        '    "relevant_passive": null,\n'
        '    "relevant_item": "도구 없음",\n'
        '    "modifiers": ["도구 없음", "시간 압박(경비병)"],\n'
        '    "suggested_outcome": "failure"\n'
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
        '  "ActionJudgment": {\n'
        '    "action": "플레이어가 시도하는 행동",\n'
        '    "difficulty": "trivial/easy/normal/hard/extreme",\n'
        '    "relevant_passive": "관련 패시브 있으면 이름, 없으면 null",\n'
        '    "relevant_item": "필요한 도구 보유 여부",\n'
        '    "modifiers": ["상황 수정자들"],\n'
        '    "suggested_outcome": "success/partial/failure/critical_success/critical_failure"\n'
        '  } OR null,\n'
        '  "Need": "Logical next step for Right Hemisphere",\n'
        '  "SystemAction": { "tool": "Quest/Memo/NPC", "type": "Add/Complete/Archive", "content": "..." } OR null,\n'
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
        "**NOTE:** PlayerUpdate, PlayerMemoryUpdate, QuestUpdate are now handled by a separate\n"
        "extraction process after narrative generation. Focus only on scene analysis fields above.\n\n"

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
    
    # 기본값 반환
    return {
        "CurrentLocation": "Unknown",
        "LocationRisk": "Low",
        "TimeContext": "Unknown",
        "Observation": "Analysis Failed",
        "Need": "Proceed with Caution",
        "SystemAction": None
    }

def build_judgment_context(action_judgment: Dict[str, Any]) -> str:
    """
    ActionJudgment를 우뇌에 전달할 컨텍스트 문자열로 변환
    """
    if not action_judgment:
        return ""
    
    return (
        f"### [GM JUDGMENT - MUST FOLLOW]\n"
        f"**Action:** {action_judgment.get('action', 'N/A')}\n"
        f"**Difficulty:** {action_judgment.get('difficulty', 'normal')}\n"
        f"**Relevant Passive:** {action_judgment.get('relevant_passive') if action_judgment.get('relevant_passive') else 'None'}\n"
        f"**Equipment:** {action_judgment.get('relevant_item', 'N/A')}\n"
        f"**Modifiers:** {', '.join(action_judgment.get('modifiers', [])) if action_judgment.get('modifiers') else 'None'}\n"
        f"**⚠️ SUGGESTED OUTCOME: {action_judgment.get('suggested_outcome', 'partial').upper()}**\n\n"
        f"**INSTRUCTION:** You MUST narrate according to this judgment.\n"
        f"- Do NOT auto-succeed if outcome is 'failure' or 'partial'\n"
        f"- Describe the ATTEMPT and the RESULT based on suggested_outcome\n"
        f"- Failure creates drama and choices, not punishment\n\n"
    )
