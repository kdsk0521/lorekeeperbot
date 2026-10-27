"""
Lorekeeper TRPG Bot - Orchestration Context & Cognition Module
Handles Step 1 (Context Gathering) and Step 2 (Cognition Analysis).
Defines shared data structures for the orchestration layer.
"""

import logging
from typing import Dict, Any, Optional, List, Tuple
from dataclasses import dataclass, field

logger = logging.getLogger("OrchContext")

# =========================================================
# Shared Data Structures (UNE V4 Stateless)
# =========================================================

from dataclasses import asdict

@dataclass
class RequestData:
    user_input: str = ""
    genres: Dict[str, Any] = field(default_factory=lambda: {
        "stage": [], "flavor": [], "lens": [],
        "atmosphere": "", "mechanic": {}
    })
    active_modules: List[str] = field(default_factory=lambda: ["judgment", "doom", "anomaly", "mental"])
    lore_summary: Dict[str, Any] = field(default_factory=dict) # [V4] theme, anomaly_seeds, locations, rules, factions, key_events
    history_text: str = ""  # [V4] Recent history for THEORIA analysis
    lore_text: str = ""     # [V4] Lore reference (fallback)
    lore_chunks: List[Dict[str, Any]] = field(default_factory=list) # [V5] Labeled lore chunks for selective injection
    lore_chunks_ranked: list = field(default_factory=list)  # [N3] Vector search ranked results

    def __post_init__(self) -> None:
        """genres 필드의 하위 호환 정규화(str/list/dict → dict[stage/flavor/lens])."""
        raw = self.genres
        normalized = {
            "stage": [],
            "flavor": [],
            "lens": [],
            "atmosphere": "",
            "mechanic": {},
        }

        def _as_list(v: Any) -> List[str]:
            if isinstance(v, str):
                v = [v]
            if not isinstance(v, list):
                return []
            out: List[str] = []
            for item in v:
                s = str(item).strip()
                if not s or s in out:
                    continue
                out.append(s)
            return out

        if isinstance(raw, str):
            normalized["stage"] = _as_list(raw)
        elif isinstance(raw, list):
            normalized["stage"] = _as_list(raw)
        elif isinstance(raw, dict):
            layers = raw.get("layers", {})
            if isinstance(layers, dict):
                normalized["stage"] = _as_list(layers.get("world_setting", []))
                normalized["flavor"] = _as_list(layers.get("style_tech", []))
                normalized["lens"] = _as_list(layers.get("narrative_tone", []))

            # layers가 없거나 비어있는 경우 직접 키 사용
            if not normalized["stage"]:
                normalized["stage"] = _as_list(raw.get("stage", []))
            if not normalized["flavor"]:
                normalized["flavor"] = _as_list(raw.get("flavor", []))
            if not normalized["lens"]:
                normalized["lens"] = _as_list(raw.get("lens", []))

            atmosphere = raw.get("atmosphere", raw.get("atmosphere_guide", ""))
            normalized["atmosphere"] = str(atmosphere or "")
            mechanic = raw.get("mechanic", raw.get("mechanic_profile", {}))
            normalized["mechanic"] = mechanic if isinstance(mechanic, dict) else {}

        self.genres = normalized

@dataclass
class SharedBus:
    """
    Shared mutable state for UNE modules.
    Note: bus.*.active indicates a module ran/triggered this turn, not DLC enablement.
    """
    # dai: Theoria 분석 결과 전체 저장 (SharedBus 데이터 인터페이스)
    dai: Dict[str, Any] = field(default_factory=lambda: {
        "active": False,
        # Input Analysis
        "input_analysis": {},
        "observation": "",
        "user_intent": "",
        # Location & Scene
        "current_location": "",
        "location_risk": "Low",
        "time_context": "",
        "scene_type": "normal",
        # Stakes (BitD)
        "position": {},
        "effect": {},
        "aspects": [],
        # Judgment Support
        "needs_judgment": False,
        "action_meta": {},
        "asset_evaluation": {"reason": "", "modifications": [], "memo_relevant": []},
        # Psychological & Narrative
        "psyche_states": {},
        "narrative_chain": {},
        "memory_triggers": [],
        # DLC Support
        "narrative_hook": "",
        "time_flow": {},
        # "doom_relief" 제거 (2026-05-23) — legacy 위기진폭 잔재, 둠은 서사 진행도로 리브랜드됨
        # [2026-09-06 P8b] "mental_impact" 칸 삭제 — Theoria 스키마·dai 매핑·bus 번역이 모두
        #   사라졌다. 생산자 0 인 칸을 기본형에 남기면 "살아 있는 것처럼 보이는" 죽은 키가 된다.
        "anomaly_profile": {},
        # Safety & Debug
        "pc_autonomy_check": {},
        "temporal_orientation": {},
        "npc_attitudes": {},
        "relevant_context": []
    })
    # [2026-10-15 C42] 4dF — roll·dc·modifications 퇴역. 굴림 키(n·edge·faces·dropped·dice_sum·mod·total)는
    #   판정이 돈 턴에만 judgment_engine 이 싣는다.
    judgment: Dict[str, Any] = field(default_factory=lambda: {
        "active": False, "success": False, "narrative_hook": ""
    })
    doom: Dict[str, Any] = field(default_factory=lambda: {
        "active": False,
        "value": 0,
        "delta": 0,
        "level": 0,
        "log": "",
        "clocks": []
    })
    anomaly: Dict[str, Any] = field(default_factory=lambda: {
        "active": False, "triggered": False, "potential": False,
        "tag": "", "category": "", "intensity": "", "polarity": "",
        "line": "", "source": "", "decision": "", "decision_reason": "",
    })
    # [2026-09-06 P8b] 두 축은 **읽기 사본**이다 — 값의 정본은 custom_vars 레지스트리이고
    #   vigor_composure_module 은 읽어서 여기 싣기만 한다. 삭제된 칸: impact(AI 임팩트 소비),
    #   rest_eval/rest_log(휴식 회복), delta(모듈이 더 이상 델타를 합산하지 않는다).
    #   남은 칸: value/stage/delta_applied/log + module_active(토글) — 26곳 소비자의 계약.
    vigor: Dict[str, Any] = field(default_factory=lambda: {
        "active": False, "value": 0, "stage": 0, "last_delta": 0,
        "delta_applied": 0, "log": ""
    })
    composure: Dict[str, Any] = field(default_factory=lambda: {
        "active": False, "value": 0, "stage": 0, "last_delta": 0,
        "delta_applied": 0, "log": ""
    })
    # Phase 8: Emotion Engine (LIBRA-inspired)
    emotion: Dict[str, Any] = field(default_factory=lambda: {
        "active": False,
        "states": {},    # {npc_name: EmotionState.to_dict()}
        "summary": {},   # {npc_name: {dominant, intensity, spike}}
    })

@dataclass
class GameContext:
    request: RequestData = field(default_factory=RequestData)
    narrative_anchors: Dict[str, Any] = field(default_factory=lambda: {
        "sheet": "",
        "relations": [], "passives": [], "inventory": [], "memos": []
    })
    shared_bus: SharedBus = field(default_factory=SharedBus)

    def get_acting_mask(self) -> str:
        """현재 행동 중인 PC의 마스크명 반환."""
        anchors = self.narrative_anchors or {}
        acting_uid = anchors.get("acting_user_id", "")
        all_pcs = anchors.get("all_pcs", {})
        if acting_uid and acting_uid in all_pcs:
            return all_pcs[acting_uid].get("mask", "PC")
        return "PC"

    def to_dict(self) -> Dict[str, Any]:
        """직렬화용 사전 변환"""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'GameContext':
        """사전 데이터를 GameContext 객체로 복원"""
        req_data = RequestData(**data.get("request", {}))
        anchors = data.get("narrative_anchors", {})
        bus_data = data.get("shared_bus", {})
        
        # SharedBus mapping (handle Optionals)
        bus = SharedBus(
            dai=bus_data.get("dai", {}),
            judgment=bus_data.get("judgment", {}),
            doom=bus_data.get("doom", {}),
            anomaly=bus_data.get("anomaly", {}),
            vigor=bus_data.get("vigor", {}),
            composure=bus_data.get("composure", {}),
            emotion=bus_data.get("emotion", {}),
        )
        return cls(request=req_data, narrative_anchors=anchors, shared_bus=bus)

# Legacy Structures (To be deprecated)

@dataclass
class ResponseContext:
    """응답 생성에 필요한 컨텍스트 데이터"""
    channel_id: str
    user_id: str
    user_mask: str
    action_text: str

    # 도메인 데이터
    domain_data: Dict[str, Any] = field(default_factory=dict)
    player_data: Optional[Dict[str, Any]] = None

    # 컨텍스트 데이터
    lore_txt: str = ""
    rule_txt: str = ""
    world_ctx: str = ""
    hist_text: str = ""
    notebook_txt: str = ""
    # [2026-10-24 C74 W1-9] quest_txt 지움 — 쓰기만 하고 읽는 곳 0(죽은 쓰기).
    # [Anti-Gravity] Sliced History List
    smart_history: List[Dict] = field(default_factory=list)

    # NVC 분석 결과
    # UNE Theoria output (shared_bus.dai)
    dai: Dict[str, Any] = field(default_factory=dict)

    # 씬 타입 및 장르
    scene_type: str = "normal"
    active_genres: Any = None
    custom_tone: Optional[str] = None

    # 판정 결과
    judgment_context: str = ""
    # [2026-09-06 P3] 판정 성패 한 낱말(success/failure/…). 4.7 expr 의 `check=judgment` 가
    #   보는 유일한 창 — bus 는 process_une_logic 로컬이라 여기까지 안 온다.
    judgment_result: str = ""
    # [2026-10-15 C42] 판정 임베드 데이터(une 결과 → 4. 시스템 메시지 전송이 Embed 로 바꿔 붙인다).
    judgment_embeds: list = field(default_factory=list)

    # 발효된 요약 (V3 Hybrid)
    fermented_summary_text: str = ""
    deep_memory_data: Dict[str, Any] = field(default_factory=dict)

    # NPC 태도
    existing_attitudes: Dict[str, Dict] = field(default_factory=dict)
    
    
    # PC Impersonation Tracking
    pc_impersonation_warnings: List[str] = field(default_factory=list)

    # [2026-09-24 감사] 이번 턴 SharedBus 사본 — orchestration 하류(Arc 승격 후보·tick_arcs·CLOSURE
    #   proximity·ReaderSeed)가 `ctx.bus`/`ctx.shared_bus` 로 읽는데 필드도 대입도 없어 전부 기본값이었다.
    bus: Any = None


@dataclass
class NVCFilterConfig:
    """NVC 정보 필터링 설정 (유통기한 관리)"""
    max_attitude_age_hours: int = 24  # NPC 태도 유효 시간
    max_observation_age_turns: int = 3  # 관찰 정보 유효 턴 수
    filter_stale_data: bool = True  # 오래된 데이터 필터링 활성화


# =========================================================
# STEP 1: CONTEXT GATHERING
# =========================================================

async def gather_context(ctx: ResponseContext) -> ResponseContext:
    """필요한 모든 컨텍스트 데이터를 수집합니다."""
    import domain_manager
    import game_system
    import game_character
    import fermentation

    channel_id = ctx.channel_id
    if not isinstance(ctx.domain_data, dict):
        logger.warning("[OrchContext] domain_data is not a dict; resetting to empty")
        ctx.domain_data = {}

    # 기본 컨텍스트
    ctx.lore_txt = domain_manager.get_lore_with_npcs(channel_id)
    # [2026-07-14 경로 감사] ctx.rule_txt 조립 제거 — 소비처 0 고아 회로였음
    # (get_rules=RULES_DIR 휴면 채널·NPC 시간 힌트·world_ctx 복제 3종이 매턴 조립 후 폐기).
    # 살아있는 규칙 채널은 !룰→world_state["rules_text"]→Slot 23. NPC 시간 힌트는
    # une_facade offscreen_candidates(서사 콜 ABSENT CAST)로 재배선.
    # 상세: 파티쳇수정/memory_lore/lore_rules_path_audit_2026-07-14.md §3
    ctx.world_ctx = game_system.get_world_context(channel_id)
    # [2026-10-25 C74 판4] ctx.obj_ctx(Slot 11 목표 블록) 은퇴 — 목표는 Slot 15 `<In_Motion>` 이 장부에서 바로 읽는다.
    ctx.notebook_txt = game_system.get_notebook_text(channel_id, ctx.user_id)

    # [V10 Sprint 4] 막간 장부 — 게으른 재구성 (턴 진입 1회, 코드만, 콜 0).
    # 플래그 OFF/경과 부족이면 None → 완전 무동작. 실패해도 턴 정상 진행 (히스토리 추가 전이라
    # '마지막 game_time'은 직전 턴 기준 — 의도된 측정 지점).
    ctx.interim_ledger_block = ""
    try:
        import interim_engine
        _interim = interim_engine.reconstruct_interim(channel_id)
        if _interim:
            ctx.interim_ledger_block = _interim
    except Exception as _e_interim:
        logger.debug(f"[Interim] skip: {_e_interim}")

    # [2026-10-25 C74 판4] Slot 29 <Standing_Threads>(ctx.thread_ledger_block) 은퇴 — 약속 · 사안은 Slot 15 `<In_Motion>`.

    # [2026-09-16 3차] passives_txt 삭제 — 읽는 곳 0(조각은 Slot 6·theoria 앵커로 간다).

    # 히스토리 (스마트 컨텍스트 윈도우)
    ctx.hist_text = _build_smart_history(ctx)

    # (NPC 시간 힌트·world_ctx의 rule_txt 병합 제거 — 위 경로 감사 주석 참조)

    # 기존 NPC 태도
    ctx.existing_attitudes = domain_manager.get_npc_attitudes(channel_id)

    # [2026-07-02] channel_id_ref 배선 — build_fermented_context 내부 2곳(벡터 가중 정렬,
    # 완결 스토리라인 주입)이 이 키를 읽는데 쓰는 곳이 0이라 영구 기아였음
    # (벡터 캐시는 실채널 키로 적립·소비는 ""로 조회 → 항상 키워드 폴백,
    #  archived_storylines는 if 가드로 통째 스킵). lore_summary_data와 같은 주입 패턴.
    ctx.domain_data["channel_id_ref"] = channel_id

    # [V10 P3 / 2026-09-05] fermented/deep 읽기 read-through — build_fermented_context에
    # 넘기기 직전 세 키를 행 값으로 덮는다. 플래그 OFF면 게터가 JSON을 그대로 돌려준다.
    ctx.domain_data["fermented_history"] = domain_manager.get_fermented_history(channel_id)
    _dn, _dd = domain_manager.get_deep_memory(channel_id)
    ctx.domain_data["deep_memory"] = _dn
    ctx.domain_data["deep_memory_data"] = _dd

    # 발효 요약 (V3 Hybrid - Mneme/Psyche)
    # build_fermented_context expects session_data dict, not separate args
    ctx.fermented_summary_text = fermentation.build_fermented_context(
        ctx.domain_data,  # 전체 세션 데이터 전달
        query=ctx.action_text
    )
    # Store deep_memory_data for prompt builder
    ctx.deep_memory_data = ctx.domain_data.get("deep_memory_data", {})

    # 장르/톤
    ctx.active_genres = domain_manager.get_active_genre_list(channel_id)
    ctx.custom_tone = domain_manager.get_custom_tone(channel_id)

    # [V4] Lore Summary Data (for UNE)
    ctx.domain_data["lore_summary_data"] = domain_manager.get_lore_summary_data(channel_id)

    return ctx


def _estimate_tokens(text: str) -> int:
    """간이 토큰 추정: 한글 1자≈1.5tok, ASCII 4자≈1tok (Gemini BPE 근사)"""
    korean = sum(1 for c in text if '\uAC00' <= c <= '\uD7A3' or '\u3131' <= c <= '\u318E')
    ascii_chars = len(text) - korean
    return int(korean * 1.5 + ascii_chars * 0.25)


def _build_smart_history(ctx: ResponseContext) -> str:
    """스마트 컨텍스트 윈도우로 히스토리를 구성합니다."""
    import fermentation
    all_hist = ctx.domain_data.get('history', [])
    # [2026-10-27 C86] 창 = 안 삭힌 원문 전부(도메인 history 엔 안 삭힌 것만 남는다) — 창과 발효를 맞물려 공백 0.
    #   안전 상한(글자)만 오래된 것부터 뺀다(F1 실패가 이어져 history 가 불어날 때). 끄면 옛 12 창.
    try:
        import config as _cfg_c86
        if getattr(_cfg_c86, "FERMENT_TRIGGER_V2", False):
            _cap = int(getattr(_cfg_c86, "RENDER_WINDOW_MAX_CHARS", 60000) or 0)
            _hist = [h for h in all_hist if isinstance(h, dict)]
            _floor = max(2, int(getattr(_cfg_c86, "UNFERMENTED_MIN", 12)))   # 최근 몫은 상한과 상관없이(옛 창도 늘 12)
            if _cap > 0:
                _tot = sum(len(str(h.get("content") or "")) for h in _hist)
                while len(_hist) > _floor and _tot > _cap:
                    _h0 = _hist.pop(0)
                    _tot -= len(str(_h0.get("content") or ""))
            ctx.smart_history = _hist
            return "\n".join([f"{h.get('role', '')}: {h.get('content', '')}" for h in _hist])
    except Exception as _e_c86:
        logger.debug(f"[Context] C86 window skip: {_e_c86}")
    target_tokens = 10000  # [Cost-Diet] 6턴(12메시지) 수준. 장기 맥락은 fermented_history가 커버.
    default_lines = 12  # 6턴 = 12메시지. 분석용(RECENT_HISTORY_FOR_ANALYSIS=30)과 독립.
    slice_idx = -default_lines

    while True:
        if abs(slice_idx) > len(all_hist):
            slice_idx = -len(all_hist)

        subset = all_hist[slice_idx:]
        hist_text = "\n".join([f"{h['role']}: {h['content']}" for h in subset])

        if _estimate_tokens(hist_text) >= target_tokens or abs(slice_idx) >= len(all_hist):
            break

        slice_idx -= 5

    history = all_hist[slice_idx:]
    ctx.smart_history = history # [Anti-Gravity] Store sliced list for response generation
    return "\n".join([f"{h['role']}: {h['content']}" for h in history])
