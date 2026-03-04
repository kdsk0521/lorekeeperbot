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

def _split_lore_chunks(lore_text: str, min_len: int = 50) -> list:
    """로어 텍스트를 섹션 단위로 청크 분할 (V2).

    V1 대비 개선:
    - 구분선(===, ---) 제거 → 노이즈 청크 방지
    - 메이저 섹션(N. Title / ## Title) 단위 그룹화
    - 대형 섹션(>4000자) 서브섹션에서 자동 분할
    - 소형 섹션(<200자) 인접 병합
    - 라벨: 섹션 헤더 기반
    - 섹션 미검출 시 문단 기반 폴백
    """
    if not lore_text or not lore_text.strip():
        return []

    _MAX_CHUNK = 4000
    _MIN_CHUNK = 200

    # 구분선: ===, ---, ***, ~~~ (3자 이상, 내용 없는 줄)
    _SEP = re.compile(r'^[\s]*[=\-\*~]{3,}[\s]*$')
    # 메이저 헤더: "1. TITLE" / "2.3.1 Title" / "SECTION 1:" / "# Title" / "## Title"
    _MAJOR = re.compile(
        r'^(?:\d+\.[\d.]*\s+[A-Z\uAC00-\uD7A3]|SECTION\s+\d+|#{1,2}\s+)'
    )
    # 마이너 헤더: "[1.1] Sub" / "--- Title ---" / "### Sub"
    _MINOR = re.compile(
        r'^(?:\[[\d.]+\]\s|---\s+.+\s+---|#{3,}\s+)'
    )

    def _label(text: str) -> str:
        s = text.lstrip('#').strip().rstrip(':').strip()
        return s[:80] if s else "Section"

    # --- Step 1: 구분선 제거 ---
    lines = [l for l in lore_text.split('\n') if not _SEP.match(l)]

    # --- Step 2: 메이저 헤더 기준 섹션 분리 ---
    sections = []
    buf = []
    cur_label = ""

    for line in lines:
        stripped = line.strip()
        indent = len(line) - len(line.lstrip())
        if indent <= 1 and stripped and _MAJOR.match(stripped):
            # 이전 버퍼 플러시
            content = '\n'.join(buf).strip()
            if content and len(content) >= min_len:
                sections.append({"label": cur_label or _label(content), "content": content})
            buf = [line]
            cur_label = _label(stripped)
        else:
            if not cur_label and stripped:
                cur_label = _label(stripped)
            buf.append(line)

    # 마지막 버퍼
    content = '\n'.join(buf).strip()
    if content and len(content) >= min_len:
        sections.append({"label": cur_label or _label(content), "content": content})

    # --- Step 2b: 폴백 — 섹션 1개 + 대형이면 문단 분할 ---
    if len(sections) <= 1 and sections and len(sections[0]["content"]) > _MAX_CHUNK:
        sections = _chunk_by_paragraph(sections[0]["content"], min_len, _MAX_CHUNK)

    # --- Step 3: 대형 섹션 서브헤더에서 분할 ---
    split_result = []
    for sec in sections:
        if len(sec["content"]) <= _MAX_CHUNK:
            split_result.append(sec)
        else:
            split_result.extend(
                _chunk_split_minor(sec, _MINOR, min_len, _MAX_CHUNK)
            )

    # --- Step 4: 소형 섹션 병합 ---
    merged = []
    for sec in split_result:
        if merged and len(merged[-1]["content"]) < _MIN_CHUNK:
            merged[-1]["content"] += "\n\n" + sec["content"]
            if len(merged[-1]["label"]) < 50:
                merged[-1]["label"] += " + " + sec["label"]
        else:
            merged.append(sec)
    if len(merged) > 1 and len(merged[-1]["content"]) < _MIN_CHUNK:
        merged[-2]["content"] += "\n\n" + merged[-1]["content"]
        merged.pop()

    # --- Step 5: 인덱싱 ---
    for i, c in enumerate(merged):
        c["index"] = i
        c["label"] = c["label"][:80]
    return merged


def _chunk_split_minor(section: dict, minor_re, min_len: int, max_chunk: int) -> list:
    """대형 섹션을 마이너 헤더([N.N], --- Title ---, ###)에서 분할."""
    parent = section["label"]
    lines = section["content"].split('\n')
    parts = []
    buf = []
    sub_label = ""

    def _lbl(text):
        # [N.N] pattern → "N.N TITLE"
        m = re.match(r'\[([\d.]+)\]\s*(.*)', text)
        if m:
            return f"{m.group(1)} {m.group(2).strip().rstrip(':').strip()}"[:60]
        # --- Title --- pattern → "Title"
        m = re.match(r'---\s+(.+?)\s+---', text)
        if m:
            return m.group(1).strip()[:60]
        return text.lstrip('#').strip().rstrip(':').strip()[:60]

    for line in lines:
        stripped = line.strip()
        if stripped and minor_re.match(stripped) and buf:
            content = '\n'.join(buf).strip()
            if content and len(content) >= min_len:
                lbl = f"{parent} > {sub_label}" if sub_label else parent
                parts.append({"label": lbl, "content": content})
            buf = [line]
            sub_label = _lbl(stripped)
        else:
            if not sub_label and stripped:
                sub_label = _lbl(stripped)
            buf.append(line)

    if buf:
        content = '\n'.join(buf).strip()
        if content and len(content) >= min_len:
            lbl = f"{parent} > {sub_label}" if sub_label else parent
            parts.append({"label": lbl, "content": content})

    # 실제 분할이 없었으면 (parts==1) 부모 라벨 유지 — 중복 방지
    if len(parts) == 1:
        parts[0]["label"] = parent

    # 마이너 분할로도 부족하면 문단 폴백
    final = []
    for p in (parts or [section]):
        if len(p["content"]) > max_chunk:
            final.extend(_chunk_by_paragraph(p["content"], min_len, max_chunk, p["label"]))
        else:
            final.append(p)
    return final


def _chunk_by_paragraph(text: str, min_len: int, max_chunk: int, parent_label: str = "") -> list:
    """문단(\n\n) 기반 폴백 분할. 섹션 구조 미검출 시 사용."""
    paragraphs = re.split(r'\n{2,}', text)
    chunks = []
    buf = ""

    def _lbl(t):
        first = t.split('\n')[0].strip().lstrip('#').strip().rstrip(':').strip()
        return first[:80] if first else "Section"

    for para in paragraphs:
        para = para.strip()
        if not para:
            continue
        if buf and len(buf) + len(para) + 2 > max_chunk:
            if len(buf) >= min_len:
                chunks.append({"label": parent_label or _lbl(buf), "content": buf})
            buf = para
        else:
            buf = buf + "\n\n" + para if buf else para

    if buf and len(buf) >= min_len:
        chunks.append({"label": parent_label or _lbl(buf), "content": buf})

    # 다중 파트일 때 의미있는 첫 줄 기반 서브라벨로 구분
    if parent_label and len(chunks) > 1:
        parent_prefix = parent_label.split('>')[0].strip()[:30]
        for c in chunks:
            for line in c["content"].split('\n'):
                line = line.strip()
                if line and line[:30] != parent_prefix:
                    c["label"] = f"{parent_label} > {line[:40]}"
                    break
    return chunks

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
        domain_manager.update_npc(channel_id, name, {"description": desc, "source": "session", "status": "Active"})
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
             
        genres = domain_manager.get_active_genre_list(channel_id)
        tone = domain_manager.get_custom_tone(channel_id)

        genre_text = ", ".join(genres) if genres else "none"
        msg = f"📜 **로어 정보**\nLength: {len(lore):,} chars\nNPCs: {len(npcs)}명\nGenres: {genre_text}"
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
            # [LoreAnalyzer V1] Unified Analysis — Flash 사용 (메타데이터 추출, Pro 안전 필터 회피)
            unified_res = await cognition.analyze_lore_unified(ctx.genai_client, config.MODEL_ID_FLASH, full_content)

            if not unified_res or not any(unified_res.get(k) for k in ("npcs", "genres", "lore_summary")):
                logger.warning("[LoreAnalyzer] 분석 결과 비어있음 — 로어 텍스트만 저장")
                if file_text:
                    domain_manager.set_lore(channel_id, full_content)
                else:
                    domain_manager.append_lore(channel_id, full_content)
                lore_chunks = _split_lore_chunks(full_content)
                if lore_chunks:
                    domain_manager.set_lore_chunks(channel_id, lore_chunks)
                await msg.edit(content="⚠️ **로어 분석 실패** — 텍스트는 저장되었으나 NPC/장르/이변 추출에 실패했습니다. 로어를 다시 업로드하거나 분량을 나누어 시도해 주세요.")
                return

            extracted_npcs = unified_res.get("npcs", [])
            pc_info = unified_res.get("pc_info")
            genre_res = unified_res.get("genres", {})
            lore_summary_data = unified_res.get("lore_summary", {})
            
            # 1. Update NPCs — Flash 메타데이터 + 로어 원문 프로필 병합
            if extracted_npcs:
                # 로어 원문에서 NPC 섹션 파싱 (Flash 요약 대신 원문 보존)
                npc_names = [n.get("name", "") for n in extracted_npcs if n.get("name")]
                full_sections = npc_manager.extract_npc_sections_from_lore(full_content, npc_names)
                for npc in extracted_npcs:
                    npc_name = npc.get("name", "")
                    if npc_name and npc_name in full_sections:
                        npc["description"] = full_sections[npc_name]
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
                     pc_msg += f"\n✅ 캐릭터 업데이트: {', '.join(updated_names)} (특질 및 설정 적용)"
            
            # 3. Update Genre (3-Layer) + mechanic_profile
            # [Guard] Flash가 레이어 간 태그를 교차 배치하는 경우 코드 레벨에서 보정
            _STAGE_TAGS = {"high_fantasy", "wuxia", "cyberpunk", "post_apocalypse", "space_opera", "modern"}
            _FLAVOR_TAGS = {"urban_fantasy", "steampunk", "cosmic_horror", "game_system"}
            _TONE_TAGS = {"noir", "comedy", "romance", "drama"}

            raw_ws = genre_res.get("world_setting", []) or []
            raw_st = genre_res.get("style_tech", []) or []
            raw_nt = genre_res.get("narrative_tone", []) or []
            all_tags = [t for t in (raw_ws + raw_st + raw_nt) if isinstance(t, str)]

            world_setting = [t for t in all_tags if t in _STAGE_TAGS][:2]
            style_tech = [t for t in all_tags if t in _FLAVOR_TAGS][:2]
            narrative_tone = [t for t in all_tags if t in _TONE_TAGS][:2]
            # 알 수 없는 태그 → 원래 레이어에 유지
            for t in all_tags:
                if t not in _STAGE_TAGS and t not in _FLAVOR_TAGS and t not in _TONE_TAGS:
                    if t in raw_ws: world_setting.append(t)
                    elif t in raw_st: style_tech.append(t)
                    elif t in raw_nt: narrative_tone.append(t)

            if set(world_setting) != set(raw_ws) or set(style_tech) != set(raw_st) or set(narrative_tone) != set(raw_nt):
                logger.warning(f"[Genre Fix] 교차 배치 보정: {raw_ws}/{raw_st}/{raw_nt} → {world_setting}/{style_tech}/{narrative_tone}")

            from config import build_mechanic_profile
            mechanic_profile = build_mechanic_profile(narrative_tone, style_tech)
            genre_data = {
                "layers": {
                    "world_setting": world_setting,
                    "style_tech": style_tech,
                    "narrative_tone": narrative_tone,
                },
                "atmosphere_guide": genre_res.get("atmosphere_guide", ""),
                "mechanic_profile": mechanic_profile,
            }
            domain_manager.set_active_genres(channel_id, genre_data)
            domain_manager.set_custom_tone(channel_id, genre_res.get("atmosphere_guide"))
            
            # 4. Update Lore Summary (Enriched V2)
            locations = lore_summary_data.get('locations', [])
            loc_str = ', '.join(l.get('name', str(l)) if isinstance(l, dict) else str(l) for l in locations) if isinstance(locations, list) else str(locations)
            rules = lore_summary_data.get('rules', [])
            rules_str = '\n'.join(f"  - {r}" for r in rules) if rules else ""
            raw_seeds = lore_summary_data.get('anomaly_seeds', [])
            seed_names = [s.get('name', str(s)) if isinstance(s, dict) else str(s) for s in raw_seeds] if isinstance(raw_seeds, list) else []
            summary_text = f"테마: {lore_summary_data.get('theme', '')}\n이변 징후: {', '.join(seed_names)}\n공간: {loc_str}"
            if rules_str:
                summary_text += f"\n규칙:\n{rules_str}"
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

            # 6. Chunk splitting (V5 — 선택적 주입용)
            lore_chunks = _split_lore_chunks(full_content)
            if lore_chunks:
                domain_manager.set_lore_chunks(channel_id, lore_chunks)

            if file_text:
                domain_manager.set_lore(channel_id, full_content)
            else:
                domain_manager.append_lore(channel_id, full_content)

            # Formatted Output (Match User's Legacy Format)
            genre_summary = f"{genre_res.get('world_setting', [])} / {genre_res.get('style_tech', [])} / {genre_res.get('narrative_tone', [])}"
            
            # [MODIFIED] Show ALL NPC Names in confirmation
            npc_names = [f"`{n['name']}`" for n in extracted_npcs if n.get('name')]
            npc_list_str = ", ".join(npc_names)
            
            anomaly_seeds = lore_summary_data.get('anomaly_seeds', [])
            anomaly_str = ", ".join(f"`{s}`" for s in anomaly_seeds) if anomaly_seeds else "없음"
            rules_count = len(lore_summary_data.get('rules', []))
            factions_count = len(lore_summary_data.get('factions', []))
            lore_extra = ""
            if rules_count or factions_count:
                lore_extra = f"\n\n📜 **세계 규칙** {rules_count}개 | **세력** {factions_count}개 추출"
            await msg.edit(content=f"✅ **로어 분석 완료**\n\n👥 **NPC: {len(extracted_npcs)}명 식별**\n{npc_list_str}{pc_msg}\n\n🌍 **장르/톤**\n{genre_summary}\n\n🌪️ **이변 징후** ({len(anomaly_seeds)}개)\n{anomaly_str}{lore_extra}")

        except Exception as e:
            import traceback
            logger.error(f"Unified Lore Analysis Failed: {e}\n{traceback.format_exc()}")
            await msg.edit(content=f"⚠️ 분석 오류: {e}")
            if file_text:
                domain_manager.set_lore(channel_id, full_content)
            else:
                domain_manager.append_lore(channel_id, full_content)
    else:
        if file_text:
            domain_manager.set_lore(channel_id, full_content)
        else:
            domain_manager.append_lore(channel_id, full_content)
        domain_manager.set_event_lore_summary(channel_id, full_content[:1000])
        await msg.edit(content="📜 저장 완료 (AI 미사용 - 단순 요약)")



@registry.register("info", category="Player", aliases=["내정보", "me", "desc", "설명", "정보"], description="캐릭터 정보 확인 / `!설명 [내용/파일]`으로 PC 설정 입력")
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
                analysis = await cognition.analyze_character_sheet(ctx.genai_client, config.MODEL_ID_FLASH, full_arg)
                if analysis:
                    # Save as PC Template (always — so apply uses fresh data)
                    domain_manager.set_default_pc_info(ctx.channel_id, analysis)
                    domain_manager.apply_pc_info_to_user(ctx.channel_id, uid)

                    # Sync others if name matches
                    updated_uids = domain_manager.sync_matching_participants(ctx.channel_id, analysis)
                    other_uids = [u for u in updated_uids if u != uid]
                    sync_info = f"\n(동일 캐릭터 사용자 {len(other_uids)}명 동시 업데이트)" if other_uids else ""

                    await status_msg.edit(content=f"✅ **캐릭터 설정 동기화 완료**\n이름: {analysis.get('name', '유지')}\n특성: {len(analysis.get('passives', []))}개 추출\n소지품/설정 데이터가 시스템에 적용되었습니다.{sync_info}")
                    return
            
            # Fallback for no-AI or Fail
            domain_manager.update_participant(ctx.channel_id, ctx.message.author, desc=full_arg[:500])
            await status_msg.edit(content=f"📝 **설명 업데이트 완료** (단순 텍스트 저장)")
            return

    # 1. Profile (Mask, Desc, etc)
    mask_name = p_data.get("mask", "Unknown")
    desc = p_data.get("desc", "")

    # Appearance/Personality from AI Memory if available
    mem = p_data.get("ai_memory", {})
    appearance = mem.get("appearance", "")
    description = mem.get("description", "")
    background = mem.get("background", "")

    has_ai_profile = appearance or description or background
    msg = [f"🎭 **{mask_name}**"]
    if desc and not has_ai_profile:
        msg.append(f"> {desc}")
    if appearance: msg.append(f"**외모:** {appearance}")
    if description: msg.append(f"**설명:** {description}")
    if background: msg.append(f"**배경:** {background}")
    status_text = game_character.format_status_effects(p_data.get("status_effects", [])) or "정상"
    msg.append(f"**상태:** {status_text}")
    
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

    # 4. Vigor/Composure
    vigor_data = mem.get("vigor", mem.get("mental", {}))
    composure_data = mem.get("composure", {})
    v_val = vigor_data.get("value", 100)
    c_val = composure_data.get("value", 100)
    v_info = game_character.get_mental_info(v_val)
    c_info = game_character.get_composure_info(c_val)
    msg.append(f"\n**💪 기력:** {v_info['emoji']} **{v_info['name']}** ({v_val}/100)")
    msg.append(f"**😌 평정:** {c_info['emoji']} **{c_info['name']}** ({c_val}/100)")

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




@registry.register("npc", category="World", aliases=["엔피씨", "addnpc", "npc정보", "npc추가"], description="NPC 관리 (조회/추가/삭제/보이스카드)")
async def cmd_npc(ctx: CommandContext) -> None:
    """!npc [이름] 조회 | !npc add [이름] [설명] | !npc remove [이름] | !npc voicecard [이름]"""
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

    # 2. Subcommand: remove / 삭제
    arg = ctx.raw_args
    channel_id = ctx.channel_id
    if arg:
        parts = arg.strip().split(None, 1)
        if parts[0].lower() in ('remove', 'delete', 'del', '삭제', '제거'):
            if len(parts) < 2 or not parts[1].strip():
                await ctx.send("⚠️ 사용법: `!npc remove [이름]`")
                return
            target_name = parts[1].strip()
            success, matched_key = domain_manager.delete_npc(channel_id, target_name)
            if success:
                display = matched_key or target_name
                await ctx.send(f"🗑️ NPC **{display}** 삭제 완료.")
            else:
                # 유사 이름 후보 제시
                npcs = domain_manager.get_npcs(channel_id)
                nl = target_name.lower()
                candidates = [k for k in npcs if nl in k.lower() or k.lower() in nl]
                hint = ""
                if candidates:
                    hint = f"\n💡 유사한 NPC: {', '.join(candidates[:5])}"
                await ctx.send(f"⚠️ NPC '{target_name}' 정보를 찾을 수 없습니다.{hint}")
            return

        # Subcommand: voicecard / 보이스카드 재추출
        if parts[0].lower() in ('voicecard', 'vc', '보이스카드', '보이스'):
            if not ctx.genai_client:
                await ctx.send("⚠️ AI 클라이언트가 초기화되지 않았습니다.")
                return
            target = parts[1].strip() if len(parts) > 1 else None
            npcs = domain_manager.get_npcs(channel_id)
            targets = {}
            if target:
                key = domain_manager._find_npc_key(npcs, target)
                if key:
                    targets[key] = npcs[key]
                else:
                    await ctx.send(f"⚠️ NPC '{target}' 정보를 찾을 수 없습니다.")
                    return
            else:
                # 인자 없으면 전체 일괄 재추출 (description 300자 이상)
                targets = {k: v for k, v in npcs.items()
                           if len(v.get("description") or v.get("desc", "")) > 300}
            if not targets:
                await ctx.send("Voice Card가 필요한 NPC가 없습니다.")
                return
            await ctx.send(f"🎙️ Voice Card 추출 시작... ({len(targets)}명)")
            vc_count = 0
            for npc_name, npc_data in targets.items():
                desc = npc_data.get("description") or npc_data.get("desc", "")
                voice_card = await npc_manager.extract_voice_card(
                    ctx.genai_client, config.MODEL_ID_FLASH, npc_name, desc
                )
                if not voice_card:
                    voice_card = npc_manager._build_fallback_voice_card(npc_name, npc_data)
                if voice_card:
                    npc_data["voice_card"] = voice_card
                    domain_manager.update_npc(channel_id, npc_name, npc_data)
                    vc_count += 1
            await ctx.send(f"🎙️ Voice Card 추출 완료: {vc_count}/{len(targets)}명 성공")
            return

    # 3. Batch Processing Logic (Restored from handle_npc_command)
    raw_lines = (arg + "\n" + file_text).strip().splitlines() if arg else (file_text or "").strip().splitlines()
    processed_count = 0

    # If explicit "addnpc" or batch mode implied
    if ctx.trigger in ['addnpc', 'npc추가'] or (len(raw_lines) > 1) or (file_text):
        if (not raw_lines or not raw_lines[0].strip()) and not file_text:
             await ctx.send("⚠️ 등록할 내용이 없습니다. `!npc추가 [이름]: [설명]` 또는 파일 첨부.")
             return

        last_name = None

        # --- Unified NPC Batch Parser ---
        # Supports: markdown (## Name), simple (이름: 설명), deep profile (Name: X), rich structured ([NPC NAME]: X + ==== separators)
        separator_pat = re.compile(r'^[=\-]{3,}$')
        name_pat = re.compile(
            r'^\s*(?:\[?\s*(?:NPC\s*NAME|Name|이름)\s*\]?\s*)[:\s]\s*(.+)$',
            re.IGNORECASE
        )
        heading_pat = re.compile(r'^##(?!#)\s+(.+)$')  # ## Name (h2 only, not ### h3)

        # Phase 0: Detect markdown heading format (## NPC Name)
        has_headings = any(heading_pat.match(l.strip()) for l in raw_lines if l.strip())

        if has_headings:
            # Markdown mode: ## headings split NPC blocks
            blocks = []
            current_name = None
            current_lines = []

            for line in raw_lines:
                stripped = line.strip()
                hm = heading_pat.match(stripped)
                if hm:
                    if current_name:
                        blocks.append((current_name, current_lines))
                    current_name = hm.group(1).strip()
                    current_lines = []
                elif current_name is not None:
                    current_lines.append(line.rstrip())

            if current_name:
                blocks.append((current_name, current_lines))

            # Helper: find existing NPC by alias match (e.g., "Limi" matches "리미 (Limi)")
            existing_npcs = domain_manager.get_npcs(channel_id)
            def _find_existing(new_name: str) -> str:
                if new_name in existing_npcs:
                    return new_name
                nl = new_name.lower().strip()
                for en in existing_npcs:
                    m = re.search(r'\(([^)]+)\)', en)
                    if m and m.group(1).strip().lower() == nl:
                        return en
                return new_name  # no match → use as-is

            for name, desc_lines in blocks:
                while desc_lines and not desc_lines[0].strip():
                    desc_lines.pop(0)
                while desc_lines and not desc_lines[-1].strip():
                    desc_lines.pop()
                desc = "\n".join(desc_lines)
                # Extract identity summary for list display
                id_fields = {}
                for dl in desc_lines:
                    dl_clean = dl.strip().lstrip("- ").strip()
                    if ":" in dl_clean:
                        fk, fv = dl_clean.split(":", 1)
                        fk_lower = fk.strip().lower()
                        fv = fv.strip()
                        if fk_lower in ("species", "종족") and fv:
                            id_fields.setdefault("species", fv)
                        elif fk_lower in ("rank/role", "role", "역할") and fv:
                            id_fields.setdefault("role", fv)
                        elif fk_lower in ("affiliation", "소속") and fv:
                            id_fields.setdefault("affiliation", fv)
                summary_items = []
                if "species" in id_fields:
                    summary_items.append(id_fields["species"])
                if "role" in id_fields:
                    summary_items.append(id_fields["role"])
                elif "affiliation" in id_fields:
                    summary_items.append(id_fields["affiliation"])
                summary = " / ".join(summary_items) if summary_items else ""
                # Merge into existing NPC if alias matches (e.g., "Limi" → "리미 (Limi)")
                target_name = _find_existing(name)
                npc_data = {"description": desc, "source": "manual", "status": "Active"}
                if summary:
                    npc_data["summary"] = summary
                domain_manager.update_npc(channel_id, target_name, npc_data)
                processed_count += 1
                last_name = target_name

        # Phase 1: Detect if structured (name declarations exist)
        elif any(name_pat.match(l.strip()) for l in raw_lines if l.strip()):
            # Block mode: name declarations split NPC blocks
            blocks = []  # [(name, [desc_lines])]
            current_name = None
            current_lines = []

            for line in raw_lines:
                stripped = line.strip()
                if not stripped or separator_pat.match(stripped):
                    continue

                nm = name_pat.match(stripped)
                if nm:
                    if current_name:
                        blocks.append((current_name, current_lines))
                    current_name = nm.group(1).strip()
                    current_lines = []
                elif current_name:
                    current_lines.append(stripped)

            if current_name:
                blocks.append((current_name, current_lines))

            for name, desc_lines in blocks:
                desc = "\n".join(desc_lines)
                domain_manager.update_npc(channel_id, name, {"description": desc, "source": "manual", "status": "Active"})
                processed_count += 1
                last_name = name
        else:
            # Simple mode: each "key: value" line = separate NPC, continuations append
            for line in raw_lines:
                stripped = line.strip()
                if not stripped or separator_pat.match(stripped):
                    continue

                if ":" in stripped:
                    key, val = stripped.split(":", 1)
                    clean_key = key.lstrip("*-> ").strip()
                    val = val.strip()
                    if clean_key and val:
                        domain_manager.update_npc(channel_id, clean_key, {"description": val, "source": "manual", "status": "Active"})
                        processed_count += 1
                        last_name = clean_key
                        continue

                # Continuation line
                if last_name:
                    curr_npc = domain_manager.get_npc(channel_id, last_name)
                    if curr_npc:
                        new_desc = (curr_npc.get("description") or curr_npc.get("desc", "")) + "\n" + stripped
                        domain_manager.update_npc(channel_id, last_name, {"description": new_desc, "source": "manual", "status": "Active"})

        if processed_count > 0:
            if processed_count == 1:
                await ctx.send(f"👥 **NPC 등록:** {last_name}")
            else:
                await ctx.send(f"👥 **NPC 일괄 등록 완료:** 총 {processed_count}명")

            # Voice Card extraction (Flash API)
            if ctx.genai_client:
                registered_npcs = domain_manager.get_npcs(channel_id)
                vc_count = 0
                for npc_name in list(registered_npcs.keys())[-processed_count:]:
                    npc_data = registered_npcs[npc_name]
                    desc = npc_data.get("description") or npc_data.get("desc", "")
                    if desc and len(desc) > 300 and not npc_data.get("voice_card"):
                        voice_card = await npc_manager.extract_voice_card(
                            ctx.genai_client, config.MODEL_ID_FLASH, npc_name, desc
                        )
                        if not voice_card:
                            voice_card = npc_manager._build_fallback_voice_card(npc_name, npc_data)
                        if voice_card:
                            npc_data["voice_card"] = voice_card
                            domain_manager.update_npc(channel_id, npc_name, npc_data)
                            vc_count += 1
                if vc_count > 0:
                    await ctx.send(f"🎙️ Voice Card 추출 완료 ({vc_count}명)")
        else:
             await ctx.send("⚠️ 유효한 형식을 찾을 수 없습니다. (예: `이름: 설명` 또는 `[NPC NAME]: 이름`)")
        return

    # Look up NPC
    if not arg:
        npcs = domain_manager.get_npcs(channel_id)
        if not npcs:
            await ctx.send("👥 등록된 NPC가 없습니다.")
            return
        
        # List all
        def _npc_preview(d: dict) -> str:
            if d.get("summary"):
                return d["summary"][:60]
            desc = d.get("description") or d.get("desc", "-")
            for line in desc.split("\n"):
                s = line.strip()
                if not s or s.startswith("#"):
                    continue
                cl = s.lstrip("- ").strip()
                if cl.lower().startswith(("name:", "alias:", "이름:")):
                    continue
                return cl[:60]
            return desc[:60]
        name_list = [f"• **{n}**: {_npc_preview(d)}" for n, d in npcs.items()]
        await send_long_message(ctx.message.channel, "👥 **NPC 목록**\n" + "\n".join(name_list))
    else:
        # Specific NPC
        npc = domain_manager.get_npc(channel_id, arg.strip())
        if npc:
            # 실제 매칭된 키 이름 표시 (부분 매칭 시 전체 이름)
            npcs = domain_manager.get_npcs(channel_id)
            display_name = domain_manager._find_npc_key(npcs, arg.strip()) or arg
            msg = [f"👤 **{display_name}**"]
            if npc.get('gender') or npc.get('race'):
                meta = []
                if npc.get('gender'): meta.append(npc.get('gender'))
                if npc.get('race'): meta.append(npc.get('race'))
                msg.append(f"({', '.join(meta)})")
            
            desc_text = npc.get("description") or npc.get("desc", "")
            if desc_text:
                # 긴 프로필은 앞부분만 표시
                preview = desc_text[:500] + ("..." if len(desc_text) > 500 else "")
                msg.append(preview)

            if npc.get('appearance'): msg.append(f"**외양:** {npc.get('appearance')}")
            if npc.get('background'): msg.append(f"**배경:** {npc.get('background')}")
            
            await send_long_message(ctx.message.channel, "\n".join(msg))
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


@registry.register("retry", category="System", aliases=["다시", "reroll", "재판정"], description="재생성 / 인풋 수정 후 재생성")
async def cmd_retry(ctx: CommandContext) -> None:
    """!다시 [수정 입력] — 빈칸이면 같은 입력으로 리롤, 내용 있으면 입력 교체 후 재생성"""
    from orchestration import get_orchestration_runtime
    orchestration = get_orchestration_runtime(ctx.genai_client, ctx.model_id, config.MODEL_ID_FLASH)

    if not orchestration:
        await ctx.send("⚠️ AI 서비스가 초기화되지 않았습니다.")
        return

    edited_input = ctx.raw_args.strip() or None
    await orchestration.retry_last(ctx.message, ctx.channel_id, edited_input=edited_input)


@registry.register("mode", category="System", aliases=["모드"], description="AI 응답 모드 변경")
async def cmd_mode(ctx: CommandContext) -> None:
    """!모드 [자동/수동]"""
    arg = ctx.raw_args.strip()
    
    if not arg:
        curr = domain_manager.get_response_mode(ctx.channel_id)
        mode_kr = {'auto': '자동', 'waiting': '수동', 'assist': '보조'}.get(curr, curr)
        await ctx.send(f"⚙️ 현재 모드: **{mode_kr}**\n사용법: `!모드 [자동/수동]`")
        return

    mode_map = {'자동': 'auto', '수동': 'waiting', 'auto': 'auto', 'waiting': 'waiting', 'manual': 'waiting', 'assist': 'assist'}
    mode = mode_map.get(arg.lower(), arg.lower())
    mode_kr = {'auto': '자동', 'waiting': '수동', 'assist': '보조'}.get(mode, mode)

    domain_manager.set_response_mode(ctx.channel_id, mode)
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
    all_mods = [("judgment", "판정"), ("doom", "둠"), ("anomaly", "이변"), ("mental", "기력")]

    # 일괄 ON
    if arg in ['on', '켜기', 'true', 'all']:
        for code, _ in all_mods:
            domain_manager.toggle_module(ctx.channel_id, code, True)
        await ctx.send("✅ **모든 모듈이 활성화되었습니다.**\n• 판정 ✅\n• 둠 ✅\n• 이변 ✅\n• 기력 ✅")
        return

    # 일괄 OFF
    if arg in ['off', '끄기', 'false', 'none']:
        for code, _ in all_mods:
            domain_manager.toggle_module(ctx.channel_id, code, False)
        await ctx.send("❌ **모든 모듈이 비활성화되었습니다.**\n• 판정 ❌\n• 둠 ❌\n• 이변 ❌\n• 기력 ❌")
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

@registry.register("anomaly", category="System", aliases=["이변", "비일상", "abnormal"], description="이변 모듈 제어 및 징후 조회")
async def cmd_toggle_anomaly(ctx: CommandContext) -> None:
    arg = ctx.raw_args.strip().lower()
    # on/off는 토글로 위임
    if arg in ['on', '켜기', 'true', 'off', '끄기', 'false']:
        await _handle_module_toggle(ctx, "anomaly", "이변")
        return

    # 인자 없음: 상태 + 이변 징후 조회
    active = domain_manager.get_active_modules(ctx.channel_id)
    status = "✅ ON" if "anomaly" in active else "❌ OFF"

    lore_data = domain_manager.get_lore_summary_data(ctx.channel_id)
    seeds = lore_data.get("anomaly_seeds", [])

    msg = f"🌪️ **이변 모듈**: {status}\n"
    if seeds:
        msg += f"\n**등록된 이변 징후** ({len(seeds)}개):\n"
        msg += "\n".join(f"• `{s}`" for s in seeds)
    else:
        msg += "\n*(이변 징후 없음 — 로어 분석 시 자동 추출됩니다)*"

    msg += f"\n\n사용법: `!이변 on/off`"
    await ctx.send(msg)

@registry.register(
    "기력모듈",
    category="System",
    aliases=["멘탈모듈", "mentalmod", "mental_mod", "vigor_mod", "기력mod"],
    description="기력 모듈 제어"
)
async def cmd_toggle_mental(ctx: CommandContext) -> None:
    await _handle_module_toggle(ctx, "mental", "기력")

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
        # general: 질문/확인 등 → 서사 생성 불필요, 루카가 답변
        return None


@registry.register("ooc", category="Analysis", aliases=["OOC", "메타", "루카"], description="루카 (OOC 도우미) 모드 토글")
async def cmd_ooc(ctx: CommandContext) -> None:
    """!ooc - 루카 (OOC 도우미) 모드 ON/OFF 토글"""
    current = domain_manager.get_ooc_mode(ctx.channel_id)
    new_state = not current
    domain_manager.set_ooc_mode(ctx.channel_id, new_state)
    if new_state:
        await ctx.send("💬 **루카 모드 ON** — 루카가 대화에 참여합니다.\n> 모든 메시지가 OOC 대화로 처리됩니다. 해제: `!ooc`")
    else:
        await ctx.send("🎭 **루카 모드 OFF** — 서사 모드로 복귀합니다.")

@registry.register(
    "기력",
    category="Player",
    aliases=["멘탈", "mental", "vigor"],
    description="기력/평정 조회 및 설정"
)
async def cmd_mental(ctx: CommandContext) -> None:
    """!기력 [기력값] [평정값] - 기력/평정 수치 설정 (0-100)"""
    uid = ctx.user_id
    p_data = domain_manager.get_participant_data(ctx.channel_id, uid)

    if not p_data:
        await ctx.send("❌ 등록 필요 (`!가면`)")
        return

    mem = p_data.get("ai_memory", {})
    # Migration: old "mental" → vigor
    if "mental" in mem and "vigor" not in mem:
        old_val = mem["mental"].get("value", 100)
        mem["vigor"] = {"value": old_val, "last_delta": 0}
        mem["composure"] = {"value": old_val, "last_delta": 0}
        del mem["mental"]

    vigor = mem.get("vigor", {"value": 100, "last_delta": 0})
    composure = mem.get("composure", {"value": 100, "last_delta": 0})

    # [View Mode]
    if not ctx.args:
        v_val = vigor.get("value", 100)
        c_val = composure.get("value", 100)
        v_info = game_character.get_mental_info(v_val)
        c_info = game_character.get_composure_info(c_val)
        await ctx.send(
            f"💪 **기력:** {v_info['emoji']} **{v_info['name']}** ({v_val}/100)\n"
            f"> {v_info.get('desc', '')}\n"
            f"😌 **평정:** {c_info['emoji']} **{c_info['name']}** ({c_val}/100)\n"
            f"> {c_info.get('desc', '')}"
        )
        return

    # [Set Mode] — !기력 80 or !기력 80 70
    try:
        v_target = max(0, min(100, int(ctx.args[0])))
        c_target = max(0, min(100, int(ctx.args[1]))) if len(ctx.args) > 1 else composure.get("value", 100)

        mem.setdefault("vigor", {})["value"] = v_target
        mem["vigor"]["last_delta"] = 0
        mem.setdefault("composure", {})["value"] = c_target
        mem["composure"]["last_delta"] = 0

        p_data["ai_memory"] = mem
        domain_manager.save_participant_data(ctx.channel_id, uid, p_data)

        v_info = game_character.get_mental_info(v_target)
        c_info = game_character.get_composure_info(c_target)
        await ctx.send(
            f"💪 **기력 설정:** {v_target}/100 ({v_info['emoji']} {v_info['name']})\n"
            f"😌 **평정 설정:** {c_target}/100 ({c_info['emoji']} {c_info['name']})"
        )

    except ValueError:
        await ctx.send("⚠️ 올바른 숫자를 입력하세요. (예: `!기력 80` 또는 `!기력 80 70`)")


@registry.register("flashback", category="Player", aliases=["회상"], description="회상 선언 (과거 준비를 소급 선언)")
async def cmd_flashback(ctx: CommandContext) -> None:
    """!회상 [내용] - 회상 선언을 대기열에 등록. 다음 턴에 Theoria가 평가."""
    # 기력 모듈 활성 체크
    active_modules = domain_manager.get_active_modules(ctx.channel_id)
    if "mental" not in active_modules:
        await ctx.send("❌ 기력 모듈이 비활성 상태입니다. 회상을 사용하려면 기력 모듈을 활성화하세요.")
        return

    content = ctx.raw_args.strip()
    if not content:
        tiers = config.FLASHBACK_COST_TIERS
        await ctx.send(
            "🔮 **회상 시스템** — 과거의 준비를 소급 선언합니다.\n"
            f"사용법: `!회상 [선언 내용]`\n\n"
            f"**비용 (기력 차감)**\n"
            f"- **trivial** (면모 매칭): -{tiers['trivial']}\n"
            f"- **standard** (합리적): -{tiers['standard']}\n"
            f"- **bold** (대담한): -{tiers['bold']}\n\n"
            f"**규칙**: 상황/포지션 변경만 가능. 수치(기력/둠) 직접 변경 불가.\n"
            f"**예시**: `!회상 탈출 루트를 미리 확보해뒀다`\n"
            f"서사적으로도 가능: 대사/행동 중 자연스럽게 '사실 미리 ~해뒀다' 표현"
        )
        return

    # 대기열에 등록
    domain_manager.set_pending_flashback(ctx.channel_id, content, ctx.user_id)
    await ctx.send(f"🔮 **회상 대기 등록**: \"{content}\"\n다음 턴에 Theoria가 평가합니다.")




@registry.register("loadout", category="Player", aliases=["로드아웃", "장비설정"], description="세션 초기 준비 슬롯 설정")
async def cmd_loadout(ctx: CommandContext) -> None:
    """!로드아웃 [경장/표준/중장] — 세션 초기 준비 슬롯 설정"""
    import config as _cfg
    active_modules = domain_manager.get_active_modules(ctx.channel_id)
    if "mental" not in active_modules:
        await ctx.send("❌ 기력 모듈이 비활성 상태입니다.")
        return

    arg = ctx.raw_args.strip().lower()
    # Korean alias → key mapping
    alias_map = {"경장": "light", "표준": "standard", "중장": "heavy"}
    load_key = alias_map.get(arg, arg) if arg else ""

    if not load_key or load_key not in _cfg.LOADOUT_TYPES:
        types_desc = " / ".join(
            f"`{v['label']}({k})` — {v['slots']}슬롯" for k, v in _cfg.LOADOUT_TYPES.items()
        )
        current = domain_manager.get_loadout(ctx.channel_id, ctx.user_id)
        current_msg = ""
        if current:
            current_msg = (
                f"\n현재: **{current.get('label', current.get('load_type'))}** "
                f"({current.get('used_slots', 0)}/{current['total_slots']}슬롯 사용)"
            )
        await ctx.send(
            f"🎒 **로드아웃** — 준비 슬롯 설정\n"
            f"사용법: `!로드아웃 [유형]`\n{types_desc}{current_msg}\n\n"
            f"설정 후, 서사 중 '가방에서 ~를 꺼낸다'로 슬롯을 소비합니다."
        )
        return

    lt = _cfg.LOADOUT_TYPES[load_key]
    domain_manager.set_loadout(ctx.channel_id, ctx.user_id, load_key, lt["slots"], lt["label"])
    await ctx.send(f"🎒 로드아웃 설정: **{lt['label']}** ({lt['slots']}슬롯)")


@registry.register("reset_npcs", category="Admin", aliases=["엔피씨초기화", "npc_reset"], description="세션 NPC 초기화")
async def cmd_reset_npcs(ctx: CommandContext) -> None:
    """!reset_npcs"""
    if not domain_manager.is_session_locked(ctx.channel_id):
        # Optional: Check admin implementation if needed, for now allow
        pass

    count = npc_manager.clear_session_npcs(ctx.channel_id)
    await ctx.send(f"🧹 **세션 NPC 초기화 완료:** {count}명 삭제됨 (Lore NPC 유지)")


@registry.register("genre", category="World", aliases=["장르", "렌즈", "lens"], description="장르/렌즈 조회 및 수동 설정")
async def cmd_genre(ctx: CommandContext) -> None:
    """!장르 — 조회 / !장르 noir drama — 렌즈 설정 / !장르 초기화 — 리셋"""
    channel_id = ctx.channel_id
    raw = ctx.raw_args.strip() if ctx.raw_args else ""

    # 조회
    if not raw:
        genres = domain_manager.get_active_genres(channel_id)
        if isinstance(genres, dict):
            layers = genres.get("layers", {})
            stage = ", ".join(layers.get("world_setting", [])) or "—"
            flavor = ", ".join(layers.get("style_tech", [])) or "—"
            lens = ", ".join(layers.get("narrative_tone", [])) or "—"
            atmo = genres.get("atmosphere_guide", "") or ""
            mech = genres.get("mechanic_profile", {})
            primary = mech.get("primary_lens", "—") if mech else "—"
            msg = (
                f"🎭 **장르 설정**\n"
                f"A-Stage (세계): {stage}\n"
                f"B-Flavor (기법): {flavor}\n"
                f"C-Lens (톤): {lens}\n"
                f"Primary Lens: {primary}"
            )
            if atmo:
                msg += f"\nAtmosphere: {atmo}"
        else:
            flat = domain_manager.get_active_genre_list(channel_id)
            msg = f"🎭 **장르**: {', '.join(flat) if flat else '미설정'}"
        await ctx.send(msg)
        return

    # 초기화
    if raw in ("초기화", "reset", "clear"):
        domain_manager.set_active_genres(channel_id, {})
        domain_manager.set_custom_tone(channel_id, None)
        await ctx.send("🎭 장르 데이터 초기화됨.")
        return

    # 수동 설정 — 태그 분류
    _STAGE_TAGS = {"high_fantasy", "wuxia", "cyberpunk", "post_apocalypse", "space_opera", "modern"}
    _FLAVOR_TAGS = {"urban_fantasy", "steampunk", "cosmic_horror", "game_system"}
    _TONE_TAGS = {"noir", "comedy", "romance", "drama"}

    # 한국어 → 영어 매핑
    _KR_ALIAS = {
        # A-Stage
        "하이판타지": "high_fantasy", "판타지": "high_fantasy", "무협": "wuxia",
        "사이버펑크": "cyberpunk", "포스트아포칼립스": "post_apocalypse", "종말": "post_apocalypse",
        "스페이스오페라": "space_opera", "우주": "space_opera", "현대": "modern",
        # B-Flavor
        "어반판타지": "urban_fantasy", "도시판타지": "urban_fantasy",
        "스팀펑크": "steampunk", "코즈믹호러": "cosmic_horror", "우주공포": "cosmic_horror",
        "게임": "game_system",
        # C-Lens
        "느와르": "noir", "코미디": "comedy", "로맨스": "romance", "드라마": "drama",
    }

    raw_tags = [t.strip().lower() for t in raw.replace(",", " ").split() if t.strip()]
    tags = [_KR_ALIAS.get(t, t) for t in raw_tags]  # 한국어 → 영어 변환

    world_setting = [t for t in tags if t in _STAGE_TAGS]
    style_tech = [t for t in tags if t in _FLAVOR_TAGS]
    narrative_tone = [t for t in tags if t in _TONE_TAGS]
    unknown = [t for t in tags if t not in _STAGE_TAGS and t not in _FLAVOR_TAGS and t not in _TONE_TAGS]

    if unknown:
        all_valid = sorted(_STAGE_TAGS | _FLAVOR_TAGS | _TONE_TAGS)
        kr_list = ", ".join(sorted(_KR_ALIAS.keys()))
        await ctx.send(f"⚠️ 알 수 없는 태그: {', '.join(unknown)}\n유효 태그: {', '.join(all_valid)}\n한국어: {kr_list}")
        return

    from config import build_mechanic_profile
    mechanic_profile = build_mechanic_profile(narrative_tone, style_tech)

    # 기존 데이터 병합 — 입력한 레이어만 덮어쓰기
    existing = domain_manager.get_active_genres(channel_id)
    if isinstance(existing, dict):
        old_layers = existing.get("layers", {})
    else:
        old_layers = {}

    new_layers = {
        "world_setting": world_setting if world_setting else old_layers.get("world_setting", []),
        "style_tech": style_tech if style_tech else old_layers.get("style_tech", []),
        "narrative_tone": narrative_tone if narrative_tone else old_layers.get("narrative_tone", []),
    }

    genre_data = {
        "layers": new_layers,
        "atmosphere_guide": existing.get("atmosphere_guide", "") if isinstance(existing, dict) else "",
        "mechanic_profile": mechanic_profile,
    }
    domain_manager.set_active_genres(channel_id, genre_data)

    stage_str = ", ".join(new_layers["world_setting"]) or "—"
    flavor_str = ", ".join(new_layers["style_tech"]) or "—"
    lens_str = ", ".join(new_layers["narrative_tone"]) or "—"
    await ctx.send(
        f"🎭 **장르 설정됨**\n"
        f"A-Stage: {stage_str}\n"
        f"B-Flavor: {flavor_str}\n"
        f"C-Lens: {lens_str}\n"
        f"Primary Lens: {mechanic_profile.get('primary_lens', '—')}"
    )


@registry.register("rule", category="World", aliases=["룰", "규칙", "rules", "worldrules", "세계규칙"], description="세계 규칙 관리")
async def cmd_rule(ctx: CommandContext) -> None:
    """!룰 [추가/삭제/목록/초기화] [키워드] [내용]  — 파일 첨부 시 일괄 등록"""
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
            await ctx.send("📜 활성화된 특수 규칙이 없습니다.\n💡 출력 형식 규칙은 `!출력룰 목록`으로 확인하세요.")
            return

        msg = ["📜 **세계 규칙 목록**"]
        for k, v in rules.items():
            desc = v.get('desc', '') if isinstance(v, dict) else str(v)
            msg.append(f"- **{k}**: {desc}")
        msg.append("\n💡 출력 형식 규칙은 `!출력룰 목록`으로 확인하세요.")
        await send_long_message(ctx.message.channel, "\n".join(msg))
        return

    # 2. Add / Update
    if sub in ['add', '추가', 'set', '설정', 'a']:
        # 파일 첨부 → 일괄 등록 ("키워드: 설명" 또는 "키워드 - 설명" per line)
        if ctx.message.attachments:
            file_text, error = await read_attachment_text(ctx.message.attachments[0])
            if error:
                await ctx.send(error)
                return
            if not file_text:
                await ctx.send("⚠️ 파일 내용이 비어있습니다.")
                return
            for line in file_text.splitlines():
                line = line.strip()
                if not line or line.startswith('#'):
                    continue
                sep = None
                for s in [':', '-', '：']:
                    if s in line:
                        sep = s
                        break
                if sep:
                    key, desc = line.split(sep, 1)
                    key, desc = key.strip(), desc.strip()
                else:
                    parts = line.split(None, 1)
                    key = parts[0]
                    desc = parts[1] if len(parts) > 1 else ""
                if key:
                    rules[key] = {"desc": desc, "created_at": time.strftime('%Y-%m-%d')}
            w["location_rules"] = rules
            domain_manager.update_world_state(ctx.channel_id, w)
            await ctx.send("📜 **추가룰 등록 완료**")
            return

        if len(args) < 3:
            await ctx.send("⚠️ 사용법: `!룰 추가 [키워드] [설명]` 또는 파일 첨부")
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

    # 4. Reset
    if sub in ['reset', '초기화', 'clear']:
        w["location_rules"] = {}
        domain_manager.update_world_state(ctx.channel_id, w)
        await ctx.send("🗑️ **모든 규칙 초기화 완료**")
        return

    await ctx.send("⚠️ 사용법: `!룰 [목록/추가/삭제/초기화]` — 파일 첨부로 일괄 등록 가능")


@registry.register("outputrule", category="World", aliases=["출력룰", "출력규칙", "outputrules", "출력"], description="출력 형식 규칙 관리 (Recency 슬롯)")
async def cmd_output_rule(ctx: CommandContext) -> None:
    """!출력룰 [추가/삭제/목록/초기화] [키워드] [내용]  — 파일 첨부 시 일괄 등록
    출력 형식 지시(상태창, 포맷 등)를 Recency 영역에 주입합니다."""
    args = ctx.args
    if not args:
        sub = "list"
    else:
        sub = args[0].lower()

    w = domain_manager.get_world_state(ctx.channel_id)
    rules = w.get("output_rules", {})

    # 1. List
    if sub in ['list', '목록', '조회', 'l']:
        if not rules:
            await ctx.send("📋 활성화된 출력 규칙이 없습니다.")
            return
        msg = ["📋 **출력 규칙 목록** (Recency 슬롯 주입)"]
        for k, v in rules.items():
            desc = v.get('desc', '') if isinstance(v, dict) else str(v)
            preview = desc[:80] + "..." if len(desc) > 80 else desc
            msg.append(f"- **{k}**: {preview}")
        await send_long_message(ctx.message.channel, "\n".join(msg))
        return

    # 2. Add / Update
    if sub in ['add', '추가', 'set', '설정', 'a']:
        if ctx.message.attachments:
            file_text, error = await read_attachment_text(ctx.message.attachments[0])
            if error:
                await ctx.send(error)
                return
            if not file_text:
                await ctx.send("⚠️ 파일 내용이 비어있습니다.")
                return
            # 파일 전체를 하나의 출력 규칙으로 등록 (키워드 = 파일명)
            fname = ctx.message.attachments[0].filename.rsplit('.', 1)[0]
            rules[fname] = {"desc": file_text.strip(), "created_at": time.strftime('%Y-%m-%d')}
            w["output_rules"] = rules
            domain_manager.update_world_state(ctx.channel_id, w)
            await ctx.send(f"📋 **출력규칙 등록 완료:** [{fname}]")
            return

        if len(args) < 3:
            await ctx.send("⚠️ 사용법: `!출력룰 추가 [키워드] [설명]` 또는 파일 첨부")
            return

        key = args[1]
        desc = " ".join(args[2:])
        rules[key] = {"desc": desc, "created_at": time.strftime('%Y-%m-%d')}
        w["output_rules"] = rules
        domain_manager.update_world_state(ctx.channel_id, w)
        await ctx.send(f"📋 **출력규칙 설정:** [{key}] - {desc}")
        return

    # 3. Remove
    if sub in ['remove', 'delete', 'del', '삭제', '제거', 'r']:
        if len(args) < 2:
            await ctx.send("⚠️ 사용법: `!출력룰 삭제 [키워드]`")
            return
        key = args[1]
        if key in rules:
            del rules[key]
            w["output_rules"] = rules
            domain_manager.update_world_state(ctx.channel_id, w)
            await ctx.send(f"🗑️ **출력규칙 삭제:** [{key}]")
        else:
            await ctx.send(f"⚠️ 출력규칙 '{key}'(을)를 찾을 수 없습니다.")
        return

    # 4. Reset
    if sub in ['reset', '초기화', 'clear']:
        w["output_rules"] = {}
        domain_manager.update_world_state(ctx.channel_id, w)
        await ctx.send("🗑️ **모든 출력규칙 초기화 완료**")
        return

    await ctx.send("⚠️ 사용법: `!출력룰 [목록/추가/삭제/초기화]` — 파일 첨부로 일괄 등록 가능")


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
            await ctx.send("⚠️ 추가할 퀘스트 내용을 입력하세요. (`!quest add 내용 [난이도]`)")
            return
        # Parse optional rank (last word)
        rank = None
        rank_kr_map = {"쉬움": "easy", "보통": "normal", "어려움": "hard", "극난": "extreme", "전설": "epic"}
        for r in config.QUEST_RANK_SETTINGS.keys():
            if content.lower().endswith(f" {r}"):
                rank = r
                content = content[:-(len(r)+1)].strip()
                break
        if not rank:
            for kr, en in rank_kr_map.items():
                if content.endswith(f" {kr}"):
                    rank = en
                    content = content[:-(len(kr)+1)].strip()
                    break
        await ctx.send(game_system.add_quest(ctx.channel_id, content, rank))
        return

    if sub in ["progress", "진행", "advance"]:
        if not content:
            await ctx.send("⚠️ 진행할 퀘스트 이름을 입력하세요. (`!quest 진행 이름 [+N]`)")
            return
        parts = content.rsplit(None, 1)
        quest_name = content
        delta = 1
        if len(parts) > 1:
            try:
                delta = int(parts[1])
                quest_name = parts[0]
            except ValueError:
                pass
        await ctx.send(game_character.advance_quest_progress(ctx.channel_id, quest_name, delta))
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
    await ctx.send("📋 사용법: `!quest [add/complete/remove/progress/list] [내용]`")


@registry.register("relation", category="World", aliases=["관계", "connection", "친밀", "유대"], description="NPC 관계(친밀도) 현황")
async def cmd_relation(ctx: CommandContext) -> None:
    """!관계 [NPC이름] — 전체 관계 현황 또는 특정 NPC 상세"""
    target = ctx.raw_args.strip()

    if not target:
        await ctx.send(npc_manager.get_connection_display(ctx.channel_id))
        return

    # 특정 NPC 상세 조회
    att = npc_manager.get_npc_attitude(ctx.channel_id, target)
    if not att:
        await ctx.send(f"⚠️ '{target}' NPC의 관계 기록이 없습니다.")
        return

    depth = att.get("depth", 0)
    tension = att.get("tension", 0)
    attitude = att.get("attitude", "neutral")
    reason = att.get("reason", "")
    stage_info = config.get_connection_stage(depth)

    depth_filled = min(10, depth // 10)
    depth_bar = "▮" * depth_filled + "▯" * (10 - depth_filled)

    lines = [
        f"🤝 **{target}** 관계 상세",
        f"태도: {attitude}" + (f" — {reason}" if reason else ""),
        f"친밀: {depth_bar} {depth}/100",
        f"단계: **{stage_info['name']}** — {stage_info['hint_kr']}",
    ]
    if tension > 0:
        lines.append(f"긴장: {tension}/100" + (" ⚡위험" if tension > config.NPC_TENSION_DRAMA_THRESHOLD else ""))

    await ctx.send("\n".join(lines))


@registry.register("time", category="World", aliases=["시간"], description="시간 조회 및 설정")
async def cmd_time(ctx: CommandContext) -> None:
    """!시간 [설정 시간대 / 진행 / N]"""
    args = ctx.args
    world = domain_manager.get_world_state(ctx.channel_id)

    if not args:
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

    # Advance clock by 1 tick
    if first in ["진행", "next", "pass"]:
        msg = game_system.advance_time(ctx.channel_id)
        await ctx.send(msg)
        return

    # Advance clock by N ticks
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

    # Set time slot
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


@registry.register("turn", category="World", aliases=["턴", "진행", "건너뛰기", "next"], description="턴 진행 (수동모드: 축적 행동 일괄 처리)")
async def cmd_turn(ctx: CommandContext) -> None:
    """!진행 — 축적된 행동 처리 또는 관찰 턴"""
    from orchestration import get_orchestration_runtime
    orch = get_orchestration_runtime(ctx.genai_client, ctx.model_id, config.MODEL_ID_FLASH)
    if not orch:
        await ctx.send("⚠️ AI 서비스가 초기화되지 않았습니다.")
        return

    pending = domain_manager.get_pending_actions(ctx.channel_id)

    if pending:
        # BATCH MODE: 축적된 행동 일괄 처리
        summary = ", ".join(f"**{v['mask']}** ({len(v['actions'])}행동)" for v in pending.values())
        feedback = await ctx.message.channel.send(f"🔄 **행동을 처리하고 있습니다...**\n> {summary}")
        await orch.execute_batch(ctx.message, ctx.channel_id, pending, feedback)
    else:
        # OBSERVATION MODE: 관찰 턴 (1틱 시간 경과 + 세계 묘사)
        tick_msg = game_system.advance_tick(ctx.channel_id)
        await ctx.send(tick_msg)
        feedback = await ctx.message.channel.send("🔄 **세계를 관찰하고 있습니다...**")
        await orch.execute_observation(ctx.message, ctx.channel_id, feedback)
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
            await ctx.send(f"⚙️ **위기 수치 재설정:** {old_v}% → {val}%")
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

@registry.register("backup", category="Admin", aliases=["백업", "저장"], description="세션 데이터 백업 (JSON 파일 다운로드)")
async def cmd_backup(ctx: CommandContext) -> None:
    """!백업 — 현재 채널의 세션+로어+룰 데이터를 JSON 파일로 전송."""
    channel_id = ctx.channel_id
    import json as _json

    backup_data = {}

    # 세션 데이터
    session_path = domain_manager.get_session_file_path(channel_id)
    if os.path.exists(session_path):
        backup_data["session"] = domain_manager.load_json(session_path, {})

    # 로어 원본
    lore_path = domain_manager.get_lore_original_file_path(channel_id)
    if not os.path.exists(lore_path):
        lore_path = domain_manager.get_lore_file_path(channel_id)
    if os.path.exists(lore_path):
        try:
            with open(lore_path, "r", encoding="utf-8") as f:
                backup_data["lore"] = f.read()
        except Exception:
            pass

    # 룰
    rules_path = domain_manager.get_rules_file_path(channel_id)
    if os.path.exists(rules_path):
        try:
            with open(rules_path, "r", encoding="utf-8") as f:
                backup_data["rules"] = f.read()
        except Exception:
            pass

    if not backup_data:
        await ctx.send("⚠️ 백업할 데이터가 없습니다.")
        return

    content = _json.dumps(backup_data, ensure_ascii=False, indent=2)
    fname = f"backup_{channel_id}.json"
    await ctx.send(
        f"💾 **백업 완료** — 세션{'✅' if 'session' in backup_data else '❌'} "
        f"로어{'✅' if 'lore' in backup_data else '❌'} "
        f"룰{'✅' if 'rules' in backup_data else '❌'}",
        file=discord.File(io.StringIO(content), filename=fname)
    )


@registry.register("restore", category="Admin", aliases=["복구", "복원"], description="백업 파일로 세션 복구")
async def cmd_restore(ctx: CommandContext) -> None:
    """!복구 — 백업 JSON 파일 첨부 시 세션 데이터 복원."""
    import json as _json
    channel_id = ctx.channel_id

    if not ctx.message.attachments:
        await ctx.send("⚠️ 백업 JSON 파일을 첨부해서 `!복구`를 입력하세요.")
        return

    attachment = ctx.message.attachments[0]
    if not attachment.filename.endswith(".json"):
        await ctx.send("⚠️ .json 파일만 복구 가능합니다.")
        return

    try:
        raw = (await attachment.read()).decode("utf-8")
        backup_data = _json.loads(raw)
    except Exception as e:
        await ctx.send(f"⚠️ 파일 파싱 실패: {e}")
        return

    restored = []

    # 세션 복구
    if "session" in backup_data and isinstance(backup_data["session"], dict):
        domain_manager.save_domain(channel_id, backup_data["session"])
        restored.append("세션")

    # 로어 복구
    if "lore" in backup_data and backup_data["lore"]:
        lore_path = domain_manager.get_lore_file_path(channel_id)
        orig_path = domain_manager.get_lore_original_file_path(channel_id)
        os.makedirs(os.path.dirname(lore_path), exist_ok=True)
        with open(lore_path, "w", encoding="utf-8") as f:
            f.write(backup_data["lore"])
        with open(orig_path, "w", encoding="utf-8") as f:
            f.write(backup_data["lore"])
        restored.append("로어")

    # 룰 복구
    if "rules" in backup_data and backup_data["rules"]:
        rules_path = domain_manager.get_rules_file_path(channel_id)
        os.makedirs(os.path.dirname(rules_path), exist_ok=True)
        with open(rules_path, "w", encoding="utf-8") as f:
            f.write(backup_data["rules"])
        restored.append("룰")

    if restored:
        await ctx.send(f"✅ **복구 완료**: {', '.join(restored)}")
    else:
        await ctx.send("⚠️ 복구할 데이터가 백업 파일에 없습니다.")


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
            # Show Korean aliases for discoverability
            kr_aliases = [a for a in info.get('aliases', []) if any('\uac00' <= c <= '\ud7a3' for c in a)]
            alias_str = f" ({', '.join('!' + a for a in kr_aliases)})" if kr_aliases else ""
            msg.append(f"`!{name}`{alias_str}: {desc}")
        
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

