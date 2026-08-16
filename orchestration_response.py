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
import bot_utils  # [2026-08-03] vlog — 전문 로그 채널
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
        Tuple[str, None]: (프롬프트, None)
        두 번째 반환값은 레거시 호환성을 위해 유지되며 항상 None입니다.
        [2026-08-12 조립 3분할] 첫 값은 Gemini 경로=단일 문자열,
        openai 경로=(system_rules, context_data, now_doc) 3튜플이다.
    """
    logger.info("[V3] Building 34-Step Slot Prompt")
    v3_prompt = slot_manager.build_34_step_prompt(ctx)
    return v3_prompt, None


_TELESCOPE_BLOCK_PATTERNS = (
    r"┣[\s\S]*?┫",
    r"<TELESCOPE>[\s\S]*?</TELESCOPE>",
    r"```telescope[\s\S]*?```",
    r"<<TELESCOPE[\s\S]*?TELESCOPE>>",
    r"<<[\s\S]*?>>",
)

# 5W1H 게이트명 (개별 라인 감지용) + legacy 호환
_TELESCOPE_GATE_NAMES = (
    # V4 Layer 1 (The Real)
    "Field", "Probe",
    # V4 Layer 2 (The Symbolic)
    "Scene", "Scene.Who", "Scene.When/Where", "Scene.Stance", "Scene.Axioms", "Scene.What", "Scene.Causal", "Scene.Chain",
    "Character", "Char.Why", "Char.PC", "Char.Pidgin", "Char.Rift",
    "Craft", "Craft.Spent", "Craft.Cargo", "Craft.Rhythm", "Craft.Attractor", "Craft.Scheme", "Craft.Echo",
    "Collision", "Gravity", "Alignment", "Alignment.Silenced", "Vending", "Unshown", "Final", "Scope",
    # V4 Adversarial
    "C",
    # V5 (2026-07-22) Author's Landing Note — 신규 필드명 (기존과 겹치는 Field/Scene/Gravity/Unshown/Scope는 위에 존재)
    "Ground", "Voice", "Pull", "Spent", "Echo", "Punctum",
    # V2 legacy
    "Who", "When", "Where", "When/Where", "What", "Why", "How",
    # V1 legacy
    "Physics", "Camera", "Cliche", "Hook", "Impersonation",
    "Spatial", "NPC Identity", "CharReason", "TheoryAlign", "GenreCoherence",
)
_GATE_LINE_RE = re.compile(
    r"^\[(?:" + "|".join(re.escape(g) for g in _TELESCOPE_GATE_NAMES)
    + r")\]\s+.*$",
    re.MULTILINE | re.IGNORECASE,
)


def _extract_telescope_block(text: str) -> Optional[str]:
    """블록 패턴 매칭 (파싱용). 전체 텍스트 검색."""
    if not text:
        return None
    for pattern in _TELESCOPE_BLOCK_PATTERNS:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            return match.group(0)
    return None


def has_telescope_content(text: str) -> bool:
    """텔레스코프 잔존 여부 감지 (블록 + 개별 게이트 라인 + 고아 마커)."""
    if not text:
        return False
    if _extract_telescope_block(text):
        return True
    if _GATE_LINE_RE.search(text):
        return True
    if "┣" in text or "┫" in text:
        return True
    return False


def parse_telescope(raw_response: str) -> Dict[str, Any]:
    """Parse a 5W1H telescope reasoning block from model output.

    V3: PASS/FAIL verdict 대신 reasoning content를 추출.
    하위호환: 옛 PASS/FAIL 형식도 파싱 가능.
    """
    block = _extract_telescope_block(raw_response or "")
    if not block:
        return {"parsed": False, "gates": {}, "reasoning_count": 0}

    # V3: [GateName] 이후 내용을 다음 게이트 또는 블록 끝까지 추출
    gate_pattern = re.compile(
        r"\[([^\]]+)\]\s*(.*?)(?=(?:\n\[[^\]]+\])|\Z)",
        flags=re.DOTALL,
    )
    gates: Dict[str, Dict[str, str]] = {}

    for gate_name, content in gate_pattern.findall(block):
        normalized = re.sub(r"[^a-z0-9]+", "_", gate_name.strip().lower()).strip("_")
        raw = content.strip()
        # 하위호환: 옛 PASS/FAIL 형식 감지
        legacy_match = re.match(r"^(PASS|FAIL)\s*:\s*(.*)", raw, re.DOTALL | re.IGNORECASE)
        if legacy_match:
            gates[normalized] = {
                "result": legacy_match.group(1).upper(),
                "reasoning": legacy_match.group(2).strip(),
            }
        else:
            gates[normalized] = {"reasoning": raw}

    return {
        "parsed": True,
        "gates": gates,
        "reasoning_count": len(gates),
    }


def _check_length(text: str, limit: int) -> str:
    """응답이 limit의 130%를 초과할 때만 경고 로그. 원문은 항상 그대로 반환."""
    if not text:
        return text
    if len(text) > int(limit * 1.3):
        logger.warning("[Length Over] %d chars (limit %d, +%d%% over)", len(text), limit, int((len(text) - limit) / limit * 100))
    return text


def strip_telescope(raw_response: str) -> str:
    """3-레이어 텔레스코프 제거.

    Layer 1: 블록 패턴 (┣...┫, <TELESCOPE>, 등) — 전체 텍스트
    Layer 2: 개별 게이트 라인 ([Physics] PASS: ... 등)
    Layer 3: 고아 마커 (┣, ┫)
    """
    if not raw_response:
        return ""
    text = raw_response

    # 격랑식 경계: ┫(텔레스코프 종료 마커)가 있으면 그 *마지막* 이후가 산문.
    # → ┣ 앞 네이티브 thinking(추론 ON 시 inline 누출) + ┣…┫ 블록을 한 번에 제외.
    if "┫" in text:
        text = text.rsplit("┫", 1)[-1]

    # Layer 1: 블록 단위 제거 (전체 텍스트에서 반복)
    for pattern in _TELESCOPE_BLOCK_PATTERNS:
        text = re.sub(pattern, "", text, flags=re.IGNORECASE)

    # Layer 2: 래퍼 없는 개별 게이트 라인 제거
    text = _GATE_LINE_RE.sub("", text)

    # Layer 3: 고아 마커 제거
    text = text.replace("┣", "").replace("┫", "")

    # 제거로 인한 연속 빈 줄 정리
    text = re.sub(r"\n{3,}", "\n\n", text)

    return text.strip()


# [2026-08-11 루프차단기 보강 / 08-12 소스 교체] 직전 턴 "이미 렌더된 것" 메모 조립.
# 재료 = telescope_logs 적립분(콜 0)의 v5 착지 노트 [Scene](+[Spent]) — 영어락 메타 레지스터라
# assistant 메시지에 섞여도 "모델의 산문 문체"로 오인·모방될 위험이 없다. 캡은 구 brief와 동일 300.
_RENDERED_NOTE_PREFIX = "[the turn already rendered: "
_RENDERED_NOTE_CAP = 300
# raw 블록이 넘어와도 dict와 같은 결과가 나오게 하는 필드 파서(정규식 1개, 두 필드 동시).
_RENDERED_NOTE_FIELD_RE = re.compile(
    r"^\[(Scene|Spent)\]\s*(.*?)(?=\n\s*\[[^\]]+\]|\Z)",
    re.MULTILINE | re.DOTALL | re.IGNORECASE,
)


def _build_rendered_note(tele_entry) -> str:
    """직전 턴 텔레스코프 노트 → 메타 한 줄. I/O 없음(호출부가 로그를 읽어 넘긴다).

    입력: save_telescope_log가 적립한 parse_telescope 결과 dict(gates 정규화 키) 또는 raw 블록 문자열.
    반환: "[the turn already rendered: ...]" / 재료 없으면 "" (호출부는 앵커 단독으로 폴백).
    """
    vals = {"scene": "", "spent": ""}
    if isinstance(tele_entry, dict):
        gates = tele_entry.get("gates") or {}
        if not isinstance(gates, dict):
            return ""
        # v4 롤백본 방어: v4의 [Scene]은 하위필드 헤더라 내용이 아니다 → 통째로 침묵(안전 no-op).
        if any(k in gates for k in ("scene_who", "craft_spent")):
            return ""
        for key in vals:
            g = gates.get(key)
            if isinstance(g, dict):
                vals[key] = str(g.get("reasoning") or "")
            elif isinstance(g, str):
                vals[key] = g
    elif isinstance(tele_entry, str):
        for _name, _body in _RENDERED_NOTE_FIELD_RE.findall(tele_entry):
            vals[_name.lower()] = _body
    else:
        return ""

    parts: List[str] = []
    for key, label in (("scene", ""), ("spent", "spent: ")):
        # 메타 한 줄 유지: 줄바꿈 접기 + 브래킷 문자 제거(프레임 조기 종료 방지) + 씨앗 마커/구두점 정리
        v = re.sub(r"[\[\]]", "", re.sub(r"\s+", " ", vals[key])).strip().strip("*★:;,. ").strip()
        if not v or v.lower() in ("none", "n/a", "-"):
            continue
        parts.append(f"{label}{v}")
    if not parts:
        return ""
    return f"{_RENDERED_NOTE_PREFIX}{'; '.join(parts)[:_RENDERED_NOTE_CAP].strip()}]"


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

    [V4 Update - Inline Extraction] → ⚰[2026-08-12 출력파생 §8] 파서 삭제
    - Returns: (narrative_text, extraction_data) — 튜플 형태만 유지, extraction_data는 상시 None
    """
    p_name = ctx.player_data.get("mask", "Unknown") if ctx.player_data else "Unknown"

    # 참여 인원 수 (동적 서사 길이 기준)
    participants = ctx.domain_data.get("participants", {})
    active_player_count = max(1, sum(1 for p in participants.values() if p.get("status") == "active"))

    # [V3] 이미 생성된 34단계 프롬프트를 직접 전달
    # OpenAI 백엔드: (system_rules, context_data, now_doc) 3튜플 → system/user 분리 주입
    # [2026-08-12 조립 3분할] now_doc(THIS TURN)은 여기서 쓰지 않는다 — 히스토리 **뒤**,
    #   최종 user 메시지로 나가야 하므로 아래 _user_input이 받는다.
    _now_doc = ""
    if isinstance(prompt, tuple):
        system_rules, context_data, _now_doc = prompt
        session = persona.create_risu_style_session(
            client=client,
            model_version=model_id,
            system_prompt=system_rules
        )
        # 데이터 슬롯을 user 메시지로 주입 (NPC 시트/로어/분석 = 참조 데이터)
        # [2026-07-08 탈부정] 부정-구문 미러링 관측 → 헤더·ack 긍정형으로 (reference 마킹 기능 보존)
        # [2026-08-12 조립 3분할] 구 헤더 1줄("[CONTEXT DATA: reference material only]")은 존 라벨
        #   _ZONE_LABEL_CONTEXT로 승격돼 context_data 머리에 이미 붙어 있다(계승·강화, 이중 0).
        session.history.append({"role": "user", "content": context_data})
        # [2026-07-07 인격대우 1단계] 매 턴 진짜 assistant 턴 = 자기발화 각인력 최강 채널.
        # [2026-08-12 조립 3분할] ack가 구 2존 구조("받았다 → 바로 쓴다")를 전제하던 것 최소 수정 —
        #   이 시점 뒤에 히스토리와 THIS TURN 문서가 더 온다.
        session.history.append({"role": "assistant", "content": "Received, and held as reference. Good material in these records; ready for this turn's document."})
    else:
        session = persona.create_risu_style_session(
            client=client,
            model_version=model_id,
            system_prompt=prompt
        )

    # 히스토리 주입
    # [Anti-Gravity] Use Smart Context Window
    history_to_inject = ctx.smart_history if ctx.smart_history else ctx.domain_data.get('history', [])
    is_openai = isinstance(session, persona.OpenAIChatSessionAdapter)
    # [Em-dash 감축] 모델 자기 과거 출력에서 엠대쉬를 줄여 미러링 루프 차단 (격랑 이식).
    # 유저 입력(role=User)은 보존, assistant/model 콘텐츠에만 적용.
    # [2026-07-08 루프-차단기 확장] 진짜 recency의 raw 산문 = 히스토리 '마지막' assistant 메시지
    # (S31 꼬리는 컨텍스트 블록이라 히스토리보다 앞 — 메시지 순서 실측). 저며진 직전 응답은 주입본에서
    # 대사-앵커로 교체. 저장본 무손상(세션 매턴 재생성, 주입본만 큐레이팅 — 엠대쉬 스크럽과 동일 원리).
    # 마지막 1개만: 옛 턴은 영향 감쇠 + 연속성 보존. 대사가 0이면 raw 유지(히스토리 통짜 제거는 과격).
    # [2026-07-22 카드2] 반복 문장 스크럽: 직전 턴에 verbatim 재발한 문장을 주입본에서 제거.
    # 넛지(CADENCE_ECHO_INJECT)는 "말리기"였고 이건 "모방 대상 제거" — 실례가 남아 있으면 넛지가 진다.
    from response_processor import (reduce_emdashes, analyze_slicing_structure,
                                    extract_dialogue_anchor, scrub_echo_sentences)
    _echo_scrub: list = []
    if getattr(config, "ECHO_SCRUB", True):
        try:
            _echo_scrub = domain_manager.get_session_ai_memory(
                getattr(ctx, "channel_id", "") or ""
            ).get("echo_scrub_sents", []) or []
            if not isinstance(_echo_scrub, list):
                _echo_scrub = []
        except Exception:
            _echo_scrub = []
    # [2026-08-11 루프차단기 보강 / 2026-08-12 소스 교체] 앵커 교체의 원 설계는 "사건·목소리 연속성 유지"인데,
    #   대사만 남기면 **사건이 통째로 빠진다** — 모델이 직전 턴에 뭘 렌더했는지 잃고 이미 묘사한 사건을 다시 묘사.
    #   초판 소스 turn_log.ai_brief는 실측상 **직전 산문 머리 300자 원문**(요약 아님, orchestration.py record_turn)
    #   → 산문 원문 재진입 부류라 폐기(산문 직행은 히스토리·발효 대사 인용만).
    #   교체 소스 = 직전 턴 텔레스코프 v5 착지 노트의 [Scene](+[Spent]), 콜 0(telescope_logs 적립분 재사용):
    #   ①영어락 메타 레지스터라 "모델의 산문 문체"로 오인·모방될 위험이 원천 소거(구 판의 '포장' 문제가 사라짐)
    #   ②[Spent]="이번 턴 소진한 카드"라 그 자체가 반-반복 재료.
    #   엠대쉬 감축·에코 스크럽은 히스토리 본문과 동일 규율 적용, 앵커는 뒤에 둬 recency 자리 유지.
    #   노트가 없으면(로그 없음/필드 none) 기존 동작(앵커 단독) 그대로 — no-op 폴백.
    _prev_note = ""
    try:
        _note_ch = getattr(ctx, "channel_id", "") or ""
        if _note_ch:
            _tele_logs = domain_manager.get_telescope_logs(_note_ch, 1)
            if _tele_logs:
                _prev_note = _build_rendered_note(_tele_logs[-1])
                if _prev_note:
                    _prev_note = reduce_emdashes(_prev_note)
                    if _echo_scrub:
                        _prev_note, _ = scrub_echo_sentences(_prev_note, _echo_scrub)
                    _prev_note = _prev_note.strip()
                    # 스크럽이 알맹이를 다 걷어냈으면 라벨만 남기지 않는다
                    if len(_prev_note) <= len(_RENDERED_NOTE_PREFIX) + 1:
                        _prev_note = ""
    except Exception:
        _prev_note = ""
    _note_tag = "note=none"
    if _prev_note:
        _note_body = _prev_note[len(_RENDERED_NOTE_PREFIX):]
        _note_tag = ("+spent" if _note_body.startswith("spent: ")
                     else "+scene+spent" if "; spent: " in _note_body else "+scene")
    _scrub_total = 0
    # 첫 등장 보존용 — 히스토리는 오래된 순이므로 최초 인스턴스는 남고 이후 재발분만 빠진다
    # (지시대상이 그 문장으로만 언급된 경우 대상 자체가 사라지는 것 방지).
    _echo_seen: set = set()
    _last_asst_idx = -1
    for _i in range(len(history_to_inject) - 1, -1, -1):
        if history_to_inject[_i]['role'] != "User":
            _last_asst_idx = _i
            break
    for _idx, h in enumerate(history_to_inject):
        _content = str(h['content'])
        _is_user = h['role'] == "User"
        if not _is_user:
            _content = reduce_emdashes(_content)
            if _echo_scrub:
                _content, _n_scrub = scrub_echo_sentences(
                    _content, _echo_scrub, already_seen=_echo_seen)
                _scrub_total += _n_scrub
            if _idx == _last_asst_idx:
                try:
                    _sl = analyze_slicing_structure(_content[-800:])  # 판정은 꼬리 기준(저미기는 꼬리 지배)
                    if _sl["flagged"]:
                        _anchor = extract_dialogue_anchor(_content)
                        if _anchor:
                            # [2026-08-11 루프차단기 보강] 사건 연속성 동봉 — 노트 먼저, 앵커가 끝(recency)
                            if _prev_note:
                                _anchor = f"{_prev_note}\n{_anchor}"
                            logging.info(f"[loop-breaker] history last-assistant sliced "
                                         f"(conn={_sl['conn_density']} avg={_sl['avg_len']}) → dialogue anchor ({len(_anchor)}자"
                                         f", {_note_tag})")
                            _content = _anchor
                except Exception:
                    pass
        if is_openai:
            session.history.append({"role": "user" if _is_user else "assistant", "content": _content})
        else:
            session.history.append(types.Content(role="user" if _is_user else "model", parts=[types.Part(text=_content)]))

    if _scrub_total:
        logging.info(f"[EchoScrub] history: {_scrub_total} sentence(s) removed from injection copy")

    # [Anti-Gravity] PC 사칭 탐지 및 BKSPC 처리가 통합된 생성 함수 호출
    # 사칭 감지 토글 확인 (기본값: 활성화)
    impersonation_enabled = ctx.domain_data.get("settings", {}).get("impersonation_filter", True)
    pc_names_for_filter = [p_name] if impersonation_enabled else []
    # Telescope V2: ctx에 저장된 프리필을 모델 응답 시작으로 전달 (스킵 불가)
    _tele_prefill = getattr(ctx, 'telescope_prefill_text', '')
    # [2026-08-12 조립 3분할] ★유저 입력 이중 사본 제거.
    #   구 동작: `"\n\n".join(prompt)` = system_rules + context_data를 최종 user 메시지로 **통째 재전송**
    #   → 룰·자료 전량이 2회, 그 안에 든 유저 입력(S32)도 2회 도착했다("Gemini 호환" 주석이 남긴 잔재.
    #   Gemini 경로는 애초에 튜플을 안 받는다 — build()가 단일 문자열).
    #   현행: openai면 THIS TURN 문서만 최종 메시지로 나간다(유저 입력은 그 안 S32에 1회).
    #   persona가 이 문자열 꼬리에 hidden_reminder를 붙이는 흐름은 불변.
    #   ⚠이 값은 filter_pc_impersonation의 출처 판정 입력이기도 하다 — 유저 입력이 안에 있어야 하며,
    #     THIS TURN 문서는 S32 <User_Input>을 포함하므로 판정 재료는 보존된다(노이즈만 감소).
    _user_input = _now_doc if isinstance(prompt, tuple) else prompt
    response = await persona.generate_response_with_retry(
        client, session, _user_input,
        pc_names=pc_names_for_filter,
        player_count=active_player_count,
        telescope_prefill=_tele_prefill,
        scene_energy=getattr(ctx, 'scene_energy', 'idle')
    )

    # 정리 (System Update & Telescope Logic Block)
    extraction_data = None

    if response:
        # 1. system_update 블록 제거
        response = re.sub(r'```system_update[\s\S]*?```', '', response, flags=re.IGNORECASE).strip()

        # 2. [Telescope] ┣┫ CoT block strip (품질 게이트 출력 제거 — 플레이어에겐 비공개)
        raw_block = _extract_telescope_block(response)
        # [Reader-GM Stage 0] 독자 콜 입력용 원문 블록 스태시 (스트립 전 보존 — 독자는 "페이지에 보이는 것"만 읽음)
        try:
            ctx.telescope_raw_block = raw_block or ""
        except Exception:
            pass
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
            # 원문 블록 전체 로그 (서버 로그에서 CoT 내용 확인용)
            # [2026-08-03] verbose 채널 이관 검토 → **journal 유지로 결정**(레티어스).
            #   텔레스코프는 매 턴 흐름을 눈으로 훑는 체크 용도라 journal에 있는 게 맞다.
            #   verbose로 뺄 대상은 "가끔 파고들 때만 필요한 전문"(리더 다이제스트 등).
            if raw_block:
                logger.info("[Telescope RAW]\n%s", raw_block)
            # [2026-08-12 출력파생 §8] legacy FAIL 게이트 스캔 삭제 — v5 "착지 노트"엔 result 필드가
            #   없어(게이트=PASS/FAIL 판정이 아니라 11필드 노트) 상시 공집합이던 v4 잔재.
            logger.info("[Telescope] Parsed OK — %d gates", len(gates))
        else:
            # 모델이 텔레스코프 블록을 안 쓴 건지 진단
            if raw_block:
                logger.warning("[Telescope] Block found but gate parse FAILED:\n%s", raw_block[:500])
            else:
                has_markers = "┣" in response or "┫" in response
                has_bracket_gates = bool(re.search(r"\[(Who|What|Why|How|When|Where)\]", response, re.IGNORECASE))
                if has_markers or has_bracket_gates:
                    # 마커는 있는데 블록 추출 실패
                    snippet = ""
                    idx = response.find("┣")
                    if idx >= 0:
                        snippet = response[idx:idx+300]
                    logger.warning("[Telescope] Partial markers found but no valid block. Snippet:\n%s", snippet)
                else:
                    logger.warning("[Telescope] No telescope block in model output — model skipped CoT")
        # 2b. 잔존 텔레스코프 안전망 (블록 파싱 실패해도 개별 게이트 라인/마커 제거)
        if has_telescope_content(response):
            logger.warning("[Telescope] 블록 스트립 후에도 잔존 감지 → 3-레이어 재스트립")
            response = strip_telescope(response)
        # 3. ⚰[2026-08-12 출력파생 §8] SYS_EXTRACT 인라인 파서 삭제 — 이중 사문이었다:
        #    출력을 지시하는 프롬프트 0 + 파싱 결과(extraction_data) 소비자 0(호출부에서 받기만 함).
        #    [NARRATIVE] 태그 제거도 이 블록 안에 중첩돼 있어 함께 사문 → 같이 삭제.
        #    반환 시그니처 (response, extraction_data)는 유지, 값은 None 고정.

    # [Anti-Gravity] Mob Tag Cleaning (System Level)
    if response:
        from response_processor import clean_mob_tags
        response = clean_mob_tags(response)

    # [2026-08-16 상태창 코드 조립] 렌더가 관성으로 그린 상태줄을 머리에서 제거.
    #   헤더는 표시 계층에서 코드가 붙이므로 여기 남으면 이중 표기 + 히스토리 에코 소스가 된다.
    #   여기가 세 실행 경로(execute/batch/observation)의 단일 합류점이라 1겹으로 충분하다.
    if response:
        from response_processor import strip_status_header
        _pre_len = len(response)
        response = strip_status_header(response)
        if len(response) != _pre_len:
            logger.info("[StatusHeader] 렌더 상태줄 %d자 제거 (구 계약 관성)", _pre_len - len(response))

    # 4. 서사 길이 체크 (인원 기반 — 경고만, 강제 절단 없음)
    if response:
        char_limit = config.get_narrative_char_limit(active_player_count)
        response = _check_length(response, char_limit)

    return response, extraction_data
