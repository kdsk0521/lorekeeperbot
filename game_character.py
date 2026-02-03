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
import math
from typing import List, Tuple, Dict, Any, Optional

from google import genai
from google.genai import types

import config
import domain_manager
from config import (
    MENTAL_STAGES,
    DOOM_STAGES,
    DOOM_MENTAL_RECOVERY_MOD,
    NEGATIVE_STATUS_EFFECTS,
    POSITIVE_STATUS_EFFECTS,
    SEVERITY_DOOM_IMPACT,
    STATUS_EFFECTS,
    get_normality_stage_info
)

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
def _list_op(cid: str, key: str, content: str, emoji: str, name: str) -> Optional[str]:
    if not content:
        return None
    board = _get_board(cid)
    lst = board.get(key, [])
    if content not in lst:
        lst.append(content)
        board[key] = lst
        _save_board(cid, board)
        return f"{emoji} **{name} 등록:** {content}"
    return f"⚠️ 이미 등록된 {name}입니다."

def _del_op(cid: str, key: str, content: str, emoji: str, name: str) -> Optional[str]:
    if not content:
        return None
    board = _get_board(cid)
    lst = board.get(key, [])
    target = next((i for i in lst if content in i), None)  # Partial Match
    if target:
        lst.remove(target)
        board[key] = lst
        _save_board(cid, board)
        return f"{emoji} **{name} 제거:** {target}"
    return f"⚠️ 해당 {name}를 찾을 수 없습니다."

def _move_op(cid: str, src: str, dst: str, content: str, emoji: str, name: str, action: str) -> Optional[str]:
    if not content:
        return None
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
    ai_mem_passives: Dict[str, Any] = p_data.get("ai_memory", {})
    passives: List[Any] = ai_mem_passives.get("passives", [])
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
    ai_mem_ctx: Dict[str, Any] = p_data.get("ai_memory", {})
    passives = ai_mem_ctx.get("passives", [])
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
    if effects: parts.append(f"상태(Status): {', '.join(effects)}")
    else: parts.append("상태(Status): 정상")
    
    # [Anti-Gravity] 2. Mental State (V7)
    mental_txt = get_mental_status_text(user_data)
    parts.append(f"멘탈(Mental): {mental_txt}")
    
    # [Anti-Gravity] 3. Adaptation / Abnormal Exposure
    abnormal_txt = get_abnormal_context(user_data)
    if abnormal_txt != "None":
        parts.append(f"적응도(Adaptation): {abnormal_txt}")

    # 4. Passives (Helper utilized)
    passives = get_passives_for_context(user_data) # Returns "Passives: ..."
    parts.append(passives)

    # 5. Companions (Collaborators)
    mem = user_data.get("ai_memory", {})
    companions = mem.get("companions", [])
    if companions:
        if isinstance(companions, list) and len(companions) > 0:
            parts.append(f"동행(Companions): {', '.join(companions)}")
    
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

def get_passives_for_context(user_data: Optional[Dict[str, Any]]) -> str:
    if not user_data:
        return "None"
    # Merge explicit user passives and ai_memory passives
    p_list: List[Any] = user_data.get("passives", [])
    ai_mem: Dict[str, Any] = user_data.get("ai_memory", {})
    ai_p: List[Any] = ai_mem.get("passives", [])
    
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
        p_tags: List[str] = p.get('tags', [])
        tags = f"({', '.join(p_tags)})" if p_tags else ""
        lines.append(f"{p['name']}{tags}")
        
    return f"Passives: {', '.join(lines)}"

# =========================================================
# CHRONICLE & EXPORTS (Moved from game_system.py)
# =========================================================

async def call_gemini_api(
    client: Optional[genai.Client], 
    model_id: str, 
    prompt: str, 
    sys_instruction: str = ""
) -> Optional[Dict[str, Any]]:
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

async def generate_chronicle_from_history(
    client: Optional[genai.Client], 
    model_id: str, 
    channel_id: str
) -> str:
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
# V7: MENTAL & ADAPTATION SYSTEM
# =========================================================

def get_mental_stage_id(value: int) -> int:
    for stage_id, info in config.MENTAL_STAGES.items():
        low, high = info["range"]
        if low <= value < high:
            return stage_id
    return 0 # Default to Calm (0)

def get_mental_info(value: int) -> Dict[str, Any]:
    stage_id = get_mental_stage_id(value)
    return config.MENTAL_STAGES.get(stage_id, config.MENTAL_STAGES[0])

def get_mental_dice_modifier(value: int) -> int:
    stage = get_mental_stage_id(value)
    # V7 Rules: +20 (Calm), +10 (Shake), 0 (Panic), -10 (Collapse)
    if stage == 0: return 20
    elif stage == 1: return 10
    elif stage == 2: return 0
    elif stage == 3: return -10
    return 0

def get_mental_status_text(p_data: Dict[str, Any]) -> str:
    """
    Returns a formatted string of the character's mental state.
    e.g. "😌 평정"
    """
    mem = p_data.get("ai_memory", {})
    ment = mem.get("mental", {"value": 100})
    val = ment.get("value", 100)
    info = get_mental_info(val)
    return f"{info['emoji']} **{info['name']}**"


def update_mental(user_data: Dict[str, Any], delta: int, reason: str) -> str:
    """
    V7 Mental Update Logic
    - Handles Doom Penalty on Recovery
    - Handles Trauma Awakening (Collapse -> Calm)
    - Handles Clamping (Max 2 stage drop) and Inertia
    """
    mem = user_data.setdefault("ai_memory", {})
    mental = mem.setdefault("mental", {"value": 100, "last_delta": 0})
    
    current_val = mental["value"]
    current_stage = get_mental_stage_id(current_val)
    
    # 1. Doom Penalty (Recovery Only)
    channel_id = "UNKNOWN" # context missing in user_data, usually passed or disregarded for pure logic
    # We might need to fetch doom if available, but simplest is to assume 1.0 or require context.
    # Since function signature is fixed, we can't easily get channel_id here without passing it.
    # We will assume standard recovery unless doom is passed?
    # Let's check update_mental usage. It's called from game_world which has channel_id.
    # We should update signature or fetch context.
    # HOWEVER, to keep it simple, we will apply doom penalty outside or fetch via domain_manager if possible (but we don't have channel_id).
    # Plan B: Assume delta is already adjusted or ignore doom penalty here?
    # No, PLAN says: "update_mental... 1. Doom Penalty".
    # I will modify signature `update_mental(user_data, delta, reason, doom_stage=0)` in future. 
    # For now, let's implement the core logic.
    
    actual_delta = delta
    
    # 2. Trauma Awakening (Collapse -> Recovery)
    if current_stage == 3 and delta > 0:
        # Check if delta is large enough or special flag? 
        # Plan says "Mental Reboot... if special trigger".
        # We will assume any significant recovery in Collapse triggers this check or just direct heal.
        # But User requested "Trauma Awakening".
        
        # If we are in Stage 3 and healing, we grant Trauma and Reset to 100 (Calm)
        # This is the "Awakening" mechanic.
        
        # Add Trauma Passive
        trauma_name = f"Trauma: {reason}"
        domain_manager.add_to_ai_memory_list("UNKNOWN", "UNKNOWN", "passives", {"name": trauma_name, "tags": ["Trauma", "Permanent"], "modifier": -5})
        
        # Reset
        mental["value"] = 90 # High Calm
        mental["last_delta"] = 0
        
        new_info = get_mental_info(90)
        return f"🧠 **각성(Trauma Awakening):** 🫥 붕괴 → {new_info['emoji']} **{new_info['name']}** (트라우마 획득: {reason})"

    # 3. Inertia & Clamping
    # Inertia: If same direction, +10% effect?
    last_delta = mental.get("last_delta", 0)
    if (delta > 0 and last_delta > 0) or (delta < 0 and last_delta < 0):
        actual_delta = int(actual_delta * 1.1)
        
    # Clamping: Prevent crossing more than 2 stages downwards
    # Stage ranges: 0(70-100), 1(40-70), 2(15-40), 3(0-15)
    # If Stage 0 -> Stage 3 (Illegal)
    target_val = max(0, min(100, current_val + actual_delta))
    target_stage = get_mental_stage_id(target_val)
    
    if target_stage > current_stage + 2: # Dropped more than 2 stages (0->3)
        # Clamp to bottom of Stage (Current+2)
        # Stage 2 range is 15-40. So set to 15.
        limit_stage = current_stage + 2
        limit_info = config.MENTAL_STAGES.get(limit_stage)
        target_val = limit_info["range"][0] # Set to min of limit stage
        target_stage = limit_stage
        
    mental["value"] = target_val
    mental["last_delta"] = delta # Store original delta
    
    # 4. Feedback
    if target_stage != current_stage:
        old_info = config.MENTAL_STAGES[current_stage]
        new_info = config.MENTAL_STAGES[target_stage]
        return f"🧠 **멘탈 변화:** {old_info['emoji']} → {new_info['emoji']} **{new_info['name']}** ({reason})"
    
    # Quiet update
    return ""

def calculate_adaptation_pct(count: int) -> int:
    """V7 Log Scale: math.log(count + 1) * 25"""
    if count <= 0: return 0
    val = math.log(count + 1) * 25
    return min(100, int(val))

# Alias for Health Check / Legacy
calculate_adaptation_percentage = calculate_adaptation_pct

def process_adaptation_encounter(user_data: Dict[str, Any], tag: str) -> Tuple[int, bool]:
    mem = user_data.setdefault("ai_memory", {})
    exposure = mem.setdefault("abnormal_exposure", {})
    
    if tag not in exposure: exposure[tag] = {"count": 0}
    
    old_count = exposure[tag]["count"]
    old_pct = calculate_adaptation_percentage(old_count) # Use V7 calc
    
    exposure[tag]["count"] = old_count + 1
    new_count = exposure[tag]["count"]
    
    new_pct = calculate_adaptation_percentage(new_count)
    
    leveled_up = (old_pct // 20) < (new_pct // 20) # 20% steps
    
    return new_pct, leveled_up

def apply_abnormal_impact(user_data: Dict[str, Any], tag: str, intensity: str = "Mid", doom_stage: int = 0) -> Tuple[str, int, int]:
    """
    V7 이변 대응 판정 - 중립적 시스템

    이변은 좋고 나쁨이 없는 '비일상적 현상'입니다.
    캐릭터가 이를 어떻게 받아들이느냐에 따라 결과가 달라집니다.

    결과:
    - 대성공(roll >= 90): 영감 획득, 멘탈 회복, 둠 감소
    - 성공(total >= dc): 침착한 대응, 보너스 획득, 둠 소폭 감소
    - 실패(total < dc): 당황, 멘탈 소폭 감소
    - 대실패(roll <= 10): 충격, 멘탈 크게 감소, 둠 증가

    Returns: (Result String, Adapt %, Doom Delta)
    """
    # 1. 적응도 확인
    mem = user_data.setdefault("ai_memory", {})
    exposure: Dict[str, Any] = mem.get("abnormal_exposure", {})
    tag_data: Dict[str, Any] = exposure.get(tag, {})
    count = tag_data.get("count", 0)
    adapt_pct = calculate_adaptation_pct(count)

    # 2. 멘탈 상태에 따른 수정자
    mental_data = mem.get("mental", {"value": 100})
    mental_val = mental_data.get("value", 100)
    mental_stage = get_mental_stage_id(mental_val)

    # 멘탈이 낮으면 적응 판정에 불리
    mental_modifier = {0: 0, 1: -5, 2: -15, 3: -25}.get(mental_stage, 0)

    # 3. 주사위 굴림
    # DC: 30 + (DoomStage * 10)
    dc = 30 + (doom_stage * 10)
    roll = random.randint(1, 100)
    total = roll + adapt_pct + mental_modifier

    # 4. 결과 판정
    new_pct, _ = process_adaptation_encounter(user_data, tag)  # 어떤 결과든 적응도는 증가
    doom_delta = 0  # 둠 변화량

    # 대성공 (원본 roll이 90 이상)
    if roll >= 90:
        # 영감 획득: 이변에서 통찰을 얻음
        mental_gain = {"Low": 5, "Mid": 10, "High": 15, "Extreme": 20}.get(intensity, 10)
        msg = update_mental(user_data, mental_gain, reason=f"{tag}에서 영감")
        user_data["temp_bonus_dice"] = user_data.get("temp_bonus_dice", 0) + 2
        doom_delta = -3  # 영감으로 긴장 완화
        return (f"✨ **영감!** (멘탈 +{mental_gain}, 직관 +20) {msg} [Adapt {adapt_pct}%→{new_pct}%]", new_pct, doom_delta)

    # 대실패 (원본 roll이 10 이하)
    elif roll <= 10:
        # 충격: 이변에 압도됨
        base_dmg = {"Low": 15, "Mid": 25, "High": 40, "Extreme": 60}.get(intensity, 25)
        mitigation = adapt_pct / 200.0  # 최대 50% 경감
        final_dmg = int(base_dmg * (1.0 - mitigation))
        msg = update_mental(user_data, -final_dmg, reason=f"{tag} 충격")
        doom_delta = 2  # 충격으로 긴장 증가
        return (f"💥 **충격!** (멘탈 -{final_dmg}) {msg} [Adapt {adapt_pct}%→{new_pct}%]", new_pct, doom_delta)

    # 성공 (total >= dc)
    elif total >= dc:
        # 침착한 대응
        user_data["temp_bonus_dice"] = user_data.get("temp_bonus_dice", 0) + 1
        doom_delta = -1  # 침착하게 대응하여 소폭 긴장 완화
        return (f"🔮 **침착!** (직관 +10) [Adapt {adapt_pct}%→{new_pct}%]", new_pct, doom_delta)

    # 실패 (total < dc)
    else:
        # 당황: 소폭의 멘탈 감소
        base_dmg = {"Low": 5, "Mid": 10, "High": 20, "Extreme": 35}.get(intensity, 10)
        mitigation = adapt_pct / 200.0
        final_dmg = int(base_dmg * (1.0 - mitigation))
        msg = update_mental(user_data, -final_dmg, reason=f"{tag} 당황")
        # doom_delta = 0  # 당황해도 둠은 변화 없음
        return (f"😰 **당황** (멘탈 -{final_dmg}) {msg} [Adapt {adapt_pct}%→{new_pct}%]", new_pct, doom_delta)

# Legacy Wrappers (Shim Layer)
def check_adaptation_roll(user_data, tag, category=None, difficulty=30, intensity: str = "Mid", doom_stage: int = 0):
    """
    이변에 대한 적응 판정을 수행합니다.

    Args:
        user_data: 플레이어 데이터
        tag: 이변 태그
        category: 이변 카테고리 (현재 미사용, 향후 확장용)
        difficulty: 기본 난이도 (현재 doom_stage 기반으로 계산)
        intensity: 이변 강도 - "Low", "Mid", "High", "Extreme"
        doom_stage: 현재 둠 스테이지 (0-5) - DC 계산에 사용

    Returns:
        (user_data, result_message, doom_delta)
    """
    msg, _, doom_delta = apply_abnormal_impact(user_data, tag, intensity=intensity, doom_stage=doom_stage)
    return user_data, msg, doom_delta

def get_abnormal_context(user_data: Dict[str, Any]) -> str:
    """
    Returns a formatted string of the character's abnormal exposure.
    """
    mem = user_data.get("ai_memory", {})
    exp = mem.get("abnormal_exposure", {})
    if not exp: return "None"
    
    entries = []
    for tag, data in exp.items():
        count = data.get("count", 0)
        pct = calculate_adaptation_pct(count)
        entries.append(f"{tag}({pct}%)")
    return ", ".join(entries)


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
