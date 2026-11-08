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
import re
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
MAX_VALUE_WRITES = 50               # [C116 판2] 한 호출의 선언 값 쓰기(lk.value_set · lk.value_add) 상한
MAX_CORE_WRITES = 20                # [C116 판6] 한 호출의 관계 · 목표 쓰기 상한(따로 셈)
HANDOUT_KINDS = ("message", "bulletin", "sns")   # [C116 판8] lk.handout — 도착물 꼴(편지 · 게시판 · SNS)
MAX_HANDOUTS_PER_CALL = 3
HANDOUT_BODY_MAX = 4000
ASK_TASK_MAX = 4000                 # [C116 판7] lk.ask — 할 일 글 · 재료 글 · 답 길이
ASK_MATERIAL_MAX = 8000
ASK_CHARS_MAX = 2000
ASK_CHARS_DEFAULT = 800
REL_SHIFT_MAX = 20                  # [C116 판6] lk.relation_shift 한 번의 폭(bond · tension 각각 ±)

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


# [2026-11-06 C117 실측] Lua 글은 바이트다 — 한글을 `string.sub` 로 자르면 글자 가운데가 잘린 바이트가 남고, 그 글이 파이썬으로
#   넘어오는 자리(lk.* 인자 · 훅이 돌려준 값)에서 lupa 의 UTF-8 풀기가 터져 호출이 통째로 실패했다(아틀리에 의뢰 제목 60바이트 자르기).
#   넘어오는 자리 둘을 Lua 쪽에서 감싼다: 잘린 바이트 = U+FFFD 한 자. 깨진 글이 없으면 손 안 대고 그대로(표 복사 0).
#   string.sub 자체는 그대로(바이트 셈 — string.find 위치와 짝).
_FIX_LUA = r"""
local lk, G = ...
local _utf8len, _sub, _byte, _type, _pairs = utf8.len, string.sub, string.byte, type, pairs
local _concat, _pack, _unpack = table.concat, table.pack, table.unpack
local function _fixs(s)
  if _utf8len(s) then return s end
  local out, i, n = {}, 1, #s
  while i <= n do
    local ok, pos = _utf8len(s, i)
    if ok then out[#out + 1] = _sub(s, i); break end
    out[#out + 1] = _sub(s, i, pos - 1)
    out[#out + 1] = "\239\191\189"
    i = pos + 1
    while i <= n do                       -- 잘린 글자의 나머지 이음 바이트(0x80~0xBF)는 같은 한 자로
      local b = _byte(s, i)
      if b < 128 or b >= 192 then break end
      i = i + 1
    end
  end
  return _concat(out)
end
local function _bad(v, d)
  local t = _type(v)
  if t == "string" then return not _utf8len(v) end
  if t ~= "table" or d > 20 then return false end
  for k, x in _pairs(v) do
    if (_type(k) == "string" and not _utf8len(k)) or _bad(x, d + 1) then return true end
  end
  return false
end
local function _fix(v, d)
  local t = _type(v)
  if t == "string" then return _fixs(v) end
  if t ~= "table" or d > 20 then return v end
  local out = {}
  for k, x in _pairs(v) do
    out[(_type(k) == "string") and _fixs(k) or k] = _fix(x, d + 1)
  end
  return out
end
local function _clean(v) if _bad(v, 0) then return _fix(v, 0) end return v end
local function _guard(f)
  return function(...)
    local a = _pack(...)
    for i = 1, a.n do a[i] = _clean(a[i]) end
    return f(_unpack(a, 1, a.n))
  end
end
for name, f in _pairs(lk) do
  if _type(f) == "userdata" then lk[name] = _guard(f) end
end
G.print = _guard(G.print)
return function(f, ...) return _clean((f(...))) end
"""


def _load_full(source: str, api: Optional[Dict[str, Callable]], data: Optional[Dict[str, Any]],
               steps: int, memory: int) -> Tuple[Any, Any]:
    """(Lua 런타임, 훅 부르는 받침). 받침 = 훅이 돌려준 값의 깨진 UTF-8 을 고쳐서 넘긴다(C117)."""
    L = _new_runtime(steps, memory)
    lk = L.table_from({})
    for name, fn in (api or {}).items():
        lk[name] = _wrap(L, fn)
    for name, val in (data or {}).items():
        lk[name] = _to_lua(L, val)
    G = L.globals()
    G.lk = lk
    log = (api or {}).get("log")
    G.print = _wrap(L, (lambda *a: log(" ".join(_print_str(x) for x in a))) if log else (lambda *a: None))
    tramp = L.execute(_FIX_LUA, lk, G)
    L.execute(_clean_source(source))
    return L, tramp


def _load(source: str, api: Optional[Dict[str, Callable]], data: Optional[Dict[str, Any]],
          steps: int, memory: int) -> Any:
    return _load_full(source, api, data, steps, memory)[0]


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

# [2026-11-06 C117 C2] lk.relation_shift 말 → 폭의 받침 — 봇은 호스트가 config.REL_BOND_SHIFT · REL_TENSION_SHIFT 를 싣는다
#   (도구 · 인-프로세스 호출용. 스모크가 config 와 같은지 본다).
REL_WORDS_FALLBACK = {"bond": {"much_warmer": 5, "warmer": 3, "holds": 0, "cooler": -3, "much_cooler": -5},
                      "tension": {"spikes": 10, "rises": 5, "holds": 0, "eases": -5}}


def keep_shape(new: Any, old: Any) -> Any:
    """[2026-11-06 C117 C6] Lua 표는 목록 · 사전이 한 꼴이라 빈 표는 `{}` 로 온다 — 그 자리가 전에 목록이었으면 `[]` 로.
    안쪽 칸도 같은 규칙(같은 키 · 같은 자리끼리). 처음 쓰는 자리 · 사전이었던 자리는 그대로."""
    if isinstance(new, dict) and not new and isinstance(old, list):
        return []
    if isinstance(new, dict) and isinstance(old, dict):
        return {k: (keep_shape(v, old[k]) if k in old else v) for k, v in new.items()}
    if isinstance(new, list) and isinstance(old, list):
        return [(keep_shape(v, old[i]) if i < len(old) else v) for i, v in enumerate(new)]
    return new


WEATHER_NAME_MAX = 20     # [C121] lk.weather 이름 한 칸

_PAREN_RE = re.compile(r"^\s*(.+?)\s*[(（]\s*(.+?)\s*[)）]\s*$")


def _paren_parts(name: Any) -> List[str]:
    """`리나(Rina)` → [`리나`, `Rina`]. 괄호 꼴이 아니면 []. (memory_index.split_name_keys 와 같은 펴기 — 작업 프로세스는 봇 모듈을 안 끈다)"""
    m = _PAREN_RE.match(str(name or ""))
    return [m.group(1), m.group(2)] if m else []


class Session:
    """한 호출의 lk — 장부 사본 · 쓰기 모음 · 보고 모음 · 로그. DB 를 모른다.
    app = {"id", "settings"}(호스트가 넣는다) · kv = 지금 장부 · world = lk.world() · hist = lk.history 재료
    · tags = lk.tags 재료([{turn, tag, fields, evidence}] 오래된 → 최신, [M7b])."""

    def __init__(self, app: Dict[str, Any], kv: Dict[str, Any], world: Dict[str, Any],
                 history: List[Dict[str, Any]], tags: Optional[List[Dict[str, Any]]] = None,
                 values: Optional[Dict[str, Dict[str, Any]]] = None, apps: Optional[Dict[str, Dict[str, Any]]] = None,
                 roster: Optional[Dict[str, List[str]]] = None, core: Optional[Dict[str, Any]] = None):
        # [2026-11-06 C116 판2 다리] values = 선언 값 사본 {이름: {type, scope, mine, value, init, range}}(호스트가 넣는다 —
        #   mine = 이 앱 선언 칸(출처 app:<id>)) · apps = 다른 앱 장부 사본 {앱id: {키: 값}}(읽기만).
        self.app = app or {}
        self.kv = dict(kv or {})
        self.writes: Dict[str, Any] = {}
        self.kv_adds: Dict[str, Any] = {}   # [C117 A3] lk.add 가 더한 양(키 → 합) — 누르기 일지엔 이 양이 남는다
        self.kv_abs: set = set()            # 이 호출에서 lk.set 으로 쓴 키(그 뒤 lk.add 는 더한 양으로 안 남긴다)
        self.reports: List[str] = []
        self.logs: List[str] = []
        self.world = world or {}
        self.hist = list(history or [])
        self.tag_rows = [t for t in (tags or []) if isinstance(t, dict)]
        self.values: Dict[str, Dict[str, Any]] = {str(k): dict(v) for k, v in (values or {}).items() if isinstance(v, dict)}
        self.apps: Dict[str, Dict[str, Any]] = {str(k): dict(v) for k, v in (apps or {}).items() if isinstance(v, dict)}
        self.value_writes: List[Dict[str, Any]] = []
        self.time_req: Optional[Dict[str, Any]] = None   # [C116 판5] lk.time · lk.time_until — 한 호출 하나(마지막이 이긴다)
        self.weather_req: Optional[Dict[str, str]] = None   # [C121] lk.weather(now, next) — 한 호출 하나(마지막이 이긴다)
        # [C116 판6] 코어 읽기 재료 {relations: [{s, t, bond, tension, stance, kind}], threads: [...], clocks: [...], npcs: {이름: {...}}}
        self.core: Dict[str, Any] = dict(core or {})
        self.rel_writes: List[Dict[str, Any]] = []
        self.goal_writes: List[Dict[str, Any]] = []
        self.ask_req: Optional[Dict[str, Any]] = None   # [C116 판7] lk.ask — 한 호출 하나(누르기에서만 산다)
        self.handouts: List[Dict[str, Any]] = []        # [C116 판8] lk.handout — 다음 산문 앞 도착물(글은 앱이 쓴다)
        # [C116 판3] lk.npc 재료 — 등록 인물 {이름: [별칭]}(호스트가 넣는다)
        self.roster: Dict[str, List[str]] = {str(k): [str(a) for a in (v or []) if a] for k, v in (roster or {}).items()}

    @staticmethod
    def _key(k: Any) -> str:
        k = str(k if k is not None else "")
        if not k or len(k) > MAX_KEY_CHARS or k.startswith("_"):
            raise ValueError(f"lk: 쓸 수 없는 키 `{k[:20]}`(1~{MAX_KEY_CHARS}자, `_` 로 시작 금지)")
        return k

    # ---------------------------------------------------------------- [C116 판2] 선언 값 다리
    @staticmethod
    def _at(at: Any) -> Dict[str, str]:
        """at = 글자 하나 | 표 {npc, item, field, pc} → 칸 dict(빈 칸 뺌)."""
        if at is None:
            return {}
        if isinstance(at, dict):
            return {k: str(at[k])[:64] for k in ("npc", "item", "field", "pc") if at.get(k) not in (None, "")}
        t = str(at)[:64]
        return {"at": t} if t else {}

    def _vrec(self, name: Any) -> Tuple[str, Dict[str, Any]]:
        nm = str(name if name is not None else "")
        rec = self.values.get(nm)
        if rec is None:
            raise ValueError(f"lk.value: 선언된 값이 아니에요 `{nm[:20]}`")
        return nm, rec

    @staticmethod
    def _where(rec: Dict[str, Any], at: Dict[str, str]) -> Dict[str, str]:
        """글자 하나(at)를 값 모양에 맞춰 칸 이름으로 — 목록이면 item, 인물별이면 npc, 그 밖이면 pc."""
        if "at" not in at:
            return at
        t = at.pop("at")
        if str(rec.get("type")) == "list":
            at["item"] = t
        elif str(rec.get("scope")) == "npc":
            at["npc"] = t
        else:
            at["pc"] = t
        return at

    @staticmethod
    def _clamp(rec: Dict[str, Any], v: Any) -> Any:
        rg = rec.get("range")
        if isinstance(v, (int, float)) and not isinstance(v, bool) and isinstance(rg, (list, tuple)) and len(rg) == 2:
            try:
                return max(rg[0], min(rg[1], v))
            except TypeError:
                return v
        return v

    def _read(self, rec: Dict[str, Any], w: Dict[str, str]) -> Any:
        cur = rec.get("value")
        if str(rec.get("type")) == "list":
            if not w.get("item"):
                return cur
            row = (cur or {}).get(w["item"]) if isinstance(cur, dict) else None
            if w.get("field"):
                return row.get(w["field"]) if isinstance(row, dict) else None
            if isinstance(row, dict):
                return row if rec.get("fields") else row.get("n")
            return row
        key = w.get("npc") or w.get("pc") or (str(self.world.get("pc") or "") if rec.get("per_actor") else "")
        if key:
            # [2026-11-07 C120] 명단 밖 = nil · 안 움직인 사람 = 인물별 시작값(init_by)
            if w.get("npc") and not self._holder(rec, key):
                return None
            _ib = rec.get("init_by") if isinstance(rec.get("init_by"), dict) else {}
            _i0 = _ib.get(key, rec.get("init"))
            if isinstance(cur, dict):
                return cur.get(key, _i0)
            return _i0 if cur is None else cur
        if str(rec.get("scope")) == "npc" and (rec.get("holders") is not None or rec.get("init_by")):
            # [C120] 표 통째 = 명단 안 사람 전부(안 움직인 사람은 시작값). 명단 밖으로 나간 사람의 저장값은 안 보인다.
            per = cur if isinstance(cur, dict) else {}
            _ib = rec.get("init_by") if isinstance(rec.get("init_by"), dict) else {}
            names = rec["holders"] if rec.get("holders") is not None else list(dict.fromkeys(list(per) + list(_ib)))
            return {n: per.get(n, _ib.get(n, rec.get("init"))) for n in names}
        return cur

    @staticmethod
    def _holder(rec: Dict[str, Any], key: Any) -> bool:
        """[C120] 이 값의 명단 안인가 — 명단 칸이 없는 값(holders 없음)은 늘 안이다."""
        hs = rec.get("holders")
        if hs is None:
            return True
        k = str(key or "").strip().casefold()
        return any(str(h).strip().casefold() == k for h in hs)

    def _echo(self, rec: Dict[str, Any], w: Dict[str, str], op: str, x: Any) -> None:
        """같은 훅 안 읽기 — 쓴 값을 사본에 비춘다(범위 클램프만. 단계 맞춤 · 항목 상한은 호스트 몫)."""
        def nxt(old):
            if op == "set":
                return self._clamp(rec, x)
            try:
                return self._clamp(rec, (old if isinstance(old, (int, float)) and not isinstance(old, bool) else 0) + x)
            except TypeError:
                return old
        cur = rec.get("value")
        if str(rec.get("type")) == "list":
            items = dict(cur) if isinstance(cur, dict) else {}
            if not w.get("item"):
                return
            row = items.get(w["item"])
            row = dict(row) if isinstance(row, dict) else {"n": row if isinstance(row, (int, float)) else 0}
            k = w.get("field") or "n"
            row[k] = nxt(row.get(k))
            items[w["item"]] = row
            rec["value"] = items
            return
        key = w.get("npc") or w.get("pc")
        if key and (isinstance(cur, dict) or str(rec.get("scope")) == "npc"):
            per = dict(cur) if isinstance(cur, dict) else {}
            _ib = rec.get("init_by") if isinstance(rec.get("init_by"), dict) else {}
            per[key] = nxt(per.get(key, _ib.get(key, rec.get("init"))))
            rec["value"] = per
            return
        rec["value"] = nxt(cur if cur is not None else rec.get("init"))

    def _value_write(self, op: str, name: Any, x: Any, at: Any) -> None:
        nm, rec = self._vrec(name)
        if not (rec.get("mine") or rec.get("core")):
            raise ValueError(f"lk.value_{op}: 이 앱이 선언한 값 · 활력 · 평형 · 소지품만 써요 `{nm[:20]}`(읽기는 됨)")
        if len(self.value_writes) >= MAX_VALUE_WRITES:
            raise ValueError(f"lk.value_{op}: 한 번에 {MAX_VALUE_WRITES}건까지")
        vtype = str(rec.get("type") or "gauge")
        if op == "add":
            if vtype in ("enum", "text"):
                raise ValueError(f"lk.value_add: 단계형 · 문자열은 value_set 으로 `{nm[:20]}`")
            if isinstance(x, bool) or not isinstance(x, (int, float)) or x != x or x in (float("inf"), float("-inf")):
                raise ValueError("lk.value_add: 수만 더해요")
        else:
            try:
                json.dumps(x, ensure_ascii=False, allow_nan=False)
            except Exception:
                raise ValueError("lk.value_set: 글 · 수(NaN · 무한대 제외)만 담아요")
            if x is None or isinstance(x, (dict, list)):
                raise ValueError("lk.value_set: 표 · nil 은 못 써요(글 · 수만)")
        w = self._where(rec, self._at(at))
        if rec.get("per_actor") and not (w.get("pc") or w.get("npc")):
            w["pc"] = str(self.world.get("pc") or "")[:64]   # [C116 판6] 활력 · 평형 기본 = 이 훅의 PC
        if vtype == "list" and not w.get("item"):
            raise ValueError(f"lk.value_{op}: 목록 값은 항목을 줘요 `{nm[:20]}`")
        if w.get("npc") and not self._holder(rec, w["npc"]):
            # [2026-11-07 C120] 명단 밖 — 그 한 건만 버린다(훅 전체가 날아가지 않게)
            if len(self.logs) < 50:
                self.logs.append(f"lk.value_{op}: {str(w['npc'])[:20]} — `{nm[:20]}` 명단 밖이라 버림")
            return
        self.value_writes.append({"op": op, "name": nm, "x": x, **w})
        self._echo(rec, w, op, x)

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
            v = keep_shape(v, self.kv.get(k))   # [C117 C6] 빈 표는 그 키가 전에 가졌던 꼴(목록이었으면 [])
            if v is None:
                self.kv.pop(k, None)
            else:
                self.kv[k] = v
            self.writes[k] = v
            self.kv_abs.add(k)
            self.kv_adds.pop(k, None)
            return None

        def add(k=None, n=None):
            # [2026-11-06 C117 A3] 장부 수 칸 더하기 — 커밋은 쓴 뒤 값(lk.set 과 같음), 누르기 일지엔 더한 양이 남아
            #   `!다시` 에서 턴 훅 몫과 같이 산다(같은 키를 턴 훅 · 버튼이 같이 셀 때 — "한 키 한 주인"을 풀었다).
            k = self._key(k)
            if isinstance(n, bool) or not isinstance(n, (int, float)) or n != n or n in (float("inf"), float("-inf")):
                raise ValueError("lk.add: 수만 더해요")
            cur = self.kv.get(k)
            cur = 0 if cur is None else cur
            if isinstance(cur, bool) or not isinstance(cur, (int, float)):
                raise ValueError(f"lk.add: 수 칸이 아니에요 `{k[:20]}`")
            v = cur + n
            if v != v or v in (float("inf"), float("-inf")):
                raise ValueError("lk.add: 결과가 수가 아니에요")
            self.kv[k] = v
            self.writes[k] = v
            if k not in self.kv_abs:
                self.kv_adds[k] = self.kv_adds.get(k, 0) + n
            return v

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

        def value(name=None, at=None):
            nm, rec = self._vrec(name)
            return self._read(rec, self._where(rec, self._at(at)))

        def value_set(name=None, v=None, at=None):
            self._value_write("set", name, v, at)
            return None

        def value_add(name=None, d=None, at=None):
            self._value_write("add", name, d, at)
            return None

        def npc(name=None):
            # [C116 판3] 이름 풀기 — 신고 글 · 입력 속 이름 → 등록 인물 이름(없거나 둘 이상이면 nil)
            q = str(name if name is not None else "").strip()
            if not q:
                return None
            if q in self.roster:
                return q
            hit = [k for k, al in self.roster.items() if q in al]
            if len(hit) == 1:
                return hit[0]
            ql = q.casefold()
            hit = [k for k, al in self.roster.items() if ql == k.casefold() or ql in {a.casefold() for a in al}]
            if len(hit) == 1:
                return hit[0]
            # [2026-11-07 C120] 괄호 꼴 펴기 — `리나(Rina)` 는 `리나` · `Rina` 로도 풀린다(start_by · only 이름 맞추기와 같은 꼴)
            hit = [k for k in self.roster if ql in {x.casefold() for x in _paren_parts(k)}]
            if len(hit) == 1:
                return hit[0]
            hit = [k for k, al in self.roster.items()
                   if any(len(x) >= 2 and x.casefold() in ql for x in [k] + list(al) + _paren_parts(k))]
            return hit[0] if len(hit) == 1 else None

        def time_(n=None):
            # [C116 판5] 이 행동이 걸리는 시간(분) — on_input 이면 이번 턴 시계(선언 꼴), on_click 이면 바로
            try:
                n = int(n)
            except Exception:
                raise ValueError("lk.time: 분(정수)으로")
            if not 1 <= n <= 1440:
                raise ValueError("lk.time: 1~1440분")
            self.time_req = {"min": n}
            return None

        def weather(now=None, nxt=None):
            # [2026-11-07 C121] 코어 날씨 — 지금 이름(world.weather) · 다음 날 첫 이름(world.weather_next). 빈 글 · nil = 안 바꿈.
            #   날씨 주인 앱(manifest weather: true, 켜진 것 중 번호가 빠른 앱)만 적힌다 — 그 밖은 호스트가 버린다.
            out = {}
            for k_, v_ in (("now", now), ("next", nxt)):
                if v_ is None:
                    continue
                if not isinstance(v_, str):
                    raise ValueError("lk.weather: 글(날씨 이름)로 — 빈 글 · nil 은 안 바꿈")
                v_ = " ".join(v_.split())[:WEATHER_NAME_MAX]
                if v_:
                    out[k_] = v_
            self.weather_req = out or None
            return None

        def time_until(t=None):
            t = str(t if t is not None else "").strip()
            if not t:
                raise ValueError("lk.time_until: 시간대 이름(새벽 · 오전 …) 또는 \"HH:MM\"")
            self.time_req = {"until": t[:10]}
            return None

        # ------------------------------------------------ [C116 판6] 코어 읽기 · 관계 · 목표 쓰기
        def _edge(s_, t_):
            for e in self.core.get("relations") or []:
                if e.get("s") == s_ and e.get("t") == t_:
                    return e
            return None

        def relation(npc_=None, target=None):
            s_ = str(npc_ if npc_ is not None else "")
            t_ = str(target if target is not None else (self.world.get("pc") or ""))
            e = _edge(s_, t_)
            return None if e is None else {k: e.get(k) for k in ("bond", "tension", "stance", "kind")}

        def relations(npc_=None):
            s_ = None if npc_ is None else str(npc_)
            return [dict(e) for e in (self.core.get("relations") or []) if s_ is None or e.get("s") == s_]

        def threads(kind=None):
            k_ = None if kind is None else str(kind)
            return [dict(t) for t in (self.core.get("threads") or []) if k_ is None or t.get("kind") == k_]

        def clocks():
            return [dict(c) for c in (self.core.get("clocks") or [])]

        def npc_info(name_=None):
            r = (self.core.get("npcs") or {}).get(str(name_ if name_ is not None else ""))
            return dict(r) if isinstance(r, dict) else None

        def _core_slot():
            if len(self.rel_writes) + len(self.goal_writes) >= MAX_CORE_WRITES:
                raise ValueError(f"lk: 관계 · 목표 쓰기는 한 번에 {MAX_CORE_WRITES}건까지")

        def _rel_word(x, axis):
            # [C117 C2] 말(much_warmer … · spikes …) → 폭. 표는 호스트가 config 에서 싣는다(없으면 같은 값의 받침).
            if isinstance(x, str):
                tb = ((self.core.get("rel_words") or {}).get(axis)) or REL_WORDS_FALLBACK[axis]
                w = x.strip().lower()
                if w not in tb:
                    raise ValueError(f"lk.relation_shift: {axis} 말은 {' · '.join(tb)}")
                return int(tb[w])
            return int(x or 0)

        def relation_shift(npc_=None, bond=0, tension=0):
            s_ = str(npc_ if npc_ is not None else "").strip()
            if not s_:
                raise ValueError("lk.relation_shift: 인물 이름을 줘요")
            try:
                b_, t_ = _rel_word(bond, "bond"), _rel_word(tension, "tension")
            except ValueError:
                raise
            except Exception:
                raise ValueError("lk.relation_shift: 정수 또는 말로")
            if abs(b_) > REL_SHIFT_MAX or abs(t_) > REL_SHIFT_MAX:
                raise ValueError(f"lk.relation_shift: 한 번에 ±{REL_SHIFT_MAX}까지")
            if not b_ and not t_:
                return None
            _core_slot()
            self.rel_writes.append({"npc": s_[:64], "bond": b_, "tension": t_})
            e = _edge(s_, str(self.world.get("pc") or ""))   # 같은 호출 안 비침(범위 −100~100 · 0~100)
            if e is not None:
                e["bond"] = max(-100, min(100, int(e.get("bond") or 0) + b_))
                e["tension"] = max(0, min(100, int(e.get("tension") or 0) + t_))
            return None

        def goal_step(tid=None, steps=1):
            t_ = str(tid if tid is not None else "").strip()
            try:
                n_ = int(steps)
            except Exception:
                raise ValueError("lk.goal_step: 정수로")
            if not t_ or not n_ or abs(n_) > 3:
                raise ValueError("lk.goal_step: 목표 id(T3) · 걸음 ±1~3")
            t_ = t_.upper()
            if not any(t.get("id") == t_ and t.get("kind") == "goal" and t.get("status") == "open"
                       for t in (self.core.get("threads") or [])):
                raise ValueError(f"lk.goal_step: 열린 목표가 아니에요 `{t_[:12]}`")
            _core_slot()
            self.goal_writes.append({"id": t_[:12], "steps": n_})
            for t in (self.core.get("threads") or []):   # [C117 B2] 같은 호출 안 비침(0 ~ max)
                if t.get("id") == t_:
                    try:
                        t["progress"] = max(0, min(int(t.get("max") or 0) or 99, int(t.get("progress") or 0) + n_))
                    except Exception:
                        pass
            return None

        def place():
            # [C117 C1] 장소 나무 — {name, type, path(뿌리 → 여기), tags, risk, near}. 못 풀면 {name}.
            p = self.core.get("place")
            if isinstance(p, dict) and p.get("name"):
                return dict(p)
            return {"name": str(self.world.get("place") or "")}

        # ------------------------------------------------ [C116 판7] 모델에게 묻기 — 답은 on_answer 로
        def ask(qid=None, spec=None):
            q_ = str(qid if qid is not None else "").strip()
            if not q_ or len(q_) > 64:
                raise ValueError("lk.ask: 물음 id(1~64자)를 줘요")
            if self.ask_req is not None:
                raise ValueError("lk.ask: 한 호출에 하나")
            sp = spec if isinstance(spec, dict) else ({"task": spec} if isinstance(spec, str) else {})
            task = str(sp.get("task") or "").strip()
            if not task:
                raise ValueError("lk.ask: task(무엇을 쓸지)를 줘요")
            if len(task) > ASK_TASK_MAX:
                raise ValueError(f"lk.ask: task 는 {ASK_TASK_MAX}자까지")
            mat = sp.get("material")
            mat = "" if mat is None else (mat if isinstance(mat, str) else json.dumps(mat, ensure_ascii=False))
            if len(mat) > ASK_MATERIAL_MAX:
                raise ValueError(f"lk.ask: material 은 {ASK_MATERIAL_MAX}자까지")
            try:
                n_ = int(sp.get("max_chars") or ASK_CHARS_DEFAULT)
            except Exception:
                raise ValueError("lk.ask: max_chars 는 정수")
            self.ask_req = {"id": q_, "task": task, "material": mat, "max_chars": max(1, min(ASK_CHARS_MAX, n_)),
                            "json": bool(sp.get("json"))}
            return None

        # ------------------------------------------------ [C116 판8] 도착물 요청 — 글은 앱, 보이기 · 기록은 봇(world_board 길)
        def handout(spec=None):
            sp = spec if isinstance(spec, dict) else {}
            kind = str(sp.get("kind") or "message").strip().lower()
            if kind not in HANDOUT_KINDS:
                raise ValueError("lk.handout: kind 는 message · bulletin · sns")
            body = str(sp.get("body") or "").strip()
            if not body:
                raise ValueError("lk.handout: body(본문)를 줘요")
            if len(body) > HANDOUT_BODY_MAX:
                raise ValueError(f"lk.handout: body 는 {HANDOUT_BODY_MAX}자까지")
            if len(self.handouts) >= MAX_HANDOUTS_PER_CALL:
                raise ValueError(f"lk.handout: 한 호출 {MAX_HANDOUTS_PER_CALL}장까지")
            h = {"kind": kind, "body": body}
            for k, cap in (("author", 200), ("to", 200), ("title", 250), ("format", 200)):
                v = sp.get(k)
                if v is not None and str(v).strip():
                    h[k] = str(v).strip()[:cap]
            self.handouts.append(h)
            return None

        def app_get(app_id=None, k=None):
            k = str(k if k is not None else "")
            if not k or k.startswith("_"):
                return None
            return (self.apps.get(str(app_id if app_id is not None else "")) or {}).get(k)

        return {"get": get, "set": set_, "add": add, "keys": keys, "setting": setting, "report": report, "world": world,
                "history": history, "tags": tags, "rand": rand, "json_encode": json_encode, "json_decode": json_decode,
                "log": log, "value": value, "value_set": value_set, "value_add": value_add, "app_get": app_get, "npc": npc,
                "time": time_, "time_until": time_until, "weather": weather,
                "relation": relation, "relations": relations, "threads": threads, "clocks": clocks, "npc_info": npc_info,
                "relation_shift": relation_shift, "goal_step": goal_step, "ask": ask,
                "handout": handout, "place": place}


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
        L, tramp = _load_full(source, api, data, steps, memory)
        fn = L.globals()[hook]
        if _lua.lua_type(fn) != "function":
            return False, None
        res = tramp(fn, *[_to_lua(L, a) for a in args])
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
                   req.get("tags") or [], values=req.get("values") or {}, apps=req.get("apps") or {},
                   roster=req.get("roster") or {}, core=req.get("core") or {})
    try:
        ran, res = call(source, str(req.get("hook") or ""), tuple(req.get("args") or ()), sess.api(), None, steps, memory)
    except LuaFail as e:
        return {"ok": False, "error": str(e), "logs": sess.logs}
    return {"ok": True, "ran": ran, "result": res, "writes": sess.writes, "reports": sess.reports, "logs": sess.logs,
            "value_writes": sess.value_writes, "time": sess.time_req, "weather": sess.weather_req,
            "rel_writes": sess.rel_writes, "goal_writes": sess.goal_writes, "ask": sess.ask_req,
            "handouts": sess.handouts, "kv_adds": sess.kv_adds,
            # [C117 B2] 쓴 값 이름의 쓴 뒤 값(같은 정산의 다음 훅 — on_turn_end — 이 본다)
            "values_after": {nm: (sess.values.get(nm) or {}).get("value")
                             for nm in {str(w.get("name")) for w in sess.value_writes}}}


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
