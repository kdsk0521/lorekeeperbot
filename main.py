# -*- coding: utf-8 -*-
"""
Lorekeeper TRPG Bot - Main Module
Version: 5.0 (Modularized with Orchestration Service)
"""

import discord
import os
import asyncio
import logging
from typing import Optional, Dict
from collections import defaultdict
from google import genai

# =========================================================
# MODULE IMPORTS
# =========================================================
try:
    import config
    import bot_utils
    import input_handler
    import command_handler
    import domain_manager

    # Orchestration Service
    from orchestration import get_orchestration_runtime

except ImportError as e:
    print(f"CRITICAL ERROR: Failed to import modules. {e}")
    exit(1)

# =========================================================
# CONFIGURATION & LOGGING
# =========================================================
logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')

DISCORD_TOKEN = config.DISCORD_TOKEN
GEMINI_API_KEY = config.GEMINI_API_KEY
MODEL_ID = config.MODEL_ID
MODEL_ID_FLASH = config.MODEL_ID_FLASH

if not GEMINI_API_KEY: logging.warning("GEMINI_API_KEY Missing!")

client_genai = None
try:
    if GEMINI_API_KEY: client_genai = genai.Client(api_key=GEMINI_API_KEY)
except Exception as e: logging.error(f"GenAI Init Failed: {e}")

intents = discord.Intents.default()
intents.message_content = True
client_discord = discord.Client(intents=intents)

channel_locks: Dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)


# =========================================================
# DISCORD EVENTS
# =========================================================
@client_discord.event
async def on_ready():
    logging.info(f'Logged in as {client_discord.user}')
    await client_discord.change_presence(activity=discord.Game(name="!help | TRPG"))

@client_discord.event
async def on_message(message: discord.Message) -> None:
    if message.author == client_discord.user: return
    if not isinstance(message.channel, (discord.TextChannel, discord.Thread)): return

    asyncio.create_task(_process_message(message))

async def _process_message(message: discord.Message) -> None:
    channel_id = str(message.channel.id)
    async with channel_locks[channel_id]:
        try:
            content = message.content.strip()
            parsed = input_handler.parse_input(content)

            # 0. BOT ACTIVE GATE (봇 꺼진 채널: !봇 토글만 허용)
            if not domain_manager.get_bot_active(channel_id):
                if parsed and parsed['type'] == 'command' and parsed['command'] in ('bot', '봇'):
                    await command_handler.dispatch_command(
                        parsed['command'], message, channel_id, parsed,
                        client_discord, client_genai, MODEL_ID, MODEL_ID_FLASH,
                        domain_manager.get_domain(channel_id)
                    )
                return

            # 1. COMMANDS
            if parsed and parsed['type'] == 'command':
                sys_trigger = await command_handler.dispatch_command(
                    parsed['command'], message, channel_id, parsed,
                    client_discord, client_genai, MODEL_ID, MODEL_ID_FLASH,
                    domain_manager.get_domain(channel_id)
                )
                if sys_trigger and isinstance(sys_trigger, str):
                    await generate_ai_response(message, channel_id, sys_trigger)
                return

            # 2. SESSION LOCK CHECK
            if not domain_manager.is_session_locked(channel_id):
                return
            status = domain_manager.get_participant_status(channel_id, message.author.id)
            if not status: return  # Ignore non-participants

            # 3. SPECIAL INPUTS (Dice, OOC inline)
            if parsed and parsed['type'] in ['dice', 'ooc', 'chat_with_ooc']:
                if parsed['type'] in ['ooc', 'chat_with_ooc']:
                    ooc_content = parsed.get('ooc_content') or parsed.get('content', '')
                    ooc_directive = await command_handler.handle_ooc_command(
                        message, channel_id, ooc_content,
                        client_genai, MODEL_ID
                    )
                    if ooc_directive:
                        await generate_ai_response(
                            message,
                            channel_id,
                            user_input_override=ooc_directive
                        )
                    return

                await command_handler.dispatch_command(
                    None, message, channel_id, parsed,
                    client_discord, client_genai, MODEL_ID, MODEL_ID_FLASH,
                    domain_manager.get_domain(channel_id)
                )
                return

            # 3.5. OOC MODE CHECK
            if domain_manager.get_ooc_mode(channel_id):
                await generate_ooc_response(message, channel_id)
                return

            # 4. CHAT LOGGING / RESPONSE
            mode = domain_manager.get_response_mode(channel_id)

            if mode == 'waiting':
                mask = domain_manager.get_user_mask(channel_id, message.author.id)
                log_content = message.content
                if message.attachments:
                    for att in message.attachments:
                        txt, _ = await bot_utils.read_attachment_text(att)
                        if txt: log_content += f"\n(Attach: {txt})"

                domain_manager.append_history(channel_id, mask, log_content.strip())
                await message.add_reaction("✏️")
                return

            # AUTO MODE
            await generate_ai_response(message, channel_id)

        except Exception as e:
            logging.error(f"Message Error: {e}", exc_info=True)
            await message.channel.send(f"⚠️ Error: {e}")


# =========================================================
# AI GENERATION CORE (Delegated to OrchestrationService)
# =========================================================

async def generate_ai_response(
    message: discord.Message,
    channel_id: str,
    system_trigger: Optional[str] = None,
    user_input_override: Optional[str] = None
) -> None:
    """AI 응답 생성 (OrchestrationService로 위임)"""
    orchestration = get_orchestration_runtime(client_genai, MODEL_ID, MODEL_ID_FLASH)
    if not orchestration:
        await message.channel.send("⚠️ No AI Configured")
        return

    feedback_msg = await message.channel.send("🔄 **서사를 생성하고 있습니다...**")

    await orchestration.execute(
        message,
        channel_id,
        system_trigger,
        feedback_msg=feedback_msg,
        user_input_override=user_input_override
    )


# =========================================================
# OOC HELPER (Lightweight AI for OOC mode)
# =========================================================

async def generate_ooc_response(
    message: discord.Message,
    channel_id: str
) -> None:
    """OOC 도우미 모드 응답 생성 (Flash 모델 사용)"""
    if not client_genai:
        await message.channel.send("⚠️ No AI Configured")
        return

    from google.genai import types
    import text_resources

    # Build context
    import game_world
    import npc_manager

    history = domain_manager.get_history(channel_id)
    history_text = "\n".join(
        [f"{h['role']}: {h['content']}" for h in history[-15:]]
    ) if history else "(히스토리 없음)"

    lore_text = domain_manager.get_lore(channel_id) or "(로어 없음)"

    # 세계 상태
    world_context = game_world.get_world_context(channel_id)

    # NPC 현황
    npcs = domain_manager.get_npcs(channel_id)
    npc_lines = []
    for name, data in npcs.items():
        attitude = data.get("attitude", "?")
        role = data.get("role", "")
        npc_lines.append(f"- {name}: {role} (태도: {attitude})")
    npc_text = "\n".join(npc_lines) if npc_lines else "(등록된 NPC 없음)"

    # 세션 AI 메모리 (진행 중인 스레드, 아크 등)
    ai_mem = domain_manager.get_session_ai_memory(channel_id)
    mem_parts = []
    if ai_mem.get("active_threads"):
        mem_parts.append(f"진행 중인 서사: {', '.join(ai_mem['active_threads'][:5])}")
    if ai_mem.get("current_arc"):
        mem_parts.append(f"현재 아크: {ai_mem['current_arc']}")
    if ai_mem.get("resolved_threads"):
        mem_parts.append(f"해결된 서사: {', '.join(ai_mem['resolved_threads'][:3])}")
    mem_text = "\n".join(mem_parts) if mem_parts else ""

    system_prompt = text_resources.OOC_HELPER_IDENTITY
    system_prompt += f"\n[세계 상태]\n{world_context}\n" if world_context else ""
    system_prompt += f"\n[NPC 현황]\n{npc_text}\n"
    system_prompt += f"\n[서사 진행]\n{mem_text}\n" if mem_text else ""
    system_prompt += f"\n[최근 히스토리]\n{history_text}\n\n[로어 요약]\n{lore_text[:2000]}"

    user_content = message.content.strip()
    if message.attachments:
        for att in message.attachments:
            txt, _ = await bot_utils.read_attachment_text(att)
            if txt:
                user_content += f"\n(첨부: {txt})"

    try:
        response = await client_genai.aio.models.generate_content(
            model=MODEL_ID_FLASH,
            contents=[
                types.Content(role="user", parts=[types.Part(text=user_content)])
            ],
            config=types.GenerateContentConfig(
                system_instruction=system_prompt,
                temperature=0.7,
                max_output_tokens=2000,
            )
        )
        if response and response.text:
            # [루카] 프리픽스 + Discord 인용 블록으로 IC/OOC 시각 구분
            lines = response.text.strip().split("\n")
            quoted = "\n".join(f"> {line}" if line.strip() else ">" for line in lines)
            formatted = f"**[루카]**\n{quoted}"
            await bot_utils.send_long_message(message.channel, formatted)
        else:
            await message.channel.send("⚠️ 루카가 응답하지 못했습니다.")
    except Exception as e:
        logging.error(f"OOC Response Error: {e}", exc_info=True)
        await message.channel.send(f"⚠️ 루카 오류: {e}")


if __name__ == "__main__":
    if DISCORD_TOKEN and GEMINI_API_KEY:
        client_discord.run(DISCORD_TOKEN)
    else:
        print("MISSING API KEYS")
