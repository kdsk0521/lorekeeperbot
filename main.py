import discord
import os
import asyncio
import logging
from dotenv import load_dotenv
from google import genai
from google.genai import types

try:
    import persona
    import domain_manager
    import character_sheet
    import input_handler
    import simulation_manager
    import memory_system
    import session_manager
    import world_manager
    import quest_manager
except ImportError as e:
    print(f"CRITICAL ERROR: 필수 모듈을 찾을 수 없습니다. {e}")
    exit(1)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[logging.FileHandler("bot_runtime.log", encoding='utf-8'), logging.StreamHandler()]
)

load_dotenv()
DISCORD_TOKEN = os.getenv('DISCORD_TOKEN')
GEMINI_API_KEY = os.getenv('GEMINI_API_KEY')
MODEL_ID = os.getenv('GEMINI_MODEL_VERSION', 'gemini-2.0-flash-exp')

if not DISCORD_TOKEN or not GEMINI_API_KEY:
    print("Error: .env 파일에서 토큰 정보를 찾을 수 없습니다.")
    exit(1)

client_genai = genai.Client(api_key=GEMINI_API_KEY)
intents = discord.Intents.default()
intents.message_content = True
client_discord = discord.Client(intents=intents)

@client_discord.event
async def on_ready():
    logging.info(f'로그인 성공: {client_discord.user}')
    domain_manager.initialize_folders()
    character_sheet.initialize_folders()
    await client_discord.change_presence(activity=discord.Game(name="TRPG 시나리오 진행"))

async def send_long_message(channel, text):
    if len(text) <= 2000:
        await channel.send(text)
    else:
        chunks = [text[i:i+2000] for i in range(0, len(text), 2000)]
        for chunk in chunks:
            await channel.send(chunk)

@client_discord.event
async def on_message(message):
    if message.author == client_discord.user: return
    if not message.content: return

    try:
        channel_id = str(message.channel.id)
        
        # [봇 비활성화 체크]
        if message.content.strip() == "!off":
            domain_manager.set_bot_disabled(channel_id, True)
            await message.channel.send("🔇 **봇 비활성화:** 이제 이 채널에서 반응하지 않습니다. (`!on`으로 활성화)")
            return
        if message.content.strip() == "!on":
            domain_manager.set_bot_disabled(channel_id, False)
            await message.channel.send("🔊 **봇 활성화:** 다시 반응을 시작합니다.")
            return
        if domain_manager.is_bot_disabled(channel_id):
            return

        domain = domain_manager.get_domain(channel_id)
        domain_manager.update_participant(channel_id, message.author)

        parsed_input = input_handler.parse_input(message.content)
        if not parsed_input: return

        system_trigger_msg = None 

        if parsed_input['type'] == 'command':
            cmd = parsed_input['command']
            args = parsed_input['content']

            is_valid, err_msg = session_manager.manager.validate_command_flow(channel_id, cmd, domain_manager)
            if not is_valid:
                await message.channel.send(err_msg)
                return

            if cmd == 'reset':
                # [수정] execute_reset 호출 시 client_discord 전달
                await session_manager.manager.execute_reset(message, client_discord, domain_manager, character_sheet)
                return
            
            elif cmd in ['export', '기록', '저장']:
                await session_manager.manager.export_data(message, domain_manager)
                return

            elif cmd in ['ready', '준비']:
                await session_manager.manager.check_preparation(message, domain_manager)
                return

            elif cmd in ['start', '시작', '세션시작']:
                await session_manager.manager.start_session(message, client_genai, MODEL_ID, domain_manager)
                return
            
            # ... (나머지 명령어 처리 부분은 기존과 동일) ...
            elif cmd in ['mode', '모드']:
                new_mode = domain_manager.toggle_mode(channel_id)
                status = "수동(Manual) 📝" if new_mode == "manual" else "자동(Auto) ⚡"
                await message.channel.send(f"⚙️ **모드 변경:** {status}")
                return
            elif cmd in ['lock', '잠금']:
                is_locked = domain_manager.toggle_session_lock(channel_id)
                status = "🔒 잠금" if is_locked else "🔓 해제"
                await message.channel.send(f"🛡️ **세션 상태:** {status}")
                return
            elif cmd in ['join', '참가', '복귀']:
                 mask, is_new = domain_manager.join_participant(channel_id, message.author.id, message.author.display_name)
                 msg = f"👋 **환영합니다!** '{mask}'님." if is_new else f"👋 **어서오세요!** '{mask}'님."
                 await message.channel.send(msg)
                 return
            elif cmd in ['leave', '이탈', '휴식']:
                 name, mask = domain_manager.leave_participant(channel_id, message.author.id)
                 if name: await message.channel.send(f"💤 **{mask}**님이 잠시 자리를 비웁니다.")
                 return
            elif cmd in ['delete', '삭제', '탈퇴']:
                 name, mask = domain_manager.remove_participant(channel_id, message.author.id)
                 if name: await message.channel.send(f"👋 **{mask}**님이 데이터를 삭제하고 떠났습니다.")
                 return
            elif cmd in ['mask', '가면']:
                 domain_manager.set_user_mask(channel_id, message.author.id, args)
                 await message.channel.send(f"🎭 **{args}**(으)로 활동합니다.")
                 return
            elif cmd in ['lore', '로어']:
                if message.attachments:
                    text = (await message.attachments[0].read()).decode('utf-8')
                    domain_manager.set_lore(channel_id, text)
                    await message.channel.send("📜 **로어 파일 로드 완료.**")
                elif args:
                    domain_manager.append_lore(channel_id, args)
                    await message.channel.send("📜 **로어 내용 추가됨.**")
                return
            elif cmd in ['rule', '룰', '룰북']:
                if message.attachments:
                    text = (await message.attachments[0].read()).decode('utf-8')
                    domain_manager.set_rules(channel_id, text)
                    await message.channel.send("📘 **룰북 파일 로드 완료.**")
                elif args:
                    domain_manager.append_rules(channel_id, args)
                    await message.channel.send("📘 **룰북 내용 추가됨.**")
                return
            elif cmd in ['system', '시스템']:
                 valid_systems = ["standard", "dnd", "milestone"]
                 if args in valid_systems:
                     domain_manager.set_growth_system(channel_id, args)
                     await message.channel.send(f"⚙️ **성장 시스템 변경:** {args}")
                 else:
                     await message.channel.send(f"❌ 사용 가능: {', '.join(valid_systems)}")
                 return
            elif cmd == '스탯': 
                 try:
                     parts = args.split()
                     if len(parts) >= 2:
                         stat_name = parts[0]
                         value = parts[1]
                         domain_manager.set_user_stat(channel_id, message.author.id, stat_name, value)
                         await message.channel.send(f"📊 **스탯 변경:** {stat_name} = {value}")
                 except: await message.channel.send("오류")
                 return
            elif cmd == '레벨설정':
                 if args:
                     new_val = domain_manager.set_user_level(channel_id, message.author.id, args)
                     await message.channel.send(f"🆙 **레벨 변경:** {new_val}")
                 return
            elif cmd == '보상': 
                 try:
                     xp_amount = int(args)
                     user_data = domain_manager.get_user_data(channel_id, message.author.id)
                     current_sys = domain_manager.get_growth_system(channel_id)
                     new_data, msg, is_levelup = simulation_manager.gain_experience(user_data, xp_amount, current_sys)
                     domain_manager.update_user_data(channel_id, message.author.id, new_data)
                     await message.channel.send(msg)
                 except: return
            elif cmd == '영지':
                 fief = domain_manager.get_fief_data(channel_id)
                 status = (f"🏰 **{fief['name']} 현황**\n"
                           f"💰 금화: {fief['gold']} | 🍞 식량: {fief['supplies']}\n"
                           f"👥 인구: {fief['population']} | 🛡️ 치안: {fief['security']}\n"
                           f"🏗️ 건물: {', '.join(fief['buildings']) if fief['buildings'] else '없음'}")
                 await message.channel.send(status)
                 return
            elif cmd == '건설':
                 if not args:
                     await message.channel.send("Usage: `!건설 [건물명]` (룰북 기반 자동 건설)")
                     return
                 parsed_input['type'] = 'chat'
                 parsed_input['content'] = f"(System Request: Build '{args}' based on Rules)"
            elif cmd == '징수':
                 fief = domain_manager.get_fief_data(channel_id)
                 new_fief, msg = simulation_manager.collect_taxes(fief)
                 domain_manager.update_fief_data(channel_id, new_fief)
                 await message.channel.send(msg)
                 system_trigger_msg = "[System Event: Taxes collected.]"
            elif cmd == '상태':
                 user_data = domain_manager.get_user_data(channel_id, message.author.id)
                 stats = user_data.get('stats', {})
                 info = f"📊 **{user_data['mask']}** (Lv.{user_data.get('level',1)})\n📈 스탯: {stats}"
                 await message.channel.send(info)
                 return
            elif cmd in ['inv', '인벤', '가방']:
                user_data = domain_manager.get_user_data(channel_id, message.author.id)
                inv = user_data.get("inventory", {})
                if not inv:
                    await message.channel.send(f"🎒 **{user_data['mask']}**님의 가방은 비어있습니다.")
                else:
                    items_str = "\n".join([f"- {k}: {v}개" for k, v in inv.items()])
                    await message.channel.send(f"🎒 **{user_data['mask']}**님의 소지품:\n{items_str}")
                return
            elif cmd in ['item', '아이템']:
                try:
                    action, item_name, count_str = args.split()
                    count = int(count_str)
                    if action in ["획득", "get", "add"]: act_code = "add"
                    elif action in ["사용", "use", "remove", "버림"]: act_code = "remove"
                    else: return
                    user_data = domain_manager.get_user_data(channel_id, message.author.id)
                    new_data, msg = simulation_manager.update_inventory(user_data, act_code, item_name, count)
                    domain_manager.update_user_data(channel_id, message.author.id, new_data)
                    await message.channel.send(msg)
                except: pass
                return
            elif cmd == '훈련':
                 user_data = domain_manager.get_user_data(channel_id, message.author.id)
                 new_data, msg, status = simulation_manager.train_character(user_data, args)
                 domain_manager.update_user_data(channel_id, message.author.id, new_data)
                 await message.channel.send(msg)
                 system_trigger_msg = f"[System Event: Character '{user_data['mask']}' trained '{args}'.]"
            elif cmd == '휴식':
                 user_data = domain_manager.get_user_data(channel_id, message.author.id)
                 new_data, msg = simulation_manager.rest_character(user_data)
                 domain_manager.update_user_data(channel_id, message.author.id, new_data)
                 await message.channel.send(msg)
                 system_trigger_msg = f"[System Event: Character '{user_data['mask']}' took a rest.]"
            elif cmd == '호감도':
                 try:
                     target, val_str = args.split()
                     val = int(val_str)
                     user_data = domain_manager.get_user_data(channel_id, message.author.id)
                     new_data, msg = simulation_manager.modify_relationship(user_data, target, val)
                     domain_manager.update_user_data(channel_id, message.author.id, new_data)
                     await message.channel.send(msg)
                 except: return
            
            # --- 퀘스트/메모/위기/소문 등 나머지 명령어 처리 (기존 코드 유지) ---
            elif cmd in ['quest', '퀘스트', '목표']:
                if not args:
                    await message.channel.send(quest_manager.get_status_message(channel_id))
                elif args.startswith("완료"):
                    try:
                        idx = args.replace("완료", "").strip()
                        msg = quest_manager.complete_quest(channel_id, idx)
                        await message.channel.send(msg)
                    except: await message.channel.send("Usage: `!퀘스트 완료 [번호]`")
                elif args.startswith("추가"):
                    content = args.replace("추가", "").strip()
                    msg = quest_manager.add_quest(channel_id, content)
                    await message.channel.send(msg)
                else:
                    msg = quest_manager.add_quest(channel_id, args)
                    await message.channel.send(msg)
                return

            elif cmd in ['memo', '메모']:
                if not args:
                    await message.channel.send(quest_manager.get_status_message(channel_id))
                elif args.startswith("보관") or args.startswith("삭제") or args.startswith("완료"):
                    try:
                        idx = args.split()[1]
                        msg = quest_manager.archive_memo(channel_id, idx)
                        await message.channel.send(msg)
                    except: await message.channel.send("Usage: `!메모 보관 [번호]`")
                else:
                    content = args.replace("추가", "").strip()
                    msg = quest_manager.add_memo(channel_id, content)
                    await message.channel.send(msg)
                return

            elif cmd in ['doom', '위기', '긴장']:
                if args:
                    try:
                        amount = int(args)
                        msg = world_manager.change_doom(channel_id, amount)
                        await message.channel.send(msg)
                        system_trigger_msg = f"[System Event: Doom changed by {amount}]"
                    except: await message.channel.send("Usage: `!위기 +10`")
                else:
                    ctx = world_manager.get_world_context(channel_id)
                    await message.channel.send(f"🌍 **현재 세계 상태**\n{ctx}")
                if not system_trigger_msg: return

            elif cmd in ['rumor', '소문']:
                parsed_input['type'] = 'chat'
                parsed_input['content'] = "(System Request: Generate a rumored story based on current Doom level and Lore)"
                
            elif cmd in ['next', '진행', 'go']:
                time_msg = world_manager.advance_time(channel_id)
                await message.channel.send(time_msg)
            
            else: return

        if parsed_input['type'] == 'dice':
            await message.channel.send(parsed_input['content'])
            return

        # ---------------------------------------------------------
        # [2] AI 응답 & NVC 처리 (AI Response)
        # ---------------------------------------------------------
        mode = domain_manager.get_mode(channel_id)
        is_force_next = (parsed_input['type'] == 'command' and parsed_input['command'] in ['next', '진행', 'go'])

        if mode == 'manual' and not is_force_next and not system_trigger_msg and parsed_input['content'].find("System Request") == -1:
            style_tag = input_handler.analyze_style(parsed_input['content'])
            formatted = f"[{style_tag}] {parsed_input['content']}"
            domain_manager.add_to_buffer(channel_id, message.author.id, formatted)
            await message.add_reaction("✍️") 
            return

        async with message.channel.typing():
            lore_text = domain_manager.get_lore(channel_id)
            rule_text = domain_manager.get_rules(channel_id)
            ai_notes = domain_manager.get_ai_notes(channel_id)
            active_users_str = domain_manager.get_active_participants_summary(channel_id)
            
            world_context_str = world_manager.get_world_context(channel_id)
            objective_context_str = quest_manager.get_objective_context(channel_id)

            if system_trigger_msg:
                final_input = system_trigger_msg
            elif is_force_next:
                buffered_text = domain_manager.flush_buffer(channel_id)
                final_input = buffered_text if buffered_text else "(No user input, proceed story)"
            else:
                user_mask = domain_manager.get_user_mask(channel_id, message.author.id)
                if parsed_input['content'].startswith("(System Request"):
                    final_input = parsed_input['content']
                else:
                    style_tag = input_handler.analyze_style(parsed_input['content'])
                    final_input = f"[{user_mask}] ({style_tag}): {parsed_input['content']}"

            recent_history_list = domain.get('history', [])[-5:]
            recent_history_text = "\n".join([f"{h['role']}: {h['content']}" for h in recent_history_list])
            recent_history_text += f"\n(Current Input): {final_input}"
            
            nvc_result = await memory_system.analyze_context_nvc(client_genai, MODEL_ID, recent_history_text, lore_text, rule_text)
            
            system_action_result = ""
            if nvc_result and nvc_result.get("SystemAction") and nvc_result.get("SystemAction") != "None":
                action_line = nvc_result["SystemAction"]
                
                # ... (나머지 SystemAction 처리 로직은 기존과 동일) ...
                if "Construct" in action_line:
                    try:
                        parts = action_line.split('|')
                        b_name, b_cost, b_effect = "Unknown", 0, ""
                        for p in parts:
                            p = p.strip()
                            if p.startswith("Name:"): b_name = p.replace("Name:", "").strip()
                            elif p.startswith("Cost:"): 
                                cost_str = p.replace("Cost:", "").strip().replace("G", "").replace(",", "")
                                b_cost = int(cost_str)
                            elif p.startswith("Effect:"): b_effect = p.replace("Effect:", "").strip()
                        fief = domain_manager.get_fief_data(channel_id)
                        new_fief, msg, success = simulation_manager.build_facility(fief, b_name, b_cost, b_effect)
                        domain_manager.update_fief_data(channel_id, new_fief)
                        await message.channel.send(msg)
                        system_action_result = f"\n[System Event: {msg}]"
                    except: pass
                elif "CollectTaxes" in action_line:
                    fief = domain_manager.get_fief_data(channel_id)
                    new_fief, msg = simulation_manager.collect_taxes(fief)
                    domain_manager.update_fief_data(channel_id, new_fief)
                    await message.channel.send(msg)
                    system_action_result = f"\n[System Event: {msg}]"
                elif "Rest" in action_line:
                    user_data = domain_manager.get_user_data(channel_id, message.author.id)
                    new_data, msg = simulation_manager.rest_character(user_data)
                    domain_manager.update_user_data(channel_id, message.author.id, new_data)
                    await message.channel.send(msg)
                    system_action_result = f"\n[System Event: {msg}]"
                elif "InventoryAction" in action_line:
                    try:
                        parts = action_line.split('|')
                        act_type, i_name, i_count = "add", "Unknown", 1
                        for p in parts:
                            p = p.strip()
                            if p.startswith("Type:"): act_type = p.replace("Type:", "").strip().lower()
                            elif p.startswith("Item:"): i_name = p.replace("Item:", "").strip()
                            elif p.startswith("Count:"): i_count = int(p.replace("Count:", "").strip())
                        user_data = domain_manager.get_user_data(channel_id, message.author.id)
                        new_data, msg = simulation_manager.update_inventory(user_data, act_type, i_name, i_count)
                        domain_manager.update_user_data(channel_id, message.author.id, new_data)
                        await message.channel.send(msg)
                        system_action_result = f"\n[System Event: {msg}]"
                    except: pass
                elif "QuestAction" in action_line:
                    try:
                        parts = action_line.split('|')
                        q_type, q_content = "Add", "Unknown"
                        for p in parts:
                            p = p.strip()
                            if p.startswith("Type:"): q_type = p.replace("Type:", "").strip().lower()
                            elif p.startswith("Content:"): q_content = p.replace("Content:", "").strip()
                        
                        if q_type == "add":
                            msg = quest_manager.add_quest(channel_id, q_content)
                            await message.channel.send(msg)
                        elif q_type == "complete":
                            msg = quest_manager.complete_quest(channel_id, q_content)
                            await message.channel.send(msg)
                        system_action_result = f"\n[System Event: Quest {q_type} - {q_content}]"
                    except: pass
                elif "MemoAction" in action_line:
                    try:
                        parts = action_line.split('|')
                        m_type, m_content = "Add", "Unknown"
                        for p in parts:
                            p = p.strip()
                            if p.startswith("Type:"): m_type = p.replace("Type:", "").strip().lower()
                            elif p.startswith("Content:"): m_content = p.replace("Content:", "").strip()
                        
                        if m_type == "add":
                            msg = quest_manager.add_memo(channel_id, m_content)
                            await message.channel.send(msg)
                        elif m_type == "remove":
                            msg = quest_manager.archive_memo(channel_id, m_content)
                            await message.channel.send(msg)
                        system_action_result = f"\n[System Event: Memo {m_type} - {m_content}]"
                    except: pass
                elif "StatusAction" in action_line:
                    try:
                        parts = action_line.split('|')
                        s_type, s_effect = "Add", "Unknown"
                        for p in parts:
                            p = p.strip()
                            if p.startswith("Type:"): s_type = p.replace("Type:", "").strip().lower()
                            elif p.startswith("Effect:"): s_effect = p.replace("Effect:", "").strip()
                        
                        user_data = domain_manager.get_user_data(channel_id, message.author.id)
                        new_data, msg = simulation_manager.update_status_effect(user_data, s_type, s_effect)
                        domain_manager.update_user_data(channel_id, message.author.id, new_data)
                        await message.channel.send(msg)
                        system_action_result = f"\n[System Event: Status {s_type} - {s_effect}]"
                    except: pass
                elif "RelationAction" in action_line:
                    try:
                        parts = action_line.split('|')
                        r_target, r_amount = "Unknown", 0
                        for p in parts:
                            p = p.strip()
                            if p.startswith("Target:"): r_target = p.replace("Target:", "").strip()
                            elif p.startswith("Amount:"): r_amount = int(p.replace("Amount:", "").strip())
                        
                        user_data = domain_manager.get_user_data(channel_id, message.author.id)
                        new_data, msg = simulation_manager.modify_relationship(user_data, r_target, r_amount)
                        domain_manager.update_user_data(channel_id, message.author.id, new_data)
                        await message.channel.send(msg)
                        system_action_result = f"\n[System Event: Relation {r_target} {r_amount:+}]"
                    except: pass

            if system_action_result:
                final_input += system_action_result

            nvc_context_str = ""
            if nvc_result:
                domain_manager.update_ai_notes(channel_id, nvc_result.get("Observation"), nvc_result.get("Request"))
                nvc_context_str = (
                    f"\n[AI Internal Thoughts]\n"
                    f"Feeling: {nvc_result.get('Feeling')}\n"
                    f"Need: {nvc_result.get('Need')}\n"
                )
            
            full_prompt = (
                f"{world_context_str}\n"
                f"{objective_context_str}\n"
                f"[Players]: {active_users_str}\n"
                f"{nvc_context_str}\n"
                f"[Action]:\n{final_input}"
            )

            session = persona.create_risu_style_session(client_genai, MODEL_ID, lore_text, rule_text)
            for h in domain.get('history', []):
                role = "user" if h['role'] == "User" else "model"
                session.history.append(types.Content(role=role, parts=[types.Part(text=h['content'])]))
            
            response = await persona.generate_response_with_retry(client_genai, session, full_prompt)
            
            if response:
                await send_long_message(message.channel, response)
                domain_manager.append_history(channel_id, "User", final_input)
                domain_manager.append_history(channel_id, "Char", response)

    except Exception as e:
        logging.error(f"Error: {e}")
        await message.channel.send(f"⚠️ 오류: {e}")

if __name__ == "__main__":
    client_discord.run(DISCORD_TOKEN)