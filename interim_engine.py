"""
Lorekeeper V10 Sprint 4 — 막간 장부 (Interim Ledger) 전진 엔진

장면 밖 NPC의 행적을 환각이 아니라 기록으로 만든다.
spec: 파티쳇수정/v10_sprint4_interim_ledger_spec.md

원칙:
- 침묵: 선제 발화 0. 이 모듈은 말하지 않는다, 준비만 한다.
- 게으른 재구성: 루프 없음. 턴 진입 시 일괄 따라잡기 (밀리초).
- 순수 코드: LLM 콜 0. 같은 입력 = 같은 장부 (결정론 — random/salted hash 금지).
- 시간: 전진 폭 = f(게임 시간 경과). 실시간 부재는 무관 ("시간은 턴이 민다").
- 카노바초: 뼈대 사실만, '어떻게'는 절대 안 적는다.
- 사자 보고: 흔적(traces)이 보고의 형식. 명사구만 — 문장 주면 렌더가 낭독한다.
- 間: "별일 없음"도 유효한 기록 (기본값).
- 체호프: pursue는 진척만. 막간에서 플롯 해소 절대 금지.
- 데이터/지시 분리: 지시는 블록 헤더 1회. 항목 내부에 명령형 금지.
"""

import json
import logging
import hashlib
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("InterimEngine")

# 행위 어휘 (state_guards.LEDGER_ACTS와 동기)
_ACTS = ("move", "routine", "contact", "pursue", "still")

# 전진 폭: 게임시간 경과(분) → 표면화 엔트리 수 가산
_BANDS = [(30, 0), (180, 1), (1440, 2)]  # 초과 시 3 (상한)

# 흔적 템플릿 — 영어 텔레그래픽 + **탈사물화(de-objectified)** (2026-06-11 개정).
# 1) echo 방어: 한국어 구체 문구는 Pro가 베낌 (기력/평정 리터럴 점화 동계열) → 영어 재료.
# 2) 실재화 방어 (사용자 원칙: "노출되면 실제 물건이 된다") — 주입 토큰은 반드시 밟히므로,
#    사물 명사(coat/notes/tool)는 세계에 그 사물을 발명한다. 템플릿은 **지각 범주만** 제공하고
#    구체 사물 선택은 렌더 시점 Pro가 장면·로어의 기존 사물에서 (K 규칙: 질감 발명의 합법 자리).
#    사물 명사는 캐논 수확(기등장 사물 재호출) 도입 후에만 흔적에 진입 가능.
# 문장/명령형 절대 금지 (데이터/지시 분리 철칙).
_TRACE_TEMPLATES: Dict[str, List[List[str]]] = {
    "move": [
        ["signs of recent return", "traces of having been outside"],
        ["air of arrival not yet settled", "small disorder of coming back"],
    ],
    "routine": [
        ["marks of work recently done", "rhythm of an ordinary day about them"],
        ["faint weariness of daily labor", "manner of someone mid-routine"],
    ],
    "contact": [
        ["air of a recent exchange", "fewer words than usual"],
        ["subtly shifted manner between them", "something recently discussed, unshared"],
    ],
    "pursue": [
        ["signs of something privately looked into", "preoccupied air"],
        ["faint fatigue of asking around", "attention partly elsewhere"],
    ],
    "still": [
        [],  # 間 — 흔적 없음도 유효
        ["nothing out of place"],
    ],
}

_MOOD_BY_TRAJECTORY = {
    "declining": "a sunken undertone",
    "improving": "a visibly eased air",
}


# =========================================================
# 순수 함수부 (단독 스모크 대상)
# =========================================================

def game_minutes(gt: Optional[Dict[str, Any]]) -> Optional[int]:
    """game_time dict → 근사 절대분 (월=30일 근사 — 구간 판정용이라 충분)."""
    if not isinstance(gt, dict):
        return None
    try:
        total = ((int(gt.get("year", 1)) * 12 + int(gt.get("month", 1))) * 30
                 + int(gt.get("day", 1))) * 1440
        return total + int(gt.get("hour", 12)) * 60 + int(gt.get("minute", 0))
    except (TypeError, ValueError):
        return None


def elapsed_band(prev_gt: Optional[Dict], curr_gt: Optional[Dict]) -> int:
    """게임시간 경과 → 전진 폭 0~3. 측정 불가/역행 = 0 (보수)."""
    a, b = game_minutes(prev_gt), game_minutes(curr_gt)
    if a is None or b is None or b <= a:
        return 0
    diff = b - a
    for limit, band in _BANDS:
        if diff < limit:
            return band
    return 3


def _stable_idx(key: str, n: int) -> int:
    """결정론적 템플릿 선택 — python hash()는 salt 때문에 금지."""
    if n <= 0:
        return 0
    return int(hashlib.md5(key.encode("utf-8")).hexdigest(), 16) % n


def _pick_traces(act: str, npc: str, day_key: str) -> List[str]:
    pool = _TRACE_TEMPLATES.get(act, [[]])
    return list(pool[_stable_idx(f"{npc}|{day_key}|{act}", len(pool))])


def select_act(npc: str, *, schedule_hint: str = "", want: str = "",
               tension: int = 0, contact_target: str = "",
               timeslot_shift: bool = False) -> Tuple[str, str, str]:
    """행위 선택 (결정론, spec §3 우선순위). Returns (act, summary, route).

    summary = 완료상 한 줄, 무시각. route = "A→B" (해당 시).
    pursue는 진척 표현만 — 해소/결말 어휘 금지 (체호프)."""
    # summary도 영어 텔레그래픽 (echo 방어 — 한국어 완성구는 베껴짐).
    # want/schedule_hint/이름은 데이터 패스스루 (DAI 출처 그대로).
    if schedule_hint:
        return "routine", f"spent the interim on usual duties around: {schedule_hint.strip()}", ""
    if want:
        # 진척만 — resolved/solved/found 계열 어휘 금지 (체호프: 막간 해소 금지).
        return "pursue", f"quietly made small headway regarding: {want.strip()} — nothing settled", ""
    if contact_target and tension >= 60:
        return "contact", f"seems to have exchanged words with {contact_target}", f"→{contact_target}"
    if timeslot_shift:
        return "move", "back at their own quarters by now", "→quarters"
    return "still", "nothing notable; ordinary hours passed", ""


def build_block(entries: List[Dict[str, Any]]) -> str:
    """주입 블록 생성. 지시는 헤더 1회뿐 — 항목은 순수 사실 (데이터/지시 분리 철칙)."""
    if not entries:
        return ""
    lines = [
        "<Interim_Ledger>",
        "[GROUND_TRUTH] Offscreen facts since last scene. Surface only as traces the PC "
        "could perceive; NPC behavior may reflect them. PC narration must not know "
        "unwitnessed interim.",
    ]
    for e in entries:
        line = f"- {e['npc_name']}: {e['summary']}"
        if e.get("motive"):
            line += f" (motive: {e['motive']})"
        extras = list(e.get("traces") or [])
        if e.get("mood_delta"):
            extras.append(e["mood_delta"])
        if extras:
            line += f" | perceivable traces: {', '.join(extras)}"
        lines.append(line)
    lines.append("</Interim_Ledger>")
    return "\n".join(lines)


# =========================================================
# 조립부 (도메인 접근)
# =========================================================

# 턴 스코프 스태시 — reconstruct(orchestration_context)가 쓰고, 같은 턴의
# anchors 빌더(une_facade→Theoria)가 읽는다. 매 재구성 시작 시 클리어.
_last_block: Dict[str, str] = {}


def get_last_block(channel_id: str) -> str:
    """이번 턴 재구성 블록 (없으면 ''). Theoria anchors용."""
    return _last_block.get(channel_id, "")

def _extract_want(psyche: Any) -> str:
    """DAI psyche에서 want 후보 추출 — 스키마 관용.

    [2026-06-12 공실률 실측 보정] 평면 want 키 적중 0/69 — Theoria 실스키마는
    psyche_states[npc] = {psyche:{active_needs[...],...}, soma, relation, deep_read, resurfacing}.
    psyche.active_needs(행동 지배 욕구, 100% 적중)가 want의 실측 등가물. 평면 키는 미래 호환 폴백."""
    if not isinstance(psyche, dict):
        return ""
    # 평면 키 (미래 호환)
    for key in ("want", "goal", "desire", "core_want", "current_want"):
        v = psyche.get(key)
        if isinstance(v, str) and v.strip():
            return v.strip()[:40]
    # 실측 스키마: psyche.active_needs 첫 항목
    inner = psyche.get("psyche")
    if isinstance(inner, dict):
        needs = inner.get("active_needs")
        if isinstance(needs, list) and needs:
            n = needs[0]
            if isinstance(n, str) and n.strip():
                return n.strip()[:40]
    return ""


def _recent_npcs_from_dai(channel_id: str, turns: int = 10):
    """최근 N턴 DAI 스캔. Returns (등장 인물 순서, NPC별 마지막 목격 psyche, 직전 턴 등장 인물).

    [2026-06-12 첫 발화 결함 수정] 직전 턴 등장 인물 = 무대 위 — 막간 장부 대상에서 제외해야 함
    (걔들의 막간은 산문이 직접 렌더; 첫 실전에서 케인 위에서 자던 리리스·비비가 routine으로 기록됨).
    부재자의 want는 최신 턴이 아니라 **마지막 목격 턴**의 psyche에서."""
    import sqlite_store
    logs = sqlite_store.read_dai_logs(channel_id, turns)  # 오래된→최신
    names: List[str] = []
    last_psyche: Dict[str, Any] = {}
    on_scene: set = set()
    for idx, (_turn, dai) in enumerate(logs):
        is_last = (idx == len(logs) - 1)
        ps = dai.get("psyche_states")
        for key in ("npc_attitudes", "psyche_states"):
            d = dai.get(key)
            if isinstance(d, dict):
                for n in d.keys():
                    if n not in names:
                        names.append(n)
                    if is_last:
                        on_scene.add(n)
        if isinstance(ps, dict):
            for n, p in ps.items():
                if isinstance(p, dict):
                    last_psyche[n] = p  # 뒤(최신)가 덮어씀 = 마지막 목격
    return names, last_psyche, on_scene


def reconstruct_interim(channel_id: str) -> Optional[str]:
    """턴 진입 시 막간 재구성. 플래그 OFF/경과 부족/대상 없음 → None (완전 무동작).

    Returns: Slot 29에 덧붙일 블록 문자열 or None."""
    import config
    _last_block.pop(channel_id, None)  # 턴 스코프 클리어 (이전 턴 블록 잔존 방지)
    if not getattr(config, "V10_INTERIM_LEDGER", False):
        return None
    try:
        import domain_manager
        import sqlite_store
        import state_guards

        # 1. 게임시간 경과 측정 (마지막 히스토리 game_time vs 현재 world)
        history = domain_manager.get_history(channel_id)
        prev_gt = None
        for entry in reversed(history):
            if isinstance(entry.get("game_time"), dict):
                prev_gt = entry["game_time"]
                break
        world = domain_manager.get_world_state(channel_id) or {}
        curr_gt = {k: world.get(k) for k in ("year", "month", "day", "hour", "minute")}
        band = elapsed_band(prev_gt, curr_gt)
        if band <= 0:
            return None

        # 2. 대상 NPC 선정: 최근 DAI 등장 ∪ 스케줄 보유 — 단, **장면 밖 한정**:
        #    직전 턴 등장 인물(무대 위)·PC·미등록 제외. 무대 위 인물의 막간은 산문 몫.
        registered = domain_manager.get_npcs(channel_id) or {}
        participants = domain_manager.get_domain(channel_id).get("participants", {})
        pc_masks = {p.get("mask", "") for p in participants.values()}
        session_mem = domain_manager.get_domain(channel_id).get("ai_session_memory", {}) or {}
        npc_summaries = session_mem.get("npc_summaries", {}) or {}

        recent_names, last_psyche, on_scene_raw = _recent_npcs_from_dai(channel_id)
        # on_scene을 등록 키로 정규화 (DAI 이름 ↔ 레지스트리 키 별칭 차 흡수)
        on_scene_keys = set()
        for n in on_scene_raw:
            k = domain_manager._find_npc_key(registered, n)
            if k:
                on_scene_keys.add(k)

        candidates: List[str] = []
        for n in recent_names + list(npc_summaries.keys()):
            key = domain_manager._find_npc_key(registered, n)
            if key and key not in candidates and key not in pc_masks and key not in on_scene_keys:
                candidates.append(key)
        cap = min(5, band + 2)
        selected = candidates[:cap]
        if not selected:
            return None

        # 3. 입력 수집 + 행위 선택
        try:
            import entity_relations
            strongest = entity_relations.get_strongest_relations(channel_id, top_n=5) or []
        except Exception:
            strongest = []
        pair_of: Dict[str, str] = {}
        for rel in strongest:
            s, t = rel.get("source"), rel.get("target")
            if s in selected and t in selected and s not in pair_of:
                pair_of[s] = t

        # want 소스: NPC별 **마지막 목격** psyche (부재자는 최신 턴에 없으므로 — 위에서 수집).
        # DAI 원이름 → 등록 키 정규화 (별칭 차 흡수)
        psyche_by_key: Dict[str, Any] = {}
        for _rn, _p in last_psyche.items():
            _k = domain_manager._find_npc_key(registered, _rn)
            if _k and _k not in psyche_by_key:
                psyche_by_key[_k] = _p
        latest_psyche = psyche_by_key

        night = world.get("time_slot") in ("밤", "심야", "새벽")
        day_key = f"{curr_gt.get('day')}-{curr_gt.get('hour')}"

        entries: List[Dict[str, Any]] = []
        for npc in selected:
            att = domain_manager.get_npc_attitude(channel_id, npc) or {}
            schedule = npc_summaries.get(npc) if isinstance(npc_summaries.get(npc), str) else ""
            want = _extract_want(latest_psyche.get(npc))
            act, summary, route = select_act(
                npc,
                schedule_hint=schedule or "",
                want=want,
                tension=int(att.get("tension", 0) or 0),
                contact_target=pair_of.get(npc, ""),
                timeslot_shift=night,
            )
            entry = {
                "npc_name": npc, "act": act, "summary": summary,
                "motive": want if act == "pursue" else "",
                "route": route,
                "traces": _pick_traces(act, npc, day_key),
                "mood_delta": _MOOD_BY_TRAJECTORY.get(att.get("trajectory", ""), ""),
                "game_span": f"band{band}",
                "consumed": True,  # 생성 즉시 이번 턴에 주입됨
            }
            clean = state_guards.validate_ledger_write(entry)
            if clean is not None:
                sqlite_store.append_ledger(channel_id, clean)
                entries.append(clean)

        if not entries:
            return None
        block = build_block(entries)
        _last_block[channel_id] = block  # 같은 턴 Theoria anchors가 읽음
        logger.info(f"[Interim] {len(entries)} entries (band={band}): "
                    + ", ".join(f"{e['npc_name']}={e['act']}" for e in entries))
        return block
    except Exception as e:
        logger.warning(f"[Interim] 재구성 실패 (무시, 턴 정상 진행): {e}")
        return None
