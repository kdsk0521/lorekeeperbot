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
import json
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
    import fermentation
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
MODEL_ID = os.getenv('GEMINI_MODEL_VERSION', 'gemini-3-flash-preview')  # Gemini 3 Flash 사용

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
        raw_lore = domain_manager.get_lore(channel_id)
        
        if raw_lore == domain_manager.DEFAULT_LORE or not raw_lore.strip():
            await message.channel.send(
                "📜 저장된 로어가 없습니다. `!로어 [내용]` 또는 텍스트 파일을 업로드하세요."
            )
            return
        
        # 장르 및 톤 정보
        genres = domain_manager.get_active_genres(channel_id)
        custom_tone = domain_manager.get_custom_tone(channel_id)
        
        info_msg = f"📜 **로어 정보**\n\n"
        info_msg += f"**📊 원본 크기:** {len(raw_lore):,}자\n"
        
        if summary:
            info_msg += f"**📦 요약본 크기:** {len(summary):,}자\n"
            info_msg += f"**🗜️ 압축률:** {len(raw_lore) // max(len(summary), 1)}:1\n"
        
        info_msg += f"\n**🎭 장르:** {', '.join(genres) if genres else '미분석'}\n"
        
        if custom_tone:
            info_msg += f"**🎨 톤:** {custom_tone}\n"
        
        await message.channel.send(info_msg)
        
        # 요약본이 있으면 파일로 첨부
        if summary:
            file_content = f"=== Lorekeeper 로어 요약본 ===\n"
            file_content += f"원본: {len(raw_lore):,}자 → 요약: {len(summary):,}자\n"
            file_content += f"장르: {', '.join(genres) if genres else '미분석'}\n"
            file_content += f"{'=' * 40}\n\n"
            file_content += summary
            
            file_buffer = io.BytesIO(file_content.encode('utf-8'))
            file_buffer.seek(0)
            
            await message.channel.send(
                "📄 **요약본 파일:**",
                file=discord.File(file_buffer, filename="lore_summary.txt")
            )
        else:
            # 요약본이 없으면 원본 미리보기
            preview = raw_lore[:500] + "..." if len(raw_lore) > 500 else raw_lore
            await message.channel.send(f"📄 **원본 미리보기:**\n```\n{preview}\n```")
        
        return
    
    # 로어 초기화
    if full == "초기화":
        domain_manager.reset_lore(channel_id)
        domain_manager.set_active_genres(channel_id, ["noir"])
        domain_manager.set_custom_tone(channel_id, None)
        await message.channel.send("📜 **로어 초기화됨** - 장르도 기본값으로 복귀")
        return
    
    # 로어 저장
    is_append = not file_text and domain_manager.get_lore(channel_id).strip()
    
    if file_text:
        domain_manager.reset_lore(channel_id)  # 파일 업로드 시 기존 로어 리셋
    
    domain_manager.append_lore(channel_id, full)
    
    # 로어 크기 확인
    raw_lore = domain_manager.get_lore(channel_id)
    lore_length = len(raw_lore)
    
    # 대용량 로어 여부 판단 (15000자 이상)
    is_massive = lore_length > 15000
    
    action_word = "추가됨" if is_append else "저장됨"
    
    if is_massive:
        estimated_chunks = (lore_length // 15000) + 1
        status_msg = await message.channel.send(
            f"📜 **로어 {action_word}** ({lore_length:,}자)\n"
            f"📚 대용량 로어 처리 모드 (약 {estimated_chunks}개 청크)\n"
            f"⏳ 예상 시간: {estimated_chunks * 10}~{estimated_chunks * 20}초\n"
            f"🔄 **전체 재분석 진행 중...**"
        )
    else:
        status_msg = await message.channel.send(
            f"📜 **로어 {action_word}** ({lore_length:,}자)\n"
            f"🔄 **AI 재분석 중...** (장르, NPC, 규칙)"
        )
    
    # AI 분석
    if client_genai:
        try:
            # 대용량 로어 처리
            if is_massive:
                async def progress_callback(stage, current, total):
                    stage_names = {
                        "splitting": "📂 청크 분할",
                        "compressing": "🗜️ 청크 압축",
                        "merging": "🔗 중간 병합",
                        "finalizing": "✨ 최종 통합"
                    }
                    stage_name = stage_names.get(stage, stage)
                    await status_msg.edit(
                        content=f"📚 **[대용량 로어 처리 중]**\n"
                                f"{stage_name}: {current}/{total}"
                    )
                
                summary, metadata = await memory_system.process_massive_lore(
                    client_genai, MODEL_ID, raw_lore, progress_callback
                )
                
                domain_manager.save_lore_summary(channel_id, summary)
                
                await status_msg.edit(
                    content=f"📚 **[대용량 처리 완료]**\n"
                            f"• 원본: {metadata['original_length']:,}자\n"
                            f"• 압축: {metadata['final_length']:,}자\n"
                            f"• 압축률: {metadata['compression_ratio']}:1\n"
                            f"• 처리 시간: {metadata['processing_time']}초\n"
                            f"• 방식: {metadata['method']}\n\n"
                            f"⏳ 장르/NPC 분석 중..."
                )
            else:
                await status_msg.edit(content="⏳ **[AI]** 세계관 압축 중...")
                summary = await memory_system.compress_lore_core(client_genai, MODEL_ID, raw_lore)
                domain_manager.save_lore_summary(channel_id, summary)
            
            # 장르 분석 (요약본 기반으로 수행 - 토큰 절약)
            await status_msg.edit(content="⏳ **[AI]** 장르 및 NPC 데이터 추출 중...")
            
            # 대용량일 경우 요약본으로 분석, 아니면 원본으로
            analysis_text = summary if is_massive else raw_lore
            
            res = await memory_system.analyze_genre_from_lore(client_genai, MODEL_ID, analysis_text)
            domain_manager.set_active_genres(channel_id, res.get("genres", ["noir"]))
            domain_manager.set_custom_tone(channel_id, res.get("custom_tone"))
            
            npcs = await memory_system.analyze_npcs_from_lore(client_genai, MODEL_ID, analysis_text)
            for n in npcs:
                character_sheet.npc_memory.add_npc(channel_id, n.get("name"), n.get("description"))
            
            rules = await memory_system.analyze_location_rules_from_lore(client_genai, MODEL_ID, analysis_text)
            if rules:
                domain_manager.set_location_rules(channel_id, rules)
            
            # 최종 메시지
            final_msg = f"✅ **[분석 완료]**\n**장르:** {res.get('genres')}"
            if is_massive:
                final_msg += f"\n**압축률:** {metadata['compression_ratio']}:1 ({metadata['original_length']:,}자 → {metadata['final_length']:,}자)"
            
            await status_msg.edit(content=final_msg)
            
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
            await message.channel.send("📘 **룰 초기화** - 기본 룰로 복귀했습니다.")
            return
        
        # 파일 업로드: 완전 커스텀 모드
        if file_text:
            domain_manager.set_custom_rules_from_file(channel_id, file_text)
            await message.channel.send(
                "📘 **완전 커스텀 룰 설정됨**\n"
                "기본 룰이 파일 내용으로 대체되었습니다.\n"
                "_기본 룰로 돌아가려면 `!룰 초기화`_"
            )
            return
        
        # 텍스트 입력: 기본룰 + 커스텀 (하이브리드)
        domain_manager.append_rules(channel_id, arg)
        rules_mode = domain_manager.get_rules_mode(channel_id)
        
        if rules_mode == "hybrid":
            await message.channel.send(
                "📘 **커스텀 룰 추가됨** (기본 룰 + 커스텀)\n"
                f"추가된 내용: {arg[:50]}{'...' if len(arg) > 50 else ''}"
            )
        else:
            await message.channel.send("📘 룰 업데이트됨")
        return
    
    # 룰 조회
    rules_mode = domain_manager.get_rules_mode(channel_id)
    mode_display = {
        "default": "📗 기본 룰",
        "hybrid": "📘 기본 룰 + 커스텀",
        "custom": "📙 완전 커스텀"
    }
    
    await send_long_message(
        message.channel,
        f"**[{mode_display.get(rules_mode, '📘')}]**\n\n{domain_manager.get_rules(channel_id)}"
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
    
    # 연대기 추출 (대화 로그 파일 다운로드 - 증분 지원)
    elif arg.startswith("추출"):
        # "추출 전체" 또는 "추출"
        mode = arg.replace("추출", "").strip()
        ch, msg = quest_manager.export_chronicles_incremental(channel_id, mode)
        
        if not ch:
            await message.channel.send(msg)
            return
        
        # 로어도 함께 포함
        lore = domain_manager.get_lore(channel_id)
        content = f"=== LORE ===\n{lore}\n\n{ch}" if lore else ch
        
        with io.BytesIO(content.encode('utf-8')) as f:
            await message.channel.send(msg, file=discord.File(f, filename="chronicles.txt"))
        return
    
    # 연대기 조회 (기본)
    lore_book = quest_manager.get_lore_book(channel_id)
    await send_long_message(message.channel, lore_book)


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


async def handle_info_command(message, channel_id: str, sub_command: str = "") -> None:
    """
    통합 정보 명령어를 처리합니다.
    
    서브 명령어:
    - (없음): 전체 정보
    - 캐릭터: 외형, 성격, 배경, 소지품
    - 관계: NPC 관계도
    - 패시브: 패시브, 칭호, 비일상 적응
    - 세계: 퀘스트, 메모, 세계상황, 복선, 아는 정보
    """
    uid = str(message.author.id)
    p = domain_manager.get_participant_data(channel_id, uid)
    
    if not p:
        await message.channel.send("❌ 정보 없음. `!가면`으로 먼저 등록하세요.")
        return
    
    mask = p.get('mask', 'Unknown')
    ai_mem = p.get('ai_memory', {})
    sub = sub_command.strip().lower()
    
    # 서브 명령어 별칭 매핑
    sub_aliases = {
        '캐릭터': 'character', 'char': 'character', 'character': 'character', 'c': 'character',
        '관계': 'relation', 'rel': 'relation', 'relation': 'relation', 'r': 'relation',
        '패시브': 'passive', 'passive': 'passive', 'p': 'passive', '칭호': 'passive',
        '세계': 'world', 'world': 'world', 'w': 'world', '월드': 'world',
    }
    sub_type = sub_aliases.get(sub, 'all')
    
    result = f"👤 **[{mask}]**\n\n"
    
    # =========================================================
    # 캐릭터 섹션: 외형, 성격, 배경, 소지품
    # =========================================================
    if sub_type in ['all', 'character']:
        result += "**━━━ 🎭 캐릭터 ━━━**\n"
        
        # 외형
        appearance = ai_mem.get('appearance', '')
        if appearance:
            result += f"👁️ **외형:** {appearance}\n"
        
        # 성격
        personality = ai_mem.get('personality', '')
        if personality:
            result += f"💭 **성격:** {personality}\n"
        
        # 배경
        background = ai_mem.get('background', '')
        if background:
            result += f"📖 **배경:** {background}\n"
        
        # 소지품 (화폐 + 인벤토리 통합)
        economy = p.get('economy', {})
        inventory = p.get('inventory', {})
        status_effects = p.get('status_effects', [])
        
        # 화폐 표시 (세계관에 따라 다를 수 있음, 기본은 골드)
        gold = economy.get('gold', 0)
        currency_name = economy.get('currency_name', '골드')  # AI가 세계관에 맞게 설정
        
        result += f"🎒 **소지품**\n"
        result += f"  💰 {currency_name}: {gold}\n"
        
        if inventory:
            for item, count in inventory.items():
                result += f"  • {item} x{count}\n"
        else:
            result += "  _(인벤토리 비어있음)_\n"
        
        if status_effects:
            result += f"\n💫 **상태이상:** {', '.join(status_effects)}\n"
        
        # 캐릭터 섹션이 비어있으면
        empty_check = f"👤 **[{mask}]**\n\n**━━━ 🎭 캐릭터 ━━━**\n🎒 **소지품**\n  💰 {currency_name}: 0\n  _(인벤토리 비어있음)_\n"
        if result == empty_check:
            result += "_아직 설정된 정보가 없습니다._\n"
        
        result += "\n"
    
    # =========================================================
    # 관계 섹션: NPC 관계도
    # =========================================================
    if sub_type in ['all', 'relation']:
        result += "**━━━ 💞 관계 ━━━**\n"
        
        relationships = ai_mem.get('relationships', {})
        if relationships:
            for name, desc in relationships.items():
                result += f"  • **{name}:** {desc}\n"
        else:
            result += "_아직 형성된 관계가 없습니다._\n"
        
        result += "\n"
    
    # =========================================================
    # 패시브 섹션: 패시브, 칭호, 비일상 적응
    # =========================================================
    if sub_type in ['all', 'passive']:
        result += "**━━━ 🏆 패시브/칭호 ━━━**\n"
        
        passives = ai_mem.get('passives', [])
        if passives:
            for p_name in passives:
                result += f"  • {p_name}\n"
        else:
            result += "_획득한 패시브/칭호가 없습니다._\n"
        
        # 비일상 적응
        normalization = ai_mem.get('normalization', {})
        if normalization:
            result += "\n🌓 **비일상 적응:**\n"
            for thing, status in normalization.items():
                result += f"  • **{thing}:** {status}\n"
        
        result += "\n"
    
    # =========================================================
    # 세계 섹션: 퀘스트, 메모, 세계상황, 복선, 아는 정보
    # =========================================================
    if sub_type in ['all', 'world']:
        result += "**━━━ 🌍 세계 ━━━**\n"
        
        # 퀘스트
        quests = quest_manager.get_active_quests(channel_id)
        if quests:
            result += "📜 **활성 퀘스트:**\n"
            for q in quests[:5]:
                result += f"  • {q}\n"
            if len(quests) > 5:
                result += f"  _... 외 {len(quests) - 5}개_\n"
        
        # 메모
        memos = quest_manager.get_memos(channel_id)
        if memos:
            result += "📝 **메모:**\n"
            for m in memos[:5]:
                result += f"  • {m}\n"
            if len(memos) > 5:
                result += f"  _... 외 {len(memos) - 5}개_\n"
        
        # 알고 있는 정보
        known_info = ai_mem.get('known_info', [])
        if known_info:
            result += "💡 **알고 있는 정보:**\n"
            for info in known_info:
                result += f"  • {info}\n"
        
        # 복선
        foreshadowing = ai_mem.get('foreshadowing', [])
        if foreshadowing:
            result += "🔮 **미해결 복선:**\n"
            for fs in foreshadowing:
                result += f"  • {fs}\n"
        
        # 세션 AI 메모리 (세계 상황)
        session_mem = domain_manager.get_session_ai_memory(channel_id)
        if session_mem:
            current_arc = session_mem.get('current_arc', '')
            if current_arc:
                result += f"\n🎬 **현재 스토리:** {current_arc}\n"
            
            active_threads = session_mem.get('active_threads', [])
            if active_threads:
                result += f"🧵 **진행 중인 이야기:** {', '.join(active_threads[:3])}\n"
            
            world_changes = session_mem.get('world_changes', [])
            if world_changes:
                result += "🌐 **세계 변화:**\n"
                for change in world_changes[:3]:
                    result += f"  • {change}\n"
        
        # 세계 섹션이 비어있으면
        if not any([quests, memos, known_info, foreshadowing, session_mem.get('current_arc') if session_mem else False]):
            result += "_아직 기록된 세계 정보가 없습니다._\n"
        
        result += "\n"
    
    # 도움말 (전체 보기일 때만)
    if sub_type == 'all':
        result += "━━━━━━━━━━━━━━━━━━━\n"
        result += "💡 `!정보 캐릭터` `!정보 관계` `!정보 패시브` `!정보 세계`\n"
        result += "✏️ 수정: `(OOC: 요청 내용)` 형식으로 입력"
    
    await send_long_message(message.channel, result)


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
    
    # XP Award 제거됨 - 성과는 패시브/칭호로 표현
    elif tool == "XP" and atype == "Award":
        # 경험치 시스템 제거됨 - 성과 로깅만
        logging.info(f"[Achievement] {content}")
    
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
            
            # --- 도움말 ---
            if cmd == 'help':
                help_msg = (
                    "📚 **Lorekeeper 명령어 목록**\n\n"
                    
                    "**━━━ 🎭 캐릭터 ━━━**\n"
                    "`!가면 [이름]` - 캐릭터 이름 설정\n"
                    "`!설명 [내용]` - 캐릭터 설명 설정\n"
                    "`!정보` - 캐릭터 정보 조회\n"
                    "  ↳ `!정보 캐릭터` `관계` `패시브` `세계`\n\n"
                    
                    "**━━━ 📜 세션 ━━━**\n"
                    "`!준비` - 세션 준비 상태 확인\n"
                    "`!시작` - 세션 시작\n"
                    "`!진행` - 기록된 행동 종합 후 다음 장면\n"
                    "`!리셋` - 세션 초기화\n\n"
                    
                    "**━━━ 🌍 세계관 ━━━**\n"
                    "`!로어 [파일]` - 세계관 설정\n"
                    "`!룰 [내용]` - 룰 추가 (기본룰 자동 적용)\n"
                    "`!퀘스트 [내용]` - 퀘스트 추가/조회\n"
                    "`!메모 [내용]` - 메모 추가/조회\n"
                    "`!연대기` - 연대기 조회\n"
                    "`!연대기 생성` - AI가 스토리 요약\n"
                    "`!연대기 추출` - 대화 로그 파일 저장 (증분)\n\n"
                    
                    "**━━━ 🎲 기타 ━━━**\n"
                    "`!r [주사위]` - 선택적 주사위 (예: !r 1d20, !r 1d100)\n"
                    "  └ 높을수록 좋은 결과, AI가 서사적으로 해석\n"
                    "`!npc [이름]` - NPC 정보 조회\n"
                    "`!분석 [질문]` - AI OOC 분석\n\n"
                    
                    "**━━━ ✏️ OOC 수정 ━━━**\n"
                    "`(OOC: 요청 내용)` - 캐릭터 정보 수정\n"
                    "예: `(OOC: 리엘이랑 친해진 걸로)`\n\n"
                    
                    "**━━━ 📖 성장 시스템 ━━━**\n"
                    "레벨/경험치 대신 **패시브/칭호**로 성장!\n"
                    "• 패시브: 반복 경험으로 습득 (독 내성, 야간 시야...)\n"
                    "• 칭호: 특별한 업적으로 획득 (드래곤 슬레이어...)\n"
                    "• 적응: 비일상에 노출될수록 익숙해짐\n\n"
                    
                    "**━━━ ⚖️ 판정 시스템 ━━━**\n"
                    "기본: AI가 패시브/칭호/상황으로 판정\n"
                    "선택: 주사위 결과를 AI가 참고하여 해석"
                )
                await send_long_message(message.channel, help_msg)
                return
            
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
            
            # --- 로어 명령어 ---
            if cmd == 'lore':
                await handle_lore_command(message, channel_id, parsed['content'].strip())
                return
            
            # --- 모드 전환 ---
            if cmd == 'mode':
                arg = parsed['content'].strip()
                if '대기' in arg or '수동' in arg:
                    domain_manager.set_response_mode(channel_id, 'waiting')
                    await message.channel.send(
                        "⏸️ **대기 모드**\n"
                        "플레이어 채팅은 기록만 됩니다. (✏️)\n"
                        "`!진행`으로 AI 응답을 받으세요."
                    )
                elif '자동' in arg:
                    domain_manager.set_response_mode(channel_id, 'auto')
                    await message.channel.send("▶️ **자동 모드** - 매 채팅마다 AI가 응답합니다.")
                else:
                    current = domain_manager.get_response_mode(channel_id)
                    mode_name = "대기" if current == "waiting" else "자동"
                    await message.channel.send(
                        f"⚙️ **현재 모드:** {mode_name}\n"
                        f"• `!모드 자동` - 매 채팅마다 AI 응답\n"
                        f"• `!모드 대기` - `!진행` 전까지 기록만"
                    )
                return
            
            # --- 진행/턴 ---
            # 대기 모드에서 모든 플레이어 행동을 모아서 한 번에 처리
            # "아무 말 없이 다음 장면을 본다" - 기록된 모든 행동을 AI가 종합
            if cmd in ['next', 'turn']:
                system_trigger = "[System: 기록된 모든 플레이어 행동을 종합하여 다음 장면을 진행하세요. 각 캐릭터의 행동과 침묵 모두 고려하여 서사적으로 전개합니다.]"
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
                sub_cmd = parsed['content'].strip()
                
                # 기존 명령어에서 리다이렉트된 경우 서브 명령어 자동 매핑
                # (input_handler에서 passive, adaptation, status → info로 매핑됨)
                # 하지만 content가 비어있으면 전체 정보 표시
                
                await handle_info_command(message, channel_id, sub_cmd)
                return
            
            # --- 퀘스트/메모 직접 명령어 ---
            if cmd == 'quest':
                arg = parsed['content'].strip()
                if not arg:
                    # 퀘스트 목록 표시
                    await send_long_message(
                        message.channel,
                        quest_manager.get_active_quests_text(channel_id)
                    )
                else:
                    # 퀘스트 추가
                    result = quest_manager.add_quest(channel_id, arg)
                    await message.channel.send(result)
                return
            
            if cmd == 'memo':
                arg = parsed['content'].strip()
                if not arg:
                    # 메모 목록 표시
                    await send_long_message(
                        message.channel,
                        quest_manager.get_memos_text(channel_id)
                    )
                else:
                    # 메모 추가
                    result = quest_manager.add_memo(channel_id, arg)
                    await message.channel.send(result)
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
            
            # --- Doom 예측 ---
            if cmd == 'forecast':
                forecast_msg = world_manager.get_doom_forecast(channel_id)
                await send_long_message(message.channel, forecast_msg)
                return
            
            # --- Doom 수동 조절 ---
            if cmd == 'doom':
                arg = parsed.get('content', '').strip()
                if not arg:
                    # 현재 상태 표시
                    status = world_manager.get_doom_status(channel_id)
                    await message.channel.send(
                        f"📊 **위기 수치:** {status['value']}% ({status['description']})\n"
                        f"{'🚨 위험!' if status['is_danger'] else '✅ 안전'}"
                    )
                    return
                
                try:
                    amount = int(arg)
                    result = world_manager.change_doom(channel_id, amount)
                    await message.channel.send(result)
                    
                    # 이벤트 트리거 확인
                    event = world_manager.trigger_doom_event(channel_id)
                    if event:
                        await message.channel.send(event)
                except ValueError:
                    await message.channel.send("⚠️ 사용법: `!둠 [+/-숫자]` 또는 `!둠` (현재 상태)")
                return
        
        # =========================================================
        # 주사위 처리
        # =========================================================
        if parsed['type'] == 'dice':
            await message.channel.send(parsed['content'])
            domain_manager.append_history(channel_id, "System", f"Dice: {parsed['content']}")
            return
        
        # =========================================================
        # OOC (자연어 메모리 수정) 처리
        # =========================================================
        if parsed['type'] == 'ooc':
            ooc_content = parsed['content']
            uid = str(message.author.id)
            
            # 현재 AI 메모리 가져오기
            ai_mem = domain_manager.get_ai_memory(channel_id, uid)
            if not ai_mem:
                await message.channel.send("❌ 먼저 `!가면`으로 캐릭터를 등록하세요.")
                return
            
            if not client_genai:
                await message.channel.send("⚠️ AI가 비활성화되어 OOC 수정이 불가능합니다.")
                return
            
            wait_msg = await message.channel.send("⏳ **[OOC]** 요청 처리 중...")
            
            # 현재 참가자 데이터 가져오기
            p_data = domain_manager.get_participant_data(channel_id, uid)
            
            # AI에게 수정 요청 파싱
            edit_result = await memory_system.process_ooc_memory_edit(
                client_genai, MODEL_ID, ooc_content, ai_mem, p_data
            )
            
            if edit_result and edit_result.get("edits"):
                # 수정 적용 (AI 메모리 + 참가자 데이터)
                updated_mem, updated_participant = memory_system.apply_memory_edits(
                    ai_mem, edit_result["edits"], p_data
                )
                domain_manager.update_ai_memory(channel_id, uid, updated_mem)
                
                # 참가자 데이터 업데이트 (인벤토리, 골드, 상태이상)
                if updated_participant:
                    if "economy" in updated_participant:
                        p_data["economy"] = updated_participant["economy"]
                    if "inventory" in updated_participant:
                        p_data["inventory"] = updated_participant["inventory"]
                    if "status_effects" in updated_participant:
                        p_data["status_effects"] = updated_participant["status_effects"]
                    domain_manager.save_participant_data(channel_id, uid, p_data)
                
                confirm_msg = edit_result.get("confirmation_message", "✅ 수정 완료!")
                interpretation = edit_result.get("interpretation", "")
                
                # 수정된 필드 목록 생성
                edited_fields = list(set(e.get("field", "").split(".")[0] for e in edit_result["edits"]))
                field_emoji = {
                    "relationships": "💞", "passives": "🏆", "known_info": "💡",
                    "foreshadowing": "🔮", "normalization": "🌓", "appearance": "👁️",
                    "personality": "💭", "background": "📖", "notes": "📋",
                    "inventory": "🎒", "economy": "💰", "status_effects": "💫"
                }
                fields_str = " ".join([field_emoji.get(f, "📝") for f in edited_fields])
                
                await safe_delete_message(wait_msg)
                await message.channel.send(
                    f"✅ **[OOC 수정 완료]** {fields_str}\n"
                    f"_{interpretation}_\n\n"
                    f"{confirm_msg}\n\n"
                    f"💡 `!정보`로 변경사항을 확인하세요."
                )
            else:
                # 실패 시 더 친절한 안내
                interpretation = edit_result.get("interpretation", "") if edit_result else ""
                await safe_delete_message(wait_msg)
                await message.channel.send(
                    f"❌ **[OOC]** 요청을 이해하지 못했습니다.\n"
                    f"{f'_({interpretation})_' if interpretation else ''}\n\n"
                    f"**사용법:** `(OOC: 요청 내용)`\n\n"
                    f"**예시:**\n"
                    f"• `(OOC: 리엘이랑 친해진 걸로)` → 관계 수정\n"
                    f"• `(OOC: 골드 500 줘)` → 💰 경제 수정\n"
                    f"• `(OOC: 마법검 얻었어)` → 🎒 인벤토리 추가\n"
                    f"• `(OOC: 중독 상태야)` → 💫 상태이상 추가\n"
                    f"• `(OOC: 피로 풀렸어)` → 상태이상 제거"
                )
            return
        
        # =========================================================
        # OOC + 행동/대사 함께 처리 (chat_with_ooc)
        # 예: "우린 친구잖아 (OOC: 철수는 나를 동료라 생각한다)"
        # → OOC 먼저 적용 후 AI 응답 생성
        # =========================================================
        if parsed['type'] == 'chat_with_ooc':
            ooc_content = parsed.get('ooc_content', '')
            chat_content = parsed.get('chat_content', '')
            uid = str(message.author.id)
            
            # 1단계: OOC 수정 먼저 적용
            ai_mem = domain_manager.get_ai_memory(channel_id, uid)
            p_data = domain_manager.get_participant_data(channel_id, uid)
            ooc_applied = False
            
            if ai_mem and client_genai and ooc_content:
                try:
                    edit_result = await memory_system.process_ooc_memory_edit(
                        client_genai, MODEL_ID, ooc_content, ai_mem, p_data
                    )
                    
                    if edit_result and edit_result.get("edits"):
                        updated_mem, updated_participant = memory_system.apply_memory_edits(
                            ai_mem, edit_result["edits"], p_data
                        )
                        domain_manager.update_ai_memory(channel_id, uid, updated_mem)
                        
                        # 참가자 데이터 업데이트
                        if updated_participant:
                            if "economy" in updated_participant:
                                p_data["economy"] = updated_participant["economy"]
                            if "inventory" in updated_participant:
                                p_data["inventory"] = updated_participant["inventory"]
                            if "status_effects" in updated_participant:
                                p_data["status_effects"] = updated_participant["status_effects"]
                            domain_manager.save_participant_data(channel_id, uid, p_data)
                        
                        ooc_applied = True
                        
                        # 간단한 OOC 적용 알림
                        edited_fields = list(set(e.get("field", "").split(".")[0] for e in edit_result["edits"]))
                        field_emoji = {
                            "relationships": "💞", "passives": "🏆", "known_info": "💡",
                            "foreshadowing": "🔮", "normalization": "🌓", "appearance": "👁️",
                            "personality": "💭", "background": "📖", "notes": "📋",
                            "inventory": "🎒", "economy": "💰", "status_effects": "💫"
                        }
                        fields_str = " ".join([field_emoji.get(f, "📝") for f in edited_fields])
                        await message.channel.send(f"✅ **[OOC 적용]** {fields_str}")
                except Exception as e:
                    logging.warning(f"OOC 적용 실패: {e}")
            
            # 2단계: 행동/대사는 일반 chat으로 처리 계속 진행
            # parsed를 chat 타입으로 변환하여 아래 AI 응답 생성으로 넘김
            parsed = {
                'type': 'chat',
                'content': chat_content,
                'style': parsed.get('style', {})
            }
            # return 하지 않고 아래 AI 응답 생성으로 계속 진행
        
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
            
            # 대기 모드에서는 기록만 하고 AI 응답 생성 안 함
            response_mode = domain_manager.get_response_mode(channel_id)
            if response_mode == 'waiting' and not system_trigger:
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
            
            # 플레이어 컨텍스트 수집 (패시브 중복 방지용)
            uid = str(message.author.id)
            p_data = domain_manager.get_participant_data(channel_id, uid)
            player_context = ""
            if p_data:
                player_context = simulation_manager.get_passives_for_context(p_data)
            
            # AI 분석 (좌뇌)
            nvc_res = {}
            if client_genai:
                nvc_res = await memory_system.analyze_context_nvc(
                    client_genai, MODEL_ID, hist_text, lore_txt, rule_txt, quest_txt,
                    player_context=player_context
                )
                
                if nvc_res.get("CurrentLocation"):
                    domain_manager.set_current_location(channel_id, nvc_res["CurrentLocation"])
                if nvc_res.get("LocationRisk"):
                    domain_manager.set_current_risk(channel_id, nvc_res["LocationRisk"])
            
            # 시스템 액션 처리
            sys_action = nvc_res.get("SystemAction", {})
            auto_msg = await process_ai_system_action(message, channel_id, sys_action)
            
            # === AI 메모리 자동 갱신 (하이브리드 시스템) ===
            # 좌뇌 분석 결과에서 PlayerMemoryUpdate, SessionMemoryUpdate 추출하여 적용
            memory_msgs = memory_system.apply_ai_memory_updates(
                channel_id, uid, nvc_res, domain_manager
            )
            
            # AI 메모리 컨텍스트 생성 (우뇌에게 전달)
            ai_memory_ctx = domain_manager.get_full_ai_context(channel_id, uid)
            
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
            
            # NPC 태도 컨텍스트 생성
            npc_attitudes = nvc_res.get("NPCAttitudes", {})
            npc_attitude_ctx = ""
            if npc_attitudes:
                npc_attitude_ctx = "### [NPC ATTITUDES]\n"
                for npc_name, attitude_data in npc_attitudes.items():
                    if isinstance(attitude_data, dict):
                        att = attitude_data.get("attitude", "neutral")
                        reason = attitude_data.get("reason", "")
                        # 태도별 말투 힌트 추가
                        speech_hints = {
                            "hostile": "위협적, 조롱, 정보 숨김",
                            "unfriendly": "퉁명스럽고 짧음, 비협조",
                            "neutral": "정중하고 사무적",
                            "friendly": "따뜻하고 친근, 정보 제공",
                            "devoted": "존경/애정, 비밀 공유 가능"
                        }
                        hint = speech_hints.get(att, "")
                        npc_attitude_ctx += f"- **{npc_name}**: {att} ({reason}) → 말투: {hint}\n"
                npc_attitude_ctx += "\n"
            
            # NPC간 대화 컨텍스트 생성
            npc_interaction = nvc_res.get("NPCInteraction")
            npc_interaction_ctx = ""
            if npc_interaction and isinstance(npc_interaction, dict):
                participants = npc_interaction.get("participants", [])
                interaction_type = npc_interaction.get("type", "")
                topic = npc_interaction.get("topic", "")
                mood = npc_interaction.get("mood", "")
                if participants and len(participants) >= 2:
                    npc_interaction_ctx = (
                        f"### [NPC INTERACTION OPPORTUNITY]\n"
                        f"NPCs present: {', '.join(participants)}\n"
                        f"Type: {interaction_type} | Mood: {mood}\n"
                        f"Suggested topic: {topic}\n"
                        f"**Instruction:** Include ambient dialogue between these NPCs "
                        f"that players can overhear. This adds atmosphere and may reveal information.\n\n"
                    )
            
            # AI 응답 생성 (우뇌) - 프리셋 순서 적용
            
            # === [5] FERMENTED 메모리 컨텍스트 ===
            fermented_ctx = ""
            try:
                fermented_ctx = fermentation.build_fermented_context(domain)
            except Exception as fme:
                logging.warning(f"[Fermentation] Fermented 컨텍스트 빌드 실패: {fme}")
            
            # === [10] Current Context 구성 ===
            current_context_parts = []
            
            # World State
            if world_ctx or obj_ctx:
                current_context_parts.append(f"### World State\n{world_ctx}\n{obj_ctx}")
            
            # Temporal Orientation
            if temporal_ctx:
                current_context_parts.append(temporal_ctx.strip())
            
            # AI Memory Context
            if ai_memory_ctx:
                current_context_parts.append(f"### AI Memory\n{ai_memory_ctx}")
            
            # NPC Attitudes
            if npc_attitude_ctx:
                current_context_parts.append(npc_attitude_ctx.strip())
            
            # NPC Interaction
            if npc_interaction_ctx:
                current_context_parts.append(npc_interaction_ctx.strip())
            
            # Left Hemisphere Analysis (NVC)
            nvc_summary = (
                f"### Left Hemisphere Analysis\n"
                f"Location: {nvc_res.get('CurrentLocation', 'Unknown')} "
                f"(Risk: {nvc_res.get('LocationRisk', 'Low')})\n"
                f"Physical State: {nvc_res.get('PhysicalState', 'N/A')}\n"
                f"Observation: {nvc_res.get('Observation', 'N/A')}\n"
                f"Need: {nvc_res.get('Need', 'N/A')}"
            )
            current_context_parts.append(nvc_summary)
            
            current_context = "\n\n".join(current_context_parts)
            
            # DEEP MEMORY 추출
            deep_memory = domain.get("deep_memory", "")
            
            # 발효 요약 추출
            fermented_summaries = []
            for entry in domain.get("fermented_history", []):
                if entry.get("summary"):
                    fermented_summaries.append(entry["summary"])
            fermented_summary_text = "\n---\n".join(fermented_summaries)
            
            # === 프리셋 순서 기반 full_prompt 구성 ===
            # [5] Fermented (Deep Memory + Episode Summary)
            # [8] Scripts (장르/톤 기반 동적 생성 - 세션에서 처리)
            # [10] Current Context
            # [11] User Message
            # [12] Output Generation Request
            
            full_prompt = ""
            
            # Fermented Context (참조용)
            if fermented_ctx:
                full_prompt += f"{fermented_ctx}\n\n"
            
            # Current Context
            full_prompt += f"""<Current-Context>
{current_context}
</Current-Context>

"""
            
            # User Message (Material)
            full_prompt += f"""<User_Message>
### Material (플레이어 입력)
<material>
{action_text}
</material>
</User_Message>

"""
            
            # Output Generation Request (Directive)
            full_prompt += """### [OUTPUT DIRECTIVE]
Process <material> as the player's attempt.
Players are identified by [Name]: prefix (e.g., [잭]:, [리사]:).
Generate NPC reactions and world response ONLY.
**Apply NPC attitudes to their speech and behavior.**
**If NPC Interaction is suggested, include their ambient dialogue.**
**CRITICAL: Reference the FERMENTED/DEEP MEMORY above for story continuity.**
Do NOT generate ANY player's dialogue, thoughts, or decisions.
Track each player separately. 3rd person narration. Korean output."""
            
            response = "⚠️ AI Error"
            if client_genai:
                loading = await message.channel.send(
                    f"⏳ **[Lorekeeper]** 집필 중..."
                )
                
                # 캐싱 세션 생성 시도 (프리셋 순서 적용)
                try:
                    session, used_cache = await persona.create_cached_session(
                        client_genai, MODEL_ID, channel_id,
                        lore_txt, rule_txt,
                        active_genres, custom_tone, deep_memory,
                        fermentation_module=fermentation
                    )
                    if used_cache:
                        logging.info(f"[Session] 캐싱 세션 사용 - {channel_id}")
                except Exception as cache_err:
                    logging.warning(f"[Session] 캐싱 실패, 일반 세션 사용: {cache_err}")
                    session = persona.create_risu_style_session(
                        client_genai, MODEL_ID, lore_txt, rule_txt, 
                        active_genres, custom_tone, deep_memory,
                        fermented_summary=fermented_summary_text
                    )
                
                # 히스토리 추가
                for h in domain.get('history', []):
                    role = "user" if h['role'] == "User" else "model"
                    session.history.append(
                        types.Content(role=role, parts=[types.Part(text=h['content'])])
                    )
                
                # 응답 생성
                response = await persona.generate_response_with_retry(
                    client_genai, session, full_prompt
                )
                
                await safe_delete_message(loading)
                
                # === 우뇌 응답에서 SYSTEM_UPDATE 파싱 ===
                if response:
                    # system_update 블록 추출
                    import re
                    system_update_match = re.search(
                        r'```system_update\s*\n?\s*(\{.*?\})\s*\n?```',
                        response, 
                        re.DOTALL
                    )
                    
                    if system_update_match:
                        try:
                            update_json = json.loads(system_update_match.group(1))
                            
                            # 참가자 데이터 및 AI 메모리 가져오기
                            p_data = domain_manager.get_participant_data(channel_id, uid)
                            ai_mem = domain_manager.get_ai_memory(channel_id, uid)
                            
                            if p_data and ai_mem:
                                update_msgs = []
                                p_updated = False
                                mem_updated = False
                                
                                # ========== 참가자 데이터 (p_data) ==========
                                
                                # 인벤토리 추가
                                if update_json.get("inventory_add"):
                                    if "inventory" not in p_data:
                                        p_data["inventory"] = {}
                                    for item, amount in update_json["inventory_add"].items():
                                        p_data["inventory"][item] = p_data["inventory"].get(item, 0) + int(amount)
                                        update_msgs.append(f"🎒 **+{item}**")
                                    p_updated = True
                                
                                # 인벤토리 제거
                                if update_json.get("inventory_remove"):
                                    if "inventory" not in p_data:
                                        p_data["inventory"] = {}
                                    for item, amount in update_json["inventory_remove"].items():
                                        if item in p_data["inventory"]:
                                            p_data["inventory"][item] = max(0, p_data["inventory"][item] - int(amount))
                                            if p_data["inventory"][item] <= 0:
                                                del p_data["inventory"][item]
                                            update_msgs.append(f"🎒 **-{item}**")
                                    p_updated = True
                                
                                # 골드 변경
                                if update_json.get("gold_change") is not None:
                                    if "economy" not in p_data:
                                        p_data["economy"] = {"gold": 0}
                                    change = int(update_json["gold_change"])
                                    p_data["economy"]["gold"] = max(0, p_data["economy"].get("gold", 0) + change)
                                    if change > 0:
                                        update_msgs.append(f"💰 **+{change}**")
                                    elif change < 0:
                                        update_msgs.append(f"💰 **{change}**")
                                    p_updated = True
                                
                                # 상태이상 추가
                                if update_json.get("status_add"):
                                    if "status_effects" not in p_data:
                                        p_data["status_effects"] = []
                                    for status in update_json["status_add"]:
                                        if status not in p_data["status_effects"]:
                                            p_data["status_effects"].append(status)
                                            update_msgs.append(f"💫 **{status}**")
                                    p_updated = True
                                
                                # 상태이상 제거
                                if update_json.get("status_remove"):
                                    if "status_effects" not in p_data:
                                        p_data["status_effects"] = []
                                    for status in update_json["status_remove"]:
                                        if status in p_data["status_effects"]:
                                            p_data["status_effects"].remove(status)
                                            update_msgs.append(f"✨ **{status} 해제**")
                                    p_updated = True
                                
                                # ========== AI 메모리 (ai_mem) ==========
                                
                                # 관계 업데이트
                                if update_json.get("relationship_update"):
                                    if "relationships" not in ai_mem:
                                        ai_mem["relationships"] = {}
                                    for npc, desc in update_json["relationship_update"].items():
                                        ai_mem["relationships"][npc] = desc
                                        update_msgs.append(f"💞 **{npc}**")
                                    mem_updated = True
                                
                                # 패시브 추가
                                if update_json.get("passive_add"):
                                    if "passives" not in ai_mem:
                                        ai_mem["passives"] = []
                                    for passive in update_json["passive_add"]:
                                        if passive not in ai_mem["passives"]:
                                            ai_mem["passives"].append(passive)
                                            update_msgs.append(f"🏆 **{passive}**")
                                    mem_updated = True
                                
                                # 알고있는 정보 추가
                                if update_json.get("info_add"):
                                    if "known_info" not in ai_mem:
                                        ai_mem["known_info"] = []
                                    for info in update_json["info_add"]:
                                        if info not in ai_mem["known_info"]:
                                            ai_mem["known_info"].append(info)
                                            update_msgs.append(f"💡 **정보**")
                                    mem_updated = True
                                
                                # 복선 추가
                                if update_json.get("foreshadow_add"):
                                    if "foreshadowing" not in ai_mem:
                                        ai_mem["foreshadowing"] = []
                                    for fs in update_json["foreshadow_add"]:
                                        if fs not in ai_mem["foreshadowing"]:
                                            ai_mem["foreshadowing"].append(fs)
                                            update_msgs.append(f"🔮 **복선**")
                                    mem_updated = True
                                
                                # 적응도 업데이트
                                if update_json.get("adaptation_update"):
                                    if "normalization" not in ai_mem:
                                        ai_mem["normalization"] = {}
                                    for element, status in update_json["adaptation_update"].items():
                                        ai_mem["normalization"][element] = status
                                        update_msgs.append(f"🌓 **{element}**")
                                    mem_updated = True
                                
                                # 외형 업데이트
                                if update_json.get("appearance_update"):
                                    current = ai_mem.get("appearance", "")
                                    new_appearance = update_json["appearance_update"]
                                    if current:
                                        ai_mem["appearance"] = f"{current} {new_appearance}"
                                    else:
                                        ai_mem["appearance"] = new_appearance
                                    update_msgs.append(f"👁️ **외형 변화**")
                                    mem_updated = True
                                
                                # 성격 업데이트
                                if update_json.get("personality_update"):
                                    current = ai_mem.get("personality", "")
                                    new_personality = update_json["personality_update"]
                                    if current:
                                        ai_mem["personality"] = f"{current} {new_personality}"
                                    else:
                                        ai_mem["personality"] = new_personality
                                    update_msgs.append(f"💭 **성격 변화**")
                                    mem_updated = True
                                
                                # 배경 업데이트
                                if update_json.get("background_update"):
                                    current = ai_mem.get("background", "")
                                    new_background = update_json["background_update"]
                                    if current:
                                        ai_mem["background"] = f"{current} {new_background}"
                                    else:
                                        ai_mem["background"] = new_background
                                    update_msgs.append(f"📖 **배경 추가**")
                                    mem_updated = True
                                
                                # 저장
                                if p_updated:
                                    domain_manager.save_participant_data(channel_id, uid, p_data)
                                if mem_updated:
                                    domain_manager.update_ai_memory(channel_id, uid, ai_mem)
                                
                                # 업데이트 메시지 출력
                                if update_msgs:
                                    await message.channel.send(" | ".join(update_msgs))
                        
                        except json.JSONDecodeError as je:
                            logging.warning(f"[SYSTEM_UPDATE] JSON 파싱 실패: {je}")
                        except Exception as ue:
                            logging.warning(f"[SYSTEM_UPDATE] 업데이트 실패: {ue}")
                        
                        # 응답에서 system_update 블록 제거 (출력에서 숨김)
                        response = re.sub(
                            r'\s*```system_update\s*\n?\s*\{.*?\}\s*\n?```\s*',
                            '',
                            response,
                            flags=re.DOTALL
                        ).strip()
                
                # 응답 길이 로깅
                if response:
                    logging.info(f"[Response] Length: {len(response)}자")
            
            # 결과 전송
            if auto_msg:
                await message.channel.send(f"🤖 {auto_msg}")
            
            # AI 메모리 갱신 메시지 출력
            if memory_msgs:
                for mem_msg in memory_msgs:
                    await message.channel.send(mem_msg)
            
            if response:
                await send_long_message(message.channel, response)
                domain_manager.append_history(channel_id, "User", action_text)
                domain_manager.append_history(channel_id, "Char", response)
                
                # === 자동 발효 시스템 (장기 기억 관리) ===
                try:
                    session_data = domain_manager.get_domain(channel_id)
                    fermentation.ensure_memory_fields(session_data)
                    
                    # 발효 필요 여부 체크 및 실행
                    if fermentation.should_ferment_fresh(session_data):
                        logging.info(f"[Fermentation] 자동 발효 시작 - {channel_id}")
                        await fermentation.auto_ferment(
                            client, MODEL_ID, session_data,
                            save_callback=lambda: domain_manager.save_domain(channel_id, session_data)
                        )
                except Exception as fe:
                    logging.warning(f"[Fermentation] 자동 발효 실패 (무시됨): {fe}")
    
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
