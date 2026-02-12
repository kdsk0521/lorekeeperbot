"""
Lorekeeper UNE - Anomaly Module (v3.0 — Genre Disruption Engine)
Handles anomaly events with genre-aware disruption axes, theory-based defense, and 2-axis damage.
"""

import random
import math
from typing import Any
from orchestration_context import GameContext
import config as _cfg


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
    def _resolve_disruption_axis(bus, genre: str) -> tuple:
        """Flash의 disruption_axis를 우선 사용, 없으면 GENRE_DISRUPTION_AXIS → GENRE_PRIMARY_RESOURCE 순 fallback.
        Returns (primary_axis, secondary_axis, secondary_ratio)."""
        flash_axis = bus.anomaly.get("disruption_axis", "").lower().strip()

        genre_config = _cfg.GENRE_DISRUPTION_AXIS.get(genre, {})
        default_primary = genre_config.get("primary_axis") or _cfg.GENRE_PRIMARY_RESOURCE.get(genre, "vigor")
        secondary_ratio = genre_config.get("secondary_ratio", 0.3)

        if flash_axis == "both":
            return "vigor", "composure", 1.0  # both axes take full damage
        elif flash_axis == "vigor":
            primary = "vigor"
        elif flash_axis == "composure":
            primary = "composure"
        else:
            primary = default_primary

        secondary = "composure" if primary == "vigor" else "vigor"
        return primary, secondary, secondary_ratio

    def _roll_defense(self, context: GameContext, intensity: str, genre: str) -> dict:
        """장르 교란 방어 롤: passive + 보호 아이템 + 장르별 방어 스탯 + 이론 보정."""
        bus = context.shared_bus
        dc_map = {"Low": 30, "Mid": 50, "High": 70, "Extreme": 90}
        dc = dc_map.get(intensity, 50)
        success_rate = 100 - dc

        # Passive 보정 (theory tag modifier system)
        passives = (context.narrative_anchors or {}).get("passives", [])
        passive_defense = 0
        for passive in passives:
            mods = _cfg.get_passive_modifiers(passive)
            if not mods:
                continue
            # Genre-specific key first, then generic fallback
            genre_key = f"anomaly_defense_{genre}" if genre else ""
            if genre_key and genre_key in mods:
                passive_defense += mods[genre_key]
            elif "anomaly_defense" in mods:
                passive_defense += mods["anomaly_defense"]
        success_rate += max(-30, min(30, passive_defense))

        # 보호 아이템 보정
        if bus.anomaly.get("protective_item"):
            success_rate += 15

        # 장르별 방어 스탯 (GENRE_DISRUPTION_AXIS 우선)
        genre_config = _cfg.GENRE_DISRUPTION_AXIS.get(genre, {})
        defense_stat = genre_config.get("defense_stat") or _cfg.GENRE_PRIMARY_RESOURCE.get(genre, "vigor")
        defense_val = getattr(bus, defense_stat).get("value", 100)
        if defense_val >= 70:
            success_rate += 10
        elif defense_val <= 14:
            success_rate -= 20
        elif defense_val <= 39:
            success_rate -= 10

        # 이론 기반 방어 보정 (Flash theory_basis 매칭 시 +5)
        flash_theory = bus.anomaly.get("theory_basis", "")
        genre_theory = genre_config.get("defense_theory", "")
        if flash_theory and genre_theory:
            # Flash가 장르 이론과 일치하는 방어 이론을 제시하면 보정
            flash_theories = set(t.strip().lower() for t in flash_theory.replace("+", ",").split(",") if t.strip())
            genre_theories = set(t.strip().lower() for t in genre_theory.replace("+", ",").split(",") if t.strip())
            if flash_theories & genre_theories:
                success_rate += 5

        success_rate = max(10, min(90, success_rate))
        defense_roll = random.randint(1, 100)
        return {"success": defense_roll <= success_rate, "roll": defense_roll, "rate": success_rate}

    def _calculate_trigger_chance(self, context: GameContext, genre: str) -> float:
        """장르별 이변 트리거 확률 계산."""
        bus = context.shared_bus
        doom_val = bus.doom.get("value", 0)

        if "doom" not in context.request.active_modules:
            return 15.0

        base_chance = 5 + (doom_val * 0.7)

        # 장르별 트리거 보너스
        genre_config = _cfg.GENRE_DISRUPTION_AXIS.get(genre, {})
        trigger_bonus = genre_config.get("trigger_bonus", 0)
        base_chance += trigger_bonus

        return max(5.0, min(95.0, base_chance))

    async def process(self, context: GameContext) -> GameContext:
        bus = context.shared_bus
        genres = context.request.genres
        genre = ""
        if isinstance(genres, dict):
            genre = genres.get("stage", "")
        elif isinstance(genres, list) and genres:
            genre = genres[0] if isinstance(genres[0], str) else ""

        # 배치 모드: skip_trigger면 트리거 롤 스킵, 방어/적응만 수행
        if bus.anomaly.get("skip_trigger"):
            if not bus.anomaly.get("potential"):
                return context
            bus.anomaly["triggered"] = True
            roll = 50  # 중립 롤 (영감/쇼크 비활성)
        else:
            if not bus.anomaly.get("potential"):
                return context

            trigger_chance = self._calculate_trigger_chance(context, genre)

            roll = random.randint(1, 100)
            if roll > trigger_chance:
                return context

            bus.anomaly["triggered"] = True

        # 1. Anomaly Info
        tag = bus.anomaly.get("tag") or "기이한 현상"
        intensity = self._normalize_intensity(bus.anomaly.get("intensity", "Mid"))
        polarity = self._normalize_polarity(bus.anomaly.get("polarity"))

        # 판정 대실패 → 이변 강도 1단계 상승 (유기적 연결)
        if bus.judgment.get("active") and bus.judgment.get("result") == "critical_failure":
            escalation = {"Low": "Mid", "Mid": "High", "High": "Extreme"}
            intensity = escalation.get(intensity, intensity)
            bus.anomaly["escalated"] = True
        category = bus.anomaly.get("category") or tag
        bus.anomaly["category"] = category
        bus.anomaly["intensity"] = intensity
        bus.anomaly["polarity"] = polarity

        intensity_label = {"Low": "낮음", "Mid": "중간", "High": "높음", "Extreme": "극단"}.get(intensity, intensity)
        polarity_label = {"positive": "호재", "negative": "악재", "mixed": "혼합"}.get(polarity, polarity)

        # 2. Adaptation & Mitigation
        adaptation_data = bus.vigor.get("adaptation", {})
        tag_exposure = adaptation_data.get(category, {"count": 0})
        count = tag_exposure.get("count", 0)

        adapt_old_pct = min(100, int(math.log(count + 1) * 25))
        adapt_new_pct = min(100, int(math.log(count + 2) * 25))
        bus.anomaly["adapt_pct"] = adapt_old_pct
        bus.anomaly["adapt_new_pct"] = adapt_new_pct

        damage_map = {"Low": 5, "Mid": 10, "High": 20, "Extreme": 35}
        base_dmg = damage_map.get(intensity, 10)

        if polarity == "positive":
            base_dmg = -abs(base_dmg)
        elif polarity == "mixed":
            base_dmg = int(base_dmg * 0.5)

        # Special Outcomes (영감/쇼크)
        outcome_msg = ""
        if roll >= 90:
            outcome_msg = " [✨영감]"
            base_dmg = -10
            bus.doom["delta"] = bus.doom.get("delta", 0) - 3
        elif roll <= 10:
            outcome_msg = " [⚠️쇼크]"
            base_dmg += 15
            bus.doom["delta"] = bus.doom.get("delta", 0) + 2

        # Adaptation Mitigation (100% = 0 damage)
        mitigation = adapt_old_pct / 100.0
        final_dmg = int(base_dmg * (1.0 - mitigation)) if base_dmg > 0 else base_dmg

        # 3. Defense Roll (only if damage is positive)
        if base_dmg > 0:
            defense = self._roll_defense(context, intensity, genre)
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

        # 4. Update Bus — disruption axis routing (Flash > genre config > fallback)
        primary, secondary, sec_ratio = self._resolve_disruption_axis(bus, genre)
        primary_bus = getattr(bus, primary)
        secondary_bus = getattr(bus, secondary)
        primary_bus["delta"] = primary_bus.get("delta", 0) - final_dmg
        secondary_bus["delta"] = secondary_bus.get("delta", 0) - int(final_dmg * sec_ratio)

        # Disruption axis log
        axis_label = {"vigor": "기력", "composure": "평정"}.get(primary, primary)
        adapt_key = f" · 적응키 {category}" if category != tag else ""
        bus.anomaly["output"] = (
            f"강도 {intensity_label} · 성격 {polarity_label} · 축 {axis_label} · "
            f"적응도 {adapt_old_pct}%{adapt_key}{outcome_msg}"
        )

        # 5. Adaptation Update for Sync
        bus.vigor.setdefault("adaptation_update", {})[category] = {"count": count + 1}

        return context
