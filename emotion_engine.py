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
import re
from collections import Counter
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

# 급변 감지 임계값: raw 도메인에서 한 턴 사이 |Δ| 이 값 이상이면 spike.
# 이론 상한은 1.0(0→1.0 완전 플립). 0.25는 강한 단일 축 반전에 반응하고
# 점진 drift는 무시하는 경험치 — Pass B에서 warm→friction 델타 ≈ 0.36이
# 정상 발동, gradual drift 델타 ≈ 0.18은 무발동. 필요 시 0.2~0.3 구간 튜닝.
SPIKE_THRESHOLD = 0.25

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

# Relational 10 팔레트 (ELEPHANT 9 − trust + shame + poise)
# Plutchik과 결합하여 base×modifier 페어를 구성. DAI relation/soma 층에서 결정적 도출 (P2).
# [2026-07-13 L1] shame 추가 — Theoria가 3경로(memory_triggers.shameful / chaemyeon /
# deep_read)에서 수치 신호를 생산하는데 착지 라벨이 없어 라벨 채널에서 전멸하던 공백 해소.
# 수치·체면은 secret_pressure 트리거·눈치 문화의 감정 엔진 (pipeline_verification_log §7.2 L1).
# [2026-07-13 poise 추가] NPC 당당함(침착한 자신감) 채널 — 평형(vigor/composure) 도메인은
# PC 전용이라 NPC의 침착·자신감은 표현 채널 0이었음(겁-질림 편향의 이면). pair v2에서
# VAD dominance 축이 삭제된 뒤 팔레트에 저각성-고지배 영역 부재 → poise가 그 자리 (§7.8).
RELATIONAL_EMOTIONS = (
    "wonder", "comfort", "play", "respect",
    "desire", "resonance", "gratitude", "friction",
    "shame", "poise",
)

# §5b _derive_relational 룰 테이블 — soma.cultural_affect → Relational 직통
# [2026-07-13 L2] 관계 자세(stance) 성격의 affect만 여기 남김.
# 내부 침전물 성격(han/hwabyung/simma)은 _psyche_to_raw_emotions의 raw 축 가산으로 이사 —
# han→friction은 응고된 슬픔·갈망을 대립으로 오역하던 의미 불일치였음 (§7.2 L2).
# chaemyeon은 respect→shame 재매핑: nunchi(장 읽기 자세)와 달리 chaemyeon의 감정 하중은
# "위협받는 체면" — respect 착지는 수치 성분을 소거했음. gi는 감정 축이 아니라 에너지
# 기술자 → 비매핑 유지 (vigor 도메인 소관).
CULTURAL_AFFECT_MAP: Dict[str, str] = {
    "jeong":     "comfort",
    "nunchi":    "respect",
    "chaemyeon": "shame",
}

# §5b memory_triggers[*].type → Relational
MEMORY_TYPE_MAP: Dict[str, str] = {
    "loving":    "gratitude",
    "traumatic": "friction",
    "nostalgic": "comfort",
    "shameful":  "shame",   # [2026-07-13 L1] 공백 해소 — Fermentation Recall "Shame→suppressed but leaks"의 착지점
}

# §6a L2 히스토리 ring buffer 파라미터
HISTORY_MAX_LEN = 5             # NPC당 최근 N턴 페어 보관
HISTORY_INTENSITY_THRESHOLD = 0.1  # 이 미만 페어는 히스토리 저장 스킵 (저강도 노이즈 필터)
SCENE_WINDOW = 3                # 씬 페어 최빈값 계산 윈도우 (턴)

# §5d 같은 의미 축 페어 금지 목록 — 매치되면 pair=None (Tier 3 폴백)
SAME_AXIS_FORBIDDEN = frozenset([
    frozenset({"anger",        "disgust"}),
    frozenset({"fear",         "sadness"}),
    frozenset({"joy",          "trust"}),
    frozenset({"anticipation", "surprise"}),
    # relational-plutchik 교차 중복
    frozenset({"friction",     "anger"}),
    frozenset({"comfort",      "trust"}),
    frozenset({"gratitude",    "joy"}),
    frozenset({"desire",       "anticipation"}),
    frozenset({"shame",        "disgust"}),   # [2026-07-13 L1] 수치=자기향 혐오 — 동축 증폭. shame×fear는 허용(노출 공포 합성)
])

# T1 축·형용사 태그 풀 (9종 고정):
#   방향성: approach/avoid | 몸상태: expansive/contracting
#   강도:  sharp-edged/soft-edged | 자세: receptive/forward-leaning (+ bounded 보조)
#
# 라벨당 1~2 태그. 의도적 중첩 3쌍(joy~wonder, comfort~gratitude, disgust~friction)은
# semantic proximity 신호로 유지. surprise/anticipation은 맥락의존 축이라 1태그.
AXIS_TAGS: Dict[str, List[str]] = {
    # Plutchik 8
    "joy":          ["approach", "expansive"],
    "trust":        ["soft-edged", "receptive"],
    "fear":         ["avoid", "contracting"],
    "surprise":     ["sharp-edged"],
    "sadness":      ["avoid", "contracting"],
    "disgust":      ["avoid", "sharp-edged"],
    "anger":        ["approach", "sharp-edged"],
    "anticipation": ["forward-leaning"],
    # Relational 9
    "wonder":       ["approach", "expansive"],
    "comfort":      ["soft-edged", "receptive"],
    "play":         ["expansive", "forward-leaning"],
    "respect":      ["receptive", "bounded"],
    "desire":       ["forward-leaning", "approach"],
    "resonance":    ["expansive", "soft-edged"],
    "gratitude":    ["soft-edged", "receptive"],
    "friction":     ["avoid", "sharp-edged"],
    "shame":        ["contracting", "bounded"],   # 시선 아래 움츠림 — fear/sadness(avoid+contracting)와 구분되는 사회적 구속 성분
    "poise":        ["expansive", "bounded"],     # 펼쳐진 몸 + 자기 소유 — 당당함. wonder(approach+expansive)와 달리 대상 없이 서 있음
}


# =========================================================
# Data Structures
# =========================================================

@dataclass
class EmotionState:
    """단일 NPC의 감정 상태 스냅샷.

    스키마 v2 (pair 구조):
      - Plutchik 8축 분포는 유지 (기존 로직 호환)
      - dominant 단일 라벨 → base×modifier 페어로 대체
      - VAD 필드(valence/arousal/dominance) 전면 제거
      - scene_pair 필드 추가 (L2 히스토리 파생, P3에서 값 채워짐)
    """
    # Plutchik 8축 감정 강도 (0.0 ~ 1.0) — blended/decay 적용된 최종치
    emotions: Dict[str, float] = field(default_factory=lambda: {
        e: 0.0 for e in CORE_EMOTIONS
    })
    # blending 이전 raw 신호 — 다음 턴 spike 검출의 비교 기준.
    # _blend_with_previous 돌기 직전 (polyvagal까지 적용된) 상태를 저장.
    raw_emotions: Dict[str, float] = field(default_factory=lambda: {
        e: 0.0 for e in CORE_EMOTIONS
    })
    # 종합 강도 (0.0 ~ 1.0)
    intensity: float = 0.0
    # 급변 플래그 — raw 도메인에서 |Δ| ≥ SPIKE_THRESHOLD 인 축이 하나라도 있으면 True
    spike_detected: bool = False
    spike_detail: str = ""

    # --- 턴 페어 (P1: base만 채움, P2에서 modifier + source + confidence 결정) ---
    base_label: str = ""            # Plutchik 또는 Relational 중 1개
    modifier_label: str = ""        # base와 다른 축
    base_source: str = ""           # "plutchik" | "relational"
    mod_source: str = ""            # "plutchik" | "relational"
    pair_confidence: float = 0.0    # 0.0~1.0, 룰 매치 강도 (디버깅 로그용)

    # --- 씬 페어 (P3: 아래 history ring buffer에서 파생) ---
    scene_base: str = ""
    scene_mod: str = ""

    # --- L2 히스토리 ring buffer (최근 HISTORY_MAX_LEN 턴의 유효 페어) ---
    # 각 엔트리: [base, mod, intensity, turn] — JSON 직렬화 호환을 위해 list-of-list
    # _append_history가 intensity < HISTORY_INTENSITY_THRESHOLD 엔트리 거름
    history: List[List[Any]] = field(default_factory=list)

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
        # raw_emotions는 P4.6에서 추가. 레거시 persistent state에는 없을 수 있어
        # 기본값(전부 0.0)으로 폴백. 첫 턴 spike 검출이 자동으로 비활성 (prev 0 → skip)
        obj.raw_emotions = data.get("raw_emotions", obj.raw_emotions)
        obj.intensity = data.get("intensity", 0.0)
        obj.spike_detected = data.get("spike_detected", False)
        obj.spike_detail = data.get("spike_detail", "")
        obj.base_label = data.get("base_label", "")
        obj.modifier_label = data.get("modifier_label", "")
        obj.base_source = data.get("base_source", "")
        obj.mod_source = data.get("mod_source", "")
        obj.pair_confidence = data.get("pair_confidence", 0.0)
        obj.scene_base = data.get("scene_base", "")
        obj.scene_mod = data.get("scene_mod", "")
        # L2 히스토리 복원. list of list/tuple 모두 수용, 비정상 엔트리는 스킵.
        # Bug 4 fix (2026-05-20): 이전 코드 `str(e[0])`은 None을 "None" (truthy 4글자)
        # 문자열로 둔갑시켜 _append_history 가드와 _derive_scene_pair Counter를 오염시킴.
        # 추가로 비문자열 타입(int 등)은 str() 코어스되어 가짜 라벨로 통과하던 문제도 차단.
        # 두 층 방어: (1) isinstance(str) 가드 → 빈 문자열 강등, (2) 빈 페어 엔트리 명시 스킵.
        raw_hist = data.get("history") or []
        if isinstance(raw_hist, list):
            clean = []
            for e in raw_hist:
                if isinstance(e, (list, tuple)) and len(e) >= 4:
                    # base/mod: 문자열일 때만 인정, 아니면 "" → 다음 단계에서 스킵 대상
                    base = e[0] if isinstance(e[0], str) else ""
                    mod = e[1] if isinstance(e[1], str) else ""
                    if not base or not mod:
                        continue  # 비정상 또는 빈 페어 — _append_history 자체 가드와 일치
                    try:
                        clean.append([base, mod, float(e[2]), int(e[3])])
                    except (TypeError, ValueError):
                        continue
            obj.history = clean[-HISTORY_MAX_LEN:]
        obj.turn = data.get("turn", 0)
        # 레거시 dominant 필드 마이그레이션 (persistent state에서 올라올 수 있음)
        legacy_dominant = data.get("dominant")
        if legacy_dominant and legacy_dominant != "neutral" and not obj.base_label:
            obj.base_label = legacy_dominant
            obj.base_source = "plutchik"
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
        scene_ctx: Optional[Dict[str, Any]] = None,
        memory_triggers: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, EmotionState]:
        """
        한 턴의 감정 처리 메인 루프.

        Args:
            psyche_states: Theoria가 추출한 NPC별 심리 상태 (nested 스키마)
                           {npc_name: {psyche:{...}, soma:{...}, relation:{...},
                                       deep_read:str, resurfacing:str|null}}
            previous_emotions: 이전 턴 감정 상태 {npc_name: EmotionState.to_dict()}
            current_turn: 현재 턴 번호
            npc_attitudes: (선택) domain_manager의 NPC 태도 데이터
            scene_ctx: (선택) {register, silence_type, scene_type} — §5b Tier 6~7 + 친밀 재서열 입력
            memory_triggers: (선택) DAI.memory_triggers 전체 리스트 — §5b Tier 4 입력

        Returns:
            {npc_name: EmotionState} — 이번 턴 감정 상태 (pair 구조)
        """
        results: Dict[str, EmotionState] = {}
        scene_ctx = scene_ctx or {}
        memory_triggers = memory_triggers or []

        for npc_name, npc_dai in psyche_states.items():
            if not isinstance(npc_dai, dict):
                continue

            # 0. DAI nested 레이어 추출
            psyche_layer = npc_dai.get("psyche") or {}
            soma_layer = npc_dai.get("soma") or {}
            relation_layer = npc_dai.get("relation") or {}
            if not isinstance(psyche_layer, dict):
                psyche_layer = {}
            if not isinstance(soma_layer, dict):
                soma_layer = {}
            if not isinstance(relation_layer, dict):
                relation_layer = {}
            deep_read = npc_dai.get("deep_read", "") or ""
            if not isinstance(deep_read, str):
                deep_read = ""

            # 1. psyche+soma → raw emotion 변환
            raw = EmotionEngine._psyche_to_raw_emotions(psyche_layer, soma_layer)

            # 2. polyvagal 편향 적용 (soma 레이어에서 읽음)
            polyvagal = soma_layer.get("polyvagal", "ventral")
            if not isinstance(polyvagal, str):
                polyvagal = "ventral"
            raw = EmotionEngine._apply_polyvagal_bias(raw, polyvagal)

            # raw 스냅샷 — 블렌딩 전 상태를 보관해 다음 턴 spike 검출의 비교 기준으로 사용.
            # 이유: blended 도메인은 EMOTION_DECAY=0.7 때문에 단일 턴 최대 델타 ≤ 0.3로 압축돼
            # SPIKE_THRESHOLD 0.5가 수학적으로 도달 불가능했음. raw는 입력 그 자체라
            # "실제 장면 입력이 얼마나 급변했나"를 직접 측정.
            raw_snapshot = dict(raw)

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

            # 5. 종합 강도 (VAD 계산 제거 — pair 구조로 전환)
            intensity = max(blended.values()) if blended else 0.0

            # 6. Plutchik top-1 추출 (후속 assign_base_modifier에서 재배치)
            if any(v > 0.05 for v in blended.values()):
                plutchik_top = max(blended, key=blended.get)
            else:
                plutchik_top = ""

            # 7. 급변 감지 — raw 도메인에서 비교
            spike, spike_detail = EmotionEngine._detect_spike(
                raw_snapshot, prev_state.raw_emotions, npc_name
            )

            # 8. Relational 라벨 결정적 도출 (§5b 9-tier)
            relational_label, pair_confidence = EmotionEngine._derive_relational(
                psyche=psyche_layer,
                relation=relation_layer,
                soma=soma_layer,
                scene_ctx=scene_ctx,
                memory_triggers=memory_triggers,
                npc_name=npc_name,
                deep_read=deep_read,
            )

            # 9. base/modifier 역할 지정 (§5c 역전 규칙)
            base_label, modifier_label, base_source, mod_source = \
                EmotionEngine._assign_base_modifier(
                    plutchik_top=plutchik_top,
                    relational=relational_label,
                    psyche=psyche_layer,
                    relation=relation_layer,
                    soma=soma_layer,
                )

            # 10. 같은 의미 축 페어 금지 검증 (§5d)
            # 같은 축 두 신호가 일치하면 modifier만 제거하고 base는 solo로 유지.
            # 두 신호가 같은 방향을 가리켰다는 건 "증폭된 순수 상태"라는 뜻이므로
            # intensity가 이미 amplification을 반영. 전체 silence 금지.
            if base_label and modifier_label and \
               EmotionEngine._is_same_axis(base_label, modifier_label):
                logger.debug(
                    f"[EmotionEngine] {npc_name}: same-axis "
                    f"({base_label}×{modifier_label}) → solo base={base_label}"
                )
                modifier_label = ""
                mod_source = ""
                pair_confidence = round(pair_confidence * 0.5, 3)  # 반감 (디버깅 신호)

            # 11. L2 히스토리 갱신 (이전 히스토리 복사 후 이번 턴 페어 append)
            new_history = EmotionEngine._append_history(
                list(prev_state.history),
                base_label, modifier_label, intensity, current_turn,
            )

            # 12. 씬 페어 파생 (최근 SCENE_WINDOW 턴의 intensity 가중 최빈 페어)
            scene_base, scene_mod = EmotionEngine._derive_scene_pair(
                new_history, current_turn, window=SCENE_WINDOW,
            )
            # Bug 1 fix (2026-05-20): 파생 실패 시 자연 소멸.
            # 이전 코드는 무조건 prev_state.scene_base를 폴백으로 복사했는데,
            # _append_history가 solo plutchik / 저강도 턴을 스킵하기 때문에 history.latest가
            # SCENE_WINDOW 밖으로 밀려나는 케이스가 발생. 이때 폴백을 적용하면 과거 강했던
            # 페어가 영구 박제되어:
            #   - [Scene Drift] 슬롯 영구 출력
            #   - story_director drift bonus (+0.3) 영구 점화
            #   - npc_emotion_states 영속 데이터에도 stale scene_pair 박힘
            # _derive_scene_pair는 SCENE_WINDOW 안 엔트리가 있으면 정상 도출하므로,
            # 여기 도달했다는 건 "history가 비었거나 stale" 케이스 = 자연 소멸이 정답.
            if not scene_base:
                scene_base = ""
                scene_mod = ""

            state = EmotionState(
                emotions=blended,
                raw_emotions=raw_snapshot,
                intensity=intensity,
                spike_detected=spike,
                spike_detail=spike_detail,
                base_label=base_label,
                modifier_label=modifier_label,
                base_source=base_source,
                mod_source=mod_source,
                pair_confidence=round(pair_confidence, 3),
                scene_base=scene_base,
                scene_mod=scene_mod,
                history=new_history,
                turn=current_turn,
            )
            results[npc_name] = state

            if spike:
                logger.warning(f"[EmotionEngine] {npc_name}: {spike_detail}")

        return results

    # ---------------------------------------------------------
    # Step 1: Psyche → Raw Emotions  (nested DAI layer aware)
    # ---------------------------------------------------------
    @staticmethod
    def _psyche_to_raw_emotions(
        psyche: Dict[str, Any],
        soma: Dict[str, Any],
    ) -> Dict[str, float]:
        """Theoria의 psyche_states[npc] 내부 nested 레이어를 8축 감정 강도로 변환.

        Theoria 실제 스키마 (theoria_analyzer.py:266-298):
          psyche_states[npc] = {
            psyche: {value, primary_emotion, active_needs, self_opacity, decision_mode, ...},
            soma:   {polyvagal, cultural_affect, dissociation, ...},
            relation:{value, attachment, phase, ...},
            deep_read: str,
            resurfacing: str | null
          }
        P2 이전 코드는 상위 dict에서 value/polyvagal/dissociation을 직접 찾아 항상 기본값 반환하는
        잠재 버그가 있었음. 본 함수는 psyche/soma 두 레이어를 명시적으로 받는다.
        """
        raw = {e: 0.0 for e in CORE_EMOTIONS}
        if not isinstance(psyche, dict):
            psyche = {}
        if not isinstance(soma, dict):
            soma = {}

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

        # active_needs: 미충족 욕구 수 → 추동(anticipation)만.
        # [2026-07-13 base 탈편향] fear 커플링 제거 — active_needs는 Theoria가 매턴
        # 1~2개 *필수* 충전하는 필드(스키마 "Identify 1-2 needs driving behavior")라
        # "욕구 존재=불안" 번역이 전 NPC 상시 fear 플로어로 작동했음(겁-질림 끌림 주입기 #1).
        # 욕구는 drive지 위협이 아님 — 야망 있는 NPC의 ego-need가 fear로 읽히면 당당함이 죽는다.
        # fear는 위협 신호(value 음수 / polyvagal sympathetic / dissociation) 소관으로 환원.
        needs = psyche.get("active_needs", [])
        if isinstance(needs, list) and needs:
            need_pressure = min(len(needs) / 5.0, 1.0)
            raw["anticipation"] = max(raw["anticipation"], need_pressure * 0.5)

        # self_opacity: Theoria 스키마상 문자열("claims X — actual: Y") 또는 null.
        #   - 존재(비공백 문자열) → surprise 약가산
        #   - 과거 숫자 호환: > 0이면 정규화
        # [2026-07-13 base 탈편향] 0.4 → 0.15 — 주석("약가산")과 실값(0.4)의 불일치 교정.
        # self_opacity는 분석이 충실할수록 채워지는 해석 필드("자기이해 부정확")지 "동요"가 아닌데,
        # 0.4 플로어는 (a) 중립 장면(value≈0)에서 무조건 surprise가 top이 되는 어트랙터
        # (사용자 관측 "friction×surprise" 쏠림의 base 측), (b) opacity null↔str 깜빡임이
        # delta 0.4 ≥ SPIKE_THRESHOLD 0.25로 가짜 spike 점화원이었음. 0.15는 델타가
        # 임계 미달이라 spike 원천 차단 + "존재=약신호" 의미 복원.
        opacity = psyche.get("self_opacity")
        if isinstance(opacity, str) and opacity.strip():
            raw["surprise"] = max(raw["surprise"], 0.15)
        elif isinstance(opacity, (int, float)) and opacity > 0:
            raw["surprise"] = max(raw["surprise"], min(opacity / 100.0, 1.0) * 0.15)

        # soma.dissociation: 해리 → 혐오(disgust) + 슬픔 강화
        dissociation = soma.get("dissociation", "none")
        if dissociation in ("mild", "moderate", "severe"):
            severity = {"mild": 0.2, "moderate": 0.5, "severe": 0.8}[dissociation]
            raw["disgust"] = max(raw["disgust"], severity * 0.3)
            raw["sadness"] = max(raw["sadness"], severity * 0.2)

        # [2026-07-13 L2] cultural_affect 내부 침전물 3종 → raw 축 가산.
        # 관계 자세가 아니라 내부 상태인 affect는 relational 라벨(Tier 3 직통)이 아니라
        # raw 분포를 물들인다. 정의: analysis_resources.py:166-175.
        #   han: 응고된 슬픔 + 아직 뻗는 갈망 → sadness 주 + anticipation 소
        #        (구 han→friction 직통은 갈망을 대립으로 오역 — 이사)
        #   hwabyung: 신체화된 분노, 폭발 위험 → anger 주 + 억압 긴장 fear 소
        #   simma: 자기파괴 내면 목소리 → disgust(자기향) 주 + fear 소
        # 가산식(+clamp)은 primary_emotion 훅과 동일 스타일 — value 스칼라 경로 위에 얹힘.
        affect = soma.get("cultural_affect")
        if affect == "han":
            raw["sadness"] = min(1.0, raw["sadness"] + 0.3)
            raw["anticipation"] = min(1.0, raw["anticipation"] + 0.1)
        elif affect == "hwabyung":
            raw["anger"] = min(1.0, raw["anger"] + 0.3)
            raw["fear"] = min(1.0, raw["fear"] + 0.1)
        elif affect == "simma":
            raw["disgust"] = min(1.0, raw["disgust"] + 0.25)
            raw["fear"] = min(1.0, raw["fear"] + 0.15)

        # primary_emotion 훅: Flash가 명시한 감정 라벨을 raw에 직접 반영.
        # value-scalar 역추정만으론 anger처럼 구조적으로 약해지는 감정이
        # 절대 top이 될 수 없는 문제를 보정한다. 다른 경로 수학은 건드리지 않음.
        # Plutchik 8축 멤버인 경우에만 +0.10 가산 (Relational 라벨은 _derive_relational이 처리).
        primary = psyche.get("primary_emotion")
        if isinstance(primary, str) and primary in CORE_EMOTIONS:
            raw[primary] = min(1.0, raw[primary] + 0.10)

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
    # Step 5: Spike Detection (일관성 검증)
    # ---------------------------------------------------------
    @staticmethod
    def _detect_spike(
        current: Dict[str, float],
        previous: Dict[str, float],
        npc_name: str,
    ) -> Tuple[bool, str]:
        """한 턴 사이에 감정이 급변했는지 감지.

        입력은 **raw 도메인** (blended/decay 이전). blended에서 재면
        EMOTION_DECAY=0.7로 단일 턴 델타가 ≤0.3로 눌려 SPIKE_THRESHOLD가
        사실상 도달 불가. raw는 입력 자체의 플립 폭을 반영.
        """
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
    # Step 8: Derive Relational (§5b — 9-tier deterministic lookup)
    # ---------------------------------------------------------
    @staticmethod
    def _derive_relational(
        psyche: Dict[str, Any],
        relation: Dict[str, Any],
        soma: Dict[str, Any],
        scene_ctx: Dict[str, Any],
        memory_triggers: List[Dict[str, Any]],
        npc_name: str,
        deep_read: str = "",
    ) -> Tuple[str, float]:
        """DAI 재료에서 Relational 10 중 1개를 결정적으로 도출.

        상위 우선순위 매치 우선. pair_confidence는 tier 번호의 역수 스케일
        (Tier 1=1.0, Tier 8=0.3, Tier 9=0.0).
        예외: scene_type=intimate이면 Tier 5→3(관계-긍정)이 Tier 1/2(갈등)보다 선발화.

        Returns:
            (relational_label, confidence). 매치 없으면 ("", 0.0).
        """
        psyche = psyche if isinstance(psyche, dict) else {}
        relation = relation if isinstance(relation, dict) else {}
        soma = soma if isinstance(soma, dict) else {}
        scene_ctx = scene_ctx if isinstance(scene_ctx, dict) else {}
        memory_triggers = memory_triggers if isinstance(memory_triggers, list) else []

        # Tier 3/5 판정 헬퍼 — 친밀 장면 재서열에서 재사용 (로직 단일 원천)
        def _tier3_culture():
            ca = soma.get("cultural_affect")
            if ca in CULTURAL_AFFECT_MAP:
                return (CULTURAL_AFFECT_MAP[ca], 0.8)
            return None

        def _tier5_attachment():
            att = relation.get("attachment")
            phase = relation.get("phase")
            try:
                rel_val = float(relation.get("value", 0) or 0)
            except (TypeError, ValueError):
                rel_val = 0.0
            if att == "anxious" and phase == "orientation":
                return ("desire", 0.6)
            if att == "secure" and rel_val > 60:
                return ("gratitude", 0.6)
            if phase == "exploitation" and rel_val > 50:
                return ("desire", 0.6)
            return None

        # [2026-07-13 친밀 장면 재서열] SceneType=intimate에서는 관계-긍정 신호(Tier 5→3)가
        # 갈등 신호(Tier 1/2)보다 먼저 발화권을 가진다.
        # 근거: 친밀 장면은 분석 교리(want vs fear 양극 유지, WRITING_DIRECTIVES)상
        # value_conflict가 거의 항상 충전되는 곳이라 Tier 1 friction(1.0)이
        # desire/gratitude/comfort를 영구 선점 — 라이브 관측 "친밀 장면 friction×surprise
        # 쏠림"의 modifier 측. 미발화 시 기존 캐스케이드 그대로 = 진짜 갈등은 여전히 friction.
        if scene_ctx.get("scene_type") == "intimate":
            _hit = _tier5_attachment() or _tier3_culture()
            if _hit:
                return _hit

        # Tier 1: value_conflict 존재 (관계 내부 구조적 모순)
        vc = relation.get("value_conflict")
        if isinstance(vc, str) and vc.strip():
            return ("friction", 1.0)

        # Tier 2: negotiation_stance
        ns = relation.get("negotiation_stance")
        if ns == "competitive" or ns == "exploitative":
            return ("friction", 0.9)
        if ns == "cooperative":
            return ("resonance", 0.9)

        # Tier 3: soma.cultural_affect 직통 매핑
        _t3 = _tier3_culture()
        if _t3:
            return _t3

        # Tier 4: memory_triggers (이 NPC 관련 또는 전역)
        for mt in memory_triggers:
            if not isinstance(mt, dict):
                continue
            mt_char = mt.get("character")
            mt_type = mt.get("type")
            # 해당 NPC 매치 또는 character 미지정 (전역 트리거)
            if mt_char and mt_char != npc_name:
                continue
            if mt_type in MEMORY_TYPE_MAP:
                return (MEMORY_TYPE_MAP[mt_type], 0.7)

        # Tier 5: attachment / phase / value 조합
        _t5 = _tier5_attachment()
        if _t5:
            return _t5

        # Tier 5b [2026-07-13 poise]: 침착한 자신감 — NPC 당당함 채널.
        # System 2 사고(deliberate) + 생리적 안전(ventral, 해리 없음) + 비-부정 가치
        # = "동요 없음"의 능동태. Tier 1(value_conflict) 하위 배치라 내적 모순이 있는
        # NPC는 애초에 도달 불가(당당함과 내적 갈등의 공존은 pair가 아니라 산문 소관).
        # 친밀 pre-pass에는 미포함 — 침착한 표면 아래 갈등을 가리지 않기 위함.
        if psyche.get("decision_mode") == "deliberate" \
                and soma.get("polyvagal") == "ventral" \
                and soma.get("dissociation", "none") not in ("mild", "moderate", "severe"):
            try:
                _pv_val = float(psyche.get("value", 0) or 0)
            except (TypeError, ValueError):
                _pv_val = 0.0
            if _pv_val >= 0:
                return ("poise", 0.55)

        # Tier 6: stage + phase 구조 / scene.register 장면 배경
        stage = relation.get("stage")
        if stage == "front" and relation.get("phase") == "orientation":
            return ("respect", 0.5)
        register = scene_ctx.get("register")
        if register == "mirror":
            return ("resonance", 0.5)
        if register == "law":
            return ("respect", 0.5)
        if register == "remainder":
            return ("friction", 0.5)

        # Tier 7: silence_type
        silence = scene_ctx.get("silence_type")
        if silence == "reflective":
            return ("comfort", 0.4)
        if silence in ("heavy", "tense"):
            return ("friction", 0.4)

        # Tier 8: deep_read Core 약신호 (Korean keyword simple match)
        # [2026-07-13 L1] shame 키워드 선행 — wonder보다 구체적·희소 신호라 우선 매치
        if deep_read and any(k in deep_read for k in ("수치", "부끄", "창피", "굴욕")):
            return ("shame", 0.3)
        if deep_read and ("경탄" in deep_read or "호기심" in deep_read):
            return ("wonder", 0.3)

        # Tier 9: 전부 미스 → Tier 3 폴백 (pair=None in assign_base_modifier)
        return ("", 0.0)

    # ---------------------------------------------------------
    # Step 9: Assign Base/Modifier (§5c — role reversal rules)
    # ---------------------------------------------------------
    @staticmethod
    def _assign_base_modifier(
        plutchik_top: str,
        relational: str,
        psyche: Dict[str, Any],
        relation: Dict[str, Any],
        soma: Dict[str, Any],
    ) -> Tuple[str, str, str, str]:
        """base/modifier 역할을 결정 (기본: Plutchik=base, Relational=mod).

        역전 조건 (순서대로 평가, 한 건만 맞으면 역전):
          R1. decision_mode="deliberate" AND |relation.value| > |psyche.value|
              → 사회적 연산 우세 + 관계 축이 더 무거움 → Relational=base
          R2. relation.stage="front" AND phase="orientation"
              → 포지션이 본체, 감정은 표면 파동 → Relational=base
          R3. soma.polyvagal="dorsal" AND dissociation in (moderate, severe)
              → 몸이 무너져 관계 레이어가 유지 → Relational=base

        Returns:
            (base_label, modifier_label, base_source, mod_source)
            한쪽만 있으면 mod는 "", 둘 다 없으면 모두 "".
        """
        psyche = psyche if isinstance(psyche, dict) else {}
        relation = relation if isinstance(relation, dict) else {}
        soma = soma if isinstance(soma, dict) else {}

        plutchik_top = plutchik_top or ""
        relational = relational or ""

        # 둘 다 없으면 silent
        if not plutchik_top and not relational:
            return ("", "", "", "")
        # Plutchik만 존재 → 단일 라벨
        if plutchik_top and not relational:
            return (plutchik_top, "", "plutchik", "")
        # Relational만 존재 → 단일 라벨 (Tier 6/7 신호만 있고 Plutchik top 미약한 케이스)
        if relational and not plutchik_top:
            return (relational, "", "relational", "")

        # 기본: Plutchik=base, Relational=mod
        default = (plutchik_top, relational, "plutchik", "relational")
        reversed_ = (relational, plutchik_top, "relational", "plutchik")

        # R1. deliberate + relation 축 우세
        dm = psyche.get("decision_mode")
        try:
            pv = abs(float(psyche.get("value", 0) or 0))
        except (TypeError, ValueError):
            pv = 0.0
        try:
            rv = abs(float(relation.get("value", 0) or 0))
        except (TypeError, ValueError):
            rv = 0.0
        if dm == "deliberate" and rv > pv:
            return reversed_

        # R2. 전경 무대 + orientation phase
        if relation.get("stage") == "front" and relation.get("phase") == "orientation":
            return reversed_

        # R3. dorsal + 중증 해리
        if soma.get("polyvagal") == "dorsal" and soma.get("dissociation") in ("moderate", "severe"):
            return reversed_

        # R4. resurfacing + traumatic memory → default 유지 (문서상 확인용, 코드 동작 없음)
        return default

    # ---------------------------------------------------------
    # Step 10: Same-Axis Guard (§5d — cross-axis forbid list)
    # ---------------------------------------------------------
    @staticmethod
    def _is_same_axis(a: str, b: str) -> bool:
        """두 라벨이 같은 의미 축인지 판정.

        - 동일 라벨은 당연히 같은 축
        - 빈 라벨은 축 판정 불가 → False (caller가 빈 측을 별도 처리)
        - SAME_AXIS_FORBIDDEN 명시 페어는 True
        """
        if not a or not b:
            return False
        if a == b:
            return True
        return frozenset({a, b}) in SAME_AXIS_FORBIDDEN

    # ---------------------------------------------------------
    # Step 11: Append to L2 History Ring Buffer (§6a)
    # ---------------------------------------------------------
    @staticmethod
    def _append_history(
        history: List[List[Any]],
        base: str,
        mod: str,
        intensity: float,
        turn: int,
        max_len: int = HISTORY_MAX_LEN,
    ) -> List[List[Any]]:
        """히스토리 ring buffer에 이번 턴 페어 append.

        - base 또는 mod가 비어 있으면 스킵 (단일 라벨 턴·Tier 3 폴백 턴은 씬 파생에서 제외)
        - intensity < HISTORY_INTENSITY_THRESHOLD면 스킵 (저강도 노이즈 최빈값 왜곡 방지)
        - 초과분은 앞에서부터 버림
        """
        if not base or not mod:
            return history[-max_len:] if len(history) > max_len else history
        try:
            intensity = float(intensity)
        except (TypeError, ValueError):
            intensity = 0.0
        if intensity < HISTORY_INTENSITY_THRESHOLD:
            return history[-max_len:] if len(history) > max_len else history
        history.append([base, mod, intensity, int(turn)])
        return history[-max_len:]

    # ---------------------------------------------------------
    # Step 12: Derive Scene Pair (§6a)
    # ---------------------------------------------------------
    @staticmethod
    def _derive_scene_pair(
        history: List[List[Any]],
        current_turn: int,
        window: int = SCENE_WINDOW,
    ) -> Tuple[str, str]:
        """최근 window 턴의 intensity 가중 최빈 페어.

        - 씬 경계 감지 불필요: 이동 평균이 자연스럽게 장면 전환 적응
        - recent 엔트리 없으면 ("", "") 반환 → caller가 prev 유지 결정
        - 동률 시 Counter.most_common(1) 자체가 결정적 (최초 발견 순) → 안정
        """
        if not history:
            return ("", "")
        recent = [h for h in history if current_turn - int(h[3]) <= window]
        if not recent:
            return ("", "")
        weighted: Counter = Counter()
        for entry in recent:
            base, mod, intensity = entry[0], entry[1], entry[2]
            if base and mod:
                weighted[(base, mod)] += float(intensity)
        if not weighted:
            return ("", "")
        pair, _ = weighted.most_common(1)[0]
        return (pair[0], pair[1])

    # ---------------------------------------------------------
    # Utility: Memory Importance Boost
    # ---------------------------------------------------------
    @staticmethod
    def get_importance_boost(intensity: float) -> float:
        """감정 강도에 따른 메모리 중요도 부스트 계수 반환.
        fermentation.py에서 메모리 저장 시 호출.

        v2 (2026-05-20): intensity (float) 직접 받음. 이전 시그니처는 EmotionState
        인스턴스를 요구했지만 실제로 .intensity 필드만 봤고, fermentation 호출자가
        더미 인스턴스를 만들어 넘기는 패턴이 있었음. float로 단순화해 ceremony 제거.
        """
        try:
            intensity = float(intensity)
        except (TypeError, ValueError):
            return 1.0
        boost = 1.0
        for threshold, multiplier in sorted(IMPORTANCE_BOOST_CURVE.items()):
            if intensity >= threshold:
                boost = multiplier
        return boost

    # ---------------------------------------------------------
    # Utility: Pair Hint Composer (T1 축 태그 합성)
    # ---------------------------------------------------------
    @staticmethod
    def _compose_pair_hint(base: str, mod: str) -> str:
        """base × modifier → 축 태그 합성 힌트.

        - AXIS_TAGS 룩업: 라벨당 1~2 태그
        - 조합표 아님. 태그 리스트 merge만 수행
        - 빈 mod 허용: base만 있는 경우 (P1 과도기 또는 Tier 3 pair=None)
        - 태그 전혀 없는 라벨: 라벨만 출력
        """
        bt = [t for t in AXIS_TAGS.get(base, []) if t]
        if not mod:
            if not bt:
                return base
            return f"{base} — {'·'.join(bt[:3])}"
        mt = [t for t in AXIS_TAGS.get(mod, []) if t]
        merged = bt + [t for t in mt if t not in bt]
        if not merged:
            return f"{base} × {mod}"
        # [2026-07-14 위생] 렌더-facing 엠대쉬 → 콜론 (미러링 실증에 따른 채널 전체 통일)
        return f"{base} × {mod}: {'·'.join(merged[:3])}"

    # ---------------------------------------------------------
    # Utility: Prompt Context Builder (T0 + T1)
    # ---------------------------------------------------------
    @staticmethod
    def build_emotion_context(
        emotion_states: Dict[str, EmotionState],
        max_npcs: int = 5,
    ) -> str:
        """슬롯 주입용 감정 컨텍스트 텍스트 생성.

        출력 구조:
          [Emotional States]
            {NPC}: {base × mod — tag·tag·tag} ({intensity:.1f}){spike_marker}
          [Scene Drift]                               # turn_pair ≠ scene_pair 시
            {NPC}: turn vs scene «{scene_hint}»

        slot_manager.py:886에서 호출되어 Slot 16 scene_intelligence에 주입.
        emotion_engine이 감정 도메인 iceberg로서 자기완결 (iceberg.py 경유 없음).
        """
        if not emotion_states:
            return ""

        # 강도순 정렬, 상위 N명
        sorted_npcs = sorted(
            emotion_states.items(),
            key=lambda x: x[1].intensity,
            reverse=True,
        )[:max_npcs]

        # [2026-07-13 gloss] 자매 블록(iceberg 번역: energy/Slot29 trend)은 전부
        # "데이터 + 소비 지시 1줄" 문법인데 이 블록만 무주석 노테이션이었음 —
        # 렌더러가 "이건 정보다"를 알아먹는가 문제(§7.10). iceberg :1221 문법 준용.
        # [2026-07-14 위생] 렌더-facing 문자열의 엠대쉬 제거(미러링 실증) — 주석은 무관.
        lines = ["[Emotional States] state data, not prose: it lives in the body; "
                 "show through gesture and behavior, never name these labels."]
        drift_lines: List[str] = []
        for npc_name, state in sorted_npcs:
            if state.intensity < 0.05 or not state.base_label:
                continue
            hint = EmotionEngine._compose_pair_hint(
                state.base_label, state.modifier_label
            )
            # ⚡ marker + spike axes (DC-01 배선): spike_detail 문자열에서
            # "{npc} 감정 급변: " 접두 제거 후 축 목록을 직접 노출.
            # 모델이 "spike 났다"만 알고 어떤 감정 축이 얼마나 튀었는지 모르던
            # 다크 서킷 해소.
            if state.spike_detected and state.spike_detail:
                _axes = state.spike_detail.split(": ", 1)[-1]
                # [2026-07-14 수치 비노출] 델타 숫자 제거 — 렌더-facing 수치가 산문에
                # 리터럴로 서술됨이 실증("7점 그대로", deepseek_interview_results §7).
                # 방향(↑↓)만 남긴다. 진단용 원값은 spike 로그/bus에 그대로.
                _axes = re.sub(r"\([0-9.]+\)", "", _axes)
                spike_marker = f" ⚡[{_axes}]"
            elif state.spike_detected:
                spike_marker = " ⚡"
            else:
                spike_marker = ""
            # [2026-07-14 수치 비노출] intensity 숫자 → 어휘 티어(light/medium/deep —
            # analysis_resources 기존 어휘와 통일). 격랑 MEASURE(수치 없는 상태창)와
            # Slot 29 gloss("not a stated number")가 같은 원칙의 방증. 정밀값은 bus 잔존.
            _tier = "deep" if state.intensity >= 0.65 else ("medium" if state.intensity >= 0.35 else "light")
            lines.append(
                f"  {npc_name}: {hint} ({_tier}){spike_marker}"
            )
            # Scene drift 메타 — turn_pair ≠ scene_pair일 때만 블록 추가
            turn_pair = (state.base_label, state.modifier_label)
            scene_pair = (state.scene_base, state.scene_mod)
            if scene_pair[0] and turn_pair != scene_pair:
                scene_hint = EmotionEngine._compose_pair_hint(*scene_pair)
                drift_lines.append(
                    f"  {npc_name}: turn vs scene «{scene_hint}»"
                )

        if drift_lines:
            lines.append("")
            lines.append("[Scene Drift] turn≠scene: feeling mid-shift; let the surface lag behind.")
            lines.extend(drift_lines)

        return "\n".join(lines) if len(lines) > 1 else ""

    # ---------------------------------------------------------
    # Utility: Bus Output Builder
    # ---------------------------------------------------------
    @staticmethod
    def to_bus_dict(emotion_states: Dict[str, EmotionState]) -> Dict[str, Any]:
        """SharedBus.emotion 필드에 저장할 dict 생성.

        summary 스키마 변경 (v2):
          - 'dominant' 제거 → 'base' (pair의 base 측)
          - 'modifier' 추가 (pair의 modifier 측, P2 이후 채워짐)
          - 외부 소비자는 P1b에서 함께 rename됨 (story_director, world_board)

        summary 스키마 v3 (2026-05-20):
          - scene_base / scene_mod 노출 — turn vs scene drift 감지 가능 (6.1)
          - pair_confidence 노출 — Tier 매치 강도. story_director focus 가중치 (6.4)
          summary는 외부 소비자가 가벼운 의사결정에 쓰는 채널이라 가능한 평면 dict 유지.
        """
        return {
            "active": bool(emotion_states),
            "states": {
                name: state.to_dict()
                for name, state in emotion_states.items()
            },
            "summary": {
                name: {
                    "base": state.base_label,
                    "modifier": state.modifier_label,
                    "intensity": state.intensity,
                    "spike": state.spike_detected,
                    # v3 (2026-05-20): scene drift 감지용
                    "scene_base": state.scene_base,
                    "scene_mod": state.scene_mod,
                    # v3 (2026-05-20): 관계 라벨 매치 강도 (Tier 1=1.0 … Tier 9=0.0)
                    "pair_confidence": state.pair_confidence,
                }
                for name, state in emotion_states.items()
            },
        }
