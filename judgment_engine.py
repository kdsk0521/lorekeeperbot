"""
Lorekeeper UNE - Judgment Engine (Call 2)
Handles dice rolls and narrative outcomes based on Call 1 analysis.

[2026-10-15 C42] 4dF 퍼지 다이스. 스펙 파티쳇수정/composition/C42_4dF판정_스펙_2026-10-15.md.
  축 셋 — 개수 N(혼전도) · 유불리(형세) · 수정치 M(무엇을 들고 왔나). d100·DC·항 10개는 퇴역.
  등급 이름 다섯은 그대로라 산문 계약(시드·결과 지시·Mira roll_outcome)과 시계 결과는 무변경.
[2026-11-06 C116 0-3] 바깥 항 문(outer_terms) — 코드 앱 judge 훅이 낸 항을 「앱 이름」 도장으로 내역 끝에 받는다.
  한 항 ±MOD_CAP_UP · 합은 같은 mod_bounds. 항 이름은 임베드에만(Mira · 렌더는 등급만 본다).
  스펙 파티쳇수정/output_artifact/C116_판0-3_판정바깥항_지도·스펙_2026-11-06.md.
[2026-11-06 C118] 밸런스 — 개수 판 기본 6(차분 4 · 혼란 8, 2~8) · 수정치 폭 −min(6, N−1) ~ +6 ·
  극단 = 주사위 몫(주사위만 ±3) · 세계 쪽 항 맞섬 −1(Theoria action_meta.oppose — 조력 +1 의 거울).
  스펙 파티쳇수정/output_artifact/C118_판정밸런스_지도·스펙_2026-11-06.md.
"""

import logging
import random
from typing import Any, Dict, List, Optional, Tuple

from orchestration_context import GameContext

logger = logging.getLogger("Judgment")


# [2026-08-18 Phase 2.5] 레지스트리 조회에 필요한 두 좌표. anchors 가 정본이다
# (channel_id·acting_user_id 는 une_facade.build_game_context 가 심는다).
def _jud_channel(context) -> str:
    return str((getattr(context, "narrative_anchors", None) or {}).get("channel_id", "") or "")


def _jud_actor(context) -> str:
    return str((getattr(context, "narrative_anchors", None) or {}).get("acting_user_id", "") or "")


# [2026-10-15 C40 C4 보완] 판정 감정(결과 → 평형 ±) 삭제. 판정이 움직이는 수치는 시계뿐이다.
# [2026-10-15 C42] 활력 구간표(_MENTAL_BANDS)·각오 선불·상대 psyche 보정·면모 숫자·세계상황 숫자·
#   Theoria modifications 합산·DC·Effect 등급 이동 퇴역. 활력·평형은 스트레스(체력) 칸이라 수정치에
#   안 들어가고, 몸 쪽 −는 타격칸(game_character.strike_view)에서만 나온다.


# =========================================================
# 4dF 기본 부품 — 순수 함수(콜 0·쓰기 0). 스모크가 하나씩 검사한다.
# =========================================================
FACES = (-1, 0, 1)
# 굴림의 자리 하나. 스모크는 이걸 갈아 끼워 결과를 고정한다.
_FACE_RNG = random.Random()

# 개수 기본 = 판정 앞 Theoria EnergyDirection(혼전도). 모르는 값은 6(기본 판).
# [C118] 옛 판 idle 2 · stagnant 3 · aftershock 3 · rising 4 · detonation 6 (2~6, 모름 4) — 주사위가 늘면 같은 ±1 의
#   무게가 조금 준다(표준편차 4개 1.63 · 6개 2.0 · 8개 2.31). 하한 2 는 가드로 남는다(차분 4 − 시간 1 = 3).
ENERGY_BASE = {"idle": 4, "stagnant": 5, "aftershock": 5, "rising": 6, "detonation": 8}
DICE_MIN, DICE_MAX, DICE_DEFAULT = 2, 8, 6
# 평형 "흔들림" 문턱 — 고갈 구간(15~39)부터.
LOW_GAUGE = 40
# 시간을 들이는 행동(개수 −1): 30분 이상. 1틱 = 2분.
TIME_HOURS_MIN = 0.5
TIME_TICKS_MIN = 15

DIFFICULTY_MOD = {"trivial": 1, "easy": 1, "normal": 0, "hard": -1, "extreme": -2}
DIFFICULTY_KR = {"trivial": "쉬움", "easy": "쉬움", "normal": "보통", "hard": "어려움", "extreme": "극한"}
SKILL_MOD = {"practiced": 1, "average": 0, "unpracticed": -1}
SKILL_KR = {"practiced": "숙련", "average": "보통", "unpracticed": "서툼"}
# 렌더 위치 사다리(iceberg._POS_TIERS)와 같은 경계 — 산문의 adverse position 과 판정의 불리가 같은 선.
POS_ADVERSE_MAX = 0.4
POS_EVEN_MAX = 0.6
POS_WORD_KR = {"dire": "절박", "adverse": "불리", "even": "대등", "favorable": "유리", "dominant": "압도"}

RESULT_KR = {
    "critical_success": "✨대성공",
    "success": "✅성공",
    "partial": "🟠부분 성공",
    "failure": "❌실패",
    "critical_failure": "⚠️대실패",
}
RESULT_COLOR = {
    "critical_success": 0xF1C40F,
    "success": 0x2ECC71,
    "partial": 0xE67E22,
    "failure": 0xE74C3C,
    "critical_failure": 0x992D22,
}
EDGE_KR = {1: "유리", 0: "평탄", -1: "불리"}
GLYPH = {1: "[+]", 0: "[ ]", -1: "[−]"}


MOD_CAP_UP = 6   # [2026-11-01 C102] 수정치 상한 — 주사위 개수와 무관(레티어스 "+6까진 풀어도"). 옛 min(3, N).


def mod_bounds(n: int) -> Tuple[int, int]:
    """수정치 폭 — 하한 −min(6, N−1) · 상한 +6(C118 거의 대칭, 옛 하한 −min(3, N−1)). 4개 −3~+6 · 6개 −5~+6 · 8개 −6~+6.
    바닥: 주사위가 전부 +면 성공은 늘 가능하다. 위쪽은 열어 둔다(레티어스 "판정은 유저 친화적이어도
    괜찮다" · "+6까진 풀어도") — 수정치 ≥ N 이면 실패 0, ≥ N+1 이면 굴리기 전에 성공(대성공만 주사위 몫)."""
    n = int(n)
    return -min(6, n - 1), MOD_CAP_UP


def clamp_outer(v: Any) -> int:
    """[2026-11-06 C116 0-3] 바깥 항 하나의 크기. 앱 몫 상한은 따로 두지 않는다(레티어스 11-06 "좀 풀어도 —
    치트성 장비 넣고 싶은 게 사람 마음") — 한 항은 ±MOD_CAP_UP 안, 합은 mod_bounds 가 한 번 더 자른다."""
    return max(-MOD_CAP_UP, min(MOD_CAP_UP, int(v)))


def outer_label(name: str, label: str = "") -> str:
    """바깥 항 표시 라벨 = 출처 도장 「이름」 + 앱이 낸 라벨."""
    name = str(name or "").strip() or "앱"
    label = str(label or "").strip()
    return f"「{name}」 {label}" if label else f"「{name}」"


async def outer_terms(context, bus) -> List[Tuple[str, int]]:
    """[2026-11-06 C116 0-3] 판정 바깥 항 문 — 지금 출처는 코드 앱 judge 훅 하나(app_host.judge_terms).
    j = 행동 종류 · 행동 글 · 난이도 · 실력(Theoria action_meta), who = 행동하는 PC 가면. 모델 콜 0.
    저장 bus.judgment["outer_terms"] = [[앱 id, 라벨, 받은 값, 낸 값]]. 예외 → [] (판정은 계속)."""
    rec: List[List[Any]] = []
    terms: List[Tuple[str, int]] = []
    try:
        meta = bus.judgment.get("meta") or {}
        meta = meta if isinstance(meta, dict) else {}
        j = {"type": str(meta.get("type") or meta.get("action_type") or "").strip().lower(),
             "action": str(meta.get("action") or "")[:200],
             "difficulty": str(meta.get("difficulty") or "").strip().lower(),
             "skill": str(meta.get("skill") or "").strip().lower()}
        import app_host
        rows = await app_host.judge_terms(_jud_channel(context), j, context.get_acting_mask())
        for r in rows or []:
            v = clamp_outer(r["value"])
            terms.append((outer_label(r.get("name"), r.get("label")), v))
            rec.append([r["app"], r.get("label") or "", v, int(r["value"])])
    except Exception as e:
        logger.warning("[Judgment] 바깥 항 건너뜀: %s", e)
        rec, terms = [], []
    bus.judgment["outer_terms"] = rec
    return terms


def tier_of(d: int, m: int) -> str:
    """등급. 극단은 총합 ±3 **그리고** 주사위만으로 ±3 이상일 때만(C118, 옛 주사위 ±2) — 수정치는 성공 · 실패를
    정하고 극단은 혼란(N)이 정한다. 수정치가 기울어도 극단 확률은 그대로다(N 4 6.2% · 6개 10.7% · 8개 14.1%, 2개 0)."""
    t = int(d) + int(m)
    if t >= 3 and d >= 3:
        return "critical_success"
    if t >= 1:
        return "success"
    if t == 0:
        return "partial"
    if t <= -3 and d <= -3:
        return "critical_failure"
    return "failure"


def roll_fudge(n: int, edge: int, rng: Optional[random.Random] = None) -> Tuple[List[int], Optional[int]]:
    """퍼지 주사위 N개. 유리 = N+1개 굴려 제일 낮은 하나 버림, 불리 = 제일 높은 하나 버림.
    Returns: (굴린 면 전부 — 굴린 순서, 버린 자리 index 또는 None)."""
    rng = rng or _FACE_RNG
    k = int(n) + (1 if edge else 0)
    faces = [rng.choice(FACES) for _ in range(k)]
    dropped = None
    if edge > 0:
        lo = min(faces)
        dropped = max(i for i, f in enumerate(faces) if f == lo)
    elif edge < 0:
        hi = max(faces)
        dropped = max(i for i, f in enumerate(faces) if f == hi)
    return faces, dropped


def kept_sum(faces: List[int], dropped: Optional[int]) -> int:
    return sum(f for i, f in enumerate(faces) if i != dropped)


def dice_line(faces: List[int], dropped: Optional[int]) -> str:
    """굴린 순서대로, 버린 하나는 맨 끝에 취소선."""
    kept = [GLYPH.get(f, "[?]") for i, f in enumerate(faces) if i != dropped]
    s = "".join(kept)
    if dropped is not None:
        s += f" ~~{GLYPH.get(faces[dropped], '[?]')}~~"
    return s


def signed(v: int) -> str:
    v = int(v)
    if v > 0:
        return f"+{v}"
    if v < 0:
        return f"−{abs(v)}"
    return "±0"


# =========================================================
# 축 셋 — 재료를 읽어 숫자와 내역(라벨, 값)을 낸다
# =========================================================
def _names(raw: Any) -> List[str]:
    out = []
    for x in (raw if isinstance(raw, list) else []):
        s = str(x or "").strip()
        if s and s not in out:
            out.append(s)
    return out


def _system_value(context, name: str, actor: str) -> Optional[int]:
    """활력·평형 레지스트리 값. 기능이 꺼졌거나 없으면 None(= 문턱 판정에서 '안 걸림')."""
    try:
        import custom_vars as _cv
        v = _cv.get_system_value(_jud_channel(context), name, actor)
        return int(v) if v is not None else None
    except Exception as e:
        logger.debug("[Judgment] %s 레지스트리 조회 skip: %s", name, e)
        return None


def _stage_names(context, bus) -> List[str]:
    """무대 명부 — anchors scene_cast, 비면 Theoria psyche_states 키(둘 다 PC 키는 이미 걸러져 있다)."""
    cast = _names((context.narrative_anchors or {}).get("scene_cast"))
    if cast:
        return cast
    ps = bus.dai.get("psyche_states") if isinstance(bus.dai, dict) else None
    return [str(k).strip() for k in (ps or {}).keys() if str(k).strip()] if isinstance(ps, dict) else []


def _location_conditions(context, bus) -> List[str]:
    """PC 위치에 걸린 세계상황(극성 무관, mixed 포함). 옛 condition_mod 의 위치 필터 그대로."""
    ch = _jud_channel(context)
    if not ch:
        return []
    try:
        import domain_manager as _dm
        st = _dm.get_storyteller_state(ch) or {}
    except Exception as e:
        logger.debug("[Judgment] 세계상황 조회 skip: %s", e)
        return []
    here = (bus.dai.get("current_location", "") if isinstance(bus.dai, dict) else "").strip()
    out = []
    for c in st.get("active_conditions", []) or []:
        if not isinstance(c, dict):
            continue
        loc = str(c.get("location") or "").strip()
        if loc and loc != here:
            continue
        out.append(str(c.get("tag") or c.get("description") or "세계상황").strip()[:24])
    return out


def _takes_time(tf: Any) -> bool:
    if not isinstance(tf, dict):
        return False
    try:
        if float(tf.get("explicit_hours") or 0) >= TIME_HOURS_MIN:
            return True
    except (TypeError, ValueError):
        pass
    if isinstance(tf.get("target"), dict) and tf.get("target"):
        return True
    try:
        return int(tf.get("ticks") or 0) >= TIME_TICKS_MIN
    except (TypeError, ValueError):
        return False


def dice_count(context, bus, assist: Optional[List[str]] = None,
               oppose: Optional[List[str]] = None) -> Tuple[int, List[Tuple[str, int]]]:
    """개수 N = EnergyDirection 기본 + 조정(PC 평형·상대 평형·세계상황 +1, 시간 들임 −1), 2~8(C118).
    상대 = 맞섬(oppose)으로 집힌 무대 위 첫 이름, 없으면 옛 셈(관련 NPC 중 조력 아닌 무대 위 첫 이름)."""
    dai = bus.dai if isinstance(bus.dai, dict) else {}
    energy = str(dai.get("energy_direction") or "").strip().lower()
    base = ENERGY_BASE.get(energy, DICE_DEFAULT)
    parts: List[Tuple[str, int]] = [(f"장면 {energy or '—'}", base)]
    n = base
    c = _system_value(context, "평형", _jud_actor(context))
    if c is not None and c < LOW_GAUGE:
        n += 1
        parts.append(("평형 흔들림", 1))
    helpers = set(assist or [])
    stage = set(_stage_names(context, bus))
    opp = next((x for x in _names(oppose) if x in stage and x not in helpers), "") \
        or next((x for x in _names(dai.get("relevant_npcs")) if x in stage and x not in helpers), "")
    if opp:
        oc = _system_value(context, "평형", opp)
        if oc is not None and oc < LOW_GAUGE:
            n += 1
            parts.append((f"{opp} 흔들림", 1))
    conds = _location_conditions(context, bus)
    if conds:
        n += 1
        parts.append((f"세계상황 {conds[0]}", 1))
    if _takes_time(dai.get("time_flow")):
        n -= 1
        parts.append(("시간을 들임", -1))
    return max(DICE_MIN, min(DICE_MAX, n)), parts


def _aspect_counts(aspects: Any) -> Tuple[int, int]:
    f = a = 0
    for asp in (aspects if isinstance(aspects, list) else []):
        if not isinstance(asp, dict):
            continue
        st = str(asp.get("for_or_against", asp.get("stance", ""))).strip().lower()
        if st in ("for", "support", "positive", "pro"):
            f += 1
        elif st in ("against", "oppose", "negative", "con"):
            a += 1
    return f, a


def edge_of(bus) -> Tuple[int, List[Tuple[str, int]]]:
    """유불리 = Position(렌더 사다리 경계) + Aspects(유리 수 − 불리 수의 부호). 합의 부호 한 칸."""
    dai = bus.dai if isinstance(bus.dai, dict) else {}
    pos_raw = dai.get("position")
    try:
        pos = float(pos_raw.get("value", 0.5)) if isinstance(pos_raw, dict) else 0.5
    except (TypeError, ValueError):
        pos = 0.5
    p = -1 if pos <= POS_ADVERSE_MAX else (1 if pos > POS_EVEN_MAX else 0)
    f, a = _aspect_counts(dai.get("aspects"))
    asp = (f > a) - (f < a)
    parts: List[Tuple[str, int]] = []
    if p:
        try:
            import iceberg as _ib
            word = POS_WORD_KR.get(_ib.position_word(pos), "")
        except Exception:
            word = ""
        parts.append((f"자리 {word or EDGE_KR[p]}", p))
    if asp:
        parts.append((f"주변 {f}:{a}", asp))
    s = p + asp
    return (s > 0) - (s < 0), parts


def _fragment_terms(passives, active_names) -> List[Tuple[str, int]]:
    """조각 = 이름 판단. Theoria 가 집은 이름이 PC 조각 목록에 있으면 강점 +1 / 약점 −1.
    행동 종류 키는 안 본다(이 행동에 걸리는지는 집는 것으로 이미 판단됐다 — 타격칸과 같은 장치)."""
    import config as _cfg_f
    want = set(_names(active_names))
    if not want:
        return []
    out = []
    for p in (passives if isinstance(passives, list) else []):
        if not isinstance(p, dict):
            continue
        nm = str(p.get("name") or "").strip()
        if nm in want and nm not in [x[0] for x in out]:
            out.append((nm, _cfg_f.fragment_edge(p)))
    return out


def _item_names(anchors: Dict[str, Any]) -> List[str]:
    try:
        from game_character import get_inventory_items as _inv
        items = _inv(anchors)
    except Exception:
        items = anchors.get("inventory") or []
    out = []
    for it in (items if isinstance(items, list) else []):
        nm = it.get("name") if isinstance(it, dict) else it
        nm = str(nm or "").strip()
        if nm:
            out.append(nm)
    return out


def _buff_terms(anchors: Dict[str, Any], action_type: str) -> List[Tuple[str, int]]:
    """좋은 상태 하나당 +1 — 옛 status_mod 의 행동 종류 매칭 그대로, buff 만."""
    try:
        from game_character import normalize_status_effects
        effects = normalize_status_effects(anchors.get("status_effects", []))
    except Exception:
        return []
    out = []
    for eff in effects:
        if not isinstance(eff, dict) or str(eff.get("type")) != "buff":
            continue
        mods = eff.get("modifiers") if isinstance(eff.get("modifiers"), dict) else {}
        v = mods.get(f"judgment_{action_type}") if action_type else None
        if v is None:
            v = mods.get("judgment")
        try:
            v = int(v or 0)
        except (TypeError, ValueError):
            v = 0
        if v > 0:
            out.append((str(eff.get("name") or eff.get("tag") or "좋은 상태"), 1))
    return out


def modifier_terms(context, bus, n: int, outer=()) -> Tuple[int, int, List[Tuple[str, int]], Dict[str, List[str]]]:
    """수정치 M. Returns: (M, 원합, 내역, 확인을 통과한 이름 {active_passives, active_items, assist, oppose, active_strikes}).
    outer = 바깥 항 [(표시 라벨, v)] (C116 0-3 — outer_terms 가 만든다). 내역 끝에 붙고 같은 합 · 같은 폭."""
    meta = bus.judgment.get("meta") or {}
    meta = meta if isinstance(meta, dict) else {}
    ev = bus.judgment.get("eval") or {}
    ev = ev if isinstance(ev, dict) else {}
    anchors = context.narrative_anchors or {}
    atype = str(meta.get("type") or meta.get("action_type") or "").strip().lower()
    terms: List[Tuple[str, int]] = []
    used: Dict[str, List[str]] = {"active_passives": [], "active_items": [], "assist": [], "oppose": [],
                                  "active_strikes": []}

    diff = str(meta.get("difficulty") or "normal").strip().lower()
    dm = DIFFICULTY_MOD.get(diff, 0)
    if dm:
        terms.append((f"{DIFFICULTY_KR.get(diff, diff)}", dm))
    sk = str(meta.get("skill") or "average").strip().lower()
    sm = SKILL_MOD.get(sk, 0)
    if sm:
        terms.append((f"실력 {SKILL_KR.get(sk, sk)}", sm))
    for nm, v in _fragment_terms(anchors.get("passives", []), meta.get("active_passives")):
        terms.append((f"조각 「{nm}」", v))
        used["active_passives"].append(nm)
    have = set(_item_names(anchors))
    for nm in _names(meta.get("active_items")):
        if nm in have:
            terms.append((nm, 1))
            used["active_items"].append(nm)
    memo = ev.get("memo_relevant")
    if (isinstance(memo, dict) and str(memo.get("content") or "").strip()) or (isinstance(memo, list) and memo):
        _mc = str(memo.get("content") if isinstance(memo, dict) else memo[0])[:16]
        terms.append((f"단서 「{_mc}」", 1))
    stage = set(_stage_names(context, bus))
    helpers = [x for x in _names(meta.get("assist")) if x in stage]
    if helpers:
        terms.append((f"조력 {'·'.join(helpers[:3])}", 1))
        used["assist"] = helpers
    # [C118] 맞섬 — 조력의 거울. 무대 위 · 조력 아닌 이름만, 몇 명이든 −1.
    foes = [x for x in _names(meta.get("oppose")) if x in stage and x not in helpers]
    if foes:
        terms.append((f"맞섬 {'·'.join(foes[:3])}", -1))
        used["oppose"] = foes
    track = set(_names(anchors.get("strikes")))
    for nm in _names(meta.get("active_strikes")):
        if nm in track:
            terms.append((f"타격 「{nm}」", -1))
            used["active_strikes"].append(nm)
    terms.extend(_buff_terms(anchors, atype))
    terms.extend((str(lab), int(v)) for lab, v in (outer or ()))

    raw = sum(v for _, v in terms)
    lo, hi = mod_bounds(n)
    return max(lo, min(hi, raw)), raw, terms, used


def _parts_text(parts: List[Tuple[str, int]], with_sign: bool = True) -> str:
    return " · ".join(f"{lab} {signed(v)}" if with_sign else lab for lab, v in parts)


class JudgmentEngine:
    def __init__(self, client, model_id: str):
        self.client = client
        self.model_id = model_id

    async def process(self, context: GameContext) -> GameContext:
        bus = context.shared_bus
        if not bus.judgment.get("active"):
            return context

        meta = bus.judgment.get("meta", {}) or {}
        meta = meta if isinstance(meta, dict) else {}
        eval_data = bus.judgment.get("eval", {}) or {}
        eval_data = eval_data if isinstance(eval_data, dict) else {}
        action = str(meta.get("action") or "행동")

        # 1. 축 셋. 조력으로 집힌 이름은 개수의 '상대' 고르기에서 빠진다(무대 위 이름만 남는 건 양쪽 같다).
        #    [C118] 맞섬으로 집힌 이름이 있으면 그 사람이 '상대'다.
        n, n_parts = dice_count(context, bus, _names(meta.get("assist")), _names(meta.get("oppose")))
        outer = await outer_terms(context, bus)   # [C116 0-3] 바깥 항(앱 judge) — 폭은 같은 mod_bounds
        m, m_raw, m_terms, used = modifier_terms(context, bus, n, outer)
        edge, e_parts = edge_of(bus)

        # 2. 굴림
        faces, dropped = roll_fudge(n, edge)
        d = kept_sum(faces, dropped)
        result = tier_of(d, m)

        # 3. 저장
        reason = str(eval_data.get("reason", "") or "").strip()
        bus.judgment.update({
            "n": n, "edge": EDGE_KR[edge], "faces": faces, "dropped": dropped,
            "dice_sum": d, "mod": m, "mod_raw": m_raw, "total": d + m, "result": result,
            "reason": reason,
            "mod_terms": [[lab, v] for lab, v in m_terms],
            "count_terms": [[lab, v] for lab, v in n_parts],
            "edge_terms": [[lab, v] for lab, v in e_parts],
        })
        bus.judgment.update(used)

        # 4. 시계 (C40: 판정이 움직이는 수치는 시계뿐)
        _apply_consequences(context, result)

        # 5. 표시 — 한 줄 + 임베드 데이터(discord 객체는 보내는 쪽이 만든다)
        mask = context.get_acting_mask()
        bus.judgment["mask"] = mask
        bus.judgment["output"] = (
            f"🎲 **{mask}** · {action} · {dice_line(faces, dropped)} {signed(m)} → **{RESULT_KR.get(result, result)}**"
        )
        _m_val = _parts_text(m_terms) or "없음"
        if m_raw != m:
            _m_val += f" (합 {signed(m_raw)} → {signed(m)})"
        fields = [
            ["형세", EDGE_KR[edge] + (f" — {_parts_text(e_parts, with_sign=False)}" if e_parts else "")],
            ["주사위", f"{n}개 — {n_parts[0][0]} {n_parts[0][1]}"
                      + (" · " + _parts_text(n_parts[1:]) if len(n_parts) > 1 else "")],
            ["수정치", f"{signed(m)} — {_m_val}"],
        ]
        _log = str(bus.judgment.get("consequence_log") or "").strip()
        if _log:
            fields.append(["📋 결과 영향", _log])
        bus.judgment["embed"] = {
            "color": RESULT_COLOR.get(result, 0x95A5A6),
            "description": f"*{reason}*" if reason else "",
            "fields": fields,
        }

        logger.info("[Judgment] %s → %s | 4dF n=%d %s faces=%s drop=%s D=%+d M=%+d(raw %+d) T=%+d",
                    action, result, n, EDGE_KR[edge], faces, dropped, d, m, m_raw, d + m)
        return context


# ── Consequence Helpers ──────────────────────────────────────

def _apply_consequences(context, result: str) -> None:
    """판정 결과 → 시계. [C40] 수치는 시계뿐 · [C42] 각오(Absorb) 갈래 삭제."""
    import config as _cfg
    bus = context.shared_bus
    cons = getattr(_cfg, "JUDGMENT_CONSEQUENCES", {}).get(result, {})
    if not cons:
        return

    consequence_log = []
    clock_effect = cons.get("clock_effect", 0)

    # B. Primary Axis — [2026-10-15 C40 C4] 삭제. 활력·평형은 출력물 콜(extract_outputs)이 대가가 그려진 장면을
    #   읽고 움직인다(판정 먼저 → Mira·렌더가 굴림을 보고 장면을 쓴다). 여기서 또 깎으면 이중 차감이었다.

    # C. Clock Effect (DLC 안전: clocks 없으면 스킵)
    if clock_effect != 0:
        clocks = bus.doom.get("clocks", [])
        if isinstance(clocks, list) and clocks:
            clock_all = cons.get("clock_all", False)
            affected = _apply_clock_consequence(clocks, clock_effect, clock_all)
            consequence_log.extend(affected)

    if consequence_log:
        bus.judgment["consequence_log"] = " | ".join(consequence_log)


def _apply_clock_consequence(clocks: list, effect: int, apply_all: bool) -> list:
    """판정 결과 → 시계 변경. effect>0: 전진, effect<0: 후퇴."""
    log = []
    active = [c for c in clocks if isinstance(c, dict) and not c.get("resolved")]
    if not active:
        return log

    if apply_all:
        for clock in active:
            segments = int(clock.get("segments", 4) or 4)
            old = int(clock.get("filled", 0) or 0)
            new = max(0, min(segments, old + effect))
            if new != old:
                clock["filled"] = new
                log.append(f"⏰ {clock.get('name', '?')} {old}→{new}/{segments}")
    else:
        # 가장 위험한(채워진 비율 높은) 시계 선택
        target = max(active, key=lambda c: int(c.get("filled", 0) or 0) / max(int(c.get("segments", 4) or 4), 1))
        segments = int(target.get("segments", 4) or 4)
        old = int(target.get("filled", 0) or 0)
        new = max(0, min(segments, old + effect))
        if new != old:
            target["filled"] = new
            direction = "⬇️" if effect < 0 else "⬆️"
            log.append(f"{direction} {target.get('name', '?')} {old}→{new}/{segments}")
    return log
