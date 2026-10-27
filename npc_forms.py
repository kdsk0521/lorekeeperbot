# -*- coding: utf-8 -*-
"""npc_forms.py — NPC 모습 · 등장 · 불러오기 (C84, 2026-10-27)

한 사람이 모습을 여럿 갖는다. 지금 모습 하나만 명부(`npcs`) · 렌더에 서고, 다른 모습의 시트는 이 표에
잠긴 채 보관한다. 조건이 맞으면 **이미 있는 개명 기계**(`npc_manager.swap_npc_name`)로 이름을 바꾸고
시트를 갈아 끼운다. 배타("C 가 있는 한 A 는 없다")는 "한 사람 = 이름 하나"로 선다.
같은 표 · 같은 판정에 효과만 다른 규칙 둘을 더 얹는다 — 등장(잠긴 사람이 조건에 등록된다) ·
불러오기(등록된 사람의 시트를 조건이 참이 된 턴에 확정으로 싣는다).

표 = domain["npc_forms"] (domain JSON — `!다시` 스냅샷이 같이 되감고 `!리셋` 이 같이 지운다. `!클리어` 는 남기되
      지금 모습을 첫 모습으로 되돌리고 등장한 사람을 다시 잠근다 — `on_clear`).
  {사람 키: {"active": 지금 모습 이름 | None(등장 전),
             "forms": {모습 이름: {"sheet": 시트 원문 | None, "src": "first|sheet|lore", "data": {시트 칸}}},
             "rules": [{"id", "effect": form|appear|load, "from", "to", "when",
                        "items", "with", "at", "words", "expr", "src": sheet|lore, "owner", "last"(load)}],
             "log": [{"turn", "from", "to", "by", "evidence"}]}}       # log 최근 LOG_MAX

★잠긴 모습은 `npcs` 에 없다 — 명부 · 렌더 · 이름 목록 · 위키 검색 · Activity 인물 앱 어디에도 안 선다.
★판정: Theoria 앞 코드 문 1(items · expr · words) → Theoria `form` 칸(when 있는 규칙만, 근거 필수) →
  DAI 초크포인트 뒤 코드 문 2(+ with · at) → 발화. 한 사람에 한 턴 하나(묶음 턴의 PC 들 사이에서도).
스펙: 파티쳇수정/output_artifact/C84_NPC모습·활성규칙_지도·스펙_2026-10-27.md
"""
import contextlib
import copy
import logging
import re
from typing import Any, Dict, Iterator, List, Optional, Tuple

logger = logging.getLogger(__name__)

TABLE_KEY = "npc_forms"
EFFECTS = ("form", "appear", "load")
CODE_FIELDS = ("items", "with", "at", "words", "expr")
LOG_MAX = 10
LOAD_CAP = 3                 # 한 턴 불러오기 상한(분석 콜 RelevantNPCs 상한 5 안에서)
WHEN_MAX = 200
EVIDENCE_MAX = 200
SCENE_TEXT_MAX = 6000

# 모습마다 따로 가는 시트 칸 — domain_manager.update_npc 는 넘긴 dict 에 **없는** 칸을 옛 값으로 이월한다(_NPC_PRESERVE_KEYS).
#   그래서 바꿀 때 이 칸을 **명시로** 채운다(들어오는 모습 시트에서 뽑은 값, 없으면 빈 값). 안 그러면 A 의 역할 · 말투가 C 에 남는다.
SHEET_KEYS = ("role", "race", "affiliation", "summary", "static_traits", "tone", "speech", "schedule",
              "location", "personality", "constraints", "appearance", "gender", "background")
_EMPTY_OF = {"static_traits": dict, "schedule": dict, "constraints": list}

# 시트 머리 라벨(D1) — 한국어 · 영문 같이 받는다.
_EFFECT_LABELS = {
    "form": ("모습", "form of"),        # 맨 `form:` 은 안 받는다 — 외부 시트의 외형 줄(`- Form: grey wolf`)과 겹친다
    "appear": ("등장", "appear"),
    "load": ("불러오기", "load"),
}
_COND_LABELS = {
    "when": ("조건", "when"),
    "items": ("소지품", "items"),
    "with": ("만남", "with"),
    "at": ("장소", "at"),
    "words": ("낱말", "words"),
    "expr": ("식", "expr"),
    "back": ("돌아감", "back"),
}
_YES = ("예", "네", "응", "yes", "y", "true", "1", "o", "on")
_NONE_WORDS = ("", "-", "없음", "none", "null", "n/a")
_LABEL_LINE = re.compile(r"^([^:：]{1,24}?)\s*[:：]\s*(.*)$")

# 개명 · 등록 관문이 예약 이름을 통과시키는 자리(이 모듈의 바꾸기 · 등장만).
_ALLOW: set = set()


# =========================================================
# 표 읽기 · 쓰기
# =========================================================
def _load(channel_id: str) -> Dict[str, Any]:
    try:
        import domain_manager
        t = (domain_manager.get_domain(channel_id) or {}).get(TABLE_KEY)
    except Exception as e:
        logger.debug(f"[Form] table read skip: {e}")
        return {}
    return copy.deepcopy(t) if isinstance(t, dict) else {}


def _save(channel_id: str, t: Dict[str, Any]) -> None:
    """도메인을 **새로 읽고** 표 칸만 갈아 끼운다 — 앞선 개명 · 등록이 쓴 칸을 옛 사본으로 덮지 않게."""
    import domain_manager
    d = domain_manager.get_domain(channel_id)
    if t:
        d[TABLE_KEY] = t
    else:
        d.pop(TABLE_KEY, None)
    domain_manager.save_domain(channel_id, d)


def has_table(channel_id: str) -> bool:
    return bool(_load(channel_id))


def _npcs(channel_id: str) -> Dict[str, Any]:
    try:
        import domain_manager
        return domain_manager.get_npcs(channel_id) or {}
    except Exception:
        return {}


def _turn(channel_id: str) -> int:
    try:
        import domain_manager
        return int((domain_manager.get_world_state(channel_id) or {}).get("turn_index", 0) or 0)
    except Exception:
        return 0


# =========================================================
# 이름
# =========================================================
def _norm(s: Any) -> str:
    """들어오는 이름(시트 태그 · 로어 분석 · 라벨 값)을 명부 키 꼴로 — 괄호 앞 공백 등(`세라핀 (Seraphine)` → `세라핀(Seraphine)`).
    개명 기계는 새 이름을 정확 문자열로 명부에서 찾는다 — 정규화 안 된 이름이면 개명은 됐는데 실패로 읽혔다(검토 1)."""
    s = " ".join(str(s or "").split()).strip()
    try:
        import domain_manager
        return domain_manager._normalize_npc_name(s) if s else ""
    except Exception:
        return s


def _nn(s: Any) -> str:
    try:
        import domain_manager
        return domain_manager._normalize_npc_name(str(s or "")).strip().lower()
    except Exception:
        return " ".join(str(s or "").split()).lower()


def _same(a: Any, b: Any) -> bool:
    return bool(a) and bool(b) and _nn(a) == _nn(b)


def _resolve(npcs: Dict[str, Any], name: Any, *, allow_token: bool = True) -> Optional[str]:
    if not name:
        return None
    if name in npcs:
        return name
    try:
        import domain_manager
        return domain_manager._find_npc_key(npcs, str(name), allow_token=allow_token)
    except Exception:
        return None


def _form_key(forms: Dict[str, Any], name: Any) -> Optional[str]:
    """보관된 모습 이름 맞추기 — 정규화 일치 → 괄호 표기 맞춤(토큰 맞춤은 안 함 — 짧은 이름 오인)."""
    if not name or not isinstance(forms, dict):
        return None
    if name in forms:
        return name
    for k in forms:
        if _same(k, name):
            return k
    return _resolve({k: {} for k in forms}, name, allow_token=False)


def _canon(t: Dict[str, Any], npcs: Dict[str, Any]) -> bool:
    """지금 모습 이름이 명부 키와 어긋났으면(괄호식 승격 `레나`→`레나(Rena)` · 늦게 등록된 첫 모습) 표를 맞춘다."""
    changed = False
    for pk, ent in t.items():
        if not isinstance(ent, dict):
            continue
        cur = ent.get("active")
        if not cur or cur in npcs:
            continue
        k = _resolve(npcs, cur, allow_token=False)
        if not k or any(isinstance(e2, dict) and e2.get("active") == k for p2, e2 in t.items() if p2 != pk):
            continue
        forms = ent.setdefault("forms", {})
        fk = _form_key(forms, k)
        if fk and not _same(fk, cur):
            # 명부가 이 사람의 **다른 모습** 키를 쥐고 있다 = 표 밖에서 이름이 바뀌었다. 모습 칸 · 규칙을 고쳐 쓰면 A→A 고리가 된다 —
            #   지금 모습만 맞춘다(검토 4).
            logger.warning("[Form] 표 밖 이름 바뀜 %s → %s (%s) — 지금 모습만 맞춤", cur, k, pk)
            ent["active"] = fk if fk in npcs else k
            changed = True
            continue
        if cur in forms and k not in forms:
            forms[k] = forms.pop(cur)
        for r in ent.get("rules") or []:
            for f in ("from", "to"):
                if _same(r.get(f), cur):
                    r[f] = k
            if r.get("owner") and _same(r["owner"], cur):
                r["owner"] = k
        ent["active"] = k
        changed = True
    return changed


def _person_of(t: Dict[str, Any], name: Any) -> Optional[str]:
    """이름이 어느 사람 것인가 — 사람 키 · 지금 모습 · 보관 모습 이름."""
    if not name:
        return None
    for pk, ent in t.items():
        if not isinstance(ent, dict):
            continue
        if _same(pk, name) or _same(ent.get("active"), name) or _form_key(ent.get("forms") or {}, name):
            return pk
    return None


def _locked_forms(t: Dict[str, Any]) -> List[Tuple[str, str]]:
    """잠긴 이름 = (사람 키, 모습 이름) — 지금 모습이 아닌 보관 모습 + 등장 전 사람."""
    out: List[Tuple[str, str]] = []
    for pk, ent in t.items():
        if not isinstance(ent, dict):
            continue
        act = ent.get("active")
        for fn in (ent.get("forms") or {}):
            if act and _same(fn, act):
                continue
            out.append((pk, fn))
    return out


def is_reserved(channel_id: str, name: Any) -> bool:
    """잠긴 모습 · 등장 전 이름이면 True(괄호 표기 · 호칭 변형 포함, 토큰 맞춤은 안 함)."""
    if not name:
        return False
    t = _load(channel_id)
    names = [fn for _pk, fn in _locked_forms(t)]
    if not names:
        return False
    return _resolve({n: {} for n in names}, str(name), allow_token=False) is not None


def blocks_new_key(channel_id: str, name: Any) -> bool:
    """새 NPC 키를 만들면 안 되는 이름인가 — 예약 이름이고 지금 이 모듈이 바꾸는 중이 아니면 True."""
    if not name or not is_reserved(channel_id, name):
        return False
    if _ALLOW and _resolve({a: {} for a in _ALLOW}, str(name), allow_token=False):
        return False
    return True


@contextlib.contextmanager
def allowing(*names: Any) -> Iterator[None]:
    added = [str(n) for n in names if n and str(n) not in _ALLOW]
    _ALLOW.update(added)
    try:
        yield
    finally:
        for n in added:
            _ALLOW.discard(n)


# =========================================================
# 시트 머리 라벨
# =========================================================
def _label_of(k: str) -> Optional[str]:
    k = " ".join(str(k or "").split()).lower()
    for canon, al in list(_EFFECT_LABELS.items()) + list(_COND_LABELS.items()):
        if k in al:
            return canon
    return None


def _yes(v: Any) -> bool:
    return str(v or "").strip().lower() in _YES


def _list(v: Any) -> List[str]:
    if isinstance(v, (list, tuple)):
        items = [str(x) for x in v]
    else:
        items = re.split(r"[,，、·/;；]", str(v or ""))
    out = []
    for x in items:
        x = " ".join(str(x).split()).strip()
        if x and x.lower() not in _NONE_WORDS and x not in out:
            out.append(x)
    return out


def _text(v: Any, cap: int = WHEN_MAX) -> str:
    s = " ".join(str(v or "").split()).strip()
    return "" if s.lower() in _NONE_WORDS else s[:cap]


def _nameish(v: Any) -> bool:
    """`모습:` 값이 이름 꼴인가 — 40자 안 · 문장 부호 없음 · 괄호 표기면 이름, 아니면 낱말 셋 이하이고 조사로 이은 수식이 아님
    (`은발에 붉은 눈의 여인` 은 외형 줄이다)."""
    s = _text(v, 200)
    if not s or len(s) > 40 or re.search(r"[.!?。,，;:]", s):
        return False
    if "(" in s:
        return True
    words = s.split()
    if len(words) > 3:
        return False
    if len(words) > 1 and any(re.search(r"(에|의|한|은|는|을|를|이|가)$", w) for w in words[:-1]):
        return False
    return True


def parse_head(desc: Any) -> Optional[Dict[str, Any]]:
    """시트 **머리**(첫 `#` 머리줄 앞)의 규칙 라벨. 효과 라벨(`모습:` · `등장: 예` · `불러오기: 예`)이 있을 때만 읽는다.

    본문(Direction · Schedule 절)의 `조건:` · `장소:` · `식:` 은 흔한 꼴이라 머리 밖은 보지 않는다.
    Returns {"effect", "target", 조건 칸들, "back", "body"(규칙 라벨 줄을 뗀 시트)} | None."""
    lines = str(desc or "").splitlines()
    end = len(lines)
    for i, ln in enumerate(lines):
        if ln.strip().startswith("#"):
            end = i
            break
    found: Dict[str, str] = {}
    hit: List[int] = []
    for i in range(end):
        s = lines[i].strip().lstrip("-*>").strip()
        m = _LABEL_LINE.match(s)
        if not m:
            continue
        canon = _label_of(m.group(1))
        if not canon:
            continue
        found.setdefault(canon, m.group(2).strip())
        hit.append(i)
    eff = None
    if _text(found.get("form")) and _nameish(found.get("form")):
        eff = "form"
    elif _yes(found.get("appear")):
        eff = "appear"
    elif _yes(found.get("load")):
        eff = "load"
    if not eff:
        return None
    # 조건 라벨이 하나도 없으면 규칙 시트가 아니다 — `모습: 은발에 붉은 눈` 같은 외형 줄을 가로채지 않는다(검토 3).
    if not (_text(found.get("when")) or any(_list(found.get(k)) for k in ("items", "with", "at", "words"))
            or _text(found.get("expr"))):
        return None
    drop = set(hit)
    body = "\n".join(ln for i, ln in enumerate(lines) if i not in drop).strip()
    return {
        "effect": eff,
        "target": _norm(_text(found.get("form"), 120)),
        "when": _text(found.get("when")),
        "items": _list(found.get("items")),
        "with": _list(found.get("with")),
        "at": _list(found.get("at")),
        "words": _list(found.get("words")),
        "expr": _text(found.get("expr"), 300),
        "back": _text(found.get("back")),
        "body": body,
    }


# =========================================================
# 규칙
# =========================================================
def _has_cond(r: Dict[str, Any]) -> bool:
    return bool(r.get("when")) or any(r.get(k) for k in CODE_FIELDS)


def _next_id(t: Dict[str, Any]) -> str:
    n = 0
    for ent in t.values():
        for r in (ent.get("rules") or []) if isinstance(ent, dict) else []:
            m = re.match(r"^r(\d+)$", str(r.get("id") or ""))
            if m:
                n = max(n, int(m.group(1)))
    return f"r{n + 1}"


def _mk_rule(t: Dict[str, Any], effect: str, frm: Optional[str], to: str, src: str, owner: str,
             cond: Dict[str, Any]) -> Dict[str, Any]:
    r = {"id": _next_id(t), "effect": effect, "from": frm, "to": to,
         "when": _text(cond.get("when")),
         "items": _list(cond.get("items")), "with": _list(cond.get("with")),
         "at": _list(cond.get("at")), "words": _list(cond.get("words")),
         "expr": _text(cond.get("expr"), 300), "src": src, "owner": owner}
    if effect == "load":
        r["last"] = False
    return r


def _put_rules(t: Dict[str, Any], ent: Dict[str, Any], owner: str, src: str,
               new: List[Dict[str, Any]]) -> int:
    """같은 출처 · 같은 주인(시트 이름)의 옛 규칙을 새 것으로. 조건 없는 규칙은 버린다(늘 참이면 매 턴 튄다)."""
    keep = [r for r in (ent.get("rules") or [])
            if not (r.get("src") == src and _same(r.get("owner"), owner))]
    ent["rules"] = keep
    n = 0
    for r in new:
        if r.get("effect") == "form" and _same(r.get("from"), r.get("to")):
            logger.info("[Form] 제 자신으로 바뀌는 규칙은 안 받음: %s", r.get("to"))
            continue
        if not _has_cond(r):
            logger.info("[Form] 조건 없는 규칙은 안 받음: %s %s→%s", r.get("effect"), r.get("from"), r.get("to"))
            continue
        r["id"] = _next_id(t)
        ent["rules"].append(r)
        n += 1
    return n


def _effective_rules(ent: Dict[str, Any]) -> List[Dict[str, Any]]:
    """시트 규칙 먼저, 같은 (효과 · from · to) 의 로어 규칙은 가린다(시트가 이긴다)."""
    rules = [r for r in (ent.get("rules") or []) if isinstance(r, dict) and r.get("effect") in EFFECTS]
    sheet = [r for r in rules if r.get("src") == "sheet"]
    sig = {(r["effect"], _nn(r.get("from")), _nn(r.get("to"))) for r in sheet}
    lore = [r for r in rules if r.get("src") != "sheet"
            and (r["effect"], _nn(r.get("from")), _nn(r.get("to"))) not in sig]
    return sheet + lore


def _live(ent: Dict[str, Any], r: Dict[str, Any], npcs: Dict[str, Any]) -> bool:
    eff = r.get("effect")
    act = ent.get("active")
    if eff == "appear":
        return not act
    key = _resolve(npcs, act, allow_token=False) if act else None
    if not key:
        return False
    if eff == "load":
        return True
    return _same(r.get("from"), act) or _same(r.get("from"), key)


def _switched_this_turn(ent: Dict[str, Any], turn: int) -> bool:
    lg = ent.get("log") or []
    if not lg or not isinstance(lg[-1], dict) or lg[-1].get("by") not in ("rule", "manual"):
        return False
    try:
        return int(lg[-1].get("turn")) == int(turn)
    except (TypeError, ValueError):
        return False


# =========================================================
# 코드 문
# =========================================================
def _squash(s: Any) -> str:
    return re.sub(r"\s+", "", str(s or "")).lower()


def _items_ok(channel_id: str, items: List[str]) -> Tuple[bool, str]:
    if not items:
        return True, ""
    try:
        import custom_vars
        have = custom_vars.sojipin_items(channel_id) or {}
    except Exception:
        have = {}
    keys = [_squash(k) for k in have]
    for it in items:
        q = _squash(it)
        if not q or not any(q == k or q in k for k in keys):
            return False, f"소지품 {it} 없음"
    return True, ""


def _expr_ok(channel_id: str, expr: str, uid: str) -> Tuple[bool, str]:
    if not expr:
        return True, ""
    try:
        import types as _types
        import expr_engine
        ok = expr_engine.eval_when(channel_id, expr, _types.SimpleNamespace(user_id=uid or ""), label="모습")
    except Exception as e:
        return False, f"식 평가 실패({str(e)[:60]})"
    return (True, "") if ok else (False, f"식 거짓: {expr}")


def _words_ok(words: List[str], text: str) -> Tuple[bool, str]:
    if not words:
        return True, ""
    sq = _squash(text)
    for w in words:
        q = _squash(w)
        if q and q in sq:
            return True, ""
    return False, "낱말 안 나옴: " + ", ".join(words[:4])


def last_prose(channel_id: str) -> str:
    """직전 산문 = 히스토리에서 마지막 GM 줄(입력이 이미 붙어 있어도 그 앞 줄)."""
    try:
        import domain_manager
        rows = (domain_manager.get_domain(channel_id) or {}).get("history") or []
    except Exception:
        return ""
    for r in reversed(rows if isinstance(rows, list) else []):
        if isinstance(r, dict) and str(r.get("role") or "") not in ("user", ""):
            return str(r.get("content") or "")
    return ""


def scene_text(channel_id: str, input_text: str) -> str:
    """D7 낱말을 찾는 자리 = 이번 턴 입력 + 직전 산문."""
    return (str(input_text or "") + "\n" + last_prose(channel_id))[-SCENE_TEXT_MAX:]


def _pc_place(channel_id: str, dai: Optional[Dict[str, Any]]) -> Tuple[str, Optional[str]]:
    """이번 턴 PC 자리 — dai.current_location 이 풀리면 그것, 아니면 저장된 자리."""
    import domain_manager
    try:
        import world_tree
    except Exception:
        world_tree = None
    loc = str((dai or {}).get("current_location") or "").strip()
    nid = world_tree.resolve_node_id(channel_id, loc) if (world_tree and loc) else None
    if not nid:
        try:
            loc2 = str(domain_manager.get_current_location(channel_id) or "").strip()
        except Exception:
            loc2 = ""
        if loc2 and world_tree:
            nid = world_tree.resolve_node_id(channel_id, loc2)
        loc = loc or loc2
    return loc, nid


def _at_ok(channel_id: str, at: List[str], loc: str, nid: Optional[str]) -> Tuple[bool, str]:
    """장소 — PC 노드가 그 장소이거나 그 안(조상 걷기). 노드로 못 풀면 이름 맞춤."""
    if not at:
        return True, ""
    try:
        import world_tree
        nodes = world_tree.get_all_nodes(channel_id) or {}
    except Exception:
        world_tree, nodes = None, {}
    for a in at:
        aid = world_tree.resolve_node_id(channel_id, a) if world_tree else None
        if aid and nid:
            cur, seen = nid, set()
            while cur and cur in nodes and cur not in seen:
                if cur == aid:
                    return True, ""
                seen.add(cur)
                cur = (nodes.get(cur) or {}).get("parent_id") or ""
        elif loc and _squash(a) and (_squash(a) == _squash(loc) or _squash(a) in _squash(loc)):
            return True, ""
    return False, f"장소: 지금 {loc or '자리 모름'}"


def _with_ok(channel_id: str, names: List[str], npcs: Dict[str, Any], nid: Optional[str],
             relevant: Optional[List[Any]]) -> Tuple[bool, str]:
    """만남 — 그 사람의 노드 == 이번 턴 PC 노드. 나무에 자리가 없으면(또는 PC 자리를 못 풀면) RelevantNPCs 로 대신."""
    if not names:
        return True, ""
    try:
        import world_tree
    except Exception:
        world_tree = None
    rel = [str(r if isinstance(r, str) else (r or {}).get("name", "")) for r in (relevant or [])]
    for w in names:
        k = _resolve(npcs, w)
        if not k:
            return False, f"만남: {w} 명부에 없음"
        loc = world_tree.get_npc_location(channel_id, k) if world_tree else None
        if loc and nid:
            if world_tree.resolve_node_id(channel_id, loc) != nid:
                return False, f"만남: {k} 다른 곳({loc})"
            continue
        if relevant is None:
            return False, (f"만남: {k} 나무에 자리 없음" if not loc else "만남: PC 자리를 못 풀어 노드 비교 불가") \
                + " → 그 턴 RelevantNPCs 로 대신"
        if not any(_resolve({k: npcs.get(k) or {}}, r, allow_token=False) for r in rel if r):
            return False, f"만남: {k} 자리 없음 · 이번 턴 인물 아님"
    return True, ""


def _code_ok(channel_id: str, r: Dict[str, Any], *, uid: str, text: Optional[str], npcs: Dict[str, Any],
             full: bool, loc: str = "", nid: Optional[str] = None,
             relevant: Optional[List[Any]] = None) -> Tuple[bool, List[str]]:
    why: List[str] = []
    for ok, w in (_items_ok(channel_id, r.get("items") or []),
                  _expr_ok(channel_id, r.get("expr") or "", uid)):
        if not ok:
            why.append(w)
    if text is not None:
        ok, w = _words_ok(r.get("words") or [], text)
        if not ok:
            why.append(w)
    if full:
        for ok, w in (_at_ok(channel_id, r.get("at") or [], loc, nid),
                      _with_ok(channel_id, r.get("with") or [], npcs, nid, relevant)):
            if not ok:
                why.append(w)
    return (not why), why


def _label(r: Dict[str, Any]) -> str:
    eff = r.get("effect")
    if eff == "form":
        return f"{r.get('from')} becomes {r.get('to')}"
    if eff == "appear":
        return f"{r.get('to')} steps in"
    return f"{r.get('to')} is called up"


def code_gate(channel_id: str, input_text: str, uid: str = "") -> List[Dict[str, Any]]:
    """Theoria 앞 코드 문 1 — 지금 설 수 있는 규칙 중 items · expr · words 를 넘은 것. 쓰기 0.
    with · at 은 여기서 안 본다(이번 턴에 이동할 수 있다)."""
    t = _load(channel_id)
    if not t:
        return []
    npcs = _npcs(channel_id)
    _canon(t, npcs)
    turn = _turn(channel_id)
    text = scene_text(channel_id, input_text)
    out: List[Dict[str, Any]] = []
    for pk, ent in t.items():
        if not isinstance(ent, dict) or _switched_this_turn(ent, turn):
            continue
        for r in _effective_rules(ent):
            if not _live(ent, r, npcs):
                continue
            ok, _w = _code_ok(channel_id, r, uid=uid, text=text, npcs=npcs, full=False)
            if ok:
                out.append({"id": r.get("id"), "effect": r.get("effect"), "person": pk,
                            "from": r.get("from"), "to": r.get("to"), "when": r.get("when") or "",
                            "label": _label(r)})
    return out


_THEORIA_HEAD = """### Forms — lore conditions on people (a form change, someone stepping in, someone called up)
- "form": null OR [ {"rule": "<id from PENDING>", "evidence": "ENGLISH-ONLY telegraphic — what in this turn's input or the last prose makes the condition true"} ]
  ONLY when this turn's input or the last prose makes the condition true. null is the common case.
  PENDING:"""


def theoria_block(pending: List[Dict[str, Any]]) -> str:
    """코드 문 1을 넘었고 `when` 이 있는 규칙만 싣는다. 없으면 ""(스키마 바이트 그대로)."""
    rows = [p for p in (pending or []) if p.get("when")]
    if not rows:
        return ""
    lines = [f"  - {p['id']} ({p['label']}): {p['when']}" for p in rows]
    return _THEORIA_HEAD + "\n" + "\n".join(lines) + "\n"


def roster_lines(pending: List[Dict[str, Any]]) -> str:
    """불러오기 규칙이 코드 문 1을 `when` 없이 넘으면 ROSTER 에 한 줄 — 분석 콜이 그 사람을 이번 턴 인물로 본다."""
    rows = [p for p in (pending or []) if p.get("effect") == "load" and not p.get("when")]
    return "\n".join(f"LOADED: {p['to']} — lore condition met" for p in rows)


def _theoria_said(raw: Any, ids: set) -> Dict[str, str]:
    """Theoria `form` 칸 → {규칙 id: 근거}. 근거가 빈 항목 · 모르는 id 는 버린다(→down 관문과 같은 규율)."""
    out: Dict[str, str] = {}
    items = raw if isinstance(raw, list) else ([raw] if isinstance(raw, dict) else [])
    for it in items[:8]:
        if not isinstance(it, dict):
            continue
        rid = str(it.get("rule") or "").strip().lower()
        ev = " ".join(str(it.get("evidence") or "").split())[:EVIDENCE_MAX]
        if rid in ids and ev and ev.lower() not in _NONE_WORDS:
            out.setdefault(rid, ev)
    return out


# =========================================================
# 발화 — 모습 바꾸기 · 등장 · 되잠그기
# =========================================================
def _drop_alias(channel_id: str, key: str, alias: str) -> None:
    """개명 실패 길이 붙인 별칭(전에 없던 것)을 걷는다 — swap_npc_name 은 실패하면 새 이름을 별칭으로 되붙인다."""
    try:
        import domain_manager
        import wiki_store
        npcs = domain_manager.get_npcs(channel_id) or {}
        cur = copy.deepcopy(npcs.get(key) or {})
        al = [a for a in (cur.get("aliases") or []) if not (isinstance(a, str) and _same(a, alias))]
        if al != list(cur.get("aliases") or []):
            cur["aliases"] = al
            domain_manager.update_npc(channel_id, key, cur)
        wiki_store.remove_page_aliases(channel_id, wiki_store.page_id_for("character", key), [alias])
    except Exception as e:
        logger.debug(f"[Form] alias restore skip: {e}")


def _has_alias(data: Dict[str, Any], name: str) -> bool:
    return any(isinstance(a, str) and _same(a, name) for a in (data.get("aliases") or []))


def _sheet_data(name: str, sheet: str) -> Dict[str, Any]:
    """들어오는 모습 시트에서 시트 칸을 뽑는다 — 등록 길(_register_npc 라벨 · update_npc 자동 추출)과 같은 함수들."""
    out: Dict[str, Any] = {}
    sheet = str(sheet or "")
    try:
        import npc_manager
        out.update({k: v for k, v in (npc_manager._extract_structured_fields(sheet) or {}).items() if v})
    except Exception as e:
        logger.debug(f"[Form] structured fields skip: {e}")
    try:
        import command_handler
        idf = command_handler._extract_id_fields(sheet.splitlines())
        if idf.get("role"):
            out["role"] = idf["role"]
        if idf.get("species"):
            out["race"] = idf["species"]
        if idf.get("affiliation"):
            out["affiliation"] = idf["affiliation"]
        _sm = command_handler._summary_from_id_fields(idf)
        if _sm:
            out["summary"] = _sm
    except Exception as e:
        logger.debug(f"[Form] id fields skip: {e}")
    try:
        from npc_profile_harness import extract_static_traits
        st = extract_static_traits(name, sheet) if len(sheet) >= 100 else {}
        if st:
            out["static_traits"] = st
    except Exception as e:
        logger.debug(f"[Form] static traits skip: {e}")
    return {k: v for k, v in out.items() if k in SHEET_KEYS}


def _fill_sheet_keys(data: Dict[str, Any], over: Dict[str, Any]) -> None:
    for k in SHEET_KEYS:
        if k in over:
            data[k] = copy.deepcopy(over[k])
        else:
            data[k] = _EMPTY_OF.get(k, str)()


def _push_event(channel_id: str, line: str) -> None:
    """`<사건>` 재료 한 줄(Slot 29, 1턴만). 재료이지 지시가 아니다 — expr 사건 줄과 같은 규율."""
    try:
        import domain_manager
        import expr_engine
        ws = domain_manager.get_world_state(channel_id) or {}
        rows = ws.get(expr_engine.PENDING_EVENTS_KEY)
        rows = rows if isinstance(rows, list) else []
        rows.append(line)
        ws[expr_engine.PENDING_EVENTS_KEY] = rows
        domain_manager.update_world_state(channel_id, ws)
    except Exception as e:
        logger.debug(f"[Form] event line skip: {e}")


def _log(ent: Dict[str, Any], turn: int, frm: Any, to: Any, by: str, evidence: str = "") -> None:
    lg = [x for x in (ent.get("log") or []) if isinstance(x, dict)]
    lg.append({"turn": int(turn), "from": frm, "to": to, "by": by, "evidence": str(evidence or "")[:EVIDENCE_MAX]})
    ent["log"] = lg[-LOG_MAX:]


def _rename_form(ent: Dict[str, Any], old: str, new: str) -> None:
    """표 안의 모습 이름 하나를 명부 키 꼴로 — 모습 칸 · 규칙 from/to · 주인."""
    forms = ent.setdefault("forms", {})
    if old in forms and new not in forms:
        forms[new] = forms.pop(old)
    for r in ent.get("rules") or []:
        for f in ("from", "to", "owner"):
            if r.get(f) == old:
                r[f] = new
    if ent.get("active") == old:
        ent["active"] = new


def switch(channel_id: str, t: Dict[str, Any], pk: str, to: str, *, by: str,
           evidence: str = "", turn: Optional[int] = None) -> Tuple[bool, str]:
    """모습 바꾸기(2-4 ①~④). ⚠ 로 끝나면 표 · 별칭 무변경(표는 맨 끝에 쓴다 — 앞이 반쯤 실패해도 거짓말 안 하게).
    성공 판정은 개명 기계의 반환이 아니라 **명부**로 한다(옛 키가 사라지고 새 이름이 섰나). Returns (ok, 한 줄)."""
    import npc_manager
    import wiki_store
    ent = t.get(pk)
    if not isinstance(ent, dict):
        return False, "⚠ 모습 표에 없는 사람"
    npcs = _npcs(channel_id)
    cur = ent.get("active")
    key = _resolve(npcs, cur, allow_token=False) if cur else None
    if not key:
        return False, f"⚠ 지금 모습 {cur or '-'} 이 명부에 없음"
    forms = ent.setdefault("forms", {})
    fto = _form_key(forms, to)
    if not fto:
        return False, f"⚠ {to} 은 {pk} 의 모습이 아님"
    if _same(fto, key):
        return False, f"⚠ 이미 {key}"
    if _norm(fto) != fto:                      # 옛 표에 정규화 안 된 이름이 있으면 여기서 맞춘다
        _rename_form(ent, fto, _norm(fto))
        fto = _norm(fto)
    turn = _turn(channel_id) if turn is None else int(turn)
    out_form = _form_key(forms, cur) or _form_key(forms, key) or key
    pid = wiki_store.page_id_for("character", key)
    cur_text = (wiki_store.assemble_lore_text(wiki_store.get_lore_sections(channel_id, pid)) or "").strip()
    cur_data = npcs.get(key) or {}
    prev = forms.get(out_form) or {}
    # ① 나가는 모습 떠 두기(로컬 — 표는 ④에서). lore 절이 비어 있던 모습(세션 NPC 첫 모습)은 "빈 시트"로 적는다 —
    #    돌아올 때 들어온 모습의 시트를 지워야 한다(검토 6).
    stash = {"sheet": cur_text or None, "blank": not cur_text,
             "src": prev.get("src") or "first",
             "data": {k: copy.deepcopy(cur_data[k]) for k in SHEET_KEYS if k in cur_data and cur_data[k]}}
    had_alias = _has_alias(cur_data, fto)
    # ② 개명 기계 — 별칭이면 먼저 떼고 개명, 되돌리기면 걷기까지 그 함수가 한다. 반환 문구는 채널에 안 낸다.
    with allowing(fto):
        ok, msg = npc_manager.swap_npc_name(channel_id, key, fto, reason=f"form:{by}")
    npcs2 = _npcs(channel_id)
    nk = fto if fto in npcs2 else _resolve(npcs2, fto, allow_token=False)
    renamed = bool(nk) and key not in npcs2 and not _same(nk, key)
    if not renamed:
        if not had_alias:
            _drop_alias(channel_id, key, fto)
        logger.info("[Form] 바꾸기 거절 %s→%s: %s", key, fto, msg)
        return False, f"⚠ {key}→{fto}: {msg if not ok else '명부가 안 바뀜'}"
    if not ok:
        logger.warning("[Form] 개명 기계는 실패라 했지만 명부는 바뀜 %s→%s(%s) — 명부를 따른다: %s", key, fto, nk, msg)
    if nk != fto:
        _rename_form(ent, fto, nk)
        fto = nk
    # ③ 시트 갈기 + 시트 칸(D3: 보관 시트도 떠 둔 칸도 없는 모습이면 이름만 바꾸고 시트 · 칸은 그대로)
    inc = forms.get(fto) or {}
    sheet = str(inc.get("sheet") or "").strip()
    over = inc.get("data") if isinstance(inc.get("data"), dict) and inc.get("data") else None
    blank = bool(inc.get("blank")) and not sheet
    if sheet or over or blank:
        nd = copy.deepcopy((_npcs(channel_id) or {}).get(fto) or {})
        nd.pop("description", None)
        nd.pop("desc", None)
        _fill_sheet_keys(nd, over if over is not None else (_sheet_data(fto, sheet) if sheet else {}))
        if sheet:
            nd["description"] = sheet
        try:
            with allowing(fto):
                npc_manager.update_npc(channel_id, fto, nd)
            if blank:
                wiki_store.set_lore_sections(channel_id, wiki_store.page_id_for("character", fto), {},
                                             "blank-form", None)
        except Exception as e:
            logger.warning("[Form] 시트 갈기 실패 %s: %s", fto, e)
    # ④ 표 — 맨 끝
    forms[out_form] = stash
    ent["active"] = fto
    _log(ent, turn, key, fto, by, evidence)
    _save(channel_id, t)
    logger.info("[Form] 모습 %s → %s (by=%s, turn=%s)", key, fto, by, turn)
    return True, f"{key} → {fto}"


def appear(channel_id: str, t: Dict[str, Any], pk: str, *, by: str, evidence: str = "",
           turn: Optional[int] = None) -> Tuple[bool, str]:
    """등장 — 보관 시트를 평소 등록 길(`_register_npc`)로 등록. 한 번뿐(등록된 뒤로는 보통 NPC)."""
    ent = t.get(pk)
    if not isinstance(ent, dict) or ent.get("active"):
        return False, "⚠ 등장할 사람이 아님"
    forms = ent.setdefault("forms", {})
    fn = next(iter(forms), None)
    if not fn:
        return False, "⚠ 보관 시트 없음"
    sheet = str((forms.get(fn) or {}).get("sheet") or "").strip()
    npcs = _npcs(channel_id)
    ex = _resolve(npcs, fn, allow_token=False)
    if ex:
        return False, f"⚠ {fn} 이 이미 {ex} 로 등록됨"
    turn = _turn(channel_id) if turn is None else int(turn)
    try:
        import command_handler
        with allowing(fn):
            key = command_handler._register_npc(channel_id, fn, sheet,
                                                command_handler._extract_id_fields(sheet.splitlines()))
    except Exception as e:
        logger.warning("[Form] 등장 등록 실패 %s: %s", fn, e)
        return False, f"⚠ {fn} 등록 실패"
    if key not in (_npcs(channel_id) or {}):
        return False, f"⚠ {fn} 등록 안 됨"
    over = (forms.get(fn) or {}).get("data")
    if isinstance(over, dict) and over:
        # 잠그기 전 등록 기록에 있던 칸(보이스카드 말투 · 일정 · 심리 라벨 등 — 페이지 밖)을 되돌린다(검토 2)
        try:
            import npc_manager
            nd = copy.deepcopy((_npcs(channel_id) or {}).get(key) or {})
            nd.pop("description", None)
            nd.pop("desc", None)
            nd.update(copy.deepcopy(over))
            npc_manager.update_npc(channel_id, key, nd)
        except Exception as e:
            logger.debug(f"[Form] appear data skip: {e}")
    if key != fn:
        _rename_form(ent, fn, key)
    ent["active"] = key
    _log(ent, turn, None, key, by, evidence)
    _save(channel_id, t)
    logger.info("[Form] 등장 %s (by=%s, turn=%s)", key, by, turn)
    return True, key


def _relock(channel_id: str, t: Dict[str, Any], pk: str) -> bool:
    """등장한 사람을 다시 잠근다(`!클리어`) — 지금 페이지 lore 절을 보관 시트로, 등록은 지움."""
    import domain_manager
    import wiki_store
    ent = t.get(pk)
    key = (ent or {}).get("active")
    if not key:
        return False
    pid = wiki_store.page_id_for("character", key)
    txt = (wiki_store.assemble_lore_text(wiki_store.get_lore_sections(channel_id, pid)) or "").strip()
    forms = ent.setdefault("forms", {})
    fk = _form_key(forms, key) or key
    rec = dict(forms.get(fk) or {})
    if txt:
        rec["sheet"] = txt
    _cur = (_npcs(channel_id) or {}).get(key) or {}
    _keep = {k: copy.deepcopy(_cur[k]) for k in SHEET_KEYS if _cur.get(k)}
    if _keep:
        rec["data"] = _keep
    forms[fk] = rec
    domain_manager.delete_npc(channel_id, key)
    ent["active"] = None
    return True


def _rename_in_dai(dai: Dict[str, Any], old: str, new: str) -> List[str]:
    """이번 턴 DAI 의 이름 키 칸(초크포인트 PC 걷기 목록 그대로)에서 old → new."""
    hit: List[str] = []
    if not isinstance(dai, dict) or not old or not new:
        return hit
    _one = {old: {}}

    def _is_old(n: Any) -> bool:   # 분석 콜은 `에린` · `Erin` 처럼 짧게도 쓴다 — 괄호 표기 맞춤(토큰 맞춤은 안 함)
        return isinstance(n, str) and (_same(n, old) or _resolve(_one, n, allow_token=False) is not None)

    for k in ("psyche_states", "npc_attitudes", "npc_knowledge", "newcomer_seeds", "trait_connections"):
        blk = dai.get(k)
        if isinstance(blk, dict):
            ks = [n for n in blk if _is_old(n)]
            if ks:
                dai[k] = {(new if n in ks else n): v for n, v in blk.items()}
                hit.append(k)
    ia = dai.get("intimacy_analysis")
    if isinstance(ia, dict):
        for sk in ("window_check", "dual_control", "desire_type", "post_encounter_prediction"):
            sub = ia.get(sk)
            if isinstance(sub, dict) and any(_is_old(n) for n in sub):
                ia[sk] = {(new if _is_old(n) else n): v for n, v in sub.items()}
                hit.append(f"intimacy_analysis.{sk}")
    rn = dai.get("relevant_npcs")
    if isinstance(rn, list) and any(_is_old(n) for n in rn):
        dai["relevant_npcs"] = [(new if _is_old(n) else n) for n in rn]
        hit.append("relevant_npcs")
    return hit


def _to_front(dai: Dict[str, Any], name: str) -> None:
    _one = {name: {}}
    rn = [n for n in (dai.get("relevant_npcs") or [])
          if not (isinstance(n, str) and (_same(n, name) or _resolve(_one, n, allow_token=False)))]
    dai["relevant_npcs"] = [name] + rn


def _mark_switch(channel_id: str, dai: Dict[str, Any], tag: str) -> None:
    """바뀐 즉시 — `form_switch` 칸(턴이 끝나면 턴 컨텍스트로) + `block_redo`(턴이 중간에 죽어 컨텍스트가 안 남아도
    직전 턴 `!다시` 가 이 턴 시작 스냅숏으로 되감지 않게, 검토 5)."""
    fs = dai.get("form_switch") if isinstance(dai.get("form_switch"), list) else []
    fs.append(tag)
    dai["form_switch"] = fs
    try:
        import domain_manager
        domain_manager.block_redo(channel_id, "모습 바뀜")
    except Exception as e:
        logger.debug(f"[Form] block_redo skip: {e}")


def _strip_reserved(channel_id: str, dai: Dict[str, Any]) -> None:
    """잠긴 이름은 이번 턴 인물 · 새 인물 시드에서 뺀다 — `!룰` 로스터나 PENDING 줄을 본 분석 콜이 C 를 골라도
    조건 전엔 세우지 않는다(시드가 C 를 낯선 사람으로 굴리지 않게)."""
    t = _load(channel_id)
    names = [fn for _pk, fn in _locked_forms(t)]
    if not names:
        return
    lk = {n: {} for n in names}

    def _hit(n: Any) -> bool:
        return isinstance(n, str) and _resolve(lk, n, allow_token=False) is not None

    rn = dai.get("relevant_npcs")
    if isinstance(rn, list) and any(_hit(n) for n in rn):
        dai["relevant_npcs"] = [n for n in rn if not _hit(n)]
    ns = dai.get("newcomer_seeds")
    if isinstance(ns, dict) and any(_hit(n) for n in ns):
        dai["newcomer_seeds"] = {k: v for k, v in ns.items() if not _hit(k)}


def _load_step(r: Dict[str, Any], cond: bool, turn: int) -> Tuple[bool, bool]:
    """불러오기 D8 — 조건이 참으로 **바뀐** 턴에 한 번. 묶음 턴은 PC 마다 판정이 돈다 → 그 턴 첫 판정 앞 상태(p)를 쥐고
    그 턴 안에선 OR 로 접는다(둘째 PC 가 거짓이라 해도 그 턴은 참). Returns (이번에 부를까, 표가 바뀌었나)."""
    before = (r.get("t"), r.get("p"), r.get("last"), r.get("ft"))
    if r.get("t") != turn:
        r["p"] = bool(r.get("last"))
        r["t"] = turn
        r["last"] = cond
    else:
        r["last"] = bool(r.get("last")) or cond
    fire_now = cond and not r.get("p") and r.get("ft") != turn
    if fire_now:
        r["ft"] = turn
    return fire_now, before != (r.get("t"), r.get("p"), r.get("last"), r.get("ft"))


def fire(channel_id: str, dai: Dict[str, Any], raw: Any, *, input_text: str = "", uid: str = "",
         turn: Optional[int] = None) -> List[str]:
    """DAI 초크포인트 뒤(코드 문 2 + 발화). 같은 턴 감정 · 디렉터 · 렌더가 바뀐 이름을 본다.
    Returns 사건 요약 줄(로그용). 표가 비면 아무것도 안 한다(쓰기 0)."""
    t = _load(channel_id)
    if not t or not isinstance(dai, dict):
        return []
    npcs = _npcs(channel_id)
    dirty = _canon(t, npcs)
    turn = _turn(channel_id) if turn is None else int(turn)
    ids = {str(r.get("id") or "").lower() for ent in t.values() if isinstance(ent, dict)
           for r in (ent.get("rules") or [])}
    said = _theoria_said(raw, ids)
    text = scene_text(channel_id, input_text)
    loc, nid = _pc_place(channel_id, dai)
    out: List[str] = []
    loads: List[str] = []
    for pk in list(t.keys()):
        ent = t.get(pk)
        if not isinstance(ent, dict) or _switched_this_turn(ent, turn):
            continue
        for r in _effective_rules(ent):
            if not _live(ent, r, npcs):
                continue
            ok, _why = _code_ok(channel_id, r, uid=uid, text=text, npcs=npcs, full=True,
                                loc=loc, nid=nid, relevant=dai.get("relevant_npcs") or [])
            rid = str(r.get("id") or "").lower()
            cond = ok and (not r.get("when") or rid in said)
            if r.get("effect") == "load":
                fire_now, ch = _load_step(r, cond, turn)
                dirty = dirty or ch
                k = _resolve(npcs, ent.get("active"), allow_token=False)
                if k and (fire_now or r.get("ft") == turn) and k not in loads:
                    loads.append(k)      # 묶음 턴 뒤 PC 판에도 다시 싣는다(렌더는 마지막 PC 판을 본다)
                continue
            if not cond:
                continue
            ev = said.get(rid) or "code"
            if r.get("effect") == "form":
                old = _resolve(npcs, ent.get("active"), allow_token=False) or ent.get("active")
                ok2, msg = switch(channel_id, t, pk, r.get("to"), by="rule", evidence=ev, turn=turn)
                if ok2:
                    new = t[pk]["active"]
                    _rename_in_dai(dai, old, new)
                    _to_front(dai, new)
                    _push_event(channel_id, f"{old} → {new}: 같은 사람이 다른 모습이 되었다")
                    _mark_switch(channel_id, dai, f"{old}→{new}")
                    out.append(f"form {old}→{new} ({rid})")
                    dirty = False      # switch 가 표를 저장했다(load 칸도 같이)
                    npcs = _npcs(channel_id)
                break
            if r.get("effect") == "appear":
                ok2, msg = appear(channel_id, t, pk, by="rule", evidence=ev, turn=turn)
                if ok2:
                    _to_front(dai, msg)
                    _push_event(channel_id, f"{msg}: 나타났다")
                    _mark_switch(channel_id, dai, f"+{msg}")
                    out.append(f"appear {msg} ({rid})")
                    dirty = False
                    npcs = _npcs(channel_id)
                break
    for k in reversed(loads[:LOAD_CAP]):
        _to_front(dai, k)
    if loads:
        out.append("load " + ", ".join(loads[:LOAD_CAP]))
        if len(loads) > LOAD_CAP:
            logger.info("[Form] 불러오기 상한 %d — 넘친 것: %s", LOAD_CAP, ", ".join(loads[LOAD_CAP:]))
    if dirty:
        _save(channel_id, t)
    _strip_reserved(channel_id, dai)
    if out:
        logger.info("[Form] turn=%s %s", turn, " / ".join(out))
    return out


# =========================================================
# 시트로 들어오는 규칙 (`!npc추가` 등록 네 길 앞)
# =========================================================
def take_sheet(channel_id: str, name: str, desc: str, register) -> Optional[str]:
    """등록 관문 앞 가로채기. None = 평소처럼 등록(가로채지 않음). 문자열 = 처리했다(등록 메시지에 이 글이 실린다).

    - 라벨 없는 시트인데 이름이 잠긴 모습 · 등장 전 사람 → 보관 시트만 갈아 끼운다(별칭 해상도로 지금 모습 페이지를 덮지 않게).
    - `모습: A` → C 는 등록하지 않고 A 사람 칸에 보관 + 규칙(A→C, 돌아감 있으면 C→A). C 가 지금 모습이면 평소처럼 페이지 갈기.
    - `등장: 예` → 등록하지 않고 보관 + 등장 규칙. 이미 등장했으면 평소처럼 페이지 갈기.
    - `불러오기: 예` → 평소처럼 등록(라벨 줄 뗀 시트) + 불러오기 규칙.
    - 같은 이름이 이미 따로 등록된 NPC 면 거절(`!npc 삭제` 뒤 다시)."""
    name = _norm(name)
    if not name:
        return None
    head = parse_head(desc)
    t = _load(channel_id)
    npcs = _npcs(channel_id)
    _canon(t, npcs)
    if head is None:
        if not t:
            return None
        for pk, fn in _locked_forms(t):
            if _same(fn, name) or _form_key({fn: {}}, name):
                rec = dict((t[pk].get("forms") or {}).get(fn) or {})
                rec["sheet"] = str(desc or "").strip()
                rec["src"] = "sheet"
                rec.pop("data", None)        # 시트가 바뀌었으니 다음 바꾸기에 새 시트에서 다시 뽑는다
                t[pk]["forms"][fn] = rec
                _save(channel_id, t)
                kind = "등장 전" if not t[pk].get("active") else f"{t[pk].get('active')} 의 잠긴 모습"
                return f"{fn} ({kind} — 보관 시트만 갈았어요)"
        return None

    eff, body = head["effect"], head["body"]
    if eff == "load":
        if is_reserved(channel_id, name):
            return f"⚠ {name} — 잠긴 모습 · 등장 전 이름이라 불러오기로 등록 못 해요(`!npc 모습`)"
        key = register(name, body)
        pk = next((p for p, e in t.items() if isinstance(e, dict) and _same(e.get("active"), key)), None) or key
        ent = t.setdefault(pk, {"active": key, "forms": {}, "rules": [], "log": []})
        n = _put_rules(t, ent, key, "sheet", [_mk_rule(t, "load", key, key, "sheet", key, head)])
        _save(channel_id, t)
        return f"{key} (불러오기 규칙 {n})" if n else f"{key} (⚠ 불러오기 조건이 없어 규칙은 안 받음)"

    if eff == "appear":
        pk = _person_of(t, name)
        ent = t.get(pk) if pk else None
        ex = _resolve(npcs, name, allow_token=False)
        if ent and ent.get("active") and ex and _same(ex, ent.get("active")):
            key = register(ex, body)           # 이미 등장 — 평소처럼 페이지 갈기 + 규칙 갱신(다시 잠글 때 쓴다)
            _put_rules(t, ent, key, "sheet", [_mk_rule(t, "appear", None, key, "sheet", key, head)])
            _save(channel_id, t)
            return f"{key} (이미 등장 — 시트 갱신)"
        if ex:
            return f"⚠ {name} — 이미 따로 등록된 NPC 라 등장 규칙을 못 걸었어요(`!npc 삭제 {ex}` 뒤 다시)"
        pk = pk or name
        ent = t.setdefault(pk, {"active": None, "forms": {}, "rules": [], "log": []})
        fn = _form_key(ent["forms"], name) or name
        ent["forms"][fn] = {"sheet": body, "src": "sheet"}
        n = _put_rules(t, ent, fn, "sheet", [_mk_rule(t, "appear", None, fn, "sheet", fn, head)])
        _save(channel_id, t)
        return f"{fn} (등장 — 조건이 맞을 때까지 잠김{'' if n else ' · ⚠ 조건이 없어 규칙은 안 받음'})"

    # form
    target = head["target"]
    pk = _person_of(t, target) or _person_of(t, name)
    tkey = _resolve(npcs, target, allow_token=False)
    if pk is None:
        pk = tkey or target
    ent = t.setdefault(pk, {"active": tkey or target, "forms": {}, "rules": [], "log": []})
    if not ent.get("active"):
        return f"⚠ {name} — {pk} 는 등장 규칙 사람이라 모습을 못 걸었어요"
    forms = ent.setdefault("forms", {})
    act = ent["active"]
    if not _form_key(forms, act):
        forms[act] = {"sheet": None, "src": "first"}
    frm = _form_key(forms, target) or act
    fn = _form_key(forms, name)
    if fn and _same(fn, act):
        key = register(act, body)              # 지금 모습 시트 — 평소처럼 페이지 갈기 + 규칙 갱신
        _put_rules(t, ent, key, "sheet", _form_rules(t, frm, key, head))
        _save(channel_id, t)
        return f"{key} (지금 모습 — 시트 · 규칙 갱신)"
    if not fn:
        ex = _resolve(npcs, name, allow_token=False)
        if ex and _same(ex, act):
            key = register(act, body)
            _put_rules(t, ent, key, "sheet", _form_rules(t, frm, key, head))
            _save(channel_id, t)
            return f"{key} (지금 모습 — 시트 · 규칙 갱신)"
        if ex:
            return f"⚠ {name} — 이미 따로 등록된 NPC 라 {target} 의 모습으로 못 넣었어요(`!npc 삭제 {ex}` 뒤 다시)"
        fn = name
    forms[fn] = {"sheet": body, "src": "sheet"}
    n = _put_rules(t, ent, fn, "sheet", _form_rules(t, frm, fn, head))
    _save(channel_id, t)
    return f"{fn} ({frm} 의 다른 모습 — 잠김{'' if n else ' · ⚠ 조건이 없어 규칙은 안 받음'})"


def _form_rules(t: Dict[str, Any], frm: str, fn: str, head: Dict[str, Any]) -> List[Dict[str, Any]]:
    rules = [_mk_rule(t, "form", frm, fn, "sheet", fn, head)]
    if head.get("back"):
        rules.append(_mk_rule(t, "form", fn, frm, "sheet", fn, {"when": head["back"]}))
    return rules


# =========================================================
# 로어 분석 `activations`
# =========================================================
def apply_lore(channel_id: str, acts: Any, lore_text: str) -> Dict[str, Any]:
    """로어 분석 칸 → 표. 로어발 규칙만 새로(시트발은 남김). 지금 C 모습인데 규칙이 사라져도 모습은 그대로(진행 상태).

    D3 모습 시트: 시트 줄로 올라온 것 > 로어 원문 그 이름 대목 > 없음.
    잠글 이름(모습 C · 등장 사람)이 이미 따로 등록돼 있으면: 판이 아직 안 열렸으면(`!시작` 전) 그 시트를 보관하고
    등록을 지워 잠근다, 열려 있으면 안 잠그고 알린다(지우면 플레이 기록이 같이 간다)."""
    import domain_manager
    rep: Dict[str, Any] = {"rules": 0, "locked": [], "conflict": []}
    t = _load(channel_id)
    for ent in t.values():
        if isinstance(ent, dict):
            ent["rules"] = [r for r in (ent.get("rules") or []) if r.get("src") == "sheet"]
    rows = [a for a in (acts if isinstance(acts, list) else []) if isinstance(a, dict)]
    npcs = _npcs(channel_id)
    _canon(t, npcs)
    try:
        started = bool(domain_manager.is_session_locked(channel_id))
    except Exception:
        started = True
    names = []
    for a in rows:
        for k in ("person", "form"):
            v = _norm(_text(a.get(k), 120))
            if v and v not in names:
                names.append(v)
    try:
        import npc_manager
        sections = npc_manager.extract_npc_sections_from_lore(lore_text or "", names) if names else {}
    except Exception as e:
        logger.debug(f"[Form] lore sections skip: {e}")
        sections = {}

    lock_data: Dict[str, Dict[str, Any]] = {}

    def _lock_if_needed(fn: str) -> Any:
        """fn 이 따로 등록된 NPC 면 잠그거나(판 전) 알린다(판 중). 거짓 = 이 규칙을 안 받는다.
        잠글 때 그 등록 기록의 시트 칸(말투 · 일정 · 심리 라벨 — 페이지 밖)도 떠 둔다(등장 · 바꾸기 때 되돌린다)."""
        _np = _npcs(channel_id)
        ex = _resolve(_np, fn, allow_token=False)
        if not ex:
            return True
        if started:
            rep["conflict"].append(fn)
            return False
        try:
            import wiki_store
            txt = (wiki_store.assemble_lore_text(
                wiki_store.get_lore_sections(channel_id, wiki_store.page_id_for("character", ex))) or "").strip()
        except Exception:
            txt = ""
        _rec = _np.get(ex) or {}
        lock_data[fn] = {k: copy.deepcopy(_rec[k]) for k in SHEET_KEYS if _rec.get(k)}
        domain_manager.delete_npc(channel_id, ex)
        rep["locked"].append(ex)
        return txt or True

    for a in rows:
        eff = str(a.get("effect") or "").strip().lower()
        person = _norm(_text(a.get("person"), 120))
        if eff not in EFFECTS or not person:
            continue
        cond = {k: a.get(k) for k in ("when",) + CODE_FIELDS}
        if not _has_cond(_mk_rule(t, eff, None, person, "lore", person, cond)):
            continue        # 조건 없는 줄은 받지도 잠그지도 않는다(늘 참 · 영영 잠김 — 검토 2)
        if eff == "load":
            key = _resolve(npcs, person, allow_token=False) or person
            pk = next((p for p, e in t.items() if isinstance(e, dict) and _same(e.get("active"), key)), None) or key
            ent = t.setdefault(pk, {"active": key, "forms": {}, "rules": [], "log": []})
            r = _mk_rule(t, "load", key, key, "lore", key, cond)
            if _has_cond(r):
                ent["rules"].append(r)
                rep["rules"] += 1
            continue
        if eff == "appear":
            pk = _person_of(t, person) or person
            ent = t.get(pk)
            if ent is None:
                st = _lock_if_needed(person)
                if not st:
                    continue
                ent = t.setdefault(pk, {"active": None, "forms": {}, "rules": [], "log": []})
                sheet = st if isinstance(st, str) else (sections.get(person) or None)
                ent["forms"][person] = {"sheet": sheet, "src": "sheet" if isinstance(st, str) else "lore"}
                if lock_data.get(person):
                    ent["forms"][person]["data"] = lock_data[person]
            elif not ent.get("active"):
                fn = next(iter(ent.get("forms") or {}), person)
                rec = (ent.get("forms") or {}).get(fn) or {}
                if rec.get("src") != "sheet" and sections.get(person):
                    rec["sheet"] = sections[person]
                    rec["src"] = "lore"
                    ent["forms"][fn] = rec
            fn = next(iter(ent.get("forms") or {}), person)
            r = _mk_rule(t, "appear", None, ent.get("active") or fn, "lore", fn, cond)
            if _has_cond(r):
                ent["rules"].append(r)
                rep["rules"] += 1
            continue
        # form
        other = _norm(_text(a.get("form"), 120))
        if not other or _same(other, person):
            continue
        pk = _person_of(t, person) or _person_of(t, other)
        pkey = _resolve(npcs, person, allow_token=False)
        if pk is None:
            pk = pkey or person
        ent = t.setdefault(pk, {"active": pkey or person, "forms": {}, "rules": [], "log": []})
        if not ent.get("active"):
            continue
        forms = ent.setdefault("forms", {})
        if not _form_key(forms, ent["active"]):
            forms[ent["active"]] = {"sheet": None, "src": "first"}
        frm = _form_key(forms, person) or ent["active"]
        fn = _form_key(forms, other)
        if not fn:
            st = _lock_if_needed(other)
            if not st:
                continue
            fn = other
            forms[fn] = {"sheet": st if isinstance(st, str) else (sections.get(other) or None),
                         "src": "sheet" if isinstance(st, str) else "lore"}
            if lock_data.get(other):
                forms[fn]["data"] = lock_data[other]
        elif not _same(fn, ent["active"]):
            rec = forms.get(fn) or {}
            if rec.get("src") != "sheet" and sections.get(other):
                rec["sheet"] = sections[other]
                rec["src"] = "lore"
                rec.pop("data", None)
                forms[fn] = rec
        r = _mk_rule(t, "form", frm, fn, "lore", fn, cond)
        if _has_cond(r):
            ent["rules"].append(r)
            rep["rules"] += 1
        back = _text(a.get("back_when"))
        if back:
            ent["rules"].append(_mk_rule(t, "form", fn, frm, "lore", fn, {"when": back}))
            rep["rules"] += 1
    # 규칙도 모습도 없는 빈 칸은 치운다(로어에서 빠진 불러오기 사람 등)
    for pk in list(t.keys()):
        ent = t[pk]
        if isinstance(ent, dict) and not ent.get("rules") and len(ent.get("forms") or {}) <= 1 \
                and ent.get("active") and not ent.get("log"):
            t.pop(pk, None)
    _save(channel_id, t)
    if rep["rules"] or rep["locked"] or rep["conflict"]:
        logger.info("[Form] 로어 규칙 %d · 잠금 %s · 충돌 %s", rep["rules"], rep["locked"], rep["conflict"])
    return rep


# =========================================================
# 수명 — `!클리어` · `!npc 삭제`
# =========================================================
def on_clear(channel_id: str) -> List[str]:
    """`!클리어` 앞(reset_session_state 가 도메인을 읽기 **전**) — 첫 모습으로 되돌리고, 등장한 사람은 다시 잠근다.
    규칙 · 보관 시트는 남는다(준비 입력). 기록 · 불러오기 직전 판정은 비운다(플레이 상태)."""
    t = _load(channel_id)
    if not t:
        return []
    done: List[str] = []
    npcs = _npcs(channel_id)
    _canon(t, npcs)
    for pk in list(t.keys()):
        ent = t.get(pk)
        if not isinstance(ent, dict):
            continue
        rules = ent.get("rules") or []
        if any(r.get("effect") == "appear" for r in rules) and ent.get("active"):
            try:
                if _relock(channel_id, t, pk):
                    _save(channel_id, t)
                    done.append(f"잠금 {pk}")
            except Exception as e:
                logger.warning("[Form] 다시 잠그기 실패 %s: %s", pk, e)
            t = _load(channel_id)
        elif ent.get("active") and (ent.get("forms") or {}):
            first = next((k for k, v in ent["forms"].items() if (v or {}).get("src") == "first"), None) \
                or _form_key(ent["forms"], pk)
            if first and not _same(first, ent.get("active")):
                ok, msg = switch(channel_id, t, pk, first, by="clear")
                if ok:
                    t = _load(channel_id)
                    done.append(msg)
                else:
                    logger.warning("[Form] 클리어 되돌리기 실패 %s: %s", pk, msg)
    t = _load(channel_id)
    for ent in t.values():
        if isinstance(ent, dict):
            ent["log"] = []
            for r in ent.get("rules") or []:
                if r.get("effect") == "load":
                    r["last"] = False
                    for _k in ("t", "p", "ft"):
                        r.pop(_k, None)
    _save(channel_id, t)
    if done:
        logger.info("[Form] 클리어: %s", " / ".join(done))
    return done


def forget(channel_id: str, name: str) -> Optional[str]:
    """`!npc 삭제 {이름}` 이 잠긴 모습 · 등장 전 사람을 가리키면 그 보관분을 지운다. 아니면 None."""
    t = _load(channel_id)
    for pk, fn in _locked_forms(t):
        if _same(fn, name) or _form_key({fn: {}}, name):
            ent = t[pk]
            if not ent.get("active"):
                t.pop(pk, None)
                _save(channel_id, t)
                return f"🗑️ 등장 전 **{fn}** — 보관 시트 · 규칙 삭제"
            ent["forms"].pop(fn, None)
            ent["rules"] = [r for r in (ent.get("rules") or [])
                            if not (_same(r.get("from"), fn) or _same(r.get("to"), fn))]
            if not ent["rules"] and len(ent.get("forms") or {}) <= 1:
                t.pop(pk, None)
            _save(channel_id, t)
            return f"🗑️ {pk} 의 잠긴 모습 **{fn}** — 보관 시트 · 규칙 삭제"
    return None


def forget_person(channel_id: str, key: str) -> bool:
    """등록 NPC 를 지운 뒤 — 그 사람(지금 모습이 key)의 표 칸째 지운다."""
    t = _load(channel_id)
    hit = [pk for pk, e in t.items() if isinstance(e, dict) and e.get("active") and _same(e.get("active"), key)]
    for pk in hit:
        t.pop(pk, None)
    if hit:
        _save(channel_id, t)
    return bool(hit)


# =========================================================
# `!npc 모습` — 보기 · 손으로 바꾸기
# =========================================================
_EFF_KO = {"form": "모습", "appear": "등장", "load": "불러오기"}


def _rule_line(r: Dict[str, Any]) -> str:
    bits = []
    if r.get("when"):
        bits.append(f"조건: {r['when']}")
    for k, ko in (("items", "소지품"), ("with", "만남"), ("at", "장소"), ("words", "낱말")):
        if r.get(k):
            bits.append(f"{ko}: {', '.join(r[k])}")
    if r.get("expr"):
        bits.append(f"식: {r['expr']}")
    eff = r.get("effect")
    if eff == "form":
        head = f"{r.get('from')} → {r.get('to')}"
    elif eff == "appear":
        head = f"{r.get('to')} 등장"
    else:
        head = f"{r.get('to')} 불러오기"
    src = "시트" if r.get("src") == "sheet" else "로어"
    return f"`{r.get('id')}` {head} [{src}] — " + (" · ".join(bits) or "조건 없음")


def view(channel_id: str, name: str = "") -> str:
    t = _load(channel_id)
    if not t:
        return "🎭 모습 · 등장 · 불러오기 규칙이 없어요. (시트 머리 `모습:` · `등장: 예` · `불러오기: 예` 또는 로어)"
    npcs = _npcs(channel_id)
    _canon(t, npcs)
    keys = list(t.keys())
    if name:
        pk = _person_of(t, name)
        if not pk:
            return f"⚠️ '{name}' — 모습 표에 없는 이름이에요."
        keys = [pk]
    loc, nid = _pc_place(channel_id, None)
    out = []
    for pk in keys:
        ent = t[pk]
        act = ent.get("active")
        forms = ent.get("forms") or {}
        kinds = {r.get("effect") for r in (ent.get("rules") or [])}
        if "appear" in kinds and not act:
            head = f"🎭 **{pk}** — 등장 전(잠김)"
        elif "load" in kinds and not forms:
            head = f"🎭 **{pk}** — 불러오기"
        else:
            head = f"🎭 **{pk}** — 지금 모습: **{act or '-'}**"
        lines = [head]
        if forms and not ("load" in kinds and len(kinds) == 1):
            fl = []
            for fn, rec in forms.items():
                s = "시트 있음" if str((rec or {}).get("sheet") or "").strip() else (
                    "지금 페이지" if _same(fn, act) else "시트 없음 — 이름만 바뀜")
                fl.append(f"{fn}({s})")
            lines.append("· 모습: " + ", ".join(fl))
        for r in _effective_rules(ent):
            ln = "· " + _rule_line(r)
            if _live(ent, r, npcs):
                ok, why = _code_ok(channel_id, r, uid="", text=None, npcs=npcs, full=True,
                                   loc=loc, nid=nid, relevant=None)
                notes = list(why)
                if r.get("words"):
                    notes.append("낱말은 턴마다 입력 · 직전 산문에서")
                if r.get("when"):
                    notes.append("조건 문장은 그 턴 분석 콜이 판정")
                if r.get("effect") == "load" and r.get("last"):
                    notes.append("직전 판정 참 — 다시 거짓이 됐다 참이 돼야 또 부름")
                ln += "\n   지금: " + (" · ".join(notes) if notes else "코드 칸 다 참")
            else:
                ln += "\n   지금: 이 모습에선 안 섬"
            lines.append(ln)
        lg = [x for x in (ent.get("log") or []) if isinstance(x, dict)][-3:]
        if lg:
            lines.append("· 최근: " + " · ".join(
                f"T{x.get('turn')} {x.get('from') or ''}→{x.get('to')}({x.get('by')})" for x in lg))
        out.append("\n".join(lines))
    return "\n\n".join(out)[:1900]


def manual(channel_id: str, rest: str) -> str:
    """`!npc 모습 {이름} {모습|등장}` — 조건 무시하고 바꾼다(by: manual). 이름에 공백이 흔해 갈라 보며 맞춘다."""
    t = _load(channel_id)
    if not t:
        return "⚠️ 모습 표가 비어 있어요."
    _canon(t, _npcs(channel_id))
    toks = str(rest or "").split()
    for i in range(len(toks) - 1, 0, -1):
        left, right = " ".join(toks[:i]), " ".join(toks[i:])
        pk = _person_of(t, left)
        if not pk:
            continue
        ent = t[pk]
        if right.lower() in ("등장", "appear") or (not ent.get("active") and _form_key(ent.get("forms") or {}, right)):
            ok, msg = appear(channel_id, t, pk, by="manual")
            return f"🎭 등장: **{msg}**" if ok else f"⚠️ {msg.lstrip('⚠ ')}"
        if _form_key(ent.get("forms") or {}, right):
            ok, msg = switch(channel_id, t, pk, right, by="manual")
            return f"🎭 모습: **{msg}**" if ok else f"⚠️ {msg.lstrip('⚠ ')}"
    return None
