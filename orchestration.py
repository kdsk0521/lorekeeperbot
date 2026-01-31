"""
Lorekeeper TRPG Bot - Orchestration Service
AI 응답 생성의 전체 흐름을 조율하는 오케스트레이션 서비스입니다.

main.py에서 분리된 generate_ai_response의 핵심 로직을 담당합니다.
각 단계(Context Gathering, Cognition, Prompt Building, Generation, Extraction)를
조율하고 실행합니다.
"""

import asyncio
import logging
import re
from typing import Dict, Any, Optional, List, Tuple
from dataclasses import dataclass, field
from google.genai import types

# 내부 모듈
import config
import bot_utils
import domain_manager
import game_system
import game_character
import game_world
import cognition
import persona
import fermentation
import npc_manager
import command_handler
import input_handler
from background_task_queue import enqueue_background_task, TaskPriority

logger = logging.getLogger("Orchestration")


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


@dataclass
class NVCFilterConfig:
    """NVC 정보 필터링 설정 (유통기한 관리)"""
    max_attitude_age_hours: int = 24  # NPC 태도 유효 시간
    max_observation_age_turns: int = 3  # 관찰 정보 유효 턴 수
    filter_stale_data: bool = True  # 오래된 데이터 필터링 활성화


class OrchestrationService:
    """
    AI 응답 생성 오케스트레이션 서비스.

    generate_ai_response의 책임을 명확히 분리:
    1. Context Gathering - 필요한 데이터 수집
    2. Cognition Analysis - NVC 분석 (Flash + Pro)
    3. World State Update - 상태 업데이트
    4. Prompt Building - 프롬프트 조립
    5. Response Generation - 응답 생성
    6. Background Extraction - 비동기 추출 (큐 시스템 사용)
    """

    def __init__(self, client_genai, model_id: str, model_id_flash: str):
        self.client = client_genai
        self.model_id = model_id
        self.model_id_flash = model_id_flash
        self.nvc_filter_config = NVCFilterConfig()

    # =========================================================
    # STEP 1: CONTEXT GATHERING
    # =========================================================
    async def gather_context(self, ctx: ResponseContext) -> ResponseContext:
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
        ctx.hist_text = self._build_smart_history(ctx)

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

    def _build_smart_history(self, ctx: ResponseContext) -> str:
        """스마트 컨텍스트 윈도우로 히스토리를 구성합니다."""
        all_hist = ctx.domain_data.get('history', [])
        target_len = 1500
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
        self,
        ctx: ResponseContext,
        previous_ai_response: Optional[str] = None
    ) -> ResponseContext:
        """
        2단계 NVC 분석을 실행합니다.

        Args:
            ctx: 응답 컨텍스트
            previous_ai_response: 이전 AI 응답 (PC 사칭 재확인용)
        """
        channel_id = ctx.channel_id

        # STEP 1: Flash Analysis (Observe & Select)
        # PC 사칭 재확인을 위한 이전 응답 전달
        ctx.flash_result = await cognition.analyze_context_flash(
            self.client, self.model_id_flash,
            ctx.hist_text, ctx.lore_txt, ctx.rule_txt, ctx.quest_txt,
            notebook=ctx.notebook_txt,
            player_context=game_system.get_status_summary(ctx.player_data) if ctx.player_data else "",
            existing_npc_attitudes=ctx.existing_attitudes
        )

        # Flash 결과 추출
        user_intent = ctx.flash_result.get("UserIntent", "Unknown")
        observation = ctx.flash_result.get("Observation", "No observation")
        relevant_context = ctx.flash_result.get("RelevantContext", [])

        # STEP 2: Pro Judgment (Judge Actions)
        ctx.pro_result = await cognition.judge_action_pro(
            self.client, self.model_id,
            user_intent, observation, relevant_context,
            history_tail=ctx.hist_text[-500:]
        )

        # 결과 병합
        ctx.nvc_result = {**ctx.flash_result, **ctx.pro_result}
        ctx.scene_type = ctx.nvc_result.get("SceneType", "normal")

        # 로깅
        pos_data = ctx.nvc_result.get("Position", {})
        eff_data = ctx.nvc_result.get("Effect", {})
        logger.info(
            f"[NVC] Position: {pos_data.get('value', 'N/A')} | "
            f"Effect: {eff_data.get('value', 'N/A')} | "
            f"Intent: {user_intent}"
        )

        return ctx

    # =========================================================
    # STEP 3: WORLD STATE UPDATE
    # =========================================================
    async def update_world_state(self, ctx: ResponseContext, message) -> Tuple[ResponseContext, List[str]]:
        """NVC 결과를 바탕으로 월드 상태를 업데이트합니다."""
        channel_id = ctx.channel_id
        messages = []

        # 위치 및 위험도 업데이트
        if ctx.nvc_result.get("CurrentLocation"):
            domain_manager.set_current_location(channel_id, ctx.nvc_result["CurrentLocation"])
        if ctx.nvc_result.get("LocationRisk"):
            domain_manager.set_current_risk(channel_id, ctx.nvc_result["LocationRisk"])

        # NPC 태도 업데이트
        new_attitudes = ctx.nvc_result.get("NPCAttitudes")
        if new_attitudes:
            for n_name, n_data in new_attitudes.items():
                existing_npc = npc_manager.get_npc(channel_id, n_name)
                if not existing_npc:
                    npc_manager.update_npc(channel_id, n_name, {
                        "source": "session",
                        "desc": "Auto-detected by AI",
                        "status": "active"
                    })
                    logger.info(f"Auto-created Session NPC: {n_name}")

                domain_manager.update_npc_attitude(
                    channel_id, n_name,
                    n_data.get("attitude", "neutral"),
                    n_data.get("reason", "")
                )

            ctx.existing_attitudes = domain_manager.get_npc_attitudes(channel_id)

        # 시간 흐름 처리
        time_flow = ctx.nvc_result.get("TimeFlow", {})
        time_msg = await self._process_time_flow(channel_id, time_flow)
        if time_msg:
            messages.append(time_msg)
            ctx.world_ctx = game_system.get_world_context(channel_id)

        return ctx, messages

    async def _process_time_flow(self, channel_id: str, time_flow: Dict) -> Optional[str]:
        """시간 흐름을 처리합니다."""
        if not time_flow:
            return None

        duration = time_flow.get("duration", "instant")
        ticks = time_flow.get("ticks", 0)
        explicit_hours = time_flow.get("explicit_hours")

        messages = []

        if duration == "explicit" and explicit_hours:
            ticks = int(explicit_hours * 5)

        if ticks <= 0:
            return None

        world = domain_manager.get_world_state(channel_id)
        current_ticks = world.get("time_ticks", 0)
        new_ticks_total = current_ticks + ticks

        # 둠 체크
        old_doom_period = current_ticks // 5
        new_doom_period = new_ticks_total // 5

        if new_doom_period > old_doom_period:
            for _ in range(new_doom_period - old_doom_period):
                game_world.process_doom_tick(channel_id)

        # 시간대 진행
        if new_ticks_total >= config.TIME_TICKS_PER_SLOT:
            slots_to_advance = new_ticks_total // config.TIME_TICKS_PER_SLOT
            remaining_ticks = new_ticks_total % config.TIME_TICKS_PER_SLOT

            world["time_ticks"] = remaining_ticks
            domain_manager.update_world_state(channel_id, world)

            for _ in range(slots_to_advance):
                msg = game_system.advance_time(channel_id)
                if msg:
                    messages.append(msg)
        else:
            world["time_ticks"] = new_ticks_total
            domain_manager.update_world_state(channel_id, world)

        return "\n".join(messages) if messages else None

    # =========================================================
    # STEP 4: ANOMALY & JUDGMENT PROCESSING
    # =========================================================
    async def process_anomaly_and_judgment(
        self,
        ctx: ResponseContext,
        message
    ) -> Tuple[ResponseContext, List[str]]:
        """이변 시스템과 판정을 처리합니다."""
        channel_id = ctx.channel_id
        messages = []

        w_state = domain_manager.get_world_state(channel_id)
        c_doom = w_state.get("doom", 0)

        # 이변 체크 (Summary/Intimate 제외)
        if ctx.scene_type not in ["summary", "intimate"] and game_world.should_trigger_anomaly(c_doom):
            anom_msgs = await self._process_anomaly(ctx, message, c_doom)
            messages.extend(anom_msgs)

        # GM 판정 처리
        judgment_msg = await self._process_judgment(ctx)
        if judgment_msg:
            messages.append(judgment_msg)

        return ctx, messages

    async def _process_anomaly(
        self,
        ctx: ResponseContext,
        message,
        c_doom: int
    ) -> List[str]:
        """이변 이벤트를 처리합니다."""
        channel_id = ctx.channel_id
        messages = []

        logger.info(f"[Anomaly] Triggered at Doom {c_doom}")

        anom_lore = domain_manager.get_event_lore_summary(channel_id) or domain_manager.get_lore(channel_id)[:1000]
        anom_loc = domain_manager.get_current_location(channel_id)
        anom_genres = ctx.domain_data.get("active_genres", ["Unknown"])

        anom_evt = await game_world.generate_anomaly_event(
            self.client, channel_id, c_doom, anom_lore, anom_loc, anom_genres,
            model_id=self.model_id_flash
        )

        if anom_evt:
            evt_msg = (
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"⚡ **이변 발생: [{anom_evt.get('tag', 'Unknown')}]**\n"
                f"{anom_evt.get('description', '...')}\n"
                f"💡 *{anom_evt.get('effect_hint', '대처하십시오.')}*\n"
                f"━━━━━━━━━━━━━━━━━━━━"
            )
            messages.append(evt_msg)

            game_world.change_doom(channel_id, config.ANOMALY_DOOM_COST)

            # 적응 판정
            participants = ctx.domain_data.get("participants", {})
            adapt_results = []

            for uid, p_data in participants.items():
                if p_data.get("status") == "active":
                    p_data, adapt_msg = game_character.check_adaptation_roll(
                        p_data,
                        tag=anom_evt.get('tag', 'Unknown'),
                        category=anom_evt.get('category')
                    )
                    domain_manager.save_participant_data(channel_id, uid, p_data)

                    user_name = p_data.get("mask") or p_data.get("name", "Unknown")
                    adapt_results.append(f"**{user_name}**: {adapt_msg.strip()}")

            if adapt_results:
                tag = anom_evt.get('tag', 'Unknown')
                adapt_msg = (
                    f"━━━━━━━━━━━━━━━━━━━━\n"
                    f"🎲 **적응 판정 결과: [{tag}]**\n" +
                    "\n".join(adapt_results) +
                    "\n━━━━━━━━━━━━━━━━━━━━"
                )
                messages.append(adapt_msg)

        return messages

    async def _process_judgment(self, ctx: ResponseContext) -> Optional[str]:
        """GM 판정을 처리합니다."""
        channel_id = ctx.channel_id
        action_judgment = ctx.nvc_result.get("ActionJudgment")

        if not action_judgment or not isinstance(action_judgment, dict):
            return None

        try:
            act = action_judgment.get("action", "Unknown Action")
            diff = action_judgment.get("difficulty", "normal")
            reason = action_judgment.get("difficulty_reason", "")
            mods = action_judgment.get("modifiers", [])

            b_dice = ctx.player_data.get("temp_bonus_dice", 0) if ctx.player_data else 0
            judgment_data = cognition.build_action_judgment_with_roll(act, diff, reason, mods, bonus_dice=b_dice)

            # 보너스 다이스 리셋
            if b_dice > 0 and ctx.player_data:
                ctx.player_data["temp_bonus_dice"] = 0
                domain_manager.save_participant_data(channel_id, ctx.user_id, ctx.player_data)

            # GM Move 추가
            gm_m = ctx.nvc_result.get("GMMove", {})
            judgment_data["potential_gm_move"] = gm_m.get("type")
            judgment_data["gm_move_description"] = gm_m.get("description")

            # Intimate 씬 치명적 실패 다운그레이드
            if ctx.scene_type == "intimate" and judgment_data.get("result") == "critical_failure":
                judgment_data["result"] = "failure"
                judgment_data["final_roll"] = max(2, judgment_data["final_roll"])
                logger.info("Downgraded Critical Failure due to Intimate Scene.")

            # 로그 구성
            roll_log = cognition.build_judgment_context_with_roll(judgment_data)

            # Doom 처리
            res_key = judgment_data.get("result")
            if res_key == "failure":
                game_world.change_doom(channel_id, 1)
            elif res_key == "critical_failure":
                game_world.change_doom(channel_id, 4)
            elif res_key == "critical_success":
                game_world.change_doom(channel_id, -1)
            elif res_key in ["success", "partial"]:
                if ctx.scene_type not in ["rest", "intimate"]:
                    game_world.change_doom(channel_id, config.DOOM_ACTION_TAX)

            ctx.judgment_context = roll_log
            return roll_log

        except Exception as e:
            logger.error(f"Failed to process judgment: {e}")
            return None

    # =========================================================
    # STEP 5: PROMPT BUILDING
    # =========================================================
    def build_prompt(self, ctx: ResponseContext) -> Tuple[str, persona.PromptBuilder]:
        """프롬프트를 구성합니다."""
        builder = persona.PromptBuilder()

        # NVC 요약 구성
        nvc_summary = self._build_nvc_summary(ctx)

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

        builder.set_cognition_data(nvc_summary)
        pc_reminder = f"### CRITICAL WARNING: DO NOT WRITE FOR [{p_name}]\n{p_name} is the PLAYER. You must NOT generate their dialogue or actions."
        builder.set_user_message(material=ctx.action_text, ooc_content=pc_reminder)

        full_prompt = builder.build_dynamic_prompt()
        return full_prompt, builder

    def _build_nvc_summary(self, ctx: ResponseContext) -> str:
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
        filtered_attitudes = self._filter_stale_nvc_data(ctx.existing_attitudes)
        if filtered_attitudes:
            att_lines = [f"- {n}: {d['attitude']} ({d['reason']})" for n, d in filtered_attitudes.items()]
            nvc_summary += f"\n\n### [NPC ATTITUDES TOWARD PC]\n" + "\n".join(att_lines)

        if ctx.judgment_context:
            nvc_summary += f"\n\n{ctx.judgment_context}"

        return nvc_summary

    def _filter_stale_nvc_data(self, attitudes: Dict[str, Dict]) -> Dict[str, Dict]:
        """
        유통기한이 지난 NVC 정보를 필터링합니다.

        오래된 NPC 태도 정보를 제거하여 프롬프트 품질을 유지합니다.
        """
        if not self.nvc_filter_config.filter_stale_data:
            return attitudes

        import time
        filtered = {}
        current_time = time.time()
        max_age_seconds = self.nvc_filter_config.max_attitude_age_hours * 3600

        for npc_name, data in attitudes.items():
            last_updated = data.get("last_updated", "")

            # 시간 파싱
            if last_updated:
                try:
                    from datetime import datetime
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
        self,
        ctx: ResponseContext,
        prompt: str
    ) -> Optional[str]:
        """AI 응답을 생성합니다."""
        p_name = ctx.player_data.get("mask", "Unknown") if ctx.player_data else "Unknown"
        p_desc = ctx.player_data.get("ai_memory", {}).get("appearance", "") if ctx.player_data else ""

        session = persona.create_risu_style_session(
            self.client, self.model_id,
            ctx.lore_txt, ctx.rule_txt,
            ctx.active_genres, ctx.custom_tone,
            ctx.domain_data.get("deep_memory", ""),
            fermented_summary=ctx.fermented_summary_text,
            character_descriptions="",
            scene_type=ctx.scene_type,
            player_name=p_name,
            player_desc=p_desc,
            nvc_summary=self._build_nvc_summary(ctx)
        )

        # 히스토리 주입
        for h in ctx.domain_data.get('history', []):
            role = "user" if h['role'] == "User" else "model"
            session.history.append(types.Content(role=role, parts=[types.Part(text=str(h['content']))]))

        response = await persona.generate_response_with_retry(self.client, session, prompt)

        # 정리
        if response:
            response = re.sub(r'```system_update[\s\S]*?```', '', response, flags=re.IGNORECASE).strip()

        # PC 사칭 필터
        if response:
            response, violations = persona.filter_pc_impersonation(response, [p_name])
            if violations:
                ctx.pc_impersonation_warnings = violations

        return response

    # =========================================================
    # STEP 7: BACKGROUND EXTRACTION (Queue-based)
    # =========================================================
    async def schedule_background_extraction(
        self,
        ctx: ResponseContext,
        response: str,
        message
    ) -> None:
        """
        백그라운드 추출 작업을 큐에 예약합니다.
        채널별 순차 실행이 보장됩니다.
        """
        channel_id = ctx.channel_id

        # 힌트 생성 휴리스틱
        extraction_hints = {
            "physical": any(kw in response for kw in [
                '아이템', '골드', '금화', '은화', '돈', '획득', '주웠', '얻었',
                '잃었', '버렸', '사용', '먹었', '마셨', '부상', '치료', '회복', '피해'
            ]),
            "social": (
                list(domain_manager.get_npcs(channel_id).keys()) and
                any(n in response for n in domain_manager.get_npcs(channel_id).keys())
            ) or ('"' in response or '「' in response),
            "narrative": any(kw in response for kw in [
                '처음으로', '마침내', '성공', '실패', '죽', '살', '마법',
                '괴물', '이상한', '기이한'
            ]) or bool(ctx.nvc_result.get("AbnormalElements")),
            "quest": any(kw in response for kw in [
                '퀘스트', '임무', '목표', '의뢰', '부탁', '완료', '달성', '단서', '정보', '비밀'
            ])
        }

        # Phase 1: 즉시 노트북 업데이트 (높은 우선순위)
        if extraction_hints["physical"]:
            async def immediate_physical_update():
                try:
                    status = ctx.player_data.get("status_effects", []) if ctx.player_data else []
                    phys_res = await cognition._extract_physical(
                        self.client, self.model_id_flash,
                        ctx.action_text, response,
                        ctx.notebook_txt, status
                    )
                    if phys_res:
                        nb_upd = phys_res.get("notebook_update")
                        if nb_upd and nb_upd != ctx.notebook_txt:
                            game_system.update_notebook_text(channel_id, nb_upd)
                            await message.channel.send("📔 노트북 기록됨")
                except Exception as e:
                    logger.error(f"Immediate physical update error: {e}")

            await enqueue_background_task(
                channel_id,
                "ImmediatePhysicalUpdate",
                immediate_physical_update,
                priority=TaskPriority.HIGH
            )

        # Phase 2: 백그라운드 추출 (일반 우선순위)
        bg_hints = {k: v for k, v in extraction_hints.items() if k != "physical" and v}

        if bg_hints:
            # 클로저를 위한 컨텍스트 캡처
            captured_ctx = ctx
            captured_response = response
            captured_message = message
            captured_hints = bg_hints

            async def background_extraction_task():
                await self._execute_background_extraction(
                    captured_ctx, captured_response,
                    captured_message, captured_hints
                )

            await enqueue_background_task(
                channel_id,
                "BackgroundExtraction",
                background_extraction_task,
                priority=TaskPriority.NORMAL
            )

    async def _execute_background_extraction(
        self,
        ctx: ResponseContext,
        response: str,
        message,
        extraction_hints: Dict[str, bool]
    ) -> None:
        """실제 백그라운드 추출을 실행합니다."""
        channel_id = ctx.channel_id

        try:
            status = ctx.player_data.get("status_effects", []) if ctx.player_data else []
            mem = domain_manager.get_ai_memory(channel_id, ctx.user_id)
            rels = mem.get("relationships", {})
            passives = mem.get("passives", [])
            p_desc = ctx.player_data.get("desc", "") if ctx.player_data else ""

            bg_res = await cognition.extract_all_updates(
                self.client, self.model_id_flash,
                ctx.action_text, response,
                notebook=game_system.get_notebook_text(channel_id),
                current_status=status,
                current_relationships=rels,
                current_passives=passives,
                current_quests=game_system.get_active_quests(channel_id),
                lore_npc_names=list(domain_manager.get_npcs(channel_id).keys()),
                fermented_context=ctx.fermented_summary_text,
                extraction_hints=extraction_hints,
                player_context=p_desc
            )

            bg_msgs = []

            # 메모리 업데이트
            pmu = bg_res.get("PlayerMemoryUpdate")
            if pmu:
                if pmu.get("relationships"):
                    domain_manager.update_ai_memory(channel_id, ctx.user_id, {"relationships": pmu["relationships"]})
                    bg_msgs.append("💞 관계도")
                if pmu.get("passives"):
                    for p in pmu["passives"]:
                        domain_manager.add_to_ai_memory_list(channel_id, ctx.user_id, "passives", p)
                    bg_msgs.append(f"🏆 패시브: {len(pmu['passives'])}개")

            # 퀘스트 업데이트
            qu = bg_res.get("QuestUpdate")
            if qu:
                if qu.get("quest_add"):
                    for q in qu["quest_add"]:
                        game_system.add_quest(channel_id, q)
                        bg_msgs.append(f"🔥 New: {q}")
                if qu.get("quest_complete"):
                    for q in qu["quest_complete"]:
                        game_system.complete_quest(channel_id, q)
                        bg_msgs.append(f"✅ Done: {q}")

            # 이상 현상 처리
            if domain_manager.get_abnormal_mode(channel_id) and bg_res.get("AbnormalTrigger"):
                trigger_name = bg_res["AbnormalTrigger"]
                trigger_cat = bg_res.get("AbnormalCategory")

                fp_data = domain_manager.get_participant_data(channel_id, ctx.user_id)
                fp_data, p_msg = game_system.expose_to_abnormal(fp_data, trigger_name, category=trigger_cat)
                if p_msg:
                    bg_msgs.append(p_msg)
                    if "마스터리 달성" in p_msg:
                        game_world.change_doom(channel_id, -5)
                domain_manager.save_participant_data(channel_id, ctx.user_id, fp_data)

            if bg_msgs:
                await message.channel.send("📋 " + " | ".join(bg_msgs))

        except Exception as e:
            logger.error(f"Background Extraction Error: {e}")

    # =========================================================
    # MAIN ORCHESTRATION ENTRY POINT
    # =========================================================
    async def execute(
        self,
        message,
        channel_id: str,
        system_trigger: str = None
    ) -> Optional[str]:
        """
        전체 응답 생성 파이프라인을 실행합니다.

        Returns:
            생성된 응답 또는 None
        """
        if not self.client:
            await message.channel.send("⚠️ No AI Configured")
            return None

        domain_data = domain_manager.get_domain(channel_id)
        if not domain_data:
            return None

        # 입력 준비
        user_input = system_trigger if system_trigger else message.content
        if not system_trigger and message.attachments:
            for att in message.attachments:
                txt, err = await bot_utils.read_attachment_text(att)
                if err:
                    await message.channel.send(err)
                    return None
                if txt:
                    user_input += f"\n(Attach):\n{txt}"

        user_input = user_input.strip()
        if not user_input and not system_trigger:
            return None

        # 사용자 마스크 및 히스토리 로깅
        user_mask = "System"
        uid = str(message.author.id)
        if not system_trigger:
            user_mask = domain_manager.get_user_mask(channel_id, message.author.id)
            domain_manager.append_history(channel_id, user_mask, user_input)
            domain_manager.update_participant(channel_id, message.author)

        # 액션 텍스트 포맷
        parsed = input_handler.parse_input(user_input) if not system_trigger else {'content': user_input, 'style': {}}
        if system_trigger:
            action_text = system_trigger
        else:
            style = parsed.get('style', 'Description')
            content = parsed['content'] if parsed else user_input
            if style == 'Dialogue':
                action_text = f"[{user_mask}] says: {content}"
            elif style == 'Action':
                action_text = f"[{user_mask}] does: {content}"
            else:
                action_text = f"[{user_mask}]: {content}"

        # 컨텍스트 초기화
        player_data = domain_manager.get_participant_data(channel_id, uid)
        ctx = ResponseContext(
            channel_id=channel_id,
            user_id=uid,
            user_mask=user_mask,
            action_text=action_text,
            domain_data=domain_data,
            player_data=player_data
        )

        async with message.channel.typing():
            try:
                # STEP 1: 컨텍스트 수집
                ctx = await self.gather_context(ctx)

                # STEP 2: NVC 분석
                ctx = await self.run_cognition_analysis(ctx)

                # STEP 3: 월드 상태 업데이트
                ctx, state_msgs = await self.update_world_state(ctx, message)
                for msg in state_msgs:
                    await message.channel.send(msg)

                # STEP 4: 이변 및 판정 처리
                ctx, event_msgs = await self.process_anomaly_and_judgment(ctx, message)
                for msg in event_msgs:
                    await message.channel.send(msg)

                # 시스템 액션 처리
                sys_action = ctx.nvc_result.get("SystemAction")
                if sys_action:
                    auto_msg = await command_handler.process_ai_system_action(channel_id, sys_action)
                    if auto_msg:
                        await message.channel.send(f"🤖 {auto_msg}")

                # STEP 5: 프롬프트 빌드
                full_prompt, builder = self.build_prompt(ctx)

                # STEP 6: 응답 생성
                response = await self.generate_response(ctx, full_prompt)

                if response:
                    # PC 사칭 경고
                    if hasattr(ctx, 'pc_impersonation_warnings') and ctx.pc_impersonation_warnings:
                        warning_msg = "\n".join(ctx.pc_impersonation_warnings)
                        await message.channel.send(warning_msg)

                    # 응답 전송
                    await bot_utils.send_long_message(message.channel, response)

                    # 히스토리 기록
                    domain_manager.append_history(channel_id, "User", action_text)
                    domain_manager.append_history(channel_id, "Char", response)

                    # STEP 7: 백그라운드 추출 (큐 기반)
                    await self.schedule_background_extraction(ctx, response, message)

                return response

            except Exception as e:
                logger.error(f"Orchestration Error: {e}", exc_info=True)
                await message.channel.send(f"⚠️ Error: {e}")
                return None


# 전역 인스턴스 생성 헬퍼
_orchestration_service: Optional[OrchestrationService] = None


def get_orchestration_service(client_genai, model_id: str, model_id_flash: str) -> OrchestrationService:
    """오케스트레이션 서비스 인스턴스를 반환합니다."""
    global _orchestration_service
    if _orchestration_service is None:
        _orchestration_service = OrchestrationService(client_genai, model_id, model_id_flash)
    return _orchestration_service
