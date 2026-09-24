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
# [2026-09-07 P9] 매턴 하단 상태 임베드 — 세 send 경로가 모두 쓴다(구 헤더 접합의 자리).
import status_panel
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
# [2026-09-18 식별 허브 S7] 인라인 태그 헬퍼는 response_processor 로 이사 —
#   `_classify_opening`(반복 검출)도 같은 판정이 필요하다. 이름은 유지(기존 스모크 무변경).
from response_processor import _INLINE_TAG_WRAP, _unwrap_inline_tag  # noqa: F401


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
    - 출처 판정(response_processor._is_supplied_by_input, 음절키 겹침)으로 에코 → 제외
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
        _inner = _unwrap_inline_tag(stripped)
        if _inner is not None:
            if not _inner:
                continue
            _shown = stripped   # 피드백 예시엔 태그째 보인다 — 모델이 제 줄을 알아보게
            stripped = _inner
        else:
            _shown = stripped
        if not stripped or system_pat.match(stripped) or tag_pat.match(stripped):
            continue

        # PC 이름으로 시작하는 줄 체크
        is_pc_line = _pc_pats and any(p.match(stripped) for p in _pc_pats)
        if is_pc_line:
            # PC 대사가 있는지 확인
            q_match = quote_pat.search(stripped)
            if q_match:
                ai_quote = q_match.group(1).strip()[:20]
                # [2026-08-28 충돌 수리] 구 판정은 **앞 5자 접두 일치**였다 —
                #   Slot 18이 `PC dialogue = player-supplied only (polish flow) … Never copy
                #   verbatim`이라고 시키는데, 다듬을수록 접두가 어긋나 사칭으로 잡히고
                #   **그대로 베낄수록 통과**했다. ★지시가 시키는 걸 검출기가 벌하는 형태
                #   ([[project-preset-aos]] 08-13과 같은 병, 다른 자리).
                #   → 08-13에 이미 배포한 **출처 판정**(음절키 겹침, 활용·불규칙 흡수)으로 교체.
                #   ★단조 안전: 판정 재료가 없으면(입력 무·짧음) 구 접두 규칙으로 폴백.
                is_echo = False
                if user_input:
                    try:
                        from response_processor import _is_supplied_by_input as _origin_ok
                        is_echo = _origin_ok(ai_quote, user_input, pc_names or [])
                    except Exception:
                        is_echo = False
                if not is_echo and _user_quotes:
                    is_echo = any(
                        ai_quote[:5] in uq or uq[:5] in ai_quote
                        for uq in _user_quotes
                    )
                if not is_echo:
                    # AI가 창작한 PC 대사 → 사칭
                    impersonations.append(stripped[:40])
            continue  # PC 줄은 포맷 위반 체크에서 항상 제외

        # [2026-08-13 대사 포맷 부활] 전면 강제 → 독립 대사줄 한정
        if _is_bare_speech_line(stripped) and not correct_pat.match(stripped):
            violations.append(_shown[:40])

    # [2026-08-13 기본형 승격 — 합법 우회 차단] 혼합 계약("bare면 prefix")은 조건문이라
    # 대사를 **전부 서술에 녹이면** bare 줄이 안 생겨 우회됐다(레티어스 "안 지켜진다" 실관측).
    # 계약을 기본형-긍정(발화=자기 줄+이름:이 기본, 녹임=의도적 예외)으로 승격했으므로,
    # 대사가 여럿(3+)인데 이름: 줄이 0이면 기본형 미준수. 낱개 woven은 정당한 선택 — 임계로 보호.
    _named_lines = 0
    _total_quotes = 0
    for line in lines:
        s = line.strip()
        _inner = _unwrap_inline_tag(s)
        if _inner is not None:
            s = _inner
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
        parts.append(f"[IMPERSONATION] PC 대사 창작 {len(impersonations)}건: {'; '.join(imp_examples)}. PC의 대사는 플레이어의 것이다: 입력에 없는 말을 새로 짓지 않는다(다듬는 것은 정상).")
    return " ".join(parts)


def _prose_for_display(text: str, channel_id: str = "") -> str:
    """렌더 산문의 **표시용 사본** — 이 채널에 실제 발급된 몹 표식만 벗긴다. 저장본(`response`)은 그대로.

    [2026-09-17→20] 설계상 산문엔 표식이 없다(identity_hub). 그래도 명부에 표식이 보이니 모델이
    베낄 수 있어 **안전망**으로 남긴다. 범위를 채널 명부의 표식으로 묶은 이유: 안전망이
    `방 #3B호실` 같은 산문을 지우면 그게 새 병이다. channel_id 없으면 종전(2자 전부).
    호출자 = 렌더 산문 전송 3곳(execute/batch/observation)뿐. 명령어 출력·임베드·💠는 무접촉.
    """
    from response_processor import clean_mob_tags
    _tags = None
    if channel_id:
        try:
            _tags = npc_manager.issued_tags(channel_id)
        except Exception:
            _tags = None
    return clean_mob_tags(text, tags=_tags)


_RFP_STR_KEYS = ("gaze", "lighting", "palette", "rhythm", "temporal_density", "withholding_scheme")


def _normalize_render_fingerprint(rfp: Dict[str, Any]) -> Dict[str, Any]:
    """render_fingerprint 저장 관문 — 문자열 6키는 늘 str, unresolved는 늘 list[str].

    [2026-09-17] 병: gaze 계약이 "null when the turn held no NPC in focus"(cognition)라 JSON null이
    **정상 도착**한다. 종전 `rfp.get(k, "")`의 기본값은 키 부재에만 걸려 None이 그대로 저장됐고,
    바로 뒤 debug 로그 인자 `gaze[:50]`가 TypeError → 배경 추출 꼬리(R4 관찰→위치 쓰기·R6 스케줄
    틱)가 NPC 없는 턴마다 통째로 스킵됐다. 저장값에 None을 남기지 않는 게 처방 — 소비자 7곳은
    ""를 "초점 없음"으로 이미 읽는다.
    list가 오면(모델이 이름 배열로 내는 경우) 쉼표 나열로 접는다 — gaze 소비자가 전부 쉼표 split이라
    str(list)는 "['리나']" 같은 이름을 만들어 매칭 0이 된다.
    """
    def _s(v: Any) -> str:
        if v is None:
            return ""
        if isinstance(v, (list, tuple)):
            return ", ".join(str(x).strip() for x in v if x is not None and str(x).strip())
        return str(v)

    rfp = rfp if isinstance(rfp, dict) else {}
    out: Dict[str, Any] = {k: _s(rfp.get(k)) for k in _RFP_STR_KEYS}
    _u = rfp.get("unresolved")
    if isinstance(_u, (list, tuple)):
        out["unresolved"] = [str(x).strip() for x in _u if x is not None and str(x).strip()]
    elif isinstance(_u, str) and _u.strip():
        out["unresolved"] = [_u.strip()]
    else:
        out["unresolved"] = []
    return out


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
            # [2026-09-14 W3b] 위키 절 벡터 — 같은 턴·같은 쿼리(md5 히트). WIKI_VECTORS OFF면 즉시 return.
            await fermentation.refresh_wiki_vector_cache(
                self.client, ctx.channel_id, ctx.action_text,
                (ctx.domain_data or {}).get("history", []) if isinstance(ctx.domain_data, dict) else [],
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
            # [2026-09-02 R1 계층 생성] 스펙 §2.5 ⓑ / §4.
            # 병: 여기 자동 생성이 `parent_id` 미지정 = **전부 루트**라 플레이 중 계층이 안 자랐다.
            #     "저택"과 "저택 서재"가 부모-자식이 아니라 **형제(둘 다 루트)**가 되어
            #     §2.4의 1단(근접 = 들어올 수 있는 사람)이 영원히 텅 빈다.
            # 처방: (a) Flash `location_path`(루트부터, 깊이 캡 4)를 앞에서부터 순회하며
            #     없는 노드를 parent=직전 노드로 만든다. 이미 있는데 **루트**인 노드는
            #     path상 부모가 명확하면 붙인다(평평 노드 승격).
            #     (b) path가 없으면 이름 접두 폴백(world_tree.infer_parent_by_prefix).
            #     그 외엔 현행 동작 유지(루트 area 1개).
            # 근거: 새 LLM 콜 0 — 기존 Theoria 스키마에 선택 필드를 얹은 것뿐(스펙 §6 "전 단계 콜 0").
            #     선택 필드라 부재가 상례 → `or []` / `or ""`로 명시 null도 누락과 같게 받는다.
            try:
                import world_tree
                _lp_raw = dai.get("location_path") or []
                _lpath = ([str(_p).strip() for _p in _lp_raw
                           if isinstance(_p, str) and str(_p).strip()]
                          if isinstance(_lp_raw, list) else [])
                # 계약 방어 — "마지막 원소 = current_location"(스펙 ⓑ). Flash가 어기면
                #   현재 위치 노드가 아예 안 생기고, 바로 아래 presence 쓰기
                #   (set_npc_location)가 통째로 "location_not_found"로 유실된다. 꼬리를 덧댄다.
                if _lpath and _lpath[-1] != str(current_location).strip():
                    _lpath.append(str(current_location).strip())
                # 깊이 캡 4(스펙 ⓑ). 초과분은 **앞쪽(가장 거친 층)**을 버린다 —
                #   "마지막 원소 = current_location" 계약이 하류 전부의 전제라 꼬리를 지킨다.
                if len(_lpath) > 4:
                    _lpath = _lpath[-4:]
                _ltype = str(dai.get("location_type") or "").strip().lower()
                if _ltype not in ("region", "area", "room"):
                    _ltype = ""
                _lprops = {"risk": location_risk or "Low", "tags": ["auto_detected"]}
                if _lpath:
                    _prev_name = ""
                    for _i, _pname in enumerate(_lpath):
                        _is_last = (_i == len(_lpath) - 1)
                        # [2026-09-02 검수 수리] 경로 원소는 **정본 이름**이라 정확·별칭만 본다
                        #   (allow_token=False). 토큰 단일후보를 허용하면 평평한 자식("저택 서재")이
                        #   부모 이름("저택")을 삼켜 부모가 영영 안 생겼다(스모크 R1-6 래칫).
                        _nid = world_tree.resolve_node_id(channel_id, _pname, allow_token=False)
                        if not _nid:
                            _ar = world_tree.add_node(
                                channel_id, _pname,
                                node_type=((_ltype or "area") if _is_last else "area"),
                                parent_id=_prev_name,
                                properties=(_lprops if _is_last else {"tags": ["auto_detected"]}),
                            )
                            if _ar != "created":
                                # capped/invalid_parent가 조용히 지나가면 계층이 부분만 생긴 채
                                # 아무도 모른다 — 관측 한 줄(수리 아님).
                                logger.info("[presence-check] path node '%s' not created: %s (path=%s)",
                                            _pname, _ar, _lpath)
                            _nid = world_tree.resolve_node_id(channel_id, _pname, allow_token=False)
                        elif _prev_name:
                            _nd = world_tree.get_all_nodes(channel_id).get(_nid) or {}
                            if not _nd.get("parent_id"):
                                _ur = world_tree.update_node(
                                    channel_id, _nd.get("name", _pname), parent_id=_prev_name,
                                )
                                if _ur not in ("updated", "ok", True):
                                    logger.info("[presence-check] reparent '%s' under '%s' refused: %s",
                                                _nd.get("name", _pname), _prev_name, _ur)
                        _prev_name = (
                            (world_tree.get_all_nodes(channel_id).get(_nid) or {}).get("name", _pname)
                            if _nid else _pname
                        )
                else:
                    _exist = world_tree.get_node(channel_id, current_location)
                    _pname_fb = world_tree.infer_parent_by_prefix(channel_id, current_location)
                    if not _exist:
                        world_tree.add_node(
                            channel_id, current_location,
                            node_type=(_ltype or "area"), parent_id=_pname_fb,
                            properties=_lprops,
                        )
                    elif _pname_fb and not _exist.get("parent_id"):
                        world_tree.update_node(
                            channel_id, _exist.get("name", current_location), parent_id=_pname_fb,
                        )
            except Exception:
                pass
            # [2026-09-02 R4] ★여기 있던 presence 쓰기를 **제거**했다. 스펙 §1.3 / §6 R4.
            # 있던 것: `get_onstage_npc_names(1)`로 뽑은 **onstage 전원을 current_location에
            #   재배치**(2026-07-18 고아 승격 → 07-28 전체 명부 수리).
            # 병(R4 이후): `get_onstage_npc_names`가 이제 **위치에서 읽는다.** 그 결과를 다시
            #   위치에 쓰면 순환이다 — 출석이 만든 위치로 출석을 확인하는 꼴(§1.3의 그 그림이
            #   방향만 뒤집힌 채 되살아난다). 게다가 PC가 이동할 때마다 직전 노드 인원이 통째로
            #   따라 붙어 §2.4의 1·2단이 영원히 비고, 이동이 곧 퇴장이라는 latch(§2.1)도 깨진다.
            # 대신: **관찰 신호로만** 쓴다 — gaze(1순위) + psyche의 unplaced 입장.
            #   자리는 아래 `_execute_background_extraction`의 render_fingerprint 저장 직후다
            #   (gaze가 거기서 생기므로 그 앞에서는 쓸 재료가 없다). 검색어: "[2026-09-02 R4] 관찰".
        if location_risk:
            domain_manager.set_current_risk(channel_id, location_risk)

        # [2026-09-15 관계 통합 1차] Theoria relation 층 → NPC→행동PC 엣지(쓰기 경로 1/3).
        #   구: NPCAttitudes(gated attitude) + trajectory→depth + 시트 initial_depth 세 갈래 → 삭제.
        #   질문은 psyche_states[NPC].relation{bond,tension,descriptor} 하나, 이동폭 캡은 upsert_edge(origin=theoria).
        #   PC 혼입 가드는 엣지 쓰기 앞단(domain_manager.upsert_relation_edge)으로 이사 — 여기선 스텁 생성만 거른다.
        #   다인: 행동 PC = ctx.user_id의 mask. 마스크 없으면 쓰지 않는다(설계 §7-12).
        _psy_rel = dai.get("psyche_states")
        if isinstance(_psy_rel, dict) and _psy_rel:
            _pc_masks_att = domain_manager.get_pc_masks(channel_id)
            # [2026-08-11 사망 파이프라인] 자동 재등록 게이트 ① — 죽은 인물 이름이 분석에 다시 떠도
            #   관계가 시체 위에 적립되지 않게 입구에서 거른다(down은 거르지 않음).
            _dead_att = {n for n in _psy_rel
                         if isinstance(n, str) and npc_manager.get_npc_status(
                             npc_manager.get_npc(channel_id, n) or {}) == "dead"}
            if _dead_att:
                logger.info(f"[NPC Status] dead 관계 갱신 차단(환각 등장 신호): {', '.join(sorted(_dead_att))}")
            _skip_rel = set(_pc_masks_att) | _dead_att
            # [2026-09-24 감사 §5-2 #8 — 레티어스 판정] 명부에 없는 이름은 여기서 **등록도 관계 쓰기도 안 한다**.
            #   구: 즉석 스텁 등록(`source: session`) — 렌더 전이라 식별 허브(decide_entity: 고유명/역할명·refers_to·
            #   몹 태그)보다 먼저 키를 선점했다 → "경비병"이 표식 없는 정본 키로 박혀 여러 경비병이 한 키로 합쳐지거나
            #   `경비병`/`경비병 #2A` 두 키로 갈렸고, 엣지는 날것 라벨에 붙어 고아가 됐다.
            #   이제 등록은 렌더 뒤 로스터 패스(허브) 한 곳. 잃는 것 = 새 인물 첫 턴 관계값 1회
            #   (시트 초기값 seed 는 첫 엣지 때 그대로 심긴다). 이름 변형은 get_npc(_find_npc_key)가 흡수하므로 무관.
            _unreg_rel = {n for n, d in _psy_rel.items()
                          if isinstance(n, str) and n not in _skip_rel
                          and isinstance(d, dict) and isinstance(d.get("relation"), dict)
                          and not npc_manager.get_npc(channel_id, n)}
            if _unreg_rel:
                logger.info(f"[Relation] 미등록 이름 관계 보류(등록은 로스터 패스): {', '.join(sorted(_unreg_rel))}")
            _skip_rel |= _unreg_rel
            _acting_mask = (domain_manager.get_participant_data(channel_id, ctx.user_id) or {}).get("mask")
            _ws_rel = domain_manager.get_world_state(channel_id) or {}
            _n_rel = domain_manager.write_theoria_relations(
                channel_id, _psy_rel, _acting_mask,
                turn=int(_ws_rel.get("turn_index", 0) or 0), skip=_skip_rel)
            if _n_rel:
                logger.debug(f"[Relation] theoria → {_acting_mask}: {_n_rel} edges")
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
                    # [2026-09-14 S5a] 출처 원장 조인 키 — 이번 턴 turn_index + 유저 메시지 id.
                    # (Model 행엔 message_id가 없어 유저 메시지 id가 유일한 앵커.)
                    _src_fs = None
                    if getattr(config, "V10_FACT_SOURCES", False):
                        try:
                            _src_fs = {
                                "turn": int((domain_manager.get_world_state(channel_id) or {}).get("turn_index", 0) or 0),
                                "message_id": getattr(message, "id", None),
                            }
                        except Exception:
                            _src_fs = None
                    domain_manager.update_npc_knowledge(channel_id, npc_name, k_data, src=_src_fs)
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
        ctx.bus = updated_context.shared_bus  # [2026-09-24 감사] 하류 ctx.bus 소비자 배달(전엔 대입 0)

        # [2026-09-06 P3] 판정 결과를 ctx 에 실어 4.7 로 넘긴다 — `bus.judgment` 는 이 함수
        #   **로컬**이라 4.7 에선 안 보인다(§0 재확인 1). 결과 문자열 하나면 충분하다:
        #   expr 의 `check=judgment` 는 성공/실패 두 갈래만 갈라 쓴다.
        try:
            _bus_j = getattr(updated_context.shared_bus, "judgment", None) or {}
            ctx.judgment_result = str(_bus_j.get("result") or "") if _bus_j.get("active") else ""
        except Exception:
            ctx.judgment_result = ""

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
        if isinstance(fb_eval, dict) and fb_eval.get("detected") and fb_eval.get("plausibility") != "impossible":
            updated_context.shared_bus.dai["flashback_confirmed"] = True
            updated_context.shared_bus.dai["flashback_declaration"] = fb_eval.get("declaration", "")

        # [2026-09-06 P8b] **다운타임 처리 폐지.** 게이트였던 Theoria 필드가 스키마에서 사라졌고
        #   (부재를 감지하지 않는다), 그 아래 활동별 코드 효과(치료 +15 / 부업 +20·과용 -15 /
        #   훈련 -5 / 사교 +15·유대 / 프로젝트 -3)는 전부 "장면을 분류해서 숫자를 정하는 코드"였다.
        #   대체 = 기력·평형의 rule 문장 + 전담 추출 콜의 관측 델타. 신설 감지 0.

        # [Item Usage] 아이템 소비/획득 처리
        item_eval = dai.get("item_usage")
        if isinstance(item_eval, dict) and item_eval:   # [2026-09-24 감사] null 계약 필드 — list/str 이면 턴 사망(동기 경로)
            item_msg = self._process_item_usage(
                channel_id, updated_context.narrative_anchors.get("acting_user_id", ""), item_eval
            )
            if item_msg:
                system_log = (system_log or "") + f"\n{item_msg}"

        # ⛔[2026-09-24 감사] N2 인벤토리 검증 호출 제거 — "응답이 암시하는 현재 소지품" 생산자가 없다
        #   (item_usage 스키마는 items_gained/items_consumed 뿐, "items" 키 0) → 늘 인벤토리를 자기 자신과 비교해
        #   경고 영구 0, 대신 item_usage 가 비정형이면 여기서 턴이 죽을 수 있었다. 함수(cognition.validate_inventory)는 남김.

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

        def _name_qty(entry):
            """item_usage 스키마엔 qty가 없다(문자열 목록). dict로 오면 qty를 받아준다."""
            if isinstance(entry, str):
                return entry.strip(), 1
            if isinstance(entry, dict):
                nm = str(entry.get("name") or entry.get("item") or "").strip()
                try:
                    q = max(1, int(entry.get("qty", 1)))
                except Exception:
                    q = 1
                return nm, q
            return "", 1

        # 소비 처리: [소지품] 섹션에서 차감 → sync가 인벤토리 자동 반영
        for item in consumed:
            nm, q = _name_qty(item)
            if not nm:
                continue
            result = game_character.remove_item_from_sojipin(channel_id, nm, user_id, qty=q)
            if "못 찾음" not in result:
                log_parts.append(f"📦 소비: {nm}" + (f" ×{q}" if q > 1 else ""))

        # 획득 처리: [소지품] 섹션에 추가 → sync가 인벤토리 자동 반영
        for item in gained:
            nm, q = _name_qty(item)
            if not nm:
                continue
            game_character.add_item_to_sojipin(channel_id, nm, user_id, qty=q)
            log_parts.append(f"📥 획득: {nm}" + (f" ×{q}" if q > 1 else ""))

        if not log_parts:
            return None

        msg = " | ".join(log_parts)
        if reason:
            msg += f" ({reason})"
        return msg

    async def _apply_outputs(self, channel_id: str, ctx, outputs: Dict[str, Any],
                             message=None) -> None:
        """[2026-09-06 P8a] 전담 콜 산출 **한 자리** 적용.

        순서가 계약이다: 값 델타 → 전이 큐 → 연산 → 노트북 메모 → status → append 기록.
        값이 먼저 움직여야 같은 턴 큐/연산이 그 값 위에서 판정되고, 노트북·status 는
        서로를 안 본다. 단계마다 try 가 따로 서는 이유는 한 소비부의 실패가 나머지 넷을
        지우면 안 되기 때문이다(배치-전담 격리와 같은 규율, 한 겹 아래).
        """
        if not outputs:
            return
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
        # ⑤ status_effects
        try:
            self._apply_status_changes(
                channel_id, ctx.user_id,
                outputs.get("status_add"), outputs.get("status_remove")
            )
        except Exception as _e_st:
            logger.debug(f"[Status] 적용 skip: {_e_st}")
        # ⑥ [2026-09-09 P12] append 기록 — 행 적립. 마지막인 이유: 도장(시간·위치·인물)이
        #    이번 턴 최종 상태를 읽어야 하고, 이 단계가 죽어도 위 다섯은 이미 끝나 있다.
        try:
            if outputs.get("entries"):
                status_panel.apply_append_entries(
                    channel_id, outputs["entries"], user_id=ctx.user_id)
        except Exception as _e_ap2:
            logger.debug(f"[AppendLog] 적립 skip: {_e_ap2}")

    def _apply_status_changes(self, channel_id: str, user_id: str, status_add, status_remove) -> None:
        """[N-2 후속] 전담 추출 콜(extract_outputs)의 status_add/remove를 실제 status_effects에 적용.
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

    # =========================================================
    # STEP 4.75: ARRIVAL HANDOUT (2026-09-13 P14)
    # =========================================================
    async def _deliver_handout(self, ctx: ResponseContext, message, channel_id: str) -> bool:
        """도착물이 있으면 산문 **앞**에서 쓰고·보이고·적립한다. 없으면 콜 0.

        방아쇠 둘(선언 전이 `deliver` / 분석 신호 `arrival`)의 판정은 코드 한 곳
        (`world_board.pick_arrival_request`)에 있고, 여기는 그 결과를 **표시**로 옮길 뿐이다.
          (a) 렌더 프롬프트 `<핸드아웃>` 블록 — 산문이 편지를 읽은 채로 시작한다.
          (b) 산문 **앞** 별도 메시지 — 📰 공개는 임베드 본문, 💌 개인은 봉투 한 줄 +
              💌 버튼(클릭하면 종전 `_respond` 가 ephemeral 로 본문을 연다).
              디스코드 임베드는 텍스트 **밑**에만 그려지므로 산문 뒤에 붙이면 종이가 늦는다.
          (c) `turn_mail` 행 — 그 메시지 id 로 적립되므로 💠 "쌓인 것" 도착물 목록에 자동으로 선다.
        실패는 전부 무해하다: 핸드아웃 없이 산문이 나가고, 다음 턴 재시도는 없다
        (전이는 이미 발화했다 — 재시도는 같은 편지를 두 번 보내는 길이다).
        """
        import world_board as _wb
        # [2026-09-24 감사] 채널 검사를 **집기 전**으로 — 전엔 선언 행을 먼저 소비한 뒤 스레드 채널이면
        #   return 해서 스레드 세션의 선언 도착물이 알림째 증발했다(안 집으면 9.52 flush 가 한 줄 알림으로 보낸다).
        if not isinstance(message.channel, discord.TextChannel):
            logger.debug("[Handout] 텍스트 채널이 아니라 스킵")
            return False
        req = _wb.pick_arrival_request(channel_id, ctx.dai)
        if not req:
            return False
        result = await _wb.trigger_board_update(
            message.channel, self.client, config.role_model("light"), channel_id,
            trigger="declared", dai=dict(ctx.dai) if ctx.dai else {}, deliver=req,
        )
        if not result:
            logger.info("[Handout] 도착물 콜 무산 — 핸드아웃 없이 진행 (source=%s)",
                        req.get("source"))
            return False

        import custom_vars as _cv
        import turn_mail as _tm
        payload = result.get("payload") or {}
        _cv.queue_handout(channel_id, _wb.handout_text(result))

        envelope = _tm.envelope_line(payload)
        turn = 0
        try:
            turn = int((domain_manager.get_world_state(channel_id) or {}).get("turn_index", 0) or 0)
        except (TypeError, ValueError):
            turn = 0
        try:
            if envelope:
                sent = await message.channel.send(envelope)
            else:
                sent = await message.channel.send(
                    embed=_tm.handout_embed(payload, turn))
            ctx.handout_message_id = getattr(sent, "id", None)   # [2026-09-24 감사] !다시 삭제 목록용
            await _tm.deliver(sent, channel_id, str(result.get("kind") or _tm.KIND_MAIL),
                              payload, turn)
        except Exception as e:
            logger.warning(f"[Handout] 표시 실패(핸드아웃 블록은 유지): {e}")
            # [2026-09-24 감사] 표시가 무산되면 선언 행을 한 줄 알림으로 되돌린다(이미 되돌렸으면 no-op).
            try:
                _wb.restore_arrival_request(channel_id, req)
            except Exception:
                pass
            return False
        logger.info("[Handout] %s ch=%s source=%s turn=%s",
                    "💌 봉투" if envelope else "📰 임베드",
                    result.get("channel_kind"), req.get("source"), turn)
        return True

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
            self.client, config.role_model("renderer"), 
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
        if _cv_feed or _cue_feed or _op_feed or _ap_feed or _want_notebook:
            async def outputs_extraction():
                try:
                    # 라이브 노트북 재읽기(stale ctx.notebook_txt 대신) — 이번 턴 [소지품]
                    # 변경을 반영하기 위함. physical 힌트가 꺼져 있으면 입력에 싣지 않는다.
                    live_notebook = ""
                    status = []
                    if _want_notebook:
                        live_notebook = game_character.get_notebook_text(channel_id, ctx.user_id)
                        status = game_character.get_status_effect_names(
                            ctx.player_data.get("status_effects", []) if ctx.player_data else []
                        )
                    outputs = await cognition.extract_outputs(
                        self.client, ctx.action_text, response,
                        custom_vars_feed=_cv_feed,
                        cues_feed=_cue_feed,
                        operations_feed=_op_feed,
                        notebook=live_notebook,
                        current_status=status,
                        include_notebook=_want_notebook,
                        append_sections=_ap_feed,
                        handout=_handout_txt,
                    )
                    await self._apply_outputs(channel_id, ctx, outputs, message)
                except Exception as e:
                    logger.error(f"Outputs extraction error: {e}")

            await enqueue_background_task(
                channel_id,
                "OutputsExtraction",
                outputs_extraction,
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
                # [2026-09-05 발효 계약] 통째 저장(save_callback) 폐지.
                # 발효는 사본 위에서 돌고, 결과 diff만 live 도메인에 얹는다.
                snap = fermentation.build_ferment_input(domain_manager.get_domain(channel_id), channel_id=channel_id)
                before = copy.deepcopy(snap)
                await fermentation.auto_ferment(
                    self.client, self.model_id,
                    snap,
                    channel_id=channel_id
                )
                result = fermentation.extract_ferment_result(before, snap)
                if result.changed:
                    domain_manager.apply_ferment_result(channel_id, result)
            except Exception as e:
                logger.error(f"[Orchestrator] Fermentation task error: {e}")

        # Schedule fermentation
        await enqueue_background_task(
            channel_id,
            "BackgroundFermentation",
            background_fermentation_task,
            priority=TaskPriority.LOW
        )

    # ⚰ [2026-09-07 P9] `_with_status_header` 삭제 — 상태창이 산문 **머리 텍스트**에서
    #   **꼬리 임베드**로 이사했다. 이 함수가 하던 일(표시용 접합)은 이제 send 인자
    #   `embeds=status_panel.build_turn_embeds(ch)` 하나로 끝난다. 08-16 계약은 그대로다 —
    #   `response` 변수는 여전히 손대지 않는다(히스토리·검수·리더·배경 추출이 원본을 읽는다).
    #   부활 금지: 머리 접합이 돌아오면 같은 값이 두 화면(머리 텍스트·임베드)에 다시 살고,
    #   임베드 예산(_fit_sections) 밖의 텍스트가 생긴다.

    # ⚰ [2026-09-13 P9b] `_panel_view` 삭제 — 합성 규칙을 이 클래스가 아니라
    #   `turn_mail.build_view` 한 곳이 갖는다. [2026-09-13 P9c] 💠 가 돌아오며 전송 시점
    #   view 도 돌아왔지만, 되살린 건 **인자**지 이 함수가 아니다: 메인 send 가
    #   `view=turn_mail.build_view(channel_id)` 를 직접 부른다. 부활 금지 — 합성이 두 곳이
    #   되면 사후 부착(attach_button)의 edit 이 전송 시점 View 와 달라져 💠 가 사라진다.

    # =========================================================
    # [2026-09-13 P15] 매턴 임베드 재그림 — 그 턴 배경 쓰기가 **끝난 뒤** 1회
    # =========================================================
    #  전송 시점 임베드는 "그 산문을 쓴 상태"(추출 전)다 — 앵커론 정합이지만
    #  핸드아웃(플레이어가 읽는 것)으로는 한 턴 늦다: 이번 턴에 움직인 값이 이번 턴
    #  화면에 없다. 그래서 배경 쓰기(_apply_outputs·_advance_scene_time·패널 콜)가
    #  다 끝난 자리에서 같은 메시지를 한 번 고쳐 그린다.
    #  자리 근거: `background_task_queue` 의 채널 큐는 **FIFO** 다(TaskPriority 는
    #  로그용 꼬리표일 뿐 순서를 바꾸지 않는다 — `_process_queue` 는 우선순위를 읽지
    #  않는다). 그래서 "맨 뒤에 적재" = "그 턴 배경 쓰기가 다 끝난 뒤". 드레인을
    #  기다리는 대신 큐에 서면 턴 임계경로에 1ms도 얹지 않는다.
    #  콜 0 · 새 버튼 0 · 문구 0. discord edit 은 **턴당 최대 1회**(실패는 재시도 없이 삼킨다).
    async def _schedule_panel_refresh(self, channel_id: str, sent_msgs, before_data) -> None:
        """산문 꼬리 임베드를 배경 쓰기 뒤 값으로 한 번 고쳐 그린다(변화 없으면 0회)."""
        if not sent_msgs:
            return
        target = sent_msgs[-1]

        async def _run_panel_refresh():
            # 비교는 `build_turn_embed_data`(discord 비의존 순수 데이터) 로 한다 —
            # Embed 객체를 두 번 지어 비교하면 같은 값에도 edit 이 나갈 수 있다.
            try:
                after = status_panel.build_turn_embed_data(channel_id)
            except Exception as e:
                logger.debug(f"[Panel] refresh build skipped: {e}")
                return
            if after == before_data:
                return                      # 이번 턴 배경이 값을 안 건드렸다 = edit 0
            try:
                await target.edit(embeds=status_panel.build_turn_embeds(channel_id, data=after))
            except Exception as e:
                # 삼킨다. 화면이 한 턴 늦는 건 무해하지만 턴이 죽는 건 무해하지 않다.
                logger.info("[Panel] edit fail: %s", e)

        try:
            await enqueue_background_task(
                channel_id, "PanelRefresh", _run_panel_refresh,
                priority=TaskPriority.LOW,
            )
        except Exception as e:
            logger.debug(f"[Panel] refresh enqueue skip: {e}")

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

    async def _npc_roster_pass(self, channel_id, est_data, _pc_masks, ctx, turn_idx, prose: str = "") -> list:
        """[2026-09-16 시트 2차 §9] NPC 로스터 관리 — 성장이 아니다(옛 NPC 시트 함수의 앞 절반).

        named_as 개명 / dead 버림 / incapacitated → down(루프 뒤 일괄) / 미등록 즉석 세션 NPC 생성
        (원문 없음 — 관찰은 grow_sheet가 페이지 Observed로) / new_individual 몹 태그 / lore_seen 적립.
        Returns: [(npc_name, descriptor)] — 이번 턴 `grow_sheet`로 넘길 관찰.
        """
        # [2026-07-22 카드3] 이번 턴 주입된 로어 청크 라벨 — NPC 등장 턴과의 동시출현을
        # 적립해 증류 접지 2단으로 쓴다(이름이 로어에 없는 세션 NPC의 접지 경로).
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
        _grow = []
        # [2026-09-18 식별 허브 S5] 판정 재료 — 명부·무대·닻 노드. 루프 밖 1회.
        _npcs_now = npc_manager.get_npcs(channel_id) or {}
        try:
            _onstage_now = [n for n in (npc_manager.get_onstage_npc_names(channel_id) or [])
                            if n not in _pc_masks]
        except Exception:
            _onstage_now = []
        try:
            import world_tree as _wt_id
            _anchor = _wt_id.anchor_node_id(channel_id)
        except Exception:
            _wt_id, _anchor = None, ""
        _pc_input = str(getattr(ctx, "action_text", "") or "")
        _receipt = {"onstage": list(_onstage_now), "labels": [], "registered": [],
                    "alias_promoted": [], "tag_issued": [], "scene_index": 0}
        for _npc_name, _ch in (_changes.items() if isinstance(_changes, dict) else []):
            if not isinstance(_ch, dict):
                continue
            # --- 판정: 이 호칭이 누구인가 / 인물이 될 자격이 있는가 ---
            # 앞단: 이 장소에 같은 호칭의 표지가 있고 그때 가리킨 사람이 지금 무대에 있으면 **판정 없이** 그 사람.
            _dec = None
            if _wt_id and _anchor and not str(_ch.get("named_as") or "").strip():
                try:
                    _ext = _wt_id.find_extra(channel_id, _anchor, _npc_name)
                except Exception:
                    _ext = None
                _ext_key = str((_ext or {}).get("key") or "").strip()
                if _ext_key and _ext_key in _onstage_now and _ext_key not in _pc_masks:
                    _dec = {"action": "refers", "key": _ext_key, "need_tag": False, "alias": None,
                            "scene_label": _npc_name, "why": "extras"}
            if _dec is None:
                _dec = npc_manager.decide_entity(
                    _npcs_now, _npc_name, _ch, onstage=_onstage_now, pc_masks=_pc_masks,
                    pc_input=_pc_input, prose=prose)
            _receipt["labels"].append({"raw": _npc_name, "kind": str(_ch.get("name_kind") or ""),
                                       "alias_kind": str(_ch.get("alias_kind") or ""),
                                       "resolved": _dec["key"] if _dec["action"] != "skip" else None,
                                       "by": _dec["why"]})
            # 장면 표지 — 닻 노드에 매단다(승격되면 아래에서 뺀다)
            if _wt_id and _anchor and _dec.get("scene_label"):
                try:
                    if _wt_id.upsert_extra(channel_id, _anchor, _dec["scene_label"],
                                           line=str(_ch.get("descriptor") or "")[:60], turn=turn_idx,
                                           key=_dec["key"] if _dec["action"] == "refers" else ""):
                        _receipt["scene_index"] += 1
                except Exception as _e_ex:
                    logger.debug(f"[Identity] 장소 표지 쓰기 건너뜀: {_e_ex}")
            if _dec["action"] == "skip":
                logger.info(f"[Identity] 문턱 미달 — 등록 안 함: {_npc_name} ({_dec['why']})")
                continue
            if _dec["action"] == "refers":
                logger.info(f"[Identity] 묘사 → 무대 인물: {_npc_name} → {_dec['key']}")
                if _dec.get("alias"):
                    self._promote_alias(channel_id, _dec["key"], _dec["alias"][0], _npcs_now, _receipt)
                # ⚠ 장소 표지는 **지우지 않는다** — 이 호칭이 그 사람을 가리켰다는 사실이 다음 턴
                #   같은 장소에서 판정 없이 붙는 재료다(표지를 지우면 매 턴 다시 판정).
                #   표지를 빼는 건 그 라벨이 **인물로 승격**될 때뿐(아래 need_tag 분기).
                _npc_name = _dec["key"]
            elif _dec["action"] == "register" and _dec["key"] != _npc_name and not _dec.get("need_tag"):
                # 라벨이 이 턴에 댄 이름으로 바로 등록(named_new) — 아래 개명 가드는 같은 이름이라 지나간다.
                logger.info(f"[Identity] 라벨 → 이름으로 등록: {_npc_name} → {_dec['key']}")
                # [2026-09-24 감사 §5-2 #10] 괄호식 호칭(`레나(Rena)`)의 다른 표기 → 별칭(키는 그대로).
                if _dec.get("alias"):
                    self._promote_alias(channel_id, _dec["key"], _dec["alias"][0], _npcs_now, _receipt)
                if _wt_id and _anchor:
                    try:
                        _wt_id.remove_extra(channel_id, _anchor, _npc_name)
                    except Exception:
                        pass
                _npc_name = _dec["key"]
            elif _dec.get("need_tag"):
                _tag = npc_manager.issue_mob_tag(channel_id, _npc_name)
                if not _tag:
                    logger.error(f"[Identity] 표식 소진 — 등록 포기: {_npc_name}")
                    continue
                _receipt["tag_issued"].append([_npc_name, _tag])
                if _wt_id and _anchor:
                    try:
                        _wt_id.remove_extra(channel_id, _anchor, _npc_name)
                    except Exception:
                        pass
                _npc_name = f"{_npc_name} #{_tag}"
                logger.info(f"[Identity] 역할명 신규 → 표식 발급: {_npc_name}")
            # [2026-07-18 이름 획득 배선] 가드: PC 마스크·자기 자신·로어 NPC·기존 타 엔티티 충돌.
            _named = _ch.get("named_as")
            if _named and str(_named).strip():
                _new_nm = str(_named).strip()
                try:
                    _src_np = npc_manager.get_npc(channel_id, _npc_name)
                    if (_new_nm != _npc_name and _new_nm not in _pc_masks
                            and _src_np
                            and npc_manager.npc_source(_src_np) != "lore"):
                        if npc_manager.get_npc(channel_id, _new_nm):
                            logger.info(f"[NPC Naming] skip: '{_new_nm}' 기존 엔티티 (!npc 병합 후보)")
                        else:
                            npc_manager.handle_identity_reveal(
                                channel_id, _npc_name, _new_nm,
                                reason="explicit naming in scene")
                            logger.info(f"[NPC Naming] {_npc_name} → {_new_nm}")
                            _npc_name = _new_nm  # 이후 관찰은 새 이름으로
                except Exception as _e_nm:
                    logger.debug(f"[NPC Naming] skip: {_e_nm}")
            # [2026-08-11 사망 파이프라인] dead면 이 엔트리 전체를 버린다(스텁 생성·관찰·몹 태그 전부).
            if npc_manager.get_npc_status(
                    npc_manager.get_npc(channel_id, _npc_name) or {}) == "dead":
                logger.info(f"[NPC Status] entity_state에 dead '{_npc_name}' — "
                            "재등록·관찰 누적 차단 (환각 등장 신호)")
                continue
            # [2026-08-11 사망 파이프라인] 무력화 관측 → down(가역). 쓰기는 루프 뒤로 미룬다
            #   (첫 등장에서 쓰러진 인물은 레코드가 아직 없어 관문이 버린다).
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
                # 원문(description) 없이 태어난다 = 세션 NPC(파생 source). 첫 관찰은 grow_sheet가 Observed로.
                npc_manager.update_npc(channel_id, _npc_name, {
                    "status": "active",
                    # [카드3] 탄생 턴의 로어 청크 = 이 인물이 태어난 세계 좌표
                    "lore_seen": {_l: 1 for _l in _turn_labels},
                })
                _receipt["registered"].append(_npc_name)
                logger.info(f"[NPC Sheet] 즉석 NPC 생성: {_npc_name}"
                            + (f" (lore: {','.join(_turn_labels[:3])})" if _turn_labels else ""))
                _grow.append((_npc_name, _desc))
                continue
            # [2026-07-28] 등록된(원문 있는) NPC는 고유 인물 — 동명 별개체 태그 대상 아님.
            if (_new_indiv and npc_manager.npc_source(_existing) not in npc_manager.FROZEN_SOURCES
                    and not npc_manager.is_mob_tag(_npc_name)):
                # [2026-09-22 voice_seed §G 결정 4] 동명 별개체도 **굴린다**. 이 경로의 이름은
                #   기존 키로 해상돼 §A 게이트를 못 지나므로(굴림 없음), 태그 **전** base 이름으로
                #   여기서 버퍼에 "rolled" entry 를 앉힌다 → 이어지는 update_npc(`여관 주인 #2A`)의
                #   §G 관문이 후보 `mob_base(final_key)` 로 집어 간다. 콜라주는 없다(렌더 뒤라
                #   서사 콜이 지나갔다) — seam·aside 는 §J 증류가 첫 정리 때 채운다.
                try:
                    import voice_seed as _vs_ni
                    _vs_ni.roll_for(channel_id, _npc_name, int(turn_idx or 0))
                except Exception as _e_ni:
                    logger.debug(f"[Seed] new_individual 굴림 건너뜀: {_e_ni}")
                _tagged = npc_manager.register_ai_npc(
                    channel_id, _npc_name, context="auto mob-tag (new_individual)")
                if _tagged and _tagged != _npc_name:
                    logger.info(f"[NPC Sheet] 동명 별개체 자동 태그: {_npc_name} → {_tagged}")
                    _grow.append((_tagged, _desc))
                continue
            # [카드3] 동시출현 청크 라벨 적립(빈도) — 상위 8개만 유지
            if _turn_labels:
                _merged = dict(_existing)
                _seen = dict(_merged.get("lore_seen") or {})
                for _l in _turn_labels:
                    _seen[_l] = int(_seen.get(_l, 0) or 0) + 1
                _merged["lore_seen"] = dict(
                    sorted(_seen.items(), key=lambda kv: (-kv[1], kv[0]))[:8])
                npc_manager.update_npc(channel_id, _npc_name, _merged)
            _grow.append((_npc_name, _desc))
        # [2026-09-18 식별 허브 S6] 턴 영수증 — 화면이 조용한 만큼 뒤에서 보이는 창 하나.
        try:
            import sqlite_store as _sq_id
            _sq_id.merge_turn_snapshot_raw(channel_id, int(turn_idx or 0), {"identity": _receipt})
        except Exception as _e_rc:
            logger.debug(f"[Identity] 영수증 적립 건너뜀: {_e_rc}")
        for _dn, _dev in _pending_down:
            npc_manager.set_npc_status_gated(
                channel_id, _dn, "down", source="extraction",
                evidence=_dev, current_turn=turn_idx)
        return _grow

    @staticmethod
    def _promote_alias(channel_id, key, label, npcs_now, receipt=None) -> bool:
        """[2026-09-18 식별 허브 S5] 지속 표지를 그 인물 별칭으로. **충돌하면 올리지 않는다** —
        다른 인물의 이름·별칭과 겹치는 문자열을 올리면 그 뒤 모든 조회가 엉뚱한 사람에 닿는다
        (F1의 별칭판). 쓰기는 domain 쪽 — 게이트가 페이지 aliases 로 밀어준다."""
        _lb = " ".join(str(label or "").split())
        if not _lb:
            return False
        try:
            _owner = domain_manager._find_npc_key(npcs_now, _lb)
            if _owner and _owner != key:
                logger.info(f"[Identity] 별칭 승격 보류(충돌): '{_lb}' → 이미 {_owner}")
                return False
            _cur = npc_manager.get_npc(channel_id, key)
            if not isinstance(_cur, dict):
                return False
            _al = [a for a in (_cur.get("aliases") or []) if isinstance(a, str)]
            if any(" ".join(a.split()).lower() == _lb.lower() for a in _al):
                return False
            _new = dict(_cur)
            _new["aliases"] = _al + [_lb]
            npc_manager.update_npc(channel_id, key, _new)
            if isinstance(receipt, dict):
                receipt.setdefault("alias_promoted", []).append(f"{_lb}→{key}")
            logger.info(f"[Identity] 별칭 승격: '{_lb}' → {key}")
            return True
        except Exception as _e_al:
            logger.debug(f"[Identity] 별칭 승격 실패: {_e_al}")
            return False

    @staticmethod
    def _take_fragments(channel_id, uid, body, candidates):
        """조각 후보 → (Observed 에서 quote 문장을 뺀 본문, 채택 조각 목록). 쓰기 0.

        채택 = quote 가 본문에 있음(desc = 빼낸 문장 그대로)(`wiki_store.take_quote_from_body`, `_norm_quote` 대조).
        미일치 = 채택 0·계수(no_quote). 같은 이름이 origin=sheet 조각이면 무시·계수(sheet_locked) — 문장은 남는다."""
        import wiki_store
        stats = game_character.FRAGMENT_STATS
        mem = domain_manager.get_ai_memory(channel_id, uid) or {}
        sheet_names = {str(p.get("name")) for p in (mem.get("passives") or [])
                       if isinstance(p, dict) and p.get("origin", "sheet") != "play"}
        adopted = []
        for c in (candidates if isinstance(candidates, list) else []):
            frag = game_character.normalize_fragment(c, "play") if isinstance(c, dict) else None
            if not frag:
                stats["invalid"] += 1
                continue
            frag["origin"] = "play"
            if frag["name"] in sheet_names:
                stats["sheet_locked"] += 1
                continue
            body2, taken = wiki_store.take_quote_from_body(body, str(c.get("quote") or ""))
            if not taken:
                stats["no_quote"] += 1
                continue
            body = body2
            frag["desc"] = taken     # §10.2 "play 절에서 빼내 desc 로" — 이동이라 OOC 삭제 시 같은 문장이 돌아간다
            adopted.append(frag)
        return body, adopted

    @staticmethod
    def _replace_line3(body: str, line: str) -> str:
        """[2026-09-22 voice_seed §J] Core Traits 절의 **셋째 줄**만 교체/신설. 1·2행은 바이트 보존.

        시드 절의 모양은 `기전1 / 기전2 / seam`(§1.4)이고 앞 두 줄은 굴림 주소 그 자체 = canon 이다.
        그래서 증류는 텍스트 대조가 아니라 **줄 번호**로 들어온다(기전이 무엇이든 안 읽는다).
        줄이 둘뿐이면 셋째 줄을 새로 얹고, 넷째 줄 이하가 있으면 그대로 둔다(삭제 0)."""
        lines = str(body or "").splitlines()
        while len(lines) < 2:
            lines.append("")
        return "\n".join(lines[:2] + [str(line or "").strip()] + lines[3:])

    async def grow_sheet(self, channel_id, entity, observation=None, *, turn=None) -> bool:
        """[2026-09-16 시트 2차 §9] 성장 관문 하나 — PC·NPC 공통.

        entity = ("pc", uid) | ("npc", name). 관찰은 인물 페이지 play 절 `Observed`에
        `append_play_item`(중복 = `_norm_quote` 대조). 직전 정리 뒤 250자 이상 자랐으면 heavy 정리 1콜
        (lore 절 전문 + Observed + 로어 접지) → `write_play_section`(CAS, reason="grow")로 **Observed만** 덮는다.
        lore 절 무접촉, 동결 판정 없음, 저장 캡 없음(길이는 정리가 관리). 마커 = 페이지 built_len.
        Returns: 정리 콜을 돌려 반영했으면 True.
        """
        import wiki_store
        kind, key = (entity or (None, None))[:2]
        obs = str(observation or "").strip()
        npc = None
        if kind == "pc":
            pid = wiki_store.pc_page_id(channel_id, key)
            if not pid:
                return False   # §7-12: 가면(페이지) 없는 uid는 성장·관찰 대상이 아니다
            page = wiki_store.get_page(channel_id, pid) or {}
            name = page.get("name") or ""
            aliases = page.get("aliases") or []
            seen_labels = {}
        elif kind == "npc":
            npc = npc_manager.get_npc(channel_id, key)
            if not npc:
                return False
            # [2026-09-24 감사] get_npc 는 별칭·짧은 이름·`레나(Rena)` 변형까지 풀어 주는데 페이지 id 는 원문 라벨로
            #   만들어, 변형 표기로 불린 턴마다 **그림자 페이지**가 생기고 Observed·built_len·조각이 거기 쌓였다
            #   (두 페이지가 같은 별칭을 나눠 resolve_page 가 모호 처리 → compile·F1 노트에서 인물이 빠짐).
            #   정본 키로 정규화한다.
            try:
                _canon = domain_manager._find_npc_key(domain_manager.get_npcs(channel_id) or {}, key)
                if _canon:
                    key = _canon
            except Exception as _e_canon:
                logger.debug(f"[Grow] NPC 키 정규화 skip: {_e_canon}")
            name = key
            pid = wiki_store.page_id_for("character", name)
            if not wiki_store.get_page(channel_id, pid):
                pid = wiki_store.ensure_page(channel_id, "character", name,
                                             aliases=npc.get("aliases") if isinstance(npc.get("aliases"), list) else None,
                                             source=npc_manager.npc_source(npc), turn=turn)
            if not pid:
                return False
            aliases = npc.get("aliases") or []
            seen_labels = npc.get("lore_seen") or {}
        else:
            return False
        # [2026-09-22 voice_seed §J] 시드 시트인가 — 페이지 source 도장 하나가 판정이다.
        #   작가 시트(lore/manual)·도장 없는 페이지엔 증류 두 키가 아예 안 열린다(§10 무접촉).
        seeded = False
        if kind == "npc":
            try:
                seeded = (wiki_store.page_source(channel_id, pid) == wiki_store.SEED_SOURCE)
            except Exception:
                seeded = False
        if obs:
            _r = wiki_store.append_play_item(channel_id, pid, "Observed", obs,
                                             src_turn=turn, turn=turn, reason="observe")
            if not _r.get("ok"):
                logger.info(f"[Sheet Grow] 관찰 착지 거부: {name} ({_r.get('reason')})")
        body, h = wiki_store.get_play_body(channel_id, pid, "Observed")
        built = wiki_store.get_built_len(channel_id, pid)
        _thr = 250   # §7-14 공통 임계(NPC 값 계승)
        if not (len(body) >= _thr and (len(body) - built) >= _thr and getattr(self, "client", None)):
            return False
        try:
            lore_secs = wiki_store.get_lore_sections(channel_id, pid)
            lore_text = "\n\n".join(f"### {k}\n{v}" for k, v in lore_secs.items())
            grounding = await npc_manager.build_distill_grounding(
                channel_id, name, aliases=aliases, observations=body,
                seen_labels=seen_labels, client=self.client)
            if kind == "pc":
                _frs_now = (domain_manager.get_ai_memory(channel_id, key) or {}).get("passives") or []
                _fr_names = ", ".join(str(f.get("name")) for f in _frs_now if isinstance(f, dict) and f.get("name"))
                out = await cognition.condense_play_section(
                    self.client, name, lore_text, body, grounding, want_aspects=False,
                    want_fragments=True, fragments_text=_fr_names)
            else:
                out = await cognition.condense_play_section(
                    self.client, name, lore_text, body, grounding, want_aspects=True,
                    seeded=seeded)
            new_body = str((out or {}).get("observed") or "").strip()
            if not new_body:
                return False
            # [2026-09-16 3차 §10.2] 조각 발췌 — quote 가 정리본에 있을 때만 그 문장을 빼내 조각(origin=play)으로.
            _adopt = []
            if kind == "pc":
                new_body, _adopt = OrchestrationService._take_fragments(channel_id, key, new_body, (out or {}).get("fragments"))
                if not new_body.strip():
                    return False
            res = wiki_store.write_play_section(channel_id, pid, "Observed", new_body,
                                                src_turns=[turn] if turn is not None else [],
                                                turn=turn, reason="grow", expected_hash=h)
            if not res.get("ok"):
                logger.info(f"[Sheet Grow] 정리 반영 거부: {name} ({res.get('reason')})")
                return False
            wiki_store.set_built_len(channel_id, pid, len(new_body))
            if _adopt:
                _mem = domain_manager.get_ai_memory(channel_id, key) or {}
                _merged, _st = game_character.merge_fragments(_mem.get("passives", []), _adopt, "play")
                domain_manager.update_ai_memory(channel_id, key, {"passives": _merged})
                game_character.FRAGMENT_STATS["adopted"] += _st["added"]
                game_character.FRAGMENT_STATS["replaced"] += _st["replaced"]
                logger.info(f"[Fragment] {name}: +{_st['added']} ~{_st['replaced']}")
            if kind == "npc":
                _cur = npc_manager.get_npc(channel_id, name) or {}
                _m = dict(_cur)
                if out.get("high_concept"):
                    _m["high_concept"] = out["high_concept"]
                if out.get("trouble"):
                    _m["trouble"] = out["trouble"]
                _asp = out.get("aspects")
                if isinstance(_asp, list) and _asp:
                    _m["aspects"] = [str(a).strip() for a in _asp if a and str(a).strip()][:6]
                if _m != _cur:
                    npc_manager.update_npc(channel_id, name, _m)
                # [2026-09-22 voice_seed §J] 시드 시트 자기 수정 — 절 둘만, 한 칸씩.
                #   Core Traits 는 **셋째 줄만** 갈린다(1·2행 = 기전 두 줄 = canon, 바이트 보존).
                #   Aside 는 통째 교체. dict 키 순증 0 — 저장은 페이지 lore 절이다.
                if seeded:
                    _seam_new = str(out.get("trait_seam") or "").strip()
                    _aside_new = str(out.get("aside") or "").strip()
                    if _seam_new:
                        wiki_store.edit_lore_section(
                            channel_id, pid, "Core Traits",
                            OrchestrationService._replace_line3(lore_secs.get("Core Traits") or "",
                                                                _seam_new),
                            turn=turn)
                    if _aside_new:
                        wiki_store.edit_lore_section(channel_id, pid, "Aside", _aside_new, turn=turn)
                    if _seam_new or _aside_new:
                        logger.info(f"[Seed] 증류 반영: {name} "
                                    f"(seam={'○' if _seam_new else '－'} aside={'○' if _aside_new else '－'})")
            logger.info(f"[Sheet Grow] {kind} {name}: Observed 정리 {len(body)}→{len(new_body)}자")
            return True
        except Exception as _e_g:
            logger.warning(f"[Sheet Grow] 정리 실패: {name}: {_e_g}")
            return False

    async def _execute_background_extraction(
        self,
        ctx: ResponseContext,
        response: str,
        message: discord.Message,
        hints: Dict[str, bool]
    ) -> None:
        """백그라운드 추출 실행 (실제 로직)"""
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
            lore_npcs = list(npc_manager.get_lore_npc_names(channel_id))
            scene_npcs = list(npc_manager.get_scene_npc_names(channel_id))
            # [2026-09-18 식별 허브 S3] 무대 명부 + 식별 한 줄. PC 가면은 뺀다(인물 목록이지 PC 목록이 아니다).
            #   ⚠ 이 시점 0단은 **이번 턴 R4 위치 쓰기 전** — 막 들어온 인물은 안 잡힌다(의도).
            try:
                _pc_masks_on = {str(p.get("mask")) for p in
                                (domain_manager.get_domain(channel_id).get("participants") or {}).values()
                                if isinstance(p, dict) and p.get("mask")}
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
                self.client, self.model_id_flash,
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

            # ⛔[2026-09-15 관계 통합] NPCDepthUpdate(npc_depth_hints → update_helena_metric) 삭제 —
            #   NPC→PC 관계 질문은 Theoria relation 층 하나(update_world_state의 write_theoria_relations).

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

            # [2026-09-06 P8a] 값 델타·전이 큐·연산 적용부는 `_apply_outputs` 로 **이사**.
            #   WHY: 셋 다 전담 콜 산출이고, 이 try 안에 남겨두면 배치가 죽는 순간
            #   출력물 적용도 같이 사라진다 — 격리의 요점이 바로 그것이다.

            # NPC Behavioral Imprints
            npc_imp = updates.get("NPCImprintUpdate")
            if npc_imp and isinstance(npc_imp, dict):
                current_turn = _bg_turn_now()
                domain_manager.update_npc_imprints(channel_id, npc_imp, turn=current_turn)
                logger.info(f"[Imprint] {list(npc_imp.keys())}")

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

            # ⛔[2026-09-16 3차] 배치 narrative passives/trait_evolution 반영 삭제 — 조각 생산은
            #   시트 heavy 콜 + grow_sheet 정리 콜(quote 발췌) 하나. PlayerMemoryUpdate 의 남은 키는 아래 소비부가 읽는다.
            # [2026-09-24 감사] 3차 삭제 때 아래 줄까지 같이 빠져 NarrativeTracker 블록(L~1925 tensions·climate)이
            #   매 턴 NameError → update_narrative_tracker_state 저장 0(turn_log·arc·storyline 동결). 바인딩만 복원.
            pmu = updates.get("PlayerMemoryUpdate")

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
                _pc_masks = set()
                try:
                    for _p in domain_manager.get_domain(channel_id).get("participants", {}).values():
                        if _p.get("mask"):
                            _pc_masks.add(_p["mask"])
                except Exception:
                    pass
                involved_npcs = [n for n in _scene_names if n not in _pc_masks]
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
                present_npcs = [n for n in _present_names if n not in _pc_masks]
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

                    # [2026-09-16 시트 2차 §9] 로스터 전처리(개명·사망·무력화·즉석 생성·몹 태그·lore_seen) →
                    # 성장 관문 하나(grow_sheet): 관찰은 인물 페이지 Observed 절, 250자 자라면 heavy 정리 1콜.
                    try:
                        for _gn, _gd in await self._npc_roster_pass(channel_id, est_data, _pc_masks, ctx, turn_idx, prose=response):
                            await self.grow_sheet(channel_id, ("npc", _gn), _gd, turn=turn_idx)
                    except Exception as _e_sheet:
                        logger.warning(f"[NPC Sheet] enrichment skipped: {_e_sheet}")

                # [2026-09-16 시트 2차 §8·§9] PC도 같은 관문 — 행동 PC(uid)의 페이지(`!가면`)가 있을 때만.
                try:
                    await self.grow_sheet(channel_id, ("pc", ctx.user_id), updates.get("PCObserved"), turn=turn_idx)
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
                    client=self.client if hasattr(self, 'client') else None,
                    model_id=flash_model
                )

                domain_manager.update_narrative_tracker_state(channel_id, nt_state)
            except Exception as nt_err:
                logger.warning("[NarrativeTracker] Update failed: %s", nt_err)

            # [Scene Continuity 2층] 렌더링 지문 저장
            _gaze_now = ""   # [2026-09-02 R4] 이번 턴 gaze — 바로 아래 관찰 쓰기가 소비한다.
            _observed_now = set()   # [2026-09-03 R6] 이번 턴 관찰이 옮기거나 입장시킨 등록 키. 스케줄 틱의 제외 집합.
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
                    _pc_masks_obs = set()
                    try:
                        for _p in domain_manager.get_domain(channel_id).get("participants", {}).values():
                            if isinstance(_p, dict) and _p.get("mask"):
                                _pc_masks_obs.add(_p["mask"])
                    except Exception:
                        pass
                    _reg_obs = domain_manager.get_npcs(channel_id) or {}

                    def _obs_key(_raw):
                        """관찰된 이름 → 등록 키. 미등록·PC 가면은 빈 문자열(=건너뜀)."""
                        _r = str(_raw or "").strip()
                        if not _r or _r in _pc_masks_obs:
                            return ""
                        _k = domain_manager._find_npc_key(_reg_obs, _r)
                        return _k if (_k and _k not in _pc_masks_obs) else ""

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
            # [2026-08-17] 인라인 절차 → `vector_search.get_scene_relevant_chunks` 공용 진입점.
            #   랭킹 로직 소유는 검색층. 게이트(풀 ≤ TOP_K면 랭킹 생략)·폴백([] = 하류가 전량 사용)·
            #   공용 엔진 규율(캐시 공유, 구 인스턴스-로컬 캐시의 매턴 전량 재임베딩 병)은 함수 안에 산다.
            try:
                import vector_search as _vs_mod
                _lore_chunks = ctx.domain_data.get("lore_chunks", [])
                _ranked = await _vs_mod.get_scene_relevant_chunks(
                    self.client, channel_id, ctx.action_text or "",
                    top_k=config.VECTOR_TOP_K,
                    min_score=config.VECTOR_MIN_SCORE,
                    chunks=_lore_chunks,
                )
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
            _snap_domain = copy.deepcopy(domain_manager.get_domain(channel_id))
            # [V10 P4] fermented/deep는 JSON이 아니라 행에 있다 — 스냅샷에 명시로 실어야
            # !다시가 발효까지 되감는다(복원은 save_domain 이음매가 행으로 되돌린다).
            # [2026-09-24 감사] 게터의 폴백(행 읽기 실패 → JSON 잔여 = P4 이후 항상 []/"")을 스냅샷에 실으면
            #   이후 !다시의 save_domain(snapshot) 이음매가 sync_fermented([])로 행을 전부 지웠다.
            #   행 읽기가 None(실패)이면 세 키를 싣지 않는다 → 복원부의 "no history rows — not rewound" 경로.
            _snap_f = _snap_dr = None
            if getattr(config, "V10_HISTORY_READ_FROM_SQLITE", False):
                try:
                    import sqlite_store as _ss_snap
                    _snap_f = _ss_snap.read_fermented(channel_id)
                    _snap_dr = _ss_snap.read_deep(channel_id)
                except Exception as _e_snap:
                    logger.debug(f"[!다시] history rows snapshot skip: {_e_snap}")
                if _snap_f is not None and _snap_dr is not None:
                    _snap_domain["fermented_history"] = _snap_f
                    _snap_domain["deep_memory"] = _snap_dr.get("narrative") or ""
                    _snap_domain["deep_memory_data"] = _snap_dr.get("data") or {}
            else:
                _snap_domain["fermented_history"] = domain_manager.get_fermented_history(channel_id)
                _snap_dn, _snap_dd = domain_manager.get_deep_memory(channel_id)
                _snap_domain["deep_memory"], _snap_domain["deep_memory_data"] = _snap_dn, _snap_dd
            self._retry_snapshots[channel_id] = {
                "_ts": time.time(),
                "_data": _snap_domain,
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

                # 4.7. [2026-09-06 P2 경계 틱] 게임 시간의 **날짜가 넘어간 첫 턴**에만 도는 자리.
                #   자리가 여기인 이유: 4.5 가 시간을 옮긴 **직후**여야 이번 턴 날짜가 확정되고,
                #   프롬프트 조립(5.) **전**이어야 그 결과(공유 일지 꼬리)가 이번 턴 컨텍스트에 실린다.
                #   동기 부분은 코드뿐(마커 비교 · 구독자 호출)이고 콜은 전부 배경 큐로 간다 —
                #   렌더 지연 0, 매턴 콜 순증 0(하루 1회 배경 콜 1개).
                #   try 로 감싸는 건 킬스위치가 아니라 **렌더 무영향** 보장이다:
                #   경계 정산이 죽어도 이번 턴 산문은 나가야 한다.
                try:
                    import boundary_engine
                    await boundary_engine.on_turn(self, ctx, channel_id)
                except Exception as _e_bnd:
                    logger.debug(f"[Boundary] tick skipped: {_e_bnd}")

                # 4.7. [2026-09-06 P3 expr 단계] 파생값·조건 전이의 정산 자리.
                #   **boundary 뒤 · build_prompt 앞**이 계약이다(스펙 §0.6):
                #     - Judgment 결과(4)·[소지품] 적용(4)·날짜 전진(4.5)이 다 끝난 뒤여야
                #       `check=judgment` 와 재고 조건이 이번 턴 사실을 본다.
                #     - Slot 29(build_prose_feed)가 읽기 **전**이어야 ⑥ 뒤 최종값이 재료로 간다.
                #   콜 0 — 이 안에서 LLM 은 한 번도 안 불린다(렌더 지연 0).
                #   try 는 킬스위치가 아니라 **렌더 무영향** 보장이다: 정산이 죽어도 산문은 나간다.
                try:
                    import expr_engine
                    await expr_engine.run_turn(channel_id, ctx)
                except Exception as _e_expr:
                    logger.debug(f"[Expr] turn skipped: {_e_expr}")

                # 4.75. [2026-09-13 P14 도착물 핸드아웃] 도착물은 **산문의 입력**이다.
                #   자리가 여기인 이유: 4.7(전이 정산) 뒤여야 이번 턴 `deliver` 발화가 보이고,
                #   build_prompt(5.) **앞**이어야 그 본문이 이번 턴 렌더 프롬프트에 실린다.
                #   유일하게 **산문을 기다리게 하는** 콜이다(동기 1) — 그게 요지다: GM 은
                #   묘사 전에 종이를 민다. 실패하면 핸드아웃 없이 산문이 그대로 나간다.
                try:
                    await self._deliver_handout(ctx, message, channel_id)
                except Exception as _e_hand:
                    logger.debug(f"[Handout] skipped: {_e_hand}")
                # [2026-09-24 감사] 핸드아웃 메시지도 `!다시` 삭제 목록에 — 빠져 있어 옛 봉투/공고가 채널에 남고
                #   (turn_mail 행은 지워져 💌 누르면 "만료") 재실행이 도착물을 한 번 더 보냈다.
                if getattr(ctx, "handout_message_id", None):
                    current_retry_ctx["message_ids"].append(ctx.handout_message_id)

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
                    #   [2026-09-07 P9] 상태창은 산문 **꼬리 임베드**로 붙는다(머리 접합 폐지).
                    #   [2026-09-13 P9b] 1장째만이 아니라 **전 장**(섹션 장·append 창 포함).
                    #   [2026-09-13 P9c] 💠("쌓인 것")는 **매턴 상시**라 전송 시점에 붙인다.
                    #     합성은 `turn_mail.build_view` 한 곳 — 도착물이 생겨 사후 부착이
                    #     view 를 통째로 갈아끼워도 같은 함수가 다시 그려 💠 가 남는다.
                    #     💌💭📰 게이트는 무접촉(전송 시점엔 message_id 가 없어 안 붙는다).
                    import turn_mail as _tm_view
                    # [2026-09-13 P15] 전송 시점 임베드 데이터 = 재그림 판정의 before.
                    _panel_before = status_panel.build_turn_embed_data(channel_id)
                    sent_msgs = await bot_utils.send_long_message(
                        message.channel, _prose_for_display(response, channel_id),
                        view=_tm_view.build_view(channel_id),
                        embeds=status_panel.build_turn_embeds(channel_id, data=_panel_before),
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

                    # [2026-09-13 S1 ④→①] 회상 영수증 — 직전 턴 Slot 9에 실린 엔티티·인용이
                    # 산문에 닿았는지 1회 대조하고 스냅샷을 소비한다. 로그뿐이라 실패해도 무해.
                    try:
                        fermentation.log_recall_trace(channel_id, response)
                    except Exception:
                        pass

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

                    # ⚰9.5. [2026-09-13 P14] 산문 **뒤** 배경 게시판 콜 삭제.
                    #   WHY: 도착물은 이제 산문의 **입력**이다(핸드아웃) — 4.75 에서 동기로
                    #   돌고 그 본문이 렌더 프롬프트에 실린다. 여기 남겨 두면 같은 턴에
                    #   편지가 둘 열린다: 앞에서 읽은 편지 하나, 뒤에서 조용히 붙는 편지 하나.
                    #   그리고 이 자리를 여는 문(턴 간격 게이트)도 같은 카드에서 사라졌다 —
                    #   방아쇠가 ①선언 ②`arrival` 둘로 갈린 뒤 `trigger="turn"` 은 사문이다.

                    # 9.52. [2026-09-06 P3] 전이 알림 flush — 산문 message_id 가 확정된 **뒤**.
                    #   도착물은 (message, kind) 로 적립·교체되므로 렌더 전엔 보낼 수 없다.
                    #   보류분이 없으면 deliver 도 버튼도 0(순증 0). 실패는 무해 — 값은 이미 움직였고
                    #   알림만 안 붙는다.
                    try:
                        import expr_engine as _ee_mod
                        _flush_msg = sent_msgs[-1] if sent_msgs else None
                        await _ee_mod.flush_mails(_flush_msg, channel_id)
                    except Exception as _e_flush:
                        logger.debug(f"[Expr] mail flush skipped: {_e_flush}")

                    # 9.55. [2026-08-16 상태패널 v0] 하단 상태 패널 — 배경 콜 1개 + 코드 저장.
                    #   패널 정의(!출력룰 panel/상태창) 미등록이면 콜 0. 산문은 **저장본과 같은
                    #   순수 response**(표시용 헤더가 섞인 문자열이 아니다 — 헤더는 표시 계층 전용).
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
                                        self.client, config.role_model("light"), channel_id, _sp_prose)
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
                    #     [콜]     선별분 psyche + 구조 장면 앵커 → NPC별 한국어 1인칭 한 호흡
                    #     [폴백]   콜 실패·TURN_MIND_CALL=0 → v0 선별기(콜 0)가 같은 명단으로 선다
                    #   ⚠[2026-08-17 앵커 교체] 산문(response)을 **넘기지 않는다**. 구 배선은
                    #     직전 렌더 원문 꼬리를 콜 입력에 실었고, 그래서 "대사 복붙 금지"를
                    #     프롬프트로 방어해야 했다. 장면 앵커는 turn_mail 이 채널·DAI(구조층)
                    #     에서 직접 세운다 — 상태패널(9.55)과 달리 여기는 산문 무접촉이다.
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
                                            self.client, config.role_model("light"), channel_id,
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
                            _interm_now = bool((getattr(getattr(ctx, "bus", None), "doom", None) or {}).get("intermission_active"))
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

                    # 9.9. [2026-09-13 P15] 임베드 재그림 — **이 턴의 마지막 적재**여야 한다.
                    #   큐가 FIFO 라 여기서 서야 앞선 배경 쓰기가 전부 끝난 뒤에 돈다.
                    #   새 배경 태스크를 이 아래에 추가하지 마라 — 추가하면 그 쓰기는
                    #   재그림보다 늦게 돌고, 그 값은 다음 턴까지 화면에 없다.
                    await self._schedule_panel_refresh(channel_id, sent_msgs, _panel_before)
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
        # [2026-09-24 감사] 결과를 본다 — 전엔 10초 타임아웃을 무시하고 복원해, 늦게 끝난 배경 작업(추출·발효·
        #   출력물 콜)이 폐기된 턴의 결과를 **복원된 도메인 위에** 썼다. 못 끝나면 멈추고 다시 치게 한다
        #   (위에서 비운 대기 태스크는 어차피 되돌릴 턴 소속이다).
        if not await queue.wait_for_channel(channel_id, timeout=45.0):
            await message.channel.send("⏳ 이전 턴 배경 작업이 아직 도는 중입니다. 잠시 뒤 `!다시`를 다시 입력해 주세요.")
            return False

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
            if not any(k in snapshot for k in ("fermented_history", "deep_memory", "deep_memory_data")):
                logger.warning("[!다시] snapshot has no history rows (pre-P4) — fermented/deep not rewound")
            domain_manager.save_domain(channel_id, copy.deepcopy(snapshot))
            logger.info(f"[!다시] Domain snapshot restored for {channel_id}")
            # [2026-09-24 감사] NPC·지식 정본은 SQLite(V10_*_READ_FROM_SQLITE)인데 복원은 JSON 스냅샷뿐이라
            #   폐기 턴에 생긴 NPC·knows 가 읽기 뷰에 살아남고(delete_npc 는 JSON 키로 찾아 못 지움),
            #   다음 bulk 미러가 되감기 자체를 무효로 만들었다. 스냅샷 기준으로 행을 맞춘다(없는 행 삭제 + upsert).
            try:
                import sqlite_store as _ss_rs
                import state_guards as _sg_rs
                if getattr(config, "V10_NPCS_READ_FROM_SQLITE", False):
                    _snap_npcs = snapshot.get("npcs") or {}
                    for _nm in list((_ss_rs.read_npcs(channel_id) or {}).keys()):
                        if _nm not in _snap_npcs:
                            _ss_rs.delete_npc_row(channel_id, _nm)
                    _clean_n = {k: c for k, c in ((k, _sg_rs.validate_npc_write(k, v)) for k, v in _snap_npcs.items())
                                if c is not None}
                    if _clean_n:
                        _ss_rs.bulk_upsert_npcs(channel_id, _clean_n)
                if getattr(config, "V10_KNOWLEDGE_READ_FROM_SQLITE", False):
                    _snap_kn = snapshot.get("npc_knowledge") or {}
                    for _nm in list((_ss_rs.read_knowledge_all(channel_id) or {}).keys()):
                        if _nm not in _snap_kn:
                            _ss_rs.delete_knowledge(channel_id, _nm)
                    _clean_k = {k: c for k, c in ((k, _sg_rs.validate_knowledge_write(k, v)) for k, v in _snap_kn.items())
                                if c is not None}
                    if _clean_k:
                        _ss_rs.upsert_knowledge_bulk(channel_id, _clean_k)
            except Exception as _e_rs:
                logger.warning(f"[!다시] SQLite NPC/지식 재동기화 실패(무시): {_e_rs}")
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
            # [2026-09-14 W2] 페이지 절 되감기 — **트림 뒤**가 계약이다(복원→트림→절).
            # 기준 턴은 스냅샷의 world_state.turn_index; 그 다음 턴부터가 되감을 구간.
            # 사건 문서(발효 엔트리)는 위 스냅샷 오버레이가 이미 되감으므로 여기선 절만 본다.
            # WIKI_PATCHES와 무관하게 WIKI_PAGES 게이트만 본다(옛 패치도 되감아야 하므로).
            try:
                import config as _cfg_wr
                if getattr(_cfg_wr, "WIKI_PAGES", False):
                    import wiki_store as _ws_wr
                    _snap_turn = int((snapshot.get("world_state", {}) or {}).get("turn_index", 0) or 0)
                    _wr_n = _ws_wr.revert_sections_after(channel_id, _snap_turn + 1)
                    logger.info(f"[Wiki] revert from_turn={_snap_turn + 1} sections={_wr_n}")
            except Exception as _e_wr:
                logger.debug(f"[Wiki] revert skipped: {_e_wr}")
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
                    ctx.bus = updated_context.shared_bus  # [2026-09-24 감사]
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
                    # [2026-09-07 P9] 표시 전용 상태 임베드 (저장본은 무오염 — 08-16 계약 그대로)
                    # [2026-09-13 P9b] 전 장 — 세 경로가 같은 화면을 낸다.
                    # [2026-09-13 P15] 재그림도 세 경로가 같다 — 배경 쓰기가 있는 곳엔 재그림이 있다.
                    _panel_before = status_panel.build_turn_embed_data(channel_id)
                    sent_msgs = await bot_utils.send_long_message(
                        message.channel, _prose_for_display(response, channel_id),
                        embeds=status_panel.build_turn_embeds(channel_id, data=_panel_before),
                    )
                    # 히스토리: PC 행동은 이미 waiting 모드에서 저장됨, Model 응답만 추가
                    domain_manager.append_history(channel_id, "Model", response)

                    # Background Extraction (첫 PC 기준)
                    await self.schedule_background_extraction(ctx, response, message)
                    await self._schedule_panel_refresh(channel_id, sent_msgs, _panel_before)

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
                    ctx.bus = updated_context.shared_bus  # [2026-09-24 감사]
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
                    # [2026-09-07 P9] 표시 전용 상태 임베드 (저장본은 무오염 — 08-16 계약 그대로)
                    # [2026-09-13 P9b] 전 장 — 세 경로가 같은 화면을 낸다.
                    # [2026-09-13 P15] 재그림도 세 경로가 같다.
                    _panel_before = status_panel.build_turn_embed_data(channel_id)
                    sent_msgs = await bot_utils.send_long_message(
                        message.channel, _prose_for_display(response, channel_id),
                        embeds=status_panel.build_turn_embeds(channel_id, data=_panel_before),
                    )
                    domain_manager.append_history(channel_id, "관찰", "[관찰 모드]")
                    domain_manager.append_history(channel_id, "Model", response)

                    await self.schedule_background_extraction(ctx, response, message)
                    await self._schedule_panel_refresh(channel_id, sent_msgs, _panel_before)

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
