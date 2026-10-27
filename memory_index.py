# -*- coding: utf-8 -*-
"""DEEP 색인 — 이름 → 턴 → 엔트리 · 연대기 장 · 원문. [2026-10-26 C80]

스펙: 파티쳇수정 `C80_DEEP색인·연대기원경_지도·스펙_2026-10-26` §2-ㄷ.
  - 옛 DEEP 은 엔트리를 녹여 지우는 LLM 층이었다. 이제 DEEP 은 **조회 함수**다 — LLM 0, 저장 0.
  - 새로 쌓는 건 sqlite_store `turn_index`(턴마다 이름 도장 · 중요도 · 원문 구간) 한 테이블뿐이다.
  - 이름은 저장 때 원문 라벨 그대로, 조회 때 위키 `resolve_page` 로 별칭을 풀어 같은 페이지 이름들로 묶는다.

함수 넷:
  turns_for(ch, name, aliases)  → 그 이름이 찍힌 턴들(중요도 높은 → 최신 순)
  entry_for_turn(ch, turn)      → 그 턴을 덮는 발효 엔트리 (idx, entry)
  chapter_for_turn(ch, turn)    → 그 턴을 덮는 연대기 장
  raw_for_turn(ch, turn)        → 그 턴 원문 행(history_log)
읽기 실패는 전부 빈 값(None · [])이다 — 소비자는 옛 길로 폴백한다.
"""
import logging
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


def _norm(s: str) -> str:
    try:
        import wiki_store
        return wiki_store._norm_name(s)
    except Exception:
        return str(s or "").strip().lower()


def name_keys(channel_id: str, name: str, aliases=None) -> List[str]:
    """이름 + 받은 별칭 + (위키 페이지가 풀리면) 그 페이지의 제목 · 별칭. 원래 글자 그대로, 중복 없이."""
    out: List[str] = []

    def _add(x):
        x = str(x or "").strip()
        if x and x not in out:
            out.append(x)

    _add(name)
    for a in (aliases or []):
        _add(a)
    try:
        import wiki_store
        pid = wiki_store.resolve_page(channel_id, name)
        if pid:
            pg = wiki_store.get_page(channel_id, pid) or {}
            _add(pg.get("name"))
            for a in (pg.get("aliases") or []):
                _add(a)
    except Exception as e:
        logger.debug(f"[MemoryIndex] resolve skip {name}: {e}")
    return out


def turns_for(channel_id: str, name: str, aliases=None, limit: int = 20) -> List[Dict[str, Any]]:
    """그 이름(별칭 포함)이 entities 나 extra 에 찍힌 턴 행들. 정렬 = 중요도 높은 → 최신. 실패 · 없음 = []."""
    if not channel_id or not str(name or "").strip():
        return []
    keys = name_keys(channel_id, name, aliases)
    want = {_norm(k) for k in keys if _norm(k)}
    if not want:
        return []
    try:
        import sqlite_store
        rows = sqlite_store.read_turn_index(channel_id, needles=keys)
    except Exception as e:
        logger.debug(f"[MemoryIndex] turns_for read skip: {e}")
        return []
    hits = [r for r in rows
            if any(_norm(x) in want for x in list(r.get("entities") or []) + list(r.get("extra") or []))]
    hits.sort(key=lambda r: (-int(r.get("importance") or 0), -int(r.get("turn") or 0)))
    return hits[:max(0, int(limit))]


def _covers(item: Dict[str, Any], turn: int) -> bool:
    ts, te = item.get("turn_start"), item.get("turn_end")
    if not isinstance(ts, int) or ts <= 0:
        return False
    te = te if (isinstance(te, int) and te >= ts) else ts
    return ts <= turn <= te


def entry_for_turn(channel_id: str, turn: int, entries: Optional[list] = None) -> Optional[Tuple[int, Dict[str, Any]]]:
    """그 턴을 덮는 발효 엔트리 (목록 위치, 엔트리). 경계 턴 포함. 없으면 None."""
    try:
        t = int(turn)
    except Exception:
        return None
    if entries is None:
        try:
            import domain_manager
            entries = domain_manager.get_fermented_history(channel_id)
        except Exception:
            return None
    for i, e in enumerate(entries or []):
        if isinstance(e, dict) and _covers(e, t):
            return i, e
    return None


def chapter_for_turn(channel_id: str, turn: int, chapters: Optional[list] = None) -> Optional[Dict[str, Any]]:
    """그 턴을 덮는 연대기 장(레거시 장 제외 — 구간이 없다). 경계 턴 포함. 없으면 None."""
    try:
        t = int(turn)
    except Exception:
        return None
    if chapters is None:
        try:
            import domain_manager
            _n, _d = domain_manager.get_deep_memory(channel_id)
            chapters = (_d or {}).get("chapters") or []
        except Exception:
            return None
    for c in chapters or []:
        if isinstance(c, dict) and not c.get("legacy") and _covers(c, t):
            return c
    return None


def raw_for_turn(channel_id: str, turn: int, limit: int = 40, row: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    """그 턴 원문 행(오래된 → 최신). 색인 행이 없거나 구간이 비면 []. row = 이미 든 색인 행(있으면 다시 안 읽는다)."""
    try:
        import sqlite_store
        if isinstance(row, dict) and "msg_from" in row:
            r = row
        else:
            rows = sqlite_store.read_turn_index(channel_id, turn=int(turn))
            if not rows:
                return []
            r = rows[0]
        if not r.get("msg_from") or not r.get("msg_to"):
            return []
        return sqlite_store.read_history_span(channel_id, r["msg_from"], r["msg_to"], limit=limit) or []
    except Exception as e:
        logger.debug(f"[MemoryIndex] raw_for_turn skip: {e}")
        return []


# =========================================================
# [2026-10-27 C82] 성장발효 — 어느 페이지가 삭힐 원문이 쌓였나 · 잎 승격 계수
# =========================================================

_GROWTH_KINDS = ("character", "location", "faction")


def _page_keymap(channel_id: str) -> Tuple[Dict[str, str], Dict[str, Dict[str, Any]]]:
    """정규화 이름(제목 · 별칭) → page_id, page_id → 페이지. 두 페이지가 나누는 키는 버린다(모호 = 안 잡는다)."""
    import wiki_store
    pages = {p["page_id"]: p for p in wiki_store.list_pages(channel_id)
             if p.get("kind") in _GROWTH_KINDS and str(p.get("status") or "") != "deleted"}
    owners: Dict[str, set] = {}
    for pid, p in pages.items():
        for k in [p.get("name")] + list(p.get("aliases") or []):
            n = _norm(k)
            if n:
                owners.setdefault(n, set()).add(pid)
    return {n: next(iter(s)) for n, s in owners.items() if len(s) == 1}, pages


class _Resolver:
    """색인 라벨(원문 그대로) → page_id. 정확 대조(제목 · 별칭) 먼저, 없으면 위키 `resolve_page`(다른 곳과 같은 퍼지 규칙)를
    kind 순서대로. 인물 라벨은 C32 몸 · 인격(`vessel.driver_of`)을 먼저 거친다 — 빈 몸("")은 페이지 없음. 한 번 푼 라벨은 캐시."""

    def __init__(self, channel_id: str, keymap: Dict[str, str], pages: Dict[str, Dict[str, Any]]):
        self.ch, self.keymap, self.pages = channel_id, keymap, pages
        self.cache: Dict[Tuple[str, str], Optional[str]] = {}
        try:
            import vessel
            self.vessel = vessel
        except Exception:
            self.vessel = None

    def page(self, label: Any, kinds: Tuple[str, ...]) -> Optional[str]:
        lab = str(label or "").strip()
        if not lab:
            return None
        ck = (lab, kinds[0] if kinds else "")
        if ck in self.cache:
            return self.cache[ck]
        pid = None
        name = lab
        if "character" in kinds and self.vessel is not None:
            try:
                name = self.vessel.driver_of(self.ch, lab)
            except Exception:
                name = lab
        if name:
            hit = self.keymap.get(_norm(name))
            if hit and self.pages.get(hit, {}).get("kind") in kinds:
                pid = hit
            else:
                try:
                    import wiki_store
                    for k in kinds:
                        r = wiki_store.resolve_page(self.ch, name, kind=k)
                        if r and r in self.pages:
                            pid = r
                            break
                except Exception:
                    pid = None
        self.cache[ck] = pid
        return pid


def pages_due(channel_id: str) -> List[Dict[str, Any]]:
    """성장 도장 뒤 등장 턴이 문턱을 넘은 페이지들, 오래 밀린 것(삭히지 않은 첫 턴이 이른 것)부터.

    인물 = 턴 색인 entities, 장소 · 세력 = extra 에서 센다(라벨 → `_Resolver`). PC 페이지(owner_uid)는 이름이 entities 에
    안 찍히므로(PC 가면은 줄거리 목록에서 뺀다) 기준점 뒤 **모든 턴**이 그 PC 의 등장이다.
    기준점 = 성장 도장(growth_log), 도장이 없으면 페이지가 생긴 턴 바로 앞(늦게 들어온 PC · 개명으로 새로 선 페이지가 생기기
    전 턴을 몰아 받지 않게). 한 번에 삭힐 턴은 오래된 쪽 `GROWTH_MAX_TURNS` 개까지 — 나머지는 다음 성장.
    돌려주는 것: [{"page_id", "kind", "name", "is_pc", "owner_uid", "mark", "turns": [색인 행 …]}]. 실패 · 없음 = []."""
    try:
        import config
        import sqlite_store
        import wiki_store
        rows = sqlite_store.read_turn_index(channel_id)
        if not rows:
            return []
        keymap, pages = _page_keymap(channel_id)
        marks = sqlite_store.read_growth_marks(channel_id)
        thr_c = max(1, int(getattr(config, "GROWTH_TURNS", 6) or 6))
        thr_p = max(1, int(getattr(config, "GROWTH_TURNS_PLACE", 10) or 10))
        win = max(thr_c, thr_p, int(getattr(config, "GROWTH_MAX_TURNS", 12) or 12))
    except Exception as e:
        logger.debug(f"[MemoryIndex] pages_due skip: {e}")
        return []
    owner: Dict[str, str] = {}
    for pid, p in pages.items():
        if p.get("kind") == "character":
            try:
                ou = str((wiki_store.get_page(channel_id, pid) or {}).get("owner_uid") or "")
            except Exception:
                ou = ""
            if ou:
                owner[pid] = ou

    def _base(pid: str) -> int:
        if pid in marks:
            return int(marks[pid])
        ct = pages.get(pid, {}).get("created_turn")
        try:
            return max(0, int(ct) - 1) if ct is not None else 0
        except (TypeError, ValueError):
            return 0

    rv = _Resolver(channel_id, keymap, pages)
    hits: Dict[str, List[Dict[str, Any]]] = {}
    for r in rows:
        t = int(r.get("turn") or 0)
        seen = set()
        for x in (r.get("entities") or []):
            pid = rv.page(x, ("character",))
            if pid and pid not in owner:
                seen.add(pid)
        for x in (r.get("extra") or []):
            pid = rv.page(x, ("location", "faction"))
            if pid:
                seen.add(pid)
        seen |= set(owner)
        for pid in seen:
            if t > _base(pid):
                hits.setdefault(pid, []).append(r)
    out = []
    for pid, rs in hits.items():
        p = pages[pid]
        thr = thr_c if p.get("kind") == "character" else thr_p
        if len(rs) >= thr:
            rs = sorted(rs, key=lambda x: int(x.get("turn") or 0))[:win]
            out.append({"page_id": pid, "kind": p.get("kind"), "name": p.get("name"), "is_pc": pid in owner,
                        "owner_uid": owner.get(pid, ""), "mark": _base(pid), "turns": rs})
    out.sort(key=lambda d: (int(d["turns"][0].get("turn") or 0), d["page_id"]))
    return out


def leaves_to_promote(channel_id: str) -> List[Tuple[str, str]]:
    """잎(모음 줄의 장소 · 세력 — 페이지 없음) 중 턴 색인 extra 에 WIKI_PROMOTE_MIN_TURNS 턴 넘게 찍힌 것 [(kind, 이름)].
    페이지로 풀리는 라벨(퍼지 포함)은 잎이 아니다. kind 는 발효 W5 판정(`_w5_aggregate_kind`) 그대로. 조회만."""
    try:
        import config
        import sqlite_store
        import fermentation
        need = max(1, int(getattr(config, "WIKI_PROMOTE_MIN_TURNS", 3) or 3))
        keymap, pages = _page_keymap(channel_id)
        rv = _Resolver(channel_id, keymap, pages)
        counts: Dict[str, set] = {}
        label: Dict[str, str] = {}
        for r in sqlite_store.read_turn_index(channel_id):
            for x in (r.get("extra") or []):
                n = _norm(x)
                if not n or rv.page(x, ("location", "faction")):
                    continue
                counts.setdefault(n, set()).add(int(r.get("turn") or 0))
                label.setdefault(n, str(x).strip())
        out = []
        for n, ts in counts.items():
            if len(ts) >= need:
                k = fermentation._w5_aggregate_kind(channel_id, label[n])
                if k in ("location", "faction"):
                    out.append((k, label[n]))
        return out
    except Exception as e:
        logger.debug(f"[MemoryIndex] leaves_to_promote skip: {e}")
        return []


def growth_raw_text(channel_id: str, turns: List[Dict[str, Any]], names: List[str], budget: int) -> Tuple[str, List[str]]:
    """성장 콜 원문 블록 — 턴마다 `T<n>` 머리 + 원문 줄(오래된 → 최신). 예산은 턴마다 고르게 나눈다(한 턴도 안 버린다 —
    도장이 그 턴들을 지나간다). 턴 몫 안에서: 플레이어 입력 줄(산문이 아닌 줄)은 늘 싣고(줄마다 몫의 1/3 까지), 남은 몫에
    GM 산문(`GM:` — 원문 행 role Model)을 이름이 든 문단 먼저 싣는다. 전체가 예산을 넘지 않는다.
    돌려주는 것 = (블록, 인용 대조용 원문 글 목록)."""
    if not turns:
        return "", []
    per = max(120, int(budget) // max(1, len(turns)) - 8)
    keys = [k for k in (names or []) if str(k or "").strip()]
    blocks, srcs = [], []
    for tr in turns:
        rows = [r for r in (raw_for_turn(channel_id, tr.get("turn"), row=tr) or []) if isinstance(r, dict)]
        if not rows:
            continue
        srcs.extend(str(r.get("content") or "") for r in rows)
        ins = [f"{r.get('role') or '?'}: {' '.join(str(r.get('content') or '').split())}" for r in rows
               if str(r.get("role") or "") != "Model"]
        cap_in = max(60, per // 3)
        ins = [x if len(x) <= cap_in else x[:cap_in - 1] + "…" for x in ins]
        left = per - sum(len(x) + 1 for x in ins)
        paras = [p.strip() for r in rows if str(r.get("role") or "") == "Model"
                 for p in str(r.get("content") or "").split("\n") if p.strip()]
        named = [i for i, p in enumerate(paras) if any(k in p for k in keys)]
        keep, used = [], 0
        for i in named + [i for i in range(len(paras)) if i not in named]:
            if left - used <= 20:
                break
            room = left - used - 1
            txt = paras[i] if len(paras[i]) <= room else paras[i][:max(0, room - 1)] + "…"
            keep.append((i, txt))
            used += len(txt) + 1
        prose = "\n".join(t for _i, t in sorted(keep))
        body = "\n".join(ins + ([f"GM: {prose}"] if prose else []))
        blocks.append(f"T{tr.get('turn')}\n{body}")
    return "\n\n".join(blocks), srcs
