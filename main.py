import discord
import os
import asyncio
import logging
import io
import re
from pathlib import Path
from dotenv import load_dotenv
from google import genai
from google.genai import types

# 필수 모듈 임포트
# (여기서 에러가 났던 이유는 persona.py가 깨져서 그랬던 것입니다. 이제 괜찮습니다.)
try:
    import persona, domain_manager, character_sheet, input_handler, simulation_manager, memory_system, session_manager, world_manager, quest_manager
except ImportError as e:
    print(f"CRITICAL ERROR: 필수 모듈을 찾을 수 없습니다. {e}"); exit(1)

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')

# [서버 설정 강화] .env 파일 경로 명시
# systemd(24시간 서버)는 .env 위치를 못 찾을 수 있으므로 절대 경로를 지정해줍니다.
env_path = Path('/home/ubuntu/lorekeeper/.env')

if env_path.exists():
    load_dotenv(dotenv_path=env_path)
    print(f"✅ Loaded .env from Server Path: {env_path}")
else:
    # 윈도우나 다른 환경에서 실행할 때를 대비한 기본 로드
    load_dotenv()
    print("⚠️ Loaded .env from default location (Local Mode)")

DISCORD_TOKEN = os.getenv('DISCORD_TOKEN')
GEMINI_API_KEY = os.getenv('GEMINI_API_KEY')
MODEL_ID = os.getenv('GEMINI_MODEL_VERSION', 'gemini-3-flash-preview')

if not GEMINI_API_KEY: logging.warning("GEMINI_API_KEY Missing!")
try: client_genai = genai.Client(api_key=GEMINI_API_KEY)
except: client_genai = None

intents = discord.Intents.default(); intents.message_content = True
client_discord = discord.Client(intents=intents)

async def send_long_message(channel, text):
    """2000자가 넘는 메시지를 나누어 전송하는 함수"""
    if not text: return
    if len(text) <= 2000: return await channel.send(text)
    for i in range(0, len(text), 2000): await channel.send(text[i:i+2000])

@client_discord.event
async def on_ready():
    domain_manager.initialize_folders()
    print(f"--- Lorekeeper V3.0 (Fixed) Online ({client_discord.user}) ---")
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
        
        # [보안] 참가자 및 잠금 확인
        is_participant = domain_manager.get_participant_data(channel_id, str(message.author.id)) is not None
        domain_data = domain_manager.get_domain(channel_id)
        is_locked = domain_data['settings'].get('session_locked', False)
        
        # [핵심 수정] 참가자가 아니어도(초기 세팅 중) 사용할 수 있는 명령어 목록 확장
        # 로어, 룰, 시스템 등 초기 설정 명령어를 허용해야 함
        entry_commands = [
            '준비', 'ready', '리셋', 'reset', '시작', 'start', '가면', 'mask', '초기화',
            '로어', 'lore', '룰', 'rule', '시스템', 'system' 
        ]
        
        if not is_participant:
            if is_locked: return 
            if parsed['type'] == 'command':
                if cmd not in entry_commands: return 
            else: return 

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
                
            if cmd in ['unlock', '잠금해제']: domain_manager.set_session_lock(channel_id, False); return await message.channel.send("🔓 **잠금 해제**")
            if cmd in ['lock', '잠금']: domain_manager.set_session_lock(channel_id, True); return await message.channel.send("🔒 **세션 잠금**")
            
            if cmd in ['system', '시스템']:
                args = parsed['content'].strip().split()
                if not args: return await message.channel.send(f"⚙️ 사용법: `!시스템 성장 [기본/헌터/DND/커스텀]`")
                if args[0] in ['성장', 'growth']:
                    if len(args) < 2: return await message.channel.send(f"📊 **현재 성장:** `{domain_manager.get_growth_system(channel_id)}`")
                    domain_manager.set_growth_system(channel_id, args[1].lower())
                    return await message.channel.send(f"✅ 설정 완료: `{args[1].lower()}`")

            # --- [치트 모드] ---
            if cmd in ['cheat', '치트', 'debug', '디버그', 'gm']:
                args = parsed['content'].strip().split(' ', 2)
                if len(args) < 1: return await message.channel.send("🛠️ **치트:** `!치트 [경험치/퀘스트/메모] ...`")
                category = args[0]
                if category in ['xp', '경험치']:
                    try:
                        amount = int(args[1])
                        uid = str(message.author.id)
                        p_data = domain_manager.get_participant_data(channel_id, uid)
                        if not p_data: return await message.channel.send("❌ 캐릭터 없음")
                        new_data, msg, _ = simulation_manager.gain_experience(p_data, amount, domain_manager.get_growth_system(channel_id))
                        domain_manager.save_participant_data(channel_id, uid, new_data)
                        return await message.channel.send(f"🛠️ **[치트]** {msg}")
                    except: return await message.channel.send("❌ 사용법: `!치트 경험치 [숫자]`")
                elif category in ['quest', '퀘스트']:
                    if len(args) < 3: return await message.channel.send("❌ 사용법: `!치트 퀘스트 [추가/완료] [내용]`")
                    if args[1] == '추가': return await message.channel.send(f"🛠️ {quest_manager.add_quest(channel_id, args[2])}")
                    elif args[1] == '완료': return await message.channel.send(f"🛠️ {quest_manager.complete_quest(channel_id, args[2])}")
                elif category in ['memo', '메모']:
                    if len(args) < 3: return await message.channel.send("❌ 사용법: `!치트 메모 [추가/삭제] [내용]`")
                    if args[1] == '추가': return await message.channel.send(f"🛠️ {quest_manager.add_memo(channel_id, args[2])}")
                    elif args[1] == '삭제': return await message.channel.send(f"🛠️ {quest_manager.remove_memo(channel_id, args[2])}")
                return await message.channel.send("⚠️ 알 수 없는 치트 명령")

            # --- [로어 명령어 개선] ---
            if cmd in ['lore', '로어']:
                arg = parsed['content'].strip()
                file_text = ""
                is_file_processed = False
                
                # 파일 처리 로직 강화: 다양한 텍스트 확장자 지원
                if message.attachments:
                    for att in message.attachments:
                        if any(att.filename.lower().endswith(ext) for ext in ['.txt', '.md', '.json', '.log', '.py', '.yaml', '.yml']):
                            try:
                                data = await att.read()
                                file_text = data.decode('utf-8')
                                is_file_processed = True
                                break
                            except Exception as e:
                                await message.channel.send(f"⚠️ 파일 `{att.filename}` 읽기 실패: {e} (UTF-8 텍스트만 지원)")
                                return
                    
                    if not is_file_processed and not arg:
                        return await message.channel.send("⚠️ **지원하지 않는 파일입니다.**\n(.txt, .md, .json 파일만 인식합니다.)")

                full = (arg + "\n" + file_text).strip()
                
                # 내용이 없으면 조회 모드
                if not full:
                    summary = domain_manager.get_lore_summary(channel_id)
                    display_text = summary if summary else domain_manager.get_lore(channel_id)
                    title = "[핵심 요약본]" if summary else "[원본 로어]"
                    if display_text == domain_manager.DEFAULT_LORE:
                         return await message.channel.send("📜 저장된 로어가 없습니다. `!로어 [내용]` 또는 텍스트 파일을 업로드하세요.")
                    return await send_long_message(message.channel, f"📜 **{title}**\n{display_text}")

                if full == "초기화": 
                    domain_manager.reset_lore(channel_id); domain_manager.set_active_genres(channel_id, ["noir"]); domain_manager.set_custom_tone(channel_id, None)
                    return await message.channel.send("📜 초기화됨")
                
                # 로어 저장 및 분석
                if file_text: domain_manager.reset_lore(channel_id) 
                domain_manager.append_lore(channel_id, full)
                
                status_msg = await message.channel.send("📜 **로어 저장됨.** (AI 분석 준비 중...)")
                
                if client_genai:
                    try:
                        await status_msg.edit(content="⏳ **[AI]** 방대한 세계관을 압축하여 요약본을 생성하고 있습니다... (최대 10초 소요)")
                        raw_lore = domain_manager.get_lore(channel_id)
                        summary = await memory_system.compress_lore_core(client_genai, MODEL_ID, raw_lore)
                        domain_manager.save_lore_summary(channel_id, summary)
                        
                        await status_msg.edit(content="⏳ **[AI]** 장르 및 NPC 데이터 추출 중...")
                        res = await memory_system.analyze_genre_from_lore(client_genai, MODEL_ID, raw_lore)
                        domain_manager.set_active_genres(channel_id, res.get("genres", ["noir"]))
                        domain_manager.set_custom_tone(channel_id, res.get("custom_tone"))
                        
                        npcs = await memory_system.analyze_npcs_from_lore(client_genai, MODEL_ID, raw_lore)
                        for n in npcs: character_sheet.npc_memory.add_npc(channel_id, n.get("name"), n.get("description"))
                        
                        rules = await memory_system.analyze_location_rules_from_lore(client_genai, MODEL_ID, raw_lore)
                        if rules: domain_manager.set_location_rules(channel_id, rules)
                        
                        await status_msg.edit(content=f"✅ **[완료]** 핵심 요약본 및 분석 완료.\n**장르:** {res.get('genres')}")
                    except Exception as e:
                        logging.error(f"Lore Analysis Error: {e}")
                        await status_msg.edit(content=f"⚠️ **분석 중 오류 발생:** {e}")
                else:
                    await status_msg.edit(content="📜 저장 완료 (⚠️ API 키 없음: AI 분석 건너뜀)")

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
                if not client_genai: return await message.channel.send(f"👤 **[{p.get('mask')}]**\n{p.get('description')}")
                wait_msg = await message.channel.send("⏳ **[AI]** 분석 중...")
                view_data = await quest_manager.generate_character_info_view(client_genai, MODEL_ID, channel_id, uid, p.get('description', ''), p.get('inventory', {}))
                if view_data:
                    final_msg = f"👤 **[{p.get('mask')}]**\n👁️ **외형:** {view_data.get('appearance_summary')}\n💰 **재산:** {view_data.get('assets_summary')}\n🤝 **관계:**\n" + "\n".join([f"- {r}" for r in view_data.get("relationships", [])])
                    await wait_msg.delete(); return await send_long_message(message.channel, final_msg)
                else: await wait_msg.edit(content="⚠️ 분석 실패")

            if cmd in ['status', '상태']: 
                return await send_long_message(message.channel, quest_manager.get_status_message(channel_id))

            if cmd in ['afk', '잠수']: domain_manager.set_participant_status(channel_id, message.author.id, "afk"); return await message.channel.send("💤")
            if cmd in ['leave', '이탈']: domain_manager.set_participant_status(channel_id, message.author.id, "left", "이탈"); return await message.channel.send("🚪")
            if cmd in ['back', '복귀']: domain_manager.update_participant(channel_id, message.author); return await message.channel.send("✨")

            if cmd in ['rule', '룰']:
                arg = parsed['content'].strip(); file_text = ""
                if message.attachments:
                    for att in message.attachments:
                        if att.filename.endswith('.txt'): data = await att.read(); file_text = data.decode('utf-8'); break
                if file_text or arg:
                    if arg == "초기화": domain_manager.reset_rules(channel_id); return await message.channel.send("📘 초기화됨")
                    domain_manager.append_rules(channel_id, file_text if file_text else arg)
                    return await message.channel.send("📘 룰 업데이트")
                return await send_long_message(message.channel, f"📘 **현재 룰:**\n{domain_manager.get_rules(channel_id)}")
            
            if cmd in ['lores', '연대기']: 
                if parsed['content'] == "생성":
                    msg = await message.channel.send("⏳ 생성 중...")
                    res = await quest_manager.generate_chronicle_from_history(client_genai, MODEL_ID, channel_id)
                    try: await msg.delete()
                    except: pass
                    return await send_long_message(message.channel, res)
                return await send_long_message(message.channel, quest_manager.get_lore_book(channel_id))
                
            if cmd in ['export', '추출']:
                mode = parsed.get('content', '').strip(); lore = domain_manager.get_lore(channel_id)
                ch, msg = quest_manager.export_chronicles_incremental(channel_id, mode)
                if not ch: return await message.channel.send(msg)
                with io.BytesIO(f"=== LORE ===\n{lore}\n\n{ch}".encode('utf-8')) as f: return await message.channel.send(msg, file=discord.File(f, filename="export.txt"))

        if parsed['type'] == 'dice':
            await message.channel.send(parsed['content']); domain_manager.append_history(channel_id, "System", f"Dice: {parsed['content']}"); return 

        if parsed['type'] == 'command' and not system_trigger: return
        domain = domain_manager.get_domain(channel_id)
        if not domain['settings'].get('session_locked', False) and not system_trigger: return

        async with message.channel.typing():
            if not domain_manager.update_participant(channel_id, message.author): return
            user_mask = domain_manager.get_user_mask(channel_id, message.author.id)
            action_text = system_trigger if system_trigger else f"[{user_mask}]: {parsed['content']}"

            if domain_manager.get_response_mode(channel_id) == 'manual' and not system_trigger and not any(k in str(system_trigger) for k in ["Resolve"]):
                domain_manager.append_history(channel_id, "User", action_text); await message.add_reaction("✍️"); return

            summary = domain_manager.get_lore_summary(channel_id)
            lore_txt = summary if summary else domain_manager.get_lore(channel_id)
            
            rule_txt = domain_manager.get_rules(channel_id)
            world_ctx = world_manager.get_world_context(channel_id); obj_ctx = quest_manager.get_objective_context(channel_id)
            active_genres, custom_tone = domain_manager.get_active_genres(channel_id), domain_manager.get_custom_tone(channel_id)
            history = domain.get('history', [])[-10:]
            hist_text = "\n".join([f"{h['role']}: {h['content']}" for h in history]) + f"\nUser: {action_text}"

            active_quests = domain_manager.get_quest_board(channel_id).get("active", [])
            quest_txt = " | ".join(active_quests) if active_quests else "None"

            nvc_res = {}
            if client_genai:
                nvc_res = await memory_system.analyze_context_nvc(client_genai, MODEL_ID, hist_text, lore_txt, rule_txt, quest_txt)
                if nvc_res.get("CurrentLocation"): domain_manager.set_current_location(channel_id, nvc_res["CurrentLocation"])
                if nvc_res.get("LocationRisk"): domain_manager.set_current_risk(channel_id, nvc_res["LocationRisk"])

            sys_action = nvc_res.get("SystemAction", {})
            auto_msg = None
            if sys_action and isinstance(sys_action, dict):
                tool = sys_action.get("tool"); atype = sys_action.get("type"); content = sys_action.get("content")
                if tool == "Memo":
                    if atype == "Add": auto_msg = quest_manager.add_memo(channel_id, content)
                    elif atype == "Remove": auto_msg = quest_manager.remove_memo(channel_id, content)
                    elif atype == "Archive": auto_msg = quest_manager.resolve_memo_auto(channel_id, content)
                elif tool == "Quest":
                    if atype == "Add": auto_msg = quest_manager.add_quest(channel_id, content)
                    elif atype == "Complete": auto_msg = quest_manager.complete_quest(channel_id, content)
                elif tool == "NPC" and atype == "Add":
                    if ":" in content: n, d = content.split(":", 1); character_sheet.npc_memory.add_npc(channel_id, n.strip(), d.strip()); auto_msg = f"👥 NPC: {n.strip()}"
                    else: character_sheet.npc_memory.add_npc(channel_id, content, "Auto"); auto_msg = f"👥 NPC: {content}"
                elif tool == "XP" and atype == "Award":
                    try:
                        match = re.match(r"(\d+)\s*(?:\((.*)\))?", str(content))
                        if match:
                            xp_amount = int(match.group(1))
                            reason = match.group(2) or "Activity"
                            uid = str(message.author.id)
                            p_data = domain_manager.get_participant_data(channel_id, uid)
                            if p_data:
                                new_data, xp_msg, _ = simulation_manager.gain_experience(p_data, xp_amount, domain_manager.get_growth_system(channel_id))
                                domain_manager.save_participant_data(channel_id, uid, new_data)
                                auto_msg = f"⚔️ **성과 확인:** {reason}\n{xp_msg}"
                    except Exception as e:
                        logging.error(f"Auto XP Error: {e}")

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
        await message.channel.send(f"⚠️ **시스템 오류 발생:** {e}")

if __name__ == "__main__":
    if DISCORD_TOKEN: client_discord.run(DISCORD_TOKEN)