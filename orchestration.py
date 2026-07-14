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
import traceback
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


def _check_dialogue_format(response: str, pc_names: list = None, user_input: str = "") -> str:
    """AI 응답에서 대사 포맷 위반을 감지하여 피드백 문자열 반환.
    PC 대사 에코(유저 입력 재출력)와 AI 창작 PC 대사를 구분:
    - 유저 입력에 포함된 대사와 5자 이상 겹치면 에코 → 제외
    - 겹치지 않으면 AI 창작 → [IMPERSONATION] 피드백"""
    lines = response.split('\n')
    correct_pat = re.compile(r'^\s*.+?\s*:\s*"')  # 이름(공백 포함): "대사"
    quote_pat = re.compile(r'"([^"]{2,})"')  # 2자 이상 쌍따옴표 텍스트 (캡처)
    # 판정/시스템 메시지 제외
    system_pat = re.compile(r'^\s*(?:🎲|📈|📉|🧠|⚠️|✅|❌|✨|🟠|🆕|🌿)')

    # 출력 형식 태그 (<Members>, <Market> 등) 제외
    tag_pat = re.compile(r'^\s*</?[A-Za-z_]')

    # PC 이름 패턴
    _pc_pats = []
    if pc_names:
        for pc in pc_names:
            if pc and pc != "Unknown":
                _pc_pats.append(re.compile(rf'^\s*{re.escape(pc)}'))

    # 유저 입력에서 대사 텍스트 추출 (에코 판별용)
    _user_quotes = set()
    if user_input:
        for m in re.finditer(r'"([^"]{3,})"', user_input):
            _user_quotes.add(m.group(1).strip()[:20])  # 앞 20자만 비교
        # 따옴표 없이 쓴 대사도 포함 (입력 전체를 청크로)
        _input_clean = re.sub(r'["\s]+', '', user_input)
        if len(_input_clean) >= 5:
            _user_quotes.add(_input_clean[:30])

    violations = []
    impersonations = []
    for line in lines:
        stripped = line.strip()
        if not stripped or system_pat.match(stripped) or tag_pat.match(stripped):
            continue

        # PC 이름으로 시작하는 줄 체크
        is_pc_line = _pc_pats and any(p.match(stripped) for p in _pc_pats)
        if is_pc_line:
            # PC 대사가 있는지 확인
            q_match = quote_pat.search(stripped)
            if q_match:
                ai_quote = q_match.group(1).strip()[:20]
                # 유저 입력과 겹치면 에코 → 제외
                is_echo = any(
                    ai_quote[:5] in uq or uq[:5] in ai_quote
                    for uq in _user_quotes
                ) if _user_quotes else False
                if not is_echo:
                    # AI가 창작한 PC 대사 → 사칭
                    impersonations.append(stripped[:40])
            continue  # PC 줄은 포맷 위반 체크에서 항상 제외

        if quote_pat.search(stripped) and not correct_pat.match(stripped):
            violations.append(stripped[:40])

    parts = []
    # [FORMAT] 출력 제거 (2026-04-26): 이름: "대사" prefix 강제가 W12 Three Chairs / W16 FID 표현과 충돌.
    # detect 로직(violations 변수)은 운영 분석 가치를 위해 유지하되 모델에 피드백 주입은 안 함.
    # if violations:
    #     examples = violations[:2]
    #     parts.append(f"[FORMAT] 대사 포맷 위반 {len(violations)}건. 예: {'; '.join(examples)}. 반드시 이름: \"대사\" 형식을 지켜라.")
    if impersonations:
        imp_examples = impersonations[:2]
        parts.append(f"[IMPERSONATION] PC 대사 창작 {len(impersonations)}건: {'; '.join(imp_examples)}. PC의 대사를 만들지 마라 — 유저가 입력한 대사만 재현하라.")
    if violations:
        # 운영 분석용 디버그만 (피드백 주입 X)
        import logging
        logging.getLogger(__name__).debug(f"[FormatDetect] dialogue prefix 부재 {len(violations)}건 — FID 허용 정책으로 무시")
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

        # [!다시] 채널별 도메인 스냅샷 — 모듈-전역 공유(인스턴스 재생성에도 보존, 상단 _RETRY_SNAPSHOTS).
        self._retry_snapshots = _RETRY_SNAPSHOTS

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
            # Tier 2: 새 위치 자동 등록 (world_tree에 없으면 추가)
            try:
                import world_tree
                if not world_tree.get_node(channel_id, current_location):
                    world_tree.add_node(
                        channel_id, current_location, node_type="area",
                        properties={"risk": location_risk or "Low", "tags": ["auto_detected"]},
                    )
            except Exception:
                pass
        if location_risk:
            domain_manager.set_current_risk(channel_id, location_risk)

        # NPC ?? ????
        new_attitudes = dai.get("npc_attitudes")
        if new_attitudes and isinstance(new_attitudes, dict):
            # [2026-07-13 PC 혼입 가드] Theoria가 per-NPC 필드에 PC를 섞는 사례는 실증됨
            # (psyche_states — waterfall 감정 stage 카메라 원칙 마스킹과 동일 근거).
            # 가드가 없으면 PC 이름의 세션 NPC가 자동 생성돼 이후 descriptor 관찰까지
            # PC/NPC 이중 성장 — "유저/캐릭터 태그 없이 관찰 혼합"의 로스터 측 경로.
            # 아래 trajectory/depth 루프도 같은 dict를 돌므로 원천에서 1회 필터.
            _pc_masks_att = set()
            try:
                for _p in domain_manager.get_domain(channel_id).get("participants", {}).values():
                    if _p.get("mask"):
                        _pc_masks_att.add(_p["mask"])
            except Exception:
                pass
            if _pc_masks_att:
                _removed_att = [n for n in new_attitudes if n in _pc_masks_att]
                if _removed_att:
                    new_attitudes = {k: v for k, v in new_attitudes.items() if k not in _pc_masks_att}
                    logger.debug(f"[NPC Attitude] PC 혼입 제외: {', '.join(_removed_att)}")
            for n_name, n_data in new_attitudes.items():
                existing_npc = npc_manager.get_npc(channel_id, n_name)
                if not existing_npc:
                    # description 플레이스홀더를 넣지 않는다 — 이후 entity_state descriptor가
                    # 실제 관찰로 채우고, 렌더는 그 전까지 이름/관찰만 보여줌.
                    # (옛 "Auto-detected by AI" 리터럴이 산문에 노출되던 문제 제거)
                    npc_manager.update_npc(channel_id, n_name, {
                        "source": "session",
                        "status": "active"
                    })
                    logger.info(f"Auto-created Session NPC: {n_name}")

                # M5: 태도 변경 코드 게이트 적용 (3턴 쿨다운 + 1단계 제한)
                _ws = domain_manager.get_world_state(channel_id)
                _current_turn = _ws.get("turn_index", 0) if _ws else 0
                _gate_result = npc_manager.update_npc_attitude_gated(
                    channel_id, n_name,
                    n_data.get("attitude", "neutral"),
                    _current_turn,
                    n_data.get("reason", "")
                )
                if _gate_result == "cooldown":
                    logger.debug(f"[M5] Attitude change for {n_name} blocked: cooldown")
                elif _gate_result == "clamped":
                    logger.debug(f"[M5] Attitude change for {n_name} clamped to ±1 step")

            # NPC Connection: trajectory → depth delta
            import random as _rng
            for n_name, n_data in new_attitudes.items():
                trajectory = n_data.get("trajectory", "stable")
                depth_range = config.NPC_TRAJECTORY_DEPTH_MAP.get(trajectory, (0, 0))
                if depth_range != (0, 0):
                    depth_delta = _rng.randint(min(depth_range), max(depth_range))
                    if depth_delta != 0:
                        domain_manager.update_helena_metric(channel_id, n_name, depth_delta=depth_delta)

            # 첫 등장 NPC: 프로필에서 초기 depth 가져오기
            for n_name in new_attitudes:
                existing_att = domain_manager.get_npc_attitudes(channel_id).get(n_name, {})
                if existing_att.get("depth", 0) == 0:
                    npc_data = npc_manager.get_npc(channel_id, n_name) or {}
                    initial_depth = npc_data.get("initial_depth", 0)
                    initial_tension = npc_data.get("initial_tension", 0)
                    if initial_depth > 0:
                        domain_manager.update_helena_metric(
                            channel_id, n_name,
                            depth_delta=initial_depth,
                            tension_delta=initial_tension
                        )

            ctx.existing_attitudes = domain_manager.get_npc_attitudes(channel_id)

        # NPC Knowledge 영속화
        new_knowledge = dai.get("npc_knowledge")
        if new_knowledge and isinstance(new_knowledge, dict):
            _atts_for_ledger = domain_manager.get_npc_attitudes(channel_id) or {}
            for npc_name, k_data in new_knowledge.items():
                if not isinstance(k_data, dict):
                    continue
                # [V10 Secret Ledger 2026-07-14] 원장 동기화 + 압력 상향.
                # 코드 압력(축적)이 LLM leak_risk(턴 판단)보다 높으면 상향만 — 하향 없음
                # (턴 낙관이 축적 압력을 리셋하는 것 방지).
                _ledger = domain_manager.sync_secret_ledger(
                    channel_id, npc_name, k_data, _atts_for_ledger.get(npc_name, {}))
                _rank = {"none": 0, "low": 1, "medium": 2, "high": 3}
                if _rank.get(_ledger["computed_risk"], 0) > _rank.get(k_data.get("leak_risk", "none"), 0):
                    k_data["leak_risk"] = _ledger["computed_risk"]
                # [v1.1 크로스턴 surface] 원장 surface를 당턴 DAI에 합성 주입 —
                # 이 블록(4.5)은 슬롯 빌드(5) 이전이라 iceberg가 같은 턴에 소비.
                # 추출이 이번 턴 surface를 준 비밀은 건드리지 않음(LLM 우선).
                if _ledger.get("surfaces"):
                    _ups = k_data.setdefault("secret_updates", [])
                    _covered = {str(u.get("truth_ref", "")).strip().lower()
                                for u in _ups if isinstance(u, dict) and u.get("surface")}
                    for _truth, _surf in _ledger["surfaces"].items():
                        if any(c and c in _truth.lower() for c in _covered):
                            continue
                        _ups.append({"truth_ref": _truth[:60], "surface": _surf})
                if k_data.get("knows"):
                    domain_manager.update_npc_knowledge(channel_id, npc_name, k_data)
            logger.info(f"[NPC Knowledge] Persisted for {len(new_knowledge)} NPCs")

            # Knowledge Propagation: 같은 장면 NPC 간 지식 전파
            scene_npcs = list(new_knowledge.keys())
            if len(scene_npcs) >= 2:
                prop_count = domain_manager.propagate_npc_knowledge(channel_id, scene_npcs)
                if prop_count:
                    logger.info(f"[Knowledge Propagation] {prop_count} facts shared among {scene_npcs}")

        # 장면 타입 추적
        curr_scene = ctx.scene_type or "normal"
        world = domain_manager.get_world_state(channel_id)
        prev_scene = world.get("current_scene_type", "normal")
        if prev_scene != curr_scene:
            world["current_scene_type"] = curr_scene
            domain_manager.update_world_state(channel_id, world)

        # 시간 흐름 처리 (Delegated to GameSystem)
        time_flow = dai.get("time_flow", {})
        # [2026-06-12] 명시 시간 Decree — 유저 인풋 regex 판정으로 explicit 신호 보강
        # (Theoria explicit_hours가 모델 교체 후 미발화 → "2시간 뒤"가 클램프에 깎이던 건 차단)
        ctx.time_decree_min = 0
        try:
            from game_world import parse_time_decree
            _decree_min = parse_time_decree(ctx.action_text or "")
            if _decree_min and not (time_flow or {}).get("explicit_hours"):
                time_flow = dict(time_flow or {})
                time_flow["explicit"] = True
                time_flow["explicit_hours"] = _decree_min / 60.0
                ctx.time_decree_min = _decree_min
                logger.info(f"[TimeDecree] 명시 선언 감지: +{_decree_min}분 (클램프 면제)")
        except Exception as _e_td:
            logger.debug(f"[TimeDecree] skip: {_e_td}")
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
        
        # UNE Run (ranked lore chunks from vector search)
        _ranked = ctx.domain_data.get("lore_chunks_ranked", []) if isinstance(ctx.domain_data, dict) else []
        result = await self.une.run(channel_id, user_id, ctx.action_text, lore_chunks_ranked=_ranked)
        
        # Extract Results
        updated_context = result["game_context"]
        directive = result["directive"]
        system_log = result["system_message"]
        
        # [BRIDGE] Sync SharedBus.dai → ResponseContext.dai
        # UNE Theoria 분석 결과를 레거시 dai로 복사
        dai = updated_context.shared_bus.dai
        ctx.dai = dai

        # [Scene Continuity 1층] DAI 스냅샷 — 이미 분석된 것을 기록
        _dai_snap = {
            "location": str(dai.get("CurrentLocation", dai.get("current_location", ""))),
            "energy": str(dai.get("EnergyDirection", dai.get("energy_direction", ""))),
            "scene_type": str(dai.get("SceneType", dai.get("scene_type", ""))),
            "position": dai.get("Position", dai.get("position", {})).get("value", 0.5)
                if isinstance(dai.get("Position", dai.get("position")), dict) else 0.5,
            "observation": str(dai.get("Observation", dai.get("observation", "")))[:200],
            "quality_flags": (lambda qf: {k: v for k, v in qf.items() if v and v != "null"} if isinstance(qf, dict) else {})(dai.get("QualityFlags") or dai.get("quality_flags") or {}),
            "chain_status": (dai.get("narrative_chain") or {}).get("chain_status", ""),
            "open_threads": (dai.get("narrative_chain") or {}).get("open_threads", [])[:5],
            "relevant_chunks": dai.get("relevant_chunks", []),
            "psyche_values": {  # B4: 이전 턴 감정 강도 비교용 (NPC별 value만)
                n: (s.get("psyche", s.get("mental", {})) or {}).get("value", 0)
                for n, s in (dai.get("psyche_states") or {}).items()
                if isinstance(s, dict)
            },
        }
        _ws = domain_manager.get_world_state(channel_id)
        _turn_num = _ws.get("turn_index", 0)
        domain_manager.update_scene_continuity(channel_id, dai_snapshot=_dai_snap, turn_number=_turn_num)

        # [Sensory Habituation] 같은 위치에서 감각 반복 감지
        if domain_manager.check_sensory_habituation(channel_id):
            qf = dai.get("quality_flags") or dai.get("QualityFlags") or {}
            if isinstance(qf, dict):
                qf["sensory_habituated"] = True
                dai["quality_flags"] = qf

        # [Flashback] (Phase 3 DEPRECATED) vigor 차감 / 로드아웃 / 인벤토리 X.
        # Theoria.flashback_eval 자동 감지는 유지 — dai 표시만 → 산문 반영.
        # _process_flashback 함수 자체는 dead 보존 (미래 재활성화 가능).
        fb_eval = dai.get("flashback_eval")
        if fb_eval and fb_eval.get("detected") and fb_eval.get("plausibility") != "impossible":
            updated_context.shared_bus.dai["flashback_confirmed"] = True
            updated_context.shared_bus.dai["flashback_declaration"] = fb_eval.get("declaration", "")

        # [Downtime] 다운타임 활동 처리 (rest_eval.activity != "rest")
        rest_eval = dai.get("rest_eval")
        if rest_eval and rest_eval.get("detected") and rest_eval.get("activity", "rest") != "rest":
            acting_uid = updated_context.narrative_anchors.get("acting_user_id", "")
            dt_msg = self._process_downtime(channel_id, updated_context.shared_bus, rest_eval, acting_uid)
            if dt_msg:
                system_log = (system_log or "") + f"\n{dt_msg}"

        # [Item Usage] 아이템 소비/획득 처리
        item_eval = dai.get("item_usage")
        if item_eval:
            item_msg = self._process_item_usage(
                channel_id, updated_context.narrative_anchors.get("acting_user_id", ""), item_eval
            )
            if item_msg:
                system_log = (system_log or "") + f"\n{item_msg}"

        # N2: Inventory validation — log warnings for items that silently vanished
        _acting_uid = updated_context.narrative_anchors.get("acting_user_id", "")
        if _acting_uid:
            _cur_mem = domain_manager.get_ai_memory(channel_id, _acting_uid)
            _cur_inv = game_character.migrate_notebook_to_inventory(
                _cur_mem.get("inventory", [])
            ).get("items", [])
            _mentioned_items = (dai.get("item_usage", {}) or {}).get("items", _cur_inv)
            if _cur_inv:
                cognition.validate_inventory(_mentioned_items, _cur_inv)

        # Scene Type 업데이트 (dai 우선)
        if dai.get("scene_type"):
            ctx.scene_type = dai["scene_type"]
        
        # Sync Context Back to ResponseContext for LLM
        ctx.judgment_context = directive # Inject UNE directives into prompt
        
        # We return system_log as a list of messages for Discord
        messages = [system_log] if system_log else []
        
        return ctx, messages, directive

    def _process_flashback(self, channel_id: str, bus, fb_eval: dict, user_id: str = "") -> Optional[str]:
        """회상 평가 → 기력 차감 + DAI 확정. Returns system message or None."""
        plausibility = fb_eval.get("plausibility", "plausible")
        tier = fb_eval.get("tier", "standard")
        declaration = fb_eval.get("declaration", "")
        dai = bus.dai

        # ── Loadout 분기 (flashback_type == "loadout") ──
        if fb_eval.get("flashback_type") == "loadout" and user_id:
            # 1) 인벤토리에 이미 있는 아이템 → 슬롯/기력 소비 없이 통과
            existing_inv = domain_manager.get_ai_memory(channel_id, user_id).get("inventory", [])
            if isinstance(existing_inv, dict):
                existing_inv = existing_inv.get("items", [])
            inv_names = {(i.get("name", "") if isinstance(i, dict) else str(i)).lower()
                         for i in existing_inv if i}
            if declaration and declaration.strip().lower() in inv_names:
                dai["flashback_confirmed"] = True
                dai["flashback_declaration"] = declaration
                return f"🎒 인벤토리: {declaration} (이미 소지 중)"

            # 2) 로드아웃 자동 초기화
            loadout = domain_manager.get_loadout(channel_id, user_id)
            if not loadout:
                domain_manager.set_loadout(channel_id, user_id, "standard", config.LOADOUT_SLOTS, "표준")
                loadout = domain_manager.get_loadout(channel_id, user_id)

            slots_needed = fb_eval.get("loadout_slots", 1)
            remaining = loadout["total_slots"] - loadout.get("used_slots", 0)
            cost = config.LOADOUT_SLOT_COST.get(slots_needed, 3)
            current_vigor = int(bus.vigor.get("value", 100))

            # 3) 슬롯 부족 시 → 기력 추가 차감으로 소프트 대체 (하드블록 안 함)
            overflow_cost = 0
            if slots_needed > remaining:
                overflow_cost = (slots_needed - remaining) * 5  # 초과 슬롯당 기력 5 추가
                slots_needed = remaining  # 남은 슬롯만 소비

            total_cost = cost + overflow_cost
            if current_vigor < total_cost:
                dai["flashback_confirmed"] = False
                return f"❌ 기력 부족 (현재 {current_vigor}, 필요 {total_cost})"

            bus.vigor["value"] = max(0, current_vigor - total_cost)
            if slots_needed > 0:
                domain_manager.consume_loadout_slot(channel_id, user_id, slots_needed, declaration)
            dai["flashback_confirmed"] = True
            dai["flashback_declaration"] = declaration
            dai["loadout_used"] = True
            # 회상 아이템 → 노트북 [소지품] + 인벤토리 자동 동기화
            game_character.add_item_to_sojipin(channel_id, declaration, user_id)
            new_vigor = bus.vigor["value"]
            new_remaining = remaining - slots_needed

            overflow_note = f" (슬롯 초과 → 기력 추가 -{overflow_cost})" if overflow_cost else ""
            return (
                f"🎒 장비: {declaration}\n"
                f"⚡ 슬롯 {slots_needed}개 소비 (잔여 {new_remaining}/{loadout['total_slots']}) | "
                f"기력 -{total_cost} → {new_vigor}/100{overflow_note}"
            )

        # 불가능한 회상 → 거부
        if plausibility == "impossible":
            dai["flashback_confirmed"] = False
            domain_manager.clear_pending_flashback(channel_id)
            return f"❌ 회상 거부: {fb_eval.get('reason', '논리적 모순')}"

        cost = int(config.FLASHBACK_COST_TIERS.get(tier, 8))
        # 특질 할인: 관련 특질 매칭 시 비용 50%
        relevant_passive = fb_eval.get("relevant_passive")
        if relevant_passive:
            cost = max(1, int(cost * config.FLASHBACK_PASSIVE_DISCOUNT))
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
        if relevant_passive:
            passive_note = f" (특질 '{relevant_passive}' 할인 → 비용 50%↓)"

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

        # 소비 처리: [소지품] 섹션에서 제거 → sync가 인벤토리 자동 반영
        for item in consumed:
            if not item or not isinstance(item, str):
                continue
            result = game_character.remove_item_from_sojipin(channel_id, item.strip(), user_id)
            if "못 찾음" not in result:
                log_parts.append(f"📦 소비: {item.strip()}")

        # 획득 처리: [소지품] 섹션에 추가 → sync가 인벤토리 자동 반영
        for item in gained:
            if not item or not isinstance(item, str):
                continue
            result = game_character.add_item_to_sojipin(channel_id, item.strip(), user_id)
            if "이미" not in result:
                log_parts.append(f"📥 획득: {item.strip()}")

        if not log_parts:
            return None

        msg = " | ".join(log_parts)
        if reason:
            msg += f" ({reason})"
        return msg

    def _apply_status_changes(self, channel_id: str, user_id: str, status_add, status_remove) -> None:
        """[N-2 후속] _extract_physical이 추출한 status_add/remove를 실제 status_effects에 적용.
        과거엔 'PlayerUpdate' 키로 묶였으나 소비처가 없어 전혀 적용되지 않았다(중복 위험 없음)."""
        if not status_add and not status_remove:
            return
        p_data = domain_manager.get_participant_data(channel_id, user_id)
        if not p_data:
            return
        try:
            current_turn = domain_manager.get_world_state(channel_id).get("turn_index", 0)
        except Exception:
            current_turn = 0
        changed = False
        for name in (status_add or []):
            if isinstance(name, str) and name.strip():
                p_data, _ = game_character.update_status_effect(p_data, "add", name.strip(), None, current_turn)
                changed = True
        for name in (status_remove or []):
            if isinstance(name, str) and name.strip():
                p_data, _ = game_character.update_status_effect(p_data, "remove", name.strip(), None, current_turn)
                changed = True
        if changed:
            domain_manager.save_participant_data(channel_id, user_id, p_data)

    def _process_downtime(self, channel_id: str, bus, rest_eval: dict, user_id: str) -> Optional[str]:
        """다운타임 활동 처리 (rest_eval.activity != 'rest'). Returns system message or None."""
        import random as _rng
        dt_type = rest_eval.get("activity", "recover")
        target = rest_eval.get("target")
        safe = rest_eval.get("safe_location", True)
        dai = bus.dai

        if dt_type == "recover":
            cfg = config.DOWNTIME_RECOVER.get("safe" if safe else "unsafe", {})
            bus.vigor["delta"] = bus.vigor.get("delta", 0) + cfg.get("vigor", 15)
            bus.composure["delta"] = bus.composure.get("delta", 0) + cfg.get("composure", 10)
            tag = "안전" if safe else "불안정"
            return f"💤 치료({tag}): 기력 +{cfg.get('vigor', 15)}, 평정 +{cfg.get('composure', 10)}"

        elif dt_type == "vice":
            cfg = config.DOWNTIME_VICE
            v_gain = cfg.get("base_vigor", 25)
            c_gain = cfg.get("base_composure", 20)
            bus.vigor["delta"] = bus.vigor.get("delta", 0) + v_gain
            bus.composure["delta"] = bus.composure.get("delta", 0) + c_gain
            projected = bus.composure.get("value", 50) + c_gain
            if projected > cfg.get("overindulge_threshold", 85):
                penalty = cfg.get("overindulge_penalty", -15)
                bus.composure["delta"] = bus.composure.get("delta", 0) + penalty
                dai["vice_overindulge"] = True
                return f"🍺 부업: 기력 +{v_gain}, 평정 +{c_gain} → 과용! 평정 {penalty}"
            return f"🍺 부업: 기력 +{v_gain}, 평정 +{c_gain}"

        elif dt_type == "train":
            cfg = config.DOWNTIME_TRAIN
            v_cost = cfg.get("vigor_cost", 5)
            c_cost = cfg.get("composure_cost", 5)
            bus.vigor["value"] = max(0, bus.vigor.get("value", 100) - v_cost)
            bus.composure["value"] = max(0, bus.composure.get("value", 100) - c_cost)
            progress_msg = ""
            if target and user_id:
                entry = domain_manager.advance_training(channel_id, user_id, target, cfg.get("progress_per_session", 1))
                progress_msg = f", 진행도 {entry.get('progress', 1)}/{entry.get('target', 3)}"
            return f"⚔️ 훈련({target or '일반'}): 기력 -{v_cost}, 평정 -{c_cost}{progress_msg}"

        elif dt_type == "socialize":
            cfg = config.DOWNTIME_SOCIALIZE
            bus.vigor["delta"] = bus.vigor.get("delta", 0) + cfg.get("vigor", 5)
            bus.composure["delta"] = bus.composure.get("delta", 0) + cfg.get("composure", 15)
            depth_gain = 0
            if target:
                depth_gain = _rng.randint(*cfg.get("depth_delta_range", (10, 15)))
                domain_manager.update_helena_metric(channel_id, target, depth_delta=depth_gain, tension_delta=0)
            return f"🤝 사교({target or '일반'}): 평정 +{cfg.get('composure', 15)}, 유대 +{depth_gain}"

        elif dt_type == "project":
            cfg = config.DOWNTIME_PROJECT
            v_cost = cfg.get("vigor_cost", 3)
            c_cost = cfg.get("composure_cost", 3)
            bus.vigor["value"] = max(0, bus.vigor.get("value", 100) - v_cost)
            bus.composure["value"] = max(0, bus.composure.get("value", 100) - c_cost)
            if target and user_id:
                domain_manager.advance_project(channel_id, user_id, target)
            return f"🔧 프로젝트({target or '?'}): 기력 -{v_cost}, 평정 -{c_cost}, 진행 +1"

        return None

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
    # STEP 7A/7B (V4) 제거 (2026-07-06 감사): _apply_inline_extraction +
    # schedule_background_tasks — 호출자 0. 발효+추출 전부
    # schedule_background_extraction(아래)이 대체 완료한 V4 이중 경로 유물.
    # =========================================================

    # =========================================================
    # STEP 7: BACKGROUND EXTRACTION (Queue-based)
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
                any(
                    n in response or n.split("(")[0].strip() in response
                    for n in domain_manager.get_npcs(channel_id).keys()
                )
            ) or ('"' in response or '「' in response),
            "narrative": any(kw in response for kw in [
                '처음으로', '마침내', '성공', '실패', '죽', '살', '마법',
                '괴물', '이상한', '기이한'
            ]) or bool((ctx.dai or {}).get("abnormal_elements")),
            "quest": any(kw in response for kw in [
                '퀘스트', '임무', '목표', '의뢰', '부탁', '완료', '달성', '단서', '정보', '비밀'
            ]),
            "world_state": True,  # Always run World State Updater (+1 Flash)
            "render_fingerprint": True,  # [Scene Continuity 2층] 항상 실행
            # [2026-07-13 수리] entity_state 키가 이 dict에 아예 없어서 cognition 게이트
            # (extraction_hints.get("entity_state", False))에서 영구 False — NPC descriptor/
            # PCObserved 추출이 라이브에서 0회 실행. 07-04 관찰→표시 브릿지(N-A backfill/
            # N-B 재작성/P-B PC임계/T-A tier)가 전부 입력 기아로 사문화된 근본 원인
            # (증상: !npc에 세션 NPC 정보 안 참). 관찰은 성장 루프의 원료라 world_state처럼 상시.
            "entity_state": True,
        }

        # Phase 1: 즉시 노트북 업데이트 (높은 우선순위)
        if extraction_hints["physical"]:
            async def immediate_physical_update():
                try:
                    # [N-1/N-2] 라이브 노트북 재읽기 (stale ctx.notebook_txt 대신).
                    # update_world_state의 item_usage가 이번 턴에 한 [소지품] 변경을 반영하기 위함.
                    live_notebook = game_character.get_notebook_text(channel_id, ctx.user_id)
                    status = game_character.get_status_effect_names(
                        ctx.player_data.get("status_effects", []) if ctx.player_data else []
                    )
                    phys_res = await cognition._extract_physical(
                        self.client, self.model_id_flash,
                        ctx.action_text, response,
                        live_notebook, status
                    )
                    if phys_res:
                        # [N-1/N-2] 역할 경계: [소지품]은 라이브 보존(item_usage 소유), [메모]만 추출분 머지.
                        nb_upd = phys_res.get("notebook_update")
                        if nb_upd:
                            merged = game_character.merge_notebook_preserve_inventory(live_notebook, nb_upd)
                            if merged != live_notebook:
                                game_system.update_notebook_text(channel_id, merged, ctx.user_id)
                                await message.channel.send("📔 노트북 기록됨")
                        # status_add/remove 배선 — 과거엔 "PlayerUpdate" 키로 묶였으나 소비처가 없어 버려졌음.
                        self._apply_status_changes(
                            channel_id, ctx.user_id,
                            phys_res.get("status_add"), phys_res.get("status_remove")
                        )
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
            prev_continuity = domain_manager.get_latest_frame(channel_id)
            # Fresh notebook reload (stale ctx 방지 — 배경 작업은 지연 실행될 수 있음)
            fresh_notebook = game_system.get_notebook_text(channel_id, ctx.user_id)

            # === Arc 컨텍스트 (Phase 4b) ===
            _arc_context_str = ""
            _arc_promote_cand = None
            try:
                _nt_state_pre = domain_manager.get_narrative_tracker_state(channel_id)
                _active_arcs_pre = [
                    s for s in _nt_state_pre.get("storylines", [])
                    if isinstance(s, dict) and s.get("is_arc") and s.get("status") == "active"
                ]
                if _active_arcs_pre:
                    _lines = []
                    for arc in _active_arcs_pre:
                        _phases = arc.get("phases", [])
                        _cur_p = _phases[-1] if _phases else "(initial)"
                        _lines.append(
                            f"Arc #{arc.get('id')}: cat={arc.get('origin_category', '?')}, "
                            f"phase={_cur_p}, prox={arc.get('proximity', 0):.2f}, "
                            f"weight={arc.get('weight', 0):.2f}"
                        )
                    _arc_context_str = "\n".join(_lines)
                # bus.anomaly.arc_promote_candidate
                if hasattr(ctx, "bus") and getattr(ctx.bus, "anomaly", None):
                    _arc_promote_cand = ctx.bus.anomaly.get("arc_promote_candidate")
            except Exception as _e_arc_pre:
                logger.debug(f"[Arc] PMU context build skipped: {_e_arc_pre}")

            updates = await cognition.extract_all_updates(
                self.client, self.model_id_flash,
                ctx.action_text, response,
                notebook=fresh_notebook,
                current_status=status,
                lore_npc_names=lore_npcs,
                scene_npc_names=scene_npcs,
                current_quests=current_quests,
                extraction_hints=hints,
                current_session_memory=session_memory,
                previous_continuity=prev_continuity,
                arc_context=_arc_context_str,
                arc_promote_candidate=_arc_promote_cand,
            )
            
            # [V10 검증 lite] 추출 self-check 로그 (detection-only — 아직 게이트 X)
            _unc = updates.get("_uncertain") if isinstance(updates, dict) else None
            if _unc:
                logger.info(f"[Extract self-check] uncertain sections this turn: {_unc}")

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

            # NPC Behavioral Imprints
            npc_imp = updates.get("NPCImprintUpdate")
            if npc_imp and isinstance(npc_imp, dict):
                ws = domain_manager.get_world_state(channel_id)
                current_turn = ws.get("turn_index", 0) if ws else 0
                domain_manager.update_npc_imprints(channel_id, npc_imp, turn=current_turn)
                logger.info(f"[Imprint] {list(npc_imp.keys())}")

            # NPC↔NPC Relations (entity_relations)
            npc_rels = updates.get("NPCRelationUpdate")
            if npc_rels and isinstance(npc_rels, list):
                try:
                    import entity_relations
                    _ws = domain_manager.get_world_state(channel_id)
                    _turn = _ws.get("turn_index", 0) if _ws else 0
                    _rel_count = entity_relations.process_flash_relations(channel_id, npc_rels, current_turn=_turn)
                    if _rel_count:
                        logger.info(f"[EntityRelations] Processed {_rel_count} relation updates")
                except Exception as e:
                    logger.warning(f"[EntityRelations] Failed to process: {e}")

            # [관계 decay] 안 건드린 관계 점감(fade) — 매 턴, npc_rels 유무 무관. delete 아닌 흐려짐.
            try:
                import entity_relations as _er_d
                _ws_d = domain_manager.get_world_state(channel_id)
                _turn_d = _ws_d.get("turn_index", 0) if _ws_d else 0
                _er_d.cleanup_stale_relations(channel_id, _turn_d)
            except Exception as _e_d:
                logger.debug(f"[EntityRelations] decay skip: {_e_d}")

            pmu = updates.get("PlayerMemoryUpdate")
            if pmu:
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
                # Trait Evolution (desc-only update for existing passives)
                if pmu.get("trait_evolution"):
                    mem = domain_manager.get_ai_memory(channel_id, ctx.user_id)
                    current_passives = mem.get("passives", [])
                    for evo in pmu["trait_evolution"]:
                        if not isinstance(evo, dict):
                            continue
                        evo_name = evo.get("name", "")
                        new_desc = evo.get("new_desc", "")
                        if not evo_name or not new_desc:
                            continue
                        for p in current_passives:
                            if isinstance(p, dict) and p.get("name") == evo_name:
                                p["desc"] = new_desc
                                logger.info(f"[TraitEvolution] {evo_name} desc updated")
                                break
                    domain_manager.update_ai_memory(channel_id, ctx.user_id, {"passives": current_passives})
            
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
                if wsu.get("residual_effects") and isinstance(wsu["residual_effects"], str):
                    mem_updates["residual_effects"] = wsu["residual_effects"]
                if mem_updates:
                    domain_manager.update_session_ai_memory(channel_id, mem_updates)
                    logger.info(f"[WorldState] Updated session memory: {list(mem_updates.keys())}")

            # [NarrativeTracker] 턴 로그 + 엔티티 상태 이력 업데이트
            try:
                import narrative_tracker
                nt_state = domain_manager.get_narrative_tracker_state(channel_id)

                # turn_idx: world_state에서 획득, 없으면 히스토리 길이 기반
                ws = domain_manager.get_world_state(channel_id)
                _ti = (ws or {}).get("turn_index")
                turn_idx = _ti if _ti is not None else len(session_memory.get("history", [])) // 2

                # 턴 로그 기록
                # [2026-06-11 Fix] entities 소스 교정: 기존 npc_schedule_hints는 "그 턴에 새 스케줄
                # 힌트가 나왔는가"의 대리 지표라 장면 인물과 무관하게 자주 빈값 → storyline 분류
                # 건너뜀 + importance 가산 누락. 주 소스를 DAI 장면 실재 인물로, 힌트는 보조 합류.
                # PC 가면은 제외 (모든 턴에 있어 storyline 변별력 없음 — 기존 동작과도 정합).
                _dai_d = ctx.dai if ctx.dai else {}
                _scene_names = list(dict.fromkeys(
                    list((_dai_d.get("npc_attitudes") or {}).keys())
                    + list((_dai_d.get("psyche_states") or {}).keys())
                    + (list((wsu or {}).get("npc_schedule_hints", {}).keys()) if wsu else [])
                ))
                _pc_masks = set()
                try:
                    for _p in domain_manager.get_domain(channel_id).get("participants", {}).values():
                        if _p.get("mask"):
                            _pc_masks.add(_p["mask"])
                except Exception:
                    pass
                involved_npcs = [n for n in _scene_names if n not in _pc_masks]
                qf = ctx.dai.get("quality_flags", {}) if ctx.dai else {}
                user_brief = str(ctx.action_text or "")[:200]
                ai_brief = str(response or "")[:300]
                narrative_tracker.record_turn(nt_state, turn_idx, user_brief, ai_brief, involved_npcs, qf)

                # [T-A] NPC 등장 카운트(구별 턴만) — 1회성/다회성 tier 계측. session만 내부 게이트.
                try:
                    for _inpc in involved_npcs:
                        npc_manager.mark_npc_appearance(channel_id, _inpc, turn_idx)
                except Exception as _e_appear:
                    logger.debug(f"[NPC Tier] appearance mark skipped: {_e_appear}")

                # 엔티티 상태 변화 기록
                est_data = updates.get("EntityStateUpdate")
                if est_data:
                    narrative_tracker.update_entity_states(nt_state, turn_idx, est_data)

                    # [NPC 관찰 누적 + 틈틈이 재작성] 룰북형 로어북 경험: 플레이가 흐를수록 NPC 시트가
                    # 알아서 자란다. Flash descriptor=이번 턴 드러난 '새 정체성 디테일'.
                    # 관찰은 '설명 본문'이 아니라 별도 raw 필드 play_observed에 누적한다(무한 누적=의미
                    # 퇴색 방지). 그 위에서:
                    #   - 미등록          → 신규 세션 NPC 생성(desc 시드 + play_observed 시드)
                    #   - new_individual   → 동명 '별개체'(몹) 자동 태그. (로어 제외)
                    #   - source=lore      → 원문 시트 동결, play_observed에만 관찰 성장(장기기억 자산)
                    #   - 세션 NPC         → 관찰이 250자씩 자랄 때마다 analyze_character_sheet 전용 콜로
                    #                        role/외형/description/passives를 '재작성'(consolidation).
                    # update_npc는 엔트리 통째 교체(source/aliases만 보존) → 항상 기존 dict를 full-merge.
                    # analyze_character_sheet는 analysis_backend 파사드로 openai(현행)에서도 동작. PC 제외.
                    try:
                        _changes = est_data.get("changes") if (isinstance(est_data, dict) and "changes" in est_data) else est_data
                        for _npc_name, _ch in (_changes.items() if isinstance(_changes, dict) else []):
                            if not isinstance(_ch, dict):
                                continue
                            _desc = _ch.get("descriptor")
                            if not _desc or not str(_desc).strip() or _npc_name in _pc_masks:
                                continue
                            _desc = str(_desc).strip()
                            _new_indiv = bool(_ch.get("new_individual"))
                            _existing = npc_manager.get_npc(channel_id, _npc_name)
                            if not _existing:
                                npc_manager.update_npc(channel_id, _npc_name, {
                                    "source": "session", "description": _desc,
                                    "status": "active", "play_observed": _desc,
                                })
                                logger.info(f"[NPC Sheet] 즉석 NPC 생성: {_npc_name}")
                                continue
                            _src = str(_existing.get("source", "")).lower()
                            if _new_indiv and _src != "lore" and not npc_manager.is_mob_tag(_npc_name):
                                _tagged = npc_manager.register_ai_npc(
                                    channel_id, _npc_name, description=_desc, context="auto mob-tag (new_individual)")
                                if _tagged and _tagged != _npc_name:
                                    logger.info(f"[NPC Sheet] 동명 별개체 자동 태그: {_npc_name} → {_tagged}")
                                continue
                            # 관찰 누적(로어/세션 공통) — 별도 play_observed 필드, dedup + 1500자 cap
                            _obs = str(_existing.get("play_observed", "") or "").strip()
                            if _desc in _obs:
                                continue  # 새 정보 없음
                            _obs = (_obs + "\n" + _desc).strip()[-1500:] if _obs else _desc
                            _merged = dict(_existing)
                            _merged["play_observed"] = _obs
                            # [N-A] description이 비어 있으면(attitude 채널이 이름만 선점한 스텁 등)
                            # 이번 관찰로 즉시 backfill → !npc/roster가 더는 빈칸이 아니고,
                            # 이후 재작성(N-B)이 정제. 이미 있으면 건드리지 않음(작가/증류 보존).
                            if not str(_merged.get("description", "") or "").strip():
                                _merged["description"] = _desc
                            _rewrote = False
                            # 세션 NPC: 관찰이 충분히 자라면 틈틈이 '면모 시트' 재작성.
                            # 이전 시트(정체성/불씨/면모)를 컨텍스트로 얹어 → 정체성은 안정 유지,
                            # 면모는 정제/추가(Fate 마일스톤). NPC는 주사위 없음 → passives 미저장.
                            # [2026-07-13 manual 동결] lore뿐 아니라 manual(!npc 수제 프로필)도 재작성 제외 —
                            # 수제 하이브리드 프로필(### Voice/Hard Rules가 description에 삶)이 증류본으로
                            # 교체되며 파괴되던 충돌 수리. tier에서 lore/manual=작가권위 동급인데 재작성만
                            # 비대칭이었음. 관찰은 lore처럼 play_observed로 계속 누적(렌더 별도 섹션).
                            if _src not in ("lore", "manual") and getattr(self, "client", None):
                                _built = int(_existing.get("_obs_built_len", 0) or 0)
                                if len(_obs) >= 250 and (len(_obs) - _built) >= 250:
                                    try:
                                        _prev = []
                                        if _existing.get("high_concept"):
                                            _prev.append(f"[기존 정체성] {_existing['high_concept']}")
                                        if _existing.get("trouble"):
                                            _prev.append(f"[기존 불씨] {_existing['trouble']}")
                                        _pa = _existing.get("aspects")
                                        if isinstance(_pa, list) and _pa:
                                            _prev.append("[기존 면모] " + " / ".join(str(a) for a in _pa))
                                        # [2026-07-13 로어 접지] 이름/별칭이 등장하는 로어 청크(최대 2, 각 600자)를
                                        # 증류 입력에 참고로 동봉 — 세계관 용어·소속이 로어와 어긋나게 증류되는 것 방지.
                                        # 리터럴 매칭=콜 0·결정론. 통복사 방지 지시 포함. lore NPC는 재작성 자체가
                                        # 동결(_src=="lore" 게이트)이라 이 경로와 무관.
                                        _lore_ref = ""
                                        try:
                                            _names = [_npc_name] + [str(a) for a in (_existing.get("aliases") or []) if a]
                                            _names += [n.split("(")[0].strip() for n in list(_names) if "(" in n]
                                            _names = [n for n in dict.fromkeys(_names) if n]
                                            _hits = []
                                            for _chk in domain_manager.get_lore_chunks(channel_id):
                                                _lbl = str(_chk.get("label", "") or "") if isinstance(_chk, dict) else ""
                                                _txt = str(_chk.get("content", "") or "") if isinstance(_chk, dict) else str(_chk or "")
                                                if any(n in _txt or n in _lbl for n in _names):
                                                    _hits.append((f"({_lbl}) " if _lbl else "") + _txt.strip()[:600])
                                                if len(_hits) >= 2:
                                                    break
                                            if _hits:
                                                _lore_ref = ("[세계관 참고 — 로어 원문 발췌. 관찰 해석의 접지로만 쓰고 "
                                                             "시트로 문장을 통복사하지 말 것. 관찰과 충돌하면 로어 우선]\n"
                                                             + "\n---\n".join(_hits) + "\n\n")
                                        except Exception:
                                            _lore_ref = ""
                                        _distill_in = _lore_ref + (("\n".join(_prev) + "\n\n" + _obs) if _prev else _obs)
                                        _sheet = await cognition.analyze_character_sheet(
                                            self.client, self.model_id_flash, _distill_in)
                                        if _sheet:
                                            # 정체성/불씨: 새 값 있으면 갱신, 없으면 이전 보존(near-sacrosanct)
                                            if _sheet.get("high_concept"):
                                                _merged["high_concept"] = _sheet["high_concept"]
                                            if _sheet.get("trouble"):
                                                _merged["trouble"] = _sheet["trouble"]
                                            _asp = _sheet.get("aspects")
                                            if isinstance(_asp, list) and _asp:
                                                _merged["aspects"] = [str(a).strip() for a in _asp if a and str(a).strip()][:6]
                                            # [N-B] 외형/역할/설명/배경: 모델이 준 경우만 덮음.
                                            # description 추가 = PC 재작성(아래)과 패리티 — NPC만 빠져
                                            # 있어서 증류돼도 설명란이 계속 비던 문제 수리.
                                            for _k in ("appearance", "role", "description", "background"):
                                                if _sheet.get(_k):
                                                    _merged[_k] = _sheet[_k]
                                            _merged["_obs_built_len"] = len(_obs)
                                            _rewrote = True
                                            logger.info(f"[NPC Sheet] 세션 NPC 면모 재작성: {_npc_name} (관찰 {len(_obs)}자)")
                                    except Exception as _e_cons:
                                        logger.warning(f"[NPC Sheet] 면모 재작성 실패: {_e_cons}")
                            npc_manager.update_npc(channel_id, _npc_name, _merged)
                            if not _rewrote:
                                logger.info(f"[NPC Sheet] 관찰 누적: {_npc_name} ({len(_obs)}자, {_src or 'session'})")
                    except Exception as _e_sheet:
                        logger.warning(f"[NPC Sheet] enrichment skipped: {_e_sheet}")

                # [PC 시트 플레이기반 '진화'] 시트 없이 시작한 PC를 플레이로 채우고 계속 진화시킨다.
                # 매 턴 pc_observed(드러난 PC 정체성)를 play_observed에 누적 → 300자씩 자랄 때마다
                # analyze_character_sheet 전용 콜로 description/역할/외형/패시브를 '재작성'(덮어쓰기)한다.
                # → 설명란 자체가 진화(자가종료 없음). 단 유저가 직접 올린 작가-시트(_pc_play_built 없이
                #   기계필드 보유)는 '동결'해 덮지 않는다(NPC의 로어/세션 구분과 동일). PC 이름(가면) 필요.
                try:
                    _pc_obs = updates.get("PCObserved")
                    if _pc_obs and str(_pc_obs).strip() and getattr(self, "client", None):
                        _pc = domain_manager.get_default_pc_info(channel_id) or {}
                        _pc_name = _pc.get("name") or (next(iter(_pc_masks)) if _pc_masks else "")
                        if _pc_name:
                            _obs_buf = str(_pc.get("play_observed", "") or "")
                            _new = str(_pc_obs).strip()
                            if _new and _new not in _obs_buf:
                                _obs_buf = (_obs_buf + "\n" + _new).strip()[-2000:]
                            _pc["name"] = _pc_name
                            _pc["play_observed"] = _obs_buf
                            # 작가-작성 시트(_pc_play_built 마커 없이 기계필드 보유) → 동결
                            _authored = bool((_pc.get("passives") or _pc.get("inventory"))
                                             and not _pc.get("_pc_play_built"))
                            domain_manager.set_default_pc_info(channel_id, _pc)
                            if not _authored:
                                _built = int(_pc.get("_pc_build_len", 0) or 0)
                                # [P-B] 첫 충전은 ~130자(중간)로 당겨 초반 빈칸 단축, 이후 재작성은
                                # 300자마다(과다 콜 방지). 진화 흐름 자체는 유지.
                                _pc_thr = 130 if _built == 0 else 300
                                if len(_obs_buf) >= _pc_thr and (len(_obs_buf) - _built) >= _pc_thr:
                                    try:
                                        # 이전 면모를 컨텍스트로 얹어 정체성 안정 유지(NPC와 동일)
                                        _pv = []
                                        if _pc.get("high_concept"):
                                            _pv.append(f"[기존 정체성] {_pc['high_concept']}")
                                        if _pc.get("trouble"):
                                            _pv.append(f"[기존 불씨] {_pc['trouble']}")
                                        _pca = _pc.get("aspects")
                                        if isinstance(_pca, list) and _pca:
                                            _pv.append("[기존 면모] " + " / ".join(str(a) for a in _pca))
                                        _pc_distill = ("\n".join(_pv) + "\n\n" + _obs_buf) if _pv else _obs_buf
                                        _sheet = await cognition.analyze_character_sheet(
                                            self.client, self.model_id_flash, _pc_distill)
                                        if _sheet:
                                            # 기계층(판정 연동): PC는 유지 — role/외형/설명 + passives/inventory
                                            # + notes(일지): apply_pc_info_to_user가 [일지] 섹션으로 라우팅
                                            for _k in ("role", "species", "appearance", "description", "background", "notes"):
                                                if _sheet.get(_k):
                                                    _pc[_k] = _sheet[_k]  # 진화: 덮어쓰기
                                            if _sheet.get("passives"):
                                                _pc["passives"] = _sheet["passives"]
                                            if _sheet.get("inventory"):
                                                _pc["inventory"] = _sheet["inventory"]
                                            # 서사층(면모 시트): 정체성은 새 값 있을 때만(보존), 면모 6 cap
                                            if _sheet.get("high_concept"):
                                                _pc["high_concept"] = _sheet["high_concept"]
                                            if _sheet.get("trouble"):
                                                _pc["trouble"] = _sheet["trouble"]
                                            _pas = _sheet.get("aspects")
                                            if isinstance(_pas, list) and _pas:
                                                _pc["aspects"] = [str(a).strip() for a in _pas if a and str(a).strip()][:6]
                                            _pc["_pc_build_len"] = len(_obs_buf)
                                            _pc["_pc_play_built"] = True
                                            domain_manager.set_default_pc_info(channel_id, _pc)
                                            # sync → apply_pc_info_to_user가 notes→[일지]·inventory→[소지품]로
                                            # 라우팅(background 누출 없음). 자동 재작성마다 연속성 일지 누적.
                                            domain_manager.sync_matching_participants(channel_id, _pc)
                                            logger.info(f"[PC Build] PC 시트 재작성/진화(면모+기계): {_pc_name} (관찰 {len(_obs_buf)}자)")
                                    except Exception as _e_pcb:
                                        logger.warning(f"[PC Build] 시트 재작성 실패: {_e_pcb}")
                except Exception as _e_pco:
                    logger.warning(f"[PC Build] pc_observed 처리 skipped: {_e_pco}")

                # 스토리라인 분류
                last_entry = nt_state["turn_log"][-1] if nt_state.get("turn_log") else None
                if last_entry:
                    narrative_tracker.assign_to_storyline(nt_state, last_entry)

                # [Sprint G 2026-04-28] Anti-Chekhov tension 라벨링 적용
                # Pro 응답에서 Flash가 추출한 발사된 무게중심 약속 (kind/primary/priority) 반영
                # 매칭은 label substring, 미매칭은 첫 active storyline에 새 entry insert
                # 가벼운 hook은 라벨링 안 받음 → 자연 소멸 layer (apply_tension_decay)가 처리
                tensions_labeled = []
                if pmu and isinstance(pmu, dict):
                    raw_tensions = pmu.get("tensions") or []
                    if isinstance(raw_tensions, list):
                        tensions_labeled = raw_tensions
                if tensions_labeled:
                    narrative_tracker.apply_tension_labels(nt_state, tensions_labeled, turn_idx)
                    logger.info(f"[NarrativeTracker] Applied {len(tensions_labeled)} tension labels")

                # [Sprint I 2026-04-28] 제미니 부정 감정 매몰 + voidfill 남기기 — 다음 턴 GM Mover prefix의 입력
                # 강제 아니라 *신호*로만 보존 — 모델 self-discipline에 의존
                if pmu and isinstance(pmu, dict):
                    _saturation = float(pmu.get("emotional_saturation") or 0.0)
                    _voidfills = pmu.get("voidfill_inferences") or []
                    if not isinstance(_voidfills, list):
                        _voidfills = []
                    nt_state["last_climate"] = {
                        "saturation": max(0.0, min(1.0, _saturation)),
                        "voidfill_count": len(_voidfills),
                        "voidfill_samples": [v for v in _voidfills if isinstance(v, dict)][:2],
                        "turn": turn_idx,
                    }
                    if _saturation >= 0.5 or len(_voidfills) > 0:
                        logger.info(f"[Climate] Saturation={_saturation:.2f} Voidfills={len(_voidfills)} (turn {turn_idx})")

                # 자연 소멸 layer — 매 턴 호출, dormant 12 / expire 36 룰
                narrative_tracker.apply_tension_decay(nt_state, turn_idx)

                # === Arc PMU 결과 처리 (Phase 4b, spec v2 §5.1) ===
                # ArcUpdates / ArcDecisions를 storyline에 적용. tick_arcs 직전에 처리해서
                # 갱신된 좌표가 같은 턴 tick_arcs에 반영되도록.
                try:
                    _arc_updates_payload = updates.get("ArcUpdates")
                    if _arc_updates_payload and isinstance(_arc_updates_payload, list):
                        _au_events = narrative_tracker.apply_arc_updates(
                            nt_state, _arc_updates_payload, turn_idx
                        )
                        if _au_events.get("phase_transitions"):
                            logger.info("[Arc] phase_transitions: %s", _au_events["phase_transitions"])
                    _arc_decisions_payload = updates.get("ArcDecisions")
                    if _arc_decisions_payload and isinstance(_arc_decisions_payload, dict):
                        _ad_events = narrative_tracker.apply_arc_decisions(
                            nt_state, _arc_decisions_payload, turn_idx
                        )
                        if _ad_events.get("promoted"):
                            logger.info("[Arc] Promoted from PMU confirm: %s", _ad_events["promoted"])
                        if _ad_events.get("rejected"):
                            logger.info("[Arc] PMU rejected categories: %s", _ad_events["rejected"])
                except Exception as _e_pmu_arc:
                    logger.warning("[Arc] PMU result apply failed: %s", _e_pmu_arc)

                # === Arc 좌표 갱신 (Phase 5, spec v2 §4.3) ===
                # active arcs의 5축 좌표 자연 갱신 + armed 토글 + dormant 판정
                try:
                    _arc_ctx = {
                        "current_location": (ctx.dai or {}).get("current_location", "") if ctx.dai else "",
                        "relevant_npcs": (ctx.dai or {}).get("relevant_npcs", []) if ctx.dai else [],
                        "scene_type": (ctx.dai or {}).get("scene_type", "normal") if ctx.dai else "normal",
                        "anomaly_category": (ctx.bus.anomaly or {}).get("category", "") if hasattr(ctx, "bus") else "",
                        "doom_phase": (ctx.bus.doom or {}).get("chapter_phase", "") if hasattr(ctx, "bus") else "",
                        "quality_flags": (ctx.dai or {}).get("quality_flags", {}) if ctx.dai else {},
                        "decisive": bool((ctx.dai or {}).get("decisive_action", False)) if ctx.dai else False,
                    }
                    _arc_events = narrative_tracker.tick_arcs(nt_state, _arc_ctx, turn_idx)
                    if _arc_events.get("dormant"):
                        logger.info("[Arc] Tick: dormant=%s", _arc_events["dormant"])
                    if _arc_events.get("armed"):
                        logger.info("[Arc] Tick: armed=%s", _arc_events["armed"])
                except Exception as _e_arc:
                    logger.warning("[Arc] tick_arcs failed: %s", _e_arc)

                # 5턴 간격 스토리라인 요약 (Flash 소형 콜)
                import config as _cfg
                flash_model = _cfg.MODEL_ID_FLASH
                await narrative_tracker.summarize_if_needed(
                    nt_state, turn_idx,
                    client=self.client if hasattr(self, 'client') else None,
                    model_id=flash_model
                )

                domain_manager.update_narrative_tracker_state(channel_id, nt_state)
            except Exception as nt_err:
                logger.warning("[NarrativeTracker] Update failed: %s", nt_err)

            # [Scene Continuity 2층] 렌더링 지문 저장
            rfp = updates.get("RenderFingerprint")
            if rfp and isinstance(rfp, dict):
                fingerprint = {k: rfp.get(k, "") for k in ("gaze", "lighting", "palette", "rhythm", "temporal_density")}
                fingerprint["unresolved"] = rfp.get("unresolved", [])
                domain_manager.update_scene_continuity(channel_id, render_fingerprint=fingerprint)
                logger.debug("[RenderFP] Stored: gaze=%s, lighting=%s",
                             fingerprint.get("gaze", "")[:50], fingerprint.get("lighting", "")[:50])

        except Exception as e:
            logger.error(f"Background Extraction Failed: {e}\n{traceback.format_exc()}")


    # =========================================================
    # EXECUTION ENTRY POINT
    # =========================================================
    async def execute(
        self,
        message: discord.Message,
        channel_id: str,
        system_trigger: Optional[str] = None,
        feedback_msg: Optional[discord.Message] = None,
        user_input_override: Optional[str] = None,
        record_user_history: bool = True
    ) -> None:
        """
        AI 응답 생성 파이프라인을 실행합니다.

        Args:
            feedback_msg: '서사 생성 중...' 안내 메시지 객체 (완료 후 삭제용)
            record_user_history: False면 유저 입력 히스토리 기록 스킵 (chat_with_ooc —
                main.py가 IC 원문을 message_id 포함 선기록한 턴. 결합 디렉티브를 또 적으면
                IC 이중 잔존 + OOC 메타가 IC 기록에 영구 노출. 2026-07-02)
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

            # 1.1 N3: Optional vector search for lore chunk ranking
            try:
                from vector_search import VectorSearchEngine
                _lore_chunks = ctx.domain_data.get("lore_chunks", [])
                if _lore_chunks and len(_lore_chunks) > config.VECTOR_TOP_K:
                    _vs = VectorSearchEngine(self.client, config.VECTOR_EMBEDDING_MODEL)
                    _query = ctx.action_text or ""
                    _ranked = await _vs.search(_query, _lore_chunks,
                                               top_k=config.VECTOR_TOP_K,
                                               min_score=config.VECTOR_MIN_SCORE)
                    if _ranked:
                        ctx.domain_data["lore_chunks_ranked"] = _ranked
                        logger.debug(f"[VectorSearch] Ranked {len(_ranked)} chunks from {len(_lore_chunks)}")
            except Exception as _vs_err:
                logger.debug(f"[VectorSearch] unavailable: {_vs_err}")

            # 1.5. NPC decision cooldown tick (매 턴 시작 시 1씩 감소)
            npc_manager.tick_all_cooldowns(channel_id)

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
            # [!다시] 디스크 영속화 — 봇 재시작/인스턴스 재생성에도 보존 (retry_last 폴백 조회).
            try:
                import sqlite_store
                _snap_data = self._retry_snapshots[channel_id]["_data"]
                _snap_turn = (_snap_data.get("world_state", {}) or {}).get("turn_index", 0)
                sqlite_store.save_retry_snapshot(channel_id, _snap_turn, _snap_data)
            except Exception as _e_rs:
                logger.debug(f"[!다시] snapshot persist skipped: {_e_rs}")

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

                # 4.5. World State Update (scene transition + time flow)
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
                    if record_user_history:
                        domain_manager.append_history(channel_id, user_mask, ctx.action_text)
                    domain_manager.append_history(channel_id, "Model", response)
                    logger.debug(f"[History] Saved: {'skip-user + ' if not record_user_history else user_mask + ' + '}Model response ({len(response)} chars)")

                    # 8.4. [TimeSync] 모델 출력 status line 시간 → 내부 클록 동기화 (2026-05-23)
                    # G1/G2(사용자 인풋 명시) 다음 2순위. SCENE_TIME_RULES로 침묵 점프 차단.
                    # V8.5: year/month 캘린더 확장 — 절대 분 차이 계산
                    try:
                        from response_processor import parse_status_line_time
                        parsed_time = parse_status_line_time(response)
                        if parsed_time:
                            _world = domain_manager.get_world_state(channel_id)
                            from game_world import _init_clock as _ic
                            _ic(_world)
                            cur_y = _world.get("year", 1)
                            cur_mo = _world.get("month", 1)
                            cur_d = _world.get("day", 1)
                            cur_h = _world.get("hour", 12)
                            cur_m = _world.get("minute", 0)
                            new_y = parsed_time["year"]
                            new_mo = parsed_time["month"]
                            new_day = parsed_time["day"]
                            new_h = parsed_time["hour"]
                            new_m = parsed_time["minute"]
                            # V8.5: year/month/day 절대 분 차이 (1년=360일=518400분, 1달=30일=43200분, 1일=1440분)
                            cur_abs = ((cur_y - 1) * 360 + (cur_mo - 1) * 30 + (cur_d - 1)) * 1440 + cur_h * 60 + cur_m
                            new_abs = ((new_y - 1) * 360 + (new_mo - 1) * 30 + (new_day - 1)) * 1440 + new_h * 60 + new_m
                            delta_min = new_abs - cur_abs
                            if delta_min < 0:
                                logger.warning(
                                    f"[TimeSync] Negative delta blocked: world={cur_y}/{cur_mo}/{cur_d} {cur_h:02d}:{cur_m:02d} → status={new_y}/{new_mo}/{new_day} {new_h:02d}:{new_m:02d}"
                                )
                            elif delta_min > 0:
                                # SCENE_TIME_RULES 클램프 (1 tick = 2분)
                                _scene = ctx.scene_type or _world.get("current_scene_type", "normal")
                                _rules = config.SCENE_TIME_RULES.get(_scene, config.SCENE_TIME_RULES["normal"])
                                max_min = _rules.get("max_ticks", 2) * 2
                                # [2026-06-12] 명시 Decree 턴은 선언량+여유까지 허용 (이중 안전망 —
                                # TimeFlow가 이미 선행 적용했으면 delta는 작아서 무해)
                                _decree = getattr(ctx, "time_decree_min", 0) or 0
                                if _decree:
                                    max_min = max(max_min, _decree + 30)
                                if delta_min > max_min:
                                    logger.info(
                                        f"[TimeSync] Clamped {delta_min}→{max_min}min (scene={_scene}, status={new_h:02d}:{new_m:02d})"
                                    )
                                    delta_min = max_min
                                # advance_minutes로 자연 진행 (day wrap 포함)
                                from game_world import advance_minutes as _adv
                                _adv(channel_id, delta_min)
                                logger.info(
                                    f"[TimeSync] world {cur_h:02d}:{cur_m:02d} → status {new_h:02d}:{new_m:02d} ({delta_min}min applied)"
                                )
                    except Exception as _e_ts:
                        logger.debug(f"[TimeSync] skipped: {_e_ts}")

                    # 8.5. Dialogue Format Feedback + Style Detectors (다음 턴 피드백용)
                    _pc_names_for_fmt = [user_mask] if user_mask and user_mask != "Unknown" else []
                    fmt_feedback = _check_dialogue_format(response, pc_names=_pc_names_for_fmt, user_input=ctx.action_text or "")
                    from response_processor import (
                        detect_cliche_patterns, detect_cargo_patterns,
                        detect_sensory_repetition, detect_pidgin_echo,
                        detect_structural_repetition, detect_tension_dissolution,
                        detect_deflection_repetition,
                        # A7: HALLABONG Gemini-cliché detectors
                        detect_arrival_patterns, detect_declaration_patterns,
                        detect_explain_then_render_patterns, detect_vending_patterns,
                        detect_premature_closure,
                    )
                    cliche_fb = detect_cliche_patterns(response)
                    # [2026-06-12] 앙상블 보정: 장면 NPC 수 전달 (분산 아닌 분배 — 다인 장면 반응 나열은 내용)
                    _scene_npc_n = len(set(
                        list((ctx.dai.get("npc_attitudes") or {}).keys())
                        + list((ctx.dai.get("psyche_states") or {}).keys())
                    )) if ctx.dai else 1
                    cargo_fb = detect_cargo_patterns(response, scene_npc_count=max(1, _scene_npc_n))
                    # A7: HALLABONG 4-pattern detection
                    arrival_fb = detect_arrival_patterns(response)
                    declaration_fb = detect_declaration_patterns(response)
                    explain_render_fb = detect_explain_then_render_patterns(response)
                    vending_fb = detect_vending_patterns(response)

                    # CLOSURE: 조기 종결 검출 (2026-07-06 감사 — 검수 함대 유일 미배선분 합류).
                    # proximity=doom 챕터 페이즈(結/間=정당한 종결 창), open_threads=직전 프레임 render_fingerprint.unresolved.
                    closure_fb = ""
                    try:
                        _bus_for_cl = getattr(ctx, "shared_bus", None) or getattr(ctx, "bus", None)
                        _doom_phase_cl = ""
                        if _bus_for_cl is not None and isinstance(getattr(_bus_for_cl, "doom", None), dict):
                            _doom_phase_cl = _bus_for_cl.doom.get("chapter_phase", "")
                        _closure_prox = {"結": 80, "間": 75, "轉": 55}.get(_doom_phase_cl, 30)
                        _prev_unresolved = (
                            domain_manager.get_latest_frame(channel_id)
                            .get("render_fingerprint", {}).get("unresolved") or []
                        )
                        if not isinstance(_prev_unresolved, list):
                            _prev_unresolved = []
                        closure_fb = detect_premature_closure(
                            response, conclusion_proximity=_closure_prox, open_threads=_prev_unresolved
                        )
                    except Exception as _e_cl:
                        logger.warning(f"[Closure] skipped: {_e_cl}")

                    # Sensory Rotation: rolling window 3턴
                    _mem_for_fb = domain_manager.get_session_ai_memory(channel_id)
                    _recent_parts = _mem_for_fb.get("recent_body_parts", [])
                    if not isinstance(_recent_parts, list):
                        _recent_parts = []
                    rotation_fb, _current_parts = detect_sensory_repetition(response, _recent_parts)
                    # Rolling window 업데이트 (최근 3턴 유지)
                    _recent_parts.append(_current_parts)
                    if len(_recent_parts) > 3:
                        _recent_parts = _recent_parts[-3:]

                    # Pidgin Echo: scene NPC label keywords
                    _scene_npcs = list(npc_manager.get_scene_npc_names(channel_id))
                    _npc_keywords = npc_manager.get_npc_label_keywords(channel_id, _scene_npcs) if _scene_npcs else {}
                    pidgin_fb = detect_pidgin_echo(response, _npc_keywords)

                    # P2: Structural repetition detection (opening/closing 3턴 연속 반복)
                    _recent_openings = _mem_for_fb.get("recent_openings", [])
                    _recent_closings = _mem_for_fb.get("recent_closings", [])
                    if not isinstance(_recent_openings, list):
                        _recent_openings = []
                    if not isinstance(_recent_closings, list):
                        _recent_closings = []
                    struct_fb, _opening_type, _closing_type = detect_structural_repetition(
                        response, _recent_openings, _recent_closings
                    )

                    # P3: Tension dissolution detection
                    _tension_fb_list = detect_tension_dissolution(response)
                    tension_fb = (
                        "[TENSION: " + "; ".join(f"{name}: {text}" for name, text in _tension_fb_list)
                        + " · the conflict stays unresolved; the friction holds]"
                    ) if _tension_fb_list else ""

                    # P3: Deflection repetition detection (NPC 회피기법 반복)
                    _recent_deflections = _mem_for_fb.get("recent_deflections", [])
                    if not isinstance(_recent_deflections, list):
                        _recent_deflections = []
                    deflection_fb, _current_deflections = detect_deflection_repetition(
                        response, _recent_deflections
                    )

                    # L축(한글 저점): log-only 관측 — 검출은 사람한테 알리는 관측이지 쓰기-제어 아님.
                    # 프롬프트 측은 KOREAN SENTENCE DOCTRINE이 직접 담당. (position-2 승격 2026-06-16 시도→철회:
                    # 검출기 임계가 골드(산문2)도 잡아 자동주입 시 자연 한국어 과교정 위험. 검출↔쓰기 분리.)
                    try:
                        from response_processor import detect_korean_floor
                        _kf_fb, _kf_stats = detect_korean_floor(response)
                        if _kf_fb:
                            logger.info(f"[KoreanFloor] {_kf_fb} {_kf_stats}")
                    except Exception as _e_kf:
                        logger.warning(f"[KoreanFloor] skipped: {_e_kf}")

                    # 숫자·계측 집착(deepseek 백스톱): log-only 관측. 프롬 PROSE_CRAFT/MATURE가 교정.
                    try:
                        from response_processor import detect_number_fixation
                        _nf_fb, _nf_stats = detect_number_fixation(response)
                        if _nf_fb:
                            logger.info(f"[NumberFixation] {_nf_fb} {_nf_stats}")
                    except Exception as _e_nf:
                        logger.warning(f"[NumberFixation] skipped: {_e_nf}")

                    # I축(재정착): verbatim 후렴 재발 → _ce_fb 넛지를 style_fb로 다음턴 주입(CADENCE_ECHO_INJECT). 윈도우 영속.
                    _ce_fb = ""
                    _ce_window = None
                    try:
                        from response_processor import detect_cadence_echo
                        _ce_recent = _mem_for_fb.get("recent_cadence_sents", [])
                        if not isinstance(_ce_recent, list):
                            _ce_recent = []
                        _ce_fb, _ce_cur = detect_cadence_echo(response, _ce_recent)
                        if _ce_fb:
                            logger.info(f"[CadenceEcho] {_ce_fb}")
                        _ce_window = (_ce_recent + _ce_cur)[-180:]
                    except Exception as _e_ce:
                        logger.warning(f"[CadenceEcho] skipped: {_e_ce}")

                    # 미완 발화 클리셰(입술 열림/말 안나옴 류): log-only 관측. 프롬 SILENT COMPLIANCE가 실제 교정.
                    # recurrence(최근 5턴 중 등장)가 진짜 신호 — 단발은 적절할 수 있음.
                    _as_window = None
                    try:
                        from response_processor import detect_aborted_speech
                        _as_hits = detect_aborted_speech(response)
                        _as_recent = _mem_for_fb.get("recent_aborted_speech", [])
                        if not isinstance(_as_recent, list):
                            _as_recent = []
                        _as_window = (_as_recent + [1 if _as_hits else 0])[-5:]
                        if _as_hits:
                            _as_labels = ", ".join(sorted({lbl for lbl, _ in _as_hits}))
                            logger.info(f"[AbortedSpeech] {len(_as_hits)} hit(s) [{_as_labels}] · recurrence {sum(_as_window)}/5"
                                        + (" HIGH" if sum(_as_window) >= 3 else ""))
                    except Exception as _e_as:
                        logger.warning(f"[AbortedSpeech] skipped: {_e_as}")

                    style_fb = " ".join(filter(None, [
                        cliche_fb, cargo_fb, rotation_fb, pidgin_fb,
                        struct_fb, tension_fb, deflection_fb,
                        arrival_fb, declaration_fb, explain_render_fb, vending_fb,
                        closure_fb,
                        (_ce_fb if config.CADENCE_ECHO_INJECT else None),
                    ]))
                    if style_fb:
                        fmt_feedback = f"{fmt_feedback} {style_fb}".strip() if fmt_feedback else style_fb

                    # Save structural tracking data to session memory
                    _tracking_update = {
                        "format_feedback": fmt_feedback,
                        "recent_body_parts": _recent_parts,
                        "recent_openings": (_recent_openings + [_opening_type])[-3:],
                        "recent_closings": (_recent_closings + [_closing_type])[-3:],
                    }
                    if _current_deflections:
                        _tracking_update["recent_deflections"] = (_recent_deflections + _current_deflections)[-6:]
                    if _ce_window is not None:
                        _tracking_update["recent_cadence_sents"] = _ce_window
                    if _as_window is not None:
                        _tracking_update["recent_aborted_speech"] = _as_window
                    domain_manager.update_session_ai_memory(channel_id, _tracking_update)
                    if fmt_feedback:
                        logger.info(f"[FormatCheck] {fmt_feedback[:80]}")

                    # 9. Background Extraction (Flash 모델로 별도 API 호출)
                    # V4 Inline Extraction 대신 기존 Background Extraction 복원
                    await self.schedule_background_extraction(ctx, response, message)

                    # 9.5. World Board (event-driven, 백그라운드)
                    try:
                        import world_board
                        if isinstance(message.channel, discord.TextChannel):
                            _board_dai = dict(ctx.dai) if ctx.dai else {}
                            asyncio.create_task(world_board.trigger_board_update(
                                message.channel, self.client,
                                config.MODEL_ID_FLASH, channel_id,
                                trigger="turn",
                                dai=_board_dai,
                            ))
                    except Exception:
                        pass

                    # 9.6. [C안 2026-07-02] 영속층 감사 — N턴마다 백그라운드 (log-only, 검출≠쓰기).
                    # knowledge/relations/world_tree 자동 적립분의 모순·중복·고아·출처불명 검출.
                    try:
                        _pa_interval = getattr(config, "PERSIST_AUDIT_INTERVAL", 0)
                        _pa_turn = int(domain_manager.get_world_state(channel_id).get("turn_index", 0) or 0)
                        if _pa_interval > 0 and _pa_turn > 0 and _pa_turn % _pa_interval == 0:
                            import persistent_audit as _pa_mod

                            async def _run_persist_audit():
                                await _pa_mod.run_persistent_audit(self.client, self.model_id_flash, channel_id)

                            await enqueue_background_task(
                                channel_id, "PersistentAudit", _run_persist_audit,
                                priority=TaskPriority.LOW,
                            )
                            logger.info(f"[PersistAudit] enqueued at turn {_pa_turn}")
                    except Exception as _e_pa:
                        logger.debug(f"[PersistAudit] enqueue skip: {_e_pa}")

                    # 9.7. [Reader-GM Stage 0, 2026-07-05] 서브 GM 독자 — blind read(텔레스코프+산문만),
                    # log-only 적립(reader_log). 프롬 급식 없음. async 지연 0. 스펙: trait_playbook §4 R1.
                    try:
                        _rg_interval = getattr(config, "READER_GM_INTERVAL", 0)
                        _rg_turn = int(domain_manager.get_world_state(channel_id).get("turn_index", 0) or 0)
                        if _rg_interval > 0 and _rg_turn > 0 and _rg_turn % _rg_interval == 0:
                            import reader_gm as _rg_mod
                            _rg_prose = response
                            _rg_block = getattr(ctx, "telescope_raw_block", "") or ""

                            async def _run_reader_gm():
                                await _rg_mod.run_reader(
                                    self.client, channel_id, _rg_turn, _rg_prose, _rg_block)

                            await enqueue_background_task(
                                channel_id, "ReaderGM", _run_reader_gm,
                                priority=TaskPriority.LOW,
                            )
                    except Exception as _e_rg:
                        logger.debug(f"[Reader] enqueue skip: {_e_rg}")

                    # 9.8. [Reader-GM Stage 3-A] 間 진입 엣지 → 수신형 시드 번역 (배경 LOW, 1회/진입).
                    try:
                        if getattr(config, "READER_GM_SEED", 0):
                            _interm_now = bool(ctx.shared_bus.doom.get("intermission_active"))
                            _mem_rs = domain_manager.get_session_ai_memory(channel_id) or {}
                            _interm_prev = bool(_mem_rs.get("_reader_seed_interm_prev"))
                            if _interm_now != _interm_prev:
                                domain_manager.update_session_ai_memory(
                                    channel_id, {"_reader_seed_interm_prev": _interm_now})
                            if _interm_now and not _interm_prev:
                                import reader_gm as _rs_mod

                                async def _run_reader_seed():
                                    await _rs_mod.run_seed_replenish(self.client, channel_id)

                                await enqueue_background_task(
                                    channel_id, "ReaderSeed", _run_reader_seed,
                                    priority=TaskPriority.LOW,
                                )
                                logger.info("[ReaderSeed] enqueued (間 entry)")
                    except Exception as _e_rs:
                        logger.debug(f"[ReaderSeed] enqueue skip: {_e_rs}")
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

                # 스냅샷은 !다시용으로 유지 (다음 턴 시작 시 덮어씀)
                # 메모리 누수는 _retry_snapshots 20개 cap으로 방지 (line 1109)

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

        # 1. 백그라운드 작업 플러시 + 실행 중 태스크 완료 대기
        from background_task_queue import get_task_queue
        queue = get_task_queue()
        flushed = await queue.flush_channel(channel_id)
        if flushed:
            logger.info(f"[!다시] Flushed {flushed} pending background tasks for {channel_id}")
        # 실행 중인 태스크가 있으면 완료 대기 (save_callback 레이스 방지)
        await queue.wait_for_channel(channel_id, timeout=10.0)

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
        if snapshot is None:
            # 인메모리 miss (봇 재시작/인스턴스 재생성) → 디스크 영속본 폴백
            try:
                import sqlite_store
                snapshot = sqlite_store.read_retry_snapshot(channel_id)
                if snapshot:
                    logger.info(f"[!다시] Snapshot loaded from disk for {channel_id}")
            except Exception as _e_rs:
                logger.debug(f"[!다시] disk snapshot read skipped: {_e_rs}")
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

# [!다시] 채널별 도메인 스냅샷 — 모듈-전역(인스턴스 재생성에도 보존).
# get_orchestration_runtime이 params(client id/model) 변동 시 OrchestrationService를 재생성하는데,
# 스냅샷이 인스턴스 속성이면 그때 비워져 !다시가 "no snapshot"→history-only 폴백→시간/기력/퀘스트 미복원이던 버그 fix.
_RETRY_SNAPSHOTS = {}

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
