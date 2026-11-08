# -*- coding: utf-8 -*-
"""[2026-10-23 C73 M7a] 코드 앱 호스트 — `.lkapp` 묶음을 채널에 올리고, 턴 · 화면에서 그 Lua 를 부른다.

스펙  파티쳇수정/output_artifact/C73_모듈틀_M7코드앱_지도·스펙_2026-10-23.md (§2 묶음 · §4 lk · §5 저장 · §6 급식 · §7 화면 · §8 쓰기)

번역 중심(10-23 레티어스 "번역이 중점") — 남의 파일을 알아먹는 호환층은 없다. 실행기(lua_runtime)는 Risu 이름을 모르고,
앱은 우리 `lk` 만 본다. Risu 이름이 필요하면 이식 가이드의 받침 조각을 앱이 제 안에 붙인다.

흐름
  올리기   `!모듈 올리기` + .lkapp → parse_package(검사 다섯) → 채널 폴더 apps/<id>.lkapp + 앱 표(sqlite apps) + 고정 번호
  턴 앞    compact(스냅숏 워터마크 앞) → turn_start: on_input → 쌓인 보고 꺼냄 → feed() → ctx.app_feed_block(Slot 29)
  규칙     rules.md → rules_block(Slot 23, `!룰` 글 뒤) — 올릴 때 정해지는 고정 글이라 캐시가 안 깨진다
  판정     [C116 0-3] 판정이 도는 턴(보통 · 묶음 `!턴`의 PC마다) — judge(j, who) → 수 · {value, label} → judgment_engine
           바깥 항(「앱 이름」 도장 · 같은 폭). 읽기 전용(쓰기 · 보고 버림) · 앱끼리 같이 띄움 · 실패한 앱만 빠짐
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
import hashlib
import json
import logging
import math
import os
import re
import time
import zipfile
from typing import Any, Dict, List, Optional, Tuple

import config
import lua_runtime
from lua_runtime import LuaFail

logger = logging.getLogger(__name__)

HOOKS = ("on_input", "feed", "on_output", "on_tags", "on_click", "view", "judge",   # [C116 0-3] judge = 판정 바깥 항(읽기)
         "tags_open", "tags_context", "on_turn_end", "on_day",   # [C116 판3] 신고 폭 · 매 턴 정산 · 날 경계
         "before_render",                                       # [C116 판4] 판정 뒤 · 렌더 앞 한 줄(읽기 전용)
         "on_answer",                                           # [C116 판7] lk.ask 의 답(누르기와 같은 턴 사이 쓰기)
         "panel")                                               # [C116 판8] 상태창 앱 칸(읽기 전용 — 산문 뒤 · 누르기 뒤 다시 셈)
# [C116 판8] 상태창 앱 칸 · 도착물 요청
PANEL_KEY = "_panel"        # 앱 장부 안 예약 키 — panel(who) 가 낸 줄(호스트가 적는다, 턴 몫 · 누르기 몫)
PANEL_ROWS_MAX = 8
PANEL_LABEL_MAX = 40
PANEL_VALUE_MAX = 200
HANDOUT_KEY = "_handouts"   # 다음 산문 앞에 실을 앱 도착물 줄(대기열)
REPLAY_HANDOUT_PREFIX = "_handouts@"
MAX_HANDOUTS_QUEUE = 10
# [C116 판7] 모델에게 묻기 — 버튼으로만(on_click) · 앱마다 하나씩 · 턴 사이 상한. 콜 = light · ANALYSIS_FRAME.
#   손잡이 넷은 config(APP_ASK_*)가 쥔다 — 여기는 그 값을 읽은 이름일 뿐.
ASK_PER_TURN = int(getattr(config, "APP_ASK_PER_TURN", 3))                  # 한 턴(turn_index) 사이 앱 하나가 물을 수 있는 수
ASK_CHANNEL_PER_TURN = int(getattr(config, "APP_ASK_CHANNEL_PER_TURN", 6))  # 채널 전체
ASK_TIMEOUT = float(getattr(config, "APP_ASK_TIMEOUT", 240.0))              # 콜 상한(초) — 넘기면 답 nil + 오류
ASK_LOCK_WAIT = float(getattr(config, "APP_ASK_LOCK_WAIT", 180.0))          # 답을 적으려고 턴 잠금을 기다리는 상한(초)
ASK_MAX_OUTPUT_TOKENS = 8192
ASK_RECEIPT = " · ✎ 묻는 중"
TAGS_CONTEXT_MAX = 400   # [C116 판3] tags_context 가 신고 콜에 싣는 글(앱마다)
JUDGE_LABEL_MAX = 20   # [C116 0-3] judge 라벨 글자 수
SCREENS = ("parts", "html", "none")
ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,31}$")
KEY_RE = re.compile(r"^[A-Za-z0-9_\-.:]{1,32}$")
ICON_RE = re.compile(r"^[a-z0-9-]{1,32}$")
MAX_CODE_BYTES = 5 * 1024 * 1024       # 묶음에서 그림 팩을 뺀 몫(옛 묶음 상한 그대로)
# [2026-11-06 C116 판8] 그림 팩 — `assets/<이름>.png|jpg|jpeg|gif|webp`. 화면(부품 `image` · HTML `lk-asset:이름`)이
#   서버에서 data: 주소로 갈아 끼운다(frame CSP `img-src data:` 그대로 — 바깥 요청 0 · 인증 없는 그림 주소 0).
ASSET_DIR = "assets/"
ASSET_MIME = {"png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg", "gif": "image/gif", "webp": "image/webp",
              # [2026-11-06 C117 C4] 소리 팩 — HTML 화면의 `lk-asset:` 로만(부품 image 는 그림만). frame CSP media-src data: 그대로.
              "mp3": "audio/mpeg", "ogg": "audio/ogg", "wav": "audio/wav"}
ASSET_NAME_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.\-]{0,63}$")
ASSET_REF_RE = re.compile(r"lk-asset:([A-Za-z0-9_][A-Za-z0-9_.\-]{0,63})")
MAX_ASSET_BYTES = 1024 * 1024
MAX_ASSETS_BYTES = 8 * 1024 * 1024
MAX_ASSETS = 100
VIEW_ASSET_BUDGET = 6 * 1024 * 1024    # 화면 한 번에 싣는 data: 글자 합
MAX_PACKAGE_BYTES = MAX_CODE_BYTES + MAX_ASSETS_BYTES + 512 * 1024   # zip 머리 · 압축 여유
MAX_MANIFEST_BYTES = 64 * 1024
MAX_MAIN_BYTES = 1024 * 1024
MAX_RULES_CHARS = 30000
FEED_DEFAULT = 2000
OUTPUTS_FILE = "outputs.json"          # [2026-11-06 C116 판1 1a] 선언 칸 — 라우터 항목 꼴 그대로(output_router.normalize_items)
MAX_OUTPUTS_BYTES = 512 * 1024
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
TAGS_OPEN_TEXT_MAX = 20000   # [C121] tags_open(text) 에 넘기는 이번 턴 입력 + 산문 상한
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
# [2026-11-06 C116 판4] before_render — 판정 뒤 · 렌더 앞 한 줄(이번 턴 문서 Slot 29 끝)
# [2026-11-06 C117 B3] 앱이 민 시간 — 앱 급식 블록 앞 한 줄(시계를 실제로 민 턴만). 사실 한 줄(렌더 재생 측정 없음).
APP_TIME_LABEL = "<앱 시간:{name}>"
APP_TIME_NOTE = ("This turn's action takes {span} by this app's count, and the clock already stands at its end: "
                 "the page carries the PC through that span.")


def time_note(name: str, minutes: int) -> str:
    m = max(0, int(minutes or 0))
    h, mm = divmod(m, 60)
    span = (f"{h} hour{'s' if h != 1 else ''}" + (f" {mm} minutes" if mm else "")) if h else f"{mm} minutes"
    return APP_TIME_LABEL.format(name=" ".join(str(name or "app").split())) + "\n" + APP_TIME_NOTE.format(span=span)


PRE_RENDER_LABEL = "<앱 이번 턴:{name}>"
PRE_RENDER_MAX = 300
PRE_RENDER_NOTE = ("This turn's reading from an app that saw the roll (or the turn) before the prose: it is how this turn "
                   "lands, not a new event — write it in.")

_PART_TYPES = ("card", "text", "rows", "empty", "switch", "select", "fold", "button", "chips",
               "edit", "add",   # [2026-11-05 C116 c3] 고치는 칸 · 새 항목 칸 — 값은 on_click(id, data) 의 data 로
               "image")         # [2026-11-06 C116 판8] 그림 팩 한 장(src = assets/이름 → 서버가 data: 로)
_INPUT_KINDS = ("number", "text", "area", "select")
_INPUTS_MAX = 6


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
    if man.get("weather") is not None:        # [2026-11-07 C121] 날씨 주인 — 켜져 있으면 코어 무작위 날씨를 끈다
        if not isinstance(man.get("weather"), bool):
            errs.append("manifest weather 는 true · false 예요")
        elif man["weather"]:
            m["weather"] = True
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
        return None, [f"묶음이 너무 커요({len(data) // 1024}KB — 코드 5MB + 그림 팩 8MB까지)"]
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

    if "manifest.json" not in names:
        return None, ["manifest.json 이(가) 없어요"]
    # [2026-11-06 C116 판1 1a] main.lua 는 선택 — 선언만 든 앱(outputs.json)은 Lua 없이 돈다. 둘 다 없으면 앱이 아니다.
    has_main = "main.lua" in names
    has_out = OUTPUTS_FILE in names
    if not has_main and not has_out:
        return None, ["main.lua 나 outputs.json 중 하나는 있어야 해요(코드 = main.lua · 선언 = outputs.json)"]
    try:
        man = json.loads(_read("manifest.json", MAX_MANIFEST_BYTES).decode("utf-8-sig"))
    except Exception as e:
        return None, [f"manifest.json 을 못 읽어요 — {e}"]
    if not isinstance(man, dict):
        return None, ["manifest.json 은 객체여야 해요"]
    m, errs = _check_manifest(man, strict)
    if not has_main:
        if str(man.get("screen") or "") in ("parts", "html"):
            errs.append("main.lua 가 없으면 화면을 못 그려요 — manifest screen 을 빼거나 none 으로")
        if m.get("tags"):
            errs.append("신고(tags)는 main.lua 의 on_tags 가 받아요 — main.lua 없이 tags 를 못 둬요")
        m["screen"] = "none"
    if errs:
        return None, errs
    main = ""
    if has_main:
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
    outputs = None
    if has_out:
        try:
            raw = json.loads(_read(OUTPUTS_FILE, MAX_OUTPUTS_BYTES).decode("utf-8-sig"))
        except Exception as e:
            return None, [f"outputs.json 을 못 읽어요 — {e}"]
        rows = raw.get("items") if isinstance(raw, dict) else raw
        if not isinstance(rows, list):
            return None, ['outputs.json 은 항목 목록이나 {"items": [...]} 이어야 해요']
        outputs = rows
    assets, a_errs = _read_assets(z, names, _read)   # [C116 판8] 그림 팩
    if a_errs and strict:
        return None, a_errs
    try:   # 그림 팩 밖 몫 = 옛 묶음 상한(압축 크기로 센다 — 그림은 이미 압축돼 있다)
        code_bytes = sum(z.getinfo(n).compress_size for n in names if not n.startswith(ASSET_DIR))
    except Exception:
        code_bytes = 0
    if code_bytes > MAX_CODE_BYTES and strict:
        return None, [f"그림 팩을 뺀 묶음이 너무 커요({code_bytes // 1024}KB — 5MB까지)"]
    return {"manifest": m, "main": main, "rules": rules, "outputs": outputs, "hooks": [], "assets": assets}, []


def _sniff_image(b: bytes) -> str:
    if b.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if b.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if b[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    if b[:4] == b"RIFF" and b[8:12] == b"WEBP":
        return "image/webp"
    if b[:4] == b"RIFF" and b[8:12] == b"WAVE":                    # [C117 C4] 소리
        return "audio/wav"
    if b[:4] == b"OggS":
        return "audio/ogg"
    if b[:3] == b"ID3" or (len(b) > 1 and b[0] == 0xFF and (b[1] & 0xE0) == 0xE0):
        return "audio/mpeg"
    return ""


def _read_assets(z: Any, names: set, _read: Any) -> Tuple[Dict[str, Dict[str, str]], List[str]]:
    """[C116 판8] assets/ 아래 그림 → {이름: {mime, b64}}. 이름 · 확장자 · 실제 꼴(앞 바이트) · 낱장 1MB · 합 8MB · 100장."""
    import base64
    out: Dict[str, Dict[str, str]] = {}
    errs: List[str] = []
    total = 0
    for n in sorted(x for x in names if x.startswith(ASSET_DIR) and not x.endswith("/")):
        nm = n[len(ASSET_DIR):]
        ext = nm.rsplit(".", 1)[-1].lower() if "." in nm else ""
        if "/" in nm or not ASSET_NAME_RE.match(nm) or ext not in ASSET_MIME:
            errs.append(f"그림 팩 `{n[:80]}` — 이름은 영문 · 숫자 · `_.-`(64자), 꼴은 png · jpg · gif · webp · mp3 · ogg · wav 만(하위 폴더 없음)")
            continue
        if len(out) >= MAX_ASSETS:
            errs.append(f"그림 팩은 {MAX_ASSETS}장까지")
            break
        try:
            b = _read(n, MAX_ASSET_BYTES)
        except Exception as e:
            errs.append(f"그림 팩 — {e}")
            continue
        mime = _sniff_image(b)
        if not mime or (mime != ASSET_MIME[ext] and not (ext in ("jpg", "jpeg") and mime == "image/jpeg")):
            errs.append(f"그림 팩 `{nm}` — 확장자({ext})와 실제 꼴이 달라요")
            continue
        total += len(b)
        if total > MAX_ASSETS_BYTES:
            errs.append(f"그림 팩 합이 너무 커요({MAX_ASSETS_BYTES // (1024 * 1024)}MB까지)")
            break
        out[nm] = {"mime": mime, "b64": base64.b64encode(b).decode("ascii"),
                   "h": hashlib.sha1(b).hexdigest()[:12]}   # [C117 C4] 내용 hash — 화면 캐시 열쇠
    return out, errs


def asset_uri(pkg: Optional[Dict[str, Any]], ref: Any, budget: Optional[List[int]] = None) -> str:
    """`assets/x.png` · `x.png` → data: 주소. 없거나 예산을 넘으면 ""."""
    nm = str(ref or "").strip()
    if nm.startswith("lk-asset:"):
        nm = nm[len("lk-asset:"):]
    if nm.startswith(ASSET_DIR):
        nm = nm[len(ASSET_DIR):]
    a = ((pkg or {}).get("assets") or {}).get(nm)
    if not a:
        return ""
    uri = f"data:{a['mime']};base64,{a['b64']}"
    if budget is not None:
        if len(uri) > budget[0]:
            return ""
        budget[0] -= len(uri)
    return uri


def fill_assets(html: str, pkg: Optional[Dict[str, Any]]) -> str:
    """HTML 안 `lk-asset:이름` → data: 주소(없는 이름 · 예산 넘침은 그대로 둔다 — frame CSP 가 막는다)."""
    if "lk-asset:" not in html or not (pkg or {}).get("assets"):
        return html
    budget = [VIEW_ASSET_BUDGET]
    cache: Dict[str, str] = {}

    def _sub(m: Any) -> str:
        nm = m.group(1)
        if nm not in cache:
            cache[nm] = asset_uri(pkg, nm, budget) or m.group(0)
        return cache[nm]
    return ASSET_REF_RE.sub(_sub, html)


def _asset_entry(pkg: Optional[Dict[str, Any]], ref: Any) -> Tuple[str, Optional[Dict[str, str]]]:
    nm = str(ref or "").strip()
    if nm.startswith("lk-asset:"):
        nm = nm[len("lk-asset:"):]
    if nm.startswith(ASSET_DIR):
        nm = nm[len(ASSET_DIR):]
    a = ((pkg or {}).get("assets") or {}).get(nm)
    if a and not a.get("h"):   # 옛 꼴(hash 없이 읽힌 묶음) — 그 자리에서 셈
        import base64
        a["h"] = hashlib.sha1(base64.b64decode(a.get("b64") or "")).hexdigest()[:12]
    return nm, a


def html_asset_refs(html: str, pkg: Optional[Dict[str, Any]]) -> Dict[str, str]:
    """[2026-11-06 C117 C4] HTML 안 `lk-asset:이름` → {이름: hash}(예산 안 · 그 앱 팩에 있는 것만). 화면이 따로 받아 갈아 낀다."""
    out: Dict[str, str] = {}
    if "lk-asset:" not in html or not (pkg or {}).get("assets"):
        return out
    budget = VIEW_ASSET_BUDGET
    for m in ASSET_REF_RE.finditer(html):
        nm, a = _asset_entry(pkg, m.group(1))
        if not a or nm in out:
            continue
        n = len(a.get("b64") or "") + 40
        if n > budget:
            continue
        budget -= n
        out[nm] = a["h"]
    return out


def asset_bytes(channel_id: str, app_id: str, name: str) -> Optional[Tuple[str, bytes, str]]:
    """[C117 C4] `/api/asset/<앱>/<이름>` 몸 — (mime, 바이트, hash) | None(없는 앱 · 꺼짐 · 없는 이름)."""
    a = get_app(channel_id, app_id)
    if a is None or not a.get("on") or not a.get("pkg"):
        return None
    nm, ent = _asset_entry(a.get("pkg"), name)
    if not ent:
        return None
    import base64
    try:
        return str(ent["mime"]), base64.b64decode(ent.get("b64") or ""), str(ent["h"])
    except Exception:
        return None


def parse_package(data: bytes, check: bool = True) -> Tuple[Optional[Dict[str, Any]], List[str]]:
    """**도구용**(lkapp_check · 스크린샷 · 스모크) — 읽기 + 이 프로세스 안에서 Lua 검사 · 훅 목록. 봇은 install(작업 프로세스)."""
    pkg, errs = read_package(data)
    if pkg is None:
        return None, errs
    if not pkg["main"]:          # [C116 판1 1a] 선언만 든 앱 — Lua 검사 · 훅 0
        return pkg, []
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
    # [C116 판1 1a] 선언 칸도 같이 — 끄면 치워 두고(값은 남음) 켜면 제자리로(C72 파일 모듈과 같은 길)
    try:
        import output_router as _or
        src = NUM_PREFIX + app_id
        if on:
            if _or.is_parked(channel_id, src):
                _or.unpark_source(channel_id, src)
        elif _or.names_by_source(channel_id).get(src):
            _or.park_source(channel_id, src)
    except Exception as e:
        logger.warning("[App] 선언 켜고 끄기 실패 %s: %s", app_id, e)
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
    pkg, errs = read_package(data)
    if pkg is None:
        return False, ["⚠️ 못 올렸어요 — " + e for e in errs]
    if pkg["main"]:             # [C116 판1 1a] Lua 가 든 앱만 lupa · 검사가 든다(선언만 든 앱은 관문 쪽에서 돈다)
        if not available():
            return False, ["⚠️ 코드 앱이 꺼져 있어요 — 서버에 lupa 가 없어요(`pip install lupa`)."]
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
    try:
        lines += apply_outputs(channel_id, app_id, pkg.get("outputs"), label_of(a))
    except Exception as e:
        logger.warning("[App] 선언 칸 적용 실패 %s: %s", app_id, e)
        lines.append("⚠️ 선언 칸(outputs.json)을 못 적었어요 — 로그를 보세요.")
    if not a.get("on"):
        lines.append(f"꺼져 있어요 — `!모듈 {label_of(a).split()[0]} on`")
    logger.info("[App] 올림 ch=%s id=%s v=%s hooks=%s", channel_id, app_id, man["version"], pkg["hooks"])
    return True, lines


def apply_outputs(channel_id: str, app_id: str, outputs: Any, label: str = "") -> List[str]:
    """[2026-11-06 C116 판1 1a] 선언 칸(outputs.json) → `!출력룰`과 같은 정규화 · 관문(output_router). 출처 = `app:<id>`.
    같은 id 로 다시 올리면 이름 기준 diff — 있는 이름은 갱신(값 보존), **빠진 이름은 선언 · 값째 지운다**(레티어스 11-06).
    꺼진 앱이면 켬 → 적용 → 다시 끔(치워 둔 칸 그대로). 되비침 줄을 돌려준다(선언 칸도 지운 것도 없으면 [])."""
    import output_router as _or
    src = NUM_PREFIX + app_id
    parked = _or.is_parked(channel_id, src)
    if parked:
        _or.unpark_source(channel_id, src)
    try:
        items = _or.normalize_items(outputs if isinstance(outputs, list) else [], src)
        new = [i.get("name") for i in items]
        gone = _or.remove_owned(channel_id, src, _or.missing_names(channel_id, src, new))
        results = _or.apply_items(channel_id, items, src) if items else []
    finally:
        if parked:
            _or.park_source(channel_id, src)
    if not results and not gone:
        return []
    lines: List[str] = []
    if results:
        lines.append(_or.format_reflection(
            label, results, head=f"📋 **선언 칸** ({OUTPUTS_FILE}, {len(results)}항목)",
            footer="고칠 땐 outputs.json 을 고쳐 다시 올려요 — 빠진 이름은 지워져요"))
    if gone:
        lines.append("🗑 이번 묶음에 없어 지움: " + " · ".join(gone))
    logger.info("[App] 선언 칸 ch=%s id=%s 항목 %d · 지움 %d", channel_id, app_id, len(results), len(gone))
    return lines


def remove(channel_id: str, app_id: str) -> Tuple[bool, str]:
    """`!모듈 지우기` — 묶음 · 앱 표 줄 · 장부 · [C116 판1 1a] 선언 칸(선언 · 값 · 치워 둔 것). 번호 줄은 남긴다(다시 올리면 제 번호)."""
    a = get_app(channel_id, app_id)
    if a is None:
        return False, "없는 앱이에요"
    gone_decl: List[str] = []
    try:
        import output_router as _or
        gone_decl = _or.remove_source(channel_id, NUM_PREFIX + app_id)
    except Exception as e:
        logger.warning("[App] 선언 지우기 실패 %s: %s", app_id, e)
    import sqlite_store
    sqlite_store.delete_app(channel_id, app_id)
    path = _pkg_path(channel_id, app_id)
    try:
        if os.path.exists(path):
            os.remove(path)
    except Exception as e:
        logger.warning("[App] 묶음 지우기 실패 %s: %s", path, e)
    _PKG_CACHE.pop(path, None)
    logger.info("[App] 지움 ch=%s id=%s 선언 %d", channel_id, app_id, len(gone_decl))
    return True, f"{label_of(a)} 지움 — 묶음 · 장부" + (f" · 선언 {len(gone_decl)}" if gone_decl else "")


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
        try:   # [C116 판3] 누적 날짜(식의 `날`) — 요일 · 주기는 앱이 이걸로 센다
            import custom_vars as _cv_w
            out["day_index"] = _cv_w.today_index(channel_id)
        except Exception:
            out["day_index"] = None
        out["season"] = str(w.get("season") or "")
    except Exception as e:
        logger.debug("[App] world skip: %s", e)
    try:
        import status_panel
        out["present"] = list(status_panel._present_names(channel_id) or [])
    except Exception:
        out["present"] = []
    out["judgment"] = _judgment_of(channel_id, out.get("turn"))   # [C116 판3] 그 턴 판정(없으면 nil)
    return out


def _judgment_of(channel_id: str, turn: Any) -> Optional[Dict[str, Any]]:
    """[C116 판3] lk.world().judgment — 턴 영수증(turn_snapshot)의 판정 칸에서 {result, total, type, action, dice, mod}.
    판정이 안 돈 턴 · 아직 영수증이 없는 때(on_input 은 지난 턴 번호를 본다)는 None."""
    try:
        import sqlite_store
        rows = sqlite_store.read_turn_snapshot_raw(channel_id, int(turn or 0))
        j = ((rows[0].get("raw") or {}).get("judgment") if rows else None) or {}
    except Exception:
        return None
    if not isinstance(j, dict) or not j.get("result"):
        return None
    return {"result": str(j.get("result")), "total": j.get("total"), "dice": j.get("dice_sum"), "mod": j.get("mod"),
            "type": str(j.get("type") or ""), "action": str(j.get("action") or "")}


def _roster_for(channel_id: str) -> Dict[str, List[str]]:
    """[C116 판3] lk.npc 재료 — 등록 인물 {이름: [별칭]}."""
    try:
        import npc_manager
        return {str(k): [str(a) for a in ((v or {}).get("aliases") or []) if a] if isinstance(v, dict) else []
                for k, v in (npc_manager.get_npcs(channel_id) or {}).items()}
    except Exception:
        return {}


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


# =====================================================================
# [2026-11-06 C116 판2 다리] 선언 값 — 읽기는 채널 전체, 쓰기는 이 앱 선언 칸(출처 app:<id>)만
# =====================================================================
# [C116 판6] 앱이 쓸 수 있는 코드 값 — 시스템 활력 · 평형(PC · 인물별) · 기본 소지품. 쓰기 문은 같은 apply_code_write.
CORE_VALUE_WRITABLE = ("활력", "평형", "소지품")


def _pc_masks(channel_id: str) -> Dict[str, str]:
    """참가자 {uid: 가면} — per_actor 값의 열쇠(uid)를 앱 쪽 이름(가면)으로 갈 때."""
    try:
        import domain_manager
        return {str(u): str(p.get("mask") or "") for u, p in (domain_manager.get_domain(channel_id).get("participants") or {}).items()
                if isinstance(p, dict) and p.get("mask")}
    except Exception:
        return {}


def _values_for(channel_id: str, app_id: str) -> Dict[str, Dict[str, Any]]:
    """lk.value 재료 — {이름: {type, scope, mine, core, per_actor, value, init, range, fields}}. 켜진(선언 층에 있는) 값만.
    [C116 판6] per_actor(활력 · 평형) 값은 uid 열쇠를 가면으로 바꿔 싣는다(인물 열쇠는 그대로)."""
    out: Dict[str, Dict[str, Any]] = {}
    try:
        import custom_vars as cv
        decl = cv.get_declarations(channel_id) or {}
        vals = cv.get_values(channel_id) or {}
    except Exception as e:
        logger.debug("[App] 값 사본 skip: %s", e)
        return out
    masks = None
    src = NUM_PREFIX + str(app_id)
    for nm, spec in decl.items():
        if not isinstance(spec, dict):
            continue
        ent = vals.get(nm) if isinstance(vals.get(nm), dict) else None
        if ent is None:
            try:
                ent = cv._initial_value(spec, None)
            except Exception:
                ent = {}
        rec = {"type": str(spec.get("type") or "gauge"), "scope": str(spec.get("scope") or "global"),
               "mine": str(spec.get("source") or "") == src and not cv.is_reserved(spec),
               "value": (ent or {}).get("value"), "init": spec.get("init")}
        if cv.is_reserved(spec) and str(nm) in CORE_VALUE_WRITABLE:
            rec["core"] = True
        if spec.get("per_actor"):
            masks = _pc_masks(channel_id) if masks is None else masks
            per = rec["value"] if isinstance(rec["value"], dict) else {}
            keyed = {k: v for k, v in per.items() if k not in masks}
            for uid, mk in masks.items():
                try:
                    keyed[mk] = cv._actor_base(channel_id, spec, ent or {}, uid)
                except Exception:
                    keyed[mk] = per.get(uid, spec.get("init"))
            rec["value"], rec["per_actor"] = keyed, True
        if isinstance(spec.get("range"), (list, tuple)) and len(spec["range"]) == 2:
            rec["range"] = list(spec["range"])
        if spec.get("fields"):
            rec["fields"] = True
        # [2026-11-07 C120] 인물별 값 — 명단(only · holders · max_npcs)과 인물별 시작값(start_by)
        if str(spec.get("scope")) == "npc" and not spec.get("per_actor"):
            try:
                if cv.roster_rules(spec):
                    rec["holders"] = cv.holder_names(channel_id, str(nm), spec)
                if spec.get("start_by"):
                    _who = rec.get("holders") if rec.get("holders") is not None else cv.allowed_npc_names(channel_id)
                    rec["init_by"] = {w: cv.npc_init(spec, w) for w in _who}
            except Exception as e:
                logger.debug("[App] 명단 사본 skip %s: %s", nm, e)
        out[str(nm)] = rec
    return out


def _apps_for(channel_id: str, app_id: str) -> Dict[str, Dict[str, Any]]:
    """lk.app_get 재료 — 다른 켜진 앱의 장부(`_` 키 뺌). 읽기만."""
    out: Dict[str, Dict[str, Any]] = {}
    try:
        import sqlite_store
        for a in list_apps(channel_id):
            if a["id"] == app_id or not a["on"]:
                continue
            kv = sqlite_store.app_kv_current(channel_id, a["id"]) or {}
            out[a["id"]] = {k: v for k, v in kv.items() if isinstance(k, str) and not k.startswith("_")}
    except Exception as e:
        logger.debug("[App] 앱 장부 사본 skip: %s", e)
    return out


def _clean_value_writes(w: Any, values: Dict[str, Dict[str, Any]]) -> List[Dict[str, Any]]:
    """작업 프로세스의 값 쓰기를 믿지 않는다 — 제 것(mine) · 상한 · 꼴을 다시 거른다."""
    out: List[Dict[str, Any]] = []
    if not isinstance(w, list):
        return out
    for r in w[:lua_runtime.MAX_VALUE_WRITES]:
        if not isinstance(r, dict) or r.get("op") not in ("set", "add"):
            continue
        nm = str(r.get("name") or "")
        if not ((values.get(nm) or {}).get("mine") or (values.get(nm) or {}).get("core")):
            continue
        x = r.get("x")
        if isinstance(x, bool) or not isinstance(x, (int, float, str)):
            continue
        if r["op"] == "add" and not isinstance(x, (int, float)):
            continue
        try:
            json.dumps(x, allow_nan=False)
        except Exception:
            continue
        out.append({"op": r["op"], "name": nm, "x": x,
                    **{k: str(r[k])[:64] for k in ("npc", "item", "field", "pc") if r.get(k) not in (None, "")}})
    return out


def _commit_values(channel_id: str, app: Dict[str, Any], writes: List[Dict[str, Any]], turn: int,
                   source: str) -> List[Dict[str, Any]]:
    """값 쓰기 → `custom_vars.apply_code_write`(식 do 와 같은 코드 소유 문 — 범위만 문다). 적힌 것 [{name, npc, item, field, actor, to}]."""
    if not writes:
        return []
    import custom_vars as cv
    done: List[Dict[str, Any]] = []
    ev = f"앱 {app.get('name') or app.get('id')}"
    decl = None
    for w in writes:
        kw = {"npc": w.get("npc", ""), "item": w.get("item", ""), "field": w.get("field", ""), "actor": w.get("pc", "")}
        if kw["actor"] or kw["npc"]:   # [C116 판6] per_actor 값 — 가면 → uid, 가면이 아니면 인물 열쇠
            decl = (cv.get_declarations(channel_id) or {}) if decl is None else decl
            if (decl.get(w["name"]) or {}).get("per_actor"):
                by_mask = {m: u for u, m in _pc_masks(channel_id).items()}
                key = kw["actor"] or kw["npc"]
                kw["actor"], kw["npc"] = (by_mask[key], "") if key in by_mask else ("", key)
        try:
            rec = cv.apply_code_write(channel_id, w["name"], delta=(w["x"] if w["op"] == "add" else None),
                                      value=(w["x"] if w["op"] == "set" else None), evidence=ev, turn=turn,
                                      source="app", **kw)
        except Exception as e:
            logger.warning("[App] 값 쓰기 실패 %s.%s: %s", app.get("id"), w.get("name"), e)
            continue
        if rec is None:
            continue
        done.append({"name": w["name"], **{k: v for k, v in kw.items() if v}, "to": rec.get("to"),
                     "op": w["op"], "x": w["x"]})   # [C117 A3] 더하기는 더한 양으로 다시 얹는다
    if done:
        logger.info("[App] 값 쓰기 ch=%s %s(%s) %d건", channel_id, app.get("id"), source, len(done))
    return done


# =====================================================================
# [2026-11-06 C116 판5] 시간 쓰기 — lk.time(분) · lk.time_until(시간대 | "HH:MM")
# =====================================================================
_HHMM_RE = re.compile(r"^(\d{1,2}):(\d{2})$")


def _clean_time_req(r: Any) -> Optional[Dict[str, Any]]:
    if not isinstance(r, dict):
        return None
    if "min" in r:
        try:
            n = int(r["min"])
        except Exception:
            return None
        return {"min": n} if 1 <= n <= 1440 else None
    t = str(r.get("until") or "").strip()[:10]
    return {"until": t} if t else None


def time_minutes(channel_id: str, req: Optional[Dict[str, Any]]) -> int:
    """시간 요청 → 지금부터 몇 분(1~1440, 못 풀면 0). until = 다음 그 시각(시간대면 그 시간대 첫 시) — 지금이 그 시각이면 하루 뒤."""
    if not req:
        return 0
    if "min" in req:
        return max(0, min(1440, int(req["min"])))
    t = str(req.get("until") or "").strip()
    hh = mm = None
    m = _HHMM_RE.match(t)
    if m:
        hh, mm = int(m.group(1)), int(m.group(2))
        if not (0 <= hh <= 23 and 0 <= mm <= 59):
            return 0
    else:
        import config
        rng = (getattr(config, "TIME_SLOT_HOURS", {}) or {}).get(t)
        if not rng:
            return 0
        hh, mm = int(rng[0]), 0
    try:
        import domain_manager
        w = domain_manager.get_world_state(channel_id) or {}
        now = int(w.get("hour", 0) or 0) * 60 + int(w.get("minute", 0) or 0)
    except Exception:
        return 0
    d = (hh * 60 + mm - now) % 1440
    return d or 1440


_CLOCK_KEYS = ("year", "month", "day", "hour", "minute", "time_slot", "weather", "weather_next")   # [C121] 다음 날 첫 날씨


def weather_owner(channel_id: str) -> str:
    """[2026-11-07 C121] 날씨 주인 앱 id — 켜진 앱 중 manifest `weather: true` 인 것, 번호가 빠른 것. 없으면 "".
    있으면 날이 바뀔 때 코어 무작위 날씨 대신 그 앱이 적어 둔 `weather_next`(game_world._day_change_weather)."""
    if not channel_id or not available():
        return ""
    try:
        import sqlite_store
        rows = [r for r in (sqlite_store.get_apps(channel_id) or [])
                if r.get("on") and (r.get("manifest") or {}).get("weather") is True]
        if not rows:
            return ""
        nums = _numbers(channel_id)
        rows.sort(key=lambda r: (int(nums.get(NUM_PREFIX + r["app_id"]) or 0) or 10 ** 9, str(r["app_id"])))
        return str(rows[0]["app_id"])
    except Exception as e:
        logger.debug("[App] 날씨 주인 찾기 skip: %s", e)
        return ""


def _clean_weather_req(r: Any) -> Optional[Dict[str, str]]:
    if not isinstance(r, dict):
        return None
    out = {}
    for k in ("now", "next"):
        v = r.get(k)
        if isinstance(v, str):
            v = " ".join(v.split())[:lua_runtime.WEATHER_NAME_MAX]
            if v:
                out[k] = v
    return out or None


def _commit_weather(channel_id: str, app: Dict[str, Any], sess: Any) -> Dict[str, str]:
    """[C121] lk.weather → world.weather(지금) · world.weather_next(다음 날 첫). 날씨 주인 앱만. 바뀐 칸 {키: 값}."""
    req = getattr(sess, "weather_req", None)
    if not req:
        return {}
    if weather_owner(channel_id) != app.get("id"):
        logger.info("[App] %s lk.weather 버림 — 날씨 주인 앱이 아님(manifest weather: true · 켜진 것 중 번호가 빠른 앱)",
                    app.get("id"))
        return {}
    import domain_manager
    w = domain_manager.get_world_state(channel_id) or {}
    done = {}
    for k_req, k_w in (("now", "weather"), ("next", "weather_next")):
        if req.get(k_req) and w.get(k_w) != req[k_req]:
            w[k_w] = req[k_req]
            done[k_w] = req[k_req]
    if done:
        domain_manager.update_world_state(channel_id, w)
    return done


# =====================================================================
# [2026-11-06 C116 판6] 코어 읽기(관계 · 장부 · 시계 · 인물) · 쓰기(관계 NPC→PC · 목표 걸음)
# =====================================================================
NPC_INFO_CHARS = 1200


PLACE_NEAR_MAX = 12


def _place_for(channel_id: str) -> Dict[str, Any]:
    """[2026-11-06 C117 C1] lk.place() 재료 — 지금 PC 자리의 나무 노드: 이름 · 종류(region/area/room) · 뿌리부터 경로 ·
    태그 · 위험 · 이웃(연결 + 형제, ≤12). 못 풀면 {name}."""
    import domain_manager
    try:
        loc = str(domain_manager.get_current_location(channel_id) or "")
    except Exception:
        loc = ""
    out: Dict[str, Any] = {"name": loc}
    if not loc:
        return out
    try:
        import world_tree
        node = world_tree.get_node(channel_id, loc)
        if not isinstance(node, dict):
            return out
        nm = str(node.get("name") or loc)
        props = node.get("properties") if isinstance(node.get("properties"), dict) else {}
        out = {"name": nm, "type": str(node.get("type") or ""),
               "path": [str(x) for x in (world_tree.get_ancestors(channel_id, nm) or [])],
               "tags": [str(t) for t in (props.get("tags") or []) if t][:12],
               "risk": str(props.get("risk") or "")}
        near = []
        for n in (world_tree.get_nearby_locations(channel_id, nm) or []):
            x = str((n or {}).get("name") or "") if isinstance(n, dict) else str(n or "")
            if x and x != nm and x not in near:
                near.append(x)
        out["near"] = near[:PLACE_NEAR_MAX]
    except Exception as e:
        logger.debug("[App] 장소 사본 skip: %s", e)
    return out


def _core_for(channel_id: str, src: str) -> Dict[str, Any]:
    """소스가 부르는 것만 싣는다(lk.relation* · lk.threads/lk.goal_step · lk.clocks · lk.npc_info)."""
    out: Dict[str, Any] = {}
    if "lk.relation" in src:
        try:
            import domain_manager
            out["relations"] = [{"s": str(e.get("source") or ""), "t": str(e.get("target") or ""),
                                 "bond": e.get("bond"), "tension": e.get("tension"), "stance": e.get("stance") or "",
                                 "kind": e.get("kind") or ""}
                                for e in (domain_manager.get_relation_edges(channel_id) or [])[:400]]
        except Exception as e:
            logger.debug("[App] 관계 사본 skip: %s", e)
    if "lk.threads" in src or "lk.goal_step" in src:
        try:
            import thread_ledger as tl
            out["threads"] = [{"id": tid, "kind": st.get("kind"), "title": st.get("title"), "status": st.get("status"),
                               "rank": st.get("rank"), "progress": st.get("progress"), "max": st.get("max"),
                               "parties": list(st.get("parties") or [])}
                              for tid, st in (tl.load_states(channel_id) or {}).items() if st.get("status") in tl.LIVE]
        except Exception as e:
            logger.debug("[App] 장부 사본 skip: %s", e)
    if "lk.clocks" in src:
        try:
            import domain_manager
            w = domain_manager.get_world_state(channel_id) or {}
            out["clocks"] = [{"name": c.get("name"), "filled": int(c.get("filled", c.get("progress", 0)) or 0),
                              "segments": int(c.get("segments", 4) or 4), "type": c.get("type") or c.get("clock_type") or "",
                              "linked_goal": c.get("linked_goal") or ""}
                             for c in (w.get("doom_clocks") or []) if isinstance(c, dict) and not c.get("resolved")]
        except Exception as e:
            logger.debug("[App] 시계 사본 skip: %s", e)
    if "lk.relation_shift" in src:   # [C117 C2] 말 → 폭(Theoria 와 같은 표)
        try:
            import config as _cfg_rw
            out["rel_words"] = {"bond": dict(getattr(_cfg_rw, "REL_BOND_SHIFT", {}) or {}),
                                "tension": dict(getattr(_cfg_rw, "REL_TENSION_SHIFT", {}) or {})}
        except Exception:
            pass
    if "lk.place" in src:            # [C117 C1] 장소 나무
        out["place"] = _place_for(channel_id)
    if "lk.npc_info" in src:
        try:
            import npc_manager, status_panel
            here = set(status_panel._present_names(channel_id) or [])
            npcs = {}
            for nm, d in (npc_manager.get_npcs(channel_id) or {}).items():
                if not isinstance(d, dict):
                    continue
                r = {"role": str(d.get("role") or ""), "place": str(d.get("location") or ""),
                     "status": str(d.get("status") or ""), "here": nm in here}
                if nm in here:   # 무대 위 인물만 공개 절(비밀 절 뺀 시트) — 사본 크기
                    r["sheet"] = str((npc_manager.get_npc_context_for_renderer(channel_id, nm) or {}).get("description")
                                     or "")[:NPC_INFO_CHARS]
                npcs[str(nm)] = r
            out["npcs"] = npcs
        except Exception as e:
            logger.debug("[App] 인물 사본 skip: %s", e)
    return out


def _clean_core_writes(rel: Any, goals: Any) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    rr, gg = [], []
    for r in (rel if isinstance(rel, list) else [])[:lua_runtime.MAX_CORE_WRITES]:
        try:
            b, t = int(r.get("bond") or 0), int(r.get("tension") or 0)
        except Exception:
            continue
        npc = str(r.get("npc") or "").strip()[:64]
        if npc and (b or t) and abs(b) <= lua_runtime.REL_SHIFT_MAX and abs(t) <= lua_runtime.REL_SHIFT_MAX:
            rr.append({"npc": npc, "bond": b, "tension": t})
    for g in (goals if isinstance(goals, list) else [])[:lua_runtime.MAX_CORE_WRITES]:
        try:
            n = int(g.get("steps"))
        except Exception:
            continue
        tid = str(g.get("id") or "").strip().upper()[:12]
        if tid and n and abs(n) <= 3:
            gg.append({"id": tid, "steps": n})
    return rr, gg


def _commit_core(channel_id: str, app: Dict[str, Any], sess: Any, turn: int) -> Dict[str, Any]:
    """관계(NPC→PC, origin app — 범위 클램프) · 목표 걸음(thread_ledger.code_events origin code:app). 적힌 것 {relations, goals}."""
    done: Dict[str, Any] = {}
    rel = list(getattr(sess, "rel_writes", None) or [])
    pc = str(getattr(sess, "who", "") or "")
    if rel and pc:
        import domain_manager
        try:
            pc = domain_manager.pc_canon(pc, domain_manager.pc_registry(channel_id)) or pc
        except Exception:
            pass
        out_r = []
        for r in rel:
            src_n = r["npc"]
            try:
                cur = next((e for e in domain_manager.get_relation_edges(channel_id, source=src_n)
                            if str(e.get("target") or "") == pc and not e.get("kind")), {})
                nb = max(-100, min(100, int(cur.get("bond") or 0) + r["bond"]))
                nt = max(0, min(100, int(cur.get("tension") or 0) + r["tension"]))
                e = domain_manager.upsert_relation_edge(channel_id, src_n, pc, bond=nb, tension=nt, turn=turn, origin="app")
            except Exception as ex:
                logger.debug("[App] 관계 쓰기 skip %s: %s", src_n, ex)
                continue
            if e:
                out_r.append({"npc": src_n, "pc": pc, "bond": e.get("bond", nb), "tension": e.get("tension", nt),
                              "db": r["bond"], "dt": r["tension"]})   # [C117 A3] 다시 얹기 = 이동량
        if out_r:
            done["relations"] = out_r
    goals = list(getattr(sess, "goal_writes", None) or [])
    if goals:
        try:
            import thread_ledger as tl
            st = tl.load_states(channel_id)
            goals = [g for g in goals if (st.get(g["id"]) or {}).get("kind") == "goal"
                     and (st.get(g["id"]) or {}).get("status") == "open"]
            n = tl.code_events(channel_id, turn, [{"op": "progress", "id": g["id"], "step": g["steps"]} for g in goals],
                               origin="code:app") if goals else 0
            if n:
                done["goals"] = goals
        except Exception as ex:
            logger.debug("[App] 목표 걸음 skip: %s", ex)
    if done:
        logger.info("[App] 코어 쓰기 ch=%s %s %s", channel_id, app.get("id"), {k: len(v) for k, v in done.items()})
    return done


def _clock_state(channel_id: str) -> Dict[str, Any]:
    import domain_manager
    w = domain_manager.get_world_state(channel_id) or {}
    return {k: w.get(k) for k in _CLOCK_KEYS if k in w}


class _Outcome:
    """작업 프로세스 응답 — 쓰기 모음 · 보고 모음 · 호출 뒤 장부(크기 셈용) · [C116 판2] 값 쓰기 모음."""

    def __init__(self, kv_before: Dict[str, Any], writes: Dict[str, Any], reports: List[str],
                 value_writes: Optional[List[Dict[str, Any]]] = None, time_req: Any = None,
                 rel_writes: Any = None, goal_writes: Any = None, who: str = ""):
        self.time_req = _clean_time_req(time_req)   # [C116 판5]
        self.weather_req: Optional[Dict[str, str]] = None   # [C121] lk.weather
        self.applied_weather: Dict[str, str] = {}
        self.rel_writes, self.goal_writes = _clean_core_writes(rel_writes, goal_writes)   # [C116 판6]
        self.who = str(who or "")
        self.applied_core: Dict[str, Any] = {}
        self.ask: Optional[Dict[str, Any]] = None   # [C116 판7] 누르기의 lk.ask(거른 것)
        self.handouts: List[Dict[str, Any]] = []     # [C116 판8] lk.handout(거른 것)
        self.writes = dict(writes or {})
        self.reports = [str(r) for r in (reports or [])][:MAX_REPORTS]
        self.value_writes = list(value_writes or [])
        self.applied_values: List[Dict[str, Any]] = []
        self.kv_adds: Dict[str, Any] = {}       # [C117 A3] lk.add 더한 양(쓰기에 든 키만)
        self.values_after: Dict[str, Any] = {}  # [C117 B2] 쓴 값 이름 → 쓴 뒤 값(다음 훅의 사본에만)
        self.kv = dict(kv_before or {})
        for k, v in self.writes.items():
            if v is None:
                self.kv.pop(k, None)
            else:
                self.kv[k] = v


async def _run(channel_id: str, app: Dict[str, Any], hook: str, args: Tuple[Any, ...],
               who: str = "", extra_tags: Optional[List[Dict[str, Any]]] = None,
               kv_over: Optional[Dict[str, Any]] = None, over: Any = None) -> Tuple[Any, _Outcome]:
    """훅 하나 → (돌려준 값, 결과). 실패는 LuaFail(오류는 앱 표에 적는다). 훅이 없으면 (None, 빈 결과).
    작업 프로세스에서 돈다(벽시계 CALL_TIMEOUT — 넘기면 그 프로세스를 죽인다)."""
    pkg = app.get("pkg") or {}
    import sqlite_store
    async with _lock(channel_id, app["id"]):
        kv = sqlite_store.app_kv_current(channel_id, app["id"])   # 잠금 안에서 읽는다(같은 앱 호출 겹침 0)
        if kv_over is not None:
            kv = dict(kv_over)   # [C116 판3] on_turn_end — 아직 안 적은 on_tags 쓰기 뒤 장부
        hist = _history(channel_id) if "lk.history" in (pkg.get("main") or "") else []
        world = _world(channel_id, who)
        tags = (sqlite_store.app_tags_recent(channel_id, app["id"], int(world.get("turn") or 0) - HISTORY_MAX)
                + list(extra_tags or []) if "lk.tags" in (pkg.get("main") or "") else [])   # extra = 아직 안 적은 이번 정산 몫
        main_src = pkg.get("main") or ""
        values = _values_for(channel_id, app["id"]) if "lk.value" in main_src else {}   # [C116 판2]
        core = _core_for(channel_id, main_src)
        if over is not None:   # [C117 B2] 같은 정산 앞 훅(on_tags)이 쓴 것 — 장부 · 값 · 관계 · 목표를 아직 안 적은 채로 비춘다
            kv = dict(getattr(over, "kv", None) or kv)
            _overlay(values, core, over)
        req = {"op": "call", "source": main_src, "hook": hook, "args": list(args),
               "app": {"id": app["id"], "settings": app.get("settings") or {}}, "kv": kv,
               "world": world, "history": hist, "tags": tags, "values": values,
               "apps": _apps_for(channel_id, app["id"]) if "lk.app_get" in main_src else {},
               "roster": _roster_for(channel_id) if "lk.npc(" in main_src else {},
               "core": core}
        try:
            resp = await lua_runtime.POOL.run(req, timeout=lua_runtime.CALL_TIMEOUT)
            if not resp.get("ok"):
                raise LuaFail(str(resp.get("error") or "?"))
        except LuaFail as e:
            _record_error(channel_id, app, hook, str(e))
            raise
    for line in resp.get("logs") or []:
        logger.info("[App:%s] %s", app.get("id"), str(line)[:300])
    out = _Outcome(kv, _clean_writes(resp.get("writes")), _clean_reports(resp.get("reports")),
                   _clean_value_writes(resp.get("value_writes"), values), resp.get("time"),
                   resp.get("rel_writes"), resp.get("goal_writes"), who)
    out.ask = _clean_ask(resp.get("ask"))   # [C116 판7]
    out.weather_req = _clean_weather_req(resp.get("weather"))   # [C121]
    out.handouts = _clean_handouts(resp.get("handouts"))   # [C116 판8]
    out.kv_adds = _clean_adds(resp.get("kv_adds"), out.writes)   # [C117 A3]
    out.values_after = _clean_values_after(resp.get("values_after"), out.value_writes)   # [C117 B2]
    if out.ask is not None and hook != "on_click":
        _record_error(channel_id, app, hook, "lk.ask 는 누르기(on_click)에서만 — 이번 물음은 버림")
        out.ask = None
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


def _clean_adds(a: Any, writes: Dict[str, Any]) -> Dict[str, Any]:
    """[C117 A3] lk.add 더한 양 — 쓰기에 든 키 · 수만(작업 프로세스 응답을 믿지 않는다)."""
    out: Dict[str, Any] = {}
    for k, n in (a.items() if isinstance(a, dict) else []):
        if isinstance(k, str) and k in writes and not isinstance(n, bool) and isinstance(n, (int, float)) \
                and n == n and n not in (float("inf"), float("-inf")):
            out[k] = n
    return out


def _clean_values_after(v: Any, vwrites: List[Dict[str, Any]]) -> Dict[str, Any]:
    """[C117 B2] 쓴 값 이름(거른 값 쓰기에 든 것)만 · JSON 꼴만. 다음 훅의 **읽기 사본**에만 쓴다(권한 칸은 호스트 것)."""
    names = {str(w.get("name")) for w in (vwrites or [])}
    out: Dict[str, Any] = {}
    for k, x in (v.items() if isinstance(v, dict) else []):
        if str(k) in names:
            try:
                json.dumps(x, ensure_ascii=False, allow_nan=False)
            except Exception:
                continue
            out[str(k)] = x
    return out


def _overlay(values: Dict[str, Dict[str, Any]], core: Dict[str, Any], over: Any) -> None:
    """[C117 B2] 앞 훅이 쓴 것을 새 사본에 비춘다(커밋은 정산 끝 한 덩이 그대로). 값 = 쓴 뒤 값 · 관계 = 이동 · 목표 = 걸음."""
    for nm, x in (getattr(over, "values_after", None) or {}).items():
        if nm in values:
            values[nm]["value"] = x
    pc = str(getattr(over, "who", "") or "")
    for r in (getattr(over, "rel_writes", None) or []):
        for e in (core.get("relations") or []):
            if e.get("s") == r.get("npc") and e.get("t") == pc and not e.get("kind"):
                e["bond"] = max(-100, min(100, int(e.get("bond") or 0) + int(r.get("bond") or 0)))
                e["tension"] = max(0, min(100, int(e.get("tension") or 0) + int(r.get("tension") or 0)))
    for g in (getattr(over, "goal_writes", None) or []):
        for t in (core.get("threads") or []):
            if t.get("id") == g.get("id"):
                try:
                    t["progress"] = max(0, min(int(t.get("max") or 0) or 99, int(t.get("progress") or 0) + int(g.get("steps") or 0)))
                except Exception:
                    pass


def _clean_reports(r: Any) -> List[str]:
    if not isinstance(r, list):
        return []
    return [str(x)[:MAX_REPORT_CHARS] for x in r if isinstance(x, str) and x.strip()][:MAX_REPORTS]


def _commit(channel_id: str, app: Dict[str, Any], sess: Any, source: str, turn: int,
            reports_only: bool = False) -> Dict[str, Any]:
    """쓰기 모음 커밋 → 실제로 적은 {키: 값}(보고는 `_reports` 키 통째). 장부 합이 상한을 넘으면 하나도 안 적는다.
    [C116 판2] 값 쓰기(sess.value_writes)는 장부 커밋이 선 뒤에 — 장부를 버리면 값도 버린다. 적힌 값 = sess.applied_values."""
    vwrites = [] if reports_only else list(getattr(sess, "value_writes", None) or [])
    writes = {} if reports_only else dict(sess.writes)
    if sess.reports:
        import sqlite_store
        cur = sqlite_store.app_kv_current(channel_id, app["id"]).get(REPORT_KEY)
        reps = (list(cur) if isinstance(cur, list) else []) + list(sess.reports)
        writes[REPORT_KEY] = reps[-MAX_REPORTS:]
    if getattr(sess, "handouts", None) and not reports_only:   # [C116 판8] 도착물 대기열 — 보고와 같은 꼴
        import sqlite_store
        cur = sqlite_store.app_kv_current(channel_id, app["id"]).get(HANDOUT_KEY)
        writes[HANDOUT_KEY] = ((list(cur) if isinstance(cur, list) else []) + list(sess.handouts))[-MAX_HANDOUTS_QUEUE:]
    if not writes:
        if vwrites:
            sess.applied_values = _commit_values(channel_id, app, vwrites, turn, source)
        if not reports_only:
            sess.applied_core = _commit_core(channel_id, app, sess, turn)   # [C116 판6]
            sess.applied_weather = _commit_weather(channel_id, app, sess)   # [C121]
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
    if vwrites:
        sess.applied_values = _commit_values(channel_id, app, vwrites, turn, source)
    if not reports_only:
        sess.applied_core = _commit_core(channel_id, app, sess, turn)   # [C116 판6]
        sess.applied_weather = _commit_weather(channel_id, app, sess)   # [C121]
    return writes


def apply_journal(channel_id: str, app_id: str, writes: Dict[str, Any], turn: int,
                  reports: Optional[List[Any]] = None, token: str = "",
                  values: Optional[List[Dict[str, Any]]] = None,
                  clock: Optional[Dict[str, Any]] = None, core: Optional[Dict[str, Any]] = None,
                  handouts: Optional[List[Any]] = None, adds: Optional[Dict[str, Any]] = None) -> bool:
    """`!다시` 재적용 — 편집 일지(app.kv)의 쓴 값을 그대로 다시 적는다. Lua 는 안 돈다(굴림이 바뀌면 안 된다).
    보고는 그 누르기 몫만, 제 열쇠(`_reports@<표>`)에 절대값으로 — 연속 `!다시`에도 한 번만 실린다.
    [10-24 검토] 옛 일지(M7a)의 `_reports` 통째(합친 보고)는 버린다 — 되감긴 턴의 훅 보고를 되살리던 자리."""
    w = {k: v for k, v in (writes or {}).items() if isinstance(k, str) and not k.startswith("_")} \
        if isinstance(writes, dict) else {}
    if isinstance(adds, dict) and adds:   # [C117 A3] lk.add 키 = 지금 장부(되감긴 턴 시작 + 앞 다시 얹기) + 더한 양
        import sqlite_store
        cur_kv = sqlite_store.app_kv_current(channel_id, app_id) or {}
        for k, n in adds.items():
            if k in w and not isinstance(n, bool) and isinstance(n, (int, float)):
                c0 = cur_kv.get(k, 0)
                if c0 is None:
                    c0 = 0
                if not isinstance(c0, bool) and isinstance(c0, (int, float)):
                    w[k] = c0 + n
    reps = [str(r)[:MAX_REPORT_CHARS] for r in (reports or []) if isinstance(r, str) and r.strip()][:MAX_REPORTS]
    if reps and token:
        w[REPLAY_REPORT_PREFIX + str(token)[:32]] = reps
    hos = _clean_handouts(handouts)          # [C116 판8]
    if hos and token:
        w[REPLAY_HANDOUT_PREFIX + str(token)[:32]] = hos
    vals = _replay_values(channel_id, app_id, values, turn)   # [C116 판2] 쓴 뒤 값 그대로
    if isinstance(clock, dict) and clock and get_app(channel_id, app_id) is not None:   # [C116 판5] 쓴 뒤 시계 그대로
        try:
            import domain_manager
            ws = domain_manager.get_world_state(channel_id) or {}   # (w = 장부 쓰기 — 이름을 가른다)
            ws.update({k: clock[k] for k in _CLOCK_KEYS if clock.get(k) is not None})
            domain_manager.update_world_state(channel_id, ws)
            vals += 1
        except Exception as e:
            logger.debug("[App] 시계 다시 얹기 skip: %s", e)
    if isinstance(core, dict) and core and get_app(channel_id, app_id) is not None:   # [C116 판6]
        vals += _replay_core(channel_id, core, turn)
    if not w:
        return vals > 0
    import sqlite_store
    if get_app(channel_id, app_id) is None:
        return False
    return sqlite_store.append_app_kv(channel_id, app_id, w, turn, "click") > 0 or vals > 0


def _replay_core(channel_id: str, core: Dict[str, Any], turn: int) -> int:
    """[C116 판6] 누르기의 관계(쓴 뒤 값 절대) · 목표 걸음 다시 얹기."""
    n = 0
    try:
        import domain_manager
        for r in (core.get("relations") or [])[:lua_runtime.MAX_CORE_WRITES]:
            if isinstance(r, dict) and r.get("npc") and r.get("pc"):
                nb, nt = int(r.get("bond") or 0), int(r.get("tension") or 0)
                if "db" in r or "dt" in r:   # [C117 A3] 이동량 — 되감긴 엣지(턴 시작) + 이동. 다시 돈 턴의 이동과 겹치지 않는다
                    cur = next((e for e in domain_manager.get_relation_edges(channel_id, source=str(r["npc"]))
                                if str(e.get("target") or "") == str(r["pc"]) and not e.get("kind")), {})
                    nb = max(-100, min(100, int(cur.get("bond") or 0) + int(r.get("db") or 0)))
                    nt = max(0, min(100, int(cur.get("tension") or 0) + int(r.get("dt") or 0)))
                if domain_manager.upsert_relation_edge(channel_id, str(r["npc"]), str(r["pc"]), bond=nb,
                                                       tension=nt, turn=turn, origin="app"):
                    n += 1
    except Exception as e:
        logger.debug("[App] 관계 다시 얹기 skip: %s", e)
    goals = [g for g in (core.get("goals") or []) if isinstance(g, dict) and g.get("id")]
    if goals:
        try:
            import thread_ledger as tl
            n += tl.code_events(channel_id, turn, [{"op": "progress", "id": g["id"], "step": int(g.get("steps") or 0)}
                                                   for g in goals], origin="code:app")
        except Exception as e:
            logger.debug("[App] 목표 다시 얹기 skip: %s", e)
    return n


def _replay_values(channel_id: str, app_id: str, values: Any, turn: int) -> int:
    """[C116 판2] 누르기의 값 쓰기 재적용 — 일지의 쓴 뒤 값(to)을 apply_code_write(value=to) 로. 제 선언 칸 값만."""
    if not isinstance(values, list) or not values:
        return 0
    a = get_app(channel_id, app_id)
    if a is None:
        return 0
    # [C117 A3′] 코어 값(활력 · 평형 · 소지품)도 — 판6 이 쓰기는 열고 다시 얹기는 제 선언 값(mine)만 봤다.
    own = {k for k, v in _values_for(channel_id, app_id).items() if v.get("mine") or v.get("core")}
    import custom_vars as cv
    n = 0
    for r in values[:lua_runtime.MAX_VALUE_WRITES]:
        if not isinstance(r, dict) or str(r.get("name") or "") not in own or r.get("to") is None:
            continue
        x = r.get("x")
        delta = x if (r.get("op") == "add" and not isinstance(x, bool) and isinstance(x, (int, float))) else None
        try:   # [C117 A3] 더하기 = 더한 양(되감긴 값 + x — 다시 돈 턴 몫과 겹치지 않는다) · 그 밖 = 쓴 뒤 값
            rec = cv.apply_code_write(channel_id, r["name"], value=(None if delta is not None else r["to"]), delta=delta,
                                      npc=str(r.get("npc") or ""),
                                      item=str(r.get("item") or ""), field=str(r.get("field") or ""),
                                      actor=str(r.get("actor") or ""), evidence=f"앱 {a.get('name') or app_id}(다시 얹음)",
                                      turn=turn, source="app")
        except Exception as e:
            logger.debug("[App] 값 다시 얹기 skip %s: %s", r.get("name"), e)
            continue
        n += 1 if rec is not None else 0
    return n


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


async def turn_start(channel_id: str, ctx: Any, inputs: Optional[List[Tuple[str, str]]] = None) -> None:
    """턴 앞(스냅숏 **뒤** · Theoria 앞) — 켜진 앱마다 on_input → 쌓인 보고 꺼냄 → feed(). 결과 = ctx.app_feed_block.
    여기서 쓴 행은 이 턴 소속이라 `!다시` 트림이 되감고, `!다시` 재실행이 이 함수를 다시 부른다.
    [2026-11-06 C117 B1] inputs = [(행동 글, 가면)] — 묶음 `!턴` 은 PC 마다 그 PC 행동으로 on_input, 관찰 턴은 [](on_input 안 돎).
    없으면 솔로 턴(ctx.action_text · ctx.user_mask). feed · 보고는 한 번(가면 = 첫 PC)."""
    ctx.app_feed_block = ""
    ctx.app_time_min = 0       # [C116 판5] 이번 턴 앱 시간 요청(분) — update_world_state 가 선언 꼴로 받는다
    ctx.app_time_src = ""
    apps = _on_apps(channel_id)
    if not apps:
        return
    who = str(getattr(ctx, "user_mask", "") or "")
    if inputs is None:
        inputs = [(str(getattr(ctx, "action_text", "") or ""), who)]
    turn = _turn(channel_id)
    blocks: List[str] = []
    for a in apps:
        hooks = a.get("hooks") or []
        if "on_input" in hooks:
            for text, pwho in inputs:
                try:
                    _, sess = await _run(channel_id, a, "on_input", (text, pwho), pwho)
                    _commit(channel_id, a, sess, "turn", turn)
                    _tm = time_minutes(channel_id, sess.time_req)   # [C116 판5] 앱 여럿이면 가장 긴 것(한 행동 · 한 시계)
                    if _tm > int(getattr(ctx, "app_time_min", 0) or 0):
                        ctx.app_time_min, ctx.app_time_src = _tm, str(a.get("name") or a["id"])
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


def judge_term(res: Any) -> Optional[Tuple[str, int]]:
    """[2026-11-06 C116 0-3] judge 가 돌려준 값 → (라벨, 정수) 또는 None.
    nil · 그 밖 꼴 · 참거짓 · 0 → None. 수 = 라벨 없는 항, {value, label} = 라벨 붙은 항. 소수는 0 쪽으로 자른다.
    라벨은 공백 접기(줄바꿈 → 칸) · JUDGE_LABEL_MAX 자. 크기는 여기서 안 자른다 — 폭의 주인은 judgment_engine."""
    label = ""
    val = res
    if isinstance(res, dict):
        val = res.get("value")
        lab = res.get("label")
        if isinstance(lab, str):
            label = " ".join(lab.split())[:JUDGE_LABEL_MAX].strip()
    if isinstance(val, bool) or not isinstance(val, (int, float)):
        return None
    if isinstance(val, float):
        if not math.isfinite(val):
            return None
    v = int(val)
    if v == 0:
        return None
    return label, v


async def judge_terms(channel_id: str, j: Dict[str, Any], who: str = "") -> List[Dict[str, Any]]:
    """[2026-11-06 C116 0-3] 판정 바깥 항 — judge 를 정의한 켜진 앱마다 judge(j, who). 읽기 전용(feed · view 처럼
    쓰기 · 보고 버림). 앱끼리 같이 띄운다(작업 프로세스 둘). 그런 앱이 없으면 호출 0.
    Returns: [{app, name, label, value}] 앱 번호 순. 실패 · 시간 초과 · 항 없음인 앱은 빠진다(오류는 앱 표)."""
    apps = [a for a in _on_apps(channel_id) if "judge" in (a.get("hooks") or [])]
    if not apps:
        return []
    jj = {"type": str(j.get("type") or ""), "action": str(j.get("action") or "")[:200],
          "difficulty": str(j.get("difficulty") or ""), "skill": str(j.get("skill") or "")}

    async def one(a: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        try:
            res, _ = await _run(channel_id, a, "judge", (jj, who), who)   # 읽기 — 쓰기 · 보고는 버린다
        except LuaFail:
            return None
        except Exception as e:
            logger.warning("[App] %s.judge 건너뜀 ch=%s: %s", a.get("id"), channel_id, e)
            return None
        t = judge_term(res)
        if t is None:
            return None
        return {"app": a["id"], "name": a["name"], "label": t[0], "value": t[1]}

    t0 = time.monotonic()
    got = await asyncio.gather(*(one(a) for a in apps))
    out = [g for g in got if g]
    logger.info("[App] 판정 항 ch=%s 앱 %d → 항 %d · %dms %s", channel_id, len(apps), len(out),
                int((time.monotonic() - t0) * 1000), [(g["app"], g["value"]) for g in out])
    return out


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
    # [C116 판1 1a] rules.md 는 Lua 를 안 탄다 — 선언만 든 앱은 lupa 없는 서버에서도 실린다(_on_apps 는 lupa 를 본다)
    for a in [x for x in list_apps(channel_id) if x["on"] and x.get("pkg")]:
        r = ((a.get("pkg") or {}).get("rules") or "").strip()
        if r:
            out.append(RULES_HEAD.format(name=a["name"]) + "\n" + r)
    return "\n\n".join(out)


async def day_station(channel_id: str, info: Optional[Dict[str, Any]] = None) -> int:
    """[2026-11-06 C116 판3] 날 경계(ON_DAY 정거장) — on_day 를 단 켜진 앱마다 on_day({days, day_index, calendar}).
    턴 안(orchestration 4.7)이라 쓰기는 턴 몫(source turn) — `!다시` 워터마크가 되감고 다시 돈 턴이 다시 쓴다. → 돈 앱 수."""
    apps = [a for a in _on_apps(channel_id) if "on_day" in (a.get("hooks") or [])]
    if not apps:
        return 0
    t = _turn(channel_id)
    w = _world(channel_id, "")
    arg = {"days": int((info or {}).get("days_jumped") or 1), "day_index": w.get("day_index"),
           "calendar": w.get("calendar", "")}
    n = 0
    for a in apps:
        try:
            _, sess = await _run(channel_id, a, "on_day", (arg,), "")
            _commit(channel_id, a, sess, "turn", t)
            n += 1
        except LuaFail:
            continue
    return n


def _insert_turn_lines(prompt: Any, body: str) -> Any:
    """[C116 판4] 조립이 끝난 프롬프트의 이번 턴 문서(Slot 29 `</Real_Time_Status>` 앞)에 줄을 끼운다 — 도착물(insert_handout)과 같은 자리.
    3분할 tuple 이면 마지막(이번 턴)만. 닫는 태그가 없으면 앞에 붙인다."""
    def _ins(now: Any) -> str:
        now = str(now or "")
        i = now.find("</Real_Time_Status>")
        if i >= 0:
            pre = now[:i]
            if pre and not pre.endswith("\n"):
                pre += "\n"
            return pre + body + "\n" + now[i:]
        return body + ("\n\n" + now if now else "")
    if isinstance(prompt, tuple) and len(prompt) == 3:
        return (prompt[0], prompt[1], _ins(prompt[2]))
    if isinstance(prompt, list) and prompt:
        return prompt[:-1] + [_ins(prompt[-1])]
    return _ins(prompt)


async def pre_render(channel_id: str, ctx: Any, prompt: Any) -> Any:
    """[2026-11-06 C116 판4] PRE_RENDER_CALL — before_render 를 단 켜진 앱마다 before_render(j, who) → 글 한 줄.
    j = 이번 턴 판정 `{result, total, dice, mod, type, action}`(판정 없던 턴은 빈 표). 읽기 전용(쓰기 버림 — 쓰기는 on_output 이
    `lk.world().judgment` 로). 줄은 이번 턴 문서 Slot 29 끝, 머리 `<앱 이번 턴:이름>`. → 고친 프롬프트(줄 없으면 그대로)."""
    apps = [a for a in _on_apps(channel_id) if "before_render" in (a.get("hooks") or [])]
    if not apps:
        return prompt
    who = str(getattr(ctx, "user_mask", "") or "")
    j = _judgment_of(channel_id, _turn(channel_id)) or {}
    lines: List[str] = []
    for a in apps:
        try:
            res, _ = await _run(channel_id, a, "before_render", (j, who), who)
        except LuaFail:
            continue
        line = " ".join(str(res).split())[:PRE_RENDER_MAX] if isinstance(res, str) else ""
        if line:
            lines.append(PRE_RENDER_LABEL.format(name=" ".join(str(a.get("name") or a["id"]).split())) + "\n" + line)
    if not lines:
        return prompt
    logger.info("[App] 렌더 앞 줄 ch=%s 앱 %d", channel_id, len(lines))
    return _insert_turn_lines(prompt, PRE_RENDER_NOTE + "\n" + "\n".join(lines))


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
        if "on_output" in (a.get("hooks") or []):
            try:
                _, sess = await _run(channel_id, a, "on_output", (prose, {"turn": t, "message_id": mid}), who)
                _commit(channel_id, a, sess, "turn", t)
            except LuaFail:
                pass
        for _pw in (_active_masks(channel_id) or [who]):   # [C116 판8 · C117 C5] 상태창 앱 칸 PC 마다 — 재그림(BACKGROUND 25) 앞
            await refresh_panel(channel_id, a, _pw, t, "turn")
    set_client(getattr(rt, "client", None))   # [C116 판7] lk.ask 콜 길 — main 이 못 채웠을 때의 바닥
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
    """정산에 끼는 앱 — 신고를 단 앱 + [C116 판3] 매 턴 정산 훅(on_turn_end)을 단 앱."""
    return [a for a in _on_apps(channel_id) if tag_spec(a) or "on_turn_end" in (a.get("hooks") or [])]


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


def build_tags_prompt(apps: List[Dict[str, Any]], user_input: str, prose: str, pc: str = "",
                      open_tags: Optional[Dict[str, Any]] = None,
                      contexts: Optional[Dict[str, str]] = None) -> Tuple[str, str]:
    """(지시 + 앱 명세, 이번 턴 재료). 앱 명세의 이름 · say 는 앱이 쓴 글이라 줄바꿈 없이 한 줄씩.
    [C116 판3] open_tags = {앱id: 이번 턴 열 태그 이름 집합}(없으면 전부) · contexts = {앱id: 지금 값 한 줄}(tags_context)."""
    lines = [APP_TAGS_FRAME, "", "## [APPS]"]
    for a in apps:
        lines.append(f"App `{a['id']}` ({' '.join(str(a.get('name') or '').split())}):")
        ctx_line = " ".join(str((contexts or {}).get(a["id"]) or "").split())[:TAGS_CONTEXT_MAX]
        if ctx_line:
            lines.append(f"  (state now: {ctx_line})")
        allow = (open_tags or {}).get(a["id"])
        for t in tag_spec(a):
            if allow is not None and t["name"] not in allow:
                continue
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
        # [C116 판3] 신고 폭 — 앱마다 이번 턴 열 태그(tags_open) · 지금 값 한 줄(tags_context). 둘 다 읽기 전용(쓰기 버림).
        open_tags: Dict[str, Any] = {}
        contexts: Dict[str, str] = {}
        _tags_open_text = (str(user_input or "") + "\n" + str(prose or ""))[:TAGS_OPEN_TEXT_MAX]
        for a in apps:
            hk = a.get("hooks") or []
            names = [t["name"] for t in tag_spec(a)]
            if not names:
                open_tags[a["id"]] = set()
                continue
            if "tags_open" in hk:
                try:
                    r_open, _ = await _run(channel_id, a, "tags_open", (_tags_open_text,), pc)   # [C121] 이번 턴 입력 + 산문
                    if isinstance(r_open, dict):           # Lua 빈 표 {} · 번호 키 표 → 목록
                        r_open = list(r_open.values())
                    open_tags[a["id"]] = ({str(x) for x in r_open} & set(names)) if isinstance(r_open, list) else set(names)
                except LuaFail:
                    open_tags[a["id"]] = set(names)        # 오류면 전부 연다(옛 동작)
            if "tags_context" in hk and open_tags.get(a["id"], True):
                try:
                    r_ctx, _ = await _run(channel_id, a, "tags_context", (), pc)
                    if isinstance(r_ctx, str) and r_ctx.strip():
                        contexts[a["id"]] = r_ctx.strip()[:TAGS_CONTEXT_MAX]
                except LuaFail:
                    pass
        call_apps = [a for a in apps if tag_spec(a) and open_tags.get(a["id"], True)]
        res: Dict[str, List[Dict[str, Any]]] = {}
        missed = False
        if call_apps:
            sys_p, usr_p = build_tags_prompt(call_apps, user_input, prose, pc, open_tags, contexts)
            try:
                raw = await asyncio.wait_for(_tags_call(client, sys_p, usr_p), timeout=SETTLE_LIMIT)
            except asyncio.TimeoutError:
                raw, missed = None, True
                if _current(channel_id, token):
                    _miss(channel_id, turn, "시간 초과", ids)
            if not _current(channel_id, token):
                logger.info("[AppTags] 늦은 결과 버림 ch=%s turn=%s", channel_id, turn)
                return
            if not missed:
                res = clean_tags_result(raw, call_apps, source=f"{user_input}\n{prose}")
                if res is None:
                    res, missed = {}, True
                    _miss(channel_id, turn, "답을 못 읽음", ids)
            # [C116 판3] 신고를 못 받은 턴도 on_turn_end 는 돈다(0건) — 매 턴 정산 훅
            for aid, allow in open_tags.items():           # 닫은 태그로 온 건 버린다
                if aid in res and allow is not None:
                    res[aid] = [r for r in res[aid] if r.get("tag") in allow]
        staged: List[Tuple[Dict[str, Any], List[Dict[str, Any]], Any, Any]] = []
        for a in apps:
            rows = res.get(a["id"]) or []
            cur = get_app(channel_id, a["id"])
            if cur is None or not cur["on"] or not cur.get("pkg"):
                continue                                   # 정산 중에 꺼졌거나 지워졌다
            hk = cur.get("hooks") or []
            if not rows and "on_turn_end" not in hk:
                continue
            items = [dict(r, turn=turn) for r in rows]
            sess = sess_end = None
            if rows and "on_tags" in hk:
                try:
                    _, sess = await _run(channel_id, cur, "on_tags", (items,), pc, extra_tags=items)
                except LuaFail:
                    sess = None                            # 오류는 앱 표에 적혔다 — 신고 행은 그대로 적는다
            if "on_turn_end" in hk:                         # [C116 판3] 신고 0건이어도 매 턴 — on_tags 뒤 장부를 본다
                try:
                    _, sess_end = await _run(channel_id, cur, "on_turn_end", (items,), pc, extra_tags=items,
                                             over=sess)   # [C117 B2] on_tags 의 장부 · 값 · 관계 · 목표를 비춘 사본
                except LuaFail:
                    sess_end = None
            staged.append((cur, rows, sess, sess_end))
        # ── 여기부터 await 없음 ──
        if not _current(channel_id, token):
            logger.info("[AppTags] 정산이 그사이 버려짐 — 안 적음 ch=%s turn=%s", channel_id, turn)
            return
        import sqlite_store
        total = ran = 0
        for cur, rows, sess, sess_end in staged:
            now = get_app(channel_id, cur["id"])
            if now is None or not now["on"]:
                continue
            if rows:
                total += sqlite_store.append_app_tags(channel_id, cur["id"], turn, message_id, rows)
            if sess is not None:
                _commit(channel_id, now, sess, "tags", turn)
                ran += 1
            if sess_end is not None:
                _commit(channel_id, now, sess_end, "tags", turn)
                ran += 1
        if not missed:
            _MISS.pop(channel_id, None)
        logger.info("[AppTags] ch=%s turn=%s 앱 %d · 신고 %d건 · on_tags %d · %.1fs",
                    channel_id, turn, len(apps), total, ran, time.time() - t0)
        for cur, _rows, sess, sess_end in staged:   # [C116 판8] 정산이 바꾼 장부로 상태창 앱 칸 다시 셈
            if sess is not None or sess_end is not None:
                await refresh_panel(channel_id, cur, pc, turn, "tags", still=lambda: _current(channel_id, token))
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


def _clean_inputs(inputs: Any) -> List[Dict[str, Any]]:
    """[C116 c3] 부품 `edit` · `add` 의 입력칸 — 꼴 · 상한만(값 검사는 Lua 몫). key 없는 칸 · 모르는 kind 는 버린다."""
    out: List[Dict[str, Any]] = []
    for x in (inputs if isinstance(inputs, list) else [])[:_INPUTS_MAX]:
        if not isinstance(x, dict) or x.get("kind") not in _INPUT_KINDS or not str(x.get("key") or "").strip():
            continue
        q: Dict[str, Any] = {"key": str(x["key"])[:40], "kind": x["kind"]}
        v = x.get("value")
        if isinstance(v, bool):
            v = int(v)
        if isinstance(v, (int, float)) and math.isfinite(v):
            q["value"] = v
        elif isinstance(v, str):
            q["value"] = v[:4000]
        for k in ("min", "max", "maxlength"):
            n = x.get(k)
            if isinstance(n, (int, float)) and not isinstance(n, bool) and math.isfinite(n):
                q[k] = int(n) if k == "maxlength" else n
        if "maxlength" in q:
            q["maxlength"] = max(1, min(4000, q["maxlength"]))
        for k, cap in (("unit", 20), ("placeholder", 60)):
            if isinstance(x.get(k), (str, int, float)) and not isinstance(x.get(k), bool):
                q[k] = str(x[k])[:cap]
        if x["kind"] == "select":
            q["options"] = [[str(o[0])[:60], str(o[1])[:60]] for o in (x.get("options") or [])
                            if isinstance(o, list) and len(o) >= 2][:40]
        out.append(q)
    return out


def _clean_parts(parts: Any, depth: int = 0, budget: Optional[List[int]] = None,
                 pkg: Optional[Dict[str, Any]] = None, img_budget: Optional[List[int]] = None,
                 inline: bool = True) -> List[Dict[str, Any]]:
    """앱이 낸 부품 — 아는 t 만 · 글은 글로 · act 는 {id, data} 만. 카드 안 카드는 화면이 평평하게 편다.
    [C116 판8] image = 그 앱 그림 팩에서 data: 로(pkg 없으면 · 없는 그림이면 버림)."""
    if budget is None:
        budget = [MAX_PARTS]
    if img_budget is None:
        img_budget = [VIEW_ASSET_BUDGET]
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
        if t in ("edit", "add"):   # [C116 c3] 입력칸이 있는 edit · add 는 act 가 있어야 한다(없으면 버림)
            ins = _clean_inputs(p.get("inputs"))
            if ins:
                q["inputs"] = ins
            act = _clean_act(p.get("act"))
            if act is not None:
                q["act"] = act
            elif ins or t == "add":
                continue
            rm = p.get("remove")
            if t == "edit" and isinstance(rm, dict):
                ra = _clean_act(rm.get("act"))
                if ra is not None:
                    q["remove"] = {"act": ra}
                    if rm.get("confirm") is not None and not isinstance(rm.get("confirm"), (dict, list)):
                        q["remove"]["confirm"] = str(rm["confirm"])[:200]
            if t == "add" and not ins:
                continue
        if t == "image":
            # [C117 C4] inline=False(봇 화면) = 그림을 싣지 않고 {asset, h} — 화면이 따로 받아 캐시한다. 도구 · 옛 길 = data:.
            _nm, _ent = _asset_entry(pkg, p.get("src"))
            if not _ent or not str(_ent.get("mime") or "").startswith("image/"):
                continue
            if inline:
                uri = asset_uri(pkg, p.get("src"), img_budget)
                if not uri:
                    continue
                q["src"] = uri
            else:
                _n = len(_ent.get("b64") or "") + 40
                if _n > img_budget[0]:
                    continue
                img_budget[0] -= _n
                q["asset"], q["h"] = _nm, _ent["h"]
            q["alt"] = str(p.get("alt") or "")[:200]
            try:
                if p.get("w") is not None:
                    q["w"] = max(16, min(800, int(p["w"])))
            except Exception:
                pass
        if t in ("card", "fold"):
            q["parts"] = _clean_parts(p.get("parts"), depth + 1, budget, pkg, img_budget, inline)
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
    base = {"app": app_id, "name": a["name"], "rev": rev,
            "pending": asking(channel_id, app_id)}   # [C117 C3] 물음이 도는 중 — 화면이 잠시 뒤 다시 받는다
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
        # [C116 판8 → C117 C4] 그림 · 소리 팩 — 글은 그대로, {이름: hash} 를 같이 보낸다(화면이 따로 받아 갈아 낀다 · 캐시)
        return dict(base, screen="html", html=html, assets=html_asset_refs(html, a.get("pkg")))
    parts = res.get("parts") if isinstance(res, dict) else res
    return dict(base, screen="parts",
                parts=_clean_parts(parts, pkg=a.get("pkg"), inline=False) or [{"t": "empty", "text": "비어 있어요."}])


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
    out = _between_commit(channel_id, a, sess, turn, res, f"{a['name']} · {button_id}")
    if out.get("ok"):
        await refresh_panel(channel_id, a, who, turn, "click")   # [C116 판8]
    if out.get("ok") and sess.ask is not None:   # [C116 판7] 모델에게 묻기 — 커밋이 선 뒤에만
        out["line"] = out["receipt"] = (out["receipt"] + _start_ask(channel_id, a, uid, who, sess.ask))[:MAX_RECEIPT]
    logger.info("[App] 누름 ch=%s %s.%s → %s", channel_id, app_id, button_id, out.get("line"))
    return out


def _between_commit(channel_id: str, a: Dict[str, Any], sess: Any, turn: int, res: Any, default_receipt: str) -> Dict[str, Any]:
    """턴 사이 쓰기(누르기 · [C116 판7] 답) — 커밋 → 시간 → 영수증 → 일지(app.kv, 쓴 값 그대로)."""
    app_id = a["id"]
    writes = _commit(channel_id, a, sess, "click", turn)
    if (sess.writes or sess.reports) and not writes:
        return {"ok": False, "line": "⚠ 앱 장부가 너무 커서 이번 쓰기를 버렸어요"}
    applied = list(getattr(sess, "applied_values", None) or [])   # [C116 판2] 쓴 뒤 값 — 일지에 그대로
    clock_after = None
    t_min = time_minutes(channel_id, sess.time_req)                # [C116 판5] 누르기 시간 = 바로 흐른다(턴 사이 — `!시간` 과 같은 몫)
    if t_min:
        try:
            import game_world
            game_world.advance_minutes(channel_id, t_min)
            clock_after = _clock_state(channel_id)
        except Exception as e:
            logger.warning("[App] 누르기 시간 실패 %s: %s", app_id, e)
    if getattr(sess, "applied_weather", None) and not clock_after:   # [C121] lk.weather = 버튼 몫(쓴 뒤 시계로 다시 얹힘)
        clock_after = _clock_state(channel_id)
    receipt = (res.strip() if isinstance(res, str) and res.strip() else default_receipt)[:MAX_RECEIPT]
    if t_min and clock_after:
        receipt = (receipt + f" · ⏱ +{t_min}분")[:MAX_RECEIPT]
    out = {"ok": True, "line": receipt, "receipt": receipt, "undo": None}
    own = {k: v for k, v in writes.items() if k not in (REPORT_KEY, HANDOUT_KEY)}
    core_done = dict(getattr(sess, "applied_core", None) or {})   # [C116 판6] 관계 쓴 뒤 값 · 목표 걸음
    hos = list(getattr(sess, "handouts", None) or [])               # [C116 판8] 이 누르기의 도착물만
    if own or sess.reports or applied or clock_after or core_done or hos:
        # 일지 = 이 누르기가 쓴 값 + 이 누르기의 보고만(합친 `_reports` 는 안 적는다 — 되감긴 턴 몫이 섞여 있다)
        import uuid
        out["journal"] = {"action": "app.kv", "app": app_id, "writes": own,
                          "reports": list(sess.reports), "token": uuid.uuid4().hex[:12]}
        if applied:
            out["journal"]["values"] = applied
        if clock_after:
            out["journal"]["clock"] = clock_after   # 쓴 뒤 시계 그대로(time.state 꼴 — 날씨 무작위까지)
        if core_done:
            out["journal"]["core"] = core_done      # 관계 = 쓴 뒤 값(절대) · 목표 = 걸음(되감기가 행을 지웠으니 다시 낸다)
        if hos:
            out["journal"]["handouts"] = hos        # 재적용 = `_handouts@<표>` 절대값(보고와 같은 꼴 — 연속 `!다시` 에도 한 번)
        adds = {k: n for k, n in (getattr(sess, "kv_adds", None) or {}).items() if k in own}
        if adds:
            out["journal"]["adds"] = adds           # [C117 A3] lk.add 키 = 더한 양으로 다시 얹는다
    return out


# =====================================================================
# [2026-11-06 C116 판8] 상태창 앱 칸(panel) · 도착물 요청(lk.handout)
# =====================================================================
def _clean_panel(res: Any) -> List[List[str]]:
    """panel(who) 가 낸 것 → [[라벨, 값]] ≤8. 꼴 = {{라벨, 값}, …} · {rows = {...}} · 글 줄 목록(라벨 없음)."""
    if isinstance(res, dict):
        res = res.get("rows")
    if isinstance(res, str):
        res = [res]
    out: List[List[str]] = []
    for r in (res if isinstance(res, list) else [])[:PANEL_ROWS_MAX]:
        if isinstance(r, list) and len(r) >= 2:
            out.append([str(r[0])[:PANEL_LABEL_MAX], str(r[1])[:PANEL_VALUE_MAX]])
        elif isinstance(r, (str, int, float)) and not isinstance(r, bool) and str(r).strip():
            out.append(["", str(r)[:PANEL_VALUE_MAX]])
    return out


async def refresh_panel(channel_id: str, a: Dict[str, Any], who: str, turn: int, source: str,
                        still: Any = None) -> bool:
    """panel(who) → 장부 `_panel`(바뀌었을 때만). 읽기 전용 훅 — 앱의 쓰기 · 보고는 버린다. still() 이 거짓이면 안 적는다."""
    if "panel" not in (a.get("hooks") or []) or not a.get("pkg"):
        return False
    try:
        res, _ = await _run(channel_id, a, "panel", (who,), who)
    except LuaFail:
        return False
    rows = _clean_panel(res)
    if still is not None and not still():
        return False
    import sqlite_store
    cur = sqlite_store.app_kv_current(channel_id, a["id"]).get(PANEL_KEY)
    masks = _active_masks(channel_id)
    if len(masks) <= 1:   # 솔로(옛 꼴) — 줄 목록 하나
        new: Any = rows or None
    else:                 # [C117 C5] PC 여럿 — {가면: 줄}, 이 사람 몫만 갈아 낀다 · 안 뛰는 PC 몫은 뺀다
        base = {k: v for k, v in (cur.items() if isinstance(cur, dict) else []) if k in masks and v}
        if rows:
            base[str(who)] = rows
        else:
            base.pop(str(who), None)
        new = base or None
    if (cur or None) == new:
        return False
    sqlite_store.append_app_kv(channel_id, a["id"], {PANEL_KEY: new}, int(turn), source)
    return True


def _active_masks(channel_id: str) -> List[str]:
    """[C117 C5] 뛰는 PC 가면(참가자 status active) — 순서는 참가 순."""
    try:
        import domain_manager
        return [str(p.get("mask")) for p in (domain_manager.get_domain(channel_id).get("participants") or {}).values()
                if isinstance(p, dict) and p.get("mask") and str(p.get("status") or "active") == "active"]
    except Exception:
        return []


def panel_sections(channel_id: str) -> List[Tuple[str, List[Tuple[str, str]]]]:
    """상태창 섹션 꼴 [(앱 이름, [(라벨, 값)])] — `status_panel.build_section_pages_data` 끝에 붙는다(Activity 상태 · 전체 모드 임베드)."""
    out: List[Tuple[str, List[Tuple[str, str]]]] = []
    try:
        import sqlite_store
        for a in list_apps(channel_id):
            if not a["on"] or "panel" not in (a.get("hooks") or []):
                continue
            rows = sqlite_store.app_kv_current(channel_id, a["id"]).get(PANEL_KEY)
            if isinstance(rows, list) and rows:
                out.append((a["name"], [(str(r[0]), str(r[1])) for r in rows if isinstance(r, list) and len(r) >= 2]))
            elif isinstance(rows, dict):   # [C117 C5] PC 여럿 — 가면마다 한 섹션
                for mk, rr in rows.items():
                    if isinstance(rr, list) and rr:
                        out.append((f"{a['name']} · {mk}", [(str(r[0]), str(r[1])) for r in rr
                                                            if isinstance(r, list) and len(r) >= 2]))
    except Exception as e:
        logger.debug("[App] panel sections skip: %s", e)
    return out


def _clean_handouts(hs: Any) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for h in (hs if isinstance(hs, list) else [])[:MAX_HANDOUTS_QUEUE]:
        if not isinstance(h, dict) or str(h.get("kind") or "") not in lua_runtime.HANDOUT_KINDS:
            continue
        body = str(h.get("body") or "").strip()[:lua_runtime.HANDOUT_BODY_MAX]
        if not body:
            continue
        q = {"kind": h["kind"], "body": body}
        for k, cap in (("author", 200), ("to", 200), ("title", 250), ("format", 200)):
            if str(h.get(k) or "").strip():
                q[k] = str(h[k]).strip()[:cap]
        out.append(q)
    return out


def take_handout(channel_id: str) -> Optional[Dict[str, Any]]:
    """산문 앞 도착물 자리(world_board `_deliver_handout`) — 켜진 앱 순서대로 대기열 맨 앞 하나를 꺼낸다(턴 몫 행 —
    `!다시` 가 되살린다). {app, name, h} | None."""
    import sqlite_store
    turn = _turn(channel_id)
    for a in [x for x in list_apps(channel_id) if x["on"]]:
        kv = sqlite_store.app_kv_current(channel_id, a["id"])
        for k in [HANDOUT_KEY] + sorted(x for x in kv if x.startswith(REPLAY_HANDOUT_PREFIX)):
            q = _clean_handouts(kv.get(k))
            if not q:
                continue
            rest = q[1:]
            sqlite_store.append_app_kv(channel_id, a["id"], {k: rest or None}, turn, "turn")
            return {"app": a["id"], "name": a["name"], "h": q[0]}
    return None


def requeue_handout(channel_id: str, taken: Optional[Dict[str, Any]]) -> None:
    """꺼냈는데 못 실은 도착물(선언 도착물이 이긴 턴 · 표시 실패)을 대기열 맨 앞으로 되돌린다."""
    if not isinstance(taken, dict) or not taken.get("app") or not isinstance(taken.get("h"), dict):
        return
    import sqlite_store
    cur = _clean_handouts(sqlite_store.app_kv_current(channel_id, taken["app"]).get(HANDOUT_KEY))
    sqlite_store.append_app_kv(channel_id, taken["app"], {HANDOUT_KEY: ([taken["h"]] + cur)[:MAX_HANDOUTS_QUEUE]},
                               _turn(channel_id), "turn")


def handout_result(channel_id: str, taken: Dict[str, Any], pc: str = "") -> Optional[Dict[str, Any]]:
    """앱 도착물 → world_board 선언 도착물 결과와 같은 꼴(`{channel_kind, kind, payload, event}`) — 표시 · 적립 · 기록이 같은 길."""
    try:
        import world_board as wb
    except Exception:
        return None
    h = taken.get("h") or {}
    kind = str(h.get("kind") or "message")
    post = {"title": h.get("title", ""), "body": h.get("body", ""), "author": h.get("author") or taken.get("name") or "",
            "to": h.get("to") or (pc if kind == "message" else ""), "format_name": h.get("format", "")}
    return {"channel_kind": kind, "kind": wb.BOARD_MAIL_KIND.get(kind, "mail"), "payload": wb._mail_payload(post, kind),
            "event": {"tag": f"app:{taken.get('app')}", "npc": post["author"]}, "post": post}


# =====================================================================
# [2026-11-06 C116 판7] 모델에게 묻기 — lk.ask(id, {task, material, max_chars, json}) → on_answer(id, 답, who, meta)
# =====================================================================
_CLIENT: Any = None                                   # main 이 set_client 로 · 산문 뒤 정거장이 rt.client 로 채운다
_ASKS: Dict[str, Dict[str, Any]] = {}                 # 채널 → {앱id: 태스크}
_ASK_COUNT: Dict[str, Dict[str, Any]] = {}            # 채널 → {turn, all, apps: {앱id: n}}
_ASK_GEN: Dict[str, int] = {}                         # 채널 → 세대(클리어 · 리셋이 올린다 — 늦은 답을 버리는 표)


def set_client(client: Any) -> None:
    global _CLIENT
    if client is not None:
        _CLIENT = client


def _clean_ask(r: Any) -> Optional[Dict[str, Any]]:
    if not isinstance(r, dict):
        return None
    q = str(r.get("id") or "").strip()[:64]
    task = str(r.get("task") or "").strip()[:lua_runtime.ASK_TASK_MAX]
    if not q or not task:
        return None
    try:
        n = max(1, min(lua_runtime.ASK_CHARS_MAX, int(r.get("max_chars") or lua_runtime.ASK_CHARS_DEFAULT)))
    except Exception:
        n = lua_runtime.ASK_CHARS_DEFAULT
    return {"id": q, "task": task, "material": str(r.get("material") or "")[:lua_runtime.ASK_MATERIAL_MAX],
            "max_chars": n, "json": bool(r.get("json"))}


def asking(channel_id: str, app_id: str = "") -> bool:
    """그 앱(빈 id 면 채널 아무 앱)의 물음이 도는 중인가."""
    tasks = _ASKS.get(str(channel_id)) or {}
    return any(not t.done() for k, t in tasks.items() if not app_id or k == app_id)


def _start_ask(channel_id: str, a: Dict[str, Any], uid: str, who: str, req: Dict[str, Any]) -> str:
    """상한을 보고 태스크를 띄운다 → 영수증 꼬리(묻는 중 · 거절 사유)."""
    ch, aid = str(channel_id), a["id"]
    if asking(ch, aid):
        return " · ⏳ 앞 물음이 아직 — 이번 물음은 버림"
    turn = _turn(ch)
    cnt = _ASK_COUNT.get(ch)
    if not cnt or cnt.get("turn") != turn:
        cnt = _ASK_COUNT[ch] = {"turn": turn, "all": 0, "apps": {}}
    if cnt["apps"].get(aid, 0) >= ASK_PER_TURN or cnt["all"] >= ASK_CHANNEL_PER_TURN:
        return " · ✗ 이번 턴 물음을 다 씀"
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return " · ✗ 물을 수 없음"
    cnt["apps"][aid] = cnt["apps"].get(aid, 0) + 1
    cnt["all"] += 1
    gen = _ASK_GEN.get(ch, 0)
    _ASKS.setdefault(ch, {})[aid] = loop.create_task(_ask_flow(ch, aid, uid, who, req, gen))
    logger.info("[AppAsk] 물음 ch=%s %s.%s %d자 · 이번 턴 %d/%d", ch, aid, req["id"], len(req["task"]) + len(req["material"]),
                cnt["apps"][aid], ASK_PER_TURN)
    return ASK_RECEIPT


def build_ask_prompt(a: Dict[str, Any], req: Dict[str, Any]) -> str:
    head = (f"A module the table installed (`{a.get('name') or a['id']}`) asks for one piece of text inside the story's world.\n"
            "Write only that text — no preamble, no notes, no quotation marks around it.")
    lim = (f"Return a JSON object only, its string values at most {req['max_chars']} characters in all."
           if req.get("json") else f"At most {req['max_chars']} characters. Korean unless the task says otherwise.")
    parts = [head, "## TASK\n" + req["task"]]
    if req.get("material"):
        parts.append("## MATERIAL (the module's own record — use it, do not contradict it)\n" + req["material"])
    parts.append(lim)
    return "\n\n".join(parts)


async def _ask_call(client: Any, a: Dict[str, Any], req: Dict[str, Any]) -> Any:
    """콜 하나(light) → 글 | 표(json) | None. 스모크는 이 자리를 갈아 끼운다(콜 0)."""
    from memory_system import api_call_with_retry
    from google.genai import types
    import bot_utils
    import text_resources
    cfg = types.GenerateContentConfig(
        system_instruction=text_resources.ANALYSIS_FRAME, temperature=0.9, max_output_tokens=ASK_MAX_OUTPUT_TOKENS,
        safety_settings=config.SAFETY_SETTINGS, **({"response_mime_type": "application/json"} if req.get("json") else {}))
    contents = [types.Content(role="user", parts=[types.Part(text=build_ask_prompt(a, req))])]
    with config.light_call(), config.call_timeout(ASK_TIMEOUT):
        result = await api_call_with_retry(client, config.role_model("light"), contents, cfg, operation_name="AppAsk")
    if not result:
        return None
    if req.get("json"):
        cleaned = bot_utils.clean_json_text(result)
        try:
            return json.loads(cleaned)
        except json.JSONDecodeError:
            return json.loads(bot_utils.repair_json(cleaned))
    return str(result).strip()[: req["max_chars"]]


async def _ask_flow(channel_id: str, app_id: str, uid: str, who: str, req: Dict[str, Any], gen: int) -> None:
    a = get_app(channel_id, app_id)
    ans, err = None, ""
    try:
        if _CLIENT is None:
            err = "모델 길이 아직 안 열림(봇 기동 직후)"
        else:
            ans = await _ask_call(_CLIENT, a or {"id": app_id}, req)
            if ans is None:
                err = "콜 실패"
            elif req.get("json") and not isinstance(ans, (dict, list)):
                ans, err = None, "JSON 이 아님"
    except asyncio.CancelledError:
        return
    except Exception as e:
        ans, err = None, f"콜 실패: {str(e)[:80]}"
    await deliver_answer(channel_id, app_id, uid, who, req, ans, err, gen)


async def deliver_answer(channel_id: str, app_id: str, uid: str, who: str, req: Dict[str, Any], ans: Any, err: str,
                         gen: int) -> Optional[Dict[str, Any]]:
    """답 → 턴 잠금 안에서 on_answer(id, 답, who, {error}) → 턴 사이 커밋 → 편집 일지(app.kv). 돌려줌 = 결과 | None(버림)."""
    import turn_locks, activity_edit, sqlite_store
    ch = str(channel_id)
    lock = turn_locks.channel_locks[ch]
    if not await activity_edit._acquire(lock, ASK_LOCK_WAIT):
        logger.warning("[AppAsk] 턴 잠금을 못 잡아 답을 버림 ch=%s %s.%s", ch, app_id, req.get("id"))
        return None
    try:
        await activity_edit._drain(ch)
        for _ in range(int(SETTLE_LIMIT * 2)):
            if not settling(ch, app_id):
                break
            await asyncio.sleep(0.5)
        if _ASK_GEN.get(ch, 0) != gen:
            logger.info("[AppAsk] 클리어 · 리셋 뒤 늦은 답 — 버림 ch=%s %s", ch, app_id)
            return None
        a = get_app(ch, app_id)
        if a is None or not a["on"] or "on_answer" not in (a.get("hooks") or []) or not a.get("pkg"):
            logger.info("[AppAsk] 앱이 꺼졌거나 on_answer 없음 — 답 버림 ch=%s %s", ch, app_id)
            return None
        turn = _turn(ch)
        meta = {"error": err} if err else {}
        try:
            res, sess = await _run(ch, a, "on_answer", (req["id"], ans, who, meta), who)
        except LuaFail:
            return None
        out = _between_commit(ch, a, sess, turn, res, f"{a['name']} · 답 {req['id']}")
        if out.get("ok"):
            await refresh_panel(ch, a, who, turn, "click")   # [C116 판8]
        if out.get("ok") and out.get("journal"):
            sqlite_store.append_activity_edit(ch, turn, uid, out["journal"], None, out.get("receipt") or "", kind="app")
        logger.info("[AppAsk] 답 ch=%s %s.%s %s → %s", ch, app_id, req.get("id"), "실패 " + err if err else "받음",
                    out.get("line"))
        return out
    finally:
        lock.release()
        tasks = _ASKS.get(ch) or {}
        t = tasks.get(app_id)
        if t is not None and t is asyncio.current_task():
            tasks.pop(app_id, None)


async def forget_asks(channel_id: str) -> None:
    """`!클리어` · `!리셋` — 도는 물음을 버린다(세대를 올려 늦은 답도 안 적는다)."""
    ch = str(channel_id)
    _ASK_GEN[ch] = _ASK_GEN.get(ch, 0) + 1
    for t in list((_ASKS.pop(ch, None) or {}).values()):
        if not t.done():
            t.cancel()
    _ASK_COUNT.pop(ch, None)


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
