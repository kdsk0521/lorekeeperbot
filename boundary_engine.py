# -*- coding: utf-8 -*-
"""
Boundary Engine — 시간 경계 틱  [2026-09-06 출력물 스펙 §0.6 / §0.7(c) / §4-5]

**게임 시간의 날짜가 넘어간 첫 턴**에 한 번 도는 자리. 스케줄 틱(orchestration ≈1862)과
같은 규율의 형제다:

  ① 전환 감지는 **마커 비교**지 writer 훅이 아니다.
     날짜를 쓰는 자리가 셋(game_system.process_time_flow → game_world.advance_to_slot/
     advance_minutes · 배경 _advance_scene_time · `!시간` advance_time)이라
     훅을 달면 같은 코드가 3벌로 복제된다. world_state 에 마커 한 칸을 두고
     현재 날짜와 비교하면 자리가 하나로 모이고, 수동 전환도 다음 턴에 자연히 잡힌다.
  ② **마커 갱신은 호출자 몫**이다. check_boundary 는 읽기만 한다 —
     감지와 처리 사이에서 죽으면 다음 턴에 다시 감지되어야 하기 때문이다.
     (단 첫 턴 = 마커 부재는 예외: 정산할 어제가 없으므로 마커만 찍고 None.)
  ③ 여러 날 점프도 **1회**로 끝낸다(스케줄 틱과 같은 안전 쪽). 몇 날인지는
     `days_jumped` 로만 남긴다 — 5회 콜은 하루 1콜 규율의 위반이다.

매턴 LLM 콜 순증 0. 경계 턴에만, 그것도 **배경 큐**로 콜 1개(light).
렌더는 이 모듈을 기다리지 않는다.

[2026-10-23 C62 M3] 하루 일을 **누가 하나**는 이제 모듈 틀이 쥔다 — 옛 SUBSCRIBERS 목록 대신
module_frame 의 ON_DAY 정거장(builtin_modules: 10 하루 연산 · 20 NPC 회복 · 30 일지 · 40 패널 day 칸).
이 파일에 남는 것 = 날짜 키·마커·감지(on_turn) + 하루 콜 주인(_day_digest 캐시·잠금) + 일지 쓰기 + 패널 day 병합.
새 하루 일은 여기 말고 모듈 선언에 `stations={ON_DAY: …}` 한 줄.

discord 무의존(순수 도메인). status_panel 은 지연 import 로만 만진다.
"""

import asyncio
import json
import logging
from typing import Any, Dict, List, Optional

import config
import domain_manager
from background_task_queue import enqueue_background_task, TaskPriority

logger = logging.getLogger("BoundaryEngine")

# world_state 의 마커 칸. dict 인 이유: week/month 자리를 **비워 둔 채** 열어 두기 위해서다
# (§7-2 기본안은 day 만 — 다른 키는 만들지도, 비교하지도 않는다).
BOUNDARY_MARKER_KEY = "boundary_tick"

JOURNAL_SECTION = "일지"
_DAY_SEP = "-"


# =========================================================
# 날짜 키 · 마커
# =========================================================

def day_key(world: Optional[Dict[str, Any]]) -> str:
    """world_state → 'Y-M-D'. 캘린더 미초기화 세션도 game_world 와 같은 규칙으로 읽는다."""
    if not isinstance(world, dict):
        return ""
    try:
        import game_world as _gw
        _gw._init_clock(world)
    except Exception as e:
        logger.debug(f"[Boundary] clock init skipped: {e}")
    try:
        return _DAY_SEP.join(str(int(world.get(k, 1) or 1)) for k in ("year", "month", "day"))
    except (TypeError, ValueError):
        return ""


def _abs_day(key: str) -> Optional[int]:
    """'Y-M-D' → 캘린더 절대 일수. 점프 폭 계산 전용(fermentation._gt_abs_minutes 와 같은 산법)."""
    parts = str(key or "").split(_DAY_SEP)
    if len(parts) != 3:
        return None
    try:
        y, mo, d = (int(p) for p in parts)
    except (TypeError, ValueError):
        return None
    dpy = int(getattr(config, "CALENDAR_DAYS_PER_YEAR", 360) or 360)
    dpm = int(getattr(config, "CALENDAR_DAYS_PER_MONTH", 30) or 30)
    return (y - 1) * dpy + (mo - 1) * dpm + (d - 1)


def journal_prefix(day: str) -> str:
    """일지 줄의 날짜 접두. 'Y-M-D' → '[D<일>]'. 표기 주인은 여기 하나."""
    parts = str(day or "").split(_DAY_SEP)
    return f"[D{parts[-1]}]" if parts and parts[-1] else "[D?]"


def mark_boundary(channel_id: str, day: str) -> None:
    """마커 갱신 — **성공 뒤에** 호출자가 부른다."""
    if not channel_id or not day:
        return
    try:
        world = domain_manager.get_world_state(channel_id) or {}
        marker = world.get(BOUNDARY_MARKER_KEY)
        if not isinstance(marker, dict):
            marker = {}
        marker["day"] = day
        world[BOUNDARY_MARKER_KEY] = marker
        domain_manager.update_world_state(channel_id, world)
    except Exception as e:
        logger.debug(f"[Boundary] marker write skipped: {e}")


def check_boundary(channel_id: str) -> Optional[Dict[str, Any]]:
    """날짜 경계를 넘었나. 넘었으면 {prev_day, now_day, days_jumped}, 아니면 None.

    **쓰지 않는다** — 마커 갱신은 호출자 몫이다. 예외는 마커 부재(세션 첫 턴):
    정산할 어제가 없으므로 마커만 심고 None 을 돌려준다.
    """
    if not channel_id:
        return None
    try:
        world = domain_manager.get_world_state(channel_id) or {}
    except Exception as e:
        logger.debug(f"[Boundary] world read skipped: {e}")
        return None
    now = day_key(world)
    if not now:
        return None
    marker = world.get(BOUNDARY_MARKER_KEY)
    prev = str((marker or {}).get("day") or "") if isinstance(marker, dict) else ""
    if not prev:
        mark_boundary(channel_id, now)
        return None
    if prev == now:
        return None
    a, b = _abs_day(prev), _abs_day(now)
    jumped = (b - a) if (a is not None and b is not None) else 1
    if jumped <= 0:
        # 시간이 뒤로 간 세션(수동 `!시간` 되감기 등). 정산할 어제가 없다 — 마커만 맞춘다.
        mark_boundary(channel_id, now)
        return None
    return {"prev_day": prev, "now_day": now, "days_jumped": jumped}


# =========================================================
# 하루 1회 배경 콜 (light) — 일지 + day 섹션 묶음
# =========================================================
# 콜은 **하나**다. 일지(ON_DAY 30)와 패널 day 섹션(ON_DAY 40)이 같은 산출을 나눠 쓴다.
# 두 번째는 캐시를 맞으므로 실제 API 왕복은 경계당 1회다.
_DIGEST_CACHE: Dict[str, Any] = {}      # channel_id -> (now_day, result)
_DIGEST_LOCKS: Dict[str, asyncio.Lock] = {}
_CLIENT: Any = None                     # on_turn 이 채운다(오케스트레이터의 genai client)


def forget_channel(channel_id: str) -> bool:
    """[2026-10-14 C37 R8] `!클리어`·`!리셋` — 채널의 하루 산출 캐시를 비운다.
    키가 day 라서, 안 비우면 새 세션의 같은 날짜 경계가 옛 세션 일지 산출을 그대로 받는다.
    락은 두지 않는다(돌고 있는 콜이 쥐고 있을 수 있다 — 캐시만 간다)."""
    return _DIGEST_CACHE.pop(str(channel_id or ""), None) is not None


def _prose_row_cap() -> int:
    return int(getattr(config, "BOUNDARY_PROSE_ROWS", 12) or 12)


def collect_day_prose(channel_id: str, prev_day: str) -> List[str]:
    """어제(prev_day)의 **산문만** 최근 순 최대 BOUNDARY_PROSE_ROWS 행, 각 1200자 컷.

    유저 입력은 넣지 않는다 — 일지는 "그날 무슨 일이 있었나"의 기록이고,
    그 사실의 정본은 렌더가 쓴 산문이다(검출≠쓰기).
    """
    if not channel_id or not prev_day:
        return []
    try:
        import sqlite_store
        rows = sqlite_store.read_history_tail(channel_id, 200) or []
    except Exception as e:
        logger.debug(f"[Boundary] history read skipped: {e}")
        return []
    out: List[str] = []
    for entry in reversed(rows):            # 최근 → 과거
        # [2026-10-17 C49 R5] 라이브 산문 행 역할은 "Model" — 소문자만 보던 탓에 일지 콜이 산문 0행으로 돌았다.
        if not isinstance(entry, dict) or str(entry.get("role") or "").lower() != "model":
            continue
        gt = entry.get("game_time")
        if isinstance(gt, str):
            try:
                gt = json.loads(gt)
            except Exception:
                gt = None
        if not isinstance(gt, dict) or day_key(dict(gt)) != prev_day:
            continue
        text = str(entry.get("content") or "").strip()
        if not text:
            continue
        out.append(text[:1200])
        if len(out) >= _prose_row_cap():
            break
    out.reverse()                           # 시간 순으로 돌려준다
    return out


def _day_payload(channel_id: str, info: Dict[str, Any]) -> Dict[str, Any]:
    """콜 입력 묶음. 산문 · 시간위치 줄 · day 섹션 정의."""
    prev = str(info.get("prev_day") or "")
    day_def = ""
    time_line = ""
    try:
        import status_panel as _sp
        day_def = _sp.get_panel_definition(channel_id, cadence="day")
        time_line = _sp._time_location_line(channel_id)
    except Exception as e:
        logger.debug(f"[Boundary] panel day definition skipped: {e}")
    return {
        "prose": collect_day_prose(channel_id, prev),
        "time_line": time_line,
        "day_definition": day_def,
        "prev_day": prev,
        "days_jumped": int(info.get("days_jumped", 1) or 1),
    }


def _normalize_digest(data: Any) -> Optional[Dict[str, Any]]:
    """콜 산출 → {"journal": [str]≤3, "sections": {섹션: {필드: str}}}. 건질 게 없으면 None.

    닫힌 스키마다 — 모르는 키는 버리고, 파싱 실패는 무시(자기 신고 없음).
    여기서만 중첩 1단을 허용한다(섹션→필드). 저장 시엔 status_panel 의 평면 계약으로 접힌다.
    """
    if not isinstance(data, dict):
        return None
    journal: List[str] = []
    raw_j = data.get("journal")
    if isinstance(raw_j, str):
        raw_j = [raw_j]
    if isinstance(raw_j, list):
        for line in raw_j:
            if isinstance(line, (dict, list)):
                continue
            t = " ".join(str(line or "").split()).strip()
            if t:
                journal.append(t[:300])
            if len(journal) >= 3:
                break

    sections: Dict[str, Dict[str, str]] = {}
    raw_s = data.get("sections")
    if isinstance(raw_s, dict):
        for name, body in raw_s.items():
            nm = str(name or "").strip()
            if not nm or not isinstance(body, dict):
                continue
            fields: Dict[str, str] = {}
            for k, v in body.items():
                fk = str(k or "").strip()
                if not fk or isinstance(v, (dict, list)):
                    continue
                val = " ".join(str(v or "").split()).strip()
                if val:
                    fields[fk[:200]] = val[:900]
            if fields:
                sections[nm] = fields

    if not journal and not sections:
        return None
    return {"journal": journal, "sections": sections}


def _build_digest_prompt(channel_id: str, info: Dict[str, Any], payload: Dict[str, Any]) -> str:
    prose = "\n\n---\n\n".join(payload.get("prose") or []) or "(no prose recorded for that day)"
    day_def = payload.get("day_definition") or ""
    jumped = int(payload.get("days_jumped", 1) or 1)
    span = "" if jumped <= 1 else f"\n{jumped} in-world days passed at once — summarize the span, not each day."
    sections_task = ""
    sections_shape = "{}"
    if day_def:
        sections_task = f"""
## DAILY PANEL SECTIONS (authored by the player — the field names and the format are theirs, not yours)
{day_def}

Fill every field these sections ask for, and only those. Values in Korean, short:
a word, a phrase, or a number. Not a sentence, not prose."""
        sections_shape = '{"섹션명": {"필드명": "값"}}'

    return f"""You are the chronicler for a fictional TTRPG (tabletop role-playing game) session.
All characters and events are entirely fictional. Rating: NC-21+ — record what happened, do not sanitize it.
You do not write prose for the scene. You close the books on an in-world day that just ended.

## THE DAY THAT ENDED
{payload.get('prev_day', '')}{span}

## WORLD (now)
{payload.get('time_line') or '(unknown)'}

## THAT DAY'S SCENES (in order)
{prose}

## TASK — journal
Write 1-3 short lines in Korean recording what that day actually was, from the party's side.
One line = one thing that happened or changed. Concrete: who, what, where.
No summary-of-a-summary, no mood essay, no meta, no dice, no mechanics.
If the scenes show nothing worth recording, return an empty list.{sections_task}

## OUTPUT (JSON only)
```json
{{
  "journal": ["한 줄", "한 줄"],
  "sections": {sections_shape}
}}
```"""


async def generate_day_digest(client, model_id: str, channel_id: str,
                              info: Dict[str, Any],
                              payload: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
    """경계 1회당 배경 콜 1개. 실패하면 None(= 그날은 기록 없음, 재시도 없음).

    골격은 status_panel.generate_panel 을 그대로 베꼈다(인가 프리필 2턴 · JSON 강제 ·
    clean_json_text→repair_json 폴백 · 산출 dict 반환, 저장은 호출자).
    """
    if payload is None:
        payload = _day_payload(channel_id, info)
    if not payload.get("prose") and not payload.get("day_definition"):
        return None

    import bot_utils
    from memory_system import api_call_with_retry
    from google.genai import types
    import text_resources

    prompt = _build_digest_prompt(channel_id, info, payload)

    cfg = types.GenerateContentConfig(
        system_instruction=text_resources.ANALYSIS_FRAME,   # [2026-10-06 분석 틀] 원본 mandate → 분석 틀
        temperature=0.5,
        # [2026-10-11 C27b] 1024 → 8192. 추론 켠 분석 콜은 추론이 출력 예산에 같이 든다 — 상한 통일(레티어스 "나머지도 다 올리자").
        max_output_tokens=8192,
        response_mime_type="application/json",
        safety_settings=config.SAFETY_SETTINGS,
    )
    contents = [
        # [2026-10-06 분석 틀] 렌더 훈련 대화·확인 응답 삭제 — 작업 한 줄은 프롬프트 머리로.
        types.Content(role="user", parts=[types.Part(text="Close the books on the in-world day. Output JSON only.\n\n" + prompt)]),
    ]

    try:
        result = await api_call_with_retry(client, model_id, contents, cfg,
                                           operation_name="BoundaryDayDigest")
        if not result:
            return None
        cleaned = bot_utils.clean_json_text(result)
        try:
            data = json.loads(cleaned)
        except json.JSONDecodeError:
            data = json.loads(bot_utils.repair_json(cleaned))
        return _normalize_digest(data)
    except Exception as e:
        logger.warning(f"[Boundary] day digest failed: {e}")
        return None


async def _day_digest(channel_id: str, info: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """경계당 **한 번만** 실제 콜을 태우고, 그 산출을 구독자들이 나눠 쓴다."""
    now = str(info.get("now_day") or "")
    cached = _DIGEST_CACHE.get(channel_id)
    if cached and cached[0] == now:
        return cached[1]
    lock = _DIGEST_LOCKS.setdefault(channel_id, asyncio.Lock())
    async with lock:
        cached = _DIGEST_CACHE.get(channel_id)
        if cached and cached[0] == now:
            return cached[1]
        payload = _day_payload(channel_id, info)
        try:
            with config.light_call():
                res = await generate_day_digest(
                    _CLIENT, config.role_model("light"), channel_id, info, payload)
        except Exception as e:
            logger.warning(f"[Boundary] day digest call skipped: {e}")
            res = None
        _DIGEST_CACHE[channel_id] = (now, res)
        return res


# =========================================================
# 일지 필자 — 일지 모듈(ON_DAY 30)이 부른다
# =========================================================

def apply_journal_lines(channel_id: str, prev_day: str, lines: List[str]) -> int:
    """일지 줄을 채널 스코프 노트북(notebook_shared)과 행 장부 양쪽에 적립. 적립 수 반환.

    노트북 = **보이는 꼬리**(상한 NOTEBOOK_JOURNAL_MAX, 오래된 날부터 탈락),
    notebook_log = **남는 전량**. 노출 축과 보관 축이 다르다(§2.2).
    """
    lines = [l for l in (lines or []) if str(l or "").strip()]
    if not channel_id or not lines:
        return 0
    prefix = journal_prefix(prev_day)
    stamped = [f"{prefix} {str(l).strip()}" for l in lines]
    cap = int(getattr(config, "NOTEBOOK_JOURNAL_MAX", 10) or 10)
    try:
        d = domain_manager.get_domain(channel_id)
        shared = domain_manager.notebook_parse(d.get("notebook_shared"))
        cur = shared["sections"][JOURNAL_SECTION]["lines"]
        cur.extend(stamped)
        if cap > 0 and len(cur) > cap:
            del cur[:len(cur) - cap]
        d["notebook_shared"] = shared
        domain_manager.save_domain(channel_id, d)
    except Exception as e:
        logger.warning(f"[Boundary] journal write failed: {e}")
        return 0
    try:
        import sqlite_store
        world = domain_manager.get_world_state(channel_id) or {}
        turn = int(world.get("turn_index", 0) or 0)
        for line in stamped:
            sqlite_store.append_notebook_log(channel_id, "", JOURNAL_SECTION, line,
                                             game_time=prev_day, turn_index=turn)
    except Exception as e:
        logger.debug(f"[Boundary] journal row skipped: {e}")
    return len(stamped)


async def queue_journal(channel_id: str, info: Dict[str, Any]) -> None:
    """일지 — 옛 구독자 A(`_sub_journal`). 콜은 배경 큐에 — 렌더는 이걸 기다리지 않는다."""
    async def _task():
        res = await _day_digest(channel_id, info)
        if not res:
            logger.info("[Boundary] day %s → journal skipped (no result)", info.get("prev_day"))
            return
        n = apply_journal_lines(channel_id, str(info.get("prev_day") or ""),
                                res.get("journal") or [])
        if n:
            logger.info("[Boundary] day %s → journal %d line(s) (jumped=%s)",
                        info.get("prev_day"), n, info.get("days_jumped"))

    await enqueue_background_task(channel_id, "BoundaryJournal", _task,
                                  priority=TaskPriority.NORMAL)


# =========================================================
# 패널 day 섹션 — 상태패널 모듈(ON_DAY 40)이 부른다
# =========================================================

async def queue_panel_day(channel_id: str, info: Dict[str, Any]) -> None:
    """패널 day 칸 — 옛 구독자 B(`_sub_panel_day`). 같은 콜의 sections 산출을 status_panel 저장값에 병합한다."""
    async def _task():
        try:
            import status_panel as _sp
            if not _sp.get_panel_definition(channel_id, cadence="day"):
                return          # day 섹션이 없으면 병합할 것도 없다
        except Exception as e:
            logger.debug(f"[Boundary] panel day gate skipped: {e}")
            return
        res = await _day_digest(channel_id, info)
        sections = (res or {}).get("sections") or {}
        if not sections:
            return
        try:
            import status_panel as _sp
            n = _sp.merge_panel_fields(channel_id, sections)
        except Exception as e:
            logger.warning(f"[Boundary] panel day merge failed: {e}")
            return
        if n:
            logger.info("[Boundary] day %s → panel day fields=%d", info.get("prev_day"), n)

    await enqueue_background_task(channel_id, "BoundaryPanelDay", _task,
                                  priority=TaskPriority.NORMAL)


# [2026-10-23 C62 M3] 옛 구독자 0(`_sub_expr_day`)·0b(`_sub_npc_rest`)는 몸 그대로 builtin_modules 로 갔다
#   (주인 = 출력룰 ON_DAY 10 · 활력 ON_DAY 20). 옛 SUBSCRIBERS 목록·register_subscriber 는 없다.


# =========================================================
# 진입점 (orchestration 4.7)
# =========================================================

async def on_turn(orch: Any = None, ctx: Any = None, channel_id: str = "") -> Optional[Dict[str, Any]]:
    """경계 턴이면 ON_DAY 정거장을 돌리고 마커를 갱신한다. 아니면 완전 무동작(로그도 없다).

    동기 부분은 코드뿐이고(마커 비교 · 정거장 호출), 콜은 전부 배경 큐로 간다 — 렌더 지연 0.
    마커는 정거장 **뒤**에 찍는다. 하루 일 하나가 죽어도 마커는 넘어간다:
    정산은 하루 한 번이고, 실패한 날은 그냥 없다(재시도 없음).
    [C62 M3] 하루 일 목록·순서는 모듈 틀(module_frame ON_DAY)이 쥔다. 한 모듈이 죽어도 나머지는 돈다(run_station).
    """
    global _CLIENT
    if not channel_id:
        return None
    info = check_boundary(channel_id)
    if not info:
        return None
    if orch is not None and getattr(orch, "client", None) is not None:
        _CLIENT = orch.client
    import module_frame as _mf
    await _mf.run_station(_mf.ON_DAY, orch, _mf.DayTick(channel_id=channel_id, info=info))
    mark_boundary(channel_id, info["now_day"])
    return info
