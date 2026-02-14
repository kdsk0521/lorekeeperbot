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


def _get_dai(ctx: ResponseContext) -> Dict[str, Any]:
    """Return UNE Theoria shared_bus.dai (no legacy fallback)."""
    return getattr(ctx, "dai", None) or {}


_TELESCOPE_BLOCK_PATTERNS = (
    r"┣[\s\S]*?┫",
    r"<TELESCOPE>[\s\S]*?</TELESCOPE>",
    r"```telescope[\s\S]*?```",
    r"<<TELESCOPE[\s\S]*?TELESCOPE>>",
    r"<<[\s\S]*?>>",
)


def _extract_telescope_block(text: str) -> Optional[str]:
    if not text:
        return None
    # 10-gate telescope can exceed 2000 chars — search up to 5000
    head = text[:5000]
    for pattern in _TELESCOPE_BLOCK_PATTERNS:
        match = re.search(pattern, head, flags=re.IGNORECASE)
        if match:
            return match.group(0)
    return None


def parse_telescope(raw_response: str) -> Dict[str, Any]:
    """Parse a telescope gate block from model output."""
    block = _extract_telescope_block(raw_response or "")
    if not block:
        return {"parsed": False, "gates": {}, "fail_count": 0, "fails": []}

    gate_pattern = re.compile(
        r"\[([^\]]+)\]\s*(PASS|FAIL)\s*:\s*(.*?)(?=(?:\n\[[^\]]+\]\s*(?:PASS|FAIL)\s*:)|\Z)",
        flags=re.DOTALL | re.IGNORECASE,
    )
    gates: Dict[str, Dict[str, str]] = {}
    fails: List[str] = []

    for gate_name, result, evidence in gate_pattern.findall(block):
        normalized = re.sub(r"[^a-z0-9]+", "_", gate_name.strip().lower()).strip("_")
        verdict = result.strip().upper()
        gates[normalized] = {
            "result": verdict,
            "evidence": evidence.strip(),
        }
        if verdict == "FAIL":
            fails.append(normalized)

    return {
        "parsed": True,
        "gates": gates,
        "fail_count": len(fails),
        "fails": fails,
    }


def _check_length(text: str, limit: int) -> str:
    """응답이 limit의 130%를 초과할 때만 경고 로그. 원문은 항상 그대로 반환."""
    if not text:
        return text
    if len(text) > int(limit * 1.3):
        logger.warning("[Length Over] %d chars (limit %d, +%d%% over)", len(text), limit, int((len(text) - limit) / limit * 100))
    return text


def strip_telescope(raw_response: str) -> str:
    """Remove telescope gate block from model output."""
    if not raw_response:
        return ""
    block = _extract_telescope_block(raw_response)
    if not block:
        return raw_response.strip()
    return raw_response.replace(block, "", 1).strip()


def _build_nvc_summary(ctx: ResponseContext, filter_config: NVCFilterConfig) -> str:
    """NVC 분석 요약을 구성합니다."""
    dai = _get_dai(ctx)
    pos_data = dai.get("position", {})
    eff_data = dai.get("effect", {})
    aspects = dai.get("aspects", [])
    gm_m = dai.get("gm_move", {})

    # [V3 Restructured] Cognition Engine Data Block
    nvc_summary = "### <Cognition_Engine_Data>\n"

    # --- Section 1: Situation Assessment (Stakes) ---
    nvc_summary += (
        f"#### SITUATION_STAKES (Risk/Potential Calibration)\n"
        f"- Position (Risk): {pos_data.get('value', 'N/A')} -> {pos_data.get('reason', '')}\n"
        f"- Effect (Potential): {eff_data.get('value', 'N/A')} -> {eff_data.get('reason', '')}\n"
        f"- Observation: {dai.get('observation', 'N/A')}\n"
        f"- UserIntent: {dai.get('user_intent', 'N/A')}\n"
        f"- Aspects: [{', '.join(aspects) if aspects else 'None'}]\n\n"
    )

    # --- Section 2: Socio-Cultural Markers (Habitus) ---
    habitus = dai.get("habitus_analysis", dai.get("HabitusAnalysis", {}))
    if habitus:
        nvc_summary += (
            f"#### SOCIO_CULTURAL_MARKERS (Habitus Rendering)\n"
            f"- Economic: {habitus.get('Economic', 'N/A')}\n"
            f"- Cultural: {habitus.get('Cultural', 'N/A')}\n"
            f"- Social: {habitus.get('Social', 'N/A')}\n\n"
        )

    # --- Section 3: Physical Props (Sensory Anchors) ---
    anchors = dai.get("sensory_anchors", dai.get("SensoryAnchors", []))
    if anchors:
        nvc_summary += "#### PHYSICAL_PROPS_FOR_RECALL (Sensory Anchors)\n"
        for a in anchors[:2]:  # Top 2 anchors
            nvc_summary += f"- Anchor: '{a.get('anchor', '')}' -> Link: {a.get('memory_link', '')}\n"
        nvc_summary += "\n"

    # --- Section 4: Psyche States (4-Axis v2.0) ---
    psyche = dai.get("psyche_states")
    if psyche and isinstance(psyche, dict):
        nvc_summary += "#### PSYCHE_STATES (Body Signal Calibration)\n"
        for char_name, state in psyche.items():
            if isinstance(state, dict):
                psyche_ax = state.get("psyche", state.get("mental", {}))
                soma = state.get("soma", {})
                relation = state.get("relation", {})
                deep_read = state.get("deep_read", "")
                nvc_summary += (
                    f"- {char_name}: "
                    f"psyche={psyche_ax.get('descriptor', '?')} ({psyche_ax.get('value', 0)}), "
                    f"soma={soma.get('descriptor', '?')}, "
                    f"relation={relation.get('descriptor', '?')} ({relation.get('value', 0)})\n"
                )
                if deep_read:
                    nvc_summary += f"  deep_read: {deep_read}\n"
        nvc_summary += "\n"

    # --- Section 5: Narrative Chain & Direction ---
    chain = dai.get("narrative_chain", {})
    temporal = dai.get("temporal_orientation", dai.get("TemporalOrientation", {}))
    nvc_summary += (
        f"#### NARRATIVE_DIRECTION\n"
        f"- Chain Status: {chain.get('chain_status', 'OPEN')} (Conclusion Proximity: {chain.get('conclusion_proximity', 0)}%)\n"
        f"- Topic Lock: {chain.get('topic_lock', 'None')}\n"
        f"- Temporal Focus: {temporal.get('suggested_focus', temporal.get('focus', 'N/A'))}\n\n"
    )

    # --- Section 6: Security & Correction Hooks ---
    pc_check = dai.get("pc_impersonation_check", dai.get("PCImpersonationCheck", {}))
    if pc_check.get("detected"):
        nvc_summary += (
            f"#### [CRITICAL] SEC_CORRECTION_HINT\n"
            f"- Violation Detected: {pc_check.get('violations', [])}\n"
            f"- Required Correction: {pc_check.get('correction_hint', '')}\n\n"
        )

    # --- Section 7: GM Move ---
    if gm_m:
        nvc_summary += f"#### GM_MOVE_SUGGESTION\n- type: {gm_m.get('type')}\n- description: {gm_m.get('description', '')}\n\n"

    # --- Section 8: Memory Triggers ---
    memory = dai.get("memory_triggers")
    if memory and isinstance(memory, list) and memory:
        nvc_summary += "#### MEMORY_TRIGGERS (Render as involuntary recall)\n"
        for m in memory[:3]:  # 최대 3개
            if isinstance(m, dict):
                trigger = m.get("trigger", "")
                char = m.get("character", "")
                echo = m.get("echo", "")
                nvc_summary += f"- [{char}] Trigger: '{trigger}' -> Echo: '{echo}'\n"
        nvc_summary += "\n"

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
) -> Tuple[Optional[str], Optional[Dict[str, Any]]]:
    """
    AI 응답을 생성합니다.

    [V3 Update]
    - prompt 파라미터를 직접 session에 주입
    - PromptBuilder를 통한 중복 생성 제거

    [V4 Update - Inline Extraction]
    - Returns: (narrative_text, extraction_data)
    - extraction_data contains: notebook, quest, rel, flag
    """
    p_name = ctx.player_data.get("mask", "Unknown") if ctx.player_data else "Unknown"

    # 참여 인원 수 (동적 서사 길이 기준)
    participants = ctx.domain_data.get("participants", {})
    active_player_count = max(1, sum(1 for p in participants.values() if p.get("status") == "active"))

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
    # 사칭 감지 토글 확인 (기본값: 활성화)
    impersonation_enabled = ctx.domain_data.get("settings", {}).get("impersonation_filter", True)
    pc_names_for_filter = [p_name] if impersonation_enabled else []
    response = await persona.generate_response_with_retry(
        client, session, prompt,
        pc_names=pc_names_for_filter,
        player_count=active_player_count
    )

    # 정리 (System Update & Telescope Logic Block)
    extraction_data = None

    if response:
        # 1. system_update 블록 제거
        response = re.sub(r'```system_update[\s\S]*?```', '', response, flags=re.IGNORECASE).strip()

        # 2. [Telescope] ┣┫ CoT block strip (품질 게이트 출력 제거 — 플레이어에겐 비공개)
        telescope_data = parse_telescope(response)
        if telescope_data.get("parsed"):
            channel_id = getattr(ctx, "channel_id", "")
            if channel_id:
                try:
                    turn = int(domain_manager.get_world_state(channel_id).get("turn_index", 0))
                except Exception:
                    turn = 0
                domain_manager.save_telescope_log(channel_id, turn, telescope_data)
            response = strip_telescope(response)
            gates = telescope_data.get("gates", {})
            fails = telescope_data.get("fails", [])
            # 전체 게이트 상세 로그 (evidence 포함)
            for name, g in gates.items():
                verdict = g.get("result", "?")
                evidence = g.get("evidence", "").strip()
                tag = "FAIL" if verdict == "FAIL" else "OK"
                level = logger.warning if verdict == "FAIL" else logger.info
                level("[Telescope %s] %-15s %s", tag, name, evidence[:120] if evidence else "(no evidence)")
        # 3. [V4 Inline Extraction] SYS_EXTRACT 블록 파싱 및 제거
        extract_match = re.search(r'\[SYS_EXTRACT\]\s*(\{[\s\S]*?\})\s*\[/SYS_EXTRACT\]', response)
        if extract_match:
            try:
                import json
                extraction_data = json.loads(extract_match.group(1))
                logger.info(f"[📦 INLINE EXTRACTION] {extraction_data}")
            except json.JSONDecodeError as e:
                logger.warning(f"[EXTRACTION] JSON parse failed: {e}")
            # 블록 제거 (파싱 성공/실패 무관)
            response = re.sub(r'\[SYS_EXTRACT\][\s\S]*?\[/SYS_EXTRACT\]', '', response).strip()
            # [NARRATIVE] 태그도 제거
            response = re.sub(r'\[NARRATIVE\]\s*', '', response).strip()

    # [Anti-Gravity] Mob Tag Cleaning (System Level)
    if response:
        from response_processor import clean_mob_tags
        response = clean_mob_tags(response)

    # 4. 서사 길이 체크 (인원 기반 — 경고만, 강제 절단 없음)
    if response:
        char_limit = config.get_narrative_char_limit(active_player_count)
        response = _check_length(response, char_limit)

    return response, extraction_data
