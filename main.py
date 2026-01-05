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
        
        # 0. 봇 전원 관리
        if message.content == "!off": domain_manager.set_bot_disabled(channel_id, True); return await message.channel.send("🔇 Off")
        if message.content == "!on": domain_manager.set_bot_disabled(channel_id, False); return await message.channel.send("🔊 On")
        if domain_manager.is_bot_disabled(channel_id): return

        # 1. 입력 분석
        parsed = input_handler.parse_input(message.content)
        if not parsed: return

        # 2. 준비 상태 확인
        cmd = parsed.get('command')
        if not domain_manager.is_prepared(channel_id):
            allowed = ['준비', 'ready', '로어', 'lore', '룰', 'rule', 'reset', '리셋']
            if parsed['type'] != 'command' or cmd not in allowed:
                return await message.channel.send("⚠️ `!준비`를 먼저 해주세요.")

        system_trigger = None

        # 3. 명령어 처리
        if parsed['type'] == 'command':
            # 세션 관리
            if cmd in ['reset', '리셋']: return await session_manager.manager.execute_reset(message, client_discord, domain_manager, character_sheet)
            if cmd in ['ready', '준비']: return await session_manager.manager.check_preparation(message, domain_manager)
            if cmd in ['start', '시작']:
                domain_manager.update_participant(channel_id, message.author)
                if await session_manager.manager.start_session(message, client_genai, MODEL_ID, domain_manager):
                    system_trigger = "[System: Generate a visceral opening scene for the campaign.]"
                else: return
                
            if cmd in ['unlock', '잠금해제']: domain_manager.set_session_lock(channel_id, False); return await message.channel.send("🔓 **세션 잠금 해제:** 새로운 참가자가 입장할 수 있습니다.")
            if cmd in ['lock', '잠금']: domain_manager.set_session_lock(channel_id, True); return await message.channel.send("🔒 **세션 잠금:** 현재 참가자 외에는 대화에 참여할 수 없습니다.")
            
            # [기능] 로어 입력 시 -> 장르 & NPC & 장소 규칙 자동 감지
            if cmd in ['lore', '로어']:
                arg = parsed['content'].strip()
                if arg == "초기화": 
                    domain_manager.reset_lore(channel_id)
                    domain_manager.set_active_genres(channel_id, ["noir"])
                    domain_manager.set_custom_tone(channel_id, None)
                    return await message.channel.send("📜 로어 및 장르 설정 초기화")
                
                if arg: 
                    domain_manager.append_lore(channel_id, arg)
                    msg = await message.channel.send("📜 로어 업데이트 완료. **세계관 분석 중 (장르/NPC/장소)...**")
                    current_lore = domain_manager.get_lore(channel_id)
                    
                    if client_genai:
                        # 1. 장르 분석
                        genre_res = await memory_system.analyze_genre_from_lore(client_genai, MODEL_ID, current_lore)
                        detected_genres = genre_res.get("genres", ["noir"])
                        custom_tone = genre_res.get("custom_tone")
                        domain_manager.set_active_genres(channel_id, detected_genres)
                        domain_manager.set_custom_tone(channel_id, custom_tone)
                        
                        report = f"📜 로어 업데이트 완료.\n🎨 **분위기:** {detected_genres}"
                        if custom_tone: report += f" ({custom_tone})"

                        # 2. NPC 추출
                        extracted_npcs = await memory_system.analyze_npcs_from_lore(client_genai, MODEL_ID, current_lore)
                        if extracted_npcs:
                            npc_names = []
                            for npc in extracted_npcs:
                                name = npc.get("name"); desc = npc.get("description")
                                if name and desc:
                                    character_sheet.npc_memory.add_npc(channel_id, name, desc)
                                    npc_names.append(name)
                            if npc_names: report += f"\n👥 **NPC 감지:** {', '.join(npc_names)}"

                        # 3. 장소 규칙 추출
                        loc_rules = await memory_system.analyze_location_rules_from_lore(client_genai, MODEL_ID, current_lore)
                        if loc_rules:
                            domain_manager.set_location_rules(channel_id, loc_rules)
                            report += f"\n🗺️ **위험 지역 감지:** {', '.join(loc_rules.keys())}"

                        await msg.edit(content=report)
                    else:
                        await msg.edit(content="📜 로어 업데이트 완료. (⚠️ AI 연결 실패로 자동 분석 불가)")
                else:
                    return await message.channel.send(f"📜 {domain_manager.get_lore(channel_id)}")

            # 진행 모드 변경
            if cmd in ['mode', '모드']:
                arg = parsed['content'].strip()
                if arg in ['수동', '턴', 'manual', 'turn']:
                    domain_manager.set_response_mode(channel_id, 'manual')
                    return await message.channel.send("🛑 **턴 모드(수동)로 전환:** 이제 AI는 `!진행`을 입력할 때만 응답합니다.")
                elif arg in ['자동', '실시간', 'auto', 'realtime']:
                    domain_manager.set_response_mode(channel_id, 'auto')
                    return await message.channel.send("⏩ **실시간 모드(자동)로 전환:** AI가 모든 채팅에 즉시 반응합니다.")
                else:
                    current = domain_manager.get_response_mode(channel_id)
                    mode_kor = "수동(턴)" if current == 'manual' else "자동(실시간)"
                    return await message.channel.send(f"⚙️ 현재 모드: **{mode_kor}**\n변경하려면 `!모드 [자동/수동]` 입력")

            # 진행 명령어 (AI 강제 트리거)
            if cmd in ['next', '진행', 'turn', '턴']: 
                arg = parsed['content'].strip()
                if arg in ['시간', 'time']:
                    world_msg = world_manager.advance_time(channel_id)
                    await message.channel.send(world_msg)
                    system_trigger = "[System: Describe the changing atmosphere due to time progression.]"
                else:
                    system_trigger = "[System: Resolve the accumulated player actions and advance the narrative.]"
                    await message.add_reaction("🎬")

            # 캐릭터 관리
            if cmd in ['mask', '가면']: 
                target_mask = parsed['content']
                if not target_mask:
                    return await message.channel.send(f"🎭 현재 가면: {domain_manager.get_user_mask(channel_id, message.author.id)}")
                
                # [기능] 이탈 상태 확인 및 환생 로직
                p_status = domain_manager.get_participant_status(channel_id, message.author.id)
                if p_status == "left":
                    domain_manager.update_participant(channel_id, message.author, is_new_char=True)
                    domain_manager.set_user_mask(channel_id, message.author.id, target_mask)
                    return await message.channel.send(f"🆕 **새로운 운명:** 이전 캐릭터의 기록을 뒤로하고, **'{target_mask}'**(으)로 새롭게 시작합니다.")
                
                domain_manager.update_participant(channel_id, message.author)
                domain_manager.set_user_mask(channel_id, message.author.id, target_mask)
                return await message.channel.send(f"🎭 가면 설정 완료: {target_mask}")

            if cmd in ['desc', '설명']: 
                domain_manager.update_participant(channel_id, message.author)
                domain_manager.set_user_description(channel_id, message.author.id, parsed['content'])
                return await message.channel.send("📝 설명 저장됨")
                
            if cmd in ['info', '내정보']:
                uid = str(message.author.id)
                p_data = domain_manager.get_participant_data(channel_id, uid)
                if not p_data: return await message.channel.send("❌ 정보 없음.")
                
                mask = p_data.get('mask', 'Unknown'); desc = p_data.get('description', '')
                status = p_data.get('status', 'active'); level = p_data.get('level', 1)
                xp = p_data.get('xp', 0); next_xp = p_data.get('next_xp', 100)
                stats_str = ", ".join([f"{k}: {v}" for k, v in p_data.get('stats', {}).items()])
                inv = p_data.get('inventory', {}); inv_str = ", ".join([f"{k} x{v}" for k, v in inv.items()]) if inv else "비어있음"
                eff_str = ", ".join(p_data.get('status_effects', [])) if p_data.get('status_effects') else "정상"
                rels = p_data.get('relations', {}); rel_str = "\n".join([f"- {k}: {v:+}" for k, v in rels.items()]) if rels else "없음"

                info_msg = (
                    f"👤 **[{mask}] 캐릭터 시트**\n━━━━━━━━━━━━━━━━━━━━━━\n"
                    f"📜 **설정**\n{desc}\n\n"
                    f"📊 **상태**\n- Lv.{level} (XP: {xp}/{next_xp})\n- 상태: {eff_str} ({status})\n- 능력치: {stats_str}\n\n"
                    f"🎒 **소지품**: {inv_str}\n"
                    f"💞 **관계**: {rel_str}\n━━━━━━━━━━━━━━━━━━━━━━"
                )
                return await message.channel.send(info_msg)

            if cmd in ['afk', '잠수']: domain_manager.set_participant_status(channel_id, message.author.id, "afk"); return await message.channel.send("💤 잠수")
            if cmd in ['leave', '이탈', '퇴장']: 
                m = domain_manager.set_participant_status(channel_id, message.author.id, "left", "자발적 이탈")
                return await message.channel.send(f"🚪 **[{m}]** 이탈 처리됨. (복귀하려면 `!가면`으로 새 캐릭터 생성)")
            if cmd in ['back', '복귀']: domain_manager.update_participant(channel_id, message.author); return await message.channel.send("✨ 복귀")

            # 룰 관리
            if cmd in ['rule', '룰']:
                if parsed['content'] == "초기화": domain_manager.reset_rules(channel_id); return await message.channel.send("📘 룰 초기화")
                if parsed['content']: domain_manager.append_rules(channel_id, parsed['content']); return await message.channel.send("📘 룰 추가")
                return await message.channel.send(f"📘 {domain_manager.get_rules(channel_id)}")

            # 퀘스트/메모 관리
            if cmd in ['quest', '퀘스트']: return await message.channel.send(quest_manager.add_quest(channel_id, parsed['content']) or "❌ 중복")
            if cmd in ['memo', '메모']: return await message.channel.send(quest_manager.add_memo(channel_id, parsed['content']) or "❌ 중복")
            if cmd in ['complete', '완료']: return await message.channel.send(quest_manager.complete_quest(channel_id, parsed['content']) or "❌ 실패")
            if cmd in ['status', '상태']: return await message.channel.send(quest_manager.get_status_message(channel_id))
            if cmd in ['archive', '보관']: return await message.channel.send(quest_manager.archive_memo_with_ai(channel_id, parsed['content']))
            if cmd in ['lores', '연대기']: return await message.channel.send(quest_manager.get_lore_book(channel_id))
            
            # 내보내기 기능
            if cmd in ['export', '추출']:
                mode = parsed.get('content', '').strip()
                lore_content = domain_manager.get_lore(channel_id)
                chronicle_text, status_msg = quest_manager.export_chronicles_incremental(channel_id, mode)
                if not chronicle_text: return await message.channel.send(status_msg)
                full_text = f"=== WORLD SETTINGS (LORE) ===\n{lore_content}\n\n{chronicle_text}"
                with io.BytesIO(full_text.encode('utf-8')) as f:
                    file = discord.File(f, filename=f"lorekeeper_export_{channel_id}.txt")
                    return await message.channel.send(status_msg, file=file)

        # 4. 주사위 처리 (단순 결과 출력 및 히스토리 기록, AI 호출 안 함)
        if parsed['type'] == 'dice':
            dice_msg = parsed['content']
            await message.channel.send(dice_msg)
            # system_trigger 주입 제거됨. 대신 히스토리에만 기록
            domain_manager.append_history(channel_id, "System", f"Player rolled dice. Result: {dice_msg}")

        # 5. AI 서사 루프
        
        # [가드] 명령어가 실행되었지만 AI 트리거가 없다면(예: !가면, !로어, !주사위) 여기서 멈춤
        if parsed['type'] == 'command' and not system_trigger:
            return

        domain = domain_manager.get_domain(channel_id)
        if not domain['settings'].get('session_locked', False) and not system_trigger: return

        async with message.channel.typing():
            if not domain_manager.update_participant(channel_id, message.author):
                return

            user_mask = domain_manager.get_user_mask(channel_id, message.author.id)
            action_text = system_trigger if system_trigger else f"[{user_mask}]: {parsed['content']}"

            # 수동 모드일 때: 강제 트리거가 아니면 기록만 함
            response_mode = domain_manager.get_response_mode(channel_id)
            is_force_trigger = system_trigger and any(k in system_trigger for k in ["Opening", "Generate", "Describe", "Resolve"])
            
            if response_mode == 'manual' and not is_force_trigger:
                domain_manager.append_history(channel_id, "User", action_text)
                await message.add_reaction("✍️")
                return

            # AI 생성 로직 시작
            lore_txt, rule_txt = domain_manager.get_lore(channel_id), domain_manager.get_rules(channel_id)
            world_ctx = world_manager.get_world_context(channel_id)
            obj_ctx = quest_manager.get_objective_context(channel_id)
            active_genres = domain_manager.get_active_genres(channel_id)
            custom_tone = domain_manager.get_custom_tone(channel_id)

            history = domain.get('history', [])[-10:]
            hist_text = "\n".join([f"{h['role']}: {h['content']}" for h in history]) + f"\nUser: {action_text}"

            # 1단계: NVC 및 시스템 액션 (위치/위험도 추적 포함)
            nvc_res = {}
            if client_genai:
                nvc_res = await memory_system.analyze_context_nvc(client_genai, MODEL_ID, hist_text, lore_txt, rule_txt)
                
                # 위치 및 위험도 추적 결과 반영
                inferred_loc = nvc_res.get("CurrentLocation")
                inferred_risk = nvc_res.get("LocationRisk")
                
                if inferred_loc and inferred_loc != "Unknown":
                    domain_manager.set_current_location(channel_id, inferred_loc)
                if inferred_risk:
                    domain_manager.set_current_risk(channel_id, inferred_risk)
            
            sys_action = nvc_res.get("SystemAction", {})
            auto_msg = None
            if sys_action and isinstance(sys_action, dict):
                tool = sys_action.get("tool")
                atype = sys_action.get("type")
                content = sys_action.get("content")
                if tool == "Memo":
                    if atype == "Add": auto_msg = quest_manager.add_memo(channel_id, content)
                    elif atype == "Remove": auto_msg = quest_manager.remove_memo(channel_id, content)
                elif tool == "Quest":
                    if atype == "Add": auto_msg = quest_manager.add_quest(channel_id, content)
                    elif atype == "Complete": auto_msg = quest_manager.complete_quest(channel_id, content)
                elif tool == "NPC" and atype == "Add":
                    if ":" in content:
                        name, desc = content.split(":", 1)
                        character_sheet.npc_memory.add_npc(channel_id, name.strip(), desc.strip())
                        auto_msg = f"👥 **[NPC 등록]** {name.strip()}"
                    else:
                        character_sheet.npc_memory.add_npc(channel_id, content, "Auto-generated NPC")
                        auto_msg = f"👥 **[NPC 등록]** {content}"
            
            # 2단계: 서사 생성
            full_prompt = (
                f"### [WORLD & MEMORY]\n{world_ctx}\n{obj_ctx}\n\n"
                f"### [GM BRAIN ANALYSIS]\nObs: {nvc_res.get('Observation')}\nNeed: {nvc_res.get('Need')}\n\n"
                f"### [ACTION]\n{action_text}\n\n"
                "Respond in Korean as the Narrator."
            )

            if client_genai:
                loading_msg = await message.channel.send("⏳ **[Lorekeeper]** 서사를 집필 중입니다...")
                session = persona.create_risu_style_session(client_genai, MODEL_ID, lore_txt, rule_txt, active_genres, custom_tone)
                for h in domain.get('history', []):
                    r = "user" if h['role'] == "User" else "model"
                    session.history.append(types.Content(role=r, parts=[types.Part(text=h['content'])]))
                
                response = await persona.generate_response_with_retry(client_genai, session, full_prompt)
                
                try: await loading_msg.delete()
                except discord.NotFound: pass
            else:
                response = "⚠️ AI Error: Gemini Client is not initialized."

            if auto_msg: await message.channel.send(f"🤖 **[AI 판단]** {auto_msg}")
            if response:
                await send_long_message(message.channel, response)
                domain_manager.append_history(channel_id, "User", action_text)
                domain_manager.append_history(channel_id, "Char", response)

    except Exception as e:
        logging.error(f"Main Error: {e}")

if __name__ == "__main__":
    if DISCORD_TOKEN: client_discord.run(DISCORD_TOKEN)