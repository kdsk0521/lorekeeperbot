"""
Lorekeeper TRPG Bot - Command Handler Module
Handles user commands (!help, !info, etc.) and AI system actions.
Replaces: command_handler.py, system_handler.py
"""

import discord
import asyncio
import logging
import io
import json
import os
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
    """로어 텍스트를 섹션 단위로 청크 분할 (V3).

    - 구분선(===, ---) 제거 → 노이즈 청크 방지
    - 메이저 섹션(`# Title` / `## Title`, 들여쓰기 1칸 이하) 단위 그룹화
      ※ V3에서 `N. Title` / `SECTION N:` 패턴 제거 — 일반 본문 false positive
    - 대형 섹션(>_MAX_CHUNK) 마이너 헤더에서 자동 분할
    - 소형 청크 병합: **_MIN_CHUNK는 하한이 아니라 병합 트리거** —
      직전 누적 청크(merged[-1])가 _MIN_CHUNK 미만이면 다음 섹션을 통째로 흡수한다.
      흡수 시 _MAX_CHUNK 재검사가 없으므로 결과가 상한을 넘을 수 있고,
      반대로 _MIN_CHUNK 미만 청크도 남을 수 있다(단일 청크·꼬리 병합 후).
      진짜 바닥은 min_len — 그 미만 섹션은 sections에 담기지 않고 버려진다.
    - 라벨: 섹션 헤더 기반
    - 섹션 미검출 시 문단 기반 폴백
    """
    if not lore_text or not lore_text.strip():
        return []

    _MAX_CHUNK = 4000
    _MIN_CHUNK = 800  # V3 (2026-05-04): 영어 로어북 sweet spot. 200은 한국어 기준이라 영어에선 과잉 분할

    # 구분선: ===, ---, ***, ~~~ (3자 이상, 내용 없는 줄)
    _SEP = re.compile(r'^[\s]*[=\-\*~]{3,}[\s]*$')
    # 메이저 헤더: "1. TITLE" / "2.3.1 Title" / "SECTION 1:" / "# Title" / "## Title"
    # V3: \uB9C8\uD06C\uB2E4\uC6B4 \uD5E4\uB354\uB9CC. \d+\. / SECTION \uD328\uD134 \uC81C\uAC70 (\uC77C\uBC18 \uBCF8\uBB38 false positive)
    _MAJOR = re.compile(r'^#{1,2}\s+')
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
# ⛔[2026-07-28 삭제] process_ai_system_action(40줄) — 호출처 0(grep 확인).
#   AI 툴콜(NPC/Add, Doom/Reduce 등)을 처리하던 구세대 경로. NPC 분기가
#   `domain_manager.update_npc`를 **직접** 불러 등록 관문(npc_manager.update_npc)을 우회했다 —
#   살아있었다면 구조화 추출·static_traits·PRESERVE_KEYS 병합이 전부 빠지는 네 번째 등록 경로.
#   2026-07-28 관문 단일화 기준으로 되살릴 이유가 없다. 현행 세션 NPC 등록은
#   orchestration의 관찰 누적 경로 + npc_manager.register_ai_npc(몹 태그)가 담당.


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
                 # 로어가 식별한 PC가 배경/설명만 있고 기계 필드가 비면 시트 자동 보강
                 pc_msg += await maybe_enrich_pc_sheet(ctx.genai_client, channel_id)
                 
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

            # 4.5 World Tree: 로어 위치 데이터로 공간 그래프 구축
            if locations and isinstance(locations, list):
                try:
                    import world_tree
                    # lore_summary 스키마(desc/danger) → world_tree 스키마(description/risk) 변환
                    _wt_locs = []
                    for loc in locations:
                        if isinstance(loc, dict) and loc.get("name"):
                            _wt_locs.append({
                                "name": loc["name"],
                                "type": loc.get("type", "area"),
                                "parent": loc.get("parent", ""),
                                "description": loc.get("desc", loc.get("description", "")),
                                "risk": loc.get("danger", loc.get("risk", "Low")),
                                "atmosphere": loc.get("atmosphere", ""),
                                "tags": loc.get("tags", []),
                                "connections": loc.get("connections", []),
                            })
                    if _wt_locs:
                        _wt_count = world_tree.import_locations_from_lore(channel_id, _wt_locs)
                        if _wt_count:
                            logger.info(f"[WorldTree] Imported {_wt_count} locations from lore")
                except Exception as e:
                    logger.warning(f"[WorldTree] Lore import failed: {e}")

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
    V2 Layout: Profile -> Relations -> Passives -> Mental -> Quests -> Notebook
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
    msg.append(f"\n**💪 활력:** {v_info['emoji']} **{v_info['name']}** ({v_val}/100)")
    msg.append(f"**😌 평형:** {c_info['emoji']} **{c_info['name']}** ({c_val}/100)")

    # [2026-08-11 비일상적응도 삭제] 5. Adaptation 게이지 제거 — 쓰기 경로가 없어 항상 빈 표시였음
    # 5. Quests (Active)
    quests = game_system.get_active_quests(ctx.channel_id)
    if quests:
        msg.append("\n**🛡️ 진행 중인 퀘스트:**")
        msg.extend([f"- {q}" for q in quests])

    # 6. Notebook (Unified Inventory/Memo, per-user)
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


async def maybe_enrich_pc_sheet(client, channel_id: str, force: bool = False) -> str:
    """현재 default PC가 설명/배경은 있으나 기계 필드(passives/inventory)가 비면
    analyze_character_sheet 1회로 시트를 보강한다. fill-empty(기존 값 보존).
    `!pc` 승격 enrich와 동일 기계를 '로어북만 있는 PC'에도 적용하는 경로.
    반환=상태 메시지(빈 문자열이면 미실행/보강할 것 없음)."""
    if not client:
        return ""
    pc = domain_manager.get_default_pc_info(channel_id)
    if not pc:
        return ""
    # 이미 기계 필드가 차 있으면 스킵 (force면 재분석 허용)
    if not force and (pc.get("passives") or pc.get("inventory")):
        return ""
    source_text = "\n".join(s for s in (pc.get("description", ""), pc.get("background", "")) if s).strip()
    if len(source_text) < 300:
        return ""
    try:
        sheet = await cognition.analyze_character_sheet(client, config.MODEL_ID_FLASH, source_text)
        pc = npc_manager.merge_character_sheet_into_pc(pc, sheet)
        domain_manager.set_default_pc_info(channel_id, pc)
        domain_manager.sync_matching_participants(channel_id, pc)
        n_pas, n_inv = len(pc.get("passives", [])), len(pc.get("inventory", []))
        if n_pas or n_inv or sheet:
            return f"\n🧩 PC 시트 자동보강: 패시브 {n_pas}개 / 소지품 {n_inv}개"
    except Exception as _e:
        logger.warning(f"[PC Enrich] 자동보강 실패(기본 정보로 진행): {_e}")
    return ""


@registry.register("pc", category="Player", aliases=["주인공", "승격"], description="NPC를 PC(주인공)로 승격 / `!pc <NPC이름>`")
async def cmd_pc_promote(ctx: CommandContext) -> None:
    """!pc <NPC이름> — 로어북 분석이 NPC로 잘못 분류한 인물을 주인공(PC)으로 승격.

    이미 추출된 NPC 데이터를 default_pc_info로 옮기고 NPC 목록에서 제거한다.
    (LLM 재분석 없음 — 정보가 NPC 버킷에 그대로 있으므로 필드 재매핑만)
    """
    if not ctx.args:
        await ctx.send("사용법: `!pc <NPC이름>` — 해당 NPC를 주인공(PC)으로 승격합니다.")
        return

    target = ctx.raw_args.strip()
    channel_id = ctx.channel_id

    result = npc_manager.npc_to_pc_info(channel_id, target)
    if not result:
        await ctx.send(f"⚠️ NPC **{target}**(을)를 찾을 수 없습니다. `!로어`로 등록된 이름을 확인하세요.")
        return

    matched_key, pc_info = result

    # B(enrich): 보존된 원문이 충분하면 캐릭터 시트 분석 1회로 passives/inventory/background 복원.
    # NPC 추출은 기계 필드를 안 뽑으므로, 승격 PC를 Tier 1 → Tier 3 fidelity로 끌어올림.
    enrich_msg = ""
    source_text = pc_info.get("description", "") or ""
    if ctx.genai_client and len(source_text) >= 300:
        try:
            sheet = await cognition.analyze_character_sheet(ctx.genai_client, config.MODEL_ID_FLASH, source_text)
            pc_info = npc_manager.merge_character_sheet_into_pc(pc_info, sheet)
            n_pas, n_inv = len(pc_info.get("passives", [])), len(pc_info.get("inventory", []))
            if n_pas or n_inv:
                enrich_msg = f"\n🧩 시트 보강: 패시브 {n_pas}개 / 소지품 {n_inv}개"
        except Exception as _e:
            logger.warning(f"[PC Promote] 캐릭터 시트 보강 실패(기본 정보로 진행): {_e}")

    domain_manager.set_default_pc_info(channel_id, pc_info)
    domain_manager.delete_npc(channel_id, matched_key)

    # 이름이 일치하는 기존 참가자에게 자동 동기화 (로어 적재 경로와 동일)
    updated_uids = domain_manager.sync_matching_participants(channel_id, pc_info)
    sync_msg = ""
    if updated_uids:
        names = []
        for uid in updated_uids:
            p = domain_manager.get_participant_data(channel_id, uid)
            if p:
                names.append(p.get("mask", "Player"))
        if names:
            sync_msg = f"\n✅ 캐릭터 적용: {', '.join(names)}"

    await ctx.send(
        f"👑 **{pc_info['name']}**(을)를 주인공(PC)으로 승격했습니다 (NPC 목록에서 제거).{enrich_msg}{sync_msg}\n"
        f"다른 플레이어는 `!가면 {pc_info['name']}`(으)로 동기화할 수 있습니다."
    )


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


@registry.register("journal", category="Player", aliases=["일지"], description="캐릭터 일지 전체 조회 (노트북엔 최근 몇 줄만 표시)")
async def cmd_journal(ctx: CommandContext) -> None:
    """!일지 — 밀려나서 노트북엔 안 보이는 것까지 포함한 전체 일지 이력 조회."""
    log = domain_manager.get_journal_log(ctx.channel_id, ctx.user_id)
    if not log:
        await ctx.send("📓 아직 기록된 일지가 없습니다. (플레이가 흐르면 자동으로 쌓입니다)")
        return
    lines = [f"{i+1}. {e}" for i, e in enumerate(log)]
    await send_long_message(ctx.message.channel, f"📓 **캐릭터 일지 (전체 {len(log)}건)**\n" + "\n".join(lines))


def _parse_foreign_single_profile(raw_lines: list) -> Optional[tuple]:
    """[2026-07-13] 타인-제작 단일 캐릭터 시트(외부 포맷) 감지·파싱.

    대상: h2(`##`) 헤더가 없고, `- Name:`/`Name:`/`이름:` 선언과 `###`/`####` 구조
    헤더를 가진 key-value 불릿 시트 (RisuAI/커뮤니티 시트 관례 — 예: lore/am.txt 형).
    이 형태가 기존 캐스케이드에서 simple 모드로 떨어지면 모든 `키: 값` 줄이
    각각 NPC로 등록되는 폭발(Name/Alias/Hair…가 전부 NPC화)이 일어남 →
    파일 전체를 NPC 1명으로 등록(원문 보존, manual=동결 소스).

    Returns: (name, description, id_fields) 또는 None(비해당 → 기존 캐스케이드 진행).
    [2026-07-28] 셋째 원소가 summary 문자열 → id_fields dict로 변경(구조화 키까지 넘기기 위해).
    """
    text_lines = [l for l in raw_lines if l.strip()]
    if not text_lines:
        return None
    # h2 있으면 마크다운 모드 소관
    if any(re.match(r'^##(?!#)\s+', l.strip()) for l in text_lines):
        return None
    # 구조 헤더(###/####) 없으면 simple 모드 소관 (진짜 한줄 목록 파일 보호)
    if not any(l.strip().startswith("###") for l in text_lines):
        return None
    name = None
    for l in text_lines:
        m = re.match(r'^[-*\s]*(?:Name|이름)\s*:\s*(.+)$', l.strip(), re.IGNORECASE)
        if m:
            name = m.group(1).strip()
            break
    if not name:
        return None
    # 긴 괄호 부연("AM (Originally ...)")은 base만 취함 — 짧은 복합표기 '이름(별칭)'은 유지
    if "(" in name and len(name) > 24:
        name = name.split("(")[0].strip() or name
    # [2026-07-28] 자체 파싱 루프 → 공용 _extract_id_fields (모드 간 라벨 대칭)
    return (name, "\n".join(raw_lines).strip(), _extract_id_fields(text_lines))


# =========================================================
# [2026-07-28 통일화] NPC 등록 공용 경로
# =========================================================
# 병 1: 4개 파서 모드가 제각각 `domain_manager.update_npc`를 **직접** 호출해
#   `npc_manager.update_npc` 래퍼를 우회했다 → 수동/파일 등록 경로에서만
#   구조화 필드 자동 추출(_extract_structured_fields)과 static_traits가 **한 번도 안 돌았다**.
#   (로어 경로 add_lore_npcs는 래퍼를 거쳐서 돌았다 = 같은 데이터가 출처에 따라 다르게 채워짐.)
# 병 2: 라벨 인식이 모드마다 달랐다 — `occupation`이 외부단일 모드에만 있었다.
# 병 3: 파싱한 species/role/affiliation을 `summary` 문자열로만 저장하고 버렸다.
#   npc_manager 쪽엔 같은 정보를 뽑는 정규식이 따로 있어 2중 구현이었다.
# 처방: 라벨 파싱과 등록을 각각 함수 하나로 모으고, 모든 모드가 이 둘만 쓴다.
#   명시 라벨은 구조화 키로도 넘긴다 — 래퍼의 자동 추출은 "빈 키만" 채우므로
#   사용자가 시트에 직접 쓴 값이 항상 이긴다.

# [2026-07-28] v2 풀시트(커뮤니티 템플릿) 라벨 편입 — race/job/duty/class.
#   구 사전은 우리 시트 관례(species/occupation)만 알아서, `- Race:`나 `- Job:`으로 쓰는
#   외부 템플릿에서는 종족·역할이 통째로 안 잡혔다.
_ID_LABELS = {
    "species": ("species", "종족", "race"),
    "role": ("rank/role", "role", "역할", "occupation", "job", "직업", "duty", "class"),
    "affiliation": ("affiliation", "소속", "faction", "nationality", "국적"),
}


def _extract_id_fields(desc_lines: list) -> dict:
    """설명 줄들에서 종족/역할/소속 라벨을 뽑는다. 콜론 필수, 대소문자 무관, 첫 값 우선."""
    found = {}
    for dl in desc_lines:
        dl_clean = str(dl).strip().lstrip("-*> ").strip()
        if ":" not in dl_clean:
            continue
        fk, fv = dl_clean.split(":", 1)
        fk_l = fk.strip().lower()
        fv = fv.strip()
        if not fv:
            continue
        for canon, aliases in _ID_LABELS.items():
            if fk_l in aliases:
                found.setdefault(canon, fv)
                break
    return found


def _summary_from_id_fields(id_fields: dict) -> str:
    """목록 미리보기용 한 줄. 종족 / 역할(없으면 소속)."""
    items = []
    if id_fields.get("species"):
        items.append(id_fields["species"])
    if id_fields.get("role"):
        items.append(id_fields["role"])
    elif id_fields.get("affiliation"):
        items.append(id_fields["affiliation"])
    return " / ".join(items)


def _register_npc(channel_id: str, name: str, desc: str,
                  id_fields: dict = None, existing_map: dict = None) -> str:
    """NPC 수동 등록 단일 관문. 반환=실제 저장된 키.

    모든 파서 모드가 이 함수만 쓴다(구 코드는 모드마다 domain_manager 직접 호출).
    npc_manager.update_npc 경유 → 구조화 필드 추출 + static_traits가 여기서도 돈다.
    """
    _map = existing_map if existing_map is not None else domain_manager.get_npcs(channel_id)
    target = domain_manager.find_equivalent_npc_key(_map, name) or name
    # [2026-08-11 사망 파이프라인] 생성 도장 대소문자 통일 ("Active" → enum 값 "active").
    #   ⚠명시 status는 _PRESERVE_KEYS를 이긴다 — 즉 이 경로(`!npc추가` 재등록)는
    #     dead를 active로 되돌린다. 수동 조작이므로 **의도된 권한**이다(작가의 손).
    data = {"description": desc, "source": "manual", "status": "active"}
    idf = id_fields or {}
    # 명시 라벨 → 구조화 키. 래퍼의 자동 추출보다 우선(래퍼는 빈 키만 채운다).
    if idf.get("role"):
        data["role"] = idf["role"]
    if idf.get("species"):
        data["race"] = idf["species"]        # 상세 조회가 읽는 키 이름
    if idf.get("affiliation"):
        data["affiliation"] = idf["affiliation"]
    _summary = _summary_from_id_fields(idf)
    if _summary:
        data["summary"] = _summary
    # [2026-08-11 드라이브 부분dict 수리] update_npc는 통째 교체 관문이라, 재등록 때
    #   _PRESERVE_KEYS 밖 필드(appear_count/_last_appear_turn/drives/soma/
    #   decision_cooldown/identity_history/affiliation…)가 조용히 증발했다.
    #   보존 목록은 수동 근사치일 뿐 — 정책 본문("지우려면 !npc 삭제 후 재등록")이
    #   요구하는 건 full-copy다. 새 시트 값은 update로 여전히 이긴다.
    _prev = _map.get(target)
    if isinstance(_prev, dict):
        _merged = dict(_prev)
        _merged.update(data)
        data = _merged
    npc_manager.update_npc(channel_id, target, data)
    return target


def _merge_npc_attachment_texts(texts: list) -> tuple:
    """!npc추가 다중 첨부 병합 결정. texts=[(filename, text), ...] → (file_text, skipped).

    [2026-08-10] 모든 첨부가 `## 이름` 인물 경계(h2)를 갖추면(v6 기본형) 병합해 일괄
    등록한다 — "파일 하나=인물 하나" 워크플로에서 여러 명을 한 메시지로(디스코드 캡 10).
    경계 없는 파일(단일시트/레거시)이 하나라도 섞이면 병합 시 그 내용이 앞 블록에
    흡수되거나 유실되므로, 구 동작(첫 파일만 + 무시 목록 안내)으로 폴백한다.
    """
    def _has_h2(t: str) -> bool:
        return any(re.match(r'^##(?!#)\s+\S', l.strip()) for l in t.splitlines())

    if not texts:
        return "", []
    if len(texts) == 1:
        return texts[0][1], []
    if all(_has_h2(t) for _, t in texts):
        return "\n\n".join(t for _, t in texts), []
    return texts[0][1], [fn for fn, _ in texts[1:]]


@registry.register("npc", category="World", aliases=["엔피씨", "addnpc", "npc정보", "npc추가"], description="NPC 관리 (조회/추가/삭제/별칭/병합)")
async def cmd_npc(ctx: CommandContext) -> None:
    """!npc [이름] 조회 | !npc추가 [이름]: [설명] (또는 파일 첨부) | !npc 삭제 [이름]
    | !npc 별칭 [이름] [별칭] | !npc 병합 [중복] [본체] | !npc 보이스카드 [이름]
    | !npc 상태 [이름] [active|down|dead]  ← [2026-08-11] 생존축 수동 확정

    [2026-07-28] 구 독스트링은 `!npc add`를 안내했으나 **그런 서브커맨드는 없다**
    (등록 게이트는 트리거가 addnpc/npc추가이거나, 여러 줄이거나, 파일 첨부이거나,
     `이름: 설명` 콜론 형태일 때 열린다)."""
    # 1. File Content
    file_text = ""
    _skipped_files = []
    if ctx.message.attachments:
        _att_texts = []
        for att in ctx.message.attachments:
            text, error = await read_attachment_text(att)
            if error:
                await ctx.send(error)
                return
            if text:
                _att_texts.append((att.filename, text))
        # [2026-08-10] 다중 첨부 지원 — "파일 하나=인물 하나"(v6 기본형) 워크플로에서
        # 여러 명을 한 메시지로. 병합 조건·안전장치는 _merge_npc_attachment_texts 참조.
        # (구 동작: 첫 파일만 + 무시 목록 안내 — 경계 없는 파일이 섞일 때만 그리로 폴백)
        file_text, _skipped_files = _merge_npc_attachment_texts(_att_texts)

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

        # Subcommand: 상태 / status — 생존축 수동 확정 (active/down/dead)
        # [2026-08-11 사망 파이프라인] **dead를 만들 수 있는 유일한 입구.**
        #   자동 경로(추출 콜 관측)는 가역 상태 down까지만 만들고, 비가역 확정과 그 해제는
        #   여기로 온다. 새 명령어를 신설하지 않고 !npc 서브커맨드로 붙인 이유는 조작면
        #   최소주의 — 인풋만으로 굴러가는 캠페인이 기본이고 이건 정정용 손잡이다.
        if parts[0].lower() in ('상태', 'status'):
            _st_values = getattr(config, "NPC_STATUS_VALUES", ("active",))
            _usage = f"⚠️ 사용법: `!npc 상태 [이름] [{'|'.join(_st_values)}]`"
            _rest = parts[1].strip() if len(parts) > 1 else ""
            if len(_rest.split()) < 2:
                await ctx.send(_usage)
                return
            # 이름에 공백이 흔하므로(`Lee Ha-yoon(이하윤)`) 상태는 **마지막 토큰**으로 자른다
            _tname, _tstatus = _rest.rsplit(None, 1)
            _tname, _tstatus = _tname.strip(), _tstatus.strip().lower()
            if _tstatus not in _st_values:
                await ctx.send(_usage)
                return
            npcs = domain_manager.get_npcs(channel_id)
            key = domain_manager._find_npc_key(npcs, _tname)
            if not key:
                await ctx.send(f"⚠️ NPC '{_tname}' 정보를 찾을 수 없습니다.")
                return
            _before = npc_manager.get_npc_status(npcs.get(key) or {})
            _res = npc_manager.set_npc_status_gated(
                channel_id, key, _tstatus, source="manual", evidence="manual command")
            if _res == "accepted":
                await ctx.send(f"✅ NPC **{key}** 상태: `{_before}` → `{_tstatus}`")
            elif _res == "unchanged":
                await ctx.send(f"ℹ️ NPC **{key}** 는 이미 `{_tstatus}` 입니다.")
            else:
                await ctx.send(f"⚠️ 상태 변경 실패 (`{_res}`).")
            return

        # Subcommand: voicecard / 보이스카드 재추출
        if parts[0].lower() in ('voicecard', 'vc', '보이스카드', '보이스'):
            if not ctx.genai_client:
                await ctx.send("⚠️ AI 클라이언트가 초기화되지 않았습니다.")
                return
            target = parts[1].strip() if len(parts) > 1 else None
            npcs = domain_manager.get_npcs(channel_id)
            targets = {}
            single = bool(target)
            if target:
                key = domain_manager._find_npc_key(npcs, target)
                if key:
                    targets[key] = npcs[key]
                else:
                    await ctx.send(f"⚠️ NPC '{target}' 정보를 찾을 수 없습니다.")
                    return
            else:
                # 인자 없으면 전체 일괄 (description 100자 이상)
                targets = {k: v for k, v in npcs.items()
                           if len((v.get("description") or v.get("desc", "")).strip()) > 100}
            if not targets:
                await ctx.send("🎙️ 보이스카드 대상 NPC가 없습니다.")
                return

            # voice 없는 NPC의 특징(description)에서 말투를 distill → tone 필드 저장.
            # 배치는 이미 voice 있는 NPC(### Voice 섹션 or tone/speech) skip, 단일 타깃은 강제 재생성.
            await ctx.send(f"🎙️ 보이스카드 추출 중... (대상 {len(targets)}명){'' if single else ' — 이미 말투 있는 NPC는 건너뜀'}")
            done, skipped = [], []
            for key, data in targets.items():
                desc = (data.get("description") or data.get("desc", "")).strip()
                has_voice = npc_manager._is_hybrid_profile(desc) or data.get("tone") or data.get("speech")
                if has_voice and not single:
                    skipped.append(key)
                    continue
                voice = await cognition.extract_voice_card(ctx.genai_client, config.MODEL_ID_FLASH, key, desc)
                if voice:
                    data["tone"] = voice
                    domain_manager.update_npc(channel_id, key, data)
                    done.append(key)
                else:
                    skipped.append(key)

            msg = f"🎙️ **보이스카드 완료** — 말투 생성 {len(done)}명"
            if done:
                msg += f": {', '.join(done[:10])}" + (" 등" if len(done) > 10 else "")
            if skipped:
                msg += f"\n(건너뜀 {len(skipped)}명: 이미 말투 있음/특징 부족/추출 실패)"
            # 생성된 말투 바로 확인: 단일 타깃은 전문, 배치는 조회 안내
            if single and done:
                _v = domain_manager.get_npc(channel_id, done[0])
                _tone = (_v.get("tone") or _v.get("speech")) if _v else ""
                if _tone:
                    msg += f"\n\n**{done[0]} 말투:**\n{_tone}"
            elif done:
                msg += "\n각 NPC 말투는 `!npc <이름>`으로 확인하세요."
            await ctx.send(msg)
            return

        # Subcommand: 별칭 / alias — 모델이 다른 언어로 부를 이름 등록 (리리스 ↔ Lilith)
        # [2026-06-12] 공백 포함 이름 지원: 구분자(->) 또는 등록 키 기반 스마트 분할
        if parts[0].lower() in ('alias', '별칭'):
            rest = parts[1] if len(parts) > 1 else ""
            npc_arg, alias_arg, perr = domain_manager.split_npc_pair(
                domain_manager.get_npcs(channel_id), rest, both_npc=False)
            if perr:
                await ctx.send(f"⚠️ {perr}\n사용법: `!npc 별칭 [NPC이름] [별칭]` 또는 `!npc 별칭 이름없는 유령 -> Nameless`")
                return
            ok, key = domain_manager.add_npc_alias(channel_id, npc_arg, alias_arg)
            if ok:
                aliases = (domain_manager.get_npcs(channel_id).get(key) or {}).get("aliases", [])
                await ctx.send(f"🏷️ **{key}** 별칭 등록: {', '.join(aliases)}")
            elif key:
                await ctx.send(f"⚠️ '{alias_arg}'는 이미 다른 NPC **{key}**로 해상됩니다.")
            else:
                await ctx.send(f"⚠️ NPC '{npc_arg}' 정보를 찾을 수 없습니다.")
            return

        # Subcommand: 병합 / merge — 중복 등록 NPC를 본체로 흡수 (흡수명 자동 별칭화)
        if parts[0].lower() in ('merge', '병합'):
            rest = parts[1] if len(parts) > 1 else ""
            dup_arg, canon_arg, perr = domain_manager.split_npc_pair(
                domain_manager.get_npcs(channel_id), rest, both_npc=True)
            if perr:
                await ctx.send(f"⚠️ {perr}\n사용법: `!npc 병합 [중복이름] [본체이름]` 또는 `!npc 병합 Ghost -> 이름없는 유령`")
                return
            ok, msg = domain_manager.merge_npc(channel_id, dup_arg, canon_arg)
            await ctx.send(f"{'🔀' if ok else '⚠️'} {msg}")
            return

    # 3. Batch Processing Logic (Restored from handle_npc_command)
    raw_lines = (arg + "\n" + file_text).strip().splitlines() if arg else (file_text or "").strip().splitlines()
    processed_count = 0

    # If explicit "addnpc" or batch mode implied
    # [2026-07-28] 게이트에 콜론 형태 추가 — 구 게이트는 `!npc 리안: 사서` **한 줄**을
    #   등록으로 안 보고 조회로 흘려보냈다(트리거도 addnpc 아니고, 줄도 1개, 첨부도 없음)
    #   → "NPC '리안: 사서' 정보를 찾을 수 없습니다"라는 엉뚱한 응답. 등록 의도가 명백한
    #   `이름: 설명` 형태는 받아준다. 조회는 콜론을 쓰지 않으므로 충돌하지 않는다.
    _looks_like_add = bool(
        raw_lines and ":" in raw_lines[0] and raw_lines[0].split(":", 1)[1].strip()
    )
    if ctx.trigger in ['addnpc', 'npc추가'] or (len(raw_lines) > 1) or file_text or _looks_like_add:
        if (not raw_lines or not raw_lines[0].strip()) and not file_text:
             await ctx.send("⚠️ 등록할 내용이 없습니다. `!npc추가 [이름]: [설명]` 또는 파일 첨부.")
             return

        # --- Phase 0.5: 타인-제작 단일 시트 (## 없음 + Name: 불릿 + ###/#### 구조) ---
        # [2026-07-13] 외부 포맷이 simple 모드로 떨어져 '키: 값' 줄마다 NPC가 등록되던
        # 폭발 방지 — 파일 전체=1명·원문 보존. manual 소스라 동결(재작성 안 덮음).
        _foreign = _parse_foreign_single_profile(raw_lines)
        if _foreign:
            _f_name, _f_desc, _f_idf = _foreign
            _f_target = _register_npc(channel_id, _f_name, _f_desc, _f_idf)
            _f_summary = _summary_from_id_fields(_f_idf)
            await ctx.send(f"👥 **NPC 등록 (단일 시트):** {_f_target}"
                           + (f"\n_{_f_summary}_" if _f_summary else "")
                           + ("\n⚠️ 첨부 파일은 **하나만** 등록됩니다 — 무시됨: "
                              + ", ".join(_skipped_files[:5]) if _skipped_files else ""))
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

            # 동일 인물 해상도는 _register_npc가 담당(양방향 매칭 — 2026-06-12 리리스/Lilith 분열 수리).
            # 배치 중 새로 생긴 NPC도 다음 블록에서 매칭되도록 맵을 매 블록 갱신한다.
            for name, desc_lines in blocks:
                while desc_lines and not desc_lines[0].strip():
                    desc_lines.pop(0)
                while desc_lines and not desc_lines[-1].strip():
                    desc_lines.pop()
                desc = "\n".join(desc_lines)
                # [2026-07-28] 자체 라벨 루프 → 공용 헬퍼(occupation 포함, 구조화 키까지 전달)
                target_name = _register_npc(
                    channel_id, name, desc, _extract_id_fields(desc_lines))
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
                # [2026-07-28] 이 모드도 라벨 인식 대상에 편입(구 코드는 마크다운·외부단일만 인식)
                last_name = _register_npc(
                    channel_id, name, desc, _extract_id_fields(desc_lines))
                processed_count += 1
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
                        last_name = _register_npc(channel_id, clean_key, val)
                        processed_count += 1
                        continue

                # Continuation line
                if last_name:
                    curr_npc = domain_manager.get_npc(channel_id, last_name)
                    if curr_npc:
                        new_desc = (curr_npc.get("description") or curr_npc.get("desc", "")) + "\n" + stripped
                        _register_npc(channel_id, last_name, new_desc)

        if processed_count > 0:
            _tail = ""
            if _skipped_files:
                _tail = ("\n⚠️ 첨부 파일은 **하나만** 등록됩니다 — 무시됨: "
                         + ", ".join(_skipped_files[:5]))
            if processed_count == 1:
                await ctx.send(f"👥 **NPC 등록:** {last_name}{_tail}")
            else:
                await ctx.send(f"👥 **NPC 일괄 등록 완료:** 총 {processed_count}명{_tail}")

            # Voice Card 시스템 제거됨 — hybrid는 ### Voice 섹션 직접 사용, legacy는 tone 폴백
        else:
             await ctx.send("⚠️ 유효한 형식을 찾을 수 없습니다. (예: `이름: 설명` 또는 `[NPC NAME]: 이름`)")
        return

    # Look up NPC
    _arg_l = arg.strip().lower() if arg else ""
    if not arg or _arg_l in ("all", "전체", "목록"):
        _show_all = _arg_l in ("all", "전체", "목록")
        npcs = domain_manager.get_npcs(channel_id)
        if not npcs:
            await ctx.send("👥 등록된 NPC가 없습니다.")
            return

        # List all — [D-A] 빈 description은 관찰/면모로 폴백, [T-B] provisional(1회성) 접기
        def _npc_preview(d: dict) -> str:
            if d.get("summary"):
                return d["summary"][:60]
            desc = npc_manager._npc_desc_fallback(d) or "-"
            for line in desc.split("\n"):
                s = line.strip()
                if not s or s.startswith("#"):
                    continue
                cl = s.lstrip("- ").strip()
                if cl.lower().startswith(("name:", "alias:", "이름:")):
                    continue
                return cl[:60]
            return desc[:60]
        _est, _prov = [], []
        for n, d in npcs.items():
            if not _show_all and npc_manager.get_npc_tier(d) == "provisional":
                _prov.append((n, d))
            else:
                _est.append((n, d))
        name_list = [f"• **{n}**: {_npc_preview(d)}" for n, d in _est]
        body = "👥 **NPC 목록**\n" + ("\n".join(name_list) if name_list else "(정착 NPC 없음)")
        if _prov and not _show_all:
            body += f"\n\n_임시 {len(_prov)}명 (1회성/신규 등) — `!npc all` 로 전체 보기_"
        await send_long_message(ctx.message.channel, body)
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
            
            # [2026-07-28] 목록(`_npc_preview`)은 _npc_desc_fallback으로 관찰/면모까지 보여주는데
            # 여기만 description 원본만 봐서, 같은 NPC가 목록엔 뜨고 상세는 텅 비는 역전이 있었다.
            desc_text = npc.get("description") or npc.get("desc", "") or npc_manager._npc_desc_fallback(npc)
            if desc_text:
                # 긴 프로필은 앞부분만 표시
                preview = desc_text[:500] + ("..." if len(desc_text) > 500 else "")
                msg.append(preview)

            if npc.get('role'): msg.append(f"**역할:** {npc.get('role')}")
            if npc.get('affiliation'): msg.append(f"**소속:** {npc.get('affiliation')}")
            if npc.get('appearance'): msg.append(f"**외양:** {npc.get('appearance')}")
            if npc.get('background'): msg.append(f"**배경:** {npc.get('background')}")
            if npc.get('tone') or npc.get('speech'):
                msg.append(f"**말투:** {npc.get('tone') or npc.get('speech')}")

            await send_long_message(ctx.message.channel, "\n".join(msg))
        else:
            # 유사 이름 후보 제시 — `!npc 삭제` 실패 경로와 동일한 친절도로 맞춤(07-28)
            _nl = arg.strip().lower()
            _cands = [k for k in (domain_manager.get_npcs(channel_id) or {})
                      if _nl in k.lower() or k.lower() in _nl][:5]
            await ctx.send(f"⚠️ NPC '{arg}' 정보를 찾을 수 없습니다."
                           + (f"\n혹시 이건가요: {', '.join(_cands)}" if _cands else
                              "\n등록하려면 `!npc추가 [이름]: [설명]` 또는 파일 첨부."))



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

@registry.register("modules", category="System", aliases=["모듈", "mods"], description="DLC 모듈 상태 확인")
async def cmd_modules(ctx: CommandContext) -> None:
    """!모듈 - 모듈 상태 확인. 핵심 4모듈은 항상 활성."""
    arg = ctx.raw_args.strip().lower()
    active = domain_manager.get_active_modules(ctx.channel_id)
    core_mods = [("judgment", "판정"), ("doom", "둠"), ("anomaly", "이변")]
    # [2026-08-17] 둘 다 **기본 ON**(domain_manager.DEFAULT_ON_MODULES) — 명시적 off만 끈다.
    extra_mods = [("board", "게시판"), ("mind", "속마음")]
    # 개별 토글 별칭. 💭는 전용 명령을 만들지 않는다(조작면 최소주의) — !모듈의 서브커맨드가 제자리.
    _mod_aliases = {
        "board": "board", "게시판": "board",
        "mind": "mind", "속마음": "mind", "💭": "mind",
    }
    _parts = arg.split()

    # 개별 토글: !모듈 속마음 off / !모듈 게시판 on
    if len(_parts) >= 2 and _parts[0] in _mod_aliases:
        _code = _mod_aliases[_parts[0]]
        _label = dict(extra_mods).get(_code, _code)
        if _parts[1] in ('on', '켜기', 'true'):
            domain_manager.toggle_module(ctx.channel_id, _code, True)
            await ctx.send(f"✅ **{_label}** 모듈 활성화")
            return
        if _parts[1] in ('off', '끄기', 'false'):
            domain_manager.toggle_module(ctx.channel_id, _code, False)
            await ctx.send(f"❌ **{_label}** 모듈 비활성화")
            return

    # 부가 모듈 일괄 토글
    if arg in ['on', '켜기', 'true', 'all']:
        for code, _ in extra_mods:
            domain_manager.toggle_module(ctx.channel_id, code, True)
        _names = " ✅ · ".join(name for _, name in extra_mods)
        await ctx.send(f"✅ **부가 모듈이 활성화되었습니다.**\n• {_names} ✅\n\n(판정/둠/이변은 항상 활성)")
        return
    if arg in ['off', '끄기', 'false', 'none']:
        for code, _ in extra_mods:
            domain_manager.toggle_module(ctx.channel_id, code, False)
        _names = " ❌ · ".join(name for _, name in extra_mods)
        await ctx.send(f"❌ **부가 모듈이 비활성화되었습니다.**\n• {_names} ❌\n\n(판정/둠/이변은 항상 활성)")
        return

    # 상태 확인
    msg = ["🔌 **모듈 상태**", "", "**핵심 모듈** (항상 활성)"]
    for code, name in core_mods:
        msg.append(f"• {name} ({code}): ✅ ON")
    msg.append("")
    msg.append("**토글 가능 모듈** (기본 ON)")
    _vc_on = domain_manager.is_vigor_composure_active(ctx.channel_id)
    msg.append(f"• 활력/평형 (mental): {'✅ ON' if _vc_on else '❌ OFF'}")
    for code, name in extra_mods:
        status = "✅ ON" if code in active else "❌ OFF"
        msg.append(f"• {name} ({code}): {status}")
    msg.append("\n💡 `!활력모듈 on/off` · `!게시판 on/off` · `!모듈 속마음 on/off` — 토글 모듈 제어")

    await ctx.send("\n".join(msg))

@registry.register("judgment", category="System", aliases=["판정"], description="판정 모듈 정보")
async def cmd_toggle_judgment(ctx: CommandContext) -> None:
    await ctx.send("⚖️ **판정 모듈**: ✅ 항상 활성\n판정 트리거는 AI 분석 + 코드 게이트로 자동 결정됩니다.")

@registry.register("doom_mod", category="System", aliases=["둠모듈", "doommod"], description="둠 모듈 정보")
async def cmd_toggle_doom(ctx: CommandContext) -> None:
    await ctx.send("⏰ **둠 모듈**: ✅ 항상 활성\n8단계 위협 시계가 항상 작동합니다.")

@registry.register("anomaly", category="System", aliases=["이변"], description="이변 모듈 정보 및 징후 조회")
async def cmd_toggle_anomaly(ctx: CommandContext) -> None:
    lore_data = domain_manager.get_lore_summary_data(ctx.channel_id)
    seeds = lore_data.get("anomaly_seeds", [])

    msg = "🌪️ **이변 모듈**: ✅ 항상 활성\n"
    if seeds:
        msg += f"\n**등록된 이변 징후** ({len(seeds)}개):\n"
        msg += "\n".join(f"• `{s}`" for s in seeds)
    else:
        msg += "\n*(이변 징후 없음 — 로어 분석 시 자동 추출됩니다)*"
    await ctx.send(msg)

@registry.register(
    "활력모듈",
    category="System",
    aliases=["기력모듈", "멘탈모듈", "mentalmod", "mental_mod", "vigor_mod", "활력mod", "기력mod", "평형모듈"],
    description="활력/평형 모듈 on/off 토글 및 상태"
)
async def cmd_toggle_mental(ctx: CommandContext) -> None:
    """!활력모듈 [on/off] — 활력/평형 2축 시스템 채널 단위 토글."""
    arg = ctx.raw_args.strip().lower()
    if arg in ['on', '켜기', 'true', '활성', 'all']:
        domain_manager.set_vigor_composure_active(ctx.channel_id, True)
        await ctx.send("💪 **활력/평형 모듈: ✅ ON**\n활력/평형 2축 시스템이 작동합니다.")
        return
    if arg in ['off', '끄기', 'false', '비활성', 'none']:
        domain_manager.set_vigor_composure_active(ctx.channel_id, False)
        await ctx.send(
            "💪 **활력/평형 모듈: ❌ OFF**\n"
            "이 채널의 활력/평형 처리·수치 변동·프롬프트 주입이 모두 중단됩니다. (수치는 현재값으로 동결)\n"
            "`!활력모듈 on` 으로 다시 켤 수 있습니다."
        )
        return
    # 인자 없음 → 현재 상태 표시
    _on = domain_manager.is_vigor_composure_active(ctx.channel_id)
    _status = "✅ ON" if _on else "❌ OFF"
    await ctx.send(
        f"💪 **활력/평형 모듈**: {_status}\n"
        "활력/평형 2축 시스템.  `!활력모듈 on` / `!활력모듈 off` 로 켜고 끌 수 있습니다."
    )


# =========================================================
# Arc System OOC 명령어 (Phase 6)
# =========================================================
# spec v2 §5 운영 자세 — "안 쓰는 게 최고. 안 쓴다는 건 잘 작동한다는 의미."
# 최소한만: 조회 / 정정 / 강제 dormant·활성 / backstage 조회.
# 디버깅용 (phases, trajectory)은 보류.

@registry.register(
    "아크",
    category="System",
    aliases=["arc", "큰호흡", "volume"],
    description="아크 조회/관리 — `!아크` 목록, `!아크 수정 [id] [field]=[value]`, `!아크 dormant/활성 [id]`, `!아크 backstage [id]`",
)
async def cmd_arc(ctx: CommandContext) -> None:
    import narrative_tracker as _nt

    args = ctx.args
    nt_state = domain_manager.get_narrative_tracker_state(ctx.channel_id)

    # 인자 없으면 조회
    if not args:
        await _arc_list(ctx, nt_state)
        return

    sub = args[0].lower()

    if sub in ("dormant", "비활성"):
        if len(args) < 2:
            await ctx.send("사용법: `!아크 dormant [id]`")
            return
        await _arc_set_status(ctx, nt_state, args[1], "dormant")
        return

    if sub in ("활성", "active", "부활"):
        if len(args) < 2:
            await ctx.send("사용법: `!아크 활성 [id]`")
            return
        await _arc_set_status(ctx, nt_state, args[1], "active")
        return

    if sub == "backstage":
        if len(args) < 2:
            await ctx.send("사용법: `!아크 backstage [id]`")
            return
        await _arc_backstage(ctx, nt_state, args[1])
        return

    if sub == "수정":
        if len(args) < 3:
            await ctx.send("사용법: `!아크 수정 [id] [field]=[value]`\n필드: declared_goal / next_waypoint")
            return
        await _arc_modify(ctx, nt_state, args[1], " ".join(args[2:]))
        return

    await ctx.send(f"알 수 없는 서브명령: `{sub}`\n사용법: `!아크` / `!아크 수정` / `!아크 dormant/활성` / `!아크 backstage`")


async def _arc_list(ctx, nt_state):
    """active + dormant arc 목록 조회."""
    storylines = nt_state.get("storylines", [])
    active_arcs = [s for s in storylines if s.get("is_arc") and s.get("status") == "active"]
    dormant_arcs = [s for s in storylines if s.get("is_arc") and s.get("status") == "dormant"]

    if not active_arcs and not dormant_arcs:
        await ctx.send("📚 **현재 아크 없음** — 시드가 누적되어 자연 격상되면 표시됩니다.")
        return

    lines = ["📚 **현재 아크**"]

    if active_arcs:
        lines.append("\n**활성**:")
        for arc in active_arcs:
            arc_id = arc.get("id")
            decl = arc.get("declared_goal", "(미정)")
            cat = arc.get("origin_category", "?")
            prox = arc.get("proximity", 0.0)
            weight = arc.get("weight", 0.0)
            pacing = arc.get("pacing", 0.0)
            mode = "crucial" if pacing >= 0.6 else "mundane"
            armed = " ⚡armed" if arc.get("armed") else ""
            phases = arc.get("phases", [])
            current = phases[-1] if phases else "(initial)"
            lines.append(
                f"• `#{arc_id}` {decl}\n"
                f"   카테고리: {cat} | prox={prox:.2f} | weight={weight:.2f} | mode={mode}{armed}\n"
                f"   현재: {current} → {arc.get('next_waypoint', '(?)')}"
            )

    if dormant_arcs:
        lines.append("\n**휴면**:")
        for arc in dormant_arcs:
            arc_id = arc.get("id")
            decl = arc.get("declared_goal", "(미정)")
            lines.append(f"• `#{arc_id}` {decl} (`!아크 활성 {arc_id}` 로 부활)")

    await ctx.send("\n".join(lines))


async def _arc_find(nt_state, arc_id_str):
    """id string으로 arc 찾기. 못 찾으면 None."""
    try:
        arc_id = int(arc_id_str)
    except (ValueError, TypeError):
        return None
    for sl in nt_state.get("storylines", []):
        if sl.get("id") == arc_id and sl.get("is_arc"):
            return sl
    return None


async def _arc_set_status(ctx, nt_state, arc_id_str, new_status):
    arc = await _arc_find(nt_state, arc_id_str)
    if not arc:
        await ctx.send(f"아크 `#{arc_id_str}` 없음.")
        return
    old_status = arc.get("status")
    arc["status"] = new_status
    domain_manager.update_narrative_tracker_state(ctx.channel_id, nt_state)
    await ctx.send(f"✅ 아크 `#{arc_id_str}` {old_status} → **{new_status}**")


async def _arc_backstage(ctx, nt_state, arc_id_str):
    arc = await _arc_find(nt_state, arc_id_str)
    if not arc:
        await ctx.send(f"아크 `#{arc_id_str}` 없음.")
        return
    backstage = arc.get("backstage_reality", "")
    decl = arc.get("declared_goal", "(미정)")
    if backstage:
        await ctx.send(
            f"🎭 **아크 #{arc_id_str} 배경 진실** (작가만 아는 정보)\n"
            f"선언된 목표: {decl}\n"
            f"\n**객관적 진실**:\n{backstage}"
        )
    else:
        await ctx.send(f"아크 `#{arc_id_str}` 배경 진실 미설정.")


async def _arc_modify(ctx, nt_state, arc_id_str, rest):
    arc = await _arc_find(nt_state, arc_id_str)
    if not arc:
        await ctx.send(f"아크 `#{arc_id_str}` 없음.")
        return

    # "field=value" 파싱
    if "=" not in rest:
        await ctx.send("형식: `!아크 수정 [id] field=value`\n필드: declared_goal / next_waypoint / backstage_reality")
        return

    field, value = rest.split("=", 1)
    field = field.strip()
    value = value.strip()

    allowed = ("declared_goal", "next_waypoint", "backstage_reality")
    if field not in allowed:
        await ctx.send(f"수정 가능 필드: {', '.join(allowed)}")
        return

    old = arc.get(field, "")
    arc[field] = value
    domain_manager.update_narrative_tracker_state(ctx.channel_id, nt_state)
    await ctx.send(f"✅ 아크 `#{arc_id_str}` {field}:\n  이전: {old or '(빈)'}\n  현재: {value}")

@registry.register("board", category="System", aliases=["게시판", "boardmod"], description="세계 게시판 모듈 관리")
async def cmd_toggle_board(ctx: CommandContext) -> None:
    """!게시판 [on/off/상태] | !게시판 공지/sns/메시지 [on/off] | !게시판 빈도 N"""
    import world_board
    arg = ctx.raw_args.strip().lower()
    parts = arg.split()

    # 채널 이름 매핑
    ch_aliases = {
        "공지": "bulletin", "bulletin": "bulletin", "게시": "bulletin",
        "sns": "sns", "소셜": "sns", "피드": "sns",
        "메시지": "message", "message": "message", "쪽지": "message", "편지": "message",
    }

    # 빈도 설정: !게시판 빈도 10 또는 !게시판 빈도 sns 5
    if len(parts) >= 2 and parts[0] in ("빈도", "freq", "frequency"):
        try:
            # !게시판 빈도 sns 5 (채널별)
            if len(parts) >= 3 and parts[1] in ch_aliases:
                ch = ch_aliases[parts[1]]
                freq = max(1, int(parts[2]))
                world_board.set_board_frequency(ctx.channel_id, freq, ch_name=ch)
                ch_display = {"bulletin": "📋 공지", "sns": "📱 SNS", "message": "💌 메시지"}[ch]
                await ctx.send(f"{ch_display} **빈도**: {freq}턴마다")
            else:
                # !게시판 빈도 10 (전체 기본값)
                freq = max(1, int(parts[1]))
                world_board.set_board_frequency(ctx.channel_id, freq)
                await ctx.send(f"📋 **게시판 전체 빈도**: {freq}턴마다 자동 게시")
            return
        except (ValueError, TypeError):
            await ctx.send("⚠️ 사용법: `!게시판 빈도 10` 또는 `!게시판 빈도 sns 5`")
            return

    # [2026-08-16 도착물 라우트] 착지 모드: !게시판 표시 메시지 버튼|스레드|끄기
    #   새 명령어를 만들지 않는다 — 게시판의 착지 방식이므로 !게시판의 서브커맨드가 제자리.
    if len(parts) >= 1 and parts[0] in ("표시", "display", "착지"):
        mode_aliases = {
            "스레드": "thread", "thread": "thread",
            "버튼": "button", "button": "button",
            "끄기": "off", "off": "off", "없음": "off",
        }
        mode_display = {"thread": "🧵 스레드(공개)", "button": "💌 버튼(본인만)", "off": "❌ 끄기"}
        if len(parts) >= 3 and parts[1] in ch_aliases and parts[2] in mode_aliases:
            ch = ch_aliases[parts[1]]
            mode = mode_aliases[parts[2]]
            world_board.set_display_mode(ctx.channel_id, ch, mode)
            ch_display = {"bulletin": "📋 공지", "sns": "📱 SNS", "message": "💌 메시지"}[ch]
            await ctx.send(f"{ch_display} **착지**: {mode_display[mode]}")
            return
        modes = world_board.get_all_display_modes(ctx.channel_id)
        await ctx.send(
            "🧭 **게시판 착지 방식**\n"
            f"  📋 공지: {mode_display.get(modes['bulletin'], modes['bulletin'])}\n"
            f"  📱 SNS: {mode_display.get(modes['sns'], modes['sns'])}\n"
            f"  💌 메시지: {mode_display.get(modes['message'], modes['message'])}\n\n"
            "사용법: `!게시판 표시 메시지 버튼` (스레드 / 버튼 / 끄기)\n"
            "  · 버튼 = 그 턴 산문에 💌가 붙고, 누른 사람만 봅니다.\n"
            "  · 스레드 = 공개 스레드에 게시(채널 전원이 봅니다)."
        )
        return

    # 개별 채널 토글: !게시판 sns on/off
    if len(parts) >= 1 and parts[0] in ch_aliases:
        ch_name = ch_aliases[parts[0]]
        ch_display = {"bulletin": "📋 공지", "sns": "📱 SNS", "message": "💌 메시지"}[ch_name]
        if len(parts) >= 2 and parts[1] in ("on", "켜기", "true"):
            world_board.set_board_channel(ctx.channel_id, ch_name, True)
            await ctx.send(f"✅ **{ch_display}** 채널 활성화")
            return
        elif len(parts) >= 2 and parts[1] in ("off", "끄기", "false"):
            world_board.set_board_channel(ctx.channel_id, ch_name, False)
            await ctx.send(f"❌ **{ch_display}** 채널 비활성화")
            return
        else:
            # 상태 표시
            channels = world_board.get_board_channels(ctx.channel_id)
            status = "✅ ON" if channels.get(ch_name) else "❌ OFF"
            await ctx.send(f"{ch_display} 상태: {status}\n사용법: `!게시판 {parts[0]} on/off`")
            return

    # 전체 on/off
    if arg in ("on", "켜기", "true"):
        domain_manager.toggle_module(ctx.channel_id, "board", True)
        await ctx.send("✅ **게시판 모듈** 활성화")
        return
    if arg in ("off", "끄기", "false"):
        domain_manager.toggle_module(ctx.channel_id, "board", False)
        await ctx.send("❌ **게시판 모듈** 비활성화")
        return

    # 상태 표시 (기본)
    modules = domain_manager.get_active_modules(ctx.channel_id)
    board_on = "board" in modules
    channels = world_board.get_board_channels(ctx.channel_id)
    freqs = world_board.get_all_frequencies(ctx.channel_id)
    # [2026-08-16 도착물 라우트] 착지 방식도 같이 — 빈도만 보이고 착지가 안 보이면
    #   "왜 스레드에 안 올라오지"가 미스터리가 된다.
    modes = world_board.get_all_display_modes(ctx.channel_id)
    _m = {"thread": "🧵", "button": "💌", "off": "❌"}
    lines = [
        f"📋 **게시판 모듈**: {'✅ ON' if board_on else '❌ OFF'}",
        f"  📋 공지: {'✅' if channels['bulletin'] else '❌'} ({freqs['bulletin']}턴 {_m.get(modes['bulletin'], '')})  |  📱 SNS: {'✅' if channels['sns'] else '❌'} ({freqs['sns']}턴 {_m.get(modes['sns'], '')})  |  💌 메시지: {'✅' if channels['message'] else '❌'} ({freqs['message']}턴 {_m.get(modes['message'], '')})",
        "",
        "사용법:",
        "  `!게시판 on/off` — 전체 모듈",
        "  `!게시판 공지/sns/메시지 on/off` — 개별 채널",
        "  `!게시판 빈도 N` — 전체 기본 빈도",
        "  `!게시판 빈도 sns 5` — 채널별 빈도",
        "  `!게시판 표시 메시지 버튼` — 착지 방식(🧵스레드/💌버튼/❌끄기)",
    ]
    await ctx.send("\n".join(lines))


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




def _build_chronicle_input(deep_memory: str, fermented: list, history: list) -> str:
    """연대기 생성을 위한 입력 텍스트 조립."""
    parts = []

    if deep_memory and isinstance(deep_memory, str) and deep_memory.strip():
        parts.append(f"## 장기 기억 (Deep Memory)\n{deep_memory[:3000]}")

    if fermented and isinstance(fermented, list):
        fermented_texts = []
        for entry in fermented:
            if not isinstance(entry, dict):
                continue
            summary = entry.get("summary", "")
            arc = entry.get("arc_observations", {})
            ts = entry.get("timestamp", "")
            text = f"[{ts}] {summary}" if summary else ""
            if isinstance(arc, dict) and arc.get("emotional_arc"):
                text += f" (감정: {arc['emotional_arc']})"
            if text:
                fermented_texts.append(text)
        if fermented_texts:
            parts.append("## 중기 기억 (Fermented)\n" + "\n".join(fermented_texts[-10:]))

    if history and isinstance(history, list):
        recent = history[-30:]
        hist_lines = []
        for h in recent:
            if isinstance(h, dict):
                role = h.get("role", "?")
                content = h.get("content", "")
                if content:
                    hist_lines.append(f"{role}: {content[:500]}")
        if hist_lines:
            parts.append(f"## 최근 대화 (Fresh History, last {len(hist_lines)} messages)\n" + "\n".join(hist_lines))

    return "\n\n---\n\n".join(parts) if parts else ""


async def _generate_session_chronicle(ctx: CommandContext) -> None:
    """Flash API로 세션 연대기 생성."""
    channel_id = ctx.channel_id
    d_data = domain_manager.get_domain(channel_id)

    # Gather all memory layers
    session_data = d_data.get("ai_session_memory", {})
    deep_memory = session_data.get("deep_memory", "") or d_data.get("deep_memory", "")
    fermented = session_data.get("fermented_history", []) or d_data.get("fermented_history", [])
    history = d_data.get("history", [])

    if not deep_memory and not fermented and not history:
        await ctx.send("⚠️ 기록된 세션 데이터가 없습니다.")
        return

    feedback = await ctx.message.channel.send("📜 **연대기를 작성하고 있습니다...**")

    chronicle_input = _build_chronicle_input(deep_memory, fermented, history)
    if not chronicle_input:
        try:
            await feedback.edit(content="⚠️ 요약할 데이터가 부족합니다.")
        except Exception:
            pass
        return

    import text_resources
    from google.genai import types

    try:
        response = await ctx.genai_client.aio.models.generate_content(
            model=config.MODEL_ID_FLASH,
            contents=[
                types.Content(role="user", parts=[types.Part(text=chronicle_input)])
            ],
            config=types.GenerateContentConfig(
                system_instruction=text_resources.CHRONICLE_SYSTEM_PROMPT,
                temperature=0.5,
                max_output_tokens=4096,
                safety_settings=config.SAFETY_SETTINGS,
            )
        )

        if response and response.text:
            chronicle_text = response.text.strip()

            # Store in domain
            chronicles = d_data.setdefault("chronicles", [])
            chronicles.append({
                "timestamp": time.time(),
                "content": chronicle_text,
                "type": "session_summary",
            })
            if len(chronicles) > 10:
                d_data["chronicles"] = chronicles[-10:]
            domain_manager.save_domain(channel_id, d_data)

            # Send to channel
            try:
                await feedback.delete()
            except Exception:
                pass

            await send_long_message(
                ctx.message.channel,
                f"📜 **[세션 연대기]**\n\n{chronicle_text}"
            )
        else:
            try:
                await feedback.edit(content="⚠️ 연대기 생성에 실패했습니다.")
            except Exception:
                pass
    except Exception as e:
        logger.error(f"[Chronicle] Generation failed: {e}", exc_info=True)
        try:
            await feedback.edit(content=f"⚠️ 연대기 생성 오류: {str(e)[:100]}")
        except Exception:
            pass


@registry.register("lores", category="Analysis", aliases=["연대기", "chronicle"], description="세션 연대기 (AI 요약 / 내보내기)")
async def cmd_lores(ctx: CommandContext) -> None:
    """!연대기 — AI 세션 요약 생성 / !연대기 내보내기 — 기존 텍스트 파일"""
    arg = ctx.raw_args.strip().lower()

    # Subcommand: export (기존 기능 유지)
    if arg.startswith(('내보내기', 'export', 'new', 'inc', '증분', '최신')):
        incremental = arg in ('new', 'inc', '증분', '최신') or '증분' in arg or 'new' in arg
        export_text, msg = game_system.export_chronicle_book(ctx.channel_id, incremental=incremental)
        if export_text:
            fname = f"Chronicles_{ctx.channel_id}_{'INC' if incremental else 'FULL'}.txt"
            await ctx.message.channel.send(msg, file=discord.File(io.StringIO(export_text), filename=fname))
        else:
            await ctx.send(msg)
        return

    # Default: AI Summary Generation
    await _generate_session_chronicle(ctx)


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
    "활력",
    category="Player",
    aliases=["기력", "평형", "평정", "멘탈", "mental", "vigor"],
    description="활력/평형 조회 및 설정"
)
async def cmd_mental(ctx: CommandContext) -> None:
    """!활력 [활력값] [평형값] - 활력/평형 수치 설정 (0-100)"""
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
            f"💪 **활력:** {v_info['emoji']} **{v_info['name']}** ({v_val}/100)\n"
            f"> {v_info.get('desc', '')}\n"
            f"😌 **평형:** {c_info['emoji']} **{c_info['name']}** ({c_val}/100)\n"
            f"> {c_info.get('desc', '')}"
        )
        return

    # [Set Mode] — !활력 80 or !활력 80 70
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
            f"💪 **활력 설정:** {v_target}/100 ({v_info['emoji']} {v_info['name']})\n"
            f"😌 **평형 설정:** {c_target}/100 ({c_info['emoji']} {c_info['name']})"
        )

    except ValueError:
        await ctx.send("⚠️ 올바른 숫자를 입력하세요. (예: `!활력 80` 또는 `!활력 80 70`)")


# [2026-08-11 로드아웃 삭제] !회상/로드아웃/장비설정 명령 폐기 — 비활성 등록 주석 + cmd_flashback 본체 제거.
# 유저 입력 소급 선언의 자동 감지(Theoria flashback_eval → Slot 30 회상 연출)는 명령과 무관하게 유지.


@registry.register("reset_npcs", category="Admin", aliases=["엔피씨초기화", "npc_reset"], description="세션 NPC 초기화")
async def cmd_reset_npcs(ctx: CommandContext) -> None:
    """!reset_npcs"""
    if not domain_manager.is_session_locked(ctx.channel_id):
        # Optional: Check admin implementation if needed, for now allow
        pass

    count = npc_manager.clear_session_npcs(ctx.channel_id)
    # [2026-07-28] 실제 보존 범위는 lore **+ manual**인데 안내는 lore만 말해
    # 손수 등록한 시트가 날아가는 줄 알게 했다(keep_sources=("lore","manual") 실측).
    await ctx.send(f"🧹 **세션 NPC 초기화 완료:** {count}명 삭제됨\n"
                   "_로어 NPC와 직접 등록한 NPC(`!npc추가`)는 유지됩니다._")


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
        rules_text = w.get("rules_text", "")
        if not rules and not rules_text:
            await ctx.send("📜 활성화된 특수 규칙이 없습니다.\n💡 출력 형식 규칙은 `!출력룰 목록`으로 확인하세요.")
            return

        msg = ["📜 **세계 규칙 목록**"]
        if rules_text:
            preview = rules_text[:300] + ("..." if len(rules_text) > 300 else "")
            msg.append(f"**[파일 규칙]**\n{preview}")
        for k, v in rules.items():
            desc = v.get('desc', '') if isinstance(v, dict) else str(v)
            # "_" 접두 = 키워드 없이 등록된 규칙 — 라벨 대신 삭제용 핸들만 옅게 표기
            msg.append(f"- {desc}  〔{k}〕" if str(k).startswith("_") else f"- **{k}**: {desc}")
        msg.append("\n💡 출력 형식 규칙은 `!출력룰 목록`으로 확인하세요.")
        await send_long_message(ctx.message.channel, "\n".join(msg))
        return

    # 2. Add / Update
    if sub in ['add', '추가', 'set', '설정', 'a']:
        # 파일 첨부 → 원본 텍스트 그대로 저장
        if ctx.message.attachments:
            file_text, error = await read_attachment_text(ctx.message.attachments[0])
            if error:
                await ctx.send(error)
                return
            if not file_text:
                await ctx.send("⚠️ 파일 내용이 비어있습니다.")
                return
            w["rules_text"] = file_text.strip()
            domain_manager.update_world_state(ctx.channel_id, w)
            await ctx.send("📜 **추가룰 등록 완료** (파일 원본 주입)")
            return

        # [2026-07-28] 키워드는 **선택**. "키워드: 설명" 콜론 표기일 때만 라벨로 잡는다.
        # 구 동작은 첫 토큰을 무조건 키로 삼아 "!룰 추가 밤 통행금지 ..." → key="밤"으로 잘렸음.
        # raw_args 사용 → 설명의 줄바꿈·연속공백 보존(구 " ".join(args[2:])는 접힘).
        _parts = ctx.raw_args.strip().split(None, 1)
        body = _parts[1].strip() if len(_parts) > 1 else ""
        if not body:
            await ctx.send(
                "⚠️ 사용법: `!룰 추가 [설명]` · 라벨을 달려면 `!룰 추가 [키워드]: [설명]` 또는 파일 첨부"
            )
            return

        _m = re.match(r'^([^\s:]{1,20}):\s*(.+)$', body, re.S)
        if _m:
            key, desc = _m.group(1), _m.group(2).strip()
        else:
            # 자동 키 — "_" 접두는 Slot 23 렌더에서 라벨 억제 표식
            desc = body
            _n = 1
            while f"_{_n}" in rules:
                _n += 1
            key = f"_{_n}"

        rules[key] = {"desc": desc, "created_at": time.strftime('%Y-%m-%d')}
        w["location_rules"] = rules
        domain_manager.update_world_state(ctx.channel_id, w)
        if key.startswith("_"):
            await ctx.send(f"📜 **규칙 설정:** {desc}\n〔삭제하려면 `!룰 삭제 {key}`〕")
        else:
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
        w.pop("rules_text", None)
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
        # [2026-07-28] 구 메시지는 "NPC가 없음"과 "NPC는 있는데 관계가 아직 없음"을
        # 한 문장으로 뭉갰다. 둘은 사용자가 할 일이 다르다(등록 vs 기다리기).
        _npcs = domain_manager.get_npcs(ctx.channel_id) or {}
        if domain_manager._find_npc_key(_npcs, target):
            await ctx.send(f"👤 **{target}** — 아직 관계 기록이 없습니다. "
                           "함께 장면을 겪으면 쌓입니다.")
        else:
            _tl = target.lower()
            _cands = [k for k in _npcs if _tl in k.lower() or k.lower() in _tl][:5]
            await ctx.send(f"⚠️ '{target}' NPC를 찾을 수 없습니다."
                           + (f"\n혹시 이건가요: {', '.join(_cands)}" if _cands else ""))
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


async def _trigger_board(ctx: 'CommandContext', trigger: str = "time") -> None:
    """게시판 트리거 헬퍼 (백그라운드 실행)."""
    try:
        import world_board
        if ctx.genai_client and isinstance(ctx.message.channel, discord.TextChannel):
            await world_board.trigger_board_update(
                ctx.message.channel, ctx.genai_client,
                config.MODEL_ID_FLASH, ctx.channel_id, trigger=trigger,
            )
    except Exception as e:
        logging.getLogger("WorldBoard").debug(f"[WorldBoard] Trigger error: {e}")


@registry.register("time", category="World", aliases=["시간"], description="시간 조회 및 설정")
async def cmd_time(ctx: CommandContext) -> None:
    """!시간 [설정 시간대 / 진행 / N]"""
    args = ctx.args
    world = domain_manager.get_world_state(ctx.channel_id)

    if not args:
        # View
        time_emoji = {"새벽": "🌅", "오전": "☀️", "오후": "🌤️", "황혼": "🌆", "저녁": "🌙", "심야": "🌑"}
        emoji = time_emoji.get(world.get("time_slot", "오후"), "⏰")
        # hour/minute/year/month 초기화 보장 (V8.5 캘린더 확장)
        import game_world as _gw_view
        _gw_view._init_clock(world)
        msg = (
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"📅 **{_gw_view.format_calendar(world)}** {world.get('hour', 12):02d}:{world.get('minute', 0):02d}\n"
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
        # 게시판 트리거 (백그라운드)
        asyncio.create_task(_trigger_board(ctx, "time"))
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
        # 다중 진행 시 마지막 1회만 트리거
        asyncio.create_task(_trigger_board(ctx, "time"))
        return

    # Set time slot
    if first in ["설정", "set"]:
        if len(args) < 2:
            await ctx.send(
                "⚠️ 사용법:\n"
                "  `!시간 설정 [오전/오후/...]` (슬롯만)\n"
                "  `!시간 설정 HH:MM` (시:분, 같은 날)\n"
                "  `!시간 설정 N년 M월 D일` (날짜만, 기존 시각 유지)\n"
                "  `!시간 설정 N년 M월 D일 HH:MM` (풀 캘린더, V8.5)"
            )
            return
        target = args[1]
        time_slots = game_system.get_time_slots(ctx.channel_id)

        # V8.5: N년 M월 D일 [HH:MM] 풀 캘린더 형식 파싱 (HH:MM 생략 시 기존 시각 유지)
        # 예: !시간 설정 1년 3월 12일 15:05 / !시간 설정 2024년 1월 1일
        import re as _re
        if len(args) >= 4:
            rest = " ".join(args[1:])
            # HH:MM 포함
            cal_match = _re.match(r'^\s*(\d+)년\s+(\d+)월\s+(\d+)일\s+(\d{1,2}):(\d{2})\s*$', rest)
            # HH:MM 미포함 (날짜만)
            cal_match_date_only = _re.match(r'^\s*(\d+)년\s+(\d+)월\s+(\d+)일\s*$', rest) if not cal_match else None
            if cal_match or cal_match_date_only:
                try:
                    if cal_match:
                        yy = int(cal_match.group(1))
                        mo = int(cal_match.group(2))
                        dd = int(cal_match.group(3))
                        hh = int(cal_match.group(4))
                        mm = int(cal_match.group(5))
                        hh_provided = True
                    else:
                        yy = int(cal_match_date_only.group(1))
                        mo = int(cal_match_date_only.group(2))
                        dd = int(cal_match_date_only.group(3))
                        # 기존 시각 유지
                        import game_world as _gw_keep
                        _gw_keep._init_clock(world)
                        hh = world.get("hour", 12)
                        mm = world.get("minute", 0)
                        hh_provided = False
                    if not (1 <= yy and 1 <= mo <= config.CALENDAR_MONTHS_PER_YEAR
                            and 1 <= dd <= config.CALENDAR_DAYS_PER_MONTH
                            and 0 <= hh <= 23 and 0 <= mm <= 59):
                        await ctx.send(
                            f"⚠️ 유효 범위: 년≥1, 월=1~{config.CALENDAR_MONTHS_PER_YEAR}, "
                            f"일=1~{config.CALENDAR_DAYS_PER_MONTH}, HH=0~23, MM=0~59"
                        )
                        return
                    # slot 자동 추론
                    inferred_slot = None
                    for slot, (start, end) in config.TIME_SLOT_HOURS.items():
                        if start <= end:
                            if start <= hh <= end:
                                inferred_slot = slot; break
                        else:
                            if hh >= start or hh <= end:
                                inferred_slot = slot; break
                    if inferred_slot is None:
                        inferred_slot = world.get("time_slot", "오후")
                    world["year"] = yy
                    world["month"] = mo
                    world["day"] = dd
                    world["hour"] = hh
                    world["minute"] = mm
                    world["time_slot"] = inferred_slot
                    domain_manager.update_world_state(ctx.channel_id, world)
                    import game_world as _gw_set
                    if hh_provided:
                        await ctx.send(f"⏰ 시간 설정: **{_gw_set.format_calendar(world)} {hh:02d}:{mm:02d}** ({inferred_slot})")
                    else:
                        await ctx.send(
                            f"⏰ 시간 설정: **{_gw_set.format_calendar(world)} {hh:02d}:{mm:02d}** ({inferred_slot})"
                            f" — 시각은 기존 유지"
                        )
                except ValueError:
                    await ctx.send(f"⚠️ 형식 오류 (예: `!시간 설정 1년 3월 12일 15:05` 또는 `!시간 설정 1년 3월 12일`)")
                return

        # HH:MM 형식 파싱 (시:분만 — 진행 중 세션 마이그레이션용, 2026-05-23)
        hhmm_match = _re.match(r'^(\d{1,2}):(\d{2})$', target)
        if hhmm_match:
            try:
                hh = int(hhmm_match.group(1))
                mm = int(hhmm_match.group(2))
                if not (0 <= hh <= 23 and 0 <= mm <= 59):
                    await ctx.send(f"⚠️ 유효 범위: HH=0~23, MM=0~59 (입력: {hh}:{mm:02d})")
                    return
                # slot 자동 추론 (config.TIME_SLOT_HOURS 기준)
                inferred_slot = None
                for slot, (start, end) in config.TIME_SLOT_HOURS.items():
                    if start <= end:
                        if start <= hh <= end:
                            inferred_slot = slot
                            break
                    else:  # wrap (심야 23~3)
                        if hh >= start or hh <= end:
                            inferred_slot = slot
                            break
                if inferred_slot is None:
                    inferred_slot = world.get("time_slot", "오후")
                world["hour"] = hh
                world["minute"] = mm
                world["time_slot"] = inferred_slot
                # V8.5: year/month 초기화 (마이그레이션) 후 저장
                import game_world as _gw_set
                _gw_set._init_clock(world)
                domain_manager.update_world_state(ctx.channel_id, world)
                await ctx.send(f"⏰ 시간 설정: **{_gw_set.format_calendar(world)} {hh:02d}:{mm:02d}** ({inferred_slot})")
            except ValueError:
                await ctx.send(f"⚠️ 형식 오류: HH:MM (예: `!시간 설정 15:05`)")
            return

        # 기존: 슬롯 이름 입력 (slot만 변경, hour/minute 보존)
        if target in time_slots:
            world["time_slot"] = target
            domain_manager.update_world_state(ctx.channel_id, world)
            await ctx.send(f"⏰ 시간 설정: **{target}** (HH:MM 변경 안 됨 — 분 단위는 `!시간 설정 HH:MM`)")
        else:
            await ctx.send(f"⚠️ 유효한 시간대: {', '.join(time_slots)} / 또는 HH:MM 형식")
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
        asyncio.create_task(_trigger_board(ctx, "observation"))
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

    content = json.dumps(backup_data, ensure_ascii=False, indent=2)
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
        backup_data = json.loads(raw)
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

