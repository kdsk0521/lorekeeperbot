# -*- coding: utf-8 -*-
"""
Lorekeeper TRPG Bot - Main Module
Version: 5.0 (Modularized with Orchestration Service)
"""

import discord
import os
import asyncio
import logging
import logging.handlers
from typing import Optional, Dict
from collections import defaultdict
from google import genai

# =========================================================
# MODULE IMPORTS
# =========================================================
try:
    import config
    import bot_utils
    import input_handler
    import command_handler
    import domain_manager

    # Orchestration Service
    from orchestration import get_orchestration_runtime
    import module_frame   # [2026-10-23 C64 M5] 입력 정거장 · persistent view 등록부

except ImportError as e:
    print(f"CRITICAL ERROR: Failed to import modules. {e}")
    exit(1)

# =========================================================
# CONFIGURATION & LOGGING
# =========================================================
_log_fmt = logging.Formatter('%(asctime)s [%(levelname)s] %(message)s')

# Console handler (기존)
_console_h = logging.StreamHandler()
_console_h.setFormatter(_log_fmt)

# File handler — 10MB × 5 rotations (최대 ~60MB)
os.makedirs("logs", exist_ok=True)
_file_h = logging.handlers.RotatingFileHandler(
    "logs/bot.log", maxBytes=10_000_000, backupCount=5, encoding="utf-8"
)
_file_h.setFormatter(_log_fmt)

logging.basicConfig(level=logging.INFO, handlers=[_console_h, _file_h])

# [2026-08-03] 전문 전용 채널 분리 — journal은 흐름만, 전문은 logs/verbose.log.
# propagate=False라 이 위 두 핸들러로 새지 않는다. 실패해도 봇은 그대로 뜬다.
bot_utils.setup_verbose_log()

DISCORD_TOKEN = config.DISCORD_TOKEN
GEMINI_API_KEY = config.GEMINI_API_KEY
# [2026-08-18 라우팅 개편] 제미니 모델명을 이름표로 돌리지 않는다 — **역할**을 선언한다.
#   main = 구 MODEL_ID 자리(발효·연대기·GC·OOC 편집) / flash = 배경 분석 공용.
#   gemini 백엔드면 실명, openai 면 "role:main"/"role:flash" 토큰이 온다(config.role_model).
MODEL_ID = config.role_model("main")
MODEL_ID_FLASH = config.role_model("flash")

# [2026-07-02] Gemini 키는 gemini 백엔드 경로가 활성일 때만 필요 (openai 전환 후 하드 의존 제거).
if not GEMINI_API_KEY and (config.ANALYSIS_BACKEND != "openai" or config.RENDERER_BACKEND != "openai"):
    logging.warning("GEMINI_API_KEY Missing! (gemini 백엔드 경로 활성 — 롤백/폴백 시 필요)")

client_genai = None
try:
    if config.ANALYSIS_BACKEND == "openai":
        # 좌뇌(Flash 분석)를 wellspring(DeepSeek)으로 라우팅. genai.Client 호환 facade.
        from analysis_backend import build_analysis_client
        client_genai = build_analysis_client()
        logging.info("[Analysis] backend=openai → GenaiCompatClient (wellspring DeepSeek)")
    elif GEMINI_API_KEY:
        client_genai = genai.Client(api_key=GEMINI_API_KEY)
except Exception as e: logging.error(f"GenAI Init Failed: {e}")

intents = discord.Intents.default()
intents.message_content = True
client_discord = discord.Client(intents=intents)

# [2026-10-23 C64 M5] on_input 정거장에 넘기는 작은 runtime — 칸 이름이 OrchestrationService 와 같다
#   (client · model_id · model_id_flash). 루카 콜·OOC 분류·편집 콜이 옛 전역 대신 이걸 읽는다.
import types as _types  # noqa: E402
_INPUT_RT = _types.SimpleNamespace(client=client_genai, model_id=MODEL_ID, model_id_flash=MODEL_ID_FLASH)

# [2026-10-23 C60 M2c] 턴 잠금 표는 공용 자리(turn_locks)로 — Activity 편집이 같은 잠금을 쥔다.
from turn_locks import channel_locks  # noqa: E402


# =========================================================
# DISCORD EVENTS
# =========================================================
def _backup_channel_dir(channel_id: str, backup_root: str) -> bool:
    """[2026-09-24 감사] 채널 폴더 한 개 백업 — state.json + memory.db(sqlite backup API) + lore/.

    전엔 state.json 만 복사했는데 NPC 본체·관계·발효/deep 기록은 memory.db 가 정본(V10 READ_FROM_SQLITE,
    HISTORY_STRIP_JSON)이라 백업을 믿고 복원하면 핵심 상태가 없었다. cache.db 는 재생성 캐시라 뺀다.
    """
    import shutil as _sh
    import sqlite3 as _sq
    src_dir = domain_manager.get_channel_dir(channel_id)
    if not os.path.isfile(os.path.join(src_dir, config.CHANNEL_STATE_FILE)):
        return False
    dst_dir = os.path.join(backup_root, str(channel_id))
    tmp_dir = dst_dir + ".tmp"
    _sh.rmtree(tmp_dir, ignore_errors=True)
    os.makedirs(tmp_dir, exist_ok=True)
    _sh.copy2(os.path.join(src_dir, config.CHANNEL_STATE_FILE), os.path.join(tmp_dir, config.CHANNEL_STATE_FILE))
    _db = os.path.join(src_dir, config.CHANNEL_DB_FILE)
    if os.path.isfile(_db):
        _src_c = _sq.connect(f"file:{_db}?mode=ro", uri=True)
        _dst_c = _sq.connect(os.path.join(tmp_dir, config.CHANNEL_DB_FILE))
        try:
            _src_c.backup(_dst_c)   # WAL 중에도 일관 스냅샷
        finally:
            _dst_c.close()
            _src_c.close()
    _lore = os.path.join(src_dir, config.CHANNEL_LORE_DIR)
    if os.path.isdir(_lore):
        _sh.copytree(_lore, os.path.join(tmp_dir, config.CHANNEL_LORE_DIR))
    _sh.rmtree(dst_dir, ignore_errors=True)
    os.replace(tmp_dir, dst_dir)
    return True


async def _auto_backup_loop():
    """매 24시간마다 모든 채널 폴더(state.json·memory.db·lore/)를 백업."""
    await client_discord.wait_until_ready()
    while not client_discord.is_closed():
        try:
            await asyncio.sleep(86400)  # 24시간
            channels_dir = config.CHANNELS_DIR
            if not os.path.isdir(channels_dir):
                continue
            backup_dir = os.path.join(config.DATA_DIR, "backups")
            os.makedirs(backup_dir, exist_ok=True)
            count = 0
            for channel_id in os.listdir(channels_dir):
                if channel_id.endswith(".tmp"):
                    continue
                try:
                    # 파일 복사·sqlite backup 은 이벤트 루프 밖에서.
                    if await asyncio.to_thread(_backup_channel_dir, channel_id, backup_dir):
                        count += 1
                except Exception as _e_bk:
                    logging.warning(f"[AutoBackup] {channel_id} skipped: {_e_bk}")
            if count:
                logging.info(f"[AutoBackup] {count} channel folders backed up to {backup_dir}")
        except asyncio.CancelledError:
            break
        except Exception as e:
            logging.error(f"[AutoBackup] Error: {e}")


@client_discord.event
async def on_ready():
    logging.info(f'Logged in as {client_discord.user}')
    await client_discord.change_presence(activity=discord.Game(name="!help | TRPG"))
    client_discord.loop.create_task(_auto_backup_loop())

    # [2026-10-23 C64 M5] persistent view 셋(TurnView · ArchiveView · 정정 되묻기)은 등록부에서 — 순서·로그 줄은 옛 그대로.
    #   (옛 손글 목록의 주석 요지) 💠/💌/💭 합성 View = custom_id 고정. 전 버튼 인스턴스 하나면 부분집합 메시지도 커버
    #   (디스패치 = custom_id 매칭). ArchiveView 는 💠 custom_id 가 TurnView 와 같아 **나중 등록이 이긴다** — 둘 다
    #   status_panel.respond_archive 로 위임하므로 어느 쪽이 이겨도 같은 창이다. 정정 되묻기 = C50 R4.
    #   선언 = builtin_modules `add_view(...)` 한 줄(이식 모듈의 버튼 판도 같다).
    for _v in module_frame.views():
        try:
            client_discord.add_view(_v.factory())
            logging.info(_v.ok_log)
        except Exception as e:
            logging.warning(_v.fail_log.format(e=e))

    # [2026-10-22 C54] Activity 화면 서버 — 봇 프로세스 안, 127.0.0.1:ACTIVITY_PORT(Funnel 이 이리로 넘긴다).
    #   on_ready 는 재연결마다 다시 불리지만 start 는 한 번만 띄운다. 꺼져 있거나 실패해도 봇은 그대로 돌고
    #   💠 는 종전 보관함 창으로 내려간다.
    try:
        import activity_server
        await activity_server.start(client_discord)
    except Exception as e:
        logging.warning(f"[Activity] server start failed: {e}")

@client_discord.event
async def on_message(message: discord.Message) -> None:
    if message.author == client_discord.user: return
    if not isinstance(message.channel, (discord.TextChannel, discord.Thread)): return

    asyncio.create_task(_process_message(message))

async def _process_message(message: discord.Message) -> None:
    channel_id = str(message.channel.id)
    async with channel_locks[channel_id]:
        try:
            content = message.content.strip()
            parsed = input_handler.parse_input(content)

            # 0. BOT ACTIVE GATE (봇 꺼진 채널: !봇 토글만 허용)
            if not domain_manager.get_bot_active(channel_id):
                if parsed and parsed['type'] == 'command' and parsed['command'] in ('bot', '봇'):
                    await command_handler.dispatch_command(
                        parsed['command'], message, channel_id, parsed,
                        client_discord, client_genai, MODEL_ID, MODEL_ID_FLASH
                    )
                return

            # 1. COMMANDS
            if parsed and parsed['type'] == 'command':
                sys_trigger = await command_handler.dispatch_command(
                    parsed['command'], message, channel_id, parsed,
                    client_discord, client_genai, MODEL_ID, MODEL_ID_FLASH
                )
                if sys_trigger and isinstance(sys_trigger, str):
                    await generate_ai_response(message, channel_id, sys_trigger)
                return

            # 2. SESSION LOCK CHECK
            if not domain_manager.is_session_locked(channel_id):
                return
            status = domain_manager.get_participant_status(channel_id, message.author.id)
            if not status: return  # Ignore non-participants

            # 3 · 3a · 3.5 [2026-10-23 C64 M5] OOC 갈래(단독 OOC · 행동+OOC · 루카 모드)는 on_input 정거장(ooc 모듈 — luka.py).
            #   정거장이 입력을 먹으면(stop) 아래 평턴 경로로 안 간다. 턴이 필요하면 묶음에 적힌 글로 여기서 띄운다.
            #   core 모듈 예외는 위로 올라와 맨 바깥 catch 가 받는다(옛 그대로: ⚠️ Error + 턴 없음).
            _in = module_frame.InputTurn(channel_id=channel_id, message=message, parsed=parsed)
            await module_frame.run_chain(module_frame.ON_INPUT, _INPUT_RT, _in)
            if _in.stop:
                if _in.turn_override is not None:
                    await generate_ai_response(message, channel_id, user_input_override=_in.turn_override,
                                               record_user_history=_in.record_user_history)
                return

            # 4. CHAT LOGGING / RESPONSE
            mode = domain_manager.get_response_mode(channel_id)

            if mode == 'waiting':
                mask = domain_manager.get_user_mask(channel_id, message.author.id)
                log_content = message.content
                if message.attachments:
                    for att in message.attachments:
                        txt, _ = await bot_utils.read_attachment_text(att)
                        if txt: log_content += f"\n(Attach: {txt})"

                # [LIBRA #2 C1] waiting 모드에서도 message.id 보존
                domain_manager.append_history(channel_id, mask, log_content.strip(), message_id=message.id)
                await message.add_reaction("✏️")
                return

            # AUTO MODE
            await generate_ai_response(message, channel_id)

        except Exception as e:
            logging.error(f"Message Error: {e}", exc_info=True)
            await message.channel.send(f"⚠️ Error: {e}")


# =========================================================
# AI GENERATION CORE (Delegated to OrchestrationService)
# =========================================================

async def generate_ai_response(
    message: discord.Message,
    channel_id: str,
    system_trigger: Optional[str] = None,
    user_input_override: Optional[str] = None,
    record_user_history: bool = True
) -> None:
    """AI 응답 생성 (OrchestrationService로 위임)"""
    orchestration = get_orchestration_runtime(client_genai, MODEL_ID, MODEL_ID_FLASH)
    if not orchestration:
        await message.channel.send("⚠️ No AI Configured")
        return

    feedback_msg = await message.channel.send("🔄 **서사를 생성하고 있습니다...**")

    try:
        await orchestration.execute(
            message,
            channel_id,
            system_trigger,
            feedback_msg=feedback_msg,
            user_input_override=user_input_override,
            record_user_history=record_user_history
        )
    except Exception as e:
        logging.error(f"Orchestration Error: {e}", exc_info=True)
        try:
            await feedback_msg.delete()
        except Exception:
            pass
        await message.channel.send(f"⚠️ 서사 생성 실패: {e}")


# =========================================================
# ⚰ [2026-10-23 C64 M5] OOC HELPER(루카 몸 · 표지 파서 · 회상/위키 발췌 · generate_ooc_response)는 luka.py 로 이사.
#    입력 갈래(옛 3 · 3a · 3.5)는 on_input 정거장 — 등록은 builtin_modules 의 ooc 모듈.
# =========================================================


if __name__ == "__main__":
    # [2026-07-02] 기동 게이트 백엔드-인지화: openai 전환 후 GEMINI_API_KEY 하드 의존 제거.
    # 활성 분석 백엔드가 요구하는 키만 필수. (renderer openai 키 부재는 persona가 로그 후 폴백)
    if config.ANALYSIS_BACKEND == "openai":
        _ai_key_ok = bool(config.ANALYSIS_OPENAI_API_KEY)
        _ai_key_name = "ANALYSIS_OPENAI_API_KEY"
    else:
        _ai_key_ok = bool(GEMINI_API_KEY)
        _ai_key_name = "GEMINI_API_KEY"
    if config.RENDERER_BACKEND == "gemini" and not GEMINI_API_KEY:
        _ai_key_ok = False
        _ai_key_name = f"{_ai_key_name} + GEMINI_API_KEY" if _ai_key_name != "GEMINI_API_KEY" else "GEMINI_API_KEY"
    # [2026-08-18 모델 env 단일 레버] config 에 모델 기본값이 없다 → 빠지면 조용히 굴러가는 대신
    # **빠진 이름을 전부 나열**하고 기동 거부. 검사는 여기서만 호출한다(config import 는 무해 유지).
    _missing_models = config.validate_model_env()
    if _missing_models:
        print("MISSING MODEL ENV (.env 에 아래 이름을 채우세요 — 코드 기본값 없음)")
        for _n in _missing_models:
            print(f"  - {_n}")
        print(f"  (backend: RENDERER={config.RENDERER_BACKEND} / ANALYSIS={config.ANALYSIS_BACKEND})")
    elif DISCORD_TOKEN and _ai_key_ok:
        client_discord.run(DISCORD_TOKEN)
    else:
        print(f"MISSING API KEYS (need DISCORD_TOKEN + {_ai_key_name})")
