"""
Lorekeeper TRPG Bot - Main Module
Version: 5.0 (Modularized with Orchestration Service)

주요 변경사항 (v5.0):
- generate_ai_response를 OrchestrationService로 분리
- 백그라운드 태스크 큐 시스템 도입 (채널별 순차 실행 보장)
- NVC 유통기한 필터링 추가
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

    # Orchestration Service (AI 응답 생성 통합)
    from orchestration import get_orchestration_service

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
async def on_message(message):
    if message.author == client_discord.user: return
    if not isinstance(message.channel, (discord.TextChannel, discord.Thread)): return
    
    asyncio.create_task(_process_message(message))

async def _process_message(message):
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
                await command_handler.dispatch_command(
                    None, message, channel_id, parsed,
                    client_discord, client_genai, MODEL_ID, MODEL_ID_FLASH,
                    domain_manager.get_domain(channel_id)
                )
                return

            # [NEW] Whitelist Check (Ignore if bot inactive)
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

# 오케스트레이션 서비스 인스턴스 (지연 초기화)
_orchestration = None


def _get_orchestration():
    """오케스트레이션 서비스를 지연 초기화하여 반환합니다."""
    global _orchestration
    if _orchestration is None and client_genai:
        _orchestration = get_orchestration_service(client_genai, MODEL_ID, MODEL_ID_FLASH)
    return _orchestration


async def generate_ai_response(message, channel_id: str, system_trigger: str = None) -> None:
    """
    AI 응답을 생성합니다.

    v5.0: OrchestrationService로 위임하여 모듈화 및 유지보수성 향상.
    기존 550줄 이상의 코드가 orchestration.py로 분리되었습니다.

    주요 개선사항:
    - 백그라운드 태스크 큐 시스템으로 채널별 순차 실행 보장
    - NVC 유통기한 필터링으로 오래된 정보 자동 제거
    - PC 사칭 자가 수정 프롬프트 강화
    """
    orchestration = _get_orchestration()
    if not orchestration:
        await message.channel.send("⚠️ No AI Configured")
        return

    # [UI Feedback] 서사 생성 중 알림
    feedback_msg = await message.channel.send("⏳ **서사를 생성하고 있습니다...**")

    # Pass the feedback message to orchestration to delete it later
    await orchestration.execute(message, channel_id, system_trigger, feedback_msg=feedback_msg)

if __name__ == "__main__":
    if DISCORD_TOKEN and GEMINI_API_KEY:
        client_discord.run(DISCORD_TOKEN)
    else:
        print("MISSING API KEYS")
