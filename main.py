import discord
import os
import asyncio
import logging
from dotenv import load_dotenv
from google import genai
from google.genai import types

# 필수 모듈 임포트
try:
    import persona, domain_manager, character_sheet, input_handler, simulation_manager, memory_system, session_manager, world_manager, quest_manager
except ImportError as e:
    print(f"CRITICAL ERROR: 필수 모듈을 찾을 수 없습니다. {e}"); exit(1)

# 로깅 설정
logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')

load_dotenv()
DISCORD_TOKEN = os.getenv('DISCORD_TOKEN')
GEMINI_API_KEY = os.getenv('GEMINI_API_KEY')
MODEL_ID = os.getenv('GEMINI_MODEL_VERSION', 'gemini-2.0-flash-exp')

client_genai = genai.Client(api_key=GEMINI_API_KEY)
intents = discord.Intents.default(); intents.message_content = True
client_discord = discord.Client(intents=intents)

async def send_long_message(channel, text):
    last_msg = None
    if len(text) <= 2000: last_msg = await channel.send(text)
    else:
        chunks = [text[i:i+2000] for i in range(0, len(text), 2000)]
        for chunk in chunks: last_msg = await channel.send(chunk)
    return last_msg

@client_discord.event
async def on_ready():
    domain_manager.initialize_folders()
    print(f"--- Lorekeeper TRPG System Online ---")
    print(f"Logged in as: {client_discord.user.name}")
    print(f"Model: {MODEL_ID}")

@client_discord.event
async def on_message(message):
    if message.author == client_discord.user or not message.content: return

    try:
        channel_id = str(message.channel.id)
        
        # 0. 봇 전원 관리
        if message.content.strip() == "!off":
            domain_manager.set_bot_disabled(channel_id, True)
            return await message.channel.send("🔇 **봇 비활성화.**")
        if message.content.strip() == "!on":
            domain_manager.set_bot_disabled(channel_id, False)
            return await message.channel.send("🔊 **봇 활성화.**")
        if domain_manager.is_bot_disabled(channel_id): return

        # 1. 입력 분석
        parsed = input_handler.parse_input(message.content)
        if not parsed: return

        # 2. 게이트키퍼
        cmd_name = parsed.get('command') if parsed['type'] == 'command' else None
        is_ready = domain_manager.is_prepared(channel_id)
        
        # 준비 전에도 허용되는 명령어 (한국어 별칭 포함)
        allowed_pre_ready = [
            'ready', '준비', 'reset', '리셋', '초기화', 
            'lore', '로어', 'rule', '룰', 'mask', '가면', 'info', '정보'
        ]
        
        if not is_ready:
            if parsed['type'] == 'command' and cmd_name in allowed_pre_ready: pass
            else: return await message.channel.send("⚠️ 세션이 준비되지 않았습니다. `!로어`와 `!룰` 설정 후 `!준비`를 입력하세요.")

        system_trigger_msg = None 

        # 3. 명령어 처리
        if parsed['type'] == 'command':
            # --- 세션 흐름 관리 ---
            if cmd_name in ['reset', '리셋', '초기화']:
                return await session_manager.manager.execute_reset(message, client_discord, domain_manager, character_sheet)
            
            elif cmd_name in ['ready', '준비']:
                return await session_manager.manager.check_preparation(message, domain_manager)
            
            elif cmd_name in ['start', '시작']:
                if await session_manager.manager.start_session(message, client_genai, MODEL_ID, domain_manager):
                    system_trigger_msg = "[System: Generate a visceral opening scene for the campaign.]"
                else: return
            
            elif cmd_name in ['unlock', '잠금해제']:
                domain_manager.set_session_lock(channel_id, False)
                return await message.channel.send("🔓 **세션 잠금 해제:** 이제 새로운 플레이어가 참가할 수 있습니다.")
            
            elif cmd_name in ['next', '진행', '건너뛰기']:
                system_trigger_msg = "[System: Advance the narrative to the next meaningful event.]"
            
            # --- 참가자 상태 관리 ---
            elif cmd_name in ['afk', '잠수']:
                m = domain_manager.set_participant_status(channel_id, message.author.id, "afk")
                return await message.channel.send(f"💤 **[{m}]** 잠수 상태로 전환.")
            
            elif cmd_name in ['leave', '이탈', '퇴장']:
                m = domain_manager.set_participant_status(channel_id, message.author.id, "left", "자발적 이탈")
                return await message.channel.send(f"🚪 **[{m}]** 캐릭터가 대열을 이탈했습니다.")
            
            elif cmd_name in ['back', '복귀']:
                domain_manager.update_participant(channel_id, message.author)
                mask = domain_manager.get_user_mask(channel_id, message.author.id)
                return await message.channel.send(f"✨ **[{mask}]** 복귀 완료!")

            # --- 프로필 및 설정 ---
            elif cmd_name in ['mask', '가면']:
                if not parsed['content']: return await message.channel.send(f"🎭 현재 가면: {domain_manager.get_user_mask(channel_id, message.author.id)}")
                domain_manager.set_user_mask(channel_id, message.author.id, parsed['content'])
                return await message.channel.send(f"🎭 가면 설정 완료: {parsed['content']}")
            
            elif cmd_name in ['desc', '설명']:
                if not parsed['content']: return await message.channel.send(f"📝 묘사: {domain_manager.get_user_description(channel_id, message.author.id)}")
                domain_manager.set_user_description(channel_id, message.author.id, parsed['content'])
                return await message.channel.send(f"📝 외형 설명이 업데이트되었습니다.")
            
            elif cmd_name in ['info', '정보', '내정보']:
                mask = domain_manager.get_user_mask(channel_id, message.author.id)
                desc = domain_manager.get_user_description(channel_id, message.author.id)
                return await message.channel.send(f"👤 **캐릭터 프로필**\n- 이름: {mask}\n- 설정: {desc if desc else '내용 없음'}")

            # --- 로어 & 룰 주입 ---
            elif cmd_name in ['lore', '로어']:
                if not parsed['content']: return await message.channel.send(f"📜 **현재 로어:**\n{domain_manager.get_lore(channel_id)}")
                domain_manager.append_lore(channel_id, parsed['content'])
                return await message.channel.send("📜 로어가 추가되었습니다.")
            
            elif cmd_name in ['rule', '룰']:
                if not parsed['content']: return await message.channel.send(f"📘 **현재 룰:**\n{domain_manager.get_rules(channel_id)}")
                domain_manager.append_rules(channel_id, parsed['content'])
                return await message.channel.send("📘 규칙이 추가되었습니다.")

            # --- 퀘스트 & 로어 박제 ---
            elif cmd_name in ['상태', 'status']:
                return await message.channel.send(quest_manager.get_status_message(channel_id))
            
            elif cmd_name in ['퀘스트', 'quest']:
                content = parsed.get('content')
                if not content: return await message.channel.send("❌ 내용을 입력하세요.")
                board = domain_manager.get_quest_board(channel_id)
                board["active"].append(content)
                domain_manager.update_quest_board(channel_id, board)
                return await message.channel.send(f"⚔️ **새로운 퀘스트:** {content}")
                
            elif cmd_name in ['메모', 'memo']:
                content = parsed.get('content')
                if not content: return await message.channel.send(quest_manager.get_status_message(channel_id))
                if content == '기록': return await message.channel.send(quest_manager.get_archived_memos(channel_id))
                return await message.channel.send(quest_manager.add_memo(channel_id, content))

            elif cmd_name in ['완료', 'complete']:
                target = parsed.get('content')
                if not target: return await message.channel.send("❌ 번호를 입력하세요.")
                return await message.channel.send(quest_manager.resolve_quest_to_lore(channel_id, target))

            elif cmd_name in ['보관', 'archive']:
                target = parsed.get('content')
                if not target: return await message.channel.send("❌ 번호를 입력하세요.")
                await message.channel.send("⏳ 기록관(AI)이 사념의 가치를 평가 중입니다...")
                return await message.channel.send(quest_manager.archive_memo_with_ai(channel_id, target))

            elif cmd_name in ['연대기', 'lores']: # 박제된 로어 북 보기
                return await message.channel.send(quest_manager.get_lore_book(channel_id))
            
            else: pass

        # 4. 주사위 처리
        if parsed['type'] == 'dice': return await message.channel.send(parsed['content'])

        # 5. 세션 잠금 가드
        domain = domain_manager.get_domain(channel_id)
        if not domain['settings'].get('session_locked', False) and not system_trigger_msg:
            if parsed['type'] == 'chat': return

        # 6. AI 서사 생성 (NVC 분석 포함)
        async with message.channel.typing():
            domain_manager.update_participant(channel_id, message.author)
            lore, rules = domain_manager.get_lore(channel_id), domain_manager.get_rules(channel_id)
            world_ctx, obj_ctx = world_manager.get_world_context(channel_id), quest_manager.get_objective_context(channel_id)
            user_mask = domain_manager.get_user_mask(channel_id, message.author.id)
            
            current_action = system_trigger_msg if system_trigger_msg else f"[{user_mask}]: {parsed['content']}"
            
            # 히스토리 구성 및 NVC 분석
            history_list = domain.get('history', [])[-10:]
            history_text = "\n".join([f"{h['role']}: {h['content']}" for h in history_list]) + f"\nUser: {current_action}"
            nvc = await memory_system.analyze_context_nvc(client_genai, MODEL_ID, history_text, lore, rules)
            
            # 최종 프롬프트 조립
            full_prompt = (
                f"### WORLD & OBJECTIVES\n{world_ctx}\n{obj_ctx}\n\n"
                f"### NVC GUIDANCE\n{nvc}\n\n"
                f"### CURRENT ACTION\n{current_action}\n\n"
                f"GM으로서 서사를 이어가세요. 한국어로 응답하십시오."
            )

            # 페르소나 세션 생성 및 히스토리 수동 주입 (Risu 스타일)
            session = persona.create_risu_style_session(client_genai, MODEL_ID, lore, rules)
            for h in domain.get('history', []):
                role = "user" if h['role'] == "User" else "model"
                session.history.append(types.Content(role=role, parts=[types.Part(text=h['content'])]))
            
            response = await persona.generate_response_with_retry(client_genai, session, full_prompt)
            if response:
                last_msg = await send_long_message(message.channel, response)
                if last_msg: await last_msg.add_reaction("✅")
                domain_manager.append_history(channel_id, "User", current_action)
                domain_manager.append_history(channel_id, "Char", response)

    except Exception as e:
        logging.error(f"Error in on_message: {e}")

if __name__ == "__main__": client_discord.run(DISCORD_TOKEN)