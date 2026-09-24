"""
Lorekeeper TRPG Bot - Session Manager Module
Manages session lifecycle (Reset, Check Prep, Start).
Replaces: session_manager.py
"""

import discord
import asyncio
import logging
from typing import Optional

import domain_manager
import config

logger = logging.getLogger(__name__)


async def _drain_background(channel_id: str, timeout: float = 30.0) -> bool:
    """[2026-09-24 감사] 리셋·클리어 전 배경 큐 비우기 + 실행 중 태스크 대기(`!다시`와 같은 문).

    안 하면 마지막 턴의 배경 작업(추출·발효·리더 등)이 리셋 **뒤에** 끝나 옛 세션 상태(fermented·deep·
    세션 NPC·메모리·위키 play 절)를 새 세션에 다시 썼다. 대기 시간 안에 못 끝나면 False.
    """
    try:
        from background_task_queue import get_task_queue
        q = get_task_queue()
        await q.flush_channel(channel_id)
        return bool(await q.wait_for_channel(channel_id, timeout=timeout))
    except Exception as e:
        logger.debug(f"[Session] background drain skip: {e}")
        return True
# game_system might be needed if logic requires it, but for now mostly domain IO

# [2026-09-02] 5.0 → 30.0. 구 값은 **물리적으로 누르기 어려웠다** — 봇이 경고 메시지를 보내고
#   자기 이모지를 단 뒤 사용자가 그걸 보고 클릭하기까지가 5초 안에 끝나야 했다.
#   그래서 `!리셋`이 "안 먹는" 것처럼 보였다(레티어스 실사용 보고). 타임아웃 문구는 f-string이라
#   이 상수만 고치면 안내도 같이 따라온다.
RESET_CONFIRM_TIMEOUT = 30.0
RESET_CONFIRM_EMOJI = "💥"
FALLBACK_PURGE_DELAY = 2

class SessionManager:
    """Manages session lifecycle events."""
    
    async def execute_reset(self, message: discord.Message, client: discord.Client) -> None:
        """Fully resets the session by blowing up the channel. 이모지 확인 후 실행."""
        channel_id = str(message.channel.id)
        
        # 이모지 확인
        confirm_msg = await message.channel.send(
            "🧨 **[경고: 전체 초기화]**\n"
            "이 채널의 **모든 데이터**가 삭제되고 채널이 재생성됩니다.\n"
            f"{RESET_CONFIRM_EMOJI} 이모지를 눌러 {RESET_CONFIRM_TIMEOUT}초 내에 확정하십시오."
        )
        await confirm_msg.add_reaction(RESET_CONFIRM_EMOJI)
        
        def check(reaction, user):
            return (user == message.author and str(reaction.emoji) == RESET_CONFIRM_EMOJI and reaction.message.id == confirm_msg.id)
        
        try:
            await client.wait_for('reaction_add', timeout=RESET_CONFIRM_TIMEOUT, check=check)
        except asyncio.TimeoutError:
            try:
                await confirm_msg.delete()
                # [2026-09-02] delete_after=5 → 20. 취소 안내가 5초 만에 사라져서 사용자가
                #   "아무 반응이 없었다"고 읽었다 — 실패가 소리를 내지 않는 자리였다.
                await message.channel.send(
                    f"❌ 초기화 취소됨 ({RESET_CONFIRM_TIMEOUT:.0f}초 내 확인 없음). "
                    "다시 하려면 `!리셋`을 입력하고 💥 를 눌러주세요.", delete_after=20)
            except Exception as e:
                logger.debug(f"[무시됨] 초기화 취소 메시지 처리 실패: {e}")
            return
        
        # Reset Data
        if not await _drain_background(channel_id):   # [2026-09-24 감사]
            await message.channel.send("⏳ 이전 턴 배경 작업이 아직 도는 중입니다. 잠시 뒤 `!리셋`을 다시 입력해 주세요.")
            return
        domain_manager.reset_domain(channel_id) # Clears cache and files
        
        # Recreate Channel
        await self._recreate_channel(message)
    
    async def _recreate_channel(self, message: discord.Message) -> None:
        original = message.channel
        try:
            new_ch = await original.clone(reason="Session Reset")
            try:
                await new_ch.edit(position=original.position)
            except Exception as e:
                logger.debug(f"[무시됨] 채널 위치 복원 실패: {e}")
            
            await original.delete(reason="Session Reset (Old)")
            await new_ch.send("✨ **세션 초기화 완료.**\n새로운 타임라인이 시작되었습니다.\n`!준비` (`!ready`)를 입력하여 설정을 시작하세요.")
        except Exception as e:
            await self._fallback_purge(original, e)

    async def _fallback_purge(self, channel, error) -> None:
        await channel.send(f"⚠️ **채널 재생성 실패:** {error}\n{FALLBACK_PURGE_DELAY}초 후 메시지 청소를 시도합니다...")
        await asyncio.sleep(FALLBACK_PURGE_DELAY)
        try:
            deleted = await channel.purge(limit=None, check=lambda m: not m.pinned)
            await channel.send(f"🧹 **{len(deleted)}개의 메시지를 청소했습니다.**\n`!준비`를 입력하세요.")
        except Exception as e:
            await channel.send(f"❌ 청소 실패: {e}")

    async def execute_clear(self, message: discord.Message) -> None:
        """
        [Soft Reset] Clears chat messages AND resets session state (History/World/NPCs).
        Keeps Lore and Participants.
        """
        content = message.content.lower().strip()
        args = content.split()
        
        # Confirmation Check
        if len(args) < 2 or args[1] not in ['confirm', '확인', 'y', 'yes']:
            await message.channel.send(
                "⚠️ **[세션 초기화 경고]**\n"
                "`!클리어` 명령어는 단순 채팅 청소가 아닙니다.\n"
                "**현재 세션의 진행 상황(히스토리, 퀘스트, 월드 상태)을 모두 초기화합니다.**\n"
                "(단, 로어북·룰·참가자·NPC 시트·출력 선언은 유지됩니다.)\n\n"
                "진행하시려면: `!클리어 확인` 또는 `!클리어 confirm` 입력."
            )
            return

        channel_id = str(message.channel.id)
        try:
            # 1. Soft Reset State
            if not await _drain_background(channel_id):   # [2026-09-24 감사]
                await message.channel.send("⏳ 이전 턴 배경 작업이 아직 도는 중입니다. 잠시 뒤 `!클리어 확인`을 다시 입력해 주세요.")
                return
            domain_manager.reset_session_state(channel_id)
            
            # 2. Visual Wipe
            await message.channel.send("🧹 **세션 초기화 중... (데이터 리셋 + 채팅 청소)**")
            await asyncio.sleep(2)
            deleted = await message.channel.purge(limit=None, check=lambda m: not m.pinned)
            
            # 3. Success Message
            await message.channel.send(
                "✨ **세션이 리셋되었습니다.**\n"
                f"• 삭제됨: {len(deleted)}개 메시지, 히스토리, 진행 상황\n"
                "• 유지됨: 로어북, 참가자, 룰, 등록 NPC, 출력 선언(변수·섹션·전이·형식)\n"
                "  — 선언은 남고 **값만 시작값**으로 돌아갑니다.\n"
                "이제 **!시작**을 입력하여 새 이야기를 시작하세요.",
                delete_after=10
            )
            
            # Ensure bot is active again
            domain_manager.set_bot_active(channel_id, True)
            
        except Exception as e:
            await message.channel.send(f"⚠️ 초기화 실패: {e}")

    async def check_preparation(self, message: discord.Message) -> None:
        """세션 준비 점검 — 4항목.

        [2026-09-05] 종전엔 로어 유무 + 룰 모드(항상 ✅)만 봐서 "무엇이 덜 됐는지"를
        알려주지 못했다. ① 로어 ② 참가자별 가면 ③ 세계 규칙 ④ 출력 규칙 넷을 표시한다.
        `prepared` 플래그의 의미는 그대로 — **①만 필수**, ②~④는 안내다.
        """
        channel_id = str(message.channel.id)
        lore = domain_manager.get_lore(channel_id)

        ready = True
        msg = "🔍 **시스템 준비 확인**\n"

        # ① 세계관 (필수)
        if lore and lore.strip() and lore != "No Lore Saved" and lore != config.DEFAULT_LORE:
             msg += "✅ 세계관(Lore) 로드됨\n"
        else:
             msg += "❌ 세계관 미설정 (`!lore [내용/파일]` 필요)\n"
             ready = False

        # ② 참가자별 가면
        try:
            _parts = domain_manager.get_active_participants(channel_id) or {}
        except Exception:
            _parts = {}
        if not _parts:
            msg += "⬜ 가면: 참가자 없음 (`!가면 [이름]`으로 등록)\n"
        else:
            _no_mask = [str(p.get("name") or uid) for uid, p in _parts.items() if not (p or {}).get("mask")]
            if _no_mask:
                msg += f"❌ 가면 미설정 {len(_no_mask)}명: {', '.join(_no_mask)} (`!가면 [이름]`)\n"
            else:
                msg += f"✅ 가면: 참가자 {len(_parts)}명 전원 설정됨\n"

        # ③ 세계 규칙 추가 여부
        # [2026-09-17] 신호를 rules_mode → world_state 실물로 교체. `!룰 추가`는 파일 첨부면
        #   `rules_text`, 한 줄이면 `location_rules`에 쓴다(Slot 23·world_board가 읽는 자리).
        #   rules_mode를 세우던 append_rules·set_custom_rules_from_file은 호출자 0 —
        #   옛 신호는 영원히 "default"라 파일을 넣어도 ⬜만 떴다.
        _ws = domain_manager.get_world_state(channel_id) or {}
        _rules_text = str(_ws.get("rules_text") or "").strip()
        _loc_rules = _ws.get("location_rules") or {}
        _n_loc = len(_loc_rules) if isinstance(_loc_rules, dict) else 0
        if _rules_text or _n_loc:
            _bits = []
            if _rules_text:
                _bits.append(f"파일 {len(_rules_text):,}자")
            if _n_loc:
                _bits.append(f"개별 {_n_loc}건")
            msg += f"✅ 세계 규칙: {' · '.join(_bits)}\n"
        else:
            msg += "⬜ 세계 규칙: 추가된 규칙 없음 (`!룰 추가 [내용/파일]`)\n"

        # ④ 출력 선언 — [2026-09-06 P7] `!출력룰` 한 명령이 넷으로 흩어졌으니(변수·섹션·
        #   전이·형식) 점검도 넷을 센다. 옛 문구는 형식 하나만 봐서, 변수만 선언한 채널을
        #   "출력 규칙 없음"으로 잘못 알렸다.
        #   변수는 **유저 선언만** 센다 — SYSTEM_VARS 기본형이 늘 얹혀 비지 않으므로
        #   그대로 세면 신호가 안 된다.
        _n_var = _n_sec = _n_tr = _n_fmt = 0
        try:
            import custom_vars as _cv_rd
            _n_var = len([1 for _v in _cv_rd.get_declarations(channel_id).values()
                          if isinstance(_v, dict) and not _v.get("system")])
        except Exception:
            pass
        try:
            import status_panel as _sp_rd
            _n_sec = len(_sp_rd.list_panel_sections(channel_id))
        except Exception:
            pass
        try:
            import expr_engine as _ee_rd
            _n_tr = len(_ee_rd.list_transitions(channel_id))
        except Exception:
            pass
        try:
            _n_fmt = len(domain_manager.get_output_rules(channel_id))
        except Exception:
            pass
        _n_all = _n_var + _n_sec + _n_tr + _n_fmt
        if _n_all:
            msg += (f"✅ 출력 선언 {_n_all}건: 변수 {_n_var} · 섹션 {_n_sec} · "
                    f"전이 {_n_tr} · 형식 {_n_fmt}\n")
        else:
            msg += "⬜ 출력 선언 없음 (`!출력룰 추가` + 파일)\n"

        # [2026-09-07 P9] 문구만 — 상태창은 이제 매턴 응답 하단 임베드다(상단 헤더 폐지).
        msg += "🪧 상태창은 매턴 응답 **바로 밑 임베드**로 전 장 자동 표시됩니다 (최대 10장).\n"
        # [2026-09-13 P9c] `!노트북` 폐기 뒤 유저가 제 노트북을 보는 길이 💠 하나다 — 그래서 적는다.
        msg += "💠 = 노트북·기록·일지·도착물 (매턴 버튼)\n"

        if ready:
            d = domain_manager.get_domain(channel_id)
            d["prepared"] = True
            domain_manager.save_domain(channel_id, d)
            
            msg += "\n✨ **준비 완료!** 다음 명령어로 시작하세요: `!가면 [이름]` -> `!시작`"
        else:
            d = domain_manager.get_domain(channel_id)
            d["prepared"] = False
            domain_manager.save_domain(channel_id, d)
            msg += "\n❗ **준비 미비.** 필수 항목을 확인해주세요."
            
        await message.channel.send(msg)

    async def start_session(self, message: discord.Message, client_genai, model_id: str) -> bool:
        channel_id = str(message.channel.id)
        d = domain_manager.get_domain(channel_id)
        
        if not d.get("prepared"):
            await message.channel.send("⚠️ 먼저 `!준비` 명령어로 상태를 확인해주세요.")
            return False
            
        if d["settings"].get("session_locked"):
            await message.channel.send("⚠️ 세션이 이미 진행 중입니다.")
            return False
            
        domain_manager.set_session_lock(channel_id, True)
        await message.channel.send("🎬 **세션 시작.**\n외부 개입이 차단되었습니다. 오프닝 생성 중...")
        return True

manager = SessionManager()
