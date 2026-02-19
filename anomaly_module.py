"""
Lorekeeper UNE - Anomaly Module (v3.1 — Genre Disruption Engine)
Handles anomaly events with genre-aware disruption axes, theory-based defense,
2-axis damage, and 2-level adaptation taxonomy.
"""

import random
import math
from typing import Any
from orchestration_context import GameContext
import config as _cfg
import game_character as _gc


def calculate_adaptation(adaptation_groups: list, player_adaptation: dict) -> int:
    """2단계 적응도: 직접 100% + 같은 상위 카테고리 내 전이 50%.
    Returns max adaptation percentage (0-100)."""
    direct_pcts = []
    for g in adaptation_groups:
        count = player_adaptation.get(g, {}).get("count", 0)
        pct = min(100, int(math.log(count + 1) * 25))
        direct_pcts.append(pct)

    transfer_pcts = []
    for g in adaptation_groups:
        parent = _cfg.get_parent_category(g)
        if not parent:
            continue
        for sibling in _cfg.ADAPTATION_TAXONOMY[parent]:
            if sibling in adaptation_groups:
                continue  # 이미 직접 계산함
            count = player_adaptation.get(sibling, {}).get("count", 0)
            if count > 0:
                pct = min(100, int(math.log(count + 1) * 25))
                transfer_pcts.append(int(pct * 0.5))  # 50% 전이

    all_pcts = direct_pcts + transfer_pcts
    return max(all_pcts) if all_pcts else 0


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

    @staticmethod
    def _resolve_disruption_axis(bus, mechanic: dict) -> tuple:
        """Flash의 disruption_axis를 우선 사용, 없으면 mechanic → GENRE_DISRUPTION_AXIS → fallback.
        Returns (primary_axis, secondary_axis, secondary_ratio)."""
        flash_axis = bus.anomaly.get("disruption_axis", "").lower().strip()

        # mechanic_profile 우선, 없으면 legacy config fallback
        default_primary = mechanic.get("primary_resource") or "vigor"
        secondary_ratio = mechanic.get("secondary_ratio", 0.3)

        if flash_axis == "both":
            return "vigor", "composure", 1.0
        elif flash_axis == "vigor":
            primary = "vigor"
        elif flash_axis == "composure":
            primary = "composure"
        else:
            primary = default_primary

        secondary = "composure" if primary == "vigor" else "vigor"
        return primary, secondary, secondary_ratio

    def _roll_defense(self, context: GameContext, intensity: str, mechanic: dict) -> dict:
        """장르 교란 방어 롤: passive + 보호 아이템 + 장르별 방어 스탯 + 이론 보정."""
        bus = context.shared_bus
        dc_map = {"Low": 30, "Mid": 50, "High": 70, "Extreme": 90}
        dc = dc_map.get(intensity, 50)
        success_rate = 100 - dc

        primary_lens = mechanic.get("primary_lens", "")

        # Passive 보정 (theory tag modifier system)
        passives = (context.narrative_anchors or {}).get("passives", [])
        passive_defense = 0
        for passive in passives:
            mods = _cfg.get_passive_modifiers(passive)
            if not mods:
                continue
            # Genre-specific key first, then generic fallback
            genre_key = f"anomaly_defense_{primary_lens}" if primary_lens else ""
            if genre_key and genre_key in mods:
                passive_defense += mods[genre_key]
            elif "anomaly_defense" in mods:
                passive_defense += mods["anomaly_defense"]
        success_rate += max(-30, min(30, passive_defense))

        # 아이템 방어 보정 (modifier 기반, Phase 4-1b)
        inventory_items = _gc.get_inventory_items(context.narrative_anchors)
        item_defense = 0
        for item in inventory_items:
            mods = _cfg.get_item_modifiers(item)
            if not mods:
                continue
            genre_key = f"anomaly_defense_{primary_lens}" if primary_lens else ""
            if genre_key and genre_key in mods:
                item_defense += mods[genre_key]
            elif "anomaly_defense" in mods:
                item_defense += mods["anomaly_defense"]
        success_rate += max(-30, min(30, item_defense))

        # 방어 스탯: mechanic.primary_resource 우선, legacy fallback
        defense_stat = mechanic.get("primary_resource") or "vigor"
        defense_val = getattr(bus, defense_stat).get("value", 100)
        if defense_val >= 70:
            success_rate += 10
        elif defense_val <= 14:
            success_rate -= 20
        elif defense_val <= 39:
            success_rate -= 10

        # 이론 기반 방어 보정 (Flash theory_basis 매칭 시 +5)
        flash_theory = bus.anomaly.get("theory_basis", "")
        # Legacy fallback: GENRE_DISRUPTION_AXIS for theory matching
        genre_config = _cfg.GENRE_DISRUPTION_AXIS.get(primary_lens, {})
        genre_theory = genre_config.get("defense_theory", "")
        if flash_theory and genre_theory:
            flash_theories = set(t.strip().lower() for t in flash_theory.replace("+", ",").split(",") if t.strip())
            genre_theories = set(t.strip().lower() for t in genre_theory.replace("+", ",").split(",") if t.strip())
            if flash_theories & genre_theories:
                success_rate += 5

        success_rate = max(10, min(90, success_rate))
        defense_roll = random.randint(1, 100)
        return {"success": defense_roll <= success_rate, "roll": defense_roll, "rate": success_rate}

    def _calculate_trigger_chance(self, context: GameContext, mechanic: dict) -> float:
        """v3: 이변 트리거 확률은 둠과 무관한 고정값."""
        _ = (context, mechanic)  # kept for interface stability
        return float(getattr(_cfg, "ANOMALY_BASE_CHANCE", 15.0))

    async def process(self, context: GameContext) -> GameContext:
        bus = context.shared_bus
        mechanic = context.request.genres.get("mechanic", {})

        # 배치 모드: skip_trigger면 트리거 롤 스킵, 방어/적응만 수행
        if bus.anomaly.get("skip_trigger"):
            if not bus.anomaly.get("potential"):
                return context
            bus.anomaly["triggered"] = True
            roll = 50  # 중립 롤 (영감/쇼크 비활성)
        else:
            if not bus.anomaly.get("potential"):
                return context

            trigger_chance = self._calculate_trigger_chance(context, mechanic)

            roll = random.randint(1, 100)
            if roll > trigger_chance:
                return context

            bus.anomaly["triggered"] = True

        # 1. Anomaly Info
        tag = bus.anomaly.get("tag") or "기이한 현상"
        intensity = self._normalize_intensity(bus.anomaly.get("intensity", "Mid"))
        polarity = self._normalize_polarity(bus.anomaly.get("polarity"))

        category = bus.anomaly.get("category") or tag
        bus.anomaly["category"] = category
        bus.anomaly["intensity"] = intensity
        bus.anomaly["polarity"] = polarity

        intensity_label = {"Low": "낮음", "Mid": "중간", "High": "높음", "Extreme": "극단"}.get(intensity, intensity)
        polarity_label = {"positive": "호재", "negative": "악재", "mixed": "혼합"}.get(polarity, polarity)

        # 2. Adaptation & Mitigation (2-level taxonomy)
        adaptation_data = bus.vigor.get("adaptation", {})

        # adaptation_group: Flash/seed 제공, 없으면 category fallback
        adaptation_groups = bus.anomaly.get("adaptation_group", [])
        if not adaptation_groups:
            adaptation_groups = [category]

        adapt_old_pct = calculate_adaptation(adaptation_groups, adaptation_data)
        # Calculate new pct (after this exposure)
        projected = dict(adaptation_data)
        for g in adaptation_groups:
            old_count = projected.get(g, {}).get("count", 0)
            projected[g] = {"count": old_count + 1}
        adapt_new_pct = calculate_adaptation(adaptation_groups, projected)

        bus.anomaly["adapt_pct"] = adapt_old_pct
        bus.anomaly["adapt_new_pct"] = adapt_new_pct

        damage_map = {"Low": 5, "Mid": 10, "High": 20, "Extreme": 35}
        base_dmg = damage_map.get(intensity, 10)

        if polarity == "positive":
            base_dmg = -abs(base_dmg)
        elif polarity == "mixed":
            base_dmg = int(base_dmg * 0.5)

        # Outcome hooks (reserved for v3 matrix expansion)
        outcome_msg = ""

        # Adaptation Mitigation (100% = 0 damage)
        mitigation = adapt_old_pct / 100.0
        final_dmg = int(base_dmg * (1.0 - mitigation)) if base_dmg > 0 else base_dmg

        # 3. Defense Roll (only if damage is positive)
        if base_dmg > 0:
            defense = self._roll_defense(context, intensity, mechanic)
            has_judgment = "judgment" in context.request.active_modules

            if defense["success"]:
                bus.anomaly["defense_success"] = True
                if has_judgment:
                    bus.dai["bonus"] = bus.dai.get("bonus", 0) + 10
                    bus.dai["reason"] = bus.dai.get("reason", "") + " [이변 대응 성공 +10]"
                    outcome_msg += " [🛡️대응 성공: 다음 판정 +10]"
                    bus.anomaly["defense_note"] = "다음 판정 +10"
                else:
                    final_dmg = int(final_dmg * 0.5)
                    outcome_msg += " [🛡️대응 성공]"
                    bus.anomaly["defense_note"] = "피해 감소"
            else:
                bus.anomaly["defense_success"] = False
                if has_judgment:
                    bus.dai["penalty"] = bus.dai.get("penalty", 0) + 10
                    bus.dai["reason"] = bus.dai.get("reason", "") + " [이변 대응 실패 -10]"
                    outcome_msg += " [❌대응 실패: 다음 판정 -10]"
                    bus.anomaly["defense_note"] = "다음 판정 -10"
                else:
                    outcome_msg += " [❌대응 실패]"
                    bus.anomaly["defense_note"] = "피해 유지"

        # 4. Update Bus — disruption axis routing (Flash > mechanic > fallback)
        primary, secondary, sec_ratio = self._resolve_disruption_axis(bus, mechanic)
        primary_bus = getattr(bus, primary)
        secondary_bus = getattr(bus, secondary)
        primary_bus["delta"] = primary_bus.get("delta", 0) - final_dmg
        secondary_bus["delta"] = secondary_bus.get("delta", 0) - int(final_dmg * sec_ratio)

        # Disruption axis log
        axis_label = {"vigor": "기력", "composure": "평정"}.get(primary, primary)
        adapt_key = f" · 적응키 {','.join(adaptation_groups)}" if adaptation_groups != [category] else ""
        bus.anomaly["output"] = (
            f"강도 {intensity_label} · 성격 {polarity_label} · 축 {axis_label} · "
            f"적응도 {adapt_old_pct}%{adapt_key}{outcome_msg}"
        )

        # 5. Adaptation Update for Sync (각 adaptation_group에 count+1)
        for g in adaptation_groups:
            old_count = adaptation_data.get(g, {}).get("count", 0)
            bus.vigor.setdefault("adaptation_update", {})[g] = {"count": old_count + 1}

        return context
