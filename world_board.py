# -*- coding: utf-8 -*-
"""
World Board Module — 세계 게시판 + SNS + 메시지
Discord 스레드에 세계관 내 게시물을 자동 생성.
서술턴과 완전 분리, 읽기 전용 (v1).

3채널: bulletin(공지), sns(소셜), message(개인 메시지/쪽지/편지)
각 채널별 독립 on/off + 독립 스레드.
"""

import json
import logging
import re
import discord
from typing import Optional, Dict, Any, List, Tuple

import config
import domain_manager
import bot_utils

logger = logging.getLogger("WorldBoard")

# 채널 정의: (key, thread_key, emoji, default_name)
BOARD_CHANNELS = {
    "bulletin": ("bulletin_thread_id", "📋", "게시판"),
    "sns":      ("sns_thread_id",      "📱", "SNS"),
    "message":  ("message_thread_id",  "💌", "메시지"),
}

# ⚰[2026-09-13 P14] 게시 최소 간격(`_FREQ_CONFIG_KEY`·`_default_frequency`·
#   `DEFAULT_BOARD_FREQUENCY`·`DEFAULT_CHANNEL_FREQUENCY`·`set/get_board_frequency`·
#   `get_all_frequencies`) 전량 삭제.
#   WHY: 저건 **콜을 아끼려고** 둔 자동 발화 타이머였다("N턴마다 한 번은 뭔가 올려라").
#   P14 가 방아쇠를 둘로 갈랐다 — ① 선언 전이 `deliver`(규칙이 정한 때) ② 분석 신호
#   `arrival`(장면이 부를 때). 둘 다 "지금 도착물이 필요하다"를 **말해서** 여는 문이고,
#   그 옆에서 턴을 세는 문은 같은 질문에 다른 답을 내는 두 번째 소유자가 된다:
#   편지가 온 턴에 게이트가 또 열려 공고가 하나 더 붙거나, 아무도 부르지 않은 턴에
#   세계가 혼자 말을 건다. 소비자 0 이 된 손잡이(`!게시판 빈도`)도 같은 카드에서 내렸다.


def _genre_labels(channel_id: str) -> tuple:
    """장르 → (stage, atmosphere) 표시 라벨. 없으면 ("미정", "미정").

    [2026-07-15 수리] 기존: `world.get("genres", {})`에서 `{"stage": [...],
    "atmosphere": ...}`를 기대 → **삼중 드리프트로 항상 "미정"**이었다(dead_scan B:
    world.genres READ-ONLY = 읽는데 쓰는 놈 없음):
      저장소 — world_state가 아니라 **domain**("active_genres")
      키     — "genres"가 아니라 **"active_genres"**
      모양   — stage/atmosphere가 아니라 **layers{world_setting,style_tech,
               narrative_tone} + atmosphere_guide** (command_handler L423-432가
               set_active_genres로 이 형태를 저장)
    옛 스키마를 향해 쓰인 코드다. 실제 생산자에 맞춘다.
    매핑: stage ← layers.world_setting(무대/세계설정) / atmosphere ← atmosphere_guide.
    ⚠ 레거시 세션은 get_active_genres가 list(["noir"])를 돌려줄 수 있다 → 양쪽 수용.
    """
    try:
        import domain_manager as _dm
        g = _dm.get_active_genres(channel_id)
    except Exception:
        return ("미정", "미정")
    if isinstance(g, dict):
        layers = g.get("layers") or {}
        ws = layers.get("world_setting") or []
        stage = ", ".join(str(t) for t in ws if t) if isinstance(ws, list) else ""
        atmosphere = str(g.get("atmosphere_guide") or "")
    elif isinstance(g, list):           # 레거시: ["noir"] 평면 리스트
        stage = ", ".join(str(t) for t in g if t)
        atmosphere = ""
    else:
        return ("미정", "미정")
    return (stage or "미정", atmosphere or "미정")


# ⛔[2026-07-28 삭제] _calc_post_count — NPC 수에 따라 채널당 게시물을 1~3으로 늘리려던 함수.
#   호출처 0(실제 호출부는 `max_posts=1` 하드코딩). 게시판이 심심하다고 느껴지면 그 숫자만
#   올리면 되므로 분기 함수를 유지할 이유가 없다. 구 규칙: ≤2명→1, ≤5명→2, 그 외 3.


# =========================================================
# Channel Toggle Helpers
# =========================================================

def get_board_channels(channel_id: str) -> Dict[str, bool]:
    """각 채널별 활성 상태 조회. 기본값: bulletin=True, sns=True, message=True."""
    world = domain_manager.get_world_state(channel_id)
    board_state = world.get("world_board", {})
    channels = board_state.get("channels", {})
    return {
        "bulletin": channels.get("bulletin", True),
        "sns":      channels.get("sns", True),
        "message":  channels.get("message", True),
    }


def set_board_channel(channel_id: str, ch_name: str, enabled: bool) -> None:
    """특정 채널 활성/비활성."""
    world = domain_manager.get_world_state(channel_id)
    board_state = world.get("world_board", {})
    channels = board_state.get("channels", {})
    channels[ch_name] = enabled
    board_state["channels"] = channels
    world["world_board"] = board_state
    domain_manager.update_world_state(channel_id, world)


# =========================================================
# [2026-08-16 도착물 라우트] 착지 모드 (thread / button / off)
# =========================================================
# v1 착지는 **공개 스레드**뿐이었다 — 편지·쪽지가 채널 전원에게 보인다. 채널종별로
# 착지를 고른다: thread=종전 스레드 게시 / button=그 턴 산문 메시지의 버튼(ephemeral)
# / off=드롭(로그만).
#
# [2026-08-17 v1.1 §3] **기본을 셋 다 button 으로.** v1은 message 만 button 이고
#   bulletin/sns 는 "원래 공개 게시물"이라 thread 를 유지했는데, 08-17 모듈 기본 ON 전환이
#   그 자리를 되살려 "표시 기본이 버튼인데 왜 스레드가 생기나"가 됐다. 실측 결과 살아 있던
#   별도 경로 같은 건 없었다 — 스레드 생성부는 `_ensure_thread` 하나뿐이고, 그걸 부르는
#   것도 이 아래 착지 루프 하나뿐이다. 즉 원인은 배선이 아니라 **이 기본값 두 칸**이었다.
#   공지·SNS도 채널을 스레드로 어지럽히지 않고 📰 버튼(kind=board)으로 내려앉는다.
#   스레드를 원하는 채널은 `!게시판 표시 공지 스레드`로 **명시**해야 구 경로를 탄다.
DISPLAY_MODES = ("thread", "button", "off")
DEFAULT_DISPLAY_MODE = {"bulletin": "button", "sns": "button", "message": "button"}

# 채널종 → turn_mail kind. 사적 도착물(message)은 💌, 공개 게시물(공지·SNS)은 📰로 나뉜다
# — 한 버튼에 섞으면 "나한테 온 것"을 여는 질문에 세상 소식이 끼어든다.
BOARD_MAIL_KIND = {"bulletin": "board", "sns": "board", "message": "mail"}


def get_display_mode(channel_id: str, ch_name: str) -> str:
    """채널종의 착지 모드. 미설정이면 DEFAULT_DISPLAY_MODE, 알 수 없는 값이면 thread."""
    world = domain_manager.get_world_state(channel_id)
    board_state = world.get("world_board", {})
    modes = board_state.get("display_mode", {})
    mode = modes.get(ch_name) if isinstance(modes, dict) else None
    if mode not in DISPLAY_MODES:
        mode = DEFAULT_DISPLAY_MODE.get(ch_name, "thread")
    return mode


def set_display_mode(channel_id: str, ch_name: str, mode: str) -> bool:
    """착지 모드 설정. 모르는 모드는 거부(False) — 조용한 오설정 방지."""
    if mode not in DISPLAY_MODES:
        return False
    world = domain_manager.get_world_state(channel_id)
    board_state = world.get("world_board", {})
    modes = board_state.get("display_mode", {})
    if not isinstance(modes, dict):
        modes = {}
    modes[ch_name] = mode
    board_state["display_mode"] = modes
    world["world_board"] = board_state
    domain_manager.update_world_state(channel_id, world)
    return True


def get_all_display_modes(channel_id: str) -> Dict[str, str]:
    """3채널 착지 모드 한 번에 (명령 UI 표시용)."""
    return {ch: get_display_mode(channel_id, ch) for ch in BOARD_CHANNELS}


# =========================================================
# Thread Management
# =========================================================

async def _ensure_thread(
    channel: discord.TextChannel,
    channel_id: str,
    thread_key: str,
    default_name: str,
) -> Optional[discord.Thread]:
    """스레드를 가져오거나 생성. thread_id를 world_state에 저장."""
    world = domain_manager.get_world_state(channel_id)
    board_state = world.get("world_board", {})
    thread_id = board_state.get(thread_key)

    # 기존 스레드 찾기
    if thread_id:
        thread = channel.guild.get_thread(thread_id)
        if thread:
            if thread.archived:
                try:
                    await thread.edit(archived=False)
                except Exception:
                    pass
            return thread

    # 새 스레드 생성
    try:
        thread = await channel.create_thread(
            name=default_name,
            type=discord.ChannelType.public_thread,
            auto_archive_duration=1440,
        )
        board_state[thread_key] = thread.id
        world["world_board"] = board_state
        domain_manager.update_world_state(channel_id, world)
        logger.info(f"[WorldBoard] Created thread '{default_name}' id={thread.id}")
        return thread
    except Exception as e:
        logger.error(f"[WorldBoard] Thread creation failed: {e}")
        return None


async def cleanup_threads(channel: discord.TextChannel, channel_id: str) -> None:
    """!클리어 시 스레드 아카이브."""
    world = domain_manager.get_world_state(channel_id)
    board_state = world.get("world_board", {})
    for ch_name, (thread_key, _, _) in BOARD_CHANNELS.items():
        tid = board_state.get(thread_key)
        if tid:
            thread = channel.guild.get_thread(tid)
            if thread:
                try:
                    await thread.edit(archived=True)
                except Exception:
                    pass
        board_state.pop(thread_key, None)
    world["world_board"] = board_state
    domain_manager.update_world_state(channel_id, world)


# =========================================================
# Flash Post Generation
# =========================================================

def _get_absent_npcs(channel_id: str) -> List[str]:
    """장면에 부재 중인 NPC 목록. gaze/psyche_states 기반."""
    npcs = domain_manager.get_npcs(channel_id)
    if not npcs:
        return []
    # [2026-08-11 사망 파이프라인] 생존축 필터 — 구멍 순위 4.
    #   여기서 뽑힌 이름이 SNS/게시판/메시지의 **작성자**가 된다. 무필터일 때
    #   죽은 인물이 부상 소식을 전하는 장면이 나온다(injury/confession은 message 채널 승격).
    import npc_manager as _npm
    all_names = {n for n, d in npcs.items() if _npm.is_npc_active(d)}
    if not all_names:
        return []

    # 최신 프레임에서 출석 NPC 추출
    # [2026-08-12 fingerprint 프레임 소급] get_latest_frame(=frames[-1])은 이번 턴에 push된 빈 프레임이라
    #   지문이 상시 빈손 → 온스테이지 인물까지 결석 처리되어 SNS/게시판 **작성자 후보**가 됐다.
    #   지문이 실제 찍힌 최근 프레임을 공용 관문으로 읽는다.
    present = set()

    # render_fingerprint.gaze → "이름, 이름" 형식
    gaze = domain_manager.get_prev_fingerprint(channel_id).get("gaze", "")
    if gaze and isinstance(gaze, str):
        for g in gaze.replace("\n", ",").split(","):
            name = g.strip()
            if not name:
                continue
            # 정확 일치
            if name in all_names:
                present.add(name)
                continue
            # 부분 매칭: "이하윤" → "Lee Ha-yoon(이하윤)"
            matched = domain_manager._find_npc_key(npcs, name)
            if matched:
                present.add(matched)

    absent = list(all_names - present)
    return absent[:10]


def _get_handle_registry(channel_id: str) -> Dict[str, Dict[str, str]]:
    """NPC별 SNS 핸들 레지스트리 조회. {npc_name: {tname, tid}}."""
    world = domain_manager.get_world_state(channel_id)
    board_state = world.get("world_board", {})
    return board_state.get("handle_registry", {})


def _update_handle_registry(channel_id: str, posts_data: Dict[str, Any]) -> None:
    """게시물에서 NPC 핸들 추출 → 레지스트리에 저장."""
    world = domain_manager.get_world_state(channel_id)
    board_state = world.get("world_board", {})
    registry = board_state.get("handle_registry", {})
    for ch_key in ("bulletin", "sns", "message"):
        raw = posts_data.get(ch_key)
        items = raw if isinstance(raw, list) else [raw] if raw else []
        for post in items:
            if not post or not isinstance(post, dict):
                continue
            author = post.get("author", post.get("from", ""))
            if not author or author in registry:
                continue
            entry = {}
            if post.get("feed_name"):
                entry["feed_name"] = post["feed_name"]
            if post.get("board_name"):
                entry["board_name"] = post["board_name"]
            if post.get("format_name"):
                entry["format_name"] = post["format_name"]
            if entry:
                registry[author] = entry
    board_state["handle_registry"] = registry
    world["world_board"] = board_state
    domain_manager.update_world_state(channel_id, world)


def _get_recent_post_summaries(channel_id: str, limit: int = 5) -> List[str]:
    """최근 게시물 요약 목록 (중복 방지용).

    [2026-10-17 C46 R8] 기계 말 줄(`MECH_WORD_RE`)은 **읽는 자리에서** 뺀다 — 프롬프트가 "같은 목소리의 나중
      순간"으로 이어 쓰라 하므로, 옛 "시계가 완성되어…" 글이 남아 있으면 그 말투가 다음 글로 물려진다. 저장은 그대로.
    """
    world = domain_manager.get_world_state(channel_id)
    board_state = world.get("world_board", {})
    rows = [r for r in (board_state.get("recent_summaries", []) or []) if not MECH_WORD_RE.search(str(r))]
    return rows[-limit:]


def _save_post_summaries(channel_id: str, posts_data: Dict[str, Any]) -> None:
    """게시물 내용을 요약하여 recent_summaries에 저장."""
    world = domain_manager.get_world_state(channel_id)
    board_state = world.get("world_board", {})
    summaries = board_state.get("recent_summaries", [])
    for ch_key in ("bulletin", "sns", "message"):
        raw = posts_data.get(ch_key)
        items = raw if isinstance(raw, list) else [raw] if raw else []
        for post in items:
            if not post or not isinstance(post, dict):
                continue
            author = post.get("author", post.get("from", "?"))
            body = (post.get("body") or "")[:60]
            if body:
                summaries.append(f"{author}: {body}")
    board_state["recent_summaries"] = summaries[-10:]  # 최근 10개만 유지
    world["world_board"] = board_state
    domain_manager.update_world_state(channel_id, world)


# =========================================================
# Event-Driven Board v2: Scanners + Selection + Routing
# =========================================================

def _turn_recent(turn: Any, current_turn: Any) -> bool:
    """[2026-09-24 감사] 도장 턴이 이번 턴 또는 직전 턴인가(Scanner 5·6 공용).

    배경 추출은 턴 N 의 산문 **뒤**에 N 도장으로 쓰고, 스캐너는 턴 N+1 의 산문 **앞**에서 돈다.
    None·문자열 도장은 거짓(예전 `==` 가 None 을 조용히 넘기던 것과 같은 결과, 예외 0).
    """
    try:
        return int(turn) >= int(current_turn) - 1
    except (TypeError, ValueError):
        return False


# =========================================================
# [2026-10-17 C46] 끝난 일 → 사건 카드
# =========================================================
# 병(C46 지도 §2): Scanner 4 는 `resolved` 하나만 보고 아홉 가지 끝을 전부 "상황 종결: X (시계 완성)" 0.95 로
#   냈다 — 조용히 잊힌 시계(정체·이탈·등락·방전)와 막아낸 시계까지 "완성"으로, 끝난 지 얼마나 됐는지도 안 보고
#   (게시 기록 20개에서 밀리면 재등장), 브리핑에 "시계 완성"·"(3/6)"을 그대로 실었다 → 도착물 본문이
#   "시계가 완성되어…"로 샜다(AUTHORSHIP "브리핑이 안 준 숫자는 발명"이라 브리핑이 준 숫자는 써도 되는 숫자였다).
# 처방: 끝난 사유(`end`, doom_module._mark_end)로 고르고 최근 것만, 브리핑은 **사건 카드**(머리·일·상태·얽힌 것).
#   카드는 결론 문장이 아니라 사실이다 — 어떻게 글에 닿을지는 글쓴이 몫(10-17 판정: "펼칠면은 빼자").
CLOCK_CARD_STATE = {"fired": "일어남", "achieved": "이뤄짐", "averted": "비켜 감", "quest": "비켜 감", "half": "짙어짐"}
CLOCK_CARD_WEIGHT = {"fired": 0.95, "achieved": 0.85, "averted": 0.7, "quest": 0.7, "half": 0.75}
QUEST_CARD_WEIGHT = 0.7
CARD_TASK_LINE = ("The event above is a card of facts, not a line to repeat; how it reaches this post is the author's.\n"
                  "The card's labels stay off the page.")
# 기계 말 감시 패턴 — 좁게 잡는다: 그냥 "시계"(시계탑·회중시계)는 세계 낱말일 수 있다.
#   읽는 쪽 둘: `_get_recent_post_summaries`(옛 글 거르기) · orchestration `[Handout] mech-word`(새 글 감시 로그).
MECH_WORD_RE = re.compile(r"시계[^.\n]{0,6}(?:완성|완료|찼|종결)|상황 (?:종결|심화)")


def _turn_within(turn: Any, current_turn: Any, window: int) -> bool:
    """[C46 R3] 도장 턴이 지금 턴에서 window 안인가. 도장 없음·문자열 = 거짓(옛 기록은 소재가 아니다).
    `!다시` 로 턴이 한 칸 물러선 경우(도장 = 지금+1)는 안으로 친다."""
    if turn is None:
        return False
    try:
        return -1 <= int(current_turn) - int(turn) <= int(window)
    except (TypeError, ValueError):
        return False


def _clock_card(clock: Dict[str, Any], end: str) -> tuple:
    """[C46 R2] (카드 본문, 검색 쿼리). 카드 = 머리 · 일(threat) · 상태 · 얽힌 것.
    숫자·"시계"·결론 문장("~이 벌어졌다")은 없다. 조사가 필요 없는 꼴이라 이름 받침에 따라 틀리지 않는다."""
    name = str(clock.get("name") or "").strip()
    threat = str(clock.get("threat") or "").strip()
    lines = [f"사건 카드 · {name}"]
    if threat:
        lines.append(f"일: {threat}")
    lines.append(f"상태: {CLOCK_CARD_STATE.get(end, '')}")
    tied: List[str] = []
    _le = str(clock.get("linked_entity") or "").strip()
    if _le:
        tied.append(_le)
    for _t in (clock.get("tags") or []):
        _t = str(_t or "").strip()
        if _t and _t not in tied:
            tied.append(_t)
    quest = ""
    tied_q: List[str] = []
    if end == "fired" and clock.get("failed_quest"):
        quest = str(clock["failed_quest"])
        tied_q = [f"'{quest}'(틀어짐)"]
    elif end == "quest" and (clock.get("end_quest") or clock.get("linked_quest")):
        quest = str(clock.get("end_quest") or clock.get("linked_quest"))
        tied_q = [f"'{quest}'(끝남)"]
    elif clock.get("linked_quest"):
        quest = str(clock["linked_quest"])
        tied_q = [f"'{quest}'"]
    if tied or tied_q:
        lines.append("얽힌 것: " + ", ".join(tied + tied_q))
    query = " ".join(b for b in [name, threat] + tied + [quest] if b)
    return "\n".join(lines), query


def _collect_board_events(channel_id: str, dai: Dict[str, Any]) -> List[Dict[str, Any]]:
    """파이프라인 산출물에서 보드 이벤트 수집. 0 API 콜. 실패한 스캐너는 무시."""
    events: List[Dict[str, Any]] = []
    world = domain_manager.get_world_state(channel_id)
    current_turn = world.get("turn_index", 0)

    # --- Scanner 1: Emotion Spike ---
    try:
        emo_states = world.get("npc_emotion_states", {})
        for npc_name, state in emo_states.items():
            if not isinstance(state, dict):
                continue
            if state.get("spike_detected"):
                weight = 0.7
                intensity = state.get("intensity", 0)
                if isinstance(intensity, (int, float)) and intensity > 0.8:
                    weight += 0.2
                # pair 스키마 v2: 'dominant' 제거 → base_label (full EmotionState dict)
                events.append({
                    "type": "emotion_spike",
                    "weight": weight,
                    "npc": npc_name,
                    "target_npc": None,
                    "channel": "sns",
                    "detail_kr": state.get("spike_detail", "")
                               or f"{npc_name}의 감정 급변: {state.get('base_label', '?')}",
                    "tag": f"emotion_spike:{npc_name}",
                })
    except Exception:
        pass

    # --- Scanner 2: Attitude Shift ---
    try:
        npc_attitudes = dai.get("npc_attitudes", {})
        if isinstance(npc_attitudes, dict):
            for npc_name, att_data in npc_attitudes.items():
                if not isinstance(att_data, dict):
                    continue
                trajectory = att_data.get("trajectory", "stable")
                if trajectory == "stable":
                    continue
                weight = 0.6
                if trajectory == "declining":
                    weight += 0.15
                reason = att_data.get("reason", "")
                events.append({
                    "type": "attitude_shift",
                    "weight": weight,
                    "npc": npc_name,
                    "target_npc": None,
                    "channel": "sns" if trajectory == "improving" else "message",
                    "detail_kr": reason or f"{npc_name}의 태도 변화: {trajectory}",
                    "tag": f"attitude:{npc_name}:{trajectory}",
                })
    except Exception:
        pass

    # --- Scanner 3: Storyteller Event Fired ---
    try:
        storyteller = world.get("storyteller", {})
        last_event_turn = storyteller.get("last_event_turn", -1)
        if last_event_turn == current_turn:
            recent_tags = storyteller.get("recent_tags", [])
            tag = recent_tags[-1] if recent_tags else "이변"
            # active_conditions에서 line 가져오기
            active_conds = storyteller.get("active_conditions", [])
            line = ""
            for c in reversed(active_conds):
                if isinstance(c, dict) and c.get("tag") == tag:
                    line = c.get("description", "")
                    break
            weight = 0.85
            events.append({
                "type": "anomaly_fired",
                "weight": weight,
                "npc": None,
                "target_npc": None,
                "channel": "bulletin",
                # [2026-10-17 C46] 줄이 없을 때의 폴백 "이변 발생: tag" 는 결론 말이라 카드로(줄이 있으면 그 줄 그대로).
                "detail_kr": line or f"사건 카드 · {tag}\n상태: 일어남",
                "query": " ".join(b for b in (str(tag), str(line or "")) if b),
                "card": not line,
                "tag": f"anomaly:{tag}",
            })
    except Exception:
        pass

    # --- Scanner 4: Situation Clock → 사건 카드 ---
    # [2026-10-17 C46 R1~R3] 끝난 사유로 고르고(잊힘·클라이맥스는 소재 아님) 최근 것만(BOARD_CLOCK_RECENT_TURNS),
    #   브리핑은 사건 카드. 절반 줄은 **끝나지 않은** 시계에만(전엔 끝난 시계에도 "상황 심화 (6/6)"가 났다).
    #   태그·게시 기록 키(clock_complete:/clock_half:)는 그대로다.
    try:
        board_state = world.get("world_board", {})
        posted_milestones = set(board_state.get("posted_clock_milestones", []))
        clocks = world.get("doom_clocks", [])
        _recent = int(getattr(config, "BOARD_CLOCK_RECENT_TURNS", 10))
        for clock in clocks:
            if not isinstance(clock, dict):
                continue
            name = str(clock.get("name", "") or "").strip()
            segments = int(clock.get("segments", 4) or 4)
            filled = int(clock.get("filled", 0) or 0)
            if not name or segments <= 0:
                continue
            if clock.get("resolved"):
                if f"{name}:complete" in posted_milestones:
                    continue
                _end = str(clock.get("end") or "")
                if _end not in CLOCK_CARD_WEIGHT or _end == "half":
                    logger.debug("[WorldBoard] clock skip (end=%s) %s", _end or "-", name)
                    continue
                if not _turn_within(clock.get("end_turn"), current_turn, _recent):
                    logger.debug("[WorldBoard] clock skip (old) %s", name)
                    continue
                _card, _q = _clock_card(clock, _end)
                events.append({
                    "type": "clock_milestone",
                    "weight": CLOCK_CARD_WEIGHT[_end],
                    "npc": None,
                    "target_npc": None,
                    "channel": "bulletin",
                    "detail_kr": _card,
                    "query": _q,
                    "names": [n for n in [str(clock.get("linked_entity") or "").strip()] if n],
                    "card": True,
                    "tag": f"clock_complete:{name}",
                })
            elif filled >= segments / 2 and f"{name}:half" not in posted_milestones:
                _card, _q = _clock_card(clock, "half")
                events.append({
                    "type": "clock_milestone",
                    "weight": CLOCK_CARD_WEIGHT["half"],
                    "npc": None,
                    "target_npc": None,
                    "channel": "bulletin",
                    "detail_kr": _card,
                    "query": _q,
                    "names": [n for n in [str(clock.get("linked_entity") or "").strip()] if n],
                    "card": True,
                    "tag": f"clock_half:{name}",
                })
    except Exception:
        pass

    # --- Scanner 5: NPC Imprint ---
    try:
        all_imprints = domain_manager.get_npc_imprints(channel_id)
        for npc_name, imp_list in all_imprints.items():
            if not isinstance(imp_list, list) or not imp_list:
                continue
            latest = imp_list[-1]
            if not isinstance(latest, dict):
                continue
            # [2026-09-24 감사] `== current_turn` → 직전 턴까지. 각인은 배경 추출이 턴 N 도장으로
            #   산문 **뒤**에 쓰고, 이 스캐너는 턴 N+1 의 4.75(산문 앞)에서 돈다 — 같은 턴 비교는
            #   영원히 거짓이라 스캐너가 사문이었다.
            if _turn_recent(latest.get("turn"), current_turn):
                event_type = latest.get("event", "")
                mark = latest.get("mark", "")
                channel = "message" if event_type in ("confession", "injury", "trauma") else "sns"
                events.append({
                    "type": "npc_imprint",
                    "weight": 0.8,
                    "npc": npc_name,
                    "target_npc": None,
                    "channel": channel,
                    "detail_kr": mark or f"{npc_name}에게 각인: {event_type}",
                    "tag": f"imprint:{npc_name}:{event_type}",
                })
    except Exception:
        pass

    # --- Scanner 6: Relation Change ---
    try:
        import entity_relations
        edges = entity_relations.get_all_relations(channel_id)
        for key, edge in edges.items():
            if not isinstance(edge, dict):
                continue
            history = edge.get("history", [])
            if not history:
                continue
            last_entry = history[-1]
            # [2026-09-24 감사] Scanner 5 와 같은 시계 어긋남 — 엣지 history 도 배경 추출(턴 N)이 쓴다.
            if isinstance(last_entry, dict) and _turn_recent(last_entry.get("turn"), current_turn):
                source = edge.get("source", "")
                target = edge.get("target", "")
                reason = last_entry.get("reason", "")
                new_type = last_entry.get("new_type", "")
                events.append({
                    "type": "relation_change",
                    "weight": 0.65,
                    "npc": source,
                    "target_npc": target,
                    "channel": "sns",
                    "detail_kr": reason or f"{source}→{target}: {new_type}",
                    "tag": f"relation:{source}→{target}",
                })
    except Exception:
        pass

    # --- Scanner 7: World Change ---
    try:
        session_mem = domain_manager.get_session_ai_memory(channel_id)
        world_changes = session_mem.get("world_changes", [])
        board_state = world.get("world_board", {})
        last_idx = board_state.get("last_world_change_idx", 0)
        if isinstance(world_changes, list) and len(world_changes) > last_idx:
            new_change = world_changes[-1]
            if isinstance(new_change, str) and new_change.strip():
                events.append({
                    "type": "world_change",
                    "weight": 0.5,
                    "npc": None,
                    "target_npc": None,
                    "channel": "bulletin",
                    "detail_kr": new_change,
                    "tag": f"world_change",
                })
    except Exception:
        pass

    # --- Scanner 8: Thread Resolved ---
    # [2026-09-25 스레드 장부] 옛 ai_session_memory.resolved_threads(쓰기 삭제) → 장부의 최근 닫힌 **사안**(matter·done).
    #   약속(promise)은 당사자 사이 일이라 공개 게시판 소재가 아니다. 게시 기록 키 = 스레드 id(제목 충돌 없음).
    try:
        import thread_ledger as _tl_wb
        _closed = [s for s in _tl_wb.load_states(channel_id).values()
                   if s.get("status") == "closed" and s.get("kind") == "matter" and s.get("outcome") == "done"
                   and s.get("title")]
        _closed.sort(key=lambda s: -(s.get("last_turn") or 0))
        _latest = _closed[0] if _closed else None
        # [2026-09-24 감사] 이미 게시한 해소는 다시 내지 않는다 — 게시 기록은 _update_board_state 의 `posted_resolved_threads`.
        _posted_res = set((world.get("world_board", {}) or {}).get("posted_resolved_threads", []) or [])
        if _latest and _latest["id"] not in _posted_res:
            events.append({
                "type": "thread_resolved",
                "weight": 0.6,
                "npc": None,
                "target_npc": None,
                "channel": "bulletin",
                # [2026-10-17 C46] "사건 해소" 결론 말 대신 카드.
                "detail_kr": f"사건 카드 · {_latest['title']}\n상태: 끝남",
                "query": str(_latest["title"]),
                "card": True,
                "tag": f"resolved:{_latest['title'][:20]}",
                "thread": _latest["id"],
            })
    except Exception:
        pass

    # --- Scanner 9: Visible Dice → 제거됨 (SD-A4, 2026-04-22)
    # 가시 다이스는 slot_manager에서 자료 존 끝(Slot 31 뒤, C43 전엔 Slot 19)에 직접 강제 제약으로 주입됨.
    # bulletin 경로로 중복 송출하면 같은 제약이 두 슬롯에 쌓여 혼선이 생김.

    # --- Scanner 10: Memory Trigger (filler) ---
    try:
        # [2026-07-02] 발효는 domain 루트에 저장 — ai_session_memory 읽기는 영구 빈손(Scanner 10 사망)이었음
        triggers = domain_manager.get_domain(channel_id).get("active_memory_triggers", [])
        if isinstance(triggers, list) and triggers:
            # 부재 NPC 중 하나를 랜덤 선정
            absent = _get_absent_npcs(channel_id)
            if absent:
                import random
                chosen_npc = random.choice(absent)
                chosen_trigger = triggers[-1] if isinstance(triggers[-1], str) else ""
                if chosen_trigger:
                    events.append({
                        "type": "memory_trigger",
                        "weight": 0.3,
                        "npc": chosen_npc,
                        "target_npc": None,
                        "channel": "sns",
                        "detail_kr": chosen_trigger,
                        "tag": f"memory:{chosen_trigger[:20]}",
                    })
    except Exception:
        pass

    # --- Scanner 11: Quest Done → 사건 카드 ---
    # [2026-10-17 C46 R4] 퀘스트 완료 그 자체도 소재 — 최근(`done_turn`) 하나. 연결 시계가 있으면 그 시계 카드
    #   (quest)가 같은 일을 말하므로 건너뛴다(한 일 두 번 금지). 실패는 시계 fired 카드가 같이 말한다.
    try:
        _qb = (domain_manager.get_domain(channel_id) or {}).get("quest_board") or {}
        _posted_q = set((world.get("world_board", {}) or {}).get("posted_quests", []) or [])
        _recent_q = int(getattr(config, "BOARD_CLOCK_RECENT_TURNS", 10))
        _cands = []
        for _qi, _qd in enumerate(_qb.get("completed") or []):
            if not isinstance(_qd, dict):
                continue
            _qn = str(_qd.get("content") or "").strip()
            if not _qn or _qn in _posted_q or _qd.get("linked_clock"):
                continue
            if not _turn_within(_qd.get("done_turn"), current_turn, _recent_q):
                continue
            _cands.append((int(_qd.get("done_turn") or 0), _qi, _qn))
        if _cands:
            _cands.sort(key=lambda x: (-x[0], -x[1]))   # 최근 턴 → 같은 턴이면 나중에 끝난 것
            _qn = _cands[0][2]
            events.append({
                "type": "quest_done",
                "weight": QUEST_CARD_WEIGHT,
                "npc": None,
                "target_npc": None,
                "channel": "bulletin",
                "detail_kr": f"사건 카드 · '{_qn}'\n상태: 끝남",
                "query": _qn,
                "card": True,
                "tag": f"quest_done:{_qn[:20]}",
                "quest": _qn,
            })
    except Exception:
        pass

    return events


def _allowed_sender_sources() -> Optional[set]:
    """발신자 가산이 붙는 출처 집합. None = 출처 축 끔(config 빈 값).

    계보는 turn_mail._allowed_mind_sources 와 같다 — 새 분류를 만들지 않는다.
    다만 여기서는 **차단이 아니라 가산**이다(💭는 게이트, 발신자는 우선순위).
    """
    raw = getattr(config, "BOARD_SENDER_SOURCES", "lore,manual")
    if raw is None:
        return None
    raw = str(raw).strip()
    if not raw:
        return None
    out = {s.strip().lower() for s in raw.split(",") if s.strip()}
    return out or None


def _sender_source(rec: Optional[Dict[str, Any]]) -> str:
    """시트 출처 문자열. 미상은 npc_manager 관례대로 session 으로 접힌다."""
    try:
        import turn_mail as _tm
        return _tm._npc_source(rec)
    except Exception:
        if not isinstance(rec, dict):
            return ""
        return str(rec.get("source", "session") or "session").lower()


def _sender_affinity(channel_id: str, npc: str) -> Dict[str, Any]:
    """[2026-08-17 발신자 긴밀화] 이 NPC가 PC에게 **사적으로** 쓸 만한 사이인가.

    사적 매체(message)의 발신자는 관계가 곧 매체다 — 공지·SNS는 낯선 이름이 정상이지만
    편지·쪽지는 아니다. 세 재료를 하나의 가산으로 접는다:
      depth   npc_attitudes[npc].depth(= bond, −100~+100) — **주 가중은 그 절대값(strength)**. 관계 축의 정본.
              [2026-09-24 감사 §5-2 #13 — 레티어스 판정] 적대도 "얽힌 사이"다 — 원수의 협박장·결투장처럼
              사적 편지를 보낼 만한 사이라 |bond| 로 가중한다(구: max(0,·) 로 적대 = 낯선 이). 부호 있는
              depth 는 그대로 두어 편지 프롬프트 `태도:` 줄이 hostile 옆에 depth=0 을 싣던 모순도 없앤다.
      appear  npcs[npc].appear_count — 보조. 포화형(SAT 회 이상이면 만점)이라
              깊이 없는 다등장이 depth 를 이기지 못한다.
      source  lore/manual(사람이 쓴 확정 시트) 가산. 그 턴 즉석 등재분은 못 받는다.
    반환 {bonus, depth, strength, appear, source} — 하드 필터(MIN_DEPTH)는 strength(= |depth|)를 쓴다.
    실패는 전부 0 가산(무해) — 관계를 못 읽은 것이 관계 없음의 근거는 아니다.
    """
    out = {"bonus": 0.0, "depth": 0, "strength": 0, "appear": 0, "source": ""}
    if not npc:
        return out
    try:
        _att = domain_manager.get_npc_attitudes(channel_id) or {}
        rel = _att.get(npc)
        if not isinstance(rel, dict):
            # 정규 키가 아닌 표기로 들어온 경우 — 이름 해상도 1회만 더.
            _k = domain_manager._find_npc_key(_att, npc)
            rel = _att.get(_k) if _k else None
        if isinstance(rel, dict):
            out["depth"] = int(rel.get("depth", 0) or 0)
            out["strength"] = min(100, abs(out["depth"]))
    except Exception as e:
        logger.debug(f"[WorldBoard] sender depth skip ({npc}): {e}")
    _rec = None
    try:
        _npcs = domain_manager.get_npcs(channel_id) or {}
        _k = domain_manager._find_npc_key(_npcs, npc)
        _rec = _npcs.get(_k) if _k else _npcs.get(npc)
        if isinstance(_rec, dict):
            out["appear"] = max(0, int(_rec.get("appear_count", 0) or 0))
    except Exception as e:
        logger.debug(f"[WorldBoard] sender record skip ({npc}): {e}")
    out["source"] = _sender_source(_rec)

    _depth_w = float(getattr(config, "BOARD_SENDER_DEPTH_BONUS", 0.40))
    _appear_w = float(getattr(config, "BOARD_SENDER_APPEAR_BONUS", 0.15))
    _sat = max(1, int(getattr(config, "BOARD_SENDER_APPEAR_SAT", 6)))
    _src_w = float(getattr(config, "BOARD_SENDER_SOURCE_BONUS", 0.10))
    _allowed = _allowed_sender_sources()

    bonus = _depth_w * min(1.0, out["strength"] / 100.0)
    bonus += _appear_w * min(1.0, out["appear"] / float(_sat))
    if _allowed is not None and out["source"] in _allowed:
        bonus += _src_w
    out["bonus"] = round(bonus, 4)
    return out


def _select_best_event(
    events: List[Dict[str, Any]],
    channel_id: str,
) -> Optional[Dict[str, Any]]:
    """이벤트 목록에서 최고 weight 1개 선택. 게이트 적용."""
    if not events:
        return None

    enabled = get_board_channels(channel_id)
    absent_npcs = _get_absent_npcs(channel_id)
    absent_set = set(absent_npcs)

    # 안전 상태 로드
    world = domain_manager.get_world_state(channel_id)
    board_state = world.get("world_board", {})
    npc_history = board_state.get("npc_post_history", [])
    recent_types = board_state.get("recent_event_types", [])

    # NPC 연속 포스트 감지 (최근 2건이 같은 NPC면 차단)
    blocked_npcs = set()
    if len(npc_history) >= 2:
        last_two = [h.get("npc") for h in npc_history[-2:]]
        if last_two[0] and last_two[0] == last_two[1]:
            blocked_npcs.add(last_two[0])

    # [2026-07-28] 이름 정규화 — absent_set/blocked_npcs는 domain 정규 키인데,
    # Scanner 1(npc_emotion_states 키)·2(dai npc_attitudes 키)가 넘기는 npc는 Theoria가 그 턴에
    # 쓴 **원시 표기**다. 표기가 어긋나면 실제 부재 NPC의 이벤트가 "부재 아님"으로 판정돼
    # 통째로 드롭되거나 message로 오분류됐다. (Scanner 5/6/10은 canonical 경유라 원래 안전.)
    _npc_map_canon = domain_manager.get_npcs(channel_id) or {}

    def _canon(n):
        if not n:
            return n
        try:
            return domain_manager._find_npc_key(_npc_map_canon, n) or n
        except Exception:
            return n

    candidates = []
    for ev in events:
        ch = ev.get("channel", "sns")
        npc = _canon(ev.get("npc"))
        if npc and npc != ev.get("npc"):
            ev["npc"] = npc          # 하류(게시·이력 기록)도 정규 이름을 쓰게
        ev_type = ev.get("type", "")
        weight = ev.get("weight", 0)

        # 채널 활성 필터
        if not enabled.get(ch, False):
            continue

        # NPC 쿨다운 필터
        if npc and npc in blocked_npcs:
            continue

        # 부재 필터: sns/bulletin은 부재 NPC만, message는 제한 없음
        if npc and ch != "message" and npc not in absent_set:
            continue

        # 반복 감쇠: 같은 type이 최근 2턴 내 → weight × 0.5
        if ev_type in recent_types[-2:]:
            weight *= 0.5

        # 최소 threshold
        if weight < 0.3:
            continue

        # [2026-08-17 발신자 긴밀화] 사적 매체(message)만 관계 가중.
        #   공지·SNS는 공적 매체라 무변경 — 낯선 이름이 정상인 자리다.
        #   가중은 threshold **뒤**에 얹는다: 관계가 이벤트의 최소 자격을 대신하지 않는다
        #   (관계 깊은 인물이 아무 사건 없이 편지를 보내지는 않는다).
        if ch == "message" and npc:
            aff = _sender_affinity(channel_id, npc)
            _min_depth = int(getattr(config, "BOARD_SENDER_MIN_DEPTH", 0) or 0)
            if _min_depth > 0 and aff["strength"] < _min_depth:   # [2026-09-24 §5-2 #13] 관계 강도
                logger.debug(
                    f"[WorldBoard] sender gate dropped {npc} "
                    f"(|depth|={aff['strength']} < {_min_depth})")
                continue
            weight += aff["bonus"]
            ev["_sender_affinity"] = aff

        candidates.append((weight, ev))

    if not candidates:
        return None

    candidates.sort(key=lambda x: x[0], reverse=True)
    best = candidates[0][1]
    _aff = best.get("_sender_affinity")
    if _aff:
        logger.info(
            "[WorldBoard] sender=%s depth=%s appear=%s src=%s bonus=%.2f",
            best.get("npc", "?"), _aff.get("depth"), _aff.get("appear"),
            _aff.get("source") or "?", _aff.get("bonus", 0.0))
    return best


def _route_event_channel(
    event: Dict[str, Any],
    enabled: Dict[str, bool],
    absent_npcs: List[str],
) -> Optional[str]:
    """이벤트의 최종 채널 결정. 비활성/부재 규칙 적용."""
    preferred = event.get("channel", "sns")
    npc = event.get("npc")
    absent_set = set(absent_npcs)

    # NPC가 장면에 있으면 message만 가능
    if npc and npc not in absent_set:
        if enabled.get("message"):
            return "message"
        return None

    # 기본 채널 활성이면 사용
    if enabled.get(preferred):
        return preferred

    # 폴백
    fallbacks = ["sns", "bulletin", "message"]
    for fb in fallbacks:
        if fb != preferred and enabled.get(fb):
            return fb

    return None


# =========================================================
# 생성 계약 (2026-08-17) — 작성자 정보 격리 + 매체 문법 분리
# =========================================================
# SNSGod 대조에서 가져온 건 문구가 아니라 **구조 두 개**다:
#   ① 매체마다 스키마·규칙을 따로 세운다(저쪽은 sns_posting / sns-comment / snsdm 프롬이
#      아예 다른 콜이다) — 우리는 한 콜이므로 계약 테이블을 채널종으로 갈라서, 라우팅된
#      종의 계약만 프롬프트에 실린다(교차 오염 0). 길이·격식·즉흥성이 종마다 다른 게 요지.
#   ② 이전 산출을 입력으로 넣어 "고정 문구 폴백"을 탐지·회피한다(저쪽 previousPosts +
#      "generic fallback diary line 금지"). 우리는 recent_summaries가 이미 들어가고 있었는데
#      계약이 "반복 금지"뿐이었다 — **이어쓰기**(같은 목소리의 나중 순간)까지 명시한다.
# ⛔기각: stats(views/likes/replies) 숫자 발명 — 수치를 LLM에 위임하지 않는다는 우리 규율 위반.
#   댓글 스레드도 기각(표시면·답글 루프 = 파이프라인 신설). 태그는 스키마 신설 없이
#   본문 안에서만, 그것도 그 세계에 그런 매체가 있을 때만.
# [2026-10-23 C70] 닫기만 막던 줄을 양쪽으로: 글은 새 사건(습격·죽음·도착·판결)을 열지 않고, 작성자 자기 삶의 말
#   (바람·부탁·제안·두려움·계획)만 보탠다 — 렌더가 핸드아웃을 읽고 배경 추출이 last_handouts 를 먹으니 지어낸
#   추신이 곧 세계 사실이 되던 자리. 이름은 자료가 준 대로(세력·관청·구역 신설 0). bulletin·sns 계약엔 전파
#   속도, sns엔 핸들 독립, message엔 "말 대신 쓰는 이유". 외부 대조 = CCS 머크넷·라이트보드(C70 스펙 부록).
_BOARD_AUTHORSHIP = """## AUTHORSHIP
The writer is a person inside this world, filing this alone. The brief above is the analysis
layer, not what the author knows — an author writes from what was seen, heard, or told, and a
post that holds the whole event is the narrator wearing someone's name.
Where the profile and the knowledge list are silent, the author was not there. An invented
witness is a different failure than an incomplete one.
Dialogue spoken in the scene belongs to the scene: quoted or paraphrased, the post is a relay.
The situation is still open when this is filed. What the brief carries lands at full weight;
past it, the author speaks from their own life: what they want, ask, offer, fear or plan is
theirs to write. What happened in the world reaches the post only as the brief and the material
above give it, so a raid, a death, an arrival or a verdict the world has not set stays out of
the author's mouth. Settling what is open, or telling the reader how to feel, is narration in a
post's clothing. Plans and orders in the record are things said; how they turn out is still
ahead of everyone.
Names the material above gives are used as given, doing what they are given to do, in the
vocabulary it speaks. A resident may mention in passing what any street holds (a stall, a
handle, where a rumor came from); a district, faction, office or house enters the world only
through that material.
No tallies — views, likes, replies, readers, dice, mechanics. A number the brief did not
supply was invented.
Write the post itself in Korean."""

# 매체 문법. **라우팅된 종의 것만** 프롬프트에 실린다.
_MEDIA_CONTRACT = {
    "bulletin": """Public notice, posted where strangers read it. The title carries the fact; the body
carries what is asked of whoever reads it. Signed by an office, a rank, or a duty before a
person — private feeling reaches the page only as what the notice declines to say.
Register stays fixed and impersonal even when the writer is not.
What happened out of public sight minutes ago reaches a notice as a sighting, a call for
witnesses, or not yet. 120-250자.""",
    "sns": """Personal feed, unedited, filed the moment the thought arrived. One thought, no structure,
no briefing for followers who already live here. The event may surface only sideways — a mood,
an aside, something adjacent; naming it whole turns the post into a press release.
If this world's medium marks posts with tags or sigils, they sit inside the body — none
invented for a world that has none.
Talk moves at this world's speed: what happened behind a closed door reaches a feed only through
someone who was inside, and what happened out of sight minutes ago arrives as a sighting, a
rumor, or not yet. A handle invented here was chosen long before today, from its owner's own
life. 40-140자.""",
    "message": """Private, to one named recipient who shares the writer's history. What the two already
know stays unexplained; an unglossed reference belongs here, and a sentence supplying
background is addressed to an audience this does not have.
A writer within speaking distance writes only where speech will not do: it must not be
overheard, must be kept, or cannot be said to a face. The note takes its shape from that reason.
Opening and signature follow this world's form for the named format. 100-220자.""",
}

# 출력 스키마도 같은 테이블에서 — 두 빌더가 각자 들고 있으면 드리프트한다.
# (구 board 빌더 쪽 문자열은 `{{`가 f-string 밖에서 쓰여 최종 프롬프트에 이중 중괄호로
#  샜다 — 여기로 합치면서 단일 중괄호로 교정. 길이는 스키마가 아니라 매체 계약이 쥔다.)
def mail_format_hint(channel_id: str) -> str:
    """선언된 **도착물 형식**(surface="mail") 이름 목록 한 줄. 없으면 "".

    [2026-09-07 P10] 유저가 "편지는 봉랍으로 닫는다"를 형식으로 선언해 놨는데 게시물
    생성 콜은 그 이름을 모른 채 `format_name` 을 매번 새로 지어냈다(≈889 자유 기입).
    이름만 알려 준다 — 본문 렌더는 여전히 작성자(모델)의 것이고, 코드는 봉투에 뭐라
    적혔는지만 맞춘다. 없으면 줄 자체가 빠진다(빈 지침은 노이즈다).
    """
    try:
        import status_panel as _sp
        names = [n for n in (_sp.mail_format_names(channel_id) or []) if str(n).strip()]
    except Exception as e:
        logger.debug(f"[WorldBoard] mail format hint skipped: {e}")
        return ""
    if not names:
        return ""
    return ("- Declared arrival formats (reuse one of these EXACT names for format_name "
            "when it fits): " + ", ".join(str(n) for n in names[:12]))


# =========================================================
# [2026-09-13 P14] 도착물 = 산문의 입력(핸드아웃). 방아쇠 둘 · 틀 하나.
# =========================================================
# 💌 개인(letter) = PC 앞으로 오는 것 / 📰 공개(bulletin·sns) = 세상에 걸리는 것.
# 선언 전이의 `deliver.kind` 는 여기서 채널종으로 번역된다 — 유저 어휘(개인/공개)와
# 게시판 내부 어휘(message/bulletin/sns) 사이의 **단 하나의 사전**이다.
DELIVER_CHANNEL = {"letter": "message", "bulletin": "bulletin", "sns": "sns"}

# 틀이 채워야 할 두 칸. 라우터 내부 표기지 유저 문법이 아니다(§2).
MAIL_SLOT_BODY = "{{본문}}"
MAIL_SLOT_SIGN = "{{서명}}"


def _declared_event(req: Dict[str, Any], final_channel: str) -> Dict[str, Any]:
    """선언(`deliver`) → 이벤트 dict. 이벤트 수집·선별을 건너뛰는 유일한 자리.

    `detail_kr` 이 곧 브리핑이다 — 프롬프트의 THIS TURN'S EVENT 칸에 그대로 앉는다.
    본문은 여기 없다: brief 는 "무엇에 대한 것인가" 한 문장이고 글은 콜이 쓴다.
    """
    brief = str(req.get("brief") or "").strip()
    who = str(req.get("from") or "").strip()
    return {
        "tag": "declared",
        "type": "declared",
        "weight": 99,
        "npc": who,
        "channel": final_channel,
        "detail_kr": brief or "(선언된 도착물 — 지금 이 장면에 닿는 것 하나)",
    }


def pick_arrival_request(channel_id: str, dai: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """이번 턴 도착물 요청 하나. 없으면 None. **턴당 1**, 겹치면 선언이 이긴다.

    ① 선언 전이 `deliver` — `expr_engine` 이 4.7 에서 적재해 둔 행(집으면 소멸).
    ② 분석 신호 `arrival` — 불리언 하나. "이 장면이 도착물을 부른다"는 신호일 뿐이라
       **종류도 내용도 내지 않는다**(그건 도착물 콜이 dai 이벤트·선언 형식 목록을 보고 고른다).
    둘이 같은 턴에 서면 ①이다: 규칙이 정한 때가 장면의 기색보다 구체적이다.
    """
    rows: List[Dict[str, Any]] = []
    try:
        import expr_engine as _ee
        rows = _ee.take_deliver_requests(channel_id)
    except Exception as e:
        logger.debug(f"[WorldBoard] deliver 픽업 skip: {e}")
        rows = []
    if rows:
        row = rows[0]
        dv = row.get("deliver") or {}
        if len(rows) > 1:
            logger.info("[WorldBoard] 같은 턴 도착물 선언 %d 건 — 첫 건만(턴당 1)", len(rows))
            # [2026-09-24 감사] 나머지는 버리지 않고 보류 알림으로 되돌린다(한 줄 알림으로 flush).
            #   take_deliver_requests 가 전부 비워 가므로, 예전엔 둘째 건부터 알림째 증발했다.
            _restore_pending_rows(channel_id, rows[1:])
        return {"source": "declared",
                "kind": str(dv.get("kind") or "letter"),
                "from": dv.get("from") or str(row.get("author") or "") or None,
                "brief": str(dv.get("brief") or row.get("body") or ""),
                "format_name": str(row.get("format_name") or ""),
                # [2026-09-24 감사] 원 행 — 핸드아웃이 무산되면 restore_arrival_request 가 되돌린다.
                "_row": row}
    if isinstance(dai, dict) and dai.get("arrival") is True:
        return {"source": "arrival", "kind": None, "from": None, "brief": "", "format_name": ""}
    return None


def _restore_pending_rows(channel_id: str, rows: List[Dict[str, Any]]) -> int:
    """[2026-09-24 감사] 집어 간 선언 행을 보류 알림(`pending_mails`)으로 되돌린다. 되돌린 수 반환.

    `deliver` 칸은 떼고 돌린다 — 되돌린 행은 **핸드아웃 재시도가 아니라** 종전 한 줄 알림이다
    (take_deliver_requests 의 약속: "집히지 않은 채 턴이 끝나면 flush 가 한 줄 알림으로").
    같은 턴 9.52 flush_mails 가 산문 메시지에 붙인다. 읽기→쓰기 사이 await 0(경합 창 없음).
    """
    back = []
    for r in rows or []:
        if isinstance(r, dict):
            if str(r.get("notify") or "") == "none":
                continue   # 선언이 알림을 끈 전이 — 한 줄 알림으로도 안 낸다
            rr = dict(r)
            rr.pop("deliver", None)
            rr.pop("notify", None)
            back.append(rr)
    if not back:
        return 0
    try:
        import expr_engine as _ee
        world = domain_manager.get_world_state(channel_id) or {}
        pend = world.get(_ee.PENDING_MAILS_KEY)
        pend = list(pend) if isinstance(pend, list) else []
        pend.extend(back)
        world[_ee.PENDING_MAILS_KEY] = pend
        domain_manager.update_world_state(channel_id, world)
        logger.info("[WorldBoard] 도착물 선언 %d 건 → 한 줄 알림으로 복귀", len(back))
        return len(back)
    except Exception as e:
        logger.warning(f"[WorldBoard] 도착물 선언 복귀 실패(알림 유실): {e}")
        return 0


def restore_arrival_request(channel_id: str, req: Optional[Dict[str, Any]]) -> bool:
    """[2026-09-24 감사] 핸드아웃이 무산된 선언 요청을 보류 알림으로 되돌린다(한 번만).

    `pick_arrival_request` 는 성공 확인 **전에** 행을 소비한다(take_deliver_requests). 콜이
    무산되면(게시판 모듈 off·채널 off·빈 산출·예외) 예전엔 알림째 증발했다. `_row` 를 pop 해서
    두 번 불려도(trigger_board_update 래퍼 + 호출부) 한 번만 돌린다. arrival 신호는 돌릴 행이 없다.
    """
    if not isinstance(req, dict) or req.get("source") != "declared":
        return False
    row = req.pop("_row", None)
    if not isinstance(row, dict):
        return False
    return _restore_pending_rows(channel_id, [row]) > 0


def mail_frame(channel_id: str, format_name: str) -> str:
    """선언된 mail 형식의 **틀**. 없으면 "".

    [2026-09-13 P14] P10 이 세운 `template` 을 도착물 본문에 처음 쓴다. 코드가 채울 수 있는
    칸(`[날짜]`·`[수신자]`·선언 변수)은 `render_placeholders` 가 먼저 채우고, 남는 건
    `{{본문}}`·`{{서명}}` 둘뿐이다 — "코드가 채울 수 있는 건 코드가, 나머지는 LLM".
    미해석 자리표시자는 원문 그대로 둔다(P10 계약 — 유저 저작을 코드가 지우지 않는다).
    """
    nm = str(format_name or "").strip()
    if not nm:
        return ""
    try:
        import status_panel as _sp
        if nm not in (_sp.mail_format_names(channel_id) or []):
            return ""
        tpl = next((t for n, t, _e in (_sp.list_template_formats(channel_id) or [])
                    if n == nm and str(t or "").strip()), "")
        blanks = next((e for n, _t, e in (_sp.list_template_formats(channel_id) or [])
                       if n == nm), {})
    except Exception as e:
        logger.debug(f"[WorldBoard] mail frame skip: {e}")
        return ""
    if not tpl:
        return ""
    try:
        import custom_vars as _cv
        return _cv.render_placeholders(channel_id, tpl, blanks)
    except Exception as e:
        logger.debug(f"[WorldBoard] frame render skip: {e}")
        return tpl


def handout_text(result: Optional[Dict[str, Any]]) -> str:
    """핸드아웃 블록 본문 — 산문 콜이 "편지를 읽은 채로" 시작하게 하는 재료 한 덩이.

    지시가 아니라 **종이**다: 인용도 답장도 강요하지 않는다(쪽지 큐와 같은 톤 규율).
    """
    if not isinstance(result, dict):
        return ""
    p = result.get("payload") or {}
    ch = str(result.get("channel_kind") or p.get("channel_kind") or "")
    body = str(p.get("body") or "").strip()
    if not body:
        return ""
    return f"{_handout_head(ch, p)}\n{body[:1200]}"


def _handout_head(ch: str, p: Dict[str, Any]) -> str:
    """도착물 머리 줄 `📰 제목 · 형식 — 작성자 → 수신자`. 핸드아웃 블록과 원문 행(C49 R1)이 같은 꼴을 쓴다."""
    head = "💌" if ch == "message" else "📰"
    title = str(p.get("title") or "").strip()
    author = str(p.get("author") or "").strip()
    recipient = str(p.get("recipient") or "").strip()
    fmt = str(p.get("format_name") or "").strip()
    bits = [b for b in (title, fmt) if b]
    line = f"{head} " + (" · ".join(bits) if bits else "도착물")
    if author and recipient:
        line += f" — {author} → {recipient}"
    elif author:
        line += f" — {author}"
    return line


# =========================================================
# [2026-10-17 C49] 도착물 원문 남기기 · 위키 연동
#   레티어스 "원문남기기 쪽으로 가서 해당 관련 위키에 언제 도착물이 왔었다 같은거 연동".
#   R1 원문 = 영구 로그(history_log) 한 행(역할 `도착물`). 작업 창(JSON 히스토리)엔 안 넣는다 —
#      렌더 히스토리·발효는 그대로, 이름으로 영구 로그를 뒤지는 독자 넷(C46 발췌·💭 record·회상 T1·루카 회상)이
#      지난 도착물을 찾게 된다.
#   R2 위키 = 관련 페이지 History 한 줄(C45 도장). 📰는 낸 쪽·실린 이름, 💌는 주고받은 두 사람만 —
#      개인 편지 본문에만 나온 제3자 페이지엔 안 쓴다(그 사람이 모르는 편지가 💭·시트로 새지 않게).
#   부르는 자리 = orchestration step 8, 입력 행 다음·산문 행 앞(실제 순서). 산문이 실패한 턴은 안 남는다.
# =========================================================
HANDOUT_ROLE = "도착물"
_WIKI_LINK_KINDS = ("character", "location", "faction", "item")


def _obj_josa(word: str) -> str:
    """을/를 — 마지막 글자 받침으로. 한글 음절이 아니면 '를'."""
    w = str(word or "").rstrip()
    if not w:
        return "를"
    c = ord(w[-1])
    if 0xAC00 <= c <= 0xD7A3 and (c - 0xAC00) % 28:
        return "을"
    return "를"


def handout_record(result: Optional[Dict[str, Any]], message_id: Any = None) -> Optional[Dict[str, Any]]:
    """표시가 끝난 도착물 → 기록 묶음(머리·본문 전문·공개 여부·이름 칸·message_id). 본문 없으면 None."""
    if not isinstance(result, dict):
        return None
    p = result.get("payload") or {}
    body = str(p.get("body") or "").strip()
    if not body:
        return None
    ch = str(result.get("channel_kind") or p.get("channel_kind") or "")
    label = str(p.get("title") or "").strip() or str(p.get("format_name") or "").strip() or "도착물"
    return {
        "head": _handout_head(ch, p),
        "body": body,
        "public": ch != "message",
        "icon": "💌" if ch == "message" else "📰",
        "label": label,
        "title": str(p.get("title") or "").strip(),
        "author": str(p.get("author") or "").strip(),
        "recipient": str(p.get("recipient") or "").strip(),
        "message_id": message_id,
    }


def _handout_wiki_targets(channel_id: str, record: Dict[str, Any]) -> List[Tuple[str, str]]:
    """관련 페이지 → [(page_id, 관계)]. 관계 = sent / recv / named. 순서 작성자 → 수신자 → 본문 첫 등장, 상한.

    매칭 = 페이지 이름·별칭(2자 이상)의 정규화 부분 일치. 두 페이지가 같은 키를 가지면 그 키는 버린다
    (`wiki_store.resolve_page` 모호 규칙과 같은 판정). 삭제 표식 페이지는 뺀다.
    """
    import wiki_store as _ws
    pages = [pg for pg in _ws.list_pages(channel_id)
             if pg.get("kind") in _WIKI_LINK_KINDS and str(pg.get("status") or "") != "deleted"]
    if not pages:
        return []
    owners: Dict[str, set] = {}
    for pg in _ws.list_pages(channel_id):
        if str(pg.get("status") or "") == "deleted":
            continue
        for k in [pg.get("name")] + list(pg.get("aliases") or []):
            n = _ws._norm_name(str(k or ""))
            if n:
                owners.setdefault(n, set()).add(pg["page_id"])
    author = _ws._norm_name(record.get("author") or "")
    recipient = _ws._norm_name(record.get("recipient") or "")
    text = _ws._norm_name(f"{record.get('title') or ''}\n{record.get('body') or ''}")
    sent, recv, named = [], [], []
    for pg in pages:
        keys = []
        for k in [pg.get("name")] + list(pg.get("aliases") or []):
            n = _ws._norm_name(str(k or ""))
            if len(n) >= 2 and len(owners.get(n, ())) == 1 and n not in keys:
                keys.append(n)
        if not keys:
            continue
        if author and any(k in author for k in keys):
            sent.append(pg["page_id"])
        elif recipient and any(k in recipient for k in keys):
            recv.append(pg["page_id"])
        elif record.get("public"):
            pos = [text.find(k) for k in keys if text.find(k) >= 0]
            if pos:
                named.append((min(pos), pg["page_id"]))
    named.sort(key=lambda x: x[0])
    out = [(pid, "sent") for pid in sent] + [(pid, "recv") for pid in recv] + [(pid, "named") for _p, pid in named]
    cap = int(getattr(config, "HANDOUT_WIKI_MAX_PAGES", 4) or 0)
    return out[:cap] if cap > 0 else []


def _handout_wiki_line(record: Dict[str, Any], rel: str, stamp: str) -> str:
    icon, label = record.get("icon") or "📰", record.get("label") or "도착물"
    obj = f"「{label}」{_obj_josa(label)}"
    if rel == "sent":
        if record.get("public"):
            body = f"{icon} {obj} 냈다"
        elif record.get("recipient"):
            body = f"{icon} {obj} {record['recipient']}에게 보냈다"
        else:
            body = f"{icon} {obj} 보냈다"
    elif rel == "recv":
        body = f"{icon} {obj} {record['author']}에게서 받았다" if record.get("author") else f"{icon} {obj} 받았다"
    else:
        body = f"{icon} 「{label}」에 이름이 실렸다"
    return f"{stamp} {body}" if stamp else body


def record_handout(channel_id: str, record: Optional[Dict[str, Any]], turn: Optional[int] = None) -> Dict[str, int]:
    """[C49 R1·R2] 원문 한 행(영구 로그) + 관련 위키 History 한 줄. 실패는 무해(그 칸만 0)."""
    out = {"log": 0, "wiki": 0}
    if not channel_id or not isinstance(record, dict) or not str(record.get("body") or "").strip():
        return out
    gt, t_now = None, 0
    try:
        gt, t_now = domain_manager.history_time_meta(channel_id)
    except Exception as e:
        logger.debug(f"[HandoutRecord] time meta skip: {e}")
    if turn is None:
        turn = t_now
    if getattr(config, "HANDOUT_LOG_RAW", True):
        try:
            if domain_manager.append_history_log(
                    channel_id, HANDOUT_ROLE, f"{record.get('head') or ''}\n{record['body']}".strip(),
                    message_id=record.get("message_id")):
                out["log"] = 1
        except Exception as e:
            logger.debug(f"[HandoutRecord] log skip: {e}")
    if getattr(config, "HANDOUT_WIKI_LINK", True):
        try:
            import wiki_store as _ws
            _t = int(turn) if isinstance(turn, int) and turn > 0 else None
            stamp = _ws._event_stamp(channel_id, [{"game_time": gt, "turn": _t}] if gt or _t else [], _t)
            for pid, rel in _handout_wiki_targets(channel_id, record):
                res = _ws.append_play_item(channel_id, pid, "History", _handout_wiki_line(record, rel, stamp),
                                           src_turn=_t, turn=_t, reason="handout")
                if isinstance(res, dict) and res.get("ok") and not res.get("dup"):
                    out["wiki"] += 1
        except Exception as e:
            logger.debug(f"[HandoutRecord] wiki skip: {e}")
    logger.info("[HandoutRecord] log=%d wiki=%d public=%s", out["log"], out["wiki"], record.get("public"))
    return out


_MEDIA_SCHEMA = {
    "bulletin": '"bulletin": [{"board_name": "게시판 이름(장르에 맞게)", "author": "작성자 이름/직함", "title": "제목", "body": "본문"}]',
    "sns": '"sns": [{"feed_name": "SNS 이름(장르에 맞게)", "author": "작성자", "body": "본문"}]',
    "message": '"message": [{"format_name": "형식 이름(장르에 맞게: 편지/쪽지/전보/마법통신 등)", "from": "발신자", "to": "수신자", "body": "본문"}]',
}

# =========================================================
# [2026-08-17] 이 사건이 만지는 세계 발췌 (장면 연관 로어)
# =========================================================
# 병: 게시물 생성 콜은 **세계를 거의 모른 채** 썼다. 구 `_build_board_prompt`에 "마지막 장면의
#   로어 재사용" 블록이 있었지만 청크 키를 `text`로 읽었고(정본은 `content`) 이벤트 경로에는
#   아예 없었다 — 즉 실제로 실린 세계 지식은 world_constraints 한 줄뿐이었다.
#   그 상태의 작성자는 지명·관습·내력을 **발명**하거나, 반대로 아무것도 안 딛고 붕 뜬다.
# 처방: 리더 부록과 **같은 검색층 진입점**(스크럽→랭킹)으로 이 사건에 닿는 청크만 발췌.
# 계약이 요지다 — 이 블록은 `_BOARD_AUTHORSHIP`의 정보 격리와 **충돌하지 않아야** 한다:
#   AUTHORSHIP은 "브리핑은 작성자의 지식이 아니다"(사건 축)를 말하고, 여기는 "발췌는
#   작성자가 아는 **공적 세계 지식**이지 이번 사건의 목격이 아니다"(세계 축)를 말한다.
#   둘은 같은 규율의 두 축이지 예외가 아니다. 그래서 문구도 목격/증언 어휘로 맞춰 쓴다.
# 인용 금지: 청크는 설정 문장이라 그대로 실으면 게시물이 사전 항목이 된다(리더 부록과 동일).
_BOARD_LORE_CONTRACT = """## WORLD REFERENCE (public knowledge — not this event)
Common ground: places, custom, standing arrangement — what a person here may already know before
anything happened today. None of it is testimony. Nothing below was witnessed, reported, or
confirmed by the author, and no entry describes the event being filed.
An entry the post does not need stays out; a phrase carried over word for word is a page recited,
not a person writing."""


async def _build_lore_reference(client, channel_id: str, query: str) -> str:
    """게시물 콜용 세계 발췌 블록(헤더 포함). 재료 없음·검색 실패·TOP_K=0 = ""(블록 생략).

    쿼리는 호출부가 정한다(이벤트 경로=`detail_kr` 브리핑 / 정기 경로=현재 장면 observation).
    비밀 스크럽은 진입점이 랭킹 **앞**에서 건다 — 게시물은 공개물이라 누출이 즉시 공표다.
    """
    if not client or not query or not str(query).strip():
        return ""
    try:
        _top_k = int(getattr(config, "BOARD_LORE_TOP_K", 2))
        if _top_k <= 0:
            return ""  # 손잡이 하나로 완전 비활성
        _cap = int(getattr(config, "BOARD_LORE_CHUNK_CHARS", 400))
        import vector_search as _vs
        ranked = await _vs.get_scrubbed_scene_chunks(
            client, channel_id, str(query)[:2000],
            top_k=_top_k, max_chars=_cap, tag="BoardLore",
        )
        body = _vs.format_chunk_lines(ranked)
        if not body:
            return ""
        logger.debug("[BoardLore] %d entries", len(ranked))
        return f"{_BOARD_LORE_CONTRACT}\n{body}"
    except Exception as e:
        logger.debug(f"[BoardLore] skip: {e}")
        return ""


# =========================================================
# [2026-10-17 C46 W2~W5] 도착물 재료 — 렌더와 같은 세계
# =========================================================
# 병(C46 지도 §5): 도착물 콜은 로어 발췌 2 × 400자 말고는 렌더 자료 존(!룰·위키·기억·PC)을 하나도 못 받았다.
# 처방: 조립이 남긴 슬롯(slot_manager 4.35, `ctx.handout_ref`) + 렌더가 고른 로어(비밀만 걷음) + 사건 검색 로어
#   (자르지 않음) + 원문 발췌(영구 로그 history_log — 임베딩으로 에피소드, 코드로 조각). ref 가 없으면(도착물 밖 경로)
#   이 블록들은 없고 옛 재료 그대로다.
_WORLD_RECORD_HEAD = """## WORLD RECORD (the table's record — the author holds only their share of it)
What the world holds and what has happened, kept so the post agrees with it. The author knows
what their profile, their knowledge list and their own place in these events give them; the
rest stands here so nothing in the post contradicts the world.
The player character's sheet is the table's file, not the town's: a public post knows them by
what has been seen of them, a letter by what its writer has lived beside."""
# [C70] 위 마지막 문장 = 렌더 text_resources "Who knows what"의 "Player-profile data ≠ public knowledge" 자매 문안.

_RECORD_EXCERPTS_HEAD = """## RECORD EXCERPTS (원문 — what was said and done then; the present may differ)
Lines from the table's session log, oldest first, kept for agreement and not the author's eyes.
They fix names, places and what happened; the post speaks in its author's words."""

_REF_SLOT_ORDER = (6, 7, 9, 11, 23, 31)
_REF_TRIM_ORDER = (9, 7, 6)          # 상한을 넘으면 이 순서로 뒤를 깎는다(로어는 상한 밖)
_RAW_ROLE = {"user": "PC 행동", "model": "서술", "Model": "서술"}   # [C49 R3] 라이브 산문 행 역할은 "Model"
_KR_JOSA_TAIL = "이가은는을를의에도로와과"


def _trim_tail(v: str, keep: int) -> str:
    """줄 단위로 뒤를 깎는다. 닫는 태그 줄(`</…>`)은 남긴다. 머리 한 줄만 남으면 통째로 뺀다."""
    if len(v) <= keep:
        return v
    lines = v.split("\n")
    close = lines.pop() if lines and lines[-1].startswith("</") else ""
    while lines and len("\n".join(lines + ([close] if close else []))) > keep:
        lines.pop()
    if len(lines) <= 1:
        return ""
    return "\n".join(lines + ([close] if close else []))


def _world_record_block(ref: Optional[Dict[str, Any]]) -> str:
    """[C46 W2] 조립이 남긴 자료 존 슬롯(PC·NPC/위키·기억·챕터·!룰·직전 꼬리) 한 블록. 없으면 ""."""
    slots = dict((ref or {}).get("slots") or {})
    parts = {i: str(slots.get(i) or "").strip() for i in _REF_SLOT_ORDER}

    def _total() -> int:
        live = [v for v in parts.values() if v]
        return sum(len(v) for v in live) + 2 * max(0, len(live) - 1)

    cap = int(getattr(config, "HANDOUT_REF_MAX_CHARS", 60000) or 0)
    if cap > 0:
        for i in _REF_TRIM_ORDER:
            over = _total() - cap
            if over <= 0:
                break
            if parts[i]:
                parts[i] = _trim_tail(parts[i], max(0, len(parts[i]) - over))
    body = "\n\n".join(parts[i] for i in _REF_SLOT_ORDER if parts[i])
    return f"{_WORLD_RECORD_HEAD}\n{body}" if body else ""


def _event_names(channel_id: str, event: Dict[str, Any]) -> List[str]:
    """[C46 W4] 원문을 이름으로 찾을 때 쓰는 이름 — 글쓴 NPC(대표 이름·별칭) + 카드의 이름들(연결 실체).
    PC 이름은 안 넣는다(거의 모든 행에 걸려 최근 행만 끌어온다)."""
    out: List[str] = []

    def _add(n: Any) -> None:
        n = str(n or "").strip()
        if len(n) >= 2 and n not in out:
            out.append(n)

    npc = event.get("npc")
    if npc:
        _add(npc)
        try:
            _all = domain_manager.get_npcs(channel_id) or {}
            _key = domain_manager._find_npc_key(_all, npc) or npc
            _add(_key)
            for _a in ((_all.get(_key) or {}).get("aliases") or [])[:4]:
                _add(_a)
        except Exception:
            pass
    for n in (event.get("names") or []):
        _add(n)
    return out[:6]


def _event_query(event: Dict[str, Any], names: List[str], pc_names: List[str]) -> str:
    """[C46 W3·W4] 검색 쿼리 — 카드면 이름·일·얽힌 것(라벨 뺌), 아니면 브리핑 원문. + 글쓴이(+편지면 PC)."""
    base = str(event.get("query") or "").strip() if event.get("card") else ""
    if not base:
        base = str(event.get("detail_kr") or "").strip()
    bits = [base] + list(names) + list(pc_names or [])
    return " ".join(b for b in bits if b)[:2000]


async def _handout_lore_block(client, channel_id: str, query: str, ref: Optional[Dict[str, Any]]) -> str:
    """[C46 W3] 렌더가 고른 청크(비밀만 걷음) + 사건 검색 청크. **자르지 않는다**(10-17 "로어는 자르지 말자")."""
    import vector_search as _vs
    picked: List[Any] = []
    try:
        _rc = [c for c in ((ref or {}).get("lore_chunks") or []) if c]
        if _rc:
            picked = list(_vs.scrub_secret_chunks(channel_id, _rc, tag="HandoutLore"))
    except Exception as e:
        logger.debug(f"[HandoutLore] render chunks skip: {e}")
        picked = []
    found: List[Any] = []
    try:
        _k = int(getattr(config, "HANDOUT_LORE_TOP_K", 4))
        if client and query and _k > 0:
            ranked = await _vs.get_scrubbed_scene_chunks(
                client, channel_id, str(query)[:2000], top_k=_k,
                max_chars=int(getattr(config, "HANDOUT_LORE_CHUNK_CHARS", 0) or 0), tag="HandoutLore")
            found = [c for c, _s in (ranked or [])]
    except Exception as e:
        logger.debug(f"[HandoutLore] search skip: {e}")
    seen = set()
    rows = []
    for c in picked + found:
        _key = _vs.chunk_text(c).strip()
        if not _key or _key in seen:
            continue
        seen.add(_key)
        rows.append((c, 0.0))
    body = _vs.format_chunk_lines(rows)
    return f"{_BOARD_LORE_CONTRACT}\n{body}" if body else ""


def _raw_terms(query: str, names: List[str]) -> List[str]:
    """조각을 자를 낱말 — 이름 먼저, 그다음 쿼리 낱말(끝 조사 하나 뗀 꼴도)."""
    terms: List[str] = []
    for n in names:
        n = str(n or "").strip()
        if len(n) >= 2 and n not in terms:
            terms.append(n)
    for w in re.findall(r"[가-힣A-Za-z0-9]{2,}", str(query or "")):
        for t in (w, w[:-1] if len(w) >= 3 and w[-1] in _KR_JOSA_TAIL else ""):
            if len(t) >= 2 and t not in terms:
                terms.append(t)
    return terms


def _raw_snippet(text: Any, terms: List[str], half: int, head_ok: bool) -> str:
    """걸린 자리 ±half 조각. 안 걸리면 head_ok 일 때만 앞 2·half 자."""
    t = str(text or "").strip()
    pos, hit = -1, ""
    for term in terms:
        i = t.find(term)
        if i >= 0 and (pos < 0 or i < pos):
            pos, hit = i, term
    if pos < 0:
        if not head_ok:
            return ""
        s2 = t[:2 * half]
        return _one_line(s2 + ("…" if len(t) > len(s2) else ""))
    a = max(0, pos - half)
    b = min(len(t), pos + len(hit) + half)
    return _one_line(("…" if a > 0 else "") + t[a:b] + ("…" if b < len(t) else ""))


def _one_line(s: str) -> str:
    """[2026-10-17 C49] 조각은 `- 라벨: …` 한 줄이다 — 산문 문단·도착물 머리 줄의 줄바꿈을 공백으로 접는다."""
    return " ".join(str(s or "").split())


def _fmt_raw_gt(gt: Any) -> str:
    """game_time → '(작중 1년 3월 5일 14:00) '. une_facade._recall_fmt_game_time 과 같은 꼴(그쪽 import 를 피해 여기 둔다)."""
    if not isinstance(gt, dict):
        return ""
    try:
        return (f"(작중 {gt.get('year', 1)}년 {gt.get('month', 1)}월 {gt.get('day', 1)}일 "
                f"{int(gt.get('hour', 0)):02d}:{int(gt.get('minute', 0)):02d}) ")
    except (TypeError, ValueError):
        return ""


async def _build_raw_excerpts(client, channel_id: str, query: str, names: List[str]) -> str:
    """[C46 W4] 원문 발췌 — 히스토리(작업 창)가 아니라 영구 로그(history_log)에서.

    ① 에피소드(임베딩): 발효 엔트리 요약을 쿼리와 맞춰(md5 캐시 — `fermentation.entry_vector_text`) 상위 N개,
       그 `from_msg_id`~`to_msg_id` 구간 원문 행.  ② 이름(코드): `search_history_log`(LIKE).
    ③ 조각(코드): 걸린 자리 ±HANDOUT_RAW_SNIPPET. 비밀 원장에 닿는 조각은 버린다(로어 스크럽과 같은 판정기).
       원장을 못 읽으면(None) 블록 통째 생략. 오래된 것 → 최신, 합계 HANDOUT_RAW_CHARS 까지 줄 단위.
    """
    import vector_search as _vs
    import sqlite_store as _ss
    refs = _vs.secret_refs(channel_id, tag="HandoutRaw")
    if refs is None:
        return ""
    half = int(getattr(config, "HANDOUT_RAW_SNIPPET", 150) or 150)
    cap = int(getattr(config, "HANDOUT_RAW_CHARS", 3000) or 0)
    if cap <= 0:
        return ""
    picked: List[Any] = []
    n_ep = 0
    try:
        _n = int(getattr(config, "HANDOUT_RAW_EPISODES", 3) or 0)
        if client and query and _n > 0:
            import fermentation as _fm
            entries = [e for e in (domain_manager.get_fermented_history(channel_id) or [])
                       if isinstance(e, dict) and e.get("from_msg_id") and e.get("to_msg_id")]
            ranked = await _fm.rank_entries_for_query(
                client, entries, query, _n, float(getattr(config, "HANDOUT_RAW_MIN_SCORE", 0.3)))
            _rows_cap = int(getattr(config, "HANDOUT_RAW_ROWS", 24) or 24)
            for e, _s in (ranked or []):
                span = _ss.read_history_span(channel_id, e.get("from_msg_id"), e.get("to_msg_id"),
                                             limit=_rows_cap) or []
                picked += [(r, True) for r in span]
                n_ep += 1
    except Exception as ex:
        logger.debug(f"[HandoutRaw] episodes skip: {ex}")
    try:
        for nm in names:
            for r in (_ss.search_history_log(channel_id, nm, limit=4) or []):
                picked.append((r, False))
    except Exception as ex:
        logger.debug(f"[HandoutRaw] names skip: {ex}")
    seen = set()
    rows: List[Any] = []
    for r, ep in picked:
        _key = str(r.get("message_id") or "") or str(r.get("content") or "")[:200]
        if _key in seen:
            continue
        seen.add(_key)
        rows.append((r, ep))

    def _ord(item):
        _mid = str(item[0].get("message_id") or "")
        return (0, int(_mid)) if _mid.isdigit() else (1, 0)

    rows.sort(key=_ord)
    terms = _raw_terms(query, names)
    lines: List[str] = []
    used = 0
    dropped = 0
    for r, ep in rows:
        snip = _raw_snippet(r.get("content"), terms, half, head_ok=ep)
        if not snip.strip():
            continue
        _it = {"note": snip, "quote": snip}
        if any(_vs.secret_touched(_it, _tr, _sf) for _tr, _sf in refs):
            dropped += 1
            continue
        _role = _RAW_ROLE.get(str(r.get("role") or ""), str(r.get("role") or ""))
        line = f"- {_fmt_raw_gt(r.get('game_time'))}{_role}: {snip}"
        if used + len(line) + 1 > cap:
            break
        lines.append(line)
        used += len(line) + 1
    logger.info("[HandoutRaw] episodes=%d rows=%d lines=%d secret_dropped=%d chars=%d",
                n_ep, len(rows), len(lines), dropped, used)
    return (f"{_RECORD_EXCERPTS_HEAD}\n" + "\n".join(lines)) if lines else ""


async def _build_board_prompt(
    client,
    channel_id: str,
    active_channels: Dict[str, bool],
    trigger: str = "time",
    extra_context: str = "",
    max_posts: int = 1,
    absent_npcs: Optional[List[str]] = None,
) -> str:
    """활성 채널에 맞는 게시물 생성 프롬프트."""
    world = domain_manager.get_world_state(channel_id)
    # V8.5: 캘린더 마이그레이션
    try:
        import game_world as _gw
        _gw._init_clock(world)
    except Exception:
        pass
    location = world.get("current_location", "Unknown")
    year = world.get("year", 1)
    month = world.get("month", 1)
    day = world.get("day", 1)
    time_slot = world.get("time_slot", "오후")
    hour = world.get("hour", 12)
    minute = world.get("minute", 0)
    weather = world.get("weather", "맑음")
    doom = world.get("doom", 0)

    # 장르 정보
    stage, atmosphere = _genre_labels(channel_id)

    # 세계 규칙
    wc = world.get("world_constraints", {})
    constraints_text = ""
    if wc:
        parts = []
        if wc.get("systems"):
            parts.append(f"체계: {wc['systems']}")
        if wc.get("social"):
            parts.append(f"사회: {wc['social']}")
        if parts:
            constraints_text = " | ".join(parts)

    # 추가 규칙 (!룰 추가)
    active_rules_parts = []
    _rules_text = world.get("rules_text", "")
    if _rules_text:
        active_rules_parts.append(_rules_text)
    _loc_rules = world.get("location_rules", {})
    if _loc_rules:
        for k, v in _loc_rules.items():
            desc = v.get("desc", "") if isinstance(v, dict) else str(v)
            active_rules_parts.append(f"- {k}: {desc}")
    active_rules_text = "\n".join(active_rules_parts)

    # NPC 프로필: 부재 NPC 우선, 없으면 전체
    _npc_keys = absent_npcs if absent_npcs else list((domain_manager.get_npcs(channel_id) or {}).keys())[:10]
    all_npcs = domain_manager.get_npcs(channel_id) or {}
    npc_profiles = []
    for _nk in _npc_keys:
        _nd = all_npcs.get(_nk) or all_npcs.get(domain_manager._find_npc_key(all_npcs, _nk) or "", {})
        desc = _nd.get("description") or _nd.get("desc", "")
        meta = []
        if _nd.get("role"): meta.append(f"역할: {_nd['role']}")
        if _nd.get("personality"): meta.append(f"성격: {_nd['personality']}")
        if _nd.get("tone") or _nd.get("speech"): meta.append(f"말투: {_nd.get('tone') or _nd.get('speech')}")
        if _nd.get("location"): meta.append(f"위치: {_nd['location']}")
        profile = f"### {_nk}\n"
        if meta: profile += " | ".join(meta) + "\n"
        if desc: profile += desc
        npc_profiles.append(profile)
    npc_names = _npc_keys  # 기존 호환
    npc_section = "\n\n".join(npc_profiles) if npc_profiles else "None"

    # 서사 앵커링: 최신 DAI observation
    frame = domain_manager.get_latest_frame(channel_id)
    dai_snap = frame.get("dai_snapshot", {})
    observation = dai_snap.get("observation", "")

    # [2026-08-17] 세계 발췌 — 이벤트 빌더와 **같은 함수·같은 계약**(드리프트 0).
    #   구 블록은 "마지막 Theoria가 고른 청크 재사용(콜 0)"이었으나 청크를 `chunk["text"]`로
    #   읽었다 — 정본 키는 `content`(command_handler 청커)라 **항상 빈 문자열**이었고,
    #   결과적으로 이 프롬프트에 로어가 실린 적이 없다(죽은 읽기). 살아 있는 경로로 교체.
    #   쿼리는 이 빌더가 아는 "지금 장면" = observation. 없으면 블록 생략.
    lore_section = await _build_lore_reference(client, channel_id, observation)

    # 스토리텔러 최근 이벤트
    storyteller = world.get("storyteller", {})
    recent_tags = storyteller.get("recent_tags", [])

    # 중복 방지: 최근 게시물 요약
    recent_summaries = _get_recent_post_summaries(channel_id)

    # 핸들 레지스트리: NPC별 기존 계정 정보
    handle_registry = _get_handle_registry(channel_id)

    # 기존 게시물 수
    board_state = world.get("world_board", {})
    post_count = board_state.get("total_posts", 0)

    # 활성 채널별 태스크 구성
    task_parts = []
    task_num = 1
    output_fields = []

    post_label = f"1-{max_posts} posts" if max_posts > 1 else "1 post"

    # 매체 계약은 활성 채널종의 것만 실린다 — 세 종을 한 번에 켜면 셋이 나란히 서지만,
    # 꺼진 종의 문법은 프롬프트에 존재하지 않는다(이벤트 경로는 항상 1종).
    if active_channels.get("bulletin"):
        task_parts.append(f"""{task_num}. **bulletin** — {post_label}, each by a DIFFERENT office, role, or person in the world.
{_MEDIA_CONTRACT['bulletin']}""")
        output_fields.append("  " + _MEDIA_SCHEMA["bulletin"])
        task_num += 1

    if active_channels.get("sns"):
        task_parts.append(f"""{task_num}. **sns** — {post_label}, each by a DIFFERENT NPC or anonymous account.
{_MEDIA_CONTRACT['sns']}""")
        output_fields.append("  " + _MEDIA_SCHEMA["sns"])
        task_num += 1

    if active_channels.get("message"):
        task_parts.append(f"""{task_num}. **message** — {post_label}, each FROM a DIFFERENT sender TO one named recipient.
{_MEDIA_CONTRACT['message']}""")
        output_fields.append("  " + _MEDIA_SCHEMA["message"])
        _fmt_hint = mail_format_hint(channel_id)
        if _fmt_hint:
            task_parts.append(_fmt_hint)     # [2026-09-07 P10] 선언된 도착물 형식 이름 1줄

    task_text = "\n\n".join(task_parts)
    output_text = ",\n".join(output_fields)

    # 서사 앵커링 + 중복 방지 + 핸들 레지스트리 섹션 구성
    context_lines = []
    if observation:
        context_lines.append(f"- Current Scene: {observation}")
    if recent_summaries:
        context_lines.append(
            f"- Already posted (same world, earlier; the next post is a later moment in the "
            f"same voice, not a restart: a subject already posted returns only carrying what "
            f"has changed since): "
            f"{' / '.join(recent_summaries)}")
    if handle_registry:
        reg_parts = [f"{name}: {info}" for name, info in handle_registry.items()]
        context_lines.append(f"- Known Accounts (reuse EXACT names): {' | '.join(reg_parts)}")
    context_section = "\n".join(context_lines)

    return f"""You are a world content generator for a fictional TTRPG (tabletop role-playing game) session.
All characters, locations, and events are entirely fictional. Content serves narrative immersion for the game world.
Rating: NC-21+. NPC posts reflect their FULL personality — crude language, dark humor, flirtation, hostility, violence — as documented in their profiles. Do not sanitize.

## WORLD INFO
- Genre/Setting: {stage}
- Atmosphere: {atmosphere}
- Location: {location}
- Time: Year {year}, Month {month}, Day {day}, {hour:02d}:{minute:02d} ({time_slot})
- Weather: {weather}
- Doom Level: {doom}%
- World Rules: {constraints_text or 'None'}
{f'- Active Rules:{chr(10)}{active_rules_text}' if active_rules_text else ''}
{f'{chr(10)}{lore_section}' if lore_section else ''}

## AVAILABLE NPCs (off-screen)
{npc_section}
- Recent Events: {', '.join(recent_tags[-3:]) or 'None'}
- Trigger: {trigger}
{f'- Extra: {extra_context}' if extra_context else ''}
- Posts so far: {post_count}
{context_section}

## TASK
Generate content for the following channels in this world:

{task_text}

## RULES
- Match the world's genre and atmosphere. Time-appropriate — a dawn post is not a night post.
- ONLY use NPCs from the AVAILABLE NPCs section above. Match their personality and speech style.
- Length and register are set by each channel's medium above, not shared across them.

{_BOARD_AUTHORSHIP}

## OUTPUT FORMAT (JSON)
```json
{{
{output_text}
}}
```"""


async def generate_posts(
    client,
    model_id: str,
    channel_id: str,
    active_channels: Dict[str, bool],
    trigger: str = "time",
    extra_context: str = "",
    max_posts: int = 1,
    absent_npcs: Optional[List[str]] = None,
    override_prompt: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """Flash API로 게시물 생성. 3턴 프리필 + CONTENT_AUTHORIZATION."""
    from memory_system import api_call_with_retry
    from google.genai import types
    import text_resources

    prompt = override_prompt or await _build_board_prompt(
        client, channel_id, active_channels, trigger, extra_context, max_posts, absent_npcs)

    cfg = types.GenerateContentConfig(
        system_instruction=text_resources.ANALYSIS_FRAME,   # [2026-10-06 분석 틀] 원본 mandate → 분석 틀
        temperature=0.9,
        # [2026-10-11 C27b] 2048 → 8192. 추론 켠 분석 콜은 추론이 출력 예산에 같이 든다 — 상한 통일(레티어스 "나머지도 다 올리자").
        max_output_tokens=8192,
        response_mime_type="application/json",
        safety_settings=config.SAFETY_SETTINGS,
    )
    contents = [
        # [2026-10-06 분석 틀] 렌더 훈련 대화·확인 응답 삭제 — 작업 한 줄은 프롬프트 머리로.
        types.Content(role="user", parts=[types.Part(text="Generate world board content. Output JSON only.\n\n" + prompt)]),
    ]

    try:
        result = await api_call_with_retry(
            client, model_id, contents, cfg,
            operation_name="WorldBoard",
        )
        if not result:
            return None

        cleaned = bot_utils.clean_json_text(result)
        try:
            data = json.loads(cleaned)
        except json.JSONDecodeError:
            repaired = bot_utils.repair_json(cleaned)
            data = json.loads(repaired)

        return data
    except Exception as e:
        logger.error(f"[WorldBoard] Generation failed: {e}")
        return None


# =========================================================
# Event-Driven Prompt Builder (v2)
# =========================================================

# digest 라벨 → POSTING NPC 섹션의 기존 한국어 라벨. 구조화 필드 두 개만 대응이 있고
# (`tone`→말투 / `personality`→성격), 시트 섹션에서 온 조각은 제 섹션 이름을 그대로 쓴다.
# 형식(`k: v`)은 양쪽이 같아서 라벨만 갈아 끼우면 섹션 모양이 바뀌지 않는다.
_VOICE_LABEL_KR = {"speech": "말투", "core": "성격"}


def _voice_meta(npc_data: Dict[str, Any], npc_name: str, channel_id: Optional[str] = None) -> List[str]:
    """시트 → POSTING NPC 메타 조각 목록. 재료 없음·digest 불가 = [] (줄 생략).

    `npc_manager.build_voice_digest`(은닉 3종 방어 내장)의 반환을 기존 섹션 형식에 맞춰
    라벨만 한국어로 되돌린다. 실패는 **빈 목록**이지 옛 경로 폴백이 아니다 — 폴백하면
    digest 를 부르는 이유(은닉 방어)가 예외 한 번에 사라진다.
    """
    try:
        import npc_manager as _npm
        frags = _npm.build_voice_digest(npc_data, npc_name, channel_id=channel_id)   # [시트 2차b] 페이지 lore 절 직접
    except Exception as e:
        logger.debug(f"[WorldBoard] voice digest skipped ({npc_name}): {e}")
        return []
    out: List[str] = []
    for f in frags or []:
        _lab, _, _val = str(f).partition(": ")
        if not _val:
            out.append(str(f))
            continue
        out.append(f"{_VOICE_LABEL_KR.get(_lab.strip().lower(), _lab)}: {_val}")
    return out


def _scrub_knows(channel_id: str, knows: List[str]) -> List[str]:
    """`알고 있는 것` 조각을 비밀 원장과 대조해 **닿은 조각만** 떨군다. 원장 불가 = [].

    판정기·임계는 로어 청크 스크럽·속마음 시트와 공용(`vector_search.secret_touched`) —
    같은 질문을 같은 임계로 묻는다. 조각 텍스트를 note·quote 두 축에 동시 투입하는 것도
    `scrub_secret_chunks`와 같다(원장 truth 는 ENGLISH-ONLY라 영문 축이 주, 한글 축은 안전망).
    """
    if not knows:
        return []
    try:
        import vector_search as _vs
        refs = _vs.secret_refs(channel_id, tag="WorldBoard")
    except Exception as e:
        logger.debug(f"[WorldBoard] knows scrub unavailable — knows omitted: {e}")
        return []
    if refs is None:
        return []
    if not refs:
        return list(knows)
    try:
        kept = [k for k in knows
                if not any(_vs.secret_touched({"note": k, "quote": k}, _tr, _sf)
                           for _tr, _sf in refs)]
    except Exception as e:
        logger.debug(f"[WorldBoard] knows scrub failed — knows omitted: {e}")
        return []
    if len(kept) != len(knows):
        logger.debug("[WorldBoard] knows scrub dropped %d/%d fragments",
                     len(knows) - len(kept), len(knows))
    return kept


async def _build_event_prompt(
    client,
    channel_id: str,
    event: Dict[str, Any],
    channel_type: str,
    frame: str = "",
    ref: Optional[Dict[str, Any]] = None,
) -> str:
    """이벤트 맞춤 프롬프트. 기존 대비 ~50% 축소.

    [2026-09-13 P14] `frame` = 선언된 편지 틀(코드 칸이 이미 채워진 것). 있으면 스키마 대신
      "이 틀의 두 칸만 써라"가 된다 — 봉투·머리·서식은 유저 저작이고 글만 모델 몫이다.
    """
    world = domain_manager.get_world_state(channel_id)
    try:
        import game_world as _gw
        _gw._init_clock(world)
    except Exception:
        pass
    location = world.get("current_location", "Unknown")
    year = world.get("year", 1)
    month = world.get("month", 1)
    day = world.get("day", 1)
    hour = world.get("hour", 12)
    minute = world.get("minute", 0)
    time_slot = world.get("time_slot", "오후")

    stage, atmosphere = _genre_labels(channel_id)

    # NPC 프로필 (1명만)
    npc_name = event.get("npc") or ""
    npc_section = ""
    if npc_name:
        all_npcs = domain_manager.get_npcs(channel_id) or {}
        npc_key = domain_manager._find_npc_key(all_npcs, npc_name) or npc_name
        npc_data = all_npcs.get(npc_key, {})
        meta = []
        if npc_data.get("role"):
            meta.append(f"역할: {npc_data['role']}")
        # [2026-08-18 §3] 말투·성격 수동 증류 → `npc_manager.build_voice_digest` 재사용.
        #   구 코드는 tone/personality 두 필드를 날것으로 실었다 — 그 두 필드가 은닉 계열
        #   키였거나(RENDERER_STRIP_KEYS) 시트 본문에 `[Secret]`이 섞여 있어도 아무도 안 봤다.
        #   digest 는 은닉 3종(v6 `### Secrets` 섹션·`[Secret]` 마커·비밀 필드)을 애초에 안
        #   담고, `{{char}}` 플레이스홀더 치환·조각 캡까지 한다. 여기서 바뀌는 건 **재료
        #   원천뿐**이고 섹션 형식(`k: v`를 ` | `로 이은 한 줄)은 그대로다.
        #   빈 반환이면 구 코드와 같이 줄(조각)이 아예 없다.
        meta.extend(_voice_meta(npc_data, npc_name, channel_id))
        npc_section = f"Name: {npc_name}\n" + " | ".join(meta) if meta else f"Name: {npc_name}"

        # NPC 태도/감정
        att = domain_manager.get_npc_attitude(channel_id, npc_key)
        # [2026-08-18 §1] 사적 매체(message)에 한해 관계 **수치**를 동반한다.
        #   공지·SNS는 공적 매체라 무변경 — PC와의 depth/tension 은 거기서 무관하고,
        #   실으면 "누구에게 쓰는 글인가"를 흐린다(발신 게이트가 message 한정인 것과 같은 이치).
        #   형식은 속마음 `toward_pc`와 같다: 라벨=값 나열. **수치는 주되 지시하지 않는다**
        #   (연출·환산은 이 재료의 일이 아니다).
        #   depth 는 게이트가 이미 계산해 이벤트에 실어 둔 `_sender_affinity`를 재사용한다
        #   — 재조회 0. 없는 경로(선호 채널이 sns 였다가 온스테이지 라우팅으로 message 가
        #   된 이벤트)만 태도 dict 에서 읽는다. tension 은 게이트가 안 쓰는 축이라 늘 dict.
        _att_bits = []
        if isinstance(att, dict) and att.get("attitude"):
            _a = str(att["attitude"]).strip()
            if _a and _a.lower() != "null":
                _att_bits.append(_a)
        if channel_type == "message" and isinstance(att, dict):
            # [2026-09-25 관계 정성] depth=/tension= 숫자 → 말(방향·마찰). 태도 이름은 위에서 이미 실었다.
            try:
                _rw = domain_manager.relation_words(att, with_attitude=False)
            except Exception:
                _rw = ""
            if _rw:
                _att_bits.append(_rw)
        if _att_bits:
            npc_section += f"\n태도: {', '.join(_att_bits)}"

        # [2026-09-15 §12] 부재 감쇠 항목 제외 — 추적기 병합 이전과 같은 집합만(출력 무변경).
        from emotion_engine import present_emotion_states as _present_emo
        emo = _present_emo(world, world.get("turn_index")).get(npc_key, {})
        # pair 스키마 v2: 'dominant' 제거 → base_label/modifier_label (full EmotionState.to_dict())
        if isinstance(emo, dict) and emo.get("base_label"):
            _base = emo.get("base_label", "")
            _mod = emo.get("modifier_label", "")
            _emo_text = f"{_base} × {_mod}" if _mod else _base
            npc_section += f" | 감정: {_emo_text}"

        # NPC 지식 — [2026-08-18 §2] 원장 대조 스크럽. 게시물은 **공개물**이라 누출이 즉시
        #   공표고(게다가 recent_summaries·handle_registry 를 타고 다음 턴 재료로 굳는다),
        #   `knows`는 유일하게 무검증으로 실리던 재료였다. 판정기·임계는 로어 스크럽·속마음
        #   시트와 **같다**(`vector_search.secret_touched`, 내용어 3 / bigram 3 / 포함).
        #   조각 단위 드롭 — 한 항목이 비밀에 닿았다고 지식을 통째로 잃으면 결손이다.
        #   원장을 못 읽으면(refs None) knows 전량 생략(안전측). 전 채널 공통: 유출 경로는
        #   공지·SNS도 같다(오히려 더 넓다).
        knowledge = domain_manager.get_npc_knowledge_for(channel_id, npc_key)
        if isinstance(knowledge, dict):
            knows = [str(k).strip() for k in (knowledge.get("knows") or []) if str(k).strip()]
            knows = _scrub_knows(channel_id, knows)
            if knows:
                npc_section += f"\n알고 있는 것: {'; '.join(knows[-5:])}"

    # 중복 방지
    recent_summaries = _get_recent_post_summaries(channel_id)
    recent_str = " / ".join(recent_summaries) if recent_summaries else "없음"

    # 핸들 레지스트리
    handle_registry = _get_handle_registry(channel_id)
    handle_str = ""
    if handle_registry:
        reg_parts = [f"{name}: {info}" for name, info in handle_registry.items()]
        handle_str = f"\n## KNOWN ACCOUNTS (reuse EXACT names)\n{' | '.join(reg_parts)}"

    # 채널별 계약·스키마 — **라우팅된 종 하나만** 실린다(다른 매체의 문법은 이 프롬프트에 없다).
    media_contract = _MEDIA_CONTRACT.get(channel_type, _MEDIA_CONTRACT["sns"])
    json_schema = _MEDIA_SCHEMA.get(channel_type, _MEDIA_SCHEMA["sns"])
    _fmt_hint = mail_format_hint(channel_id)
    if _fmt_hint:
        media_contract = f"{media_contract}\n{_fmt_hint}"

    # [2026-08-17] 이 사건이 만지는 세계 발췌. 쿼리 = 이벤트 브리핑 원문(detail_kr).
    #   자리는 **이벤트 앞** — 자료(공적 세계 지식)가 먼저 서고 이번 사건이 뒤에 온다
    #   (openai 3분할 조립의 자료→THIS TURN 순서와 같은 이치). 없으면 통째로 빠진다.
    # [2026-10-17 C46 W2~W5] 도착물(산문 앞) 길은 렌더와 같은 세계를 본다 — 자료 존 슬롯 + 렌더가 고른 로어(비밀 걷음)
    #   + 사건 검색 로어(자르지 않음) + 원문 발췌. ref 가 없거나(도착물 밖 경로) 플래그가 꺼지면 옛 재료(발췌 2×400).
    record_block = raw_block = ""
    if isinstance(ref, dict) and bool(getattr(config, "HANDOUT_WORLD_REF", True)):
        _names = _event_names(channel_id, event)
        _pcs = list(ref.get("pc_names") or []) if channel_type == "message" else []
        _q = _event_query(event, _names, _pcs)
        record_block = _world_record_block(ref)
        lore_block = await _handout_lore_block(client, channel_id, _q, ref)
        raw_block = await _build_raw_excerpts(client, channel_id, _q, _names)
        logger.info("[Handout] ref=%d자 lore=%d자 raw=%d자", len(record_block), len(lore_block), len(raw_block))
    else:
        lore_block = await _build_lore_reference(client, channel_id, event.get("detail_kr", ""))
    _data_blocks = "".join(f"\n{b}\n" for b in (record_block, lore_block, raw_block) if b)
    _card_task = ("\n" + CARD_TASK_LINE) if event.get("card") else ""

    prompt = f"""You are a world content generator for a fictional TTRPG session.
All characters and events are entirely fictional. Rating: NC-21+.
NPC posts reflect their FULL personality — crude language, dark humor, flirtation, hostility as documented.

## CONTEXT
Genre: {stage} | Location: {location} | Time: Y{year}/M{month}/D{day} {hour:02d}:{minute:02d} ({time_slot}) | Mood: {atmosphere}
{_data_blocks}
## THIS TURN'S EVENT (brief — analysis layer, not the author's knowledge)
{event.get("detail_kr", "")}

## POSTING NPC
{npc_section or "(세계 이벤트 — 적절한 NPC 또는 익명 작성자를 선택하라)"}
{handle_str}

## ALREADY POSTED (same world, earlier)
{recent_str}
These exist. The next post is a later moment in the same voice, not a restart: a subject already
posted returns only carrying what has changed since, and a thread this author opened earlier may
be continued.

## MEDIUM — {channel_type}
{media_contract}

{_BOARD_AUTHORSHIP}

## TASK
One post, written because of the event above and for no other reason.{_card_task}

## OUTPUT (JSON)
```json
{{{json_schema}}}
```{_frame_block(frame)}"""
    return prompt


# =========================================================
# Discord Posting
# =========================================================

def _frame_block(frame: str) -> str:
    """[2026-09-13 P14] 틀 블록. 틀이 없으면 "" — 종전 자유 본문 그대로다."""
    f = str(frame or "").strip()
    if not f:
        return ""
    return f"""

## FORM (the declared stationery — already filled in where the code could)
{f}
Write ONLY the two slots: {MAIL_SLOT_BODY} and {MAIL_SLOT_SIGN}. Your "body" field is the whole
form above with those two slots replaced — every other character stays exactly as printed,
including labels, brackets, punctuation and line breaks. Nothing is added outside the form."""


def _get_time_footer(channel_id: str) -> tuple:
    """공통 시간 정보 반환. V8.5: year/month 포함 마이그레이션 보장."""
    world = domain_manager.get_world_state(channel_id)
    # V8.5: 캘린더 마이그레이션 (day=N → year/month/day_in_month 자동)
    try:
        import game_world
        game_world._init_clock(world)
    except Exception:
        pass
    year = world.get("year", 1)
    month = world.get("month", 1)
    day = world.get("day", 1)
    hour = world.get("hour", 12)
    minute = world.get("minute", 0)
    time_slot = world.get("time_slot", "")
    return year, month, day, hour, minute, time_slot


async def _post_bulletin(
    thread: discord.Thread,
    post: Dict[str, Any],
    channel_id: str,
) -> None:
    """게시판 스레드에 Embed 게시."""
    year, month, day, hour, minute, time_slot = _get_time_footer(channel_id)
    embed = discord.Embed(
        title=post.get("title", ""),
        description=post.get("body", ""),
        color=0x2F3136,
    )
    embed.set_author(name=post.get("author", "익명"))
    embed.set_footer(text=f"{year}년 {month}월 {day}일 {hour:02d}:{minute:02d} ({time_slot})")
    await thread.send(embed=embed)


async def _post_sns(
    thread: discord.Thread,
    post: Dict[str, Any],
    channel_id: str,
) -> None:
    """SNS 스레드에 Embed 게시."""
    year, month, day, hour, minute, _ = _get_time_footer(channel_id)
    embed = discord.Embed(
        description=post.get("body", ""),
        color=0x5865F2,
    )
    embed.set_author(name=post.get("author", "익명"))
    embed.set_footer(text=f"{year}년 {month}월 {day}일 · {hour:02d}:{minute:02d}")
    await thread.send(embed=embed)


async def _post_message(
    thread: discord.Thread,
    post: Dict[str, Any],
    channel_id: str,
) -> None:
    """메시지 스레드에 Embed 게시."""
    year, month, day, hour, minute, time_slot = _get_time_footer(channel_id)
    sender = post.get("from", "익명")
    receiver = post.get("to", "???")
    embed = discord.Embed(
        description=post.get("body", ""),
        color=0xED4245,
    )
    embed.set_author(name=f"{sender} → {receiver}")
    fmt_name = post.get("format_name", "")
    footer_parts = [f"{year}년 {month}월 {day}일 {hour:02d}:{minute:02d}"]
    if fmt_name:
        footer_parts.append(fmt_name)
    embed.set_footer(text=" · ".join(footer_parts))
    await thread.send(embed=embed)


# =========================================================
# Main Trigger Entry Point
# =========================================================

def _mail_payload(post: Dict[str, Any], ch_name: str) -> Dict[str, Any]:
    """[2026-08-16 도착물 라우트] 게시물 dict → turn_mail payload (표시층 공용 평면형).

    채널종마다 이름이 다른 필드(author/from·to/board_name·feed_name·format_name)를
    여기서 한 번만 흡수한다 — 표시(turn_mail._mail_embed)는 평면 키만 안다.
    """
    label = (post.get("format_name") or post.get("board_name")
             or post.get("feed_name") or "")
    return {
        "channel_kind": ch_name,
        "title": str(post.get("title") or "")[:250],
        "body": str(post.get("body") or "")[:4000],
        "author": str(post.get("author") or post.get("from") or "")[:200],
        "recipient": str(post.get("to") or "")[:200],
        "format_name": str(label or "")[:200],
    }




# =========================================================
# [2026-09-13 P14] 착지 뒤 정산 둘 — 종전 꼬리를 함수로. 핸드아웃(declared)과 착지 경로가
#   **같은 코드**를 타야 한다: 세계가 자기가 쓴 걸 기억하는 자리(중복 방지·핸들·쪽지 큐)를
#   한쪽만 타면 그 경로의 글만 매번 같은 말을 다시 쓴다.
# =========================================================

def _queue_world_mail(channel_id: str, final_channel: str, items: List[Any],
                      best_event: Dict[str, Any], current_turn: int) -> None:
    """[2026-08-17 쪽지 서사 접지] 사적 도착물 → 1턴 큐(다음 턴 좌뇌 서사 콜 재료).

    공지·SNS는 공적 매체라 제외 — "나한테 온 것"이 아니면 답장하지 않은 편지도 아니다.
    """
    if final_channel != "message":
        return
    try:
        import narrative_queries as _nq_mail
        _post0 = next((p for p in items if isinstance(p, dict) and p.get("body")), {})
        _nq_mail.queue_world_mail(
            channel_id,
            sender=str(_post0.get("from") or _post0.get("author") or
                       best_event.get("npc") or ""),
            kind=str(_post0.get("format_name") or ""),
            summary=str(_post0.get("body") or ""),
            turn=current_turn,
        )
    except Exception as e:
        logger.debug(f"[WorldBoard] world mail queue skipped: {e}")


def _update_board_state(channel_id: str, best_event: Dict[str, Any], final_channel: str,
                        posts: Dict[str, Any], posted_count: int, current_turn: int) -> None:
    """게시 뒤 세계 상태 적립 — 카운트·NPC 이력·타입 이력·시계 마일스톤·요약·핸들.

    ⚠`last_post_turn` 은 **게이트가 아니다**(P14 에서 턴 간격 게이트 소멸). 마지막 게시가
      언제였나는 여전히 사실이라 남긴다 — 읽는 쪽이 없어졌을 뿐이다.
    """

    world = domain_manager.get_world_state(channel_id)
    board_state = world.get("world_board", {})

    # 기본 카운트
    board_state["total_posts"] = board_state.get("total_posts", 0) + posted_count
    board_state["last_post_turn"] = current_turn

    # NPC 포스트 히스토리 (쿨다운용, max 10)
    npc_history = board_state.get("npc_post_history", [])
    npc_history.append({
        "npc": best_event.get("npc", ""),
        "turn": current_turn,
        "channel": final_channel,
        "type": best_event.get("type", ""),
    })
    board_state["npc_post_history"] = npc_history[-10:]

    # 이벤트 타입 히스토리 (반복 감쇠용, max 3)
    recent_types = board_state.get("recent_event_types", [])
    recent_types.append(best_event.get("type", ""))
    board_state["recent_event_types"] = recent_types[-3:]

    # 시계 마일스톤 추적
    tag = best_event.get("tag", "")
    if tag.startswith("clock_"):
        milestones = board_state.get("posted_clock_milestones", [])
        milestone_key = tag.replace("clock_complete:", "").replace("clock_half:", "")
        suffix = "complete" if "complete" in tag else "half"
        milestones.append(f"{milestone_key}:{suffix}")
        board_state["posted_clock_milestones"] = milestones[-20:]

    # [2026-09-24 감사] 해소 스레드 게시 기록 — Scanner 8 이 같은 해소를 다시 내지 않게.
    if best_event.get("type") == "thread_resolved" and best_event.get("thread"):
        done = board_state.get("posted_resolved_threads", [])
        done = list(done) if isinstance(done, list) else []
        if best_event["thread"] not in done:
            done.append(str(best_event["thread"]))
        board_state["posted_resolved_threads"] = done[-20:]

    # [2026-10-17 C46 R4] 퀘스트 카드 게시 기록 — Scanner 11 이 같은 퀘스트를 다시 내지 않게.
    if best_event.get("type") == "quest_done" and best_event.get("quest"):
        _pq = board_state.get("posted_quests", [])
        _pq = list(_pq) if isinstance(_pq, list) else []
        if best_event["quest"] not in _pq:
            _pq.append(str(best_event["quest"]))
        board_state["posted_quests"] = _pq[-20:]

    # world_change 인덱스 추적
    if best_event.get("type") == "world_change":
        session_mem = domain_manager.get_session_ai_memory(channel_id)
        wc = session_mem.get("world_changes", [])
        board_state["last_world_change_idx"] = len(wc)

    world["world_board"] = board_state
    domain_manager.update_world_state(channel_id, world)
    _save_post_summaries(channel_id, posts)
    _update_handle_registry(channel_id, posts)


async def trigger_board_update(
    channel: discord.TextChannel,
    client,
    model_id: str,
    channel_id: str,
    trigger: str = "turn",
    extra_context: str = "",
    dai: Optional[Dict[str, Any]] = None,
    prose_message: Optional[discord.Message] = None,
    deliver: Optional[Dict[str, Any]] = None,
    ref: Optional[Dict[str, Any]] = None,
) -> Optional[Dict[str, Any]]:
    """[2026-09-24 감사] 얇은 래퍼 — 본체는 `_trigger_board_update`(docstring 그쪽).

    선언 핸드아웃(`trigger="declared"`)이 무산되면(None·예외) 소비된 선언 행을 보류 알림으로
    되돌린다(`restore_arrival_request`). 본체엔 None 을 내는 자리가 여섯이라 한 곳에서 문다.
    """
    try:
        result = await _trigger_board_update(
            channel, client, model_id, channel_id, trigger=trigger,
            extra_context=extra_context, dai=dai, prose_message=prose_message,
            deliver=deliver, ref=ref)
    except BaseException:
        if trigger == "declared":
            restore_arrival_request(channel_id, deliver)
        raise
    if result is None and trigger == "declared":
        restore_arrival_request(channel_id, deliver)
    return result


async def _trigger_board_update(
    channel: discord.TextChannel,
    client,
    model_id: str,
    channel_id: str,
    trigger: str = "turn",
    extra_context: str = "",
    dai: Optional[Dict[str, Any]] = None,
    prose_message: Optional[discord.Message] = None,
    deliver: Optional[Dict[str, Any]] = None,
    ref: Optional[Dict[str, Any]] = None,
) -> Optional[Dict[str, Any]]:
    """이벤트 드리븐 게시판 트리거 (v2). 이벤트 없으면 0 API 콜.

    [2026-08-16 도착물 라우트] prose_message = 이번 턴 산문의 **마지막** 메시지.
    착지 모드가 button 인 채널종은 스레드 대신 이 메시지에 💌를 사후 부착한다.

    [2026-09-13 P14 핸드아웃] `trigger="declared"` = 도착물이 **산문의 입력**인 모드.
      생산자는 그대로다(같은 이벤트 수집·같은 프롬프트·같은 콜 1). 다른 건 세 가지뿐:
        · 게이트 우회 — 부른 쪽이 이미 "지금이다"를 말했다(턴 간격 게이트는 P14 에서 소멸).
        · 착지를 **안 한다** — 산문이 아직 없으니 붙일 메시지가 없다. 산출을 **반환**하고
          표시(봉투/임베드)·적립은 호출부(orchestration 4.75)가 산문 **앞**에서 한다.
        · `deliver` = 선언이 정한 것(kind·from·brief·format_name). `kind` 가 있으면 채널종을
          강제하고, 없으면(=`arrival` 신호) 종전대로 dai 이벤트가 고른다.
      반환 `{"channel_kind", "kind"(turn_mail), "payload", "event"}` — 실패·무발화는 None.
    """
    declared = (trigger == "declared")
    req = deliver if isinstance(deliver, dict) else {}
    # 모듈 활성 체크
    modules = domain_manager.get_active_modules(channel_id)
    if "board" not in modules:
        return None

    # 활성 채널 확인
    enabled_channels = get_board_channels(channel_id)
    if not any(enabled_channels.values()):
        return None

    # ⚰[2026-09-13 P14] 턴 간격 게이트(2단: 조기탈출 + 라우팅 후 재판정) 삭제 — 파일 머리 묘비 참조.
    world = domain_manager.get_world_state(channel_id)
    current_turn = world.get("turn_index", 0)

    absent_npcs = _get_absent_npcs(channel_id)
    if declared and str(req.get("kind") or ""):
        # 선언이 종류를 정했다 — 이벤트 수집·선별을 건너뛰고 **선언 자체가 이벤트**다.
        #   (무엇을 쓸지는 brief 가, 누가 쓸지는 from 이 말한다. 코드는 본문을 안 쓴다.)
        final_channel = DELIVER_CHANNEL.get(str(req.get("kind")), "message")
        if not enabled_channels.get(final_channel):
            logger.info(f"[WorldBoard] declared dropped (channel off) ch={final_channel}")
            return None
        best_event = _declared_event(req, final_channel)
    else:
        # 이벤트 수집
        events = _collect_board_events(channel_id, dai or {})
        if not events:
            return None

        # 최고 weight 이벤트 선택
        best_event = _select_best_event(events, channel_id)
        if not best_event:
            return None

        # 채널 라우팅
        final_channel = _route_event_channel(best_event, enabled_channels, absent_npcs)
        if not final_channel:
            return None

    # [2026-08-16 도착물 라우트] 착지 모드 판정 — **Flash 콜 앞**에서 한다.
    #   off = 드롭(콜 0). button 인데 붙일 산문 메시지가 없는 경로(!시간 진행 등)도 여기서
    #   드롭한다 — 스레드로 폴백하면 "공개되면 안 되는 것"이 다시 공개되므로 폴백은 금지.
    #
    # [2026-08-17 v1.1 §3c] off 의미론 = **"콜 앞 드롭"으로 확정**(생성 후 숨김이 아니다).
    #   "저장은 하되 표시 0"으로 바꾸면 아무도 볼 수 없는 게시물 하나당 Flash 콜 1개가
    #   순증하고, 그 산출이 _save_post_summaries·_update_handle_registry 를 통해 다음 턴
    #   프롬프트 재료로 흘러든다 — 즉 "표시만 끈" 게 아니라 **보이지 않는 세계가 계속 자란다**.
    #   끄기는 끄기여야 한다. 이력이 필요하면 button 을 쓰면 되고, 그건 이미 저장된다.
    # [2026-09-13 P14] declared 는 착지 판정 밖이다 — 산문 **앞** 제 메시지로 서므로
    #   "붙일 산문 메시지"도 "공개 스레드"도 묻지 않는다. off 만은 존중한다(끄기는 끄기다).
    display_mode = get_display_mode(channel_id, final_channel)
    if display_mode == "off":
        logger.info(f"[WorldBoard] dropped (display=off) ch={final_channel} "
                    f"event={best_event.get('tag', '?')}")
        return None
    if not declared and display_mode == "button" and prose_message is None:
        logger.info(f"[WorldBoard] dropped (display=button, no prose message) "
                    f"ch={final_channel} trigger={trigger}")
        return None

    # 이벤트 맞춤 프롬프트로 Flash 콜
    # [2026-08-17 light 라우트] 감싸는 건 **실제 LLM 콜**(generate_posts)뿐이다 —
    #   프롬프트 빌더(_build_event_prompt)는 임베딩만 쓰고(_map_model 무관) 라우팅 대상이 아니다.
    #   contextvar 라 generate_posts → api_call_with_retry → 백엔드까지 await 를 타고 살아간다.
    _frame = mail_frame(channel_id, str(req.get("format_name") or "")) if declared else ""
    _ep_kw: Dict[str, Any] = {"frame": _frame}
    if declared and isinstance(ref, dict):
        _ep_kw["ref"] = ref          # [2026-10-17 C46] 재료(자료 존·원문)는 도착물 길에만 — 다른 길은 부르는 꼴 그대로
    prompt = await _build_event_prompt(client, channel_id, best_event, final_channel, **_ep_kw)
    active_channels = {final_channel: True}
    with config.light_call():
        posts = await generate_posts(
            client, model_id, channel_id, active_channels,
            trigger, extra_context, max_posts=1,
            absent_npcs=absent_npcs,
            override_prompt=prompt,
        )
    if not posts:
        logger.info(f"[WorldBoard] Flash returned empty for event={best_event.get('tag', '?')}")
        return None

    # [2026-09-13 P14] declared — 착지 대신 **반환**. 상태 적립(중복 방지 재료)은 종전과 같은
    #   함수를 탄다: 핸드아웃이라고 세계가 자기가 쓴 걸 잊으면 다음 글이 같은 말을 다시 쓴다.
    if declared:
        _raw = posts.get(final_channel)
        _items = _raw if isinstance(_raw, list) else [_raw] if _raw else []
        _post = next((p for p in _items if isinstance(p, dict) and p.get("body")), None)
        if not _post:
            logger.info("[WorldBoard] declared: 본문 없는 산출 → 핸드아웃 없음")
            return None
        if str(req.get("format_name") or "") and not _post.get("format_name"):
            _post["format_name"] = str(req.get("format_name"))
        _update_board_state(channel_id, best_event, final_channel, posts, 1, current_turn)
        _queue_world_mail(channel_id, final_channel, _items, best_event, current_turn)
        return {
            "channel_kind": final_channel,
            "kind": BOARD_MAIL_KIND.get(final_channel, "mail"),
            "payload": _mail_payload(_post, final_channel),
            "event": best_event,
        }

    # Discord에 착지 — [2026-08-16 도착물 라우트] thread(종전) / button(그 턴 메시지 💌).
    posted_count = 0
    raw = posts.get(final_channel)
    items = raw if isinstance(raw, list) else [raw] if raw else []
    thread = None
    for post in items:
        if not post or not isinstance(post, dict) or not post.get("body"):
            continue

        if display_mode == "button":
            # 공개 스레드 대신 turn_mail 적립 + 산문 메시지에 버튼 사후 부착.
            # 저장 키 = 그 턴 산문 메시지 id → 다음 턴 내용이 옛 버튼에 새지 않는다.
            # [2026-08-17 v1.1 §3] kind 는 채널종이 정한다 — message=💌, 공지·SNS=📰.
            try:
                import turn_mail
                _kind = BOARD_MAIL_KIND.get(final_channel, turn_mail.KIND_MAIL)
                if await turn_mail.deliver(prose_message, channel_id, _kind,
                                           _mail_payload(post, final_channel),
                                           turn=current_turn):
                    posted_count += 1
            except Exception as e:
                logger.warning(f"[WorldBoard] turn_mail deliver 실패: {e}")
            continue

        if thread is None:
            thread_name_map = {
                "bulletin": f"📋 {post.get('board_name', '게시판')}",
                "sns": f"📱 {post.get('feed_name', 'SNS')}",
                "message": f"💌 {post.get('format_name', '메시지')}",
            }
            thread_key_map = {
                "bulletin": "bulletin_thread_id",
                "sns": "sns_thread_id",
                "message": "message_thread_id",
            }
            thread = await _ensure_thread(
                channel, channel_id,
                thread_key_map[final_channel],
                thread_name_map.get(final_channel, "📋 게시판"),
            )
        if thread:
            post_fn = {"bulletin": _post_bulletin, "sns": _post_sns, "message": _post_message}
            await post_fn[final_channel](thread, post, channel_id)
            posted_count += 1

    # [2026-08-17 쪽지 서사 접지] 사적 도착물이 **표시층에서 끝나지 않게** — 보낸 사실+내용을
    #   1턴 큐에 적재하고, 다음 턴 좌뇌 서사 콜이 소비한다(narrative_queries.world_mail_block).
    #   자리가 여기인 이유: 착지 모드(button/thread) 둘 다를 지나는 **유일한 합류점**이라
    #   한 번만 적재된다(모드별로 걸면 이중 적재나 한쪽 결손이 난다).
    #   공지·SNS는 공적 매체라 제외 — "나한테 온 것"이 아니면 답장하지 않은 편지도 아니다.
    if posted_count > 0:
        _queue_world_mail(channel_id, final_channel, items, best_event, current_turn)

    # 상태 업데이트
    if posted_count > 0:
        _update_board_state(channel_id, best_event, final_channel, posts,
                            posted_count, current_turn)

    logger.info(
        f"[WorldBoard] Posted {posted_count} event={best_event.get('tag', '?')} "
        f"ch={final_channel} trigger={trigger} display={display_mode}"
    )


# =====================================================================
# [2026-10-23 C64 M5] 도착물 핸드아웃 — 게시판 모듈의 pre_render_call 정거장
#   옛 orchestration 5.5(`_deliver_handout` 메서드 + 둘레 줄)를 글자 그대로 옮겼다. 바뀐 건 이름뿐:
#   self → rt(OrchestrationService) · 지역 full_prompt → b.prompt · 삭제 목록 → b.message_ids.
#   로그는 옛 로거("Orchestration") 그대로 — `[Handout] …` 줄의 글자·로거가 같다.
# =====================================================================
_olog = logging.getLogger("Orchestration")


async def handout_station(rt, b) -> None:
    """옛 5.5 둘레 줄 — 핸드아웃 콜·표시 → `!다시` 삭제 목록 → 이번 턴 문서에 끼우기. 초기화(ctx.handout_*)는 호출부."""
    ctx, message, channel_id = b.ctx, b.message, b.channel_id
    try:
        await _deliver_handout(rt, ctx, message, channel_id)
    except Exception as _e_hand:
        _olog.debug(f"[Handout] skipped: {_e_hand}")
    # [2026-09-24 감사] 핸드아웃 메시지도 `!다시` 삭제 목록에 — 빠져 있어 옛 봉투/공고가 채널에 남고
    #   (turn_mail 행은 지워져 💌 누르면 "만료") 재실행이 도착물을 한 번 더 보냈다.
    if getattr(ctx, "handout_message_id", None):
        b.message_ids.append(ctx.handout_message_id)
    if getattr(ctx, "handout_text", ""):
        try:
            import custom_vars as _cv_ho
            b.prompt = _cv_ho.insert_handout(b.prompt, ctx.handout_text)
        except Exception as _e_ins:
            _olog.debug(f"[Handout] insert skipped: {_e_ins}")


async def deliver_pending_handout(ctx, prose_message, channel_id: str) -> bool:
    """[2026-10-23 C69 A2b] 압축 모드에서 보류한 도착물을 **산문 메시지**에 적립하고 C49 원문 묶음(handout_record)을 만든다.

    자리 = orchestration 7(산문 전송) 바로 뒤 · 8(기록) 앞 — 8 이 `ctx.handout_record` 로 원문 행 · 위키 줄을 적는다.
    보류가 없으면 아무것도 안 한다. 적립이 실패해도 한 줄 알림으로 되돌리지 않는다 — 산문은 이미 그 편지를 읽고
    나갔다(되돌리면 같은 편지를 두 번 알린다). 렌더가 실패해 산문 메시지가 없으면 부르는 쪽이 `discard_pending_handout`.
    """
    pend = getattr(ctx, "handout_pending", None)
    ctx.handout_pending = None
    if not isinstance(pend, dict) or prose_message is None:
        return False
    import turn_mail as _tm
    ok = False
    try:
        ok = await _tm.deliver(prose_message, channel_id, str(pend.get("kind") or _tm.KIND_MAIL),
                               pend.get("payload") or {}, pend.get("turn"))
    except Exception as e:
        _olog.warning(f"[Handout] 산문 메시지 적립 실패(본문은 렌더가 이미 읽음): {e}")
    try:
        ctx.handout_record = handout_record(pend.get("result"), getattr(prose_message, "id", None))
    except Exception as e:
        _olog.debug(f"[Handout] record skipped: {e}")
    _olog.info("[Handout] 산문 메시지에 적립 ok=%s msg=%s", ok, getattr(prose_message, "id", None))
    return bool(ok)


def discard_pending_handout(ctx) -> None:
    """[2026-10-23 C69 A2b] 렌더 실패 — 보류한 도착물을 버린다(`!다시`가 턴 시작 스냅샷으로 되감아 다시 온다)."""
    if getattr(ctx, "handout_pending", None):
        _olog.info("[Handout] 렌더 실패 — 보류 도착물 버림(!다시면 다시 온다)")
    ctx.handout_pending = None


# =========================================================
# [옛 orchestration] STEP 5.5: ARRIVAL HANDOUT (2026-09-13 P14 · 2026-10-17 C46 조립 뒤로 — 전 STEP 4.75)
# =========================================================
async def _deliver_handout(rt, ctx, message, channel_id: str) -> bool:
    """도착물이 있으면 산문 **앞**에서 쓰고·보이고·적립한다. 없으면 콜 0.

    방아쇠 둘(선언 전이 `deliver` / 분석 신호 `arrival`)의 판정은 코드 한 곳
    (`world_board.pick_arrival_request`)에 있고, 여기는 그 결과를 **표시**로 옮길 뿐이다.
      (a) 렌더 프롬프트 `<핸드아웃>` 블록 — 산문이 편지를 읽은 채로 시작한다.
      (b) 산문 **앞** 별도 메시지 — 📰 공개는 임베드 본문, 💌 개인은 봉투 한 줄 +
          💌 버튼(클릭하면 종전 `_respond` 가 ephemeral 로 본문을 연다).
          디스코드 임베드는 텍스트 **밑**에만 그려지므로 산문 뒤에 붙이면 종이가 늦는다.
      (c) `turn_mail` 행 — 그 메시지 id 로 적립되므로 💠 "쌓인 것" 도착물 목록에 자동으로 선다.
    실패는 전부 무해하다: 핸드아웃 없이 산문이 나가고, 다음 턴 재시도는 없다
    (전이는 이미 발화했다 — 재시도는 같은 편지를 두 번 보내는 길이다).
    """
    import world_board as _wb
    # [2026-09-24 감사] 채널 검사를 **집기 전**으로 — 전엔 선언 행을 먼저 소비한 뒤 스레드 채널이면
    #   return 해서 스레드 세션의 선언 도착물이 알림째 증발했다(안 집으면 9.52 flush 가 한 줄 알림으로 보낸다).
    if not isinstance(message.channel, discord.TextChannel):
        _olog.debug("[Handout] 텍스트 채널이 아니라 스킵")
        return False
    req = _wb.pick_arrival_request(channel_id, ctx.dai)
    if not req:
        return False
    # [2026-10-17 C46 W2] 조립이 남긴 재료 + PC 이름(편지 받는 쪽 — 원문 발췌 쿼리에만 쓴다).
    _ref = getattr(ctx, "handout_ref", None)
    if isinstance(_ref, dict):
        _ref = dict(_ref)
        _ref["pc_names"] = rt._pc_names_for_extract(channel_id, ctx.user_id)
    result = await _wb.trigger_board_update(
        message.channel, rt.client, config.role_model("light"), channel_id,
        trigger="declared", dai=dict(ctx.dai) if ctx.dai else {}, deliver=req, ref=_ref,
    )
    if not result:
        _olog.info("[Handout] 도착물 콜 무산 — 핸드아웃 없이 진행 (source=%s)",
                    req.get("source"))
        return False

    import custom_vars as _cv
    import turn_mail as _tm
    payload = result.get("payload") or {}
    # [2026-10-17 C46 W6] 조립 **뒤**라 큐(급식 소비)가 아니라 호출부(5.5)가 이번 턴 문서에 끼운다.
    #   배경 추출이 읽는 `last_handouts` 칸은 여기서 직접 채운다.
    _ho_text = _wb.handout_text(result)
    ctx.handout_text = _ho_text
    _cv.set_last_handouts(channel_id, _ho_text)
    _ev_tag = (result.get("event") or {}).get("tag")
    # [2026-10-17 C46 R8] 기계 말 감시 — 막지 않고 센다(라이브에서 "시계가 완성되어…"가 새는지).
    try:
        _mw = _wb.MECH_WORD_RE.search(str(payload.get("body") or ""))
        if _mw:
            _olog.info("[Handout] mech-word=%s event=%s", _mw.group(0), _ev_tag)
    except Exception:
        pass

    envelope = _tm.envelope_line(payload)
    turn = 0
    try:
        turn = int((domain_manager.get_world_state(channel_id) or {}).get("turn_index", 0) or 0)
    except (TypeError, ValueError):
        turn = 0
    # [2026-10-23 C69 A2b] 압축 모드(Activity 떠 있음) — 산문 앞 따로 메시지(✉ 봉투 · 📰 공고)를 **안 보낸다**.
    #   적립할 거리만 ctx 에 두고, orchestration 7(산문 전송) 바로 뒤가 산문 메시지에 deliver + handout_record 를 만든다
    #   (`deliver_pending_handout`). 핸드아웃 글(렌더 입력)은 위에서 이미 끼웠다 — 렌더는 지금처럼 편지를 읽고 쓴다.
    #   본문은 Activity 편지 · 소식 앱, 디스코드엔 압축 상태 알림 줄(C68)이 "💌 새 편지"로 센다.
    try:
        import status_panel as _sp_ho
        _compact = _sp_ho.turn_compact_mode()
    except Exception:
        _compact = False
    if _compact:
        ctx.handout_message_id = None
        ctx.handout_pending = {"kind": str(result.get("kind") or _tm.KIND_MAIL), "payload": payload,
                               "turn": turn, "result": result}
        _olog.info("[Handout] 압축 모드 — 따로 메시지 없이 산문 메시지에 적립 대기 ch=%s source=%s event=%s turn=%s",
                   result.get("channel_kind"), req.get("source"), _ev_tag, turn)
        return True
    try:
        if envelope:
            sent = await message.channel.send(envelope)
        else:
            sent = await message.channel.send(
                embed=_tm.handout_embed(payload, turn))
        ctx.handout_message_id = getattr(sent, "id", None)   # [2026-09-24 감사] !다시 삭제 목록용
        await _tm.deliver(sent, channel_id, str(result.get("kind") or _tm.KIND_MAIL),
                          payload, turn)
        # [2026-10-17 C49 R1·R2] 원문 행·위키 줄은 step 8(입력 다음·산문 앞)이 쓴다 — 여기선 묶음만 남긴다.
        ctx.handout_record = _wb.handout_record(result, ctx.handout_message_id)
    except Exception as e:
        _olog.warning(f"[Handout] 표시 실패(핸드아웃 블록은 유지): {e}")
        # [2026-09-24 감사] 표시가 무산되면 선언 행을 한 줄 알림으로 되돌린다(이미 되돌렸으면 no-op).
        try:
            _wb.restore_arrival_request(channel_id, req)
        except Exception:
            pass
        return False
    _olog.info("[Handout] %s ch=%s source=%s event=%s turn=%s",   # [C46 R7] 골린 이벤트 tag
                "💌 봉투" if envelope else "📰 임베드",
                result.get("channel_kind"), req.get("source"), _ev_tag, turn)
    return True
