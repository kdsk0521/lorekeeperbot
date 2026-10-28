"""
Lorekeeper - Universal Narrative Engine (UNE) Facade
The main entry point for the UNE engine.
"""

import json
import logging
import random
from typing import Dict, Any, List, Tuple

from orchestration_context import GameContext
from waterfall_pipeline import WaterfallPipeline
import domain_manager
import game_character
import game_world

logger = logging.getLogger("UNE")


def _cv_vigor(channel_id: str, user_id: str, mem: Dict[str, Any]) -> int:
    """[2026-08-18 Phase 2.5] 활력 = 레지스트리 값. 폴백 계단은 custom_vars 한 곳에만 있다."""
    try:
        import custom_vars as _cv
        return _cv.vigor_value(channel_id, user_id, mem)
    except Exception as e:
        logger.debug(f"[CustomVar] 활력 조회 skip: {e}")
        src = (mem or {}).get("vigor") or (mem or {}).get("mental") or {}
        return int(src.get("value", 100) or 100)


def _cv_composure(channel_id: str, user_id: str, mem: Dict[str, Any]) -> int:
    """[2026-09-24 감사] 평형 = 레지스트리 값(P8b 이후 ai_memory.composure 는 이월 승계용으로 동결).
    `_cv_vigor` 와 같은 문 — 폴백 계단은 custom_vars.composure_value 한 곳."""
    try:
        import custom_vars as _cv
        return _cv.composure_value(channel_id, user_id, mem)
    except Exception as e:
        logger.debug(f"[CustomVar] 평형 조회 skip: {e}")
        src = (mem or {}).get("composure") or {}
        v = src.get("value", 100)
        return int(v) if v is not None else 100


def _to_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


# [2026-10-01 1차] 판정층 라벨 — 함수 로컬에서 모듈로 올렸다. slot_manager._build_telescope_prefill의
#   [Outcome] 시드가 같은 사전을 쓴다(한 프롬프트 안에서 같은 사실엔 같은 말). 값 무변경.
RESULT_LABEL_EN = {
    "critical_success": "critical success", "success": "success",
    "partial": "partial success", "failure": "failure", "critical_failure": "critical failure",
}
# [2026-10-06 어휘 V1 · P-a] 위치 낱말 = Turn_Brief(iceberg `_POS_TIERS`)와 같은 5단 사다리 하나.
#   옛 POSITION_LABEL_EN(controlled/risky/dire, 3단)은 같은 값 dai.position.value를 다른 사다리·다른 문턱
#   ("dire" 0.2 대 0.25)으로 불렀다 — 한 렌더에 같은 사실이 두 말로 갔다. 내부 3단 키(_position_tier)는
#   MC 행렬 폴백·World situation 표기만 쓴다. 판정 줄·[Outcome] 시드·Mira 굴림 줄은 이 함수 하나.
def position_label(value) -> str:
    import iceberg as _ib
    return f"{_ib.position_word(_to_float(value, 0.5))} position"


def _position_tier(value: float) -> str:
    if value <= 0.25:
        return "desperate"
    if value <= 0.5:
        return "risky"
    return "controlled"


def _mc_move(position: str, result: str) -> str:
    matrix = {
        ("desperate", "critical_failure"): "Catastrophic - something irreversible happens.",
        ("desperate", "failure"): "the threat becomes real - irreversible consequences.",
        ("desperate", "partial"): "Heavy price - gain what was sought but lose something.",
        ("desperate", "success"): "Turnaround - the direst moment gives way.",
        ("desperate", "critical_success"): "Full reversal - the worst position turns over.",
        ("risky", "critical_failure"): "Worst case unfolds - danger becomes reality.",
        ("risky", "failure"): "the danger escalates - a new one reveals itself.",
        ("risky", "partial"): "Success with cost - complications follow.",
        ("risky", "success"): "Danger cleared - competent execution.",
        ("risky", "critical_success"): "Beyond the odds - more lands than was risked.",
        ("controlled", "critical_failure"): "Unexpected reversal - safety shatters.",
        ("controlled", "failure"): "Minor cost - a small setback.",
        ("controlled", "partial"): "Minor friction - less smooth than expected.",
        ("controlled", "success"): "Clean - smooth and effortless.",
        ("controlled", "critical_success"): "Beyond the aim - more than was sought.",
    }
    return matrix.get((position, result), "Outcome determined by world logic.")


def _collect_aspect_stance(aspects: Any) -> Tuple[List[str], List[str]]:
    if not isinstance(aspects, list):
        return [], []

    favorable: List[str] = []
    against: List[str] = []
    for aspect in aspects:
        if not isinstance(aspect, dict):
            continue
        # [2026-09-24 감사] 스키마 키는 "text"(Theoria Aspects) — "name" 만 읽어 in favor/against 줄이 영구 미출력.
        name = str(aspect.get("text") or aspect.get("name") or "").strip()
        if not name:
            continue
        stance = str(aspect.get("for_or_against", aspect.get("stance", ""))).strip().lower()
        if stance in ("for", "support", "positive", "pro"):
            favorable.append(name)
        elif stance in ("against", "oppose", "negative", "con"):
            against.append(name)
    return favorable, against


# ── Directing Notation Tables (♪ 음악 | ▶ 카메라 | ◎ 사진 — 3축 연출 표기) ──
_POSITION_NOTATION = {
    "controlled": "situation | ♪ mp, andante, legato | ▶ wide, parallel, pan | ◎ midground",
    "risky":      "situation | ♪ f, allegro, marcato | ▶ two-shot, facing, cut | ◎ foreground",
    "desperate":  "situation | ♪ ff, presto, staccato | ▶ low-angle, back-to-back, jump-cut | ◎ spotlight",
}
_ENERGY_NOTATION = {
    "idle":       "scene | ♪ mp, andante, legato | ▶ pan, pillow, long-take | ◎ background",
    "steady":     "scene | ♪ mf, andante, legato | ▶ eye-level, parallel, match-cut | ◎ midground",
    "rising":     "scene | ♪ f, allegro, marcato, crescendo | ▶ two-shot, facing, crosscut | ◎ glimpses",
    "falling":    "scene | ♪ p, adagio, legato, diminuendo | ▶ long-take, back-to-back, fade | ◎ background",
    "peak":       "scene | ♪ ff, presto, sforzando | ▶ close-up, cut, jump-cut | ◎ foreground",
    "stagnant":   "scene | ♪ pp, largo, legato | ▶ long-take, pillow, height-gap | ◎ background",
    "detonation": "scene | ♪ sfz, presto, sforzando | ▶ wide, montage, cut | ◎ spotlight",
    "aftershock": "scene | ♪ p, adagio, staccato | ▶ long-take, back-to-back, fade | ◎ background",
}
# [2026-10-12 C23] 활력·평형 단계 표 셋은 iceberg로 옮겼다 — PC(여기, Slot 30)와 NPC 인물 블록
#   (iceberg.translate_psyche_states, Slot 14)이 같은 표를 읽는다. strained·collapsing 머리에 상태 낱말
#   (body:compensating/giving, psyche:holding/giving)이 붙었다. 이름은 그대로 다시 내보낸다 —
#   아래 _build_atmosphere_layer 분기와 tests/test_notation_ab가 이 이름을 읽는다.
from iceberg import _VIGOR_NOTATION, _COMPOSURE_NOTATION, _MIXED_NOTATION  # noqa: E402
# _DOOM_NOTATION 제거 (2026-07-06 감사): 형제(_COMPOSURE/_MIXED)와 달리 소비자 0 —
# 둠→챕터볼륨 리브랜드로 world-layer doom 연출 분기가 사라진 잔재. doom 무드는
# doom_module lens×phase atmosphere가 담당.
# [2026-10-02 시간 단일 주인 T2] _SCENE_PHOTO_OVERRIDE("time override: ◎ …") 삭제 — 장면 시간의 주인은 [TIME] 한 줄
#   (분석 time_flow + SceneType). 같은 원천의 중복이었고 친밀 장면에선 [TIME]과 반대 말을 했다.
#   스펙 composition/시간단일주인·연출표기어휘_스펙_2026-10-02.md
def _build_world_layer(bus) -> str:
    """World Layer: 연출 표기 + 장면 초점 + 액션만.
    데이터(position reason, effect, NPC attitudes, psyche, narrative chain,
    quality flags)는 iceberg 개별 슬롯(13-17, 28)이 커버."""
    dai = bus.dai if isinstance(bus.dai, dict) else {}
    parts: List[str] = []

    # Position → directing notation ONLY (reason+friction은 Slot 13 iceberg)
    pos = dai.get("position", {}) if isinstance(dai.get("position"), dict) else {}
    pos_value = _to_float(pos.get("value", 0.5), 0.5)
    pos_tier = _position_tier(pos_value)
    notation = _POSITION_NOTATION.get(pos_tier, "")
    if notation:
        parts.append(notation)

    scene_type = str(dai.get("scene_type", "normal"))

    # Energy → directing notation ONLY (산문 힌트는 Slot 16 iceberg)
    energy = str(dai.get("energy_direction", "idle"))
    energy_notation = _ENERGY_NOTATION.get(energy, "")
    if energy_notation:
        parts.append(energy_notation)


    # Action Reading (고유 — iceberg에 없음)
    action_meta = dai.get("action_meta", {}) if isinstance(dai.get("action_meta"), dict) else {}
    action_name = str(action_meta.get("action", "")).strip()
    # [2026-10-01 2단계] 'attempt — 난이도' 꼬리표는 attempt 입력에만 — decree 입력에 붙으면 RB_PC DECREE
    #   ("never … downgraded to an attempt")와 부딪쳤다(예: "신장을 들어내…" decree → "attempt — easy").
    if action_name and str(dai.get("input_mode") or "decree").strip().lower() == "attempt":
        difficulty = str(action_meta.get("difficulty", "normal"))
        # [2026-10-06 이중주입 H3] 판정 턴엔 Events 판정 줄이 행동을 인용한다("{PC} attempted '…'") —
        #   여기는 난이도만 싣는다. 판정 없는 attempt 턴은 인용째 그대로(Events에 인용이 없다).
        _jd = getattr(bus, "judgment", None)
        if isinstance(_jd, dict) and _jd.get("active"):
            parts.append(f"attempt — {difficulty}")
        else:
            parts.append(f"'{action_name}' attempt — {difficulty}")
        # [2026-10-01 1차 후속] "no roll; resolved by the situation (…) and the world's logic." 줄 삭제 — 굴림 여부의 집 =
        #   텔레스코프 [Outcome] 시드(slot_manager). 이 줄은 Flash 원 needs_judgment를 읽어 게이트가 막은 턴엔 침묵했다.
        #   ⚠남은 것: 'attempt — 난이도' 꼬리표가 decree 입력에도 붙는다(RB_PC DECREE와 부딪침) — 2단계에서.

    if not parts:
        return ""
    return "── World ──\n" + "\n".join(parts)


def _build_events_layer(context, bus) -> str:
    parts: List[str] = []

    # World Event → fact only (metadata stripped)
    # [2026-09-29 배치2 2a-1 · B안] 이변 한 주인 = 이 "world event:" 사실 줄. 같은 이변이 셋으로 갔다(6/22):
    #   이 줄 · 같은 턴 등록된 condition의 "current situation:" · story_director 범용 비트("the shockwave…").
    #   발화 턴엔 condition 줄을 빼고(다음 턴부터 서 있는 상황으로 나간다), 비트는 story_director가 싣지 않는다.
    anomaly = bus.anomaly if isinstance(bus.anomaly, dict) else {}
    _fired_line = str(anomaly.get("line", "") or "").strip() if anomaly.get("triggered") else ""
    if _fired_line:
        parts.append(f"world event: {_fired_line}")

    # Active Conditions → tag stripped
    _st_state = {}
    _ch_id = (context.narrative_anchors or {}).get("channel_id", "")
    if _ch_id:
        import domain_manager as _dm_ev
        _st_state = _dm_ev.get_storyteller_state(_ch_id)
    _all_conds = _st_state.get("active_conditions", [])
    _pc_loc = (bus.dai.get("current_location", "") if isinstance(bus.dai, dict) else "").strip()

    for cond in _all_conds:
        cond_loc = (cond.get("location") or "").strip()
        if not cond_loc or cond_loc == _pc_loc:
            _desc = cond.get("description", "")
            if _fired_line and str(_desc or "").strip() == _fired_line:
                continue                      # 이번 턴 발화한 그 이변 — world event 줄이 싣는다
            if _desc:
                # [2026-10-09 SD개편 J1] 꼬리 처방("it shapes the environment, NPC behavior, and the available
                #   actions")을 뺐다 — 서 있는 상황이 풀릴 때까지 매 턴(26%) 실리며 렌더가 매 턴 다시 그리게 했다.
                #   이어짐(비·정전)은 상태 한 줄로 지킨다.
                parts.append(f"current situation: {_desc}")

    # Omen → tag stripped
    _omen = anomaly.get("omen")
    if isinstance(_omen, dict) and _omen.get("tag"):
        _omen_line = _omen.get("line", "")
        if _omen_line:
            # [2026-10-09 전조 문구] 전조와 발화가 같은 문장(큐 line)이라 전조 턴과 발화 턴에 같은 그림이 두 번 그려졌다
            #   (수처시티 붉은 점 T4·T5). 전조는 다른 결로 앞서 보이고, 사건 제 그림은 발화 턴 몫. 1차 문안("arrives as a sign
            #   ahead of it")은 렌더가 전조를 계획에서 통째로 뺐다(2/2) → '이번 턴 장면에 싣는다'를 머리에.
            parts.append(
                f"omen: {_omen_line} — "
                "this turn carries a sign of it in the scene: another sense, a smaller trace, a side effect; "
                "its own image waits for the turn it fires."
            )

    # Condition resolved → text only
    _cond_resolved_log = anomaly.get("conditions_resolved_log")
    if _cond_resolved_log:
        parts.append(str(_cond_resolved_log))

    # Clock Progress / Doom Shift → REMOVED (system_msg only)
    doom = bus.doom if isinstance(bus.doom, dict) else {}

    # [2026-10-25 C74 판4] 시계 끝남("X has become real") · 목표 깎임 · 진전 · 임박 시계 · 진전 없는 목표 약화 줄 → Slot 15
    #   `<In_Motion>` 의 "This turn" 줄 · 시계 / 목표 줄 꼬리표(in_motion.turn_items · render_block). 같은 일이 세 자리(11 · 29 · 30)로
    #   나뉘어 가던 것을 한 블록으로(C74 W2-4, 레티어스 판정 7). 클라이맥스 줄은 판 전체 사건이라 여기 남는다.

    # Climax → fact
    if doom.get("climax_triggered"):
        parts.append("critical point — everything has converged at once. the final choice.")

    # Defense → REMOVED (mechanical log)

    # Status effects → tag stripped
    status_seen = set()
    for container in (bus.vigor, bus.composure, bus.dai):
        if not isinstance(container, dict):
            continue
        for key, label_kr in (("new_status_effects", "begins"), ("expired_status_effects", "ends")):
            entries = container.get(key, [])
            if not isinstance(entries, list):
                continue
            for entry in entries:
                name = ""
                hint = ""
                if isinstance(entry, dict):
                    name = str(entry.get("name", "")).strip()
                    hint = str(entry.get("narrative_hint", "")).strip()
                else:
                    name = str(entry).strip()
                if not name:
                    continue
                sig = (label_kr, name)
                if sig in status_seen:
                    continue
                status_seen.add(sig)
                # [2026-07-06] 시스템 라벨("X begins") → 신체-우선 연출 지시 (body-before-mind).
                # 상태명은 렌더러 참조용 컨텐츠 — 산문에 이름/상태언어로 올리지 말라는 프레임 내장.
                if label_kr == "begins":
                    line = (f"{name}: onset — the body notices before the mind names it "
                            "(a sensation, a reflex, a small failure of ease). "
                            "the state itself stays unnamed; no status language in prose.")
                else:
                    line = (f"{name}: lifting — register it as absence "
                            "(breath comes easier, a weight quietly gone). no announcement.")
                if hint:
                    line += f" ({hint})"
                parts.append(line)

    # Flashback → tag stripped
    flashback = bus.dai.get("flashback_eval") if isinstance(bus.dai, dict) else None   # [2026-10-07] 옛 flashback_result 폴백 삭제(생산자 0)
    if isinstance(flashback, dict):
        declaration = str(flashback.get("declaration", "")).strip()
        if not declaration:
            declaration = str(flashback.get("reason", "")).strip()
        if declaration:
            parts.append(f"retroactive declaration: {declaration}")

    # [2026-09-06 P8b] 다운타임 산문 힌트 삭제 — 생산자(rest_eval)가 사라졌다. 신설 감지 0.

    if not parts:
        return ""
    return "── Events ──\n" + "\n".join(parts)


# Universal narrative principles — separated from MC Moves (genre flavor)
CONSEQUENCE_DIRECTIVES = {
    "critical_success": (
        "the PC achieves more than they intended. "
        "A new possibility opens, or the PC takes decisive control of the situation. "
        # [2026-09-29 배치2 2a-5] 관계 변화 지시 → 목격 반응(C8). 관계는 관계 상태(Relation)가 쥔다.
        "with NPCs present, their reaction to witnessing it surfaces."
    ),
    "partial": (
        "the PC's intent is achieved, but one unwanted change follows with it. "
        "What was lost, exposed, or complicated takes concrete shape. "
        "a partial success carries a cost; the cost-free version doesn't exist. "
        "with NPCs present, their reaction to witnessing it surfaces. "
        # [2026-10-15 C40 B4] 억까 4단계 결
        "The PC's reach falls a little shy of what was hoped."
    ),
    "failure": (
        "the PC's intent goes unachieved. "
        "The situation is now different from before the attempt — more than 'it didn't work.' "
        "What has changed shows in the scene. "
        "with NPCs present, their reaction to witnessing it surfaces. "
        # [2026-10-15 C40 C3] 실패 대가 5단계
        "The PC carries the cost out of the scene and stands somewhere tighter than before."
    ),
    "critical_failure": (
        "an irreversible change occurs in this scene. "
        "The opposite of the PC's intent comes about, or an unforeseen reality reveals itself. "
        "The world after this moment differs from the world before. "
        "with NPCs present, their reaction to witnessing it surfaces. "
        # [2026-10-15 C40 C3] 실패 대가 5단계
        "What it costs the PC stays: a wound, exhaustion, or a loss that outlasts the scene."
    ),
}


def _build_judgment_layer(bus, mask: str) -> str:
    judgment = bus.judgment if isinstance(bus.judgment, dict) else {}
    if not judgment.get("active"):
        return ""

    meta = judgment.get("meta", {}) if isinstance(judgment.get("meta"), dict) else {}
    action = str(meta.get("action", "action"))
    result = str(judgment.get("result", "failure"))
    reason = str(judgment.get("reason", "")).strip()

    dai = bus.dai if isinstance(bus.dai, dict) else {}
    pos_value = _to_float((dai.get("position", {}) or {}).get("value", 0.5), 0.5)
    pos_tier = _position_tier(pos_value)

    move = _mc_move(pos_tier, result)

    favorable, against = _collect_aspect_stance(dai.get("aspects", []))

    # Natural language — no tags, no framework labels
    _result_kr = RESULT_LABEL_EN   # [2026-10-01 1차] 모듈 상수로 올림(텔레스코프 [Outcome] 시드와 같은 말) — 동작 무변경
    reason_part = f" {reason}" if reason else ""
    # [2026-10-01 2단계] ①첫 줄 동사는 attempt 입력에만 "attempted" — decree·probe는 행위가 착지한다(RB_PC DECREE).
    #   ②서사 콜(Mira)이 굴림 결과를 알고 쓴 roll_outcome 이 있으면 MC 행렬 문안·CONSEQUENCE_DIRECTIVES 를 싣지 않는다 —
    #   대가를 각자 묻던 일반 목소리(행렬·결과 지시·[Outcome] clause)가 장면 구체 한 줄([Outcome] 시드)로 모인다.
    #   roll_outcome 이 없으면(서사 콜 실패) 종전 그대로(폴백). 스펙 composition/분석렌더_2단계_판정먼저_스펙_2026-10-01.md §4.
    _is_attempt = str(dai.get("input_mode") or "decree").strip().lower() == "attempt"
    _ro = dai.get("roll_outcome")
    _has_ro = isinstance(_ro, str) and bool(_ro.strip())
    lines = [
        f"{mask} attempted '{action}'.{reason_part}" if _is_attempt else f"{mask}: '{action}'.{reason_part}",
        f"{_result_kr.get(result, result)} — {position_label(pos_value)}.",
    ]
    if not _has_ro:
        lines.append(move)
    if favorable:
        lines.append("in favor: " + ", ".join(favorable))
    if against:
        lines.append("against: " + ", ".join(against))

    # 범용 서사 원칙 (장르 불문) — tag stripped
    cons_dir = "" if _has_ro else CONSEQUENCE_DIRECTIVES.get(result, "")
    if cons_dir:
        lines.append(cons_dir)

    # [2026-10-15 C42] Effort / Absorb(각오 / 흡수) 줄 삭제 — 각오 퇴역.

    return "\n".join(lines)


def _build_atmosphere_layer(context, bus) -> str:
    parts: List[str] = []

    # N8 경량 채택: [지배] 태그 + [전환] 디렉티브만 사용 (합성 파이프라인 제거)
    dai = bus.dai if isinstance(bus.dai, dict) else {}

    # [전환] 디렉티브 — 에너지 방향 변화 시에만
    from notation_compositor import compose_transition
    # [2026-07-02 shape fix] scene_continuity는 {"frames":[...]} — 마지막 프레임(=직전 턴)을 넘겨야
    # 실제 전환에서만 발화. (기존엔 dict 통째로 넘겨 prev_energy 항상 idle → 상승 지속 중에도
    # 매 턴 "정적이 깨진다" 오발화 + 진짜 전환(rising→falling 등)은 영구 침묵)
    _sc_raw = (context.narrative_anchors or {}).get("session_memory", {}).get("scene_continuity", {})
    _sc_frames = _sc_raw.get("frames", []) if isinstance(_sc_raw, dict) else []
    prev_frame = _sc_frames[-1] if _sc_frames else {}
    current_energy = str(dai.get("energy_direction", "idle"))
    _transition = compose_transition(prev_frame, current_energy)
    if _transition:
        parts.append(_transition)

    # [지배] 태그 — 장르+씬타입 기반 우선 레이어 1줄
    from notation_compositor import LAYER_PRIORITY, GENRE_NOTATION_WEIGHTS
    scene_type = str(dai.get("scene_type", "normal"))
    mechanic = context.request.genres.get("mechanic", {})
    genre = mechanic.get("primary_lens", "")
    _weights = GENRE_NOTATION_WEIGHTS.get(genre, {})
    _dominant = _weights.get("dominant", "")
    _reframe = _weights.get("reframe", {})
    if _dominant:
        _lens = _reframe.get(_dominant, "")
        _lens_tag = f" ({_lens})" if _lens else ""
        parts.append(f"[dominant:{_dominant}{_lens_tag}]")

    vigor_val = int(_to_float((bus.vigor or {}).get("value", 100), 100))
    composure_val = int(_to_float((bus.composure or {}).get("value", 100), 100))

    # Vigor → directing notation
    if vigor_val >= 70:
        parts.append(_VIGOR_NOTATION["high"])
    elif vigor_val >= 40:
        pass  # 정상 구간 — directive 없음
    elif vigor_val >= 15:
        parts.append(_VIGOR_NOTATION["strained"])
    else:
        parts.append(_VIGOR_NOTATION["collapsing"])

    # Composure → directing notation
    if composure_val >= 70:
        parts.append(_COMPOSURE_NOTATION["high"])
    elif composure_val >= 40:
        pass  # 정상 구간
    elif composure_val >= 15:
        parts.append(_COMPOSURE_NOTATION["strained"])
    else:
        parts.append(_COMPOSURE_NOTATION["collapsing"])

    # Mixed conditions → directing notation
    v_low = vigor_val <= 39
    c_low = composure_val <= 39

    if v_low and c_low:
        parts.append(_MIXED_NOTATION["desperate"])
    elif vigor_val >= 70 and c_low:
        parts.append(_MIXED_NOTATION["reckless"])
    elif composure_val >= 70 and v_low:
        parts.append(_MIXED_NOTATION["fragile"])

    # NPC Reaction → natural language
    if composure_val <= 14:
        parts.append("those nearby sense the PC's instability — concern, avoidance, or exploitation.")
    if vigor_val <= 14:
        parts.append("those nearby witness the PC's physical limit.")

    # Doom = Chapter Volume Gauge (Phase × Lens) — 둠 리브랜드 산문 주입
    # [2026-10-06 어휘 V2] 머리 이름 "Tension" → "Chapter", "register" → "genre".
    #   Tension은 한 입력에 넷(Story Direction 긴장 축·Energy·이 게이지·스토리라인 열린 질문)이었고,
    #   모델이 이 게이지를 장면 긴장 수치로 읽었다("tension is low, 30%") — 정적 MEASURE가 tension으로
    #   리듬을 정한다. register는 정적 쪽에서 인물 말씨다. genre는 충돌 사다리 6 "Active genre conventions"와 같은 말.
    # phase × lens atmosphere block을 산문 주입의 진짜 매체로 사용.
    # 페이즈 letter(起承轉結間)는 식별자, lens(noir/comedy/romance/drama)는 톤.
    doom_val = int(_to_float((bus.doom or {}).get("value", 0), 0))
    import config as _cfg
    lens_tags = bus.doom.get("lens_tags", []) if isinstance(bus.doom, dict) else []
    phase = bus.doom.get("chapter_phase", "") if isinstance(bus.doom, dict) else ""
    if not phase:
        # [C90 10-28] doom_module 과 같은 경계(문턱에서 파생) — 옛: 첫 렌즈 표
        phase = _cfg.chapter_phase(doom_val, lens_tags, bus.doom.get("flavor_tags", []) if isinstance(bus.doom, dict) else [])

    if not lens_tags:
        # C-Lens 미활성 → default 블록만
        block = _cfg.get_lens_atmosphere("default", phase)
        if block:
            parts.append(f"[Chapter {doom_val}% — phase {phase}]\n{block}")
    elif len(lens_tags) == 1:
        # 단일 lens
        block = _cfg.get_lens_atmosphere(lens_tags[0], phase)
        if block:
            parts.append(f"[Chapter {doom_val}% — phase {phase}, {lens_tags[0]}]\n{block}")
    else:
        # 다중 lens (hybrid) — 양쪽 block + neither-erases 디렉티브
        blocks = [(lens, _cfg.get_lens_atmosphere(lens, phase)) for lens in lens_tags]
        blocks = [(l, b) for l, b in blocks if b]
        if blocks:
            header = f"[Chapter {doom_val}% — phase {phase}, dual genre: {' × '.join(l for l, _ in blocks)}]"
            joined = "\n× crosscut with:\n".join(b for _, b in blocks)
            parts.append(f"{header}\n{joined}\n— neither genre erases the other; both qualities in the same beat")

    # 챕터 종결 라벨 (climax 발동 직후, 間 페이즈)
    if isinstance(bus.doom, dict) and bus.doom.get("intermission_active"):
        parts.append("chapter close — epilogue / lingering phase. the crisis's business gets done here: questions for whoever was taken, accounts and blame settled, rewards shared, wounds tended, the next step talked over. new clocks carry over to the next chapter.")

    # ◎ optics (conditional — derived from DAI fields)
    dai = bus.dai if isinstance(bus.dai, dict) else {}
    optical: List[str] = []

    # [multiple-exposure]: memory_triggers → time overlap
    mem_triggers = dai.get("memory_triggers", [])
    if isinstance(mem_triggers, list) and mem_triggers:
        optical.append("[multiple-exposure]")

    # [polarizer]: self_opacity → facade crack (POV guard: surface contradictions only)
    psyche_states = dai.get("psyche_states", {})
    if isinstance(psyche_states, dict):
        for _npc_data in psyche_states.values():
            if isinstance(_npc_data, dict):
                if (_npc_data.get("psyche") or {}).get("self_opacity"):
                    optical.append("[polarizer]")
                    break

    # [infrared]: leak_risk >= medium → behavioral leak (POV guard: distortion, not secrets)
    npc_knowledge = dai.get("npc_knowledge", {})
    if isinstance(npc_knowledge, dict):
        for _kn_data in npc_knowledge.values():
            if isinstance(_kn_data, dict) and _kn_data.get("leak_risk") in ("medium", "high"):
                optical.append("[infrared]")
                break

    # [solarization]: doom >= 80 → inversion
    if doom_val >= 80:
        optical.append("[solarization]")

    # [vignette]: position <= 0.15 → tunnel vision
    pos = dai.get("position", {}) if isinstance(dai.get("position"), dict) else {}
    if _to_float(pos.get("value", 0.5), 0.5) <= 0.15:
        optical.append("[vignette]")

    if optical:
        parts.append("◎ optics: " + " ".join(optical))

    # Pacing rules (narrative constraints only — rhythm/density covered by ♪▶◎)
    if doom_val < 20:
        parts.append("the existing tension stays unresolved; this turn plants seeds, not payoffs.")
    elif doom_val >= 80:
        parts.append("resolution comes only through PC action; the way out is earned, not handed over.")

    # Distant conditions → natural language (camera NOT there)
    _st_atm = {}
    _ch_atm = (context.narrative_anchors or {}).get("channel_id", "")
    if _ch_atm:
        import domain_manager as _dm_atm
        _st_atm = _dm_atm.get_storyteller_state(_ch_atm)
    _all_conds_atm = _st_atm.get("active_conditions", [])
    _pc_loc_atm = (bus.dai.get("current_location", "") if isinstance(bus.dai, dict) else "").strip()

    for _c in _all_conds_atm:
        _cloc = (_c.get("location") or "").strip()
        if _cloc and _cloc != _pc_loc_atm:
            parts.append(f"a sense of {_c.get('tag', '?')} drifts in from far off")
    # [World Conditions] → REMOVED (Events Active와 중복)

    # 서사 공간 → tag stripped
    narrative_space = int(_to_float((bus.doom or {}).get("narrative_space", 0), 0))
    if narrative_space > 0:
        if narrative_space >= 15:
            intensity = "wide"
        elif narrative_space >= 8:
            intensity = "moderate"
        else:
            intensity = "small"
        mechanic = context.request.genres.get("mechanic", {})
        primary_res = mechanic.get("primary_resource") or "vigor"
        if primary_res == "vigor":
            parts.append(
                f"{intensity} narrative space — the tension has eased. room to:\n"
                "- deepen character relationships (dialogue, emotional exchange, confirming bonds)\n"
                "- the world's response to the user's actions, and its echo\n"
                "- a natural seed of foreshadowing for the next crisis"
            )
        else:
            parts.append(
                f"{intensity} narrative space — the everyday has returned. room to:\n"
                "- deepen relationships (small talk, emotional exchange)\n"
                "- the effect the user's choices had on those around them\n"
                "- a natural seed of new change"
            )

    # Clock surfacing → tag stripped (clock name removed, threat only)
    clocks = (bus.doom or {}).get("clocks", [])
    if isinstance(clocks, list):
        mechanic = context.request.genres.get("mechanic", {})
        primary_res = mechanic.get("primary_resource") or "vigor"
        for clock in clocks:
            if not isinstance(clock, dict) or clock.get("resolved"):
                continue
            segments = int(_to_float(clock.get("segments", 4), 4))
            filled = int(_to_float(clock.get("filled", 0), 0))
            if segments <= 0:
                continue
            ratio = filled / segments
            threat = clock.get("threat", "")
            if ratio >= 0.75:
                if primary_res == "vigor":
                    parts.append(f"the omen of {threat} is clear — it takes concrete shape around the PC; the POV stays with the PC.")
                else:
                    parts.append(f"the omen of {threat} is felt — in shifts among those nearby, in subtle atmosphere; the POV stays with the PC.")
            elif ratio >= 0.5:
                if primary_res == "vigor":
                    parts.append(f"a sign of {threat} hinted through sensory detail.")
                else:
                    parts.append(f"the omen of {threat} hinted through small everyday dissonances.")

    # Status effects → tag stripped
    status_effects = (context.narrative_anchors or {}).get("status_effects", [])
    if isinstance(status_effects, list):
        for status in status_effects[:3]:
            if not isinstance(status, dict):
                continue
            name = str(status.get("name", "")).strip()
            if not name:
                continue
            hint = str(status.get("narrative_hint", status.get("description", ""))).strip()
            if hint:
                parts.append(f"{name}: {hint}")

    # POV Lock → tag stripped
    if isinstance(clocks, list):
        active_clocks = [c for c in clocks if isinstance(c, dict) and not c.get("resolved")]
        if active_clocks:
            pass  # [2026-09-29 반죽] Clock POV 문장 → RB_WORLD Time("Clock events render strictly within the PC's POV")

    if not parts:
        return ""
    return "── Atmosphere ──\n" + "\n".join(parts)


def _build_aspects_layer(context, bus) -> str:
    """
    Aspects (시스템 교차 결합) 산문 디렉티브. 활성 라벨 → typological 텍스트만.
    라벨 자체는 Pro 산문에 직접 노출 X (내부 식별자).
    Arc 사이클 시 백업한 자리 V3 재이식.
    """
    try:
        import narrative_tracker as _nt
        import domain_manager as _dm
        import config as _cfg
    except Exception:
        return ""

    # primary axis (자원 임계 평가용)
    try:
        mechanic = context.request.genres.get("mechanic", {})
        primary_axis = mechanic.get("primary_resource") or "vigor"
    except Exception:
        primary_axis = "vigor"

    # arc proximity / armed 평가용 — [2026-10-24 C75 G2] 아크 = 이름 얻은 우물(줄거리 상태가 아니라 well_store)
    channel_id = (context.narrative_anchors or {}).get("channel_id", "") if context.narrative_anchors else ""
    state = {}
    if channel_id:
        try:
            import well_store as _ws_asp
            state = {"arcs": _ws_asp.arcs(channel_id, pc_location=(getattr(bus, "dai", None) or {}).get("current_location"))}
        except Exception:
            state = {}

    # 활성 라벨 평가
    try:
        active = _nt.compute_aspects(bus, state, primary_axis=primary_axis)
    except Exception:
        return ""

    if not active:
        return ""

    # typological 디렉티브 변환
    directives = _cfg.ASPECTS_DIRECTIVES or {}
    lines = []
    for label in active:
        text = directives.get(label, "")
        if text:
            lines.append(text)

    if not lines:
        return ""

    return (
        "── Convergence Flow ──\n"
        + "\n".join(lines)
        + "\n(only the grain of these joins carries into the prose; the naming labels stay out of it.)"
    )


def _build_system_message(bus) -> str:
    chunks: List[str] = []

    judgment = bus.judgment if isinstance(bus.judgment, dict) else {}
    if judgment.get("output"):
        chunks.append(str(judgment.get("output")))

    anomaly = bus.anomaly if isinstance(bus.anomaly, dict) else {}
    if anomaly.get("triggered"):
        tag = anomaly.get("tag", "anomaly")
        # [2026-10-24 C74 W1-5] 약속 기한은 "[이변] thread_due:T3"(기계 이름)가 채널에 샜다 → "[기한] 제목".
        if str(tag).startswith("thread_due:"):
            chunks.append(f"[기한] {anomaly.get('due_title') or str(tag).split(':', 1)[1]}")
        elif str(tag).startswith("collision:"):
            # [2026-10-25 C75 G3 검토 #3] 충돌파 — "[이변] collision:3-7"(기계 이름) 대신 두 흐름 이름
            chunks.append(f"[이변] {anomaly.get('due_title') or '두 큰 흐름이 맞부딪쳤다'}")
        # [2026-10-25 C77 A-6 · Q18] 그 밖 이변 태그(`[이변] <Theoria 제안>`)는 채널에 안 낸다 — 레티어스(10-24) "애초에 산문
        #   재료라 나한테 안 보여도 된다". 산문은 디렉티브로 그대로 받는다. 기한 · 충돌 두 줄은 사람 몫이라 남긴다.

    doom = bus.doom if isinstance(bus.doom, dict) else {}
    # relief_log 제거 (2026-05-23) — legacy 위기진폭 잔재
    # [2026-09-06 P8b] mental_pressure_log 제거 — 페이즈 기반 회복 공식과 함께 생산자가 사라졌다.
    for key in ("clock_log", "log"):
        val = doom.get(key)
        if val:
            chunks.append(str(val))

    vigor = bus.vigor if isinstance(bus.vigor, dict) else {}
    if vigor.get("log"):
        chunks.append(str(vigor.get("log")))

    deduped: List[str] = []
    seen = set()
    for chunk in chunks:
        text = chunk.strip()
        if not text or text in seen:
            continue
        seen.add(text)
        deduped.append(text)

    return "\n".join(deduped).strip()

# =========================================================
# [2026-09-13 S2 T1] 재등장 인물 원문 근거 (recall_evidence)
#   30창 밖으로 밀려난 인물이 이번 입력에 다시 불려 나왔을 때, 그 인물에 대한
#   **로그 원문 행**을 Theoria(분석·JSON 입, echo 0)에만 넘긴다. 산문 입엔 안 간다.
#   판정 ①=엄격: 입력에 있고 AND 30창에 없고 AND scene/nearby cast에도 없을 때만.
# =========================================================
import re as _re_recall

_RECALL_WORDCHAR = _re_recall.compile(r"[0-9A-Za-z\uac00-\ud7a3]")


def _recall_name_in_input(name: str, text: str) -> int:
    """이름이 입력 텍스트에 '단어로' 나오면 그 위치, 아니면 -1.

    2자 이하 이름은 **왼쪽 경계**만 요구한다(앞 글자가 한글/영문/숫자가 아님).
    오른쪽은 한국어 조사가 붙으므로 경계를 요구하지 않는다("미라가", "미라는").
    이 왼쪽 경계가 09-02·09-10에서 났던 "금"⊂"지금" 류 오탐을 막는다.
    """
    if not name or not text:
        return -1
    start = 0
    while True:
        i = text.find(name, start)
        if i < 0:
            return -1
        if len(name) >= 3:
            return i
        if i == 0 or not _RECALL_WORDCHAR.match(text[i - 1]):
            return i
        start = i + 1


def _recall_fmt_game_time(gt) -> str:
    """game_time dict → '(작중 1년 3월 5일 14:00) ' 접두. 없으면 빈 문자열."""
    if not isinstance(gt, dict):
        return ""
    try:
        return (f"(작중 {gt.get('year', 1)}년 {gt.get('month', 1)}월 {gt.get('day', 1)}일 "
                f"{int(gt.get('hour', 0)):02d}:{int(gt.get('minute', 0)):02d}) ")
    except (TypeError, ValueError):
        return ""


def _recall_msg_order(r) -> int:
    """원문 줄 시간 키 — 디스코드 message_id(눈송이)는 시간순으로 커진다. 숫자가 아니면 0."""
    try:
        return int(str((r or {}).get("message_id") or "0"))
    except (TypeError, ValueError):
        return 0


def _recall_rows_v2(channel_id: str, canon: str, matched: str, names: list, rows_per: int,
                    search_limit: int, history_text: str) -> list:
    """[2026-10-26 C80] T1 재등장 근거 — DEEP 색인 길 + 옛 LIKE, 최신순(옛 search_history_log 와 같은 순서).

    채우는 순서: ① 색인 턴(중요도 높은 → 최신) 원문 중 이름이 글자로 든 줄 ② 옛 LIKE(이름이 든 최신 줄)
    ③ 그래도 모자라면 색인 턴의 산문(이름 없이 엔티티로만 찍힌 턴 — 옛 LIKE 가 못 찾던 몫). ③은 지금 창에 이미 있는
    줄이면 뺀다. 이름이 든 줄이 늘 먼저다 — 이름 없는 산문이 이름 든 옛 줄을 밀어내지 않게."""
    named, loose, seen = [], [], set()

    def _add(lst, r):
        k = (r.get("message_id"), str(r.get("content") or "")[:80])
        if k in seen:
            return
        seen.add(k)
        lst.append(r)

    try:
        import memory_index
        turns = memory_index.turns_for(channel_id, canon, aliases=names, limit=search_limit)
    except Exception as _e_ix:
        logger.debug(f"[Evidence] T1 index skip {canon}: {_e_ix}")
        turns = []
    for tr in turns:
        if len(named) >= rows_per:
            break
        raw = memory_index.raw_for_turn(channel_id, tr.get("turn"), row=tr)
        if not raw:
            continue
        hit = [r for r in raw if isinstance(r, dict)
               and any(n and n in str(r.get("content") or "") for n in names)]
        if hit:
            _add(named, hit[0])
            continue
        prose = [r for r in raw if isinstance(r, dict) and str(r.get("role") or "") == "Model"]
        _p = prose[-1] if prose else None
        if _p and str(_p.get("content") or "")[:60] not in (history_text or ""):
            _add(loose, _p)
    if not named and not loose:
        # 색인이 아무것도 못 냈다(옛 채널 · 색인 전 턴) — 옛 LIKE 그대로(순서 · 모양 옛것과 같다)
        try:
            return _ss_search(channel_id, matched, search_limit) or []
        except Exception as _e_q:
            logger.debug(f"[Evidence] T1 search skip {canon}: {_e_q}")
            return []
    if len(named) < rows_per:
        try:
            for r in (_ss_search(channel_id, matched, search_limit) or []):
                if isinstance(r, dict):
                    _add(named, r)
        except Exception as _e_q:
            logger.debug(f"[Evidence] T1 search skip {canon}: {_e_q}")
    rows = (sorted(named, key=_recall_msg_order, reverse=True)[:rows_per]
            + sorted(loose, key=_recall_msg_order, reverse=True))[:rows_per]
    return sorted(rows, key=_recall_msg_order, reverse=True)


def _ss_search(channel_id: str, query: str, limit: int) -> list:
    import sqlite_store as _ss
    return _ss.search_history_log(channel_id, query, limit=limit)


def _recall_clip_around(content: str, names: list, line_chars: int) -> str:
    """[2026-10-26 C80] 줄 상한 안에 이름이 안 보이면 이름 앞뒤 창으로 자른다(색인 길은 산문 통째가 올 수 있다)."""
    pos = min([content.find(n) for n in names if n and n in content] or [-1])
    if pos < 0 or pos + 20 <= line_chars:
        return content[:line_chars]
    start = max(0, pos - line_chars // 3)
    return "…" + content[start:start + line_chars - 1]


def _build_recall_evidence(channel_id: str, user_input: str, history_text: str,
                           anchors: dict) -> dict:
    """재등장 인물별 로그 원문 행. 실패·해당 없음이면 빈 dict(키를 안 넣는다)."""
    import config as _cfg
    if not getattr(_cfg, "V10_HISTORY_EVIDENCE", False):
        return {}
    max_npcs = int(getattr(_cfg, "RECALL_EVIDENCE_MAX_NPCS", 3))
    rows_per = int(getattr(_cfg, "RECALL_EVIDENCE_ROWS_PER_NPC", 2))
    line_chars = int(getattr(_cfg, "RECALL_EVIDENCE_LINE_CHARS", 200))
    search_limit = int(getattr(_cfg, "RECALL_EVIDENCE_SEARCH_LIMIT", 4))

    text = user_input or ""
    if not text or not channel_id:
        return {}

    import npc_manager as _nm
    npcs = domain_manager.get_npcs(channel_id) or {}

    # PC 마스크는 제외 (07-19 교훈: 명부 대조 코드는 PC 포함 여부를 항상 점검).
    pc_masks = set()
    for _pc in (anchors.get("all_pcs") or {}).values():
        _m = (_pc or {}).get("mask")
        if _m:
            pc_masks.add(str(_m))

    cast = list(anchors.get("scene_cast") or []) + list(anchors.get("nearby_cast") or [])

    def _in_cast(nm: str) -> bool:
        return any(nm and c and (nm in str(c) or str(c) in nm) for c in cast)

    # 후보: 이름 + aliases → 대표 이름(키)로 접는다.
    hits = []  # (pos, canon, matched)
    _names_of = {}  # [C80] canon → 이름 + 별칭(색인 조회 · 줄 창)
    for canon, data in npcs.items():
        if not isinstance(canon, str) or domain_manager.is_pc_name(canon, pc_masks):   # [2026-10-07] 가면 이름 규칙
            continue
        try:
            if _nm.get_npc_status(data) == "dead":
                continue
        except Exception:
            pass
        names = [canon]
        for a in ((data or {}).get("aliases") or []):
            if isinstance(a, str) and a and not domain_manager.is_pc_name(a, pc_masks):
                names.append(a)
        _names_of[canon] = names
        best = -1
        matched = canon
        for nm in names:
            pos = _recall_name_in_input(nm, text)
            if pos < 0:
                continue
            # 엄격: 30창에 있으면 재등장이 아니다 / 무대·근접에 있어도 아니다.
            if nm in (history_text or "") or _in_cast(nm):
                best = -1
                break
            if best < 0 or pos < best:
                best = pos
                matched = nm
        if best >= 0 and not _in_cast(canon) and canon not in (history_text or ""):
            hits.append((best, canon, matched))

    hits.sort(key=lambda x: x[0])
    hits = hits[:max_npcs]
    if not hits:
        return {}

    import sqlite_store as _ss
    _v2 = bool(getattr(_cfg, "MEMORY_LAYERS_V2", False))
    out = {}
    rows_total = 0
    for _pos, canon, matched in hits:
        if _v2:
            rows = _recall_rows_v2(channel_id, canon, matched, _names_of.get(canon) or [canon], rows_per,
                                   search_limit, history_text)
        else:
            try:
                rows = _ss.search_history_log(channel_id, matched, limit=search_limit) or []
            except Exception as _e_q:
                logger.debug(f"[Evidence] T1 search skip {canon}: {_e_q}")
                continue
        rows = rows[:rows_per]          # search_history_log는 최신순
        if not rows:
            continue                     # 행 0이면 그 인물 생략
        lines = []
        for r in reversed(rows):         # 렌더는 오래된→최신
            if not isinstance(r, dict):
                continue
            content = (_recall_clip_around(str(r.get("content") or ""), _names_of.get(canon) or [canon], line_chars)
                       if _v2 else str(r.get("content") or "")[:line_chars])
            if not content:
                continue
            lines.append(f"{_recall_fmt_game_time(r.get('game_time'))}{r.get('role')}: {content}")
        if lines:
            out[canon] = lines
            rows_total += len(lines)

    if out:
        logger.info(f"[Evidence] T1 recall npcs={len(out)} rows={rows_total}")
    return out


def convert_to_game_context(channel_id: str, user_id: str, user_input: str, lore_chunks_ranked: list = None) -> GameContext:
    """[UNE Bridge] ParticipantData -> GameContext"""
    from orchestration_context import GameContext, RequestData, SharedBus

    p_data = domain_manager.get_participant_data(channel_id, user_id)
    mem = p_data.get("ai_memory", {}) if p_data else {}
    world = domain_manager.get_world_state(channel_id)

    # Genre mapping
    genres_raw = domain_manager.get_active_genres(channel_id)
    if isinstance(genres_raw, dict) and "layers" in genres_raw:
        layers = genres_raw["layers"]
        mechanic = genres_raw.get("mechanic_profile", {})
        genres = {
            "stage": layers.get("world_setting", []),
            "flavor": layers.get("style_tech", []),
            "lens": layers.get("narrative_tone", []),
            "atmosphere": genres_raw.get("atmosphere_guide", ""),
            "mechanic": mechanic,
        }
    else:
        genres = {
            "stage": [genres_raw[0]] if isinstance(genres_raw, list) and genres_raw else ([str(genres_raw)] if genres_raw else []),
            "flavor": [],
            "lens": [],
            "atmosphere": "",
            "mechanic": {},
        }
    # 하위 호환: str이 들어오면 List로 래핑
    for key in ["stage", "flavor", "lens"]:
        val = genres[key]
        if isinstance(val, str):
            genres[key] = [val] if val else []

    # Active Modules
    active_modules = domain_manager.get_active_modules(channel_id)

    # Lore Summary (V4) + Chunks (V5)
    lore_summary = domain_manager.get_lore_summary_data(channel_id)
    lore_chunks = domain_manager.get_lore_chunks(channel_id)

    # History & Lore Text (V4 - fallback)
    history = domain_manager.get_history(channel_id)
    recent_history = history[-30:] if history else []  # Last 30 turns
    history_text = "\n".join([f"{h['role']}: {h['content']}" for h in recent_history])
    lore_text = domain_manager.get_lore(channel_id)

    # Narrative Anchors (행동자 PC)
    # [2026-09-16 시트 2차 §8 뷰 하나] 서술 = get_unified_player_info의 좌뇌용 모양(PC 페이지 절).
    _pc_anchor = domain_manager.get_unified_player_info(channel_id, user_id, shape="anchor")
    anchors = {
        "channel_id": channel_id,
        "sheet": _pc_anchor.get("sheet", ""),
        "passives": mem.get("passives", []),
        "status_effects": p_data.get("status_effects", []) if p_data else [],
        # [2026-10-15 C42] 타격칸 이름(빈 칸 제외) — Theoria PC 블록 `- Strikes:`·판정 active_strikes 확인이 읽는다.
        "strikes": game_character.strike_names(p_data or {}),
        # [2026-10-23 C61] 소지품 정본 = 기본 선언 목록 — [{name, qty}]. Theoria `- Inventory:`·판정 `_item_names`.
        "inventory": game_character.sojipin_anchor(channel_id),
        "memos": mem.get("memos", [])
    }

    # 모든 활성 PC 정보 수집 (다인 플레이 지원)
    all_participants = domain_manager.get_domain(channel_id).get("participants", {})
    all_pcs = {}
    for uid, pdata in all_participants.items():
        if pdata.get("status") == "active":
            pmem = pdata.get("ai_memory", {})
            all_pcs[uid] = {
                "mask": pdata.get("mask", "Unknown"),
                "sheet": domain_manager.get_unified_player_info(channel_id, uid, shape="anchor").get("sheet", ""),
                "passives": pmem.get("passives", []),
                "strikes": game_character.strike_names(pdata),   # [2026-10-15 C42]
                # [Phase 2.5] 활력은 레지스트리 우선(PC별 슬롯), 없으면 옛 자리 폴백.
                "vigor_value": _cv_vigor(channel_id, uid, pmem),
                "composure_value": _cv_composure(channel_id, uid, pmem),  # [2026-09-24 감사] 동결된 옛 자리 읽기 교정
            }
    anchors["all_pcs"] = all_pcs
    anchors["acting_user_id"] = user_id

    # NPC Knowledge & Attitudes (피드백용)
    anchors["stored_npc_knowledge"] = domain_manager.get_npc_knowledge(channel_id)
    # [2026-09-15 관계 통합] 4b 앵커 = **행동 PC를 target으로 하는 엣지**(다인에서 다른 PC 값이 앵커로 새지 않게).
    #   마스크 없으면 종전(NPC별 최근 관측 엣지).
    _acting_mask = (p_data or {}).get("mask") if p_data else None
    anchors["stored_npc_attitudes"] = domain_manager.get_npc_attitudes(channel_id, pc=_acting_mask or None)
    # [2026-08-02 B축 지속] 이전 턴 soma(polyvagal/dissociation) — `Track across turns` 집행 재료.
    try:
        anchors["stored_npc_soma"] = (domain_manager.get_world_state(channel_id) or {}).get(
            "npc_soma_states", {}) or {}
    except Exception:
        anchors["stored_npc_soma"] = {}
    # [2026-10-07 a묶음 A4] 지난 턴 관계 상태(phase·attachment) — 4b Relation(prev) 재료.
    try:
        anchors["stored_npc_relation"] = (domain_manager.get_world_state(channel_id) or {}).get(
            "npc_relation_states", {}) or {}
    except Exception:
        anchors["stored_npc_relation"] = {}
    # [2026-08-11 사망 파이프라인] 생존축이 anchors로 안 흘러 theoria가 볼 수가 없었다.
    #   가장 싼 배선 = **비활성 이름만** 여기서 동봉(전체 status 맵은 대부분 active라 낭비).
    #   빈 dict가 정상값(전원 생존) — 소비처는 `.get(name)`으로 조용히 통과한다.
    try:
        import npc_manager as _npm_st
        anchors["npc_inactive"] = {
            _n: _npm_st.get_npc_status(_d)
            for _n, _d in (domain_manager.get_npcs(channel_id) or {}).items()
            if not _npm_st.is_npc_active(_d)
        }
    except Exception:
        anchors["npc_inactive"] = {}

    # [V10 Sprint 4] 막간 장부 (이번 턴 재구성분) — Theoria가 막간을 알아야 NPC 심리 추론 정합
    try:
        import interim_engine
        _ledger_block = interim_engine.get_last_block(channel_id)
        if _ledger_block:
            anchors["interim_ledger"] = _ledger_block
    except Exception:
        pass

    # NPC Roster (Theoria용 이름+역할 요약)
    import npc_manager as _npc_mgr
    _npc_mgr.migrate_npc_fields(channel_id)  # desc→description 통일 + 구조화 필드 자동 추출
    anchors["npc_roster"] = _npc_mgr.get_npc_roster(channel_id)

    # [2026-09-02 R4/R5] 3단 출석 앵커 — 스펙 §2.4 / §6 R4·R5.
    # 여기서 한 번만 뽑아 세 소비자에게 나눠 준다: scene_cast(R5 psyche 대상 명시),
    #   nearby_cast(1단 = "들어올 수 있다"), absent+hops(아래 offscreen 후보 정렬).
    # 앵커가 **비었으면 키를 안 넣는다** — 빈 목록을 넣으면 프롬프트에 "아무도 없다"는
    #   블록이 서는데, 위치 미기록과 부재는 다른 사실이다(§2.4 unresolved 규율과 같은 이유).
    _tiers = {}
    try:
        _tiers = _npc_mgr.get_presence_tiers(channel_id) or {}
        if not _tiers.get("unresolved"):
            if _tiers.get("scene"):
                anchors["scene_cast"] = list(_tiers.get("scene") or [])
            if _tiers.get("reachable"):
                anchors["nearby_cast"] = list(_tiers.get("reachable") or [])
    except Exception as _e_tiers:
        logger.debug(f"[presence-check] tiers anchor skip: {_e_tiers}")
        _tiers = {}

    # [2026-09-26 관계 시작값 S3] 관계 기록이 아직 없는 NPC 중 시트에 이 PC 를 가리키는 문장이 있는 사람 → 4b 시작 줄.
    #   순서 = 장면 > 근처 > 이번 입력 이름 > 최근 히스토리 이름. 조건·상한은 domain_manager.relation_start_candidates 한 곳.
    try:
        if _acting_mask:
            _ord_ru = list(anchors.get("scene_cast") or []) + list(anchors.get("nearby_cast") or [])
            _npcs_ru = domain_manager.get_npcs(channel_id) or {}
            for _txt_ru in (user_input or "", history_text or ""):
                for _canon_ru, _d_ru in _npcs_ru.items():
                    if not isinstance(_canon_ru, str) or _canon_ru in _ord_ru:
                        continue
                    _nms_ru = [_canon_ru] + [a for a in ((_d_ru or {}).get("aliases") or []) if isinstance(a, str) and a]
                    if any(_recall_name_in_input(_n, _txt_ru) >= 0 for _n in _nms_ru):
                        _ord_ru.append(_canon_ru)
            _ru = domain_manager.relation_start_candidates(channel_id, _acting_mask, _ord_ru)
            if _ru:
                anchors["relation_unrecorded"] = _ru
    except Exception as _e_ru:
        logger.debug(f"[Relation] start candidates skip: {_e_ru}")

    # [2026-07-02 Offscreen Motion — 뮈토스 이식] 부재 등록 NPC 후보 → Theoria offscreen_trace 재료.
    # 장면 밖 NPC의 "세계가 턴 사이에 움직인 흔적" 공급용. 후보 6명 캡, 실패 무해.
    # [Phase 0] last_seen(emotion_log 기준)으로 부재 '기간' 동봉 — 오래 빈 인물일수록 흔적 가치↑.
    try:
        # [2026-07-28] 구 코드는 get_scene_npc_names(=로어 아닌 **전체** 명부)를 "무대 위"로 봤다.
        # 그래서 _absent_names에는 거의 로어 NPC만 남고, 정작 노리던 "한동안 안 나온 세션 NPC"는
        # 절대 후보에 못 올랐다 — 오프스크린 흔적 기능이 사실상 사문.
        # [2026-09-02 R4] 그 "무대 위의 여집합"을 **2단(absent)**으로 교체한다. 스펙 §2.4.
        #   병: 여집합은 평평하다 — 옆방 사람과 다른 도시 사람이 같은 무게로 후보에 오르고,
        #     1단(근접)이 통째로 여기 섞여 "들어올 수 있는 사람"이 부재 흔적으로 소비됐다.
        #   처방: 후보는 2단만, 정렬은 **홉 오름차순**(옆방 흔적이 다른 도시 흔적보다 먼저).
        #     같은 홉이면 현행 규칙 — 오래 안 보인 사람이 먼저(흔적 가치↑, 아래 last_seen).
        _reg_all = domain_manager.get_npcs(channel_id) or {}
        _att_map = domain_manager.get_npc_attitudes(channel_id) or {}
        _last_seen = {}
        try:
            import narrative_queries as _nq
            _last_seen = _nq.last_seen_turns(channel_id)
        except Exception:
            _last_seen = {}
        _cur_t = int(world.get("turn_index", 0) or 0)

        def _ls_lookup(name: str):
            if name in _last_seen:
                return _last_seen[name]
            _base = name.split("(")[0].strip().lower()
            for _k, _v in _last_seen.items():
                _kb = _k.split("(")[0].strip().lower()
                if _kb == _base or _base in _k.lower() or _kb in name.lower():
                    return _v
            return None

        if (not _tiers) or _tiers.get("unresolved"):
            # 폴백 — PC 노드 미해상(전환 직후·위치 미기록)이거나 tiers 조회 자체가 실패한 경우.
            # 판독기(`get_onstage_npc_names`)가 같은 조건에서 같은 레거시로 내려가므로 정합.
            _scene_set = set(_npc_mgr.get_onstage_npc_names(channel_id, within_turns=2))
            # [2026-08-11 사망 파이프라인] 생존축 필터 — 구멍 순위 2.
            #   죽은/쓰러진 인물은 정의상 **항상 부재**라 상시 offscreen_trace 후보였다
            #   (그 자리에 `likely-now=` 현재 추정 행동까지 붙었다).
            _absent_pool = [n for n, _d in _reg_all.items()
                            if n not in _scene_set and _npc_mgr.is_npc_active(_d)]
            _hops_map = {}
        else:
            _absent_pool = list(_tiers.get("absent") or [])   # 생존축 필터는 tiers가 이미 건다
            _hops_map = _tiers.get("hops") or {}
        # 정렬 키는 전부 유한·결정적이다(미배치=UNPLACED_HOPS, 미관측=-1, 마지막은 이름) —
        #   dict 반복 순서에 기대는 선택은 버그 후보라는 Pass D-2 교훈.
        _far = int(getattr(_npc_mgr, "UNPLACED_HOPS", 999))

        def _absent_sort_key(_n):
            try:
                _h = int(_hops_map.get(_n, _far))
            except (TypeError, ValueError):
                _h = _far
            try:
                _l = _ls_lookup(_n)
                _l = int(_l) if _l is not None else -1   # 미관측 = 가장 오래된 쪽으로
            except (TypeError, ValueError):
                _l = -1
            return (_h, _l, str(_n))

        _absent_names = sorted(_absent_pool, key=_absent_sort_key)[:6]
        if _absent_names:
            # [2026-07-14 고아 재배선] NPC 시간 힌트(P1 관찰/P2 스케줄, 무근거 폴백 없음)
            # — ctx.rule_txt 고아 회로에서 버려지던 것을 offscreen_trace 재료로 이송.
            # 서사 콜이 큐레이션 게이트 = 렌더러 직행 노이즈(random cast pull) 없음.
            _time_hints = {}
            try:
                import npc_manager as _npm
                for _h in (_npm.get_npc_time_progression(channel_id) or []):
                    if ":" in _h:
                        _hn, _hv = _h.split(":", 1)
                        _time_hints[_hn.strip()] = _hv.strip()
            except Exception:
                _time_hints = {}

            # [2026-07-18 고아 승격] world_tree last-known 위치 — 부재 NPC가 '마지막으로
            # 목격된 장소' 앵커 (get_npc_location 소비 0이던 것). Contract-First 오프스크린 바닥.
            _wt_loc = {}
            try:
                import world_tree as _wt
                for _an in _absent_names:
                    _lloc = _wt.get_npc_location(channel_id, _an)
                    if _lloc:
                        _wt_loc[_an] = _lloc
            except Exception:
                _wt_loc = {}

            _cand_lines = []
            for _an in _absent_names:
                _aa = _att_map.get(_an, {}) if isinstance(_att_map, dict) else {}
                _ls = _ls_lookup(_an)
                _absent_str = (f", absent_for={max(0, _cur_t - int(_ls))} turns"
                               if _ls is not None else ", absent_for=unknown")
                _th = _time_hints.get(_an) or _time_hints.get(_an.split("(")[0].strip())
                _th_str = f", likely-now={_th}" if _th else ""
                _loc_str = f", last_seen_at={_wt_loc[_an]}" if _an in _wt_loc else ""
                _cand_lines.append(
                    f"{_an}: attitude={_aa.get('attitude', 'neutral')}, depth={int(_aa.get('depth', 0) or 0)}{_absent_str}{_loc_str}{_th_str}"
                )
            anchors["offscreen_candidates"] = _cand_lines
    except Exception as _e_osc:
        logger.debug(f"[OffscreenMotion] candidates skip: {_e_osc}")

    # [Phase 0 2026-07-02] 최근 제안 비트 목록 → 반복 회피 재료 (suggested_beats가 매 턴 같은 축 반복 방지)
    try:
        import narrative_queries as _nq_rb
        _rb = _nq_rb.recent_beats(channel_id, n=10, cap=8)
        if _rb:
            anchors["recent_beats_avoid"] = _rb
    except Exception as _e_rb:
        logger.debug(f"[Phase0] recent_beats skip: {_e_rb}")

    # [2026-06-11 소비자 감사 #6 부활] P6 capability hints — Flash *입력*으로 (원설계 배달).
    # 등록 NPC 전용 (PC는 npcs 레지스트리에 없음 — NPC 제한, PC 자율성 무관).
    try:
        from waterfall_pipeline import _inject_capability_hints
        _cap = _inject_capability_hints(domain_manager.get_npcs(channel_id) or {})
        if _cap:
            anchors["capability_hints"] = _cap
    except Exception as _e_cap:
        logger.debug(f"[P6] capability hints skip: {_e_cap}")

    # Session Memory (World State Updater 피드백용)
    # [2026-09-14 W5] 이번 턴 입력 원문 — theoria 4f(T1)가 세력 이름 부분일치에 쓴다.
    #   플래그 OFF면 키 자체가 없다(앵커 dict 모양 종전 동일).
    try:
        import config as _cfg_w5
        if getattr(_cfg_w5, "WIKI_PLACES", False):
            anchors["action_text"] = str(user_input or "")
    except Exception:
        pass
    anchors["session_memory"] = domain_manager.get_session_ai_memory(channel_id)

    # [2026-09-13 S2 T1] 재등장 인물 원문 근거 — Theoria 전용(분석 입). 예외 전부 삼킴.
    try:
        _recall = _build_recall_evidence(channel_id, user_input, history_text, anchors)
        if _recall:
            anchors["recall_evidence"] = _recall
    except Exception as _e_recall:
        logger.debug(f"[Evidence] T1 recall skip: {_e_recall}")

    # [2026-10-10 기록 정정] Theoria·Mira 가 읽는 옛 기록(최근 원문·회상 원행·세션 메모리)에 걸린 정정 한 블록(R4·R5).
    #   걸린 게 없으면 키를 안 넣는다(프롬프트 바이트 그대로).
    try:
        import corrections as _corr_an
        _sm_an = anchors.get("session_memory") or {}
        _corr_txt = "\n".join([
            history_text or "", user_input or "",
            json.dumps(anchors.get("recall_evidence") or {}, ensure_ascii=False),
            json.dumps({k: _sm_an.get(k) for k in ("npc_summaries", "world_changes", "residual_effects")},
                       ensure_ascii=False)])
        _corr_blk_an = _corr_an.block_for(channel_id, _corr_txt, english_head=True)
        if _corr_blk_an:
            anchors["corrections_block"] = _corr_blk_an
    except Exception as _e_corr_an:
        logger.debug(f"[정정] anchors skip: {_e_corr_an}")

    # [2026-07-15 수리] theoria가 읽는데 아무도 안 쓰던 앵커 2종 (dead_scan B: anchors READ-ONLY).
    # 07-04 entity_state / 07-15 arc 와 같은 병 — .get(k, default)라 조용히 default를 먹었다.
    #
    #  ① scene_type — theoria L76이 `anchors.get("scene_type", "normal")`로 읽어
    #     `_build_system_instruction`의 조건부 게이트에 쓴다:
    #         if scene_context.get("scene_type") == "intimate":
    #             rule_tables += SEXUAL_PSYCHOLOGY_ANALYSIS
    #     키가 없어 항상 "normal" → **SEXUAL_PSYCHOLOGY_ANALYSIS가 한 번도 로딩된 적 없음.**
    #     추출 콜은 이번 턴 SceneType을 아직 모른다(그걸 생산하는 게 이 콜) → 직전 프레임의
    #     dai_snapshot에서 가져온다. 장면은 이어지므로 1턴 지연은 감수(렌더러 쪽과 동일 성질).
    #
    #  ② turn_index — theoria L80이 `len(anchors.get("history", []))`로 turn_count를 세어
    #     `get_session_spotlight(seed, turn_number, 5, ...)`에 넘긴다. 그 안에서
    #         group_idx = turn_number % total_groups
    #     즉 turn_number가 로테이션 축인데 항상 0 → group_idx 항상 0 → **매 턴 pool[0:5]
    #     동일**. "This turn, actively apply these highlighted theories"라 써놓고 채널당
    #     같은 5개 이론이 영구 고정이었다. history 길이 대신 실 카운터(turn_index)를 준다.
    try:
        _prev_frame = domain_manager.get_latest_frame(channel_id) or {}
        _prev_dai = _prev_frame.get("dai_snapshot") or {}
        anchors["scene_type"] = str(_prev_dai.get("scene_type", "normal") or "normal")
    except Exception as _e_st:
        logger.debug("[UNE] anchors.scene_type skip: %s", _e_st)
        anchors["scene_type"] = "normal"
    try:
        anchors["turn_index"] = int(world.get("turn_index", 0) or 0)
    except (TypeError, ValueError):
        anchors["turn_index"] = 0

    #  ③ active_genres — 같은 병 6번째 (dead_scan v2.3의 narrative_anchors 별칭이 드러냄).
    #     doom_module L185가 `(context.narrative_anchors or {}).get("active_genres", [])`로
    #     읽어 C-Lens/B-Flavor를 뽑는다:
    #         lens_tags   = _extract_lens_tags(...)    → noir/comedy/romance/drama
    #         flavor_tags = _extract_flavor_tags(...)  → urban_fantasy/steampunk/...
    #         _get_doom_phase L109: primary = lens_tags[0] if lens_tags else "default"
    #     키가 없어 항상 [] → **primary 영구 "default"** → doom_chapter_volume_plan의
    #     lens×flavor×phase atmosphere 매트릭스가 통째로 미가동(起承轉結 경계가 늘 기본값).
    #     생산자·소비자 모양은 이미 일치했다(command_handler L423-432가 layers.narrative_tone에
    #     lens를, layers.style_tech에 flavor를 저장 / _extract_*가 {layers} 형식 판독).
    #     **배달만 없었다.**
    try:
        anchors["active_genres"] = domain_manager.get_active_genres(channel_id)
    except Exception as _e_ag:
        logger.debug("[UNE] anchors.active_genres skip: %s", _e_ag)
        anchors["active_genres"] = []

    # Bus initialization
    bus = SharedBus()
    import config as _cfg_dv
    bus.doom["value"] = world.get("doom", _cfg_dv.DEFAULT_WORLD_STATE.get("doom", 0))   # [C90] 옛 40 → 기본값(0)
    bus.doom["carry"] = world.get("doom_carry", 0.0)  # 소수 이월 누적 (round 0증발 방지)
    # [2026-09-24 감사] 챕터 상태 3키 적재 — bus 는 매 턴 신조인데 이 셋은 영속되지 않아
    #   間 페이즈가 다음 턴에 안 이어졌다 → doom ≥ climax 임계면 매 턴 _trigger_climax 재발화
    #   (전 시계 강제 완성 + storyteller 큐에 climax 삽입), 間 자연 감쇠·챕터 리셋(起)은 사문.
    bus.doom["intermission_active"] = bool(world.get("doom_intermission_active", False))
    bus.doom["climax_triggered"] = bool(world.get("doom_climax_triggered", False))
    bus.doom["climax_armed"] = bool(world.get("doom_climax_armed", False))
    # 0626: pending_doom_gain(퀘스트 완성 등 외부 raw 적립) → bus.doom delta 합류 → doom_module이 수식(phase×lens)+carry 통과. 소비 후 클리어.
    _pending_dg = world.get("pending_doom_gain", 0)
    if _pending_dg:
        bus.doom["delta"] = bus.doom.get("delta", 0) + _pending_dg
        world["pending_doom_gain"] = 0
        domain_manager.update_world_state(channel_id, world)
    # [Reader-GM Stage 3-A] 수신형 시계 후보 적립분 → bus 폴백 인테이크
    # (doom_module이 Flash 제안 부재 시 소비 — 기존 캡·중복·間 규칙 그대로 통과. 소비 후 클리어.)
    _pending_ck = world.get("pending_clock_new")
    if _pending_ck:
        bus.doom["pending_clock_new"] = _pending_ck
        world["pending_clock_new"] = None
        domain_manager.update_world_state(channel_id, world)
    # Doom clocks (local threats)
    clocks = world.get("doom_clocks", [])
    bus.doom["clocks"] = clocks if isinstance(clocks, list) else []

    # [2026-08-11 로드아웃 삭제] loadout 자동 지급 + anchors["pending_flashback"] 급식 제거.
    # (전자는 아무도 안 읽는 dict를 매 세션 심고 있었고, 후자의 생산자 !회상 명령이 폐기됨.)

    # Vigor/Composure migration: old "mental" → vigor + composure
    if "mental" in mem and "vigor" not in mem:
        old_val = mem["mental"].get("value", 100)
        old_delta = mem["mental"].get("last_delta", 0)
        mem["vigor"] = {"value": old_val, "last_delta": old_delta}
        mem["composure"] = {"value": old_val, "last_delta": 0}
        del mem["mental"]

    # [2026-08-18 Phase 2.5] 활력의 정본은 **레지스트리**(custom_var_values["활력"][user_id]).
    #   bus.vigor 는 이번 턴 읽기 사본이다 — 하류(판정 폴백·notation·cascade·스냅샷·로그)가
    #   전부 bus 를 읽으므로 여기 한 곳만 갈아끼우면 배선이 통째로 옮겨간다.
    #   레지스트리가 답을 못 주면(기능 off) 옛 자리로 폴백 — 이월 승계와 같은 계단.
    vigor_data = mem.get("vigor", {"value": 100, "last_delta": 0})
    _vigor_val = vigor_data.get("value", 100)
    try:
        import custom_vars as _cv_bus
        _reg_v = _cv_bus.get_system_value(channel_id, "활력", user_id)
        if _reg_v is not None:
            _vigor_val = _reg_v
    except Exception as _e_cvb:
        logger.debug(f"[CustomVar] 활력 레지스트리 로드 skip: {_e_cvb}")
    bus.vigor["value"] = _vigor_val
    bus.vigor["last_delta"] = vigor_data.get("last_delta", 0)
    # [2026-09-06 P8b] 평형도 레지스트리 소유 — 활력과 완전 대칭. bus.composure 는 읽기 사본.
    composure_data = mem.get("composure", {"value": 100, "last_delta": 0})
    _composure_val = composure_data.get("value", 100)
    try:
        import custom_vars as _cv_bus2
        _reg_c = _cv_bus2.get_system_value(channel_id, "평형", user_id)
        if _reg_c is not None:
            _composure_val = _reg_c
    except Exception as _e_cvc:
        logger.debug(f"[CustomVar] 평형 레지스트리 로드 skip: {_e_cvc}")
    bus.composure["value"] = _composure_val
    bus.composure["last_delta"] = composure_data.get("last_delta", 0)
    # stage3_turns(붕괴 dwell) 로드 제거 — 트라우마 각성 폐지 (2026-07-06)

    # 활력/평형 채널 토글 (off면 prime/process가 스킵 → 수치 동결)
    _vc_on = domain_manager.is_vigor_composure_active(channel_id)
    bus.vigor["module_active"] = _vc_on
    bus.composure["module_active"] = _vc_on

    context = GameContext(
        request=RequestData(
            user_input=user_input,
            genres=genres,
            active_modules=active_modules,
            lore_summary=lore_summary,
            history_text=history_text,
            lore_text=lore_text,
            lore_chunks=lore_chunks,
            lore_chunks_ranked=lore_chunks_ranked or []
        ),
        narrative_anchors=anchors,
        shared_bus=bus
    )

    return context

def sync_from_game_context(channel_id: str, user_id: str, ctx: Any) -> None:
    """[UNE Bridge] GameContext -> ParticipantData/WorldState Sync"""
    import config
    from orchestration_context import GameContext
    if isinstance(ctx, dict):
        ctx = GameContext.from_dict(ctx)
    bus = ctx.shared_bus

    # 1. World State Sync (Doom)
    if bus.doom.get("active") or isinstance(bus.doom.get("clocks"), list):
        world = domain_manager.get_world_state(channel_id)
        world["doom"] = bus.doom["value"]
        world["doom_carry"] = bus.doom.get("carry", 0.0)  # 소수 이월 영속
        world["doom_intermission_active"] = bool(bus.doom.get("intermission_active", False))  # [2026-09-24 감사]
        world["doom_climax_triggered"] = bool(bus.doom.get("climax_triggered", False))
        world["doom_climax_armed"] = bool(bus.doom.get("climax_armed", False))
        if isinstance(bus.doom.get("clocks"), list):
            # [2026-10-24 C74 판2 §2-⑭] 끝남은 이긴다 — 배경(목표 done → resolve_clocks_by_goal)이 그 사이 세계에
            #   적은 시계 끝을 이 턴 통째 저장이 덮지 않게. 같은 시계(이름 + 만든 턴)가 세계 쪽에서 끝났고 이 턴 판엔
            #   아직 서 있으면, 끝 칸을 판으로 옮긴 뒤 쓴다(판의 같은 dict 를 고치므로 이 턴 뒤 읽는 자리도 본다).
            _ended = {(c.get("name"), c.get("turn_created")): c for c in (world.get("doom_clocks") or [])
                      if isinstance(c, dict) and c.get("resolved")}
            if _ended:
                _end_keys = ("resolved", "end", "end_turn", "end_quest", "linked_goal", "advanced_goal")   # [C89 검토 #3]
                for _c in bus.doom.get("clocks") or []:
                    if not isinstance(_c, dict):
                        continue
                    _w = _ended.get((_c.get("name"), _c.get("turn_created")))
                    if not _w:
                        continue
                    if _c.get("resolved"):
                        # [2026-10-28 C89 검토 #3] 판 사본이 이 턴에 흐려짐 · 클라이맥스로 끝났어도, 세계 쪽이 그보다 먼저
                        #   (배경 — 렌더 뒤 끝 · 목표 done) 끝냈으면 먼저 끝난 쪽이 이긴다(averted 가 faded 로 덮이던 자리).
                        try:
                            _first = int(_w.get("end_turn") or 0) < int(_c.get("end_turn") or 0) \
                                and _c.get("end") in ("faded", "climax")
                        except (TypeError, ValueError):
                            _first = False
                        if not _first:
                            continue
                    for _k in _end_keys:
                        if _k in _w:
                            _c[_k] = _w[_k]
                    _c.pop("pending_completion", None)
                    _c.pop("pending_turn", None)
            world["doom_clocks"] = bus.doom.get("clocks", [])
        domain_manager.update_world_state(channel_id, world)

    # 2. Participant Data Sync (Vigor, Composure)
    p_data = domain_manager.get_participant_data(channel_id, user_id)
    if p_data:
        mem = p_data.setdefault("ai_memory", {})

        # Remove legacy "mental" key if present
        mem.pop("mental", None)

        # [2026-09-06 P8b] **두 축 다 여기서 영속하지 않는다.** 활력에 이어 평형의 저장처도
        #   레지스트리로 옮겨갔고, 쓰기는 그쪽 관문(apply_deltas / apply_system_delta)이 전담한다 —
        #   두 자리에 같은 수를 적으면 어느 쪽이 정본인지 아무도 모르게 된다.
        #   ai_memory["vigor"]·["composure"] 는 손대지 않고 그대로 둔다: 이월 승계의 소스다.

        # [2026-07-06 status begins/ends 재배선 — 레티어스 승인: 플레이어 표시 X, 산문 힌트만]
        # 스냅샷 diff: 직전 턴 status 이름 목록 vs 현재 → 신규=begins, 소실=ends.
        # 소스 불문(추출 add/remove·turns 만료·수동 편집) 전부 커버 — 전용 이벤트 배선 불필요.
        # sync가 Events 레이어(_extract)보다 먼저라 같은 턴 프롬프트에 실린다.
        try:
            _cur_names = [
                str(s.get("name", "")).strip()
                for s in (p_data.get("status_effects") or [])
                if isinstance(s, dict) and str(s.get("name", "")).strip()
            ]
            _prev_names = mem.get("_status_names_prev")
            if _prev_names is None:
                pass  # 콜드 스타트: 기존 status를 begins로 오인하지 않게 스냅샷만 초기화
            else:
                _new = [n for n in _cur_names if n not in _prev_names]
                _gone = [n for n in _prev_names if n not in _cur_names]
                if _new and isinstance(bus.dai, dict):
                    bus.dai.setdefault("new_status_effects", []).extend(_new)
                if _gone and isinstance(bus.dai, dict):
                    bus.dai.setdefault("expired_status_effects", []).extend(_gone)
            mem["_status_names_prev"] = _cur_names
        except Exception as _e_st:
            logger.warning("[StatusTransition] diff skipped: %s", _e_st)

        # [2026-10-15 C40 C4] 기세(judgment_momentum) 운반 삭제 — 판정 수치는 시계만. 옛 값은 치운다.
        mem.pop("judgment_momentum", None)

        domain_manager.save_participant_data(channel_id, user_id, p_data)

class UniversalNarrativeEngine:
    def __init__(self, client, model_id: str):
        self.pipeline = WaterfallPipeline(client, model_id)

    async def run(self, channel_id: str, user_id: str, user_input: str, lore_chunks_ranked: list = None) -> Dict[str, Any]:
        """단일 PC 행동 처리 (솔로/자동 모드용)"""
        turn_index = game_world.increment_turn_index(channel_id)
        game_character.process_status_expiry(channel_id, user_id, turn_index)
        # [2026-07-02 감사 노트] expiry 반환값(만료 이름 리스트)을 bus.dai["expired_status_effects"]에
        # 실으면 Events 레이어 "ends" 알림이 산문에 뜬다(현재 기아 소비자). 트라우마 각성 존폐 결정
        # 대기로 배선 보류 — 원하면 여기 1줄.
        context = convert_to_game_context(channel_id, user_id, user_input, lore_chunks_ranked=lore_chunks_ranked)
        p_data = domain_manager.get_participant_data(channel_id, user_id)
        mask = p_data.get("mask") if p_data else "PC"

        updated_context = await self.pipeline.execute(context)
        sync_from_game_context(channel_id, user_id, updated_context)

        result = self._extract_pc_result_v3(updated_context, mask)
        return {
            "game_context": updated_context,
            "directive": result["directive"],
            "system_message": result["system_msg"],
            "judgment_embeds": result.get("judgment_embeds", []),   # [2026-10-15 C42]
        }

    async def run_batch(self, channel_id: str, pending_actions: Dict[str, Dict]) -> Dict[str, Any]:
        """다인 동시 행동 처리. pending_actions = {uid: {"mask":str, "actions":[str]}}"""
        all_results = []
        anomaly_data = None
        last_context = None
        _goal_rc_all = []   # [C74 판3 검토 #3] PC 마다 doom 이 낸 목표 영수증 — 하류(after_goal)는 마지막 판만 본다
        _motion_all: List[str] = []   # [C74 판4] PC 마다 이번 턴 doom 줄(Slot 15 "This turn") — 프롬프트는 마지막 판 버스를 읽는다
        turn_index = game_world.increment_turn_index(channel_id)

        for uid, info in pending_actions.items():
            game_character.process_status_expiry(channel_id, uid, turn_index)
            combined_input = "\n".join(info["actions"])
            context = convert_to_game_context(channel_id, uid, combined_input)

            # 스토리텔러 중복 방지: 이미 발동했으면 동일 이벤트 데이터 복사
            if anomaly_data:
                context.shared_bus.anomaly["_skip_storyteller"] = True
                context.shared_bus.anomaly.update(anomaly_data)

            updated = await self.pipeline.execute(context)

            # 이변 정보 보존 (첫 발동분)
            if updated.shared_bus.anomaly.get("triggered") and not anomaly_data:
                anomaly_data = {
                    "tag": updated.shared_bus.anomaly.get("tag"),
                    "intensity": updated.shared_bus.anomaly.get("intensity"),
                    "polarity": updated.shared_bus.anomaly.get("polarity"),
                    "category": updated.shared_bus.anomaly.get("category"),
                    "due_title": updated.shared_bus.anomaly.get("due_title"),   # [C74 판1 검토 #5] 둘째 PC 부터도 "[기한] 제목"
                    "potential": True,
                }

            sync_from_game_context(channel_id, uid, updated)
            all_results.append(self._extract_pc_result_v3(updated, info["mask"]))
            _goal_rc_all.extend(updated.shared_bus.doom.get("goal_receipts") or [])
            for _mt in (updated.shared_bus.doom.get("motion_turn") or []):   # [C74 판4] PC 마다 이번 턴 줄
                if _mt not in _motion_all:
                    _motion_all.append(_mt)
            last_context = updated

        if last_context is not None and _goal_rc_all:
            last_context.shared_bus.doom["goal_receipts"] = _goal_rc_all
        if last_context is not None and isinstance(last_context.shared_bus.doom, dict):
            last_context.shared_bus.doom["motion_turn"] = _motion_all
        return self._combine_batch_results_v3(all_results, last_context)

    async def run_observation(self, channel_id: str) -> Dict[str, Any]:
        """관찰 모드: PC 행동 없이 세계 묘사"""
        turn_index = game_world.increment_turn_index(channel_id)
        participants = domain_manager.get_domain(channel_id).get("participants", {})
        base_uid = None
        for uid, p in participants.items():
            if p.get("status") == "active":
                base_uid = uid
                break

        if not base_uid:
            return {"game_context": None, "directive": "", "system_message": ""}

        for uid, pdata in participants.items():
            if pdata.get("status") == "active":
                game_character.process_status_expiry(channel_id, uid, turn_index)

        observation_input = "[observation mode — watching the surroundings without taking direct action]"
        context = convert_to_game_context(channel_id, base_uid, observation_input)

        # 판정 비활성화 (관찰은 행동이 아님)
        context.shared_bus.judgment["active"] = False

        updated = await self.pipeline.execute(context)
        sync_from_game_context(channel_id, base_uid, updated)

        result = self._extract_pc_result_v3(updated, "")
        return {
            "game_context": updated,
            "directive": "[observation mode] the world and its NPCs go about their natural activity; the PC takes no action.\n" + result["directive"],
            "system_message": result["system_msg"]
        }

    # [2026-10-10 던지는판 B1] _evaluate_npc_autonomy 은퇴 → npc_autonomous.evaluate_for_turn(waterfall, 서사 콜 앞).
    #   렌더 지시층("[NPC Autonomous Behavior]")을 더 만들지 않는다 — 결과는 Mira 추출 다이제스트의 pull로 간다.

    def _extract_pc_result_v3(self, context, mask: str) -> Dict[str, Any]:
        """Build directive-layer v3: World -> Events -> Judgment -> Atmosphere."""
        bus = context.shared_bus
        layers: List[str] = []

        world = _build_world_layer(bus)
        if world:
            layers.append(world)

        events = _build_events_layer(context, bus)
        if events:
            layers.append(events)
        # [2026-10-25 C74 판4] 이번 턴 doom 줄 → Slot 15 "This turn"(묶음 `!턴` 은 run_batch 가 PC 마다 모은다)
        try:
            import in_motion as _im
            if isinstance(bus.doom, dict):
                bus.doom["motion_turn"] = _im.turn_items(bus)
        except Exception:
            pass

        effective_mask = mask or context.get_acting_mask()
        judgment = _build_judgment_layer(bus, effective_mask)
        if judgment:
            layers.append(judgment)

        atmosphere = _build_atmosphere_layer(context, bus)
        if atmosphere:
            layers.append(atmosphere)

        # Aspects (시스템 교차 결합 신호) — 라벨 내부 식별자, 산문 typological.
        aspects_layer = _build_aspects_layer(context, bus)
        if aspects_layer:
            layers.append(aspects_layer)

        # [2026-10-10 던지는판 B3] NPC 자율 렌더 지시층 은퇴 — '무엇'의 주인은 Mira(응답·hook). 자율은 Mira 재료(pull)로 간다.

        return {
            "directive": "\n\n".join(part for part in layers if part).strip(),
            "system_msg": _build_system_message(bus),
            # [2026-10-15 C42] 판정 임베드 데이터(한 줄은 system_msg 에, 이유·형세·주사위·수정치·결과 영향은 여기).
            "judgment_embeds": ([bus.judgment["embed"]]
                                if isinstance(bus.judgment, dict) and bus.judgment.get("active")
                                and isinstance(bus.judgment.get("embed"), dict) else []),
            "has_anomaly": bool(bus.anomaly and bus.anomaly.get("triggered")),
            "anomaly_header": "",
            # [2026-08-11 비일상적응도 삭제] adaptation_line 키 제거 — 항상 "" + 소비자 0
            "mental_log": bus.vigor.get("log", "") if bus.vigor else "",
        }

    def _combine_batch_results_v3(self, results: list, last_context) -> Dict[str, Any]:
        """Merge multi-PC directives and system logs for batch processing."""
        all_directives: List[str] = []
        system_chunks: List[str] = []
        seen_sys = set()
        embeds: List[Dict[str, Any]] = []   # [2026-10-15 C42] PC별 판정 임베드(한 메시지 최대 10)

        for result in results:
            directive = (result.get("directive") or "").strip()
            if directive:
                all_directives.append(directive)

            sys_msg = (result.get("system_msg") or "").strip()
            if sys_msg and sys_msg not in seen_sys:
                seen_sys.add(sys_msg)
                system_chunks.append(sys_msg)
            embeds.extend(e for e in (result.get("judgment_embeds") or []) if isinstance(e, dict))

        return {
            "game_context": last_context,
            "directive": "\n\n".join(all_directives).strip(),
            "system_message": "\n\n".join(system_chunks).strip(),
            "judgment_embeds": embeds[:10],
        }

