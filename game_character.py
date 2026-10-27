"""
Lorekeeper TRPG Bot - Game Character Module
Handles Character Inventory, Status Effects, Quests, Memos, and Mechanics (Dice).
Extracted from game_system.py
"""

import logging
import time
import re
import asyncio
from typing import List, Tuple, Dict, Any, Optional

from google import genai
from google.genai import types

logger = logging.getLogger("GameCharacter")

import config
import domain_manager
import text_resources
from config import (
    MENTAL_STAGES,
    COMPOSURE_STAGES,
    STATUS_EFFECTS,
)

# =========================================================
# QUEST & MEMO SYSTEM
# [2026-10-24 C74 판2 합치기] 퀘스트 보드 → 스레드 장부 목표(goal). 퀘스트 함수(add · advance · complete · remove ·
#   archive_stale · dismiss · _find_quest)는 지웠다 — 목표 쓰기는 thread_ledger(추출 · OOC · 코드) 하나.
#   보드에 남는 것 = lore(옛 연대기) · memos(옛 칸, 비어 있음). 옛 저장의 active/completed/archive/dismissed 는
#   읽는 곳이 없다(이사 없음 — 판정 15번).
# =========================================================

def _get_board(channel_id: str) -> Dict[str, Any]:
    d = domain_manager.get_domain(channel_id)
    if "quest_board" not in d or not isinstance(d["quest_board"], dict):
        d["quest_board"] = {"memos": [], "lore": []}
    return d["quest_board"]


def _save_board(channel_id: str, board: Dict[str, Any]) -> None:
    domain_manager.update_quest_board(channel_id, board)


# Memo Operations — notebook v2 (섹션 dict 정본, 2026-09-06)
#  · 유저 줄('-')은 **코드만** 만들고 지운다. LLM은 읽기만 한다.
#  · LLM 줄('>')은 apply_llm_memos()가 단독 소유(B-1 memo_add/memo_remove).
#  리터럴 헤더 split은 전부 사라졌다 — 구역 오염/유저 메모 소실이 구조적으로 불가능.
def get_notebook_data(channel_id: str, user_id: str = "") -> dict:
    return domain_manager.get_notebook_data(channel_id, user_id)


def update_notebook_data(channel_id: str, nb: dict, user_id: str = "") -> None:
    # [2026-10-23 C61] 거울(ai_memory.inventory) 동기화 삭제 — 소지품 정본이 노트북을 떠났다.
    domain_manager.update_notebook_data(channel_id, nb, user_id)


def _norm(s: str) -> str:
    return re.sub(r'\s+', ' ', str(s or '').strip())


def add_memo(channel_id: str, content: str, user_id: str = "") -> str:
    """유저색 메모 1줄 추가. 공백 정규화 dedup(두 색 전체 대조)."""
    t = _norm(content)
    if not t:
        return ""
    nb = get_notebook_data(channel_id, user_id)
    memo = nb["sections"]["메모"]
    if t in memo["user"] or t in memo["llm"]:
        return f"⚠️ 이미 노트북에 있는 내용입니다: {content}"
    memo["user"].append(t)
    update_notebook_data(channel_id, nb, user_id)
    return f"📝 **노트북 기록:** {content}"


def remove_memo(channel_id: str, content: str, user_id: str = "") -> str:
    """메모 1줄 삭제(부분일치 첫 매칭). 유저색 우선, 없으면 LLM색."""
    t = _norm(content)
    if not t:
        return ""
    nb = get_notebook_data(channel_id, user_id)
    memo = nb["sections"]["메모"]
    for color in ("user", "llm"):
        for i, line in enumerate(memo[color]):
            if t in line:
                del memo[color][i]
                update_notebook_data(channel_id, nb, user_id)
                return f"🗑️ **노트북 삭제:** {content}"
    return f"⚠️ '{content}' 내용을 찾을 수 없습니다."


def edit_memo(channel_id: str, old_content: str, new_content: str, user_id: str = "") -> str:
    """메모 1줄 부분치환(첫 매칭). 줄 전체 교체가 아니라 substring 치환."""
    o, n = _norm(old_content), _norm(new_content)
    if not o:
        return ""
    nb = get_notebook_data(channel_id, user_id)
    memo = nb["sections"]["메모"]
    for color in ("user", "llm"):
        for i, line in enumerate(memo[color]):
            if o in line:
                memo[color][i] = _norm(line.replace(o, n, 1))
                update_notebook_data(channel_id, nb, user_id)
                return f"📝 **노트북 수정:** {old_content} -> {new_content}"
    return f"⚠️ '{old_content}' 내용을 찾을 수 없습니다."


def resolve_memo_auto(channel_id: str, content: str, user_id: str = "") -> str:
    return remove_memo(channel_id, content, user_id) + " (자동 해결)"


def apply_llm_memos(channel_id: str, user_id: str, memo_add=None, memo_remove=None) -> bool:
    """B-1(_extract_physical) 산출을 **LLM색 줄에만** 적용한다. 유저색 배열 무접촉.
    WHY: 옛 계약(notebook_update 전문 반환)은 stale 스냅샷으로 유저 메모를 통째 덮어썼다.
    상한 config.NOTEBOOK_LLM_MEMO_MAX 초과 시 오래된 LLM 줄부터 탈락."""
    # [2026-10-07 노트북 메모 바닥화 M2] '>'는 코드가 LLM 줄에 붙이는 표식(notebook_render "> {줄}") —
    #   모델이 직접 붙여 낸 앞머리 '>'(겹친 "> >" 포함)는 떼고 저장·대조한다. 안 떼면 화면에 "> > …".
    def _llm_line(x):
        return re.sub(r'^(?:>\s*)+', '', _norm(x)).strip()
    add = [y for y in (_llm_line(x) for x in (memo_add or [])) if y]
    rem = [y for y in (_llm_line(x) for x in (memo_remove or [])) if y]
    if not add and not rem:
        return False
    nb = get_notebook_data(channel_id, user_id)
    memo = nb["sections"]["메모"]
    before = list(memo["llm"])
    for r in rem:
        for i, line in enumerate(list(memo["llm"])):
            if r in line:
                memo["llm"].remove(line)
                break
    for a in add:
        if a not in memo["llm"] and a not in memo["user"]:
            memo["llm"].append(a)
    cap = int(getattr(config, "NOTEBOOK_LLM_MEMO_MAX", 12))
    if cap > 0 and len(memo["llm"]) > cap:
        memo["llm"] = memo["llm"][-cap:]
    if memo["llm"] == before:
        return False
    update_notebook_data(channel_id, nb, user_id)
    return True


# Notebook System (New in V5.1, per-user in V8; v2 섹션 dict 2026-09-06)
def get_notebook_text(channel_id: str, user_id: str = "", with_items: bool = True) -> str:
    """표시/프롬프트용 렌더 텍스트. 정본 아님 — 쓰기는 get/update_notebook_data로.
    [2026-10-23 C61] [소지품] 칸은 기본 선언 목록에서 읽는다. with_items=False 면 칸을 안 그린다
    (출력물 추출 콜 — 소지품은 선언 급식으로 따로 받는다)."""
    return domain_manager.get_notebook(channel_id, user_id, with_items=with_items)


def update_notebook_text(channel_id: str, new_text, user_id: str = "") -> None:
    """[legacy shim] 텍스트를 받으면 파싱해 dict로 저장. 새 코드는 쓰지 마라."""
    domain_manager.update_notebook(channel_id, new_text, user_id)


# [notebook v2] 헤더 리터럴의 주인은 domain_manager(렌더 전용). 여기선 더 이상 split하지 않는다.
_SOJIPIN_HEADER = domain_manager.NOTEBOOK_SOJIPIN_HEADER
_MEMO_HEADER = domain_manager.NOTEBOOK_MEMO_HEADER
_JOURNAL_HEADER = domain_manager.NOTEBOOK_JOURNAL_HEADER
_JOURNAL_DISPLAY_CAP = 10  # [일지] 섹션 표시 안전 상한(줄). 요약은 보통 몇 줄이라 거의 안 걸림.

def _render_journal_section(channel_id: str, recent_lines: list, user_id: str = "") -> None:
    """노트북 [일지] 섹션을 recent_lines로 **교체**(living-rewrite). [소지품]/[메모] 무접촉 —
    v2에선 섹션이 별개 키라 파서 토글 사고가 원천적으로 없다."""
    _clean = lambda e: _norm(str(e).lstrip('-'))
    nb = get_notebook_data(channel_id, user_id)
    nb["sections"]["일지"]["lines"] = [_clean(e) for e in recent_lines if _clean(e)]
    update_notebook_data(channel_id, nb, user_id)


def add_to_journal(channel_id: str, content: str, user_id: str = "") -> str:
    """캐릭터 연속성 일지 갱신 [living-rewrite]. content = 캐릭터의 '현재 여정 요약'
    (시트 notes가 매 재작성마다 재생성). 표시/저장 분리:
      - 노트북 [일지] 섹션 → 이번 요약으로 **통째 교체**(옛 내용 수정/대체 = living document).
      - 전체 이력(요약이 어떻게 진화했나) → append_journal_log(영속). !일지로 조회.
    즉 노트북엔 항상 현재 요약만, 진화 이력은 로그에 보존."""
    content = (content or "").strip()
    if not content:
        return ""
    if user_id:
        domain_manager.append_journal_log(channel_id, user_id, content)  # 진화 이력 보존
    # [일지] 표시 = 최신 요약으로 교체(append 아님). 여러 줄이면 줄별 bullet, 안전 cap.
    display = [l.strip() for l in content.splitlines() if l.strip()] or [content]
    _render_journal_section(channel_id, display[:_JOURNAL_DISPLAY_CAP], user_id)
    return f"📓 일지 갱신: {content[:40]}"

# [삭제 2026-09-06] merge_notebook_preserve_inventory — WHY: 전문 반환 계약(notebook_update)을
# 사후 봉합하려고 [소지품] 파트만 라이브에서 되살리던 함수. v2에선 B-1이 memo_add/remove만
# 돌려주고 섹션이 dict로 분리돼 있어 봉합할 충돌 자체가 없다. 호출자 0(orchestration 정리 완료).


# [2026-10-23 C61] 소지품 정본 = custom_vars 기본 선언 목록 `소지품`(노트북 [소지품] 칸은 표시만).
#   두 함수의 **속만** 바꿨다 — 호출부(OOC·표현식 `재고[x]`)와 되비침 문자열은 그대로다.
#   uid 는 받기만 한다(솔로 기준 채널 목록 하나 — 다인은 나중).
def add_item_to_sojipin(channel_id: str, item_name: str, user_id: str = "", qty: int = 1,
                        source: str = "code") -> str:
    """소지품에 수량 추가. 이미 있으면 qty 누적."""
    item_name = (item_name or "").strip()
    if not item_name:
        return ""
    try:
        qty = max(1, int(qty))
    except Exception:
        qty = 1
    import custom_vars as _cv
    rec = _cv.sojipin_add(channel_id, item_name, qty, source=source)
    if not rec:
        return f"⚠ 소지품에 못 넣음: {item_name}"
    real = rec.get("item") or item_name
    if int(rec.get("from") or 0) > 0:
        return f"이미 소지품에 있음: {real} (×{int(rec.get('to') or 0)})"
    return f"소지품 추가: {real}" + (f" ×{qty}" if qty > 1 else "")


def remove_item_from_sojipin(channel_id: str, item_name: str, user_id: str = "", qty=None,
                             source: str = "code") -> str:
    """소지품에서 제거. qty=None이면 항목 전부, 숫자면 그만큼 차감(0 이하 → 항목 삭제).
    정확 일치 우선, 없을 때만 부분일치 첫 매칭(일반 이름의 wrong-target 완화 — 기존 규칙 승계)."""
    item_name = (item_name or "").strip()
    if not item_name:
        return ""
    import custom_vars as _cv
    real, rec = _cv.sojipin_take(channel_id, item_name, qty, source=source)
    if not real or not rec:
        return f"소지품에서 '{item_name}' 못 찾음"
    return f"소지품 제거: {real}"


def seed_item_in_sojipin(channel_id: str, item_name: str, qty: int = 1) -> bool:
    """[C61 R3] 시트 시작 장비 — **없을 때만** qty 개. `!설명` 재등록이 수량을 쌓지 않는다."""
    import custom_vars as _cv
    return bool(_cv.sojipin_seed(channel_id, item_name, qty))


def sojipin_anchor(channel_id: str) -> List[Dict[str, Any]]:
    """[C61 R5] une 앵커 `inventory` — [{name, qty}]. Theoria `- Inventory:`·판정 `_item_names` 가 읽는다."""
    try:
        import custom_vars as _cv
        return [{"name": k, "qty": n} for k, n in _cv.sojipin_items(channel_id).items()]
    except Exception:
        return []


# [삭제 2026-10-23 C61] sync_notebook_to_inventory — 노트북 → ai_memory.inventory 거울. 정본이 노트북을
#   떠나 거울의 원천이 없다. 소비자(une 앵커)는 sojipin_anchor 로 옮겼다.


def get_active_quests(channel_id: str) -> List[str]:
    """[C74 판2] 열린 목표 줄(사람에게 보이는 말 — `!정보`). 출처 = 장부."""
    import thread_ledger
    return thread_ledger.goal_lines_ko(channel_id)


def get_active_quests_text(channel_id: str) -> str:
    lines = get_active_quests(channel_id)
    if not lines:
        return "📭 현재 진행 중인 목표가 없습니다."
    return "🔥 **진행 중인 목표:**\n" + "\n".join(f"{i+1}. {l}" for i, l in enumerate(lines))

def get_status_message(channel_id: str, user_id: str = "") -> str:
    quests = get_active_quests_text(channel_id)
    notebook = get_notebook_text(channel_id, user_id)
    return f"{quests}\n\n{notebook}"

# ⚰ [2026-10-25 C74 판4] get_objective_context 은퇴 — Slot 11 목표 블록이 Slot 15 `<In_Motion>`(in_motion.py)으로 갔다.

# =========================================================
# CHARACTER STATUS & INVENTORY
# =========================================================

def _coerce_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default

def _default_status_modifiers(effect_type: str, severity: int) -> Dict[str, int]:
    if effect_type != "debuff":
        return {}
    base = getattr(config, "SEVERITY_EFFECTS", {}).get(severity)
    if isinstance(base, dict):
        return dict(base)
    return {}

def _normalize_duration(raw_duration: Any, current_turn: Optional[int]) -> Dict[str, Any]:
    if isinstance(raw_duration, dict):
        dtype = str(raw_duration.get("type", "persistent")).strip().lower()
        if dtype not in getattr(config, "DURATION_TYPES", set()):
            dtype = "persistent"
        value = raw_duration.get("value")
        start_turn = raw_duration.get("start_turn")
        if dtype == "turns":
            value = _coerce_int(value, 0)
            if value <= 0:
                return {"type": "persistent"}
            start_turn = _coerce_int(start_turn, current_turn or 0)
            return {"type": "turns", "value": value, "start_turn": start_turn}
        return {"type": dtype, "value": value, "start_turn": start_turn}

    if isinstance(raw_duration, str):
        d = raw_duration.strip().lower()
        if d.startswith("turns_"):
            try:
                value = int(d.split("_", 1)[1])
            except (ValueError, IndexError):
                value = 0
            if value > 0:
                return {"type": "turns", "value": value, "start_turn": current_turn or 0}
        if d in getattr(config, "DURATION_TYPES", set()):
            return {"type": d}

    return {"type": "persistent"}

def _build_effect_from_name(effect_name: str, current_turn: Optional[int]) -> Dict[str, Any]:
    tag = None
    if effect_name in getattr(config, "LEGACY_TAG_MAP", {}):
        tag = config.LEGACY_TAG_MAP[effect_name]
    base = None
    if tag and tag in getattr(config, "STATUS_TAGS", {}):
        base = config.STATUS_TAGS[tag]
    elif effect_name in getattr(config, "STATUS_TAGS", {}):
        tag = effect_name
        base = config.STATUS_TAGS[tag]

    if base:
        effect_type = base.get("type", "debuff")
        severity = _coerce_int(base.get("severity", 1), 1)
        modifiers = base.get("modifiers", {})
        return {
            "tag": tag,
            "name": base.get("name", effect_name),
            "type": effect_type,
            "severity": severity,
            "duration": _normalize_duration(base.get("duration"), current_turn),
            "modifiers": dict(modifiers) if isinstance(modifiers, dict) else {},
        }

    info = STATUS_EFFECTS.get(effect_name, {})
    effect_type = info.get("type", "debuff")
    severity = _coerce_int(info.get("severity", 1), 1)
    modifiers = _default_status_modifiers(effect_type, severity)
    return {
        "tag": tag or effect_name,
        "name": effect_name,
        "type": effect_type,
        "severity": severity,
        "duration": _normalize_duration(None, current_turn),
        "modifiers": modifiers,
    }

def _normalize_effect_dict(effect: Dict[str, Any], current_turn: Optional[int]) -> Dict[str, Any]:
    normalized = dict(effect)
    name = normalized.get("name") or normalized.get("label") or ""
    tag = normalized.get("tag") or normalized.get("id")
    # [2026-10-07 활력평형 B6] 옛 버프 "활력"(tag vigor) → "고양"(inspired). 게이지 활력과 이름이 겹쳤다.
    if tag in getattr(config, "STATUS_TAG_RENAMES", {}):
        tag = config.STATUS_TAG_RENAMES[tag]
        normalized["tag"] = tag
    if name in getattr(config, "STATUS_NAME_RENAMES", {}):
        name = config.STATUS_NAME_RENAMES[name]
        normalized["name"] = name

    if not tag and name in getattr(config, "LEGACY_TAG_MAP", {}):
        tag = config.LEGACY_TAG_MAP[name]

    base = None
    if tag and tag in getattr(config, "STATUS_TAGS", {}):
        base = config.STATUS_TAGS[tag]
    elif name and name in getattr(config, "STATUS_TAGS", {}):
        tag = name
        base = config.STATUS_TAGS[tag]

    if base:
        normalized["tag"] = tag
        normalized["name"] = normalized.get("name") or base.get("name", tag)
        normalized["type"] = normalized.get("type") or base.get("type", "debuff")
        normalized["severity"] = _coerce_int(normalized.get("severity", base.get("severity", 1)), base.get("severity", 1))
        if not isinstance(normalized.get("modifiers"), dict):
            normalized["modifiers"] = dict(base.get("modifiers", {}))
    else:
        if not tag:
            tag = name or "status"
        normalized["tag"] = tag
        if not normalized.get("name"):
            normalized["name"] = name or tag
        if not normalized.get("type"):
            normalized["type"] = STATUS_EFFECTS.get(name, {}).get("type", "debuff")
        if normalized.get("severity") is None:
            normalized["severity"] = STATUS_EFFECTS.get(name, {}).get("severity", 1)
        if not isinstance(normalized.get("modifiers"), dict):
            normalized["modifiers"] = _default_status_modifiers(normalized.get("type", "debuff"), _coerce_int(normalized.get("severity", 1), 1))

    normalized["duration"] = _normalize_duration(normalized.get("duration"), current_turn)
    if not isinstance(normalized.get("modifiers"), dict):
        normalized["modifiers"] = {}
    return normalized

def normalize_status_effects(raw_effects: Any, current_turn: Optional[int] = None) -> List[Dict[str, Any]]:
    if not raw_effects:
        return []
    if isinstance(raw_effects, dict):
        raw_effects = [raw_effects]
    if not isinstance(raw_effects, list):
        return []

    normalized: Dict[str, Dict[str, Any]] = {}
    for item in raw_effects:
        if isinstance(item, str):
            eff = _build_effect_from_name(item, current_turn)
        elif isinstance(item, dict):
            eff = _normalize_effect_dict(item, current_turn)
        else:
            continue
        tag = eff.get("tag") or eff.get("name") or ""
        if not tag:
            continue
        normalized[tag] = eff
    return list(normalized.values())

def get_status_effect_names(raw_effects: Any, current_turn: Optional[int] = None) -> List[str]:
    effects = normalize_status_effects(raw_effects, current_turn)
    names = []
    for eff in effects:
        if not isinstance(eff, dict):
            continue
        name = eff.get("name") or eff.get("tag")
        if name:
            names.append(str(name))
    return names

def format_status_effects(raw_effects: Any, current_turn: Optional[int] = None) -> str:
    names = get_status_effect_names(raw_effects, current_turn)
    return ", ".join(names)

def process_status_expiry(channel_id: str, user_id: str, current_turn: Optional[int] = None) -> List[str]:
    """Remove expired status effects (turns-based). Returns list of expired names."""
    p_data = domain_manager.get_participant_data(channel_id, user_id)
    if not p_data:
        return []

    if current_turn is None:
        world = domain_manager.get_world_state(channel_id)
        current_turn = _coerce_int(world.get("turn_index", 0), 0)

    raw = p_data.get("status_effects", [])
    effects = normalize_status_effects(raw, current_turn)
    expired = []
    active = []

    for eff in effects:
        duration = eff.get("duration", {}) if isinstance(eff, dict) else {}
        dtype = duration.get("type", "persistent")
        if dtype == "turns":
            start_turn = _coerce_int(duration.get("start_turn", current_turn), current_turn)
            value = _coerce_int(duration.get("value", 0), 0)
            if value > 0 and (current_turn - start_turn) >= value:
                expired.append(eff.get("name") or eff.get("tag") or "status")
                continue
        active.append(eff)

    if active != raw:
        p_data["status_effects"] = active
        domain_manager.save_participant_data(channel_id, user_id, p_data)

    return expired


def update_status_effect(
    user_data: Dict[str, Any],
    action: str,
    effect_name: str,
    effect_data: Optional[Dict[str, Any]] = None,
    current_turn: Optional[int] = None,
) -> Tuple[Dict[str, Any], str]:
    effects = normalize_status_effects(user_data.get("status_effects", []), current_turn)
    msg = ""

    def _find_index(name_or_tag: str) -> Optional[int]:
        for idx, eff in enumerate(effects):
            if not isinstance(eff, dict):
                continue
            if eff.get("tag") == name_or_tag or eff.get("name") == name_or_tag:
                return idx
        return None

    if action == "add":
        new_effect = None
        if effect_data and isinstance(effect_data, dict):
            new_effects = normalize_status_effects([effect_data], current_turn)
            new_effect = new_effects[0] if new_effects else None
        if not new_effect:
            new_effects = normalize_status_effects([effect_name], current_turn)
            new_effect = new_effects[0] if new_effects else None
        if not new_effect:
            return user_data, "⚠️ 상태 추가 실패"

        idx = _find_index(new_effect.get("tag") or new_effect.get("name"))
        if idx is None:
            effects.append(new_effect)
            sym = "✨" if new_effect.get("type") == "buff" else "⚠️"
            msg = f"{sym} **상태:** [{new_effect.get('name', effect_name)}]"
        else:
            effects[idx] = new_effect
            msg = f"⚠️ 이미 [{new_effect.get('name', effect_name)}] 상태"

    elif action == "remove":
        idx = _find_index(effect_name)
        if idx is None and effect_name in getattr(config, "LEGACY_TAG_MAP", {}):
            idx = _find_index(config.LEGACY_TAG_MAP[effect_name])
        if idx is not None:
            removed = effects.pop(idx)
            msg = f"✨ **해제:** [{removed.get('name', effect_name)}]"
        else:
            msg = f"⚠️ [{effect_name}] 없음"
        
    user_data["status_effects"] = effects
    return user_data, msg

# =========================================================
# [2026-10-15 C42] 타격칸 — 페이트 consequence. 마이너스 상태 전용 4칸(몸·마음 한 줄).
#   저장 = participants[uid]["strikes"] = [이름 ×4]("" = 빈 칸). 자리 번호가 칸 등급
#   (1·2 경상 · 3 중상 · 4 치명상). 체력은 활력·평형(스트레스)이 든다.
#   채우기·비우기의 주인 = 출력물 콜(산문 뒤) 하나 — orchestration._apply_strike_changes.
#   옛 status_effects 의 debuff 는 **읽을 때** 칸으로 보이고(읽기는 저장을 바꾸지 않는다),
#   첫 타격 쓰기에서 칸으로 옮겨지고 status_effects 엔 좋은 상태만 남는다.
#   4칸이 다 찬 뒤 들어오는 추가는 받지 않는다 — 넘친 피해를 어떻게 그릴지는 렌더 몫(RB 몸 줄).
# =========================================================
STRIKE_SLOTS = 4
STRIKE_TIER_KR = ("경상", "경상", "중상", "치명상")


def _names_list(raw: Any) -> List[str]:
    if isinstance(raw, str):
        raw = [raw]
    out: List[str] = []
    for x in (raw if isinstance(raw, list) else []):
        nm = str(x or "").strip()
        if nm and nm not in out:
            out.append(nm)
    return out


def _debuff_names(raw_effects: Any) -> List[str]:
    out: List[str] = []
    for eff in normalize_status_effects(raw_effects):
        if isinstance(eff, dict) and str(eff.get("type")) == "debuff":
            nm = str(eff.get("name") or eff.get("tag") or "").strip()
            if nm and nm not in out:
                out.append(nm)
    return out


def strike_view(p_data: Any) -> List[str]:
    """타격칸 4칸("" = 빈 칸). strikes 가 아직 없으면 옛 debuff 를 칸으로 보인다(저장 무변경)."""
    p = p_data if isinstance(p_data, dict) else {}
    raw = p.get("strikes")
    if isinstance(raw, list):
        track = [str(x or "").strip() for x in raw[:STRIKE_SLOTS]]
    else:
        track = _debuff_names(p.get("status_effects", []))[:STRIKE_SLOTS]
    return track + [""] * (STRIKE_SLOTS - len(track))


def strike_names(p_data: Any) -> List[str]:
    return [x for x in strike_view(p_data) if x]


def format_strikes(p_data: Any) -> str:
    """`타격 2/4: 부러진 손목 · 공황` / `타격 0/4` — 시스템 줄·패널·PC 정보가 같은 모양을 쓴다."""
    names = strike_names(p_data)
    return f"타격 {len(names)}/{STRIKE_SLOTS}" + (f": {' · '.join(names)}" if names else "")


def buff_effects(p_data: Any) -> List[Dict[str, Any]]:
    """status_effects 중 좋은 상태만(타격칸이 생긴 뒤 status_effects 의 몫)."""
    p = p_data if isinstance(p_data, dict) else {}
    return [e for e in normalize_status_effects(p.get("status_effects", []))
            if isinstance(e, dict) and str(e.get("type")) != "debuff"]


def is_known_debuff(name: Any) -> bool:
    """알려진 나쁜 상태 이름(부상·공포 등)인가. 모르는 이름은 False — 출력물 콜의 status_add 는
    좋은 상태 전용이라 모르는 이름은 좋은 상태로 받는다(orchestration._apply_status_changes)."""
    nm = str(name or "").strip()
    if not nm:
        return False
    tags = getattr(config, "STATUS_TAGS", {}) or {}
    tag = (getattr(config, "LEGACY_TAG_MAP", {}) or {}).get(nm, nm)
    if tag in tags:
        return str(tags[tag].get("type")) == "debuff"
    for t in tags.values():
        if isinstance(t, dict) and t.get("name") == nm:
            return str(t.get("type")) == "debuff"
    info = STATUS_EFFECTS.get(nm)
    return bool(info) and str(info.get("type")) == "debuff"


def apply_strike_changes(p_data: Any, add: Any = None, clear: Any = None) -> Tuple[Any, bool, List[str]]:
    """strike_add/strike_clear 적용. 비우기 먼저, 채우기는 가장 가벼운 빈 칸부터(비운 자리는 당기지 않는다),
    같은 이름은 한 칸, 4칸이 차면 더 안 받는다. Returns: (p_data, 바뀌었나, 안 받은 이름들)."""
    if not isinstance(p_data, dict):
        return p_data, False, []
    migrated = not isinstance(p_data.get("strikes"), list)
    track = strike_view(p_data)
    before = list(track)
    overflow: List[str] = []
    for nm in _names_list(clear):
        track = ["" if x == nm else x for x in track]
    for nm in _names_list(add):
        if nm in track:
            continue
        if "" not in track:
            overflow.append(nm)
            continue
        track[track.index("")] = nm
    changed = track != before
    if changed or (migrated and any(before)):
        p_data["strikes"] = track
        if migrated:
            p_data["status_effects"] = buff_effects(p_data)
        changed = True
    return p_data, changed, overflow


# [DEPRECATED] perform_check moved to UNE JudgmentEngine

# [2026-09-16 3차] get_status_summary 삭제 — 호출자 0(game_system 재export만), 사문.

# [2026-07-18 고아 삭제] get_recent_relationships — ai_memory.relationships 렌더는 시트/노트북 경로가 담당, 잉여 헬퍼 (dead_scan 참조0 확인, git 이력 복원 가능)

# =========================================================
# 시트 조각("패시브") — 2026-09-16 3차 (설계 relation_unify §10.2)
#   조각 = {name, desc, value:{edge: ±1}, origin: sheet|play}. 저장 = ai_memory.passives.
#   [2026-10-15 C42] 값은 강점/약점 부호 하나(이름 판단). 옛 roll_<type>·cost 는 config.normalize_fragment_value 가 부호로 접는다.
#   ⛔ add_passive(생산자 0)·get_passives_for_context(→ passives_txt, 읽는 곳 0) 삭제.
#   tags·theory_links·acquired_at·modifiers 는 저장하지 않는다 — 모든 쓰기 경로가 normalize_fragment 를 지난다.
# =========================================================

# 조각 채택·교체 계수(프로세스 수명). 채택 안 된 후보는 여기 숫자로만 남는다.
FRAGMENT_STATS: Dict[str, int] = {"adopted": 0, "replaced": 0, "no_quote": 0, "sheet_locked": 0, "invalid": 0}


def normalize_fragment(raw: Any, origin: str = "sheet") -> Optional[Dict[str, Any]]:
    """어떤 모양이 들어와도 조각 네 칸으로 접는다. 이름 없으면 None.

    옛 `modifiers.judgment_<type>`은 입력 쪽에서만 `value.roll_<type>`으로 옮겨 읽는다(저장은 새 모양뿐)."""
    if isinstance(raw, str):
        raw = {"name": raw}
    if not isinstance(raw, dict):
        return None
    name = str(raw.get("name") or "").strip()
    if not name:
        return None
    val_in = dict(raw.get("value")) if isinstance(raw.get("value"), dict) else {}
    old_mods = raw.get("modifiers")
    if isinstance(old_mods, dict):
        for k, v in old_mods.items():
            k = str(k)
            if k.startswith("judgment_") and f"roll_{k[9:]}" not in val_in:
                val_in[f"roll_{k[9:]}"] = v
    org = str(raw.get("origin") or "").strip()
    if org not in config.FRAGMENT_ORIGINS:
        org = origin if origin in config.FRAGMENT_ORIGINS else "sheet"
    return {"name": name, "desc": str(raw.get("desc") or "").strip(),
            "value": config.normalize_fragment_value(val_in), "origin": org}


def merge_fragments(existing: Any, incoming: Any, origin: str) -> Tuple[List[Dict[str, Any]], Dict[str, int]]:
    """이름 기준 병합. 같은 이름이 origin=sheet 면 **incoming origin=play 는 무시**(desc·value 교체 금지).

    Returns: (새 목록, {"added", "replaced", "sheet_locked"}). 기존 항목도 새 모양으로 접힌다."""
    out: List[Dict[str, Any]] = []
    for e in (existing if isinstance(existing, list) else []):
        f = normalize_fragment(e, "sheet")
        if f and not any(x["name"] == f["name"] for x in out):
            out.append(f)
    st = {"added": 0, "replaced": 0, "sheet_locked": 0}
    for raw in (incoming if isinstance(incoming, list) else []):
        f = normalize_fragment(raw, origin)
        if not f:
            continue
        idx = next((i for i, x in enumerate(out) if x["name"] == f["name"]), None)
        if idx is None:
            out.append(f)
            st["added"] += 1
        elif out[idx]["origin"] == "sheet" and f["origin"] == "play":
            st["sheet_locked"] += 1
        else:
            out[idx] = f
            st["replaced"] += 1
    return out, st


# =========================================================
# INVENTORY 읽기 (판정 앵커)
# [삭제 2026-10-23 C61] create_inventory_item · migrate_notebook_to_inventory · add_inventory_item ·
#   remove_inventory_item · get_inventory_for_context — 거울·구조화 인벤 잔재. 호출자는 거울 재구축(삭제)·
#   une 앵커(sojipin_anchor 로 교체)뿐이었고 나머지 셋은 07-06 감사 때부터 호출 0.
# =========================================================

def get_inventory_items(user_data: Optional[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """구조화 아이템 리스트 반환 (modifier 계산용).
    ai_memory.inventory 또는 직접 inventory 키에서 dict 형태의 아이템만 반환."""
    if not user_data:
        return []
    # narrative_anchors 직접 접근 또는 ai_memory 경유 모두 지원
    ai_mem = user_data.get("ai_memory", {})
    inv = ai_mem.get("inventory", user_data.get("inventory", []))
    result = []
    for item in inv:
        if isinstance(item, dict):
            result.append(item)
        elif isinstance(item, str):
            result.append({"name": item, "tags": [], "qty": 1})
    return result


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
    cfg = types.GenerateContentConfig(system_instruction=text_resources.ANALYSIS_FRAME,   # [2026-10-06 분석 틀] 유저 턴 mandate·확인·"Proceed." 대신
                                      response_mime_type="application/json", temperature=0.1, safety_settings=config.SAFETY_SETTINGS)

    for _ in range(config.MAX_RETRY_COUNT):
        try:
            resp = await client.aio.models.generate_content(
                model=model_id,
                contents=[
                    types.Content(role="user", parts=[types.Part(text=full_prompt)]),
                ],
                config=cfg
            )
            if resp and resp.text:
                clean = re.sub(r"```(json)?", "", resp.text).strip().strip("`")
                # Using json.loads for safety
                import json
                try:
                    return json.loads(clean)
                except json.JSONDecodeError:
                    # [2026-08-01] 공용 수리기 경유. 모델이 JSON 값 뒤에 해설을 다는 버릇
                    # (V4=괄호 / GLM=엠대쉬 / 스트레이 콜론)을 여기서도 놓치고 있었다.
                    # 수리에 성공하면 구조를 살리고, 실패해야 비로소 원문 폴백.
                    try:
                        import bot_utils as _bu
                        return json.loads(_bu.repair_json(_bu.clean_json_text(clean)))
                    except Exception:
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
# V7: MENTAL SYSTEM
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
    """평형 단계 정보를 반환합니다."""
    for stage_id, info in config.COMPOSURE_STAGES.items():
        low, high = info["range"]
        if low <= value < high:
            return info
    return config.COMPOSURE_STAGES[0]


def get_vigor_composure_text(p_data: Dict[str, Any],
                             channel_id: str = "", user_id: str = "") -> str:
    """Returns "활력 85 | 평형 70" format.

    [2026-08-18 Phase 2.5] 활력의 정본은 레지스트리다. channel_id 를 주면 그쪽을 읽고,
    안 주면 p_data 의 옛 자리로 폴백한다 — 이 함수는 채널 문맥 없이 불리는 자리가 있어서
    (game_system 재수출) 인자를 필수로 못 만든다.
    """
    mem = p_data.get("ai_memory", {})
    vigor = mem.get("vigor", mem.get("mental", {"value": 100}))
    composure = mem.get("composure", {"value": 100})
    v_val = vigor.get("value", 100)
    c_val = composure.get("value", 100)
    if channel_id:
        # [2026-09-06 P8b] 평형도 레지스트리 소유 — 두 축이 같은 문을 쓴다(표기 무변경).
        try:
            import custom_vars as _cv_gc
            v_val = _cv_gc.vigor_value(channel_id, user_id, mem)
            c_val = _cv_gc.composure_value(channel_id, user_id, mem)
        except Exception:
            pass
    return f"활력 {v_val} | 평형 {c_val}"


# update_mental 제거 (2026-07-06 감사+트라우마 폐지): V7 단일 멘탈 시스템의 유물,
# 호출자 0. 자체 트라우마 각성(붕괴+회복→90 리셋+영구 Trauma 패시브)을 품고 있었음.
# 현행은 vigor/composure 2축(vigor_composure_module)이 전담.

# [2026-08-11 비일상적응도 삭제] calculate_adaptation_pct / calculate_adaptation_percentage /
# get_abnormal_context 제거 — 노출 카운트를 올리는 쓰기 경로(apply_abnormal_impact)가 이미
# 사라져 abnormal_exposure는 영구 빈 딕셔너리였음. 읽기측(get_status_summary 적응도 줄,
# !정보 게이지)도 함께 철거. 복원은 git 이력.


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
    """연대기 내보내기 — 소스 통합 (2026-08-11).

    소스 2곳:
      ① quest_board["lore"] — 레거시 연대기. append-only → 기존 인덱스 커서(last_chronicle_idx) 유지.
      ② domain["chronicles"] — 자동(발효 3회마다)/수동(!연대기) 연대기. 10개 롤링이라
         인덱스가 못 쓰임 → 타임스탬프 워터마크(last_chronicle_export_ts)로 증분 추적.
    구판은 ①만 읽어 ②를 내보내지 못했다(연대기 3계통 분단). 이사(내보내기)가 목적인
    기능이므로 ②가 본진이다.
    """
    board = _get_board(channel_id)
    lore_entries = board.get("lore", [])
    domain = domain_manager.get_domain(channel_id)
    chronicle_entries = domain.get("chronicles", [])

    lore_start_idx = 0
    ts_watermark = 0.0
    mode_text = "전체"

    if incremental:
        lore_start_idx = domain_manager.get_last_chronicle_idx(channel_id)
        ts_watermark = domain_manager.get_last_chronicle_export_ts(channel_id)
        mode_text = "증분"

    _TYPE_TITLES = {"auto": "자동 연대기", "session_summary": "세션 연대기"}
    items = []
    n_board = 0
    n_chron = 0
    for entry in lore_entries[lore_start_idx:]:
        items.append({
            "title": entry.get("title", "Untitled"),
            "content": entry.get("content", ""),
            "timestamp": entry.get("timestamp", 0) or 0,
            "unresolved": "",
        })
        n_board += 1
    max_chron_ts = 0.0
    for entry in chronicle_entries:
        ts = entry.get("timestamp", 0) or 0
        if incremental and ts <= ts_watermark:
            continue
        items.append({
            "title": _TYPE_TITLES.get(entry.get("type", ""), "연대기"),
            "content": entry.get("content", ""),
            "timestamp": ts,
            "unresolved": entry.get("unresolved", "") or "",
        })
        n_chron += 1
        if ts > max_chron_ts:
            max_chron_ts = ts

    # [2026-10-26 C80] ③ 연대기 장(deep_memory 행 data["chapters"]) — 롤링 없이 쌓이므로 증분은 장 id 워터마크
    #   (id 는 만든 순서로 1씩 큰다. created_turn 은 배경 큐가 밀리면 두 장이 같은 턴을 가질 수 있다).
    #   전체 내보내기에는 0장(옛 deep 서사, 얼린 것)도 맨 앞에 싣는다. 루트 chronicles(②)는 레거시 장으로 위에서 그대로.
    _v2_layers = bool(getattr(config, "MEMORY_LAYERS_V2", False))
    n_chap = 0
    chapters = []
    max_chap_id = 0
    id_watermark = 0
    if _v2_layers:
        import fermentation
        _deep_nar, _deep_data = domain_manager.get_deep_memory(channel_id)
        chapters = [c for c in fermentation.get_chapters(_deep_data) if not c.get("legacy")]
        if incremental:
            id_watermark = domain_manager.get_last_chronicle_export_chapter(channel_id)
        elif isinstance(_deep_nar, str) and _deep_nar.strip():
            items.append({"title": "0장 · 이전 기록", "content": _deep_nar.strip(), "timestamp": 0, "unresolved": ""})
        for ch in chapters:
            cid = int(ch.get("id") or 0)
            if incremental and cid <= id_watermark:
                continue
            items.append({
                "title": fermentation.chapter_title(ch),
                "content": str(ch.get("text") or ""),
                "timestamp": float(ch.get("created_at") or 0),
                "unresolved": "",
            })
            n_chap += 1
            max_chap_id = max(max_chap_id, cid)

    if not items:
        if incremental and (lore_entries or chronicle_entries or chapters):
            return "", "✅ 새로운 연대기 기록이 없습니다."
        return "", "⚠️ 기록된 연대기가 없습니다."

    items.sort(key=lambda x: x.get("timestamp", 0))

    lines = []
    lines.append(f"# Chronicle Export ({mode_text}) - {channel_id}")
    lines.append(f"Date: {time.strftime('%Y-%m-%d %H:%M')}")
    lines.append(f"Items: {len(items)} (기록보드 {n_board} / 연대기 {n_chron}"
                 + (f" / 장 {n_chap}" if _v2_layers else "") + ")\n")

    for entry in items:
        date_str = time.strftime('%Y-%m-%d', time.localtime(entry.get('timestamp', 0)))
        lines.append(f"## [{date_str}] {entry['title']}")
        lines.append(entry["content"])
        if entry.get("unresolved"):
            lines.append(f"\n> 미해결: {entry['unresolved']}")
        lines.append("\n" + "="*30 + "\n")

    if incremental:
        domain_manager.set_last_chronicle_idx(channel_id, len(lore_entries))
        if max_chron_ts > ts_watermark:
            domain_manager.set_last_chronicle_export_ts(channel_id, max_chron_ts)
        if max_chap_id > id_watermark:
            domain_manager.set_last_chronicle_export_chapter(channel_id, max_chap_id)

    return "\n".join(lines), f"✅ **연대기 추출 완료** ({mode_text}, {len(items)} items)"
