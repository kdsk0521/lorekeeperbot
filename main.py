import discord
import os
import asyncio
import logging
import io
from dotenv import load_dotenv
from google import genai
from google.genai import types

# 필수 모듈 임포트
try:
    import persona, domain_manager, character_sheet, input_handler, simulation_manager, memory_system, session_manager, world_manager, quest_manager
except ImportError as e:
    print(f"CRITICAL ERROR: 필수 모듈을 찾을 수 없습니다. {e}"); exit(1)

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')

load_dotenv()
DISCORD_TOKEN = os.getenv('DISCORD_TOKEN')
GEMINI_API_KEY = os.getenv('GEMINI_API_KEY')
MODEL_ID = os.getenv('GEMINI_MODEL_VERSION', 'gemini-2.0-flash-exp')

if not GEMINI_API_KEY: logging.warning("GEMINI_API_KEY Missing!")
try: client_genai = genai.Client(api_key=GEMINI_API_KEY)
except: client_genai = None

intents = discord.Intents.default(); intents.message_content = True
client_discord = discord.Client(intents=intents)

async def send_long_message(channel, text):
    if len(text) <= 2000: return await channel.send(text)
    for i in range(0, len(text), 2000): await channel.send(text[i:i+2000])

@client_discord.event
async def on_ready():
    domain_manager.initialize_folders()
    print(f"--- Lorekeeper V2.0 Online ({client_discord.user}) ---")
    print(f"Model: {MODEL_ID}")

@client_discord.event
async def on_message(message):
    if message.author == client_discord.user or not message.content: return

    try:
        channel_id = str(message.channel.id)
        if message.content == "!off": domain_manager.set_bot_disabled(channel_id, True); return await message.channel.send("🔇 Off")
        if message.content == "!on": domain_manager.set_bot_disabled(channel_id, False); return await message.channel.send("🔊 On")
        if domain_manager.is_bot_disabled(channel_id): return

        parsed = input_handler.parse_input(message.content)
        if not parsed: return
        cmd = parsed.get('command')
        if not domain_manager.is_prepared(channel_id):
            allowed = ['준비', 'ready', '로어', 'lore', '룰', 'rule', 'reset', '리셋', '시스템', 'system']
            if parsed['type'] != 'command' or cmd not in allowed:
                return await message.channel.send("⚠️ `!준비`를 먼저 해주세요.")

        system_trigger = None

        if parsed['type'] == 'command':
            if cmd in ['reset', '리셋']: return await session_manager.manager.execute_reset(message, client_discord, domain_manager, character_sheet)
            if cmd in ['ready', '준비']: return await session_manager.manager.check_preparation(message, domain_manager)
            if cmd in ['start', '시작']:
                domain_manager.update_participant(channel_id, message.author)
                if await session_manager.manager.start_session(message, client_genai, MODEL_ID, domain_manager):
                    system_trigger = "[System: Generate a visceral opening scene for the campaign.]"
                else: return
                
            if cmd in ['unlock', '잠금해제']: domain_manager.set_session_lock(channel_id, False); return await message.channel.send("🔓 잠금 해제")
            if cmd in ['lock', '잠금']: domain_manager.set_session_lock(channel_id, True); return await message.channel.send("🔒 세션 잠금")
            
            if cmd in ['system', '시스템']:
                args = parsed['content'].strip().split()
                if not args or args[0] not in ['성장', 'growth']:
                    return await message.channel.send(f"⚙️ 변경: `!시스템 성장 [기본/헌터/DND/커스텀]`")
                domain_manager.set_growth_system(channel_id, args[1].lower())
                return await message.channel.send(f"✅ 설정 완료: `{args[1]}`")

            if cmd in ['xp', '경험치']:
                try:
                    amount = int(parsed['content'].strip())
                    uid = str(message.author.id)
                    p_data = domain_manager.get_participant_data(channel_id, uid)
                    if not p_data: return await message.channel.send("❌ 캐릭터 없음")
                    new_data, msg, check_ai = simulation_manager.gain_experience(p_data, amount, domain_manager.get_growth_system(channel_id))
                    if check_ai == "CheckAI":
                        await message.channel.send(msg)
                        eval_msg = await message.channel.send("🤔 **커스텀 룰 판정 중...**")
                        if client_genai:
                            res = quest_manager.evaluate_custom_growth(new_data['level'], new_data['xp'], domain_manager.get_rules(channel_id))
                            if res.get("leveled_up"):
                                new_data['level'] = res.get("new_level", new_data['level'] + 1)
                                await eval_msg.edit(content=f"🎉 **레벨 업!** Lv.{new_data['level']} ({res.get('reason')})")
                            else: await eval_msg.edit(content="🔒 레벨 유지")
                        else: await eval_msg.edit(content="⚠️ AI 오류")
                    else: await message.channel.send(msg)
                    domain_manager.save_participant_data(channel_id, uid, new_data)
                except: return await message.channel.send("❌ `!경험치 [숫자]`")

            if cmd in ['lore', '로어']:
                arg = parsed['content'].strip(); file_text = ""
                if message.attachments:
                    for att in message.attachments:
                        if att.filename.endswith('.txt'): data = await att.read(); file_text = data.decode('utf-8'); break
                
                full = (arg + "\n" + file_text).strip()
                if full == "초기화": 
                    domain_manager.reset_lore(channel_id); domain_manager.set_active_genres(channel_id, ["noir"]); domain_manager.set_custom_tone(channel_id, None)
                    return await message.channel.send("📜 초기화됨")
                if full:
                    if file_text: domain_manager.reset_lore(channel_id)
                    domain_manager.append_lore(channel_id, full)
                    msg = await message.channel.send("📜 업데이트 완료. 분석 중...")
                    if client_genai:
                        lore = domain_manager.get_lore(channel_id)
                        res = await memory_system.analyze_genre_from_lore(client_genai, MODEL_ID, lore)
                        domain_manager.set_active_genres(channel_id, res.get("genres", ["noir"]))
                        domain_manager.set_custom_tone(channel_id, res.get("custom_tone"))
                        npcs = await memory_system.analyze_npcs_from_lore(client_genai, MODEL_ID, lore)
                        if npcs:
                            for n in npcs: character_sheet.npc_memory.add_npc(channel_id, n.get("name"), n.get("description"))
                        rules = await memory_system.analyze_location_rules_from_lore(client_genai, MODEL_ID, lore)
                        if rules: domain_manager.set_location_rules(channel_id, rules)
                        await msg.edit(content=f"📜 분석 완료.\n🎨 분위기: {res.get('genres')}")
                    else: await msg.edit(content="📜 저장 완료 (AI 미연동)")
                else: return await message.channel.send(f"📜 {domain_manager.get_lore(channel_id)}")

            if cmd in ['mode', '모드']:
                arg = parsed['content'].strip()
                if '수동' in arg: domain_manager.set_response_mode(channel_id, 'manual'); return await message.channel.send("🛑 수동 모드")
                if '자동' in arg: domain_manager.set_response_mode(channel_id, 'auto'); return await message.channel.send("⏩ 자동 모드")
                return await message.channel.send(f"⚙️ 현재: {domain_manager.get_response_mode(channel_id)}")

            if cmd in ['next', '진행', 'turn', '턴']: 
                if '시간' in parsed['content']: await message.channel.send(world_manager.advance_time(channel_id)); system_trigger = "[System: Time passes.]"
                else: system_trigger = "[System: Resolve actions.]"; await message.add_reaction("🎬")

            if cmd in ['mask', '가면']: 
                target = parsed['content']; st = domain_manager.get_participant_status(channel_id, message.author.id)
                if st == "left": domain_manager.update_participant(channel_id, message.author, True); await message.channel.send("🆕 환생 완료")
                domain_manager.update_participant(channel_id, message.author); domain_manager.set_user_mask(channel_id, message.author.id, target)
                return await message.channel.send(f"🎭 가면: {target}")

            if cmd in ['desc', '설명']: 
                domain_manager.update_participant(channel_id, message.author); domain_manager.set_user_description(channel_id, message.author.id, parsed['content'])
                return await message.channel.send("📝 저장됨")

            if cmd in ['info', '내정보']:
                uid = str(message.author.id); p = domain_manager.get_participant_data(channel_id, uid)
                if not p: return await message.channel.send("❌ 정보 없음")
                lvl_disp = f"Lv.{p.get('level')}"
                if domain_manager.get_growth_system(channel_id) == 'hunter': lvl_disp = simulation_manager.get_hunter_rank(p.get('level'))
                msg = f"👤 **[{p.get('mask')}]**\n📊 {lvl_disp} (XP: {p.get('xp')})\n📜 {p.get('description')}"
                return await message.channel.send(msg)

            if cmd in ['afk', '잠수']: domain_manager.set_participant_status(channel_id, message.author.id, "afk"); return await message.channel.send("💤")
            if cmd in ['leave', '이탈']: domain_manager.set_participant_status(channel_id, message.author.id, "left", "이탈"); return await message.channel.send("🚪")
            if cmd in ['back', '복귀']: domain_manager.update_participant(channel_id, message.author); return await message.channel.send("✨")

            if cmd in ['rule', '룰']:
                arg = parsed['content'].strip(); file_text = ""
                if message.attachments:
                    for att in message.attachments:
                        if att.filename.endswith('.txt'): data = await att.read(); file_text = data.decode('utf-8'); break
                if file_text:
                    domain_manager.reset_rules(channel_id); domain_manager.append_rules(channel_id, file_text)
                    if arg: domain_manager.append_rules(channel_id, arg)
                    return await message.channel.send("📘 룰 덮어쓰기 완료")
                if arg == "초기화": domain_manager.reset_rules(channel_id); return await message.channel.send("📘 초기화됨")
                elif arg: domain_manager.append_rules(channel_id, arg); return await message.channel.send("📘 추가됨")
                return await message.channel.send(f"📘 {domain_manager.get_rules(channel_id)}")

            if cmd in ['quest', '퀘스트']: return await message.channel.send(quest_manager.add_quest(channel_id, parsed['content']) or "❌")
            if cmd in ['memo', '메모']: return await message.channel.send(quest_manager.add_memo(channel_id, parsed['content']) or "❌")
            if cmd in ['complete', '완료']: return await message.channel.send(quest_manager.complete_quest(channel_id, parsed['content']) or "❌")
            if cmd in ['status', '상태']: return await message.channel.send(quest_manager.get_status_message(channel_id))
            if cmd in ['archive', '보관']: return await message.channel.send(quest_manager.archive_memo_with_ai(channel_id, parsed['content']))
            if cmd in ['lores', '연대기']: 
                if parsed['content'] == "생성":
                    msg = await message.channel.send("⏳ 연대기 생성 중...")
                    return await msg.edit(content=quest_manager.generate_chronicle_from_history(channel_id))
                return await message.channel.send(quest_manager.get_lore_book(channel_id))
            if cmd in ['export', '추출']:
                mode = parsed.get('content', '').strip(); lore = domain_manager.get_lore(channel_id)
                ch, msg = quest_manager.export_chronicles_incremental(channel_id, mode)
                if not ch: return await message.channel.send(msg)
                with io.BytesIO(f"=== LORE ===\n{lore}\n\n{ch}".encode('utf-8')) as f: return await message.channel.send(msg, file=discord.File(f, filename="export.txt"))

        if parsed['type'] == 'dice':
            await message.channel.send(parsed['content'])
            domain_manager.append_history(channel_id, "System", f"Dice: {parsed['content']}")
            return 

        if parsed['type'] == 'command' and not system_trigger: return
        domain = domain_manager.get_domain(channel_id)
        if not domain['settings'].get('session_locked', False) and not system_trigger: return

        async with message.channel.typing():
            if not domain_manager.update_participant(channel_id, message.author): return
            user_mask = domain_manager.get_user_mask(channel_id, message.author.id)
            action_text = system_trigger if system_trigger else f"[{user_mask}]: {parsed['content']}"

            if domain_manager.get_response_mode(channel_id) == 'manual' and not system_trigger and not any(k in str(system_trigger) for k in ["Resolve"]):
                domain_manager.append_history(channel_id, "User", action_text); await message.add_reaction("✍️"); return

            lore_txt = domain_manager.get_lore(channel_id); rule_txt = domain_manager.get_rules(channel_id)
            world_ctx = world_manager.get_world_context(channel_id); obj_ctx = quest_manager.get_objective_context(channel_id)
            active_genres, custom_tone = domain_manager.get_active_genres(channel_id), domain_manager.get_custom_tone(channel_id)
            history = domain.get('history', [])[-10:]
            hist_text = "\n".join([f"{h['role']}: {h['content']}" for h in history]) + f"\nUser: {action_text}"

            nvc_res = {}
            if client_genai:
                nvc_res = await memory_system.analyze_context_nvc(client_genai, MODEL_ID, hist_text, lore_txt, rule_txt)
                if nvc_res.get("CurrentLocation"): domain_manager.set_current_location(channel_id, nvc_res["CurrentLocation"])
                if nvc_res.get("LocationRisk"): domain_manager.set_current_risk(channel_id, nvc_res["LocationRisk"])

            sys_action = nvc_res.get("SystemAction", {})
            auto_msg = None
            if sys_action and isinstance(sys_action, dict):
                tool = sys_action.get("tool"); atype = sys_action.get("type"); content = sys_action.get("content")
                if tool == "Memo":
                    if atype == "Add": auto_msg = quest_manager.add_memo(channel_id, content)
                    elif atype == "Remove": auto_msg = quest_manager.remove_memo(channel_id, content)
                    elif atype == "Archive": auto_msg = quest_manager.resolve_memo_auto(channel_id, content) # [핵심] 자동 보관 기능 연결
                elif tool == "Quest":
                    if atype == "Add": auto_msg = quest_manager.add_quest(channel_id, content)
                    elif atype == "Complete": auto_msg = quest_manager.complete_quest(channel_id, content)
                elif tool == "NPC" and atype == "Add":
                    if ":" in content: n, d = content.split(":", 1); character_sheet.npc_memory.add_npc(channel_id, n.strip(), d.strip()); auto_msg = f"👥 NPC: {n.strip()}"
                    else: character_sheet.npc_memory.add_npc(channel_id, content, "Auto"); auto_msg = f"👥 NPC: {content}"

            full_prompt = (
                f"### [WORLD & MEMORY]\n{world_ctx}\n{obj_ctx}\n\n### [GM BRAIN]\nObs: {nvc_res.get('Observation')}\nNeed: {nvc_res.get('Need')}\n\n### [ACTION]\n{action_text}\n\nRespond in Korean."
            )

            if client_genai:
                loading = await message.channel.send("⏳ **[Lorekeeper]** 집필 중...")
                session = persona.create_risu_style_session(client_genai, MODEL_ID, lore_txt, rule_txt, active_genres, custom_tone)
                for h in domain.get('history', []): session.history.append(types.Content(role="user" if h['role']=="User" else "model", parts=[types.Part(text=h['content'])]))
                response = await persona.generate_response_with_retry(client_genai, session, full_prompt)
                try: await loading.delete()
                except: pass
            else: response = "⚠️ AI Error"

            if auto_msg: await message.channel.send(f"🤖 {auto_msg}")
            if response:
                await send_long_message(message.channel, response)
                domain_manager.append_history(channel_id, "User", action_text)
                domain_manager.append_history(channel_id, "Char", response)

    except Exception as e:
        logging.error(f"Main Error: {e}")

if __name__ == "__main__":
    if DISCORD_TOKEN: client_discord.run(DISCORD_TOKEN)