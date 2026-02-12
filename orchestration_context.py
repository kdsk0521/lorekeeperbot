"""
Lorekeeper TRPG Bot - Orchestration Context & Cognition Module
Handles Step 1 (Context Gathering) and Step 2 (Cognition Analysis).
Defines shared data structures for the orchestration layer.
"""

import logging
from typing import Dict, Any, Optional, List, Tuple
from dataclasses import dataclass, field

logger = logging.getLogger("OrchContext")

# =========================================================
# Shared Data Structures (UNE V4 Stateless)
# =========================================================

from dataclasses import asdict

@dataclass
class RequestData:
    user_input: str = ""
    genres: Dict[str, str] = field(default_factory=lambda: {"stage": "", "flavor": "", "lens": ""})
    active_modules: List[str] = field(default_factory=lambda: ["judgment", "doom", "anomaly", "mental"])
    lore_summary: Dict[str, Any] = field(default_factory=dict) # [V4] theme, anomaly_seeds, locations, rules, factions, key_events
    history_text: str = ""  # [V4] Recent history for THEORIA analysis
    lore_text: str = ""     # [V4] Lore reference (fallback)
    lore_chunks: List[Dict[str, Any]] = field(default_factory=list) # [V5] Labeled lore chunks for selective injection

@dataclass
class SharedBus:
    """
    Shared mutable state for UNE modules.
    Note: bus.*.active indicates a module ran/triggered this turn, not DLC enablement.
    """
    # dai: Theoria 분석 결과 전체 저장 (SharedBus 데이터 인터페이스)
    dai: Dict[str, Any] = field(default_factory=lambda: {
        "active": False,
        # Input Analysis
        "input_analysis": {},
        "observation": "",
        "user_intent": "",
        # Location & Scene
        "current_location": "",
        "location_risk": "Low",
        "time_context": "",
        "scene_type": "normal",
        # Stakes (BitD)
        "position": {},
        "effect": {},
        "aspects": [],
        # Judgment Support
        "needs_judgment": False,
        "action_meta": {},
        "asset_evaluation": {"bonus": 0, "penalty": 0, "reason": "", "modifications": [], "defense_success": False},
        # Psychological & Narrative
        "psyche_states": {},
        "narrative_chain": {},
        "memory_triggers": [],
        # DLC Support
        "narrative_hook": "",
        "time_flow": {},
        "doom_relief": {},
        "mental_impact": {},
        "anomaly_profile": {},
        # Safety & Debug
        "pc_impersonation_check": {},
        "temporal_orientation": {},
        "npc_attitudes": {},
        "relevant_context": []
    })
    judgment: Dict[str, Any] = field(default_factory=lambda: {
        "active": False, "success": False, "roll": 0, "dc": 0, 
        "modifications": [], "narrative_hook": ""
    })
    doom: Dict[str, Any] = field(default_factory=lambda: {"active": False, "value": 0, "delta": 0, "level": 0, "log": ""})
    anomaly: Dict[str, Any] = field(default_factory=lambda: {
        "active": False, "triggered": False, "potential": False, "narrative_hook": ""
    })
    vigor: Dict[str, Any] = field(default_factory=lambda: {
        "active": False, "value": 0, "delta": 0, "last_delta": 0,
        "adaptation": {}, "adaptation_update": {},
        "impact": {}, "rest_eval": None, "rest_log": "",
        "judgment_emotion": 0, "trauma_trigger": False, "log": ""
    })
    composure: Dict[str, Any] = field(default_factory=lambda: {
        "active": False, "value": 0, "delta": 0, "last_delta": 0,
        "impact": {},
        "judgment_emotion": 0, "trauma_trigger": False, "log": ""
    })

@dataclass
class GameContext:
    request: RequestData = field(default_factory=RequestData)
    narrative_anchors: Dict[str, Any] = field(default_factory=lambda: {
        "appearance": "", "personality": "", "background": "",
        "relations": [], "passives": [], "inventory": [], "memos": []
    })
    shared_bus: SharedBus = field(default_factory=SharedBus)

    def get_acting_mask(self) -> str:
        """현재 행동 중인 PC의 마스크명 반환."""
        anchors = self.narrative_anchors or {}
        acting_uid = anchors.get("acting_user_id", "")
        all_pcs = anchors.get("all_pcs", {})
        if acting_uid and acting_uid in all_pcs:
            return all_pcs[acting_uid].get("mask", "PC")
        return "PC"

    def to_dict(self) -> Dict[str, Any]:
        """직렬화용 사전 변환"""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'GameContext':
        """사전 데이터를 GameContext 객체로 복원"""
        req_data = RequestData(**data.get("request", {}))
        anchors = data.get("narrative_anchors", {})
        bus_data = data.get("shared_bus", {})
        
        # SharedBus mapping (handle Optionals)
        bus = SharedBus(
            dai=bus_data.get("dai", {}),
            judgment=bus_data.get("judgment", {}),
            doom=bus_data.get("doom", {}),
            anomaly=bus_data.get("anomaly", {}),
            vigor=bus_data.get("vigor", {}),
            composure=bus_data.get("composure", {}),
        )
        return cls(request=req_data, narrative_anchors=anchors, shared_bus=bus)

# Legacy Structures (To be deprecated)

@dataclass
class ResponseContext:
    """응답 생성에 필요한 컨텍스트 데이터"""
    channel_id: str
    user_id: str
    user_mask: str
    action_text: str

    # 도메인 데이터
    domain_data: Dict[str, Any] = field(default_factory=dict)
    player_data: Optional[Dict[str, Any]] = None

    # 컨텍스트 데이터
    lore_txt: str = ""
    rule_txt: str = ""
    world_ctx: str = ""
    obj_ctx: str = ""
    passives_txt: str = ""
    hist_text: str = ""
    notebook_txt: str = ""
    quest_txt: str = "" 
    # [Anti-Gravity] Sliced History List
    smart_history: List[Dict] = field(default_factory=list)

    # NVC 분석 결과
    # UNE Theoria output (shared_bus.dai)
    dai: Dict[str, Any] = field(default_factory=dict)

    # 씬 타입 및 장르
    scene_type: str = "normal"
    active_genres: Any = None
    custom_tone: Optional[str] = None

    # 판정 결과
    judgment_context: str = ""

    # 발효된 요약 (V3 Hybrid)
    fermented_summary_text: str = ""
    deep_memory_data: Dict[str, Any] = field(default_factory=dict)

    # NPC 태도
    existing_attitudes: Dict[str, Dict] = field(default_factory=dict)
    
    
    # PC Impersonation Tracking
    pc_impersonation_warnings: List[str] = field(default_factory=list)


@dataclass
class NVCFilterConfig:
    """NVC 정보 필터링 설정 (유통기한 관리)"""
    max_attitude_age_hours: int = 24  # NPC 태도 유효 시간
    max_observation_age_turns: int = 3  # 관찰 정보 유효 턴 수
    filter_stale_data: bool = True  # 오래된 데이터 필터링 활성화


# =========================================================
# STEP 1: CONTEXT GATHERING
# =========================================================

async def gather_context(ctx: ResponseContext) -> ResponseContext:
    """필요한 모든 컨텍스트 데이터를 수집합니다."""
    import domain_manager
    import game_system
    import game_character
    import fermentation

    channel_id = ctx.channel_id
    if not isinstance(ctx.domain_data, dict):
        logger.warning("[OrchContext] domain_data is not a dict; resetting to empty")
        ctx.domain_data = {}

    # 기본 컨텍스트
    ctx.lore_txt = domain_manager.get_lore_with_npcs(channel_id)
    ctx.rule_txt = domain_manager.get_rules(channel_id)
    ctx.world_ctx = game_system.get_world_context(channel_id)
    ctx.obj_ctx = game_system.get_objective_context(channel_id, ctx.user_id)
    ctx.notebook_txt = game_system.get_notebook_text(channel_id, ctx.user_id)

    # 플레이어 패시브
    ctx.passives_txt = game_character.get_passives_for_context(ctx.player_data)

    # 히스토리 (스마트 컨텍스트 윈도우)
    ctx.hist_text = _build_smart_history(ctx)

    # 활성 퀘스트
    active_quests = game_character.get_active_quests(channel_id)
    ctx.quest_txt = " | ".join(active_quests) if active_quests else "None"

    # NPC 시간 힌트
    npc_hints = game_system.get_npc_time_progression(channel_id)
    if npc_hints:
        ctx.rule_txt += "\n\n### [NPC ACTIVITY HINTS (Time-based)]\n" + "\n".join(npc_hints)

    # [Anti-Gravity] Inject World Context (Doom, Time) into Rules for Cognition
    if ctx.world_ctx:
        ctx.rule_txt += f"\n\n{ctx.world_ctx}"

    # 기존 NPC 태도
    ctx.existing_attitudes = domain_manager.get_npc_attitudes(channel_id)

    # 발효 요약 (V3 Hybrid - Mneme/Psyche)
    # build_fermented_context expects session_data dict, not separate args
    ctx.fermented_summary_text = fermentation.build_fermented_context(
        ctx.domain_data  # 전체 세션 데이터 전달
    )
    # Store deep_memory_data for prompt builder
    ctx.deep_memory_data = ctx.domain_data.get("deep_memory_data", {})

    # 장르/톤
    ctx.active_genres = domain_manager.get_active_genres(channel_id)
    ctx.custom_tone = domain_manager.get_custom_tone(channel_id)

    # [V4] Lore Summary Data (for UNE)
    ctx.domain_data["lore_summary_data"] = domain_manager.get_lore_summary_data(channel_id)

    return ctx


def _build_smart_history(ctx: ResponseContext) -> str:
    """스마트 컨텍스트 윈도우로 히스토리를 구성합니다."""
    import fermentation
    all_hist = ctx.domain_data.get('history', [])
    target_len = 100000 # [Anti-Gravity] Maximize Context (Targeting <200k Tokens)
    default_lines = getattr(fermentation, "RECENT_HISTORY_FOR_ANALYSIS", 30)
    slice_idx = -default_lines

    while True:
        if abs(slice_idx) > len(all_hist):
            slice_idx = -len(all_hist)

        subset = all_hist[slice_idx:]
        hist_text = "\n".join([f"{h['role']}: {h['content']}" for h in subset])

        if len(hist_text) >= target_len or abs(slice_idx) >= len(all_hist):
            break

        slice_idx -= 5

    history = all_hist[slice_idx:]
    ctx.smart_history = history # [Anti-Gravity] Store sliced list for response generation
    return "\n".join([f"{h['role']}: {h['content']}" for h in history])