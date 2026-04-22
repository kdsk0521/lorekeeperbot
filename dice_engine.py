"""
Lorekeeper UNE - Seven Dice Engine (W9)
=======================================
Narrative entropy engine, extracted from story_director.py for single-responsibility.

Origin: 거울공방 AGELAST preset. Caillois play classifications → 7 faces.

가시 3면 (Agon/Alea/Mimicry) = 능동적 창작 마찰 (Slot 19 렌더링 제약).
은닉 4면 (Silence/Broken/Ghost/Yours) = 수동적/부재의 힘 (Slot 16 분위기/메타).

Responsibilities:
- Derive scene_state from energy + quality_flags.
- Weighted random selection with 3-consecutive face blocking.
- Read/write recent_dice state via domain_manager.

Interface:
    DiceEngine.roll(channel_id, energy, quality_flags) -> dict
    {
        "face": str,          # canonical face id (agon/alea/mimicry/silence/broken/ghost/yours)
        "name": str,          # display name (Agon/적 etc.)
        "visible": bool,      # True = 가시(제약), False = 은닉(분위기)
        "effect": str,        # prompt-ready effect text
        "scene_state": str,   # which weight table was used
    }

Returns {} on any failure (safe degradation — caller can ignore).
"""

from __future__ import annotations

import logging
import random
from typing import Any, Dict, List, Optional

import config
import domain_manager

logger = logging.getLogger("DiceEngine")


class DiceEngine:
    """Stateless roller. State lives in domain_manager (storyteller_state.recent_dice)."""

    # ---------------------------------------------------------------
    # Scene state resolution
    # ---------------------------------------------------------------
    @staticmethod
    def determine_scene_state(energy: Any, quality_flags: Optional[Dict[str, Any]]) -> str:
        """energy_direction + quality_flags → dice weight 카테고리."""
        if not isinstance(quality_flags, dict):
            quality_flags = {}
        if quality_flags.get("stagnation_warning") or energy == "stagnant":
            return "scene_stagnant"
        if quality_flags.get("convergence_warning"):
            return "scene_repetitive"
        return "default"

    # ---------------------------------------------------------------
    # State I/O
    # ---------------------------------------------------------------
    @staticmethod
    def _load_recent_dice(channel_id: Optional[str]) -> List[str]:
        if not channel_id:
            return []
        try:
            st = domain_manager.get_storyteller_state(channel_id)
            if isinstance(st, dict):
                rd = st.get("recent_dice", [])
                if isinstance(rd, list):
                    return list(rd)
        except Exception as e:
            logger.warning("[DiceEngine] load recent_dice failed: %s", e)
        return []

    @staticmethod
    def _persist_face(channel_id: Optional[str], face: str) -> None:
        if not channel_id or not face:
            return
        try:
            st = domain_manager.get_storyteller_state(channel_id)
            if not isinstance(st, dict):
                st = {}
            rd = list(st.get("recent_dice", [])) if isinstance(st.get("recent_dice"), list) else []
            rd.append(face)
            cap = getattr(config, "DICE_HISTORY_CAP", 10)
            st["recent_dice"] = rd[-cap:]
            domain_manager.update_storyteller_state(channel_id, st)
        except Exception as e:
            logger.warning("[DiceEngine] persist face=%s failed: %s", face, e)

    # ---------------------------------------------------------------
    # Core roll
    # ---------------------------------------------------------------
    @staticmethod
    def _pick_face(scene_state: str, recent_dice: List[str]) -> str:
        """Weighted random with 3-consecutive blocking."""
        weights_table = getattr(config, "DICE_WEIGHTS", {}) or {}
        default_weights = weights_table.get("default", {})
        weights = dict(weights_table.get(scene_state, default_weights))
        if not weights:
            return "silence"

        # 3-consecutive prevention: if last two faces are identical, block that face
        if isinstance(recent_dice, list) and len(recent_dice) >= 2:
            if recent_dice[-1] == recent_dice[-2]:
                blocked = recent_dice[-1]
                if blocked in weights:
                    weights[blocked] = 0.0

        total = sum(weights.values())
        if total <= 0:
            return "silence"

        names = list(weights.keys())
        probs = [weights[n] / total for n in names]
        return random.choices(names, weights=probs, k=1)[0]

    @staticmethod
    def roll(
        channel_id: Optional[str],
        energy: Any,
        quality_flags: Optional[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """
        Roll one Seven Dice face. Persists result to storyteller_state.recent_dice.

        Returns dice result dict, or {} on failure (caller should treat empty as skip).
        """
        try:
            scene_state = DiceEngine.determine_scene_state(energy, quality_flags)
            recent_dice = DiceEngine._load_recent_dice(channel_id)
            chosen = DiceEngine._pick_face(scene_state, recent_dice)

            seven = getattr(config, "SEVEN_DICE", {}) or {}
            info = seven.get(chosen, {})
            result = {
                "face": chosen,
                "name": info.get("name", chosen),
                "visible": bool(info.get("visible", False)),
                "effect": info.get("effect", ""),
                "scene_state": scene_state,
            }

            DiceEngine._persist_face(channel_id, chosen)
            return result
        except Exception as e:
            logger.warning("[DiceEngine] Roll failed: %s", e)
            return {}
