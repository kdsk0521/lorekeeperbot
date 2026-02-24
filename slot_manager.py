"""
Lorekeeper TRPG Bot - Slot-Based Prompt Manager (V2 Refactored)
34단계 프롬프트 아키텍처를 위한 슬롯 관리자 모듈입니다.

[리팩토링] 기존 prompt_builder.py와 orchestration_response.py의 유틸리티들을
재사용하여 코드 중복을 최소화합니다.

[V2.1] Primacy/Recency 최적화 적용
- Primacy Zone (1-4): 핵심 철학 → AI가 가장 강하게 기억
- World Zone (5-9): 참조 데이터 → 중간 배치 (참조만 하면 됨)
- Context Zone (10-12): 현재 상황
- Cognition Zone (13-17): 분석 데이터
- Rules Zone (18-25): 행동 규칙 → Static Recency로 강화
- Dynamic Zone (27-34): 실시간 데이터 + 최종 지시 → 최강 Recency
"""

import logging
from typing import Dict, Any, Optional, List
from dataclasses import dataclass
import text_resources
import iceberg

# [레거시 재사용] 기존 모듈에서 유용한 함수 임포트
import prompt_builder as legacy_builder

logger = logging.getLogger("SlotManager")


# =========================================================
# Dynamic STATUS_WINDOW_LAYOUT Builder
# =========================================================

def _build_status_layout(active_modules: list) -> str:
    """active_modules에 따라 STATUS_WINDOW_LAYOUT을 동적으로 생성.
    OFF인 모듈의 메트릭은 포맷/예시/규칙에서 완전 제거."""
    module_set = set(active_modules or [])
    has_mental = "mental" in module_set
    has_doom = "doom" in module_set

    # --- FORMAT block ---
    fmt = ["위치 [Location] | 시간 [Month/Day, Time] | 인물 [Present Characters]"]
    ex = ["위치 하숙집 거실 | 시간 3/15, 새벽 | 인물 리미, 옥상 남자"]

    line2_fmt, line2_ex = [], []
    if has_mental:
        line2_fmt.extend(["기력 [value]", "평정 [value]"])
        line2_ex.extend(["기력 72", "평정 38"])
    if has_doom:
        line2_fmt.append("Doom [value]")
        line2_ex.append("Doom 45")

    if line2_fmt:
        fmt.append(" | ".join(line2_fmt))
        ex.append(" | ".join(line2_ex))

    if has_mental:
        line2_fmt.append("로드아웃 [used/total]")
        line2_ex.append("로드아웃 1/4")

    if has_doom:
        fmt.append("[Clock1 filled/segments] [Clock2 filled/segments ...]")
        ex.append("[조직의 추적 4/6] [붉은 문턱 2/4]")

    # --- RULES block ---
    rules = ["- Line 1: location, time, characters."]
    if has_mental and has_doom:
        rules.append("- Line 2: Vigor + Composure + Global Doom (numeric only).")
        rules.append("- Line 3: active doom clocks only. Omit line 3 if no active clock.")
    elif has_mental:
        rules.append("- Line 2: Vigor + Composure (numeric only).")
    elif has_doom:
        rules.append("- Line 2: Global Doom (numeric only).")
        rules.append("- Line 3: active doom clocks only. Omit line 3 if no active clock.")

    off_parts = []
    if not has_mental:
        off_parts.append("기력/평정(Vigor/Composure)")
    if not has_doom:
        off_parts.append("Doom/Doom Clocks")
    if off_parts:
        rules.append(f"- DISABLED: {', '.join(off_parts)} — do NOT display these metrics.")

    rules.append("- Keep it compact and stable across turns.")

    return (
        "<Status_Window_Layout>\n"
        "## SCENE HEADER FORMAT\n\n"
        "Place a compact status line at the TOP of each narrative output.\n"
        "Character profiles are accessed via !info command. Do NOT duplicate full sheets here.\n\n"
        "### FORMAT\n```\n" + "\n".join(fmt) + "\n```\n\n"
        "### EXAMPLES\n```\n" + "\n".join(ex) + "\n```\n\n"
        "### RULES\n" + "\n".join(rules) + "\n"
        "</Status_Window_Layout>"
    )


# =========================================================
# 5W1H Telescope Prefill Builder
# =========================================================

def _build_telescope_prefill(dai: dict, real_time_data: str) -> str:
    """Telescope v3 프리필: Scene.Who / Scene.When/Where를 코드에서 조립.

    코드 프리필 = GROUND_TRUTH → 환각 불가.
    모델은 Phase A 나머지 + Phase B를 채운 뒤 ┫ 닫고 산문.
    """
    scene_lines = []

    # [Scene.Who]
    psyche = dai.get("psyche_states", {})
    who_names = iceberg.translate_telescope_who(psyche)
    if who_names:
        scene_lines.append(f"  ├ [Scene.Who] {who_names}")

    # [Scene.When/Where]
    if real_time_data:
        rt_lines = [ln.strip() for ln in real_time_data.strip().split("\n") if ln.strip()]
        when_where = ""
        for ln in rt_lines[:5]:
            if "위치" in ln or "시간" in ln or "Location" in ln or "Time" in ln:
                when_where = ln
                break
        if when_where:
            scene_lines.append(f"  ├ [Scene.When/Where] {when_where}")
        else:
            scene_lines.append(f"  ├ [Scene.When/Where] {rt_lines[0][:200]}")
    else:
        observation = dai.get("observation", "")
        if observation:
            scene_lines.append(f"  ├ [Scene.When/Where] (observation) {observation[:150]}")

    if not scene_lines:
        return ""

    header = "=== Phase A: Domain Checks ===\n\n[Scene] — 장면 구조"
    return "┣\n" + header + "\n" + "\n".join(scene_lines) + "\n"


# =========================================================
# 34-Step Slot Definition (Primacy/Recency Optimized)
# =========================================================

@dataclass
class SlotDefinition:
    """개별 슬롯의 정의"""
    index: int
    name: str
    category: str  # identity, world, rules, reasoning, dynamic, etc.
    source: str    # text_resources, cognition, domain_manager, etc.
    is_static: bool = True  # True면 캐시 가능, False면 매 턴 동적


# 34개 슬롯 정의 - Primacy/Recency 최적화
SLOT_DEFINITIONS: Dict[int, SlotDefinition] = {
    # ===== PRIMACY ZONE (1-4): AI가 가장 강하게 기억하는 구간 =====
    1: SlotDefinition(1, "AI_MANDATE", "identity", "text_resources.CONTENT_AUTHORIZATION_MANDATE"),
    2: SlotDefinition(2, "AI_IDENTITY", "identity", "text_resources.AI_CORE_IDENTITY"),
    3: SlotDefinition(3, "MIRROR_WORKSHOP", "philosophy", "text_resources.MIRROR_WORKSHOP_PROTOCOL"),
    4: SlotDefinition(4, "PHYSICAL_RENDERING", "philosophy", "text_resources.PHYSICAL_RENDERING_DOCTRINE"),

    # ===== WORLD ZONE (5-9): 참조 데이터 (중간 배치 OK) =====
    5: SlotDefinition(5, "WORLD_AXIOM", "world", "text_resources.WORLD_AXIOM + MEMORY_HIERARCHY"),
    6: SlotDefinition(6, "PC_DATA", "world", "ResponseContext.player_data", is_static=False),
    7: SlotDefinition(7, "NPC_ROLES", "world", "npc_manager.get_npcs", is_static=False),
    8: SlotDefinition(8, "LORE", "world", "domain_manager.get_lore", is_static=False),
    9: SlotDefinition(9, "FERMENTED_HISTORY", "history", "fermentation + cognition.memory_triggers", is_static=False),

    # ===== CONTEXT ZONE (10-12): 현재 상황 =====
    10: SlotDefinition(10, "TEMPORAL_FLOW", "context", "text_resources.TEMPORAL_FLOW + TIME_ATMOSPHERE"),
    11: SlotDefinition(11, "CHAPTER_CONTEXT", "context", "domain_manager.get_current_chapter", is_static=False),
    12: SlotDefinition(12, "SOCIAL_INTERACTION", "context", "text_resources.INTERACTION_MODEL + NPC_BEHAVIOR"),

    # ===== COGNITION ZONE (13-17): Theoria 분석 데이터 =====
    13: SlotDefinition(13, "INPUT_ANALYSIS", "reasoning", "Theoria: InputAnalysis + Observation + UserIntent + Position/Effect", is_static=False),
    14: SlotDefinition(14, "PSYCHE_STATES", "reasoning", "Theoria: psyche_states (6-Axis)", is_static=False),
    15: SlotDefinition(15, "PSYCHE_RENDERING", "reasoning", "text_resources.PSYCHE_STATE_RENDERING"),
    16: SlotDefinition(16, "SCENE_INTELLIGENCE", "reasoning", "Theoria: Aspects + SensoryAnchors + HabitusAnalysis + narrative_hook", is_static=False),
    17: SlotDefinition(17, "EXTENDED_INTELLIGENCE", "reasoning", "Theoria: NPCKnowledge + IntimacyAnalysis", is_static=False),

    # ===== RULES ZONE (18-25): Static Recency - 행동 규칙 강화 =====
    18: SlotDefinition(18, "PC_AUTONOMY", "rules", "text_resources.PC_AUTONOMY_DOCTRINE"),
    20: SlotDefinition(20, "STATUS_LAYOUT", "rules", "_build_status_layout() dynamic"),
    21: SlotDefinition(21, "ACTION_RESOLUTION", "mechanics", "text_resources.ACTION_RESOLUTION + Aspects + SITUATION_PRIORITY"),
    22: SlotDefinition(22, "VISCERAL_CONTENT", "content", "text_resources.VISCERAL (conditional)", is_static=False),
    23: SlotDefinition(23, "MATURE_CONTENT", "content", "text_resources.MATURE (conditional)", is_static=False),
    24: SlotDefinition(24, "HYBRID_CONTENT", "content", "text_resources.HYBRID (conditional)", is_static=False),
    25: SlotDefinition(25, "STYLE", "rules", "text_resources.ANTI_CLICHE + PROSE_CRAFT"),

    # ========== CACHE BOUNDARY ==========
    26: SlotDefinition(26, "CACHE_BOUNDARY", "boundary", "==========CACHE BOUNDARY==========", is_static=False),

    # ===== DYNAMIC ZONE (27-34): 최강 Recency =====
    27: SlotDefinition(27, "OLDER_HISTORY", "dynamic", "smart_history (2~11턴 전)", is_static=False),
    28: SlotDefinition(28, "NARRATIVE_CHAIN", "dynamic", "cognition.narrative_chain + PACING", is_static=False),
    29: SlotDefinition(29, "REAL_TIME_DATA", "dynamic", "world_context (Doom, HP, Time)", is_static=False),
    30: SlotDefinition(30, "GM_MOVER", "dynamic", "cognition.GMMover + COGNITIVE_INTEGRATION", is_static=False),
    31: SlotDefinition(31, "LAST_RESPONSE", "dynamic", "직전 AI 응답 (turn -1)", is_static=False),
    32: SlotDefinition(32, "USER_INPUT", "dynamic", "현재 유저 입력", is_static=False),
    33: SlotDefinition(33, "AUTHOR_NOTE", "dynamic", "AUTHOR_NOTE + GENRE_DIRECTIVE", is_static=False),
    34: SlotDefinition(34, "TELESCOPE_LANGUAGE", "kernel", "TELESCOPE + LANGUAGE + EMOTION"),
}


class SlotPromptBuilder:
    """
    34단계 슬롯 기반 프롬프트 빌더.

    기존 prompt_builder.py의 유틸리티 함수들을 재사용하며,
    각 슬롯에 데이터를 주입하고 정해진 순서대로 프롬프트를 조립합니다.
    """

    def __init__(self):
        """슬롯 저장소 초기화"""
        # 34개 슬롯을 None으로 초기화
        self.slots: Dict[int, Optional[str]] = {i: None for i in range(1, 35)}
        self._static_built = False

        # 장르/톤 설정 (레거시 호환)
        self.active_genres: Optional[List[str]] = None
        self.custom_tone: Optional[str] = None
        self.scene_type: str = "normal"

    def set_slot(self, index: int, content: str) -> 'SlotPromptBuilder':
        """특정 슬롯에 콘텐츠 주입"""
        if index not in self.slots:
            logger.warning(f"Invalid slot index: {index}")
            return self
        self.slots[index] = content
        return self

    def get_slot(self, index: int) -> Optional[str]:
        """특정 슬롯의 콘텐츠 반환"""
        return self.slots.get(index)

    # =========================================================
    # Legacy Compatibility: Genre/Tone/Scene Settings
    # =========================================================

    def set_genres(self, genres: Optional[List[str]]) -> 'SlotPromptBuilder':
        """레거시 호환: 활성 장르 설정"""
        self.active_genres = genres
        return self

    def set_tone(self, tone: Optional[str]) -> 'SlotPromptBuilder':
        """레거시 호환: 커스텀 톤 설정"""
        self.custom_tone = tone
        return self

    def set_scene(self, scene_type: str) -> 'SlotPromptBuilder':
        """레거시 호환: 씬 타입 설정"""
        self.scene_type = scene_type
        return self

    # =========================================================
    # Static Slot Population (Primacy/Recency Optimized)
    # =========================================================

    def populate_static_slots(self) -> 'SlotPromptBuilder':
        """
        정적 슬롯들을 text_resources에서 로드합니다.
        Primacy/Recency 최적화 순서 적용.
        """
        if self._static_built:
            return self

        # ===== PRIMACY ZONE (1-4) =====
        # [1-2] Identity
        self.set_slot(1, text_resources.CONTENT_AUTHORIZATION_MANDATE)
        self.set_slot(2, text_resources.AI_CORE_IDENTITY)

        # [3] Mirror Workshop (거울공방 - 핵심 서사 철학 → Primacy!)
        mirror_workshop = getattr(text_resources, 'MIRROR_WORKSHOP_PROTOCOL', '')
        self.set_slot(3, mirror_workshop)

        # [4] Physical Rendering
        physical = getattr(text_resources, 'PHYSICAL_RENDERING_DOCTRINE', '') or getattr(text_resources, 'PHYSICAL_RENDER', '')
        self.set_slot(4, physical)

        # ===== WORLD ZONE (5) =====
        self.set_slot(5, f"{text_resources.WORLD_AXIOM}\n\n{text_resources.MEMORY_HIERARCHY}")

        # ===== CONTEXT ZONE (10, 12) =====
        # [10] Temporal
        temporal = getattr(text_resources, 'TEMPORAL_FLOW_DOCTRINE', '') or getattr(text_resources, 'TEMPORAL_FLOW', '')
        time_atm = getattr(text_resources, 'TIME_ATMOSPHERE', '')
        self.set_slot(10, f"{temporal}\n\n{time_atm}")

        # [12] Social
        interaction = getattr(text_resources, 'INTERACTION_MODEL', '')
        npc_behavior = getattr(text_resources, 'NPC_BEHAVIOR_SYSTEM', '')
        self.set_slot(12, f"{interaction}\n\n{npc_behavior}")

        # ===== COGNITION ZONE (15) =====
        # [15] Psyche Rendering Instruction
        psyche_render = getattr(text_resources, 'PSYCHE_STATE_RENDERING', '')
        self.set_slot(15, psyche_render)

        # ===== RULES ZONE (18-25) - Static Recency 강화 =====
        # [18] PC Autonomy
        self.set_slot(18, text_resources.PC_AUTONOMY_DOCTRINE)

        # [20] Status Layout — 동적 빌더(_build_status_layout)가 덮어씀
        self.set_slot(20, "")

        # [21] Action Resolution + Situation Priority
        action_res = getattr(text_resources, 'ACTION_RESOLUTION', '')
        aspect_util = getattr(text_resources, 'ASPECT_UTILIZATION', '')
        situation_priority = getattr(text_resources, 'SITUATION_PRIORITY_PROTOCOL', '')
        self.set_slot(21, f"{action_res}\n\n{aspect_util}\n\n{situation_priority}")

        # [25] Style (Static Recency! 캐시 구간 마지막)
        anti_cliche = getattr(text_resources, 'ANTI_CLICHE_PROTOCOL', '')
        prose_craft = getattr(text_resources, 'PROSE_CRAFT_PROTOCOL', '')
        self.set_slot(25, f"{anti_cliche}\n\n{prose_craft}")

        # ===== CACHE BOUNDARY =====
        self.set_slot(26, "\n==========CACHE BOUNDARY==========\n")

        # ===== DYNAMIC ZONE (34) - Telescope 규칙만 정적, 프리필은 동적 =====
        # [34] Telescope rules (정적) + Language — 프리필은 populate_dynamic_slots()에서 추가
        telescope_rules = getattr(text_resources, 'TELESCOPE_PROTOCOL', '')
        language = getattr(text_resources, 'LANGUAGE_CORRECTION', '')
        slot34_parts = [p for p in [telescope_rules, language] if p.strip()]
        self.set_slot(34, "\n\n".join(slot34_parts))

        self._static_built = True
        logger.info("[SlotPromptBuilder] Static slots populated (Primacy/Recency optimized).")
        return self

    # =========================================================
    # Dynamic Slot Population (Uses Legacy Functions)
    # =========================================================

    def populate_dynamic_slots(
        self,
        player_data: str = "",
        npc_roles: str = "",
        lore: str = "",
        fermented_history: str = "",
        input_analysis: str = "",
        psyche_states: str = "",
        scene_intelligence: str = "",
        extended_intelligence: str = "",
        chapter_context: str = "",
        content_level: str = "normal",
        older_history: str = "",
        last_response: str = "",
        narrative_chain: str = "",
        real_time_data: str = "",
        gm_mover: str = "",
        user_input: str = "",
        author_note: str = "",
        telescope_prefill: str = ""
    ) -> 'SlotPromptBuilder':
        """동적 슬롯들을 주입합니다. 레거시 함수들을 재사용."""

        # ===== WORLD ZONE (6-9) =====
        # [6] PC Data (솔로: Player_Character, 다인: Player_Characters)
        # BABEL Discovery Protocol: PC 정보는 작가 참조용. NPC는 관찰로만 발견.
        if player_data:
            _discovery = (
                "[DISCOVERY PROTOCOL] This is AUTHOR REFERENCE — not character knowledge.\n"
                "NPCs discover PC traits ONLY through: direct observation, shared dialogue, behavioral inference.\n"
                "Do NOT 'download' profile data into NPC perception. Unobserved traits remain invisible.\n\n"
            )
            if "\n---\n" in player_data:
                self.set_slot(6, f"<Player_Characters>\n{_discovery}{player_data}\n</Player_Characters>")
            else:
                self.set_slot(6, f"<Player_Character>\n{_discovery}{player_data}\n</Player_Character>")

        # [7] NPC Roles
        # BABEL Pidgin→Creole: 라벨을 산문에 그대로 옮기지 말고 필드별 감식 변환
        if npc_roles:
            _pidgin = (
                "[PIDGIN→CREOLE] Profiles below = author reference, NOT prose vocabulary.\n"
                "If a profile word appears as an adjective in your output, you have failed. Transform:\n"
                "- personality label → physical consequence (behavior, not adjective)\n"
                "- appearance → arrives piecemeal through different moments/gazes, not listed\n"
                "- background → residue in present behavior only (hesitation, reflex, avoidance)\n"
                "- speech/tone → dialogue PERFORMS the pattern. Describing it = narrating the label.\n\n"
            )
            self.set_slot(7, f"<NPC_Roles>\n{_pidgin}{npc_roles}\n</NPC_Roles>")

        # [8] Lore
        if lore:
            self.set_slot(8, f"<Lore>\n{lore}\n</Lore>")

        # [9] Fermented History
        if fermented_history:
            self.set_slot(9, f"<Fermented_Memory>\n{fermented_history}\n</Fermented_Memory>")

        # ===== CONTEXT ZONE (11) =====
        # [11] Chapter Context
        if chapter_context:
            self.set_slot(11, f"<Chapter_Context>\n{chapter_context}\n</Chapter_Context>")

        # ===== COGNITION ZONE (13-14, 16) =====
        # [13] Input Analysis (Enhanced with Observation + Intent + Position/Effect)
        if input_analysis:
            self.set_slot(13, f"<Input_Analysis source='theoria_flash'>\n[ANALYSIS] The following is Theoria's inference — may contain errors.\n{input_analysis}\n</Input_Analysis>")

        # [14] Psyche States
        if psyche_states:
            self.set_slot(14, f"<Psyche_States source='theoria_flash'>\n[ANALYSIS] NPC psychology inferred by Flash model. Cross-reference with NPC profiles.\n{psyche_states}\n</Psyche_States>")

        # [16] Scene Intelligence (Aspects + SensoryAnchors + Habitus + Hook)
        if scene_intelligence:
            self.set_slot(16, f"<Scene_Intelligence>\n{scene_intelligence}\n</Scene_Intelligence>")

        # [17] Extended Intelligence (NPC Knowledge + Intimacy Analysis)
        if extended_intelligence:
            self.set_slot(17, f"<Extended_Intelligence>\n{extended_intelligence}\n</Extended_Intelligence>")

        # ===== RULES ZONE (22-24) =====
        # [22-24] Content Level
        self._populate_content_slots_legacy(content_level)

        # ===== DYNAMIC ZONE (27-34) =====
        # [27] Older History (2~11턴 전 대화 - 참고 맥락)
        if older_history:
            self.set_slot(27, f"<Previous_History>\n{older_history}\n</Previous_History>")

        # [28] Narrative Chain
        if narrative_chain:
            pacing = getattr(text_resources, 'PACING_CONTROL_PROTOCOL', '')
            self.set_slot(28, f"<Narrative_Chain>\n{narrative_chain}\n</Narrative_Chain>\n\n{pacing}")

        # [29] Real-time Data
        if real_time_data:
            self.set_slot(29, f"<Real_Time_Status>\n[GROUND_TRUTH] Current world state from game mechanics.\n{real_time_data}\n</Real_Time_Status>")

        # [30] World Response (GM Mover)
        if gm_mover:
            cognitive_int = getattr(text_resources, 'COGNITIVE_DATA_INTEGRATION', '')
            self.set_slot(30, f"<World_Response>\n{gm_mover}\n</World_Response>\n\n{cognitive_int}")

        # [31] Last Response (직전 AI 응답 - 유저 입력 바로 앞!)
        if last_response:
            self.set_slot(31, f"<Last_Response>\n{last_response}\n</Last_Response>")

        # [32] User Input (현재 유저 입력 - 직전 응답 바로 뒤!)
        if user_input:
            self.set_slot(32, f"<User_Input>\n{user_input}\n</User_Input>")

        # [33] Author Note + Genre Directive
        if author_note:
            self.set_slot(33, f"<Author_Note>\n{author_note}\n</Author_Note>")
        elif self.active_genres or self.custom_tone:
            directive = legacy_builder.build_combined_directive(self.active_genres, self.custom_tone)
            self.set_slot(33, directive)

        # [34] Telescope prefill 동적 추가 (정적 규칙은 _build_static에서 이미 설정)
        if telescope_prefill:
            existing = self.slots.get(34, "")
            self.set_slot(34, existing + "\n\n" + telescope_prefill if existing else telescope_prefill)

        return self

    def _populate_content_slots_legacy(self, content_level: str) -> None:
        """
        콘텐츠 수위 슬롯 설정 - 레거시 build_mature_content_prompt 재사용
        """
        if content_level and content_level != 'normal':
            mature_prompt = legacy_builder.build_mature_content_prompt(content_level)
            if mature_prompt:
                self.set_slot(22, mature_prompt)

    # =========================================================
    # Build Final Prompt
    # =========================================================

    def build(self) -> str:
        """모든 슬롯을 조립하여 최종 프롬프트 문자열 반환."""
        parts = []

        for i in range(1, 35):
            content = self.slots.get(i)
            if content:
                slot_def = SLOT_DEFINITIONS.get(i)
                slot_name = slot_def.name if slot_def else f"SLOT_{i}"

                parts.append(content)

        return "\n\n".join(parts)

    def build_static_only(self) -> str:
        """정적 슬롯(1-25)만 빌드 (캐시용)"""
        parts = []
        for i in range(1, 26):
            content = self.slots.get(i)
            if content:
                parts.append(content)
        return "\n\n".join(parts)

    def build_dynamic_only(self) -> str:
        """동적 슬롯(26-34)만 빌드"""
        parts = []
        for i in range(26, 35):
            content = self.slots.get(i)
            if content:
                parts.append(content)
        return "\n\n".join(parts)


# =========================================================
# Helper: Quest Directive
# =========================================================

def _prepend_quest_directive(obj_ctx: str) -> str:
    """퀘스트 컨텍스트 앞에 체호프의 총 방지 원칙을 추가."""
    if not obj_ctx:
        return obj_ctx
    quest_directive = (
        "[QUEST ≠ CHEKHOV'S GUN]\n"
        "퀘스트는 서사적 약속이 아니라 세계에 존재하는 가능성이다.\n"
        "- 유저의 DO(현재 행동)만 서사에 반영. WANT(퀘스트)를 DO로 끌어올리지 마라\n"
        "- 유저가 퀘스트와 무관한 행동을 하면 퀘스트를 언급하지 마라\n"
        "- 미해결 퀘스트는 해소 압박 없이 세계에 존재한다\n"
        "- 퀘스트 환경 묘사는 유저 행동이 자연스럽게 겹칠 때만\n"
    )
    return quest_directive + "\n" + obj_ctx


# =========================================================
# Factory Function for Easy Integration
# =========================================================

def build_34_step_prompt(ctx) -> str:
    """
    ResponseContext를 받아 34단계 슬롯 기반 프롬프트를 생성합니다.
    orchestration_response.py에서 호출됩니다.

    [Phase 2 강화]
    - RAG 컨텍스트 다이어트 적용
    - memory_triggers 통합
    - 풍부한 Cognition 데이터 주입
    """
    import config  # Lazy import to avoid circular
    import domain_manager
    import npc_manager

    builder = SlotPromptBuilder()

    # 장르/톤/씬 설정 (레거시 호환)
    builder.set_genres(getattr(ctx, 'active_genres', None))
    builder.set_tone(getattr(ctx, 'custom_tone', None))
    builder.set_scene(getattr(ctx, 'scene_type', 'normal'))

    # 1. 정적 슬롯 로드
    builder.populate_static_slots()

    # =========================================================
    # 1.5. Slot 20 동적 오버라이드: active_modules 기반 STATUS_WINDOW_LAYOUT
    # =========================================================
    channel_id = getattr(ctx, 'channel_id', '')
    user_id = getattr(ctx, 'user_id', '')
    if channel_id:
        _active_modules = domain_manager.get_active_modules(channel_id)
        builder.set_slot(20, _build_status_layout(_active_modules))

    # =========================================================
    # 2. 동적 슬롯 주입 (Phase 2 강화)
    # =========================================================

    dai = getattr(ctx, 'dai', None) or {}

    # --- [Slot 6] PC Data (Rich Player Info — 다인 플레이 지원) ---
    player_info = ""
    if channel_id and user_id:
        try:
            all_participants = domain_manager.get_domain(channel_id).get("participants", {})
            active_pcs = {uid: p for uid, p in all_participants.items() if p.get("status") == "active"}

            if len(active_pcs) > 1:
                # 다인 플레이: 모든 PC 표시
                pc_sections = []
                for uid, p in active_pcs.items():
                    info = domain_manager.get_unified_player_info(channel_id, uid)
                    marker = " ★행동자" if uid == user_id else ""
                    mask = p.get("mask", "Unknown")
                    pc_sections.append(f"### {mask}{marker}\n{info}")
                player_info = "\n---\n".join(pc_sections)
            else:
                # 솔로 플레이: 기존 방식
                rich_player_info = domain_manager.get_unified_player_info(channel_id, user_id)
                if rich_player_info:
                    player_info = rich_player_info
        except Exception as e:
            logger.warning(f"Failed to get rich player info: {e}")

    if not player_info:
        player_data = getattr(ctx, 'player_data', None)
        if player_data:
            player_info = f"Name: {player_data.get('mask', 'Unknown')}\n"

    # --- [Slot 7] NPC Roles (Smart Loading: relevant NPCs get full profiles) ---
    domain_data = getattr(ctx, 'domain_data', {}) or {}
    relevant_npcs = dai.get("relevant_npcs", [])

    if relevant_npcs and channel_id:
        import npc_manager as _npc_mgr
        npc_scene_type = dai.get("scene_type", "normal")
        full_profiles = _npc_mgr.get_npc_full_profiles(channel_id, relevant_npcs, scene_type=npc_scene_type)
        others = _npc_mgr.get_npc_names_only(channel_id, exclude=relevant_npcs)
        npc_roles = full_profiles
        if others:
            npc_roles += f"\n\n{others}"
        logger.info(f"[NPC Smart Load] Full: {relevant_npcs}, Others: name-only")
    else:
        npc_roles = str(domain_data.get("npcs", "")) if domain_data.get("npcs") else ""

    # --- [Slot 8] Lore (V5 Chunk-based RAG + Context Diet) ---
    relevant_chunks_idx = dai.get("relevant_chunks", [])
    lore_chunks = domain_data.get("lore_chunks", [])
    relevant_context = dai.get("relevant_context", [])

    # Priority 1: Chunk-based injection (V5)
    if relevant_chunks_idx and lore_chunks:
        chunk_parts = []
        for idx in relevant_chunks_idx:
            if isinstance(idx, int) and 0 <= idx < len(lore_chunks):
                chunk = lore_chunks[idx]
                chunk_parts.append(f"[{chunk.get('label', f'Chunk {idx}')}]\n{chunk.get('content', '')}")
        if chunk_parts:
            logger.info(f"[Chunk RAG] Injecting {len(chunk_parts)} selected chunks.")
            lore_content = (
                "### [LORE: SELECTED CHUNKS — GROUND_TRUTH]\n"
                "Source: User lorebook. Treat as established world fact.\n\n"
                + "\n\n".join(chunk_parts)
            )
        else:
            lore_content = getattr(ctx, 'lore_txt', '')
    # Priority 2: Free-form relevant_context (legacy RAG)
    elif isinstance(relevant_context, list) and relevant_context:
        logger.info(f"[Context Diet] Using {len(relevant_context)} extracted items.")
        lore_content = (
            "### [RAG: FILTERED CONTEXT]\n"
            "The following rules/lore are extracted as MOST RELEVANT for this turn:\n"
            + "\n".join([f"- {item}" for item in relevant_context])
            + "\n\n(Use this context faithfully. If information is missing, rely on General Logic.)"
        )
    # Priority 3: Full lore text fallback
    else:
        lore_content = getattr(ctx, 'lore_txt', '')

    # --- [Slot 9] Fermented History + Memory Triggers ---
    fermented_base = getattr(ctx, 'fermented_summary_text', '')
    memory_triggers = dai.get("memory_triggers", [])
    deep_data = getattr(ctx, 'deep_memory_data', {}) or {}
    active_triggers = deep_data.get("active_memory_triggers", [])

    fermented_history = fermented_base
    if memory_triggers or active_triggers:
        all_triggers = list(set(active_triggers + [
            m.get("trigger", "") for m in memory_triggers if isinstance(m, dict)
        ]))
        if all_triggers:
            triggers_str = "\n".join(f"- {t}" for t in all_triggers if t)
            fermented_history = f"### [ACTIVE MEMORY TRIGGERS - Unresolved Narrative Hooks]\n{triggers_str}\n\n{fermented_base}"

    # --- [Slot 13] Input Analysis (Enhanced with Observation + Intent + Position/Effect) ---
    input_analysis_parts = []
    input_analysis_data = dai.get("input_analysis", {})
    if input_analysis_data:
        input_analysis_parts.append(
            f"Original: {input_analysis_data.get('Original', 'N/A')}\n"
            f"Enhanced: {input_analysis_data.get('Enhanced', 'N/A')}\n"
            f"Plausibility: {input_analysis_data.get('Plausibility', 'N/A')}\n"
            f"Momentum: {input_analysis_data.get('Momentum', 'OPEN')}"
        )

    # Observation: 실제로 일어난 일
    observation = dai.get("observation", "")
    if observation:
        input_analysis_parts.append(f"Observation: {observation}")

    # UserIntent: 유저가 원하는 것
    user_intent = dai.get("user_intent", "")
    if user_intent:
        input_analysis_parts.append(f"UserIntent: {user_intent}")

    # Position/Effect: iceberg 번역 (수치 → 서수 tier + friction)
    position = dai.get("position", {})
    effect = dai.get("effect", {})
    pos_eff_text = iceberg.translate_position_effect(position, effect)
    if pos_eff_text:
        input_analysis_parts.append(pos_eff_text)

    input_analysis = "\n".join(input_analysis_parts)

    # --- [Slot 16] Scene Intelligence (Aspects + Energy + SensoryAnchors + Habitus + Hook + Flags) ---
    scene_intel_parts = []

    # EnergyDirection: iceberg 번역 (라벨 → 산문 호흡 힌트)
    energy_dir = dai.get("energy_direction", "")
    energy_hint = iceberg.translate_energy_direction(energy_dir)
    if energy_hint:
        scene_intel_parts.append(energy_hint)

    # Aspects: 활용 가능한 장면 요소
    aspects = dai.get("aspects", [])
    if aspects and isinstance(aspects, list):
        scene_intel_parts.append("### Scene Aspects\n" + "\n".join(f"- {a}" for a in aspects))

    # SensoryAnchors: 감각 앵커 + 기억 연결
    sensory = dai.get("SensoryAnchors", dai.get("sensory_anchors", []))
    if sensory and isinstance(sensory, list):
        anchors_str = "\n".join(
            f"- {s.get('anchor', '?')} → {s.get('memory_link', '')}"
            for s in sensory if isinstance(s, dict)
        )
        if anchors_str:
            scene_intel_parts.append(f"### Sensory Anchors\n{anchors_str}")

    # HabitusAnalysis: 경제/문화/사회적 분석
    habitus = dai.get("HabitusAnalysis", dai.get("habitus_analysis", {}))
    if habitus and isinstance(habitus, dict):
        hab_lines = [f"- {k}: {v}" for k, v in habitus.items() if v]
        if hab_lines:
            scene_intel_parts.append("### Habitus\n" + "\n".join(hab_lines))

    # narrative_hook: 트위스트 제안
    hook = dai.get("narrative_hook", "")
    if hook:
        scene_intel_parts.append(f"### Narrative Hook [INFERRED]\n{hook}")

    # QualityFlags: iceberg 번역 (경고 라벨 → 행동 지시)
    qflags = dai.get("quality_flags", {})
    qflag_text = iceberg.translate_quality_flags(qflags)
    if qflag_text:
        scene_intel_parts.append("### 서사 품질 보정\n" + qflag_text)

    # Scene Continuity: 불연속 감지 → 보정 지시
    continuity_data = dai.get("continuity_check", {})
    continuity_text = iceberg.translate_continuity_check(continuity_data)
    if continuity_text:
        scene_intel_parts.append(continuity_text)

    # Apophenia Guard: iceberg 번역 (OBVIOUS= → 한국어)
    trait_conn = dai.get("trait_connections", {})
    trait_text = iceberg.translate_trait_connections(trait_conn)
    if trait_text:
        scene_intel_parts.append(trait_text)

    # Foreshadowing Guard: ai_memory에 추적된 복선을 ambient fact으로 주입
    if channel_id:
        _fs_mem = domain_manager.get_session_ai_memory(channel_id)
        _foreshadowing = _fs_mem.get("foreshadowing", [])
        if isinstance(_foreshadowing, list) and _foreshadowing:
            fs_items = "\n".join(f"- {f}" for f in _foreshadowing[:5] if f)
            if fs_items:
                scene_intel_parts.append(
                    "### Foreshadowing [AMBIENT — DO NOT RESOLVE]\n"
                    "These seeds exist in the world. They are NOT dramatic promises awaiting payoff.\n"
                    "Render only as background texture (environmental detail, NPC micro-behavior) — "
                    "NEVER as climactic reveal or resolution, unless user action directly engages them.\n"
                    + fs_items
                )

    scene_intelligence = "\n\n".join(scene_intel_parts)

    # --- [Slot 17] Extended Intelligence (NPC Knowledge + Intimacy Analysis) ---
    extended_intel_parts = []

    # NPCAttitudes: iceberg 번역 (태도 라벨 제거, trajectory → 행동 힌트)
    npc_attitudes = dai.get("NPCAttitudes", dai.get("npc_attitudes", {}))
    att_text = iceberg.translate_npc_attitudes(npc_attitudes)
    if att_text:
        extended_intel_parts.append("### NPC 태도 방향\n" + att_text)

    # NPCKnowledge: iceberg 번역 (leak_risk/would_share 제거, 내용 유지)
    npc_knowledge = dai.get("NPCKnowledge", dai.get("npc_knowledge", {}))
    know_text = iceberg.translate_npc_knowledge(npc_knowledge)
    if know_text:
        extended_intel_parts.append(know_text)

    # IntimacyAnalysis: iceberg 번역 (window_check + dual_control 버그 수정 포함)
    intimacy = dai.get("IntimacyAnalysis", dai.get("intimacy_analysis"))
    intim_text = iceberg.translate_intimacy(intimacy)
    if intim_text:
        extended_intel_parts.append(
            "### 친밀 장면 신체 상태\n"
            "(신체 감각과 행동으로만 렌더링하라. 필드명이나 분석 용어를 산문에 쓰지 마.)\n"
            + intim_text
        )

    # NPC Connection Milestones (1회성 서사 힌트)
    if channel_id:
        milestone_hints = npc_manager.get_connection_milestone_hints(channel_id)
        if milestone_hints:
            extended_intel_parts.append("### NPC Connection Milestones\n" + "\n".join(milestone_hints))

        # NPC Connection Depth: iceberg 번역 (수치/스테이지명 제거)
        all_attitudes = domain_manager.get_npc_attitudes(channel_id)
        conn_lines = []
        for _cn, _ca in all_attitudes.items():
            _depth = _ca.get("depth", 0)
            _tension = _ca.get("tension", 0)
            if _depth > 0 or _tension > 20:
                _stage = config.get_connection_stage(_depth)
                _line = iceberg.translate_connection_depth(
                    _cn, _stage["name"], _depth, _tension, _stage.get("hint_en", "")
                )
                conn_lines.append(_line)
        if conn_lines:
            extended_intel_parts.append(
                "### NPC 관계 깊이\n" + "\n".join(conn_lines)
            )

    extended_intelligence = "\n\n".join(extended_intel_parts)

    # --- NPC별 수면 계산 (per-NPC depth knobs) ---
    npc_depths = None
    psyche_data = dai.get("psyche_states", {})
    scene_type = dai.get("scene_type", "normal")
    energy_dir_raw = dai.get("energy_direction", "idle")

    if psyche_data and channel_id:
        _turn_count = 0
        _conn_depths = {}
        try:
            _ws = domain_manager.get_world_state(channel_id)
            _turn_count = _ws.get("turn_index", 0)
        except Exception:
            pass
        try:
            _all_att = domain_manager.get_npc_attitudes(channel_id)
            _conn_depths = {n: a.get("depth", 0) for n, a in _all_att.items()}
        except Exception:
            pass

        _auto_triggers = dai.get("autonomous_triggers", [])
        _npc_attitudes_raw = dai.get("NPCAttitudes", dai.get("npc_attitudes", {}))

        npc_depths = iceberg.compute_npc_depths(
            npc_names=list(psyche_data.keys()),
            scene_type=scene_type,
            energy=energy_dir_raw,
            turn_count=_turn_count,
            autonomous_triggers=_auto_triggers,
            connection_depths=_conn_depths,
            npc_attitudes=_npc_attitudes_raw,
        )

    # [Slot 17 보충] 대사 방향 지시 (gaze 기반 심도)
    _prev_gaze = ""
    if channel_id:
        _latest = domain_manager.get_latest_frame(channel_id)
        _prev_gaze = _latest.get("render_fingerprint", {}).get("gaze", "")

    _dialogue_dir = iceberg.compose_dialogue_directives(
        psyche_data, npc_knowledge,
        prev_gaze=_prev_gaze, npc_depths=npc_depths,
    )
    if _dialogue_dir:
        if extended_intelligence:
            extended_intelligence += "\n\n" + _dialogue_dir
        else:
            extended_intelligence = _dialogue_dir

    # --- [Slot 14] Psyche States (iceberg 번역) ---
    psyche_states = iceberg.translate_psyche_states(
        psyche_data, scene_type, energy_dir_raw,
        npc_depths=npc_depths,
    )

    # --- [Slot 28] Narrative Chain (iceberg 번역) ---
    narrative_chain = ""
    chain_data = dai.get("narrative_chain", {})
    if chain_data and isinstance(chain_data, dict):
        narrative_chain = iceberg.translate_narrative_chain(chain_data)
        # Anti-Resolution: open threads guard (가드 텍스트 유지, 카테고리 라벨만 제거)
        open_threads = chain_data.get("open_threads", [])
        if isinstance(open_threads, list) and open_threads:
            thread_list = iceberg.translate_open_threads(open_threads[:5])
            if thread_list:
                narrative_chain += (
                    f"\n\n[OPEN THREADS — AMBIENT ONLY]\n"
                    f"These threads are active world forces. Maintain their PRESENCE, not their RESOLUTION.\n"
                    f"Only user action directly engaging a thread may advance or close it.\n"
                    f"{thread_list}"
                )

    # --- [Slot 30] GM Mover (iceberg: type 라벨 제거) ---
    gm_mover = ""
    gm_move = dai.get("gm_move", {})
    gm_move_text = iceberg.translate_gm_move(gm_move)
    if gm_move_text:
        gm_mover = gm_move_text

    # Flashback Scene Instruction (회상 확정 시)
    if dai.get("flashback_confirmed"):
        fb_decl = dai.get("flashback_declaration", "")
        fb_instruction = (
            f"\n[FLASHBACK] The player has activated a flashback: \"{fb_decl}\"\n"
            "Write a brief 2-3 sentence flashback scene, then return to the present.\n"
            "This changes the SITUATION/POSITION only. Do NOT change any stats (HP, 기력, doom).\n"
            "Do NOT give the PC free items that would bypass resource management."
        )
        gm_mover = (gm_mover + fb_instruction) if gm_mover else fb_instruction

    # [POSITION_FRICTION 제거됨] — Slot 13 translate_position_effect()에서 tier별 friction 자동 append

    # Time directive (Pro에 서사 시간 범위 힌트)
    time_flow_data = dai.get("time_flow", {})
    if time_flow_data:
        import game_system as _gs
        scene = dai.get("scene_type", "normal")
        tf_ticks = time_flow_data.get("ticks", 1)
        rules = config.SCENE_TIME_RULES.get(scene, config.SCENE_TIME_RULES["normal"])
        explicit = time_flow_data.get("explicit", False) or time_flow_data.get("duration") == "explicit"
        if not explicit and tf_ticks > rules["max_ticks"]:
            tf_ticks = rules["max_ticks"]
        if not explicit and tf_ticks <= 0 and rules["base_ticks"] > 0:
            tf_ticks = rules["base_ticks"]
        time_dir = _gs.build_time_directive(tf_ticks, scene)
        gm_mover = (gm_mover + f"\n\n{time_dir}") if gm_mover else time_dir

    # UNE Narrative Layers (Events / Atmosphere / Judgment / World) → Slot 30
    une_directive = getattr(ctx, 'judgment_context', '')
    if une_directive:
        gm_mover = (gm_mover + f"\n\n{une_directive}") if gm_mover else une_directive

    # --- [Slot 29] Real-time Data (compact v3 status first, legacy fallback) ---
    real_time_data = ""
    if channel_id:
        try:
            import game_world as _game_world
            real_time_data = _game_world.build_real_time_display(channel_id, user_id=user_id)
        except Exception as e:
            logger.debug(f"[RealTimeDisplay] Fallback to legacy world_ctx: {e}")
    if not real_time_data:
        real_time_data = getattr(ctx, 'world_ctx', '')

    # PC Impersonation Check 강화
    pc_check = dai.get("pc_impersonation_check", {})
    if pc_check.get("detected"):
        pc_warning = (
            f"\n\n⚠️ PC_IMPERSONATION_WARNING:\n"
            f"- detected: true\n"
            f"- violations: {pc_check.get('violations', [])[:3]}\n"
            f"- correction_hint: {pc_check.get('correction_hint', '')}"
        )
        real_time_data += pc_warning

    # Emotion Intensity: iceberg 번역 (밴드명/수치 제거 → 행동 강도 힌트)
    psyche_states_raw = dai.get("psyche_states", {})
    intensity_text = iceberg.translate_emotion_intensity(psyche_states_raw)
    if intensity_text:
        real_time_data += f"\n\n{intensity_text}"

    # Vigor ↔ Composure CONTRAST: iceberg 번역 (수치·해석 제거, 괴리 사실만)
    if channel_id:
        try:
            _target_p = domain_manager.get_domain(channel_id).get("participants", {}).get(user_id, {})
            _mem = _target_p.get("ai_memory", {}) if isinstance(_target_p, dict) else {}
            _v_dict = _mem.get("vigor") or _mem.get("mental") or {}
            _c_dict = _mem.get("composure") or {}
            _v = int(_v_dict.get("value", 100)) if _v_dict.get("value") is not None else 100
            _c = int(_c_dict.get("value", 100)) if _c_dict.get("value") is not None else 100
            contrast_text = iceberg.translate_vigor_composure(_v, _c)
            if contrast_text:
                real_time_data += f"\n\n{contrast_text}"
        except Exception:
            pass

    # =========================================================
    # 3. 히스토리 분리 (직전 응답 vs 이전 대화)
    # =========================================================
    # SillyTavern 패턴: 직전 AI 응답을 유저 입력 바로 앞에 배치
    # → AI가 "방금 이 말 했으니 → 유저가 이렇게 반응 → 이어서 써라" 흐름 유지

    older_history = ""
    last_response = ""
    smart_history = getattr(ctx, 'smart_history', [])

    if smart_history and isinstance(smart_history, list):
        # 직전 AI 응답 찾기 (마지막 assistant/model 메시지)
        last_ai_idx = -1
        for i in range(len(smart_history) - 1, -1, -1):
            role = smart_history[i].get('role', '').lower()
            if role in ('assistant', 'model'):
                last_ai_idx = i
                break

        if last_ai_idx >= 0:
            # 직전 AI 응답
            last_response = smart_history[last_ai_idx].get('content', '')

            # 이전 대화 (2~11턴 전, 최대 20개 메시지)
            # 직전 AI 응답 이전의 대화만 포함
            older_start = max(0, last_ai_idx - 20)
            older_msgs = smart_history[older_start:last_ai_idx]
            if older_msgs:
                older_history = "\n".join(
                    f"{h.get('role', '?')}: {h.get('content', '')}" for h in older_msgs
                )
        else:
            # AI 응답이 없으면 전체를 older_history로
            older_history = "\n".join(
                f"{h.get('role', '?')}: {h.get('content', '')}" for h in smart_history[-20:]
            )
    else:
        # smart_history가 없으면 기존 hist_text 폴백
        older_history = getattr(ctx, 'hist_text', '')

    # =========================================================
    # 3.5. POV 모드 전환 (사칭 토글 연동)
    # =========================================================
    # impersonation_filter=True  → Camera Eye (행동주의, 모든 내면 블랙박스)
    # impersonation_filter=False → Omniscient Author (전지적 작가, NPC 내면 허용)
    impersonation_enabled = domain_data.get("settings", {}).get("impersonation_filter", True)

    if not impersonation_enabled:
        # 전지적 모드: Camera Eye 제한을 NPC에 대해 완화
        omniscient_override = getattr(text_resources, 'OMNISCIENT_MODE_OVERRIDE', '')
        if omniscient_override:
            # 슬롯 34 (TELESCOPE + OUTPUT + KERNEL)에 오버라이드 추가 → 최종 Recency 강화
            current_slot34 = builder.get_slot(34) or ''
            builder.set_slot(34, f"{omniscient_override}\n\n{current_slot34}")
            logger.info("[POV Mode] Omniscient Author mode active — NPC inner states accessible")
    else:
        logger.debug("[POV Mode] Camera Eye mode active — all inner states sealed")

    # =========================================================
    # 3.6. 히스토리 사칭 정화
    # =========================================================
    # AI 이전 응답에 PC 사칭이 포함되면 다음 응답도 패턴을 답습함
    # → 프롬프트에 주입하기 전에 히스토리에서 사칭 문장을 선제 제거
    pc_name = getattr(ctx, 'user_mask', '') or ''
    if impersonation_enabled and pc_name and pc_name != 'Unknown':
        from response_processor import filter_pc_impersonation
        pc_names_list = [pc_name]
        if last_response:
            cleaned, violations = filter_pc_impersonation(last_response, pc_names_list)
            if violations:
                last_response = cleaned
                logger.info(f"[History Sanitize] last_response: {len(violations)} impersonation(s) removed")
        if older_history:
            cleaned, violations = filter_pc_impersonation(older_history, pc_names_list)
            if violations:
                older_history = cleaned
                logger.info(f"[History Sanitize] older_history: {len(violations)} impersonation(s) removed")

    # =========================================================
    # 4. 동적 슬롯 주입 실행
    # =========================================================

    # 5W1H Telescope 프리필 조립 (코드 레벨 GROUND_TRUTH)
    # V2: Slot 34 대신 ctx에 저장 → 모델 응답 프리필로 직접 주입 (스킵 불가)
    telescope_prefill = _build_telescope_prefill(dai, real_time_data)
    if telescope_prefill:
        ctx.telescope_prefill_text = telescope_prefill
        logger.info("[Telescope] Prefill stored on ctx for model response injection")

    builder.populate_dynamic_slots(
        player_data=player_info,
        npc_roles=npc_roles,
        lore=lore_content,
        fermented_history=fermented_history,
        input_analysis=input_analysis,
        psyche_states=psyche_states,
        scene_intelligence=scene_intelligence,
        extended_intelligence=extended_intelligence,
        chapter_context=_prepend_quest_directive(getattr(ctx, 'obj_ctx', '')),
        content_level=getattr(ctx, 'scene_type', 'normal'),
        older_history=older_history,
        last_response=last_response,
        narrative_chain=narrative_chain,
        real_time_data=real_time_data,
        gm_mover=gm_mover,
        user_input=getattr(ctx, 'action_text', ''),
        author_note="",  # 장르/톤이 설정되어 있으면 자동으로 레거시 함수 사용
        telescope_prefill=""  # V2: Slot 34에 넣지 않음 — ctx를 통해 모델 프리필로 전달
    )

    # 4.5. Format Feedback Injection (이전 턴 대사 포맷 위반 피드백)
    session_mem = domain_manager.get_session_ai_memory(channel_id) if channel_id else {}
    fmt_feedback = session_mem.get("format_feedback", "")
    if fmt_feedback:
        current_33 = builder.get_slot(33) or ""
        builder.set_slot(33, f"{current_33}\n\n{fmt_feedback}")
        logger.info("[FormatFeedback] Injected dialogue format correction into slot 33")

    # NPC Recency Echo — 프로필이 중간 슬롯에 묻히므로 핵심 제약 + 말투를 recency에 재주입
    if relevant_npcs and channel_id:
        import npc_manager as _npc_mgr_voice
        npc_reminder = _npc_mgr_voice.get_npc_recency_reminders(channel_id, relevant_npcs)
        if npc_reminder:
            current_33 = builder.get_slot(33) or ""
            builder.set_slot(33, f"{current_33}\n\n{npc_reminder}")
            logger.info(f"[RecencyEcho] NPC reminders injected into slot 33 ({len(relevant_npcs)} NPCs)")

    # [Scene Breathing 제거됨] — Slot 16 iceberg.translate_energy_direction()이 동일 정보를 커버

    # 5W1H Recency Echo — always present at maximum recency position
    fidelity_echo = "[5W1H: Draw events only from DAI data. Camera scans environment evenly. Prose intensity follows EnergyDirection.]"
    current_33 = builder.get_slot(33) or ""
    builder.set_slot(33, f"{current_33}\n\n{fidelity_echo}")

    return builder.build()
