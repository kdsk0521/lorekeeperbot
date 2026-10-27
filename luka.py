# -*- coding: utf-8 -*-
"""OOC·루카 — [2026-10-23 C64 M5] main.py 에서 이사(글자 그대로).

스펙  파티쳇수정/output_artifact/C64_모듈틀_M5렌더앞_지도·스펙_2026-10-23.md

- 정거장 `on_input(rt, b)` = 옛 `main._process_message` 3 단독 OOC · 3a 행동+OOC · 3.5 루카 모드 갈래.
  갈래를 타면 `b.stop`(입력을 먹었다). 턴이 필요하면 `b.turn_override`(+ `b.record_user_history`) — 턴은 main 이 띄운다.
  해당 없음 = 아무것도 안 하고 넘긴다(main 의 대기·자동 경로로).
- 루카 몸(표지 파서 · 회상 발췌 · 위키 발췌 · `generate_ooc_response`)은 옛 main.py 343~696줄 글자 그대로.
- 바뀐 건 이름뿐: `client_genai` → `rt.client` · `MODEL_ID` → `rt.model_id` · `MODEL_ID_FLASH` → `rt.model_id_flash` ·
  `generate_ai_response(…)` → 묶음에 적기. `_handle_ooc_via_luka` 는 runtime 을 `run_rt` 로 받는다(옛 지역 변수 `rt` 가 그 안에 산다).
- 로그는 옛 자리처럼 루트 로거(`logging.info`)로 남는다 — 줄 글자 그대로.
- 등록은 builtin_modules(`ooc` 모듈) — 여기는 몸만.
"""
import logging
from typing import Optional

import discord

import config
import bot_utils
import input_handler
import command_handler
import domain_manager


# =========================================================
# OOC HELPER (Lightweight AI for OOC mode)
# =========================================================

# ---------------------------------------------------------
# [2026-09-13 S4] 루카 과거형 질문 -> history_log 발췌 (C1 사용자 창구)
#   근거: memory_distribution_design 2026-09-13 v0.2 §2 C1.
#   플래그 V10_HISTORY_EVIDENCE 하위. 마커는 config.MEMORY_PAST_INTENT_MARKERS(S3) 공유.
#   OOC 분류기/edit/narrative/결합 경로는 건드리지 않는다 — 여기(루카 답변 콜)에만 얹는다.
# ---------------------------------------------------------
import re as _re_luka

_LUKA_TOKEN_SPLIT = _re_luka.compile(r"[^0-9A-Za-z\uac00-\ud7a3]+")
# 조사 꼬리 한 겹. 긴 것부터 시도하고, 마지막이 지시서의 한 글자 집합.
_LUKA_JOSA_TAILS = (
    "에서는", "으로는", "에서", "에게", "으로", "한테", "부터", "까지",
    "라고", "에는", "에도", "보다", "처럼", "마다", "조차", "밖에", "께서",
    "은", "는", "이", "가", "을", "를", "의", "에", "도", "로", "와", "과",
)


def _luka_ooc_body(raw: str) -> str:
    """`(ooc: …)` 껍데기를 벗긴 본문. 껍데기가 없으면(OOC 모드) 원문 그대로."""
    text = (raw or "").strip()
    try:
        body, _rest = input_handler.extract_ooc(text)   # [2026-09-26 O11] 짝 맞춤
        if body:
            return body
    except Exception:
        pass
    return text


# [2026-09-26 O1] 루카 표지 — 첫 줄들에만 선다. 변형 괄호([수정]·【수정】)도 받는다.
_LUKA_ROUTE_RE = _re_luka.compile(r"^\s*[⟦\[【]\s*(수정|장면)\s*[⟧\]】]\s*[:：]?\s*(.*)$")


def _luka_parse_route(text: str, allow_scene: bool = True) -> dict:
    """루카 응답 → {"edit", "scene", "scene_seen", "body"}. 표지는 맨 앞 줄들에서만 읽는다(본문 속 인용은 무시).
    값이 빈 표지는 "*"(= 원문 전체)."""
    edit, scene, scene_seen, body, lead = "", "", False, [], True
    for ln in (text or "").split("\n"):
        m = _LUKA_ROUTE_RE.match(ln) if lead else None
        if m:
            _v = m.group(2).strip() or "*"
            if m.group(1) == "수정":
                edit = _v if not edit else f"{edit} / {_v}"
            else:
                scene_seen = True
                if allow_scene:
                    scene = _v if not scene else f"{scene} / {_v}"
            continue
        if lead and not ln.strip():
            continue
        lead = False
        body.append(ln)
    return {"edit": edit, "scene": scene, "scene_seen": scene_seen, "body": "\n".join(body).strip()}


async def _handle_ooc_via_luka(run_rt, b, message: discord.Message, channel_id: str, ooc_content: str,
                               luka_mode: bool = False) -> None:
    """[2026-09-26 O1·O2] 단독 OOC·루카 모드 — 루카 콜이 판정을 겸한다.
    ⟦수정⟧ → 편집 한 벌(command_handler.run_ooc_edit) / ⟦장면⟧(단독만) → 턴 / 표지 없음 → 루카 답(이미 보냄).
    루카 콜이 실패하면 옛 키워드 분류로 폴백(단독 OOC 만)."""
    rt = await generate_ooc_response(run_rt, message, channel_id, route=("mode" if luka_mode else "ooc"))
    if rt is None:
        if luka_mode or not ooc_content:
            return
        directive = await command_handler.handle_ooc_command(message, channel_id, ooc_content, run_rt.client, run_rt.model_id)
        if directive:
            b.turn_override = directive   # [C64 M5] 옛 generate_ai_response(…, user_input_override=directive) — 턴은 main 이 띄운다
        return
    if rt.get("kind") != "route":
        return
    if rt.get("edit"):
        await command_handler.run_ooc_edit(
            message, channel_id, ooc_content or (message.content or ""), run_rt.client, run_rt.model_id,
            edit_part=("" if rt["edit"] == "*" else rt["edit"]))
    if rt.get("scene") and not luka_mode:
        _sc = ooc_content if rt["scene"] == "*" else rt["scene"]
        b.turn_override = f"[OOC Directive: {_sc}]"   # [C64 M5] 옛 generate_ai_response(…, user_input_override=…)


def _luka_strip_josa(tok: str) -> str:
    for tail in _LUKA_JOSA_TAILS:
        if len(tok) > len(tail) + 1 and tok.endswith(tail):
            return tok[:-len(tail)]
    return tok


def _luka_recall_keywords(channel_id: str, body: str) -> list:
    """ⓐ 명부 이름(별칭·사망 포함) 우선 -> ⓑ 조사 벗긴 토큰 길이 내림차순."""
    import config as _cfg
    from une_facade import _recall_name_in_input
    markers = tuple(getattr(_cfg, "MEMORY_PAST_INTENT_MARKERS", ()))
    out, seen = [], set()

    named = []
    try:
        npcs = domain_manager.get_npcs(channel_id) or {}
    except Exception:
        npcs = {}
    for nm, data in npcs.items():
        cands = [nm]
        try:
            cands += [a for a in (data.get("aliases") or []) if a]
        except Exception:
            pass
        for c in cands:
            pos = _recall_name_in_input(c, body)
            if pos >= 0:
                named.append((pos, c))
                break
    for _pos, c in sorted(named, key=lambda t: t[0]):
        if c not in seen:
            seen.add(c)
            out.append(c)

    toks = []
    for raw_tok in _LUKA_TOKEN_SPLIT.split(body):
        if len(raw_tok) < 2:
            continue
        if any(m in raw_tok for m in markers):
            continue
        tok = _luka_strip_josa(raw_tok)
        if len(tok) < 2 or tok in seen:
            continue
        seen.add(tok)
        toks.append(tok)
    toks.sort(key=len, reverse=True)
    out += toks

    return out[:int(getattr(_cfg, "LUKA_RECALL_KEYWORDS", 3))]


def _luka_recall_block(channel_id: str, raw_content: str) -> str:
    """과거형 마커가 있을 때만 history_log 발췌 블록. 실패·해당 없음이면 빈 문자열."""
    try:
        import config as _cfg
        if not getattr(_cfg, "V10_HISTORY_EVIDENCE", False):
            return ""
        body = _luka_ooc_body(raw_content)
        markers = tuple(getattr(_cfg, "MEMORY_PAST_INTENT_MARKERS", ()))
        if not body or not any(m in body for m in markers):
            return ""

        kws = _luka_recall_keywords(channel_id, body)
        if not kws:
            return ""

        import sqlite_store as _ss
        from une_facade import _recall_fmt_game_time
        rows_cap = int(getattr(_cfg, "LUKA_RECALL_ROWS", 6))
        picked, seen_keys = [], set()
        for kw in kws:
            for r in (_ss.search_history_log(channel_id, kw, limit=rows_cap) or []):
                key = r.get("message_id") or r.get("content")
                if key in seen_keys:
                    continue
                seen_keys.add(key)
                picked.append(r)
        if not picked:
            return ""
        picked = picked[:rows_cap]
        # 오래된 -> 최신. message_id가 전부 있으면 오름차순, 아니면 조회 순 역순.
        if all(r.get("message_id") for r in picked):
            try:
                picked.sort(key=lambda r: int(r["message_id"]))
            except (TypeError, ValueError):
                picked.reverse()
        else:
            picked.reverse()

        head = ('\n[기록 발췌 - 과거형 질문에 한해 첨부, 발췌는 "그때 말해진 것"이지 '
                '현재 상태가 아님]\n')
        line_cap = int(getattr(_cfg, "LUKA_RECALL_LINE_CHARS", 160))
        total_cap = int(getattr(_cfg, "LUKA_RECALL_CHARS", 1200))
        block, used = head, len(head)
        n = 0
        for r in picked:
            line = (f"- {_recall_fmt_game_time(r.get('game_time'))}"
                    f"{r.get('role')}: {str(r.get('content') or '')[:line_cap]}\n")
            if used + len(line) > total_cap:
                break            # 줄 단위 절단 — 줄 중간에서 자르지 않는다
            block += line
            used += len(line)
            n += 1
        if n == 0:
            return ""
        logging.info(f"[Evidence] C1 luka keywords={len(kws)} rows={n} chars={used}")
        return block
    except Exception as e:
        logging.debug(f"[Evidence] C1 luka skipped: {e}")
        return ""


def _luka_wiki_block(channel_id: str, raw_content: str) -> str:
    """[2026-09-14 W3a] 루카(C1) 위키 발췌 — play 절만, 네 절 전부.

    과거형 표지와 **무관하게** 본문에 이름만 있으면 붙인다(루카는 사용자 창구라
    재탕 위험 0). 이름 추출은 S4 `_luka_recall_keywords`를 그대로 재사용하고,
    명부 밖 토큰은 `wiki_store.resolve_page`가 알아서 버린다(중복 구현 0)."""
    try:
        import config as _cfg
        if not getattr(_cfg, "WIKI_COMPILE", False):
            return ""
        body = _luka_ooc_body(raw_content)
        if not body:
            return ""
        kws = _luka_recall_keywords(channel_id, body)
        if not kws:
            return ""
        import wiki_store as _ws
        res = _ws.compile_for(channel_id, "C1", kws)
        text = res.get("text") or ""
        if not text:
            return ""
        return "\n[위키 발췌]\n" + text + "\n"
    except Exception as e:
        logging.debug(f"[Wiki] C1 luka skipped: {e}")
        return ""


async def generate_ooc_response(
    rt,
    message: discord.Message,
    channel_id: str,
    route: str = "",
) -> Optional[dict]:
    """OOC 도우미 모드 응답 생성 (Flash 모델 사용)
    [2026-09-26 O1] route="ooc"(단독 OOC: ⟦수정⟧·⟦장면⟧) / "mode"(루카 모드: ⟦수정⟧만) 이면 표지 계약을 붙이고,
    표지가 있으면 **보내지 않고** {"kind": "route", "edit", "scene"} 를 돌려준다. 답을 보냈으면 {"kind": "answered"}, 실패 None."""
    if not rt.client:
        await message.channel.send("⚠️ No AI Configured")
        return None

    from google.genai import types
    import text_resources

    # Build context
    import game_world
    import npc_manager

    history = domain_manager.get_history(channel_id)
    history_text = "\n".join(
        [f"{h['role']}: {h['content']}" for h in history[-15:]]
    ) if history else "(히스토리 없음)"

    lore_text = domain_manager.get_lore(channel_id) or "(로어 없음)"

    # 세계 상태
    world_context = game_world.get_world_context(channel_id)

    # NPC 현황
    npcs = domain_manager.get_npcs(channel_id)
    npc_lines = []
    # [2026-09-24 감사] NPC dict 에는 attitude 키가 없다(09-15 이후 관계 = relations 엣지 파생) — 전원 "?"였다.
    try:
        _atts = domain_manager.get_npc_attitudes(channel_id) or {}
    except Exception:
        _atts = {}
    for name, data in npcs.items():
        attitude = (_atts.get(name) or {}).get("attitude") or data.get("attitude", "?")
        role = data.get("role", "")
        npc_lines.append(f"- {name}: {role} (태도: {attitude})")
    npc_text = "\n".join(npc_lines) if npc_lines else "(등록된 NPC 없음)"

    # 세션 AI 메모리 (진행 중인 스레드, 아크 등)
    ai_mem = domain_manager.get_session_ai_memory(channel_id)
    mem_parts = []
    # [2026-09-25 스레드 장부] 진행 중·해결된 서사 = 장부(옛 active/resolved_threads 쓰기 없음).
    try:
        import thread_ledger as _tl_lk
        _tl_act, _tl_res = _tl_lk.luka_lines(channel_id)
    except Exception:
        _tl_act, _tl_res = "", ""
    if _tl_act:
        mem_parts.append(f"진행 중인 서사: {_tl_act}")
    try:   # [2026-10-25 C74 판4 · G4] 한 줄 아크 = 코드가 우물에서(옛 세션 기억 current_arc 안 읽음)
        import well_store as _ws_lk
        _arc_lk = _ws_lk.arc_line(channel_id)
    except Exception:
        _arc_lk = ""
    if _arc_lk:
        mem_parts.append(f"현재 아크: {_arc_lk}")
    if _tl_res:
        mem_parts.append(f"해결된 서사: {_tl_res}")
    mem_text = "\n".join(mem_parts) if mem_parts else ""

    system_prompt = text_resources.OOC_HELPER_IDENTITY
    system_prompt += f"\n[세계 상태]\n{world_context}\n" if world_context else ""
    system_prompt += f"\n[NPC 현황]\n{npc_text}\n"
    system_prompt += f"\n[서사 진행]\n{mem_text}\n" if mem_text else ""
    # [2026-09-13 S4] 과거형 질문이면 로그 원문 발췌를 [최근 히스토리] 바로 앞에 끼운다.
    system_prompt += _luka_recall_block(channel_id, message.content)
    # [2026-09-14 W3a] 위키 play 절 — [기록 발췌] 뒤 · [최근 히스토리] 앞.
    system_prompt += _luka_wiki_block(channel_id, message.content)
    system_prompt += f"\n[최근 히스토리]\n{history_text}\n\n[로어 요약]\n{lore_text[:2000]}"
    # [2026-10-10 기록 정정] 루카가 읽은 옛 기록(서사 진행·회상·위키·원문)에 걸린 정정(R8). 표지 계약보다 앞(계약이 맨 끝).
    try:
        import corrections as _corr_lk
        _cb_lk = _corr_lk.block_for(channel_id, system_prompt + "\n" + (message.content or ""))
        if _cb_lk:
            system_prompt += "\n\n" + _cb_lk
    except Exception:
        pass
    # [2026-09-26 O1] 판정은 이미 도는 루카 콜이 겸한다 — 표지 계약은 맨 끝(최근접).
    if route in ("ooc", "mode"):
        system_prompt += "\n\n" + text_resources.LUKA_ROUTE_CONTRACT
        if route == "ooc":
            system_prompt += "\n" + text_resources.LUKA_ROUTE_SCENE

    user_content = message.content.strip()
    if message.attachments:
        for att in message.attachments:
            txt, _ = await bot_utils.read_attachment_text(att)
            if txt:
                user_content += f"\n(첨부: {txt})"

    try:
        response = await rt.client.aio.models.generate_content(
            model=rt.model_id_flash,
            contents=[
                # [2026-10-06 분석 틀] 인가 훈련 대화·"in character" 삭제 — 틀은 시스템 머리에 한 번(OOC 모드는 system_prompt가 집).
                types.Content(role="user", parts=[types.Part(text=user_content)])
            ],
            config=types.GenerateContentConfig(
                system_instruction=f"{text_resources.ANALYSIS_FRAME}\n\n{system_prompt}",
                temperature=0.7,
                # [2026-10-11 C27b] 2000 → 8192. 추론 켠 분석 콜은 추론이 출력 예산에 같이 든다 — 상한 통일(레티어스 "나머지도 다 올리자").
                max_output_tokens=8192,
                safety_settings=config.SAFETY_SETTINGS,
            )
        )
        if response and response.text:
            _txt = response.text.strip()
            if route in ("ooc", "mode"):
                _rt = _luka_parse_route(_txt, allow_scene=(route == "ooc"))
                if _rt["edit"] or _rt["scene"]:
                    logging.info("[OOC] luka-route: %s", "+".join(
                        [k for k, v in (("수정", _rt["edit"]), ("장면", _rt["scene"])) if v]))
                    return {"kind": "route", "edit": _rt["edit"], "scene": _rt["scene"]}
                logging.info("[OOC] luka-route: 답")
                _txt = _rt["body"]
                if not _txt:
                    if _rt["scene_seen"]:
                        await message.channel.send("💬 루카 모드에선 장면을 돌리지 않아 — `!ooc`로 나온 뒤 말해 줘.")
                        return {"kind": "answered"}
                    _txt = response.text.strip()
            # [루카] 프리픽스 + Discord 인용 블록으로 IC/OOC 시각 구분
            lines = _txt.split("\n")
            quoted = "\n".join(f"> {line}" if line.strip() else ">" for line in lines)
            formatted = f"**[루카]**\n{quoted}"
            await bot_utils.send_long_message(message.channel, formatted)
            return {"kind": "answered"}
        else:
            await message.channel.send("⚠️ 루카가 응답하지 못했습니다.")
            return None
    except Exception as e:
        logging.error(f"OOC Response Error: {e}", exc_info=True)
        await message.channel.send(f"⚠️ 루카 오류: {e}")
        return None


# =========================================================
# on_input 정거장 — 옛 main._process_message 3 · 3a · 3.5
# =========================================================
async def on_input(rt, b) -> None:
    """[2026-10-23 C64 M5] 옛 갈래 글자 그대로. rt = client · model_id · model_id_flash(main 의 `_INPUT_RT`)."""
    message, channel_id, parsed = b.message, b.channel_id, b.parsed
    # 3. PURE OOC (GM에게 질문/메타 요청)
    # [2026-09-26 O1·O2] 루카 콜이 판정을 겸한다(⟦수정⟧/⟦장면⟧ 표지). 옛: 키워드 분류 → 편집 뒤에도 루카가 또 답했다.
    if parsed and parsed['type'] == 'ooc':
        b.stop = True   # [C64 M5] 이 갈래가 입력을 먹는다
        ooc_content = parsed.get('content', '')
        await _handle_ooc_via_luka(rt, b, message, channel_id, ooc_content)
        return

    # 3a. CHAT + OOC (IC 행동 + 서사 지시)
    if parsed and parsed['type'] == 'chat_with_ooc':
        b.stop = True   # [C64 M5] 이 갈래가 입력을 먹는다
        ic_text = parsed.get('chat_content', '')
        ooc_content = parsed.get('ooc_content', '')
        mask = domain_manager.get_user_mask(channel_id, message.author.id)

        # IC 행동을 히스토리에 기록
        if ic_text:
            # [LIBRA #2 C1] Discord message.id 보존 — 출처 회상용 단서
            domain_manager.append_history(channel_id, mask, ic_text, message_id=message.id)

        # [2026-09-26 O2] 턴 **전에** OOC 조각을 가른다(짧은 분류 콜 — 루카가 안 도는 자리).
        #   수정은 먼저 적용 → 그 턴 Theoria 4b 가 바뀐 값을 본다. 장면 지시는 수정 부분을 뺀 나머지만
        #   (수정 문구가 렌더러로 가면 "호감도 올려"를 서사로 풀어 버린다). 지시가 없으면 행동만으로 턴.
        _edit_part, _dir_part = await command_handler.route_ooc_split(
            channel_id, ooc_content, rt.client, rt.model_id_flash)
        if _edit_part:
            await command_handler.run_ooc_edit(message, channel_id, ooc_content, rt.client, rt.model_id,
                                               edit_part=_edit_part)
        combined_directive = (f"[플레이어 행동: {ic_text}] [OOC 지시: {_dir_part}]" if _dir_part else ic_text)
        # [2026-07-02] IC 원문은 위에서 이미 기록(message_id 포함) — execute의 user 기록은
        # 스킵해 이중 잔존 차단. 결합 디렉티브는 이번 턴 프롬프트로만 쓰고 히스토리엔 안 남김
        # (OOC 메타가 IC 기록에 영구 노출되던 것도 함께 차단).
        b.turn_override = combined_directive   # [C64 M5] 옛 generate_ai_response(…, user_input_override=combined_directive,
        b.record_user_history = False          #   record_user_history=False) — 턴은 main 이 띄운다
        return

    # 3.5. OOC MODE CHECK
    # [2026-09-26 O1] 루카 모드도 루카 콜이 판정(⟦수정⟧만 — 루카 모드는 턴을 안 돈다). 늘어나는 콜 0.
    if domain_manager.get_ooc_mode(channel_id):
        b.stop = True   # [C64 M5] 이 갈래가 입력을 먹는다
        await _handle_ooc_via_luka(rt, b, message, channel_id, (message.content or "").strip(), luka_mode=True)
        return
