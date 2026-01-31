"""
Lorekeeper TRPG Bot - Orchestration Response Module
Handles Step 5 (Prompt Building) and Step 6 (Response Generation).
Uses context from Step 1 & 2 to generate the final AI output.
"""

import logging
import re
from typing import Tuple, Optional, Dict, List
from google.genai import types

import config
import persona
from orchestration_context import ResponseContext, NVCFilterConfig

logger = logging.getLogger("OrchResponse")

# =========================================================
# STEP 5: PROMPT BUILDING
# =========================================================

def build_prompt(
    ctx: ResponseContext,
    filter_config: NVCFilterConfig
) -> Tuple[str, persona.PromptBuilder]:
    """프롬프트를 구성합니다."""
    builder = persona.PromptBuilder()

    # NVC 요약 구성
    nvc_summary = _build_nvc_summary(ctx, filter_config)

    # 오프스크린 컨텍스트
    temporal = ctx.nvc_result.get("TemporalOrientation", {})
    offscreen_npcs = temporal.get("offscreen_npcs", [])
    offscreen_context = ""
    if offscreen_npcs:
        offscreen_context = (
            "### [OFFSCREEN WORLD]\n"
            "While this scene unfolds, elsewhere:\n"
            + "\n".join([f"- {npc}" for npc in offscreen_npcs])
            + "\n**Instruction:** Naturally weave 1-2 of these background events into the narrative.\n"
        )

    # 활성 스레드
    active_threads = temporal.get("active_threads", [])
    threads_context = ""
    if active_threads:
        threads_context = (
            "### [ACTIVE PLOT THREADS]\n"
            + "\n".join([f"- {thread}" for thread in active_threads])
            + "\n"
        )

    # 프롬프트 빌더 설정
    p_name = ctx.player_data.get("mask", "Unknown") if ctx.player_data else "Unknown"
    p_desc = ctx.player_data.get("ai_memory", {}).get("appearance", "") if ctx.player_data else ""

    builder.set_genres(ctx.active_genres)
    builder.set_custom_tone(ctx.custom_tone)
    builder.set_scene_type(ctx.scene_type)
    # [Context Diet / RAG Implementation]
    # Use Flash-extracted context instead of full lore if available
    relevant_context = ctx.nvc_result.get("RelevantContext", [])
    
    # Validation: Ensure it's a list and has content
    if isinstance(relevant_context, list) and relevant_context:
        logger.info(f"[Context Diet] Using {len(relevant_context)} extracted items. Full Lore bypassed.")
        
        # Build optimized lore block
        filtered_lore = (
            "### [RAG: FILTERED CONTEXT] (Full Lore Hidden for Efficiency)\n"
            "The following rules/lore are extracted as MOST RELEVANT for this turn:\n"
            + "\n".join([f"- {item}" for item in relevant_context])
            + "\n\n(Use this context faithfully. If information is missing, rely on General Logic.)"
        )
        # Pass filtered lore instead of full lore
        builder.set_lore(filtered_lore, ctx.rule_txt)
    else:
        # Fallback: Use full lore if extraction failed or is empty
        logger.warning("[Context Diet] No relevant context extracted. Falling back to Full Lore.")
        builder.set_lore(ctx.lore_txt, ctx.rule_txt)
    builder.set_player_info(p_name, p_desc)
    builder.set_roles(character_descriptions="")
    builder.set_fermented(ctx.fermented_summary_text, ctx.domain_data.get("deep_memory", ""))

    # 동적 섹션
    dynamic_world_state = f"{ctx.world_ctx}\n\n"
    if threads_context:
        dynamic_world_state += f"{threads_context}\n"
    if offscreen_context:
        dynamic_world_state += f"{offscreen_context}\n"

    builder.set_current_context(
        recent_chat="",
        world_state=dynamic_world_state,
        nvc_analysis=nvc_summary
    )

    # [Phase 2] Inject Psych Profile
    psych_profile = ctx.player_data.get("ai_memory", {}).get("psych_profile") if ctx.player_data else None
    builder.set_cognition_data(nvc_summary, psych_profile)
    pc_reminder = f"### CRITICAL WARNING: DO NOT WRITE FOR [{p_name}]\n{p_name} is the PLAYER. You must NOT generate their dialogue or actions."
    builder.set_user_message(material=ctx.action_text, ooc_content=pc_reminder)

    full_prompt = builder.build_dynamic_prompt()
    return full_prompt, builder


def _build_nvc_summary(ctx: ResponseContext, filter_config: NVCFilterConfig) -> str:
    """NVC 분석 요약을 구성합니다."""
    pos_data = ctx.nvc_result.get("Position", {})
    eff_data = ctx.nvc_result.get("Effect", {})
    aspects = ctx.nvc_result.get("Aspects", [])
    gm_m = ctx.nvc_result.get("GMMove", {})
    off_hint = ctx.nvc_result.get("OffscreenHint")

    nvc_summary = (
        f"### COGNITIVE ANALYSIS (IR#1 v3)\n"
        f"- **Observation**: {ctx.nvc_result.get('Observation')}\n"
        f"- **User Intent**: {ctx.nvc_result.get('UserIntent')}\n"
        f"- **Position (Risk/Stakes)**: {pos_data.get('value', 'N/A')} ({pos_data.get('reason', '')})\n"
        f"- **Effect (Potential)**: {eff_data.get('value', 'N/A')} ({eff_data.get('reason', '')})\n"
        f"- **Aspects**: {', '.join(aspects) if aspects else 'None'}\n"
    )

    # PC 사칭 자가 수정 경고 (Flash 분석에서 검출된 경우)
    pc_check = ctx.nvc_result.get("PCImpersonationCheck", {})
    if pc_check.get("detected"):
        violations = pc_check.get("violations", [])
        hint = pc_check.get("correction_hint", "")
        nvc_summary += (
            f"\n### ⚠️ PC IMPERSONATION SELF-CORRECTION WARNING\n"
            f"Previous AI responses contained PC impersonation violations:\n"
        )
        for v in violations[:3]:  # 최대 3개만 표시
            nvc_summary += f"- {v}\n"
        if hint:
            nvc_summary += f"\n**Correction Guidance**: {hint}\n"
        nvc_summary += "**CRITICAL**: Do NOT repeat these patterns. Write ONLY NPC/world responses.\n"

    if off_hint:
        nvc_summary += f"\n- **Offscreen Hint**: {off_hint}\n"

    if gm_m:
        nvc_summary += f"\n- **Proposed GM Move**: {gm_m.get('type')} ({gm_m.get('description', '')})\n"

    temporal = ctx.nvc_result.get("TemporalOrientation", {})
    suggested_focus = temporal.get("suggested_focus", "")
    nvc_summary += f"\nFocus: {suggested_focus}"

    # 유통기한 필터링된 NPC 태도
    filtered_attitudes = _filter_stale_nvc_data(ctx.existing_attitudes, filter_config)
    if filtered_attitudes:
        att_lines = [f"- {n}: {d['attitude']} ({d['reason']})" for n, d in filtered_attitudes.items()]
        nvc_summary += f"\n\n### [NPC ATTITUDES TOWARD PC]\n" + "\n".join(att_lines)

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
# STEP 6: RESPONSE GENERATION
# =========================================================

async def generate_response(
    client,
    model_id: str,
    ctx: ResponseContext,
    prompt: str,
    filter_config: NVCFilterConfig
) -> Optional[str]:
    """AI 응답을 생성합니다."""
    p_name = ctx.player_data.get("mask", "Unknown") if ctx.player_data else "Unknown"
    p_desc = ctx.player_data.get("ai_memory", {}).get("appearance", "") if ctx.player_data else ""

    session = persona.create_risu_style_session(
        client, model_id,
        ctx.lore_txt, ctx.rule_txt,
        ctx.active_genres, ctx.custom_tone,
        ctx.domain_data.get("deep_memory", ""),
        fermented_summary=ctx.fermented_summary_text,
        character_descriptions="",
        scene_type=ctx.scene_type,
        player_name=p_name,
        player_desc=p_desc,
        nvc_summary=_build_nvc_summary(ctx, filter_config)
    )

    # 히스토리 주입
    for h in ctx.domain_data.get('history', []):
        role = "user" if h['role'] == "User" else "model"
        session.history.append(types.Content(role=role, parts=[types.Part(text=str(h['content']))]))

    response = await persona.generate_response_with_retry(client, session, prompt)

    # 정리 (System Update & Telescope Logic Block)
    if response:
        # 1. system_update 블록 제거 (기존)
        response = re.sub(r'```system_update[\s\S]*?```', '', response, flags=re.IGNORECASE).strip()
        
        # 2. [Telescope] Hidden Logic Block 추출 및 로깅
        logic_match = re.search(r'(┣[\s\S]*?┫)', response)
        if logic_match:
            logic_content = logic_match.group(1)
            logger.info(f"\n[🔭 TELESCOPE LOGIC LAYER]\n{logic_content}\n[-----------------------]")
            # 사용자에게는 숨김 (제거)
            response = response.replace(logic_content, "").strip()

    # PC 사칭 필터
    if response:
        response, violations = persona.filter_pc_impersonation(response, [p_name])
        if violations:
            ctx.pc_impersonation_warnings = violations

    return response
