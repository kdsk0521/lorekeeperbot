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


# [2026-08-13 대사 포맷 부활] 혼합 계약의 판정 임계 (로컬 상수 — config 노출 안 함)
_BARE_SPEECH_QUOTE_RATIO = 0.6   # 인용부 길이가 줄의 60% 이상 = 발화가 줄을 지배
_BARE_SPEECH_TAG_TAIL = 12       # 따옴표로 여는 줄의 잔여 서술이 이 이하면 대사태그 수준 → 여전히 bare
_QUOTED_SPAN_PAT = re.compile(r'"([^"]*)"')


def _is_bare_speech_line(line: str) -> bool:
    """[2026-08-13 대사 포맷 부활] 이 줄이 '독립 대사줄'인가 (혼합 계약의 대상 판별).

    True = 따옴표 발화가 줄을 지배하는 줄 → `이름: "대사"` 형식 대상.
    False = 서술 문장 안 인용 / FID / 서술뿐인 줄 → 자유 (04-26 W12·W16 충돌 사유 존중).

    판정: (a) 인용부 총길이가 줄의 60%(_BARE_SPEECH_QUOTE_RATIO) 이상,
          또는 (b) 줄이 따옴표로 시작하고 나머지 서술이 12자(_BARE_SPEECH_TAG_TAIL) 이하.
    오탐이 미탐보다 비싸므로 둘 다 보수적으로 잡는다."""
    stripped = (line or "").strip()
    if not stripped:
        return False
    spans = [m.group(1) for m in _QUOTED_SPAN_PAT.finditer(stripped) if len(m.group(1).strip()) >= 2]
    if not spans:
        return False
    quoted_len = sum(len(s) + 2 for s in spans)  # 따옴표 두 개 포함
    if quoted_len / len(stripped) >= _BARE_SPEECH_QUOTE_RATIO:
        return True
    if stripped.startswith('"') and (len(stripped) - quoted_len) <= _BARE_SPEECH_TAG_TAIL:
        return True
    return False


def _check_dialogue_format(response: str, pc_names: list = None, user_input: str = "") -> str:
    """AI 응답에서 대사 포맷 위반을 감지하여 피드백 문자열 반환.
    [2026-08-13 대사 포맷 부활] 혼합 계약: 독립 대사줄(_is_bare_speech_line)만 `이름: "대사"` 대상.
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

        # [2026-08-13 대사 포맷 부활] 전면 강제 → 독립 대사줄 한정
        if _is_bare_speech_line(stripped) and not correct_pat.match(stripped):
            violations.append(stripped[:40])

    # [2026-08-13 기본형 승격 — 합법 우회 차단] 혼합 계약("bare면 prefix")은 조건문이라
    # 대사를 **전부 서술에 녹이면** bare 줄이 안 생겨 우회됐다(레티어스 "안 지켜진다" 실관측).
    # 계약을 기본형-긍정(발화=자기 줄+이름:이 기본, 녹임=의도적 예외)으로 승격했으므로,
    # 대사가 여럿(3+)인데 이름: 줄이 0이면 기본형 미준수. 낱개 woven은 정당한 선택 — 임계로 보호.
    _named_lines = 0
    _total_quotes = 0
    for line in lines:
        s = line.strip()
        if not s or system_pat.match(s) or tag_pat.match(s):
            continue
        if _pc_pats and any(p.match(s) for p in _pc_pats):
            continue
        if correct_pat.match(s):
            _named_lines += 1
        _total_quotes += len(quote_pat.findall(s))

    parts = []
    # [FORMAT] 경위: 2026-04-26 전면 강제(kimi 시절 "Every spoken line MUST follow")가
    #   W12 Three Chairs / W16 FID 표현과 충돌 → 피드백 주입 중단(검출만 유지).
    #   [2026-08-13 대사 포맷 부활] 충돌을 혼합 계약으로 해소하고 재활성:
    #   독립 대사줄만 `이름: "대사"`(멀티플레이 화자 가독), 서술 안 인용·FID는 그대로 자유.
    if violations:
        examples = violations[:2]
        parts.append(
            f"[FORMAT] 화자 없는 독립 대사줄 {len(violations)}건. 예: {'; '.join(examples)}. "
            f"bare speech lines open 이름: \"대사\"; quotes inside narration stay free."
        )
    elif _named_lines == 0 and _total_quotes >= 3:
        # [2026-08-13 기본형 승격] 전량 서술 삽입형 = 기본형 우회. bare 위반과 동시 점등 방지(elif).
        parts.append(
            f"[FORMAT] 대사 {_total_quotes}건 전부 서술 삽입형(이름: 줄 0) — "
            f"spoken exchange defaults to its own line opening 이름: \"대사\"; weaving stays the deliberate exception."
        )
    if impersonations:
        imp_examples = impersonations[:2]
        parts.append(f"[IMPERSONATION] PC 대사 창작 {len(impersonations)}건: {'; '.join(imp_examples)}. PC의 대사를 만들지 마라 — 유저가 입력한 대사만 재현하라.")
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
        # [F2 2026-07-18] 회상 벡터 캐시를 소비 직전에 현행 쿼리로 정합
        # (기존: auto_ferment 시점 쿼리로만 적립 → 소비 턴과 한 턴 이상 어긋남).
        # gather 내부의 build_fermented_context가 이 캐시를 읽는다. 실패 무해.
        try:
            await fermentation.refresh_recall_vector_cache(
                self.client, ctx.domain_data, ctx.action_text, channel_id=ctx.channel_id
            )
        except Exception as _e_f2:
            logger.debug(f"[F2] recall cache refresh skip: {_e_f2}")
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
            # [2026-07-19 PersistAudit 처방] 위치 이름 정화 — 추출이 섞어 보내는 상태 꼬리
            # ("쇼핑 애비뉴 (이동 완료)")가 그대로 domain location·world_tree 노드·presence로
            # 오염되는 것 차단. 괄호 꼬리 중 상태/진행 서술만 제거 (지명 부속 괄호는 보존).
            _loc_clean = re.sub(
                r"\s*[\(（][^)）]*(?:완료|도착|이동|하는 중|중)[\)）]\s*$", "",
                str(current_location),
            ).strip()
            if _loc_clean:
                current_location = _loc_clean
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
            # [2026-07-18 고아 승격] world_tree NPC presence 쓰기 — 장면 NPC를 현재 위치에 기록.
            # 읽기 경로는 기배선인데(S16 build_location_context_text 'NPCs present' 렌더 +
            # 오프스크린 last-known 소비) 쓰기가 0이라 통째로 사문이었다. 콜 순증 0.
            try:
                import world_tree
                import npc_manager as _npm_wt
                # [2026-07-28] get_scene_npc_names(=전체 명부) → 최근 등장 인물로 교체.
                # 구 코드는 PC가 이동할 때마다 **등록 NPC 전원**을 그 장소로 옮겨
                # (set_npc_location은 기존 위치에서 제거 후 재배치) 위치 기록을 매번 오염시켰다.
                _scene_names = _npm_wt.get_onstage_npc_names(channel_id, within_turns=1) or []
                _placed = sum(
                    1 for _sn in _scene_names
                    if _sn and world_tree.set_npc_location(channel_id, _sn, current_location) == "placed"
                )
                if _placed:
                    logger.debug(f"[WorldTree] presence: {_placed} NPC @ {current_location}")
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
            # [2026-08-11 사망 파이프라인] 자동 재등록 게이트 ① — 태도 채널.
            #   여기는 "모르는 이름이면 스텁을 만든다"는 자리라, 죽은 인물 이름이 분석에
            #   다시 뜨면 태도·깊이가 시체 위에 계속 적립된다. dead면 채널 전체에서 뺀다
            #   (아래 두 루프가 같은 dict를 도므로 입구 한 곳에서 거른다).
            #   ★down은 거르지 않는다 — 쓰러진 인물도 관측 대상이고, 되살아나는 경로는
            #     mark_npc_appearance(등장 관측의 단일 관문)가 따로 쥐고 있다.
            _dead_att = [n for n in new_attitudes
                         if npc_manager.get_npc_status(
                             npc_manager.get_npc(channel_id, n) or {}) == "dead"]
            if _dead_att:
                new_attitudes = {k: v for k, v in new_attitudes.items() if k not in _dead_att}
                logger.info(f"[NPC Status] dead 태도 갱신 차단(환각 등장 신호): {', '.join(_dead_att)}")
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
                        # origin=감사 라벨 전용(캡 미발동 — source와 분리된 인자)
                        domain_manager.update_helena_metric(channel_id, n_name, depth_delta=depth_delta,
                                                            origin="trajectory")

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
                            tension_delta=initial_tension,
                            origin="npc_sheet_initial",
                        )

            ctx.existing_attitudes = domain_manager.get_npc_attitudes(channel_id)

        # NPC Knowledge 영속화
        new_knowledge = dai.get("npc_knowledge")
        if new_knowledge and isinstance(new_knowledge, dict):
            _atts_for_ledger = domain_manager.get_npc_attitudes(channel_id) or {}
            # [2026-07-19 PC 혼입 가드] 지식 '보유자' 키에 PC가 오면 스킵 — PC 지식은 플레이어
            # 소관, 영속 시 PC 이름의 지식 엔트리가 자라남 (07-13 npc_attitudes 가드·
            # PersistAudit PC 명부 수리와 같은 병 계열: LLM 산출 이름의 PC/NPC 미구분).
            _pc_masks_k = set()
            try:
                for _p in domain_manager.get_domain(channel_id).get("participants", {}).values():
                    if isinstance(_p, dict) and _p.get("mask"):
                        _pc_masks_k.add(_p["mask"])
            except Exception:
                pass
            for npc_name, k_data in new_knowledge.items():
                if not isinstance(k_data, dict):
                    continue
                if npc_name in _pc_masks_k:
                    logger.debug(f"[NPC Knowledge] PC 혼입 스킵: {npc_name}")
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
                    # [2026-07-19 프로덕션 픽스] setdefault는 키가 있고 값이 null이면 None을
                    # 반환 — LLM이 "secret_updates": null을 명시로 뱉은 턴에 TypeError.
                    # None/비리스트 전부 []로 정규화 (LLM schema pragmatism).
                    _ups = k_data.get("secret_updates")
                    if not isinstance(_ups, list):
                        _ups = []
                        k_data["secret_updates"] = _ups
                    _covered = {str(u.get("truth_ref", "")).strip().lower()
                                for u in _ups if isinstance(u, dict) and u.get("surface")}
                    for _truth, _surf in _ledger["surfaces"].items():
                        if any(c and c in _truth.lower() for c in _covered):
                            continue
                        _ups.append({"truth_ref": _truth[:60], "surface": _surf})
                if k_data.get("knows"):
                    domain_manager.update_npc_knowledge(channel_id, npc_name, k_data)
            logger.info(f"[NPC Knowledge] Persisted for {len(new_knowledge)} NPCs")

            # Knowledge Propagation: 같은 장면 NPC 간 지식 전파 ([07-19] PC 혼입 가드 동반)
            scene_npcs = [n for n in new_knowledge.keys() if n not in _pc_masks_k]
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
            "open_threads": ((dai.get("narrative_chain") or {}).get("open_threads") or [])[:5],  # [07-19] 명시 null 방어
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

        # [Flashback] 자원 차감·로드아웃·인벤토리 없음 — dai 플래그만 → Slot 30 산문 반영.
        # [2026-08-11 로드아웃 삭제] 차감/슬롯 엔진 `_process_flashback`(loadout_used 쓰기 포함) 제거.
        # 남은 건 입력의 소급 선언을 장면 연출로 옮기는 이 통로뿐 (명령 계보와 무관).
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

    def _process_item_usage(self, channel_id: str, user_id: str, item_eval: dict) -> Optional[str]:
        """아이템 소비/획득 처리. Returns system message or None."""
        consumed = item_eval.get("items_consumed") or []  # [07-19] 명시 null 방어
        gained = item_eval.get("items_gained") or []
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
                domain_manager.update_helena_metric(channel_id, target, depth_delta=depth_gain, tension_delta=0,
                                                    origin="downtime_socialize")
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
            # [2026-07-15 수리] entity_state와 같은 병 — 이 dict에 "arc" 키가 없어서
            # cognition L112 batch_sections 게이트(extraction_hints.get("arc", False))에서
            # 영구 False → arc 추출 0회. 그런데 파이프 나머지는 전부 지어져 있었다:
            # 여기서 매 턴 _arc_context_str/_arc_promote_cand를 만들어 넘기고(L870-907),
            # cognition이 ArcUpdates/ArcDecisions로 받고(L224-225), L1341에서
            # narrative_tracker.apply_arc_updates/decisions로 적용 — 입력 기아로 전부 사문화.
            # (Arc Phase 1~6 완료·226 PASS인데 라이브 미가동이었음.)
            # 배치 섹션이라 LLM 콜 순증 0. 관측: [Arc] phase_transitions/Promoted 로그 빈도.
            "arc": True,
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

    def _with_status_header(self, channel_id: str, response: str) -> str:
        """[2026-08-16 상태창 코드 조립] 표시용으로만 상태 헤더를 접합한다.

        ⚠ 반환값을 `response` 변수에 되담지 말 것 — 히스토리·검수·리더·배경 추출이
        전부 원본 `response`를 읽는다(표시/저장 분리가 이 이관의 계약이다).
        """
        try:
            header = game_world.build_status_header(channel_id)
        except Exception as _e_sh:
            logger.debug(f"[StatusHeader] skipped: {_e_sh}")
            return response
        return f"{header}\n\n{response}" if header else response

    def _panel_view(self, channel_id: str):
        """[2026-08-16 상태패널 v0] 패널 정의(!출력룰 panel/상태창)가 등록된 채널에만 💠 버튼.

        미등록 채널은 None → send_long_message 가 종전과 완전히 동일하게 동작한다.
        View 는 persistent(custom_id 고정)라 매 턴 새로 만들어 붙여도 재시작 후 main.on_ready
        의 add_view 가 콜백을 다시 잡는다.

        [2026-08-16 도착물 라우트] 합성 지점 이동 — 한 메시지에 View 는 하나뿐이라
        💠/💌/💭를 한 묶음으로 만들어야 한다. 전송 **시점**엔 message_id 가 없으므로
        (=도착물 조회 불가) 여기서 나오는 건 💠뿐이고, 💌/💭는 도착물이 실제로 생긴 뒤
        turn_mail.attach_button 이 같은 메시지를 edit 해서 붙인다(사후 부착).
        """
        try:
            import turn_mail
            return turn_mail.build_view(channel_id)
        except Exception as _e_pv:
            logger.debug(f"[StatusPanel] view skip: {_e_pv}")
            return None

    def _advance_scene_time(self, channel_id: str, ctx: ResponseContext, delta_min: int) -> None:
        """[2026-08-16 상태창 코드 조립] 이번 턴 산문 경과 분을 세계 시계에 반영.

        구 TimeSync(모델 상태줄 정규식 되읽기)에서 **입력원만** 갈아끼운 것 —
        SCENE_TIME_RULES 클램프와 Decree 이중 안전망은 그대로 옮겨 왔다.
        G1/G2(사용자 인풋 명시 선언) 다음 2순위, 침묵 점프 차단.
        """
        try:
            delta_min = int(delta_min)
        except (TypeError, ValueError):
            return
        if delta_min <= 0:
            return
        try:
            _world = domain_manager.get_world_state(channel_id)
            _scene = getattr(ctx, "scene_type", "") or _world.get("current_scene_type", "normal")
            _rules = config.SCENE_TIME_RULES.get(_scene, config.SCENE_TIME_RULES["normal"])
            max_min = _rules.get("max_ticks", 2) * 2   # 1 tick = 2분
            # [2026-06-12] 명시 Decree 턴은 선언량+여유까지 허용 (이중 안전망 —
            # TimeFlow가 이미 선행 적용했으면 delta는 작아서 무해)
            _decree = getattr(ctx, "time_decree_min", 0) or 0
            if _decree:
                max_min = max(max_min, _decree + 30)
            if delta_min > max_min:
                logger.info(f"[TimeSync] Clamped {delta_min}→{max_min}min (scene={_scene})")
                delta_min = max_min
            # advance_minutes로 자연 진행 (day wrap 포함)
            from game_world import advance_minutes as _adv
            _adv(channel_id, delta_min)
            logger.info(f"[TimeSync] scene_minutes_elapsed → {delta_min}min applied (scene={_scene})")
        except Exception as _e_ts:
            logger.debug(f"[TimeSync] skipped: {_e_ts}")

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
            # [2026-08-12 fingerprint 프레임 소급] 이 dict는 dai_snapshot(frames[-1]이 정답)과
            #   지문(직전에 실제로 찍힌 프레임이 정답)이 섞여 있다. 지문 쪽은 frames[-1]=이번 턴 빈
            #   프레임이라 cognition의 이전값 참조(Lighting/Palette/Rhythm/…)가 상시 공백이었다.
            #   **지문만** 공용 관문으로 교체 — dai_snapshot 경로는 무변경.
            prev_continuity["render_fingerprint"] = domain_manager.get_prev_fingerprint(channel_id)
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
                            # [C1] LLM(cognition npc_depth_hints) 제안 → 선언 범위로 캡
                            domain_manager.update_helena_metric(
                                channel_id, npc_name,
                                depth_delta=int(d_d), tension_delta=int(t_d),
                                source="helena.cognition",
                            )

            # [2026-08-02 C축] npc_drive — LLM은 **단계 이름만** 낸다.
            #   수치가 없으므로 cap_llm_delta가 아니라 set_drive_gated가 클램프한다
            #   (쿨다운 + ±1단계 / 해소는 면제·다단 하강). 콜 순증 0 — 같은 추출 콜에 필드만 얹음.
            _drive_hints = updates.get("npc_drive")
            if isinstance(_drive_hints, dict) and _drive_hints:
                try:
                    _ws_dr = domain_manager.get_world_state(channel_id) or {}
                    _turn_dr = int(_ws_dr.get("turn_index", 0) or 0)
                    for _npc_n, _dv in _drive_hints.items():
                        if not isinstance(_dv, dict):
                            continue
                        _stage = str(_dv.get("stage", "") or "").lower().strip()
                        if not _stage:
                            continue
                        npc_manager.set_drive_gated(
                            channel_id, _npc_n, _stage, _turn_dr,
                            axis=str(_dv.get("axis", "lust") or "lust").lower().strip(),
                            released=bool(_dv.get("released")),
                            reason="cognition.npc_drive",
                        )
                except Exception as _e_dr:
                    logger.debug(f"[Drive] hint 처리 skip: {_e_dr}")

            if npc_depth and isinstance(npc_depth, dict):
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

            # [2026-08-02 A축 감쇠] PC↔NPC 관계(depth/tension)도 같은 자리에서 점감.
            #   NPC↔NPC(위)만 흐려지고 정작 PC 관계는 단조 누적이라 **한 번 오른 값이
            #   절대 안 내려왔다**. 시계는 등장 기록(_last_appear_turn) — 안 만나면 식는다.
            #   같은 턴-종료 지점에 두어 매 턴 도는 루프를 하나로 유지한다.
            try:
                domain_manager.decay_stale_relations(channel_id, _turn_d)
            except Exception as _e_rd:
                logger.debug(f"[RelationDecay] skip: {_e_rd}")

            # [2026-08-02 C축] 충동 압력 자연 하강. A축과 시계가 다르다 —
            #   A축=등장(안 만나면 식음) / C축=무변화(안 건드리면 가라앉음).
            #   압력은 만나지 않아도 스스로 내려간다.
            try:
                npc_manager.tick_drive_decay(channel_id, _turn_d)
            except Exception as _e_dd:
                logger.debug(f"[Drive] decay skip: {_e_dd}")

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
                # [2026-08-16 상태창 코드 조립] 시간 전진 — 구 status line 파싱의 후계.
                #   session memory가 아니라 world_state로 가므로 mem_updates 밖에서 처리한다.
                if wsu.get("scene_minutes_elapsed"):
                    self._advance_scene_time(channel_id, ctx, wsu["scene_minutes_elapsed"])
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
                # [2026-07-28] PC 혼입 가드 — 이건 waterfall의 DAI 정제와 **다른 콜**(후행 추출
                # extract_all_updates)의 산출이라 그 초크포인트를 안 지난다. 무가드로 두면
                # PC의 위치·건강·기분이 NPC 엔티티 로그에 영구 저장되고 Slot 7로 되돌아온다.
                if isinstance(est_data, dict) and est_data:
                    _pc_masks_est = set()
                    try:
                        for _p in domain_manager.get_domain(channel_id).get("participants", {}).values():
                            if isinstance(_p, dict) and _p.get("mask"):
                                _pc_masks_est.add(_p["mask"])
                    except Exception:
                        pass
                    if _pc_masks_est:
                        _est_hit = [n for n in est_data if n in _pc_masks_est]
                        if _est_hit:
                            est_data = {k: v for k, v in est_data.items() if k not in _pc_masks_est}
                            logger.debug(f"[EntityState] PC 혼입 제외: {', '.join(_est_hit)}")
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
                        # [2026-07-22 카드3] 이번 턴 주입된 로어 청크 라벨 — NPC 등장 턴과의 동시출현을
                        # 적립해 증류 접지 2단으로 쓴다(이름이 로어에 없는 세션 NPC의 접지 경로).
                        # 인덱스가 아니라 **라벨**로 저장: 로어 재청킹 시 인덱스는 깨지지만 라벨은 남는다.
                        _turn_labels = []
                        try:
                            _rc = (ctx.dai or {}).get("relevant_chunks", []) if ctx.dai else []
                            _lc_all = domain_manager.get_lore_chunks(channel_id) or []
                            for _i in _rc:
                                if isinstance(_i, int) and 0 <= _i < len(_lc_all):
                                    _c = _lc_all[_i]
                                    _l = str(_c.get("label", "") or "").strip() if isinstance(_c, dict) else ""
                                    if _l:
                                        _turn_labels.append(_l)
                        except Exception:
                            _turn_labels = []

                        _changes = est_data.get("changes") if (isinstance(est_data, dict) and "changes" in est_data) else est_data
                        _pending_down = []   # [2026-08-11 사망 파이프라인] (이름, 근거) — 루프 뒤 일괄 적용
                        for _npc_name, _ch in (_changes.items() if isinstance(_changes, dict) else []):
                            if not isinstance(_ch, dict):
                                continue
                            # [2026-07-18 이름 획득 배선] 경비병 #2A가 "한스"를 얻는 순간 —
                            # handle_identity_reveal 고아 출구 배선 (구명=aliases 보존, 태도·위치 이관).
                            # 가드: PC 마스크·자기 자신·로어 NPC·기존 타 엔티티 충돌(→!npc 병합 후보).
                            _named = _ch.get("named_as")
                            if _named and str(_named).strip():
                                _new_nm = str(_named).strip()
                                try:
                                    _src_np = npc_manager.get_npc(channel_id, _npc_name)
                                    if (_new_nm != _npc_name and _new_nm not in _pc_masks
                                            and _src_np
                                            and str(_src_np.get("source", "")).lower() != "lore"):
                                        if npc_manager.get_npc(channel_id, _new_nm):
                                            logger.info(f"[NPC Naming] skip: '{_new_nm}' 기존 엔티티 (!npc 병합 후보)")
                                        else:
                                            npc_manager.handle_identity_reveal(
                                                channel_id, _npc_name, _new_nm,
                                                reason="explicit naming in scene")
                                            logger.info(f"[NPC Naming] {_npc_name} → {_new_nm}")
                                            _npc_name = _new_nm  # 이후 관찰 누적은 새 이름으로
                                except Exception as _e_nm:
                                    logger.debug(f"[NPC Naming] skip: {_e_nm}")
                            # [2026-08-11 사망 파이프라인] 자동 재등록 게이트 ② — entity_state 채널.
                            #   dead면 이 엔트리 전체를 버린다(스텁 생성·관찰 누적·몹 태그 전부).
                            #   로그를 남기는 이유: 죽은 이름이 장면에 다시 뜬 것 자체가 관측 재료다.
                            if npc_manager.get_npc_status(
                                    npc_manager.get_npc(channel_id, _npc_name) or {}) == "dead":
                                logger.info(f"[NPC Status] entity_state에 dead '{_npc_name}' — "
                                            "재등록·관찰 누적 차단 (환각 등장 신호)")
                                continue
                            # [2026-08-11 사망 파이프라인] 무력화 관측 → down(가역).
                            #   자동 경로는 여기까지만 만들 수 있다. dead 확정은 수동 명령뿐이고,
                            #   근거(evidence)가 비면 관문이 거부한다 — 계약이 느슨하면
                            #   "필드는 있는데 트리거가 없다"의 역방향(날조 승격)이 된다.
                            #   ★쓰기는 **루프 뒤로 미룬다**: 이 자리에서 쓰면 아래 스텁 생성
                            #     (`status:"active"`)이 같은 턴에 덮고, 첫 등장에서 쓰러진 인물은
                            #     레코드가 아직 없어 관문이 rejected_invalid로 버린다.
                            _inc = _ch.get("incapacitated")
                            if (isinstance(_inc, dict) and _inc.get("value")
                                    and _npc_name not in _pc_masks):
                                _inc_ev = str(_inc.get("evidence") or "").strip()
                                if _inc_ev:
                                    _pending_down.append((_npc_name, _inc_ev))
                                else:
                                    logger.info(f"[NPC Status] {_npc_name}: incapacitated 근거 없음 — 무효")
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
                                    # [카드3] 탄생 턴의 로어 청크 = 이 인물이 태어난 세계 좌표
                                    "lore_seen": {_l: 1 for _l in _turn_labels},
                                })
                                logger.info(f"[NPC Sheet] 즉석 NPC 생성: {_npc_name}"
                                            + (f" (lore: {','.join(_turn_labels[:3])})" if _turn_labels else ""))
                                continue
                            _src = str(_existing.get("source", "")).lower()
                            # [2026-07-28] `_src != "lore"` → FROZEN_SOURCES(lore+manual).
                            # 근거는 **"등록된 NPC는 무조건 고유 인물"**이라는 운영 원칙이다
                            # (레티어스 확인: `!npc추가`를 몹 템플릿으로 쓰지 않는다).
                            # 고유 인물에게 동명 별개체(`경비병 #4F`)가 붙을 이유가 없다.
                            # ※ 옆의 두 게이트(증류 재작성 L1294 / 면모 대체 npc_manager L1164)도
                            #   lore+manual을 함께 배제하지만 **성격은 다르다** — 그 둘은 원본 시트를
                            #   덮는 것을 막는 것이고, 몹 태그는 원본을 안 건드리고 새 엔티티를 만든다.
                            #   형태가 같다고 같은 이유로 묶지 말 것(이 배제의 근거는 위의 운영 원칙뿐).
                            if (_new_indiv and _src not in npc_manager.FROZEN_SOURCES
                                    and not npc_manager.is_mob_tag(_npc_name)):
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
                            # [카드3] 동시출현 청크 라벨 적립(빈도) — 상위 8개만 유지
                            if _turn_labels:
                                _seen = dict(_merged.get("lore_seen") or {})
                                for _l in _turn_labels:
                                    _seen[_l] = int(_seen.get(_l, 0) or 0) + 1
                                _merged["lore_seen"] = dict(
                                    sorted(_seen.items(), key=lambda kv: (-kv[1], kv[0]))[:8])
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
                                        # [2026-07-22 카드3] 접지 3단 + 규칙부 — 구 이름-리터럴 단독은
                                        # 세션 NPC(모델이 방금 지은 이름)에서 영구 미스라 사문화였다.
                                        # [2026-07-28] 3단이 임베딩 의미 유사도로 승격 — client 전달.
                                        # 공용 엔진 캐시 공유(위 L1559 로어 랭킹과 동일 청크) → 순증은 쿼리 1건.
                                        _lore_ref = await npc_manager.build_distill_grounding(
                                            channel_id, _npc_name,
                                            aliases=_existing.get("aliases") or [],
                                            observations=_obs,
                                            seen_labels=_existing.get("lore_seen") or {},
                                            client=self.client,
                                        )
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
                        # [2026-08-11 사망 파이프라인] 무력화 관측 일괄 적용 — 시트 쓰기가 전부 끝난 뒤.
                        #   이 순서라야 (a) 방금 생성된 즉석 NPC도 down이 되고
                        #   (b) 스텁의 `status:"active"`가 이번 턴 관측을 되돌리지 않는다.
                        for _dn, _dev in _pending_down:
                            npc_manager.set_npc_status_gated(
                                channel_id, _dn, "down", source="extraction",
                                evidence=_dev, current_turn=turn_idx)
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
                # [2026-08-12 출력파생 §8] withholding_scheme 추가 — Flash가 생산(cognition:462,469)하고
                # 소비자 2곳(slot_manager rotation / iceberg.translate_prev_scheme)이 대기 중인데
                # 화이트리스트에 키가 없어 저장 시 버려지고 있었음(끊긴 배선).
                fingerprint = {k: rfp.get(k, "") for k in ("gaze", "lighting", "palette", "rhythm", "temporal_density", "withholding_scheme")}
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
                from vector_search import get_shared_engine
                _lore_chunks = ctx.domain_data.get("lore_chunks", [])
                if _lore_chunks and len(_lore_chunks) > config.VECTOR_TOP_K:
                    # [2026-07-28] 매턴 새 인스턴스 → 공용 싱글턴. 구 코드는 인스턴스 로컬
                    # _cache가 턴마다 즉사해 **로어 청크 전량을 매턴 재임베딩**하고 있었다.
                    # 이제 청크 벡터는 1회, 매턴 과금은 쿼리 1건 + 증류 접지와 캐시 공유.
                    _vs = get_shared_engine(self.client)
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
            # [2026-08-12 !다시 유령 정리] SQLite 로그 워터마크 동봉 — 도메인(JSON)만 되돌아가고
            # append 로그(reader_log·dai_logs·emotion_log·…)는 무접촉이라 폐기 턴 행이 유령으로
            # 남았다. 여기서 max(id)를 찍어 두고 retry_last가 복원 직후 초과분을 트림한다.
            try:
                import sqlite_store as _ss_wm
                _log_marks = _ss_wm.snapshot_log_watermarks(channel_id)
            except Exception as _e_wm:
                _log_marks = {}
                logger.debug(f"[!다시] watermark skip: {_e_wm}")
            self._retry_snapshots[channel_id] = {
                "_ts": time.time(),
                "_data": copy.deepcopy(domain_manager.get_domain(channel_id)),
                "_marks": _log_marks,
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
                sqlite_store.save_retry_snapshot(channel_id, _snap_turn, _snap_data, marks=_log_marks)
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

                # [2026-08-12 출력파생 §8] 렌더 실패 안내는 유저에게만 — 산문 파이프라인에서 배제.
                #   구 동작: 안내 문자열이 `if response:`를 통과해 히스토리 적립·검출기·배경 추출
                #   입력까지 오염(§7-11). 여기서 None으로 낮추면 아래 else가 종전 실패 경로를 탄다.
                if persona.is_render_failure(response):
                    logger.warning("[Render] 폴백 안내 반환 — 히스토리·검출기·배경콜 전량 스킵")
                    _fail_msg = await message.channel.send(response)
                    # 안내 메시지도 !다시 정리 대상으로 유지(종전 동작: 안내가 응답 자리를 차지해
                    # message_ids/has_response에 실렸다 — 안내 문구가 "다시 시도"를 권하므로 재시도 경로 보존).
                    if _fail_msg:
                        current_retry_ctx["message_ids"].append(_fail_msg.id)
                        current_retry_ctx["has_response"] = True
                    response = None

                if response:
                    # [UI Feedback] 완료 시 안내 메시지 삭제
                    if feedback_msg:
                        try:
                            await feedback_msg.delete()
                        except Exception:
                            pass # 이미 삭제되었거나 권한 부족 시 무시

                    # 7. Send Response
                    # [2026-08-16 상태창 코드 조립] 헤더는 **표시 계층에서만** 붙는다.
                    #   `response` 변수는 손대지 않는다 — 아래 append_history·검출기 함대·
                    #   배경 추출·리더가 전부 이 변수를 쓰므로, 오염시키면 기계 표기가 히스토리에
                    #   되돌아가 에코 소스가 된다(이관의 부수 목표가 히스토리 순수화).
                    #   [2026-08-16 상태패널 v0] 💠 버튼은 **메인 경로만** v0 (배치·관찰 경로 무접촉).
                    sent_msgs = await bot_utils.send_long_message(
                        message.channel, self._with_status_header(channel_id, response),
                        view=self._panel_view(channel_id),
                    )

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

                    # 8.4. ⚰[2026-08-16 상태창 코드 조립] 구 TimeSync(모델 상태줄 정규식 되읽기) 삭제.
                    #   상태창을 코드가 그리게 되면서 파싱 대상 자체가 없어졌다. 시간 전진은
                    #   배경 추출 world_state.scene_minutes_elapsed → _advance_scene_time으로 이관
                    #   (클램프·Decree 안전망은 그 함수에 그대로 이사했다). 구 파싱도 렌더 후 실행이라
                    #   타이밍 등가 — 반영이 다음 턴 프롬프트 전이면 충분하다.

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
                        # [2026-08-03 합류점] 가족 처방 1회 병합 + 로그용 태그 요약
                        merge_style_feedback, style_feedback_tags,
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

                    # [2026-08-02] 형용사 나열 관측 — log-only, 처방 없음.
                    #   실관측 "임상적이고, 따뜻하고, 사무적이었다"는 시트 tone 필드
                    #   (한국어 묘사문)가 그대로 서술된 것. 생성 프롬프트와 Slot 33 헤더를
                    #   고쳤으나 **기존 DB tone 값은 여전히 형용사 나열**이라 빈도를 봐야 한다.
                    #   ⚠피드백에 합류시키지 않는다 — 대비 나열은 정당한 기법이다
                    #   ([[feedback_detection_not_writing]]: 검출→사람이 판독→사람이 튜닝).
                    try:
                        from response_processor import detect_adjective_stacking
                        _adj_log, _adj_n = detect_adjective_stacking(response)
                        if _adj_log:
                            logger.info(_adj_log)
                    except Exception as _e_adj:
                        logger.debug(f"[AdjStack] skip: {_e_adj}")

                    # CLOSURE: 조기 종결 검출 (2026-07-06 감사 — 검수 함대 유일 미배선분 합류).
                    # proximity=doom 챕터 페이즈(結/間=정당한 종결 창), open_threads=직전 프레임 render_fingerprint.unresolved.
                    closure_fb = ""
                    try:
                        _bus_for_cl = getattr(ctx, "shared_bus", None) or getattr(ctx, "bus", None)
                        _doom_phase_cl = ""
                        if _bus_for_cl is not None and isinstance(getattr(_bus_for_cl, "doom", None), dict):
                            _doom_phase_cl = _bus_for_cl.doom.get("chapter_phase", "")
                        _closure_prox = {"結": 80, "間": 75, "轉": 55}.get(_doom_phase_cl, 30)
                        # [2026-08-12 fingerprint 프레임 소급] get_latest_frame은 frames[-1] —
                        #   여긴 렌더 직후 동기 실행이라 이번 턴 지문은 아직 배경 추출 전이다(레이스).
                        #   지문이 실제 찍힌 최근 프레임을 공용 관문으로 읽는다.
                        _prev_unresolved = (
                            domain_manager.get_prev_fingerprint(channel_id).get("unresolved") or []
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
                    # [2026-07-22 카드2] + 재발 문장(_ce_hits)을 영속 → 다음 턴 주입본(히스토리·S31)에서 스크럽.
                    #   넛지는 "말리기", 스크럽은 "모방 대상 제거" — 후자가 이 스택의 검증된 반복 억제 계보.
                    _ce_fb = ""
                    _ce_window = None
                    _ce_hits = None
                    try:
                        from response_processor import detect_cadence_echo
                        _ce_recent = _mem_for_fb.get("recent_cadence_sents", [])
                        if not isinstance(_ce_recent, list):
                            _ce_recent = []
                        _ce_fb, _ce_cur, _ce_hits = detect_cadence_echo(response, _ce_recent)
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

                    # [2026-08-03 합류점] 종전엔 `" ".join(filter(None, [...]))`로 13종이
                    # 무순위·무캡 연결됐다. 공급자는 완전한데(13종 전부 침묵 경로 보유)
                    # 합류에 중복 제거가 없어, TELLING 4종·REPETITION 3종이 **같은 처방을
                    # 각각** 실어 날랐다(399+310자). merge_style_feedback가 라벨은 전부
                    # 보존한 채 처방만 1회로 묶는다. 순서·개수 캡은 별건(빈도 계측 후).
                    style_fb = merge_style_feedback([
                        cliche_fb, cargo_fb, rotation_fb, pidgin_fb,
                        struct_fb, tension_fb, deflection_fb,
                        arrival_fb, declaration_fb, explain_render_fb, vending_fb,
                        closure_fb,
                        (_ce_fb if config.CADENCE_ECHO_INJECT else None),
                    ])
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
                    # [카드2] 스크럽 대상 문장 — 다음 턴 주입본에서 제거. 누적 캡 12(오래된 건 자연 소멸).
                    if _ce_hits:
                        _prev_scrub = _mem_for_fb.get("echo_scrub_sents", [])
                        if not isinstance(_prev_scrub, list):
                            _prev_scrub = []
                        _tracking_update["echo_scrub_sents"] = (_prev_scrub + _ce_hits)[-12:]
                    if _as_window is not None:
                        _tracking_update["recent_aborted_speech"] = _as_window
                    domain_manager.update_session_ai_memory(channel_id, _tracking_update)
                    # [2026-08-03] journal엔 **태그만**, 전문은 verbose 로거로.
                    #   구 `fmt_feedback[:80]`은 80자에서 잘려 뒤쪽 검출기가 터져도
                    #   로그에 흔적이 없었다 — "뭐가 자주 터지나"라는 감각이 실제 빈도가
                    #   아니라 join 순서로 만들어지고 있었다. 태그 요약은 길이 무관 전량 노출.
                    if fmt_feedback:
                        logger.info(f"[FormatCheck] {style_feedback_tags(fmt_feedback)}")
                        bot_utils.vlog("FormatCheck", fmt_feedback)

                    # 9. Background Extraction (Flash 모델로 별도 API 호출)
                    # V4 Inline Extraction 대신 기존 Background Extraction 복원
                    await self.schedule_background_extraction(ctx, response, message)

                    # 9.5. World Board (event-driven, 백그라운드)
                    try:
                        import world_board
                        if isinstance(message.channel, discord.TextChannel):
                            _board_dai = dict(ctx.dai) if ctx.dai else {}
                            # [2026-08-16 도착물 라우트] 산문 메시지 id 전달 — 착지 모드가
                            #   button 인 채널종은 스레드 대신 **이 메시지**에 💌를 붙인다.
                            #   마지막 청크를 넘긴다(send_long_message 가 view 를 붙이는 청크와 동일).
                            _prose_msg = sent_msgs[-1] if sent_msgs else None
                            asyncio.create_task(world_board.trigger_board_update(
                                message.channel, self.client,
                                config.MODEL_ID_FLASH, channel_id,
                                trigger="turn",
                                dai=_board_dai,
                                prose_message=_prose_msg,
                            ))
                    except Exception:
                        pass

                    # 9.55. [2026-08-16 상태패널 v0] 하단 상태 패널 — 배경 콜 1개 + 코드 저장.
                    #   패널 정의(!출력룰 panel/상태창) 미등록이면 콜 0. 산문은 **저장본과 같은
                    #   순수 response**(표시용 헤더가 섞인 문자열이 아니다 — 헤더는 표시 계층 전용).
                    try:
                        import status_panel as _sp_mod
                        if _sp_mod.get_panel_definition(channel_id):
                            _sp_prose = response

                            async def _run_status_panel():
                                _res = await _sp_mod.generate_panel(
                                    self.client, self.model_id_flash, channel_id, _sp_prose)
                                if not _res or not _sp_mod.apply_panel_result(channel_id, _res):
                                    return
                                logger.info(
                                    "[StatusPanel] updated turn=%s fields=%d comments=%d",
                                    domain_manager.get_world_state(channel_id).get("turn_index", 0),
                                    len(_res.get("fields") or {}), len(_res.get("comments") or []))

                            await enqueue_background_task(
                                channel_id, "StatusPanel", _run_status_panel,
                                priority=TaskPriority.LOW,
                            )
                    except Exception as _e_sp:
                        logger.debug(f"[StatusPanel] enqueue skip: {_e_sp}")

                    # 9.56. [2026-08-17 속마음 v1] 💭 속마음 — **기본 on**(전역 TURN_MIND_ENABLED
                    #   × 채널 모듈 "mind"). 상태패널과 **같은 시점·같은 큐**(LOW)의 배경 콜이라
                    #   턴 임계 경로에 1ms도 얹지 않는다.
                    #     [게이트] 무대 ∩ 점수 → 0명이면 콜도 저장도 없다(조용).
                    #     [콜]     선별분 psyche + 산문 꼬리 → NPC별 한국어 1인칭 한 호흡
                    #     [폴백]   콜 실패·TURN_MIND_CALL=0 → v0 선별기(콜 0)가 같은 명단으로 선다
                    try:
                        import turn_mail as _tm_mod
                        if _tm_mod.mind_enabled(channel_id) and sent_msgs:
                            _mind_dai = dict(ctx.dai) if ctx.dai else {}
                            _mind_msg = sent_msgs[-1]
                            _mind_prose = response

                            async def _run_turn_mind():
                                _targets = _tm_mod.select_mind_targets(channel_id, _mind_dai)
                                if not _targets:
                                    return          # 대상 0명 = mail 미생성(버튼도 안 붙는다)
                                _names = [t["name"] for t in _targets]
                                _payload = None
                                if int(getattr(config, "TURN_MIND_CALL", 1) or 0):
                                    _payload = await _tm_mod.generate_mind_call(
                                        self.client, self.model_id_flash, channel_id,
                                        _mind_dai, _mind_prose, targets=_targets)
                                if not _payload:
                                    # 결정론 폴백 — 콜이 죽어도 💭는 그 턴 재료로 뜬다
                                    _payload = _tm_mod.generate_mind(channel_id, _mind_dai, names=_names)
                                if not _payload:
                                    return
                                logger.info("[TurnMind] source=%s npcs=%d",
                                            _payload.get("source", "?"), len(_payload.get("entries") or []))
                                await _tm_mod.deliver(
                                    _mind_msg, channel_id, _tm_mod.KIND_MIND, _payload)

                            await enqueue_background_task(
                                channel_id, "TurnMind", _run_turn_mind,
                                priority=TaskPriority.LOW,
                            )
                    except Exception as _e_tm:
                        logger.debug(f"[TurnMail] mind enqueue skip: {_e_tm}")

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

                    # 9.7. [Reader-GM 2026-07-05] 서브 GM 독자 — blind read(텔레스코프+산문만) → reader_log 적립.
                    # [2026-08-11 리더 §7] 구 "Stage 0 / log-only / 프롬 급식 없음"은 stale — 현행 FEED=1에서
                    # **다음 턴 좌뇌 서사 콜**에 조건부 급식(fog 재조명·굴절). 렌더 프롬프트 직행만 여전히 금지.
                    # async 지연 0. 스펙: trait_playbook §4 R1, 리더GM_지도_2026-08-11.
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
        # [2026-08-12 !다시 유령 정리] 도메인과 같은 시점의 SQLite 로그 워터마크 (구 스냅샷이면 None/빈 dict)
        log_marks = snapshot_entry.get("_marks") if snapshot_entry else None
        if snapshot is None:
            # 인메모리 miss (봇 재시작/인스턴스 재생성) → 디스크 영속본 폴백
            try:
                import sqlite_store
                snapshot = sqlite_store.read_retry_snapshot(channel_id)
                if snapshot:
                    log_marks = sqlite_store.read_retry_marks(channel_id)
                    logger.info(f"[!다시] Snapshot loaded from disk for {channel_id}")
            except Exception as _e_rs:
                logger.debug(f"[!다시] disk snapshot read skipped: {_e_rs}")
        if snapshot:
            domain_manager.save_domain(channel_id, copy.deepcopy(snapshot))
            logger.info(f"[!다시] Domain snapshot restored for {channel_id}")
            # [2026-08-12 !다시 유령 정리] 복원 **직후**, 재실행 **전**에 트림 — 순서가 계약이다.
            # (재실행분은 트림 뒤에 쌓이므로 절대 지워지지 않는다.)
            if log_marks:
                try:
                    import sqlite_store as _ss_tr
                    _trimmed = _ss_tr.trim_logs_to_watermarks(channel_id, log_marks)
                    if _trimmed:
                        logger.info(f"[Retry] sqlite ghosts trimmed: {_trimmed} rows")
                except Exception as _e_tr:
                    logger.debug(f"[Retry] sqlite trim skipped: {_e_tr}")
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

                # [2026-08-12 출력파생 §8] 렌더 실패 안내는 유저에게만 (§7-11)
                if persona.is_render_failure(response):
                    logger.warning("[Render] 폴백 안내 반환 — 배치 경로 히스토리·배경콜 스킵")
                    await message.channel.send(response)
                    response = None

                if response:
                    # [2026-08-16 상태창 코드 조립] 표시 전용 헤더 (저장본은 무오염)
                    await bot_utils.send_long_message(
                        message.channel, self._with_status_header(channel_id, response)
                    )
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

                # [2026-08-12 출력파생 §8] 렌더 실패 안내는 유저에게만 (§7-11)
                if persona.is_render_failure(response):
                    logger.warning("[Render] 폴백 안내 반환 — 관찰 경로 히스토리·배경콜 스킵")
                    await message.channel.send(response)
                    response = None

                if response:
                    # [2026-08-16 상태창 코드 조립] 표시 전용 헤더 (저장본은 무오염)
                    await bot_utils.send_long_message(
                        message.channel, self._with_status_header(channel_id, response)
                    )
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
