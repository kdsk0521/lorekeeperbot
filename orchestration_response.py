"""
Lorekeeper TRPG Bot - Orchestration Response Module
Handles Step 5 (Prompt Building) and Step 6 (Response Generation).
Uses context from Step 1 & 2 to generate the final AI output.
"""

import logging
import re
from typing import Tuple, Optional, Dict, List, Any
from google.genai import types

import config
import persona
import domain_manager
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

    # Combined Player Info (Status + Passives + Desc)
    rich_player_info = domain_manager.get_unified_player_info(ctx.channel_id, ctx.user_id)
    builder.set_player_info(name="", desc=rich_player_info)

    builder.set_roles(character_descriptions="")
    # V3 Hybrid: Pass structured deep_memory_data
    deep_memory_str = ctx.domain_data.get("deep_memory", "")
    deep_data = getattr(ctx, 'deep_memory_data', {})
    
    # Inject active_memory_triggers into prompt if present
    if deep_data.get("active_memory_triggers"):
        triggers_str = "\n".join(f"- {t}" for t in deep_data["active_memory_triggers"])
        deep_memory_str = f"### [ACTIVE MEMORY TRIGGERS - Unresolved Narrative Hooks]\n{triggers_str}\n\n{deep_memory_str}"
    
    builder.set_fermented(ctx.fermented_summary_text, deep_memory_str)
    
    # [BUGFIX] Include conversation history in the prompt
    # 히스토리를 "Immediate" 섹션으로 프롬프트에 추가
    builder.set_immediate(ctx.hist_text)

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
    ai_mem_resp: Dict[str, Any] = ctx.player_data.get("ai_memory", {}) if ctx.player_data else {}
    psych_profile = ai_mem_resp.get("psych_profile")
    builder.set_cognition_data(nvc_summary, psych_profile)
    
    # [Restored] Define p_name for Reminder
    p_name = ctx.player_data.get("mask", "Unknown") if ctx.player_data else "Unknown"
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
    ai_mem_gen: Dict[str, Any] = ctx.player_data.get("ai_memory", {}) if ctx.player_data else {}
    p_desc = ai_mem_gen.get("appearance", "")

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
    # [Anti-Gravity] Use Smart Context Window
    history_to_inject = ctx.smart_history if ctx.smart_history else ctx.domain_data.get('history', [])
    for h in history_to_inject:
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
            
        # [Anti-Gravity] Mob Tag Cleaning (System Level)
        from response_processor import clean_mob_tags
        response = clean_mob_tags(response)

    return response
