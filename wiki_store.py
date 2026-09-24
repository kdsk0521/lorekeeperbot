# -*- coding: utf-8 -*-
"""wiki_store.py — 내부 위키 페이지 API (2026-09-14 W1)

설계: memory_lore/internal_wiki_spec_2026-09-14.md §2·§3·§4·§7 / 지시서 wiki_w1_pages_todo.

한 문장: **페이지는 새 기억 기계가 아니라, 로어 등록·F1 발효가 이미 만드는 것의 착지 형식**이다.
  - `sqlite_store`는 스키마·연결만 진다(비대화 방지). 페이지 로직은 전부 여기.
  - [2026-09-16 시트 2차b] **인물(character) 페이지 lore 절 = 시트 원문의 정본**(PC·NPC 공통).
    NPC dict에는 `description`을 저장하지 않는다 — 원문은 `domain_manager` 쓰기 관문이
    `set_sheet_text`(=`_parse_sections`→`map_sheet_sections`→`set_lore_sections`)로 여기에만 쓰고,
    `get_npcs()`가 읽을 때 `assemble_lore_text`로 조립해 `description` 키를 채운다(파생, 캐시 없음).
    lore 절은 리비전을 안 남긴다(전량 교체). 되감기가 복원하는 것은 play 절뿐.
    `!클리어`는 PC 페이지를 지우지 않는다. 성장(grow_sheet)은 NPC·PC 공통으로 play 절 `Observed`만 쓴다.
  - 실패 계약: 행 0 = `[]`/`None`, 실패 = `None`/`False`/`{"ok":False,...}`, 예외는 삼키고 로그.
  - 삭제 0: 절·페이지를 지우지 않는다(강등·status 표시만). 예외는 ① lore 절 전량 교체(파생물)
    ② 되감기 복원(첫 리비전이면 절 삭제 — 그 절은 애초에 없던 것) ③ rename 이관(옛 페이지 행).

이름 정규화는 **새 규칙을 만들지 않는다** — 페이지 id는 S0 `fermentation._norm_quote`(NFKC+공백접기),
별칭 해상도는 `domain_manager._find_npc_key`를 그대로 호출한다(낱말 경계 병 자리 6번째 금지).
"""

import json
import hashlib
import logging
from typing import Any, Dict, List, Optional

import config

logger = logging.getLogger("WikiStore")


# =========================================================
# 0. 공용 — 정규화 · id · 해시 · 연결
# =========================================================

def _norm_name(s: str) -> str:
    """페이지 이름 정규화 = S0 `_norm_quote`(NFKC + 공백 접기) + 소문자. 새 규칙 0."""
    try:
        from fermentation import _norm_quote
        return _norm_quote(s or "").lower()
    except Exception:
        # fermentation을 못 불러오는 자리(도구·부분 import)에서도 id가 흔들리면 안 된다.
        import re as _re
        import unicodedata as _ud
        return _re.sub(r"\s+", " ", _ud.normalize("NFKC", s or "")).strip().lower()


def _sha1(s: str) -> str:
    return hashlib.sha1((s or "").encode("utf-8")).hexdigest()


def page_id_for(kind: str, name: str) -> str:
    """`kind:sha1(정규화 이름)[:16]`. 이름·별칭을 바꿔도 id는 안 변한다(§2)."""
    return f"{kind}:{_sha1(_norm_name(name))[:16]}"


def _conn(channel_id: str):
    """채널 DB 연결. 실패는 None(예외 안 던짐)."""
    try:
        import sqlite_store
        if not sqlite_store._ensure_schema(channel_id):
            return None
        return sqlite_store._get_conn(channel_id)
    except Exception as e:
        logger.debug(f"[Wiki] conn 실패 (무시): {channel_id}: {e}")
        return None


def _json_list(raw: Any) -> list:
    try:
        v = json.loads(raw) if isinstance(raw, str) else raw
        return list(v) if isinstance(v, list) else []
    except Exception:
        return []


def _kind_of(channel_id: str, page_id: str) -> str:
    p = get_page(channel_id, page_id)
    return str(p.get("kind", "")) if p else ""


def _lore_enum(kind: str) -> tuple:
    return tuple(getattr(config, "WIKI_LORE_SECTIONS", {}).get(kind, ()) or ())


def _play_enum(kind: str) -> tuple:
    return tuple(getattr(config, "WIKI_PLAY_SECTIONS", {}).get(kind, ()) or ())


def _is_item_section(section: str) -> bool:
    return section in tuple(getattr(config, "WIKI_ITEM_SECTIONS", ()) or ())


# =========================================================
# 1. 페이지
# =========================================================

def ensure_page(channel_id: str, kind: str, name: str, *,
                aliases: Optional[list] = None, source: str = "play",
                turn: Optional[int] = None) -> Optional[str]:
    """페이지 생성/갱신(멱등). 이름·별칭은 **병합**, 삭제 0. Returns page_id | None."""
    if not channel_id or not kind or not str(name or "").strip():
        return None
    conn = _conn(channel_id)
    if conn is None:
        return None
    pid = page_id_for(kind, name)
    try:
        row = conn.execute(
            "SELECT name, aliases, source, status, created_turn FROM pages "
            "WHERE channel_id=? AND page_id=?", (channel_id, pid)).fetchone()
        new_aliases = [a for a in (aliases or []) if isinstance(a, str) and a.strip()]
        if row is None:
            merged = _merge_aliases([], new_aliases, exclude=name)
            conn.execute(
                "INSERT INTO pages (channel_id, page_id, kind, name, aliases, source, status, "
                "created_turn, updated_turn) VALUES (?,?,?,?,?,?,?,?,?)",
                (channel_id, pid, kind, name, json.dumps(merged, ensure_ascii=False),
                 str(source or "play"), "", turn, turn))
        else:
            merged = _merge_aliases(_json_list(row[1]), new_aliases, exclude=name)
            conn.execute(
                "UPDATE pages SET name=?, aliases=?, source=?, updated_turn=? "
                "WHERE channel_id=? AND page_id=?",
                (name, json.dumps(merged, ensure_ascii=False), str(source or row[2] or "play"),
                 turn if turn is not None else row[4], channel_id, pid))
        conn.commit()
        return pid
    except Exception as e:
        logger.warning(f"[Wiki] ensure_page 실패 (무시): {channel_id}/{kind}/{name}: {e}")
        return None


_ALIAS_MAX = 32


def _merge_aliases(old: list, new: list, exclude: str = "") -> list:
    """별칭 병합 — NFKC 소문자 중복 제거, 제목과 같은 것은 뺀다, ≤32(§2). 삭제 0(앞쪽 유지)."""
    out: List[str] = []
    seen = set()
    ex = _norm_name(exclude) if exclude else ""
    for a in list(old) + list(new):
        if not isinstance(a, str) or not a.strip():
            continue
        n = _norm_name(a)
        if not n or n == ex or n in seen:
            continue
        seen.add(n)
        out.append(a.strip())
        if len(out) >= _ALIAS_MAX:
            break
    return out


def get_page(channel_id: str, page_id: str) -> Optional[dict]:
    conn = _conn(channel_id)
    if conn is None:
        return None
    try:
        r = conn.execute(
            "SELECT page_id, kind, name, aliases, source, status, created_turn, updated_turn, "
            "owner_uid, built_len FROM pages WHERE channel_id=? AND page_id=?",
            (channel_id, page_id)).fetchone()
        if r is None:
            return None
        return {"page_id": r[0], "kind": r[1], "name": r[2], "aliases": _json_list(r[3]),
                "source": r[4], "status": r[5], "created_turn": r[6], "updated_turn": r[7],
                "owner_uid": r[8] or "", "built_len": int(r[9] or 0)}
    except Exception as e:
        logger.warning(f"[Wiki] get_page 실패 (무시): {e}")
        return None


def list_pages(channel_id: str, kind: Optional[str] = None) -> list:
    conn = _conn(channel_id)
    if conn is None:
        return []
    try:
        if kind:
            rows = conn.execute(
                "SELECT page_id, kind, name, aliases, source, status, created_turn, updated_turn "
                "FROM pages WHERE channel_id=? AND kind=? ORDER BY name", (channel_id, kind)).fetchall()
        else:
            rows = conn.execute(
                "SELECT page_id, kind, name, aliases, source, status, created_turn, updated_turn "
                "FROM pages WHERE channel_id=? ORDER BY kind, name", (channel_id,)).fetchall()
        return [{"page_id": r[0], "kind": r[1], "name": r[2], "aliases": _json_list(r[3]),
                 "source": r[4], "status": r[5], "created_turn": r[6], "updated_turn": r[7]}
                for r in rows]
    except Exception as e:
        logger.warning(f"[Wiki] list_pages 실패 (무시): {e}")
        return []


def resolve_page(channel_id: str, name_or_alias: str, kind: Optional[str] = None) -> Optional[str]:
    """이름·별칭 → page_id. 규칙은 `domain_manager._find_npc_key` 것을 **호출**한다(§0 ④).

    같은 키를 두 페이지가 가지면 **모호 처리 = 둘 다 안 잡는다**(§2, 낱말 경계 병의 페이지판)."""
    if not str(name_or_alias or "").strip():
        return None
    # [2026-09-24 감사 §5-2 #9b] 삭제 표식(status='deleted') 페이지는 이름 조회에서 뺀다 — 읽기에서 가려진 페이지가
    #   살아 있는 동명 페이지와 "모호"로 겹쳐 둘 다 못 잡거나, F1 패치가 묘비로 가던 자리.
    pages = [p for p in list_pages(channel_id, kind) if str(p.get("status") or "") != "deleted"]
    if not pages:
        return None
    # ① 모호 검사 — 제목·별칭 정규화 키가 두 페이지에 걸치면 그 키는 안 잡는다.
    key_owners: Dict[str, set] = {}
    for p in pages:
        for k in [p["name"]] + list(p["aliases"]):
            n = _norm_name(k)
            if n:
                key_owners.setdefault(n, set()).add(p["page_id"])
    q = _norm_name(name_or_alias)
    if len(key_owners.get(q, ())) > 1:
        logger.info(f"[Wiki] ambiguous name={name_or_alias}")
        return None
    # ② 해상도 — 기존 규칙 그대로(정규화 → base/inner 대칭 → aliases → 단일 축약 토큰).
    try:
        import domain_manager
        pseudo = {p["name"]: {"aliases": list(p["aliases"])} for p in pages}
        matched = domain_manager._find_npc_key(pseudo, name_or_alias)
    except Exception as e:
        logger.warning(f"[Wiki] resolve_page 실패 (무시): {e}")
        return None
    if not matched:
        return None
    hits = [p["page_id"] for p in pages if p["name"] == matched]
    if len(hits) != 1:
        if len(hits) > 1:
            logger.info(f"[Wiki] ambiguous name={name_or_alias}")
        return None
    return hits[0]


# =========================================================
# 2. 섹션 읽기
# =========================================================

def get_sections(channel_id: str, page_id: str, owner: Optional[str] = None) -> list:
    conn = _conn(channel_id)
    if conn is None:
        return []
    try:
        sql = ("SELECT section, owner, body, src_turns, updated_turn, hash, derived_hash "
               "FROM sections WHERE channel_id=? AND page_id=?")
        args: list = [channel_id, page_id]
        if owner:
            sql += " AND owner=?"
            args.append(owner)
        rows = conn.execute(sql + " ORDER BY section", args).fetchall()
        return [{"section": r[0], "owner": r[1], "body": r[2], "src_turns": _json_list(r[3]),
                 "updated_turn": r[4], "hash": r[5], "derived_hash": r[6]} for r in rows]
    except Exception as e:
        logger.warning(f"[Wiki] get_sections 실패 (무시): {e}")
        return []


def page_char_count(channel_id: str, page_id: str) -> int:
    return sum(len(s.get("body") or "") for s in get_sections(channel_id, page_id))


# =========================================================
# 3. lore 절 — 전량 교체(파생물, history 없음)
# =========================================================

def set_lore_sections(channel_id: str, page_id: str, sections: Dict[str, str],
                      derived_hash: str, turn: Optional[int] = None) -> int:
    """lore 절 전량 교체. 같은 `derived_hash`면 no-op(0). play 절은 건드리지 않는다.

    인물 페이지 lore 절의 **유일한 쓰기 관문**(시트 2차b — NPC description 복사 경로 폐지).
    `section_history`엔 안 남긴다(전량 교체 — 리비전 대상은 play 절).
    enum 밖 이름은 `Notes`에 합치고(원 헤더 보존), 상한 초과는 방어적으로 잘라 저장(로그 1줄)."""
    conn = _conn(channel_id)
    if conn is None:
        return 0
    kind = _kind_of(channel_id, page_id)
    if not kind:
        return 0
    try:
        cur = conn.execute(
            "SELECT derived_hash FROM sections WHERE channel_id=? AND page_id=? AND owner='lore' LIMIT 1",
            (channel_id, page_id)).fetchone()
        # [2026-09-24 감사] 삭제 표식 페이지는 단락하지 않는다 — mark_page_deleted는 lore 절을 남기므로
        #   같은 원문 재등록(=유일한 부활 경로)이 해시 일치로 0을 돌려 status='deleted'가 영구히 남았다.
        #   아래 전량 교체를 그대로 타면 n>0 분기가 status를 되돌린다.
        if cur is not None and derived_hash and cur[0] == derived_hash \
                and not _page_deleted(channel_id, page_id):
            return 0
        enum = _lore_enum(kind)
        fallback = "Notes" if "Notes" in enum else (enum[-1] if enum else "Notes")
        merged: Dict[str, str] = {}
        for name, body in (sections or {}).items():
            target = name if name in enum else fallback
            txt = str(body or "").strip()
            if not txt:
                continue
            merged[target] = (merged[target] + "\n\n" + txt) if target in merged else txt
        # [2026-09-16 레티어스] lore 절(작가 원문)에 저장 캡 없음 — 원문은 동결·정본이라 잘리면 복구 불가.
        #   길이는 렌더/급식 발췌(600자)·compile 예산이 관리한다. 옛 4000자 truncate 삭제.
        conn.execute("DELETE FROM sections WHERE channel_id=? AND page_id=? AND owner='lore'",
                     (channel_id, page_id))
        n = 0
        order = {s: i for i, s in enumerate(enum)}
        for sec in sorted(merged, key=lambda s: order.get(s, 999)):
            body = merged[sec]
            conn.execute(
                "INSERT INTO sections (channel_id, page_id, section, owner, body, src_turns, "
                "updated_turn, hash, derived_hash) VALUES (?,?,?,'lore',?,'[]',?,?,?)",
                (channel_id, page_id, sec, body, turn, _sha1(body), derived_hash or ""))
            n += 1
        # [2026-09-16 2차b 추기] 작가 원문 재등록 = 유일한 부활 경로. 세션 자동 생성(ensure_page)은 status를 안 건드린다.
        if n > 0:
            conn.execute("UPDATE pages SET updated_turn=?, status=CASE WHEN status='deleted' THEN '' ELSE status END "
                         "WHERE channel_id=? AND page_id=?", (turn, channel_id, page_id))
        else:
            conn.execute("UPDATE pages SET updated_turn=? WHERE channel_id=? AND page_id=?",
                         (turn, channel_id, page_id))
        conn.commit()
        return n
    except Exception as e:
        logger.warning(f"[Wiki] set_lore_sections 실패 (무시): {page_id}: {e}")
        return 0


# =========================================================
# 4. play 절 — 절 패치 + CAS + 리비전
# =========================================================

def _reject(page_id: str, section: str, op: str, reason: str) -> dict:
    logger.info(f"[Wiki] patch page={page_id} section={section} op={op} rejected:{reason}")
    return {"ok": False, "reason": reason}


def write_play_section(channel_id: str, page_id: str, section: str, body: str, *,
                       src_turns: Optional[list] = None, turn: Optional[int] = None,
                       reason: str = "", expected_hash: Optional[str] = None,
                       op: str = "upsert") -> dict:
    """play 절 upsert. owner=lore 대상·해시 불일치·상한 초과는 **거부**(중간 절단 금지, §4).

    항목 절의 개수 초과분만 예외적으로 거부가 아니라 `History` 절 끝으로 **강등**한다(삭제 0)."""
    conn = _conn(channel_id)
    if conn is None:
        return {"ok": False, "reason": "no_conn"}
    kind = _kind_of(channel_id, page_id)
    if not kind:
        return _reject(page_id, section, op, "no_page")
    if section not in _play_enum(kind):
        return _reject(page_id, section, op, "not_play")
    try:
        cur = conn.execute(
            "SELECT owner, body, src_turns, hash FROM sections "
            "WHERE channel_id=? AND page_id=? AND section=?",
            (channel_id, page_id, section)).fetchone()
        if cur is not None and cur[0] != "play":
            return _reject(page_id, section, op, "not_play")
        if expected_hash is not None and (cur[3] if cur else "") != expected_hash:
            return _reject(page_id, section, op, "hash")

        new_body = str(body or "")
        demoted: List[str] = []
        if _is_item_section(section):
            lines = [ln for ln in new_body.splitlines() if ln.strip()]
            cap = int(getattr(config, "WIKI_ITEM_SECTION_MAX", 60))
            if len(lines) > cap and section != "History":
                demoted = lines[:len(lines) - cap]      # 오래된 항목부터 강등
                lines = lines[len(lines) - cap:]
            new_body = "\n".join(lines)
        else:
            # [2026-09-16 레티어스] 텍스트 절(Observed)에도 저장 캡 없음 — 관찰이 size로 버려지던
            #   경로(2차 의심 4) 폐지. 길이는 grow_sheet 정리 콜(+250자 임계)이 관리한다.
            pass

        # [2026-09-16 레티어스] 페이지 총량 상한(WIKI_PAGE_MAX_CHARS)도 저장 거부에 쓰지 않는다 — 저장 캡 없음.

        merged_turns = sorted({int(t) for t in list(_json_list(cur[2]) if cur else [])
                               + list(src_turns or []) if isinstance(t, (int, float))})
        # ① 현 리비전 적립(있을 때만 — 없던 절은 적립할 과거가 없다)
        rev = 0
        if cur is not None:
            m = conn.execute(
                "SELECT COALESCE(MAX(revision), 0) FROM section_history "
                "WHERE channel_id=? AND page_id=? AND section=?",
                (channel_id, page_id, section)).fetchone()
            rev = int(m[0] or 0) + 1
            conn.execute(
                "INSERT INTO section_history (channel_id, page_id, section, revision, body, "
                "src_turns, turn, reason) VALUES (?,?,?,?,?,?,?,?)",
                (channel_id, page_id, section, rev, cur[1],
                 json.dumps(_json_list(cur[2]), ensure_ascii=False), turn, reason))
        # ② 절 upsert
        h = _sha1(new_body)
        conn.execute(
            "INSERT INTO sections (channel_id, page_id, section, owner, body, src_turns, "
            "updated_turn, hash, derived_hash) VALUES (?,?,?,'play',?,?,?,?,'') "
            "ON CONFLICT(channel_id, page_id, section) DO UPDATE SET owner='play', "
            "body=excluded.body, src_turns=excluded.src_turns, updated_turn=excluded.updated_turn, "
            "hash=excluded.hash",
            (channel_id, page_id, section, new_body,
             json.dumps(merged_turns, ensure_ascii=False), turn, h))
        # ③ 강등분은 History 끝에 append(삭제 0) — 같은 트랜잭션
        if demoted:
            _append_history_lines(conn, channel_id, page_id, demoted, merged_turns, turn)
            logger.info(f"[Wiki] item overflow page={page_id} section={section} "
                        f"demoted={len(demoted)} -> History")
        conn.execute("UPDATE pages SET updated_turn=? WHERE channel_id=? AND page_id=?",
                     (turn, channel_id, page_id))
        conn.commit()
        logger.info(f"[Wiki] patch page={page_id} section={section} op={op} accepted")
        return {"ok": True, "revision": rev, "hash": h, "demoted": len(demoted)}
    except Exception as e:
        logger.warning(f"[Wiki] write_play_section 실패 (무시): {page_id}/{section}: {e}")
        return {"ok": False, "reason": "error"}


def _append_history_lines(conn, channel_id: str, page_id: str, lines: list,
                          src_turns: list, turn: Optional[int]) -> None:
    """History 절 끝에 줄 append. 강등 전용(같은 트랜잭션 안에서만 쓴다)."""
    r = conn.execute("SELECT body, src_turns FROM sections "
                     "WHERE channel_id=? AND page_id=? AND section='History'",
                     (channel_id, page_id)).fetchone()
    old = (r[0] if r else "") or ""
    have = {ln.strip() for ln in old.splitlines() if ln.strip()}
    add = [ln for ln in lines if ln.strip() and ln.strip() not in have]
    if not add:
        return
    body = (old + "\n" + "\n".join(add)).strip() if old.strip() else "\n".join(add)
    turns = sorted({int(t) for t in list(_json_list(r[1]) if r else []) + list(src_turns or [])
                    if isinstance(t, (int, float))})
    conn.execute(
        "INSERT INTO sections (channel_id, page_id, section, owner, body, src_turns, "
        "updated_turn, hash, derived_hash) VALUES (?,?,?,'play',?,?,?,?,'') "
        "ON CONFLICT(channel_id, page_id, section) DO UPDATE SET owner='play', "
        "body=excluded.body, src_turns=excluded.src_turns, updated_turn=excluded.updated_turn, "
        "hash=excluded.hash",
        (channel_id, page_id, "History", body, json.dumps(turns, ensure_ascii=False),
         turn, _sha1(body)))


def append_play_item(channel_id: str, page_id: str, section: str, line: str, *,
                     src_turn: Optional[int] = None, turn: Optional[int] = None,
                     reason: str = "") -> dict:
    """항목 절에 한 줄 append(편의 래퍼). 같은 줄은 중복 0.

    [2026-09-16 시트 2차] 텍스트 절은 `Observed` 하나만 받는다(grow_sheet 관찰 착지).
    중복 = `_norm_quote` 정규화 후 기존 본문에 포함, 순서 = `WIKI_OBSERVED_KEEP_ORDER`
    (F1 병합과 같은 규칙 — new_first면 앞에). 그 밖 텍스트 절은 종전대로 not_item 거부."""
    if not _is_item_section(section):
        if section != "Observed":
            return _reject(page_id, section, "append", "not_item")
        ln = str(line or "").strip()
        if not ln:
            return _reject(page_id, section, "append", "empty")
        cur = [s for s in get_sections(channel_id, page_id) if s["section"] == section]
        old = (cur[0]["body"] if cur else "") or ""
        if _norm_q(ln) and _norm_q(ln) in _norm_q(old):
            return {"ok": True, "revision": -1, "hash": cur[0]["hash"] if cur else "", "dup": True}
        if not old.strip():
            body = ln
        elif str(getattr(config, "WIKI_OBSERVED_KEEP_ORDER", "new_first")) == "new_first":
            body = ln + "\n" + old.strip()
        else:
            body = old.rstrip() + "\n" + ln
        return write_play_section(channel_id, page_id, section, body,
                                  src_turns=[src_turn] if src_turn is not None else [],
                                  turn=turn, reason=reason, op="append")
    ln = str(line or "").strip()
    if not ln:
        return _reject(page_id, section, "append", "empty")
    cur = [s for s in get_sections(channel_id, page_id) if s["section"] == section]
    old = cur[0]["body"] if cur else ""
    if ln in {x.strip() for x in old.splitlines() if x.strip()}:
        return {"ok": True, "revision": -1, "hash": cur[0]["hash"] if cur else "", "dup": True}
    body = (old.rstrip() + "\n" + ln) if old.strip() else ln
    return write_play_section(channel_id, page_id, section, body,
                              src_turns=[src_turn] if src_turn is not None else [],
                              turn=turn, reason=reason, op="append")


# =========================================================
# 4.4 인물 시트 (2026-09-16 시트 2차 — PC 페이지·원문 절·성장 마커)
#   설계 relation_unify_design §8·§9·§13. 쓰기는 전부 set_lore_sections / write_play_section 경유.
# =========================================================

def pc_page_id(channel_id: str, uid: Any) -> Optional[str]:
    """참가자 uid → PC 페이지 id(`!가면`이 만든 것만). 없으면 None — 페이지·성장·관찰 대상 아님."""
    if not channel_id or uid is None or not str(uid).strip():
        return None
    conn = _conn(channel_id)
    if conn is None:
        return None
    try:
        r = conn.execute("SELECT page_id FROM pages WHERE channel_id=? AND owner_uid=? LIMIT 1",
                         (channel_id, str(uid))).fetchone()
        return r[0] if r else None
    except Exception as e:
        logger.warning(f"[Wiki] pc_page_id 실패 (무시): {e}")
        return None


def ensure_pc_page(channel_id: str, uid: Any, mask: str, *, turn: Optional[int] = None) -> Optional[str]:
    """`!가면` 진입 — PC 인물 페이지 생성/개명(멱등). id는 uid에 묶인다(가면을 바꿔도 같은 페이지,
    옛 가면은 별칭으로). 이름 정규화·별칭 병합은 ensure_page와 같은 규칙."""
    if not getattr(config, "WIKI_PAGES", False):
        return None
    if not channel_id or uid is None or not str(uid).strip() or not str(mask or "").strip():
        return None
    conn = _conn(channel_id)
    if conn is None:
        return None
    mask = str(mask).strip()
    try:
        pid = pc_page_id(channel_id, uid) or page_id_for("character", f"pc:{uid}")
        row = conn.execute("SELECT name, aliases FROM pages WHERE channel_id=? AND page_id=?",
                           (channel_id, pid)).fetchone()
        if row is None:
            conn.execute(
                "INSERT INTO pages (channel_id, page_id, kind, name, aliases, source, status, "
                "created_turn, updated_turn, owner_uid, built_len) VALUES (?,?,?,?,?,?,?,?,?,?,0)",
                (channel_id, pid, "character", mask, "[]", "pc", "", turn, turn, str(uid)))
        else:
            old_name = row[0] or ""
            add = [old_name] if old_name and _norm_name(old_name) != _norm_name(mask) else []
            merged = _merge_aliases(_json_list(row[1]), add, exclude=mask)
            conn.execute("UPDATE pages SET name=?, aliases=?, owner_uid=?, updated_turn=COALESCE(?, updated_turn) "
                         "WHERE channel_id=? AND page_id=?",
                         (mask, json.dumps(merged, ensure_ascii=False), str(uid), turn, channel_id, pid))
        conn.commit()
        return pid
    except Exception as e:
        logger.warning(f"[Wiki] ensure_pc_page 실패 (무시): {channel_id}/{uid}: {e}")
        return None


def get_lore_sections(channel_id: str, page_id: str) -> Dict[str, str]:
    """lore 절 {절 이름: 본문} (enum 순서).

    [2026-09-16 2차b 추기] status='deleted' 페이지는 **{}** — 삭제 표식이 lore 절보다 우선(레티어스: 부활 없음).
    동명 세션 NPC가 나중에 생겨도 옛 원문·manual 출처를 물려받지 않는다. 되살리는 길은
    `set_lore_sections`(작가 원문 재등록)뿐."""
    if _page_deleted(channel_id, page_id):
        return {}
    kind = _kind_of(channel_id, page_id)
    order = {s: i for i, s in enumerate(_lore_enum(kind))}
    rows = get_sections(channel_id, page_id, owner="lore")
    rows.sort(key=lambda r: order.get(r["section"], 999))
    return {r["section"]: r.get("body") or "" for r in rows if (r.get("body") or "").strip()}


def _page_deleted(channel_id: str, page_id: str) -> bool:
    conn = _conn(channel_id)
    if conn is None or not page_id:
        return False
    try:
        row = conn.execute("SELECT status FROM pages WHERE channel_id=? AND page_id=?",
                           (channel_id, page_id)).fetchone()
        return bool(row) and str(row[0] or "") == "deleted"
    except Exception:
        return False


def has_lore_sections(channel_id: str, page_id: str) -> bool:
    """원문 절 유무 — 승격·`source` 파생의 **유일한** 판정(시트 2차b: description 쪽 판정 삭제)."""
    return bool(page_id) and bool(get_lore_sections(channel_id, page_id))


# [2026-09-22 voice_seed §H] 시드 도장. `_npc_sheet_gate`가 시드 원문을 앉힐 때 페이지 source에
#   찍는 값이고, 그 도장이 붙어 있는 한 lore 절이 **있어도** 작가 시트가 아니다(= NPC source는 session).
#   사람이 `!npc 시트 … set` 으로 손대면 그 쓰기가 "manual"로 덮는다 = 자연 승격.
SEED_SOURCE = "seed"


def page_source(channel_id: str, page_id: str) -> str:
    """페이지 source 도장 한 번 읽기. 페이지 없으면 ""."""
    p = get_page(channel_id, page_id) if page_id else None
    return str((p or {}).get("source") or "")


def is_authored_page(channel_id: str, page_id: str) -> bool:
    """**작가 시트인가** — lore 절이 있고 ∧ 그 절이 시드 도장이 아닌가 (배선 스펙 §H).

    `has_lore_sections`(절 유무)와 갈라진 이유: 시드도 lore 절에 앉기 때문이다. 절 유무만으로
    판정하면 세션 NPC가 시드를 받는 순간 manual로 승격돼 동결·태그 제외까지 따라온다(§H 기각안 H-3).
    승격 판정(`derive_npc_source`)은 이 함수를, 절 유무 자체가 필요한 자리는 종전 함수를 쓴다."""
    if not has_lore_sections(channel_id, page_id):
        return False
    return page_source(channel_id, page_id) != SEED_SOURCE


def store_available(channel_id: str) -> bool:
    """채널 위키 저장소(sqlite) 연결 가능 여부. 쓰기 관문이 "원문 없음"과 "저장소 장애"를 가르는 데 쓴다."""
    return bool(channel_id) and _conn(channel_id) is not None


def assemble_lore_text(sections: Dict[str, str]) -> str:
    """lore 절 dict → 원문 문자열(읽기 파생). 절 이름을 `### 절` 헤더로, 본문은 그대로.

    `map_sheet_sections`가 enum 밖 원 절에 붙인 `#### 원헤더`는 본문에 들어 있으므로 그대로 살아난다
    (보존 규칙의 역방향). 입력 순서(= `get_lore_sections`의 enum 순서)를 지킨다. 빈 dict = ""."""
    parts = []
    for name, body in (sections or {}).items():
        b = str(body or "").strip()
        if b:
            parts.append(f"### {name}\n{b}")
    return "\n\n".join(parts)


_LORE_MAP_SECTIONS = "sections"
_LORE_MAP_SOURCE = "source"


def lore_map_entry(entry: Any) -> tuple:
    """`character_lore_map` 값 한 칸 → `(절 dict, page source)`. **두 모양 다 받는다**.

    `with_source=True`로 부른 값은 `{"sections": {...}, "source": "..."}`이고, 종전 모양은 절 dict
    그대로다(source는 ""). 판정 코드가 모양을 몰라도 되게 여기 한 곳에서 편다 — 절 이름 enum에
    `sections`/`source`가 없으므로 두 모양은 섞이지 않는다."""
    if (isinstance(entry, dict) and isinstance(entry.get(_LORE_MAP_SECTIONS), dict)
            and _LORE_MAP_SOURCE in entry):
        return entry[_LORE_MAP_SECTIONS], str(entry.get(_LORE_MAP_SOURCE) or "")
    return (entry if isinstance(entry, dict) else {}), ""


def character_lore_map(channel_id: str, *,
                       with_source: bool = False) -> Optional[Dict[str, Dict[str, Any]]]:
    """채널의 인물(character) 페이지 lore 절 전량 — {page_id: {절: 본문}}(enum 순서). **쿼리 1회**.

    `domain_manager.get_npcs()`가 NPC마다 `description`을 조립할 때 쓴다(NPC 수만큼 조회하지 않게).
    실패 = **None**(행 0 = {}와 구분 — 호출자가 "원문 없음"으로 오판해 source를 session으로 떨구지 않게).

    [2026-09-22 voice_seed §H] `with_source=True`면 값이 `{"sections": ..., "source": page.source}` —
    같은 한 번의 조인에서 도장까지 실어 온다(조회 순증 0). 읽기 뷰의 승격 판정이 `is_authored_page`와
    **같은 식**(절 있음 ∧ 도장≠seed)을 쓰려면 도장이 필요한데, 그걸 NPC마다 따로 읽으면
    채널당 1회라는 이 함수의 존재 이유가 무너진다. 꺼낼 때는 `lore_map_entry`."""
    conn = _conn(channel_id)
    if conn is None:
        return None
    try:
        rows = conn.execute(
            "SELECT s.page_id, s.section, s.body, COALESCE(p.source,'') FROM sections s JOIN pages p "
            "ON p.channel_id=s.channel_id AND p.page_id=s.page_id "
            "WHERE s.channel_id=? AND s.owner='lore' AND p.kind='character' "
            "AND COALESCE(p.status,'')!='deleted'",
            (channel_id,)).fetchall()
    except Exception as e:
        logger.warning(f"[Wiki] character_lore_map 실패 (무시): {e}")
        return None
    order = {s: i for i, s in enumerate(_lore_enum("character"))}
    tmp: Dict[str, list] = {}
    src: Dict[str, str] = {}
    for pid, sec, body, psrc in rows:
        if str(body or "").strip():
            tmp.setdefault(pid, []).append((order.get(sec, 999), sec, body))
            src[pid] = str(psrc or "")
    out = {pid: {sec: body for _, sec, body in sorted(v)} for pid, v in tmp.items()}
    if not with_source:
        return out
    return {pid: {_LORE_MAP_SECTIONS: secs, _LORE_MAP_SOURCE: src.get(pid, "")}
            for pid, secs in out.items()}


def sheet_sections_from_text(text: str) -> Dict[str, str]:
    """시트 원문 → lore 절 dict. NPC와 같은 파서(`_parse_sections` → `map_sheet_sections`),
    절을 하나도 못 잡으면 원문 통째를 `Notes`로(파싱 실패해도 원문은 원천에 남는다)."""
    t = str(text or "").strip()
    if not t:
        return {}
    try:
        from npc_manager import _parse_sections
        mapped = map_sheet_sections(_parse_sections(t))
    except Exception:
        mapped = {}
    if not any(str(v or "").strip() for v in mapped.values()):
        return {"Notes": t}
    return mapped


def set_sheet_text(channel_id: str, page_id: str, text: str, turn: Optional[int] = None, *,
                   as_notes: bool = False) -> int:
    """시트 원문 → 페이지 lore 절(전량 교체, play 절 무접촉). Returns 쓴 절 수(같은 원문이면 0).
    `as_notes` = AI 분석 실패 폴백 — 절로 가르지 않고 원문 통째를 `Notes`에."""
    t = str(text or "").strip()
    secs = ({"Notes": t} if t else {}) if as_notes else sheet_sections_from_text(text)
    if not secs or not page_id:
        return 0
    return set_lore_sections(channel_id, page_id, secs, _sha1(str(text or "").strip()), turn)


def edit_lore_section(channel_id: str, page_id: str, section: str, body: str, *,
                      action: str = "set", turn: Optional[int] = None) -> bool:
    """OOC 절 편집 — lore 절 한 칸만 set/append/remove, 나머지 절은 그대로 다시 쓴다.
    OOC 편집용(현재 호출은 PC 페이지). NPC lore 절도 정본이라 여기서 고쳐도 덮이지 않는다(시트 2차b)."""
    kind = _kind_of(channel_id, page_id)
    if not kind or section not in _lore_enum(kind):
        return False
    # [2026-09-24 감사] get_lore_sections는 status='deleted'면 {}를 돌려준다(읽기 가림). 그걸 기준으로
    #   한 절만 고쳐 전량 교체하면 나머지 lore 절이 전부 지워졌다 → 삭제 표식과 무관하게 실제 행을 읽는다.
    cur = {r["section"]: r.get("body") or "" for r in get_sections(channel_id, page_id, owner="lore")
           if (r.get("body") or "").strip()}
    val = str(body or "").strip()
    if action == "append":
        cur[section] = (((cur.get(section) or "").rstrip() + "\n" + val).strip()) if val else cur.get(section, "")
    elif action == "remove":
        cur.pop(section, None)
    else:
        cur[section] = val
    cur = {k: v for k, v in cur.items() if str(v or "").strip()}
    dh = _sha1(json.dumps(cur, ensure_ascii=False, sort_keys=True))
    if not cur:
        conn = _conn(channel_id)
        if conn is None:
            return False
        try:
            # lore 절 전량 교체의 빈 경우(set_lore_sections는 빈 dict면 절을 지우기만 한다).
            return set_lore_sections(channel_id, page_id, {}, dh, turn) == 0
        except Exception:
            return False
    return set_lore_sections(channel_id, page_id, cur, dh, turn) > 0


def get_built_len(channel_id: str, page_id: str) -> int:
    p = get_page(channel_id, page_id) if page_id else None
    return int((p or {}).get("built_len") or 0)


def set_built_len(channel_id: str, page_id: str, n: int) -> bool:
    """grow_sheet 정리 마커 — 페이지 메타 하나(구 PC·NPC 필드 마커 둘을 대체)."""
    conn = _conn(channel_id)
    if conn is None or not page_id:
        return False
    try:
        conn.execute("UPDATE pages SET built_len=? WHERE channel_id=? AND page_id=?",
                     (int(n or 0), channel_id, page_id))
        conn.commit()
        return True
    except Exception as e:
        logger.warning(f"[Wiki] set_built_len 실패 (무시): {e}")
        return False


def get_play_body(channel_id: str, page_id: str, section: str = "Observed") -> tuple:
    """(본문, hash). 없으면 ("", "")."""
    for s in get_sections(channel_id, page_id, owner="play"):
        if s["section"] == section:
            return s.get("body") or "", s.get("hash") or ""
    return "", ""


# =========================================================
# 4.5 F1 절 패치 (W2) — 게이트는 전부 여기, LLM 판정자 0
# =========================================================

# 인용 부호 쌍. **정규식을 안 쓴다** — 이 파일은 이름·인용 정규화 규칙을 새로 만들지 않는다
# (W1 계약: 정규화는 S0 `_norm_quote` 재사용, 낱말 경계 병 자리 6번째 금지).
_QUOTE_PAIRS = (('"', '"'), ("\u201c", "\u201d"))
_QUOTE_INNER_MAX = 400

_PATCH_COUNT_KEYS = ("ok", "rej_nopage", "rej_lore", "rej_hash", "rej_size", "rej_cap", "rej_op")
# write_play_section의 사유 → 영수증 칸. 합 불변식(ok + rej_* == 패치 수)을 지키려고
# 낯선 사유는 rej_op로 떨어뜨린다(사유 문자열은 로그 한 줄에 그대로 남는다).
_WRITE_REASON_MAP = {"not_play": "rej_lore", "hash": "rej_hash", "size": "rej_size",
                     "no_page": "rej_nopage", "no_conn": "rej_nopage"}


def _norm_q(s: str) -> str:
    """인용 대조 정규화 = S0 `fermentation._norm_quote` 재사용(새 규칙 0)."""
    try:
        from fermentation import _norm_quote
        return _norm_quote(s or "")
    except Exception:
        import re as _re
        import unicodedata as _ud
        return _re.sub(r"\s+", " ", _ud.normalize("NFKC", s or "")).strip()


def _quote_spans(content: str) -> list:
    """따옴표 구간 [(start, end, inner)] — 겹치는 구간은 앞선 것만 남긴다."""
    spans = []
    for op, cl in _QUOTE_PAIRS:
        i = 0
        while True:
            a = content.find(op, i)
            if a < 0:
                break
            b = content.find(cl, a + 1)
            if b < 0:
                break
            inner = content[a + 1:b]
            if inner and "\n" not in inner and len(inner) <= _QUOTE_INNER_MAX:
                spans.append((a, b + 1, inner))
            i = b + 1
    spans.sort()
    out = []
    last = -1
    for a, b, inner in spans:
        if a < last:
            continue
        out.append((a, b, inner))
        last = b
    return out


def _strip_unmatched_quotes(content: str, sources: list) -> tuple:
    """따옴표 구간을 청크 원문과 substring 대조. 미일치는 **부호만 벗긴다**(드롭 아님).

    요약은 본디 패러프레이즈다 — 인용 부호가 "원문 그대로"라는 거짓 신호를 내는 것만 막는다.
    Returns: (새 content, 벗긴 개수)
    """
    if not content:
        return content, 0
    spans = _quote_spans(content)
    if not spans:
        return content, 0
    norms = [_norm_q(t) for t in (sources or []) if t]
    parts = []
    cut = 0
    stripped = 0
    for a, b, inner in spans:
        q = _norm_q(inner)
        if q and any(q in n for n in norms):
            continue
        parts.append(content[cut:a])
        parts.append(inner)
        cut = b
        stripped += 1
    if not stripped:
        return content, 0
    parts.append(content[cut:])
    return "".join(parts), stripped


def _split_sents(text: str) -> list:
    """[2026-09-14 S5b E14] 문장 분할 = S0/S5b `fermentation._split_sentences` 재사용(규칙 하나).

    실패해도 동작은 이어진다 — 통짜 한 문장으로 본다(= 종전 통째 교체와 같은 결과)."""
    try:
        from fermentation import _split_sentences
        return _split_sentences(text or "")
    except Exception:
        t = (text or "").strip()
        return [t] if t else []


def take_quote_from_body(body: str, quote: str) -> tuple:
    """[2026-09-16 3차 조각 발췌] 인용 `quote`가 본문에 있으면 그 문장(들)을 **빼낸** 본문을 돌려준다.

    대조 = `_norm_q` 정규화 후 부분문자열(새 규칙 0). 뺄 문장 = 인용과 겹치는 문장(`_split_sents`,
    인용 ⊇ 문장 또는 문장 ⊇ 인용). 줄 구조는 유지하고 빈 줄은 버린다. 쓰기 0(순수 함수).
    Returns: (new_body, taken) — 미일치면 (body 그대로, "").
    """
    q = _norm_q(quote)
    src = str(body or "")
    if len(q) < 2 or q not in _norm_q(src):
        return src, ""
    out_lines, taken = [], []
    for ln in src.splitlines():
        keep = []
        for s in _split_sents(ln):
            ns = _norm_q(s)
            if ns and len(ns) >= 2 and (ns in q or q in ns):
                taken.append(s.strip())
            else:
                keep.append(s.strip())
        joined = " ".join(k for k in keep if k)
        if joined.strip():
            out_lines.append(joined)
    if not taken:
        return src, ""
    return "\n".join(out_lines), " ".join(taken)


def append_observed_tail(channel_id: str, page_id: str, line: str, *,
                         turn: Optional[int] = None, reason: str = "fragment_return") -> dict:
    """[2026-09-16 3차] Observed 절 **끝**에 한 줄 되돌림(조각 OOC 삭제의 역연산). 중복 = `_norm_q` 포함."""
    ln = str(line or "").strip()
    if not ln:
        return _reject(page_id, "Observed", "append", "empty")
    old, h = get_play_body(channel_id, page_id, "Observed")
    if _norm_q(ln) and _norm_q(ln) in _norm_q(old):
        return {"ok": True, "revision": -1, "hash": h, "dup": True}
    body = (old.rstrip() + "\n" + ln) if old.strip() else ln
    return write_play_section(channel_id, page_id, "Observed", body, turn=turn,
                              reason=reason, op="append")


def _merge_observed(old_body: str, content: str, retract: Any) -> tuple:
    """[2026-09-14 S5b E14] Observed 가산 병합. 누락은 보존, `retract`만 뺀다(그 외 삭제 0).

    Returns: (merged_body, add, keep, retracted, retract_miss)
    """
    new_s = _split_sents(content)
    old_s = _split_sents(old_body)
    new_keys = {_norm_q(x) for x in new_s if _norm_q(x)}

    drop = set()
    rhit = rmiss = 0
    for r in (retract if isinstance(retract, list) else []):
        rk = _norm_q(r if isinstance(r, str) else "")
        if not rk:
            rmiss += 1
            continue
        hit = False
        for o in old_s:
            ok_ = _norm_q(o)
            if ok_ and (rk in ok_ or ok_ in rk):
                drop.add(ok_)
                hit = True
        rhit += 1 if hit else 0
        rmiss += 0 if hit else 1

    keep = [o for o in old_s
            if _norm_q(o) and _norm_q(o) not in new_keys and _norm_q(o) not in drop]

    order_new_first = str(getattr(config, "WIKI_OBSERVED_KEEP_ORDER", "new_first")) == "new_first"

    def _join(kp):
        parts = (new_s + kp) if order_new_first else (kp + new_s)
        return " ".join(parts)

    body = _join(keep)
    # [2026-09-16 레티어스] 오래된 문장 탈락(tmax) 폐지 — 저장 캡 없음. 길이는 정리 콜이 관리.
    return body, len(new_s), len(keep), rhit, rmiss


def apply_patches(channel_id: str, patches: Any, notes: Any,
                  chunk_entries: Any, turn: Optional[int] = None) -> dict:
    """F1 `page_patches` 적용. 게이트 순서대로 첫 실패 사유로 계수한다(설계 §4).

      ① notes에 없는 page → rej_nopage  ② play enum 밖 section → rej_lore
      ③ base_hash 불일치 → rej_hash     ④ 페이지당 상한 초과 → rej_cap
      ⑤ 인용 게이트(미일치는 부호만 벗김, quote_stripped) ⑥ op 어긋남 → rej_op
      ⑦ W1 write_play_section(expected_hash CAS·src_turns·reason="F1") 사유 그대로

    `notes`는 이 발효가 F1에 준 노트 그대로 — **모델이 못 본 페이지는 못 고친다**.
    """
    counts = {k: 0 for k in _PATCH_COUNT_KEYS}
    counts["quote_stripped"] = 0
    # [2026-09-14 W5] 모음 줄로 온 패치 — 내용은 버리고 계수만(잎은 일시적). 순서 끝 두 칸.
    counts["agg_hit"] = 0
    counts["promoted"] = 0
    # [2026-09-14 S5b] E14 병합 계수 + E12 미검증 표시 계수. 플래그 OFF면 전부 0으로 남는다.
    counts["merge_add"] = 0
    counts["merge_keep"] = 0
    counts["merge_retract"] = 0
    counts["retract_miss"] = 0
    counts["ungrounded"] = 0
    if not getattr(config, "WIKI_PATCHES", False):
        return counts
    if not isinstance(patches, list) or not patches:
        logger.info("[Wiki] patches " + " ".join(f"{k}={counts[k]}" for k in _PATCH_COUNT_KEYS)
                    + f" quote_stripped={counts['quote_stripped']}"
                    + f" agg_hit={counts['agg_hit']} promoted={counts['promoted']}"
                + (f" merge add={counts['merge_add']} keep={counts['merge_keep']}"
                   f" retract={counts['merge_retract']} retract_miss={counts['retract_miss']}"
                   f" ungrounded={counts['ungrounded']}"
                   if getattr(config, "MEMORY_LINEAGE", False) else ""))
        return counts

    by_name: Dict[str, dict] = {}
    for n in (notes or []):
        if isinstance(n, dict) and n.get("page"):
            by_name[_norm_name(str(n["page"]))] = n

    srcs = [str(e.get("content") or "") for e in (chunk_entries or []) if isinstance(e, dict)]
    src_turns = sorted({int(e["turn"]) for e in (chunk_entries or [])
                        if isinstance(e, dict) and isinstance(e.get("turn"), int)})
    if not src_turns and turn is not None:
        src_turns = [int(turn)]

    per_page: Dict[str, int] = {}
    cap = int(getattr(config, "WIKI_PATCH_MAX_PER_PAGE", 3))

    for raw in patches:
        if not isinstance(raw, dict):
            counts["rej_op"] += 1
            _reject("?", "?", "?", "op")
            continue
        name = str(raw.get("page") or "")
        section = str(raw.get("section") or "")
        op = str(raw.get("op") or "").strip().lower()
        note = by_name.get(_norm_name(name))
        # ① 노트에 없는 페이지
        if note is None:
            counts["rej_nopage"] += 1
            _reject(name or "?", section or "?", op or "?", "nopage")
            continue
        page_id = str(note.get("page_id") or "")
        # [2026-09-14 W5] 모음 줄로 온 패치 — 페이지에 **안 쓴다**. 계수만 올리고 내용은 버린다
        #   (잎은 일시적; 뿌리로 승격되면 그때부터 절이 쌓인다). 임계에 닿으면 그 자리에서
        #   승격하고 **이번 패치를 그 페이지에 정상 적용**한다(아래 게이트 ②~⑦ 그대로).
        if note.get("aggregate"):
            counts["agg_hit"] += 1
            _aknd = str(note.get("kind") or "location")
            _hits = tally_hit(channel_id, _aknd, name, turn)
            if _hits < int(getattr(config, "WIKI_PROMOTE_MIN_PATCHES", 3)):
                continue
            page_id = promote(channel_id, _aknd, name, reason="patches", turn=turn) or ""
            if not page_id:
                continue
            counts["promoted"] += 1
        kind = _kind_of(channel_id, page_id)
        # ② play enum 밖(= lore 절이거나 없는 절)
        if not kind or section not in _play_enum(kind):
            counts["rej_lore"] += 1
            _reject(page_id or name, section or "?", op or "?", "not_play")
            continue
        # ③ base_hash CAS — 노트에 적힌 hash를 그대로 돌려줘야 한다(절이 없으면 빈 절 기준)
        note_secs = note.get("sections") if isinstance(note.get("sections"), dict) else {}
        expected = str((note_secs.get(section) or {}).get("hash") or "")
        got = raw.get("base_hash")
        got = "" if got is None else str(got)
        if got != expected:
            counts["rej_hash"] += 1
            _reject(page_id, section, op or "?", "hash")
            continue
        # ④ 페이지당 절 상한
        per_page[page_id] = per_page.get(page_id, 0) + 1
        if per_page[page_id] > cap:
            counts["rej_cap"] += 1
            _reject(page_id, section, op or "?", "cap")
            continue
        # ⑤ 인용 게이트 — 미일치 인용은 부호만 벗기고 통과(드롭 0)
        content, n_str = _strip_unmatched_quotes(str(raw.get("content") or ""), srcs)
        counts["quote_stripped"] += n_str
        # ⑥ op 규약 — 텍스트 절은 upsert만, 항목 절은 append만
        want = "append" if _is_item_section(section) else "upsert"
        if op != want:
            counts["rej_op"] += 1
            _reject(page_id, section, op or "?", "op")
            continue
        # ⑦ 쓰기 — 항목 절은 기존 줄 뒤에 붙이고 중복 줄은 버린다(삭제 0)
        if want == "append":
            cur = [x for x in get_sections(channel_id, page_id) if x["section"] == section]
            old = (cur[0]["body"] if cur else "") or ""
            have = {ln.strip() for ln in old.splitlines() if ln.strip()}
            add = []
            for ln in content.splitlines():
                t = ln.strip()
                if t and t not in have:
                    have.add(t)
                    add.append(t)
            if not add:
                counts["ok"] += 1
                logger.info(f"[Wiki] patch page={page_id} section={section} op={op} accepted")
                continue
            body = (old.rstrip() + "\n" + "\n".join(add)) if old.strip() else "\n".join(add)
        else:
            body = content
            # [2026-09-14 S5b E14] Observed는 가산적 — 모델이 안 쓴 문장을 프로그램이 보존한다.
            #   기준 본문은 **DB 전체**(get_sections)지 노트 발췌가 아니다. 게이트 ①~⑥ 불변.
            if (section == "Observed"
                    and getattr(config, "MEMORY_LINEAGE", False)
                    and getattr(config, "WIKI_OBSERVED_ADDITIVE", False)):
                _cur = [x for x in get_sections(channel_id, page_id) if x["section"] == section]
                _old = (_cur[0]["body"] if _cur else "") or ""
                body, _a, _b, _c, _d = _merge_observed(_old, content, raw.get("retract"))
                counts["merge_add"] += _a
                counts["merge_keep"] += _b
                counts["merge_retract"] += _c
                counts["retract_miss"] += _d
                logger.info(f"[Wiki] patch page={page_id} section={section} "
                            f"merge add={_a} keep={_b} retract={_c} retract_miss={_d}")
        res = write_play_section(channel_id, page_id, section, body,
                                 src_turns=src_turns, turn=turn, reason="F1",
                                 expected_hash=expected, op=op)
        if res.get("ok"):
            counts["ok"] += 1
            # [2026-09-14 S5b E12] 미검증 블록에서 온 패치는 표시만 달고 적용된다(거부 아님).
            _mk = str(getattr(config, "MEMORY_UNGROUNDED_MARK", "(미검증)"))
            if (getattr(config, "MEMORY_LINEAGE", False) and _mk
                    and content.lstrip().startswith(_mk)):
                counts["ungrounded"] += 1
                logger.info(f"[Wiki] patch page={page_id} section={section} "
                            f"op={op} accepted(ungrounded)")
        else:
            counts[_WRITE_REASON_MAP.get(str(res.get("reason") or ""), "rej_op")] += 1

    logger.info("[Wiki] patches " + " ".join(f"{k}={counts[k]}" for k in _PATCH_COUNT_KEYS)
                + f" quote_stripped={counts['quote_stripped']}"
                + f" agg_hit={counts['agg_hit']} promoted={counts['promoted']}"
                + (f" merge add={counts['merge_add']} keep={counts['merge_keep']}"
                   f" retract={counts['merge_retract']} retract_miss={counts['retract_miss']}"
                   f" ungrounded={counts['ungrounded']}"
                   if getattr(config, "MEMORY_LINEAGE", False) else ""))
    return counts


def revert_sections_after(channel_id: str, from_turn: int) -> int:
    """`src_turns`에 `>= from_turn`이 있는 **play 절**을 직전 리비전으로 복원(없으면 절 삭제).

    W2 되감기(`!다시`)가 쓴다 — W1은 함수와 스모크만 둔다(반사실 실패는 관측 안 됨 →
    쓰는 자리가 생길 때까지 게이트를 걸지 않고 코드를 먼저 넣는다, §4).

    [2026-09-14 W5] **승격으로 생긴 페이지는 되감지 않는다** — 페이지 행은 절이 아니고
    여기는 `sections`만 본다. 승격은 `!클리어`가 치운다(source='play')."""
    conn = _conn(channel_id)
    if conn is None:
        return 0
    # [2026-09-24 감사] 판정 기준을 src_turns(출처 턴)에서 **쓰기 시점 턴**으로 바꾼다.
    #   ① F1 패치는 src_turns=발효 청크의 옛 턴이라 `!다시` 구간에 쓰였어도 안 잡혔고
    #   ② 잡혀도 절당 최신 리비전 1개만 pop — 구간 안 쓰기가 둘 이상이면 중간 상태에 멈췄다.
    #   section_history 행 = "쓰기 W 직전 본문", 행.turn = W의 턴. 그러므로 turn >= from_turn 인 **가장 이른**
    #   리비전의 본문이 구간 이전 상태다 → 그 본문으로 복원하고 그 리비전 이후 행을 전부 지운다(= 역순 전부 복원).
    #   리비전이 아예 없는 절은 생성 쓰기뿐이므로 updated_turn(쓰기 턴)·src_turns 중 하나라도 구간이면 삭제(종전 규약).
    #   복원 후 남은 리비전이 없고 복원본 src_turns가 구간이면 그 본문 자체가 구간 안에서 생긴 것 → 삭제.
    #   한계: 강등(_append_history_lines)은 리비전을 안 남겨 되감지 못한다(종전과 동일).
    try:
        rows = conn.execute(
            "SELECT page_id, section, src_turns, updated_turn FROM sections "
            "WHERE channel_id=? AND owner='play'",
            (channel_id,)).fetchall()
        n = 0

        def _in_window(turns) -> bool:
            return any(isinstance(t, (int, float)) and t >= from_turn for t in turns)

        for page_id, section, raw, upd in rows:
            hist = conn.execute(
                "SELECT id, revision, body, src_turns, turn FROM section_history "
                "WHERE channel_id=? AND page_id=? AND section=? ORDER BY revision ASC",
                (channel_id, page_id, section)).fetchall()
            post = [h for h in hist if isinstance(h[4], (int, float)) and h[4] >= from_turn]
            if post:
                first = post[0]
                prev = [h for h in hist if h[1] < first[1]]
                if not prev and _in_window(_json_list(first[3])):
                    conn.execute("DELETE FROM sections WHERE channel_id=? AND page_id=? AND section=?",
                                 (channel_id, page_id, section))
                    logger.info(f"[Wiki] revert page={page_id} section={section} -> removed")
                else:
                    conn.execute(
                        "UPDATE sections SET body=?, src_turns=?, updated_turn=?, hash=? "
                        "WHERE channel_id=? AND page_id=? AND section=?",
                        (first[2], first[3], prev[-1][4] if prev else None, _sha1(first[2]),
                         channel_id, page_id, section))
                    logger.info(f"[Wiki] revert page={page_id} section={section} -> rev{first[1] - 1} "
                                f"(undo {len(hist) - len(prev)})")
                conn.execute("DELETE FROM section_history WHERE channel_id=? AND page_id=? AND section=? "
                             "AND revision>=?", (channel_id, page_id, section, first[1]))
                n += 1
            elif not hist and ((isinstance(upd, (int, float)) and upd >= from_turn)
                               or _in_window(_json_list(raw))):
                conn.execute("DELETE FROM sections WHERE channel_id=? AND page_id=? AND section=?",
                             (channel_id, page_id, section))
                logger.info(f"[Wiki] revert page={page_id} section={section} -> removed")
                n += 1
        conn.commit()
        return n
    except Exception as e:
        logger.warning(f"[Wiki] revert_sections_after 실패 (무시): {e}")
        return 0


# =========================================================
# 4b. 수명 — !클리어 (2026-09-14 W4)
# =========================================================

def clear_play(channel_id: str) -> dict:
    """`!클리어` = play만 지운다. lore 절·lore 페이지는 생존(설계 §6).

    한 트랜잭션으로 ① `sections` owner='play' 전량 ② `section_history` 전량(파생 리비전은
    play 절의 그림자라 같이 간다) ③ `pages` 중 `source NOT IN ('lore','manual')`.
    페이지 정책은 **npcs와 같다** — `delete_npcs_except_sources(("lore","manual"))`와 한 짝이라
    같은 어휘를 쓴다(NPC 쓰기 관문이 파생 `source`를 `ensure_page`로 도장 찍는다).
    남은(lore·manual) 페이지의 `updated_turn`은 건드리지 않는다 — 시트는 바뀐 게 없다.

    Returns {"sections": a, "history": b, "pages": c}. 실패해도 예외 0(계수 0 반환).
    """
    out = {"sections": 0, "history": 0, "pages": 0, "tally": 0}
    conn = _conn(channel_id)
    if conn is None:
        return out
    try:
        a = conn.execute("SELECT COUNT(*) FROM sections WHERE channel_id=? AND owner='play'",
                         (channel_id,)).fetchone()
        b = conn.execute("SELECT COUNT(*) FROM section_history WHERE channel_id=?",
                         (channel_id,)).fetchone()
        c = conn.execute("SELECT COUNT(*) FROM pages WHERE channel_id=? "
                         "AND source NOT IN ('lore','manual') AND owner_uid=''",
                         (channel_id,)).fetchone()
        out["sections"] = int(a[0] if a else 0)
        out["history"] = int(b[0] if b else 0)
        out["pages"] = int(c[0] if c else 0)
        # [2026-09-14 W5] 모음 줄 패치 계수 — play의 그림자라 같이 간다(승격 이력 0에서 재시작).
        try:
            d = conn.execute("SELECT COUNT(*) FROM wiki_tally WHERE channel_id=?",
                             (channel_id,)).fetchone()
            out["tally"] = int(d[0] if d else 0)
            conn.execute("DELETE FROM wiki_tally WHERE channel_id=?", (channel_id,))
        except Exception:
            out["tally"] = 0
        conn.execute("DELETE FROM sections WHERE channel_id=? AND owner='play'", (channel_id,))
        conn.execute("DELETE FROM section_history WHERE channel_id=?", (channel_id,))
        # [2026-09-16 시트 2차] PC 페이지(owner_uid)는 참가자와 수명이 같다 — 클리어 생존(play만 비움).
        conn.execute("DELETE FROM pages WHERE channel_id=? AND source NOT IN ('lore','manual') "
                     "AND owner_uid=''", (channel_id,))
        conn.execute("UPDATE pages SET built_len=0 WHERE channel_id=?", (channel_id,))
        # 사라진 페이지의 절 쓸기 — 지워지는 페이지는 source∉{lore,manual}(= lore 절 없는 세션 인물)라
        # 주인 없이 남으면 클리어마다 쌓이기만 한다. 살아남은 페이지의 절은 건드릴 수 없다
        # (WHERE가 pages에 없는 page_id만 잡는다) → "lore 절·lore 페이지 삭제 금지"와 충돌 0.
        _orph = conn.execute(
            "DELETE FROM sections WHERE channel_id=? AND page_id NOT IN "
            "(SELECT page_id FROM pages WHERE channel_id=?)", (channel_id, channel_id)).rowcount
        conn.commit()
        if _orph:
            logger.debug(f"[Wiki] clear orphan lore sections={_orph}")
        logger.info(f"[Wiki] clear sections={out['sections']} history={out['history']} "
                    f"pages={out['pages']} tally={out['tally']}")
        # [2026-09-14 W3b] cache.db(절 벡터)는 재생성 가능한 파생물 — 클리어면 통째로 간다.
        try:
            import sqlite_store as _ss_w3b
            _ss_w3b.delete_cache_db(channel_id)
        except Exception:
            pass
        return out
    except Exception as e:
        try:
            conn.rollback()
        except Exception:
            pass
        logger.warning(f"[Wiki] clear_play 실패 (무시): {channel_id}: {e}")
        return {"sections": 0, "history": 0, "pages": 0, "tally": 0}


# =========================================================
# 4b-2. 절 벡터 캐시 — cache.db (2026-09-14 W3b)
#   본문은 복사하지 않는다(hash + vec만). 절 본문의 정본은 여전히 sections 하나.
#   numpy 0 — float32는 표준 라이브러리 `array('f')` 바이트로 저장한다.
# =========================================================

def _cache_conn(channel_id: str):
    """cache.db 연결. 실패는 None(예외 안 던짐)."""
    try:
        import sqlite_store
        if not sqlite_store.ensure_cache_schema(channel_id):
            return None
        return sqlite_store.get_cache_conn(channel_id)
    except Exception as e:
        logger.debug(f"[Wiki] cache conn 실패 (무시): {channel_id}: {e}")
        return None


def _vec_to_blob(vec) -> bytes:
    from array import array
    return array("f", [float(x) for x in (vec or ())]).tobytes()


def _blob_to_vec(blob) -> list:
    from array import array
    if not blob:
        return []
    try:
        a = array("f")
        a.frombytes(bytes(blob))
        return list(a)
    except Exception:
        return []


def vec_live_sections(channel_id: str) -> Dict[tuple, tuple]:
    """살아 있는 절 전부 -> {(page_id, section): (hash, body)}.

    dead(status='deleted') 페이지 제외, 빈 본문 제외. lore 절도 **대상**이다 —
    벡터는 시드(어느 페이지를 부를까)용이고 컴파일 출력은 여전히 play 절만이다."""
    conn = _conn(channel_id)
    if conn is None:
        return {}
    try:
        rows = conn.execute(
            "SELECT s.page_id, s.section, s.hash, s.body FROM sections s "
            "JOIN pages p ON p.channel_id=s.channel_id AND p.page_id=s.page_id "
            "WHERE s.channel_id=? AND COALESCE(p.status,'') != 'deleted'",
            (channel_id,)).fetchall()
        out = {}
        for page_id, section, h, body in rows:
            body = (body or "").strip()
            if not body:
                continue
            out[(str(page_id), str(section))] = (str(h or "") or _sha1(body), body)
        return out
    except Exception as e:
        logger.debug(f"[Wiki] vec_live_sections 실패 (무시): {e}")
        return {}


def vec_get_all(channel_id: str) -> Dict[tuple, tuple]:
    """저장된 절 벡터 전부 -> {(page_id, section): (hash, vec)}."""
    conn = _cache_conn(channel_id)
    if conn is None:
        return {}
    try:
        rows = conn.execute(
            "SELECT page_id, section, hash, vec FROM section_vectors").fetchall()
        return {(str(r[0]), str(r[1])): (str(r[2] or ""), _blob_to_vec(r[3]))
                for r in rows}
    except Exception as e:
        logger.debug(f"[Wiki] vec_get_all 실패 (무시): {e}")
        return {}


def vec_put_many(channel_id: str, rows: Any, *, model: str = "") -> int:
    """rows = [(page_id, section, hash, vec), ...]. 빈 벡터는 **저장 0**(다음 턴 재시도)."""
    conn = _cache_conn(channel_id)
    if conn is None:
        return 0
    import time as _t
    now = _t.time()
    n = 0
    try:
        for r in (rows or []):
            try:
                page_id, section, h, vec = r[0], r[1], r[2], r[3]
            except Exception:
                continue
            if not vec:
                continue
            conn.execute(
                "INSERT OR REPLACE INTO section_vectors "
                "(page_id, section, hash, model, dim, vec, updated_at) VALUES (?,?,?,?,?,?,?)",
                (str(page_id), str(section), str(h or ""), str(model or ""),
                 len(vec), _vec_to_blob(vec), now))
            n += 1
        conn.commit()
        return n
    except Exception as e:
        logger.debug(f"[Wiki] vec_put_many 실패 (무시): {e}")
        return n


def vec_delete_missing(channel_id: str, live_keys: Any) -> int:
    """살아 있는 키 집합에 없는 절 벡터 삭제(삭제·되감긴 절 정리). Returns 지운 행 수."""
    conn = _cache_conn(channel_id)
    if conn is None:
        return 0
    try:
        live = {(str(k[0]), str(k[1])) for k in (live_keys or ())}
        rows = conn.execute("SELECT page_id, section FROM section_vectors").fetchall()
        gone = [(str(a), str(b)) for a, b in rows if (str(a), str(b)) not in live]
        for a, b in gone:
            conn.execute("DELETE FROM section_vectors WHERE page_id=? AND section=?", (a, b))
        if gone:
            conn.commit()
        return len(gone)
    except Exception as e:
        logger.debug(f"[Wiki] vec_delete_missing 실패 (무시): {e}")
        return 0


# =========================================================
# 4c. 장소·세력 — 뿌리만 페이지, 잎은 모음의 한 줄 (2026-09-14 W5)
#   설계: 지시서 wiki_w5_places_factions_todo. LLM 콜 0·명령 0·정규식 0.
#   한 문장: **페이지는 부모·뿌리에만 준다** — 잎은 일시적이라 `장소 모음`/`세력 모음`
#   페이지의 `Entries` 한 줄로 살고, 뿌리가 되거나(자식이 붙거나) 로어에 등록되거나
#   발효 패치를 `WIKI_PROMOTE_MIN_PATCHES`회 받으면 그때 페이지가 생긴다(승격).
#   강등 0 — 페이지는 남고 `!클리어`가 play 것만 지운다.
# =========================================================

_AGG_ROOT_LABEL = "(뿌리)"
_AGG_LINE_MAX = 4000


def _places_on() -> bool:
    return bool(getattr(config, "WIKI_PAGES", False) and getattr(config, "WIKI_PLACES", False))


def _agg_name(kind: str) -> str:
    return str((getattr(config, "WIKI_AGGREGATE_PAGES", {}) or {}).get(kind) or "")


def ensure_aggregate(channel_id: str, kind: str) -> Optional[str]:
    """`장소 모음`·`세력 모음` concept 페이지(멱등). source='lore' = `!클리어` 생존.

    Entries 줄은 owner='play'라 클리어가 지운다 — **잎은 일시적**이라는 판정 그대로."""
    name = _agg_name(kind)
    if not name:
        return None
    pid = ensure_page(channel_id, "concept", name, source="lore")
    if not pid:
        return None
    label = "장소" if kind == "location" else "세력"
    body = f"플레이에서 등장한 {label}. 뿌리·부모는 개별 페이지 [[이름]]으로."
    set_lore_sections(channel_id, pid, {"Body": body}, _sha1(body))
    return pid


def _agg_split_children(line: str) -> list:
    """`- {parent}: a, b, c` → ['a','b','c']. 정규식 0(구분자 고정)."""
    if ":" not in line:
        return []
    tail = line.split(":", 1)[1]
    return [c.strip() for c in tail.split(",") if c.strip()]


def _agg_bare(child: str) -> str:
    """`[[이름]]` → `이름`(승격 표시를 벗긴 비교용 키)."""
    c = str(child or "").strip()
    if c.startswith("[[") and c.endswith("]]"):
        c = c[2:-2].strip()
    return c


def _agg_entries_lines(channel_id: str, page_id: str) -> list:
    for row in get_sections(channel_id, page_id, owner="play"):
        if row.get("section") == "Entries":
            return [ln for ln in (row.get("body") or "").split("\n") if ln.strip()]
    return []


def _write_entries(channel_id: str, page_id: str, lines: list, *,
                   turn: Optional[int] = None, reason: str = "") -> dict:
    body = "\n".join([ln for ln in lines if ln.strip()])
    return write_play_section(channel_id, page_id, "Entries", body,
                              src_turns=[turn] if turn is not None else [],
                              turn=turn, reason=reason or "W5", op="append")


def upsert_aggregate_line(channel_id: str, kind: str, parent_name: str, child_name: str, *,
                          turn: Optional[int] = None) -> dict:
    """모음 `Entries`에 `- {parent}: {child…}` 한 줄. 같은 parent 줄은 **교체**(부모당 한 줄).

    부모당 한 줄이라 `WIKI_ITEM_SECTION_MAX`(60) 강등 계약 안에 자연히 들어온다."""
    if not _places_on():
        return {"ok": False, "reason": "off"}
    child = str(child_name or "").strip()
    if not child:
        return {"ok": False, "reason": "empty"}
    pid = ensure_aggregate(channel_id, kind)
    if not pid:
        return {"ok": False, "reason": "no_page"}
    parent = str(parent_name or "").strip() or _AGG_ROOT_LABEL
    prefix = f"- {parent}:"
    lines = _agg_entries_lines(channel_id, pid)
    kept, cur = [], []
    for ln in lines:
        if ln.strip().startswith(prefix):
            cur = _agg_split_children(ln)
        else:
            kept.append(ln)
    have = {_norm_name(_agg_bare(c)) for c in cur}
    if _norm_name(child) not in have:
        cur.append(child)
    while len(cur) > 1 and len(prefix) + 1 + len(", ".join(cur)) > _AGG_LINE_MAX:
        cur.pop(0)                      # 줄 상한 — 오래된 자식부터 탈락(삭제 0 예외: 줄 하나)
    kept.append(prefix + " " + ", ".join(cur))
    return _write_entries(channel_id, pid, kept, turn=turn, reason="W5-agg")


def aggregate_has(channel_id: str, kind: str, name: str) -> bool:
    """모음 Entries의 자식 목록에 이 이름이 있나(승격 전 잎인가)."""
    if not _places_on():
        return False
    pid = page_id_for("concept", _agg_name(kind)) if _agg_name(kind) else ""
    if not pid or get_page(channel_id, pid) is None:
        return False
    q = _norm_name(name)
    for ln in _agg_entries_lines(channel_id, pid):
        for c in _agg_split_children(ln):
            if _norm_name(_agg_bare(c)) == q:
                return True
    return False


def location_page_eligible(node: Any) -> bool:
    """개별 페이지 자격 = 타입이 region/area이고 (로어 등록이거나 자식이 있는) 노드."""
    if not isinstance(node, dict):
        return False
    if str(node.get("type") or "") not in tuple(getattr(config, "WIKI_LOC_PAGE_TYPES", ()) or ()):
        return False
    tags = ((node.get("properties") or {}).get("tags") or [])
    auto = "auto_detected" in [str(t) for t in tags]
    return (not auto) or bool(node.get("children"))


def sync_location_page(channel_id: str, node: Any, *, source: str = "play",
                       parent_name: str = "", turn: Optional[int] = None) -> Optional[str]:
    """eligible이면 location 페이지 + lore 절 `Identity`(derived_hash), 아니면 모음 한 줄."""
    if not _places_on() or not isinstance(node, dict):
        return None
    name = str(node.get("name") or "").strip()
    if not name:
        return None
    if not location_page_eligible(node):
        upsert_aggregate_line(channel_id, "location", parent_name, name, turn=turn)
        return None
    pid = ensure_page(channel_id, "location", name,
                      aliases=list(node.get("aliases") or []), source=source, turn=turn)
    if not pid:
        return None
    desc = str(node.get("description") or "").strip()
    risk = str(((node.get("properties") or {}).get("risk")) or "").strip()
    body = desc
    if risk:
        body = (body + "\n" if body else "") + f"위험: {risk}"
    if body:
        set_lore_sections(channel_id, pid, {"Identity": body}, _sha1(body), turn=turn)
    return pid


def sync_faction_page(channel_id: str, faction: Any, *, turn: Optional[int] = None) -> Optional[str]:
    """로어 세력 → faction 페이지 + lore 절 `Identity`/`Stance`. 재등록은 derived_hash로 무갱신."""
    if not _places_on() or not isinstance(faction, dict):
        return None
    name = str(faction.get("name") or "").strip()
    if not name:
        return None
    pid = ensure_page(channel_id, "faction", name, source="lore", turn=turn)
    if not pid:
        return None
    secs = {}
    d = str(faction.get("desc") or faction.get("description") or "").strip()
    st = str(faction.get("stance") or "").strip()
    if d:
        secs["Identity"] = d
    if st:
        secs["Stance"] = st
    if secs:
        set_lore_sections(channel_id, pid, secs,
                          _sha1("\x1f".join(f"{k}={v}" for k, v in sorted(secs.items()))), turn=turn)
    return pid


def promote(channel_id: str, kind: str, name: str, *, reason: str = "",
            turn: Optional[int] = None) -> Optional[str]:
    """모음 줄의 잎 → 개별 페이지. 멱등(이미 페이지면 no-op). 모음 줄은 `[[이름]]`으로 바뀐다."""
    if not _places_on():
        return None
    nm = str(name or "").strip()
    if not nm:
        return None
    exist = resolve_page(channel_id, nm, kind=kind)
    if exist:
        return exist
    pid = ensure_page(channel_id, kind, nm, source="play", turn=turn)
    if not pid:
        return None
    # 모음 줄 안의 그 이름을 링크로 — 줄은 남고(잎 목록의 계보) 내용은 페이지로 간다.
    apid = page_id_for("concept", _agg_name(kind)) if _agg_name(kind) else ""
    if apid and get_page(channel_id, apid) is not None:
        q = _norm_name(nm)
        lines, hit = [], False
        for ln in _agg_entries_lines(channel_id, apid):
            if ":" not in ln:
                lines.append(ln)
                continue
            head, tail = ln.split(":", 1)
            outc = []
            for c in [x.strip() for x in tail.split(",") if x.strip()]:
                if _norm_name(_agg_bare(c)) == q and not c.startswith("[["):
                    outc.append(f"[[{_agg_bare(c)}]]")
                    hit = True
                else:
                    outc.append(c)
            lines.append(head + ": " + ", ".join(outc) if outc else head + ":")
        if hit:
            _write_entries(channel_id, apid, lines, turn=turn, reason="W5-promote")
    logger.info(f"[Wiki] promote kind={kind} name={nm} reason={reason or '?'}")
    return pid


# --- 모음 줄 패치 계수(wiki_tally) — 내용은 안 담는다 --------------------------

def tally_hit(channel_id: str, kind: str, name: str, turn: Optional[int] = None) -> int:
    """모음 줄이 패치를 한 번 받았다. 누적 hits 반환(실패 0)."""
    conn = _conn(channel_id)
    if conn is None:
        return 0
    try:
        key = _norm_name(name)
        conn.execute(
            "INSERT INTO wiki_tally (channel_id, kind, name, hits, last_turn) VALUES (?,?,?,1,?) "
            "ON CONFLICT(channel_id, kind, name) DO UPDATE SET hits=hits+1, last_turn=excluded.last_turn",
            (channel_id, kind, key, turn))
        conn.commit()
        r = conn.execute("SELECT hits FROM wiki_tally WHERE channel_id=? AND kind=? AND name=?",
                         (channel_id, kind, key)).fetchone()
        return int(r[0]) if r else 0
    except Exception as e:
        logger.warning(f"[Wiki] tally_hit 실패 (무시): {name}: {e}")
        return 0


def get_tally(channel_id: str, kind: str, name: str) -> int:
    conn = _conn(channel_id)
    if conn is None:
        return 0
    try:
        r = conn.execute("SELECT hits FROM wiki_tally WHERE channel_id=? AND kind=? AND name=?",
                         (channel_id, kind, _norm_name(name))).fetchone()
        return int(r[0]) if r else 0
    except Exception:
        return 0


def extra_entity_names(channel_id: str, text: str = "", *, max_factions: int = 3) -> list:
    """[2026-09-14 W5] 이번 턴의 장소·세력 이름 = `[현재 위치 노드명] + [본문에 나온 세력 이름]`.

    새 콜 0·새 계산 0 — 턴로그 `extra`(§2)와 프롬프트 배선(§5)이 **같은 함수**를 쓴다.
    세력 후보 = 로어 `lore_summary_data.factions[*].name` + 승격된 faction 페이지 이름.
    2자 미만은 버린다(한 글자 부분일치는 오탐 밭). 대조는 S0 `_norm_quote`(NFKC+공백접기).
    """
    out: List[str] = []
    if not _places_on() or not channel_id:
        return out
    try:
        import domain_manager as _dm5
        loc = str(_dm5.get_current_location(channel_id) or "").strip()
        if loc:
            try:
                import world_tree as _wt5
                nid = _wt5.resolve_node_id(channel_id, loc)
                if nid:
                    loc = str(((_wt5.get_all_nodes(channel_id) or {}).get(nid) or {}).get("name") or loc)
            except Exception:
                pass
            out.append(loc)
    except Exception as e:
        logger.debug(f"[Wiki] extra location skipped: {e}")
    try:
        body = _norm_q(str(text or ""))
        if not body:
            return out
        cand: List[str] = []
        try:
            import domain_manager as _dm5
            for f in ((_dm5.get_domain(channel_id) or {}).get("lore_summary_data", {}) or {}).get("factions", []) or []:
                nm = str((f or {}).get("name") or "").strip() if isinstance(f, dict) else str(f or "").strip()
                if nm:
                    cand.append(nm)
        except Exception:
            pass
        for pg in list_pages(channel_id, "faction"):
            nm = str(pg.get("name") or "").strip()
            if nm:
                cand.append(nm)
        seen = set()
        nf = 0
        for nm in cand:
            if len(nm) < 2:
                continue
            k = _norm_name(nm)
            if not k or k in seen:
                continue
            seen.add(k)
            if _norm_q(nm) and _norm_q(nm) in body:
                out.append(nm)
                nf += 1
            if nf >= max_factions:
                break
    except Exception as e:
        logger.debug(f"[Wiki] extra factions skipped: {e}")
    return out


def _resolve_any_kind(channel_id: str, name: str) -> Optional[str]:
    """이름 → page_id. W5 ON이면 kind 순회 `character → location → faction`(첫 해결)."""
    if not _places_on():
        return resolve_page(channel_id, name, kind="character")
    for k in ("character", "location", "faction"):
        pid = resolve_page(channel_id, name, kind=k)
        if pid:
            return pid
    return None


def _location_projection(channel_id: str, page_name: str) -> Dict[str, str]:
    """저장 0 투영 — `Occupants`(npcs_present, dead 제외) · `Layout`(connections + risk/atmosphere).

    world_tree가 정본이라 절로 **저장하지 않는다**(정본 둘 금지). 컴파일 직전에만 만든다."""
    out: Dict[str, str] = {}
    try:
        import world_tree
        nid = world_tree.resolve_node_id(channel_id, page_name)
        if not nid:
            return out
        node = (world_tree.get_all_nodes(channel_id) or {}).get(nid) or {}
        names = [str(n).strip() for n in (node.get("npcs_present") or []) if str(n or "").strip()]
        if names:
            try:
                import domain_manager as _dm_w5
                import npc_manager as _nm_w5
                npcs = (_dm_w5.get_domain(channel_id) or {}).get("npcs", {}) or {}
                alive = []
                for n in names:
                    d = npcs.get(n)
                    if isinstance(d, dict) and _nm_w5.get_npc_status(d) == "dead":
                        continue
                    alive.append(n)
                names = alive
            except Exception:
                pass
        if names:
            out["Occupants"] = ", ".join(names)
        nodes = world_tree.get_all_nodes(channel_id) or {}
        lines = []
        for c in (node.get("connections") or [])[:6]:
            if not isinstance(c, dict):
                continue
            tgt = (nodes.get(str(c.get("target_id") or "")) or {}).get("name") or ""
            if not tgt:
                continue
            d = str(c.get("direction") or "").strip()
            lines.append(f"\u2192 {tgt}({d})" if d else f"\u2192 {tgt}")
        props = node.get("properties") or {}
        rk = str(props.get("risk") or "").strip()
        at = str(props.get("atmosphere") or "").strip()
        if rk or at:
            lines.append("/".join([x for x in (rk, at) if x]))
        if lines:
            out["Layout"] = "\n".join(lines)
    except Exception as e:
        logger.debug(f"[Wiki] projection skipped: {page_name}: {e}")
    return out


# =========================================================
# 5. 렌더
# =========================================================

def render_page(channel_id: str, page_id: str, sections: Optional[list] = None, *,
                excerpt_chars: int = 600) -> str:
    """`## 이름` + 요청한 절 순서대로 `### 절` + 발췌. 없는 절은 생략. 투영 절(State)은 W3."""
    page = get_page(channel_id, page_id)
    if page is None:
        return ""
    have = {s["section"]: s for s in get_sections(channel_id, page_id)}
    if sections is None:
        kind = page.get("kind", "")
        order = list(_lore_enum(kind)) + list(_play_enum(kind))
        wanted = [s for s in order if s in have] + [s for s in have if s not in order]
    else:
        wanted = [s for s in sections if s in have]
    out = [f"## {page.get('name', '')}"]
    for sec in wanted:
        body = have[sec].get("body") or ""
        if not body.strip():
            continue
        if excerpt_chars and len(body) > excerpt_chars:
            body = body[:excerpt_chars].rstrip() + "…"
        out.append(f"### {sec}\n{body}")
    return "\n\n".join(out)


# =========================================================
# 5b. 컴파일러 — 독자별 play 절 분배 (W3a, 설계 §5 / 지시서 §2)
#   LLM 0콜. 직접 시드(이름)만 — 벡터 시드·두 레인 예산은 W3b.
#   lore 절은 기존 NPC 라인(Slot 7 get_npc_renderer_profiles / Theoria _build_npc_context)이
#   이미 주고 있으므로 여기선 **play 절만** 얹는다(이중 투입 금지).
# =========================================================

# 독자 공통 머리 한 줄(상수). S1 Slot 9 계약과 겹치지 않게 한 문장, 블록 맨 위에 1회.
WIKI_COMPILE_HEAD = (
    "아래는 플레이 중 확립된 사실이다(로어 아님). "
    "현재 장면·입력이 우선하며, 여기 없는 일이 없었던 일은 아니다."
)


def _compile_empty() -> dict:
    return {"text": "", "pages": [], "chars": 0, "omitted": 0, "sections": 0}


def _compile_section_excerpt(section: str, body: str, excerpt_chars: int,
                             history_links: int) -> str:
    """절 하나의 발췌. 텍스트 절 = 앞부분 절단, 항목 절 = **최신 줄부터** 담는다."""
    body = (body or "").strip()
    if not body:
        return ""
    if not _is_item_section(section):
        if excerpt_chars and len(body) > excerpt_chars:
            return body[:excerpt_chars].rstrip() + "\u2026"
        return body
    lines = [ln for ln in body.split("\n") if ln.strip()]
    if not lines:
        return ""
    cap_lines = history_links if section == "History" else len(lines)
    kept: List[str] = []
    used = 0
    for ln in reversed(lines):            # 최신 줄부터
        if len(kept) >= cap_lines:
            break
        if excerpt_chars and kept and (used + 1 + len(ln)) > excerpt_chars:
            break
        kept.append(ln)
        used += len(ln) + 1
    kept.reverse()                        # 담은 것들끼리는 원래 순서(오래된 -> 최신)
    return "\n".join(kept)


def compile_for(channel_id: str, reader: str, names: Any, *,
                exclude_sections: Any = ()) -> dict:
    """독자(reader) 프로파일대로 이름 목록의 페이지 play 절을 한 블록으로 컴파일한다.

    입력 이름 순서 보존, 모호·미존재·dead 페이지는 skip. 총 상한 초과는
    **페이지 단위로 통째 탈락**(절 중간 절단 금지) + `omitted` 계수.
    예외는 전부 삼킨다 -> `{"text": ""}`."""
    try:
        if not getattr(config, "WIKI_PAGES", False):
            return _compile_empty()
        if not getattr(config, "WIKI_COMPILE", False):
            return _compile_empty()
        prof = (getattr(config, "WIKI_COMPILE_PROFILES", {}) or {}).get(reader)
        if not prof:
            return _compile_empty()
        order = [str(x) for x in (prof[0] or ())]
        excerpt_chars = int(prof[1])
        total_cap = int(prof[2])
        _ex = {str(x) for x in (exclude_sections or ())}
        order = [s for s in order if s not in _ex]
        if not order:
            return _compile_empty()
        max_pages = int(getattr(config, "WIKI_COMPILE_MAX_PAGES", 6))
        history_links = int(getattr(config, "WIKI_COMPILE_HISTORY_LINKS", 3))

        # ① 이름 -> 페이지 (순서 보존, 중복 제거, 상한)
        targets = []
        seen = set()
        for nm in (names or []):
            if not isinstance(nm, str) or not nm.strip():
                continue
            if len(targets) >= max_pages:
                break
            pid = _resolve_any_kind(channel_id, nm)
            if not pid or pid in seen:
                continue
            page = get_page(channel_id, pid)
            if page is None:
                continue
            if str(page.get("status") or "") == "deleted":
                continue
            seen.add(pid)
            targets.append((pid, page))
        # [2026-09-24 감사] `if not targets: return _compile_empty()` 삭제 — 직접 시드가 0이면 아래 ③ 벡터 시드
        #   (바닥 WIKI_LANE_FLOOR 전량이 남는, 가장 필요한 경우)까지 못 가고 돌아갔다. 빈 결과 판정은 끝의 `if not blocks`.

        # ② 페이지마다 프로파일 절 순서대로 play 절만
        blocks: List[str] = []
        pages_out: List[str] = []
        used = 0
        omitted = 0
        sec_count = 0

        def _render(_pid, _page, _score_by_sec=None):
            """페이지 하나 -> (block, n_sec). 담을 게 없으면 ("", 0).

            `_score_by_sec`가 있으면(벡터 시드) **점수 높은 절 우선** — 프로파일이 허용한
            절 안에서만 순서를 바꾼다. None이면 종전 프로파일 순서 그대로(바이트 동일)."""
            have = {r["section"]: r for r in get_sections(channel_id, _pid, owner="play")}
            # [2026-09-14 W5] location 투영 절(저장 0) — 렌더 직전에만 끼운다. 총 상한 계산 포함.
            if _places_on() and str(_page.get("kind") or "") == "location":
                for _psec, _pbody in _location_projection(channel_id, str(_page.get("name") or "")).items():
                    if _psec in order and _pbody:
                        have[_psec] = {"section": _psec, "body": _pbody}
            _secs = order
            if _score_by_sec:
                _secs = sorted(
                    order,
                    key=lambda _s: (-float(_score_by_sec.get(_s, -1.0)), order.index(_s)))
            lines: List[str] = []
            n_sec = 0
            for sec in _secs:
                row = have.get(sec)
                if not row:
                    continue
                txt = _compile_section_excerpt(sec, row.get("body") or "",
                                               excerpt_chars, history_links)
                if not txt:
                    continue
                lines.append("### " + sec)
                lines.append(txt)
                n_sec += 1
            if not lines:
                return "", 0
            return (("[" + str(_page.get("name", "")) + " \u2014 플레이에서 확립된 것]\n"
                     ) + "\n".join(lines)), n_sec

        for pid, page in targets:
            block, n_sec = _render(pid, page)
            if not block:
                continue
            if total_cap and (used + len(block)) > total_cap:
                omitted += 1              # 페이지 통째 탈락 (절 중간 절단 금지)
                continue
            used += len(block) + 2
            blocks.append(block)
            pages_out.append(str(page.get("name", "")))
            sec_count += n_sec

        # ③ [2026-09-14 W3b] 벡터 시드 — 직접 시드가 **바닥**(WIKI_LANE_FLOOR)을 다 못 쓰면
        #   남은 바닥만큼만 유사 절 페이지를 더 담는다. 직접 시드를 밀어내지 않고(레인 예약),
        #   프로파일 천장(total_cap)도 그대로. 캐시 결측은 결측이지 실패가 아니다(vec=0).
        n_direct = len(pages_out)
        direct_chars = used
        n_vec = 0
        lane_floor = 0
        if getattr(config, "WIKI_VECTORS", False):
            lane_floor = int((getattr(config, "WIKI_LANE_FLOOR", {}) or {}).get(reader, 0) or 0)
            vec_budget = max(lane_floor - used, 0)
            if total_cap:
                vec_budget = min(vec_budget, max(total_cap - used, 0))
            if vec_budget > 0:
                try:
                    import fermentation as _fm_w3b
                    cache = _fm_w3b._wiki_similarity_cache.get(channel_id) or {}
                except Exception:
                    cache = {}
                min_score = float(getattr(config, "WIKI_VEC_MIN_SCORE", 0.12))
                rel_min = float(getattr(config, "MEMORY_LADDER_MIN_REL_SCORE", 0.6))
                top_pages = int(getattr(config, "WIKI_VEC_TOP_PAGES", 4))
                page_best: Dict[str, float] = {}
                sec_scores: Dict[str, Dict[str, float]] = {}
                for _k, _sc in (cache or {}).items():
                    try:
                        _pid_k, _sec_k, _sc = str(_k[0]), str(_k[1]), float(_sc)
                    except Exception:
                        continue
                    if _sc < min_score or _pid_k in seen:
                        continue          # 직접 시드와 중복 0
                    if _sc > page_best.get(_pid_k, -1.0):
                        page_best[_pid_k] = _sc
                    _d = sec_scores.setdefault(_pid_k, {})
                    if _sc > _d.get(_sec_k, -1.0):
                        _d[_sec_k] = _sc
                cands = sorted(page_best.items(), key=lambda x: (-x[1], x[0]))
                _top = cands[0][1] if cands else 0.0
                for _i, (_pid_k, _sc) in enumerate(cands):
                    if n_vec >= top_pages:
                        break
                    if _i and _sc < _top * rel_min:
                        break             # 상대 게이트(S3와 같은 규칙)
                    _pg = get_page(channel_id, _pid_k)
                    if _pg is None or str(_pg.get("status") or "") == "deleted":
                        continue
                    block, n_sec = _render(_pid_k, _pg, sec_scores.get(_pid_k))
                    if not block:
                        continue
                    if (used - direct_chars) + len(block) > vec_budget:
                        omitted += 1      # 페이지 통째 탈락 (절 중간 절단 금지)
                        continue
                    used += len(block) + 2
                    blocks.append(block)
                    pages_out.append(str(_pg.get("name", "")))
                    sec_count += n_sec
                    seen.add(_pid_k)
                    n_vec += 1

        if not blocks:
            return {"text": "", "pages": [], "chars": 0, "omitted": omitted, "sections": 0}
        text = WIKI_COMPILE_HEAD + "\n\n" + "\n\n".join(blocks)
        _tail = ""
        if getattr(config, "WIKI_VECTORS", False):
            _tail = (f" direct={n_direct} vec={n_vec} lane_used={used} floor={lane_floor}")
        logger.info(
            f"[Wiki] compile reader={reader} pages={len(pages_out)} sections={sec_count} "
            f"chars={len(text)} omitted={omitted}" + _tail
        )
        out = {"text": text, "pages": pages_out, "chars": len(text),
               "omitted": omitted, "sections": sec_count}
        if getattr(config, "WIKI_VECTORS", False):
            out.update({"seeds_direct": n_direct, "seeds_vec": n_vec,
                        "lane_used": used, "lane_floor": lane_floor})
        return out
    except Exception as e:
        logger.warning(f"[Wiki] compile_for 실패 (무시): {e}")
        return _compile_empty()


# =========================================================
# 6. 로어 착지 (character) — §4
# =========================================================

# v7 정본 헤더 8종. 소문자 비교 + 복수 허용(`relationship(s)`·`secret(s)`).
_V7_EXACT = {
    "identity": "Identity", "core traits": "Core Traits", "aside": "Aside",
    "direction": "Direction", "relationship": "Relationships", "relationships": "Relationships",
    "secret": "Secrets", "secrets": "Secrets", "background": "Background", "notes": "Notes",
}
# `_section_family`(npc_manager, 5가족) → 우리 enum. 가족은 **코드가 다르게 대우하는 기능**이라
# 시트 관례가 아니라 그 기능으로 떨어뜨린다(hidden=감싸기 → Secrets, voice=목소리 재료 → Core Traits).
_FAMILY_TO_SECTION = {
    "identity": "Identity", "voice": "Core Traits", "rules": "Direction",
    "hidden": "Secrets", "background": "Background",
}


def map_sheet_sections(parsed: Dict[str, str]) -> Dict[str, str]:
    """v7 시트 파서 결과(`npc_manager._parse_sections`) → lore 절 dict. 파서는 **호출만** 한다.

    (a) 정확명이 v7 8종이면 그대로 → (b) 아니면 `_section_family` 5가족 → (c) 나머지는 `Notes`.
    enum이 아닌 이름으로 떨어진 원 절은 `#### {원헤더}`를 앞에 붙여 **원 헤더를 보존**한다
    (유실 0·가역 — 사전이 뒤처져도 내용은 온전히 남는다는 09-02 원칙의 페이지판).
    `_preamble`은 `Identity` **앞**에 놓는다(헤더 없는 도입부는 정체의 일부)."""
    try:
        from npc_manager import _normalize_section_name, _section_family
    except Exception as e:
        logger.warning(f"[Wiki] 시트 파서 import 실패 (무시): {e}")
        return {}
    out: Dict[str, str] = {}
    preamble = ""

    def _strip_header(text: str) -> str:
        lines = (text or "").split("\n")
        if lines and lines[0].lstrip().startswith("#"):
            lines = lines[1:]
        return "\n".join(lines).strip()

    def _put(target: str, header: str, body: str, exact: bool) -> None:
        if not body.strip():
            return
        chunk = body if exact else f"#### {header}\n{body}"
        out[target] = (out[target] + "\n\n" + chunk) if target in out else chunk

    for raw_name, raw_text in (parsed or {}).items():
        body = _strip_header(raw_text)
        if raw_name == "_preamble":
            preamble = (raw_text or "").strip()
            continue
        n = _normalize_section_name(raw_name)
        if n in _V7_EXACT:
            _put(_V7_EXACT[n], raw_name, body, True)
            continue
        fam = _section_family(raw_name)
        if fam in _FAMILY_TO_SECTION:
            _put(_FAMILY_TO_SECTION[fam], raw_name, body, False)
            continue
        _put("Notes", raw_name, body, False)
    if preamble:
        out["Identity"] = (preamble + "\n\n" + out["Identity"]) if "Identity" in out else preamble
    return out


def _move_tombstone_aside(conn, channel_id: str, page_id: str) -> Optional[str]:
    """[2026-09-24 감사 §5-2 #9b — 레티어스 판정] 삭제 표식 페이지(묘비)를 옆 id 로 옮긴다.

    새 인물이 같은 이름을 넘겨받을 때(개명·정체 공개) — 안 옮기면 ① 새 인물 원문이 옛 원문을 덮고 페이지가
    되살아나 옛 인물의 play 절(Observed)을 물려받거나 ② 원문이 없으면 삭제 표식이 그대로라 새 인물이 안 보였다.
    내용·리비전·별칭은 그대로(삭제 0), 제목만 `이름 (삭제됨)`(겹치면 번호). 작가 원문 재등록(set_lore_sections
    부활 경로 = 같은 인물)은 여기를 안 탄다. Returns 새 page_id | None."""
    try:
        row = conn.execute("SELECT kind, name, status FROM pages WHERE channel_id=? AND page_id=?",
                           (channel_id, page_id)).fetchone()
        if not row or str(row[2] or "") != "deleted":
            return None
        kind, name = str(row[0]), str(row[1])
        n, new_pid, new_title = 1, "", ""
        while n < 100:
            new_title = f"{name} (삭제됨)" if n == 1 else f"{name} (삭제됨 {n})"
            new_pid = page_id_for(kind, new_title)
            if conn.execute("SELECT 1 FROM pages WHERE channel_id=? AND page_id=?",
                            (channel_id, new_pid)).fetchone() is None:
                break
            n += 1
        else:
            return None
        conn.execute("UPDATE pages SET page_id=?, name=? WHERE channel_id=? AND page_id=?",
                     (new_pid, new_title, channel_id, page_id))
        conn.execute("UPDATE sections SET page_id=? WHERE channel_id=? AND page_id=?",
                     (new_pid, channel_id, page_id))
        conn.execute("UPDATE section_history SET page_id=? WHERE channel_id=? AND page_id=?",
                     (new_pid, channel_id, page_id))
        conn.commit()
        logger.info(f"[Wiki] tombstone moved aside {page_id} -> {new_pid} ({new_title})")
        return new_pid
    except Exception as e:
        logger.warning(f"[Wiki] 묘비 이동 실패 (무시): {page_id}: {e}")
        return None


def rename_npc_page(channel_id: str, old_name: str, new_name: str,
                    data: Optional[Dict[str, Any]] = None) -> Optional[str]:
    """NPC 개명 → play·lore 절을 새 page_id로 **이관**, 옛 페이지 행 삭제, 별칭에 옛 이름 추가.

    [2026-09-16 시트 2차b] lore 절도 정본이라 이사시킨다(구: 파생이라 옛 복사 함수가 새로 깔았다).
    새 페이지에 lore 절이 이미 있으면 옛 lore 절은 덮지 않는다. 정본 이관이므로 `WIKI_PAGES` 게이트 없음."""
    if not channel_id or not old_name or not new_name:
        return None
    conn = _conn(channel_id)
    if conn is None:
        return None
    old_pid = page_id_for("character", old_name)
    try:
        # [2026-09-24 감사 §5-2 #9b] 새 이름 자리에 묘비가 있으면 먼저 옆으로 — 새 인물은 빈 페이지에서 시작.
        _tgt_pid = page_id_for("character", new_name)
        if _tgt_pid != old_pid:
            _move_tombstone_aside(conn, channel_id, _tgt_pid)
        d = dict(data or {})
        al = d.get("aliases")
        al = list(al) if isinstance(al, list) else []
        old_page = get_page(channel_id, old_pid)
        if old_page:
            al = list(old_page.get("aliases") or []) + al
        al.append(old_name)
        _src_new = str(d.get("source") or (old_page or {}).get("source") or "play")
        # [2026-09-22 voice_seed §H] 개명이 시드 도장을 떨어뜨리지 않게 — dict 쪽 source 는 시드 NPC 면
        #   "session"이라, 그대로 찍으면 옮겨 앉은 lore 절이 새 페이지에서 작가 시트로 읽힌다.
        if str((old_page or {}).get("source") or "") == SEED_SOURCE and _src_new not in ("lore", "manual"):
            _src_new = SEED_SOURCE
        new_pid = ensure_page(channel_id, "character", new_name, aliases=al,
                              source=_src_new, turn=d.get("updated_turn"))
        if not new_pid:
            return None
        if old_pid != new_pid and old_page is not None:
            # 새 페이지에 같은 절이 이미 있으면 옛 절은 덮지 않는다(삭제 0 — 이관은 빈자리로만).
            # [2026-09-24 감사] 그런데 건너뛴 옛 절도 아래 DELETE가 통째로 지웠다(개명 = 관찰 유실).
            #   건너뛴 절의 줄은 새 페이지 `History` 끝으로 **강등**한다(항목 절 상한 강등과 같은 함수·같은 규약).
            existing = {s["section"] for s in get_sections(channel_id, new_pid, owner="play")}
            _dem_lines: List[str] = []
            _dem_turns: list = []
            _dem_secs: List[str] = []
            for s in get_sections(channel_id, old_pid, owner="play"):
                if s["section"] in existing:
                    _dem_lines += [ln for ln in (s.get("body") or "").splitlines() if ln.strip()]
                    _dem_turns += list(s.get("src_turns") or [])
                    _dem_secs.append(s["section"])
                    continue
                conn.execute("UPDATE sections SET page_id=? WHERE channel_id=? AND page_id=? "
                             "AND section=? AND owner='play'",
                             (new_pid, channel_id, old_pid, s["section"]))
                conn.execute("UPDATE section_history SET page_id=? WHERE channel_id=? AND page_id=? "
                             "AND section=?", (new_pid, channel_id, old_pid, s["section"]))
            if _dem_lines:
                _append_history_lines(conn, channel_id, new_pid, _dem_lines, _dem_turns, d.get("updated_turn"))
                logger.info(f"[Wiki] rename collision {old_pid} -> {new_pid}: "
                            f"{','.join(_dem_secs)} demoted={len(_dem_lines)} -> History")
            # 강등된 절의 리비전은 주인(절)이 사라지므로 같이 치운다(고아 리비전이 되감기에 끼지 않게).
            for _sec in _dem_secs:
                conn.execute("DELETE FROM section_history WHERE channel_id=? AND page_id=? AND section=?",
                             (channel_id, old_pid, _sec))
            conn.commit()
            _old_lore = get_lore_sections(channel_id, old_pid)
            _lore_ok = True
            if _old_lore and not has_lore_sections(channel_id, new_pid):
                _lore_ok = set_lore_sections(channel_id, new_pid, _old_lore,
                                             _sha1(json.dumps(_old_lore, ensure_ascii=False, sort_keys=True)),
                                             d.get("updated_turn")) > 0
            if not _lore_ok:
                # [2026-09-24 감사] lore 이관 실패면 옛 원문을 지우지 않는다 — play 절만 치우고 옛 페이지는
                #   삭제 표식으로 남긴다(원문 보존·읽기 가림, 삭제 0 원칙).
                conn.execute("DELETE FROM sections WHERE channel_id=? AND page_id=? AND owner='play'",
                             (channel_id, old_pid))
                conn.execute("UPDATE pages SET status='deleted' WHERE channel_id=? AND page_id=?",
                             (channel_id, old_pid))
                conn.commit()
                logger.warning(f"[Wiki] rename page {old_pid} -> {new_pid}: lore 이관 실패 — 옛 페이지 보존(deleted)")
                return new_pid
            conn.execute("DELETE FROM sections WHERE channel_id=? AND page_id=?",
                         (channel_id, old_pid))
            conn.execute("DELETE FROM pages WHERE channel_id=? AND page_id=?", (channel_id, old_pid))
            conn.commit()
            logger.info(f"[Wiki] rename page {old_pid} -> {new_pid}")
        return new_pid
    except Exception as e:
        logger.warning(f"[Wiki] rename_npc_page 실패 (무시): {old_name}->{new_name}: {e}")
        return None


# [2026-09-14 W5] `world_tree.remove_node`엔 훅을 두지 않는다 — 장소 노드 삭제는 드물고,
#   남은 페이지는 `status` 그대로 둔다(삭제 0 원칙). 필요하면 사람이 아래를 직접 부른다.
def mark_page_deleted(channel_id: str, kind: str, name: str) -> bool:
    """NPC 삭제 경로 — 페이지는 **남긴다**(status='deleted' 표시만, 삭제 0 원칙)."""
    if not getattr(config, "WIKI_PAGES", False):
        return False
    conn = _conn(channel_id)
    if conn is None:
        return False
    try:
        pid = page_id_for(kind, name)
        conn.execute("UPDATE pages SET status='deleted' WHERE channel_id=? AND page_id=?",
                     (channel_id, pid))
        conn.commit()
        logger.info(f"[Wiki] page marked deleted page={pid}")
        return True
    except Exception as e:
        logger.warning(f"[Wiki] mark_page_deleted 실패 (무시): {e}")
        return False
