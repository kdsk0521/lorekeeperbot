# -*- coding: utf-8 -*-
"""Activity 화면 서버 — [2026-10-22 C54] 1단계(읽기 전용) · [2026-10-23 C60 M2] 탭 등록·편집(2단계)

디스코드 Activity(디스코드 앱 안 iframe)가 이 서버에서 화면 파일과 데이터를 받는다.
    디스코드 프록시 → Funnel(공개 https) → 127.0.0.1:ACTIVITY_PORT → 여기
봇 프로세스 **안**(같은 이벤트 루프)에서 돈다. 읽기는 턴 파이프라인과 같은 함수·같은 DB를 부른다.
1단계는 쓰기 경로 0 · 모델 콜 0.
[C60 M2] 탭은 등록부(module_frame.screens)에서 온다(`/api/tabs`). 쓰기는 `/api/act` 하나 — 세션 확인 ·
  참가자 확인 · 액션 화이트리스트 · 턴 잠금 · 배경 큐 비우기 · rev 검사를 activity_edit.act 가 지킨다.
  쓰기는 새 길을 만들지 않고 OOC 편집이 지나는 문을 그대로 지난다. 모델 콜은 여전히 0.

공개 주소가 전제다(10-22 실관측 — 주소가 생기고 15초 만에 `/.env`·`/CLAUDE.md`·`/config.json` 스캐너).
  · 경로는 아래 두 표(STATIC_FILES · build_app 의 API 경로)에 있는 것만 응답한다. 폴더째 서빙 없음, 나머지는 404.
  · `/api/view/*` 는 서명된 세션 없이는 401. 세션의 채널은 발급 때 디스코드에
    "이 사람이 이 채널을 볼 수 있나"를 물어 통과한 것뿐이고, 요청이 채널을 바꿀 수 없다.
  · 세션 키는 부팅 때 만든 무작위 값(메모리에만) — 재시작하면 화면이 한 번 다시 인증한다.
  · client secret 은 서버 밖으로 나가지 않는다(화면에 가는 건 공개값 client_id 뿐).
바인딩은 127.0.0.1 — Funnel 만 들어온다. Lightsail 방화벽이 열려 있어도 바깥에서 직접 못 닿는다.

비밀 절(NPC Secrets 등)은 **가리지 않는다** — 레티어스 10-22 결정(솔로 위주라 시트를 쓴 사람이 곧 보는 사람).
스펙 파티쳇수정/output_artifact/C54_Activity화면1단계_지도·스펙_2026-10-22.md
"""

import base64
import hashlib
import hmac
import json
import logging
import os
import re
import secrets
import time
from typing import Any, Dict, List, Optional, Tuple

from aiohttp import web, ClientSession, ClientTimeout

import config

logger = logging.getLogger("Activity")

STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "activity")
# 경로 → (파일 이름, Content-Type). **이 표가 정적 서빙의 전부다.** 파일을 늘리면 여기 한 줄.
STATIC_FILES: Dict[str, Tuple[str, str]] = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/app.js": ("app.js", "text/javascript; charset=utf-8"),
    "/style.css": ("style.css", "text/css; charset=utf-8"),
    "/sdk.js": ("sdk.js", "text/javascript; charset=utf-8"),
    # [2026-10-23 C73 M7a] 코드 앱 HTML 화면 틀 — app.js 가 sandbox="allow-scripts" iframe 으로만 띄운다(규칙 §5e).
    "/frame.html": ("frame.html", "text/html; charset=utf-8"),
    "/frame.js": ("frame.js", "text/javascript; charset=utf-8"),
}
# [2026-10-24 C73 보안 검토] 코드 앱 HTML 틀(frame.html)에만 거는 CSP — 앱 HTML 의 CSS(@import · url() · @font-face)가
#   바깥 주소로 요청을 내는 길(보는 사람 IP · 시각이 새는 비컨)을 닫는다. 스크립트는 우리 frame.js 하나, 소리 · 그림 · 글꼴은 data: 만.
FRAME_CSP = ("default-src 'none'; script-src 'self'; style-src 'unsafe-inline'; img-src data:; media-src data:; "
             "font-src data:; connect-src 'none'; frame-src 'none'; child-src 'none'; form-action 'none'; base-uri 'none'")
CLIENT_ID_PLACEHOLDER = "__DISCORD_CLIENT_ID__"
# [2026-10-22 C55] 정적 파일 주소 꼬리 `?v=` — 디스코드 앱·프록시가 app.js/style.css 를 붙들고 있어
#   서버 파일을 바꿔도 예전 화면이 뜨던 일(10-22 실관측) 방지. 값 = app.js+style.css 내용 해시라 파일이 바뀌면 주소도 바뀐다.
ASSET_VER_PLACEHOLDER = "__ASSET_VER__"
SESSION_TTL = 3600              # 초. 만료되면 화면이 조용히 다시 인증한다
MAX_BODY = 16 * 1024            # 요청 본문 상한 — 세션(code·channel_id)·편집 한 건(action·args·rev) 모두 작다
DISCORD_API = "https://discord.com/api/v10"
_SEC_HEADERS = {"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff",
                "Referrer-Policy": "no-referrer"}

_SESSION_KEY = secrets.token_bytes(32)
_started = False
_runner: Optional[web.AppRunner] = None
_client = None                  # discord.Client — 채널 권한 확인용(on_ready 가 넘긴다)
_http: Optional[ClientSession] = None


def running() -> bool:
    """서버가 실제로 떠 있나. 💠 가 화면을 열지, 종전 창으로 내려갈지를 이걸로 정한다."""
    return _started


# =========================================================
# 세션 — {uid, channel, 만료} + HMAC. 상태 저장 0
# =========================================================

def _b64e(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode("ascii")


def _b64d(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def _sign(body: str) -> str:
    return _b64e(hmac.new(_SESSION_KEY, body.encode("ascii"), hashlib.sha256).digest())


def make_session(uid: str, channel_id: str, now: Optional[float] = None, ttl: int = SESSION_TTL) -> str:
    now = time.time() if now is None else now
    body = _b64e(json.dumps({"u": str(uid), "c": str(channel_id), "e": int(now + ttl)},
                            separators=(",", ":")).encode("utf-8"))
    return f"{body}.{_sign(body)}"


def read_session(token: str, now: Optional[float] = None) -> Optional[Tuple[str, str]]:
    """유효하면 (uid, channel_id), 아니면 None. 위조·만료·모양 오류 전부 None 하나로."""
    try:
        body, sig = str(token or "").split(".", 1)
        if not hmac.compare_digest(sig, _sign(body)):
            return None
        d = json.loads(_b64d(body))
        if int(d.get("e", 0)) < (time.time() if now is None else now):
            return None
        uid, ch = str(d.get("u") or ""), str(d.get("c") or "")
        return (uid, ch) if uid and ch else None
    except Exception:
        return None


def _auth(request: web.Request) -> Tuple[str, str]:
    h = request.headers.get("Authorization", "")
    s = read_session(h[7:] if h.startswith("Bearer ") else "")
    if not s:
        raise web.HTTPUnauthorized(headers=_SEC_HEADERS)
    return s


# =========================================================
# 디스코드 쪽 — 스모크가 이 셋을 스텁으로 갈아끼운다(모듈 전역 이름으로 부른다)
# =========================================================

async def _http_session() -> ClientSession:
    global _http
    if _http is None or _http.closed:
        _http = ClientSession(timeout=ClientTimeout(total=10))
    return _http


async def exchange_code(code: str) -> Optional[str]:
    """authorize 가 준 code → access_token. 실패 None."""
    try:
        s = await _http_session()
        async with s.post(f"{DISCORD_API}/oauth2/token", data={
            "client_id": config.DISCORD_CLIENT_ID,
            "client_secret": config.DISCORD_CLIENT_SECRET,
            "grant_type": "authorization_code",
            "code": code,
        }, headers={"Content-Type": "application/x-www-form-urlencoded"}) as r:
            if r.status != 200:
                logger.info("[Activity] token exchange %s", r.status)
                return None
            d = await r.json()
            return str(d.get("access_token") or "") or None
    except Exception as e:
        logger.info(f"[Activity] token exchange 실패: {e}")
        return None


async def fetch_user(access_token: str) -> Optional[Tuple[str, str]]:
    """access_token → (uid, 표시 이름). 실패 None."""
    try:
        s = await _http_session()
        async with s.get(f"{DISCORD_API}/users/@me",
                         headers={"Authorization": f"Bearer {access_token}"}) as r:
            if r.status != 200:
                return None
            d = await r.json()
            uid = str(d.get("id") or "")
            return (uid, str(d.get("global_name") or d.get("username") or "")) if uid else None
    except Exception as e:
        logger.info(f"[Activity] users/@me 실패: {e}")
        return None


async def can_view_channel(uid: str, channel_id: str) -> bool:
    """이 사람이 이 채널을 볼 수 있나 — 봇이 디스코드에 직접 묻는다.

    members intent 가 없어도 된다: 캐시에 없으면 `fetch_member`(REST 단건, 특권 intent 불필요).
    DM·모르는 채널·서버 밖 사람은 전부 False.
    """
    if _client is None:
        return False
    try:
        cid, mid = int(channel_id), int(uid)
        ch = _client.get_channel(cid) or await _client.fetch_channel(cid)
        guild = getattr(ch, "guild", None)
        if guild is None:
            return False
        member = guild.get_member(mid) or await guild.fetch_member(mid)
        return bool(ch.permissions_for(member).view_channel)
    except Exception as e:
        logger.info(f"[Activity] 채널 권한 확인 실패(거절): {e}")
        return False


# =========================================================
# 핸들러
# =========================================================

def _json(data: Any, status: int = 200) -> web.Response:
    return web.json_response(data, status=status, headers=_SEC_HEADERS,
                             dumps=lambda o: json.dumps(o, ensure_ascii=False, default=str))


async def handle_static(request: web.Request) -> web.Response:
    entry = STATIC_FILES.get(request.path)
    if not entry:
        raise web.HTTPNotFound()
    fname, ctype = entry
    try:
        with open(os.path.join(STATIC_DIR, fname), "rb") as f:
            data = f.read()
    except OSError:
        raise web.HTTPNotFound()
    if fname in ("index.html", "frame.html"):   # [C73] frame.html 도 ?v= 꼬리를 채운다
        # client_id 는 공개값이지만 숫자만 들어가게 거른다(HTML 에 섞이는 유일한 바깥 값).
        cid = re.sub(r"\D", "", str(getattr(config, "DISCORD_CLIENT_ID", "") or ""))
        data = data.replace(CLIENT_ID_PLACEHOLDER.encode("ascii"), cid.encode("ascii"))
        data = data.replace(ASSET_VER_PLACEHOLDER.encode("ascii"), asset_version().encode("ascii"))
    headers = dict(_SEC_HEADERS)
    headers["Content-Type"] = ctype
    if fname == "frame.html":
        headers["Content-Security-Policy"] = FRAME_CSP   # [C73 보안 검토] §5e — 바깥 요청 0
    if fname == "sdk.js":
        headers["Cache-Control"] = "public, max-age=86400"   # 묶음 파일은 바뀔 일이 드물다
    return web.Response(body=data, headers=headers)


def asset_version() -> str:
    """app.js + style.css 내용 해시 앞 12자리. 못 읽으면 "0"(그래도 화면은 뜬다)."""
    h = hashlib.sha256()
    try:
        for fn in ("app.js", "style.css", "frame.js"):   # [C73] frame.js 가 바뀌어도 꼬리가 바뀐다
            with open(os.path.join(STATIC_DIR, fn), "rb") as f:
                h.update(f.read())
        return h.hexdigest()[:12]
    except OSError:
        return "0"


async def handle_session(request: web.Request) -> web.Response:
    """code + 채널 → 세션. 순서: 토큰 교환 → 누구인가 → 그 채널을 볼 수 있나 → 서명."""
    try:
        payload = await request.json()
    except web.HTTPException:
        raise                                   # 본문 상한 초과(413) 등은 aiohttp 판정 그대로
    except Exception:
        return _json({"error": "bad request"}, 400)
    if not isinstance(payload, dict):
        return _json({"error": "bad request"}, 400)
    code = str(payload.get("code") or "")[:256]
    channel_id = re.sub(r"\D", "", str(payload.get("channel_id") or ""))[:24]
    if not code or not channel_id:
        return _json({"error": "bad request"}, 400)
    token = await exchange_code(code)
    if not token:
        return _json({"error": "auth failed"}, 401)
    who = await fetch_user(token)
    if not who:
        return _json({"error": "auth failed"}, 401)
    uid, name = who
    if not await can_view_channel(uid, channel_id):
        logger.info("[Activity] 채널 접근 거절 uid=%s ch=%s", uid, channel_id)
        return _json({"error": "forbidden"}, 403)
    logger.info("[Activity] session uid=%s ch=%s", uid, channel_id)
    return _json({"session": make_session(uid, channel_id), "access_token": token,
                  "user": {"id": uid, "name": name}, "channel_id": channel_id})


def view_status(channel_id: str, uid: str) -> Dict[str, Any]:
    """상태 탭 = 턴 임베드 1장째 데이터 + 섹션 장 데이터(예산 절단 없음)."""
    import status_panel
    return {"turn": status_panel.build_turn_embed_data(channel_id),
            "sections": status_panel.build_section_pages_data(channel_id)}


def view_archive(channel_id: str, uid: str) -> Dict[str, Any]:
    """보관함 탭 = 💠 창의 장 그대로(노트북은 이 사람 것).
    [2026-10-23 C69 A3] ④ 도착물 목록은 뺀다 — 편지 · 소식 앱이 본문까지 그 자리다(C69 §7-1)."""
    import status_panel
    return {"pages": status_panel.build_archive_pages_data(channel_id, uid, include_mail=False)}


# =========================================================
# [2026-10-23 C69 A3] 편지 · 소식 · 속마음 앱 — turn_mail 행을 최신부터(💌 📰 💭 버튼이 ephemeral 로 열던 그 본문)
# =========================================================
FEED_MAX = 30
FEED_APPS = (("mail", "mail"), ("board", "board"), ("mind", "mind"))   # (앱 tab, turn_mail kind) — 지금은 같은 이름


def _feed_kind(app: str) -> str:
    return dict(FEED_APPS).get(app, app)


def _feed_meta(p: Dict[str, Any], turn: Any) -> str:
    """'보낸 이 → 받는 이 · 형식 이름 · t턴' — 있는 것만(💌 임베드 _mail_embed 와 같은 칸)."""
    author = str(p.get("author") or "").strip()
    recipient = str(p.get("recipient") or "").strip()
    parts: List[str] = []
    if author and recipient:
        parts.append(f"{author} → {recipient}")
    elif author:
        parts.append(author)
    fmt = str(p.get("format_name") or "").strip()
    if fmt:
        parts.append(fmt)
    try:
        if int(turn or 0) > 0:
            parts.append(f"t{int(turn)}")
    except (TypeError, ValueError):
        pass
    return " · ".join(parts)


def _mind_entries(p: Dict[str, Any]) -> List[Dict[str, Any]]:
    """속마음 행 → [{name, lines:[{label, text}]}] — 💭 임베드(_mind_embed)와 같은 순서: entries 다음 notes."""
    import turn_mail
    out: List[Dict[str, Any]] = []
    for ent in (p.get("entries") or [])[:turn_mail.MAX_MIND_ENTRIES]:
        if not isinstance(ent, dict):
            continue
        lines = [{"label": str(l.get("label") or "").strip(), "text": str(l.get("text") or "").strip()}
                 for l in (ent.get("lines") or []) if isinstance(l, dict) and str(l.get("text") or "").strip()]
        if lines:
            out.append({"name": str(ent.get("name") or "?"), "lines": lines})
    for n in (p.get("notes") or []):
        if isinstance(n, dict) and str(n.get("body") or "").strip():
            out.append({"name": str(n.get("title") or ""),
                        "lines": [{"label": "", "text": str(n.get("body") or "").strip()}]})
    return out


def feed_item(row: Dict[str, Any], seen: int) -> Dict[str, Any]:
    """행 하나 → 화면 카드 하나. 글은 전부 글자로 간다(화면이 글자 노드로 그린다)."""
    p = row.get("payload") if isinstance(row.get("payload"), dict) else {}
    kind = str(row.get("kind") or "")
    turn = row.get("turn")
    item = {"id": int(row.get("id") or 0), "turn": turn, "new": int(row.get("id") or 0) > int(seen or 0),
            "title": "", "meta": "", "body": "", "entries": []}
    if kind == "mind" and isinstance(p.get("entries"), list) and p.get("entries"):
        item["title"] = f"t{turn}" if turn else "속마음"
        item["entries"] = _mind_entries(p)
        return item
    item["title"] = str(p.get("title") or "").strip() or (f"t{turn}" if kind == "mind" and turn else "(제목 없음)")
    item["meta"] = _feed_meta(p, turn)
    item["body"] = str(p.get("body") or "").strip()
    if kind == "mind":
        item["entries"] = _mind_entries({"notes": p.get("notes") or []})
    return item


def view_feed_for(app: str):
    """앱 하나의 데이터 함수 — {items(최신 위, ≤ FEED_MAX), total}. 부르면 이 사람의 본 자리를 그 앱 최대 id 로 올린다
    (`new` 는 올리기 **전** 기준 — 그 화면에서는 "새" 칩이 보이고 다음에 열면 없다). GET 이 남기는 기록 = 읽음 표시."""
    def _view(channel_id: str, uid: str) -> Dict[str, Any]:
        import sqlite_store
        kind = _feed_kind(app)
        seen = sqlite_store.get_activity_seen(channel_id, uid, app)
        rows = sqlite_store.read_channel_mail(channel_id, FEED_MAX, kinds=(kind,)) or []
        items = [feed_item(r, seen) for r in rows]
        if rows:
            sqlite_store.set_activity_seen(channel_id, uid, app, max(int(r.get("id") or 0) for r in rows))
        return {"items": items, "total": sqlite_store.count_turn_mail(channel_id, kind, 0)}
    _view.__name__ = f"view_feed_{app}"
    return _view


def feed_badges(channel_id: str, uid: str, apps: Optional[List[str]] = None) -> Dict[str, int]:
    """앱마다 안 본 행 수(C69 §7-2 — 행 = 메시지 하나 · 종류 하나)."""
    import sqlite_store
    out: Dict[str, int] = {}
    for app, kind in FEED_APPS:
        if apps is not None and app not in apps:
            continue
        out[app] = sqlite_store.count_turn_mail(channel_id, kind, sqlite_store.get_activity_seen(channel_id, uid, app))
    return out


def view_people(channel_id: str, uid: str) -> Dict[str, Any]:
    """인물 탭 목록 — 내 PC 페이지 맨 위, 다음 인물(character), 다음 그 밖(장소·세력 등), 각 이름순. 삭제 표식 제외."""
    import wiki_store
    mine = wiki_store.pc_page_id(channel_id, uid) or ""
    pages = [p for p in (wiki_store.list_pages(channel_id) or [])
             if str(p.get("status") or "") != "deleted"]

    def _rank(p):
        return (0 if p["page_id"] == mine else 1 if p.get("kind") == "character" else 2,
                str(p.get("kind") or ""), str(p.get("name") or ""))
    out = []
    for p in sorted(pages, key=_rank):
        out.append({"page_id": p["page_id"], "kind": p.get("kind"), "name": p.get("name"),
                    "aliases": p.get("aliases") or [], "mine": p["page_id"] == mine})
    return {"pages": out}


def view_person(channel_id: str, uid: str, page_id: str) -> Optional[Dict[str, Any]]:
    """인물 한 장 — lore 절(정본, 시트 순서) + play 절(쌓인 것). 채널 밖 page_id 는 None(=404)."""
    import wiki_store
    page = wiki_store.get_page(channel_id, page_id)
    if not page or str(page.get("status") or "") == "deleted":
        return None
    lore = [{"section": k, "body": v} for k, v in (wiki_store.get_lore_sections(channel_id, page_id) or {}).items()]
    play = [{"section": s.get("section"), "body": s.get("body") or "", "updated_turn": s.get("updated_turn")}
            for s in (wiki_store.get_sections(channel_id, page_id, owner="play") or [])
            if str(s.get("body") or "").strip()]
    return {"page": page, "lore": lore, "play": play,
            "mine": (wiki_store.pc_page_id(channel_id, uid) or "") == page_id}


def view_motion(channel_id: str, uid: str) -> Dict[str, Any]:
    """[2026-10-25 C74 판4 W3] 진행 중 앱 — 아크 · 휘는 목표 · 시계 · 약속/사안 · 서 있는 상황 · 끝난 것(부품 쪽, 보기만).
    재료는 렌더 Slot 15 와 같은 `in_motion.collect`(이번 턴 줄만 뺀다 — 홈 "이번 턴" 줄이 따로 있다). 고치기는 OOC."""
    import activity_edit as _ae
    import in_motion
    return {"parts": in_motion.activity_parts(in_motion.collect(channel_id)), "rev": _ae._turn(channel_id), "view": "motion"}


def view_modules(channel_id: str, uid: str) -> Dict[str, Any]:
    """[2026-10-23 C72 A5] 모듈 앱 — 부품 쪽 {parts, rev, view}. 옛 "선언" 앱(view_decl)을 대신한다.
    카드 순서: 토글 코드 모듈(등록 순, 설정 칸은 접기 안) → 올린 모듈(번호순, 들어 있는 것은 접기 안) → 늘 도는 것.
    쓰기는 세계 등급 그대로(/api/act mod.toggle · mod.set, view="modules") — 스위치가 곧 고치기라 편집 연필은 없다."""
    import module_frame as _mf
    import output_router as _or
    import activity_edit as _ae
    parts: List[Dict[str, Any]] = []
    for m in _mf.on_shelf("toggle"):
        if m.toggle is None:
            continue
        on = _mf.is_on(m, channel_id)
        inner: List[Dict[str, Any]] = [{"t": "switch", "label": "사용", "on": bool(on),
                                         "act": {"action": "mod.toggle", "args": {"module": m.id}}}]
        fold: List[Dict[str, Any]] = []
        for st in (m.settings or ()):
            try:
                v = st.get(channel_id)
            except Exception as e:
                logger.debug(f"[Activity] setting {m.id}.{st.key} skipped: {e}")
                continue
            act = {"action": "mod.set", "args": {"module": m.id, "key": st.key}}
            if st.kind == "switch":
                fold.append({"t": "switch", "label": st.label, "on": bool(v), "act": act})
            elif st.kind == "select":
                fold.append({"t": "select", "label": st.label, "value": str(v),
                             "options": [[str(a), str(b)] for a, b in (st.options or ())], "act": act})
        if fold:
            inner.append({"t": "fold", "title": "설정", "parts": fold})
        parts.append({"t": "card", "title": m.name, "icon": "blocks", "chip": "" if on else "꺼짐", "parts": inner})
    bl = []
    try:
        bl = _or.bundles(channel_id)
    except Exception as e:
        logger.debug(f"[Activity] bundles skipped: {e}")
    for b in bl:
        inner = [{"t": "switch", "label": "사용", "on": bool(b["on"]),
                  "act": {"action": "mod.toggle", "args": {"module": f"#{b['no']}"}}},
                 {"t": "text", "text": _or.counts_line(b), "muted": True}]
        rws = [[lb, ", ".join(b["names"][lb])] for lb in _or.KIND_LABELS if (b.get("names") or {}).get(lb)]
        if rws:
            inner.append({"t": "fold", "title": "들어 있는 것", "parts": [{"t": "rows", "rows": rws}]})
        parts.append({"t": "card", "title": f"#{b['no']} {b['label']}", "icon": "scroll",
                      "chip": "" if b["on"] else "꺼짐", "parts": inner})
    if not bl:
        parts.append({"t": "card", "title": "올린 모듈", "icon": "scroll",
                      "parts": [{"t": "empty", "text": "올린 모듈이 없어요."}]})
    parts += _app_cards(channel_id)   # [2026-10-23 C73 M7a] 코드 앱 — 올린 모듈 다음, 늘 도는 것 앞
    always: List[Dict[str, Any]] = []
    core = [m.name for m in _mf.on_shelf("core")]
    if core:
        always.append({"t": "text", "text": "핵심 · " + " · ".join(core)})
    cfg = []
    for m in _mf.on_shelf("config"):
        try:
            cfg.append([m.name, m.status(channel_id) if m.status else ""])
        except Exception as e:
            logger.debug(f"[Activity] status {m.id} skipped: {e}")
            cfg.append([m.name, "?"])
    if cfg:
        always.append({"t": "rows", "rows": cfg})
    try:
        n_free = _or.free_count(channel_id)
    except Exception:
        n_free = 0
    if n_free:
        always.append({"t": "text", "text": f"그 밖 {n_free}개 (손으로 쓴 것 · 한 줄 서술) · 늘 켬", "muted": True})
    if always:
        parts.append({"t": "card", "title": "늘 도는 것", "icon": "layers", "parts": always})
    return {"parts": parts, "rev": _ae._turn(channel_id), "view": "modules"}


def _app_cards(channel_id: str) -> List[Dict[str, Any]]:
    """[2026-10-23 C73 M7a] 모듈 앱의 코드 앱 카드 — 번호순. 스위치(mod.toggle `#n`) · 설정 접기(mod.set) · 들어 있는 것 · 최근 오류.
    코드 앱이 하나도 없으면 카드 0(빈 카드를 세우지 않는다 — 대부분의 채널은 코드 앱을 안 쓴다)."""
    try:
        import app_host
        al = app_host.list_apps(channel_id)
    except Exception as e:
        logger.debug(f"[Activity] apps skipped: {e}")
        return []
    out: List[Dict[str, Any]] = []
    for a in al:
        word = f"#{a['no']}" if a.get("no") else a["id"]
        inner: List[Dict[str, Any]] = [{"t": "switch", "label": "사용", "on": bool(a["on"]),
                                         "act": {"action": "mod.toggle", "args": {"module": word}}}]
        _miss = app_host.last_miss(channel_id, a["id"]) if a["on"] else ""
        if _miss:   # [2026-10-24 C73 M7b] 지난 턴 신고 콜이 실패 · 시간 초과 — 그 턴 신고 없이 갔다
            inner.append({"t": "text", "text": f"지난 턴 신고 못 받음 · {_miss}", "muted": True})
        fold: List[Dict[str, Any]] = []
        for st in (a.get("manifest") or {}).get("settings") or []:
            act = {"action": "mod.set", "args": {"module": word, "key": st["key"]}}
            v = (a.get("settings") or {}).get(st["key"])
            if st["kind"] == "switch":
                fold.append({"t": "switch", "label": st["label"], "on": bool(v), "act": act})
            else:
                fold.append({"t": "select", "label": st["label"], "value": str(v),
                             "options": [[str(o[0]), str(o[1])] for o in st.get("options") or []], "act": act})
        if fold:
            inner.append({"t": "fold", "title": "설정", "parts": fold})
        rws = [["훅", " · ".join(a.get("hooks") or []) or "없음"],
               ["화면", {"parts": "부품", "html": "HTML", "none": "없음"}.get(a.get("screen"), "?")]]
        _tags = app_host.tag_spec(a)
        if _tags:
            rws.append(["신고", f"{len(_tags)}종 · " + " · ".join(t["name"] for t in _tags[:8])
                        + (" …" if len(_tags) > 8 else "")])
        if a.get("version"):
            rws.append(["판", a["version"]])
        if not a.get("pkg"):
            rws.append(["묶음", "파일을 못 읽어요 — 다시 올려 주세요"])
        if a.get("errors"):
            rws.append(["최근 오류", f"{a['errors']}회 · {a.get('last_error') or ''}"])
        inner.append({"t": "fold", "title": "들어 있는 것", "parts": [{"t": "rows", "rows": rws}]})
        out.append({"t": "card", "title": app_host.label_of(a), "icon": a.get("icon") or "layers",
                    "chip": "" if a["on"] else "꺼짐", "parts": inner})
    return out


def view_edit(channel_id: str, uid: str) -> Dict[str, Any]:
    """[2026-10-23 C60 M2c] 고치기 탭 — 고칠 수 있는 값과 rev(지금 턴). 쓰기는 /api/act."""
    import activity_edit
    return activity_edit.build_edit_view(channel_id, uid)


def _register_screens() -> None:
    """[2026-10-23 C60 M2b] 탭 = 모듈이 선언한 화면. 옛 손으로 쓴 표(이 파일의 `_VIEWS` · app.js 의 SHOW/TAB_ICON ·
    index.html 의 버튼 넷)가 여기 선언으로 접힌다. 새 모듈은 `add_screen` 한 줄 + 범용 cards 모양 데이터면 된다.

    [2026-10-23 C67 A1] 탭 = 앱. 상태 → "상태창"(C66 §3.2). 고치기는 서랍 밖(drawer False) — 데이터 경로
    `/api/view/edit` 는 그대로이고, 각 앱의 편집 모드가 `edit` 고르개로 제 몫 묶음만 그린다(C67 §2.2)."""
    import module_frame as _mf
    _mf.add_screen("screen", _mf.Screen("status", "상태창", "activity", 10, view_status, renderer="status",
                                        edit=("pc", "values", "list:declared", "world")))
    # [2026-10-25 C74 판4 W3] 진행 중 — 주인 = goal 모듈(core, 늘 켬). 보기만(edit 없음 — 고치기는 OOC, 판정 9).
    _mf.add_screen("goal", _mf.Screen("motion", "진행 중", "orbit", 12, view_motion, renderer="parts"))
    _mf.add_screen("screen", _mf.Screen("archive", "보관함", "archive", 20, view_archive, renderer="archive",
                                        edit=("list:builtin",)))
    _mf.add_screen("wiki", _mf.Screen("people", "인물", "users", 30, view_people, renderer="people",
                                      edit=("relations",)))
    # [2026-10-23 C69 A3] 편지 · 소식 · 속마음 — 주인은 도착물(board) · 속마음(mind) 모듈. 모듈을 끈 채널이면 서랍에서 빠진다.
    _mf.add_screen("board", _mf.Screen("mail", "편지", "mail", 32, view_feed_for("mail"), renderer="feed"))
    _mf.add_screen("board", _mf.Screen("board", "소식", "newspaper", 34, view_feed_for("board"), renderer="feed"))
    _mf.add_screen("mind", _mf.Screen("mind", "속마음", "brain", 36, view_feed_for("mind"), renderer="feed"))
    # [2026-10-23 C72 A5] 선언 앱 → 모듈 앱(부품 쪽). 주인 = screen(늘 켬) — 꺼진 모듈도 여기서 켜야 하니까.
    _mf.add_screen("screen", _mf.Screen("modules", "모듈", "blocks", 40, view_modules, renderer="parts"))
    _mf.add_screen("screen", _mf.Screen("edit", "고치기", "pencil", 50, view_edit, renderer="edit", drawer=False))


_register_screens()
# 탭 → 데이터 함수. 등록부에서 만든다(스모크가 항목을 갈아끼울 수 있게 사전으로 둔다). 나중에 붙은 탭은 등록부에서 찾는다.
_VIEWS = {s.tab: s.view for s in __import__("module_frame").screens()}


def _view_fn(tab: str):
    fn = _VIEWS.get(tab)
    if fn is None:
        import module_frame as _mf
        s = _mf.screen(tab)
        fn = s.view if s else None
    return fn


def screens_on(channel_id: str) -> list:
    """[2026-10-23 C69 A3] 이 채널에서 주인 모듈이 켜진 화면만 — `!모듈`로 게시판 · 속마음을 끈 채널이면 그 앱이 빠진다.
    토글 없는 모듈(상태창 · 위키 · 출력룰)은 늘 켬(module_frame.is_on)."""
    import module_frame as _mf
    owner = {s.tab: m for m in _mf.REGISTRY.values() for s in m.screens}
    out = [s for s in _mf.screens() if owner.get(s.tab) is None or _mf.is_on(owner[s.tab], channel_id)]
    try:   # [2026-10-23 C73 M7a] 켜진 코드 앱 — 채널마다 다른 확장 타일(kind extension · tab app:<id>)
        import app_host
        out += app_host.screens_for(channel_id)
    except Exception as e:
        logger.debug(f"[Activity] app screens skipped: {e}")
    return out


async def handle_tabs(request: web.Request) -> web.Response:
    """[C60 M2b] 탭 줄 — [{tab, label, icon, renderer}]. 아이콘은 이름만(SVG 는 늘 app.js 표에서).
    [C67 A1] 앱 목록이 됐다 — 칸 셋(drawer · kind · edit)을 더 싣는다. 경로 이름은 호환으로 그대로.
    [C69 A3] 주인 모듈이 꺼진 앱은 뺀다(screens_on)."""
    _uid, channel_id = _auth(request)
    return _json({"tabs": [{"tab": s.tab, "label": s.label, "icon": s.icon, "renderer": s.renderer,
                            "drawer": bool(s.drawer), "kind": str(s.kind or "builtin"), "edit": list(s.edit or ())}
                           for s in screens_on(channel_id)]})


# =========================================================
# 홈 (C67 A1)
# =========================================================

HOME_SUMMARY_MAX_LINES = 3
HOME_SUMMARY_LINE_CHARS = 60
_HOME_SUMMARY_SECTIONS = ("세계", "관계", "장면")   # 이 순서로, 섹션마다 한 줄. PC(지금 카드가 실음)·기록은 안 싣는다
_LEAD_SYMBOL = re.compile(r"^[^\w\s]+\s*")
_GAUGE_BARS = re.compile(r"[▰▱]+\s*")


def home_summary(channel_id: str) -> List[str]:
    """홈 상태창 요약 — 세계 · 관계 · 장면 섹션마다 앞 두 행을 ' · '로 이은 한 줄(60자 컷), 줄 ≤ 3.
    한 행 = "이름 값 첫 줄"(값이 이미 이름으로 시작하면 값만), 글자 막대(▰▱)는 떼고 n/N 만 남긴다.
    섹션 제목은 임베드 시절 이모지 머리(🌍 세계)를 떼고 맞춘다. 실패하면 [](로그 debug)."""
    import status_panel
    try:
        data = status_panel.build_turn_embed_data(channel_id) or {}
    except Exception as e:
        logger.debug(f"[Activity] home summary skipped: {e}")
        return []
    by_name: Dict[str, List[Any]] = {}
    for sec in data.get("sections") or []:
        try:
            title, rows = sec[0], sec[1]
        except Exception:
            continue
        name = _LEAD_SYMBOL.sub("", str(title or "")).strip()
        by_name.setdefault(name, rows or [])
    out: List[str] = []
    for name in _HOME_SUMMARY_SECTIONS:
        parts: List[str] = []
        for row in (by_name.get(name) or [])[:2]:
            try:
                label, value = str(row[0] or "").replace("​", "").strip(), row[1]
            except Exception:
                continue
            first = str(value or "").replace("​", "").strip().split("\n", 1)[0].strip()
            first = _GAUGE_BARS.sub("", first).strip()          # 요약은 글자 막대 없이 n/N 만
            if first and label and not first.startswith(label):
                first = f"{label} {first}"
            if first:
                parts.append(first)
        if parts:
            line = " · ".join(parts)
            if len(line) > HOME_SUMMARY_LINE_CHARS:
                line = line[:HOME_SUMMARY_LINE_CHARS - 1].rstrip() + "…"
            out.append(line)
        if len(out) >= HOME_SUMMARY_MAX_LINES:
            break
    return out


def view_home(channel_id: str, uid: str) -> Dict[str, Any]:
    """홈 데이터 — 지금 카드(= 압축 턴 임베드와 같은 데이터, 없으면 None) + 상태창 요약 줄. 칸마다 따로 실패한다.
    [2026-10-23 C69 A3] + badges(앱마다 안 본 행 수) · this_turn(이번 턴에 온 것 [{app, label, count}]).
      켜진 앱 몫만 싣는다(서랍에 없는 앱의 배지 · 칩은 낼 곳이 없다)."""
    import status_panel
    now = None
    try:
        now = status_panel.build_turn_compact_data(channel_id)
    except Exception as e:
        logger.debug(f"[Activity] home now skipped: {e}")
    on = None
    try:
        on = [s.tab for s in screens_on(channel_id)]
    except Exception as e:
        logger.debug(f"[Activity] home apps skipped: {e}")
    badges: Dict[str, int] = {}
    try:
        badges = feed_badges(channel_id, uid, on)
    except Exception as e:
        logger.debug(f"[Activity] home badges skipped: {e}")
    this_turn: List[Dict[str, Any]] = []
    try:
        this_turn = [x for x in status_panel.this_turn_items(channel_id) if on is None or x.get("app") in on]
    except Exception as e:
        logger.debug(f"[Activity] home this turn skipped: {e}")
    return {"now": now, "summary": home_summary(channel_id), "badges": badges, "this_turn": this_turn}


async def handle_home(request: web.Request) -> web.Response:
    """[C67 A1] GET /api/home — 세션 필수."""
    uid, channel_id = _auth(request)
    try:
        return _json(view_home(channel_id, uid))
    except Exception as e:
        logger.warning(f"[Activity] home 실패: {e}")
        return _json({"error": "internal"}, 500)


async def handle_act(request: web.Request) -> web.Response:
    """[C60 M2c] 편집 한 건 — {action, args, rev}. 판정·잠금·쓰기는 activity_edit.act 한 곳."""
    uid, channel_id = _auth(request)
    try:
        payload = await request.json()
    except web.HTTPException:
        raise
    except Exception:
        return _json({"error": "bad request"}, 400)
    import activity_edit
    try:
        status, body = await activity_edit.act(channel_id, uid, payload)
    except Exception as e:
        logger.warning(f"[Activity] act 실패: {e}")
        return _json({"error": "internal"}, 500)
    return _json(body, status)


async def handle_view(request: web.Request) -> web.Response:
    uid, channel_id = _auth(request)
    tab = request.match_info.get("tab", "")
    if tab.startswith("app:"):   # [2026-10-23 C73 M7a] 코드 앱 화면 — Lua 를 부르니 비동기(app_host.view)
        import app_host
        app_id = app_host.app_id_of_tab(tab)
        if not app_id or app_host.get_app(channel_id, app_id) is None:
            raise web.HTTPNotFound()
        try:
            return _json(await app_host.view(channel_id, uid, app_id))
        except Exception as e:
            logger.warning(f"[Activity] app view {tab} 실패: {e}")
            return _json({"error": "internal"}, 500)
    fn = _view_fn(tab)
    if fn is None:
        raise web.HTTPNotFound()
    try:
        return _json(fn(channel_id, uid))
    except Exception as e:
        logger.warning(f"[Activity] view {request.path} 실패: {e}")
        return _json({"error": "internal"}, 500)


async def handle_person(request: web.Request) -> web.Response:
    uid, channel_id = _auth(request)
    page_id = str(request.match_info.get("page_id", ""))[:128]
    try:
        data = view_person(channel_id, uid, page_id)
    except Exception as e:
        logger.warning(f"[Activity] person 실패: {e}")
        return _json({"error": "internal"}, 500)
    if data is None:
        raise web.HTTPNotFound()
    return _json(data)


# =========================================================
# 앱 · 기동
# =========================================================

def build_app() -> web.Application:
    """경로표. 여기 없는 경로는 aiohttp 가 404 를 낸다(본문에 아무 정보 없음)."""
    app = web.Application(client_max_size=MAX_BODY)
    for path in STATIC_FILES:
        app.router.add_get(path, handle_static)
    app.router.add_post("/api/session", handle_session)
    app.router.add_get("/api/tabs", handle_tabs)          # [C60 M2b]
    app.router.add_get("/api/home", handle_home)          # [C67 A1]
    app.router.add_post("/api/act", handle_act)           # [C60 M2c]
    app.router.add_get("/api/view/people/{page_id}", handle_person)
    app.router.add_get("/api/view/{tab}", handle_view)
    return app


async def start(client) -> bool:
    """on_ready 에서 한 번. 이미 떠 있으면 아무것도 안 한다(on_ready 는 재연결마다 다시 불린다).

    꺼져 있거나(.env 의 DISCORD_CLIENT_ID/SECRET 없음) 실패하면 False — 봇은 그대로 돌고,
    💠 는 `running()` 이 거짓이니 종전 보관함 창으로 내려간다.
    """
    global _started, _runner, _client
    if _started:
        return True
    if not getattr(config, "ACTIVITY_ENABLED", False):
        logger.info("[Activity] off — .env 에 DISCORD_CLIENT_ID/DISCORD_CLIENT_SECRET 없음")
        return False
    _client = client
    port = int(getattr(config, "ACTIVITY_PORT", 8080) or 8080)
    try:
        _runner = web.AppRunner(build_app(), access_log=None)
        await _runner.setup()
        await web.TCPSite(_runner, "127.0.0.1", port).start()
    except Exception as e:
        logger.warning(f"[Activity] 서버 기동 실패(port {port}): {e}")
        _runner = None
        return False
    _started = True
    logger.info("[Activity] 서버 기동 127.0.0.1:%s (host=%s)", port, getattr(config, "ACTIVITY_HOST", ""))
    return True
