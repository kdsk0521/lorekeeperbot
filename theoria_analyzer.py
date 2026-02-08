"""
Lorekeeper UNE - Integrated Theoria Analyzer (좌뇌 분석 엔진)
인지 + 분석 통합: 상황 관찰, 의도 해석, Position/Effect, Psyche, Narrative Chain
"""

import logging
import json
from typing import Dict, Any, Optional

import config
import bot_utils
import text_resources
import analysis_resources
from orchestration_context import GameContext
from google.genai import types

# [SYSTEM NOTE] Flash tends to hallucinate in complex models. 
# We maintain the original complexity but wrap them in clear instructional tags.

logger = logging.getLogger("Theoria")

# =========================================================
# THEORIA SYSTEM PROMPTS (UNE 통합 분석 엔진)
# =========================================================


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
                temperature=config.ANALYSIS_TEMPERATURE,
                top_k=config.ANALYSIS_TOP_K,
                top_p=config.ANALYSIS_TOP_P
                # [Gemini 3] presence_penalty/frequency_penalty not supported
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
            text_resources.CONTENT_AUTHORIZATION_MANDATE,
            "<THEORIA role='Observer and Librarian'>",
            # [Core Identity & Principles]
            analysis_resources.THEORIA_IDENTITY,
            analysis_resources.THEORIA_PRINCIPLES,
            # [Observation Method]
            analysis_resources.OBSERVER_APPARATUS,
            analysis_resources.EVIDENCE_PIPELINE,
            # [Input Analysis]
            analysis_resources.THEORIA_PC_CHECK,
            analysis_resources.THEORIA_INPUT_DECODING,
            analysis_resources.OBSERVATION_INTENT,
            # [Psychological Analysis]
            analysis_resources.COGNITIVE_ARCHITECTURE_MODEL,
            analysis_resources.THEORIA_PSYCHE,
            analysis_resources.STATE_TRACKING_FORMAT,
            # [Temporal & Memory]
            analysis_resources.TEMPORAL_ORIENTATION_PROTOCOL,
            analysis_resources.THEORIA_MEMORY,
            analysis_resources.THEORIA_TEMPORAL,
            # [Narrative & Stakes]
            analysis_resources.THEORIA_CHAIN,
            analysis_resources.THEORIA_POSITION_EFFECT,
            analysis_resources.THEORIA_ASPECTS,
            # [NPC & Judgment]
            analysis_resources.NPC_ATTITUDE_ANALYSIS,
            analysis_resources.NPC_KNOWLEDGE_TRACKING,
            analysis_resources.JUDGMENT_SUPPORT,
            # [Resource Tracking]
            analysis_resources.DOOM_MENTAL_TRACKING,
            analysis_resources.ANOMALY_DETECTION,
            analysis_resources.SENSORY_ANCHORS,
            # [Intimate Scene Psychology]
            analysis_resources.SEXUAL_PSYCHOLOGY_ANALYSIS,
            # [Workflow & Output]
            analysis_resources.THEORIA_PROCESS,
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
- "Observation": str (Korean - 중립적 관점에서 실제로 일어난 일)
- "UserIntent": str (Korean - 유저가 즉시 원하는 것)
- "CurrentLocation": str (Korean)
- "LocationRisk": "None/Low/Medium/High/Extreme"
- "TimeContext": str (Korean - e.g. "깊은 밤", "이른 아침")
- "SceneType": "normal/combat/social/summary/intimate"
- "EnergyDirection": "rising/stagnant/detonation/aftershock"

## STAKES & ENVIRONMENT
- "Position": {"value": 0.0-1.0, "reason": "Korean - 왜 이 위치인지"}
- "Effect": {"value": 0.0-1.0, "reason": "Korean - 잠재적 영향력"}
- "Aspects": ["Korean aspect - 활용 가능성 포함", ...]

## PSYCHOLOGICAL & NARRATIVE
- "psyche_states": {
    "CharName": {
        "mental": {"descriptor": "emotional label", "value": -100~+100, "primary_emotion": "plutchik"},
        "soma": {"descriptor": "physical label", "polyvagal": "ventral/sympathetic/dorsal"},
        "relation": {"descriptor": "stance toward each PC", "value": -100~+100}
    }
  }
- "narrative_chain": {
    "topic_lock": str or null,
    "chain_status": "OPEN/CLOSED/DORMANT",
    "conclusion_proximity": 0-100,
    "open_threads": ["thread type: description", ...]
  }
- "memory_triggers": [{"trigger": str, "character": str, "echo": str, "type": "traumatic/nostalgic/shameful/loving"}]

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
- "narrative_hook": str (Korean - 실패/부분성공 시 트위스트)
- "time_flow": {"ticks": 1-20, "reason": "Korean"}
- "doom_relief": {"applicable": boolean, "amount": 0-20, "reason": "Korean"}
- "mental_impact": {"applicable": boolean, "delta": -35~+20, "reason": "Korean"}
- "anomaly_profile": {"trigger": str, "category": "supernatural/psychological/social/environmental/temporal", "intensity": "Low/Mid/High/Extreme", "polarity": "positive/negative/mixed", "line": "Korean", "reason": "Korean"}

## COGNITIVE ENHANCEMENT
- "HabitusAnalysis": {
    "Economic": "English - material standing indicators",
    "Cultural": "English - knowledge/taste patterns",
    "Social": "English - network/authority position"
  }
- "SensoryAnchors": [{"anchor": "Physical sensation", "memory_link": "English - connected memory"}]

## SAFETY & TRACKING
- "PCImpersonationCheck": {"detected": boolean, "violations": [{"type": str, "severity": str}], "correction_hint": str}
- "TemporalOrientation": {"focus": "past/present/future", "intensity": 0.0-1.0}
- "NPCAttitudes": {
    "NpcName": {
        "attitude": "hostile/unfriendly/neutral/friendly/devoted",
        "trajectory": "improving/stable/declining",
        "reason": "Korean"
    }
  }
- "NPCKnowledge": {
    "NpcName": {
        "knows": ["Korean - 이 NPC가 현재 알고 있는 핵심 정보"],
        "secrets_held": ["Korean - 이 NPC가 숨기고 있는 것"],
        "would_share": boolean,
        "leak_risk": "none/low/medium/high"
    }
  }
- "IntimacyAnalysis": null OR (when SceneType="intimate") {
    "vulnerability": {"char_name": 0-100, ...},
    "desire_type": {"char_name": "attachment/power/escape/connection/validation"},
    "power_dynamic": "Korean - 주도권 분석",
    "body_memory": "Korean - 과거 경험과의 연결"
  }
- "QualityFlags": {
    "convergence_warning": boolean,
    "echo_warning": boolean,
    "stagnation_warning": boolean
  }
- "RelevantContext": ["Quoted lore/rule directly applicable", ...]
- "RelevantNPCs": ["NPC name from roster that is relevant to THIS scene (max 5)"]
</output_schema>
"""

    def _build_pc_section(self, anchors: dict) -> str:
        """PC 정보 섹션 빌드 (솔로/다인 자동 분기)"""
        all_pcs = anchors.get("all_pcs", {})
        acting_uid = anchors.get("acting_user_id", "")

        # 다인 플레이 (2명 이상)
        if len(all_pcs) > 1:
            acting_mask = "Unknown"
            for uid, pc in all_pcs.items():
                if uid == acting_uid:
                    acting_mask = pc.get("mask", "Unknown")
                    break

            lines = [f"### 3. PLAYER CHARACTERS (행동자: {acting_mask})"]
            for uid, pc in all_pcs.items():
                mask = pc.get("mask", "Unknown")
                marker = " (행동자)" if uid == acting_uid else ""
                lines.append(f"\n**[{mask}]{marker}**")
                lines.append(f"- Appearance: {pc.get('appearance', 'N/A')}")
                lines.append(f"- Personality: {pc.get('personality', 'N/A')}")
                lines.append(f"- Passives: {pc.get('passives', [])}")
                lines.append(f"- Mental: {pc.get('mental_value', 100)}")
            return "\n".join(lines)

        # 솔로 플레이 (기존 형식 유지)
        lines = ["### 3. PLAYER CHARACTER"]
        lines.append(f"- **Appearance**: {anchors.get('appearance', 'N/A')}")
        lines.append(f"- **Personality**: {anchors.get('personality', 'N/A')}")
        lines.append(f"- **Background**: {anchors.get('background', 'N/A')}")
        lines.append(f"- **Passives**: {anchors.get('passives', [])}")
        lines.append(f"- **Inventory**: {anchors.get('inventory', [])}")
        lines.append(f"- **Relations**: {anchors.get('relations', [])}")
        lines.append(f"- **Memos**: {anchors.get('memos', [])}")
        return "\n".join(lines)

    def _build_mental_line(self, anchors: dict, bus) -> str:
        """Mental 상태 라인 빌드 (솔로/다인 분기)"""
        all_pcs = anchors.get("all_pcs", {})
        if len(all_pcs) > 1:
            parts = []
            for uid, pc in all_pcs.items():
                mask = pc.get("mask", "Unknown")
                mental = pc.get("mental_value", 100)
                parts.append(f"{mask}: {mental}")
            return f"- **Mental (PC별)**: {' / '.join(parts)}"
        return f"- **Mental (PC Mental Health)**: {bus.mental.get('value', 100)}"

    def _build_npc_context(self, anchors: dict) -> str:
        """NPC 태도 + 지식 상태를 프롬프트에 포함"""
        parts = []
        attitudes = anchors.get("stored_npc_attitudes", {})
        knowledge = anchors.get("stored_npc_knowledge", {})
        if not attitudes and not knowledge:
            return ""

        parts.append("### 4b. NPC STATE (Previous Turn)")
        for npc_name in set(list(attitudes.keys()) + list(knowledge.keys())):
            npc_lines = [f"**{npc_name}**:"]
            att = attitudes.get(npc_name, {})
            if att:
                npc_lines.append(f"  Attitude={att.get('attitude', 'neutral')} ({att.get('reason', '')})")
            kn = knowledge.get(npc_name, {})
            if kn and kn.get("knows"):
                knows_str = "; ".join(kn["knows"][:5])
                npc_lines.append(f"  Knows: [{knows_str}]")
                if kn.get("secrets_held"):
                    npc_lines.append(f"  Secrets: [{'; '.join(kn['secrets_held'][:3])}]")
                npc_lines.append(f"  LeakRisk={kn.get('leak_risk', 'none')}")
            parts.append("\n".join(npc_lines))

        return "\n".join(parts)

    def _build_session_memory_context(self, anchors: dict) -> str:
        """세션 메모리(active_threads, arc, NPC schedules)를 프롬프트에 포함"""
        mem = anchors.get("session_memory", {})
        if not mem:
            return ""

        parts = []
        arc = mem.get("current_arc")
        if arc:
            parts.append(f"- **Current Arc**: {arc}")

        threads = mem.get("active_threads", [])
        if threads:
            parts.append(f"- **Active Threads**: {'; '.join(threads[:8])}")

        npc_schedules = mem.get("npc_summaries", {})
        if npc_schedules:
            sched_items = [f"{k}: {v}" for k, v in list(npc_schedules.items())[:6]]
            parts.append(f"- **NPC Activity**: {'; '.join(sched_items)}")

        world_changes = mem.get("world_changes", [])
        if world_changes:
            parts.append(f"- **Recent World Changes**: {'; '.join(world_changes[-5:])}")

        needs = mem.get("basic_needs_flags", {})
        active_needs = [k for k, v in needs.items() if v]
        if active_needs:
            parts.append(f"- **PC Physical State**: {', '.join(active_needs)}")

        if not parts:
            return ""
        return "### 4c. SESSION MEMORY (Accumulated)\n" + "\n".join(parts)

    def _build_prompt(self, context: GameContext) -> str:
        """분석 프롬프트 생성"""
        req = context.request
        anchors = context.narrative_anchors
        bus = context.shared_bus

        pc_section = self._build_pc_section(anchors)
        mental_line = self._build_mental_line(anchors, bus)
        npc_context = self._build_npc_context(anchors)
        npc_roster = anchors.get("npc_roster", "")
        session_memory_context = self._build_session_memory_context(anchors)

        return f"""## ANALYSIS REQUEST

### 1. USER INPUT
"{req.user_input}"

### 2. CURRENT STATE
- **Genre**: {req.genres}
- **Doom (World Tension)**: {bus.doom.get('value', 0)}
{mental_line}

{pc_section}

### 4. WORLD CONTEXT
- **Core Theme**: {req.lore_summary.get('theme', 'General TRPG')}
- **Anomaly Seeds**: {', '.join(req.lore_summary.get('anomaly_seeds', [])) or 'None'}
- **Major Locations**: {req.lore_summary.get('locations', 'Current surroundings')}

{npc_context}

### 4c. NPC ROSTER (Select relevant NPCs for RelevantNPCs field, max 5)
{npc_roster or '[No NPCs registered]'}

{session_memory_context}

### 5. RECENT HISTORY
{req.history_text or '[No history]'}

### 6. LORE REFERENCE
{req.lore_text[:2000] if req.lore_text else '[No lore loaded]'}

---
Perform FULL Theoria analysis and return JSON with ALL required fields.
"""
