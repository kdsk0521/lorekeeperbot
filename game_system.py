"""
Lorekeeper TRPG Bot - Game System Module
Consolidates World, Quest, and Simulation logic into a cohesive game system.
Replaces: world_manager.py, quest_manager.py, simulation_manager.py
"""

import logging
import random
import time
import json
import asyncio
import re
from typing import List, Dict, Any, Optional, Tuple

import config
import domain_manager
from config import (
    NEGATIVE_STATUS_EFFECTS,
    POSITIVE_STATUS_EFFECTS,
    STATUS_EFFECTS,
    SEVERITY_DOOM_IMPACT,
    NORMALITY_STAGES,
    get_normality_stage_info
)
from google.genai import types

# =========================================================
# WORLD SYSTEM (From world_manager.py)
# =========================================================

def get_time_slots(channel_id: str) -> List[str]:
    return config.DEFAULT_TIME_SLOTS

def get_weather_types(channel_id: str) -> List[str]:
    return config.DEFAULT_WEATHER_TYPES

def advance_time(channel_id: str) -> str:
    world = domain_manager.get_world_state(channel_id)
    if not world:
        return "⚠️ 데이터 없음"

    time_slots = get_time_slots(channel_id)
    weather_types = get_weather_types(channel_id)

    if not time_slots or not weather_types:
        return "⚠️ 시간대 또는 날씨 설정 오류"

    current_slot = world.get("time_slot", time_slots[1] if len(time_slots) > 1 else time_slots[0])
    try:
        current_idx = time_slots.index(current_slot)
    except ValueError:
        current_idx = 0

    msg = ""
    next_idx = current_idx + 1

    # 자정이 지나면 다음 날로
    if next_idx >= len(time_slots):
        world["time_slot"] = time_slots[0]
        world["day"] = world.get("day", 1) + 1
        new_weather = random.choice(weather_types)
        world["weather"] = new_weather
        msg = f"🌙 밤이 지나고 **{world['day']}일차 {time_slots[0]}**이 되었습니다. (날씨: {new_weather})"
    else:
        world["time_slot"] = time_slots[next_idx]
        msg = f"🕰️ 시간이 흘러 **{world['time_slot']}**가 되었습니다."
    
    # Doom Logic
    doom_increase, doom_reasons = calculate_doom_increase(channel_id, world, next_idx, time_slots)
    if doom_increase > 0:
        current_doom = world.get("doom", 0)
        world["doom"] = min(config.DOOM_MAX, current_doom + doom_increase)
        
        for reason in doom_reasons:
            if "위험 지역" in reason or "로어 규칙" in reason:
                msg += f"\n⚠️ **경고:** {reason}"
    
    # Update Context
    if next_idx >= len(time_slots) or "황혼" in world["time_slot"]:
         if "황혼" in world["time_slot"]:
             msg += " (🌅 해가 저물며 그림자가 길어집니다...)"
         else:
             msg += " (🌑 어둠이 짙어집니다...)"

    domain_manager.update_world_state(channel_id, world)
    return msg

def calculate_doom_increase(channel_id: str, world: dict, next_idx: int, time_slots: list) -> Tuple[int, List[str]]:
    doom_increase = 0
    doom_reasons = []
    
    # 1. Time Check
    is_night_time = next_idx >= len(time_slots) - 2
    if "황혼" in world.get("time_slot", ""):
        is_night_time = True
    
    if is_night_time:
        doom_increase += config.DOOM_INCREASE_NIGHT
    
    # 2. Nemesis Check
    domain = domain_manager.get_domain(channel_id)
    participants = domain.get("participants", {})
    nemesis_detected = False
    for uid, p in participants.items():
        if p.get("status") == "left": continue
        rels = p.get("relations", {})
        for npc_name, score in rels.items():
            if score <= config.NEMESIS_THRESHOLD:
                nemesis_detected = True; break
        if nemesis_detected: break
    
    if nemesis_detected:
        doom_increase += random.randint(config.DOOM_INCREASE_NEMESIS_MIN, config.DOOM_INCREASE_NEMESIS_MAX)
        doom_reasons.append("👿 적대 세력")
    
    # 3. AI Risk Level
    ai_risk = world.get("risk_level", "None").lower()
    location = world.get("current_location", "Unknown")
    
    if "high" in ai_risk or "extreme" in ai_risk:
        doom_increase += config.DOOM_INCREASE_HIGH_RISK
        doom_reasons.append(f"💀 위험 지역({location}): 고위험 감지")
    elif "medium" in ai_risk:
        doom_increase += config.DOOM_INCREASE_MEDIUM_RISK
        doom_reasons.append(f"⚠️ 위험 지역({location}): 주의 필요")
        
    # 4. Lore Rules
    loc_rules = world.get("location_rules", {})
    for loc_name, rule in loc_rules.items():
        if loc_name.lower() in location.lower():
            condition = rule.get("condition", "").lower()
            should_apply = False
            if "night" in condition and is_night_time: should_apply = True
            elif "always" in condition: should_apply = True
            
            if should_apply and "high" not in ai_risk:
                doom_increase += config.DOOM_INCREASE_LORE_RULE
                doom_reasons.append(f"📜 로어 규칙({loc_name})")
                
    return doom_increase, doom_reasons

def get_random_doom_event(doom: int) -> str:
    """둠 수치에 따른 랜덤 플레이버 텍스트 이벤트를 반환합니다."""
    
    critical_events = [
        "🌌 하늘이 찢어지며 공허가 쏟아져 내립니다.",
        "👁️ 세상의 모든 눈이 당신을 주시하고 있습니다.",
        "🩸 대지가 비명을 지르며 붉은 균열을 일으킵니다.",
        "🌑 태양이 검게 물들고 영원한 밤이 시작되려 합니다.",
    ]
    
    danger_events = [
        "👹 그림자 속에서 끔찍한 형체가 꿈틀거립니다.",
        "🌪️ 피 냄새 섞인 바람이 불어옵니다.",
        "🔥 멀리서 원인 모를 화재가 발생했습니다.",
        "💀 까마귀 떼가 하늘을 뒤덮습니다.",
    ]
    
    warning_events = [
        "🦅 정찰병이 수상한 움직임을 보고했습니다.",
        "📜 불길한 예언이 전해지고 있습니다.",
        "🌫️ 기이한 안개가 피어오르고 있습니다.",
        "🔔 먼 곳에서 종소리가 울려옵니다.",
    ]
    
    calm_events = [
        "🌸 평화로운 하루입니다. 특별한 일이 없습니다.",
        "🐦 새들이 노래하고 있습니다. 좋은 징조입니다.",
        "☀️ 맑은 날씨가 계속되고 있습니다.",
        "🏠 마을 사람들이 일상을 보내고 있습니다.",
    ]
    
    if doom >= config.DOOM_THRESHOLD_CRITICAL:
        event = random.choice(critical_events)
    elif doom >= config.DOOM_THRESHOLD_DANGER:
        event = random.choice(danger_events)
    elif doom >= config.DOOM_THRESHOLD_WARNING:
        event = random.choice(warning_events)
    else:
        event = random.choice(calm_events)
    
    return f"🎲 **[둠 이벤트]**\n{event}"

def change_doom(channel_id: str, amount: int) -> str:
    world = domain_manager.get_world_state(channel_id)
    current = world.get("doom", 0)
    new_val = max(0, min(config.DOOM_MAX, current + amount))
    world["doom"] = new_val
    domain_manager.update_world_state(channel_id, world)
    return f"📉 **위기 수치 변경:** {current}% -> {new_val}% ({_get_doom_description(new_val)})"

def _get_doom_description(doom: int) -> str:
    if doom >= config.DOOM_MAX: return "💥 파멸 💥"
    elif doom >= config.DOOM_THRESHOLD_CRITICAL: return "절망적"
    elif doom >= config.DOOM_THRESHOLD_DANGER: return "임박한 위협"
    elif doom >= config.DOOM_THRESHOLD_WARNING: return "불길한 징조"
    else: return "평온함"

def get_world_context(channel_id: str) -> str:
    world = domain_manager.get_world_state(channel_id)
    if not world: return ""
    
    party_context = domain_manager.get_party_status_context(channel_id)
    location = world.get("current_location", "Unknown")
    
    return (
        f"[Current World State]\n"
        f"- Location: {location}\n"
        f"- Risk Level: {world.get('risk_level', 'None')}\n"
        f"- Time: Day {world.get('day', 1)}, {world.get('time_slot', '오후')}\n"
        f"- Weather: {world.get('weather', '맑음')}\n"
        f"- Doom Level: {world.get('doom', 0)}% ({_get_doom_description(world.get('doom', 0))})\n"
        f"- **Atmosphere Context**: {party_context}\n"
        f"*Instruction: Adjust the narrative tone based on Location, Time, Doom, and Party Condition.*"
    )

def get_doom_forecast(channel_id: str) -> str:
    world = domain_manager.get_world_state(channel_id)
    current = world.get("doom", 0)
    desc = _get_doom_description(current)
    
    # Calculate trend
    # This logic assumes we want to show what might increase doom.
    # Reusing calculate_doom_increase logic partially or just giving a static report.
    # For now, let's give a static status report.
    
    msg = f"🛡️ **위기 예보**\n현재 수치: {current}% ({desc})\n"
    
    if current >= config.DOOM_THRESHOLD_CRITICAL:
        msg += "⚠️ **경고:** 파멸이 임박했습니다. 모든 행동에 위험이 따릅니다."
    elif current >= config.DOOM_THRESHOLD_DANGER:
        msg += "⚠️ **주의:** 세계의 적의가 느껴집니다."
    else:
        msg += "✅ 아직은 안전합니다."
        
    return msg 

# =========================================================
# QUEST SYSTEM (From quest_manager.py)
# =========================================================
# Helper Functions
def _get_board(channel_id: str) -> Dict[str, Any]:
    d = domain_manager.get_domain(channel_id)
    if "quest_board" not in d or not isinstance(d["quest_board"], dict):
        d["quest_board"] = {"active": [], "completed": [], "memos": [], "archive": [], "lore": []}
    return d["quest_board"]

def _save_board(channel_id: str, board: Dict[str, Any]) -> None:
    domain_manager.update_quest_board(channel_id, board)

# Quest & Memo Operations
def add_quest(channel_id: str, content: str) -> str:
    return _list_op(channel_id, "active", content, "🔥", "퀘스트")

def complete_quest(channel_id: str, content: str) -> str:
    return _move_op(channel_id, "active", "completed", content, "✅", "퀘스트", "완료")

def add_memo(channel_id: str, content: str) -> str:
    return _list_op(channel_id, "memos", content, "📝", "메모")

def remove_memo(channel_id: str, content: str) -> str:
    return _del_op(channel_id, "memos", content, "🗑️", "메모")

def resolve_memo_auto(channel_id: str, content: str) -> str:
    return _move_op(channel_id, "memos", "archive", content, "🗄️", "메모", "해결(보관)")

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
        
    txt = "### [QUESTS & MEMOS]\n"
    if active:
        txt += "**Active Objectives:**\n" + "\n".join([f"- {q}" for q in active]) + "\n\n"
    if memos:
        txt += "**Active Memos:**\n" + "\n".join([f"- {m}" for m in memos]) + "\n\n"
    if archive:
        txt += "**Archived Info:**\n" + "\n".join([f"- {m}" for m in archive[-config.MAX_ARCHIVE_DISPLAY:]]) + "\n"
    return txt.strip()

# AI/Chronicle Features
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
                return json.loads(clean)
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
    """
    로어 데이터를 텍스트 파일 형식으로 내보냅니다.
    """
    lore = domain_manager.get_lore(channel_id)
    npcs = domain_manager.get_npcs(channel_id)
    
    if not lore and not npcs:
        return None, "⚠️ 내보낼 데이터가 없습니다."
        
    lines = []
    lines.append(f"# Lore Export - {channel_id}")
    lines.append(f"Date: {time.strftime('%Y-%m-%d %H:%M')}\n")
    
    lines.append("## LORE")
    lines.append(lore)
    lines.append("\n## NPC LIST")
    
    for name, data in npcs.items():
        lines.append(f"### {name}")
        lines.append(f"Desc: {data.get('desc', '-')}")
        if data.get('appearance'): lines.append(f"Look: {data.get('appearance')}")
        if data.get('personality'): lines.append(f"Personality: {data.get('personality')}")
        lines.append("")

    # [Restored] Session-detected NPCs
    mem = domain_manager.get_session_ai_memory(channel_id)
    npc_summaries = mem.get("npc_summaries", {})
    if npc_summaries:
        lines.append("\n### [SESSION NPCs - AI Detected]")
        for name, summary in npc_summaries.items():
            if name in npcs: continue # Skip if promoted
            lines.append(f"#### {name}")
            lines.append(f"Summary: {summary}")
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
# SIMULATION SYSTEM (From simulation_manager.py)
# =========================================================

def get_status_doom_modifier(status_effects: List[str]) -> Tuple[int, int, List[str], List[str]]:
    inc, dec, n_f, p_f = 0, 0, [], []
    for eff in status_effects:
        eff_l = eff.lower()
        for k, v in NEGATIVE_STATUS_EFFECTS.items():
            if k in eff_l or eff_l in k:
                inc += v; n_f.append(f"{eff} (+{v})"); break
        else:
            for k, v in POSITIVE_STATUS_EFFECTS.items():
                if k in eff_l or eff_l in k:
                    dec += v; p_f.append(f"{eff} (-{v})"); break
    return inc, dec, n_f, p_f

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

def get_status_summary(user_data: Dict[str, Any]) -> str:
    effects = user_data.get("status_effects", [])
    if not effects: return "✅ **상태:** 정상"
    return f"🧬 **상태:** {', '.join(effects)}"

# Abnormal Exposure
def get_normality_stage(normality: int) -> Dict[str, str]:
    for (l, h), info in NORMALITY_STAGES.items():
        if l <= normality < h: return info
    return NORMALITY_STAGES[(80, 101)]

def calculate_normality(count: int) -> int:
    if count <= 0: return 0
    import math
    return min(100, int((math.log(count + 1) / math.log(11)) * 100))

def expose_to_abnormal(user_data: Dict[str, Any], abnormal_type: str, current_day: int = 1) -> Tuple[Dict[str, Any], Optional[str], Optional[Dict]]:
    exp = user_data.get("abnormal_exposure", {})
    if abnormal_type not in exp:
        exp[abnormal_type] = {"count": 0, "normality": 0, "first_day": current_day}
    
    d = exp[abnormal_type]
    d["count"] += 1
    old_n = d["normality"]
    new_n = calculate_normality(d["count"])
    d["normality"] = new_n
    
    user_data["abnormal_exposure"] = exp
    
    old_s = get_normality_stage(old_n)
    new_s = get_normality_stage(new_n)
    
    msg = None
    if old_s["stage"] != new_s["stage"]:
        msg = f"🌓 **[{abnormal_type}]** 적응: {old_s['name']} → {new_s['name']}"
    
    return user_data, msg, new_s

def get_abnormal_context(user_data: Dict[str, Any], abnormal_types: List[str]) -> str:
    if not abnormal_types: return ""
    exp = user_data.get("abnormal_exposure", {})
    ctxs = []
    
    for ab in abnormal_types:
        if ab in exp:
            n = exp[ab]["normality"]
            s = get_normality_stage(n)
            ctxs.append(f"- {ab}: {n}% ({s['name']}) -> {s['reaction_hint']}")
        else:
            ctxs.append(f"- {ab}: 0% (New) -> Shock/Fear")
    return "### [Abnormal Adaptation]\n" + "\n".join(ctxs) + "\n"

# Passive System
def get_passives_for_context(user_data: Dict[str, Any]) -> str:
    p_list = user_data.get("passives", [])
    ai_p = user_data.get("ai_memory", {}).get("passives", [])
    
    names = set()
    for p in p_list:
        names.add(p.get("name") if isinstance(p, dict) else str(p))
    for p in ai_p:
        names.add(str(p))
        
    if not names: return "Passives: None"
    return f"Passives: {', '.join(names)}"
