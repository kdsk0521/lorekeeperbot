"""
Lorekeeper TRPG Bot - Main Module
Version: 3.2 (Modularized)
"""

import discord
import os
import asyncio
import logging
import io
import re
import json
from typing import Optional, Tuple, List, Dict
from dotenv import load_dotenv
from google import genai
from collections import defaultdict, deque
from time import time

# =========================================================
# 모듈 임포트
# =========================================================
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
    import fermentation
    import left_brain_analysis
    import left_brain_extraction
    
    # New Modules
    import bot_utils
    import command_handler
    import system_handler
    from bot_utils import send_long_message, safe_delete_message, rate_limiter

except ImportError as e:
    print(f"CRITICAL ERROR: 필수 모듈을 찾을 수 없습니다. {e}")
    exit(1)

# =========================================================
# 로깅 설정
# =========================================================
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.FileHandler("bot_runtime.log", encoding='utf-8'),
        logging.StreamHandler()
    ]
)

# =========================================================
# 환경 변수 로드
# =========================================================
import config

# =========================================================
# 환경 변수 및 설정 로드
# =========================================================
DISCORD_TOKEN = config.DISCORD_TOKEN
GEMINI_API_KEY = config.GEMINI_API_KEY
MODEL_ID = config.MODEL_ID
MODEL_ID_FLASH = config.MODEL_ID_FLASH

# =========================================================
# API 클라이언트 초기화
# =========================================================
if not GEMINI_API_KEY:
    logging.warning("GEMINI_API_KEY가 설정되지 않았습니다!")

client_genai = None
try:
    if GEMINI_API_KEY:
        client_genai = genai.Client(api_key=GEMINI_API_KEY)
except Exception as e:
    logging.error(f"Gemini 클라이언트 초기화 실패: {e}")

# =========================================================
# Discord 클라이언트 초기화
# =========================================================
intents = discord.Intents.default()
intents.message_content = True
client_discord = discord.Client(intents=intents)

# =========================================================
# Per-channel locks
# =========================================================
channel_locks: Dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)


# =========================================================
# Discord 이벤트 핸들러
# =========================================================
@client_discord.event
async def on_ready():
    logging.info(f'Logged in as {client_discord.user} (ID: {client_discord.user.id})')
    logging.info(f'System Version: {bot_utils.VERSION if hasattr(bot_utils, "VERSION") else "3.2"}')
    await client_discord.change_presence(activity=discord.Game(name="!도움 | TRPG Session"))


@client_discord.event
async def on_message(message):
    if message.author == client_discord.user:
        return

    # 단순 텍스트 채널만 처리 (스레드 등 제외 가능하나 일단 허용)
    if not isinstance(message.channel, (discord.TextChannel, discord.Thread)):
        return

    # 봇 멘션 또는 !로 시작하는 경우 처리 (혹은 모든 메시지)
    # 여기서는 모든 메시지를 처리하되, 내부에서 구분
    
    # 비동기 처리
    asyncio.create_task(_process_message(message))


async def _process_message(message):
    channel_id = str(message.channel.id)

    # 락 획득 (채널별 순차 처리)
    async with channel_locks[channel_id]:
        try:
            content = message.content.strip()
            
            # 첨부파일 처리 (텍스트 파일)
            if not content and message.attachments:
                # 텍스트 파일이 있으면 내용을 content로 간주 (단, 명령어가 아닐 경우)
                # 명령어 처리는 command_handler에서 수행하므로, 여기서는 단순히 로그용이나 
                # 일반 대화용으로 텍스트를 읽을지 결정해야 함.
                # 일단 input_handler가 처리하도록 둠.
                pass

            # 입력 파싱
            parsed = input_handler.parse_input(content)
            
            if not parsed:
                # 파싱 실패 혹은 무시할 내용
                # 첨부파일만 있는 경우(이미지 등)는 무시될 수 있음
                # 단, 텍스트 파일 첨부 시 !명령어가 없으면 일반 대화로 처리될 수 있음
                # (input_handler 수정 필요할 수 있으나 유지는 기존 로직 따름)
                
                # 기존 로직: 텍스트 파일 내용을 읽어서 대화로 처리하는 부분이 있었는지 확인
                # 없었으면 패스
                pass

            # =========================================================
            # 1. 명령어 처리 (!command)
            # =========================================================
            if parsed and parsed['type'] == 'command':
                # Command Handler 위임
                system_trigger = await command_handler.dispatch_command(
                    parsed['command'], message, channel_id, parsed, 
                    client_discord, client_genai, MODEL_ID, MODEL_ID_FLASH,
                    domain_manager.get_domain(channel_id) # 필요한 경우 도메인 데이터 전달
                )
                
                # 시스템 트리거가 반환되면 AI 생성 실행 (예: !시작, !주사위 등에서 리턴 가능)
                if system_trigger:
                    await generate_ai_response(message, channel_id, system_trigger)
                return

            # =========================================================
            # 2. 세션 잠금 확인 (명령어는 통과, 대화는 차단)
            # =========================================================
            if domain_manager.is_session_locked(channel_id):
                participant_status = domain_manager.get_participant_status(channel_id, message.author.id)
                # 참가자(active, afk, left)가 아니면 무시
                if not participant_status:
                    return

            # =========================================================
            # 3. 주사위 / OOC 처리 (Command Handler에서 처리하지 않는 타입들)
            # =========================================================
            # input_handler가 dice/ooc를 'command'가 아닌 별도 타입으로 리턴함. 
            # command_handler.dispatch에서 처리하도록 수정했으므로, 
            # 여기서는 command_handler를 호출해야 함.
            
            # 단, dispatch는 cmd 문자열을 받는데, dice/ooc는 cmd가 없음.
            # command_handler에 별도 진입점을 만들거나, 로직을 가져와야 함.
            # 현재 command_handler.dispatch_command는 cmd 문자열 기반임.
            # 따라서 dice/ooc는 여기서 별도로 처리하거나 command_handler를 확장해야 함.
            
            # 리팩토링: dice와 ooc 로직도 command_handler 내에 정적 함수나 별도 함수로 둠?
            # 아니면 dispatch_command 내에서 parsed['type']을 보고 처리?
            # -> dispatch_command 내에 주사위/OOC 처리 로직을 넣었음.
            # 하지만 dispatch_command는 cmd 인자를 요구함.
            
            # 수정: dispatch_command 호출 방식을 변경하거나, 아래 로직을 유지.
            # command_handler.dispatch_command 코드에는 dice/ooc 처리가 포함되어 있음 (lines 351, 358 등)
            # 하지만 cmd 인자 체크 블록 밖이라 도달하지 않을 수 있음.
            # -> command_handler 코드를 다시 보니, cmd 체크 후 맨 아래에 return None.
            # dice/ooc 처리가 블록 밖이 아니라 cmd if-elif 구조임.
            # 아, command_handler 코드의 351라인은 cmd if문들이 끝난 뒤가 아니라
            # 들여쓰기가 되어있나? 확인 필요.
            # 작성한 command_handler.py를 보면 dice/ooc 처리가 함수 맨 끝에 있음 (indentation level 1).
            # 즉 cmd 매칭이 안 되면 주사위/OOC로 넘어감.
            # 따라서 cmd로 'dice'나 'ooc' 같은 가짜 커맨드를 넘기거나, None을 넘겨도 됨.
            
            if parsed and parsed['type'] in ['dice', 'ooc', 'chat_with_ooc']:
                await command_handler.dispatch_command(
                    None, message, channel_id, parsed,
                    client_discord, client_genai, MODEL_ID, MODEL_ID_FLASH,
                    domain_manager.get_domain(channel_id)
                )
                return

            # =========================================================
            # 4. 일반 대화 처리 (AI 응답 생성)
            # =========================================================
            # prepare_context 등은 main에 남겨두거나 simulation_manager로 이동 가능.
            # 일단 main에 generate_ai_response 함수가 있으니 호출.
            
            # 모드 확인
            mode = domain_manager.get_response_mode(channel_id)
            
            # 대기 모드면 기록만 하고 종료
            if mode == 'waiting':
                # 사용자 메시지 기록
                role_name = domain_manager.get_user_mask(channel_id, message.author.id)
                content_to_log = message.content
                
                # 첨부파일 텍스트 병합
                if message.attachments:
                   for att in message.attachments:
                        text, _ = await bot_utils.read_attachment_text(att)
                        if text:
                            content_to_log += f"\n(첨부: {text})"

                domain_manager.append_history(channel_id, role_name, content_to_log.strip())
                # 반응으로 접수 표시
                await message.add_reaction("✏️")
                return

            # 자동 모드면 응답 생성
            await generate_ai_response(message, channel_id)

        except Exception as e:
            logging.error(f"Message Processing Error: {e}", exc_info=True)
            await message.channel.send(f"⚠️ **오류 발생:** {e}")


async def generate_ai_response(message, channel_id: str, system_trigger: str = None) -> None:
    """AI 응답을 생성하고 전송합니다."""
    from google.genai import types

    # 0. 필수 체크
    if not client_genai:
        await message.channel.send("⚠️ AI 설정이 되어있지 않습니다.")
        return

    domain_data = domain_manager.get_domain(channel_id)
    if not domain_data:
        return

    # 1. 사용자 입력 처리
    user_input = system_trigger if system_trigger else message.content
    user_name = "System"
    
    # 첨부파일 텍스트 병합 (시스템 트리거가 아닐 때만)
    if not system_trigger and message.attachments:
        for att in message.attachments:
            text, error = await bot_utils.read_attachment_text(att)
            if error:
                await message.channel.send(error)
                return
            if text:
                user_input += f"\n(첨부 파일 내용):\n{text}"
    
    user_input = user_input.strip()
    if not user_input and not system_trigger:
        return

    if not system_trigger:
        user_name = domain_manager.get_user_mask(channel_id, message.author.id)
        domain_manager.append_history(channel_id, user_name, user_input)

    # 2. 모델 입력 준비
    parsed = input_handler.parse_input(user_input) if not system_trigger else {'content': user_input, 'style': {}}
    
    # 3. AI 처리 및 응답 (Typing Indicator)
    async with message.channel.typing():
        try:
            if not domain_manager.update_participant(channel_id, message.author):
                return
            
            user_mask = domain_manager.get_user_mask(channel_id, message.author.id)
            
            # Action Text Formatting
            if system_trigger:
                action_text = system_trigger
            else:
                style = parsed.get('style', 'Description')
                content = parsed['content'] if parsed else user_input
                
                if style == 'Dialogue':
                    action_text = f"[{user_mask}] says: {content}"
                elif style == 'Action':
                    action_text = f"[{user_mask}] does: {content}"
                else:
                    action_text = f"[{user_mask}]: {content}"

            # 컨텍스트 수집
            lore_txt = domain_manager.get_lore_with_npcs(channel_id)
            rule_txt = domain_manager.get_rules(channel_id)
            world_ctx = world_manager.get_world_context(channel_id)
            obj_ctx = quest_manager.get_objective_context(channel_id)
            active_genres = domain_manager.get_active_genres(channel_id)
            custom_tone = domain_manager.get_custom_tone(channel_id)
            scene_type = domain_manager.get_scene_type(channel_id)
            
            # 좌뇌 분석용 히스토리
            history = domain_data.get('history', [])[-fermentation.RECENT_HISTORY_FOR_ANALYSIS:]
            hist_text = "\n".join([f"{h['role']}: {h['content']}" for h in history])
            hist_text += f"\nUser: {action_text}"
            
            active_quests = domain_manager.get_quest_board(channel_id).get("active", [])
            quest_txt = " | ".join(active_quests) if active_quests else "None"
            
            # 플레이어 컨텍스트
            uid = str(message.author.id)
            p_data = domain_manager.get_participant_data(channel_id, uid)
            player_context = ""
            if p_data:
                player_context = simulation_manager.get_passives_for_context(p_data)
            
            # AI 분석 (좌뇌)
            nvc_res = await left_brain_analysis.analyze_context_nvc(
                client_genai, MODEL_ID, hist_text, lore_txt, rule_txt, quest_txt,
                player_context=player_context
            )
            
            if nvc_res.get("CurrentLocation"):
                domain_manager.set_current_location(channel_id, nvc_res["CurrentLocation"])
            if nvc_res.get("LocationRisk"):
                domain_manager.set_current_risk(channel_id, nvc_res["LocationRisk"])
            
            # 시스템 액션 처리 (System Handler 위임)
            sys_action = nvc_res.get("SystemAction", {})
            auto_msg = await system_handler.process_ai_system_action(message, channel_id, sys_action)

            # 세션 메모리 업데이트
            memory_msgs = memory_system.apply_ai_memory_updates(
                channel_id, uid, nvc_res, domain_manager
            )
            
            # AI 메모리 컨텍스트
            ai_memory_ctx = domain_manager.get_full_ai_context(channel_id, uid)
            
            # 나머지 컨텍스트 빌드 (Temporal, NPC Attitudes, Interaction, Action Judgment)
            # ... (이전 코드에서 로직 복원)
            
            temporal = nvc_res.get("TemporalOrientation", {})
            temporal_ctx = ""
            if temporal:
                temporal_ctx = f"### [TEMPORAL]\nContinuity: {temporal.get('continuity_from_previous', 'N/A')}\nFocus: {temporal.get('suggested_focus', 'N/A')}\n\n"

            npc_attitudes = nvc_res.get("NPCAttitudes", {})
            npc_attitude_ctx = ""
            if npc_attitudes:
                npc_attitude_ctx = "### [NPC ATTITUDES]\n"
                for npc_name, attitude_data in npc_attitudes.items():
                    if isinstance(attitude_data, dict):
                        att = attitude_data.get("attitude", "neutral")
                        npc_attitude_ctx += f"- **{npc_name}**: {att}\n"
                npc_attitude_ctx += "\n"

            npc_interaction = nvc_res.get("NPCInteraction")
            npc_interaction_ctx = ""
            if npc_interaction and isinstance(npc_interaction, dict):
                 npc_interaction_ctx = f"### [NPC INTERACTION]\nParticipants: {npc_interaction.get('participants')}\nTopic: {npc_interaction.get('topic')}\n\n"

            # GM Action Judgment
            action_judgment = nvc_res.get("ActionJudgment")
            action_judgment_ctx = ""
            final_roll_result = None
            if action_judgment and isinstance(action_judgment, dict):
                modifiers_raw = action_judgment.get("modifiers", [])
                rolled_judgment = left_brain_analysis.build_action_judgment_with_roll(
                    action=action_judgment.get("action", "Unknown"),
                    difficulty=action_judgment.get("difficulty", "normal"),
                    difficulty_reason=action_judgment.get("difficulty_reason", ""),
                    modifiers_list=modifiers_raw
                )
                action_judgment_ctx = left_brain_analysis.build_judgment_context_with_roll(rolled_judgment)
                final_roll_result = rolled_judgment

            # Scene Type Auto-detect
            detected_scene_type = nvc_res.get("SceneType", "normal")
            if detected_scene_type and detected_scene_type != "normal":
                manual_scene_type = domain_manager.get_scene_type(channel_id)
                if manual_scene_type == "normal":
                    scene_type = detected_scene_type
            
            # Fermented Context
            fermented_ctx = ""
            try:
                fermented_ctx = fermentation.build_fermented_context(domain_data)
            except Exception:
                pass

            # Full Prompt Construction
            current_context_parts = []
            if obj_ctx and obj_ctx != quest_manager.EMPTY_QUEST_MEMO_MSG:
                current_context_parts.append(f"### [QUESTS & MEMOS]\n{obj_ctx}")
            if world_ctx: current_context_parts.append(f"### World State\n{world_ctx}")
            if temporal_ctx: current_context_parts.append(temporal_ctx)
            if ai_memory_ctx: current_context_parts.append(f"### AI Memory\n{ai_memory_ctx}")
            if npc_attitude_ctx: current_context_parts.append(npc_attitude_ctx)
            if npc_interaction_ctx: current_context_parts.append(npc_interaction_ctx)
            if action_judgment_ctx: current_context_parts.append(action_judgment_ctx)
            
            nvc_summary = f"### Left Brain Analysis\nLoc: {nvc_res.get('CurrentLocation')} (Risk: {nvc_res.get('LocationRisk')})\nObs: {nvc_res.get('Observation')}\nNeed: {nvc_res.get('Need')}"
            current_context_parts.append(nvc_summary)
            
            current_context = "\n\n".join(current_context_parts)
            
            # 발효 요약
            fermented_summaries = [e["summary"] for e in domain_data.get("fermented_history", []) if e.get("summary")]
            fermented_summary_text = "\n---\n".join(fermented_summaries)

            full_prompt = f"{fermented_ctx}\n\n<Current-Context>\n{current_context}\n</Current-Context>\n\n<User_Message>\n### Material\n<material>\n{action_text}\n</material>\n</User_Message>\n\n### [OUTPUT DIRECTIVE]\nGenerate response in Korean. 3rd person."

            # Generate Response
            response = "⚠️ AI Error"
            
            # 세션 생성 및 생성 호출
            try:
                session, used_cache = await persona.create_cached_session(
                    client_genai, MODEL_ID, channel_id, lore_txt, rule_txt, 
                    active_genres, custom_tone, domain_data.get("deep_memory", ""),
                    fermentation_module=fermentation, scene_type=scene_type
                )
            except Exception:
                session = persona.create_risu_style_session(
                    client_genai, MODEL_ID, lore_txt, rule_txt, active_genres, custom_tone,
                    domain_data.get("deep_memory", ""), fermented_summary=fermented_summary_text,
                    character_descriptions="", scene_type=scene_type
                )

            # 히스토리 주입
            for h in domain_data.get('history', []):
                role = "user" if h['role'] == "User" else "model"
                session.history.append(types.Content(role=role, parts=[types.Part(text=h['content'])]))
            
            response = await persona.generate_response_with_retry(client_genai, session, full_prompt)
            
            # Clean Response
            if response:
                response = re.sub(r'```system_update[\s\S]*?```', '', response, flags=re.IGNORECASE).strip()
                response = re.sub(r'system_update[:\s]*\{[^}]+\}', '', response, flags=re.IGNORECASE).strip()

            # 응답 전송 및 결과 처리
            if auto_msg: await message.channel.send(f"🤖 {auto_msg}")
            
            if final_roll_result:
                r_kr = {"success": "✅ 성공", "failure": "❌ 실패", "critical_success": "✨ 대성공", "partial": "⚠️ 부분 성공"}.get(final_roll_result['result'], final_roll_result['result'])
                dice_msg = f"🎲 **[판정] {final_roll_result['action']}**\n결과: **{r_kr}** ({final_roll_result['final_roll']})"
                await message.channel.send(dice_msg)
            
            if memory_msgs:
                for mm in memory_msgs: await message.channel.send(mm)
            
            if response:
                await send_long_message(message.channel, response)
                domain_manager.append_history(channel_id, "User", action_text)
                domain_manager.append_history(channel_id, "Char", response)
                
                # 업데이트 추출 (우뇌 B)
                try:
                    update_result = await left_brain_extraction.extract_all_updates(
                        client_genai, MODEL_ID_FLASH, action_text, response,
                        current_inventory=p_data.get("inventory", {}),
                        current_gold=p_data.get("economy", {}).get("gold", 0),
                        current_status=p_data.get("status_effects", []),
                        current_relationships=domain_manager.get_ai_memory(channel_id, uid).get("relationships", {}),
                        current_passives=domain_manager.get_ai_memory(channel_id, uid).get("passives", []),
                        current_quests=active_quests,
                        current_memos=quest_manager.get_memos(channel_id),
                        lore_npc_names=list(domain_manager.get_npcs(channel_id).keys()),
                        scene_npc_names=list(npc_attitudes.keys())
                    )
                    
                    extract_msgs = []
                    if update_result.get("PlayerUpdate"):
                        extract_msgs.extend(character_sheet.apply_player_updates(channel_id, uid, update_result["PlayerUpdate"]))
                    if update_result.get("PlayerMemoryUpdate"):
                        extract_msgs.extend(character_sheet.apply_memory_updates(channel_id, uid, update_result["PlayerMemoryUpdate"]))
                    if update_result.get("QuestUpdate"):
                        extract_msgs.extend(character_sheet.apply_quest_updates(channel_id, update_result["QuestUpdate"]))
                    
                    if extract_msgs:
                        await message.channel.send(f"📊 {' | '.join(extract_msgs)}")
                        
                    if update_result.get("PassiveSuggestion"):
                        p_data, p_msg = simulation_manager.grant_ai_passive(p_data, update_result["PassiveSuggestion"], current_day=1)
                        if p_msg:
                            await message.channel.send(p_msg)
                            domain_manager.save_participant_data(channel_id, uid, p_data)

                except Exception as ue:
                    logging.warning(f"Update Extraction Failed: {ue}")
                
                # 자동 발효
                try:
                    if fermentation.should_ferment_fresh(domain_data):
                        asyncio.create_task(fermentation.auto_ferment(
                            client_genai, MODEL_ID_FLASH, domain_data,
                            save_callback=lambda: domain_manager.save_domain(channel_id, domain_data)
                        ))
                except Exception:
                    pass

        except Exception as e:
            logging.error(f"Generation Error: {e}", exc_info=True)
            await message.channel.send(f"⚠️ **오류:** {e}")

# =========================================================
# 메인 실행
# =========================================================
def validate_environment() -> bool:
    """환경 변수 검증"""
    if not DISCORD_TOKEN or not GEMINI_API_KEY:
        print("🚨 환경 변수 오류: DISCORD_TOKEN 또는 GEMINI_API_KEY가 없습니다.")
        return False
    return True

if __name__ == "__main__":
    if validate_environment():
        logging.info("Starting Bot...")
        client_discord.run(DISCORD_TOKEN)
    else:
        exit(1)
