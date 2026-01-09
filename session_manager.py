"""
Lorekeeper TRPG Bot - Session Manager Module
세션 초기화, 준비, 시작 등 세션 생명주기를 관리합니다.
"""

import discord
import asyncio
import logging
from typing import Optional

# =========================================================
# 상수 정의
# =========================================================
RESET_CONFIRM_TIMEOUT = 5.0  # 초
RESET_CONFIRM_EMOJI = "💥"
FALLBACK_PURGE_DELAY = 2  # 초


class SessionManager:
    """세션 관리를 담당하는 클래스입니다."""
    
    async def execute_reset(
        self,
        message: discord.Message,
        client: discord.Client,
        domain_manager,
        character_sheet
    ) -> None:
        """
        데이터를 리셋하고, 채널을 재생성(Nuke)하여 완벽히 초기화합니다.
        
        Args:
            message: Discord 메시지 객체
            client: Discord 클라이언트
            domain_manager: 도메인 매니저 모듈
            character_sheet: 캐릭터 시트 모듈
        """
        channel_id = str(message.channel.id)
        
        # 경고 메시지
        confirm_msg = await message.channel.send(
            "🧨 **[세션 완전 초기화 경고]**\n"
            "이 작업은 **채널을 폭파하고 재생성**하여 모든 대화 내용을 영구적으로 삭제합니다.\n"
            f"계속하려면 {RESET_CONFIRM_TIMEOUT:.0f}초 내에 {RESET_CONFIRM_EMOJI} 이모지를 누르세요."
        )
        await confirm_msg.add_reaction(RESET_CONFIRM_EMOJI)
        
        def check(reaction, user):
            return (
                user == message.author and
                str(reaction.emoji) == RESET_CONFIRM_EMOJI and
                reaction.message.id == confirm_msg.id
            )
        
        try:
            # 사용자 확인 대기
            await client.wait_for(
                'reaction_add',
                timeout=RESET_CONFIRM_TIMEOUT,
                check=check
            )
            
            # 1. 메모리 상의 데이터 초기화 (파일 삭제 전에 먼저)
            try:
                character_sheet.reset_npc_status(channel_id)
            except Exception as e:
                logging.warning(f"NPC 초기화 중 오류 (무시됨): {e}")
            
            # 2. 도메인 파일 삭제
            domain_manager.reset_domain(channel_id)
            
            # 3. 채널 재생성 시도
            await self._recreate_channel(message)
            
        except asyncio.TimeoutError:
            await self._cancel_reset(confirm_msg, message.channel)
    
    async def _recreate_channel(self, message: discord.Message) -> None:
        """채널을 재생성합니다."""
        original_channel = message.channel
        
        # 권한 사전 체크
        bot_member = original_channel.guild.me
        permissions = original_channel.permissions_for(bot_member)
        
        if not permissions.manage_channels:
            await self._fallback_purge(original_channel)
            return
        
        try:
            # 채널 복제
            new_channel = await original_channel.clone(
                reason="Lorekeeper Session Reset (Nuke)"
            )
            
            # 기존 채널 위치로 이동
            try:
                await new_channel.edit(position=original_channel.position)
            except discord.HTTPException:
                pass
            
            # 기존 채널 삭제
            await original_channel.delete(
                reason="Lorekeeper Session Reset (Old Channel)"
            )
            
            # 새 채널에 환영 메시지
            await new_channel.send(
                "✨ **세션이 완전히 초기화되었습니다.**\n"
                "새로운 타임라인이 시작됩니다.\n"
                "`!준비`를 입력하여 설정을 시작하세요."
            )
            
        except discord.Forbidden:
            await self._fallback_purge(original_channel)
        except discord.HTTPException as e:
            await original_channel.send(f"❌ **오류 발생:** {e}")
    
    async def _fallback_purge(self, channel) -> None:
        """채널 재생성 실패 시 메시지 삭제로 폴백합니다."""
        await channel.send(
            "⚠️ **[권한 부족]** 봇에게 '채널 관리' 권한이 없어 "
            "채널을 재생성할 수 없습니다.\n대신 메시지 청소를 시도합니다."
        )
        await asyncio.sleep(FALLBACK_PURGE_DELAY)
        
        try:
            deleted = await channel.purge(
                limit=None,
                check=lambda m: not m.pinned
            )
            await channel.send(
                f"🧹 **청소 완료.** (메시지 {len(deleted)}개 삭제됨)\n"
                "`!준비`를 입력하세요."
            )
        except discord.Forbidden:
            await channel.send("❌ 메시지 삭제 권한도 없습니다.")
        except discord.HTTPException as e:
            await channel.send(f"❌ **오류:** {e}")
    
    async def _cancel_reset(self, confirm_msg, channel) -> None:
        """리셋을 취소합니다."""
        try:
            await confirm_msg.delete()
            await channel.send(
                "❌ **리셋 취소됨:** 시간이 초과되었습니다.",
                delete_after=5
            )
        except discord.HTTPException:
            pass
    
    async def check_preparation(self, message: discord.Message, domain_manager) -> None:
        """
        세션 시작 전 필수 요소가 준비되었는지 확인합니다.
        룰이 없으면 기본 룰을 자동으로 적용합니다.
        
        Args:
            message: Discord 메시지 객체
            domain_manager: 도메인 매니저 모듈
        """
        channel_id = str(message.channel.id)
        
        lore = domain_manager.get_lore(channel_id)
        rules = domain_manager.get_rules(channel_id)
        summary = domain_manager.get_lore_summary(channel_id)
        
        msg = "🔍 **시스템 점검**\n"
        ready = True
        
        # 로어 확인 (로어는 반드시 사용자가 설정해야 함)
        has_lore = (lore and lore.strip()) or summary
        if has_lore:
            msg += "✅ 로어 OK\n"
        else:
            msg += "❌ 로어 부족 (`!로어`로 세계관을 설정하세요)\n"
            ready = False
        
        # 룰 확인 - 3가지 모드 표시
        rules_mode = domain_manager.get_rules_mode(channel_id)
        
        if rules_mode == "custom":
            msg += "✅ 룰북 OK (📙 완전 커스텀)\n"
        elif rules_mode == "hybrid":
            msg += "✅ 룰북 OK (📘 기본 + 커스텀)\n"
        else:
            msg += "✅ 룰북 OK (📗 기본 규칙)\n"
        
        # 결과 처리
        if ready:
            domain_manager.set_prepared(channel_id, True)
            msg += "\n✨ **준비 완료!** `!가면` 설정 후 `!시작` 하세요."
            if rules_mode == "default":
                msg += "\n💡 _커스텀 룰: `!룰 [내용]` 추가 또는 `!룰 [파일]` 업로드_"
        else:
            domain_manager.set_prepared(channel_id, False)
            msg += "\n❗ **준비 실패** - `!로어 [파일]`로 세계관을 먼저 설정해주세요."
        
        await message.channel.send(msg)
    
    async def start_session(
        self,
        message: discord.Message,
        client_genai,
        model_id: str,
        domain_manager
    ) -> bool:
        """
        세션을 시작하고 외부인의 접근을 차단합니다.
        
        Args:
            message: Discord 메시지 객체
            client_genai: Gemini 클라이언트
            model_id: 모델 ID
            domain_manager: 도메인 매니저 모듈
        
        Returns:
            세션 시작 성공 여부
        """
        channel_id = str(message.channel.id)
        
        # 준비 완료 여부 확인
        if not domain_manager.is_prepared(channel_id):
            await message.channel.send("⚠️ 먼저 `!준비`를 완료해주세요.")
            return False
        
        # 이미 시작된 세션인지 확인
        domain = domain_manager.get_domain(channel_id)
        if domain['settings'].get('session_locked', False):
            await message.channel.send("⚠️ 이미 세션이 진행 중입니다.")
            return False
        
        # 세션 잠금
        domain_manager.set_session_lock(channel_id, True)
        
        await message.channel.send(
            "🎬 **세션이 시작됩니다.**\n"
            "외부인의 개입이 차단됩니다. (`!잠금해제`로 풀 수 있습니다.)\n"
            "AI가 오프닝을 생성합니다..."
        )
        return True


# 싱글톤 인스턴스 생성
manager = SessionManager()
