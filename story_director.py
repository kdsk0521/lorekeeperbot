"""
Lorekeeper UNE - Story Director (v1.0)
Code-driven narrative pacing & direction engine.
Fills gaps: proactive plot advancement, idle input handling, scene transition direction.

NOT a prompt generator — outputs structured hints consumed by the response builder.
Sits at Stage 4.5 (after Anomaly, before Doom).

Design: AnomalyModule 패턴 — 점수 테이블 + 코드 결정, LLM 호출 없음.
"""

import logging
from typing import Any, Dict, List, Optional, Tuple
from orchestration_context import GameContext

logger = logging.getLogger("StoryDirector")

# =========================================================
# Pacing Table: scene_type × energy_direction → pacing advice
# Values: "push" (accelerate), "hold" (maintain), "breathe" (slow down), "pivot" (shift direction)
# =========================================================
PACING_TABLE: Dict[str, Dict[str, str]] = {
    "normal":      {"idle": "push",  "stagnant": "push",  "rising": "hold",    "detonation": "breathe", "aftershock": "hold"},
    "combat":      {"idle": "hold",  "stagnant": "hold",  "rising": "hold",    "detonation": "hold",    "aftershock": "breathe"},
    "social":      {"idle": "push",  "stagnant": "pivot", "rising": "hold",    "detonation": "breathe", "aftershock": "pivot"},
    "exploration": {"idle": "push",  "stagnant": "push",  "rising": "hold",    "detonation": "breathe", "aftershock": "hold"},
    "rest":        {"idle": "hold",  "stagnant": "hold",  "rising": "breathe", "detonation": "breathe", "aftershock": "hold"},
}

# Thread priority by chain status
CHAIN_PRIORITY: Dict[str, float] = {
    "OPEN": 0.3,
    "RISING": 0.6,
    "CLIMAX": 1.0,
    "FALLING": 0.4,
    "CLOSED": 0.0,
}

# Idle input detection keywords (short/empty/reactive-only inputs)
IDLE_INPUT_MARKERS = {
    "계속", "다음", "진행", "어떻게", "뭐해", "주변",
    "기다린다", "기다려", "지켜본다", "관찰", "둘러본다",
    "...", "…", "ㅇㅇ", "ㅇㅋ", "넹", "음",
}

# Scene transition mood mapping
TRANSITION_MOOD: Dict[str, Dict[str, str]] = {
    # energy → {doom_level_bucket → mood_hint}
    "idle":       {"low": "contemplative", "mid": "uneasy", "high": "dread"},
    "stagnant":   {"low": "listless", "mid": "tense", "high": "suffocating"},
    "rising":     {"low": "hopeful", "mid": "charged", "high": "desperate"},
    "detonation": {"low": "cathartic", "mid": "overwhelming", "high": "apocalyptic"},
    "aftershock": {"low": "reflective", "mid": "haunted", "high": "numb"},
}


class StoryDirector:
    """Stateless narrative direction engine. All state comes from SharedBus."""

    @staticmethod
    def process(context: GameContext) -> GameContext:
        """
        Main entry point. Reads bus data, computes direction hints.
        Writes to bus.dai["story_direction"].
        """
        bus = context.shared_bus
        dai = bus.dai

        # Gather inputs
        energy = dai.get("energy_direction", "rising")
        scene_type = dai.get("scene_type", "normal")
        narrative_chain = dai.get("narrative_chain", {})
        memory_triggers = dai.get("memory_triggers", [])
        user_input = context.request.user_input.strip()
        try:
            doom_value = int(bus.doom.get("value", 0))
        except (TypeError, ValueError):
            doom_value = 0
        emotion_summary = bus.emotion.get("summary", {})
        anomaly_triggered = bus.anomaly.get("triggered", False)
        anomaly_decision = bus.anomaly.get("decision", "skip")
        active_conditions = bus.anomaly.get("_storyteller_state", {}).get("active_conditions", [])
        quality_flags = dai.get("quality_flags", {})

        # 1. Pacing decision
        pacing = StoryDirector._decide_pacing(scene_type, energy)

        # 2. Idle input detection
        is_idle = StoryDirector._detect_idle_input(user_input)
        idle_direction = None
        if is_idle:
            idle_direction = StoryDirector._generate_idle_direction(
                energy, scene_type, active_conditions,
                narrative_chain, emotion_summary, doom_value
            )

        # 3. Plot thread tracking & advancement hints
        plot_hints = StoryDirector._analyze_plot_threads(
            narrative_chain, memory_triggers, active_conditions,
            quality_flags, energy
        )

        # 4. Scene transition guidance
        transition = StoryDirector._compute_transition(
            energy, doom_value, scene_type,
            anomaly_triggered, anomaly_decision, pacing
        )

        # 5. Focus guidance (who/what to spotlight)
        focus = StoryDirector._determine_focus(
            emotion_summary, active_conditions,
            narrative_chain, dai.get("relevant_npcs", [])
        )

        # 6. Tension axis (rising/falling/plateau)
        try:
            _vigor_val = int(bus.vigor.get("value", 70))
        except (TypeError, ValueError):
            _vigor_val = 70
        try:
            _composure_val = int(bus.composure.get("value", 70))
        except (TypeError, ValueError):
            _composure_val = 70
        tension_axis = StoryDirector._compute_tension_axis(
            energy, doom_value, narrative_chain,
            _vigor_val, _composure_val
        )

        # 7. Seven Dice (W9) — narrative entropy management
        dice_result = None
        try:
            scene_state = StoryDirector._determine_scene_state(energy, quality_flags)
            recent_dice = bus.anomaly.get("_storyteller_state", {}).get("recent_dice", [])
            dice_result = StoryDirector._roll_narrative_dice(scene_state, recent_dice)
        except Exception as e:
            logger.warning("[StoryDirector] Dice roll failed, skipping: %s", e)

        # Assemble direction output
        direction: Dict[str, Any] = {
            "active": True,
            "pacing": pacing,
            "tension_axis": tension_axis,
            "is_idle_input": is_idle,
            "plot_hints": plot_hints,
            "transition": transition,
            "focus": focus,
        }
        if idle_direction:
            direction["idle_direction"] = idle_direction
        if dice_result:
            direction["dice"] = dice_result

        # Write to bus
        dai["story_direction"] = direction

        # Log
        logger.info(
            "[StoryDirector] pacing=%s tension=%s idle=%s hints=%d focus=%s",
            pacing, tension_axis, is_idle, len(plot_hints), focus.get("spotlight", "none")
        )

        return context

    # ----- Pacing -----

    @staticmethod
    def _decide_pacing(scene_type: str, energy: str) -> str:
        """Pacing table lookup."""
        row = PACING_TABLE.get(scene_type, PACING_TABLE["normal"])
        return row.get(energy, "hold")

    # ----- Idle Input Detection -----

    @staticmethod
    def _detect_idle_input(user_input: str) -> bool:
        """Detect short/passive/idle inputs that need proactive direction."""
        if not user_input:
            return True
        cleaned = user_input.strip().lower()
        # Very short input (≤ 5 chars, excluding OOC)
        if len(cleaned) <= 5 and not cleaned.startswith(("(", "[")):
            return True
        # Known idle markers
        if cleaned in IDLE_INPUT_MARKERS:
            return True
        # Ellipsis-heavy
        if cleaned.replace(".", "").replace("…", "").strip() == "":
            return True
        return False

    @staticmethod
    def _generate_idle_direction(
        energy: str, scene_type: str,
        active_conditions: List[dict],
        narrative_chain: dict,
        emotion_summary: dict,
        doom_value: int
    ) -> Dict[str, Any]:
        """Generate proactive direction when input is idle/passive."""
        direction: Dict[str, Any] = {"type": "proactive"}

        # Priority 1: Active conditions with escalation potential
        if active_conditions:
            # Pick highest-intensity condition
            best = max(
                active_conditions,
                key=lambda c: {"Low": 1, "Mid": 2, "High": 3, "Extreme": 4}.get(
                    c.get("intensity", "Mid"), 2
                )
            )
            direction["source"] = "active_condition"
            direction["tag"] = best.get("tag", "")
            direction["hint"] = "condition_escalation"
            return direction

        # Priority 2: Unresolved narrative chain
        chain_status = narrative_chain.get("status", "OPEN")
        if chain_status in ("RISING", "CLIMAX"):
            direction["source"] = "narrative_chain"
            direction["hint"] = "chain_continuation"
            direction["chain_status"] = chain_status
            return direction

        # Priority 3: Emotional spike in an NPC → NPC-driven scene
        for npc_name, emo in emotion_summary.items():
            if not isinstance(emo, dict):
                continue
            _emo_intensity = 0.0
            try:
                _emo_intensity = float(emo.get("intensity", 0))
            except (TypeError, ValueError):
                pass
            if emo.get("spike") or _emo_intensity > 0.6:
                direction["source"] = "emotion"
                direction["hint"] = "npc_initiative"
                direction["npc"] = npc_name
                direction["emotion"] = emo.get("dominant", "neutral")
                return direction

        # Priority 4: High doom → environmental pressure
        if doom_value >= 60:
            direction["source"] = "doom"
            direction["hint"] = "environmental_pressure"
            return direction

        # Default: world-driven ambient progression
        direction["source"] = "ambient"
        direction["hint"] = "world_progression"
        return direction

    # ----- Plot Thread Analysis -----

    @staticmethod
    def _analyze_plot_threads(
        narrative_chain: dict,
        memory_triggers: List[Any],
        active_conditions: List[dict],
        quality_flags: dict,
        energy: str
    ) -> List[Dict[str, Any]]:
        """Score and rank unresolved plot threads for advancement hints."""
        threads: List[Dict[str, Any]] = []

        # Thread from narrative chain
        chain_status = narrative_chain.get("status", "OPEN")
        chain_priority = CHAIN_PRIORITY.get(chain_status, 0.3)
        if chain_status not in ("CLOSED",) and chain_priority > 0:
            thread = {
                "source": "narrative_chain",
                "label": narrative_chain.get("current_thread", "main_plot"),
                "priority": chain_priority,
                "type": "continuation",
            }
            # Stagnation boost
            if quality_flags.get("stagnation_warning"):
                thread["priority"] = min(1.0, thread["priority"] + 0.3)
                thread["urgency"] = "stagnation"
            # Convergence boost
            if quality_flags.get("convergence_warning"):
                thread["priority"] = min(1.0, thread["priority"] + 0.2)
                thread["urgency"] = "convergence"
            threads.append(thread)

        # Threads from active conditions
        for cond in active_conditions:
            intensity_score = {"Low": 0.2, "Mid": 0.4, "High": 0.7, "Extreme": 1.0}.get(
                cond.get("intensity", "Mid"), 0.4
            )
            threads.append({
                "source": "active_condition",
                "label": cond.get("tag", "unknown"),
                "priority": intensity_score,
                "type": "condition_thread",
                "polarity": cond.get("polarity", "mixed"),
            })

        # Threads from memory triggers
        for trigger in memory_triggers[:3]:  # Cap to avoid noise
            if isinstance(trigger, dict):
                threads.append({
                    "source": "memory",
                    "label": trigger.get("tag", trigger.get("key", "memory")),
                    "priority": 0.3,
                    "type": "memory_callback",
                })
            elif isinstance(trigger, str) and trigger:
                threads.append({
                    "source": "memory",
                    "label": trigger,
                    "priority": 0.3,
                    "type": "memory_callback",
                })

        # Sort by priority descending
        threads.sort(key=lambda t: t["priority"], reverse=True)

        # Return top threads (cap at 3 to avoid information overload)
        return threads[:3]

    # ----- Scene Transition -----

    @staticmethod
    def _compute_transition(
        energy: str, doom_value: int, scene_type: str,
        anomaly_triggered: bool, anomaly_decision: str, pacing: str
    ) -> Dict[str, Any]:
        """Compute scene transition mood and cut guidance."""
        # Doom bucket
        if doom_value < 30:
            doom_bucket = "low"
        elif doom_value < 65:
            doom_bucket = "mid"
        else:
            doom_bucket = "high"

        # Mood from table
        mood_row = TRANSITION_MOOD.get(energy, TRANSITION_MOOD["rising"])
        mood = mood_row.get(doom_bucket, "neutral")

        # Cut type: how to transition between beats
        if pacing == "push":
            cut = "hard_cut"  # Jump to next beat quickly
        elif pacing == "breathe":
            cut = "fade"  # Slow transition, room to settle
        elif pacing == "pivot":
            cut = "contrast_cut"  # Shift tone/location
        else:
            cut = "natural"  # Smooth continuation

        # If anomaly just triggered, override to dramatic entrance
        if anomaly_triggered:
            cut = "dramatic_entrance"

        transition = {
            "mood": mood,
            "cut": cut,
            "scene_type": scene_type,
        }

        # Suggest scene type shift if energy/pacing warrant it
        if pacing == "pivot" and scene_type in ("normal", "social"):
            transition["suggest_shift"] = "exploration"
        elif pacing == "breathe" and scene_type == "combat":
            transition["suggest_shift"] = "normal"

        return transition

    # ----- Focus Guidance -----

    @staticmethod
    def _determine_focus(
        emotion_summary: dict,
        active_conditions: List[dict],
        narrative_chain: dict,
        relevant_npcs: list
    ) -> Dict[str, Any]:
        """Determine who/what to spotlight this beat."""
        focus: Dict[str, Any] = {"spotlight": "none", "elements": []}

        # 1. Emotional NPC spotlight
        best_npc = None
        best_intensity = 0.0
        for npc_name, emo in emotion_summary.items():
            if not isinstance(emo, dict):
                continue
            try:
                intensity = float(emo.get("intensity", 0))
            except (TypeError, ValueError):
                intensity = 0.0
            # Spike NPCs get priority
            if emo.get("spike"):
                intensity += 0.5
            if intensity > best_intensity:
                best_intensity = intensity
                best_npc = npc_name

        if best_npc and best_intensity > 0.3:
            focus["spotlight"] = best_npc
            focus["reason"] = "emotional_intensity"
            focus["elements"].append({
                "type": "npc", "name": best_npc,
                "emotion": emotion_summary[best_npc].get("dominant", ""),
                "intensity": best_intensity,
            })

        # 2. Active condition elements (environmental focus)
        for cond in active_conditions[:2]:
            focus["elements"].append({
                "type": "condition",
                "tag": cond.get("tag", ""),
                "intensity": cond.get("intensity", "Mid"),
                "location": cond.get("location", ""),
            })

        # 3. Chain thread element
        chain_thread = narrative_chain.get("current_thread", "")
        if chain_thread:
            focus["elements"].append({
                "type": "thread",
                "label": chain_thread,
                "status": narrative_chain.get("status", "OPEN"),
            })

        # 4. Relevant NPCs from Theoria (non-overlapping with spotlight)
        for npc in relevant_npcs[:3]:
            npc_name = npc if isinstance(npc, str) else npc.get("name", "")
            if npc_name and npc_name != focus.get("spotlight"):
                focus["elements"].append({
                    "type": "npc_mention",
                    "name": npc_name,
                })

        return focus

    # ----- Tension Axis -----

    @staticmethod
    def _compute_tension_axis(
        energy: str, doom_value: int,
        narrative_chain: dict,
        vigor_value: int, composure_value: int
    ) -> str:
        """
        Compute aggregate tension direction: rising / falling / plateau / critical.
        Used by response builder for prose intensity calibration.
        """
        score = 0.0

        # Energy contribution
        energy_scores = {
            "idle": -0.2,
            "stagnant": 0.0,
            "rising": 0.3,
            "detonation": 0.8,
            "aftershock": -0.1,
        }
        score += energy_scores.get(energy, 0.0)

        # Doom contribution (normalized to 0-1)
        score += (doom_value / 100.0) * 0.4

        # Chain status contribution
        chain_score = {
            "OPEN": 0.0,
            "RISING": 0.2,
            "CLIMAX": 0.5,
            "FALLING": -0.2,
            "CLOSED": -0.3,
        }
        score += chain_score.get(narrative_chain.get("status", "OPEN"), 0.0)

        # Mental state contribution (low vigor/composure = higher tension)
        avg_mental = (vigor_value + composure_value) / 2
        if avg_mental < 30:
            score += 0.3
        elif avg_mental < 50:
            score += 0.1

        # Classify
        if score >= 0.8:
            return "critical"
        elif score >= 0.4:
            return "rising"
        elif score <= -0.1:
            return "falling"
        else:
            return "plateau"

    # ----- W9: Seven Dice -----

    @staticmethod
    def _determine_scene_state(energy: str, quality_flags: dict) -> str:
        """energy_direction + quality_flags → dice weight 카테고리."""
        if not isinstance(quality_flags, dict):
            quality_flags = {}
        if quality_flags.get("stagnation_warning") or energy == "stagnant":
            return "scene_stagnant"
        if quality_flags.get("convergence_warning"):
            return "scene_repetitive"
        return "default"

    @staticmethod
    def _roll_narrative_dice(scene_state: str, recent_dice: list) -> dict:
        """Seven Dice 가중 랜덤 선택. 3연속 방지."""
        import random
        import config as _cfg

        weights = dict(_cfg.DICE_WEIGHTS.get(scene_state, _cfg.DICE_WEIGHTS["default"]))

        # 3-consecutive prevention
        if isinstance(recent_dice, list) and len(recent_dice) >= 2:
            if recent_dice[-1] == recent_dice[-2]:
                blocked = recent_dice[-1]
                weights[blocked] = 0.0

        # Normalize and select
        total = sum(weights.values())
        if total <= 0:
            chosen = "silence"
        else:
            names = list(weights.keys())
            probs = [weights[n] / total for n in names]
            chosen = random.choices(names, weights=probs, k=1)[0]

        dice_info = _cfg.SEVEN_DICE.get(chosen, {})
        return {
            "face": chosen,
            "name": dice_info.get("name", chosen),
            "visible": dice_info.get("visible", False),
            "effect": dice_info.get("effect", ""),
            "scene_state": scene_state,
        }
