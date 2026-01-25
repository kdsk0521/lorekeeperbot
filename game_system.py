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
    """시간을 다음 슬롯으로 진행하고 세계 변화를 반환"""
    world = domain_manager.get_world_state(channel_id)
    time_slots = get_time_slots(channel_id)
    
    current_slot = world.get("time_slot", "오후")
    try:
        current_idx = time_slots.index(current_slot)
    except ValueError:
        current_idx = 2 # Default to Afternoon

    next_idx = current_idx + 1
    
    # 이모지 매핑
    time_emoji = {
        "새벽": "🌅", "오전": "☀️", "오후": "🌤️",
        "황혼": "🌆", "저녁": "🌙", "심야": "🌑"
    }
    
    msg = ""
    
    if next_idx >= len(time_slots):
        # 날짜 변경
        world["time_slot"] = time_slots[0]
        world["day"] = world.get("day", 1) + 1
        new_weather = random.choice(get_weather_types(channel_id))
        world["weather"] = new_weather
        
        emoji = time_emoji.get(time_slots[0], "🌅")
        msg = (
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"🌙 **밤이 지나고...**\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"📅 **{world['day']}일차** {emoji} **{time_slots[0]}**\n"
            f"🌤️ 날씨: {new_weather}\n"
            f"━━━━━━━━━━━━━━━━━━━━"
        )
    else:
        world["time_slot"] = time_slots[next_idx]
        emoji = time_emoji.get(time_slots[next_idx], "⏰")
        
        # 시간대별 분위기 메시지
        atmosphere = {
            "새벽": "동이 트기 시작합니다...",
            "오전": "아침 햇살이 비춥니다.",
            "오후": "태양이 중천에 떠 있습니다.",
            "황혼": "해가 저물어갑니다...",
            "저녁": "어둠이 내려앉습니다.",
            "심야": "깊은 밤이 찾아왔습니다..."
        }
        atm = atmosphere.get(time_slots[next_idx], "")
        
        msg = f"{emoji} **{time_slots[next_idx]}** — {atm}"
    
    # Doom 체크
    doom_increase, doom_reasons = calculate_doom_increase(channel_id, world, next_idx, time_slots)
    if doom_increase > 0:
        current_doom = world.get("doom", 0)
        world["doom"] = min(config.DOOM_MAX, current_doom + doom_increase)
        for reason in doom_reasons:
            if "위험 지역" in reason or "로어 규칙" in reason:
                 msg += f"\n⚠️ **경고:** {reason}"
            else:
                 msg += f"\n⚠️ {reason}"

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
        ai_mem = p.get("ai_memory", {})
        rels = ai_mem.get("relationships", {})
        for npc_name, score in rels.items():
            try:
                score_val = int(score)
            except (ValueError, TypeError):
                continue # Skip invalid scores
                
            if score_val <= config.NEMESIS_THRESHOLD:
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
        f"[현재 세계 상태]\n"
        f"- 위치: {location}\n"
        f"- 위험도: {world.get('risk_level', 'None')}\n"
        f"- 시간: {world.get('day', 1)}일차, {world.get('time_slot', '오후')}\n"
        f"- 날씨: {world.get('weather', '맑음')}\n"
        f"- 위기 수치: {world.get('doom', 0)}% ({_get_doom_description(world.get('doom', 0))})\n"
        f"- **파티 분위기**: {party_context}\n"
        f"*지침: 이 위치, 시간, 위기 수치, 파티 상태를 반영하여 서술 톤을 조절하십시오.*"
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

def get_npc_time_progression(channel_id: str) -> List[str]:
    """
    시간 경과에 따른 NPC 상태 변화 힌트 생성
    
    Returns:
        NPC 상태 변화 힌트 리스트
    """
    npcs = domain_manager.get_npcs(channel_id)
    world = domain_manager.get_world_state(channel_id)
    time_slot = world.get("time_slot", "오후")
    
    hints = []
    
    # 시간대별 일반적 NPC 활동
    time_activities = {
        "새벽": ["잠들어 있다", "이른 기상 준비", "야간 근무 마무리", "깊은 잠에 빠져 있다"],
        "오전": ["아침 식사", "일과 시작", "청소/정리", "분주하게 움직임"],
        "오후": ["업무 중", "점심 후 활동", "외출", "나른하게 휴식"],
        "황혼": ["퇴근 준비", "저녁 준비", "휴식", "하루를 정리함"],
        "저녁": ["저녁 식사", "여가 활동", "TV 시청", "술자리"],
        "심야": ["잠자리 준비", "야식", "늦은 작업", "비밀스러운 만남"]
    }
    
    activities = time_activities.get(time_slot, ["활동 중"])
    
    # Random selection to avoid static feel (simple simulation)
    # The actual behavior is refined by AI reasoning, this gives a baseline.
    import random
    
    for npc_name, npc_data in npcs.items():
        activity = random.choice(activities)
        hints.append(f"{npc_name}: {activity}")
    
    return hints 

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

def remove_quest(channel_id: str, content: str) -> str:
    return _del_op(channel_id, "active", content, "🗑️", "퀘스트")

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
        
    txt = "### [퀘스트 및 메모]\n"
    if active:
        txt += "**진행 중인 목표:**\n" + "\n".join([f"- {q}" for q in active]) + "\n\n"
    if memos:
        txt += "**저장된 메모:**\n" + "\n".join([f"- {m}" for m in memos]) + "\n\n"
    if archive:
        txt += "**보관된 정보:**\n" + "\n".join([f"- {m}" for m in archive[-config.MAX_ARCHIVE_DISPLAY:]]) + "\n"
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

    # [Restored] Session-detected NPCs
    mem = domain_manager.get_session_ai_memory(channel_id)
    npc_summaries = mem.get("npc_summaries", {})
    if npc_summaries:
        lines.append("\n### [세션 감지 NPC - AI Detected]")
        for name, summary in npc_summaries.items():
            if name in npcs: continue # Skip if promoted
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
# ABNORMAL ADAPTATION SYSTEM (Restored Feature)
# =========================================================

# NORMALITY_STAGES and get_normality_stage_info are imported from config.py

def calculate_normality(count: int, base_threshold: int = 10) -> int:
    """
    노출 횟수에 따른 적응도(0-100)를 계산합니다 (로그 스케일).
    1회: ~20%, 3회: ~45%, 5회: ~60%, 10회: ~100%
    """
    if count <= 0: return 0
    import math
    # math.log(count + 1) -> 1=0.69, 3=1.38, 5=1.79, 10=2.39
    # math.log(base_threshold + 1) -> 10=2.39
    normality = min(100, int((math.log(count + 1) / math.log(base_threshold + 1)) * 100))
    return normality

def expose_to_abnormal(user_data: Dict[str, Any], abnormal_type: str) -> Tuple[Dict[str, Any], Optional[str]]:
    """
    비일상 적응도 업데이트
    Returns: (update_user_data, notification_msg)
    """
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
    """AI에게 전달할 비일상 적응 현황"""
    exposure = user_data.get("abnormal_exposure", {})
    if not exposure: return ""
    
    lines = []
    for k, v in exposure.items():
        norm = v["normality"]
        stage = get_normality_stage_info(norm)
        lines.append(f"- {k}: {norm}% ({stage['reaction_hint']})")
        
    if not lines: return ""
    return "### [Mental Adaptation]\n" + "\n".join(lines) + "\n*Adjust reaction based on adaptation level.*"

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

def perform_check(channel_id: str, user_id: str, action_desc: str = "") -> str:
    """
    주사위 판정 (1d100) + 상태 이상 보정
    """
    import random
    
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
            
    # 3. 주사위 굴림
    dice_val = random.randint(1, 100)
    final_val = max(1, dice_val + modifier) # 최소 1
    
    # 4. 결과 포맷팅
    # [행동] 판정
    # 🎲 1d100(45) - 10 (부상, 탈진) = 35
    # 보유 패시브: [검술, 야간시야]
    
    header = f"🎲 **판정: {action_desc}**" if action_desc else "🎲 **판정**"
    
    calc_str = f"**{dice_val}**"
    if modifier != 0:
        sign = "+" if modifier > 0 else ""
        calc_str += f" {sign}{modifier} ({', '.join(mod_details)})"
        
    result_str = f"{header}\n결과: {calc_str} = **{final_val}**"
    
    # 패시브 컨텍스트 (AI/GM 참고용)
    passives = p_data.get("passives", [])
    if passives:
        p_names = [p.get("name") if isinstance(p, dict) else str(p) for p in passives]
        result_str += f"\n💡 참고 특성: {', '.join(p_names)}"
        
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

def export_session_history(channel_id: str, incremental: bool = False) -> Tuple[str, str]:
    """
    세션의 대화 내역(History)을 텍스트 파일 형식으로 내보냅니다.
    incremental=True일 경우, 지난번 추출 이후의 데이터만 내보냅니다.
    """
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

    # Update index if incremental
    if incremental:
        domain_manager.set_last_export_idx(channel_id, len(history))
        
    return "\n".join(lines), f"✅ **대화 내역 추출 완료** ({mode_text}, {len(target_history)} lines)"

def export_chronicle_book(channel_id: str, incremental: bool = False) -> Tuple[str, str]:
    """
    기록된 연대기(Fermented Chronicles)를 텍스트 파일 형식으로 내보냅니다.
    incremental=True일 경우, 지난번 추출 이후의 연대기만 내보냅니다.
    """
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

    # Update index if incremental
    if incremental:
        domain_manager.set_last_chronicle_idx(channel_id, len(lore_entries))

    return "\n".join(lines), f"✅ **연대기 추출 완료** ({mode_text}, {len(target_entries)} entries)"

# Wrappers / Aliases
def get_quest_board(channel_id: str) -> Dict[str, Any]:
    return _get_board(channel_id)

def get_active_quests(channel_id: str) -> List[str]:
    return _get_board(channel_id).get("active", [])

def get_memos(channel_id: str) -> List[str]:
    return _get_board(channel_id).get("memos", [])
