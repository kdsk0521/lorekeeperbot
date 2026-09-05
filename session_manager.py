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
import config

logger = logging.getLogger(__name__)
# game_system might be needed if logic requires it, but for now mostly domain IO

# [2026-09-02] 5.0 → 30.0. 구 값은 **물리적으로 누르기 어려웠다** — 봇이 경고 메시지를 보내고
#   자기 이모지를 단 뒤 사용자가 그걸 보고 클릭하기까지가 5초 안에 끝나야 했다.
#   그래서 `!리셋`이 "안 먹는" 것처럼 보였다(레티어스 실사용 보고). 타임아웃 문구는 f-string이라
#   이 상수만 고치면 안내도 같이 따라온다.
RESET_CONFIRM_TIMEOUT = 30.0
RESET_CONFIRM_EMOJI = "💥"
FALLBACK_PURGE_DELAY = 2

class SessionManager:
    """Manages session lifecycle events."""
    
    async def execute_reset(self, message: discord.Message, client: discord.Client) -> None:
        """Fully resets the session by blowing up the channel. 이모지 확인 후 실행."""
        channel_id = str(message.channel.id)
        
        # 이모지 확인
        confirm_msg = await message.channel.send(
            "🧨 **[경고: 전체 초기화]**\n"
            "이 채널의 **모든 데이터**가 삭제되고 채널이 재생성됩니다.\n"
            f"{RESET_CONFIRM_EMOJI} 이모지를 눌러 {RESET_CONFIRM_TIMEOUT}초 내에 확정하십시오."
        )
        await confirm_msg.add_reaction(RESET_CONFIRM_EMOJI)
        
        def check(reaction, user):
            return (user == message.author and str(reaction.emoji) == RESET_CONFIRM_EMOJI and reaction.message.id == confirm_msg.id)
        
        try:
            await client.wait_for('reaction_add', timeout=RESET_CONFIRM_TIMEOUT, check=check)
        except asyncio.TimeoutError:
            try:
                await confirm_msg.delete()
                # [2026-09-02] delete_after=5 → 20. 취소 안내가 5초 만에 사라져서 사용자가
                #   "아무 반응이 없었다"고 읽었다 — 실패가 소리를 내지 않는 자리였다.
                await message.channel.send(
                    f"❌ 초기화 취소됨 ({RESET_CONFIRM_TIMEOUT:.0f}초 내 확인 없음). "
                    "다시 하려면 `!리셋`을 입력하고 💥 를 눌러주세요.", delete_after=20)
            except Exception as e:
                logger.debug(f"[무시됨] 초기화 취소 메시지 처리 실패: {e}")
            return
        
        # Reset Data
        domain_manager.reset_domain(channel_id) # Clears cache and files
        
        # Recreate Channel
        await self._recreate_channel(message)
    
    async def _recreate_channel(self, message: discord.Message) -> None:
        original = message.channel
        try:
            new_ch = await original.clone(reason="Session Reset")
            try:
                await new_ch.edit(position=original.position)
            except Exception as e:
                logger.debug(f"[무시됨] 채널 위치 복원 실패: {e}")
            
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
        """
        [Soft Reset] Clears chat messages AND resets session state (History/World/NPCs).
        Keeps Lore and Participants.
        """
        content = message.content.lower().strip()
        args = content.split()
        
        # Confirmation Check
        if len(args) < 2 or args[1] not in ['confirm', '확인', 'y', 'yes']:
            await message.channel.send(
                "⚠️ **[세션 초기화 경고]**\n"
                "`!클리어` 명령어는 단순 채팅 청소가 아닙니다.\n"
                "**현재 세션의 진행 상황(히스토리, 퀘스트, 월드 상태)을 모두 초기화합니다.**\n"
                "(단, 로어북과 참가자는 유지됩니다.)\n\n"
                "진행하시려면: `!클리어 확인` 또는 `!클리어 confirm` 입력."
            )
            return

        channel_id = str(message.channel.id)
        try:
            # 1. Soft Reset State
            domain_manager.reset_session_state(channel_id)
            
            # 2. Visual Wipe
            await message.channel.send("🧹 **세션 초기화 중... (데이터 리셋 + 채팅 청소)**")
            await asyncio.sleep(2)
            deleted = await message.channel.purge(limit=None, check=lambda m: not m.pinned)
            
            # 3. Success Message
            await message.channel.send(
                "✨ **세션이 리셋되었습니다.**\n"
                f"• 삭제됨: {len(deleted)}개 메시지, 히스토리, 진행 상황\n"
                "• 유지됨: 로어북, 참가자, 룰, 등록 NPC\n"
                "이제 **!시작**을 입력하여 새 이야기를 시작하세요.",
                delete_after=10
            )
            
            # Ensure bot is active again
            domain_manager.set_bot_active(channel_id, True)
            
        except Exception as e:
            await message.channel.send(f"⚠️ 초기화 실패: {e}")

    async def check_preparation(self, message: discord.Message) -> None:
        """Checks if session is ready to start (Lore/Rules)."""
        channel_id = str(message.channel.id)
        lore = domain_manager.get_lore(channel_id)
        
        ready = True
        msg = "🔍 **시스템 준비 확인**\n"
        
        if lore and lore.strip() and lore != "No Lore Saved" and lore != config.DEFAULT_LORE:
             msg += "✅ 세계관(Lore) 로드됨\n"
        else:
             msg += "❌ 세계관 미설정 (`!lore [내용/파일]` 필요)\n"
             ready = False

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
