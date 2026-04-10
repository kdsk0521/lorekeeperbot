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
import config as _cfg
# Kimi 전용 오버라이드: 존재하면 text_resources 상수를 덮어씀
if _cfg.RENDERER_BACKEND == "openai":
    try:
        import text_resources_kimi as _kimi_tr
        for _attr in dir(_kimi_tr):
            if _attr.isupper() and not _attr.startswith("_"):
                setattr(text_resources, _attr, getattr(_kimi_tr, _attr))
        logging.getLogger("SlotManager").info("[SlotManager] Kimi text_resources override applied")
    except ImportError:
        pass
import iceberg

# [레거시 재사용] 기존 모듈에서 유용한 함수 임포트
import prompt_builder as legacy_builder

logger = logging.getLogger("SlotManager")


# N5: 카테고리-슬롯 자동 배치 매핑
CATEGORY_SLOT_MAP = {
    'world_rule': 5,        # World Axiom
    'character': 6,          # PC Data
    'npc_info': 7,           # NPC Role
    'lore': 8,               # Lore
    'temporal': 10,           # Time Flow
    'interaction': 12,        # Social Interaction
    'narrative_rule': 25,     # Anti-Cliche + Style
    'real_time': 29,          # Real-Time Data
}


def get_slot_for_category(category: str) -> int:
    """카테고리에 해당하는 슬롯 번호 반환. 기본값: 8 (로어)."""
    return CATEGORY_SLOT_MAP.get(category, 8)


# =========================================================
# Dynamic STATUS_WINDOW_LAYOUT Builder
# =========================================================

def _build_status_layout(active_modules: list = None, present_chars: str = "") -> str:
    """STATUS_WINDOW_LAYOUT 생성. 모든 핵심 모듈은 항상 활성.
    active_modules 인자는 하위 호환용으로 유지하나 무시됨.
    present_chars: 현재 장면 인물 힌트 (gaze 기반)."""

    # --- FORMAT block (모든 메트릭 항상 포함) ---
    fmt = [
        "위치 [Location] | 시간 [Day]일차 [HH:MM] ([TimeSlot]) | 인물 [Present Characters]",
        "기력 [value] | 평정 [value] | 로드아웃 [used/total] | Doom [value]",
        "[Clock1 filled/segments] [Clock2 filled/segments ...]",
    ]
    ex = [
        "위치 하숙집 거실 | 시간 3일차 04:30 (새벽) | 인물 리미, 옥상 남자",
        "기력 72 | 평정 38 | 로드아웃 1/4 | Doom 45",
        "[조직의 추적 4/6] [붉은 문턱 2/4]",
    ]

    # --- RULES block ---
    rules = [
        "- Line 1: location, time, characters.",
        "- Line 2: Vigor + Composure + Global Doom (numeric only).",
        "- Line 3: active doom clocks only. Omit line 3 if no active clock.",
        "- Keep it compact and stable across turns.",
    ]
    if present_chars:
        rules.append(f"- CURRENT SCENE CHARACTERS: {present_chars}")

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
# §S Spatial Sense — 공간 유형 → 물성 + 감각 잔류 힌트
# =========================================================

_SPATIAL_HINTS = {
    "enclosed":  "[§S] 밀폐 — 냄새와 체온이 오래 남는다. 시선을 피하기 어렵고, 침묵이 무겁다",
    "resonant":  "[§S] 반향 — 발소리가 벽을 타고 돌아온다. 빈 공간이 존재감을 갖고, 속삭임도 멀리 간다",
    "open":      "[§S] 개방 — 바람이 흔적을 지운다. 발자국만 남고, 거리가 몸 사이를 벌린다",
    "elevated":  "[§S] 고소 — 바람이 체온을 앗아간다. 소리는 아래로 떨어지고, 몸이 노출된다",
    "crowded":   "[§S] 군중 — 개별 흔적이 소음에 묻힌다. 가까이 붙어야 하고, 사적 공간이 사라진다",
    "moving":    "[§S] 이동 — 흔적을 남길 수 없다. 진동이 몸에 전해지고, 공간 자체가 일시적이다",
}

# Architecture.decay_profile — 코드 보관, 후속 확장용 (현재 프롬프트 미사용)
_DECAY_PROFILE = {
    "enclosed":  {"scent": "high", "thermal": "high", "acoustic": "absorbed", "visual": "high"},
    "resonant":  {"scent": "low",  "thermal": "low",  "acoustic": "high",     "visual": "mid"},
    "open":      {"scent": "none", "thermal": "none", "acoustic": "none",     "visual": "low"},
    "elevated":  {"scent": "none", "thermal": "none", "acoustic": "none",     "visual": "mid"},
    "crowded":   {"scent": "noise","thermal": "noise","acoustic": "noise",    "visual": "noise"},
    "moving":    {"scent": "none", "thermal": "none", "acoustic": "none",     "visual": "none"},
}


def _resolve_spatial(dai: dict) -> str:
    """spatial_read.spatial_type(Flash 판단) → §S 힌트. 없으면 빈 문자열."""
    spatial = dai.get("spatial_read")
    if not spatial or not isinstance(spatial, dict):
        return ""
    stype = spatial.get("spatial_type", "")
    return _SPATIAL_HINTS.get(stype, "")



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

    # [§S] Spatial Sense (Flash spatial_type 판단 기반)
    spatial_hint = _resolve_spatial(dai)
    if spatial_hint:
        scene_lines.append(f"  ├ {spatial_hint}")

    if not scene_lines:
        return ""

    header = "[Scene] — 장면 구조"
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

    # ===== WORLD ZONE (5-9): 참조 데이터 (중간 배치 OK) =====
    5: SlotDefinition(5, "WORLD_AXIOM", "world", "text_resources.WORLD_AXIOM"),
    6: SlotDefinition(6, "PC_DATA", "world", "ResponseContext.player_data", is_static=False),
    7: SlotDefinition(7, "NPC_ROLES", "world", "npc_manager.get_npcs", is_static=False),
    8: SlotDefinition(8, "LORE", "world", "domain_manager.get_lore", is_static=False),
    9: SlotDefinition(9, "FERMENTED_HISTORY", "history", "fermentation + cognition.memory_triggers", is_static=False),

    # ===== CONTEXT ZONE (10-12): 현재 상황 =====
    10: SlotDefinition(10, "TEMPORAL_FLOW", "context", "text_resources.TEMPORAL_FLOW_DOCTRINE"),
    11: SlotDefinition(11, "CHAPTER_CONTEXT", "context", "domain_manager.get_current_chapter", is_static=False),
    12: SlotDefinition(12, "SOCIAL_INTERACTION", "context", "text_resources.INTERACTION_MODEL + NPC_BEHAVIOR_SYSTEM"),

    # ===== COGNITION ZONE (13-17): Theoria 분석 데이터 =====
    13: SlotDefinition(13, "INPUT_ANALYSIS", "reasoning", "Theoria: InputAnalysis + Observation + UserIntent + Position/Effect", is_static=False),
    14: SlotDefinition(14, "PSYCHE_STATES", "reasoning", "Theoria: psyche_states (6-Axis)", is_static=False),
    16: SlotDefinition(16, "SCENE_INTELLIGENCE", "reasoning", "Theoria: Aspects + SensoryAnchors + HabitusAnalysis + narrative_hook", is_static=False),
    17: SlotDefinition(17, "EXTENDED_INTELLIGENCE", "reasoning", "Theoria: NPCKnowledge + IntimacyAnalysis", is_static=False),

    # ===== RULES ZONE (18-25): Static Recency - 행동 규칙 강화 =====
    18: SlotDefinition(18, "PC_AUTONOMY", "rules", "text_resources.PC_AUTONOMY_DOCTRINE"),
    20: SlotDefinition(20, "STATUS_LAYOUT", "rules", "_build_status_layout() dynamic"),
    22: SlotDefinition(22, "VISCERAL_CONTENT", "content", "text_resources.VISCERAL (conditional)", is_static=False),
    25: SlotDefinition(25, "STYLE", "rules", "text_resources.PROSE_CRAFT_PROTOCOL"),

    # ========== CACHE BOUNDARY ==========
    26: SlotDefinition(26, "CACHE_BOUNDARY", "boundary", "==========CACHE BOUNDARY==========", is_static=False),

    # ===== DYNAMIC ZONE (27-34): 최강 Recency =====
    27: SlotDefinition(27, "OLDER_HISTORY", "dynamic", "smart_history (2~11턴 전)", is_static=False),
    28: SlotDefinition(28, "NARRATIVE_CHAIN", "dynamic", "cognition.narrative_chain", is_static=False),
    29: SlotDefinition(29, "REAL_TIME_DATA", "dynamic", "world_context (Doom, HP, Time)", is_static=False),
    30: SlotDefinition(30, "GM_MOVER", "dynamic", "cognition.GMMover", is_static=False),
    31: SlotDefinition(31, "LAST_RESPONSE", "dynamic", "직전 AI 응답 (turn -1)", is_static=False),
    32: SlotDefinition(32, "USER_INPUT", "dynamic", "현재 유저 입력", is_static=False),
    33: SlotDefinition(33, "AUTHOR_NOTE", "dynamic", "AUTHOR_NOTE + GENRE_DIRECTIVE", is_static=False),
    34: SlotDefinition(34, "TELESCOPE", "kernel", "TELESCOPE_PROTOCOL"),
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

        # [3] Mirror Workshop
        self.set_slot(3, getattr(text_resources, 'MIRROR_WORKSHOP_PROTOCOL', ''))

        # [4] Narrative Priority (W4)
        self.set_slot(4, getattr(text_resources, 'NARRATIVE_PRIORITY', ''))

        # ===== WORLD ZONE (5) =====
        self.set_slot(5, text_resources.WORLD_AXIOM)

        # ===== CONTEXT ZONE (10, 12) =====
        self.set_slot(10, getattr(text_resources, 'TEMPORAL_FLOW_DOCTRINE', ''))

        # [12] Social
        interaction = getattr(text_resources, 'INTERACTION_MODEL', '')
        npc_behavior = getattr(text_resources, 'NPC_BEHAVIOR_SYSTEM', '')
        self.set_slot(12, f"{interaction}\n\n{npc_behavior}")

        # ===== RULES ZONE (18-25) =====
        self.set_slot(18, text_resources.PC_AUTONOMY_DOCTRINE)
        # [19] Writing Directives — ɑ/ɑ′ Dual-Path (W11)
        self.set_slot(19, getattr(text_resources, 'WRITING_DIRECTIVES', ''))
        # [21] Input Authority — Decree/Attempt (W3)
        self.set_slot(21, getattr(text_resources, 'INPUT_AUTHORITY', ''))
        self.set_slot(20, "")  # 동적 빌더가 덮어씀
        self.set_slot(25, getattr(text_resources, 'PROSE_CRAFT_PROTOCOL', ''))

        # ===== CACHE BOUNDARY =====
        self.set_slot(26, "\n==========CACHE BOUNDARY==========\n")

        # ===== DYNAMIC ZONE (34) =====
        _telescope = getattr(text_resources, 'TELESCOPE_PROTOCOL', '')
        if _cfg.RENDERER_BACKEND == "openai":
            _telescope += (
                "\n\n### STRICT BUDGET (renderer-specific)"
                "\n┣┫ block MUST stay ≤ 250 words. Telegraphic English only."
                "\nProse MUST be ≥ 3× telescope length. If prose is short, telescope was too long."
                "\nDo NOT repeat ┣ blocks. One ┣...┫ per response."
            )
        self.set_slot(34, _telescope)

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
        if player_data:
            if "\n---\n" in player_data:
                self.set_slot(6, f"<Player_Characters>\n{player_data}\n</Player_Characters>")
            else:
                self.set_slot(6, f"<Player_Character>\n{player_data}\n</Player_Character>")

        # [7] NPC Roles
        # BABEL Pidgin→Creole + Knowledge Isolation (유일한 선언 지점)
        if npc_roles:
            _pidgin = (
                "[PIDGIN→CREOLE + KNOWLEDGE ISOLATION]\n"
                "Profiles below = author reference, NOT prose vocabulary, NOT character knowledge.\n"
                "If a profile word appears as an adjective in your output, you have failed. Transform:\n"
                "- personality label → physical consequence (behavior, not adjective)\n"
                "- appearance → arrives piecemeal through different moments/gazes, not listed\n"
                "- background → residue in present behavior only (hesitation, reflex, avoidance)\n"
                "- speech/tone → dialogue PERFORMS the pattern. Describing it = narrating the label.\n"
                "NPCs know ONLY what they acquired through in-scene interaction.\n"
                "Absent scene = unknown. Unacquired name → 'that person'. Profile data ≠ character knowledge.\n\n"
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
        # [27] Gemini 채팅 히스토리에 원문 이미 포함. 시간 우선순위 지시만 유지.
        self.set_slot(27, (
            "[TEMPORAL PRIORITY] 현재 장면 데이터(Real_Time_Status, User_Input, Scene_Intelligence)가 "
            "이전 대화 패턴보다 항상 우선한다. 과거 대화는 연속성 참고용이며, "
            "동일한 감정 흐름·장면 구조·대사 패턴을 반복하지 말 것."
        ))

        # [28] Narrative Chain — PACING_CONTROL removed (codified into iceberg.translate_energy_direction)
        if narrative_chain:
            self.set_slot(28, f"<Narrative_Chain>\n{narrative_chain}\n</Narrative_Chain>")

        # [29] Real-time Data
        if real_time_data:
            self.set_slot(29, f"<Real_Time_Status>\n[GROUND_TRUTH] Current world state from game mechanics.\n{real_time_data}\n</Real_Time_Status>")

        # [30] World Response (GM Mover)
        if gm_mover:
            # COGNITIVE_DATA_INTEGRATION은 AI_CORE_IDENTITY로 병합됨
            self.set_slot(30, f"<World_Response>\n{gm_mover}\n</World_Response>")

        # [31] Last Response (직전 AI 응답 끝부분 — recency 앵커. 전문은 Gemini 히스토리에 있음)
        if last_response:
            # 마지막 2문단만 추출 (~500자 캡)
            paragraphs = [p.strip() for p in last_response.split("\n\n") if p.strip()]
            tail = "\n\n".join(paragraphs[-2:]) if len(paragraphs) > 2 else last_response
            if len(tail) > 500:
                tail = tail[-500:]
            self.set_slot(31, f"<Last_Response_Tail>\n{tail}\n</Last_Response_Tail>")

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

    def build_split(self) -> tuple:
        """OpenAI용: system(규칙) + context(데이터)로 분리 빌드.
        system = 지시/규칙 슬롯, context = NPC/로어/히스토리/분석 데이터.
        Returns: (system_prompt, context_prompt)"""
        # 규칙 슬롯: 1-4 (Primacy), 10, 12, 18-25 (Rules), 34 (Telescope)
        _RULE_SLOTS = {1, 2, 3, 4, 10, 12, 18, 19, 20, 21, 22, 25, 34}
        system_parts = []
        context_parts = []
        for i in range(1, 35):
            content = self.slots.get(i)
            if not content:
                continue
            if i in _RULE_SLOTS:
                system_parts.append(content)
            else:
                context_parts.append(content)
        return "\n\n".join(system_parts), "\n\n".join(context_parts)


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


def _extract_voice_quirks(voice_card: str) -> str:
    """voice_card에서 Quirks 줄만 추출 (대사 디렉티브 합성용)."""
    for line in voice_card.split("\n"):
        stripped = line.strip()
        if stripped.lower().startswith("quirks:"):
            return stripped[7:].strip()
    return ""


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
        # gaze에서 현재 장면 인물 추출
        _present_chars = ""
        try:
            _frame = domain_manager.get_latest_frame(channel_id)
            _gaze = _frame.get("render_fingerprint", {}).get("gaze", "")
            if _gaze and isinstance(_gaze, str):
                _present_chars = _gaze.strip()
        except Exception:
            pass
        builder.set_slot(20, _build_status_layout(_active_modules, _present_chars))

    # =========================================================
    # 2. 동적 슬롯 주입 (Phase 2 강화)
    # =========================================================

    dai = getattr(ctx, 'dai', None) or {}

    # --- [Slot 12] NPC Relation Context (entity_relations — 동적 append) ---
    if channel_id:
        try:
            import entity_relations
            _relevant_npcs = dai.get("relevant_npcs", [])
            _npc_names = [n if isinstance(n, str) else n.get("name", "") for n in _relevant_npcs] if _relevant_npcs else None
            _rel_ctx = entity_relations.build_relation_context(channel_id, relevant_npcs=_npc_names)
            if _rel_ctx:
                _slot12 = builder.get_slot(12) or ""
                builder.set_slot(12, f"{_slot12}\n\n{_rel_ctx}" if _slot12 else _rel_ctx)
        except Exception:
            pass

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
        # P5: Renderer gets secret-stripped profiles; Theoria already has full data
        full_profiles = _npc_mgr.get_npc_renderer_profiles(channel_id, relevant_npcs, scene_type=npc_scene_type)
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

    # --- [Slot 8+] Location Rules (!룰 추가) → Lore 하단 append ---
    if channel_id:
        _ws = domain_manager.get_world_state(channel_id)
        _rules_parts = []
        # 파일 원본 텍스트
        _rules_text = _ws.get("rules_text", "")
        if _rules_text:
            _rules_parts.append(_rules_text)
        # 개별 추가 규칙 (!룰 추가 key desc)
        _loc_rules = _ws.get("location_rules", {})
        if _loc_rules:
            rule_lines = []
            for k, v in _loc_rules.items():
                desc = v.get("desc", "") if isinstance(v, dict) else str(v)
                rule_lines.append(f"- {k}: {desc}")
            _rules_parts.append(" ".join(rule_lines))
        if _rules_parts:
            lore_content += "\n\n### [ACTIVE RULES]\n" + "\n".join(_rules_parts)

    # --- [Slot 9] Fermented History + Memory Triggers ---
    fermented_base = getattr(ctx, 'fermented_summary_text', '')
    memory_triggers = dai.get("memory_triggers", [])
    deep_data = getattr(ctx, 'deep_memory_data', {}) or {}
    active_triggers = deep_data.get("active_memory_triggers", [])

    fermented_history = fermented_base
    if memory_triggers or active_triggers:
        # Theoria DAI memory_triggers: type/character/echo 서브필드 보존
        trigger_lines = []
        dai_trigger_texts = set()
        for m in memory_triggers:
            if not isinstance(m, dict):
                continue
            trigger = m.get("trigger", "")
            if not trigger:
                continue
            dai_trigger_texts.add(trigger)
            mtype = m.get("type", "")
            char = m.get("character", "")
            echo = m.get("echo", "")
            line = trigger
            if char:
                line += f" ({char})"
            if echo:
                line += f" — {echo}"
            if mtype:
                line += f" [{mtype}]"
            trigger_lines.append(line)
        # Fermentation active_triggers: 중복 제거 후 추가
        for t in active_triggers:
            if t and t not in dai_trigger_texts:
                trigger_lines.append(t)
        if trigger_lines:
            triggers_str = "\n".join(f"- {t}" for t in trigger_lines)
            fermented_history = f"### [ACTIVE MEMORY TRIGGERS - Unresolved Narrative Hooks]\n{triggers_str}\n\n{fermented_base}"

    # 연대기 미해결 떡밥 주입 (자동 생성분)
    _chronicle_unresolved = domain_manager.get_session_ai_memory(channel_id).get("chronicle_unresolved", "")
    if not _chronicle_unresolved:
        _chronicles = domain_manager.get_domain(channel_id).get("chronicles", [])
        if _chronicles:
            _chronicle_unresolved = _chronicles[-1].get("unresolved", "")
    if _chronicle_unresolved:
        fermented_history = f"[연대기 떡밥] {_chronicle_unresolved}\n\n{fermented_history}"

    # --- [Slot 13] Input Analysis (Enhanced with Observation + Intent + Position/Effect) ---
    input_analysis_parts = []
    input_analysis_data = dai.get("input_analysis", {})
    if input_analysis_data:
        _ia_fields = [
            ("Original", input_analysis_data.get("Original")),
            ("Enhanced", input_analysis_data.get("Enhanced")),
            ("Plausibility", input_analysis_data.get("Plausibility")),
        ]
        _ia_lines = [f"{k}: {v}" for k, v in _ia_fields if v]
        _ia_lines.append(f"Momentum: {input_analysis_data.get('Momentum', 'OPEN')}")
        if _ia_lines:
            input_analysis_parts.append("\n".join(_ia_lines))
        # LogicTrace: 논리 추론 체인 (있을 때만)
        logic_trace = input_analysis_data.get("LogicTrace", [])
        if logic_trace and isinstance(logic_trace, list):
            trace_str = " → ".join(str(t) for t in logic_trace if t)
            if trace_str:
                input_analysis_parts.append(f"LogicTrace: {trace_str}")

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

    # EmotionEngine: NPC 감정 상태 컨텍스트 (급변 표시 포함)
    try:
        from emotion_engine import EmotionEngine, EmotionState
        _emo_bus = dai.get("_emotion_states_for_slot", {})
        if not _emo_bus and channel_id:
            import domain_manager as _dm_emo
            _raw_emo = _dm_emo.get_world_state(channel_id).get("npc_emotion_states", {})
            if _raw_emo:
                _emo_bus = {n: EmotionState.from_dict(s) for n, s in _raw_emo.items() if isinstance(s, dict)}
        if isinstance(_emo_bus, dict) and _emo_bus:
            _emo_text = EmotionEngine.build_emotion_context(_emo_bus)
            if _emo_text:
                scene_intel_parts.append(_emo_text)
    except Exception:
        pass

    # EnergyDirection: iceberg 번역 (라벨 → 톤/비트/종결 + 장면 빛)
    energy_dir = dai.get("energy_direction", "")
    energy_hint = iceberg.translate_energy_direction(energy_dir)
    if energy_hint:
        scene_intel_parts.append(energy_hint)

    # WorldTree: 공간 그래프 컨텍스트 (계층 경로 + 연결 + NPC 위치 + 출구)
    if channel_id:
        try:
            import world_tree
            _loc_ctx = world_tree.build_location_context_text(channel_id)
            if _loc_ctx:
                scene_intel_parts.append(_loc_ctx)
        except Exception:
            pass

    # scene_type: 하위 여러 곳에서 사용
    scene_type = dai.get("scene_type", "normal")

    # StoryDirection: iceberg 번역 (pacing + tension + transition + focus)
    _story_dir = dai.get("story_direction", {})
    _story_dir_hint = iceberg.translate_story_direction(_story_dir, scene_type)
    if _story_dir_hint:
        scene_intel_parts.append(_story_dir_hint)

    # Register Lens: 장면의 지배적 관계 역학 (H3 + B1 fallback)
    _register = dai.get("scene_register")
    if not _register or _register == "null":
        # B1: Flash가 null → psyche_states + narrative_chain에서 추론
        _register = iceberg.infer_scene_register(
            dai.get("psyche_states"), dai.get("narrative_chain"),
        )
    if _register:
        _reg_hint = iceberg.translate_register(_register)
        if _reg_hint:
            scene_intel_parts.append(_reg_hint)

    # Propagation Shape: 장면 전파 형태 (B5)
    _psyche_raw = dai.get("psyche_states", {})
    _nchain = dai.get("narrative_chain", {})
    _npc_count = len(_psyche_raw) if isinstance(_psyche_raw, dict) else 1
    _prop_shape = iceberg.infer_propagation_shape(
        _psyche_raw, _nchain, scene_type, energy_dir, _npc_count,
    )
    if _prop_shape:
        _prop_hint = iceberg.translate_propagation_shape(_prop_shape)
        if _prop_hint:
            scene_intel_parts.append(_prop_hint)

    # Detail Density: scene_type + energy → 디테일 밀도 캘리브레이션 (B6)
    _density_hint = iceberg.translate_detail_density(scene_type, energy_dir)
    if _density_hint:
        scene_intel_parts.append(_density_hint)

    # MemoryType: iceberg 번역 (기억 유형 → 산문 스타일 힌트, 발동 턴만)
    memory_hint = iceberg.translate_memory_type(memory_triggers)
    if memory_hint:
        scene_intel_parts.append(memory_hint)

    # TimeAtmosphere: iceberg 번역 (시간대 → 감각 힌트, 전투 duration)
    time_context = dai.get("TimeContext", dai.get("time_context", ""))
    time_atm = iceberg.translate_time_atmosphere(time_context, scene_type)
    if time_atm:
        scene_intel_parts.append(time_atm)

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

    # TemporalOrientation: iceberg 번역
    temporal_text = iceberg.translate_temporal_orientation(dai.get("TemporalOrientation"))
    if temporal_text:
        scene_intel_parts.append(temporal_text)

    # narrative_hook: 트위스트 제안
    hook = dai.get("narrative_hook", "")
    if hook:
        scene_intel_parts.append(f"### Narrative Hook [INFERRED]\n{hook}")

    # QualityFlags: iceberg 번역 (경고 라벨 → 행동 지시)
    qflags = dai.get("quality_flags", {})
    qflag_text = iceberg.translate_quality_flags(qflags)
    if qflag_text:
        scene_intel_parts.append("### 서사 품질 보정\n" + qflag_text)

    # W5: Pipeline Degradation Notice
    _degraded = dai.get("_degraded_stages", [])
    if _degraded:
        _deg_names = [d.get("stage", "?") for d in _degraded if isinstance(d, dict)]
        if _deg_names:
            scene_intel_parts.append(f"[System] 제한된 분석: {', '.join(_deg_names)}")

    # Scene Continuity: 불연속 감지 → 보정 지시
    continuity_data = dai.get("continuity_check", {})
    continuity_text = iceberg.translate_continuity_check(continuity_data)
    if continuity_text:
        scene_intel_parts.append(continuity_text)

    # Withholding Scheme rotation feedback (render_fingerprint → Slot 16)
    if channel_id:
        _prev_frame = domain_manager.get_latest_frame(channel_id)
        _prev_scheme = _prev_frame.get("render_fingerprint", {}).get("withholding_scheme", "")
        _scheme_text = iceberg.translate_prev_scheme(_prev_scheme)
        if _scheme_text:
            scene_intel_parts.append(_scheme_text)

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

    # Spatial Inscription: 공간 각인 렌더링 힌트
    spatial_read = dai.get("spatial_read")
    spatial_text = iceberg.translate_spatial_inscription(spatial_read)
    if spatial_text:
        extended_intel_parts.append(spatial_text)

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

        # npc_depths는 여기서 1회 계산, slot 14 psyche_states + slot 17 dialogue directives 모두 공유.
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

    _npc_imprints = domain_manager.get_npc_imprints(channel_id) if channel_id else {}
    # Voice quirks for dialogue directive merge (5-9)
    _voice_quirks = {}
    if channel_id:
        import npc_manager as _npc_mgr
        for _npc_n in (psyche_data or {}):
            _nd = _npc_mgr.get_npc(channel_id, _npc_n)
            if _nd and _nd.get("voice_card"):
                _vq = _extract_voice_quirks(_nd["voice_card"])
                if _vq:
                    _voice_quirks[_npc_n] = _vq
    _dialogue_dir = iceberg.compose_dialogue_directives(
        psyche_data, npc_knowledge,
        prev_gaze=_prev_gaze, npc_depths=npc_depths,
        npc_imprints=_npc_imprints,
        voice_quirks=_voice_quirks,
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

    # Flashback Scene Instruction (회상 확정 시 — 세부 정보 포함)
    if dai.get("flashback_confirmed"):
        fb_eval = dai.get("flashback_eval", {}) or {}
        fb_decl = dai.get("flashback_declaration", fb_eval.get("declaration", ""))
        fb_plaus = fb_eval.get("plausibility", "plausible")
        fb_tier = fb_eval.get("tier", "standard")
        fb_type = fb_eval.get("flashback_type", "standard")
        # 설득력에 따른 렌더링 힌트
        plaus_hint = ""
        if fb_plaus == "stretch":
            plaus_hint = " 가능하지만 의외다 — 의외성을 살려라."
        elif fb_plaus == "impossible":
            plaus_hint = " 무리한 선언이다 — 실패하거나 대가가 따른다."
        # 타입에 따른 방향
        type_hint = "소급 선언" if fb_type == "standard" else "사전 준비물 소환"
        fb_instruction = (
            f"\n[FLASHBACK] \"{fb_decl}\"\n"
            f"Type: {type_hint} | Weight: {fb_tier}.{plaus_hint}\n"
            "Render 2-3 sentences of memory, then return to present."
        )
        gm_mover = (gm_mover + fb_instruction) if gm_mover else fb_instruction

    # Rest/Downtime Scene Direction (휴식/다운타임 장면 렌더링 힌트)
    rest_eval = dai.get("rest_eval")
    if rest_eval and isinstance(rest_eval, dict) and rest_eval.get("detected"):
        _activity_kr = {
            "rest": "쉬는 중", "recover": "치료/회복 중", "vice": "탐닉 중",
            "train": "훈련 중", "socialize": "교류 중", "project": "작업 중",
        }
        _quality_kr = {"full": "충분한", "brief": "짧은", "interrupted": "방해받는"}
        r_activity = rest_eval.get("activity", "rest")
        r_quality = rest_eval.get("quality", "brief")
        r_target = rest_eval.get("target")
        r_safe = rest_eval.get("safe_location", True)
        rest_dir = f"\n[DOWNTIME] {_quality_kr.get(r_quality, r_quality)} {_activity_kr.get(r_activity, r_activity)}"
        if r_target:
            rest_dir += f" (대상: {r_target})"
        if not r_safe:
            rest_dir += " — 안전하지 않은 장소. 긴장을 유지하라."
        gm_mover = (gm_mover + rest_dir) if gm_mover else rest_dir

    # Idle Proactive Direction (유휴 입력 시 능동적 서사 전개 힌트)
    _sd = dai.get("story_direction", {})
    if isinstance(_sd, dict) and _sd.get("is_idle_input") and _sd.get("idle_direction"):
        _idle = _sd["idle_direction"]
        _idle_source = _idle.get("source", "ambient")
        _idle_hint = _idle.get("hint", "")
        _idle_npc = _idle.get("npc", "")
        _idle_parts = [f"[IDLE INPUT → PROACTIVE] Source: {_idle_source}"]
        if _idle_hint:
            _idle_parts.append(f"Hint: {_idle_hint}")
        if _idle_npc:
            _idle_parts.append(f"Focus NPC: {_idle_npc}")
        _idle_parts.append("유저가 능동적 입력을 하지 않았다 — 세계/NPC가 주도하여 장면을 전진시켜라.")
        _idle_dir = "\n".join(_idle_parts)
        gm_mover = (gm_mover + f"\n{_idle_dir}") if gm_mover else _idle_dir

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

    # Perception Type: 이상현상 인식 유형 (anomaly가 발생했을 때만 유의미)
    _anomaly_prof = dai.get("anomaly_profile", {})
    if _anomaly_prof and isinstance(_anomaly_prof, dict):
        _perc_type = _anomaly_prof.get("perception_type")
        if _perc_type and isinstance(_perc_type, str) and _perc_type.lower() != "null":
            _perc_hints = {
                "veridical": "실제 일어난 일이다 — 명확하게 묘사하라",
                "illusory": "감각이 왜곡되었다 — 혼란과 불일치를 섞어라",
                "hallucinatory": "자극 없는 지각이다 — 생생하지만 타인은 반응하지 않는다",
                "delusional": "확신에 찬 오해다 — 당사자에겐 절대적 진실이다",
            }
            _p_hint = _perc_hints.get(_perc_type.lower().strip(), "")
            if _p_hint:
                _perc_dir = f"\n[이상현상 인식] {_p_hint}"
                gm_mover = (gm_mover + _perc_dir) if gm_mover else _perc_dir

    # PROBE Mode: 탐침 입력 시 NPC 반응 지시 (H5)
    _input_mode = dai.get("input_mode", "decree")
    if _input_mode == "probe":
        _probe_dir = "\n[PROBE] 유저 입력 = 압력. NPC는 복종하지 않고 반응한다 — 인식/신체 기억/사회적 습관/환경을 통해."
        gm_mover = (gm_mover + _probe_dir) if gm_mover else _probe_dir

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

    # PC Autonomy Check — 사실 보고 기반
    # PC Autonomy: pc_spoke 제외 (유저 대사 재사용은 사칭 아님). pc_thought/pc_moved만 경고.
    pc_check = dai.get("pc_autonomy_check", {})
    if pc_check.get("pc_thought") or pc_check.get("pc_moved_unprompted"):
        flags = []
        if pc_check.get("pc_thought"): flags.append("PC inner thought present")
        if pc_check.get("pc_moved_unprompted"): flags.append("PC moved without player input")
        gm_focus = pc_check.get("gm_focus", "")
        pc_reminder = (
            f"\n\nPC_AUTONOMY_REMINDER:\n"
            f"- Flags: {', '.join(flags)}\n"
            f"- GM focus: {gm_focus}\n"
            f"- Rule: Narrate WORLD reactions only. PC dialogue/thoughts belong to the player."
        )
        real_time_data += pc_reminder

    # Emotion Intensity: iceberg 번역 (밴드명/수치 제거 → 행동 강도 힌트 + B4 페이싱)
    psyche_states_raw = dai.get("psyche_states", {})
    _prev_psyche_vals = {}
    if channel_id:
        try:
            _pf = domain_manager.get_latest_frame(channel_id)
            _prev_psyche_vals = _pf.get("dai_snapshot", {}).get("psyche_values", {})
        except Exception:
            pass
    intensity_text = iceberg.translate_emotion_intensity(psyche_states_raw, _prev_psyche_vals)
    if intensity_text:
        real_time_data += f"\n\n{intensity_text}"

    # Item Usage: 이번 턴 아이템 소비/획득 정보
    item_eval = dai.get("item_usage")
    if item_eval and isinstance(item_eval, dict):
        _consumed = item_eval.get("items_consumed", [])
        _gained = item_eval.get("items_gained", [])
        _item_parts = []
        if _consumed and isinstance(_consumed, list):
            _item_parts.append(f"소비: {', '.join(str(i) for i in _consumed)}")
        if _gained and isinstance(_gained, list):
            _item_parts.append(f"획득: {', '.join(str(i) for i in _gained)}")
        if _item_parts:
            _item_reason = item_eval.get("reason", "")
            _item_text = f"[아이템 변동] {' | '.join(_item_parts)}"
            if _item_reason:
                _item_text += f" ({_item_reason})"
            real_time_data += f"\n\n{_item_text}"

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

    last_response = ""
    smart_history = getattr(ctx, 'smart_history', [])

    if smart_history and isinstance(smart_history, list):
        # 직전 AI 응답 찾기 (마지막 assistant/model 메시지)
        for i in range(len(smart_history) - 1, -1, -1):
            role = smart_history[i].get('role', '').lower()
            if role in ('assistant', 'model'):
                last_response = smart_history[i].get('content', '')
                break

    # =========================================================
    # 3.5. POV — Camera Eye 고정 (전지적 모드 제거)

    # =========================================================
    # 3.6. 히스토리 사칭 정화
    # =========================================================
    # AI 이전 응답에 PC 사칭이 포함되면 다음 응답도 패턴을 답습함
    # → 프롬프트에 주입하기 전에 히스토리에서 사칭 문장을 선제 제거
    pc_name = getattr(ctx, 'user_mask', '') or ''
    _domain_data = getattr(ctx, 'domain_data', {}) or {}
    impersonation_enabled = _domain_data.get("settings", {}).get("impersonation_filter", True)
    if impersonation_enabled and pc_name and pc_name != 'Unknown':
        from response_processor import filter_pc_impersonation
        pc_names_list = [pc_name]
        if last_response:
            cleaned, violations = filter_pc_impersonation(last_response, pc_names_list)
            if violations:
                last_response = cleaned
                logger.info(f"[History Sanitize] last_response: {len(violations)} impersonation(s) removed")

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
        last_response=last_response,
        narrative_chain=narrative_chain,
        real_time_data=real_time_data,
        gm_mover=gm_mover,
        user_input=getattr(ctx, 'action_text', ''),
        author_note="",  # 장르/톤이 설정되어 있으면 자동으로 레거시 함수 사용
        telescope_prefill=""  # V2: Slot 34에 넣지 않음 — ctx를 통해 모델 프리필로 전달
    )

    # =========================================================
    # 4.5. Slot 33 일괄 조립 (Author Note + 모든 Recency 요소)
    # =========================================================
    slot33_parts = []

    # Base: Author Note / Genre Directive (populate_dynamic_slots에서 이미 설정됨)
    base_33 = builder.get_slot(33) or ""
    if base_33:
        slot33_parts.append(base_33)

    # Format Feedback (이전 턴 대사 포맷 위반 피드백)
    session_mem = domain_manager.get_session_ai_memory(channel_id) if channel_id else {}
    fmt_feedback = session_mem.get("format_feedback", "")
    if fmt_feedback:
        slot33_parts.append(fmt_feedback)
        logger.info("[FormatFeedback] Injected dialogue format correction into slot 33")

    # NPC Recency Echo — 프로필이 중간 슬롯에 묻히므로 핵심 제약 + 말투를 recency에 재주입
    if relevant_npcs and channel_id:
        import npc_manager as _npc_mgr_voice
        npc_reminder = _npc_mgr_voice.get_npc_recency_reminders(channel_id, relevant_npcs)
        if npc_reminder:
            slot33_parts.append(npc_reminder)
            logger.info(f"[RecencyEcho] NPC reminders injected into slot 33 ({len(relevant_npcs)} NPCs)")

    # Output Rules (!출력룰)
    if channel_id:
        _ws_out = domain_manager.get_world_state(channel_id)
        _out_rules = _ws_out.get("output_rules", {})
        if _out_rules:
            out_lines = []
            for k, v in _out_rules.items():
                desc = v.get("desc", "") if isinstance(v, dict) else str(v)
                out_lines.append(desc)
            out_block = "<Output_Format_Rules>\n[NOTE: These format blocks are OUTSIDE the prose token budget. Write full prose first, then append format blocks at the end.]\n" + "\n\n".join(out_lines) + "\n</Output_Format_Rules>"
            slot33_parts.append(out_block)
            logger.info(f"[OutputRules] {len(_out_rules)} output rules injected into slot 33")

    # Cognition Zone Recency Echo — Slot 13-17 Lost-in-the-Middle 방어
    _echo_parts = []
    if energy_hint:
        _echo_parts.append(energy_hint.split("\n")[0])  # 첫 줄만
    _active_flags = [k for k, v in (dai.get("quality_flags", {}) or {}).items() if v]
    if _active_flags:
        _echo_parts.append("flags=" + ",".join(_active_flags))
    if _echo_parts:
        slot33_parts.append(f"[Scene Echo] {' | '.join(_echo_parts)}")

    # 5W1H Recency Echo — always present at maximum recency position
    slot33_parts.append("[5W1H: Draw events only from DAI data. Camera scans environment evenly. Prose intensity follows EnergyDirection.]")

    builder.set_slot(33, "\n\n".join(slot33_parts))

    # OpenAI 백엔드: system(규칙) + context(데이터) 분리 빌드
    if _cfg.RENDERER_BACKEND == "openai":
        return builder.build_split()

    return builder.build()