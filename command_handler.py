"""
Lorekeeper TRPG Bot - Command Handler Module
Handles user commands (!help, !info, etc.) and AI system actions.
Replaces: command_handler.py, system_handler.py
"""

import discord
import asyncio
import logging
import io
import time
from typing import Optional, Dict
import re

# Unified Modules
import config
import domain_manager
import game_system
# session_manager and memory_system are still external for now, or integrated?
# Plan said session_manager is modified to import domain_manager directly.
# memory_system seems to be next or treated separately. I will assume memory_system exists.
import session_manager
import memory_system 
from bot_utils import send_long_message, read_attachment_text, safe_delete_message, SUPPORTED_TEXT_EXTENSIONS

# =========================================================
# SYSTEM HANDLER LOGIC (Absorbed)
# =========================================================
async def process_ai_system_action(channel_id: str, sys_action: dict) -> Optional[str]:
    """AI가 제안한 시스템 액션을 처리합니다."""
    if not sys_action or not isinstance(sys_action, dict): return None
    
    tool = sys_action.get("tool")
    atype = sys_action.get("type")
    content = sys_action.get("content")
    
    if not all([tool, atype, content]): return None
    
    auto_msg = None
    if tool == "Memo":
        if atype == "Add": auto_msg = game_system.add_memo(channel_id, content)
        elif atype == "Remove": auto_msg = game_system.remove_memo(channel_id, content)
        elif atype == "Archive": auto_msg = game_system.resolve_memo_auto(channel_id, content)
        
    elif tool == "Quest":
        if atype == "Add": auto_msg = game_system.add_quest(channel_id, content)
        elif atype == "Complete": auto_msg = game_system.complete_quest(channel_id, content)
        
    elif tool == "NPC" and atype == "Add":
        name = content.split(":", 1)[0].strip() if ":" in content else content
        desc = content.split(":", 1)[1].strip() if ":" in content else "Auto Registered"
        domain_manager.update_npc(channel_id, name, {"desc": desc, "source": "session", "status": "Active"})
        auto_msg = f"🎭 NPC: {name}"
        
    return auto_msg


# =========================================================
# COMMAND HANDLERS
# =========================================================

async def handle_lore_command(message, channel_id: str, arg: str, client_genai=None, model_id=None) -> None:
    """!로어 명령어 처리"""
    file_text = ""
    if message.attachments:
        for att in message.attachments:
            text, error = await read_attachment_text(att)
            if error:
                await message.channel.send(error)
                return
            if text:
                file_text = text
                break
        if not file_text and not arg:
             await message.channel.send(f"⚠️ 확장자 오류. 지원: {', '.join(SUPPORTED_TEXT_EXTENSIONS)}")
             return

    full = (arg + "\n" + file_text).strip()
    
    # 조회
    if not full:
        lore = domain_manager.get_lore(channel_id)
        npcs = domain_manager.get_npcs(channel_id)
        if lore == config.DEFAULT_LORE:
             await message.channel.send("📜 로어 없음. `!로어 [내용]` 입력.")
             return
             
        genres = domain_manager.get_active_genres(channel_id)
        tone = domain_manager.get_custom_tone(channel_id)
        
        msg = f"📜 **로어 정보**\nLength: {len(lore):,} chars\nNPCs: {len(npcs)}명\nGenres: {', '.join(genres)}"
        if tone: msg += f"\nTone: {tone}"
        await message.channel.send(msg)
        
        # Preview NPCs
        if npcs:
            preview = [f"• **{n}**: {d.get('desc','-')[:50]}..." for n, d in list(npcs.items())[:5]]
            await message.channel.send("👥 **NPC Preview:**\n" + "\n".join(preview))
            
        await message.channel.send(f"📄 **Lore Preview:**\n```\n{lore[:500]}...\n```")
        return

    # 초기화
    if full == "초기화":
        domain_manager.reset_lore(channel_id)
        await message.channel.send("📜 **로어 초기화됨**")
        return
        
    # 추출
    if full.lower() in ['추출', 'export']:
        # Check for incremental argument
        incremental = False
        # Remove primary command and check leftovers
        args = full.split()
        if len(args) > 1 and args[1].lower() in ['new', 'inc', '증분', '최신']:
            incremental = True
            
        export_text, msg = game_system.export_session_history(channel_id, incremental=incremental)
        if hasattr(game_system, 'export_session_history'):
             fname = f"SessionHistory_{channel_id}_{'INC' if incremental else 'FULL'}.txt"
             if export_text:
                await message.channel.send(msg, file=discord.File(io.StringIO(export_text), filename=fname))
             else:
                await message.channel.send(msg)   
        else:
             await message.channel.send("⚠️ 대화 내역 추출 기능 로드 실패.")
        return
        
    # 저장
    domain_manager.save_lore_original(channel_id, full)
    msg = await message.channel.send("📜 **로어 저장됨**. AI 분석 중...")
    
    if client_genai:
        try:
            # Entity Extraction (Parallel)
            # 1. NPCs (Legacy: extract_npcs_only)
            # 2. PC Info (New: extract_pc_info)
            npc_task = memory_system.extract_npcs_only(client_genai, model_id, full)
            pc_task = memory_system.extract_pc_info(client_genai, model_id, full)
            
            npcs, pc_info = await asyncio.gather(npc_task, pc_task)
            
            # Update NPCs
            for n in npcs:
                domain_manager.update_npc(channel_id, n.get("name"), {
                    "desc": n.get("description"), "source": "lore", "status": "Active"
                })
                
            # Update PC Info
            pc_msg = ""
            if pc_info and pc_info.get("name"):
                 # Save as default PC info for !mask to pick up
                 domain_manager.set_default_pc_info(channel_id, pc_info)
                 pc_msg = f"\n주인공 식별: {pc_info.get('name')} (가면 설정 시 자동 적용)"
            
            domain_manager.append_lore(channel_id, full) 
            
            # Genre Analysis
            res = await memory_system.analyze_genre_from_lore(client_genai, model_id, full)
            domain_manager.set_active_genres(channel_id, res.get("genres", ["noir"]))
            domain_manager.set_custom_tone(channel_id, res.get("custom_tone"))
            
            await msg.edit(content=f"✅ **로어 분석 완료**\nNPC: {len(npcs)}명 추출{pc_msg}\n장르: {res.get('genres')}")
        except Exception as e:
            await msg.edit(content=f"⚠️ 분석 오류: {e}")
    else:
        domain_manager.append_lore(channel_id, full)
        await msg.edit(content="📜 저장 완료 (AI 미사용)")


async def handle_info_command(message, channel_id: str, sub_command: str = "") -> None:
    uid = str(message.author.id)
    if not domain_manager.get_participant_data(channel_id, uid):
        await message.channel.send("❌ 등록 필요 (`!가면`)")
        return

    res = domain_manager.get_unified_player_info(channel_id, uid)
    
    # Append Quest & Memo Info
    quests = game_system.get_active_quests(channel_id)
    memos = game_system.get_memos(channel_id)
    
    if quests:
        res += "\n**🛡️ 진행 중인 퀘스트:**\n" + "\n".join([f"- {q}" for q in quests]) + "\n"
    
    if memos:
        res += "\n**📝 메모:**\n" + "\n".join([f"- {m}" for m in memos]) + "\n"

    await send_long_message(message.channel, res)



async def handle_npc_command(message, channel_id: str, cmd: str, arg: str, client_genai=None, model_id=None) -> None:
    """NPC 관련 명령어 처리 (!npc, !addnpc)"""
    # 1. 파일 내용 확인
    file_text = ""
    if message.attachments:
        for att in message.attachments:
            text, error = await read_attachment_text(att)
            if error:
                await message.channel.send(error)
                return
            if text:
                file_text = text
                break

    # 2. 일괄 처리 로직 (Batch Processing)
    # 텍스트 파일이나 인자에서 여러 줄의 NPC 정의를 읽어들임
    raw_lines = (arg + "\n" + file_text).strip().splitlines()
    processed_count = 0
    
    if cmd == 'addnpc':
        if not raw_lines:
            await message.channel.send("⚠️ 등록할 내용이 없습니다. `!npc추가 [이름]: [설명]` 또는 파일 첨부.")
            return

        last_name = None
        
        # [Smart Parsing Logic]
        # Detect if this is a "Deep Profile" (e.g. Character Card) or "Batch List"
        # Heuristic: Presence of explicit "Name:" or "이름:" triggers suggests a profile.
        full_text = "\n".join(raw_lines)
        is_deep_profile = bool(re.search(r"(?:^|\n)\s*(?:\*|-)?\s*(?:Name|이름)\s*:", full_text, re.IGNORECASE))
        
        for line in raw_lines:
            line = line.strip()
            if not line: continue
            
            # 1. Explicit Name Trigger (Start of New NPC in Profile Mode)
            # Matches: "Name: Nyx", "* 이름: 닉스", "- Name : Arthur"
            name_match = re.match(r"^(?:\*|-)?\s*(?:Name|이름)\s*:\s*(.+)$", line, re.IGNORECASE)
            
            if name_match:
                name = name_match.group(1).strip()
                # Create/Reset NPC. Description starts with this line (preserving the Name line in bio is good)
                domain_manager.update_npc(channel_id, name, {"desc": line, "source": "manual", "status": "Active"})
                processed_count += 1
                last_name = name
                continue

            # 2. Key-Value Line (Property or New NPC)
            if ":" in line:
                # If we are in Deep Profile Mode and have a current target, treat this as a property
                if is_deep_profile and last_name:
                     curr_npc = domain_manager.get_npc(channel_id, last_name)
                     if curr_npc:
                         new_desc = curr_npc.get("desc", "") + "\n" + line
                         domain_manager.update_npc(channel_id, last_name, {"desc": new_desc, "source": "manual", "status": "Active"})
                     continue
                
                # If NOT in Deep Profile Mode (or no active target), treat "Key: Value" as "Name: Desc"
                # This supports the simple batch format: "Arthur: Merchant", "* Merlin: Wizard"
                key, val = line.split(":", 1)
                clean_key = key.lstrip("*-> ").strip() # Remove bullets if user bulleted the list
                val = val.strip()
                
                if clean_key and val:
                    domain_manager.update_npc(channel_id, clean_key, {"desc": val, "source": "manual", "status": "Active"})
                    processed_count += 1
                    last_name = clean_key
                    continue

            # 3. Continuation Text (No colon)
            # Appends to the last defined NPC's description
            if last_name:
                 curr_npc = domain_manager.get_npc(channel_id, last_name)
                 if curr_npc:
                     new_desc = curr_npc.get("desc", "") + "\n" + line
                     domain_manager.update_npc(channel_id, last_name, {"desc": new_desc, "source": "manual", "status": "Active"})

        if processed_count > 0:
            if processed_count == 1:
                await message.channel.send(f"👥 **NPC 등록:** {last_name}")
            else:
                await message.channel.send(f"👥 **NPC 일괄 등록 완료:** 총 {processed_count}명")
        else:
             await message.channel.send("⚠️ 유효한 형식을 찾을 수 없습니다. (예: `이름: 설명`)")
        return

    # Look up NPC
    if not arg:
        npcs = domain_manager.get_npcs(channel_id)
        if not npcs:
            await message.channel.send("👥 등록된 NPC가 없습니다.")
            return
        
        # List all
        name_list = [f"• **{n}**: {d.get('desc','-')[:30]}..." for n, d in npcs.items()]
        await send_long_message(message.channel, "👥 **NPC 목록**\n" + "\n".join(name_list))
    else:
        # Specific NPC
        npc = domain_manager.get_npc(channel_id, arg)
        if npc:
            msg = f"👤 **{arg}**\n{npc.get('desc')}\n"
            if npc.get('appearance'): msg += f"Look: {npc.get('appearance')}\n"
            if npc.get('personality'): msg += f"Personality: {npc.get('personality')}\n"
            await message.channel.send(msg)
        else:
            await message.channel.send(f"⚠️ NPC '{arg}' 정보를 찾을 수 없습니다.")


async def handle_participant_command(message, channel_id: str, cmd: str, arg: str) -> None:
    """참가자 상태 관리 (!desc, !afk, !leave, !back)"""
    uid = str(message.author.id)
    p_data = domain_manager.get_participant_data(channel_id, uid)
    
    if not p_data and cmd != 'desc': 
        await message.channel.send("❌ 먼저 `!가면 [이름]`으로 등록하세요.")
        return

    if cmd == 'desc':
        if not arg:
            await message.channel.send(f"📝 현재 설명: {p_data.get('desc', '없음') if p_data else '등록되지 않음'}")
            return
        domain_manager.update_participant(channel_id, message.author, desc=arg)
        await message.channel.send(f"📝 설명 업데이트: {arg}")
        return

    status_map = {
        'afk': 'Away',
        'leave': 'Left',
        'back': 'Active'
    }
    new_status = status_map.get(cmd)
    
    if new_status:
        domain_manager.update_participant(channel_id, message.author)
        d = domain_manager.get_domain(channel_id)
        if uid in d['participants']:
            d['participants'][uid]['status'] = new_status
            domain_manager.save_domain(channel_id, d)
            
        icon = {'afk': '💤', 'leave': '👋', 'back': '✅'}.get(cmd, '')
        status_kr = {'Away': '자리 비움', 'Left': '퇴장', 'Active': '활동 중'}.get(new_status, new_status)
        await message.channel.send(f"{icon} 상태 변경: **{status_kr}**")


async def handle_system_command(message, channel_id: str, cmd: str, arg: str) -> None:
    """시스템 제어 (!mode, !scene, !lock, !unlock)"""
    if cmd == 'mode':
        if not arg:
            d = domain_manager.get_domain(channel_id)
            curr = d['settings'].get('mode', 'auto')
            mode_kr = {'auto': '자동', 'waiting': '수동', 'manual': '수동', 'assist': '보조'}.get(curr, curr)
            await message.channel.send(f"⚙️ 현재 모드: **{mode_kr}**\n사용법: `!모드 [자동/수동]`")
            return
        
        # Korean to English mapping
        mode_map = {'자동': 'auto', '수동': 'waiting', 'auto': 'auto', 'waiting': 'waiting', 'manual': 'waiting', 'assist': 'assist'}
        mode = mode_map.get(arg.lower(), arg.lower())
        mode_kr = {'auto': '자동', 'waiting': '수동', 'assist': '보조'}.get(mode, mode)
        
        domain_manager.update_settings(channel_id, mode=mode)
        await message.channel.send(f"⚙️ 모드 변경: **{mode_kr}**")
        return

    if cmd == 'scene':
        if not arg:
            d = domain_manager.get_domain(channel_id)
            curr = d['settings'].get('scene_type', 'normal')
            scene_kr = {'normal': '일반', 'gore': '고어', 'nsfw': 'NSFW', 'gore_nsfw': '전체(고어+NSFW)'}.get(curr, curr)
            await message.channel.send(
                f"🎬 현재 장면: **{scene_kr}**\n"
                f"사용법: `!장면 [일반/고어/nsfw/전체]`\n"
                f"• 일반: 기본 묘사\n"
                f"• 고어: 폭력/잔혹 허용\n"
                f"• NSFW: 성인 묘사 허용\n"
                f"• 전체: 모든 묘사 허용"
            )
            return
        
        # Korean to English mapping
        scene_map = {
            '일반': 'normal', 'normal': 'normal', '노말': 'normal',
            '고어': 'gore', 'gore': 'gore',
            'nsfw': 'nsfw', '성인': 'nsfw',
            '전체': 'gore_nsfw', 'all': 'gore_nsfw', 'gore_nsfw': 'gore_nsfw', '올': 'gore_nsfw'
        }
        scene_type = scene_map.get(arg.lower())
        
        if not scene_type:
             await message.channel.send("⚠️ 지원하지 않는 장면 유형입니다.\n`일반`, `고어`, `nsfw`, `전체` 중 선택하세요.")
             return
             
        scene_kr = {'normal': '일반', 'gore': '고어', 'nsfw': 'NSFW', 'gore_nsfw': '전체(고어+NSFW)'}.get(scene_type, scene_type)
        domain_manager.update_settings(channel_id, scene_type=scene_type)
        await message.channel.send(f"🎬 장면 유형 변경: **{scene_kr}**")
        return

    if cmd == 'lock':
        domain_manager.set_session_lock(channel_id, True)
        await message.channel.send("🔒 **세션 잠금**: 외부 개입이 제한됩니다.")
        return

    if cmd == 'unlock':
        domain_manager.set_session_lock(channel_id, False)
        await message.channel.send("🔓 **세션 잠금 해제**: 자유롭게 참여 가능합니다.")
        return

    if cmd == 'bot':
        if not arg:
            curr = domain_manager.get_bot_active(channel_id)
            status = "✅ ON" if curr else "❌ OFF"
            await message.channel.send(f"🤖 봇 상태: **{status}**\n사용법: `!bot [on/off]`")
            return
            
        if arg.lower() in ['on', '켜기', 'true']:
            domain_manager.set_bot_active(channel_id, True)
            await message.channel.send("🤖 **봇 활성화:** ✅ ON")
        elif arg.lower() in ['off', '끄기', 'false']:
            domain_manager.set_bot_active(channel_id, False)
            await message.channel.send("🤖 **봇 비활성화:** ❌ OFF (명령어만 반응)")
        return


async def handle_analysis_command(message, channel_id: str, cmd: str, arg: str, client_genai, model_id) -> None:
    """AI 분석 도구 (!analyze, !consistency, !forecast, !rule, !lores)"""
    
    if cmd == 'rule':
        if not arg:
            w = domain_manager.get_world_state(channel_id)
            rules = w.get("location_rules", {})
            if not rules:
                await message.channel.send("📜 활성화된 특수 규칙이 없습니다.")
            else:
                msg = "📜 **세계 규칙 목록**\n" + "\n".join([f"- {k}: {v.get('desc','')}" for k, v in rules.items()])
                await message.channel.send(msg)
        else:
            await message.channel.send(f"🔍 기밀 규칙 '{arg}' 검색 결과: (보안 등급 필요)")
        return

    if cmd == 'lores':
        # Check for incremental argument "new", "inc"
        incremental = False
        if arg and arg.lower() in ['new', 'inc', '증분', '최신']:
             incremental = True
             
        export_text, msg = game_system.export_chronicle_book(channel_id, incremental=incremental)
        if export_text:
            fname = f"Chronicles_{channel_id}_{'INC' if incremental else 'FULL'}.txt"
            await message.channel.send(msg, file=discord.File(io.StringIO(export_text), filename=fname))
        else:
            await message.channel.send(msg)
        return

    if cmd == 'abnormal' or cmd == '비일상':
        if not arg:
            current = domain_manager.get_abnormal_mode(channel_id)
            status = "✅ ON" if current else "❌ OFF"
            await message.channel.send(f"🧠 **비일상 적응도 시스템**: {status}\n(사용법: `!비일상 on`, `!비일상 off`)")
            return
            
        if arg.lower() in ['on', '켜기', 'true']:
            domain_manager.set_abnormal_mode(channel_id, True)
            await message.channel.send("🧠 **비일상 적응도 시스템**: ✅ 켜짐\n이제 공포/스트레스 요소에 대한 적응도가 추적됩니다.")
        elif arg.lower() in ['off', '끄기', 'false']:
            domain_manager.set_abnormal_mode(channel_id, False)
            await message.channel.send("🧠 **비일상 적응도 시스템**: ❌ 꺼짐")
        return

    domain = domain_manager.get_domain(channel_id)
    history = domain.get('history', [])
    if not history:
        await message.channel.send("⚠️ 분석할 데이터가 부족합니다.")
        return
        
    history_text = "\n".join([f"{h['role']}: {h['content']}" for h in history[-20:]])
    lore_text = domain_manager.get_lore(channel_id)

    msg = await message.channel.send("🔄 **AI 분석 중...** (잠시만 기다려주세요)")
    
    try:
        if cmd == 'analyze': # Brainstorming
            res = await memory_system.analyze_brainstorming(client_genai, model_id, history_text, lore_text, arg or "현재 상황 분석")
            txt = f"🧠 **브레인스토밍**\n\n**상황:** {res.get('current_state_summary')}\n\n**추천:** {res.get('recommendation')}\n\n**가능성:**\n"
            for p in res.get('potential_paths', []):
                 txt += f"- {p.get('path')} ({p.get('pros')})\n"
            await msg.edit(content=txt[:2000])
            
        elif cmd == 'consistency':
            res = await memory_system.check_narrative_consistency(client_genai, model_id, history_text, lore_text)
            txt = f"⚖️ **일관성 검사**\n등급: {res.get('overall_consistency')}\n\n**이슈:**\n"
            for i in res.get('issues', []):
                txt += f"- [{i.get('severity')}] {i.get('description')}\n"
            await msg.edit(content=txt[:2000])
            
        elif cmd == 'forecast':
            res = await memory_system.analyze_brainstorming(client_genai, model_id, history_text, lore_text, "다음 전개 예측")
            txt = f"🔮 **미래 예지**\n{res.get('recommendation')}"
            await msg.edit(content=txt[:2000])

    except Exception as e:
        await msg.edit(content=f"⚠️ 오류 발생: {e}")


async def handle_time_command(message, channel_id: str, arg: str) -> None:
    """
    !시간 [진행/설정/조회]
    !시간 - 현재 시간 조회
    !시간 진행 - 다음 시간대로
    !시간 3 - 3시간대 진행
    !시간 설정 오후 - 특정 시간대로 설정
    """
    world = domain_manager.get_world_state(channel_id)
    
    if not arg:
        # 현재 시간 조회
        time_emoji = {"새벽": "🌅", "오전": "☀️", "오후": "🌤️", "황혼": "🌆", "저녁": "🌙", "심야": "🌑"}
        emoji = time_emoji.get(world.get("time_slot", "오후"), "⏰")
        
        msg = (
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"📅 **{world.get('day', 1)}일차**\n"
            f"{emoji} 시간: **{world.get('time_slot', '오후')}**\n"
            f"🌤️ 날씨: {world.get('weather', '맑음')}\n"
            f"⚠️ 위기: {world.get('doom', 0)}/100\n"
            f"━━━━━━━━━━━━━━━━━━━━"
        )
        await message.channel.send(msg)
        return
    
    if arg == "진행" or arg == "next":
        msg = game_system.advance_time(channel_id)
        await message.channel.send(msg)
        return
    
    if arg.isdigit():
        # N 시간대 진행
        count = int(arg)
        if count > 12:
            await message.channel.send("⚠️ 최대 12시간대까지 진행 가능합니다.")
            return
        
        messages = []
        for _ in range(count):
            messages.append(game_system.advance_time(channel_id))
        await message.channel.send("\n".join(messages))
        return
    
    if arg.startswith("설정 "):
        target = arg[3:].strip()
        time_slots = game_system.get_time_slots(channel_id)
        if target in time_slots:
            world["time_slot"] = target
            domain_manager.update_world_state(channel_id, world)
            await message.channel.send(f"⏰ 시간 설정: **{target}**")
        else:
            await message.channel.send(f"⚠️ 유효한 시간대: {', '.join(time_slots)}")
        return
    
    if arg.startswith("set "):
        target = arg[4:].strip()
        time_slots = game_system.get_time_slots(channel_id)
        # Try english mapping but default Korean
        if target in time_slots:
             world["time_slot"] = target
             domain_manager.update_world_state(channel_id, world)
             await message.channel.send(f"⏰ 시간 설정: **{target}**")
        else:
             await message.channel.send(f"⚠️ 유효한 시간대: {', '.join(time_slots)}")
        return


def classify_ooc_type(ooc_content: str) -> str:
    """OOC 내용 분류"""
    content_lower = ooc_content.lower()
    
    # 수정, 설정 관련 키워드
    edit_keywords = [
        "추가", "삭제", "수정", "설정", "인벤토리", "골드", "돈", "아이템", 
        "획득", "잃음", "관계", "호감도", "패시브", "특성", "제거", "변경"
    ]
    if any(kw in content_lower for kw in edit_keywords):
        return "edit"
    
    # 서사 요청 키워드
    narrative_keywords = ["해줘", "보여줘", "묘사", "장면", "진행", "스킵", "넘어가"]
    if any(kw in content_lower for kw in narrative_keywords):
        return "narrative_request"
    
    return "general"


async def handle_ooc_command(message, channel_id, ooc_content, client_genai, model_id):
    """OOC 요청 처리"""
    if not ooc_content: return None
    
    uid = str(message.author.id)
    
    # OOC 타입 분류
    ooc_type = classify_ooc_type(ooc_content)
    
    if ooc_type == "edit":
        # 데이터 로드
        ai_mem = domain_manager.get_ai_memory(channel_id, uid)
        p_data = domain_manager.get_participant_data(channel_id, uid)
        
        if not p_data:
            await message.channel.send("⚠️ 먼저 세션에 참가해주세요 (`!가면`).")
            return None
            
        await message.channel.send("🔄 **OOC 데이터 수정 중...**")
        
        # AI 처리
        result = await memory_system.process_ooc_memory_edit(
            client_genai, model_id, ooc_content, ai_mem, p_data
        )
        
        if result and result.get("edits"):
            # 수정 적용
            new_mem, new_p_data = memory_system.apply_memory_edits(
                ai_mem, result["edits"], p_data
            )
            
            # 저장
            domain_manager.update_ai_memory(channel_id, uid, new_mem)
            domain_manager.save_participant_data(channel_id, uid, new_p_data)
            
            # 결과 알림
            confirm = result.get('confirmation_message', '수정 완료')
            interp = result.get('interpretation', '')
            
            msg = f"📝 **OOC 처리 완료**\n"
            if interp: msg += f"> *{interp}*\n"
            msg += f"└ {confirm}"
            
            await message.channel.send(msg)
            return None # RP 생성 중단 (필요시 반환값으로 조절)
            
        else:
             await message.channel.send("⚠️ OOC 수정 사항을 인식하지 못했습니다.")
             return None
    
    elif ooc_type == "narrative_request":
        # 서사 지시는 프롬프트에 주입하기 위해 반환
        return f"[OOC Directive: {ooc_content}]"
    
    else:
        # 단순 잡담 (General)
        # 봇이 굳이 반응하지 않거나, 간단히 이모지 반응
        await message.add_reaction("👀")
        return None


async def dispatch_command(cmd, message, channel_id, parsed, client_discord, client_genai, model_id, model_id_flash, domain_data):
    if cmd == 'help':
        help_text = (
            "📚 **로어키퍼 봇 명령어 안내**\n\n"
            
            "**━━ 세션 관리 ━━**\n"
            "`!준비` (`!ready`) - 세션 시작 조건(로어, 룰 등)이 갖춰졌는지 점검합니다.\n"
            "`!시작` (`!start`) - 새로운 세션을 시작하고 오프닝 장면을 생성합니다.\n"
            "`!리셋` (`!reset`) - 현재 세션을 종료하고 모든 진행 데이터를 초기화합니다.\n"
            "`!클리어` (`!clear`) - 채팅 히스토리만 비웁니다. (데이터 유지)\n\n"
            
            "**━━ 내 캐릭터 ━━**\n"
            "`!가면 [이름]` (`!mask`) - 채팅 시 표시될 캐릭터 이름(가면)을 설정합니다.\n"
            "`!설명 [내용]` (`!desc`) - 내 캐릭터의 외모/성격 설명을 등록합니다.\n"
            "`!정보` (`!info`) - 현재 내 캐릭터 상태(인벤토리, 관계, 패시브 등)를 확인합니다.\n"
            "`!잠수` (`!afk`) - 잠시 세션을 떠납니다. (AI가 캐릭터를 조종하지 않음)\n"
            "`!복귀` (`!back`) - 잠수 상태에서 돌아옵니다.\n\n"
            
            "**━━ 진행 ━━**\n"
            "`!진행` (`!next`) - AI에게 다음 장면으로 넘어가라고 지시합니다.\n"
            "`!모드 [자동/수동]` (`!mode`) - AI 응답 모드. 자동=즉시응답, 수동=대기.\n"
            "`!장면 [일반/고어/nsfw/전체]` (`!scene`) - 현재 장면의 묘사 수위 설정.\n"
            "`!주사위` (`!r`) - 1d100 주사위를 굴립니다.\n\n"
            
            "**━━ 세계관 설정 ━━**\n"
            "`!로어` (`!lore`) - 현재 로어 정보를 조회하거나, 내용 입력 시 새 로어를 저장합니다.\n"
            "`!로어 [내용/파일]` - 세계관, 배경, 캐릭터 설정 등을 등록합니다. (.txt 첨부 가능)\n"
            "`!엔피씨 [이름]` (`!npc`) - 특정 NPC의 정보를 조회합니다.\n"
            "`!npc추가 [이름]: [설명]` (`!addnpc`) - 새 NPC를 수동으로 등록합니다. (.txt 첨부 가능)\n"
            "`!룰 [내용]` (`!rule`) - 세계관 고유 규칙을 추가합니다.\n"
            "`!연대기` (`!lores`) - 세션 중 기록된 연대기 목록을 확인합니다.\n\n"
            
            "**━━ 퀘스트 & 메모 ━━**\n"
            "`!퀘스트` (`!quest`) - 현재 진행 중인 퀘스트 목록을 확인합니다.\n"
            "`!퀘스트 [내용]` - 새 퀘스트를 수동으로 추가합니다.\n"
            "`!메모` (`!memo`) - 저장된 메모 목록을 확인합니다.\n"
            "`!메모 [내용]` - 새 메모(단서, 이름, 비밀번호 등)를 추가합니다.\n"
            "`!추출` (`!export`) - 로어, NPC, 퀘스트 데이터를 텍스트 파일로 추출합니다.\n\n"
            
            "**━━ 분석 도구 ━━**\n"
            "`!분석` (`!analyze`) - AI가 현재 상황을 분석하여 객관적 요약을 제공합니다.\n"
            "`!예측` (`!forecast`) - 현재 위기 수치(Doom)와 세계 상태를 예보합니다.\n"
            "`!일관성` (`!consistency`) - 최근 서사의 논리/인과적 일관성을 검사합니다.\n\n"
            
            "**💡 팁:** 대부분의 기능은 AI가 대화 중 자동으로 처리합니다 (퀘스트/메모/NPC 추가 등)."
        )
        await send_long_message(message.channel, help_text)
        return None

    if cmd == 'clear':
        await session_manager.manager.execute_clear(message)
        return None
        
    if cmd == 'reset':
        await session_manager.manager.execute_reset(message, client_discord)
        return None
    if cmd == 'ready':
        await session_manager.manager.check_preparation(message)
        return None
    if cmd == 'start':
        domain_manager.update_participant(channel_id, message.author)
        if await session_manager.manager.start_session(message, client_genai, model_id):
            return "[System: 오프닝 장면 생성]"
        return None
        
    if cmd == 'lore':
        await handle_lore_command(message, channel_id, parsed['content'], client_genai, model_id)
        return None
        
    if cmd == 'info':
        await handle_info_command(message, channel_id, parsed['content'])
        return None
        
    if cmd == 'mask':
        target = parsed['content']
        domain_manager.update_participant(channel_id, message.author)
        domain_manager.set_user_mask(channel_id, message.author.id, target)
        
        # Link PC info
        pc = domain_manager.get_default_pc_info(channel_id)
        if pc and (target in pc.get("name", "") or pc.get("name", "") in target):
             if domain_manager.apply_pc_info_to_user(channel_id, message.author.id):
                 await message.channel.send(f"🎭 가면: {target} (PC 정보 적용됨)")
                 return None
                 
        await message.channel.send(f"🎭 가면: {target}")
        return None

    if cmd == 'r' or cmd == 'dice' or cmd == '주사위' or cmd == '판정': 
        # Advanced roll with modifiers
        action_desc = parsed['content'] if parsed else ""
        result_msg = game_system.perform_check(channel_id, str(message.author.id), action_desc)
        await message.channel.send(result_msg)
        
        # Log to history and trigger AI narrative
        domain_manager.append_history(channel_id, "System", result_msg)
        return f"[System: Dice Roll Result - {action_desc if action_desc else 'Standard Check'}] {result_msg}"
        
    if cmd == 'doom':
        await message.channel.send(game_system.get_doom_forecast(channel_id))
        return None

    # Quest/Memo Direct
    # Quest Command
    if cmd == 'quest':
        arg = parsed['content']
        if not arg:
            await send_long_message(message.channel, game_system.get_active_quests_text(channel_id))
            return None
            
        # Parse Subcommands
        subCmd = arg.split()[0].lower()
        content = arg[len(subCmd):].strip()
        
        if subCmd in ['remove', 'delete', '삭제', '제거', '취소']:
            if not content: await message.channel.send("⚠️ 삭제할 퀘스트 내용을 입력하세요.")
            else: await message.channel.send(game_system.remove_quest(channel_id, content))
            
        elif subCmd in ['complete', 'finish', 'done', '완료', '달성']:
            if not content: await message.channel.send("⚠️ 완료한 퀘스트 내용을 입력하세요.")
            else: await message.channel.send(game_system.complete_quest(channel_id, content))
            
        else:
            # Default: Add
            await message.channel.send(game_system.add_quest(channel_id, arg))
        return None

    # Memo Command
    if cmd == 'memo':
        arg = parsed['content']
        if not arg:
            await send_long_message(message.channel, game_system.get_memos_text(channel_id))
            return None
            
        # Parse Subcommands
        subCmd = arg.split()[0].lower()
        content = arg[len(subCmd):].strip()
        
        if subCmd in ['remove', 'delete', '삭제', '제거']:
            if not content: await message.channel.send("⚠️ 삭제할 메모 내용을 입력하세요.")
            else: await message.channel.send(game_system.remove_memo(channel_id, content))
            
        elif subCmd in ['archive', 'complete', 'done', '보관', '해결', '완료']:
            if not content: await message.channel.send("⚠️ 보관할 메모 내용을 입력하세요.")
            else: await message.channel.send(game_system.resolve_memo_auto(channel_id, content))
            
        else:
            # Default: Add
            await message.channel.send(game_system.add_memo(channel_id, arg))
        return None

    if cmd in ['next', 'turn']:
        await message.add_reaction("🎬")
        return "[System: Proceed to next scene.]"

    # OOC Processing
    if parsed and parsed.get('type') == 'ooc':
        return await handle_ooc_command(
            message, channel_id, parsed['content'],
            client_genai, model_id
        )
    
    if parsed and parsed.get('type') == 'chat_with_ooc':
        # OOC 먼저 처리
        ooc_result = await handle_ooc_command(
            message, channel_id, parsed['ooc_content'],
            client_genai, model_id
        )
        
        # 만약 OOC가 서사 지시(string 반환)라면 RP 생성 프롬프트에 추가될 수 있도록 반환
        # (현재 구조상 dispatch_command의 반환값은 즉시 출력 메시지 용도임)
        # 따라서 RP 생성 흐름(process_message)에서 이를 받아야 함.
        # 일단은 OOC 처리가 끝났고 RP 컨텐츠가 있다면 None을 반환하여 RP 흐름을 타게 함.
        
        if ooc_result and isinstance(ooc_result, str) and not ooc_result.startswith("[OOC Directive"):
             # 메시지가 반환되었다면 출력 (시스템 메시지 등)
             return ooc_result
             
        # RP 컨텐츠가 있으면 계속 진행 (None 반환)
        if parsed.get('chat_content'):
            # OOC 지시사항이 있다면 이를 어딘가에 저장하거나 전달해야 하는데,
            # 현재 구조에서는 dispatch가 RP 생성을 직접 호출하지 않음.
            # main.py에서 dispatch의 반환값이 None이면 generate_ai_response를 호출함.
            # 하지만 OOC 지시사항을 넘길 방법이 모호함.
            # 임시 해결: OOC 지시사항은 handle_ooc_command 내부에서 처리되거나,
            # main.py 수정이 필요할 수 있음.
            # 여기서는 "edit" 타입은 처리 후 종료, "narrative"는 무시(RP에 반영되길 기대)로 처리.
            return None 
            
        return ooc_result

    # --- [NEW] Dispatch to Handlers ---
    
    # NPC dispatch
    if cmd in ['npc', 'addnpc']:
        await handle_npc_command(message, channel_id, cmd, parsed['content'], client_genai, model_id)
        return None

    # Participant dispatch
    if cmd in ['desc', 'afk', 'leave', 'back']:
        await handle_participant_command(message, channel_id, cmd, parsed['content'])
        return None

    # System dispatch
    if cmd in ['mode', 'scene', 'lock', 'unlock', 'bot']:
        await handle_system_command(message, channel_id, cmd, parsed['content'])
        return None

    # Analysis/Tools dispatch
    if cmd in ['analyze', 'consistency', 'forecast', 'rule', 'lores', 'abnormal', '비일상']:
        await handle_analysis_command(message, channel_id, cmd, parsed['content'], client_genai, model_id)
        return None

    # Time dispatch
    if cmd in ['time', '시간', 'time_adv', '시간진행']:
        await handle_time_command(message, channel_id, parsed['content'])
        return None

    # [NEW] Rest Command (Reduces Doom by risk level)
    if cmd == "rest" or cmd == "휴식":
        # Calculate reduction based on current location/risk
        w_state = domain_manager.get_world_state(channel_id)
        risk = w_state.get("risk_level", "medium")
        
        reduction = 5 # Default
        if risk == "low": reduction = 15
        elif risk == "high": reduction = 2
        
        msg = game_world.reduce_doom(channel_id, reduction, "Rest")
        return f"⛺ **휴식을 취합니다.**\n{msg}"

    # [NEW] NPC Reset (Clear Session NPCs)
    if cmd == "reset_npcs":
        count = npc_manager.clear_session_npcs(channel_id)
        return f"🧹 **세션 NPC 초기화 완료:** {count}명 삭제됨 (Lore NPC 유지)"

    return None
