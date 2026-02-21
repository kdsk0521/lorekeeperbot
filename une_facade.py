"""
Lorekeeper - Universal Narrative Engine (UNE) Facade
The main entry point for the UNE engine.
"""

import logging
import random
from typing import Dict, Any, List, Tuple

from orchestration_context import GameContext
from waterfall_pipeline import WaterfallPipeline
import domain_manager
import game_character
import game_world

logger = logging.getLogger("UNE")

def _pick(items):
    return random.choice(items) if items else ""

# =========================================================
# Genre MC Move Tables (Phase 8)
# Key: (position_tier, result) → Korean MC move text
# =========================================================
GENRE_MC_MOVES = {
    "cosmic_horror": {
        ("desperate", "critical_failure"): "현실이 무너진다 — 돌이킬 수 없는 진실이 열린다",
        ("desperate", "failure"): "공포가 실체가 된다 — 되돌릴 수 없는 결과",
        ("desperate", "partial"): "살아남았지만 대가가 크다 — 세계관이 흔들린다",
        ("desperate", "success"): "절망 속 한 줄기 빛 — 그러나 그 빛도 의심스럽다",
        ("desperate", "critical_success"): "불가능한 기적 — 대가는 아직 청구되지 않았다",
        ("risky", "critical_failure"): "최악이 현실이 된다 — 공포가 구체화한다",
        ("risky", "failure"): "상황이 악화된다 — 새로운 공포가 모습을 드러낸다",
        ("risky", "partial"): "일부 성공했지만 — 무언가 알아서는 안 될 것을 알게 되었다",
        ("risky", "success"): "위험을 넘겼다 — 잠시간의 안전",
        ("risky", "critical_success"): "선명한 통찰 — 공포의 정체를 직시하고 살아남았다",
        ("controlled", "critical_failure"): "예상치 못한 반전 — 안전이 무너진다",
        ("controlled", "failure"): "작은 실패가 균열을 만든다 — 불안의 씨앗",
        ("controlled", "partial"): "부분적 성과 — 미묘한 불편함이 남는다",
        ("controlled", "success"): "깔끔한 성공 — 평온이 유지된다",
        ("controlled", "critical_success"): "완벽한 대응 — 공포를 이해의 영역으로 끌어왔다",
    },
    "romance": {
        ("desperate", "critical_failure"): "마음이 드러난 순간, 취약성만 남았다 — 상처가 깊다",
        ("desperate", "failure"): "진심이 전해지지 않았다 — 오해만 깊어진다",
        ("desperate", "partial"): "감정이 닿았지만 타이밍이 아니었다 — 여운이 남는다",
        ("desperate", "success"): "절박한 진심이 통했다 — 관계가 급격히 움직인다",
        ("desperate", "critical_success"): "운명적 순간 — 모든 벽이 무너진다",
        ("risky", "critical_failure"): "결정적 오해가 발생한다 — 관계가 흔들린다",
        ("risky", "failure"): "감정의 엇갈림 — 라이벌이나 장애물이 선명해진다",
        ("risky", "partial"): "마음은 전했지만 완전하지 않다 — 불안이 남는다",
        ("risky", "success"): "감정이 전해졌다 — 관계가 한 걸음 나아간다",
        ("risky", "critical_success"): "완벽한 순간 — 두 사람만의 세계가 열린다",
        ("controlled", "critical_failure"): "안전한 거리에서 예상치 못한 감정이 터진다",
        ("controlled", "failure"): "소소한 실수 — 그러나 감정의 여운",
        ("controlled", "partial"): "일상적 교류 — 미세한 설렘",
        ("controlled", "success"): "자연스러운 친밀함 — 편안한 진전",
        ("controlled", "critical_success"): "완벽한 하모니 — 서로를 깊이 이해하는 순간",
    },
    "comedy": {
        ("desperate", "critical_failure"): "전부 들통 — 숨긴 모든 것이 한꺼번에 공개된다",
        ("desperate", "failure"): "상황이 완전히 통제를 벗어났다 — 그러나 웃기다",
        ("desperate", "partial"): "겨우 수습했지만 새 거짓말이 필요하다",
        ("desperate", "success"): "기적적 수습 — 아무도 믿지 못할 행운",
        ("desperate", "critical_success"): "모든 거짓말이 우연히 진실이 된다",
        ("risky", "critical_failure"): "최악의 타이밍에 최악의 사람이 등장한다",
        ("risky", "failure"): "소동이 커진다 — 목격자가 늘어난다",
        ("risky", "partial"): "절반만 성공 — 나머지 절반이 문제를 만든다",
        ("risky", "success"): "깔끔한 수습 — 잠깐의 안도",
        ("risky", "critical_success"): "예상치 못한 방식으로 완벽하게 해결된다",
        ("controlled", "critical_failure"): "확실한 상황에서 황당한 실패",
        ("controlled", "failure"): "사소한 실수가 나비효과를 일으킨다",
        ("controlled", "partial"): "되긴 됐는데 뭔가 어색하다",
        ("controlled", "success"): "순조로운 진행 — 평화로운 한 때",
        ("controlled", "critical_success"): "모든 것이 완벽하게 맞아떨어진다 — 기분 좋은 놀라움",
    },
    "noir": {
        ("desperate", "critical_failure"): "덫이 닫힌다 — 탈출구 없음",
        ("desperate", "failure"): "진실이 무기가 되어 돌아온다 — 배신의 대가",
        ("desperate", "partial"): "살아남았지만 빚이 생겼다 — 누군가에게 약점을 잡혔다",
        ("desperate", "success"): "어둠 속에서 한 수 앞을 내다봤다 — 위험한 도박의 성공",
        ("desperate", "critical_success"): "모든 퍼즐이 맞아떨어진다 — 그러나 그 대가는?",
        ("risky", "critical_failure"): "증거가 뒤바뀐다 — 사냥꾼이 사냥감이 된다",
        ("risky", "failure"): "수사선이 꼬인다 — 새로운 용의자, 새로운 의혹",
        ("risky", "partial"): "일부 진실에 접근했지만 — 더 큰 비밀이 있다",
        ("risky", "success"): "한 겹을 벗겼다 — 진실에 한 발 더 가까이",
        ("risky", "critical_success"): "결정적 단서 확보 — 퍼즐의 핵심 조각",
        ("controlled", "critical_failure"): "안전하다고 생각한 곳에서 칼이 날아온다",
        ("controlled", "failure"): "사소한 실수가 흔적을 남긴다",
        ("controlled", "partial"): "조용한 진전 — 그러나 감시의 눈이 있다",
        ("controlled", "success"): "계획대로 — 아직은 주도권을 쥐고 있다",
        ("controlled", "critical_success"): "완벽한 수 — 상대방은 움직였다는 것조차 모른다",
    },
    "action": {
        ("desperate", "critical_failure"): "최악의 결과 — 치명적 부상 또는 장비 파괴",
        ("desperate", "failure"): "위기가 실체화된다 — 후퇴할 곳이 없다",
        ("desperate", "partial"): "살아남았지만 상처가 깊다 — 전투 능력 저하",
        ("desperate", "success"): "기사회생 — 절체절명에서의 역전",
        ("desperate", "critical_success"): "전설적 순간 — 불가능을 가능으로",
        ("risky", "critical_failure"): "전세가 역전된다 — 적이 주도권을 잡는다",
        ("risky", "failure"): "공격이 빗나간다 — 적이 반격 기회를 잡는다",
        ("risky", "partial"): "명중했지만 완전하지 않다 — 적도 반격한다",
        ("risky", "success"): "확실한 타격 — 전세가 유리해진다",
        ("risky", "critical_success"): "완벽한 일격 — 적을 압도한다",
        ("controlled", "critical_failure"): "방심의 대가 — 예상치 못한 반격",
        ("controlled", "failure"): "실수로 기회를 놓친다",
        ("controlled", "partial"): "무난한 성과 — 조금 부족하다",
        ("controlled", "success"): "깔끔한 처리 — 전문가다운 수행",
        ("controlled", "critical_success"): "압도적 우위 — 적이 전의를 상실한다",
    },
    "slice_of_life": {
        ("desperate", "critical_failure"): "최악의 타이밍에 모든 것이 엉킨다 — 관계에 금이 간다",
        ("desperate", "failure"): "진심이 전해지지 않았다 — 오해가 깊어진다",
        ("desperate", "partial"): "마음은 닿았지만 방식이 서툴렀다",
        ("desperate", "success"): "서투르지만 진심이 통했다 — 작은 기적",
        ("desperate", "critical_success"): "모든 것이 제자리를 찾는다 — 일상의 따뜻함",
        ("risky", "critical_failure"): "일상의 균형이 무너진다 — 익숙한 것이 낯설어진다",
        ("risky", "failure"): "사소한 것이 꼬인다 — 불편함이 쌓인다",
        ("risky", "partial"): "되긴 됐지만 아쉬움이 남는다",
        ("risky", "success"): "자연스럽게 잘 풀린다 — 소소한 성취",
        ("risky", "critical_success"): "예상치 못한 좋은 일 — 일상의 반짝임",
        ("controlled", "critical_failure"): "확실하다고 생각했는데 뜻밖의 변수",
        ("controlled", "failure"): "사소한 실수 — 웃어넘길 수 있는 정도",
        ("controlled", "partial"): "평범한 하루의 한 장면",
        ("controlled", "success"): "편안한 일상 — 모든 것이 순조롭다",
        ("controlled", "critical_success"): "완벽한 하루 — 일상이 빛나는 순간",
    },
}

def _get_genre_mc_move(genre: str, pos_tier: str, result: str) -> str:
    """장르별 MC Move를 반환. 매칭 없으면 빈 문자열."""
    genre_table = GENRE_MC_MOVES.get(genre, {})
    return genre_table.get((pos_tier, result), "")

def _to_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _position_tier(value: float) -> str:
    if value <= 0.25:
        return "desperate"
    if value <= 0.5:
        return "risky"
    return "controlled"


def _mc_move(position: str, result: str) -> str:
    matrix = {
        ("desperate", "critical_failure"): "Catastrophic - something irreversible happens.",
        ("desperate", "failure"): "Make the threat real - irreversible consequences.",
        ("desperate", "partial"): "Heavy price - gain what was sought but lose something.",
        ("desperate", "success"): "Dramatic turnaround - shining in the direst moment.",
        ("desperate", "critical_success"): "Miraculous reversal - transcendent moment.",
        ("risky", "critical_failure"): "Worst case unfolds - danger becomes reality.",
        ("risky", "failure"): "Escalate - a new danger reveals itself.",
        ("risky", "partial"): "Success with cost - complications follow.",
        ("risky", "success"): "Danger cleared - competent execution.",
        ("risky", "critical_success"): "Brilliant - impressive against the odds.",
        ("controlled", "critical_failure"): "Unexpected reversal - safety shatters.",
        ("controlled", "failure"): "Minor cost - a small setback.",
        ("controlled", "partial"): "Minor friction - less smooth than expected.",
        ("controlled", "success"): "Clean - smooth and effortless.",
        ("controlled", "critical_success"): "Overwhelming mastery - exceeds expectations.",
    }
    return matrix.get((position, result), "Outcome determined by world logic.")


def _collect_aspect_stance(aspects: Any) -> Tuple[List[str], List[str]]:
    if not isinstance(aspects, list):
        return [], []

    favorable: List[str] = []
    against: List[str] = []
    for aspect in aspects:
        if not isinstance(aspect, dict):
            continue
        name = str(aspect.get("name", "")).strip()
        if not name:
            continue
        stance = str(aspect.get("for_or_against", aspect.get("stance", ""))).strip().lower()
        if stance in ("for", "support", "positive", "pro"):
            favorable.append(name)
        elif stance in ("against", "oppose", "negative", "con"):
            against.append(name)
    return favorable, against


def _build_world_layer(bus) -> str:
    dai = bus.dai if isinstance(bus.dai, dict) else {}
    parts: List[str] = []

    pos = dai.get("position", {}) if isinstance(dai.get("position"), dict) else {}
    pos_value = _to_float(pos.get("value", 0.5), 0.5)
    pos_tier = _position_tier(pos_value)
    pos_labels = {
        "desperate": "desperate - stakes are lethal, one wrong move can end it.",
        "risky": "risky - danger is present, outcome uncertain.",
        "controlled": "controlled - situation favors the actor.",
    }
    parts.append(f"[Position] {pos_labels.get(pos_tier, pos_tier)}")
    if pos.get("reason"):
        parts.append(f"Why: {pos.get('reason')}")

    eff = dai.get("effect", {}) if isinstance(dai.get("effect"), dict) else {}
    eff_value = _to_float(eff.get("value", 0.5), 0.5)
    if eff_value >= 0.7:
        eff_label = "great - success changes the situation dramatically."
    elif eff_value >= 0.4:
        eff_label = "standard - meaningful but not decisive."
    else:
        eff_label = "limited - small gain even on success."
    parts.append(f"[Effect] {eff_label}")
    if eff.get("reason"):
        parts.append(f"Effect basis: {eff.get('reason')}")

    scene_type = str(dai.get("scene_type", "normal"))
    scene_map = {
        "normal": "Normal - standard interaction, observe and react.",
        "combat": "Combat - physicality, positioning, threat, consequences.",
        "social": "Social - reputation, leverage, hidden agendas.",
        "tension": "Tension - suspense, restricted information, slow reveal.",
        "intimate": "Intimate - emotion, subtlety, vulnerability, trust.",
        "exploration": "Exploration - curiosity, discovery, world-building.",
    }
    parts.append(f"[Scene] {scene_map.get(scene_type, scene_type)}")

    energy = str(dai.get("energy_direction", "steady"))
    energy_map = {
        "rising": "RISING - escalate tension and pacing.",
        "falling": "FALLING - breathing room and reflection.",
        "peak": "PEAK - climactic intensity.",
        "steady": "STEADY - maintain current rhythm.",
        "stagnant": "STAGNANT - break the pattern and introduce change.",
        "detonation": "DETONATION - everything erupts now.",
        "aftershock": "AFTERSHOCK - consequences settle in.",
    }
    parts.append(f"[Energy] {energy_map.get(energy, energy)}")

    attitudes = dai.get("npc_attitudes", {})
    if isinstance(attitudes, dict) and attitudes:
        lines: List[str] = []
        for name, data in attitudes.items():
            if not isinstance(data, dict):
                continue
            attitude = str(data.get("attitude", "neutral"))
            trajectory = str(data.get("trajectory", "stable"))
            reason = str(data.get("reason", "")).strip()
            line = f"- {name}: {attitude} ({trajectory})"
            if reason:
                line += f" | {reason}"
            lines.append(line)
        if lines:
            parts.append("[NPC States]\n" + "\n".join(lines))

    psyche = dai.get("psyche_states", {})
    if isinstance(psyche, dict) and psyche:
        lines = []
        for char_name, state in psyche.items():
            if not isinstance(state, dict):
                continue
            mental = state.get("mental") if isinstance(state.get("mental"), dict) else {}
            if not mental:
                mental = state.get("psyche") if isinstance(state.get("psyche"), dict) else {}
            soma = state.get("soma") if isinstance(state.get("soma"), dict) else {}
            desc = str(mental.get("descriptor", "")).strip()
            polyvagal = str(soma.get("polyvagal", "")).strip()
            if desc or polyvagal:
                lines.append(f"- {char_name}: {desc or 'n/a'} / body={polyvagal or 'n/a'}")
        if lines:
            parts.append("[Psyche]\n" + "\n".join(lines))

    needs_judgment = bool(dai.get("needs_judgment", False))
    action_meta = dai.get("action_meta", {}) if isinstance(dai.get("action_meta"), dict) else {}
    action_name = str(action_meta.get("action", "")).strip()
    if action_name:
        difficulty = str(action_meta.get("difficulty", "normal"))
        parts.append(f"[Action Reading] '{action_name}' - {difficulty}")
        if not needs_judgment:
            parts.append(f"No dice. Resolve by Position ({pos_tier}) and world logic.")

    chain = dai.get("narrative_chain", {})
    if isinstance(chain, dict) and chain:
        status = str(chain.get("chain_status", "OPEN"))
        proximity = int(_to_float(chain.get("conclusion_proximity", 0), 0))
        chain_lines = [f"[Narrative] chain={status}, proximity={proximity}%"]
        hook = str(dai.get("narrative_hook", "")).strip()
        if hook:
            chain_lines.append(f"Hook: {hook}")
        threads = chain.get("open_threads", [])
        if isinstance(threads, list) and threads:
            chain_lines.append("Threads: " + " | ".join(str(t) for t in threads[:3]))
        parts.append("\n".join(chain_lines))

    qflags = dai.get("quality_flags", {})
    if isinstance(qflags, dict):
        warnings: List[str] = []
        if qflags.get("convergence_warning"):
            warnings.append("CONVERGENCE: comfort without earning it.")
        if qflags.get("echo_warning"):
            warnings.append("ECHO: NPC mirrors PC instead of independent response.")
        if qflags.get("stagnation_warning"):
            warnings.append("STAGNATION: scene pattern stayed flat too long.")
        if warnings:
            parts.append("[Quality Alerts]\n" + "\n".join(f"- {w}" for w in warnings))

    return "[World]\n" + "\n".join(parts)


def _build_events_layer(context, bus) -> str:
    parts: List[str] = []

    anomaly = bus.anomaly if isinstance(bus.anomaly, dict) else {}
    if anomaly.get("triggered"):
        tag = anomaly.get("tag") or "anomaly"
        intensity = anomaly.get("intensity", "")
        polarity = anomaly.get("polarity", "")
        block = [f"[World Event] {tag} | intensity={intensity} | polarity={polarity}"]
        if anomaly.get("line"):
            block.append(f"Scene seed: {anomaly.get('line')}")
        parts.append("\n".join(block))

    doom = bus.doom if isinstance(bus.doom, dict) else {}
    if doom.get("clock_log"):
        parts.append(f"[Clock Progress] {doom.get('clock_log')}")
    if doom.get("log"):
        parts.append(f"[Doom Shift] {doom.get('log')}")

    # v3: 완성된 시계 → 서사 지시
    completed_clocks = doom.get("completed_this_turn", [])
    if isinstance(completed_clocks, list):
        for clock in completed_clocks:
            if isinstance(clock, dict):
                threat = clock.get("threat", "")
                cname = clock.get("name", "?")
                parts.append(
                    f"[CLOCK COMPLETED] '{cname}' → {threat}.\n"
                    "PC가 이 영향을 직접 마주칠 때 변화된 세계를 보여주라. "
                    "이미 벌어진 결과의 흔적으로 묘사. '한편' 시점 전환 금지."
                )

    # 클라이맥스
    if doom.get("climax_triggered"):
        parts.append("[CLIMAX] All clocks completed simultaneously. MAXIMUM TENSION. Final choice moment.")

    # 방어 보상 피드백
    if doom.get("defense_log"):
        parts.append(f"[Defense] {doom.get('defense_log')}")

    # 퀘스트 실패 (시계 완성으로)
    for fail in doom.get("quest_failed", []):
        if isinstance(fail, dict):
            parts.append(f"[Quest Failed] {fail.get('quest', '?')} — {fail.get('reason', '')}")

    # 임박 시계
    clocks = doom.get("clocks", [])
    if isinstance(clocks, list) and clocks:
        imminent: List[str] = []
        for clock in clocks:
            if not isinstance(clock, dict) or clock.get("resolved"):
                continue
            name = str(clock.get("name", "Unnamed Clock"))
            segments = int(_to_float(clock.get("segments", 4), 4))
            filled = int(_to_float(clock.get("filled", clock.get("progress", 0)), 0))
            remaining = max(0, segments - filled)
            if remaining <= 1:
                imminent.append(f"{name} ({remaining} left)")
        if imminent:
            parts.append("[Clock Imminent] " + " | ".join(imminent[:3]))

    status_seen = set()
    for container in (bus.vigor, bus.composure, bus.dai):
        if not isinstance(container, dict):
            continue
        for key, label in (("new_status_effects", "Status Added"), ("expired_status_effects", "Status Expired")):
            entries = container.get(key, [])
            if not isinstance(entries, list):
                continue
            for entry in entries:
                name = ""
                hint = ""
                if isinstance(entry, dict):
                    name = str(entry.get("name", "")).strip()
                    hint = str(entry.get("narrative_hint", "")).strip()
                else:
                    name = str(entry).strip()
                if not name:
                    continue
                sig = (label, name)
                if sig in status_seen:
                    continue
                status_seen.add(sig)
                line = f"[{label}] {name}"
                if hint:
                    line += f" - {hint}"
                parts.append(line)

    flashback = bus.dai.get("flashback_result") if isinstance(bus.dai, dict) else None
    if not flashback and isinstance(bus.dai, dict):
        flashback = bus.dai.get("flashback_eval")
    if isinstance(flashback, dict):
        declaration = str(flashback.get("declaration", "")).strip()
        if not declaration:
            declaration = str(flashback.get("reason", "")).strip()
        if declaration:
            parts.append(f"[Flashback] {declaration}")

    # ── Task 6: 장기 퀘스트 리마인드 (코어 — 둠 독립) ──
    channel_id = (context.narrative_anchors or {}).get("channel_id", "")
    if channel_id:
        try:
            import game_character
            active_quests = game_character.get_active_quests_raw(channel_id)
            turn_index = int(_to_float((bus.dai or {}).get("turn_index", 0), 0))
            for q in active_quests:
                if not isinstance(q, dict):
                    continue
                last_progress_turn = q.get("last_progress_turn", 0)
                stale_turns = turn_index - last_progress_turn
                if stale_turns >= 8:
                    parts.append(
                        f"[Quest Echo: {q.get('content', '?')}] "
                        "이 목표가 세계에 여전히 존재한다. 유저가 이 방향으로 행동할 때만 반영하라 — 강제 진전 금지."
                    )
        except Exception:
            pass

    if not parts:
        return ""
    return "[Events]\n" + "\n".join(parts)


# Universal narrative principles — separated from MC Moves (genre flavor)
CONSEQUENCE_DIRECTIVES = {
    "critical_success": (
        "Show the PC achieving MORE than they intended. "
        "A new possibility opens, or the PC seizes decisive control of the situation. "
        "If NPCs are present, show how this success positively shifts the relationship."
    ),
    "partial": (
        "The PC's intent is achieved, but ONE unwanted change necessarily follows. "
        "Concretely depict what was lost, exposed, or complicated. "
        "A partial success with zero cost is FORBIDDEN. "
        "If NPCs are present, let this cost leave a subtle mark on the relationship."
    ),
    "failure": (
        "The PC's intent is NOT achieved. "
        "The situation is now different from before the attempt — do NOT simply say 'it didn't work.' "
        "Show what has changed. "
        "If NPCs are present, show their reaction to witnessing this failure."
    ),
    "critical_failure": (
        "An IRREVERSIBLE change occurs in this scene. "
        "The opposite of the PC's intent is realized, or an unforeseen new reality is revealed. "
        "The world after this moment is different from the world before. "
        "If NPCs are present, this catastrophe fundamentally shakes the relationship."
    ),
}


def _build_judgment_layer(context, bus, mask: str) -> str:
    judgment = bus.judgment if isinstance(bus.judgment, dict) else {}
    if not judgment.get("active"):
        return ""

    meta = judgment.get("meta", {}) if isinstance(judgment.get("meta"), dict) else {}
    action = str(meta.get("action", "행동"))
    result = str(judgment.get("result", "failure"))
    reason = str(judgment.get("reason", "")).strip()

    dai = bus.dai if isinstance(bus.dai, dict) else {}
    pos_value = _to_float((dai.get("position", {}) or {}).get("value", 0.5), 0.5)
    pos_tier = _position_tier(pos_value)

    mechanic = context.request.genres.get("mechanic", {})
    primary_genre = mechanic.get("primary_lens", "")
    move = _get_genre_mc_move(primary_genre, pos_tier, result) or _mc_move(pos_tier, result)

    favorable, against = _collect_aspect_stance(dai.get("aspects", []))
    reason_part = f" ({reason})" if reason else ""
    lines = [
        f"[Judgment: {mask} '{action}'{reason_part}]",
        f"Result: {result} | Position: {pos_tier}",
        f"MC Move: {move}",
    ]
    if favorable:
        lines.append("Favorable: " + ", ".join(favorable))
    if against:
        lines.append("Against: " + ", ".join(against))

    # 범용 서사 원칙 (장르 불문)
    cons_dir = CONSEQUENCE_DIRECTIVES.get(result, "")
    if cons_dir:
        lines.append(f"[Consequence] {cons_dir}")

    return "\n".join(lines)


def _build_atmosphere_layer(context, bus) -> str:
    parts: List[str] = []

    vigor_val = int(_to_float((bus.vigor or {}).get("value", 100), 100))
    composure_val = int(_to_float((bus.composure or {}).get("value", 100), 100))

    if vigor_val >= 70:
        parts.append("[기력] FORTUNATE - body responds quickly and reliably.")
    elif vigor_val >= 40:
        pass
    elif vigor_val >= 15:
        parts.append("[기력] STRAINED - fatigue leaks into actions.")
    else:
        parts.append("[기력] COLLAPSING - body is failing; show cost, not hard block.")

    if composure_val >= 70:
        parts.append("[평정] FORTUNATE - social flow and observation stay sharp.")
    elif composure_val >= 40:
        pass
    elif composure_val >= 15:
        parts.append("[평정] STRAINED - mask slips and NPCs notice.")
    else:
        parts.append("[평정] COLLAPSING - mind frays; show cost, not hard block.")

    # Named Mixed-State Conditions
    v_low = vigor_val <= 39      # Stage 2+ (Exhaustion or Collapse)
    c_low = composure_val <= 39

    if v_low and c_low:
        parts.append(
            "[Condition: Desperate] Both body and mind are breaking. "
            "Show the PC pushing through on sheer will or raw instinct. "
            "Every action should carry visible strain and cost."
        )
    elif vigor_val >= 70 and c_low:
        parts.append(
            "[Condition: Reckless] Body is strong but mind falters. "
            "Show impulsive, unguarded behavior — the PC acts before thinking. "
            "Physical actions succeed but social judgment slips."
        )
    elif composure_val >= 70 and v_low:
        parts.append(
            "[Condition: Fragile] Mind is sharp but body fails. "
            "Show the PC's painful awareness of their physical limits — "
            "plans they can see but cannot execute."
        )

    if composure_val <= 14:
        parts.append("[NPC Reaction] composure collapse draws concern, avoidance, or exploitation.")
    if vigor_val <= 14:
        parts.append("[NPC Reaction] physical collapse changes how others treat the PC.")

    doom_val = int(_to_float((bus.doom or {}).get("value", 0), 0))
    if doom_val > 0:
        mechanic = context.request.genres.get("mechanic", {})
        primary_genre = mechanic.get("primary_lens", "")
        doom_info = game_world.get_doom_info(doom_val, genre=primary_genre)
        stage_name = doom_info.get("name", "")
        stage_emoji = doom_info.get("emoji", "")
        parts.append(f"[Tension] {stage_emoji}{stage_name} ({doom_val}%)")

    # Pacing directive based on doom stage
    if doom_val < 20:
        parts.append(
            "[Pacing: Breathing] The world is calm and clocks advance slowly. "
            "Use this space for character depth, relationships, and quiet moments. "
            "Let the PC enjoy what they've earned. "
            "Plant subtle seeds — sensory details, NPC remarks, environmental shifts — "
            "that may or may not become relevant later. Do NOT resolve existing tensions."
        )
    elif doom_val >= 80:
        parts.append(
            "[Pacing: Converging] The world accelerates. Unresolved threats compound. "
            "New dangers arrive already in motion. Show mounting urgency through "
            "environmental details and NPC behavior. "
            "Resolution must come from PC action, not narrative convenience. "
            "Do NOT hand the PC easy solutions or convenient escapes."
        )

    # ── Task 2b: 서사 공간 디렉티브 (둠 하락 시) ──
    narrative_space = int(_to_float((bus.doom or {}).get("narrative_space", 0), 0))
    if narrative_space > 0:
        if narrative_space >= 15:
            intensity = "넓은"
        elif narrative_space >= 8:
            intensity = "적당한"
        else:
            intensity = "작은"
        mechanic = context.request.genres.get("mechanic", {})
        primary_res = mechanic.get("primary_resource") or "vigor"
        if primary_res == "vigor":
            parts.append(
                f"[서사 공간: {intensity}] 긴장이 풀렸다 — 이 여유를 써라:\n"
                "- 캐릭터 관계 심화 (대화, 감정 교류, 유대 확인)\n"
                "- 유저 행동에 대한 세계의 반응과 되새김\n"
                "- 다음 위기의 복선을 자연스럽게 배치"
            )
        else:
            parts.append(
                f"[서사 공간: {intensity}] 일상이 돌아왔다 — 이 여유를 써라:\n"
                "- 인물 간 관계 심화 (소소한 대화, 감정 교류)\n"
                "- 유저의 선택이 주변에 미친 영향 묘사\n"
                "- 새로운 변화의 씨앗을 자연스럽게 배치"
            )

    # ── Task 1: 시계 조짐 디렉티브 (Clock Surfacing) ──
    clocks = (bus.doom or {}).get("clocks", [])
    if isinstance(clocks, list):
        mechanic = context.request.genres.get("mechanic", {})
        primary_res = mechanic.get("primary_resource") or "vigor"
        for clock in clocks:
            if not isinstance(clock, dict) or clock.get("resolved"):
                continue
            segments = int(_to_float(clock.get("segments", 4), 4))
            filled = int(_to_float(clock.get("filled", 0), 0))
            if segments <= 0:
                continue
            ratio = filled / segments
            name = clock.get("name", "?")
            threat = clock.get("threat", "")
            if ratio >= 0.75:
                if primary_res == "vigor":
                    parts.append(f"[Clock Omen: {name}] 위협의 징후가 뚜렷하다 — {threat}의 조짐을 PC 주변에서 구체적으로 묘사하라. 소문, 흔적, 이상 현상. '한편' 시점 전환 금지.")
                else:
                    parts.append(f"[Clock Omen: {name}] 위기의 전조가 감지된다 — {threat}의 조짐을 주변 인물의 태도 변화, 미묘한 분위기, 사소한 균열로 묘사하라. 시점 전환 금지.")
            elif ratio >= 0.5:
                if primary_res == "vigor":
                    parts.append(f"[Clock Hint: {name}] 먼 전조 — {threat}의 징후를 감각적 디테일로 암시하라.")
                else:
                    parts.append(f"[Clock Hint: {name}] 미세한 변화 — {threat}의 전조를 일상의 작은 어긋남으로 암시하라.")

    status_effects = (context.narrative_anchors or {}).get("status_effects", [])
    if isinstance(status_effects, list):
        for status in status_effects[:3]:
            if not isinstance(status, dict):
                continue
            name = str(status.get("name", "")).strip()
            if not name:
                continue
            hint = str(status.get("narrative_hint", status.get("description", ""))).strip()
            if hint:
                parts.append(f"[Condition: {name}] {hint}")
            else:
                parts.append(f"[Condition: {name}] active.")

    # ── Task 3: 시점 고정 디렉티브 (POV Lock) ──
    if isinstance(clocks, list):
        active_clocks = [c for c in clocks if isinstance(c, dict) and not c.get("resolved")]
        if active_clocks:
            parts.append(
                "[시점 고정] 시계 이벤트는 PC 시점 안에서만 묘사하라. "
                "'한편', '그 무렵', '다른 곳에서는' 등 시점 전환 절대 금지. "
                "PC가 직접 목격·감지하는 것만 서술."
            )

    if not parts:
        return ""
    return "[Atmosphere]\n" + "\n".join(parts)


def _build_system_message(bus) -> str:
    chunks: List[str] = []

    judgment = bus.judgment if isinstance(bus.judgment, dict) else {}
    if judgment.get("output"):
        chunks.append(str(judgment.get("output")))

    anomaly = bus.anomaly if isinstance(bus.anomaly, dict) else {}
    if anomaly.get("triggered"):
        tag = anomaly.get("tag", "anomaly")
        chunks.append(f"[이변] {tag}")

    doom = bus.doom if isinstance(bus.doom, dict) else {}
    for key in ("relief_log", "mental_pressure_log", "clock_log", "log"):
        val = doom.get(key)
        if val:
            chunks.append(str(val))

    vigor = bus.vigor if isinstance(bus.vigor, dict) else {}
    if vigor.get("log"):
        chunks.append(str(vigor.get("log")))

    deduped: List[str] = []
    seen = set()
    for chunk in chunks:
        text = chunk.strip()
        if not text or text in seen:
            continue
        seen.add(text)
        deduped.append(text)

    return "\n".join(deduped).strip()

def convert_to_game_context(channel_id: str, user_id: str, user_input: str) -> GameContext:
    """[UNE Bridge] ParticipantData -> GameContext"""
    from orchestration_context import GameContext, RequestData, SharedBus

    p_data = domain_manager.get_participant_data(channel_id, user_id)
    mem = p_data.get("ai_memory", {}) if p_data else {}
    world = domain_manager.get_world_state(channel_id)

    # Genre mapping
    genres_raw = domain_manager.get_active_genres(channel_id)
    if isinstance(genres_raw, dict) and "layers" in genres_raw:
        layers = genres_raw["layers"]
        mechanic = genres_raw.get("mechanic_profile", {})
        genres = {
            "stage": layers.get("world_setting", []),
            "flavor": layers.get("style_tech", []),
            "lens": layers.get("narrative_tone", []),
            "atmosphere": genres_raw.get("atmosphere_guide", ""),
            "mechanic": mechanic,
        }
    else:
        genres = {
            "stage": [genres_raw[0]] if isinstance(genres_raw, list) and genres_raw else ([str(genres_raw)] if genres_raw else []),
            "flavor": [],
            "lens": [],
            "atmosphere": "",
            "mechanic": {},
        }
    # 하위 호환: str이 들어오면 List로 래핑
    for key in ["stage", "flavor", "lens"]:
        val = genres[key]
        if isinstance(val, str):
            genres[key] = [val] if val else []

    # Active Modules
    active_modules = domain_manager.get_active_modules(channel_id)

    # Lore Summary (V4) + Chunks (V5)
    lore_summary = domain_manager.get_lore_summary_data(channel_id)
    lore_chunks = domain_manager.get_lore_chunks(channel_id)

    # History & Lore Text (V4 - fallback)
    history = domain_manager.get_history(channel_id)
    recent_history = history[-30:] if history else []  # Last 30 turns
    history_text = "\n".join([f"{h['role']}: {h['content']}" for h in recent_history])
    lore_text = domain_manager.get_lore(channel_id)

    # Narrative Anchors (행동자 PC)
    anchors = {
        "channel_id": channel_id,
        "appearance": mem.get("appearance", ""),
        "personality": mem.get("personality", ""),
        "background": mem.get("background", ""),
        "relations": mem.get("relationships", {}),
        "passives": mem.get("passives", []),
        "status_effects": p_data.get("status_effects", []) if p_data else [],
        "inventory": mem.get("inventory", []),
        "memos": mem.get("memos", [])
    }

    # 모든 활성 PC 정보 수집 (다인 플레이 지원)
    all_participants = domain_manager.get_domain(channel_id).get("participants", {})
    all_pcs = {}
    for uid, pdata in all_participants.items():
        if pdata.get("status") == "active":
            pmem = pdata.get("ai_memory", {})
            all_pcs[uid] = {
                "mask": pdata.get("mask", "Unknown"),
                "appearance": pmem.get("appearance", ""),
                "personality": pmem.get("personality", ""),
                "passives": pmem.get("passives", []),
                "vigor_value": pmem.get("vigor", pmem.get("mental", {})).get("value", 100),
                "composure_value": pmem.get("composure", {}).get("value", 100),
            }
    anchors["all_pcs"] = all_pcs
    anchors["acting_user_id"] = user_id

    # NPC Knowledge & Attitudes (피드백용)
    anchors["stored_npc_knowledge"] = domain_manager.get_npc_knowledge(channel_id)
    anchors["stored_npc_attitudes"] = domain_manager.get_npc_attitudes(channel_id)

    # NPC Roster (Theoria용 이름+역할 요약)
    import npc_manager as _npc_mgr
    _npc_mgr.migrate_npc_fields(channel_id)  # desc→description 통일 + 구조화 필드 자동 추출
    anchors["npc_roster"] = _npc_mgr.get_npc_roster(channel_id)

    # Session Memory (World State Updater 피드백용)
    anchors["session_memory"] = domain_manager.get_session_ai_memory(channel_id)

    # Pending Flashback (회상 대기)
    anchors["pending_flashback"] = domain_manager.get_pending_flashback(channel_id)

    # Bus initialization
    bus = SharedBus()
    bus.doom["value"] = world.get("doom", 40)
    # Doom clocks (local threats)
    clocks = world.get("doom_clocks", [])
    bus.doom["clocks"] = clocks if isinstance(clocks, list) else []

    # Vigor/Composure migration: old "mental" → vigor + composure
    if "mental" in mem and "vigor" not in mem:
        old_val = mem["mental"].get("value", 100)
        old_delta = mem["mental"].get("last_delta", 0)
        mem["vigor"] = {"value": old_val, "last_delta": old_delta}
        mem["composure"] = {"value": old_val, "last_delta": 0}
        del mem["mental"]

    vigor_data = mem.get("vigor", {"value": 100, "last_delta": 0})
    bus.vigor["value"] = vigor_data.get("value", 100)
    bus.vigor["last_delta"] = vigor_data.get("last_delta", 0)
    composure_data = mem.get("composure", {"value": 100, "last_delta": 0})
    bus.composure["value"] = composure_data.get("value", 100)
    bus.composure["last_delta"] = composure_data.get("last_delta", 0)

    # Momentum 로드 (이전 턴 판정 결과의 여운)
    bus.judgment["momentum_carry"] = mem.get("judgment_momentum", 0)

    context = GameContext(
        request=RequestData(
            user_input=user_input,
            genres=genres,
            active_modules=active_modules,
            lore_summary=lore_summary,
            history_text=history_text,
            lore_text=lore_text,
            lore_chunks=lore_chunks
        ),
        narrative_anchors=anchors,
        shared_bus=bus
    )

    return context

def sync_from_game_context(channel_id: str, user_id: str, ctx: Any) -> None:
    """[UNE Bridge] GameContext -> ParticipantData/WorldState Sync"""
    from orchestration_context import GameContext
    if isinstance(ctx, dict):
        ctx = GameContext.from_dict(ctx)
    bus = ctx.shared_bus

    # 1. World State Sync (Doom)
    if bus.doom.get("active") or isinstance(bus.doom.get("clocks"), list):
        world = domain_manager.get_world_state(channel_id)
        world["doom"] = bus.doom["value"]
        if isinstance(bus.doom.get("clocks"), list):
            world["doom_clocks"] = bus.doom.get("clocks", [])
        domain_manager.update_world_state(channel_id, world)

    # 2. Participant Data Sync (Vigor, Composure, Adaptation)
    p_data = domain_manager.get_participant_data(channel_id, user_id)
    if p_data:
        mem = p_data.setdefault("ai_memory", {})

        # Remove legacy "mental" key if present
        mem.pop("mental", None)

        for axis_name in ("vigor", "composure"):
            axis_bus = getattr(bus, axis_name)
            if axis_bus.get("active"):
                axis_sys = mem.setdefault(axis_name, {"value": 100, "last_delta": 0})
                axis_sys["value"] = axis_bus["value"]
                axis_sys["last_delta"] = axis_bus.get("last_delta", 0)

                # Trauma Trigger (사용 후 pop — 다음 턴 중복 방지)
                if axis_bus.get("trauma_trigger"):
                    passives = mem.setdefault("passives", [])
                    trauma_name = f"트라우마 ({axis_name} 각성)"
                    if not any(p.get("name") == trauma_name for p in passives if isinstance(p, dict)):
                        label = "기력" if axis_name == "vigor" else "평정"
                        passives.append({
                            "name": trauma_name,
                            "tags": ["Trauma", "Hard-to-cure"],
                            "modifier": -5,
                            "desc": f"{label} 붕괴에서 깨어난 트라우마입니다. 모든 판정에 -5 패널티를 받습니다."
                        })
                    axis_bus.pop("trauma_trigger", None)

        # Momentum 저장 (다음 턴 carry)
        momentum_next = bus.judgment.get("momentum_next", 0)
        if momentum_next != 0:
            mem["judgment_momentum"] = momentum_next
        else:
            mem.pop("judgment_momentum", None)

        domain_manager.save_participant_data(channel_id, user_id, p_data)

class UniversalNarrativeEngine:
    def __init__(self, client, model_id: str):
        self.pipeline = WaterfallPipeline(client, model_id)

    async def run(self, channel_id: str, user_id: str, user_input: str) -> Dict[str, Any]:
        """단일 PC 행동 처리 (솔로/자동 모드용)"""
        turn_index = game_world.increment_turn_index(channel_id)
        game_character.process_status_expiry(channel_id, user_id, turn_index)
        context = convert_to_game_context(channel_id, user_id, user_input)
        p_data = domain_manager.get_participant_data(channel_id, user_id)
        mask = p_data.get("mask") if p_data else "PC"

        updated_context = await self.pipeline.execute(context)
        sync_from_game_context(channel_id, user_id, updated_context)

        result = self._extract_pc_result_v3(updated_context, mask)
        return {
            "game_context": updated_context,
            "directive": result["directive"],
            "system_message": result["system_msg"]
        }

    async def run_batch(self, channel_id: str, pending_actions: Dict[str, Dict]) -> Dict[str, Any]:
        """다인 동시 행동 처리. pending_actions = {uid: {"mask":str, "actions":[str]}}"""
        all_results = []
        anomaly_data = None
        last_context = None
        turn_index = game_world.increment_turn_index(channel_id)

        for uid, info in pending_actions.items():
            game_character.process_status_expiry(channel_id, uid, turn_index)
            combined_input = "\n".join(info["actions"])
            context = convert_to_game_context(channel_id, uid, combined_input)

            # 스토리텔러 중복 방지: 이미 발동했으면 동일 이벤트 데이터 복사
            if anomaly_data:
                context.shared_bus.anomaly["_skip_storyteller"] = True
                context.shared_bus.anomaly.update(anomaly_data)

            updated = await self.pipeline.execute(context)

            # 이변 정보 보존 (첫 발동분)
            if updated.shared_bus.anomaly.get("triggered") and not anomaly_data:
                anomaly_data = {
                    "tag": updated.shared_bus.anomaly.get("tag"),
                    "intensity": updated.shared_bus.anomaly.get("intensity"),
                    "polarity": updated.shared_bus.anomaly.get("polarity"),
                    "category": updated.shared_bus.anomaly.get("category"),
                    "potential": True,
                }

            sync_from_game_context(channel_id, uid, updated)
            all_results.append(self._extract_pc_result_v3(updated, info["mask"]))
            last_context = updated

        return self._combine_batch_results_v3(all_results, last_context)

    async def run_observation(self, channel_id: str) -> Dict[str, Any]:
        """관찰 모드: PC 행동 없이 세계 묘사"""
        turn_index = game_world.increment_turn_index(channel_id)
        participants = domain_manager.get_domain(channel_id).get("participants", {})
        base_uid = None
        for uid, p in participants.items():
            if p.get("status") == "active":
                base_uid = uid
                break

        if not base_uid:
            return {"game_context": None, "directive": "", "system_message": ""}

        for uid, pdata in participants.items():
            if pdata.get("status") == "active":
                game_character.process_status_expiry(channel_id, uid, turn_index)

        observation_input = "[관찰 모드 — 직접적인 행동 없이 주변을 지켜본다]"
        context = convert_to_game_context(channel_id, base_uid, observation_input)

        # 판정 비활성화 (관찰은 행동이 아님)
        context.shared_bus.judgment["active"] = False

        updated = await self.pipeline.execute(context)
        sync_from_game_context(channel_id, base_uid, updated)

        result = self._extract_pc_result_v3(updated, "")
        return {
            "game_context": updated,
            "directive": "[관찰 모드] 세계와 NPC의 자연스러운 활동을 묘사하라. PC의 행동은 없다.\n" + result["directive"],
            "system_message": result["system_msg"]
        }

    def _extract_pc_result_v3(self, context, mask: str) -> Dict[str, Any]:
        """Build directive-layer v3: World -> Events -> Judgment -> Atmosphere."""
        bus = context.shared_bus
        layers: List[str] = []

        world = _build_world_layer(bus)
        if world:
            layers.append(world)

        events = _build_events_layer(context, bus)
        if events:
            layers.append(events)

        effective_mask = mask or context.get_acting_mask()
        judgment = _build_judgment_layer(context, bus, effective_mask)
        if judgment:
            layers.append(judgment)

        atmosphere = _build_atmosphere_layer(context, bus)
        if atmosphere:
            layers.append(atmosphere)

        # Preserve autonomous NPC behavior directive after core v3 layers.
        if bus.dai and bus.dai.get("psyche_states"):
            from npc_autonomous import NPCAutonomousEngine

            triggers = NPCAutonomousEngine.evaluate_triggers(
                psyche_states=bus.dai.get("psyche_states", {}),
                npc_knowledge=bus.dai.get("npc_knowledge", {}),
                npc_attitudes=bus.dai.get("npc_attitudes", {}),
                scene_type=bus.dai.get("scene_type", "normal"),
            )
            auto_directive = NPCAutonomousEngine.build_autonomous_directive(triggers)
            if auto_directive:
                layers.append(auto_directive)

        fallback_msg = self.pipeline.get_fallback_directives(context.request.active_modules)
        if fallback_msg:
            layers.append(f"[Module Constraints]\n{fallback_msg}")

        return {
            "directive": "\n\n".join(part for part in layers if part).strip(),
            "system_msg": _build_system_message(bus),
            "has_anomaly": bool(bus.anomaly and bus.anomaly.get("triggered")),
            "anomaly_header": "",
            "adaptation_line": "",
            "mental_log": bus.vigor.get("log", "") if bus.vigor else "",
        }

    def _combine_batch_results_v3(self, results: list, last_context) -> Dict[str, Any]:
        """Merge multi-PC directives and system logs for batch processing."""
        all_directives: List[str] = []
        system_chunks: List[str] = []
        seen_sys = set()

        for result in results:
            directive = (result.get("directive") or "").strip()
            if directive:
                all_directives.append(directive)

            sys_msg = (result.get("system_msg") or "").strip()
            if sys_msg and sys_msg not in seen_sys:
                seen_sys.add(sys_msg)
                system_chunks.append(sys_msg)

        return {
            "game_context": last_context,
            "directive": "\n\n".join(all_directives).strip(),
            "system_message": "\n\n".join(system_chunks).strip(),
        }

    def _extract_pc_result(self, context, mask: str) -> Dict[str, Any]:
        """Extract 5-Layer Directive + system_msg from single PC pipeline result.

        Layer 0: [Base Directive] — DAI soft hints (when Judgment OFF)
        Layer 1: [Narrative] — FitD Position + PbtA MC Move (Judgment result)
        Layer 2: [Aspects] — Fate Aspect declaration (cross-module interaction)
        Layer 3: [Intrusion] — Cypher GM Intrusion (Anomaly event)
        Layer 4: [Atmosphere] — Doom Clock progress + Vigor state
        """
        bus = context.shared_bus
        directive_parts = []
        system_msg = ""
        result_line = ""

        doom_val = bus.doom.get("value", 0) if bus.doom else 0
        vigor_val = bus.vigor.get("value", 100) if bus.vigor else 100
        composure_val = bus.composure.get("value", 100) if bus.composure else 100

        # ── Layer 1: [Narrative] — Position + MC Move ──
        j_active = bus.judgment and bus.judgment.get("active")
        j_result = ""
        if j_active:
            j_mask = bus.judgment.get("mask", mask)
            meta = bus.judgment.get("meta", {})
            action = meta.get("action", "행동")
            j_result = bus.judgment.get("result", "failure")
            reason_txt = bus.judgment.get("reason", "")

            # Position from Theoria (FitD)
            pos_val = bus.dai.get("position", {}).get("value", 0.5) if bus.dai else 0.5
            if pos_val <= 0.25:
                pos_tier = "desperate"
            elif pos_val <= 0.5:
                pos_tier = "risky"
            else:
                pos_tier = "controlled"

            # MC Move: Genre-specific first, then generic fallback (PbtA)
            mechanic = context.request.genres.get("mechanic", {})
            primary_genre = mechanic.get("primary_lens", "")

            # Try genre-specific MC move first
            move = _get_genre_mc_move(primary_genre, pos_tier, j_result)

            # Fallback: generic MC moves
            if not move:
                if j_result in ("failure", "critical_failure"):
                    mc_moves = {
                        "desperate": "Make the threat real — irreversible consequences",
                        "risky": "Escalate the situation — a new danger reveals itself",
                        "controlled": "Demand a minor cost — a small setback occurs",
                    }
                    if j_result == "critical_failure":
                        mc_moves = {
                            "desperate": "Catastrophic outcome — something irreversible happens",
                            "risky": "Worst case unfolds — the danger becomes reality",
                            "controlled": "Unexpected reversal — safety shatters",
                        }
                elif j_result == "partial":
                    mc_moves = {
                        "desperate": "Heavy price paid — gain what was sought but lose something",
                        "risky": "Success with cost — complications follow",
                        "controlled": "Minor friction — less smooth than expected",
                    }
                else:  # success / critical_success
                    mc_moves = {
                        "desperate": "Dramatic turnaround — shining in the direst moment",
                        "risky": "Danger cleared — competent execution",
                        "controlled": "Clean success — smooth and effortless",
                    }
                    if j_result == "critical_success":
                        mc_moves = {
                            "desperate": "Miraculous reversal — a transcendent moment",
                            "risky": "Brilliant success — impressive result against the odds",
                            "controlled": "Overwhelming mastery — exceeds all expectations",
                        }
                move = mc_moves.get(pos_tier, mc_moves.get("risky", ""))
            reason_part = f" ({reason_txt})" if reason_txt else ""
            directive_parts.append(
                f"[Narrative: {j_mask} '{action}'{reason_part} — {pos_tier}] {move}"
            )
            system_msg += bus.judgment.get("output", "")
            if bus.judgment.get("party_wide_hook"):
                system_msg += "\n⚠️ **[전체 파티 영향]** — 이 결과는 모든 동료에게 영향을 미칩니다."

        # ── Layer 0: [Base Directive] — DAI soft hints (Judgment OFF) ──
        if not j_active and bus.dai and bus.dai.get("active"):
            hints = []

            # Genre scene hint
            mechanic = context.request.genres.get("mechanic", {})
            primary_genre = mechanic.get("primary_lens", "")

            genre_scene_hints = {
                "cosmic_horror": "Genre: Cosmic Horror — dread builds from the unseen and unknowable",
                "romance": "Genre: Romance — emotional resonance and interpersonal nuance matter most",
                "comedy": "Genre: Comedy — timing, escalation, and social absurdity drive the scene",
                "noir": "Genre: Noir — shadows hide truth, trust is currency, everyone has angles",
                "action": "Genre: Action — momentum, physical stakes, and tactical decisions",
                "slice_of_life": "Genre: Slice of Life — quiet moments carry meaning, change is gradual",
            }
            if primary_genre in genre_scene_hints:
                hints.append(genre_scene_hints[primary_genre])

            # Position → narrative tone
            pos_data = bus.dai.get("position", {})
            pos_val = pos_data.get("value", 0.5)
            if pos_val <= 0.25:
                hints.append("Position: Desperate — stakes are lethal, consequences loom")
            elif pos_val <= 0.5:
                hints.append("Position: Risky — danger present, outcome uncertain")
            else:
                hints.append("Position: Controlled — situation favors the actor")

            # SceneType → scene-specific guidance
            scene_type = bus.dai.get("scene_type", "normal")
            scene_hints = {
                "combat": "Combat scene: emphasize physicality, positioning, and threat",
                "tension": "Tension scene: build suspense, restrict information flow",
                "intimate": "Intimate scene: focus on emotion, subtlety, and vulnerability",
                "exploration": "Exploration scene: reward curiosity, reveal the world",
                "social": "Social scene: weigh reputation, leverage, and hidden agendas",
            }
            if scene_type in scene_hints:
                hints.append(scene_hints[scene_type])

            # EnergyDirection → pacing
            energy = bus.dai.get("energy_direction", "steady")
            energy_hints = {
                "rising": "Energy rising — escalate tension, accelerate pacing",
                "falling": "Energy falling — allow breathing room, reflect on aftermath",
                "peak": "Energy at peak — climactic moment, maximum intensity",
                "steady": "Energy steady — maintain current rhythm",
            }
            if energy in energy_hints:
                hints.append(energy_hints[energy])

            # needs_judgment=True but module OFF → soft probability hint
            if bus.dai.get("needs_judgment"):
                action_meta = bus.dai.get("action_meta", {})
                action_name = action_meta.get("action", "")
                if action_name:
                    hints.append(f"Action '{action_name}' attempted — judge outcome by situational probability, no dice")
                else:
                    hints.append("Meaningful action attempted — judge outcome by situational probability, no dice")

            if hints:
                directive_parts.append("[Base Directive]\n" + "\n".join(hints))

        # ── Layer 3: [Intrusion] — World Initiative (Genre-Aware) ──
        anomaly_sys = ""
        a_triggered = bus.anomaly and bus.anomaly.get("triggered")
        if a_triggered:
            tag = bus.anomaly.get("tag") or "이변"
            intensity = bus.anomaly.get("intensity")
            polarity = bus.anomaly.get("polarity")
            line = bus.anomaly.get("line", "")

            # Resolve genre for framing
            mechanic = context.request.genres.get("mechanic", {})
            intrusion_genre = mechanic.get("primary_lens", "")

            # Genre-specific anomaly framing
            genre_frames = {
                "cosmic_horror": {"positive": "a glimpse of forbidden understanding", "negative": "the veil thins — reality distorts", "mixed": "revelation wrapped in dread"},
                "romance": {"positive": "a fateful encounter or revelation", "negative": "emotional disruption — hearts shaken", "mixed": "a moment that changes everything"},
                "comedy": {"positive": "absurd luck — things go impossibly right", "negative": "comedic disaster — everything that can go wrong does", "mixed": "the situation escalates hilariously"},
                "noir": {"positive": "an unexpected card to play", "negative": "the net tightens — exposure looms", "mixed": "a new piece enters the game"},
                "action": {"positive": "tactical advantage appears", "negative": "the battlefield shifts against you", "mixed": "chaos reshapes the fight"},
                "slice_of_life": {"positive": "a pleasant surprise in the routine", "negative": "the familiar becomes uncomfortable", "mixed": "change ripples through daily life"},
            }
            default_frame = {"positive": "may serve as opportunity", "negative": "arrives as threat", "mixed": "both opportunity and threat"}
            frame_table = genre_frames.get(intrusion_genre, default_frame)
            polarity_frame = frame_table.get(polarity, frame_table.get("mixed", "shifts the situation"))
            intrusion = f"[Intrusion: {tag}] {polarity_frame}"
            if line:
                intrusion += f"\n{line}"
            directive_parts.append(intrusion)

            # Anomaly system message (Discord)
            header = f"⚡ 이변 발생: [[{tag}]]"
            anomaly_sys += f"\n{header}"
            if line:
                anomaly_sys += f"\n{line}"
            else:
                info_parts = []
                if tag: info_parts.append(f"태그: {tag}")
                if intensity: info_parts.append(f"강도: {intensity}")
                if polarity: info_parts.append(f"성격: {polarity}")
                anomaly_sys += f"\n{' / '.join(info_parts) if info_parts else '이변 정보: (미상)'}"
        system_msg += anomaly_sys

        # ── System Logs (Discord) ──
        if bus.doom and bus.doom.get("relief_log"):
            system_msg += f"\n{bus.doom.get('relief_log')}"
        if bus.doom and bus.doom.get("mental_pressure_log"):
            system_msg += f"\n{bus.doom.get('mental_pressure_log')}"
        if bus.doom and bus.doom.get("clock_log"):
            system_msg += f"\n⏰ {bus.doom.get('clock_log')}"
        if bus.doom and bus.doom.get("defense_log"):
            system_msg += f"\n{bus.doom.get('defense_log')}"
        if bus.vigor:
            log_parts = []
            if bus.vigor.get("log"):
                log_parts.append(bus.vigor.get("log"))
            if log_parts:
                system_msg += f"\n{' → '.join(log_parts)}"

        # ── Layer 2: [Aspects] — Fate Aspect declaration (Genre-Aware) ──
        aspects = []
        import config as _cfg
        mechanic = context.request.genres.get("mechanic", {})
        primary_axis = mechanic.get("primary_resource") or "vigor"
        primary_val = vigor_val if primary_axis == "vigor" else composure_val

        m_trauma = (bus.vigor and bus.vigor.get("trauma_trigger")) or (bus.composure and bus.composure.get("trauma_trigger"))
        if j_active and a_triggered:
            if j_result in ("critical_failure", "failure"):
                aspects.append("Failure Resonance")
            elif j_result == "critical_success":
                aspects.append("Glory's Shadow")
        if a_triggered and primary_val <= 39:
            erosion_label = "기력 침식" if primary_axis == "vigor" else "평정 균열"
            aspects.append(erosion_label)
        if m_trauma and a_triggered:
            aspects.append("Inner-Outer Convergence")
        if m_trauma and j_active:
            aspects.append("Resurgence")
        if j_result == "critical_failure" and primary_val <= 14:
            aspects.append("Abyss")
        if bus.anomaly and bus.anomaly.get("escalated"):
            aspects.append("Loss of Control")
        if aspects:
            directive_parts.append("[Aspects]: " + ", ".join(aspects))

        # ── Layer 4: [Atmosphere] — Doom Clock + Vigor ──
        atmosphere = []
        active_modules = context.request.active_modules

        # Doom = 8-Segment FitD Clock (only when module active)
        if "doom" in active_modules:
            # Genre-aware doom stage lookup
            import game_world as _gw
            mechanic_doom = context.request.genres.get("mechanic", {})
            primary_genre = mechanic_doom.get("primary_lens", "")
            doom_info = _gw.get_doom_info(doom_val, genre=primary_genre)
            stage_name = doom_info.get("name", "")
            stage_emoji = doom_info.get("emoji", "")

            if doom_val >= 88:
                atmosphere.append(f"Tension Clock {doom_val}% {stage_emoji}[{stage_name}] — about to break")
            elif doom_val >= 76:
                atmosphere.append(f"Tension Clock {doom_val}% {stage_emoji}[{stage_name}] — running out of time")
            elif doom_val >= 63:
                atmosphere.append(f"Tension Clock {doom_val}% {stage_emoji}[{stage_name}] — closing in")
            elif doom_val >= 50:
                atmosphere.append(f"Tension Clock {doom_val}% {stage_emoji}[{stage_name}] — tension fills the air")
            elif doom_val >= 38:
                atmosphere.append(f"Tension Clock {doom_val}% {stage_emoji}[{stage_name}] — uneasy calm")
            elif doom_val >= 25:
                atmosphere.append(f"Tension Clock {doom_val}% {stage_emoji}[{stage_name}] — equilibrium")
            elif doom_val >= 13:
                atmosphere.append(f"Tension Clock {doom_val}% {stage_emoji}[{stage_name}] — relative calm")
            else:
                atmosphere.append(f"Tension Clock {doom_val}% {stage_emoji}[{stage_name}] — tension has receded")

        # Vigor + Composure = 2-axis PC state (only when module active)
        if "mental" in active_modules:
            if vigor_val <= 14:
                atmosphere.append(f"기력 붕괴 ({vigor_val}%) — 한계를 넘어 신체가 무너진다")
            elif vigor_val <= 39:
                atmosphere.append(f"기력 고갈 ({vigor_val}%) — 탈진으로 몸이 버거워진다")
            elif vigor_val <= 69:
                atmosphere.append(f"기력 동요 ({vigor_val}%) — 신체 균형이 흔들린다")

            if composure_val <= 14:
                atmosphere.append(f"평정 붕괴 ({composure_val}%) — 정신이 무너진다")
            elif composure_val <= 39:
                atmosphere.append(f"평정 동요 ({composure_val}%) — 감정이 취약해진다")
            elif composure_val <= 69:
                atmosphere.append(f"평정 흔들림 ({composure_val}%) — 내면이 불안정하다")

        v_trauma = bus.vigor and bus.vigor.get("trauma_trigger")
        c_trauma = bus.composure and bus.composure.get("trauma_trigger")
        if v_trauma:
            atmosphere.append("기력 트라우마 각성 — 벼랑 끝에서 신체가 재점화된다")
        if c_trauma:
            atmosphere.append("평정 트라우마 각성 — 붕괴 직전에서 정신이 재기동된다")

        if atmosphere:
            directive_parts.append("[Atmosphere]: " + " / ".join(atmosphere))

        # ── NPC Autonomous Behavior Triggers (Phase 7) ──
        if bus.dai and bus.dai.get("psyche_states"):
            from npc_autonomous import NPCAutonomousEngine
            triggers = NPCAutonomousEngine.evaluate_triggers(
                psyche_states=bus.dai.get("psyche_states", {}),
                npc_knowledge=bus.dai.get("npc_knowledge", {}),
                npc_attitudes=bus.dai.get("npc_attitudes", {}),
                scene_type=bus.dai.get("scene_type", "normal"),
            )
            auto_directive = NPCAutonomousEngine.build_autonomous_directive(triggers)
            if auto_directive:
                directive_parts.append(auto_directive)

        # Fallbacks
        fallback_msg = self.pipeline.get_fallback_directives(context.request.active_modules)
        if fallback_msg:
            directive_parts.append(f"\n[Module Constraints]:\n{fallback_msg}")

        return {
            "directive": "\n".join(directive_parts),
            "system_msg": system_msg,
            "has_anomaly": bool(a_triggered),
            "anomaly_header": anomaly_sys.strip() if anomaly_sys else "",
            "adaptation_line": "",
            "mental_log": bus.vigor.get("log", "") if bus.vigor else "",
        }

    def _combine_batch_results(self, results: list, last_context) -> Dict[str, Any]:
        """다인 배치 결과를 통합 출력으로 합침"""
        all_directives = []
        judgment_msgs = []
        anomaly_header = ""
        mental_lines = []

        for r in results:
            if r["directive"]:
                all_directives.append(r["directive"])

            # 판정 부분만 추출 (system_msg에서 이변/멘탈 제외)
            sys = r["system_msg"]
            if "🎲" in sys:
                judgment_part = sys.split("⚡")[0].split("⏳")[0]
                for marker in ["\n📈", "\n📉", "\n🧠"]:
                    if marker in judgment_part:
                        judgment_part = judgment_part[:judgment_part.index(marker)]
                judgment_msgs.append(judgment_part.strip())

            # 이변 헤더는 1회만
            if r["has_anomaly"] and not anomaly_header:
                anomaly_header = r["anomaly_header"]

            if r["mental_log"]:
                mental_lines.append(r["mental_log"])

        # 통합 시스템 메시지 구성
        combined_sys = ""

        # 1. 모든 판정 결과
        if judgment_msgs:
            combined_sys += "\n\n".join(judgment_msgs)

        # 2. 이변 헤더 (1회)
        if anomaly_header:
            combined_sys += f"\n\n{anomaly_header}"

        # 3. 멘탈 변동 (PC별)
        if mental_lines:
            combined_sys += "\n"
            for line in mental_lines:
                combined_sys += f"\n{line}"

        return {
            "game_context": last_context,
            "directive": "\n\n".join(all_directives),
            "system_message": combined_sys.strip()
        }
