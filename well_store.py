"""
Lorekeeper — 중력 우물 장부 (C75)
======================================
천 = world_tree · 공 = 우물(아크) · 무게 = 쌓인 id 수 · 거리 = 홉. 스펙:
파티쳇수정/output_artifact/C75_중력우물_지도·스펙_2026-10-24.md · C75_G2_옮기기·강착·관측_지도·스펙_2026-10-24.md

행이 정본이다(sqlite `well_log`, 스레드 장부와 같은 꼴). 지금 상태 = `fold(행)`. `!다시` 는 워터마크 트림이
곧 되감기, `!클리어` · `!리셋` 은 행을 지운다. JSON(줄거리 상태)엔 `well_cache`(그리기 lerp · 지난 근접) 하나만 —
잃어도 기본값으로 돌아갈 캐시다.

[G2] 아크가 줄거리(`is_arc`)에서 여기로 이사했다. 질량 = 시계(생김 · 터짐), 이변 = 빛(관측 — 질량 아님),
줄어듦 = 호킹. 옛 아크 줄거리는 첫 tick 에 한 번 옮기고(`migrate`) 아크 칸을 걷는다.

읽는 쪽 계약(옛 `get_active_arcs` 칸 이름 그대로): `arcs(ch)` = 이름 얻은 산 우물 —
id · status("active") · declared_goal · phases · next_waypoint · backstage_reality · sensory_foreshadowing ·
offscreen_actions · proximity · weight · pacing · armed · origin_category + mass · d · where · entities · omens_seen.
"""

import logging
import math
from typing import Any, Dict, List, Optional, Tuple

import config

logger = logging.getLogger("Well")

SHADOW = "shadow"     # G1 그림자 — 옮기기 전까지만
DUST = "dust"         # 이름 없는 우물
NAMED = "named"       # 이름 얻은 우물(아크)
CLIMAX = "climax"     # [G3] 결전 — 익은 우물 중심(안)에 PC 가 섰다
ENDED = "ended"
_LIVE = (DUST, NAMED, SHADOW, CLIMAX)
_ARCLIKE = (NAMED, CLIMAX)
_ATT_ORDER = ("hostile", "unfriendly", "wary", "neutral", "friendly", "warm", "loyal", "devoted")

_ARC_FIELDS = (
    "is_arc", "phase_pos", "proximity", "pacing", "momentum", "weight",
    "declared_goal", "origin_category", "phases", "next_waypoint", "backstage_reality",
    "sensory_foreshadowing", "offscreen_actions", "trajectory",
    "promoted_at_turn", "last_advanced_turn", "armed",
    "well_id", "well_mass", "well_d", "well_proximity",
)


def _cfg(name: str, default: Any) -> Any:
    return getattr(config, name, default)


def _int(v: Any, default: int = 0) -> int:
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


# =========================================================
# 1. fold — 행 → 우물
# =========================================================

def _new_well(wid: int, r: Dict[str, Any], turn: int) -> Dict[str, Any]:
    origin = str(r.get("origin") or "")
    return {
        "id": wid, "center": str(r.get("center") or ""), "ids": [],
        "origin": origin, "storyline_id": r.get("storyline_id"),
        "label": str(r.get("label") or ""), "status": SHADOW if origin == SHADOW else DUST,
        "born_turn": turn, "last_capture_turn": turn,
        "phases": [], "next_waypoint": "", "backstage_reality": "",
        "sensory_foreshadowing": [], "offscreen_actions": [],
        "armed": False, "category": "", "omens_seen": 0, "caught": [], "rejected_mass": 0,
        "grown": [], "collisions": {}, "collapsed": False,
    }


def _trace_entry(p: Dict[str, Any], turn: int, src: str = "") -> Dict[str, Any]:
    return {"summary": str(p.get("summary") or "")[:200], "polarity": str(p.get("polarity") or "mixed"),
            "intensity": str(p.get("intensity") or "Mid"), "turn": _int(p.get("turn"), turn),
            "src": str(src or p.get("src") or "")}


def _push(w: Dict[str, Any], key: str, entry: Dict[str, Any], cap: int) -> None:
    arr = w.setdefault(key, [])
    arr.append(entry)
    if len(arr) > cap:
        w[key] = arr[-cap:]


def fold(rows: List[Dict[str, Any]], seen: Optional[set] = None) -> Dict[int, Dict[str, Any]]:
    """well_log 행(id 오름차순) → {well_id: 우물}. op 는 G2 스펙 §3 표.
    한 id(key)는 한 산 우물에만 — 이미 다른 산 우물이 쥔 key 는 capture 에서 건너뛴다.
    seen(set)을 넘기면 한 번이라도 든 key 를 채운다(끝난 우물 포함) — 시계 맞춤이 같은 시계를 두 번 낳지 않게."""
    wells: Dict[int, Dict[str, Any]] = {}
    owner: Dict[str, int] = {}
    ph_cap = int(_cfg("ARC_PHASES_CAP", 10))
    fg_cap = int(_cfg("ARC_FORESHADOWING_CAP", 20))
    bg_cap = int(_cfg("ARC_OFFSCREEN_ACTIONS_CAP", 20))
    for r in rows or []:
        wid = _int(r.get("well_id"), -1)
        if wid < 0:
            continue
        op = str(r.get("op") or "")
        turn = _int(r.get("turn"))
        p = r.get("payload") if isinstance(r.get("payload"), dict) else {}
        if op == "birth":
            if wid not in wells:
                wells[wid] = _new_well(wid, r, turn)
            continue
        w = wells.get(wid)
        if not w:
            continue
        if op == "capture":
            if w["status"] == ENDED:
                continue
            got = False
            for it in r.get("ids") or []:
                if not isinstance(it, dict):
                    continue
                k = str(it.get("key") or "")
                if not k or (k in owner and wells.get(owner[k], {}).get("status") != ENDED):
                    continue
                owner[k] = wid
                w["ids"].append(dict(it))
                if seen is not None:
                    seen.add(k)
                got = True
            if got:
                w["last_capture_turn"] = turn
        elif op == "center":
            if not w["center"] and r.get("center"):
                w["center"] = str(r["center"])
        elif op == "name":
            if w["status"] == ENDED:
                continue
            if w["status"] != CLIMAX:
                w["status"] = NAMED
            if r.get("label"):
                w["label"] = str(r["label"])
            w["named_turn"] = turn
            # [검토 #3] 이름 얻음도 활동 — 옮긴 그림자가 G1 의 옛 먹은 턴으로 그 tick 에 바로 증발하지 않게
            w["last_capture_turn"] = max(_int(w.get("last_capture_turn")), turn)
            if isinstance(p.get("phases"), list):
                w["phases"] = [str(x) for x in p["phases"] if str(x or "").strip()][-ph_cap:]
            if str(p.get("phase") or "").strip():
                _push(w, "phases", str(p["phase"]).strip(), ph_cap)
            for k in ("next_waypoint", "backstage_reality"):
                if str(p.get(k) or "").strip():
                    w[k] = str(p[k]).strip()
            if p.get("armed"):
                w["armed"] = True
            if str(p.get("category") or "").strip():
                w["category"] = str(p["category"]).strip()
            for side, key, cap in (("fg", "sensory_foreshadowing", fg_cap), ("bg", "offscreen_actions", bg_cap)):
                for e in (p.get(side) or []):
                    if isinstance(e, dict) and str(e.get("summary") or "").strip():
                        _push(w, key, _trace_entry(e, turn, "migrated"), cap)
                        if side == "fg":
                            # [G3 검토 #1] 옮긴 아크의 무대 위 단서 = PC 가 본 떡밥(옛 아크도 익을 수 있고 '못 본 우물'로 안 무너진다)
                            w["omens_seen"] += 1
        elif op == "phase":
            if str(p.get("label") or "").strip():
                _push(w, "phases", str(p["label"]).strip(), ph_cap)
        elif op == "note":
            for k in ("next_waypoint", "backstage_reality"):
                if str(p.get(k) or "").strip():
                    w[k] = str(p[k]).strip()
        elif op == "trace":
            if not str(p.get("summary") or "").strip():
                continue
            e = _trace_entry(p, turn)
            if p.get("side") == "fg":
                _push(w, "sensory_foreshadowing", e, fg_cap)
                if e["src"] == "light":
                    w["omens_seen"] += 1
            else:
                _push(w, "offscreen_actions", e, bg_cap)
        elif op == "caught":
            k = str(p.get("key") or "")
            if k and all(c.get("key") != k for c in w["caught"]):
                w["caught"].append({"key": k, "clock": str(p.get("clock") or ""), "end": str(p.get("end") or ""),
                                    "turn": turn})
        elif op == "reject":
            w["rejected_mass"] = max(w["rejected_mass"], _int(p.get("mass")))
        elif op == "grow":
            # [G3] 이 우물 쪽으로 휜 채 끝난 목표(성장 셈) — key 로 한 번
            k = str(p.get("key") or "")
            if k and all(g.get("key") != k for g in w["grown"]):
                w["grown"].append({"key": k, "goal": str(p.get("goal") or ""), "turn": turn})
        elif op == "climax":
            if w["status"] in (NAMED, DUST):
                w["status"] = CLIMAX
                w["climax_turn"] = turn
        elif op == "collapse":
            # [G3] 멀리서 무너짐(Ia) 잔해 — 가장 최근 id 몇 개만 남고 나머지는 천으로
            keep = {str(k) for k in (p.get("keep") or [])}
            kept = []
            for it in w["ids"]:
                k = str(it.get("key") or "")
                if k in keep:
                    kept.append(it)
                elif owner.get(k) == wid:
                    owner.pop(k, None)
            w["ids"] = kept
            w["collapsed"] = True
        elif op == "collide":
            try:
                w["collisions"][int(p.get("other"))] = (_int(p.get("mass")), _int(p.get("other_mass")), turn)
            except (TypeError, ValueError):
                pass
        elif op == "end":
            if w["status"] == ENDED:
                continue
            w["status"] = ENDED
            w["end_turn"] = turn
            w["end_reason"] = str(p.get("reason") or "")
            for it in w["ids"]:
                k = str(it.get("key") or "")
                if owner.get(k) == wid:
                    owner.pop(k, None)
    for w in wells.values():
        w["mass"] = len(w["ids"])
    return wells


def _rows(channel_id: str) -> List[Dict[str, Any]]:
    try:
        import sqlite_store
        return sqlite_store.read_well_log(channel_id)
    except Exception as e:
        logger.debug(f"[Well] read skip: {e}")
        return []


def _append(channel_id: str, rows: List[Dict[str, Any]]) -> int:
    if not rows:
        return 0
    try:
        import sqlite_store
        return sqlite_store.append_well_events(channel_id, rows)
    except Exception as e:
        logger.warning(f"[Well] append failed: {e}")
        return 0


def load(channel_id: str, seen: Optional[set] = None) -> Dict[int, Dict[str, Any]]:
    return fold(_rows(channel_id), seen)


def is_live(w: Dict[str, Any]) -> bool:
    return w.get("status") in _LIVE and int(w.get("mass", 0) or 0) > 0


def _live_sorted(wells: Dict[int, Dict[str, Any]]) -> List[Dict[str, Any]]:
    return sorted((w for w in wells.values() if is_live(w)), key=lambda w: (w.get("born_turn", 0), w["id"]))


def _owner_of(wells: Dict[int, Dict[str, Any]], *keys: str) -> Optional[Dict[str, Any]]:
    ks = {k for k in keys if k}
    for w in _live_sorted(wells):
        if any(str(i.get("key") or "") in ks for i in w["ids"]):
            return w
    return None


def _next_id(wells: Dict[int, Dict[str, Any]], storylines: Optional[List[Dict[str, Any]]] = None) -> int:
    hi = max([0] + list(wells.keys()) + [_int(s.get("id")) for s in (storylines or []) if isinstance(s, dict)])
    return hi + 1


# =========================================================
# 2. 천 위의 자리
# =========================================================

def _nodes(channel_id: str) -> Dict[str, Any]:
    try:
        import world_tree
        return world_tree._get_tree(channel_id).get("nodes", {}) or {}
    except Exception:
        return {}


def _hops(nodes: Dict[str, Any], from_id: Optional[str]) -> Dict[str, int]:
    if not from_id or not nodes or from_id not in nodes:
        return {}
    try:
        import world_tree
        return world_tree.hop_distances_in(nodes, from_id, via_world=True)
    except Exception:
        return {}


def node_of(channel_id: str, location: Any) -> Optional[str]:
    """장소 글자 → 노드(호명 토큰 허용 — PC 위치 · 이변 자리처럼 사람이 부른 이름)."""
    loc = str(location or "").strip()
    if not loc or loc.lower() == "unknown":
        return None
    try:
        import world_tree
        return world_tree.resolve_node_id(channel_id, loc)
    except Exception:
        return None


def pc_node(channel_id: str, location: Any = None) -> Optional[str]:
    """PC 노드(world_tree). location 을 주면 그걸로(이번 턴 버스 위치), 비었으면 저장된 현재 위치. 못 풀면 None —
    위치 미기록과 '아무 데도 없음'은 다르다(get_presence_tiers 와 같은 결)."""
    if not str(location or "").strip():
        try:
            import domain_manager
            location = domain_manager.get_current_location(channel_id)
        except Exception:
            return None
    return node_of(channel_id, location)


def center_of_storyline(channel_id: str, sl: Dict[str, Any]) -> str:
    """옛 아크의 중심 = entities 인물 중 위치가 잡히는 첫 사람의 노드(C75 §14). 없으면 ''."""
    try:
        import world_tree
        for name in sl.get("entities") or []:
            loc = world_tree.get_npc_location(channel_id, name)
            if loc:
                nid = world_tree.resolve_node_id(channel_id, loc)
                if nid:
                    return nid
    except Exception as e:
        logger.debug(f"[Well] center skip: {e}")
    return ""


def resolve_center(channel_id: str, center: str, nodes: Dict[str, Any]) -> Optional[str]:
    """쥐고 있던 중심 노드 id → 지금 노드 id. 노드 병합(merge_nodes)이 id 를 바꾸면 이름 · 별칭으로 다시 찾는다."""
    if not center:
        return None
    if center in nodes:
        return center
    if not nodes:
        return None
    try:
        import world_tree
        return world_tree.resolve_node_id(channel_id, center, allow_token=False)
    except Exception:
        return None


def entity_ids(channel_id: str, name: Any) -> List[Dict[str, Any]]:
    """시계에 실려 오는 사람 · 세력 → id 칸. 위키 인물(없으면 세력) page id. PC 페이지(owner_uid)는 절대 안 싣는다."""
    nm = str(name or "").strip()
    if not nm:
        return []
    try:
        import wiki_store
        pid = wiki_store.resolve_page(channel_id, nm, "character")
        if pid:
            pg = wiki_store.get_page(channel_id, pid) or {}
            if pg.get("owner_uid"):
                return []
            return [{"key": pid, "pid": pid, "name": nm, "kind": "person"}]
        fid = wiki_store.resolve_page(channel_id, nm, "faction")
        if fid:
            return [{"key": fid, "pid": fid, "name": nm, "kind": "faction"}]
    except Exception as e:
        logger.debug(f"[Well] entity ids skip: {e}")
    return []


def clock_place(channel_id: str, clock: Dict[str, Any], pc_location: Any = None,
                wells: Optional[Dict[int, Dict[str, Any]]] = None) -> str:
    """시계가 떨어지는 자리(G2 스펙 §5 — 레티어스 10-24 밤 순서). 못 풀면 ''.
    ① linked_entity 가 장소면 그 노드(정확 · 별칭만) ② 사람이면 home_node ③ 그 사람이 든 우물의 중심 ④ PC 노드."""
    ent = str((clock or {}).get("linked_entity") or "").strip()
    nodes = _nodes(channel_id)
    if ent:
        try:
            import world_tree
            nid = world_tree.resolve_node_id(channel_id, ent, allow_token=False)
            if nid:
                return nid
            nid = world_tree.home_node(channel_id, ent)
            if nid:
                return nid
        except Exception as e:
            logger.debug(f"[Well] place skip: {e}")
        keys = [i["key"] for i in entity_ids(channel_id, ent)]
        if keys:
            if wells is None:
                wells = load(channel_id)
            w = _owner_of(wells, *keys)
            c = resolve_center(channel_id, w.get("center") or "", nodes) if w else None
            if c:
                return c
    return pc_node(channel_id, pc_location) or ""


# =========================================================
# 3. 강착 — 시계가 떨어진다 (본 스펙 §6)
# =========================================================

def _pull_best(channel_id: str, wells: Dict[int, Dict[str, Any]], node: Optional[str], nodes: Dict[str, Any],
               person_keys: set = frozenset(), names_in: str = "") -> Tuple[Optional[Dict[str, Any]], float]:
    """자리에서 가장 세게 당기는 산 우물과 그 당김 M ÷ (r+1)². 동점은 먼저 생긴 우물.
    자리를 못 쓰면(천이 비었거나 노드 없음) 사람으로 — 그 사람을 쥔 우물(또는 줄에 그 이름이 나온 우물)은 r = 0."""
    best, best_pull = None, 0.0
    live = _live_sorted(wells)
    if node and nodes and node in nodes:
        dist = _hops(nodes, node)
        for w in live:
            c = resolve_center(channel_id, w.get("center") or "", nodes)
            r = dist.get(c) if c else None
            if r is None:
                continue
            pull = float(w["mass"]) / float((r + 1) ** 2)
            if pull > best_pull:
                best, best_pull = w, pull
        return best, best_pull
    low = str(names_in or "").lower()
    for w in live:
        hit = any(str(i.get("key") or "") in person_keys for i in w["ids"])
        if not hit and low:
            hit = any(i.get("kind") in ("person", "faction") and str(i.get("name") or "").strip()
                      and str(i["name"]).strip().lower() in low for i in w["ids"])
        if hit and float(w["mass"]) > best_pull:
            best, best_pull = w, float(w["mass"])
    return best, best_pull


def accrete(channel_id: str, node: str, ids: List[Dict[str, Any]], turn: int,
            wells: Optional[Dict[int, Dict[str, Any]]] = None, nodes: Optional[Dict[str, Any]] = None,
            origin: str = DUST) -> int:
    """후보 자리에서 가장 센 당김 ≥ P_cap 이면 그 우물로, 아니면 그 자리에 새 먼지 우물. 돌려줌 = 우물 id."""
    if wells is None:
        wells = load(channel_id)
    if nodes is None:
        nodes = _nodes(channel_id)
    pk = {str(i.get("key")) for i in ids if i.get("kind") in ("person", "faction")}
    best, pull = _pull_best(channel_id, wells, node, nodes, pk)
    if best is not None and pull >= float(_cfg("WELL_P_CAP", 1.0)):
        _append(channel_id, [{"turn": turn, "well_id": best["id"], "op": "capture", "ids": ids}])
        return best["id"]
    wid = _next_id(wells)
    _append(channel_id, [{"turn": turn, "well_id": wid, "op": "birth", "center": node or "", "origin": origin or DUST},
                         {"turn": turn, "well_id": wid, "op": "capture", "ids": ids}])
    return wid


def _clock_keys(clock: Dict[str, Any]) -> Tuple[str, str]:
    name = str(clock.get("name") or "")
    return f"C:{name}#{_int(clock.get('turn_created'))}", f"C:{name}"


def _first_by_name(clocks: List[Dict[str, Any]]) -> Dict[str, int]:
    out: Dict[str, int] = {}
    for c in clocks or []:
        if isinstance(c, dict) and str(c.get("name") or "").strip():
            t = _int(c.get("turn_created"))
            out[str(c["name"])] = min(out.get(str(c["name"]), t), t)
    return out


def _center_brightness(channel_id: str, w: Dict[str, Any], pc_location: Any = None,
                       nodes: Optional[Dict[str, Any]] = None) -> Optional[float]:
    """PC 자리에서 본 이 우물의 밝기 M ÷ (d+1)²(중심 → PC). PC 노드 · 중심을 못 풀면 None."""
    if nodes is None:
        nodes = _nodes(channel_id)
    pn = pc_node(channel_id, pc_location)
    c = resolve_center(channel_id, w.get("center") or "", nodes)
    if not pn or not c:
        return None
    d = _hops(nodes, pn).get(c)
    return None if d is None else float(w["mass"]) / float((d + 1) ** 2)


def _maybe_collapse(channel_id: str, wid: int, turn: int, pc_location: Any = None) -> str:
    """[G3] 멀리서 무너짐(Ia, 본 스펙 §8) — 시계가 또 떨어진 직후: M ≥ N · 떡밥 0(PC 가 무대 위에서 한 번도 못 본 우물).
    PC 자리 밝기 ≥ P_bend(또는 모름) → 잔해(가장 최근 id 몇 개 + 막후 한 줄) · 미만 → 조용히 꺼짐. 돌려줌 remnant · quiet · ''."""
    w = load(channel_id).get(int(wid))
    if not w or w["status"] not in (DUST, NAMED) or not is_live(w):
        return ""
    if int(w["mass"]) < int(_cfg("WELL_MASS_LIMIT", 16)) or int(w.get("omens_seen") or 0) > 0:
        return ""
    nodes = _nodes(channel_id)
    pn = pc_node(channel_id, pc_location)
    c = resolve_center(channel_id, w.get("center") or "", nodes)
    d = _hops(nodes, pn).get(c) if (pn and c) else None
    # [G3 검토 #1] '멀리서' — PC 가 그 중심 안(중심이 PC 자리의 조상)이거나 닿는 거리 R 안이면 무너지지 않는다
    if d is not None and (d <= reach(int(w["mass"])) or c in _chain(nodes, pn)):
        return ""
    b = None if d is None else float(w["mass"]) / float((d + 1) ** 2)
    if b is not None and b < float(_cfg("WELL_P_BEND", 0.25)):
        _append(channel_id, [{"turn": turn, "well_id": w["id"], "op": "end", "payload": {"reason": "collapsed"}}])
        logger.info("[Well] collapse(quiet) #%s M=%s", w["id"], w["mass"])
        return "quiet"
    n = int(_cfg("WELL_REMNANT", 4))
    keep = [str(i.get("key")) for i in w["ids"][-n:]]
    who = w.get("label") or ""
    if not who:
        who = str((nodes.get(c) or {}).get("name") or "") if c else ""
    _append(channel_id, [
        {"turn": turn, "well_id": w["id"], "op": "collapse", "payload": {"keep": keep}},
        {"turn": turn, "well_id": w["id"], "op": "trace", "payload": {
            "side": "bg", "summary": f"{who or '먼 흐름'} — 멀리서 무너졌다", "polarity": "mixed",
            "intensity": "High", "src": "collapse"}}])
    logger.info("[Well] collapse(remnant) #%s M=%s → %s", w["id"], w["mass"], len(keep))
    return "remnant"


def reconcile_clocks(channel_id: str, clocks: Optional[List[Dict[str, Any]]] = None, turn: Optional[int] = None,
                     pc_location: Any = None) -> Dict[str, Any]:
    """세계 시계 목록 ↔ 우물 행 맞춤(G2 스펙 §5). 멱등 — 몇 번 돌려도 같은 행. 부르는 곳: 배경 tick · 이변 라우터 입구.
    생김(산 시계 · 3턴 안 끝난 시계) → 강착 · 터짐(fired) → 생김을 쥔 우물 · 막아냄 · 달성 · 목표로 닫힘 → caught 행."""
    ev: Dict[str, Any] = {"born": [], "fired": [], "caught": []}
    if not channel_id:
        return ev
    if clocks is None or turn is None:
        try:
            import domain_manager
            world = domain_manager.get_world_state(channel_id) or {}
        except Exception:
            world = {}
        if clocks is None:
            clocks = world.get("doom_clocks") or []
        if turn is None:
            turn = _int(world.get("turn_index"))
    window = int(_cfg("WELL_RECONCILE_WINDOW", 3))
    # [검토 #4] G1 그림자가 쥔 맨 `C:<이름>` 은 그때 살아 있던 시계 — 같은 이름 중 **먼저 만든** 시계에만 맞춘다
    #   (끝난 뒤 같은 이름으로 다시 선 시계가 영영 안 태어나거나 옛 우물로 빨려 가지 않게).
    first_t = _first_by_name(clocks)
    seen: set = set()
    wells = load(channel_id, seen)
    caught = {c["key"] for w in wells.values() for c in w.get("caught") or []}
    nodes = _nodes(channel_id)
    for clock in clocks or []:
        if not isinstance(clock, dict) or not str(clock.get("name") or "").strip():
            continue
        name = str(clock["name"])
        live = not clock.get("resolved")
        end = str(clock.get("end") or "")
        if not live:
            _et = clock.get("end_turn")
            if _et is None or turn - _int(_et) > window:
                continue
        bkey, legacy = _clock_keys(clock)
        if _int(clock.get("turn_created")) != first_t.get(name):
            legacy = ""
        carried = entity_ids(channel_id, clock.get("linked_entity"))

        def _place() -> str:
            c = resolve_center(channel_id, str(clock.get("born_node") or ""), nodes)
            return c or clock_place(channel_id, clock, pc_location, wells)

        if bkey not in seen and not (legacy and legacy in seen):
            wid = accrete(channel_id, _place(), [{"key": bkey, "name": name, "kind": "clock"}] + carried,
                          turn, wells, nodes)
            ev["born"].append((name, wid))
            _c = _maybe_collapse(channel_id, wid, turn, pc_location)
            if _c:
                ev.setdefault("collapsed", []).append((wid, _c))
            seen = set()
            wells = load(channel_id, seen)
        if live:
            continue
        ow = _owner_of(wells, bkey, legacy)
        if end == "fired":
            fkey = bkey + ":fire"
            if fkey in seen:
                continue
            ids = [{"key": fkey, "name": name, "kind": "clock_fire"}] + carried
            if ow is not None:
                _append(channel_id, [{"turn": turn, "well_id": ow["id"], "op": "capture", "ids": ids}])
                wid = ow["id"]
            else:
                wid = accrete(channel_id, _place(), ids, turn, wells, nodes)
            ev["fired"].append((name, wid))
            _c = _maybe_collapse(channel_id, wid, turn, pc_location)
            if _c:
                ev.setdefault("collapsed", []).append((wid, _c))
            seen = set()
            wells = load(channel_id, seen)
        elif end in ("averted", "achieved", "quest"):
            if bkey in caught or ow is None:
                continue
            _append(channel_id, [{"turn": turn, "well_id": ow["id"], "op": "caught",
                                  "payload": {"key": bkey, "clock": name, "end": end}}])
            caught.add(bkey)
            ev["caught"].append((name, ow["id"]))
    if ev["born"] or ev["fired"] or ev["caught"]:
        logger.info("[Well] clocks t=%s born=%s fired=%s caught=%s", turn, ev["born"], ev["fired"], ev["caught"])
    return ev


# =========================================================
# 4. 보기 — 잴 때마다 (옛 아크 칸 이름 계약)
# =========================================================

def _allies(channel_id: str, wells: List[Dict[str, Any]], nodes: Dict[str, Any]) -> Dict[int, List[str]]:
    """[G3 ⑦ 동료 — 레티어스 10-25 "장르를 최대한 안 타게 · 관계 중점"] 우물이 생긴 뒤 PC 쪽으로 돌아선 사람(태도 로그
    friendly 이상으로 오른 전이, 한 사람 한 번) 중 그 우물에 든 사람이거나 지금 그 우물의 당김 안(자리 당김 ≥ P_bend)에
    선 사람. 범위를 장르가 아니라 중력으로 자른다. {우물 id: [이름]}"""
    if not wells:
        return {}
    try:
        import sqlite_store
        log = sqlite_store.read_attitude_log(channel_id, limit=500)
    except Exception:
        log = []
    rank = {a: i for i, a in enumerate(_ATT_ORDER)}
    fi, ne = rank["friendly"], rank["neutral"]
    events: List[Tuple[int, str]] = []
    latest: Dict[str, str] = {}
    for r in log or []:
        to = str(r.get("to") or "").strip().lower()
        fr = str(r.get("from") or "").strip().lower()
        nm = str(r.get("npc_name") or "").strip()
        if nm and to:
            latest[nm] = to
        # [G3 검토 #8] 첫 관측(from 빈칸 · result initial)은 돌아선 게 아니다
        if not fr or str(r.get("result") or "") == "initial":
            continue
        if nm and to in rank and rank[to] >= fi and rank[to] > rank.get(fr, ne):
            events.append((_int(r.get("turn")), nm))
    # 나중에 다시 등 돌린 사람은 안 센다 — 지금 태도가 friendly 이상인 사람만
    events = [(t, n) for t, n in events if rank.get(latest.get(n, ""), -1) >= fi]
    if not events:
        return {}
    try:
        import world_tree, wiki_store
    except Exception:
        return {}
    names = {n for _, n in events}
    home = {}
    pid = {}
    for n in names:
        try:
            home[n] = world_tree.home_node(channel_id, n)
        except Exception:
            home[n] = None
        try:
            pid[n] = wiki_store.resolve_page(channel_id, n, "character")
        except Exception:
            pid[n] = None
    pb = float(_cfg("WELL_P_BEND", 0.25))
    hops_from: Dict[str, Dict[str, int]] = {}
    out: Dict[int, List[str]] = {}
    for w in wells:
        c = resolve_center(channel_id, w.get("center") or "", nodes)
        in_names = {str(i.get("name") or "").strip().lower() for i in w["ids"] if i.get("kind") in ("person", "faction")}
        in_keys = {str(i.get("key") or "") for i in w["ids"]}
        got: List[str] = []
        for t, n in events:
            if t < _int(w.get("born_turn")) or n in got:
                continue
            if n.lower() in in_names or (pid.get(n) and pid[n] in in_keys):
                got.append(n)
                continue
            nd = home.get(n)
            if not nd or not c:
                continue
            if nd not in hops_from:
                hops_from[nd] = _hops(nodes, nd)
            r_ = hops_from[nd].get(c)
            if r_ is not None and float(w["mass"]) / float((r_ + 1) ** 2) >= pb:
                got.append(n)
        out[w["id"]] = got
    return out


def ripeness(w: Dict[str, Any], allies: Optional[List[str]] = None) -> Dict[str, Any]:
    """[G3] 익음(옛 armed 칸) — 떡밥 omens_seen ≥ ⌈M÷4⌉ · 성장(휜 목표 + caught + 동료) ≥ max(1, ⌈M÷8⌉).
    이름 얻은 우물만. 옮긴 아크가 armed 였으면 익음으로 친다."""
    m = int(w.get("mass") or 0)
    om_need = int(math.ceil(m / 4.0))
    gr_need = max(1, int(math.ceil(m / 8.0)))
    omens = int(w.get("omens_seen") or 0)
    growth = len(w.get("grown") or []) + len(w.get("caught") or []) + len(allies or [])
    ripe = w.get("status") in _ARCLIKE and ((omens >= om_need and growth >= gr_need) or bool(w.get("armed")))
    return {"ripe": bool(ripe), "omens_need": om_need, "growth": growth, "growth_need": gr_need}


def view(channel_id: str, nt_state: Optional[Dict[str, Any]] = None, pc_location: Any = None,
         turn: Optional[int] = None, wells: Optional[Dict[int, Dict[str, Any]]] = None) -> List[Dict[str, Any]]:
    """산 우물마다 fold + 이번에 잰 값. PC 노드를 못 풀면 근접 = 지난 잰 값 − 0.05 × 지난 턴 수(본 스펙 §4).
    중심이 없거나 닿는 길이 없으면 근접 0(거리 ∞)."""
    if not channel_id:
        return []
    if wells is None:   # [C74 판4 검토 L2] 이미 접은 우물을 넘기면 다시 안 접는다
        wells = load(channel_id)
    live = _live_sorted(wells)
    if not live:
        return []
    if nt_state is None:
        try:
            import domain_manager
            nt_state = domain_manager.get_narrative_tracker_state(channel_id) or {}
        except Exception:
            nt_state = {}
    cache = (nt_state or {}).get("well_cache") or {}
    nodes = _nodes(channel_id)
    pn = pc_node(channel_id, pc_location)
    dist = _hops(nodes, pn)
    N = max(1, int(_cfg("WELL_MASS_LIMIT", 16)))
    allies = _allies(channel_id, [w for w in live if w["status"] in _ARCLIKE], nodes)
    out = []
    for w in live:
        c = resolve_center(channel_id, w.get("center") or "", nodes)
        cw = cache.get(str(w["id"])) or {}
        d = dist.get(c) if (c and pn) else None
        if d is not None:
            prox = 1.0 / (d + 1)
        elif pn is None and cw:
            if turn is None:
                try:
                    import domain_manager
                    turn = _int((domain_manager.get_world_state(channel_id) or {}).get("turn_index"))
                except Exception:
                    turn = _int(cw.get("turn"))
            prox = max(0.0, float(cw.get("prox", 0.0) or 0.0) - 0.05 * max(0, turn - _int(cw.get("turn"))))
        else:
            prox = 0.0
        climax = w["status"] == CLIMAX
        pacing = float(cw.get("pacing", 0.3) or 0.3)
        if climax:
            prox, pacing = 1.0, max(pacing, 0.9)   # [G3] 결전 — 옛 강제 절정(근접 1.0)의 자리
        rp = ripeness(w, allies.get(w["id"]))
        m = int(w["mass"])
        node_name = ""
        if c:
            node_name = str((nodes.get(c) or {}).get("name") or c)
        out.append({
            "id": w["id"], "status": "active", "well_status": w["status"],
            "declared_goal": w.get("label") or "", "phases": list(w.get("phases") or []),
            "next_waypoint": w.get("next_waypoint") or "", "backstage_reality": w.get("backstage_reality") or "",
            "sensory_foreshadowing": list(w.get("sensory_foreshadowing") or []),
            "offscreen_actions": list(w.get("offscreen_actions") or []),
            "armed": rp["ripe"], "origin_category": w.get("category") or "",
            "proximity": prox, "weight": min(1.0, m / N), "pacing": pacing,
            "climax": climax, "ripe": rp["ripe"], "growth": rp["growth"], "growth_need": rp["growth_need"],
            "omens_need": rp["omens_need"], "allies": list(allies.get(w["id"]) or []),
            "goals_grown": len(w.get("grown") or []), "collapsed": bool(w.get("collapsed")),
            "climax_turn": _int(w.get("climax_turn")),
            "seeded_only": bool(w["ids"]) and all(i.get("kind") in ("seed", "anchor") or i.get("via") == "seed"
                                                  for i in w["ids"]),
            "mass": m, "d": d, "center": c or "", "where": node_name, "brightness": m * prox * prox,
            "entities": [str(i.get("name")) for i in w["ids"] if i.get("kind") in ("person", "faction") and i.get("name")],
            "clocks": [str(i.get("name")) for i in w["ids"] if i.get("kind") == "clock" and i.get("name")],
            "omens_seen": int(w.get("omens_seen") or 0), "caught": list(w.get("caught") or []),
            "rejected_mass": int(w.get("rejected_mass") or 0),
            "born_turn": w.get("born_turn", 0), "last_capture_turn": w.get("last_capture_turn", 0),
            "origin": w.get("origin") or "",
        })
    return out


def arcs(channel_id: str, nt_state: Optional[Dict[str, Any]] = None, pc_location: Any = None) -> List[Dict[str, Any]]:
    """옛 `get_active_arcs` — 이름 얻은 산 우물(레티어스 10-24 밤: 먼지는 안에서만). 결전 중인 우물 포함."""
    return [w for w in view(channel_id, nt_state, pc_location) if w["well_status"] in _ARCLIKE]


def in_sight(a: Dict[str, Any]) -> bool:
    """[C74 판4 검토 S1] 렌더 쪽 보임 — Slot 30 아크 지시와 같은 문턱: PC 자리 밝기 ≥ P_bend 또는 근접 ≥ 노출 문턱(2홉 안).
    결전은 근접 1.0 이라 늘 보인다. 진행 중 블록 · 한 줄 아크가 이걸로 거른다(G2 ④ — 안 보이는 우물은 렌더에 안 간다)."""
    return (float(a.get("brightness") or 0) >= float(_cfg("WELL_P_BEND", 0.25))
            or float(a.get("proximity") or 0) >= float(_cfg("ARC_PROXIMITY_EXPOSURE_THRESHOLD", 0.3)))


def visible(channel_id: str, nt_state: Optional[Dict[str, Any]] = None, pc_location: Any = None) -> List[Dict[str, Any]]:
    """보이는 우물 — 이름 얻음 · PC 자리 밝기 M ÷ (d+1)² ≥ P_bend."""
    pb = float(_cfg("WELL_P_BEND", 0.25))
    return [w for w in arcs(channel_id, nt_state, pc_location) if w["brightness"] >= pb]


# =========================================================
# 5. 관측 — 이변 = 빛 (본 스펙 §7)
# =========================================================

def observe(channel_id: str, location: Any = "", line: str = "", pc_location: Any = None) -> Dict[str, Any]:
    """고른 이변의 빛 출처 · 밝기 · 갈래. {"well": 우물 | None, "pull", "b", "tier": stage | glow | dark | None}.
    출처 = 빛 자리에서 가장 세게 당기는 산 우물(당김 ≥ P_bend). 없으면 tier None(옛 발사 그대로).
    밝기 식은 config.WELL_LIGHT_FROM: "emission"(빛 난 자리 → PC) · "center"(우물 중심 → PC).
    PC 노드를 못 풀면 b = 1(옛처럼 터짐 · 그 우물 결만)."""
    out: Dict[str, Any] = {"well": None, "pull": 0.0, "b": 0.0, "tier": None}
    if not channel_id:
        return out
    wells = load(channel_id)
    if not _live_sorted(wells):
        return out
    nodes = _nodes(channel_id)
    pn = pc_node(channel_id, pc_location)
    src = node_of(channel_id, location) if str(location or "").strip() else pn
    best, pull = _pull_best(channel_id, wells, src, nodes, frozenset(), line)
    if best is None or pull < float(_cfg("WELL_P_BEND", 0.25)):
        return out
    m = float(best["mass"])
    if pn is None:
        b = 1.0
    else:
        dpc = _hops(nodes, pn)
        mode = str(_cfg("WELL_LIGHT_FROM", "emission"))
        d = dpc.get(src) if (mode == "emission" and src) else None
        if d is None:
            c = resolve_center(channel_id, best.get("center") or "", nodes)
            d = dpc.get(c) if c else None
        # [검토 #2] 거리를 모르면(빛 자리 · 중심 둘 다 못 잼) 빛을 지우지 않는다 — PC 못 풂과 같이 b = 1
        b = 1.0 if d is None else m / float((d + 1) ** 2)
    tier = "stage" if b >= float(_cfg("WELL_P_CAP", 1.0)) else ("glow" if b >= float(_cfg("WELL_P_BEND", 0.25)) else "dark")
    out.update({"well": best, "pull": pull, "b": b, "tier": tier})
    return out


def record_light(channel_id: str, well_id: int, side: str, cand: Dict[str, Any], turn: int) -> int:
    """관측 흔적 행 — 무대 위(fg) · 막후(bg). src light."""
    summ = str(cand.get("summary") or cand.get("line") or cand.get("tag") or "").strip()
    if not summ:
        return 0
    return _append(channel_id, [{"turn": turn, "well_id": int(well_id), "op": "trace", "payload": {
        "side": "fg" if side == "fg" else "bg", "summary": summ[:200], "polarity": cand.get("polarity", "mixed"),
        "intensity": cand.get("intensity", "Mid"), "src": "light"}}])


# =========================================================
# 6. 이름 얻기 · 배치 갱신 (본 스펙 §6 ⑥ · G2 스펙 §8)
# =========================================================

def naming_candidate(channel_id: str, nt_state: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
    """이름 없는 산 우물 중 M ≥ WELL_NAME_MASS · M > 거절 질량. 질량 큰 것 → 가까운 것 → 먼저 생긴 것. 한 턴에 하나."""
    nm = int(_cfg("WELL_NAME_MASS", 4))
    cands = [w for w in view(channel_id, nt_state)
             if w["well_status"] == DUST and w["mass"] >= nm and w["mass"] > w["rejected_mass"]
             and not w.get("seeded_only")]   # [G3 검토] 시드만 뭉친 우물은 시계 · 빛이 붙어야 후보(G3 §2)
    if not cands:
        return None
    cands.sort(key=lambda w: (-w["mass"], w["d"] if w["d"] is not None else 99, w["born_turn"], w["id"]))
    w = cands[0]
    seen = [e.get("summary", "") for e in (w["sensory_foreshadowing"] + w["offscreen_actions"])[-2:] if e.get("summary")]
    return {"well_id": w["id"], "mass": w["mass"], "where": w["where"] or "(unplaced)",
            "clocks": w["clocks"], "people": w["entities"], "seen": seen}


def candidate_line(cand: Optional[Dict[str, Any]]) -> str:
    if not cand:
        return "None"
    parts = [f"well #{cand.get('well_id')}: where={cand.get('where') or '?'}", f"mass={cand.get('mass')}"]
    if cand.get("clocks"):
        parts.append("clocks=" + " · ".join(str(x) for x in cand["clocks"][:4]))
    if cand.get("people"):
        parts.append("people=" + " · ".join(str(x) for x in cand["people"][:4]))
    if cand.get("seen"):
        parts.append("seen=" + " / ".join(str(x)[:60] for x in cand["seen"]))
    return ", ".join(parts)


def apply_decisions(channel_id: str, decisions: Dict[str, Any], turn: int,
                    offered_id: Optional[int] = None) -> Dict[str, Any]:
    """배치 arc_decisions → 우물 행. confirms → name · rejects → reject. well_id 가 비었거나 엉뚱하면 그 턴 낸 후보로."""
    ev: Dict[str, Any] = {"named": [], "rejected": []}
    if not channel_id or not isinstance(decisions, dict):
        return ev
    wells = load(channel_id)

    nm = int(_cfg("WELL_NAME_MASS", 4))

    def _target(item: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        # [검토 #1] 그 턴 낸 후보만 받는다 — 모델이 엉뚱한 id 를 적으면(먼지 id 는 작은 정수라 잘 맞는다) 다른 먼지가
        #   이름을 얻거나 거절됐다. 후보가 없던 턴의 결정은 버린다. 질량 문턱도 다시 본다.
        if offered_id is None:
            return None
        wid = _int(offered_id, -1)
        w = wells.get(wid)
        if w and is_live(w) and w["status"] == DUST and w["mass"] >= nm and wid not in ev["named"] \
                and wid not in ev["rejected"]:
            return w
        return None

    rows = []
    for c in decisions.get("confirms") or []:
        if not isinstance(c, dict):
            continue
        w = _target(c)
        goal = str(c.get("declared_goal") or "").strip()
        if not w or not goal:
            continue
        rows.append({"turn": turn, "well_id": w["id"], "op": "name", "label": goal[:120], "payload": {
            "phase": str(c.get("initial_phase_label") or "").strip()[:60],
            "next_waypoint": str(c.get("next_waypoint") or "").strip()[:200],
            "backstage_reality": str(c.get("backstage_reality") or "").strip()[:300]}})
        ev["named"].append(w["id"])
    for r in decisions.get("rejects") or []:
        if not isinstance(r, dict):
            continue
        w = _target(r)
        if not w:
            continue
        rows.append({"turn": turn, "well_id": w["id"], "op": "reject", "payload": {
            "mass": w["mass"], "reason": str(r.get("reason") or "")[:120]}})
        ev["rejected"].append(w["id"])
    _append(channel_id, rows)
    return ev


def apply_updates(channel_id: str, updates: List[Dict[str, Any]], turn: int) -> Dict[str, Any]:
    """배치 arc_updates → 우물 행(arc_id = 이름 얻은 산 우물 id). 옛 `apply_arc_updates` 와 같은 칸."""
    ev: Dict[str, Any] = {"phase_transitions": [], "applied": []}
    if not channel_id or not isinstance(updates, list):
        return ev
    wells = load(channel_id)
    rows = []
    for upd in updates:
        if not isinstance(upd, dict):
            continue
        w = wells.get(_int(upd.get("arc_id"), -1))
        if not w or not is_live(w) or w["status"] not in _ARCLIKE:
            continue
        wid, n0 = w["id"], len(rows)
        pt = upd.get("phase_transition")
        if isinstance(pt, dict) and pt.get("enter") and str(pt.get("label") or "").strip():
            rows.append({"turn": turn, "well_id": wid, "op": "phase", "payload": {"label": str(pt["label"]).strip()[:60]}})
            ev["phase_transitions"].append(wid)
            if w["status"] == CLIMAX and turn >= _int(w.get("climax_turn")):
                # [G3] 결전 중 장면이 넘어갔다 = 결판 — 끝(id 가 천으로 풀린다). [검토 #2] 결전 전 턴의 배치(배경 큐가
                #   다음 턴 앞단과 겹친다)가 늦게 와서 막 들어선 결전을 끝내지 않게 — 결전 턴 이후 배치만.
                rows.append({"turn": turn, "well_id": wid, "op": "end", "payload": {"reason": "resolved"}})
                ev.setdefault("resolved", []).append(wid)
        note = {}
        if isinstance(upd.get("next_waypoint_update"), str) and upd["next_waypoint_update"].strip():
            note["next_waypoint"] = upd["next_waypoint_update"].strip()[:200]
        if isinstance(upd.get("backstage_reality_update"), str) and upd["backstage_reality_update"].strip():
            note["backstage_reality"] = upd["backstage_reality_update"].strip()[:300]
        if note:
            rows.append({"turn": turn, "well_id": wid, "op": "note", "payload": note})
        for side, key in (("fg", "sensory_foreshadowing_add"), ("bg", "offscreen_actions_add")):
            for e in upd.get(key) or []:
                if isinstance(e, dict) and str(e.get("summary") or "").strip():
                    rows.append({"turn": turn, "well_id": wid, "op": "trace", "payload": {
                        "side": side, "summary": str(e["summary"]).strip()[:200],
                        "polarity": e.get("polarity", "mixed"), "intensity": e.get("intensity", "Mid"), "src": "batch"}})
        if len(rows) > n0:
            ev["applied"].append(wid)
    _append(channel_id, rows)
    return ev


# =========================================================
# 7. 옮기기 — 옛 아크 줄거리 → 우물 (본 스펙 §14)
# =========================================================

def ids_of_storyline(channel_id: str, sl: Dict[str, Any], world: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    """옛 아크를 옮길 때 쥘 id(C75 §14): entities 인물 → {key, pid, name} + 그 인물에 걸린 살아 있는 시계 +
    모자라면 옮긴 흔적 id 로 채워 질량 = max(그 개수, round(weight × N))."""
    out: List[Dict[str, Any]] = []
    names = [str(n) for n in (sl.get("entities") or []) if str(n or "").strip()]
    try:
        import wiki_store
    except Exception:
        wiki_store = None
    for nm in names:
        pid = None
        if wiki_store is not None:
            try:
                pid = wiki_store.resolve_page(channel_id, nm, "character")
            except Exception:
                pid = None
        out.append({"key": pid or f"n:{nm.strip().lower()}", "pid": pid, "name": nm, "kind": "person"})
    try:
        if world is None:
            import domain_manager
            world = domain_manager.get_world_state(channel_id) or {}
        low = {n.strip().lower() for n in names}
        for c in world.get("doom_clocks") or []:
            if isinstance(c, dict) and not c.get("resolved") and str(c.get("linked_entity") or "").strip().lower() in low:
                out.append({"key": _clock_keys(c)[0], "name": c.get("name", ""), "kind": "clock"})
    except Exception as e:
        logger.debug(f"[Well] clock ids skip: {e}")
    N = int(_cfg("WELL_MASS_LIMIT", 16))
    try:
        target = int(round(float(sl.get("weight", 0.0) or 0.0) * N))
    except (TypeError, ValueError):
        target = 0
    i = 0
    while len(out) < max(1, target):
        out.append({"key": f"M:{sl.get('id')}:{i}", "kind": "migrated"})
        i += 1
    return out


def strip_arc_fields(sl: Dict[str, Any]) -> None:
    for k in _ARC_FIELDS:
        sl.pop(k, None)


def _migrate_target(sl: Dict[str, Any]) -> int:
    """옮긴 아크의 질량 바닥 = max(round(weight × N), 이름 얻는 질량) — 이름 얻은 우물은 4 아래로 안 선다(검토 #3)."""
    N = int(_cfg("WELL_MASS_LIMIT", 16))
    try:
        t = int(round(float(sl.get("weight", 0.0) or 0.0) * N))
    except (TypeError, ValueError):
        t = 0
    return max(t, int(_cfg("WELL_NAME_MASS", 4)))


def migrate(channel_id: str, nt_state: Dict[str, Any], turn: int) -> Dict[str, Any]:
    """`is_arc` 줄거리가 있으면 한 번: 활성 아크 → 정본 우물(그림자가 있으면 그것, 없으면 새로) + name 행.
    남은 그림자 → end. 모든 아크 줄거리(잠든 것 포함)에서 아크 칸을 걷는다 — 잠든 아크는 옮기지 않는다.
    이미 옮긴 줄거리(그 storyline_id 의 산 정본 우물이 있음)가 옛 사본 저장으로 깃발을 다시 달고 와도 칸만 걷는다(검토 #6).
    질량 채우기는 겹침 **뒤**(fold 다음) — 라우터 입구 맞춤이 먼저 돌아 사람 · 시계 id 를 먼지가 쥐었어도 바닥까지(검토 #5)."""
    ev: Dict[str, Any] = {"migrated": [], "ended": []}
    if not channel_id or not isinstance(nt_state, dict):
        return ev
    sls = [s for s in (nt_state.get("storylines") or []) if isinstance(s, dict)]
    flagged = [s for s in sls if s.get("is_arc")]
    wells = load(channel_id)
    shadows = {w.get("storyline_id"): w for w in wells.values() if w["status"] == SHADOW}
    done = {w.get("storyline_id") for w in wells.values()
            if w["status"] in _ARCLIKE and w.get("storyline_id") is not None}   # [G3 검토 #7] 결전 중도 옮긴 것
    if not flagged and not shadows:
        return ev
    pn = pc_node(channel_id)
    world = None
    rows: List[Dict[str, Any]] = []
    nid = _next_id(wells, sls)
    targets: Dict[int, Tuple[Any, int]] = {}
    for sl in [s for s in flagged if s.get("status") == "active"]:
        sid = sl.get("id")
        if sid in done:
            continue
        w = shadows.pop(sid, None)
        if w is not None:
            wid = w["id"]
            if not w.get("center") and pn:
                rows.append({"turn": turn, "well_id": wid, "op": "center", "center": pn})
        else:
            if world is None:
                try:
                    import domain_manager
                    world = domain_manager.get_world_state(channel_id) or {}
                except Exception:
                    world = {}
            wid = nid
            nid += 1
            rows.append({"turn": turn, "well_id": wid, "op": "birth", "origin": "migrated", "storyline_id": sid,
                         "center": center_of_storyline(channel_id, sl) or pn or "",
                         "label": str(sl.get("declared_goal") or "")[:120]})
            rows.append({"turn": turn, "well_id": wid, "op": "capture", "ids": ids_of_storyline(channel_id, sl, world)})
        rows.append({"turn": turn, "well_id": wid, "op": "name", "label": str(sl.get("declared_goal") or "")[:120] or "(migrated)",
                     "payload": {"phases": list(sl.get("phases") or []),
                                 "next_waypoint": str(sl.get("next_waypoint") or ""),
                                 "backstage_reality": str(sl.get("backstage_reality") or ""),
                                 "armed": bool(sl.get("armed")), "category": str(sl.get("origin_category") or ""),
                                 "fg": list(sl.get("sensory_foreshadowing") or []),
                                 "bg": list(sl.get("offscreen_actions") or [])}})
        targets[wid] = (sid, _migrate_target(sl))
        ev["migrated"].append(wid)
    for w in shadows.values():
        rows.append({"turn": turn, "well_id": w["id"], "op": "end", "payload": {"reason": "shadow_gone"}})
        ev["ended"].append(w["id"])
    _append(channel_id, rows)
    if targets:
        wells = load(channel_id)
        pad = []
        for wid, (sid, target) in targets.items():
            w = wells.get(wid)
            if not w or w["mass"] >= target:
                continue
            have = {str(i.get("key")) for i in w["ids"]}
            extra, j = [], 0
            while w["mass"] + len(extra) < target:
                k = f"M:{sid}:{wid}:p{j}"
                j += 1
                if k not in have:
                    extra.append({"key": k, "kind": "migrated"})
            pad.append({"turn": turn, "well_id": wid, "op": "capture", "ids": extra})
        _append(channel_id, pad)
    for sl in sls:
        strip_arc_fields(sl)
    if ev["migrated"] or ev["ended"]:
        logger.info("[Well] migrate t=%s migrated=%s ended=%s", turn, ev["migrated"], ev["ended"])
    return ev


# =========================================================
# 8. 증발 · 중심 사라짐 · tick
# =========================================================

def _ending_rows(channel_id: str, wells: Dict[int, Dict[str, Any]], clocks: List[Dict[str, Any]], turn: int,
                 nodes: Dict[str, Any]) -> List[Dict[str, Any]]:
    """호킹(τ = k · M³, 산 시계를 쥔 우물은 안 증발 · 칸이 차면 활동) · 중심 노드 사라짐 · 빈 우물."""
    rows: List[Dict[str, Any]] = []
    k = float(_cfg("WELL_HAWKING_K", 4))
    by_key: Dict[str, Dict[str, Any]] = {}
    live_keys: set = set()
    first_t = _first_by_name(clocks)
    for c in clocks or []:
        if not isinstance(c, dict) or not c.get("name"):
            continue
        bkey, legacy = _clock_keys(c)
        keys_c = [bkey] + ([legacy] if _int(c.get("turn_created")) == first_t.get(str(c["name"])) else [])
        for key in keys_c:
            by_key[key] = c
            if not c.get("resolved"):
                live_keys.add(key)
    for w in wells.values():
        if w["status"] not in (DUST, NAMED):
            continue
        if int(w.get("mass", 0) or 0) <= 0:
            rows.append({"turn": turn, "well_id": w["id"], "op": "end", "payload": {"reason": "void"}})
            continue
        if w.get("center") and nodes and resolve_center(channel_id, w["center"], nodes) is None:
            rows.append({"turn": turn, "well_id": w["id"], "op": "end", "payload": {"reason": "center_gone"}})
            continue
        keys = {str(i.get("key") or "") for i in w["ids"]}
        if keys & live_keys:
            continue
        last = _int(w.get("last_capture_turn"))
        for key in keys:
            c = by_key.get(key)
            if c is not None:
                # 칸이 찬 턴 · 끝난 턴도 활동 — 먼지는 시계가 끝나고 4턴(검토 #7)
                last = max(last, _int(c.get("last_progress_turn")), _int(c.get("end_turn")))
        if turn - last >= k * (int(w["mass"]) ** 3):
            rows.append({"turn": turn, "well_id": w["id"], "op": "end", "payload": {"reason": "evaporated"}})
    return rows


def tick(channel_id: str, nt_state: Dict[str, Any], turn: int, ctx: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """배경 한 턴(옛 그림자 tick + tick_arcs 자리): 옮기기 → 시계 맞춤 → 증발 · 중심 사라짐 → 그리기 캐시.
    캐시(`nt_state["well_cache"]`)는 이 함수만 쓴다 — 줄거리 저장(W1-7) 바로 앞에서 불린다."""
    ev: Dict[str, Any] = {}
    if not channel_id or not isinstance(nt_state, dict):
        return ev
    ctx = ctx or {}
    ev["migrate"] = migrate(channel_id, nt_state, turn)
    try:
        import domain_manager
        world = domain_manager.get_world_state(channel_id) or {}
    except Exception:
        world = {}
    clocks = world.get("doom_clocks") or []
    ev["clocks"] = reconcile_clocks(channel_id, clocks, turn, ctx.get("current_location"))
    ev["seeds"] = reconcile_seeds(channel_id, None, turn)          # [G3]
    ev["goals"] = reconcile_goals(channel_id, turn, ctx.get("current_location"))   # [G3]
    wells = load(channel_id)
    end_rows = _ending_rows(channel_id, wells, clocks, turn, _nodes(channel_id))
    _append(channel_id, end_rows)
    ev["ended"] = [(r["well_id"], r["payload"]["reason"]) for r in end_rows]
    # 그리기 캐시 — lerp(옛 compute_pacing_mode) · 지난 잰 근접(PC 못 풀 때 깎을 값)
    cache_old = nt_state.get("well_cache") if isinstance(nt_state.get("well_cache"), dict) else {}
    cache: Dict[str, Any] = {}
    pn = pc_node(channel_id, ctx.get("current_location"))
    try:
        import narrative_tracker as _nt
        _pace = _nt.compute_pacing_mode
    except Exception:
        _pace = None
    for w in view(channel_id, nt_state, ctx.get("current_location"), turn):
        cw = dict(cache_old.get(str(w["id"])) or {})
        if pn is not None:
            cw["prox"] = round(float(w["proximity"]), 4)
            cw["turn"] = int(turn)
        if w["well_status"] in _ARCLIKE and _pace is not None:
            cw["pacing"] = round(float(_pace({"proximity": w["proximity"], "pacing": float(cw.get("pacing", 0.3) or 0.3)},
                                             ctx.get("scene_type", "normal"), ctx.get("doom_phase", ""), False)), 4)
        cache[str(w["id"])] = cw
    nt_state["well_cache"] = cache
    if ev["ended"]:
        logger.info("[Well] t=%s ended=%s", turn, ev["ended"])
    return ev


# =========================================================
# 8b. [G3] 시드 = 첫 질량 · 목표 휨 기록 · 앞단(결전 · 충돌파)
# =========================================================

def _seed_anchor(channel_id: str, anchor: Any) -> Tuple[Optional[str], List[Dict[str, Any]]]:
    """시드 닻 → (노드, 닻 id 칸). 장소면 정확 · 별칭, 사람이면 거처. 못 풀면 (None, [])."""
    a = str(anchor or "").strip()
    if not a:
        return None, []
    try:
        import world_tree
        nid = world_tree.resolve_node_id(channel_id, a, allow_token=False)
        if nid:
            return nid, [{"key": f"P:{nid}", "name": a, "kind": "anchor"}]
        nid = world_tree.home_node(channel_id, a)
        if nid:
            ids = entity_ids(channel_id, a) or [{"key": f"A:{a.lower()}", "name": a, "kind": "anchor"}]
            return nid, ids
    except Exception as e:
        logger.debug(f"[Well] seed anchor skip: {e}")
    return None, []


def _gone_only(wells: Dict[int, Dict[str, Any]]) -> set:
    """[G3 검토] 시드 목록에서 빠져 `seed_gone` 으로만 끝난 시드 key — 목록에 돌아오면 다시 선다(증발 · 무너짐 · 결판과
    달리 그 흐름이 자기 일로 꺼진 게 아니다: `!로어` 재실행이 빠뜨렸거나 독자 시드 FIFO 가 밀어냈다)."""
    st: Dict[str, bool] = {}
    for w in wells.values():
        for i in w.get("ids") or []:
            k = str(i.get("key") or "")
            if not k.startswith("S:"):
                continue
            ok = w["status"] == ENDED and w.get("end_reason") == "seed_gone"
            st[k] = st.get(k, True) and ok
    return {k for k, v in st.items() if v}


def _seed_dicts(lsd: Optional[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return [x for x in ((lsd or {}).get("anomaly_seeds") or []) if isinstance(x, dict) and str(x.get("name") or "").strip()]


def reconcile_seeds(channel_id: str, lsd: Optional[Dict[str, Any]] = None, turn: Optional[int] = None) -> Dict[str, Any]:
    """[G3] 로어 · 독자 시드 ↔ 우물 맞춤(멱등, 본 스펙 §10 · G3 §2). 닻을 푼 시드마다 key `S:<이름>` 을 본 적 없으면
    강착 — ids = [시드, 닻] → 원시 질량 2(같은 자리 시드는 먼저 선 우물에 떨어져 뭉친다). 증발해 본 적 있는 시드는
    다시 안 선다(안티체호프). 시드 목록에서 사라진 시드만 쥔 우물(시계 0)은 `end seed_gone`."""
    ev: Dict[str, Any] = {"born": [], "gone": []}
    if not channel_id:
        return ev
    if lsd is None:
        try:
            import domain_manager
            lsd = domain_manager.get_lore_summary_data(channel_id) or {}
        except Exception:
            lsd = {}
    if turn is None:
        try:
            import domain_manager
            turn = _int((domain_manager.get_world_state(channel_id) or {}).get("turn_index"))
        except Exception:
            turn = 0
    seeds = _seed_dicts(lsd)
    names = {str(x["name"]).strip() for x in seeds}
    seen: set = set()
    wells = load(channel_id, seen)
    nodes = _nodes(channel_id)
    back = _gone_only(wells)
    for sd in seeds:
        nm = str(sd["name"]).strip()
        key = f"S:{nm}"
        if key in seen and key not in back:
            continue
        node, aid = _seed_anchor(channel_id, sd.get("anchor"))
        if not node:
            continue
        aid = [dict(i, via="seed") for i in aid]
        _best, _pull = _pull_best(channel_id, wells, node, nodes)
        if _best is None or _pull < float(_cfg("WELL_P_CAP", 1.0)):
            # [G3 검토 #5] 새 우물로 설 때 닻(사람 page id)을 이미 다른 산 우물이 쥐었으면 그 칸은 빈다 → 질량 1 → 4턴에
            #   증발. 그 칸만 이 시드 고유 닻 칸으로 — 원시 질량 2 를 지킨다.
            _owned = {str(i.get("key") or "") for _w in _live_sorted(wells) for i in _w["ids"]}
            aid = [i if str(i.get("key") or "") not in _owned
                   else {"key": f"A:{nm}", "name": i.get("name") or "", "kind": "anchor", "via": "seed"} for i in aid]
        wid = accrete(channel_id, node, [{"key": key, "name": nm, "kind": "seed"}] + aid, turn, wells, nodes,
                      origin="seed")
        ev["born"].append((nm, wid))
        seen = set()
        wells = load(channel_id, seen)
    rows = []
    for w in _live_sorted(wells):
        sk = [str(i.get("key"))[2:] for i in w["ids"] if str(i.get("key") or "").startswith("S:")]
        if not sk or any(i.get("kind") in ("clock", "clock_fire") for i in w["ids"]) or w["status"] in _ARCLIKE:
            continue
        if not any(n in names for n in sk):
            rows.append({"turn": turn, "well_id": w["id"], "op": "end", "payload": {"reason": "seed_gone"}})
            ev["gone"].append(w["id"])
    _append(channel_id, rows)
    if ev["born"] or ev["gone"]:
        logger.info("[Well] seeds t=%s born=%s gone=%s", turn, ev["born"], ev["gone"])
    return ev


def seed_view(channel_id: str, seeds: List[Any], pc_location: Any = None) -> Dict[str, List[Any]]:
    """[G3] Theoria 씨앗 줄 — {"visible": [시드], "far": [시드], "free": [시드]}. 보임 = 그 시드를 쥔 산 우물의 PC 자리 밝기
    ≥ P_bend · 멀다 = 그 밖 · free = 닻을 못 풀어 우물이 없는 시드(옛 그대로 늘 실림). 증발한(본 적 있는데 산 우물이
    없는) 시드는 어디에도 안 든다."""
    out: Dict[str, List[Any]] = {"visible": [], "far": [], "free": []}
    seen: set = set()
    wells = load(channel_id, seen) if channel_id else {}
    nodes = _nodes(channel_id) if channel_id else {}
    back = _gone_only(wells)
    pb = float(_cfg("WELL_P_BEND", 0.25))
    for sd in seeds or []:
        nm = str((sd.get("name") if isinstance(sd, dict) else sd) or "").strip()
        if not nm:
            continue
        key = f"S:{nm}"
        ow = _owner_of(wells, key)
        if ow is not None:
            b = _center_brightness(channel_id, ow, pc_location, nodes)
            (out["visible"] if (b is None or b >= pb) else out["far"]).append(sd)
        elif key in seen and key not in back:
            continue
        else:
            out["free"].append(sd)
    return out


def goal_places(channel_id: str, st: Dict[str, Any], pc_location: Any = None) -> List[str]:
    """[C75 §11] 목표 자리 — 당사자(`parties`)들의 거처 노드. 하나도 안 풀리면 PC 노드. 못 풀면 []."""
    pos: List[str] = []
    try:
        import world_tree
    except Exception:
        world_tree = None
    for pn_ in st.get("parties") or []:
        try:
            nd = world_tree.home_node(channel_id, pn_) if world_tree else None
        except Exception:
            nd = None
        if nd and nd not in pos:
            pos.append(nd)
    if not pos:
        nd = pc_node(channel_id, pc_location)
        if nd:
            pos.append(nd)
    return pos


def goal_bend(channel_id: str, st: Dict[str, Any], wells: List[Dict[str, Any]],
              nodes: Optional[Dict[str, Any]] = None, pc_location: Any = None,
              places: Optional[List[str]] = None) -> Optional[Tuple[Dict[str, Any], float]]:
    """[C75 §11 · G4] 목표가 휘는 우물 — 목표 자리(`goal_places`)에서 우물마다 속도 M ÷ (r+1)(r = 그 자리들 중 중심에
    가장 가까운 홉), 가장 큰 값 ≥ WELL_GOAL_BEND 면 (그 우물, 속도). 동점은 먼저 온 우물(목록 순서 — 먼저 생긴 순으로
    넘긴다). 붙는 게 아니라 잴 때마다 그 자리 값. 쓰는 곳 둘: 목표 휨 기록(`reconcile_goals`, 산 우물 전부) ·
    진행 중 블록(`in_motion`, 이름 얻은 우물만)."""
    if not wells:
        return None
    if nodes is None:
        nodes = _nodes(channel_id)
    pos = places if places is not None else goal_places(channel_id, st, pc_location)
    if not pos:
        return None
    dists = [_hops(nodes, p_) for p_ in pos]
    best, best_v = None, 0.0
    for w in wells:
        c = resolve_center(channel_id, w.get("center") or "", nodes)
        rs = [d.get(c) for d in dists if c and d.get(c) is not None]
        if not rs:
            continue
        v = float(w.get("mass") or 0) / float(min(rs) + 1)
        if v > best_v:
            best, best_v = w, v
    if best is not None and best_v >= float(_cfg("WELL_GOAL_BEND", 2.0)):
        return best, best_v
    return None


def arc_line(channel_id: str, pc_location: Any = None, limit: int = 3) -> str:
    """[C74 판4 · G4 · 판정 6] 한 줄 아크 — 이름 얻은 산 우물, 가까운 순 최대 limit, `이름(지나온 마지막 장면 이름)` 을
    ` / ` 로. 장면이 없으면 이름만. 없으면 ''. 옛 배치 추출 `current_arc` 칸의 자리(Theoria 4c · 루카가 읽을 때 부른다)."""
    if not channel_id:
        return ""
    try:
        aa = sorted((a for a in arcs(channel_id, None, pc_location) if in_sight(a)),
                    key=lambda a: (not a.get("climax"), -float(a.get("proximity") or 0), -int(a.get("mass") or 0), a["id"]))
    except Exception:
        return ""
    bits = []
    for a in aa[: max(0, int(limit))]:
        nm = str(a.get("declared_goal") or a.get("where") or "").strip()
        if not nm:
            continue
        ph = [p for p in (a.get("phases") or []) if str(p or "").strip()]
        bits.append(f"{nm}({ph[-1]})" if ph else nm)
    return " / ".join(bits)


def reconcile_goals(channel_id: str, turn: int, pc_location: Any = None) -> Dict[str, Any]:
    """[G3] 목표 휨 기록(본 스펙 §11 · 성장 셈의 재료) — 장부에서 done 으로 닫힌 지 창 안의 목표마다 한 번: 목표 자리 =
    당사자들의 거처(못 풀면 PC 노드)에서 우물마다 속도 M ÷ (r+1), 가장 큰 값 ≥ WELL_GOAL_BEND 면 그 우물에 `grow` 행.
    붙는 게 아니라 끝난 그 턴 재 본 결과다."""
    ev: Dict[str, Any] = {"grown": []}
    if not channel_id:
        return ev
    try:
        import thread_ledger
        states = thread_ledger.load_states(channel_id) or {}
    except Exception:
        return ev
    window = int(_cfg("WELL_RECONCILE_WINDOW", 3))
    wells = load(channel_id)
    done = {g.get("key") for w in wells.values() for g in (w.get("grown") or [])}
    live = _live_sorted(wells)
    if not live:
        return ev
    nodes = _nodes(channel_id)
    rows = []
    for tid, st in states.items():
        if st.get("kind") != "goal" or st.get("status") != "closed" or st.get("outcome") != "done":
            continue
        t_end = _int(st.get("last_turn"))
        if turn - t_end > window:
            continue
        key = f"G:{tid}"   # [G3 검토] 다시 열려 또 닫혀도 한 번(장부도 한 번 갚는다)
        if key in done:
            continue
        hit = goal_bend(channel_id, st, live, nodes, pc_location)   # [C74 판4] 셈 한 벌 — 진행 중 블록과 같은 함수
        if hit is not None:
            best = hit[0]
            rows.append({"turn": turn, "well_id": best["id"], "op": "grow",
                         "payload": {"key": key, "goal": str(st.get("title") or "")[:80]}})
            done.add(key)
            ev["grown"].append((tid, best["id"]))
    _append(channel_id, rows)
    return ev


def _chain(nodes: Dict[str, Any], nid: Optional[str]) -> List[str]:
    """노드와 그 조상들(부모 사슬) — '중심 안' 판정용."""
    out: List[str] = []
    cur = nid
    while cur and cur in nodes and cur not in out:
        out.append(cur)
        cur = str((nodes.get(cur) or {}).get("parent_id") or "")
    return out


def _well_name(w: Dict[str, Any]) -> str:
    return str(w.get("declared_goal") or w.get("where") or f"#{w.get('id')}")


def collision_title(a: Dict[str, Any], b: Dict[str, Any]) -> str:
    """[G3 검토 #3] 채널 시스템 줄용 — 기계 태그(collision:3-7) 대신 두 흐름 이름."""
    return f"{_well_name(a)} × {_well_name(b)}"


def collision_line(a: Dict[str, Any], b: Dict[str, Any]) -> str:
    na, nb = _well_name(a), _well_name(b)
    return (f"a tremor runs through the whole world: {na} and {nb} have met — "
            f"word of the collision reaches everywhere.")


def front(channel_id: str, pc_location: Any = None, turn: Optional[int] = None) -> Dict[str, Any]:
    """[G3] 앞단 한 번(이변 모듈 입구 — 매 턴 렌더 앞): 시계 맞춤 → 결전 들어섬 / 떠남 → 충돌파 감지.
    충돌은 행만 쓰고 돌려준다 — 대기열 항목은 부른 쪽(이변 모듈)이 그 턴 사본에 넣는다(배경이 따로 쓰면 앞단 저장이 덮는다)."""
    ev: Dict[str, Any] = {"climax": [], "resolved": [], "collisions": []}
    if not channel_id:
        return ev
    if turn is None:
        try:
            import domain_manager
            turn = _int((domain_manager.get_world_state(channel_id) or {}).get("turn_index"))
        except Exception:
            turn = 0
    ev["clocks"] = reconcile_clocks(channel_id, None, turn, pc_location)
    nodes = _nodes(channel_id)
    pn = pc_node(channel_id, pc_location)
    vw = view(channel_id, None, pc_location, turn)
    rows: List[Dict[str, Any]] = []
    # [G3 검토 #4] 결전 '안' = 중심이 PC 자리 자신이거나 조상이면서 그 사이 홉 ≤ R(M) — 나라 · 도시처럼 큰 자리를 중심으로 쥔
    #   우물이 PC 가 그 안 어디에 있든 결전을 영영 붙들지 않게. 그리고 결전은 WELL_CLIMAX_MAX 턴을 넘기면 결판(배치가
    #   장면 넘어감을 못 냈어도).
    chain = _chain(nodes, pn) if pn else []
    cap = int(_cfg("WELL_CLIMAX_MAX", 12))
    for w in vw:
        if w["well_status"] == CLIMAX and cap > 0 and turn - _int(w.get("climax_turn")) >= cap:
            rows.append({"turn": turn, "well_id": w["id"], "op": "end", "payload": {"reason": "resolved"}})
            ev["resolved"].append(w["id"])
            continue
        if not pn:
            continue
        up = chain.index(w["center"]) if (w["center"] and w["center"] in chain) else None
        inside = up is not None and up <= max(0, reach(int(w["mass"])))
        if w["well_status"] == NAMED and w["ripe"] and inside:
            rows.append({"turn": turn, "well_id": w["id"], "op": "climax"})
            ev["climax"].append(w["id"])
        elif w["well_status"] == CLIMAX and not inside:
            rows.append({"turn": turn, "well_id": w["id"], "op": "end", "payload": {"reason": "resolved"}})
            ev["resolved"].append(w["id"])
    # 충돌파 — 둘 다 M ≥ WELL_COLLIDE_MASS · 두 중심 홉 ≤ R₁ + R₂ · 한 쌍에 한 번(둘 다 regrow 이상 더 먹으면 다시)
    cm = int(_cfg("WELL_COLLIDE_MASS", 9))
    rg = int(_cfg("WELL_COLLIDE_REGROW", 4))
    big = [w for w in vw if w["mass"] >= cm and w["center"]]
    if len(big) >= 2:
        raw = load(channel_id)
        for i in range(len(big)):
            di = _hops(nodes, big[i]["center"])
            for j in range(i + 1, len(big)):
                a, b = big[i], big[j]
                d = di.get(b["center"])
                if d is None or d > reach(a["mass"]) + reach(b["mass"]):
                    continue
                prev = (raw.get(a["id"]) or {}).get("collisions", {}).get(b["id"])
                if prev and not (a["mass"] - prev[0] >= rg and b["mass"] - prev[1] >= rg):
                    continue
                rows.append({"turn": turn, "well_id": a["id"], "op": "collide",
                             "payload": {"other": b["id"], "mass": a["mass"], "other_mass": b["mass"]}})
                rows.append({"turn": turn, "well_id": b["id"], "op": "collide",
                             "payload": {"other": a["id"], "mass": b["mass"], "other_mass": a["mass"]}})
                ev["collisions"].append({"a": a["id"], "b": b["id"], "line": collision_line(a, b),
                                         "title": collision_title(a, b)})
    _append(channel_id, rows)
    if ev["climax"] or ev["resolved"] or ev["collisions"]:
        logger.info("[Well] front t=%s climax=%s resolved=%s collisions=%s", turn, ev["climax"], ev["resolved"],
                    [(c["a"], c["b"]) for c in ev["collisions"]])
    return ev


# =========================================================
# 9. 값 — 잴 때마다 (C75 §5)
# =========================================================

def proximity(d: Optional[int]) -> float:
    return 0.0 if d is None else 1.0 / (int(d) + 1)


def weight(mass: int) -> float:
    return min(1.0, float(mass) / max(1, int(_cfg("WELL_MASS_LIMIT", 16))))


def speed(mass: int, d: Optional[int]) -> float:
    return 0.0 if d is None else float(mass) / (int(d) + 1)


def brightness(mass: int, d: Optional[int]) -> float:
    return 0.0 if d is None else float(mass) / float((int(d) + 1) ** 2)


def reach(mass: int, p_cap: float = 1.0) -> int:
    """닿는 거리 R = ⌊√(M ÷ P_cap)⌋ − 1 (질량 1 → 0칸 · 4 → 1 · 9 → 2 · 16 → 3)."""
    if mass <= 0:
        return -1
    return int(math.isqrt(int(mass / p_cap))) - 1
