"""
Lorekeeper UNE - Waterfall Pipeline
Orchestrates the sequence of narrative analysis and mechanical updates.
"""

import logging
from typing import Dict, Any
from orchestration_context import GameContext, SharedBus
from theoria_analyzer import TheoriaAnalyzer
from vigor_composure_module import VigorComposureModule
from judgment_engine import JudgmentEngine
from anomaly_module import AnomalyModule
from doom_module import DoomModule
from judgment_gate import gate_judgment
from emotion_engine import EmotionEngine, EmotionState
from story_director import StoryDirector
import domain_manager
import voice_seed  # [2026-09-22] 세션 NPC 시드 — 게이트·굴림·버퍼(배선 스펙 §2 A·D·F)

logger = logging.getLogger("Waterfall")


# =========================================================
# P6: 인과 구속 힌트 (Flash에 능력 범위 주입)
# =========================================================
def _inject_capability_hints(npc_profiles: dict) -> str:
    """NPC별 능력 범위 요약을 Flash 프롬프트에 주입.
    하드블록 아님. Flash가 참조할 수 있는 힌트만."""
    hints = []
    for name, profile in npc_profiles.items():
        if not isinstance(profile, dict):
            continue
        caps = profile.get("capabilities", {})
        static_traits = profile.get("static_traits", {})

        parts = []
        if caps:
            strengths = caps.get("strengths", "?")
            limits = caps.get("limits", "?")
            parts.append(f"can={strengths} | cannot={limits}")
        if static_traits:
            # [2026-07-28] 기본값은 힌트로 내보내지 않는다 — coping_style이 구조적으로
            # 항상 "adaptive"였던 탓에 **전 NPC에 같은 문구**가 매 턴 나가고 있었다
            # (빈 값보다 나쁘다: Flash가 "이 인물은 적응적/중립적"이라고 읽는다).
            # 패턴 보강으로 이제 실제 값이 나오지만, 기본값이면 침묵이 맞다.
            coping = static_traits.get("coping_style", "")
            moral = static_traits.get("moral_stance", "")
            _bits = []
            if coping and coping != "adaptive":
                _bits.append(f"coping={coping}")
            if moral and moral != "neutral":
                _bits.append(f"moral={moral}")
            if _bits:
                parts.append(", ".join(_bits))

        if parts:
            hints.append(f"[{name}] {' | '.join(parts)}")

    return "\n".join(hints) if hints else ""


def _pc_masks(context) -> set:
    """이번 턴 PC 마스크 집합 — DAI PC 정제(아래 ★단일 정제)와 시드 게이트의 **같은 원천**.

    [2026-09-22 voice_seed §2 A 2] 시드 게이트는 자기 명단을 따로 만들지 않는다.
    한쪽만 고쳐져 두 판정이 갈리는 걸 막으려고 원천을 함수 하나로 묶었다."""
    anchors = getattr(context, "narrative_anchors", None) or {}
    try:
        masks = {p.get("mask") for p in (anchors.get("all_pcs", {}) or {}).values()
                 if isinstance(p, dict) and p.get("mask")}
    except Exception:
        return set()
    masks.discard("")
    return masks


def _turn_now(context) -> int:
    """[2026-09-22] 시드 TTL·born_turn 시계. 아래 판정 게이트 current_turn 과 같은 읽기 순서
    (세션 메모리 turn_count → world_state turn_index)."""
    anchors = getattr(context, "narrative_anchors", None) or {}
    try:
        t = (anchors.get("session_memory") or {}).get("turn_count", 0)
        if not t:
            t = domain_manager.get_world_state(anchors.get("channel_id", "")).get("turn_index", 0)
        return int(t or 0)
    except Exception:
        return 0


def load_last_judgment_turn(channel_id: str, actor: str = "") -> int:
    """[2026-09-16 3차] Rule2 쿨다운 정본 = 채널 world_state["last_judgment_turn"](bus 는 매턴 신조).

    [2026-09-24 감사] **PC(uid)별**로 갈랐다 — 채널 공용 한 칸이면 `!진행` 배치에서 첫 PC 저장 뒤
      둘째 PC가 같은 턴 diff 0 ≤ 1 로 차단되고, 솔로 다인 채널에서도 A 가 굴린 다음 턴 B 가 막혔다.
      옛 int 값은 actor 무관 공용값으로 읽는다(이행 호환).
    """
    try:
        if channel_id:
            v = domain_manager.get_world_state(channel_id).get("last_judgment_turn", -10)
            if isinstance(v, dict):
                return int(v.get(str(actor or ""), -10))
            return int(v)
    except Exception:
        pass
    return -10


def save_last_judgment_turn(channel_id: str, turn: int, actor: str = "") -> None:
    try:
        if channel_id:
            ws = domain_manager.get_world_state(channel_id)
            cur = ws.get("last_judgment_turn")
            m = dict(cur) if isinstance(cur, dict) else {}
            m[str(actor or "")] = int(turn)
            ws["last_judgment_turn"] = m
            domain_manager.update_world_state(channel_id, ws)
    except Exception as e:
        logger.debug(f"[Gate] last_judgment_turn 저장 skip: {e}")


def judgment_snapshot(j: dict):
    """[2026-09-16 3차 §10.3] turn_snapshot `judgment` dict — 판정이 돈 턴만(게이트가 막은 턴은 gate 만).

    terms 는 0 포함 전 항 — 합 = final − roll(불변식). 판정 요청이 없던 턴은 None."""
    j = j if isinstance(j, dict) else {}
    if not j.get("active"):
        return {"gate": j.get("gate_reason", "")} if j.get("gate_requested") else None
    return {
        "roll": j.get("roll"), "final": j.get("final_roll"), "dc_base": j.get("dc_base"),
        "dc_pos_mod": j.get("position_dc_mod"), "result": j.get("result"),
        "terms": dict(j.get("terms") or {}), "active_passives": list(j.get("active_passives") or []),
        "crit_reason": j.get("crit_reason", ""), "effort_cost": j.get("effort_cost", 0),
        "gate": j.get("gate_reason", ""),
    }


def _degrade_stage(bus, stage_name: str, error: Exception) -> None:
    """W5: Record pipeline degradation and apply fallback from config table."""
    try:
        import config as _cfg
        rule = _cfg.PIPELINE_DEGRADATION.get(stage_name, {})
        behavior = rule.get("absent_behavior", "skip")
        logger.warning("[Degradation] %s → %s: %s", stage_name, behavior, error)
        degraded = bus.dai.setdefault("_degraded_stages", [])
        degraded.append({"stage": stage_name, "behavior": behavior, "error": str(error)[:200]})
        # Apply fallback DAI values if present
        for k, v in rule.get("fallback_dai", {}).items():
            if k not in bus.dai or not bus.dai[k]:
                bus.dai[k] = v
    except Exception as inner:
        logger.error("[Degradation] Handler itself failed for %s: %s", stage_name, inner)


# =========================================================
# [2026-08-11 soma 지속] B축 몸 상태 병합 (순수 함수 — I/O 0)
# =========================================================
def _write_emotion_tracker(channel_id, prev_emotions, emotion_results, current_turn, context) -> None:
    """[2026-09-15 §12] world_state `npc_emotion_states` 병합 쓰기 — 본체는 `EmotionEngine.merge_tracked_states`.
    사망 이름 = anchors `npc_inactive`의 dead(down은 유지). 값이 같으면 쓰지 않는다."""
    _inactive = (getattr(context, "narrative_anchors", None) or {}).get("npc_inactive", {}) or {}
    _dead = [n for n, v in _inactive.items() if str(v).lower() == "dead"] if isinstance(_inactive, dict) else []
    merged = EmotionEngine.merge_tracked_states(prev_emotions or {}, emotion_results or {}, current_turn, _dead)
    world = domain_manager.get_world_state(channel_id)
    if world.get("npc_emotion_states") != merged:
        world["npc_emotion_states"] = merged
        domain_manager.update_world_state(channel_id, world)


def _merge_soma_states(prev: dict, psyche_states: dict, current_turn: int) -> tuple[dict, list]:
    """이번 턴 psyche 관측분을 이전 턴 soma 스냅샷에 **NPC별로 병합**하고,
    상태가 실제로 뒤집힌 NPC만 전이 리스트로 돌려준다.

    반환: (새 npc_soma_states, [(npc, from_polyvagal, to_polyvagal,
                                from_dissociation, to_dissociation), ...])

    ★I/O 0 — world_state 읽기/쓰기도, soma_log 적립도 여기서 하지 않는다(호출부 책임).

    [2026-08-02 B축 지속] 왜 스냅샷을 남기나.
      `dissociation` 스키마엔 **"Track across turns"**가 이미 적혀 있는데
      이전 턴 값이 어디에도 남지 않아 **집행 재료 없는 사문 지시**였다.
      (npc_emotion_states엔 Plutchik 8축만, PREVIOUS FRAME엔 위치/에너지/밀도/시선만)
      ★새 게이지를 만들지 않는다 — 이미 있는 enum을 다음 턴에 되돌려줄 뿐.

    [2026-08-11 soma 지속] 통짜 교체 → **NPC별 병합 + since_turn 도장**.
      구 코드는 `world["npc_soma_states"] = _soma_snap`으로 dict를 통째 갈아서
      (a) 이번 턴 psyche에 없는 NPC = **증발**(안 만나면 몸 상태 기억이 사라짐)
      (b) 같은 상태가 언제부터인지 = 알 방법 없음(1턴 창).

    오프스테이지 잔존: 이번 턴 관측분으로 덮되, 못 본 NPC는 그대로 둔다.
      그래서 전이 로그도 **관측 턴에만** 쌓인다 — 잔존은 사건이 아니다.

    시계 문법: A축=등장 / C축=무변화 / **B축=무변화**(같은 상태가 언제부터냐).
      ★무변화면 도장을 안 찍는다 — npc_manager.set_drive_gated L1946과 같은 규율.
        동일 상태에 재도장하면 지속 시계가 매 턴 리셋돼 영원히 임계를 못 넘는다.

    ⚠`psyche_states`는 **PC 제외본**을 넘겨야 한다(execute()의 `_pc_masks_em` 필터 결과).
      bus.dai 원본은 PC를 품고 있어, 잔존 배선과 만나면 PC 몸 상태가 **영구**
      잔류한다(통짜 교체로 매 턴 증발하던 시절엔 무해했다).
    """
    _psy = psyche_states or {}
    _soma_prev = prev if isinstance(prev, dict) else {}
    _soma_snap = {k: dict(v) for k, v in _soma_prev.items()
                  if isinstance(v, dict)}
    _soma_moves = []
    for _sn, _sblk in _psy.items():
        if not isinstance(_sblk, dict):
            continue
        _s = _sblk.get("soma")
        if not isinstance(_s, dict):
            continue
        _keep = {k: _s.get(k) for k in ("polyvagal", "dissociation")
                 if _s.get(k)}
        if not _keep:
            continue
        _old = _soma_prev.get(_sn)
        if not isinstance(_old, dict):
            _old = None
        _same = bool(_old) and all(
            (_old.get(_k) or None) == (_keep.get(_k) or None)
            for _k in ("polyvagal", "dissociation")
        )
        if _same:
            _keep["since_turn"] = int(_old.get("since_turn", current_turn) or 0)
        else:
            _keep["since_turn"] = int(current_turn)
            # 전이 로그는 **관측 턴에만** — 오프스테이지 잔존은 사건이 아니다.
            _soma_moves.append((
                _sn,
                (_old or {}).get("polyvagal"), _keep.get("polyvagal"),
                (_old or {}).get("dissociation"), _keep.get("dissociation"),
            ))
        _soma_snap[_sn] = _keep
    return _soma_snap, _soma_moves



def _relation_view(psyche_states, stored: dict = None) -> dict:
    """[2026-09-15 관계 통합] psyche_states[NPC].relation → {NPC: {attitude, reason, bond, trajectory}} 파생.
    bond 없으면 옛 value 폴백. trajectory = 저장 엣지 대비 이번 판독의 부호(domain_manager.trajectory_from_delta).
    저장하지 않는다."""
    out = {}
    if not isinstance(psyche_states, dict):
        return out
    try:
        from domain_manager import attitude_from_bond, trajectory_from_delta
    except Exception:
        return out
    for _n, _st in psyche_states.items():
        _rel = _st.get("relation") if isinstance(_st, dict) else None
        if not isinstance(_n, str) or not isinstance(_rel, dict):
            continue
        _b = _rel.get("bond", _rel.get("value"))
        try:
            _b = int(float(_b)) if _b is not None and not isinstance(_b, bool) else None
        except (TypeError, ValueError):
            _b = None
        if _b is None:
            continue
        # depth/tension은 싣지 않는다 — 소비부(une_facade 자율 병합)가 저장 엣지 값으로 채운다(구 동작 유지).
        _prev = (stored or {}).get(_n) if isinstance(stored, dict) else None
        _traj = (trajectory_from_delta(_b - int(_prev.get("bond", _prev.get("depth", 0)) or 0))
                 if isinstance(_prev, dict) else "stable")
        out[_n] = {"attitude": attitude_from_bond(_b), "reason": str(_rel.get("descriptor") or ""),
                   "bond": _b, "trajectory": _traj}
    return out

class WaterfallPipeline:
    def __init__(self, client, model_id: str):
        self.theoria = TheoriaAnalyzer(client, model_id)
        # These will be lazy loaded or injected
        self.judgment = None
        self.doom = None
        self.anomaly = None
        self.vigor_composure = None

    def _ensure_bus_schema(self, bus: SharedBus) -> None:
        """
        Ensure SharedBus has required dict fields without overwriting existing values.
        This is a schema guard for DLC on/off during runtime.
        """
        defaults = SharedBus()
        for key in ("dai", "judgment", "doom", "anomaly", "vigor", "composure", "emotion"):
            current = getattr(bus, key, None)
            if current is None or not isinstance(current, dict):
                setattr(bus, key, getattr(defaults, key))

    async def execute(self, context: GameContext) -> GameContext:
        """Analysis -> Mental(pre) -> Judgment -> Storyteller -> Doom -> Mental(sync)

        All core modules (Doom, Anomaly, Mental, Judgment) are always active.
        Judgment trigger is gated by judgment_gate (N1).

        Data-flow map (SharedBus ownership):
        - Theoria: bus.dai (all analysis), bus.judgment, bus.doom
        - Mental(pre): stage snapshot only (no delta consumption)
        - Judgment: resolves action, writes consequences (doom.delta, primary axis delta, clock effects, momentum)
        - Storyteller: bus.anomaly.triggered/tag/decision (narrative only, no deltas)
        - Doom: bus.doom.value/delta/log, consumes judgment doom_delta, may write vigor/composure pressure delta
        - Mental(sync): consumes ALL accumulated deltas (judgment + doom pressure + rest + status)
        """

        self._ensure_bus_schema(context.shared_bus)
        
        # 1. [A안 v2 2026-07-02 직렬] 추출 콜(냉, 기계 필드) → 서사 콜(온, 방향+심리해석 필드).
        # 병렬→직렬 전환(레티어스 "지연 감수"): 서사 콜이 이번 턴 추출 다이제스트를 입력으로 받아
        # 동턴 정합 확보 — 2차 이사(deep_read 등 심리 해석층)의 전제. W5 강하는 콜별 독립 유지.
        bus = context.shared_bus
        try:
            analysis = await self.theoria.analyze_input(context)
        except Exception as e:
            _degrade_stage(bus, "theoria_analysis", e)
            analysis = {}

        # Safety: Gemini가 JSON 배열을 반환하면 첫 번째 요소를 사용
        if isinstance(analysis, list):
            logger.warning(f"[Theoria] Returned list instead of dict, extracting first element")
            analysis = analysis[0] if analysis and isinstance(analysis[0], dict) else {}
        if not isinstance(analysis, dict):
            logger.error(f"[Theoria] Invalid response type: {type(analysis)}")
            analysis = {}
        # [2026-09-24 감사] analyze_input 은 파싱 실패·빈 응답·API 예외를 raise 하지 않고
        #   `{"error": ...}` 로 반환한다 → 위 except 를 안 타서 W5 강하 기록(_degraded_stages·Slot 16 고지)이
        #   주된 실패 모드에서 침묵했다. 오류 전용 dict 는 강하로 기록하고 빈 분석으로 바꾼다.
        if set(analysis.keys()) == {"error"}:
            _degrade_stage(bus, "theoria_analysis", RuntimeError(str(analysis.get("error"))[:200]))
            analysis = {}

        # [2026-09-22 voice_seed §2 A] 미등록 신규 인물 게이트 + 굴림 — 서사 콜 **앞**.
        #   새 콜 0(전부 코드). 굴림은 서사 콜 입력(§2 C NEWCOMERS 블록)의 재료가 된다.
        #   시드는 best-effort다 — 어디서 터져도 예외를 삼키고 턴은 그대로 간다.
        _seed_ch = (context.narrative_anchors or {}).get("channel_id", "")
        _seed_turn = _turn_now(context)
        _seed_rolls = {}
        try:
            voice_seed.tick(_seed_ch, _seed_turn)          # TTL 퍼지(게이트 직전, §2 F)
            _seed_rolls = voice_seed.gate_and_roll(
                _seed_ch, analysis.get("RelevantNPCs"), _pc_masks(context)) or {}
            # 빈 dict면 앵커 키 자체를 안 넣는다 — 서사 콜 프롬프트 순증 0(§2 C).
            if _seed_rolls and isinstance(context.narrative_anchors, dict):
                context.narrative_anchors["newcomer_rolls"] = _seed_rolls
        except Exception as _e_seed:
            logger.warning("[Seed] gate/roll skipped: %s", _e_seed)
            _seed_rolls = {}

        try:
            narrative = await self.theoria.analyze_narrative(context, extract=analysis)
        except Exception as e:
            _degrade_stage(bus, "narrative_analysis", e)
            narrative = {}
        if not isinstance(narrative, dict):
            narrative = {}

        # [A안] 서사 콜 결과 합류 — 서사 필드는 narrative 콜이 소유 (추출 스키마에서 제거됨).
        # 서사 콜 실패 시 키 부재 → 아래 전개가 기존 디폴트({}/[]/None) 적용 = 현행 강하와 동일 동작.
        if narrative:
            _merge_keys = ("narrative_chain", "suggested_beats", "narrative_hook",
                           "open_invitations",  # [H9 2026-07-18] 플레이어향 전방 affordance
                           "offscreen_trace", "scene_register", "trait_connections",
                           "newcomer_seeds")  # [2026-09-22 voice_seed §2 D 1] 여기 빠지면 증발한다
            for _nk in _merge_keys:
                _nv = narrative.get(_nk)
                if _nv is not None:
                    analysis[_nk] = _nv

            # [2차 이사] psyche_narrative → psyche_states per-NPC 병합
            # (deep_read/resurfacing=톱레벨, value_conflict=relation 내부 — 하류 소비 형태 그대로)
            _pn = narrative.get("psyche_narrative")
            _ps = analysis.get("psyche_states")
            if isinstance(_pn, dict) and isinstance(_ps, dict) and _ps:
                def _match_npc(name: str):
                    if name in _ps:
                        return name
                    _b = name.split("(")[0].strip().lower()
                    for _k in _ps:
                        _kb = _k.split("(")[0].strip().lower()
                        if _kb == _b or _b in _k.lower() or _kb in name.lower():
                            return _k
                    return None
                for _pn_name, _pn_blk in _pn.items():
                    if not isinstance(_pn_blk, dict):
                        continue
                    _tgt = _match_npc(str(_pn_name))
                    if not _tgt or not isinstance(_ps.get(_tgt), dict):
                        continue
                    if _pn_blk.get("deep_read"):
                        _ps[_tgt]["deep_read"] = _pn_blk["deep_read"]
                    if _pn_blk.get("resurfacing") is not None:
                        _ps[_tgt]["resurfacing"] = _pn_blk["resurfacing"]
                    if _pn_blk.get("value_conflict") is not None:
                        _rel = _ps[_tgt].setdefault("relation", {})
                        if isinstance(_rel, dict):
                            _rel["value_conflict"] = _pn_blk["value_conflict"]
                    # [2026-07-22 카드1] pressure(drives/cannot) 병합 — 렌더러가 받는 "감정이 무엇을
                    # 하게 만드는가". LLM이 명시 null로 줄 수도 있으므로 setdefault 금지·isinstance 정규화.
                    _pr = _pn_blk.get("pressure")
                    if isinstance(_pr, dict):
                        _drives = _pr.get("drives")
                        _cannot = _pr.get("cannot")
                        _clean = {}
                        if isinstance(_drives, str) and _drives.strip() and _drives.strip().lower() != "null":
                            _clean["drives"] = _drives.strip()
                        if isinstance(_cannot, str) and _cannot.strip() and _cannot.strip().lower() != "null":
                            _clean["cannot"] = _cannot.strip()
                        if _clean:
                            _ps[_tgt]["pressure"] = _clean

            logger.info("[Narrative] merged: "
                        + ", ".join(k for k in _merge_keys if narrative.get(k) is not None)
                        + (f" + psyche_narrative({len(_pn)})" if isinstance(_pn, dict) and _pn else ""))

        # Store ALL Theoria results in SharedBus.dai (replaces nvc_result)
        bus.dai["input_analysis"] = analysis.get("InputAnalysis", {})
        bus.dai["observation"] = analysis.get("Observation", "")
        bus.dai["user_intent"] = analysis.get("UserIntent", "")
        bus.dai["current_location"] = analysis.get("CurrentLocation", "")
        # [2026-09-02 R1] 위치 계층 재료 — 스펙 §2.5 ⓑ. 자동 노드 생성이 parent 미지정이라
        #   전부 루트로 앉던 구멍(§4)을 메우려면 "루트부터의 경로"가 필요하다.
        #   선택 필드라 부재가 상례 → `or []` / `or ""`로 **명시 null도 누락과 같게** 받는다.
        _lp = analysis.get("location_path")
        bus.dai["location_path"] = _lp if isinstance(_lp, list) else []
        bus.dai["location_type"] = str(analysis.get("location_type") or "")
        bus.dai["location_risk"] = analysis.get("LocationRisk", "Low")
        bus.dai["time_context"] = analysis.get("TimeContext", "")
        bus.dai["scene_type"] = analysis.get("SceneType", "normal")
        bus.dai["energy_direction"] = analysis.get("EnergyDirection", "idle")  # 2026-06-25: 디폴트 rising→idle. omission/불확실 시 긴장을 만들지 않음(둠·톤·페이싱 안전쪽). Theoria가 보통 채움.
        bus.dai["quality_flags"] = analysis.get("QualityFlags", {})
        bus.dai["position"] = analysis.get("Position", {})
        bus.dai["effect"] = analysis.get("Effect", {})
        bus.dai["aspects"] = analysis.get("Aspects", [])
        bus.dai["psyche_states"] = analysis.get("psyche_states", {})
        bus.dai["narrative_chain"] = analysis.get("narrative_chain", {})
        # SD-Bb2 (2026-04-22): Theoria author-hint beats (휴리스틱 비트 보강용, 필수 아님)
        _sb_raw = analysis.get("suggested_beats", [])
        if isinstance(_sb_raw, list):
            bus.dai["suggested_beats"] = [str(b).strip() for b in _sb_raw if isinstance(b, str) and b.strip()]
        else:
            bus.dai["suggested_beats"] = []
        # [2026-07-02 Offscreen Motion — 뮈토스 이식] 부재 캐스트 흔적 (dict or null, null이 상례)
        _ot_raw = analysis.get("offscreen_trace")
        bus.dai["offscreen_trace"] = _ot_raw if isinstance(_ot_raw, dict) else None
        bus.dai["scene_register"] = analysis.get("scene_register")
        bus.dai["input_mode"] = analysis.get("input_mode", "decree")
        bus.dai["memory_triggers"] = analysis.get("memory_triggers", [])
        bus.dai["narrative_hook"] = analysis.get("narrative_hook", "")
        # [2026-09-24 감사] H9 open_invitations 전개 누락 복구 — 위 _merge_keys 로 analysis 에는 합류했으나
        #   bus.dai 매핑이 없어 아래 정규화가 [] 로 채웠다 → Slot 16 translate_open_invitations 영구 빈손(07-18~).
        bus.dai["open_invitations"] = analysis.get("open_invitations") or []
        bus.dai["time_flow"] = analysis.get("TimeFlow", analysis.get("time_flow", {}))
        bus.dai["doom_clocks"] = analysis.get("doom_clocks", {})
        # doom_relief 제거 (2026-05-23) — legacy 위기진폭 잔재
        # [2026-09-06 P8b] mental_impact 매핑 삭제 — Theoria 스키마에서 필드가 사라졌다.
        #   옛 모델이 그 키를 계속 보내도 매핑이 없으니 bus 에 실리지 않는다(무시 = 정규화).
        bus.dai["anomaly_profile"] = analysis.get("anomaly_profile", {})
        bus.dai["pc_autonomy_check"] = analysis.get("PCAutonomyCheck", {})
        bus.dai["temporal_orientation"] = analysis.get("TemporalOrientation", {})
        # [2026-09-15 관계 통합] NPCAttitudes 질문 삭제 — 이 버스 키는 이제 **이번 턴 relation 층의 파생 뷰**
        #   (attitude=bond 구간, reason=descriptor). 저장 아님(저장은 orchestration의 write_theoria_relations → 엣지).
        #   하류(slot iceberg·자율 트리거·world_board·presence) 소비 모양 유지용.
        # [2026-09-25 관계 한 숫자 — 레티어스 "한 턴 안에 두 숫자가 따로 노는 건 위험"] + [관계 정성]
        #   Theoria relation 을 **이번 턴 저장될 값**으로 먼저 맞춘다: 이동 말(bond_shift/tension_shift) → 숫자,
        #   옛 숫자 출력이면 시드·턴당 캡·범위로 클램프. ★자리 = 이 파생 뷰(_relation_view) **앞** — 첫 수리(감정 단계 앞)는
        #   이 뷰가 날숫자를 읽는 걸 놓쳤다(npc_attitudes → iceberg·자율 트리거·월드보드·presence). 감정·iceberg·저장도 같은 값.
        #   행동 PC = anchors.acting_user_id 의 mask(쓰기 경로 ctx.user_id 마스크와 같은 사람), 턴 = 같은 turn_index.
        try:
            _anch_al = context.narrative_anchors or {}
            _ch_al = _anch_al.get("channel_id", "")
            _acting_al = ((_anch_al.get("all_pcs") or {}).get(_anch_al.get("acting_user_id", "")) or {}).get("mask")
            if _ch_al and _acting_al:
                _turn_al = int(domain_manager.get_world_state(_ch_al).get("turn_index", 0) or 0)
                domain_manager.align_theoria_relations(
                    _ch_al, analysis.get("psyche_states") or {}, _acting_al, _turn_al)
        except Exception as _e_al:
            logger.debug(f"[Relation] align skip: {_e_al}")
        bus.dai["npc_attitudes"] = _relation_view(
            analysis.get("psyche_states"),
            (context.narrative_anchors or {}).get("stored_npc_attitudes"))
        bus.dai["npc_knowledge"] = analysis.get("NPCKnowledge", {})
        bus.dai["sensory_anchors"] = analysis.get("SensoryAnchors", [])
        bus.dai["habitus_analysis"] = analysis.get("HabitusAnalysis", {})
        bus.dai["intimacy_analysis"] = analysis.get("IntimacyAnalysis")
        bus.dai["relevant_context"] = analysis.get("RelevantContext", [])
        bus.dai["relevant_npcs"] = analysis.get("RelevantNPCs", [])
        bus.dai["relevant_chunks"] = analysis.get("relevant_chunks", [])
        bus.dai["needs_judgment"] = analysis.get("needs_judgment", False)
        bus.dai["action_meta"] = analysis.get("action_meta", {})
        bus.dai["asset_evaluation"] = analysis.get("asset_evaluation", {})
        bus.dai["flashback_eval"] = analysis.get("flashback_eval")
        # [2026-09-06 P8b] rest_eval 매핑 삭제 — "부재를 감지하지 않는다". 소비자(다운타임
        #   코드 효과·휴식 회복·산문 지시)는 같은 카드에서 전부 폐기됐다.
        bus.dai["item_usage"] = analysis.get("item_usage")
        # [2026-09-13 P14] 도착물 방아쇠 ② — 불리언 하나. 부재/비불리언 = False(무동작).
        #   소비자는 world_board.pick_arrival_request 하나뿐이고, 종류·내용은 여기서 안 온다.
        bus.dai["arrival"] = bool(analysis.get("arrival") is True)
        # [2026-06-11 소비자 감사 #2~4] 운송 누락 복구 — Theoria 스키마에 실재(=Flash가 매 턴 생산)
        # 했으나 매핑이 빠져 슬롯 번역기 3종(trait_connections/spatial_inscription/continuity_check)이
        # 영구 빈손이었음 (dai_consumer_audit.md). 번역기들은 빈값 관용이라 연결만으로 안전.
        bus.dai["trait_connections"] = analysis.get("trait_connections", {})
        bus.dai["spatial_read"] = analysis.get("spatial_read", {})
        bus.dai["continuity_check"] = analysis.get("continuity_check")
        # [2026-09-22 voice_seed §2 D 2] 굴림 + 서사 콜 콜라주 병합. 굴린 이름만 채택하고
        #   목록 밖 이름은 버린다(모델이 인물을 발명하는 자리를 여기서 막는다).
        #   굴림이 없으면(게이트 통과 0 · 마스터 OFF) `{}` — 하류(Slot 7 꼬리)는 빈손이면 침묵.
        bus.dai["newcomer_seeds"] = voice_seed.merge_collage(
            _seed_rolls, analysis.get("newcomer_seeds")) if _seed_rolls else {}

        # [2026-07-19 명시 null 정규화] LLM Optional 필드는 "누락"뿐 아니라 "명시 null"로도
        # 온다 — .get(k, default)는 키가 존재하면 null을 그대로 통과시킴 (E3 프로덕션 크래시
        # 교훈). 형 계약 필드를 여기서 일괄 정규화 — 하류 무가드 순회/슬라이스 방어 초크포인트.
        # (의미상 null 허용 필드는 제외: anomaly는 {}=falsy로 동치, offscreen_trace/scene_register/
        #  intimacy_analysis/flashback_eval/item_usage/continuity_check는 null 계약 유지)
        for _lk in ("aspects", "memory_triggers", "sensory_anchors", "relevant_context",
                    "relevant_npcs", "relevant_chunks", "suggested_beats", "open_invitations"):
            if not isinstance(bus.dai.get(_lk), list):
                bus.dai[_lk] = []
        for _dk in ("input_analysis", "quality_flags", "position", "effect", "psyche_states",
                    "narrative_chain", "time_flow", "doom_clocks",
                    "anomaly_profile", "pc_autonomy_check", "temporal_orientation",
                    "npc_attitudes", "npc_knowledge", "habitus_analysis", "action_meta",
                    "asset_evaluation", "trait_connections", "spatial_read"):
            if not isinstance(bus.dai.get(_dk), dict):
                bus.dai[_dk] = {}
        if not isinstance(bus.dai["narrative_chain"].get("open_threads"), list):
            bus.dai["narrative_chain"]["open_threads"] = []

        # [2026-07-28 PC 혼입 단일 정제] ★같은 병의 5·6번째 재발을 끊는 자리.
        # 그동안은 **소비처마다** 가드를 새로 달았다(NPCAttitudes 07-13 / npc_knowledge /
        # scene_npcs / psyche_states 07-28). 그런데 그 가드들은 대개 **지역 변수만** 정제하고
        # bus.dai 원본은 그대로 둬서, 원본을 보는 하류(world_board·story_director·
        # narrative_tracker)는 여전히 PC가 섞인 데이터를 받았다 — world_board는 PC 이름으로
        # 세계 게시물을 만들 수 있는 상태였다.
        # 처방: **DAI가 만들어지는 이 초크포인트에서 한 번만** 걷어내고 하류는 믿게 한다.
        # (PC=카메라 원칙. 개별 가드는 이중 안전으로 남겨도 무해하다.)
        try:
            _pc_masks_dai = _pc_masks(context)   # [2026-09-22] 시드 게이트와 같은 원천(단일 함수)
            if _pc_masks_dai:
                _purged = []
                # [2026-09-22 voice_seed §2 D 3] newcomer_seeds 편입 — 게이트 2가 이미 걸렀지만
                #   서사 콜이 키를 PC 이름으로 바꿔 돌려줄 수 있어 이중으로 본다.
                for _nk in ("psyche_states", "npc_attitudes", "npc_knowledge", "newcomer_seeds"):
                    _blk = bus.dai.get(_nk)
                    if isinstance(_blk, dict):
                        _hit = [n for n in _blk if n in _pc_masks_dai]
                        if _hit:
                            bus.dai[_nk] = {k: v for k, v in _blk.items() if k not in _pc_masks_dai}
                            _purged.append(f"{_nk}({','.join(_hit)})")
                _rn = bus.dai.get("relevant_npcs")
                if isinstance(_rn, list):
                    _hit = [n for n in _rn if n in _pc_masks_dai]
                    if _hit:
                        bus.dai["relevant_npcs"] = [n for n in _rn if n not in _pc_masks_dai]
                        _purged.append(f"relevant_npcs({','.join(_hit)})")
                if _purged:
                    logger.warning("[DAI] PC 혼입 제외: %s", " / ".join(_purged))
        except Exception as _e_pcp:
            logger.debug(f"[DAI] PC purge skipped: {_e_pcp}")

        # [2026-09-22 voice_seed §2 F] 버퍼 적재 — **정제 뒤**다(PC 키가 버퍼에 눕지 않게).
        #   등록 관문(§G, PR 3)이 이걸 집어 lore 절로 앉힌다. 여기서도 예외는 삼킨다.
        try:
            if bus.dai.get("newcomer_seeds"):
                voice_seed.buffer_put(_seed_ch, bus.dai["newcomer_seeds"], _seed_turn)
        except Exception as _e_seedbuf:
            logger.warning("[Seed] buffer_put skipped: %s", _e_seedbuf)

        # [2026-06-11 소비자 감사 #6] 죽은 저장 제거 — capability hints는 이제 anchors 경유로
        # Theoria *입력*에 배달됨 (une_facade에서 계산, theoria_analyzer 로스터 옆 렌더 — 원설계).
        # 기존 이 자리 코드는 Flash 콜 후 저장 + 독자 0 + npc_roster가 str이라 isinstance(dict)
        # 가드에 막혀 사실상 한 번도 실행 안 됨 (이중 사망 확인).

        # N1: Judgment Gate — Flash의 needs_judgment를 코드 게이트로 검증
        raw_needs = analysis.get("needs_judgment", False)
        resolve = (analysis.get("action_meta") or {}).get("resolve", "none")
        # [2026-09-16 3차] Rule2 쿨다운 복원 — bus 는 매턴 신조라 채널 world_state 가 정본.
        last_j_turn = bus.judgment.get("last_judgment_turn")
        if last_j_turn is None:
            last_j_turn = load_last_judgment_turn((context.narrative_anchors or {}).get("channel_id", ""),
                                                  (context.narrative_anchors or {}).get("acting_user_id", ""))
        current_turn = (context.narrative_anchors or {}).get(
            "session_memory", {}
        ).get("turn_count", 0) or domain_manager.get_world_state(
            (context.narrative_anchors or {}).get("channel_id", "")
        ).get("turn_index", 0)

        # [2026-06-11 소비자 감사 #1] turn_index 배선 — bus.dai["turn_index"]를 아무도 안 실어
        # 항상 0이었음 → doom 시계 fade 7개 읽기 + une_facade 퀘스트 stale archive가 0 기반 동작
        # (staleness 트리거 사망). 게이트 계산용 current_turn을 그대로 적재.
        # 주의: 부활 첫 턴에 묵은 퀘스트 일괄 archive는 정상 동작.
        bus.dai["turn_index"] = current_turn

        final_needs, gate_reason = gate_judgment(
            user_input=context.request.user_input,
            flash_needs_judgment=raw_needs,
            last_judgment_turn=last_j_turn,
            current_turn=current_turn,
            resolve=resolve,
        )

        bus.judgment["active"] = final_needs
        bus.judgment["gate_reason"] = gate_reason
        bus.judgment["gate_requested"] = bool(raw_needs)
        if final_needs:
            bus.judgment["last_judgment_turn"] = current_turn
            save_last_judgment_turn((context.narrative_anchors or {}).get("channel_id", ""), current_turn,
                                    (context.narrative_anchors or {}).get("acting_user_id", ""))
            bus.judgment["meta"] = analysis.get("action_meta") or {}  # [07-27] 명시 null 방어
            eval_data = analysis.get("asset_evaluation") or {}  # [07-19] 명시 null 방어
            bus.judgment["eval"] = eval_data
            bus.judgment["modifications"] = eval_data.get("modifications") or []
            bus.judgment["narrative_hook"] = analysis.get("narrative_hook", "")

        # [V10] DAI 스냅샷 롤링 보존 — bus.dai 완성 직후, 코드만(콜 0)·실패 무해.
        # 용도: ①관측 — 필드 비대/모델 JSON 버릇을 실데이터로 ②Sprint 4 동적 NPC 원재료
        # (턴별 심리·사회 이력 질의). 읽기: sqlite_store.read_dai_logs(channel_id, n).
        # [2026-09-24 감사] 적재 자리를 StoryDirector **뒤**로 옮겼다(아래 5.5 직후) — 여기서 찍으면
        #   story_direction.next_beat 가 아직 없어 서사 콜 RECENT BEATS(반복 회피 목록)에 디렉터 비트가 영영 안 들어갔다.
        
        # Doom Clocks v3 연동 (clock_updates, clock_new, clock_resolved)
        # relief 제거 (2026-05-23) — legacy 위기진폭 잔재. 둠은 서사 진행도라 평화 장면 자동 감소는 의미 충돌.
        doom_clocks_output = analysis.get("doom_clocks") or {}
        if isinstance(doom_clocks_output, dict):
            bus.doom["flash_clock_updates"] = doom_clocks_output.get("clock_updates") or []  # [07-19] 명시 null 방어
            bus.doom["flash_clock_new"] = doom_clocks_output.get("clock_new")
            bus.doom["flash_clock_resolved"] = doom_clocks_output.get("clock_resolved") or []

        # [2026-09-06 P8b] **Composure Impact 번역 삭제.** 여기가 mental_impact 를 bus.composure
        #   의 impact 칸으로 옮기던 마지막 다리였다 — severity enum → 수치(MENTAL_IMPACT_ENUM_SCALE),
        #   씬타입별 캡, 방향 전환 감쇠까지 전부 "장면을 분류해 숫자를 정하는 코드"라 함께 사라졌다.
        #   평형의 이동은 이제 전담 추출 콜의 deltas 한 경로뿐이다(기력과 완전 대칭).

        # Anomaly Profile 연동
        anomaly_profile = analysis.get("anomaly_profile") or {}
        if isinstance(anomaly_profile, dict):
            tag = anomaly_profile.get("trigger") or ""
            category = anomaly_profile.get("category") or ""
            intensity = anomaly_profile.get("intensity") or ""
            polarity = anomaly_profile.get("polarity") or ""
            line = anomaly_profile.get("line") or ""
            reason = anomaly_profile.get("reason") or ""

            if tag:
                bus.anomaly["tag"] = tag
            if category:
                bus.anomaly["category"] = category
            if intensity:
                bus.anomaly["intensity"] = intensity
            if polarity:
                bus.anomaly["polarity"] = polarity
            if line:
                bus.anomaly["line"] = line
            if reason:
                bus.anomaly["reason"] = reason

        # Condition resolved (from Flash analysis)
        cond_resolved = analysis.get("condition_resolved")
        if isinstance(cond_resolved, list) and cond_resolved:
            bus.anomaly["condition_resolved"] = cond_resolved

        # Condition updates — severity transition (from Flash analysis)
        cond_updates = analysis.get("condition_updates")
        if isinstance(cond_updates, list) and cond_updates:
            bus.anomaly["condition_updates"] = cond_updates

        # Event location override (from Flash anomaly_profile)
        if isinstance(anomaly_profile, dict):
            event_location = (anomaly_profile.get("location") or "").strip()
            if event_location:
                bus.anomaly["location"] = event_location

        # M3 제거 (2026-07-02): "chain CLOSED → anomaly 강제" 컷.
        # ① 키 드리프트('status' → 'chain_status')로 장기간 사망 상태였고 부재가 관측된 적 없음.
        # ② 현 자세와 충돌 — quiet resolution 허용(Scheherazade 완화) + 페이싱은 doom 起承轉結/storyteller 결정이 담당.
        # 부활 시: bus.dai["narrative_chain"].get("chain_status") == "CLOSED" 게이트로 재작성할 것.

        # Seed fallback 제거 (2026-07-09): "tag 제안 없음 → 시드 무작위 강제" 컷.
        # M3(chain CLOSED→anomaly 강제)의 쌍둥이 — 조용한 턴 허용 + 페이싱=doom/storyteller 원칙에
        # 동일하게 걸린다. 매 조용한 턴 무작위 시드 발화(Mid 강도 + "{tag}의 기운이 감돈다" 위협 프레임 line)는
        # wingbeat 시드 재설계(작은·장르중립·로어접지)와 정면 충돌. 능동성 공급은 story_director/reader_gm/doom이
        # 담당하고, 날개짓의 성장은 reader_gm replenish(축 기반, 게이트됨)가 맡는다.
        # 부활 시: 무작위 강제가 아니라 stall 감지 게이트 + 중립 line + 적합 시드 선택으로 재작성할 것.

        # Normalize defaults for downstream use
        if bus.anomaly.get("tag") and not bus.anomaly.get("category"):
            bus.anomaly["category"] = bus.anomaly.get("tag")
        if not bus.anomaly.get("intensity"):
            bus.anomaly["intensity"] = "Mid"
        if not bus.anomaly.get("polarity"):
            bus.anomaly["polarity"] = "mixed"
        # Default line for a Flash/Theoria-proposed anomaly that has a tag but no line.
        # (Seed-based fallback 제거 2026-07-09 이후로 이 경로는 진짜 Flash anomaly에만 적용된다.)
        if bus.anomaly.get("tag") and not bus.anomaly.get("line"):
            bus.anomaly["line"] = f"{bus.anomaly['tag']}의 기운이 감돈다."

        # 1.5 Emotion Engine: psyche_states → normalized emotion tracking
        channel_id = (context.narrative_anchors or {}).get("channel_id", "")
        current_turn = 0
        if channel_id:
            current_turn = domain_manager.get_world_state(channel_id).get("turn_index", 0)

        # [2026-09-25] 관계 정렬(align_theoria_relations)은 위 `_relation_view` 앞으로 옮겼다 — 여기서는 이미 한 숫자.

        try:
            psyche_states = bus.dai.get("psyche_states", {})
            # [2026-06-12] PC 혼입 차단 (4호) — Theoria가 psyche_states에 PC를 포함시키는데
            # 감정엔진과 하류 3기관(스토리디렉터 focus/NPC자율 집단게이트/iceberg Slot 14·16)은
            # 전부 NPC 전용. PC가 흘러들면: 디렉터가 PC를 연출 대상으로(focus=도만 관측됨),
            # 집단 게이트 인원 수 부풀림, PC 내면 힌트가 Pro에 주입(사칭 압력). PC=카메라 원칙.
            _pc_masks_em = {
                p.get("mask") for p in (context.narrative_anchors or {}).get("all_pcs", {}).values()
                if isinstance(p, dict) and p.get("mask")
            }
            if psyche_states and _pc_masks_em:
                _removed = [n for n in psyche_states if n in _pc_masks_em]
                if _removed:
                    psyche_states = {k: v for k, v in psyche_states.items() if k not in _pc_masks_em}
                    logger.debug(f"[EmotionEngine] PC 제외: {', '.join(_removed)}")
            if psyche_states:
                prev_emotions = {}
                if channel_id:
                    prev_emotions = domain_manager.get_world_state(channel_id).get(
                        "npc_emotion_states", {}
                    )
                # P2: scene 컨텍스트와 memory_triggers 전달 (_derive_relational Tier 4/6/7 입력)
                _narrative_chain = bus.dai.get("narrative_chain", {}) or {}
                _scene_ctx = {
                    "register": bus.dai.get("scene_register"),
                    "silence_type": _narrative_chain.get("silence_type"),
                    # [2026-07-13] 친밀 장면 재서열 신호 — intimate에서 관계-긍정 tier 선발화
                    "scene_type": bus.dai.get("scene_type"),
                }
                emotion_results = EmotionEngine.process_turn(
                    psyche_states=psyche_states,
                    previous_emotions=prev_emotions,
                    current_turn=current_turn,
                    scene_ctx=_scene_ctx,
                    memory_triggers=bus.dai.get("memory_triggers", []),
                )
                bus.emotion = EmotionEngine.to_bus_dict(emotion_results)
                # 6.2 (2026-05-20): slot_manager fast-path 배선. slot_manager가
                # `dai._emotion_states_for_slot`를 우선 보고 있었으나 어디서도 set
                # 안 해 항상 world_state round-trip으로 폴백했음. 같은 턴 내에서는
                # 이 라이브 dict가 가장 신선하므로 직접 주입한다.
                # 값 타입: Dict[str, EmotionState] — slot_manager가 그대로 소비.
                bus.dai["_emotion_states_for_slot"] = emotion_results
                if channel_id:
                    # [2026-09-15 §12 추적기] 통째 교체 → 병합. 이번 턴 결과가 없는 NPC는 탈락이 아니라
                    #   기준선 감쇠(prev×EMOTION_DECAY), 사망은 즉시 제거. 버스·Slot 16·log는 이번 턴 결과만(무변경).
                    _write_emotion_tracker(channel_id, prev_emotions, emotion_results, current_turn, context)
                if channel_id and emotion_results:
                    world = domain_manager.get_world_state(channel_id)
                    # [2026-08-11 soma 지속] B축 스냅샷 — 병합·도장 본체는 `_merge_soma_states()`
                    #   (모듈 상단, 근거 주석 전량 독스트링). 여기선 읽기/저장/적립만.
                    try:
                        _psy = psyche_states or {}  # ⚠PC 제외본(위 _pc_masks_em 필터 결과)
                        world = domain_manager.get_world_state(channel_id)
                        _soma_prev = world.get("npc_soma_states")
                        if not isinstance(_soma_prev, dict):
                            _soma_prev = {}
                        _soma_snap, _soma_moves = _merge_soma_states(_soma_prev, _psy, current_turn)
                        if _soma_snap != _soma_prev:
                            world["npc_soma_states"] = _soma_snap
                            domain_manager.update_world_state(channel_id, world)
                        if _soma_moves:
                            try:  # [V10 적립] soma_log — 몸 상태가 *언제* 뒤집혔나. 실패 무해.
                                import sqlite_store
                                for _tn, _fp, _tp, _fd, _td in _soma_moves:
                                    sqlite_store.append_soma_log(
                                        channel_id, current_turn, _tn, _fp, _tp, _fd, _td)
                            except Exception as _e_somalog:
                                logger.debug(f"[Soma] log skipped: {_e_somalog}")
                    except Exception as _e_soma:
                        logger.debug(f"[Soma] snapshot skip: {_e_soma}")
                    # [V10 적립] emotion_log 적립 — bus.emotion 완성 직후, 코드만(콜0)·실패 무해.
                    # 턴별 per-NPC 감정 스냅샷 → 궤적/스파이크 질의(독자: sqlite_store.read_emotion_*).
                    try:
                        import sqlite_store
                        sqlite_store.append_emotion_log(
                            channel_id, current_turn, bus.emotion,
                            log_extra={n: {k: getattr(st, k, "") for k in EmotionState.LOG_ONLY_KEYS}
                                       for n, st in emotion_results.items()})
                    except Exception as _e_emolog:
                        logger.debug(f"[V10] emotion log skipped: {_e_emolog}")
                spikes = [
                    f"{n}({s.spike_detail})"
                    for n, s in emotion_results.items()
                    if s.spike_detected
                ]
                if spikes:
                    logger.info(f"[EmotionEngine] Spikes: {', '.join(spikes)}")
            elif channel_id:
                # [2026-09-15 §12] 이번 턴 psyche_states가 비어도 부재 감쇠는 돈다(전원 부재 턴).
                _prev_only = domain_manager.get_world_state(channel_id).get("npc_emotion_states", {})
                if _prev_only:
                    _write_emotion_tracker(channel_id, _prev_only, {}, current_turn, context)
        except Exception as e:
            _degrade_stage(bus, "emotion_engine", e)

        # 2. Mental Pre-pass: annotate current stage for downstream modules.
        try:
            self.vigor_composure = VigorComposureModule()
            context = await self.vigor_composure.prime(context)
        except Exception as e:
            _degrade_stage(bus, "vigor_composure", e)

        # 3. Judgment (gated by Flash needs_judgment + judgment_gate)
        if bus.judgment["active"]:
            try:
                self.judgment = JudgmentEngine(self.theoria.client, self.theoria.model_id)
                context = await self.judgment.process(context)
            except Exception as e:
                bus.judgment["active"] = False
                _degrade_stage(bus, "judgment_engine", e)

        # 4. Storyteller: inject state + set potential
        bus.anomaly["potential"] = True
        channel_id = (context.narrative_anchors or {}).get("channel_id", "")
        if channel_id:
            st_state = domain_manager.get_storyteller_state(channel_id)
            bus.anomaly["_storyteller_state"] = st_state
            bus.anomaly["_current_turn"] = domain_manager.get_world_state(channel_id).get("turn_index", 0)
            bus.anomaly["_channel_id"] = channel_id

        # 5. Storyteller Decision
        if bus.anomaly.get("potential"):
            try:
                self.anomaly = AnomalyModule(self.theoria.client, self.theoria.model_id)
                context = await self.anomaly.process(context)
            except Exception as e:
                bus.anomaly["triggered"] = False
                _degrade_stage(bus, "anomaly_module", e)

        # 5.5 Story Director: pacing, plot hints, idle handling, transition mood
        try:
            context = StoryDirector.process(context)
        except Exception as e:
            _degrade_stage(bus, "story_director", e)

        # [V10 Sprint 0] 턴별 DAI 스냅샷 적재 — [2026-09-24 감사] 위(분석 전개 직후)에서 이사: 디렉터 비트 포함.
        try:
            import sqlite_store
            _dai_ch = (context.narrative_anchors or {}).get("channel_id", "")
            if _dai_ch:
                sqlite_store.append_dai_log(_dai_ch, current_turn, bus.dai)
        except Exception as _e_dai:
            logger.debug(f"[V10] dai log skipped: {_e_dai}")

        # 5.6 Seven Dice persistence → DiceEngine가 자체 처리 (dice_engine.py)

        # 6. Doom Update — consumes judgment doom_delta naturally
        try:
            self.doom = DoomModule()
            context = await self.doom.process(context)
        except Exception as e:
            _degrade_stage(bus, "doom_module", e)

        # 7. Vigor/Composure Sync (LAST — consumes all accumulated deltas)
        try:
            self.vigor_composure = VigorComposureModule()
            context = await self.vigor_composure.process(context)
        except Exception as e:
            _degrade_stage(bus, "vigor_composure", e)

        # ===== Pipeline Summary Log =====
        self._log_pipeline_summary(bus)

        # [V10 적립] turn_snapshot — 파이프라인 말미, 모든 신호 최종. 콜0·append-only·실패무해.
        try:
            import sqlite_store
            _ts_ch = (context.narrative_anchors or {}).get("channel_id", "")
            if _ts_ch:
                _sd = (bus.dai.get("story_direction") or {}) if isinstance(bus.dai, dict) else {}
                _snap = {
                    "doom_value": bus.doom.get("value"),
                    "doom_phase": bus.doom.get("chapter_phase", ""),
                    "vigor": bus.vigor.get("value"),
                    "vigor_delta": bus.vigor.get("delta_applied", 0) or 0,
                    "composure": bus.composure.get("value"),
                    "composure_delta": bus.composure.get("delta_applied", 0) or 0,
                    "sd_pacing": _sd.get("pacing", ""),
                    "sd_tension": _sd.get("tension_axis", ""),
                    "sd_focus": (_sd.get("focus") or {}).get("spotlight", ""),
                    "sd_beat": bool(_sd.get("next_beat")),
                    "sd_idle": bool(_sd.get("is_idle_input")),
                    "judgment": judgment_snapshot(bus.judgment),
                    "anomaly_triggered": bool(bus.anomaly.get("triggered")),
                }
                sqlite_store.append_turn_snapshot(_ts_ch, current_turn, _snap)
        except Exception as _e_ts:
            logger.debug(f"[V10] turn_snapshot skipped: {_e_ts}")

        return context

    def _log_pipeline_summary(self, bus: SharedBus) -> None:
        """파이프라인 실행 결과 한눈에 볼 수 있는 요약 로그."""
        parts = ["[Pipeline Summary] (all modules always active)"]

        # Judgment
        if bus.judgment.get("active"):
            j = bus.judgment
            result = j.get("result", "N/A")
            roll = j.get("final_roll", "?")
            dc = j.get("dc", "?")
            gate = j.get("gate_reason", "")
            parts.append(f"  Judgment: {result} (roll={roll} vs DC={dc}) [{gate}]")
        else:
            parts.append(f"  Judgment: skipped (no action requiring roll)")

        # Doom
        doom_val = bus.doom.get("value", "?")
        doom_log = bus.doom.get("log", "")
        parts.append(f"  Doom: {doom_val}/100 — {doom_log[:100]}" if doom_log else f"  Doom: {doom_val}/100 (no change)")

        # Storyteller
        anomaly_triggered = bus.anomaly.get("triggered", False)
        if anomaly_triggered:
            a_tag = bus.anomaly.get("tag", "?")
            a_int = bus.anomaly.get("intensity", "?")
            a_dec = bus.anomaly.get("decision", "?")
            a_reason = bus.anomaly.get("decision_reason", "")
            parts.append(f"  Storyteller: ACT [{a_tag}] intensity={a_int} ({a_dec}: {a_reason})")
        else:
            a_dec = bus.anomaly.get("decision", "skip")
            a_reason = bus.anomaly.get("decision_reason", "")
            parts.append(f"  Storyteller: {a_dec} ({a_reason})" if a_reason else f"  Storyteller: {a_dec}")

        # Vigor / Composure
        v_val = bus.vigor.get("value", "?")
        c_val = bus.composure.get("value", "?")
        v_delta = bus.vigor.get("delta_applied", 0) or 0
        c_delta = bus.composure.get("delta_applied", 0) or 0
        v_sign = f"+{v_delta}" if v_delta > 0 else str(v_delta)
        c_sign = f"+{c_delta}" if c_delta > 0 else str(c_delta)
        parts.append(f"  Vigor: {v_val} ({v_sign}) | Composure: {c_val} ({c_sign})")

        # Story Director (SD-A4 새 스키마 — plot_hints/transition.mood 제거, focus.spotlight/next_beat 사용)
        sd = bus.dai.get("story_direction") or {}  # [07-27] 명시 null 방어 (요약 로그는 try 밖)
        if sd.get("active"):
            sd_pacing = sd.get("pacing", "?")
            sd_tension = sd.get("tension_axis", "?")
            sd_idle = sd.get("is_idle_input", False)
            sd_focus = (sd.get("focus") or {}).get("spotlight", "none")
            sd_cut = (sd.get("transition") or {}).get("cut", "?")
            sd_beat = "Y" if sd.get("next_beat") else "N"
            parts.append(
                f"  StoryDir: pacing={sd_pacing} tension={sd_tension} idle={sd_idle} "
                f"focus={sd_focus} cut={sd_cut} beat={sd_beat}"
            )
            # Seven Dice
            sd_dice = sd.get("dice", {})
            if sd_dice:
                parts.append(f"  Dice: {sd_dice.get('name','?')} (state={sd_dice.get('scene_state','?')}, visible={sd_dice.get('visible',False)})")
        else:
            parts.append("  StoryDir: inactive")

        # Degradation
        _deg = bus.dai.get("_degraded_stages", [])
        if _deg:
            _deg_str = ", ".join(d.get("stage", "?") for d in _deg if isinstance(d, dict))
            parts.append(f"  ⚠ Degraded: {_deg_str}")

        # Emotion — pair 스키마 v2: 'dominant' → 'base' + 'modifier' (to_bus_dict summary)
        emotion_data = bus.emotion
        if emotion_data.get("active"):
            summaries = emotion_data.get("summary", {})
            emo_parts = []
            for n, s in summaries.items():
                if s.get("intensity", 0) <= 0.05:
                    continue
                _base = s.get("base", "?") or "?"
                _mod = s.get("modifier", "")
                _pair = f"{_base}×{_mod}" if _mod else _base
                # [2026-08-03] pair_confidence 노출. 이미 계산돼 bus에 실려 있었는데
                #   요약 로그만 안 찍어서, "왜 이 modifier가 붙었나"를 소스에서
                #   역추적해야 했다. confidence는 _derive_relational의 **티어 번호 역수**라
                #   값 하나로 어느 티어가 결정했는지 특정된다:
                #     1.0=T1 value_conflict / 0.9=T2 negotiation_stance / 0.8=T3 cultural_affect
                #     0.7=T4 memory_trigger / 0.6=T5 attachment / 0.55=T5b poise
                #     0.5=T6 register / 0.4=T7 silence_type / 0.3=T8 deep_read
                #   ★특히 T6·T7은 scene_ctx(장면 공유) 소스라, 여러 NPC가 **같은 modifier**로
                #     몰렸을 때 "개인 신호인가 장면 필드가 덮었나"를 이걸로 가른다.
                #   solo base(modifier 없음)면 confidence 무의미 → 생략.
                _conf = s.get("pair_confidence", 0)
                _conf_s = f"/{_conf:.2f}" if _mod and _conf else ""
                emo_parts.append(
                    f"{n}={_pair}({s.get('intensity',0):.1f}{_conf_s})"
                    + (" ⚡" if s.get("spike") else "")
                )
            if emo_parts:
                parts.append(f"  Emotion: {', '.join(emo_parts)}")
        else:
            parts.append("  Emotion: inactive")

            # [2026-08-03] Scene register / silence_type — **modifier 쏠림 때만** 발화.
            #   _derive_relational의 Tier 6(register)·Tier 7(silence_type)은 scene_ctx 소스라
            #   한 값이 전원의 modifier를 덮는다. 그래서 "여러 NPC가 같은 modifier"는
            #   개인 신호가 아니라 장면 필드가 결정했다는 신호고, 그때만 이 두 필드가
            #   설명력을 갖는다. register는 감정 modifier뿐 아니라 Slot 16 연출 지시
            #   (iceberg.translate_register)도 만들어서, 오판정이면 두 곳이 같이 틀린다.
            #   ★상시 출력하지 않는다 — 쏠림이 없는 턴엔 설명할 게 없다(침묵 경로).
            #     오늘 detail_density에서 고친 것과 같은 규율을 이 줄에도 적용.
            try:
                _mods = [s.get("modifier") for s in summaries.values()
                         if s.get("modifier") and s.get("intensity", 0) > 0.05]
                if len(_mods) >= 2 and len(set(_mods)) == 1:
                    _sr = (bus.dai.get("scene_register") or "") if isinstance(bus.dai, dict) else ""
                    _sil = ((bus.dai.get("narrative_chain") or {}).get("silence_type") or "") \
                        if isinstance(bus.dai, dict) else ""
                    parts.append(
                        f"  └ modifier {len(_mods)}인 쏠림 — register={_sr or '-'} silence={_sil or '-'}"
                    )
            except Exception:
                pass

        logger.info("\n".join(parts))
