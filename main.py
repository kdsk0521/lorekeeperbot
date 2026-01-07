"""
Lorekeeper TRPG Bot - Main Module
Version: 3.1 (Refactored)
"""

import discord
import os
import asyncio
import logging
import io
import re
from typing import Optional, Tuple, List
from dotenv import load_dotenv
from google import genai
from google.genai import types

# =========================================================
# 상수 정의
# =========================================================
MAX_DISCORD_MESSAGE_LENGTH = 2000
SUPPORTED_TEXT_EXTENSIONS = ['.txt', '.md', '.json', '.log', '.py', '.yaml', '.yml']
VERSION = "3.1"

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
except ImportError as e:
    print(f"CRITICAL ERROR: 필수 모듈을 찾을 수 없습니다. {e}")
    exit(1)

# =========================================================
# 로깅 설정
# =========================================================
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s'
)

# =========================================================
# 환경 변수 로드
# =========================================================
load_dotenv()
DISCORD_TOKEN = os.getenv('DISCORD_TOKEN')
GEMINI_API_KEY = os.getenv('GEMINI_API_KEY')
MODEL_ID = os.getenv('GEMINI_MODEL_VERSION', 'gemini-2.0-flash')

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
# 유틸리티 함수
# =========================================================
async def send_long_message(channel, text: str) -> None:
    """2000자가 넘는 메시지를 나누어 전송하는 함수"""
    if not text:
        return
    
    if len(text) <= MAX_DISCORD_MESSAGE_LENGTH:
        await channel.send(text)
        return
    
    # 메시지 분할 전송
    for i in range(0, len(text), MAX_DISCORD_MESSAGE_LENGTH):
        chunk = text[i:i + MAX_DISCORD_MESSAGE_LENGTH]
        await channel.send(chunk)


async def read_attachment_text(attachment) -> Tuple[Optional[str], Optional[str]]:
    """
    첨부파일에서 텍스트를 읽어옵니다.
    
    Returns:
        Tuple[Optional[str], Optional[str]]: (텍스트 내용, 에러 메시지)
    """
    filename_lower = attachment.filename.lower()
    
    # 지원되는 확장자인지 확인
    if not any(filename_lower.endswith(ext) for ext in SUPPORTED_TEXT_EXTENSIONS):
        return None, f"⚠️ **지원하지 않는 파일입니다.**\n지원 확장자: {', '.join(SUPPORTED_TEXT_EXTENSIONS)}"
    
    try:
        data = await attachment.read()
        text = data.decode('utf-8')
        return text, None
    except UnicodeDecodeError:
        return None, f"⚠️ 파일 `{attachment.filename}` 읽기 실패: UTF-8 인코딩이 아닙니다."
    except Exception as e:
        return None, f"⚠️ 파일 `{attachment.filename}` 읽기 실패: {e}"


async def safe_delete_message(message) -> None:
    """메시지를 안전하게 삭제합니다."""
    try:
        await message.delete()
    except discord.NotFound:
        pass
    except discord.Forbidden:
        logging.warning("메시지 삭제 권한이 없습니다.")
    except Exception as e:
        logging.warning(f"메시지 삭제 실패: {e}")


# =========================================================
# 명령어 핸들러
# =========================================================
async def handle_cheat_command(message, channel_id: str, args: List[str]) -> Optional[str]:
    """
    치트 명령어를 처리합니다.
    
    Args:
        message: Discord 메시지 객체
        channel_id: 채널 ID
        args: 명령어 인자 리스트
    
    Returns:
        응답 메시지 또는 None
    """
    if not args or args[0] == '':
        return "🛠️ **치트 명령어:**\n`!치트 경험치 [숫자]`\n`!치트 퀘스트 [추가/완료] [내용]`\n`!치트 메모 [추가/삭제] [내용]`"
    
    category = args[0]
    
    # 경험치 치트
    if category in ['xp', '경험치']:
        if len(args) < 2:
            return "❌ 사용법: `!치트 경험치 [숫자]`"
        
        try:
            amount = int(args[1])
        except ValueError:
            return "❌ 경험치는 숫자로 입력해주세요."
        
        uid = str(message.author.id)
        p_data = domain_manager.get_participant_data(channel_id, uid)
        
        if not p_data:
            return "❌ 캐릭터가 없습니다. `!가면`으로 먼저 등록하세요."
        
        growth_system = domain_manager.get_growth_system(channel_id)
        new_data, msg, _ = simulation_manager.gain_experience(p_data, amount, growth_system)
        domain_manager.save_participant_data(channel_id, uid, new_data)
        return f"🛠️ **[치트]** {msg}"
    
    # 퀘스트 치트
    elif category in ['quest', '퀘스트']:
        if len(args) < 3:
            return "❌ 사용법: `!치트 퀘스트 [추가/완료] [내용]`"
        
        action = args[1]
        content = args[2]
        
        if action in ['추가', 'add']:
            result = quest_manager.add_quest(channel_id, content)
            return f"🛠️ {result}"
        elif action in ['완료', 'complete']:
            result = quest_manager.complete_quest(channel_id, content)
            return f"🛠️ {result}"
        else:
            return "❌ 퀘스트 동작은 `추가` 또는 `완료`만 가능합니다."
    
    # 메모 치트
    elif category in ['memo', '메모']:
        if len(args) < 3:
            return "❌ 사용법: `!치트 메모 [추가/삭제] [내용]`"
        
        action = args[1]
        content = args[2]
        
        if action in ['추가', 'add']:
            result = quest_manager.add_memo(channel_id, content)
            return f"🛠️ {result}"
        elif action in ['삭제', 'remove', 'delete']:
            result = quest_manager.remove_memo(channel_id, content)
            return f"🛠️ {result}"
        else:
            return "❌ 메모 동작은 `추가` 또는 `삭제`만 가능합니다."
    
    return "⚠️ 알 수 없는 치트 명령입니다."


async def handle_lore_command(message, channel_id: str, arg: str) -> None:
    """로어 명령어를 처리합니다."""
    file_text = ""
    is_file_processed = False
    
    # 첨부파일 처리
    if message.attachments:
        for att in message.attachments:
            text, error = await read_attachment_text(att)
            if error:
                await message.channel.send(error)
                return
            if text:
                file_text = text
                is_file_processed = True
                break
        
        # 첨부파일이 있지만 처리되지 않았고, 텍스트 인자도 없는 경우
        if not is_file_processed and not arg:
            await message.channel.send(
                f"⚠️ **지원하지 않는 파일입니다.**\n"
                f"지원 확장자: {', '.join(SUPPORTED_TEXT_EXTENSIONS)}"
            )
            return
    
    full = (arg + "\n" + file_text).strip()
    
    # 로어 조회
    if not full:
        summary = domain_manager.get_lore_summary(channel_id)
        display_text = summary if summary else domain_manager.get_lore(channel_id)
        title = "[핵심 요약본]" if summary else "[원본 로어]"
        
        if display_text == domain_manager.DEFAULT_LORE:
            await message.channel.send(
                "📜 저장된 로어가 없습니다. `!로어 [내용]` 또는 텍스트 파일을 업로드하세요."
            )
            return
        
        await send_long_message(message.channel, f"📜 **{title}**\n{display_text}")
        return
    
    # 로어 초기화
    if full == "초기화":
        domain_manager.reset_lore(channel_id)
        domain_manager.set_active_genres(channel_id, ["noir"])
        domain_manager.set_custom_tone(channel_id, None)
        await message.channel.send("📜 초기화됨")
        return
    
    # 로어 저장
    if file_text:
        domain_manager.reset_lore(channel_id)  # 파일 업로드 시 기존 로어 리셋
    
    domain_manager.append_lore(channel_id, full)
    status_msg = await message.channel.send("📜 **로어 저장됨.** (AI 분석 준비 중...)")
    
    # AI 분석
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
            for n in npcs:
                character_sheet.npc_memory.add_npc(channel_id, n.get("name"), n.get("description"))
            
            rules = await memory_system.analyze_location_rules_from_lore(client_genai, MODEL_ID, raw_lore)
            if rules:
                domain_manager.set_location_rules(channel_id, rules)
            
            await status_msg.edit(
                content=f"✅ **[완료]** 핵심 요약본 및 분석 완료.\n**장르:** {res.get('genres')}"
            )
        except Exception as e:
            logging.error(f"Lore Analysis Error: {e}")
            await status_msg.edit(content=f"⚠️ **분석 중 오류 발생:** {e}")
    else:
        await status_msg.edit(content="📜 저장 완료 (⚠️ API 키 없음: AI 분석 건너뜀)")


async def handle_rule_command(message, channel_id: str, arg: str) -> None:
    """룰 명령어를 처리합니다."""
    file_text = ""
    
    # 첨부파일 처리
    if message.attachments:
        for att in message.attachments:
            if att.filename.lower().endswith('.txt'):
                try:
                    data = await att.read()
                    file_text = data.decode('utf-8')
                    break
                except Exception as e:
                    await message.channel.send(f"⚠️ 파일 읽기 실패: {e}")
                    return
    
    # 룰 저장 또는 초기화
    if file_text or arg:
        if arg == "초기화":
            domain_manager.reset_rules(channel_id)
            await message.channel.send("📘 초기화됨")
            return
        
        content = file_text if file_text else arg
        domain_manager.append_rules(channel_id, content)
        await message.channel.send("📘 룰 업데이트")
        return
    
    # 룰 조회
    await send_long_message(
        message.channel,
        f"📘 **현재 룰:**\n{domain_manager.get_rules(channel_id)}"
    )


async def handle_chronicle_command(message, channel_id: str, arg: str) -> None:
    """연대기 명령어를 처리합니다."""
    # 연대기 생성 (AI 요약)
    if arg == "생성":
        msg = await message.channel.send("⏳ **[AI]** 현재까지의 이야기를 연대기로 요약 중입니다...")
        
        if not client_genai:
            await msg.edit(content="⚠️ AI 미연동 상태입니다.")
            return
        
        result_text = await quest_manager.generate_chronicle_from_history(client_genai, MODEL_ID, channel_id)
        await safe_delete_message(msg)
        await send_long_message(message.channel, result_text)
        return
    
    # 연대기 추출 (파일 다운로드)
    elif arg == "추출":
        txt_data, msg = quest_manager.export_lore_book_file(channel_id)
        
        if not txt_data:
            await message.channel.send(msg)
            return
        
        with io.BytesIO(txt_data.encode('utf-8')) as f:
            await message.channel.send(msg, file=discord.File(f, filename="chronicles.txt"))
        return
    
    # 연대기 목록 조회 (기본)
    await send_long_message(message.channel, quest_manager.get_lore_book(channel_id))


async def handle_npc_info_command(message, channel_id: str, npc_name: str) -> None:
    """NPC 정보 조회 명령어를 처리합니다."""
    if not npc_name:
        # 전체 NPC 목록
        summary = character_sheet.get_npc_summary(channel_id)
        if not summary:
            await message.channel.send("⚠️ 등록된 NPC가 없습니다.")
            return
        await send_long_message(message.channel, f"👥 **NPC 목록**\n{summary}")
        return
    
    # 특정 NPC 조회
    npcs = domain_manager.get_npcs(channel_id)
    npc_data = npcs.get(npc_name)
    
    if npc_data:
        status = npc_data.get('status', 'Active')
        desc = npc_data.get('desc', '설명 없음')
        await message.channel.send(f"👤 **{npc_name}** ({status})\n{desc}")
    else:
        await message.channel.send(f"⚠️ '{npc_name}'라는 NPC를 찾을 수 없습니다.")


async def handle_info_command(message, channel_id: str) -> None:
    """내정보 명령어를 처리합니다."""
    uid = str(message.author.id)
    p = domain_manager.get_participant_data(channel_id, uid)
    
    if not p:
        await message.channel.send("❌ 정보 없음. `!가면`으로 먼저 등록하세요.")
        return
    
    if not client_genai:
        await message.channel.send(f"👤 **[{p.get('mask')}]**\n{p.get('description')}")
        return
    
    wait_msg = await message.channel.send("⏳ **[AI]** 분석 중...")
    
    # AI 분석 실행
    view_data = await quest_manager.generate_character_info_view(
        client_genai, MODEL_ID, channel_id, uid,
        p.get('description', ''), p.get('inventory', {})
    )
    
    if view_data:
        # 분석된 요약 정보를 저장
        domain_manager.save_participant_summary(channel_id, uid, view_data)
        
        relationships = view_data.get("relationships", [])
        rel_text = "\n".join([f"- {r}" for r in relationships]) if relationships else "- 없음"
        
        final_msg = (
            f"👤 **[{p.get('mask')}]**\n"
            f"👁️ **외형:** {view_data.get('appearance_summary', '정보 없음')}\n"
            f"💰 **재산:** {view_data.get('assets_summary', '정보 없음')}\n"
            f"🤝 **관계:**\n{rel_text}"
        )
        
        await safe_delete_message(wait_msg)
        await send_long_message(message.channel, final_msg)
    else:
        await wait_msg.edit(content="⚠️ 분석 실패")


async def process_ai_system_action(message, channel_id: str, sys_action: dict) -> Optional[str]:
    """AI가 제안한 시스템 액션을 처리합니다."""
    if not sys_action or not isinstance(sys_action, dict):
        return None
    
    tool = sys_action.get("tool")
    atype = sys_action.get("type")
    content = sys_action.get("content")
    
    if not all([tool, atype, content]):
        return None
    
    auto_msg = None
    
    if tool == "Memo":
        if atype == "Add":
            auto_msg = quest_manager.add_memo(channel_id, content)
        elif atype == "Remove":
            auto_msg = quest_manager.remove_memo(channel_id, content)
        elif atype == "Archive":
            auto_msg = quest_manager.resolve_memo_auto(channel_id, content)
    
    elif tool == "Quest":
        if atype == "Add":
            auto_msg = quest_manager.add_quest(channel_id, content)
        elif atype == "Complete":
            auto_msg = quest_manager.complete_quest(channel_id, content)
    
    elif tool == "NPC" and atype == "Add":
        if ":" in content:
            name, desc = content.split(":", 1)
            character_sheet.npc_memory.add_npc(channel_id, name.strip(), desc.strip())
            auto_msg = f"👥 NPC: {name.strip()}"
        else:
            character_sheet.npc_memory.add_npc(channel_id, content, "Auto")
            auto_msg = f"👥 NPC: {content}"
    
    elif tool == "XP" and atype == "Award":
        try:
            match = re.match(r"(\d+)\s*(?:\((.*)\))?", str(content))
            if match:
                xp_amount = int(match.group(1))
                reason = match.group(2) or "Activity"
                uid = str(message.author.id)
                p_data = domain_manager.get_participant_data(channel_id, uid)
                
                if p_data:
                    growth_system = domain_manager.get_growth_system(channel_id)
                    new_data, xp_msg, _ = simulation_manager.gain_experience(
                        p_data, xp_amount, growth_system
                    )
                    domain_manager.save_participant_data(channel_id, uid, new_data)
                    auto_msg = f"⚔️ **성과 확인:** {reason}\n{xp_msg}"
        except Exception as e:
            logging.error(f"Auto XP Error: {e}")
    
    return auto_msg


# =========================================================
# Discord 이벤트 핸들러
# =========================================================
@client_discord.event
async def on_ready():
    """봇 준비 완료 시 실행"""
    domain_manager.initialize_folders()
    print(f"--- Lorekeeper V{VERSION} Online ({client_discord.user}) ---")
    print(f"Model: {MODEL_ID}")


@client_discord.event
async def on_message(message):
    """메시지 수신 시 실행"""
    # 봇 자신의 메시지 또는 빈 메시지 무시
    if message.author == client_discord.user or not message.content:
        return
    
    try:
        channel_id = str(message.channel.id)
        
        # 봇 On/Off 명령어
        if message.content == "!off":
            domain_manager.set_bot_disabled(channel_id, True)
            await message.channel.send("🔇 Off")
            return
        
        if message.content == "!on":
            domain_manager.set_bot_disabled(channel_id, False)
            await message.channel.send("🔊 On")
            return
        
        # 봇이 비활성화된 경우 무시
        if domain_manager.is_bot_disabled(channel_id):
            return
        
        # 입력 파싱
        parsed = input_handler.parse_input(message.content)
        if not parsed:
            return
        
        cmd = parsed.get('command')
        
        # =========================================================
        # 보안: 참가자 및 잠금 확인
        # =========================================================
        is_participant = domain_manager.get_participant_data(
            channel_id, str(message.author.id)
        ) is not None
        domain_data = domain_manager.get_domain(channel_id)
        is_locked = domain_data['settings'].get('session_locked', False)
        
        # 비참가자가 사용 가능한 명령어
        entry_commands = [
            'ready', 'reset', 'start', 'mask', 'lore', 'rule', 'system'
        ]
        
        if not is_participant:
            if is_locked:
                return  # 잠긴 세션에서 비참가자 무시
            if parsed['type'] == 'command':
                if cmd not in entry_commands:
                    return
            else:
                return
        
        # 준비되지 않은 세션에서 허용되는 명령어
        if not domain_manager.is_prepared(channel_id):
            allowed_before_ready = ['ready', 'lore', 'rule', 'reset', 'system']
            if parsed['type'] != 'command' or cmd not in allowed_before_ready:
                await message.channel.send("⚠️ `!준비`를 먼저 해주세요.")
                return
        
        system_trigger = None
        
        # =========================================================
        # 명령어 처리
        # =========================================================
        if parsed['type'] == 'command':
            
            # --- 세션 관리 ---
            if cmd == 'reset':
                await session_manager.manager.execute_reset(
                    message, client_discord, domain_manager, character_sheet
                )
                return
            
            if cmd == 'ready':
                await session_manager.manager.check_preparation(message, domain_manager)
                return
            
            if cmd == 'start':
                domain_manager.update_participant(channel_id, message.author)
                if await session_manager.manager.start_session(
                    message, client_genai, MODEL_ID, domain_manager
                ):
                    system_trigger = "[System: Generate a visceral opening scene for the campaign.]"
                else:
                    return
            
            if cmd == 'unlock':
                domain_manager.set_session_lock(channel_id, False)
                await message.channel.send("🔓 **잠금 해제**")
                return
            
            if cmd == 'lock':
                domain_manager.set_session_lock(channel_id, True)
                await message.channel.send("🔒 **세션 잠금**")
                return
            
            # --- 시스템 설정 ---
            if cmd == 'system':
                args = parsed['content'].strip().split()
                if not args:
                    await message.channel.send("⚙️ 사용법: `!시스템 성장 [기본/헌터/DND/커스텀]`")
                    return
                
                if args[0] in ['성장', 'growth']:
                    if len(args) < 2:
                        current = domain_manager.get_growth_system(channel_id)
                        await message.channel.send(f"📊 **현재 성장:** `{current}`")
                        return
                    
                    domain_manager.set_growth_system(channel_id, args[1].lower())
                    await message.channel.send(f"✅ 설정 완료: `{args[1].lower()}`")
                return
            
            # --- 치트 모드 ---
            if cmd == 'cheat':
                args = parsed['content'].strip().split(' ', 2)
                result = await handle_cheat_command(message, channel_id, args)
                if result:
                    await message.channel.send(result)
                return
            
            # --- 로어 명령어 ---
            if cmd == 'lore':
                await handle_lore_command(message, channel_id, parsed['content'].strip())
                return
            
            # --- 모드 전환 ---
            if cmd == 'mode':
                arg = parsed['content'].strip()
                if '수동' in arg:
                    domain_manager.set_response_mode(channel_id, 'manual')
                    await message.channel.send("🛑 수동 모드")
                elif '자동' in arg:
                    domain_manager.set_response_mode(channel_id, 'auto')
                    await message.channel.send("⏩ 자동 모드")
                else:
                    current = domain_manager.get_response_mode(channel_id)
                    await message.channel.send(f"⚙️ 현재: {current}")
                return
            
            # --- 진행/턴 ---
            if cmd in ['next', 'turn']:
                if '시간' in parsed['content']:
                    await message.channel.send(world_manager.advance_time(channel_id))
                    system_trigger = "[System: Time passes.]"
                else:
                    system_trigger = "[System: Resolve actions.]"
                    await message.add_reaction("🎬")
            
            # --- 캐릭터 관리 ---
            if cmd == 'mask':
                target = parsed['content']
                status = domain_manager.get_participant_status(channel_id, message.author.id)
                
                if status == "left":
                    domain_manager.update_participant(channel_id, message.author, True)
                    await message.channel.send("🆕 환생 완료")
                
                domain_manager.update_participant(channel_id, message.author)
                domain_manager.set_user_mask(channel_id, message.author.id, target)
                await message.channel.send(f"🎭 가면: {target}")
                return
            
            if cmd == 'desc':
                domain_manager.update_participant(channel_id, message.author)
                domain_manager.set_user_description(
                    channel_id, message.author.id, parsed['content']
                )
                await message.channel.send("📝 저장됨")
                return
            
            if cmd == 'info':
                await handle_info_command(message, channel_id)
                return
            
            if cmd == 'status':
                await send_long_message(
                    message.channel,
                    quest_manager.get_status_message(channel_id)
                )
                return
            
            # --- 참가자 상태 ---
            if cmd == 'afk':
                domain_manager.set_participant_status(channel_id, message.author.id, "afk")
                await message.channel.send("💤")
                return
            
            if cmd == 'leave':
                domain_manager.set_participant_status(
                    channel_id, message.author.id, "left", "이탈"
                )
                await message.channel.send("🚪")
                return
            
            if cmd == 'back':
                domain_manager.update_participant(channel_id, message.author)
                await message.channel.send("✨")
                return
            
            # --- 룰 명령어 ---
            if cmd == 'rule':
                await handle_rule_command(message, channel_id, parsed['content'].strip())
                return
            
            # --- 연대기 ---
            if cmd == 'lores':
                await handle_chronicle_command(message, channel_id, parsed['content'].strip())
                return
            
            # --- 내보내기 ---
            if cmd == 'export':
                mode = parsed.get('content', '').strip()
                lore = domain_manager.get_lore(channel_id)
                ch, msg = quest_manager.export_chronicles_incremental(channel_id, mode)
                
                if not ch:
                    await message.channel.send(msg)
                    return
                
                content = f"=== LORE ===\n{lore}\n\n{ch}"
                with io.BytesIO(content.encode('utf-8')) as f:
                    await message.channel.send(msg, file=discord.File(f, filename="export.txt"))
                return
            
            # --- NPC 정보 ---
            if cmd == 'npc':
                await handle_npc_info_command(
                    message, channel_id, parsed.get('content', '').strip()
                )
                return
            
            # --- AI 분석 도구 (신규) ---
            if cmd == 'analyze' or cmd == 'ooc':
                question = parsed.get('content', '').strip()
                if not question:
                    await message.channel.send(
                        "🔍 **OOC 분석 모드**\n"
                        "사용법: `!분석 [질문]` 또는 `!ooc [질문]`\n"
                        "예: `!분석 이 NPC의 동기는 뭘까?`"
                    )
                    return
                
                if not client_genai:
                    await message.channel.send("⚠️ AI가 연결되지 않았습니다.")
                    return
                
                loading = await message.channel.send("🔍 **[OOC 분석 중...]**")
                
                # 컨텍스트 수집
                lore = domain_manager.get_lore(channel_id)
                history = domain.get('history', [])[-20:]
                hist_text = "\n".join([f"{h['role']}: {h['content']}" for h in history])
                
                # 브레인스토밍 분석 호출
                result = await memory_system.analyze_brainstorming(
                    client_genai, MODEL_ID, hist_text, lore, question
                )
                
                await safe_delete_message(loading)
                
                # 결과 포맷팅
                if result.get("analysis_type") == "error":
                    await message.channel.send(f"⚠️ 분석 실패: {result.get('recommendation')}")
                else:
                    response_text = (
                        f"🔍 **[OOC 분석 결과]**\n\n"
                        f"**현재 상황:** {result.get('current_state_summary', 'N/A')}\n\n"
                    )
                    
                    if result.get('potential_paths'):
                        response_text += "**가능한 경로:**\n"
                        for i, path in enumerate(result.get('potential_paths', [])[:3], 1):
                            response_text += f"{i}. {path.get('path', 'N/A')}\n"
                    
                    if result.get('recommendation'):
                        response_text += f"\n**추천:** {result.get('recommendation')}\n"
                    
                    if result.get('open_questions'):
                        response_text += "\n**열린 질문:**\n"
                        for q in result.get('open_questions', [])[:3]:
                            response_text += f"• {q}\n"
                    
                    await send_long_message(message.channel, response_text)
                return
            
            if cmd == 'consistency':
                if not client_genai:
                    await message.channel.send("⚠️ AI가 연결되지 않았습니다.")
                    return
                
                loading = await message.channel.send("🔍 **[일관성 검사 중...]**")
                
                lore = domain_manager.get_lore(channel_id)
                history = domain.get('history', [])[-30:]
                hist_text = "\n".join([f"{h['role']}: {h['content']}" for h in history])
                
                result = await memory_system.check_narrative_consistency(
                    client_genai, MODEL_ID, hist_text, lore
                )
                
                await safe_delete_message(loading)
                
                response_text = f"📋 **[일관성 검사 결과]**\n\n"
                response_text += f"**전체 일관성:** {result.get('overall_consistency', 'Unknown')}\n\n"
                
                issues = result.get('issues', [])
                if issues:
                    response_text += "**발견된 문제:**\n"
                    for issue in issues[:5]:
                        severity = "🔴" if issue.get('severity') == 'critical' else "🟡"
                        response_text += f"{severity} [{issue.get('category')}] {issue.get('description')}\n"
                else:
                    response_text += "✅ 발견된 문제 없음\n"
                
                threads = result.get('plot_threads', [])
                if threads:
                    response_text += f"\n**활성 플롯 스레드:** {', '.join(threads[:5])}\n"
                
                await send_long_message(message.channel, response_text)
                return
            
            if cmd == 'worldrules':
                if not client_genai:
                    await message.channel.send("⚠️ AI가 연결되지 않았습니다.")
                    return
                
                loading = await message.channel.send("🌍 **[세계 규칙 추출 중...]**")
                
                lore = domain_manager.get_lore(channel_id)
                
                # World Constraints 추출 (memory_system의 새 함수 필요)
                result = await memory_system.extract_world_constraints(
                    client_genai, MODEL_ID, lore
                )
                
                await safe_delete_message(loading)
                
                if result:
                    response_text = "🌍 **[세계 규칙]**\n\n"
                    
                    if result.get('setting'):
                        s = result['setting']
                        response_text += f"**배경:** {s.get('era', 'N/A')} / {s.get('location', 'N/A')}\n"
                    
                    if result.get('theme'):
                        t = result['theme']
                        response_text += f"**장르:** {', '.join(t.get('genres', []))}\n"
                        response_text += f"**분위기:** {t.get('tone', 'N/A')}\n"
                    
                    if result.get('systems'):
                        response_text += "\n**시스템 규칙:**\n"
                        for key, val in result['systems'].items():
                            if val:
                                response_text += f"• {key}: {val}\n"
                    
                    if result.get('social', {}).get('taboos'):
                        response_text += f"\n**금기:** {', '.join(result['social']['taboos'][:5])}\n"
                    
                    await send_long_message(message.channel, response_text)
                else:
                    await message.channel.send("⚠️ 세계 규칙 추출 실패")
                return
        
        # =========================================================
        # 주사위 처리
        # =========================================================
        if parsed['type'] == 'dice':
            await message.channel.send(parsed['content'])
            domain_manager.append_history(channel_id, "System", f"Dice: {parsed['content']}")
            return
        
        # =========================================================
        # AI 응답 생성
        # =========================================================
        if parsed['type'] == 'command' and not system_trigger:
            return
        
        domain = domain_manager.get_domain(channel_id)
        if not domain['settings'].get('session_locked', False) and not system_trigger:
            return
        
        async with message.channel.typing():
            if not domain_manager.update_participant(channel_id, message.author):
                return
            
            user_mask = domain_manager.get_user_mask(channel_id, message.author.id)
            action_text = system_trigger if system_trigger else f"[{user_mask}]: {parsed['content']}"
            
            # 수동 모드에서는 기록만 하고 AI 응답 생성 안 함
            response_mode = domain_manager.get_response_mode(channel_id)
            if response_mode == 'manual' and not system_trigger:
                domain_manager.append_history(channel_id, "User", action_text)
                await message.add_reaction("✏️")
                return
            
            # 컨텍스트 수집
            summary = domain_manager.get_lore_summary(channel_id)
            lore_txt = summary if summary else domain_manager.get_lore(channel_id)
            rule_txt = domain_manager.get_rules(channel_id)
            world_ctx = world_manager.get_world_context(channel_id)
            obj_ctx = quest_manager.get_objective_context(channel_id)
            active_genres = domain_manager.get_active_genres(channel_id)
            custom_tone = domain_manager.get_custom_tone(channel_id)
            
            history = domain.get('history', [])[-10:]
            hist_text = "\n".join([f"{h['role']}: {h['content']}" for h in history])
            hist_text += f"\nUser: {action_text}"
            
            active_quests = domain_manager.get_quest_board(channel_id).get("active", [])
            quest_txt = " | ".join(active_quests) if active_quests else "None"
            
            # AI 분석 (좌뇌)
            nvc_res = {}
            if client_genai:
                nvc_res = await memory_system.analyze_context_nvc(
                    client_genai, MODEL_ID, hist_text, lore_txt, rule_txt, quest_txt
                )
                
                if nvc_res.get("CurrentLocation"):
                    domain_manager.set_current_location(channel_id, nvc_res["CurrentLocation"])
                if nvc_res.get("LocationRisk"):
                    domain_manager.set_current_risk(channel_id, nvc_res["LocationRisk"])
            
            # 시스템 액션 처리
            sys_action = nvc_res.get("SystemAction", {})
            auto_msg = await process_ai_system_action(message, channel_id, sys_action)
            
            # Temporal Orientation 추출
            temporal = nvc_res.get("TemporalOrientation", {})
            temporal_ctx = ""
            if temporal:
                temporal_ctx = (
                    f"### [TEMPORAL ORIENTATION]\n"
                    f"Continuity: {temporal.get('continuity_from_previous', 'N/A')}\n"
                    f"Active Threads: {', '.join(temporal.get('active_threads', []))}\n"
                    f"Off-screen NPCs: {', '.join(temporal.get('offscreen_npcs', []))}\n"
                    f"Focus: {temporal.get('suggested_focus', 'N/A')}\n\n"
                )
            
            # AI 응답 생성 (우뇌) - 강화된 프롬프트
            full_prompt = (
                f"### [WORLD STATE]\n{world_ctx}\n{obj_ctx}\n\n"
                f"{temporal_ctx}"
                f"### [LEFT HEMISPHERE ANALYSIS]\n"
                f"Location: {nvc_res.get('CurrentLocation', 'Unknown')} "
                f"(Risk: {nvc_res.get('LocationRisk', 'Low')})\n"
                f"Physical State: {nvc_res.get('PhysicalState', 'N/A')}\n"
                f"Observation: {nvc_res.get('Observation', 'N/A')}\n"
                f"Need: {nvc_res.get('Need', 'N/A')}\n\n"
                f"### [MATERIAL]\n"
                f"<material>\n{action_text}\n</material>\n\n"
                f"### [DIRECTIVE]\n"
                f"Process <material> as {{{{user}}}}'s attempt. "
                f"Generate NPC reactions and world response ONLY. "
                f"Do NOT generate {{{{user}}}}'s dialogue, thoughts, or decisions. "
                f"3rd person narration. Korean output."
            )
            
            response = "⚠️ AI Error"
            if client_genai:
                loading = await message.channel.send("⏳ **[Lorekeeper]** 집필 중...")
                
                session = persona.create_risu_style_session(
                    client_genai, MODEL_ID, lore_txt, rule_txt, active_genres, custom_tone
                )
                
                # 히스토리 추가
                for h in domain.get('history', []):
                    role = "user" if h['role'] == "User" else "model"
                    session.history.append(
                        types.Content(role=role, parts=[types.Part(text=h['content'])])
                    )
                
                response = await persona.generate_response_with_retry(
                    client_genai, session, full_prompt
                )
                
                await safe_delete_message(loading)
            
            # 결과 전송
            if auto_msg:
                await message.channel.send(f"🤖 {auto_msg}")
            
            if response:
                await send_long_message(message.channel, response)
                domain_manager.append_history(channel_id, "User", action_text)
                domain_manager.append_history(channel_id, "Char", response)
    
    except Exception as e:
        logging.error(f"Main Error: {e}", exc_info=True)
        await message.channel.send(f"⚠️ **시스템 오류 발생:** {e}")


# =========================================================
# 메인 실행
# =========================================================
if __name__ == "__main__":
    if DISCORD_TOKEN:
        client_discord.run(DISCORD_TOKEN)
    else:
        print("ERROR: DISCORD_TOKEN이 설정되지 않았습니다.")
