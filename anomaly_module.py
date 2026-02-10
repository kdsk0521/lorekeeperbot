"""
Lorekeeper UNE - Anomaly Module
Handles supernatural events and their mental impact.
"""

import random
import math
from typing import Any
from orchestration_context import GameContext


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

    def _roll_defense(self, context: GameContext, intensity: str) -> dict:
        """공통 방어 롤: passive 분석 + 정신 상태 + 성공률 계산 + 롤."""
        dc_map = {"Low": 30, "Mid": 50, "High": 70, "Extreme": 90}
        dc = dc_map.get(intensity, 50)
        success_rate = 100 - dc

        # Passive 보정
        passives = (context.narrative_anchors or {}).get("passives", [])
        for passive in passives:
            if isinstance(passive, dict):
                p_name = passive.get("name", "").lower()
                if any(kw in p_name for kw in ["용감", "냉정", "강인", "침착"]):
                    success_rate += 15
                elif any(kw in p_name for kw in ["겁쟁이", "나약", "불안", "공포"]):
                    success_rate -= 15

        # 보호 아이템 보정: Theoria가 인벤토리에서 관련 아이템 감지 시 +15
        if context.shared_bus.anomaly.get("protective_item"):
            success_rate += 15

        # 정신 상태 보정: 평정(+10), 동요(0), 공황(-10), 붕괴(-20)
        mental_val = context.shared_bus.mental.get("value", 100)
        if mental_val >= 70:
            success_rate += 10
        elif mental_val <= 14:
            success_rate -= 20
        elif mental_val <= 39:
            success_rate -= 10

        success_rate = max(10, min(90, success_rate))
        defense_roll = random.randint(1, 100)
        return {"success": defense_roll <= success_rate, "roll": defense_roll, "rate": success_rate}

    async def process(self, context: GameContext) -> GameContext:
        bus = context.shared_bus

        # 배치 모드: skip_trigger면 트리거 롤 스킵, 방어/적응만 수행
        if bus.anomaly.get("skip_trigger"):
            if not bus.anomaly.get("potential"):
                return context
            bus.anomaly["triggered"] = True
            roll = 50  # 중립 롤 (영감/쇼크 비활성)
        else:
            if not bus.anomaly.get("potential"):
                return context

            doom_val = bus.doom.get("value", 0)
            if "doom" in context.request.active_modules:
                trigger_chance = 5 + (doom_val * 0.7)
            else:
                trigger_chance = 15

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
        adaptation_data = bus.mental.get("adaptation", {})
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
            defense = self._roll_defense(context, intensity)
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

        # 4. Update Bus
        bus.mental["delta"] = bus.mental.get("delta", 0) - final_dmg
        adapt_key = f" · 적응키 {category}" if category != tag else ""
        bus.anomaly["output"] = (
            f"강도 {intensity_label} · 성격 {polarity_label} · "
            f"적응도 {adapt_old_pct}%{adapt_key}{outcome_msg}"
        )

        # 5. Adaptation Update for Sync
        bus.mental.setdefault("adaptation_update", {})[category] = {"count": count + 1}

        return context
