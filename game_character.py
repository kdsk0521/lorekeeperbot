"""
Lorekeeper TRPG Bot - Game Character Module
Handles Character Inventory, Status Effects, Quests, Memos, and Mechanics (Dice, Normality).
Extracted from game_system.py
"""

import logging
import random
import time
import re
import asyncio
from typing import List, Dict, Any, Tuple, Optional

import config
import domain_manager
from config import (
    STATUS_EFFECTS,
    NEGATIVE_STATUS_EFFECTS,
    POSITIVE_STATUS_EFFECTS,
    SEVERITY_DOOM_IMPACT,
    get_normality_stage_info
)
from google.genai import types

# =========================================================
# QUEST & MEMO SYSTEM
# =========================================================

def _get_board(channel_id: str) -> Dict[str, Any]:
    d = domain_manager.get_domain(channel_id)
    if "quest_board" not in d or not isinstance(d["quest_board"], dict):
        d["quest_board"] = {"active": [], "completed": [], "memos": [], "archive": [], "lore": []}
    return d["quest_board"]

def _save_board(channel_id: str, board: Dict[str, Any]) -> None:
    domain_manager.update_quest_board(channel_id, board)

# Generic List Ops
def _list_op(cid, key, content, emoji, name):
    if not content: return None
    board = _get_board(cid)
    lst = board.get(key, [])
    if content not in lst:
        lst.append(content)
        board[key] = lst
        _save_board(cid, board)
        return f"{emoji} **{name} 등록:** {content}"
    return f"⚠️ 이미 등록된 {name}입니다."

def _del_op(cid, key, content, emoji, name):
    if not content: return None
    board = _get_board(cid)
    lst = board.get(key, [])
    target = next((i for i in lst if content in i), None) # Partial Match
    if target:
        lst.remove(target)
        board[key] = lst
        _save_board(cid, board)
        return f"{emoji} **{name} 제거:** {target}"
    return f"⚠️ 해당 {name}를 찾을 수 없습니다."

def _move_op(cid, src, dst, content, emoji, name, action):
    if not content: return None
    board = _get_board(cid)
    s_lst = board.get(src, [])
    d_lst = board.get(dst, [])
    target = next((i for i in s_lst if content in i), None)
    if target:
        s_lst.remove(target)
        d_lst.append(target)
        board[src] = s_lst
        board[dst] = d_lst
        _save_board(cid, board)
        return f"{emoji} **{name} {action}:** {target}"
    return f"⚠️ 해당 {name}를 찾을 수 없습니다."

# Operations
def add_quest(channel_id: str, content: str) -> str:
    return _list_op(channel_id, "active", content, "🔥", "퀘스트")

def complete_quest(channel_id: str, content: str) -> str:
    res = _move_op(channel_id, "active", "completed", content, "✅", "퀘스트", "완료")
    if "✅" in res:
        # Reduce Doom on success
        import game_world
        doom_msg = game_world.change_doom(channel_id, -5) # Reward
        return f"{res}\n{doom_msg}"
    return res

def remove_quest(channel_id: str, content: str) -> str:
    return _del_op(channel_id, "active", content, "🗑️", "퀘스트")

def add_memo(channel_id: str, content: str) -> str:
    return _list_op(channel_id, "memos", content, "📝", "메모")

def remove_memo(channel_id: str, content: str) -> str:
    return _del_op(channel_id, "memos", content, "🗑️", "메모")

def resolve_memo_auto(channel_id: str, content: str) -> str:
    return _move_op(channel_id, "memos", "archive", content, "🗄️", "메모", "해결(보관)")

# Text Views
def get_active_quests_text(channel_id: str) -> str:
    board = _get_board(channel_id)
    active = board.get("active", [])
    if not active: return "📭 현재 진행 중인 퀘스트가 없습니다."
    return "🔥 **진행 중인 퀘스트:**\n" + "\n".join([f"{i+1}. {q}" for i, q in enumerate(active)])

def get_memos_text(channel_id: str) -> str:
    board = _get_board(channel_id)
    memos = board.get("memos", [])
    if not memos: return "📭 저장된 메모가 없습니다."
    return "📝 **메모 목록:**\n" + "\n".join([f"- {m}" for m in memos])

def get_status_message(channel_id: str) -> str:
    return f"{get_active_quests_text(channel_id)}\n\n{get_memos_text(channel_id)}"

def get_objective_context(channel_id: str) -> str:
    board = _get_board(channel_id)
    active = board.get("active", [])
    memos = board.get("memos", [])
    archive = board.get("archive", [])
    
    if not active and not memos and not archive:
        return config.EMPTY_QUEST_MEMO_MSG
        
    txt = "### [퀘스트 및 메모]\n"
    if active:
        txt += "**진행 중인 목표:**\n" + "\n".join([f"- {q}" for q in active]) + "\n\n"
    if memos:
        txt += "**저장된 메모:**\n" + "\n".join([f"- {m}" for m in memos]) + "\n\n"
    if archive:
        txt += "**보관된 정보:**\n" + "\n".join([f"- {m}" for m in archive[-config.MAX_ARCHIVE_DISPLAY:]]) + "\n"
    return txt.strip()

# =========================================================
# CHARACTER STATUS & INVENTORY
# =========================================================

def update_inventory(user_data: Dict[str, Any], action: str, item_name: str, count: int = 1) -> Tuple[Dict[str, Any], str]:
    inv = user_data.get("inventory", {})
    curr = inv.get(item_name, 0)
    msg = ""
    
    if action == "add":
        inv[item_name] = curr + count
        msg = f"🎒 **획득:** {item_name} x{count}"
    elif action == "remove":
        if curr < count: msg = f"❌ **부족:** {item_name} (보유: {curr})"
        else:
            inv[item_name] = curr - count
            if inv[item_name] <= 0: del inv[item_name]
            msg = f"📉 **사용:** {item_name} x{count}"
    
    user_data["inventory"] = inv
    return user_data, msg

def update_status_effect(user_data: Dict[str, Any], action: str, effect_name: str) -> Tuple[Dict[str, Any], str]:
    effects = user_data.get("status_effects", [])
    info = STATUS_EFFECTS.get(effect_name, {})
    msg = ""
    
    if action == "add":
        if effect_name not in effects:
            effects.append(effect_name)
            sym = "✨" if info.get("type") == "buff" else "⚠️"
            msg = f"{sym} **상태:** [{effect_name}]"
        else: msg = f"⚠️ 이미 [{effect_name}] 상태"
    elif action == "remove":
        if effect_name in effects:
            effects.remove(effect_name)
            msg = f"✨ **해제:** [{effect_name}]"
        else: msg = f"⚠️ [{effect_name}] 없음"
        
    user_data["status_effects"] = effects
    return user_data, msg

def perform_check(channel_id: str, user_id: str, action_desc: str = "") -> str:
    """
    주사위 판정 (1d100) + 상태 이상 보정
    """
    
    # 1. 데이터 로드
    p_data = domain_manager.get_participant_data(channel_id, user_id)
    if not p_data:
        return "⚠️ 먼저 `!가면`으로 캐릭터를 등록하세요."
        
    # 2. 보정치 계산
    effects = p_data.get("status_effects", [])
    modifier = 0
    mod_details = []
    
    for eff in effects:
        # Buff
        if eff in POSITIVE_STATUS_EFFECTS:
            val = 10 # 기본 버프 보정치
            modifier += val
            mod_details.append(f"{eff}(+{val})")
            continue
            
        # Debuff
        if eff in NEGATIVE_STATUS_EFFECTS:
            severity = NEGATIVE_STATUS_EFFECTS[eff]
            val = severity * -5 # 심각도당 -5
            modifier += val
            mod_details.append(f"{eff}({val})")
            continue

    # [NEW] Doom Modifier
    world = domain_manager.get_world_state(channel_id)
    doom = world.get("doom", 0)
    doom_mod = ((config.DOOM_DICE_BASELINE - doom) // 10) * config.DOOM_DICE_MODIFIER_STEP
    if doom_mod != 0:
        modifier += doom_mod
        sign = "+" if doom_mod > 0 else ""
        mod_details.append(f"Doom({sign}{doom_mod})")
            
    # 3. 주사위 굴림
    dice_val = random.randint(1, 100)
    final_val = max(1, dice_val + modifier) # 최소 1
    
    # 4. 결과 포맷팅
    # [행동] 판정
    # 🎲 1d100(45) - 10 (부상, 탈진) = 35
    # 보유 패시브: [검술, 야간시야]
    
    header = f"🎲 **판정: {action_desc}**" if action_desc else "🎲 **판정**"
    
    # Critical Detection
    crit_msg = ""
    if dice_val <= 5: 
        crit_msg = " [⚠️ **대실패**]"
    elif dice_val >= 96:
        crit_msg = " [✨ **대성공 가능!**]" # Only if final >= DC (assumed 50)
    
    calc_str = f"**{dice_val}**"
    if modifier != 0:
        sign = "+" if modifier > 0 else ""
        calc_str += f" {sign}{modifier} ({', '.join(mod_details)})"
        
    result_str = f"{header}\n결과: {calc_str} = **{final_val}**{crit_msg}\n(보통 기준: DC 40)"
    
    # 패시브 & 소지품 컨텍스트 (AI/GM 참고용)
    passives = p_data.get("ai_memory", {}).get("passives", [])
    if passives:
        p_names = [p.get("name") if isinstance(p, dict) else str(p) for p in passives]
        result_str += f"\n💡 참고 특성: {', '.join(p_names)}"
    
    inv = p_data.get("inventory", {})
    if inv:
        i_names = [f"{k}({v})" if isinstance(v, int) and v > 1 else str(k) for k, v in inv.items()]
        result_str += f"\n📦 관련 소지품: {', '.join(i_names)}"
        
    return result_str

def get_status_summary(user_data: Dict[str, Any]) -> str:
    """Returns a concise summary of player status for AI analysis."""
    parts = []
    
    # 1. Status Effects
    effects = user_data.get("status_effects", [])
    if effects: parts.append(f"상태: {', '.join(effects)}")
    else: parts.append("상태: 정상")
    
    # 2. Passives (Helper utilized)
    passives = get_passives_for_context(user_data) # Returns "Passives: ..."
    parts.append(passives)
    
    # 3. Inventory (Tools/Weapons)
    inv = user_data.get("inventory", {})
    if inv:
        # Simple list for context
        items = [f"{k}" for k in inv.keys()]
        parts.append(f"소지품: {', '.join(items)}")
        
    return "\n".join(parts)

def add_passive(channel_id: str, user_id: str, name: str, tags: List[str] = None, desc: str = "") -> str:
    """하이브리드 패시브 추가 (태그 시스템 포함)"""
    if tags is None: tags = []
    
    # AI Memory에 저장 (영구적)
    new_passive = {
        "name": name,
        "tags": tags,
        "desc": desc,
        "acquired_at": time.strftime('%Y-%m-%d')
    }
    
    domain_manager.add_to_ai_memory_list(channel_id, user_id, "passives", new_passive)
    
    tag_str = f" [{', '.join(tags)}]" if tags else ""
    return f"🏆 **패시브 획득:** {name}{tag_str}\n_{desc}_"

def get_passives_for_context(user_data: Dict[str, Any]) -> str:
    # Merge explicit user passives and ai_memory passives
    p_list = user_data.get("passives", [])
    ai_p = user_data.get("ai_memory", {}).get("passives", [])
    
    # Dedup by name
    all_passives = {}
    
    for p in p_list:
        if isinstance(p, dict): all_passives[p['name']] = p
        else: all_passives[str(p)] = {"name": str(p), "tags": []}
        
    for p in ai_p:
        if isinstance(p, dict):
            # AI memory usually authoritative for new style
            all_passives[p['name']] = p
        else:
             if str(p) not in all_passives:
                 all_passives[str(p)] = {"name": str(p), "tags": []}
    
    if not all_passives: return "Passives: None"
    
    lines = []
    for p in all_passives.values():
        tags = f"({', '.join(p.get('tags', []))})" if p.get('tags') else ""
        lines.append(f"{p['name']}{tags}")
        
    return f"Passives: {', '.join(lines)}"

def calculate_status_doom_contribution(user_data: Dict[str, Any]) -> Tuple[int, List[str]]:
    effects = user_data.get("status_effects", [])
    total, reasons = 0, []
    for ename in effects:
        info = STATUS_EFFECTS.get(ename, {})
        if info.get("type") == "debuff":
            imp = SEVERITY_DOOM_IMPACT.get(info.get("severity", 1), 0)
            if imp > 0:
                total += imp
                reasons.append(f"💀 {ename}")
    return total, reasons

# =========================================================
# CHRONICLE & EXPORTS (Moved from game_system.py)
# =========================================================

async def call_gemini_api(client, model_id, prompt, sys_instruction=""):
    if not client: return None
    full_prompt = f"{sys_instruction}\n\n{prompt}" if sys_instruction else prompt
    cfg = types.GenerateContentConfig(response_mime_type="application/json", temperature=0.1)
    
    for _ in range(config.MAX_RETRY_COUNT):
        try:
            resp = await client.aio.models.generate_content(
                model=model_id,
                contents=[types.Content(role="user", parts=[types.Part(text=full_prompt)])],
                config=cfg
            )
            if resp and resp.text:
                clean = re.sub(r"```(json)?", "", resp.text).strip().strip("`")
                # Using json.loads for safety
                import json
                try:
                    return json.loads(clean)
                except json.JSONDecodeError:
                    return {"summary": resp.text} # Fallback for non-JSON text
        except Exception as e:
            logging.warning(f"API Error: {e}")
            await asyncio.sleep(config.RETRY_DELAY_SECONDS)
    return None

async def generate_chronicle_from_history(client, model_id, channel_id: str) -> str:
    domain = domain_manager.get_domain(channel_id)
    board = _get_board(channel_id)
    history = domain.get('history', [])
    if not history: return "기록된 역사가 없습니다."
    
    full_text = "\n".join([f"{h['role']}: {h['content']}" for h in history[-config.MAX_HISTORY_FOR_CHRONICLE:]])
    
    # Importing time here to avoid top-level optional import issues if minimal
    import time
    
    res = await call_gemini_api(
        client, model_id, 
        f"Log:\n{full_text}",
        "You are the Chronicler. Summarize the session log into a narrative. Output JSON: {\"title\": \"Title\", \"summary\": \"Content...\"}"
    )
    
    if res and "summary" in res:
        entry = {"title": res.get("title", "기록"), "content": res.get("summary"), "timestamp": time.time()}
        board.setdefault("lore", []).append(entry)
        _save_board(channel_id, board)
        return f"📜 **[연대기 기록됨]** {entry['title']}\n{entry['content'][:100]}..."
    return "연대기 생성 실패"

def export_lore_data(channel_id: str) -> Tuple[str, str]:
    lore = domain_manager.get_lore(channel_id)
    npcs = domain_manager.get_npcs(channel_id)
    
    if not lore and not npcs:
        return None, "⚠️ 내보낼 데이터가 없습니다."
        
    lines = []
    lines.append(f"# 세계관 데이터 내보내기 - {channel_id}")
    lines.append(f"일시: {time.strftime('%Y-%m-%d %H:%M')}\n")
    
    lines.append("## 세계관(LORE)")
    lines.append(lore)
    lines.append("\n## NPC 목록")
    
    for name, data in npcs.items():
        lines.append(f"### {name}")
        lines.append(f"설명: {data.get('desc', '-')}")
        if data.get('appearance'): lines.append(f"외모: {data.get('appearance')}")
        if data.get('personality'): lines.append(f"성격: {data.get('personality')}")
        lines.append("")

    # Session-detected NPCs
    mem = domain_manager.get_session_ai_memory(channel_id)
    npc_summaries = mem.get("npc_summaries", {})
    if npc_summaries:
        lines.append("\n### [세션 감지 NPC - AI Detected]")
        for name, summary in npc_summaries.items():
            if name in npcs: continue 
            lines.append(f"#### {name}")
            lines.append(f"요약: {summary}")
            lines.append("")

    export_content = "\n".join(lines)
    return export_content, f"✅ **데이터 추출 완료** (NPC {len(npcs)}명 + 감지 {len(npc_summaries)}명)"

def get_lore_book(channel_id: str) -> str:
    board = _get_board(channel_id)
    lore = board.get("lore", [])
    if not lore: return "📖 **연대기 없음**"
    msg = "📖 **[연대기 목록]**\n"
    for i, e in enumerate(lore):
        date_str = time.strftime('%Y-%m-%d', time.localtime(e.get('timestamp', 0)))
        msg += f"{i+1}. [{date_str}] {e.get('title', 'Untitled')}\n"
    return msg

# =========================================================
# ABNORMAL ADAPTATION SYSTEM
# =========================================================

def calculate_normality(count: int, base_threshold: int = 10) -> int:
    if count <= 0: return 0
    import math
    normality = min(100, int((math.log(count + 1) / math.log(base_threshold + 1)) * 100))
    return normality

def expose_to_abnormal(user_data: Dict[str, Any], abnormal_type: str) -> Tuple[Dict[str, Any], Optional[str]]:
    exposure = user_data.get("abnormal_exposure", {})
    if abnormal_type not in exposure:
        exposure[abnormal_type] = {"count": 0, "normality": 0}
        
    # Increment
    exposure[abnormal_type]["count"] += 1
    count = exposure[abnormal_type]["count"]
    
    # Calculate
    old_norm = exposure[abnormal_type]["normality"]
    new_norm = calculate_normality(count)
    exposure[abnormal_type]["normality"] = new_norm
    
    user_data["abnormal_exposure"] = exposure
    
    # Stage Change Check
    old_stage = get_normality_stage_info(old_norm)
    new_stage = get_normality_stage_info(new_norm)
    
    msg = None
    if old_stage["stage"] != new_stage["stage"]:
        msg = f"🧠 **[{abnormal_type}]** 심리 적응: {old_stage['name']} → {new_stage['name']}"
    elif count == 1:
        msg = f"🧠 **[{abnormal_type}]** 첫 조우! (적응도 시작)"
        
    return user_data, msg

def get_abnormal_context(user_data: Dict[str, Any]) -> str:
    exposure = user_data.get("abnormal_exposure", {})
    if not exposure: return ""
    
    lines = []
    for k, v in exposure.items():
        norm = v["normality"]
        stage = get_normality_stage_info(norm)
        lines.append(f"- {k}: {norm}% ({stage['reaction_hint']})")
        
    if not lines: return ""
    return "### [Mental Adaptation]\n" + "\n".join(lines) + "\n*Adjust reaction based on adaptation level.*"

# History/Archive Exports
def export_session_history(channel_id: str, incremental: bool = False) -> Tuple[str, str]:
    history = domain_manager.get_history(channel_id)
    if not history:
        return "", "⚠️ 기록된 대화 내역이 없습니다."
    
    start_idx = 0
    mode_text = "전체"
    
    if incremental:
        start_idx = domain_manager.get_last_export_idx(channel_id)
        if start_idx >= len(history):
            return "", "✅ 새로운 대화 내역이 없습니다."
        mode_text = "증분"
        
    target_history = history[start_idx:]
    lines = []
    lines.append(f"# Session History Export ({mode_text}) - {channel_id}")
    lines.append(f"Date: {time.strftime('%Y-%m-%d %H:%M')}")
    lines.append(f"Range: #{start_idx+1} ~ #{len(history)}\n")
    
    for i, h in enumerate(target_history):
        role = h.get('role', 'Unknown')
        content = h.get('content', '')
        lines.append(f"[{role}] {content}")
        lines.append("") # Spacer

    if incremental:
        domain_manager.set_last_export_idx(channel_id, len(history))
        
    return "\n".join(lines), f"✅ **대화 내역 추출 완료** ({mode_text}, {len(target_history)} lines)"

def export_chronicle_book(channel_id: str, incremental: bool = False) -> Tuple[str, str]:
    board = _get_board(channel_id)
    lore_entries = board.get("lore", [])
    
    if not lore_entries:
        return "", "⚠️ 기록된 연대기가 없습니다."

    start_idx = 0
    mode_text = "전체"

    if incremental:
        start_idx = domain_manager.get_last_chronicle_idx(channel_id)
        if start_idx >= len(lore_entries):
             return "", "✅ 새로운 연대기 기록이 없습니다."
        mode_text = "증분"

    target_entries = lore_entries[start_idx:]
    lines = []
    lines.append(f"# Chronicle Export ({mode_text}) - {channel_id}")
    lines.append(f"Date: {time.strftime('%Y-%m-%d %H:%M')}")
    lines.append(f"Range: #{start_idx+1} ~ #{len(lore_entries)}\n")
    
    for entry in target_entries:
        title = entry.get("title", "Untitled")
        date_str = time.strftime('%Y-%m-%d', time.localtime(entry.get('timestamp', 0)))
        content = entry.get("content", "")
        
        lines.append(f"## [{date_str}] {title}")
        lines.append(content)
        lines.append("\n" + "="*30 + "\n")

    if incremental:
        domain_manager.set_last_chronicle_idx(channel_id, len(lore_entries))
        
    return "\n".join(lines), f"✅ **연대기 추출 완료** ({mode_text}, {len(target_entries)} items)"
