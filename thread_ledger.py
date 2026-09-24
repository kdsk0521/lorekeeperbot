# -*- coding: utf-8 -*-
"""스레드 장부 — 약속·사안의 수명주기(이벤트 소싱) + 기한(남은 시간 → 거친 자연어).

스펙: 파티쳇수정/state_v10/thread_ledger_spec_v0.1_2026-09-25.md
  - 정본 = memory.db `thread_log` 행(이벤트). 현재 상태는 fold 로 만든다 → !다시 워터마크 트림 = 상태 복원.
  - LLM(배경 배치 world_state 섹션)은 **전이만** 낸다. 상태·기한 절대 분·남은 시간 문구는 코드 소유.
  - 기한: 모델은 time_flow target 모양의 표현만, 코드가 절대 분 창(start, end, prec)으로 푼다.
    돌려줄 땐 분 숫자 없이 거친 말(오늘 저녁 / 내일 / 사흘 뒤 / 지금이 그때 / 때가 지났다 — 결과는 아직).
  - 렌더(Slot 29)엔 라벨만, 인용은 안 싣는다. 리더는 두 번째 증인(log-only), 장부를 보지 않는다.
새 LLM 콜 0 · 새 명령 0.
"""
import json
import logging
from difflib import SequenceMatcher
from typing import Any, Dict, List, Optional, Tuple

import config

logger = logging.getLogger(__name__)

OPS = ("open", "progress", "close", "pause", "resume", "reschedule")
KINDS = ("promise", "matter")
OUTCOMES = ("done", "dropped", "broken")
# op → 허용되는 현재 상태 (open 은 신규 발급이라 표 밖)
_ALLOWED = {
    "progress": ("open",),
    "close": ("open", "paused"),
    "pause": ("open",),
    "resume": ("paused", "closed"),
    "reschedule": ("open", "paused"),
}
_KIND_KO = {"promise": "약속", "matter": "사안"}
_OUTCOME_KO = {"done": "완료", "dropped": "취소", "broken": "어김"}
_EVENTS_MAX = 8          # 한 턴에 받는 이벤트 상한(폭주 방어)
_PARTIES_MAX = 6
_REMAINING_MAX = 80


def _cfg(name: str, default):
    return getattr(config, name, default)


def _norm(s: Any) -> str:
    """인용 대조 정규화 — 정본은 fermentation._norm_quote(NFKC + 공백 접기) 하나."""
    from fermentation import _norm_quote
    return _norm_quote(s if isinstance(s, str) else str(s or ""))


def _clip(s: Any, n: int) -> str:
    s = s.strip() if isinstance(s, str) else ""
    return s[:n]


def _parties(v: Any) -> List[str]:
    out: List[str] = []
    if isinstance(v, list):
        for x in v:
            if isinstance(x, str) and x.strip() and x.strip() not in out:
                out.append(x.strip()[:30])
            if len(out) >= _PARTIES_MAX:
                break
    return out


# =========================================================
# fold — 행 → 현재 상태 (순수)
# =========================================================

def _apply_row(states: Dict[str, Dict[str, Any]], r: Dict[str, Any]) -> None:
    tid, op = r.get("thread_id"), r.get("op")
    if not tid or op not in OPS:
        return
    parties = r.get("parties")
    if isinstance(parties, str):
        try:
            parties = json.loads(parties or "[]")
        except Exception:
            parties = []
    parties = _parties(parties)
    q = r.get("quote") or ""
    if op == "open":
        states[tid] = {
            "id": tid, "kind": r.get("kind") if r.get("kind") in KINDS else "matter",
            "title": r.get("title") or "", "parties": parties,
            "status": "open", "outcome": "", "remaining": "",
            "due_start": r.get("due_start"), "due_end": r.get("due_end"), "due_prec": r.get("due_prec") or "",
            "opened_turn": int(r.get("turn") or 0), "last_turn": int(r.get("turn") or 0),
            "last_op": "open", "quotes": [q] if q else [],
            "_row_ids": [r.get("id")] if r.get("id") is not None else [],
        }
        return
    st = states.get(tid)
    if st is None:
        return
    st["last_turn"] = int(r.get("turn") or st.get("last_turn") or 0)
    st["last_op"] = op
    if r.get("title"):
        st["title"] = r["title"]
    if parties:
        st["parties"] = parties
    if op == "progress":
        st["remaining"] = r.get("remaining") or ""
    elif op == "close":
        st["status"] = "closed"
        st["outcome"] = r.get("outcome") if r.get("outcome") in OUTCOMES else "done"
    elif op == "pause":
        st["status"] = "paused"
    elif op == "resume":
        st["status"] = "open"
        st["outcome"] = ""
    elif op == "reschedule":
        st["due_start"], st["due_end"], st["due_prec"] = r.get("due_start"), r.get("due_end"), r.get("due_prec") or ""
    if q:
        st["quotes"] = (st.get("quotes") or []) + [q]
        st["quotes"] = st["quotes"][-4:]


def fold(rows: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    """thread_log 행(id 오름차순) → {thread_id: state}. 순수 함수."""
    states: Dict[str, Dict[str, Any]] = {}
    for r in rows or []:
        if isinstance(r, dict):
            _apply_row(states, r)
    return states


def _thread_num(tid: str) -> int:
    try:
        return int(str(tid)[1:])
    except (TypeError, ValueError):
        return 0


def next_thread_id(states: Dict[str, Any]) -> str:
    return f"T{max([_thread_num(t) for t in states] + [0]) + 1}"


# =========================================================
# 시계 — 절대 분 (캘린더 규칙은 fermentation._gt_abs_minutes 한 벌)
# =========================================================

def _world_view(world: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    w = dict(world or {})
    try:
        import game_world
        game_world._init_clock(w)   # year/month/hour/minute 초기화(사본 위)
    except Exception:
        pass
    return w


def _abs_minutes(year, month, day, hour, minute) -> Optional[int]:
    from fermentation import _gt_abs_minutes
    return _gt_abs_minutes({"year": year, "month": month, "day": day, "hour": hour, "minute": minute})


def now_abs(world: Optional[Dict[str, Any]]) -> Optional[int]:
    w = _world_view(world)
    return _abs_minutes(w.get("year", 1), w.get("month", 1), w.get("day", 1), w.get("hour", 12), w.get("minute", 0))


def resolve_due(due: Any, world: Optional[Dict[str, Any]]) -> Optional[Tuple[int, int, str]]:
    """기한 표현(time_flow target 모양) → 절대 분 창 (start, end, prec). 풀 수 없으면 None. 순수(시계 인자).

    prec: minute(시각 지정, 창=THREAD_MINUTE_GRACE) | slot(시간대, 창=그 슬롯) | day(날, 창=하루).
    오늘(+절대 날짜 없음) 시각·슬롯이 이미 지났으면 내일로 넘긴다("저녁에 보자"를 저녁 뒤에 말하면 내일 저녁).
    절대 날짜가 과거면 다음 해(연도 명시면 안 넘김)."""
    if not isinstance(due, dict):
        return None
    try:
        import game_system
        tg = game_system.normalize_time_target(due)
    except Exception:
        tg = None
    if not isinstance(tg, dict):
        return None
    w = _world_view(world)
    now = now_abs(w)
    if now is None:
        return None
    cur_day = now // 1440
    has_abs = any(tg.get(k) is not None for k in ("year", "month", "day_in_month"))
    if has_abs:
        d0 = _abs_minutes(tg.get("year") or w.get("year", 1), tg.get("month") or w.get("month", 1),
                          tg.get("day_in_month") or w.get("day", 1), 0, 0)
        if d0 is None:
            return None
        day_idx = d0 // 1440
        if day_idx < cur_day and tg.get("year") is None:
            day_idx += int(_cfg("CALENDAR_MONTHS_PER_YEAR", 12)) * int(_cfg("CALENDAR_DAYS_PER_MONTH", 30))
    else:
        day_idx = cur_day + int(tg.get("day_offset") or 0)
    slot = tg.get("slot") if tg.get("slot") in (_cfg("TIME_SLOT_HOURS", {}) or {}) else None
    hour = tg.get("hour")
    minute = tg.get("minute") or 0
    base = day_idx * 1440
    if hour is not None:
        prec = "minute"
        start = base + int(hour) * 60 + int(minute)
        end = start + int(_cfg("THREAD_MINUTE_GRACE", 60))
    elif slot:
        prec = "slot"
        sh, eh = config.TIME_SLOT_HOURS[slot]
        start = base + sh * 60
        end = base + (eh + 1) * 60 if eh >= sh else base + 1440 + (eh + 1) * 60
    else:
        prec = "day"
        start, end = base, base + 1440
    if not has_abs and int(tg.get("day_offset") or 0) == 0 and prec in ("minute", "slot") and end <= now:
        start, end = start + 1440, end + 1440
    return int(start), int(end), prec


def _slot_for_hour(h: int) -> str:
    try:
        import game_world
        return game_world._slot_for_hour(int(h))
    except Exception:
        return ""


def remaining_phrase(st: Dict[str, Any], now: Optional[int]) -> str:
    """남은 시간 → 거친 한국어. 분 숫자는 내보내지 않는다. 기한 없음 = ''."""
    s, e = st.get("due_start"), st.get("due_end")
    if s is None or e is None or now is None:
        return ""
    s, e, prec = int(s), int(e), st.get("due_prec") or "day"
    if now >= e:
        return "때가 지났다 — 결과는 아직"
    if s <= now:
        return "오늘 중" if prec == "day" else "지금이 그때"
    if s - now <= int(_cfg("THREAD_SOON_MIN", 60)):
        return "곧"
    dd = s // 1440 - now // 1440
    hour = (s % 1440) // 60
    slot = _slot_for_hour(hour)
    if prec == "day":
        tail = ""
    elif prec == "slot":
        tail = f" {slot}" if slot else ""
    else:
        h12 = hour % 12 or 12
        tail = f" {slot} {h12}시쯤" if slot else f" {h12}시쯤"
    if dd <= 0:
        return f"오늘{tail}"
    if dd == 1:
        return f"내일{tail}"
    if dd == 2:
        return f"모레{tail}"
    if dd <= 6:
        return f"{dd}일 뒤{tail}"
    return f"약 {max(1, round(dd / 7))}주 뒤"


# =========================================================
# 저장소 접근
# =========================================================

def load_states(channel_id: str) -> Dict[str, Dict[str, Any]]:
    if not channel_id:
        return {}
    try:
        import sqlite_store
        return fold(sqlite_store.read_thread_log(channel_id))
    except Exception as e:
        logger.debug(f"[Thread] load skip: {e}")
        return {}


def _world_of(channel_id: str, world: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    if world is not None:
        return world
    try:
        import domain_manager
        return domain_manager.get_world_state(channel_id) or {}
    except Exception:
        return {}


# =========================================================
# 쓰기 관문
# =========================================================

def apply_events(channel_id: str, turn: int, events: Any, source_text: str,
                 world: Optional[Dict[str, Any]] = None) -> Dict[str, int]:
    """배치 world_state `threads` → 관문 → thread_log 행. 영수증 dict 반환.

    관문(이벤트마다, 앞 이벤트 결과가 뒤 이벤트의 상태): 모양 → 인용 접지(_norm 부분 문자열) → id → 전이 표.
    open 인데 id 가 기존 것이면: closed→resume, 그 외→no-op(dup). id 없는 open 인데 같은 title 이 열림/멈춤이면 dup."""
    rc = {k: 0 for k in ("open", "progress", "close", "pause", "resume", "reschedule",
                         "rej_shape", "rej_quote", "rej_id", "rej_state", "dup")}
    if not channel_id or not isinstance(events, list) or not events or not _cfg("THREAD_LEDGER", True):
        return rc
    src = _norm(source_text)
    w = _world_of(channel_id, world)
    states = load_states(channel_id)
    title_max = int(_cfg("THREAD_TITLE_MAX", 40))
    rows: List[Dict[str, Any]] = []
    for ev in events[:_EVENTS_MAX]:
        if not isinstance(ev, dict):
            rc["rej_shape"] += 1
            continue
        op = str(ev.get("op") or "").strip().lower()
        if op not in OPS:
            rc["rej_shape"] += 1
            continue
        quote = ev.get("quote") if isinstance(ev.get("quote"), str) else ""
        q = _norm(quote)
        if not q or q not in src:
            rc["rej_quote"] += 1
            continue
        tid = ev.get("id")
        tid = str(tid).strip().upper() if isinstance(tid, (str, int)) and str(tid).strip() else None
        title = _clip(ev.get("title"), title_max)
        row = {"turn": int(turn or 0), "quote": quote.strip()[:300], "title": "", "parties": "[]",
               "kind": "", "outcome": "", "remaining": "", "due_start": None, "due_end": None, "due_prec": ""}
        if op == "open":
            if tid and tid in states:
                if states[tid]["status"] == "closed":
                    op = "resume"
                else:
                    rc["dup"] += 1
                    continue
            else:
                if not title:
                    rc["rej_shape"] += 1
                    continue
                nt = _norm(title)
                if any(s.get("status") in ("open", "paused") and _norm(s.get("title")) == nt
                       for s in states.values()):
                    rc["dup"] += 1
                    continue
                tid = next_thread_id(states)
                row.update({"thread_id": tid, "op": "open", "title": title,
                            "kind": ev.get("kind") if ev.get("kind") in KINDS else "matter",
                            "parties": json.dumps(_parties(ev.get("parties")), ensure_ascii=False)})
                _d = resolve_due(ev.get("due"), w) if ev.get("due") else None
                if _d:
                    row["due_start"], row["due_end"], row["due_prec"] = _d
                rows.append(row)
                _apply_row(states, row)
                rc["open"] += 1
                continue
        if not tid or tid not in states:
            rc["rej_id"] += 1
            continue
        if states[tid]["status"] not in _ALLOWED[op]:
            rc["rej_state"] += 1
            continue
        row.update({"thread_id": tid, "op": op, "title": title,
                    "parties": json.dumps(_parties(ev.get("parties")), ensure_ascii=False)})
        if op == "close":
            row["outcome"] = ev.get("outcome") if ev.get("outcome") in OUTCOMES else "done"
        elif op == "progress":
            row["remaining"] = _clip(ev.get("remaining"), _REMAINING_MAX)
        elif op == "reschedule":
            _d = resolve_due(ev.get("due"), w) if ev.get("due") else None
            if _d:
                row["due_start"], row["due_end"], row["due_prec"] = _d
        rows.append(row)
        _apply_row(states, row)
        rc[op] += 1
    if rows:
        try:
            import sqlite_store
            sqlite_store.append_thread_events(channel_id, rows)
        except Exception as e:
            logger.warning(f"[Thread] write fail (무시): {e}")
    if any(rc.values()):
        logger.info("[Thread] turn=%s open=%d progress=%d close=%d pause=%d resume=%d resched=%d | "
                    "rej_shape=%d rej_quote=%d rej_id=%d rej_state=%d dup=%d",
                    turn, rc["open"], rc["progress"], rc["close"], rc["pause"], rc["resume"], rc["reschedule"],
                    rc["rej_shape"], rc["rej_quote"], rc["rej_id"], rc["rej_state"], rc["dup"])
    return rc


# =========================================================
# 읽기 — 렌더 / 분석 / 추출 입력 / 루카
# =========================================================

def _party_text(st: Dict[str, Any], sep: str) -> str:
    return sep.join((st.get("parties") or [])[:3])


def _render_order(states: List[Dict[str, Any]]):
    def key(st):
        has_due = st.get("due_start") is not None
        return (0 if has_due else 1, st.get("due_start") or 0,
                0 if st.get("kind") == "promise" else 1, -(st.get("last_turn") or 0))
    return sorted(states, key=key)


RENDER_HEADER = ("[GROUND_TRUTH] What stands open between people in this world, from the record. "
                 "A passed hour settles nothing by itself; the page decides how it went.")


def build_render_block(channel_id: str, world: Optional[Dict[str, Any]] = None,
                       states: Optional[Dict[str, Dict[str, Any]]] = None) -> str:
    """Slot 29 블록. 열린 스레드만, 라벨·남은 시간만(인용 0). 0개면 ''."""
    if not channel_id or not _cfg("THREAD_LEDGER", True):
        return ""
    states = load_states(channel_id) if states is None else states
    now = now_abs(_world_of(channel_id, world))
    hide = int(_cfg("THREAD_OVERDUE_HIDE_DAYS", 7)) * 1440
    items = []
    for st in states.values():
        if st.get("status") != "open":
            continue
        if st.get("due_end") is not None and now is not None and now - int(st["due_end"]) > hide:
            continue
        items.append(st)
    items = _render_order(items)[: int(_cfg("THREAD_RENDER_MAX", 6))]
    if not items:
        return ""
    lines = ["<Standing_Threads>", RENDER_HEADER]
    for st in items:
        label = _KIND_KO.get(st.get("kind"), "사안")
        pt = _party_text(st, " ↔ ")
        line = f"- {label}" + (f" · {pt}" if pt else "") + f": {st.get('title', '')}"
        ph = remaining_phrase(st, now)
        if ph:
            line += f" — {ph}"
        lines.append(line)
    lines.append("</Standing_Threads>")
    return "\n".join(lines)


def analysis_line(channel_id: str, world: Optional[Dict[str, Any]] = None, limit: int = 8) -> str:
    """Theoria 4c 한 줄. 열림·멈춤. 없으면 ''."""
    if not channel_id or not _cfg("THREAD_LEDGER", True):
        return ""
    states = load_states(channel_id)
    now = now_abs(_world_of(channel_id, world))
    items = [s for s in states.values() if s.get("status") in ("open", "paused")]
    items = _render_order(items)[:limit]
    if not items:
        return ""
    parts = []
    for st in items:
        bits = [st.get("kind", "matter")]
        pt = _party_text(st, "·")
        if pt:
            bits.append(pt)
        ph = remaining_phrase(st, now)
        if ph:
            bits.append(f"due {ph}")
        if st.get("status") == "paused":
            bits.append("paused")
        if st.get("remaining"):
            bits.append(f"left: {st['remaining']}")
        parts.append(f"{st.get('title', '')}({', '.join(bits)})")
    return "- Standing threads: " + "; ".join(parts)


def extraction_ledger_line(channel_id: str, world: Optional[Dict[str, Any]] = None) -> str:
    """배치 world_state 입력 줄. 열림·멈춤, 최근 활동순 THREAD_EXTRACT_MAX 개. 비면 '(empty)'."""
    if not channel_id:
        return ""
    states = load_states(channel_id)
    now = now_abs(_world_of(channel_id, world))
    items = sorted((s for s in states.values() if s.get("status") in ("open", "paused")),
                   key=lambda s: -(s.get("last_turn") or 0))[: int(_cfg("THREAD_EXTRACT_MAX", 15))]
    if not items:
        return "Ledger: (empty)"
    parts = []
    for st in items:
        pt = _party_text(st, "·")
        seg = f"{st['id']} {st.get('kind', 'matter')}" + (f" {pt}" if pt else "") + f" 「{st.get('title', '')}」"
        tail = [st.get("status", "open")]
        ph = remaining_phrase(st, now)
        if ph:
            tail.append(f"due {ph}")
        if st.get("remaining"):
            tail.append(f"left {st['remaining']}")
        parts.append(f"{seg} ({', '.join(tail)})")
    return "Ledger: " + " | ".join(parts)


def luka_lines(channel_id: str, world: Optional[Dict[str, Any]] = None) -> Tuple[str, str]:
    """루카 OOC (진행 중, 해결된) 두 줄 내용. 없으면 ''."""
    if not channel_id or not _cfg("THREAD_LEDGER", True):
        return "", ""
    states = load_states(channel_id)
    now = now_abs(_world_of(channel_id, world))
    act = _render_order([s for s in states.values() if s.get("status") == "open"])[:5]
    active = ", ".join(s.get("title", "") + (f"({remaining_phrase(s, now)})" if remaining_phrase(s, now) else "")
                       for s in act)
    done = sorted((s for s in states.values() if s.get("status") == "closed"),
                  key=lambda s: -(s.get("last_turn") or 0))[:3]
    resolved = ", ".join(f"{s.get('title', '')}({_OUTCOME_KO.get(s.get('outcome'), '완료')})" for s in done)
    return active, resolved


# =========================================================
# 리더 두 번째 증인 (log-only)
# =========================================================

def _overlap(a: str, b: str) -> bool:
    na, nb = _norm(a), _norm(b)
    if not na or not nb:
        return False
    if na in nb or nb in na:
        return True
    m = SequenceMatcher(None, na, nb, autojunk=False).find_longest_match(0, len(na), 0, len(nb))
    return m.size >= int(_cfg("THREAD_WITNESS_LCS", 12))


def witness_counts(live_quotes: List[str], open_quotes: List[str], closed_quotes: List[str]) -> Dict[str, int]:
    """순수 셈. agree=열린 스레드와 겹친 리더 live / early_close=이번 창에 닫힌 스레드와 겹친 live / reader_only=어디에도 안 겹침."""
    agree = early = only = 0
    for lq in live_quotes:
        hit_open = any(_overlap(lq, q) for q in open_quotes)
        hit_closed = any(_overlap(lq, q) for q in closed_quotes)
        agree += 1 if hit_open else 0
        early += 1 if hit_closed else 0
        only += 1 if not (hit_open or hit_closed) else 0
    return {"agree": agree, "early_close": early, "reader_only": only}


def witness(channel_id: str, turn: int, digest: Dict[str, Any]) -> Optional[Dict[str, int]]:
    """리더 digest ↔ 장부. 로그만 남긴다(처방 0). 리더 턴과 추출 턴은 같은 카운터지만 ±1 창으로 본다."""
    if not channel_id or not isinstance(digest, dict) or not _cfg("THREAD_LEDGER", True):
        return None
    lives = [it.get("quote") for it in (digest.get("live_threads") or [])
             if isinstance(it, dict) and isinstance(it.get("quote"), str) and it.get("quote").strip()]
    if not lives:
        return None
    try:
        import sqlite_store
        rows = sqlite_store.read_thread_log(channel_id)
    except Exception:
        return None
    states = fold(rows)
    lo = int(turn or 0) - 1
    closed_now = {r.get("thread_id") for r in rows if r.get("op") == "close" and int(r.get("turn") or 0) >= lo}
    open_q = [q for s in states.values() if s.get("status") == "open" for q in (s.get("quotes") or [])]
    closed_q = [q for tid in closed_now for q in ((states.get(tid) or {}).get("quotes") or [])]
    c = witness_counts(lives, open_q, closed_q)
    logger.info("[ThreadWitness] turn=%s agree=%d early_close=%d reader_only=%d open=%d",
                turn, c["agree"], c["early_close"], c["reader_only"],
                sum(1 for s in states.values() if s.get("status") == "open"))
    return c
