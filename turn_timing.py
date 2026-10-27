# -*- coding: utf-8 -*-
"""[2026-10-24 C74 W0] 턴 시간 계측 — 동작 변화 0, 로그 줄만 더한다.

줄 셋(전부 INFO, `tools/turn_timing_report.py` 가 이 줄들만 읽는다):
  [Turn]         산문 턴 하나가 끝날 때 — 단계별 초 · 단계 안 세부(Theoria 두 콜 · drain · 렌더 시도 수) · get/save_domain 셈.
  [Turn-settled] 그 턴의 재그림(PanelRefresh)이 끝났을 때 — 산문 보낸 뒤 상태창 · 출력물이 다 서기까지 초 + 그 사이 배경 태스크.
  [BG]           배경 태스크 하나가 끝날 때 — 큐 대기 · 실행 초 (background_task_queue 가 `bg_done` 을 부른다).

설계:
  - 채널마다 한 턴씩 돈다(채널 잠금) → 상태는 채널 키 dict.
  - 단계 안 세부(`sub` · `count`)와 get/save_domain 셈은 **그 턴을 연 태스크 안에서 부른 것만** 센다(asyncio 태스크
    정체로 가른다). 턴 도중 만들어진 태스크(배경 일꾼 · 앱 정산 등)는 따로 태스크라 안 섞인다.
    [판1 검토 #4] 처음엔 contextvar 토큰이었다 — create_task 가 문맥을 복사해, 턴 안에서 처음 뜬 배경 일꾼이 그 턴
    토큰을 물려받아 배경 몫이 [Turn] 에 섞였다.
  - 무엇이 실패해도 턴은 그대로 간다 — 함수는 전부 예외를 삼킨다.
스펙 파티쳇수정/output_artifact/C74_전선정리_지도·스펙_2026-10-24.md §5 W0.
"""
import asyncio
import logging
import time
from typing import Any, Dict, Optional

logger = logging.getLogger("TurnTiming")

_T: Dict[str, Dict[str, Any]] = {}      # 채널 → 진행 중 턴 시계
_SENT: Dict[str, Dict[str, Any]] = {}   # 채널 → 산문 보낸 뒤 정리 기다림(settled 용)


def _now() -> float:
    return time.perf_counter()


def _task():
    try:
        return asyncio.current_task()
    except RuntimeError:   # 돌고 있는 루프 밖
        return None


def begin(channel_id: Any, kind: str = "turn") -> None:
    """턴 시계를 연다. 앞 턴 시계가 남아 있으면(예외로 끝난 턴) 덮는다."""
    try:
        ch = str(channel_id)
        t0 = _now()
        _T[ch] = {"kind": kind, "t0": t0, "last": t0, "stages": {}, "order": [],
                  "sub": {}, "count": {}, "dm": [0, 0, 0.0], "task": _task()}
    except Exception:
        pass


def _mine(ch: str) -> Optional[Dict[str, Any]]:
    st = _T.get(ch)
    if not st or st.get("task") is not _task():
        return None
    return st


def mark(channel_id: Any, stage: str) -> None:
    """지난 표시부터 지금까지를 `stage` 몫으로 더한다."""
    try:
        st = _T.get(str(channel_id))
        if not st:
            return
        now = _now()
        st["stages"][stage] = st["stages"].get(stage, 0.0) + (now - st["last"])
        if stage not in st["order"]:
            st["order"].append(stage)
        st["last"] = now
    except Exception:
        pass


def sub(channel_id: Any, key: str, seconds: float) -> None:
    """단계 안 세부 시간(Theoria 두 콜 · drain 등). 그 턴의 태스크에서 부른 것만."""
    try:
        st = _mine(str(channel_id))
        if st is not None:
            st["sub"][key] = st["sub"].get(key, 0.0) + float(seconds)
    except Exception:
        pass


def count(key: str, n: int = 1, channel_id: Any = None) -> None:
    """셈(렌더 시도 수 등). 채널을 모르면 지금 태스크가 연 턴."""
    try:
        if channel_id is not None:
            st = _mine(str(channel_id))
        else:
            cur = _task()
            st = next((s for s in _T.values() if s.get("task") is cur and cur is not None), None)
        if st is not None:
            st["count"][key] = st["count"].get(key, 0) + int(n)
    except Exception:
        pass


def dm(op: str, channel_id: Any, seconds: float) -> None:
    """domain_manager get_domain / save_domain 셈 — 그 턴의 태스크에서 부른 것만."""
    try:
        st = _mine(str(channel_id))
        if st is None:
            return
        if op == "get":
            st["dm"][0] += 1
        else:
            st["dm"][1] += 1
        st["dm"][2] += float(seconds)
    except Exception:
        pass


def sent(channel_id: Any, message_id: Any = None, turn: Any = None) -> None:
    """산문을 보냈다 — 정리 끝(settled) 재기의 출발점."""
    try:
        ch = str(channel_id)
        _SENT[ch] = {"t": _now(), "msg": message_id, "turn": turn, "bg": 0, "bg_run": 0.0}
    except Exception:
        pass


def _fmt(d: Dict[str, float], order=None) -> str:
    keys = order or list(d.keys())
    return " · ".join(f"{k} {d[k]:.1f}" for k in keys if k in d)


def finish(channel_id: Any, turn: Any = None) -> None:
    """턴 시계를 닫고 [Turn] 한 줄."""
    try:
        ch = str(channel_id)
        st = _T.pop(ch, None)
        if not st:
            return
        total = _now() - st["t0"]
        parts = [f"[Turn] ch={ch} t={turn if turn is not None else '?'} kind={st['kind']} total {total:.1f}s",
                 _fmt(st["stages"], st["order"]) or "-"]
        subs = _fmt(st["sub"])
        cnts = " · ".join(f"{k} {v}" for k, v in st["count"].items())
        if subs or cnts:
            parts.append(" · ".join(x for x in (subs, cnts) if x))
        g, s, sec = st["dm"]
        parts.append(f"dm get {g} save {s} {sec:.2f}s")
        logger.info(" | ".join(parts))
    except Exception:
        pass


def bg_done(channel_id: Any, name: str, wait: float, run: float, retry: int = 0, ok: bool = True) -> None:
    """배경 태스크 하나 끝 — [BG] 한 줄 + 정리 기다림 셈."""
    try:
        ch = str(channel_id)
        logger.info(f"[BG] ch={ch} {name} wait {wait:.1f}s run {run:.1f}s"
                    + (f" retry {retry}" if retry else "") + ("" if ok else " FAIL"))
        sv = _SENT.get(ch)
        if sv is not None:
            sv["bg"] += 1
            sv["bg_run"] += float(run)
    except Exception:
        pass


def settled(channel_id: Any, message_id: Any = None, edited: bool = False) -> None:
    """그 턴의 재그림이 끝났다 — [Turn-settled] 한 줄. 다른 메시지의 재그림이면(늦은 턴) 건너뛴다."""
    try:
        ch = str(channel_id)
        sv = _SENT.get(ch)
        if not sv:
            return
        if message_id is not None and sv.get("msg") is not None and message_id != sv.get("msg"):
            return
        _SENT.pop(ch, None)
        logger.info(f"[Turn-settled] ch={ch} t={sv.get('turn') if sv.get('turn') is not None else '?'} "
                    f"settle {_now() - sv['t']:.1f}s · bg {sv['bg']} (run {sv['bg_run']:.1f}s)"
                    + (" · edited" if edited else ""))
    except Exception:
        pass
