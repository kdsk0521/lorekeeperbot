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


_NO_EXPIRE_TYPES = ("clock_completion", "thread_due")


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

    def _decide_timing(self, bus, st_state: dict, current_turn: int) -> str:
        """Determine act/defer/skip based on timing table + overrides + DAI."""
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
        queue = st_state.get("event_queue", [])
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
        for i, event in enumerate(st_state.get("event_queue", [])):
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
            clock_decision = self._decide_timing(bus, st_state, current_turn)
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
                # === Arc 라우터 (Phase 2, spec v2 §4.0/§4.2) ===
                # 후보를 단일 분기로: absorb / promote_candidate / emit
                _normalized_cand = {
                    "category": selected.get("category", ""),
                    "tag": selected.get("tag", ""),
                    "intensity": self._normalize_intensity(selected.get("intensity", "Mid")),
                    "polarity": self._normalize_polarity(selected.get("polarity")),
                    "line": selected.get("line", ""),
                    "summary": selected.get("line", ""),
                }
                _route = "emit"  # 기본
                _target_arc = None
                _is_due = selected.get("type") == "thread_due"   # [2026-10-10 던지는판 C1] 약속 기한은 아크로 흡수하지 않는다
                # [2026-09-24 감사] 아크는 storyteller 상태가 아니라 narrative_tracker 상태(storylines)에 산다.
                #   전엔 st_state(storylines 키 없음)를 넘겨 흡수 분기 영구 None · 승격 임계의 "같은 카테고리
                #   활성 아크 없음" 검사가 공허(승격이 살아나면 같은 카테고리 아크 중복 승격 위험)였다.
                _nt_state_r = None
                try:
                    if channel_id:
                        import domain_manager as _dm_arc
                        _nt_state_r = _dm_arc.get_narrative_tracker_state(channel_id)
                except Exception as _e_ntl:
                    logger.debug("[Storyteller] nt_state load skip: %s", _e_ntl)
                _arc_state = _nt_state_r if isinstance(_nt_state_r, dict) else {}
                try:
                    import narrative_tracker as _nt
                    _target_arc = None if _is_due else _nt.find_absorbing_arc(_arc_state, _normalized_cand["category"])
                    if _target_arc:
                        _route = "absorb"
                    elif not _is_due and _nt.check_promote_threshold(
                        _arc_state, _normalized_cand,
                        st_state.get("recent_categories", []),
                        st_state.get("event_queue", []),
                        reader_axes=bus.anomaly.get("_reader_axes"),  # [Reader-GM R4b] 독자 1표 (현행 FEED=1 라이브 적재 — [2026-08-11 리더 §7] 구 "FEED=0이면 미적재" 정정)
                    ):
                        _route = "promote_candidate"
                except Exception as _e_route:
                    logger.warning("[Storyteller] Router error: %s", _e_route)

                if _route == "absorb":
                    # === (d) arc 흡수 — 일반 발사 흐름 skip ===
                    _was_armed = bool(_target_arc.get("armed"))
                    try:
                        _absorbed = _nt.absorb_to_arc(_target_arc, _normalized_cand, current_turn)
                    except Exception as _e_abs:
                        logger.warning("[Storyteller] absorb_to_arc failed: %s", _e_abs)
                        _absorbed = False

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
                    bus.anomaly["arc_absorbed"] = {
                        "arc_id": _target_arc.get("id"),
                        "accepted": bool(_absorbed),
                    }
                    logger.info(
                        "[Storyteller] ABSORB → arc#%s cat=%s accepted=%s",
                        _target_arc.get("id"), _normalized_cand["category"], _absorbed,
                    )
                    # Supernova 트리거(a): 이미 armed였던 arc에 같은 카테고리 시드 흡수 성공
                    # (spec §4.5 — 2026-07-06 배선). 이번 흡수로 armed된 경우는 다음 trigger 대기.
                    if _absorbed and _was_armed:
                        try:
                            _nt.supernova_branch(_target_arc, current_turn)
                        except Exception as _e_sn:
                            logger.warning("[Storyteller] supernova branch failed: %s", _e_sn)
                    # [2026-09-24 감사] 흡수는 in-place 변형이라 tracker 상태를 저장해야 남는다.
                    #   ⚠ 배경 추출(직전 턴)이 같은 상태를 들고 있으면 나중 저장이 이긴다 — best-effort.
                    if _absorbed and isinstance(_nt_state_r, dict) and channel_id:
                        try:
                            _dm_arc.update_narrative_tracker_state(channel_id, _nt_state_r)
                        except Exception as _e_nts:
                            logger.warning("[Storyteller] absorb save failed: %s", _e_nts)
                    # 일반 발사 흐름 skip — active_condition / escalated 등록 X
                else:
                    # === (e) promote_candidate 또는 (f) 일반 발사 ===
                    if _route == "promote_candidate":
                        bus.anomaly["arc_promote_candidate"] = dict(_normalized_cand)
                        logger.info(
                            "[Storyteller] PROMOTE_CANDIDATE cat=%s tag=%s",
                            _normalized_cand["category"], _normalized_cand["tag"],
                        )
                        # 1차 안전: PMU confirm 단계 도입 전엔 일반 발사도 함께 진행
                        # PMU가 다음 턴에 confirm하면 arc 등록 별도

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
                        cond_location = bus.anomaly.get("location") or ""
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
                    if bus.anomaly["intensity"] in ("High", "Extreme"):
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
