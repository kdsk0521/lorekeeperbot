"""
Lorekeeper TRPG Bot - Slot-Based Prompt Manager (V2 Refactored)
34단계 프롬프트 아키텍처를 위한 슬롯 관리자 모듈입니다.

[리팩토링] 기존 prompt_builder.py와 orchestration_response.py의 유틸리티들을
재사용하여 코드 중복을 최소화합니다.
"""

import logging
from typing import Dict, Any, Optional, List
from dataclasses import dataclass
import text_resources

# [레거시 재사용] 기존 모듈에서 유용한 함수 임포트
import prompt_builder as legacy_builder

logger = logging.getLogger("SlotManager")

# =========================================================
# 34-Step Slot Definition (Phase 1: Static Foundation)
# =========================================================

@dataclass
class SlotDefinition:
    """개별 슬롯의 정의"""
    index: int
    name: str
    category: str  # identity, world, rules, reasoning, dynamic, etc.
    source: str    # text_resources, cognition, domain_manager, etc.
    is_static: bool = True  # True면 캐시 가능, False면 매 턴 동적


# 34개 슬롯 정의 - implementation_plan.md 매핑을 그대로 반영
SLOT_DEFINITIONS: Dict[int, SlotDefinition] = {
    # [Identity: 1-2]
    1: SlotDefinition(1, "AI_MANDATE", "identity", "text_resources.CONTENT_AUTHORIZATION_MANDATE"),
    2: SlotDefinition(2, "AI_IDENTITY", "identity", "text_resources.AI_CORE_IDENTITY"),
    
    # [World/PC: 3-6]
    3: SlotDefinition(3, "PC_DATA", "world", "ResponseContext.player_data", is_static=False),
    4: SlotDefinition(4, "NPC_ROLES", "world", "npc_manager.get_npcs", is_static=False),
    5: SlotDefinition(5, "WORLD_AXIOM", "world", "text_resources.WORLD_AXIOM + MEMORY_HIERARCHY"),
    6: SlotDefinition(6, "LORE", "world", "domain_manager.get_lore", is_static=False),
    
    # [History: 7]
    7: SlotDefinition(7, "FERMENTED_HISTORY", "history", "fermentation + cognition.memory_triggers", is_static=False),
    
    # [Temporal: 8]
    8: SlotDefinition(8, "TEMPORAL_FLOW", "rules", "text_resources.TEMPORAL_FLOW + TIME_ATMOSPHERE"),
    
    # [GM Conduct: 9-10]
    9: SlotDefinition(9, "PC_AUTONOMY", "rules", "text_resources.PC_AUTONOMY_DOCTRINE"),
    10: SlotDefinition(10, "OBSERVER_NEUTRALITY", "rules", "text_resources.OBSERVER_NEUTRALITY_DOCTRINE"),
    
    # [Rendering: 11]
    11: SlotDefinition(11, "PHYSICAL_RENDERING", "rules", "text_resources.PHYSICAL_RENDERING + ANTI_DIDACTIC"),
    
    # [Social: 12]
    12: SlotDefinition(12, "SOCIAL_INTERACTION", "rules", "text_resources.INTERACTION_MODEL + NPC_BEHAVIOR"),
    
    # [Style/Voice: 13]
    13: SlotDefinition(13, "STYLE_VOICE", "rules", "text_resources.ANTI_CLICHE + AUTHOR_PERSONA"),
    
    # [Reasoning: 14] - Cognition 주입
    14: SlotDefinition(14, "INPUT_ANALYSIS", "reasoning", "cognition.InputAnalysis", is_static=False),
    
    # [Cognition Psyche: 15-18]
    15: SlotDefinition(15, "PSYCHE_STATE_1", "reasoning", "cognition.psyche_states", is_static=False),
    16: SlotDefinition(16, "PSYCHE_STATE_2", "reasoning", "text_resources.PSYCHE_STATE_RENDERING"),
    17: SlotDefinition(17, "PSYCHE_STATE_3", "reasoning", "Reserve Slot"),
    18: SlotDefinition(18, "PSYCHE_STATE_4", "reasoning", "Reserve Slot"),
    
    # [Chapter: 19]
    19: SlotDefinition(19, "CHAPTER_CONTEXT", "context", "domain_manager.get_current_chapter", is_static=False),
    
    # [Status Instruction: 20]
    20: SlotDefinition(20, "STATUS_LAYOUT", "rules", "text_resources.STATUS_WINDOW_LAYOUT"),
    
    # [Mechanics: 21]
    21: SlotDefinition(21, "ACTION_RESOLUTION", "mechanics", "text_resources.ACTION_RESOLUTION + Aspects"),
    
    # [Content: 22-24]
    22: SlotDefinition(22, "VISCERAL_CONTENT", "content", "text_resources.VISCERAL (conditional)", is_static=False),
    23: SlotDefinition(23, "MATURE_CONTENT", "content", "text_resources.MATURE (conditional)", is_static=False),
    24: SlotDefinition(24, "HYBRID_CONTENT", "content", "text_resources.HYBRID (conditional)", is_static=False),
    
    # [Final Static: 25]
    25: SlotDefinition(25, "CRITICAL_PROTOCOL", "rules", "text_resources.CRITICAL + PCImpersonationCheck"),
    
    # ========== CACHE BOUNDARY ==========
    26: SlotDefinition(26, "CACHE_BOUNDARY", "boundary", "==========CACHE BOUNDARY==========", is_static=False),
    
    # [Live History: 27]
    27: SlotDefinition(27, "LIVE_HISTORY", "dynamic", "smart_history (1-2 turns)", is_static=False),
    
    # [Thread/Pace: 28]
    28: SlotDefinition(28, "NARRATIVE_CHAIN", "dynamic", "cognition.narrative_chain + PACING", is_static=False),
    
    # [Live Data: 29]
    29: SlotDefinition(29, "REAL_TIME_DATA", "dynamic", "world_context (Doom, HP, Time)", is_static=False),
    
    # [Analysis: 30]
    30: SlotDefinition(30, "GM_MOVER", "dynamic", "cognition.GMMover + COGNITIVE_INTEGRATION", is_static=False),
    
    # [Material: 31]
    31: SlotDefinition(31, "USER_INPUT", "dynamic", "action_text", is_static=False),
    
    # [Correction: 32]
    32: SlotDefinition(32, "SELF_CORRECTION", "dynamic", "text_resources.SELF_CORRECTION_BKSPC"),
    
    # [Notes: 33 - 레거시 재사용: build_combined_directive]
    33: SlotDefinition(33, "AUTHOR_NOTE", "dynamic", "legacy_builder.build_combined_directive", is_static=False),
    
    # [Kernel: 34]
    34: SlotDefinition(34, "NARRATIVE_KERNEL", "kernel", "text_resources.NARRATIVE_KERNEL"),
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
    # Static Slot Population (Steps 1-25)
    # =========================================================
    
    def populate_static_slots(self) -> 'SlotPromptBuilder':
        """
        정적 슬롯들(1~25)을 text_resources에서 로드합니다.
        이 부분은 캐시 가능하므로 한 번만 빌드합니다.
        """
        if self._static_built:
            return self
            
        # [1-2] Identity
        self.set_slot(1, text_resources.CONTENT_AUTHORIZATION_MANDATE)
        self.set_slot(2, text_resources.AI_CORE_IDENTITY)
        
        # [5] World Axiom
        self.set_slot(5, f"{text_resources.WORLD_AXIOM}\n\n{text_resources.MEMORY_HIERARCHY}")
        
        # [8] Temporal
        temporal = getattr(text_resources, 'TEMPORAL_FLOW_DOCTRINE', '') or getattr(text_resources, 'TEMPORAL_FLOW', '')
        time_atm = getattr(text_resources, 'TIME_ATMOSPHERE', '')
        self.set_slot(8, f"{temporal}\n\n{time_atm}")
        
        # [9-10] GM Conduct
        self.set_slot(9, text_resources.PC_AUTONOMY_DOCTRINE)
        self.set_slot(10, text_resources.OBSERVER_NEUTRALITY_DOCTRINE)
        
        # [11] Rendering
        physical = getattr(text_resources, 'PHYSICAL_RENDERING_DOCTRINE', '') or getattr(text_resources, 'PHYSICAL_RENDER', '')
        anti_didactic = getattr(text_resources, 'ANTI_DIDACTIC_PRINCIPLES', '')
        self.set_slot(11, f"{physical}\n\n{anti_didactic}")
        
        # [12] Social
        interaction = getattr(text_resources, 'INTERACTION_MODEL', '')
        npc_behavior = getattr(text_resources, 'NPC_BEHAVIOR_SYSTEM', '')
        self.set_slot(12, f"{interaction}\n\n{npc_behavior}")
        
        # [13] Style/Voice
        anti_cliche = getattr(text_resources, 'ANTI_CLICHE_PROTOCOL', '')
        author_persona = getattr(text_resources, 'AUTHOR_PERSONA_PROTOCOL', '')
        self.set_slot(13, f"{anti_cliche}\n\n{author_persona}")
        
        # [16] Psyche Rendering Instruction
        psyche_render = getattr(text_resources, 'PSYCHE_STATE_RENDERING', '')
        self.set_slot(16, psyche_render)
        
        # [20] Status Layout
        status_layout = getattr(text_resources, 'STATUS_WINDOW_LAYOUT', '')
        self.set_slot(20, status_layout)
        
        # [21] Action Resolution
        action_res = getattr(text_resources, 'ACTION_RESOLUTION', '')
        aspect_util = getattr(text_resources, 'ASPECT_UTILIZATION', '')
        self.set_slot(21, f"{action_res}\n\n{aspect_util}")
        
        # [25] Critical Protocol
        critical = getattr(text_resources, 'CRITICAL_PROTOCOL', '')
        self.set_slot(25, critical)
        
        # [26] Cache Boundary
        self.set_slot(26, "\n==========CACHE BOUNDARY==========\n")
        
        # [32] Self Correction
        self.set_slot(32, getattr(text_resources, 'SELF_CORRECTION_BKSPC', ''))
        
        # [34] Narrative Kernel
        kernel = getattr(text_resources, 'NARRATIVE_KERNEL', '')
        output_protocol = getattr(text_resources, 'OUTPUT_PROTOCOL', '')
        self.set_slot(34, f"{output_protocol}\n\n{kernel}")
        
        self._static_built = True
        logger.info("[SlotPromptBuilder] Static slots (1-25, 32, 34) populated.")
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
        chapter_context: str = "",
        content_level: str = "normal",
        live_history: str = "",
        narrative_chain: str = "",
        real_time_data: str = "",
        gm_mover: str = "",
        user_input: str = "",
        author_note: str = ""
    ) -> 'SlotPromptBuilder':
        """동적 슬롯들을 주입합니다. 레거시 함수들을 재사용."""
        
        # [3] PC Data
        if player_data:
            self.set_slot(3, f"<Player_Character>\n{player_data}\n</Player_Character>")
        
        # [4] NPC Roles
        if npc_roles:
            self.set_slot(4, f"<NPC_Roles>\n{npc_roles}\n</NPC_Roles>")
            
        # [6] Lore
        if lore:
            self.set_slot(6, f"<Lore>\n{lore}\n</Lore>")
            
        # [7] Fermented History
        if fermented_history:
            self.set_slot(7, f"<Fermented_Memory>\n{fermented_history}\n</Fermented_Memory>")
            
        # [14] Input Analysis (Cognition)
        if input_analysis:
            self.set_slot(14, f"<Input_Analysis>\n{input_analysis}\n</Input_Analysis>")
            
        # [15] Psyche States
        if psyche_states:
            self.set_slot(15, f"<Psyche_States>\n{psyche_states}\n</Psyche_States>")
            
        # [19] Chapter Context
        if chapter_context:
            self.set_slot(19, f"<Chapter_Context>\n{chapter_context}\n</Chapter_Context>")
            
        # [22-24] Content Level - 레거시 함수 재사용
        self._populate_content_slots_legacy(content_level)
        
        # [27] Live History
        if live_history:
            self.set_slot(27, f"<Live_History>\n{live_history}\n</Live_History>")
            
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
            
        # [31] User Input
        if user_input:
            self.set_slot(31, f"<User_Input>\n{user_input}\n</User_Input>")
            
        # [33] Author Note - 레거시 함수 재사용
        if author_note:
            self.set_slot(33, f"<Author_Note>\n{author_note}\n</Author_Note>")
        elif self.active_genres or self.custom_tone:
            # 레거시 build_combined_directive 재사용
            directive = legacy_builder.build_combined_directive(self.active_genres, self.custom_tone)
            self.set_slot(33, directive)
            
        return self
    
    def _populate_content_slots_legacy(self, content_level: str) -> None:
        """
        콘텐츠 수위 슬롯 설정 - 레거시 build_mature_content_prompt 재사용
        """
        if content_level and content_level != 'normal':
            mature_prompt = legacy_builder.build_mature_content_prompt(content_level)
            if mature_prompt:
                # 22번 슬롯에 통합 배치
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
    
    # --- [Step 3] PC Data (Rich Player Info) ---
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
    
    # --- [Step 4] NPC Roles ---
    domain_data = getattr(ctx, 'domain_data', {}) or {}
    npc_roles = str(domain_data.get("npcs", "")) if domain_data.get("npcs") else ""
    
    # --- [Step 6] Lore (RAG Context Diet 적용) ---
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
    
    # --- [Step 7] Fermented History + Memory Triggers ---
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
    
    # --- [Step 14] Input Analysis (Enhanced) ---
    input_analysis = ""
    input_analysis_data = dai.get("input_analysis", {})
    if input_analysis_data:
        input_analysis = (
            f"Original: {input_analysis_data.get('Original', 'N/A')}\n"
            f"Enhanced: {input_analysis_data.get('Enhanced', 'N/A')}\n"
            f"Plausibility: {input_analysis_data.get('Plausibility', 'N/A')}\n"
            f"Momentum: {input_analysis_data.get('Momentum', 'OPEN')}"
        )
    
    # --- [Step 15] Psyche States (6-Axis, Structured) ---
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
    
    # --- [Step 28] Narrative Chain ---
    narrative_chain = ""
    chain_data = dai.get("narrative_chain", {})
    if chain_data and isinstance(chain_data, dict):
        narrative_chain = (
            f"chain_status: {chain_data.get('chain_status', 'OPEN')}\n"
            f"topic_lock: {chain_data.get('topic_lock', 'None')}\n"
            f"conclusion_proximity: {chain_data.get('conclusion_proximity', 'N/A')}"
        )
    
    # --- [Step 30] GM Mover ---
    gm_mover = ""
    gm_move = dai.get("gm_move", {})
    if gm_move:
        gm_mover = f"type: {gm_move.get('type', 'N/A')}\ndescription: {gm_move.get('description', '')}"
    
    # --- [Step 29] Real-time Data (World Context + Variables) ---
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
    # 3. 동적 슬롯 주입 실행
    # =========================================================
    
    builder.populate_dynamic_slots(
        player_data=player_info,
        npc_roles=npc_roles,
        lore=lore_content,
        fermented_history=fermented_history,
        input_analysis=input_analysis,
        psyche_states=psyche_states,
        chapter_context=getattr(ctx, 'obj_ctx', ''),
        content_level=getattr(ctx, 'scene_type', 'normal'),
        live_history=getattr(ctx, 'hist_text', ''),
        narrative_chain=narrative_chain,
        real_time_data=real_time_data,
        gm_mover=gm_mover,
        user_input=getattr(ctx, 'action_text', ''),
        author_note=""  # 장르/톤이 설정되어 있으면 자동으로 레거시 함수 사용
    )
    
    return builder.build()
