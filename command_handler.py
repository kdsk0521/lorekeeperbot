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

# ⛔[2026-09-05 삭제] handle_participant_command — registry.dispatch로 바로 넘기기만 하던
#   스텁. 유일한 참조가 tests/health_check.py의 hasattr 검사였고 그것도 같이 지웠다.

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
    # [2026-09-24 감사] reset_lore = reset_domain(채널 폴더 통째 — state·memory.db·NPC·기록 전부)인데 확인 없이
    #   한 줄로 실행되고 안내는 "로어 초기화됨"뿐이었다. `!리셋`/`!클리어`처럼 확인 단어를 요구한다.
    _lore_args = full_content.split()
    if _lore_args and _lore_args[0] == "초기화" and len(_lore_args) <= 2:
        if len(_lore_args) < 2 or _lore_args[1].lower() not in ("확인", "confirm", "y", "yes"):
            await ctx.send("⚠️ `!로어 초기화`는 로어만이 아니라 **이 채널 데이터 전부**(기록·NPC·관계·상태)를 지웁니다.\n"
                           "진행하려면 `!로어 초기화 확인`을 입력하세요.")
            return
        domain_manager.reset_lore(channel_id)
        await ctx.send("📜 **로어 초기화됨** (채널 데이터 전체 리셋)")
        return
        
    # 3. Export
    # [2026-09-24 감사] 하위 명령을 첫 낱말로 판정 — 전엔 전문 비교라 `추출 new`(증분)가 도달 불가였고,
    #   그 입력이 4번 갱신 분기로 떨어져 **명령 문자열이 로어로 저장**되고 heavy 분석 콜 1회가 낭비됐다.
    if _lore_args and _lore_args[0].lower() in ['추출', 'export'] and len(_lore_args) <= 2:
        # Check for incremental argument
        incremental = False
        args = _lore_args
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
            unified_res = await cognition.analyze_lore_unified(ctx.genai_client, config.role_model("heavy"), full_content)

            if not unified_res or not any(unified_res.get(k) for k in ("npcs", "genres", "lore_summary")):
                logger.warning("[LoreAnalyzer] 분석 결과 비어있음 — 로어 텍스트만 저장")
                if file_text:
                    domain_manager.set_lore(channel_id, full_content)
                else:
                    domain_manager.append_lore(channel_id, full_content)
                lore_chunks = _split_lore_chunks(domain_manager.get_lore(channel_id) or full_content)  # [2026-09-24 감사] 누적 전문
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
                 # [2026-09-16 시트 2차] 대기 상자 — 원문은 로어의 그 인물 절(콜 0), 못 찾으면 추출 필드를 원문 대용으로.
                 _pc_sec = npc_manager.extract_npc_sections_from_lore(full_content, [pc_info["name"]])
                 pc_info = npc_manager.pc_box_from_info(pc_info, _pc_sec.get(pc_info["name"], ""))
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

            # 4.6 [2026-09-14 W5] 로어 세력 → faction 페이지(조직도의 뿌리). 새 콜 0.
            #   자리: lore_summary_data가 저장된 **직후**(지시서는 `set_lore_summary_data` 직후라
            #   적었지만 이 명령은 그 함수를 안 쓴다 — d_data 직접 저장이 이 파일의 등록 관문이다).
            try:
                if getattr(config, "WIKI_PLACES", False):
                    import wiki_store as _ws_w5
                    for _f in (lore_summary_data.get("factions") or []):
                        _ws_w5.sync_faction_page(channel_id, _f)
            except Exception as _e_w5:
                logger.debug(f"[Wiki] faction pages skipped: {_e_w5}")

            if file_text:
                domain_manager.set_lore(channel_id, full_content)
            else:
                domain_manager.append_lore(channel_id, full_content)

            # 6. Chunk splitting (V5 — 선택적 주입용)
            # [2026-09-24 감사] 청크는 **누적된 로어 전문**으로 — 텍스트 추가(append) 경로에서 이번 조각만 잘라
            #   set_lore_chunks(전량 교체)하면 앞서 올린 부분이 벡터 랭킹·relevant_chunks 에서 빠졌다.
            lore_chunks = _split_lore_chunks(domain_manager.get_lore(channel_id) or full_content)
            if lore_chunks:
                domain_manager.set_lore_chunks(channel_id, lore_chunks)

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
            
            # [2026-09-16 시트 2차 §8] 원문 = 페이지 lore 절(정본). heavy 콜은 조각(passives/inventory)·
            #   이름·species 추출용으로만. AI 실패 폴백 = 원문 그대로(파서가 절을 못 잡으면 Notes).
            analysis = {}
            if ctx.genai_client:
                analysis = await cognition.analyze_character_sheet(ctx.genai_client, config.role_model("heavy"), full_arg) or {}
            box = npc_manager.pc_box_from_info(analysis, full_arg)
            if not analysis:
                box["sheet_fallback"] = True   # AI 실패 폴백: 원문 그대로 Notes 절
            domain_manager.set_default_pc_info(ctx.channel_id, box)
            if domain_manager.apply_pc_info_to_user(ctx.channel_id, uid):
                _tail = "" if analysis else " (AI 분석 실패 — 원문만 저장)"
                await status_msg.edit(content=f"✅ **캐릭터 시트 등록 완료**{_tail}\n특성: {len(box.get('passives', []))}개 추출\n원문은 캐릭터 페이지에 절 단위로 저장되었습니다.")
            else:
                await status_msg.edit(content="📦 **시트 대기 중** — `!가면 [이름]`으로 캐릭터를 세우면 적용됩니다.")
            return

    # 1. Profile — [2026-09-16 시트 2차] 서술은 PC 페이지 lore 절(+Observed)에서.
    mask_name = p_data.get("mask", "Unknown")
    mem = p_data.get("ai_memory", {})
    msg = [f"🎭 **{mask_name}**"]
    _sheet_view = domain_manager.get_pc_sheet_text(ctx.channel_id, uid)
    if _sheet_view:
        msg.append(_sheet_view)
    elif not domain_manager.get_pc_page_id(ctx.channel_id, uid):
        msg.append("_(캐릭터 페이지 없음 — `!가면 [이름]`)_")
    status_text = game_character.format_status_effects(p_data.get("status_effects", [])) or "정상"
    msg.append(f"**상태:** {status_text}")
    
    # 2. Relations — [2026-09-15 관계 통합] 존재하지 않던 mem["relations"] 읽기(항상 빈 칸) 수리:
    #   이 PC를 target으로 하는 NPC→PC 엣지(파생 attitude + stance).
    try:
        _rels = domain_manager.get_npc_attitudes(ctx.channel_id, pc=mask_name) if mask_name != "Unknown" else {}
    except Exception:
        _rels = {}
    if _rels:
        rel_txt = []
        for r_name, r in sorted(_rels.items(), key=lambda kv: -abs(int(kv[1].get("bond", 0) or 0))):
            _st = str(r.get("stance", "") or "").strip()
            rel_txt.append(f"- **{r_name}**: {r.get('attitude', 'neutral')}" + (f" — {_st}" if _st else ""))
        msg.append("\n**🤝 관계:**")
        msg.extend(rel_txt[:12])

    # 3. Passives (Traits + Titles)
    passives = mem.get("passives", [])
    if passives:
        p_list = []
        for p in passives:
            if isinstance(p, dict):
                # [2026-09-16 3차] tags 삭제 — 조각은 이름만 표시(모양 {name, desc, value, origin}).
                p_list.append(f"🔹 **{p.get('name', '?')}**")
            else:
                p_list.append(f"🔹 **{p}**")
        if p_list:
            msg.append("\n**✨ 특성:**")
            msg.append(" / ".join(p_list))

    # 4. Vigor/Composure — [Phase 2.5] 기력은 레지스트리 값(표시 무변경)
    vigor_data = mem.get("vigor", mem.get("mental", {}))
    composure_data = mem.get("composure", {})
    try:
        import custom_vars as _cv_info
        v_val = _cv_info.vigor_value(ctx.channel_id, ctx.user_id, mem)
        c_val = _cv_info.composure_value(ctx.channel_id, ctx.user_id, mem)
    except Exception:
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
    # [2026-09-16 시트 2차 §8] PC = 위키 인물 페이지 — 가면을 세우는 순간 페이지가 선다(uid 1:1).
    import wiki_store as _ws_mask
    _ws_mask.ensure_pc_page(ctx.channel_id, ctx.user_id, target)

    # Link PC info — 대기 상자 흡수(정규화 정확일치 또는 aliases, 부분일치 없음)
    pc = domain_manager.get_default_pc_info(ctx.channel_id)
    mapped_msg = ""

    if pc and domain_manager.pc_mask_matches(target, pc.get("name", ""), pc.get("aliases")):
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
    source_text = str(pc.get("sheet_text", "") or "").strip()
    if len(source_text) < 300:
        return ""
    try:
        sheet = await cognition.analyze_character_sheet(client, config.role_model("heavy"), source_text)
        pc = npc_manager.merge_character_sheet_into_pc(pc, sheet)
        domain_manager.set_default_pc_info(channel_id, pc)
        domain_manager.sync_matching_participants(channel_id, pc)
        n_pas, n_inv = len(pc.get("passives", [])), len(pc.get("inventory", []))
        if n_pas or n_inv or sheet:
            return f"\n🧩 PC 시트 자동보강: 패시브 {n_pas}개 / 소지품 {n_inv}개"
    except Exception as _e:
        logger.warning(f"[PC Enrich] 자동보강 실패(기본 정보로 진행): {_e}")
    return ""


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


def _format_schedule_lines(npc: dict) -> list:
    """신형 `schedule`({슬롯: {activity, location}})을 표시 줄로. 레거시 문자열형·부재는 [].
    [2026-09-03 R6] `!npc 일정` 단일 결과 표시와 `!npc <이름>` 조회가 같은 모양을 쓴다."""
    _sc = (npc or {}).get("schedule") if isinstance(npc, dict) else None
    if not isinstance(_sc, dict):
        return []
    _lines = []
    for _slot in getattr(config, "DEFAULT_TIME_SLOTS", []):
        _e = _sc.get(_slot)
        if not isinstance(_e, dict):
            continue
        _loc = str(_e.get("location", "") or "").strip()
        _lines.append(f"· {_slot}: {_e.get('activity', '')}" + (f" @ {_loc}" if _loc else ""))
    return _lines


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


# =========================================================
# [2026-09-02] 엔벨로프 — 인물 경계 **선언**
# =========================================================
# 병: 아래 캐스케이드 4모드는 경계를 **추측**한다(h2 헤더 / Name: 선언 / 콜론 줄).
#   외부 시트는 관례가 제각각이라 추측은 항상 *다음* 시트에서 진다. 실측(09-02):
#     `# 이름` + 불릿  → simple 폭발: NPC 키가 `종족`/`역할`로 등록되고 이름 줄은 버려짐
#     `# 이름` + `## 섹션` → h2 모드가 **섹션명을 인물로** 등록. 게다가 "성공"으로 보고된다.
# 처방: 사람이 태그로 경계를 선언하면 추측을 **0**으로 만든다. 07-28 harness 판정
#   ("자동 추출은 보조로 격하, 확실히 하고 싶으면 명시 라벨")을 인물 경계 축에 적용한 것.
# 표기 근거: npc_manager._HEADER_LINE에 `<(?P<xml>[^/<>]+?)>` 갈래가 **이미 있다**(로어 경로가
#   쓴다). 새 문법 발명이 아니라 이미 쓰는 표기를 등록 경로에도 들이는 것. 닫는 태그는
#   그 정규식이 `/`를 배제하므로 충돌하지 않는다.
# 설계: 파티쳇수정/npc/npc_sheet_ingest_spec_2026-09-02.md §3
_ENV_OPEN = re.compile(r'^<([^<>/]{1,120})>$')
_ENV_CLOSE = re.compile(r'^</([^<>/]{0,120})>$')


def _has_envelope(text: str) -> bool:
    return any(_ENV_OPEN.match(l.strip()) for l in (text or "").splitlines())


def _parse_envelope_sheets(raw_lines: list) -> tuple:
    """`<이름> … </이름>` 블록을 자른다. Returns (blocks, warnings).

    blocks=[(name, body)] / 엔벨로프가 하나도 없으면 ([], []) → 기존 캐스케이드로 폴백.
    **엔벨로프가 하나라도 있으면 내부의 h1·h2·`Name:`·`====`는 경계로 보지 않는다.** 그게 요점이다.

    관대함이 원칙이다(사람이 손으로 쓰는 표기다) — 닫힘 누락·이름 불일치·중첩을 전부 받아준다.
    다만 관대함이 조용하면 그게 다시 위 병(틀린 등록을 "성공"으로 보고)이 되므로,
    **추측으로 메꾼 자리마다 경고를 남긴다.** 짝만 맞으면 경고는 0줄이다.
    """
    if not any(_ENV_OPEN.match(str(l).strip()) for l in raw_lines):
        return [], []
    blocks, warns = [], []
    cur_name, cur_body, stray = None, [], 0
    stray_first = ""      # [2026-09-02] "1줄 무시"만으로는 **어느 줄인지** 알 수 없다 — 실물을 보여준다.
    for ln in raw_lines:
        s_ = str(ln).strip()
        mo, mc = _ENV_OPEN.match(s_), _ENV_CLOSE.match(s_)
        if mo:
            _nm = mo.group(1).strip()
            if '="' in _nm or "='" in _nm:      # 속성은 쓰지 않는다(라벨 사전이 이미 잡는다)
                _nm = _nm.split()[0]
                warns.append("태그 속성은 읽지 않습니다. 이름만 사용: `%s`" % _nm)
            if cur_name is not None:
                blocks.append((cur_name, "\n".join(cur_body)))
                warns.append("`%s`가 닫히기 전에 `%s`가 열려 그 지점에서 끊었습니다." % (cur_name, _nm))
            cur_name, cur_body = _nm, []
            continue
        if mc and cur_name is not None:
            _cn = mc.group(1).strip()
            if _cn and _cn != cur_name:
                warns.append("닫는 태그 이름이 다릅니다 (`%s` / `%s`). 닫힘으로 처리했습니다." % (cur_name, _cn))
            blocks.append((cur_name, "\n".join(cur_body)))
            cur_name, cur_body = None, []
            continue
        if cur_name is None:
            if s_:
                stray += 1
                if not stray_first:
                    stray_first = s_[:60] + ("…" if len(s_) > 60 else "")
            continue
        cur_body.append(str(ln).rstrip())
    if cur_name is not None:
        blocks.append((cur_name, "\n".join(cur_body)))
        warns.append("`%s` 닫는 태그가 없어 파일 끝까지를 본문으로 처리했습니다." % cur_name)
    if stray:
        # 흔한 원인: 첫 태그 앞 제목 줄 / 마지막 닫는 태그 뒤 꼬리 / 태그에 `/`가 섞인 줄
        # (`<A/>`·`<Lee/Kim>`는 여는 태그로 인식되지 않는다).
        warns.append("태그 밖 텍스트 %d줄이 무시되었습니다. 첫 줄: `%s`" % (stray, stray_first))
    return [(n, b.strip()) for n, b in blocks if n and b.strip()], warns


def _envelope_warning_tail(warns: list) -> str:
    """경고는 최대 2줄 + 그 외 N건. 5명 등록에 경고 12줄이면 그것대로 안 읽힌다."""
    if not warns:
        return ""
    tail = "".join("\n⚠️ " + w for w in warns[:2])
    if len(warns) > 2:
        tail += "\n⚠️ 그 외 %d건" % (len(warns) - 2)
    return tail


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
    # [2026-09-02] 엔벨로프도 병합 안전 신호 — 경계가 선언돼 있으면 흡수·유실이 없다.
    if all(_has_h2(t) for _, t in texts) or all(_has_envelope(t) for _, t in texts):
        return "\n\n".join(t for _, t in texts), []
    return texts[0][1], [fn for fn, _ in texts[1:]]


# =========================================================
# [2026-09-22 voice_seed §H] NPC 시트 보기·편집 입구
# =========================================================
#   NPC 시트 원문의 정본은 위키 인물 페이지 lore 절인데, 그걸 **꺼내 보고 직접 고칠 입구**가
#   없었다(사람 편집 경로는 PC 페이지 OOC 편집뿐). 시드가 그 절에 앉기 시작하면서 필요해진 자리다:
#   굴림이 앉힌 기전·seam·방백을 사람이 읽고, 마음에 안 들면 고쳐 쓸 수 있어야 한다.
#   편집은 `_npc_sheet_gate`(관문)가 아니라 `edit_lore_section` **직접**이므로 페이지 도장이
#   "seed"로 남는다 → 그래서 편집 뒤 `ensure_page(source="manual")`로 **명시 갱신** = 자연 승격
#   (그 순간부터 FROZEN 취급: 증류가 절을 덮지 않고, 몹 태그 대상에서도 빠진다).
#   새 최상위 명령어를 만들지 않는다 — `!npc` 서브커맨드(조작면 최소주의, `상태`와 같은 관례).
_NPC_SHEET_ACTIONS = ("set", "append", "remove")
_NPC_SHEET_USAGE = ("⚠️ 사용법: `!npc 시트 [이름]` (보기) · "
                    "`!npc 시트 [이름] [절이름] set|append|remove [본문…]` (편집)")


def _npc_sheet_split(rest: str) -> tuple:
    """`[이름] [절이름] set|append|remove [본문…]` → (이름, 절, 동작, 본문). 편집형이 아니면 (rest, "", "", "").

    이름에도 절 이름에도 공백이 흔하다(`Lee Ha-yoon(이하윤)` · `Core Traits`) → 토큰 위치로 가르지 않고
    **동작 토큰**(set/append/remove)을 기준점으로 삼는다. 그 앞은 이름+절인데, 절 이름은 enum
    안에서만 나오므로 뒤에서부터 1~3 단어를 붙여 보며 enum(대소문자 무시)에 걸리는 가장 긴 것을 절로 본다."""
    toks = str(rest or "").split()
    _secs = tuple(getattr(config, "WIKI_LORE_SECTIONS", {}).get("character") or ())
    _by_norm = {s.lower(): s for s in _secs}
    for i, t in enumerate(toks):
        if t.lower() not in _NPC_SHEET_ACTIONS:
            continue
        pre = toks[:i]
        for take in (3, 2, 1):
            if len(pre) <= take:
                continue                      # 이름이 없어지면 그건 절 이름이 아니다
            sec = _by_norm.get(" ".join(pre[-take:]).lower())
            if sec:
                return " ".join(pre[:-take]), sec, t.lower(), " ".join(toks[i + 1:])
        return "", "", "", ""                 # 동작은 있는데 절 이름을 못 찾음 = 사용법 오류
    return str(rest or "").strip(), "", "", ""


def _chunk_message(text: str, limit: int = 1900) -> list:
    """디스코드 2000자 벽 — 줄 경계로 나눈다(한 줄이 통째로 길면 그 줄만 잘라 보낸다)."""
    out, cur = [], ""
    for line in str(text or "").splitlines():
        while len(line) > limit:
            if cur:
                out.append(cur)
                cur = ""
            out.append(line[:limit])
            line = line[limit:]
        if len(cur) + len(line) + 1 > limit:
            out.append(cur)
            cur = line
        else:
            cur = (cur + "\n" + line) if cur else line
    if cur:
        out.append(cur)
    return out or [""]


async def _npc_sheet_subcommand(ctx, rest: str) -> None:
    """`!npc 시트 …` — 인물 페이지 lore 절 보기 / 한 칸 편집(set·append·remove)."""
    import wiki_store
    channel_id = ctx.channel_id
    name, section, action, body = _npc_sheet_split(rest)
    if not str(name or "").strip():
        await ctx.send(_NPC_SHEET_USAGE)
        return
    npcs = domain_manager.get_npcs(channel_id)
    key = domain_manager._find_npc_key(npcs, name.strip())
    if not key:
        await ctx.send(f"⚠️ NPC '{name.strip()}' 정보를 찾을 수 없습니다.")
        return
    pid = wiki_store.page_id_for("character", key)

    if not action:                                   # ── 보기
        secs = wiki_store.get_lore_sections(channel_id, pid)
        if not secs:
            await ctx.send(f"📄 **{key}** — 시트 절이 아직 없습니다. "
                           f"`!npc 시트 {key} Core Traits set [본문]` 으로 쓸 수 있습니다.")
            return
        _src = wiki_store.page_source(channel_id, pid)
        head = f"📄 **{key}** 시트" + (" _(시드 — 편집하면 작가 시트로 승격)_"
                                     if _src == wiki_store.SEED_SOURCE else "")
        for chunk in _chunk_message(head + "\n\n" + wiki_store.assemble_lore_text(secs)):
            await ctx.send(chunk)
        return

    if action != "remove" and not str(body or "").strip():   # ── 편집
        await ctx.send(_NPC_SHEET_USAGE)
        return
    _turn = int((domain_manager.get_world_state(channel_id) or {}).get("turn_index", 0) or 0)
    if not wiki_store.get_page(channel_id, pid):
        wiki_store.ensure_page(channel_id, "character", key, source="manual", turn=_turn)
    ok = wiki_store.edit_lore_section(channel_id, pid, section, body, action=action, turn=_turn)
    if not ok:
        await ctx.send(f"⚠️ 시트 편집 실패 — 절 `{section}` 을 쓰지 못했습니다.")
        return
    # 사람이 손댄 시트 = manual. 도장 갱신이 곧 승격이다(seed 도장이 여기서 걷힌다).
    wiki_store.ensure_page(channel_id, "character", key, source="manual", turn=_turn)
    await ctx.send(f"✅ **{key}** 시트 `{section}` {action} 완료. "
                   f"_(이제 작가 시트로 취급 — 자동 증류가 이 절을 덮지 않습니다.)_")


@registry.register("npc", category="World", aliases=["엔피씨", "addnpc", "npc정보", "npc추가"], description="NPC 관리 (조회/추가/삭제/별칭/병합/초기화)")
async def cmd_npc(ctx: CommandContext) -> None:
    """!npc [이름] 조회 | !npc추가 [이름]: [설명] (또는 파일 첨부) | !npc 삭제 [이름]
    | !npc 별칭 [이름] [별칭] | !npc 병합 [중복] [본체] | !npc 보이스카드 [이름]
    | !npc 상태 [이름] [active|down|dead]  ← [2026-08-11] 생존축 수동 확정
    | !npc 시트 [이름] | !npc 시트 [이름] [절이름] set|append|remove [본문…]
      ← [2026-09-22 voice_seed §H] 페이지 lore 절 보기·편집(편집=manual 승격)

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
        # [2026-09-05] `!reset_npcs` 흡수 — 세션 NPC 일괄 삭제.
        if parts[0].lower() in ('초기화', 'reset'):
            count = npc_manager.clear_session_npcs(channel_id)
            # [2026-07-28] 실제 보존 범위는 lore **+ manual**인데 안내는 lore만 말해
            # 손수 등록한 시트가 날아가는 줄 알게 했다(keep_sources=("lore","manual") 실측).
            await ctx.send(f"🧹 **세션 NPC 초기화 완료:** {count}명 삭제됨\n"
                           "_로어 NPC와 직접 등록한 NPC(`!npc추가`)는 유지됩니다._")
            return
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

        # Subcommand: 시트 / sheet — 인물 페이지 lore 절 보기·편집 (voice_seed §H 입구)
        if parts[0].lower() in ('시트', 'sheet'):
            await _npc_sheet_subcommand(ctx, parts[1].strip() if len(parts) > 1 else "")
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
                has_voice = (npc_manager.lore_has_voice_block(npc_manager.npc_lore_sections(channel_id, key))  # [시트 2차b] 절 직접
                             or data.get("tone") or data.get("speech"))
                if has_voice and not single:
                    skipped.append(key)
                    continue
                voice = await cognition.extract_voice_card(ctx.genai_client, config.role_model("heavy"), key, desc)
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

        # Subcommand: 일정 / schedule 추출 (스펙 §6 R6 ② / §2.8)
        # [2026-09-03 R6] 병: `schedule` 필드는 소비자만 있고 **생산자가 0곳**이었다.
        #   시트 파서도 등록 경로도 이 필드를 안 만들어서, R6 자율 이동이 읽을 재료가
        #   세션 어디에도 없었다(기능이 켜져도 아무도 안 움직인다).
        # 처방: 보이스카드와 같은 부류의 1회성 사용자 명령. 턴 경로에는 콜을 안 붙인다.
        #   등록(`!npc 추가`) 경로에도 안 붙인다 - 178KB 3분할 업로드에 NPC당 콜을 끼우면
        #   느려지고 실패 지점이 하나 는다. 필요할 때 사람이 부른다.
        if parts[0].lower() in ('일정', '스케줄', 'schedule', 'sched'):
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
                # 인자 없으면 전체 일괄 (description 100자 이상 - 보이스카드와 같은 문턱)
                targets = {k: v for k, v in npcs.items()
                           if len((v.get("description") or v.get("desc", "")).strip()) > 100}
            if not targets:
                await ctx.send("🕰️ 일정 추출 대상 NPC가 없습니다.")
                return

            def _has_slot_schedule(_d):
                """이미 **신형**(딕셔너리) 일정을 가졌나. 레거시 문자열형은 '없음'으로
                친다 - 문자열은 장소가 없어 이동을 못 하므로 승격 대상이다."""
                _s = (_d or {}).get("schedule")
                return (isinstance(_s, dict) and bool(_s)
                        and any(isinstance(_v, dict) for _v in _s.values()))

            await ctx.send(f"🕰️ 일정 추출 중... (대상 {len(targets)}명)"
                           f"{'' if single else ' - 이미 시간대별 일정이 있는 NPC는 건너뜀'}")
            done, skipped = [], []
            for key, data in targets.items():
                desc = (data.get("description") or data.get("desc", "")).strip()
                if _has_slot_schedule(data) and not single:
                    skipped.append(key)
                    continue
                sched = await cognition.extract_schedule(
                    ctx.genai_client, config.role_model("heavy"), key, desc)
                if sched:
                    data["schedule"] = sched
                    domain_manager.update_npc(channel_id, key, data)
                    done.append(key)
                else:
                    # 빈 결과는 **저장하지 않는다** - 근거 없는 {}로 기존 값을 지우면
                    #   추출 한 번 실패가 곧 데이터 삭제가 된다.
                    skipped.append(key)

            msg = f"🕰️ **일정 추출 완료** - 생성 {len(done)}명"
            if done:
                msg += f": {', '.join(done[:10])}" + (" 등" if len(done) > 10 else "")
            if skipped:
                msg += f"\n(건너뜀 {len(skipped)}명: 이미 일정 있음/시트에 루틴 없음/추출 실패)"
            if single and done:
                _lines = _format_schedule_lines(domain_manager.get_npc(channel_id, done[0]))
                if _lines:
                    msg += f"\n\n**{done[0]} 일정:**\n" + "\n".join(_lines)
            elif done:
                msg += "\n각 NPC 일정은 `!npc <이름>`으로 확인하세요."
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

        # --- Phase -1: 엔벨로프 (인물 경계 **선언**) — 있으면 추측 0 ---
        _env_blocks, _env_warns = _parse_envelope_sheets(raw_lines)
        if _env_blocks:
            _env_names = [
                _register_npc(channel_id, _n, _b, _extract_id_fields(_b.splitlines()))
                for _n, _b in _env_blocks
            ]
            _env_tail = _envelope_warning_tail(_env_warns)
            if _skipped_files:
                _env_tail += ("\n⚠️ 첨부 파일은 **하나만** 등록됩니다 — 무시됨: "
                              + ", ".join(_skipped_files[:5]))
            _shown = ", ".join(_env_names[:10])
            if len(_env_names) > 10:
                _shown += " 외 %d명" % (len(_env_names) - 10)
            await ctx.send("👥 **NPC 등록:** %s%s" % (_shown, _env_tail))
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
        def _npc_preview(n: str, d: dict) -> str:
            if d.get("summary"):
                return d["summary"][:60]
            desc = npc_manager._npc_desc_fallback(d, channel_id=channel_id, name=n) or "-"
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
            if not _show_all and npc_manager.get_npc_tier(d, channel_id=channel_id, name=n) == "provisional":
                _prov.append((n, d))
            else:
                _est.append((n, d))
        name_list = [f"• **{n}**: {_npc_preview(n, d)}" for n, d in _est]
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
            desc_text = npc.get("description") or npc.get("desc", "") or npc_manager._npc_desc_fallback(npc, channel_id=channel_id, name=display_name)
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
            # [2026-09-03 R6 검수] `!npc 일정` 배치 완료 메시지가 "여기서 확인하라"고 가리키는데
            #   정작 이 조회엔 일정 줄이 없었다(가리키는 곳이 빈 자리). 신형(딕셔너리) 일정만 표시.
            _sch_lines = _format_schedule_lines(npc)
            if _sch_lines:
                msg.append("**일정:**\n" + "\n".join(_sch_lines))

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


# [2026-09-06 P7] 옛 문구 "화면 청소 (데이터 유지)"는 코드와 **반대**였다 —
#   execute_clear 는 soft reset(진행 상황 삭제) + 채팅 purge 다. 명령 목록만 읽고
#   `!클리어`를 누른 유저가 세션을 날리는 사고가 이 한 줄에서 났다.
@registry.register("clear", category="Admin", aliases=["클리어", "청소"],
                   description="세션 초기화 (로어·룰·참가자·NPC 시트·출력 선언 유지, 진행 상황 삭제)")
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
    orchestration = get_orchestration_runtime(ctx.genai_client, ctx.model_id, config.role_model("flash"))

    if not orchestration:
        await ctx.send("⚠️ AI 서비스가 초기화되지 않았습니다.")
        return

    edited_input = ctx.raw_args.strip() or None
    await orchestration.retry_last(ctx.message, ctx.channel_id, edited_input=edited_input)


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


@registry.register("lock", category="System", aliases=["잠금"], description="세션 진행/일시정지 (인자 없음=토글, `on/off`=명시)")
async def cmd_lock(ctx: CommandContext) -> None:
    """!잠금 — 토글 / !잠금 on|off|해제 — 명시.

    [2026-09-05] `!해제`(unlock) 흡수. 별칭에 `해제`를 두지 않는 이유: `!해제`가 토글이면
    잠기지 않은 세션에서 오히려 잠긴다 — 반대로 도는 명령은 만들지 않는다."""
    arg = ctx.raw_args.strip().lower()
    if arg in ('on', '켜기', 'true', '잠금'):
        target = True
    elif arg in ('off', '끄기', 'false', '해제', '풀기'):
        target = False
    elif not arg:
        target = not domain_manager.is_session_locked(ctx.channel_id)
    else:
        await ctx.send("⚠️ 사용법: `!잠금` (토글) · `!잠금 on` · `!잠금 off`")
        return

    domain_manager.set_session_lock(ctx.channel_id, target)
    # [2026-09-24 감사] 안내를 실제 동작에 맞춘다 — `session_locked` 는 "세션 진행 중" 플래그라(main 2단계 게이트)
    #   해제하면 채팅이 **무시**된다(구 안내 "자유롭게 참여 가능"은 반대였다).
    if target:
        await ctx.send("🔒 **세션 진행**: 채팅을 턴으로 받습니다.")
    else:
        await ctx.send("🔓 **잠금 해제 — 세션 일시정지**: 채팅(루카·OOC 포함)을 받지 않습니다. 재개는 `!잠금`.")


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
    # [2026-09-05] `!활력모듈` 흡수 — vigor는 toggle_module이 아니라 전용 세터를 쓴다.
    extra_mods = extra_mods + [("vigor", "활력")]
    _mod_aliases = {
        "board": "board", "게시판": "board",
        "mind": "mind", "속마음": "mind", "💭": "mind",
        "vigor": "vigor", "활력": "vigor", "평형": "vigor", "mental": "vigor",
    }
    _parts = arg.split()

    # 개별 토글: !모듈 속마음 off / !모듈 게시판 on
    if len(_parts) >= 2 and _parts[0] in _mod_aliases:
        _code = _mod_aliases[_parts[0]]
        _label = dict(extra_mods).get(_code, _code)
        if _parts[1] in ('on', '켜기', 'true'):
            if _code == "vigor":
                domain_manager.set_vigor_composure_active(ctx.channel_id, True)
            else:
                domain_manager.toggle_module(ctx.channel_id, _code, True)
            await ctx.send(f"✅ **{_label}** 모듈 활성화")
            return
        if _parts[1] in ('off', '끄기', 'false'):
            if _code == "vigor":
                domain_manager.set_vigor_composure_active(ctx.channel_id, False)
                await ctx.send("❌ **활력** 모듈 비활성화\n"
                               "이 채널의 활력/평형 처리·수치 변동·프롬프트 주입이 모두 중단됩니다. "
                               "(수치는 현재값으로 동결)")
                return
            domain_manager.toggle_module(ctx.channel_id, _code, False)
            await ctx.send(f"❌ **{_label}** 모듈 비활성화")
            return

    # 부가 모듈 일괄 토글
    if arg in ['on', '켜기', 'true', 'all']:
        for code, _ in extra_mods:
            if code == "vigor":
                domain_manager.set_vigor_composure_active(ctx.channel_id, True)
            else:
                domain_manager.toggle_module(ctx.channel_id, code, True)
        _names = " ✅ · ".join(name for _, name in extra_mods)
        await ctx.send(f"✅ **부가 모듈이 활성화되었습니다.**\n• {_names} ✅\n\n(판정/둠/이변은 항상 활성)")
        return
    if arg in ['off', '끄기', 'false', 'none']:
        for code, _ in extra_mods:
            if code == "vigor":
                domain_manager.set_vigor_composure_active(ctx.channel_id, False)
            else:
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
    for code, name in extra_mods:
        if code == "vigor":
            status = "✅ ON" if domain_manager.is_vigor_composure_active(ctx.channel_id) else "❌ OFF"
        else:
            status = "✅ ON" if code in active else "❌ OFF"
        msg.append(f"• {name} ({code}): {status}")
    msg.append("\n💡 `!모듈 활력 on/off` · `!게시판 on/off` · `!모듈 속마음 on/off` — 토글 모듈 제어")

    await ctx.send("\n".join(msg))

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

    # ⚰[2026-09-13 P14] `빈도` 하위 동사 삭제. WHY: 그 손잡이가 돌리던 것(턴 간격 게이트)이
    #   없어졌다 — 도착물은 이제 선언 전이 `deliver` 와 분석 신호 `arrival` 이 부를 때만 온다.
    #   저장만 되고 아무것도 안 움직이는 숫자를 명령 UI가 계속 보여 주면, 유저는 그걸
    #   레버로 읽고 안 듣는 세계를 탓한다. 명령 표면 순감(나머지 `!게시판` 동사는 유지).

    # [2026-08-16 도착물 라우트] 착지 모드: !게시판 표시 메시지 버튼|스레드|끄기
    #   새 명령어를 만들지 않는다 — 게시판의 착지 방식이므로 !게시판의 서브커맨드가 제자리.
    if len(parts) >= 1 and parts[0] in ("표시", "display", "착지"):
        mode_aliases = {
            "스레드": "thread", "thread": "thread",
            "버튼": "button", "button": "button",
            "끄기": "off", "off": "off", "없음": "off",
        }
        # [2026-08-17 v1.1 §3] 공지·SNS도 기본 button → 라벨을 💌 고정에서 "버튼"으로 중립화
        #   (실제 이모지는 채널종이 정한다: 메시지=💌 / 공지·SNS=📰).
        mode_display = {"thread": "🧵 스레드(공개)", "button": "🔘 버튼(누른 사람만)", "off": "❌ 끄기"}
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
            "  · 버튼 = 그 턴 산문에 버튼이 붙고(메시지 💌 / 공지·SNS 📰), 누른 사람만 봅니다.\n"
            "  · 스레드 = 공개 스레드에 게시(채널 전원이 봅니다). **명시해야만** 스레드가 생깁니다.\n"
            "  · 끄기 = 생성 자체를 안 합니다(저장도 없음)."
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
    # [2026-08-16 도착물 라우트] 착지 방식도 같이 — 어디에 어떻게 내려앉는지가 안 보이면
    #   "왜 스레드에 안 올라오지"가 미스터리가 된다.
    modes = world_board.get_all_display_modes(ctx.channel_id)
    _m = {"thread": "🧵", "button": "🔘", "off": "❌"}
    lines = [
        f"📋 **게시판 모듈**: {'✅ ON' if board_on else '❌ OFF'}",
        f"  📋 공지: {'✅' if channels['bulletin'] else '❌'} {_m.get(modes['bulletin'], '')}  |  📱 SNS: {'✅' if channels['sns'] else '❌'} {_m.get(modes['sns'], '')}  |  💌 메시지: {'✅' if channels['message'] else '❌'} {_m.get(modes['message'], '')}",
        "",
        "도착물은 **선언이 부를 때** 옵니다 — 전이의 `deliver` 칸(규칙이 정한 때)이나",
        "장면 자체가 부를 때. 턴 수로 재는 자동 게시는 없습니다.",
        "",
        "사용법:",
        "  `!게시판 on/off` — 전체 모듈",
        "  `!게시판 공지/sns/메시지 on/off` — 개별 채널",
        "  `!게시판 표시 메시지 버튼` — 착지 방식(🧵스레드/💌버튼/❌끄기)",
    ]
    await ctx.send("\n".join(lines))


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
    """세션 연대기 생성. [2026-08-18] 역할=main — 발효·연대기 라인과 **같은 모델**로 통일
    (종전 "flash"였다: 같은 연대기가 자동 발효(main)와 !연대기(flash) 두 모델로 갈렸다)."""
    channel_id = ctx.channel_id
    d_data = domain_manager.get_domain(channel_id)

    # Gather all memory layers
    session_data = d_data.get("ai_session_memory", {})
    # [V10 P3 / 2026-09-05] fermented/deep는 게터(read-through) 경유.
    _row_deep, _ = domain_manager.get_deep_memory(channel_id)
    _row_fermented = domain_manager.get_fermented_history(channel_id)
    deep_memory = session_data.get("deep_memory", "") or _row_deep
    fermented = session_data.get("fermented_history", []) or _row_fermented
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
            model=config.role_model("main"),
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
            # [2026-09-05 발효 계약] await(LLM) 뒤이므로 live 도메인을 다시 뜬다.
            # 아래 대입~save_domain 사이 await 0 (동기 RMW).
            d_data = domain_manager.get_domain(channel_id)
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


def classify_ooc_type(ooc_content: str, channel_id: str = "") -> str:
    """OOC 내용 분류

    [2026-09-10 P13] `channel_id` 를 받으면 **그 채널의 선언 이름**도 편집 신호로 센다
    (값·기록 섹션·서술 섹션·형식·전이 이름 + 레코드 항목 이름 + 시스템 변수 기력·평형).
    하드코딩 키워드 목록은 한 글자도 안 바뀐다 — 채널을 안 주면 종전 분류 그대로다.

    ★이름 검사가 서사 키워드보다 **먼저** 선다: "금을 50으로 해줘"의 `해줘`는 서사 요청
      키워드지만 그 문장은 편집이다. 이름이 있으면 그 이름이 문장의 주어다.
    """
    content_lower = ooc_content.lower()
    
    # 수정, 설정 관련 키워드
    edit_keywords = [
        "추가", "삭제", "수정", "설정", "인벤토리", "골드", "돈", "아이템", 
        "획득", "잃음", "관계", "호감도", "패시브", "특성", "제거", "변경",
        "합쳐", "정리", "통합"
    ]
    if any(kw in content_lower for kw in edit_keywords):
        return "edit"

    # [2026-09-10 P13] 그 채널이 이름으로 아는 것이 본문에 있으면 편집이다.
    #   판정면은 `mentioned_names`(부분 일치) — 급식 게이트와 같은 자리를 쓴다.
    if channel_id:
        try:
            import custom_vars as _cv_cls
            if _cv_cls.mentions_declared(str(channel_id), ooc_content):
                return "edit"
        except Exception as _e_decl:
            logging.debug(f"[OOC] 선언 이름 분류 skip: {_e_decl}")

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
    ooc_type = classify_ooc_type(ooc_content, channel_id)
    
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
        
        # [2026-09-10 P13] 선언 블록 급식 — 콜 0(저장분 읽기뿐). 선언이 없으면 "" 라
        #   프롬프트도 종전 그대로다.
        declared_txt = ""
        try:
            import custom_vars as _cv_ooc
            declared_txt = _cv_ooc.build_declared_block(channel_id)
        except Exception as _e_db:
            logging.debug(f"[OOC] 선언 블록 skip: {_e_db}")

        # [2026-09-15 관계 통합] 관계 현재값 = NPC→이 PC 엣지(ai_memory.relationships 삭제).
        _rel_state = {}
        try:
            _mask_ooc = (p_data or {}).get("mask")
            if _mask_ooc:
                for _nn, _aa in domain_manager.get_npc_attitudes(channel_id, pc=_mask_ooc).items():
                    _rel_state[_nn] = {"bond": _aa.get("bond", 0), "tension": _aa.get("tension", 0),
                                       "stance": _aa.get("stance", "")}
        except Exception as _e_rs:
            logging.debug(f"[OOC] 관계 상태 skip: {_e_rs}")

        # [2026-09-16 시트 2차] 시트 현재값 = PC 페이지 lore 절(없으면 빈 dict — 편집 시 !가면 안내).
        _sheet_state = {}
        try:
            import wiki_store as _ws_ooc
            _pid_ooc = domain_manager.get_pc_page_id(channel_id, uid)
            if _pid_ooc:
                _sheet_state = _ws_ooc.get_lore_sections(channel_id, _pid_ooc)
        except Exception as _e_ss:
            logging.debug(f"[OOC] 시트 상태 skip: {_e_ss}")
        _sheet_fields = set(getattr(config, "WIKI_LORE_SECTIONS", {}).get("character", ()))

        # AI 처리
        result = await memory_system.process_ooc_memory_edit(
            client_genai, model_id, ooc_content, ai_mem, p_data, notebook_text=notebook_txt,
            declared_block=declared_txt, relations_state=_rel_state, sheet_sections=_sheet_state
        )
        
        if result and result.get("edits"):
            # 1. Separate Notebook Edits vs Memory Edits
            mem_edits = []
            decl_edits = []
            rel_edits = []
            sheet_edits = []

            for edit in result["edits"]:
                field = edit.get("field")
                action = edit.get("action")
                value = edit.get("value")

                # [2026-09-10 P13] 선언 값 편집 — 관문 하나로 모아 뒀다가 한 번에 적용한다.
                if field == "declared":
                    decl_edits.append(edit)
                    continue
                # [2026-09-15 관계 통합] 관계 편집 → 엣지 직접 set(캡 면제, source="ooc").
                if field in ("relation", "relations", "relationships"):
                    rel_edits.append(edit)
                    continue
                # [2026-09-16 시트 2차] 시트 절 편집 → PC 페이지 lore 절(grow_sheet는 lore 절을 안 건드린다).
                if field in _sheet_fields:
                    sheet_edits.append(edit)
                    continue
                
                # Notebook Handling
                if field in ["notebook", "notes", "note"]:
                    # [notebook v2 2026-09-06] replace/set(전문 덮어쓰기) 폐지 —
                    # WHY: OOC 한 줄이 [소지품]·[일지]·다른 메모까지 통째로 날리던 유일한 통로였다.
                    # 남은 건 줄 단위 add/remove뿐(유저색 '-' 줄).
                    if action == "append":
                        game_system.add_memo(channel_id, value, uid)
                    elif action == "remove":
                        game_system.remove_memo(channel_id, value, uid)
                    continue # handled
                    
                mem_edits.append(edit)
            
            # 2. Apply Memory Edits
            if mem_edits:
                # [2026-09-24 감사] LLM 대기(위 await) **전에** 읽은 ai_mem/p_data 로 참가자 레코드를 통째 교체하면
                #   그 사이 배경 작업이 쓴 값(조각·일지·status)과 **이 루프 위에서 방금 한 노트북 편집**이
                #   되돌아갔다. 적용 직전에 다시 읽는다(여기서 저장까지 await 0 — 경합 창 없음).
                _fresh_p = domain_manager.get_participant_data(channel_id, uid) or p_data
                _fresh_mem = (_fresh_p or {}).get("ai_memory") or domain_manager.get_ai_memory(channel_id, uid) or ai_mem
                ai_mem = _fresh_mem
                new_mem, new_p_data = memory_system.apply_memory_edits(
                    _fresh_mem, mem_edits, _fresh_p
                )
                if isinstance(new_p_data, dict):
                    new_p_data["ai_memory"] = new_mem
                # [2026-09-10 P13 버그] 순서가 **거꾸로였다.** save_participant_data 는 참가자
                #   레코드를 통째로 갈아 끼우므로(merge 아님), 뒤에 서면 방금 쓴 ai_memory 를
                #   호출 전 스냅샷으로 되돌린다 — 외모·성격·관계·패시브 OOC 편집이 전부 조용히
                #   증발하던 자리다. 참가자 저장이 먼저, 기억 병합이 나중(그쪽이 다시 읽는다).
                domain_manager.save_participant_data(channel_id, uid, new_p_data)
                domain_manager.update_ai_memory(channel_id, uid, new_mem)
                # [2026-09-16 3차] origin=play 조각 삭제 = 발췌의 역연산 — desc 가 Observed 절 끝으로 돌아간다.
                try:
                    domain_manager.return_removed_play_fragments(channel_id, uid, ai_mem, new_mem)
                except Exception as _e_fr:
                    logging.warning(f"[OOC] 조각 되돌림 실패: {_e_fr}")
            
            # 2b. Apply Relation Edits — 엣지(NPC→이 PC). 되비침은 결과 메시지에 합류.
            rel_lines = []
            if rel_edits:
                try:
                    rel_lines = domain_manager.apply_ooc_relation_edits(channel_id, uid, rel_edits)
                except Exception as _e_rl:
                    logging.error(f"[OOC] 관계 편집 실패: {_e_rl}")

            # 2c. Apply Sheet Section Edits
            if sheet_edits:
                rel_lines = rel_lines + domain_manager.apply_ooc_sheet_edits(channel_id, uid, sheet_edits)

            # 3. Apply Declared Edits (P13) — 되비침 줄만 돌려받는다. 콜 0.
            decl_lines = []
            if decl_edits:
                try:
                    import custom_vars as _cv_ap
                    decl_lines = _cv_ap.apply_ooc_edits(channel_id, uid, decl_edits)
                except Exception as _e_ap:
                    logging.error(f"[OOC] 선언 편집 실패: {_e_ap}")

            # 결과 알림
            confirm = result.get('confirmation_message', '수정 완료')
            interp = result.get('interpretation', '')
            
            msg = f"📝 **OOC 처리 완료**\n"
            if interp: msg += f"> *{interp}*\n"
            msg += f"└ {confirm}"
            # 새 메시지 0 — 선언 편집 되비침은 **이 결과 메시지에 합류**한다.
            for _ln in (rel_lines + decl_lines)[:12]:
                msg += f"\n  {_ln}"
            
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
    # [2026-08-18 Phase 2.5] 기력의 정본은 레지스트리. 조회도 설정도 그쪽을 본다.
    try:
        import custom_vars as _cv_m
    except Exception:
        _cv_m = None

    # [View Mode]
    if not ctx.args:
        v_val = _cv_m.vigor_value(ctx.channel_id, uid, mem) if _cv_m else vigor.get("value", 100)
        c_val = _cv_m.composure_value(ctx.channel_id, uid, mem) if _cv_m else composure.get("value", 100)
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
        _c_now = _cv_m.composure_value(ctx.channel_id, uid, mem) if _cv_m else composure.get("value", 100)
        c_target = max(0, min(100, int(ctx.args[1]))) if len(ctx.args) > 1 else _c_now

        # 기력 = 레지스트리 쓰기(코드 소유, 캡 면제 — 운영자 수동 설정은 관측 델타가 아니다).
        #   옛 자리(ai_memory["vigor"])도 같이 맞춰 둔다: 레지스트리가 꺼진 채널의 폴백값이
        #   설정 직후에도 어긋나지 않게. 정본은 여전히 레지스트리다.
        _v_now = _cv_m.vigor_value(ctx.channel_id, uid, mem) if _cv_m else vigor.get("value", 100)
        if _cv_m and v_target != _v_now:
            _cv_m.apply_system_delta(ctx.channel_id, "기력", v_target - _v_now,
                                     "manual set", actor=uid, exempt_cap=True,
                                     source="command.활력")
        mem.setdefault("vigor", {})["value"] = v_target
        mem["vigor"]["last_delta"] = 0
        # [2026-09-06 P8b] 평형도 레지스트리 쓰기(코드 소유, 캡 면제 — 운영자 수동 설정은
        #   관측 델타가 아니다). 옛 자리도 같이 맞춰 둔다: 기능이 꺼진 채널의 폴백값이 어긋나지 않게.
        if _cv_m and c_target != _c_now:
            _cv_m.apply_system_delta(ctx.channel_id, "평형", c_target - _c_now,
                                     "manual set", actor=uid, exempt_cap=True,
                                     source="command.활력")
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


# [2026-08-18 대형식화 v0] `!출력룰 … 변수 …` 의 예약 키워드.
#   새 최상위 명령어를 만들지 않는다 — "출력·상태 저작 = !출력룰" 하나로 유지(조작면 최소주의).
_VAR_SUBKEYS = ("변수", "var", "vars", "variable", "변수선언")


async def _handle_custom_var_subcommand(ctx: CommandContext, sub: str, payload: str) -> None:
    """`!출력룰 추가/수정/삭제/목록 변수 …` 처리.

    저작 두 경로:
      (a) 반구조 파이프 문법 — `마나 | 0-100 | 시작 80 | PC | 마법 쓰면 준다`. 결정론.
      (b) 자연어 한 문장 → **1회성 변환 콜**(light). 실패하면 (a)가 폴백.
    두 경로 모두 같은 검증기를 지나고, 거부 시 규칙 요지를 그대로 동봉한다.
    """
    import custom_vars

    if not custom_vars.is_enabled():
        await ctx.send("⚠️ 변수 기능이 꺼져 있습니다 (`CUSTOM_VARS_ENABLED=0`).")
        return

    # 목록
    if sub in ('list', '목록', '조회', 'l') or not payload:
        await send_long_message(ctx.message.channel, custom_vars.format_list(ctx.channel_id))
        return

    # 삭제
    if sub in ('remove', 'delete', 'del', '삭제', '제거', 'r'):
        name = payload.strip()
        # [Phase 2.5] 시스템 변수는 코드가 심은 기관이라 지울 수 없다 — 대신 고칠 수 있다.
        _sys = custom_vars.system_name(name)
        if _sys:
            await ctx.send(
                f"🔒 `{_sys}`은(는) 코드가 심어 둔 시스템 변수라 지울 수 없습니다.\n"
                f"규칙과 캡은 고칠 수 있습니다: "
                f"`!출력룰 수정 변수 {_sys} | 상승 5 하강 7 | (새 규칙)`"
            )
            return
        if custom_vars.unregister(ctx.channel_id, name):
            await ctx.send(f"🗑️ **변수 삭제:** [{name}]")
        else:
            await ctx.send(f"⚠️ 변수 '{name}'(을)를 찾을 수 없습니다.")
        return

    # 추가 / 수정
    if sub not in ('add', '추가', 'set', '설정', 'a', '수정', 'edit', 'modify', 'update', 'u'):
        await ctx.send(
            "⚠️ 사용법: `!출력룰 [추가/수정/삭제/목록] 변수 …`\n\n" + custom_vars.RULES_TEXT
        )
        return

    existing = custom_vars.get_declarations(ctx.channel_id)
    spec = None
    src = "파이프"
    if '|' in payload:
        spec, perr = custom_vars.parse_pipe_declaration(payload)
        if spec is None:
            await send_long_message(ctx.message.channel, f"⚠️ {perr}\n\n{custom_vars.RULES_TEXT}")
            return
    else:
        # 자연어 경로 — 저작 시 **1회성** 콜(매턴 순증 0).
        notice = await ctx.send("📊 문장을 변수 선언으로 옮기는 중…")
        spec, cerr = await custom_vars.convert_natural_declaration(
            ctx.genai_client, config.role_model("light"), payload, list(existing.keys()),
        )
        try:
            if notice:
                await notice.delete()
        except Exception:
            pass
        if spec is None:
            await send_long_message(
                ctx.message.channel,
                f"⚠️ 자연어 변환에 실패했습니다 ({cerr}).\n아래 형식으로 직접 적어 주세요.\n\n"
                + custom_vars.RULES_TEXT,
            )
            return
        src = "자연어"

    clean, verr = custom_vars.validate_declaration(spec, existing)
    if clean is None:
        await send_long_message(ctx.message.channel, f"⚠️ {verr}")
        return

    ok, verb = custom_vars.register(ctx.channel_id, clean)
    if not ok:
        await ctx.send(f"⚠️ {verb}")
        return

    # [2026-08-18 v1] 타입마다 "모양"이 다르다 — 범위/단계/항목모드. 확인문은 등록된 그대로 되읽는다
    #   (유저가 방금 무엇을 만들었는지가 여기서 한 번에 보여야 파이프 저작의 오해가 즉시 잡힌다).
    _t = clean.get("type", "gauge")
    if _t == "enum":
        _shape = " > ".join(clean.get("stages") or [])
        if clean.get("monotonic"):
            _shape += " · 단조(역행 불가)"
    elif _t == "list":
        _ilo, _ihi = (clean.get("item_range") or [0, 0])[:2]
        _shape = f"{clean.get('item_mode', 'stock')} 항목 {_ilo}-{_ihi}"
    else:
        _lo, _hi = clean["range"]
        _shape = f"범위 {_lo}-{_hi}"
        if clean.get("max_gain") or clean.get("max_loss"):
            _shape += f" · 캡 +{clean.get('max_gain', '∞')}/-{clean.get('max_loss', '∞')}"
    _cur = (custom_vars.get_values(ctx.channel_id).get(clean["name"]) or {}).get("value", clean.get("init"))
    _cur_text = custom_vars.format_value(clean, _cur) if not isinstance(_cur, dict) else (
        "—" if not _cur else ", ".join(f"{k}" for k in list(_cur)[:4]))
    await ctx.send(
        f"📊 **변수 {verb}:** [{clean['name']}] — 현재 `{_cur_text}` ({_shape}, "
        f"{_t}, {clean['scope']})\n"
        f"> 규칙: {clean['rule']}\n"
        f"-# {src} 저작 · 매턴 산문 밑 상태 임베드에서 확인"
    )


def _sp_mark_is_panel(key) -> bool:
    """패널 정의 키는 형식 표식 밖 — 그건 형식이 아니라 상태창 정의다."""
    try:
        import status_panel as _sp
        return bool(_sp.is_panel_key(key))
    except Exception:
        return False


@registry.register("outputrule", category="World", aliases=["출력룰", "출력규칙", "outputrules", "출력"], description="출력 형식 규칙 관리 (Recency 슬롯)")
async def cmd_output_rule(ctx: CommandContext) -> None:
    """!출력룰 [추가/삭제/목록/초기화] [키워드] [내용]  — 파일 첨부 시 일괄 등록
    출력 형식 지시(상태창, 포맷 등)를 Recency 영역에 주입합니다.

    [2026-08-18] `변수` 키워드는 선언형 변수 레지스트리로 분기한다(별도 명령어 없음).
    """
    args = ctx.args
    if not args:
        sub = "list"
    else:
        sub = args[0].lower()

    # === 대형식화 분기 ===  `!출력룰 변수` (목록) / `!출력룰 추가 변수 …`
    if args and args[0].strip().lower() in _VAR_SUBKEYS:
        _p = ctx.raw_args.strip()[len(args[0]):].strip()
        await _handle_custom_var_subcommand(ctx, "add" if _p else "list", _p)
        return
    if len(args) >= 2 and args[1].strip().lower() in _VAR_SUBKEYS:
        _rest = ctx.raw_args.strip()
        _rest = _rest[len(args[0]):].lstrip()
        _rest = _rest[len(args[1]):].lstrip()
        await _handle_custom_var_subcommand(ctx, sub, _rest)
        return

    # [2026-09-06 P7] 형식 저작의 자리는 선언 층이다 — 읽기·쓰기 모두 헬퍼 한 쌍으로.
    rules = domain_manager.get_output_rules(ctx.channel_id)

    # 1. List
    if sub in ['list', '목록', '조회', 'l']:
        try:
            import status_panel as _sp_gate
            _has_sections = bool(_sp_gate.list_panel_sections(ctx.channel_id))
        except Exception:
            _has_sections = False
        if not rules and not _has_sections:
            await ctx.send("📋 활성화된 출력 규칙이 없습니다.")
            return
        # [2026-09-07 P9] 문구만 — 상태창의 자리가 산문 머리에서 매턴 하단 임베드로 옮겼다.
        # [2026-09-13 P9c] 문구만 — 💠 가 "쌓인 것"(노트북·기록·일지·도착물) 창으로 돌아왔다.
        msg = ["📋 **출력 규칙 목록** (Recency 슬롯 주입)",
               "🪧 상태창은 매턴 응답 **바로 밑 임베드**로 전 장 자동 표시됩니다 (최대 10장).",
               "💠 = 노트북·기록·일지·도착물 (매턴 버튼, 누른 사람에게만 보입니다)"]
        # [2026-09-07 P10] 표식만 — 같은 목록, 같은 순서. 형식이 **누구 손에 그려지는가**를
        #   한 낱말로 말한다: 템플릿=코드가 그림 · 문체=산문이 그림 · 도착물=편지 봉투 이름.
        try:
            import status_panel as _sp_mark
            _tpl_marks = set(_sp_mark.template_format_names(ctx.channel_id) or ())
            _mail_marks = set(_sp_mark.mail_format_names(ctx.channel_id) or ())
        except Exception:
            _tpl_marks = set(); _mail_marks = set()
        for k, v in rules.items():
            desc = v.get('desc', '') if isinstance(v, dict) else str(v)
            preview = desc[:80] + "..." if len(desc) > 80 else desc
            _mk = []
            if k in _tpl_marks:
                _mk.append("템플릿")
            elif not _sp_mark_is_panel(k):
                _mk.append("문체")
            if k in _mail_marks:
                _mk.append("도착물")
            _tag = f" `{'·'.join(_mk)}`" if _mk else ""
            msg.append(f"- **{k}**{_tag}: {preview}")
        # [2026-08-18 대형식화] 같은 명령어의 다른 문 — 선언 변수가 있으면 여기서 안내한다.
        try:
            import custom_vars as _cv_hint
            _decl = _cv_hint.get_declarations(ctx.channel_id)
            if _decl:
                msg.append(f"\n📊 선언 변수 {len(_decl)}개 — `!출력룰 목록 변수`")
        except Exception:
            pass
        # [2026-09-13 P16] 종류 표식 한 줄 — 지시는 값도 섹션도 아니라 위 목록 어디에도 안 뜬다.
        #   명령·버튼·문법 신설 0: 같은 목록의 한 줄이다.
        try:
            import expr_engine as _ee_dir
            _dirs = _ee_dir.list_directives(ctx.channel_id)
            if _dirs:
                msg.append(f"📐 지시(조건) {len(_dirs)}개 — 조건이 참인 턴에만 산문에 실립니다: "
                           + ", ".join(list(_dirs)[:10]))
        except Exception:
            pass
        # [2026-09-06 P1] 패널은 이제 output_rules 가 아니라 선언 층에 산다 —
        # 같은 명령의 목록에서 보이지 않으면 "등록했는데 사라졌다"로 읽힌다.
        try:
            import status_panel as _sp_hint
            _secs = _sp_hint.list_panel_sections(ctx.channel_id)
            if _secs:
                # [2026-09-09 P12] append 섹션은 **쌓이는 기록**이다 — 같은 목록에서
                #   rewrite 섹션과 구분되지 않으면 "왜 안 지워지나"로 읽힌다. 문구뿐이다.
                _snames = []
                for _n, _rec in _secs.items():
                    if (_rec or {}).get("mode") == "append":
                        _snames.append(f"{_n}(기록(최근 {(_rec or {}).get('keep')}))")
                    else:
                        _snames.append(_n)
                msg.append(f"💠 패널 섹션 {len(_secs)}개: " + ", ".join(_snames))
        except Exception:
            pass
        # [2026-09-06 P3] 파생값·전이도 같은 선언 층에 산다 — 목록에서 안 보이면
        #   "등록했는데 사라졌다"로 읽힌다. **표시만**이다(명령·하위 동사 신설 0).
        try:
            import expr_engine as _ee_hint
            _nd = len(_ee_hint.list_derives(ctx.channel_id))
            _nt = len(_ee_hint.list_transitions(ctx.channel_id))
            if _nd or _nt:
                msg.append(f"⚙ 파생 {_nd} · 전이 {_nt}")
        except Exception:
            pass
        # [2026-09-06 P6] 파일 하나가 항목 N개로 흩어졌으니, **어느 파일에서 왔는지**로
        #   묶어 보여준다. 안 묶으면 유저는 자기가 올린 파일과 등록물을 대조할 길이 없다.
        try:
            import output_router as _or_hint
            for _src, _names in sorted((_or_hint.names_by_source(ctx.channel_id)).items()):
                if not _src:
                    continue
                msg.append(f"📎 **{_src}** ({len(_names)}): " + ", ".join(_names))
        except Exception:
            pass
        # [2026-09-06 P7] 선언/값 층이 갈라졌으니 **수명**을 여기서 한 줄로 알린다 —
        #   기능이 아니라 문구다(선언 층은 클리어 생존, 값만 시작값으로).
        msg.append("ℹ 선언은 `!클리어` 뒤에도 남고, 값만 시작값으로 돌아갑니다.")
        await send_long_message(ctx.message.channel, "\n".join(msg))
        return

    # 2. Add / Update
    if sub in ['add', '추가', 'set', '설정', 'a']:
        # [2026-09-06 P6] 라우터 경로. 옛 "파일 = 규칙 1개"(키=파일명 → Slot 33 통짜)를
        #   **파일 = 항목 N개**로 바꾼다. 명령·하위 동사는 그대로고, 바뀐 건 목적지다.
        #   판별은 output_router.route_decision 한 곳 — 여기에 낱말 목록을 또 두지 않는다.
        try:
            import output_router as _or
            _dest = _or.route_decision(ctx.channel_id, args, ctx.raw_args,
                                       bool(ctx.message.attachments))
        except Exception as _e:
            logging.getLogger("OutputRule").warning(f"[P6] 판별 실패 — 옛 경로: {_e}")
            _or, _dest = None, "legacy"

        if _or is not None and _dest == "router":
            _src_name = ""
            if ctx.message.attachments:
                _att = ctx.message.attachments[0]
                file_text, error = await read_attachment_text(_att)
                if error:
                    await ctx.send(error)
                    return
                if not file_text:
                    await ctx.send("⚠️ 파일 내용이 비어있습니다.")
                    return
                _src_name = _att.filename
            else:
                file_text = ctx.raw_args.strip()
                _rest = file_text[len(args[0]):].strip() if args else file_text
                file_text = _rest or file_text
                _src_name = "한 줄 서술"
            await ctx.send("📋 준비물을 읽는 중… (등록 시 1회성 분석)")
            _result = await _or.route_text(ctx.genai_client, ctx.channel_id,
                                           file_text, _src_name)
            if _result.items:
                _names = [i.get("name") for i in _result.items]
                _missing = _or.missing_names(ctx.channel_id, _src_name, _names)
                _applied = _or.apply_items(ctx.channel_id, _result.items, _src_name)
                await send_long_message(
                    ctx.message.channel,
                    _or.format_reflection(_src_name, _applied, _missing))
                return
            # ★콜이 죽었거나(클라이언트 없음·안전필터·잘림) 읽어낼 항목이 0이면 **옛 경로가
            #   받는다.** 등록이 아예 안 되는 것보다 통짜 규칙 1개가 낫다 — 라우터는 자리를
            #   나누는 기계지 등록의 관문이 아니다. 아래 종전 갈래로 그대로 떨어진다.
            await ctx.send(f"⚠️ {_result.error or '읽어낼 항목이 없었습니다.'} "
                           "— 옛 방식(키 = 규칙 1개)으로 등록합니다.")

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
            domain_manager.set_output_rules(ctx.channel_id, rules)
            await ctx.send(f"📋 **출력규칙 등록 완료:** [{fname}]")
            return

        if len(args) < 3:
            await ctx.send("⚠️ 사용법: `!출력룰 추가 [키워드] [설명]` 또는 파일 첨부")
            return

        key = args[1]
        desc = " ".join(args[2:])
        # [2026-09-06 P1] panel/상태창 키는 **선언 층**(output_decl.panel_sections["기본"])에 쓴다.
        # 명령·하위 동사는 그대로다 — 바뀐 건 저장 자리 하나뿐(옛 output_rules 키엔 안 쓴다).
        try:
            import status_panel as _sp_reg
            _is_panel = _sp_reg.is_panel_key(key)
        except Exception:
            _is_panel = False
        if _is_panel:
            _ok, _verb = _sp_reg.register_panel_section(
                ctx.channel_id, _sp_reg.DEFAULT_PANEL_SECTION, desc)
            if not _ok:
                await ctx.send(f"⚠️ {_verb}")
                return
            await ctx.send(f"💠 **패널 섹션 {_verb}:** "
                           f"[{_sp_reg.DEFAULT_PANEL_SECTION}] - {desc}")
            return
        rules[key] = {"desc": desc, "created_at": time.strftime('%Y-%m-%d')}
        domain_manager.set_output_rules(ctx.channel_id, rules)
        await ctx.send(f"📋 **출력규칙 설정:** [{key}] - {desc}")
        return

    # 3. Remove
    if sub in ['remove', 'delete', 'del', '삭제', '제거', 'r']:
        if len(args) < 2:
            await ctx.send("⚠️ 사용법: `!출력룰 삭제 [키워드]`")
            return
        key = args[1]
        # [2026-09-06 P1] 저장 자리가 옮겨간 만큼 **지우는 자리**도 따라간다 —
        # 안 따라가면 `!출력룰 삭제 상태창` 이 조용히 아무것도 못 지운다.
        try:
            import status_panel as _sp_del
            _is_panel = _sp_del.is_panel_key(key)
        except Exception:
            _is_panel = False
        if _is_panel:
            _ok, _verb = _sp_del.remove_panel_section(
                ctx.channel_id, _sp_del.DEFAULT_PANEL_SECTION)
            if _ok:
                await ctx.send(f"🗑️ **패널 섹션 삭제:** [{_sp_del.DEFAULT_PANEL_SECTION}]")
                return
            # 선언 층에 없으면 옛 자리(output_rules)에 남아 있을 수 있다 — 아래 종전 갈래로.
        # [2026-09-06 P6] 저장 자리가 넷으로 흩어졌으니 **지우는 자리도 넷**이다 —
        #   목적지 정정이지 동사 신설이 아니다. 이름 하나면 네 관문에서, 파일명이면 그
        #   source 로 들어온 항목 전량.
        try:
            import output_router as _or_del
            _gone = _or_del.remove_name(ctx.channel_id, key)
            if _gone:
                await ctx.send(f"🗑️ **삭제:** [{key}] — " + " · ".join(_gone))
                return
            _batch = _or_del.remove_source(ctx.channel_id, key)
            if _batch:
                await ctx.send(f"🗑️ **[{key}] 항목 {len(_batch)}개 삭제:** "
                               + ", ".join(_batch))
                return
        except Exception as _e:
            logging.getLogger("OutputRule").warning(f"[P6] 삭제 경로 실패: {_e}")
        if key in rules:
            del rules[key]
            domain_manager.set_output_rules(ctx.channel_id, rules)
            await ctx.send(f"🗑️ **출력규칙 삭제:** [{key}]")
        else:
            await ctx.send(f"⚠️ 출력규칙 '{key}'(을)를 찾을 수 없습니다.")
        return

    # 4. Reset
    if sub in ['reset', '초기화', 'clear']:
        domain_manager.set_output_rules(ctx.channel_id, {})
        # [2026-09-06 P1] 패널 정의가 output_rules 를 떠났으니 여기서 같이 비운다 —
        # 안 그러면 "모든 출력규칙 초기화"가 패널만 남기는 반쪽 명령이 된다.
        try:
            import status_panel as _sp_clr
            _sp_clr.clear_panel_sections(ctx.channel_id)
        except Exception:
            pass
        await ctx.send("🗑️ **모든 출력규칙 초기화 완료**")
        return

    await ctx.send("⚠️ 사용법: `!출력룰 [목록/추가/삭제/초기화]` — 파일 첨부로 일괄 등록 가능")


# ⚰[2026-09-24 감사 §5-2 #19b] `_trigger_board`(명령 경로 게시판 트리거) 삭제.
#   `!시간 진행/N`·`!턴`(관찰)이 게이트 없이 게시판 콜을 돌려, P14 가 닫은 "아무도 부르지 않은 때 세계가
#   혼자 말을 건다" 문이 명령 두 곳에 남아 있었다(world_board 머리 묘비 참조). 도착물 방아쇠는 P14 의 둘뿐 —
#   ① 선언 전이 `deliver` ② 분석 신호 `arrival`(orchestration 4.75 핸드아웃).


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


@registry.register("turn", category="World", aliases=["턴", "진행", "건너뛰기", "next"], description="턴 진행 (수동모드: 축적 행동 일괄 처리) / `!턴 자동|수동|보조`로 응답 모드 설정")
async def cmd_turn(ctx: CommandContext) -> None:
    """!진행 — 축적된 행동 처리 또는 관찰 턴.

    [2026-09-05] `!모드`(mode) 흡수 — `!턴 자동|수동|보조`로 응답 모드 설정, `!턴 모드`로 조회.
    인자 없음은 기존 동작(수동모드 축적 행동 일괄 처리 / 관찰 턴) 그대로."""
    _MODE_KR = {'auto': '자동', 'waiting': '수동', 'assist': '보조'}
    _arg = ctx.raw_args.strip().lower()
    if _arg in ('모드', 'mode'):
        _curr = domain_manager.get_response_mode(ctx.channel_id)
        await ctx.send(f"⚙️ 현재 모드: **{_MODE_KR.get(_curr, _curr)}**\n"
                       "사용법: `!턴 자동` · `!턴 수동` · `!턴 보조`")
        return
    _mode_map = {'자동': 'auto', 'auto': 'auto',
                 '수동': 'waiting', 'waiting': 'waiting', 'manual': 'waiting',
                 '보조': 'assist', 'assist': 'assist'}
    if _arg in _mode_map:
        _mode = _mode_map[_arg]
        domain_manager.set_response_mode(ctx.channel_id, _mode)
        await ctx.send(f"⚙️ 모드 변경: **{_MODE_KR.get(_mode, _mode)}**")
        return

    from orchestration import get_orchestration_runtime
    orch = get_orchestration_runtime(ctx.genai_client, ctx.model_id, config.role_model("flash"))
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


@registry.register("help", category="System", aliases=["도움말", "도움", "명령어", "h"], description="명령어 목록")
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
    model_id_flash: str
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

