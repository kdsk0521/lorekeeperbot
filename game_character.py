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

# Memo Operations (Integrated into Notebook)
def add_memo(channel_id: str, content: str) -> str:
    current_nb = get_notebook_text(channel_id)
    # Check if duplicate line exists to avoid clutter
    if f"- {content}" in current_nb:
        return f"⚠️ 이미 노트북에 있는 내용입니다: {content}"
        
    # Append to [메모] section if possible, else append to end
    new_nb = ""
    if "— [메모] —" in current_nb:
        parts = current_nb.split("— [메모] —")
        # Ensure we append to the second part (the memo section)
        new_nb = parts[0] + "— [메모] —" + parts[1] + f"\n- {content}"
    else:
        new_nb = current_nb + f"\n\n— [메모] —\n- {content}"
        
    update_notebook_text(channel_id, new_nb)
    return f"📝 **노트북 기록:** {content}"

def remove_memo(channel_id: str, content: str) -> str:
    current_nb = get_notebook_text(channel_id)
    lines = current_nb.splitlines()
    new_lines = []
    removed = False
    
    for line in lines:
        if content in line and line.strip().startswith("-"):
            removed = True
            continue # Skip this line
        new_lines.append(line)
        
    if removed:
        update_notebook_text(channel_id, "\n".join(new_lines))
        return f"🗑️ **노트북 삭제:** {content}"
    return f"⚠️ '{content}' 내용을 찾을 수 없습니다."

def edit_memo(channel_id: str, old_content: str, new_content: str) -> str:
    current_nb = get_notebook_text(channel_id)
    lines = current_nb.splitlines()
    new_lines = []
    edited = False
    
    for line in lines:
        if old_content in line:
            # Replace logic: If line was a bullet item, keep bullet
            if line.strip().startswith("-"):
                 new_lines.append(f"- {new_content}")
            else:
                 # Just replace the text part if it wasn't a bullet (unlikely for memos but possible for free text)
                 new_lines.append(line.replace(old_content, new_content))
            edited = True
        else:
            new_lines.append(line)
            
    if edited:
         update_notebook_text(channel_id, "\n".join(new_lines))
         return f"📝 **노트북 수정:** {old_content} -> {new_content}"
    return f"⚠️ '{old_content}' 내용을 찾을 수 없습니다."

def resolve_memo_auto(channel_id: str, content: str) -> str:
    # Just remove for now, archiving text is complex
    return remove_memo(channel_id, content) + " (자동 해결)"

# Alias for V6
def expose_to_abnormal(user_data: Dict[str, Any], trigger: str, category: str = None) -> Tuple[Dict[str, Any], str]:
    # Wraps check_adaptation_roll with default difficulty
    return check_adaptation_roll(user_data, trigger, category=category, difficulty=30)


# Notebook System (New in V5.1)
def get_notebook_text(channel_id: str) -> str:
    return domain_manager.get_notebook(channel_id)

def update_notebook_text(channel_id: str, new_text: str) -> None:
    domain_manager.update_notebook(channel_id, new_text)

def get_active_quests(channel_id: str) -> List[str]:
    return _get_board(channel_id).get("active", [])


def get_active_quests_text(channel_id: str) -> str:
    board = _get_board(channel_id)
    active = board.get("active", [])
    if not active: return "📭 현재 진행 중인 퀘스트가 없습니다."
    return "🔥 **진행 중인 퀘스트:**\n" + "\n".join([f"{i+1}. {q}" for i, q in enumerate(active)])

def get_status_message(channel_id: str) -> str:
    quests = get_active_quests_text(channel_id)
    notebook = get_notebook_text(channel_id)
    return f"{quests}\n\n{notebook}"

def get_objective_context(channel_id: str) -> str:
    """AI를 위한 가독성 중심의 세계 상태 정보 (퀘스트 + 노트북)"""
    active = get_active_quests(channel_id)
    notebook = get_notebook_text(channel_id)
    
    txt = "### [진행 목표 (QUESTS)]\n"
    if active:
        txt += "\n".join([f"- {q}" for q in active]) + "\n"
    else:
        txt += "None\n"
        
    txt += f"\n### [노트북 (INVENTORY & MEMOS)]\n{notebook}"
    return txt.strip()

# =========================================================
# CHARACTER STATUS & INVENTORY
# =========================================================


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

    # [Anti-Gravity Feature] Hidden Passive Modifiers
    passives = p_data.get("ai_memory", {}).get("passives", [])
    passive_bonus = 0
    for p in passives:
        if isinstance(p, dict):
            # [NEW] Skip Titles
            if "Title" in p.get("tags", []):
                continue

            val = p.get("modifier", 0)
            if val != 0:
                passive_bonus += val
                mod_details.append(f"{p.get('name')}(+{val})")
    
    if passive_bonus != 0:
        modifier += passive_bonus

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
    
    # Critical Detection & Doom Trigger
    crit_msg = ""
    doom_fb = ""
    import game_world # Local import to avoid circular dependency
    
    if dice_val <= 5: 
        crit_msg = " [⚠️ **대실패**]"
        doom_fb = game_world.change_doom(channel_id, 5) # +5 Doom (Accident)
    elif dice_val >= 96:
        crit_msg = " [✨ **대성공!**]"
        doom_fb = game_world.change_doom(channel_id, -5) # -5 Doom (Heroism)

    if doom_fb:
        crit_msg += f"\n{doom_fb}"
    
    calc_str = f"**{dice_val}**"
    if modifier != 0:
        sign = "+" if modifier > 0 else ""
        calc_str += f" {sign}{modifier} ({', '.join(mod_details)})"
        
    result_str = f"{header}\n결과: {calc_str} = **{final_val}**{crit_msg}\n(보통 기준: DC 40)"
    
    # 패시브 & 노트북 컨텍스트 (AI/GM 참고용)
    passives = p_data.get("ai_memory", {}).get("passives", [])
    if passives:
        p_names = [p.get("name") if isinstance(p, dict) else str(p) for p in passives]
        result_str += f"\n💡 참고 특성: {', '.join(p_names)}"
    
    notebook_ctx = domain_manager.get_notebook(channel_id)
    if notebook_ctx and "(비어 있음)" not in notebook_ctx:
        # Show first 5 lines of notebook as context preview
        nb_preview = "\n".join(notebook_ctx.splitlines()[:5]) 
        result_str += f"\n📔 **노트북 참고:**\n{nb_preview}"
        
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
    
    return "\n".join(parts)

def get_recent_relationships(user_data: Dict[str, Any], limit: int = 5) -> str:
    """Returns the last N updated relationships from AI memory."""
    mem = user_data.get("ai_memory", {})
    rels = mem.get("relationships", [])
    
    if not rels:
        return ""
        
    items = []
    if isinstance(rels, list):
        # Allow string or dict items
        raw_items = rels[-limit:]
        raw_items.reverse() # Newest first
        for r in raw_items:
            if isinstance(r, dict):
                # {name: ..., desc: ...} or similar
                items.append(f"{r.get('name', '???')}: {r.get('desc', '')}")
            else:
                items.append(str(r))
    elif isinstance(rels, dict):
        # Dictionary format (Name -> Desc)
        # Using list(dict) preserves insertion order in Python 3.7+
        keys = list(rels.keys())[-limit:]
        keys.reverse()
        for k in keys:
            items.append(f"{k}: {rels[k]}")
    else:
        # String fallback
        return str(rels)[:100] + "..."
        
    if not items: return ""
    return ", ".join(items)

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

def calculate_status_doom_contribution(user_data: Dict[str, Any]) -> Tuple[int, List[str]]:
    """
    Calculate Doom increase based on active Negative Status Effects (V2/V5 Rule).
    Returns (doom_increase_amount, list_of_reasons).
    """
    effects = user_data.get("status_effects", [])
    doom_val = 0
    reasons = []
    
    # Severity Table (Hardcoded or Config dependent)
    # 1 (Light): +1
    # 2 (Medium): +3
    # 3 (Critical): +5
    
    for eff in effects:
        # Check config first
        if hasattr(config, "NEGATIVE_STATUS_EFFECTS") and eff in config.NEGATIVE_STATUS_EFFECTS:
            sev = config.NEGATIVE_STATUS_EFFECTS[eff]
            add = 0
            if sev == 1: add = 1
            elif sev == 2: add = 3
            elif sev >= 3: add = 5
            
            if add > 0:
                doom_val += add
                reasons.append(f"{eff}(+{add})")
                
    return doom_val, reasons

# =========================================================
# =========================================================
# MENTAL & ADAPTATION SYSTEM (V6)
# =========================================================

MENTAL_STAGES = {
    0: {"name": "평정", "emoji": "🟢", "desc": "안정된 상태입니다."},
    1: {"name": "동요", "emoji": "🟡", "desc": "손이 떨리고 식은땀이 흐릅니다."},
    2: {"name": "공황", "emoji": "🔴", "desc": "이성적인 판단이 불가능합니다. (지능/관찰 -20)"},
    3: {"name": "붕괴", "emoji": "💔", "desc": "정신이 부서졌습니다. (영구 트라우마 위험)"}
}

def calculate_adaptation_percentage(count: int) -> int:
    """
    Logarithmic growth for adaptation percentage.
    Formula: (log(count + 1) / log(11)) * 100
    - 0 count -> 0%
    - 1 count -> 20%
    - 3 count -> 50%
    - 10 count -> 100%
    """
    if count <= 0: return 0
    import math
    base = 11
    val = math.log(count + 1) / math.log(base)
    return min(100, int(val * 100))

def check_adaptation_roll(user_data: Dict[str, Any], tag: str, category: str = None, difficulty: int = 30) -> Tuple[Dict[str, Any], str]:
    """
    Performs an Adaptation Check against a generic Anomaly Tag.
    Formula: 1d100 + Adaptation% >= Difficulty
    
    [V6.1 Hybrid Strategy]
    1. Check Category (LLM provided)
    2. Check Keyword Match (Search existing keys in new tag name)
    3. Fallback to cleaned tag name
    """
    import random
    import re
    
    # [1] Sanitization
    def _clean(s: str):
        if not s: return ""
        # Remove brackets, parens, and leading articles
        s = s.replace("[", "").replace("]", "").strip()
        s = re.sub(r'\(.*?\)', '', s).strip()
        s = re.sub(r'^(the|a|an)\s+', '', s, flags=re.IGNORECASE).strip()
        return s

    clean_tag = _clean(tag)
    clean_cat = _clean(category) if category else None
    
    exposure = user_data.setdefault("abnormal_exposure", {})
    
    # [2] Match Resolution
    target_key = None
    
    # Path A: LLM Category provided
    if clean_cat and clean_cat in exposure:
        target_key = clean_cat
    # Path B: Keyword Match (Method 3)
    elif not target_key:
        # Sort existing keys by length (desc) to find most specific match first
        existing_keys = sorted(exposure.keys(), key=len, reverse=True)
        for k in existing_keys:
            if k.lower() in clean_tag.lower():
                target_key = k
                break
                
    # Path C: LLM Category is new
    if not target_key and clean_cat:
        target_key = clean_cat
        
    # Path D: Use full cleaned name
    if not target_key:
        target_key = clean_tag if clean_tag else "Unknown"
    
    # 1. Get current adaptation
    tag_data = exposure.get(target_key, {"count": 0})
    adapt_pct = calculate_adaptation_percentage(tag_data.get("count", 0))
    
    # 2. Roll
    dice = random.randint(1, 100)
    total = dice + adapt_pct
    
    current_mental = user_data.get("mental_stage", 0)
    mental_info = MENTAL_STAGES.get(current_mental, MENTAL_STAGES[0])
    
    display_tag = f"[{tag}]"
    if target_key != tag and target_key != clean_tag:
        display_tag = f"[{tag} (속성: {target_key})]"

    msg = f"🧠 **{display_tag} 적응 판정** (난이도 {difficulty})\n"
    msg += f"`1d100({dice}) + 적응도({adapt_pct}%) = {total}`"
    
    if total >= difficulty:
        # Success: Gain XP (Count +2 for faster mastery on success)
        count_inc = 2
        msg += f" ▶ **성공!** (익숙해집니다)\n"
    else:
        # Fail logic
        count_inc = 1
        
        # Buffer: difficulty 30 is base.
        # If Stage 0 -> 1 (Always happen on fail)
        # If Stage 1 -> 2 (Only if fail by margin > 10?)
        # For now, keeping it simple: Just lower the CALLER's default difficulty.
        
        new_mental = min(3, current_mental + 1)

        
        # Check Break
        if current_mental == 3 and new_mental == 3:
             msg += f" ▶ **실패!** 💔 이미 정신이 붕괴되었습니다...\n"
        else:
            user_data["mental_stage"] = new_mental
            new_info = MENTAL_STAGES.get(new_mental)
            msg += f" ▶ **실패!** 멘탈 악화: {mental_info['emoji']} → {new_info['emoji']} **{new_info['name']}**\n"
            
    # Update Adaptation Count
    old_count = tag_data.get("count", 0)
    new_count = old_count + count_inc
    exposure[target_key] = {"count": new_count}
    user_data["abnormal_exposure"] = exposure
    
    # [V6.2] Item 6: Adaptation Mastery (100% reached for the first time)
    is_mastery = (old_count < 10 and new_count >= 10)
    if is_mastery:
        msg += " | 🌟 **마스터리 달성!** (위기 수치 감소)"
        # Note: Actual Doom reduction will be triggered by this keyword in main.py or handled here
        # Since p_data doesn't have channel_id, we use the signal in 'msg' which main.py can detect.
    
    # Report growth
    new_pct = calculate_adaptation_percentage(new_count)
    if new_pct > adapt_pct:
        msg += f"📈 **적응도 상승:** {adapt_pct}% → {new_pct}%"
        
    return user_data, msg

def get_mental_status_text(user_data: Dict[str, Any]) -> str:
    stage = user_data.get("mental_stage", 0)
    info = MENTAL_STAGES.get(stage, MENTAL_STAGES[0])
    return f"{info['emoji']} {info['name']}"

def get_abnormal_context(user_data: Dict[str, Any]) -> str:
    exposure = user_data.get("abnormal_exposure", {})
    if not exposure: return ""
    
    lines = []
    for k, v in exposure.items():
        # [V6.1 Fix] Use 'count' to derive percentage, ensuring consistency with dice rules
        count = v.get("count", 0)
        norm_pct = calculate_adaptation_percentage(count)
        stage = get_normality_stage_info(norm_pct)
        lines.append(f"- {k}: {norm_pct}% ({stage['reaction_hint']})")
        
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
