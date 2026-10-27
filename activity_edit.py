# -*- coding: utf-8 -*-
"""Activity 편집 — [2026-10-23 C60 M2c] 화면에서 값을 고친다.

스펙 파티쳇수정/output_artifact/C60_M2표면등록_지도·스펙_2026-10-23.md §4·§6·§7.

**쓰기는 새 길을 만들지 않는다** — OOC 편집이 지나는 문을 그대로 지난다(한 함수 두 입구, §7 S4).
  선언 값·PC 활력/평형·소지품(E1·E2·옛 E3) = custom_vars.apply_declared_edit
  관계(E4)                                  = domain_manager.apply_ooc_relation_edits(origin="activity")
  시간(E5)                                  = ooc_edits.set_clock
  장소(E5)                                  = ooc_edits.apply_place_edits(op=pc)

**겹쳐 쓰기를 막는 자리**(§4.1 · §7 S3) — 셋 다 통과해야 쓴다.
  ① 채널 턴 잠금(turn_locks) — 턴이 도는 중이면 기다리고, LOCK_WAIT 를 넘기면 409 busy
  ② 배경 큐 비우기 — DRAIN_WAIT 안에 안 비면 409 draining(늦게 끝난 배경 쓰기가 편집을 덮지 않게)
  ③ rev(화면이 본 턴 번호) ≠ 지금 턴 → 409 stale + 새 데이터(화면이 낡은 값을 보고 덮어쓰지 않게)

레티어스 10-23 결정
  · 편집은 전체(E1~E5) · 확인창 없음 · 다음 턴 렌더엔 알림 줄 없음(값만, OOC 와 같다)
  · `!다시`에도 편집은 산다 — 편집 일지(sqlite activity_edits)를 스냅샷 복원 직후 다시 얹는다(replay_after)
  · ↶ = **방금 고친 값 하나** 원클릭 되돌리기
  · 두 번 세기 의심은 로그만 — custom_vars.apply_deltas 의 `[EditEcho]`
"""
import asyncio
import logging
from typing import Any, Dict, List, Optional, Tuple

import config

logger = logging.getLogger("Activity")

SOURCE = "activity"          # 쓰기 문 도장(last_change.source · 관계 origin)
LOCK_WAIT = 8.0              # 초 — 턴이 이보다 오래 돌면 409 busy(화면이 "끝나면 다시")
DRAIN_WAIT = 5.0             # 초 — 배경 큐가 이 안에 안 비면 409 draining
_POLL = 0.2

ACTIONS = ("decl.set", "decl.item", "rel.set", "rel.remove", "clock.set", "place.set",
           "mod.toggle", "mod.set",            # [2026-10-23 C72 A5] 모듈 앱 — 켜고 끄기 · 설정 칸
           "app.click",                        # [2026-10-23 C73 M7a] 코드 앱 누르기(↶ 없음)
           "undo")
# [C72 §5] 이 액션들은 `!다시` 재적용(replay_after)이 건너뛴다 — 넘겨 얹기(domain_manager.redo_carry)가 지금 값을 이미 들고 온다.
#   다시 얹으면 오히려 틀린다(Activity 로 끔 → `!모듈` 로 켬 → `!다시` 면 일지의 옛 "끔"이 덮어쓴다).
CARRIED_ACTIONS = ("mod.toggle", "mod.set")
VIEWS = ("edit", "modules")   # 응답 data 로 돌려줄 쪽 — 편집 화면(옛) · 모듈 앱(C72) · [C73] `app:<id>` = 그 코드 앱 화면
_FAIL_HEADS = ("⚠", "✗")

MSG_BUSY = "턴이 도는 중이에요. 끝나면 다시 눌러 주세요."
MSG_DRAINING = "지난 턴 정리가 아직 도는 중이에요. 잠시 뒤 다시 눌러 주세요."
MSG_STALE = "그 사이 턴이 지나서 새로 받았어요."
MSG_FORBIDDEN = "이 채널 참가자만 고칠 수 있어요."
MSG_NO_UNDO = "되돌릴 수 있는 건 방금 고친 것 하나뿐이에요."
MSG_SETTLING = "지난 턴 신고를 정리하는 중이에요. 잠시 뒤 다시 눌러 주세요."


# =========================================================
# 작은 도우미
# =========================================================

def _turn(channel_id: str) -> int:
    try:
        import domain_manager
        return int((domain_manager.get_world_state(channel_id) or {}).get("turn_index", 0) or 0)
    except Exception:
        return 0


def _is_participant(channel_id: str, uid: str) -> bool:
    try:
        import domain_manager
        return bool(uid) and domain_manager.get_participant_data(channel_id, str(uid)) is not None
    except Exception:
        return False


def _ok_line(line: Any) -> bool:
    s = str(line or "").strip()
    return bool(s) and not s.startswith(_FAIL_HEADS)


def _res(ok: bool, line: str, receipt: str = "", undo: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    return {"ok": bool(ok), "line": str(line or ""), "receipt": receipt if ok else "",
            "undo": undo if ok else None}


def _clock_now(channel_id: str) -> Dict[str, Any]:
    """world_state 사본 + 시계 초기화(_init_clock). 옛·새 채널은 hour/year 가 첫 시간 쓰기 전까지 없다 —
    화면도 ↶ 도 set_clock 이 보는 그 값(초기화된 값)을 봐야 "옛 시각"이 비지 않는다. 저장은 안 한다."""
    import domain_manager as dm
    w = dict(dm.get_world_state(channel_id) or {})
    try:
        import game_world
        game_world._init_clock(w)
    except Exception:
        pass
    return w


def _num_or_none(v: Any) -> Optional[int]:
    try:
        return None if v is None or str(v).strip() == "" else int(round(float(v)))
    except (TypeError, ValueError):
        return None


# =========================================================
# 화면 데이터 — 고치기 탭
# =========================================================

def _scalar_item(cv, channel_id: str, uid: str, name: str, spec: Dict[str, Any],
                 value: Any, npc: str = "", label: str = "") -> Optional[Dict[str, Any]]:
    vtype = str(spec.get("type", "gauge"))
    it: Dict[str, Any] = {"name": name, "npc": npc, "label": label or (f"{name} · {npc}" if npc else name)}
    if vtype == "enum":
        it.update(kind="select", value=str(value if value not in (None, "") else spec.get("init") or ""),
                  options=[str(x) for x in (spec.get("stages") or [])])
    elif vtype == "text":
        it.update(kind="text", value=str(value if value is not None else spec.get("init") or ""),
                  maxlength=int(getattr(cv, "TEXT_MAX", 60)))
    elif vtype in ("gauge", "counter"):
        lo, hi = (list(spec.get("range") or [0, 0]) + [0, 0])[:2]
        n = _num_or_none(value)
        it.update(kind="number", value=n if n is not None else _num_or_none(spec.get("init")),
                  min=_num_or_none(lo), max=_num_or_none(hi))
    else:
        return None
    return it


def build_edit_view(channel_id: str, uid: str) -> Dict[str, Any]:
    """고치기 탭 데이터. 화면은 이 모양만 안다 — 값 계산·검사는 늘 서버(쓰기 문)가 한다."""
    import custom_vars as cv
    import domain_manager as dm
    out: Dict[str, Any] = {"rev": _turn(channel_id), "groups": []}
    groups: List[Dict[str, Any]] = out["groups"]
    try:
        decl = cv.get_declarations(channel_id) or {}
        vals = cv.get_values(channel_id) or {}
    except Exception as e:
        logger.debug(f"[Activity] edit view decl skipped: {e}")
        decl, vals = {}, {}

    # --- PC (활력·평형) — 활력 모듈이 꺼진 채널이면 수치가 동결이라 안 그린다 ---
    pc_items: List[Dict[str, Any]] = []
    try:
        if dm.is_vigor_composure_active(channel_id):
            for nm in ("활력", "평형"):
                spec = decl.get(nm)
                if isinstance(spec, dict):
                    it = _scalar_item(cv, channel_id, uid, nm, spec, cv.get_system_value(channel_id, nm, uid))
                    if it:
                        pc_items.append(it)
    except Exception as e:
        logger.debug(f"[Activity] edit view pc skipped: {e}")
    if pc_items:
        groups.append({"key": "pc", "title": "PC", "icon": "user", "items": pc_items})

    # --- 선언 값(스칼라) · 목록 ---
    scal: List[Dict[str, Any]] = []
    lists: List[Dict[str, Any]] = []
    for nm, spec in decl.items():
        if not isinstance(spec, dict) or spec.get("system"):
            continue
        try:
            if cv.is_derived(spec):
                continue                                    # 파생값은 식이 매턴 다시 계산한다(쓰기 거부)
        except Exception:
            pass
        vtype = str(spec.get("type", "gauge"))
        cur = (vals.get(nm) or {}).get("value")
        if vtype == "list":
            flds = cv.item_fields(spec) if spec.get("fields") else {}
            items = []
            for k, rec in (cur.items() if isinstance(cur, dict) else []):
                r = rec if isinstance(rec, dict) else {"n": rec}
                items.append({"name": str(k), "n": _num_or_none(r.get("n")),
                              "fields": {f: _num_or_none(r.get(f)) for f in flds}})
            lists.append({"name": nm, "builtin": bool(spec.get("builtin")),
                          "mode": str(spec.get("item_mode", "stock")),
                          "fields": [{"name": f, "min": rng[0], "max": rng[1]} for f, rng in flds.items()],
                          "max_items": cv.list_max(spec), "items": items})
            continue
        if str(spec.get("scope")) == "npc":
            for who, v in (cur.items() if isinstance(cur, dict) else []):
                it = _scalar_item(cv, channel_id, uid, nm, spec, v, npc=str(who))
                if it:
                    scal.append(it)
            continue
        it = _scalar_item(cv, channel_id, uid, nm, spec, cur)
        if it:
            scal.append(it)
    if scal:
        groups.append({"key": "values", "title": "선언 값", "icon": "globe", "items": scal})
    lists.sort(key=lambda L: (0 if L["builtin"] else 1))     # 소지품이 맨 위
    for L in lists:
        groups.append({"key": "list", "title": L["name"], "icon": "notebook" if L["builtin"] else "layers",
                       "list": L})

    # --- 관계(NPC → 이 PC) ---
    try:
        import sqlite_store
        mask = (dm.get_participant_data(channel_id, uid) or {}).get("mask") or ""
        if mask:
            rel = [{"npc": e["source"], "bond": int(e.get("bond") or 0), "tension": int(e.get("tension") or 0)}
                   for e in (sqlite_store.get_edges(channel_id, target=mask) or [])]
            if rel:
                groups.append({"key": "relations", "title": "관계", "icon": "handshake", "relations": rel,
                               "bond_range": [-100, 100], "tension_range": [0, 100]})
    except Exception as e:
        logger.debug(f"[Activity] edit view relations skipped: {e}")

    # --- 시간·장소 ---
    try:
        w = _clock_now(channel_id)
        clock = {k: _num_or_none(w.get(k)) for k in ("year", "month", "day", "hour", "minute")}
        clock["weather"] = str(w.get("weather") or "")
        clock["months"] = int(getattr(config, "CALENDAR_MONTHS_PER_YEAR", 12) or 12)
        clock["days"] = int(getattr(config, "CALENDAR_DAYS_PER_MONTH", 30) or 30)
        groups.append({"key": "world", "title": "시간·장소", "icon": "clock", "clock": clock,
                       "location": str(dm.get_current_location(channel_id) or "")})
    except Exception as e:
        logger.debug(f"[Activity] edit view world skipped: {e}")
    return out


# =========================================================
# 집행기 — 편집 한 건. 잠금·rev·일지는 act() 몫(되감기 재적용도 이걸 부른다)
# =========================================================

def _current_scalar(cv, channel_id: str, uid: str, name: str, spec: Dict[str, Any], npc: str) -> Any:
    entry = (cv.get_values(channel_id) or {}).get(name) or {}
    if spec.get("per_actor"):
        return cv.get_system_value(channel_id, name, uid)
    if str(spec.get("scope")) == "npc":
        who = cv.resolve_npc(channel_id, npc) or npc
        per = entry.get("value") if isinstance(entry.get("value"), dict) else {}
        return per.get(who, spec.get("init"))
    v = entry.get("value")
    return spec.get("init") if v is None else v


def _exec_decl_set(channel_id: str, uid: str, a: Dict[str, Any]) -> Dict[str, Any]:
    import custom_vars as cv
    name = str(a.get("name") or "").strip()
    npc = str(a.get("npc") or "").strip()
    spec = (cv.get_declarations(channel_id) or {}).get(name)
    if not isinstance(spec, dict) or str(spec.get("type")) == "list":
        return _res(False, f"✗ 모르는 값: {name or '(비어 있음)'}")
    old = _current_scalar(cv, channel_id, uid, name, spec, npc)
    line, rec = cv.apply_declared_edit(channel_id, uid, {"field": "declared", "name": name, "op": "set",
                                                          "value": a.get("value"), "npc": npc}, source=SOURCE)
    if not rec:
        return _res(False, line)
    frm = rec.get("from", old)
    label = name + (f"·{npc}" if npc else "")
    undo = ({"action": "decl.set", "name": name, "npc": npc, "value": frm} if frm is not None else None)
    return _res(True, line, f"{label} {frm} → {rec.get('to')}", undo)


def _exec_decl_item(channel_id: str, uid: str, a: Dict[str, Any]) -> Dict[str, Any]:
    import custom_vars as cv
    name = str(a.get("name") or "").strip()
    item = str(a.get("item") or "").strip()
    op = str(a.get("op") or "set").strip().lower()
    field = str(a.get("field") or "").strip()
    spec = (cv.get_declarations(channel_id) or {}).get(name)
    if not isinstance(spec, dict) or str(spec.get("type")) != "list":
        return _res(False, f"✗ 모르는 목록: {name or '(비어 있음)'}")
    if not item:
        return _res(False, f"✗ {name} — 항목 이름이 없다")
    items = ((cv.get_values(channel_id) or {}).get(name) or {}).get("value") or {}
    real = cv.match_item_key(items, item) if isinstance(items, dict) else ""
    old = items.get(real) if real else None
    old_rec = old if isinstance(old, dict) else ({"n": old} if old is not None else None)

    if op in ("set", "qty"):
        ed: Dict[str, Any] = {"field": "declared", "name": name, "op": "set", "item": real or item,
                              "value": a.get("qty", a.get("value"))}
        if field:
            ed["record"] = field
        line, rec = cv.apply_declared_edit(channel_id, uid, ed, source=SOURCE)
        if not rec:
            return _res(False, line)
        tgt = rec.get("item") or real or item
        if old_rec is None:
            undo = {"action": "decl.item", "name": name, "item": tgt, "op": "remove"}
        else:
            back = old_rec.get(field) if field else old_rec.get("n")
            undo = {"action": "decl.item", "name": name, "item": tgt, "op": "set", "qty": back,
                    **({"field": field} if field else {})}
        head = f"{name} {tgt}" + (f" {field}" if field else "")
        return _res(True, line, f"{head} {rec.get('from', 0)} → {rec.get('to', 0)}", undo)

    if op == "add":
        line, rec = cv.apply_declared_edit(channel_id, uid, {"field": "declared", "name": name, "op": "add",
                                                              "item": item}, source=SOURCE)
        if not rec:
            return _res(False, line)
        tgt = rec.get("item") or item
        return _res(True, line, f"{name} + {tgt}",
                    {"action": "decl.item", "name": name, "item": tgt, "op": "remove"})

    if op == "remove":
        if not real:
            return _res(False, f"✗ {name} — 그런 항목이 없다: {item}")
        line, rec = cv.apply_declared_edit(channel_id, uid, {"field": "declared", "name": name, "op": "remove",
                                                              "item": real}, source=SOURCE)
        if not rec:
            return _res(False, line)
        n_old = (old_rec or {}).get("n")
        # 되돌리기 = 옛 수량으로 다시 세운다. 레코드 목록의 나머지 필드는 init 로 선다(남기는 건 수량 하나).
        undo = ({"action": "decl.item", "name": name, "item": real, "op": "set", "qty": n_old}
                if n_old is not None and not spec.get("fields")
                else {"action": "decl.item", "name": name, "item": real, "op": "add"})
        return _res(True, line, f"{name} − {real}", undo)

    if op == "rename":
        to = str(a.get("to") or "").strip()
        if not real or not to:
            return _res(False, f"✗ {name} — 이름 정정엔 옛 이름과 새 이름이 다 있어야 한다")
        line, rec = cv.apply_declared_edit(channel_id, uid, {"field": "declared", "name": name, "op": "rename",
                                                              "item": real, "to": to}, source=SOURCE)
        if not rec:
            return _res(False, line)
        return _res(True, line, f"{name} {real} → {rec.get('item') or to}",
                    {"action": "decl.item", "name": name, "item": rec.get("item") or to, "op": "rename", "to": real})

    return _res(False, f"✗ {name} — 목록에 쓸 수 없는 지시({op or '?'})")


def _edge(channel_id: str, key: str, mask: str) -> Optional[Dict[str, Any]]:
    import domain_manager as dm
    import sqlite_store
    try:
        mk = dm._mind(channel_id, key) or key
    except Exception:
        mk = key
    return sqlite_store.get_edge(channel_id, mk, mask) or (sqlite_store.get_edge(channel_id, key, mask)
                                                            if mk != key else None)


def _rel_key(channel_id: str, npc: str) -> str:
    import domain_manager as dm
    try:
        return dm.find_equivalent_npc_key(dm.get_npcs(channel_id) or {}, npc) or npc
    except Exception:
        return npc


def _exec_rel_set(channel_id: str, uid: str, a: Dict[str, Any]) -> Dict[str, Any]:
    import domain_manager as dm
    npc = str(a.get("npc") or "").strip()
    mask = (dm.get_participant_data(channel_id, uid) or {}).get("mask") or ""
    if not npc:
        return _res(False, "⚠️ 관계: 인물 이름이 없어")
    if not mask:
        return _res(False, "⚠️ 관계: `!가면`이 없어 적용하지 못했어")
    key = _rel_key(channel_id, npc)
    old = _edge(channel_id, key, mask)
    e: Dict[str, Any] = {"field": "relation", "action": "set", "key": npc}
    for k in ("bond", "tension"):
        n = _num_or_none(a.get(k))
        if n is not None:
            e[k] = n
    lines = dm.apply_ooc_relation_edits(channel_id, uid, [e], origin=SOURCE)
    line = lines[0] if lines else "⚠️ 관계: 변화 없음"
    if not _ok_line(line):
        return _res(False, line)
    new = _edge(channel_id, key, mask) or {}
    ob, ot = (old or {}).get("bond"), (old or {}).get("tension")
    receipt = (f"{key} bond {ob if old else '—'} → {new.get('bond')}"
               f" · tension {ot if old else '—'} → {new.get('tension')}")
    undo = ({"action": "rel.set", "npc": key, "bond": ob, "tension": ot} if old
            else {"action": "rel.remove", "npc": key})
    return _res(True, line, receipt, undo)


def _exec_rel_remove(channel_id: str, uid: str, a: Dict[str, Any]) -> Dict[str, Any]:
    import domain_manager as dm
    npc = str(a.get("npc") or "").strip()
    mask = (dm.get_participant_data(channel_id, uid) or {}).get("mask") or ""
    key = _rel_key(channel_id, npc)
    old = _edge(channel_id, key, mask) if mask else None
    lines = dm.apply_ooc_relation_edits(channel_id, uid, [{"field": "relation", "action": "remove", "key": npc}],
                                        origin=SOURCE)
    line = lines[0] if lines else "⚠️ 관계: 변화 없음"
    if not _ok_line(line):
        return _res(False, line)
    undo = ({"action": "rel.set", "npc": key, "bond": old.get("bond"), "tension": old.get("tension")}
            if old else None)
    return _res(True, line, f"{key} 관계 지움", undo)


_CLOCK_KEYS = ("year", "month", "day", "hour", "minute")


def _clock_text(c: Dict[str, Any]) -> str:
    try:
        return (f"{int(c.get('year') or 1)}년 {int(c.get('month') or 1)}월 {int(c.get('day') or 1)}일 "
                f"{int(c.get('hour') or 0):02d}:{int(c.get('minute') or 0):02d}")
    except (TypeError, ValueError):
        return "—"


def _exec_clock_set(channel_id: str, uid: str, a: Dict[str, Any]) -> Dict[str, Any]:
    import domain_manager as dm
    import ooc_edits
    w = _clock_now(channel_id)
    old = {k: w.get(k) for k in _CLOCK_KEYS}
    old_wx = str(w.get("weather") or "")
    kw = {k: a.get(k) for k in _CLOCK_KEYS if a.get(k) not in (None, "")}
    if str(a.get("weather") or "").strip():
        kw["weather"] = str(a.get("weather")).strip()
    line = ooc_edits.set_clock(channel_id, **kw)
    if not _ok_line(line):
        return _res(False, line)
    w2 = dm.get_world_state(channel_id) or {}
    undo: Dict[str, Any] = {"action": "clock.set", **{k: v for k, v in old.items() if v is not None}}
    if old_wx and "weather" in kw and kw["weather"] != old_wx:
        undo["weather"] = old_wx
    rc = f"시간 {_clock_text(old)} → {_clock_text(w2)}"
    if "weather" in kw:
        rc += f" · 날씨 {old_wx or '—'} → {w2.get('weather') or '—'}"
    return _res(True, line, rc, undo)


def _exec_place_set(channel_id: str, uid: str, a: Dict[str, Any]) -> Dict[str, Any]:
    import domain_manager as dm
    import ooc_edits
    loc = str(a.get("location") or "").strip()[:60]
    if not loc:
        return _res(False, "⚠ 위치: 장소 이름이 없어")
    old = str(dm.get_current_location(channel_id) or "")
    lines = ooc_edits.apply_place_edits(channel_id, [{"op": "pc", "value": loc}])
    line = lines[0] if lines else "⚠ 위치: 변화 없음"
    if not _ok_line(line):
        return _res(False, line)
    new = str(dm.get_current_location(channel_id) or loc)
    return _res(True, line, f"장소 {old or '—'} → {new}",
                {"action": "place.set", "location": old} if old else None)


# ---------------------------------------------------------
# [2026-10-23 C72 A5] 모듈 — 켜고 끄기 · 설정 칸. 쓰기 함수는 `!모듈` 명령과 같다(module_frame.set_module_on · Setting.set).
#   module 인자 = 코드 모듈 id 또는 선언 모듈 `#번호`(쪽이 그렇게 싣는다 — 이름표가 바뀌어도 번호는 안 바뀐다).
# ---------------------------------------------------------
def _bundle_title(channel_id: str, source: Any) -> str:
    """선언 모듈 → `#번호 이름표`(영수증 · 줄)."""
    try:
        import output_router as _or
        for b in _or.bundles(channel_id):
            if b["source"] == source:
                return f"#{b['no']} {b['label']}"
        return _or.bundle_label(source)
    except Exception:
        return str(source or "")


def _exec_mod_toggle(channel_id: str, uid: str, a: Dict[str, Any]) -> Dict[str, Any]:
    import module_frame as mf
    word = str(a.get("module") or "").strip()
    on = a.get("on")
    if not isinstance(on, bool):
        return _res(False, "⚠ 모듈: 켬/끔 값이 이상해")
    kind, target, _cands = mf.resolve_module(channel_id, word)
    if not kind:
        return _res(False, f"⚠ 모듈: 못 찾았어 ({word})")
    if kind == "code" and (target.shelf != "toggle" or target.toggle is None):
        return _res(False, f"⚠ {target.name}: 늘 켜져 있는 모듈이야")
    ok, line = mf.set_module_on(channel_id, kind, target, on)
    if not ok:
        return _res(False, "⚠ " + line)
    if kind == "code":
        name = target.name
    elif kind == "app":   # [C73] 코드 앱 — 앱 표 켬끔
        name = _app_title(channel_id, target)
    else:
        name = _bundle_title(channel_id, target)
    return _res(True, "✓ " + line, f"{name} {'켬' if on else '끔'}",
                {"action": "mod.toggle", "module": word, "on": not on})


def _app_title(channel_id: str, app_id: Any) -> str:
    try:
        import app_host
        a = app_host.get_app(channel_id, str(app_id))
        return app_host.label_of(a) if a else str(app_id)
    except Exception:
        return str(app_id or "")


def _opt_label(st: Any, v: Any) -> str:
    if st.kind == "switch":
        return "켬" if v else "끔"
    for val, lb in (st.options or ()):
        if str(val) == str(v):
            return str(lb)
    return str(v if v not in (None, "") else "—")


def _exec_mod_set(channel_id: str, uid: str, a: Dict[str, Any]) -> Dict[str, Any]:
    import module_frame as mf
    word = str(a.get("module") or "").strip()
    key = str(a.get("key") or "").strip()
    kind, target, _cands = mf.resolve_module(channel_id, word)
    if kind == "app":   # [C73] 코드 앱 설정 칸(manifest settings — 값은 앱 표, `!다시`에 산다)
        import app_host
        ok, line, old = app_host.set_setting(channel_id, str(target), key, a.get("value"))
        if not ok:
            return _res(False, "⚠ 설정: " + line)
        return _res(True, "✓ " + line, line, {"action": "mod.set", "module": word, "key": key, "value": old})
    if kind != "code":
        return _res(False, f"⚠ 설정: 설정 칸이 있는 모듈이 아니야 ({word})")
    st = mf.setting_of(target, key)
    if st is None:
        return _res(False, f"⚠ 설정: {target.name}에 그런 칸이 없어 ({key})")
    v = a.get("value")
    if st.kind == "switch":
        if not isinstance(v, bool):
            return _res(False, f"⚠ {st.label}: 켬/끔만 돼")
    elif st.kind == "select":
        if str(v) not in {str(o[0]) for o in (st.options or ())}:
            return _res(False, f"⚠ {st.label}: 고를 수 있는 값이 아니야")
        v = str(v)
    else:
        return _res(False, f"⚠ {st.label}: 모르는 칸 종류")
    old = st.get(channel_id)
    st.set(channel_id, v)
    new = st.get(channel_id)
    rc = f"{target.name} · {st.label} {_opt_label(st, old)} → {_opt_label(st, new)}"
    return _res(True, "✓ " + rc, rc, {"action": "mod.set", "module": word, "key": key, "value": old})


def _exec_app_kv(channel_id: str, uid: str, a: Dict[str, Any]) -> Dict[str, Any]:
    """[2026-10-23 C73 M7a] `!다시` 재적용 몫 — 코드 앱 누르기가 쓴 값을 **그대로** 다시 적는다(Lua 는 안 돈다 — 굴림이
    바뀌면 안 된다). 화면 입구(ACTIONS)엔 없다 — 일지에만 서는 액션이다."""
    import app_host
    # [10-24 M7b 검토] 보고는 그 누르기 몫만 · 제 열쇠로(연속 `!다시`에도 한 번) — app_host.apply_journal
    ok = app_host.apply_journal(channel_id, str(a.get("app") or ""), a.get("writes") or {}, _turn(channel_id),
                                reports=a.get("reports") or [], token=str(a.get("token") or ""))
    return _res(ok, "✓ 앱 장부 다시 얹음" if ok else "⚠ 앱 장부: 다시 얹을 게 없어")


_EXEC = {"decl.set": _exec_decl_set, "decl.item": _exec_decl_item, "rel.set": _exec_rel_set,
         "rel.remove": _exec_rel_remove, "clock.set": _exec_clock_set, "place.set": _exec_place_set,
         "mod.toggle": _exec_mod_toggle, "mod.set": _exec_mod_set,
         "app.kv": _exec_app_kv}   # [C73] 일지 전용(ACTIONS 밖)


def execute(channel_id: str, uid: str, edit: Dict[str, Any]) -> Dict[str, Any]:
    """편집 한 건(잠금·rev 검사 없이). edit = {"action": …, 인자…}. 예외는 실패 한 줄로 접는다."""
    fn = _EXEC.get(str((edit or {}).get("action") or ""))
    if fn is None:
        return _res(False, f"✗ 모르는 편집: {(edit or {}).get('action')}")
    try:
        return fn(str(channel_id), str(uid or ""), dict(edit))
    except Exception as e:
        logger.warning(f"[Activity] edit {edit.get('action')} 실패: {e}")
        return _res(False, "⚠ 편집 중 오류")


# =========================================================
# 편집 일지 — !다시 재적용(§6.1)
# =========================================================

def replay_after(channel_id: str, snap_turn: int) -> int:
    """스냅샷 복원 **직후**, 다시 도는 턴 **앞**에 부른다. 되돌린 지점 뒤(after_turn > snap_turn)에 적힌 편집을
    적힌 순서대로 다시 얹는다(↶ 행도 편집이라 같이 — 결과가 같다). 일지는 다시 적지 않는다. 돌려줌 = 얹은 수.
    편집은 전부 **절대값 쓰기**라 같은 행을 두 번 얹어도 결과가 같다(멱등) — 연속 `!다시`도 안전하다."""
    try:
        import sqlite_store
        rows = sqlite_store.read_activity_edits(channel_id, after_turn_gt=int(snap_turn))
    except Exception as e:
        logger.debug(f"[Activity] replay read skipped: {e}")
        return 0
    n = 0
    for r in rows:
        if str((r.get("edit") or {}).get("action") or "") in CARRIED_ACTIONS:
            continue        # [C72 §5] 넘겨 얹기가 이미 지금 값을 들고 왔다
        res = execute(channel_id, r.get("uid") or "", r.get("edit") or {})
        if res.get("ok"):
            n += 1
    if rows:
        logger.info("[Activity] !다시 — 편집 일지 %d/%d건 다시 얹음 (after t%s)", n, len(rows), snap_turn)
    return n


# =========================================================
# 입구 — POST /api/act
# =========================================================

async def _acquire(lock: asyncio.Lock, wait: float) -> bool:
    """wait 초 안에 잠금을 잡으면 True. (wait_for(lock.acquire()) 의 취소 경합을 피하려고 폴링한다.)"""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + max(0.0, float(wait))
    while lock.locked():
        if loop.time() >= deadline:
            return False
        await asyncio.sleep(_POLL)
    await lock.acquire()
    return True


async def _drain(channel_id: str) -> bool:
    try:
        import background_task_queue as btq
        return bool(await btq.wait_for_channel_tasks(channel_id, timeout=DRAIN_WAIT))
    except Exception as e:
        logger.debug(f"[Activity] drain check skipped: {e}")
        return True


def _undo(channel_id: str, uid: str, args: Dict[str, Any], now: int) -> Tuple[int, Dict[str, Any]]:
    import sqlite_store
    last = sqlite_store.last_activity_edit(channel_id)
    rid = _num_or_none(args.get("id"))
    if (not last or rid is None or int(last.get("id") or 0) != rid or last.get("kind") != "edit"
            or last.get("undone") or str(last.get("uid") or "") != str(uid)
            or int(last.get("after_turn") or 0) != now or not last.get("undo")):
        return 409, {"error": "no_undo", "message": MSG_NO_UNDO}
    res = execute(channel_id, uid, last["undo"])
    if res.get("ok"):
        sqlite_store.mark_activity_edit_undone(channel_id, rid)
        sqlite_store.append_activity_edit(channel_id, now, uid, last["undo"], None,
                                          "↶ " + str(last.get("receipt") or ""), kind="undo")
        logger.info("[Activity] undo uid=%s ch=%s #%s → %s", uid, channel_id, rid, res.get("line"))
    return 200, {"ok": res["ok"], "line": res["line"], "receipt": "", "undo_id": None}


def _view_data(channel_id: str, uid: str, want: str) -> Dict[str, Any]:
    """[C72] 응답 data — 보낸 화면이 고른 쪽(모듈 앱 = view_modules, 그 밖 = 편집 화면)."""
    if want == "modules":
        import activity_server
        return activity_server.view_modules(channel_id, uid)
    return build_edit_view(channel_id, uid)


async def _view_data_async(channel_id: str, uid: str, want: str) -> Dict[str, Any]:
    """[C73] 코드 앱 화면(`app:<id>`)은 Lua 를 부르니 비동기 — 그 밖은 _view_data 그대로."""
    if want.startswith("app:"):
        import app_host
        return await app_host.view(channel_id, uid, want[len("app:"):])
    return _view_data(channel_id, uid, want)


async def act(channel_id: str, uid: str, payload: Any) -> Tuple[int, Dict[str, Any]]:
    """(HTTP 상태, 응답 본문). 성공·실패 모두 본문에 새 화면 데이터(data)를 싣는다(낙관 갱신 0).
    [C72] payload.view = "modules" 면 data 가 모듈 앱 쪽이다(모듈 앱이 보낸 쓰기)."""
    channel_id, uid = str(channel_id), str(uid or "")
    if not isinstance(payload, dict):
        return 400, {"error": "bad request"}
    action = str(payload.get("action") or "")
    if action not in ACTIONS:
        return 400, {"error": "unknown action"}
    args = payload.get("args") if isinstance(payload.get("args"), dict) else {}
    want = str(payload.get("view") or "edit")
    if want not in VIEWS and not (want.startswith("app:") and len(want) > 4):
        want = "edit"
    if not _is_participant(channel_id, uid):
        return 403, {"error": "forbidden", "message": MSG_FORBIDDEN}

    import turn_locks
    lock = turn_locks.channel_locks[channel_id]
    if not await _acquire(lock, LOCK_WAIT):
        return 409, {"error": "busy", "message": MSG_BUSY}
    try:
        if not await _drain(channel_id):
            return 409, {"error": "draining", "message": MSG_DRAINING}
        now = _turn(channel_id)
        if _num_or_none(payload.get("rev")) != now:
            return 409, {"error": "stale", "message": MSG_STALE, "data": await _view_data_async(channel_id, uid, want)}
        if action == "undo":
            status, body = _undo(channel_id, uid, args, now)
        elif action == "app.click":
            # [2026-10-23 C73 M7a] 코드 앱 누르기 — on_click(Lua) → 쓰기 커밋. 일지엔 **쓴 값 그대로**(app.kv)를 적는다:
            #   `!다시` 재적용이 Lua 를 다시 돌리지 않고 같은 값을 얹게(굴림이 바뀌면 안 된다). ↶ 없음(kind="app").
            import app_host
            # [2026-10-24 C73 M7b] 그 앱의 신고 정산이 도는 중이면 409 — on_tags 쓰기와 버튼 쓰기가 같은 장부에서 엇갈리지 않게.
            if app_host.settling(channel_id, str(args.get("app") or "")):
                return 409, {"error": "settling", "message": MSG_SETTLING,
                             "data": await _view_data_async(channel_id, uid, want)}
            res = await app_host.click(channel_id, uid, str(args.get("app") or ""), str(args.get("id") or ""),
                                       args.get("data") if isinstance(args.get("data"), dict) else {})
            if res.get("ok") and res.get("journal"):
                import sqlite_store
                sqlite_store.append_activity_edit(channel_id, now, uid, res["journal"], None,
                                                  res.get("receipt") or "", kind="app")
            status, body = 200, {"ok": res["ok"], "line": res["line"], "receipt": res.get("receipt") or "",
                                 "undo_id": None}
        else:
            edit = dict(args, action=action)
            res = execute(channel_id, uid, edit)
            rid = None
            if res["ok"]:
                import sqlite_store
                rid = sqlite_store.append_activity_edit(channel_id, now, uid, edit, res.get("undo"),
                                                        res.get("receipt") or "")
                logger.info("[Activity] edit uid=%s ch=%s %s %s → %s", uid, channel_id, action,
                            {k: v for k, v in args.items()}, res.get("line"))
            status, body = 200, {"ok": res["ok"], "line": res["line"], "receipt": res.get("receipt") or "",
                                 "undo_id": rid if (rid and res.get("undo")) else None}
        body["data"] = await _view_data_async(channel_id, uid, want)
        return status, body
    finally:
        lock.release()
