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
    # OpenAI 백엔드: (system_rules, context_data) 튜플 → system/user 분리 주입
    if isinstance(prompt, tuple):
        system_rules, context_data = prompt
        session = persona.create_risu_style_session(
            client=client,
            model_version=model_id,
            system_prompt=system_rules
        )
        # 데이터 슬롯을 user 메시지로 주입 (NPC 시트/로어/분석 = 참조 데이터)
        # [2026-07-08 탈부정] 부정-구문 미러링 관측 → 헤더·ack 긍정형으로 (reference 마킹 기능 보존)
        session.history.append({"role": "user", "content": f"[CONTEXT DATA: reference material only]\n{context_data}"})
        # [2026-07-07 인격대우 1단계] 매 턴 진짜 assistant 턴 = 자기발화 각인력 최강 채널.
        session.history.append({"role": "assistant", "content": "Received, and held as reference. Good material in these records; I'm ready to write."})
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
    from response_processor import reduce_emdashes, analyze_slicing_structure, extract_dialogue_anchor
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
            if _idx == _last_asst_idx:
                try:
                    _sl = analyze_slicing_structure(_content[-800:])  # 판정은 꼬리 기준(저미기는 꼬리 지배)
                    if _sl["flagged"]:
                        _anchor = extract_dialogue_anchor(_content)
                        if _anchor:
                            logging.info(f"[loop-breaker] history last-assistant sliced "
                                         f"(conn={_sl['conn_density']} avg={_sl['avg_len']}) → dialogue anchor ({len(_anchor)}자)")
                            _content = _anchor
                except Exception:
                    pass
        if is_openai:
            session.history.append({"role": "user" if _is_user else "assistant", "content": _content})
        else:
            session.history.append(types.Content(role="user" if _is_user else "model", parts=[types.Part(text=_content)]))

    # [Anti-Gravity] PC 사칭 탐지 및 BKSPC 처리가 통합된 생성 함수 호출
    # 사칭 감지 토글 확인 (기본값: 활성화)
    impersonation_enabled = ctx.domain_data.get("settings", {}).get("impersonation_filter", True)
    pc_names_for_filter = [p_name] if impersonation_enabled else []
    # Telescope V2: ctx에 저장된 프리필을 모델 응답 시작으로 전달 (스킵 불가)
    _tele_prefill = getattr(ctx, 'telescope_prefill_text', '')
    # user_input: 튜플이면 원본 전체 프롬프트 재조립 (Gemini 호환), 문자열이면 그대로
    _user_input = "\n\n".join(prompt) if isinstance(prompt, tuple) else prompt
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
            if raw_block:
                logger.info("[Telescope RAW]\n%s", raw_block)
            # [2026-04-28] gate별 200자 cap 출력 제거 — RAW 블록과 중복 + 짤림 노이즈.
            # 검증 단계 종료 + 휘발 자세. FAIL gate만 warning으로 잡아 알림 유지.
            fail_gates = [
                (name, g.get("reasoning", "").strip())
                for name, g in gates.items()
                if g.get("result") == "FAIL"
            ]
            for name, reasoning in fail_gates:
                logger.warning("[Telescope FAIL] %-12s %s", name, reasoning[:200] if reasoning else "(empty)")
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
