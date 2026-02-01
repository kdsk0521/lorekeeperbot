"""
Lorekeeper TRPG Bot - Orchestration Context & Cognition Module
Handles Step 1 (Context Gathering) and Step 2 (Cognition Analysis).
Defines shared data structures for the orchestration layer.
"""

import logging
from typing import Dict, Any, Optional, List, Tuple
from dataclasses import dataclass, field
import fermentation
import domain_manager
import game_system
import game_character
from npc_manager import get_npc

logger = logging.getLogger("OrchContext")

# =========================================================
# Shared Data Structures
# =========================================================

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

    # NVC 분석 결과
    nvc_result: Dict[str, Any] = field(default_factory=dict)
    flash_result: Dict[str, Any] = field(default_factory=dict)
    pro_result: Dict[str, Any] = field(default_factory=dict)

    # 씬 타입 및 장르
    scene_type: str = "normal"
    active_genres: Any = None
    custom_tone: Optional[str] = None

    # 판정 결과
    judgment_context: str = ""

    # 발효된 요약
    fermented_summary_text: str = ""

    # NPC 태도
    existing_attitudes: Dict[str, Dict] = field(default_factory=dict)
    
    # Crisis Control
    is_crisis: bool = False
    crisis_reason: str = ""
    
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
    channel_id = ctx.channel_id

    # 기본 컨텍스트
    ctx.lore_txt = domain_manager.get_lore_with_npcs(channel_id)
    ctx.rule_txt = domain_manager.get_rules(channel_id)
    ctx.world_ctx = game_system.get_world_context(channel_id)
    ctx.obj_ctx = game_system.get_objective_context(channel_id)
    ctx.notebook_txt = game_system.get_notebook_text(channel_id)

    # 플레이어 패시브
    ctx.passives_txt = game_character.get_passives_for_context(ctx.player_data)

    # 히스토리 (스마트 컨텍스트 윈도우)
    ctx.hist_text = _build_smart_history(ctx)

    # 활성 퀘스트
    active_quests = game_system.get_quest_board(channel_id).get("active", [])
    ctx.quest_txt = " | ".join(active_quests) if active_quests else "None"

    # NPC 시간 힌트
    npc_hints = game_system.get_npc_time_progression(channel_id)
    if npc_hints:
        ctx.rule_txt += "\n\n### [NPC ACTIVITY HINTS (Time-based)]\n" + "\n".join(npc_hints)

    # 기존 NPC 태도
    ctx.existing_attitudes = domain_manager.get_npc_attitudes(channel_id)

    # 발효 요약
    fermented_summaries = [
        e["summary"] for e in ctx.domain_data.get("fermented_history", [])
        if e.get("summary")
    ]
    ctx.fermented_summary_text = "\n---\n".join(fermented_summaries)

    # 장르/톤
    ctx.active_genres = domain_manager.get_active_genres(channel_id)
    ctx.custom_tone = domain_manager.get_custom_tone(channel_id)

    return ctx


def _build_smart_history(ctx: ResponseContext) -> str:
    """스마트 컨텍스트 윈도우로 히스토리를 구성합니다."""
    all_hist = ctx.domain_data.get('history', [])
    target_len = 30000 # [Anti-Gravity] Expanded Context Window (1500 -> 30000)
    default_lines = getattr(fermentation, "RECENT_HISTORY_FOR_ANALYSIS", 20)
    slice_idx = -default_lines

    while True:
        if abs(slice_idx) > len(all_hist):
            slice_idx = -len(all_hist)

        subset = all_hist[slice_idx:]
        hist_text = "\n".join([f"{h['role']}: {h['content']}" for h in subset])

        if len(hist_text) >= target_len or abs(slice_idx) >= len(all_hist) or abs(slice_idx) >= 60:
            break

        slice_idx -= 5

    history = all_hist[slice_idx:]
    return "\n".join([f"{h['role']}: {h['content']}" for h in history]) + f"\nUser: {ctx.action_text}"


# =========================================================
# STEP 2: COGNITION ANALYSIS (NVC)
# =========================================================

async def run_cognition_analysis(
    gm_cognition, # Passed from service instance
    ctx: ResponseContext
) -> ResponseContext:
    """
    2단계 NVC 분석을 실행합니다.
    """
    # [Phase 1 Upgrade] Use GMCognition ReAct Loop
    # 1. Gather Inputs
    player_context_str = game_system.get_status_summary(ctx.player_data) if ctx.player_data else ""
    
    # 2. Execute ReAct Loop
    gm_result = await gm_cognition.process_turn(
        ctx.hist_text,
        ctx.lore_txt, 
        ctx.rule_txt, 
        ctx.quest_txt,
        player_context_str,
        ctx.action_text
    )
    
    # 3. Handle Crisis Halt
    if gm_result.get("type") == "CRISIS_HALT":
        ctx.is_crisis = True
        ctx.crisis_reason = gm_result.get("reason", "Unknown Crisis")
        return ctx
        
    # 4. Map Results (CONTINUE)
    ctx.flash_result = gm_result.get("observation", {})
    ctx.pro_result = gm_result.get("judgment", {})
    
    # Merge NVC Results
    ctx.nvc_result = {**ctx.flash_result, **ctx.pro_result}
    ctx.scene_type = ctx.nvc_result.get("SceneType", "normal")
    
    # Map Narrative Flow & Actors
    flow_plan = gm_result.get("flow_plan", {})
    if flow_plan:
        ctx.nvc_result["NarrativeFlow"] = flow_plan
        # GM Move injection if Narrative Plan suggests it
        if flow_plan.get("narrative_hook"):
             ctx.nvc_result["GMMove"] = {"type": "Narrative Hook", "description": flow_plan["narrative_hook"]}

    actors = gm_result.get("actors", [])
    if actors:
         ctx.nvc_result["IdentifiedActors"] = actors

    # Logging
    pos_data = ctx.nvc_result.get("Position", {})
    eff_data = ctx.nvc_result.get("Effect", {})
    
    logger.info(
        f"[GMCognition] Result: {gm_result.get('type')} | "
        f"Pos: {pos_data.get('value')} | Eff: {eff_data.get('value')} | "
        f"Crisis: {ctx.is_crisis}"
    )

    return ctx
