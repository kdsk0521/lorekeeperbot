import discord
import asyncio

class SessionManager:
    async def execute_reset(self, message, client, domain_manager, character_sheet):
        """
        데이터를 리셋하고, 채널을 재생성(Nuke)하여 시각적으로도 완벽히 초기화합니다.
        """
        channel_id = str(message.channel.id)
        
        # 경고 메시지 (이모지 변경: 다이너마이트)
        confirm_msg = await message.channel.send(
            "🧨 **[세션 완전 초기화 경고]**\n"
            "이 작업은 **채널을 폭파하고 재생성**하여 모든 대화 내용을 영구적으로 삭제합니다.\n"
            "계속하려면 5초 내에 💥 (충돌/폭발) 이모지를 누르세요."
        )
        await confirm_msg.add_reaction("💥")

        def check(reaction, user):
            return user == message.author and str(reaction.emoji) == "💥" and reaction.message.id == confirm_msg.id

        try:
            # 5초 대기
            await client.wait_for('reaction_add', timeout=5.0, check=check)
            
            # 1. 내부 데이터 파일 삭제 (구 채널 ID 기준)
            domain_manager.reset_domain(channel_id)
            character_sheet.reset_npc_status(channel_id)
            
            # 2. 채널 재생성 시도 (Nuke)
            original_channel = message.channel
            
            try:
                # 채널 복제 (설정, 권한, 토픽 유지)
                new_channel = await original_channel.clone(reason="Lorekeeper Session Reset (Nuke)")
                
                # 기존 채널 위치로 이동 시도 (순서 유지)
                try:
                    await new_channel.edit(position=original_channel.position)
                except:
                    pass
                
                # 기존 채널 삭제
                await original_channel.delete(reason="Lorekeeper Session Reset (Old Channel)")
                
                # 새 채널에 환영 메시지 전송
                await new_channel.send(
                    "✨ **세션이 완전히 초기화되었습니다.**\n"
                    "새로운 타임라인이 시작됩니다.\n"
                    "`!준비`를 입력하여 설정을 시작하세요."
                )
                
            except discord.Forbidden:
                # 권한이 없을 경우: 기존 방식(메시지 삭제)으로 폴백
                await message.channel.send("⚠️ **[권한 부족]** 봇에게 '채널 관리' 권한이 없어 채널을 재생성할 수 없습니다.\n대신 메시지 청소를 시도합니다.")
                await asyncio.sleep(2)
                deleted = await message.channel.purge(limit=None, check=lambda m: not m.pinned) # limit=None으로 가능한 전부 삭제
                await message.channel.send(f"🧹 **청소 완료.** (메시지 {len(deleted)}개 삭제됨)\n`!준비`를 입력하세요.")
                
            except discord.HTTPException as e:
                await message.channel.send(f"❌ **오류 발생:** {e}")

        except asyncio.TimeoutError:
            try:
                await confirm_msg.delete()
                await message.channel.send("❌ **리셋 취소됨:** 시간이 초과되었습니다.", delete_after=5)
            except:
                pass

    async def check_preparation(self, message, domain_manager):
        channel_id = str(message.channel.id)
        l, r = domain_manager.get_lore(channel_id), domain_manager.get_rules(channel_id)
        msg = "🔍 **시스템 점검**\n"
        ready = True
        
        # 로어 확인 (요약본 혹은 원본)
        summary = domain_manager.get_lore_summary(channel_id)
        if not l and not summary: msg += "❌ 로어 부족\n"; ready = False
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
        if not domain_manager.is_prepared(str(message.channel.id)):
            await message.channel.send("⚠️ 먼저 `!준비`를 완료해주세요.")
            return False
        
        # 이미 시작된 세션인지 확인
        if domain_manager.get_domain(str(message.channel.id))['settings'].get('session_locked', False):
             await message.channel.send("⚠️ 이미 세션이 진행 중입니다.")
             return False

        # 세션 잠금
        domain_manager.set_session_lock(str(message.channel.id), True)
        
        await message.channel.send(
            "🎬 **세션이 시작됩니다.**\n"
            "외부인의 개입이 차단됩니다. (`!잠금해제`로 풀 수 있습니다.)\n"
            "AI가 오프닝을 생성합니다..."
        )
        return True

# 인스턴스 생성
manager = SessionManager()