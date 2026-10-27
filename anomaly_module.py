"""
Lorekeeper UNE - Anomaly Module (v4.0 — Storyteller / World Initiative Engine)
RimWorld-style event scheduler: Flash proposes events, code decides timing/diversity/rhythm.
No damage calculation — purely narrative event scheduling.
"""

import logging
from typing import Any, Dict, List, Optional
from orchestration_context import GameContext
import config as _cfg

logger = logging.getLogger("Anomaly")

# =========================================================
# Timing Table (Cassandra Curve)
# energy_direction × turns_since_last_event → decision
# =========================================================
TIMING_TABLE: Dict[str, Dict[int, str]] = {
    "idle":       {0: "defer", 1: "defer", 2: "act",   3: "act"},
    "stagnant":   {0: "defer", 1: "act",   2: "act",   3: "act"},
    "rising":     {0: "defer", 1: "defer", 2: "defer", 3: "act"},
    "detonation": {0: "skip",  1: "skip",  2: "defer", 3: "defer"},
    "aftershock": {0: "defer", 1: "defer", 2: "act",   3: "act"},
}


# [2026-10-10 큐 이어짐] Theoria에 [E#]로 보이는 대기 사건 = 시계 완성 항목을 뺀 큐(순서 그대로).
#   theoria_analyzer._build_pending_context와 길잡이(_route_continuation)가 같은 셈을 쓴다.
_INTENSITY_RANK = {"Low": 0, "Mid": 1, "High": 2, "Extreme": 3}


_NO_EXPIRE_TYPES = ("clock_completion", "thread_due", "collision")   # [C75 G3] 충돌파도 만료 없음 · 전조 아님
# [2026-10-24 C74 W1-6] 큐 거르기 규칙 — 쓰는 곳마다 목적이 다르다(한 곳에 적어 둔다):
#   pending_events(Theoria [E#] · 길잡이) ...... clock_completion 뺌 — 시계 완성은 doom 차례, 고를 사건이 아니다
#   _omen_top(전조) ............................ clock_completion · thread_due 뺌 — 시계 줄 · 장부 줄이 이미 앞서 알린다
#   굶주림 셈(_decide_timing) ................... clock_completion 뺌 — 시계는 자기 셈(non_intimate_turns)이 따로 있다
#   만료(EVENT_QUEUE_EXPIRY_TURNS) .............. _NO_EXPIRE_TYPES 뺌 — 기계적 약속은 만료 안 함


def pending_events(st_state: dict) -> list:
    return [e for e in (st_state.get("event_queue") or [])
            if isinstance(e, dict) and e.get("type") != "clock_completion"]


def _omen_top(queue) -> "dict | None":
    """[2026-10-10 전조 미루기] 전조의 주인 = 큐에서 첫 이변 항목(시계 완성·스레드 기한 제외)."""
    return next((e for e in (queue or []) if isinstance(e, dict) and e.get("type") not in _NO_EXPIRE_TYPES), None)


class AnomalyModule:
    def __init__(self, client, model_id: str):
        self.client = client
        self.model_id = model_id

    @staticmethod
    def _normalize_intensity(val: Any) -> str:
        if not val:
            return "Mid"
        raw = str(val).strip().lower()
        mapping = {
            "low": "Low", "낮음": "Low", "하": "Low", "약": "Low", "약함": "Low",
            "mid": "Mid", "medium": "Mid", "중": "Mid", "중간": "Mid", "보통": "Mid",
            "high": "High", "높음": "High", "상": "High", "강": "High", "강함": "High",
            "extreme": "Extreme", "극": "Extreme", "극단": "Extreme", "매우 강함": "Extreme",
        }
        return mapping.get(raw, "Mid")

    @staticmethod
    def _normalize_polarity(val: Any) -> str:
        if not val:
            return "mixed"
        raw = str(val).strip().lower()
        mapping = {
            "positive": "positive", "긍정": "positive", "호재": "positive", "좋음": "positive", "유리": "positive",
            "negative": "negative", "부정": "negative", "악재": "negative", "나쁨": "negative", "불리": "negative",
            "mixed": "mixed", "혼합": "mixed", "중립": "mixed", "중립적": "mixed",
        }
        return mapping.get(raw, "mixed")

    # ----- Timing Decision -----

    def _decide_timing(self, bus, st_state: dict, current_turn: int, for_clock: bool = False) -> str:
        """Determine act/defer/skip based on timing table + overrides + DAI.

        for_clock=True: 시계 완성 판정용 — 굶주림 머리를 시계 완성 항목들 중에서 잡는다(C74 W1-6)."""
        dai = bus.dai

        # Override 0: 間 페이즈 (후일담) — anomaly 강제 defer (정적/회상 톤만 통과)
        if bus.doom.get("intermission_active"):
            bus.anomaly["decision_reason"] = "intermission_defer"
            return "defer"

        # Override 1: Force act (climax push)
        if bus.anomaly.get("_force_act"):
            return "act"

        # Override 2: Batch skip (2nd+ PC in batch — reuse existing data)
        if bus.anomaly.get("_skip_storyteller"):
            return "keep"

        # Override 3: Judgment active → defer (scene overload prevention)
        if bus.judgment.get("active"):
            return "defer"

        # Starvation: queued event waiting 3+ turns → force act
        # [2026-10-24 C74 W1-6] 머리 = 시계 완성이 아닌 첫 항목. 옛 queue[0] 은 시계 완성 항목의 나이로 **이변** 굶주림을
        #   터뜨렸다(시계는 아래 clock_queue 의 non_intimate_turns 셈이 따로 맡는다).
        #   시계 판정(for_clock)은 거꾸로 시계 완성 항목들의 머리 — 옛 셈에서 시계가 머리일 때 3턴 굶주림으로
        #   발사되던 박자를 그대로 둔다(다른 이변이 더 늙었다고 시계가 끌려 터지지는 않는다).
        queue = [e for e in (st_state.get("event_queue") or [])
                 if isinstance(e, dict) and ((e.get("type") == "clock_completion") == bool(for_clock))]
        if queue:
            oldest_queued_turn = queue[0].get("queued_turn", 0)
            if current_turn - oldest_queued_turn >= _cfg.STORYTELLER_STARVATION_TURNS:
                # [2026-07-15] 하드 억제 씬(allowed=빈 집합: combat/intimate)은 굶주림도 못 뚫는다.
                # ⚠ 이 return이 아래 씬 필터(L111~)를 건너뛴다 — 굶주림이 발동하면 combat/intimate
                #   한복판에 이벤트가 터졌다. _select_event의 소프트 필터도 `if allowed is not None
                #   and allowed:`라 빈 집합이면 falsy → 거기서도 안 걸러 **두 번 뚫림**.
                # 잠복 버그였다가 이번에 도달 가능해졌음: ANOMALY_DETECTION의 null 탈출구를
                # 좁히기(같은 날) 전엔 제안률 0 → 큐가 늘 비어 굶주림 자체가 발동한 적 없음.
                # 제안이 흐르기 시작하니 3턴마다 발동 → 하드 억제가 무력화.
                # 소프트 불일치(social에 psychological 등, allowed 비어있지 않음)는 그대로 통과 —
                # 굶주림은 압력 밸브고, _select_event가 `if filtered:`로 이미 소프트 처리 중.
                _st_starv = dai.get("scene_type", "normal")
                _allowed_starv = _cfg.STORYTELLER_SCENE_CATEGORIES.get(_st_starv)
                if _allowed_starv is not None and not _allowed_starv:
                    bus.anomaly["decision_reason"] = f"scene_suppression:{_st_starv}"
                    return "defer"
                return "act"

        # No proposal and no queue → nothing to schedule
        has_proposal = bool(bus.anomaly.get("tag"))
        if not has_proposal and not queue:
            return "skip"

        # Timing table lookup
        energy = dai.get("energy_direction", "idle")
        last_event_turn = st_state.get("last_event_turn", 0)
        turns_since = max(0, current_turn - last_event_turn)
        turns_key = min(turns_since, 3)  # 3+ capped

        row = TIMING_TABLE.get(energy, TIMING_TABLE["rising"])
        decision = row.get(turns_key, "defer")

        # DAI promotion: defer → act
        if decision == "defer":
            quality_flags = dai.get("quality_flags") or {}  # [07-27] 명시 null 방어
            if quality_flags.get("stagnation_warning"):
                decision = "act"
                bus.anomaly["decision_reason"] = "stagnation_promotion"
            elif quality_flags.get("convergence_warning"):
                decision = "act"
                bus.anomaly["decision_reason"] = "convergence_promotion"

        # DAI suppression: act → defer (scene_type filter)
        if decision == "act":
            scene_type = dai.get("scene_type", "normal")
            allowed = _cfg.STORYTELLER_SCENE_CATEGORIES.get(scene_type)
            if allowed is not None:  # None = all allowed
                # Check if current proposal's category is allowed
                proposal_cat = (bus.anomaly.get("category") or "").lower()
                if not allowed:  # empty set = skip all
                    decision = "defer"
                    bus.anomaly["decision_reason"] = f"scene_suppression:{scene_type}"
                elif proposal_cat and proposal_cat not in allowed:
                    # Check queue for allowed events
                    has_allowed = any(
                        ((e.get("category") or "").lower() in allowed) for e in queue
                    )
                    if not has_allowed:
                        decision = "defer"
                        bus.anomaly["decision_reason"] = f"category_mismatch:{scene_type}"

        return decision

    # ----- Event Selection (when ACT) -----

    def _select_event(self, bus, st_state: dict) -> dict:
        """Select best event from queue + current proposal using diversity + polarity scoring."""
        dai = bus.dai
        candidates: List[dict] = []

        # Queued events
        # [2026-10-24 C74 판1 검토 #1] 시계 완성 항목은 고를 사건이 아니다 — doom 차례(위 clock_queue 처리)다.
        #   옛 셈은 이변 판정과 시계 판정이 같은 굶주림 머리를 써서, 이변이 act 면 시계가 먼저 터지고 이변은 defer 였다
        #   (여기까지 시계 항목이 온 적이 없다). W1-6 으로 두 판정의 머리가 갈리자 시계 항목이 보통 이변으로 뽑혀
        #   큐에서 빠지고 시계는 대기로 남아 영영 안 터졌다. 번호(_queue_index)는 전체 큐 기준 그대로 — 꺼낼 때 맞는다.
        for i, event in enumerate(st_state.get("event_queue", [])):
            if isinstance(event, dict) and event.get("type") == "clock_completion":
                continue
            candidates.append({**event, "_source": "queue", "_queue_index": i})

        # Current Flash proposal
        tag = bus.anomaly.get("tag", "")
        if tag:
            candidates.append({
                "tag": tag,
                "category": bus.anomaly.get("category") or tag,
                "intensity": bus.anomaly.get("intensity", "Mid"),
                "polarity": bus.anomaly.get("polarity", "mixed"),
                "line": bus.anomaly.get("line", ""),
                "reason": bus.anomaly.get("reason", ""),
                "replaces": bus.anomaly.get("replaces", ""),   # [2026-10-10 큐 이어짐] 확전이 갈아 끼울 조건 tag
                "location": bus.anomaly.get("location", ""),   # [2026-10-24 C75 G2] 빛 자리(관측 라우터)
                "_source": "flash",
            })

        if not candidates:
            return {}

        # Scene-type filter
        scene_type = dai.get("scene_type", "normal")
        allowed = _cfg.STORYTELLER_SCENE_CATEGORIES.get(scene_type)
        if allowed is not None and allowed:
            filtered = [c for c in candidates if (c.get("category") or "").lower() in allowed]
            if filtered:
                candidates = filtered

        # Score each candidate
        recent_tags = st_state.get("recent_tags", [])
        recent_cats = st_state.get("recent_categories", [])
        position = dai.get("position", {})
        pos_label = position.get("label", "") if isinstance(position, dict) else ""
        _qf = dai.get("quality_flags", {})
        convergence = _qf.get("convergence_warning", False) if isinstance(_qf, dict) else False

        scored = []
        for c in candidates:
            score = 1.0

            # Diversity penalty: tag overlap
            if c.get("tag") in recent_tags:
                score -= 0.5

            # Diversity penalty: category overlap
            cat = c.get("category", "")
            cat_count = recent_cats.count(cat)
            if cat_count > 0:
                score -= 0.3 * cat_count

            # Polarity bonus (DAI-driven)
            pol = c.get("polarity", "mixed")
            if pos_label == "weak" and pol == "positive":
                score += 0.2
            elif pos_label == "strong" and pol == "negative":
                score += 0.2
            if convergence and pol in ("negative", "mixed"):
                score += 0.3

            # [2026-10-10 던지는판 C1] 기한이 온 약속은 주변 사건보다 앞선다
            if c.get("type") == "thread_due":
                score += 0.5

            # Queue events get tiebreaker priority (FIFO fairness)
            if c.get("_source") == "queue":
                score += 0.01

            # [Reader-GM R4b] 독자 지속 축 부스트 — 신설 없이 기존 후보의 가점만.
            # 매칭=독자 인용(한국어) vs 이벤트 line/reason 한글 bigram 겹침(>=3).
            _axes = bus.anomaly.get("_reader_axes") or []
            if _axes:
                _ev_bg = self._hangul_bigrams(f"{c.get('line', '')} {c.get('reason', '')}")
                if _ev_bg and any(
                    len(self._hangul_bigrams(a) & _ev_bg) >= 3 for a in _axes
                ):
                    score += 0.25
                    logger.info("[ReaderBoost] '%s' +0.25 (reader-persistent axis overlap)",
                                c.get("tag", ""))

            scored.append((score, c))

        scored.sort(key=lambda x: x[0], reverse=True)
        return scored[0][1] if scored else {}

    # ----- Thread due → 던지는 판 (2026-10-10 C1) -----

    @staticmethod
    def _sync_thread_due(st_state: dict, channel_id: str, current_turn: int) -> int:
        """[2026-10-10 던지는판 C1] 스레드 장부의 기한 창 진입(now >= due_start, 장부 문구 "지금이 그때"·"오늘 중"·
        "때가 지났다") → 큐 항목 한 번(type thread_due). 닫힘·일시정지된 스레드의 항목은 뺀다. 넣은 수를 돌려준다.
        기한 창 계산은 장부(코드), 열림·닫힘은 배경 추출 전이. 스펙 analysis_line/던지는판·NPC자율_스펙_2026-10-10 §2 C1."""
        if not channel_id:
            return 0
        import thread_ledger as _tl
        states = _tl.load_states(channel_id)
        queue = st_state.get("event_queue") or []
        open_ids = {tid for tid, st in (states or {}).items() if isinstance(st, dict) and st.get("status") == "open"}
        queue = [e for e in queue if not (isinstance(e, dict) and e.get("type") == "thread_due"
                                          and e.get("thread_id") not in open_ids)]
        added = 0
        if states:
            now = _tl.now_abs(_tl._world_of(channel_id, None))
            done = set(st_state.get("due_queued_ids") or [])
            for tid, st in states.items():
                if not isinstance(st, dict) or st.get("status") != "open" or tid in done:
                    continue
                ds = st.get("due_start")
                if ds is None or now is None or now < int(ds):
                    continue
                title = str(st.get("title") or "").strip() or str(tid)
                parties = ", ".join([str(p) for p in (st.get("parties") or [])][:3])
                queue.append({
                    "type": "thread_due", "thread_id": tid, "tag": f"thread_due:{tid}",
                    "title": title,   # [2026-10-24 C74 W1-5] 시스템 줄 "[기한] 제목" 용 — 태그(기계 이름) 대신
                    "category": "social", "intensity": "Mid", "polarity": "mixed",
                    "line": f"{title}: its time has come" + (f" ({parties})" if parties else ""),
                    "reason": "thread ledger due window reached", "queued_turn": current_turn,
                })
                done.add(tid)
                added += 1
            st_state["due_queued_ids"] = sorted(done)[-50:]
        st_state["event_queue"] = queue
        if added:
            logger.info("[Storyteller] thread due → queue +%d", added)
        return added

    @staticmethod
    def _sync_clock_completion(st_state: dict, channel_id: str, clocks=None) -> int:
        """[2026-10-24 C74 W1-1] 시계 완성 항목 동기화 — `_sync_thread_due` 의 짝.

        큐의 clock_completion 은 발동(아래 clock_queue 처리)으로만 빠졌다. 그 사이 시계가 퀘스트 완료 ·
        Theoria 해결 · 소멸 · 클라이맥스로 끝나도 항목이 남아, 머리에 서면 발동 판정 → doom §0 은 resolved 라
        건너뛰는데 이변만 defer(clock_mutual_exclusion) 됐다 — 아무 일도 없는 턴에 이변 자리만 뺏겼다.
        시계가 없거나 · 끝났거나(resolved) · 대기(pending_completion)가 아니면 뺀다. 끝나는 자리 아홉 곳을
        일일이 고치지 않고 큐 주인 한 곳에서. 뺀 수를 돌려준다. 스펙 C74 §2-① · W1-1.

        clocks = 이 턴 버스의 시계(bus.doom["clocks"] — 턴 끝 sync_from_game_context 가 저장할 바로 그 사본).
        [판1 검토 #2] 살아 있는 world_state 로 판정하면 안 된다: 앞 턴 배경이 이 턴 Theoria 도중 퀘스트 완료로 시계를
        끝내도, 턴 끝 저장은 버스의 옛 사본(대기 · 미해결)으로 덮는다 — 큐 항목만 빠지고 시계는 대기로 남아 영영 안 터졌다.
        없거나 비었을 때만 도메인을 읽는다 — 버스 기본값이 빈 목록이라, convert_to_game_context 를 안 거친 버스(스모크 ·
        도구)가 진짜 시계를 다 지우지 않게. 진짜로 시계가 0이면 도메인도 0이라 결과가 같다."""
        if not channel_id:
            return 0
        queue = st_state.get("event_queue") or []
        if not any(isinstance(e, dict) and e.get("type") == "clock_completion" for e in queue):
            return 0
        if not isinstance(clocks, list) or not clocks:
            import domain_manager as _dm
            clocks = (_dm.get_world_state(channel_id) or {}).get("doom_clocks") or []
        live = {str(c.get("name") or "") for c in clocks
                if isinstance(c, dict) and c.get("pending_completion") and not c.get("resolved")}
        kept = [e for e in queue if not (isinstance(e, dict) and e.get("type") == "clock_completion"
                                         and str(e.get("clock_name") or "") not in live)]
        dropped = len(queue) - len(kept)
        if dropped:
            st_state["event_queue"] = kept
            logger.info("[Storyteller] clock completion sync → queue −%d (clock already ended)", dropped)
        return dropped

    # ----- Thread Router (큐 이어짐) -----

    def _route_continuation(self, bus, st_state: dict, current_turn: int) -> str:
        """[2026-10-10 큐 이어짐] 이번 턴 제안이 잇는 실(continues)을 받아 이미 있는 입력으로 바꿔 넘긴다.

        E#(대기 사건)            → 그 큐 항목에 합침: line 갈기·steps+1·last_seen_turn·강도는 높은 쪽. 제안 비움.
        C#(서 있는 조건), 같거나 낮은 강도 → condition_updates 한 줄(1b가 처리). 제안 비움.
        C#, 높은 강도(확전)      → 제안에 replaces=조건 tag. 발화 판정은 그대로, 터지면 조건을 갈아 끼운다.
        없음·모르는 id           → 지금처럼(""). 새로 쌓는 장치는 없다 — 판정은 타이밍 표·1b·act 가지가 쥔다.
        """
        cid = str(bus.anomaly.pop("continues", "") or "").strip().upper()
        if not cid or not bus.anomaly.get("tag") or not cid[1:].isdigit():
            return ""
        idx = int(cid[1:]) - 1
        new_line = str(bus.anomaly.get("line") or "").strip()
        new_int = self._normalize_intensity(bus.anomaly.get("intensity", "Mid"))
        if cid[0] == "E":
            pend = pending_events(st_state)
            if not 0 <= idx < len(pend):
                return ""
            item = pend[idx]
            # [2026-10-25 C77 E-2 · Q32①] 충돌파는 이어짐으로 안 합친다 — 합치면 두 흐름이 부딪힌 그 줄(line)이 모델 문장으로
            #   덮였다. 제안은 비우지 않고 그대로 둔다(보통 제안처럼 판정을 탄다). 기한 줄은 제목 칸이 따로 있어 그대로 합친다.
            if item.get("type") == "collision":
                logger.info("[Storyteller] THREAD %s → collision item, not merged", cid)
                return ""
            if new_line:
                item["line"] = new_line
            item["steps"] = int(item.get("steps", 1) or 1) + 1
            item["last_seen_turn"] = current_turn
            if _INTENSITY_RANK.get(new_int, 1) > _INTENSITY_RANK.get(self._normalize_intensity(item.get("intensity")), 1):
                item["intensity"] = new_int
            self._clear_proposal(bus)
            bus.anomaly["thread_routed"] = {"id": cid, "to": "pending", "tag": item.get("tag", "")}
            logger.info("[Storyteller] THREAD %s ← %s (steps=%d)", cid, new_line[:60], item["steps"])
            return "pending"
        if cid[0] == "C":
            conds = st_state.get("active_conditions") or []
            if not 0 <= idx < len(conds) or not isinstance(conds[idx], dict) or not conds[idx].get("tag"):
                return ""
            ctag = conds[idx]["tag"]
            if _INTENSITY_RANK.get(new_int, 1) > _INTENSITY_RANK.get(self._normalize_intensity(conds[idx].get("intensity")), 1):
                bus.anomaly["replaces"] = ctag
                bus.anomaly["thread_routed"] = {"id": cid, "to": "escalate", "tag": ctag}
                logger.info("[Storyteller] THREAD %s escalates → %s (%s)", cid, new_line[:60], new_int)
                return "escalate"
            upd = bus.anomaly.get("condition_updates")
            upd = list(upd) if isinstance(upd, list) else []
            upd.append({"tag": ctag, "description": new_line} if new_line else {"tag": ctag})
            bus.anomaly["condition_updates"] = upd
            self._clear_proposal(bus)
            bus.anomaly["thread_routed"] = {"id": cid, "to": "condition", "tag": ctag}
            logger.info("[Storyteller] THREAD %s ← condition update: %s", cid, new_line[:60])
            return "condition"
        return ""

    @staticmethod
    def _clear_proposal(bus) -> None:
        for k in ("tag", "line", "reason", "category", "location", "replaces"):
            bus.anomaly.pop(k, None)

    @staticmethod
    def _sync_wells(bus, st_state: dict, channel_id: str, current_turn: int) -> int:
        """[2026-10-25 C75 G3] 우물 앞단 한 번 → 충돌파 대기열 항목(type collision — 만료 없음 · 관측 라우터 안 탐 ·
        전조 아님 · 박자표를 탄다). 넣은 수를 돌려준다."""
        if not channel_id:
            return 0
        import well_store as _wsf
        ev = _wsf.front(channel_id, pc_location=bus.dai.get("current_location"), turn=current_turn)
        # [2026-10-25 C77 A-9 · Q32③] 죽은 쓰기 `bus.anomaly["well_front"]` 삭제 — 읽는 곳 0(전수 grep, activity 포함).
        queue = st_state.get("event_queue", [])
        have = {e.get("tag") for e in queue if isinstance(e, dict)}
        n = 0
        for c in ev.get("collisions") or []:
            a, b = sorted((int(c["a"]), int(c["b"])))
            tag = f"collision:{a}-{b}"
            if tag in have:
                continue
            queue.append({"type": "collision", "tag": tag, "category": "environmental", "intensity": "High",
                          "polarity": "mixed", "line": c.get("line", ""), "reason": "two great wells met",
                          "title": c.get("title", ""), "queued_turn": current_turn})
            have.add(tag)
            n += 1
        if n:
            st_state["event_queue"] = queue
            logger.info("[Storyteller] collision queued x%d", n)
        return n

    # ----- Main Process -----

    @staticmethod
    def _hangul_bigrams(text: str) -> set:
        """[Reader-GM R4b] 한글 문자 bigram — 독자 인용↔이벤트 line/reason 겹침 매칭용(순수)."""
        import re as _re
        s = _re.sub(r"[^가-힣]", "", str(text))
        return {s[i:i + 2] for i in range(len(s) - 1)}

    async def process(self, context: GameContext) -> GameContext:
        bus = context.shared_bus

        if not bus.anomaly.get("potential"):
            return context

        # Load storyteller state
        st_state = bus.anomaly.get("_storyteller_state", {})
        # [Reader-GM R4b] 독자 지속 축(한국어 인용) 로드 — FEED=1일 때만. 선택 *가점* 전용(이벤트 신설 없음),
        # bus 휘발 키라 st_state 영속에 안 섞임. spec §6b.
        try:
            if getattr(_cfg, "READER_GM_FEED", 0):
                _ch_r = bus.anomaly.get("_channel_id", "")
                if _ch_r:
                    import domain_manager as _dm_r
                    _mem_r = _dm_r.get_session_ai_memory(_ch_r) or {}
                    bus.anomaly["_reader_axes"] = [
                        str(p.get("quote", "") or "")
                        for p in (_mem_r.get("reader_candidates") or []) if p.get("quote")
                    ][:8]
        except Exception:
            pass
        current_turn = bus.anomaly.get("_current_turn", 0)
        channel_id = bus.anomaly.get("_channel_id", "")

        # --- [2026-10-10 큐 이어짐] 길잡이 — 만료·조건 해소보다 먼저(Theoria가 본 E#·C# 번호가 그대로일 때) ---
        if not bus.anomaly.get("_skip_storyteller"):
            try:
                self._route_continuation(bus, st_state, current_turn)
            except Exception as _e_rt:
                logger.warning("[Storyteller] thread route failed: %s", _e_rt)
        # [2026-10-10 던지는판 C1] 스레드 기한 도래 → 큐(길잡이 뒤: Theoria가 본 E# 번호를 먼저 쓴다)
        try:
            self._sync_thread_due(st_state, channel_id, current_turn)
        except Exception as _e_td:
            logger.warning("[Storyteller] thread due sync failed: %s", _e_td)
        # [2026-10-24 C74 W1-1] 이미 끝난 시계의 완성 항목을 뺀다(같은 자리 — 길잡이 뒤. E# 번호는 pending_events 가
        #   clock_completion 을 빼고 매기므로 이 정리가 번호를 밀지 않는다).
        try:
            self._sync_clock_completion(st_state, channel_id, clocks=bus.doom.get("clocks"))
        except Exception as _e_cs:
            logger.warning("[Storyteller] clock completion sync failed: %s", _e_cs)
        # [2026-10-25 C75 G3] 우물 앞단 — 시계 맞춤 · 결전 들어섬/떠남 · 충돌파. 충돌 항목은 이 턴 사본(st_state)에 넣는다
        #   (모듈 끝에 저장 — 배경이 따로 쓰면 이 저장이 덮는다).
        # [G3 검토 #6] 배치 모드 두 번째 PC(_skip_storyteller)는 앞단을 안 돈다 — 그 PC 자리로 첫 PC 의 결전을 끝내거나
        #   'keep' 으로 저장 없이 돌아가며 충돌 항목을 흘리지 않게.
        if not bus.anomaly.get("_skip_storyteller"):
            try:
                self._sync_wells(bus, st_state, channel_id, current_turn)
            except Exception as _e_wf:
                logger.warning("[Storyteller] well front failed: %s", _e_wf)

        # --- event_queue 만료 정리 ---
        # queued_turn으로부터 EVENT_QUEUE_EXPIRY_TURNS 이상 묵힌 일반 이벤트는 폐기.
        # clock_completion 타입은 제외 — 기계적 약속이라 만료 안 함 (intimate 5턴 starvation은 별도).
        # starvation_turns(3) < expiry_turns(8) 이라 보통은 starvation으로 발사되고,
        # 발사 못한 채 8턴 이상 묵힌 건 "기회 놓침"으로 폐기.
        try:
            _exp_threshold = _cfg.EVENT_QUEUE_EXPIRY_TURNS
            queue = st_state.get("event_queue", [])
            kept = []
            expired_tags = []
            for ev in queue:
                if ev.get("type") in _NO_EXPIRE_TYPES:   # [2026-10-10 던지는판 C1] 기한 도래 약속도 만료 없음
                    kept.append(ev)
                    continue
                qt = ev.get("last_seen_turn", ev.get("queued_turn", current_turn))   # [2026-10-10 큐 이어짐] 살아 있는 실은 마지막 걸음부터
                if current_turn - qt >= _exp_threshold:
                    expired_tags.append(ev.get("tag", "?"))
                    continue
                kept.append(ev)
            if expired_tags:
                st_state["event_queue"] = kept
                logger.info(
                    "[Storyteller] Expired %d stale event(s): %s",
                    len(expired_tags), ", ".join(expired_tags),
                )
        except Exception as _e_expire:
            logger.warning("[Storyteller] event_queue expiry failed: %s", _e_expire)

        # --- Active Condition lifecycle ---

        # 1a. Process condition resolutions (from Flash)
        condition_resolved = bus.anomaly.pop("condition_resolved", [])
        if isinstance(condition_resolved, list) and condition_resolved:
            active_conds = st_state.get("active_conditions", [])
            resolved_set = set(condition_resolved)
            before_count = len(active_conds)
            active_conds = [c for c in active_conds if c.get("tag") not in resolved_set]
            st_state["active_conditions"] = active_conds
            resolved_count = before_count - len(active_conds)
            if resolved_count > 0:
                bus.anomaly["conditions_resolved_log"] = (
                    f"condition resolved: {', '.join(condition_resolved[:3])} ({resolved_count})"
                )
                logger.info("[Storyteller] Conditions resolved: %s", condition_resolved)

        # 1b. Process condition updates — severity transition (from Flash)
        condition_updates = bus.anomaly.pop("condition_updates", [])
        if isinstance(condition_updates, list):
            active_conds = st_state.get("active_conditions", [])
            for upd in condition_updates:
                if not isinstance(upd, dict):
                    continue
                upd_tag = upd.get("tag", "")
                if not upd_tag:
                    continue
                for cond in active_conds:
                    if cond.get("tag") == upd_tag:
                        if upd.get("intensity"):
                            cond["intensity"] = self._normalize_intensity(upd["intensity"])
                        if upd.get("description"):
                            cond["description"] = upd["description"]
                        logger.info("[Storyteller] Condition updated: %s → %s",
                                    upd_tag, upd.get("intensity", ""))
                        break
            st_state["active_conditions"] = active_conds

        # Normalize proposal
        tag = bus.anomaly.get("tag") or ""
        if tag:
            bus.anomaly["intensity"] = self._normalize_intensity(bus.anomaly.get("intensity", "Mid"))
            bus.anomaly["polarity"] = self._normalize_polarity(bus.anomaly.get("polarity"))
            if not bus.anomaly.get("category"):
                bus.anomaly["category"] = tag

        # ── clock_completion 분리 큐 처리 (anomaly보다 우선) ──
        clock_fired = False
        clock_queue = [e for e in st_state.get("event_queue", []) if e.get("type") == "clock_completion"]
        if clock_queue and not bus.judgment.get("active"):
            # non_intimate_turns 카운터 갱신 (intimate 턴은 세지 않음)
            scene_type = bus.dai.get("scene_type", "normal")
            for cq in clock_queue:
                if scene_type != "intimate":
                    cq["non_intimate_turns"] = cq.get("non_intimate_turns", 0) + 1

            # 타이밍 판단 (기존 _decide_timing 재사용)
            clock_decision = self._decide_timing(bus, st_state, current_turn, for_clock=True)
            # intimate 중에는 starvation도 무시 — non_intimate_turns 기준으로만
            top_clock = clock_queue[0]
            if scene_type == "intimate":
                clock_decision = "defer"
            elif top_clock.get("non_intimate_turns", 0) >= 5:
                clock_decision = "act"  # non-intimate 5턴 starvation 강제

            if clock_decision == "act":
                fire_list = bus.doom.get("_fire_completions", [])
                fire_list.append(top_clock["clock_name"])
                bus.doom["_fire_completions"] = fire_list
                # queue에서 제거
                queue = st_state.get("event_queue", [])
                st_state["event_queue"] = [
                    e for e in queue
                    if not (e.get("type") == "clock_completion" and e.get("clock_name") == top_clock["clock_name"])
                ]
                clock_fired = True
                logger.info("[Storyteller] Clock fired: %s (waited %d non-intimate turns)",
                            top_clock["clock_name"], top_clock.get("non_intimate_turns", 0))

        # Decide timing (상호 배제: clock 발동 턴에는 anomaly defer)
        if clock_fired:
            decision = "defer"
            bus.anomaly["decision_reason"] = "clock_mutual_exclusion"
        else:
            decision = self._decide_timing(bus, st_state, current_turn)
        bus.anomaly["decision"] = decision
        if not bus.anomaly.get("decision_reason"):
            energy = bus.dai.get("energy_direction", "idle")
            turns_since = max(0, current_turn - st_state.get("last_event_turn", 0))
            bus.anomaly["decision_reason"] = f"table:{energy}/t{turns_since}"

        if decision == "keep":
            # Batch mode: data already set by first PC
            bus.anomaly["triggered"] = True
            logger.info("[Storyteller] keep (batch reuse)")
            return context

        if decision == "act":
            # Select best event
            selected = self._select_event(bus, st_state)
            if selected:
                # === 관측 라우터 — [2026-10-24 C75 G2] 이변 = 우물이 내는 빛(질량 아님) ===
                #   빛 자리(이변 location → 노드, 비면 PC 노드)에서 가장 세게 당기는 우물 = 출처(당김 ≥ 0.25).
                #   밝기 = 출처 질량 ÷ (빛 자리 ↔ PC 홉 + 1)² — 빛 난 자리(레티어스 10-24 밤: "이변은 모델의 행동 능동성을
                #   올리자는 것" — PC 앞에 낸 이변은 늘 터지고 우물은 결만 붙인다). ≥ 1 무대 위 · 0.25~1 전언 · < 0.25 산문 0.
                #   **삼키는 건 이름 얻은 우물뿐** — 먼지는 안에서만 산다(보이는 곳이 없어 전언을 실어 갈 자리가 없다) → 터지고 결만.
                #   출처가 없으면 옛 발사 그대로. 약속 기한은 안 탄다. 옛 카테고리 흡수 · 격상 후보 · 초신성(a)은 은퇴.
                _normalized_cand = {
                    "category": selected.get("category", ""),
                    "tag": selected.get("tag", ""),
                    "intensity": self._normalize_intensity(selected.get("intensity", "Mid")),
                    "polarity": self._normalize_polarity(selected.get("polarity")),
                    "line": selected.get("line", ""),
                    "summary": selected.get("line", ""),
                }
                _route = "emit"  # 기본
                _obs = None
                # [2026-10-10 던지는판 C1] 약속 기한은 우물 빛이 아니다 · [C75 G3] 충돌파도(중력파는 안 막힌다)
                _is_due = selected.get("type") in ("thread_due", "collision")
                if not _is_due and channel_id:
                    try:
                        import well_store as _wsr
                        # 시계 맞춤은 입구의 우물 앞단(_sync_wells)이 이미 했다.
                        _obs = _wsr.observe(channel_id, selected.get("location") or "",
                                            f"{selected.get('line', '')} {selected.get('reason', '')}",
                                            pc_location=bus.dai.get("current_location"))
                        if _obs.get("well") is not None and _obs.get("tier") in ("glow", "dark") \
                                and _obs["well"].get("status") == "named":
                            _route = "backstage"
                    except Exception as _e_route:
                        logger.warning("[Storyteller] Well router error: %s", _e_route)
                        _obs = None

                if _route == "backstage":
                    # === 막후 — 안 터진다(전언 · 소문 / 산문 0). 그 우물 막후 칸에 흔적 ===
                    _w_obs = _obs["well"]
                    try:
                        _wsr.record_light(channel_id, _w_obs["id"], "bg", _normalized_cand, current_turn)
                    except Exception as _e_rl:
                        logger.warning("[Storyteller] well trace failed: %s", _e_rl)

                    # diversity 갱신 (시드 들어왔다는 사실 추적)
                    _cats = st_state.get("recent_categories", [])
                    _cats.append(_normalized_cand["category"])
                    st_state["recent_categories"] = _cats[-_cfg.STORYTELLER_DIVERSITY_WINDOW:]

                    # queue 출처면 제거
                    if selected.get("_source") == "queue":
                        _idx = selected.get("_queue_index", -1)
                        _queue = st_state.get("event_queue", [])
                        if 0 <= _idx < len(_queue):
                            _queue.pop(_idx)

                    bus.anomaly["triggered"] = False
                    if _obs.get("tier") == "glow":
                        # 전언이 닿는 갈래만 외부 사건 신호(aspects 가 읽는 모양 그대로). 산문 0(dark)은 신호도 0.
                        bus.anomaly["arc_absorbed"] = {"arc_id": _w_obs.get("id"), "accepted": True}
                    bus.anomaly["well"] = {"id": _w_obs.get("id"), "label": _w_obs.get("label", ""),
                                           "tier": _obs.get("tier"), "b": round(float(_obs.get("b", 0.0)), 3)}
                    logger.info(
                        "[Storyteller] BACKSTAGE → well#%s tier=%s b=%.2f pull=%.2f [%s]",
                        _w_obs.get("id"), _obs.get("tier"), float(_obs.get("b", 0.0)), float(_obs.get("pull", 0.0)),
                        _normalized_cand["tag"],
                    )
                    # 일반 발사 흐름 skip — active_condition / escalated 등록 X
                else:
                    # === 기존 일반 발사 흐름 ===
                    # "계속되면 이변이 아니다" — 이미 standing인 조건의 재발사는 [이변]으로
                    # 재공지하지 않는다(triggered=False → 배너/anomaly-shock 비트 억제). 조건은
                    # active_conditions에 그대로 남아 장면에 조용히 영향.
                    _sel_tag = selected.get("tag", "")
                    _already_active = any(
                        c.get("tag") == _sel_tag
                        for c in st_state.get("active_conditions", [])
                    )
                    # [2026-10-10 큐 이어짐] 확전(서 있는 조건을 잇는 더 센 걸음)은 터지되, 조건을 하나 더 세우지 않고
                    #   그 조건을 갈아 끼운다(아래 등록 자리).
                    _replaces = str(selected.get("replaces") or "")
                    _rep_cond = next((c for c in st_state.get("active_conditions", [])
                                      if _replaces and c.get("tag") == _replaces), None)
                    bus.anomaly["triggered"] = not _already_active
                    bus.anomaly["tag"] = _sel_tag
                    bus.anomaly["category"] = selected.get("category", "")
                    bus.anomaly["intensity"] = self._normalize_intensity(selected.get("intensity", "Mid"))
                    bus.anomaly["polarity"] = self._normalize_polarity(selected.get("polarity"))
                    bus.anomaly["line"] = selected.get("line", "")
                    bus.anomaly["reason"] = selected.get("reason", "")
                    bus.anomaly["source"] = selected.get("_source", "flash")
                    # [2026-10-24 C75 G2] 터지는 빛 — 출처 우물 결을 붙이고, 실제로 터졌을 때만 그 우물에 흔적. 무대 위(stage)면
                    #   전조 칸(떡밥), 먼지가 출처인 먼 빛이면 막후 칸. 이미 서 있는 조건의 재발사(triggered False)는 새 빛이 아니다.
                    bus.anomaly.pop("well", None)
                    if _obs and _obs.get("well") is not None:
                        _w_obs = _obs["well"]
                        _tier = _obs.get("tier")
                        bus.anomaly["well"] = {"id": _w_obs.get("id"), "label": _w_obs.get("label", ""),
                                               "tier": _tier, "b": round(float(_obs.get("b", 0.0)), 3)}
                        if bus.anomaly["triggered"]:
                            try:
                                _wsr.record_light(channel_id, _w_obs["id"], "fg" if _tier == "stage" else "bg",
                                                  _normalized_cand, current_turn)
                            except Exception as _e_rl:
                                logger.warning("[Storyteller] well trace failed: %s", _e_rl)
                    # [2026-10-24 C74 W1-5] 약속 기한 발사 — 사람이 읽는 시스템 줄은 태그(thread_due:T3) 대신 장부 제목.
                    #   제목 칸이 없는 옛 항목은 줄 머리("<제목>: its time has come")에서 떼어 낸다.
                    if selected.get("type") == "thread_due":
                        _dt = str(selected.get("title") or "").strip()
                        if not _dt:
                            _dt = str(selected.get("line") or "").split(": its time has come")[0].strip()
                        bus.anomaly["due_title"] = _dt
                    elif selected.get("type") == "collision":
                        # [G3 검토 #3] 충돌파도 기계 태그 대신 두 흐름 이름
                        bus.anomaly["due_title"] = str(selected.get("title") or "").strip()
                    else:
                        bus.anomaly.pop("due_title", None)

                    # Update storyteller state
                    st_state["last_event_turn"] = current_turn
                    st_state["total_events_fired"] = st_state.get("total_events_fired", 0) + 1

                    # Update diversity window
                    cats = st_state.get("recent_categories", [])
                    cats.append(selected.get("category", ""))
                    st_state["recent_categories"] = cats[-_cfg.STORYTELLER_DIVERSITY_WINDOW:]

                    tags = st_state.get("recent_tags", [])
                    tags.append(selected.get("tag", ""))
                    st_state["recent_tags"] = tags[-_cfg.STORYTELLER_DIVERSITY_WINDOW:]

                    # Remove selected from queue if it came from there
                    if selected.get("_source") == "queue":
                        idx = selected.get("_queue_index", -1)
                        queue = st_state.get("event_queue", [])
                        if 0 <= idx < len(queue):
                            queue.pop(idx)

                    # Register Active Condition (Fate Aspect + Ironsworn Location)
                    # 이미 standing이면 중복 등록 안 함(재공지 억제와 정합).
                    active_conds = st_state.get("active_conditions", [])
                    if _rep_cond is not None:
                        if selected.get("line"):
                            _rep_cond["description"] = selected.get("line", "")
                        _rep_cond["intensity"] = bus.anomaly["intensity"]
                        _rep_cond["escalated_turn"] = current_turn
                        logger.info("[Storyteller] condition escalated in place: %s → %s",
                                    _replaces, bus.anomaly["intensity"])
                    elif not _already_active and not _is_due and len(active_conds) < _cfg.ACTIVE_CONDITION_CAP:   # 기한 약속은 조건으로 안 선다(장부가 쥔다)
                        # [2026-10-24 C75 G2] 고른 이변의 자리 — 대기열에서 왔으면 그 항목의 location(옛 꼴은 이번 턴
                        #   Flash 제안의 location 을 대기열 이변에 붙였다).
                        cond_location = (selected.get("location") or "") if selected.get("_source") == "queue" \
                            else (bus.anomaly.get("location") or "")
                        if not cond_location:
                            cond_location = bus.dai.get("current_location", "") if hasattr(bus, "dai") else ""
                        active_conds.append({
                            "tag": selected.get("tag", ""),
                            "category": selected.get("category", ""),
                            "intensity": bus.anomaly["intensity"],
                            "polarity": bus.anomaly["polarity"],
                            "description": selected.get("line", ""),
                            "location": cond_location,
                            "turn_created": current_turn,
                        })
                        st_state["active_conditions"] = active_conds

                    # DC-09 배선: High/Extreme intensity 발현 시 escalated 플래그 설정.
                    # une_facade.py:1262가 이 플래그를 읽어 directive Aspects에
                    # "Loss of Control"을 추가한다. 배선 전에는 플래그가 영원히
                    # 설정되지 않아 Aspects가 죽어있었음 (컨슈머 고아 해소).
                    # [G3 검토 #3] 충돌파는 먼 데서 온 떨림 — PC 장면의 '통제를 넘었다'(Loss of Control)로 번지지 않게.
                    if bus.anomaly["intensity"] in ("High", "Extreme") and selected.get("type") != "collision":
                        bus.anomaly["escalated"] = True

                    logger.info("[Storyteller] ACT [%s] cat=%s int=%s pol=%s src=%s reason=%s escalated=%s",
                                bus.anomaly["tag"], bus.anomaly["category"],
                                bus.anomaly["intensity"], bus.anomaly["polarity"],
                                bus.anomaly["source"], bus.anomaly.get("decision_reason", ""),
                                bus.anomaly.get("escalated", False))
            else:
                bus.anomaly["triggered"] = False
                decision = "skip"
                bus.anomaly["decision"] = "skip"
                bus.anomaly["decision_reason"] = "no_candidates"
                logger.info("[Storyteller] skip (no candidates)")

        elif decision == "defer":
            bus.anomaly["triggered"] = False
            # Queue current proposal if it exists
            if tag:
                queue = st_state.get("event_queue", [])
                if len(queue) < _cfg.STORYTELLER_QUEUE_MAX:
                    queue.append({
                        "tag": tag,
                        "category": bus.anomaly.get("category", tag),
                        "intensity": bus.anomaly.get("intensity", "Mid"),
                        "polarity": bus.anomaly.get("polarity", "mixed"),
                        "line": bus.anomaly.get("line", ""),
                        "reason": bus.anomaly.get("reason", ""),
                        "queued_turn": current_turn,
                        **({"replaces": bus.anomaly["replaces"]} if bus.anomaly.get("replaces") else {}),   # [2026-10-10 큐 이어짐]
                        **({"location": bus.anomaly["location"]} if bus.anomaly.get("location") else {}),   # [C75 G2] 빛 자리
                    })
                    st_state["event_queue"] = queue
            logger.info("[Storyteller] defer [%s] queue_size=%d reason=%s",
                        tag or "(none)", len(st_state.get("event_queue", [])),
                        bus.anomaly.get("decision_reason", ""))

        else:  # skip
            bus.anomaly["triggered"] = False
            logger.info("[Storyteller] skip reason=%s", bus.anomaly.get("decision_reason", ""))

        # Omen: expose top queued event for Main model hint (PbtA Soft Move)
        # [2026-10-09 SD개편 S3] 전조는 한 사건에 한 번, 익기 직전에만 — 큐 머리가 굶주림 한 턴 전(기다린 턴 ==
        #   STORYTELLER_STARVATION_TURNS - 1)이고 아직 전조를 안 냈을 때. 옛 꼴은 이변이 안 터진 모든 턴(81%)에
        #   같은 문장을 실어 렌더가 매 턴 다시 그렸다("붉은 점" 반복). 근거: analysis_line/스토리디렉팅_개편_스펙_2026-10-09.
        queue = st_state.get("event_queue", [])
        # [2026-10-10 던지는판 C1] 전조는 이변 항목만 — 시계 완성·스레드 기한은 장부·시계 줄이 이미 앞서 알린다.
        top = _omen_top(queue)
        if top is not None and decision != "act":
            _waited = current_turn - int(top.get("queued_turn", current_turn) or current_turn)
            if (not top.get("omen_shown")
                    and _waited >= max(0, _cfg.STORYTELLER_STARVATION_TURNS - 1)):
                # [2026-10-10 전조 미루기] 전투·친밀(하드 억제 = 허용 범주 빈 집합)은 사건만이 아니라 전조도 미룬다.
                #   묘사만 미룬다: 쓰지 않고 남겨 둘 뿐(omen_shown 그대로) 발화 시점은 건드리지 않는다. 풀린 뒤 사건이 먼저
                #   터지면 전조는 안 나온다 — 레티어스 10-10 "묘사적 미루기, 다 끝나고 나오든 말든". (같은 날 넣었던 '전조 먼저'
                #   한 턴 보류는 뺐다.) 근거: analysis_line/전조미루기_구현보고_2026-10-10.
                _st_om = bus.dai.get("scene_type", "normal")
                _allowed_om = _cfg.STORYTELLER_SCENE_CATEGORIES.get(_st_om)
                if _allowed_om is not None and not _allowed_om:
                    if not top.get("omen_deferred"):
                        logger.info("[Storyteller] omen deferred (scene_suppression:%s) [%s]", _st_om, top.get("tag", ""))
                    top["omen_deferred"] = True
                else:
                    bus.anomaly["omen"] = {
                        "tag": top.get("tag", ""),
                        "line": top.get("line", ""),
                    }
                    top["omen_shown"] = True

        # Persist storyteller state
        if channel_id:
            import domain_manager
            domain_manager.update_storyteller_state(channel_id, st_state)

        return context
