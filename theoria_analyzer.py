"""
Lorekeeper UNE - Integrated Theoria Analyzer (좌뇌 분석 엔진)
인지 + 분석 통합: 상황 관찰, 의도 해석, Position/Effect, Psyche, Narrative Chain

[2026-09-06 P8b] 출력 스키마에서 두 필드를 **삭제**했다 — WHY:
  - `mental_impact` (applicable/vigor_severity/composure_severity): 장면을 severity 로 분류해
    두 축의 숫자를 정하던 자리. 값의 변화는 이제 전담 추출 콜의 `deltas` 가 선언 rule 을 보고
    evidence 를 붙여 신고한다(비대칭 캡 7/5). 같은 사건을 두 콜이 각자 번역하면 어느 쪽이
    정본인지 알 수 없어진다. 대체 = 활력·평형의 rule 문장.
  - `rest_eval` (detected/quality/safe_location/activity/target): **부재를 감지하는 필드**였다.
    "아무것도 안 함을 감지하는 것만큼 쓸데없는 감지가 없다" — 휴식은 감지 대상이 아니라 산문에
    있으면 델타의 근거일 뿐이다. 대체 = 없음(다운타임 코드 효과와 함께 폐기).
  ENGLISH ONLY 목록에서도 둘의 reason 이 빠졌다.
"""

import logging
import re
import json
from typing import Dict, Any, Optional

import config
import bot_utils
import text_resources
import analysis_resources
import domain_manager
from orchestration_context import GameContext
from google.genai import types

# [SYSTEM NOTE] Flash tends to hallucinate in complex models. 
# We maintain the original complexity but wrap them in clear instructional tags.

logger = logging.getLogger("Theoria")


# [2026-07-27] render-facing 필드 언어 드리프트 계측 — **이상할 때만 1줄**(정상 시 로그 0).
# 배경: 지시(스키마 "ENGLISH ONLY … Korean here gets transcribed verbatim into prose = BUG")는
#   있었으나 이행 여부를 아무도 보지 않았다(ko_ratio는 텔레스코프 전용). 리더 로그에서 같은 병이
#   실증돼(07-27 노트 전량 한글) 계측을 붙인다. 신규 태그이나 정상 상태에서 미발화 → 판독 부담 0
#   (iceberg "정상은 말하지 않는다" 원칙). 값은 고치지 않는다 — 검출≠쓰기. 순수 함수.
_RENDER_FACING_KEYS = frozenset({
    # 확실히 영어 서술이어야 하는 필드만. line/reason/Aspects 같은 **범용 키는 제외** —
    # 퀘스트 내용·시계 라벨·사물명처럼 한글이 정당한 자리에서 오탐이 난다(레티어스 지적 07-27).
    "Observation", "UserIntent", "descriptor", "env_influence", "value_conflict", "deep_read",
    "attitude", "deflection", "primary_link", "render_hint", "offscreen_trace",
    "narrative_hook", "knows", "secrets_held", "false_beliefs",
    "roll_outcome",   # [2026-10-01 2단계] 렌더 [Outcome] 시드로 직행하는 영어 한 줄
})
_HANGUL_RF = re.compile(r"[가-힣]")
# 고유명사는 한글 허용("Korean only for proper nouns") → 1글자라도 잡으면 오탐 대량.
# 비율 임계: 서술 자체가 한글로 넘어간 경우만(고유명사 몇 개 섞인 영문은 보통 20% 미만).
_LANG_DRIFT_RATIO = 0.4


def _scan_lang_drift(node, _hits=None, _key=None):
    """render-facing 값의 한글 비율이 임계 초과인 필드만 수집. 반환: {키: 비율}."""
    if _hits is None:
        _hits = {}
    if isinstance(node, dict):
        for k, v in node.items():
            _scan_lang_drift(v, _hits, k)
    elif isinstance(node, list):
        for v in node:
            _scan_lang_drift(v, _hits, _key)
    elif isinstance(node, str) and _key in _RENDER_FACING_KEYS:
        t = node.strip()
        if len(t) >= 8:  # 짧은 값(고유명사 단독 등)은 비율이 튀므로 제외
            ratio = len(_HANGUL_RF.findall(t)) / len(t)
            if ratio >= _LANG_DRIFT_RATIO:
                _hits[_key] = max(_hits.get(_key, 0.0), ratio)
    return _hits

# =========================================================
# THEORIA SYSTEM PROMPTS (UNE 통합 분석 엔진)
# =========================================================


class TheoriaAnalyzer:
    """
    UNE 좌뇌 분석 엔진.
    인지 + 분석을 통합하여 GameContext를 풍부하게 채웁니다.
    """
    
    def __init__(self, client, model_id: str):
        self.client = client
        self.model_id = model_id

    @staticmethod
    def _extract_active_genres(genres: Any) -> list:
        """RequestData.genres(dict)에서 이론 강조용 장르 리스트를 추출."""
        if isinstance(genres, str):
            genres = [genres]
        # [2026-07-16 분석스택 감사] 폴백에서 drama 제거 — 장르 미설정 세션이 자동으로
        # drama emphasize(Four-Layer 극적 폭로·Kübler-Ross·恨·Fermentation Recall,
        # npc_default='complex')를 받던 최상류 중력원. drama는 명시 선택 시만.
        if isinstance(genres, list):
            out = [str(g).strip() for g in genres if str(g).strip()]
            return out or ["modern"]

        if isinstance(genres, dict):
            out = []
            for key in ("stage", "flavor", "lens"):
                val = genres.get(key, [])
                if isinstance(val, str):
                    val = [val]
                if not isinstance(val, list):
                    continue
                for item in val:
                    s = str(item).strip()
                    if not s or s in out:
                        continue
                    out.append(s)
            return out or ["modern"]

        return ["modern"]

    async def analyze_input(self, context: GameContext) -> Dict[str, Any]:
        """전체 분석을 수행하고 결과를 반환합니다."""
        if not self.client:
            return {"error": "No client"}

        prompt = self._build_prompt(context)

        # Extract genre and scene context for conditional loading
        req = context.request
        anchors = context.narrative_anchors
        active_genres = self._extract_active_genres(req.genres)
        scene_context = {
            "scene_type": anchors.get("scene_type", "normal"),
            "intimate_module": True,
            "channel_id": anchors.get("channel_id", ""),
            # [2026-07-15 수리] 기존: len(anchors.get("history", [])) — anchors에 "history"
            # 키가 존재한 적이 없어 **항상 0**. turn_count는 get_session_spotlight의 로테이션
            # 축(group_idx = turn_number % total_groups)이라 스포트라이트가 매 턴 같은 5개로
            # 고정돼 있었다. une_facade가 실 카운터(world_state.turn_index)를 넣어준다.
            "turn_count": int(anchors.get("turn_index", 0) or 0),
        }
        system_instruction = self._build_system_instruction(active_genres, scene_context)
        
        gen_config = types.GenerateContentConfig(
            system_instruction=system_instruction,
            response_mime_type="application/json",
            max_output_tokens=16384,
            temperature=config.ANALYSIS_TEMPERATURE,
            top_k=config.ANALYSIS_TOP_K,
            top_p=config.ANALYSIS_TOP_P,
            safety_settings=config.SAFETY_SETTINGS,
            # [Gemini 3] presence_penalty/frequency_penalty not supported
        )
        # [2026-10-06 분석 틀] 유저 턴 mandate·가짜 확인 응답 삭제 — 틀은 시스템 머리에 한 번(build_analysis_directive).
        contents = [
            types.Content(role="user", parts=[types.Part(text=prompt)]),
        ]

        last_error = None
        for attempt in range(2):
            try:
                # [2026-07-05 후속] per-turn 추출만 전용 모델로 라우팅.
                # [2026-08-18 역할 선언] 모델은 역할 토큰이 지목한다(extract 체인 = EXTRACT>FLASH).
                # contextvar 는 잔류 — 추론 tier(ANALYSIS_REASONING_TIER_EXTRACT)를 여전히 소유한다.
                with config.extract_analysis():
                    response = await self.client.aio.models.generate_content(
                        model=config.role_model("extract"), contents=contents, config=gen_config
                    )

                if not response.text:
                    last_error = "Empty response"
                    continue

                cleaned = bot_utils.clean_json_text(response.text)
                try:
                    result = json.loads(cleaned)
                except json.JSONDecodeError:
                    try:
                        repaired = bot_utils.repair_json(cleaned)
                        result = json.loads(repaired)
                        logger.info("[Theoria] JSON repair succeeded")
                    except json.JSONDecodeError as je2:
                        # [2026-07-15] Python-repr 폴백 — 모델이 단따옴표 dict(파이썬 repr)로
                        # 출력하는 케이스(GLM 추출 에코 실관측: [{'id': 'c27a...'}]).
                        # repair_json은 키만 고치고 값 따옴표는 못 고침. literal_eval은
                        # 리터럴 전용 평가라 코드 실행 위험 0. 실패 시 원래 에러 재던짐(재시도 유지).
                        import ast
                        try:
                            result = ast.literal_eval(cleaned)
                        except (ValueError, SyntaxError, MemoryError, RecursionError):
                            raise je2
                        if not isinstance(result, (dict, list)):
                            raise je2
                        logger.info("[Theoria] Python-literal fallback succeeded")
                # Gemini가 간헐적으로 `[{...DAI...}]` 배열로 wrap하는 경우 구제.
                # 원소 1개 + dict만 unwrap. 그 외는 기존 경로 유지.
                if isinstance(result, list) and len(result) == 1 and isinstance(result[0], dict):
                    logger.info("[Theoria] unwrapped JSON array top-level (Gemini quirk)")
                    result = result[0]
                _dai = self._validate_dai(result)
                self._blank_watch_extract(_dai, anchors)
                return _dai

            except json.JSONDecodeError as je:
                last_error = str(je)
                # 깨진 부분 주변 컨텍스트 로그 (진단용)
                pos = je.pos or 0
                snippet = cleaned[max(0, pos - 60):pos + 60] if cleaned else ""
                if attempt == 0:
                    logger.warning("[Theoria] JSON failed, retrying: %s | context: ...%s...", je, snippet)
                    logger.info("[빈칸] Theoria JSON 1차 실패 — 재시도")
                else:
                    logger.error("[Theoria] JSON failed (attempt 2): %s | context: ...%s...", je, snippet)
            except Exception as e:
                logger.error(f"Theoria analysis failed: {e}")
                return {"error": str(e)}

        logger.error(f"Theoria analysis failed after retry: {last_error}")
        logger.info("[빈칸] Theoria 분석 전체 빔 (%s)", last_error or "JSON parse failed")
        return {"error": last_error or "JSON parse failed"}

    def _validate_dai(self, dai: dict) -> dict:
        """DAI 결과 후검사 — 언어 드리프트 로그(관측 전용).

        [2026-10-07 a묶음 A3] deep_read 깊이 검사(shallow_read 플래그) 삭제 — 스키마에 없는 `npc_states`를 읽어
        07-16 이후 늘 일찍 돌아갔고(영구 꺼짐), 내보내던 지시도 "분석이 얕았다"를 렌더러에게 말하는 것이라 받는 쪽이 틀렸다.
        """
        # 언어 드리프트: 발견 시에만 1줄(정상=무발화).
        try:
            _drift = _scan_lang_drift(dai)
            if _drift:
                logger.warning("[LangDrift] %d field(s) mostly Korean: %s", len(_drift),
                               ", ".join(f"{k} {int(r*100)}%" for k, r in
                                         sorted(_drift.items(), key=lambda x: -x[1])[:3]))
        except Exception:
            pass
        return dai

    def _blank_watch_extract(self, dai: dict, anchors: dict) -> None:
        """[2026-10-07 분석 안쪽 E2] 빈칸 감시 — 조건이 선 턴에 칸이 비면 `[빈칸]` 한 줄. 로그만(동작 0).
        근거: 분석빈칸_null진단_2026-10-06 §5-2(진짜 빠짐을 라이브에서 센다)."""
        try:
            if not isinstance(dai, dict):
                return
            if str(dai.get("SceneType") or "").strip().lower() == "intimate" and not dai.get("IntimacyAnalysis"):
                logger.info("[빈칸] Theoria IntimacyAnalysis null (SceneType=intimate)")
            if dai.get("needs_judgment") is True:
                _am = dai.get("action_meta") or {}
                if isinstance(_am, dict) and not _am.get("active_passives"):
                    _anc = anchors if isinstance(anchors, dict) else {}
                    _pas = _anc.get("passives")
                    if not _pas:
                        _ap = (_anc.get("all_pcs") or {}).get(str(_anc.get("acting_user_id") or "")) or {}
                        _pas = _ap.get("passives") if isinstance(_ap, dict) else None
                    _n = len(_pas) if isinstance(_pas, (list, dict)) else 0
                    if _n:
                        logger.info("[빈칸] Theoria active_passives [] (needs_judgment, PC passives %d)", _n)
        except Exception:
            pass

    def _build_system_instruction(self, active_genres=None, scene_context=None) -> str:
        """Theoria v2.0 시스템 프롬프트 조립 — build_analysis_directive() 사용"""
        from theory_emphasis_engine import build_analysis_directive, get_session_spotlight, get_suppressed_theories, get_emphasized_theories

        active_genres = active_genres or ["modern"]  # [2026-07-16] drama 폴백 제거 — _extract_active_genres와 동기
        scene_context = scene_context or {}

        # 코어 이론 블록 (PART A~E, 항상 로딩)
        core_theories = "\n\n".join([
            "<THEORIA role='Observer and Librarian'>",
            analysis_resources.THEORIA_IDENTITY_V2,
            analysis_resources.ANALYTICAL_LENSES_ESTABLISHED,
            analysis_resources.ANALYTICAL_LENSES_CULTURAL,
            analysis_resources.ANALYTICAL_LENSES_CUSTOM,
            analysis_resources.ANALYTICAL_LENSES_LITERARY,
        ])

        # 규칙표 (항상 로딩)
        rule_tables = "\n\n".join([
            analysis_resources.STATE_TRACKING_V2,
            analysis_resources.OBSERVATION_INTENT,
            analysis_resources.TEMPORAL_ORIENTATION_V2,
            # THEORIA_CHAIN → 서사 콜(_build_narrative_system)로 이사 (2026-07-02 A안 — chain 생산이 그쪽)
            analysis_resources.THEORIA_POSITION_EFFECT,
            analysis_resources.THEORIA_MEMORY,
            analysis_resources.NPC_ATTITUDE_ANALYSIS,
            analysis_resources.NPC_KNOWLEDGE_V2,
            analysis_resources.JUDGMENT_SUPPORT,
            analysis_resources.DOOM_MENTAL_TRACKING,
            analysis_resources.ANOMALY_DETECTION,
            analysis_resources.SENSORY_ANCHORS,
        ])

        # 조건부 규칙표
        if scene_context.get("scene_type") == "intimate":
            # [2026-10-10 칸이관] 추출판 = 머리 + 0 다양성 + 1 창(window_check). 나머지 절은 서사 콜(Mira) 몫.
            rule_tables += "\n\n" + analysis_resources.SEXUAL_PSYCHOLOGY_EXTRACT
        # [2026-08-11 로드아웃 삭제] pending_flashback 게이트 + FLASHBACK_REST_DETECTION 규칙표 제거.
        # 이 규칙표의 유일한 게이트가 !회상 명령의 대기 앵커였다 = 명령 비활성 이후 한 번도 주입된 적 없음
        # (휴식 절반도 같이 사문이었고, 그 스키마 §Rest/Downtime 은 2026-09-06 P8b 에서 삭제됐다).
        # 같은 파일: _build_pending_flashback 빌더 + flashback_eval의 로드아웃·비용 서브필드 5종도 함께 제거.

        directive = build_analysis_directive(
            active_genres=active_genres,
            core_theories=core_theories,
            rule_tables=rule_tables,
            content_mandate=text_resources.ANALYSIS_FRAME,   # [2026-10-06] 원본 mandate → 분석 틀(1회)
        )

        # Rotation Spotlight: 세션 고정 시드 + 로테이션 + SUPPRESS/EMPHASIZE 필터
        session_seed = hash(str(scene_context.get("channel_id", "")))
        turn_number = scene_context.get("turn_count", 0)
        suppressed = get_suppressed_theories(active_genres)
        emphasized = get_emphasized_theories(active_genres)
        spotlight = get_session_spotlight(session_seed, turn_number, 5, suppressed, emphasized)
        # [2026-09-24 감사 §5-2 #27] 추출판 스포트라이트 — 서사 소유 칸 매핑은 떼고(서사 콜이 같은 선택을 받는다).
        from theory_emphasis_engine import retarget_for_extraction
        spotlight = retarget_for_extraction(spotlight)

        # 조건부 null 가이드: 모듈 미로딩 시 해당 필드 null 기본값 안내
        from theory_emphasis_engine import get_active_modules
        active_mods = set(get_active_modules(active_genres))
        null_hints = []
        # [2026-09-13 P14] 도착물 신호는 **부재가 기본**이다. 모듈과 무관하게 늘 실린다 —
        #   "부재=false"를 말하지 않으면 모델이 매 턴 true 를 찍고 편지 콜이 매 턴 돈다.
        null_hints.append("- arrival: false unless this scene itself calls for something to arrive")
        if 'COSMIC_HORROR_MODULE' not in active_mods:
            null_hints.append("- soma.dissociation: null unless extreme trauma/shutdown observed")
            null_hints.append("- anomaly_profile.perception_type: null unless supernatural elements confirmed in setting")
        if 'GROUP_DYNAMICS_MODULE' not in active_mods:
            null_hints.append("- relation.group_dynamic: null unless 3+ characters actively pressuring each other")
        if 'NEGOTIATION_MODULE' not in active_mods:
            null_hints.append("- relation.negotiation_stance: null unless active bargaining/trade in scene")
        if 'FORENSIC_MODULE' not in active_mods:
            null_hints.append("- NPCKnowledge.deception_cues: null unless strong behavioral deception signals")

        null_guide = ""
        if null_hints:
            null_guide = "\n\n<module_absent_guidance>\nThese fields' full theory modules are not loaded for current genre. Default to null/false unless clear evidence:\n" + "\n".join(null_hints) + "\n</module_absent_guidance>"

        # [2026-10-12 C32 몸·인격] 장전된 채널만 `vessel` 칸(+지금 표) — 장전 안 된 채널은 0자(종전 바이트 그대로).
        _vessel_blk = ""
        try:
            import vessel as _vs_t
            _vessel_blk = _vs_t.theoria_block(str((scene_context or {}).get("channel_id", "") or ""))
        except Exception as _e_vs:
            logger.debug(f"[Vessel] theoria block skip: {_e_vs}")
            _vessel_blk = ""
        _schema = self._get_output_schema()
        if _vessel_blk:
            # 스키마 태그 **안**(최종 언어 점검 앞)에 — 밖에 두면 "ALL these fields"에서 빠져 조용히 누락될 수 있다.
            _cut = _schema.rfind("\n<language_final>")
            if _cut < 0:
                _cut = _schema.rfind("</output_schema>")
            _schema = (_schema[:_cut] + "\n" + _vessel_blk.rstrip("\n") + _schema[_cut:]) if _cut >= 0 else (_schema + _vessel_blk)
        return directive + "\n\n" + spotlight + "\n\n" + _schema + null_guide + "\n</THEORIA>"

    # [2026-10-08 시점선] 렌더 전 분석의 시점 = 입력이 떨어지는 자리. Observation·soma·relation·logos는 시작점과 끄는 힘까지,
    #   NPC가 입력을 받는 답(바닥)은 산문 몫. CHARACTER ANALYSIS 머리 Time point 줄, IA power_dynamic은 동의 판정 대신 이끄는 쪽·가장자리.
    #   스펙 analysis_line/시점선_분석시작점·렌더바닥_스펙_2026-10-07.md (측정 §2-1: 라이브 zS T4 마라 대사 3/3 대 0/4).
    # [2026-10-08 관계 stance] relation.descriptor = PC를 향한 심리적 자세, 축 하나 "what they want from them"(can/want/do 중 want).
    #   옛 "stance … as concrete behavior / what the body is doing"은 자세를 몸의 자세로 읽게 해 soma를 한 번 더 썼고,
    #   그 몸 스냅샷·부정형이 엣지 stance로 저장돼 4b·렌더 관계 줄·🤝로 돌았다. 예시 목록·몸 단서는 받아쓰기/역효과(측정).
    #   스펙 analysis_line/4b고리·속마음연결_스펙_2026-10-08.md §1 (녹화 8턴×2: 자세 16/16→0/16, 부정형 2.38→0.88).
    def _get_output_schema(self) -> str:
        """출력 스키마 정의 (v2.0 — 16 new fields, mental→psyche rename)"""
        # [2026-10-09 반응주인] T1·Observation: 답은 서사 콜(굴림 뒤)에서 읽는다. psyche.descriptor = 마음이 붙든 것(MSE·signs 뺌 —
        #   MSE는 면담 기록이라 입력이 검사자가 돼 반응을 적었다: psy 146개 시선 86%·부정 79%·말 60%). T1 "Readings name what a body does" → 자리·쥔 것.
        #   근거 analysis_line/반응주인_Mira응답칸_스펙_2026-10-09
        # [2026-10-09 묶음A A1·A2·A4] psyche.descriptor "MSE affect signs as the input finds them", soma.descriptor
        #   "where it is, what it holds"(옛 "each a thing the body does"), power_dynamic에서 "how each answers the other" 뺌.
        #   답(이번 턴 NPC 반응) → 착지 순간(시작점). 근거 analysis_line/묶음A_do칸착지_측정_2026-10-09
        # [2026-10-09 묶음CDE] 4d 스키마 이름 = SCENE CONTINUITY(E3). D1(attempt 굴림 전 결과)은 문안으로 안 움직였다 —
        #   attempt 정의·T1 괄호(1차), Observation·soma·knows 정의 절(2차) 모두 강제 실패 판에서 추출이 성공을 그대로 썼다(4/4).
        #   문안은 되돌렸다. 해는 원장 저장 하나라 코드 게이트로 간다(판정 대기). 근거 analysis_line/묶음CDE_측정·스펙_2026-10-09
        # [2026-10-11 청소 C12] QualityFlags에서 echo_warning·redemption_warning 뺌(422판 켜짐 0·0, 응답 판정은 Mira 몫).
        #   dissonance_flag·sheet_deducible은 판독의 결이라 둔다. 근거 composition/청소묶음_지도·스펙_2026-10-11
        return """
<output_schema>
Return valid JSON with ALL these fields.
LANGUAGE RULE: every render-facing direction/analysis field → ENGLISH telegraphic ONLY (strictly English; NO Korean, NO Chinese characters). Korean ONLY where a value is world content (location/time labels, dialogue) or game-mechanics display (clock/judgment/condition/time_flow).


## INPUT & CONTEXT
- "InputAnalysis": {"Enhanced": str, "LogicTrace": ["≤3 short links: why this can or cannot happen here"], "Plausibility": "High/Low/Impossible", "Momentum": "Open/Closed"}
- "Observation": str (ENGLISH-ONLY telegraphic - neutral account of the input as it lands and the world facts it touches. no interpretation, facts only. NPC answers are read after the roll.)
- "UserIntent": str (ENGLISH-ONLY telegraphic - what the user immediately wants: explicit + implicit goals, emotional tone of the request)
- "input_mode": "decree" | "attempt" | "probe"
  - decree: the PC's own act = established fact, world absorbs; outcomes on others and world premises riding the line stay the world's ("자리에 앉는다", "밥을 먹는다")
  - attempt: user input = intention, outcome uncertain ("자물쇠를 따본다", "절벽을 오른다")
  - probe: user input = pressure/exploration, not command ("주변을 둘러본다", "유우의 눈을 바라본다"). Characters REACT to pressure, not obey.
- "CurrentLocation": str (Korean. PLACE NAME only — never append status/progress annotations like "(이동 완료)" or movement notes. While in the same place, keep the exact same name form across turns; sub-spots of one place stay under the same name unless the scene truly moved to a distinct location.)
- "location_path": [str] | null (OPTIONAL. Korean place names, root → current, max 4 elements. Last element = CurrentLocation exactly. e.g. ["아르카디아", "백작 저택", "서재"]. Omit when the place has no containing place worth naming.)
- "location_type": "region" | "area" | "room" | null (OPTIONAL. Type of the LAST location_path node only — travel scale, not depth: region=hours, area=minutes, room=instant.)
- "LocationRisk": "None/Low/Medium/High/Extreme"
- "TimeContext": str (Korean - e.g. "깊은 밤", "이른 아침")
- "SceneType": "normal/combat/social/summary/intimate"
- "EnergyDirection": "idle/rising/stagnant/detonation/aftershock" (observed scene energy. idle = low-energy everyday rhythm, the world breathes normally, nothing brewing. rising = tension accumulating from existing causal forces; do not block plausible resolutions. stagnant = energy present but locked in place, deadlock; report it, do not force change. detonation = conflict erupting from established causes. aftershock = post-eruption physical aftermath; silence is factual, not dramatic.)


## STAKES & ENVIRONMENT
- "Position": {"value": 0.0-1.0, "reason": "ENGLISH-ONLY telegraphic - why this position"}
- "Effect": {"value": 0.0-1.0, "reason": "ENGLISH-ONLY telegraphic - potential leverage"}
- "Aspects": [{"text": "ENGLISH-ONLY telegraphic aspect", "for_or_against": "for/against"}, ...] (Terrain | Lighting | Sound | Crowd | Objects | Weather | Social — each can help or hinder)


## CHARACTER ANALYSIS (psyche_states)
Fill soma BEFORE psyche (James-Lange + 五蘊 order). soma and psyche are INDEPENDENT (Cartesian Dualism).
Time point = where the input lands: each NPC's body as the input finds it, and what pulls at it. Readings name where a body is and what it holds; an act it does not do is not a reading. The answer to the input is read on the scout pass, after the roll. Where the input moves a body, read the body from there; the input outranks the previous frame.
**NPCs ONLY — never include the PC.** PC interiority belongs to the player.
NPCs perceive the PC through what the input SHOWS (words, actions), not through a PC psyche profile.
- SCENE CAST lists who is physically in this scene. Fill psyche_states for those names; a character only mentioned, remembered, or discussed is not in the scene and gets no entry.
- When SCENE CAST is absent, judge presence from the input as before.
- WHO THEY ARE is who each person is, not what this turn did: a habit or want it names is a lean. Descriptors record where this turn's evidence put the body; the sheet says what that evidence means for this person, never stands in for it.

- "psyche_states": {
    "CharName": {
        "psyche": {
            "descriptor": "ENGLISH-ONLY telegraphic cue - where the mind is as the input lands: what it attends to, what it turns over. direction/vector, NOT finished prose",
            "value": -100~+100 (extremely negative ~ extremely positive),
            "primary_emotion": "plutchik enum (陰陽: note opposing seed within)",
            "active_needs": ["one or two bare need words (Henderson/Erikson, e.g. safety, intimacy, esteem, rest): what dominates behavior now. The word only: no theory name, no 'X vs Y' pair"],
            "decision_mode": "reactive/deliberate (Kahneman + Carstensen)",
            "coping": "problem_focused/emotion_focused/avoidant/null (Lazarus. null = no stressor)",
            "apprehension_gap": "str or null (Absence/Approximation/Distortion: what THIS character failed to perceive, roughly approximated, or distorted through their own schema/defense. null = accurate apprehension)"
        },
        "soma": {
            "descriptor": "ENGLISH-ONLY telegraphic cue - SOAP-OA body signals as the input finds the body: where it is, what it holds; no emotion labels. direction/vector, NOT finished prose",
            "polyvagal": "ventral/sympathetic/dorsal (Porges: 3+ signals required)",
            "cultural_affect": "han/jeong/hwabyung/nunchi/chaemyeon/simma/gi/null",
            "env_influence": "str or null (ENGLISH-ONLY telegraphic. Nightingale: environment→psyche influence. null = negligible)",
            "dissociation": "none/mild/moderate/severe/null (Dissociation Spectrum: dorsal→entry point. mild=flat affect,delayed response. moderate=third-person self-reference,time gaps. severe=autopilot,recognition failure. Track across turns. null = no trigger)"
        },
        "relation": {
            "descriptor": "ENGLISH-ONLY telegraphic cue - psychological stance toward the acting PC: what they want from them. direction/vector, NOT finished prose. No trait/emotion naming",
            "bond_shift": "much_warmer/warmer/holds/cooler/much_cooler (toward the acting PC. 4b Stands = where they stand now; name how this turn moved it — holds when it did not. warmer/cooler = a small visible step; much_ = a turn that changes where they stand: a rescue, a betrayal, a confession)",
            "tension_shift": "spikes/rises/holds/eases (open conflict with the acting PC, independent of bond — a devoted NPC can still be furious; 4b friction = where it stands now; spikes = conflict breaks into the open this turn)",
            "attachment": "secure/anxious/avoidant/disorganized (Bowlby: from behavioral evidence)",
            "phase": "orientation/identification/exploitation/resolution (Peplau: cannot skip stages; 4b Relation(prev) = last turn's phase, at most one step per turn, regression unlimited)",
            "stage": "front/back (Goffman: by audience, not just location)",
            "group_dynamic": "conformity/obedience/groupthink/diffusion/null (Group Dynamics: active in 3+ character scenes. null = no group pressure)",
            "negotiation_stance": "cooperative/competitive/exploitative/null (BATNA strength reflects Position value. null = no negotiation active)"
        }
    }
  }


## NARRATIVE TRACKING
- "memory_triggers": [{"trigger": str, "character": str, "echo": str, "type": "traumatic/nostalgic/shameful/loving (Fermentation Recall: current state distorts memory)"}]


## JUDGMENT SUPPORT
- "needs_judgment": boolean
- "action_meta": {"action": "Korean — describe the CURRENT user input ONLY. Do NOT repeat or rephrase previous turn's action.", "type": "__ACTION_TYPES__", "difficulty": "easy/normal/hard/extreme", "skill": "practiced/average/unpracticed", "active_passives": ["verbatim name from the acting PC's Passives whose desc applies to THIS action; [] when none"], "active_items": ["verbatim name from the acting PC's Inventory that THIS action puts to use; [] when none"], "assist": ["verbatim name of an on-stage character already working alongside the PC before this input; [] when the PC acts alone"], "active_strikes": ["verbatim name from the acting PC's Strikes that bears on THIS action; [] when none"]}
- "asset_evaluation": {
    "reason": "Korean",
    "memo_relevant": {"content": "relevant memo/clue excerpt (Korean)", "reason": "why this helps (Korean)"} | null
  }


## NARRATIVE HOOKS & TIME
- "time_flow": {"ticks": 1-20, "explicit_hours": number | null, "target": {"slot": "시간대명(새벽/오전/오후/황혼/저녁/심야)", "day_offset": 0|1, "hour": 0-23, "minute": 0-59, "year": int | null, "month": 1-12 | null, "day_in_month": 1-30 | null} | null}
  - Two channels for time the user states, both ONLY from THIS turn's user input (profile, lore, sheet, and earlier narration never trigger them):
    - target = ABSOLUTE time ("오후 3시", "15시 5분", "다음날 아침 9시", "저녁까지 기다린다", "3월 5일"). hour/minute ONLY when stated. year/month/day_in_month (V8.5 캘린더) ONLY when the user states a date ("3월 5일" → month=3, day_in_month=5 / "2년 1월" → year=2, month=1); a plain clock jump leaves them null. day_offset is relative ("다음날", "이튿날"), separate from the absolute date. Simple actions never use target.
    - explicit_hours = RELATIVE skip ("10분 뒤" → 0.167, "30분 후" → 0.5, "1시간 지나" → 1.0, "2시간 뒤" → 2.0). It bypasses the SCENE_TIME_RULES clamp: the user's stated delta is authoritative.
    - Both stated → target (more specific).
  - Vague phrases ("한참 후", "잠시 후", "시간이 흘러", "얼마 지나", "결국", "이내") → ticks only; target and explicit_hours stay null.
  - No explicit time statement → both null; time advances by ticks. When unsure, nulls and fewer ticks: over-advancing steals the player's time.
  - 예: "다음날 아침 9시" → {"target": {"slot": "오전", "day_offset": 1, "hour": 9}}, "10분 뒤" → {"explicit_hours": 0.167, "ticks": 5}, "3월 5일 오후 2시" → {"target": {"slot": "오후", "month": 3, "day_in_month": 5, "hour": 14}}.
- "doom_clocks": {
    "clock_updates": [{"name": str, "delta": int(-1~+2), "reason": "ENGLISH-ONLY telegraphic"}],
    "clock_new": {"name": "Korean", "segments": 4|6|8, "tick_mode": "action|time|hybrid", "threat": "Korean — 이 시계가 완성되면 무슨 일이 벌어지는가", "defense_action": "Korean — 이 시계를 막으려면 무엇을 해야 하는가 (구체적 행동 힌트)", "source": "narrative|consequence", "linked_entity": "str or null — 관련 NPC/세력 이름", "tags": ["Korean"], "doom_on_complete": "null(threat 기본: doom 상승) | 0(timer: 중립 마감) | negative int(opportunity: 완성 시 doom 감소, e.g. -10)"} | null,
    "clock_resolved": ["시계 이름 — 서사적으로 위협이 해소된 경우만"]
  }
- "anomaly_profile": {"trigger": str, "category": "supernatural/psychological/social/environmental/temporal", "intensity": "Low/Mid/High/Extreme", "polarity": "positive/negative/mixed", "perception_type": "veridical/illusory/hallucinatory/delusional/null (Anomalous Experience Framework. In supernatural settings, 'hallucinatory' may be CORRECT. null = no anomaly)", "line": "ENGLISH-ONLY telegraphic - 1-line event direction", "reason": "ENGLISH-ONLY telegraphic", "continues": "E#/C# id from Pending events or Active Conditions when this proposal carries that thread one step further | null", "location": "이벤트 발생 장소 (CurrentLocation과 다를 때만. 빈 문자열이면 현재 위치)"} | null (2026-07-15: propose each turn — downstream code gates timing/acceptance and holds most back. null = the scene physically cannot host any event, NOT "seems quiet")
- "condition_resolved": ["조건 태그 — 서사적으로 해당 조건이 더 이상 세계에 유효하지 않을 때. Active Conditions 참고"]
- "condition_updates": [{"tag": "조건 태그", "intensity": "새 강도 (Low/Mid/High/Extreme)", "description": "갱신된 상황 묘사 (Korean)"}]


## COGNITIVE ENHANCEMENT
- "HabitusAnalysis": {
    "Economic": "English - material standing indicators",
    "Cultural": "English - knowledge/taste patterns",
    "Social": "English - network/authority position"
  }
- "SensoryAnchors": [{"anchor": "Physical sensation (Somatic Marker/Damasio)", "memory_link": "English - connected memory"}]


## NPC TRACKING
- NAMING (all per-NPC keys — psyche_states / NPCKnowledge; avoid duplicate entities): reuse the EXACT name form of any NPC already in the
  NPC ROSTER / 4b NPC STATE. Never translate or re-romanize a known character — 레나 stays
  레나, not Rena; Rena stays Rena. A new name only for a genuinely new person. For a
  cross-script reference to a KNOWN NPC, write it as KnownName(otherform) e.g. 레나(Rena)
  so it resolves to one entity, not two.
- "NPCKnowledge": {
    "NpcName": {
        "knows": ["ENGLISH-ONLY telegraphic - key info currently known. Direct witness or confirmed telling; what was only overheard or inferred belongs in suspects"],
        "secrets_held": ["ENGLISH-ONLY telegraphic - what is being hidden"],
        "would_share": boolean,
        "leak_risk": "none/low/medium/high (Curse of Knowledge: 아는 것을 숨기기 어려움)",
        "false_beliefs": ["ENGLISH-ONLY telegraphic - believed contrary to fact (Theory of Mind). A 4b \"Believes (wrongly)\" entry carries forward while it holds; drop it once this scene corrects it."],
        "suspects": ["ENGLISH-ONLY telegraphic - SUSPECTED but unconfirmed: overheard, half-seen, inferred. Distinct from knows(confirmed). Seeing a result does not give the actor, cause, method, ownership, motive, or private thought behind it — those land here"],
        "deception_cues": "str or null (Statement Analysis/SCAN: pronoun_shift/tense_shift/time_gap/over_detail/emotion_misplace, read in what this NPC has already said or shown before this input. null = no deception detected)",
        "secret_updates": [{"truth_ref": "short substring of the secrets_held entry this updates", "surface": "ENGLISH-ONLY - what it LOOKS like from outside (cover story, visible tell)", "reveal_gate": "condition that would crack it open, or ''", "knowers": ["who now knows the truth"], "suspecters": ["who now suspects"], "status": "kept/leaking/revealed"}]
    }
  }
  (secret_updates: OPTIONAL, only when a secret's outward surface, gate, or who-knows changed THIS turn. Omit otherwise.)
  (secrets_held: what play has shown being hidden — a played gap between what someone presents and what their behavior conceals. A secret or hidden fact written in their sheet enters only once this scene's play touches it; until then it stays the sheet's. Never invent an unplayed secret.)


## SAFETY & QUALITY
- "TemporalOrientation": {"focus": "past/present/future", "intensity": 0.0-1.0}
- "QualityFlags": {
    "convergence_warning": "boolean - unearned comfort / premature resolution",
    "stagnation_warning": "boolean - 3+ turns flat. Test: does this turn repeat a prior scene's purpose, the same location-function pair, the same investigation step, the same waiting state, the same dialogue aim, or the same emotional beat, without new evidence? YES → true",
    "dissonance_flag": "boolean - NPC contradictory beliefs/actions (Festinger)",
    "symptom_cluster": "PTSD/anxiety/depression/null (DSM-5: track co-occurring symptoms as consistent SET. Cherry-picking = inconsistent character. null = no clinical pattern)",
    "sheet_deducible": "boolean - current NPC response is directly deducible from profile tags alone. Test: could ANY character with the same tags produce this response? YES → true"
  }
- "RelevantContext": ["Quoted lore/rule directly applicable", ...]
- "RelevantNPCs": ["NPC name from roster relevant to THIS scene (max 5)"]
- "relevant_chunks": [0, 2, 5] (indices from LORE CHUNKS — up to 8 most relevant)

## SPATIAL PALETTE

Observe the physical space — architecture, not decoration. Territory vs Lens: a body's presence or an action can change the space itself (objective); a POV character's perception can color it (subjective) — that is filter, never a space change.
- "spatial_read": {
    "spatial_type": "enclosed(traces linger)/resonant(echoes, emptiness)/open(wind erases)/elevated(exposed)/crowded(traces drown)/moving(transient)",
    "active_traces": [{"type": "thermal/scent/acoustic/surface/object", "detail": "ENGLISH-ONLY telegraphic 1 fragment"}] | null,
    "light": {
      "lighting": "ENGLISH-ONLY telegraphic — source + key + direction (e.g. 'low-key window side-light')",
      "hue": "ENGLISH-ONLY telegraphic — specific hue from full spectrum (amber/gold/rust/crimson/grey/steel/cool/green-cast/…)",
      "saturation": "ENGLISH-ONLY telegraphic — vivid/solid/washed/pastel"
    } (base palette, every turn: the controlling light DERIVED, not picked — lighting follows the actual source + key + direction; hue follows the scene's dominant valence + source; saturation follows emotional intensity. When the valence or source shifts, the light shifts with it; while they hold, it holds),
    "filter": "ENGLISH-ONLY telegraphic or null — POV character's perceptual lens (subjective). Never a change to the space itself",
    "tension": "designed X <-> lived Y (Lefebvre: the space's intended purpose vs how characters actually inhabit it)" | null,
    "shift": null | "gradual" | "sudden",
    "threshold": null | "mild" | "sharp"
  }

## SCENE CONTINUITY (requires ### 4d. SCENE CONTINUITY — null when it is absent)
- "continuity_check": null OR {
    "flags": [{"type": "spatial_break|sensory_break|object_break|tone_break|npc_break",
               "risk": "ENGLISH-ONLY telegraphic — what discontinuity risk exists",
               "correction": "ENGLISH-ONLY telegraphic — how to naturally bridge the gap: the previous frame's fact to carry forward (for npc_break: where that person was, what they held, what they had said)"}],
    "anchor_consumed": boolean
  }


## CONDITIONAL MODULES (output null if not triggered)

### IntimacyAnalysis (SceneType="intimate")
- "IntimacyAnalysis": null OR {
    "window_check": {"char_name": "within/above/below (Siegel, from polyvagal state)"}
  }  (NPCs only. Desire, dual control, who leads, body memory and after-behavior are read on the scout pass.)

### Flashback Evaluation (유저 입력에 소급 선언("사실 미리 ~했다", "이미 ~해뒀다")이 있으면 생성하라)
- "flashback_eval": null OR {
    "detected": boolean,
    "declaration": "Korean - 1-sentence summary of retroactive claim",
    "plausibility": "plausible/stretch/impossible",
    "tier": "trivial/standard/bold",
    "reason": "ENGLISH-ONLY telegraphic"
  }

### Arrival signal — 이 장면이 도착물(편지·공고·기사·전언)을 부르는가. **불리언 하나**.
- "arrival": boolean (true ONLY when the scene itself calls for something to ARRIVE — a letter
  handed over, a notice going up, word reaching the PC. This is not detection of an arrival that
  already happened in the prose; it is the signal that this moment wants one. Absence = false.
  Do NOT decide what arrives, from whom, or through which medium — the arrival call chooses that.)

<language_final>
BEFORE OUTPUT — RE-CHECK EVERY FIELD'S LANGUAGE. Exactly two zones, mutually exclusive, no field is bilingual:
- ENGLISH ONLY (render-facing — feeds the renderer; Korean here gets transcribed verbatim into prose = BUG): Observation, UserIntent, descriptor, env_influence, knows, secrets_held, false_beliefs, Aspects, AND the reason fields of clock_updates / flashback_eval.
- KOREAN (user-displayed only): CurrentLocation, location_path, TimeContext, action_meta.action, asset_evaluation reason+labels (판정 표시), clock_new name/threat/defense_action/tags (시계 UI), condition_updates description (상태 표시).
Any render-facing field whose value would contain Hangul → write that field's value in English instead. This is a per-field language swap ONLY — never a reason to halt, shorten, or null the output; always return the complete JSON with every field. When unsure → English.
</language_final>
</output_schema>
""".replace("__ACTION_TYPES__", "/".join(config.ACTION_TYPES))  # [2026-09-16 3차] type enum 한 곳(config.ACTION_TYPES)

    def _build_pc_section(self, anchors: dict) -> str:
        """PC 정보 섹션 빌드 (솔로/다인 자동 분기)"""
        all_pcs = anchors.get("all_pcs", {})
        acting_uid = anchors.get("acting_user_id", "")

        # 다인 플레이 (2명 이상)
        if len(all_pcs) > 1:
            acting_mask = "Unknown"
            for uid, pc in all_pcs.items():
                if uid == acting_uid:
                    acting_mask = pc.get("mask", "Unknown")
                    break

            lines = [f"### 3. PLAYER CHARACTERS (행동자: {acting_mask})"]
            for uid, pc in all_pcs.items():
                mask = pc.get("mask", "Unknown")
                marker = " (행동자)" if uid == acting_uid else ""
                lines.append(f"\n[{mask}]{marker}")
                lines.append(f"- Sheet:\n{pc.get('sheet') or 'N/A'}")
                lines.append(f"- Passives: {json.dumps(pc.get('passives', []), ensure_ascii=False, default=str)}")
                # [2026-10-15 C42] 타격칸(마이너스 상태 전용 4칸) — active_strikes 는 이 목록에서 이름 그대로 집는다.
                lines.append(f"- Strikes: {json.dumps(pc.get('strikes', []), ensure_ascii=False, default=str)}")
                lines.append(f"- Vigor: {pc.get('vigor_value', 100)} | Composure: {pc.get('composure_value', 100)}")
            return "\n".join(lines)

        # 솔로 플레이 (기존 형식 유지)
        lines = ["### 3. PLAYER CHARACTER"]
        # [2026-09-16 시트 2차] 서술 = PC 페이지 lore 절 발췌(절 이름 그대로) — personality 키 소멸.
        lines.append(f"- Sheet:\n{anchors.get('sheet') or 'N/A'}")
        # [2026-07-15] Python repr(단따옴표) 주입 금지 — 모델이 형식을 미러링해
        # 단따옴표 dict로 응답(JSON 파싱 붕괴 실관측). json.dumps로 직렬화(§7.11 미러 원리).
        _j = lambda v: json.dumps(v, ensure_ascii=False, default=str)
        lines.append(f"- Passives: {_j(anchors.get('passives', []))}")
        lines.append(f"- Strikes: {_j(anchors.get('strikes', []))}")   # [2026-10-15 C42] 타격칸
        lines.append(f"- Inventory: {_j(anchors.get('inventory', []))}")
        lines.append(f"- Memos: {_j(anchors.get('memos', []))}")
        return "\n".join(lines)

    def _build_mental_line(self, anchors: dict, bus) -> str:
        """Vigor/Composure 상태 라인 빌드 (솔로/다인 분기)"""
        all_pcs = anchors.get("all_pcs", {})
        if len(all_pcs) > 1:
            parts = []
            for uid, pc in all_pcs.items():
                mask = pc.get("mask", "Unknown")
                vigor = pc.get("vigor_value", 100)
                composure = pc.get("composure_value", 100)
                parts.append(f"{mask}: 활력{vigor}/평형{composure}")
            return f"- Vigor/Composure (PC별): {' / '.join(parts)}"
        vigor_val = bus.vigor.get('value', 100)
        composure_val = bus.composure.get('value', 100)
        return f"- Vigor: {vigor_val} | Composure: {composure_val}"

    _DEPTH_PSYCHE_HINTS = {
        70: "deep bond — defenses lowered, mask cracks show, willing to share",
        40: "growing familiarity — selective openness, testing boundaries",
    }

    def _build_npc_context(self, anchors: dict, starts_hint: bool = True) -> str:
        # [2026-10-10 칸이관] starts_hint: 'no record yet' 줄 끝 지시(starts_as / friction_starts)는 그 칸의 주인(Mira)에게만.
        #   추출 콜(Theoria)은 시트 줄만 본다(False).
        """NPC 태도 + 지식 + depth/psyche 힌트를 프롬프트에 포함"""
        parts = []
        attitudes = anchors.get("stored_npc_attitudes", {})
        knowledge = anchors.get("stored_npc_knowledge", {})
        # [2026-08-02 B축 지속] 이전 턴 soma. 스키마의 `dissociation: Track across turns`가
        #   그동안 **집행 재료 없이** 서 있었다 — 이전 턴 값이 어디에도 안 돌아왔으므로
        #   모델은 매 턴 히스토리에서 재유추(=재발명)할 수밖에 없었다.
        soma_prev = anchors.get("stored_npc_soma", {}) or {}
        # [2026-10-07 a묶음 A4] 지난 턴 관계 상태(phase·attachment) — Peplau "단계 못 건너뜀"·convergence의 집행 재료.
        #   종전엔 이전 값이 어디에도 안 돌아와 매 턴 새로 판정했다(phase 85%가 orientation 고착).
        rel_prev = anchors.get("stored_npc_relation", {}) or {}
        # [2026-08-11 사망 파이프라인] {이름: down|dead}. 전원 생존이면 빈 dict.
        _inactive = anchors.get("npc_inactive", {}) or {}
        # [2026-09-13 S2 T1] 재등장 인물 로그 원문(4e)만 있어도 렌더해야 한다 —
        #   종전 조기 return은 attitudes/knowledge/soma 셋만 봤다.
        recall_evidence = anchors.get("recall_evidence", {}) or {}
        # [2026-09-26 관계 시작값 S3] 관계 기록 없음 + 시트에 이 PC 문장 있음 → "no record yet" 줄(시작점을 말로 받는다).
        unrecorded = anchors.get("relation_unrecorded", {}) or {}
        if not attitudes and not knowledge and not soma_prev and not rel_prev and not recall_evidence and not unrecorded:
            return ""
        _pc_ru = (((anchors.get("all_pcs") or {}).get(anchors.get("acting_user_id", "")) or {}).get("mask")
                  or "the acting PC")

        _npc_state_names = set(list(attitudes.keys()) + list(knowledge.keys())
                               + list(soma_prev.keys()) + list(rel_prev.keys()) + list(unrecorded.keys()))
        if _npc_state_names:
            parts.append("### 4b. NPC STATE (Previous Turn)")
        # [2026-10-12 C23b] 저장된 NPC 활력·평형(게이지) — soma가 매 턴 새로 판단하면서 이 값을 몰랐다.
        #   렌더는 soma를 따르므로(C23 렌더 8콜 영), 몸을 판단하는 자리에 값을 돌려준다. 이름 맞추기·허용 인물·
        #   토글은 custom_vars.npc_vc_values 한 문(세션 NPC는 값이 없어 줄도 없다). Mira도 같은 함수라 같이 본다.
        _reserve = {}
        if _npc_state_names:
            try:
                import custom_vars as _cv_rs
                _reserve = _cv_rs.npc_vc_values(str(anchors.get("channel_id", "") or ""), sorted(_npc_state_names))
            except Exception as _e_rs:
                logger.debug("[Theoria] 4b Reserve skip: %s", _e_rs)
                _reserve = {}
        for npc_name in _npc_state_names:
            npc_lines = [f"{npc_name}:"]
            att = attitudes.get(npc_name, {})
            if att:
                # [2026-09-15 관계 통합] 엣지 값을 **그대로** 돌려준다 — relation 층 bond/tension의 앵커.
                #   (구: Attitude enum + Depth 힌트만, tension·이전 relation.value는 안 돌아왔다.)
                _bond = att.get("bond", att.get("depth", 0))
                _stance = str(att.get("stance", att.get("reason", "")) or "").strip()
                try:
                    _bond_i = int(_bond)
                except (TypeError, ValueError):
                    _bond_i = 0
                # [2026-09-25 관계 정성] 숫자 대신 말(구간·방향·마찰, domain_manager.relation_words 한 벌). 숫자는 코드 소유 —
                #   모델은 bond_shift/tension_shift 로 이동만 낸다. _bond_i 는 아래 psyche 힌트 문턱(코드 안)에만 쓴다.
                try:
                    import domain_manager as _dm4b
                    _rw = _dm4b.relation_words(att)
                except Exception:
                    _rw = ""
                npc_lines.append(f"  Stands: {_rw or 'neutral'}" + (f" — {_stance}" if _stance else ""))
                # Bond↔Psyche feedback hint (구 Depth 힌트 문장 유지)
                for threshold, hint in sorted(self._DEPTH_PSYCHE_HINTS.items(), reverse=True):
                    if _bond_i >= threshold:
                        npc_lines.append(f"  → {hint}")
                        break
            elif unrecorded.get(npc_name):
                _ls_ru = [str(_l).replace("{{user}}", _pc_ru) for _l in unrecorded[npc_name]]
                _q_ru = " / ".join('"' + _l + '"' for _l in _ls_ru)
                # [2026-09-27 E] 이름 없는 관계 줄(대명사)이면 "PC 이야기인가"를 모델이 가른다.
                try:
                    import npc_manager as _nm_ru
                    _named_ru = any(_nm_ru.mentions_pc(_l, [_pc_ru]) for _l in _ls_ru)
                except Exception:
                    _named_ru = True
                if _named_ru:
                    npc_lines.append(f"  Stands: no record yet — sheet on {_pc_ru}: {_q_ru}"
                                     + (" → name where they start (starts_as / friction_starts)" if starts_hint else ""))
                else:
                    npc_lines.append(f"  Stands: no record yet — unnamed sheet lines, maybe about {_pc_ru}: {_q_ru}"
                                     + (f" → if they are about {_pc_ru}, name where they start "
                                        f"(starts_as / friction_starts); if not, omit both" if starts_hint else ""))
            kn = knowledge.get(npc_name, {})
            if kn and kn.get("knows"):
                knows_str = "; ".join(kn["knows"][-5:])   # [2026-10-09 묶음CDE E4] 최신 다섯 — 원장은 오래된 것→새것(update_npc_knowledge [-20:]). 옛 [:5]는 가장 오래된 다섯
                npc_lines.append(f"  Knows: [{knows_str}]")
                if kn.get("secrets_held"):
                    npc_lines.append(f"  Secrets: [{'; '.join(kn['secrets_held'][:3])}]")
                npc_lines.append(f"  LeakRisk={kn.get('leak_risk', 'none')}")
            # [2026-09-30 wave8_2nd G] 오해 되먹임 — domain_manager가 영속한 misbeliefs를 다음 턴 분석에 값만 돌려준다.
            #   전엔 저장만 되고 읽는 곳이 0이라 모델이 매 턴 오해를 새로 지어 덮어썼다. knows가 비어도 싣는다(게이트 밖).
            #   해소 계약 = 분석이 그 NPC의 false_beliefs를 비워 내면 비워진다(update_npc_knowledge 대체 규칙).
            _mb = [str(x) for x in ((kn or {}).get("misbeliefs") or []) if x][:3]
            if _mb:
                npc_lines.append(f"  Believes (wrongly): [{'; '.join(_mb)}]")
            # [2026-10-07 a묶음 A4] Relation(prev) — 값만 돌려준다. held = phase가 그대로인 턴 수(soma와 같은 임계, 미만은 침묵).
            _rp = rel_prev.get(npc_name) or {}
            if _inactive.get(npc_name):
                _rp = {}
            if isinstance(_rp, dict) and (_rp.get("phase") or _rp.get("attachment")):
                _rbits = [f"{k}={_rp.get(k)}" for k in ("phase", "attachment") if _rp.get(k)]
                _rhold = ""
                try:
                    _rsince = _rp.get("since_turn")
                    _rcur = int(anchors.get("turn_index", 0) or 0)
                    if _rp.get("phase") and _rsince is not None and _rcur > 0:
                        _rheld = _rcur - int(_rsince)
                        if _rheld >= int(getattr(config, "SOMA_PERSIST_MIN_TURNS", 2)):
                            _rhold = f" (held {_rheld}t)"
                except (TypeError, ValueError):
                    _rhold = ""
                npc_lines.append("  Relation(prev): " + ", ".join(_rbits) + _rhold)
            _sm = soma_prev.get(npc_name) or {}
            # [2026-08-11 사망 파이프라인] 생존축 필터 — 구멍 순위 5.
            #   soma 스냅샷은 08-11 오프스테이지 잔존 이후 **관측이 끊겨도 남는다**.
            #   그래서 죽거나 쓰러진 인물의 몸 상태가 `(held Nt)`를 달고 매 턴 재급식됐다
            #   — 시체에게 "이 상태가 12턴째 지속 중"이라고 알려주는 꼴.
            #   침묵이 정답: 값을 지우지는 않는다(회복하면 그 자리에서 다시 흐른다).
            if _inactive.get(npc_name):
                _sm = {}
            if isinstance(_sm, dict) and _sm:
                # 값만 돌려준다 — 해석·지시 없이. 유지/심화/회복 판단은 이번 턴 관찰의 몫.
                # [2026-08-11 soma 지속] enum 두 축만 렌더 — since_turn은 내부 시계지 상태가 아니다.
                _bits = [f"{k}={_sm.get(k)}" for k in ("polyvagal", "dissociation") if _sm.get(k)]
                if _bits:
                    # 무변화 지속 턴수. 임계 미만은 **침묵**(1턴짜리 = 노이즈).
                    # SQLite 조회 없음 — world_state가 실어 온 값만 뺀다(매 턴 경로).
                    _hold = ""
                    try:
                        _since = _sm.get("since_turn")
                        _cur_t = int(anchors.get("turn_index", 0) or 0)
                        if _since is not None and _cur_t > 0:
                            _held = _cur_t - int(_since)
                            if _held >= int(getattr(config, "SOMA_PERSIST_MIN_TURNS", 2)):
                                _hold = f" (held {_held}t)"
                    except (TypeError, ValueError):
                        _hold = ""
                    npc_lines.append("  Soma(prev): " + ", ".join(_bits) + _hold)
            # [2026-10-12 C23b] Reserve — 두 축 중 하나라도 70 아래일 때만(무탈한 인물마다 100/100 줄은 소음).
            #   값만 돌려준다(4b의 결). 읽는 법은 시스템 STATE_TRACKING_V2 1번(Continuity) 한 절이 맡는다.
            #   40~69는 숫자만 — 절이 해석을 주지 않는 구간이라 '조금 닳음'으로 읽힌다(하루 회복 중의 이음).
            #   죽음·쓰러짐(_inactive)은 soma와 같이 침묵.
            _rv = _reserve.get(npc_name)
            if _rv and not _inactive.get(npc_name):
                _rbits_rs = [f"{_lab} {_val}/100" for _lab, _val in (("body", _rv[0]), ("composure", _rv[1]))
                             if _val is not None]
                if _rbits_rs and any(_val is not None and _val < 70 for _val in _rv):
                    npc_lines.append("  Reserve: " + ", ".join(_rbits_rs))
            parts.append("\n".join(npc_lines))

        # [V10 Sprint 4] 막간 장부 — 분석이 막간 사실을 알아야 NPC 심리 추론이 정합
        interim = anchors.get("interim_ledger", "")
        if interim:
            parts.append(interim)

        # [2026-09-13 S2 T1] 4e. RECALL EVIDENCE — 30창 밖에서 다시 불려 나온 인물의
        #   로그 원문 행. 연속성 재료일 뿐 현재 상태가 아니다(계약 문장으로 못박는다).
        if recall_evidence:
            _rc = ["### 4e. RECALL EVIDENCE (verbatim log rows; continuity only)",
                   "These rows prove what was said or done then, not the present state; "
                   "a present character knows them only if the record shows it."]
            for _name, _lines in recall_evidence.items():
                if not _lines:
                    continue
                _rc.append(f"{_name}:")
                for _ln in _lines:
                    _rc.append(f"  - {_ln}")
            parts.append("\n".join(_rc))

        # [2026-09-14 W3a] 4f. WIKI — 페이지 play 절(T1 프로파일). LLM 콜 0.
        #   Knowledge는 위 4b가 이미 `Knows:`로 주므로 **제외**(이중 투입 금지).
        #   lore 절도 제외 — compile_for는 play 절만 낸다.
        try:
            if getattr(config, "WIKI_COMPILE", False):
                _ch_w3 = str(anchors.get("channel_id", "") or "")
                if _ch_w3:
                    _w3_names = []
                    for _n in (list(anchors.get("scene_cast") or [])
                               + list(anchors.get("nearby_cast") or [])
                               + list(_npc_state_names)
                               + list(recall_evidence.keys())):
                        if not isinstance(_n, str) or not _n.strip():
                            continue
                        if str(_inactive.get(_n) or "") == "dead":
                            continue
                        if _n not in _w3_names:
                            _w3_names.append(_n)
                    # [2026-09-14 W5] 현재 위치 + 이번 턴 세력을 뒤에 붙인다(상한 그대로).
                    try:
                        if getattr(config, "WIKI_PLACES", False):
                            import wiki_store as _ws_w5
                            for _n5 in _ws_w5.extra_entity_names(
                                    _ch_w3, str(anchors.get("action_text", "") or "")):
                                if _n5 not in _w3_names:
                                    _w3_names.append(_n5)
                    except Exception:
                        pass
                    if _w3_names:
                        import wiki_store as _ws_w3
                        _wc = _ws_w3.compile_for(_ch_w3, "T1", _w3_names,
                                                 exclude_sections=("Knowledge",))
                        if _wc.get("text"):
                            parts.append("### 4f. WIKI (play sections)\n" + _wc["text"])
        except Exception as e:
            logger.debug("[Theoria] wiki compile skipped: %s", e)

        # [A안 2026-07-02] ABSENT CAST(4c)·RECENT BEATS(4d)는 서사 콜(_build_narrative_prompt)로
        # 이사 — offscreen_trace/suggested_beats 생산이 그쪽이므로 재료도 그쪽에만 공급.

        return "\n".join(parts)

    @staticmethod
    def _build_scene_cast(anchors: dict) -> str:
        """[2026-09-02 R5] SCENE CAST 블록 — 스펙 §6 R5.
        psyche_states 조항이 "누가 있나"까지 판정하던 것을 "있는 사람들이 어떤가"로 좁힌다.
        무대 인원은 이제 코드가 알고 있으므로(위치 0단) 프롬프트가 **받는** 쪽이 된다.
        앵커가 없으면 빈 문자열 — 블록이 없을 때의 조항("judge presence from the input as
        before")이 그대로 살아 있으므로 위치 미기록 세션에서도 손해가 없다."""
        cast = anchors.get("scene_cast") or []
        if not cast:
            return ""
        return ("### SCENE CAST (registered NPCs physically in this scene right now)\n"
                + "\n".join(f"- {c}" for c in cast))

    def _build_session_memory_context(self, anchors: dict) -> str:
        """세션 메모리(arc, NPC schedules) + 스레드 장부 줄을 프롬프트에 포함"""
        mem = anchors.get("session_memory", {})
        if not mem:
            return ""

        parts = []
        arc = mem.get("current_arc")
        if arc:
            parts.append(f"- Current Arc: {arc}")

        # [2026-09-25 스레드 장부] 옛 active_threads(통째 재작성 리스트) → 장부(상태·기한·남은 몫).
        try:
            import thread_ledger as _tl4c
            _tl_line = _tl4c.analysis_line(anchors.get("channel_id", ""))
        except Exception:
            _tl_line = ""
        if _tl_line:
            parts.append(_tl_line)

        npc_schedules = mem.get("npc_summaries", {})
        # [2026-10-07 PC 이름 단일화] 옛 세션에 쌓인 PC 키("아담")는 읽을 때 뺀다 — NPC 일정 줄에 PC가 서던 자리.
        try:
            import domain_manager as _dm_sch
            _pcr_sch = _dm_sch.pc_registry(anchors.get("channel_id", "")) if anchors.get("channel_id") else {}
            if _pcr_sch and isinstance(npc_schedules, dict):
                npc_schedules = {k: v for k, v in npc_schedules.items() if not _dm_sch.is_pc_name(k, _pcr_sch)}
        except Exception:
            pass
        # [2026-10-11 C26 P4] 저장(npc_summaries)은 "마지막으로 본 활동"이라 합치기만 한다(막간 엔진·시간 경과 힌트가 읽음).
        #   4c에는 **무대 인물 줄만** 싣는다. 전엔 넣은 순서 앞 6이라 가장 먼저 등록된 여섯만 보였고, 떠난 인물의 옛 활동도 실렸다.
        #   무대 = scene_cast 앵커(위치 0단). 앵커가 없으면 get_onstage_npc_names(위치 미해상이면 그 안의 레거시 폴백).
        if npc_schedules and isinstance(npc_schedules, dict):
            _stage4c = anchors.get("scene_cast")
            if _stage4c is None:
                try:
                    import npc_manager as _npm_4c
                    _ch4c = anchors.get("channel_id", "")
                    _stage4c = _npm_4c.get_onstage_npc_names(_ch4c) if _ch4c else []
                except Exception:
                    _stage4c = []
            sched_items = []
            for _n4c in list(_stage4c or []):
                _k4c = domain_manager._find_npc_key(npc_schedules, str(_n4c), allow_token=False)
                _line4c = f"{_k4c}: {npc_schedules.get(_k4c)}" if _k4c and npc_schedules.get(_k4c) else ""
                if _line4c and _line4c not in sched_items:
                    sched_items.append(_line4c)
                if len(sched_items) >= 6:
                    break
            if sched_items:
                parts.append(f"- NPC Activity: {'; '.join(sched_items)}")

        world_changes = mem.get("world_changes", [])
        if world_changes:
            parts.append(f"- Recent World Changes: {'; '.join(str(w) for w in world_changes[-5:])}")  # [2026-09-24 감사]

        needs = mem.get("basic_needs_flags", {})
        active_needs = [k for k, v in needs.items() if v]
        if active_needs:
            parts.append(f"- PC Physical State (shown last turn): {', '.join(active_needs)}")   # [2026-10-11 C26] 이번 턴 판독

        residual = mem.get("residual_effects", "")
        if residual and isinstance(residual, str):
            parts.append(f"- Residual Effects (last turn): {residual}")   # [2026-10-11 C26] 이번 턴 판독(옛 "from last success")

        if not parts:
            return ""
        return "### 4c. SESSION MEMORY (Accumulated)\n" + "\n".join(parts)

    def _build_continuity_context(self, anchors: dict) -> str:
        """멀티프레임 scene continuity → ### 4d. SCENE CONTINUITY (2-tier display)"""
        mem = anchors.get("session_memory", {})
        sc = mem.get("scene_continuity", {})
        if not sc:
            return ""

        # 신/구 포맷 모두 처리
        frames = sc.get("frames", [])
        if not frames:
            old_snap = sc.get("dai_snapshot", {})
            old_fp = sc.get("render_fingerprint", {})
            if old_snap or old_fp:
                frames = [{"dai_snapshot": old_snap, "render_fingerprint": old_fp, "turn": 0}]
            else:
                return ""

        parts = ["### 4d. SCENE CONTINUITY"]

        # ── Latest frame: 풀 디테일 ──
        # [2026-10-09 묶음CDE E3] 이 프레임은 지난 턴이다(저장은 렌더 뒤). 옛 이름 "CURRENT FRAME"·스키마 "PREVIOUS FRAME"·
        #   아래 "PREVIOUS FRAMES"가 셋으로 엇갈렸다. 스냅샷(분석, 렌더 전)과 fingerprint(산문이 남긴 것, 렌더 뒤)의 머리도 가른다.
        #   스펙: analysis_line/묶음CDE_측정·스펙_2026-10-09 §5.
        latest = frames[-1]
        parts.append("#### LAST TURN")
        snap = latest.get("dai_snapshot", {})
        fp = latest.get("render_fingerprint", {})

        _sl = []
        if snap:
            if snap.get("location"):
                _sl.append(f"- Location: {snap['location']}")
            if snap.get("energy"):
                _sl.append(f"- Energy: {snap['energy']}")
            if snap.get("observation"):
                _sl.append(f"- Observation: {snap['observation']}")
            if snap.get("chain_status"):
                _sl.append(f"- Chain: {snap['chain_status']}")
        _fl = []
        if fp:
            if fp.get("gaze"):
                _fl.append(f"- Gaze: {fp['gaze']}")
            if fp.get("lighting"):
                _fl.append(f"- Lighting: {fp['lighting']}")
            if fp.get("palette"):
                _fl.append(f"- Palette: {fp['palette']}")
            if fp.get("rhythm"):
                _fl.append(f"- Rhythm: {fp['rhythm']}")
            if fp.get("temporal_density"):
                _fl.append(f"- TemporalDensity: {fp['temporal_density']}")
            unresolved = fp.get("unresolved", [])
            if unresolved:
                _fl.append(f"- Unresolved: {'; '.join(str(u) for u in unresolved[:3])}")
        if _sl:
            parts.append("read before last turn's prose:")
            parts.extend(_sl)
        if _fl:
            parts.append("as last turn's prose left it:")
            parts.extend(_fl)

        # ── Older frames: 압축 원라이너 ──
        if len(frames) > 1:
            parts.append("#### EARLIER TURNS")
            for i, frame in enumerate(reversed(frames[:-1])):
                offset = i + 1
                s = frame.get("dai_snapshot", {})
                f = frame.get("render_fingerprint", {})
                loc = s.get("location", "?")
                energy = s.get("energy", "?")
                td = f.get("temporal_density", "?")
                gaze = (f.get("gaze") or "?")[:40]
                parts.append(f"[T-{offset}] {loc} | {energy} | {td} | {gaze}")

        # ── Discontinuity flags (top-level) ──
        flags = sc.get("discontinuity_flags", [])
        if flags:
            parts.append("- DISCONTINUITY:")
            for fl in flags[:3]:
                if isinstance(fl, dict):
                    parts.append(f"  [{fl.get('type', '?')}] {fl.get('desc', '')}")

        return "\n".join(parts) if len(parts) > 1 else ""

    @staticmethod
    def _format_anomaly_seeds(seeds: list) -> str:
        """구조화 씨앗이면 상세 형식, str이면 기존 형식으로 포맷."""
        if not seeds:
            return "None"
        if isinstance(seeds[0], dict):
            lines = []
            for s in seeds:
                name = s.get("name", "?")
                tags = s.get("tags", [])
                lines.append(f"- {name} (tags:{tags})" if tags else f"- {name}")
            return "\n" + "\n".join(lines)
        return ", ".join(str(s) for s in seeds)

    def _build_lore_structured(self, lore_summary: dict) -> str:
        """lore_summary의 구조화된 데이터를 Theoria 프롬프트로 변환"""
        parts = []

        # Locations
        locations = lore_summary.get("locations", [])
        if isinstance(locations, list) and locations:
            loc_lines = []
            for loc in locations:
                if isinstance(loc, dict):
                    name = loc.get("name", "?")
                    desc = loc.get("desc", "")
                    danger = loc.get("danger", "")
                    danger_tag = f" [{danger}]" if danger else ""
                    # [2026-10-12 C30] 로어 분석이 부모를 뽑는다 — 어디 안에 있는지 한 마디(장소 트리와 같은 사실).
                    _parent = str(loc.get("parent") or "").strip()
                    in_tag = f" (in {_parent})" if _parent and _parent != name else ""
                    loc_lines.append(f"  - {name}{danger_tag}{in_tag}: {desc}")
                else:
                    loc_lines.append(f"  - {loc}")
            parts.append("- Locations:\n" + "\n".join(loc_lines))
        elif isinstance(locations, str) and locations:
            parts.append(f"- Locations: {locations}")

        # Rules
        rules = lore_summary.get("rules", [])
        if rules:
            parts.append("- World Rules:\n" + "\n".join(f"  - {r}" for r in rules))

        # Factions
        factions = lore_summary.get("factions", [])
        if factions:
            fac_lines = []
            for f in factions:
                if isinstance(f, dict):
                    fac_lines.append(f"  - {f.get('name', '?')}: {f.get('desc', '')} ({f.get('stance', '')})")
                else:
                    fac_lines.append(f"  - {f}")
            parts.append("- Factions:\n" + "\n".join(fac_lines))

        # Key Events
        events = lore_summary.get("key_events", [])
        if events:
            parts.append("- Key Events:\n" + "\n".join(f"  - {e}" for e in events))

        return "\n".join(parts) if parts else "- Locations: Current surroundings"

    def _build_chunk_index(self, chunks: list, ranked_chunks: list = None) -> str:
        """청크 라벨 인덱스를 Theoria 프롬프트에 포함 (선택용).
        ranked_chunks: [(chunk, score), ...] from vector search — 상위 청크에 ★ 마커 부여.
        """
        if not chunks:
            return ""

        # Build a set of ranked chunk indices for priority marking
        ranked_indices: dict = {}
        if ranked_chunks:
            for item, score in ranked_chunks:
                if isinstance(item, dict):
                    ridx = item.get("index")
                    if ridx is not None:
                        ranked_indices[ridx] = score

        # Reorder: ranked chunks first, then the rest
        if ranked_indices:
            ranked_first = [c for c in chunks if c.get("index") in ranked_indices]
            rest = [c for c in chunks if c.get("index") not in ranked_indices]
            ordered = ranked_first + rest
        else:
            ordered = chunks

        lines = ["### 6. LORE CHUNKS (Select relevant indices for relevant_chunks field, max 8)"]
        for chunk in ordered:
            idx = chunk.get("index", 0)
            label = chunk.get("label", f"Section {idx}")
            if idx in ranked_indices:
                lines.append(f"[{idx}] ★ {label}")
            else:
                lines.append(f"[{idx}] {label}")
        return "\n".join(lines) + "\n"

    @staticmethod
    def _build_clock_context(clocks: list) -> str:
        """활성 둠 시계 현황을 Flash 프롬프트용 텍스트로 변환."""
        active = [c for c in clocks if isinstance(c, dict) and not c.get("resolved")]
        if not active:
            return "- Situation Clocks: None"
        lines = ["- Situation Clocks:"]
        for c in active:
            filled = int(c.get("filled", c.get("progress", 0)) or 0)
            segments = int(c.get("segments", 4) or 4)
            mode_kr = {"action": "행동", "time": "시간", "hybrid": "복합"}.get(
                str(c.get("tick_mode", "")).lower(), "행동")
            threat = c.get("threat", "")
            name = c.get("name", "?")
            defense = c.get("defense_action", "")
            if defense:
                lines.append(f"  [{name} {filled}/{segments}] ({mode_kr}) → {threat} | 방어: {defense}")
            else:
                lines.append(f"  [{name} {filled}/{segments}] ({mode_kr}) → {threat}")
        return "\n".join(lines)

    @staticmethod
    def _build_condition_context(channel_id: str) -> str:
        """활성 조건 현황을 Flash 프롬프트용 텍스트로 변환 (장소별 그룹)."""
        import domain_manager
        st_state = domain_manager.get_storyteller_state(channel_id)
        conditions = st_state.get("active_conditions", [])
        if not conditions:
            return "- Active Conditions: None"
        # [2026-10-10 큐 이어짐] 조건마다 [C#](active_conditions 순서) — anomaly_profile.continues가 이 번호로 잇는다.
        #   condition_updates·condition_resolved는 지금처럼 tag로 쓴다.
        grouped: dict = {}
        for i, c in enumerate(conditions, 1):
            loc = c.get("location", "") or "Global"
            grouped.setdefault(loc, []).append((i, c))
        lines = ["- Active Conditions:"]
        for loc, conds in grouped.items():
            lines.append(f"  [{loc}]")
            for i, c in conds:
                tag = c.get("tag", "?")
                intensity = c.get("intensity", "Mid")
                polarity = c.get("polarity", "mixed")
                desc = c.get("description", "")
                lines.append(f"    - [C{i}] {tag} ({intensity}/{polarity}): {desc}")
        return "\n".join(lines)

    @staticmethod
    def _build_pending_context(channel_id: str) -> str:
        """[2026-10-10 큐 이어짐] 대기 사건(큐, 시계 완성 제외)을 [E#]로 — 같은 씨앗을 새로 내지 않고 잇게."""
        try:
            import domain_manager
            from anomaly_module import pending_events
            pend = pending_events(domain_manager.get_storyteller_state(channel_id))
        except Exception:
            return "- Pending events: None"
        if not pend:
            return "- Pending events: None"
        lines = ["- Pending events (queued, not yet fired):"]
        for i, e in enumerate(pend[:5], 1):
            lines.append(f"  [E{i}] {str(e.get('line') or e.get('tag') or '').strip()}")
        return "\n".join(lines)

    @staticmethod
    def _build_arc_context(channel_id: str) -> str:
        """
        Active arcs 현황을 Flash 프롬프트용 텍스트로 변환. spec v2 §5.

        Active arc별: declared_goal / current_phase / next_waypoint / pacing 모드 / proximity / 최근 단서.
        Flash가 다음 anomaly_seed 생성 시 정합 활용 — 같은 카테고리 시드는 흡수 가능, 새 카테고리는 신규.
        """
        try:
            import narrative_tracker as _nt
            import domain_manager
            nt_state = domain_manager.get_narrative_tracker_state(channel_id)
            active_arcs = _nt.get_active_arcs(nt_state)
        except Exception:
            return "- Active Arcs: None"

        if not active_arcs:
            return "- Active Arcs: None"

        lines = ["- Active Arcs:"]
        for arc in active_arcs:
            arc_id = arc.get("id", "?")
            decl = arc.get("declared_goal", "")
            phases = arc.get("phases", [])
            current_phase = phases[-1] if phases else "(initial)"
            next_wp = arc.get("next_waypoint", "")
            origin_cat = arc.get("origin_category", "")
            prox = arc.get("proximity", 0.0)
            pacing = arc.get("pacing", 0.3)
            mode = "crucial" if pacing >= 0.6 else "mundane"
            armed = " [ARMED]" if arc.get("armed") else ""
            lines.append(
                f"  [Arc #{arc_id}] cat={origin_cat} prox={prox:.2f} mode={mode}{armed}"
            )
            if decl:
                lines.append(f"    declared_goal: {decl}")
            lines.append(f"    current_phase: {current_phase}")
            if next_wp:
                lines.append(f"    next_waypoint: {next_wp}")

            # 최근 단서 (sensory_foreshadowing 5개)
            sens = arc.get("sensory_foreshadowing") or []
            if sens:
                recent_sens = sens[-3:]  # 최근 3개만 (토큰 budget)
                summaries = [s.get("summary", "") for s in recent_sens if isinstance(s, dict)]
                summaries = [s for s in summaries if s]
                if summaries:
                    lines.append(f"    recent_seeds: {' / '.join(summaries)}")

        return "\n".join(lines)

    # =========================================================
    # [A안 2026-07-02] 서사 방향 전용 병렬 콜 (분석 2분할)
    # 추출 콜(analyze_input, T=0.1)과 asyncio.gather 병렬 — waterfall이 결과를 DAI에 합류.
    # 소유 필드: narrative_chain / narrative_hook / offscreen_trace / scene_register  ([2026-10-09 SD개편] suggested_beats 은퇴)
    # (memory_triggers·anomaly_profile = 규칙표 결합이 강해 추출 잔류, 2차 이사 후보)
    # =========================================================

    # [2026-10-08 시점선] Mira = 시작점과 끄는 힘을 읽는다, 바닥은 산문. 초대 = 실제로 내민 것(가만한 몸은 손이 아니다), drives 예시 = 방향 동사.
    # [2026-10-09 압력 want·can] pressure = drives(want 꼴 'wants to …') + can(지금 할 수 있는 것). 옛 cannot은
    #   19개 중 13개가 이번 턴 반응을 부정형으로 정해(대답·말·돌아섬·숨) 산문 정지 목록이 됐다. drives "a verb it
    #   performs"는 이번 턴 do(답)였다. 07-22 카드1·10-08 시점선 T5c/T5d의 부분 뒤집기. 예시는 받아쓰기라 안 둔다.
    #   측정·스펙: analysis_line/압력_want·can_측정·스펙_2026-10-08 (친밀 부정문 4.0→2.1/턴, 말한 턴 2/6→8/12).
    # [2026-10-09 반응주인] 반응(이번 턴 NPC가 입력에 어떻게 답하나)의 주인 = 서사 콜 `psyche_narrative.<NPC>.response`(굴림 뒤).
    #   칸 문안으로 반응을 막으면 옆 칸으로 옮겨 갔다(drives·cannot·soma·psy — 보존). 집을 하나 준다. 거절·정지·침묵도 행위로.
    #   정체성 줄 "where each body comes to rest is the prose's"(10-08 시점선)를 뒤집었다. 렌더는 `answers:` 줄로 받는다(무엇=Mira, 어떻게=렌더).
    #   스펙·측정: analysis_line/반응주인_Mira응답칸_스펙_2026-10-09
    # [2026-10-09 소유정리] 응답 칸: 정지·침묵 나열("holding still, keeping silent")은 예시처럼 읽혀 정지 응답을 불렀다(프리즘 fg 9건 중 4건)
    #   → 거절만 행위로 명시 + 둘 중 하나 금지. M1: 본 것만·순응엔 그 사람 몫의 대가·probe는 응하되 따르지 않음 —
    #   이 규칙들이 렌더(RB_PEOPLE·RB_PC)에만 있어 답의 주인이 모른 채 답을 썼다. 렌더 쪽은 빈칸 폴백으로 남는다(짝 P4).
    #   근거: analysis_line/렌더규칙_소유지도_2026-10-09 §5 + 프리즘교차측정_소유정리_2026-10-09 §4
    # [2026-10-09 응답범위] 응답 칸 채움 범위를 pressure(두세 명)와 갈라 '이번 턴이 그 답에 걸린 한두 명'으로 좁혔다.
    #   단체 장면에서 Mira가 4명 모두에게 응답을 쓰고 배경 인물을 정지로 채워 산문이 조용해졌다(대사 3.75/턴).
    #   null 문구("this turn does not reach this body")는 그대로 둔다 — 바꾼 판에선 2인 장면 둘째 인물이 '안 낀다' 응답으로 빠졌다.
    #   측정: analysis_line/응답범위_단체장면_측정_2026-10-09 §8 (저녁 식탁 응답 4→3·대사 3.75→7.75, 거실 응답 그대로)
    # [2026-10-09 묶음B] 이번 턴 답 = response. 래퍼로 가는 칸은 '어떻게' 재료만 쓴다.
    #   render_hint(small tells) = 이 장면에서 몸이 드러나는 채널·버릇(응답 재기록 15/46·엇갈림 4/46이었다).
    #   can = 손에 든 수단(자리·쥔 것·닿는 것·몸이 허락하는 것). 응답과 같은 행위 약 40/60·고르는 꼴 13/73이었다.
    #   Theoria 스키마의 NPCKnowledge.deception_cues·continuity_check correction도 같은 날 '이미 한 말·지난 프레임 사실'로 좁혔다(굴림 전 콜).
    #   두 칸의 '이번 턴 답은 굴림 뒤' 꼬리는 뺐다 — 라이브에서 continuity 값 5건 중 3건이 그 절을 베껴 렌더로 갔다
    #   ("their answers come after the roll"). 소유는 T1 줄이 이미 말한다. 값으로 가는 칸엔 긍정 정의만 둔다.
    #   측정·스펙: analysis_line/묶음B_렌더래퍼_측정·스펙_2026-10-09
    # [2026-10-09 묶음CDE] 이번 턴 NPC 답 = response. 비트 = 세계의 다음 수·입력이 부르지 않은 NPC 자기 수(답과 엇갈린 비트 4/26).
    #   손(open_invitations) = 장면에 이미 놓인 것(NPC 상태 18/22·정지를 틈으로). roll_outcome "who answers and how" → "who answers"
    #   (말의 결은 렌더).
    #   측정·스펙: analysis_line/묶음CDE_측정·스펙_2026-10-09
    # [2026-10-09 비트 주인] NPC가 이끄는 비트는 psyche_narrative 키 이름으로 연다. 이번 턴 응답이 있는 NPC의 비트는
    #   waterfall `_screen_beats`가 착지 후보에서 뺀다(그 몸의 이번 턴 수는 response). 첫 문안([키]/[world] 꼬리표)은
    #   Mira가 괄호 없이 키 이름으로 열어 버려(라이브 3판) 그 꼴에 맞췄다. 코드는 괄호 꼬리표도 받는다.
    #   문안만으로는 착지 6턴 중 2턴이 NPC 답이었다. 근거: analysis_line/묶음CDE_구현보고_2026-10-09 §3-3
    # [2026-10-09 SD개편] 위 비트 주인은 suggested_beats와 함께 은퇴. 평소 턴의 세계 한 걸음 = narrative_hook(응답 뒤로
    #   자리를 옮김: 답을 보고 그 바깥을 쓴다 — 입력이 안 부른 몸과 세계, 머무는 턴엔 null). 언제 던지나는 Storyteller.
    #   근거: analysis_line/스토리디렉팅_개편_스펙_2026-10-09
    # [2026-10-10 끝 긍정문] 응답 "delay" → "counter-move"(보류·대기 낱말이 정지 끝을 두 배로 — 낱말표 §2). 손 = NPC·세계가 플레이어 쪽에 놓은 것, 누가 놓았나와 함께(자세·대기·
    #   'at rest'·'keep visibly open'·금지 셋 걷음 — 10-01 'PC·입력 행위 제외'는 'an NPC or the world … after the input lands'로 긍정 범위). 훅 "null when the turn holds" → "…the answers carry the turn".
    #   근거: analysis_line/산문끝_낱말표_2026-10-10 · 산문끝_긍정문정리_지도_2026-10-10
    _NARRATIVE_IDENTITY = """You are Mira, the analytical mind at this table, now on your scout pass: you range ahead of the turn and report what the world is already moving toward. Your own observation pass ran first; these are your readings from this turn, and you are free to read deeper into them.
What you look for: durable plot pressure, the world's own motion, the interpretive depth of NPC psyche. Mechanics, judgment, and prose belong to other hands. You read where the turn starts, what pulls at each body, and how each answers the input; how that answer lands on the page is the prose's.
The player character named in the request belongs to the player: that character's mind, next move, and choices are settled at the table, not read here. Every field reads the world and the NPCs around them.
Finding the smallest earned movement is the work, and finding it is what you are good at. Look widely; a reading that catches what others would pass over is worth having.
Open threads are pressure available to you, never an agenda you owe.
Where two directions both fit, take the one the scene already leans toward; the lean is in the material, not in your preference.
Hooks come from unresolved world state; where the world genuinely rests, saying so is also a finding (null hook, null trace).
Psyche interpretation stays CONSISTENT with the extract's this-turn readings (values, polyvagal, emotions) — you deepen them, never contradict them.
Every proposal stands on the supplied material (history, NPC state, sheets, extract, measurements): the scout finds paths that exist and does not lay new ones. No major events, no completed schemes, no invented antecedents."""

    # [2026-10-11 C1 logos] logos_layer 칸: 겹 이름으로 여는 틀(모든 인물이 같은 머리) → 이번 턴 PC에게 어떻게 말하나, 세 박자
    #   (누르는 것 → 둘 사이에 지나간 일이 연 정도 → 말로 나오는 꼴). 근거 analysis_line/C1_logos머리말_측정·스펙_2026-10-11 §6(lg4).
    # [2026-10-15 C40] 응답 칸: 받는 사람마다의 읽기(B — 오독 가능)·능숙한 사람은 틈을 누른다(대칭, 시트 그대로).
    #   roll_outcome 칸: 부분=모자라고 대가는 진짜, 실패·대실패=대가가 PC에게 남고 더 좁은 자리에서 끝(억까 5단계 실패 대가).
    #   판정 수치는 시계만(C4) — 활력·평형은 출력물 콜이 이 장면을 읽고 움직인다. 스펙 composition/C40_인풋해석·억까_스펙_2026-10-15.md
    _NARRATIVE_SCHEMA = """<output_schema>
Return valid JSON with EXACTLY these fields. ENGLISH telegraphic ONLY (Korean only when quoting world content).

- "roll_outcome": str | null (ENGLISH-ONLY telegraphic, one line. Only when a ROLL RESULT block is present; null otherwise. That tier made concrete in this scene: which part of the bid lands, what it costs and who bears it, who answers. Built from what the scene already holds (a wound, a lost object, a broken leverage, a witness). Partial: the bid lands short, and the cost is real. Failure and critical failure: the cost lands on the PC and stays (a wound, exhaustion, a loss, a worse position), and the PC ends the turn in a tighter spot than before. Every field below starts from this line.)
- "narrative_chain": {
    "topic_lock": str or null (the matter the talk is held on, named as the matter itself: 'the unpaid price', not who raised it. null = nothing held),
    "chain_status": "OPEN/CLOSED/DORMANT (Scheherazade: CLOSED + no threads = violation → inject hook)",
    "conclusion_proximity": 0-100,
    "open_threads": ["thread type: description", ...]  (first entry = the thread this turn presses on; each written as a standing pressure stated as fact after this turn (a debt owed, a price unnamed, a secret held), not as a question or a decision someone must make),
    "silence_type": "companionable/reflective/hesitant/heavy/tense/null (間/Ma: classify when dialogue pauses. companionable = shared ease, nothing needs saying — the usual pause of a calm scene; heavy/tense need actually loaded content)"
  }
- "open_invitations": [str, ...]  (0~2 ENGLISH-ONLY telegraphic. [H9 CUSTOM] Something an NPC or the world has put toward the PLAYER to take or refuse, named with who put it there: an object an NPC set out, a door the wind pushed open, a price an NPC named, a question an NPC asked. An NPC's hand this turn arrives through response; this lists what the scene already carries toward the player after the input lands, built from props in the scene or chain. Unlike narrative_hook (the world's own move), these are affordances for the player to take or refuse. [] when the scene extends no hand — [] is common.)
- "offscreen_trace": {"name": str, "movement": str, "visible_sign": str} or null  (Offscreen Motion [CUSTOM]: ONE absent cast member from the ABSENT CAST list only. Plausible modest motion inferred from last known state; visible_sign = the trace that reaches the CURRENT scene: rumor, delay, changed readiness, missing presence, preparation, a message. ENGLISH-ONLY telegraphic. NEVER: major events, reveals, deaths, betrayals, arrivals, completed schemes, private motives stated as fact. null when nothing plausible touches this scene — null is the common case.)
- "scene_register": "mirror" | "law" | "remainder" | null
  - mirror: character sees own trait in another without recognizing it. Name trait AND misrecognition.
  - law: protocol/hierarchy/expectation bends under input pressure. Name order, bend, who pretends not to notice.
  - remainder: what scene cannot metabolize into dialogue or action. Sensory residue, weight without plot function.
  - null: no dominant register. null is the common case — a register is what the scene TURNS ON, not a condition the words happen to satisfy. A granted request, a courtesy, an uncontested choice bends no order and mirrors nothing. Name one only when removing it would flatten the scene.
- "psyche_narrative": {
    "CharName": {
        "deep_read": "str (ENGLISH-ONLY telegraphic. Four-Layer [CUSTOM]: Surface→Adaptation→Core→Lack, 1 fragment each. Lack is never stated by character.)",
        "self_opacity": "str or null (Self-Opacity [CUSTOM]: 'claims X — actual: Y', read through their sheet. null = their self-understanding holds this turn)",
        "value_conflict": "str or null (ENGLISH-ONLY telegraphic. 'X vs Y' tension axis ONLY, no resolution narration. null = no conflict. null is the common case — a live tension needs a this-scene trigger, not a standing trait re-read each turn)",
        "resurfacing": "str or null (past trauma, contradictory desire, or 'resolved' emotion re-emerging through current interaction. What resurfaces and what triggered it. null = no resurgence)",
        "logos_layer": "str (Logos [CUSTOM], ENGLISH telegraphic: how this person talks to the acting PC this turn, in three beats: what presses at it now → how far what has passed between them has opened them toward this person → how that comes out in their words, in their sheet's voice)",
        "starts_as": "devoted/friendly/neutral/unfriendly/hostile — ONLY when 4b reads 'no record yet' for this NPC: where their sheet puts them toward the acting PC before this scene; this turn's shift comes on top and the record sets the number. Omit otherwise, and omit when the sheet's lines are not about the acting PC",
        "friction_starts": "low/strained/high/breaking — same condition: the standing friction their sheet implies. Omit otherwise",
        "pressure": {
            "drives": "str (ENGLISH telegraphic, 'wants to …', no subject, <=10 words: what this body wants to do now.)",
            "can": "str or null (ENGLISH telegraphic, <=10 words: the means at this body's hand now: where it stands, what it holds or can reach, what the body allows. What it does with them is response's.)"
        },
        "response": "str or null (ENGLISH telegraphic VERB PHRASE, no subject, <=15 words: how this body answers the input this turn, after the roll. The input's acts have already happened; answer from where they leave the body. A refusal is an answer too: write it as an act aimed at someone, with what it is for. One answer, not a choice between two. It carries only what this person has witnessed; compliance comes with a cost, condition, or counter-move of their own unless history has already paid for it; a probe is met, not obeyed. It answers the input as this person read it, through their own wants and fears; that reading can miss. A capable person presses an opening the PC gives, staying exactly as their sheet writes them. Where the input or the roll already settles it, follow that. A pull on their extract line is what presses on them; their sheet (WHO THEY ARE) is how this person moves on it. null = this turn does not reach this body.)"
    }
  }  (NPCs from the THIS-TURN EXTRACT only, keyed exactly as written there — deepen its readings, never contradict them. Omit NPCs with nothing worth deepening.
   pressure is the exception to that omission rule: fill it for the two or three of those NPCs this turn actually turns on. logos_layer goes to every NPC on the extract lines who speaks or is spoken to this turn: it carries their dialogue direction. response goes only to the one or two whose answer this turn turns on; the rest of the room stays null and the prose carries it. It is read off the scene: an NPC with no profile still has a body that wants something and something at hand. Thin background figures with no bearing on this turn stay omitted.)
- "intimacy": null OR {  (ONLY when the extract says scene=intimate; null otherwise. NPCs only, keyed as on the extract lines. SEXUAL PSYCHOLOGY lens, read through each person's sheet.)
    "desire_type": {"CharName": "attachment/power/escape/connection/validation/sensation (Basson)"},
    "dual_control": {"CharName": {"SES": "ENGLISH telegraphic - what excites", "SIS": "ENGLISH telegraphic - what brakes"}},
    "power_dynamic": "ENGLISH telegraphic (Benjamin Intersubjectivity: who leads, toward which edge)",
    "body_memory": "ENGLISH telegraphic or null (van der Kolk: involuntary echo of past experience)",
    "post_encounter_prediction": {"CharName": "possible after-behavior for this person if attachment activates — null if no change expected"}
  }
- "narrative_hook": str | null (ENGLISH-ONLY telegraphic, one line, read after the answers above: the move the world or a body the input did not reach makes this turn — weather, a door, an arrival, a standing pressure tipping, someone acting on their own. Not an answer to the input; that is response. Built from forces already in the world. When a RECENT PULLS list is present, approach from an angle it does not cover. When THIS TURN'S WORLD has something firing, that is the world's move this turn; the hook goes to what it did not reach, or stays null. null when the answers carry the turn.)
- "trait_connections": {
    "NpcName": {
        "trait_pair": "trait_A × trait_B (the two profile traits being connected this turn)",
        "primary_link": "ENGLISH-ONLY telegraphic - most obvious first link (highest probability, most cliché)",
        "deflection": "ENGLISH-ONLY telegraphic - refraction/complication/reversal. alternative reading that avoids primary",
        "render_hint": "ENGLISH-ONLY telegraphic - 1 fragment: the channel this body shows itself through in this scene, as a standing habit (a hand, a held object, a sound, a way of standing; 'when X, Y' where it turns on something). The answer itself is response's."
    }
  } | null (NPCs from the THIS-TURN EXTRACT, keyed as written there; null when no NPC traits are being actively expressed this turn)
- "newcomer_seeds": { "<name>": { "seam": str, "aside": str } }  (NEWCOMERS block only, one entry per listed name, {} otherwise.
   seam = ONE conditional sentence: the condition under which trait 1 yields to trait 2 (or the reverse). when/until, never a ranking. hint conflict → the two collide and the seam says who wins where; amplify → the seam says where the shared push tips over; free → the seam says which room each trait owns.
   aside = 2-3 lines of stage direction fusing both traits + vocabulary well + pressure + speech rule into one body's movement and habit of speech. ENGLISH telegraphic. No quoted lines, no rules, no third trait, no softening of the rolled traits. Fill only the blanks the tables delegate (a regular's topic, a home dialect of THIS world, a kind of document unread, a loved thing); invent nothing the PC says.)
</output_schema>"""

    def _build_narrative_system(self, scene_type: str = "") -> str:
        # [2026-10-10 칸이관] 친밀 장면이면 친밀 렌즈 서사판(욕망 구조·몸 기억·힘과 인정·만남 뒤 애착)을 싣는다 — intimacy 칸의 정의.
        _sp = (getattr(analysis_resources, "SEXUAL_PSYCHOLOGY_NARRATIVE", "")
               if str(scene_type or "").strip().lower() == "intimate" else "")
        return "\n\n".join(x for x in [
            "<NARRATIVE_DIRECTOR role='thread steward · world pull · offscreen continuity'>",
            self._NARRATIVE_IDENTITY,
            analysis_resources.THEORIA_CHAIN,
            # [2026-07-16 소유권 대청소] 추출 PART D에서 이사 온 서사 소유 렌즈 압축본
            getattr(analysis_resources, "NARRATIVE_CUSTOM_LENSES", ""),
            _sp,
            self._NARRATIVE_SCHEMA,
            "</NARRATIVE_DIRECTOR>",
        ] if x)

    @staticmethod
    def _world_this_turn_block(bus) -> str:
        """[2026-10-10 던지는판 A2] 이번 턴 Storyteller 결정(서사 콜 앞) → Mira 블록. 터짐·전조가 없으면 ""."""
        an = getattr(bus, "anomaly", None) if bus is not None else None
        if not isinstance(an, dict):
            return ""
        lines = []
        _fired = str(an.get("line") or "").strip() if an.get("triggered") else ""
        if _fired:
            lines.append(f"- fires: {_fired}")
        _om = an.get("omen")
        _om_line = str(_om.get("line") or "").strip() if isinstance(_om, dict) else ""
        if _om_line:
            lines.append(f"- a sign this turn: {_om_line}")
        if not lines:
            return ""
        return "### THIS TURN'S WORLD (the Storyteller has already decided this)\n" + "\n".join(lines)

    @staticmethod
    def _digest_extract(extract) -> str:
        """[A안 v2 직렬] 추출 콜 결과 → 서사 콜 입력용 컴팩트 다이제스트 (동턴 정합의 근거)."""
        if not isinstance(extract, dict) or not extract:
            return ""
        lines = []
        _st = extract.get("SceneType") or extract.get("scene_type")
        _en = extract.get("EnergyDirection") or extract.get("energy_direction")
        _im = extract.get("input_mode")
        _head = ", ".join(x for x in (
            f"scene={_st}" if _st else "",
            f"energy={_en}" if _en else "",
            f"mode={_im}" if _im else "",
            # [2026-10-01 2단계] "judgment=yes"(Flash 원 요청) 삭제 — 굴림 사실의 집 = ROLL RESULT 블록(게이트 뒤).
        ) if x)
        if _head:
            lines.append(f"- {_head}")
        _ps = extract.get("psyche_states")
        _pulls = extract.get("_autonomy_pulls")
        if isinstance(_ps, dict):
            for _name, _blk in list(_ps.items())[:6]:
                if not isinstance(_blk, dict):
                    continue
                _p = _blk.get("psyche") if isinstance(_blk.get("psyche"), dict) else {}
                _r = _blk.get("relation") if isinstance(_blk.get("relation"), dict) else {}
                _s = _blk.get("soma") if isinstance(_blk.get("soma"), dict) else {}
                _bits = []
                if _p.get("value") is not None:
                    _bits.append(f"psy {_p.get('value')}")
                if _p.get("primary_emotion"):
                    _bits.append(str(_p.get("primary_emotion")))
                if _s.get("polyvagal"):
                    _bits.append(str(_s.get("polyvagal")))
                # [2026-09-25 관계 정성] 서사 콜에도 숫자 대신 이번 턴 이동 말(추출 콜이 낸 그대로). 옛 숫자 출력이면 구간 이름.
                # [2026-10-10 칸이관] starts_as(첫 기록 시작점)는 이제 Mira 칸 — 추출 줄에 싣지 않는다.
                _bs_d, _ts_d = _r.get("bond_shift"), _r.get("tension_shift")
                if _bs_d or _ts_d:
                    _bits.append("rel " + ", ".join(x for x in (str(_bs_d) if _bs_d else "",
                                                               f"tension {_ts_d}" if _ts_d else "") if x))
                else:
                    _rb = _r.get("bond") if _r.get("bond") is not None else _r.get("value")
                    if _rb is not None:
                        try:
                            import domain_manager as _dm_dg
                            _bits.append(f"rel {_dm_dg.attitude_from_bond(_rb)}")
                        except Exception:
                            pass
                # [2026-10-10 칸이관] self_opacity는 이제 Mira 칸 — 추출 줄의 "self-opaque" 표지는 뺐다.
                # [2026-10-10 던지는판 B2] NPC 자율 트리거 → 그 몸이 끌리는 것(pull). 지시가 아니라 읽기 — 이번 턴 사는지는
                #   Mira가 response(입력이 닿은 몸)나 hook(입력이 안 닿은 몸이 스스로)으로 정한다. 렌더 지시는 은퇴.
                _pl = (_pulls.get(_name) or []) if isinstance(_pulls, dict) else []
                if _bits or _pl:
                    lines.append(f"- {_name}: " + ", ".join(_bits) + (("; " if _bits else "") + "pulls: " + ", ".join(_pl) if _pl else ""))
        if not lines:
            return ""
        return ("### THIS-TURN EXTRACT (mechanical pass, this turn — deepen these readings, never contradict)\n"
                + "\n".join(lines))

    # [2026-10-10 Mira 시트] 이번 턴 추출 줄 인물의 시트 전문 → Mira 입력. 전엔 Mira가 시트를 한 줄도 못 봤다(추출 콜도 못 봄,
    #   렌더만 통째로) — 답의 주인(10-09 반응주인)이 재료 없이 response를 써서, 받는 꼴·말하는 꼴이 모델 기본값으로 갔다
    #   (헤이젤 받는 꼴은 시트와 우연히 겹침, 클라라 "answers coolly … as she speaks"). 원천 = 렌더 프로필 정본
    #   get_npc_renderer_profiles(비밀 절·숨김 표지 제거, {{char}}/{{user}} 치환) — 렌더와 같은 글. 레티어스 "시트는 전문".
    #   측정(변형 mira_sheet_live, 5턴): Mira 콜 5.5~9.1초(기존 10.4~13.4), 응답이 시트 방식(술잔·말 삼킴·끝나고 붙어 있음,
    #   클라라 증기음)으로. 근거: analysis_line/성격·끌어당김_Mira시트_스펙_2026-10-10, Mira시트_구현보고_2026-10-10.
    _SHEET_HEAD = "### WHO THEY ARE (sheets of the characters on the extract lines: how each of them moves, speaks and answers)"

    # [2026-10-10 시트 둘] Theoria도 장면 인물 시트 전문을 본다(레티어스 "어쨌거나 둘 다 읽게"). 첫 측정(Theoria시트_측정)에서
    #   시트의 버릇이 이번 턴 상태로 굳고(헤이젤 "Words held in"), 시트 비밀이 원장에 오르고, needs 이름표가 흔들렸다 —
    #   머리의 선 긋기·CHARACTER ANALYSIS 한 줄·secrets_held 문안·active_needs 맨 낱말로 같이 고쳤다.
    _SHEET_HEAD_EXTRACT = ("### WHO THEY ARE (sheets of the scene cast: who each person is, not what this turn did — a habit or want "
                           "written here is a lean, never this turn's outcome; every reading stands on this turn's evidence, and the "
                           "sheet says what that evidence means for this person)")

    @staticmethod
    def _cast_sheet_names(anchors: dict, user_input: str = "") -> tuple:
        """Theoria 시트 대상 = SCENE CAST 앞 6. 비면(위치 기록이 늦은 턴) 입력에 이름이 나온 등록 NPC(키·괄호 안 이름·별칭).
        반환 (이름 목록, 출처 'scene_cast'|'input_names'|'')."""
        anchors = anchors or {}
        cast = [n for n in list(anchors.get("scene_cast") or [])[:6] if isinstance(n, str) and n.strip()]
        if cast:
            return cast, "scene_cast"
        ch = str(anchors.get("channel_id", "") or "")
        text = str(user_input or "")
        if not ch or not text.strip():
            return [], ""
        try:
            import npc_manager as _nm_cs
            npcs = _nm_cs.get_npcs(ch) or {}
        except Exception:
            return [], ""
        out = []
        for key, data in npcs.items():
            if not isinstance(key, str) or not key.strip():
                continue
            forms = {key.strip(), key.split("(")[0].strip()}
            if "(" in key and key.rstrip().endswith(")"):
                forms.add(key[key.index("(") + 1:key.rstrip().rindex(")")].strip())
            for _a in ((data.get("aliases") or []) if isinstance(data, dict) else []):
                if isinstance(_a, str) and _a.strip():
                    forms.add(_a.strip())
            if any(len(f) >= 2 and f in text for f in forms):
                out.append(key)
            if len(out) >= 6:
                break
        return out, ("input_names" if out else "")

    @staticmethod
    def _sheet_names(extract) -> list:
        """THIS-TURN EXTRACT 인물 줄과 같은 이름(psyche_states 앞 6, PC는 waterfall 초크포인트가 이미 뺐다)."""
        ps = extract.get("psyche_states") if isinstance(extract, dict) else None
        if not isinstance(ps, dict):
            return []
        return [n for n in list(ps.keys())[:6] if isinstance(n, str) and n.strip()]

    @classmethod
    def _sheet_block(cls, channel_id: str, names: list, user_mask: str = "", head: str = "") -> str:
        """시트 전문 블록. 등록 NPC만(PC·미등록은 프로필 정본이 건너뛴다), 없으면 ""."""
        if not channel_id or not names:
            return ""
        try:
            import npc_manager as _nm_sh
            txt = _nm_sh.get_npc_renderer_profiles(channel_id, list(names), user_mask=user_mask) or ""
        except Exception as e:
            logger.debug("[Theoria] sheet block skipped: %s", e)
            return ""
        txt = txt.strip()
        return f"{head or cls._SHEET_HEAD}\n{txt}" if txt else ""

    def _build_narrative_prompt(self, context: GameContext, extract=None, roll=None) -> str:
        req = context.request
        anchors = context.narrative_anchors or {}
        channel_id = anchors.get("channel_id", "")
        # [2026-10-01 1차] 입력 머리에 입력 모드 틀 — 서사 콜이 PC 자기 행위·입력 행위를 '손'(open_invitations)이나
        #   앞으로 올 비트로 되돌려 내놓던 것(분석쪽_붙잡이_추적 B). 모드 = 추출 콜 input_mode(_digest_extract와 같은 원천).
        #   decree·probe·빈 값 → 착지 틀 / attempt → 결과는 굴림 몫. probe는 스펙에 없어 decree 틀로(둘러보기·압박도
        #   이번 턴에 일어난다; 'outcome is the roll's'는 probe에 틀린 말). 리플레이(--call 1, decree): gv 3/5→2/4,
        #   w8e 2/4→1/4 — 부분 효과, 판정 턴 모순은 2단계(판정 → 서사 순서). 스펙 composition/분석렌더_1차_구현스펙_2026-10-01.md §2.
        _im = str((extract or {}).get("input_mode") or "").strip().lower() if isinstance(extract, dict) else ""
        # [2026-10-01 2단계] 판정 먼저 — waterfall 이 게이트·판정 뒤 굴림 사실(roll)을 넘긴다. 머리 4갈래(모드 × 굴림).
        #   roll=None 은 모름(옛 호출) → 1차 2갈래 그대로. 굴림 블록 라벨·clause = 렌더 [Outcome]·판정층과 같은 사전.
        #   스펙 composition/분석렌더_2단계_판정먼저_스펙_2026-10-01.md §3.
        _roll_block = ""
        _rolled = False
        if isinstance(roll, dict) and roll.get("active"):
            _rres = str(roll.get("result") or "")
            _rclause = (getattr(text_resources, "TELESCOPE_SEED_OUTCOME", {}) or {}).get(_rres)
            if _rclause:
                _rolled = True
                try:
                    from une_facade import RESULT_LABEL_EN, position_label
                    # [2026-10-06 어휘 V1 · P-a] 렌더와 같은 5단 낱말(Turn_Brief·판정 줄·[Outcome] 시드와 한 말)
                    _rpos = position_label(roll.get("position_value", 0.5))
                    _rlab = RESULT_LABEL_EN.get(_rres, _rres.replace("_", " "))
                except Exception:
                    _rpos, _rlab = "", _rres.replace("_", " ")
                _rpc = context.get_acting_mask() if hasattr(context, "get_acting_mask") else "the PC"
                if not _rpc or _rpc == "PC":
                    _rpc = "the PC"
                _roll_block = ("### ROLL RESULT\n" + _rlab + (f", {_rpos}" if _rpos else "") + ": "
                               + _rclause.format(pc=_rpc))
        _no_roll = isinstance(roll, dict) and not _rolled
        if _im == "attempt":
            if _rolled:
                _in_head = ("### 1. USER INPUT (the player's attempted act: the roll below has resolved it; "
                            "every forward field starts from that outcome, never before it)")
            elif _no_roll:
                _in_head = ("### 1. USER INPUT (the player's attempted act: no roll ran; the situation and the "
                            "world's logic resolve it, and every forward field starts after that)")
            else:
                _in_head = ("### 1. USER INPUT (the player's attempted act: its outcome is the roll's; "
                            "forward fields start after this turn's attempt, never before it)")
        elif _rolled:
            _in_head = ("### 1. USER INPUT (the player's stated act: it lands this turn; the roll below decides "
                        "what it yields or costs, and every forward field starts from that)")
        else:
            _in_head = ("### 1. USER INPUT (the player's stated act: it lands this turn, "
                        "and every forward field starts from the state after it)")
        parts = ["## NARRATIVE DIRECTION REQUEST", f'{_in_head}\n"{req.user_input}"']
        if _roll_block:
            parts.append(_roll_block)
        # [2026-10-10 던지는판 A2] Storyteller가 서사 콜 앞으로 와서, 이번 턴 터지는 사건·전조를 Mira가 안다.
        #   응답·hook을 그 사건에 맞춰 쓴다(hook 정의의 짝 절). 렌더 쪽 "world event:"·omen 줄과 이변 턴 hook 빼기는 그대로.
        _wb = self._world_this_turn_block(getattr(context, "shared_bus", None))
        if _wb:
            parts.append(_wb)

        # [2026-10-07 Mira PC 방향 P1] 누가 PC인지 — `!가면` 등록 이름 그대로(all_pcs mask). 규칙은 IDENTITY 가 말한다.
        #   전엔 입력 어디에도 PC 이름이 없어서(위키 페이지·기록 화자로만 등장) Mira가 PC를 읽을 인물로 다뤘다
        #   (psyche·trait 키, PC 주어 비트). 스펙 analysis_line/Mira_PC방향_수리스펙_2026-10-07.md.
        _all_pcs = anchors.get("all_pcs") or {}
        _acting_uid = anchors.get("acting_user_id", "")
        _pc_lines = []
        for _uid, _pc in (_all_pcs.items() if isinstance(_all_pcs, dict) else []):
            _m = str((_pc or {}).get("mask") or "").strip() if isinstance(_pc, dict) else ""
            if not _m or _m in ("PC", "Unknown"):
                continue
            _pc_lines.append(f"- {_m}" + (" (acting)" if len(_all_pcs) > 1 and _uid == _acting_uid else ""))
        if _pc_lines:
            parts.append("### PLAYER CHARACTER\n" + "\n".join(_pc_lines))

        _digest = self._digest_extract(extract)
        if _digest:
            parts.append(_digest)
        # [2026-10-10 Mira 시트] 인물 줄 바로 뒤에 그 인물들의 시트 전문(위 _sheet_block 주석).
        _sh_mask = context.get_acting_mask() if hasattr(context, "get_acting_mask") else ""
        _sheets = self._sheet_block(channel_id, self._sheet_names(extract),
                                    "" if _sh_mask in ("", "PC", "Unknown") else _sh_mask)
        if _sheets:
            parts.append(_sheets)

        hist = req.history_text or ""
        if hist:
            parts.append("### 2. RECENT HISTORY (tail)\n" + hist[-4000:])
        # [2026-10-10 기록 정정] 원문·회상에 걸린 정정(R5) — une_facade 가 만든 같은 블록.
        if anchors.get("corrections_block"):
            parts.append(anchors["corrections_block"])

        npc_ctx = self._build_npc_context(anchors)
        if npc_ctx:
            parts.append(npc_ctx)

        # ABSENT CAST — offscreen_trace 재료 (une_facade가 absent_for 동봉)
        absent = anchors.get("offscreen_candidates", [])
        if absent:
            parts.append(
                "### ABSENT CAST (registered, not in current scene — offscreen_trace candidates)\n"
                + "\n".join(f"- {a}" for a in absent)
            )

        # [2026-09-02 R4] NEARBY CAST — 3단 출석의 1단(근접). 스펙 §2.4 / §6 R4.
        # 레티어스: "있되 만날 수 있나 그런 정보를 건네주게." 부재(2단)와 출석(0단) 사이가
        # 그동안 통째로 비어 있어서, 옆방 사람이 "없는 사람"과 같은 칸에 들어갔다.
        # ABSENT CAST 바로 옆·같은 형식으로 둔다 — 새 슬롯 0, 새 콜 0, 서사 콜 앵커 하나 추가.
        nearby = anchors.get("nearby_cast", [])
        if nearby:
            parts.append(
                "### NEARBY CAST (reachable from the current scene, could enter)\n"
                + "\n".join(f"- {n}" for n in nearby)
            )

        # [2026-09-22 voice_seed §2 C] NEWCOMERS — 이번 턴 미등록 신규 인물의 굴린 뼈대.
        #   NEARBY CAST 바로 뒤(출석 3단 옆에 "아직 이름뿐인 사람"을 둔다). 블록 문안은
        #   voice_seed 소유 — 기전·본문만 닿고 라벨·칸 번호는 코드에 남는다(스펙 §2.1).
        #   앵커에 newcomer_rolls 가 없으면 블록 자체가 없다(프롬프트 순증 0).
        _nc_rolls = anchors.get("newcomer_rolls")
        if _nc_rolls:
            try:
                import voice_seed as _vs
                _nc_block = _vs.narrative_block(_nc_rolls)
            except Exception:
                _nc_block = ""
            if _nc_block:
                parts.append(_nc_block)

        # RECENT PULLS — 지난 턴 hook(세계 한 걸음) 반복 회피 대조 목록 (Phase 0 → [2026-10-09 SD개편] 비트 → hook)
        beats_avoid = anchors.get("recent_beats_avoid", [])
        if beats_avoid:
            parts.append(
                "### RECENT PULLS (world moves written in prior turns)\n"
                + "\n".join(f"- {b}" for b in beats_avoid)
            )

        # [Phase 0] DB 계측 — 히스토리 재탕이 아니라 측정을 급식 (실패 무해)
        try:
            import narrative_queries as _nq
            meas = []
            _pc = _nq.pacing_curve(channel_id, n=8)
            if _pc:
                meas.append(f"- pacing: {_pc}")
            _ages = _nq.thread_ages(channel_id)
            if _ages:
                meas.append("- open thread ages (turns): " + ", ".join(f"{t} [{a}]" for t, a in _ages[:6]))
            _att = anchors.get("stored_npc_attitudes", {}) or {}
            _top = sorted(
                _att.items(),
                key=lambda kv: -(abs(kv[1].get("depth", 0) or 0) if isinstance(kv[1], dict) else 0),
            )[:3]
            for _n, _ in _top:
                _arc = _nq.emotion_arc(channel_id, _n, n=12)
                if _arc:
                    meas.append(f"- emotion arc: {_arc}")
            # [2026-07-15] 감정 잔열의 출처 — 과거 spike × 그 턴의 사건 (코드측 조인).
            # emotion arc는 'spikes 3/12'로 개수만 주고 턴을 버렸다 → 언제/무엇인지 못 붙임.
            # resurfacing(psyche_narrative) 재료. 딥스키 0712 §7-2.
            try:
                _cur_turn = int(
                    (domain_manager.get_world_state(channel_id) or {}).get("turn_index", 0)
                ) if channel_id else 0
            except Exception:
                _cur_turn = 0
            if _cur_turn > 0:
                _res = _nq.emotion_residue(channel_id, _cur_turn, n=8, cap=4)
                if _res:
                    meas.append("- residue (settled spikes still owed): " + "; ".join(_res))
            # [2026-07-18 고아 승격] 태도 전이 이력 — attitude가 '왜 지금 이 값'인지
            # (read_attitude_log 소비 0이던 것을 계측 급식으로. D2 감정부채 동병동처방)
            _shifts = _nq.attitude_shifts(channel_id, n=20, cap=5)
            if _shifts:
                meas.append("- attitude shifts: " + "; ".join(_shifts))
            if meas:
                parts.append("### MEASUREMENTS (from state DB — ground pressure in these)\n" + "\n".join(meas))
        except Exception:
            pass

        # 이전 체인 스냅샷 — 연속성 (급변 아닌 이어쓰기)
        try:
            _pf = domain_manager.get_latest_frame(channel_id) if channel_id else {}
            _snap = _pf.get("dai_snapshot", {}) if isinstance(_pf, dict) else {}
            # [2026-10-09 묶음CDE E2] 스냅샷 스레드는 지난 턴 렌더 전 계획이다 — 산문이 실제로 열어 둔 것(fingerprint
            #   unresolved, 렌더 뒤)을 곁에 싣는다. 콜 0. 스펙: analysis_line/묶음CDE_측정·스펙_2026-10-09 §5.
            _fpu = _pf.get("render_fingerprint", {}) if isinstance(_pf, dict) else {}
            _un = [str(u) for u in ((_fpu or {}).get("unresolved") or []) if u][:3] if isinstance(_fpu, dict) else []
            if _snap.get("chain_status") or _snap.get("open_threads") or _un:
                _pc = "### PREVIOUS CHAIN"
                if _snap.get("chain_status") or _snap.get("open_threads"):
                    _pc += ("\n" f"- status: {_snap.get('chain_status', '')} | open: "
                            + ", ".join(str(t) for t in (_snap.get("open_threads") or [])[:5]))
                if _un:
                    _pc += "\n- left open by last turn's prose: " + "; ".join(_un)
                parts.append(_pc)
        except Exception:
            pass

        # [2026-08-11 리더 소비자] C1(fog 재조명) + C3(예측가능성 굴절) — 독자 신호 블록.
        # 지도 §9 1·3순위. 콜 0(휘발 세션 메모리 읽기), 게이트=READER_GM_FEED, 없으면 "".
        # 여기 붙는 이유: 재조명은 narrative_hook/open_invitations가 소유한 결정 —
        # 렌더러가 아니라 **방향을 정하는 콜**이 받아야 다음 턴 산문이 자연히 다시 비춘다.
        try:
            import narrative_queries as _nq_reader
            _rs = _nq_reader.reader_signal_block(channel_id) if channel_id else ""
            if _rs:
                parts.append(_rs)
        except Exception:
            pass

        # [2026-08-17 쪽지 서사 접지] 지난 턴 세계가 PC에게 보낸 문자·쪽지 (1턴 큐 소비).
        #   리더 신호 바로 옆인 이유가 같다 — 답장하지 않은 편지는 열린 초대이고, 그 결정은
        #   narrative_hook/open_invitations 소유다. 소비 후 큐는 비워진다(사건이지 상태가 아님).
        try:
            import narrative_queries as _nq_mail
            if channel_id:
                _wm = _nq_mail.world_mail_block(
                    channel_id,
                    int((domain_manager.get_world_state(channel_id) or {}).get("turn_index", 0) or 0),
                )
                if _wm:
                    parts.append(_wm)
        except Exception:
            pass

        # [2026-09-24 감사 §5-2 #27] 장르 렌즈 — 추출 콜과 같은 모듈·스포트라이트 선택의 서사 칸 매핑만.
        #   (전엔 이 매핑이 추출 콜에만 실려 생산자인 이 콜은 못 받았다.) 콜 0, 없으면 블록 0.
        try:
            from theory_emphasis_engine import build_narrative_lens_block
            _lens = build_narrative_lens_block(
                self._extract_active_genres(getattr(req, "genres", None)),
                hash(str(channel_id or "")), int(anchors.get("turn_index", 0) or 0))
            if _lens:
                parts.append(_lens)
        except Exception:
            pass

        parts.append("### OUTPUT\nReturn ONLY the JSON per schema. Render-facing fields stay English: the scene you read is Korean, your readings are not. Korean belongs inside quoted lines and proper nouns only.")
        return "\n\n".join(parts)

    async def analyze_narrative(self, context: GameContext, extract=None, roll=None) -> Dict[str, Any]:
        """서사 방향 콜 (추출 콜 *다음* 직렬 — 이번 턴 추출 다이제스트를 입력으로 받아 동턴 정합).
        실패/비정상 시 {} — waterfall이 W5 강하로 처리(폴백=현행 SD 휴리스틱)."""
        if not self.client:
            return {}

        prompt = self._build_narrative_prompt(context, extract=extract, roll=roll)
        _scene_t = str((extract.get("SceneType") or extract.get("scene_type") or "") if isinstance(extract, dict) else "")
        gen_config = types.GenerateContentConfig(
            # [2026-10-06 분석 틀] 틀은 시스템 머리에 한 번 — 역할(thread steward)은 그 뒤 자기 시스템이 말한다.
            system_instruction=f"{text_resources.ANALYSIS_FRAME}\n\n{self._build_narrative_system(_scene_t)}",
            response_mime_type="application/json",
            # [2026-07-02] 4096→8192: v2에서 psyche_narrative+trait_connections 합류로 출력 증가
            # + Ollama /v1이 thinking을 max_tokens에 포함할 가능성 대비. JSON 잘림=콜 전체 무효(비대칭 실패)라 여유 필수.
            # [2026-10-10 칸이관] 8192→12288: 네 칸(logos_layer·self_opacity·starts_as·intimacy)이 넘어와 출력이 인물 넷 장면에서
            #   +1.5~2k자. 저녁 표본 MV_pD2 T3에서 추론 27.9k자 + 출력이 상한에 걸려 JSON이 잘렸다(복구는 됐지만 hook·응답 일부 유실).
            #   추출 콜(16384)보다는 낮게. 근거: analysis_line/저녁표본_시트둘_2026-10-10.
            max_output_tokens=12288,
            temperature=config.ANALYSIS_TEMPERATURE_NARRATIVE,
            top_p=config.ANALYSIS_TOP_P,
            safety_settings=config.SAFETY_SETTINGS,
        )
        contents = [
            types.Content(role="user", parts=[types.Part(text=prompt)]),
        ]

        for attempt in range(2):
            try:
                # [2026-07-05 GLM 스왑] 서사 콜만 전용 모델로 라우팅(추출=ds-flash / 서사=GLM).
                # [2026-08-18 역할 선언] narrative 체인 = NARRATIVE>FLASH. contextvar 는 잔류.
                with config.narrative_analysis():
                    response = await self.client.aio.models.generate_content(
                        model=config.role_model("narrative"), contents=contents, config=gen_config
                    )
                if not response.text:
                    continue
                cleaned = bot_utils.clean_json_text(response.text)
                try:
                    result = json.loads(cleaned)
                except json.JSONDecodeError as je:
                    # [2026-07-27] 추출 콜과 파싱 강건성 동등화. 여기가 추출보다 얇아서
                    # (repair 1단 + 실패 컨텍스트 로그 없음 + literal_eval 폴백 없음)
                    # 같은 모델·같은 버릇인데 서사 콜만 조용히 {} 반환하고 있었다.
                    try:
                        result = json.loads(bot_utils.repair_json(cleaned))
                        logger.info("[Narrative] JSON repair succeeded")
                    except json.JSONDecodeError as je2:
                        import ast
                        try:
                            result = ast.literal_eval(cleaned)
                        except (ValueError, SyntaxError, MemoryError, RecursionError):
                            _at = max(0, getattr(je2, "pos", 0) - 60)
                            logger.error(
                                "[Narrative] JSON failed (attempt %d): %s | context: ...%s...",
                                attempt + 1, je2, cleaned[_at:_at + 160].replace("\n", " "))
                            logger.info("[빈칸] Mira JSON 실패 (시도 %d)", attempt + 1)
                            raise je
                        if not isinstance(result, (dict, list)):
                            raise je
                if isinstance(result, list) and len(result) == 1 and isinstance(result[0], dict):
                    result = result[0]
                # [2026-10-07 E2] 빈칸 감시 — EXTRACT에 인물이 있는데 psyche_narrative가 비면 한 줄(로그만).
                try:
                    _ps = (extract or {}).get("psyche_states") if isinstance(extract, dict) else None
                    if isinstance(_ps, dict) and _ps and isinstance(result, dict) and not result.get("psyche_narrative"):
                        logger.info("[빈칸] Mira psyche_narrative 빔 (EXTRACT %d명)", len(_ps))
                except Exception:
                    pass
                return result if isinstance(result, dict) else {}
            except Exception as e:
                logger.warning(f"[Narrative] attempt {attempt + 1} failed: {e}")
        logger.error("[Narrative] analysis failed after retry — narrative fields fall back to extract-only")
        logger.info("[빈칸] Mira 서사 칸 전체 빔")
        return {}

    def _build_prompt(self, context: GameContext) -> str:
        """분석 프롬프트 생성"""
        req = context.request
        anchors = context.narrative_anchors
        bus = context.shared_bus

        # lore_summary가 list로 저장된 경우 방어 (구버전 데이터 호환)
        if not isinstance(req.lore_summary, dict):
            req.lore_summary = {}

        pc_section = self._build_pc_section(anchors)
        mental_line = self._build_mental_line(anchors, bus)
        npc_context = self._build_npc_context(anchors, starts_hint=False)
        npc_roster = anchors.get("npc_roster", "")
        # [2026-06-11 소비자 감사 #6 부활] P6 원설계 배달 — NPC 능력 범위 힌트 (하드블록 아님).
        # 기존엔 Flash 콜 *후* bus.dai에 저장돼 한 번도 미배달이었음.
        _cap_hints = anchors.get("capability_hints", "")
        if _cap_hints:
            npc_roster = f"{npc_roster}\n\n#### Capability bounds (reference, not hard limits)\n{_cap_hints}"
        scene_cast_block = self._build_scene_cast(anchors)
        # [2026-10-10 시트 둘] SCENE CAST 바로 뒤에 그 인물들의 시트 전문(렌더·Mira와 같은 정본). 위 _SHEET_HEAD_EXTRACT 주석.
        _cs_names, _cs_src = self._cast_sheet_names(anchors, getattr(req, "user_input", ""))
        _cs_mask = context.get_acting_mask() if hasattr(context, "get_acting_mask") else ""
        sheet_block = self._sheet_block(str(anchors.get("channel_id", "") or ""), _cs_names,
                                        "" if _cs_mask in ("", "PC", "Unknown") else _cs_mask,
                                        head=self._SHEET_HEAD_EXTRACT)
        if _cs_names:
            logger.info("[Theoria] sheets → %s (%s) %d chars", _cs_names, _cs_src, len(sheet_block))
        session_memory_context = self._build_session_memory_context(anchors)
        continuity_context = self._build_continuity_context(anchors)
        channel_id = anchors.get("channel_id", "")

        return f"""## ANALYSIS REQUEST

### 1. USER INPUT
"{req.user_input}"

### 2. CURRENT STATE
- Genre: {req.genres}
- Chapter volume (story activity): {bus.doom.get('value', 0)}
{self._build_clock_context(bus.doom.get('clocks', []))}
{self._build_condition_context(channel_id)}
{self._build_pending_context(channel_id)}
{self._build_arc_context(channel_id)}
{mental_line}

{pc_section}

### 4. WORLD CONTEXT
- Core Theme: {req.lore_summary.get('theme', 'General TRPG')}
- Anomaly Seeds: {self._format_anomaly_seeds(req.lore_summary.get('anomaly_seeds', []))}
{self._build_lore_structured(req.lore_summary)}

{npc_context}

### 4a. NPC ROSTER (Select relevant NPCs for RelevantNPCs field, max 5)
{npc_roster or '[No NPCs registered]'}

{scene_cast_block}

{sheet_block}

{session_memory_context}

{continuity_context}

### 5. RECENT HISTORY
{req.history_text or '[No history]'}
{(chr(10) + anchors.get("corrections_block", "") + chr(10)) if anchors.get("corrections_block") else ""}
{self._build_chunk_index(req.lore_chunks, ranked_chunks=req.lore_chunks_ranked)}
---
Perform FULL Theoria analysis and return JSON with ALL required fields.
Render-facing fields stay English: the scene you read is Korean, your readings are not. Korean belongs inside quoted lines and proper nouns only.
"""
