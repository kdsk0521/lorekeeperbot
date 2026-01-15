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
from typing import Optional, Tuple, List, Dict
from dotenv import load_dotenv
from google import genai
from google.genai import types
from collections import defaultdict, deque
from time import time

# =========================================================
# 상수 정의
# =========================================================
MAX_DISCORD_MESSAGE_LENGTH = 2000
MAX_FILE_SIZE_MB = 10  # 최대 파일 크기 (MB)
MAX_FILE_SIZE_BYTES = MAX_FILE_SIZE_MB * 1024 * 1024
MAX_TEXT_INPUT_LENGTH = 50000  # 최대 텍스트 입력 길이
SUPPORTED_TEXT_EXTENSIONS = ['.txt', '.md', '.json', '.log', '.py', '.yaml', '.yml']
NPC_PREVIEW_LIMIT = 5  # 일괄 추가 시 미리보기 NPC 개수
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
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.FileHandler("bot_runtime.log", encoding='utf-8'),
        logging.StreamHandler()
    ]
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
# Per-channel locks to prevent race conditions
# =========================================================
channel_locks: Dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)

# =========================================================
# Discord Rate Limiting Protection
# =========================================================
class RateLimiter:
    """Discord API rate limiting 방지를 위한 간단한 rate limiter"""

    def __init__(self, max_messages: int = 5, time_window: float = 5.0):
        self.max_messages = max_messages
        self.time_window = time_window
        self.message_times: Dict[str, deque] = defaultdict(lambda: deque(maxlen=max_messages))

    async def wait_if_needed(self, channel_id: str) -> None:
        """필요 시 대기하여 rate limit을 준수합니다."""
        now = time()
        times = self.message_times[channel_id]

        # 오래된 타임스탬프 제거
        while times and now - times[0] > self.time_window:
            times.popleft()

        # Rate limit 초과 시 대기
        if len(times) >= self.max_messages:
            oldest = times[0]
            wait_time = self.time_window - (now - oldest)
            if wait_time > 0:
                logging.debug(f"Rate limit 대기: {wait_time:.2f}초")
                await asyncio.sleep(wait_time)

        # 현재 메시지 타임스탬프 기록
        self.message_times[channel_id].append(time())

rate_limiter = RateLimiter()


# =========================================================
# 유틸리티 함수
# =========================================================
async def send_long_message(channel, text: str) -> None:
    """2000자가 넘는 메시지를 나누어 전송하는 함수"""
    if not text:
        return

    channel_id = str(channel.id)

    if len(text) <= MAX_DISCORD_MESSAGE_LENGTH:
        await rate_limiter.wait_if_needed(channel_id)
        await channel.send(text)
        return

    # 메시지 분할 전송
    for i in range(0, len(text), MAX_DISCORD_MESSAGE_LENGTH):
        chunk = text[i:i + MAX_DISCORD_MESSAGE_LENGTH]
        await rate_limiter.wait_if_needed(channel_id)
        await channel.send(chunk)


async def read_attachment_text(attachment) -> Tuple[Optional[str], Optional[str]]:
    """
    첨부파일에서 텍스트를 읽어옵니다.
    
    Returns:
        Tuple[Optional[str], Optional[str]]: (텍스트 내용, 에러 메시지)
    """
    filename_lower = attachment.filename.lower()

    # 파일 크기 확인
    if attachment.size > MAX_FILE_SIZE_BYTES:
        return None, f"⚠️ 파일이 너무 큽니다. 최대 크기: {MAX_FILE_SIZE_MB}MB"

    # 지원되는 확장자인지 확인
    if not any(filename_lower.endswith(ext) for ext in SUPPORTED_TEXT_EXTENSIONS):
        return None, f"⚠️ **지원하지 않는 파일입니다.**\n지원 확장자: {', '.join(SUPPORTED_TEXT_EXTENSIONS)}"

    try:
        data = await attachment.read()

        # 여러 인코딩 시도 (한국어 파일 지원)
        encodings = ['utf-8', 'cp949', 'euc-kr', 'utf-8-sig']
        text = None
        last_error = None

        for encoding in encodings:
            try:
                text = data.decode(encoding)
                logging.info(f"파일 '{attachment.filename}' 인코딩: {encoding}")
                break
            except (UnicodeDecodeError, LookupError) as e:
                last_error = e
                continue

        if text is None:
            return None, f"⚠️ 파일 `{attachment.filename}` 읽기 실패: 지원하지 않는 인코딩입니다."

        # 텍스트 길이 검증
        if len(text) > MAX_TEXT_INPUT_LENGTH:
            return None, f"⚠️ 파일 내용이 너무 깁니다. 최대 {MAX_TEXT_INPUT_LENGTH:,}자까지 지원합니다."

        return text, None
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
        raw_lore = domain_manager.get_lore(channel_id)
        original_lore = domain_manager.get_lore_original(channel_id)
        npcs = domain_manager.get_npcs(channel_id)
        
        if raw_lore == domain_manager.DEFAULT_LORE or not raw_lore.strip():
            await message.channel.send(
                "📜 저장된 로어가 없습니다. `!로어 [내용]` 또는 텍스트 파일을 업로드하세요."
            )
            return
        
        # 장르 및 톤 정보
        genres = domain_manager.get_active_genres(channel_id)
        custom_tone = domain_manager.get_custom_tone(channel_id)
        
        info_msg = f"📜 **로어 정보**\n\n"
        
        if original_lore:
            info_msg += f"**📚 원본 (NPC 포함):** {len(original_lore):,}자\n"
        info_msg += f"**📖 정리된 로어 (NPC 제외):** {len(raw_lore):,}자\n"
        info_msg += f"**👥 추출된 NPC:** {len(npcs)}명\n"
        
        info_msg += f"\n**🎭 장르:** {', '.join(genres) if genres else '미분석'}\n"
        
        if custom_tone:
            info_msg += f"**🎨 톤:** {custom_tone}\n"
        
        await message.channel.send(info_msg)
        
        # NPC 목록 미리보기 (최대 5명)
        if npcs:
            npc_preview = []
            for _, (name, data) in enumerate(list(npcs.items())[:5]):
                desc = data.get('desc', '설명 없음')
                short_desc = desc[:50] + "..." if len(desc) > 50 else desc
                npc_preview.append(f"• **{name}**: {short_desc}")
            
            npc_msg = "👥 **NPC 목록 (미리보기):**\n" + "\n".join(npc_preview)
            if len(npcs) > 5:
                npc_msg += f"\n_... 외 {len(npcs) - 5}명 (`!npc`로 전체 확인)_"
            await message.channel.send(npc_msg)
        
        # 로어 미리보기
        preview = raw_lore[:500] + "..." if len(raw_lore) > 500 else raw_lore
        await message.channel.send(f"📄 **정리된 로어 미리보기:**\n```\n{preview}\n```")
        
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
    
    # 원본 로어 저장 (NPC 포함)
    domain_manager.save_lore_original(channel_id, full)
    
    # 로어 크기 확인
    raw_lore = full
    lore_length = len(raw_lore)
    
    action_word = "추가됨" if is_append else "저장됨"
    
    status_msg = await message.channel.send(
        f"📜 **로어 {action_word}** ({lore_length:,}자)\n"
        f"🔄 **AI 재분석 중...** (NPC 분리, 장르, 규칙)"
    )
    
    # AI 분석
    if client_genai:
        try:
            # NPC 추출 및 로어 분리
            await status_msg.edit(content="⏳ **[AI]** NPC 추출 및 로어 분리 중...")
            npcs_extracted, cleaned_lore = await memory_system.extract_npcs_with_segments(
                client_genai, MODEL_ID, raw_lore
            )
            
            # NPC 추가
            for n in npcs_extracted:
                character_sheet.npc_memory.add_npc(channel_id, n.get("name"), n.get("description"))
            
            # 정리된 로어 저장 (NPC 제거됨)
            domain_manager.append_lore(channel_id, cleaned_lore)
            
            # 장르 분석 (정리된 로어 기반)
            await status_msg.edit(content="⏳ **[AI]** 장르 분석 중...")
            
            res = await memory_system.analyze_genre_from_lore(client_genai, MODEL_ID, cleaned_lore)
            domain_manager.set_active_genres(channel_id, res.get("genres", ["noir"]))
            domain_manager.set_custom_tone(channel_id, res.get("custom_tone"))
            
            rules = await memory_system.analyze_location_rules_from_lore(client_genai, MODEL_ID, cleaned_lore)
            if rules:
                domain_manager.set_location_rules(channel_id, rules)
            
            # 최종 메시지
            final_msg = f"✅ **[분석 완료]**\n**장르:** {res.get('genres')}\n**NPC 추출:** {len(npcs_extracted)}명"
            
            await status_msg.edit(content=final_msg)
            
        except Exception as e:
            logging.error(f"Lore Analysis Error: {e}")
            await status_msg.edit(content=f"⚠️ **분석 중 오류 발생:** {e}")
    else:
        # AI 없으면 그냥 원본 로어 저장
        domain_manager.append_lore(channel_id, full)
        await status_msg.edit(content="📜 저장 완료 (⚠️ API 키 없음: AI 분석 건너뜀)")


async def handle_rule_command(message, channel_id: str, arg: str) -> None:
    """룰 명령어를 처리합니다."""
    # 성장 시스템 표시 문자열 상수
    growth_display = {
        "default": "🎭 기본 (패시브/칭호 자동 부여)",
        "custom": "🎭 커스텀 (룰에 따름)"
    }
    
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
            await message.channel.send(
                "📘 **룰 초기화** - 기본 룰로 복귀했습니다.\n"
                f"{growth_display['default']}으로 복귀"
            )
            return
        
        # 파일 업로드: 완전 커스텀 모드
        if file_text:
            domain_manager.set_custom_rules_from_file(channel_id, file_text)
            await message.channel.send(
                "📘 **완전 커스텀 룰 설정됨**\n"
                "기본 룰이 파일 내용으로 대체되었습니다.\n"
                f"**성장 시스템도 커스텀으로 변경됨** - AI가 룰에 정의된 성장 규칙을 따릅니다.\n"
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
    growth_system = domain_manager.get_growth_system(channel_id)
    
    mode_display = {
        "default": "📗 기본 룰",
        "hybrid": "📘 기본 룰 + 커스텀",
        "custom": "📙 완전 커스텀"
    }
    
    await send_long_message(
        message.channel,
        f"**[{mode_display.get(rules_mode, '📘')}]**\n"
        f"**[{growth_display.get(growth_system, growth_display['default'])}]**\n\n"
        f"{domain_manager.get_rules(channel_id)}"
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
        ch, msg_text = quest_manager.export_chronicles_incremental(channel_id, mode)
        
        if not ch:
            await message.channel.send(msg_text)
            return
        
        # 로어도 함께 포함
        lore = domain_manager.get_lore_with_npcs(channel_id)
        content = f"=== LORE ===\n{lore}\n\n{ch}" if lore else ch
        
        with io.BytesIO(content.encode('utf-8')) as f:
            await message.channel.send(msg_text, file=discord.File(f, filename="chronicles.txt"))
        return
    
    # 연대기 조회 (기본)
    lore_book = quest_manager.get_lore_book(channel_id)
    await send_long_message(message.channel, lore_book)


async def handle_npc_info_command(message, channel_id: str, npc_name: str) -> None:
    """NPC 정보 조회 명령어를 처리합니다."""
    # domain NPCs와 session_mem의 npc_summaries를 모두 확인
    npcs = domain_manager.get_npcs(channel_id)
    session_mem = domain_manager.get_session_ai_memory(channel_id)
    npc_summaries = session_mem.get("npc_summaries", {}) if session_mem else {}
    
    # npc_summaries를 npcs에 병합 (세션 중 감지된 NPC 포함)
    for name, summary in npc_summaries.items():
        if name not in npcs:
            npcs[name] = {"desc": summary, "status": "Active"}
    
    if not npc_name:
        # 전체 NPC 목록
        if not npcs:
            await message.channel.send("⚠️ 등록된 NPC가 없습니다.")
            return
        
        summary_list = []
        for name, data in npcs.items():
            status = data.get('status', 'Active')
            desc = data.get('desc', '')
            short_desc = desc[:50] + "..." if len(desc) > 50 else desc
            summary_list.append(f"• **{name}** ({status}): {short_desc}")
        
        await send_long_message(message.channel, f"👥 **NPC 목록**\n" + "\n".join(summary_list))
        return
    
    # 특정 NPC 조회
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
        
        # 동행자 (known_info에서 "동행자:" 접두사 가진 항목 추출)
        known_info = ai_mem.get('known_info', [])
        companions = [info for info in known_info if info.startswith("동행자:")]
        if companions:
            result += "🐾 **동행자:**\n"
            for comp in companions:
                # "동행자: 이름 - 설명" 형태에서 추출
                comp_desc = comp.replace("동행자:", "").strip()
                result += f"  • {comp_desc}\n"
        
        # 소지품 (화폐 + 인벤토리 통합)
        economy = p.get('economy', {})
        inventory = p.get('inventory', {})
        status_effects = p.get('status_effects', [])
        
        # 화폐 표시 (세계관에 따라 다를 수 있음, 기본은 골드)
        gold = economy.get('gold', 0)
        currency_name = economy.get('currency_name', '골드')
        
        result += f"🎒 **소지품**\n"
        result += f"  💰 {currency_name}: {gold}\n"
        
        if inventory:
            for item, count in inventory.items():
                result += f"  • {item} x{count}\n"
        else:
            result += "  _(인벤토리 비어있음)_\n"
        
        if status_effects:
            result += f"\n💫 **상태이상:** {', '.join(status_effects)}\n"
        
        result += "\n"
    
    # =========================================================
    # 관계 섹션: NPC 관계도 (모든 출처의 NPC 통합)
    # =========================================================
    if sub_type in ['all', 'relation']:
        result += "**━━━ 💞 관계 ━━━**\n"
        
        relationships = ai_mem.get('relationships', {})
        
        # 모든 출처의 NPC 수집 (domain NPCs + session npc_summaries)
        all_npcs = domain_manager.get_npcs(channel_id).copy()
        session_mem = domain_manager.get_session_ai_memory(channel_id)
        if session_mem:
            npc_summaries = session_mem.get("npc_summaries", {})
            for name, summary in npc_summaries.items():
                if name not in all_npcs:
                    all_npcs[name] = {"desc": summary, "status": "Active"}
        
        # relationships에 있는 NPC들 먼저 표시
        displayed_npcs = set()
        if relationships:
            for name, rel_desc in relationships.items():
                displayed_npcs.add(name)
                npc_info = all_npcs.get(name, {})
                npc_desc = npc_info.get('desc', '') if npc_info else ''
                short_desc = (npc_desc[:30] + "...") if len(npc_desc) > 30 else npc_desc
                if short_desc:
                    result += f"  • **{name}** ({rel_desc}) - _{short_desc}_\n"
                else:
                    result += f"  • **{name}:** {rel_desc}\n"
        
        # relationships에 없지만 알려진 NPC들 (관계 미정)
        other_npcs = [n for n in all_npcs.keys() if n not in displayed_npcs]
        if other_npcs:
            if relationships:
                result += "\n👥 **기타 알려진 NPC:**\n"
            for name in other_npcs[:10]:  # 최대 10명까지만
                npc_info = all_npcs.get(name, {})
                npc_desc = npc_info.get('desc', '')
                short_desc = (npc_desc[:30] + "...") if len(npc_desc) > 30 else npc_desc
                result += f"  • **{name}** _(관계 미정)_ - {short_desc}\n" if short_desc else f"  • **{name}** _(관계 미정)_\n"
            if len(other_npcs) > 10:
                result += f"  _... 외 {len(other_npcs) - 10}명_\n"
        
        if not relationships and not all_npcs:
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
            world_summary = session_mem.get('world_summary', '')
            if world_summary:
                result += f"\n🌏 **세계 상황:** {world_summary}\n"
            
            current_arc = session_mem.get('current_arc', '')
            if current_arc:
                result += f"🎬 **현재 스토리:** {current_arc}\n"
            
            active_threads = session_mem.get('active_threads', [])
            if active_threads:
                result += f"🧵 **진행 중인 이야기:** {', '.join(active_threads[:3])}\n"
            
            world_changes = session_mem.get('world_changes', [])
            if world_changes:
                result += "🌐 **세계 변화:**\n"
                for change in world_changes[:3]:
                    result += f"  • {change}\n"
            
            npc_summaries = session_mem.get('npc_summaries', {})
            if npc_summaries:
                result += "👥 **주요 NPC:**\n"
                for name, summary in list(npc_summaries.items())[:5]:
                    result += f"  • **{name}:** {summary}\n"
        
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
        logging.info(f"[Achievement] {content}")
    
    return auto_msg


# =========================================================
# Discord 이벤트 핸들러
# =========================================================
@client_discord.event
async def on_ready():
    """봇 준비 완료 시 실행"""
    domain_manager.initialize_folders()
    logging.info(f"로그인 성공: {client_discord.user}")
    print(f"--- Lorekeeper V{VERSION} Online ({client_discord.user}) ---")
    print(f"Model: {MODEL_ID}")


@client_discord.event
async def on_message(message):
    """메시지 수신 시 실행"""
    # 봇 자신의 메시지 또는 빈 메시지 무시
    if message.author == client_discord.user or not message.content:
        return

    channel_id = str(message.channel.id)

    # Per-channel lock으로 race condition 방지
    async with channel_locks[channel_id]:
        await _process_message(message, channel_id)


async def _process_message(message, channel_id: str):
    """메시지 처리 로직 (lock 내부에서 실행)"""
    try:

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
                return
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
                    "`!정보` / `!내정보` - 캐릭터 정보 조회\n"
                    "  ↳ `!정보 캐릭터` `관계` `패시브` `세계`\n\n"

                    "**━━━ 📜 세션 관리 ━━━**\n"
                    "`!준비` - 세션 준비 상태 확인\n"
                    "`!시작` - 세션 시작 및 첫 장면 생성\n"
                    "`!진행` - 기록된 행동 종합 후 다음 장면\n"
                    "`!리셋` / `!초기화` - 세션 초기화\n"
                    "`!모드 자동` - 자동 모드 (매 채팅마다 AI 응답)\n"
                    "`!모드 대기` / `!모드 수동` - 대기 모드 (기록만, `!진행`으로 응답)\n"
                    "`!잠금` - 세션 잠금 (참가자만 접근 가능)\n"
                    "`!잠금해제` - 세션 잠금 해제\n\n"

                    "**━━━ ⚔️ 참가자 관리 ━━━**\n"
                    "`!잠수` / `!afk` - 자리 비움 상태로 전환\n"
                    "`!이탈` / `!퇴장` - 세션에서 이탈\n"
                    "`!복귀` / `!컴백` - 세션으로 복귀\n\n"

                    "**━━━ 🌍 세계관 ━━━**\n"
                    "`!로어 [파일]` - 세계관 설정 (파일 업로드 또는 텍스트)\n"
                    "  └ NPC 자동 추출, 장르 분석 포함\n"
                    "`!룰 [내용]` - 룰 추가 (기본룰 자동 적용)\n"
                    "`!룰 초기화` - 기본 룰로 복귀\n"
                    "`!퀘스트 [내용]` - 퀘스트 추가/조회\n"
                    "`!메모 [내용]` - 메모 추가/조회\n"
                    "`!연대기` - 연대기 조회\n"
                    "`!연대기 생성` - AI가 스토리 자동 요약\n"
                    "`!연대기 추출` - 대화 로그 파일 저장 (증분 지원)\n\n"

                    "**━━━ 👥 NPC 관리 ━━━**\n"
                    "`!npc` - 전체 NPC 목록 조회\n"
                    "`!npc [이름]` - 특정 NPC 정보 조회\n"
                    "`!npc추가 이름:설명` - 수동으로 NPC 추가\n"
                    "`!npc추가 이름` + txt파일 - 파일로 NPC 추가\n"
                    "`!npc추가` + txt파일 - 여러 NPC 일괄 추가\n"
                    "  └ 파일 형식: `이름: 설명` (각 줄)\n\n"

                    "**━━━ 🎲 주사위 & 분석 ━━━**\n"
                    "`!r [주사위]` / `!주사위` / `!굴림` - 주사위 굴림\n"
                    "  └ 예: `!r 1d20`, `!r 2d6+3`, `!r 1d20 유리`\n"
                    "  └ 유리함/불리함 지원 (adv/dis)\n"
                    "`!분석 [질문]` / `!ooc [질문]` - AI OOC 분석\n"
                    "  └ 예: `!분석 이 NPC의 동기는 뭘까?`\n"
                    "`!일관성` - 나레이션 일관성 검사\n"
                    "`!세계규칙` - 세계 규칙 자동 추출\n\n"

                    "**━━━ 💀 위기 시스템 (Doom) ━━━**\n"
                    "`!둠` - 현재 위기 수치 조회 (0-100%)\n"
                    "`!둠 [+/-숫자]` - 위기 수치 조정\n"
                    "  └ 예: `!둠 +10`, `!둠 -5`\n"
                    "`!예측` - Doom 예측 및 경고 표시\n\n"

                    "**━━━ 🎬 장면 유형 ━━━**\n"
                    "AI가 장면을 분석하여 자동으로 묘사 수준을 조절합니다.\n"
                    "• 전투/폭력 → 고어 묘사 활성화\n"
                    "• 로맨스/친밀 → 성인 묘사 활성화\n"
                    "`!장면` - 현재 장면 설정 조회\n"
                    "`!장면 일반` / `!장면 자동` - 자동 감지 모드\n"
                    "`!장면 고어` / `!장면 폭력` - 고어 묘사 활성화\n"
                    "`!장면 성인` / `!장면 19` - NSFW 묘사 활성화\n"
                    "`!장면 전체` - 고어+NSFW 모두 활성화\n\n"

                    "**━━━ ⚡ 비일상 시스템 ━━━**\n"
                    "`!비일상` - 현재 상태 및 카운터 확인\n"
                    "`!비일상 켜기` - 비일상 감지 활성화\n"
                    "`!비일상 끄기` - 비일상 감지 비활성화\n"
                    "  └ 기본: 꺼짐 (로어만 즐길 때)\n"
                    "  └ 시간 경과/장소 이동에 따라 카운터 증가\n"
                    "  └ 조건 충족 시 비일상 이벤트 자동 발생\n\n"

                    "**━━━ ✏️ OOC 수정 ━━━**\n"
                    "`(OOC: 요청 내용)` - 캐릭터 정보 수정\n"
                    "  └ 예: `(OOC: 리엘이랑 친해진 걸로)`\n"
                    "  └ 예: `(OOC: 골드 500 줘)`\n"
                    "  └ 예: `(OOC: 마법검 얻었어)`\n\n"

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
            if cmd in ['next', 'turn']:
                system_trigger = "[System: 기록된 모든 플레이어 행동을 종합하여 다음 장면을 진행하세요. 각 캐릭터의 행동과 침묵 모두 고려하여 서사적으로 진행하세요.]"
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
                await handle_info_command(message, channel_id, sub_cmd)
                return
            
            # --- 퀘스트/메모 직접 명령어 ---
            if cmd == 'quest':
                arg = parsed['content'].strip()
                if not arg:
                    await send_long_message(
                        message.channel,
                        quest_manager.get_active_quests_text(channel_id)
                    )
                else:
                    result = quest_manager.add_quest(channel_id, arg)
                    await message.channel.send(result)
                return
            
            if cmd == 'memo':
                arg = parsed['content'].strip()
                if not arg:
                    await send_long_message(
                        message.channel,
                        quest_manager.get_memos_text(channel_id)
                    )
                else:
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
            
            # --- NPC 추가 ---
            if cmd == 'addnpc':
                content = parsed.get('content', '').strip()
                file_text = ""
                
                # txt 파일 첨부 처리
                if message.attachments:
                    for att in message.attachments:
                        text, error = await read_attachment_text(att)
                        if error:
                            await message.channel.send(error)
                            return
                        if text:
                            file_text = text.strip()
                            break
                
                # 파일도 텍스트도 없으면 도움말
                if not content and not file_text:
                    await message.channel.send(
                        "📝 **NPC 추가**\n"
                        "사용법:\n"
                        "• `!npc추가 이름:설명` - 단일 NPC 추가\n"
                        "• `!npc추가 이름` + txt 파일 첨부 - 단일 NPC에 상세 설명\n"
                        "• `!npc추가` + txt 파일 첨부 - 여러 NPC 일괄 추가\n\n"
                        "**일괄 추가 파일 형식:**\n"
                        "```\n"
                        "리엘: 엘프 궁수, 과묵하고 비밀이 있음\n"
                        "가렌: 용감한 전사, 정의감이 강함\n"
                        "```\n"
                        "또는\n"
                        "```\n"
                        "# 리엘\n"
                        "엘프 궁수, 과묵하고 비밀이 있음\n\n"
                        "# 가렌\n"
                        "용감한 전사, 정의감이 강함\n"
                        "```"
                    )
                    return
                
                # 이름과 설명 분리
                if file_text:
                    # 파일이 있는 경우
                    if content:
                        # 이름이 지정된 경우: 단일 NPC (파일은 설명으로 사용)
                        name = content
                        desc = file_text
                        character_sheet.npc_memory.add_npc(channel_id, name, desc)
                        await message.channel.send(f"✅ NPC 추가됨: **{name}**\n{desc[:100]}{'...' if len(desc) > 100 else ''}")
                    else:
                        # 이름이 없는 경우: 일괄 추가
                        npcs = memory_system.parse_bulk_npcs_from_text(file_text)
                        if not npcs:
                            await message.channel.send("⚠️ 파일에서 NPC를 찾을 수 없습니다. 형식을 확인해주세요.")
                            return
                        
                        # 모든 NPC 추가
                        added_count = 0
                        npc_names = []
                        for npc in npcs:
                            name = npc.get("name", "").strip()
                            desc = npc.get("description", "").strip()
                            if name:
                                character_sheet.npc_memory.add_npc(channel_id, name, desc)
                                added_count += 1
                                npc_names.append(name)
                        
                        if added_count > 0:
                            names_preview = ", ".join(npc_names[:NPC_PREVIEW_LIMIT])
                            if added_count > NPC_PREVIEW_LIMIT:
                                names_preview += f" 외 {added_count - NPC_PREVIEW_LIMIT}명"
                            await message.channel.send(
                                f"✅ **{added_count}명의 NPC 일괄 추가 완료**\n"
                                f"**추가된 NPC:** {names_preview}"
                            )
                        else:
                            await message.channel.send("⚠️ 유효한 NPC를 찾을 수 없습니다.")
                elif ':' in content:
                    name, desc = content.split(':', 1)
                    name = name.strip()
                    desc = desc.strip()
                    character_sheet.npc_memory.add_npc(channel_id, name, desc)
                    await message.channel.send(f"✅ NPC 추가됨: **{name}**\n{desc}")
                else:
                    name = content
                    desc = "설명 없음"
                    character_sheet.npc_memory.add_npc(channel_id, name, desc)
                    await message.channel.send(f"✅ NPC 추가됨: **{name}**\n{desc}")
                return
            
            # --- AI 분석 도구 ---
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
                
                # 컨텍스트 수집 - domain_data 사용
                lore = domain_manager.get_lore_with_npcs(channel_id)
                history = domain_data.get('history', [])[-20:]
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
                
                lore = domain_manager.get_lore_with_npcs(channel_id)
                history = domain_data.get('history', [])[-30:]
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
                
                lore = domain_manager.get_lore_with_npcs(channel_id)
                
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
                    
                    event = world_manager.trigger_doom_event(channel_id)
                    if event:
                        await message.channel.send(event)
                except ValueError:
                    await message.channel.send("⚠️ 사용법: `!둠 [+/-숫자]` 또는 `!둠` (현재 상태)")
                return
            
            # --- 장면 유형 전환 ---
            if cmd == 'scene':
                arg = parsed.get('content', '').strip().lower()
                
                # 현재 상태 조회
                if not arg:
                    current_scene = domain_manager.get_scene_type(channel_id)
                    scene_descriptions = {
                        'normal': '🟢 일반 (자동 감지 활성)',
                        'gore': '🔴 고어 (수동 설정)',
                        'nsfw': '🟣 NSFW (수동 설정)',
                        'gore_nsfw': '⚫ 고어+NSFW (수동 설정)'
                    }
                    desc = scene_descriptions.get(current_scene, scene_descriptions['normal'])
                    
                    await message.channel.send(
                        f"🎬 **현재 장면 설정:** {desc}\n\n"
                        f"_기본적으로 AI가 장면을 분석하여 자동으로 묘사 수준을 조절합니다._\n"
                        f"_수동 전환이 필요한 경우:_\n"
                        f"• `!장면 고어/성인/전체` - 수동 모드\n"
                        f"• `!장면 일반` - 자동 감지로 복귀"
                    )
                    return
                
                # 장면 유형 변경
                scene_mapping = {
                    '일반': 'normal', 'normal': 'normal', '기본': 'normal', '자동': 'normal',
                    '고어': 'gore', 'gore': 'gore', '폭력': 'gore',
                    '성인': 'nsfw', 'nsfw': 'nsfw', '19': 'nsfw',
                    '전체': 'gore_nsfw', 'all': 'gore_nsfw', '고어+nsfw': 'gore_nsfw',
                    '고어+성인': 'gore_nsfw'
                }
                
                new_scene = scene_mapping.get(arg, None)
                if new_scene:
                    domain_manager.set_scene_type(channel_id, new_scene)
                    
                    if new_scene == 'normal':
                        await message.channel.send(
                            f"🟢 **자동 감지 모드로 복귀**\n"
                            f"_AI가 장면을 분석하여 묘사 수준을 자동 조절합니다._"
                        )
                    else:
                        scene_names = {
                            'gore': '고어',
                            'nsfw': 'NSFW',
                            'gore_nsfw': '고어+NSFW'
                        }
                        name = scene_names.get(new_scene, new_scene)
                        await message.channel.send(
                            f"🔒 **수동 모드:** {name} 묘사 활성화\n"
                            f"_자동 감지로 돌아가려면 `!장면 일반`_"
                        )
                else:
                    await message.channel.send(
                        f"⚠️ 알 수 없는 설정: `{arg}`\n"
                        f"사용 가능: `일반(자동)`, `고어`, `성인`, `전체`"
                    )
                return
            
            # --- 비일상 감지 설정 ---
            if cmd == 'abnormal':
                arg = parsed.get('content', '').strip().lower()
                
                # 현재 상태 조회
                if not arg:
                    enabled = domain_manager.is_abnormal_detection_enabled(channel_id)
                    status = "🟢 활성화" if enabled else "🔴 비활성화"
                    counter = domain_manager.get_abnormal_trigger_counter(channel_id)
                    
                    await message.channel.send(
                        f"👁️ **비일상 감지 상태:** {status}\n"
                        f"⚡ **비일상 발생 카운터:** {counter}/100\n\n"
                        f"_AI가 장면에서 비일상적 요소(마법, 드래곤, 초능력 등)를 감지하고_\n"
                        f"_캐릭터의 비일상 적응도를 자동으로 업데이트합니다._\n"
                        f"_시간 경과/장소 이동 시 카운터가 증가하며, 100이 되면 0.1% 확률로 비일상 이벤트가 발생합니다._\n"
                        f"_(발생 조건: 비일상이 없거나 모든 적응도가 80% 이상, gore/nsfw 장면이 아닐 때)_\n\n"
                        f"**사용법:**\n"
                        f"• `!비일상 켜기` - 비일상 감지 활성화\n"
                        f"• `!비일상 끄기` - 비일상 감지 비활성화"
                    )
                    return
                
                # 설정 변경
                enable_keywords = ['켜기', 'on', '활성화', 'enable', '1', 'true']
                disable_keywords = ['끄기', 'off', '비활성화', 'disable', '0', 'false']
                
                if arg in enable_keywords:
                    domain_manager.set_abnormal_detection(channel_id, True)
                    await message.channel.send(
                        f"🟢 **비일상 감지 활성화**\n"
                        f"_AI가 비일상적 요소를 감지하고 적응도를 업데이트합니다._"
                    )
                elif arg in disable_keywords:
                    domain_manager.set_abnormal_detection(channel_id, False)
                    await message.channel.send(
                        f"🔴 **비일상 감지 비활성화**\n"
                        f"_비일상 적응도 시스템이 일시 중지됩니다._"
                    )
                else:
                    await message.channel.send(
                        f"⚠️ 알 수 없는 설정: `{arg}`\n"
                        f"사용 가능: `켜기`, `끄기`"
                    )
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
            
            ai_mem = domain_manager.get_ai_memory(channel_id, uid)
            if not ai_mem:
                await message.channel.send("❌ 먼저 `!가면`으로 캐릭터를 등록하세요.")
                return
            
            if not client_genai:
                await message.channel.send("⚠️ AI가 비활성화되어 OOC 수정이 불가능합니다.")
                return
            
            wait_msg = await message.channel.send("⏳ **[OOC]** 요청 처리 중...")
            
            p_data = domain_manager.get_participant_data(channel_id, uid)
            
            edit_result = await memory_system.process_ooc_memory_edit(
                client_genai, MODEL_ID, ooc_content, ai_mem, p_data
            )
            
            if edit_result and edit_result.get("edits"):
                updated_mem, updated_participant = memory_system.apply_memory_edits(
                    ai_mem, edit_result["edits"], p_data
                )
                domain_manager.update_ai_memory(channel_id, uid, updated_mem)
                
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

                # API 응답 구조 검증
                edited_fields = []
                if isinstance(edit_result.get("edits"), list):
                    for e in edit_result["edits"]:
                        if isinstance(e, dict):
                            field = e.get("field", "")
                            if field and isinstance(field, str):
                                edited_fields.append(field.split(".")[0])
                edited_fields = list(set(edited_fields))

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
        # =========================================================
        if parsed['type'] == 'chat_with_ooc':
            ooc_content = parsed.get('ooc_content', '')
            chat_content = parsed.get('chat_content', '')
            uid = str(message.author.id)
            
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
                        
                        if updated_participant:
                            if "economy" in updated_participant:
                                p_data["economy"] = updated_participant["economy"]
                            if "inventory" in updated_participant:
                                p_data["inventory"] = updated_participant["inventory"]
                            if "status_effects" in updated_participant:
                                p_data["status_effects"] = updated_participant["status_effects"]
                            domain_manager.save_participant_data(channel_id, uid, p_data)
                        
                        ooc_applied = True
                        
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
            
            # 행동/대사는 일반 chat으로 처리 계속 진행
            parsed = {
                'type': 'chat',
                'content': chat_content,
                'style': parsed.get('style', {})
            }
        
        # =========================================================
        # AI 응답 생성
        # =========================================================
        if parsed['type'] == 'command' and not system_trigger:
            return
        
        # 세션 잠금 확인 - 세션이 잠겨있어야(시작되어야) AI 응답 생성
        if not domain_data['settings'].get('session_locked', False) and not system_trigger:
            return
        
        async with message.channel.typing():
            if not domain_manager.update_participant(channel_id, message.author):
                return
            
            user_mask = domain_manager.get_user_mask(channel_id, message.author.id)
            
            # 스타일에 따른 action_text 포맷팅
            if system_trigger:
                action_text = system_trigger
            else:
                style = parsed.get('style', 'Description')
                content = parsed['content']
                
                # 스타일별 포맷 (AI가 대사/행동/묘사를 명확히 구분)
                if style == 'Dialogue':
                    # 따옴표로 시작하면 대사
                    action_text = f"[{user_mask}] says: {content}"
                elif style == 'Action':
                    # *로 감싸져 있으면 행동
                    action_text = f"[{user_mask}] does: {content}"
                else:
                    # 그 외는 일반 묘사/서술
                    action_text = f"[{user_mask}]: {content}"
            
            # 대기 모드에서는 기록만 하고 AI 응답 생성 안 함
            response_mode = domain_manager.get_response_mode(channel_id)
            if response_mode == 'waiting' and not system_trigger:
                domain_manager.append_history(channel_id, "User", action_text)
                await message.add_reaction("✏️")
                return
            
            # 컨텍스트 수집
            lore_txt = domain_manager.get_lore_with_npcs(channel_id)
            rule_txt = domain_manager.get_rules(channel_id)
            world_ctx = world_manager.get_world_context(channel_id)
            obj_ctx = quest_manager.get_objective_context(channel_id)
            active_genres = domain_manager.get_active_genres(channel_id)
            custom_tone = domain_manager.get_custom_tone(channel_id)
            scene_type = domain_manager.get_scene_type(channel_id)  # 장면 유형 가져오기
            
            # 좌뇌 분석용 최근 히스토리 (상수 사용)
            history = domain_data.get('history', [])[-fermentation.RECENT_HISTORY_FOR_ANALYSIS:]
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
            
            # === 장면 유형 자동 감지 (좌뇌 분석 결과 사용) ===
            detected_scene_type = nvc_res.get("SceneType", "normal")
            if detected_scene_type and detected_scene_type != "normal":
                # 수동 설정이 없으면 자동 감지된 장면 유형 사용
                manual_scene_type = domain_manager.get_scene_type(channel_id)
                if manual_scene_type == "normal":
                    scene_type = detected_scene_type
                    logging.info(f"[Scene] 자동 감지된 장면 유형: {scene_type}")
                else:
                    scene_type = manual_scene_type  # 수동 설정 우선
            
            # === [5] FERMENTED 메모리 컨텍스트 ===
            fermented_ctx = ""
            try:
                fermented_ctx = fermentation.build_fermented_context(domain_data)
            except Exception as fme:
                logging.warning(f"[Fermentation] Fermented 컨텍스트 빌드 실패: {fme}")
            
            # === [10] Current Context 구성 ===
            current_context_parts = []
            
            # 퀘스트/메모를 별도로 강조 (AI가 더 잘 인식하도록)
            if obj_ctx and obj_ctx.strip() != quest_manager.EMPTY_QUEST_MEMO_MSG:
                current_context_parts.append(f"### [ACTIVE QUESTS & MEMOS - CRITICAL INFO]\n{obj_ctx}\n**⚠️ Always reference active quests and memos when relevant to the scene.**")
            
            if world_ctx:
                current_context_parts.append(f"### World State\n{world_ctx}")
            
            if temporal_ctx:
                current_context_parts.append(temporal_ctx.strip())
            
            if ai_memory_ctx:
                current_context_parts.append(f"### AI Memory\n{ai_memory_ctx}")
            
            if npc_attitude_ctx:
                current_context_parts.append(npc_attitude_ctx.strip())
            
            if npc_interaction_ctx:
                current_context_parts.append(npc_interaction_ctx.strip())
            
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
            deep_memory = domain_data.get("deep_memory", "")
            
            # 발효 요약 추출
            fermented_summaries = []
            for entry in domain_data.get("fermented_history", []):
                if entry.get("summary"):
                    fermented_summaries.append(entry["summary"])
            fermented_summary_text = "\n---\n".join(fermented_summaries)
            
            # === 프리셋 순서 기반 full_prompt 구성 ===
            full_prompt = ""
            
            if fermented_ctx:
                full_prompt += f"{fermented_ctx}\n\n"
            
            full_prompt += f"""<Current-Context>
{current_context}
</Current-Context>

"""
            
            full_prompt += f"""<User_Message>
### Material (플레이어 입력)
<material>
{action_text}
</material>
</User_Message>

"""
            
            full_prompt += """### [OUTPUT DIRECTIVE]
Process <material> as the player's attempt.

**Player Input Formats:**
- `[Name] says: "..."` → Player is speaking dialogue. NPCs should hear and respond.
- `[Name] does: *...*` → Player is performing an action. Describe the result.
- `[Name]: ...` → General description or narration by player.

## ⚠️ CRITICAL: STORY CONTINUITY & MEMORY
**BEFORE generating any response, you MUST:**
1. ✅ Check <Fermented> section for relevant past events
2. ✅ Verify current response doesn't contradict established history
3. ✅ Reference past NPCs, locations, and events mentioned in <Fermented>
4. ✅ Maintain consistency with Deep Memory and Episode Summaries
5. ✅ **Check ACTIVE QUESTS & MEMOS** — Remember ongoing objectives and important information
6. ✅ **Reference AI MEMORY CONTEXT** — Use stored relationships, passives, and known information

**Common Mistakes to AVOID:**
- ❌ Forgetting NPCs already introduced in past sessions
- ❌ Contradicting established relationships or events
- ❌ Ignoring plot threads mentioned in <Fermented>
- ❌ Treating recurring locations as if they're new
- ❌ **Forgetting active quests when they become relevant**
- ❌ **Ignoring memos that contain critical clues or information**
- ❌ **Not using stored AI memory (relationships, passives, known info)**

## ⚠️ CRITICAL: ANTI-IMPERSONATION RULE
**The characters marked with `[Name]` are PLAYER CHARACTERS (PCs). NEVER generate their dialogue, thoughts, or actions.**

**FORBIDDEN:**
- ❌ Making PC speak (no dialogue like "yes", "okay", "I agree")
- ❌ Making PC think (no internal monologue)
- ❌ Making PC react emotionally (no "was surprised", "felt sad")
- ❌ Making PC decide or choose actions

**ALLOWED:**
- ✅ NPC dialogue responding to PC
- ✅ Environmental descriptions
- ✅ World consequences of PC's stated action
- ✅ NPC reactions and behaviors

**Do NOT generate ANY player's dialogue, thoughts, or decisions.**
Generate NPC reactions and world response ONLY.
**Apply NPC attitudes to their speech and behavior.**
**If NPC Interaction is suggested, include their ambient dialogue.**
**CRITICAL: Reference the FERMENTED/DEEP MEMORY above for story continuity.**
**CRITICAL: Remember and reference ACTIVE QUESTS and MEMOS when relevant.**
**CRITICAL: Use AI MEMORY CONTEXT (relationships, passives, known info) in your narrative.**
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
                        fermentation_module=fermentation,
                        scene_type=scene_type  # 장면 유형 전달
                    )
                    if used_cache:
                        logging.info(f"[Session] 캐싱 세션 사용 - {channel_id}")
                except Exception as cache_err:
                    logging.warning(f"[Session] 캐싱 실패, 일반 세션 사용: {cache_err}")
                    # 수정: fermented_summary 파라미터 추가
                    session = persona.create_risu_style_session(
                        client_genai, MODEL_ID, lore_txt, rule_txt,
                        active_genres, custom_tone, deep_memory,
                        fermented_summary=fermented_summary_text,
                        character_descriptions="",
                        scene_type=scene_type  # 장면 유형 전달
                    )
                
                # 히스토리 추가
                for h in domain_data.get('history', []):
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
                    system_update_match = re.search(
                        r'```system_update\s*\n?\s*(\{.*?\})\s*\n?```',
                        response,
                        re.DOTALL
                    )
                    
                    if system_update_match:
                        try:
                            update_json = json.loads(system_update_match.group(1))
                            
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
                                
                                # 동행자/펫 추가 (known_info에 저장, 외형에 넣지 않음!)
                                if update_json.get("companion_add"):
                                    if "known_info" not in ai_mem:
                                        ai_mem["known_info"] = []
                                    companions = update_json["companion_add"]
                                    
                                    # dict 형태: {"Shadow": "loyal wolf"}
                                    if isinstance(companions, dict):
                                        for name, desc in companions.items():
                                            companion_info = f"동행자: {name} - {desc}"
                                            if companion_info not in ai_mem["known_info"]:
                                                ai_mem["known_info"].append(companion_info)
                                                update_msgs.append(f"🐾 **{name}**")
                                    # list 형태: ["Shadow the wolf"]
                                    elif isinstance(companions, list):
                                        for companion in companions:
                                            if companion:
                                                companion_info = f"동행자: {companion}"
                                                if companion_info not in ai_mem["known_info"]:
                                                    ai_mem["known_info"].append(companion_info)
                                                    update_msgs.append(f"🐾 **{companion}**")
                                    # string 형태: "Shadow"
                                    elif isinstance(companions, str):
                                        companion_info = f"동행자: {companions}"
                                        if companion_info not in ai_mem["known_info"]:
                                            ai_mem["known_info"].append(companion_info)
                                            update_msgs.append(f"🐾 **{companions}**")
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
                            await message.channel.send("⚠️ 시스템 업데이트 처리 중 오류 발생 (JSON 파싱 실패)")
                        except Exception as ue:
                            logging.warning(f"[SYSTEM_UPDATE] 업데이트 실패: {ue}")
                            await message.channel.send(f"⚠️ 시스템 업데이트 처리 중 오류: {str(ue)[:50]}")
                        
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
                    
                    # 발효 필요 여부 체크 및 실행 - 수정: client_genai 사용
                    if fermentation.should_ferment_fresh(session_data):
                        logging.info(f"[Fermentation] 자동 발효 시작 - {channel_id}")
                        await fermentation.auto_ferment(
                            client_genai, MODEL_ID, session_data,
                            save_callback=lambda: domain_manager.save_domain(channel_id, session_data)
                        )
                except Exception as fe:
                    logging.warning(f"[Fermentation] 자동 발효 실패 (무시됨): {fe}")
    
    except Exception as e:
        logging.error(f"Error: {e}", exc_info=True)
        try:
            await message.channel.send(f"⚠️ **오류:** {str(e)[:100]}")
        except (discord.HTTPException, discord.Forbidden) as send_error:
            logging.error(f"오류 메시지 전송 실패: {send_error}")


# =========================================================
# 메인 실행
# =========================================================
def validate_environment() -> bool:
    """환경 변수 검증"""
    errors = []

    if not DISCORD_TOKEN:
        errors.append("DISCORD_TOKEN이 설정되지 않았습니다.")

    if not GEMINI_API_KEY:
        errors.append("GEMINI_API_KEY가 설정되지 않았습니다.")

    if not client_genai:
        errors.append("Gemini 클라이언트 초기화에 실패했습니다.")

    if errors:
        print("=" * 60)
        print("🚨 환경 설정 오류:")
        print("=" * 60)
        for error in errors:
            print(f"  ❌ {error}")
        print("=" * 60)
        print("💡 .env 파일을 확인하고 필요한 환경 변수를 설정해주세요.")
        print("   예시:")
        print("   DISCORD_TOKEN=your_discord_token_here")
        print("   GEMINI_API_KEY=your_gemini_api_key_here")
        print("=" * 60)
        return False

    return True


if __name__ == "__main__":
    if validate_environment():
        logging.info("환경 변수 검증 완료")
        client_discord.run(DISCORD_TOKEN)
    else:
        logging.error("환경 변수 검증 실패 - 봇을 시작할 수 없습니다.")
        exit(1)
