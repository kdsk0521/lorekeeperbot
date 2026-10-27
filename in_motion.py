"""in_motion.py — 진행 중 (2026-10-25 C74 판4 · W2-4 · W3 · C75 G4)

진행 중인 줄 한 재료: 아크(이름 얻은 우물) → 그쪽으로 휘는 목표 → 그 목표의 시계 · 곧게 가는 목표 · 혼자 선 시계 ·
약속 · 사안 · 최근 끝난 것 · 줄거리 · 서 있는 상황. `collect` 한 번을 둘이 읽는다:
  - 렌더 Slot 15 `<In_Motion>` — `render_block`(+ 이번 턴 줄 `turn_items`)
  - Activity 앱 "진행 중" — `activity_parts`(보기만)

옛 자리(Slot 11 목표 · 줄거리 · Slot 29 시계 줄 · `<Standing_Threads>` · Slot 30 시계 끝남 · 목표 깎임/진전 · 임박 시계 ·
진전 없는 목표 약화 줄)는 여기로 모였다. 스펙 `파티쳇수정/output_artifact/C74_판4_진행중블록·화면·렌더실측_지도·스펙_2026-10-25.md`.

목표는 아크에 안 붙는다(C75 §11) — 그릴 때 `well_store.goal_bend` 로 잰 결과다. 콜 0, 쓰기 0.
"""
import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

HEADER = ("[GROUND_TRUTH] What is in motion in this world, from the record. "
          "The figures are the record's tally; prose never states them. "
          "A passed hour settles nothing by itself; the page decides how it went.")   # [검토 S2] 옛 Standing_Threads 머리 끝 절
_KIND_KO = {"threat": "위협", "timer": "타이머", "opportunity": "기회"}


def _cfg(name: str, default: Any) -> Any:
    try:
        import config
        return getattr(config, name, default)
    except Exception:
        return default


def _int(v: Any, d: int = 0) -> int:
    try:
        return int(v)
    except (TypeError, ValueError):
        return d


# =========================================================
# 1. 재료
# =========================================================

def clock_kind(clock: Dict[str, Any]) -> str:
    """시계 셋(theoria `doom_on_complete` 극성): 완성하면 doom↑ = threat · 0 = timer · ↓ = opportunity."""
    try:
        import doom_module
        v = doom_module._get_clock_doom(clock)
    except Exception:
        v = 1
    return "threat" if v > 0 else ("opportunity" if v < 0 else "timer")


def _clock_item(c: Dict[str, Any]) -> Dict[str, Any]:
    seg = max(1, _int(c.get("segments"), 4) or 4)
    filled = _int(c.get("filled", c.get("progress", 0)))
    return {"name": str(c.get("name") or "?").strip(), "filled": filled, "segments": seg,
            "kind": clock_kind(c), "imminent": seg - filled <= 1,
            "tick": str(c.get("tick_mode") or "").lower() in ("time", "hybrid")}   # [검토 L3] 옛 Slot 29 ⏱ 표 — 세계 시간으로 차는 시계


def _goal_item(st: Dict[str, Any], states: Dict[str, Any], now: Optional[int], turn: int) -> Dict[str, Any]:
    import thread_ledger
    stale = int(_cfg("IN_MOTION_STALE_TURNS", 8))
    return {"id": st.get("id", ""), "title": st.get("title", ""), "line": thread_ledger.goal_line(st, states, now),
            "progress": _int(st.get("progress")), "max": _int(st.get("max")), "rank": st.get("rank", "normal"),
            "due": thread_ledger.remaining_phrase(st, now),
            "stale": turn > 0 and turn - _int(st.get("last_step_turn")) >= stale, "clocks": []}


def collect(channel_id: str, pc_location: Any = None, world: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """진행 중 재료(dict). 비면 모든 칸이 빈 목록 · 빈 글. 덩이마다 따로 지킨다 — 한 덩이가 깨져도 나머지는 선다(검토 L1)."""
    out: Dict[str, Any] = {"arcs": [], "straight": [], "lone_clocks": [], "threads": [], "closed": [],
                           "storylines": "", "conditions": []}
    if not channel_id:
        return out
    try:
        import domain_manager, thread_ledger, well_store
    except Exception as e:
        logger.warning(f"[InMotion] import skip: {e}")
        return out
    if world is None:
        try:
            world = domain_manager.get_world_state(channel_id) or {}
        except Exception as e:
            logger.warning(f"[InMotion] world skip: {e}")
            world = {}
    turn = _int(world.get("turn_index"))
    try:
        states = thread_ledger.load_states(channel_id) if _cfg("THREAD_LEDGER", True) else {}
        now = thread_ledger.now_abs(world)
    except Exception as e:
        logger.warning(f"[InMotion] ledger skip: {e}")
        states, now = {}, None

    # 아크 — 이름 얻은 산 우물(결전 포함) 중 렌더 쪽에서 보이는 것(Slot 30 아크 지시와 같은 문턱 — 검토 S1), 가까운 순
    raw: Dict[int, Dict[str, Any]] = {}
    aa: List[Dict[str, Any]] = []
    try:
        raw = well_store.load(channel_id)
        aa = [a for a in well_store.view(channel_id, None, pc_location, turn, wells=raw)
              if a["well_status"] in well_store._ARCLIKE and well_store.in_sight(a)
              and str(a.get("declared_goal") or a.get("where") or "").strip()]
    except Exception as e:
        logger.warning(f"[InMotion] wells skip: {e}")
    aa.sort(key=lambda a: (not a.get("climax"), -float(a.get("proximity") or 0), -int(a.get("mass") or 0), a["id"]))
    aa = aa[: int(_cfg("IN_MOTION_ARCS_MAX", 5))]
    try:
        nodes = well_store._nodes(channel_id) if aa else {}
    except Exception:
        nodes = {}
    folds = sorted((raw[a["id"]] for a in aa if a["id"] in raw), key=lambda w: (w.get("born_turn", 0), w["id"]))
    arcs: Dict[int, Dict[str, Any]] = {}
    for a in aa:
        ph = [p for p in (a.get("phases") or []) if str(p or "").strip()]
        arcs[a["id"]] = {"id": a["id"], "label": str(a.get("declared_goal") or a.get("where") or "").strip(),
                         "phase": ph[-1] if ph else "", "climax": bool(a.get("climax")), "goals": [], "clocks": []}

    # 목표 → 휘는 아크(그 자리에서 잼) 또는 곧게
    goals: Dict[str, Dict[str, Any]] = {}
    straight: List[Dict[str, Any]] = []
    try:
        for st in thread_ledger.open_goals(states):
            g = _goal_item(st, states, now, turn)
            goals[g["id"]] = g
            hit = None
            if folds:
                try:
                    hit = well_store.goal_bend(channel_id, st, folds, nodes, pc_location)
                except Exception:
                    hit = None
            if hit is not None and hit[0]["id"] in arcs:
                arcs[hit[0]["id"]]["goals"].append(g)
            else:
                straight.append(g)
    except Exception as e:
        logger.warning(f"[InMotion] goals skip: {e}")

    # 시계 → 산 연결의 목표 · 아니면 그 시계를 쥔 아크 · 아니면 혼자
    lone: List[Dict[str, Any]] = []
    try:
        clocks = [c for c in (world.get("doom_clocks") or []) if isinstance(c, dict) and not c.get("resolved")
                  and str(c.get("name") or "").strip()]
        keymap: Dict[str, int] = {}
        for w in folds:
            for i in w.get("ids") or []:
                if i.get("kind") == "clock":
                    keymap[str(i.get("key") or "")] = w["id"]
        first_t = well_store._first_by_name(world.get("doom_clocks") or [])
        rows = None
        if any(str(c.get("linked_goal") or "") in goals for c in clocks):
            try:
                import sqlite_store
                rows = sqlite_store.read_thread_log(channel_id)   # [검토 L2] 연결마다 다시 읽지 않게 한 번
            except Exception:
                rows = None
        for c in clocks:
            item = _clock_item(c)
            gid = str(c.get("linked_goal") or "")
            if gid and gid in goals:
                try:
                    import doom_module
                    live = doom_module._goal_link_live(channel_id, c, gid, states.get(gid), rows)
                except Exception:
                    live = True
                if live:
                    goals[gid]["clocks"].append(item)
                    continue
            nm, t0 = str(c["name"]), _int(c.get("turn_created"))
            wid = keymap.get(f"C:{nm}#{t0}")
            if wid is None and first_t.get(nm) == t0:
                wid = keymap.get(f"C:{nm}")
            if wid is not None and wid in arcs:
                arcs[wid]["clocks"].append(item)
            else:
                lone.append(item)
    except Exception as e:
        logger.warning(f"[InMotion] clocks skip: {e}")

    out["arcs"] = [arcs[a["id"]] for a in aa]
    out["straight"] = straight
    out["lone_clocks"] = lone
    try:
        out["threads"] = [{"line": thread_ledger.thread_line(st, states, now), "kind": st.get("kind"),
                           "parties": list(st.get("parties") or []), "title": st.get("title", ""),
                           "due": thread_ledger.remaining_phrase(st, now)}
                          for st in thread_ledger.open_threads(states, now)]
    except Exception as e:
        logger.warning(f"[InMotion] threads skip: {e}")
    try:
        out["closed"] = [{"line": thread_ledger.closed_line(st), "kind": st.get("kind"), "title": st.get("title", ""),
                          "outcome": st.get("outcome") or ""}
                         for st in thread_ledger.recent_closed(states, world, channel_id, goals=True)]
    except Exception as e:
        logger.warning(f"[InMotion] closed skip: {e}")
    try:
        import narrative_tracker
        txt = narrative_tracker.format_storylines_for_prompt(
            domain_manager.get_narrative_tracker_state(channel_id) or {}, turn) or ""
        out["storylines"] = "\n".join(ln for ln in txt.split("\n") if ln.strip() and ln.strip() != "[Active Storylines]")
    except Exception as e:
        logger.warning(f"[InMotion] storylines skip: {e}")
    try:
        conds = (domain_manager.get_storyteller_state(channel_id) or {}).get("active_conditions") or []
        out["conditions"] = [str(c.get("description") or "").strip() for c in conds
                             if isinstance(c, dict) and str(c.get("description") or "").strip()]
    except Exception as e:
        logger.warning(f"[InMotion] conditions skip: {e}")
    return out


def turn_items(bus: Any) -> List[str]:
    """이번 턴 doom 버스 → 이번 턴 줄(옛 Slot 30 이벤트 층 문장 그대로): 시계 끝남(위협 발동) · 목표 깎임 · 목표 진전."""
    doom = getattr(bus, "doom", None) if not isinstance(bus, dict) else bus
    doom = doom if isinstance(doom, dict) else {}
    out: List[str] = []
    for clock in doom.get("completed_this_turn") or []:
        if isinstance(clock, dict):
            out.append(f"{clock.get('name', '?')} has become real — {clock.get('threat', '')}. "
                       "the changed world surfaces; the POV stays with the PC.")
    for sb in doom.get("goal_setback") or []:
        if isinstance(sb, dict):
            out.append(f"{sb.get('reason', '')} — {sb.get('goal', '?')} has suffered a setback")
    for ga in doom.get("goal_advance") or []:
        if isinstance(ga, dict):
            out.append(f"{ga.get('reason', '')} — {ga.get('goal', '?')} moves forward")
    return out


# =========================================================
# 2. 렌더 블록 (Slot 15)
# =========================================================

def _clock_bits(c: Dict[str, Any]) -> str:
    return f"{c['name']} {c['filled']}/{c['segments']}" + (" ⏱" if c.get("tick") else "") + f" · {c['kind']}" + (
        " · about to become real" if c.get("imminent") else "")


def _clock_text(c: Dict[str, Any]) -> str:
    return "- Clock: " + _clock_bits(c)


def _goal_text(g: Dict[str, Any]) -> str:
    """목표 줄 — 그 목표의 시계는 같은 줄 꼬리(`· its clock: …`). 렌더 전송 정화(cognition.sanitize_minor_tokens,
    MINOR_SANITIZE_RENDER)가 겹 공백을 하나로 접어 들여쓰기 계층이 사라진다(판4 실측) — 계층은 줄 머리 `- ` 와 꼬리로만 싣는다."""
    line = f"- Goal: {g['line']}" + (" · waits on the player's lead" if g.get("stale") else "")
    cs = g.get("clocks") or []
    if cs:
        line += (" · its clock: " if len(cs) == 1 else " · its clocks: ") + "; ".join(_clock_bits(c) for c in cs)
    return line


def render_block(data: Dict[str, Any], this_turn: Optional[List[str]] = None) -> str:
    """`<In_Motion>` 글. 덩이 머리(Arc: · Goals going straight · …)는 줄 머리, 그 아래 항목은 `- `. 비는 덩이는 줄째 안 쓴다 ·
    통째로 비면 ''."""
    body: List[str] = []
    for a in data.get("arcs") or []:
        tag = "coming to a head" if a.get("climax") else a.get("phase", "")
        body.append(f"Arc: {a.get('label') or '?'}" + (f" · {tag}" if tag else ""))
        body += [_goal_text(g) for g in a.get("goals") or []]
        body += [_clock_text(c) for c in a.get("clocks") or []]
    if data.get("straight"):
        body.append("Goals going straight")
        body += [_goal_text(g) for g in data["straight"]]
    if data.get("lone_clocks"):
        body.append("Clocks on their own")
        body += [_clock_text(c) for c in data["lone_clocks"]]
    if data.get("threads"):
        body.append("Promises · matters")
        body += ["- " + t["line"] for t in data["threads"]]
    if data.get("closed"):
        body.append("Closed lately")
        body += ["- " + c["line"] for c in data["closed"]]
    if data.get("storylines"):
        body.append("Storylines")
        body.append(data["storylines"])
    if this_turn:
        body.append("This turn")
        body += ["- " + s for s in this_turn]
    if not body:
        return ""
    return "\n".join(["<In_Motion>", HEADER] + body + ["</In_Motion>"])


def build_for_prompt(channel_id: str, bus: Any = None, pc_location: Any = None) -> str:
    """slot_manager 가 부른다 — 재료 + 이번 턴 줄(버스 doom `motion_turn`, 묶음 `!턴` 은 PC 마다 모은 것)."""
    try:
        doom = getattr(bus, "doom", None) if bus is not None else None
        items = list((doom or {}).get("motion_turn") or []) if isinstance(doom, dict) else []
        return render_block(collect(channel_id, pc_location), items)
    except Exception as e:
        logger.debug(f"[InMotion] block skip: {e}")
        return ""


# =========================================================
# 3. 화면 (Activity "진행 중", 부품 쪽 — 보기만)
# =========================================================

def _bar(n: int, m: int) -> str:
    n, m = max(0, int(n)), max(0, int(m))
    return "■" * min(n, m) + "□" * max(0, m - n) + f" {n}/{m}"


def _goal_rows(g: Dict[str, Any]) -> List[List[str]]:
    val = _bar(g["progress"], g["max"]) + (f" · 기한 {g['due']}" if g.get("due") else "") + (
        " · 멈춤 가까움" if g.get("stale") else "")
    rows = [[f"목표 · {g['title']}", val]]
    rows += _clock_rows(g.get("clocks") or [])
    return rows


def _clock_rows(cs: List[Dict[str, Any]]) -> List[List[str]]:
    return [[f"시계 · {c['name']}", _bar(c["filled"], c["segments"]) + (" ⏱" if c.get("tick") else "")
             + f" · {_KIND_KO.get(c['kind'], c['kind'])}"] for c in cs]


def activity_parts(data: Dict[str, Any]) -> List[Dict[str, Any]]:
    parts: List[Dict[str, Any]] = []
    for a in data.get("arcs") or []:
        inner: List[Dict[str, Any]] = []
        if a.get("phase"):
            inner.append({"t": "text", "text": a["phase"], "muted": True})
        rws: List[List[str]] = []
        for g in a.get("goals") or []:
            rws += _goal_rows(g)
        rws += _clock_rows(a.get("clocks") or [])
        if rws:
            inner.append({"t": "rows", "rows": rws})
        parts.append({"t": "card", "title": a.get("label") or "?", "icon": "orbit",
                      "chip": "결전" if a.get("climax") else "", "parts": inner})
    if data.get("straight"):
        rws = []
        for g in data["straight"]:
            rws += _goal_rows(g)
        parts.append({"t": "card", "title": "곧게 가는 목표", "icon": "target", "parts": [{"t": "rows", "rows": rws}]})
    if data.get("lone_clocks"):
        parts.append({"t": "card", "title": "혼자 선 시계", "icon": "clock",
                      "parts": [{"t": "rows", "rows": _clock_rows(data["lone_clocks"])}]})
    if data.get("threads"):
        rws = []
        for t in data["threads"]:
            lab = {"promise": "약속", "matter": "사안"}.get(t.get("kind"), "사안")
            who = " ↔ ".join((t.get("parties") or [])[:3])
            rws.append([lab + (f" · {who}" if who else ""), t["title"] + (f" · {t['due']}" if t.get("due") else "")])
        parts.append({"t": "card", "title": "약속 · 사안", "icon": "handshake", "parts": [{"t": "rows", "rows": rws}]})
    if data.get("conditions"):
        parts.append({"t": "card", "title": "서 있는 상황", "icon": "flag",
                      "parts": [{"t": "text", "text": c} for c in data["conditions"]]})
    if data.get("closed"):
        parts.append({"t": "fold", "title": "끝난 것", "parts": [{"t": "text", "text": c["line"]} for c in data["closed"]]})
    if not parts:
        parts.append({"t": "empty", "text": "아직 진행 중인 게 없어요."})
    return parts
