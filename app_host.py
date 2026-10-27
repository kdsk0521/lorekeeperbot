# -*- coding: utf-8 -*-
"""[2026-10-23 C73 M7a] 코드 앱 호스트 — `.lkapp` 묶음을 채널에 올리고, 턴 · 화면에서 그 Lua 를 부른다.

스펙  파티쳇수정/output_artifact/C73_모듈틀_M7코드앱_지도·스펙_2026-10-23.md (§2 묶음 · §4 lk · §5 저장 · §6 급식 · §7 화면 · §8 쓰기)

번역 중심(10-23 레티어스 "번역이 중점") — 남의 파일을 알아먹는 호환층은 없다. 실행기(lua_runtime)는 Risu 이름을 모르고,
앱은 우리 `lk` 만 본다. Risu 이름이 필요하면 이식 가이드의 받침 조각을 앱이 제 안에 붙인다.

흐름
  올리기   `!모듈 올리기` + .lkapp → parse_package(검사 다섯) → 채널 폴더 apps/<id>.lkapp + 앱 표(sqlite apps) + 고정 번호
  턴 앞    compact(스냅숏 워터마크 앞) → turn_start: on_input → 쌓인 보고 꺼냄 → feed() → ctx.app_feed_block(Slot 29)
  규칙     rules.md → rules_block(Slot 23, `!룰` 글 뒤) — 올릴 때 정해지는 고정 글이라 캐시가 안 깨진다
  산문 뒤  AFTER_PROSE 정거장 → on_output(산문, {turn, message_id}) → [M7b] 신고 정산 시작(tags 단 앱이 켜져 있을 때만)
  신고     [M7b] 제 차선의 AppTags 콜(light) 하나 → 거름(선언된 태그 · 칸 · 근거) → app_tags 장부 → 앱마다 on_tags(list)
           다음 턴은 정산이 끝날 때까지 ⏳ 로 잡힌다(상한 60초 — 넘거나 실패하면 그 턴 신고 없이 간다 · 늦은 결과는 버린다)
  화면     view(who) → {parts} | {html} · 누르기 = Activity `app.click` → on_click → 쓰기 모음 커밋 → 편집 일지(app.kv, 쓴 값 그대로)
  저장     sqlite app_kv_log(행이 정본) — 턴 훅 쓰기는 `!다시` 워터마크 트림이 되감고, 버튼 쓰기는 일지 재적용이 다시 얹는다

Lua 는 봇 프로세스에서 안 돈다(10-24 보안 검토) — 작업 프로세스(lua_runtime.POOL)에 요청 한 줄을 보내고, 벽시계 상한을 넘기면
그 프로세스를 죽인다. 올리기 검사(맨 위 코드 실행 · 훅 목록)도 같은 길이다. 장부는 호출 **앞**에 읽어 요청에 싣고,
쓰기(lk.set · lk.report)는 응답으로 받아 훅이 오류 없이 끝났을 때만 **한 번에** 적는다 — 실패한 호출은 아무것도 안 남긴다.
"""
from __future__ import annotations

import asyncio
import io
import json
import logging
import os
import re
import time
import zipfile
from typing import Any, Dict, List, Optional, Tuple

import config
import lua_runtime
from lua_runtime import LuaFail

logger = logging.getLogger(__name__)

HOOKS = ("on_input", "feed", "on_output", "on_tags", "on_click", "view")
SCREENS = ("parts", "html", "none")
ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,31}$")
KEY_RE = re.compile(r"^[A-Za-z0-9_\-.:]{1,32}$")
ICON_RE = re.compile(r"^[a-z0-9-]{1,32}$")
MAX_PACKAGE_BYTES = 5 * 1024 * 1024
MAX_MANIFEST_BYTES = 64 * 1024
MAX_MAIN_BYTES = 1024 * 1024
MAX_RULES_CHARS = 30000
FEED_DEFAULT = 2000
FEED_CAP = 6000
MAX_HTML_CHARS = 2 * 1024 * 1024
MAX_REPORT_CHARS = 2000
MAX_REPORTS = 10
MAX_KEY_CHARS = 64
MAX_KV_BYTES = 1024 * 1024
MAX_SETTINGS = 20
MAX_OPTIONS = 20
HISTORY_MAX = 50
MAX_PARTS = 400
MAX_RECEIPT = 120
# [2026-10-24 C73 M7b] 신고(tags) — 명세 상한 · 정산
TAG_NAME_RE = re.compile(r"^[0-9A-Za-z_가-힣][0-9A-Za-z_\-가-힣]{0,31}$")
FIELD_TYPES = ("number", "text")
MAX_TAGS = 20               # 앱 하나가 받는 태그 수
MAX_TAG_FIELDS = 8          # 태그 하나의 칸 수
MAX_SAY = 200               # 태그 설명(콜에 실린다)
MAX_TAG_ROWS = 20           # 앱 하나 · 한 턴에 받는 신고 건수
MAX_TAG_TEXT = 200          # text 칸 · 근거 길이
TAGS_KEEP_TURNS = 60        # 장부에 남기는 턴(lk.tags 는 50턴까지 본다)
SETTLE_LIMIT = 60.0         # 초 — 콜 상한이자 다음 턴이 기다리는 상한
HOLD_EMOJI = "⏳"
TAGS_PROSE_CAP = 12000
TAGS_INPUT_CAP = 4000
TAGS_MAX_OUTPUT_TOKENS = 8192

REPORT_KEY = "_reports"     # 앱 장부 안 예약 키(앱은 `_` 로 시작하는 키를 못 쓴다)
# [10-24 M7b 검토] `!다시` 재적용이 얹는 누르기 보고 — 누르기마다 제 열쇠(`_reports@<표>`, 절대값 → 두 번 얹어도 같다).
#   옛 꼴은 합친 `_reports` 통째를 일지에 적어, 되감긴 턴의 훅 보고(on_output · on_tags)까지 되살렸다.
REPLAY_REPORT_PREFIX = "_reports@"
NUM_PREFIX = "app:"         # 고정 번호 줄 이름(output_router 번호 줄을 같이 쓴다 — `!모듈 3 off`)
TAB_PREFIX = "app:"         # Activity tab

APP_FEED_LABEL = "<앱:{name}>"
APP_REPORT_LABEL = "<앱 보고:{name}>"
# [C73 §6.3] 보고 머리 — C70 핸드아웃 머리(HANDOUT_FEED_NOTE)와 같은 결: 일어난 것은 사실 · 산문이 따라 씀 · 새 사건 0.
#   [10-24 M7b] 보고가 버튼(on_click)만이 아니라 신고 정산(on_tags)에서도 나온다 — 머리가 "화면에서 한 일"로만 읽히지 않게.
APP_REPORT_NOTE = ("What this app recorded since the last prose — the player's actions on its screen, or what it tallied "
                   "from the story: it happened — let the prose pick it up from here. It opens no new event.")
RULES_HEAD = "[앱: {name}]"

_PART_TYPES = ("card", "text", "rows", "empty", "switch", "select", "fold", "button", "chips")


def available() -> bool:
    return bool(lua_runtime.AVAILABLE)


# =====================================================================
# 묶음 — 검사 · 저장 · 읽기
# =====================================================================

def _apps_dir(channel_id: str) -> str:
    return os.path.join(config.CHANNELS_DIR, str(channel_id), "apps")


def _pkg_path(channel_id: str, app_id: str) -> str:
    return os.path.join(_apps_dir(channel_id), f"{app_id}.lkapp")


_Session = lua_runtime.Session   # 도구(lkapp_check · 스크린샷)가 쓰는 옛 이름 — 몸은 lua_runtime 에 있다(작업 프로세스와 같은 몸)


def _check_manifest(man: Dict[str, Any], strict: bool = True) -> Tuple[Dict[str, Any], List[str]]:
    """strict=False = 이미 올린 묶음을 다시 읽을 때 — 신고 명세가 이번 규칙에 안 맞으면 앱을 죽이지 않고 명세만 뺀다
    (M7a 때 거르지 않고 싣던 tags 가 있을 수 있다)."""
    errs: List[str] = []
    app_id = str(man.get("id") or "")
    if not ID_RE.fullmatch(app_id):
        errs.append("manifest id 는 영문 소문자 · 숫자 · `-` 로 1~32자예요(첫 글자는 영문 · 숫자)")
    name = str(man.get("name") or "").strip()
    if not name or len(name) > 40:
        errs.append("manifest name 이 비었거나 40자를 넘어요")
    screen = str(man.get("screen") or "parts")
    if screen not in SCREENS:
        errs.append("manifest screen 은 parts · html · none 중 하나예요")
    icon = str(man.get("icon") or "layers")
    if not ICON_RE.fullmatch(icon):
        icon = "layers"
    try:
        feed_max = int(man.get("feed_max", FEED_DEFAULT))
    except Exception:
        feed_max = FEED_DEFAULT
    feed_max = max(0, min(FEED_CAP, feed_max))
    settings: List[Dict[str, Any]] = []
    raw_settings = man.get("settings") or []
    if not isinstance(raw_settings, list) or len(raw_settings) > MAX_SETTINGS:
        errs.append(f"manifest settings 는 {MAX_SETTINGS}개까지인 목록이에요")
        raw_settings = []
    seen = set()
    for st in raw_settings:
        if not isinstance(st, dict):
            errs.append("settings 항목은 객체여야 해요")
            continue
        key = str(st.get("key") or "")
        kind = str(st.get("kind") or "")
        if not KEY_RE.fullmatch(key) or key in seen:
            errs.append(f"settings key `{key}` 가 틀렸거나 겹쳐요")
            continue
        seen.add(key)
        label = str(st.get("label") or key)[:40]
        if kind == "switch":
            settings.append({"key": key, "label": label, "kind": "switch", "default": bool(st.get("default", False))})
        elif kind == "select":
            opts = []
            for o in (st.get("options") or [])[:MAX_OPTIONS]:
                if isinstance(o, (list, tuple)) and len(o) == 2:
                    opts.append([str(o[0])[:40], str(o[1])[:40]])
                elif isinstance(o, str):
                    opts.append([o[:40], o[:40]])
            if not opts:
                errs.append(f"settings `{key}`: select 에 options 가 없어요")
                continue
            dflt = str(st.get("default") if st.get("default") is not None else opts[0][0])
            if dflt not in [o[0] for o in opts]:
                dflt = opts[0][0]
            settings.append({"key": key, "label": label, "kind": "select", "options": opts, "default": dflt})
        else:
            errs.append(f"settings `{key}`: kind 는 switch · select 예요")
    m = {"id": app_id, "name": name, "version": str(man.get("version") or "")[:20], "icon": icon,
         "screen": screen, "feed_max": feed_max, "settings": settings}
    if man.get("tags") is not None:           # [M7b] 신고 명세
        tags, terrs = _check_tags(man.get("tags"))
        if terrs and not strict:
            logger.warning("[App] %s: 저장된 묶음의 신고 명세가 규칙에 안 맞아 뺌(다시 올리면 고칠 수 있다) — %s",
                           app_id, "; ".join(terrs)[:300])
            tags, terrs = [], []
        errs += terrs
        if tags:
            m["tags"] = tags
    return m, errs


def _check_tags(raw: Any) -> Tuple[List[Dict[str, Any]], List[str]]:
    """manifest `tags: [{name, say, fields: {칸: "number"|"text"}}]` → (거른 명세, 오류들). 오류가 하나라도 있으면 올리기 거부.
    say 는 신고 콜 프롬프트에 실리니 줄바꿈을 걷고 200자로 자른다."""
    errs: List[str] = []
    out: List[Dict[str, Any]] = []
    if not isinstance(raw, list) or len(raw) > MAX_TAGS:
        return [], [f"manifest tags 는 {MAX_TAGS}개까지인 목록이에요"]
    seen = set()
    for t in raw:
        if not isinstance(t, dict):
            errs.append("tags 항목은 객체여야 해요")
            continue
        name = str(t.get("name") or "")
        if not TAG_NAME_RE.fullmatch(name) or name in seen:
            errs.append(f"tags name `{name[:32]}` 가 틀렸거나 겹쳐요(한글 · 영문 · 숫자 · `_` `-`, 32자까지)")
            continue
        seen.add(name)
        say = " ".join(str(t.get("say") or "").split())
        if not say:
            errs.append(f"tags `{name}`: say(언제 무엇을 신고하나)가 비었어요")
            continue
        fields_raw = t.get("fields") if t.get("fields") is not None else {}
        if not isinstance(fields_raw, dict) or len(fields_raw) > MAX_TAG_FIELDS:
            errs.append(f"tags `{name}`: fields 는 {MAX_TAG_FIELDS}칸까지인 객체예요")
            continue
        fields: Dict[str, str] = {}
        bad = False
        for fk, ft in fields_raw.items():
            fk = str(fk)
            if not TAG_NAME_RE.fullmatch(fk) or str(ft) not in FIELD_TYPES:
                errs.append(f"tags `{name}`: 칸 `{fk[:32]}` — 이름이 틀렸거나 종류가 number · text 가 아니에요")
                bad = True
                break
            fields[fk] = str(ft)
        if bad:
            continue
        out.append({"name": name, "say": say[:MAX_SAY], "fields": fields})
    return out, errs


def read_package(data: bytes, strict: bool = True) -> Tuple[Optional[Dict[str, Any]], List[str]]:
    """묶음 읽기(Lua 안 돎) — zip · 상한 · manifest. → (pkg | None, 오류들). pkg = {manifest, main, rules, hooks: []}.
    [10-24 보안 검토] 크기는 zip 머리(꾸밀 수 있다)를 믿지 않고 **풀면서 센다** — 작은 묶음이 GB 로 부푸는 폭탄을 상한에서 끊는다."""
    if len(data or b"") > MAX_PACKAGE_BYTES:
        return None, [f"묶음이 너무 커요({len(data) // 1024}KB — 5MB까지)"]
    try:
        z = zipfile.ZipFile(io.BytesIO(data))
        names = set(z.namelist())
    except Exception:
        return None, [".lkapp 은 zip 이어야 해요"]

    def _read(name: str, cap: int) -> bytes:
        info = z.getinfo(name)
        if info.file_size > cap:
            raise ValueError(f"{name} 이(가) 너무 커요({info.file_size // 1024}KB — {cap // 1024}KB까지)")
        buf = bytearray()
        with z.open(info) as fp:
            while True:
                chunk = fp.read(65536)
                if not chunk:
                    break
                buf += chunk
                if len(buf) > cap:
                    raise ValueError(f"{name} 이(가) 너무 커요({cap // 1024}KB까지)")
        return bytes(buf)

    missing = [n for n in ("manifest.json", "main.lua") if n not in names]
    if missing:
        return None, [f"{n} 이(가) 없어요" for n in missing]
    try:
        man = json.loads(_read("manifest.json", MAX_MANIFEST_BYTES).decode("utf-8-sig"))
    except Exception as e:
        return None, [f"manifest.json 을 못 읽어요 — {e}"]
    if not isinstance(man, dict):
        return None, ["manifest.json 은 객체여야 해요"]
    m, errs = _check_manifest(man, strict)
    if errs:
        return None, errs
    try:
        main = _read("main.lua", MAX_MAIN_BYTES).decode("utf-8-sig")
    except Exception as e:
        return None, [f"main.lua — {e}"]
    rules = ""
    if "rules.md" in names:
        try:
            rules = _read("rules.md", MAX_RULES_CHARS * 4).decode("utf-8-sig").strip()
        except Exception as e:
            return None, [f"rules.md — {e}"]
        if len(rules) > MAX_RULES_CHARS:
            return None, [f"rules.md 가 너무 길어요({len(rules)}자 — {MAX_RULES_CHARS}자까지)"]
    return {"manifest": m, "main": main, "rules": rules, "hooks": []}, []


def parse_package(data: bytes, check: bool = True) -> Tuple[Optional[Dict[str, Any]], List[str]]:
    """**도구용**(lkapp_check · 스크린샷 · 스모크) — 읽기 + 이 프로세스 안에서 Lua 검사 · 훅 목록. 봇은 install(작업 프로세스)."""
    pkg, errs = read_package(data)
    if pkg is None:
        return None, errs
    if check:
        ok, err = lua_runtime.check_source(pkg["main"])
        if not ok:
            return None, [f"main.lua 를 못 불러요 — {err}"]
    pkg["hooks"] = list(lua_runtime.hooks_of(pkg["main"], HOOKS))
    return pkg, []


async def check_package(pkg: Dict[str, Any]) -> Tuple[bool, str]:
    """올리기 검사 — 작업 프로세스에서 맨 위 코드를 한 번 돌리고 훅 목록을 받는다(벽시계 CHECK_TIMEOUT). pkg["hooks"] 를 채운다."""
    try:
        resp = await lua_runtime.POOL.run({"op": "check", "source": pkg["main"], "hooks": list(HOOKS)},
                                          timeout=lua_runtime.CHECK_TIMEOUT)
    except LuaFail as e:
        return False, str(e)
    if not resp.get("ok"):
        return False, str(resp.get("error") or "?")
    pkg["hooks"] = [h for h in (resp.get("hooks") or []) if h in HOOKS]
    return True, ""


_PKG_CACHE: Dict[str, Tuple[float, Dict[str, Any]]] = {}


def load_pkg(channel_id: str, app_id: str) -> Optional[Dict[str, Any]]:
    """저장된 묶음 — 파일 mtime 이 같으면 캐시. 파일이 없거나 깨졌으면 None."""
    path = _pkg_path(channel_id, app_id)
    try:
        mt = os.path.getmtime(path)
    except OSError:
        _PKG_CACHE.pop(path, None)
        return None
    hit = _PKG_CACHE.get(path)
    if hit and hit[0] == mt:
        return hit[1]
    try:
        with open(path, "rb") as f:
            pkg, errs = read_package(f.read(), strict=False)   # Lua 안 돎 — 훅 목록은 올릴 때 앱 표(manifest.hooks)에 적어 둔 것
    except Exception as e:
        logger.warning("[App] 묶음 읽기 실패 %s: %s", path, e)
        return None
    if pkg is None:
        logger.warning("[App] 저장된 묶음이 깨짐 %s: %s", path, errs)
        return None
    _PKG_CACHE[path] = (mt, pkg)
    return pkg


# =====================================================================
# 앱 표 — 목록 · 번호 · 켬끔 · 설정
# =====================================================================

def _numbers(channel_id: str) -> Dict[str, int]:
    try:
        import output_router as _or
        return _or.module_numbers(channel_id)
    except Exception as e:
        logger.debug("[App] numbers skip: %s", e)
        return {}


def list_apps(channel_id: str) -> List[Dict[str, Any]]:
    """채널 앱 — [{id, name, no, on, version, icon, screen, manifest, settings(값), hooks, pkg, errors, last_error}] 번호순."""
    if not channel_id:
        return []
    try:
        import sqlite_store
        rows = sqlite_store.get_apps(channel_id)
    except Exception as e:
        logger.debug("[App] list skip: %s", e)
        return []
    if not rows:
        return []
    nums = _numbers(channel_id)
    out = []
    for r in rows:
        man = r.get("manifest") or {}
        pkg = load_pkg(channel_id, r["app_id"])
        out.append({"id": r["app_id"], "name": r.get("name") or r["app_id"],
                    "no": int(nums.get(NUM_PREFIX + r["app_id"]) or 0), "on": bool(r.get("on")),
                    "version": r.get("version") or "", "icon": man.get("icon") or "layers",
                    "screen": man.get("screen") or "parts", "manifest": man,
                    "settings": _settings_values(man, r.get("settings") or {}),
                    "hooks": [h for h in (man.get("hooks") or []) if h in HOOKS], "pkg": pkg,
                    "errors": int(r.get("errors") or 0), "last_error": r.get("last_error") or ""})
    out.sort(key=lambda a: (a["no"] or 10 ** 9, a["name"]))
    return out


def get_app(channel_id: str, app_id: str) -> Optional[Dict[str, Any]]:
    for a in list_apps(channel_id):
        if a["id"] == app_id:
            return a
    return None


def has_apps(channel_id: str) -> bool:
    try:
        import sqlite_store
        return bool(sqlite_store.get_apps(channel_id))
    except Exception:
        return False


def _settings_values(manifest: Dict[str, Any], stored: Dict[str, Any]) -> Dict[str, Any]:
    out = {}
    for st in manifest.get("settings") or []:
        v = stored.get(st["key"], st.get("default"))
        if st["kind"] == "switch":
            v = bool(v)
        elif st["kind"] == "select" and str(v) not in [o[0] for o in st.get("options") or []]:
            v = st.get("default")
        out[st["key"]] = v
    return out


def setting_def(app: Dict[str, Any], key: str) -> Optional[Dict[str, Any]]:
    for st in (app.get("manifest") or {}).get("settings") or []:
        if st.get("key") == key:
            return st
    return None


def set_on(channel_id: str, app_id: str, on: bool) -> Tuple[bool, str]:
    """켜고 끄기 — `!모듈` · Activity `mod.toggle` 이 같은 함수(module_frame.set_module_on 이 부른다)."""
    a = get_app(channel_id, app_id)
    if a is None:
        return False, "없는 앱이에요"
    import sqlite_store
    if not sqlite_store.set_app_fields(channel_id, app_id, on=bool(on)):
        return False, "저장 실패"
    return True, f"{label_of(a)} {'켬' if on else '끔'}"


def set_setting(channel_id: str, app_id: str, key: str, value: Any) -> Tuple[bool, str, Any]:
    """설정 칸 한 칸 → (ok, 한 줄, 옛 값). switch 는 참/거짓만 · select 는 options 안 값만."""
    a = get_app(channel_id, app_id)
    if a is None:
        return False, "없는 앱이에요", None
    st = setting_def(a, key)
    if st is None:
        return False, "그 설정 칸이 없어요", None
    old = a["settings"].get(key)
    if st["kind"] == "switch":
        if not isinstance(value, bool):
            return False, "켬/끔만 받아요", old
    else:
        value = str(value)
        if value not in [o[0] for o in st.get("options") or []]:
            return False, "고를 수 있는 값이 아니에요", old
    import sqlite_store
    rows = {r["app_id"]: r for r in sqlite_store.get_apps(channel_id)}
    stored = dict((rows.get(app_id) or {}).get("settings") or {})
    stored[key] = value
    if not sqlite_store.set_app_fields(channel_id, app_id, settings=stored):
        return False, "저장 실패", old
    return True, f"{label_of(a)} · {st['label']} {_opt_label(st, old)} → {_opt_label(st, value)}", old


def _opt_label(st: Dict[str, Any], v: Any) -> str:
    if st.get("kind") == "switch":
        return "켬" if v else "끔"
    for o in st.get("options") or []:
        if o[0] == str(v):
            return o[1]
    return str(v)


def label_of(a: Dict[str, Any]) -> str:
    return f"#{a['no']} {a['name']}" if a.get("no") else str(a.get("name") or a.get("id"))


# =====================================================================
# 올리기 · 지우기
# =====================================================================

async def install(channel_id: str, data: bytes) -> Tuple[bool, List[str]]:
    """`!모듈 올리기` — (ok, 알림 줄들). 같은 id = 새 판으로 갈아 끼움(켬끔 · 설정 · 장부 그대로).
    Lua 검사는 작업 프로세스에서(10-24 — 맨 위 코드가 봇 이벤트 루프를 붙잡지 않게)."""
    if not available():
        return False, ["⚠️ 코드 앱이 꺼져 있어요 — 서버에 lupa 가 없어요(`pip install lupa`)."]
    pkg, errs = read_package(data)
    if pkg is None:
        return False, ["⚠️ 못 올렸어요 — " + e for e in errs]
    ok_chk, err_chk = await check_package(pkg)
    if not ok_chk:
        return False, [f"⚠️ 못 올렸어요 — main.lua 를 못 불러요 — {err_chk}"]
    man = dict(pkg["manifest"], hooks=list(pkg["hooks"]))   # 훅 목록은 앱 표에 적어 둔다(읽을 때 Lua 를 안 돌리게)
    app_id = man["id"]
    others = [a for a in list_apps(channel_id) if a["id"] != app_id and a["name"] == man["name"]]
    if others:
        return False, [f"⚠️ 같은 이름의 앱이 이미 있어요 — {label_of(others[0])}. manifest name 을 바꿔 주세요."]
    had = get_app(channel_id, app_id)
    path = _pkg_path(channel_id, app_id)
    try:
        os.makedirs(_apps_dir(channel_id), exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "wb") as f:
            f.write(data)
        os.replace(tmp, path)
    except Exception as e:
        logger.warning("[App] 묶음 저장 실패 %s: %s", path, e)
        return False, ["⚠️ 묶음을 저장하지 못했어요 — 로그를 보세요."]
    _PKG_CACHE.pop(path, None)
    import sqlite_store
    if not sqlite_store.upsert_app(channel_id, app_id, man["name"], man["version"], man):
        return False, ["⚠️ 앱 표에 못 적었어요 — 로그를 보세요."]
    try:
        import output_router as _or
        no = _or.module_number(channel_id, NUM_PREFIX + app_id)
    except Exception as e:
        logger.debug("[App] number skip: %s", e)
        no = 0
    a = get_app(channel_id, app_id) or {"no": no, "name": man["name"], "on": True}
    head = f"✅ **{label_of(a)}**" + (f" v{man['version']}" if man["version"] else "")
    lines = [head + (" 새 판으로 갈아 끼움 — 장부 · 설정은 그대로" if had else " 올림")]
    lines.append("훅: " + (" · ".join(pkg["hooks"]) or "없음")
                 + f" · 화면: {dict(parts='부품', html='HTML', none='없음')[man['screen']]}"
                 + (f" · 고정 규칙 {len(pkg['rules'])}자" if pkg["rules"] else "")
                 + (f" · 신고 {len(man['tags'])}종(턴마다 신고 콜 +1)" if man.get("tags") else ""))
    if not a.get("on"):
        lines.append(f"꺼져 있어요 — `!모듈 {label_of(a).split()[0]} on`")
    logger.info("[App] 올림 ch=%s id=%s v=%s hooks=%s", channel_id, app_id, man["version"], pkg["hooks"])
    return True, lines


def remove(channel_id: str, app_id: str) -> Tuple[bool, str]:
    """`!모듈 지우기` — 묶음 · 앱 표 줄 · 장부. 번호 줄은 남긴다(다시 올리면 제 번호)."""
    a = get_app(channel_id, app_id)
    if a is None:
        return False, "없는 앱이에요"
    import sqlite_store
    sqlite_store.delete_app(channel_id, app_id)
    path = _pkg_path(channel_id, app_id)
    try:
        if os.path.exists(path):
            os.remove(path)
    except Exception as e:
        logger.warning("[App] 묶음 지우기 실패 %s: %s", path, e)
    _PKG_CACHE.pop(path, None)
    logger.info("[App] 지움 ch=%s id=%s", channel_id, app_id)
    return True, f"{label_of(a)} 지움 — 묶음 · 장부"


# =====================================================================
# 호출 — lk · 쓰기 모음
# =====================================================================

_LOCKS: Dict[Tuple[str, str], asyncio.Lock] = {}


def _lock(channel_id: str, app_id: str) -> asyncio.Lock:
    k = (str(channel_id), str(app_id))
    if k not in _LOCKS:
        _LOCKS[k] = asyncio.Lock()
    return _LOCKS[k]


def _turn(channel_id: str) -> int:
    try:
        import domain_manager
        return int((domain_manager.get_world_state(channel_id) or {}).get("turn_index", 0) or 0)
    except Exception:
        return 0


def _world(channel_id: str, who: str) -> Dict[str, Any]:
    """lk.world() — 우리 시계 · 위치 · 출석. 읽기 전용 사본."""
    out: Dict[str, Any] = {"pc": str(who or "")}
    try:
        import domain_manager
        w = dict(domain_manager.get_world_state(channel_id) or {})
        try:
            import game_world
            game_world._init_clock(w)
            out["calendar"] = game_world.format_calendar(w)
        except Exception:
            out["calendar"] = ""
        for k in ("year", "month", "day", "hour", "minute"):
            try:
                out[k] = int(w.get(k, 0) or 0)
            except Exception:
                out[k] = 0
        out["turn"] = int(w.get("turn_index", 0) or 0)
        out["time_slot"] = str(w.get("time_slot") or "")
        out["place"] = str(w.get("current_location") or w.get("location") or "")
        out["weather"] = str(w.get("weather") or "")
        out["season"] = str(w.get("season") or "")
    except Exception as e:
        logger.debug("[App] world skip: %s", e)
    try:
        import status_panel
        out["present"] = list(status_panel._present_names(channel_id) or [])
    except Exception:
        out["present"] = []
    return out


def _history(channel_id: str) -> List[Dict[str, Any]]:
    """lk.history 재료 — 최근 50행(오래된 → 최신). role = char(모델) | user, name = 가면."""
    rows = None
    try:
        import sqlite_store
        rows = sqlite_store.read_history_tail(channel_id, HISTORY_MAX)
    except Exception:
        rows = None
    if rows is None:
        try:
            import domain_manager
            rows = list(domain_manager.get_history(channel_id) or [])[-HISTORY_MAX:]
        except Exception:
            rows = []
    out = []
    for r in rows or []:
        role = str((r or {}).get("role") or "")
        out.append({"role": "char" if role == "Model" else "user", "name": role,
                    "text": str((r or {}).get("content") or "")})
    return out


def _mask_of(channel_id: str, uid: str) -> str:
    try:
        import domain_manager
        p = domain_manager.get_participant_data(channel_id, str(uid)) or {}
        return str(p.get("mask") or "")
    except Exception:
        return ""


def _record_error(channel_id: str, app: Dict[str, Any], hook: str, err: str) -> None:
    logger.warning("[App] %s.%s 실패 ch=%s: %s", app.get("id"), hook, channel_id, err)
    try:
        import sqlite_store
        sqlite_store.set_app_fields(channel_id, app["id"], errors=int(app.get("errors") or 0) + 1,
                                    last_error=f"{hook}: {err}"[:300])
        app["errors"] = int(app.get("errors") or 0) + 1
        app["last_error"] = f"{hook}: {err}"[:300]
    except Exception:
        pass


class _Outcome:
    """작업 프로세스 응답 — 쓰기 모음 · 보고 모음 · 호출 뒤 장부(크기 셈용)."""

    def __init__(self, kv_before: Dict[str, Any], writes: Dict[str, Any], reports: List[str]):
        self.writes = dict(writes or {})
        self.reports = [str(r) for r in (reports or [])][:MAX_REPORTS]
        self.kv = dict(kv_before or {})
        for k, v in self.writes.items():
            if v is None:
                self.kv.pop(k, None)
            else:
                self.kv[k] = v


async def _run(channel_id: str, app: Dict[str, Any], hook: str, args: Tuple[Any, ...],
               who: str = "", extra_tags: Optional[List[Dict[str, Any]]] = None) -> Tuple[Any, _Outcome]:
    """훅 하나 → (돌려준 값, 결과). 실패는 LuaFail(오류는 앱 표에 적는다). 훅이 없으면 (None, 빈 결과).
    작업 프로세스에서 돈다(벽시계 CALL_TIMEOUT — 넘기면 그 프로세스를 죽인다)."""
    pkg = app.get("pkg") or {}
    import sqlite_store
    async with _lock(channel_id, app["id"]):
        kv = sqlite_store.app_kv_current(channel_id, app["id"])   # 잠금 안에서 읽는다(같은 앱 호출 겹침 0)
        hist = _history(channel_id) if "lk.history" in (pkg.get("main") or "") else []
        world = _world(channel_id, who)
        tags = (sqlite_store.app_tags_recent(channel_id, app["id"], int(world.get("turn") or 0) - HISTORY_MAX)
                + list(extra_tags or []) if "lk.tags" in (pkg.get("main") or "") else [])   # extra = 아직 안 적은 이번 정산 몫
        req = {"op": "call", "source": pkg.get("main") or "", "hook": hook, "args": list(args),
               "app": {"id": app["id"], "settings": app.get("settings") or {}}, "kv": kv,
               "world": world, "history": hist, "tags": tags}
        try:
            resp = await lua_runtime.POOL.run(req, timeout=lua_runtime.CALL_TIMEOUT)
            if not resp.get("ok"):
                raise LuaFail(str(resp.get("error") or "?"))
        except LuaFail as e:
            _record_error(channel_id, app, hook, str(e))
            raise
    for line in resp.get("logs") or []:
        logger.info("[App:%s] %s", app.get("id"), str(line)[:300])
    out = _Outcome(kv, _clean_writes(resp.get("writes")), _clean_reports(resp.get("reports")))
    return (resp.get("result") if resp.get("ran") else None), out


def _clean_writes(w: Any) -> Dict[str, Any]:
    """[10-24 재검토] 작업 프로세스 응답을 믿지 않는다 — 쓰기 키 · 값을 lk.set 과 같은 규칙으로 다시 거른다
    (예약 `_` 키 · 길이 · JSON 꼴 · NaN). 규약이 깨져 가짜 응답이 와도 장부엔 규칙 밖 값이 못 들어간다."""
    out: Dict[str, Any] = {}
    if not isinstance(w, dict):
        return out
    for k, v in w.items():
        if not isinstance(k, str) or not k or len(k) > MAX_KEY_CHARS or k.startswith("_"):
            continue
        try:
            json.dumps(v, ensure_ascii=False, allow_nan=False)
        except Exception:
            continue
        out[k] = v
    return out


def _clean_reports(r: Any) -> List[str]:
    if not isinstance(r, list):
        return []
    return [str(x)[:MAX_REPORT_CHARS] for x in r if isinstance(x, str) and x.strip()][:MAX_REPORTS]


def _commit(channel_id: str, app: Dict[str, Any], sess: Any, source: str, turn: int,
            reports_only: bool = False) -> Dict[str, Any]:
    """쓰기 모음 커밋 → 실제로 적은 {키: 값}(보고는 `_reports` 키 통째). 장부 합이 상한을 넘으면 하나도 안 적는다."""
    writes = {} if reports_only else dict(sess.writes)
    if sess.reports:
        import sqlite_store
        cur = sqlite_store.app_kv_current(channel_id, app["id"]).get(REPORT_KEY)
        reps = (list(cur) if isinstance(cur, list) else []) + list(sess.reports)
        writes[REPORT_KEY] = reps[-MAX_REPORTS:]
    if not writes:
        return {}
    merged = dict(sess.kv)
    merged.update({k: v for k, v in writes.items() if v is not None})
    try:
        size = len(json.dumps(merged, ensure_ascii=False, allow_nan=False).encode("utf-8"))
    except ValueError:
        _record_error(channel_id, app, "commit", "NaN · 무한대 값이라 이번 쓰기를 버림")
        return {}
    if size > MAX_KV_BYTES:
        _record_error(channel_id, app, "commit", f"장부가 {MAX_KV_BYTES // 1024}KB 를 넘어 이번 쓰기를 버림({size // 1024}KB)")
        return {}
    import sqlite_store
    sqlite_store.append_app_kv(channel_id, app["id"], writes, turn, source)
    return writes


def apply_journal(channel_id: str, app_id: str, writes: Dict[str, Any], turn: int,
                  reports: Optional[List[Any]] = None, token: str = "") -> bool:
    """`!다시` 재적용 — 편집 일지(app.kv)의 쓴 값을 그대로 다시 적는다. Lua 는 안 돈다(굴림이 바뀌면 안 된다).
    보고는 그 누르기 몫만, 제 열쇠(`_reports@<표>`)에 절대값으로 — 연속 `!다시`에도 한 번만 실린다.
    [10-24 검토] 옛 일지(M7a)의 `_reports` 통째(합친 보고)는 버린다 — 되감긴 턴의 훅 보고를 되살리던 자리."""
    w = {k: v for k, v in (writes or {}).items() if isinstance(k, str) and not k.startswith("_")} \
        if isinstance(writes, dict) else {}
    reps = [str(r)[:MAX_REPORT_CHARS] for r in (reports or []) if isinstance(r, str) and r.strip()][:MAX_REPORTS]
    if reps and token:
        w[REPLAY_REPORT_PREFIX + str(token)[:32]] = reps
    if not w:
        return False
    import sqlite_store
    if get_app(channel_id, app_id) is None:
        return False
    return sqlite_store.append_app_kv(channel_id, app_id, w, turn, "click") > 0


# =====================================================================
# 턴 — 앞(on_input · 보고 · feed) · 규칙 · 산문 뒤(on_output)
# =====================================================================

def _on_apps(channel_id: str) -> List[Dict[str, Any]]:
    if not available():
        return []
    return [a for a in list_apps(channel_id) if a["on"] and a.get("pkg")]


def compact(channel_id: str) -> int:
    """턴 시작 — 스냅숏 워터마크를 찍기 **앞**에 장부를 접는다(키마다 마지막 행)."""
    try:
        if not has_apps(channel_id):
            return 0
        import sqlite_store
        n = sqlite_store.compact_app_kv(channel_id)
        # [M7b] 오래된 신고 덜기 — 워터마크 앞이라 `!다시` 트림과 안 겹친다
        n += sqlite_store.prune_app_tags(channel_id, _turn(channel_id) - TAGS_KEEP_TURNS)
        return n
    except Exception as e:
        logger.debug("[App] compact skip: %s", e)
        return 0


async def turn_start(channel_id: str, ctx: Any) -> None:
    """턴 앞(스냅숏 **뒤** · Theoria 앞) — 켜진 앱마다 on_input → 쌓인 보고 꺼냄 → feed(). 결과 = ctx.app_feed_block.
    여기서 쓴 행은 이 턴 소속이라 `!다시` 트림이 되감고, `!다시` 재실행이 이 함수를 다시 부른다."""
    ctx.app_feed_block = ""
    apps = _on_apps(channel_id)
    if not apps:
        return
    who = str(getattr(ctx, "user_mask", "") or "")
    text = str(getattr(ctx, "action_text", "") or "")
    turn = _turn(channel_id)
    blocks: List[str] = []
    for a in apps:
        hooks = a.get("hooks") or []
        if "on_input" in hooks:
            try:
                _, sess = await _run(channel_id, a, "on_input", (text, who), who)
                _commit(channel_id, a, sess, "turn", turn)
            except LuaFail:
                pass
        reports = take_reports(channel_id, a, turn)
        feed = ""
        if "feed" in hooks and int((a.get("manifest") or {}).get("feed_max", FEED_DEFAULT) or 0) > 0:
            try:
                res, _ = await _run(channel_id, a, "feed", (), who)   # feed 는 읽기 — 쓰기는 버린다
                if isinstance(res, str):
                    feed = res.strip()[: int(a["manifest"].get("feed_max", FEED_DEFAULT))]
            except LuaFail:
                feed = ""
        parts = []
        if feed:
            parts.append(APP_FEED_LABEL.format(name=a["name"]) + "\n" + feed)
        if reports:
            parts.append(APP_REPORT_LABEL.format(name=a["name"]) + "\n" + APP_REPORT_NOTE + "\n"
                         + "\n".join(f"- {r}" for r in reports))
        if parts:
            blocks.append("\n".join(parts))
    ctx.app_feed_block = "\n\n".join(blocks)
    if blocks:
        logger.info("[App] 급식 ch=%s 앱 %d · %d자", channel_id, len(blocks), len(ctx.app_feed_block))


def take_reports(channel_id: str, app: Dict[str, Any], turn: int) -> List[str]:
    """쌓인 보고를 꺼내고 비운다(비우는 행 = 이 턴 소속 → `!다시`가 되살려 재실행이 다시 꺼낸다).
    `_reports`(훅 · 누르기) 다음에 `_reports@<표>`(`!다시`가 다시 얹은 누르기 보고)."""
    import sqlite_store
    kv = sqlite_store.app_kv_current(channel_id, app["id"])
    keys = [REPORT_KEY] + sorted(k for k in kv if k.startswith(REPLAY_REPORT_PREFIX))
    reps: List[str] = []
    clear: Dict[str, Any] = {}
    for k in keys:
        cur = kv.get(k)
        if k in kv:
            clear[k] = None
        if isinstance(cur, list):
            reps += [str(r) for r in cur if str(r).strip()]
    if clear:
        sqlite_store.append_app_kv(channel_id, app["id"], clear, turn, "turn")
    return reps[:MAX_REPORTS * 2]


def rules_block(channel_id: str) -> str:
    """Slot 23(규칙 존) 몫 — 켜진 앱의 rules.md. 올릴 때 정해지는 고정 글이라 매 턴 같다(캐시)."""
    out = []
    for a in _on_apps(channel_id):
        r = ((a.get("pkg") or {}).get("rules") or "").strip()
        if r:
            out.append(RULES_HEAD.format(name=a["name"]) + "\n" + r)
    return "\n\n".join(out)


async def after_prose_station(rt: Any, turn: Any) -> None:
    """AFTER_PROSE 정거장(C73 — 이 정거장을 처음 부른다) — 켜진 앱마다 on_output(산문, {turn, message_id}),
    그다음 [M7b] 신고 정산을 제 차선에 띄운다(tags 단 앱이 켜져 있을 때만 — 없으면 콜 0)."""
    channel_id = str(getattr(turn, "channel_id", "") or "")
    on = _on_apps(channel_id)
    if not on:
        return
    sent = list(getattr(turn, "sent_msgs", None) or [])
    mid = str(getattr(sent[0], "id", "") or "") if sent else ""
    t = _turn(channel_id)
    ctx = getattr(turn, "ctx", None)
    who = str(getattr(ctx, "user_mask", "") or "")
    prose = str(getattr(turn, "response", "") or "")
    for a in on:
        if "on_output" not in (a.get("hooks") or []):
            continue
        try:
            _, sess = await _run(channel_id, a, "on_output", (prose, {"turn": t, "message_id": mid}), who)
            _commit(channel_id, a, sess, "turn", t)
        except LuaFail:
            continue
    start_settlement(getattr(rt, "client", None), channel_id, str(getattr(ctx, "action_text", "") or ""),
                     prose, who, t, mid)


# =====================================================================
# 신고 (M7b) — 산문 뒤 제 차선의 AppTags 콜 → 거름 → app_tags 장부 → on_tags · 다음 턴 ⏳ 정산
# =====================================================================

# 신고 콜 머리(영어 — 분석 콜 관행). 근거 없는 신고 0(B-Outputs evidence 결) · 일어난 일만 · 빈 목록이 정상 답.
APP_TAGS_FRAME = (
    "## [APP TAGS — REPORT WHAT THIS TURN SHOWS]\n"
    "Code apps attached to this game keep their own ledgers. Each app below lists the events it wants reported "
    "(its tags). Read the player's input and this turn's prose, then report each listed event that this turn "
    "shows happening.\n"
    "- Report what happens in this turn: the prose, and the player's action as the prose carries it out. Plans, "
    "threats, memories, guesses and what might happen later are not events.\n"
    "- One entry per occurrence. The same event twice in the turn is two entries; an earlier event the prose only "
    "mentions again is not new.\n"
    "- An app with nothing to report this turn gets an empty list.\n"
    "- Use only the tags listed for that app, spelled exactly as listed.\n"
    "- fields: fill the fields the tag lists. number = a plain number, no units. text = a short Korean phrase, as "
    "the prose names it. Leave a field out when the turn does not give it.\n"
    "- evidence: copy the fragment of this turn's text that shows the event. Quote, do not paraphrase. No fragment "
    "means no entry.\n"
    "\nOutput JSON only, one key per app id listed below:\n"
    "{\"<app id>\": [{\"tag\": \"<tag name>\", \"fields\": {\"<field>\": <value>}, \"evidence\": \"<quote>\"}]}"
)

_SETTLE: Dict[str, Dict[str, Any]] = {}   # 채널 → {token, turn, apps, started, task} — 지금 도는(또는 막 끝난) 정산
_MISS: Dict[str, Dict[str, Any]] = {}     # 채널 → {turn, why, apps} — 지난 턴 신고를 못 받은 이유(모듈 앱 카드 한 줄)
_SEQ = [0]
_NUM_RE = re.compile(r"[+-]?\d{1,15}(?:\.\d{1,6})?")
_THOUSANDS_RE = re.compile(r"[+-]?\d{1,3}(?:,\d{3})+(?:\.\d+)?")
NUM_LIMIT = 2 ** 53          # 수 칸 절대값 상한 — Lua 정수 · JSON 이 안전하게 받는 자리(넘으면 on_tags · lk.tags 가 깨진다)
_EV_SPLIT = re.compile(r"…|\.\.\.")
_EV_STRIP = re.compile(r"[\s\"'“”‘’「」『』.,!?~·…\-—–:;()\[\]]+")


def tag_spec(app: Dict[str, Any]) -> List[Dict[str, Any]]:
    """앱의 신고 명세 — 묶음 manifest(읽을 때마다 _check_tags 를 지난 것)에서. 없으면 []."""
    return list((((app.get("pkg") or {}).get("manifest") or {}).get("tags")) or [])


def _tag_apps(channel_id: str) -> List[Dict[str, Any]]:
    return [a for a in _on_apps(channel_id) if tag_spec(a)]


def settling(channel_id: str, app_id: Optional[str] = None) -> bool:
    """정산이 도는 중인가 — app_id 를 주면 그 앱이 이번 정산에 끼어 있을 때만(Activity app.click 409)."""
    st = _SETTLE.get(str(channel_id))
    t = st.get("task") if st else None
    if t is None or t.done():
        return False
    return app_id is None or str(app_id) in st.get("apps", [])


def last_miss(channel_id: str, app_id: Optional[str] = None) -> str:
    """지난 턴 신고를 못 받은 이유("" = 받았거나 신고 앱이 아님)."""
    m = _MISS.get(str(channel_id))
    if not m or (app_id is not None and str(app_id) not in m.get("apps", [])):
        return ""
    if int(m.get("turn") or 0) < _turn(str(channel_id)):
        return ""            # 그 뒤로 턴이 지났다(정산 없는 `!턴` · 앱을 끈 채 등) — "지난 턴" 줄이 아니다
    return str(m.get("why") or "")


def build_tags_prompt(apps: List[Dict[str, Any]], user_input: str, prose: str, pc: str = "") -> Tuple[str, str]:
    """(지시 + 앱 명세, 이번 턴 재료). 앱 명세의 이름 · say 는 앱이 쓴 글이라 줄바꿈 없이 한 줄씩."""
    lines = [APP_TAGS_FRAME, "", "## [APPS]"]
    for a in apps:
        lines.append(f"App `{a['id']}` ({' '.join(str(a.get('name') or '').split())}):")
        for t in tag_spec(a):
            fl = ", ".join(f"{k} ({v})" for k, v in (t.get("fields") or {}).items())
            lines.append(f"- {t['name']} — {t['say']}" + (f" | fields: {fl}" if fl else " | no fields"))
    usr = []
    if str(pc or "").strip():
        usr.append(f"Player character: {str(pc).strip()}")
    usr.append("Player input:\n" + (str(user_input or "").strip()[:TAGS_INPUT_CAP] or "(none)"))
    usr.append("This turn's prose:\n" + str(prose or "").strip()[:TAGS_PROSE_CAP])
    usr.append("Output the app-tags JSON.")
    return "\n".join(lines), "\n\n".join(usr)


def _num(v: Any) -> Optional[Any]:
    """수 칸 값 → 유한한 수(|x| ≤ 2^53) | None. 글은 숫자 꼴만(천 단위 쉼표 `1,500` 은 받고 `1,5` 는 안 받는다)."""
    if isinstance(v, bool) or v is None:
        return None
    if isinstance(v, (int, float)):
        f = float(v) if not isinstance(v, int) or abs(v) <= NUM_LIMIT else None
    else:
        s = str(v).strip()
        if _THOUSANDS_RE.fullmatch(s):
            s = s.replace(",", "")
        if not _NUM_RE.fullmatch(s):
            return None
        f = float(s)
    if f is None or f != f or f in (float("inf"), float("-inf")) or abs(f) > NUM_LIMIT:
        return None
    return int(f) if f.is_integer() else f


def _norm_ev(t: str) -> str:
    return _EV_STRIP.sub("", str(t or ""))


def _evidence_in(ev: str, source_norm: str) -> bool:
    """근거가 이번 턴 글(입력 + 산문)에 정말 있나 — 공백 · 따옴표 · 구두점을 걷고 대조. 말줄임(… · ...)으로 끊은
    인용은 조각마다(2자 넘는 것) 다 있어야 한다. 원문을 고쳐 쓴 근거 · "없음" 같은 채움말은 여기서 떨어진다."""
    pieces = [_norm_ev(x) for x in _EV_SPLIT.split(ev)]
    pieces = [x for x in pieces if len(x) >= 2]
    return bool(pieces) and all(x in source_norm for x in pieces)


def _clean_tag_rows(v: Any, spec: List[Dict[str, Any]], source_norm: Optional[str] = None) -> List[Dict[str, Any]]:
    if isinstance(v, dict):
        v = [v]
    if not isinstance(v, list):
        return []
    exact = {t["name"]: t for t in spec}
    loose = {t["name"].lower(): t for t in spec}
    rows: List[Dict[str, Any]] = []
    for it in v:
        if len(rows) >= MAX_TAG_ROWS:
            break
        if not isinstance(it, dict):
            continue
        nm = str(it.get("tag") or it.get("name") or "").strip().strip("`")
        t = exact.get(nm) or loose.get(nm.lower())
        if t is None:
            continue                                   # 명세에 없는 태그 — 모델이 태그를 발명하지 못한다
        ev = " ".join(str(it.get("evidence") or "").split())[:MAX_TAG_TEXT]
        if len(ev) < 2:
            continue                                   # 근거 없는 신고는 버린다
        if source_norm is not None and not _evidence_in(ev, source_norm):
            logger.info("[AppTags] 근거가 이번 턴 글에 없음 → 버림: %s %r", t["name"], ev[:60])
            continue
        src_f = it.get("fields") if isinstance(it.get("fields"), dict) else {k: it[k] for k in it if k in t["fields"]}
        fields: Dict[str, Any] = {}
        for fk, ft in (t.get("fields") or {}).items():
            if fk not in src_f:
                continue
            val = src_f[fk]
            if ft == "number":
                n = _num(val)
                if n is not None:
                    fields[fk] = n
            elif val is not None and not isinstance(val, (dict, list)):
                sv = " ".join(str(val).split())[:MAX_TAG_TEXT]
                if sv:
                    fields[fk] = sv
        rows.append({"tag": t["name"], "fields": fields, "evidence": ev})
    return rows


def clean_tags_result(data: Any, apps: List[Dict[str, Any]],
                      source: Optional[str] = None) -> Optional[Dict[str, List[Dict[str, Any]]]]:
    """콜 답 → {app_id: [{tag, fields, evidence}]}(건이 있는 앱만). None = 답을 못 읽음(정산 실패).
    빈 객체 {} 는 '신고 없음'으로 받는다. 앱 id 대신 이름을 쓴 키 · 한 겹 더 싼 `apps` · 앱 칸을 단 평평한 목록은 접는다.
    source(이번 턴 입력 + 산문)를 주면 근거가 그 글에 정말 있는지도 본다(봇 길은 늘 준다)."""
    src_norm = _norm_ev(source) if source is not None else None
    if isinstance(data, dict) and isinstance(data.get("apps"), dict) and not any(a["id"] in data for a in apps):
        data = data["apps"]
    if isinstance(data, list):
        grouped: Dict[str, List[Any]] = {}
        for it in data:
            if isinstance(it, dict) and it.get("app"):
                grouped.setdefault(str(it["app"]), []).append(it)
        if not grouped and data:
            return None
        data = grouped
    if not isinstance(data, dict):
        return None
    by_id = {a["id"]: a for a in apps}
    by_name: Dict[str, Dict[str, Any]] = {}
    for a in apps:
        by_name.setdefault(" ".join(str(a.get("name") or "").split()), a)
    out: Dict[str, List[Dict[str, Any]]] = {}
    known = 0
    for k, v in data.items():
        kk = str(k).strip().strip("`")
        a = by_id.get(kk) or by_name.get(kk)           # id 가 먼저 — 한 앱의 이름이 다른 앱의 id 와 같아도 id 가 이긴다
        if a is None:
            continue
        known += 1
        rows = _clean_tag_rows(v, tag_spec(a), src_norm)
        if rows:
            out[a["id"]] = (out.get(a["id"], []) + rows)[:MAX_TAG_ROWS]
    if data and not known:
        return None
    return out


async def _tags_call(client: Any, sys_p: str, usr_p: str) -> Any:
    """AppTags 콜 하나(light) → 파싱한 JSON | None. 스모크는 이 자리를 갈아 끼운다(콜 0)."""
    from memory_system import api_call_with_retry
    from google.genai import types
    import bot_utils
    import text_resources
    cfg = types.GenerateContentConfig(
        system_instruction=text_resources.ANALYSIS_FRAME, temperature=0.1,
        max_output_tokens=TAGS_MAX_OUTPUT_TOKENS, response_mime_type="application/json",
        safety_settings=config.SAFETY_SETTINGS)
    contents = [types.Content(role="user", parts=[types.Part(text=sys_p)]),
                types.Content(role="user", parts=[types.Part(text=usr_p)])]
    with config.light_call():      # 코루틴 안에서 감싼다(contextvar)
        result = await api_call_with_retry(client, config.role_model("light"), contents, cfg,
                                           operation_name="AppTags")
    if not result:
        return None
    cleaned = bot_utils.clean_json_text(result)
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        return json.loads(bot_utils.repair_json(cleaned))


def _current(channel_id: str, token: int) -> bool:
    st = _SETTLE.get(channel_id)
    return bool(st) and st.get("token") == token


def _miss(channel_id: str, turn: int, why: str, apps: List[str]) -> None:
    _MISS[channel_id] = {"turn": int(turn), "why": why, "apps": list(apps)}
    logger.warning("[AppTags] 신고 못 받음 ch=%s turn=%s — %s", channel_id, turn, why)


def start_settlement(client: Any, channel_id: str, user_input: str, prose: str, pc: str, turn: int,
                     message_id: str = "") -> bool:
    """산문 뒤 — tags 단 앱이 켜져 있으면 정산 태스크를 제 차선(배경 큐 밖)에 띄운다. 없으면 콜 0 · False."""
    ch = str(channel_id)
    apps = _tag_apps(ch)
    if not apps or not str(prose or "").strip():
        return False
    old = _SETTLE.get(ch)
    if old and old.get("task") is not None and not old["task"].done():
        old["task"].cancel()      # 안 일어나야 정상(다음 턴이 앞 정산을 기다린다) — 겹치면 옛 것을 버린다
        logger.warning("[AppTags] 앞 정산이 아직 돌던 중 — 버림 ch=%s turn=%s", ch, old.get("turn"))
    _SEQ[0] += 1
    token = _SEQ[0]
    st: Dict[str, Any] = {"token": token, "turn": int(turn), "apps": [a["id"] for a in apps], "started": time.time()}
    _SETTLE[ch] = st
    st["task"] = asyncio.get_running_loop().create_task(
        _settle(client, ch, apps, user_input, prose, pc, int(turn), str(message_id or ""), token))
    return True


async def _settle(client: Any, channel_id: str, apps: List[Dict[str, Any]], user_input: str, prose: str,
                  pc: str, turn: int, message_id: str, token: int) -> None:
    """콜 → 거름 → 앱마다 on_tags(쓰기는 모아만 둔다) → **한 번에** 적기(await 없는 한 덩이: 지금 정산인지 ·
    앱이 아직 켜져 있는지 다시 보고 신고 행 + on_tags 쓰기). 그래서 중간에 끊겨도(다음 턴 상한 · `!다시` · 클리어)
    반쯤 적힌 정산이 없다 — 다 적히거나 하나도 안 적힌다. [10-24 검토]"""
    t0 = time.time()
    ids = [a["id"] for a in apps]
    try:
        sys_p, usr_p = build_tags_prompt(apps, user_input, prose, pc)
        try:
            raw = await asyncio.wait_for(_tags_call(client, sys_p, usr_p), timeout=SETTLE_LIMIT)
        except asyncio.TimeoutError:
            if _current(channel_id, token):
                _miss(channel_id, turn, "시간 초과", ids)
            return
        if not _current(channel_id, token):
            logger.info("[AppTags] 늦은 결과 버림 ch=%s turn=%s", channel_id, turn)
            return
        res = clean_tags_result(raw, apps, source=f"{user_input}\n{prose}")
        if res is None:
            _miss(channel_id, turn, "답을 못 읽음", ids)
            return
        staged: List[Tuple[Dict[str, Any], List[Dict[str, Any]], Any]] = []
        for a in apps:
            rows = res.get(a["id"]) or []
            if not rows:
                continue
            cur = get_app(channel_id, a["id"])
            if cur is None or not cur["on"] or not cur.get("pkg"):
                continue                                   # 정산 중에 꺼졌거나 지워졌다
            sess = None
            if "on_tags" in (cur.get("hooks") or []):
                items = [dict(r, turn=turn) for r in rows]
                try:
                    _, sess = await _run(channel_id, cur, "on_tags", (items,), pc, extra_tags=items)
                except LuaFail:
                    sess = None                            # 오류는 앱 표에 적혔다 — 신고 행은 그대로 적는다
            staged.append((cur, rows, sess))
        # ── 여기부터 await 없음 ──
        if not _current(channel_id, token):
            logger.info("[AppTags] 정산이 그사이 버려짐 — 안 적음 ch=%s turn=%s", channel_id, turn)
            return
        import sqlite_store
        total = ran = 0
        for cur, rows, sess in staged:
            now = get_app(channel_id, cur["id"])
            if now is None or not now["on"]:
                continue
            total += sqlite_store.append_app_tags(channel_id, cur["id"], turn, message_id, rows)
            if sess is not None:
                _commit(channel_id, now, sess, "tags", turn)
                ran += 1
        _MISS.pop(channel_id, None)
        logger.info("[AppTags] ch=%s turn=%s 앱 %d · 신고 %d건 · on_tags %d · %.1fs",
                    channel_id, turn, len(apps), total, ran, time.time() - t0)
    except asyncio.CancelledError:
        raise
    except Exception as e:
        logger.warning("[AppTags] 정산 실패 ch=%s turn=%s: %s", channel_id, turn, e)
        if _current(channel_id, token):
            _miss(channel_id, turn, "콜 실패", ids)


async def drop_settlement(channel_id: str, why: str = "") -> bool:
    """도는 정산을 버린다 — `!다시`(재실행이 다시 신고한다) · `!클리어` · `!리셋` · 다음 턴의 시간 초과.
    태스크가 멈출 때까지(≤5초) 기다린 뒤 돌아온다 → 돌아온 뒤엔 그 정산이 아무것도 안 적는다."""
    ch = str(channel_id)
    st = _SETTLE.pop(ch, None)
    t = st.get("task") if st else None
    if t is None or t.done():
        return False
    t.cancel()
    try:
        await asyncio.wait({t}, timeout=5.0)
    except Exception:
        pass
    if why:
        _miss(ch, st["turn"], why, st["apps"])
    logger.info("[AppTags] 정산 버림 ch=%s turn=%s (%s)", ch, st["turn"], why or "다시 · 클리어")
    return True


async def forget_settlement(channel_id: str) -> None:
    """`!클리어` · `!리셋` — 도는 정산을 버리고 '못 받음' 줄도 지운다."""
    await drop_settlement(channel_id)
    _MISS.pop(str(channel_id), None)


async def hold_for_settlement(channel_id: str, message: Any = None) -> float:
    """다음 턴 입구 — 앞 정산이 돌고 있으면 끝날 때까지 잡는다(PC 메시지에 ⏳, 끝나면 뗀다). 상한 60초(+on_tags 몫 · 5초).
    넘으면 그 정산을 버리고(그 턴 신고 없이) 간다. 돌려줌 = 기다린 초."""
    ch = str(channel_id)
    st = _SETTLE.get(ch)
    t = st.get("task") if st else None
    if t is None or t.done():
        if st is not None and _SETTLE.get(ch) is st:
            _SETTLE.pop(ch, None)
        return 0.0
    t0 = time.time()
    reacted = False
    if message is not None:
        try:
            await message.add_reaction(HOLD_EMOJI)
            reacted = True
        except Exception:
            pass
    try:
        # 상한 = 콜 상한 + 앱마다 on_tags 한 호출(CALL_TIMEOUT) + 5초 — 콜이 상한 안에 끝났는데 on_tags 도중에 끊지 않게
        margin = 5.0 + lua_runtime.CALL_TIMEOUT * len(st.get("apps") or [])
        left = max(1.0, SETTLE_LIMIT + margin - (time.time() - float(st.get("started") or t0)))
        await asyncio.wait({t}, timeout=left)
        if not t.done():
            await drop_settlement(ch, "시간 초과")
        elif _SETTLE.get(ch) is st:
            _SETTLE.pop(ch, None)
    finally:
        if reacted:
            try:
                me = (getattr(getattr(message, "guild", None), "me", None)
                      or getattr(getattr(message, "channel", None), "me", None))
                if me is not None:
                    await message.remove_reaction(HOLD_EMOJI, me)
            except Exception:
                pass
    waited = time.time() - t0
    logger.info("[AppTags] 다음 턴이 정산을 %.1f초 기다림 ch=%s", waited, ch)
    return waited


# =====================================================================
# 화면 · 누르기 (Activity)
# =====================================================================

def _clean_act(act: Any) -> Optional[Dict[str, Any]]:
    if not isinstance(act, dict) or not str(act.get("id") or ""):
        return None
    data = act.get("data")
    return {"id": str(act["id"])[:64], "data": data if isinstance(data, dict) else {}}


def _clean_parts(parts: Any, depth: int = 0, budget: Optional[List[int]] = None) -> List[Dict[str, Any]]:
    """앱이 낸 부품 — 아는 t 만 · 글은 글로 · act 는 {id, data} 만. 카드 안 카드는 화면이 평평하게 편다."""
    if budget is None:
        budget = [MAX_PARTS]
    out: List[Dict[str, Any]] = []
    if not isinstance(parts, list) or depth > 4:
        return out
    for p in parts:
        if budget[0] <= 0:
            break
        if not isinstance(p, dict) or p.get("t") not in _PART_TYPES:
            continue
        budget[0] -= 1
        t = p["t"]
        q: Dict[str, Any] = {"t": t}
        for k in ("title", "icon", "chip", "text", "label", "confirm", "style", "value"):
            if p.get(k) is not None and not isinstance(p.get(k), (dict, list)):
                q[k] = str(p[k])[:4000] if k == "text" else str(p[k])[:200]
        if t == "text" and p.get("muted"):
            q["muted"] = True
        if t == "switch":
            q["on"] = bool(p.get("on"))
        if t == "rows":
            q["rows"] = [[str(r[0])[:200], str(r[1])[:1000]] for r in (p.get("rows") or [])
                         if isinstance(r, list) and len(r) >= 2][:200]
        if t in ("select", "chips"):
            key = "options" if t == "select" else "items"
            q[key] = [[str(o[0])[:60], str(o[1])[:60]] for o in (p.get(key) or [])
                      if isinstance(o, list) and len(o) >= 2][:40]
        if t in ("switch", "select", "button", "chips"):
            act = _clean_act(p.get("act"))
            if act is None:
                continue
            q["act"] = act
        if t in ("card", "fold"):
            q["parts"] = _clean_parts(p.get("parts"), depth + 1, budget)
        out.append(q)
    return out


def _error_parts(text: str) -> List[Dict[str, Any]]:
    return [{"t": "card", "title": "앱 화면 오류", "icon": "alert", "parts": [{"t": "text", "text": text, "muted": True}]}]


async def view(channel_id: str, uid: str, app_id: str) -> Dict[str, Any]:
    """앱 화면 — {app, name, screen, parts | html, rev}. view 도 읽기라 쓰기는 버린다."""
    rev = _turn(channel_id)
    a = get_app(channel_id, app_id)
    if a is None:
        return {"app": app_id, "name": app_id, "screen": "parts", "rev": rev,
                "parts": [{"t": "empty", "text": "없는 앱이에요."}]}
    base = {"app": app_id, "name": a["name"], "rev": rev}
    if not a["on"]:
        return dict(base, screen="parts", parts=[{"t": "empty", "text": "꺼진 앱이에요. 모듈 앱에서 켤 수 있어요."}])
    if not a.get("pkg"):
        return dict(base, screen="parts", parts=_error_parts("묶음 파일을 못 읽어요. 다시 올려 주세요."))
    if a["screen"] == "none" or "view" not in (a.get("hooks") or []):
        return dict(base, screen="parts", parts=[{"t": "empty", "text": "화면이 없는 앱이에요."}])
    if not available():
        return dict(base, screen="parts", parts=_error_parts("코드 앱이 꺼져 있어요(서버에 lupa 없음)."))
    who = _mask_of(channel_id, uid)
    try:
        res, _ = await _run(channel_id, a, "view", (who,), who)
    except LuaFail as e:
        return dict(base, screen="parts", parts=_error_parts(str(e)))
    if a["screen"] == "html":
        html = res.get("html") if isinstance(res, dict) else (res if isinstance(res, str) else None)
        if not isinstance(html, str):
            return dict(base, screen="parts", parts=_error_parts("view 가 html 을 안 냈어요."))
        if len(html) > MAX_HTML_CHARS:
            return dict(base, screen="parts", parts=_error_parts("화면 HTML 이 너무 커요(2MB까지)."))
        return dict(base, screen="html", html=html)
    parts = res.get("parts") if isinstance(res, dict) else res
    return dict(base, screen="parts", parts=_clean_parts(parts) or [{"t": "empty", "text": "비어 있어요."}])


async def click(channel_id: str, uid: str, app_id: str, button_id: str, data: Any) -> Dict[str, Any]:
    """Activity `app.click` 몸 — on_click → 쓰기 커밋 → 영수증. ↶ 없음(굴림을 되돌리는 길이 된다).
    돌려줌 = activity_edit 결과 꼴 + journal(일지에 적을 것 = 쓴 값 그대로, `!다시` 재적용 몫)."""
    a = get_app(channel_id, app_id)
    if a is None:
        return {"ok": False, "line": "✗ 없는 앱이에요"}
    if not a["on"]:
        return {"ok": False, "line": "✗ 꺼진 앱이에요"}
    if "on_click" not in (a.get("hooks") or []) or not a.get("pkg"):
        return {"ok": False, "line": "✗ 이 앱은 누르기를 안 받아요"}
    if not available():
        return {"ok": False, "line": "✗ 코드 앱이 꺼져 있어요(서버에 lupa 없음)"}
    who = _mask_of(channel_id, uid)
    turn = _turn(channel_id)
    try:
        res, sess = await _run(channel_id, a, "on_click", (str(button_id or "")[:64], data if isinstance(data, dict) else {},
                                                           who), who)
    except LuaFail as e:
        return {"ok": False, "line": f"⚠ 앱 오류 — {str(e)[:160]}"}
    writes = _commit(channel_id, a, sess, "click", turn)
    if (sess.writes or sess.reports) and not writes:
        return {"ok": False, "line": "⚠ 앱 장부가 너무 커서 이번 쓰기를 버렸어요"}
    receipt = (res.strip() if isinstance(res, str) and res.strip() else f"{a['name']} · {button_id}")[:MAX_RECEIPT]
    out = {"ok": True, "line": receipt, "receipt": receipt, "undo": None}
    own = {k: v for k, v in writes.items() if k != REPORT_KEY}
    if own or sess.reports:
        # 일지 = 이 누르기가 쓴 값 + 이 누르기의 보고만(합친 `_reports` 는 안 적는다 — 되감긴 턴 몫이 섞여 있다)
        import uuid
        out["journal"] = {"action": "app.kv", "app": app_id, "writes": own,
                          "reports": list(sess.reports), "token": uuid.uuid4().hex[:12]}
    logger.info("[App] 누름 ch=%s %s.%s → %s (쓰기 %d)", channel_id, app_id, button_id, receipt, len(writes))
    return out


# =====================================================================
# 화면 등록 — 채널마다 다른 확장 앱 타일(activity_server.screens_on 이 붙인다)
# =====================================================================

def screens_for(channel_id: str) -> List[Any]:
    """켜진 앱 → Screen(tab app:<id>, kind extension, edit 없음). view 는 activity_server 가 비동기로 따로 부른다."""
    import module_frame as _mf
    out = []
    for a in _on_apps(channel_id):
        if a["screen"] == "none":
            continue
        out.append(_mf.Screen(TAB_PREFIX + a["id"], a["name"], a["icon"], 900 + int(a["no"] or 0),
                              _no_sync_view, renderer=("frame" if a["screen"] == "html" else "parts"),
                              drawer=True, kind="extension", edit=()))
    return out


def _no_sync_view(channel_id: str, uid: str) -> Dict[str, Any]:   # pragma: no cover - 동기 길은 안 쓴다
    return {"error": "앱 화면은 비동기로 받는다"}


def app_id_of_tab(tab: str) -> str:
    t = str(tab or "")
    return t[len(TAB_PREFIX):] if t.startswith(TAB_PREFIX) else ""
