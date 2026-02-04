"""
Lorekeeper TRPG Bot - Orchestration Response Module
Handles Step 5 (Prompt Building) and Step 6 (Response Generation).
Uses context from Step 1 & 2 to generate the final AI output.

[V3 CONFIRMED] 34단계 슬롯 시스템이 기본 시스템으로 확정되었습니다.
"""

import logging
import re
from typing import Tuple, Optional, Dict, List, Any
from google.genai import types

import config
import persona
import domain_manager
from orchestration_context import ResponseContext, NVCFilterConfig

# [V3] 34단계 슬롯 시스템 (유일한 프롬프트 빌더)
import slot_manager

logger = logging.getLogger("OrchResponse")

# =========================================================
# STEP 5: PROMPT BUILDING (V3 - 34단계 슬롯 시스템)
# =========================================================

def build_prompt(
    ctx: ResponseContext,
    filter_config: NVCFilterConfig
) -> Tuple[str, None]:
    """
    34단계 슬롯 시스템 기반 프롬프트를 구성합니다.
    
    Returns:
        Tuple[str, None]: (프롬프트 문자열, None)
        두 번째 반환값은 레거시 호환성을 위해 유지되며 항상 None입니다.
    """
    logger.info("[V3] Building 34-Step Slot Prompt")
    v3_prompt = slot_manager.build_34_step_prompt(ctx)
    return v3_prompt, None


def _build_nvc_summary(ctx: ResponseContext, filter_config: NVCFilterConfig) -> str:
    """NVC 분석 요약을 구성합니다."""
    pos_data = ctx.nvc_result.get("Position", {})
    eff_data = ctx.nvc_result.get("Effect", {})
    aspects = ctx.nvc_result.get("Aspects", [])
    gm_m = ctx.nvc_result.get("GMMove", {})
    off_hint = ctx.nvc_result.get("OffscreenHint")

    # [V3 Restructured] Cognition Engine Data Block
    nvc_summary = "### <Cognition_Engine_Data>\n"
    
    # --- Section 1: Input Analysis ---
    input_analysis = ctx.nvc_result.get("InputAnalysis", {})
    if input_analysis:
        nvc_summary += (
            f"#### INPUT_ANALYSIS\n"
            f"- Original: {input_analysis.get('Original', 'N/A')}\n"
            f"- Enhanced: {input_analysis.get('Enhanced', 'N/A')}\n"
            f"- Plausibility: {input_analysis.get('Plausibility', 'N/A')}\n"
            f"- Momentum: {input_analysis.get('Momentum', 'OPEN')}\n\n"
        )
    
    # --- Section 2: Observation & Intent ---
    nvc_summary += (
        f"#### SITUATION_ASSESSMENT\n"
        f"- Observation: {ctx.nvc_result.get('Observation', 'N/A')}\n"
        f"- UserIntent: {ctx.nvc_result.get('UserIntent', 'N/A')}\n"
        f"- Position (Risk): {pos_data.get('value', 'N/A')} — {pos_data.get('reason', '')}\n"
        f"- Effect (Potential): {eff_data.get('value', 'N/A')} — {eff_data.get('reason', '')}\n"
        f"- Aspects: [{', '.join(aspects) if aspects else 'None'}]\n\n"
    )

    # --- Section 3: Psyche States (6-Axis) ---
    psyche = ctx.nvc_result.get("psyche_states")
    if psyche and isinstance(psyche, dict):
        nvc_summary += "#### PSYCHE_STATES (Use for body signal rendering)\n"
        for char_name, state in psyche.items():
            if isinstance(state, str):
                nvc_summary += f"- {char_name}: {state}\n"
            elif isinstance(state, dict):
                # Structured format
                mental = state.get("mental", {})
                soma = state.get("soma", {})
                relation = state.get("relation", {})
                nvc_summary += (
                    f"- {char_name}: "
                    f"Μ[{mental.get('descriptor', '?')}±{mental.get('value', 0)}] "
                    f"Φ[{soma.get('descriptor', '?')}] "
                    f"Ι[{relation.get('descriptor', '?')}±{relation.get('value', 0)}]\n"
                )
        nvc_summary += "\n"

    # --- Section 4: Narrative Chain ---
    chain = ctx.nvc_result.get("narrative_chain")
    if chain and isinstance(chain, dict):
        status = chain.get("chain_status", "OPEN")
        lock = chain.get("topic_lock", "None")
        conclusion = chain.get("conclusion_proximity", "N/A")
        nvc_summary += (
            f"#### NARRATIVE_CHAIN\n"
            f"- chain_status: {status}\n"
            f"- topic_lock: {lock}\n"
            f"- conclusion_proximity: {conclusion}\n\n"
        )

    # --- Section 5: Memory Triggers ---
    memory = ctx.nvc_result.get("memory_triggers")
    if memory and isinstance(memory, list) and memory:
        nvc_summary += "#### MEMORY_TRIGGERS (Render as involuntary recall)\n"
        for m in memory[:3]:  # 최대 3개
            if isinstance(m, dict):
                trigger = m.get("trigger", "")
                char = m.get("character", "")
                echo = m.get("echo", "")
                nvc_summary += f"- [{char}] Trigger: '{trigger}' → Echo: '{echo}'\n"
        nvc_summary += "\n"

    # --- Section 6: PC Impersonation Warning ---
    pc_check = ctx.nvc_result.get("PCImpersonationCheck", {})
    if pc_check.get("detected"):
        violations = pc_check.get("violations", [])
        hint = pc_check.get("correction_hint", "")
        nvc_summary += (
            f"#### ⚠️ PC_IMPERSONATION_WARNING\n"
            f"- detected: true\n"
            f"- violations: {violations[:3]}\n"
            f"- correction_hint: {hint}\n\n"
        )

    # --- Section 7: GM Move & Offscreen ---
    if gm_m:
        nvc_summary += f"#### GM_MOVE_SUGGESTION\n- type: {gm_m.get('type')}\n- description: {gm_m.get('description', '')}\n\n"

    temporal = ctx.nvc_result.get("TemporalOrientation", {})
    suggested_focus = temporal.get("suggested_focus", "")
    if suggested_focus:
        nvc_summary += f"#### TEMPORAL_FOCUS\n- suggested_focus: {suggested_focus}\n\n"
    
    if off_hint:
        nvc_summary += f"#### OFFSCREEN_HINT\n- {off_hint}\n\n"

    nvc_summary += "### </Cognition_Engine_Data>\n"

    # --- Legacy Section: NPC Attitudes (Outside Cognition Block) ---
    filtered_attitudes = _filter_stale_nvc_data(ctx.existing_attitudes, filter_config)
    if filtered_attitudes:
        att_lines = [f"- {n}: {d['attitude']} ({d['reason']})" for n, d in filtered_attitudes.items()]
        nvc_summary += f"\n### [NPC ATTITUDES TOWARD PC]\n" + "\n".join(att_lines)

    if ctx.judgment_context:
        nvc_summary += f"\n\n{ctx.judgment_context}"

    return nvc_summary


def _filter_stale_nvc_data(attitudes: Dict[str, Dict], filter_config: NVCFilterConfig) -> Dict[str, Dict]:
    """
    유통기한이 지난 NVC 정보를 필터링합니다.

    오래된 NPC 태도 정보를 제거하여 프롬프트 품질을 유지합니다.
    """
    if not filter_config.filter_stale_data:
        return attitudes

    import time
    from datetime import datetime
    filtered = {}
    
    # max_attitude_age_hours is relative to now
    max_age_seconds = filter_config.max_attitude_age_hours * 3600

    for npc_name, data in attitudes.items():
        last_updated = data.get("last_updated", "")

        # 시간 파싱
        if last_updated:
            try:
                update_time = datetime.strptime(last_updated, '%Y-%m-%d %H:%M')
                age_seconds = (datetime.now() - update_time).total_seconds()

                if age_seconds <= max_age_seconds:
                    filtered[npc_name] = data
                else:
                    logger.debug(f"Filtered stale NPC attitude: {npc_name} (age: {age_seconds/3600:.1f}h)")
            except (ValueError, TypeError):
                # 파싱 실패 시 유지
                filtered[npc_name] = data
        else:
            # last_updated 없으면 유지 (하위 호환성)
            filtered[npc_name] = data

    return filtered


# =========================================================
# STEP 6: RESPONSE GENERATION (V3 - 34단계 프롬프트 직접 사용)
# =========================================================

async def generate_response(
    client,
    model_id: str,
    ctx: ResponseContext,
    prompt: str,  # V3 34단계 프롬프트 (build_prompt()에서 생성됨)
    filter_config: NVCFilterConfig
) -> Optional[str]:
    """
    AI 응답을 생성합니다.
    
    [V3 Update]
    - prompt 파라미터를 직접 session에 주입
    - PromptBuilder를 통한 중복 생성 제거
    """
    p_name = ctx.player_data.get("mask", "Unknown") if ctx.player_data else "Unknown"

    # [V3] 이미 생성된 34단계 프롬프트를 직접 전달
    session = persona.create_risu_style_session(
        client=client,
        model_version=model_id,
        system_prompt=prompt  # ← V3 프롬프트 직접 주입!
    )

    # 히스토리 주입
    # [Anti-Gravity] Use Smart Context Window
    history_to_inject = ctx.smart_history if ctx.smart_history else ctx.domain_data.get('history', [])
    for h in history_to_inject:
        role = "user" if h['role'] == "User" else "model"
        session.history.append(types.Content(role=role, parts=[types.Part(text=str(h['content']))]))

    # [Anti-Gravity] PC 사칭 탐지 및 BKSPC 처리가 통합된 생성 함수 호출
    response = await persona.generate_response_with_retry(client, session, prompt, pc_names=[p_name])

    # 정리 (System Update & Telescope Logic Block)
    if response:
        # 1. system_update 블록 제거
        response = re.sub(r'```system_update[\s\S]*?```', '', response, flags=re.IGNORECASE).strip()
        
        # 2. [Telescope] Hidden Logic Block 추출 및 로깅
        logic_match = re.search(r'(┣[\s\S]*?┫)', response)
        if logic_match:
            logic_content = logic_match.group(1)
            logger.info(f"\n[🔭 TELESCOPE LOGIC LAYER]\n{logic_content}\n[-----------------------]")
            response = response.replace(logic_content, "").strip()

    # [Anti-Gravity] Mob Tag Cleaning (System Level)
    if response:
        from response_processor import clean_mob_tags
        response = clean_mob_tags(response)

    return response
