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
from typing import Optional, Dict, Any
import re

logger = logging.getLogger(__name__)

# Unified Modules
import config
import domain_manager
import game_system
import game_world
import game_character
import npc_manager
import cognition
# session_manager and memory_system are still external for now, or integrated?
# Plan said session_manager is modified to import domain_manager directly.
# memory_system seems to be next or treated separately. I will assume memory_system exists.
import session_manager
import memory_system 
from bot_utils import send_long_message, read_attachment_text, safe_delete_message
from command_registry import CommandRegistry, CommandContext

# Registry Instance
registry = CommandRegistry()

# =========================================================
# SYSTEM HANDLER LOGIC (Absorbed)
# =========================================================
async def process_ai_system_action(channel_id: str, sys_action: Dict[str, Any], user_id: str = "") -> Optional[str]:
    """AI가 제안한 시스템 액션을 처리합니다."""
    if not sys_action or not isinstance(sys_action, dict): return None

    tool = sys_action.get("tool")
    atype = sys_action.get("type")
    content = sys_action.get("content")

    if not all([tool, atype, content]): return None

    auto_msg = None
    if tool == "Memo":
        if atype == "Add": auto_msg = game_system.add_memo(channel_id, content, user_id)
        elif atype == "Remove": auto_msg = game_system.remove_memo(channel_id, content, user_id)
        elif atype == "Archive": auto_msg = game_system.resolve_memo_auto(channel_id, content, user_id)
        
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
# System Commands & Registry
# =========================================================

# Registry logic and dispatch (Restored for Health Check compatibility)
async def handle_participant_command(ctx: CommandContext) -> bool:
    """Legacy entry point, now uses registry dispatch."""
    return await registry.dispatch(ctx)

@registry.register("lore", category="World", aliases=["로어", "lore"], description="세계관 정보 조회 및 수정")
async def cmd_lore(ctx: CommandContext) -> None:
    """!로어 [내용/파일] or !로어 초기화"""
    arg = ctx.raw_args.strip()
    
    # Check File
    file_text = ""
    if ctx.message.attachments:
        for att in ctx.message.attachments:
            text, error = await read_attachment_text(att)
            if error:
                 await ctx.send(error)
                 return
            if text:
                 file_text = text
                 break
                 
    full_content = (arg + "\n" + file_text).strip()
    channel_id = ctx.channel_id
    
    # 1. View (No Content)
    if not full_content:
        lore = domain_manager.get_lore(channel_id)
        npcs = domain_manager.get_npcs(channel_id)
        
        if lore == config.DEFAULT_LORE:
             await ctx.send("📜 로어 없음. `!로어 [내용]` 입력.")
             return
             
        genres = domain_manager.get_active_genres(channel_id)
        tone = domain_manager.get_custom_tone(channel_id)
        
        msg = f"📜 **로어 정보**\nLength: {len(lore):,} chars\nNPCs: {len(npcs)}명\nGenres: {', '.join(genres)}"
        if tone: msg += f"\nTone: {tone}"
        
        # [MODIFIED] Show ALL NPCs (Name Only)
        if npcs:
            names = [f"`{n}`" for n in npcs.keys()]
            msg += f"\n\n👥 **식별된 NPC 목록 ({len(npcs)}명):**\n" + ", ".join(names)
            
        await send_long_message(ctx.message.channel, msg)
        # await send_long_message(ctx.message.channel, f"📄 **Lore Preview:**\n```\n{lore[:500]}...\n```")
        return

    # 2. Reset
    if full_content == "초기화":
        domain_manager.reset_lore(channel_id)
        await ctx.send("📜 **로어 초기화됨**")
        return
        
    # 3. Export
    if full_content.lower() in ['추출', 'export']:
        # Check for incremental argument
        incremental = False
        args = full_content.split()
        if len(args) > 1 and args[1].lower() in ['new', 'inc', '증분', '최신']:
            incremental = True
            
        export_text, msg = game_system.export_session_history(channel_id, incremental=incremental)
        if export_text:
             fname = f"SessionHistory_{channel_id}_{'INC' if incremental else 'FULL'}.txt"
             await ctx.send(msg, file=discord.File(io.StringIO(export_text), filename=fname))
        else:
             await ctx.send(msg)   
        return
        
    # 4. Update (Append & Analyze)
    domain_manager.save_lore_original(channel_id, full_content)
    msg = await ctx.message.channel.send("📜 **로어 저장됨**. AI 분석 중...")
    
    if ctx.genai_client:
        try:
            # [LoreAnalyzer V1] Unified Analysis
            unified_res = await cognition.analyze_lore_unified(ctx.genai_client, ctx.model_id, full_content)
            
            extracted_npcs = unified_res.get("npcs", [])
            pc_info = unified_res.get("pc_info")
            genre_res = unified_res.get("genres", {})
            lore_summary_data = unified_res.get("lore_summary", {})
            
            # 1. Update NPCs
            if extracted_npcs:
                npc_manager.add_lore_npcs(channel_id, extracted_npcs)
                
            # 2. Update PC Info
            pc_msg = ""
            if pc_info and pc_info.get("name"):
                 # Save as default PC info for !mask to pick up
                 domain_manager.set_default_pc_info(channel_id, pc_info)
                 pc_msg = f"\n주인공 식별: {pc_info.get('name')} (가면 설정 시 자동 적용)"
                 
                 # Auto-apply to existing participants with matching name
                 updated_uids = domain_manager.sync_matching_participants(channel_id, pc_info)
                 
                 if updated_uids:
                     # Get names for display
                     updated_names = []
                     for uid in updated_uids:
                         p = domain_manager.get_participant_data(channel_id, uid)
                         if p: updated_names.append(p.get("mask", "Player"))
                     pc_msg += f"\n✅ 캐릭터 업데이트: {', '.join(updated_names)} (패시브 및 설정 적용)"
            
            # 3. Update Genre (3-Layer)
            # Adapt unified_res to legacy structure
            genre_data = {
                "layers": {
                    "world_setting": genre_res.get("world_setting", []),
                    "style_tech": genre_res.get("style_tech", []),
                    "narrative_tone": genre_res.get("narrative_tone", [])
                },
                "atmosphere_guide": genre_res.get("atmosphere_guide", "")
            }
            domain_manager.set_active_genres(channel_id, genre_data)
            domain_manager.set_custom_tone(channel_id, genre_res.get("atmosphere_guide"))
            
            # 4. Update Lore Summary (Anomaly Seeds included)
            summary_text = f"테마: {lore_summary_data.get('theme', '')}\n이변 징후: {', '.join(lore_summary_data.get('anomaly_seeds', []))}\n공간: {lore_summary_data.get('locations', '')}"
            domain_manager.set_event_lore_summary(channel_id, summary_text)
            
            # 5. Update World Constraints (로어 세계 규칙)
            world_constraints = unified_res.get("world_constraints", {})
            if world_constraints and isinstance(world_constraints, dict):
                w = domain_manager.get_world_state(channel_id)
                w["world_constraints"] = world_constraints
                domain_manager.update_world_state(channel_id, w)

            # JSON format storage for V4 Deep Analysis
            d_data = domain_manager.get_domain(channel_id)
            d_data["lore_summary_data"] = lore_summary_data
            domain_manager.save_domain(channel_id, d_data)

            domain_manager.append_lore(channel_id, full_content) 
            
            # Formatted Output (Match User's Legacy Format)
            genre_summary = f"{genre_res.get('world_setting', [])} / {genre_res.get('style_tech', [])} / {genre_res.get('narrative_tone', [])}"
            
            # [MODIFIED] Show ALL NPC Names in confirmation
            npc_names = [f"`{n['name']}`" for n in extracted_npcs if n.get('name')]
            npc_list_str = ", ".join(npc_names)
            
            await msg.edit(content=f"✅ **로어 분석 완료**\n\n👥 **NPC: {len(extracted_npcs)}명 식별**\n{npc_list_str}{pc_msg}\n\n🌍 **장르/톤**\n{genre_summary}\n\n🌪️ **이변 징후**\n{len(lore_summary_data.get('anomaly_seeds', []))}개 식별")

        except Exception as e:
            import traceback
            logger.error(f"Unified Lore Analysis Failed: {e}\n{traceback.format_exc()}")
            await msg.edit(content=f"⚠️ 분석 오류: {e}")
            domain_manager.append_lore(channel_id, full_content)
    else:
        domain_manager.append_lore(channel_id, full_content)
        domain_manager.set_event_lore_summary(channel_id, full_content[:1000])
        await msg.edit(content="📜 저장 완료 (AI 미사용 - 단순 요약)")



@registry.register("info", category="Player", aliases=["내정보", "me", "desc", "설명", "정보"], description="캐릭터 정보를 확인합니다.")
async def cmd_info(ctx: CommandContext) -> None:
    """
    V2 Layout: Profile -> Relations -> Passives -> Mental -> Adaptation -> Quests -> Notebook
    """
    uid = ctx.user_id
    p_data = domain_manager.get_participant_data(ctx.channel_id, uid)
    
    if not p_data:
        await ctx.send("❌ 등록 필요 (`!가면 [이름]`)")
        return

    # [NEW] Description Update Support (if alias is desc/설명)
    if ctx.trigger in ['desc', '설명']:
        # 1. Extract Full Argument (Text + Attachment)
        file_text = ""
        if ctx.message.attachments:
            for att in ctx.message.attachments:
                text, error = await read_attachment_text(att)
                if error:
                    await ctx.send(error)
                    return
                if text:
                    file_text = text
                    break
        
        full_arg = (ctx.raw_args + "\n" + file_text).strip()
        
        if not full_arg:
            if not ctx.message.attachments:
                # Fallback to View mode if no args provided at all
                pass 
            else:
                await ctx.send("⚠️ 파일 내용을 읽을 수 없습니다.")
                return
        else:
            # 2. Update logic
            status_msg = await ctx.send("📝 **캐릭터 설정 분석 중...**")
            
            # AI Analysis Integration
            if ctx.genai_client:
                analysis = await cognition.analyze_character_sheet(ctx.genai_client, ctx.model_id, full_arg)
                if analysis:
                    # [V4 Integration] Save as Global PC Template (Like !로어)
                    if analysis.get("name"):
                        domain_manager.set_default_pc_info(ctx.channel_id, analysis)
                        
                    # 1. Update Current User directly
                    domain_manager.apply_pc_info_to_user(ctx.channel_id, uid)
                    
                    # 2. Sync others if name matches (Same as !로어)
                    updated_uids = domain_manager.sync_matching_participants(ctx.channel_id, analysis)
                    
                    # Filter out current user from "sync others" count
                    other_uids = [u for u in updated_uids if u != uid]
                    sync_info = ""
                    if other_uids:
                        sync_info = f"\n(동일 캐릭터 사용자 {len(other_uids)}명 동시 업데이트)"

                    await status_msg.edit(content=f"✅ **캐릭터 설정 동기화 완료**\n이름: {analysis.get('name', '유지')}\n특성: {len(analysis.get('passives', []))}개 추출\n소지품/설정 데이터가 시스템에 적용되었습니다.{sync_info}")
                    return
            
            # Fallback for no-AI or Fail
            domain_manager.update_participant(ctx.channel_id, ctx.message.author, desc=full_arg[:500])
            await status_msg.edit(content=f"📝 **설명 업데이트 완료** (단순 텍스트 저장)")
            return

    # 1. Profile (Mask, Desc, etc)
    mask_name = p_data.get("mask", "Unknown")
    desc = p_data.get("desc", "설명 없음")
    
    # Appearance/Personality from AI Memory if available
    mem = p_data.get("ai_memory", {})
    appearance = mem.get("appearance", "")
    description = mem.get("description", "")
    background = mem.get("background", "")
    
    msg = [f"🎭 **{mask_name}**"]
    if desc: msg.append(f"> {desc}")
    if appearance: msg.append(f"**외모:** {appearance}")
    if description: msg.append(f"**설명:** {description}")
    if background: msg.append(f"**배경:** {background}")
    
    # 2. Relations (NPC/Colleague)
    # This might be in 'relations' key in memory or external
    relations = mem.get("relations", [])
    if relations:
        rel_txt = []
        for r in relations:
            # Handle string or dict
            if isinstance(r, dict):
                r_name = r.get("name", "Unknown")
                r_desc = r.get("desc", "")
                rel_txt.append(f"- **{r_name}**: {r_desc}")
            else:
                rel_txt.append(f"- {r}")
        if rel_txt:
            msg.append("\n**🤝 관계:**")
            msg.extend(rel_txt)

    # 3. Passives (Traits + Titles)
    passives = mem.get("passives", [])
    if passives:
        p_list = []
        for p in passives:
            if isinstance(p, dict):
                p_name = p.get("name", "?")
                # Show Title prominently?
                tags = p.get("tags", [])
                prefix = "🏆 " if "Title" in tags else "🔹 "
                p_list.append(f"{prefix}**{p_name}**")
            else:
                p_list.append(f"🔹 **{p}**")
        if p_list:
            msg.append("\n**✨ 특성:**")
            msg.append(" / ".join(p_list))

    # 4. Mental
    m_stage = p_data.get("mental_stage", 0)
    # Using game_character logic to get Emoji/Name
    mental_data: Dict[str, Any] = mem.get("mental", {})
    m_val = mental_data.get("value", 100)
    m_info = game_character.get_mental_info(m_val)
    msg.append(f"\n**🧠 멘탈:** {m_info['emoji']} **{m_info['name']}**")

    # 5. Adaptation (Hidden Bar)
    exposure = p_data.get("abnormal_exposure", {})
    if exposure:
        msg.append("**🦠 적응도:**")
        for tag, data in exposure.items():
            count = data.get("count", 0)
            pct = game_character.calculate_adaptation_percentage(count)
            # Simple Bar
            bar_len = min(10, int(pct / 10))
            bar = "▮" * bar_len + "▯" * (10 - bar_len)
            msg.append(f"• [{tag}]: {bar} ({pct}%)")

    # 6. Quests (Active)
    quests = game_system.get_active_quests(ctx.channel_id)
    if quests:
        msg.append("\n**🛡️ 진행 중인 퀘스트:**")
        msg.extend([f"- {q}" for q in quests])

    # 7. Notebook (Unified Inventory/Memo, per-user)
    notebook = game_system.get_notebook_text(ctx.channel_id, ctx.user_id)
    if notebook:
        msg.append(f"\n**📔 노트북:**\n{notebook}")

    await send_long_message(ctx.message.channel, "\n".join(msg))


@registry.register("mask", category="Player", aliases=["가면", "persona"], description="캐릭터 변경/등록")
async def cmd_mask(ctx: CommandContext) -> None:
    """!가면 [이름] - 캐릭터 설정 및 PC 정보 연결"""
    if not ctx.args:
        # Show Current
        p_data = domain_manager.get_participant_data(ctx.channel_id, ctx.user_id)
        curr = p_data.get("mask", "없음") if p_data else "없음"
        await ctx.send(f"🎭 현재 가면: **{curr}**\n사용법: `!가면 [이름]`")
        return

    target = ctx.raw_args.strip() # Use raw args to allow spaces in names
    
    # Update Participation
    domain_manager.update_participant(ctx.channel_id, ctx.message.author)
    domain_manager.set_user_mask(ctx.channel_id, ctx.user_id, target)
    
    # Link PC info (Auto-Mapping)
    pc = domain_manager.get_default_pc_info(ctx.channel_id)
    mapped_msg = ""
    
    if pc and (target in pc.get("name", "") or pc.get("name", "") in target):
         if domain_manager.apply_pc_info_to_user(ctx.channel_id, ctx.user_id):
             mapped_msg = " (PC 정보 동기화됨)"
             
    await ctx.send(f"🎭 **{target}**(으)로 변신했습니다.{mapped_msg}")


@registry.register("notebook", category="Player", aliases=["노트북", "note", "memo", "메모", "inven", "인벤"], description="노트북/인벤토리 관리")
async def cmd_notebook(ctx: CommandContext) -> None:
    """!노트북 [추가/수정/삭제] [내용]"""
    arg = ctx.raw_args.strip()
    channel_id = ctx.channel_id
    
    uid = ctx.user_id
    if not arg:
        text = game_system.get_notebook_text(channel_id, uid)
        await send_long_message(ctx.message.channel, f"📔 **현재 노트북 내용:**\n\n{text}")
        return

    # sub_command parsing
    parts = arg.split(None, 1)
    sub = parts[0].lower()
    content = parts[1] if len(parts) > 1 else ""

    if sub in ['추가', 'add', 'a']:
        curr = game_system.get_notebook_text(channel_id, uid)
        to_add = content if content else ""
        new_text = f"{curr}\n- {to_add}"
        game_system.update_notebook_text(channel_id, new_text, uid)
        await ctx.send("✅ 노트북에 내용이 추가되었습니다.")

    elif sub in ['수정', 'edit', 'set', 'e']:
        if "->" in content:
            old_val, new_val = content.split("->", 1)
            await ctx.send(game_system.edit_memo(channel_id, old_val.strip(), new_val.strip(), uid))
        else:
            game_system.update_notebook_text(channel_id, content, uid)
            await ctx.send("✅ 노트북 내용이 전체 수정되었습니다. (부분 수정은 `구형 -> 신형` 형식 사용)")

    elif sub in ['삭제', 'del', 'remove', 'r', 'd']:
        curr = game_system.get_notebook_text(channel_id, uid)
        if content and content in curr:
            new_text = curr.replace(content, "").replace("\n\n\n", "\n\n").strip()
            game_system.update_notebook_text(channel_id, new_text, uid)
            await ctx.send(f"🗑️ 노트북에서 '{content[:20]}...' 내용을 삭제했습니다.")
        else:
            await ctx.send("⚠️ 삭제할 내용을 찾을 수 없습니다. (정확히 일치해야 합니다)")

    else:
        curr = game_system.get_notebook_text(channel_id, uid)
        new_text = f"{curr}\n- {arg}"
        game_system.update_notebook_text(channel_id, new_text, uid)
        await ctx.send("✅ 노트북에 내용이 기록되었습니다.")




@registry.register("npc", category="World", aliases=["엔피씨", "addnpc", "npc정보", "npc추가"], description="NPC 관리")
async def cmd_npc(ctx: CommandContext) -> None:
    """!npc [조회/추가] [이름] [설명] or !addnpc [Batch]"""
    # 1. File Content
    file_text = ""
    if ctx.message.attachments:
        for att in ctx.message.attachments:
            text, error = await read_attachment_text(att)
            if error:
                await ctx.send(error)
                return
            if text:
                file_text = text
                break

    # 2. Batch Processing Logic (Restored from handle_npc_command)
    arg = ctx.raw_args
    raw_lines = (arg + "\n" + file_text).strip().splitlines()
    processed_count = 0
    channel_id = ctx.channel_id
    
    # If explicit "addnpc" or batch mode implied
    if ctx.trigger in ['addnpc', 'npc추가'] or (len(raw_lines) > 1) or (file_text):
        if not raw_lines[0].strip() and not file_text:
             await ctx.send("⚠️ 등록할 내용이 없습니다. `!npc추가 [이름]: [설명]` 또는 파일 첨부.")
             return

        last_name = None
        full_text = "\n".join(raw_lines)
        is_deep_profile = bool(re.search(r"(?:^|\n)\s*(?:\*|-)?\s*(?:Name|이름)\s*:", full_text, re.IGNORECASE))
        
        for line in raw_lines:
            line = line.strip()
            if not line: continue
            
            # Explicit Name
            name_match = re.match(r"^(?:\*|-)?\s*(?:Name|이름)\s*:\s*(.+)$", line, re.IGNORECASE)
            if name_match:
                name = name_match.group(1).strip()
                domain_manager.update_npc(channel_id, name, {"desc": line, "source": "manual", "status": "Active"})
                processed_count += 1
                last_name = name
                continue

            # Key-Value
            if ":" in line:
                if is_deep_profile and last_name:
                     curr_npc = domain_manager.get_npc(channel_id, last_name)
                     if curr_npc:
                         new_desc = curr_npc.get("desc", "") + "\n" + line
                         domain_manager.update_npc(channel_id, last_name, {"desc": new_desc, "source": "manual", "status": "Active"})
                     continue
                
                key, val = line.split(":", 1)
                clean_key = key.lstrip("*-> ").strip()
                val = val.strip()
                
                if clean_key and val:
                    domain_manager.update_npc(channel_id, clean_key, {"desc": val, "source": "manual", "status": "Active"})
                    processed_count += 1
                    last_name = clean_key
                    continue

            # Continuation
            if last_name:
                 curr_npc = domain_manager.get_npc(channel_id, last_name)
                 if curr_npc:
                     new_desc = curr_npc.get("desc", "") + "\n" + line
                     domain_manager.update_npc(channel_id, last_name, {"desc": new_desc, "source": "manual", "status": "Active"})

        if processed_count > 0:
            if processed_count == 1:
                await ctx.send(f"👥 **NPC 등록:** {last_name}")
            else:
                await ctx.send(f"👥 **NPC 일괄 등록 완료:** 총 {processed_count}명")
        else:
             # Fallback if single line simple add "Name Desc"? 
             # No, stick to "Name: Desc" format for consistency.
             await ctx.send("⚠️ 유효한 형식을 찾을 수 없습니다. (예: `이름: 설명`)")
        return

    # Look up NPC
    if not arg:
        npcs = domain_manager.get_npcs(channel_id)
        if not npcs:
            await ctx.send("👥 등록된 NPC가 없습니다.")
            return
        
        # List all
        name_list = [f"• **{n}**: {d.get('desc','-')[:30]}..." for n, d in npcs.items()]
        await send_long_message(ctx.message.channel, "👥 **NPC 목록**\n" + "\n".join(name_list))
    else:
        # Specific NPC
        npc = domain_manager.get_npc(channel_id, arg.strip())
        if npc:
            msg = [f"👤 **{arg}**"]
            if npc.get('gender') or npc.get('race'):
                meta = []
                if npc.get('gender'): meta.append(npc.get('gender'))
                if npc.get('race'): meta.append(npc.get('race'))
                msg.append(f"({', '.join(meta)})")
            
            msg.append(f"{npc.get('desc')}")
            
            if npc.get('appearance'): msg.append(f"**외양:** {npc.get('appearance')}")
            if npc.get('description'): msg.append(f"**설명:** {npc.get('description')}")
            if npc.get('background'): msg.append(f"**배경:** {npc.get('background')}")
            
            await ctx.send("\n".join(msg))
        else:
            await ctx.send(f"⚠️ NPC '{arg}' 정보를 찾을 수 없습니다.")



# Handle Participant Command Removed (Logic absorbed into cmd_info and deprecated commands removed)



@registry.register("reset", category="Admin", aliases=["리셋", "초기화"], description="세션 데이터 전체 초기화")
async def cmd_reset(ctx: CommandContext) -> None:
    """!리셋 - 이모지 확인 후 전체 초기화 (채널 재생성)"""
    # 이모지 확인은 session_manager에서 처리
    await session_manager.manager.execute_reset(ctx.message, ctx.client)


@registry.register("clear", category="Admin", aliases=["클리어", "청소"], description="화면 청소 (데이터 유지)")
async def cmd_clear(ctx: CommandContext) -> None:
    """!클리어"""
    await session_manager.manager.execute_clear(ctx.message)


@registry.register("ready", category="Admin", aliases=["준비"], description="준비 상태 점검")
async def cmd_ready(ctx: CommandContext) -> None:
    """!준비"""
    await session_manager.manager.check_preparation(ctx.message)


@registry.register("start", category="Admin", aliases=["시작"], description="세션 시작 [첫 상황]")
async def cmd_start(ctx: CommandContext) -> None:
    """
    !시작 [첫 상황]

    예시:
    - !시작                              → LLM이 오프닝 생성
    - !시작 노예 시장의 철창 안에서 경매를 기다린다  → 지정된 상황으로 시작
    """
    domain_manager.update_participant(ctx.channel_id, ctx.message.author)
    if await session_manager.manager.start_session(ctx.message, ctx.genai_client, ctx.model_id):
        # Check for custom opening scenario
        custom_scenario = ctx.raw_args.strip() if ctx.raw_args else ""
        if custom_scenario:
            # Include custom scenario in the trigger
            return f"Opening: {custom_scenario}"
        return "Opening"
    return None


@registry.register("retry", category="System", aliases=["다시", "reroll", "재판정"], description="마지막 AI 응답 재생성")
async def cmd_retry(ctx: CommandContext) -> None:
    """!다시 - 마지막 AI 응답을 삭제하고 새로 굴림"""
    from orchestration import get_orchestration_runtime
    orchestration = get_orchestration_runtime(ctx.genai_client, ctx.model_id, config.MODEL_ID_FLASH)
    
    if not orchestration:
        await ctx.send("⚠️ AI 서비스가 초기화되지 않았습니다.")
        return
    
    await orchestration.retry_last(ctx.message, ctx.channel_id)


@registry.register("mode", category="System", aliases=["모드"], description="AI 응답 모드 변경")
async def cmd_mode(ctx: CommandContext) -> None:
    """!모드 [자동/수동]"""
    arg = ctx.raw_args.strip()
    
    if not arg:
        d = domain_manager.get_domain(ctx.channel_id)
        curr = d['settings'].get('mode', 'auto')
        mode_kr = {'auto': '자동', 'waiting': '수동', 'manual': '수동', 'assist': '보조'}.get(curr, curr)
        await ctx.send(f"⚙️ 현재 모드: **{mode_kr}**\n사용법: `!모드 [자동/수동]`")
        return
    
    mode_map = {'자동': 'auto', '수동': 'waiting', 'auto': 'auto', 'waiting': 'waiting', 'manual': 'waiting', 'assist': 'assist'}
    mode = mode_map.get(arg.lower(), arg.lower())
    mode_kr = {'auto': '자동', 'waiting': '수동', 'assist': '보조'}.get(mode, mode)
    
    domain_manager.update_settings(ctx.channel_id, mode=mode)
    await ctx.send(f"⚙️ 모드 변경: **{mode_kr}**")


@registry.register("scene", category="System", aliases=["장면", "수위", "mature"], description="장면 수위/유형 변경")
async def cmd_scene(ctx: CommandContext) -> None:
    """!장면 [일반/고어/성인/전체]"""
    arg = ctx.raw_args.strip()
    
    if not arg:
        d = domain_manager.get_domain(ctx.channel_id)
        curr = d['settings'].get('scene_type', 'normal')
        scene_info = {
            'normal': ('🌿 일반', '모든 성인 콘텐츠 비활성화'),
            'gore': ('🩸 고어', '폭력/잔혹 묘사 활성화'),
            'nsfw': ('💋 NSFW', '성인 묘사 활성화'),
            'gore_nsfw': ('⚠️ 전체', '고어 + NSFW + 하이브리드 모두 활성화')
        }
        info = scene_info.get(curr, ('❓ 알 수 없음', ''))
        await ctx.send(
            f"🎬 **현재 장면 수위: {info[0]}**\n"
            f"> {info[1]}\n\n"
            f"**사용법:** `!장면 [일반/고어/성인/전체]`\n"
            f"- `일반` (normal): 성인 콘텐츠 없음\n"
            f"- `고어` (gore): 폭력/잔혹 묘사만\n"
            f"- `성인` (nsfw): 성인 묘사만\n"
            f"- `전체` (all): 고어+성인+하이브리드"
        )
        return
    
    scene_map = {
        '일반': 'normal', 'normal': 'normal', '노말': 'normal', '안전': 'normal',
        '고어': 'gore', 'gore': 'gore', '폭력': 'gore', '잔혹': 'gore',
        'nsfw': 'nsfw', '성인': 'nsfw', '야한': 'nsfw', '19': 'nsfw',
        '전체': 'gore_nsfw', 'all': 'gore_nsfw', 'gore_nsfw': 'gore_nsfw', 
        '올': 'gore_nsfw', '하이브리드': 'gore_nsfw', 'hybrid': 'gore_nsfw'
    }
    scene_type = scene_map.get(arg.lower())
    
    if not scene_type:
         await ctx.send("⚠️ 지원하지 않는 장면 유형입니다.\n`일반`, `고어`, `성인`, `전체` 중 선택하세요.")
         return
    
    scene_info = {
        'normal': ('🌿 일반', '모든 성인 콘텐츠가 비활성화되었습니다.'),
        'gore': ('🩸 고어', '폭력/잔혹 묘사가 활성화되었습니다.\n해부학적 정밀도, 슬래셔 페이싱, 환경 통합이 적용됩니다.'),
        'nsfw': ('💋 NSFW', '성인 묘사가 활성화되었습니다.\n오감 몰입, 감정 아키텍처, 호흡 메트로놈이 적용됩니다.'),
        'gore_nsfw': ('⚠️ 전체(하이브리드)', '고어 + NSFW + 하이브리드 모드가 모두 활성화되었습니다.\n료나/구로/스너프 등 극단적 콘텐츠가 허용됩니다.')
    }
    info = scene_info.get(scene_type)
    domain_manager.update_settings(ctx.channel_id, scene_type=scene_type)
    await ctx.send(f"🎬 **장면 수위 변경: {info[0]}**\n> {info[1]}")


@registry.register("lock", category="System", aliases=["잠금"], description="세션 잠금")
async def cmd_lock(ctx: CommandContext) -> None:
    """!잠금"""
    domain_manager.set_session_lock(ctx.channel_id, True)
    await ctx.send("🔒 **세션 잠금**: 외부 개입이 제한됩니다.")


@registry.register("unlock", category="System", aliases=["해제", "잠금해제"], description="세션 잠금 해제")
async def cmd_unlock(ctx: CommandContext) -> None:
    """!해제"""
    domain_manager.set_session_lock(ctx.channel_id, False)
    await ctx.send("🔓 **세션 잠금 해제**: 자유롭게 참여 가능합니다.")


# =========================================================
# UNE Module Control Commands
# =========================================================

@registry.register("modules", category="System", aliases=["모듈", "mods"], description="DLC 모듈 활성화 상태 확인 및 일괄 제어")
async def cmd_modules(ctx: CommandContext) -> None:
    """!모듈 [on/off] - 모듈 상태 확인 또는 일괄 제어"""
    arg = ctx.raw_args.strip().lower()
    active = domain_manager.get_active_modules(ctx.channel_id)
    all_mods = [("judgment", "판정"), ("doom", "둠"), ("anomaly", "이변"), ("mental", "멘탈")]
    
    # 일괄 ON
    if arg in ['on', '켜기', 'true', 'all']:
        for code, _ in all_mods:
            domain_manager.toggle_module(ctx.channel_id, code, True)
        await ctx.send("✅ **모든 모듈이 활성화되었습니다.**\n• 판정 ✅\n• 둠 ✅\n• 이변 ✅\n• 멘탈 ✅")
        return
    
    # 일괄 OFF
    if arg in ['off', '끄기', 'false', 'none']:
        for code, _ in all_mods:
            domain_manager.toggle_module(ctx.channel_id, code, False)
        await ctx.send("❌ **모든 모듈이 비활성화되었습니다.**\n• 판정 ❌\n• 둠 ❌\n• 이변 ❌\n• 멘탈 ❌")
        return
    
    # 상태 확인
    msg = ["🔌 **DLC 모듈 상태**"]
    for code, name in all_mods:
        status = "✅ ON" if code in active else "❌ OFF"
        msg.append(f"• {name} ({code}): {status}")
    
    msg.append("\n💡 **사용법**:")
    msg.append("• `!모듈 on` - 모든 모듈 활성화")
    msg.append("• `!모듈 off` - 모든 모듈 비활성화")
    msg.append("• `!판정 on/off` - 개별 모듈 제어")
    
    await ctx.send("\n".join(msg))

@registry.register("judgment", category="System", aliases=["판정"], description="판정 모듈 제어")
async def cmd_toggle_judgment(ctx: CommandContext) -> None:
    await _handle_module_toggle(ctx, "judgment", "판정")

@registry.register("doom_mod", category="System", aliases=["둠모듈", "doommod"], description="둠 모듈 제어")
async def cmd_toggle_doom(ctx: CommandContext) -> None:
    await _handle_module_toggle(ctx, "doom", "둠")

@registry.register("anomaly", category="System", aliases=["이변", "비일상", "abnormal"], description="이변 모듈 제어")
async def cmd_toggle_anomaly(ctx: CommandContext) -> None:
    await _handle_module_toggle(ctx, "anomaly", "이변")

@registry.register("mental_mod", category="System", aliases=["멘탈모듈", "mentalmod"], description="멘탈 모듈 제어")
async def cmd_toggle_mental(ctx: CommandContext) -> None:
    await _handle_module_toggle(ctx, "mental", "멘탈")

async def _handle_module_toggle(ctx: CommandContext, code: str, name: str):
    arg = ctx.raw_args.strip().lower()
    if not arg:
        active = domain_manager.get_active_modules(ctx.channel_id)
        status = "✅ ON" if code in active else "❌ OFF"
        await ctx.send(f"⚙️ **{name} 모듈 상태**: {status}\n사용법: `!{ctx.trigger} on/off`")
        return
    
    if arg in ['on', '켜기', 'true']:
        domain_manager.toggle_module(ctx.channel_id, code, True)
        await ctx.send(f"✅ **{name} 모듈**이 활성화되었습니다.")
    elif arg in ['off', '끄기', 'false']:
        domain_manager.toggle_module(ctx.channel_id, code, False)
        await ctx.send(f"❌ **{name} 모듈**이 비활성화되었습니다.")

@registry.register("impersonation", category="System", aliases=["사칭", "사칭감지"], description="PC 사칭 감지 on/off")
async def cmd_toggle_impersonation(ctx: CommandContext) -> None:
    """!사칭 [on/off]"""
    arg = ctx.raw_args.strip().lower()
    if not arg:
        enabled = domain_manager.get_domain(ctx.channel_id).get("settings", {}).get("impersonation_filter", True)
        status = "✅ ON" if enabled else "❌ OFF"
        await ctx.send(f"🛡️ **PC 사칭 감지 상태**: {status}\n사용법: `!사칭 on/off`")
        return

    if arg in ['on', '켜기', 'true']:
        domain_manager.update_settings(ctx.channel_id, impersonation_filter=True)
        await ctx.send("✅ **PC 사칭 감지**가 활성화되었습니다.\n응답에서 PC 행동/대사/사고 묘사를 감지하고 제거합니다.")
    elif arg in ['off', '끄기', 'false']:
        domain_manager.update_settings(ctx.channel_id, impersonation_filter=False)
        await ctx.send("❌ **PC 사칭 감지**가 비활성화되었습니다.\nPC 사칭 필터링이 중단됩니다.")

@registry.register("bot", category="System", aliases=["봇"], description="봇 활성화 제어")
async def cmd_bot(ctx: CommandContext) -> None:
    """!봇 [on/off]"""
    arg = ctx.raw_args.strip().lower()
    
    if not arg:
        curr = domain_manager.get_bot_active(ctx.channel_id)
        status = "✅ ON" if curr else "❌ OFF"
        await ctx.send(f"🤖 봇 상태: **{status}**\n사용법: `!bot [on/off]`")
        return
        
    if arg in ['on', '켜기', 'true']:
        domain_manager.set_bot_active(ctx.channel_id, True)
        await ctx.send("🤖 **봇 활성화:** ✅ ON")
    elif arg in ['off', '끄기', 'false']:
        domain_manager.set_bot_active(ctx.channel_id, False)
        await ctx.send("🤖 **봇 비활성화:** ❌ OFF (명령어만 반응)")




@registry.register("lores", category="Analysis", aliases=["연대기", "chronicle"], description="연대기 추출")
async def cmd_lores(ctx: CommandContext) -> None:
    """!연대기 [new/증분]"""
    # Check for incremental argument "new", "inc"
    incremental = False
    arg = ctx.raw_args.lower()
    if arg in ['new', 'inc', '증분', '최신']:
         incremental = True
         
    export_text, msg = game_system.export_chronicle_book(ctx.channel_id, incremental=incremental)
    if export_text:
        fname = f"Chronicles_{ctx.channel_id}_{'INC' if incremental else 'FULL'}.txt"
        await ctx.message.channel.send(msg, file=discord.File(io.StringIO(export_text), filename=fname))
    else:
        await ctx.send(msg)


# !abnormal / !비일상 은 !이변 (anomaly) 으로 통합되었습니다.
# @registry.register("abnormal", category="Analysis", aliases=["비일상"], description="비일상 적응도 시스템 제어")



# handle_time_command migrated to cmd_time


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
    narrative_keywords = [
        "해줘", "보여줘", "묘사", "장면", "진행", "스킵", "넘어가",
        "가정", "상정", "상황", "이었다", "되었다"
    ]
    if any(kw in content_lower for kw in narrative_keywords):
        return "narrative_request"
    
    return "general"


async def handle_ooc_command(
    message: discord.Message, 
    channel_id: str, 
    ooc_content: str, 
    client_genai, 
    model_id: str
) -> Optional[str]:
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
            
        await message.channel.send("🔄 **루카가 데이터를 수정하고 있어...**")
        
        # [V5.3] Notebook Integration (per-user)
        notebook_txt = game_system.get_notebook_text(channel_id, uid)
        
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
                        game_system.add_memo(channel_id, value, uid)
                    elif action == "replace" or action == "set":
                         game_system.update_notebook_text(channel_id, value, uid)
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
            await message.channel.send("⚠️ 루카가 수정 사항을 인식하지 못했어.")
            return None
    
    elif ooc_type == "narrative_request":
        # 서사 지시는 프롬프트에 주입하기 위해 반환
        return f"[OOC Directive: {ooc_content}]"

    else:
        # general: 일반 OOC도 서사 지시로 전달
        return f"[OOC Directive: {ooc_content}]"


@registry.register("ooc", category="System", aliases=["OOC", "메타", "루카"], description="루카 (OOC 도우미) 모드 토글")
async def cmd_ooc(ctx: CommandContext) -> None:
    """!ooc - 루카 (OOC 도우미) 모드 ON/OFF 토글"""
    current = domain_manager.get_ooc_mode(ctx.channel_id)
    new_state = not current
    domain_manager.set_ooc_mode(ctx.channel_id, new_state)
    if new_state:
        await ctx.send("💬 **루카 모드 ON** — 루카가 대화에 참여합니다.\n> 모든 메시지가 OOC 대화로 처리됩니다. 해제: `!ooc`")
    else:
        await ctx.send("🎭 **루카 모드 OFF** — 서사 모드로 복귀합니다.")

@registry.register("mental", category="Player", aliases=["멘탈", "mental"], description="멘탈 조회 및 설정")
async def cmd_mental(ctx: CommandContext) -> None:
    """!멘탈 [값] - 멘탈 수치를 특정 값으로 설정 (0-100)"""
    uid = ctx.user_id
    p_data = domain_manager.get_participant_data(ctx.channel_id, uid)
    
    if not p_data:
        await ctx.send("❌ 등록 필요 (`!가면`)")
        return
        
    mem = p_data.get("ai_memory", {})
    ment = mem.get("mental", {"value": 100})
    
    # [View Mode]
    if not ctx.args:
        val = ment.get("value", 100)
        info = game_character.get_mental_info(val)
        desc = info['desc']
        await ctx.send(f"🧠 **정신 상태:** {info['emoji']} **{info['name']}** ({val}/100)\n> {desc}")
        return

    # [Set Mode]
    try:
        target_val = int(ctx.args[0])
        # Clamp 0-100
        target_val = max(0, min(100, target_val))
        
        # Direct Set (Bypass game logic mechanics like Trauma/Clamping for manual correction)
        # However, we should respect the structure
        if "mental" not in mem: mem["mental"] = {}
        
        mem["mental"]["value"] = target_val
        mem["mental"]["last_delta"] = 0 # Reset delta on manual set
        
        # Save
        p_data["ai_memory"] = mem # Ensure ref
        domain_manager.save_participant_data(ctx.channel_id, uid, p_data)
        
        # Feedback
        info = game_character.get_mental_info(target_val)
        await ctx.send(f"🧠 **멘탈 설정 완료:** {target_val}/100\n현재 상태: {info['emoji']} **{info['name']}**")
        
    except ValueError:
        await ctx.send("⚠️ 올바른 숫자를 입력하세요. (예: `!멘탈 50`)")






@registry.register("reset_npcs", category="Admin", aliases=["엔피씨초기화", "npc_reset"], description="세션 NPC 초기화")
async def cmd_reset_npcs(ctx: CommandContext) -> None:
    """!reset_npcs"""
    if not domain_manager.is_session_locked(ctx.channel_id):
        # Optional: Check admin implementation if needed, for now allow
        pass

    count = npc_manager.clear_session_npcs(ctx.channel_id)
    await ctx.send(f"🧹 **세션 NPC 초기화 완료:** {count}명 삭제됨 (Lore NPC 유지)")


@registry.register("rule", category="World", aliases=["룰", "규칙", "rules", "worldrules", "세계규칙"], description="세계 규칙 관리")
async def cmd_rule(ctx: CommandContext) -> None:
    """!룰 [추가/삭제/목록] [키워드] [내용]"""
    args = ctx.args
    if not args:
        sub = "list"
    else:
        sub = args[0].lower()
    
    # Load World Data
    w = domain_manager.get_world_state(ctx.channel_id)
    rules = w.get("location_rules", {})
    
    # 1. List
    if sub in ['list', '목록', '조회', 'l']:
        if not rules:
            await ctx.send("📜 활성화된 특수 규칙이 없습니다.")
            return
        
        msg = ["📜 **세계 규칙 목록**"]
        for k, v in rules.items():
            desc = v.get('desc', '') if isinstance(v, dict) else str(v)
            msg.append(f"- **{k}**: {desc}")
        await ctx.send("\n".join(msg))
        return

    # 2. Add / Update
    if sub in ['add', '추가', 'set', '설정', 'a']:
        if len(args) < 3:
            await ctx.send("⚠️ 사용법: `!룰 추가 [키워드] [설명]`")
            return
        
        key = args[1]
        desc = " ".join(args[2:])
        
        rules[key] = {"desc": desc, "created_at": time.strftime('%Y-%m-%d')}
        w["location_rules"] = rules
        domain_manager.update_world_state(ctx.channel_id, w)
        await ctx.send(f"📜 **규칙 설정:** [{key}] - {desc}")
        return

    # 3. Remove
    if sub in ['remove', 'delete', 'del', '삭제', '제거', 'r']:
        if len(args) < 2:
            await ctx.send("⚠️ 사용법: `!룰 삭제 [키워드]`")
            return
            
        key = args[1]
        if key in rules:
            del rules[key]
            w["location_rules"] = rules
            domain_manager.update_world_state(ctx.channel_id, w)
            await ctx.send(f"🗑️ **규칙 삭제:** [{key}]")
        else:
            await ctx.send(f"⚠️ 규칙 '{key}'(을)를 찾을 수 없습니다.")
        return
        
    await ctx.send(f"⚠️ 사용법: `!룰 [목록/추가/삭제]`")


@registry.register("quest", category="World", aliases=["퀘스트"], description="퀘스트 관리")
async def cmd_quest(ctx: CommandContext) -> None:
    """!quest [add/complete/remove/list] [내용]"""
    args = ctx.args
    raw = ctx.raw_args.strip()

    if not args:
        await ctx.send(game_system.get_active_quests_text(ctx.channel_id))
        return

    sub = args[0].lower()
    content = raw[len(args[0]):].strip() if raw else ""

    if sub in ["list", "목록", "l"]:
        await ctx.send(game_system.get_active_quests_text(ctx.channel_id))
        return

    if sub in ["add", "추가", "+"]:
        if not content:
            await ctx.send("⚠️ 추가할 퀘스트 내용을 입력하세요. (`!quest add 내용`)")
            return
        await ctx.send(game_system.add_quest(ctx.channel_id, content))
        return

    if sub in ["complete", "완료", "done", "clear"]:
        if not content:
            await ctx.send("⚠️ 완료할 퀘스트 이름을 입력하세요. (`!quest complete 이름`)")
            return
        await ctx.send(game_system.complete_quest(ctx.channel_id, content))
        return

    if sub in ["remove", "삭제", "del", "delete"]:
        if not content:
            await ctx.send("⚠️ 삭제할 퀘스트 이름을 입력하세요. (`!quest remove 이름`)")
            return
        await ctx.send(game_system.remove_quest(ctx.channel_id, content))
        return

    # Fallback: treat raw input as a quest to add
    if raw:
        await ctx.send(game_system.add_quest(ctx.channel_id, raw))
        return
    await ctx.send("📋 사용법: `!quest [add/complete/remove/list] [내용]`")


@registry.register("time", category="World", aliases=["시간", "time_adv", "next", "turn", "진행", "건너뛰기", "턴"], description="시간 관리")
async def cmd_time(ctx: CommandContext) -> None:
    """!시간 [진행/조회/설정]"""
    args = ctx.args
    arg_str = ctx.raw_args.strip()
    
    world = domain_manager.get_world_state(ctx.channel_id)
    
    if not args:
        if ctx.trigger in ["next", "turn", "진행", "건너뛰기", "턴"]:
            # 축적된 PC 행동 확인
            pending = domain_manager.get_pending_actions(ctx.channel_id)

            from orchestration import get_orchestration_runtime
            orch = get_orchestration_runtime(ctx.genai_client, ctx.model_id, config.MODEL_ID_FLASH)
            if not orch:
                await ctx.send("⚠️ AI 서비스가 초기화되지 않았습니다.")
                return

            if pending:
                # BATCH MODE: 축적된 행동 일괄 처리
                feedback = await ctx.message.channel.send("🔄 **행동을 처리하고 있습니다...**")
                await orch.execute_batch(ctx.message, ctx.channel_id, pending, feedback)
            else:
                # OBSERVATION MODE: 관찰 턴 (1틱 시간 경과 + 세계 묘사)
                tick_msg = game_system.advance_tick(ctx.channel_id)
                await ctx.send(tick_msg)
                feedback = await ctx.message.channel.send("🔄 **세계를 관찰하고 있습니다...**")
                await orch.execute_observation(ctx.message, ctx.channel_id, feedback)
            return
        # View
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
        await ctx.send(msg)
        return
        
    first = args[0].lower()
    
    # Advance
    if first in ["진행", "next", "pass"]:
        msg = game_system.advance_time(ctx.channel_id)
        await ctx.send(msg)
        return
        
    # Advance N
    if first.isdigit():
        count = int(first)
        if count > 12:
            await ctx.send("⚠️ 최대 12시간대까지 진행 가능합니다.")
            return
        msgs = []
        for _ in range(count):
            msgs.append(game_system.advance_time(ctx.channel_id))
        await ctx.send("\n".join(msgs))
        return

    # Set
    if first in ["설정", "set"]:
        if len(args) < 2:
            await ctx.send("⚠️ 사용법: `!시간 설정 [오전/오후/...]`")
            return
        target = args[1]
        time_slots = game_system.get_time_slots(ctx.channel_id)
        if target in time_slots:
            world["time_slot"] = target
            domain_manager.update_world_state(ctx.channel_id, world)
            await ctx.send(f"⏰ 시간 설정: **{target}**")
        else:
            await ctx.send(f"⚠️ 유효한 시간대: {', '.join(time_slots)}")
        return


@registry.register("doom", category="World", aliases=["둠", "위기", "tension"], description="위기 수치 관리")
async def cmd_doom(ctx: CommandContext) -> None:
    """!둠 [조회/설정/증감]"""
    args = ctx.args
    
    if not args:
        await send_long_message(ctx.message.channel, game_world.get_doom_forecast(ctx.channel_id))
        return
        
    op = args[0]
    
    # Set
    if op.lower() == "set" or op == "설정":
        if len(args) < 2: 
            await ctx.send("⚠️ 값을 입력하세요 (예: `!둠 설정 50`)")
            return
        try:
            val = int(args[1])
            w = domain_manager.get_world_state(ctx.channel_id)
            old_v = w.get("doom", 0)
            w["doom"] = max(0, min(100, val))
            domain_manager.update_world_state(ctx.channel_id, w)
            await ctx.send(f"�️ **위기 수치 재설정:** {old_v}% → {val}%")
        except ValueError:
            await ctx.send("⚠️ 올바른 숫자가 아닙니다.")
        return
        
    # Increment/Decrement
    try:
        val = int(op)
        res = game_world.change_doom(ctx.channel_id, val)
        await ctx.send(res)
    except (ValueError, TypeError):
        await ctx.send("⚠️ 사용법: `!둠 10`, `!둠 -5`, `!둠 설정 50`")

@registry.register("export", category="System", aliases=["추출", "로그"], description="대화 내역 추출")
async def cmd_export(ctx: CommandContext) -> None:
    """!추출 [inc/증분]"""
    # Check Incremental
    arg = ctx.raw_args.lower()
    is_inc = any(x in arg for x in ['new', 'inc', '증분', '최신'])
    
    content, msg = game_character.export_session_history(ctx.channel_id, incremental=is_inc)
    mode_str = "INC" if is_inc else "FULL"
    fname = f"SessionLog_{ctx.channel_id}_{mode_str}.txt"

    if content:
        await ctx.send(msg, file=discord.File(io.StringIO(content), filename=fname))
    else:
        await send_long_message(ctx.message.channel, msg)


@registry.register("help", category="System", aliases=["도움말", "도움", "명령어", "help", "h"], description="명령어 목록")
async def cmd_help(ctx: CommandContext) -> None:
    """!도움말"""
    # Dynamic Help from Registry (Using existing method)
    grouped_cmds = registry.get_commands_by_category()
    
    msg = ["📜 **Lorekeeper Bot 명령어**"]
    
    # Sort Categories
    for cat in sorted(grouped_cmds.keys()):
        cmds = grouped_cmds[cat]
        if not cmds: continue
        
        msg.append(f"\n**[{cat}]**")
        for info in cmds:
            name = info['name']
            desc = info['description']
            # Optional: Show aliases
            # aliases = info['aliases']
            # if aliases: desc += f" ({', '.join(aliases)})"
            msg.append(f"`!{name}`: {desc}")
        
    await send_long_message(ctx.message.channel, "\n".join(msg))


async def dispatch_command(
    cmd: Optional[str], 
    message: discord.Message, 
    channel_id: str, 
    parsed: Optional[Dict], 
    client_discord: discord.Client, 
    client_genai, 
    model_id: str, 
    model_id_flash: str, 
    domain_data: Dict
) -> Optional[str]:
    """
    중앙 명령어 처리 함수 (Pure Registry)
    """
    arg_content = parsed.get('content', '') if parsed else ""
    
    ctx = CommandContext(
        message=message,
        client=client_discord,
        genai_client=client_genai,
        model_id=model_id,
        channel_id=channel_id,
        user_id=str(message.author.id),
        trigger=cmd,
        args=arg_content.split(),
        raw_args=arg_content
    )
    
    # 1. New Registry Dispatch
    return await registry.dispatch(ctx)

