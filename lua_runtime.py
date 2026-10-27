# -*- coding: utf-8 -*-
"""[2026-10-23 C73 M7a] 코드 앱 실행기 — Lua 5.4 샌드박스(lupa). 스펙 `C73_모듈틀_M7코드앱_지도·스펙` §3.

한 호출 = 런타임 하나: 새로 띄움 → 앱 소스 실행(전역 함수 정의) → 훅 하나 부름 → 버림.
  Risu triggerlua 도 사건마다 새로 돈다 — 옮겨 온 코드는 이미 그 가정이다. 그래서 Lua 전역은 호출 사이에 안 남고,
  상태는 `lk` 장부(Session)에 둔다. Session 은 순수 메모리다(DB 를 모른다) — 호스트가 장부 사본을 넣고, 쓰기 모음을 받아 간다.

닫는 문(서버에서 남의 코드를 돌린다):
  · 전역 제거: os · io · require · dofile · loadfile · load · package · debug · collectgarbage · python · string.dump
  · 파이썬 객체 속성 접근 금지(attribute_filter) — 넘긴 함수의 `__globals__` 같은 길을 막는다
  · 명령 수 상한(count 훅 — 코루틴에도 건다) · 메모리 상한(max_memory)
  · pcall · xpcall · coroutine.resume 감싸기 — 상한을 넘긴 뒤엔 잡아도 다시 던진다(10-24 검토: pcall 이 훅 오류를 삼켜 끝없이 돌았다)
  · 돌려준 값은 깊이 · 개수 상한 안에서만 파이썬으로 옮긴다
  · **[10-24 검토] 봇은 Lua 를 자기 프로세스에서 안 돌린다** — 작업 프로세스(POOL, `python lua_runtime.py --worker`)에 보내고
    벽시계 상한(CALL_TIMEOUT)을 넘기면 그 프로세스를 죽인다. C 함수 안에서 도는 string 패턴처럼 훅이 못 잡는 것도
    여기서 끊긴다. 올리기 검사(맨 위 코드 실행)도 같은 길이다 — 봇 이벤트 루프에서 남의 Lua 가 도는 자리는 0.
  · 인-프로세스 `call` · `check_source` · `hooks_of` 는 도구(lkapp_check · 스크린샷 · 스모크) 몫이다.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import random
import sys
from typing import Any, Callable, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

try:   # lupa 가 없으면 코드 앱 전체가 꺼질 뿐 봇은 뜬다(Activity 의 .env 없음과 같은 꼴)
    from lupa import lua54 as _lua
    AVAILABLE = True
except Exception:   # pragma: no cover - 서버에 lupa 가 없을 때
    _lua = None
    AVAILABLE = False

DEFAULT_STEPS = 50_000_000          # 명령 수 상한(1000 단위로 센다)
DEFAULT_MEMORY = 64 * 1024 * 1024   # 바이트(Lua 쪽)
MAX_DEPTH = 24                      # Lua → 파이썬 옮길 때 표 깊이
MAX_ITEMS = 200_000                 # Lua → 파이썬 옮길 때 원소 합
_HOOK_EVERY = 1000

CALL_TIMEOUT = 5.0                  # 초 — 훅 한 번(벽시계). 넘기면 작업 프로세스를 죽인다
CHECK_TIMEOUT = 10.0                # 초 — 올리기 검사(맨 위 코드 실행 + 훅 목록)
POOL_SIZE = 2                       # 작업 프로세스 수(동시에 도는 호출 수)
WORKER_RLIMIT_AS = 768 * 1024 * 1024   # 작업 프로세스 주소 공간 상한(리눅스) — 파이썬 쪽 변환까지 묶는다
_PIPE_LIMIT = 16 * 1024 * 1024      # 한 줄(요청 · 응답) 상한

HISTORY_MAX = 50
MAX_REPORT_CHARS = 2000
MAX_REPORTS = 10
MAX_KEY_CHARS = 64

_BLOCKED_GLOBALS = ("os", "io", "require", "dofile", "loadfile", "load", "package",
                    "collectgarbage", "python", "warn")
# print 은 막지 않고 lk.log 로 돌린다(옮겨 온 Risu 코드가 흔히 쓴다). 표준 출력에 닿는 길은 0 — 10-24 재검토:
#   작업 프로세스가 stdout 을 규약 줄로 쓰던 때 print 한 줄이 가짜 응답이 됐다(규약은 이제 따로 뗀 fd 로만 간다).

# count 훅 + 코루틴 감싸기 + 잡기 감싸기.
#   훅은 스레드(코루틴)마다라 새 코루틴에도 같은 훅을 건다. sethook 을 지역에 붙든 뒤 debug 를 지운다.
#   pcall · xpcall · coroutine.resume 은 오류를 잡는다 — 상한을 넘긴 뒤 잡은 것은 다시 던져 바깥으로 올린다.
_PRELUDE = """
local _sethook = debug.sethook
local _error = error            -- 10-24 재검토: 앱이 전역 error 를 갈아 끼워도 상한은 그대로 던진다
local _n, _limit = 0, %d
local _MSG = "lk: 명령 수 상한을 넘었다"
local function _over() return _n > _limit end
local function _hook()
  _n = _n + %d
  if _n > _limit then _error(_MSG, 2) end
end
_sethook(_hook, "", %d)
local _pack, _unpack = table.pack, table.unpack
local _pcall, _xpcall, _resume = pcall, xpcall, coroutine.resume
pcall = function(...)
  local r = _pack(_pcall(...))
  if _over() then _error(_MSG, 0) end
  return _unpack(r, 1, r.n)
end
xpcall = function(...)
  local r = _pack(_xpcall(...))
  if _over() then _error(_MSG, 0) end
  return _unpack(r, 1, r.n)
end
local _resume_w = function(...)
  local r = _pack(_resume(...))
  if _over() then _error(_MSG, 0) end
  return _unpack(r, 1, r.n)
end
coroutine.resume = _resume_w
local _cc = coroutine.create
coroutine.create = function(f)
  local co = _cc(f)
  _sethook(co, _hook, "", %d)
  return co
end
local _create_w = coroutine.create
coroutine.wrap = function(f)
  local co = _create_w(f)
  return function(...)
    local r = _pack(_resume_w(co, ...))
    if not r[1] then _error(r[2], 2) end
    return _unpack(r, 2, r.n)
  end
end
debug = nil
"""


class LuaFail(Exception):
    """앱 호출 실패 — 문법 · 실행 오류 · 상한."""


def _deny_attr(obj, name, is_setting):
    raise AttributeError("lk: 파이썬 속성 접근 금지")


def _is_table(v: Any) -> bool:
    return _lua is not None and _lua.lua_type(v) == "table"


def to_py(v: Any, _depth: int = 0, _budget: Optional[list] = None) -> Any:
    """Lua 값 → 파이썬(JSON 꼴). 함수 · userdata 는 None. 1..n 연속 정수 키 표는 list."""
    if _budget is None:
        _budget = [MAX_ITEMS]
    if v is None or isinstance(v, (bool, int, str)):
        return v
    if isinstance(v, float):
        if v != v or v in (float("inf"), float("-inf")):
            raise LuaFail("NaN · 무한대는 담을 수 없다")
        return v
    if isinstance(v, bytes):
        return v.decode("utf-8", "replace")
    if not _is_table(v):
        return None
    if _depth >= MAX_DEPTH:
        raise LuaFail("돌려준 표가 너무 깊다")
    items = []
    for k, val in v.items():
        _budget[0] -= 1
        if _budget[0] < 0:
            raise LuaFail("돌려준 표가 너무 크다")
        items.append((k, val))
    keys = [k for k, _ in items]
    if keys and all(isinstance(k, int) and not isinstance(k, bool) for k in keys) \
            and sorted(keys) == list(range(1, len(keys) + 1)):
        return [to_py(val, _depth + 1, _budget) for _, val in sorted(items, key=lambda kv: kv[0])]
    out: Dict[str, Any] = {}
    for k, val in items:
        if isinstance(k, bytes):
            k = k.decode("utf-8", "replace")
        if isinstance(k, float) and k.is_integer():
            k = int(k)
        if not isinstance(k, (str, int)) or isinstance(k, bool):
            continue
        out[str(k)] = to_py(val, _depth + 1, _budget)
    return out


def _to_lua(L: Any, v: Any) -> Any:
    if isinstance(v, (dict, list, tuple)):
        return L.table_from(_jsonish(v), recursive=True)
    return v


def _jsonish(v: Any) -> Any:
    """table_from(recursive) 가 받는 꼴로 — tuple → list, dict 키는 글."""
    if isinstance(v, dict):
        return {str(k): _jsonish(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [_jsonish(x) for x in v]
    return v


def _wrap(L: Any, fn: Callable) -> Callable:
    """파이썬 함수 → Lua 에서 부를 함수. 인자는 파이썬으로, 돌려줌은 Lua 로 옮긴다."""
    def _call(*args):
        py_args = [to_py(a) for a in args]
        return _to_lua(L, fn(*py_args))
    return _call


def _new_runtime(steps: int, memory: int) -> Any:
    L = _lua.LuaRuntime(unpack_returned_tuples=True, register_eval=False, register_builtins=False,
                        max_memory=int(memory), attribute_filter=_deny_attr, encoding="utf-8")
    L.execute(_PRELUDE % (int(steps), _HOOK_EVERY, _HOOK_EVERY, _HOOK_EVERY))
    G = L.globals()
    for k in _BLOCKED_GLOBALS:
        G[k] = None
    try:
        G.string.dump = None
    except Exception:
        pass
    return L


def _clean_source(source: str) -> str:
    if source.startswith("﻿"):
        source = source[1:]
    return source


def _load(source: str, api: Optional[Dict[str, Callable]], data: Optional[Dict[str, Any]],
          steps: int, memory: int) -> Any:
    L = _new_runtime(steps, memory)
    lk = L.table_from({})
    for name, fn in (api or {}).items():
        lk[name] = _wrap(L, fn)
    for name, val in (data or {}).items():
        lk[name] = _to_lua(L, val)
    L.globals().lk = lk
    log = (api or {}).get("log")
    L.globals().print = _wrap(L, (lambda *a: log(" ".join(_print_str(x) for x in a))) if log else (lambda *a: None))
    L.execute(_clean_source(source))
    return L


def _print_str(x: Any) -> str:
    if x is None:
        return "nil"
    if isinstance(x, bool):
        return "true" if x else "false"
    if isinstance(x, (dict, list)):
        return json.dumps(x, ensure_ascii=False)[:200]
    return str(x)


# =====================================================================
# lk 장부 — 순수 메모리(작업 프로세스 안에서도, 도구에서도 같은 몸)
# =====================================================================

class Session:
    """한 호출의 lk — 장부 사본 · 쓰기 모음 · 보고 모음 · 로그. DB 를 모른다.
    app = {"id", "settings"}(호스트가 넣는다) · kv = 지금 장부 · world = lk.world() · hist = lk.history 재료
    · tags = lk.tags 재료([{turn, tag, fields, evidence}] 오래된 → 최신, [M7b])."""

    def __init__(self, app: Dict[str, Any], kv: Dict[str, Any], world: Dict[str, Any],
                 history: List[Dict[str, Any]], tags: Optional[List[Dict[str, Any]]] = None):
        self.app = app or {}
        self.kv = dict(kv or {})
        self.writes: Dict[str, Any] = {}
        self.reports: List[str] = []
        self.logs: List[str] = []
        self.world = world or {}
        self.hist = list(history or [])
        self.tag_rows = [t for t in (tags or []) if isinstance(t, dict)]

    @staticmethod
    def _key(k: Any) -> str:
        k = str(k if k is not None else "")
        if not k or len(k) > MAX_KEY_CHARS or k.startswith("_"):
            raise ValueError(f"lk: 쓸 수 없는 키 `{k[:20]}`(1~{MAX_KEY_CHARS}자, `_` 로 시작 금지)")
        return k

    def api(self) -> Dict[str, Any]:
        def get(k=None):
            k = str(k if k is not None else "")
            return None if k.startswith("_") else self.kv.get(k)

        def set_(k=None, v=None):
            k = self._key(k)
            try:
                json.dumps(v, ensure_ascii=False, allow_nan=False)   # [10-24 검토] NaN · 무한대는 화면 JSON 을 깨뜨린다
            except Exception:
                raise ValueError("lk.set: 글 · 수(NaN · 무한대 제외) · 참거짓 · 표만 담아요")
            if v is None:
                self.kv.pop(k, None)
            else:
                self.kv[k] = v
            self.writes[k] = v
            return None

        def keys():
            return sorted(k for k in self.kv if not k.startswith("_"))

        def setting(k=None):
            return (self.app.get("settings") or {}).get(str(k))

        def report(t=None):
            t = str(t if t is not None else "").strip()
            if not t:
                return None
            if len(self.reports) >= MAX_REPORTS:
                raise ValueError(f"lk.report: 한 번에 {MAX_REPORTS}장까지")
            self.reports.append(t[:MAX_REPORT_CHARS])
            return None

        def world():
            return self.world

        def history(n=None):
            try:
                n = int(n if n is not None else HISTORY_MAX)
            except Exception:
                n = HISTORY_MAX
            n = max(0, min(HISTORY_MAX, n))
            return self.hist[-n:] if n else []

        def tags(n=None):
            # [2026-10-24 C73 M7b] 최근 n턴 신고 — 턴 > (지금 턴 − n). n ≤ 50, 빠지면 50.
            #   on_input 에선 지금 턴 = 지난 턴 번호라 lk.tags(1) = 지난 턴 신고 · on_tags 에선 이번 정산 몫.
            try:
                n = int(n if n is not None else HISTORY_MAX)
            except Exception:
                n = HISTORY_MAX
            n = max(0, min(HISTORY_MAX, n))
            if not n:
                return []
            try:
                now = int(self.world.get("turn") or 0)
            except Exception:
                now = 0
            return [dict(t) for t in self.tag_rows if int(t.get("turn") or 0) > now - n]

        def rand(lo=1, hi=None):
            lo = int(lo)
            hi = lo if hi is None else int(hi)
            if hi < lo:
                lo, hi = hi, lo
            return random.randint(lo, hi)

        def json_encode(v=None):
            return json.dumps(v, ensure_ascii=False)

        def json_decode(s=None):
            return json.loads(str(s if s is not None else "null"))

        def log(t=None):
            if len(self.logs) < 50:
                self.logs.append(str(t if t is not None else "")[:300])
            return None

        return {"get": get, "set": set_, "keys": keys, "setting": setting, "report": report, "world": world,
                "history": history, "tags": tags, "rand": rand, "json_encode": json_encode, "json_decode": json_decode,
                "log": log}


def stub_api() -> Dict[str, Any]:
    """올리기 검사 · 훅 목록용 빈 받침 — 맨 위 코드가 lk 를 불러도 터지지 않게."""
    return Session({"id": "check", "settings": {}}, {}, {}, []).api()


# =====================================================================
# 인-프로세스 — 도구(lkapp_check · 스크린샷 · 스모크) 몫. 봇은 아래 POOL 을 쓴다.
# =====================================================================

def check_source(source: str, api: Optional[Dict[str, Callable]] = None, data: Optional[Dict[str, Any]] = None,
                 steps: int = DEFAULT_STEPS, memory: int = DEFAULT_MEMORY) -> Tuple[bool, str]:
    """올리기 검사 — 소스를 한 번 실행해 전역 함수를 정의해 본다. (ok, 오류 한 줄)."""
    if not AVAILABLE:
        return False, "lupa 없음"
    try:
        _load(source, api if api is not None else stub_api(), data, steps, memory)
        return True, ""
    except Exception as e:
        return False, _err_line(e)


def hooks_of(source: str, names: Tuple[str, ...], api: Optional[Dict[str, Callable]] = None,
             data: Optional[Dict[str, Any]] = None, steps: int = DEFAULT_STEPS,
             memory: int = DEFAULT_MEMORY) -> Tuple[str, ...]:
    """소스가 정의하는 훅 이름(전역 함수) 목록."""
    if not AVAILABLE:
        return ()
    try:
        G = _load(source, api if api is not None else stub_api(), data, steps, memory).globals()
        return tuple(n for n in names if _lua.lua_type(G[n]) == "function")
    except Exception:
        return ()


def call(source: str, hook: str, args: Tuple[Any, ...] = (), api: Optional[Dict[str, Callable]] = None,
         data: Optional[Dict[str, Any]] = None, steps: int = DEFAULT_STEPS,
         memory: int = DEFAULT_MEMORY) -> Tuple[bool, Any]:
    """훅 하나 부르기(이 프로세스 안) → (있었나, 돌려준 값 파이썬). 훅이 없으면 (False, None). 실패는 LuaFail."""
    if not AVAILABLE:
        raise LuaFail("lupa 없음 — 코드 앱 꺼짐")
    try:
        L = _load(source, api, data, steps, memory)
        fn = L.globals()[hook]
        if _lua.lua_type(fn) != "function":
            return False, None
        res = fn(*[_to_lua(L, a) for a in args])
        if isinstance(res, tuple):
            res = res[0] if res else None
        return True, to_py(res)
    except LuaFail:
        raise
    except Exception as e:
        raise LuaFail(_err_line(e)) from None


def _err_line(e: BaseException) -> str:
    name = type(e).__name__
    msg = str(e).strip().splitlines()[0] if str(e).strip() else ""
    if name == "LuaMemoryError" or "not enough memory" in msg:
        return "메모리 상한을 넘었다"
    return (f"{name}: {msg}" if msg else name)[:300]


# =====================================================================
# 작업 프로세스 — 요청 하나 = JSON 한 줄(stdin) → 응답 한 줄(stdout)
# =====================================================================

def handle_request(req: Dict[str, Any]) -> Dict[str, Any]:
    """요청 하나를 이 프로세스에서 처리 → 응답 dict(JSON 꼴). 작업 프로세스가 부르고, 도구도 그대로 부를 수 있다.
    op = "check"(소스 실행 + 훅 목록) | "call"(훅 하나)."""
    op = str(req.get("op") or "call")
    source = str(req.get("source") or "")
    steps = int(req.get("steps") or DEFAULT_STEPS)
    memory = int(req.get("memory") or DEFAULT_MEMORY)
    if op == "check":
        names = tuple(str(x) for x in (req.get("hooks") or ()))
        try:
            G = _load(source, stub_api(), None, steps, memory).globals()
            return {"ok": True, "hooks": [n for n in names if _lua.lua_type(G[n]) == "function"]}
        except Exception as e:
            return {"ok": False, "error": _err_line(e)}
    sess = Session(req.get("app") or {}, req.get("kv") or {}, req.get("world") or {}, req.get("history") or [],
                   req.get("tags") or [])
    try:
        ran, res = call(source, str(req.get("hook") or ""), tuple(req.get("args") or ()), sess.api(), None, steps, memory)
    except LuaFail as e:
        return {"ok": False, "error": str(e), "logs": sess.logs}
    return {"ok": True, "ran": ran, "result": res, "writes": sess.writes, "reports": sess.reports, "logs": sess.logs}


def _worker_main() -> None:   # pragma: no cover - 작업 프로세스 몸(스모크가 프로세스로 돌려 본다)
    try:
        import resource
        resource.setrlimit(resource.RLIMIT_AS, (WORKER_RLIMIT_AS, WORKER_RLIMIT_AS))
    except Exception:
        pass
    # [10-24 재검토] 규약 줄은 따로 뗀 fd 로만 쓴다 — fd 1(표준 출력)은 /dev/null 로 돌려, Lua · C 가 무엇을 찍어도
    #   응답 줄에 섞이지 않게 한다(print 한 줄이 가짜 응답이 되던 구멍).
    proto_fd = os.dup(1)
    devnull = os.open(os.devnull, os.O_WRONLY)
    os.dup2(devnull, 1)
    sys.stdout = open(os.devnull, "w")
    out = os.fdopen(proto_fd, "w", encoding="utf-8")
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            resp = handle_request(json.loads(line))
        except Exception as e:
            resp = {"ok": False, "error": _err_line(e)}
        try:
            text = json.dumps(resp, ensure_ascii=False, allow_nan=False)
        except Exception as e:
            text = json.dumps({"ok": False, "error": f"응답을 못 옮김: {_err_line(e)}"}, ensure_ascii=False)
        out.write(text + "\n")
        out.flush()


class _Worker:
    def __init__(self, proc: Any):
        self.proc = proc

    def kill(self) -> None:
        try:
            self.proc.kill()
        except Exception:
            try:
                os.kill(self.proc.pid, 9)
            except Exception:
                pass


class WorkerPool:
    """작업 프로세스 POOL_SIZE 개. 요청 하나 = 일꾼 하나를 빌려 한 줄 쓰고 한 줄 읽는다.
    벽시계 상한을 넘기거나 일꾼이 죽으면 그 일꾼을 죽이고 버린다(다음 요청이 새로 띄운다).
    이벤트 루프가 바뀌면(스모크가 호출마다 새 루프를 쓴다) 옛 일꾼은 버리고 새로 띄운다."""

    def __init__(self, size: int = POOL_SIZE):
        self.size = max(1, int(size))
        self._loop: Any = None
        self._idle: List[_Worker] = []
        self._sem: Optional[asyncio.Semaphore] = None
        self.spawned = 0
        self.killed = 0

    def _reset_for(self, loop: Any) -> None:
        for w in self._idle:
            w.kill()
        self._idle = []
        self._loop = loop
        self._sem = asyncio.Semaphore(self.size)

    async def _spawn(self) -> _Worker:
        proc = await asyncio.create_subprocess_exec(
            sys.executable, os.path.abspath(__file__), "--worker",
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=None, limit=_PIPE_LIMIT)
        self.spawned += 1
        return _Worker(proc)

    async def run(self, req: Dict[str, Any], timeout: float = CALL_TIMEOUT) -> Dict[str, Any]:
        """요청 → 응답. 시간 상한 · 일꾼 죽음 · 파이프 오류는 LuaFail."""
        if not AVAILABLE:
            raise LuaFail("lupa 없음 — 코드 앱 꺼짐")
        loop = asyncio.get_running_loop()
        if loop is not self._loop:
            self._reset_for(loop)
        async with self._sem:
            w = self._idle.pop() if self._idle else await self._spawn()
            ok_back = False
            try:
                data = (json.dumps(req, ensure_ascii=False) + "\n").encode("utf-8")
                w.proc.stdin.write(data)
                await asyncio.wait_for(w.proc.stdin.drain(), timeout=timeout)
                line = await asyncio.wait_for(w.proc.stdout.readline(), timeout=timeout)
                if not line:
                    raise LuaFail("작업 프로세스가 죽었다(메모리 · 오류)")
                resp = json.loads(line.decode("utf-8"))
                ok_back = True
                return resp
            except asyncio.TimeoutError:
                raise LuaFail(f"시간 상한({timeout:g}초)을 넘었다") from None
            except LuaFail:
                raise
            except Exception as e:
                raise LuaFail(f"작업 프로세스 오류: {_err_line(e)}") from None
            finally:
                if ok_back:
                    self._idle.append(w)
                else:
                    w.kill()
                    self.killed += 1


POOL = WorkerPool()


if __name__ == "__main__" and "--worker" in sys.argv:   # pragma: no cover
    _worker_main()
