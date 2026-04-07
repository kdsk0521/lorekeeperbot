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
                return "act"

        # No proposal and no queue → nothing to schedule
        has_proposal = bool(bus.anomaly.get("tag"))
        if not has_proposal and not queue:
            return "skip"

        # Timing table lookup
        energy = dai.get("energy_direction", "rising")
        last_event_turn = st_state.get("last_event_turn", 0)
        turns_since = max(0, current_turn - last_event_turn)
        turns_key = min(turns_since, 3)  # 3+ capped

        row = TIMING_TABLE.get(energy, TIMING_TABLE["rising"])
        decision = row.get(turns_key, "defer")

        # DAI promotion: defer → act
        if decision == "defer":
            quality_flags = dai.get("quality_flags", {})
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
                        (e.get("category", "").lower() in allowed) for e in queue
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
                "_source": "flash",
            })

        if not candidates:
            return {}

        # Scene-type filter
        scene_type = dai.get("scene_type", "normal")
        allowed = _cfg.STORYTELLER_SCENE_CATEGORIES.get(scene_type)
        if allowed is not None and allowed:
            filtered = [c for c in candidates if c.get("category", "").lower() in allowed]
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

            # Queue events get tiebreaker priority (FIFO fairness)
            if c.get("_source") == "queue":
                score += 0.01

            scored.append((score, c))

        scored.sort(key=lambda x: x[0], reverse=True)
        return scored[0][1] if scored else {}

    # ----- Main Process -----

    async def process(self, context: GameContext) -> GameContext:
        bus = context.shared_bus

        if not bus.anomaly.get("potential"):
            return context

        # Load storyteller state
        st_state = bus.anomaly.get("_storyteller_state", {})
        current_turn = bus.anomaly.get("_current_turn", 0)
        channel_id = bus.anomaly.get("_channel_id", "")

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
                    f"🌤️ 조건 해소: {', '.join(condition_resolved[:3])} ({resolved_count}건)"
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

        # Decide timing
        decision = self._decide_timing(bus, st_state, current_turn)
        bus.anomaly["decision"] = decision
        if not bus.anomaly.get("decision_reason"):
            energy = bus.dai.get("energy_direction", "rising")
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
                bus.anomaly["triggered"] = True
                bus.anomaly["tag"] = selected.get("tag", "")
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
                active_conds = st_state.get("active_conditions", [])
                if len(active_conds) < _cfg.ACTIVE_CONDITION_CAP:
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

                logger.info("[Storyteller] ACT [%s] cat=%s int=%s pol=%s src=%s reason=%s",
                            bus.anomaly["tag"], bus.anomaly["category"],
                            bus.anomaly["intensity"], bus.anomaly["polarity"],
                            bus.anomaly["source"], bus.anomaly.get("decision_reason", ""))
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
                    })
                    st_state["event_queue"] = queue
            logger.info("[Storyteller] defer [%s] queue_size=%d reason=%s",
                        tag or "(none)", len(st_state.get("event_queue", [])),
                        bus.anomaly.get("decision_reason", ""))

        else:  # skip
            bus.anomaly["triggered"] = False
            logger.info("[Storyteller] skip reason=%s", bus.anomaly.get("decision_reason", ""))

        # Omen: expose top queued event for Main model hint (PbtA Soft Move)
        queue = st_state.get("event_queue", [])
        if queue and decision != "act":
            top = queue[0]
            bus.anomaly["omen"] = {
                "tag": top.get("tag", ""),
                "line": top.get("line", ""),
            }

        # Persist storyteller state
        if channel_id:
            import domain_manager
            domain_manager.update_storyteller_state(channel_id, st_state)

        return context
