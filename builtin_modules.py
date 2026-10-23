"""기본 모듈 선언 (2026-10-22 C58 M0·M1).

스펙  파티쳇수정/output_artifact/C58_모듈틀_M0·M1_스펙_2026-10-22.md

- 등록 순서 = `!모듈` 목록 순서(핵심: 판정·둠·이변 / 토글: 게시판·속마음·활력 / 설정: 상태패널·지속 감사·Reader-GM).
- M1: 렌더 뒤 배경 다섯(옛 orchestration.execute 9.55·9.56·9.6·9.7·9.8)을 **글자 그대로** 옮겼다.
    게이트·붙잡는 값은 함수의 동기 부분(적재 전), 코루틴 본문·큐 이름·LOW·로그 문구는 옛 그대로.
    바뀐 건 이름뿐: self → rt, 지역 변수 → turn.<필드>.
- 큐 적재는 `_btq.enqueue_background_task`(모듈 속성)로 부른다 — 스모크가 background_task_queue 를 바꿔 끼우면 먹는다.
- 일 하는 모듈(status_panel·turn_mail·persistent_audit·reader_gm)은 옛 자리처럼 함수 안에서 늦게 import 한다.
- [2026-10-23 C63 M4] 렌더 뒤 배경 추출 셋(B-Outputs·B-Batch·발효)과 그 소비자들 — 몸은 shared_extract, 선언은 여기.
    background 2 outputs · 4 world · 6 ferment(상태패널 10 앞 = 옛 큐 순서) / after_outputs 10·30·40·60 / after_batch 10·20·50·60·70.
- [2026-10-23 C64 M5] 렌더 앞 — 입력 갈래(OOC·루카)는 ooc 모듈 on_input(몸 luka.py), 도착물은 게시판 pre_render_call
    (몸 world_board.handout_station), persistent view 셋은 add_view(screen 10·20 · ooc 30). 몸은 함수 안에서 늦게 import(순환 방지).
- [2026-10-23 C62 M3] 날 경계 일 넷(옛 boundary_engine.SUBSCRIBERS)이 ON_DAY 정거장으로 왔다.
    순서 = orders[ON_DAY] 10 출력룰(하루 연산) → 20 활력(NPC 회복) → 30 일지 → 40 상태패널(day 칸).
    값(10·20)이 먼저 서야 일지·패널 콜(30·40, 배경 큐)이 오늘 값을 본다.
"""
import logging

import config
import domain_manager
import background_task_queue as _btq
from background_task_queue import TaskPriority
from module_frame import Module, Call, Toggle, register, BACKGROUND, ON_DAY, AFTER_OUTPUTS, AFTER_BATCH, channel_toggle
from module_frame import ON_INPUT, PRE_RENDER_CALL, View, add_view   # [C64 M5]
from module_frame import Setting   # [C72 A5]
import shared_extract as _sx   # [C63 M4] 공용 추출 몸 — 이 파일은 선언만

# 옮긴 로그 줄은 옛 자리와 같은 로거로 남긴다(로그 모양·필터 그대로).
logger = logging.getLogger("Orchestration")
_blog = logging.getLogger("BoundaryEngine")     # [C62 M3] 옛 boundary_engine 구독자 로그 자리


# =====================================================================
# 핵심 — 늘 켬 (렌더 앞 몫 = waterfall 본문이 직접 부른다 · 정거장 이사는 전선 정리 때 — C64 §6-5)
# =====================================================================
register(Module(id="judgment", name="판정", tier="core", shelf="core",
                stations={AFTER_OUTPUTS: _sx.out_status}, orders={AFTER_OUTPUTS: 40}))   # [C63 M4] 좋은 상태·타격칸(판정이 읽는다)
register(Module(id="doom", name="둠", tier="core", shelf="core"))
register(Module(id="anomaly", name="이변", tier="core", shelf="core"))


# =====================================================================
# 토글 — 기본 ON, 명시 OFF 만 끈다 (domain_manager 두 축 그대로)
# =====================================================================
# [C62 M3] 토글 헬퍼(옛 _mod_on·_mod_set)는 module_frame.channel_toggle 로 올라갔다 — 이식 모듈도 같은 걸 쓴다.
async def _pre_board(rt, b):
    """[C64 M5] 도착물 핸드아웃 — 옛 orchestration 5.5. 몸은 world_board(늦게 import)."""
    import world_board as _wb_pr
    await _wb_pr.handout_station(rt, b)


# [2026-10-23 C72 A5] 게시판 설정 칸 여섯 — 저장은 지금 자리 그대로(world_state.world_board, `!게시판` 명령과 같은 함수).
_BOARD_CH = (("bulletin", "공지"), ("sns", "SNS"), ("message", "메시지"))
_BOARD_SHOW = (("button", "버튼"), ("thread", "스레드"), ("off", "끄기"))


def _board_ch_get(ch_name):
    def _g(channel_id):
        import world_board as _wb
        return bool(_wb.get_board_channels(channel_id).get(ch_name, True))
    return _g


def _board_ch_set(ch_name):
    def _s(channel_id, value):
        import world_board as _wb
        _wb.set_board_channel(channel_id, ch_name, bool(value))
    return _s


def _board_show_get(ch_name):
    def _g(channel_id):
        import world_board as _wb
        return _wb.get_display_mode(channel_id, ch_name)
    return _g


def _board_show_set(ch_name):
    def _s(channel_id, value):
        import world_board as _wb
        _wb.set_display_mode(channel_id, ch_name, str(value))
    return _s


_BOARD_SETTINGS = tuple(
    [Setting(f"ch.{k}", lb, "switch", _board_ch_get(k), _board_ch_set(k)) for k, lb in _BOARD_CH]
    + [Setting(f"show.{k}", f"{lb} 표시", "select", _board_show_get(k), _board_show_set(k), _BOARD_SHOW)
       for k, lb in _BOARD_CH])


register(Module(
    id="board", name="게시판", tier="toggle", shelf="toggle",
    toggle=channel_toggle("board"),
    aliases=("게시판",),
    stations={PRE_RENDER_CALL: _pre_board},   # [C64 M5] 꺼진 채널이면 틀 토글이 건너뛴다(§6-2)
    calls=(Call("WorldBoard", "light", "선언 전이 deliver 또는 분석 arrival 신호가 있을 때(산문이 기다림)"),),
    settings=_BOARD_SETTINGS,
))


# ---------------------------------------------------------------------
# 속마음 — 옛 9.56
# ---------------------------------------------------------------------
async def _bg_mind(rt, turn):
    # 9.56. [2026-08-17 속마음 v1] 💭 속마음 — **기본 on**(전역 TURN_MIND_ENABLED
    #   × 채널 모듈 "mind"). 상태패널과 **같은 시점·같은 큐**(LOW)의 배경 콜이라
    #   턴 임계 경로에 1ms도 얹지 않는다.
    #     [게이트] 무대 ∩ 점수 → 0명이면 콜도 저장도 없다(조용).
    #     [콜]     선별분 psyche + 구조 장면 앵커 → NPC별 한국어 1인칭 한 호흡
    #     [폴백]   콜 실패·TURN_MIND_CALL=0 → v0 선별기(콜 0)가 같은 명단으로 선다
    #   ⚠[2026-08-17 앵커 교체] 산문(response)을 **넘기지 않는다**. 구 배선은
    #     직전 렌더 원문 꼬리를 콜 입력에 실었고, 그래서 "대사 복붙 금지"를
    #     프롬프트로 방어해야 했다. 장면 앵커는 turn_mail 이 채널·DAI(구조층)
    #     에서 직접 세운다 — 상태패널(9.55)과 달리 여기는 산문 무접촉이다.
    channel_id, ctx, sent_msgs = turn.channel_id, turn.ctx, turn.sent_msgs
    try:
        import turn_mail as _tm_mod
        if _tm_mod.mind_enabled(channel_id) and sent_msgs:
            _mind_dai = dict(ctx.dai) if ctx.dai else {}
            _mind_msg = sent_msgs[-1]

            async def _run_turn_mind():
                _targets = _tm_mod.select_mind_targets(channel_id, _mind_dai)
                if not _targets:
                    return          # 대상 0명 = mail 미생성(버튼도 안 붙는다)
                _names = [t["name"] for t in _targets]
                _payload = None
                if int(getattr(config, "TURN_MIND_CALL", 1) or 0):
                    # [2026-08-17 light 라우트] 상태 패널과 같은 자리 — 코루틴 안에서 감싼다.
                    with config.light_call():
                        _payload = await _tm_mod.generate_mind_call(
                            rt.client, config.role_model("light"), channel_id,
                            _mind_dai, targets=_targets)
                if not _payload:
                    # 결정론 폴백 — 콜이 죽어도 💭는 그 턴 재료로 뜬다
                    _payload = _tm_mod.generate_mind(channel_id, _mind_dai, names=_names)
                if not _payload:
                    return
                logger.info("[TurnMind] source=%s npcs=%d",
                            _payload.get("source", "?"), len(_payload.get("entries") or []))
                await _tm_mod.deliver(
                    _mind_msg, channel_id, _tm_mod.KIND_MIND, _payload)

            await _btq.enqueue_background_task(
                channel_id, "TurnMind", _run_turn_mind,
                priority=TaskPriority.LOW,
            )
    except Exception as _e_tm:
        logger.debug(f"[TurnMail] mind enqueue skip: {_e_tm}")


register(Module(
    id="mind", name="속마음", tier="toggle", shelf="toggle", order=20,
    stations={BACKGROUND: _bg_mind},
    calls=(Call("TurnMind", "light", "무대 NPC 중 고를 대상이 있을 때"),),
    toggle=channel_toggle("mind"),
    aliases=("속마음", "💭"),
))

# ---------------------------------------------------------------------
# 무대 밖 NPC 회복 — 옛 boundary_engine._sub_npc_rest (구독자 0b)
# ---------------------------------------------------------------------
async def _day_npc_rest(rt, day):
    """[2026-10-12 C23] 무대 밖 NPC 회복 — **동기 · 콜 0**.

    NPC 활력·평형을 올리는 문은 추출 델타뿐인데 추출은 무대 위만 본다. 무대 밖에서 지난 하루를
    코드가 맡는다(custom_vars.npc_rest_tick: init과의 차이를 하루에 절반씩, days_jumped만큼).
    expr day 정산 바로 뒤 — 하루 연산이 먼저 서고, 일지·패널 콜은 회복까지 끝난 오늘 값을 본다.
    """
    try:
        import custom_vars as _cv
        _cv.npc_rest_tick(day.channel_id, (day.info or {}).get("days_jumped", 1))
    except Exception as e:
        _blog.warning("[Boundary] NPC 회복 실패: %s", e)


register(Module(
    id="vigor", name="활력", tier="toggle", shelf="toggle",
    stations={ON_DAY: _day_npc_rest}, orders={ON_DAY: 20},
    toggle=Toggle(lambda ch: domain_manager.is_vigor_composure_active(ch),
                  lambda ch, on: domain_manager.set_vigor_composure_active(ch, on)),
    aliases=("활력", "평형", "mental"),
    off_notice=("이 채널의 활력/평형 처리·수치 변동·프롬프트 주입이 모두 중단됩니다. "
                "(수치는 현재값으로 동결)"),
))


# =====================================================================
# 설정으로 도는 것 — !모듈 에 보이기만(끄고 켜기는 !출력룰·config)
# =====================================================================

# ---------------------------------------------------------------------
# 상태패널 — 옛 9.55
# ---------------------------------------------------------------------
async def _bg_status_panel(rt, turn):
    # 9.55. [2026-08-16 상태패널 v0] 하단 상태 패널 — 배경 콜 1개 + 코드 저장.
    #   패널 정의(!출력룰 panel/상태창) 미등록이면 콜 0. 산문은 **저장본과 같은
    #   순수 response**(표시용 헤더가 섞인 문자열이 아니다 — 헤더는 표시 계층 전용).
    channel_id, response = turn.channel_id, turn.response
    try:
        import status_panel as _sp_mod
        if _sp_mod.get_panel_definition(channel_id):
            _sp_prose = response

            async def _run_status_panel():
                # [2026-08-17 light 라우트] 단문 배경 콜 → 경량 모델.
                # with 가 **코루틴 안**에 있어야 실행 시점 컨텍스트에 걸린다
                # (큐 적재 바깥에서 감싸면 실행 전에 reset 된다).
                with config.light_call():
                    _res = await _sp_mod.generate_panel(
                        rt.client, config.role_model("light"), channel_id, _sp_prose)
                if not _res or not _sp_mod.apply_panel_result(channel_id, _res):
                    return
                logger.info(
                    "[StatusPanel] updated turn=%s fields=%d comments=%d",
                    domain_manager.get_world_state(channel_id).get("turn_index", 0),
                    len(_res.get("fields") or {}), len(_res.get("comments") or []))

            await _btq.enqueue_background_task(
                channel_id, "StatusPanel", _run_status_panel,
                priority=TaskPriority.LOW,
            )
    except Exception as _e_sp:
        logger.debug(f"[StatusPanel] enqueue skip: {_e_sp}")


def _status_panel_line(ch):
    import status_panel as _sp_mod
    return "패널 정의 있음" if _sp_mod.get_panel_definition(ch) else "패널 정의 없음"


async def _day_panel(rt, day):
    # [2026-10-23 C62 M3] 옛 boundary_engine 구독자 B(패널 day 칸) — 일지와 같은 하루 콜 산출을 나눠 쓴다.
    #   적재·게이트·병합 몸은 boundary_engine.queue_panel_day 그대로. warning 줄은 옛 구독자 루프 것.
    try:
        import boundary_engine as _be
        await _be.queue_panel_day(day.channel_id, day.info)
    except Exception as e:
        _blog.warning("[Boundary] subscriber %s failed: %s", "queue_panel_day", e)


register(Module(
    id="status_panel", name="상태패널", tier="declared", shelf="config", order=10,
    stations={BACKGROUND: _bg_status_panel, ON_DAY: _day_panel, AFTER_OUTPUTS: _sx.out_append},
    orders={ON_DAY: 40, AFTER_OUTPUTS: 60},
    calls=(Call("StatusPanel", "light", "패널 정의(!출력룰 panel/상태창)가 있을 때"),),
    status=_status_panel_line,
))


# ---------------------------------------------------------------------
# 지속 감사 — 옛 9.6
# ---------------------------------------------------------------------
async def _bg_persist_audit(rt, turn):
    # 9.6. [C안 2026-07-02] 영속층 감사 — N턴마다 백그라운드 (log-only, 검출≠쓰기).
    # knowledge/relations/world_tree 자동 적립분의 모순·중복·고아·출처불명 검출.
    channel_id = turn.channel_id
    try:
        _pa_interval = getattr(config, "PERSIST_AUDIT_INTERVAL", 0)
        _pa_turn = int(domain_manager.get_world_state(channel_id).get("turn_index", 0) or 0)
        if _pa_interval > 0 and _pa_turn > 0 and _pa_turn % _pa_interval == 0:
            import persistent_audit as _pa_mod

            async def _run_persist_audit():
                await _pa_mod.run_persistent_audit(rt.client, rt.model_id_flash, channel_id)

            await _btq.enqueue_background_task(
                channel_id, "PersistentAudit", _run_persist_audit,
                priority=TaskPriority.LOW,
            )
            logger.info(f"[PersistAudit] enqueued at turn {_pa_turn}")
    except Exception as _e_pa:
        logger.debug(f"[PersistAudit] enqueue skip: {_e_pa}")


def _every_line(n):
    n = int(n or 0)
    return "꺼짐" if n <= 0 else ("매 턴" if n == 1 else f"{n}턴마다")


register(Module(
    id="persist_audit", name="지속 감사", tier="core", shelf="config", order=30,
    stations={BACKGROUND: _bg_persist_audit},
    calls=(Call("PersistentAudit", "flash", "PERSIST_AUDIT_INTERVAL 턴마다"),),
    status=lambda ch: _every_line(getattr(config, "PERSIST_AUDIT_INTERVAL", 0)),
))


# ---------------------------------------------------------------------
# Reader-GM — 옛 9.7 + 9.8 (이 순서, 각자 try)
# ---------------------------------------------------------------------
async def _bg_reader(rt, turn):
    channel_id, ctx, response = turn.channel_id, turn.ctx, turn.response

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
                    rt.client, channel_id, _rg_turn, _rg_prose, _rg_block)

            await _btq.enqueue_background_task(
                channel_id, "ReaderGM", _run_reader_gm,
                priority=TaskPriority.LOW,
            )
    except Exception as _e_rg:
        logger.debug(f"[Reader] enqueue skip: {_e_rg}")

    # 9.8. [Reader-GM Stage 3-A] 間 진입 엣지 → 수신형 시드 번역 (배경 LOW, 1회/진입).
    try:
        if getattr(config, "READER_GM_SEED", 0):
            _interm_now = bool((getattr(getattr(ctx, "bus", None), "doom", None) or {}).get("intermission_active"))
            _mem_rs = domain_manager.get_session_ai_memory(channel_id) or {}
            _interm_prev = bool(_mem_rs.get("_reader_seed_interm_prev"))
            if _interm_now != _interm_prev:
                domain_manager.update_session_ai_memory(
                    channel_id, {"_reader_seed_interm_prev": _interm_now})
            if _interm_now and not _interm_prev:
                import reader_gm as _rs_mod

                async def _run_reader_seed():
                    await _rs_mod.run_seed_replenish(rt.client, channel_id)

                await _btq.enqueue_background_task(
                    channel_id, "ReaderSeed", _run_reader_seed,
                    priority=TaskPriority.LOW,
                )
                logger.info("[ReaderSeed] enqueued (間 entry)")
    except Exception as _e_rs:
        logger.debug(f"[ReaderSeed] enqueue skip: {_e_rs}")


def _reader_line(ch):
    seed = "間 시드 켬" if getattr(config, "READER_GM_SEED", 0) else "間 시드 꺼짐"
    return f"{_every_line(getattr(config, 'READER_GM_INTERVAL', 0))} · {seed}"


register(Module(
    id="reader", name="Reader-GM", tier="core", shelf="config", order=40,
    stations={BACKGROUND: _bg_reader},
    calls=(Call("ReaderGM", "reader", "READER_GM_INTERVAL 턴마다"),
           Call("ReaderSeed", "reader", "間에 들어선 턴(READER_GM_SEED)")),
    status=_reader_line,
))


# =====================================================================
# 표면 주인 — [2026-10-23 C60 M2] `!모듈` 목록엔 안 선다(shelf ""). 버튼·탭은 일 하는 모듈이 붙인다:
#   turn_mail = 턴 꼬리 버튼(💠 screen · 💌📰 board · 💭 mind) / activity_server = Activity 탭.
#   맨 끝에 등록한다 — 위 아홉의 등록 순서(= `!모듈` 목록 순서)를 건드리지 않게.
# =====================================================================
register(Module(id="screen", name="상태창·보관함", tier="core",
                stations={AFTER_OUTPUTS: _sx.out_notebook}, orders={AFTER_OUTPUTS: 30}))   # [C63 M4] 노트북 LLM 메모
register(Module(id="wiki", name="위키", tier="core"))


# ---------------------------------------------------------------------
# 하루 연산 — 옛 boundary_engine._sub_expr_day (구독자 0)
# ---------------------------------------------------------------------
async def _day_expr(rt, day):
    """[2026-09-09 P11] `cadence=day` 전이 정산 — **동기 · 콜 0**.

    맨 앞에 서는 이유: 일지·패널 day 섹션은 "어제 무슨 일이 있었나"를 읽는 배경 콜이고,
    하루 연산(나이+1·수분-1…)은 그 어제의 **값**이다. 값이 먼저 서야 읽는 쪽이 오늘을 본다.
    배경 큐에 넣지 않는다 — 코드뿐이라 렌더 지연이 0 이고, 큐에 넣으면 도리어 일지 콜과
    순서가 갈린다.
    """
    try:
        import expr_engine as _ee
        _ee.run_day(day.channel_id, None, day.info)
    except Exception as e:
        _blog.warning("[Boundary] expr day 실패: %s", e)


register(Module(id="output_rules", name="출력룰", tier="declared",
                stations={ON_DAY: _day_expr, AFTER_OUTPUTS: _sx.out_values},
                orders={ON_DAY: 10, AFTER_OUTPUTS: 10}))   # [C63 M4] 값 델타 + cue·연산 적재


# =====================================================================
# 일지 — [2026-10-23 C62 M3] 옛 boundary_engine 구독자 A. 끌 수 없는 핵심이라 `!모듈` 목록엔 안 선다(shelf "").
#   하루 콜(BoundaryDayDigest) 하나를 상태패널 day 칸(40)과 나눠 쓴다 — 콜·캐시·일지 쓰기 몸은 boundary_engine.
# =====================================================================
async def _day_journal(rt, day):
    try:
        import boundary_engine as _be
        await _be.queue_journal(day.channel_id, day.info)
    except Exception as e:
        _blog.warning("[Boundary] subscriber %s failed: %s", "queue_journal", e)


register(Module(
    id="journal", name="일지", tier="core",
    stations={ON_DAY: _day_journal}, orders={ON_DAY: 30},
    calls=(Call("BoundaryDayDigest", "light", "날짜가 넘어간 턴(상태패널 day 칸과 한 콜)"),),
))


# =====================================================================
# 공용 추출 — [2026-10-23 C63 M4] 옛 orchestration step 9(schedule_background_extraction) + 두 적용부.
#   끌 수 없는 핵심이라 `!모듈` 목록엔 안 선다(shelf ""). 토글이 없어 틀 토글도 안 걸린다(동작 변화 0).
#   맨 끝 등록 — 위 열셋의 등록 순서를 건드리지 않게.
# =====================================================================
register(Module(
    id="outputs", name="출력물 추출", tier="core", order=2,
    stations={BACKGROUND: _sx.bg_outputs},
    calls=(Call("B-Outputs", "light", "선언 언급·cue·연산·append 장부·노트북 힌트·타격칸 중 하나라도"),),
))
register(Module(
    id="world", name="세계 추출", tier="core", order=4,
    stations={BACKGROUND: _sx.bg_world, AFTER_BATCH: _sx.after_world}, orders={AFTER_BATCH: 50},
    calls=(Call("B-Batch", "flash", "힌트 하나라도(사실상 매 턴)"),),
))
register(Module(
    id="ferment", name="발효", tier="core", order=6,
    stations={BACKGROUND: _sx.bg_ferment},
    calls=(Call("Fermentation", "main", "발효·연대기 임계를 넘을 때"),),
))
register(Module(id="quest", name="퀘스트", tier="core", order=10, stations={AFTER_BATCH: _sx.after_quest}))
register(Module(id="npc", name="인물", tier="core", order=20, stations={AFTER_BATCH: _sx.after_npc}))
register(Module(
    id="narrative", name="줄거리·arc", tier="core", order=60, stations={AFTER_BATCH: _sx.after_narrative},
    calls=(Call("Play Section Condense", "heavy", "인물 관찰이 250자 넘게 쌓일 때(B3)"),
           Call("Storyline Summary", "flash", "요약 임계를 넘을 때(B4)")),
))
register(Module(id="presence", name="출석·위치", tier="core", order=70, stations={AFTER_BATCH: _sx.after_presence}))


# =====================================================================
# [2026-10-23 C64 M5] 렌더 앞 — OOC·루카(on_input) + persistent view
#   ooc 는 끌 수 없는 핵심이라 `!모듈` 목록엔 안 선다(shelf ""). 맨 끝 등록 — 위 스물의 등록 순서를 건드리지 않게.
# =====================================================================
async def _in_ooc(rt, b):
    """옛 main._process_message 3 · 3a · 3.5. 몸은 luka(늦게 import — command_handler 순환 방지)."""
    import luka as _lk
    await _lk.on_input(rt, b)


register(Module(
    id="ooc", name="OOC·루카", tier="core", order=10,
    stations={ON_INPUT: _in_ooc},
    calls=(Call("Luka", "flash", "단독 OOC · 루카 모드 — 표지(⟦수정⟧·⟦장면⟧) 판정을 겸한다"),
           Call("OOC Route", "flash", "행동+OOC — 수정 조각·장면 지시 가르기"),
           Call("OOC Edit", "main", "수정 조각이 있을 때(정정 되묻기 버튼 포함)")),
))


def _view_turn():
    import turn_mail
    return turn_mail.TurnView()


def _view_archive():
    import status_panel
    return status_panel.ArchiveView()


def _view_correction():
    import command_handler
    return command_handler.CorrectionAskView()


# 순서 = 옛 on_ready 손글 순서. 💠 custom_id 가 TurnView·ArchiveView 둘에 있어 나중 등록(ArchiveView)이 이긴다 — 응답은 같은 곳.
add_view("screen", View("TurnView", _view_turn, 10,
                        "[TurnMail] persistent view registered", "[TurnMail] view registration failed: {e}"))
add_view("screen", View("ArchiveView", _view_archive, 20,
                        "[StatusPanel] archive button handler registered",
                        "[StatusPanel] archive button registration failed: {e}"))
add_view("ooc", View("CorrectionAskView", _view_correction, 30,
                     "[OOC] correction ask view registered", "[OOC] correction ask view registration failed: {e}"))
