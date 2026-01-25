"""
Lorekeeper TRPG Bot - Session Manager Module
Manages session lifecycle (Reset, Check Prep, Start).
Replaces: session_manager.py
"""

import discord
import asyncio
import logging
from typing import Optional

import domain_manager
# game_system might be needed if logic requires it, but for now mostly domain IO

RESET_CONFIRM_TIMEOUT = 5.0
RESET_CONFIRM_EMOJI = "💥"
FALLBACK_PURGE_DELAY = 2

class SessionManager:
    """Manages session lifecycle events."""
    
    async def execute_reset(self, message: discord.Message, client: discord.Client) -> None:
        """Fully resets the session by blowing up the channel."""
        channel_id = str(message.channel.id)
        
        confirm_msg = await message.channel.send(
            "🧨 **[경고: 세션 초기화]**\n"
            "이 채널의 모든 데이터와 기억이 **영구적으로 삭제**되며 채널이 재생성됩니다.\n"
            f"{RESET_CONFIRM_EMOJI} 이모지를 눌러 {RESET_CONFIRM_TIMEOUT}초 내에 확정하십시오."
        )
        await confirm_msg.add_reaction(RESET_CONFIRM_EMOJI)
        
        def check(reaction, user):
            return (user == message.author and str(reaction.emoji) == RESET_CONFIRM_EMOJI and reaction.message.id == confirm_msg.id)
        
        try:
            await client.wait_for('reaction_add', timeout=RESET_CONFIRM_TIMEOUT, check=check)
            
            # Reset Data
            domain_manager.reset_domain(channel_id) # Clears cache and files
            
            # Recreate Channel
            await self._recreate_channel(message)
            
        except asyncio.TimeoutError:
            try:
                await confirm_msg.delete()
                await message.channel.send("❌ 초기화 취소됨 (시간 초과).", delete_after=5)
            except: pass
    
    async def _recreate_channel(self, message: discord.Message) -> None:
        original = message.channel
        try:
            new_ch = await original.clone(reason="Session Reset")
            try: await new_ch.edit(position=original.position)
            except: pass
            
            await original.delete(reason="Session Reset (Old)")
            await new_ch.send("✨ **세션 초기화 완료.**\n새로운 타임라인이 시작되었습니다.\n`!준비` (`!ready`)를 입력하여 설정을 시작하세요.")
        except Exception as e:
            await self._fallback_purge(original, e)

    async def _fallback_purge(self, channel, error) -> None:
        await channel.send(f"⚠️ **채널 재생성 실패:** {error}\n{FALLBACK_PURGE_DELAY}초 후 메시지 청소를 시도합니다...")
        await asyncio.sleep(FALLBACK_PURGE_DELAY)
        try:
            deleted = await channel.purge(limit=None, check=lambda m: not m.pinned)
            await channel.send(f"🧹 **{len(deleted)}개의 메시지를 청소했습니다.**\n`!준비`를 입력하세요.")
        except Exception as e:
            await channel.send(f"❌ 청소 실패: {e}")

    async def execute_clear(self, message: discord.Message) -> None:
        """Clears chat messages but keeps session data."""
        try:
            await message.channel.send("🧹 **채팅 청소 중...**")
            await asyncio.sleep(1)
            deleted = await message.channel.purge(limit=None, check=lambda m: not m.pinned)
            await message.channel.send(f"✨ **청소 완료.** ({len(deleted)}개 삭제됨)", delete_after=5)
        except Exception as e:
            await message.channel.send(f"⚠️ 청소 실패: {e}")

    async def check_preparation(self, message: discord.Message) -> None:
        """Checks if session is ready to start (Lore/Rules)."""
        channel_id = str(message.channel.id)
        lore = domain_manager.get_lore(channel_id)
        
        ready = True
        msg = "🔍 **시스템 준비 확인**\n"
        
        if lore and lore.strip() and lore != "No Lore Saved": # Check default
             msg += "✅ 세계관(Lore) 로드됨\n"
        else:
             if len(lore) < 50:
                 msg += "❌ 세계관 미설정 (`!lore [내용/파일]` 필요)\n"
                 ready = False
             else:
                 msg += "✅ 세계관 로드됨\n"

        rules_mode = domain_manager.get_rules_mode(channel_id)
        mode_kr = "기본 (Default)" if rules_mode == "default" else "사용자 설정 (Custom)"
        msg += f"✅ 룰 설정: {mode_kr}\n"
        
        if ready:
            d = domain_manager.get_domain(channel_id)
            d["prepared"] = True
            domain_manager.save_domain(channel_id, d)
            
            msg += "\n✨ **준비 완료!** 다음 명령어로 시작하세요: `!가면 [이름]` -> `!시작`"
        else:
            d = domain_manager.get_domain(channel_id)
            d["prepared"] = False
            domain_manager.save_domain(channel_id, d)
            msg += "\n❗ **준비 미비.** 필수 항목을 확인해주세요."
            
        await message.channel.send(msg)

    async def start_session(self, message: discord.Message, client_genai, model_id: str) -> bool:
        channel_id = str(message.channel.id)
        d = domain_manager.get_domain(channel_id)
        
        if not d.get("prepared"):
            await message.channel.send("⚠️ 먼저 `!준비` 명령어로 상태를 확인해주세요.")
            return False
            
        if d["settings"].get("session_locked"):
            await message.channel.send("⚠️ 세션이 이미 진행 중입니다.")
            return False
            
        domain_manager.set_session_lock(channel_id, True)
        await message.channel.send("🎬 **세션 시작.**\n외부 개입이 차단되었습니다. 오프닝 생성 중...")
        return True

manager = SessionManager()
