# -*- coding: utf-8 -*-
"""공용 추출 — [2026-10-23 C63 M4] 렌더 뒤 배경 추출 셋(B1·B2·B5)과 그 결과를 먹는 구독자들.

스펙  파티쳇수정/output_artifact/C63_모듈틀_M4배경추출_지도·스펙_2026-10-23.md
지도  파티쳇수정/output_artifact/C56_모듈틀_지도_2026-10-22.md §1.2 · §4.3

옛 자리 = orchestration 메서드 셋(`schedule_background_extraction` → `_apply_outputs` / `_execute_background_extraction`).
몸은 **글자 그대로** 옮겼다(M1 규율). 바뀐 건 이름뿐이다: self → rt, 큐 적재 → `_btq.enqueue_background_task`.

  background 정거장(builtin_modules 선언)
    outputs  2  bg_outputs  게이트 + B-Outputs 콜 → after_outputs 정거장
    world    4  bg_world    힌트 + B-Batch 콜   → after_batch 정거장
    ferment  6  bg_ferment  발효
  after_outputs — 옛 `_apply_outputs` 6단(순서가 계약: 값 델타 → 전이 큐 → 연산 → 노트북 메모 → status → append 기록)
    output_rules 10 out_values ①②③ · screen 30 out_notebook ④ · judgment 40 out_status ⑤⑤-b · status_panel 60 out_append ⑥
  after_batch — 옛 `_execute_background_extraction` 블록 경계 그대로(레티어스 10-23 "1번 오케이")
    quest 10 #1 · npc 20 #2·#3·#4 · world 50 #5 · narrative 60 #6(B3·B4 포함 한 덩어리) · presence 70 #7·#8·#9

**격리**(레티어스 10-23 "격리는 필요할거 같고") — 옛 블록은 바깥 try 하나라 try 없는 블록(퀘스트·각인·세계 상태·지문)이
  터지면 뒤가 전부 멈췄다. 이제 블록마다 try 다. 정상 턴은 같고, 실패 턴엔 그 블록만 빠진다(error 줄은 옛 결 그대로).
**섹션 선언**은 아직 없다 — 배치 프롬프트 글자는 cognition 그대로(레티어스 "선언은 추후 바꿀거고").

⚠ 순환 import 금지: module_frame → builtin_modules → 이 파일. 여기선 module_frame·일 하는 모듈을 **함수 안에서** import 한다.
"""
import copy
import logging
import traceback
from typing import Any, Dict

import config
import domain_manager
import background_task_queue as _btq
from background_task_queue import TaskPriority

# 옮긴 로그 줄은 옛 자리와 같은 로거로 남긴다(로그 모양·필터 그대로).
logger = logging.getLogger("Orchestration")


def _fail(name: str, e: Exception) -> None:
    """[C63 M4 격리] 옛 바깥 catch 와 같은 결 — error + traceback. 앞에 블록 이름만 붙는다."""
    logger.error(f"Background Extraction Failed [{name}]: {e}\n{traceback.format_exc()}")


# =====================================================================
# 추출 힌트 — 옛 schedule_background_extraction 첫 절(B1·B2 가 같이 읽는다)
# =====================================================================

def extraction_hints_for(ctx, response: str) -> Dict[str, Any]:
    """힌트 생성 휴리스틱 — 옛 자리에선 한 번 만들어 B1(physical)·B2(나머지)가 나눠 썼다. 순수 읽기라 둘이 각자 부른다."""
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
    return extraction_hints


# =====================================================================
# background 정거장 — 공용 콜 셋
# =====================================================================
async def bg_outputs(rt, turn) -> None:
    """[C63 M4] 옛 schedule_background_extraction Phase 1 — 출력물 전담 추출(B-Outputs) 게이트·적재. 큐 `OutputsExtraction` HIGH."""
    ctx, response, message = turn.ctx, turn.response, turn.message
    channel_id = ctx.channel_id
    extraction_hints = extraction_hints_for(ctx, response)
    import cognition
    import game_character
    import status_panel
    # Phase 1: 출력물 전담 추출 + 적용 (높은 우선순위)
    # [2026-09-06 P8a] 옛 ImmediatePhysicalUpdate(B-1 노트북 콜) 자리. 값 델타·전이 큐·
    #   연산 신고까지 **한 콜**로 받고 한 자리(_apply_outputs)에서 적용한다. 배치와는
    #   태스크·try 가 완전히 갈라져 있어 한쪽 실패가 다른 쪽을 지우지 못한다.
    #   급식 게이트: 선언·후보가 0이고 physical 힌트도 꺼져 있으면 **태스크 자체가 없다**
    #   (선언 없는 채널은 종전 대비 콜 -1 — 전담 +1, B-1 -1 로 총합 ±0).
    _cv_feed = []
    # [2026-09-13 P16] 이번 턴 도착물 본문 — 급식이 소비하면서 한 턴 남긴 것.
    #   게이트도 추출 입력도 이걸 같이 본다(편지 안의 선언 이름이 잡히게).
    _handout_txt = ""
    try:
        import custom_vars as _cv_h
        _handout_txt = _cv_h.last_handouts(channel_id)
    except Exception as _e_ho:
        logger.debug(f"[CustomVar] 도착물 다리 skipped: {_e_ho}")
    try:
        import custom_vars as _cv
        _cv_feed = _cv.select_mentioned(channel_id, ctx.action_text, response,
                                        _handout_txt)
        if _cv_feed:
            logger.debug(f"[CustomVar] mentions gate: {[v['name'] for v in _cv_feed]}")
    except Exception as _e_cv:
        logger.debug(f"[CustomVar] mentions gate skipped: {_e_cv}")
    _cue_feed = []
    _op_feed = []
    try:
        import expr_engine as _ee_feed
        _cue_feed = _ee_feed.pending_cues(channel_id, ctx.action_text, response)
        _op_feed = _ee_feed.pending_operations(channel_id, ctx.action_text, response)
        if _cue_feed or _op_feed:
            logger.debug(f"[Expr] 급식: cues={[c['name'] for c in _cue_feed]} "
                         f"ops={[o['name'] for o in _op_feed]}")
    except Exception as _e_ee:
        logger.debug(f"[Expr] 급식 게이트 skipped: {_e_ee}")

    # [2026-09-09 P12] append 기록 섹션의 **존재**도 콜 조건이다. 급식 셋이 다 비어도
    #   쌓을 장부가 선언돼 있으면 콜은 돌고, 산문에 그 일이 없으면 entries 가 빈 배열로
    #   돌아온다(그게 정답이다 — 부재 감지 0). 콜 순증 0: 이 콜은 원래 있던 그 콜이다.
    _ap_feed = []
    try:
        _ap_feed = status_panel.append_sections_feed(channel_id)
    except Exception as _e_ap:
        logger.debug(f"[AppendLog] 급식 게이트 skipped: {_e_ap}")

    _want_notebook = bool(extraction_hints["physical"])
    # [2026-10-15 C42] 타격칸 키는 노트북과 무관하게 켠다 — 판정이 모든 채널에서 읽는다.
    #   토글은 활력·평형과 같은 채널 스위치(꺼진 채널은 콜 순증 0).
    try:
        _want_strikes = bool(domain_manager.is_vigor_composure_active(channel_id))
    except Exception:
        _want_strikes = False
    if _cv_feed or _cue_feed or _op_feed or _ap_feed or _want_notebook or _want_strikes:
        async def outputs_extraction():
            try:
                # 라이브 노트북 재읽기(stale ctx.notebook_txt 대신). physical 힌트가 꺼져 있으면 입력에 싣지 않는다.
                # [2026-10-23 C61] [소지품] 칸은 빼고 싣는다 — 소지품은 선언 급식(always_feed)으로 따로 받는다.
                live_notebook = ""
                status = []
                if _want_notebook:
                    live_notebook = game_character.get_notebook_text(channel_id, ctx.user_id,
                                                                     with_items=False)
                    status = game_character.get_status_effect_names(
                        ctx.player_data.get("status_effects", []) if ctx.player_data else []
                    )
                outputs = await cognition.extract_outputs(
                    rt.client, ctx.action_text, response,
                    custom_vars_feed=_cv_feed,
                    cues_feed=_cue_feed,
                    operations_feed=_op_feed,
                    notebook=live_notebook,
                    current_status=status,
                    include_notebook=_want_notebook,
                    append_sections=_ap_feed,
                    handout=_handout_txt,
                    include_strikes=_want_strikes,
                    current_strikes=game_character.strike_view(
                        domain_manager.get_participant_data(channel_id, ctx.user_id) or {}),
                    # [2026-10-07 S2] 행동 PC 가면 — scope pc 변수의 주인
                    pc_name=(rt._pc_names_for_extract(channel_id, ctx.user_id)[:1] or [""])[0],
                )
                # [C63 M4] 옛 `_apply_outputs` → after_outputs 정거장(구독자 넷 · 옛 6단 순서). 빈 산출이면 옛 첫 줄처럼 아무것도 안 한다.
                if outputs:
                    import module_frame as _mf
                    await _mf.run_station(_mf.AFTER_OUTPUTS, rt, _mf.OutputsTurn(
                        channel_id=channel_id, ctx=ctx, outputs=outputs, message=message))
            except Exception as e:
                logger.error(f"Outputs extraction error: {e}")

        await _btq.enqueue_background_task(
            channel_id,
            "OutputsExtraction",
            outputs_extraction,
            priority=TaskPriority.HIGH
        )


async def bg_world(rt, turn) -> None:
    """[C63 M4] 옛 schedule_background_extraction Phase 2 — 세계 추출(B-Batch) 적재. 큐 `BackgroundExtraction` NORMAL."""
    ctx, response, message = turn.ctx, turn.response, turn.message
    channel_id = ctx.channel_id
    extraction_hints = extraction_hints_for(ctx, response)
    # Phase 2: 백그라운드 추출 (일반 우선순위)
    bg_hints = {k: v for k, v in extraction_hints.items() if k != "physical" and v}

    if bg_hints:
        # 클로저를 위한 컨텍스트 캡처
        captured_ctx = ctx
        captured_response = response
        captured_message = message
        captured_hints = bg_hints

        async def background_extraction_task():
            # [C63 M4] 옛 self._execute_background_extraction → _run_world_batch(같은 앞부분 + after_batch 정거장)
            await _run_world_batch(
                rt,
                captured_ctx, captured_response,
                captured_message, captured_hints
            )

        await _btq.enqueue_background_task(
            channel_id,
            "BackgroundExtraction",
            background_extraction_task,
            priority=TaskPriority.NORMAL
        )


async def bg_ferment(rt, turn) -> None:
    """[C63 M4] 옛 schedule_background_extraction Phase 3 — 발효. 큐 `BackgroundFermentation` LOW."""
    channel_id = turn.ctx.channel_id
    import fermentation
    # Phase 3: Mnemosyne Fermentation (Low Priority)
    async def background_fermentation_task():
        try:
            # [2026-09-05 발효 계약] 통째 저장(save_callback) 폐지.
            # 발효는 사본 위에서 돌고, 결과 diff만 live 도메인에 얹는다.
            snap = fermentation.build_ferment_input(domain_manager.get_domain(channel_id), channel_id=channel_id)
            before = copy.deepcopy(snap)
            await fermentation.auto_ferment(
                rt.client, rt.model_id,
                snap,
                channel_id=channel_id
            )
            result = fermentation.extract_ferment_result(before, snap)
            if result.changed:
                domain_manager.apply_ferment_result(channel_id, result)
        except Exception as e:
            logger.error(f"[Orchestrator] Fermentation task error: {e}")

    # Schedule fermentation
    await _btq.enqueue_background_task(
        channel_id,
        "BackgroundFermentation",
        background_fermentation_task,
        priority=TaskPriority.LOW
    )


async def schedule_extraction(rt, ctx, response: str, message) -> None:
    """[C63 M4] 배치·관찰 경로(orchestration execute_batch · execute_observation) — 배경 추출 셋만 적재.
    옛 `self.schedule_background_extraction(ctx, response, message)` 자리 그대로다: 두 경로는 옛날에도 추출 셋 + 재그림만 했다
    (상태패널·속마음·감사·Reader 는 본 경로 execute 의 BACKGROUND 정거장에만 선다). 예외도 옛처럼 호출자에게 간다."""
    import module_frame as _mf
    ap = _mf.AfterProse(channel_id=ctx.channel_id, ctx=ctx, response=response, sent_msgs=[], message=message)
    for fn in (bg_outputs, bg_world, bg_ferment):
        await fn(rt, ap)


# =====================================================================
# 세계 추출 본체 — 옛 _execute_background_extraction 앞부분(입력 조립 · 콜 · 자기 점검 로그)
# =====================================================================
async def _run_world_batch(rt, ctx, response: str, message, hints: Dict[str, bool]) -> None:
    """백그라운드 추출 실행 — [C63 M4] 콜까지는 옛 몸 그대로, 적용은 after_batch 정거장의 구독자들(블록마다 try)."""
    import cognition
    import game_system
    import npc_manager
    channel_id = ctx.channel_id
    # [2026-09-24 감사] 이 추출이 속한 턴 = 렌더 시점에 waterfall 이 찍은 ctx.dai["turn_index"].
    #   배경 큐가 밀려 다음 턴의 increment 뒤에 돌면 live world_state 는 N+1 이다 → turn_log·등장
    #   마크·엣지·위키 Observed src_turns 가 N+1 로 도장되고, N+1 을 !다시 하면 턴 N 관찰까지 되감겼다.
    def _bg_turn_now(_ws_live=None) -> int:
        _t = (ctx.dai or {}).get("turn_index") if isinstance(getattr(ctx, "dai", None), dict) else None
        if isinstance(_t, int) and not isinstance(_t, bool):
            return _t
        _w = _ws_live if _ws_live is not None else (domain_manager.get_world_state(channel_id) or {})
        return int((_w or {}).get("turn_index", 0) or 0)

    try:
        # Reload critical data to ensure we work on latest state
        # (Though extraction mainly produces updates, merging is handled by domain_manager)

        # Prepare extended context
        p_data_latest = domain_manager.get_participant_data(channel_id, ctx.user_id)
        ai_mem: Dict[str, Any] = p_data_latest.get("ai_memory", {}) if p_data_latest else {}
        # ... (Assume these getters exist or use ctx if acceptable)
        # Actually, getting fresh data is safer for background tasks running later.

        # For simplicity, we use what we have or simple lookups
        # [2026-10-07 PC 이름 단일화] 명부에 PC(옛 오염 "아담" 포함)가 섞이면 B1이 NPC로 다시 쓴다 — 가면 이름 규칙으로 뺀다.
        _pc_reg_b1 = domain_manager.pc_registry(channel_id)
        lore_npcs = [n for n in npc_manager.get_lore_npc_names(channel_id) if not domain_manager.is_pc_name(n, _pc_reg_b1)]
        scene_npcs = [n for n in npc_manager.get_scene_npc_names(channel_id) if not domain_manager.is_pc_name(n, _pc_reg_b1)]
        # [2026-09-18 식별 허브 S3] 무대 명부 + 식별 한 줄. PC 가면은 뺀다(인물 목록이지 PC 목록이 아니다).
        #   ⚠ 이 시점 0단은 **이번 턴 R4 위치 쓰기 전** — 막 들어온 인물은 안 잡힌다(의도).
        try:
            _pc_masks_on = _pc_reg_b1   # [2026-10-07] 가면 이름 규칙 명부(받는 쪽이 is_pc_name)
            onstage_lines = npc_manager.onstage_roster_lines(channel_id, exclude=_pc_masks_on)
        except Exception as _e_on:
            logger.debug(f"[Identity] 무대 명부 급식 건너뜀: {_e_on}")
            onstage_lines = []
        current_quests = game_system.get_active_quests(channel_id)

        session_memory = domain_manager.get_session_ai_memory(channel_id)
        prev_continuity = domain_manager.get_latest_frame(channel_id)
        # [2026-08-12 fingerprint 프레임 소급] 이 dict는 dai_snapshot(frames[-1]이 정답)과
        #   지문(직전에 실제로 찍힌 프레임이 정답)이 섞여 있다. 지문 쪽은 frames[-1]=이번 턴 빈
        #   프레임이라 cognition의 이전값 참조(Lighting/Palette/Rhythm/…)가 상시 공백이었다.
        #   **지문만** 공용 관문으로 교체 — dai_snapshot 경로는 무변경.
        prev_continuity["render_fingerprint"] = domain_manager.get_prev_fingerprint(channel_id)
        # [2026-09-06 P8a] 노트북/status 재읽기 삭제 — 배치는 더 이상 둘을 읽지 않는다
        #   (B-1 폐지). 같은 재읽기는 전담 태스크(outputs_extraction) 안에 있다.

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
            if getattr(ctx, "bus", None) is not None and getattr(ctx.bus, "anomaly", None):
                _arc_promote_cand = ctx.bus.anomaly.get("arc_promote_candidate")
        except Exception as _e_arc_pre:
            logger.debug(f"[Arc] PMU context build skipped: {_e_arc_pre}")

        # [2026-09-06 P8a] 급식 게이트 3종(select_mentioned / pending_cues /
        #   pending_operations)은 `schedule_background_extraction` 으로 **이사**했다 —
        #   그 산출을 먹는 콜이 전담 콜이라 게이트도 그 태스크와 같은 자리에 있어야
        #   실패가 함께 격리된다.

        # [2026-09-25 스레드 장부] 배치 world_state 입력 장부 줄(열림·멈춤, id·기한 포함).
        try:
            import thread_ledger as _tl_in
            _thread_line = _tl_in.extraction_ledger_line(channel_id)
        except Exception as _e_tl:
            logger.debug(f"[Thread] ledger line skip: {_e_tl}")
            _thread_line = ""

        updates = await cognition.extract_all_updates(
            rt.client, rt.model_id_flash,
            ctx.action_text, response,
            lore_npc_names=lore_npcs,
            scene_npc_names=scene_npcs,
            current_quests=current_quests,
            extraction_hints=hints,
            current_session_memory=session_memory,
            previous_continuity=prev_continuity,
            arc_context=_arc_context_str,
            arc_promote_candidate=_arc_promote_cand,
            onstage_lines=onstage_lines,
            thread_ledger_line=_thread_line,
            pc_names=rt._pc_names_for_extract(channel_id, ctx.user_id),   # [2026-10-07 S2] `!가면` 등록 이름
        )

        # [V10 검증 lite] 추출 self-check 로그 (detection-only — 아직 게이트 X)
        _unc = updates.get("_uncertain") if isinstance(updates, dict) else None
        if _unc:
            logger.info(f"[Extract self-check] uncertain sections this turn: {_unc}")

        # [C63 M4] 적용 = after_batch 정거장. 블록 경계 그대로 구독자가 순서대로 받는다(quest 10 · npc 20 · world 50 ·
        #   narrative 60 · presence 70). 한 구독자가 죽어도 나머지는 돈다(구독자 안 try + 틀 run_station).
        import module_frame as _mf
        await _mf.run_station(_mf.AFTER_BATCH, rt, _mf.BatchTurn(
            channel_id=channel_id, ctx=ctx, response=response, message=message,
            updates=updates, turn_now=_bg_turn_now, session_memory=session_memory))

    except Exception as e:
        logger.error(f"Background Extraction Failed: {e}\n{traceback.format_exc()}")


# =====================================================================
# after_outputs 구독자 — 옛 _apply_outputs 6단(단마다 try 는 옛 그대로)
# =====================================================================
async def out_values(rt, o) -> None:
    """① 값 델타 + ②③ 신고 대기열 — 주인 출력룰(after_outputs 10)."""
    channel_id, ctx, outputs = o.channel_id, o.ctx, o.outputs
    # ① 값 델타 — 이전 값+델타 → 범위 클램프 → 저장은 전부 코드 몫. 근거 없는 행은
    #    apply_deltas 가 폐기한다(evidence 필수).
    if outputs.get("deltas"):
        try:
            import custom_vars as _cv2
            _ws_cv = domain_manager.get_world_state(channel_id) or {}
            # [2026-09-24 감사 §5-2 #18a] 이중 차감 방지는 **이번 추출에서 시도 신고된 연산**의 대상만.
            _op_tried = [str(o.get("name") or "").strip() for o in (outputs.get("operations") or [])
                         if isinstance(o, dict) and o.get("attempted")]
            _cv2.apply_deltas(channel_id, outputs["deltas"],
                              turn=int(_ws_cv.get("turn_index", 0) or 0),
                              actor=ctx.user_id, op_names=_op_tried)
        except Exception as _e_cv:
            logger.debug(f"[CustomVar] delta 적용 skip: {_e_cv}")
    # ②③ 신고 → 대기열. 집행은 **다음 턴**(추출이 배경이라 한 턴 뒤 — 스펙 §0.7 e).
    if outputs.get("cues") or outputs.get("operations"):
        try:
            import expr_engine as _ee_q
            _nq = _ee_q.queue_cues(channel_id, outputs.get("cues"))
            _nq += _ee_q.queue_operations(channel_id, outputs.get("operations"))
            if _nq:
                logger.info(f"[Expr] 신고 적재 {_nq}건 (다음 턴 집행)")
        except Exception as _e_q:
            logger.debug(f"[Expr] 신고 적재 skip: {_e_q}")


async def out_notebook(rt, o) -> None:
    """④ 노트북 LLM 메모 — 주인 상태창·보관함(after_outputs 30)."""
    channel_id, ctx, outputs, message = o.channel_id, o.ctx, o.outputs, o.message
    import game_character
    # ④ 노트북 — 역할 경계는 저장 모양이 보장한다: 코드는 [메모].llm 배열에만 쓴다.
    #    유저 줄('-')과 [소지품]/[일지]는 손댈 통로가 아예 없다.
    try:
        if game_character.apply_llm_memos(
            channel_id, ctx.user_id,
            outputs.get("memo_add"), outputs.get("memo_remove")
        ) and message is not None:
            await message.channel.send("📔 노트북 기록됨")
    except Exception as _e_nb:
        logger.debug(f"[Notebook] llm 메모 적용 skip: {_e_nb}")


async def out_status(rt, o) -> None:
    """⑤ status_effects + ⑤-b 타격칸 — 주인 판정(after_outputs 40). 판정이 둘을 읽는다."""
    channel_id, ctx, outputs = o.channel_id, o.ctx, o.outputs
    # ⑤ status_effects — [2026-10-15 C42] 좋은 상태 전용(알려진 나쁜 상태는 타격칸으로 돌린다)
    try:
        rt._apply_status_changes(
            channel_id, ctx.user_id,
            outputs.get("status_add"), outputs.get("status_remove")
        )
    except Exception as _e_st:
        logger.debug(f"[Status] 적용 skip: {_e_st}")
    # ⑤-b [2026-10-15 C42] 타격칸 — 마이너스 상태 전용 4칸. 채우기·비우기 주인은 이 콜 하나.
    try:
        rt._apply_strike_changes(
            channel_id, ctx.user_id,
            outputs.get("strike_add"), outputs.get("strike_clear")
        )
    except Exception as _e_sk:
        logger.debug(f"[Strike] 적용 skip: {_e_sk}")


async def out_append(rt, o) -> None:
    """⑥ append 기록 — 주인 상태패널(after_outputs 60). 마지막: 도장이 이번 턴 최종 상태를 읽는다."""
    channel_id, ctx, outputs = o.channel_id, o.ctx, o.outputs
    import status_panel
    # ⑥ [2026-09-09 P12] append 기록 — 행 적립. 마지막인 이유: 도장(시간·위치·인물)이
    #    이번 턴 최종 상태를 읽어야 하고, 이 단계가 죽어도 위 다섯은 이미 끝나 있다.
    try:
        if outputs.get("entries"):
            status_panel.apply_append_entries(
                channel_id, outputs["entries"], user_id=ctx.user_id)
    except Exception as _e_ap2:
        logger.debug(f"[AppendLog] 적립 skip: {_e_ap2}")


# =====================================================================
# after_batch 구독자 — 옛 _execute_background_extraction 블록(경계 그대로 · 블록마다 try)
# =====================================================================
async def after_quest(rt, b) -> None:
    """#1 퀘스트 — 주인 quest(after_batch 10). 채널 메시지를 보낸다(옛 그대로)."""
    channel_id, message, updates = b.channel_id, b.message, b.updates
    import game_character
    import game_system
    try:
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

        # ⛔[2026-09-15 관계 통합] NPCDepthUpdate(npc_depth_hints → update_helena_metric) 삭제 —
        #   NPC→PC 관계 질문은 Theoria relation 층 하나(update_world_state의 write_theoria_relations).
    except Exception as e:
        _fail("quest", e)


async def after_npc(rt, b) -> None:
    """#2 충동 · #3 각인 · #4 NPC↔NPC 관계 + 관계 감쇠 + 충동 감쇠 — 주인 npc(after_batch 20). 블록마다 try."""
    channel_id, updates = b.channel_id, b.updates
    _bg_turn_now = b.turn_now
    import npc_manager
    try:
        # [2026-08-02 C축] npc_drive — LLM은 **단계 이름만** 낸다.
        #   수치가 없으므로 cap_llm_delta가 아니라 set_drive_gated가 클램프한다
        #   (쿨다운 + ±1단계 / 해소는 면제·다단 하강). 콜 순증 0 — 같은 추출 콜에 필드만 얹음.
        _drive_hints = updates.get("npc_drive")
        if isinstance(_drive_hints, dict) and _drive_hints:
            try:
                _turn_dr = _bg_turn_now()
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
    except Exception as e:
        _fail("npc_drive", e)

    try:
        # NPC Behavioral Imprints
        npc_imp = updates.get("NPCImprintUpdate")
        if npc_imp and isinstance(npc_imp, dict):
            current_turn = _bg_turn_now()
            domain_manager.update_npc_imprints(channel_id, npc_imp, turn=current_turn)
            logger.info(f"[Imprint] {list(npc_imp.keys())}")
    except Exception as e:
        _fail("npc_imprint", e)

    try:
        # NPC↔NPC Relations — [2026-09-15 관계 통합] 같은 relations 엣지에 upsert(쓰기 경로 2/3).
        _turn_d = _bg_turn_now()
        npc_rels = updates.get("NPCRelationUpdate")
        if npc_rels and isinstance(npc_rels, list):
            try:
                import entity_relations
                _rel_count = entity_relations.process_batch_relations(channel_id, npc_rels, current_turn=_turn_d)
                if _rel_count:
                    logger.info(f"[EntityRelations] Processed {_rel_count} relation updates")
            except Exception as e:
                logger.warning(f"[EntityRelations] Failed to process: {e}")

        # [관계 감쇠] 한 곳 — NPC→PC·NPC↔NPC 엣지 모두, last_turn 시계(안 관측되면 식는다).
        #   구: entity_relations fade + decay_stale_relations(등장 시계) 둘 따로 → 하나로.
        try:
            domain_manager.decay_relation_edges(channel_id, _turn_d)
        except Exception as _e_rd:
            logger.debug(f"[RelationDecay] skip: {_e_rd}")

        # [2026-08-02 C축] 충동 압력 자연 하강. A축과 시계가 다르다 —
        #   A축=등장(안 만나면 식음) / C축=무변화(안 건드리면 가라앉음).
        #   압력은 만나지 않아도 스스로 내려간다.
        try:
            npc_manager.tick_drive_decay(channel_id, _turn_d)
        except Exception as _e_dd:
            logger.debug(f"[Drive] decay skip: {_e_dd}")
    except Exception as e:
        _fail("npc_relations", e)


async def after_world(rt, b) -> None:
    """#5 세계 상태(스레드 장부 → 세션 메모리 → 장면 시각 전진) — 주인 world(after_batch 50)."""
    channel_id, ctx, response, updates = b.channel_id, b.ctx, b.response, b.updates
    _bg_turn_now = b.turn_now
    session_memory = b.session_memory
    try:
        # World State Update (ai_session_memory 갱신)
        wsu = updates.get("WorldStateUpdate")
        if wsu and isinstance(wsu, dict):
            mem_updates = {}
            # [2026-09-25 스레드 장부] 옛 active/resolved_threads 병합 삭제(빈 리스트면 갱신을 건너뛰어
            #   목록이 0으로 못 줄던 자리). 전이 → 관문 → thread_log. ★_advance_scene_time **앞** —
            #   기한 표현("내일 정오")은 이번 턴 장면 시각 기준이라 이번 턴 경과분을 밀기 전 시계로 푼다.
            if isinstance(wsu.get("threads"), list) and wsu["threads"]:
                try:
                    import thread_ledger as _tl_ap
                    _tl_ap.apply_events(channel_id, _bg_turn_now(), wsu["threads"],
                                        f"{ctx.action_text or ''}\n{response or ''}")
                except Exception as _e_tla:
                    logger.warning(f"[Thread] apply skip: {_e_tla}")
            if wsu.get("world_changes"):
                existing_changes = session_memory.get("world_changes", [])
                merged_changes = existing_changes + wsu["world_changes"]
                mem_updates["world_changes"] = merged_changes[-15:]
            if wsu.get("npc_schedule_hints"):
                existing_schedules = session_memory.get("npc_summaries", {})
                # [2026-10-07 PC 이름 단일화] NPC 일정에 PC("아담")가 들어가면 Theoria 4c "NPC Activity"로 되돌아왔다.
                _pcr_sch = domain_manager.pc_registry(channel_id)
                existing_schedules.update({k: v for k, v in (wsu["npc_schedule_hints"] or {}).items()
                                           if not domain_manager.is_pc_name(k, _pcr_sch)})
                mem_updates["npc_summaries"] = existing_schedules
            if wsu.get("current_arc"):
                mem_updates["current_arc"] = wsu["current_arc"]
            # [2026-10-11 C26 P4] 몸 상태·성공의 부작용은 **이번 턴 판독**이다 — 키가 오면 매 턴 쓴다(빈 값·null = 지움).
            #   전엔 값이 참일 때만 덮어서 빈 값 턴(녹화: 부작용 빈 문자열 78%, 몸 {} 55%)에 옛 값이 4c에 계속 실렸다.
            #   추출 콜은 지금 플래그를 못 보므로 서 있는 상태를 쥘 수 없다 — 오래 가는 손상의 추적은 활력·평형 몫(레티어스).
            #   근거 memory_lore/C26_P4낡은값_스펙_2026-10-11
            if "basic_needs_flags" in wsu and (wsu["basic_needs_flags"] is None or isinstance(wsu["basic_needs_flags"], dict)):
                mem_updates["basic_needs_flags"] = wsu["basic_needs_flags"] or {}
            if "residual_effects" in wsu and (wsu["residual_effects"] is None or isinstance(wsu["residual_effects"], str)):
                mem_updates["residual_effects"] = (wsu["residual_effects"] or "").strip()
            # [2026-08-16 상태창 코드 조립] 시간 전진 — 구 status line 파싱의 후계.
            #   session memory가 아니라 world_state로 가므로 mem_updates 밖에서 처리한다.
            if wsu.get("scene_minutes_elapsed"):
                rt._advance_scene_time(channel_id, ctx, wsu["scene_minutes_elapsed"])
            if mem_updates:
                domain_manager.update_session_ai_memory(channel_id, mem_updates)
                logger.info(f"[WorldState] Updated session memory: {list(mem_updates.keys())}")
    except Exception as e:
        _fail("world_state", e)


async def after_narrative(rt, b) -> None:
    """#6 줄거리 추적(턴 로그 · 등장 · 출석 점검 · 엔티티 · B3 시트 성장 · 스토리라인 · arc · B4 요약 · 저장) — 주인 narrative(after_batch 60). 한 덩어리(레티어스 10-23)."""
    channel_id, ctx, response, updates = b.channel_id, b.ctx, b.response, b.updates
    _bg_turn_now = b.turn_now
    import npc_manager
    # ⛔[2026-09-16 3차] 배치 narrative passives/trait_evolution 반영 삭제 — 조각 생산은
    #   시트 heavy 콜 + grow_sheet 정리 콜(quote 발췌) 하나. PlayerMemoryUpdate 의 남은 키는 아래 소비부가 읽는다.
    # [2026-09-24 감사] 3차 삭제 때 아래 줄까지 같이 빠져 NarrativeTracker 블록(L~1925 tensions·climate)이
    #   매 턴 NameError → update_narrative_tracker_state 저장 0(turn_log·arc·storyline 동결). 바인딩만 복원.
    pmu = updates.get("PlayerMemoryUpdate")
    wsu = updates.get("WorldStateUpdate")   # [C63 M4] 옛 #5 의 같은 줄 — 장면 인물 합집합(npc_schedule_hints)이 읽는다

    try:
        # [NarrativeTracker] 턴 로그 + 엔티티 상태 이력 업데이트
        try:
            import narrative_tracker
            nt_state = domain_manager.get_narrative_tracker_state(channel_id)

            # turn_idx: world_state에서 획득, 없으면 히스토리 길이 기반
            turn_idx = _bg_turn_now()

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
            _pc_masks = domain_manager.pc_registry(channel_id)   # [2026-10-07 PC 이름 단일화] 가면 이름 규칙 명부
            involved_npcs = [n for n in _scene_names if not domain_manager.is_pc_name(n, _pc_masks)]
            # [2026-09-02 P0 선행 수리] 스펙 §1.5 / §6 P0 — 출석 마킹 입력에서
            #   `npc_schedule_hints`를 뺀다.
            # 병: 그 필드는 cognition 지시문이 **"Only mentioned NPCs"**라고 정의한 재료다
            #   (cognition.py npc_schedule_hints 조항). 그 키가 위 합집합을 타고
            #   mark_npc_appearance까지 흘러, PC가 "리안은 지금 뭐 하려나" 한마디만 해도
            #   리안이 **출석**으로 기록됐다. Flash 오판이 아니라 *정의상 언급*인 재료를
            #   코드가 출석에 합친 것 — "이름만 부르면 무대에 뜨나"의 실물 답이 여기다.
            #   한 번 잘못 찍히면 그 턴의 출석 소비처 여섯이 같이 틀린다(§1.4).
            # 경위: 힌트는 원래 storyline 분류용 엔티티 목록에 보조로 합류시킨 것이고
            #   (06-11 주석 참조), 출석 마킹(T-A)이 나중에 **같은 목록에 얹혔다.**
            #   두 소비자가 한 목록을 쓰다 한쪽에 안 맞는 재료가 섞인 형태.
            # 처방: 목록을 둘로 — storyline(record_turn)은 합집합 유지(_scene_names 무변경),
            #   출석 마킹만 psyche ∪ attitudes − PC 가면.
            # ⚠ 이 스펙과 **무관한 독립 수리**다(출석 판정 방식 자체는 A에서 안 바꾼다).
            _present_names = list(dict.fromkeys(
                list((_dai_d.get("npc_attitudes") or {}).keys())
                + list((_dai_d.get("psyche_states") or {}).keys())
            ))
            present_npcs = [n for n in _present_names if not domain_manager.is_pc_name(n, _pc_masks)]
            qf = ctx.dai.get("quality_flags", {}) if ctx.dai else {}
            user_brief = str(ctx.action_text or "")[:200]
            ai_brief = str(response or "")[:300]
            # [2026-09-14 W5] 장소·세력을 턴로그 **별도 키**로 도장한다(entities 무접촉).
            #   현재 위치는 여기(다른 메서드)에서 지역변수로 안 살아 있다 — `update_world_state`가
            #   이미 저장한 정본을 `domain_manager.get_current_location`으로 읽는다(지시서 §0 ④).
            _w5_extra = None
            try:
                if getattr(config, "WIKI_PLACES", False):
                    import wiki_store as _ws_w5
                    _w5_extra = _ws_w5.extra_entity_names(
                        channel_id, str(ctx.action_text or "") + "\n" + str(response or "")) or None
            except Exception as _e_w5:
                _w5_extra = None
            narrative_tracker.record_turn(nt_state, turn_idx, user_brief, ai_brief, involved_npcs, qf,
                                          extra_entities=_w5_extra)

            # [T-A] NPC 등장 카운트(구별 턴만) — 1회성/다회성 tier 계측. session만 내부 게이트.
            try:
                # [2026-09-02 P0] involved_npcs(=storyline용 합집합) → present_npcs.
                #   위 주석 참조: 힌트는 정의상 "mentioned"라 출석 재료가 아니다(§1.5).
                for _inpc in present_npcs:
                    npc_manager.mark_npc_appearance(channel_id, _inpc, turn_idx)
            except Exception as _e_appear:
                logger.debug(f"[NPC Tier] appearance mark skipped: {_e_appear}")

            # [2026-09-02 R2 검증자 — log-only] 스펙 §6 R2.
            # 출석의 원본은 아직 Flash다(판정 뒤집기는 덩어리 B/R4). 여기서는 위치 그래프가
            # 말하는 0단(같은 노드)과 방금 찍은 Flash 출석의 **차집합만 기록**한다.
            # 목적: 전환 전에 두 판정이 실제로 얼마나 벌어지는지 실측을 쌓는 것.
            # ⚠ 관측이 본류를 죽이면 안 된다 — 예외는 전부 삼키고 debug로 내린다.
            try:
                _tiers = npc_manager.get_presence_tiers(channel_id)
                if not _tiers.get("unresolved"):
                    _flash_set = set(present_npcs)
                    _scene_set = set(_tiers.get("scene") or [])
                    _f_only = sorted(_flash_set - _scene_set)
                    _n_only = sorted(_scene_set - _flash_set)
                    if _f_only or _n_only:
                        logger.info(
                            "[presence-check] flash_only=%d(%s) node_only=%d(%s) @ %s",
                            len(_f_only), ", ".join(_f_only[:5]) or "-",
                            len(_n_only), ", ".join(_n_only[:5]) or "-",
                            domain_manager.get_current_location(channel_id),
                        )
            except Exception as _e_ptier:
                logger.debug(f"[presence-check] skipped: {_e_ptier}")

            # 엔티티 상태 변화 기록
            est_data = updates.get("EntityStateUpdate")
            # [2026-07-28] PC 혼입 가드 — 이건 waterfall의 DAI 정제와 **다른 콜**(후행 추출
            # extract_all_updates)의 산출이라 그 초크포인트를 안 지난다. 무가드로 두면
            # PC의 위치·건강·기분이 NPC 엔티티 로그에 영구 저장되고 Slot 7로 되돌아온다.
            if isinstance(est_data, dict) and est_data:
                _pc_masks_est = domain_manager.pc_registry(channel_id)   # [2026-10-07 PC 이름 단일화]
                if _pc_masks_est:
                    _est_hit = [n for n in est_data if domain_manager.is_pc_name(n, _pc_masks_est)]
                    if _est_hit:
                        est_data = {k: v for k, v in est_data.items() if k not in _est_hit}
                        logger.debug(f"[EntityState] PC 혼입 제외: {', '.join(_est_hit)}")
            if est_data:
                narrative_tracker.update_entity_states(nt_state, turn_idx, est_data)

                # [2026-09-16 시트 2차 §9] 로스터 전처리(개명·사망·무력화·즉석 생성·몹 태그·lore_seen) →
                # 성장 관문 하나(grow_sheet): 관찰은 인물 페이지 Observed 절, 250자 자라면 heavy 정리 1콜.
                try:
                    for _gn, _gd in await rt._npc_roster_pass(channel_id, est_data, _pc_masks, ctx, turn_idx, prose=response):
                        await rt.grow_sheet(channel_id, ("npc", _gn), _gd, turn=turn_idx)
                except Exception as _e_sheet:
                    logger.warning(f"[NPC Sheet] enrichment skipped: {_e_sheet}")

            # [2026-09-16 시트 2차 §8·§9] PC도 같은 관문 — 행동 PC(uid)의 페이지(`!가면`)가 있을 때만.
            try:
                await rt.grow_sheet(channel_id, ("pc", ctx.user_id), updates.get("PCObserved"), turn=turn_idx)
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
                    "anomaly_category": (ctx.bus.anomaly or {}).get("category", "") if getattr(ctx, "bus", None) is not None else "",
                    "doom_phase": (ctx.bus.doom or {}).get("chapter_phase", "") if getattr(ctx, "bus", None) is not None else "",
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
            flash_model = _cfg.role_model("flash")
            await narrative_tracker.summarize_if_needed(
                nt_state, turn_idx,
                client=rt.client if hasattr(rt, 'client') else None,
                model_id=flash_model
            )

            domain_manager.update_narrative_tracker_state(channel_id, nt_state)
        except Exception as nt_err:
            logger.warning("[NarrativeTracker] Update failed: %s", nt_err)
    except Exception as e:
        _fail("narrative", e)


async def after_presence(rt, b) -> None:
    """#7 렌더 지문 · #8 관찰 → 위치 · #9 스케줄 틱 — 주인 presence(after_batch 70). gaze·관찰 집합은 함수 안 지역 값으로 넘긴다."""
    channel_id, ctx, updates = b.channel_id, b.ctx, b.updates
    import npc_manager
    # [Scene Continuity 2층] 렌더링 지문 저장
    _gaze_now = ""   # [2026-09-02 R4] 이번 턴 gaze — 바로 아래 관찰 쓰기가 소비한다.
    _observed_now = set()   # [2026-09-03 R6] 이번 턴 관찰이 옮기거나 입장시킨 등록 키. 스케줄 틱의 제외 집합.
    try:
        from orchestration import _normalize_render_fingerprint   # [C63 M4] 지문 정규화는 옛 자리(모듈 함수)에 남았다
        rfp = updates.get("RenderFingerprint")
        if rfp and isinstance(rfp, dict):
            # [2026-08-12 출력파생 §8] withholding_scheme 추가 — Flash가 생산(cognition:462,469)하고
            # 소비자 2곳(slot_manager rotation / iceberg.translate_prev_scheme)이 대기 중인데
            # 화이트리스트에 키가 없어 저장 시 버려지고 있었음(끊긴 배선).
            fingerprint = _normalize_render_fingerprint(rfp)
            domain_manager.update_scene_continuity(channel_id, render_fingerprint=fingerprint)
            logger.debug("[RenderFP] Stored: gaze=%s, lighting=%s",
                         (fingerprint.get("gaze") or "")[:50], (fingerprint.get("lighting") or "")[:50])
            _gaze_now = str(fingerprint.get("gaze", "") or "")
    except Exception as e:
        _fail("render_fingerprint", e)

    # [2026-09-02 R4] 관찰 → 위치 쓰기 (입장·이동). 스펙 §2.6 ⓒ / §2.7 ⓓ / §6 R4.
    # 병: 출석이 위치의 함수가 되는 순간, 아무도 배치하지 않으면 **영원히 등장 못 하는
    #   사람**이 생긴다(§2.6 — 현행 Flash 기반엔 없던 구멍). 그렇다고 "언급되면 배치"로
    #   열면 R4가 고치려던 병("이름만 부르면 무대에 뜬다")이 위치 축으로 그대로 이사한다.
    # 처방: 입장은 **서술이 결정한다** — §2.6 "산문 우선·psyche 보조"의 실물.
    #   ① gaze = 1순위(강). `render_fingerprint.gaze`는 렌더 **산문에서** 뽑은
    #      "카메라가 이번 턴 실제로 머문 등록 NPC 이름"이다(cognition gaze 조항:
    #      SceneNPCs 이름 정확일치, 오프스테이지·무명 제외). 이미 매 턴 도는 산문 우선
    #      게이트라 새 콜 0. 그러므로 **이미 어디에 있든** PC 노드로 옮긴다 —
    #      "카메라가 머물렀다 = 그 사람은 여기 있다"가 저장된 위치를 이긴다(관찰 > 위치).
    #   ② psyche_states = 보조(약). **`unplaced`(어느 노드에도 없는) 인물만** 입장.
    #      이미 배치된 인물은 psyche로 **옮기지 않는다** — psyche 조항엔 (R5 전까지)
    #      "이 장면에 실재하는 인물만"이 없어서, 언급 한 번에 텔레포트가 일어난다.
    #      Flash 판정은 인물당 딱 한 번(unplaced → placed)만 개입한다(§2.6).
    # ★자리: gaze는 render_fingerprint가 **렌더 후**에 추출하므로 이 함수 위쪽의
    #   P0/mark 루프 시점엔 아직 존재하지 않는다. fingerprint 저장 **직후**가 유일한
    #   자리다. 그 시점엔 update_world_state(STEP 3)의 set_current_location도 이미 돌아
    #   PC 노드가 서 있다(없으면 set_npc_location이 location_not_found로 조용히 샌다).
    # ⚠ 1턴 지연은 **의도**다 — 이번 턴 관찰이 다음 턴 출석이 된다. 현행
    #   `_last_appear_turn`도 턴 끝에 찍고 다음 턴에 읽는 같은 구조다(부기의 지연이지
    #   장면의 지연이 아니다). latch(§2.1) 덕분에 한 턴 늦어도 사람이 사라지지 않는다.
    # ⚠ 등록되지 않은 이름은 배치하지 않는다 — 노드에 낯선 문자열을 앉히면 그것이
    #   그대로 0단(출석)이 되어 상태창에 뜬다. 해상 실패는 침묵이 정답.
    try:
        import world_tree as _wt_obs
        _cur_loc = domain_manager.get_current_location(channel_id)
        if _cur_loc and str(_cur_loc).strip().lower() not in ("", "unknown"):
            _pc_masks_obs = domain_manager.pc_registry(channel_id)   # [2026-10-07 PC 이름 단일화]
            _reg_obs = domain_manager.get_npcs(channel_id) or {}

            def _obs_key(_raw):
                """관찰된 이름 → 등록 키. 미등록·PC 가면은 빈 문자열(=건너뜀)."""
                _r = str(_raw or "").strip()
                if not _r or domain_manager.is_pc_name(_r, _pc_masks_obs):
                    return ""
                _k = domain_manager._find_npc_key(_reg_obs, _r)
                return _k if (_k and not domain_manager.is_pc_name(_k, _pc_masks_obs)) else ""

            # [2026-09-03 R6] 카운터 옆에 **이름 집합**(_observed_now, 위에서 초기화)도 채운다.
            #   아래 스케줄 틱이 "이번 턴 관찰이 손댄 사람"을 제외 집합에 넣어야 하는데,
            #   개수만으로는 누구였는지 알 수 없다(관찰 > 스케줄의 실물이 이 집합이다).
            _gaze_moved = 0
            for _g in str(_gaze_now or "").replace("\n", ",").split(","):
                _gk = _obs_key(_g)
                if _gk and _wt_obs.set_npc_location(channel_id, _gk, _cur_loc) == "placed":
                    _gaze_moved += 1
                    _observed_now.add(_gk)
            _psy_entered = 0
            for _pn in ((ctx.dai or {}).get("psyche_states") or {}):
                _pk = _obs_key(_pn)
                if not _pk or _wt_obs.get_npc_location(channel_id, _pk):
                    continue   # 미등록이거나 **이미 배치됨** → psyche는 옮기지 않는다
                if _wt_obs.set_npc_location(channel_id, _pk, _cur_loc) == "placed":
                    _psy_entered += 1
                    _observed_now.add(_pk)
            if _gaze_moved or _psy_entered:
                logger.info(
                    "[presence-check] observed→location: gaze=%d placed/moved, "
                    "psyche=%d entered @ %s", _gaze_moved, _psy_entered, _cur_loc)
    except Exception as _e_obs:
        logger.debug(f"[presence-check] observation write skipped: {_e_obs}")

    # [2026-09-03 R6] 스케줄 틱 - 시간대 **전환 턴**에만 도는 자율 이동. 스펙 §6 R6 ③ / §2.8.
    # 병: 2단(부재)이 "실제로 다른 곳에 있는 사람"이 아니라 "아직 아무도 안 옮긴 사람"이다.
    #   시트에 루틴이 적혀 있어도 위치는 관찰이 건드릴 때까지 그대로라, 무대 밖 세계가 정지한다.
    #   (07-14에 지운 P3 랜덤 활동이 하려던 일의 Contract-First 형태 = 시트 루틴은 발명이 아니다.)
    # 처방 ① 전환 감지는 **마커 비교**지 writer 훅이 아니다. 시간대 writer는 3경로
    #   (game_world.advance_minutes/advance_to_slot/advance_time, `!시간` 수동, game_system)라
    #   훅을 달면 같은 코드가 3벌로 복제된다. world_state에 `schedule_tick_slot` 한 칸을 두고
    #   현재 time_slot과 비교하면 자리가 하나로 모이고, 수동 전환도 다음 턴에 자연히 잡힌다.
    #   마커가 같으면 완전 무동작(로그도 없다). 마커 부재 = 세션 첫 턴이 곧 첫 틱.
    # 처방 ② **관찰 > 스케줄.** 제외 집합 = 0단(tiers["scene"]) ∪ 이번 턴 관찰이 옮기거나
    #   입장시킨 이름 ∪ PC 가면 ∪ 비활성(is_npc_active). 0단 제외가 latch(§2.1)의 실물이다:
    #   스케줄은 **무대 밖 사람만** 움직인다. 눈앞의 인물이 루틴 때문에 사라지면 안 된다.
    # 처방 ③ PC 노드를 못 세는 턴(tiers.unresolved)은 **틱 전체를 건너뛰고 마커도 안 찍는다.**
    #   0단을 모르면 누구를 지켜야 할지도 모른다. 안전 쪽으로 넘어지고, 해상되는 다음 턴에 돈다.
    # 새 LLM 콜 0 - 순수 코드다. 루틴 장소가 마침 PC 노드면 다음 턴 0단이 되는데 그건 의도다
    #   (근거 있는 등장). 자리는 관찰 쓰기 **뒤** - 그래야 "이번 턴 관찰 집합"과 0단이 확정된다.
    try:
        _ws_tick = domain_manager.get_world_state(channel_id) or {}
        _slot_now = str(_ws_tick.get("time_slot", "") or "").strip()
        # 마커는 **비교 전에** 떠 둔다. get_world_state는 도메인의 살아 있는 dict를
        #   그대로 돌려주므로, 아래에서 마커를 찍은 뒤에 읽으면 새 값이 나온다.
        _prev_slot = str(_ws_tick.get("schedule_tick_slot", "") or "-")
        if _slot_now and _ws_tick.get("schedule_tick_slot") != _slot_now:
            _tiers_tick = npc_manager.get_presence_tiers(channel_id) or {}
            if _tiers_tick.get("unresolved"):
                logger.debug("[presence-check] schedule tick skipped: PC node unresolved "
                             "(slot=%s, marker unchanged)", _slot_now)
            else:
                import world_tree as _wt_sch
                # 관찰 쓰기가 예외로 죽은 턴이면 위 초기화 그대로 빈 집합 = 관찰이 옮긴 사람 0명.
                _obs_excl = set(_observed_now)
                _pc_masks_sch = set()
                try:
                    for _p in domain_manager.get_domain(channel_id).get("participants", {}).values():
                        if isinstance(_p, dict) and _p.get("mask"):
                            _pc_masks_sch.add(_p["mask"])
                except Exception:
                    pass
                _scene_tick = set(_tiers_tick.get("scene") or [])
                _excluded = _scene_tick | _obs_excl | _pc_masks_sch
                _sch_moved = 0
                for _sn, _sd in (domain_manager.get_npcs(channel_id) or {}).items():
                    if not isinstance(_sd, dict) or _sn in _excluded:
                        continue
                    if not npc_manager.is_npc_active(_sd):
                        continue   # 생존축 필터는 tiers와 **같은 관문** 하나로만
                    _sch_dest = npc_manager._schedule_entry(_sd, _slot_now)[1]
                    if not _sch_dest:
                        continue   # 레거시 문자열형·장소 없는 신형 = 이동 없음
                    _dest_id = _wt_sch.resolve_node_id(channel_id, _sch_dest)
                    if not _dest_id:
                        # 시트 거처 폴백 배치(npc_manager.update_npc)와 같은 형태.
                        _ar = _wt_sch.add_node(channel_id, _sch_dest, node_type="area",
                                               properties={"tags": ["schedule"]})
                        if _ar != "created":
                            logger.debug("[presence-check] schedule node '%s' not created: %s",
                                         _sch_dest, _ar)
                            continue   # capped 등 - 없는 노드에 앉히지 않는다
                        _dest_id = _wt_sch.resolve_node_id(channel_id, _sch_dest)
                    _cur_node = _wt_sch.get_npc_location(channel_id, _sn)
                    if _cur_node and _wt_sch.resolve_node_id(channel_id, _cur_node) == _dest_id:
                        continue   # 이미 그 노드 - 불필요한 저장 없음
                    if _wt_sch.set_npc_location(channel_id, _sn, _sch_dest) == "placed":
                        _sch_moved += 1
                _ws_mark = domain_manager.get_world_state(channel_id) or {}
                _ws_mark["schedule_tick_slot"] = _slot_now
                domain_manager.update_world_state(channel_id, _ws_mark)
                if _sch_moved:
                    logger.info("[presence-check] schedule tick %s→%s: moved=%d "
                                "(scene=%d, observed=%d excluded)",
                                _prev_slot, _slot_now, _sch_moved,
                                len(_scene_tick), len(_obs_excl))
                else:
                    logger.debug("[presence-check] schedule tick %s→%s: moved=0 "
                                 "(scene=%d, observed=%d excluded)",
                                 _prev_slot, _slot_now, len(_scene_tick), len(_obs_excl))
    except Exception as _e_sch:
        logger.debug(f"[presence-check] schedule tick skipped: {_e_sch}")
