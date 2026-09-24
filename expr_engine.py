# -*- coding: utf-8 -*-
"""
expr_engine — 표현식 문법·실행 순서·파생값·조건 전이·알림  [2026-09-06 P3]

정본 스펙: `파티쳇수정/output_artifact/output_artifact_spec_2026-09-06.md` §3.4 · §3.5 · ④-1 · §0.6 · §1-9 · §1-10
지시서:   `파티쳇수정/output_artifact/p3_expr_engine_instruction_2026-09-06.md`

한 문장: **식은 LLM 이 쓰고, 유저가 승인하고, 코드가 돈다.**

이 모듈이 지키는 것 넷:
  1. **`eval`/`exec` 호출 0.** `ast.parse` 로 트리만 얻고, 노드 화이트리스트 워커가 직접
     계산한다. 금지 노드는 "막혀 있는" 게 아니라 **존재 자체가 등록 거부**다 — 속성접근·
     람다·import·컴프리헨션·조건문·반복은 문법에 자리가 없다.
  2. **식은 값 계산만.** 언제(when·cadence)·몇 번(once)·판정(check)·실패(on_fail)·알림·
     기록은 전부 식 **밖** 고정 필드다. 제어 흐름이 식 안으로 들어가는 순간 이 설계가
     피하려던 그 스크립트가 된다.
  3. **실행 순서 고정** — ② derive → ④ operations → ⑤ transitions → ⑥ derive.
     재현성이 이 줄 하나에 걸려 있다. 주석의 ②④⑤⑥ 번호를 지우지 마라.
  4. **검출 ≠ 쓰기.** 발화 사실은 산문에 `<사건>` 한 줄로 **재료**가 되지 지시가 되지 않고,
     값 쓰기는 전부 `source="expr"` · `evidence="expr:<전이명>"` 도장을 남긴다.

⚠ 이 모듈은 discord 를 import 하지 않는다(스모크가 스텁 없이 돈다).
"""

import ast
import logging
import math
import random
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

import config
import domain_manager

logger = logging.getLogger("ExprEngine")


class ExprError(Exception):
    """문법 밖 / 이름 없음 / 계산 실패. 등록 거부 사유는 이 두 부류만이다(§3.4)."""


# =========================================================
# 값 층 키 (world_state — !클리어 에 함께 소멸. §4-7)
# =========================================================
TRANSITION_STATE_KEY = "transition_state"
PENDING_MAILS_KEY = "pending_mails"
# [2026-09-13 P14] 도착물 선언의 종류. 💌 개인(letter) / 📰 공개(bulletin·sns).
#   `notify` 와 다른 축이다 — notify 는 "알릴까"(표시), deliver 는 "무엇이 도착하는가"(생산).
DELIVER_KINDS = ("letter", "bulletin", "sns")
PENDING_EVENTS_KEY = "pending_events"
# [2026-09-06 P4/P5] 추출 신고 대기열. 추출은 **배경**(턴 N 렌더 뒤)이므로 신고는 턴 N+1 의
#   4.7 에서 소비된다 — 서술 조건·연산은 한 턴 뒤 집행이다(스펙 §0.7 e 수용).
PENDING_CUE_HITS_KEY = "pending_cue_hits"
PENDING_OP_HITS_KEY = "pending_operation_hits"

# 선언 층 키 (output_decl — 도메인 루트, 클리어 생존. P1 이 판 자리 옆)
DECL_DERIVES = "derives"
DECL_TRANSITIONS = "transitions"
# [2026-09-13 P16] 조건부 지시 — `{when, text}` 한 쌍. 값을 **쓰지 않는다**(프롬프트 재료다).
DECL_DIRECTIVES = "directives"
DIRECTIVE_TEXT_MAX = 300

# 코드 소유 **읽기 전용** 이름 — 대입하면 등록 거부(§3.4 "코드 소유 필드에 대입은 등록 거부").
# [2026-09-09 P11] `날씨` — world_state["weather"](game_world 가 굴린다). 읽기 전용이고
#   문자열이라 `날씨 == "맑음"` 비교로만 쓴다(문자열 Constant 는 비교 우변에만 선다).
CODE_READONLY = ("기력", "평형", "시각", "일", "시간대", "턴", "날씨", "날")
# 재고 = P0 노트북 [소지품](유저 스코프). custom_vars stock 이관은 P8.
STOCK_NAME = "재고"
# [2026-09-16 3차 §10.2] 조각 = 행위자 PC 의 시트 조각(ai_memory.passives) 이름 컬렉션. **읽기 전용** —
#   `has(조각, "이름")` 만 뜻이 있다. 대입·첨자 쓰기·등록 시 쓰기 대상은 전부 거부.
FRAGMENT_NAME = "조각"

NOTIFY_KINDS = ("mail", "mind", "none")
# [2026-09-09 P11] 전이가 **언제** 재어지는가. turn = 매턴 ⑤ / day = 경계 틱 1회.
CADENCE_KINDS = ("turn", "day")
CHECK_KINDS = ("judgment", "none")
# 전이가 무엇에 의해 열리는가. P3 는 when/narrated_cue 로 **암묵**이었고 P4 가 필드로 닫는다.
TRIGGER_KINDS = ("expr", "narrated", "operation")
# cue 급식 게이트 — cue 문장의 낱말이 이번 턴 텍스트에 **몇 개** 겹치면 후보인가.
#   1이면 흔한 조사·부사 하나로 매턴 급식이 서고, 3이면 짧은 cue 가 영영 안 선다.
CUE_MATCH_MIN = 2
FEED_MODES = ("always", "mentioned", "never")

# [2026-09-13 P17 후속] 파생·전이 상한 — `custom_vars.MAX_VARS = 80` 과 **같은 저울**로 맞춘다.
#   근거는 SimCore 실측(P17 §7): 내장 16종 중 idol 한 장이 파생 55 · 전이 44 를 쓴다.
#   12/24 는 그 아래여서 아틀리에급 한 장이 55번째 파생에서 거부됐다 — MAX_VARS 를 80 으로
#   올릴 때와 같은 판단이다. 프롬프트 비대를 막는 관문은 급식 캡(config.PROSE_FEED_MAX)이지
#   선언 상한이 아니다: 여기 상한은 **저장의 상한**이고, 저장이 막히면 유저의 한 장이 통째로 깨진다.
MAX_DERIVES = 60          # 파생값도 선언이다 — custom_vars.MAX_VARS 와 같은 급의 상한.
MAX_TRANSITIONS = 60
EXPR_SRC_MAX = 400        # 식 원문. 되비침 한 줄에 실려야 하므로 문단이 아니다.
# [2026-09-13 P17] `a if c else b` 중첩 깊이. 4 면 "네 갈래"까지다 — 그보다 깊은 분기는
#   식이 아니라 표(stages)여야 하고, 깊이를 안 물면 한 줄이 파서·되비침·사람의 눈을
#   동시에 넘어선다(EXPR_SRC_MAX 는 길이만 물지 구조를 못 문다).
IFEXP_DEPTH_MAX = 4


# =========================================================
# 1. 파서 — 노드 화이트리스트 워커
# =========================================================
# ★"허용 밖 노드 하나라도 있으면 파싱 실패 = 등록 거부"(§3.4). 검사는 **리터럴이 아니라
#   노드 타입**으로 한다 — 소스 문자열에서 `import` 를 찾는 식이면 우회가 문자열 장난 하나다.

_EXPR_NODES = (
    ast.Expression, ast.Module, ast.Expr,
    ast.Assign, ast.AugAssign,
    ast.BinOp, ast.UnaryOp, ast.BoolOp, ast.Compare,
    ast.Name, ast.Constant, ast.Subscript, ast.Call,
    ast.IfExp,
    ast.Load, ast.Store,
    # 연산자 노드(잎)
    ast.Add, ast.Sub, ast.Mult, ast.Div, ast.FloorDiv, ast.Mod,
    ast.USub, ast.UAdd, ast.Not,
    ast.And, ast.Or,
    ast.Lt, ast.LtE, ast.Gt, ast.GtE, ast.Eq, ast.NotEq,
)
_AUG_OPS = (ast.Add, ast.Sub, ast.Mult)


def _node_label(node: ast.AST) -> str:
    return type(node).__name__


def _reject(node: ast.AST) -> None:
    raise ExprError(f"문법 밖: {_node_label(node)}")


class Compiled:
    """컴파일 결과. 등록 때 한 번 만들고 **매턴 재파싱하지 않는다**(④-1).

    names  — 참조 이름 집합(Name 과 Subscript 밑동). 미선언·순환 검사가 이걸 본다.
    writes — 대입 대상 집합. 쓰기 적법성 검사가 이걸 본다.
    bare   — 그중 **첨자 없이** 대입된 이름. [2026-09-06 P8c] 쓰기 적법성이 이름만으로는
             안 갈리는 자리가 생겼다: `기력[리나] -= 10`(인물 값 — 허용)과 `기력 = 0`(PC 총량
             대입 — 코드 소유라 거부)은 writes 에 같은 이름을 남긴다. 첨자 유무를 여기서 들고
             있어야 등록 시점에 그 둘이 갈린다.
    """

    __slots__ = ("src", "mode", "tree", "names", "writes", "bare")

    def __init__(self, src: str, mode: str, tree: ast.AST,
                 names: Set[str], writes: Set[str],
                 bare: Optional[Set[str]] = None) -> None:
        self.src = src
        self.mode = mode
        self.tree = tree
        self.names = names
        self.writes = writes
        self.bare = set(writes) if bare is None else set(bare)

    def __repr__(self) -> str:
        return f"<Compiled {self.mode} {self.src!r}>"


def compile_expr(src: Any, mode: str = "eval") -> Compiled:
    """`ast.parse` → 화이트리스트 워커. 통과한 것만 Compiled 로 나간다.

    mode="eval" — 값을 내는 식(when). 대입은 여기서 문법 밖이다.
    mode="exec" — 문장(do·on_fail). `;` 와 줄바꿈이 문 구분.
    """
    text = str(src or "").strip()
    if not text:
        raise ExprError("문법 밖: 빈 식")
    if len(text) > EXPR_SRC_MAX:
        raise ExprError(f"문법 밖: 식이 깁니다 ({len(text)}자 > {EXPR_SRC_MAX}자)")
    if mode not in ("eval", "exec"):
        raise ExprError(f"문법 밖: 모르는 모드 {mode!r}")
    try:
        tree = ast.parse(text, mode=mode)
    except SyntaxError as e:
        raise ExprError(f"문법 밖: 파싱 실패 ({e.msg})") from e

    names: Set[str] = set()
    writes: Set[str] = set()
    bare: Set[str] = set()
    _walk(tree, names, writes, mode, bare=bare)
    _d = _ifexp_depth(tree)
    if _d > IFEXP_DEPTH_MAX:
        raise ExprError(f"문법 밖: 조건식 중첩이 깊습니다 ({_d} > {IFEXP_DEPTH_MAX}단). "
                        "그건 식이 아니라 단계표(stages)입니다")
    return Compiled(text, mode, tree, names, writes, bare)


def _ifexp_depth(node: ast.AST) -> int:
    """`a if c else b` 의 **중첩 깊이**. 한 줄에 몇 갈래가 겹쳐 있나."""
    best = 0
    for ch in ast.iter_child_nodes(node):
        d = _ifexp_depth(ch)
        if d > best:
            best = d
    return best + 1 if isinstance(node, ast.IfExp) else best


def _sub_key(node: ast.Subscript) -> str:
    """첨자 키 — 문자열 **리터럴**. `이름[리나]` 와 `이름["리나"]` 둘 다 같은 키다.

    ★§0 불일치 1: 스펙 §3.4 의 노드표는 `Subscript(Name, Constant)` 라고 적었지만 같은 표의
      **예시가 `호감도[리나]`** 다. 파이썬 문법에서 그건 `Name` 노드지 `Constant` 가 아니다.
      예시가 유저가 실제로 쓰는 표기이므로 여기서 맨이름을 **키 리터럴로** 받는다 —
      변수 조회가 아니다(첨자 자리의 이름은 값으로 풀리지 않는다). 슬라이스·이중 첨자·
      식 첨자는 여전히 문법 밖이다.
    """
    sl = node.slice
    # py3.8 호환: Index 래퍼가 있으면 벗긴다(3.9+ 는 바로 Constant/Name).
    sl = getattr(sl, "value", sl) if type(sl).__name__ == "Index" else sl
    if isinstance(sl, ast.Name):
        return str(sl.id)
    if isinstance(sl, ast.Constant) and isinstance(sl.value, str):
        return str(sl.value)
    _reject(node.slice)


def _sub_base(node: ast.Subscript) -> str:
    return _sub_path(node)[0]


def _sub_path(node: ast.Subscript) -> Tuple[str, str, str]:
    """첨자 경로 → `(밑동, 키, 필드)`. 필드가 빈 문자열이면 한 단이다.

    ★[2026-09-09 P11] 두 단은 **Subscript 두 겹**(`화분["양파"]["수분"]`)으로 연다.
      후보는 둘이었다 — 속성 표기(`화분["양파"].수분`)는 `ast.Attribute` 를 화이트리스트에
      **새로 들이는** 일이라, 이 모듈이 첫 줄에 못 박은 "속성접근은 문법에 자리가 없다"가
      거짓이 된다(그리고 노드 하나가 열리면 `.__class__` 같은 자리까지 워커가 손으로 막아야
      한다). 두 겹 첨자는 새 노드 타입이 0 이고 `_walk` 의 Subscript 가지 한 줄만 바뀐다 —
      `_walk` 부담이 작은 쪽이 이쪽이다. 세 단은 여전히 문법 밖이다.
    """
    inner = node.value
    if isinstance(inner, ast.Subscript):
        if not isinstance(inner.value, ast.Name):
            _reject(inner.value)
        return inner.value.id, _sub_key(inner), _sub_key(node)
    if not isinstance(inner, ast.Name):
        _reject(inner)
    return inner.id, _sub_key(node), ""


def _walk(node: ast.AST, names: Set[str], writes: Set[str], mode: str,
          store: bool = False, bare: Optional[Set[str]] = None) -> None:
    """노드 하나를 검사하고 자식으로 내려간다. 허용표 밖이면 그 자리에서 ExprError."""
    if not isinstance(node, _EXPR_NODES):
        _reject(node)

    if isinstance(node, ast.Expression):
        _walk(node.body, names, writes, mode)
        return

    if isinstance(node, ast.Module):
        if mode != "exec":
            _reject(node)
        if not node.body:
            raise ExprError("문법 밖: 빈 문장")
        for st in node.body:
            _walk(st, names, writes, mode, bare=bare)
        return

    if isinstance(node, ast.Expr):
        _walk(node.value, names, writes, mode)
        return

    if isinstance(node, ast.Assign):
        if mode != "exec":
            _reject(node)
        if len(node.targets) != 1:
            _reject(node)
        _walk_target(node.targets[0], names, writes, bare=bare)
        _walk(node.value, names, writes, mode)
        return

    if isinstance(node, ast.AugAssign):
        if mode != "exec":
            _reject(node)
        if not isinstance(node.op, _AUG_OPS):
            _reject(node.op)
        # AugAssign 대상은 **읽기이기도 하다** — names 에도 든다(미선언 검사가 걸러야 한다).
        _walk_target(node.target, names, writes, also_read=True, bare=bare)
        _walk(node.value, names, writes, mode)
        return

    if isinstance(node, ast.Name):
        if not isinstance(node.ctx, ast.Load):
            _reject(node)
        names.add(node.id)
        return

    if isinstance(node, ast.Constant):
        if not isinstance(node.value, (int, float, str, bool)):
            _reject(node)
        return

    if isinstance(node, ast.Subscript):
        if not isinstance(node.ctx, ast.Load):
            _reject(node)
        base, _k, _f = _sub_path(node)
        names.add(base)
        return

    if isinstance(node, ast.IfExp):
        # [2026-09-13 P17] `a if c else b`. 조건은 참·거짓, 양 갈래는 값이다.
        #   깊이 상한은 컴파일 끝에서 한 번 잰다(_ifexp_depth) — 여기선 잎만 걷는다.
        _walk(node.test, names, writes, mode)
        _walk(node.body, names, writes, mode)
        _walk(node.orelse, names, writes, mode)
        return

    if isinstance(node, ast.Call):
        if not isinstance(node.func, ast.Name):
            _reject(node.func)
        if node.func.id not in FUNCS:
            raise ExprError(f"문법 밖: 허용되지 않은 함수 {node.func.id}()")
        if getattr(node, "keywords", None):
            _reject(node)
        if node.func.id in EFFECT_FUNCS:
            # 부수효과 = 쓰기다. eval(when)에서는 문법 밖이고, exec 에서도 첫 인자 목록 이름을
            #   writes 에 남긴다 — 안 남기면 파생값·코드 소유 이름에 each 로 몰래 쓸 수 있다.
            if mode != "exec":
                raise ExprError(f"문법 밖: {node.func.id}() 은 조건식에 쓸 수 없습니다")
            if not node.args or not isinstance(node.args[0], ast.Name):
                raise ExprError(f"문법 밖: {node.func.id}() 의 첫 인자는 목록 이름이어야 합니다")
            writes.add(node.args[0].id)
            names.add(node.args[0].id)
            # [2026-09-24 감사] 인자 1개(`each(화분)`)는 홀수라 통과했다가 실행 때 args[1] 에서
            #   IndexError — 등록 시 거부한다(최소 3개: 목록·필드·델타).
            if node.func.id == "each" and (len(node.args) < 3 or len(node.args) % 2 == 0):
                raise ExprError('문법 밖: each(목록, "필드", 델타[, "조건필드", 최소]…)')
            if node.func.id != "each" and len(node.args) != 2:
                raise ExprError(f'문법 밖: {node.func.id}(목록, "항목")')
        for a in node.args:
            if isinstance(a, ast.Starred):
                _reject(a)
            _walk(a, names, writes, mode)
        return

    if isinstance(node, ast.BinOp):
        _walk(node.op, names, writes, mode)
        _walk(node.left, names, writes, mode)
        _walk(node.right, names, writes, mode)
        return

    if isinstance(node, ast.UnaryOp):
        _walk(node.op, names, writes, mode)
        _walk(node.operand, names, writes, mode)
        return

    if isinstance(node, ast.BoolOp):
        _walk(node.op, names, writes, mode)
        for v in node.values:
            _walk(v, names, writes, mode)
        return

    if isinstance(node, ast.Compare):
        for o in node.ops:
            _walk(o, names, writes, mode)
        _walk(node.left, names, writes, mode)
        for c in node.comparators:
            _walk(c, names, writes, mode)
        return

    # 연산자 잎(Add/Sub/…): 허용표를 이미 지났으므로 통과.
    return


def _walk_target(node: ast.AST, names: Set[str], writes: Set[str],
                 also_read: bool = False, bare: Optional[Set[str]] = None) -> None:
    """대입 대상 — `Name` 또는 `Subscript(Name, Constant str)` 만."""
    if isinstance(node, ast.Name):
        if not isinstance(node.ctx, ast.Store):
            _reject(node)
        writes.add(node.id)
        if bare is not None:
            bare.add(node.id)          # 첨자 없는 대입 — 이름 하나가 값 전체를 가리킨다
        if also_read:
            names.add(node.id)
        return
    if isinstance(node, ast.Subscript):
        if not isinstance(node.ctx, ast.Store):
            _reject(node)
        base, _k, _f = _sub_path(node)
        writes.add(base)
        if also_read:
            names.add(base)
        return
    _reject(node)


# =========================================================
# 2. 함수표 — 닫힘. 여기 없는 이름은 노드 검사에서 이미 죽는다.
# =========================================================
# ★`rand` 는 §7-6 의 열린 결정이었다: 허용하되 **시드를 turn_index + 식 해시로 고정**해
#   재현 가능하게 둔다. 같은 턴에 같은 식을 두 번 재면 같은 값이 나온다 — `!다시` 가
#   스냅샷을 되돌린 뒤 재실행해도 같은 사건이 일어난다는 뜻이고, 그게 "코드가 전이를
#   발명하지 않는다"(08-18)의 실물이다.

FUNCS: Tuple[str, ...] = (
    "min", "max", "abs", "floor", "round", "rand", "has", "count",
    "day", "slot", "turn",
    # [2026-09-09 P11] 일괄·생멸. 값을 내지 않고 **값을 바꾼다** — 그래서 exec 전용이다.
    "each", "add", "remove",
    # [2026-09-13 P17] `sum(목록)` = 수량 합(값을 낸다) · `expire(목록,"필드")` = 기한이 지난
    #   항목 제거(값을 바꾼다 → EFFECT_FUNCS).
    "sum", "expire",
)

# 부수효과 함수. `when`(eval)에 서면 조건을 재는 자리가 값을 바꾼다 — 등록 거부다.
#   `each(목록, "필드", 델타[, "조건필드", 최소]…)` — 조건쌍은 AND, 전부 `>=` 비교다.
#   루프가 아니다: 항목을 세는 일은 코드가 하고 식은 **무엇을 얼마나**만 말한다.
EFFECT_FUNCS: Tuple[str, ...] = ("each", "add", "remove", "expire")


def _as_num(v: Any, where: str) -> float:
    if isinstance(v, bool):
        return 1.0 if v else 0.0
    if isinstance(v, (int, float)):
        return float(v)
    raise ExprError(f"수가 아닙니다: {where} = {v!r}")


def _items_of(v: Any) -> Dict[str, Any]:
    """list 변수·재고의 항목 dict. 값 모양이 `{n, goal}` 든 정수든 같은 문법으로 읽는다."""
    if isinstance(v, dict):
        return v
    raise ExprError(f"목록이 아닙니다: {v!r}")


def _item_n(rec: Any) -> int:
    if isinstance(rec, dict):
        rec = rec.get("n", rec.get("qty", 0))
    try:
        return int(rec or 0)
    except (TypeError, ValueError):
        return 0


class _Evaluator:
    """트리 워커. **`eval` 을 부르지 않는다** — 노드마다 손으로 계산한다."""

    def __init__(self, resolver: Optional["Resolver"] = None, turn: int = 0,
                 src: str = "") -> None:
        self.r = resolver
        self.turn = int(turn if resolver is None else resolver.turn_index())
        self._rng: Optional[random.Random] = None
        self._src = src

    # --- rand: 시드 = 턴 + 식 원문(재현 가능) ---
    def rng(self) -> random.Random:
        if self._rng is None:
            self._rng = random.Random(f"{self.turn}|{self._src}")
        return self._rng

    # --- 읽기 ---
    def read_name(self, name: str) -> Any:
        if self.r is None:
            raise ExprError(f"이름 없음: {name}")
        return self.r.read(name)

    def read_sub(self, base: str, key: str, field: str = "") -> Any:
        if self.r is None:
            raise ExprError(f"이름 없음: {base}[{key}]")
        return self.r.read_sub(base, key, field)

    # --- 표현식 ---
    def ev(self, node: ast.AST) -> Any:
        if isinstance(node, ast.Constant):
            return node.value
        if isinstance(node, ast.Name):
            return self.read_name(node.id)
        if isinstance(node, ast.Subscript):
            return self.read_sub(*_sub_path(node))
        if isinstance(node, ast.BinOp):
            return self._binop(node)
        if isinstance(node, ast.UnaryOp):
            if isinstance(node.op, ast.Not):
                return not _truth(self.ev(node.operand))
            v = _as_num(self.ev(node.operand), "단항")
            return -v if isinstance(node.op, ast.USub) else v
        if isinstance(node, ast.BoolOp):
            if isinstance(node.op, ast.And):
                out: Any = True
                for v in node.values:
                    out = self.ev(v)
                    if not _truth(out):
                        return False
                return True
            for v in node.values:
                if _truth(self.ev(v)):
                    return True
            return False
        if isinstance(node, ast.Compare):
            return self._compare(node)
        if isinstance(node, ast.IfExp):
            return self.ev(node.body) if _truth(self.ev(node.test)) else self.ev(node.orelse)
        if isinstance(node, ast.Call):
            return self._call(node)
        _reject(node)

    def _binop(self, node: ast.BinOp) -> Any:
        left, right = self.ev(node.left), self.ev(node.right)
        # 문자열 잇기는 하지 않는다 — 식은 값 계산만이고, 값은 수다.
        a, b = _as_num(left, "좌항"), _as_num(right, "우항")
        op = node.op
        if isinstance(op, ast.Add):
            r = a + b
        elif isinstance(op, ast.Sub):
            r = a - b
        elif isinstance(op, ast.Mult):
            r = a * b
        elif isinstance(op, (ast.Div, ast.FloorDiv, ast.Mod)):
            if b == 0:
                raise ExprError("0 으로 나눌 수 없습니다")
            r = a / b if isinstance(op, ast.Div) else (a // b if isinstance(op, ast.FloorDiv) else a % b)
        else:
            _reject(op)
        return _tidy(r)

    def _compare(self, node: ast.Compare) -> bool:
        left = self.ev(node.left)
        for op, comp_node in zip(node.ops, node.comparators):
            right = self.ev(comp_node)
            if isinstance(op, ast.Eq):
                res = _eq(left, right)
            elif isinstance(op, ast.NotEq):
                res = not _eq(left, right)
            else:
                a, b = _as_num(left, "좌항"), _as_num(right, "우항")
                if isinstance(op, ast.Lt):
                    res = a < b
                elif isinstance(op, ast.LtE):
                    res = a <= b
                elif isinstance(op, ast.Gt):
                    res = a > b
                elif isinstance(op, ast.GtE):
                    res = a >= b
                else:
                    _reject(op)
            if not res:
                return False
            left = right
        return True

    def _call(self, node: ast.Call) -> Any:
        fn = node.func.id            # 노드 검사에서 Name·FUNCS 를 이미 확인했다
        args = node.args
        if fn in ("day", "slot", "turn"):
            if args:
                raise ExprError(f"문법 밖: {fn}() 은 인자를 받지 않습니다")
            if self.r is None:
                raise ExprError(f"이름 없음: {fn}()")
            return {"day": self.r.read("일"), "slot": self.r.read("시간대"),
                    "turn": self.r.read("턴")}[fn]
        if fn in ("has", "count"):
            if not args or not isinstance(args[0], ast.Name):
                raise ExprError(f"문법 밖: {fn}() 의 첫 인자는 목록 이름이어야 합니다")
            items = _items_of(self.read_name(args[0].id))
            if fn == "count":
                if len(args) != 1:
                    raise ExprError("문법 밖: count(목록)")
                return len([k for k in items if _item_n(items[k]) > 0])
            if len(args) != 2:
                raise ExprError('문법 밖: has(목록, "항목")')
            key = self.ev(args[1])
            return any(str(k) == str(key) and _item_n(v) > 0 for k, v in items.items())
        if fn == "sum":
            if len(args) != 1 or not isinstance(args[0], ast.Name):
                raise ExprError("문법 밖: sum(목록)")
            items = _items_of(self.read_name(args[0].id))
            return _tidy(sum(_item_n(v) for v in items.values()))
        if fn in EFFECT_FUNCS:
            if self.r is None:
                raise ExprError(f"이름 없음: {fn}()")
            listname = args[0].id
            if fn == "expire":
                return self.r.expire(listname, str(self.ev(args[1])))
            if fn in ("add", "remove"):
                return self.r.item_op(listname, str(self.ev(args[1])), fn)
            field = str(self.ev(args[1]))
            delta = _as_num(self.ev(args[2]), "each 델타")
            conds: List[Tuple[str, float]] = []
            for i in range(3, len(args), 2):
                conds.append((str(self.ev(args[i])),
                              _as_num(self.ev(args[i + 1]), "each 조건")))
            return self.r.each(listname, field, delta, conds)
        vals = [self.ev(a) for a in args]
        if fn == "min":
            if not vals:
                raise ExprError("문법 밖: min() 은 인자가 필요합니다")
            return _tidy(min(_as_num(v, "min") for v in vals))
        if fn == "max":
            if not vals:
                raise ExprError("문법 밖: max() 은 인자가 필요합니다")
            return _tidy(max(_as_num(v, "max") for v in vals))
        if fn == "abs":
            return _tidy(abs(_as_num(vals[0], "abs"))) if len(vals) == 1 else _reject(node)
        if fn == "floor":
            return int(math.floor(_as_num(vals[0], "floor"))) if len(vals) == 1 else _reject(node)
        if fn == "round":
            return int(round(_as_num(vals[0], "round"))) if len(vals) == 1 else _reject(node)
        if fn == "rand":
            if len(vals) != 2:
                raise ExprError("문법 밖: rand(a, b)")
            lo, hi = int(_as_num(vals[0], "rand")), int(_as_num(vals[1], "rand"))
            if lo > hi:
                lo, hi = hi, lo
            return self.rng().randint(lo, hi)
        _reject(node)

    # --- 문장 (do · on_fail) ---
    def run(self, node: ast.AST) -> None:
        if isinstance(node, ast.Module):
            for st in node.body:
                self.run(st)
            return
        if isinstance(node, ast.Expr):
            self.ev(node.value)          # 값만 내는 문장 = no-op(부수효과 없음)
            return
        if isinstance(node, ast.Assign):
            self._assign(node.targets[0], self.ev(node.value), aug=None)
            return
        if isinstance(node, ast.AugAssign):
            self._assign(node.target, self.ev(node.value), aug=node.op)
            return
        _reject(node)

    def _assign(self, target: ast.AST, value: Any, aug: Optional[ast.AST]) -> None:
        if self.r is None:
            raise ExprError("이름 없음: 쓰기 대상이 없습니다")
        if isinstance(target, ast.Name):
            base, key, field = target.id, "", ""
        else:
            base, key, field = _sub_path(target)
        if aug is None:
            self.r.write(base, key, mode="set", value=value, field=field)
            return
        n = _as_num(value, "대입")
        if isinstance(aug, ast.Add):
            self.r.write(base, key, mode="delta", value=n, field=field)
        elif isinstance(aug, ast.Sub):
            self.r.write(base, key, mode="delta", value=-n, field=field)
        elif isinstance(aug, ast.Mult):
            cur = _as_num(self.r.read_for_write(base, key, field), "대입")
            self.r.write(base, key, mode="set", value=_tidy(cur * n), field=field)
        else:
            _reject(aug)


def _truth(v: Any) -> bool:
    if isinstance(v, str):
        return bool(v.strip())
    if isinstance(v, dict):
        return bool(v)
    return bool(v)


def _eq(a: Any, b: Any) -> bool:
    """enum 은 `==` 만 쓴다(§3.4) — 문자열끼리면 문자열 비교, 아니면 수 비교."""
    if isinstance(a, str) or isinstance(b, str):
        return str(a) == str(b)
    return _as_num(a, "좌항") == _as_num(b, "우항")


def _tidy(v: float) -> Any:
    """정수로 떨어지면 정수로. 값 층이 정수라 여기서 접어 두면 쓰기가 단순해진다."""
    if isinstance(v, float) and v.is_integer():
        return int(v)
    return v


def eval_expr(compiled: Any, resolver: Optional["Resolver"] = None, turn: int = 0) -> Any:
    """값을 내는 식(when·derive) 한 번 계산."""
    c = compiled if isinstance(compiled, Compiled) else compile_expr(compiled, "eval")
    ev = _Evaluator(resolver, turn=turn, src=c.src)
    body = c.tree.body if isinstance(c.tree, ast.Expression) else c.tree
    return ev.ev(body)


def exec_expr(compiled: Any, resolver: "Resolver", turn: int = 0) -> None:
    """문장(do·on_fail) 순서대로 적용. 한 문장이 죽으면 그 식 전체가 죽는다(부분 적용은 남는다)."""
    c = compiled if isinstance(compiled, Compiled) else compile_expr(compiled, "exec")
    ev = _Evaluator(resolver, turn=turn, src=c.src)
    ev.run(c.tree)


# =========================================================
# 3. 이름 해석 — Resolver
# =========================================================
# 읽기 순서(§3.4): 선언 변수 → 재고[항목] → 코드 소유 읽기 전용 → 없으면 "이름 없음".
# 쓰기 대상: 선언 변수 · 재고 만. 코드 소유 필드·파생값에 대입하면 **등록 거부**다.
# ★모든 쓰기는 source="expr" · evidence="expr:<전이명>" 도장을 남긴다 — 나중에 "이 값이
#   왜 움직였나"를 물었을 때 답이 값 옆에 있어야 한다(사망 파이프라인 문법).

class Resolver:
    """한 채널·한 턴·한 전이의 이름 해석기.

    acting user = `ctx.user_id`. 없으면 유저 스코프 이름(재고·per_actor)은 ExprError —
    **등록은 통과하고 실행에서 스킵**된다. 등록 시점엔 누가 행위자인지 알 수 없기 때문이다.
    """

    def __init__(self, channel_id: str, ctx: Any = None, label: str = "") -> None:
        self.channel_id = str(channel_id)
        self.ctx = ctx
        self.label = str(label or "")
        self._cv = None
        self._decl: Optional[Dict[str, Any]] = None

    # --- 지연 로딩(순수 파서·평가기 층이 custom_vars 를 안 끌게) ---
    @property
    def cv(self):
        if self._cv is None:
            import custom_vars as _cv
            self._cv = _cv
        return self._cv

    def decl(self) -> Dict[str, Any]:
        if self._decl is None:
            self._decl = self.cv.get_declarations(self.channel_id) or {}
        return self._decl

    def invalidate(self) -> None:
        self._decl = None

    def actor(self) -> str:
        return str(getattr(self.ctx, "user_id", "") or "")

    def world(self) -> Dict[str, Any]:
        try:
            return domain_manager.get_world_state(self.channel_id) or {}
        except Exception:
            return {}

    def turn_index(self) -> int:
        try:
            return int(self.world().get("turn_index", 0) or 0)
        except (TypeError, ValueError):
            return 0

    def evidence(self) -> str:
        return f"expr:{self.label}" if self.label else "expr"

    # --- 소지품(재고) ---
    def _sojipin(self) -> Dict[str, Any]:
        uid = self.actor()
        if not uid:
            raise ExprError("이름 없음: 재고 (행위자 미상 — 유저 스코프)")
        try:
            nb = domain_manager.get_notebook_data(self.channel_id, uid) or {}
            return dict(((nb.get("sections") or {}).get("소지품") or {}).get("items") or {})
        except Exception as e:
            raise ExprError(f"재고를 읽지 못했습니다: {e}")

    # --- 조각(읽기 전용) ---
    def _fragments(self) -> Dict[str, Any]:
        uid = self.actor()
        if not uid:
            raise ExprError(f"이름 없음: {FRAGMENT_NAME} (행위자 미상 — 유저 스코프)")
        try:
            mem = domain_manager.get_ai_memory(self.channel_id, uid) or {}
        except Exception as e:
            raise ExprError(f"조각을 읽지 못했습니다: {e}")
        return {str(p.get("name")).strip(): {"n": 1} for p in (mem.get("passives") or [])
                if isinstance(p, dict) and str(p.get("name") or "").strip()}

    # --- 읽기 ---
    def read(self, name: str) -> Any:
        nm = str(name).strip()
        decl = self.decl()
        spec = decl.get(nm)
        if isinstance(spec, dict):
            vals = self.cv.get_values(self.channel_id)
            raw = (vals.get(nm) or {}).get("value", spec.get("init"))
            if spec.get("per_actor"):
                return int(self.cv.get_system_value(self.channel_id, nm, self.actor()) or 0)
            vtype = str(spec.get("type", "gauge"))
            if str(spec.get("scope")) == "npc" or vtype == "list":
                return raw if isinstance(raw, dict) else {}
            if vtype in ("enum", "text"):
                return str(raw if raw is not None else (spec.get("init") or "") or "")
            try:
                return int(raw)
            except (TypeError, ValueError):
                return int(spec.get("init") or 0)

        if nm == STOCK_NAME:
            return {k: {"n": _item_n(v)} for k, v in self._sojipin().items()}

        if nm == FRAGMENT_NAME:
            return self._fragments()

        if nm in CODE_READONLY:
            return self._code_read(nm)

        raise ExprError(f"이름 없음: {nm}")

    def _code_read(self, nm: str) -> Any:
        w = self.world()
        if nm == "기력":
            return int(self.cv.vigor_value(self.channel_id, self.actor()) or 0)
        if nm == "평형":
            v = self.cv.get_system_value(self.channel_id, "평형", self.actor())
            if v is None:
                try:
                    v = int(((w.get("composure") or {}).get("value")) or 0)
                except (TypeError, ValueError, AttributeError):
                    v = 0
            return int(v or 0)
        if nm == "시각":
            try:
                return int(w.get("hour", 0) or 0)
            except (TypeError, ValueError):
                return 0
        if nm == "일":
            try:
                return int(w.get("day", 1) or 1)
            except (TypeError, ValueError):
                return 1
        if nm == "날":
            # [2026-09-13 P17] 360일 달력의 **누적 일수** — 연·월·일 셋을 정수 하나로 접는다.
            #   `일`(달 안 날짜)은 달을 넘을 때 30→1 로 되감기므로 기한을 저장할 수 없다.
            #   산법은 boundary_engine._abs_day 와 같다(두 자리가 다른 날을 말하면 안 된다).
            try:
                dpy = int(getattr(config, "CALENDAR_DAYS_PER_YEAR", 360) or 360)
                dpm = int(getattr(config, "CALENDAR_DAYS_PER_MONTH", 30) or 30)
                y = int(w.get("year", 1) or 1)
                mo = int(w.get("month", 1) or 1)
                d = int(w.get("day", 1) or 1)
            except (TypeError, ValueError):
                return 0
            return (y - 1) * dpy + (mo - 1) * dpm + (d - 1)
        if nm == "시간대":
            return str(w.get("time_slot", "") or "")
        if nm == "턴":
            return self.turn_index()
        if nm == "날씨":
            return str(w.get("weather", "") or "")
        raise ExprError(f"이름 없음: {nm}")

    def _npc_keyed(self, spec: Any) -> bool:
        """첨자가 **인물 키**인가 — 정본은 custom_vars.npc_keyed(npc 스코프 · npc_enabled)."""
        try:
            return bool(self.cv.npc_keyed(spec))
        except Exception:
            return isinstance(spec, dict) and str(spec.get("scope")) == "npc"

    def read_sub(self, base: str, key: str, field: str = "") -> Any:
        nm = str(base).strip()
        k = str(key).strip()
        f = str(field or "").strip()
        if nm == FRAGMENT_NAME:
            raise ExprError(f'문법 밖: {nm}[{k}] — 조각은 has({nm}, "이름")으로만 읽습니다')
        if nm == STOCK_NAME:
            if f:
                raise ExprError(f"이름 없음: {nm}[{k}][{f}] (재고는 필드를 갖지 않습니다)")
            return _item_n(self._sojipin().get(k, 0))
        spec = self.decl().get(nm)
        if not isinstance(spec, dict):
            raise ExprError(f"이름 없음: {nm}[{k}]")
        # [2026-09-06 P8c] npc_enabled 시스템 변수는 `read` 가 acting user 의 **정수**를 돌려주므로
        #   첨자 갈래는 저장 dict 를 직접 본다(같은 dict 안에서 키만 인물이다).
        if self._npc_keyed(spec) and str(spec.get("scope")) != "npc":
            who = self.cv.resolve_npc(self.channel_id, k)
            if not who:
                raise ExprError(f"이름 없음: {nm}[{k}] (허용 인물이 아닙니다)")
            stored = (self.cv.get_values(self.channel_id).get(nm) or {}).get("value")
            cur = stored.get(who) if isinstance(stored, dict) else None
            if cur is None:
                cur = spec.get("init", 0)
            try:
                return int(cur)
            except (TypeError, ValueError):
                return 0
        raw = self.read(nm)
        if str(spec.get("scope")) == "npc":
            who = self.cv.resolve_npc(self.channel_id, k)
            if not who:
                raise ExprError(f"이름 없음: {nm}[{k}] (허용 인물이 아닙니다)")
            cur = (raw or {}).get(who)
            if cur is None:
                cur = spec.get("init", 0)
            if str(spec.get("type")) == "enum":
                return str(cur or "")
            try:
                return int(cur)
            except (TypeError, ValueError):
                return 0
        if str(spec.get("type")) == "list":
            rec = (raw or {}).get(k)
            if not f:
                return _item_n(rec if rec is not None else 0)
            # [2026-09-09 P11] 두 번째 단 = 레코드 필드. `fields` 가 없는 옛 list 도 `n` 하나짜리
            #   레코드로 **읽힌다**(저장 모양은 그대로) — 그게 마이그레이션 0 의 실물이다.
            flds = self.cv.item_fields(spec)
            if f not in flds:
                raise ExprError(f"이름 없음: {nm}[{k}][{f}] (선언에 없는 필드)")
            if rec is None:
                raise ExprError(f"이름 없음: {nm}[{k}] (없는 항목)")
            src = rec if isinstance(rec, dict) else {"n": rec}
            cur = src.get(f, (spec.get("field_init") or {}).get(f, flds[f][0]))
            try:
                return int(cur)
            except (TypeError, ValueError):
                return int(flds[f][0])
        raise ExprError(f"이름 없음: {nm}[{k}] (첨자를 받지 않는 변수)")

    def read_for_write(self, base: str, key: str, field: str = "") -> Any:
        return self.read_sub(base, key, field) if key else self.read(base)

    # --- [2026-09-09 P11] 일괄·생멸 ---
    def _list_spec(self, name: str) -> Dict[str, Any]:
        spec = self.decl().get(str(name).strip())
        if not isinstance(spec, dict) or str(spec.get("type")) != "list":
            raise ExprError(f"목록이 아닙니다: {name}")
        okk, why = self.writable(str(name).strip())
        if not okk:
            raise ExprError(why)
        return spec

    def each(self, name: str, field: str, delta: float,
             conds: List[Tuple[str, float]]) -> int:
        """모든 항목의 그 필드에 델타(범위 클램프). 조건쌍은 전부 `필드 >= 최소` AND.

        루프 문법을 여는 대신 **코드가 항목을 돈다** — 식은 무엇을·얼마나만 말한다.
        """
        nm = str(name).strip()
        spec = self._list_spec(nm)
        f = str(field).strip()
        flds = self.cv.item_fields(spec)
        if f not in flds:
            raise ExprError(f"이름 없음: {nm}[…][{f}] (선언에 없는 필드)")
        for cf, _mn in conds:
            if str(cf).strip() not in flds:
                raise ExprError(f"이름 없음: {nm}[…][{cf}] (선언에 없는 필드)")
        raw = self.read(nm)
        hit = 0
        for key in list(_items_of(raw).keys()):
            rec = _items_of(raw).get(key)
            src = rec if isinstance(rec, dict) else {"n": rec}
            passed = True
            for cf, mn in conds:
                try:
                    v = int(src.get(str(cf).strip(),
                                    (spec.get("field_init") or {}).get(str(cf).strip(), 0)))
                except (TypeError, ValueError):
                    v = 0
                if v < mn:
                    passed = False
                    break
            if not passed:
                continue
            self.write(nm, str(key), mode="delta", value=delta, field=f)
            hit += 1
        return hit

    def item_op(self, name: str, item: str, op: str) -> int:
        nm = str(name).strip()
        self._list_spec(nm)
        r = self.cv.apply_code_item(self.channel_id, nm, str(item).strip(), op,
                                    evidence=self.evidence(), turn=self.turn_index(),
                                    source="expr")
        return 1 if r else 0

    def expire(self, name: str, field: str) -> int:
        """[2026-09-13 P17] 기한이 지난 항목을 지운다 — `expire(작물, "기한")`.

        저장하는 건 **고정된 끝**(절대 일수)이고 움직이는 쪽은 시계(`날`)다. 매일 -1 로
        깎는 카운트다운은 틱을 한 번 놓치면 영영 어긋나지만, 끝은 놓쳐도 어긋나지 않는다.
        """
        nm = str(name).strip()
        spec = self._list_spec(nm)
        f = str(field or "").strip()
        if f not in self.cv.item_fields(spec):
            raise ExprError(f"이름 없음: {nm}[{f}] (선언에 없는 필드)")
        today = int(self._code_read("날"))
        items = _items_of(self.read(nm))
        gone: List[str] = []
        for k in list(items.keys()):
            rec = items.get(k)
            try:
                end = int((rec or {}).get(f))
            except (TypeError, ValueError, AttributeError):
                continue
            if end > today:
                continue
            if self.cv.apply_code_item(self.channel_id, nm, str(k), "remove",
                                       evidence=self.evidence(), turn=self.turn_index(),
                                       source="expr"):
                gone.append(str(k))
        if gone:
            logger.info("[Expr] expire %s 날=%s 제거 %s", nm, today, ", ".join(gone))
        return len(gone)

    # --- 쓰기 ---
    def write(self, base: str, key: str, mode: str, value: Any, field: str = "") -> None:
        nm = str(base).strip()
        k = str(key).strip()
        f = str(field or "").strip()
        # [2026-09-06 P8c] 맨이름 쓰기는 그대로 막는다(`기력 = 0` = PC 총량 대입 — 코드 소유).
        #   막지 않는 건 **인물 첨자**뿐이다: 인물 값은 코드가 계산하는 값이 아니라 npc 스코프
        #   변수와 같은 관측 값이고, 그 문법(`호감도[리나] -= 5`)이 이미 유저의 것이다.
        if nm in CODE_READONLY and not (k and self._npc_keyed(self.decl().get(nm))):
            raise ExprError(f"코드 소유 필드에는 쓸 수 없습니다: {nm}")
        if nm == FRAGMENT_NAME:
            raise ExprError(f"읽기 전용 컬렉션에는 쓸 수 없습니다: {nm}")
        if nm == STOCK_NAME:
            self._write_stock(k, mode, value)
            return
        spec = self.decl().get(nm)
        if not isinstance(spec, dict):
            raise ExprError(f"이름 없음: {nm}")

        vtype = str(spec.get("type", "gauge"))
        kw: Dict[str, Any] = {"evidence": self.evidence(), "source": "expr",
                              "turn": self.turn_index(), "actor": self.actor()}
        if vtype == "list":
            if not k:
                raise ExprError(f"목록형은 항목 첨자가 필요합니다: {nm}[항목]")
            kw["item"] = k
            if f:
                if f not in self.cv.item_fields(spec):
                    raise ExprError(f"이름 없음: {nm}[{k}][{f}] (선언에 없는 필드)")
                kw["field"] = f
        elif f:
            raise ExprError(f"두 단 첨자는 목록형에만 씁니다: {nm}[{k}][{f}]")
        elif str(spec.get("scope")) == "npc":
            if not k:
                raise ExprError(f"인물별 변수는 첨자가 필요합니다: {nm}[인물]")
            kw["npc"] = k
        elif k and self._npc_keyed(spec):
            kw["npc"] = k               # [P8c] npc_enabled 시스템 변수 — 첨자는 인물 키
        elif k:
            raise ExprError(f"첨자를 받지 않는 변수입니다: {nm}[{k}]")
        if spec.get("per_actor") and not kw.get("npc") and not self.actor():
            raise ExprError(f"이름 없음: {nm} (행위자 미상 — 유저 스코프)")

        if vtype == "enum":
            if mode != "set":
                raise ExprError(f"단계형에는 `=` 만 쓸 수 있습니다: {nm}")
            self.cv.apply_code_write(self.channel_id, nm, value=str(value), **kw)
        elif mode == "delta":
            self.cv.apply_code_write(self.channel_id, nm, delta=value, **kw)
        else:
            self.cv.apply_code_write(self.channel_id, nm, value=value, **kw)

    def _write_stock(self, item: str, mode: str, value: Any) -> None:
        """재고 = P0 노트북 [소지품](유저 스코프). custom_vars stock 이관은 P8."""
        uid = self.actor()
        if not uid:
            raise ExprError("이름 없음: 재고 (행위자 미상 — 유저 스코프)")
        if not item:
            raise ExprError("재고는 항목 첨자가 필요합니다: 재고[철]")
        try:
            n = int(round(float(value)))
        except (TypeError, ValueError):
            raise ExprError(f"재고 수량이 수가 아닙니다: {value!r}")
        if mode == "set":
            cur = _item_n(self._sojipin().get(item, 0))
            n = n - cur
        if n == 0:
            return
        import game_character as _gc
        if n > 0:
            _gc.add_item_to_sojipin(self.channel_id, item, uid, n)
        else:
            _gc.remove_item_from_sojipin(self.channel_id, item, uid, -n)
        logger.info("[Expr] 재고 %s %+d (uid=%s) ev=%s", item, n, uid, self.evidence())

    # --- 등록 검사용(값 없이 이름만 본다) ---
    def name_exists(self, nm: str, extra: Optional[Set[str]] = None) -> bool:
        nm = str(nm).strip()
        if nm in (extra or set()):
            return True
        return nm in self.decl() or nm == STOCK_NAME or nm == FRAGMENT_NAME or nm in CODE_READONLY

    def writable(self, nm: str, keyed: bool = False) -> Tuple[bool, str]:
        """등록 시점의 쓰기 적법성. keyed=True 면 그 이름은 **첨자로만** 쓰였다(P8c)."""
        nm = str(nm).strip()
        if nm in CODE_READONLY:
            if keyed and self._npc_keyed(self.decl().get(nm)):
                return True, ""     # `기력[리나]` — 인물 값은 코드 소유가 아니다
            return False, f"코드 소유 필드에는 쓸 수 없습니다: {nm}"
        if nm == FRAGMENT_NAME:
            return False, f"읽기 전용 컬렉션에는 쓸 수 없습니다: {nm}"
        if nm == STOCK_NAME:
            return True, ""
        spec = self.decl().get(nm)
        if not isinstance(spec, dict):
            return False, f"이름 없음: {nm}"
        if self.cv.is_derived(spec):
            return False, f"파생값에는 쓸 수 없습니다: {nm} (식이 매턴 다시 계산합니다)"
        return True, ""


def feed_mode(spec: Any) -> str:
    """급식 게이트 조회 — 정본은 custom_vars.feed_mode. 여기선 재export 한 줄."""
    import custom_vars as _cv
    return _cv.feed_mode(spec)


# =========================================================
# 4. 선언 저장 — output_decl["derives"] / ["transitions"]
# =========================================================
# ★선언 층에 산다(도메인 루트 `output_decl`) — `!클리어` 를 넘는다. 값·발화 이력은
#   world_state 라 클리어에 죽는다. "클리어 후 선언은 그대로, 값은 start 로"(§4-7).

def _decl_layer(channel_id: str) -> Dict[str, Any]:
    return domain_manager.get_output_decl(channel_id) or {}


def _save_layer(channel_id: str, layer: Dict[str, Any]) -> None:
    domain_manager.update_output_decl(channel_id, layer)


def list_derives(channel_id: str) -> Dict[str, Any]:
    d = _decl_layer(channel_id).get(DECL_DERIVES)
    return dict(d) if isinstance(d, dict) else {}


def list_transitions(channel_id: str) -> Dict[str, Any]:
    d = _decl_layer(channel_id).get(DECL_TRANSITIONS)
    return dict(d) if isinstance(d, dict) else {}


def list_directives(channel_id: str) -> Dict[str, Any]:
    """[2026-09-13 P16] 조건부 지시 선언. 전이·파생과 같은 선언 층에 산다(`!클리어` 생존)."""
    d = _decl_layer(channel_id).get(DECL_DIRECTIVES)
    return dict(d) if isinstance(d, dict) else {}


def _ordered(items: Dict[str, Any]) -> List[Tuple[str, Dict[str, Any]]]:
    """order 순 → 같은 order 면 등록 순. 실행 순서는 재현성의 일부다."""
    rows = [(k, v) for k, v in items.items() if isinstance(v, dict)]
    return sorted(rows, key=lambda kv: (int(kv[1].get("order", 0) or 0), str(kv[0])))


def _check_names(res: Resolver, compiled: Compiled, extra: Set[str]) -> str:
    for nm in sorted(compiled.names):
        if not res.name_exists(nm, extra):
            return f"이름 없음: {nm}"
    return ""


def _check_writes(res: Resolver, compiled: Compiled) -> str:
    bare = getattr(compiled, "bare", None)
    for nm in sorted(compiled.writes):
        keyed = bare is not None and nm not in bare      # 첨자로만 쓰인 이름
        okw, msg = res.writable(nm, keyed=keyed)
        if not okw:
            return msg
    return ""


def _compile_or_msg(src: Any, mode: str) -> Tuple[Optional[Compiled], str]:
    try:
        return compile_expr(src, mode), ""
    except ExprError as e:
        return None, str(e)


def register_derive(channel_id: str, name: Any, expr: Any,
                    order: Optional[int] = None) -> Tuple[bool, str]:
    """파생값 등록. 변수는 **이미 선언돼 있어야** 한다(값의 모양은 custom_vars 가 쥔다).

    등록이 하는 일 셋: 컴파일 · 이름/순환 검사 · `custom_vars` 에 `derived` 표시.
    표시가 붙는 순간 그 이름은 LLM 델타 대상에서 빠지고 추출 급식에서도 빠진다.
    """
    nm = str(name or "").strip()
    if not nm:
        return False, "이름 없음: (빈 이름)"
    res = Resolver(channel_id, None, label=nm)
    spec = res.decl().get(nm)
    if not isinstance(spec, dict):
        return False, f"이름 없음: {nm} (먼저 변수로 선언해 주세요)"
    if spec.get("system"):
        return False, f"시스템 변수는 파생값이 될 수 없습니다: {nm}"
    # [2026-09-24 감사] _derive_pass 는 `apply_code_write(nm, value=…)` 한 줄이라 npc 스코프(인물 키
    #   없음 → resolve_npc "" → None)·목록형(항목 키 없음 → None)에선 매턴 조용히 no-op 였다.
    #   등록이 통과해 LLM 델타만 끊기고 값은 영영 init — 스칼라(global·pc 의 gauge/counter/enum/text)만 받는다.
    if str(spec.get("type", "gauge")) not in ("gauge", "counter", "enum", "text") \
            or res.cv.npc_keyed(spec) or spec.get("per_actor"):
        return False, f"파생값은 인물별·목록형 변수에 붙지 않습니다: {nm} (전역·PC 단일값만)"

    c, msg = _compile_or_msg(expr, "eval")
    if c is None:
        return False, msg

    rows = list_derives(channel_id)
    if nm not in rows and len(rows) >= MAX_DERIVES:
        return False, f"파생값은 채널당 {MAX_DERIVES}개까지입니다."
    msg = _check_names(res, c, set(rows) | {nm})
    if msg:
        return False, msg

    cand = dict(rows)
    cand[nm] = {"expr": c.src, "order": int(order) if order is not None else len(rows)}
    cyc = _find_cycle(cand)
    if cyc:
        return False, f"순환 참조입니다: {' → '.join(cyc)}"

    layer = _decl_layer(channel_id)
    layer[DECL_DERIVES] = cand
    _save_layer(channel_id, layer)
    res.cv.mark_derived(channel_id, nm, True)
    logger.info("[Expr] 파생값 등록 %s = %s", nm, c.src)
    return True, f"{nm} · 파생값 · {c.src}"


def unregister_derive(channel_id: str, name: Any) -> bool:
    nm = str(name or "").strip()
    rows = list_derives(channel_id)
    if nm not in rows:
        return False
    rows.pop(nm, None)
    layer = _decl_layer(channel_id)
    layer[DECL_DERIVES] = rows
    _save_layer(channel_id, layer)
    try:
        import custom_vars as _cv
        _cv.mark_derived(channel_id, nm, False)
    except Exception as e:
        logger.debug("[Expr] derived 표시 해제 skip: %s", e)
    return True


def _find_cycle(rows: Dict[str, Any]) -> List[str]:
    """파생 그래프 DFS. 순환이면 경로를 돌려준다(되비침에 그대로 찍힌다)."""
    edges: Dict[str, Set[str]] = {}
    for nm, rec in rows.items():
        try:
            edges[nm] = set(compile_expr(rec.get("expr"), "eval").names)
        except ExprError:
            edges[nm] = set()
    state: Dict[str, int] = {}
    path: List[str] = []

    def dfs(node: str) -> List[str]:
        state[node] = 1
        path.append(node)
        for nxt in sorted(edges.get(node, ())):
            if nxt not in edges:
                continue
            if state.get(nxt) == 1:
                return path[path.index(nxt):] + [nxt]
            if state.get(nxt, 0) == 0:
                hit = dfs(nxt)
                if hit:
                    return hit
        path.pop()
        state[node] = 2
        return []

    for nm in sorted(edges):
        if state.get(nm, 0) == 0:
            hit = dfs(nm)
            if hit:
                return hit
    return []


def register_transition(channel_id: str, spec: Any) -> Tuple[bool, str]:
    """전이 등록. **제어 흐름은 전부 고정 필드**고 식은 값 계산만 한다(§3.4).

    when   — eval 모드. 거짓→참 엣지에서 발화.
    do/on_fail — exec 모드. 발화 시 코드가 순서대로 적용.
    나머지(check·once·hold_turns·notify·record·order·source)는 식 밖의 칸이다.
    """
    if not isinstance(spec, dict):
        return False, "전이 선언을 읽지 못했습니다."
    nm = str(spec.get("name", "") or "").strip()
    if not nm:
        return False, "이름 없음: (전이 이름이 비었습니다)"

    res = Resolver(channel_id, None, label=nm)
    rows = list_transitions(channel_id)
    if nm not in rows and len(rows) >= MAX_TRANSITIONS:
        return False, f"전이는 채널당 {MAX_TRANSITIONS}개까지입니다."

    compiled: List[Compiled] = []
    when_src = spec.get("when")
    if when_src not in (None, ""):
        c, msg = _compile_or_msg(when_src, "eval")
        if c is None:
            return False, msg
        compiled.append(c)
    for key in ("do", "on_fail"):
        src = spec.get(key)
        if src in (None, ""):
            continue
        c, msg = _compile_or_msg(src, "exec")
        if c is None:
            return False, msg
        compiled.append(c)

    cue = str(spec.get("narrated_cue", "") or "").strip()
    if not compiled and not cue:
        return False, "문법 밖: when·do·narrated_cue 가 모두 비었습니다."

    # [2026-09-06 P4] trigger — 무엇이 이 전이를 여는가. 명시가 없으면 **옛 모양에서 읽는다**
    #   (P3 시절 저장분은 이 칸이 없다): 큐만 있고 when 이 없으면 narrated, 그 밖은 expr.
    trigger = str(spec.get("trigger", "") or "").strip().lower()
    if trigger not in TRIGGER_KINDS:
        trigger = _infer_trigger(spec)
    if trigger == "narrated" and not cue:
        return False, "문법 밖: narrated 전이에는 narrated_cue 가 필요합니다."
    if trigger == "operation" and spec.get("do") in (None, ""):
        return False, "문법 밖: operation 전이에는 do 가 필요합니다."

    known = set(list_derives(channel_id))
    for c in compiled:
        msg = _check_names(res, c, known)
        if msg:
            return False, msg
        msg = _check_writes(res, c)
        if msg:
            return False, msg

    check = str(spec.get("check", "none") or "none").strip().lower()
    if check not in CHECK_KINDS:
        check = "none"
    notify = str(spec.get("notify", "mail") or "mail").strip().lower()
    if notify not in NOTIFY_KINDS:
        notify = "mail"
    try:
        hold = max(0, int(spec.get("hold_turns", 0) or 0))
    except (TypeError, ValueError):
        hold = 0
    # [2026-09-09 P11] `cadence` 는 여기까지 라우터 스키마에만 있고 **저장되지 않았다**(§0 정정).
    #   저장하면서 뜻이 하나 생긴다: day 전이는 매턴 ⑤ 에서 빠지고 경계 틱에서만 평가된다.
    cadence = str(spec.get("cadence", "turn") or "turn").strip().lower()
    if cadence not in CADENCE_KINDS:
        cadence = "turn"
    # [2026-09-24 감사 §5-2 #18b] 하루(day) 전이는 경계 틱(`run_day(ctx=None)`)에서만 평가돼 **그 턴 판정이 없다** —
    #   check=judgment 면 영구 보류였다(엣지는 안 먹지만 한 번도 안 연다). 등록에서 거절하고 고칠 길을 말한다.
    if cadence == "day" and check == "judgment":
        return False, ("문법 밖: 하루(day) 전이는 판정(check=judgment)을 쓸 수 없습니다 — 하루 경계엔 그 턴 판정이 "
                       "없습니다. 판정이 필요하면 cadence 를 turn 으로 두고, 아니면 check 를 빼세요.")
    try:
        hold_d = max(0, int(spec.get("hold_days", 0) or 0))
    except (TypeError, ValueError):
        hold_d = 0
    # [2026-09-13 P17] `cooldown` — 발화 뒤 N cadence 동안 **엣지를 억제**한다. 새 상태 칸은
    #   없다: once·hold·cooldown 은 같은 불변량(마지막 발화 시각 = `fired_at`)의 세 읽기다.
    #   once = cooldown ∞ / hold = 발화 뒤 N 동안 do 반복 / cooldown = 발화 뒤 N 동안 엣지 무시.
    try:
        cooldown = max(0, int(spec.get("cooldown", 0) or 0))
    except (TypeError, ValueError):
        cooldown = 0
    # [2026-09-13 P14] `deliver` — 이 전이가 발화하면 **도착물이 온다**. 칸 셋(kind·from·brief)뿐이고
    #   본문은 여기 없다: 본문은 world_board 콜이 쓴다(틀=코드·글=LLM). 없으면 None(종전 그대로).
    deliver = spec.get("deliver")
    if isinstance(deliver, dict):
        _dk = str(deliver.get("kind", "") or "").strip().lower()
        if _dk not in DELIVER_KINDS:
            _dk = "letter"          # 모르는 종류는 가장 좁은 쪽(개인)으로 접는다
        deliver = {"kind": _dk,
                   "from": str(deliver.get("from") or "").strip() or None,
                   "brief": str(deliver.get("brief") or "").strip()}
    else:
        deliver = None

    record = spec.get("record")
    if isinstance(record, dict):
        record = {"notebook_section": str(record.get("notebook_section", "") or "").strip(),
                  "exposure": str(record.get("exposure", "on_demand") or "on_demand").strip()}
        if not record["notebook_section"]:
            record = None
    else:
        record = None

    entry = {
        "when": str(when_src).strip() if when_src not in (None, "") else None,
        "do": str(spec.get("do")).strip() if spec.get("do") not in (None, "") else None,
        "on_fail": str(spec.get("on_fail")).strip() if spec.get("on_fail") not in (None, "") else None,
        "narrated_cue": cue or None,
        "trigger": trigger,
        "check": check,
        "once": bool(spec.get("once", False)),
        "cadence": cadence,
        "hold_turns": hold,
        "hold_days": hold_d,
        "cooldown": cooldown,
        "notify": notify,
        # [2026-09-07 P10] 도착물 형식 이름. **이름만** 나른다 — 본문 렌더는 여기 없다.
        "notify_format": str(spec.get("notify_format", "") or "").strip() or None,
        # [2026-09-13 P14] 도착물 선언(kind·from·brief) 또는 None.
        "deliver": deliver,
        "record": record,
        "order": int(spec.get("order", len(rows)) or 0),
        "source": str(spec.get("source", "") or ""),
    }
    rows[nm] = entry
    layer = _decl_layer(channel_id)
    layer[DECL_TRANSITIONS] = rows
    _save_layer(channel_id, layer)
    logger.info("[Expr] 전이 등록 %s trigger=%s when=%r do=%r",
                nm, trigger, entry["when"], entry["do"])
    return True, format_registration(dict(entry, name=nm))


def unregister_transition(channel_id: str, name: Any) -> bool:
    nm = str(name or "").strip()
    rows = list_transitions(channel_id)
    if nm not in rows:
        return False
    rows.pop(nm, None)
    layer = _decl_layer(channel_id)
    layer[DECL_TRANSITIONS] = rows
    _save_layer(channel_id, layer)
    return True


def register_directive(channel_id: str, spec: Any) -> Tuple[bool, str]:
    """[2026-09-13 P16] 조건부 지시 등록 — `when`(참·거짓)과 `text`(유저 자연어 한 줄).

    ★전이와 다른 점 하나가 이 선언의 전부다: **아무것도 쓰지 않는다.** do 도 on_fail 도
      기록도 알림도 없다. 참인 동안 매턴 산문 급식에 그 문장이 실릴 뿐이다 —
      "코드가 값으로 조건을 판정하고, 문장은 유저 것"의 가장 곧은 형태(스펙 §1 헌법).
    실패는 (False, 사유). 라우터는 그 실패를 **거부가 아니라 강등**으로 받는다(P10 과 같은 길):
      조건을 못 읽으면 문장을 상시 출력룰로 내려보낸다 — 지켜짐은 잃고 출력은 산다.
    """
    if not isinstance(spec, dict):
        return False, "지시 선언을 읽지 못했습니다."
    nm = str(spec.get("name", "") or "").strip()
    if not nm:
        return False, "이름 없음: (지시 이름이 비었습니다)"
    text = str(spec.get("text", "") or "").strip()[:DIRECTIVE_TEXT_MAX]
    if not text:
        return False, f"{nm} · 지시 문장이 비었습니다."
    when_src = spec.get("when")
    if when_src in (None, ""):
        # 조건 없는 지시는 애초에 **상시 규칙**이다(=출력룰 원문). 여기서 받으면
        # 매턴 무조건 실리는 줄이 선언 층에 숨는다 — 그 자리는 Slot 33 이다.
        return False, f"{nm} · 조건이 없습니다(상시 규칙은 출력룰입니다)."

    rows = list_directives(channel_id)
    try:
        cap = int(getattr(config, "MAX_DIRECTIVES", 40) or 40)
    except (TypeError, ValueError):
        cap = 40
    if nm not in rows and len(rows) >= cap:
        return False, f"지시는 채널당 {cap}개까지입니다 (현재 {len(rows)}개)."

    c, msg = _compile_or_msg(when_src, "eval")
    if c is None:
        return False, msg
    res = Resolver(channel_id, None, label=nm)
    msg = _check_names(res, c, set(list_derives(channel_id)))
    if msg:
        return False, msg

    entry = {
        "when": str(when_src).strip(),
        "text": text,
        "order": int(spec.get("order", len(rows)) or 0),
        "source": str(spec.get("source", "") or ""),
    }
    rows[nm] = entry
    layer = _decl_layer(channel_id)
    layer[DECL_DIRECTIVES] = rows
    _save_layer(channel_id, layer)
    logger.info("[Expr] 지시 등록 %s when=%r", nm, entry["when"])
    return True, format_registration(dict(entry, name=nm, kind="directive"))


def unregister_directive(channel_id: str, name: Any) -> bool:
    nm = str(name or "").strip()
    rows = list_directives(channel_id)
    if nm not in rows:
        return False
    rows.pop(nm, None)
    layer = _decl_layer(channel_id)
    layer[DECL_DIRECTIVES] = rows
    _save_layer(channel_id, layer)
    return True


def eval_when(channel_id: str, src: Any, ctx: Any = None, label: str = "",
              resolver: Optional["Resolver"] = None) -> bool:
    """`when` 한 줄을 **지금 값**으로 참·거짓 판정. 전이 ⑤·day 틱·지시가 같은 함수를 쓴다.

    ★한 자리인 것이 계약이다 — 전이와 지시가 다른 스냅샷을 보면 같은 조건이 같은 턴에
      두 답을 낸다. 예외는 삼키지 않는다(호출자가 스킵·거짓 중 무엇으로 접을지 정한다).
    """
    res = resolver if resolver is not None else Resolver(channel_id, ctx, label=label)
    return bool(eval_expr(compile_expr(src, "eval"), res))


def active_directives(channel_id: str, ctx: Any = None) -> List[Dict[str, Any]]:
    """지금 참인 지시들 — 선언 순서. 쓰기 0·콜 0(순수 판정).

    평가 실패(이름 없음·예외)는 **거짓**으로 접는다 + debug 로그 한 줄: 지시 하나가 깨져도
    산문은 서야 하고, 깨진 조건을 참으로 접으면 안 걸려야 할 문장이 매턴 실린다.
    """
    out: List[Dict[str, Any]] = []
    try:
        rows = _ordered(list_directives(channel_id))
    except Exception as e:
        logger.debug("[Expr] 지시 목록 skip: %s", e)
        return out
    for nm, rec in rows:
        text = str(rec.get("text", "") or "").strip()
        if not text:
            continue
        try:
            if not eval_when(channel_id, rec.get("when"), ctx, nm):
                continue
        except Exception as e:
            logger.debug("[Expr] 지시 %s 평가 실패 → 거짓: %s", nm, e)
            continue
        out.append({"name": nm, "text": text, "when": str(rec.get("when") or "")})
    return out


def _infer_trigger(rec: Any) -> str:
    """옛 모양(trigger 칸이 없는 P3 저장분)에서 갈래를 읽는다. 저장은 안 고친다."""
    if not isinstance(rec, dict):
        return "expr"
    t = str(rec.get("trigger", "") or "").strip().lower()
    if t in TRIGGER_KINDS:
        return t
    if rec.get("narrated_cue") and not rec.get("when"):
        return "narrated"
    return "expr"


def _target_parts(node: ast.AST) -> Tuple[str, str]:
    """대입 대상 → (밑동, 첨자키). `재고[철]` → ("재고", "철"), `검` → ("검", "")."""
    if isinstance(node, ast.Subscript):
        # [2026-09-24 감사] 두 겹 첨자(`화분["양파"]["수분"]`)에서 `_sub_key(node)` 는 바깥 키=
        #   **필드**("수분")라 항목 이름 자리에 필드가 들어갔다. 항목 키는 `_sub_path` 의 둘째 칸.
        _b, _k, _f = _sub_path(node)
        return _b, _k
    if isinstance(node, ast.Name):
        return node.id, ""
    _reject(node)


def _target_label(node: ast.AST) -> str:
    """사람이 부르는 이름 — 첨자가 있으면 **항목 이름**이 그 이름이다(`재고[철]` 은 "철")."""
    base, key = _target_parts(node)
    return key or base


def operation_io(src: Any) -> Dict[str, List[str]]:
    """연산 `do` 의 재료·산출 — `-=` 대상이 inputs, 그 밖의 쓰기가 outputs.

    부호가 곧 방향이라는 것이 이 함수의 전부다. 별도 선언 칸을 만들지 않은 이유:
    선언과 식이 어긋나면 사전 검사가 식이 아니라 선언을 검사하게 된다.
    """
    c = src if isinstance(src, Compiled) else compile_expr(src, "exec")
    ins: List[str] = []
    outs: List[str] = []
    for node in ast.walk(c.tree):
        if isinstance(node, ast.AugAssign):
            lab = _target_label(node.target)
            (ins if isinstance(node.op, ast.Sub) else outs).append(lab)
        elif isinstance(node, ast.Assign):
            for t in node.targets:
                outs.append(_target_label(t))
    seen: Set[str] = set()
    ins = [x for x in ins if not (x in seen or seen.add(x))]
    seen = set()
    outs = [x for x in outs if not (x in seen or seen.add(x))]
    return {"inputs": ins, "outputs": outs}


def _op_requirements(src: Any) -> List[Tuple[str, str, str, ast.AST]]:
    """사전 검사 재료 — `-=` 마다 (밑동, 첨자키, 필드, 수량 노드). 수량은 실행 시점에 잰다."""
    c = src if isinstance(src, Compiled) else compile_expr(src, "exec")
    reqs: List[Tuple[str, str, str, ast.AST]] = []
    for node in ast.walk(c.tree):
        if isinstance(node, ast.AugAssign) and isinstance(node.op, ast.Sub):
            base, key = _target_parts(node.target)
            # [2026-09-24 감사] 두 겹 첨자의 필드를 버리고 `화분["수분"]`(없는 항목=0)을 읽어
            #   사전 검사가 늘 "현재 0" 으로 연산을 막았다 — 필드까지 실어 그 필드를 읽는다.
            fld = _sub_path(node.target)[2] if isinstance(node.target, ast.Subscript) else ""
            reqs.append((base, key, fld, node.value))
    return reqs


# =========================================================
# 4-2. 추출 급식 · 신고 큐 (P4 transition_cues · P5 operations)
# =========================================================
# 급식은 **후보 목록**이고 신고는 **시도 여부**다. 성공/실패는 코드가 판정한다 —
# 모델에게 "됐나"를 물으면 그 순간 재고도 판정도 모델 소유가 된다(스펙 ④-3).

def _fireable(rec: Dict[str, Any], st: Dict[str, Any], turn: int) -> bool:
    """급식 자격 — once 로 이미 발화했거나 hold 중이면 후보가 아니다(물어봐야 못 쓴다)."""
    if rec.get("once") and int(st.get("fire_count", 0) or 0) > 0:
        return False
    hold_until = st.get("hold_until")
    if hold_until is not None and turn <= int(hold_until):
        return False
    return True


def _live_rows(channel_id: str, kind: str) -> List[Tuple[str, Dict[str, Any], Dict[str, Any]]]:
    ws = domain_manager.get_world_state(channel_id) or {}
    state = ws.get(TRANSITION_STATE_KEY) or {}
    try:
        turn = int(ws.get("turn_index", 0) or 0)
    except (TypeError, ValueError):
        turn = 0
    out = []
    for nm, rec in _ordered(list_transitions(channel_id)):
        if _infer_trigger(rec) != kind:
            continue
        st = state.get(nm) if isinstance(state.get(nm), dict) else {}
        if not _fireable(rec, st, turn):
            continue
        out.append((nm, rec, st))
    return out


def pending_cues(channel_id: str, *texts: str) -> List[Dict[str, Any]]:
    """P4 급식 — 이번 턴 텍스트가 cue 를 스칠 때만 후보. 0개면 섹션 자체가 없다(콜 순증 0).

    게이트는 `custom_vars.mentioned_names` 와 **같은 판정면**이되 재료가 낱말 단위다:
    cue 한 문장을 통째로 부분 일치시키면 산문이 그 문장을 그대로 쓰지 않는 한 영영 안 걸린다.
    """
    import custom_vars as _cv
    out: List[Dict[str, Any]] = []
    for nm, rec, _st in _live_rows(channel_id, "narrated"):
        cue = str(rec.get("narrated_cue") or "").strip()
        if not cue:
            continue
        words = [w for w in cue.split() if w]
        if not words:
            continue
        need = min(CUE_MATCH_MIN, len(words))
        if len(_cv.mentioned_names(words, *texts)) < need:
            continue
        out.append({"name": nm, "cue": cue})
    return out


def pending_operations(channel_id: str, *texts: str) -> List[Dict[str, Any]]:
    """P5 급식 — 연산 이름이나 재료·산출 항목이 이번 턴 텍스트에 스칠 때만 후보."""
    import custom_vars as _cv
    out: List[Dict[str, Any]] = []
    for nm, rec, _st in _live_rows(channel_id, "operation"):
        try:
            io_ = operation_io(rec.get("do"))
        except ExprError as e:
            logger.debug("[Expr] 연산 %s 급식 스킵: %s", nm, e)
            continue
        words = set(io_["inputs"]) | set(io_["outputs"]) | {nm}
        if not _cv.mentioned_names(words, *texts):
            continue
        out.append({"name": nm, "inputs": list(io_["inputs"]),
                    "outputs": list(io_["outputs"])})
    return out


def _queue(channel_id: str, key: str, rows: Any, kind: str,
           flag: str) -> int:
    """신고 → world_state 대기열. **근거 없으면 폐기**(사망 파이프라인 문법).

    `_ws_set` 규율: world_state 는 매번 deep copy 스냅샷이라 읽는 즉시 고쳐 쓴다.
    """
    if not isinstance(rows, list) or not rows:
        return 0
    known = {nm for nm, _r, _s in _live_rows(channel_id, kind)}
    ws = domain_manager.get_world_state(channel_id) or {}
    bag = ws.get(key)
    if not isinstance(bag, dict):
        bag = {}
    try:
        turn = int(ws.get("turn_index", 0) or 0)
    except (TypeError, ValueError):
        turn = 0
    n = 0
    for row in rows:
        if not isinstance(row, dict):
            continue
        nm = str(row.get("name", "") or "").strip()
        evidence = str(row.get("evidence", "") or "").strip()
        if nm not in known:
            logger.debug("[Expr] %s 신고 폐기(후보 아님): %r", kind, row)
            continue
        if not row.get(flag):
            continue
        if len(evidence) < 2:
            logger.info("[Expr] %s %s 근거 없음 → 폐기 (%r)", kind, nm, row)
            continue
        bag[nm] = {"evidence": evidence[:200], "turn": turn}
        n += 1
    if n:
        ws[key] = bag
        domain_manager.update_world_state(channel_id, ws)
    return n


def queue_cues(channel_id: str, cues: Any) -> int:
    """`[{"name","hit","evidence"}]` → `pending_cue_hits`. 소비는 다음 턴 ⑤."""
    return _queue(channel_id, PENDING_CUE_HITS_KEY, cues, "narrated", "hit")


def queue_operations(channel_id: str, ops: Any) -> int:
    """`[{"name","attempted","evidence"}]` → `pending_operation_hits`. 소비는 다음 턴 ④."""
    return _queue(channel_id, PENDING_OP_HITS_KEY, ops, "operation", "attempted")


def operation_write_targets(channel_id: str, names: Optional[Any] = None) -> Set[str]:
    """연산이 소유한 쓰기 대상 이름 — `apply_deltas` 가 이 집합의 LLM 델타를 폐기한다.

    이중 차감 방지(P5): 같은 사건을 코드가 한 번(연산), 모델이 한 번(델타) 미는 걸 막는다.
    이름은 **밑동**이다 — 델타 스키마가 부르는 이름이 선언 이름이기 때문이다.

    [2026-09-24 감사 §5-2 #18a] `names` = **이번 추출에서 시도 신고된 연산 이름**. 주면 그 연산들의 쓰기
    대상만 돌려준다. 전엔 선언된 연산 전부의 대상이 **영구히** 막혀, 요리 연산이 `체력`을 쓰면 요리와 무관한
    턴의 "맞았다 −3"도 버려졌다. 이중 차감은 같은 사건 = 같은 추출에서만 생긴다. None 이면 종전(전부).
    """
    _only = None if names is None else {str(n).strip() for n in names if str(n or "").strip()}
    out: Set[str] = set()
    for nm, rec in _ordered(list_transitions(channel_id)):
        if _infer_trigger(rec) != "operation":
            continue
        if _only is not None and nm not in _only:
            continue
        for key in ("do", "on_fail"):
            src = rec.get(key)
            if not src:
                continue
            try:
                out |= compile_expr(src, "exec").writes
            except ExprError as e:
                logger.debug("[Expr] 연산 %s.%s 쓰기 대상 스킵: %s", nm, key, e)
    return out


ROUTER_KIND_LABEL = {"value": "값", "narrative": "서술", "format": "형식"}
ROUTER_SURFACE_LABEL = {"header": "헤더", "panel": "패널", "mail": "도착물",
                        "notebook": "노트북"}
ROUTER_CADENCE_LABEL = {"turn": "매턴", "slot": "시간대", "day": "하루", "week": "주",
                        "month": "달", "on_transition": "전이 시", "on_demand": "요청 시"}


def _router_line(nm: str, kind: str, item: Dict[str, Any]) -> str:
    """`이름 · 종류 · 자리 · 때 · 식 원문` — 값·서술·형식 세 종(§3.4 되비침 형식).

    마지막 칸은 **원문**이다: 값이면 범위·파생식, 서술이면 연속성 규칙, 형식이면 지시문
    첫머리. 승인은 유저 눈이지 코드의 요약이 아니라, 자르는 것 말고는 손대지 않는다.
    """
    surface = ROUTER_SURFACE_LABEL.get(str(item.get("surface", "")), "—")
    cadence = ROUTER_CADENCE_LABEL.get(str(item.get("cadence", "turn")), "매턴")
    tail = ""
    if kind == "value":
        v = item.get("value") or {}
        bits = [str(v.get("type", "gauge"))]
        if v.get("range"):
            bits.append(f"{v['range'][0]}-{v['range'][1]}")
        if v.get("stages"):
            bits.append("/".join(str(x) for x in v["stages"]))
        # [2026-09-09 P11] 레코드 목록은 **필드 수**가 승인의 핵심이다 — 항목 하나가 몇 개의
        #   값을 드는지 보이지 않으면 유저는 이걸 옛 수량 목록과 구별하지 못한다.
        if v.get("fields"):
            bits.append(f"필드 {len(v['fields'])}")
            if v.get("stage_of"):
                bits.append(f"단계={v['stage_of']}")
        if v.get("start") is not None:
            bits.append(f"시작 {v['start']}")
        if v.get("derive"):
            bits.append(f"= {v['derive']}")
        scope = str(item.get("scope", "global"))
        if scope != "global":
            bits.append(scope)
        tail = " ".join(bits)
    elif kind == "narrative":
        n = item.get("narrative") or {}
        tail = str(n.get("continuity_rule", "") or "")
        if n.get("lines"):
            tail += f" ({n['lines']}줄)"
    else:
        tail = str((item.get("format") or {}).get("text", "") or "")
    tail = " ".join(tail.split())
    if len(tail) > 120:
        tail = tail[:117] + "…"
    return f"{nm} · {ROUTER_KIND_LABEL[kind]} · {surface} · {cadence} · {tail}"


def format_registration(item: Any) -> str:
    """되비침 한 줄 — `이름 · 종류 · 자리 · 때 · 식 원문`(§3.4).

    라우터(P6)가 "이렇게 읽었다" 블록에 쓰는 형식. 이번 단계에선 register_* 의 msg 로만 산다.
    식은 **원문 그대로** 찍는다 — 승인은 유저 눈이지 코드의 요약이 아니다.
    """
    if not isinstance(item, dict):
        return str(item)
    nm = str(item.get("name", "") or "")
    # [2026-09-06 P6] 라우터 항목(kind 칸이 있는 dict)도 **같은 형식**으로 찍는다 —
    #   되비침 줄을 두 곳에서 만들면 두 모양이 되고, 유저는 그걸 두 기능으로 읽는다.
    _kind = str(item.get("kind", "") or "")
    if _kind in ("value", "narrative", "format"):
        return _router_line(nm, _kind, item)
    # [2026-09-13 P16] 지시 — `이름 · 지시(조건) · 조건식 → 문장`. 문장은 **원문 그대로**다:
    #   승인은 유저 눈이고, 그 줄이 곧 매턴 산문에 실릴 그 줄이다.
    if _kind == "directive" or ("text" in item and "when" in item and "do" not in item):
        # 라우터 항목은 칸이 서브dict(`item["directive"]`)에 있고, 등록 엔트리는 평평하다.
        #   둘 다 **같은 한 줄**로 찍는다 — 되비침이 두 모양이면 유저는 두 기능으로 읽는다.
        _sub = item.get("directive") if isinstance(item.get("directive"), dict) else {}
        _w = str(item.get("when") or _sub.get("when") or "")
        _t = str(item.get("text") or _sub.get("text") or item.get("raw") or "")
        return f"{nm} · 지시(조건) · 산문 · 조건 참인 동안 · {_w} → {_t}"
    if "expr" in item and "when" not in item:
        return f"{nm} · 파생값 · 값 · 매턴 · {item.get('expr', '')}"
    kind = "전이"
    surface = {"mail": "도착물", "mind": "속마음", "none": "—"}.get(
        str(item.get("notify", "mail")), "도착물")
    when = "전이 시"
    if item.get("hold_turns"):
        when += f"(지속 {item['hold_turns']}턴)"
    if str(item.get("cadence")) == "day":
        when = "하루 1회" + (f"(지속 {item['hold_days']}일)" if item.get("hold_days") else "")
    bits = []
    if item.get("when"):
        bits.append(str(item["when"]))
    if item.get("narrated_cue"):
        bits.append(f'큐 "{item["narrated_cue"]}"')
    if item.get("do"):
        bits.append(f"→ {item['do']}")
    if item.get("on_fail"):
        bits.append(f"실패 → {item['on_fail']}")
    if isinstance(item.get("deliver"), dict):
        _d = item["deliver"]
        _who = str(_d.get("from") or "").strip()
        _br = str(_d.get("brief") or "").strip()
        # 💌 = "도착물이 온다"는 표식(§1.5). 종류는 그 옆 라벨로 — 개인/공개.
        _lbl = {"letter": "개인", "bulletin": "공개", "sns": "공개"}.get(
            str(_d.get("kind")), "개인")
        bits.append(f"💌 {_lbl} {(_who + ' ') if _who else ''}{_br}".strip())
    if str(item.get("check")) == "judgment":
        bits.append("[판정]")
    if item.get("once"):
        bits.append("[1회]")
    return f"{nm} · {kind} · {surface} · {when} · " + " ".join(bits)


# =========================================================
# 5. 실행 — run_turn (orchestration 4.7, boundary 뒤 · build_prompt 앞)
# =========================================================
# 순서는 스펙 §3.4 가 못 박았다: ② derive → ③ 경계 틱(P2, 이 함수 **밖**·앞) →
#   ④ operations → ⑤ transitions → ⑥ derive. 번호를 지우지 마라 — 재현성이 이 줄에 걸려 있다.
# 콜 0. 이 함수 안에서 LLM 은 한 번도 불리지 않는다(그래서 렌더 지연이 없다).

try:                                        # 스모크가 갈아 끼울 수 있게 모듈 전역으로 둔다
    from background_task_queue import wait_for_channel_tasks
except Exception:                           # pragma: no cover
    async def wait_for_channel_tasks(channel_id, timeout=30.0):  # type: ignore
        return True


def _tstate(world: Dict[str, Any]) -> Dict[str, Any]:
    st = world.get(TRANSITION_STATE_KEY)
    if not isinstance(st, dict):
        st = {}
        world[TRANSITION_STATE_KEY] = st
    return st


def _save_tstate(channel_id: str, name: str, st: Dict[str, Any]) -> None:
    """전이 한 칸의 런타임 상태만 저장. **값 쓰기가 끝난 뒤에** 부른다."""
    ws = domain_manager.get_world_state(channel_id) or {}
    state = _tstate(ws)
    # [2026-09-24 감사] 전이마다 무조건 save_domain(세션 JSON 전체 + SQLite 미러)이었다 — 한가한
    #   턴에도 전이 N개 = 전체 저장 N번. 저장분과 같으면 쓰기 결과가 같으므로 건너뛴다.
    if state.get(name) == st:
        return
    state[name] = st
    ws[TRANSITION_STATE_KEY] = state
    domain_manager.update_world_state(channel_id, ws)


def _judgment_of(ctx: Any) -> str:
    """4.7 에서 판정 결과에 닿는 경로.

    §0 재확인 (1)의 답: `bus.judgment` 는 `process_une_logic` **로컬**이라 4.7 에선 안 보인다.
    그래서 그 함수가 결과 문자열 하나만 `ctx.judgment_result` 로 실어 넘긴다(한 줄).
    """
    for attr in ("judgment_result", "judgment"):
        v = getattr(ctx, attr, None)
        if isinstance(v, dict):
            v = v.get("result")
        if v:
            return str(v).strip().lower()
    return ""


_JUDGE_PASS = ("success", "critical_success", "성공", "대성공")
_JUDGE_FAIL = ("failure", "critical_failure", "partial", "실패", "대실패", "부분 성공")


def _precheck(res: "Resolver", do_src: Any) -> str:
    """재고 사전 검사 — `do` 의 모든 `-=` 대상이 0 밑으로 안 가는가. 사유 문자열(없으면 "").

    ★모델은 "시도했나"만 신고한다. **성공/실패는 코드**고, 그 첫 관문이 여기다 —
      재료가 모자란 시도는 판정에 가기 전에 끝난다(값 무변화 + 사유 한 줄).
    """
    if not do_src:
        return ""
    ev = _Evaluator(res, src=str(do_src))
    lacks: List[str] = []
    for base, key, fld, vnode in _op_requirements(do_src):
        try:
            need = ev.ev(vnode)
            cur = res.read_for_write(base, key, fld)   # [2026-09-24 감사] 필드까지(위 _op_requirements)
        except ExprError:
            continue                      # 못 읽는 자리는 사전 검사 밖(실행이 제 사유로 죽는다)
        try:
            need_n = float(need)
            cur_n = float(cur)
        except (TypeError, ValueError):
            continue                      # 단계형처럼 수가 아닌 값엔 하한 개념이 없다
        if cur_n - need_n < 0:
            lacks.append(f"{key or base}{(' ' + fld) if fld else ''} {_tidy(need_n)} 필요, 현재 {_tidy(cur_n)}")
    return " · ".join(lacks)


def run_operations(channel_id: str, ctx: Any = None) -> List[Dict[str, Any]]:
    """④ 연산 신고분 집행 — 사전 검사 → 판정 → do/on_fail (P5, 스펙 ④-3).

    한 턴에 도는 것: `pending_operation_hits` 에 이름이 든 `trigger="operation"` 전이뿐이다.
    큐는 **처리 여부와 무관하게 이번 턴에 비운다** — 신고는 그 턴의 사실이지 미결 과제가 아니다.
    """
    ws = domain_manager.get_world_state(channel_id) or {}
    raw_hits = ws.get(PENDING_OP_HITS_KEY)
    hits = dict(raw_hits) if isinstance(raw_hits, dict) else {}
    try:
        turn = int(ws.get("turn_index", 0) or 0)
    except (TypeError, ValueError):
        turn = 0

    done: List[Dict[str, Any]] = []
    for nm, rec in _ordered(list_transitions(channel_id)):
        if _infer_trigger(rec) != "operation":
            continue
        st: Dict[str, Any] = {}
        try:
            state = _tstate(domain_manager.get_world_state(channel_id) or {})
            raw = state.get(nm) if isinstance(state.get(nm), dict) else {}
            st = {"last": bool(raw.get("last")), "fired_at": raw.get("fired_at"),
                  "hold_until": raw.get("hold_until"),
                  "fire_count": int(raw.get("fire_count", 0) or 0)}

            hold_until = st.get("hold_until")
            if hold_until is not None and turn <= int(hold_until):
                if rec.get("do"):           # 지속 중 — do 반복, 발화 0 (⑤ 와 같은 규율)
                    exec_expr(compile_expr(rec["do"], "exec"),
                              Resolver(channel_id, ctx, label=nm))
                _save_tstate(channel_id, nm, st)
                continue
            if hold_until is not None and turn > int(hold_until):
                st["hold_until"] = None

            if nm not in hits:
                _save_tstate(channel_id, nm, st)
                continue
            if rec.get("once") and st["fire_count"] > 0:
                _save_tstate(channel_id, nm, st)
                continue
            if rec.get("when") and not eval_when(channel_id, rec["when"], ctx, nm):
                _save_tstate(channel_id, nm, st)
                continue

            res = Resolver(channel_id, ctx, label=nm)
            lack = _precheck(res, rec.get("do"))
            if lack:
                # 값은 안 움직인다. 사유만 한 줄 — "왜 안 됐나"가 값 옆에 있어야 한다.
                kind = str(rec.get("notify", "mail") or "mail")
                _ws_append(channel_id, PENDING_MAILS_KEY,
                           {"title": nm, "body": lack, "author": "",
                            "kind": kind if kind in ("mail", "mind") else "mail",
                            "format_name": str(rec.get("notify_format") or "")})
                logger.info("[Expr] 연산 %s 사전 검사 불통과: %s", nm, lack)
                _save_tstate(channel_id, nm, st)
                continue

            _fire(channel_id, nm, rec, ctx, turn)
            st["fire_count"] += 1
            st["fired_at"] = turn
            hold = int(rec.get("hold_turns", 0) or 0)
            if hold > 0:
                st["hold_until"] = turn + hold
            _save_tstate(channel_id, nm, st)
            done.append({"name": nm})
        except ExprError as e:
            logger.warning("[Expr] 연산 %s 스킵: %s", nm, e)
            try:
                _save_tstate(channel_id, nm, st)
            except Exception:
                pass
        except Exception as e:                 # 한 연산의 기형이 턴을 죽이지 않는다
            logger.warning("[Expr] 연산 %s 실패: %s", nm, e)
            # [2026-09-24 감사] ExprError 갈래만 상태를 저장했다 — 일반 예외도 같은 규율(st 가
            #   세워진 뒤라면). 빈 st 는 쓰지 않는다(발화 이력을 {} 로 덮지 않게).
            if st:
                try:
                    _save_tstate(channel_id, nm, st)
                except Exception:
                    pass

    if hits:
        _ws_set(channel_id, PENDING_OP_HITS_KEY, {})
    return done


def _take_cue_hit(channel_id: str, name: str) -> Optional[Dict[str, Any]]:
    """`pending_cue_hits` 에서 한 칸을 **꺼낸다**(읽고 지운다). `_ws_set` 규율."""
    ws = domain_manager.get_world_state(channel_id) or {}
    bag = ws.get(PENDING_CUE_HITS_KEY)
    if not isinstance(bag, dict) or name not in bag:
        return None
    row = bag.pop(name)
    ws[PENDING_CUE_HITS_KEY] = bag
    domain_manager.update_world_state(channel_id, ws)
    return row if isinstance(row, dict) else {}


def _derive_pass(channel_id: str, res: Resolver, tag: str) -> int:
    """② / ⑥ 파생값 재계산 — order 순 1패스. 한 항목의 실패가 나머지를 죽이지 않는다."""
    rows = _ordered(list_derives(channel_id))
    if not rows:
        return 0
    moved = 0
    for nm, rec in rows:
        try:
            res.invalidate()
            c = compile_expr(rec.get("expr"), "eval")
            val = eval_expr(c, res)
            r2 = Resolver(channel_id, res.ctx, label=f"derive {nm}")
            hit = r2.cv.apply_code_write(channel_id, nm, value=val,
                                         evidence=f"expr:derive {nm}", source="expr",
                                         turn=res.turn_index(), actor=res.actor())
            if hit:
                moved += 1
        except ExprError as e:
            logger.warning("[Expr] 파생값 %s 스킵(%s): %s", nm, tag, e)
        except Exception as e:                     # 한 항목의 기형이 턴을 죽이지 않는다
            logger.warning("[Expr] 파생값 %s 실패(%s): %s", nm, tag, e)
    res.invalidate()
    return moved


def _ws_set(channel_id: str, key: str, value: Any) -> None:
    """world_state 한 칸만 **읽는 즉시** 고쳐 쓴다.

    ⚠ 계약: `domain_manager.get_world_state` 는 캐시의 **deep copy** 를 돌려준다
      (cache_manager.get_session). 그래서 world_state dict 를 값 쓰기 너머로 들고 다니면
      뒤늦은 저장이 그 사이의 쓰기를 통째로 되돌린다 — 09-05 lost update 카드와 같은 병이다.
      여기서 매번 다시 읽는 것이 그 병의 처방이고, 이 함수 밖에서 world 를 캐싱하지 마라.
    """
    ws = domain_manager.get_world_state(channel_id) or {}
    ws[key] = value
    domain_manager.update_world_state(channel_id, ws)


def _ws_append(channel_id: str, key: str, row: Any) -> None:
    ws = domain_manager.get_world_state(channel_id) or {}
    rows = ws.get(key)
    if not isinstance(rows, list):
        rows = []
    rows.append(row)
    ws[key] = rows
    domain_manager.update_world_state(channel_id, ws)


def _cooldown_ok(rec: Dict[str, Any], st: Dict[str, Any], now: int, edge: bool) -> bool:
    """[2026-09-13 P17] 발화 뒤 `cooldown` cadence 동안 엣지를 삼킨다. 새 상태 칸 0 —
    마지막 발화 시각(`fired_at`)은 hold·once 가 이미 쓰던 칸이다."""
    if not edge:
        return False
    try:
        cd = int(rec.get("cooldown", 0) or 0)
    except (TypeError, ValueError):
        return True
    if cd <= 0:
        return True
    last = st.get("fired_at")
    if last is None:
        return True
    try:
        if int(now) - int(last) < cd:
            logger.debug("[Expr] %s cooldown %s — 엣지 억제 (last=%s now=%s)",
                         rec.get("name", ""), cd, last, now)
            return False
    except (TypeError, ValueError):
        return True
    return True


def _run_every(channel_id: str, name: str, rec: Dict[str, Any], ctx: Any) -> None:
    """[2026-09-13 P17] 매 cadence 무조건 연산 — `do` 만 돈다. 발화가 아니라 계산이라
    알림·사건 줄·기록이 없다. 범위 클램프는 종전대로 쓰기 문이 문다."""
    src = rec.get("do")
    if not src:
        return
    exec_expr(compile_expr(src, "exec"), Resolver(channel_id, ctx, label=name))


def _fire(channel_id: str, name: str, rec: Dict[str, Any], ctx: Any,
          turn: int) -> None:
    """발화 한 번 — do/on_fail 적용 + 알림 + 사건 줄 + 기록. 실패는 호출자가 격리한다."""
    res = Resolver(channel_id, ctx, label=name)
    branch = "do"
    if str(rec.get("check")) == "judgment":
        jr = _judgment_of(ctx)
        if not jr:
            raise ExprError(f"판정 결과가 없습니다 — {name} 스킵")
        branch = "do" if jr in _JUDGE_PASS else "on_fail"
    src = rec.get(branch)
    if src:
        exec_expr(compile_expr(src, "exec"), res)

    # 알림(도착물) — 전송 뒤 flush_mails 가 한 통으로 묶는다(같은 (message,kind) 교체 규칙).
    notify = str(rec.get("notify", "mail") or "mail")
    body = _event_line(name, rec, res)
    # [2026-09-13 P14] `deliver` 가 있으면 그 행 하나가 **한 줄 알림을 대체한다**(추가가 아니다).
    #   같은 사건을 봉투 둘로 내면 유저는 두 사건으로 읽는다. 이 행은 flush 가 아니라
    #   산문 **앞** 핸드아웃 파이프라인(world_board.pick_arrival_request)이 집어 간다.
    _dlv = rec.get("deliver")
    if isinstance(_dlv, dict):
        _ws_append(channel_id, PENDING_MAILS_KEY,
                   {"title": name, "body": body, "author": str(_dlv.get("from") or ""),
                    "kind": notify if notify in ("mail", "mind") else "mail",
                    "format_name": str(rec.get("notify_format") or ""),
                    # [2026-09-24 감사] 원 notify 동봉 — 핸드아웃이 무산돼 한 줄 알림으로 되돌릴 때 "none"이면 조용히 둔다.
                    "notify": notify,
                    "deliver": dict(_dlv)})
    elif notify in ("mail", "mind"):
        _ws_append(channel_id, PENDING_MAILS_KEY,
                   {"title": name, "body": body, "author": "", "kind": notify,
                    "format_name": str(rec.get("notify_format") or "")})

    # 사건 줄 — 다음 렌더에 1턴만. 검출≠쓰기: 사실만 주고 쓰라고 하지 않는다.
    _ws_append(channel_id, PENDING_EVENTS_KEY, body)

    _record(channel_id, name, rec, turn)


def _event_line(name: str, rec: Dict[str, Any], res: Resolver) -> str:
    """`데이트 해금 (호감도[리나] >= 70)` — 이름 + 무엇이 참이 됐나. 템플릿 한 줄."""
    why = str(rec.get("when") or rec.get("narrated_cue") or "").strip()
    return f"{name} ({why})" if why else name


def _record(channel_id: str, name: str, rec: Dict[str, Any], turn: int) -> None:
    """`record.notebook_section` 이 있으면 행 + 채널 섹션 줄. 노출은 P4 이후."""
    r = rec.get("record")
    if not isinstance(r, dict):
        return
    section = str(r.get("notebook_section", "") or "").strip()
    if not section:
        return
    content = f"[t{turn}] {name}"
    try:
        import sqlite_store
        sqlite_store.append_notebook_log(channel_id, "", section, content)
    except Exception as e:
        logger.debug("[Expr] notebook_log append skip: %s", e)
    try:
        shared = domain_manager.get_notebook_shared(channel_id) or {}
        sections = shared.setdefault("sections", {})
        sec = sections.setdefault(section, {})
        lines = sec.get("lines")
        if not isinstance(lines, list):
            lines = []
        lines.append(content)
        cap = int(getattr(config, "NOTEBOOK_JOURNAL_MAX", 10) or 10)
        sec["lines"] = lines[-cap:] if cap > 0 else lines
        sections[section] = sec
        d = domain_manager.get_domain(channel_id)
        d["notebook_shared"] = shared
        domain_manager.save_domain(channel_id, d)
    except Exception as e:
        logger.debug("[Expr] notebook_shared append skip: %s", e)


def run_day(channel_id: str, ctx: Any = None,
            info: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """[2026-09-09 P11] `cadence=day` 전이 정산 — **경계 틱 구독자**로만 불린다.

    콜 0 · 동기. 하루가 넘어간 그 턴에 한 번, `boundary_engine.on_turn` 이 마커를 찍기 **전에**
    돈다(4.7 안에서 boundary 가 expr 보다 앞이다 — `orchestration` 2091 / 2104).

    매턴 ⑤ 와 다른 곳은 둘뿐이다:
      · `when` 이 비면 **무조건 발화**한다(하루가 지났다는 사실 자체가 조건이다).
      · 지속은 `hold_days` — 턴이 아니라 **날짜**로 센다. 지속 중에는 `do` 만 반복하고
        발화(사건 줄·도착물)는 하지 않는다. 매턴 지속(`hold_turns`)과 같은 규율이다.
    """
    out = {"fired": 0, "errors": 0}
    res = Resolver(channel_id, ctx)
    turn = res.turn_index()
    try:
        # [2026-09-24 감사] 시계가 `일`(달 안 날짜 1-30)이었다 — 달을 넘으면 30→1 로 되감겨
        #   `hold_until_day`(예: 29+3=32)가 영영 안 풀리고 cooldown 도 음수 차로 뚫렸다.
        #   누적 일수 `날`(expire 와 같은 시계)로 센다.
        today = int(res._code_read("날"))
    except Exception:
        today = 0
    for nm, rec in _ordered(list_transitions(channel_id)):
        if str(rec.get("cadence", "turn")) != "day":
            continue
        st: Dict[str, Any] = {}
        try:
            state = _tstate(domain_manager.get_world_state(channel_id) or {})
            raw = state.get(nm) if isinstance(state.get(nm), dict) else {}
            st = {"last": bool(raw.get("last")), "fired_at": raw.get("fired_at"),
                  "hold_until": raw.get("hold_until"),
                  "hold_until_day": raw.get("hold_until_day"),
                  "fire_count": int(raw.get("fire_count", 0) or 0)}

            hud = st.get("hold_until_day")
            if hud is not None and today <= int(hud):
                if rec.get("do"):
                    exec_expr(compile_expr(rec["do"], "exec"),
                              Resolver(channel_id, ctx, label=nm))
                _save_tstate(channel_id, nm, st)
                continue
            if hud is not None and today > int(hud):
                st["hold_until_day"] = None

            trig = _infer_trigger(rec)
            if trig == "operation":
                continue
            # [2026-09-13 P17] 매턴 ⑤ 와 같은 규율 — `when` 이 없으면 **매일 do 만** 돈다.
            # [2026-09-24 감사] 트리거 검사 없이 돌아 when 없는 narrated 전이(큐가 문)의 do 가
            #   매일 무조건 실행됐다. run_turn 처럼 expr 로 한정 — narrated 는 여기서 건너뛴다.
            if not rec.get("when"):
                if trig == "expr":
                    _run_every(channel_id, nm, rec, ctx)
                    st["last"] = True
                _save_tstate(channel_id, nm, st)
                continue
            cur = eval_when(channel_id, rec["when"], ctx, nm)
            edge = cur and not st["last"]
            edge = _cooldown_ok(rec, st, today, edge)
            # [2026-09-24 감사] check=judgment 인데 판정 결과가 없으면 _fire 가 ExprError 로 스킵하기
            #   **전에** last=cur 가 서서 엣지가 소비됐다(조건이 계속 참이면 영영 재발화 없음).
            #   판정 부재 스킵은 last 를 갱신하지 않는다 — 판정이 있는 날 같은 엣지로 다시 연다.
            if (edge and (not rec.get("once") or st["fire_count"] == 0)
                    and str(rec.get("check")) == "judgment" and not _judgment_of(ctx)):
                logger.debug("[Expr] day 전이 %s 판정 결과 없음 — 엣지 보류", nm)
                _save_tstate(channel_id, nm, st)
                continue
            st["last"] = cur

            if edge and (not rec.get("once") or st["fire_count"] == 0):
                _fire(channel_id, nm, rec, ctx, turn)
                st["fire_count"] += 1
                # day cadence 의 `fired_at` 은 **그 전이의 시계값**(오늘)이다 — cooldown 이
                # 날 단위로 세려면 마지막 발화를 같은 단위로 들고 있어야 한다(새 칸 0).
                st["fired_at"] = today
                hd = int(rec.get("hold_days", 0) or 0)
                if hd > 0:
                    st["hold_until_day"] = today + hd
                out["fired"] += 1
            _save_tstate(channel_id, nm, st)
        except ExprError as e:
            out["errors"] += 1
            logger.warning("[Expr] day 전이 %s 스킵: %s", nm, e)
            try:
                _save_tstate(channel_id, nm, st)
            except Exception:
                pass
        except Exception as e:
            out["errors"] += 1
            logger.warning("[Expr] day 전이 %s 실패: %s", nm, e)
            # [2026-09-24 감사] 일반 예외도 엣지 상태를 저장한다 — 안 그러면 부분 적용된 do 가
            #   last 미갱신으로 다음 틱마다 재발화한다. 빈 st 는 쓰지 않는다.
            if st:
                try:
                    _save_tstate(channel_id, nm, st)
                except Exception:
                    pass
    if out["fired"] or out["errors"]:
        logger.info("[Expr] day=%s 발화 %s · 실패 %s", today, out["fired"], out["errors"])
    return out


async def run_turn(channel_id: str, ctx: Any = None) -> Dict[str, Any]:
    """4.7 진입점. 반환은 요약 dict(로그·스모크용) — 예외는 이 함수가 삼킨다.

    자리 근거(§0.6): Judgment 결과·[소지품] 적용·날짜 전진이 **다 끝난 뒤**여야 하고,
    Slot 29(`build_prose_feed`)가 **읽기 전**이어야 한다. 그래서 boundary 뒤·build_prompt 앞.
    """
    out = {"derives": 0, "fired": 0, "errors": 0}

    # 0. 배경 큐 drain (§0.7 e) — N턴 배경(apply_deltas·시간 전진)이 N+1턴 4.7 보다 늦게
    #    도는 게 가능하다. 값 시차 1턴은 재료로는 무해하지만 **동시 쓰기**는 lost update 다.
    #    타임아웃이면 경고 1줄 + 진행 — 렌더를 잡지 않는 것이 우선이다.
    try:
        drained = await wait_for_channel_tasks(
            channel_id, getattr(config, "EXPR_DRAIN_TIMEOUT", 3.0))
        if drained is False:
            logger.warning("[Expr] 배경 큐 drain 타임아웃 — 시차 수용하고 진행 (%s)", channel_id)
    except Exception as e:
        logger.debug("[Expr] drain skipped: %s", e)

    res = Resolver(channel_id, ctx)
    turn = res.turn_index()

    # ② derive 재계산 (LLM 델타는 이미 적용돼 있다 — 배경 큐 ①)
    try:
        out["derives"] += _derive_pass(channel_id, res, "②")
    except Exception as e:
        out["errors"] += 1
        logger.warning("[Expr] ② derive 실패: %s", e)

    # ④ operations — 이번 단계는 빈 슬롯(P5). 자리만 잡는다.
    try:
        run_operations(channel_id, ctx)
    except Exception as e:
        out["errors"] += 1
        logger.warning("[Expr] ④ operations 실패: %s", e)

    # ⑤ transitions — order 순. **한 전이의 실패는 그 항목만** 죽인다.
    #   world_state 를 값 쓰기 너머로 들고 다니지 않는다(_ws_set 주석 참조) —
    #   전이 하나를 다 처리한 **뒤에** 그 전이의 상태 한 칸만 다시 읽어 쓴다.
    for nm, rec in _ordered(list_transitions(channel_id)):
        if str(rec.get("cadence", "turn")) == "day":
            continue                        # [P11] 하루 1회 — 경계 틱(run_day)의 몫이다
        # [2026-09-24 감사] 루프마다 초기화 — 안 하면 st 가 세워지기 전 예외에서 **앞 전이의 st** 가
        #   이 이름으로 저장될 수 있다(아래 예외 갈래가 st 를 저장한다).
        st: Dict[str, Any] = {}
        try:
            # [2026-09-24 감사] operation 판별을 hold 분기 **앞**으로 — hold 분기가 먼저 서서
            #   ④ run_operations 가 이미 돈 지속 do 를 여기서 한 번 더 돌렸다(턴당 이중 차감).
            trig = _infer_trigger(rec)
            if trig == "operation":
                continue                    # ④ 가 이미 돌렸다(같은 전이를 두 번 돌리지 않는다)

            state = _tstate(domain_manager.get_world_state(channel_id) or {})
            raw = state.get(nm) if isinstance(state.get(nm), dict) else {}
            st = {"last": bool(raw.get("last")), "fired_at": raw.get("fired_at"),
                  "hold_until": raw.get("hold_until"),
                  "fire_count": int(raw.get("fire_count", 0) or 0)}

            hold_until = st.get("hold_until")
            if hold_until is not None and turn <= int(hold_until):
                # 지속 중 — do 를 반복하고 **발화는 하지 않는다**(P8 트라우마 dwell 전제).
                if rec.get("do"):
                    exec_expr(compile_expr(rec["do"], "exec"),
                              Resolver(channel_id, ctx, label=nm))
                _save_tstate(channel_id, nm, st)
                continue
            if hold_until is not None and turn > int(hold_until):
                st["hold_until"] = None

            # [2026-09-06 P4] narrated 갈래 — `when` 대신 **이번 턴 큐**가 문을 연다.
            #   큐는 사건이라 레벨 엣지(거짓→참)가 아니다: 온 턴에만 발화하고, `when` 이
            #   함께 있으면 AND 다. 큐는 읽는 즉시 소비된다.
            # [2026-09-13 P17] `when` 이 없는 expr 전이 = **매 cadence 무조건 연산**
            #   (SimCore onTurn). 조건이 없으니 엣지도 once 도 hold 도 없고, 발화(사건 줄·
            #   도착물·기록)도 없다 — 사건이 아니라 계산이다. 매턴 봉투를 내면 유저는
            #   "매턴 같은 편지"를 받는다.
            if trig == "expr" and not rec.get("when"):
                _run_every(channel_id, nm, rec, ctx)
                st["last"] = True
                _save_tstate(channel_id, nm, st)
                continue

            cue_hit = _take_cue_hit(channel_id, nm) if trig == "narrated" else None
            cur = (trig == "narrated")
            if rec.get("when"):
                cur = eval_when(channel_id, rec["when"], ctx, nm)
            if trig == "narrated":
                edge = bool(cue_hit) and cur
            else:
                edge = cur and not st["last"]
            edge = _cooldown_ok(rec, st, turn, edge)
            # [2026-09-24 감사] check=judgment 인데 이번 턴 판정 결과가 없으면 _fire 가 ExprError 로
            #   스킵하기 **전에** last=cur 가 서서 엣지가 소비됐다(조건이 참으로 머물면 영영 재발화 없음).
            #   판정 부재 스킵은 last 를 갱신하지 않는다 — 판정이 있는 턴에 같은 엣지로 다시 연다.
            if (edge and (not rec.get("once") or st["fire_count"] == 0)
                    and str(rec.get("check")) == "judgment" and not _judgment_of(ctx)):
                logger.debug("[Expr] 전이 %s 판정 결과 없음 — 엣지 보류", nm)
                _save_tstate(channel_id, nm, st)
                continue
            st["last"] = cur

            if edge and (not rec.get("once") or st["fire_count"] == 0):
                _fire(channel_id, nm, rec, ctx, turn)
                st["fire_count"] += 1
                st["fired_at"] = turn
                hold = int(rec.get("hold_turns", 0) or 0)
                if hold > 0:
                    st["hold_until"] = turn + hold
                out["fired"] += 1
            _save_tstate(channel_id, nm, st)
        except ExprError as e:
            out["errors"] += 1
            logger.warning("[Expr] 전이 %s 스킵: %s", nm, e)
            if st:                              # [2026-09-24 감사] 빈 st 로 이력을 덮지 않는다
                try:
                    _save_tstate(channel_id, nm, st)
                except Exception:
                    pass
        except Exception as e:
            out["errors"] += 1
            logger.warning("[Expr] 전이 %s 실패: %s", nm, e)
            # [2026-09-24 감사] 일반 예외(IndexError 등)는 상태를 안 저장해 last 가 거짓으로 남고,
            #   do 의 앞부분만 적용된 전이가 **매 턴** 다시 발화했다 — ExprError 갈래와 같은 규율.
            if st:
                try:
                    _save_tstate(channel_id, nm, st)
                except Exception:
                    pass

    # 큐 청소 — 남은 서술 신고는 이번 턴의 사실이지 미결 과제가 아니다(한 턴 수명).
    try:
        if (domain_manager.get_world_state(channel_id) or {}).get(PENDING_CUE_HITS_KEY):
            _ws_set(channel_id, PENDING_CUE_HITS_KEY, {})
    except Exception as e:
        logger.debug("[Expr] cue 큐 청소 skip: %s", e)

    # ⑥ derive 재계산 한 번 더 — ④⑤ 가 값을 바꿨으니. Slot 29 는 **이 결과**를 읽는다.
    try:
        out["derives"] += _derive_pass(channel_id, Resolver(channel_id, ctx), "⑥")
    except Exception as e:
        out["errors"] += 1
        logger.warning("[Expr] ⑥ derive 실패: %s", e)

    if out["fired"] or out["errors"]:
        logger.info("[Expr] turn=%s 발화 %s · 파생 %s · 실패 %s",
                    turn, out["fired"], out["derives"], out["errors"])
    return out


# =========================================================
# 6. 알림 flush — 전송 뒤(산문 message_id 확정 뒤)
# =========================================================
# 도착물은 message_id 를 요구한다(같은 (message,kind) 교체) → 렌더 **전**엔 못 보낸다.
# 그래서 발화 시엔 world_state 에 보류하고, 전송 직후 여기서 kind 별로 **한 통**으로 묶는다.
# 한 통인 이유: store_mail 이 같은 (message,kind) 를 교체하므로 두 번 부르면 앞 통이 사라진다.

def take_deliver_requests(channel_id: str) -> List[Dict[str, Any]]:
    """보류 알림에서 **도착물 선언 행만** 뽑아내고 비운다(나머지 행은 그대로 둔다).

    자리가 여기인 이유: 적재자(`_fire`)와 같은 파일이어야 행 모양이 한 곳에 산다.
    소비는 산문 **앞**(world_board.pick_arrival_request) — flush_mails 보다 이르다.
    집히지 않은 채 턴이 끝나면(핸드아웃 경로가 통째로 죽은 경우) flush 가 종전
    한 줄 알림으로 내보낸다 — 값은 이미 움직였고 알림만 모양이 다르다.
    """
    try:
        world = domain_manager.get_world_state(channel_id) or {}
    except Exception:
        return []
    rows = world.get(PENDING_MAILS_KEY)
    if not isinstance(rows, list) or not rows:
        return []
    picked = [r for r in rows if isinstance(r, dict) and isinstance(r.get("deliver"), dict)]
    if not picked:
        return []
    world[PENDING_MAILS_KEY] = [r for r in rows
                                if not (isinstance(r, dict) and isinstance(r.get("deliver"), dict))]
    try:
        domain_manager.update_world_state(channel_id, world)
    except Exception as e:
        logger.debug("[Expr] 도착물 선언 행 비우기 실패: %s", e)
    return picked


async def flush_mails(prose_message: Any, channel_id: str) -> int:
    """보류 알림 → `turn_mail.deliver`. 비면 아무것도 안 한다(버튼 순증 0)."""
    if prose_message is None:
        return 0
    try:
        world = domain_manager.get_world_state(channel_id) or {}
    except Exception:
        return 0
    rows = world.get(PENDING_MAILS_KEY)
    if not isinstance(rows, list) or not rows:
        return 0

    grouped: Dict[str, List[Dict[str, Any]]] = {}
    for m in rows:
        if not isinstance(m, dict):
            continue
        if str(m.get("notify") or "") == "none":
            continue   # [2026-09-24 감사] 알림을 끈 도착물 선언 행이 핸드아웃에 안 집혔을 때 — 한 줄 알림으로도 안 낸다
        grouped.setdefault(str(m.get("kind") or "mail"), []).append(m)

    world[PENDING_MAILS_KEY] = []
    try:
        domain_manager.update_world_state(channel_id, world)
    except Exception as e:
        logger.debug("[Expr] 보류 알림 비우기 실패: %s", e)

    sent = 0
    try:
        import turn_mail
    except Exception as e:
        logger.debug("[Expr] turn_mail 없음: %s", e)
        return 0
    turn = 0
    try:
        turn = int((domain_manager.get_world_state(channel_id) or {}).get("turn_index", 0) or 0)
    except (TypeError, ValueError):
        turn = 0
    for kind, items in grouped.items():
        titles = [str(i.get("title") or "") for i in items if i.get("title")]
        payload = {
            "title": titles[0] if len(titles) == 1 else f"사건 {len(items)}건",
            "body": "\n".join(f"· {i.get('body') or i.get('title') or ''}" for i in items),
            "author": "",
            "recipient": "",
            "channel_kind": "",
            # [2026-09-07 P10] 전이가 지정한 도착물 형식 이름 — 묶음 안 첫 지정이 이긴다
            #   (한 통으로 묶이므로 봉투는 하나뿐이다). 없으면 종전 그대로 빈 칸.
            "format_name": next((str(i.get("format_name") or "") for i in items
                                 if str(i.get("format_name") or "").strip()), ""),
        }
        try:
            if await turn_mail.deliver(prose_message, channel_id, kind, payload, turn):
                sent += 1
        except Exception as e:
            logger.warning("[Expr] 도착물 전달 실패(kind=%s): %s", kind, e)
    return sent
