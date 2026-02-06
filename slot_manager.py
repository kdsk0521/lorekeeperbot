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

# [레거시 재사용] 기존 모듈에서 유용한 함수 임포트
import prompt_builder as legacy_builder

logger = logging.getLogger("SlotManager")

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
    17: SlotDefinition(17, "RESERVE", "reasoning", "Reserve Slot"),

    # ===== RULES ZONE (18-25): Static Recency - 행동 규칙 강화 =====
    18: SlotDefinition(18, "PC_AUTONOMY", "rules", "text_resources.PC_AUTONOMY_DOCTRINE"),
    19: SlotDefinition(19, "OBSERVER_NEUTRALITY", "rules", "text_resources.OBSERVER_NEUTRALITY_DOCTRINE"),
    20: SlotDefinition(20, "STATUS_LAYOUT", "rules", "text_resources.STATUS_WINDOW_LAYOUT"),
    21: SlotDefinition(21, "ACTION_RESOLUTION", "mechanics", "text_resources.ACTION_RESOLUTION + Aspects + SITUATION_PRIORITY"),
    22: SlotDefinition(22, "VISCERAL_CONTENT", "content", "text_resources.VISCERAL (conditional)", is_static=False),
    23: SlotDefinition(23, "MATURE_CONTENT", "content", "text_resources.MATURE (conditional)", is_static=False),
    24: SlotDefinition(24, "HYBRID_CONTENT", "content", "text_resources.HYBRID (conditional)", is_static=False),
    25: SlotDefinition(25, "CRITICAL_STYLE", "rules", "text_resources.CRITICAL + ANTI_CLICHE + AUTHOR_PERSONA + PROSE_CRAFT"),

    # ========== CACHE BOUNDARY ==========
    26: SlotDefinition(26, "CACHE_BOUNDARY", "boundary", "==========CACHE BOUNDARY==========", is_static=False),

    # ===== DYNAMIC ZONE (27-34): 최강 Recency =====
    27: SlotDefinition(27, "OLDER_HISTORY", "dynamic", "smart_history (2~11턴 전)", is_static=False),
    28: SlotDefinition(28, "NARRATIVE_CHAIN", "dynamic", "cognition.narrative_chain + PACING", is_static=False),
    29: SlotDefinition(29, "REAL_TIME_DATA", "dynamic", "world_context (Doom, HP, Time)", is_static=False),
    30: SlotDefinition(30, "GM_MOVER", "dynamic", "cognition.GMMover + COGNITIVE_INTEGRATION", is_static=False),
    31: SlotDefinition(31, "LAST_RESPONSE", "dynamic", "직전 AI 응답 (turn -1)", is_static=False),
    32: SlotDefinition(32, "USER_INPUT", "dynamic", "현재 유저 입력", is_static=False),
    33: SlotDefinition(33, "SELF_CORRECTION_NOTE", "dynamic", "SELF_CORRECTION + AUTHOR_NOTE", is_static=False),
    34: SlotDefinition(34, "NARRATIVE_KERNEL", "kernel", "TELESCOPE + OUTPUT + LANGUAGE + KERNEL + EMOTION"),
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
        # [18-19] GM Conduct
        self.set_slot(18, text_resources.PC_AUTONOMY_DOCTRINE)
        self.set_slot(19, text_resources.OBSERVER_NEUTRALITY_DOCTRINE)

        # [20] Status Layout
        status_layout = getattr(text_resources, 'STATUS_WINDOW_LAYOUT', '')
        self.set_slot(20, status_layout)

        # [21] Action Resolution + Situation Priority
        action_res = getattr(text_resources, 'ACTION_RESOLUTION', '')
        aspect_util = getattr(text_resources, 'ASPECT_UTILIZATION', '')
        situation_priority = getattr(text_resources, 'SITUATION_PRIORITY_PROTOCOL', '')
        self.set_slot(21, f"{action_res}\n\n{aspect_util}\n\n{situation_priority}")

        # [25] Critical + Style (Static Recency! 캐시 구간 마지막)
        critical = getattr(text_resources, 'CRITICAL_PROTOCOL', '')
        anti_cliche = getattr(text_resources, 'ANTI_CLICHE_PROTOCOL', '')
        author_persona = getattr(text_resources, 'AUTHOR_PERSONA_PROTOCOL', '')
        prose_craft = getattr(text_resources, 'PROSE_CRAFT_PROTOCOL', '')
        self.set_slot(25, f"{critical}\n\n{anti_cliche}\n\n{author_persona}\n\n{prose_craft}")

        # ===== CACHE BOUNDARY =====
        self.set_slot(26, "\n==========CACHE BOUNDARY==========\n")

        # ===== DYNAMIC ZONE (34) - 정적 부분 =====
        # [34] Telescope + Output + Language + Kernel + Emotion (최종 Recency)
        telescope = getattr(text_resources, 'TELESCOPE_PROTOCOL', '')
        output_protocol = getattr(text_resources, 'OUTPUT_PROTOCOL', '')
        language = getattr(text_resources, 'LANGUAGE_CORRECTION', '')
        kernel = getattr(text_resources, 'NARRATIVE_KERNEL', '')
        emotion = getattr(text_resources, 'EMOTION_BOOSTER', '')
        self.set_slot(34, f"{telescope}\n\n{output_protocol}\n\n{language}\n\n{kernel}\n\n{emotion}")

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
        chapter_context: str = "",
        content_level: str = "normal",
        older_history: str = "",
        last_response: str = "",
        narrative_chain: str = "",
        real_time_data: str = "",
        gm_mover: str = "",
        user_input: str = "",
        author_note: str = ""
    ) -> 'SlotPromptBuilder':
        """동적 슬롯들을 주입합니다. 레거시 함수들을 재사용."""

        # ===== WORLD ZONE (6-9) =====
        # [6] PC Data
        if player_data:
            self.set_slot(6, f"<Player_Character>\n{player_data}\n</Player_Character>")

        # [7] NPC Roles
        if npc_roles:
            self.set_slot(7, f"<NPC_Roles>\n{npc_roles}\n</NPC_Roles>")

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
            self.set_slot(13, f"<Input_Analysis>\n{input_analysis}\n</Input_Analysis>")

        # [14] Psyche States
        if psyche_states:
            self.set_slot(14, f"<Psyche_States>\n{psyche_states}\n</Psyche_States>")

        # [16] Scene Intelligence (Aspects + SensoryAnchors + Habitus + Hook)
        if scene_intelligence:
            self.set_slot(16, f"<Scene_Intelligence>\n{scene_intelligence}\n</Scene_Intelligence>")

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
            self.set_slot(29, f"<Real_Time_Status>\n{real_time_data}\n</Real_Time_Status>")

        # [30] GM Mover
        if gm_mover:
            cognitive_int = getattr(text_resources, 'COGNITIVE_DATA_INTEGRATION', '')
            self.set_slot(30, f"<GM_Analysis>\n{gm_mover}\n</GM_Analysis>\n\n{cognitive_int}")

        # [31] Last Response (직전 AI 응답 - 유저 입력 바로 앞!)
        if last_response:
            self.set_slot(31, f"<Last_Response>\n{last_response}\n</Last_Response>")

        # [32] User Input (현재 유저 입력 - 직전 응답 바로 뒤!)
        if user_input:
            self.set_slot(32, f"<User_Input>\n{user_input}\n</User_Input>")

        # [33] Self Correction + Author Note
        correction = getattr(text_resources, 'SELF_CORRECTION_BKSPC', '')
        if author_note:
            self.set_slot(33, f"{correction}\n\n<Author_Note>\n{author_note}\n</Author_Note>")
        elif self.active_genres or self.custom_tone:
            directive = legacy_builder.build_combined_directive(self.active_genres, self.custom_tone)
            self.set_slot(33, f"{correction}\n\n{directive}")
        elif correction:
            self.set_slot(33, correction)

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

                # 캐시 바운더리는 특별 처리
                if i == 26:
                    parts.append(content)
                else:
                    # XML 래핑 (디버깅 및 추적 용이)
                    parts.append(f"<!-- SLOT_{i}: {slot_name} -->\n{content}")

        # [레거시 재사용] 응답 길이 지시문 추가
        length_instruction = legacy_builder.build_length_instruction()
        parts.append(f"<!-- SLOT_EXTRA: LENGTH_INSTRUCTION -->\n{length_instruction}")

        return "\n\n".join(parts)

    def build_static_only(self) -> str:
        """정적 슬롯(1-25)만 빌드 (캐시용)"""
        parts = []
        for i in range(1, 26):
            content = self.slots.get(i)
            if content:
                slot_def = SLOT_DEFINITIONS.get(i)
                slot_name = slot_def.name if slot_def else f"SLOT_{i}"
                parts.append(f"<!-- SLOT_{i}: {slot_name} -->\n{content}")
        return "\n\n".join(parts)

    def build_dynamic_only(self) -> str:
        """동적 슬롯(26-34)만 빌드"""
        parts = []
        for i in range(26, 35):
            content = self.slots.get(i)
            if content:
                slot_def = SLOT_DEFINITIONS.get(i)
                slot_name = slot_def.name if slot_def else f"SLOT_{i}"
                if i == 26:
                    parts.append(content)
                else:
                    parts.append(f"<!-- SLOT_{i}: {slot_name} -->\n{content}")
        return "\n\n".join(parts)


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
    import domain_manager  # Lazy import to avoid circular

    builder = SlotPromptBuilder()

    # 장르/톤/씬 설정 (레거시 호환)
    builder.set_genres(getattr(ctx, 'active_genres', None))
    builder.set_tone(getattr(ctx, 'custom_tone', None))
    builder.set_scene(getattr(ctx, 'scene_type', 'normal'))

    # 1. 정적 슬롯 로드
    builder.populate_static_slots()

    # =========================================================
    # 2. 동적 슬롯 주입 (Phase 2 강화)
    # =========================================================

    dai = getattr(ctx, 'dai', None) or {}

    # --- [Slot 6] PC Data (Rich Player Info) ---
    player_info = ""
    channel_id = getattr(ctx, 'channel_id', '')
    user_id = getattr(ctx, 'user_id', '')
    if channel_id and user_id:
        try:
            rich_player_info = domain_manager.get_unified_player_info(channel_id, user_id)
            if rich_player_info:
                player_info = rich_player_info
        except Exception as e:
            logger.warning(f"Failed to get rich player info: {e}")

    if not player_info:
        player_data = getattr(ctx, 'player_data', None)
        if player_data:
            player_info = f"Name: {player_data.get('mask', 'Unknown')}\n"

    # --- [Slot 7] NPC Roles ---
    domain_data = getattr(ctx, 'domain_data', {}) or {}
    npc_roles = str(domain_data.get("npcs", "")) if domain_data.get("npcs") else ""

    # --- [Slot 8] Lore (RAG Context Diet 적용) ---
    relevant_context = dai.get("relevant_context", [])
    if isinstance(relevant_context, list) and relevant_context:
        logger.info(f"[Context Diet] Using {len(relevant_context)} extracted items.")
        lore_content = (
            "### [RAG: FILTERED CONTEXT] (Full Lore Hidden for Efficiency)\n"
            "The following rules/lore are extracted as MOST RELEVANT for this turn:\n"
            + "\n".join([f"- {item}" for item in relevant_context])
            + "\n\n(Use this context faithfully. If information is missing, rely on General Logic.)"
        )
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

    # Position/Effect: 상황 위치 및 영향력
    position = dai.get("position", {})
    effect = dai.get("effect", {})
    if position:
        input_analysis_parts.append(
            f"Position: {position.get('value', 0.5)} ({position.get('reason', '')})"
        )
    if effect:
        input_analysis_parts.append(
            f"Effect: {effect.get('value', 0.5)} ({effect.get('reason', '')})"
        )

    input_analysis = "\n".join(input_analysis_parts)

    # --- [Slot 16] Scene Intelligence (Aspects + SensoryAnchors + Habitus + Hook) ---
    scene_intel_parts = []

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
        scene_intel_parts.append(f"### Narrative Hook\n{hook}")

    scene_intelligence = "\n\n".join(scene_intel_parts)

    # --- [Slot 14] Psyche States (6-Axis, Structured) ---
    psyche_states = ""
    psyche_data = dai.get("psyche_states", {})
    if psyche_data and isinstance(psyche_data, dict):
        psyche_lines = []
        for char_name, state in psyche_data.items():
            if isinstance(state, str):
                psyche_lines.append(f"- {char_name}: {state}")
            elif isinstance(state, dict):
                mental = state.get("mental", {})
                soma = state.get("soma", {})
                relation = state.get("relation", {})
                psyche_lines.append(
                    f"- {char_name}: "
                    f"Μ[{mental.get('descriptor', '?')}±{mental.get('value', 0)}] "
                    f"Φ[{soma.get('descriptor', '?')}] "
                    f"Ι[{relation.get('descriptor', '?')}±{relation.get('value', 0)}]"
                )
        psyche_states = "\n".join(psyche_lines)

    # --- [Slot 28] Narrative Chain ---
    narrative_chain = ""
    chain_data = dai.get("narrative_chain", {})
    if chain_data and isinstance(chain_data, dict):
        narrative_chain = (
            f"chain_status: {chain_data.get('chain_status', 'OPEN')}\n"
            f"topic_lock: {chain_data.get('topic_lock', 'None')}\n"
            f"conclusion_proximity: {chain_data.get('conclusion_proximity', 'N/A')}"
        )

    # --- [Slot 30] GM Mover ---
    gm_mover = ""
    gm_move = dai.get("gm_move", {})
    if gm_move:
        gm_mover = f"type: {gm_move.get('type', 'N/A')}\ndescription: {gm_move.get('description', '')}"

    # --- [Slot 29] Real-time Data (World Context + Variables) ---
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
    # 4. 동적 슬롯 주입 실행
    # =========================================================

    builder.populate_dynamic_slots(
        player_data=player_info,
        npc_roles=npc_roles,
        lore=lore_content,
        fermented_history=fermented_history,
        input_analysis=input_analysis,
        psyche_states=psyche_states,
        scene_intelligence=scene_intelligence,
        chapter_context=getattr(ctx, 'obj_ctx', ''),
        content_level=getattr(ctx, 'scene_type', 'normal'),
        older_history=older_history,
        last_response=last_response,
        narrative_chain=narrative_chain,
        real_time_data=real_time_data,
        gm_mover=gm_mover,
        user_input=getattr(ctx, 'action_text', ''),
        author_note=""  # 장르/톤이 설정되어 있으면 자동으로 레거시 함수 사용
    )

    return builder.build()
