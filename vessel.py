# -*- coding: utf-8 -*-
"""vessel.py — 몸·인격 분리 (C32 B′, 2026-10-12)

"이 몸을 지금 누가·무엇이 몰고 있나" 한 장.
  표 = world_state["vessels"] = {몸 키: {"driver": 마음 키 | "", "overlays": [{label, text, since}],
                                      "since": 턴, "note": 한 줄}}
  - 행이 없으면 몸 = 마음(평소). 표가 비면 이 모듈의 모든 함수는 받은 이름을 그대로 돌려준다.
  - driver "" = 아무도 안 모는 몸(빈 인형·마음이 떠난 몸).
  - overlays = 몸의 주인은 그대로인데 위에 씌운 것(최면·세뇌·명령).

★모델 쪽 이름은 건드리지 않는다. 렌더·Theoria·추출·위키 패치는 보이는 이름(몸)으로 읽고 쓴다.
  마음 쪽 저장소(관계 source·지식·비밀 원장·사실 출처·위키 play 절·평형)만 domain_manager 등의
  **입구에서 한 번** `driver_of` 로 돌린다. 두 번 돌리면 몸 바꾸기(두 행이 서로를 가리킴)가 제자리로
  뒤집힌다 — 그래서 `driver_of` 는 순수 조회이고, 의미(떠난 몸 비우기 등)는 `apply_events` 가 행으로 쓴다.
★world_state 에 사는 이유: `!다시` 스냅샷(도메인 JSON)이 같이 되감고 `!리셋` 이 같이 지운다.
  저장소 행은 옮기지 않는다(관계는 되감기지 않으므로 행을 옮기면 다시 하기 때 어긋난다).

사건은 입력단에서 — Theoria `vessel` 칸(장전된 채널만 스키마에 실린다).
스펙: 파티쳇수정/npc/C32_몸·인격분리_스펙_2026-10-12.md
"""
import logging
import re
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

WS_KEY = "vessels"
ARM_KEY = "vessel_arm"            # world_state: True/False = 출력물 파일 선언이 켜고 끔. 없으면 로어 분석 판정.
LORE_FLAG_KEY = "mind_transfer"   # lore_summary_data 불리언(로어 분석이 뽑음)
OPS = ("drive", "release", "overlay", "lift")
MAX_OVERLAYS = 3
MAX_EVENTS = 4                    # 한 턴에 받는 사건 상한
TEXT_MAX = 120
LABEL_MAX = 12


# =========================================================
# 조회 — 순수
# =========================================================
def rows(channel_id: str) -> Dict[str, Dict[str, Any]]:
    """표 전체. 실패·없음 = {}."""
    if not channel_id:
        return {}
    try:
        import domain_manager
        v = (domain_manager.get_world_state(channel_id) or {}).get(WS_KEY)
    except Exception as e:
        logger.debug(f"[Vessel] rows skip: {e}")
        return {}
    return v if isinstance(v, dict) else {}


def driver_of(channel_id: str, name: Any) -> Any:
    """몸 이름 → 그 몸을 모는 마음의 키. 행이 없으면 받은 값 그대로. "" = 빈 몸.
    ★입구에서 **한 번만** 부른다(돌린 값을 다시 넣지 말 것)."""
    if not name or not isinstance(name, str):
        return name
    r = rows(channel_id)
    if not r:
        return name
    row = r.get(name)
    if row is None:
        # 짧은 이름("리나")·영문 호칭으로 와도 표의 몸 키("리나(Lina)")에 맞춘다. 맞는 행이 없으면 받은 값 그대로.
        try:
            import domain_manager
            k = domain_manager._find_npc_key(domain_manager.get_npcs(channel_id) or {}, name, allow_token=False)
        except Exception:
            k = None
        if k and k in r:
            row = r[k]
    if isinstance(row, dict) and "driver" in row:
        return str(row.get("driver") or "")
    return name


def visible_of(channel_id: str, mind: Any, _rows: Optional[Dict[str, Any]] = None) -> Optional[str]:
    """마음 키 → 지금 그 마음이 보이는 이름(모는 몸). 몸이 없는 마음(잠듦·병 속)은 None."""
    if not mind or not isinstance(mind, str):
        return mind
    r = rows(channel_id) if _rows is None else _rows
    if not r:
        return mind
    for body, row in r.items():
        if body != mind and isinstance(row, dict) and str(row.get("driver") or "") == mind:
            return body
    own = r.get(mind)
    if isinstance(own, dict) and "driver" in own and str(own.get("driver") or "") != mind:
        return None
    return mind


def view_keyed(channel_id: str, stored: Dict[str, Any]) -> Dict[str, Any]:
    """마음 키 dict → 보이는 이름 dict. 잠든 마음 항목은 빠진다. 표가 비면 받은 dict 그대로(같은 객체)."""
    r = rows(channel_id)
    if not r or not isinstance(stored, dict):
        return stored
    out: Dict[str, Any] = {}
    for k, v in stored.items():
        vis = visible_of(channel_id, k, r) if isinstance(k, str) else k
        if vis is None:
            continue
        out[vis] = v
    return out


def view_edges(channel_id: str, edges: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """관계 행 목록 — source(마음)만 보이는 이름으로. target(남이 보는 얼굴)은 그대로. 잠든 마음의 행은 빠진다."""
    r = rows(channel_id)
    if not r or not isinstance(edges, list):
        return edges
    out = []
    for e in edges:
        if not isinstance(e, dict):
            continue
        src = e.get("source")
        vis = visible_of(channel_id, src, r)
        if vis is None:
            continue
        out.append(e if vis == src else dict(e, source=vis))
    return out


# =========================================================
# 장전 — 이 채널이 이 일을 보는가
# =========================================================
def armed(channel_id: str) -> bool:
    """출력물 파일 선언(켜기/끄기)이 이기고, 없으면 로어 분석 불리언."""
    if not channel_id:
        return False
    try:
        import domain_manager
        flag = (domain_manager.get_world_state(channel_id) or {}).get(ARM_KEY)
        if flag is True or flag is False:
            return flag
        return (domain_manager.get_lore_summary_data(channel_id) or {}).get(LORE_FLAG_KEY) is True
    except Exception as e:
        logger.debug(f"[Vessel] armed skip: {e}")
        return False


_ARM_LINE_RE = re.compile(
    r"^[ \t]*(?:깃들기|vessel)[ \t]*[:：]?[ \t]*(켜기|켬|on|끄기|끔|off)[ \t]*$",
    re.IGNORECASE | re.MULTILINE)


def take_arm_line(text: str):
    """출력물 파일의 `깃들기: 켜기|끄기` 줄을 떼어 낸다. → (남은 글, True|False|None)."""
    if not isinstance(text, str):
        return text, None
    m = None
    for m in _ARM_LINE_RE.finditer(text):
        pass
    if m is None:
        return text, None
    val = m.group(1).lower() in ("켜기", "켬", "on")
    rest = _ARM_LINE_RE.sub("", text).strip()
    return rest, val


def set_arm(channel_id: str, value: Optional[bool]) -> None:
    import domain_manager
    w = domain_manager.get_world_state(channel_id)
    if value is None:
        w.pop(ARM_KEY, None)
    else:
        w[ARM_KEY] = bool(value)
    domain_manager.update_world_state(channel_id, w)


# =========================================================
# 입력단 — Theoria 칸 문안 + 적용
# =========================================================
_THEORIA_BLOCK = """
### Vessels — who drives a body (this world has possession, hypnosis or mind transfer)
- "vessel": null OR [ {"op": "drive|release|overlay|lift",
                       "body": "registered name of the person whose body it is",
                       "mind": "registered name of the person now driving that body (drive only) | null = nobody drives it",
                       "label": "short Korean name of the hold, e.g. 최면 (overlay/lift only)",
                       "text": "ENGLISH-ONLY telegraphic, one line"} ]
  ONLY when this turn's input or the last prose makes it happen: a named person's mind now drives another
  named person's body (drive — this already leaves that mind's own body empty; do not also release it),
  the mind driving a body leaves it and the owner comes back (release), a hypnosis/command/compulsion takes
  hold of someone (overlay) or lets go (lift). Never the PC's body. Do not decide what the person becomes —
  only who drives which body. null is the common case."""


def theoria_block(channel_id: str) -> str:
    """장전된 채널이면 Theoria 출력 스키마에 붙일 칸 문안(+지금 표). 아니면 ""."""
    if not armed(channel_id):
        return ""
    cur = []
    for body, row in rows(channel_id).items():
        if not isinstance(row, dict):
            continue
        bits = []
        if "driver" in row:
            d = str(row.get("driver") or "")
            bits.append("driven by " + d if d else "nobody drives it")
        for ov in row.get("overlays") or []:
            if isinstance(ov, dict) and ov.get("label"):
                bits.append("overlay " + str(ov["label"]))
        if bits:
            cur.append(f"{body}: " + ", ".join(bits))
    tail = ("\n  CURRENT: " + "; ".join(cur)) if cur else ""
    return _THEORIA_BLOCK + tail + "\n"


def _clip(v: Any, n: int) -> str:
    return str(v or "").strip()[:n]


def apply_events(channel_id: str, events: Any, turn: int = 0) -> List[str]:
    """Theoria `vessel` 사건 → 표. 관문은 코드가 쥔다. Returns: 적용 줄(로그·스모크용)."""
    if not channel_id or not isinstance(events, list) or not events:
        return []
    try:
        import domain_manager
        npcs = domain_manager.get_npcs(channel_id) or {}
        pcr = domain_manager.pc_registry(channel_id)
        w = domain_manager.get_world_state(channel_id)
    except Exception as e:
        logger.debug(f"[Vessel] apply skip: {e}")
        return []
    table = w.get(WS_KEY)
    table = dict(table) if isinstance(table, dict) else {}
    done: List[str] = []

    def _key(raw: Any) -> str:
        nm = _clip(raw, 80)
        if not nm or nm.lower() in ("null", "none", "nobody"):
            return ""
        return domain_manager._find_npc_key(npcs, nm) or ""

    def _row(body: str) -> Dict[str, Any]:
        r = table.get(body)
        return dict(r) if isinstance(r, dict) else {}

    def _put(body: str, r: Dict[str, Any]) -> None:
        # driver 가 자기 자신이고 덧씌움도 없으면 행은 뜻이 없다 → 지운다(평소로).
        if r.get("driver", body) == body and not r.get("overlays"):
            table.pop(body, None)
            return
        if r.get("driver", body) == body:
            r.pop("driver", None)
        table[body] = r

    # [측정 10-12] 모델이 drive 와 함께 "그 마음의 원래 몸 release"를 같이 내는 일이 있다(2표본 중 1).
    #   drive 가 이미 원래 몸을 비우므로, 같은 묶음에서 drive 의 마음을 몸으로 한 release 는 버린다
    #   — 순서가 drive 뒤면 비운 몸을 되돌리고 방금 몬 몸까지 비워 버린다.
    _drive_minds = set()
    for ev in events[:MAX_EVENTS]:
        if isinstance(ev, dict) and str(ev.get("op") or "").strip().lower() == "drive":
            _m0 = _key(ev.get("mind"))
            if _m0:
                _drive_minds.add(_m0)

    for ev in events[:MAX_EVENTS]:
        if not isinstance(ev, dict):
            continue
        op = str(ev.get("op") or "").strip().lower()
        if op not in OPS:
            continue
        body = _key(ev.get("body"))
        if op == "release" and body and body in _drive_minds:
            logger.info("[Vessel] 버림 — 같은 묶음 drive 가 이미 비운 몸의 release: %s", body)
            continue
        if not body or domain_manager.is_pc_name(body, pcr):
            logger.info("[Vessel] 버림 — 몸이 명부 밖이거나 PC: %r", ev.get("body"))
            continue
        text = _clip(ev.get("text"), TEXT_MAX)
        if op == "drive":
            raw_mind = ev.get("mind")
            mind = _key(raw_mind)
            if raw_mind not in (None, "") and str(raw_mind).strip().lower() not in ("null", "none", "nobody") and not mind:
                # 등록 안 된 마음(이름 없는 영혼 등)은 그 몸 시트와 산문이 맡는다(수처 판정 C).
                logger.info("[Vessel] 버림 — 마음이 명부 밖: %r → %s", raw_mind, body)
                continue
            if mind and domain_manager.is_pc_name(mind, pcr):
                continue
            if mind == body:                       # 자기 마음이 돌아옴 = release 와 같다
                op = "release"
            else:
                if mind:
                    # 같은 마음은 한 몸에만 — 앞에 몰던 몸은 빈 몸으로.
                    for b2, r2 in list(table.items()):
                        if b2 != body and isinstance(r2, dict) and r2.get("driver") == mind:
                            r2 = dict(r2)
                            r2["driver"] = ""
                            table[b2] = r2
                    # 그 마음의 원래 몸(등록 인물)이 아직 그 마음으로 돌던 중이면 → 빈 몸 행을 명시.
                    if mind in npcs:
                        own = _row(mind)
                        if own.get("driver", mind) == mind:
                            own["driver"] = ""
                            own.setdefault("since", int(turn or 0))
                            table[mind] = own
                r = _row(body)
                r["driver"] = mind
                r["since"] = int(turn or 0)
                if text:
                    r["note"] = text
                table[body] = r
                done.append(f"drive {body} ← {mind or '(없음)'}")
                continue
        if op == "release":
            r = _row(body)
            prev = r.get("driver", body)
            r.pop("driver", None)
            r.pop("note", None)
            _put(body, r)
            # 이 몸의 원래 마음이 다른 몸을 몰고 있었으면(몸 바꾸기) 그 마음은 집으로 — 그 몸은 빈다.
            for b2, r2 in list(table.items()):
                if b2 != body and isinstance(r2, dict) and r2.get("driver") == body:
                    r2 = dict(r2)
                    r2["driver"] = ""
                    table[b2] = r2
            # 떠난 마음이 원래 몸에서 빈 몸 행이었으면 → 집으로(원래 몸 평소로).
            if prev and prev != body:
                own = _row(prev)
                if own.get("driver") == "":
                    own.pop("driver", None)
                    _put(prev, own)
            done.append(f"release {body}" + (f" (← {prev})" if prev and prev != body else ""))
            continue
        if op == "overlay":
            label = _clip(ev.get("label") or text, LABEL_MAX)
            if not label:
                continue
            r = _row(body)
            ovs = [o for o in (r.get("overlays") or []) if isinstance(o, dict) and o.get("label") != label]
            ovs.append({"label": label, "text": text, "since": int(turn or 0)})
            r["overlays"] = ovs[-MAX_OVERLAYS:]
            _put(body, r)
            done.append(f"overlay {body} [{label}]")
            continue
        if op == "lift":
            label = _clip(ev.get("label"), LABEL_MAX)
            r = _row(body)
            ovs = [o for o in (r.get("overlays") or []) if isinstance(o, dict)]
            ovs = [o for o in ovs if label and o.get("label") != label] if label else []
            r["overlays"] = ovs
            if not ovs:
                r.pop("overlays", None)
            _put(body, r)
            done.append(f"lift {body}" + (f" [{label}]" if label else ""))
            continue

    if done:
        if table:
            w[WS_KEY] = table
        else:
            w.pop(WS_KEY, None)
        domain_manager.update_world_state(channel_id, w)
        logger.info("[Vessel] t%s %s", turn, "; ".join(done))
    return done


# =========================================================
# 화면 — 하단 임베드 이름 표지 (D4: 대괄호 + 한국어 말머리)
# =========================================================
def tag(channel_id: str, name: Any, _rows: Optional[Dict[str, Any]] = None) -> str:
    """`리나(Lina)` 뒤에 붙일 표지. `[안: 세리아(Seria) · 최면]` / `[빈 몸]` / ""."""
    if not name or not isinstance(name, str):
        return ""
    r = rows(channel_id) if _rows is None else _rows
    row = r.get(name) if r else None
    if row is None and r:
        try:
            import domain_manager
            k = domain_manager._find_npc_key(domain_manager.get_npcs(channel_id) or {}, name, allow_token=False)
            row = r.get(k) if k else None
        except Exception:
            row = None
    if not isinstance(row, dict):
        return ""
    parts = []
    if "driver" in row:
        d = str(row.get("driver") or "")
        if d == "":
            parts.append("빈 몸")
        elif d != name:
            parts.append(f"안: {d}")
    for ov in row.get("overlays") or []:
        if isinstance(ov, dict) and ov.get("label"):
            parts.append(str(ov["label"]))
    return f" [{' · '.join(parts)}]" if parts else ""


def overlay_lines(channel_id: str, name: Any) -> List[str]:
    """렌더 프로필 밑에 붙일 덧씌움 줄."""
    row = rows(channel_id).get(name) if name else None
    out = []
    for ov in (row or {}).get("overlays") or []:
        if not isinstance(ov, dict) or not ov.get("label"):
            continue
        body = f'[Overlay: {ov["label"]}'
        if ov.get("text"):
            body += f' — {ov["text"]}'
        body += f' (since t{int(ov.get("since") or 0)})]'
        out.append(body)
    return out
