# -*- coding: utf-8 -*-
"""[2026-10-10 기록 정정] 정오표 — 플레이어가 OOC 로 바로잡은 지난 일.

원문·발효 요약·딥 기억은 **고치지 않는다**(기록이 정본). 정정은 이 원장에 한 줄씩 덧붙고,
옛 기록을 프롬프트로 꺼내는 자리가 자기 본문에 그 이름이 나오면 정정을 옆에 붙인다(정오표).
꼬리표는 `about`(인물·장소·핵심 낱말) 하나 — 턴 범위는 맞추지 않는다. 정정은 언제나 참이라 넓게 붙어도 해가 없다.
위키 Observed 만 자기 칸을 고친다(틀린 줄은 History 로 강등 — wiki_store.correct_observed).
새 LLM 콜 0. 활성 정정이 없는 채널은 모든 자리의 출력이 바이트 그대로다.
설계: 파티쳇수정/memory_lore/기록정정_지도·스펙_2026-10-10.md
"""
import logging
import re
from typing import Any, Dict, Iterable, List, Optional, Tuple

import sqlite_store

logger = logging.getLogger("Corrections")

CAP = 6
# [2026-10-18 C50 R7] 최신이 위 — 같은 일에 정정이 둘이면 위의 것(이번 수리 전에 쌓인 라이브 행 때문).
HEAD_KO = "### 정정 (플레이어가 바로잡음 — 위 기록보다 우선. 최신이 위, 같은 일에 둘이면 위의 것)"
HEAD_EN = ("### Player corrections — these override the log. Stay consistent with them; "
           "do not restate a corrected version as fact. Newest first; where two cover the same matter, the upper one holds.")
_PAREN = re.compile(r'[(\[（]')


def _norm(s: Any) -> str:
    return " ".join(str(s or "").split()).lower()


def _turn_now(channel_id: str) -> Optional[int]:
    try:
        import domain_manager
        return int((domain_manager.get_world_state(channel_id) or {}).get("turn_index", 0) or 0)
    except Exception:
        return None


def _canon_about(channel_id: str, about: Iterable[Any]) -> List[str]:
    """about 낱말 → 인물이면 정본 키로(별칭·괄호식 흡수), 아니면 그대로. 중복·1글자 제거."""
    out: List[str] = []
    try:
        import domain_manager
        npcs = domain_manager.get_npcs(channel_id) or {}
    except Exception:
        npcs = {}
    for a in about or []:
        w = str(a or "").strip()
        if len(w) < 2:
            continue
        try:
            import domain_manager
            k = domain_manager._find_npc_key(npcs, w) if npcs else None
        except Exception:
            k = None
        w = k or w
        if w not in out:
            out.append(w)
    return out


def add(channel_id: str, about: Iterable[Any], text: str, wrong: str = "", *,
        turn: Optional[int] = None, source: str = "ooc") -> Optional[Dict[str, Any]]:
    """정정 한 줄을 원장에 덧붙인다. 돌려줌: 행 dict(실패 None)."""
    text = str(text or "").strip()
    ab = _canon_about(channel_id, about)
    if not text or not ab:
        return None
    t = _turn_now(channel_id) if turn is None else int(turn)
    row = {"turn": t, "about": ab, "text": text[:300], "wrong": str(wrong or "").strip()[:300], "source": source}
    cid = sqlite_store.add_correction(channel_id, row)
    if not cid:
        return None
    row["id"] = cid
    logger.info(f"[정정] +{cid} T{t} about={ab} {text[:60]}")
    return row


def same_claim(wrong: str, row: Dict[str, Any]) -> bool:
    """[2026-10-18 C50 R4] 새 정정의 wrong 이 옛 정정의 문장이나 wrong 과 맞나(정규화 포함 판정 — 위키 강등과 같은 규칙)."""
    w = _norm(wrong)
    if not w:
        return False
    for x in (row.get("text"), row.get("wrong")):
        nx = _norm(x)
        if nx and (w in nx or nx in w):
            return True
    return False


def undo(channel_id: str, cid: int, *, restore: bool = True) -> Dict[str, Any]:
    """[2026-10-18 C50 R2·R3] 정정 하나를 되돌린다 — 행 off + links 의 페이지마다 위키 줄 빼기(+ restore 면 내린 문장 복귀).
    links 가 없는 옛 행은 꼴(`정정(T{turn}): 문장`, `(정정됨 T{turn}) …wrong…`)로 찾는다. 돌려줌: {ok, row, pages, restored}."""
    try:
        cid = int(cid)
    except (TypeError, ValueError):
        return {"ok": False, "reason": "id"}
    row = next((r for r in sqlite_store.read_corrections(channel_id, active_only=False) if r.get("id") == cid), None)
    if not row:
        return {"ok": False, "reason": "nomatch"}
    if not row.get("active"):
        return {"ok": False, "reason": "inactive", "row": row}
    sqlite_store.set_correction_active(channel_id, cid, False)
    pages = restored = 0
    try:
        import wiki_store
        tag = f"T{row['turn']}" if row.get("turn") is not None else "T?"
        links = row.get("links") or {}
        targets = [(pid, lk.get("line") or "", lk.get("demoted") or "", "")
                   for pid, lk in links.items() if not str(pid).startswith("_") and isinstance(lk, dict)]
        if not targets:
            for nm in row.get("about") or []:
                pid = wiki_store.resolve_page(channel_id, nm)
                if pid:
                    targets.append((pid, f"{wiki_store.CORRECTION_PREFIX}{tag}): {row.get('text') or ''}", "",
                                    row.get("wrong") or ""))
        for pid, line, dem, wr in targets:
            r = wiki_store.undo_correction_lines(channel_id, pid, line, dem, tag, wrong=wr, restore=restore)
            if r.get("line") or r.get("restored"):
                pages += 1
            restored += int(r.get("restored") or 0)
    except Exception as e:
        logger.warning(f"[정정] 위키 되돌림 실패(행은 껐다): {e}")
    logger.info(f"[정정] -{cid} 되돌림 pages={pages} restored={restored} restore={restore}")
    return {"ok": True, "row": row, "pages": pages, "restored": restored}


def retract(channel_id: str, text: str) -> Tuple[int, str]:
    """활성 정정 중 text 와 맞는 것을 거둔다. 하나일 때만. 돌려줌: (거둔 수, 사유).
    [2026-10-18 C50 R2] 거두기 = 완전 되돌림(`undo`) — 위키 정정 줄을 빼고 내렸던 문장을 되돌린다."""
    q = _norm(text)
    if not q:
        return 0, "empty"
    rows = sqlite_store.read_corrections(channel_id, active_only=True)
    hit = [r for r in rows if _norm(r.get("text")) == q]
    if not hit:
        hit = [r for r in rows if q in _norm(r.get("text")) or _norm(r.get("text")) in q]
    if not hit:
        return 0, "nomatch"
    if len(hit) > 1:
        return 0, "ambiguous"
    undo(channel_id, hit[0]["id"], restore=True)
    logger.info(f"[정정] -{hit[0]['id']} 거둠")
    return 1, str(hit[0].get("text") or "")


def _forms(channel_id: str, word: str, npcs: Dict[str, Any]) -> List[str]:
    """대조 꼴 — 낱말 자체 + 인물이면 키·괄호 앞·괄호 안·별칭."""
    forms = {word}
    data = npcs.get(word) if isinstance(npcs, dict) else None
    if isinstance(data, dict):
        forms.add(_PAREN.split(word)[0].strip())
        m = re.search(r'[(\[（]([^)\]）]+)[)\]）]', word)
        if m:
            forms.add(m.group(1).strip())
        for a in (data.get("aliases") or []):
            if isinstance(a, str):
                forms.add(a.strip())
    return [f for f in forms if f and len(f) >= 2]


def corrections_for(channel_id: str, text: str, cap: int = CAP,
                    exclude_ids: Iterable[int] = ()) -> List[Dict[str, Any]]:
    """활성 정정 중 about 낱말이 text 에 나오는 것 — 최신순 cap 개."""
    if not channel_id or not str(text or "").strip():
        return []
    rows = sqlite_store.read_corrections(channel_id, active_only=True)
    if not rows:
        return []
    try:
        import domain_manager
        npcs = domain_manager.get_npcs(channel_id) or {}
    except Exception:
        npcs = {}
    hay = str(text)
    ex = set(exclude_ids or ())
    out = []
    for r in reversed(rows):
        if r.get("id") in ex:
            continue
        if any(f in hay for w in (r.get("about") or []) for f in _forms(channel_id, str(w), npcs)):
            out.append(r)
            if len(out) >= max(0, int(cap)):
                break
    return out


def render(rows: List[Dict[str, Any]], english_head: bool = False) -> str:
    if not rows:
        return ""
    lines = [HEAD_EN if english_head else HEAD_KO]
    for r in rows:
        t = r.get("turn")
        lines.append(f"- T{t}: {r.get('text')}" if t is not None else f"- {r.get('text')}")
    return "\n".join(lines)


def block_for(channel_id: str, text: str, *, cap: int = CAP, english_head: bool = False,
              exclude_ids: Iterable[int] = ()) -> str:
    """읽는 자리 한 줄 입구 — 걸린 정정이 없으면 "" (그 자리 출력 바이트 그대로)."""
    try:
        return render(corrections_for(channel_id, text, cap=cap, exclude_ids=exclude_ids), english_head)
    except Exception as e:
        logger.debug(f"[정정] block skip: {e}")
        return ""


def wrongs_for_names(channel_id: str, names: Iterable[Any]) -> List[str]:
    """F1 게이트용 — 이 이름들(페이지 이름·별칭)에 걸린 활성 정정의 wrong 문장들."""
    rows = sqlite_store.read_corrections(channel_id, active_only=True)
    if not rows:
        return []
    keys = {_norm(n) for n in (names or []) if str(n or "").strip()}
    out = []
    for r in rows:
        if not any(_norm(a) in keys for a in (r.get("about") or [])):
            continue
        w = str(r.get("wrong") or "").strip()
        if w:
            out.append(w)
        # [2026-10-18 C50 R3] 갈아치운 옛 정정 문장도 틀린 문장이다 — 그 뒤 산문을 본 F1이 다시 써넣지 않게.
        for aw in ((r.get("links") or {}).get("_also_wrong") or []):
            if str(aw or "").strip():
                out.append(str(aw).strip())
    return out
