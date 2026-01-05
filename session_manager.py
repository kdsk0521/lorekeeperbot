import discord
import asyncio

class SessionManager:
    async def execute_reset(self, message, client, domain_manager, character_sheet):
        channel_id = str(message.channel.id)
        confirm_msg = await message.channel.send("🗑️ **데이터 리셋 확인:** 5초 내에 ⭕를 누르면 초기화됩니다.")
        await confirm_msg.add_reaction("⭕")

        def check(reaction, user):
            return user == message.author and str(reaction.emoji) == "⭕" and reaction.message.id == confirm_msg.id

        try:
            await client.wait_for('reaction_add', timeout=5.0, check=check)
            domain_manager.reset_domain(channel_id)
            character_sheet.reset_npc_status(channel_id)
            
            try:
                deleted = await message.channel.purge(limit=100, check=lambda m: not m.pinned)
                await message.channel.send(f"✅ **리셋 완료.** (메시지 {len(deleted)}개 삭제됨)\n`!준비`를 입력하세요.")
            except discord.Forbidden:
                await message.channel.send("✅ **데이터 초기화 완료.** (메시지 삭제 권한 없음)")
            except discord.HTTPException as e:
                await message.channel.send(f"✅ **데이터 초기화 완료.** (메시지 삭제 오류: {e})")

        except asyncio.TimeoutError:
            await message.channel.send("❌ 취소됨.")

    async def check_preparation(self, message, domain_manager):
        channel_id = str(message.channel.id)
        l, r = domain_manager.get_lore(channel_id), domain_manager.get_rules(channel_id)
        msg = "🔍 **시스템 점검**\n"
        ready = True
        if not l or "장르" not in l: msg += "❌ 로어 부족\n"; ready = False
        else: msg += "✅ 로어 OK\n"
        if not r: msg += "❌ 룰북 부족\n"; ready = False
        else: msg += "✅ 룰북 OK\n"
        
        if ready:
            domain_manager.set_prepared(channel_id, True)
            msg += "\n✨ **준비 완료!** `!가면` 설정 후 `!시작` 하세요."
        else:
            domain_manager.set_prepared(channel_id, False)
            msg += "\n❗ **준비 실패**"
        await message.channel.send(msg)

    async def start_session(self, message, client_genai, model_id, domain_manager):
        channel_id = str(message.channel.id)
        if not domain_manager.is_prepared(channel_id):
            await message.channel.send("❌ `!준비` 먼저 하세요.")
            return False
        domain_manager.set_session_lock(channel_id, True)
        await message.channel.send("🔒 **세션 시작 (잠금됨).** 오프닝 생성 중...")
        return True

manager = SessionManager()