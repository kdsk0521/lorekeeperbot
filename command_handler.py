"""
Lorekeeper TRPG Bot - Command Handler Module
사용자 명령어(!help, !info 등)를 처리합니다.
"""

import discord
import logging
import io
from typing import Optional

# 모듈 임포트
import domain_manager
import quest_manager
import character_sheet
import session_manager
import world_manager
import memory_system
from bot_utils import send_long_message, read_attachment_text, safe_delete_message, SUPPORTED_TEXT_EXTENSIONS

# 상수
NPC_PREVIEW_LIMIT = 5

async def handle_lore_command(message, channel_id: str, arg: str, client_genai=None, model_id=None) -> None:
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

        # PC 정보 표시
        pc_info = domain_manager.get_default_pc_info(channel_id)
        if pc_info:
            pc_name = pc_info.get('name', 'Unknown')
            info_msg += f"**🧑 PC:** {pc_name}\n"
        else:
            info_msg += f"**🧑 PC:** 없음\n"

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
        domain_manager.clear_default_pc_info(channel_id)
        await message.channel.send("📜 **로어 초기화됨** - 장르, PC 정보도 기본값으로 복귀")
        return

    # 로어 추출 (텍스트 파일로 내보내기)
    if full.lower() in ['추출', '내보내기', 'export', 'dump']:
        import io
        export_text, msg = quest_manager.export_lore_data(channel_id)
        if export_text:
            f = io.BytesIO(export_text.encode('utf-8'))
            await message.channel.send(msg, file=discord.File(f, filename="lore_export.txt"))
        else:
            await message.channel.send(msg)
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
            # NPC만 추출 (원본 로어는 수정하지 않음, PC 제외)
            await status_msg.edit(content="⏳ **[AI]** NPC 추출 중 (PC 제외)...")
            npcs_extracted = await memory_system.extract_npcs_only(
                client_genai, model_id, raw_lore
            )

            # NPC 추가 (로어 출처 명시, 상세 정보 포함)
            for n in npcs_extracted:
                character_sheet.npc_memory.add_npc(
                    channel_id,
                    name=n.get("name"),
                    description=n.get("description"),
                    source="lore",
                    appearance=n.get("appearance"),
                    personality=n.get("personality"),
                    sexual_characteristics=n.get("sexual_characteristics"),
                    abilities=n.get("abilities"),
                    passives=n.get("passives")
                )

            # 원본 로어 그대로 저장 (AI가 재작성하지 않음)
            domain_manager.append_lore(channel_id, raw_lore)

            # 장르 분석 (원본 로어 기반)
            await status_msg.edit(content="⏳ **[AI]** 장르 분석 중...")

            res = await memory_system.analyze_genre_from_lore(client_genai, model_id, raw_lore)
            domain_manager.set_active_genres(channel_id, res.get("genres", ["noir"]))
            domain_manager.set_custom_tone(channel_id, res.get("custom_tone"))

            rules = await memory_system.analyze_location_rules_from_lore(client_genai, model_id, raw_lore)
            if rules:
                domain_manager.set_location_rules(channel_id, rules)

            # PC 정보 추출 (있는 경우에만)
            await status_msg.edit(content="⏳ **[AI]** PC 정보 확인 중...")
            pc_info = await memory_system.extract_pc_info(client_genai, model_id, raw_lore)

            # 최종 메시지
            final_msg = f"✅ **[분석 완료]**\n**장르:** {res.get('genres')}\n**NPC 추출:** {len(npcs_extracted)}명"

            if pc_info:
                # 채널의 기본 PC 정보로 저장
                domain_manager.set_default_pc_info(channel_id, pc_info)
                pc_name = pc_info.get('name', 'Unknown')
                final_msg += f"\n**PC 감지:** {pc_name}"
            else:
                # PC 정보 없음 - 정상 케이스, 에러 아님
                final_msg += f"\n**PC 정보:** 없음 (수동 설정 필요)"

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


async def handle_chronicle_command(message, channel_id: str, arg: str, client_genai=None, model_id=None) -> None:
    """연대기 명령어를 처리합니다."""
    # 연대기 생성 (AI 요약)
    if arg == "생성":
        msg = await message.channel.send("⏳ **[AI]** 현재까지의 이야기를 연대기로 요약 중입니다...")
        
        if not client_genai:
            await msg.edit(content="⚠️ AI 미연동 상태입니다.")
            return
        
        result_text = await quest_manager.generate_chronicle_from_history(client_genai, model_id, channel_id)
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
    # NPC 추출 (텍스트 파일로 내보내기)
    if npc_name.lower() in ['추출', '내보내기', 'export', 'dump']:
        import io
        export_text, msg = quest_manager.export_npc_data(channel_id)
        if export_text:
            f = io.BytesIO(export_text.encode('utf-8'))
            await message.channel.send(msg, file=discord.File(f, filename="npc_export.txt"))
        else:
            await message.channel.send(msg)
        return

    # NPC 초기화 (선택적)
    if npc_name.lower().startswith('초기화') or npc_name.lower().startswith('reset') or npc_name.lower().startswith('clear'):
        option = npc_name.replace('초기화', '').replace('reset', '').replace('clear', '').strip().lower()

        if option in ['로어', 'lore']:
            count = character_sheet.npc_memory.clear_npcs_by_source(channel_id, "lore")
            await message.channel.send(f"📖 로어 NPC {count}명 삭제됨")
        elif option in ['세션', 'session']:
            count = character_sheet.npc_memory.clear_npcs_by_source(channel_id, "session")
            await message.channel.send(f"🎭 세션 NPC {count}명 삭제됨")
        else:
            count = character_sheet.npc_memory.clear_npcs_by_source(channel_id, None)
            await message.channel.send(f"👥 전체 NPC {count}명 삭제됨")
        return

    # domain NPCs 조회
    npcs = domain_manager.get_npcs(channel_id)

    if not npc_name:
        # 전체 NPC 목록 (출처별 분류)
        if not npcs:
            await message.channel.send("⚠️ 등록된 NPC가 없습니다.")
            return

        result = "**━━━ 👥 NPC 목록 ━━━**\n\n"

        # 로어 NPC
        lore_npcs = [(n, d) for n, d in npcs.items() if d.get("source") == "lore"]
        if lore_npcs:
            result += "**📖 로어 NPC:**\n"
            for name, data in lore_npcs:
                status = data.get("status", "Active")
                rel = data.get("relationship")
                desc = data.get("desc", "")[:50]
                rel_str = f" [{rel}]" if rel else ""
                result += f"  • **{name}** ({status}){rel_str}"
                if desc:
                    result += f" - {desc}..."
                result += "\n"
            result += "\n"

        # 세션 NPC
        session_npcs = [(n, d) for n, d in npcs.items() if d.get("source") == "session"]
        if session_npcs:
            result += "**🎭 세션 NPC:**\n"
            for name, data in session_npcs:
                status = data.get("status", "Active")
                rel = data.get("relationship")
                desc = data.get("desc", "")[:50]
                rel_str = f" [{rel}]" if rel else ""
                result += f"  • **{name}** ({status}){rel_str}"
                if desc:
                    result += f" - {desc}..."
                result += "\n"
            result += "\n"

        # 출처 미정 NPC (기존 데이터 호환)
        other_npcs = [(n, d) for n, d in npcs.items() if not d.get("source")]
        if other_npcs:
            result += "**👤 기타 NPC:**\n"
            for name, data in other_npcs:
                status = data.get("status", "Active")
                rel = data.get("relationship")
                desc = data.get("desc", "")[:50]
                rel_str = f" [{rel}]" if rel else ""
                result += f"  • **{name}** ({status}){rel_str}"
                if desc:
                    result += f" - {desc}..."
                result += "\n"
            result += "\n"

        result += "\n💡 `!npc 초기화 [로어|세션]` - 선택적 삭제"

        await send_long_message(message.channel, result)
        return

    # 특정 NPC 조회
    npc_data = npcs.get(npc_name)

    if npc_data:
        status = npc_data.get('status', 'Active')
        desc = npc_data.get('desc', '설명 없음')
        source = npc_data.get('source', '미정')
        rel = npc_data.get('relationship')
        last_seen = npc_data.get('last_seen')

        source_tag = "📖 로어" if source == "lore" else ("🎭 세션" if source == "session" else "👤 기타")
        result = f"**{npc_name}** ({status})\n"
        result += f"출처: {source_tag}\n"
        if rel:
            result += f"관계: {rel}\n"
        if last_seen:
            result += f"마지막 등장: {last_seen}\n"
        result += f"\n{desc}"

        await message.channel.send(result)
    else:
        await message.channel.send(f"⚠️ '{npc_name}'라는 NPC를 찾을 수 없습니다.")


async def handle_info_command(message, channel_id: str, sub_command: str = "") -> None:
    """
    통합 정보 명령어를 처리합니다.
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
    # 관계 섹션: NPC 관계도 (domain.npcs 통합)
    # =========================================================
    if sub_type in ['all', 'relation']:
        result += "**━━━ 💞 관계 ━━━**\n"

        # 통합된 NPC 데이터에서 관계 읽기 (domain.npcs에서 직접)
        npcs = domain_manager.get_npcs(channel_id)

        has_relationship = False
        for name, data in npcs.items():
            rel = data.get("relationship")
            if rel:
                has_relationship = True
                desc = data.get("desc", "")
                short_desc = (desc[:30] + "...") if len(desc) > 30 else desc
                source_tag = "📖" if data.get("source") == "lore" else "🎭"
                result += f"  {source_tag} **{name}** ({rel})"
                if short_desc:
                    result += f" - _{short_desc}_"
                result += "\n"

        # 관계 없는 NPC들
        no_rel_npcs = [name for name, data in npcs.items() if not data.get("relationship")]
        if no_rel_npcs:
            if has_relationship:
                result += "\n👥 **기타 알려진 NPC:**\n"
            for name in no_rel_npcs[:10]:
                data = npcs[name]
                desc = data.get("desc", "")
                short_desc = (desc[:30] + "...") if len(desc) > 30 else desc
                source_tag = "📖" if data.get("source") == "lore" else "🎭"
                result += f"  {source_tag} **{name}** _(관계 미정)_"
                if short_desc:
                    result += f" - {short_desc}"
                result += "\n"
            if len(no_rel_npcs) > 10:
                result += f"  _... 외 {len(no_rel_npcs) - 10}명_\n"

        if not npcs:
            result += "_아직 알려진 NPC가 없습니다._\n"

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
            result += "\n📝 **메모:**\n"
            for m in memos[:5]:
                result += f"  • {m}\n"
            if len(memos) > 5:
                result += f"  _... 외 {len(memos) - 5}개_\n"
                
        result += "\n"

    await send_long_message(message.channel, result)


async def dispatch_command(cmd, message, channel_id, parsed, client_discord, client_genai, model_id, model_id_flash, domain_data):
    """
    명령어를 적절한 핸들러로 분배합니다.
    Returns: system_trigger (str or None)
    """
    system_trigger = None

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
            "`!잠금` - 세션 잠금\n\n"
            "**━━━ 🎲 주사위 & 분석 ━━━**\n"
            "`!r [주사위]` - 주사위 굴림\n"
            "`!분석 [질문]` - AI OOC 분석\n"
            "`!둠` / `!예측` - 위기 및 예측\n\n"
            "_더 자세한 내용은 `!도움말 전체`를 확인하세요._"
        )
        await send_long_message(message.channel, help_msg)
        return None
    
    # --- 세션 관리 ---
    if cmd == 'reset':
        await session_manager.manager.execute_reset(
            message, client_discord
        )
        return None
    
    if cmd == 'ready':
        await session_manager.manager.check_preparation(message)
        return None
    
    if cmd == 'start':
        domain_manager.update_participant(channel_id, message.author)
        if await session_manager.manager.start_session(
            message, client_genai, model_id
        ):
            return "[System: Generate a visceral opening scene for the campaign.]"
        return None
    
    if cmd == 'unlock':
        domain_manager.set_session_lock(channel_id, False)
        await message.channel.send("🔓 **잠금 해제**")
        return None
    
    if cmd == 'lock':
        domain_manager.set_session_lock(channel_id, True)
        await message.channel.send("🔒 **세션 잠금**")
        return None
    
    # --- 로어 명령어 ---
    if cmd == 'lore':
        await handle_lore_command(message, channel_id, parsed['content'].strip(), client_genai, model_id)
        return None
    
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
        return None
    
    # --- 진행/턴 ---
    if cmd in ['next', 'turn']:
        await message.add_reaction("🎬")
        return "[System: 기록된 모든 플레이어 행동을 종합하여 다음 장면을 진행하세요. 각 캐릭터의 행동과 침묵 모두 고려하여 서사적으로 진행하세요.]"
    
    # --- 캐릭터 관리 ---
    if cmd == 'mask':
        target = parsed['content']
        status = domain_manager.get_participant_status(channel_id, message.author.id)

        if status == "left":
            domain_manager.update_participant(channel_id, message.author, True)
            await message.channel.send("🆕 환생 완료")

        domain_manager.update_participant(channel_id, message.author)
        domain_manager.set_user_mask(channel_id, message.author.id, target)

        # PC 정보 자동 적용 (가면 이름이 PC 이름과 일치하거나 포함되면)
        pc_info = domain_manager.get_default_pc_info(channel_id)
        if pc_info:
            pc_name = pc_info.get('name', '')
            if pc_name and (target.lower() in pc_name.lower() or pc_name.lower() in target.lower()):
                applied = domain_manager.apply_pc_info_to_user(channel_id, message.author.id)
                if applied:
                    await message.channel.send(f"🎭 가면: {target}\n✨ 로어의 PC 정보가 자동 적용되었습니다!")
                    return None

        await message.channel.send(f"🎭 가면: {target}")
        return None

    if cmd in ['pc적용', 'applypc', 'pcapply']:
        applied = domain_manager.apply_pc_info_to_user(channel_id, message.author.id)
        if applied:
            await message.channel.send("✨ 로어의 PC 정보가 내 캐릭터에 적용되었습니다!\n`!내정보`로 확인하세요.")
        else:
            await message.channel.send("⚠️ 적용할 PC 정보가 없습니다.\n로어에 PC 정보가 포함되어 있는지 확인하세요.")
        return None

    if cmd == 'desc':
        domain_manager.update_participant(channel_id, message.author)
        domain_manager.set_user_description(
            channel_id, message.author.id, parsed['content']
        )
        await message.channel.send("📝 저장됨")
        return None
    
    if cmd == 'info':
        sub_cmd = parsed['content'].strip()
        await handle_info_command(message, channel_id, sub_cmd)
        return None
    
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
        return None
    
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
        return None
    
    # --- 참가자 상태 ---
    if cmd == 'afk':
        domain_manager.set_participant_status(channel_id, message.author.id, "afk")
        await message.channel.send("💤")
        return None
    
    if cmd == 'leave':
        domain_manager.set_participant_status(
            channel_id, message.author.id, "left", "이탈"
        )
        await message.channel.send("🚪")
        return None
    
    if cmd == 'back':
        domain_manager.update_participant(channel_id, message.author)
        await message.channel.send("✨")
        return None
    
    # --- 룰 명령어 ---
    if cmd == 'rule':
        await handle_rule_command(message, channel_id, parsed['content'].strip())
        return None
    
    # --- 연대기 ---
    if cmd == 'lores':
        await handle_chronicle_command(message, channel_id, parsed['content'].strip(), client_genai, model_id)
        return None
    
    # --- NPC 정보 ---
    if cmd == 'npc':
        await handle_npc_info_command(
            message, channel_id, parsed.get('content', '').strip()
        )
        return None
    
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
                    return None
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
                "• `!npc추가` + txt 파일 첨부 - 여러 NPC 일괄 추가\n"
            )
            return None
        
        # 이름과 설명 분리
        if file_text:
            # 파일이 있는 경우
            if content:
                # 이름이 지정된 경우: 단일 NPC (파일은 설명으로 사용)
                name = content
                desc = file_text
                character_sheet.npc_memory.add_npc(channel_id, name, desc, source="session")
                await message.channel.send(f"✅ 🎭 세션 NPC 추가됨: **{name}**\n{desc[:100]}{'...' if len(desc) > 100 else ''}")
            else:
                # 이름이 없는 경우: 일괄 추가
                npcs = memory_system.parse_bulk_npcs_from_text(file_text)
                if not npcs:
                    await message.channel.send("⚠️ 파일에서 NPC를 찾을 수 없습니다.")
                    return None
                
                # 모든 NPC 추가 (세션 출처)
                added_count = 0
                npc_names = []
                for npc in npcs:
                    name = npc.get("name", "").strip()
                    desc = npc.get("description", "").strip()
                    if name:
                        character_sheet.npc_memory.add_npc(channel_id, name, desc, source="session")
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
        else: # 파일이 없는 경우
            if not content:
                await message.channel.send("⚠️ NPC 이름과 설명을 입력해주세요. 예: `!npc 리엘: 숲의 정령`")
                return None
            
            if ":" in content:
                name, desc = content.split(":", 1)
                character_sheet.npc_memory.add_npc(channel_id, name.strip(), desc.strip(), source="manual")
                await message.channel.send(f"✅ 🎭 NPC 추가됨 (수동): **{name.strip()}**\n{desc.strip()}")
            else:
                name = content
                desc = "설명 없음"
                character_sheet.npc_memory.add_npc(channel_id, name, desc, source="manual")
                await message.channel.send(f"✅ 🎭 NPC 추가됨 (수동): **{name}**\n{desc}")
        return None
    
    # --- AI 분석 도구 ---
    if cmd == 'analyze' or cmd == 'ooc':
        question = parsed.get('content', '').strip()
        if not question:
            await message.channel.send(
                "🔍 **OOC 분석 모드**\n"
                "사용법: `!분석 [질문]` 또는 `!ooc [질문]`\n"
                "예: `!분석 이 NPC의 동기는 뭘까?`"
            )
            return None
        
        if not client_genai:
            await message.channel.send("⚠️ AI가 연결되지 않았습니다.")
            return None
        
        loading = await message.channel.send("🔍 **[OOC 분석 중...]**")
        
        # 컨텍스트 수집 - domain_data 사용
        lore = domain_manager.get_lore_with_npcs(channel_id)
        history = domain_data.get('history', [])[-20:]
        hist_text = "\n".join([f"{h['role']}: {h['content']}" for h in history])
        
        # 브레인스토밍 분석 호출
        result = await memory_system.analyze_brainstorming(
            client_genai, model_id, hist_text, lore, question
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
        return None
    
    # --- 세션 초기화 (Partial Reset: Lore Safe) ---
    if cmd == 'clear':
        domain_manager.reset_session_data(channel_id)
        
        # 세션 NPC만 삭제 (로어/수동 NPC 유지)
        removed_count = character_sheet.npc_memory.clear_npcs_by_source(channel_id, "session")
        
        # 채팅 청소 (최근 500개)
        try:
            await message.channel.purge(limit=500, check=lambda m: not m.pinned)
        except Exception as e:
            logging.warning(f"메시지 청소 실패: {e}")

        await message.channel.send(
            "🧹 **세션 클리어 완료** (부분 초기화)\n"
            "• 히스토리/기억 삭제 ✅\n"
            "• 참여자 정보 초기화 ✅\n"
            "• 퀘스트/메모 초기화 ✅\n"
            f"• 세션 NPC 삭제 ({removed_count}명) ✅\n"
            "• **로어/수동추가 NPC, 룰 유지** 🛡️\n"
            "• 최근 메시지 청소 완료 (최대 500개) 🗑️\n\n"
            "_※ 완전 초기화(폭파)를 원하시면 `!리셋`을 입력하세요._"
        )
        return None

    if cmd == 'consistency':
        if not client_genai:
            await message.channel.send("⚠️ AI가 연결되지 않았습니다.")
            return None
        
        loading = await message.channel.send("🔍 **[일관성 검사 중...]**")
        
        lore = domain_manager.get_lore_with_npcs(channel_id)
        history = domain_data.get('history', [])[-30:]
        hist_text = "\n".join([f"{h['role']}: {h['content']}" for h in history])
        
        result = await memory_system.check_narrative_consistency(
            client_genai, model_id, hist_text, lore
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
        return None
    
    if cmd == 'worldrules':
        if not client_genai:
            await message.channel.send("⚠️ AI가 연결되지 않았습니다.")
            return None
        
        loading = await message.channel.send("🌍 **[세계 규칙 추출 중...]**")
        
        lore = domain_manager.get_lore_with_npcs(channel_id)
        
        result = await memory_system.extract_world_constraints(
            client_genai, model_id, lore
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
        return None
    
    # --- Doom 예측 ---
    if cmd == 'forecast':
        forecast_msg = world_manager.get_doom_forecast(channel_id)
        await send_long_message(message.channel, forecast_msg)
        return None
    
    # --- Doom 수동 조절 ---
    if cmd == 'doom':
        arg = parsed.get('content', '').strip()
        if not arg:
            status = world_manager.get_doom_status(channel_id)
            await message.channel.send(
                f"📊 **위기 수치:** {status['value']}% ({status['description']})\n"
                f"{'🚨 위험!' if status['is_danger'] else '✅ 안전'}"
            )
            return None
        
        try:
            amount = int(arg)
            result = world_manager.change_doom(channel_id, amount)
            await message.channel.send(result)
            
            event = world_manager.trigger_doom_event(channel_id)
            if event:
                await message.channel.send(event)
        except ValueError:
            await message.channel.send("⚠️ 사용법: `!둠 [+/-숫자]` 또는 `!둠` (현재 상태)")
        return None
    
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
            return None
        
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
        return None
    
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
            return None
        
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
        return None
    
    return None
