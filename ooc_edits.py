# -*- coding: utf-8 -*-
"""[2026-10-10 OOC 확장] OOC 편집 콜의 인물·자리·시간 칸을 적용한다. (+ 기록 정정 fact, 이야기 실 thread·quest)

명령어(`!npc 병합·별칭·상태·시트`, `!시간 설정`)가 쓰는 함수를 그대로 부른다 — 새 콜 0, 새 관문 0.
OOC 는 플레이어가 직접 한 말이라 명령어와 같은 권위다. 다만 편집 콜(LLM)이 말을 칸으로 옮기므로
되돌릴 수 없는 일(병합)에는 가드를 둔다.
되비침: 줄 목록. 실패 줄은 ⚠ 로 시작한다(run_ooc_edit 이 적용 수에서 뺀다).
근거: 파티쳇수정/state_v10/OOC확장_스펙_2026-10-10.md
"""
import logging
import re
from typing import Any, Dict, List, Optional, Tuple

import config
import domain_manager

logger = logging.getLogger(__name__)

NPC_OPS = ("merge", "alias", "rename", "status", "sheet", "knows")
# 편집 콜이 지어내기 쉬운 op → 그 일을 하는 곳
_NPC_OP_HINT = {
    "delete": "삭제는 `!npc 삭제 [이름]`으로", "remove": "삭제는 `!npc 삭제 [이름]`으로",
    "kill": "죽음은 op status, value dead 로", "move": "자리는 field place 로",
    "relation": "관계는 field relation 으로",
}
_HERE = ("here", "여기", "이곳", "current", "")


def _norm(s: Any) -> str:
    return " ".join(str(s or "").split()).lower()


def _turn(channel_id: str) -> int:
    try:
        return int((domain_manager.get_world_state(channel_id) or {}).get("turn_index", 0) or 0)
    except Exception:
        return 0


def _op_of(e: Dict[str, Any], ops: tuple) -> str:
    """op 칸. 편집 콜은 op 를 `action` 칸에 적기도 한다(재기: merge·rename·status 를 action 으로) —
    action 이 op 목록에 있는 말이면 op 로 읽는다. sheet·knows 의 action(set·add…)은 op 목록에 없어서 섞이지 않는다."""
    op = str(e.get("op") or "").strip().lower()
    if not op:
        act = str(e.get("action") or "").strip().lower()
        if act in ops:
            op = act
    return op


def _resolve(npcs: Dict[str, Any], raw: Any) -> Optional[str]:
    raw = str(raw or "").strip()
    if not raw:
        return None
    try:
        return domain_manager.find_equivalent_npc_key(npcs, raw)
    except Exception:
        return domain_manager._find_npc_key(npcs, raw)


# =========================================================
# 인물 (npc)
# =========================================================
def merge_pair(channel_id: str, dup_raw: str, canon_raw: str, pc_masks: Any = None) -> str:
    """같은 사람 합치기. 한쪽만 시트·로어 인물이면 그쪽을 본체로 돌린다. 둘 다면 거부(`!npc 병합` 안내)."""
    import npc_manager as npm
    npcs = npm.get_npcs(channel_id) or {}
    pcm = pc_masks if pc_masks is not None else domain_manager.pc_registry(channel_id)
    d, c = _resolve(npcs, dup_raw), _resolve(npcs, canon_raw)
    if not d or not c:
        return f"⚠ 인물: '{dup_raw if not d else canon_raw}' — 명부에 없는 이름이야"
    if d == c:
        return f"⚠ 인물: {d} — 이미 같은 사람으로 묶여 있어"
    if domain_manager.is_pc_name(d, pcm) or domain_manager.is_pc_name(c, pcm):
        return "⚠ 인물: PC는 NPC와 합칠 수 없어"
    a_d = npm.is_authored(npcs.get(d) or {}, channel_id, d)
    a_c = npm.is_authored(npcs.get(c) or {}, channel_id, c)
    if a_d and a_c:
        return f"⚠ 인물: {d}·{c} 둘 다 시트 인물이라 여기선 안 합쳐 — `!npc 병합 {d} {c}`"
    flip = ""
    if a_d and not a_c:
        d, c = c, d
        flip = " (시트 인물 쪽을 본체로 뒀어)"
    ok, msg = npm.merge_identity(channel_id, d, c, via="ooc", turn=_turn(channel_id))
    if not ok:
        return f"⚠ 인물: 합치기 실패 — {msg}"
    return f"인물: {d} → {c} 같은 사람으로 합침{flip}"


def _sheet_edit(channel_id: str, key: str, section: str, action: str, body: str) -> str:
    """NPC 페이지 lore 절 한 칸 — `!npc 시트 [이름] [절] set|append|remove` 와 같은 길(편집 = manual 승격)."""
    import wiki_store
    sections = tuple(getattr(config, "WIKI_LORE_SECTIONS", {}).get("character", ()))
    section = str(section or "").strip()
    if section not in sections:
        return f"⚠ 시트: {key} — 절 이름 '{section}'이 없어({', '.join(sections)})"
    action = str(action or "set").strip().lower()
    if action not in ("set", "append", "remove"):
        return f"⚠ 시트: {key} — '{action}'은 못 해(set·append·remove)"
    if action != "remove" and not str(body or "").strip():
        return f"⚠ 시트: {key} {section} — 쓸 내용이 없어"
    pid = wiki_store.page_id_for("character", key)
    t = _turn(channel_id)
    if not wiki_store.get_page(channel_id, pid):
        wiki_store.ensure_page(channel_id, "character", key, source="manual", turn=t)
    if not wiki_store.edit_lore_section(channel_id, pid, section, str(body or ""), action=action, turn=t):
        return f"⚠ 시트: {key} {section} — 쓰지 못했어"
    wiki_store.ensure_page(channel_id, "character", key, source="manual", turn=t)
    return f"시트: {key} {section} {action} (작가 시트로 승격)"


def _knows_edit(channel_id: str, key: str, action: str, value: Any) -> str:
    """NPC 가 아는 것 — add 는 기존 병합 관문(update_npc_knowledge), remove 는 그 줄만 뺀다.
    remove 대조: 정규화 일치 → 없으면 한쪽이 다른 쪽을 품는 줄이 **딱 하나**일 때만. 둘 이상이면 거부."""
    text = str(value or "").strip()
    if not text:
        return f"⚠ 지식: {key} — 내용이 없어"
    action = str(action or "add").strip().lower()
    if action in ("add", "set", "append"):
        domain_manager.update_npc_knowledge(channel_id, key, {"knows": [text]})
        return f"지식: {key} 이제 앎 — {text[:60]}"
    if action != "remove":
        return f"⚠ 지식: {key} — '{action}'은 못 해(add·remove)"
    d = domain_manager.get_domain(channel_id)
    # [2026-10-12 C32] 지식은 마음 키에 산다 — 보이는 이름(몸)을 한 번 돌린다. 빈 몸이면 지울 것이 없다.
    _mkey = domain_manager._mind(channel_id, key)
    if not _mkey:
        return f"⚠ 지식: {key} — 지금 그 몸엔 마음이 없어"
    kn = (d.get("npc_knowledge") or {}).get(_mkey)
    items = list((kn or {}).get("knows") or [])
    q = _norm(text)
    hit = [i for i, it in enumerate(items) if _norm(it) == q]
    if not hit:
        hit = [i for i, it in enumerate(items) if q and (q in _norm(it) or _norm(it) in q)]
    if not hit:
        return f"⚠ 지식: {key} — '{text[:40]}'에 맞는 기록이 없어"
    if len(hit) > 1:
        return f"⚠ 지식: {key} — '{text[:40]}'에 맞는 줄이 {len(hit)}개라 못 골라"
    gone = items.pop(hit[0])
    kn["knows"] = items
    d["npc_knowledge"][_mkey] = kn
    domain_manager.save_domain(channel_id, d)
    try:
        domain_manager._mirror_knowledge(channel_id, _mkey, kn)
    except Exception as _e_mk:
        logger.debug(f"[OOC] 지식 미러 skip: {_e_mk}")
    return f"지식: {key} 모름으로 — {str(gone)[:60]}"


def apply_npc_edits(channel_id: str, uid: str, edits: List[Dict[str, Any]]) -> List[str]:
    """field="npc" 편집 → 되비침 줄. op = merge·alias·rename·status·sheet·knows."""
    import npc_manager as npm
    out: List[str] = []
    pcm = domain_manager.pc_registry(channel_id)
    for e in edits or []:
        if not isinstance(e, dict):
            continue
        op = _op_of(e, NPC_OPS)
        raw = domain_manager._ooc_edit_npc_name(e)
        if op not in NPC_OPS:
            out.append(f"⚠ 인물: '{op or '?'}'은 여기서 못 해" + (f" — {_NPC_OP_HINT[op]}" if op in _NPC_OP_HINT else ""))
            continue
        if not raw:
            out.append(f"⚠ 인물({op}): 이름이 없어")
            continue
        if op == "merge":
            into = str(e.get("into") or e.get("canon") or "").strip()
            if not into and isinstance(e.get("value"), str):
                into = e["value"].strip()
            out.append(merge_pair(channel_id, raw, into, pcm) if into else f"⚠ 인물: {raw} — 누구와 같은 사람인지가 없어")
            continue
        npcs = npm.get_npcs(channel_id) or {}
        key = _resolve(npcs, raw)
        if not key or domain_manager.is_pc_name(key, pcm):
            out.append(f"⚠ 인물: '{raw}' — 명부에 없는 이름이야")
            continue
        val = e.get("value")
        if op == "alias":
            alias = str(val or "").strip()
            if not alias:
                out.append(f"⚠ 별칭: {key} — 별칭이 없어")
                continue
            ok, who = domain_manager.add_npc_alias(channel_id, key, alias)
            if ok:
                out.append(f"별칭: {key} = {alias}")
            elif who and who != key:
                out.append(f"⚠ 별칭: '{alias}'은 이미 {who}의 이름이야 — 같은 사람이면 합치기로")
            else:
                out.append(f"⚠ 별칭: {key} — '{alias}'을 붙이지 못했어")
        elif op == "rename":
            new = str(val or "").strip()
            if not new:
                out.append(f"⚠ 이름: {key} — 새 이름이 없어")
                continue
            if domain_manager.is_pc_name(new, pcm):
                out.append(f"⚠ 이름: '{new}'은 PC 이름이야")
                continue
            _tgt = _resolve(npcs, new)
            if _tgt and _tgt == key:
                # [2026-10-16 C44] 새 이름이 이 인물 자신(옛 이름·별칭)으로 풀린다 = 합치기가 아니라 이름 바꾸기.
                #   옛: merge_pair(key, key) → "이미 같은 사람으로 묶여 있어"로 막혀 잘못된 개명을 되돌릴 길이 없었다.
                out.append(npm.swap_npc_name(channel_id, key, new, reason="ooc")[1])
                continue
            if _tgt:
                # [2026-10-16 C44 R4] 다른 인물 이름으로 개명 = 되묻기(레티어스). 옛: 바로 합치기 — OOC 오독이면 두 사람이
                #   합쳐지고 가르는 길이 없다. 합치기는 merge op(같은 사람이라는 말)로만 일어난다.
                out.append(f"⚠ 이름: '{new}'는 명부에 이미 있는 다른 인물이야 — 같은 사람이면 \"{key}랑 {_tgt} 합쳐줘\"로 말해줘")
                continue
            res = str(npm.handle_identity_reveal(channel_id, key, new, reason="ooc") or "")
            out.append(f"⚠ 이름: {key} — {res.lstrip('⚠️ ')}" if res.startswith("⚠") else f"이름: {key} → {new}")
        elif op == "status":
            st = str(val or "").strip().lower()
            if st not in getattr(config, "NPC_STATUS_VALUES", ("active",)):
                out.append(f"⚠ 생사: {key} — '{st}'은 없는 상태야(active·down·dead)")
                continue
            before = npm.get_npc_status(npcs.get(key) or {})
            res = npm.set_npc_status_gated(channel_id, key, st, source="manual", evidence="ooc")
            if res == "accepted":
                out.append(f"생사: {key} {before} → {st}")
            elif res == "unchanged":
                out.append(f"생사: {key} 이미 {st}")
            else:
                out.append(f"⚠ 생사: {key} — 바꾸지 못했어({res})")
        elif op == "sheet":
            out.append(_sheet_edit(channel_id, key, e.get("section") or "", e.get("action") or "set",
                                   val if isinstance(val, str) else ""))
        elif op == "knows":
            out.append(_knows_edit(channel_id, key, e.get("action") or "add", val))
    return out


# =========================================================
# 자리 (place)
# =========================================================
def _patch_last_frame_location(channel_id: str, loc: str) -> None:
    """Theoria 는 지난 턴 장소를 SCENE CONTINUITY 프레임(dai_snapshot.location)에서 읽는다.
    world_state 만 고치면 다음 턴 분석이 옛 장소를 다시 써서 고친 값이 한 턴 만에 덮인다 — 프레임도 같이 고친다."""
    try:
        sc = domain_manager.get_scene_continuity(channel_id)
        frames = sc.get("frames") or []
        if frames and isinstance(frames[-1], dict):
            snap = dict(frames[-1].get("dai_snapshot") or {})
            snap["location"] = loc
            frames[-1]["dai_snapshot"] = snap
            sc["frames"] = frames
            domain_manager.update_session_ai_memory(channel_id, {"scene_continuity": sc})
    except Exception as _e_fr:
        logger.debug(f"[OOC] 프레임 장소 고침 skip: {_e_fr}")


def apply_place_edits(channel_id: str, edits: List[Dict[str, Any]]) -> List[str]:
    """field="place" → op pc(지금 PC 장소) / npc_in(인물을 장소에) / npc_out(인물을 이 장면에서 뺌)."""
    import npc_manager as npm
    import world_tree
    out: List[str] = []
    pcm = domain_manager.pc_registry(channel_id)
    for e in edits or []:
        if not isinstance(e, dict):
            continue
        op = _op_of(e, ("pc", "npc_in", "npc_out"))
        if op == "pc":
            loc = str(e.get("value") or e.get("location") or "").strip()
            if not loc:
                out.append("⚠ 위치: 장소 이름이 없어")
                continue
            nid = world_tree.resolve_node_id(channel_id, loc, allow_token=False)
            if nid:
                loc = (world_tree.get_all_nodes(channel_id).get(nid) or {}).get("name", loc)
            else:
                world_tree.add_node(channel_id, loc, node_type="area",
                                    parent_id=world_tree.infer_parent_by_prefix(channel_id, loc),
                                    properties={"tags": ["ooc"]})
            domain_manager.set_current_location(channel_id, loc)
            _patch_last_frame_location(channel_id, loc)
            out.append(f"위치: 지금 {loc}")
            continue
        if op not in ("npc_in", "npc_out"):
            out.append(f"⚠ 위치: '{op or '?'}'은 못 해(pc·npc_in·npc_out)")
            continue
        raw = domain_manager._ooc_edit_npc_name(e)
        key = _resolve(npm.get_npcs(channel_id) or {}, raw)
        if not key or domain_manager.is_pc_name(key, pcm):
            out.append(f"⚠ 위치: '{raw}' — 명부에 없는 이름이야")
            continue
        if op == "npc_out":
            world_tree.remove_npc_presence(channel_id, key)
            out.append(f"위치: {key} 이 장면에서 뺌")
            continue
        loc = str(e.get("value") or e.get("location") or "").strip()
        if loc.lower() in _HERE:
            loc = domain_manager.get_current_location(channel_id)
        r = world_tree.set_npc_location(channel_id, key, loc)
        if r == "location_not_found":
            out.append(f"⚠ 위치: '{loc}'이라는 장소가 아직 없어")
        else:
            out.append(f"위치: {key} → {loc}")
    return out


# =========================================================
# 시간 (time)
# =========================================================
def _slot_of(hour: int, fallback: str) -> str:
    for slot, (start, end) in getattr(config, "TIME_SLOT_HOURS", {}).items():
        if (start <= end and start <= hour <= end) or (start > end and (hour >= start or hour <= end)):
            return slot
    return fallback


def set_clock(channel_id: str, *, slot: Any = None, hour: Any = None, minute: Any = None,
              year: Any = None, month: Any = None, day: Any = None, weather: Any = None) -> str:
    """시간대·시각·날짜·날씨를 한 번에. 범위는 `!시간 설정`과 같다. 되비침 한 줄(실패는 ⚠)."""
    import game_world

    def _i(v):
        try:
            return None if v is None or str(v).strip() == "" else int(float(v))
        except (TypeError, ValueError):
            return "bad"

    h, mi, yy, mo, dd = _i(hour), _i(minute), _i(year), _i(month), _i(day)
    if "bad" in (h, mi, yy, mo, dd):
        return "⚠ 시간: 숫자가 아닌 값이 있어"
    world = domain_manager.get_world_state(channel_id)
    game_world._init_clock(world)
    if h is not None and not (0 <= h <= 23):
        return "⚠ 시간: 시는 0~23"
    if mi is not None and not (0 <= mi <= 59):
        return "⚠ 시간: 분은 0~59"
    if mo is not None and not (1 <= mo <= config.CALENDAR_MONTHS_PER_YEAR):
        return f"⚠ 시간: 월은 1~{config.CALENDAR_MONTHS_PER_YEAR}"
    if dd is not None and not (1 <= dd <= config.CALENDAR_DAYS_PER_MONTH):
        return f"⚠ 시간: 일은 1~{config.CALENDAR_DAYS_PER_MONTH}"
    if yy is not None and yy < 1:
        return "⚠ 시간: 년은 1 이상"
    slot = str(slot or "").strip()
    if slot and slot not in game_world.get_time_slots(channel_id):
        return f"⚠ 시간: 시간대는 {', '.join(game_world.get_time_slots(channel_id))} 중 하나"
    wx = str(weather or "").strip()
    if not any(x is not None for x in (h, mi, yy, mo, dd)) and not slot and not wx:
        return "⚠ 시간: 바꿀 값이 없어"
    for k, v in (("year", yy), ("month", mo), ("day", dd), ("hour", h), ("minute", mi)):
        if v is not None:
            world[k] = v
    if h is not None:
        world["time_slot"] = _slot_of(h, world.get("time_slot", "오후"))
    elif slot:
        world["time_slot"] = slot
    if wx:
        world["weather"] = wx[:40]
    domain_manager.update_world_state(channel_id, world)
    return (f"시간: {game_world.format_calendar(world)} {int(world.get('hour', 12)):02d}:{int(world.get('minute', 0)):02d}"
            f" ({world.get('time_slot', '')})" + (f" · 날씨 {world['weather']}" if wx else ""))


def apply_time_edits(channel_id: str, edits: List[Dict[str, Any]]) -> List[str]:
    out: List[str] = []
    for e in edits or []:
        if isinstance(e, dict):
            out.append(set_clock(channel_id, slot=e.get("slot"), hour=e.get("hour"), minute=e.get("minute"),
                                 year=e.get("year"), month=e.get("month"), day=e.get("day"),
                                 weather=e.get("weather")))
    return out


# =========================================================
# 지난 일 정정 (fact) — [2026-10-10 기록 정정]
# =========================================================
# =========================================================
# [2026-10-18 C50] 정정 연결 · 덮어쓰기 · 되묻기 — 스펙 composition/C50_정정연결·되묻기_지도·스펙_2026-10-18.md
#   레티어스 "ooc는 고치라고 하는거라" — 최신이 이긴다. 묻는 건 대상만 같고 턴이 다르며 같은 일인지 안 설 때뿐.
# =========================================================
PENDING_KEY = "_pending_corrections"


def _write_correction(channel_id: str, about: List[str], text: str, wrong: str, *,
                      also_wrong: Optional[List[str]] = None) -> Tuple[Optional[Dict[str, Any]], List[str]]:
    """정정 한 줄을 넣고 위키를 고친 뒤 끈(links)을 적는다. 돌려줌: (행 | None, 위키 되비침 조각)."""
    import corrections
    import sqlite_store
    import wiki_store
    row = corrections.add(channel_id, about, text, wrong)
    if not row:
        return None, []
    links: Dict[str, Any] = {}
    wiki_bits: List[str] = []
    for nm in row["about"]:
        try:
            pid = wiki_store.resolve_page(channel_id, nm)
        except Exception:
            pid = None
        if not pid:
            continue
        r = wiki_store.correct_observed(channel_id, pid, row.get("wrong") or "", row["text"], turn=row.get("turn"))
        if r.get("ok"):
            links[pid] = {"line": r.get("line") or "", "demoted": r.get("demoted_text") or ""}
            wiki_bits.append(f"{nm} 위키" + (" (옛 줄 History로)" if r.get("demoted") else ""))
            if r.get("status") == "ambiguous":
                wiki_bits[-1] += " ⚠옛 줄이 여럿이라 그대로 둠"
    aw = [str(x).strip() for x in (also_wrong or []) if str(x or "").strip()]
    if aw:
        links["_also_wrong"] = aw[:6]
    if links:
        sqlite_store.set_correction_links(channel_id, row["id"], links)
        row["links"] = links
    return row, wiki_bits


def _supersede(channel_id: str, old: Dict[str, Any], about: List[str], text: str, wrong: str) -> str:
    """[C50 R3] 옛 정정 → 새 정정. 옛 것은 줄만 빼고(내린 원문은 History 그대로), wrong 은 이어받고, 옛 문장은 also_wrong."""
    import corrections
    corrections.undo(channel_id, old["id"], restore=False)
    also = [old.get("text") or ""] + list(((old.get("links") or {}).get("_also_wrong") or []))
    row, bits = _write_correction(channel_id, about, text, wrong or str(old.get("wrong") or ""), also_wrong=also)
    if not row:
        return f"⚠ 정정: '{str(old.get('text'))[:40]}'는 거뒀는데 새 정정을 못 넣었어"
    return (f"정정 바꿈: T{old.get('turn')} '{str(old.get('text'))[:40]}' → T{row.get('turn')} '{row['text'][:40]}'"
            + (f" · {' · '.join(bits)}" if bits else ""))


def _decide(channel_id: str, e: Dict[str, Any], about: List[str], text: str, wrong: str):
    """[C50 R4] → (kind, target_row, cands). kind = dup / replace / new / ask."""
    import corrections
    import sqlite_store
    active = sqlite_store.read_corrections(channel_id, active_only=True)
    ab = {_norm(a) for a in about}
    ov = [r for r in active if ab & {_norm(a) for a in (r.get("about") or [])}]
    if any(_norm(r.get("text")) == _norm(text) for r in ov):
        return "dup", None, []
    rid = e.get("replaces")
    if rid not in (None, "", 0, "0"):
        try:
            rid = int(rid)
        except (TypeError, ValueError):
            rid = None
        tgt = next((r for r in active if r.get("id") == rid), None)
        if tgt:
            return "replace", tgt, []
    if e.get("new") in (True, "true", "True", 1, "1"):
        return "new", None, []
    if not ov:
        return "new", None, []
    same = [r for r in ov if wrong and corrections.same_claim(wrong, r)]
    if same:
        return "replace", same[-1], []
    now = _turn(channel_id)
    st = [r for r in ov if r.get("turn") == now]
    if st:
        return "replace", st[-1], []
    return "ask", None, [r["id"] for r in reversed(ov)][:3]


def pending_question(channel_id: str, item: Dict[str, Any]) -> Tuple[str, List[str]]:
    """보류 하나 → (질문 메시지, 후보 버튼 글들)."""
    import sqlite_store
    byid = {r["id"]: r for r in sqlite_store.read_corrections(channel_id, active_only=True)}
    ed = item.get("edit") or {}
    lines = [f"❓ **정정 확인** — '{str(ed.get('value') or '')[:80]}'",
             "같은 대상에 다른 턴에 고친 정정이 있어. 그걸 바꾸는 거야, 다른 일이야? (고르기 전엔 반영 안 해)"]
    labels = []
    for cid in item.get("cands") or []:
        r = byid.get(cid)
        if not r:
            continue
        lines.append(f"· T{r.get('turn')}: {str(r.get('text'))[:80]}")
        labels.append(f"T{r.get('turn')} '{str(r.get('text'))[:30]}' → 이걸로 바꿈"[:80])
    return "\n".join(lines), labels


def store_pending(channel_id: str, msg_id: Any, item: Dict[str, Any], uid: Any) -> None:
    """[C50 R4] 보류를 세션 메모리에 질문 메시지 id 로. 같은 대상의 옛 보류·만료된 보류는 버린다."""
    if msg_id is None or not isinstance(item, dict):
        return
    mem = domain_manager.get_session_ai_memory(channel_id) or {}
    pend = dict(mem.get(PENDING_KEY) or {})
    now = _turn(channel_id)
    keep = int(getattr(config, "CORRECTION_PENDING_TURNS", 10) or 10)
    ab = {_norm(a) for a in (item.get("about") or [])}
    for k, v in list(pend.items()):
        if (not isinstance(v, dict) or now - int(v.get("turn") or 0) > keep
                or ab & {_norm(a) for a in (v.get("about") or [])}):
            pend.pop(k, None)
    pend[str(msg_id)] = {**item, "uid": str(uid or "")}
    domain_manager.update_session_ai_memory(channel_id, {PENDING_KEY: pend})


def resolve_pending(channel_id: str, msg_id: Any, choice: str, uid: Any) -> Tuple[bool, str]:
    """[C50 R4] 버튼 → 적용. choice = pick:N / new / drop. 돌려줌: (적용했나, 되비침)."""
    import sqlite_store
    mem = domain_manager.get_session_ai_memory(channel_id) or {}
    pend = dict(mem.get(PENDING_KEY) or {})
    it = pend.get(str(msg_id))
    if not isinstance(it, dict):
        return False, "이미 정리됐거나 만료된 정정이야"
    if str(it.get("uid") or "") and str(uid) != str(it.get("uid")):
        return False, "이 정정은 낸 사람이 골라야 해"
    pend.pop(str(msg_id), None)
    domain_manager.update_session_ai_memory(channel_id, {PENDING_KEY: pend})
    keep = int(getattr(config, "CORRECTION_PENDING_TURNS", 10) or 10)
    if _turn(channel_id) - int(it.get("turn") or 0) > keep:
        return False, "오래돼서 버렸어 — 다시 말해줘"
    ed = it.get("edit") or {}
    about, text, wrong = list(ed.get("about") or []), str(ed.get("value") or ""), str(ed.get("wrong") or "")
    if choice == "drop":
        return True, "취소 — 반영 안 했어"
    if str(choice).startswith("pick:"):
        try:
            i = int(str(choice).split(":", 1)[1])
        except (TypeError, ValueError):
            i = -1
        cands = list(it.get("cands") or [])
        if 0 <= i < len(cands):
            tgt = next((r for r in sqlite_store.read_corrections(channel_id, active_only=True)
                        if r.get("id") == cands[i]), None)
            if tgt:
                return True, _supersede(channel_id, tgt, about, text, wrong)
        # 후보가 이미 꺼졌으면 따로 넣는다
    row, bits = _write_correction(channel_id, about, text, wrong)
    if not row:
        return False, "⚠ 정정: 넣지 못했어"
    return True, (f"정정: T{row.get('turn')} {', '.join(row['about'])} — {row['text'][:60]}"
                  + (f" · {' · '.join(bits)}" if bits else ""))


def apply_fact_edits(channel_id: str, edits: List[Dict[str, Any]],
                     pending_out: Optional[List[Dict[str, Any]]] = None) -> List[str]:
    """field="fact" → 정오표 한 줄(corrections) + 걸린 위키 페이지 Observed 정정(틀린 줄은 History 로 강등).
    원문·발효·딥은 손대지 않는다 — 읽는 자리가 정정을 옆에 붙인다.
    [2026-10-18 C50] 거두기 = 완전 되돌림(번호 `id` 또는 옛 꼴 문장). 같은 대상이면 판정(`_decide`) —
    같은 문장 무시 / `replaces` · 같은 주장 · 같은 턴 → 갈아치움 / `new` → 따로 / 다른 턴만 겹침 → 보류(pending_out).
    pending_out 이 없으면(옛 호출부) 보류 대신 따로 넣는다 — 지우는 쪽보다 안전하다."""
    import corrections
    out: List[str] = []
    for e in edits or []:
        if not isinstance(e, dict):
            continue
        act = str(e.get("action") or e.get("op") or "add").strip().lower()
        text = str(e.get("value") or e.get("text") or "").strip()
        if act in ("remove", "retract", "delete"):
            _cid = e.get("id")
            if _cid not in (None, ""):
                res = corrections.undo(channel_id, _cid, restore=True)
                if res.get("ok"):
                    _r = res["row"]
                    out.append(f"정정 거둠: T{_r.get('turn')} {', '.join(_r.get('about') or [])} — {str(_r.get('text'))[:60]}"
                               + (f" (위키 {res['pages']}쪽 되돌림)" if res.get("pages") else ""))
                else:
                    out.append(f"⚠ 정정: {_cid}번 정정이 없거나 이미 거둔 거야")
                continue
            n, why = corrections.retract(channel_id, text)
            if n:
                out.append(f"정정 거둠: {why[:60]} (위키까지 되돌림)")
            else:
                out.append({"nomatch": f"⚠ 정정: '{text[:40]}'에 맞는 정정이 없어",
                            "ambiguous": f"⚠ 정정: '{text[:40]}'에 맞는 정정이 여럿이라 못 골라",
                            "empty": "⚠ 정정: 거둘 내용이 없어"}.get(why, "⚠ 정정: 거두지 못했어"))
            continue
        about = e.get("about")
        if isinstance(about, str):
            about = [about]
        if not isinstance(about, list) or not about:
            raw = domain_manager._ooc_edit_npc_name(e)
            about = [raw] if raw else []
        if not text:
            out.append("⚠ 정정: 맞는 사실이 없어")
            continue
        ab = corrections._canon_about(channel_id, about)
        if not ab:
            out.append("⚠ 정정: 무엇에 대한 정정인지(이름·낱말)가 없어")
            continue
        wrong = str(e.get("wrong") or "").strip()
        kind, tgt, cands = _decide(channel_id, e, ab, text, wrong)
        if kind == "dup":
            out.append(f"⚠ 정정: 이미 같은 정정이 있어 — {text[:60]}")
            continue
        if kind == "replace" and tgt:
            out.append(_supersede(channel_id, tgt, ab, text, wrong))
            continue
        if kind == "ask" and pending_out is not None:
            pending_out.append({"edit": {"about": ab, "value": text, "wrong": wrong},
                                "about": ab, "cands": cands, "turn": _turn(channel_id)})
            out.append(f"⚠ 정정 보류: '{text[:40]}' — 같은 대상에 다른 턴 정정이 있어. 아래에서 골라줘(고르기 전엔 반영 안 해)")
            continue
        row, wiki_bits = _write_correction(channel_id, ab, text, wrong)
        if not row:
            out.append("⚠ 정정: 무엇에 대한 정정인지(이름·낱말)가 없어")
            continue
        out.append(f"정정: T{row.get('turn')} {', '.join(row['about'])} — {row['text'][:60]}"
                   + (f" · {' · '.join(wiki_bits)}" if wiki_bits else ""))
    return out


# =========================================================
# 이야기 실 — 스레드 장부(약속·사안)·퀘스트 보드 — [2026-10-10 이야기 실 O-E]
#   스펙: 파티쳇수정/state_v10/이야기실_지도·스펙_2026-10-10.md
# =========================================================
THREAD_OPS = ("open", "close", "pause", "resume", "reschedule", "progress", "void")
# 편집 콜이 고르기 쉬운 다른 말 → op (outcome 이 따라붙는 것은 짝으로)
_THREAD_ALIAS = {"add": ("open", None), "new": ("open", None), "create": ("open", None),
                 "delete": ("void", None), "remove": ("void", None), "never": ("void", None),
                 "complete": ("close", "done"), "done": ("close", "done"), "finish": ("close", "done"),
                 "kept": ("close", "done"), "cancel": ("close", "dropped"), "drop": ("close", "dropped"),
                 "break": ("close", "broken"), "broken": ("close", "broken"),
                 "reopen": ("resume", None), "due": ("reschedule", None)}
QUEST_OPS = ("add", "complete", "remove", "progress")
_QUEST_ALIAS = {"open": "add", "new": "add", "create": "add", "done": "complete", "finish": "complete",
                "close": "complete", "delete": "remove", "dismiss": "remove", "drop": "remove",
                "advance": "progress"}


def apply_thread_edits(channel_id: str, edits: List[Dict[str, Any]]) -> List[str]:
    """field="thread" → thread_ledger.apply_ooc_events(인용 관문 없는 OOC 관문). op 를 action 칸에 적어도 읽는다."""
    import thread_ledger
    evs: List[Dict[str, Any]] = []
    out: List[str] = []
    for e in edits or []:
        if not isinstance(e, dict):
            continue
        op = _op_of(e, THREAD_OPS + tuple(_THREAD_ALIAS))
        ev = dict(e)
        if op in _THREAD_ALIAS:
            op, oc = _THREAD_ALIAS[op]
            if oc and not ev.get("outcome"):
                ev["outcome"] = oc
        if op not in THREAD_OPS:
            out.append(f"⚠ 이야기 실: 모르는 일 '{op or '?'}'")
            continue
        ev["op"] = op
        if not ev.get("id") and ev.get("key") and not ev.get("title"):
            ev["title"] = ev.get("key")
        evs.append(ev)
    if evs:
        out += thread_ledger.apply_ooc_events(channel_id, _turn(channel_id), evs)
    return out


def apply_quest_edits(channel_id: str, edits: List[Dict[str, Any]]) -> List[str]:
    """field="quest" → 퀘스트 보드. add(dismissed 풀기)·complete(장 볼륨 보상 포함 — 자동 완료와 같은 함수)·
    remove(+dismissed: 자동 등록이 다시 못 올림)·progress(못 찾으면 ⚠ — 배경 경로처럼 자동 등록 안 함).
    이름 찾기는 _find_quest(고유 일치만)."""
    import game_character as gc
    out: List[str] = []

    def _say(r: Any) -> str:
        r = str(r or "").strip()
        if not r:
            return "⚠ 퀘스트: 결과 없음"
        if r.startswith(("⚠", "ℹ")):
            return "⚠ 퀘스트: " + r.lstrip("⚠️ℹ️ ").replace("**", "")
        return r
    for e in edits or []:
        if not isinstance(e, dict):
            continue
        op = _op_of(e, QUEST_OPS + tuple(_QUEST_ALIAS))
        op = _QUEST_ALIAS.get(op, op)
        name = str(e.get("value") or e.get("name") or e.get("content") or e.get("title") or e.get("key") or "").strip()
        if op not in QUEST_OPS:
            out.append(f"⚠ 퀘스트: 모르는 일 '{op or '?'}' — add·complete·remove·progress")
            continue
        if not name:
            out.append("⚠ 퀘스트: 이름이 없어")
            continue
        if op == "add":
            out.append(_say(gc.add_quest(channel_id, name, e.get("rank"), ooc=True)))
        elif op == "complete":
            out.append(_say(gc.complete_quest(channel_id, name)))
        elif op == "remove":
            tgt = gc._find_quest(gc.get_active_quests_raw(channel_id), name)
            real = (tgt.get("content") if isinstance(tgt, dict) else tgt) if tgt else ""
            r = gc.remove_quest(channel_id, name)
            line = _say(r)
            if real and not line.startswith("⚠"):
                gc.dismiss_quest(channel_id, real)
                line += " (다시 자동 등록 안 함)"
            out.append(line)
        else:
            try:
                delta = int(e.get("delta") if e.get("delta") is not None else 1)
            except (TypeError, ValueError):
                delta = 1
            delta = max(-5, min(5, delta)) or 1
            out.append(_say(gc.advance_quest_progress(channel_id, name, delta)))
    return out


# =========================================================
# 편집 콜 현재 상태(콜 0)
# =========================================================
def world_state_for_edit(channel_id: str, ooc_text: str = "", knows_cap: int = 3) -> Dict[str, Any]:
    """편집 콜 current_state 에 더할 것 — 지금 장소·시간·날씨, 장소 이름들, 본문에 나온 인물이 아는 것."""
    import game_world
    import npc_manager as npm
    out: Dict[str, Any] = {}
    try:
        w = domain_manager.get_world_state(channel_id) or {}
        game_world._init_clock(w)
        out["world"] = {"location": domain_manager.get_current_location(channel_id),
                        "time": f"{w.get('time_slot', '')} {int(w.get('hour', 12)):02d}:{int(w.get('minute', 0)):02d}",
                        "date": game_world.format_calendar(w), "weather": w.get("weather", "")}
    except Exception as _e_w:
        logger.debug(f"[OOC] world 상태 skip: {_e_w}")
    try:
        import world_tree
        out["places"] = [str(n.get("name")) for n in (world_tree.get_all_nodes(channel_id) or {}).values()
                         if isinstance(n, dict) and n.get("name")][:30]
    except Exception as _e_p:
        logger.debug(f"[OOC] places skip: {_e_p}")
    try:
        txt = str(ooc_text or "")
        kn_all = domain_manager.get_domain(channel_id).get("npc_knowledge") or {}
        picked: Dict[str, List[str]] = {}
        for name, data in (npm.get_npcs(channel_id) or {}).items():
            if len(picked) >= knows_cap:
                break
            forms = [name, re.split(r'[(\[（]', name)[0].strip()] + [a for a in ((data or {}).get("aliases") or [])
                                                                    if isinstance(a, str)]
            if any(f and len(f) >= 2 and f in txt for f in forms):
                ks = list(((kn_all.get(domain_manager._mind(channel_id, name)) or {}).get("knows") or []))[-20:]
                if ks:
                    picked[name] = ks
        if picked:
            out["npc_knows"] = picked
    except Exception as _e_k:
        logger.debug(f"[OOC] npc_knows skip: {_e_k}")
    # [2026-10-10 이야기 실 O-E] 스레드(id 포함 — 루카 컨텍스트엔 제목만 있어 id 찾기는 편집 콜 몫)·퀘스트. 비면 안 싣는다.
    try:
        import thread_ledger
        _th = thread_ledger.ooc_state(channel_id)
        if _th:
            out["threads"] = _th
    except Exception as _e_th:
        logger.debug(f"[OOC] threads skip: {_e_th}")
    try:
        import game_character
        _qs = []
        for q in game_character.get_active_quests_raw(channel_id) or []:
            if isinstance(q, dict):
                _qs.append({"content": q.get("content", ""), "rank": q.get("rank", "normal"),
                            "progress": f"{q.get('progress', 0)}/{q.get('max_progress', 0)}"})
            elif q:
                _qs.append({"content": str(q)})
        if _qs:
            out["quests"] = _qs[:15]
    except Exception as _e_q:
        logger.debug(f"[OOC] quests skip: {_e_q}")
    # [2026-10-18 C50 R5] 활성 정정(번호·턴·대상·문장) — 편집 콜이 번호로 거두고 번호로 바꾼다.
    #   본문에 대상 이름·별칭이 나온 것(최신순 ≤8), 없으면 최근 5. 비면 안 싣는다.
    try:
        import corrections as _corr_ws
        import sqlite_store as _ss_ws
        _act = _ss_ws.read_corrections(channel_id, active_only=True)
        if _act:
            _txt = str(ooc_text or "")
            _npcs = npm.get_npcs(channel_id) or {}
            _hit = [r for r in reversed(_act)
                    if any(f in _txt for w in (r.get("about") or []) for f in _corr_ws._forms(channel_id, str(w), _npcs))]
            _pick = _hit[:8] if _hit else list(reversed(_act))[:5]
            out["corrections"] = [{"id": r["id"], "turn": r.get("turn"), "about": r.get("about") or [],
                                   "text": r.get("text")} for r in _pick]
    except Exception as _e_cr:
        logger.debug(f"[OOC] corrections skip: {_e_cr}")
    return out
