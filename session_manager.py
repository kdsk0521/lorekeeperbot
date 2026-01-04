import discord
import asyncio

class SessionManager:
    """TRPG 세션의 흐름(리셋, 준비, 시작)을 제어합니다."""

    async def execute_reset(self, message, client, domain_manager, character_sheet):
        """데이터 초기화 및 채널 메시지 청소."""
        channel_id = str(message.channel.id)
        confirm_msg = await message.channel.send("🗑️ **데이터 리셋 확인:** 5초 내에 ⭕를 누르시면 모든 기록이 삭제되고 채널이 청소됩니다.")
        await confirm_msg.add_reaction("⭕")

        def check(reaction, user):
            return user == message.author and str(reaction.emoji) == "⭕" and reaction.message.id == confirm_msg.id

        try:
            await client.wait_for('reaction_add', timeout=5.0, check=check)
            domain_manager.reset_domain(channel_id)
            character_sheet.reset_npc_status(channel_id)
            try:
                await message.channel.purge(limit=100) # 메시지 관리 권한 필요
                await message.channel.send("✅ **리셋 완료.** 신규 시작을 위해 `!준비`를 입력하세요.")
            except discord.Forbidden:
                await message.channel.send("✅ **데이터만 삭제됨:** 봇에게 '메시지 관리' 권한이 없어 채팅 청소는 실패했습니다.")
        except asyncio.TimeoutError:
            await message.channel.send("❌ 초기화가 취소되었습니다.")

    async def check_preparation(self, message, domain_manager):
        """필수 로어/룰북 설정 여부를 확인합니다."""
        channel_id = str(message.channel.id)
        l, r = domain_manager.get_lore(channel_id), domain_manager.get_rules(channel_id)
        
        msg = "🔍 **시스템 점검 중...**\n"
        ready = True
        
        if not l or "장르" not in l: # 기본값이라도 있어야 함
            msg += "❌ **로어:** 설정 부족\n"; ready = False
        else: msg += "✅ **로어:** 준비됨\n"
        
        if not r:
            msg += "❌ **룰북:** 설정 부족\n"; ready = False
        else: msg += "✅ **룰북:** 준비됨\n"
        
        if ready:
            domain_manager.set_prepared(channel_id, True)
            msg += "\n✨ **활성화 완료!** 이제 `!가면` 등록 후 `!시작`이 가능합니다."
        else:
            domain_manager.set_prepared(channel_id, False)
            msg += "\n❗ **준비 실패:** 설정을 완료한 뒤 다시 입력하세요."
        
        await message.channel.send(msg)

    async def start_session(self, message, client_genai, model_id, domain_manager):
        """세션 잠금을 걸고 시작 여부를 반환합니다."""
        channel_id = str(message.channel.id)
        if not domain_manager.is_prepared(channel_id):
            await message.channel.send("❌ **시작 불가:** 먼저 `!준비` 과정을 통과해야 합니다.")
            return False
        
        domain_manager.set_session_lock(channel_id, True)
        await message.channel.send("🔒 **세션 잠금:** 게임이 공식적으로 시작되었습니다. 오프닝을 생성합니다...")
        return True

manager = SessionManager()