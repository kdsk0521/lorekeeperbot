# -*- coding: utf-8 -*-
"""스레드 장부 — 약속·사안의 수명주기(이벤트 소싱) + 기한(남은 시간 → 거친 자연어).

스펙: 파티쳇수정/state_v10/thread_ledger_spec_v0.1_2026-09-25.md
  - 정본 = memory.db `thread_log` 행(이벤트). 현재 상태는 fold 로 만든다 → !다시 워터마크 트림 = 상태 복원.
  - LLM(배경 배치 world_state 섹션)은 **전이만** 낸다. 상태·기한 절대 분·남은 시간 문구는 코드 소유.
  - 기한: 모델은 time_flow target 모양의 표현만, 코드가 절대 분 창(start, end, prec)으로 푼다.
    돌려줄 땐 분 숫자 없이 거친 말(오늘 저녁 / 내일 / 사흘 뒤 / 지금이 그때 / 때가 지났다 — 결과는 아직).
  - 렌더(Slot 29)엔 라벨만, 인용은 안 싣는다. 리더는 두 번째 증인(log-only), 장부를 보지 않는다.
새 LLM 콜 0 · 새 명령 0.

[2026-10-24 C74 판2 합치기] 퀘스트 보드 → 장부 세 번째 종류 **goal(목표)**. 스펙 C74_판2_합치기_문안초안·스펙_2026-10-24.
  - 목표만 rank · progress(step 합, 0~max 가둠) · max(QUEST_RANK_SETTINGS). 추출 step 말(small|big)은 관문이 1 · 2 로.
  - 다 참(progress ≥ max) → 코드가 close done 행. 목표 close done → 보상(pending_doom_gain) · 연결 시계 해결.
  - 선(after): 기다리는 줄은 asleep — 기다리는 쪽이 close **done** 이면 코드가 wake 행. 들어오는 선 하나 · 나가는 선 여럿.
  - 코드가 쓰는 행(다 참 닫기 · 깨우기 · 진전 없음 멈춤 · 시계 깎임 · 間 시드)은 인용 없음, origin 칸에 출처.
  - 끝 훅(_Txn)은 하나 — 추출 · OOC · 코드 길이 다 같은 훅을 지난다.
"""
import json
import logging
from difflib import SequenceMatcher
from typing import Any, Dict, List, Optional, Tuple

import config

logger = logging.getLogger(__name__)

OPS = ("open", "progress", "close", "pause", "resume", "reschedule")
KINDS = ("goal", "promise", "matter")   # [C74 판2] goal = 옛 퀘스트
OUTCOMES = ("done", "dropped", "broken")
# [2026-10-10 이야기 실 O-E] fold 만 아는 op — 배경 추출은 낼 수 없다(OPS 밖). 플레이어 OOC "없던 일"(void).
#   행은 남기고 상태만 void. 읽는 자리는 모두 상태 이름(open·paused·closed)으로 걸러서 손대지 않아도 빠진다.
# [C74 판2] wake(코드: 기다리던 줄이 done 으로 닫힘 → 깨움) · sleep(OOC: 선을 새로 잇기 — 기다리는 줄 id 를 after_id 에).
FOLD_ONLY_OPS = ("void", "wake", "sleep")
# op → 허용되는 현재 상태 (open 은 신규 발급이라 표 밖)
#   [C74 판2] asleep(자는 줄)은 닫을 수만 있다(산문이 그 일을 끝냈으면 끝). 진전 · 멈춤 · 기한은 깬 뒤.
_ALLOWED = {
    "progress": ("open",),
    "close": ("open", "paused", "asleep"),
    "pause": ("open",),
    "resume": ("paused", "closed"),
    "reschedule": ("open", "paused"),
}
LIVE = ("open", "paused", "asleep")   # 장부에 서 있는 것(끝나지 않은 것)
_STEP_WORD = {"small": 1, "big": 2}
_KIND_KO = {"goal": "목표", "promise": "약속", "matter": "사안"}
_OUTCOME_KO = {"done": "완료", "dropped": "취소", "broken": "어김"}
_EVENTS_MAX = 8          # 한 턴에 받는 이벤트 상한(폭주 방어)
CLOCK_MOVE_ORIGINS = ("code:averted", "code:achieved")   # [C74 판3] 시계가 목표를 민 행(같은 턴 추출 진전을 막는다)
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
# 목표 — 등급 표(옛 퀘스트 표 그대로)
# =========================================================

def rank_of(v: Any) -> str:
    """등급 말 → 표의 키. 모르는 말이면 기본(normal)."""
    v = str(v or "").strip().lower()
    table = _cfg("QUEST_RANK_SETTINGS", {}) or {}
    return v if v in table else str(_cfg("QUEST_DEFAULT_RANK", "normal"))


def goal_max(rank: Any) -> int:
    try:
        return int(((_cfg("QUEST_RANK_SETTINGS", {}) or {}).get(rank_of(rank)) or {}).get("max_progress", 6))
    except (TypeError, ValueError):
        return 6


def doom_reward(rank: Any) -> int:
    try:
        return int(((_cfg("QUEST_RANK_SETTINGS", {}) or {}).get(rank_of(rank)) or {}).get("doom_reward", 3))
    except (TypeError, ValueError):
        return 3


_SIGNS = str.maketrans({"\u2212": "-", "\u2013": "-", "\u2014": "-", "\uff0d": "-", "\uff0b": "+"})


def _num(v: Any) -> Optional[int]:
    """숫자 · 숫자 글자("2" · "+2" · "−1" — 유니코드 빼기 · 전각 포함) → 정수. 못 읽으면 None."""
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return int(v)
    if isinstance(v, str):
        s = v.strip().translate(_SIGNS).replace(" ", "")
        try:
            return int(float(s))
        except ValueError:
            return None
    return None


def step_of(v: Any) -> int:
    """추출 step(small|big) → 1|2. 숫자(글자 숫자 포함)가 오면 1~2 로 가둔다(모델은 말을 낸다). 못 읽으면 0."""
    if isinstance(v, str) and v.strip().lower() in _STEP_WORD:
        return _STEP_WORD[v.strip().lower()]
    n = _num(v)
    if n is None:
        return 0
    return 2 if n >= 2 else (1 if n >= 1 else 0)


def _int(v: Any, default: int = 0) -> int:
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


# =========================================================
# fold — 행 → 현재 상태 (순수)
# =========================================================

def _apply_row(states: Dict[str, Dict[str, Any]], r: Dict[str, Any]) -> None:
    tid, op = r.get("thread_id"), r.get("op")
    if not tid or (op not in OPS and op not in FOLD_ONLY_OPS):
        return
    parties = r.get("parties")
    if isinstance(parties, str):
        try:
            parties = json.loads(parties or "[]")
        except Exception:
            parties = []
    parties = _parties(parties)
    q = r.get("quote") or ""
    turn = _int(r.get("turn"))
    if op == "open":
        kind = r.get("kind") if r.get("kind") in KINDS else "matter"
        after = str(r.get("after_id") or "")
        states[tid] = {
            "id": tid, "kind": kind,
            "title": r.get("title") or "", "parties": parties,
            # [C74 판2] 선이 걸린 open = 자는 줄(기다리는 쪽이 done 으로 닫히면 wake 행이 깨운다)
            "status": "asleep" if after else "open", "outcome": "", "remaining": "",
            "due_start": r.get("due_start"), "due_end": r.get("due_end"), "due_prec": r.get("due_prec") or "",
            "opened_turn": turn, "last_turn": turn,
            "last_op": "open", "quotes": [q] if q else [],
            "after": after,
            "_row_ids": [r.get("id")] if r.get("id") is not None else [],
        }
        if kind == "goal":
            rk = rank_of(r.get("rank"))
            states[tid].update({"rank": rk, "progress": 0, "max": goal_max(rk), "last_step_turn": turn})
        return
    st = states.get(tid)
    if st is None:
        return
    st["last_turn"] = turn or int(st.get("last_turn") or 0)
    st["last_op"] = op
    if r.get("title"):
        st["title"] = r["title"]
    if parties:
        st["parties"] = parties
    if op == "progress":
        if st.get("kind") == "goal":
            # [C74 판2] 목표 = step 합(부호 있음 — 시계 깎임은 음수), 0 ~ max 로 가둔다. 진전 턴 = 멈춤 시계의 기준.
            #   진전 턴은 진행도가 실제로 움직였을 때만(0 에서 깎임 · max 에서 더함은 움직임이 아니다 — 검토 #7).
            stp = _int(r.get("step"))
            if stp:
                mx = int(st.get("max") or goal_max(st.get("rank")))
                p0 = int(st.get("progress") or 0)
                st["progress"] = max(0, min(mx, p0 + stp))
                if st["progress"] != p0:
                    st["last_step_turn"] = turn
            if r.get("remaining"):
                st["remaining"] = r.get("remaining")
        else:
            st["remaining"] = r.get("remaining") or ""
    elif op == "close":
        st["status"] = "closed"
        st["outcome"] = r.get("outcome") if r.get("outcome") in OUTCOMES else "done"
        if st.get("kind") == "goal" and st["outcome"] == "done":
            st["paid"] = True   # [C74 판2 검토 #4] 보상은 한 목표에 한 번 — 다시 열려 또 끝나도 두 번 안 붙는다
    elif op == "pause":
        st["status"] = "paused"
    elif op in ("resume", "wake"):
        # [C74 판2] wake = 자던 줄이 깸(선은 기록으로 남는다). 목표는 다시 열린 턴부터 멈춤 시계를 새로 센다.
        st["status"] = "open"
        st["outcome"] = ""
        if st.get("kind") == "goal":
            st["last_step_turn"] = turn
    elif op == "sleep":
        # [C74 판2] OOC 로 선을 새로 이음 — 자는 줄은 기한이 없다(깬 뒤에 정한다).
        st["status"] = "asleep"
        st["after"] = str(r.get("after_id") or "")
        st["due_start"], st["due_end"], st["due_prec"] = None, None, ""
    elif op == "reschedule":
        st["due_start"], st["due_end"], st["due_prec"] = r.get("due_start"), r.get("due_end"), r.get("due_prec") or ""
    elif op == "void":
        st["status"] = "void"
        st["outcome"] = ""
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
# [C74 판2] 끝 훅 — 쓰기 한 묶음(_Txn). 추출 · OOC · 코드가 다 이것으로 행을 낸다.
#   emit(row) = fold 에 얹고 행 목록에 쌓은 뒤 뒤따름을 같이 낸다:
#     목표 progress 가 max 에 닿음 → close done 행(origin code:auto)
#     close done(아무 종류) → 그 줄을 기다리던 자는 줄마다 wake 행(origin code:wake)
#     목표 close done → 보상 몫(쓰기 뒤에 한 번: pending_doom_gain · 연결 시계 해결)
#   commit() = 행 쓰기 → 성공했을 때만 보상(세계 쓰기). 영수증 = 목표 · 선 일 목록(알림 · 되비침 재료).
# =========================================================

def _blank_row(turn: int, tid: str, op: str, origin: str = "", quote: str = "") -> Dict[str, Any]:
    return {"turn": int(turn or 0), "thread_id": tid, "op": op, "quote": quote, "title": "", "parties": "[]",
            "kind": "", "outcome": "", "remaining": "", "due_start": None, "due_end": None, "due_prec": "",
            "rank": "", "step": 0, "after_id": "", "origin": origin}


def sleepers_of(states: Dict[str, Dict[str, Any]], tid: str) -> List[Dict[str, Any]]:
    """tid 를 기다리며 자는 줄들(id 순)."""
    return [states[k] for k in sorted(states, key=_thread_num)
            if states[k].get("status") == "asleep" and states[k].get("after") == tid]


def _line_reaches(states: Dict[str, Dict[str, Any]], start: str, target: str) -> bool:
    """start 에서 after 를 따라가면 target 에 닿나(고리 검사). 길이 상한 = 줄 수."""
    seen = set()
    cur = start
    while cur and cur not in seen:
        if cur == target:
            return True
        seen.add(cur)
        cur = (states.get(cur) or {}).get("after") or ""
    return False


class _Txn:
    def __init__(self, channel_id: str, turn: int, states: Dict[str, Dict[str, Any]],
                 receipts: Optional[List[Dict[str, Any]]] = None):
        self.channel_id, self.turn, self.states = channel_id, int(turn or 0), states
        self.rows: List[Dict[str, Any]] = []
        self.receipts = receipts if isinstance(receipts, list) else []
        self._mine: List[Dict[str, Any]] = []   # 이 묶음의 영수증(커밋 실패면 바깥 목록에서 걷는다)
        self._done_goals: List[str] = []
        self.rewards: List[str] = []   # 보상 · 시계 해결 줄(커밋 뒤)

    def row(self, tid: str, op: str, origin: str = "", quote: str = "") -> Dict[str, Any]:
        return _blank_row(self.turn, tid, op, origin, quote)

    def _receipt(self, row: Dict[str, Any], st: Dict[str, Any]) -> None:
        op = row.get("op")
        if st.get("kind") != "goal" and op not in ("wake", "sleep") and not (op == "open" and st.get("after")):
            return
        rc = {"op": op, "id": st.get("id"), "kind": st.get("kind"), "title": st.get("title", ""),
              "origin": row.get("origin") or "", "status": st.get("status"), "after": st.get("after") or ""}
        if st.get("kind") == "goal":
            rc.update({"rank": st.get("rank"), "progress": st.get("progress", 0), "max": st.get("max", 0),
                       "step": _int(row.get("step"))})
        if op == "close":
            rc["outcome"] = st.get("outcome") or "done"
        self._mine.append(rc)
        self.receipts.append(rc)

    def emit(self, row: Dict[str, Any]) -> Dict[str, Any]:
        tid, op = row["thread_id"], row["op"]
        was_paid = bool((self.states.get(tid) or {}).get("paid"))
        _apply_row(self.states, row)
        self.rows.append(row)
        st = self.states.get(tid) or {}
        self._receipt(row, st)
        if (op == "progress" and st.get("kind") == "goal" and st.get("status") == "open"
                and int(st.get("progress") or 0) >= int(st.get("max") or 0) > 0):
            r2 = self.row(tid, "close", origin="code:auto")
            r2["outcome"] = "done"
            self.emit(r2)
        elif op == "close" and st.get("outcome") == "done":
            # [검토 #4] 한 번 끝나 보상받은 목표가 다시 열려 또 끝나면 보상은 없다(paid — 행에서 접히니 !다시도 따른다).
            if st.get("kind") == "goal" and tid not in self._done_goals and not was_paid:
                self._done_goals.append(tid)
            for sl in sleepers_of(self.states, tid):
                self.emit(self.row(sl["id"], "wake", origin="code:wake"))
        return st

    def commit(self, tag: str = "") -> bool:
        if not self.rows:
            return True
        try:
            import sqlite_store
            n = sqlite_store.append_thread_events(self.channel_id, self.rows)
        except Exception as e:
            logger.warning(f"[Thread] {tag} write fail (무시): {e}")
            n = 0
        if n < len(self.rows):
            logger.warning(f"[Thread] {tag} write short {n}/{len(self.rows)} — 보상 건너뜀")
            self.receipts[:] = [x for x in self.receipts if not any(x is m for m in self._mine)]
            self._mine = []
            return False
        for tid in self._done_goals:
            lines = goal_done_effects(self.channel_id, self.states.get(tid) or {}, self.turn)
            self.rewards += lines
            # [검토 #9] 보상 줄은 그 목표의 닫힘 영수증에 붙인다(알림이 다른 목표 줄 밑에 붙지 않게).
            for rc in reversed(self._mine):
                if rc.get("id") == tid and rc.get("op") == "close":
                    rc["reward"] = list(lines)
                    break
        return True


def goal_done_effects(channel_id: str, st: Dict[str, Any], turn: int) -> List[str]:
    """[C74 판2] 목표 close done 의 보상 — 옛 complete_quest 의 뒷몸. 긴장도 적립(pending_doom_gain, 수식은 다음 턴
    doom_module) + 이 목표에 걸린 시계 해결(end="quest"). 되비침 줄 목록. 실패해도 장부 행은 이미 섰다."""
    out: List[str] = []
    if not channel_id or not st:
        return out
    gain = doom_reward(st.get("rank"))
    try:
        import domain_manager
        w = domain_manager.get_world_state(channel_id)
        w["pending_doom_gain"] = int(w.get("pending_doom_gain", 0) or 0) + gain
        domain_manager.update_world_state(channel_id, w)
        out.append(f"📈 긴장도 +{gain} (다음 정산 시 페이즈 변조 적용)")
    except Exception as e:
        logger.warning(f"[Thread] goal reward fail: {e}")
    try:
        import game_world
        out += game_world.resolve_clocks_by_goal(channel_id, st.get("id", ""), st.get("title", ""), turn)
    except Exception as e:
        logger.warning(f"[Thread] goal clock resolve fail: {e}")
    logger.info("[Thread] goal done %s 「%s」 rank=%s doom+%d", st.get("id"), st.get("title", ""), st.get("rank"), gain)
    return out


def _resolve_after(ref: Any, states: Dict[str, Dict[str, Any]],
                   opened_here: Optional[Dict[str, str]] = None) -> Tuple[Optional[str], str]:
    """선 대상 → (id, why). 순서: 장부 id → 같은 목록 앞에서 연 줄 제목 → 장부 서 있는 줄 제목(정확, 하나일 때).
    why: '' | 'none'(안 줌) | 'unknown'."""
    if ref is None or (isinstance(ref, str) and not ref.strip()):
        return None, "none"
    if not isinstance(ref, (str, int)):
        return None, "unknown"
    s = str(ref).strip()
    if s.upper() in states:
        return s.upper(), ""
    n = _norm(s)
    if opened_here and n in opened_here:
        return opened_here[n], ""
    cands = [tid for tid, st in states.items() if st.get("status") in LIVE and _norm(st.get("title")) == n]
    if len(cands) == 1:
        return cands[0], ""
    return None, "unknown"


# =========================================================
# 쓰기 관문
# =========================================================

def apply_events(channel_id: str, turn: int, events: Any, source_text: str,
                 world: Optional[Dict[str, Any]] = None,
                 receipts: Optional[List[Dict[str, Any]]] = None) -> Dict[str, int]:
    """배치 world_state `threads` → 관문 → thread_log 행. 영수증 dict(셈) 반환.

    관문(이벤트마다, 앞 이벤트 결과가 뒤 이벤트의 상태): 모양 → 인용 접지(_norm 부분 문자열) → id → 전이 표.
    open 인데 id 가 기존 것이면: closed→resume, 그 외→no-op(dup). id 없는 open 인데 같은 title 이 서 있음/없던 일이면 dup.
    [C74 판2] goal open 은 rank, goal progress 는 step(small|big → 1|2, 없으면 small). 멈춘 목표에 진전 = resume 행 먼저.
      open 의 after = 선(장부 id · 같은 목록 앞 제목 · 장부 제목) — 대상이 이미 done 이면 선 없이 연다, 못 찾으면 선 버림.
      receipts(list)를 넘기면 목표 · 선 일 영수증을 거기 쌓는다(알림 재료)."""
    rc = {k: 0 for k in ("open", "progress", "close", "pause", "resume", "reschedule",
                         "rej_shape", "rej_quote", "rej_id", "rej_state", "dup",
                         "line", "line_drop", "auto_close", "wake")}
    if not channel_id or not isinstance(events, list) or not events or not _cfg("THREAD_LEDGER", True):
        return rc
    src = _norm(source_text)
    w = _world_of(channel_id, world)
    states = load_states(channel_id)
    tx = _Txn(channel_id, turn, states, receipts)
    opened_here: Dict[str, str] = {}
    title_max = int(_cfg("THREAD_TITLE_MAX", 40))
    _clock_moved: Optional[set] = None   # [C74 판3 검토 #1] 이 턴 시계(막아냄 · 기회 달성)가 이미 민 목표 — 처음 쓸 때 읽는다
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
        quote_s = quote.strip()[:300]
        tid = ev.get("id")
        tid = str(tid).strip().upper() if isinstance(tid, (str, int)) and str(tid).strip() else None
        title = _clip(ev.get("title"), title_max)
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
                # [2026-10-10 O-E] void(플레이어가 없던 일로 한 것)도 dup — 산문이 그 얘기를 또 꺼내도 되살리지 않는다.
                if any(s.get("status") in LIVE + ("void",) and _norm(s.get("title")) == nt
                       for s in states.values()):
                    rc["dup"] += 1
                    continue
                tid = next_thread_id(states)
                kind = ev.get("kind") if ev.get("kind") in KINDS else "matter"
                row = tx.row(tid, "open", quote=quote_s)
                row.update({"title": title, "kind": kind,
                            "parties": json.dumps(_parties(ev.get("parties")), ensure_ascii=False)})
                if kind == "goal":
                    row["rank"] = rank_of(ev.get("rank"))
                a_tid, why = _resolve_after(ev.get("after"), states, opened_here)
                if a_tid and states[a_tid].get("status") == "closed" and states[a_tid].get("outcome") == "done":
                    a_tid = None   # 기다릴 것이 이미 이뤄졌다 — 선 없이 연다
                elif a_tid and states[a_tid].get("status") not in LIVE:
                    a_tid = None   # [검토 #5] 이미 다른 끝(취소 · 어김 · 없던 일) — 깰 일이 없으니 선을 버리고 연다
                    rc["line_drop"] += 1
                elif not a_tid and why == "unknown":
                    rc["line_drop"] += 1
                if a_tid:
                    row["after_id"] = a_tid   # 자는 줄은 기한이 없다
                    rc["line"] += 1
                else:
                    _d = resolve_due(ev.get("due"), w) if ev.get("due") else None
                    if _d:
                        row["due_start"], row["due_end"], row["due_prec"] = _d
                tx.emit(row)
                opened_here[nt] = tid
                rc["open"] += 1
                continue
        if not tid or tid not in states:
            rc["rej_id"] += 1
            continue
        st = states[tid]
        if op == "progress" and st.get("kind") == "goal":
            # [C74 판3 검토 #1] 시계가 이 턴 이 목표를 밀었으면(렌더가 "moves forward"를 받아 그렸다) 같은 진전을 또 셈하지 않는다
            if _clock_moved is None:
                try:
                    import sqlite_store as _ss_cm
                    _clock_moved = {str(r.get("thread_id")) for r in _ss_cm.read_thread_log(channel_id)
                                    if int(r.get("turn") or 0) == int(turn or 0) and r.get("op") == "progress"
                                    and r.get("origin") in CLOCK_MOVE_ORIGINS}
                except Exception:
                    _clock_moved = set()
            if tid in _clock_moved:
                rc["dup"] += 1
                continue
        if op == "progress" and st.get("kind") == "goal" and st.get("status") == "paused":
            tx.emit(tx.row(tid, "resume", quote=quote_s))   # [C74 판2] 멈춘 목표에 진전 = 다시 열림
            rc["resume"] += 1
        if st["status"] not in _ALLOWED[op]:
            rc["rej_state"] += 1
            continue
        row = tx.row(tid, op, quote=quote_s)
        row.update({"title": title, "parties": json.dumps(_parties(ev.get("parties")), ensure_ascii=False)})
        if op == "close":
            row["outcome"] = ev.get("outcome") if ev.get("outcome") in OUTCOMES else "done"
        elif op == "progress":
            row["remaining"] = _clip(ev.get("remaining"), _REMAINING_MAX)
            if st.get("kind") == "goal":
                row["step"] = step_of(ev.get("step")) or 1
        elif op == "reschedule":
            _d = resolve_due(ev.get("due"), w) if ev.get("due") else None
            if _d:
                row["due_start"], row["due_end"], row["due_prec"] = _d
        tx.emit(row)
        rc[op] += 1
    rc["auto_close"] = sum(1 for r in tx.rows if r.get("origin") == "code:auto")
    rc["wake"] = sum(1 for r in tx.rows if r.get("op") == "wake")
    tx.commit("apply")
    if any(rc.values()):
        logger.info("[Thread] turn=%s open=%d progress=%d close=%d pause=%d resume=%d resched=%d | "
                    "rej_shape=%d rej_quote=%d rej_id=%d rej_state=%d dup=%d | line=%d line_drop=%d auto_close=%d wake=%d",
                    turn, rc["open"], rc["progress"], rc["close"], rc["pause"], rc["resume"], rc["reschedule"],
                    rc["rej_shape"], rc["rej_quote"], rc["rej_id"], rc["rej_state"], rc["dup"],
                    rc["line"], rc["line_drop"], rc["auto_close"], rc["wake"])
    return rc


def code_events(channel_id: str, turn: int, events: Any, origin: str = "code",
                world: Optional[Dict[str, Any]] = None,
                receipts: Optional[List[Dict[str, Any]]] = None) -> int:
    """[C74 판2] 코드가 내는 행 — 인용 관문 없음, origin 칸에 출처(code:stale · code:setback · code:seed …).
    op: open(제목 · kind · rank — 같은 제목이 서 있음/없던 일이면 건너뜀) · progress(step 정수, 부호 있음) · pause ·
    resume · close(outcome). 전이 표 밖이면 조용히 건너뛴다. 끝 훅은 추출과 같은 것. 쓴 행 수."""
    if not channel_id or not isinstance(events, list) or not events or not _cfg("THREAD_LEDGER", True):
        return 0
    w = _world_of(channel_id, world)
    states = load_states(channel_id)
    tx = _Txn(channel_id, turn, states, receipts)
    title_max = int(_cfg("THREAD_TITLE_MAX", 40))
    for ev in events[:_EVENTS_MAX]:
        if not isinstance(ev, dict):
            continue
        op = str(ev.get("op") or "").strip().lower()
        if op == "open":
            title = _clip(ev.get("title"), title_max)
            if not title:
                continue
            nt = _norm(title)
            if any(s.get("status") in LIVE + ("void",) and _norm(s.get("title")) == nt for s in states.values()):
                continue
            tid = next_thread_id(states)
            kind = ev.get("kind") if ev.get("kind") in KINDS else "matter"
            row = tx.row(tid, "open", origin=origin)
            row.update({"title": title, "kind": kind,
                        "parties": json.dumps(_parties(ev.get("parties")), ensure_ascii=False)})
            if kind == "goal":
                row["rank"] = rank_of(ev.get("rank"))
            _d = resolve_due(ev.get("due"), w) if _due_given(ev.get("due")) else None
            if _d:
                row["due_start"], row["due_end"], row["due_prec"] = _d
            tx.emit(row)
            continue
        tid = ev.get("id")
        tid = str(tid).strip().upper() if isinstance(tid, (str, int)) and str(tid).strip() else None
        if op not in _ALLOWED or not tid or tid not in states or states[tid].get("status") not in _ALLOWED[op]:
            continue
        row = tx.row(tid, op, origin=origin)
        if op == "progress":
            row["step"] = _int(ev.get("step"))
            if not row["step"]:
                continue
        elif op == "close":
            row["outcome"] = ev.get("outcome") if ev.get("outcome") in OUTCOMES else "done"
        tx.emit(row)
    n = len(tx.rows)
    ok = tx.commit(origin)
    if n:
        logger.info("[Thread] code(%s) turn=%s rows=%d ops=%s", origin, turn, n, [r["op"] for r in tx.rows])
    return n if ok else 0


def pause_stale_goals(channel_id: str, turn: int, threshold: Optional[int] = None) -> List[str]:
    """[C74 판2] 진전 없음 멈춤 — 열린 목표가 threshold(QUEST_STALE_ARCHIVE_TURNS 12)턴 동안 progress 가 안 움직이면
    pause 행(code:stale). 실패 아님 — 산문이 다시 그 일을 하면 resume(진전 = 자동 resume). 자리는 턴 번호를 올린
    직후 한 번(game_world.increment_turn_index). 멈춘 목표 id 목록."""
    if not channel_id or not _cfg("THREAD_LEDGER", True):
        return []
    th = int(threshold if threshold is not None else _cfg("QUEST_STALE_ARCHIVE_TURNS", 12))
    states = load_states(channel_id)
    stale = [tid for tid, st in states.items()
             if st.get("kind") == "goal" and st.get("status") == "open"
             and int(turn or 0) - int(st.get("last_step_turn") or 0) >= th]
    if not stale:
        return []
    code_events(channel_id, turn, [{"op": "pause", "id": t} for t in sorted(stale, key=_thread_num)], origin="code:stale")
    return stale


# =========================================================
# OOC 관문 — 플레이어가 OOC 로 여닫는다 (2026-10-10 이야기 실 O-E)
#   스펙: 파티쳇수정/state_v10/이야기실_지도·스펙_2026-10-10.md
#   배경 경로(apply_events)와 _apply_row·resolve_due·next_thread_id 를 같이 쓴다. 다른 점은 셋:
#   ① 인용 관문 없음(quote="" — 리더 증인 대조에 플레이어 말이 섞이지 않게) ② void op ③ void·closed 에서 resume.
# =========================================================

_OOC_ALLOWED = dict(_ALLOWED, resume=("paused", "closed", "void"), void=("open", "paused", "closed", "asleep"))
_DUE_KEYS = ("day_offset", "slot", "hour", "minute", "year", "month", "day_in_month")


def _due_given(due: Any) -> bool:
    """기한 칸이 실제로 무언가를 말하나. resolve_due 는 빈 dict 도 '오늘'로 풀기 때문에 OOC 는 여기서 거른다."""
    return isinstance(due, dict) and any(due.get(k) not in (None, "") for k in _DUE_KEYS)
_STATUS_KO = {"open": "열림", "paused": "멈춤", "closed": "닫힘", "void": "없던 일", "asleep": "잠"}


def find_thread(states: Dict[str, Dict[str, Any]], ref: Any) -> Tuple[Optional[str], str]:
    """id 또는 제목 → (thread_id, why). why: '' | 'none' | 'ambiguous'.
    제목: 정규화 같음 → 하나면 그것(여럿이면 서 있는 것이 하나일 때 그것) → 아니면 한쪽이 품는 게 딱 하나일 때만."""
    if isinstance(ref, (str, int)) and str(ref).strip().upper() in states:
        return str(ref).strip().upper(), ""
    t = _norm(ref) if isinstance(ref, str) else ""
    if not t:
        return None, "none"

    def _pick(ids: List[str]) -> Tuple[Optional[str], str]:
        if len(ids) == 1:
            return ids[0], ""
        live = [x for x in ids if states[x].get("status") in LIVE]
        if len(live) == 1:
            return live[0], ""
        return None, "ambiguous"

    exact = [tid for tid, st in states.items() if _norm(st.get("title")) == t]
    if exact:
        return _pick(exact)
    subs = [tid for tid, st in states.items()
            if _norm(st.get("title")) and (t in _norm(st.get("title")) or _norm(st.get("title")) in t)]
    if subs:
        return _pick(subs)
    return None, "none"


def _goal_bits(st: Dict[str, Any]) -> str:
    return f"{st.get('rank', 'normal')} {int(st.get('progress') or 0)}/{int(st.get('max') or 0)}"


def _ooc_line(op: str, st: Dict[str, Any], now: Optional[int], renamed: str = "",
              states: Optional[Dict[str, Dict[str, Any]]] = None) -> str:
    tid, title = st.get("id", "?"), st.get("title", "")
    goal = st.get("kind") == "goal"
    if op == "open":
        bits = [_KIND_KO.get(st.get("kind"), "사안")]
        if goal:
            bits.append(_goal_bits(st))
        ph = remaining_phrase(st, now)
        if ph:
            bits.append(ph)
        if st.get("status") == "asleep":
            bits.append(f"잠 — {st.get('after')} 끝나면 깬다")
        pt = _party_text(st, "·")
        line = f"🧵 {tid} 열림 — {title}({' · '.join(bits)})" + (f" [{pt}]" if pt else "")
    elif op == "close":
        line = f"🧵 {tid} 닫힘({_OUTCOME_KO.get(st.get('outcome'), '완료')}) — {title}"
    elif op == "reschedule":
        line = f"🧵 {tid} 기한 → {remaining_phrase(st, now) or '정해진 때 없음'} — {title}"
    elif op == "progress":
        if goal:
            line = f"🧵 {tid} 진행 {int(st.get('progress') or 0)}/{int(st.get('max') or 0)} — {title}"
        else:
            line = f"🧵 {tid} 남은 몫: {st.get('remaining') or '-'} — {title}"
    elif op == "void":
        line = f"🧵 {tid} 없던 일로 — {title}"
    elif op == "resume":
        line = f"🧵 {tid} 다시 열림 — {title}"
    elif op == "wake":
        line = f"🧵 {tid} 깨어남 — {title}"
    elif op == "sleep":
        _t = ((states or {}).get(st.get("after") or "") or {}).get("title", "")
        line = f"🧵 {tid} 잠 — {st.get('after')}" + (f" 「{_t}」" if _t else "") + f" 끝나면 깬다 — {title}"
    else:
        line = f"🧵 {tid} {_STATUS_KO.get(st.get('status'), op)} — {title}"
    return line + (f" (이름 고침: {renamed})" if renamed else "")


def _ooc_step(ev: Dict[str, Any], st: Dict[str, Any]) -> int:
    """OOC 목표 진전 — 플레이어가 말한 수 그대로(±). 없으면 +1. ±max 로 가둔다."""
    raw = ev.get("step") if ev.get("step") is not None else ev.get("delta")
    if isinstance(raw, str) and raw.strip().lower() in _STEP_WORD:
        n = _STEP_WORD[raw.strip().lower()]
    else:
        n = _num(raw) if raw is not None else 1   # "−1"(유니코드 빼기)도 −1 — 검토 #8
        n = 1 if n is None else n
    mx = int(st.get("max") or goal_max(st.get("rank")))
    return max(-mx, min(mx, n))


def apply_ooc_events(channel_id: str, turn: int, events: Any,
                     world: Optional[Dict[str, Any]] = None) -> List[str]:
    """OOC 편집 `thread` → thread_log 행. 되비침 줄 목록(실패는 ⚠ 로 시작).
    대상: id, 없으면 제목(title·value)으로 찾는다. id 와 title 이 같이 오면 title 은 새 이름이다.
    [C74 판2] goal 은 rank(open) · step(progress, ±) · 끝 훅(보상 · 시계 해결 · 깨우기) 같이.
      "after": "T4" = 선 잇기(sleep 행), "after": null = 선 끊고 깨우기(wake 행). 자는 줄에 resume = 깨우기."""
    out: List[str] = []
    if not channel_id or not isinstance(events, list) or not events:
        return out
    if not _cfg("THREAD_LEDGER", True):
        return ["⚠ 스레드 장부가 꺼져 있어(THREAD_LEDGER)"]
    w = _world_of(channel_id, world)
    now = now_abs(w)
    states = load_states(channel_id)
    title_max = int(_cfg("THREAD_TITLE_MAX", 40))
    rec: List[Dict[str, Any]] = []
    tx = _Txn(channel_id, turn, states, rec)

    def _emit(row: Dict[str, Any], op_: str, renamed_: str = "") -> None:
        n0 = len(tx.rows)
        tx.emit(row)
        out.append(_ooc_line(op_, states[row["thread_id"]], now, renamed_, states))
        for r2 in tx.rows[n0 + 1:]:   # 뒤따른 코드 행(다 참 닫기 · 깨우기)도 되비춘다
            out.append(_ooc_line(r2["op"], states[r2["thread_id"]], now, "", states))

    for ev in events[:_EVENTS_MAX]:
        if not isinstance(ev, dict):
            continue
        op = str(ev.get("op") or "").strip().lower()
        if op not in OPS and op not in FOLD_ONLY_OPS:
            out.append(f"⚠ 이야기 실: 모르는 일 '{op}' — open·close·pause·resume·reschedule·progress·void")
            continue
        title = _clip(ev.get("title"), title_max) or _clip(ev.get("value"), title_max)
        raw_id = ev.get("id")
        tid = str(raw_id).strip().upper() if isinstance(raw_id, (str, int)) and str(raw_id).strip() else None
        row = tx.row(tid or "", op, origin="ooc")
        if op == "open" and not (tid and tid in states):
            if not title:
                out.append("⚠ 이야기 실: 새로 열 제목이 없어")
                continue
            nt = _norm(title)
            same = [x for x, st in states.items() if _norm(st.get("title")) == nt]
            live = [x for x in same if states[x].get("status") in LIVE]
            if live:
                out.append(f"⚠ 🧵 {live[0]} 이미 {_STATUS_KO.get(states[live[0]]['status'])} — {states[live[0]].get('title', '')}")
                continue
            # [검토 #11] 다른 종류로 다시 여는 거면(사안 → 목표 등) 되살리지 않고 새로 연다.
            _k_new = ev.get("kind") if ev.get("kind") in KINDS else None
            revive = [x for x in same if states[x].get("status") == "void"
                      and (_k_new is None or states[x].get("kind") == _k_new)]
            if revive:
                tid, op, title = revive[-1], "resume", ""   # 같은 제목이 없던 일이면 그 id 를 되살린다(닫힌 같은 제목은 새 약속일 수 있어 새로 연다)
                row = tx.row(tid, op, origin="ooc")
            else:
                tid = next_thread_id(states)
                kind = ev.get("kind") if ev.get("kind") in KINDS else "matter"
                row.update({"thread_id": tid, "op": "open", "title": title, "kind": kind,
                            "parties": json.dumps(_parties(ev.get("parties")), ensure_ascii=False)})
                if kind == "goal":
                    row["rank"] = rank_of(ev.get("rank"))
                a_tid, why = _resolve_after(ev.get("after"), states)
                if a_tid and states[a_tid].get("status") not in LIVE:
                    out.append(f"ℹ {a_tid}는 이미 {_STATUS_KO.get(states[a_tid].get('status'), '끝')}"
                               f"({_OUTCOME_KO.get(states[a_tid].get('outcome'), '')})이라 선 없이 열어 — {states[a_tid].get('title', '')}")
                    a_tid = None
                elif not a_tid and why == "unknown":
                    out.append(f"⚠ 이야기 실: 기다릴 줄 '{ev.get('after')}'를 못 찾아 선 없이 열어")
                if a_tid:
                    row["after_id"] = a_tid
                else:
                    _d = resolve_due(ev.get("due"), w) if _due_given(ev.get("due")) else None
                    if _d:
                        row["due_start"], row["due_end"], row["due_prec"] = _d
                _emit(row, "open")
                continue
        elif op == "open":
            op = "resume"   # 있는 id 로 open = 다시 열기
            row = tx.row(tid, op, origin="ooc")
        renamed = ""
        if tid and tid in states:
            if title and _norm(title) != _norm(states[tid].get("title")):
                renamed = title
        else:
            tid, why = find_thread(states, title)
            if not tid:
                out.append(f"⚠ 이야기 실: '{title or raw_id or '?'}'를 " + ("하나로 못 골랐어(여럿이 걸림)" if why == "ambiguous" else "장부에서 못 찾았어"))
                continue
        st = states[tid]
        row["thread_id"] = tid
        # ── 선 고치기 — 잇기 = op sleep + "after": 대상(sleep 행), 끊기 = op wake 또는 자는 줄에 resume(wake 행).
        #    [검토 #2 · #3] 다른 op 에 붙은 "after" 칸은 보지 않는다 — 분류기가 상태의 after 를 베끼거나 null 로 채워
        #    보내도 그 편집(닫기 · 진전 · 이름 고침 …)이 그대로 돈다.
        line = None
        if op == "wake" or (op == "resume" and st.get("status") == "asleep"):
            line = ("wake", None)
        elif op == "sleep":
            line = ("sleep", ev.get("after"))
        if line:
            if line[0] == "wake":
                if st.get("status") != "asleep":
                    out.append(f"⚠ 🧵 {tid}는 자고 있지 않아 — {st.get('title', '')}")
                    continue
                _emit(tx.row(tid, "wake", origin="ooc"), "wake")
            else:
                a_tid, why = _resolve_after(line[1], states)
                if not a_tid:
                    out.append(f"⚠ 이야기 실: 기다릴 줄 '{line[1] or '?'}'를 못 찾았어")
                    continue
                if a_tid == tid or _line_reaches(states, a_tid, tid):
                    out.append(f"⚠ 🧵 {tid} ↔ {a_tid} 고리가 돼서 못 이어")
                    continue
                if st.get("status") not in LIVE:
                    out.append(f"⚠ 🧵 {tid}는 지금 {_STATUS_KO.get(st.get('status'), st.get('status'))}이라 못 재워 — {st.get('title', '')}")
                    continue
                if st.get("status") == "asleep" and st.get("after") == a_tid:
                    out.append(f"ℹ 🧵 {tid}는 이미 {a_tid}를 기다려 — {st.get('title', '')}")
                    continue
                if states[a_tid].get("status") not in LIVE:   # 이미 끝남(어떤 끝이든) — 깰 일이 없다
                    out.append(f"ℹ {a_tid}는 이미 {_STATUS_KO.get(states[a_tid].get('status'), '끝')}이라 잇지 않아 — "
                               f"{states[a_tid].get('title', '')}")
                    continue
                r = tx.row(tid, "sleep", origin="ooc")
                r["after_id"] = a_tid
                _emit(r, "sleep")
            continue
        if op == "progress" and st.get("kind") == "goal" and st.get("status") == "paused":
            _emit(tx.row(tid, "resume", origin="ooc"), "resume")   # 멈춘 목표에 진전 = 다시 열림
        if st.get("status") not in _OOC_ALLOWED.get(op, ()):
            out.append(f"⚠ 🧵 {tid}는 지금 {_STATUS_KO.get(st.get('status'), st.get('status'))}이라 그 일은 안 돼 — {st.get('title', '')}")
            continue
        row.update({"thread_id": tid, "op": op, "title": renamed,
                    "parties": json.dumps(_parties(ev.get("parties")), ensure_ascii=False)})
        if op == "close":
            row["outcome"] = ev.get("outcome") if ev.get("outcome") in OUTCOMES else "done"
        elif op == "progress":
            row["remaining"] = _clip(ev.get("remaining"), _REMAINING_MAX)
            if st.get("kind") == "goal":
                row["step"] = _ooc_step(ev, st)
                if not row["step"]:
                    out.append(f"⚠ 🧵 {tid} 진행 변화가 0이야 — {st.get('title', '')}")
                    continue
        elif op == "reschedule":
            if "due" in ev and ev.get("due") is None:
                pass   # 기한 없앰("때는 안 정했어") — 행의 due 칸이 비어 fold 가 지운다
            else:
                _d = resolve_due(ev.get("due"), w) if _due_given(ev.get("due")) else None
                if not _d:
                    out.append(f"⚠ 🧵 {tid} 새 기한을 못 풀었어 — {st.get('title', '')}")
                    continue
                row["due_start"], row["due_end"], row["due_prec"] = _d
        _emit(row, op, renamed)
    if tx.rows:
        if not tx.commit("OOC"):
            return ["⚠ 이야기 실 저장 실패"]
        out += tx.rewards
        logger.info("[Thread] OOC turn=%s rows=%d ops=%s", turn, len(tx.rows), [r["op"] for r in tx.rows])
    return out


def ooc_state(channel_id: str, world: Optional[Dict[str, Any]] = None,
              live_cap: int = 15, closed_cap: int = 5) -> List[Dict[str, Any]]:
    """편집 콜 현재 상태 `threads` — 서 있는 것(열림·멈춤·잠, 최근 활동순 live_cap) + 최근 닫힘 closed_cap. 비면 [].
    [C74 판2] 목표는 rank · progress("2/6"), 자는 줄은 after."""
    if not channel_id or not _cfg("THREAD_LEDGER", True):
        return []
    states = load_states(channel_id)
    if not states:
        return []
    now = now_abs(_world_of(channel_id, world))
    live = sorted((s for s in states.values() if s.get("status") in LIVE),
                  key=lambda s: -(s.get("last_turn") or 0))[:live_cap]
    done = sorted((s for s in states.values() if s.get("status") == "closed"),
                  key=lambda s: -(s.get("last_turn") or 0))[:closed_cap]
    out = []
    for st in live + done:
        it = {"id": st.get("id"), "kind": st.get("kind"), "title": st.get("title", ""),
              "parties": st.get("parties") or [], "status": st.get("status"),
              "due": remaining_phrase(st, now), "remaining": st.get("remaining") or "",
              "outcome": st.get("outcome") or ""}
        if st.get("kind") == "goal":
            it["rank"] = st.get("rank")
            it["progress"] = f"{int(st.get('progress') or 0)}/{int(st.get('max') or 0)}"
        if st.get("status") == "asleep":
            it["after"] = st.get("after") or ""
        out.append({k: v for k, v in it.items() if v not in ("", [], None)})
    return out


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


def recent_closed(states: Dict[str, Dict[str, Any]], world: Optional[Dict[str, Any]] = None,
                  channel_id: str = "", goals: bool = True) -> List[Dict[str, Any]]:
    """[2026-10-16 C45 R3] 최근 끝난 것(closed — void 는 없던 일이라 제외) — 지금 턴에서 THREAD_CLOSED_SHOW_TURNS 안,
    최근순 THREAD_CLOSED_SHOW_MAX 개. 장기기억 글 3번: 끝난 일은 지우지 말고 끝남 표시. 옛: 끝남은 루카만 봤다.
    [C74 판2] goals=False 면 목표는 뺀다(Slot 29 · Theoria — 옛 퀘스트를 안 보던 자리)."""
    w = _world_of(channel_id, world)
    try:
        now_t = int((w or {}).get("turn_index", 0) or 0)
    except (TypeError, ValueError):
        now_t = 0
    win = int(_cfg("THREAD_CLOSED_SHOW_TURNS", 30))
    mx = int(_cfg("THREAD_CLOSED_SHOW_MAX", 3))
    out = [st for st in (states or {}).values() if st.get("status") == "closed"
           and (goals or st.get("kind") != "goal")
           and (now_t <= 0 or now_t - int(st.get("last_turn") or 0) <= win)]
    out.sort(key=lambda st: -(st.get("last_turn") or 0))
    return out[:mx]


def _sleeper_tail(states: Dict[str, Dict[str, Any]], tid: str) -> str:
    """[C74 판2] 자는 줄 꼬리 — 기다리는 쪽 줄 끝에 "— 끝나면: …". 걸린 것이 있다는 신호지 보장이 아니다."""
    sl = sleepers_of(states, tid)
    if not sl:
        return ""
    bits = []
    for x in sl[:3]:
        pt = _party_text(x, "·")
        bits.append((f"{pt} " if pt else "") + str(x.get("title", "")))
    return " — 끝나면: " + "; ".join(bits)


def open_threads(states: Dict[str, Dict[str, Any]], now: Optional[int]) -> List[Dict[str, Any]]:
    """[C74 판4] 렌더 · 화면에 서는 열린 약속 · 사안 — 목표 · 자는 줄 빼고, 기한이 THREAD_OVERDUE_HIDE_DAYS 넘게 지난 것 빼고,
    기한 가까운 것 먼저 THREAD_RENDER_MAX 개(옛 Slot 29 `build_render_block` 의 거르기 그대로)."""
    hide = int(_cfg("THREAD_OVERDUE_HIDE_DAYS", 7)) * 1440
    items = []
    for st in (states or {}).values():
        if st.get("status") != "open" or st.get("kind") == "goal":
            continue
        if st.get("due_end") is not None and now is not None and now - int(st["due_end"]) > hide:
            continue
        items.append(st)
    return _render_order(items)[: int(_cfg("THREAD_RENDER_MAX", 6))]


def hook_candidates(states: Dict[str, Dict[str, Any]], absent: Any, now: Optional[int]) -> List[Dict[str, Any]]:
    """[2026-10-26 C81] 게시판 Scanner 10 재료 — 열린 약속 · 사안(`open_threads` 와 같은 거르기: 목표 · 자는 줄 · 기한이
    THREAD_OVERDUE_HIDE_DAYS 넘게 지난 것 뺌) 중 당사자에 무대 밖 인물이 있는 줄, 최근 건드린 순. 읽기만.
    돌려주는 것 = [{"thread": 상태, "party": 그 무대 밖 당사자}]."""
    ab = {_norm(a): str(a) for a in (absent or []) if str(a or "").strip()}
    if not ab:
        return []
    hide = int(_cfg("THREAD_OVERDUE_HIDE_DAYS", 7)) * 1440
    out = []
    for st in (states or {}).values():
        if st.get("status") != "open" or st.get("kind") == "goal":
            continue
        if st.get("due_end") is not None and now is not None and now - int(st["due_end"]) > hide:
            continue
        party = next((ab[_norm(p)] for p in (st.get("parties") or []) if _norm(p) in ab), None)
        if party:
            out.append({"thread": st, "party": party})
    out.sort(key=lambda x: -int(x["thread"].get("last_turn") or 0))
    return out


def thread_line(st: Dict[str, Any], states: Dict[str, Dict[str, Any]], now: Optional[int]) -> str:
    """[C74 판4] 약속 · 사안 한 줄(라벨·당사자·제목·남은 시간 + 자는 줄 꼬리, 인용 0) — 옛 `<Standing_Threads>` 줄 꼴."""
    label = _KIND_KO.get(st.get("kind"), "사안")
    pt = _party_text(st, " ↔ ")
    line = label + (f" · {pt}" if pt else "") + f": {st.get('title', '')}"
    ph = remaining_phrase(st, now)
    if ph:
        line += f" — {ph}"
    return line + _sleeper_tail(states, st.get("id", ""))


def closed_line(st: Dict[str, Any]) -> str:
    """[C74 판4] 끝난 줄 — `약속 · 리나: 제목 — 끝남(완료)`."""
    label = _KIND_KO.get(st.get("kind"), "사안")
    pt = _party_text(st, " ↔ ")
    return (label + (f" · {pt}" if pt else "") + f": {st.get('title', '')}"
            + f" — 끝남({_OUTCOME_KO.get(st.get('outcome'), '완료')})")


def open_goals(states: Dict[str, Dict[str, Any]]) -> List[Dict[str, Any]]:
    """열린 목표(멈춤 · 잠 제외), 연 순서."""
    return [states[k] for k in sorted(states, key=_thread_num)
            if states[k].get("kind") == "goal" and states[k].get("status") == "open"]


def goal_line(st: Dict[str, Any], states: Dict[str, Dict[str, Any]], now: Optional[int]) -> str:
    """[C74 판4] 목표 한 줄 — `제목 2/6 (normal, 기한 내일) — 끝나면: …`(옛 Slot 11 목표 블록 줄에서 Rank · Progress 이름표만 뗌)."""
    ph = remaining_phrase(st, now)
    return (f"{st.get('title', '')} {int(st.get('progress') or 0)}/{int(st.get('max') or 0)} "
            f"({st.get('rank', 'normal')}" + (f", 기한 {ph}" if ph else "") + ")"
            + _sleeper_tail(states, st.get("id", "")))


def goal_lines_ko(channel_id: str, states: Optional[Dict[str, Dict[str, Any]]] = None) -> List[str]:
    """[C74 판2] `!정보` 목표 줄 — 열린 목표 + 멈춘 목표(표시). 사람에게 보이는 말."""
    states = load_states(channel_id) if states is None else states
    out = []
    for k in sorted(states, key=_thread_num):
        st = states[k]
        if st.get("kind") != "goal" or st.get("status") not in ("open", "paused"):
            continue
        disp = ((_cfg("QUEST_RANK_SETTINGS", {}) or {}).get(st.get("rank")) or {}).get("display", st.get("rank", ""))
        bar = f"[{'■' * int(st.get('progress') or 0)}{'□' * max(0, int(st.get('max') or 0) - int(st.get('progress') or 0))}]"
        out.append(f"{st.get('title', '')} [{disp}] {bar} {int(st.get('progress') or 0)}/{int(st.get('max') or 0)}"
                   + (" (멈춤)" if st.get("status") == "paused" else ""))
    return out


GOAL_ANALYSIS_MAX = 6


def analysis_line(channel_id: str, world: Optional[Dict[str, Any]] = None, limit: int = 8) -> str:
    """Theoria 4c 한 줄. 열림·멈춤. 없으면 ''. [C74 판2] 목표는 서 있는 줄에서 뺀다(옛 Theoria 는 퀘스트를 안 봤다).
    [C74 판3 W2-2] 대신 열린 목표의 id · 제목만 따로 한 줄 — 새 시계의 `for_goal` 이 고를 수 있게(진행도 · 기한은 안 싣는다)."""
    if not channel_id or not _cfg("THREAD_LEDGER", True):
        return ""
    states = load_states(channel_id)
    now = now_abs(_world_of(channel_id, world))
    items = [s for s in states.values() if s.get("status") in ("open", "paused") and s.get("kind") != "goal"]
    items = _render_order(items)[:limit]
    _closed = recent_closed(states, world, channel_id, goals=False)   # [2026-10-16 C45 R3]
    _goals = open_goals(states)[-GOAL_ANALYSIS_MAX:]
    if not items and not _closed and not _goals:
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
    _out = []
    if parts:
        _out.append("- Standing threads: " + "; ".join(parts))
    if _goals:
        _out.append("- Goals: " + "; ".join(f"{g.get('id')} {g.get('title', '')}" for g in _goals))
    if _closed:
        _out.append("- Recently closed: " + "; ".join(f"{s.get('title', '')}({s.get('outcome') or 'done'})" for s in _closed))
    return "\n".join(_out)


def extraction_ledger_line(channel_id: str, world: Optional[Dict[str, Any]] = None) -> str:
    """배치 world_state 입력 줄. 서 있는 것(열림·멈춤·잠), 최근 활동순 THREAD_EXTRACT_MAX 개. 비면 '(empty)'.
    [C74 판2] 목표는 `normal 2/6`, 자는 줄은 `asleep, after T4` — 모델이 그 id 로 닫거나 이을 수 있게."""
    if not channel_id:
        return ""
    states = load_states(channel_id)
    now = now_abs(_world_of(channel_id, world))
    items = sorted((s for s in states.values() if s.get("status") in LIVE),
                   key=lambda s: -(s.get("last_turn") or 0))[: int(_cfg("THREAD_EXTRACT_MAX", 15))]
    if not items:
        return "Ledger: (empty)"
    parts = []
    for st in items:
        pt = _party_text(st, "·")
        seg = f"{st['id']} {st.get('kind', 'matter')}" + (f" {pt}" if pt else "") + f" 「{st.get('title', '')}」"
        tail = [st.get("status", "open")]
        if st.get("kind") == "goal":
            tail.append(f"{st.get('rank', 'normal')} {int(st.get('progress') or 0)}/{int(st.get('max') or 0)}")
        if st.get("status") == "asleep" and st.get("after"):
            tail.append(f"after {st['after']}")
        ph = remaining_phrase(st, now)
        if ph:
            tail.append(f"due {ph}")
        if st.get("remaining"):
            tail.append(f"left {st['remaining']}")
        parts.append(f"{seg} ({', '.join(tail)})")
    return "Ledger: " + " | ".join(parts)


def luka_lines(channel_id: str, world: Optional[Dict[str, Any]] = None) -> Tuple[str, str]:
    """루카 OOC (진행 중, 해결된) 두 줄 내용. 없으면 ''. [C74 판2] 목표도 같이(진행도 붙임)."""
    if not channel_id or not _cfg("THREAD_LEDGER", True):
        return "", ""
    states = load_states(channel_id)
    now = now_abs(_world_of(channel_id, world))
    act = _render_order([s for s in states.values() if s.get("status") == "open"])[:5]

    def _a(s):
        bits = []
        if s.get("kind") == "goal":
            bits.append(f"목표 {int(s.get('progress') or 0)}/{int(s.get('max') or 0)}")
        ph = remaining_phrase(s, now)
        if ph:
            bits.append(ph)
        return s.get("title", "") + (f"({', '.join(bits)})" if bits else "")
    active = ", ".join(_a(s) for s in act)
    done = sorted((s for s in states.values() if s.get("status") == "closed"),
                  key=lambda s: -(s.get("last_turn") or 0))[:3]
    resolved = ", ".join(f"{s.get('title', '')}({_OUTCOME_KO.get(s.get('outcome'), '완료')})" for s in done)
    return active, resolved


def closed_block_for_deep(channel_id: str, limit: int = 20) -> str:
    """[2026-10-16 C45 R3] 딥 압축 입력 — 장부의 끝난 약속·사안(void 제외) 최근순. 없으면 ''.
    딥 압축의 "Remove triggers that have been resolved"가 근거를 갖게 한다(떡밥 자유문을 코드로 대조하지는 않는다).
    [2026-10-26 C80] MEMORY_LAYERS_V2 면 딥 압축이 안 돌아 호출 0 — 2단계(떡밥 집 하나로)에서 정리한다."""
    if not channel_id or not _cfg("THREAD_LEDGER", True):
        return ""
    states = load_states(channel_id)
    done = sorted((s for s in states.values() if s.get("status") == "closed"),
                  key=lambda s: -(s.get("last_turn") or 0))[:limit]
    if not done:
        return ""
    return ("Closed in the ledger (settled; drop any active_memory_trigger these settle): "
            + "; ".join(f"{s.get('title', '')} — {s.get('outcome') or 'done'}" for s in done))


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
