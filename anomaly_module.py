"""
Lorekeeper UNE - Anomaly Module
Handles supernatural events and their mental impact.
"""

from typing import Any
from orchestration_context import GameContext

class AnomalyModule:
    def __init__(self, client, model_id: str):
        self.client = client
        self.model_id = model_id

    async def process(self, context: GameContext) -> GameContext:
        bus = context.shared_bus
        import random
        import math

        # 배치 모드: skip_trigger가 설정되면 트리거 롤 스킵, 방어/적응만 수행
        if bus.anomaly.get("skip_trigger"):
            if not bus.anomaly.get("potential"):
                return context
            bus.anomaly["triggered"] = True
            roll = 50  # 중립 롤 (영감/쇼크 특수 효과 비활성)
        else:
            if not bus.anomaly.get("potential"):
                return context

            # [Rule] Trigger Anomaly based on Doom probability
            doom_val = bus.doom.get("value", 0)
            if "doom" in context.request.active_modules:
                trigger_chance = 25 + (doom_val / 2)
            else:
                trigger_chance = 30

            roll = random.randint(1, 100)
            if roll > trigger_chance:
                return context

            bus.anomaly["triggered"] = True

        # 1. Generate Anomaly Info
        tag = bus.anomaly.get("tag") or "기이한 현상"
        intensity = bus.anomaly.get("intensity", "Mid")
        category = bus.anomaly.get("category") or tag
        bus.anomaly["category"] = category

        def _normalize_intensity(val: Any) -> str:
            if not val:
                return "Mid"
            raw = str(val).strip().lower()
            if raw in ["low", "낮음", "하", "약", "약함"]:
                return "Low"
            if raw in ["mid", "medium", "중", "중간", "보통"]:
                return "Mid"
            if raw in ["high", "높음", "상", "강", "강함"]:
                return "High"
            if raw in ["extreme", "극", "극단", "매우 강함"]:
                return "Extreme"
            return "Mid"

        def _normalize_polarity(val: Any) -> str:
            if not val:
                return "mixed"
            raw = str(val).strip().lower()
            if raw in ["positive", "긍정", "호재", "좋음", "유리"]:
                return "positive"
            if raw in ["negative", "부정", "악재", "나쁨", "불리"]:
                return "negative"
            if raw in ["mixed", "혼합", "중립", "중립적"]:
                return "mixed"
            return "mixed"

        intensity = _normalize_intensity(intensity)
        polarity = _normalize_polarity(bus.anomaly.get("polarity"))
        bus.anomaly["intensity"] = intensity
        bus.anomaly["polarity"] = polarity

        intensity_label = {
            "Low": "낮음",
            "Mid": "중간",
            "High": "높음",
            "Extreme": "극단"
        }.get(intensity, intensity)
        polarity_label = {
            "positive": "호재",
            "negative": "악재",
            "mixed": "혼합"
        }.get(polarity, polarity)
        
        # 2. Adaptation & Mitigation
        adaptation_data = bus.mental.get("adaptation", {})
        tag_exposure = adaptation_data.get(category, {"count": 0})
        count = tag_exposure.get("count", 0)
        
        # Log-scale Adaptation: math.log(count + 1) * 25
        adapt_old_pct = min(100, int(math.log(count + 1) * 25))
        adapt_new_pct = min(100, int(math.log(count + 2) * 25))
        bus.anomaly["adapt_pct"] = adapt_old_pct
        bus.anomaly["adapt_new_pct"] = adapt_new_pct
        
        damage_map = {"Low": 5, "Mid": 10, "High": 20, "Extreme": 35}
        base_dmg = damage_map.get(intensity, 10)

        # Polarity influence (positive anomalies heal, mixed are softer)
        if polarity == "positive":
            base_dmg = -abs(base_dmg)
        elif polarity == "mixed":
            base_dmg = int(base_dmg * 0.5)
        
        # Special Outcomes based on Anomaly Trigger Roll
        outcome_msg = ""
        if roll >= 90: # Inspiration
            outcome_msg = " [✨영감]"
            base_dmg = -10 # Recovery
            bus.doom["delta"] = bus.doom.get("delta", 0) - 3
        elif roll <= 10: # Shock
            outcome_msg = " [⚠️쇼크]"
            base_dmg += 15
            bus.doom["delta"] = bus.doom.get("delta", 0) + 2

        # Damage Mitigation from Adaptation (100% Adapt = 0 Damage)
        mitigation = adapt_old_pct / 100.0
        final_dmg = int(base_dmg * (1.0 - mitigation)) if base_dmg > 0 else base_dmg
        
        # 3. Internal Defense Roll (Only if Judgment is OFF)
        if "judgment" not in context.request.active_modules and base_dmg > 0:
            # Difficulty based on anomaly intensity
            dc_map = {"Low": 30, "Mid": 50, "High": 70, "Extreme": 90}
            dc = dc_map.get(intensity, 50)
            
            # Base success rate = 100 - DC
            success_rate = 100 - dc
            
            # Analyze passives for mental-related bonuses
            passives = context.narrative_anchors.get("passives", [])
            for passive in passives:
                if isinstance(passive, dict):
                    p_name = passive.get("name", "").lower()
                    # Positive traits
                    if any(kw in p_name for kw in ["용감", "냉정", "강인", "침착"]):
                        success_rate += 15
                    # Negative traits
                    elif any(kw in p_name for kw in ["겁쟁이", "나약", "불안", "공포"]):
                        success_rate -= 15
            
            # Clamp success rate
            success_rate = max(10, min(90, success_rate))
            
            # Roll defense
            defense_roll = random.randint(1, 100)
            
            if defense_roll <= success_rate:
                # Success: Reduce damage by 50%
                final_dmg = int(final_dmg * 0.5)
                outcome_msg += " [🛡️대응 성공]"
                bus.anomaly["defense_success"] = True
                bus.anomaly["defense_note"] = "피해 감소"
            else:
                # Failure: Normal damage
                outcome_msg += " [❌대응 실패]"
                bus.anomaly["defense_success"] = False
                bus.anomaly["defense_note"] = "피해 유지"
        
        # 3-2. If Judgment is ON, provide bonus/penalty for next judgment
        elif "judgment" in context.request.active_modules and base_dmg > 0:
            # Difficulty based on anomaly intensity
            dc_map = {"Low": 30, "Mid": 50, "High": 70, "Extreme": 90}
            dc = dc_map.get(intensity, 50)
            
            # Base success rate = 100 - DC
            success_rate = 100 - dc
            
            # Analyze passives
            passives = context.narrative_anchors.get("passives", [])
            for passive in passives:
                if isinstance(passive, dict):
                    p_name = passive.get("name", "").lower()
                    if any(kw in p_name for kw in ["용감", "냉정", "강인", "침착"]):
                        success_rate += 15
                    elif any(kw in p_name for kw in ["겁쟁이", "나약", "불안", "공포"]):
                        success_rate -= 15
            
            success_rate = max(10, min(90, success_rate))
            
            # Roll defense
            defense_roll = random.randint(1, 100)
            
            if defense_roll <= success_rate:
                # Success: Provide bonus to next judgment
                bus.dai["bonus"] = bus.dai.get("bonus", 0) + 10
                bus.dai["reason"] = bus.dai.get("reason", "") + f" [이변 대응 성공 +10]"
                outcome_msg += " [🛡️대응 성공: 다음 판정 +10]"
                bus.anomaly["defense_success"] = True
                bus.anomaly["defense_note"] = "다음 판정 +10"
            else:
                # Failure: Provide penalty to next judgment
                bus.dai["penalty"] = bus.dai.get("penalty", 0) + 10
                bus.dai["reason"] = bus.dai.get("reason", "") + f" [이변 대응 실패 -10]"
                outcome_msg += " [❌대응 실패: 다음 판정 -10]"
                bus.anomaly["defense_success"] = False
                bus.anomaly["defense_note"] = "다음 판정 -10"
        
        # 4. Update Bus
        bus.mental["delta"] = bus.mental.get("delta", 0) - final_dmg
        adapt_key = f" · 적응키 {category}" if category != tag else ""
        bus.anomaly["output"] = (
            f"강도 {intensity_label} · 성격 {polarity_label} · "
            f"적응도 {adapt_old_pct}%{adapt_key}{outcome_msg}"
        )
        
        # 5. Prepare Adaptation Update for Sync
        bus.mental.setdefault("adaptation_update", {})[category] = {"count": count + 1}
        
        return context
