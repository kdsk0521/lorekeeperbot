"""
Lorekeeper TRPG Bot - Orchestration Service
AI 응답 생성의 전체 흐름을 조율하는 오케스트레이션 서비스입니다.

[Phase 4 Refactor]
Logic split into:
- orchestration_context.py (Input/Analysis)
- orchestration_response.py (Output/Generation)
- game_system.py (Mechanics/Rules)
"""

import asyncio
import copy
import logging
import re
import time
from typing import Dict, Any, Optional, List, Tuple

import discord

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
import input_handler
from background_task_queue import enqueue_background_task, TaskPriority

# [Phase 4] Split Modules
import orchestration_context as orch_ctx
import orchestration_response as orch_res
from orchestration_context import ResponseContext

logger = logging.getLogger("Orchestration")


def _check_dialogue_format(response: str, pc_name: str = "", user_input: str = "") -> str:
    """AI 응답에서 대사 포맷 위반 + PC 대사 창작을 감지하여 피드백 문자열 반환."""
    lines = response.split('\n')
    correct_pat = re.compile(r'^\s*\S+\s*:\s*"')  # 이름: "대사"
    quote_pat = re.compile(r'"[^"]{2,}"')  # 2자 이상 쌍따옴표 텍스트
    # 판정/시스템 메시지 제외
    system_pat = re.compile(r'^\s*(?:🎲|📈|📉|🧠|⚠️|✅|❌|✨|🟠|🆕|🌿)')

    violations = []
    for line in lines:
        stripped = line.strip()
        if not stripped or system_pat.match(stripped):
            continue
        if quote_pat.search(stripped) and not correct_pat.match(stripped):
            violations.append(stripped[:40])

    parts = []
    if violations:
        examples = violations[:2]
        parts.append(f"[FORMAT] 대사 포맷 위반 {len(violations)}건. 예: {'; '.join(examples)}. 반드시 이름: \"대사\" 형식을 지켜라.")

    # PC 대사 창작 감지: PC이름: "대사" 가 응답에 있는데 유저 입력에 해당 대사 내용이 없으면 사칭
    if pc_name:
        pc_dialogue_pat = re.compile(rf'^\s*{re.escape(pc_name)}\s*:\s*"([^"]+)"')
        invented = []
        for line in lines:
            m = pc_dialogue_pat.match(line.strip())
            if m:
                dialogue_text = m.group(1)
                # 유저 입력에 대사 핵심 내용이 포함되어 있는지 체크 (5자 이상 연속 매칭)
                if user_input and len(dialogue_text) >= 5:
                    # 유저 입력에서 대사 내용의 일부(5자 이상)가 있으면 리워딩으로 간주
                    found = False
                    for i in range(len(dialogue_text) - 4):
                        if dialogue_text[i:i+5] in user_input:
                            found = True
                            break
                    if not found:
                        invented.append(dialogue_text[:30])
                elif not user_input:
                    # 유저 입력 없이 PC 대사 생성 = 확실한 사칭
                    invented.append(dialogue_text[:30])
        if invented:
            logger.warning(f"[Impersonation] PC 대사 창작 감지 {len(invented)}건: {invented}")
            parts.append(f"[IMPERSONATION] PC({pc_name}) 대사를 창작하지 마라. 유저가 제공한 대사만 리워딩 허용. 위반 {len(invented)}건.")

    return " ".join(parts)


class OrchestrationService:
    """
    AI 응답 생성 오케스트레이션 서비스.
    
    Acts as a Coordinator (Facade) for:
    1. Context Gathering (orchestration_context)
    2. Cognition Analysis (orchestration_context)
    3. World State Update (game_system / domain_manager)
    4. Anomaly & Judgment (game_system)
    5. Response Generation (orchestration_response)
    6. Background Extraction (Local Logic)
    """

    def __init__(self, client_genai, model_id: str, model_id_flash: str):
        self.client = client_genai
        self.model_id = model_id
        self.model_id_flash = model_id_flash
        self.nvc_filter_config = orch_ctx.NVCFilterConfig()

        # [UNE] Universal Narrative Engine 통합 엔진
        from une_facade import UniversalNarrativeEngine
        self.une = UniversalNarrativeEngine(client_genai, model_id_flash)

        # [!다시] 채널별 도메인 스냅샷 (전체 상태 롤백용, 인메모리)
        self._retry_snapshots: Dict[str, Dict[str, Any]] = {}

    # =========================================================
    # STEP 1: CONTEXT GATHERING
    # =========================================================
    async def gather_context(self, ctx: ResponseContext) -> ResponseContext:
        """??? ?? ???? ???? ?????. (Delegated)"""
        return await orch_ctx.gather_context(ctx)

    # STEP 2: COGNITION ANALYSIS (UNE Theoria 실행)
    # ??? process_une_logic?? UNE Pipeline?? ?????.

    # =========================================================
    # STEP 3: WORLD STATE UPDATE
    # =========================================================
    async def update_world_state(self, ctx: ResponseContext, message: discord.Message) -> Tuple[ResponseContext, List[str]]:
        """NVC ??? ???? ?? ??? ???????."""
        channel_id = ctx.channel_id
        messages = []

        dai = ctx.dai or {}
        current_location = dai.get("current_location")
        location_risk = dai.get("location_risk")
        if current_location:
            domain_manager.set_current_location(channel_id, current_location)
        if location_risk:
            domain_manager.set_current_risk(channel_id, location_risk)

        # NPC ?? ????
        new_attitudes = dai.get("npc_attitudes")
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

            # NPC Connection: trajectory → depth delta
            import random as _rng
            for n_name, n_data in new_attitudes.items():
                trajectory = n_data.get("trajectory", "stable")
                depth_range = config.NPC_TRAJECTORY_DEPTH_MAP.get(trajectory, (0, 0))
                if depth_range != (0, 0):
                    depth_delta = _rng.randint(min(depth_range), max(depth_range))
                    if depth_delta != 0:
                        domain_manager.update_helena_metric(channel_id, n_name, depth_delta=depth_delta)

            ctx.existing_attitudes = domain_manager.get_npc_attitudes(channel_id)

        # NPC Knowledge 영속화
        new_knowledge = dai.get("npc_knowledge")
        if new_knowledge and isinstance(new_knowledge, dict):
            for npc_name, k_data in new_knowledge.items():
                if isinstance(k_data, dict) and k_data.get("knows"):
                    domain_manager.update_npc_knowledge(channel_id, npc_name, k_data)
            logger.info(f"[NPC Knowledge] Persisted for {len(new_knowledge)} NPCs")

        # 장면 전환 exit_ticks 처리
        curr_scene = ctx.scene_type or "normal"
        world = domain_manager.get_world_state(channel_id)
        prev_scene = world.get("current_scene_type", "normal")
        if prev_scene != curr_scene:
            exit_rules = config.SCENE_TIME_RULES.get(prev_scene, {})
            exit_ticks = exit_rules.get("exit_ticks", 0)
            if exit_ticks > 0:
                exit_msg = await game_system.process_time_flow(
                    channel_id, {"ticks": exit_ticks, "reason": f"{prev_scene} 장면 종료"}, "normal")
                if exit_msg:
                    messages.append(exit_msg)
            world["current_scene_type"] = curr_scene
            domain_manager.update_world_state(channel_id, world)

        # 시간 흐름 처리 (Delegated to GameSystem)
        time_flow = dai.get("time_flow", {})
        time_msg = await game_system.process_time_flow(channel_id, time_flow, curr_scene)
        if time_msg:
            messages.append(time_msg)
            ctx.world_ctx = game_system.get_world_context(channel_id)

        return ctx, messages

    async def process_une_logic(
        self,
        ctx: ResponseContext,
        message: discord.Message
    ) -> Tuple[ResponseContext, List[str], str]:
        """[UNE] 구형 판정/이변 로직을 대체하는 통합 로직 실행"""
        channel_id = ctx.channel_id
        user_id = ctx.user_id
        
        # UNE Run
        result = await self.une.run(channel_id, user_id, ctx.action_text)
        
        # Extract Results
        updated_context = result["game_context"]
        directive = result["directive"]
        system_log = result["system_message"]
        
        # [BRIDGE] Sync SharedBus.dai → ResponseContext.dai
        # UNE Theoria 분석 결과를 레거시 dai로 복사
        dai = updated_context.shared_bus.dai
        ctx.dai = dai

        # [Flashback] 회상 기력 차감 (vigor_composure 전에 직접 처리)
        fb_eval = dai.get("flashback_eval")
        if fb_eval and fb_eval.get("detected"):
            fb_msg = self._process_flashback(
                channel_id, updated_context.shared_bus, fb_eval
            )
            if fb_msg:
                system_log = (system_log or "") + f"\n{fb_msg}"

        # [Item Usage] 아이템 소비/획득 처리
        item_eval = dai.get("item_usage")
        if item_eval:
            item_msg = self._process_item_usage(
                channel_id, updated_context.narrative_anchors.get("acting_user_id", ""), item_eval
            )
            if item_msg:
                system_log = (system_log or "") + f"\n{item_msg}"

        # Scene Type 업데이트 (dai 우선)
        if dai.get("scene_type"):
            ctx.scene_type = dai["scene_type"]
        
        # Sync Context Back to ResponseContext for LLM
        ctx.judgment_context = directive # Inject UNE directives into prompt
        
        # We return system_log as a list of messages for Discord
        messages = [system_log] if system_log else []
        
        return ctx, messages, directive

    def _process_flashback(self, channel_id: str, bus, fb_eval: dict) -> Optional[str]:
        """회상 평가 → 기력 차감 + DAI 확정. Returns system message or None."""
        plausibility = fb_eval.get("plausibility", "plausible")
        tier = fb_eval.get("tier", "standard")
        declaration = fb_eval.get("declaration", "")
        dai = bus.dai

        # 불가능한 회상 → 거부
        if plausibility == "impossible":
            dai["flashback_confirmed"] = False
            domain_manager.clear_pending_flashback(channel_id)
            return f"❌ 회상 거부: {fb_eval.get('reason', '논리적 모순')}"

        cost = int(config.FLASHBACK_COST_TIERS.get(tier, 8))
        current_vigor = int(bus.vigor.get("value", 100))
        current_composure = int(bus.composure.get("value", 100))

        # v3: 2축 비율 차감 (한 축 부족분은 다른 축으로 전가)
        try:
            vigor_ratio = float(fb_eval.get("vigor_ratio", 1.0) or 0.0)
            composure_ratio = float(fb_eval.get("composure_ratio", 0.0) or 0.0)
        except (TypeError, ValueError):
            vigor_ratio, composure_ratio = 1.0, 0.0

        vigor_ratio = max(0.0, vigor_ratio)
        composure_ratio = max(0.0, composure_ratio)
        ratio_sum = vigor_ratio + composure_ratio
        if ratio_sum <= 0:
            vigor_ratio, composure_ratio = 1.0, 0.0
            ratio_sum = 1.0

        vigor_cost = int(round(cost * (vigor_ratio / ratio_sum)))
        composure_cost = cost - vigor_cost

        # 한 축 부족분 전가
        if current_vigor < vigor_cost:
            deficit = vigor_cost - current_vigor
            vigor_cost = current_vigor
            composure_cost += deficit
        if current_composure < composure_cost:
            deficit = composure_cost - current_composure
            composure_cost = current_composure
            vigor_cost += deficit

        # 양축 합이 비용을 감당 못하면 실패
        if (vigor_cost + composure_cost) < cost:
            dai["flashback_confirmed"] = False
            domain_manager.clear_pending_flashback(channel_id)
            return (
                f"❌ 회상 불가: 자원 부족 "
                f"(기력 {current_vigor}, 평정 {current_composure}, 비용 {cost})"
            )

        new_vigor = max(0, current_vigor - vigor_cost)
        new_composure = max(0, current_composure - composure_cost)
        bus.vigor["value"] = new_vigor
        bus.composure["value"] = new_composure
        dai["flashback_confirmed"] = True
        dai["flashback_declaration"] = declaration
        domain_manager.clear_pending_flashback(channel_id)

        passive_note = ""
        relevant_passive = fb_eval.get("relevant_passive")
        if relevant_passive:
            passive_note = f" (면모 '{relevant_passive}' 활성화 → {tier})"

        return (
            f"🔮 회상 발동: {declaration}\n"
            f"⚡ 기력 -{vigor_cost} → {new_vigor}/100 | 평정 -{composure_cost} → {new_composure}/100 "
            f"[{tier}]{passive_note}"
        )

    def _process_item_usage(self, channel_id: str, user_id: str, item_eval: dict) -> Optional[str]:
        """아이템 소비/획득 처리. Returns system message or None."""
        consumed = item_eval.get("items_consumed", [])
        gained = item_eval.get("items_gained", [])
        reason = item_eval.get("reason", "")

        if not consumed and not gained:
            return None

        log_parts = []

        # 소비 처리: 노트북 + 구조화 인벤토리 양쪽에서 제거
        for item in consumed:
            if not item or not isinstance(item, str):
                continue
            result = game_character.remove_memo(channel_id, item.strip(), user_id)
            game_character.remove_inventory_item(channel_id, user_id, item.strip())
            if "⚠️" not in result:
                log_parts.append(f"📦 소비: {item.strip()}")

        # 획득 처리: 노트북 + 구조화 인벤토리 양쪽에 추가
        for item in gained:
            if not item or not isinstance(item, str):
                continue
            result = game_character.add_memo(channel_id, item.strip(), user_id)
            game_character.add_inventory_item(channel_id, user_id, item.strip())
            if "⚠️" not in result:
                log_parts.append(f"📥 획득: {item.strip()}")

        if not log_parts:
            return None

        msg = " | ".join(log_parts)
        if reason:
            msg += f" ({reason})"
        return msg

    # =========================================================
    # STEP 5: PROMPT BUILDING (V3 - 34단계 슬롯 시스템)
    # =========================================================
    def build_prompt(self, ctx: ResponseContext) -> Tuple[str, None]:
        """프롬프트를 구성합니다. (V3 34단계 슬롯 시스템 사용)"""
        return orch_res.build_prompt(ctx, self.nvc_filter_config)

    # =========================================================
    # STEP 6: RESPONSE GENERATION
    # =========================================================
    async def generate_response(
        self,
        ctx: ResponseContext,
        prompt: str
    ) -> Optional[str]:
        """AI 응답을 생성합니다. (Delegated)"""
        return await orch_res.generate_response(
            self.client, self.model_id, 
            ctx, prompt, self.nvc_filter_config
        )

    # =========================================================
    # STEP 7A: INLINE EXTRACTION (V4 - No Extra API Call)
    # =========================================================
    async def _apply_inline_extraction(
        self,
        ctx: ResponseContext,
        extraction_data: Optional[Dict[str, Any]],
        message: discord.Message
    ) -> None:
        """
        [V4] 서사 응답에서 추출된 데이터를 즉시 적용합니다.
        별도 API 호출 없이 서사 생성 시 함께 추출된 데이터를 사용합니다.
        """
        if not extraction_data:
            return

        channel_id = ctx.channel_id
        notifications = []

        try:
            # 1. Notebook Update (per-user)
            notebook_delta = extraction_data.get("notebook")
            if notebook_delta and notebook_delta != "null":
                current_nb = game_system.get_notebook_text(channel_id, ctx.user_id) or ""
                from datetime import datetime
                timestamp = datetime.now().strftime("%m/%d %H:%M")
                updated_nb = f"{current_nb}\n[{timestamp}] {notebook_delta}".strip()
                game_system.update_notebook_text(channel_id, updated_nb, ctx.user_id)
                notifications.append("📔 노트북 기록됨")
                logger.info(f"[InlineExtract] Notebook: {notebook_delta}")

            # 2. Quest Updates
            quest_data = extraction_data.get("quest", {})
            if quest_data:
                for q in quest_data.get("add", []):
                    if q:
                        result = game_system.add_quest(channel_id, q)
                        if result and not result.startswith("⚠️"):
                            notifications.append(f"🆕 퀘스트: {q}")
                for q in quest_data.get("complete", []):
                    if q:
                        result = game_system.complete_quest(channel_id, q)
                        if result and not result.startswith("⚠️"):
                            notifications.append(f"✅ 완료: {q}")

            # 3. Relationship Updates
            rel_data = extraction_data.get("rel", {})
            if rel_data and isinstance(rel_data, dict):
                for npc_name, delta in rel_data.items():
                    if delta and delta != 0:
                        domain_manager.update_npc_relationship(
                            channel_id, ctx.user_id, npc_name, delta
                        )
                        logger.info(f"[InlineExtract] Relation: {npc_name} {delta:+d}")

            # 4. Anomaly Flag
            flag = extraction_data.get("flag")
            if flag and flag != "null":
                logger.info(f"[InlineExtract] Anomaly Flag: {flag}")
                # 이상현상 트리거는 로깅만 (실제 처리는 UNE에서)

            # Send notifications
            if notifications:
                await message.channel.send(" | ".join(notifications))

        except Exception as e:
            logger.error(f"[InlineExtract] Error applying extraction: {e}")

    # =========================================================
    # STEP 7B: BACKGROUND TASKS (V4 - Fermentation Only)
    # =========================================================
    async def schedule_background_tasks(
        self,
        ctx: ResponseContext,
        response: str,
        message: discord.Message
    ) -> None:
        """
        [V4] 백그라운드 작업 스케줄링 (발효만 수행).
        추출은 이제 Inline Extraction으로 처리됩니다.
        """
        channel_id = ctx.channel_id

        # Mnemosyne Fermentation (Low Priority)
        async def background_fermentation_task():
            try:
                fresh_data = domain_manager.get_domain(channel_id)

                def save_cb():
                    domain_manager.save_domain(channel_id, fresh_data)

                await fermentation.auto_ferment(
                    self.client, self.model_id,
                    fresh_data,
                    channel_id=channel_id,
                    save_callback=save_cb
                )
            except Exception as e:
                logger.error(f"[Orchestrator] Fermentation task error: {e}")

        await enqueue_background_task(
            channel_id,
            "BackgroundFermentation",
            background_fermentation_task,
            priority=TaskPriority.LOW
        )

    # =========================================================
    # STEP 7 (Legacy): BACKGROUND EXTRACTION (Queue-based)
    # =========================================================
    async def schedule_background_extraction(
        self,
        ctx: ResponseContext,
        response: str,
        message: discord.Message
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
            ]) or bool((ctx.dai or {}).get("abnormal_elements")),
            "quest": any(kw in response for kw in [
                '퀘스트', '임무', '목표', '의뢰', '부탁', '완료', '달성', '단서', '정보', '비밀'
            ]),
            "world_state": True  # Always run World State Updater (+1 Flash)
        }

        # Phase 1: 즉시 노트북 업데이트 (높은 우선순위)
        if extraction_hints["physical"]:
            async def immediate_physical_update():
                try:
                    status = game_character.get_status_effect_names(
                        ctx.player_data.get("status_effects", []) if ctx.player_data else []
                    )
                    phys_res = await cognition._extract_physical(
                        self.client, self.model_id_flash,
                        ctx.action_text, response,
                        ctx.notebook_txt, status
                    )
                    if phys_res:
                        nb_upd = phys_res.get("notebook_update")
                        if nb_upd and nb_upd != ctx.notebook_txt:
                            game_system.update_notebook_text(channel_id, nb_upd, ctx.user_id)
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
                # Note: This refers to self._execute_background_extraction 
                # which would be a loop or something. 
                # Wait, originally this was _execute_background_extraction in orchestration.py
                # This seems to be missing in my copy plan. I must ensure it exists.
                # It uses cognition.extract_all_updates. I should probably implement it here.
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

        # Phase 3: Mnemosyne Fermentation (Low Priority)
        async def background_fermentation_task():
            try:
                # Reload latest state to avoid race conditions
                fresh_data = domain_manager.get_domain(channel_id)
                
                # Pass a save callback that persists changes
                def save_cb():
                    domain_manager.save_domain(channel_id, fresh_data)

                await fermentation.auto_ferment(
                    self.client, self.model_id, 
                    fresh_data, 
                    channel_id=channel_id,
                    save_callback=save_cb
                )
            except Exception as e:
                logger.error(f"[Orchestrator] Fermentation task error: {e}")

        # Schedule fermentation
        await enqueue_background_task(
            channel_id,
            "BackgroundFermentation",
            background_fermentation_task,
            priority=TaskPriority.LOW
        )

    async def _execute_background_extraction(
        self,
        ctx: ResponseContext,
        response: str,
        message: discord.Message,
        hints: Dict[str, bool]
    ) -> None:
        """백그라운드 추출 실행 (실제 로직)"""
        channel_id = ctx.channel_id
        
        try:
            # Reload critical data to ensure we work on latest state
            # (Though extraction mainly produces updates, merging is handled by domain_manager)
            
            # Prepare extended context
            p_data_latest = domain_manager.get_participant_data(channel_id, ctx.user_id)
            status = game_character.get_status_effect_names(
                p_data_latest.get("status_effects", []) if p_data_latest else []
            )
            ai_mem: Dict[str, Any] = p_data_latest.get("ai_memory", {}) if p_data_latest else {}
            rels = ai_mem.get("relationships", {})
            # ... (Assume these getters exist or use ctx if acceptable)
            # Actually, getting fresh data is safer for background tasks running later.
            
            # For simplicity, we use what we have or simple lookups
            lore_npcs = list(npc_manager.get_lore_npc_names(channel_id))
            scene_npcs = list(npc_manager.get_scene_npc_names(channel_id))
            current_quests = game_system.get_active_quests(channel_id)
            
            session_memory = domain_manager.get_session_ai_memory(channel_id)
            # Fresh notebook reload (stale ctx 방지 — 배경 작업은 지연 실행될 수 있음)
            fresh_notebook = game_system.get_notebook_text(channel_id, ctx.user_id)

            updates = await cognition.extract_all_updates(
                self.client, self.model_id_flash,
                ctx.action_text, response,
                notebook=fresh_notebook,
                current_status=status,
                lore_npc_names=lore_npcs,
                scene_npc_names=scene_npcs,
                current_quests=current_quests,
                extraction_hints=hints,
                current_session_memory=session_memory
            )
            
            # Apply Updates (⚠️ 에러는 로그만, 성공만 Discord 출력)
            if updates.get("QuestUpdate"):
                qu = updates["QuestUpdate"]
                if qu.get("quest_add"):
                    for q in qu["quest_add"]:
                        if isinstance(q, dict):
                            result = game_system.add_quest(channel_id, q.get("content", ""), q.get("rank"))
                        else:
                            result = game_system.add_quest(channel_id, q)
                        if result and not result.startswith("⚠️"):
                            await message.channel.send(result)
                        elif result:
                            logger.debug(f"[Quest Auto] {result}")
                if qu.get("quest_complete"):
                    for q in qu["quest_complete"]:
                        result = game_system.complete_quest(channel_id, q)
                        if result and not result.startswith("⚠️"):
                            await message.channel.send(result)
                        elif result:
                            logger.debug(f"[Quest Auto] {result}")
                if qu.get("quest_progress") and isinstance(qu["quest_progress"], dict):
                    for quest_name, delta in qu["quest_progress"].items():
                        if delta and isinstance(delta, (int, float)) and delta != 0:
                            result = game_character.advance_quest_progress(channel_id, quest_name, int(delta))
                            if result and result.startswith("⚠️"):
                                # 매칭 실패 → 새 퀘스트로 자동 등록 후 진행
                                logger.info(f"[Quest Auto] Progress miss → auto-add: {quest_name}")
                                add_r = game_system.add_quest(channel_id, quest_name, "normal")
                                if add_r and not add_r.startswith("⚠️"):
                                    await message.channel.send(add_r)
                                result = game_character.advance_quest_progress(channel_id, quest_name, int(delta))
                            if result and not result.startswith("⚠️"):
                                await message.channel.send(result)

            # NPC Depth/Tension from cognition extraction
            npc_depth = updates.get("NPCDepthUpdate")
            if npc_depth and isinstance(npc_depth, dict):
                for npc_name, deltas in npc_depth.items():
                    if isinstance(deltas, dict):
                        d_d = deltas.get("depth_delta", 0)
                        t_d = deltas.get("tension_delta", 0)
                        if d_d or t_d:
                            domain_manager.update_helena_metric(channel_id, npc_name, depth_delta=int(d_d), tension_delta=int(t_d))

                # Convergence Detection
                convergence_warnings = []
                for npc_name, deltas in npc_depth.items():
                    if isinstance(deltas, dict):
                        d_d = deltas.get("depth_delta", 0)
                        if isinstance(d_d, (int, float)) and d_d > 15:
                            convergence_warnings.append(
                                f"[CONVERGENCE: {npc_name} depth_delta={d_d:+.0f} — match relationship progression speed to current Peplau phase]"
                            )
                if convergence_warnings:
                    existing_fb = domain_manager.get_session_ai_memory(channel_id).get("format_feedback", "")
                    conv_str = " ".join(convergence_warnings)
                    combined = f"{existing_fb} {conv_str}".strip() if existing_fb else conv_str
                    domain_manager.update_session_ai_memory(channel_id, {"format_feedback": combined})
                    logger.info(f"[Convergence] {conv_str}")

            if updates.get("PlayerMemoryUpdate"):
                pmu = updates["PlayerMemoryUpdate"]
                if pmu.get("relationships"):
                    for nm, val in pmu["relationships"].items():
                        new_rel = domain_manager.update_npc_relationship(channel_id, ctx.user_id, nm, val)
                # Passive merge (theory_links + modifiers 포함)
                if pmu.get("passives"):
                    for passive in pmu["passives"]:
                        if isinstance(passive, dict) and passive.get("name"):
                            domain_manager.add_to_ai_memory_list(
                                channel_id, ctx.user_id, "passives", passive
                            )
            
            # Abnormal Trigger logic
            if updates.get("AbnormalTrigger"):
                trigger = updates["AbnormalTrigger"]
                category = updates.get("AbnormalCategory")
                logger.info(f"[Background] Narrative Anomaly Detected: {trigger} ({category})")

            # World State Update (ai_session_memory 갱신)
            wsu = updates.get("WorldStateUpdate")
            if wsu and isinstance(wsu, dict):
                mem_updates = {}
                if wsu.get("active_threads"):
                    mem_updates["active_threads"] = wsu["active_threads"][:10]
                if wsu.get("resolved_threads"):
                    # Append to resolved list (keep last 20)
                    existing_resolved = session_memory.get("resolved_threads", [])
                    merged_resolved = existing_resolved + wsu["resolved_threads"]
                    mem_updates["resolved_threads"] = merged_resolved[-20:]
                if wsu.get("world_changes"):
                    existing_changes = session_memory.get("world_changes", [])
                    merged_changes = existing_changes + wsu["world_changes"]
                    mem_updates["world_changes"] = merged_changes[-15:]
                if wsu.get("npc_schedule_hints"):
                    existing_schedules = session_memory.get("npc_summaries", {})
                    existing_schedules.update(wsu["npc_schedule_hints"])
                    mem_updates["npc_summaries"] = existing_schedules
                if wsu.get("current_arc"):
                    mem_updates["current_arc"] = wsu["current_arc"]
                if wsu.get("basic_needs_flags") and isinstance(wsu["basic_needs_flags"], dict):
                    mem_updates["basic_needs_flags"] = wsu["basic_needs_flags"]
                if mem_updates:
                    domain_manager.update_session_ai_memory(channel_id, mem_updates)
                    logger.info(f"[WorldState] Updated session memory: {list(mem_updates.keys())}")

        except Exception as e:
            logger.error(f"Background Extraction Failed: {e}")


    # =========================================================
    # EXECUTION ENTRY POINT
    # =========================================================
    async def execute(
        self, 
        message: discord.Message, 
        channel_id: str, 
        system_trigger: Optional[str] = None, 
        feedback_msg: Optional[discord.Message] = None,
        user_input_override: Optional[str] = None
    ) -> None:
        """
        AI 응답 생성 파이프라인을 실행합니다.
        
        Args:
            feedback_msg: '서사 생성 중...' 안내 메시지 객체 (완료 후 삭제용)
        """
        try:
            user_id = str(message.author.id)
            user_input = user_input_override if user_input_override is not None else message.content
            
            # 0. 초기 컨텍스트 설정
            d_data = domain_manager.get_domain(channel_id)
            participants = d_data.get('participants', {})
            p_data = participants.get(user_id)
            
            # [Fix] Fallback for System Events or missing player data
            if not p_data and participants:
                # Pick any active participant for context if user is missing (e.g. Admin)
                for uid, pd in participants.items():
                    if pd.get("status") == "active":
                        p_data = pd
                        break
            
            # 시스템 트리거 처리
            action_text = f"[System Event] {system_trigger}" if system_trigger else user_input

            ctx = ResponseContext(
                channel_id=channel_id,
                user_id=user_id,
                user_mask=(p_data.get('mask') if p_data else 'Unknown') or 'Unknown',
                action_text=action_text,
                domain_data=d_data,
                player_data=p_data
            )

            # 1. Context Gathering
            ctx = await self.gather_context(ctx)

            # 2. Cognition Analysis (Theoria 수행)
            # 분석은 이제 process_une_logic 내부의 UNE Theoria에서 수행됩니다.


            # [!다시] 도메인 스냅샷 (UNE 실행 전 전체 상태 저장)
            self._retry_snapshots[channel_id] = {
                "_ts": time.time(),
                "_data": copy.deepcopy(domain_manager.get_domain(channel_id))
            }
            # 메모리 누수 방지: 최대 20개 채널 스냅샷만 유지
            if len(self._retry_snapshots) > 20:
                oldest = min(self._retry_snapshots, key=lambda k: self._retry_snapshots[k].get("_ts", 0))
                del self._retry_snapshots[oldest]

            # async output (typing indicator)
            async with message.channel.typing():
                # [!다시] 마지막 컨텍스트 초기화 (재생성 전)
                current_retry_ctx = {
                    "action_text": ctx.action_text,
                    "user_id": user_id,
                    "original_message_id": message.id,
                    "message_ids": [],
                    "has_response": False
                }

                # 4. UNE Integrated Logic (Batching Process)
                ctx, une_logs, une_directive = await self.process_une_logic(ctx, message)
                
                # Log messages (User Facing)
                if une_logs:
                    une_msg = await message.channel.send("\n".join(une_logs))
                    current_retry_ctx["message_ids"].append(une_msg.id)
                    current_retry_ctx["has_response"] = True # Mark as retryable even if only system logs exist

                # 4.5. World State Update (scene transition exit_ticks + time flow)
                ctx, world_msgs = await self.update_world_state(ctx, message)
                if world_msgs:
                    for wm in world_msgs:
                        if wm:
                            w_msg = await message.channel.send(wm)
                            current_retry_ctx["message_ids"].append(w_msg.id)
                            current_retry_ctx["has_response"] = True

                # 5. Prompt Building
                # UNE directive is already injected into ctx.judgment_context
                full_prompt, builder = self.build_prompt(ctx)

                # 6. Response Generation (V4: returns Tuple[response, extraction_data])
                # + Impersonation retry loop (max 3 retries)
                _MAX_IMP_RETRIES = 3
                response, extraction_data = await self.generate_response(ctx, full_prompt)

                _impersonation_retried = 0
                if response and ctx.user_mask:
                    for _imp_attempt in range(_MAX_IMP_RETRIES):
                        _imp_check = _check_dialogue_format(response, pc_name=ctx.user_mask, user_input=ctx.action_text or "")
                        if "[IMPERSONATION]" not in _imp_check:
                            break
                        _impersonation_retried += 1
                        logger.warning(f"[Impersonation Retry {_impersonation_retried}/{_MAX_IMP_RETRIES}] 사칭 감지 → 재생성")
                        domain_manager.update_session_ai_memory(channel_id, {"format_feedback": _imp_check})
                        response, extraction_data = await self.generate_response(ctx, full_prompt)

                if response:
                    # [UI Feedback] 완료 시 안내 메시지 삭제
                    if feedback_msg:
                        try:
                            await feedback_msg.delete()
                        except Exception:
                            pass # 이미 삭제되었거나 권한 부족 시 무시

                    # 7. Send Response
                    sent_msgs = await bot_utils.send_long_message(message.channel, response)

                    # Store message IDs for retry deletion
                    if sent_msgs:
                        current_retry_ctx["message_ids"].extend([m.id for m in sent_msgs])
                        current_retry_ctx["has_response"] = True

                    # 8. [IMPORTANT] 히스토리에 사용자 입력과 AI 응답 저장
                    user_mask = ctx.user_mask or "User"
                    domain_manager.append_history(channel_id, user_mask, ctx.action_text)
                    domain_manager.append_history(channel_id, "Model", response)
                    logger.debug(f"[History] Saved: {user_mask} + Model response ({len(response)} chars)")

                    # 8.5. Dialogue Format Feedback + PC Impersonation + Style Detectors (다음 턴 피드백용)
                    fmt_feedback = _check_dialogue_format(response, pc_name=ctx.user_mask or "", user_input=ctx.action_text or "")
                    from response_processor import (
                        detect_cliche_patterns, detect_cargo_patterns,
                        detect_premature_closure, detect_sensory_repetition, detect_pidgin_echo
                    )
                    cliche_fb = detect_cliche_patterns(response)
                    cargo_fb = detect_cargo_patterns(response)

                    # Closure Detection: narrative_chain에서 proximity/threads 가져옴
                    _mem_for_fb = domain_manager.get_session_ai_memory(channel_id)
                    _chain = _mem_for_fb.get("narrative_chain", {})
                    if isinstance(_chain, dict):
                        _prox = _chain.get("conclusion_proximity", 50)
                        try:
                            _prox = int(str(_prox).replace("%", ""))
                        except (ValueError, TypeError):
                            _prox = 50
                        _threads = _chain.get("open_threads", [])
                        if not isinstance(_threads, list):
                            _threads = []
                    else:
                        _prox, _threads = 50, []
                    closure_fb = detect_premature_closure(response, _prox, _threads)

                    # Sensory Rotation: rolling window 3턴
                    _recent_parts = _mem_for_fb.get("recent_body_parts", [])
                    if not isinstance(_recent_parts, list):
                        _recent_parts = []
                    rotation_fb, _current_parts = detect_sensory_repetition(response, _recent_parts)
                    # Rolling window 업데이트 (최근 3턴 유지)
                    _recent_parts.append(_current_parts)
                    if len(_recent_parts) > 3:
                        _recent_parts = _recent_parts[-3:]

                    # Pidgin Echo: scene NPC label keywords
                    _npc_keywords = npc_manager.get_npc_label_keywords(channel_id, scene_npcs) if scene_npcs else {}
                    pidgin_fb = detect_pidgin_echo(response, _npc_keywords)

                    style_fb = " ".join(filter(None, [cliche_fb, cargo_fb, closure_fb, rotation_fb, pidgin_fb]))
                    if style_fb:
                        fmt_feedback = f"{fmt_feedback} {style_fb}".strip() if fmt_feedback else style_fb
                    domain_manager.update_session_ai_memory(
                        channel_id, {"format_feedback": fmt_feedback, "recent_body_parts": _recent_parts}
                    )
                    if fmt_feedback:
                        logger.info(f"[FormatCheck] {fmt_feedback[:80]}")
                    if _impersonation_retried:
                        if "[IMPERSONATION]" in fmt_feedback:
                            logger.warning(f"[Impersonation] {_impersonation_retried}회 재시도 후에도 사칭 지속 — 피드백으로 넘김")
                        else:
                            logger.info(f"[Impersonation] {_impersonation_retried}회 재시도 후 사칭 제거됨")

                    # 9. Background Extraction (Flash 모델로 별도 API 호출)
                    # V4 Inline Extraction 대신 기존 Background Extraction 복원
                    await self.schedule_background_extraction(ctx, response, message)
                else:
                    logger.warning(f"[!다시] No response generated for channel {channel_id}")
                    if feedback_msg:
                        try:
                            await feedback_msg.delete()
                        except Exception:
                            pass

                # [!다시] 컨텍스트 영구 저장 (응답 성공/실패 여부와 관계없이 유효한 데이터가 있으면 저장)
                if current_retry_ctx.get("has_response"):
                    domain_manager.save_last_execution_context(channel_id, current_retry_ctx)
                    logger.debug(f"[!다시] Persistent context saved for channel {channel_id}")

                # 성공 완료 후 스냅샷 정리 (메모리 누수 방지)
                self._retry_snapshots.pop(channel_id, None)

        except Exception as e:
            if feedback_msg:
                try:
                    await feedback_msg.delete()
                except Exception:
                    pass
            
            import traceback
            error_traceback = traceback.format_exc()
            logger.error(f"AI Process Error: {e}\n{error_traceback}")
            # Show the last part of the traceback to the user for debugging
            await message.channel.send(f"⚠️ **AI 처리 오류:** {e}\n```python\n{error_traceback[-500:]}\n```")

    # =========================================================
    # RETRY / REROLL (!다시)
    # =========================================================
    async def retry_last(self, message: discord.Message, channel_id: str, edited_input: str = None) -> bool:
        """
        마지막 AI 응답을 재생성합니다.
        [V2] 전체 도메인 스냅샷 복원으로 퀘스트/NPC/월드 상태까지 롤백.
        edited_input이 있으면 이전 입력을 교체하여 재생성합니다.
        """
        last_ctx = domain_manager.get_last_execution_context(channel_id)

        if not last_ctx or not last_ctx.get("has_response"):
            await message.channel.send("⚠️ 재시도할 이전 응답이 없거나 이미 처리 중입니다.")
            return False

        # 1. 백그라운드 작업 플러시 (이전 응답의 추출/발효 작업 취소)
        from background_task_queue import get_task_queue
        queue = get_task_queue()
        flushed = await queue.flush_channel(channel_id)
        if flushed:
            logger.info(f"[!다시] Flushed {flushed} background tasks for {channel_id}")

        # 2. 이전 메시지 삭제 (UNE 로그 + AI 응답)
        msg_ids = last_ctx.get("message_ids", [])
        for mid in msg_ids:
            try:
                m = await message.channel.fetch_message(mid)
                await m.delete()
            except Exception: pass

        # 3. 도메인 스냅샷 복원 (퀘스트/NPC/둠/기력/히스토리 전부 롤백)
        snapshot_entry = self._retry_snapshots.get(channel_id)
        snapshot = snapshot_entry.get("_data") if snapshot_entry else None
        if snapshot:
            domain_manager.save_domain(channel_id, copy.deepcopy(snapshot))
            logger.info(f"[!다시] Domain snapshot restored for {channel_id}")
        else:
            # 스냅샷 없음 (봇 재시작 등) — 히스토리만 정리 (레거시 폴백)
            d = domain_manager.get_domain(channel_id)
            history = d.get("history", [])
            if history and history[-1].get("role") == "Model":
                history.pop()
                if history and history[-1].get("role") != "Model":
                    history.pop()
                domain_manager.save_domain(channel_id, d)
            logger.warning(f"[!다시] No snapshot available, history-only rollback for {channel_id}")

        # 4. 재실행 텍스트 결정
        action_text = edited_input or last_ctx.get("action_text")
        label = "입력 수정 후 재생성" if edited_input else "서사를 다시 뽑는 중"
        feedback = await message.channel.send(f"🔄 **{label}...**")

        # 5. 재실행 (edited_input이 있으면 항상 system_trigger로 주입)
        if edited_input:
            await self.execute(message, channel_id, system_trigger=action_text, feedback_msg=feedback)
        else:
            orig_msg_id = last_ctx.get("original_message_id")
            try:
                orig_msg = await message.channel.fetch_message(orig_msg_id)
                await self.execute(orig_msg, channel_id, feedback_msg=feedback)
            except Exception:
                await self.execute(message, channel_id, system_trigger=action_text, feedback_msg=feedback)

        return True

    def get_last_context(self, channel_id: str) -> Optional[Dict[str, Any]]:
        """채널의 마지막 컨텍스트 반환 (디버깅용)"""
        return domain_manager.get_last_execution_context(channel_id)

    # =========================================================
    # BATCH / OBSERVATION (!진행/!턴 — 다인 동시 행동 + 관찰 모드)
    # =========================================================
    async def execute_batch(
        self,
        message: discord.Message,
        channel_id: str,
        pending_actions: Dict[str, Dict],
        feedback_msg: Optional[discord.Message] = None
    ) -> None:
        """다인 동시 행동 처리 → 통합 AI 서사 생성"""
        try:
            async with message.channel.typing():
                # 1. UNE 배치 실행
                result = await self.une.run_batch(channel_id, pending_actions)
                directive = result["directive"]
                system_log = result["system_message"]
                updated_context = result["game_context"]

                # 2. 시스템 메시지 출력 (판정/이변/멘탈 결과)
                if system_log:
                    await message.channel.send(system_log)

                # 3. 통합 action_text (모든 PC 행동 결합)
                action_parts = []
                for uid, info in pending_actions.items():
                    action_parts.append(f"[{info['mask']}]: {' / '.join(info['actions'])}")
                combined_action = "\n".join(action_parts)

                # 4. ResponseContext 구성
                d_data = domain_manager.get_domain(channel_id)
                first_uid = next(iter(pending_actions))
                p_data = d_data.get("participants", {}).get(first_uid, {})

                ctx = ResponseContext(
                    channel_id=channel_id,
                    user_id=first_uid,
                    user_mask=p_data.get("mask", "Unknown"),
                    action_text=combined_action,
                    domain_data=d_data,
                    player_data=p_data
                )

                # 5. Context 보강 + DAI 주입
                ctx = await self.gather_context(ctx)
                if updated_context:
                    ctx.dai = updated_context.shared_bus.dai
                    scene_type = ctx.dai.get("scene_type")
                    if scene_type:
                        ctx.scene_type = scene_type
                ctx.judgment_context = directive

                # 6. 프롬프트 빌드 + AI 응답 생성
                full_prompt, _ = self.build_prompt(ctx)
                response, _ = await self.generate_response(ctx, full_prompt)

                if feedback_msg:
                    try: await feedback_msg.delete()
                    except Exception: pass

                if response:
                    await bot_utils.send_long_message(message.channel, response)
                    # 히스토리: PC 행동은 이미 waiting 모드에서 저장됨, Model 응답만 추가
                    domain_manager.append_history(channel_id, "Model", response)

                    # Background Extraction (첫 PC 기준)
                    await self.schedule_background_extraction(ctx, response, message)

        except Exception as e:
            if feedback_msg:
                try: await feedback_msg.delete()
                except Exception: pass
            import traceback
            error_tb = traceback.format_exc()
            logger.error(f"Batch Process Error: {e}\n{error_tb}")
            await message.channel.send(f"⚠️ **배치 처리 오류:** {e}\n```python\n{error_tb[-500:]}\n```")

    async def execute_observation(
        self,
        message: discord.Message,
        channel_id: str,
        feedback_msg: Optional[discord.Message] = None
    ) -> None:
        """관찰 모드 → 세계 묘사 AI 서사 생성"""
        try:
            async with message.channel.typing():
                # 1. UNE 관찰 실행
                result = await self.une.run_observation(channel_id)
                directive = result["directive"]
                system_log = result["system_message"]
                updated_context = result["game_context"]

                if system_log:
                    await message.channel.send(system_log)

                if not updated_context:
                    if feedback_msg:
                        try: await feedback_msg.delete()
                        except Exception: pass
                    await message.channel.send("⚠️ 활성 캐릭터가 없습니다.")
                    return

                # 2. ResponseContext 구성
                d_data = domain_manager.get_domain(channel_id)
                participants = d_data.get("participants", {})
                base_uid = None
                for uid, p in participants.items():
                    if p.get("status") == "active":
                        base_uid = uid
                        break

                p_data = participants.get(base_uid, {}) if base_uid else {}
                ctx = ResponseContext(
                    channel_id=channel_id,
                    user_id=base_uid or "",
                    user_mask=p_data.get("mask", "관찰자"),
                    action_text="[관찰 — 주변을 지켜본다]",
                    domain_data=d_data,
                    player_data=p_data
                )

                ctx = await self.gather_context(ctx)
                if updated_context:
                    ctx.dai = updated_context.shared_bus.dai
                ctx.judgment_context = directive

                # 3. AI 응답 생성
                full_prompt, _ = self.build_prompt(ctx)
                response, _ = await self.generate_response(ctx, full_prompt)

                if feedback_msg:
                    try: await feedback_msg.delete()
                    except Exception: pass

                if response:
                    await bot_utils.send_long_message(message.channel, response)
                    domain_manager.append_history(channel_id, "관찰", "[관찰 모드]")
                    domain_manager.append_history(channel_id, "Model", response)

                    await self.schedule_background_extraction(ctx, response, message)

        except Exception as e:
            if feedback_msg:
                try: await feedback_msg.delete()
                except Exception: pass
            import traceback
            error_tb = traceback.format_exc()
            logger.error(f"Observation Process Error: {e}\n{error_tb}")
            await message.channel.send(f"⚠️ **관찰 처리 오류:** {e}\n```python\n{error_tb[-500:]}\n```")


# =========================================================
# FACTORY
# =========================================================

def get_orchestration_service(client_genai, model_id: str, model_id_flash: str) -> OrchestrationService:
    """OrchestrationService 인스턴스를 생성 및 반환합니다."""
    return OrchestrationService(client_genai, model_id, model_id_flash)

# =========================================================
# RUNTIME SINGLETON (Avoids main <-> command_handler cycles)
# =========================================================

_orchestration_runtime = None
_orchestration_params = None

def get_orchestration_runtime(client_genai, model_id: str, model_id_flash: str) -> Optional[OrchestrationService]:
    """
    Returns a cached OrchestrationService instance for the given client/model ids.
    Rebuilds if parameters changed. This replaces main._get_orchestration to avoid
    cyclic imports.
    """
    global _orchestration_runtime, _orchestration_params
    if not client_genai:
        return None
    params = (id(client_genai), model_id, model_id_flash)
    if _orchestration_runtime is None or _orchestration_params != params:
        _orchestration_runtime = get_orchestration_service(client_genai, model_id, model_id_flash)
        _orchestration_params = params
    return _orchestration_runtime
