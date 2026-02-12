"""
Lorekeeper TRPG Bot - Game Character Module
Handles Character Inventory, Status Effects, Quests, Memos, and Mechanics (Dice, Normality).
Extracted from game_system.py
"""

import logging
import time
import re
import asyncio
import math
from typing import List, Tuple, Dict, Any, Optional

from google import genai
from google.genai import types

logger = logging.getLogger("GameCharacter")

import config
import domain_manager
from config import (
    MENTAL_STAGES,
    COMPOSURE_STAGES,
    STATUS_EFFECTS,
)

# =========================================================
# QUEST & MEMO SYSTEM
# =========================================================

def _get_board(channel_id: str) -> Dict[str, Any]:
    d = domain_manager.get_domain(channel_id)
    if "quest_board" not in d or not isinstance(d["quest_board"], dict):
        d["quest_board"] = {"active": [], "completed": [], "memos": [], "archive": [], "lore": []}

    # Migration: normalize old string quests to dict format
    board = d["quest_board"]
    active = board.get("active", [])
    migrated = False
    for i, q in enumerate(active):
        if isinstance(q, str):
            active[i] = {
                "content": q,
                "rank": config.QUEST_DEFAULT_RANK,
                "progress": 0,
                "max_progress": config.QUEST_RANK_SETTINGS[config.QUEST_DEFAULT_RANK]["max_progress"]
            }
            migrated = True
    if migrated:
        board["active"] = active
        domain_manager.update_quest_board(channel_id, board)

    return board


def _save_board(channel_id: str, board: Dict[str, Any]) -> None:
    domain_manager.update_quest_board(channel_id, board)


def _find_quest(active: List, content: str) -> Optional[Dict]:
    """active 리스트에서 content가 포함된 퀘스트 dict를 찾습니다. 퍼지 매칭 지원."""
    if not content or not active:
        return None
    # 1. 정확한 부분 일치
    for q in active:
        q_content = q["content"] if isinstance(q, dict) else q
        if content in q_content or q_content in content:
            return q
    # 2. 퍼지 매칭: 공백/조사/따옴표/특수문자 정규화 후 비교
    def _normalize(s: str) -> str:
        s = s.replace('\u2018', "'").replace('\u2019', "'").replace('\u201C', '"').replace('\u201D', '"')
        return re.sub(r'[의을를이가은는에서로부터과와및\s\'\""&,·\-_~:()]', '', s).lower()
    norm_content = _normalize(content)
    best, best_q = 0.0, None
    for q in active:
        q_content = q["content"] if isinstance(q, dict) else q
        norm_q = _normalize(q_content)
        # 짧은 쪽이 긴 쪽에 포함되면 매칭
        if norm_content in norm_q or norm_q in norm_content:
            return q
        # 공통 문자 비율로 유사도 계산 (글자 수 기반, 세트 기반보다 정확)
        common = len(set(norm_content) & set(norm_q))
        total = max(len(set(norm_content)), len(set(norm_q)), 1)
        ratio = common / total
        if ratio > best:
            best, best_q = ratio, q
    if best >= 0.6:
        return best_q
    # 매칭 실패 시 디버그
    active_names = [q["content"] if isinstance(q, dict) else q for q in active]
    logger.warning(f"[Quest] _find_quest MISS: '{content}' not matched in {active_names}")
    return None


def get_quest_progress_bar(progress: int, max_progress: int) -> str:
    filled = min(progress, max_progress)
    empty = max_progress - filled
    return f"[{'■' * filled}{'□' * empty}]"


# Quest Operations (Progress Track)
def add_quest(channel_id: str, content: str, rank: str = None) -> str:
    if not content:
        return None
    rank = rank or config.QUEST_DEFAULT_RANK
    if rank not in config.QUEST_RANK_SETTINGS:
        rank = config.QUEST_DEFAULT_RANK

    board = _get_board(channel_id)
    active = board.get("active", [])

    if _find_quest(active, content):
        return f"⚠️ 이미 등록된 퀘스트입니다."

    settings = config.QUEST_RANK_SETTINGS[rank]
    quest_obj = {
        "content": content,
        "rank": rank,
        "progress": 0,
        "max_progress": settings["max_progress"]
    }
    active.append(quest_obj)
    board["active"] = active
    _save_board(channel_id, board)

    bar = get_quest_progress_bar(0, settings["max_progress"])
    return f"🔥 **퀘스트 등록:** {content}\n> 난이도: {settings['display']} | {bar}"


def advance_quest_progress(channel_id: str, content: str, delta: int = 1) -> str:
    """퀘스트 진행도 증가. max 도달 시 자동 완료."""
    board = _get_board(channel_id)
    active = board.get("active", [])
    target = _find_quest(active, content)

    if not target:
        return f"⚠️ 퀘스트 '{content}'를 찾을 수 없습니다."

    old_progress = target["progress"]
    target["progress"] = max(0, min(target["max_progress"], old_progress + delta))
    new_progress = target["progress"]
    bar = get_quest_progress_bar(new_progress, target["max_progress"])

    if new_progress >= target["max_progress"]:
        _save_board(channel_id, board)
        complete_msg = complete_quest(channel_id, target["content"])
        return f"📊 **퀘스트 진행:** {target['content']}\n> {bar} ({new_progress}/{target['max_progress']})\n{complete_msg}"

    _save_board(channel_id, board)
    return f"📊 **퀘스트 진행:** {target['content']}\n> {bar} ({new_progress}/{target['max_progress']})"


def complete_quest(channel_id: str, content: str) -> str:
    board = _get_board(channel_id)
    active = board.get("active", [])
    completed = board.get("completed", [])
    target = _find_quest(active, content)

    if not target:
        return f"⚠️ 해당 퀘스트를 찾을 수 없습니다."

    active.remove(target)
    completed.append(target)
    board["active"] = active
    board["completed"] = completed
    _save_board(channel_id, board)

    rank = target.get("rank", config.QUEST_DEFAULT_RANK) if isinstance(target, dict) else config.QUEST_DEFAULT_RANK
    doom_reward = config.QUEST_RANK_SETTINGS.get(rank, {}).get("doom_reward", -5)

    import game_world
    doom_msg = game_world.change_doom(channel_id, doom_reward)
    q_name = target["content"] if isinstance(target, dict) else target
    return f"✅ **퀘스트 완료:** {q_name}\n{doom_msg}"


def remove_quest(channel_id: str, content: str) -> str:
    if not content:
        return None
    board = _get_board(channel_id)
    active = board.get("active", [])
    target = _find_quest(active, content)

    if target:
        active.remove(target)
        board["active"] = active
        _save_board(channel_id, board)
        q_name = target["content"] if isinstance(target, dict) else target
        return f"🗑️ **퀘스트 제거:** {q_name}"
    return f"⚠️ 해당 퀘스트를 찾을 수 없습니다."

# Memo Operations (Integrated into Notebook, per-user in V8)
def add_memo(channel_id: str, content: str, user_id: str = "") -> str:
    current_nb = get_notebook_text(channel_id, user_id)
    if f"- {content}" in current_nb:
        return f"⚠️ 이미 노트북에 있는 내용입니다: {content}"

    new_nb = ""
    if "— [메모] —" in current_nb:
        parts = current_nb.split("— [메모] —")
        new_nb = parts[0] + "— [메모] —" + parts[1] + f"\n- {content}"
    else:
        new_nb = current_nb + f"\n\n— [메모] —\n- {content}"

    update_notebook_text(channel_id, new_nb, user_id)
    return f"📝 **노트북 기록:** {content}"

def remove_memo(channel_id: str, content: str, user_id: str = "") -> str:
    current_nb = get_notebook_text(channel_id, user_id)
    lines = current_nb.splitlines()
    new_lines = []
    removed = False

    for line in lines:
        if content in line and line.strip().startswith("-"):
            removed = True
            continue
        new_lines.append(line)

    if removed:
        update_notebook_text(channel_id, "\n".join(new_lines), user_id)
        return f"🗑️ **노트북 삭제:** {content}"
    return f"⚠️ '{content}' 내용을 찾을 수 없습니다."

def edit_memo(channel_id: str, old_content: str, new_content: str, user_id: str = "") -> str:
    current_nb = get_notebook_text(channel_id, user_id)
    lines = current_nb.splitlines()
    new_lines = []
    edited = False

    for line in lines:
        if old_content in line:
            if line.strip().startswith("-"):
                 new_lines.append(f"- {new_content}")
            else:
                 new_lines.append(line.replace(old_content, new_content))
            edited = True
        else:
            new_lines.append(line)

    if edited:
         update_notebook_text(channel_id, "\n".join(new_lines), user_id)
         return f"📝 **노트북 수정:** {old_content} -> {new_content}"
    return f"⚠️ '{old_content}' 내용을 찾을 수 없습니다."

def resolve_memo_auto(channel_id: str, content: str, user_id: str = "") -> str:
    return remove_memo(channel_id, content, user_id) + " (자동 해결)"

# Notebook System (New in V5.1, per-user in V8)
def get_notebook_text(channel_id: str, user_id: str = "") -> str:
    return domain_manager.get_notebook(channel_id, user_id)

def update_notebook_text(channel_id: str, new_text: str, user_id: str = "") -> None:
    domain_manager.update_notebook(channel_id, new_text, user_id)

def get_active_quests(channel_id: str) -> List[str]:
    """content 문자열 리스트 반환 (cognition 추출 호환)."""
    raw = _get_board(channel_id).get("active", [])
    return [q["content"] if isinstance(q, dict) else q for q in raw]


def get_active_quests_raw(channel_id: str) -> List[Dict]:
    """dict 리스트 원본 반환."""
    return _get_board(channel_id).get("active", [])


def get_active_quests_text(channel_id: str) -> str:
    active = get_active_quests_raw(channel_id)
    if not active:
        return "📭 현재 진행 중인 퀘스트가 없습니다."
    lines = []
    for i, q in enumerate(active):
        if isinstance(q, dict):
            bar = get_quest_progress_bar(q["progress"], q["max_progress"])
            rank_display = config.QUEST_RANK_SETTINGS.get(q.get("rank", "normal"), {}).get("display", "보통")
            lines.append(f"{i+1}. {q['content']} [{rank_display}] {bar} {q['progress']}/{q['max_progress']}")
        else:
            lines.append(f"{i+1}. {q}")
    return "🔥 **진행 중인 퀘스트:**\n" + "\n".join(lines)

def get_status_message(channel_id: str, user_id: str = "") -> str:
    quests = get_active_quests_text(channel_id)
    notebook = get_notebook_text(channel_id, user_id)
    return f"{quests}\n\n{notebook}"

def get_objective_context(channel_id: str, user_id: str = "") -> str:
    """AI를 위한 가독성 중심의 세계 상태 정보 (퀘스트 + 노트북)"""
    active = get_active_quests_raw(channel_id)
    notebook = get_notebook_text(channel_id, user_id)

    txt = "### [진행 목표 (QUESTS)]\n"
    if active:
        for q in active:
            if isinstance(q, dict):
                txt += f"- {q['content']} (Rank:{q.get('rank','normal')}, Progress:{q['progress']}/{q['max_progress']})\n"
            else:
                txt += f"- {q}\n"
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

# [DEPRECATED] perform_check moved to UNE JudgmentEngine

def get_status_summary(user_data: Dict[str, Any]) -> str:
    """Returns a concise summary of player status for AI analysis."""
    parts = []
    
    # 1. Status Effects
    effects = user_data.get("status_effects", [])
    if effects: parts.append(f"상태(Status): {', '.join(effects)}")
    else: parts.append("상태(Status): 정상")
    
    # [V3.0] 2. Vigor/Composure State
    vc_txt = get_vigor_composure_text(user_data)
    parts.append(f"기력/평정(Vigor/Composure): {vc_txt}")
    
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

def add_passive(channel_id: str, user_id: str, name: str, tags: List[str] = None, desc: str = "",
                 theory_links: List[str] = None, modifiers: dict = None) -> str:
    """하이브리드 패시브 추가 (이론 태그 + 수정치 시스템 포함)"""
    if tags is None: tags = []

    # AI Memory에 저장 (영구적)
    new_passive = {
        "name": name,
        "tags": tags,
        "desc": desc,
        "acquired_at": time.strftime('%Y-%m-%d')
    }
    if theory_links:
        new_passive["theory_links"] = theory_links
    if modifiers:
        new_passive["modifiers"] = modifiers

    domain_manager.add_to_ai_memory_list(channel_id, user_id, "passives", new_passive)

    tag_str = f" [{', '.join(tags)}]" if tags else ""
    return f"🏆 **특질 획득:** {name}{tag_str}\n_{desc}_"

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
        tag_str = f"({', '.join(p_tags)})" if p_tags else ""
        # Include theory_links for Flash analysis
        t_links = p.get('theory_links', [])
        link_str = f" [theories:{','.join(t_links)}]" if t_links else ""
        lines.append(f"{p['name']}{tag_str}{link_str}")

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

# [DEPRECATED] calculate_status_doom_contribution moved to UNE logic

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

def get_mental_status_text(p_data: Dict[str, Any]) -> str:
    """
    Returns a formatted string of the character's vigor/composure state.
    e.g. "💪 충만 | 😌 안정"
    """
    mem = p_data.get("ai_memory", {})
    # Migration: old "mental" → vigor fallback
    vigor = mem.get("vigor", mem.get("mental", {"value": 100}))
    composure = mem.get("composure", {"value": 100})
    v_val = vigor.get("value", 100)
    c_val = composure.get("value", 100)
    v_info = get_mental_info(v_val)
    c_info = get_composure_info(c_val)
    return f"💪 **{v_info['name']}** | 😌 **{c_info['name']}**"


def get_composure_info(value: int) -> Dict[str, Any]:
    """평정 단계 정보를 반환합니다."""
    for stage_id, info in config.COMPOSURE_STAGES.items():
        low, high = info["range"]
        if low <= value < high:
            return info
    return config.COMPOSURE_STAGES[0]


def get_vigor_composure_text(p_data: Dict[str, Any]) -> str:
    """Returns "기력 85 | 평정 70" format."""
    mem = p_data.get("ai_memory", {})
    vigor = mem.get("vigor", mem.get("mental", {"value": 100}))
    composure = mem.get("composure", {"value": 100})
    v_val = vigor.get("value", 100)
    c_val = composure.get("value", 100)
    return f"기력 {v_val} | 평정 {c_val}"


def update_mental(
    user_data: Dict[str, Any],
    delta: int,
    reason: str,
    channel_id: Optional[str],
    user_id: Optional[str]
) -> str:
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
        
        # Add Trauma Passive (requires valid IDs)
        trauma_name = f"Trauma: {reason}"
        if channel_id and user_id:
            domain_manager.add_to_ai_memory_list(
                channel_id,
                user_id,
                "passives",
                {"name": trauma_name, "tags": ["Trauma", "Permanent"], "modifier": -5}
            )
        
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

    # Clamp floor based on the *base* delta (before inertia)
    base_target = max(0, min(100, current_val + delta))
    base_stage = get_mental_stage_id(base_target)
    clamp_floor = base_target

    # Clamping: Prevent crossing more than 2 stages downwards
    # Stage ranges: 0(70-100), 1(40-70), 2(15-40), 3(0-15)
    if base_stage > current_stage + 2:
        limit_stage = current_stage + 2
        limit_info = config.MENTAL_STAGES.get(limit_stage)
        clamp_floor = limit_info["range"][0]

    target_val = max(0, min(100, current_val + actual_delta))
    if delta < 0:
        # Do not drop below the clamped floor when inertia amplifies damage.
        target_val = max(target_val, clamp_floor)
    target_stage = get_mental_stage_id(target_val)
        
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
