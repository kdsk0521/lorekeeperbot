# -*- coding: utf-8 -*-
"""output_router.py — 출력물 선언 항목 → 기존 관문 (스펙 v0.2 §3.2~§3.3 · §0.5)

[2026-09-06 P6] 항목 꼴(value · narrative · format · transition · directive)과 관문 매핑의 집.
[2026-11-06 C116 판1 1c] `!출력룰` 은퇴 — 말글 파일을 라우터 콜로 항목으로 자르던 길(route_text)은 걷혔다.
  항목은 이제 코드 앱 `outputs.json` 이 직접 쓴다(app_host.apply_outputs → normalize_items → apply_items). 모델 콜 0.

세 가지 규율이 이 파일의 뼈다:
  ① **관문은 이해하지 않는다, 자리만 정한다.** `format` 항목의 원문은 한 글자도 안 고친다.
  ② **강등은 "문법 밖"만.** 식이 `expr_engine.compile_expr` 을 못 지나면 거부가 아니라
     `narrative`(패널 섹션)로 내려보낸다 — 지켜짐은 잃되 출력은 산다(§0.5 마지막 문단).
     거부(✗)로 남는 것은 "이름 없음"과 "이름 충돌" 둘뿐이다.
  ③ **재등록은 값을 안 죽인다.** 같은 `source` 로 다시 오면 이름 기준 diff: 있는 이름은 선언
     갱신(현재값 보존 — `custom_vars.register` 가 이미 그 계약이다). 앱은 빠진 이름을 지운다(C116 1a — app_host 몫).

discord import 0.
"""
import logging
import time
from typing import Any, Dict, List, Optional, Tuple


logger = logging.getLogger("OutputRouter")

MAX_TOTAL_ITEMS = 120   # 묶음 하나의 항목 상한(넘는 꼬리는 버린다)
NAME_MAX = 24
RULE_MAX = 300
FORMAT_TEXT_MAX = 4000

# [2026-09-13 P16] 다섯째 종류 `directive`(지시) — 조건이 참인 **동안** 산문에 붙는 유저 문장.
#   값을 안 쓰므로 전이 뒤에 선다: 순서는 "값 → 그 값을 읽는 것들" 하나뿐이다.
KINDS = ("value", "narrative", "format", "transition", "directive")
# 적용 순서는 고정이다: 값이 먼저 서야 그 값을 참조하는 전이가 "이름 없음"으로 안 튕긴다(§4 R2).
KIND_ORDER = {k: i for i, k in enumerate(KINDS)}

SURFACES = ("header", "panel", "mail", "notebook")
SCOPES = ("global", "pc", "npc", "user")
CADENCES = ("turn", "slot", "day", "week", "month", "on_transition", "on_demand")
FEED_MODES = ("always", "mentioned", "never")

_KIND_ALIASES = {
    "value": "value", "값": "value", "variable": "value", "변수": "value",
    "narrative": "narrative", "서술": "narrative", "section": "narrative", "패널": "narrative",
    "format": "format", "형식": "format", "prose": "format", "산문": "format",
    "transition": "transition", "전이": "transition", "event": "transition", "사건": "transition",
    "directive": "directive", "지시": "directive", "조건지시": "directive",
    "conditional": "directive",
}
_KIND_LABEL = {"value": "값", "narrative": "서술", "format": "형식",
               "transition": "전이", "directive": "지시(조건)"}

STATUS_MARK = {"ok": "✓", "fail": "✗", "demoted": "↓"}


# ⚰[2026-11-06 C116 판1 1c] 라우터 콜(ROUTER_PROMPT · split_chunks · strip_cbs · known_names · build_router_prompt ·
#   _call_router · route_text) · 통째 초기화(reset_all · reset_preview · format_reset_*) · 명령 판별(route_decision) 삭제 —
#   `!출력룰` 은퇴. 항목 꼴은 그대로: 코드 앱 `outputs.json` 이 이 모듈의 normalize_items → apply_items 를 탄다(app_host.apply_outputs).
#   옛 라우터 프롬프트(항목 칸 정의)는 작성 킷 `04_선언칸_쓰기.md` 로 옮겼다. 옛 본문 = git C116 1b 이전.


# =========================================================
# 2. 순수 함수 — 정규화 · 순서
# =========================================================
def _s(v: Any, limit: int = 0) -> str:
    t = str(v if v is not None else "").strip()
    return t[:limit] if limit and len(t) > limit else t


def _int(v: Any, default: Optional[int] = None) -> Optional[int]:
    try:
        if isinstance(v, bool):
            return default
        return int(v)
    except (TypeError, ValueError):
        return default


def _range(v: Any) -> Optional[List[int]]:
    if isinstance(v, str):
        import re
        m = re.search(r"(-?\d+)\s*[-~:]\s*(-?\d+)", v)
        v = [m.group(1), m.group(2)] if m else None
    if isinstance(v, (list, tuple)) and len(v) == 2:
        lo, hi = _int(v[0]), _int(v[1])
        if lo is not None and hi is not None:
            return [lo, hi]
    return None


def _pick(d: Any, *keys: str) -> Any:
    if not isinstance(d, dict):
        return None
    for k in keys:
        if d.get(k) not in (None, ""):
            return d[k]
    return None


def _enum(v: Any, allowed: Tuple[str, ...], default: str) -> str:
    t = _s(v).lower()
    return t if t in allowed else default


def normalize_items(data: Any, source: str = "") -> List[Dict[str, Any]]:
    """콜 출력 → 닫힌 항목 목록. **폐기는 조용하지 않다**(로그 한 줄).

    폐기 사유는 둘뿐이다: 모르는 kind, 이름 없음. 그 밖의 잘못된 칸은 폐기가 아니라
    기본값으로 접힌다 — 라우터가 칸 하나 틀렸다고 유저의 준비물을 버리면 안 된다.
    """
    rows: Any = data
    if isinstance(data, dict):
        rows = _pick(data, "items", "항목", "list", "results") or []
    if not isinstance(rows, (list, tuple)):
        return []

    out: List[Dict[str, Any]] = []
    dropped_kind = dropped_name = 0
    for raw in rows:
        if not isinstance(raw, dict):
            continue
        kind = _KIND_ALIASES.get(_s(_pick(raw, "kind", "종류")).lower())
        if kind not in KINDS:
            dropped_kind += 1
            continue
        name = _s(_pick(raw, "name", "이름"), NAME_MAX)
        if not name or "|" in name or "\n" in name:
            dropped_name += 1
            continue

        item: Dict[str, Any] = {
            "kind": kind,
            "name": name,
            "surface": _enum(_pick(raw, "surface", "자리"), SURFACES,
                             {"value": "header", "narrative": "panel",
                              "format": "panel", "transition": "mail",
                              "directive": "panel"}[kind]),
            "scope": _enum(_pick(raw, "scope", "스코프"), SCOPES, "global"),
            "cadence": _enum(_pick(raw, "cadence", "때"), CADENCES, "turn"),
            "raw": _s(_pick(raw, "raw", "원문"), RULE_MAX),
            "source": source,
        }
        feed = _pick(raw, "feed", "급식")
        prose = _enum(_pick(feed, "prose") if isinstance(feed, dict) else feed,
                      FEED_MODES, "")
        if prose:
            item["feed"] = {"prose": prose}

        sub = raw.get(kind) if isinstance(raw.get(kind), dict) else {}
        if kind == "value":
            item["value"] = _norm_value(sub, raw)
        elif kind == "narrative":
            item["narrative"] = _norm_narrative(sub, raw, name)
        elif kind == "format":
            item["format"] = _norm_format(sub, raw)
        elif kind == "transition":
            item["transition"] = _norm_transition(sub, raw)
        else:
            item["directive"] = _norm_directive(sub, raw)
        out.append(item)
        if len(out) >= MAX_TOTAL_ITEMS:
            break

    if dropped_kind or dropped_name:
        logger.info("[Router] 폐기 — 모르는 kind %d · 이름 없음 %d (source=%s)",
                    dropped_kind, dropped_name, source)
    return out


def expand_stage_at(first: Any, step: Any, top: Any) -> List[int]:
    """[2026-09-13 P17] 레벨당 필요치 수식 → **경계 목록**. 라우터가 여기서 전개한다.

    경계 k = 경계 k-1 + (first + (k-1)*step) — "레벨마다 +step 씩 더 필요"의 누적형이다.
    `custom_vars.MAX_STAGES` 를 넘지 않고 range 의 천장(top)도 넘지 않는다.
    ★수식을 값 층으로 들여보내지 않는 이유: 단계 판정이 매 표시마다 식을 풀면 경계가
      선언이 아니라 계산이 되고, 그러면 되비침이 "무엇이 레벨 3인가"를 못 적는다.
    """
    import custom_vars as cv
    try:
        f, st, hi = int(first), int(step), int(top)
    except (TypeError, ValueError):
        return []
    if f <= 0:
        return []
    out = [0]
    cur = 0
    for k in range(1, int(getattr(cv, "MAX_STAGES", 8))):
        cur += f + (k - 1) * max(0, st)
        if cur > hi:
            break
        out.append(cur)
    return out if len(out) >= 2 else []


def _norm_value(sub: Dict[str, Any], raw: Dict[str, Any]) -> Dict[str, Any]:
    vtype = _s(_pick(sub, "type", "타입")).lower() or "gauge"
    stages = _pick(sub, "stages", "단계")
    stages = [_s(x, NAME_MAX) for x in stages if _s(x)] if isinstance(stages, (list, tuple)) else None
    # [2026-09-13 P17] 경험치바 — "레벨당 필요 경험치" 수식은 여기서 **경계 목록**이 된다.
    #   리셋 전이(경험치=0, 레벨+1)는 만들지 않는다: 누적이 정본이고 바는 `.남은`/`.퍼센트` 다.
    rng = _range(_pick(sub, "range", "범위"))
    # [2026-10-12 C35b] 범위 없는 게이지·카운터는 관문이 통째 거부했다(C35 실측 경과일 = 날 + 1).
    #   기본 폭을 준다 — 게이지 0-100, 카운터 0-99999 — 시작값이 밖이면 그만큼 넓힌다.
    if rng is None and vtype in ("gauge", "counter"):
        _st0 = _int(_pick(sub, "start", "init", "시작값"))
        _lo0, _hi0 = (0, 100) if vtype == "gauge" else (0, 99999)
        if _st0 is not None:
            _lo0, _hi0 = min(_lo0, _st0), max(_hi0, _st0)
        rng = [_lo0, _hi0]
    _at_raw = _pick(sub, "stage_at", "단계문턱")
    _at_nums = ([_int(x) for x in _at_raw] if isinstance(_at_raw, (list, tuple)) else [])
    _at_nums = [x for x in _at_nums if x is not None]
    _first = _int(_pick(sub, "stage_first", "첫단계필요"))
    _step = _int(_pick(sub, "stage_step", "단계증가"))
    if not _at_nums and _first is not None and rng:
        _at_nums = expand_stage_at(_first, _step if _step is not None else 0, rng[1])
    if _at_nums and vtype in ("gauge", "counter") and not stages:
        stages = [f"레벨{i + 1}" for i in range(len(_at_nums))]
    if _at_nums and len(_at_nums) != len(stages or []):
        _at_nums = []                       # 짝이 안 맞는 경계는 버린다(값 층이 또 거른다)
    if stages and vtype in ("gauge", "counter") and not _at_nums:
        # [2026-11-07 C120 킷] 수 시작값이 있으면 접지 않는다 — 관문이 "stage_at 을 같이" 라고 까닭을 말한다
        #   (옛 동작이 받아 주던 꼴 — 시작값 없음 · 0 · 단계 이름으로 읽히는 수 — 은 그대로 단계형으로 접는다)
        import custom_vars as _cv_e
        _st_n = _int(_pick(sub, "start", "init", "시작값"))
        if not _st_n or _cv_e._match_stage(_st_n, stages):
            vtype = "enum"
    v: Dict[str, Any] = {"type": vtype}
    if rng:
        v["range"] = rng
    if vtype == "text":
        # text 의 시작값은 수가 아니라 **한 줄 글자**다.
        _ts = _s(_pick(sub, "start", "init", "시작값"), 200)
        if _ts:
            v["start"] = _ts
    else:
        start = _int(_pick(sub, "start", "init", "시작값"))
        if start is not None:
            v["start"] = start
        elif vtype == "enum" and stages:
            # [2026-10-12 C35 덤] 단계형 시작은 **단계 이름**이다 — 수로만 읽던 탓에 버려져
            #   늘 첫 단계에서 시작했다. 목록에 있는 이름일 때만 싣는다(관문이 한 번 더 본다).
            _ss = _s(_pick(sub, "start", "init", "시작값"), NAME_MAX)
            if _ss in stages:
                v["start"] = _ss
    if stages:
        v["stages"] = stages
    if _at_nums and vtype in ("gauge", "counter"):
        v["stage_at"] = _at_nums
    mode = _s(_pick(sub, "item_mode", "항목모드")).lower()
    if mode:
        v["item_mode"] = mode
    # [2026-09-13 P15] `max` = 이 목록이 몇 항목까지 드나. 원문이 "최대 30개" 라고
    #   말할 때만 찬다 — 유저 문법이 아니라 **라우터 내부 표기**다. 범위 판정(1~99)은
    #   custom_vars.validate_declaration 한 곳이 하므로 여기선 수로만 옮긴다.
    cap = _int(_pick(sub, "max", "최대", "항목수"))
    if cap is not None:
        v["max"] = cap
    # [2026-09-09 P11] 레코드 목록 — 항목마다 같은 모양의 값 묶음. 칸이 없으면 옛 list 그대로다.
    flds = _pick(sub, "fields", "필드")
    if isinstance(flds, dict) and flds:
        clean_f: Dict[str, List[int]] = {}
        for fk, fv in flds.items():
            nk = _s(fk, NAME_MAX)
            rg = _range(fv)
            if nk and rg:
                clean_f[nk] = rg
        if clean_f:
            v["fields"] = clean_f
            init = _pick(sub, "init", "시작")
            if isinstance(init, dict):
                clean_i = {}
                for fk, fv in init.items():
                    nk = _s(fk, NAME_MAX)
                    iv = _int(fv)
                    if nk in clean_f and iv is not None:
                        clean_i[nk] = iv
                if clean_i:
                    v["init"] = clean_i
            so = _s(_pick(sub, "stage_of", "단계필드"), NAME_MAX)
            if so:
                v["stage_of"] = so
            at = _pick(sub, "stage_at", "단계문턱")
            if isinstance(at, (list, tuple)):
                nums = [_int(x) for x in at]
                if nums and all(x is not None for x in nums):
                    v["stage_at"] = nums
    # [2026-10-12 C35] 파이프 줄이 정하는 칸을 파일도 정한다 — 캡 · 방향 · 숨겨서 늘 추적.
    #   칸이 없거나 못 읽으면 **뺀다**(옛 출력은 바이트 그대로). 0·음수·문자 캡을 넘기면
    #   관문(_cap_ok)이 선언 전체를 거부하므로 여기서 먼저 떨군다.
    if vtype in ("gauge", "counter"):
        import custom_vars as _cv
        _top = int(getattr(_cv, "DELTA_CAP_MAX", 10 ** 6))
        for _k, _alias in (("max_gain", "상승캡"), ("max_loss", "하강캡")):
            _n = _int(_pick(sub, _k, _alias))
            if _n is not None and 1 <= _n <= _top:
                v[_k] = _n
    if vtype in ("gauge", "counter", "enum"):
        _dir = _s(_pick(sub, "direction", "방향")).lower()
        if _dir not in ("both", "up", "down") and sub.get("monotonic") is True:
            _dir = "up"
        if _dir in ("up", "down"):
            v["direction"] = _dir
    if _s(_pick(sub, "track", "추적")).lower() == "always":
        v["track"] = "always"
    # [2026-11-07 C120] 칸 일곱 — 뜻 · 검사는 custom_vars._c120_fields 한 곳. 여기선 옮기기만.
    #   항목 바깥(kind · name 옆)에 적어도 읽는다 — 킷은 `value` 안에 쓰라 하지만 옮겨 적다 바깥에 두기 쉽다.
    _tr = _s(_pick(sub, "track", "추적")).lower() or _s(_pick(raw, "track", "추적")).lower()
    if _tr in ("never", "안함", "안 함", "없음", "추적안함", "추적 안 함"):
        v["track"] = "never"
    for _k, _al in (("start_by", "인물별시작값"), ("only", "명단"), ("max_npcs", "최대인원"),
                    ("holders", "명단식"), ("group", "묶기"), ("stage_notes", "단계설명")):
        _val = _pick(sub, _k, _al)
        if _val in (None, "", [], {}):
            _val = _pick(raw, _k, _al)
        if _val not in (None, "", [], {}):
            v[_k] = _val
    if vtype == "list":
        _li = _pick(sub, "start", "시작값")
        if isinstance(_li, dict) and _li:
            v["start_items"] = _li
    fmt = _s(_pick(sub, "format", "표시"), 60)
    if fmt:
        v["format"] = fmt
    v["rule"] = _s(_pick(sub, "rule", "규칙") or _pick(raw, "rule", "규칙", "raw", "원문"), RULE_MAX)
    derive = _s(_pick(sub, "derive", "파생"), RULE_MAX)
    v["derive"] = derive or None
    return v


def _norm_narrative(sub: Dict[str, Any], raw: Dict[str, Any], name: str) -> Dict[str, Any]:
    """[2026-09-09 P12] 칸 둘 순증 — `mode`(rewrite|append) · `keep`(표시 창).

    기본은 rewrite 라 옛 산출·옛 저장분은 한 글자도 안 바뀐다. 상한 클램프는
    status_panel 이 문다(선언 층의 관문은 거기 하나여야 한다).
    """
    return {
        "panel_section": _s(_pick(sub, "panel_section", "섹션") or name, NAME_MAX),
        "continuity_rule": _s(_pick(sub, "continuity_rule", "rule", "규칙")
                              or _pick(raw, "rule", "raw", "원문"), RULE_MAX),
        "lines": _int(_pick(sub, "lines", "줄수")),
        "mode": _enum(_pick(sub, "mode", "방식") or _pick(raw, "mode", "방식"),
                      ("rewrite", "append"), "rewrite"),
        "keep": _int(_pick(sub, "keep", "표시줄")),
    }


def _norm_format(sub: Dict[str, Any], raw: Dict[str, Any]) -> Dict[str, Any]:
    """형식은 **원문 그대로**. strip 밖의 손질은 하지 않는다(§6 형식 원문 불변).

    [2026-09-07 P10] 옆칸이 둘 늘었다 — `template`(표시 줄의 자리표시자 환원형)과
    `empty`(빈 값의 표기 표). `text` 는 종전과 한 글자도 다르지 않다: 환원이 실패하든
    성공하든 **원문은 언제나 Slot 33 으로 간다**(강등≠폐기).
    """
    text = _pick(sub, "text", "원문", "desc") or _pick(raw, "text", "raw", "원문")
    tpl = _s(_pick(sub, "template", "틀", "표시틀"), FORMAT_TEXT_MAX)
    blanks: Dict[str, str] = {}
    src_empty = _pick(sub, "empty", "빈값")
    if isinstance(src_empty, dict):
        for k, v in src_empty.items():
            nk = _s(k, NAME_MAX)
            if nk:
                blanks[nk] = _s(v, NAME_MAX)
    return {"text": _s(text, FORMAT_TEXT_MAX), "template": tpl or None, "empty": blanks}


def _norm_deliver(src: Any) -> Optional[Dict[str, Any]]:
    """[2026-09-13 P14] `deliver` 서브dict → 칸 셋. 아니면 None.

    종류 정규화는 여기서 **하지 않는다** — 등록기(expr_engine.register_transition)가
    한 곳에서 접는다(두 곳에서 접으면 두 기본값이 생긴다).
    """
    if not isinstance(src, dict):
        return None
    return {"kind": _s(_pick(src, "kind", "종류")).lower() or "letter",
            "from": _s(_pick(src, "from", "보낸이", "발신"), NAME_MAX) or None,
            "brief": _s(_pick(src, "brief", "요지", "무엇"), RULE_MAX)}


def _norm_transition(sub: Dict[str, Any], raw: Dict[str, Any]) -> Dict[str, Any]:
    cue = _s(_pick(sub, "narrated_cue", "큐"), RULE_MAX)
    trig = _s(_pick(sub, "trigger", "방아쇠")).lower()
    if trig not in ("expr", "narrated", "operation"):
        trig = "narrated" if cue and not _pick(sub, "when") else "expr"
    return {
        "trigger": trig,
        "when": _s(_pick(sub, "when", "조건"), RULE_MAX) or None,
        "do": _s(_pick(sub, "do", "실행"), RULE_MAX) or None,
        "on_fail": _s(_pick(sub, "on_fail", "실패"), RULE_MAX) or None,
        "narrated_cue": cue or None,
        "check": _enum(_pick(sub, "check", "판정"), ("judgment", "none"), "none"),
        "once": bool(_pick(sub, "once", "1회")),
        "hold_turns": _int(_pick(sub, "hold_turns"), 0) or 0,
        "hold_days": _int(_pick(sub, "hold_days"), 0) or 0,
        # [2026-09-24 감사] 스키마(L125)·규칙 16 이 요구하는 칸인데 정규화가 빠뜨려
        #   register_transition 에 늘 0 으로 도착했다(쿨다운 사문).
        "cooldown": _int(_pick(sub, "cooldown", "쿨다운"), 0) or 0,
        "notify": _enum(_pick(sub, "notify", "알림"), ("mail", "mind", "none"), "mail"),
        # [2026-09-07 P10] 도착물이 **어떤 형식으로** 도착하는가. 이름만 나른다(본문 렌더 0).
        "notify_format": _s(_pick(sub, "notify_format", "도착물형식"), NAME_MAX) or None,
        # [2026-09-13 P14] 도착물 선언 — 칸 셋만 나른다(본문 0). 정규화·기본값은 expr_engine.
        "deliver": _norm_deliver(_pick(sub, "deliver", "도착")),
        "record": sub.get("record") if isinstance(sub.get("record"), dict) else None,
    }


def _norm_directive(sub: Dict[str, Any], raw: Dict[str, Any]) -> Dict[str, Any]:
    """[2026-09-13 P16] 칸 둘뿐이다 — 조건식과 문장. 문장은 **한 글자도 안 고친다**(형식과 같은 규율)."""
    return {
        "when": _s(_pick(sub, "when", "조건", "if"), RULE_MAX),
        "text": _s(_pick(sub, "text", "문장", "지시", "instruction"), RULE_MAX),
    }


def sorted_items(items: Any) -> List[Dict[str, Any]]:
    """value → narrative → format → transition. **파일 안의 순서는 그 안에서만 산다.**"""
    rows = [i for i in (items or []) if isinstance(i, dict)]
    return [r for _, r in sorted(
        enumerate(rows),
        key=lambda p: (KIND_ORDER.get(p[1].get("kind"), 99), p[0]))]


# =========================================================
# 4. 관문 매핑 — 네 관문은 이미 있다. 여기서는 자리만 고른다.
# =========================================================
def _owner_source(channel_id: str, name: str) -> Optional[str]:
    """그 이름이 이미 어느 source 소유인가. 없으면 None, source 태그가 없으면 ""."""
    for _gate, rec in _find_all(channel_id, name):
        if isinstance(rec, dict):
            return _s(rec.get("source"))
    return None


def _find_all(channel_id: str, name: str) -> List[Tuple[str, Any]]:
    """이름 하나가 어느 관문에 사는지 전부. 삭제·충돌 검사가 같은 눈을 쓴다."""
    hits: List[Tuple[str, Any]] = []
    try:
        import custom_vars as cv
        rec = cv.get_declarations(channel_id).get(name)
        if isinstance(rec, dict) and not cv.is_reserved(rec):
            hits.append(("value", rec))
    except Exception as e:
        logger.debug("[Router] 변수 조회 skip: %s", e)
    try:
        import status_panel as sp
        rec = sp.list_panel_sections(channel_id).get(name)
        if rec:
            hits.append(("narrative", rec))
    except Exception as e:
        logger.debug("[Router] 섹션 조회 skip: %s", e)
    try:
        import domain_manager as dm
        rec = dm.get_output_rules(channel_id).get(name)
        if rec:
            hits.append(("format", rec if isinstance(rec, dict) else {"desc": rec}))
    except Exception as e:
        logger.debug("[Router] 형식 조회 skip: %s", e)
    try:
        import expr_engine as ee
        rec = ee.list_transitions(channel_id).get(name)
        if rec:
            hits.append(("transition", rec))
        rec = ee.list_directives(channel_id).get(name)
        if rec:
            hits.append(("directive", rec))
    except Exception as e:
        logger.debug("[Router] 전이 조회 skip: %s", e)
    # [2026-10-23 C72] 꺼진 모듈(치워 둔 레코드)도 이름을 계속 쥔다(C65-3) — 충돌 검사가 같은 눈으로 본다.
    for _src, _kinds in _parked(channel_id).items():
        for _key, _recs in (_kinds or {}).items():
            rec = (_recs or {}).get(name) if isinstance(_recs, dict) else None
            if isinstance(rec, dict) and _key != "derives":
                hits.append(("parked", dict(rec, source=_s(rec.get("source")) or _src)))
    return hits


def _compiles(src: Any, mode: str) -> bool:
    """식이 §3.4 문법 안인가. 밖이면 그 항목은 거부가 아니라 **강등**이다."""
    if src in (None, ""):
        return True
    try:
        import expr_engine as ee
        ee.compile_expr(src, mode)
        return True
    except Exception:
        return False


def _demote(channel_id: str, item: Dict[str, Any], source: str,
            why: str) -> Dict[str, Any]:
    """문법 밖 → 패널 섹션. 지켜짐은 잃고 출력은 산다(§0.5)."""
    rule = why or item.get("raw") or item["name"]
    narrative = dict(item)
    narrative["kind"] = "narrative"
    narrative["surface"] = "panel"
    narrative["narrative"] = {"panel_section": item["name"],
                              "continuity_rule": _s(rule, RULE_MAX), "lines": None}
    okk, msg = _register_narrative(channel_id, narrative, source)
    return {"item": narrative, "name": item["name"],
            "status": "demoted" if okk else "fail",
            "msg": (_line(narrative) + "  ↓서술로 받음") if okk else msg}


def _register_narrative(channel_id: str, item: Dict[str, Any],
                        source: str) -> Tuple[bool, str]:
    import status_panel as sp
    n = item.get("narrative") or {}
    cadence = item.get("cadence") if item.get("cadence") in ("turn", "day") else "turn"
    return sp.register_panel_section(
        channel_id, item["name"], n.get("continuity_rule") or item.get("raw") or item["name"],
        n.get("lines"), cadence, source=source,
        mode=n.get("mode"), keep=n.get("keep"))


def _register_value(channel_id: str, item: Dict[str, Any],
                    source: str) -> Tuple[bool, str]:
    import custom_vars as cv
    v = item.get("value") or {}
    # [2026-10-12 C35b] 봇이 이미 아는 이름(시간·날짜·위치…)으로 값을 만들면 자리표시자가 선언 변수를
    #   먼저 읽어 상태 줄의 `[위치]` 가 봇 위치 대신 그 값을 그렸다(C35 실측). 파일 길만 막는다 —
    #   파이프로 일부러 만드는 것은 유저 뜻이다.
    if item.get("name") in tuple(getattr(cv, "SYSTEM_PLACEHOLDERS", ()) or ()):
        return False, (f"{item['name']} · 봇이 이미 아는 이름이라 값으로 만들지 않음 — "
                       f"상태 줄엔 [{item['name']}]로 쓰면 된다")
    spec: Dict[str, Any] = {
        "name": item["name"],
        "type": v.get("type") or "gauge",
        "scope": item.get("scope") or "global",
        "rule": v.get("rule") or item.get("raw") or item["name"],
        "source": source,
    }
    for key, val in (("range", v.get("range")),
                     # [P11] 레코드 목록은 `init` 이 **필드별 dict** 다(수 하나가 아니다).
                     ("init", v.get("init") if v.get("fields") else v.get("start")),
                     ("stages", v.get("stages")), ("item_mode", v.get("item_mode")),
                     ("max", v.get("max")),
                     ("fields", v.get("fields")), ("stage_of", v.get("stage_of")),
                     ("stage_at", v.get("stage_at")),
                     ("max_gain", v.get("max_gain")), ("max_loss", v.get("max_loss")),
                     ("direction", v.get("direction")),
                     ("format", v.get("format")), ("feed", item.get("feed")),
                     # [2026-11-07 C120]
                     ("start_by", v.get("start_by")), ("only", v.get("only")),
                     ("max_npcs", v.get("max_npcs")), ("holders", v.get("holders")),
                     ("group", v.get("group")), ("stage_notes", v.get("stage_notes")),
                     ("start_items", v.get("start_items")),
                     ("track", "never" if v.get("track") == "never" else None)):
        if val not in (None, "", []):
            spec[key] = val
    # [2026-10-12 C35] 숨겨서 늘 추적 — 늘 보이는 값(feed always)이 아니면 렌더에서는 뺀다.
    if v.get("track") == "always":
        spec["always_feed"] = True
        if (item.get("feed") or {}).get("prose") != "always":
            spec["feed"] = {"prose": "never"}
    clean, err = cv.validate_declaration(spec, cv.get_declarations(channel_id))
    if clean is None:
        return False, (err or "선언을 읽지 못했습니다.").splitlines()[0]
    # ★`validate_declaration` 은 **화이트리스트 반환**이라 source 가 거기서 증발한다.
    #   custom_vars 의 관문을 늘리는 대신 통과분에 한 칸 얹는다 — `register` 는 spec 을
    #   통째로 복사해 저장하므로(entry = dict(spec)) 이 한 줄이 태그의 전부다.
    if source:
        clean["source"] = str(source)
    okk, msg = cv.register(channel_id, clean)
    if not okk:
        return False, msg
    derive = v.get("derive")
    if derive:
        import expr_engine as ee
        d_ok, d_msg = ee.register_derive(channel_id, item["name"], derive)
        if not d_ok:
            return False, d_msg
    return True, (err or "")            # [C120] 선언은 섰고 버린 칸이 있으면 되비침 한 줄(⚠)


def _register_format(channel_id: str, item: Dict[str, Any], source: str,
                     batch_names: Any = (), batch_sections: Any = ()
                     ) -> Tuple[bool, str, List[str]]:
    """원문 그대로 `output_rules` → Slot 33. 라우터는 형식의 **뜻**을 이해하지 않는다.

    [2026-09-07 P10] 관문 하나가 붙었다 — 콜 없이, 코드만으로:
      `template` 의 자리표시자가 전부 (선언 변수 ∪ 같은 배치 신규 이름 ∪ 시스템 이름표 ∪
      `이름.max/.min` ∪ `섹션/필드`) 안이면 **템플릿 형식**(코드가 그린다).
      하나라도 밖이면 **문체 형식으로 강등**한다(template 을 떨어뜨린다).
    ★강등은 폐기가 아니다: `desc` 는 어느 쪽이든 원문 그대로 남아 Slot 33 으로 간다.
      코드가 못 그리면 산문이 그리면 된다 — 잃는 것은 "코드가 그림"뿐이다.
    """
    import domain_manager as dm
    fmt = item.get("format") or {}
    text = fmt.get("text") or item.get("raw") or ""
    if not text:
        return False, "형식 원문이 비었습니다.", []
    tpl = _s(fmt.get("template"), FORMAT_TEXT_MAX)
    bad: List[str] = []
    if tpl:
        try:
            import custom_vars as cv
            bad = cv.unresolved_placeholders(channel_id, tpl, batch_names, batch_sections)
        except Exception as e:
            logger.debug("[Router] 템플릿 검증 skip: %s", e)
            bad = []
        if bad:
            tpl = ""                       # ↓ 문체로. 원문(desc)은 그대로 산다.
    rules = dm.get_output_rules(channel_id)
    prev = rules.get(item["name"]) if isinstance(rules.get(item["name"]), dict) else {}
    rec: Dict[str, Any] = {"desc": text,
                           "created_at": prev.get("created_at") or time.strftime("%Y-%m-%d"),
                           "source": source,
                           "template": tpl or None,
                           "empty": fmt.get("empty") or {}}
    surface = _s(item.get("surface"))
    if surface:
        rec["surface"] = surface
    rules[item["name"]] = rec
    dm.set_output_rules(channel_id, rules)
    return True, "", bad


def _register_directive(channel_id: str, item: Dict[str, Any],
                        source: str) -> Tuple[bool, str]:
    import expr_engine as ee
    d = dict(item.get("directive") or {})
    d["name"] = item["name"]
    d["source"] = source
    if not _s(d.get("text")):
        d["text"] = item.get("raw") or ""
    return ee.register_directive(channel_id, d)


def _demote_directive(channel_id: str, item: Dict[str, Any], source: str,
                      why: str, batch_names: Any = (), batch_sections: Any = ()
                      ) -> Dict[str, Any]:
    """[2026-09-13 P16] 조건을 못 읽은 지시 → **출력룰 원문**(Slot 33). P10 강등과 같은 길이다.

    ★서술(패널 섹션)이 아니라 출력룰인 이유: 지시는 "화면에 늘 보이는 칸"이 아니라 **산문에게
      하는 말**이다. 패널로 내리면 유저 문장이 상태창 한 칸이 되어 GM 은 그 말을 영영 못 듣는다.
      조건만 잃고 문장은 상시 규칙으로 산다(지켜짐은 잃고 출력은 산다, §0.5).
    """
    d = item.get("directive") or {}
    text = _s(d.get("text")) or _s(item.get("raw")) or item["name"]
    fmt_item = dict(item)
    fmt_item["kind"] = "format"
    fmt_item["surface"] = "panel"
    fmt_item["format"] = {"text": text, "template": None, "empty": {}}
    okk, msg, _bad = _register_format(channel_id, fmt_item, source,
                                      batch_names, batch_sections)
    if not okk:
        return {"item": item, "name": item["name"], "status": "fail", "msg": msg}
    return {"item": fmt_item, "name": item["name"], "status": "demoted",
            "msg": f"{item['name']} · 지시 ↓ 상시 규칙으로(조건을 못 읽음: {_s(why, 80)}) · {text}"}


def _register_transition(channel_id: str, item: Dict[str, Any],
                         source: str) -> Tuple[bool, str]:
    import expr_engine as ee
    t = dict(item.get("transition") or {})
    t["name"] = item["name"]
    t["source"] = source
    # [2026-09-09 P11] `cadence` 는 **항목 칸**이다(전이 서브dict 가 아니라). 여기서 옮겨
    #   싣지 않으면 라우터가 "매일"이라 읽은 것이 매턴 전이로 등록된다.
    t["cadence"] = item.get("cadence") if item.get("cadence") in ("turn", "day") else "turn"
    return ee.register_transition(channel_id, t)


def _line(item: Dict[str, Any]) -> str:
    try:
        import expr_engine as ee
        return ee.format_registration(item)
    except Exception:
        return f"{item.get('name','')} · {_KIND_LABEL.get(item.get('kind'), '')}"


def _stage_number_note(channel_id: str, when: Any) -> str:
    """[2026-10-12 C35b] 조건이 단계형 값을 **수**와 순서 비교하면 읽는 법을 되비침에 적는다.
    식 엔진은 0부터 센 순번으로 읽는다(0 = 첫 단계) — 조용히 맞히는 대신 보이게 한다."""
    src = _s(when)
    if not src:
        return ""
    try:
        import ast
        import custom_vars as cv
        decl = cv.get_declarations(channel_id) or {}
        tree = ast.parse(src, mode="eval")
    except Exception:
        return ""
    notes: List[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Compare):
            continue
        sides = [node.left] + list(node.comparators)
        for i, op in enumerate(node.ops):
            if isinstance(op, (ast.Eq, ast.NotEq)):
                continue
            for a, b in ((sides[i], sides[i + 1]), (sides[i + 1], sides[i])):
                nm = a.id if isinstance(a, ast.Name) else (
                    a.value.id if isinstance(a, ast.Subscript) and isinstance(a.value, ast.Name) else "")
                spec = decl.get(nm) if nm else None
                if (isinstance(spec, dict) and spec.get("type") == "enum"
                        and isinstance(b, ast.Constant) and isinstance(b.value, (int, float))
                        and not isinstance(b.value, bool)):
                    stages = list(spec.get("stages") or [])
                    k = int(b.value)
                    label = stages[k] if 0 <= k < len(stages) else "목록 밖"
                    notes.append(f"{nm} {k} = {label}")
    if not notes:
        return ""
    return "  ⚠ 단계를 수로 비교 — 0부터 센 순번으로 읽음(" + ", ".join(dict.fromkeys(notes)) + ")"


def apply_items(channel_id: str, items: Any, source: str = "") -> List[Dict[str, Any]]:
    """항목 → 관문. 반환은 되비침 재료 `[{item, name, status, msg}]`.

    status: ok(✓) / demoted(↓ 문법 밖 → 서술) / fail(✗ 이름 없음·이름 충돌·관문 거부).
    적용 순서는 여기서도 강제한다 — 호출자가 정렬을 잊어도 전이가 값보다 먼저 서지 않는다.
    """
    rows = sorted_items(items)
    # [2026-10-23 C72] 꺼진 모듈 파일은 다시 올릴 수 없다 — 켜진 채 갱신하는 길(이름 기준 diff)만 둔다.
    #   (같은 source 라 이름 충돌 검사가 통과해 버려서, 막지 않으면 켜진 사본과 치워 둔 사본 둘이 생긴다.)
    if is_bundle_source(source) and is_parked(channel_id, source):
        return [{"item": it, "name": it.get("name") or "", "status": "fail",
                 "msg": f"{it.get('name') or '?'} · {bundle_label(source)} 모듈이 꺼져 있음 — 켜고 올려 주세요"}
                for it in rows]
    # [2026-09-07 P10] 배치 전체를 먼저 훑는다 — 형식 template 의 자리표시자가 "이 응답에서
    #   막 만들어진 이름"을 가리켜도 통과해야 하기 때문(값이 먼저 서는 KIND_ORDER 만으로는
    #   같은 배치 뒤쪽 이름을 못 본다).
    batch_names = {it.get("name") for it in rows if it.get("name")}
    batch_sections = {it.get("name") for it in rows
                      if it.get("kind") == "narrative" and it.get("name")}
    variants: Dict[str, int] = {}
    for it in rows:
        nm0 = it.get("name") or ""
        if nm0:
            variants[nm0] = variants.get(nm0, 0) + 1
    seen_names: set = set()

    out: List[Dict[str, Any]] = []
    for item in rows:
        name = item.get("name") or ""
        kind = item.get("kind")
        # 같은 배치 안 같은 이름이 둘 이상이면 **첫 항목을 채택**한다. 옛 동작은 뒤엣것이
        #   앞엣것을 조용히 덮는 것이었다 — 조용한 덮어쓰기는 "왜 내 규칙이 사라졌나"가 된다.
        if name and name in seen_names:
            out.append({"item": item, "name": name, "status": "demoted",
                        "msg": f"{name} · ↓ 변형 {variants.get(name, 1)}개 중 1 채택"})
            continue
        if name:
            seen_names.add(name)
        owner = _owner_source(channel_id, name)
        if owner is not None and _s(owner) and _s(owner) != _s(source):
            out.append({"item": item, "name": name, "status": "fail",
                        "msg": f"{name} · 이름 충돌: {source_label(channel_id, owner)}"})
            continue
        try:
            if kind == "value":
                derive = (item.get("value") or {}).get("derive")
                if derive and not _compiles(derive, "eval"):
                    out.append(_demote(channel_id, item, source, str(derive)))
                    continue
                okk, msg = _register_value(channel_id, item, source)
            elif kind == "narrative":
                okk, msg = _register_narrative(channel_id, item, source)
            elif kind == "format":
                okk, msg, bad = _register_format(channel_id, item, source,
                                                 batch_names, batch_sections)
                if okk and bad:
                    out.append({"item": item, "name": name, "status": "demoted",
                                "msg": f"{_line(item)}  ↓문체로(문법 밖 이름: "
                                       + ", ".join(bad) + ")"})
                    continue
            elif kind == "directive":
                # 관문 하나로 묶는다 — 컴파일 실패도, 이름 없음도, 조건 부재도 **같은 강등**이다.
                #   (거부로 남기면 유저가 쓴 문장이 통째로 사라진다.)
                okk, msg = _register_directive(channel_id, item, source)
                if not okk:
                    out.append(_demote_directive(channel_id, item, source, msg,
                                                 batch_names, batch_sections))
                    continue
            elif kind == "transition":
                t = item.get("transition") or {}
                bad = [(t.get("when"), "eval"), (t.get("do"), "exec"),
                       (t.get("on_fail"), "exec")]
                broken = [src for src, mode in bad if not _compiles(src, mode)]
                if broken:
                    out.append(_demote(channel_id, item, source, "; ".join(broken)))
                    continue
                okk, msg = _register_transition(channel_id, item, source)
            else:
                continue
        except Exception as e:                       # 관문 하나가 죽어도 나머지는 등록된다
            logger.warning("[Router] 등록 실패 %s(%s): %s", name, kind, e)
            okk, msg = False, f"{name} · 등록 실패: {e}"
        # [2026-09-24 감사] 서술·전이는 turn/day 만 등록된다(week·month 등은 turn 으로 접힘). 되비침이 "주"로
        #   보이면 유저는 매주 정산으로 믿는데 실제는 매턴 — 등록된 값으로 보여 주고 접혔다고 적는다.
        _cad_note = ""
        if okk and kind == "value" and msg:          # [C120] 버린 칸 — 선언은 섰다
            _cad_note += "  ⚠ " + str(msg)[:300]
        if okk and kind == "value":                  # [C120] 되비침 줄은 **선 것**을 보인다(버린 칸은 줄에서 빠지고 ⚠ 에만)
            try:
                import custom_vars as _cv_l
                _st = (_cv_l.get_declarations(channel_id) or {}).get(name) or {}
                _vv = dict(item.get("value") or {})
                for _k in ("start_by", "only", "max_npcs", "holders", "group", "stage_notes", "start_items", "track"):
                    if _st.get(_k) in (None, "", [], {}, False):
                        _vv.pop(_k, None)
                    else:
                        _vv[_k] = _st[_k]
                item = dict(item, value=_vv)
            except Exception as _e_l:
                logger.debug("[Router] 되비침 칸 맞추기 skip %s: %s", name, _e_l)
        if okk and kind in ("directive", "transition"):
            _cad_note += _stage_number_note(channel_id, (item.get(kind) or {}).get("when"))
        if okk and kind in ("narrative", "transition") and item.get("cadence") not in (None, "turn", "day"):
            _cad_note = f"  ⚠ 때 '{item.get('cadence')}' 미지원 → 매턴"
            item = dict(item, cadence="turn")
        out.append({"item": item, "name": name,
                    "status": "ok" if okk else "fail",
                    "msg": (_line(item) + _cad_note) if okk else (msg or f"{name} · 등록 실패")})
    # [2026-10-23 C72] 파일 묶음이 처음 서면 고정 번호를 준다(다시 올리면 제 번호 그대로).
    if is_bundle_source(source) and any(r.get("status") in ("ok", "demoted") for r in out):
        try:
            _assign_numbers(channel_id, [source])
        except Exception as e:
            logger.debug("[Router] 모듈 번호 skip: %s", e)
    return out


# =========================================================
# 5. 재등록 diff · 되비침
# =========================================================
def names_by_source(channel_id: str) -> Dict[str, List[str]]:
    """source 태그별 이름 묶음. 태그 없는 옛 항목은 `""` 키로 모인다."""
    out: Dict[str, List[str]] = {}

    def _add(src: Any, name: str) -> None:
        out.setdefault(_s(src), []).append(name)

    try:
        import custom_vars as cv
        for nm, rec in (cv.get_declarations(channel_id) or {}).items():
            if isinstance(rec, dict) and not cv.is_reserved(rec):
                _add(rec.get("source"), nm)
    except Exception as e:
        logger.debug("[Router] 변수 묶음 skip: %s", e)
    try:
        import status_panel as sp
        for nm, rec in (sp.list_panel_sections(channel_id) or {}).items():
            _add((rec or {}).get("source"), nm)
    except Exception as e:
        logger.debug("[Router] 섹션 묶음 skip: %s", e)
    try:
        import domain_manager as dm
        for nm, rec in dm.get_output_rules(channel_id).items():
            _add(rec.get("source") if isinstance(rec, dict) else "", nm)
    except Exception as e:
        logger.debug("[Router] 형식 묶음 skip: %s", e)
    try:
        import expr_engine as ee
        for nm, rec in (ee.list_transitions(channel_id) or {}).items():
            _add((rec or {}).get("source"), nm)
        for nm, rec in (ee.list_directives(channel_id) or {}).items():
            _add((rec or {}).get("source"), nm)
    except Exception as e:
        logger.debug("[Router] 전이 묶음 skip: %s", e)
    return {k: sorted(set(v)) for k, v in out.items()}


def missing_names(channel_id: str, source: str, new_names: Any) -> List[str]:
    """같은 source 로 다시 왔을 때 **이번 파일에 없어진** 이름. 지우지 않는다 — 묻기만 한다."""
    have = set(names_by_source(channel_id).get(_s(source), []))
    return sorted(have - {_s(n) for n in (new_names or [])})


def format_reflection(source: str, results: Any, missing: Any = (),
                      footer: str = "잘못 읽은 항목은 (ooc: …)로 고쳐줘", head: str = "") -> str:
    """"📋 이렇게 읽었다" 블록. 항목당 한 줄, ✓/✗/↓ 접두(§3.4 되비침 형식).
    [C116 판1 1a] head · footer — 앱 선언 칸은 "📋 선언 칸" 머리 · "outputs.json 을 고쳐 다시 올려요" 꼬리."""
    rows = [r for r in (results or []) if isinstance(r, dict)]
    head = head or f"📋 **이렇게 읽었다** ({source or '서술'}, {len(rows)}항목)"
    lines = [head]
    for r in rows:
        mark = STATUS_MARK.get(r.get("status"), "✗")
        body = " ".join(str(r.get("msg") or r.get("name") or "").split())
        lines.append(f"{mark} {body}")
    miss = [str(m) for m in (missing or []) if str(m).strip()]
    if miss:
        lines.append("빠진 이름: " + " · ".join(miss))
    if footer:
        lines.append(footer)
    return "\n".join(lines)


# =========================================================
# 6. 삭제 — 목적지 정정(동사 신설 0)
# =========================================================
def remove_name(channel_id: str, name: str) -> List[str]:
    """이름 하나를 **모든 관문**에서 찾아 지운다(값·서술·형식·전이·지시). 지운 관문 이름들을 돌려준다."""
    nm = _s(name)
    gone: List[str] = []
    if not nm:
        return gone
    try:
        import custom_vars as cv
        import expr_engine as ee
        if nm in (cv.get_declarations(channel_id) or {}):
            ee.unregister_derive(channel_id, nm)
            if cv.unregister(channel_id, nm):
                gone.append("값")
    except Exception as e:
        logger.debug("[Router] 변수 삭제 skip: %s", e)
    try:
        import status_panel as sp
        okk, _msg = sp.remove_panel_section(channel_id, nm)
        if okk:
            gone.append("서술")
    except Exception as e:
        logger.debug("[Router] 섹션 삭제 skip: %s", e)
    try:
        import domain_manager as dm
        rules = dm.get_output_rules(channel_id)
        if nm in rules:
            del rules[nm]
            dm.set_output_rules(channel_id, rules)
            gone.append("형식")
    except Exception as e:
        logger.debug("[Router] 형식 삭제 skip: %s", e)
    try:
        import expr_engine as ee2
        if ee2.unregister_transition(channel_id, nm):
            gone.append("전이")
        if ee2.unregister_directive(channel_id, nm):
            gone.append("지시")
    except Exception as e:
        logger.debug("[Router] 전이 삭제 skip: %s", e)
    # [2026-10-23 C72] 꺼진 모듈 안의 같은 이름도 지운다(지우기가 parked 까지 — C72 §2.3).
    try:
        if _drop_parked_name(channel_id, nm):
            gone.append("꺼진 모듈")
    except Exception as e:
        logger.debug("[Router] 꺼진 모듈 삭제 skip: %s", e)
    return gone


def remove_source(channel_id: str, source: str) -> List[str]:
    """그 source 로 들어온 항목 전량. 파일 하나를 통째로 무르는 길이다.
    [2026-10-23 C72] 꺼진 모듈이면 치워 둔 레코드째 지운다. 번호 줄(`bundles`)은 남긴다 — 다시 올리면 제 번호."""
    names = names_by_source(channel_id).get(_s(source), [])
    gone = [nm for nm in names if remove_name(channel_id, nm)]
    try:
        gone += _drop_parked_source(channel_id, source)
    except Exception as e:
        logger.debug("[Router] 꺼진 모듈 통째 삭제 skip: %s", e)
    return sorted(set(gone))


# =========================================================
# 6a. [2026-10-23 C72 M6] 선언 모듈 — 파일 하나 = 모듈 하나 · 고정 번호 · 끄기 = 치워 두기
# =========================================================
# 스펙 파티쳇수정/output_artifact/C72_모듈틀_M6+A5_모듈앱·켜고끄기·설정칸_스펙_2026-10-23.md §2.
#   묶음 = source 가 **파일 이름**인 레코드 전부. "" (손으로 쓴 것)·"한 줄 서술"은 묶음 밖 — 늘 켬 · 번호 없음(C65-4).
#   끄기 = 그 source 레코드를 선언 층 여섯 칸에서 빼서 `parked[source]` 로 옮긴다. 읽는 자리는 한 곳도 안 고친다 —
#     꺼진 선언은 저장소에 없으니 급식·추출·패널·expr·Slot 33 이 "등록 안 한 것"과 같은 길로 못 본다.
#   ★`custom_vars.unregister` 는 값까지 지운다 — 치워 두기는 그 함수를 안 쓰고 선언 dict 만 옮긴다(값 · 기록은 남는다).
BUNDLE_FREE = ("", "한 줄 서술")
# (선언 층 칸, 사람 말). 파생식은 변수에 딸려 같이 간다(제 이름표가 없다).
PARK_KINDS = (("custom_vars", "값"), ("derives", ""), ("panel_sections", "서술"),
              ("output_rules", "형식"), ("transitions", "전이"), ("directives", "지시"))
KIND_LABELS = ("값", "서술", "형식", "전이", "지시")


APP_SOURCE_PREFIX = "app:"   # [2026-11-06 C116 판1 1a] 코드 앱 선언 칸(outputs.json)의 출처 = 앱 번호 줄 이름(app_host.NUM_PREFIX)


def is_decl_source(source: Any) -> bool:
    """[C116 판1 1a] 모듈로 묶이는 출처 — 파일 모듈 + 앱 선언. 번호 매기기 · 치워 두기 · 지우기가 이 판별을 쓴다."""
    s = _s(source)
    return bool(s) and s not in BUNDLE_FREE


def is_app_source(source: Any) -> bool:
    return _s(source).startswith(APP_SOURCE_PREFIX)


def is_bundle_source(source: Any) -> bool:
    """파일 모듈 출처. [C116 판1 1a] 앱 선언(`app:<id>`)은 빠진다 — 파일 모듈 목록(bundles)에 따로 안 서고 앱 카드가 보여 준다."""
    return is_decl_source(source) and not is_app_source(source)


def source_label(channel_id: str, source: Any) -> str:
    """[C116 판1 1a] 출처를 사람 말로 — 앱 `#3 장비함(앱)` · 파일 모듈 `#2 화원` · 손 선언 `손 선언`."""
    s = _s(source)
    if is_app_source(s):
        aid = s[len(APP_SOURCE_PREFIX):]
        try:
            import app_host as _ah
            a = _ah.get_app(channel_id, aid)
            if a:
                return _ah.label_of(a) + "(앱)"
        except Exception as e:
            logger.debug("[Router] 앱 이름 skip: %s", e)
        return f"{aid}(앱)"
    if not s or s in BUNDLE_FREE:
        return "손 선언"
    try:
        no = module_numbers(channel_id).get(s)
    except Exception:
        no = 0
    return f"#{no} {bundle_label(s)}" if no else bundle_label(s)


def source_names(channel_id: str, source: Any) -> Dict[str, List[str]]:
    """[C116 판1 1a] 한 출처의 선언 이름 {사람 말: [이름]} — 켜졌으면 저장소, 꺼졌으면 치워 둔 칸에서."""
    s = _s(source)
    parked = _parked(channel_id).get(s)
    if isinstance(parked, dict):
        out: Dict[str, List[str]] = {}
        for key, label in PARK_KINDS:
            recs = parked.get(key)
            if label and isinstance(recs, dict) and recs:
                out[label] = sorted(recs)
        return {lb: out[lb] for lb in KIND_LABELS if out.get(lb)}
    act = _active_by_source(channel_id).get(s) or {}
    return {lb: sorted(set(act[lb])) for lb in KIND_LABELS if act.get(lb)}


def remove_owned(channel_id: str, source: Any, names: Any) -> List[str]:
    """[C116 판1 1a] 그 출처가 쥔 이름만 지운다(남의 이름은 손 안 댐). 지운 이름들."""
    s = _s(source)
    owned = set(names_by_source(channel_id).get(s, []))
    return [nm for nm in (names or []) if nm in owned and remove_name(channel_id, nm)]


def bundle_label(source: Any) -> str:
    """`화원.txt` → `화원`. 확장자가 없으면 그대로."""
    s = _s(source)
    head, dot, ext = s.rpartition(".")
    return head if dot and head and ext and len(ext) <= 5 and " " not in ext else s


def _parked(channel_id: str) -> Dict[str, Dict[str, Dict[str, Any]]]:
    try:
        import domain_manager as dm
        p = (dm.get_output_decl(channel_id) or {}).get("parked")
    except Exception as e:
        logger.debug("[Router] parked 읽기 skip: %s", e)
        return {}
    return {str(k): v for k, v in p.items() if isinstance(v, dict)} if isinstance(p, dict) else {}


def is_parked(channel_id: str, source: Any) -> bool:
    return _s(source) in _parked(channel_id)


def _assign_numbers(channel_id: str, sources: List[Any]) -> Dict[str, int]:
    """번호 없는 묶음에 다음 번호를 준다(입력 순서대로). 이미 있는 번호는 안 바꾼다. {source: no} 전부를 돌려준다."""
    import domain_manager as dm
    layer = dm.get_output_decl(channel_id)
    nums = dict(layer.get("bundles") or {})
    seq = int(layer.get("bundle_seq") or 0)
    changed = False
    for src in sources:
        s = _s(src)
        if not is_decl_source(s) or isinstance(nums.get(s), dict) and nums[s].get("no"):
            continue
        seq += 1
        nums[s] = {"no": seq}
        changed = True
    if changed:
        layer["bundles"] = nums
        layer["bundle_seq"] = seq
        dm.update_output_decl(channel_id, layer)
    return {k: int((v or {}).get("no") or 0) for k, v in nums.items() if isinstance(v, dict)}


def module_number(channel_id: str, key: str) -> int:
    """[2026-10-23 C73] 고정 번호 줄 하나 — 코드 앱(`app:<id>`)도 같은 줄 · 같은 카운터를 쓴다(`!모듈 3 off`)."""
    return int(_assign_numbers(channel_id, [key]).get(_s(key)) or 0)


def module_numbers(channel_id: str) -> Dict[str, int]:
    """[C73] 번호 줄 전부 읽기(안 매긴다)."""
    try:
        import domain_manager as dm
        nums = dm.get_output_decl(channel_id).get("bundles") or {}
    except Exception as e:
        logger.debug("[Router] 번호 읽기 skip: %s", e)
        return {}
    return {str(k): int((v or {}).get("no") or 0) for k, v in nums.items() if isinstance(v, dict)}


def _active_by_source(channel_id: str) -> Dict[str, Dict[str, List[str]]]:
    """{source: {사람 말: [이름]}} — 켜진(저장소에 있는) 레코드. 읽기 길은 names_by_source 와 같다(게터 = 옛 자리 폴백 포함)."""
    out: Dict[str, Dict[str, List[str]]] = {}

    def _add(src: Any, label: str, name: str) -> None:
        out.setdefault(_s(src), {}).setdefault(label, []).append(name)

    try:
        import custom_vars as cv
        for nm, rec in (cv.get_declarations(channel_id) or {}).items():
            if isinstance(rec, dict) and not cv.is_reserved(rec):
                _add(rec.get("source"), "값", nm)
    except Exception as e:
        logger.debug("[Router] 모듈 값 skip: %s", e)
    try:
        import status_panel as sp
        for nm, rec in (sp.list_panel_sections(channel_id) or {}).items():
            _add((rec or {}).get("source"), "서술", nm)
    except Exception as e:
        logger.debug("[Router] 모듈 서술 skip: %s", e)
    try:
        import domain_manager as dm
        for nm, rec in dm.get_output_rules(channel_id).items():
            _add(rec.get("source") if isinstance(rec, dict) else "", "형식", nm)
    except Exception as e:
        logger.debug("[Router] 모듈 형식 skip: %s", e)
    try:
        import expr_engine as ee
        for nm, rec in (ee.list_transitions(channel_id) or {}).items():
            _add((rec or {}).get("source"), "전이", nm)
        for nm, rec in (ee.list_directives(channel_id) or {}).items():
            _add((rec or {}).get("source"), "지시", nm)
    except Exception as e:
        logger.debug("[Router] 모듈 전이 skip: %s", e)
    return out


HAND_SOURCE = "손 선언"   # [C116 판1 1c] 옛 모듈 밖 선언(손으로 친 파이프 · 한 줄 서술)이 옮겨 가는 묶음 이름


def retag_free(channel_id: str) -> int:
    """[2026-11-06 C116 판1 1c] 모듈 밖(출처 "" · "한 줄 서술" · 출처 없음) 레코드 → 출처 `손 선언`.
    `!출력룰` 이 걷혀 새 손 선언은 안 생긴다 — 남은 옛 것을 파일 모듈처럼 번호 · 켜고 끄기 · 지우기로 다루게 한 번 옮긴다.
    선언 층 레코드의 `source` 칸만 바꾼다(값 · 기록 무접촉). 시스템 · 기본 선언(활력 · 평형 · 소지품 개정)은 안 옮긴다.
    → 옮긴 레코드 수(0 = 쓰기 0)."""
    try:
        _migrate_legacy(channel_id)
    except Exception as e:
        logger.debug("[Router] 손 선언 이월 skip: %s", e)
    import domain_manager as dm
    try:
        import custom_vars as cv
        _reserved = cv.is_reserved
    except Exception:
        def _reserved(_r):
            return False
    layer = dm.get_output_decl(channel_id)
    n = 0
    for key, label in PARK_KINDS:
        recs = layer.get(key)
        if not label or not isinstance(recs, dict):
            continue                      # 파생식(제 이름표 없음)은 변수에 딸려 간다
        for rec in recs.values():
            if not isinstance(rec, dict) or (key == "custom_vars" and _reserved(rec)):
                continue
            if _s(rec.get("source")) in BUNDLE_FREE:
                rec["source"] = HAND_SOURCE
                n += 1
    if n:
        dm.update_output_decl(channel_id, layer)
        logger.info("[Router] 손 선언 옮겨 적기 ch=%s — 레코드 %d", channel_id, n)
    return n


def bundles(channel_id: str) -> List[Dict[str, Any]]:
    """선언 모듈 목록(번호순) — [{source, label, no, on, counts{말: n}, names{말: [이름]}}].
    레코드가 있는 묶음만 선다(켜짐 · 꺼짐). 번호 없는 묶음(옛 채널)은 이름순으로 한 번 매긴다 — 읽기가 쓰는 유일한 자리.
    [C116 1c] 맨 앞에서 옛 모듈 밖 선언을 `손 선언` 묶음으로 옮긴다(retag_free — 있을 때만 쓴다)."""
    try:
        retag_free(channel_id)
    except Exception as e:
        logger.debug("[Router] 손 선언 옮겨 적기 skip: %s", e)
    active = {k: v for k, v in _active_by_source(channel_id).items() if is_bundle_source(k)}
    parked = _parked(channel_id)
    srcs = sorted(set(active) | {s for s in parked if is_bundle_source(s)})
    if not srcs:
        return []
    try:
        nums = _assign_numbers(channel_id, srcs)
    except Exception as e:
        logger.debug("[Router] 모듈 번호 매기기 skip: %s", e)
        nums = {}
    out: List[Dict[str, Any]] = []
    for s in srcs:
        on = s not in parked
        if on:
            names = {lb: sorted(set(v)) for lb, v in active.get(s, {}).items()}
        else:
            names = {}
            for key, label in PARK_KINDS:
                recs = (parked.get(s) or {}).get(key)
                if label and isinstance(recs, dict) and recs:
                    names[label] = sorted(recs)
        out.append({"source": s, "label": bundle_label(s), "no": int(nums.get(s) or 0), "on": on,
                    "counts": {lb: len(names[lb]) for lb in KIND_LABELS if names.get(lb)},
                    "names": {lb: names[lb] for lb in KIND_LABELS if names.get(lb)}})
    out.sort(key=lambda b: (b["no"] or 10 ** 9, b["label"]))
    return out


def free_count(channel_id: str) -> int:
    """묶음 밖(손으로 쓴 것 · 한 줄 서술) 레코드 수 — 모듈 앱 "그 밖 n개"."""
    active = _active_by_source(channel_id)
    return sum(len(v) for s in BUNDLE_FREE for v in (active.get(s) or {}).values())


def counts_line(b: Dict[str, Any]) -> str:
    return " · ".join(f"{lb} {n}" for lb, n in (b.get("counts") or {}).items()) or "비어 있음"


def _migrate_legacy(channel_id: str) -> None:
    """옛 자리(world_state)에만 있는 선언(변수 · 형식)을 선언 층으로 한 번 옮긴다 — 치워 두기가 층만 보고 움직여도 안 새게.
    옮기는 일은 각 저장소의 쓰기 관문이 한다(옛 자리를 비우는 것까지 그 관문 몫)."""
    import domain_manager as dm
    layer = dm.get_output_decl(channel_id)
    try:
        import custom_vars as cv
        if not layer.get(cv.KEY_DECL):
            raw = cv._read_decl(channel_id)
            if isinstance(raw, dict) and raw:
                cv._save(channel_id, raw, cv.get_values(channel_id))
    except Exception as e:
        logger.debug("[Router] 변수 이월 skip: %s", e)
    try:
        if not layer.get("output_rules"):
            rules = dm.get_output_rules(channel_id)
            if rules:
                dm.set_output_rules(channel_id, rules)
    except Exception as e:
        logger.debug("[Router] 형식 이월 skip: %s", e)


def park_source(channel_id: str, source: Any) -> Tuple[bool, str]:
    """끄기 = 치워 두기. (ok, 한 줄). 이미 꺼져 있으면 ok · "이미 꺼짐"."""
    s = _s(source)
    if not is_decl_source(s):
        return False, "파일로 올린 모듈 · 앱 선언만 끄고 켤 수 있어요"
    if s in _parked(channel_id):
        return True, f"{bundle_label(s)} 이미 꺼져 있음"
    _migrate_legacy(channel_id)
    import domain_manager as dm
    import custom_vars as cv
    layer = dm.get_output_decl(channel_id)
    take: Dict[str, Dict[str, Any]] = {}
    var_names: set = set()
    for key, _label in PARK_KINDS:
        if key == "derives":
            continue
        store = dict(layer.get(key) or {})
        moved = {}
        for nm in list(store):
            rec = store[nm]
            if not isinstance(rec, dict) or _s(rec.get("source")) != s:
                continue
            if key == "custom_vars" and cv.reserved_name(nm):
                continue
            moved[nm] = store.pop(nm)
        if moved:
            layer[key] = store
            take[key] = moved
            if key == "custom_vars":
                var_names |= set(moved)
    if var_names:
        dstore = dict(layer.get("derives") or {})
        dmoved = {nm: dstore.pop(nm) for nm in list(dstore) if nm in var_names}
        if dmoved:
            layer["derives"] = dstore
            take["derives"] = dmoved
    if not take:
        return False, f"{bundle_label(s)} 안에 올린 것이 없어요"
    parked = dict(layer.get("parked") or {})
    parked[s] = take
    layer["parked"] = parked
    dm.update_output_decl(channel_id, layer)
    n = sum(len(v) for k, v in take.items() if k != "derives")
    logger.info("[Module] %s 끔 — 치워 둔 선언 %d", s, n)
    return True, f"{bundle_label(s)} 끔"


def unpark_source(channel_id: str, source: Any) -> Tuple[bool, str]:
    """켜기 = 되돌리기. 그사이 같은 이름이 저장소에 생겼으면(묶음 밖 손 등록 같은 것) 그 줄만 못 돌리고 알린다."""
    s = _s(source)
    import domain_manager as dm
    layer = dm.get_output_decl(channel_id)
    parked = dict(layer.get("parked") or {})
    take = parked.pop(s, None)
    if not isinstance(take, dict):
        return (True, f"{bundle_label(s)} 이미 켜져 있음") if is_decl_source(s) else (False, "모르는 모듈")
    skipped: List[str] = []
    for key, recs in take.items():
        if not isinstance(recs, dict):
            continue
        store = dict(layer.get(key) or {})
        for nm, rec in recs.items():
            if nm in store and key != "derives":
                skipped.append(nm)
                continue
            store[nm] = rec
        layer[key] = store
    layer["parked"] = parked
    dm.update_output_decl(channel_id, layer)
    logger.info("[Module] %s 켬 — 겹쳐 못 돌린 이름 %s", s, skipped or "0")
    line = f"{bundle_label(s)} 켬"
    if skipped:
        line += " · 이름이 겹쳐 못 돌린 것: " + ", ".join(sorted(skipped))
    return True, line


def set_bundle(channel_id: str, source: Any, on: bool) -> Tuple[bool, str]:
    return unpark_source(channel_id, source) if on else park_source(channel_id, source)


def _drop_parked_name(channel_id: str, name: str) -> bool:
    import domain_manager as dm
    layer = dm.get_output_decl(channel_id)
    parked = dict(layer.get("parked") or {})
    hit = False
    for s in list(parked):
        kinds = dict(parked.get(s) or {})
        for key in list(kinds):
            recs = dict(kinds.get(key) or {})
            if name in recs:
                recs.pop(name)
                hit = True
                if recs:
                    kinds[key] = recs
                else:
                    kinds.pop(key)
        if kinds:
            parked[s] = kinds
        else:
            parked.pop(s)
    if hit:
        layer["parked"] = parked
        dm.update_output_decl(channel_id, layer)
    return hit


def _drop_parked_source(channel_id: str, source: Any) -> List[str]:
    import domain_manager as dm
    s = _s(source)
    layer = dm.get_output_decl(channel_id)
    parked = dict(layer.get("parked") or {})
    take = parked.pop(s, None)
    if not isinstance(take, dict):
        return []
    layer["parked"] = parked
    dm.update_output_decl(channel_id, layer)
    return sorted({nm for k, recs in take.items() if k != "derives" and isinstance(recs, dict) for nm in recs})


def parked_section_names(channel_id: str) -> List[str]:
    """꺼진 모듈의 패널 섹션 이름 — 매턴 패널 저장(전량 교체)이 그 몫 저장값을 지우지 않게(status_panel 이 읽는다)."""
    out: List[str] = []
    for kinds in _parked(channel_id).values():
        recs = (kinds or {}).get("panel_sections")
        if isinstance(recs, dict):
            out += list(recs)
    return out
