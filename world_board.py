# -*- coding: utf-8 -*-
"""
World Board Module — 세계 게시판 + SNS
Discord 스레드에 세계관 내 게시물을 자동 생성.
서술턴과 완전 분리, 읽기 전용 (v1).
"""

import json
import logging
import discord
from typing import Optional, Dict, Any, List

import config
import domain_manager
import bot_utils

logger = logging.getLogger("WorldBoard")


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
            auto_archive_duration=1440,  # 24시간 비활성 시 아카이브
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
    for key in ("bulletin_thread_id", "sns_thread_id"):
        tid = board_state.get(key)
        if tid:
            thread = channel.guild.get_thread(tid)
            if thread:
                try:
                    await thread.edit(archived=True)
                except Exception:
                    pass
    board_state.pop("bulletin_thread_id", None)
    board_state.pop("sns_thread_id", None)
    world["world_board"] = board_state
    domain_manager.update_world_state(channel_id, world)


# =========================================================
# Flash Post Generation
# =========================================================

def _build_board_prompt(
    channel_id: str,
    trigger: str = "time",
    extra_context: str = "",
) -> str:
    """게시판 + SNS 게시물 생성 프롬프트."""
    world = domain_manager.get_world_state(channel_id)
    location = world.get("current_location", "Unknown")
    day = world.get("day", 1)
    time_slot = world.get("time_slot", "오후")
    hour = world.get("hour", 12)
    minute = world.get("minute", 0)
    weather = world.get("weather", "맑음")
    doom = world.get("doom", 0)

    # 장르 정보
    genres = world.get("genres", {})
    if isinstance(genres, dict):
        stage = ", ".join(genres.get("stage", [])) or "미정"
        atmosphere = genres.get("atmosphere", "") or "미정"
    else:
        stage = "미정"
        atmosphere = "미정"

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

    # NPC 이름 목록
    npcs = domain_manager.get_npcs(channel_id)
    npc_names = list(npcs.keys())[:10] if npcs else []

    # 스토리텔러 최근 이벤트
    storyteller = world.get("storyteller", {})
    recent_tags = storyteller.get("recent_tags", [])

    # 기존 게시물 수 (중복 방지)
    board_state = world.get("world_board", {})
    post_count = board_state.get("total_posts", 0)

    return f"""You are a world content generator for a TTRPG session.

## WORLD INFO
- Genre/Setting: {stage}
- Atmosphere: {atmosphere}
- Location: {location}
- Time: Day {day}, {hour:02d}:{minute:02d} ({time_slot})
- Weather: {weather}
- Doom Level: {doom}%
- World Rules: {constraints_text or 'None'}
- NPC Names: {', '.join(npc_names) or 'None'}
- Recent Events: {', '.join(recent_tags[-3:]) or 'None'}
- Trigger: {trigger}
{f'- Extra: {extra_context}' if extra_context else ''}
- Posts so far: {post_count} (avoid repeating themes)

## TASK
Generate content for TWO channels in this world:

1. **bulletin** — Public board (guild board, notice board, news bulletin, etc.)
   One post: official notice, job posting, news, warning, etc.
   Written by an NPC or organization IN the world.

2. **sns** — Personal feed (social media, tavern gossip, personal diary, etc.)
   One post: casual, personal, showing NPC daily life or rumors.
   Written by a specific NPC or anonymous character.

## RULES
- Write in Korean
- Each post 100-200 characters (body)
- Match the world's genre and atmosphere
- Use NPC names when appropriate
- Time-appropriate content (dawn posts differ from night posts)
- DO NOT reference game mechanics or meta information

## OUTPUT FORMAT (JSON)
```json
{{
  "bulletin": {{
    "board_name": "게시판 이름 (장르에 맞게)",
    "author": "작성자 이름/직함",
    "title": "제목",
    "body": "본문 (100-200자)"
  }},
  "sns": {{
    "feed_name": "SNS 이름 (장르에 맞게)",
    "author": "작성자",
    "body": "본문 (100-200자)"
  }}
}}
```"""


async def generate_posts(
    client,
    model_id: str,
    channel_id: str,
    trigger: str = "time",
    extra_context: str = "",
) -> Optional[Dict[str, Any]]:
    """Flash API로 게시물 생성."""
    from memory_system import api_call_with_retry
    from google.genai import types

    prompt = _build_board_prompt(channel_id, trigger, extra_context)

    cfg = types.GenerateContentConfig(
        temperature=0.9,
        max_output_tokens=1024,
        response_mime_type="application/json",
        safety_settings=config.SAFETY_SETTINGS,
    )
    contents = [
        types.Content(role="user", parts=[types.Part(text=prompt)]),
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
# Discord Posting
# =========================================================

async def _post_bulletin(
    thread: discord.Thread,
    post: Dict[str, Any],
    channel_id: str,
) -> None:
    """게시판 스레드에 Embed 게시."""
    world = domain_manager.get_world_state(channel_id)
    day = world.get("day", 1)
    hour = world.get("hour", 12)
    minute = world.get("minute", 0)
    time_slot = world.get("time_slot", "")

    embed = discord.Embed(
        title=post.get("title", ""),
        description=post.get("body", ""),
        color=0x2F3136,
    )
    embed.set_author(name=post.get("author", "익명"))
    embed.set_footer(text=f"{day}일차 {hour:02d}:{minute:02d} ({time_slot})")

    await thread.send(embed=embed)


async def _post_sns(
    thread: discord.Thread,
    post: Dict[str, Any],
    channel_id: str,
) -> None:
    """SNS 스레드에 Embed 게시."""
    world = domain_manager.get_world_state(channel_id)
    day = world.get("day", 1)
    hour = world.get("hour", 12)
    minute = world.get("minute", 0)

    embed = discord.Embed(
        description=post.get("body", ""),
        color=0x5865F2,
    )
    embed.set_author(name=post.get("author", "익명"))
    embed.set_footer(text=f"Day {day} · {hour:02d}:{minute:02d}")

    await thread.send(embed=embed)


# =========================================================
# Main Trigger Entry Point
# =========================================================

async def trigger_board_update(
    channel: discord.TextChannel,
    client,
    model_id: str,
    channel_id: str,
    trigger: str = "time",
    extra_context: str = "",
) -> None:
    """게시판 업데이트 트리거. 시간 경과/장소 변경 시 호출."""
    # 모듈 활성 체크
    modules = domain_manager.get_active_modules(channel_id)
    if "board" not in modules:
        return

    # Flash로 게시물 생성
    posts = await generate_posts(client, model_id, channel_id, trigger, extra_context)
    if not posts:
        return

    # 게시판 게시
    bulletin = posts.get("bulletin")
    if bulletin and bulletin.get("body"):
        board_name = bulletin.get("board_name", "📋 게시판")
        thread = await _ensure_thread(
            channel, channel_id, "bulletin_thread_id", f"📋 {board_name}"
        )
        if thread:
            await _post_bulletin(thread, bulletin, channel_id)

    # SNS 게시
    sns = posts.get("sns")
    if sns and sns.get("body"):
        feed_name = sns.get("feed_name", "📱 SNS")
        thread = await _ensure_thread(
            channel, channel_id, "sns_thread_id", f"📱 {feed_name}"
        )
        if thread:
            await _post_sns(thread, sns, channel_id)

    # 게시물 카운트 업데이트
    world = domain_manager.get_world_state(channel_id)
    board_state = world.get("world_board", {})
    board_state["total_posts"] = board_state.get("total_posts", 0) + 1
    world["world_board"] = board_state
    domain_manager.update_world_state(channel_id, world)

    logger.info(f"[WorldBoard] Posted (trigger={trigger})")
