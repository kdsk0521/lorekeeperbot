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
            if domain_manager.is_session_locked(channel_id):
                status = domain_manager.get_participant_status(channel_id, message.author.id)
                if not status: return # Ignore non-participants

            # 3. SPECIAL INPUTS (Dice, OOC -> handled by dispatch too)
            if parsed and parsed['type'] in ['dice', 'ooc', 'chat_with_ooc']:
                if parsed['type'] in ['ooc', 'chat_with_ooc']:
                    ooc_content = parsed.get('ooc_content') or parsed.get('content', '')
                    ooc_parsed = {'content': ooc_content}
                    ooc_directive = await command_handler.dispatch_command(
                        'ooc', message, channel_id, ooc_parsed,
                        client_discord, client_genai, MODEL_ID, MODEL_ID_FLASH,
                        domain_manager.get_domain(channel_id)
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

            # Whitelist Check (Ignore if bot inactive)
            if not domain_manager.get_bot_active(channel_id):
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

if __name__ == "__main__":
    if DISCORD_TOKEN and GEMINI_API_KEY:
        client_discord.run(DISCORD_TOKEN)
    else:
        print("MISSING API KEYS")
