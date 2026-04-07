"""
Lorekeeper UNE - Emotion Engine (Phase 8: LIBRA-inspired)
Tracks NPC emotional states across turns, validates consistency,
and links emotion intensity to memory importance.

Integrates with:
  - SharedBus.dai["psyche_states"]  (input: Theoria output)
  - SharedBus.emotion               (output: this module's results)
  - domain_manager                  (persistence: per-NPC emotion state)
  - npc_autonomous.py               (downstream: emotional_contagion trigger)
  - fermentation.py                 (downstream: memory importance boost)
"""

import logging
import math
from typing import Dict, Any, List, Optional, Tuple
from dataclasses import dataclass, field, asdict

logger = logging.getLogger("EmotionEngine")


# =========================================================
# Constants
# =========================================================

# Plutchik 8 기본 감정 (로어키퍼 기존 Plutchik 모델 호환)
CORE_EMOTIONS = (
    "joy", "trust", "fear", "surprise",
    "sadness", "disgust", "anger", "anticipation",
)

# 감정 전이 감쇠율: 턴당 이전 감정이 얼마나 유지되는지
EMOTION_DECAY = 0.7  # 70% 유지, 30% 감쇠

# 급변 감지 임계값: 한 턴에 이 이상 변하면 inconsistency 경고
SPIKE_THRESHOLD = 0.5

# 감정 강도 → 메모리 중요도 부스트 매핑
IMPORTANCE_BOOST_CURVE = {
    # intensity range → boost multiplier
    0.0: 1.0,   # 무감정 → 부스트 없음
    0.3: 1.0,   # 약한 감정 → 부스트 없음
    0.5: 1.2,   # 중간 감정 → 20% 부스트
    0.7: 1.5,   # 강한 감정 → 50% 부스트
    0.9: 2.0,   # 극한 감정 → 100% 부스트
}

# Polyvagal 상태 → 감정 편향
POLYVAGAL_BIAS = {
    "ventral":      {"joy": 0.1, "trust": 0.1},
    "sympathetic":  {"fear": 0.15, "anger": 0.1},
    "dorsal":       {"sadness": 0.2, "disgust": 0.05},
}


# =========================================================
# Data Structures
# =========================================================

@dataclass
class EmotionState:
    """단일 NPC의 감정 상태 스냅샷."""
    # Plutchik 8축 감정 강도 (0.0 ~ 1.0)
    emotions: Dict[str, float] = field(default_factory=lambda: {
        e: 0.0 for e in CORE_EMOTIONS
    })
    # 지배적 감정
    dominant: str = "neutral"
    # 종합 강도 (0.0 ~ 1.0)
    intensity: float = 0.0
    # Valence-Arousal-Dominance (VAD) 좌표
    valence: float = 0.0     # -1.0(부정) ~ +1.0(긍정)
    arousal: float = 0.0     # 0.0(이완) ~ 1.0(흥분)
    dominance: float = 0.0   # -1.0(무력) ~ +1.0(지배)
    # 급변 플래그
    spike_detected: bool = False
    spike_detail: str = ""
    # 메타
    turn: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'EmotionState':
        if not data:
            return cls()
        obj = cls()
        obj.emotions = data.get("emotions", obj.emotions)
        obj.dominant = data.get("dominant", "neutral")
        obj.intensity = data.get("intensity", 0.0)
        obj.valence = data.get("valence", 0.0)
        obj.arousal = data.get("arousal", 0.0)
        obj.dominance = data.get("dominance", 0.0)
        obj.spike_detected = data.get("spike_detected", False)
        obj.spike_detail = data.get("spike_detail", "")
        obj.turn = data.get("turn", 0)
        return obj


# =========================================================
# Core Engine
# =========================================================

class EmotionEngine:
    """
    NPC 감정 상태를 추적하고 일관성을 검증하는 독립 모듈.

    호출 시점: Waterfall Stage 1 (Theoria) 완료 직후, Stage 2 (Mental Pre-pass) 전.
    입력: bus.dai["psyche_states"]
    출력: bus.emotion (새 필드)
    """

    @staticmethod
    def process_turn(
        psyche_states: Dict[str, Any],
        previous_emotions: Dict[str, Dict],
        current_turn: int,
        npc_attitudes: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, EmotionState]:
        """
        한 턴의 감정 처리 메인 루프.

        Args:
            psyche_states: Theoria가 추출한 NPC별 심리 상태
                           {npc_name: {value, active_needs, polyvagal, ...}}
            previous_emotions: 이전 턴 감정 상태 {npc_name: EmotionState.to_dict()}
            current_turn: 현재 턴 번호
            npc_attitudes: (선택) domain_manager의 NPC 태도 데이터

        Returns:
            {npc_name: EmotionState} — 이번 턴 감정 상태
        """
        results: Dict[str, EmotionState] = {}

        for npc_name, psyche in psyche_states.items():
            if not isinstance(psyche, dict):
                continue

            # 1. psyche → raw emotion 변환
            raw = EmotionEngine._psyche_to_raw_emotions(psyche)

            # 2. polyvagal 편향 적용
            polyvagal = psyche.get("polyvagal", "ventral")
            if not isinstance(polyvagal, str):
                polyvagal = "ventral"
            raw = EmotionEngine._apply_polyvagal_bias(raw, polyvagal)

            # 3. 이전 턴과 블렌딩 (시간 감쇠)
            prev_data = previous_emotions.get(npc_name, {})
            prev_state = EmotionState.from_dict(prev_data)
            has_previous = any(v > 0 for v in prev_state.emotions.values())
            if has_previous:
                blended = EmotionEngine._blend_with_previous(raw, prev_state.emotions)
            else:
                # 첫 턴이거나 이전 데이터 없으면 raw 그대로 사용
                blended = raw

            # 4. 정규화 (총합 → 1.0 이하)
            blended = EmotionEngine._normalize(blended)

            # 5. VAD 좌표 계산
            valence, arousal, dominance_val = EmotionEngine._compute_vad(blended)

            # 6. 지배적 감정 & 종합 강도
            dominant = max(blended, key=blended.get) if any(v > 0.05 for v in blended.values()) else "neutral"
            intensity = max(blended.values()) if blended else 0.0

            # 7. 급변 감지
            spike, spike_detail = EmotionEngine._detect_spike(
                blended, prev_state.emotions, npc_name
            )

            state = EmotionState(
                emotions=blended,
                dominant=dominant,
                intensity=intensity,
                valence=valence,
                arousal=arousal,
                dominance=dominance_val,
                spike_detected=spike,
                spike_detail=spike_detail,
                turn=current_turn,
            )
            results[npc_name] = state

            if spike:
                logger.warning(f"[EmotionEngine] {npc_name}: {spike_detail}")

        return results

    # ---------------------------------------------------------
    # Step 1: Psyche → Raw Emotions
    # ---------------------------------------------------------
    @staticmethod
    def _psyche_to_raw_emotions(psyche: Dict[str, Any]) -> Dict[str, float]:
        """Theoria의 psyche_state를 8축 감정 강도로 변환."""
        raw = {e: 0.0 for e in CORE_EMOTIONS}

        # psyche.value: -100 ~ +100 (부정 ~ 긍정)
        value = psyche.get("value", 0)
        if isinstance(value, (int, float)):
            norm_value = max(-100, min(100, value)) / 100.0
            if norm_value > 0:
                raw["joy"] = norm_value * 0.6
                raw["trust"] = norm_value * 0.3
                raw["anticipation"] = norm_value * 0.2
            else:
                abs_val = abs(norm_value)
                raw["sadness"] = abs_val * 0.5
                raw["fear"] = abs_val * 0.3
                raw["anger"] = abs_val * 0.2

        # active_needs: 미충족 욕구 수 → 긴장(anticipation) + 불안(fear)
        needs = psyche.get("active_needs", [])
        if isinstance(needs, list) and needs:
            need_pressure = min(len(needs) / 5.0, 1.0)
            raw["anticipation"] = max(raw["anticipation"], need_pressure * 0.5)
            raw["fear"] = max(raw["fear"], need_pressure * 0.3)

        # self_opacity: 자기 불투명도 → 놀람(surprise)
        opacity = psyche.get("self_opacity", 0)
        if isinstance(opacity, (int, float)) and opacity > 0:
            raw["surprise"] = min(opacity / 100.0, 1.0) * 0.4

        # dissociation: 해리 → 혐오(disgust) + 슬픔 강화
        dissociation = psyche.get("dissociation", "none")
        if dissociation in ("mild", "moderate", "severe"):
            severity = {"mild": 0.2, "moderate": 0.5, "severe": 0.8}[dissociation]
            raw["disgust"] = max(raw["disgust"], severity * 0.3)
            raw["sadness"] = max(raw["sadness"], severity * 0.2)

        return raw

    # ---------------------------------------------------------
    # Step 2: Polyvagal Bias
    # ---------------------------------------------------------
    @staticmethod
    def _apply_polyvagal_bias(
        raw: Dict[str, float], polyvagal: str
    ) -> Dict[str, float]:
        """Polyvagal 상태에 따른 감정 편향 적용."""
        bias = POLYVAGAL_BIAS.get(polyvagal, {})
        for emotion, boost in bias.items():
            raw[emotion] = min(1.0, raw.get(emotion, 0.0) + boost)
        return raw

    # ---------------------------------------------------------
    # Step 3: Temporal Blending (Decay)
    # ---------------------------------------------------------
    @staticmethod
    def _blend_with_previous(
        current: Dict[str, float],
        previous: Dict[str, float],
    ) -> Dict[str, float]:
        """이전 턴 감정과 현재 감정을 시간 감쇠로 블렌딩."""
        blended = {}
        for e in CORE_EMOTIONS:
            prev_val = previous.get(e, 0.0)
            curr_val = current.get(e, 0.0)
            # 이전 감정 감쇠 + 현재 감정 가중
            blended[e] = prev_val * EMOTION_DECAY + curr_val * (1 - EMOTION_DECAY)
        return blended

    # ---------------------------------------------------------
    # Step 4: Normalization
    # ---------------------------------------------------------
    @staticmethod
    def _normalize(emotions: Dict[str, float]) -> Dict[str, float]:
        """감정 값을 0.0~1.0 범위로 클램핑, 아주 작은 값은 버림."""
        out = {}
        for e in CORE_EMOTIONS:
            v = emotions.get(e, 0.0)
            v = max(0.0, min(1.0, v))
            out[e] = round(v, 3) if v >= 0.01 else 0.0
        return out

    # ---------------------------------------------------------
    # Step 5: VAD Coordinates
    # ---------------------------------------------------------
    @staticmethod
    def _compute_vad(emotions: Dict[str, float]) -> Tuple[float, float, float]:
        """8축 감정 → Valence-Arousal-Dominance 좌표 변환.
        Russell's Circumplex 기반 매핑."""
        # 각 감정의 VAD 좌표 (대략적 매핑)
        VAD_MAP = {
            "joy":          ( 0.8,  0.5,  0.6),
            "trust":        ( 0.6,  0.2,  0.3),
            "fear":         (-0.7,  0.8, -0.6),
            "surprise":     ( 0.1,  0.8,  0.0),
            "sadness":      (-0.7, -0.3, -0.5),
            "disgust":      (-0.5,  0.3,  0.2),
            "anger":        (-0.5,  0.8,  0.5),
            "anticipation": ( 0.3,  0.6,  0.3),
        }
        v, a, d = 0.0, 0.0, 0.0
        total_weight = sum(emotions.values()) or 1.0
        for e, intensity in emotions.items():
            if intensity <= 0 or e not in VAD_MAP:
                continue
            ev, ea, ed = VAD_MAP[e]
            w = intensity / total_weight
            v += ev * w
            a += ea * w
            d += ed * w
        return (
            round(max(-1.0, min(1.0, v)), 3),
            round(max(0.0, min(1.0, abs(a))), 3),
            round(max(-1.0, min(1.0, d)), 3),
        )

    # ---------------------------------------------------------
    # Step 6: Spike Detection (일관성 검증)
    # ---------------------------------------------------------
    @staticmethod
    def _detect_spike(
        current: Dict[str, float],
        previous: Dict[str, float],
        npc_name: str,
    ) -> Tuple[bool, str]:
        """한 턴 사이에 감정이 급변했는지 감지."""
        if not previous or all(v == 0 for v in previous.values()):
            return False, ""  # 이전 데이터 없으면 급변 판정 안함

        spikes = []
        for e in CORE_EMOTIONS:
            delta = abs(current.get(e, 0.0) - previous.get(e, 0.0))
            if delta >= SPIKE_THRESHOLD:
                direction = "↑" if current.get(e, 0) > previous.get(e, 0) else "↓"
                spikes.append(f"{e}{direction}({delta:.2f})")

        if spikes:
            detail = f"{npc_name} 감정 급변: {', '.join(spikes)}"
            return True, detail
        return False, ""

    # ---------------------------------------------------------
    # Utility: Memory Importance Boost
    # ---------------------------------------------------------
    @staticmethod
    def get_importance_boost(emotion_state: EmotionState) -> float:
        """감정 강도에 따른 메모리 중요도 부스트 계수 반환.
        fermentation.py에서 메모리 저장 시 호출."""
        intensity = emotion_state.intensity
        boost = 1.0
        for threshold, multiplier in sorted(IMPORTANCE_BOOST_CURVE.items()):
            if intensity >= threshold:
                boost = multiplier
        return boost

    # ---------------------------------------------------------
    # Utility: Prompt Context Builder
    # ---------------------------------------------------------
    @staticmethod
    def build_emotion_context(
        emotion_states: Dict[str, EmotionState],
        max_npcs: int = 5,
    ) -> str:
        """슬롯 주입용 감정 컨텍스트 텍스트 생성.
        slot_manager에서 호출하여 프롬프트에 삽입."""
        if not emotion_states:
            return ""

        # 강도순 정렬, 상위 N명
        sorted_npcs = sorted(
            emotion_states.items(),
            key=lambda x: x[1].intensity,
            reverse=True,
        )[:max_npcs]

        lines = ["[Emotional States]"]
        for npc_name, state in sorted_npcs:
            if state.intensity < 0.05:
                continue
            # 상위 2개 감정만 표시
            top_emotions = sorted(
                state.emotions.items(), key=lambda x: x[1], reverse=True
            )[:2]
            emo_str = ", ".join(f"{e}={v:.1f}" for e, v in top_emotions if v > 0.05)
            spike_marker = " ⚡" if state.spike_detected else ""
            lines.append(f"  {npc_name}: {state.dominant}({state.intensity:.1f}) [{emo_str}]{spike_marker}")

        return "\n".join(lines) if len(lines) > 1 else ""

    # ---------------------------------------------------------
    # Utility: Bus Output Builder
    # ---------------------------------------------------------
    @staticmethod
    def to_bus_dict(emotion_states: Dict[str, EmotionState]) -> Dict[str, Any]:
        """SharedBus.emotion 필드에 저장할 dict 생성."""
        return {
            "active": bool(emotion_states),
            "states": {
                name: state.to_dict()
                for name, state in emotion_states.items()
            },
            "summary": {
                name: {
                    "dominant": state.dominant,
                    "intensity": state.intensity,
                    "spike": state.spike_detected,
                }
                for name, state in emotion_states.items()
            },
        }
