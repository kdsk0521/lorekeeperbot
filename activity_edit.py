# -*- coding: utf-8 -*-
"""Activity 편집 — [2026-10-23 C60 M2c] 화면에서 값을 고친다.

스펙 파티쳇수정/output_artifact/C60_M2표면등록_지도·스펙_2026-10-23.md §4·§6·§7.

**쓰기는 새 길을 만들지 않는다** — OOC 편집이 지나는 문을 그대로 지난다(한 함수 두 입구, §7 S4).
  선언 값·PC 활력/평형·소지품(E1·E2·옛 E3) = custom_vars.apply_declared_edit
  관계(E4)                                  = domain_manager.apply_ooc_relation_edits(origin="activity")
  시간(E5)                                  = ooc_edits.set_clock
  장소(E5)                                  = ooc_edits.apply_place_edits(op=pc)
  [C116 0-1c c1] 명령 입구(npc.* · pc.info · pc.mask · time.state) = 명령 몸 그대로 — 명령이 쓴 뒤 `record` 로 일지에 적고,
  `!다시`는 화면 편집과 같은 replay_after 로 다시 얹는다(옛 block_redo 자리). 화면 입구(ACTIONS)엔 아직 없다.
  [C116 0-1c c2] 루카 모드 편집(ooc.edits) = command_handler.apply_ooc_edit_list — 편집 콜이 낸 edits 중 되감기에 닿는 칸만
  일지에(정정 · 시트 절은 되감기 밖이라 안 적는다). 다시 얹을 때 편집 콜은 안 돈다.

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
import copy
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
           # [2026-11-05 C116 0-1c c3] 인물 · PC · 진행 중 편집 — 몸은 명령 · 루카 모드와 같은 함수
           "npc.status", "npc.place", "npc.alias", "npc.alias_remove", "npc.form", "npc.fields", "npc.sheet",
           "npc.knows", "pc.sheet", "pc.memo", "pc.state", "thread.edit", "clock.doom",
           "sys.decl",                         # [2026-11-06 C116 판1 1b] 기본 카드 — 활력 · 평형 · 소지품 고칠 칸
           "undo")
# [C72 §5] 이 액션들은 `!다시` 재적용(replay_after)이 건너뛴다 — 넘겨 얹기(domain_manager.redo_carry)가 지금 값을 이미 들고 온다.
#   다시 얹으면 오히려 틀린다(Activity 로 끔 → `!모듈` 로 켬 → `!다시` 면 일지의 옛 "끔"이 덮어쓴다).
CARRIED_ACTIONS = ("mod.toggle", "mod.set")
VIEWS = ("edit", "modules", "medit")   # 응답 data 로 돌려줄 쪽 — 편집 화면(옛) · 모듈 앱(C72) · 진행 중 편집(C116 c3) · [C73] `app:<id>` = 그 코드 앱 화면 · [C116 c3] `pedit:<page_id>` = 인물 편집
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
    npc_items: List[Dict[str, Any]] = []
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
            # [2026-11-07 C120 판2] 명단 안 사람 전부(안 움직인 사람은 시작값 — 고치면 그 사람 값이 선다).
            #   따로 묶음 '인물별 값'으로 — 화면이 무대 위 · 전체 · 묶기 칩 · 찾기로 거른다.
            per_v = cur if isinstance(cur, dict) else {}
            for who in cv.holder_names(channel_id, nm, spec):
                it = _scalar_item(cv, channel_id, uid, nm, spec, per_v.get(who, cv.npc_init(spec, who)), npc=str(who))
                if it:
                    npc_items.append(it)
            continue
        it = _scalar_item(cv, channel_id, uid, nm, spec, cur)
        if it:
            scal.append(it)
    if scal:
        groups.append({"key": "values", "title": "선언 값", "icon": "globe", "items": scal})
    if npc_items:
        try:
            whos = list(dict.fromkeys(it["npc"] for it in npc_items))
            on_set = {str(n) for n in (cv.onstage_names(channel_id) or [])}
            gtab = cv.group_table(channel_id, whos)
            people = {w: {"onstage": w in on_set, "group": gtab.get(w, {})} for w in whos}
            gdefs = cv.group_values(channel_id)
        except Exception as e:
            logger.debug(f"[Activity] edit view npc filter skipped: {e}")
            people, gdefs = {}, []
        groups.append({"key": "values", "title": "인물별 값", "icon": "users", "items": npc_items,
                       "people": people, "group_defs": gdefs})
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
    # --- [C116 c3] 메모 · 타격 · 좋은 상태 · 조각(묶음 안은 부품 — 상태창 편집 모드) ---
    try:
        groups += pcstate_groups(channel_id, uid)
    except Exception as e:
        logger.debug(f"[Activity] edit view pcstate skipped: {e}")
    return out


# =========================================================
# [2026-11-05 C116 0-1c c3] 편집 쪽(부품) — 인물(pedit:<page_id>) · 진행 중(medit) · 상태창 묶음 pcstate
#   스펙 파티쳇수정/output_artifact/C116_판0-1c_c3_Activity편집칸_스펙_2026-11-05.md · 그리는 규칙 conventions/activity_ui.md §5d
#   쓰기는 늘 /api/act(act) — 여기는 화면 데이터만. 값 검사는 쓰기 문(_exec_*)이 한다.
# =========================================================
WAITING = "@waiting"          # 인물 앱 목록의 `등장 전` 줄(위키에 없는 사람)
_AREA_MAX = 8000              # 여러 줄 칸 글자 상한(요청 본문 상한 activity_server.MAX_BODY 안)
_SHOW_MAX = 240               # 줄에 보이는 본문 앞부분


def _jong(w: Any) -> Optional[int]:
    """마지막 한글 글자의 받침 번호(0 = 없음). 한글이 아니면 None."""
    t = str(w or "").strip()
    if not t or not ("가" <= t[-1] <= "힣"):
        return None
    return (ord(t[-1]) - 0xAC00) % 28


def _ro(w: Any) -> str:
    j = _jong(w)
    return "(으)로" if j is None else ("로" if j in (0, 8) else "으로")


def _eul(w: Any) -> str:
    j = _jong(w)
    return "을(를)" if j is None else ("을" if j else "를")


def _short(t: Any, n: int = _SHOW_MAX) -> str:
    s = str(t or "").strip()
    return s if len(s) <= n else s[:n].rstrip() + "…"


def _inp(key: str, kind: str, value: Any = None, **kw: Any) -> Dict[str, Any]:
    d: Dict[str, Any] = {"key": key, "kind": kind}
    if value is not None:
        d["value"] = value
    d.update({k: v for k, v in kw.items() if v is not None})
    return d


def _edit(label: str, text: Any, inputs: Optional[List[Dict[str, Any]]], action: str, args: Dict[str, Any],
          remove: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    p: Dict[str, Any] = {"t": "edit", "label": label, "text": str(text if text not in (None, "") else "—"),
                         "act": {"action": action, "args": args}}
    if inputs:
        p["inputs"] = inputs
    if remove:
        p["remove"] = remove
    return p


def _rm(action: str, args: Dict[str, Any], confirm: str = "") -> Dict[str, Any]:
    r: Dict[str, Any] = {"act": {"action": action, "args": args}}
    if confirm:
        r["confirm"] = confirm
    return r


def _add(inputs: List[Dict[str, Any]], action: str, args: Dict[str, Any]) -> Dict[str, Any]:
    return {"t": "add", "inputs": inputs, "act": {"action": action, "args": args}}


def _card(title: str, icon: str, parts: List[Dict[str, Any]], chip: str = "") -> Dict[str, Any]:
    c: Dict[str, Any] = {"t": "card", "title": title, "icon": icon, "parts": parts}
    if chip:
        c["chip"] = chip
    return c


def _section_list(present: List[str]) -> List[str]:
    std = list((getattr(config, "WIKI_LORE_SECTIONS", {}) or {}).get("character", ()))
    return std + [x for x in present if x not in std]


def _sheet_card(sections: Dict[str, str], action: str, base: Dict[str, Any]) -> Dict[str, Any]:
    parts = [_edit(sec, _short(sections.get(sec)), [_inp("text", "area", sections.get(sec) or "", maxlength=_AREA_MAX)],
                   action, dict(base, section=sec))
             for sec in _section_list(list(sections.keys()))]
    return _card("시트", "scroll", parts)


def _rel_edit(label: str, key: str, e: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    b, t = _num_or_none((e or {}).get("bond")), _num_or_none((e or {}).get("tension"))
    text = f"유대 {b} · 긴장 {t}" if e else "—"
    return _edit(label, text, [_inp("bond", "number", b if b is not None else 0, min=-100, max=100, unit="유대"),
                               _inp("tension", "number", t if t is not None else 0, min=0, max=100, unit="긴장")],
                 "rel.set", {"npc": key})


def waiting_people(channel_id: str) -> List[str]:
    """모습 표에 등장 규칙이 있고 아직 안 선 사람(위키 · 명부에 없다)."""
    try:
        import npc_forms
        t = npc_forms._load(channel_id)
    except Exception:
        return []
    out = []
    for pk, ent in (t or {}).items():
        if not isinstance(ent, dict) or ent.get("active"):
            continue
        if any(isinstance(r, dict) and r.get("effect") == "appear" for r in (ent.get("rules") or [])):
            out.append(str(pk))
    return out


def _npc_parts(channel_id: str, uid: str, key: str) -> List[Dict[str, Any]]:
    import domain_manager as dm
    import wiki_store
    import world_tree
    data = dm.get_npc(channel_id, key) or {}
    parts: List[Dict[str, Any]] = []
    st = _npc_status_now(channel_id, key)
    parts.append(_card("상태", "activity", [{"t": "select", "label": "상태", "value": st,
                                             "options": [[k, v] for k, v in _STATUS_KO.items()],
                                             "act": {"action": "npc.status", "args": {"npc": key}}}]))
    loc = str(world_tree.get_npc_location(channel_id, key) or "")
    pl = [_edit("지금 자리", loc or "장면 밖", [_inp("place", "text", loc, maxlength=60)], "npc.place", {"npc": key})]
    mask = (dm.get_participant_data(channel_id, uid) or {}).get("mask") or ""
    if mask:
        pl.append(_rel_edit(f"{mask} 쪽", key, _edge(channel_id, _rel_key(channel_id, key), mask)))
    parts.append(_card("자리 · 관계", "map", pl))
    try:
        import npc_forms
        t = npc_forms._load(channel_id)
        pk = npc_forms._person_of(t, key) if t else None
        forms = list(((t or {}).get(pk) or {}).get("forms") or {}) if pk else []
        if pk and len(forms) >= 2:
            parts.append(_card("모습", "layers", [{"t": "select", "label": "지금 모습",
                                                   "value": str((t[pk] or {}).get("active") or key),
                                                   "options": [[f, f] for f in forms],
                                                   "act": {"action": "npc.form", "args": {"npc": pk}}}]))
    except Exception as e:
        logger.debug(f"[Activity] forms skipped: {e}")
    tone = data.get("tone")
    sched = data.get("schedule") if isinstance(data.get("schedule"), dict) else {}
    vl = [_edit("말투", _short(tone), [_inp("tone", "area", str(tone or ""), maxlength=2000)], "npc.fields", {"npc": key})]
    for slot in (getattr(config, "DEFAULT_TIME_SLOTS", []) or []):
        cur = sched.get(slot) if isinstance(sched.get(slot), dict) else {}
        act_, place_ = str(cur.get("activity") or ""), str(cur.get("location") or "")
        shown = " · ".join(x for x in (act_, place_) if x)
        vl.append(_edit(slot, shown, [_inp("activity", "text", act_, maxlength=60, unit="활동"),
                                      _inp("location", "text", place_, maxlength=60, unit="장소")],
                        "npc.fields", {"npc": key, "slot": slot}))
    parts.append(_card("말투 · 일정", "clock", vl))
    kn: List[str] = []
    try:
        import ooc_edits
        _k = (dm.get_domain(channel_id).get("npc_knowledge") or {}).get(dm._mind(channel_id, key)) or {}
        kn = [str(x) for x in (_k.get("knows") or []) if str(x).strip()]
        if getattr(config, "MEMORY_CURRENT", False):
            kn += [x for x in ooc_edits._page_knows(channel_id, key) if x not in kn]
    except Exception as e:
        logger.debug(f"[Activity] knows skipped: {e}")
    kl = [_edit("", line, None, "npc.knows", {"npc": key},
                remove=_rm("npc.knows", {"npc": key, "op": "remove", "text": line})) for line in kn[-30:]]
    if not kl:
        kl.append({"t": "empty", "text": "아는 게 아직 없어요."})
    kl.append(_add([_inp("text", "text", maxlength=300, placeholder="새로 아는 것")], "npc.knows", {"npc": key, "op": "add"}))
    parts.append(_card("아는 것", "brain", kl))
    al = [_edit("", x, None, "npc.alias", {"npc": key}, remove=_rm("npc.alias_remove", {"npc": key, "alias": x}))
          for x in (data.get("aliases") or []) if isinstance(x, str) and x.strip()]
    al.append(_add([_inp("alias", "text", maxlength=40, placeholder="새 별칭")], "npc.alias", {"npc": key}))
    parts.append(_card("별칭", "users", al))
    pid = wiki_store.page_id_for("character", key)
    parts.append(_sheet_card(wiki_store.get_lore_sections(channel_id, pid) or {}, "npc.sheet", {"npc": key}))
    return parts


def _pc_parts(channel_id: str, uid: str, page_id: str) -> List[Dict[str, Any]]:
    import domain_manager as dm
    import sqlite_store
    import wiki_store
    parts = [_sheet_card(wiki_store.get_lore_sections(channel_id, page_id) or {}, "pc.sheet", {})]
    mask = (dm.get_participant_data(channel_id, uid) or {}).get("mask") or ""
    rl: List[Dict[str, Any]] = []
    if mask:
        try:
            for e in (sqlite_store.get_edges(channel_id, target=mask) or []):
                rl.append(_rel_edit(str(e["source"]), str(e["source"]), e))
        except Exception as ex:
            logger.debug(f"[Activity] pc relations skipped: {ex}")
    if not rl:
        rl.append({"t": "empty", "text": "아직 관계가 없어요."})
    parts.append(_card("관계", "handshake", rl))
    return parts


def build_person_edit(channel_id: str, uid: str, page_id: str) -> Dict[str, Any]:
    """인물 앱 편집 쪽 — NPC 페이지 · 내 PC 페이지 · `등장 전`(@waiting). 남의 PC · 장소 · 세력은 고칠 칸 없음."""
    import domain_manager as dm
    import wiki_store
    page_id = str(page_id or "")[:128]
    out: Dict[str, Any] = {"rev": _turn(channel_id), "view": f"pedit:{page_id}", "parts": []}
    parts: List[Dict[str, Any]] = out["parts"]
    try:
        if page_id == WAITING:
            names = waiting_people(channel_id)
            for nm in names:
                parts.append(_card(nm, "clock", [{"t": "button", "label": "등장", "style": "primary",
                                                  "confirm": f"{nm}{_eul(nm)} 지금 세울까요?",
                                                  "act": {"action": "npc.form", "args": {"npc": nm, "form": "등장"}}}]))
            if not names:
                parts.append({"t": "empty", "text": "등장을 기다리는 사람이 없어요."})
            return out
        page = wiki_store.get_page(channel_id, page_id)
        if not page or str(page.get("status") or "") == "deleted":
            parts.append({"t": "empty", "text": "페이지가 없어요."})
            return out
        mine = (wiki_store.pc_page_id(channel_id, uid) or "") == page_id
        if mine:
            parts += _pc_parts(channel_id, uid, page_id)
            return out
        key = _npc_key(channel_id, page.get("name")) if page.get("kind") == "character" else ""
        if key and not dm.is_pc_name(key, dm.pc_registry(channel_id)):
            parts += _npc_parts(channel_id, uid, key)
            return out
    except Exception as e:
        logger.warning(f"[Activity] person edit {page_id} 실패: {e}")
    if not parts:
        parts.append({"t": "empty", "text": "고칠 칸이 없어요."})
    return out


def _bar(n: int, m: int) -> str:
    try:
        import in_motion
        return in_motion._bar(n, m)
    except Exception:
        return f"{n}/{m}"


_THREAD_STATUS_KO = {"paused": "멈춤", "asleep": "기다림"}


def build_motion_edit(channel_id: str, uid: str) -> Dict[str, Any]:
    """진행 중 앱 편집 쪽 — 목표 · 약속 · 사안 · 시계, 카드 하나 = 줄 하나. 아크(우물)는 고칠 칸이 없어 안 싣는다."""
    out: Dict[str, Any] = {"rev": _turn(channel_id), "view": "medit", "parts": []}
    parts: List[Dict[str, Any]] = out["parts"]
    try:
        import thread_ledger
        states = thread_ledger.load_states(channel_id) or {}
        live_set = tuple(getattr(thread_ledger, "LIVE", ("open", "paused", "asleep")))
        live = sorted((s for s in states.values() if s.get("status") in live_set),
                      key=lambda s: (0 if s.get("kind") == "goal" else 1, -(s.get("last_turn") or 0)))
        for st in live:
            tid, title = str(st.get("id")), str(st.get("title") or st.get("id"))
            chip = _THREAD_STATUS_KO.get(str(st.get("status")), "")
            ends: List[Dict[str, Any]] = []
            if st.get("kind") == "goal":
                p, m = int(st.get("progress") or 0), int(st.get("max") or 0)
                inner: List[Dict[str, Any]] = [
                    _edit("진행", f"{_bar(p, m)} {p}/{m}", [_inp("target", "number", p, min=0, max=m)],
                          "thread.edit", {"op": "progress", "id": tid})]
                _rk = getattr(config, "QUEST_RANK_SETTINGS", {}) or {}
                if _rk:   # [2026-11-06 C116] 등급 고치기 — 진행도는 그대로(새 끝에 닿으면 한 칸 앞, 끝내기는 따로)
                    inner.append({"t": "select", "label": "등급", "value": str(st.get("rank") or ""),
                                  "options": [[k, str((v or {}).get("display") or k)] for k, v in _rk.items()],
                                  "act": {"action": "thread.edit", "args": {"op": "rank", "id": tid}}})
                if st.get("status") != "asleep":
                    inner.append({"t": "switch", "label": "멈춤", "on": st.get("status") == "paused",
                                  "act": {"action": "thread.edit", "args": {"op": "pause", "id": tid}}})
                others = [[str(o.get("id")), f"{o.get('id')} {o.get('title') or ''}".strip()] for o in live
                          if o.get("id") != tid]
                if others or st.get("status") == "asleep":
                    inner.append({"t": "select", "label": "기다림", "value": str(st.get("after") or ""),
                                  "options": [["", "없음"]] + others,
                                  "act": {"action": "thread.edit", "args": {"op": "after", "id": tid}}})
                ends = [("이룸", "close", "done", ""), ("그만둠", "close", "dropped", ""), ("없던 일", "void", "", "danger")]
                icon = "target"
            else:
                inner = []
                tail = " · ".join(x for x in (thread_ledger.remaining_phrase(st, thread_ledger.now_abs(
                    thread_ledger._world_of(channel_id, None))) if st.get("due_start") is not None else "",
                    str(st.get("remaining") or "")) if x)
                who = " ↔ ".join((st.get("parties") or [])[:3])
                if who or tail:
                    inner.append({"t": "text", "text": " · ".join(x for x in (who, tail) if x), "muted": True})
                if st.get("kind") == "promise":
                    ends = [("지킴", "close", "done", ""), ("그만둠", "close", "dropped", ""), ("깨짐", "close", "broken", ""),
                            ("없던 일", "void", "", "danger")]
                else:
                    ends = [("풀림", "close", "done", ""), ("그만둠", "close", "dropped", ""), ("없던 일", "void", "", "danger")]
                icon = "handshake"
            btns = []
            for lab, op, oc, sty in ends:
                args = {"op": op, "id": tid}
                if oc:
                    args["outcome"] = oc
                b = {"t": "button", "label": lab, "confirm": (f"{lab}{_ro(lab)} 끝낼까요?" if op == "close" else "없던 일로 할까요?"),
                     "act": {"action": "thread.edit", "args": args}}
                if sty:
                    b["style"] = sty
                btns.append(b)
            inner.append({"t": "fold", "title": "끝내기", "parts": btns})
            parts.append(_card(f"{tid} {title}", icon, inner, chip))
    except Exception as e:
        logger.warning(f"[Activity] motion edit threads 실패: {e}")
    try:
        import ooc_edits
        for c in ooc_edits.doom_clock_state(channel_id, cap=20):
            nm, f, n = c["name"], int(c["filled"]), int(c["segments"])
            inner = [_edit("칸", f"{_bar(f, n)} {f}/{n}", [_inp("filled", "number", f, min=0, max=n)],
                           "clock.doom", {"op": "set", "name": nm})]
            if c.get("goal"):
                inner.append({"t": "text", "text": f"걸린 목표 · {c['goal']}", "muted": True})
            inner.append({"t": "button", "label": "막아냄", "confirm": "막아낸 걸로 할까요?",
                          "act": {"action": "clock.doom", "args": {"op": "end", "name": nm}}})
            inner.append({"t": "button", "label": "없던 일", "style": "danger", "confirm": "없던 일로 할까요?",
                          "act": {"action": "clock.doom", "args": {"op": "void", "name": nm}}})
            parts.append(_card(nm, "clock", inner, "위협" if c.get("kind") == "threat" else "타이머"))
    except Exception as e:
        logger.warning(f"[Activity] motion edit clocks 실패: {e}")
    if not parts:
        parts.append({"t": "empty", "text": "고칠 게 아직 없어요."})
    return out


def _edge_word(v: Any) -> str:
    """조각 값 → 강점 · 약점(C42 — edge 부호, 옛 roll_* 합 부호)."""
    if not isinstance(v, dict):
        return ""
    e = v.get("edge")
    if e is None:
        try:
            e = sum(int(x) for k, x in v.items() if str(k).startswith("roll_"))
        except (TypeError, ValueError):
            e = 0
    try:
        e = int(e)
    except (TypeError, ValueError):
        e = 0
    return "강점" if e > 0 else "약점" if e < 0 else ""


def pcstate_groups(channel_id: str, uid: str) -> List[Dict[str, Any]]:
    """상태창 편집 묶음 `pcstate` — 메모 · 타격 · 좋은 상태 · 조각(묶음 안은 부품). 참가자가 아니면 []."""
    import domain_manager as dm
    import game_character as gc
    p = dm.get_participant_data(channel_id, uid) if uid else None
    if not p:
        return []
    groups: List[Dict[str, Any]] = []
    try:
        nb = dm.get_notebook_data(channel_id, uid) or {}
        memo = ((nb.get("sections") or {}).get("메모") or {})
        lines = [str(x) for x in (memo.get("user") or []) + (memo.get("llm") or []) if str(x).strip()]
        ml = [_edit("", ln, [_inp("new", "text", ln, maxlength=200)], "pc.memo", {"op": "edit", "old": ln},
                    remove=_rm("pc.memo", {"op": "remove", "text": ln})) for ln in lines[-40:]]
        if not ml:
            ml.append({"t": "empty", "text": "메모가 없어요."})
        ml.append(_add([_inp("text", "text", maxlength=200, placeholder="새 메모")], "pc.memo", {"op": "add"}))
        groups.append({"key": "pcstate", "title": "메모", "icon": "notebook", "parts": ml})
    except Exception as e:
        logger.debug(f"[Activity] pcstate memo skipped: {e}")
    try:
        sk = [str(x) for x in (gc.strike_names(p) or []) if str(x).strip()]
        sl = [_edit("", x, None, "pc.state", {"field": "strikes"},
                    remove=_rm("pc.state", {"field": "strikes", "op": "remove", "value": x})) for x in sk]
        sl.insert(0, {"t": "text", "text": f"타격 {len(sk)}/{gc.STRIKE_SLOTS}", "muted": True})
        sl.append(_add([_inp("value", "text", maxlength=30, placeholder="새 타격")], "pc.state",
                       {"field": "strikes", "op": "add"}))
        groups.append({"key": "pcstate", "title": "타격", "icon": "alert", "parts": sl})
        bf = [str((x or {}).get("name") or "") for x in (gc.buff_effects(p) or []) if isinstance(x, dict)]
        bl = [_edit("", x, None, "pc.state", {"field": "status_effects"},
                    remove=_rm("pc.state", {"field": "status_effects", "op": "remove", "value": x})) for x in bf if x]
        if not bl:
            bl.append({"t": "empty", "text": "좋은 상태가 없어요."})
        bl.append(_add([_inp("value", "text", maxlength=30, placeholder="새 좋은 상태")], "pc.state",
                       {"field": "status_effects", "op": "add"}))
        groups.append({"key": "pcstate", "title": "좋은 상태", "icon": "star", "parts": bl})
    except Exception as e:
        logger.debug(f"[Activity] pcstate strikes skipped: {e}")
    try:
        frs = [x for x in ((dm.get_ai_memory(channel_id, uid) or {}).get("passives") or []) if isinstance(x, dict)]
        fl = []
        for f in frs:
            nm = str(f.get("name") or "").strip()
            if not nm:
                continue
            txt = " · ".join(x for x in (nm, _edge_word(f.get("value")), _short(f.get("desc"), 80)) if x)
            fl.append(_edit("", txt, None, "pc.state", {"field": "passives"},
                            remove=_rm("pc.state", {"field": "passives", "op": "remove", "value": nm},
                                       confirm=f"조각 '{nm}'{_eul(nm)} 뺄까요? 되돌리기는 없어요.")))
        if not fl:
            fl.append({"t": "empty", "text": "조각이 없어요."})
        fl.append(_add([_inp("name", "text", maxlength=30, placeholder="이름"),
                        _inp("edge", "select", "1", options=[["1", "강점"], ["-1", "약점"]]),
                        _inp("desc", "text", maxlength=200, placeholder="설명")], "pc.state",
                       {"field": "passives", "op": "add"}))
        groups.append({"key": "pcstate", "title": "조각", "icon": "layers", "parts": fl})
    except Exception as e:
        logger.debug(f"[Activity] pcstate passives skipped: {e}")
    return groups


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
        return per.get(who, cv.npc_init(spec, who))          # [C120] 인물별 시작값
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
                                reports=a.get("reports") or [], token=str(a.get("token") or ""),
                                values=a.get("values") or [],   # [C116 판2] 선언 값 — 쓴 뒤 값 그대로
                                clock=a.get("clock"),           # [C116 판5] 누르기 시간 — 쓴 뒤 시계 그대로
                                core=a.get("core"),             # [C116 판6] 관계 · 목표
                                handouts=a.get("handouts"),     # [C116 판8] 누르기 도착물 — `_handouts@<표>`
                                adds=a.get("adds"))             # [C117 A3] lk.add 키 — 더한 양
    return _res(ok, "✓ 앱 장부 다시 얹음" if ok else "⚠ 앱 장부: 다시 얹을 게 없어")


# =========================================================
# [2026-11-05 C116 0-1c c1] 명령 입구 액션 — 명령이 부르는 몸을 그대로 부른다(한 함수 두 입구).
#   명령은 쓴 뒤 `record` 로 일지에 한 줄 → `!다시`가 되감은 뒤 replay_after 가 여기로 다시 얹는다.
#   화면 입구(ACTIONS)엔 아직 없다(c3 에서 연다). 스펙 C116_판0-1c_편집일지하나_지도·스펙_2026-11-05.md
# =========================================================

def _npc_key(channel_id: str, name: Any) -> str:
    import domain_manager as dm
    nm = str(name or "").strip()
    if not nm:
        return ""
    return str(dm._find_npc_key(dm.get_npcs(channel_id) or {}, nm) or "")


def _npc_status_now(channel_id: str, key: str) -> str:
    import domain_manager as dm
    import npc_manager
    try:
        return str(npc_manager.get_npc_status(dm.get_npc(channel_id, key) or {}) or "active")
    except Exception:
        return "active"


def _lore_body(channel_id: str, page_id: str, section: str) -> Optional[str]:
    import wiki_store
    try:
        b = (wiki_store.get_lore_sections(channel_id, page_id) or {}).get(section)
    except Exception:
        return None
    return b if isinstance(b, str) and b.strip() else None


def _exec_npc_register(channel_id: str, uid: str, a: Dict[str, Any]) -> Dict[str, Any]:
    """`!npc추가` 글 통째 — command_handler.register_npcs_from_lines(명령 몸 그대로)."""
    import command_handler as chd
    lines = [str(x) for x in (a.get("lines") or []) if isinstance(x, str)]
    if not lines:
        return _res(False, "⚠ 인물 등록: 글이 없어")
    r = chd.register_npcs_from_lines(channel_id, lines)
    names = [str(n) for n in (r.get("names") or []) if n]
    return _res(bool(names), ("✓ 인물 등록 " + ", ".join(names[:5])) if names else "⚠ 인물 등록: 다시 선 사람이 없어")


def _exec_npc_delete(channel_id: str, uid: str, a: Dict[str, Any]) -> Dict[str, Any]:
    import command_handler as chd
    nm = str(a.get("name") or "").strip()
    r = chd.npc_delete_flow(channel_id, nm) if nm else {"kind": "none"}
    if r.get("kind") == "none":
        return _res(False, f"⚠ 인물 삭제: {nm or '(이름 없음)'} 없음")
    return _res(True, f"✓ 인물 삭제 {r.get('display') or nm}")


def _exec_npc_clear_session(channel_id: str, uid: str, a: Dict[str, Any]) -> Dict[str, Any]:
    import npc_manager
    n = npc_manager.clear_session_npcs(channel_id)
    return _res(True, f"✓ 세션 인물 {n}명 지움")


def _exec_npc_alias(channel_id: str, uid: str, a: Dict[str, Any]) -> Dict[str, Any]:
    import domain_manager as dm
    key, alias = _npc_key(channel_id, a.get("npc")), str(a.get("alias") or a.get("value") or "").strip()[:40]
    if not key or not alias:
        return _res(False, f"⚠ 별칭: {a.get('npc') or '(이름 없음)'} 없음" if not key else "⚠ 별칭: 넣을 이름이 없어")
    had = alias in ((dm.get_npc(channel_id, key) or {}).get("aliases") or [])
    ok, k2 = dm.add_npc_alias(channel_id, key, alias)
    if not ok and k2 == key:
        return _res(True, f"✓ {key} 별칭 {alias}(이미 있음)")
    if not ok:
        return _res(False, f"⚠ 별칭 {alias}: 다른 인물 {k2 or '?'}로 풀린다" if k2 else f"⚠ 별칭 {alias}: 못 붙여")
    return _res(True, f"✓ {key} 별칭 {alias}", f"{key} 별칭 + {alias}",
                None if had else {"action": "npc.alias_remove", "npc": key, "alias": alias})


def _exec_npc_alias_remove(channel_id: str, uid: str, a: Dict[str, Any]) -> Dict[str, Any]:
    """[C116 c3] 별칭 빼기 — domain_manager.remove_npc_alias(dict · 플레이 표시 · 페이지 별칭)."""
    import domain_manager as dm
    key, alias = _npc_key(channel_id, a.get("npc")), str(a.get("alias") or a.get("value") or "").strip()
    if not key or not alias:
        return _res(False, f"⚠ 별칭: {a.get('npc') or '(이름 없음)'} 없음")
    ok, _k = dm.remove_npc_alias(channel_id, key, alias)
    if not ok:
        return _res(False, f"⚠ {key} 별칭 {alias}: 없음")
    return _res(True, f"✓ {key} 별칭 {alias} 뺌", f"{key} 별칭 − {alias}",
                {"action": "npc.alias", "npc": key, "alias": alias})


def _exec_npc_merge(channel_id: str, uid: str, a: Dict[str, Any]) -> Dict[str, Any]:
    import domain_manager as dm
    ok, msg = dm.merge_npc(channel_id, str(a.get("dup") or ""), str(a.get("canon") or ""))
    return _res(bool(ok), ("✓ " if ok else "⚠ ") + str(msg or "병합"))


_STATUS_KO = {"active": "살아 있음", "down": "쓰러짐", "dead": "죽음"}


def _exec_npc_status(channel_id: str, uid: str, a: Dict[str, Any]) -> Dict[str, Any]:
    import npc_manager
    key = _npc_key(channel_id, a.get("npc"))
    st = str(a.get("status") or a.get("value") or "").strip().lower()
    if not key or not st:
        return _res(False, f"⚠ 상태: {a.get('npc') or '(이름 없음)'} 없음")
    old = _npc_status_now(channel_id, key)
    r = npc_manager.set_npc_status_gated(channel_id, key, st, source="manual", evidence="manual command")
    if r not in ("accepted", "unchanged"):
        return _res(False, f"⚠ {key} 상태: {r}")
    if r == "unchanged":
        return _res(True, f"✓ {key} 상태 {st}")
    return _res(True, f"✓ {key} 상태 {st}",
                f"{key} 상태 {_STATUS_KO.get(old, old)} → {_STATUS_KO.get(st, st)}",
                {"action": "npc.status", "npc": key, "status": old} if old != st else None)


_NPC_FIELD_KEYS = ("tone",)          # 화면이 바로 고치는 칸(보이스카드 글). 일정은 slot 모드로
_SCHED_KEYS = ("activity", "location")


def _exec_npc_fields(channel_id: str, uid: str, a: Dict[str, Any]) -> Dict[str, Any]:
    """`!npc 보이스카드` · `!npc 일정`의 **콜 결과** — 명령과 같은 쓰기(그 사람 dict 에 칸을 얹어 update_npc). 다시 콜 0.
    [C116 c3] 화면 입구 둘 더: 칸을 바로(`tone`) · 일정 한 시간대(`slot` + activity · location — 둘 다 비면 그 시간대를 뺀다).
    손으로 쓴 일정은 LLM 길(extract_schedule)의 '시트에 없는 장소' 관문 없이 받는다(쓰는 사람이 저자)."""
    import domain_manager as dm
    key = _npc_key(channel_id, a.get("npc"))
    if not key:
        return _res(False, f"⚠ 인물 칸: {a.get('npc') or '(이름 없음)'} 없음")
    data = dict(dm.get_npc(channel_id, key) or {})
    fields = a.get("fields") if isinstance(a.get("fields"), dict) else None
    slot = str(a.get("slot") or "").strip()
    if fields is None and slot:
        if slot not in (getattr(config, "DEFAULT_TIME_SLOTS", []) or []):
            return _res(False, f"⚠ 일정: '{slot}'은 없는 시간대")
        sched = dict(data.get("schedule") or {}) if isinstance(data.get("schedule"), dict) else {}
        cur = dict(sched.get(slot) or {}) if isinstance(sched.get(slot), dict) else {}
        for k in _SCHED_KEYS:
            if k in a and a[k] is not None:
                cur[k] = str(a[k]).strip()[:60]
        if not any(str(cur.get(k) or "").strip() for k in _SCHED_KEYS):
            sched.pop(slot, None)
        else:
            sched[slot] = {k: str(cur.get(k) or "") for k in _SCHED_KEYS}
        fields = {"schedule": sched}
    elif fields is None:
        fields = {k: (str(a[k])[:2000] if a[k] is not None else None) for k in _NPC_FIELD_KEYS if k in a}
    if not fields:
        return _res(False, f"⚠ 인물 칸: {key} — 바꿀 칸이 없어")
    prev = {k: data.get(k) for k in fields}
    data.update(fields)
    dm.update_npc(channel_id, key, data)
    label = {"tone": "말투", "schedule": "일정"}
    rc = f"{key} " + " · ".join(label.get(k, k) for k in fields) + (f" {slot}" if slot else "")
    return _res(True, f"✓ {key} " + " · ".join(fields.keys()), rc,
                {"action": "npc.fields", "npc": key, "fields": copy.deepcopy(prev)})


def _exec_npc_sheet(channel_id: str, uid: str, a: Dict[str, Any]) -> Dict[str, Any]:
    """`!npc 시트` — **쓴 뒤 본문**을 그대로(lore 절은 `!다시`가 안 되감으니 덧붙이기를 다시 하면 겹친다). text None = 절 지움.
    [C116 c3] 화면 입구 — 빈 글 = 절 지움 · ↶ = 옛 본문."""
    import domain_manager as dm
    import wiki_store
    key, section = _npc_key(channel_id, a.get("npc")), str(a.get("section") or "").strip()
    if not key or not section:
        return _res(False, f"⚠ 시트: {a.get('npc') or '(이름 없음)'} 없음")
    pid = wiki_store.page_id_for("character", key)
    turn = int((dm.get_world_state(channel_id) or {}).get("turn_index", 0) or 0)
    if not wiki_store.get_page(channel_id, pid):
        wiki_store.ensure_page(channel_id, "character", key, source="manual", turn=turn)
    prev = _lore_body(channel_id, pid, section)
    text = a.get("text")
    if text is None or not str(text).strip():
        if prev is None:
            return _res(True, f"✓ {key} 시트 {section}(이미 비어 있음)")   # 다시 얹기에도 탈 없게(c1 명령 줄)
        ok = wiki_store.edit_lore_section(channel_id, pid, section, "", action="remove", turn=turn)
    elif prev is not None and prev.strip() == str(text).strip():
        return _res(True, f"✓ {key} 시트 {section}(그대로)")   # 같은 본문 — `!다시` 다시 얹기(lore 절은 안 되감김)에 탈 없게
    else:
        ok = wiki_store.edit_lore_section(channel_id, pid, section, str(text), action="set", turn=turn)
    if ok:
        wiki_store.ensure_page(channel_id, "character", key, source="manual", turn=turn)
    if not ok:
        return _res(False, f"⚠ {key} 시트 {section}: 못 씀")
    return _res(True, f"✓ {key} 시트 {section}", f"{key} 시트 {section}" + (" 지움" if not str(text or "").strip() else ""),
                {"action": "npc.sheet", "npc": key, "section": section, "text": prev})


def _exec_npc_form(channel_id: str, uid: str, a: Dict[str, Any]) -> Dict[str, Any]:
    """`!npc 모습 {이름} {모습|등장}` — 명령은 rest 글, 화면은 npc · form(고르기면 value). ↶ 없음(이름이 움직인다)."""
    import npc_forms
    rest = str(a.get("rest") or "").strip()
    if not rest:
        nm, fm = str(a.get("npc") or "").strip(), str(a.get("form") or a.get("value") or "").strip()
        rest = f"{nm} {fm}".strip() if nm and fm else ""
    m = npc_forms.manual(channel_id, rest) if len(rest.split()) >= 2 else None
    ok = m is not None and not str(m).startswith("⚠")
    msg = str(m or rest).replace("**", "")
    return _res(ok, ("✓ " + msg) if ok else ("⚠ 모습: " + msg.lstrip("⚠️ ")), msg.lstrip("🎭 ") if ok else "")


def _exec_npc_place(channel_id: str, uid: str, a: Dict[str, Any]) -> Dict[str, Any]:
    """[C116 c3] 인물 자리 — ooc_edits.apply_place_edits(npc_in · 빈칸이면 npc_out). ↶ = 옛 자리(없었으면 뺌)."""
    import ooc_edits
    import world_tree
    key = _npc_key(channel_id, a.get("npc"))
    if not key:
        return _res(False, f"⚠ 자리: {a.get('npc') or '(이름 없음)'} 없음")
    place = str(a.get("place") if a.get("place") is not None else a.get("value") or "").strip()[:60]
    old = str(world_tree.get_npc_location(channel_id, key) or "")
    if place == old:
        return _res(False, f"⚠ 자리: {key}는 이미 {old or '장면 밖'}")
    e = {"op": "npc_in", "key": key, "value": place} if place else {"op": "npc_out", "key": key}
    lines = ooc_edits.apply_place_edits(channel_id, [e])
    line = lines[0] if lines else "⚠ 자리: 변화 없음"
    if not _ok_line(line):
        return _res(False, line)
    new = str(world_tree.get_npc_location(channel_id, key) or "")
    return _res(True, line, f"{key} 자리 {old or '—'} → {new or '장면 밖'}",
                {"action": "npc.place", "npc": key, "place": old})


def _exec_npc_knows(channel_id: str, uid: str, a: Dict[str, Any]) -> Dict[str, Any]:
    """[C116 c3] 아는 것 — ooc_edits._knows_edit(도메인 + 페이지 Knowledge 둘 다). ↶ = 반대 일."""
    import ooc_edits
    key = _npc_key(channel_id, a.get("npc"))
    op = "remove" if str(a.get("op") or "").strip().lower() == "remove" else "add"
    text = str(a.get("text") or a.get("value") or "").strip()[:300]
    if not key or not text:
        return _res(False, f"⚠ 지식: {a.get('npc') or '(이름 없음)'} 없음" if not key else "⚠ 지식: 내용이 없어")
    line = ooc_edits._knows_edit(channel_id, key, op, text)
    if not _ok_line(line):
        return _res(False, line)
    return _res(True, line, f"{key} 앎 {'+' if op == 'add' else '−'} {text[:40]}",
                {"action": "npc.knows", "npc": key, "op": "remove" if op == "add" else "add", "text": text})


def _exec_pc_sheet(channel_id: str, uid: str, a: Dict[str, Any]) -> Dict[str, Any]:
    """[C116 c3] PC 시트 절 — PC 페이지 lore 절에 **쓴 뒤 본문 통째**(되감기 밖이라 다시 얹어도 같다). 빈 글 = 절 지움."""
    import domain_manager as dm
    import wiki_store
    section = str(a.get("section") or "").strip()
    pid = dm.get_pc_page_id(channel_id, uid) if uid else None
    if not pid:
        return _res(False, "⚠ PC 시트: `!가면`이 없어")
    if not section:
        return _res(False, "⚠ PC 시트: 절 이름이 없어")
    turn = _turn(channel_id)
    prev = _lore_body(channel_id, pid, section)
    text = a.get("text")
    if text is None or not str(text).strip():
        if prev is None:
            return _res(True, f"✓ PC 시트 {section}(이미 비어 있음)")
        ok = wiki_store.edit_lore_section(channel_id, pid, section, "", action="remove", turn=turn)
    elif prev is not None and prev.strip() == str(text).strip():
        return _res(True, f"✓ PC 시트 {section}(그대로)")
    else:
        ok = wiki_store.edit_lore_section(channel_id, pid, section, str(text), action="set", turn=turn)
    if not ok:
        return _res(False, f"⚠ PC 시트 {section}: 못 씀")
    return _res(True, f"✓ PC 시트 {section}", f"PC 시트 {section}" + (" 지움" if not str(text or "").strip() else ""),
                {"action": "pc.sheet", "section": section, "text": prev})


def _exec_pc_memo(channel_id: str, uid: str, a: Dict[str, Any]) -> Dict[str, Any]:
    """[C116 c3] 메모 줄 — command_handler._ooc_memo_edit(루카 모드와 같은 몸). ↶ = 반대 일."""
    import command_handler as chd
    op = str(a.get("op") or "").strip().lower()
    if op == "add":
        text = str(a.get("text") or a.get("value") or "").strip()[:200]
        e, undo = {"action": "append", "value": text}, {"action": "pc.memo", "op": "remove", "text": text}
        rc = f"메모 + {text[:40]}"
    elif op == "remove":
        text = str(a.get("text") or a.get("value") or "").strip()
        e, undo = {"action": "remove", "value": text}, {"action": "pc.memo", "op": "add", "text": text}
        rc = f"메모 − {text[:40]}"
    elif op == "edit":
        old, new = str(a.get("old") or "").strip(), str(a.get("new") or "").strip()[:200]
        if not new:
            return _res(False, "⚠ 메모: 비우려면 ✕로 빼")
        e, undo = {"action": "edit", "old": old, "new": new}, {"action": "pc.memo", "op": "edit", "old": new, "new": old}
        rc = f"메모 {old[:20]} → {new[:20]}"
    else:
        return _res(False, f"⚠ 메모: '{op or '?'}'은 못 해")
    line = chd._ooc_memo_edit(channel_id, uid, e)
    return _res(_ok_line(line), line, rc, undo)


_PC_STATE_FIELDS = ("strikes", "status_effects", "passives")


def _exec_pc_state(channel_id: str, uid: str, a: Dict[str, Any]) -> Dict[str, Any]:
    """[C116 c3] 타격 · 좋은 상태 · 조각 — 루카 모드 적용 함수(apply_ooc_edit_list) 한 건. ↶ = 타격 · 좋은 상태만(반대 일).
    조각을 빼면 play 조각 설명이 Observed 로 돌아가 되돌리기가 깔끔하지 않아 ↶ 없음."""
    import command_handler as chd
    field = str(a.get("field") or "").strip()
    op = "remove" if str(a.get("op") or "").strip().lower() == "remove" else "add"
    if field not in _PC_STATE_FIELDS:
        return _res(False, f"⚠ 상태: '{field or '?'}' 칸은 없어")
    if field == "passives" and op == "add":
        name = str(a.get("name") or "").strip()[:30]
        try:
            edge = 1 if int(float(a.get("edge") or 1)) >= 0 else -1
        except (TypeError, ValueError):
            edge = 1
        value: Any = {"name": name, "desc": str(a.get("desc") or "").strip()[:200], "value": {"edge": edge}}
        shown = name
    else:
        value = str(a.get("value") or a.get("name") or "").strip()[:60]
        shown = value
    if not shown:
        return _res(False, "⚠ 상태: 이름이 없어")
    r = chd.apply_ooc_edit_list(channel_id, uid, [{"field": field, "action": op, "value": value}])
    lines = [str(x) for x in (r.get("journal_lines") or [])]
    good = [x for x in lines if _ok_line(x)]
    if not good:
        return _res(False, lines[0] if lines else "⚠ 상태: 변화 없음")
    label = {"strikes": "타격", "status_effects": "좋은 상태", "passives": "조각"}[field]
    undo = (None if field == "passives" else
            {"action": "pc.state", "field": field, "op": "remove" if op == "add" else "add", "value": shown})
    return _res(True, good[0], f"{label} {'+' if op == 'add' else '−'} {shown}", undo)


_THREAD_DONE_OPS = ("close", "void")


def _exec_thread_edit(channel_id: str, uid: str, a: Dict[str, Any]) -> Dict[str, Any]:
    """[C116 c3] 목표 · 약속 · 사안 — ooc_edits.apply_thread_edits(루카 모드와 같은 몸).
    progress 는 **목표 칸**(target)을 적는다 — 지금 값과의 차이를 step 으로(다시 얹을 때도 되감긴 값에서 같은 step).
    pause(+on) = 멈춤 · 다시, after(+value) = 기다림 잇기 · 빈 값이면 깨우기. ↶ = 진행(그걸로 안 닫혔을 때) · 멈춤만."""
    import ooc_edits
    import thread_ledger
    tid = str(a.get("id") or "").strip().upper()
    op = str(a.get("op") or "").strip().lower()
    states = thread_ledger.load_states(channel_id) or {}
    st = states.get(tid)
    if not tid or not st:
        return _res(False, f"⚠ 이야기 실: '{tid or '?'}' 없음")
    title = str(st.get("title") or tid)
    undo: Optional[Dict[str, Any]] = None
    ev: Dict[str, Any] = {"field": "thread", "id": tid}
    if op == "progress":
        try:
            want = int(float(a.get("target")))
        except (TypeError, ValueError):
            return _res(False, "⚠ 진행: 숫자가 아니야")
        cur = int(st.get("progress") or 0)
        mx = int(st.get("max") or 0)
        want = max(0, min(mx, want)) if mx else max(0, want)
        if want == cur:
            return _res(False, f"⚠ {title}: 이미 {cur}")
        ev.update(op="progress", step=want - cur)
        rc = f"{title} 진행 {cur} → {want}" + (f"/{mx}" if mx else "")
        undo = {"action": "thread.edit", "op": "progress", "id": tid, "target": cur}
    elif op == "pause":
        on = a.get("on")
        on = bool(on) if on is not None else st.get("status") != "paused"
        ev["op"] = "pause" if on else "resume"
        rc = f"{title} " + ("멈춤" if on else "다시")
        undo = {"action": "thread.edit", "op": "pause", "id": tid, "on": not on}
    elif op == "after":
        after = str(a.get("value") if a.get("value") is not None else a.get("after") or "").strip().upper()
        if after:
            ev.update(op="sleep", after=after)
            rc = f"{title} → {after} 기다림"
        else:
            ev["op"] = "wake"
            rc = f"{title} 깨움"
    elif op == "rank":
        # [2026-11-06 C116] 등급 고치기 — ↶ = 옛 등급 + 옛 진행(내려서 한 칸 앞에 갇혔으면 그 칸까지 되돌린다)
        rk = thread_ledger.rank_key(a.get("value") if a.get("value") is not None else a.get("rank"))
        if not rk:
            return _res(False, f"⚠ 등급: '{a.get('value') or a.get('rank') or '?'}'을 몰라")
        if st.get("kind") != "goal":
            return _res(False, f"⚠ {title}: 목표가 아니라 등급이 없어")
        if rk == st.get("rank"):
            return _res(False, f"⚠ {title}: 이미 {thread_ledger._rank_ko(rk)}")
        ev.update(op="rank", rank=rk)
        rc = f"{title} 등급 {thread_ledger._rank_ko(st.get('rank'))} → {thread_ledger._rank_ko(rk)}"
        undo = {"action": "thread.edit", "op": "rank", "id": tid, "value": st.get("rank"),
                "target": int(st.get("progress") or 0)}
    elif op in ("close", "void"):
        ev["op"] = op
        if op == "close":
            oc = str(a.get("outcome") or "done").strip().lower()
            ev["outcome"] = oc if oc in ("done", "dropped", "broken") else "done"
        rc = f"{title} " + {"done": "끝(이룸)", "dropped": "그만둠", "broken": "깨짐"}.get(ev.get("outcome"), "없던 일")
    else:
        return _res(False, f"⚠ 이야기 실: '{op or '?'}'은 못 해")
    evs = [ev]
    if op == "rank" and a.get("target") is not None:   # ↶ 로 온 등급 되돌리기 — 옛 진행 칸까지
        try:
            _t = int(float(a.get("target")))
            _mx = thread_ledger.goal_max(ev["rank"])
            _now = min(int(st.get("progress") or 0), max(0, _mx - 1)) if int(st.get("progress") or 0) >= _mx else int(st.get("progress") or 0)
            _t = max(0, min(_mx - 1, _t))
            if _t != _now:
                evs.append({"field": "thread", "id": tid, "op": "progress", "step": _t - _now})
        except (TypeError, ValueError):
            pass
    lines = ooc_edits.apply_thread_edits(channel_id, evs)
    good = [x for x in lines if _ok_line(x) and not str(x).startswith("ℹ")]
    if not good:
        return _res(False, next((x for x in lines if not _ok_line(x) or str(x).startswith("ℹ")), "⚠ 이야기 실: 변화 없음"))
    if op == "progress":
        st2 = (thread_ledger.load_states(channel_id) or {}).get(tid) or {}
        if st2.get("status") not in getattr(thread_ledger, "LIVE", ("open", "paused", "asleep")):
            undo = None      # 다 차서 닫혔다(보상 · 깨우기까지 돌았다) — 되돌리기 없음
    return _res(True, " · ".join(good)[:200], rc, undo)


def _exec_sys_decl(channel_id: str, uid: str, a: Dict[str, Any]) -> Dict[str, Any]:
    """[2026-11-06 C116 판1 1b] 기본 카드 — 활력 · 평형 · 소지품 한 칸(custom_vars.revise_reserved, 옛 `!출력룰 수정 변수`와 같은 검증).
    선언 층이라 넘겨 얹힘으로도 살지만 일지에도 남긴다(0-1c 규칙). ↶ = 옛 값."""
    import custom_vars as cv
    name = str(a.get("name") or "").strip()
    field = str(a.get("field") or "").strip()
    ok, line, old = cv.revise_reserved(channel_id, name, field, a.get("value"))
    if not ok:
        return _res(False, f"⚠ {name or '기본 값'}: {line}")
    lab = _SYS_FIELD_KR.get(field, field)
    undo = {"action": "sys.decl", "name": name, "field": field, "value": old} if old is not None else None
    return _res(True, f"✓ {name} {lab} 고침", f"{name} {lab} 고침", undo)


_SYS_FIELD_KR = {"rule": "규칙", "max_gain": "상승 캡", "max_loss": "하강 캡", "max": "칸 수", "format": "표시"}


def basic_card(channel_id: str) -> Dict[str, Any]:
    """[2026-11-06 C116 판1 1b] 모듈 앱 맨 위 "기본" 카드 — 코드가 심은 값(활력 · 평형 · 소지품). 번호 없음 · 못 지움.
    켬끔 = 활력 토글 모듈(`vigor`) 그대로. 고칠 칸 = 옛 `!출력룰 수정 변수` 몫(rule · 캡 · 표시 / 소지품은 규칙 · 칸 수 · 표시)."""
    import custom_vars as cv
    parts: List[Dict[str, Any]] = []
    try:
        import module_frame as _mf
        m = _mf.get("vigor") if hasattr(_mf, "get") else None
        if m is not None and m.toggle is not None:
            parts.append({"t": "switch", "label": "활력 · 평형 사용", "on": bool(_mf.is_on(m, channel_id)),
                          "act": {"action": "mod.toggle", "args": {"module": m.id}}})
    except Exception as e:
        logger.debug(f"[Activity] basic toggle skipped: {e}")
    decl = cv.get_declarations(channel_id) or {}
    for nm in list(cv.SYSTEM_VARS) + list(cv.BUILTIN_VARS):
        spec = decl.get(nm) or {}
        fields = cv.SYSTEM_EDITABLE if nm in cv.SYSTEM_VARS else cv.BUILTIN_EDITABLE
        inner: List[Dict[str, Any]] = []
        for f in fields:
            cur = spec.get(f)
            if f == "rule":
                inp = _inp("value", "area", str(cur or ""), maxlength=cv.RULE_MAX)
            elif f == "format":
                inp = _inp("value", "text", str(cur or ""), maxlength=cv.FORMAT_MAX, placeholder="{v} …")
            elif f == "max":
                inp = _inp("value", "number", _num_or_none(cur), min=1, max=cv.LIST_MAX_HARD)
            else:
                inp = _inp("value", "number", _num_or_none(cur), min=1, max=cv.DELTA_CAP_MAX)
            inner.append(_edit(_SYS_FIELD_KR.get(f, f), cur, [inp], "sys.decl", {"name": nm, "field": f}))
        parts.append({"t": "fold", "title": nm, "parts": inner})
    parts.append({"t": "text", "text": "코드가 심은 값 — 못 지운다. 값의 주인은 봇이고, 여기선 규칙 · 캡 · 표시만 고친다.", "muted": True})
    return {"t": "card", "title": "기본", "icon": "blocks", "parts": parts}


def _exec_clock_doom(channel_id: str, uid: str, a: Dict[str, Any]) -> Dict[str, Any]:
    """[C116 c3] 위협 시계 — ooc_edits.apply_doom_clock_edits(루카 모드와 같은 몸). ↶ = 칸(set)만."""
    import ooc_edits
    op = str(a.get("op") or "").strip().lower()
    name = str(a.get("name") or "").strip()
    if not name:
        return _res(False, "⚠ 시계: 이름이 없어")
    old = None
    if op == "set":
        import domain_manager as dm
        c = next((x for x in (dm.get_world_state(channel_id) or {}).get("doom_clocks") or []
                  if isinstance(x, dict) and x.get("name") == name and not x.get("resolved")), None)
        old = _num_or_none((c or {}).get("filled"))
    lines = ooc_edits.apply_doom_clock_edits(channel_id, [{"op": op, "key": name, "filled": a.get("filled")}], actor=uid)
    line = lines[0] if lines else "⚠ 시계: 변화 없음"
    if not _ok_line(line):
        return _res(False, line)
    rc = line.lstrip("✓ ").strip()
    undo = ({"action": "clock.doom", "op": "set", "name": name, "filled": old}
            if op == "set" and old is not None else None)
    if op == "set" and old is not None:
        rc = f"시계 '{name}' {old} → {line.rsplit(' ', 1)[-1]}"
    return _res(True, line, rc, undo)


def _exec_pc_info(channel_id: str, uid: str, a: Dict[str, Any]) -> Dict[str, Any]:
    """`!설명` — heavy 콜이 낸 **상자**를 그대로(다시 콜 0)."""
    import domain_manager as dm
    box = a.get("box")
    if not isinstance(box, dict) or not uid:
        return _res(False, "⚠ PC 시트: 상자 없음")
    ok = dm.apply_pc_info_to_user(channel_id, uid, box)
    return _res(bool(ok), "✓ PC 시트" if ok else "⚠ PC 시트: 못 씀")


def _exec_pc_mask(channel_id: str, uid: str, a: Dict[str, Any]) -> Dict[str, Any]:
    """`!가면` — 참가자 칸은 되감기 뒤에도 있다(그 턴을 돈 사람). 가면 · 페이지 · 옛 상자 치우기만."""
    import domain_manager as dm
    import wiki_store
    name = str(a.get("name") or "").strip()
    if not name or not uid:
        return _res(False, "⚠ 가면: 이름 없음")
    dm.set_user_mask(channel_id, uid, name)
    wiki_store.ensure_pc_page(channel_id, uid, name)
    dm.clear_default_pc_info(channel_id)
    return _res(True, f"✓ 가면 {name}")


_TIME_STATE_KEYS = ("year", "month", "day", "hour", "minute", "time_slot", "weather")


def _exec_time_state(channel_id: str, uid: str, a: Dict[str, Any]) -> Dict[str, Any]:
    """`!시간` 뒤 시계 **그대로** — 진행(날씨 무작위)이든 설정(시간대만 바꾼 것)이든 같은 값으로 얹는다."""
    import domain_manager as dm
    w = dm.get_world_state(channel_id) or {}
    got = {k: a[k] for k in _TIME_STATE_KEYS if k in a and a[k] is not None}
    if not got:
        return _res(False, "⚠ 시간: 적힌 값 없음")
    w.update(got)
    dm.update_world_state(channel_id, w)
    return _res(True, "✓ 시간 " + _clock_text(dict(_clock_now(channel_id))))


def _exec_ooc_edits(channel_id: str, uid: str, a: Dict[str, Any]) -> Dict[str, Any]:
    """[C116 0-1c c2] 루카 모드 편집 다시 얹기 — 편집 콜이 낸 edits(되감기에 닿는 칸만)를 같은 함수로 다시 적용한다.
    편집 콜은 다시 안 돈다. 처음 적용 때보다 ⚠ · ✗ 줄이 늘면 못 얹은 것(버린 턴에 생긴 대상 등)."""
    import command_handler as chd
    edits = [e for e in (a.get("edits") or []) if isinstance(e, dict)]
    if not edits:
        return _res(False, "⚠ 루카 편집: 적힌 편집 없음")
    r = chd.apply_ooc_edit_list(channel_id, uid, edits)
    bad = [str(x) for x in (r.get("journal_lines") or []) if not _ok_line(x)]
    try:
        was = int(a.get("fails") or 0)
    except (TypeError, ValueError):
        was = 0
    if len(bad) > was:
        return _res(False, "⚠ 루카 편집: " + " · ".join(bad[:3])[:160])
    good = [str(x) for x in (r.get("journal_lines") or []) if _ok_line(x)]
    return _res(True, "✓ 루카 편집 " + (" · ".join(good[:3])[:160] if good else f"{len(edits)}건"))


_EXEC = {"decl.set": _exec_decl_set, "decl.item": _exec_decl_item, "rel.set": _exec_rel_set,
         "rel.remove": _exec_rel_remove, "clock.set": _exec_clock_set, "place.set": _exec_place_set,
         "mod.toggle": _exec_mod_toggle, "mod.set": _exec_mod_set,
         "app.kv": _exec_app_kv,   # [C73] 일지 전용(ACTIONS 밖)
         # [C116 0-1c c1] 명령 입구 — 일지 전용(ACTIONS 밖, 화면 입구는 c3)
         "npc.register": _exec_npc_register, "npc.delete": _exec_npc_delete,
         "npc.clear_session": _exec_npc_clear_session, "npc.alias": _exec_npc_alias,
         "npc.merge": _exec_npc_merge, "npc.status": _exec_npc_status, "npc.fields": _exec_npc_fields,
         "npc.sheet": _exec_npc_sheet, "npc.form": _exec_npc_form,
         "pc.info": _exec_pc_info, "pc.mask": _exec_pc_mask, "time.state": _exec_time_state,
         "ooc.edits": _exec_ooc_edits,   # [C116 0-1c c2] 루카 모드 편집 — 일지 전용
         # [C116 0-1c c3] 화면 입구(ACTIONS 에도)
         "npc.alias_remove": _exec_npc_alias_remove, "npc.place": _exec_npc_place, "npc.knows": _exec_npc_knows,
         "pc.sheet": _exec_pc_sheet, "pc.memo": _exec_pc_memo, "pc.state": _exec_pc_state,
         "thread.edit": _exec_thread_edit, "clock.doom": _exec_clock_doom,
         "sys.decl": _exec_sys_decl}   # [C116 판1 1b]


def record(channel_id: str, uid: str, edit: Dict[str, Any], receipt: str = "", kind: str = "cmd") -> Optional[int]:
    """[2026-11-05 C116 0-1c c1] 화면 밖 입구(명령 · 루카 모드)의 쓰기 → 편집 일지 한 줄(after_turn = 지금 턴).
    잠금 · rev 검사는 없다(쓰기는 이미 됐다). kind cmd · ooc 는 ↶ 대상이 아니다(`_undo` 는 kind edit 만). 실패 None."""
    if not isinstance(edit, dict) or str(edit.get("action") or "") not in _EXEC:
        logger.warning(f"[Activity] record: 모르는 액션 {edit.get('action') if isinstance(edit, dict) else edit!r}")
        return None
    try:
        import sqlite_store
        return sqlite_store.append_activity_edit(str(channel_id), _turn(channel_id), str(uid or ""), dict(edit), None,
                                                 str(receipt or ""), kind=kind)
    except Exception as e:
        logger.warning(f"[Activity] record 실패: {e}")
        return None


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

def replay_after(channel_id: str, snap_turn: int, after_id: Optional[int] = None) -> int:
    """스냅샷 복원 **직후**, 다시 도는 턴 **앞**에 부른다. 되돌린 지점 뒤(after_turn > snap_turn)에 적힌 편집을
    적힌 순서대로 다시 얹는다(↶ 행도 편집이라 같이 — 결과가 같다). 일지는 다시 적지 않는다. 돌려줌 = 얹은 수.
    [C117 A3] after_id = 스냅숏을 찍을 때의 일지 끝 id — 그 뒤 행만. 연속 `!다시` 의 두 번째 스냅숏은 첫 `!다시` 가
    다시 얹은 뒤에 찍혀 그 몫을 이미 담는다 → 같은 행을 두 번 얹지 않는다(더한 양으로 적힌 누르기가 두 번 더해지지 않게).
    None = 옛 스냅숏(그 턴 뒤 일지 전부 — 절대값 쓰기라 멱등)."""
    try:
        import sqlite_store
        rows = sqlite_store.read_activity_edits(channel_id, after_turn_gt=int(snap_turn), after_id=after_id)
    except Exception as e:
        logger.debug(f"[Activity] replay read skipped: {e}")
        return 0
    n = 0
    fails: List[str] = []
    for r in rows:
        if str((r.get("edit") or {}).get("action") or "") in CARRIED_ACTIONS:
            continue        # [C72 §5] 넘겨 얹기가 이미 지금 값을 들고 왔다
        res = execute(channel_id, r.get("uid") or "", r.get("edit") or {})
        if res.get("ok"):
            n += 1
        else:
            # [C116 0-1c c1] 못 얹은 편집 — 대개 버린 턴에 생긴 대상(그 턴에 처음 나온 인물 등). `!다시` 안내에 붙인다.
            fails.append(str(r.get("receipt") or (r.get("edit") or {}).get("action") or "?")[:60])
    _REPLAY_FAILS[str(channel_id)] = fails
    if rows:
        logger.info("[Activity] !다시 — 편집 일지 %d/%d건 다시 얹음 (after t%s)%s", n, len(rows), snap_turn,
                    f" · 못 얹음 {fails}" if fails else "")
    return n


_REPLAY_FAILS: Dict[str, List[str]] = {}


def replay_failures(channel_id: str) -> List[str]:
    """[C116 0-1c c1] 바로 앞 replay_after 가 못 얹은 편집(영수증 글) — 한 번 읽으면 비운다."""
    return _REPLAY_FAILS.pop(str(channel_id), [])


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
    if want == "medit":
        return build_motion_edit(channel_id, uid)
    if want.startswith("pedit:"):
        return build_person_edit(channel_id, uid, want[len("pedit:"):])
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
    if want not in VIEWS and not (want.startswith("app:") and len(want) > 4) \
            and not (want.startswith("pedit:") and len(want) > 6):
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
