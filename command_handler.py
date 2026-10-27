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

# [2026-10-12 C30] 로어 분위기 절 — `## 분위기 …` / `## Tone …` 헤더 하나. 그 절 본문이 Slot 33 장르 지시문의
#   "The table's own note for this run" 한 줄 자리(custom_tone)에 **원문 그대로** 간다(분석이 지어 쓴 atmosphere_guide 대신).
#   매 턴 Slot 33에 실리므로 청크(Slot 8 선택 후보)에서는 뺀다. 절이 없으면 지금처럼 분석 값.
#   레티어스 "코드 고정문 + 로어에 적힌 걸로", "청크에서 빼자". 스펙 memory_lore/C30_분위기절·장소트리_스펙_2026-10-12.
_TONE_HEADER_RE = re.compile(r'^(?:분위기|tone)(?:\s|$|[—–\-:(（])', re.I)


def _is_tone_label(label: str) -> bool:
    """청크 라벨(헤더에서 `#`·끝 콜론을 뗀 글)이 분위기 절 머리인가."""
    return bool(_TONE_HEADER_RE.match(str(label or "").strip()))


def _lore_tone_section(lore_text: str) -> str:
    """로어에서 첫 분위기 절(`#`/`##` 헤더, 들여쓰기 1칸 이하)의 본문을 원문 그대로. 없으면 ""."""
    if not lore_text:
        return ""
    _major = re.compile(r'^#{1,2}\s+')
    _sep = re.compile(r'^[\s]*[=\-\*~]{3,}[\s]*$')
    body, inside = [], False
    for line in str(lore_text).split('\n'):
        stripped = line.strip()
        indent = len(line) - len(line.lstrip())
        if indent <= 1 and stripped and _major.match(stripped):
            if inside:
                break
            if _is_tone_label(stripped.lstrip('#').strip()):
                inside = True
            continue
        if inside and not _sep.match(line):
            body.append(line)
    return '\n'.join(body).strip()


def _apply_lore_tone(channel_id: str) -> bool:
    """쌓인 로어 전문에 분위기 절이 있으면 custom_tone = 그 본문. 있으면 True(분석 값 위에 덮는다)."""
    try:
        body = _lore_tone_section(domain_manager.get_lore(channel_id) or "")
    except Exception as _e:
        logger.debug(f"[LoreTone] skip: {_e}")
        return False
    if body:
        domain_manager.set_custom_tone(channel_id, body)
        logger.info(f"[LoreTone] 로어 분위기 절 → custom_tone ({len(body)}자)")
        return True
    return False


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

    _MAX_CHUNK = 8000   # [2026-10-27] 4000 → 8000(레티어스 "로어 청킹 한도 8000자까지"). 도구 check_lore_chunks · lore_chunk_gui · lore_check · 작성킷 01 같이
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
            # [2026-10-12 C30] 분위기 절은 Slot 33이 매 턴 싣는다 — 청크 후보에서 뺀다.
            if content and len(content) >= min_len and not _is_tone_label(cur_label):
                sections.append({"label": cur_label or _label(content), "content": content})
            buf = [line]
            cur_label = _label(stripped)
        else:
            if not cur_label and stripped:
                cur_label = _label(stripped)
            buf.append(line)

    # 마지막 버퍼
    content = '\n'.join(buf).strip()
    if content and len(content) >= min_len and not _is_tone_label(cur_label):   # [C30]
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

@registry.register("lore", category="World", aliases=["로어", "lore"],
                   description="로어 넣기 · 보기 — 파일 = 갈아 끼우기, 글 = 덧붙이기",
                   usage=("`!로어` — 지금 로어 · NPC 목록",
                          "`!로어` + 파일 — 갈아 끼우기(새 파일에 없는 옛 로어 장소는 지움 · 이름만 바뀐 곳은 별칭)",
                          "`!로어 {글}` — 덧붙이기(쌓인 로어 전체를 다시 분석)",
                          "`!로어 초기화 확인` — ⚠️ 로어만이 아니라 채널 데이터 전부"))
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
    # [2026-10-24 C76 §3] 한 신호 — **파일 = 갈아 끼우기 · 글 = 덧붙이기.** 원문 저장은 옛 그대로(set_lore / append_lore).
    #   분석 입력: 파일이면 새 원문, 글이면 **쌓인 원문 전체**(옛: 이번 조각만 → 앞 조각의 테마 · 시드 · 규칙 · 세력 ·
    #   사건 · 장소 목록이 Theoria 에서 사라졌다). 결과는 늘 "지금 로어 전부의 분석" 하나 — 칸마다 합치는 규칙이 없다.
    #   쓰기 · 지우기는 `_apply_lore_analysis` 한 곳, **분석이 성공한 뒤에만**.
    domain_manager.save_lore_original(channel_id, full_content)
    msg = await ctx.message.channel.send("📜 **로어 저장됨**. AI 분석 중...")
    _swap = bool(file_text)
    _prev_lore = domain_manager.get_lore(channel_id) or ""
    if _swap or not _prev_lore.strip() or _prev_lore.strip() == config.DEFAULT_LORE.strip():
        analysis_text = full_content
    else:
        analysis_text = f"{_prev_lore}\n\n{full_content}"

    _stored = [False]

    def _store_text():
        # [C76 검토 #2] 한 번만 — 완료 메시지(2000자 제한)가 실패해 except 로 떨어지면 덧붙이기가 두 번 붙었다.
        if _stored[0]:
            return
        _stored[0] = True
        if file_text:
            domain_manager.set_lore(channel_id, full_content)
        else:
            domain_manager.append_lore(channel_id, full_content)

    if ctx.genai_client:
        try:
            # [LoreAnalyzer V1] Unified Analysis — Flash 사용 (메타데이터 추출, Pro 안전 필터 회피)
            unified_res = await cognition.analyze_lore_unified(ctx.genai_client, config.role_model("heavy"), analysis_text)

            if not unified_res or not any(unified_res.get(k) for k in ("npcs", "genres", "lore_summary")):
                logger.warning("[LoreAnalyzer] 분석 결과 비어있음 — 로어 텍스트만 저장 (옛 분석 유지)")
                _store_text()
                _apply_lore_tone(channel_id)   # [2026-10-12 C30] 분석이 비어도 분위기 절은 들어간다
                lore_chunks = _split_lore_chunks(domain_manager.get_lore(channel_id) or full_content)  # [2026-09-24 감사] 누적 전문
                if lore_chunks:
                    domain_manager.set_lore_chunks(channel_id, lore_chunks)
                await msg.edit(content="⚠️ **로어 분석 실패** — 텍스트는 저장되었으나 NPC/장르/이변 추출에 실패했습니다. "
                                       "장소 · 이름 목록 · 시드는 **옛 분석 그대로**입니다. 로어를 다시 업로드하거나 분량을 나누어 시도해 주세요.")
                return

            rep = _apply_lore_analysis(channel_id, unified_res, analysis_text, swap=_swap)

            _store_text()
            # [2026-10-12 C30] 분석의 atmosphere_guide로 custom_tone을 채웠다 — 로어에 분위기 절이 있으면 그 원문이 덮는다.
            _apply_lore_tone(channel_id)

            # 6. Chunk splitting (V5 — 선택적 주입용)
            # [2026-09-24 감사] 청크는 **누적된 로어 전문**으로 — 텍스트 추가(append) 경로에서 이번 조각만 잘라
            #   set_lore_chunks(전량 교체)하면 앞서 올린 부분이 벡터 랭킹·relevant_chunks 에서 빠졌다.
            lore_chunks = _split_lore_chunks(domain_manager.get_lore(channel_id) or full_content)
            if lore_chunks:
                domain_manager.set_lore_chunks(channel_id, lore_chunks)

            try:
                await msg.edit(content=_lore_done_message(unified_res, rep, swap=_swap))
            except Exception as _e_msg:
                logger.warning(f"[LoreAnalyzer] 완료 메시지 실패(분석은 적용됨): {_e_msg}")

        except Exception as e:
            import traceback
            logger.error(f"Unified Lore Analysis Failed: {e}\n{traceback.format_exc()}")
            await msg.edit(content=f"⚠️ 분석 오류: {e}")
            _store_text()
            _apply_lore_tone(channel_id)   # [C30]
    else:
        _store_text()
        _apply_lore_tone(channel_id)   # [C30]
        # [2026-10-24 C76 §5] 옛 요약 파일 쓰기(set_event_lore_summary) 지움 — 읽는 곳 0.
        await msg.edit(content="📜 저장 완료 (AI 미사용 - 단순 요약)")


def _lore_loc_names(lsd: Any) -> list:
    """`lore_summary_data.locations` → 장소 이름 목록(dict 의 name 또는 글)."""
    locs = (lsd or {}).get("locations") if isinstance(lsd, dict) else None
    out = []
    for l in (locs or []) if isinstance(locs, list) else []:
        n = (l.get("name") if isinstance(l, dict) else l)
        n = str(n or "").strip()
        if n:
            out.append(n)
    return out


def _apply_lore_analysis(channel_id: str, unified_res: Dict[str, Any], analysis_text: str, *, swap: bool) -> Dict[str, Any]:
    """[2026-10-24 C76 §3] 로어 분석이 **성공한 뒤** 쓰기 · 지우기 한 곳(분석이 비거나 실패하면 부르지 않는다 — 옛 것 유지).

    swap=True(파일 = 갈아 끼우기): 이름 목록 · 거처 · 시드(독자 시드 포함) · 요약 칸 새로, 옛 로어 장소 중 새 목록에
      없는 것을 지움(`world_tree.strip_lore`) · 같은 이름 장소는 설명 · 위험을 새 로어로 · 옛 lore 장소 · 세력 페이지
      `status='deleted'` · 자리 잃은 등록 NPC 다시 앉힘 · PC 자리가 지워졌으면 알림.
    swap=False(글 = 덧붙이기 — analysis_text 는 쌓인 원문 전체): 요약 칸 새로 + 독자 시드 · 뒤표지 이어받음, 장소는 추가만.
    Returns 메시지용 {"removed_places", "pc_place_gone", "homes", "seated", "deleted_pages"}."""
    rep: Dict[str, Any] = {"removed_places": [], "recreated": [], "pc_place_gone": "", "homes": 0, "seated": [],
                           "deleted_pages": 0, "renamed": []}
    prev_lsd = domain_manager.get_lore_summary_data(channel_id) or {}
    if not isinstance(prev_lsd, dict):
        prev_lsd = {}
    extracted_npcs = [n for n in (unified_res.get("npcs") or []) if isinstance(n, dict)]
    genre_res = unified_res.get("genres", {}) or {}
    lore_summary_data = unified_res.get("lore_summary", {}) or {}
    # 사본에 쓴다 — 분석 결과 dict 를 그 자리에서 고치면 완료 메시지가 이어받은 독자 시드까지 센다(검토 덤).
    lore_summary_data = dict(lore_summary_data) if isinstance(lore_summary_data, dict) else {}

    # 이름 · 거처 — [2026-09-29 배치2 2a-9 C12] 이름은 voice_seed 게이트 1c 가 읽는다(등록 아님).
    #   [C76] 합치지 않는다 — 분석 입력이 늘 지금 로어 전부라 그 목록이 정본이다(갈아 끼우면 옛 이름은 사라진다).
    #   [검토 #10] 단 출력에 npcs 칸 자체가 없으면(정상 종료인데 칸만 빠짐) 옛 목록을 둔다 — 비우면 게이트 1c 가 풀린다.
    if "npcs" in unified_res:
        _names = [str(n.get("name")).strip() for n in extracted_npcs if str(n.get("name") or "").strip()]
        lore_summary_data["npc_names"] = list(dict.fromkeys(_names))
        _homes: Dict[str, str] = {}
        for n in extracted_npcs:
            _nm = str(n.get("name") or "").strip()
            _hm = str(n.get("home") or n.get("location") or "").strip()
            if _nm and _hm:
                _homes[_nm] = _hm
        lore_summary_data["npc_homes"] = _homes
    else:
        logger.warning("[LoreAnalyzer] 출력에 npcs 칸 없음 — 옛 이름 목록 · 거처 유지")
        lore_summary_data["npc_names"] = list(prev_lsd.get("npc_names") or [])
        lore_summary_data["npc_homes"] = dict(prev_lsd.get("npc_homes") or {})
    rep["homes"] = len(lore_summary_data["npc_homes"])

    # 독자 시드 · 뒤표지 — 덧붙이기면 이어받는다(근거가 같은 세계). 갈아 끼우면 버린다(옛 세계의 것).
    if not swap:
        _cur = [s for s in (lore_summary_data.get("anomaly_seeds") or []) if s]
        _have = {str(s.get("name") if isinstance(s, dict) else s) for s in _cur}
        for s in (prev_lsd.get("anomaly_seeds") or []):
            if isinstance(s, dict) and s.get("source") == "reader" and str(s.get("name")) not in _have:
                _cur.append(s)
        lore_summary_data["anomaly_seeds"] = _cur
        for _k in ("reader_blurb", "reader_blurb_hash"):
            if prev_lsd.get(_k) and _k not in lore_summary_data:
                lore_summary_data[_k] = prev_lsd[_k]

    # [2026-10-27 C84] 활성 규칙(모습 · 등장 · 불러오기) — 로어발 규칙만 새로(시트발은 남김). NPC 등록 **앞**:
    #   잠글 이름이 이미 따로 등록돼 있으면 판 전엔 잠그고(보관 시트로) 판 중엔 알린다. 로어 NPC 등록은 잠긴 이름을 뺀다.
    #   출력에 칸 자체가 없으면(정상 종료인데 칸만 빠짐) 옛 로어 규칙을 둔다 — npcs 칸과 같은 규율([C76 검토 #10]).
    _acts = lore_summary_data.pop("activations", None)
    rep["forms"] = {}
    try:
        import npc_forms as _nf_lore
        if isinstance(_acts, list):
            rep["forms"] = _nf_lore.apply_lore(channel_id, _acts, analysis_text)
        elif _nf_lore.has_table(channel_id):
            logger.warning("[LoreAnalyzer] 출력에 activations 칸 없음 — 옛 로어 활성 규칙 유지")
        extracted_npcs = [n for n in extracted_npcs
                          if not _nf_lore.is_reserved(channel_id, str(n.get("name") or ""))]
    except Exception as _e_act:
        logger.warning(f"[Form] 로어 활성 규칙 skip: {_e_act}")

    # 1. NPC — 로어 NPC 자동 등록이 켜졌을 때만 원문 절을 자른다(꺼져 있으면 그 결과를 아무도 안 받았다).
    if extracted_npcs and getattr(config, "LORE_NPC_AUTO_REGISTER", False):
        npc_names = [n.get("name", "") for n in extracted_npcs if n.get("name")]
        full_sections = npc_manager.extract_npc_sections_from_lore(analysis_text, npc_names)
        for npc in extracted_npcs:
            npc_name = npc.get("name", "")
            if npc_name and npc_name in full_sections:
                npc["description"] = full_sections[npc_name]
        npc_manager.add_lore_npcs(channel_id, extracted_npcs)

    # [2026-10-12 C33] 2. PC 정보 — 삭제. 로어는 PC를 뽑지 않는다(PC 시트 입구 = `!설명` 하나).

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

    # 4. World Tree — [C76 §3-1] 갈아 끼우면 옛 로어 장소부터 걷고(새 목록에 없는 것) 새 것을 들인다.
    #   (먼저 걷어야 새 장소가 곧 지워질 옛 부모 밑에 붙지 않고, 노드 상한(MAX_NODES)도 안 먹는다.)
    locations = lore_summary_data.get('locations', [])
    new_loc_names = _lore_loc_names(lore_summary_data)
    try:
        import world_tree
        if swap:
            _st = world_tree.strip_lore(channel_id, new_loc_names, _lore_loc_names(prev_lsd))
            rep["removed_places"] = list(_st.get("removed") or [])
        if locations and isinstance(locations, list):
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
                _wt_count = world_tree.import_locations_from_lore(channel_id, _wt_locs, refresh=swap)
                if _wt_count:
                    logger.info(f"[WorldTree] Imported {_wt_count} locations from lore")
        # [2026-10-25 C77 E-3 · Q33] 이름만 바뀐 옛 장소 → 새 노드 별칭(PC 자리 · 우물 중심 · 시계 born_node 가 그대로 풀린다).
        if swap and rep["removed_places"]:
            rep["renamed"] = world_tree.adopt_removed_names(channel_id, rep["removed_places"], new_loc_names,
                                                            _lore_loc_names(prev_lsd))
            _renamed = {o for o, _n in rep["renamed"]}
            rep["removed_places"] = [n for n in rep["removed_places"] if n not in _renamed]
    except Exception as e:
        logger.warning(f"[WorldTree] Lore import failed: {e}")

    # 5. [C76 §5] world_constraints 은퇴 — 분석이 안 뽑고, 여기서 안 쓴다(늘 실을 세계 규칙은 `!룰`).

    # JSON format storage for V4 Deep Analysis — 트리가 스스로 get/save_domain 을 돌았으니 그 뒤에 새로 읽는다.
    d_data = domain_manager.get_domain(channel_id)
    d_data["lore_summary_data"] = lore_summary_data
    domain_manager.save_domain(channel_id, d_data)

    # 4.6 [2026-09-14 W5] 로어 세력 → faction 페이지(조직도의 뿌리). 새 콜 0.
    try:
        if getattr(config, "WIKI_PLACES", False):
            import wiki_store as _ws_w5
            for _f in (lore_summary_data.get("factions") or []):
                _ws_w5.sync_faction_page(channel_id, _f)
            # [C76 §3] 갈아 끼우면 옛 lore 장소 · 세력 페이지는 내린다(위키 삭제 0 원칙 — status 만).
            if swap:
                # [검토 #1] 이름 비교는 위키 page id 로(NFKC · 공백 접기 · 소문자) — 날 글자로 비교하면 대소문자만 바뀐
                #   같은 세력을 방금 갱신해 놓고 곧바로 내렸다.
                _new_f = {_ws_w5.page_id_for("faction", str(f.get("name") if isinstance(f, dict) else f).strip())
                          for f in (lore_summary_data.get("factions") or [])}
                for _f in (prev_lsd.get("factions") or []):
                    _fn = str(_f.get("name") if isinstance(_f, dict) else _f).strip()
                    if _fn and _ws_w5.page_id_for("faction", _fn) not in _new_f \
                            and _ws_w5.mark_page_deleted(channel_id, "faction", _fn):
                        rep["deleted_pages"] += 1
                # [2026-10-25 C77 E-1] 지운 장소 페이지 내리기는 아래 "다시 선 곳" 가르기 **뒤**로 옮겼다 — 여기서 내리면
                #   시트 거처로 곧바로 다시 선 장소의 페이지까지 deleted 가 됐다.
    except Exception as _e_w5:
        logger.debug(f"[Wiki] faction pages skipped: {_e_w5}")

    # [C76 §4-3 ⓑ] 등록 NPC 다시 앉히기 — 자리 잃은 사람 + 로어를 NPC 등록보다 뒤에 넣은 판. 미배치일 때만(R4 규칙 그대로).
    try:
        import world_tree as _wt_seat
        for _nm, _nd in (domain_manager.get_npcs(channel_id) or {}).items():
            if not isinstance(_nd, dict) or not npc_manager.is_npc_active(_nd):
                continue
            if _wt_seat.get_npc_location(channel_id, _nm):
                continue
            if npc_manager.seat_sheet_residence(channel_id, _nm, lore_home=True) == "placed":
                rep["seated"].append(_nm)
    except Exception as _e_seat:
        logger.debug(f"[Lore] reseat skipped: {_e_seat}")
    # [검토 #3] 지운 옛 장소가 시트 거처로 다시 섰으면(R4 — 그 NPC 시트가 그 장소를 거처로 적었다) 따로 알린다.
    #   시트가 이긴다는 규칙 그대로 둔다. 다음 갈아 끼우기도 그 노드(`sheet_residence`)는 안 지운다.
    if rep["removed_places"]:
        try:
            import world_tree as _wt_rc
            rep["recreated"] = [n for n in rep["removed_places"] if _wt_rc.resolve_node_id(channel_id, n, allow_token=False)]
            rep["removed_places"] = [n for n in rep["removed_places"] if n not in rep["recreated"]]
        except Exception:
            pass
    # [2026-10-25 C77 E-1 · Q28] 정말 지운 장소(다시 선 곳 뺌)만 위키에서 내린다 — 개별 페이지는 status, room 은 모음 줄에서 뺀다.
    #   이름만 바뀐 곳(E-3)의 옛 이름 페이지 · 모음 칸도 같이 내린다 — 그 장소는 새 이름 페이지로 산다.
    _unpage = list(rep["removed_places"]) + [o for o, _n in (rep.get("renamed") or [])]
    if swap and _unpage and getattr(config, "WIKI_PLACES", False):
        try:
            import wiki_store as _ws_rm
            _new_l = {_ws_rm.page_id_for("location", n) for n in new_loc_names}
            _gone = [n for n in _unpage if _ws_rm.page_id_for("location", n) not in _new_l]
            for _pn in _gone:
                if _ws_rm.mark_page_deleted(channel_id, "location", _pn):
                    rep["deleted_pages"] += 1
            _ws_rm.remove_aggregate_names(channel_id, "location", _gone)
        except Exception as _e_rm:
            logger.debug(f"[Wiki] removed place pages skipped: {_e_rm}")

    # PC 자리 — 갈아 끼우며 지워진 장소였으면 알린다(글은 그대로 둔다 — 다음 장면이 새로 잡는다).
    if swap and rep["removed_places"]:
        try:
            import world_tree as _wt_pc
            _cur = str(domain_manager.get_current_location(channel_id) or "").strip()
            if _cur and _cur not in ("Unknown",) and not _wt_pc.resolve_node_id(channel_id, _cur):
                rep["pc_place_gone"] = _cur
        except Exception:
            pass
    # [2026-10-25 C75 G3] 시드 = 첫 질량 — 새 장소가 나무에 선 뒤 닻을 푼 시드마다 우물(갈아 끼우기면 옛 시드 우물은 끝).
    try:
        import well_store as _ws_seed
        _ws_seed.reconcile_seeds(channel_id, lore_summary_data)   # [C77 A-9] 돌려받은 수를 담던 `rep["seed_wells"]` 는 읽는 곳 0
    except Exception as _e_ws:
        logger.debug(f"[Lore] seed wells skipped: {_e_ws}")
    domain_manager.block_redo(channel_id, "!로어")   # [C77 C-4] 분석 결과(장소 · NPC · 장르 · 시드 우물 행)는 턴과 섞인 칸
    return rep


def _lore_done_message(unified_res: Dict[str, Any], rep: Dict[str, Any], *, swap: bool) -> str:
    """[C76] `!로어` 완료 메시지 — 시드는 이름으로(옛: dict 를 그대로 찍었다) · 거처 · 갈아 끼우기 결과."""
    extracted_npcs = [n for n in (unified_res.get("npcs") or []) if isinstance(n, dict)]
    genre_res = unified_res.get("genres", {}) or {}
    lsd = unified_res.get("lore_summary", {}) or {}
    genre_summary = f"{genre_res.get('world_setting', [])} / {genre_res.get('style_tech', [])} / {genre_res.get('narrative_tone', [])}"
    npc_names = [f"`{n['name']}`" for n in extracted_npcs if n.get('name')]
    npc_list_str = ", ".join(npc_names)
    seeds = lsd.get('anomaly_seeds', []) if isinstance(lsd, dict) else []
    seed_names = [str(s.get('name', '') if isinstance(s, dict) else s).strip() for s in (seeds or [])]
    seed_names = [s for s in seed_names if s]
    anomaly_str = ", ".join(f"`{s}`" for s in seed_names) if seed_names else "없음"
    rules_count = len(lsd.get('rules', []) or []) if isinstance(lsd, dict) else 0
    factions_count = len(lsd.get('factions', []) or []) if isinstance(lsd, dict) else 0
    lore_extra = ""
    if rules_count or factions_count:
        lore_extra = f"\n\n📜 **세계 규칙** {rules_count}개 | **세력** {factions_count}개 추출"
    homes = int(rep.get("homes") or 0)
    msg = (f"✅ **로어 분석 완료**{' (갈아 끼움)' if swap else ''}\n\n👥 **NPC: {len(extracted_npcs)}명 식별**"
           f"{f' (거처 {homes}명)' if homes else ''}\n{npc_list_str}\n\n🌍 **장르/톤**\n{genre_summary}"
           f"\n\n🌪️ **이변 징후** ({len(seed_names)}개)\n{anomaly_str}{lore_extra}")
    _rm = rep.get("removed_places") or []
    if _rm:
        msg += f"\n\n🔄 옛 로어 장소 {len(_rm)}곳 지움: " + ", ".join(f"`{x}`" for x in _rm[:12]) + (" …" if len(_rm) > 12 else "")
    _rn = rep.get("renamed") or []
    if _rn:
        msg += f"\n🔁 이름만 바뀐 곳 {len(_rn)}: " + ", ".join(f"`{o}` → `{n}`" for o, n in _rn[:8]) + (" …" if len(_rn) > 8 else "")
    _rc = rep.get("recreated") or []
    if _rc:
        msg += f"\n↩️ 시트 거처라 다시 선 곳 {len(_rc)}: " + ", ".join(f"`{x}`" for x in _rc[:8]) + (" …" if len(_rc) > 8 else "")
    if rep.get("pc_place_gone"):
        msg += f"\n📍 PC 자리 `{rep['pc_place_gone']}` — 옛 로어 장소라 다음 장면에서 새로 잡힘"
    _st = rep.get("seated") or []
    if _st:
        msg += f"\n🪑 거처에 앉힘 {len(_st)}명: " + ", ".join(f"`{x}`" for x in _st[:12]) + (" …" if len(_st) > 12 else "")
    _fm = rep.get("forms") or {}
    if _fm.get("rules") or _fm.get("locked") or _fm.get("conflict"):   # [2026-10-27 C84]
        msg += f"\n🎭 활성 규칙 {int(_fm.get('rules') or 0)}개 (`!npc 모습`)"
        if _fm.get("locked"):
            msg += " · 잠금: " + ", ".join(f"`{x}`" for x in _fm["locked"][:8])
        if _fm.get("conflict"):
            msg += ("\n⚠️ 판이 진행 중이라 안 잠근 이름: " + ", ".join(f"`{x}`" for x in _fm["conflict"][:8])
                    + " — `!npc 삭제` 뒤 그 시트를 다시 올리면 잠겨요")
    # [검토 #2] 디스코드 2000자 — 넘치면 NPC 이름 줄을 줄인다(전원 이름은 `!로어` 보기에서).
    if len(msg) > 1900 and npc_list_str:
        _keep = []
        for _n in npc_names:
            if len(", ".join(_keep + [_n])) > 400:
                break
            _keep.append(_n)
        msg = msg.replace(npc_list_str, ", ".join(_keep) + f" 외 {len(npc_names) - len(_keep)}명", 1)
    return msg[:1990]


_INFO_USAGE = ("📝 사용법: `!설명` + PC 파일 · `!설명 [내용]` — 먼저 `!가면 [이름]`.\n"
               "보기는 Activity — 인물 앱 내 페이지 · 상태창 · 보관함 · 진행 중.")


@registry.register("info", category="Player", aliases=["desc", "설명"], description="PC 설정 넣기(글 · 파일)",
                   usage=("`!설명` + PC 파일 · `!설명 {글}` — PC 설정 넣기(다시 하면 새 글로 바뀜). 먼저 `!가면 {이름}`",
                          "보기는 Activity — 인물 앱 내 페이지(시트) · 상태창(활력 · 평형 · 상태 · 특성) · 보관함 · 진행 중"))
async def cmd_info(ctx: CommandContext) -> None:
    """!설명 [내용/파일] — PC 설정 넣기.

    [2026-10-25 C78 B2] 보기(`!내정보` · `!정보`)는 걷었다 — 시트 · 관계 · 활력/평형 · 목표 · 노트북은 Activity 가 다 보이고,
      남던 상태 · 특성 두 줄은 상태창 PC 칸으로 옮겼다(`status_panel._pc_extra_lines`). 이름(`info`)은 남긴다 — 부르는 스모크 · 막기 표.
    [C78 검토] 쓰는 건 `!설명`(desc) 으로 부를 때뿐 — `!info [글]` 이 시트를 덮지 않게(C77 까지 `!info` 는 보기였다).
    """
    if str(ctx.trigger or "").lower() not in ("desc", "설명"):
        await ctx.send(_INFO_USAGE)
        return
    uid = ctx.user_id
    p_data = domain_manager.get_participant_data(ctx.channel_id, uid)
    if not p_data:
        await ctx.send("❌ 등록 필요 (`!가면 [이름]`)")
        return

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
        if ctx.message.attachments:
            await ctx.send("⚠️ 파일 내용을 읽을 수 없습니다.")
            return
        await ctx.send(_INFO_USAGE)
        return
    # 2. Update logic
    # [2026-10-12 C33] 페이지(`!가면`)가 없으면 콜 전에 거절 — 대기 상자는 없다.
    if not domain_manager.get_pc_page_id(ctx.channel_id, uid):
        await ctx.send("❌ 먼저 `!가면 [이름]`으로 캐릭터를 세운 뒤 `!설명`을 넣어 주세요.")
        return
    status_msg = await ctx.send("📝 **캐릭터 설정 분석 중...**")
    
    # [2026-09-16 시트 2차 §8] 원문 = 페이지 lore 절(정본). heavy 콜은 조각(passives/inventory)·
    #   이름·species 추출용으로만. AI 실패 폴백 = 원문 그대로(파서가 절을 못 잡으면 Notes).
    analysis = {}
    if ctx.genai_client:
        analysis = await cognition.analyze_character_sheet(ctx.genai_client, config.role_model("heavy"), full_arg) or {}
    box = npc_manager.pc_box_from_info(analysis, full_arg)
    if not analysis:
        box["sheet_fallback"] = True   # AI 실패 폴백: 원문 그대로 Notes 절
    if domain_manager.apply_pc_info_to_user(ctx.channel_id, uid, box):
        _tail = "" if analysis else " (AI 분석 실패 — 원문만 저장)"
        await status_msg.edit(content=f"✅ **캐릭터 시트 등록 완료**{_tail}\n특성: {len(box.get('passives', []))}개 추출\n원문은 캐릭터 페이지에 절 단위로 저장되었습니다.")
    else:
        await status_msg.edit(content="⚠️ **시트 저장 실패** — 다시 시도해 주세요.")
    return


@registry.register("mask", category="Player", aliases=["가면", "persona"], description="PC 이름 정하기 · 바꾸기",
                   usage=("`!가면` — 지금 가면", "`!가면 {이름}` — PC 이름 정하기 · 바꾸기(캐릭터 페이지가 선다)"))
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

    # [2026-10-12 C33] 대기 상자 흡수 삭제 — PC 시트 입구는 `!설명` 하나. 옛 로어 상자가 남아 있으면 치운다.
    domain_manager.clear_default_pc_info(ctx.channel_id)

    await ctx.send(f"🎭 **{target}**(으)로 변신했습니다.")


# [2026-10-12 C33] maybe_enrich_pc_sheet 삭제 — 로어 PC 상자 보강 전용이었다.


# [2026-10-25 C78 B1] `!일지`(PC 여정 요약 이력 = ai_memory.journal_log) 걷음 — 대신 세울 장은 없다: 쓰는 길
#   (`game_character.add_to_journal`)을 부르는 곳이 0 이라(2026-09 시트 2차 무렵부터) 늘 비어 있었다(대기열 Q40).
#   채널의 날 일지는 보관함 ③ 일지 장.


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


def _register_or_form(channel_id: str, name: str, desc: str,
                      id_fields: dict = None, existing_map: dict = None) -> str:
    """[2026-10-27 C84] 등록 네 길(엔벨로프 · 단일 시트 · h2 · Name:) 앞 가로채기 — 시트 머리 `모습:` · `등장: 예` ·
    `불러오기: 예` 와 잠긴 모습 이름을 `npc_forms` 가 먼저 본다. 가로채지 않으면 종전 `_register_npc` 그대로.
    반환 = 등록 메시지에 실릴 이름(가로챘으면 그 사정 한 줄)."""
    try:
        import npc_forms
        _res = npc_forms.take_sheet(
            channel_id, name, desc,
            lambda _n, _d: _register_npc(channel_id, _n, _d, _extract_id_fields(str(_d or "").splitlines())))
    except Exception as _e_fm:
        logger.warning(f"[Form] 시트 가로채기 실패 — 평소 등록: {name}: {_e_fm}")
        _res = None
    if _res is not None:
        return _res
    return _register_npc(channel_id, name, desc, id_fields, existing_map)


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
_NPC_SHEET_USAGE = ("⚠️ 사용법: `!npc 시트 [이름] [절이름] set|append|remove [본문…]` (편집) — "
                    "보기는 Activity 인물 앱")


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



async def _npc_sheet_subcommand(ctx, rest: str) -> None:
    """`!npc 시트 …` — 인물 페이지 lore 절 한 칸 편집(set·append·remove).
    [2026-10-25 C78 A4] 보기는 Activity 인물 앱(같은 `get_lore_sections` 절, 시트 순서) — 여기는 가리키기만."""
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

    if not action:                                   # ── 보기 → 인물 앱
        if not wiki_store.get_lore_sections(channel_id, pid):   # [C78 검토] 페이지 · 절이 없으면 인물 앱에도 없다
            await ctx.send(f"📄 **{key}** — 시트 절이 아직 없습니다. "
                           f"`!npc 시트 {key} Core Traits set [본문]` 으로 쓸 수 있습니다.")
            return
        await ctx.send(f"📄 **{key}** 시트는 Activity **인물** 앱에서 봐요. "
                       f"고치기: `!npc 시트 {key} [절이름] set|append|remove [본문…]`")
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


@registry.register("npc", category="World", aliases=["엔피씨", "addnpc", "npc정보", "npc추가"],
                   description="NPC 등록 · 시트 · 별칭 · 병합",
                   usage=("`!npc` — 등록된 NPC 목록(`!npc all` 임시까지) · `!npc {이름}` — 한 사람(말투 · 일정 · 역할)",
                          "`!npc추가` + 시트 파일(여럿 가능) · `!npc추가 이름: 설명`",
                          "`!npc 시트 {이름} {절} set|append|remove {본문}` — 절 하나 고치기(보기는 Activity 인물 앱)",
                          "`!npc 별칭 {이름} {별칭}` · `!npc 병합 {중복} {본체}`",
                          "`!npc 상태 {이름} active|down|dead`",
                          "`!npc 모습 [{이름}]` — 모습 · 등장 · 불러오기 규칙 보기 · `!npc 모습 {이름} {모습}` — 손으로 바꾸기",
                          "`!npc 일정 [{이름}]` · `!npc 보이스카드 [{이름}]` — 시트에서 일정 · 말투 뽑기(콜)",
                          "`!npc 삭제 {이름}` · `!npc 초기화` — 세션 NPC 만(로어 · 직접 등록은 남음)"))
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
                await ctx.send("⚠️ 사용법: `!npc 삭제 [이름]`")
                return
            target_name = parts[1].strip()
            # [2026-10-27 C84] 잠긴 모습 · 등장 전 사람은 명부에 없다 — 보관분을 먼저 본다.
            try:
                import npc_forms as _nf_del
                _fm_msg = _nf_del.forget(channel_id, target_name)
            except Exception as _e_fd:
                _fm_msg = None
                logger.debug(f"[Form] forget skip: {_e_fd}")
            if _fm_msg:
                await ctx.send(_fm_msg)
                return
            success, matched_key = domain_manager.delete_npc(channel_id, target_name)
            if success:
                display = matched_key or target_name
                try:   # 그 사람의 모습 표도 같이(개명 길의 delete_npc 는 domain 쪽이라 여기만 지운다)
                    import npc_forms as _nf_dp
                    if _nf_dp.forget_person(channel_id, display):
                        display += " (모습 · 규칙 표도)"
                except Exception as _e_fp:
                    logger.debug(f"[Form] forget_person skip: {_e_fp}")
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

        # Subcommand: 모습 / form — [2026-10-27 C84] 모습 · 등장 · 불러오기 규칙 보기 · 손으로 바꾸기(D5)
        if parts[0].lower() in ('모습', 'form', 'forms'):
            import npc_forms
            _rest = parts[1].strip() if len(parts) > 1 else ""
            _mres = npc_forms.manual(channel_id, _rest) if len(_rest.split()) >= 2 else None
            if _mres is not None:
                if not _mres.startswith("⚠"):
                    domain_manager.block_redo(channel_id, "!npc 모습")   # 이름 · 페이지 · 엣지를 옮긴다 = 턴과 섞인 칸
                await ctx.send(_mres)
                return
            await ctx.send(npc_forms.view(channel_id, _rest))
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
            try:
                import npc_forms as _nf_alc
                if _nf_alc.is_reserved(channel_id, alias_arg):
                    await ctx.send(f"⚠️ '{alias_arg}'는 잠긴 모습 · 등장 전 이름이라 별칭으로 못 붙여요(`!npc 모습`).")
                    return
            except Exception:
                pass
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
                _register_or_form(channel_id, _n, _b, _extract_id_fields(_b.splitlines()))
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
            _f_target = _register_or_form(channel_id, _f_name, _f_desc, _f_idf)
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
                target_name = _register_or_form(
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
                last_name = _register_or_form(
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


@registry.register("reset", category="Admin", aliases=["리셋", "초기화"], description="채널 데이터 전부 지우기(확인)",
                   usage=("`!리셋` — 채널 데이터 전부 · 채널 재생성(이모지로 확인)",))
async def cmd_reset(ctx: CommandContext) -> None:
    """!리셋 - 이모지 확인 후 전체 초기화 (채널 재생성)"""
    # 이모지 확인은 session_manager에서 처리
    await session_manager.manager.execute_reset(ctx.message, ctx.client)


# [2026-09-06 P7] 옛 문구 "화면 청소 (데이터 유지)"는 코드와 **반대**였다 —
#   execute_clear 는 soft reset(진행 상황 삭제) + 채팅 purge 다. 명령 목록만 읽고
#   `!클리어`를 누른 유저가 세션을 날리는 사고가 이 한 줄에서 났다.
@registry.register("clear", category="Admin", aliases=["클리어", "청소"],
                   description="진행만 지우기(로어 · 룰 · 참가자 · NPC 시트 · 출력 선언은 남음)",
                   usage=("`!클리어` — 진행 상황 삭제 · 채팅 비우기. 로어 · 룰 · 참가자 · NPC 시트 · 출력 선언은 남는다",))
async def cmd_clear(ctx: CommandContext) -> None:
    """!클리어"""
    await session_manager.manager.execute_clear(ctx.message)


@registry.register("ready", category="Admin", aliases=["준비"], description="준비 점검",
                   usage=("`!준비` — 로어 · 가면 · 룰 · 출력 선언 점검",))
async def cmd_ready(ctx: CommandContext) -> None:
    """!준비"""
    await session_manager.manager.check_preparation(ctx.message)


@registry.register("start", category="Admin", aliases=["시작"], description="세션 시작 [첫 상황]",
                   usage=("`!시작` — 오프닝을 봇이 · `!시작 {첫 상황}` — 그 상황으로",))
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


@registry.register("retry", category="System", aliases=["다시", "reroll"], description="마지막 산문 다시 쓰기 [고친 입력]",
                   usage=("`!다시` — 같은 입력으로 다시 · `!다시 {고친 입력}` — 입력을 바꿔 다시",
                          "안 되는 때: 마지막 턴이 `!턴` · 관찰 턴 / 그 뒤 턴 칸을 쓰는 명령(`!npc` 쓰기 · `!로어` · `!설명` · "
                          "`!가면` · `!시간` · `!출력룰` 변수 · OOC 편집) / 앞 턴 배경 정리가 덜 끝난 채 시작한 턴"))
async def cmd_retry(ctx: CommandContext) -> None:
    """!다시 [수정 입력] — 빈칸이면 같은 입력으로 리롤, 내용 있으면 입력 교체 후 재생성"""
    from orchestration import get_orchestration_runtime
    orchestration = get_orchestration_runtime(ctx.genai_client, ctx.model_id, config.role_model("flash"))

    if not orchestration:
        await ctx.send("⚠️ AI 서비스가 초기화되지 않았습니다.")
        return

    edited_input = ctx.raw_args.strip() or None
    await orchestration.retry_last(ctx.message, ctx.channel_id, edited_input=edited_input)


# [2026-10-25 C78 D2] `!장면`(수위 안내 한 줄 — 10-11 뒤 설정 0) 걷음. 친 사람은 없는 명령 안내를 받는다.


@registry.register("lock", category="System", aliases=["잠금"], description="세션 진행 / 일시정지",
                   usage=("`!잠금` — 토글 · `!잠금 on` — 진행(채팅을 턴으로) · `!잠금 off` — 일시정지(채팅 무시)",))
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

@registry.register("modules", category="System", aliases=["모듈", "mods"],
                   description="모듈 켜고 끄기 · 코드 앱 올리기(보기는 모듈 앱)",
                   usage=("`!모듈 {번호|이름} on/off` — 하나(예: `!모듈 속마음 off` · `!모듈 게시판 on` · `!모듈 2 off`)",
                          "`!모듈 on/off` — 토글 모듈 일괄(올린 것은 안 건드림)",
                          "`!모듈 올리기` + .lkapp — 코드 앱 올리기 · `!모듈 지우기 {번호|이름} 확인`",
                          "상태 · 설정 · 들어 있는 것은 Activity 모듈 앱"))
async def cmd_modules(ctx: CommandContext) -> None:
    """!모듈 - 모듈 상태 확인. 핵심 모듈은 항상 활성.

    [2026-10-22 C58 M0] 목록·별칭·토글은 모듈 틀 등록부(module_frame / builtin_modules)가 쥔다.
      옛 손글 표(core_mods·extra_mods·_mod_aliases)와 출력 줄이 같다 — 등록 순서가 목록 순서.
      맨 아래 '설정으로 도는 것'(상태패널·지속 감사·Reader-GM)은 보이기만 한다(끄고 켜기는 !출력룰·config).
    """
    import module_frame as _mf
    arg = ctx.raw_args.strip().lower()
    core_mods = _mf.on_shelf("core")
    toggle_mods = _mf.on_shelf("toggle")
    _parts = arg.split()

    # [2026-10-23 C73 M7a] 코드 앱 올리기 · 지우기 — `!모듈 올리기` + 첨부 .lkapp / `!모듈 지우기 <이름|#n> [확인]`.
    if _parts and _parts[0] in ("올리기", "upload", "install"):
        import app_host as _ah_up
        if not ctx.message.attachments:
            await ctx.send("⚠️ `.lkapp` 파일을 첨부해 주세요 — `!모듈 올리기` + 파일 하나.")
            return
        _att = ctx.message.attachments[0]
        if int(getattr(_att, "size", 0) or 0) > _ah_up.MAX_PACKAGE_BYTES:
            await ctx.send("⚠️ 묶음이 너무 커요(5MB까지).")
            return
        try:
            _data = await _att.read()
        except Exception as _e_rd:
            logger.warning(f"[App] 첨부 읽기 실패: {_e_rd}")
            await ctx.send("⚠️ 첨부를 못 읽었어요.")
            return
        _ok_up, _lines_up = await _ah_up.install(ctx.channel_id, _data)   # 검사는 작업 프로세스(이벤트 루프 안 붙잡음)
        await ctx.send("\n".join(_lines_up))
        return
    if _parts and _parts[0] in ("지우기", "remove", "uninstall"):
        import app_host as _ah_rm
        _raw_rm = ctx.raw_args.strip().split()[1:]
        _confirm = bool(_raw_rm) and _raw_rm[-1] in ("확인", "confirm")
        if _confirm:
            _raw_rm = _raw_rm[:-1]
        _kind_rm, _target_rm, _cands_rm = _mf.resolve_module(ctx.channel_id, " ".join(_raw_rm))
        if _kind_rm != "app":
            if _cands_rm:
                await ctx.send("⚠️ 여럿에 맞아요 — " + " · ".join(_cands_rm))
            else:
                await ctx.send("⚠️ 그런 코드 앱이 없어요. `!모듈` 목록의 번호나 이름으로 쳐 주세요.")
            return
        _app_rm = _ah_rm.get_app(ctx.channel_id, _target_rm) or {}
        _label_rm = _ah_rm.label_of(_app_rm) if _app_rm else str(_target_rm)
        if not _confirm:
            await ctx.send(f"⚠️ **{_label_rm}** 을(를) 지우면 묶음과 그 앱 장부가 사라져요(번호는 남아요).\n"
                           f"진행하려면: `!모듈 지우기 {' '.join(_raw_rm)} 확인`")
            return
        _ok_rm, _line_rm = _ah_rm.remove(ctx.channel_id, _target_rm)
        await ctx.send(("🗑️ " if _ok_rm else "⚠️ ") + _line_rm)
        return

    # 개별 토글: !모듈 속마음 off / !모듈 게시판 on  (별칭 = 각 모듈의 aliases + id)
    # [2026-10-23 C72] + 올린 모듈(`!출력룰` 파일): `!모듈 2 off` · `!모듈 #2 off` · `!모듈 화원 off` · 앞글자(하나로만 맞을 때).
    #   고르기 = module_frame.resolve_module(코드 별칭이 먼저), 쓰기 = set_module_on(Activity mod.toggle 과 같은 함수).
    _ON_WORDS, _OFF_WORDS = ('on', '켜기', 'true'), ('off', '끄기', 'false')
    _raw_parts = ctx.raw_args.strip().split()
    _core_note = ""
    if len(_raw_parts) >= 2 and _raw_parts[-1].lower() in _ON_WORDS + _OFF_WORDS:
        _on = _raw_parts[-1].lower() in _ON_WORDS
        _kind, _target, _cands = _mf.resolve_module(ctx.channel_id, " ".join(_raw_parts[:-1]))
        if _kind == "code" and _target.shelf == "toggle" and _target.toggle is not None and _target.toggle.set is not None:
            _m = _target
            _m.toggle.set(ctx.channel_id, _on)
            if _on:
                await ctx.send(f"✅ **{_m.name}** 모듈 활성화")
                return
            if _m.off_notice:
                await ctx.send(f"❌ **{_m.name}** 모듈 비활성화\n{_m.off_notice}")
                return
            await ctx.send(f"❌ **{_m.name}** 모듈 비활성화")
            return
        if _kind in ("bundle", "app"):   # [C73] 코드 앱도 같은 길
            _ok, _line = _mf.set_module_on(ctx.channel_id, _kind, _target, _on)
            await ctx.send(("✅ " if _on else "❌ ") + _line if _ok else f"⚠️ {_line}")
            return
        if _cands:
            await ctx.send("⚠️ 여럿에 맞아요 — " + " · ".join(_cands) + "\n번호나 이름 전체로 다시 쳐 주세요.")
            return
        # [C78 A3] 옛 상태 화면이 "핵심 모듈 (항상 활성)"으로 말해 주던 몫 — 핵심 모듈을 끄려 하면 한 줄.
        _w_core = " ".join(_raw_parts[:-1]).strip().lower()
        _core_hit = _target if (_kind == "code" and getattr(_target, "shelf", "") == "core") else next(
            (m for m in core_mods if _w_core in {str(m.id).lower(), str(m.name).lower(), *[str(a).lower() for a in (m.aliases or ())]}),
            None)
        if _core_hit is not None:
            _core_note = f"ℹ️ **{_core_hit.name}** 은(는) 핵심 모듈이라 늘 켜져 있어요."
        else:
            # [C78 검토] 옛 상태 화면이 대신 받던 꼴 — 이제는 말없이 안내만 나와 "꺼졌나?" 로 읽혔다.
            _cfg_hit = _target if (_kind == "code" and getattr(_target, "shelf", "") == "config") else next(
                (m for m in _mf.on_shelf("config")
                 if _w_core in {str(m.id).lower(), str(m.name).lower(), *[str(a).lower() for a in (m.aliases or ())]}),
                None)
            _core_note = (f"ℹ️ **{_cfg_hit.name}** 은(는) 설정으로 도는 것이라 `!모듈` 로 켜고 끄지 않아요."
                          if _cfg_hit is not None else
                          f"⚠️ 그런 모듈이 없어요: `{' '.join(_raw_parts[:-1])}`")

    _core_names = "/".join(m.name for m in core_mods)

    # 부가 모듈 일괄 토글
    if arg in ['on', '켜기', 'true', 'all']:
        for m in toggle_mods:
            m.toggle.set(ctx.channel_id, True)
        _names = " ✅ · ".join(m.name for m in toggle_mods)
        await ctx.send(f"✅ **부가 모듈이 활성화되었습니다.**\n• {_names} ✅\n\n({_core_names}은 항상 활성)")
        return
    if arg in ['off', '끄기', 'false', 'none']:
        for m in toggle_mods:
            m.toggle.set(ctx.channel_id, False)
        _names = " ❌ · ".join(m.name for m in toggle_mods)
        await ctx.send(f"❌ **부가 모듈이 비활성화되었습니다.**\n• {_names} ❌\n\n({_core_names}은 항상 활성)")
        return

    # [2026-10-25 C78 A3] 상태 화면은 Activity 모듈 앱(같은 목록 + 설정 · 들어 있는 것 · 코드 앱) — 여기는 하위 명령 안내만.
    msg = ([_core_note] if _core_note else []) + ["🔌 **모듈** — 상태 · 설정 · 들어 있는 것은 Activity **모듈** 앱에서 봐요.",
           "• `!모듈 {번호|이름} on/off` — 하나 켜고 끄기 (예: `!모듈 속마음 off` · `!모듈 게시판 on` · `!모듈 2 off`)",
           "• `!모듈 on/off` — 토글 모듈 일괄 (올린 것은 안 건드림)",
           "• `!모듈 올리기` + .lkapp — 코드 앱 올리기 · `!모듈 지우기 {번호|이름} 확인`"]
    try:
        import app_host as _ah_ls
        if _ah_ls.list_apps(ctx.channel_id) and not _ah_ls.available():
            msg.append("⚠️ 서버에 lupa 가 없어 코드 앱이 안 돌아요(`pip install lupa`).")
    except Exception as _e_al:
        logger.debug(f"[Modules] apps skip: {_e_al}")
    await ctx.send("\n".join(msg))


# [2026-10-25 C78 A2 · D3] `!게시판` 걷음 — 모듈 앱 게시판 카드(사용 · 공지/SNS/메시지 스위치) + `!모듈 게시판 on/off`.
#   `!게시판 표시`(스레드 · 버튼 · 끄기)는 C77 A-4 뒤 끄기 말고 효과가 없었다(Q36) — 옛 `끄기` 저장값은 채널 꺼짐으로 읽는다(world_board).


@registry.register("bot", category="System", aliases=["봇"], description="봇 켜고 끄기",
                   usage=("`!봇` — 지금 상태 · `!봇 on/off` — 꺼지면 `!봇` 만 받는다",))
async def cmd_bot(ctx: CommandContext) -> None:
    """!봇 [on/off]"""
    arg = ctx.raw_args.strip().lower()
    
    if not arg:
        curr = domain_manager.get_bot_active(ctx.channel_id)
        status = "✅ ON" if curr else "❌ OFF"
        await ctx.send(f"🤖 봇 상태: **{status}**\n사용법: `!봇 on/off`")
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


def _build_chronicle_tail_input(tail: list) -> str:
    """[2026-10-26 C80] `!연대기` 꼬리 — 장에 아직 안 묶인 엔트리만(작중 시각 · 턴 머리 + 요약)."""
    import fermentation
    lines = []
    for e in tail or []:
        if not isinstance(e, dict) or not str(e.get("summary") or "").strip():
            continue
        stamp = fermentation._episode_stamp(e)
        lines.append(f"{stamp} {e['summary'].strip()}" if stamp else e["summary"].strip())
    return ("## 최근 에피소드 (아직 장으로 묶이지 않은 구간)\n" + "\n".join(lines)) if lines else ""


async def _show_chronicle_chapters(ctx: CommandContext) -> None:
    """[2026-10-26 C80 D9] `!연대기` v2 — 쌓인 장을 시간순으로 보여 주고(콜 0), 장에 안 묶인 꼬리 엔트리만 즉석 요약(콜 1).
    저장은 안 한다(장은 발효가 쓴다). 통째로 보여 주는 장은 최근 CHRONICLE_SHOW_MAX 개, 나머지는 내보내기."""
    import fermentation
    channel_id = ctx.channel_id
    d_data = domain_manager.get_domain(channel_id)
    _nar, _ddata = domain_manager.get_deep_memory(channel_id)
    chapters = [c for c in fermentation.get_chapters(_ddata) if not c.get("legacy")]
    legacy = [c for c in (d_data.get("chronicles") or []) if isinstance(c, dict) and c.get("content")]
    tail = fermentation.unchaptered_entries(domain_manager.get_fermented_history(channel_id), _ddata)

    if not chapters and not legacy and not tail:
        await ctx.send("⚠️ 아직 연대기로 묶인 장도, 정리할 에피소드도 없습니다.")
        return

    show_max = max(1, int(getattr(config, "CHRONICLE_SHOW_MAX", 3) or 3))
    blocks = []
    if not chapters and legacy:
        _lg = legacy[-1]
        blocks.append(f"**이전 연대기** (옛 방식, {len(legacy)}개 중 마지막)\n{str(_lg.get('content') or '').strip()}")
    hidden = max(0, len(chapters) - show_max)
    for ch in chapters[-show_max:]:
        blocks.append(f"**{fermentation.chapter_title(ch)}**\n{str(ch.get('text') or '').strip()}")
    if hidden or (chapters and legacy):
        _more = []
        if hidden:
            _more.append(f"앞 장 {hidden}개")
        if chapters and legacy:
            _more.append(f"이전 연대기 {len(legacy)}개")
        blocks.append(f"-# {' · '.join(_more)}는 `!연대기 내보내기` 파일에 있습니다.")
    if blocks:
        await send_long_message(ctx.message.channel, "📜 **[세션 연대기]**\n\n" + "\n\n".join(blocks))

    tail_input = _build_chronicle_tail_input(tail)
    if not tail_input:
        return
    try:
        import corrections as _corr_ct
        _cb_ct = _corr_ct.block_for(channel_id, tail_input)
        if _cb_ct:
            tail_input += "\n\n---\n\n" + _cb_ct
    except Exception:
        pass
    feedback = await ctx.message.channel.send("📜 **최근 구간을 정리하고 있습니다...**")
    import text_resources
    from google.genai import types
    try:
        response = await ctx.genai_client.aio.models.generate_content(
            model=config.role_model("main"),
            contents=[types.Content(role="user", parts=[types.Part(text=tail_input)])],
            config=types.GenerateContentConfig(
                system_instruction=text_resources.CHRONICLE_SYSTEM_PROMPT,
                temperature=0.5,
                max_output_tokens=8192,
                safety_settings=config.SAFETY_SETTINGS,
            )
        )
        text = (response.text or "").strip() if response else ""
        try:
            await feedback.delete()
        except Exception:
            pass
        if text:
            await send_long_message(ctx.message.channel, f"📜 **[최근 구간 — 아직 장이 아님]**\n\n{text}")
        else:
            await ctx.send("⚠️ 최근 구간 정리에 실패했습니다.")
    except Exception as e:
        logger.error(f"[Chronicle] tail summary failed: {e}", exc_info=True)
        try:
            await feedback.edit(content=f"⚠️ 최근 구간 정리 오류: {str(e)[:100]}")
        except Exception:
            pass


async def _generate_session_chronicle(ctx: CommandContext) -> None:
    """세션 연대기 생성. [2026-08-18] 역할=main — 발효·연대기 라인과 **같은 모델**로 통일
    (종전 "flash"였다: 같은 연대기가 자동 발효(main)와 !연대기(flash) 두 모델로 갈렸다)."""
    if getattr(config, "MEMORY_LAYERS_V2", False):
        await _show_chronicle_chapters(ctx)   # [C80] 장 보기 + 꼬리 즉석(저장 0)
        return
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
    # [2026-10-10 기록 정정] 연대기 재료에 걸린 정정(R9).
    if chronicle_input:
        try:
            import corrections as _corr_cc
            _cb_cc = _corr_cc.block_for(channel_id, chronicle_input)
            if _cb_cc:
                chronicle_input += "\n\n---\n\n" + _cb_cc
        except Exception:
            pass
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
                # [2026-10-11 C27b] 4096 → 8192. 추론 켠 분석 콜은 추론이 출력 예산에 같이 든다 — 상한 통일(레티어스 "나머지도 다 올리자").
                max_output_tokens=8192,
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


@registry.register("lores", category="Analysis", aliases=["연대기", "chronicle"], description="세션 요약 · 내보내기",
                   usage=("`!연대기` — 지난 장 + 최근 구간 요약(콜) · `!연대기 내보내기 [증분]` — 연대기 파일",))
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
    (값·기록 섹션·서술 섹션·형식·전이 이름 + 레코드 항목 이름 + 시스템 변수 활력·평형).
    하드코딩 키워드 목록은 한 글자도 안 바뀐다 — 채널을 안 주면 종전 분류 그대로다.

    ★이름 검사가 서사 키워드보다 **먼저** 선다: "금을 50으로 해줘"의 `해줘`는 서사 요청
      키워드지만 그 문장은 편집이다. 이름이 있으면 그 이름이 문장의 주어다.
    """
    content_lower = ooc_content.lower()
    
    # 수정, 설정 관련 키워드
    edit_keywords = [
        "추가", "삭제", "수정", "설정", "인벤토리", "골드", "돈", "아이템", 
        "획득", "잃음", "관계", "호감도", "패시브", "특성", "제거", "변경",
        "합쳐", "정리", "통합",
        "별칭", "개명", "같은 사람", "동일인물",   # [2026-10-10 OOC 확장] 인물 정정(시간·위치는 "시간 넘겨줘"가 새서 뺐다)
        "정정",   # [2026-10-10 기록 정정]
        "퀘스트",   # [2026-10-10 이야기 실 O-E] "약속"은 장면 요청("약속 장소로 가자")에 흔해서 뺐다
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


def _ooc_route_names(channel_id: str) -> list:
    """[2026-09-26 O1] 분류 콜에 주는 이름 — 명부 이름·별칭 + 시트 절 이름 + 선언 이름(짧게)."""
    names = []
    try:
        for _n, _d in (domain_manager.get_npcs(channel_id) or {}).items():
            names.append(_n)
            names.extend([a for a in ((_d or {}).get("aliases") or []) if isinstance(a, str)][:2])
    except Exception:
        pass
    names.extend(list(getattr(config, "WIKI_LORE_SECTIONS", {}).get("character", ())))
    try:
        import custom_vars as _cv_rn
        names.extend(_cv_rn.declared_mention_names(channel_id) or [])
    except Exception:
        pass
    return list(dict.fromkeys([str(n) for n in names if n]))


async def route_ooc_split(channel_id: str, ooc_content: str, client_genai, model_id: str) -> tuple:
    """[2026-09-26 O1] 행동+OOC 전용 — 짧은 분류 콜 → (수정 조각, 장면 지시 조각).
    콜이 실패하면 옛 키워드 분류: edit 면 통째 수정, 아니면 통째 지시(종전 동작)."""
    split = None
    try:
        split = await memory_system.route_ooc_request(client_genai, model_id, ooc_content,
                                                      _ooc_route_names(channel_id))
    except Exception as _e_rt:
        logging.warning(f"[OOC] route 실패 → 키워드 폴백: {_e_rt}")
    if not isinstance(split, dict):
        _t = classify_ooc_type(ooc_content, channel_id)
        logging.info(f"[OOC] route(폴백 키워드): {_t}")
        return (ooc_content, "") if _t == "edit" else ("", ooc_content)
    _e, _d = split.get("edit", ""), split.get("directive", "")
    logging.info(f"[OOC] route: edit={_e[:80]!r} directive={_d[:80]!r}")
    return _e, _d


def _ooc_clean_line(r: Any) -> str:
    return str(r or "").replace("**", "").strip()


def _ooc_memo_edit(channel_id: str, uid: str, edit: Dict[str, Any]) -> str:
    """[2026-10-16 C44 O2·O3] field=notebook — [메모] 줄 append/remove/edit → 되비침 한 줄(실패는 ⚠)."""
    act = str(edit.get("action") or "").strip().lower()
    value = edit.get("value")
    if act in ("append", "add"):
        r = game_system.add_memo(channel_id, str(value or ""), uid)
    elif act in ("remove", "delete"):
        r = game_system.remove_memo(channel_id, str(value or ""), uid)
    elif act in ("edit", "fix", "rename"):
        _v = value if isinstance(value, dict) else {}
        old = str(edit.get("old") or edit.get("from") or _v.get("old") or "").strip()
        new = str(edit.get("new") or edit.get("to") or _v.get("new") or "").strip()
        if not old:
            return "⚠ 메모: 바꿀 낱말(old)이 없어"
        r = game_system.edit_memo(channel_id, old, new, uid)
    else:
        return f"⚠ 메모: '{act or '?'}'은 못 해(append·remove·edit)"
    r = _ooc_clean_line(r)
    if not r:
        return "⚠ 메모: 내용이 없어"
    return ("⚠ 메모: " + r.lstrip("⚠️ ").strip()) if r.startswith("⚠") else ("메모: " + r.split(":", 1)[-1].strip())


def _ooc_item_edit(channel_id: str, uid: str, edit: Dict[str, Any]) -> str:
    """[2026-10-16 C44 O2] field=item — [소지품] add/remove(수량) → 되비침 한 줄(실패는 ⚠)."""
    act = str(edit.get("action") or "").strip().lower()
    value = edit.get("value")
    _v = value if isinstance(value, dict) else {}
    name = str(_v.get("name") or (value if isinstance(value, str) else "") or edit.get("key") or "").strip()
    qty = edit.get("qty", edit.get("quantity", _v.get("qty")))
    if not name:
        return "⚠ 소지품: 이름이 없어"
    if act in ("add", "append", "set", "obtain", "gain"):
        try:
            n = max(1, int(qty)) if qty not in (None, "") else 1
        except (TypeError, ValueError):
            n = 1
        r = _ooc_clean_line(game_character.add_item_to_sojipin(channel_id, name, uid, n, source="ooc"))
        if not r or r.startswith("⚠"):
            return f"⚠ 소지품: {r.lstrip('⚠ ').strip() or name}"   # [C61] 꺼짐·상한(99)
        return f"소지품: +{name}" + (f" ×{n}" if n > 1 else "") + (f" ({r})" if r.startswith("이미") else "")
    if act in ("remove", "delete", "lose", "use", "drop"):
        r = _ooc_clean_line(game_character.remove_item_from_sojipin(channel_id, name, uid,
                                                                     qty if qty not in (None, "") else None,
                                                                     source="ooc"))
        return f"⚠ 소지품: {r}" if (not r or "못 찾음" in r) else f"소지품: −{r.split(':', 1)[-1].strip()}"
    return f"⚠ 소지품: '{act or '?'}'은 못 해(add·remove)"


class CorrectionAskView(discord.ui.View):
    """[2026-10-18 C50 R4] 정정 되묻기 버튼 — persistent(custom_id 고정, main.on_ready 가 전 버튼 인스턴스를 등록).
    보류는 세션 메모리에 질문 메시지 id 로(`ooc_edits.store_pending`). 디스패치는 custom_id 매칭이라
    보낼 때는 후보 수만큼의 pick 버튼에 그 정정 문장을 글로 단다."""

    def __init__(self, labels: Optional[list] = None):
        super().__init__(timeout=None)
        n = 3 if labels is None else min(3, len(labels))
        for i in range(n):
            lab = (labels[i] if labels else f"{i + 1}번 정정을 이걸로 바꿈")[:80]
            b = discord.ui.Button(label=lab, style=discord.ButtonStyle.primary, custom_id=f"corr:pick:{i}")
            b.callback = self._cb(f"pick:{i}")
            self.add_item(b)
        for cid, lab, sty in (("corr:new", "다른 일 — 따로 둠", discord.ButtonStyle.secondary),
                              ("corr:drop", "취소", discord.ButtonStyle.danger)):
            b = discord.ui.Button(label=lab, style=sty, custom_id=cid)
            b.callback = self._cb(cid.split(":", 1)[1])
            self.add_item(b)

    @staticmethod
    def _cb(choice: str):
        async def _run(interaction: discord.Interaction):
            import ooc_edits as _oe_btn
            ch_id = str(interaction.channel_id)
            ok, line = _oe_btn.resolve_pending(ch_id, interaction.message.id, choice, interaction.user.id)
            if not ok and line.startswith("이 정정은 낸 사람"):
                await interaction.response.send_message(line, ephemeral=True)
                return
            content = (interaction.message.content or "") + f"\n→ {line}"
            await interaction.response.edit_message(content=content[:1990], view=None)
        return _run


async def run_ooc_edit(
    message: discord.Message,
    channel_id: str,
    ooc_content: str,
    client_genai,
    model_id: str,
    edit_part: str = "",
) -> bool:
    """[2026-09-26 O2] OOC 편집 한 벌 — 단독 OOC·행동+OOC·루카 모드 공용. 적용된 것이 하나라도 있으면 True.
    edit_part = 판정(루카 ⟦수정⟧ 줄·분류 콜 edit)이 떼어 준 수정 조각(편집 콜에 원문과 같이 간다)."""
    uid = str(message.author.id)
    # 데이터 로드
    ai_mem = domain_manager.get_ai_memory(channel_id, uid)
    p_data = domain_manager.get_participant_data(channel_id, uid)

    if not p_data:
        await message.channel.send("⚠️ 먼저 세션에 참가해주세요 (`!가면`).")
        return False

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
    # [2026-09-26 O8] 숫자 옆에 말(stands/friction)도 — 편집 모델이 말로 답할 수 있게.
    _rel_state = {}
    _mask_ooc = (p_data or {}).get("mask")
    try:
        if _mask_ooc:
            for _nn, _aa in domain_manager.get_npc_attitudes(channel_id, pc=_mask_ooc).items():
                _b_o, _t_o = int(_aa.get("bond", 0) or 0), int(_aa.get("tension", 0) or 0)
                _rel_state[_nn] = {"bond": _b_o, "tension": _t_o,
                                   "stands": domain_manager.attitude_from_bond(_b_o),
                                   "friction": domain_manager.tension_band(_t_o),
                                   "stance": _aa.get("stance", "")}
    except Exception as _e_rs:
        logging.debug(f"[OOC] 관계 상태 skip: {_e_rs}")

    # [2026-09-26 O5] 명부 — "아빠" 같은 호칭을 등록 이름으로 잇는 재료.
    _roster = []
    try:
        _roster = domain_manager.ooc_roster(channel_id, _mask_ooc)
    except Exception as _e_ro:
        logging.debug(f"[OOC] 명부 skip: {_e_ro}")

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

    # [2026-10-10 OOC 확장] 지금 장소·시간·날씨, 장소 이름들, 본문에 나온 인물이 아는 것 — 콜 0(저장분 읽기).
    _world_ed = {}
    try:
        import ooc_edits as _oe_st
        _world_ed = _oe_st.world_state_for_edit(channel_id, ooc_content)
    except Exception as _e_we:
        logging.debug(f"[OOC] world 상태 skip: {_e_we}")

    # AI 처리
    result = await memory_system.process_ooc_memory_edit(
        client_genai, model_id, ooc_content, ai_mem, p_data, notebook_text=notebook_txt,
        declared_block=declared_txt, relations_state=_rel_state, sheet_sections=_sheet_state,
        roster=_roster, edit_part=edit_part, world_state=_world_ed,
    )

    if not (result and result.get("edits")):
        await message.channel.send("⚠️ 루카가 수정 사항을 인식하지 못했어.")
        return False

    # 1. Separate Notebook Edits vs Memory Edits
    mem_edits = []
    decl_edits = []
    rel_edits = []
    sheet_edits = []
    npc_edits, place_edits, time_edits = [], [], []   # [2026-10-10 OOC 확장]
    fact_edits = []   # [2026-10-10 기록 정정]
    thread_edits, quest_edits = [], []   # [2026-10-10 이야기 실 O-E]
    applied = 0   # [2026-09-26 O10] 실제로 적용된 것 수 — 0 이면 모델 확인문 대신 "적용된 것 없음"
    # [2026-10-16 C44 O3] 메모·소지품·기억(조각·상태·타격) 편집도 결과 줄을 낸다 — 적용 수는 맨 끝에서 ⚠ 아닌 줄로만 센다.
    pre_lines: list = []

    for edit in result["edits"]:
        if not isinstance(edit, dict):
            continue
        field = edit.get("field")
        action = edit.get("action")
        value = edit.get("value")

        # [2026-09-10 P13] 선언 값 편집 — 관문 하나로 모아 뒀다가 한 번에 적용한다.
        if field == "declared":
            decl_edits.append(edit)
            continue
        # [2026-09-15 관계 통합] 관계 편집 → 엣지 직접 set(캡 면제, source="ooc").
        # [2026-09-26] 단수형 relationship 도 관계 칸으로(옛: 기억 편집으로 떨어져 조용히 무시).
        if field in ("relation", "relations", "relationship", "relationships"):
            rel_edits.append(edit)
            continue
        # [2026-10-10 OOC 확장] 인물·자리·시간 → ooc_edits(명령어와 같은 함수).
        if field in ("npc", "npcs", "character"):
            npc_edits.append(edit)
            continue
        if field in ("place", "location"):
            place_edits.append(edit)
            continue
        if field in ("time", "clock", "weather"):
            time_edits.append(edit)
            continue
        if field in ("fact", "facts", "record", "correction"):   # [2026-10-10 기록 정정] 지난 일
            fact_edits.append(edit)
            continue
        if field in ("thread", "threads", "promise", "promises"):   # [2026-10-10 이야기 실 O-E] 스레드 장부
            thread_edits.append(edit)
            continue
        if field in ("quest", "quests", "goal", "goals"):   # [C74 판2] 목표 = 장부 goal — 옛 칸 이름도 받아 thread 로 넘긴다
            quest_edits.append(edit)
            continue
        # [2026-09-16 시트 2차] 시트 절 편집 → PC 페이지 lore 절(grow_sheet는 lore 절을 안 건드린다).
        if field in _sheet_fields:
            # [2026-10-10 OOC 확장] 절 이름에 명부 NPC 이름이 달려 오면 그 NPC 시트다 — PC 시트에 떨어지지 않게 돌린다.
            _snk = domain_manager._ooc_edit_npc_name(edit)
            try:
                _snk_key = (domain_manager._find_npc_key(domain_manager.get_npcs(channel_id) or {}, _snk)
                            if _snk and not domain_manager.is_pc_name(_snk, domain_manager.pc_registry(channel_id)) else None)
            except Exception:
                _snk_key = None
            if _snk_key:
                npc_edits.append({"field": "npc", "op": "sheet", "key": _snk_key, "section": field,
                                  "action": edit.get("action") or "set", "value": edit.get("value")})
                continue
            sheet_edits.append(edit)
            continue

        # Notebook Handling
        if field in ["notebook", "notes", "note"]:
            # [notebook v2 2026-09-06] replace/set(전문 덮어쓰기) 폐지 —
            # WHY: OOC 한 줄이 [소지품]·[일지]·다른 메모까지 통째로 날리던 유일한 통로였다.
            # 남은 건 줄 단위 add/remove뿐(유저색 '-' 줄).
            # [2026-10-16 C44 O2·O3] + edit(줄 안 낱말 바꾸기 — "사라를 세라로" 같은 말이 메모에 닿게). 결과 줄을 낸다
            #   (옛: "이미 있음"·"못 찾음"을 버리고 +1 → 아무것도 안 바뀌어도 "처리 완료"). 소지품은 field=item.
            pre_lines.append(_ooc_memo_edit(channel_id, uid, edit))
            continue # handled
        # [2026-10-16 C44 O2] 소지품 = [소지품] 칸(수량). 옛: 아이템이 notebook append 로 [메모]에 쌓여 판정·상태창이 못 봤다.
        if field in ("item", "items", "inventory"):
            pre_lines.append(_ooc_item_edit(channel_id, uid, edit))
            continue

        mem_edits.append(edit)

    # 2. Apply Memory Edits
    if mem_edits:
        # [2026-09-24 감사] LLM 대기(위 await) **전에** 읽은 ai_mem/p_data 로 참가자 레코드를 통째 교체하면
        #   그 사이 배경 작업이 쓴 값(조각·일지·status)과 **이 루프 위에서 방금 한 노트북 편집**이
        #   되돌아갔다. 적용 직전에 다시 읽는다(여기서 저장까지 await 0 — 경합 창 없음).
        _fresh_p = domain_manager.get_participant_data(channel_id, uid) or p_data
        _fresh_mem = (_fresh_p or {}).get("ai_memory") or domain_manager.get_ai_memory(channel_id, uid) or ai_mem
        ai_mem = _fresh_mem
        _mem_lines: list = []
        new_mem, new_p_data = memory_system.apply_memory_edits(
            _fresh_mem, mem_edits, _fresh_p, report=_mem_lines
        )
        pre_lines += _mem_lines
        if isinstance(new_p_data, dict):
            new_p_data["ai_memory"] = new_mem
        # [2026-09-10 P13 버그] 순서가 **거꾸로였다.** save_participant_data 는 참가자
        #   레코드를 통째로 갈아 끼우므로(merge 아님), 뒤에 서면 방금 쓴 ai_memory 를
        #   호출 전 스냅샷으로 되돌린다 — 외모·성격·관계·패시브 OOC 편집이 전부 조용히
        #   증발하던 자리다. 참가자 저장이 먼저, 기억 병합이 나중(그쪽이 다시 읽는다).
        domain_manager.save_participant_data(channel_id, uid, new_p_data)
        domain_manager.update_ai_memory(channel_id, uid, new_mem)
        # [2026-10-16 C44 O3] 옛 `applied += len(mem_edits)` 삭제 — 적용 수는 결과 줄(⚠ 아닌 것)로 맨 끝에서 센다.
        # [2026-09-16 3차] origin=play 조각 삭제 = 발췌의 역연산 — desc 가 Observed 절 끝으로 돌아간다.
        try:
            domain_manager.return_removed_play_fragments(channel_id, uid, ai_mem, new_mem)
        except Exception as _e_fr:
            logging.warning(f"[OOC] 조각 되돌림 실패: {_e_fr}")

    # 2b. Apply Relation Edits — 엣지(NPC→이 PC). 되비침은 결과 메시지에 합류.
    rel_lines = list(pre_lines)   # [2026-10-16 C44 O3] 메모·소지품·기억 결과 줄이 앞에
    # [2026-10-10 OOC 확장] 인물 정정이 관계보다 먼저(같은 사람 합치기 뒤에 관계가 본체로 간다). 자리·시간은 뒤에.
    if npc_edits:
        try:
            import ooc_edits as _oe_np
            rel_lines += _oe_np.apply_npc_edits(channel_id, uid, npc_edits)
        except Exception as _e_np:
            logging.error(f"[OOC] 인물 편집 실패: {_e_np}")
            rel_lines.append("⚠️ 인물: 편집 중 오류")
    if rel_edits:
        # [2026-10-10 O-E 수리] `=` 였다 — 인물 줄(npc_edits)이 앞에 쌓였으면 지워져 되비침·적용 수에서 빠졌다.
        try:
            rel_lines += domain_manager.apply_ooc_relation_edits(channel_id, uid, rel_edits)
        except Exception as _e_rl:
            logging.error(f"[OOC] 관계 편집 실패: {_e_rl}")
            rel_lines.append("⚠️ 관계: 편집 중 오류")

    # 2c. Apply Sheet Section Edits
    if sheet_edits:
        rel_lines = rel_lines + domain_manager.apply_ooc_sheet_edits(channel_id, uid, sheet_edits)

    # 2c'. [2026-10-10 기록 정정] 지난 일 — 정오표 + 위키 Observed 정정(인물 정정 뒤라 합친 본체 이름으로 간다)
    _pend_corr: list = []   # [2026-10-18 C50 R4] 다른 턴 정정과 겹쳐 보류된 것 → 버튼 질문
    if fact_edits:
        try:
            import ooc_edits as _oe_fc
            rel_lines += _oe_fc.apply_fact_edits(channel_id, fact_edits, pending_out=_pend_corr)
        except Exception as _e_fc:
            logging.error(f"[OOC] 정정 편집 실패: {_e_fc}")
            rel_lines.append("⚠️ 정정: 편집 중 오류")

    # 2c''. [2026-10-10 이야기 실 O-E] 스레드 장부·퀘스트 보드
    if thread_edits or quest_edits:
        try:
            import ooc_edits as _oe_tq
            rel_lines += _oe_tq.apply_thread_edits(channel_id, thread_edits) if thread_edits else []
            rel_lines += _oe_tq.apply_quest_edits(channel_id, quest_edits) if quest_edits else []
        except Exception as _e_tq:
            logging.error(f"[OOC] 이야기 실 편집 실패: {_e_tq}")
            rel_lines.append("⚠️ 이야기 실: 편집 중 오류")

    # 2d. [2026-10-10 OOC 확장] 자리·시간
    if place_edits or time_edits:
        try:
            import ooc_edits as _oe_pt
            rel_lines += _oe_pt.apply_place_edits(channel_id, place_edits) if place_edits else []
            rel_lines += _oe_pt.apply_time_edits(channel_id, time_edits) if time_edits else []
        except Exception as _e_pt:
            logging.error(f"[OOC] 자리·시간 편집 실패: {_e_pt}")
            rel_lines.append("⚠️ 자리·시간: 편집 중 오류")

    # 3. Apply Declared Edits (P13) — 되비침 줄만 돌려받는다. 콜 0.
    decl_lines = []
    if decl_edits:
        try:
            import custom_vars as _cv_ap
            decl_lines = _cv_ap.apply_ooc_edits(channel_id, uid, decl_edits)
        except Exception as _e_ap:
            logging.error(f"[OOC] 선언 편집 실패: {_e_ap}")
            decl_lines = ["✗ 선언 편집 중 오류"]

    _fail_mark = ("⚠", "✗")
    applied += sum(1 for _ln in (rel_lines + decl_lines) if not str(_ln).lstrip().startswith(_fail_mark))

    # 결과 알림
    interp = result.get('interpretation', '')
    if applied:
        confirm = result.get('confirmation_message', '수정 완료')
        msg = f"📝 **OOC 처리 완료**\n"
        if interp: msg += f"> *{interp}*\n"
        msg += f"└ {confirm}"
    else:
        # [2026-09-26 O10] 모델이 미리 쓴 확인문은 적용 여부와 무관했다 → 적용 0 이면 사유만.
        msg = "⚠️ **OOC: 적용된 것 없음**\n"
        if interp: msg += f"> *{interp}*\n"
    # 새 메시지 0 — 선언 편집 되비침은 **이 결과 메시지에 합류**한다.
    for _ln in (rel_lines + decl_lines)[:12]:
        msg += f"\n  {_ln}"
    logging.info(f"[OOC] edit applied={applied} rel/sheet={len(rel_lines)} decl={len(decl_lines)}")

    await message.channel.send(msg)
    # [2026-10-18 C50 R4] 정정 보류 — 하나씩 묻는다(≤3). 버튼은 persistent, 보류는 세션 메모리에 질문 메시지 id 로.
    for _it in _pend_corr[:3]:
        try:
            import ooc_edits as _oe_pd
            _q, _labels = _oe_pd.pending_question(channel_id, _it)
            _qm = await message.channel.send(_q, view=CorrectionAskView(labels=_labels))
            _oe_pd.store_pending(channel_id, getattr(_qm, "id", None), _it, uid)
        except Exception as _e_pd:
            logging.warning(f"[OOC] 정정 보류 질문 실패: {_e_pd}")
    if applied > 0:
        domain_manager.block_redo(channel_id, "OOC 편집")   # [C77 C-4] ai_memory 는 되감기고 관계 · 위키는 남아 갈라졌다
    return applied > 0


async def handle_ooc_command(
    message: discord.Message,
    channel_id: str,
    ooc_content: str,
    client_genai,
    model_id: str
) -> Optional[str]:
    """[2026-09-26] **폴백 전용** — 루카 표지 판정(main._handle_ooc_via_luka)이 실패했을 때의 옛 키워드 분류.
    edit → 편집 한 벌 / narrative_request → 지시 문자열 반환 / general → None(루카는 이미 실패 메시지를 냈다)."""
    if not ooc_content:
        return None
    ooc_type = classify_ooc_type(ooc_content, channel_id)
    if ooc_type == "edit":
        await run_ooc_edit(message, channel_id, ooc_content, client_genai, model_id)
        return None
    elif ooc_type == "narrative_request":
        # 서사 지시는 프롬프트에 주입하기 위해 반환
        return f"[OOC Directive: {ooc_content}]"
    return None


@registry.register("ooc", category="Analysis", aliases=["OOC", "메타", "루카"], description="루카(OOC 도우미) 켜고 끄기",
                   usage=("`!ooc` — 루카 모드 토글(켜면 모든 메시지가 OOC 대화)",))
async def cmd_ooc(ctx: CommandContext) -> None:
    """!ooc - 루카 (OOC 도우미) 모드 ON/OFF 토글"""
    current = domain_manager.get_ooc_mode(ctx.channel_id)
    new_state = not current
    domain_manager.set_ooc_mode(ctx.channel_id, new_state)
    if new_state:
        await ctx.send("💬 **루카 모드 ON** — 루카가 대화에 참여합니다.\n> 모든 메시지가 OOC 대화로 처리됩니다. 해제: `!ooc`")
    else:
        await ctx.send("🎭 **루카 모드 OFF** — 서사 모드로 복귀합니다.")

# [2026-10-25 C78 A1] `!활력` 걷음 — 상태창 PC 장(활력 · 평형) · 고치기 PC 묶음. 켜고 끄기 `!모듈 활력 on/off`,
#   규칙 · 캡 `!출력룰 수정 변수 활력 | …`.


@registry.register("genre", category="World", aliases=["장르", "렌즈", "lens"], description="장르 · 렌즈 보기 · 손으로 정하기",
                   usage=("`!장르` — 지금 장르 · 렌즈", "`!장르 {렌즈…}` — 손으로 정하기(예: `!장르 noir drama`) · `!장르 초기화`"))
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


@registry.register("rule", category="World", aliases=["룰", "규칙", "rules", "worldrules", "세계규칙"],
                   description="산문에 늘 실리는 규칙 · 이 판의 결 — `!룰 추가` + 파일",
                   usage=("`!룰` · `!룰 목록` — 지금 룰",
                          "`!룰 추가` + 파일 — 파일 글 통째(다시 올리면 바뀜)",
                          "`!룰 추가 {설명}` · `!룰 추가 {키워드}: {설명}` — 한 줄 규칙",
                          "`!룰 삭제 {키워드|_n}` · `!룰 초기화`"))
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

    # [2026-10-25 C77 A-5] 전체 규칙 — 실패 안내는 걸린 칸만 싣고 이 길을 가리킨다.
    if sub in ('help', '도움', '도움말', '규칙', '?'):
        await send_long_message(ctx.message.channel, custom_vars.RULES_TEXT)
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
        # [2026-10-23 C61] 기본 목록(소지품)도 못 지운다 — PC가 들고 다니는 물건이 사는 자리다.
        _bi = custom_vars.builtin_name(name)
        if _bi:
            await ctx.send(
                f"🔒 `{_bi}`은(는) 코드가 심어 둔 기본 목록이라 지울 수 없습니다.\n"
                f"규칙·항목 상한·표시형식은 `!출력룰` 로 고칠 수 있습니다."
            )
            return
        if custom_vars.unregister(ctx.channel_id, name):
            domain_manager.block_redo(ctx.channel_id, "!출력룰 삭제 변수")   # [C77 C-4] 값은 world_state — 턴과 섞인 칸
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
            await send_long_message(ctx.message.channel, f"⚠️ {perr}\n\n{custom_vars.rules_hint(perr)}")
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

    # [2026-10-12 C34] 수정인데 칸이 플래그뿐이면(`호감 | 추적 항상`) 기존 선언 위에 그 칸만 덮는다.
    if sub in ('수정', 'edit', 'modify', 'update', 'u'):
        spec = custom_vars.merge_flag_edit(spec, existing)

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
    domain_manager.block_redo(ctx.channel_id, f"!출력룰 {verb} 변수")   # [C77 C-4] 시작값은 world_state — 턴과 섞인 칸
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


@registry.register("outputrule", category="World", aliases=["출력룰", "출력규칙", "outputrules", "출력"],
                   description="세는 값 · 상태창 · 문턱 묘사 · 도착물",
                   usage=("`!출력룰 목록` — 올린 파일마다 #번호 · `!출력룰 목록 변수` — 값마다 범위 · 캡 · 스코프",
                          "`!출력룰 추가` + 파일 — 모듈 하나(같은 파일 이름으로 다시 올리면 갱신)",
                          "`!출력룰 추가 변수 {이름} | {범위} | …` — 값 하나(파이프) · `!출력룰 수정 변수 {이름} | {칸}` — 칸 하나",
                          "`!출력룰 삭제 {이름|파일 이름}` · `!출력룰 초기화`",
                          "`!출력룰 도움 변수` — 변수 선언 규칙 전문"))
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
               "💠 보관함 = 노트북·기록·일지·도착물·관계 (매턴 버튼, 누른 사람에게만 보입니다)"]
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
            # [2026-10-23 C72] 파일 = 모듈 — 고정 번호 · 꺼짐 표시. 꺼진 모듈 이름은 치워 둔 칸에서 읽는다.
            for _b in _or_hint.bundles(ctx.channel_id):
                _nm_all = [n for lb in _or_hint.KIND_LABELS for n in (_b.get("names") or {}).get(lb, [])]
                _off = "" if _b["on"] else " · 꺼짐"
                msg.append(f"📎 **#{_b['no']} {_b['source']}** ({len(_nm_all)}{_off}): " + ", ".join(_nm_all))
            for _src, _names in sorted((_or_hint.names_by_source(ctx.channel_id)).items()):
                if not _src or _or_hint.is_bundle_source(_src):
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
            # [2026-10-12 C32 몸·인격] 장전 줄 `깃들기: 켜기|끄기` — 리스 모듈 붙이듯 명시로 켜고 끈다(로어 분석 판정보다 이긴다).
            #   코드가 떼어 읽고 라우터엔 안 보낸다. 그 줄뿐이면 라우터 콜 0.
            try:
                import vessel as _vs_arm
                file_text, _arm = _vs_arm.take_arm_line(file_text)
                if _arm is not None:
                    _vs_arm.set_arm(ctx.channel_id, _arm)
                    await ctx.send("🫥 깃들기(빙의·최면·몸 옮기기) 추적: " + ("켬" if _arm else "끔"))
                    if not (file_text or "").strip():
                        return
            except Exception as _e_arm:
                logging.getLogger("OutputRule").debug(f"[Vessel] 장전 줄 skip: {_e_arm}")
            # [2026-10-23 C72] 꺼진 모듈 파일은 다시 올리지 않는다 — 콜 전에 막는다(켜고 올리게).
            if _or.is_bundle_source(_src_name) and _or.is_parked(ctx.channel_id, _src_name):
                await ctx.send(f"⚠️ **{_or.bundle_label(_src_name)}** 모듈이 꺼져 있어요. "
                               f"`!모듈 {_or.bundle_label(_src_name)} on`으로 켜고 올려 주세요.")
                return
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
            # [2026-10-15 C38 가] 옛 자리의 패널 키(read-through `기본`)를 지운 경우 — 기본 섹션이
            #   사라졌으면 그 저장값도 같이(선언 층 섹션 삭제와 같은 규칙).
            if _is_panel:
                try:
                    if _sp_del.DEFAULT_PANEL_SECTION not in _sp_del.list_panel_sections(ctx.channel_id):
                        _sp_del.prune_saved_fields(ctx.channel_id, [_sp_del.DEFAULT_PANEL_SECTION])
                except Exception:
                    pass
            await ctx.send(f"🗑️ **출력규칙 삭제:** [{key}]")
        else:
            await ctx.send(f"⚠️ 출력규칙 '{key}'(을)를 찾을 수 없습니다.")
        return

    # 4. Reset
    # [2026-10-15 C38 나] 출력물 **통째** 초기화(형식·패널 섹션·변수·파생·전이·조건부 지시 + 그 값·
    #   패널 저장값·기록 행). 확인 단계는 `!클리어`와 같은 문법 — 확인 없이는 개수만 보여 준다.
    #   옛 판은 형식·섹션 정의만 지워 저장값이 1장째에 `섹션/칸`으로 남았다(스펙 C38 §0).
    if sub in ['reset', '초기화', 'clear']:
        import output_router as _or_rs
        _conf = len(args) >= 2 and args[1].strip().lower() in ('확인', 'confirm', 'y', 'yes')
        if not _conf:
            await ctx.send(_or_rs.format_reset_preview(_or_rs.reset_preview(ctx.channel_id)))
            return
        await ctx.send(_or_rs.format_reset_result(_or_rs.reset_all(ctx.channel_id)))
        return

    await ctx.send("⚠️ 사용법: `!출력룰 [목록/추가/삭제/초기화]` — 파일 첨부로 일괄 등록 가능")


# ⚰[2026-09-24 감사 §5-2 #19b] `_trigger_board`(명령 경로 게시판 트리거) 삭제.
#   `!시간 진행/N`·`!턴`(관찰)이 게이트 없이 게시판 콜을 돌려, P14 가 닫은 "아무도 부르지 않은 때 세계가
#   혼자 말을 건다" 문이 명령 두 곳에 남아 있었다(world_board 머리 묘비 참조). 도착물 방아쇠는 P14 의 둘뿐 —
#   ① 선언 전이 `deliver` ② 분석 신호 `arrival`(orchestration 4.75 핸드아웃).


_TIME_USAGE = ("⏰ 사용법: `!시간 진행` · `!시간 N`(12까지) · `!시간 설정 [시간대|HH:MM|N년 M월 D일 [HH:MM]]`\n"
               "보기는 Activity 상태창(세계 장).")


@registry.register("time", category="World", aliases=["시간"], description="시간 넘기기 · 정하기(보기는 상태창)",
                   usage=("`!시간 진행` · `!시간 {N}` — 시간대 하나 · N개(12까지) 넘기기",
                          "`!시간 설정 {시간대}` · `!시간 설정 HH:MM` · `!시간 설정 N년 M월 D일 [HH:MM]`",
                          "보기는 Activity 상태창(세계 장)"))
async def cmd_time(ctx: CommandContext) -> None:
    """!시간 [설정 시간대 / 진행 / N]"""
    args = ctx.args

    if not args:
        # [2026-10-25 C78 A5] 보기는 Activity 상태창(세계 장 — 날짜 · 시각 · 시간대 · 날씨). 옛 "위기" 줄(D4)도 같이 갔다.
        await ctx.send(_TIME_USAGE)
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
        # [2026-10-11 청소 `!시간` 두 벌] 쓰기는 ooc_edits.set_clock 한 곳(범위 검사·시각→시간대·저장·되비침).
        #   여기서는 형식만 읽는다. 옛 길은 world 를 직접 고쳐 써서 OOC 시간 편집과 같은 일을 두 곳이 따로 했다.
        #   달라진 것 하나: 날짜만 바꿀 때 시간대를 다시 맞추지 않는다(시각이 그대로라 시간대도 그대로).
        import re as _re
        import ooc_edits as _oe_tm
        _kw = None
        _note = ""
        # V8.5: N년 M월 D일 [HH:MM] 풀 캘린더 (HH:MM 생략 시 기존 시각 유지)
        if len(args) >= 4:
            rest = " ".join(args[1:])
            cal_match = _re.match(r'^\s*(\d+)년\s+(\d+)월\s+(\d+)일(?:\s+(\d{1,2}):(\d{2}))?\s*$', rest)
            if cal_match:
                _kw = {"year": cal_match.group(1), "month": cal_match.group(2), "day": cal_match.group(3)}
                if cal_match.group(4) is not None:
                    _kw.update(hour=cal_match.group(4), minute=cal_match.group(5))
                else:
                    _note = " — 시각은 기존 유지"
        if _kw is None:
            # HH:MM (시:분만 — 진행 중 세션 마이그레이션용, 2026-05-23)
            hhmm_match = _re.match(r'^(\d{1,2}):(\d{2})$', target)
            if hhmm_match:
                _kw = {"hour": hhmm_match.group(1), "minute": hhmm_match.group(2)}
            else:
                # 시간대 이름 (시간대만 바꾸고 시·분은 그대로)
                _kw = {"slot": target}
                _note = " — HH:MM 변경 안 됨(분 단위는 `!시간 설정 HH:MM`)"
        _r = _oe_tm.set_clock(ctx.channel_id, **_kw)
        if _r.startswith("⚠"):
            await ctx.send(_r + (" / 또는 HH:MM 형식" if "slot" in _kw else ""))
        else:
            await ctx.send(f"⏰ {_r}{_note}")
        return
    await ctx.send(_TIME_USAGE)


@registry.register("turn", category="World", aliases=["턴", "진행", "건너뛰기", "next"],
                   description="모은 행동 처리 · 관찰 턴 · 응답 모드",
                   usage=("`!턴` — 모은 행동 처리(수동 모드) · 모은 게 없으면 관찰 턴",
                          "`!턴 자동|수동|보조` — 응답 모드 · `!턴 모드` — 지금 모드"))
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


@registry.register("export", category="System", aliases=["추출", "로그"], description="대화 내역 파일",
                   usage=("`!추출` — 대화 내역 파일 · `!추출 증분` — 지난번 뒤로만",))
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


# [2026-10-25 C78 D1] 목록 = 한국어 대표 이름 · 판 차리는 순서. 표에 없는 명령은 "기타"로 저절로 선다(스모크가 비었는지 본다).
HELP_GROUPS = (
    ("세계 만들기", ("lore", "rule", "npc", "outputrule", "genre")),
    ("내 캐릭터", ("mask", "info")),
    ("진행", ("start", "turn", "retry", "time", "lock", "ooc")),
    ("모듈", ("modules",)),
    ("기록", ("lores", "export")),
    ("관리", ("ready", "clear", "reset", "bot")),
)
HELP_NAME = {"lore": "로어", "rule": "룰", "npc": "npc", "outputrule": "출력룰", "genre": "장르",
             "mask": "가면", "info": "설명", "start": "시작", "turn": "턴", "retry": "다시", "time": "시간",
             "lock": "잠금", "ooc": "ooc", "modules": "모듈", "lores": "연대기", "export": "추출",
             "ready": "준비", "clear": "클리어", "reset": "리셋", "bot": "봇", "help": "도움"}
HELP_HEAD = "📜 **로어키퍼 명령어** — `!도움 {명령}` 이면 하위 명령 · 예"


def help_name(name: str) -> str:
    return HELP_NAME.get(name, name)


def help_list_text() -> str:
    """`!도움` 목록 한 장 — discord 비의존(스모크가 본다)."""
    descs = registry._descriptions
    placed = {n for _g, ns in HELP_GROUPS for n in ns} | {"help"}
    lines = [HELP_HEAD]
    for title, names in HELP_GROUPS:
        rows = [f"`!{help_name(n)}` {descs.get(n, '')}" for n in names if n in registry._commands]
        if rows:
            lines.append(f"\n**{title}**")
            lines.extend(rows)
    rest = sorted(n for n in registry._commands if n not in placed)
    if rest:
        lines.append("\n**기타**")
        lines.extend(f"`!{help_name(n)}` {descs.get(n, '')}" for n in rest)
    return "\n".join(lines)


def help_detail_text(name: str) -> str:
    """`!도움 {명령}` 상세 — 설명 · 별칭 · 하위 명령 줄."""
    disp = help_name(name)
    al = [a for a in registry.aliases_of(name) if a != disp and a != name]
    head = f"`!{disp}` — {registry._descriptions.get(name, '')}"
    if al:
        head += "\n별칭: " + " · ".join(f"`!{a}`" for a in al)
    lines = [head]
    lines.extend(f"• {u}" for u in registry.usage_of(name))
    return "\n".join(lines)


@registry.register("help", category="System", aliases=["도움말", "도움", "명령어", "h"], description="명령어 목록 · `!도움 {명령}`",
                   usage=("`!도움` — 목록 · `!도움 {명령}` — 그 명령의 하위 명령 · 예",))
async def cmd_help(ctx: CommandContext) -> None:
    """!도움 [명령] — 목록 / 그 명령의 하위 명령 · 예."""
    word = (ctx.raw_args or "").strip().split()
    if word:
        name = registry.resolve(word[0])
        if name:
            await send_long_message(ctx.message.channel, help_detail_text(name))
            return
        await ctx.send(f"❔ 없는 명령이에요: `!{word[0].lstrip('!')}`" + _near_hint(word[0]))
        return
    await send_long_message(ctx.message.channel, help_list_text())


# [C78 검토] 걷은 명령 → 어디서 보나. difflib 은 엉뚱한 이름을 고른다(`!일지` → ?) — 걷은 이름은 이 표가 먼저.
_RETIRED_WHERE = {
    "활력": "상태창 PC 칸 · 켜고 끄기 `!모듈 활력 on/off`",
    "게시판": "Activity 모듈 앱 게시판 카드 · `!모듈 게시판 on/off`",
    "일지": "날 일지는 Activity 보관함 일지 장",
    "내정보": "Activity 인물 앱 내 페이지 · 상태창 — 설정 넣기는 `!설명`",
    "장면": "수위는 늘 전체예요(따로 정하는 명령 없음)",
    "재판정": "`!다시`",
}
for _k, _ws in (("활력", ("기력", "평형", "평정", "멘탈", "mental", "vigor")), ("게시판", ("board", "boardmod")),
                ("일지", ("journal",)), ("내정보", ("정보", "me")), ("장면", ("수위", "mature", "scene"))):
    for _w in _ws:
        _RETIRED_WHERE[_w] = _RETIRED_WHERE[_k]


def _near_hint(word: str) -> str:
    """가까운 이름 하나(대표 이름 · 별칭 전부에서) → " — `!출력룰`?" · 없으면 "". 끝에 (`!도움`).
    걷은 명령이면 " — 걷었어요 → {어디서}" (C78 검토)."""
    import difflib
    w = str(word or "").strip().lower().lstrip("!")
    if w in _RETIRED_WHERE:
        return f" — 걷었어요 → {_RETIRED_WHERE[w]} (`!도움`)"
    near = difflib.get_close_matches(w, registry.all_words(), n=1, cutoff=0.5)
    if near:
        return f" — `!{help_name(registry.resolve(near[0]) or near[0])}`? (`!도움`)"
    return " (`!도움`)"


_CMD_WORD_RE = re.compile(r"^[A-Za-z가-힣]")


async def _unknown_command_hint(ctx: CommandContext) -> None:
    """[2026-10-25 C78 D5] 없는 명령 → 한 줄. `!` 바로 뒤가 한글 · 영문일 때만(`!?` · `!!` 은 조용히)."""
    trig = str(ctx.trigger or "")
    if not int(getattr(config, "UNKNOWN_COMMAND_HINT", 1) or 0) or not _CMD_WORD_RE.match(trig):
        return
    await ctx.send(f"❔ 없는 명령이에요: `!{trig}`" + _near_hint(trig))

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
    try:
        if registry.resolve(ctx.trigger) is None:
            await _unknown_command_hint(ctx)   # [C78 D5]
            return None
        return await registry.dispatch(ctx)
    finally:
        _redo_guard_after(ctx)


# [2026-10-25 C77 C-4 가+ · Q9] 턴과 섞인 칸을 쓰는 명령 → 직전 턴 `!다시` 막기. 넘겨 얹을 수 있는 칸(`!룰` · `!장르` · 설정 ·
#   모듈 · 게시판 · 선언 층)은 `domain_manager._CARRY_*` 가 살리고, 여기는 못 살리는 칸만. 보기(인자 없음 · 이름만)는 안 막는다.
def _npc_writes(ctx: CommandContext) -> str:
    raw = (ctx.raw_args or "").strip()
    trig = str(ctx.trigger or "").lower()
    if getattr(ctx.message, "attachments", None) or trig in ("addnpc", "npc추가"):
        return "!npc 등록"
    if not raw:
        return ""
    parts = raw.split(None, 1)
    head, rest = parts[0].lower(), (parts[1] if len(parts) > 1 else "")
    if head in ("초기화", "reset", "remove", "delete", "del", "삭제", "제거", "alias", "별칭", "merge", "병합"):
        return f"!npc {parts[0]}"
    if head in ("상태", "status"):
        return f"!npc {parts[0]}" if len(rest.split()) >= 2 else ""
    if head in ("일정", "스케줄", "schedule", "sched", "voicecard", "vc", "보이스카드", "보이스"):
        return f"!npc {parts[0]}"          # [C77 검토] 인자 0~1개로도 NPC 칸(schedule · tone)을 쓴다 — 늘 막는다
    if head in ("시트", "sheet"):
        return "!npc 시트" if any(op in rest.lower().split() for op in ("set", "append", "remove")) else ""
    return "!npc 등록" if (":" in raw or "\n" in raw) else ""


def _lore_writes(ctx: CommandContext) -> str:
    # [C77 검토] 보기(인자 0) · `추출`(내보내기) · 확인 없는 `초기화`(안내만)는 안 쓴다 — cmd_lore 갈래와 같은 판정.
    if getattr(ctx.message, "attachments", None):
        return "!로어"
    a = (ctx.raw_args or "").split()
    if not a:
        return ""
    if len(a) <= 2 and a[0].lower() in ("추출", "export"):
        return ""
    if len(a) <= 2 and a[0] == "초기화" and (len(a) < 2 or a[1].lower() not in ("확인", "confirm", "y", "yes")):
        return ""
    return "!로어"


def _info_writes(ctx: CommandContext) -> str:
    # [C78 B2] 보기(`!내정보` · `!정보`)를 걷어 이 명령은 `!설명` 하나 — 글 · 파일이 있으면 쓴다.
    #   [C78 검토] `!info` 로 부르면 안 쓴다(cmd_info 와 같은 판정).
    if str(ctx.trigger or "").lower() not in ("desc", "설명"):
        return ""
    return "!설명" if (getattr(ctx.message, "attachments", None) or (ctx.raw_args or "").strip()) else ""


_REDO_WRITES = {
    "npc": _npc_writes,
    "lore": _lore_writes,
    "info": _info_writes,
    "mask": lambda c: "!가면" if c.args else "",
    "time": lambda c: "!시간" if c.args else "",
}


def _redo_guard_after(ctx: CommandContext) -> None:
    try:
        trig = str(ctx.trigger or "").lower()
        name = registry._aliases.get(trig, trig)
        fn = _REDO_WRITES.get(name)
        why = fn(ctx) if fn else ""
        if why:
            domain_manager.block_redo(ctx.channel_id, why)
    except Exception as e:
        logger.debug(f"[!다시] redo guard skipped: {e}")

