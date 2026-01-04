import discord
import asyncio
import os

class SessionManager:
    """
    TRPG 세션의 진행 단계, 초기화, 파일 내보내기 등을 관리합니다.
    """

    def __init__(self):
        self.ready_status = {} 

    # [수정] client 인자 추가
    async def execute_reset(self, message, client, domain_manager, character_sheet):
        """
        [기능 1] 채널 내용을 완전히 비우고(Purge), 데이터를 초기화합니다.
        """
        channel_id = str(message.channel.id)
        
        # 1. 안전장치: 확인 절차
        confirm_msg = await message.channel.send(
            "🗑️ **경고:** 이 채널의 **모든 채팅 내역**과 **세션 데이터(로어, 룰, 캐릭터)**가 영구 삭제됩니다.\n"
            "진행하려면 5초 내에 ⭕ 이모지를 누르세요."
        )
        await confirm_msg.add_reaction("⭕")

        def check(reaction, user):
            return user == message.author and str(reaction.emoji) == "⭕" and reaction.message.id == confirm_msg.id

        try:
            # [수정] message.channel.bot 대신 전달받은 client 객체 사용
            await client.wait_for('reaction_add', timeout=5.0, check=check)
        except asyncio.TimeoutError:
            await message.channel.send("❌ 초기화가 취소되었습니다.")
            return

        # 2. 데이터 리셋
        domain_manager.reset_domain(channel_id)
        character_sheet.reset_npc_status(channel_id)
        
        if channel_id in self.ready_status:
            del self.ready_status[channel_id]

        # 3. [핵심] 채널 청소 (모든 메시지 삭제)
        try:
            await message.channel.send("🧹 **채널 청소 중...** (잠시만 기다려주세요)")
            await asyncio.sleep(1) 
            await message.channel.purge(limit=None)
        except discord.Forbidden:
            await message.channel.send("⚠️ **권한 부족:** 봇에게 '메시지 관리' 권한이 없어 채팅 청소에 실패했습니다.\n(데이터는 초기화되었습니다.)")
            return
        except Exception as e:
            await message.channel.send(f"⚠️ 청소 중 오류 발생: {e}")

        # 4. 초기 가이드 전송
        embed = discord.Embed(title="🎲 세션 초기화 완료", description="새로운 모험을 시작할 준비가 되었습니다.", color=0x95a5a6)
        embed.add_field(name="1단계: 준비", value="`!준비`를 입력하여 설정을 점검하세요.\n(로어/룰북이 없으면 기본값으로 자동 설정됩니다.)", inline=False)
        embed.add_field(name="2단계: 가면", value="플레이어들은 `!가면 [이름]...`으로 참가하세요.", inline=False)
        embed.add_field(name="3단계: 시작", value="모든 준비가 끝나면 `!시작`을 입력하세요.", inline=False)
        await message.channel.send(embed=embed)

    async def export_data(self, message, domain_manager):
        """
        [신규 기능] 현재 세션의 로어(Lore) 파일을 디스코드에 업로드합니다.
        """
        channel_id = str(message.channel.id)
        lore_path = domain_manager.get_lore_file_path(channel_id)
        
        if not os.path.exists(lore_path):
            await message.channel.send("❌ 저장된 로어 파일이 없습니다.")
            return

        try:
            file = discord.File(lore_path, filename=f"Lore_History_{channel_id}.txt")
            await message.channel.send("📜 **현재까지 기록된 로어(역사) 파일입니다.**", file=file)
        except Exception as e:
            await message.channel.send(f"⚠️ 파일 전송 실패: {e}")

    async def check_preparation(self, message, domain_manager):
        channel_id = str(message.channel.id)
        
        lore = domain_manager.get_lore(channel_id)
        rules = domain_manager.get_rules(channel_id)
        
        msg_log = ""
        
        if not lore or lore == "Dark Fantasy World: A grim realm where survival is the only virtue.":
             if not lore:
                domain_manager.set_lore(channel_id, "Dark Fantasy World: A grim realm where survival is the only virtue.")
                msg_log += "📜 **로어:** 설정된 파일이 없어 [기본 다크 판타지] 설정을 적용했습니다.\n"
             else:
                msg_log += "📜 **로어:** 기존 설정(또는 기본값)이 확인되었습니다.\n"
        else:
            msg_log += "📜 **로어:** 사용자 설정이 확인되었습니다.\n"

        if not rules or rules == "Basic TRPG Rules: D20 system, Success check.":
            if not rules:
                domain_manager.set_rules(channel_id, "Basic TRPG Rules: D20 system, Success check.")
                msg_log += "📘 **룰북:** 설정된 파일이 없어 [기본 D20 규칙]을 적용했습니다.\n"
            else:
                msg_log += "📘 **룰북:** 기존 설정(또는 기본값)이 확인되었습니다.\n"
        else:
            msg_log += "📘 **룰북:** 사용자 설정이 확인되었습니다.\n"

        participants = domain_manager.get_active_participants_summary(channel_id)
        if not participants:
            msg_log += "\n⚠️ **주의:** 아직 등록된 플레이어(가면)가 없습니다.\n`!가면 [이름] [설명]`으로 최소 1명 이상 참가해야 시작할 수 있습니다."
            self.ready_status[channel_id] = False
        else:
            msg_log += f"\n🎭 **참가자:** {participants}\n모든 준비가 완료되었습니다! `!시작`을 입력하세요."
            self.ready_status[channel_id] = True

        await message.channel.send(msg_log)

    def validate_command_flow(self, channel_id, command, domain_manager):
        domain = domain_manager.get_domain(channel_id)
        is_locked = domain.get('is_locked', False)

        if command in ['start', '시작', '세션시작']:
            if is_locked:
                return False, "⚠️ 이미 세션이 진행 중입니다."
            if not self.ready_status.get(channel_id, False):
                return False, "❌ **시작 불가**: 먼저 `!준비`를 입력하여 설정을 점검해주세요."
            return True, None

        if command in ['lore', '로어', 'rule', '룰', '룰북']:
            return True, None

        if command in ['join', '참가', 'mask', '가면']:
            if is_locked:
                return False, "🔒 세션이 시작되어 신규 참가가 제한됩니다. (참가하려면 `!잠금` 해제 필요)"
            return True, None

        return True, None

    async def start_session(self, message, client_genai, model_id, domain_manager):
        channel_id = str(message.channel.id)
        
        domain = domain_manager.get_domain(channel_id)
        if not domain.get('is_locked', False):
            domain_manager.toggle_session_lock(channel_id)
            
        await message.channel.send("🔒 **세션이 시작되었습니다.** (신규 참가 차단됨)\n📜 **AI가 오프닝을 작성 중입니다...**")

        lore = domain_manager.get_lore(channel_id)
        rules = domain_manager.get_rules(channel_id)
        participants = domain_manager.get_active_participants_summary(channel_id)

        prompt = f"""
        당신은 TRPG 게임 마스터입니다.
        현재 등록된 [세계관]과 [룰북], [참가자] 정보를 바탕으로 게임의 **오프닝 장면**을 서술하세요.
        
        [절대 규칙]
        1. 제공된 세계관(Lore)과 룰(Rules) 이외의 설정을 임의로 창조하지 마시오.
        2. 참가자들이 상황을 인식하고 첫 행동을 결정할 수 있도록 유도하시오.
        
        [세계관(Lore)]
        {lore}
        
        [룰북(Rules)]
        {rules}
        
        [참가자 명단]
        {participants}
        """

        try:
            async with message.channel.typing():
                response = await asyncio.to_thread(
                    client_genai.models.generate_content,
                    model=model_id,
                    contents=prompt
                )
                text = response.text
                
                if len(text) > 2000:
                    chunks = [text[i:i+2000] for i in range(0, len(text), 2000)]
                    for chunk in chunks:
                        await message.channel.send(chunk)
                else:
                    await message.channel.send(text)
                    
        except Exception as e:
            await message.channel.send(f"⚠️ 오프닝 생성 오류: {e}")

# 싱글톤 인스턴스
manager = SessionManager()