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

logger = logging.getLogger(__name__)

# Unified Modules
import config
import domain_manager
import game_system
import game_world
import game_character
import npc_manager
# session_manager and memory_system are still external for now, or integrated?
# Plan said session_manager is modified to import domain_manager directly.
# memory_system seems to be next or treated separately. I will assume memory_system exists.
import session_manager
import memory_system 
from bot_utils import send_long_message, read_attachment_text, safe_delete_message
import config  # SUPPORTED_TEXT_EXTENSIONS 등은 config에서 직접 사용

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
        elif atype == "Complete":
            # [V6.2] Item 2: Quest Completion reduces Doom
            auto_msg = game_system.complete_quest(channel_id, content)
            game_world.change_doom(channel_id, -5) # Tension release
            
    elif tool == "NPC" and atype == "Add":
        name = content.split(":", 1)[0].strip() if ":" in content else content
        desc = content.split(":", 1)[1].strip() if ":" in content else "Auto Registered"
        domain_manager.update_npc(channel_id, name, {"desc": desc, "source": "session", "status": "Active"})
        auto_msg = f"🎭 NPC: {name}"

    elif tool == "Doom" and atype == "Reduce":
        # [V6.2] Item 4: AI can explicitly request Doom reduction (e.g. via item use)
        try:
            amt = int(content)
        except (ValueError, TypeError):
            logger.debug(f"[무시됨] Doom 감소량 파싱 실패, 기본값(3) 사용: {content}")
            amt = 3
        game_world.change_doom(channel_id, -amt)
        auto_msg = f"📉 긴급 안정화 ({amt}%)"
        
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
             await message.channel.send(f"⚠️ 확장자 오류. 지원: {', '.join(config.SUPPORTED_TEXT_EXTENSIONS)}")
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
            # 1. NPCs (via npc_manager - 책임 명확화)
            # 2. PC Info
            npc_task = npc_manager.extract_npcs_from_lore(client_genai, model_id, full)
            pc_task = memory_system.extract_pc_info(client_genai, model_id, full)

            npcs, pc_info = await asyncio.gather(npc_task, pc_task)

            # Update NPCs (npc_manager가 소스 타입 자동 설정)
            npc_manager.add_lore_npcs(channel_id, npcs)
                
            # Update PC Info
            pc_msg = ""
            if pc_info and pc_info.get("name"):
                 # Save as default PC info for !mask to pick up
                 domain_manager.set_default_pc_info(channel_id, pc_info)
                 pc_msg = f"\n주인공 식별: {pc_info.get('name')} (가면 설정 시 자동 적용)"
                 
                 # [Anti-Gravity Fix] Auto-apply to existing participants with matching name
                 d = domain_manager.get_domain(channel_id)
                 updated_players = []
                 for uid, p_data in d.get("participants", {}).items():
                     if p_data.get("mask", "").lower() == pc_info.get("name").lower():
                         # Merge passives
                         new_passives = pc_info.get("passives", [])
                         if new_passives:
                             # Ensure ai_memory exists
                             if "ai_memory" not in p_data: p_data["ai_memory"] = {}
                             if "passives" not in p_data["ai_memory"]: p_data["ai_memory"]["passives"] = []
                             
                             # Append non-duplicate passives
                             current_names = [p['name'] if isinstance(p, dict) else str(p) for p in p_data["ai_memory"]["passives"]]
                             for np in new_passives:
                                 # Standardize to Dict
                                 np_obj = np if isinstance(np, dict) else {"name": str(np), "modifier": 0, "desc": "Extracted"}
                                 name_key = np_obj.get("name", "Unknown")
                                 
                                 if name_key not in current_names:
                                     p_data["ai_memory"]["passives"].append({
                                         "name": name_key,
                                         "tags": ["Lore", "+Auto"],
                                         "modifier": np_obj.get("modifier", 0), # Store the hidden stat
                                         "desc": np_obj.get("desc", "Extracted from Lore"),
                                         "acquired_at": time.strftime('%Y-%m-%d')
                                     })
                             
                             updated_players.append(pc_info.get("name"))
                             domain_manager.save_participant_data(channel_id, uid, p_data)
                 
                 if updated_players:
                     pc_msg += f"\n✅ 캐릭터 업데이트: {', '.join(updated_players)} (패시브 적용, Hidden Stat 포함)"
            
            domain_manager.append_lore(channel_id, full) 
            
            # Genre Analysis (3-Layer)
            res = await memory_system.analyze_genre_layers(client_genai, model_id, full)
            # Store the FULL structure so persona.py can use it
            domain_manager.set_active_genres(channel_id, res) 
            domain_manager.set_custom_tone(channel_id, res.get("atmosphere_guide"))
            
            # [NEW] Event Lore Summarization
            event_summary = await memory_system.summarize_lore_for_events(client_genai, model_id, full)
            domain_manager.set_event_lore_summary(channel_id, event_summary)
            
            # Formatted Output
            layers = res.get("layers", {})
            genre_summary = f"{layers.get('world_setting', '?')} / {layers.get('style_tech', '?')} / {layers.get('narrative_tone', '?')}"
            
            await msg.edit(content=f"✅ **로어 분석 완료**\nNPC: {len(npcs)}명 추출{pc_msg}\n장르(3계층): {genre_summary}\n이벤트 요약: 생성됨 ({len(event_summary)}자)")
        except Exception as e:
            await msg.edit(content=f"⚠️ 분석 오류: {e}")
    else:
        domain_manager.append_lore(channel_id, full)
        # Attempt to save a naive summary if AI not available
        domain_manager.set_event_lore_summary(channel_id, full[:1000])
        await msg.edit(content="📜 저장 완료 (AI 미사용 - 단순 요약)")


async def handle_info_command(message, channel_id: str, sub_command: str = "") -> None:
    uid = str(message.author.id)
    if not domain_manager.get_participant_data(channel_id, uid):
        await message.channel.send("❌ 등록 필요 (`!가면`)")
        return

    res = domain_manager.get_unified_player_info(channel_id, uid)
    
    # Append Quest & Notebook Info
    quests = game_system.get_active_quests(channel_id)
    notebook = game_system.get_notebook_text(channel_id)
    
    # [NEW] Append Mental & Adaptation Info
    p_data = domain_manager.get_participant_data(channel_id, uid)
    if p_data:
        # Mental
        m_stage = p_data.get("mental_stage", 0)
        m_info = game_character.MENTAL_STAGES.get(m_stage, {"name": "??", "emoji": "❓", "desc": ""})
        res += f"\n**🧠 멘탈:** {m_info['emoji']} {m_info['name']} (Lv.{m_stage})\n"
        
        # Adaptation
        exposure = p_data.get("abnormal_exposure", {})
        if exposure:
            res += "**🦠 적응도:**\n"
            for tag, data in exposure.items():
                count = data.get("count", 0)
                pct = game_character.calculate_adaptation_percentage(count)
                res += f"• [{tag}]: {pct}%\n"

    if quests:
        res += "\n**🛡️ 진행 중인 퀘스트:**\n" + "\n".join([f"- {q}" for q in quests]) + "\n"
    
    if notebook:
        res += f"\n**📔 노트북:**\n{notebook}\n"

    await send_long_message(message.channel, res)

async def handle_notebook_command(message, channel_id: str, arg: str) -> None:
    """노트북 관리 명령어 (!노트북 [추가/수정/삭제/마이그레이션])"""
    if not arg:
        text = game_system.get_notebook_text(channel_id)
        await send_long_message(message.channel, f"📔 **현재 노트북 내용:**\n\n{text}")
        return

    # sub_command parsing
    parts = arg.split(None, 1)
    sub = parts[0]
    content = parts[1] if len(parts) > 1 else ""

    if sub in ['추가', 'add']:
        curr = game_system.get_notebook_text(channel_id)
        new_text = f"{curr}\n- {content}"
        game_system.update_notebook_text(channel_id, new_text)
        await message.channel.send("✅ 노트북에 내용이 추가되었습니다.")
        
    elif sub in ['수정', 'edit', 'set']:
        # Check for specific edit syntax: "old -> new"
        if "->" in content:
            old_val, new_val = content.split("->", 1)
            await message.channel.send(game_system.edit_memo(channel_id, old_val.strip(), new_val.strip()))
        else:
            # Fallback: Replace All
            game_system.update_notebook_text(channel_id, content)
            await message.channel.send("✅ 노트북 내용이 전체 수정되었습니다. (부분 수정은 `구형 -> 신형` 형식 사용)")
        
    elif sub in ['삭제', 'del', 'remove']:
        curr = game_system.get_notebook_text(channel_id)
        if content in curr:
            new_text = curr.replace(content, "").replace("\n\n\n", "\n\n").strip()
            game_system.update_notebook_text(channel_id, new_text)
            await message.channel.send(f"🗑️ 노트북에서 '{content[:20]}...' 내용을 삭제했습니다.")
        else:
            await message.channel.send("⚠️ 삭제할 내용을 찾을 수 없습니다. (정확히 일치해야 합니다)")
            
    else:
        # Default: Add if no sub-command recognized but content exists
        curr = game_system.get_notebook_text(channel_id)
        new_text = f"{curr}\n- {arg}"
        game_system.update_notebook_text(channel_id, new_text)
        await message.channel.send("✅ 노트북에 내용이 기록되었습니다.")



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

    if cmd in ['reset', 'clear', '클리어', '초기화']:
        if not arg or arg.lower() not in ['confirm', '확인']:
            await message.channel.send(
                "⚠️ **세션 초기화 경고**\n"
                "모든 진행 상황(히스토리, 시간, 날씨, 퀘스트, 아이템)이 초기화됩니다.\n"
                "(단, 로어와 참가자 명단은 유지됩니다)\n\n"
                "진행하시려면: `!초기화 확인` 또는 `!reset confirm`을 입력하세요."
            )
            return
            
        domain_manager.reset_session_state(channel_id)
        
        # Reset Complete Message
        await message.channel.send(
            "♻️ **세션이 초기화되었습니다.**\n"
            "• **유지**: 로어북, 룰, 참가자 설정\n"
            "• **삭제**: 대화 내역, 퀘스트, 아이템(노트북), NPC(세션), 월드 상태(1일차로 복귀)\n\n"
            "이제 새로운 마음으로 **!스타트** 또는 바로 롤플레잉을 시작하실 수 있습니다."
        )
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
        "획득", "잃음", "관계", "호감도", "패시브", "특성", "제거", "변경",
        "합쳐", "정리", "통합"
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
        
        # [V5.3] Notebook Integration
        notebook_txt = game_system.get_notebook_text(channel_id)
        
        # AI 처리
        result = await memory_system.process_ooc_memory_edit(
            client_genai, model_id, ooc_content, ai_mem, p_data, notebook_text=notebook_txt
        )
        
        if result and result.get("edits"):
            # 1. Separate Notebook Edits vs Memory Edits
            mem_edits = []
            
            for edit in result["edits"]:
                field = edit.get("field")
                action = edit.get("action")
                value = edit.get("value")
                
                # Notebook Handling
                if field in ["notebook", "notes", "note"]:
                    if action == "append":
                        game_system.add_memo(channel_id, value) # add_memo appends line
                    elif action == "replace" or action == "set":
                         # Dangerous but allowed
                         game_system.update_notebook_text(channel_id, value)
                    continue # handled
                    
                mem_edits.append(edit)
            
            # 2. Apply Memory Edits
            if mem_edits:
                new_mem, new_p_data = memory_system.apply_memory_edits(
                    ai_mem, mem_edits, p_data
                )
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
            "━━━━━━━━━━━━━━━━━━━━\n"
            "📜 **Lorekeeper Bot V6 명령어 (통합)**\n"
            "━━━━━━━━━━━━━━━━━━━━\n\n"
            
            "**━━ 캐릭터 & 멘탈 ━━**\n"
            "`!가면 [이름]` (`!mask`) - 캐릭터(NPC/PC)로 변신합니다. (자동 프로필 연결)\n"
            "`!정보` (`!me`, `!desc`) - 자신의 캐릭터 정보(멘탈, 상태, 패시브)를 확인합니다.\n"
            "`!멘탈 [이름] [확인/설정 X]` - 멘탈 상태를 확인하거나 강제로 조정합니다. (GM)\n"
            "`!칭호 [이름] [추가/제거] [칭호명]` - 캐릭터에게 칭호를 부여하거나 박탈합니다.\n"
            "`!휴식` (`!rest`) - 휴식을 취하여 위기(Doom)를 낮춥니다. (장소 위험도에 따라 다름)\n\n"
            
            "**━━ 월드 & 시스템 ━━**\n"
            "`!시간 [진행/N/설정]` - 시간을 흐르게 하거나 강제로 설정합니다.\n"
            "`!둠` (`!doom`) - 현재 위기 수치를 확인하거나 조정합니다. (`!둠 10`, `!둠 set 50`)\n"
            "`!모드 [자동/수동]` - AI 응답 모드를 변경합니다.\n"
            "`!장면 [일반/고어/NSFW]` - 장면 수위를 설정합니다.\n"
            "`!잠금` (`!lock`) / `!해제` (`!unlock`) - 세션 참여를 제한하거나 풉니다.\n"
            "`!엔피씨 초기화` (`!reset_npcs`) - 세션에서 생성된 임시 NPC를 삭제합니다.\n"
            "`!비일상 [on/off]` - 비일상 적응도 시스템을 켜거나 끕니다.\n\n"
            
            "**━━ 데이터 & 기록 ━━**\n"
            "`!노트북` (`!memo`) - 인벤토리와 메모를 통합 관리합니다. (아이템/단서 확인)\n"
            "`!노트북 [내용]` - 새 메모나 아이템을 추가합니다.\n"
            "`!로어 [내용/파일]` - 세계관 정보를 조회하거나 추가합니다.\n"
            "`!퀘스트` - 퀘스트 목록을 확인하거나 관리합니다.\n"
            "`!연대기` (`!lores`) - 작성된 연대기(소설)를 TXT 파일로 저장합니다. (증분 지원)\n"
            "`!추출` (`!export`) - 전체 대화 내역(로그)을 TXT 파일로 저장합니다. (증분 지원)\n\n"
            
            "**━━ 세션 관리 ━━**\n"
            "`!준비` (`!ready`) - 세션 시작 전 준비 상태를 점검합니다.\n"
            "`!시작` (`!start`) - 오프닝을 생성하고 세션을 시작합니다.\n"
            "`!리셋` (`!reset`) - **[주의]** 모든 데이터를 초기화하고 세션을 종료합니다.\n"
            "`!클리어` (`!clear`) - 화면의 채팅 내역만 지웁니다. (데이터 유지)\n\n"
            
            "**━━ 주사위 & 판정 ━━**\n"
            "`!판정 [행동]` (`!r`) - 주사위(1d100)를 굴립니다. (상태이상/패시브/위기 반영)\n"
            "`!분석` (`!analyze`) - 현재 상황을 AI가 객관적으로 분석합니다.\n"
            
            "**💡 팁:** 대부분의 기능은 AI가 대화 중 자동으로 처리합니다."
        )
        await send_long_message(message.channel, help_text)
        return None

    # [NEW] Export Command (Session History Default)
    if cmd in ['export', '추출']:
        # Args: [lore/chat] [new/inc]
        arg_lower = parsed['content'].lower() if parsed['content'] else ""
        
        # 1. Lore Export Override
        if "lore" in arg_lower or "로어" in arg_lower:
            content, msg = game_character.export_lore_data(channel_id)
            fname = f"LoreData_{channel_id}.txt"
            
        # 2. Chat History Export (Default)
        else:
            # Check Incremental
            is_inc = any(x in arg_lower for x in ['new', 'inc', '증분', '최신'])
            content, msg = game_character.export_session_history(channel_id, incremental=is_inc)
            mode_str = "INC" if is_inc else "FULL"
            fname = f"SessionLog_{channel_id}_{mode_str}.txt"

        if content:
            await message.channel.send(msg, file=discord.File(io.StringIO(content), filename=fname))
        else:
            await message.channel.send(msg)
        return None

    if cmd in ['clear', '클리어', '청소']:
        await session_manager.manager.execute_clear(message)
        return None
        
    if cmd in ['reset', '리셋', '초기화']:
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

    # Quest/Notebook Command
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

    # Notebook Command (Unified Inventory/Memos)
    if cmd in ['notebook', '노트북', 'note', 'n', 'memo', '메모', 'inven', '인벤', 'i']:
        await handle_notebook_command(message, channel_id, parsed['content'])
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

    # [NEW] Manual Doom Control
    if cmd == "doom" or cmd == "둠" or cmd == "위기":
        args = parsed['content'].split() if parsed['content'] else []
        if not args:
            # Query
            await message.channel.send(game_world.get_doom_forecast(channel_id))
        else:
            # Set/Mod
            # !doom set 50
            # !doom +10
            # !doom -5
            op = args[0]
            val = 0
            
            if op.lower() == "set":
                if len(args) < 2: return "⚠️ 값을 입력하세요 (예: `!doom set 50`)"
                try:
                    val = int(args[1])
                    game_world.change_doom(channel_id, 0) # Clear existing? No, direct set needed.
                    # Direct Set Helper
                    w = domain_manager.get_world_state(channel_id)
                    old_v = w.get("doom", 0)
                    w["doom"] = max(0, min(100, val))
                    domain_manager.update_world_state(channel_id, w)
                    await message.channel.send(f"🛡️ **위기 수치 재설정:** {old_v}% → {val}%")
                except ValueError: return "⚠️ 올바른 숫자가 아닙니다."
            else:
                # Assuming operator-like syntax within arg check, but usually users type "!doom 10" or "!doom -10"
                # Let's handle generic inputs
                try:
                    val = int(op)
                    res = game_world.change_doom(channel_id, val)
                    await message.channel.send(res)
                except (ValueError, TypeError):
                    return "⚠️ 사용법: `!doom 10` (증가/감소), `!doom set 50` (설정)"
        return None

    # [NEW] Manual Mental Control
    if cmd == "mental" or cmd == "멘탈":
        # Usage: 
        # !mental (Check Self)
        # !mental <Target> (Check Target)
        # !mental <State> (Set Self to State)
        # !mental <Target> <State> (Set Target to State)
        # !mental <Target> <set> <State> (Explicit Set)
        
        args = parsed['content'].split() if parsed['content'] else []
        uid = str(message.author.id)
        
        target_uid = None
        target_name = None
        subcmd = "check"
        val_arg = None
        
        # Helper: Build State Map
        state_map = {}
        for k, v in game_character.MENTAL_STAGES.items():
            state_map[v["name"]] = k
            state_map[str(k)] = k
            
        # 1. Determine Target
        if not args:
            # !mental -> Check Self
            target_uid = uid
        else:
            first = args[0]
            found_uid = domain_manager.find_participant_id_by_name(channel_id, first)
            
            if found_uid:
                # Arg0 is User
                target_uid = found_uid
                target_name = first
                
                # Check next args for Set/Check
                if len(args) > 1:
                    second = args[1]
                    if second in ["set", "설정"]:
                        subcmd = "set"
                        if len(args) > 2: val_arg = args[2]
                    elif second in ["check", "확인"]:
                        subcmd = "check"
                    elif second in state_map:
                        # !mental User PyungJeong
                        subcmd = "set"
                        val_arg = second
            else:
                # Arg0 is NOT User -> Assume Self Target and Arg0 is State/Cmd
                target_uid = uid
                if first in ["check", "확인"]:
                    subcmd = "check"
                elif first in state_map:
                    subcmd = "set"
                    val_arg = first
                    # Handle "!mental 평정 확인" case -> Confirm/Check isn't strictly used as 'Set', but user intent is likely Set.
                    # Or valid arg parsing for safety.
                elif first in ["set", "설정"]:
                    subcmd = "set"
                    if len(args) > 1: val_arg = args[1]
                else:
                    return f"⚠️ 참가자 '{first}'을(를) 찾을 수 없거나, 올바른 상태명이 아닙니다."

        # Verify Target
        p_data = domain_manager.get_participant_data(channel_id, target_uid)
        if not p_data:
             return "❌ 등록된 캐릭터가 없습니다. (`!가면`)"
        target_name = p_data.get("mask", "Unknown")

        # Execute
        if subcmd == "check":
            ms = game_character.get_mental_status_text(p_data)
            await message.channel.send(f"🧠 **{target_name}님의 멘탈:** {ms}")
            
        elif subcmd == "set":
            if val_arg is None: return "⚠️ 설정할 단계(0-3)나 상태명(평정 등)을 입력하세요."
            
            if val_arg not in state_map:
                 return f"⚠️ 올바른 상태명이 아닙니다. ({', '.join(game_character.MENTAL_STAGES[i]['name'] for i in range(4))})"
            
            new_stage = state_map[val_arg]
            p_data["mental_stage"] = new_stage
            domain_manager.save_participant_data(channel_id, target_uid, p_data)
            
            ms = game_character.get_mental_status_text(p_data)
            await message.channel.send(f"🧠 **{target_name}** 멘탈 조정 완료: {ms}")
            
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

    # [NEW] Title System
    if cmd in ['title', '칭호']:
        await handle_title_command(message, channel_id, parsed['content'])
        return None

    return None

async def handle_title_command(message, channel_id: str, arg: str) -> None:
    """칭호 관리 명령어 (!칭호 [대상] [동작] [칭호명])"""
    # Ex: !칭호 리라 추가 용사냥꾼
    # Ex: !칭호 리라 제거 용사냥꾼
    
    if not arg:
        await message.channel.send("⚠️ 사용법: `!칭호 [캐릭터] [추가/제거] [칭호명]`")
        return

    parts = arg.split(None, 2)
    if len(parts) < 3:
        await message.channel.send("⚠️ 인자가 부족합니다. (예: `!칭호 리라 추가 용사냥꾼`)")
        return

    target_name = parts[0]
    action = parts[1]
    title_name = parts[2]

    # 1. Find Participant
    d = domain_manager.get_domain(channel_id)
    target_uid = None
    target_p = None
    
    for uid, p in d.get("participants", {}).items():
        if p.get("mask", "").lower() == target_name.lower():
            target_uid = uid
            target_p = p
            break
            
    if not target_p:
        await message.channel.send(f"⚠️ 캐릭터 '{target_name}'를 찾을 수 없습니다.")
        return

    # 2. Add/Remove Title (as Passive)
    mem = target_p.get("ai_memory", {})
    if "passives" not in mem: mem["passives"] = []
    
    current_passives = mem["passives"]
    
    if action in ['추가', 'add']:
        # Check duplicate
        exists = False
        for p in current_passives:
            p_name = p.get("name") if isinstance(p, dict) else str(p)
            if p_name == title_name:
                exists = True
                break
        
        if exists:
            await message.channel.send(f"⚠️ '{title_name}' 칭호(특성)를 이미 보유하고 있습니다.")
            return
            
        # Add new Title Passive
        new_passive = {
            "name": title_name,
            "tags": ["Title", "Lore"], # Explicit Title Tag
            "modifier": 0, # No mechanical impact
            "desc": "칭호",
            "acquired_at": time.strftime('%Y-%m-%d')
        }
        current_passives.append(new_passive)
        domain_manager.save_participant_data(channel_id, target_uid, target_p)
        await message.channel.send(f"🏆 **칭호 수여:** [{title_name}] -> {target_name}")

    elif action in ['제거', 'remove', 'del']:
        # Find and remove
        found_idx = -1
        for i, p in enumerate(current_passives):
            p_name = p.get("name") if isinstance(p, dict) else str(p)
            if p_name == title_name:
                found_idx = i
                break
        
        if found_idx != -1:
            removed = current_passives.pop(found_idx)
            domain_manager.save_participant_data(channel_id, target_uid, target_p)
            r_name = removed.get("name") if isinstance(removed, dict) else str(removed)
            await message.channel.send(f"🗑️ **칭호 박탈:** [{r_name}] <- {target_name}")
        else:
            await message.channel.send(f"⚠️ '{title_name}' 칭호를 보유하고 있지 않습니다.")
            
    else:
         await message.channel.send("⚠️ 알 수 없는 동작입니다. (`추가`, `제거` 중 선택)")
