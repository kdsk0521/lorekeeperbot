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
import domain_manager
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

    @staticmethod
    def _extract_active_genres(genres: Any) -> list:
        """RequestData.genres(dict)에서 이론 강조용 장르 리스트를 추출."""
        if isinstance(genres, str):
            genres = [genres]
        if isinstance(genres, list):
            out = [str(g).strip() for g in genres if str(g).strip()]
            return out or ["modern", "drama"]

        if isinstance(genres, dict):
            out = []
            for key in ("stage", "flavor", "lens"):
                val = genres.get(key, [])
                if isinstance(val, str):
                    val = [val]
                if not isinstance(val, list):
                    continue
                for item in val:
                    s = str(item).strip()
                    if not s or s in out:
                        continue
                    out.append(s)
            return out or ["modern", "drama"]

        return ["modern", "drama"]

    async def analyze_input(self, context: GameContext) -> Dict[str, Any]:
        """전체 분석을 수행하고 결과를 반환합니다."""
        if not self.client:
            return {"error": "No client"}

        prompt = self._build_prompt(context)

        # Extract genre and scene context for conditional loading
        req = context.request
        anchors = context.narrative_anchors
        active_genres = self._extract_active_genres(req.genres)
        scene_context = {
            "scene_type": anchors.get("scene_type", "normal"),
            "intimate_module": True,
            "pending_flashback": bool(anchors.get("pending_flashback")),
        }
        system_instruction = self._build_system_instruction(active_genres, scene_context)
        
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

    def _build_system_instruction(self, active_genres=None, scene_context=None) -> str:
        """Theoria v2.0 시스템 프롬프트 조립 — build_analysis_directive() 사용"""
        from theory_emphasis_engine import build_analysis_directive, get_turn_spotlight

        active_genres = active_genres or ["modern", "drama"]
        scene_context = scene_context or {}

        # 코어 이론 블록 (PART A~E, 항상 로딩)
        core_theories = "\n\n".join([
            "<THEORIA role='Observer and Librarian'>",
            analysis_resources.THEORIA_IDENTITY_V2,
            analysis_resources.ANALYTICAL_LENSES_ESTABLISHED,
            analysis_resources.ANALYTICAL_LENSES_CULTURAL,
            analysis_resources.ANALYTICAL_LENSES_CUSTOM,
            analysis_resources.ANALYTICAL_LENSES_LITERARY,
        ])

        # 규칙표 (항상 로딩)
        rule_tables = "\n\n".join([
            analysis_resources.THEORIA_PC_CHECK,
            analysis_resources.STATE_TRACKING_V2,
            analysis_resources.OBSERVATION_INTENT,
            analysis_resources.TEMPORAL_ORIENTATION_V2,
            analysis_resources.THEORIA_CHAIN,
            analysis_resources.THEORIA_POSITION_EFFECT,
            analysis_resources.THEORIA_ASPECTS,
            analysis_resources.THEORIA_MEMORY,
            analysis_resources.NPC_ATTITUDE_ANALYSIS,
            analysis_resources.NPC_KNOWLEDGE_V2,
            analysis_resources.JUDGMENT_SUPPORT,
            analysis_resources.DOOM_MENTAL_TRACKING,
            analysis_resources.ANOMALY_DETECTION,
            analysis_resources.SENSORY_ANCHORS,
            analysis_resources.ITEM_AWARENESS,
        ])

        # 조건부 규칙표
        if scene_context.get("scene_type") == "intimate":
            rule_tables += "\n\n" + analysis_resources.SEXUAL_PSYCHOLOGY_ANALYSIS
        if scene_context.get("pending_flashback"):
            rule_tables += "\n\n" + analysis_resources.FLASHBACK_REST_DETECTION

        directive = build_analysis_directive(
            active_genres=active_genres,
            core_theories=core_theories,
            rule_tables=rule_tables,
            content_mandate=text_resources.CONTENT_AUTHORIZATION_MANDATE,
        )

        # Rotation Spotlight: 매 턴 5개 이론 랜덤 하이라이트
        spotlight = get_turn_spotlight(5)

        return directive + "\n\n" + spotlight + "\n\n" + self._get_output_schema() + "\n</THEORIA>"

    def _get_output_schema(self) -> str:
        """출력 스키마 정의 (v2.0 — 16 new fields, mental→psyche rename)"""
        return """
<output_schema>
Return valid JSON with ALL these fields (Korean values where specified):


## INPUT & CONTEXT
- "InputAnalysis": {"Original": str, "Enhanced": str, "Plausibility": "High/Low/Impossible", "LogicTrace": [], "Momentum": "Open/Closed"}
- "Observation": str (Korean - 중립적 관점에서 실제로 일어난 일. 해석 금지, 사실만.)
- "UserIntent": str (Korean - 유저가 즉시 원하는 것)
- "CurrentLocation": str (Korean)
- "LocationRisk": "None/Low/Medium/High/Extreme"
- "TimeContext": str (Korean - e.g. "깊은 밤", "이른 아침")
- "SceneType": "normal/combat/social/summary/intimate"
- "EnergyDirection": "rising/stagnant/detonation/aftershock"


## STAKES & ENVIRONMENT
- "Position": {"value": 0.0-1.0, "reason": "Korean - 왜 이 위치인지"}
- "Effect": {"value": 0.0-1.0, "reason": "Korean - 잠재적 영향력"}
- "Aspects": [{"text": "Korean aspect", "for_or_against": "for/against", "reason": "Korean"}, ...]


## CHARACTER ANALYSIS (psyche_states)
Fill soma BEFORE psyche (James-Lange + 五蘊 order). soma and psyche are INDEPENDENT (Cartesian Dualism).

- "psyche_states": {
    "CharName": {
        "psyche": {
            "descriptor": "Korean - MSE 기반 관찰 가능한 정서 징후",
            "value": -100~+100,
            "primary_emotion": "plutchik enum (陰陽: note opposing seed within)",
            "active_needs": ["henderson/erikson enum - 현재 행동 지배하는 욕구 max 2"],
            "self_opacity": "str or null (Self-Opacity: 'claims X — actual: Y' format. null = self-aware)",
            "decision_mode": "reactive/deliberate (Kahneman + Carstensen)",
            "coping": "problem_focused/emotion_focused/avoidant/null (Lazarus. null = no stressor)"
        },
        "soma": {
            "descriptor": "Korean - SOAP-OA 기반 관찰 가능한 신체 신호만. 감정 라벨 금지.",
            "polyvagal": "ventral/sympathetic/dorsal (Porges: 3+ signals required)",
            "cultural_affect": "han/jeong/hwabyung/nunchi/chaemyeon/simma/gi/null",
            "env_influence": "str or null (Nightingale: 환경→심리 영향. null = negligible)"
        },
        "relation": {
            "descriptor": "Korean - PC에 대한 현재 태도를 구체적 행동으로",
            "value": -100~+100,
            "attachment": "secure/anxious/avoidant/disorganized (Bowlby: from behavioral evidence)",
            "phase": "orientation/identification/exploitation/resolution (Peplau: cannot skip stages)",
            "logos_layer": "str (Logos [CUSTOM]: current layer state + THIS TURN behavioral hint)",
            "value_conflict": "str or null ('X vs Y' format + resolution direction. null = no conflict)",
            "stage": "front/back (Goffman: by audience, not just location)"
        },
        "deep_read": "str (Four-Layer [CUSTOM]: Surface→Adaptation→Core→Lack in 1 sentence each. Lack is never stated by character.)"
    }
  }


## NARRATIVE TRACKING
- "narrative_chain": {
    "topic_lock": str or null,
    "chain_status": "OPEN/CLOSED/DORMANT (Scheherazade: CLOSED + no threads = violation → inject hook)",
    "conclusion_proximity": 0-100,
    "open_threads": ["thread type: description", ...],
    "silence_type": "reflective/hesitant/heavy/tense/null (間/Ma: classify when dialogue pauses)"
  }
- "memory_triggers": [{"trigger": str, "character": str, "echo": str, "type": "traumatic/nostalgic/shameful/loving (Fermentation Recall: current state distorts memory)"}]


## JUDGMENT SUPPORT
- "needs_judgment": boolean
- "action_meta": {"action": "Korean", "type": "combat/social/exploration/stealth/survival/crafting/general", "resource_axis": "vigor/composure/both", "difficulty": "easy/normal/hard/extreme"}
- "asset_evaluation": {
    "reason": "Korean",
    "modifications": [{"label": "Korean", "value": int}],
    "memo_relevant": ["Korean short note", ...],
    "defense_success": boolean
  }


## NARRATIVE HOOKS & TIME
- "narrative_hook": str (Korean - 실패/부분성공 시 트위스트. Scheherazade 준수.)
- "time_flow": {"ticks": 1-20, "reason": "Korean"}
- "doom_relief": {"applicable": boolean, "amount": 0-20, "reason": "Korean"}
- "mental_impact": {"applicable": boolean, "vigor_delta": -35~+20, "composure_delta": -35~+20, "reason": "Korean"}
- "anomaly_profile": {"trigger": str, "category": "supernatural/psychological/social/environmental/temporal", "intensity": "Low/Mid/High/Extreme", "polarity": "positive/negative/mixed", "disruption_axis": "vigor/composure/both (which PC resource axis this anomaly disrupts — Horror/Action→vigor, Romance/Social→composure, Extreme→both)", "adaptation_group": ["1-3 items from ADAPTATION_TAXONOMY: undead/dragon/eldritch/cursed/spirit/divine/demonic/shapeshifter/fear/deception/exposure/betrayal/madness/guilt/obsession/encounter/jealousy/intimacy/separation/rivalry/loyalty/timing/cascade/authority/environment/resource/crowd/evidence/surveillance/leak/secret/misinformation"], "theory_basis": "str — 방어에 적용되는 이론 (e.g. 'Continuum+TMT', 'Nunchi+Chaemyeon', 'Prospect+BATNA')", "defense_hint": "str — 이 이변에 대한 방어 힌트 1문장 (Korean)", "line": "Korean - 이변의 서사적 묘사 1문장", "protective_item": str or null, "reason": "Korean"}


## COGNITIVE ENHANCEMENT
- "HabitusAnalysis": {
    "Economic": "English - material standing indicators",
    "Cultural": "English - knowledge/taste patterns",
    "Social": "English - network/authority position"
  }
- "SensoryAnchors": [{"anchor": "Physical sensation (Somatic Marker/Damasio)", "memory_link": "English - connected memory"}]


## NPC TRACKING
- "NPCAttitudes": {
    "NpcName": {
        "attitude": "hostile/unfriendly/neutral/friendly/devoted",
        "trajectory": "improving/stable/declining",
        "reason": "Korean (오륜 role expectation 위반 시 명시)"
    }
  }
- "NPCKnowledge": {
    "NpcName": {
        "knows": ["Korean - 현재 알고 있는 핵심 정보"],
        "secrets_held": ["Korean - 숨기고 있는 것"],
        "would_share": boolean,
        "leak_risk": "none/low/medium/high (Curse of Knowledge: 아는 것을 숨기기 어려움)",
        "false_beliefs": ["Korean - 사실과 다르게 믿고 있는 것 (Theory of Mind)"]
    }
  }


## SAFETY & QUALITY
- "PCImpersonationCheck": {"detected": boolean, "violations": [{"type": str, "severity": str}], "correction_hint": str}
- "TemporalOrientation": {"focus": "past/present/future", "intensity": 0.0-1.0}
- "QualityFlags": {
    "convergence_warning": "boolean - unearned comfort / premature resolution",
    "echo_warning": "boolean - NPC mirroring PC",
    "stagnation_warning": "boolean - 3+ turns flat",
    "mse_deviation": "boolean - MSE mental state anomaly detected",
    "dissonance_flag": "boolean - NPC contradictory beliefs/actions (Festinger)",
    "redemption_warning": "boolean - NPC showing unearned positive behavioral change (Bandura/Maruna)"
  }
- "RelevantContext": ["Quoted lore/rule directly applicable", ...]
- "RelevantNPCs": ["NPC name from roster relevant to THIS scene (max 5)"]
- "relevant_chunks": [0, 2, 5] (indices from LORE CHUNKS — up to 5 most relevant)


## CONDITIONAL MODULES (output null if not triggered)

### IntimacyAnalysis (SceneType="intimate" AND lorebook.intimate_module=true)
- "IntimacyAnalysis": null OR {
    "window_check": {"char_name": "within/above/below (Siegel, from polyvagal state)"},
    "dual_control": {"char_name": {"SES": "str - excitation factors", "SIS": "str - inhibition factors"}},
    "desire_type": {"char_name": "attachment/power/escape/connection/validation/sensation (Basson)"},
    "power_dynamic": "Korean (Benjamin Intersubjectivity - mutual recognition status)",
    "body_memory": "Korean (van der Kolk - involuntary echoes of past experience)",
    "post_encounter_prediction": {"char_name": "attachment activation pattern - predicted post-behavior"}
  }

### Flashback Evaluation (trigger pattern detected)
- "flashback_eval": null OR {
    "detected": boolean,
    "declaration": "Korean - 1-sentence summary of retroactive claim",
    "plausibility": "plausible/stretch/impossible",
    "relevant_passive": "passive name or null",
    "tier": "trivial/standard/bold",
    "vigor_ratio": 0.0~1.0,
    "composure_ratio": 0.0~1.0,
    "reason": "Korean"
  }

### Rest Evaluation (rest trigger detected)
- "rest_eval": null OR {
    "detected": boolean,
    "quality": "full/brief/interrupted",
    "safe_location": boolean,
    "reason": "Korean"
  }

### Item Tracking (item interaction detected)
- "item_usage": null OR {
    "items_consumed": ["item name", ...],
    "items_gained": ["item name", ...],
    "reason": "Korean"
  }
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
        """Vigor/Composure 상태 라인 빌드 (솔로/다인 분기)"""
        all_pcs = anchors.get("all_pcs", {})
        if len(all_pcs) > 1:
            parts = []
            for uid, pc in all_pcs.items():
                mask = pc.get("mask", "Unknown")
                vigor = pc.get("vigor_value", 100)
                composure = pc.get("composure_value", 100)
                parts.append(f"{mask}: 기력{vigor}/평정{composure}")
            return f"- **Vigor/Composure (PC별)**: {' / '.join(parts)}"
        vigor_val = bus.vigor.get('value', 100)
        composure_val = bus.composure.get('value', 100)
        return f"- **Vigor**: {vigor_val} | **Composure**: {composure_val}"

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

    @staticmethod
    def _format_anomaly_seeds(seeds: list) -> str:
        """구조화 씨앗이면 상세 형식, str이면 기존 형식으로 포맷."""
        if not seeds:
            return "None"
        if isinstance(seeds[0], dict):
            lines = []
            for s in seeds:
                name = s.get("name", "?")
                axis = s.get("axis", "?")
                groups = s.get("adaptation_group", [])
                lines.append(f"- {name} (axis:{axis}, groups:{groups})")
            return "\n" + "\n".join(lines)
        return ", ".join(str(s) for s in seeds)

    def _build_lore_structured(self, lore_summary: dict) -> str:
        """lore_summary의 구조화된 데이터를 Theoria 프롬프트로 변환"""
        parts = []

        # Locations
        locations = lore_summary.get("locations", [])
        if isinstance(locations, list) and locations:
            loc_lines = []
            for loc in locations:
                if isinstance(loc, dict):
                    name = loc.get("name", "?")
                    desc = loc.get("desc", "")
                    danger = loc.get("danger", "")
                    danger_tag = f" [{danger}]" if danger else ""
                    loc_lines.append(f"  - {name}{danger_tag}: {desc}")
                else:
                    loc_lines.append(f"  - {loc}")
            parts.append("- **Locations**:\n" + "\n".join(loc_lines))
        elif isinstance(locations, str) and locations:
            parts.append(f"- **Locations**: {locations}")

        # Rules
        rules = lore_summary.get("rules", [])
        if rules:
            parts.append("- **World Rules**:\n" + "\n".join(f"  - {r}" for r in rules))

        # Factions
        factions = lore_summary.get("factions", [])
        if factions:
            fac_lines = []
            for f in factions:
                if isinstance(f, dict):
                    fac_lines.append(f"  - {f.get('name', '?')}: {f.get('desc', '')} ({f.get('stance', '')})")
                else:
                    fac_lines.append(f"  - {f}")
            parts.append("- **Factions**:\n" + "\n".join(fac_lines))

        # Key Events
        events = lore_summary.get("key_events", [])
        if events:
            parts.append("- **Key Events**:\n" + "\n".join(f"  - {e}" for e in events))

        return "\n".join(parts) if parts else "- **Locations**: Current surroundings"

    def _build_chunk_index(self, chunks: list) -> str:
        """청크 라벨 인덱스를 Theoria 프롬프트에 포함 (선택용)"""
        if not chunks:
            return ""
        lines = ["### 6. LORE CHUNKS (Select relevant indices for relevant_chunks field, max 5)"]
        for chunk in chunks:
            idx = chunk.get("index", 0)
            label = chunk.get("label", f"Section {idx}")
            lines.append(f"[{idx}] {label}")
        return "\n".join(lines) + "\n"

    def _build_pending_flashback(self, anchors: dict) -> str:
        """대기 중인 회상 선언을 프롬프트에 포함"""
        pending = anchors.get("pending_flashback")
        if not pending:
            return ""
        content = pending.get("content", "") if isinstance(pending, dict) else str(pending)
        if not content:
            return ""
        return f"""### 5b. PENDING FLASHBACK DECLARATION
The player explicitly declared a flashback via !회상 command:
"{content}"
Evaluate this in flashback_eval field. Check plausibility, passive match, assign tier, and provide vigor_ratio/composure_ratio split.

"""

    def _build_telescope_quality_context(self, anchors: dict) -> str:
        """Inject recent telescope fail history for self-correction."""
        channel_id = str(anchors.get("channel_id", "")).strip()
        if not channel_id:
            return ""
        context_text = domain_manager.build_telescope_context(channel_id, n=3)
        if not context_text:
            return ""
        return f"### 5c. RECENT QUALITY GATE FAILURES\n{context_text}\n\n"

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
- **Anomaly Seeds**: {self._format_anomaly_seeds(req.lore_summary.get('anomaly_seeds', []))}
{self._build_lore_structured(req.lore_summary)}

{npc_context}

### 4c. NPC ROSTER (Select relevant NPCs for RelevantNPCs field, max 5)
{npc_roster or '[No NPCs registered]'}

{session_memory_context}

### 5. RECENT HISTORY
{req.history_text or '[No history]'}

{self._build_pending_flashback(anchors)}{self._build_telescope_quality_context(anchors)}{self._build_chunk_index(req.lore_chunks)}
---
Perform FULL Theoria analysis and return JSON with ALL required fields.
"""
