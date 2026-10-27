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
# [C63 M4] `import traceback` 삭제 — 쓰던 자리(배경 추출 바깥 catch)가 shared_extract 로 갔다. 남은 자리는 함수 안 import.
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

# [2026-10-27 C82] 성장발효 연속 실패 셈 {(채널, 페이지, 삭힐 마지막 턴): 횟수} — 프로세스 메모리(재시작이면 처음부터).
_GROWTH_FAILS: Dict[Tuple[str, str, int], int] = {}
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
from response_processor import _INLINE_TAG_WRAP, _unwrap_inline_tag, pc_name_variants  # noqa: F401


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
    pc_names = pc_name_variants(pc_names)   # [2026-09-29] 괄호 병기 마스크 풀기(`아담(Adam)` → 아담·Adam도 PC줄)
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
        # [2026-09-29 배치1 C1] 과거형·개수 없이 — 'N건'이 이번 턴 할당량으로 읽혀 추론 자석 1위(41표본·인용 219)였다.
        parts.append(
            f"[FORMAT] Last reply ran a bare speech line without its speaker's name (e.g. {'; '.join(examples)}). "
            f"Bare speech lines open 이름: \"대사\"; quotes inside narration stay free."
        )
    elif _named_lines == 0 and _total_quotes >= 3:
        # [2026-08-13 기본형 승격] 전량 서술 삽입형 = 기본형 우회. bare 위반과 동시 점등 방지(elif).
        parts.append(
            f"[FORMAT] Last reply wove every spoken line into narration (no 이름: line); "
            f"spoken exchange defaults to its own line opening 이름: \"대사\"; weaving stays the deliberate exception."
        )
    if impersonations:
        imp_examples = impersonations[:2]
        parts.append(f"[IMPERSONATION] PC 대사 창작 {len(impersonations)}건: {'; '.join(imp_examples)}. PC의 대사는 플레이어의 것이다: 입력에 없는 말을 새로 짓지 않는다(다듬는 것은 정상).")
    return " ".join(parts)


def _first_msg_id(sent_msgs) -> Optional[int]:
    """[2026-10-17 C49 R4] 보낸 산문 메시지들 → 첫 메시지 id(없으면 None). 히스토리 행 message_id 용."""
    try:
        return getattr(sent_msgs[0], "id", None) if sent_msgs else None
    except Exception:
        return None


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
            # [2026-10-25 C77 E-4 · Q34] 덮기 전에 직전 PC 자리를 읽어 둔다 — 새로 선 자동 감지 장소를 그 옆에 잇는 재료.
            try:
                _prev_loc = str(domain_manager.get_current_location(channel_id) or "").strip()
            except Exception:
                _prev_loc = ""
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
                _created_cur = False
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
                            elif _is_last:
                                _created_cur = True
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
                        _created_cur = world_tree.add_node(
                            channel_id, current_location,
                            node_type=(_ltype or "area"), parent_id=_pname_fb,
                            properties=_lprops,
                        ) == "created"
                    elif _pname_fb and not _exist.get("parent_id"):
                        world_tree.update_node(
                            channel_id, _exist.get("name", current_location), parent_id=_pname_fb,
                        )
                # [2026-10-25 C77 E-4 · Q34] 방금 선 자동 감지 장소가 **뿌리**(부모 못 찾음)이고 region 이 아니면 직전 PC 자리와
                #   1홉 간선으로 잇는다(레티어스 10-25 "간선"). 병: 진료소 문밖 골목이 뿌리에 붙어 진료소에서 6홉 → 방금 나온 우물이
                #   렌더 문턱 밖으로 숨었다. 나무 모양 · 위키 부모 승격은 안 건드린다(간선만 — 우물 거리 `hop_distances_in` 은 간선을 센다).
                if _created_cur and _prev_loc and _ltype != "region":
                    _nodes_e4 = world_tree.get_all_nodes(channel_id) or {}
                    _cid = world_tree.resolve_node_id(channel_id, current_location, allow_token=False)
                    _pid = world_tree.resolve_node_id(channel_id, _prev_loc, allow_token=False)
                    if _cid and _pid and _cid != _pid and not (_nodes_e4.get(_cid) or {}).get("parent_id"):
                        _cr = world_tree.add_connection(
                            channel_id, (_nodes_e4.get(_pid) or {}).get("name", _prev_loc),
                            (_nodes_e4.get(_cid) or {}).get("name", current_location),
                            description="자동 감지 — 바로 옆에서 걸어 나옴")
                        logger.info("[presence-check] auto place '%s' linked to previous '%s': %s",
                                    current_location, _prev_loc, _cr)
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
            _pc_masks_att = domain_manager.pc_registry(channel_id)   # [2026-10-07 PC 이름 단일화] 가면 이름 규칙 명부
            # [2026-08-11 사망 파이프라인] 자동 재등록 게이트 ① — 죽은 인물 이름이 분석에 다시 떠도
            #   관계가 시체 위에 적립되지 않게 입구에서 거른다(down은 거르지 않음).
            _dead_att = {n for n in _psy_rel
                         if isinstance(n, str) and npc_manager.get_npc_status(
                             npc_manager.get_npc(channel_id, n) or {}) == "dead"}
            if _dead_att:
                logger.info(f"[NPC Status] dead 관계 갱신 차단(환각 등장 신호): {', '.join(sorted(_dead_att))}")
            _skip_rel = {n for n in _psy_rel if isinstance(n, str)
                         and domain_manager.is_pc_name(n, _pc_masks_att)} | _dead_att
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
            _pc_masks_k = domain_manager.pc_registry(channel_id)   # [2026-10-07 PC 이름 단일화]
            for npc_name, k_data in new_knowledge.items():
                if not isinstance(k_data, dict):
                    continue
                if domain_manager.is_pc_name(npc_name, _pc_masks_k):
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
                # [2026-09-30 wave8_2nd G] 오해만 있고 knows가 빈 NPC도 저장(빈 목록은 게이트를 안 연다 — 스키마 채우기용
                #   빈 엔트리가 secrets_held를 덮어쓰지 않게).
                if k_data.get("knows") or k_data.get("false_beliefs"):
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
            scene_npcs = [n for n in new_knowledge.keys() if not domain_manager.is_pc_name(n, _pc_masks_k)]
            # [2026-10-27 C85 D2] 전파는 손잡이 KNOWLEDGE_PROPAGATE(기본 끔) — 원문에 없는 "말해 줬다"를 지어내서.
            if len(scene_npcs) >= 2 and getattr(config, "KNOWLEDGE_PROPAGATE", True):
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
            # [2026-10-23 C68 A2a] 압축 모드(Activity 떠 있음)면 시간 흐름 줄을 따로 안 보낸다 —
            #   압축 임베드 꼬리에 시간·장소가 이미 있다. 시계는 위 process_time_flow 가 이미 옮겼다(빠지는 건 메시지뿐).
            if status_panel.turn_compact_mode():
                logger.info("[TimeFlow] 압축 모드 — 시간 줄 안 보냄: %s", time_msg)
            else:
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
        ctx.judgment_embeds = list(result.get("judgment_embeds") or [])   # [2026-10-15 C42]
        
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

        # [2026-10-23 C61] item_usage 처리 삭제 — 렌더 **전** 입력만 보고 소지품을 움직이던 길이다(시도가
        #   실패해도 획득으로 셌다). 소지품 = 기본 선언 목록, 변화는 출력물 추출 콜이 산문(결과)을 보고 잡는다.
        #   산문 앞 `📦 소비 / 📥 획득` 시스템 줄도 같이 사라졌다(변화는 💠 노트북 칸의 ↕ 줄로 본다).

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

    @staticmethod
    def _pc_names_for_extract(channel_id: str, user_id: Any) -> List[str]:
        """[2026-10-07 PC 이름 단일화 S2] 배경 추출 두 콜에 실을 PC 이름 — `!가면` 등록 가면, 행동 PC 먼저.
        활성 참가자만(une_facade all_pcs 와 같은 기준). 가면이 없거나 "PC"·"Unknown"이면 뺀다."""
        out: List[str] = []
        try:
            parts = domain_manager.get_domain(channel_id).get("participants", {}) or {}
            order = [str(user_id)] + [u for u in parts if str(u) != str(user_id)]
            for uid in order:
                p = parts.get(uid) if uid in parts else parts.get(str(uid))
                if not isinstance(p, dict) or p.get("status") != "active":   # une_facade all_pcs 와 같은 기준
                    continue
                m = str(p.get("mask") or "").strip()
                if m and m not in ("PC", "Unknown") and m not in out:
                    out.append(m)
        except Exception:
            pass
        return out

    # ⚰ [2026-10-23 C63 M4] `_apply_outputs` → after_outputs 정거장 구독자 넷(shared_extract out_values·out_notebook·
    #   out_status·out_append — 주인 출력룰·상태창·판정·상태패널). 6단 순서·단마다 try 그대로.
    #   `_apply_status_changes`·`_apply_strike_changes`는 여기 남아 out_status 가 `rt.` 로 부른다.

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
        _to_strikes = []
        for name in (status_add or []):
            if isinstance(name, str) and name.strip():
                _nm = name.strip()
                # [2026-10-15 C42] 알려진 나쁜 상태(부상·공포…)는 타격칸으로 — 지시문이 status 를 좋은 상태 전용으로
                #   바꿨어도 모델이 옛 버릇으로 흘리는 경우를 받는다. 모르는 이름은 좋은 상태로 받는다.
                if game_character.is_known_debuff(_nm):
                    _to_strikes.append(_nm)
                    continue
                _known = _nm in getattr(config, "STATUS_EFFECTS", {}) or _nm in getattr(config, "LEGACY_TAG_MAP", {})
                _data = None if _known else {"name": _nm, "tag": _nm, "type": "buff", "severity": 0,
                                             "modifiers": {"judgment": 5}}
                p_data, _ = game_character.update_status_effect(p_data, "add", _nm, _data, current_turn)
                changed = True
        for name in (status_remove or []):
            if isinstance(name, str) and name.strip():
                p_data, _ = game_character.update_status_effect(p_data, "remove", name.strip(), None, current_turn)
                changed = True
        if changed:
            domain_manager.save_participant_data(channel_id, user_id, p_data)
        if _to_strikes:
            self._apply_strike_changes(channel_id, user_id, _to_strikes, None)

    @staticmethod
    async def _send_with_judgment_embeds(channel, text: str, embed_datas):
        """[2026-10-15 C42] 시스템 메시지(판정 한 줄 등) + 판정 임베드. 임베드가 실패해도 한 줄은 나간다."""
        embeds = []
        try:
            embeds = status_panel.build_judgment_embeds(embed_datas)
        except Exception as _e_je:
            logger.debug(f"[Judgment] 임베드 조립 skip: {_e_je}")
        if embeds:
            try:
                return await channel.send(text, embeds=embeds)
            except Exception as _e_js:
                logger.warning(f"[Judgment] 임베드 전송 실패 → 한 줄만: {_e_js}")
        return await channel.send(text)

    def _apply_strike_changes(self, channel_id: str, user_id: str, strike_add, strike_clear) -> None:
        """[2026-10-15 C42] 출력물 콜 strike_add/strike_clear → 타격칸 4칸. 4칸이 차면 더 안 받는다(넘침은 렌더 몫)."""
        if not strike_add and not strike_clear:
            return
        p_data = domain_manager.get_participant_data(channel_id, user_id)
        if not p_data:
            return
        p_data, changed, overflow = game_character.apply_strike_changes(p_data, strike_add, strike_clear)
        if overflow:
            logger.info(f"[Strike] 4칸 가득 — 안 받은 타격: {overflow}")
        if changed:
            domain_manager.save_participant_data(channel_id, user_id, p_data)
            logger.info(f"[Strike] {game_character.format_strikes(p_data)}")

    # =========================================================
    # ⚰ [2026-10-23 C64 M5] STEP 5.5 ARRIVAL HANDOUT(`_deliver_handout`)은 world_board.handout_station 으로 이사 —
    #    게시판 모듈의 pre_render_call 정거장. 5.5 자리는 run_station 한 줄(아래 execute).
    # =========================================================

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
    # ⚰ [2026-10-23 C63 M4] `schedule_background_extraction` → shared_extract `bg_outputs`·`bg_world`·`bg_ferment`
    #   (background 정거장 2·4·6) + `extraction_hints_for`. 게이트·힌트·큐 이름·우선순위 글자 그대로.

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
        # [2026-10-23 C69 A2b] 이 채널의 마지막 산문 메시지 = target(재그림 전). 늦게 붙은 도착물의 임베드 고침 자리 판정용.
        status_panel.note_view_message(channel_id, getattr(target, "id", 0))

        async def _run_panel_refresh():
            # 비교는 `build_turn_embed_data`(discord 비의존 순수 데이터) 로 한다 —
            # Embed 객체를 두 번 지어 비교하면 같은 값에도 edit 이 나갈 수 있다.
            try:
                # [C59] 보낸 것과 같은 모양으로 비교. [2026-10-23 C68 A2a] 대상 메시지 id 를 넘겨 알림 줄까지 —
                #   이 턴 도착물(9.52 전이 알림 · 큐 안 속마음)은 FIFO 상 전부 이 재그림보다 먼저 적립된다.
                #   보낼 때는 메시지가 없어 알림이 없었으니, 알림이 생기면 데이터가 달라져 edit 이 한 번 나간다.
                after = status_panel.turn_view_data(channel_id, message_id=getattr(target, "id", 0) or 0)
            except Exception as e:
                logger.debug(f"[Panel] refresh build skipped: {e}")
                import turn_timing as _tt_s
                _tt_s.settled(channel_id, getattr(target, "id", None))
                return
            import turn_timing as _tt_s   # [2026-10-24 C74 W0-4] 산문 → 상태창 · 출력물 정리 끝 = 이 재그림이 끝난 때
            if after == before_data:
                status_panel.mark_refreshed(channel_id, getattr(target, "id", 0))   # [C69 A2b]
                _tt_s.settled(channel_id, getattr(target, "id", None), edited=False)
                return                      # 이번 턴 배경이 값을 안 건드렸다 = edit 0
            try:
                await target.edit(embeds=status_panel.turn_view_embeds(channel_id, data=after))
            except Exception as e:
                # 삼킨다. 화면이 한 턴 늦는 건 무해하지만 턴이 죽는 건 무해하지 않다.
                logger.info("[Panel] edit fail: %s", e)
            # [C69 A2b] 재그림이 돌았다 — 이 뒤에 붙는 도착물은 attach_button 이 임베드까지 고친다.
            status_panel.mark_refreshed(channel_id, getattr(target, "id", 0))
            _tt_s.settled(channel_id, getattr(target, "id", None), edited=True)

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
                            if not domain_manager.is_pc_name(n, _pc_masks)]
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
        # [2026-10-10 별칭 A6] RP상 정정 — 신호는 루프 안에서 모으고, 판정·병합은 루프 뒤 한 번.
        _merge_sig, _turn_resolved = [], []   # [(dup, canon, via, 항목번호)], [(항목번호, 최종 키)]
        for _ent_i, (_npc_name, _ch) in enumerate(_changes.items() if isinstance(_changes, dict) else []):
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
                if _ext_key and _ext_key in _onstage_now and not domain_manager.is_pc_name(_ext_key, _pc_masks):
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
                    # [2026-10-10 별칭 A6 R2] 라벨이 다른 인물의 자기 키 꼴 = 장면이 "그 사람이 이 사람"이라 했다.
                    #   별칭 승격은 아래에서 충돌로 막히고, 병합 신호만 모은다(두 턴 확인은 루프 뒤).
                    try:
                        _own_al = domain_manager._find_npc_key(_npcs_now, _dec["alias"][0])
                        if (_own_al and _own_al != _dec["key"]
                                and npc_manager.is_own_label(_dec["alias"][0], _own_al)):
                            _merge_sig.append((_own_al, _dec["key"], "refers_to", _ent_i))
                    except Exception as _e_r2:
                        logger.debug(f"[Identity] 정정 신호(R2) 건너뜀: {_e_r2}")
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
                    if (_new_nm != _npc_name and not domain_manager.is_pc_name(_new_nm, _pc_masks)
                            and _src_np
                            and npc_manager.npc_source(_src_np) != "lore"):
                        if npc_manager.get_npc(channel_id, _new_nm):
                            # [2026-10-10 별칭 A6 R1] 이 인물에게 이미 있는 이름이 붙었다 = RP상 정정. 개명 대신 병합 신호 —
                            #   합치는 건 루프 뒤(같은 턴 따로 등장했는지는 항목 전부를 봐야 안다).
                            _merge_sig.append((_npc_name, _new_nm, "named_as", _ent_i))
                            logger.info(f"[NPC Naming] '{_npc_name}' = 기존 '{_new_nm}' — 정정 병합 신호")
                        else:
                            npc_manager.handle_identity_reveal(
                                channel_id, _npc_name, _new_nm,
                                reason="explicit naming in scene")
                            logger.info(f"[NPC Naming] {_npc_name} → {_new_nm}")
                            _npc_name = _new_nm  # 이후 관찰은 새 이름으로
                except Exception as _e_nm:
                    logger.debug(f"[NPC Naming] skip: {_e_nm}")
            _turn_resolved.append((_ent_i, _npc_name))   # [A6] 이번 턴 이 항목의 최종 키(같은 턴 따로 등장 판정)
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
                    and not domain_manager.is_pc_name(_npc_name, _pc_masks)):
                _inc_ev = str(_inc.get("evidence") or "").strip()
                if _inc_ev:
                    _pending_down.append((_npc_name, _inc_ev))
                else:
                    logger.info(f"[NPC Status] {_npc_name}: incapacitated 근거 없음 — 무효")
            _desc = _ch.get("descriptor")
            if not _desc or not str(_desc).strip() or domain_manager.is_pc_name(_npc_name, _pc_masks):
                continue
            _desc = str(_desc).strip()
            _new_indiv = bool(_ch.get("new_individual"))
            _existing = npc_manager.get_npc(channel_id, _npc_name)
            if not _existing:
                # [2026-10-27 C84] 잠긴 모습 · 등장 전 이름은 세션 NPC 로 세우지 않는다(조건 전에 산문에 나와도 기록은 비운다).
                try:
                    import npc_forms as _nf_rp
                    if _nf_rp.blocks_new_key(channel_id, _npc_name):
                        logger.info(f"[Form] 잠긴 이름 — 세션 NPC 안 세움: {_npc_name}")
                        continue
                except Exception:
                    pass
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
        # [2026-10-10 별칭 A6] RP상 정정 병합 — 루프 뒤 한 번(같은 턴 따로 등장은 항목 전부를 봐야 안다).
        #   합쳐진 이름은 이번 턴 관찰·쓰러짐 목록에서도 본체로 바꾼다 — 안 바꾸면 사라진 키에 다시 써서 또 갈라진다.
        if _merge_sig or _turn_resolved:
            try:
                _mr = npc_manager.settle_identity_merges(
                    channel_id, _merge_sig, _turn_resolved, int(turn_idx or 0), _pc_masks)
            except Exception as _e_mg:
                _mr = {}
                logger.warning(f"[Identity] 정정 병합 판정 실패: {_e_mg}")
            _remap = {_d: _c for _d, _c, _v in (_mr.get("merged") or [])}
            if _remap:
                def _final(_n):
                    _seen_f = set()
                    while _n in _remap and _n not in _seen_f:
                        _seen_f.add(_n)
                        _n = _remap[_n]
                    return _n
                _g2 = {}
                for _gn, _gd in _grow:
                    _gn = _final(_gn)
                    if _gn in _g2:
                        if _gd and _gd not in _g2[_gn]:
                            _g2[_gn] = f"{_g2[_gn]} / {_gd}"
                    else:
                        _g2[_gn] = _gd
                _grow = list(_g2.items())
                _pending_down = [(_final(_dn), _dev) for _dn, _dev in _pending_down]
            for _rk, _mk in (("merged", "merged"), ("pending", "merge_pending"),
                             ("held", "merge_held"), ("dropped", "merge_dropped")):
                if _mr.get(_rk):
                    _receipt[_mk] = [f"{_x[0]} → {_x[1]} ({_x[2]})" if isinstance(_x, tuple) else _x
                                     for _x in _mr[_rk]]
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
            import npc_forms as _nf_pa   # [2026-10-27 C84] 잠긴 모습 이름을 남의 별칭으로 올리면 그 모습이 영영 못 선다
            if _nf_pa.is_reserved(channel_id, _lb):
                logger.info(f"[Form] 잠긴 이름 — 별칭 승격 안 함: '{_lb}' → {key}")
                return False
        except Exception:
            pass
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
            # [2026-10-14 C37 C2] 출처 표시 — 플레이가 붙인 별칭은 `!클리어`가 뗀다.
            _new["_play_aliases"] = npc_manager.mark_play_aliases(_cur.get("_play_aliases"), [_lb])
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

    async def grow_sheet(self, channel_id, entity, observation=None, *, turn=None, condense: bool = True) -> bool:
        """[2026-09-16 시트 2차 §9] 성장 관문 하나 — PC·NPC 공통.

        entity = ("pc", uid) | ("npc", name). 관찰은 인물 페이지 play 절 `Observed`에
        `append_play_item`(중복 = `_norm_quote` 대조). 직전 정리 뒤 250자 이상 자랐으면 heavy 정리 1콜
        (lore 절 전문 + Observed + 로어 접지) → `write_play_section`(CAS, reason="grow")로 **Observed만** 덮는다.
        lore 절 무접촉, 동결 판정 없음, 저장 캡 없음(길이는 정리가 관리). 마커 = 페이지 built_len.
        [2026-10-24 C74 W4-1b] condense=False = 덧붙이기만(동기) — heavy 정리는 기억 차선 꼬리
        (shared_extract.bg_narrative_tail)가 같은 함수를 관찰 없이 다시 불러 한다. 임계 판정은 그때 상태로.
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
        if not condense:
            return False
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
            OrchestrationService._condense_post(channel_id, kind, key, name, pid, out, new_body, seeded, lore_secs, turn, _adopt)
            logger.info(f"[Sheet Grow] {kind} {name}: Observed 정리 {len(body)}→{len(new_body)}자")
            return True
        except Exception as _e_g:
            logger.warning(f"[Sheet Grow] 정리 실패: {name}: {_e_g}")
            return False

    async def grow_page(self, channel_id, due) -> bool:
        """[2026-10-27 C82] 성장발효 — 페이지 하나(인물 · 장소 · 세력)를 성장 도장 뒤 원문 턴으로 키운다.

        due = memory_index.pages_due 한 줄. 콜 = cognition.grow_play_sections(옛 heavy 정리 자리를 넓힌 것) →
        wiki_store.apply_growth(인용 게이트 · 정정 가드 · hash · 사유 growth) → 인물이면 옛 정리 뒤 몫(_condense_post).
        성공 = 성장 도장(growth_log, 삭힌 마지막 턴). 실패 · hash 충돌은 도장을 안 넘겨 다음 꼬리에 다시 —
        같은 범위가 GROWTH_MAX_FAIL 번 연속 실패하면 도장만 넘긴다(돈 드는 콜이 같은 실패를 끝없이 되풀이하지 않게)."""
        import wiki_store
        import memory_index
        import sqlite_store
        pid = str(due.get("page_id") or "")
        kind = str(due.get("kind") or "")
        name = str(due.get("name") or "")
        turns = due.get("turns") or []
        if not pid or not turns or not getattr(self, "client", None):
            return False
        upto = max(int(t.get("turn") or 0) for t in turns)
        # 실패 셈은 범위의 **시작**(지금 도장)에 묶는다 — 끝(upto)은 그 페이지가 또 나올 때마다 밀려 셈이 영영 안 찬다.
        fkey = (str(channel_id), pid, int(due.get("mark") or 0))
        # 쓰기 턴 = 지금 턴(`!다시` 되감기가 쓰기 턴으로 본다 — 도장 행은 워터마크로 지워지니 둘이 같은 턴에 묶여야 한다).
        #   upto 는 도장 · 날짜 도장에만 쓴다.
        try:
            now_t = int((domain_manager.get_world_state(channel_id) or {}).get("turn_index", 0) or 0) or upto
        except Exception:
            now_t = upto
        page = wiki_store.get_page(channel_id, pid) or {}
        aliases = [a for a in (page.get("aliases") or []) if a]
        ekind, key, seeded, seen_labels = kind, None, False, {}
        if kind == "character":
            if due.get("is_pc"):
                ekind, key = "pc", due.get("owner_uid")
            else:
                ekind, key = "npc", name
                _npc = npc_manager.get_npc(channel_id, name) or {}
                aliases = list(dict.fromkeys(aliases + [a for a in (_npc.get("aliases") or []) if isinstance(a, str) and a]))
                seen_labels = _npc.get("lore_seen") or {}
                try:
                    seeded = (wiki_store.page_source(channel_id, pid) == wiki_store.SEED_SOURCE)
                except Exception:
                    seeded = False
        raw, srcs = memory_index.growth_raw_text(channel_id, turns, [name] + aliases,
                                                 int(getattr(config, "GROWTH_INPUT_CHARS", 12000) or 12000))
        if not raw.strip():
            # 원문 다리가 없다(색인 구간 빔 · 행 트림) — 같은 턴을 다시 세지 않게 도장만 넘긴다(콜 0).
            sqlite_store.append_growth_log(channel_id, pid, upto, now_t)
            return False
        body, h = wiki_store.get_play_body(channel_id, pid, "Observed")
        item_secs = tuple(s for s in wiki_store._play_enum(kind) if wiki_store._is_item_section(s))
        existing_full = {s: wiki_store.get_play_body(channel_id, pid, s)[0] for s in item_secs}
        # 프롬프트엔 절마다 최근 GROWTH_ITEMS_SHOWN 줄만(History 는 끝없이 자란다) — 중복 판정은 코드가 전체로 한다.
        _shown = max(1, int(getattr(config, "GROWTH_ITEMS_SHOWN", 40) or 40))
        existing = {}
        for _s, _b in existing_full.items():
            _ls = [ln for ln in str(_b or "").splitlines() if ln.strip()]
            existing[_s] = ("\n".join((["(앞 %d줄 생략)" % (len(_ls) - _shown)] if len(_ls) > _shown else []) + _ls[-_shown:])
                            if _ls else "")
        lore_secs = wiki_store.get_lore_sections(channel_id, pid)
        lore_text = "\n\n".join(f"### {k}\n{v}" for k, v in lore_secs.items())
        try:
            grounding = await npc_manager.build_distill_grounding(
                channel_id, name, aliases=aliases, observations=body, seen_labels=seen_labels, client=self.client)
        except Exception:
            grounding = ""
        try:
            import corrections as _corr_g
            corr = _corr_g.block_for(channel_id, raw + "\n" + body)
        except Exception:
            corr = ""
        frag_names = ""
        if ekind == "pc":
            _frs_now = (domain_manager.get_ai_memory(channel_id, key) or {}).get("passives") or []
            frag_names = ", ".join(str(f.get("name")) for f in _frs_now if isinstance(f, dict) and f.get("name"))
        out = await cognition.grow_play_sections(
            self.client, name, kind, lore_text, body, raw, existing_items=existing, item_sections=item_secs,
            grounding=grounding, corrections_block=corr, want_aspects=(ekind == "npc"), want_fragments=(ekind == "pc"),
            fragments_text=frag_names, seeded=seeded)
        new_body = str((out or {}).get("observed") or "").strip()
        _adopt = []
        if out and ekind == "pc" and new_body:
            new_body, _adopt = OrchestrationService._take_fragments(channel_id, key, new_body, out.get("fragments"))
        turn_meta = {}
        for tr in turns:
            _gts = [r.get("game_time") for r in (memory_index.raw_for_turn(channel_id, tr.get("turn"), row=tr) or [])
                    if isinstance(r, dict) and isinstance(r.get("game_time"), dict)]
            if _gts:
                turn_meta[int(tr.get("turn") or 0)] = {"game_time": _gts[-1]}
        # 인용 대조 원문 = 이번 턴들 + 지금 절 본문(예전 성장이 원문에서 옮긴 인용이 이번 창에 없다고 벗겨지지 않게)
        _qsrc = list(srcs) + [body] + [b for b in existing_full.values() if b]
        rc = (wiki_store.apply_growth(channel_id, pid, new_body, out.get("items"), _qsrc, now_t,
                                      pre_observed=(body, h), turn_meta=turn_meta) if out else {})
        if not out or rc.get("rej_hash"):
            n = _GROWTH_FAILS.get(fkey, 0) + 1
            _GROWTH_FAILS[fkey] = n
            if n < int(getattr(config, "GROWTH_MAX_FAIL", 3) or 3):
                logger.info(f"[Growth] {kind} {name}: {'콜 실패' if not out else 'hash 충돌'} {n} — 도장 그대로, 다음 꼬리에 다시")
                return False
            logger.warning(f"[Growth] {kind} {name}: {n}번 연속 실패 — T{upto} 까지 도장만 넘김")
            _GROWTH_FAILS.pop(fkey, None)
            sqlite_store.append_growth_log(channel_id, pid, upto, now_t)
            return False
        _GROWTH_FAILS.pop(fkey, None)
        # 절은 이미 썼다 — 도장을 먼저 찍고(실패해도 같은 턴을 다시 읽어 줄을 겹쳐 쓰지 않게), 뒤 몫은 따로 감싼다.
        if not sqlite_store.append_growth_log(channel_id, pid, upto, now_t):
            logger.warning(f"[Growth] {kind} {name}: 도장 쓰기 실패 — 다음 성장이 같은 턴을 다시 읽을 수 있다")
        if new_body and rc.get("observed") and ekind in ("pc", "npc"):
            try:
                OrchestrationService._condense_post(channel_id, ekind, key, name, pid, out, new_body, seeded,
                                                    lore_secs, now_t, _adopt)
            except Exception as _e_post:
                logger.warning(f"[Growth] {kind} {name}: 정리 뒤 몫 skipped: {_e_post}")
        logger.info(f"[Growth] {kind} {name}: 턴 {len(turns)}개(~T{upto}) → Observed {len(body)}→{len(new_body)}자 "
                    f"· 항목 +{rc.get('items', 0)}")
        return True

    @staticmethod
    def _condense_post(channel_id, kind, key, name, pid, out, new_body, seeded, lore_secs, turn, _adopt) -> None:
        """[2026-10-27 C82] grow_sheet 정리 반영 뒤 몫(글자 그대로 옮김) — built_len · PC 조각 채택 · NPC 면모 · 시드 seam/aside.
        grow_sheet(옛 250자 정리)와 grow_page(성장발효)가 같이 부른다."""
        import wiki_store
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

    # ⚰ [2026-10-23 C63 M4] `_execute_background_extraction` → shared_extract(`_run_world_batch` 앞부분 + after_batch
    #   구독자 다섯: npc·world·goal(C74 판2 — 옛 quest)·narrative·presence). 몸은 글자 그대로, 블록마다 try(격리). 여기 남은 도우미
    #   (`grow_sheet`·`_npc_roster_pass`·`_advance_scene_time`·`_pc_names_for_extract`)는 구독자가 `rt.` 로 부른다.


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
            # [2026-10-24 C74 W0] 턴 시계 — 단계 경계마다 mark, 끝에서 [Turn] 한 줄(동작 변화 0).
            import turn_timing as _tt
            _tt.begin(channel_id, "turn")
            # [2026-10-24 C73 M7b] 앞 턴 코드 앱 신고 정산이 도는 중이면 끝날 때까지 잡는다(⏳, 상한 60초).
            #   스냅숏 · 장부 접기 **앞** — 정산이 적는 행(앞 턴 소속)이 이 턴 워터마크 아래로 들어가야 이 턴 `!다시`가 안 지운다.
            try:
                import app_host as _ah_hold
                await _ah_hold.hold_for_settlement(channel_id, message)
            except Exception as _e_hold:
                logger.debug(f"[AppTags] hold skip: {_e_hold}")
            _tt.mark(channel_id, "settle")
            # [2026-10-25 C77 C-2 · 갈래 가] 앞 턴 배경 작업이 아직 돌면 턴 컨텍스트 · 스냅샷 **앞**에서 기다린다(상한 RETRY_SNAPSHOT_BG_WAIT).
            #   [검토] 자리는 `get_domain` · gather_context 앞 — 스냅샷만이 아니라 이 턴이 읽는 컨텍스트도 앞 턴 배경 결과를 본다.
            #   병: 스냅샷 · 워터마크 앞에 배경 대기가 없어(배경 drain 은 Theoria 뒤 3초), 앞 턴 배경 결과가 스냅샷에 빠지고 그 행이
            #   워터마크 위에 쌓였다 → 이 턴을 `!다시` 하면 **살아 있는 앞 턴**의 배경 결과가 복원 · 트림으로 사라졌다.
            #   상한을 넘겨도 끝나지 않으면 이 턴 컨텍스트에 `snapshot_overlap` 을 남겨 `!다시` 가 거절한다(조용히 지우지 않는다).
            _snap_overlap = False
            try:
                from background_task_queue import get_task_queue as _gtq_snap
                _cap_w = float(getattr(config, "RETRY_SNAPSHOT_BG_WAIT", 15.0) or 0.0)
                if _cap_w > 0:
                    _t_w = time.perf_counter()
                    _drained = await _gtq_snap().wait_for_channel(channel_id, timeout=_cap_w)
                    _dt_w = time.perf_counter() - _t_w
                    _snap_overlap = not _drained
                    if _dt_w >= 0.05 or _snap_overlap:
                        logger.info(f"[!다시] snapshot waited {_dt_w:.1f}s for previous-turn background "
                                    f"(drained={_drained})")
            except Exception as _e_bw:
                logger.debug(f"[!다시] snapshot background wait skip: {_e_bw}")
            _tt.mark(channel_id, "bgwait")      # 배경 대기 몫이 gather 로 안 섞이게
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
            _tt.mark(channel_id, "gather")

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
            _tt.mark(channel_id, "chunks")

            # 1.5. NPC decision cooldown tick (매 턴 시작 시 1씩 감소)
            npc_manager.tick_all_cooldowns(channel_id)

            # 2. Cognition Analysis (Theoria 수행)
            # 분석은 이제 process_une_logic 내부의 UNE Theoria에서 수행됩니다.


            # [!다시] 도메인 스냅샷 (UNE 실행 전 전체 상태 저장)
            # [2026-08-12 !다시 유령 정리] SQLite 로그 워터마크 동봉 — 도메인(JSON)만 되돌아가고
            # append 로그(reader_log·dai_logs·emotion_log·…)는 무접촉이라 폐기 턴 행이 유령으로
            # 남았다. 여기서 max(id)를 찍어 두고 retry_last가 복원 직후 초과분을 트림한다.
            # [2026-10-23 C73 M7a] 코드 앱 장부 접기 — 워터마크를 찍기 **앞**(키마다 마지막 행만). 앱이 없으면 no-op.
            try:
                import app_host as _ah_cp
                _ah_cp.compact(channel_id)
            except Exception as _e_cp:
                logger.debug(f"[App] compact skip: {_e_cp}")
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
            # [2026-10-25 C77 C-3 · Q16] 비밀 원장 사본 — 덮어쓰기 표라 워터마크로 못 되감는다(`restore_secrets` 가 통째로).
            _snap_secrets = None
            try:
                import sqlite_store as _ss_sec
                _snap_secrets = _ss_sec.read_secrets(channel_id, include_closed=True, strict=True)   # 실패 = None → 복원 건너뜀
            except Exception as _e_sec:
                logger.debug(f"[!다시] secret ledger snapshot skip: {_e_sec}")
            self._retry_snapshots[channel_id] = {
                "_ts": time.time(),
                "_data": _snap_domain,
                "_marks": _log_marks,
                "_secrets": _snap_secrets,
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
                _marks_disk = dict(_log_marks or {})
                if isinstance(_snap_secrets, list):
                    _marks_disk[sqlite_store.RETRY_SECRETS_KEY] = _snap_secrets   # [C77 C-3] 워터마크 칸 옆에(복원 도메인엔 안 섞임)
                sqlite_store.save_retry_snapshot(channel_id, _snap_turn, _snap_data, marks=_marks_disk)
            except Exception as _e_rs:
                logger.debug(f"[!다시] snapshot persist skipped: {_e_rs}")

            # [2026-10-23 C73 M7a] 코드 앱 턴 앞 — 스냅숏 **뒤**(여기서 쓴 행은 이 턴 소속 → `!다시` 트림이 되감고,
            #   재실행이 다시 부른다) · Theoria 앞. on_input → 쌓인 보고 꺼냄 → feed() → ctx.app_feed_block(Slot 29).
            #   ON_INPUT 정거장(main 입구, C64)은 스냅숏 앞이라 여기를 쓴다. 앱이 없으면 no-op.
            try:
                import app_host as _ah_ts
                await _ah_ts.turn_start(channel_id, ctx)
            except Exception as _e_ts:
                ctx.app_feed_block = ""
                logger.debug(f"[App] turn_start skip: {_e_ts}")

            # async output (typing indicator)
            async with message.channel.typing():
                # [!다시] 마지막 컨텍스트 초기화 (재생성 전)
                current_retry_ctx = {
                    "action_text": ctx.action_text,
                    "user_id": user_id,
                    "original_message_id": message.id,
                    "message_ids": [],
                    "has_response": False,
                    "snapshot_overlap": _snap_overlap,   # [C77 C-2] 앞 턴 배경과 겹친 채 찍힌 스냅샷 → `!다시` 거절
                }

                # 4. UNE Integrated Logic (Batching Process)
                _tt.mark(channel_id, "pre")
                ctx, une_logs, une_directive = await self.process_une_logic(ctx, message)
                _tt.mark(channel_id, "une")
                # [2026-10-27 C84 D4] 모습이 바뀐 턴(개명 · 페이지 · 엣지 이사는 `!다시` 가 되감지 못한다) → `!다시` 거절 칸.
                if isinstance(getattr(ctx, "dai", None), dict) and ctx.dai.get("form_switch"):
                    current_retry_ctx["form_switch"] = list(ctx.dai["form_switch"])
                
                # Log messages (User Facing)
                if une_logs:
                    une_msg = await self._send_with_judgment_embeds(
                        message.channel, "\n".join(une_logs), getattr(ctx, "judgment_embeds", None))
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

                _tt.mark(channel_id, "world")
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

                _tt.mark(channel_id, "expr")
                # 5. Prompt Building
                # UNE directive is already injected into ctx.judgment_context
                full_prompt, builder = self.build_prompt(ctx)
                _tt.mark(channel_id, "prompt")

                # 5.5. [2026-10-17 C46 W1] 도착물 핸드아웃 — 조립 **뒤** · 렌더 앞(전 자리 4.75 = 조립 앞).
                #   조립이 남긴 자료 존 슬롯(`ctx.handout_ref`: PC·NPC/위키·기억·챕터·!룰·직전 꼬리 + 렌더가 고른 로어)을
                #   도착물 콜이 같이 받는다 — 렌더와 같은 세계를 보고 쓴다. 4.7(전이 `deliver` 적재) 뒤인 건 그대로다.
                #   본문은 큐(급식 소비)가 아니라 여기서 이번 턴 문서에 끼운다(`custom_vars.insert_handout`, 자리는
                #   예전과 같은 Slot 29 끝). 유일하게 **산문을 기다리게 하는** 콜(동기 1)이고, 실패하면 핸드아웃 없이
                #   산문이 그대로 나간다.
                ctx.handout_text = ""
                ctx.handout_record = None   # [C49] 표시에 성공한 도착물만 step 8 이 기록한다
                ctx.handout_pending = None  # [C69 A2b] 압축 모드 — 따로 메시지 대신 산문 메시지에 적립할 거리
                # [2026-10-23 C64 M5] 도착물 = 게시판 모듈의 pre_render_call 정거장(world_board.handout_station).
                #   정거장 함수가 프롬프트에 핸드아웃을 끼우고(_pr.prompt) `!다시` 삭제 목록 몫(_pr.message_ids)을 적는다.
                #   게시판이 꺼진 채널이면 틀 토글이 정거장을 건너뛴다(C64 §6-2) — 선언 행은 9.52 flush 가 한 줄 알림으로.
                import module_frame as _mf_pr
                _pr = _mf_pr.PreRenderCall(channel_id=channel_id, ctx=ctx, message=message,
                                           prompt=full_prompt, message_ids=[])
                await _mf_pr.run_station(_mf_pr.PRE_RENDER_CALL, self, _pr)
                full_prompt = _pr.prompt
                current_retry_ctx["message_ids"].extend(_pr.message_ids)
                _tt.mark(channel_id, "handout")

                # 6. Response Generation (V4: returns Tuple[response, extraction_data])
                response, extraction_data = await self.generate_response(ctx, full_prompt)
                _tt.mark(channel_id, "render")

                # [2026-08-12 출력파생 §8] 렌더 실패 안내는 유저에게만 — 산문 파이프라인에서 배제.
                #   구 동작: 안내 문자열이 `if response:`를 통과해 히스토리 적립·검출기·배경 추출
                #   입력까지 오염(§7-11). 여기서 None으로 낮추면 아래 else가 종전 실패 경로를 탄다.
                if persona.is_render_failure(response):
                    logger.warning("[Render] 폴백 안내 반환 — 히스토리·검출기·배경콜 전량 스킵")
                    if getattr(ctx, "handout_pending", None):   # [C69 A2b] 산문이 없으니 적립할 자리도 없다
                        import world_board as _wb_disc
                        _wb_disc.discard_pending_handout(ctx)
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
                    _panel_before = status_panel.turn_view_data(channel_id)   # [C59] 압축/전체 한 자리
                    sent_msgs = await bot_utils.send_long_message(
                        message.channel, _prose_for_display(response, channel_id),
                        view=_tm_view.build_view(channel_id),
                        embeds=status_panel.turn_view_embeds(channel_id, data=_panel_before),
                    )

                    # Store message IDs for retry deletion
                    if sent_msgs:
                        current_retry_ctx["message_ids"].extend([m.id for m in sent_msgs])
                        current_retry_ctx["has_response"] = True
                    _tt.mark(channel_id, "send")
                    _tt.sent(channel_id, getattr(sent_msgs[-1], "id", None) if sent_msgs else None,
                             (ctx.dai or {}).get("turn_index") if isinstance(getattr(ctx, "dai", None), dict) else None)

                    # 7.5 [2026-10-23 C69 A2b] 압축 모드에서 보류한 도착물 → 산문 메시지(임베드가 붙은 마지막 조각)에 적립 +
                    #   C49 원문 묶음(handout_record). 8 이 그 묶음으로 원문 행 · 위키 줄을 적는다. 보류가 없으면 no-op.
                    if getattr(ctx, "handout_pending", None):
                        import world_board as _wb_pend
                        if sent_msgs:
                            await _wb_pend.deliver_pending_handout(ctx, sent_msgs[-1], channel_id)
                        else:
                            _wb_pend.discard_pending_handout(ctx)

                    # 8. [IMPORTANT] 히스토리에 사용자 입력과 AI 응답 저장
                    user_mask = ctx.user_mask or "User"
                    # [2026-10-17 C49 R4] 입력·산문 행에 message_id — 없으면 발효 에피소드 구간(from/to_msg_id)이
                    #   비어 원문 구간 발췌(C46 W4 `read_history_span`)가 라이브에서 안 걸렸다.
                    if record_user_history:
                        domain_manager.append_history(channel_id, user_mask, ctx.action_text,
                                                      message_id=getattr(message, "id", None))
                    # [2026-10-17 C49 R1·R2] 도착물 원문 행 + 위키 줄 — 입력 다음·산문 앞(실제로 일어난 순서).
                    if getattr(ctx, "handout_record", None):
                        try:
                            import world_board as _wb_rec
                            _wb_rec.record_handout(channel_id, ctx.handout_record)
                        except Exception as _e_rec:
                            logger.debug(f"[HandoutRecord] skipped: {_e_rec}")
                    domain_manager.append_history(channel_id, "Model", response,
                                                  message_id=_first_msg_id(sent_msgs))
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

                    # 9. [2026-10-23 C63 M4] 배경 추출 셋(B-Outputs·B-Batch·발효)은 아래 9.55 의 run_station(BACKGROUND)에서
                    #   적재된다(shared_extract · 주인 outputs 2 · world 4 · ferment 6 — 상태패널 10 보다 앞이라 큐 순서 그대로).
                    #   옛 자리(여기)보다 9.52 flush 만큼 늦게 서지만 큐 안 순서는 같다(레티어스 10-23 "시간은 문제 없고").

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

                    # 9.55~9.8 → 모듈 틀(2026-10-22 C58 M1 · 2026-10-23 C63 M4). 배경 추출 셋·상태패널·속마음·지속 감사·Reader-GM(+間 시드)이
                    #   builtin_modules 의 BACKGROUND 정거장에 선다. 순서 = order(2·4·6 · 10·20·30·40) = 옛 9 → 9.55→9.56→9.6→9.7→9.8.
                    #   본문은 글자 그대로 옮겼다(게이트·붙잡는 값·큐 이름·LOW·로그). 새 배경 태스크는 여기 말고
                    #   builtin_modules 에 선언한다. [C74 W4-1] 재그림도 이 정거장 안(order 25)이다 — 아래 주석.
                    import module_frame as _mf
                    # 9.53. [2026-10-23 C73 M7a] AFTER_PROSE 정거장(처음 부르는 자리) — 산문 message id 확정 · 히스토리 적재 뒤.
                    #   지금 선 것 = 코드 앱 호스트(on_output). 동기 · 짧게(앱마다 Lua 한 호출). 배경 적재(9.55)보다 앞.
                    await _mf.run_station(
                        _mf.AFTER_PROSE, self,
                        _mf.AfterProse(channel_id=channel_id, ctx=ctx, response=response, sent_msgs=sent_msgs, message=message))
                    # [2026-10-24 C74 W4-1] 9.9 재그림이 이 정거장 **안**으로 들어갔다(builtin_modules panel_refresh, order 25):
                    #   값 차선(출력물 2 · 세계 4 · 상태패널 10 · 속마음 20) → 재그림 25 → 기억 차선(줄거리 꼬리 27 · 발효 28 ·
                    #   감사 30 · 독자 40). 옛 자리(정거장 뒤 맨 끝)는 임베드가 읽지도 않는 발효 · 감사 · 독자 콜까지 기다렸다.
                    #   새 배경 태스크가 임베드에 보일 값을 쓰면 order 를 25 앞에, 아니면 뒤에 둔다.
                    await _mf.run_station(
                        _mf.BACKGROUND, self,
                        _mf.AfterProse(channel_id=channel_id, ctx=ctx, response=response, sent_msgs=sent_msgs, message=message,
                                       panel_before=_panel_before))
                else:
                    logger.warning(f"[!다시] No response generated for channel {channel_id}")
                    if getattr(ctx, "handout_pending", None):   # [C69 A2b] 렌더 실패 — 보류 도착물 버림
                        import world_board as _wb_disc2
                        _wb_disc2.discard_pending_handout(ctx)
                    if feedback_msg:
                        try:
                            await feedback_msg.delete()
                        except Exception:
                            pass

                # [!다시] 컨텍스트 영구 저장 (응답 성공/실패 여부와 관계없이 유효한 데이터가 있으면 저장)
                if current_retry_ctx.get("has_response"):
                    domain_manager.save_last_execution_context(channel_id, current_retry_ctx)
                    logger.debug(f"[!다시] Persistent context saved for channel {channel_id}")
                _tt.mark(channel_id, "post")
                _tt.finish(channel_id, (ctx.dai or {}).get("turn_index") if isinstance(getattr(ctx, "dai", None), dict) else None)

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

        # [2026-10-25 C77 C-1] 묶음 · 관찰 턴은 되감지 않는다(그 턴은 스냅샷을 안 찍는다).
        if isinstance(last_ctx, dict) and last_ctx.get("kind") in ("batch", "observation"):
            await message.channel.send("⚠️ 묶음 턴(`!턴`) · 관찰 턴은 `!다시`로 되감지 않아요 — 되감으면 그 앞 솔로 턴까지 같이 돌아가요.")
            return False
        if not last_ctx or not last_ctx.get("has_response"):
            await message.channel.send("⚠️ 재시도할 이전 응답이 없거나 이미 처리 중입니다.")
            return False
        # [2026-10-25 C77 C-4 가+ · Q9] 직전 턴 뒤 명령이 턴과 섞인 칸을 바꿨으면 되감지 않는다(되감으면 그 변경이 같이 사라진다).
        _blk = domain_manager.redo_block_reason(channel_id, last_ctx)
        if _blk:
            await message.channel.send(
                f"⚠️ 직전 턴 뒤에 `{_blk}`(으)로 바뀐 게 있어서 이 턴은 `!다시`로 되감지 않아요 — "
                "되감으면 그 변경도 같이 사라져요. 다음 턴부터는 다시 돼요.")
            return False
        # [2026-10-25 C77 C-2] 앞 턴 배경 작업이 끝나기 전에 찍힌 스냅샷 — 되감으면 앞 턴 배경 결과까지 지운다.
        if last_ctx.get("snapshot_overlap"):
            await message.channel.send(
                "⚠️ 이 턴은 앞 턴 배경 정리가 끝나기 전에 시작돼서 `!다시`로 되감지 않아요 — 되감으면 앞 턴 정리분까지 지워져요.")
            return False
        # [2026-10-27 C84 D4] 모습이 바뀐(또는 잠긴 사람이 등장한) 턴 — 이름 · 페이지 · 관계 엣지는 되감기 밖이라 어긋난다.
        if last_ctx.get("form_switch"):
            await message.channel.send(
                "⚠️ 모습이 바뀐 턴은 `!다시`로 되감지 않아요 (" + ", ".join(map(str, last_ctx["form_switch"]))[:80]
                + ") — 되감으면 이름 · 시트 · 관계가 어긋나요.")
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
        # [2026-10-24 C74 판1 검토 #6] 비운 줄거리 꼬리(NarrativeTail)의 볼 것 표도 버린다 — 되감을 턴 소속이다.
        try:
            import shared_extract as _sx_rt
            _sx_rt.forget_tail(channel_id)
        except Exception as _e_ft:
            logger.debug(f"[!다시] tail forget skip: {_e_ft}")
        # [2026-10-24 C73 M7b] 되감을 턴의 코드 앱 신고 정산을 버린다(재실행 산문이 다시 신고한다) — 복원 · 트림 **앞**.
        try:
            import app_host as _ah_drop
            await _ah_drop.drop_settlement(channel_id)
        except Exception as _e_drop:
            logger.debug(f"[AppTags] drop skip: {_e_drop}")

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
            # [2026-10-23 C72 §5] 턴이 안 만드는 설정(선언 층 · 모듈 켬끔 · 게시판 설정)은 되감기 전에 떠 뒀다가 복원 직후 덮는다.
            try:
                _carry = domain_manager.redo_carry(channel_id)
            except Exception as _e_cy:
                logger.warning(f"[!다시] 설정 넘겨 얹기 뜨기 실패(무시 — 옛 동작으로 되감긴다): {_e_cy}")
                _carry = None
            domain_manager.save_domain(channel_id, copy.deepcopy(snapshot))
            logger.info(f"[!다시] Domain snapshot restored for {channel_id}")
            if _carry is not None:
                try:
                    domain_manager.apply_redo_carry(channel_id, _carry)
                    logger.info("[!다시] 설정 넘겨 얹음 — 선언 층 · 모듈 켬끔 · 게시판 설정")
                except Exception as _e_cy2:
                    logger.warning(f"[!다시] 설정 넘겨 얹기 실패(무시): {_e_cy2}")
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
            # [2026-10-25 C77 C-3 · Q16] 비밀 원장 → 턴 시작 모양(공개 처리 · turn_count · 그 턴에 선 행까지). 옛 스냅샷(사본 없음)은 손 안 댐.
            try:
                import sqlite_store as _ss_sr
                _sec_rows = (snapshot_entry or {}).get("_secrets") if snapshot_entry and snapshot_entry.get("_data") is snapshot \
                    else _ss_sr.read_retry_secrets(channel_id)
                if isinstance(_sec_rows, list):
                    if _ss_sr.restore_secrets(channel_id, _sec_rows):
                        logger.info(f"[!다시] secret ledger restored ({len(_sec_rows)} rows)")
            except Exception as _e_sr:
                logger.warning(f"[!다시] 비밀 원장 되감기 실패(무시): {_e_sr}")
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
            # [2026-10-23 C60 M2c §6.1] Activity 편집 일지 — 복원(+트림·절 되감기) **직후**, 재실행 **앞**.
            #   되돌린 지점 뒤에 화면에서 고친 값이 다시 얹혀 그 턴의 세계가 된다(레티어스 "되감기는 편집은 살리게").
            try:
                import activity_edit as _ae_rp
                _ae_rp.replay_after(channel_id, int((snapshot.get("world_state", {}) or {}).get("turn_index", 0) or 0))
            except Exception as _e_rp:
                logger.debug(f"[!다시] activity edit replay skipped: {_e_rp}")
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
            import turn_timing as _tt   # [2026-10-24 C74 W0] 턴 시계(execute 와 같은 줄, 단계는 굵게)
            _tt.begin(channel_id, "batch")
            # [2026-10-25 C77 C-1] 묶음 턴은 `!다시` 스냅샷을 안 찍는다 — 직전 솔로 턴 컨텍스트가 남아 있으면 그 뒤 `!다시` 가
            #   솔로 턴 시작으로 되감아 이 묶음 턴(과 그 사이 일)을 지웠다. 컨텍스트를 "되감지 않는 턴"으로 갈아 둔다.
            domain_manager.save_last_execution_context(channel_id, {"kind": "batch", "has_response": False, "message_ids": []})
            try:   # [2026-10-24 C73 M7b] 앞 턴 코드 앱 신고 정산 대기(execute 와 같은 문)
                import app_host as _ah_hold
                await _ah_hold.hold_for_settlement(channel_id, message)
            except Exception as _e_hold:
                logger.debug(f"[AppTags] hold skip: {_e_hold}")
            _tt.mark(channel_id, "settle")
            async with message.channel.typing():
                # 1. UNE 배치 실행
                result = await self.une.run_batch(channel_id, pending_actions)
                directive = result["directive"]
                system_log = result["system_message"]
                updated_context = result["game_context"]

                # 2. 시스템 메시지 출력 (판정/이변/멘탈 결과) — [2026-10-15 C42] PC별 판정 임베드 동봉
                if system_log:
                    await self._send_with_judgment_embeds(
                        message.channel, system_log, result.get("judgment_embeds"))

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
                _tt.mark(channel_id, "une")
                full_prompt, _ = self.build_prompt(ctx)
                _tt.mark(channel_id, "prompt")
                response, _ = await self.generate_response(ctx, full_prompt)
                _tt.mark(channel_id, "render")

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
                    _panel_before = status_panel.turn_view_data(channel_id)   # [C59] 압축/전체 한 자리
                    sent_msgs = await bot_utils.send_long_message(
                        message.channel, _prose_for_display(response, channel_id),
                        embeds=status_panel.turn_view_embeds(channel_id, data=_panel_before),
                    )
                    # 히스토리: PC 행동은 이미 waiting 모드에서 저장됨, Model 응답만 추가
                    domain_manager.append_history(channel_id, "Model", response,
                                                  message_id=_first_msg_id(sent_msgs))   # [C49 R4]

                    # Background Extraction (첫 PC 기준)
                    import shared_extract as _sx_p   # [C63 M4] 옛 self.schedule_background_extraction — 추출 셋만(옛 그대로)
                    _tt.mark(channel_id, "send")
                    _tt.sent(channel_id, getattr(sent_msgs[-1], "id", None) if sent_msgs else None,
                             (ctx.dai or {}).get("turn_index") if isinstance(getattr(ctx, "dai", None), dict) else None)
                    # [2026-10-24 C74 W4-1] 재그림은 값 차선(출력물 · 세계 추출) 뒤 · 기억 차선(줄거리 꼬리 · 발효) 앞 —
                    #   schedule_extraction 이 그 순서로 한 번에 적재한다(옛: 추출 셋 → 재그림, 발효가 재그림 앞).
                    await _sx_p.schedule_extraction(self, ctx, response, message,
                                                    sent_msgs=sent_msgs, panel_before=_panel_before)
                _tt.mark(channel_id, "post")
                _tt.finish(channel_id, (ctx.dai or {}).get("turn_index") if isinstance(getattr(ctx, "dai", None), dict) else None)

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
            import turn_timing as _tt   # [2026-10-24 C74 W0] 턴 시계(execute 와 같은 줄, 단계는 굵게)
            _tt.begin(channel_id, "obs")
            # [2026-10-25 C77 C-1] 관찰 턴도 같다(위 묶음 턴 주석).
            domain_manager.save_last_execution_context(channel_id, {"kind": "observation", "has_response": False, "message_ids": []})
            try:   # [2026-10-24 C73 M7b] 앞 턴 코드 앱 신고 정산 대기(execute 와 같은 문)
                import app_host as _ah_hold
                await _ah_hold.hold_for_settlement(channel_id, message)
            except Exception as _e_hold:
                logger.debug(f"[AppTags] hold skip: {_e_hold}")
            _tt.mark(channel_id, "settle")
            async with message.channel.typing():
                # 1. UNE 관찰 실행
                result = await self.une.run_observation(channel_id)
                directive = result["directive"]
                system_log = result["system_message"]
                updated_context = result["game_context"]

                if system_log:
                    await self._send_with_judgment_embeds(
                        message.channel, system_log, result.get("judgment_embeds"))

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
                _tt.mark(channel_id, "une")
                full_prompt, _ = self.build_prompt(ctx)
                _tt.mark(channel_id, "prompt")
                response, _ = await self.generate_response(ctx, full_prompt)
                _tt.mark(channel_id, "render")

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
                    _panel_before = status_panel.turn_view_data(channel_id)   # [C59] 압축/전체 한 자리
                    sent_msgs = await bot_utils.send_long_message(
                        message.channel, _prose_for_display(response, channel_id),
                        embeds=status_panel.turn_view_embeds(channel_id, data=_panel_before),
                    )
                    domain_manager.append_history(channel_id, "관찰", "[관찰 모드]",
                                                  message_id=getattr(message, "id", None))   # [C49 R4]
                    domain_manager.append_history(channel_id, "Model", response,
                                                  message_id=_first_msg_id(sent_msgs))

                    import shared_extract as _sx_p   # [C63 M4] 옛 self.schedule_background_extraction — 추출 셋만(옛 그대로)
                    _tt.mark(channel_id, "send")
                    _tt.sent(channel_id, getattr(sent_msgs[-1], "id", None) if sent_msgs else None,
                             (ctx.dai or {}).get("turn_index") if isinstance(getattr(ctx, "dai", None), dict) else None)
                    # [2026-10-24 C74 W4-1] 재그림은 값 차선(출력물 · 세계 추출) 뒤 · 기억 차선(줄거리 꼬리 · 발효) 앞 —
                    #   schedule_extraction 이 그 순서로 한 번에 적재한다(옛: 추출 셋 → 재그림, 발효가 재그림 앞).
                    await _sx_p.schedule_extraction(self, ctx, response, message,
                                                    sent_msgs=sent_msgs, panel_before=_panel_before)
                _tt.mark(channel_id, "post")
                _tt.finish(channel_id, (ctx.dai or {}).get("turn_index") if isinstance(getattr(ctx, "dai", None), dict) else None)

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
