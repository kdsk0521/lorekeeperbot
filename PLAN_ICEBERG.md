# 빙산이론 구현 — Theoria↔Renderer 수면 레이어 (iceberg.py)

## Context

**문제**: Theoria(Flash/Left Brain)가 분석한 심리학 용어, 수치, 프레임워크 이름이 Renderer(Main/Right Brain) 프롬프트에 거의 날것으로 주입됨. "echo하지 마"라는 가드를 붙여도 모델이 컨텍스트에서 본 용어를 산문에 쓰는 것은 구조적으로 막기 어려움.

**해법**: 이중뇌 아키텍처를 활용한 빙산이론 — 코드가 "수면" 역할. Theoria의 분석(수면 아래)을 **관찰 가능한 행동 힌트**(수면 위)로 번역한 뒤 Renderer에 전달. Renderer는 분석 용어를 애초에 볼 수 없으므로 echo 불가.

**부수 효과**: 번역 규칙을 가르치던 text_resources.py 상수 ~1,600토큰 절감 가능.

---

## 이론적 근거 (바벨2 / 커비 / 목격자 교차 검증)

### 수면 기준 (커비 ┣┫ 원칙)

> "Does this exist without cognitive processing?"
> → YES = prose (physical world) → Renderer에 전달
> → NO = ┣┫ (mental world) → iceberg가 번역 또는 제거

이 테스트가 iceberg.py의 **공식 수면 기준**:
- 물리적으로 관찰 가능한 것 (descriptor, 신체 신호, 행동) → 수면 위
- 인지 후에만 존재하는 것 (축 이름, 수치, 프레임워크명, 분석 레이블) → 수면 아래 (코드가 번역)

### Pidgin Decompression [1-4] (커비/바벨2 공통)

iceberg.py의 목표 = Decompression Level [4] 강제:
- [1] Label copied → prose ❌ (iceberg가 원천 차단)
- [2] Adjective survives as modifier ❌ (수치/축명 제거)
- [3] Behavior + smuggled interpretation ❌ (프레임워크명 제거)
- [4] Action + temporal delay → reader renders verdict ✓ (behavioral hint만 전달)

### 4층 심리 구조 = 3개 프레임워크 일치

| 목격자 Character Core | 커비 deep_read | 빙산 번역 |
|---|---|---|
| Declared Self (표면) | Surface | ▸평소(80%) |
| Believed Self (중간) | Adaptation | ▸반복패턴 |
| Actual Self (심층) | Core | ▸극한에서만 |
| Lack (기저) | Lack | ▸절대 직접 말하지 마 |

### 善惡果 검증

- **善惡果 1: SECRET** = NPCKnowledge 빈칸의 근거. "Gap between evidence and interpretation = story fuel." 내용(evidence)은 보존, 예측 라벨(interpretation)은 제거.
- **善惡果 3: HUMANITY** = iceberg가 해결하는 문제. "If internal monologue reads like a diagnostic manual → fruit eaten."

### 바벨2 ↔ 빙산이론 교차점

**Matrix II (Pidgin) = iceberg.py 그 자체**
- 바벨2의 Pidgin Matrix: "언어가 어디에서 도착하는가" — `<arrived from pidgin>` 슬롯
- iceberg.py가 정확히 이 역할: Theoria의 분석 언어(pidgin) → 관찰 가능한 산문 언어로 번역
- Pidgin Decompression [1-4] 강제 = iceberg의 번역 품질 기준 (위 섹션 참조)

**Matrix VI (Weather Forecast) = 동적 수면 시스템**
- 바벨2: "묘사가 무엇을 예측하는가" — 묘사 ≠ 장식, 묘사 = 예보
- iceberg 수면 높이 = "독자(=PC)에게 어떤 예측을 가능하게 할지" 결정
- scene_type별 수면: 전투에서는 신체 날씨가 보이고, 친밀에서는 내면 날씨가 보임

**Protocol I (Causal Architecture) 3-tier ↔ 수면 깊이**
| 바벨2 인과 3층 | 수면 depth | iceberg 노출 |
|---|---|---|
| Visible Cause (보이는 원인) | ≥ 0.6 | soma descriptor, 표면 행동 |
| Hidden Cause (숨겨진 원인) | ≥ 0.4 | psyche descriptor, ▸반복패턴 |
| Root Cause (근본 원인) | < 0.2 | ▸극한에서만, ▸절대 직접 말하지 마 |

**Prefill v3 (B.0-B.7) ↔ Telescope + iceberg**
| 바벨2 Prefill Phase | 현재 시스템 대응 | 상태 |
|---|---|---|
| A. Lineage (계보) | [Who] 게이트 | 구현됨 |
| B.0 Scene Diagnosis | [Logic] + [5W1H] 게이트 | 구현됨 |
| B.1 Principle Invocation | [TheoryAlign] + [GenreCoherence] 게이트 | 구현됨 |
| B.2 State | iceberg.translate_psyche_states() | Phase 1-2 |
| B.3 Structural Drain | [Structural] 게이트 신설 후보 | Phase 9 |
| B.4 Causal Commitment | [CausalCommit] 게이트 신설 후보 | Phase 9 |
| B.5 Hold Open | [Rhythm] 게이트 (부분) | 보강 필요 |
| B.6 Risk | [PC_Check] 게이트 | 구현됨 |
| B.7 Dead on Arrival | [DOA] 게이트 신설 후보 | Phase 9 |
| C. Scene | [Render] + [Final] 게이트 | 구현됨 |

**Matrix XXI (Unfired Gun) = NPCKnowledge 빈칸**
- "발사되지 않은 총": 설정된 것이 발현되기까지의 지연 = 서사 연료
- NPCKnowledge에서 leak_risk/would_share 제거 = 총을 보여주되 발사 시점을 정하지 않음
- iceberg가 "what"(내용)은 보존, "when/how"(예측)는 제거 → Unfired Gun의 코드 구현
- 善惡果 1 (SECRET)과 동일 원리: "Gap between evidence and interpretation = story fuel"

### 커비 6축 ↔ Theoria 6축 대응

| 커비 L_STATUS | Theoria psyche_states | iceberg 처리 |
|---|---|---|
| Μ Mental (정신) | psyche (정신) | descriptor만 → 수면 위 |
| Φ Soma (신체) | soma (신체) | descriptor만 → 수면 위 |
| Λ Coping (대처) | coping (대처 양식) | 제거 — 코드용 (reactance/desistance 트리거) |
| Ι Relation (관계) | relation (관계) | descriptor만 → 수면 위 |
| 東 Eastern (동양적) | cultural_affect | 제거 — judgment theory_mod 내부 소비 |
| 象 Image (이미지/상) | — (대응 없음) | Theoria 미출력 |

핵심 차이: 커비는 6축 전부를 ┣┫에서 처리 (모델 자율 판단). 우리는 3축(psyche/soma/relation) descriptor만 수면 위, 나머지(coping/cultural_affect)는 코드가 내부 소비 후 행동 지시로 변환. 모델 자율 → 코드 강제 = iceberg의 존재 이유.

### Energy Direction × Relationship State 교차 검증

커비의 SITUATION PRIORITY: Relationship State × Energy Direction → 우선 행동 매트릭스.

```
                  RISING     STAGNANT   DETONATION  AFTERSHOCK
EXPLORATION      관찰 우선   환경 질감   충격 반응    거리 유지
ESTABLISHMENT    마찰 증가   불편한 침묵  경계 시험    재평가
RUPTURE          대립 격화   냉전        폭발         잔해 속 선택
INTIMACY         긴장과 기대  취약성 노출  마스크 붕괴   나체의 침묵
```

우리 시스템 대응:
- Relationship State ≈ connection_depth stage (initial/warming/established/intimate/ruptured)
- Energy Direction ≈ DAI energy_direction (idle/rising/stagnant/detonation/aftershock)

현재 iceberg는 이 두 축을 **독립적으로** 번역. 향후 NPC별 수면에서 교차 적용 가능:
- RUPTURE + DETONATION = 수면 최저 (모든 것이 터져 나옴)
- EXPLORATION + IDLE = 수면 최고 (관찰만, 내면 미노출)
→ 향후 노브 후보 (NPC별 수면 조절)에 반영

### Causal Commitment (바벨2 B.4)

> "이 정보를 보여줬다면, 그것이 장면 안에서 무엇을 변형시켜야 하는가?"

iceberg가 "뭘 보여줄까"를 결정 → Causal Commitment는 "보여준 것이 장면에 실제로 작용해야 한다"는 원칙.

예시:
- iceberg가 "눈에 띄는 체언어"를 노출 → 장면 내 누군가가 실제로 알아채야 함 (아니면 왜 보여줬나?)
- ▸반복패턴을 노출 → 그 패턴이 이 턴에 실제로 발현되어야 함
- NPC의 secrets_held를 노출 → 비밀이 장면 행동에 영향을 줘야 함 (직접 노출 아니더라도)

iceberg와의 관계:
- iceberg = 수면 위 무엇을 놓을지 (content selection)
- Causal Commitment = 놓인 것이 장면을 실제로 변형하는지 (content activation)
- 이 둘이 합쳐져야 "보여줬지만 아무 역할 안 하는 정보" 방지

구현 위치: Phase 9 Telescope [CausalCommit] 게이트 — "iceberg가 노출한 정보 중 장면에서 작동하지 않은 것이 있는가?"

### Session-Phase Calibration (바벨2 세션 진행 원칙)

바벨2: 세션 진행에 따라 서사 원칙의 가중치가 변화.

| 세션 단계 | 핵심 원칙 | 이유 |
|---|---|---|
| Exchange 1-2 (도입) | Structural Drain (B.3) | 상투적 도입 시퀀스("도착→경외→유대") 방지가 최우선 |
| Exchange 3-8 (전개) | Causal Commitment (B.4) | 정보가 장면을 변형해야, 긴장 유지 |
| Exchange 9+ (마무리) | Hold Open (B.5) | 닫지 않는 기술, 여운 |

iceberg 수면에 적용 가능한 시간축 노브:
- 초반 (turn 1-3): 수면 +0.1 — PC가 아직 관찰자, 환경 우선
- 중반 (turn 4-8): 기본 수면 — 점진적 공개
- 후반 (turn 9+): 수면 -0.05 — 축적된 관계가 자연스럽게 더 많이 드러냄
→ 향후 노브 후보에 반영

### iceberg ↔ ┣┫ 역할 분리

```
iceberg.py   = 입력 필터 (뭘 보여줄까 — content selection)
Telescope ┣┫ = 출력 필터 (어떻게 쓸까 — craft quality CoT)
```

커비 원래 ┣┫ = 콘텐츠 분리기 (모델이 자율 판단). 현재 우리 ┣┫ = CoT (10게이트 구조화). iceberg가 입력 쪽 분리를 코드로 처리하므로, ┣┫는 craft CoT에 특화 가능. Phase 9에서 게이트 재설계.

---

## 설계 원칙

1. **Theoria 스키마 변경 없음** — Flash 출력은 그대로. 코드가 중간에서 번역.
2. **정보 손실 없음** — 원본 DAI는 보존. 번역본이 별도로 생성됨.
3. **slot_manager만 수정** — 번역 함수를 호출해서 포맷팅. 다른 모듈(waterfall, une_facade 등)은 원본 DAI 계속 사용.
4. **점진적 적용** — 슬롯별로 독립 적용 가능. 한 슬롯 적용 후 테스트 → 다음.

---

## 파일 구조

### 신규: `iceberg.py` (번역 레이어)

```
iceberg.py
├── translate_psyche_states(psyche_data) → str     # Slot 14
├── translate_position_effect(pos, eff) → str      # Slot 13
├── translate_energy_direction(energy) → str       # Slot 16 (label→prose hint)
├── translate_quality_flags(flags) → str           # Slot 16
├── translate_npc_attitudes(attitudes) → str       # Slot 17
├── translate_connection_depth(stage, depth, tension) → str  # Slot 17
├── translate_intimacy(intimacy_data) → str        # Slot 17
├── translate_narrative_chain(chain) → str         # Slot 28
├── translate_emotion_intensity(psyche_data) → str # Slot 29
├── translate_vigor_composure(v, c) → str          # Slot 29
├── translate_gm_move(gm_data) → str              # Slot 30
├── translate_open_threads(threads) → str          # Slot 28 (카테고리 라벨 strip)
└── translate_telescope_who(psyche_data) → str     # Slot 34 prefill
```

### 수정: `slot_manager.py`
- 각 슬롯의 DAI 포맷팅 로직에서 `iceberg.translate_*()` 호출
- anti-echo 가드 문자열 대부분 제거 (번역 후 필요 없음)

### 수정: `text_resources.py`
- PSYCHE_STATE_RENDERING: 프레임워크 설명 제거, 핵심 지시만 보존
- COGNITIVE_DATA_INTEGRATION: Logos/Four-Layer 교육 제거, 렌더링 지시만 보존
- NPC_BEHAVIOR_SYSTEM: 심리학 프레임워크 이름 제거
- ANTI_CLICHE: Pidgin Decompression 프레임워크 제거, 규칙만 보존

---

## 번역 테이블 상세

### 1. psyche_states (Slot 14) — 가장 큰 변화

**현재** (slot_manager L889-916):
```
- 캐릭터명: Μ[공포에 질린 표정±-75/fear] Φ[빠른 호흡, 떨림] Ι[불안한 거리감±40]
  └ Surface: 침착한 척... Adaptation: 유머로 회피... Core: 친밀감에 대한 공포... Lack: 안전한 취약성
```
문제: ±value, /emotion, deep_read의 분석적 층위명(Surface/Adaptation/Core/Lack)

**번역 후**:
```
- 캐릭터명: 공포에 질린 표정. 빠른 호흡, 떨림. 불안한 거리감.
  └ 침착한 척 하지만 분위기가 무거워질 때마다 웃는다. 누군가 다가오면 자리를 뜬다.
```
변환 규칙:
- `Μ[...±value/emotion] Φ[...] Ι[...±value]` → descriptor만 연결 (값/축명 제거)
- `primary_emotion` → 제거 (descriptor에 이미 반영됨)
- `value` (±100) → 제거 (Emotion Intensity에서 행동 강도로 대체)
- `deep_read` → 층위 레이블을 **렌더링 가중치**로 교체 (분석 프레임워크명 제거)
  - `Surface:` → `▸평소(80%):` (가장 많이 보여줘)
  - `Adaptation:` → `▸반복패턴:` (장면 걸쳐 반복으로만)
  - `Core:` → `▸극한에서만:` (마스크가 벗겨질 때만 5%)
  - `Lack:` → `▸절대 직접 말하지 마:` (보상 행동의 형태로만)

```python
def translate_psyche_states(psyche_data: dict) -> str:
    """6축 심리 데이터 → 관찰 가능한 행동 힌트로 변환."""
    lines = []
    for name, state in psyche_data.items():
        if isinstance(state, str):
            lines.append(f"- {name}: {state}")
            continue
        if not isinstance(state, dict):
            continue

        psyche = state.get("psyche", state.get("mental", {}))
        soma = state.get("soma", {})
        relation = state.get("relation", {})
        deep = state.get("deep_read", "")

        # descriptor만 추출 (값/축명/이모션태그 제거)
        parts = []
        if psyche.get("descriptor"):
            parts.append(psyche["descriptor"])
        if soma.get("descriptor"):
            parts.append(soma["descriptor"])
        if relation.get("descriptor"):
            parts.append(relation["descriptor"])

        line = f"- {name}: {'. '.join(parts)}" if parts else f"- {name}"
        lines.append(line)

        # deep_read: 층위 레이블 제거
        if deep:
            clean = _rename_layer_labels(deep)
            lines.append(f"  └ {clean}")

    return "\n".join(lines)

_LAYER_RENAMES = {
    "Surface": "▸평소(80%)",
    "Adaptation": "▸반복패턴",
    "Core": "▸극한에서만",
    "Lack": "▸절대 직접 말하지 마",
}

def _rename_layer_labels(deep_read: str) -> str:
    """4층 분석 레이블 → 렌더링 가중치 교체."""
    result = deep_read
    for eng, kor in _LAYER_RENAMES.items():
        result = result.replace(f"{eng}:", f"{kor}:")
        result = result.replace(f"{eng}：", f"{kor}:")
    return result
```

### 2. position/effect (Slot 13) — 수치 제거

**현재**: `Position: 0.3 (건물에 갇혀있다)` / `Effect: 0.5 (제한된 자원)`
**번역 후**: `상황: 불리 — 건물에 갇혀있다` / `영향력: 보통 — 제한된 자원`

```python
_POS_TIERS = [
    (0.2, "절망적"),  # desperate
    (0.4, "불리"),    # disadvantaged
    (0.6, "보통"),    # controlled
    (0.8, "유리"),    # favorable
    (1.0, "지배적"),  # dominant
]

def translate_position_effect(position: dict, effect: dict) -> str:
    parts = []
    if position:
        tier = _to_tier(position.get("value", 0.5), _POS_TIERS)
        reason = position.get("reason", "")
        parts.append(f"상황: {tier} — {reason}" if reason else f"상황: {tier}")
    if effect:
        tier = _to_tier(effect.get("value", 0.5), _POS_TIERS)
        reason = effect.get("reason", "")
        parts.append(f"영향력: {tier} — {reason}" if reason else f"영향력: {tier}")
    return "\n".join(parts)
```

### 3. energy_direction (Slot 16) — 레이블 제거

**현재**: `### Energy Direction [ANALYSIS]: STAGNANT`
**번역 후**: `### 장면 호흡: 정적 — 침묵, 부재의 존재감, 말하지 않은 무게`

```python
_ENERGY_HINTS = {
    "idle": "일상 — 환경 질감, 間(MA), 일상적 디테일, 느린 리듬",
    "rising": "고조 — 인물 간 마찰, 체언어 모순, 대인 긴장",
    "stagnant": "정적 — 침묵, 부재의 존재감, 말하지 않은 무게",
    "detonation": "폭발 — 물리적 충격, 짧은 문장, 행동의 대가",
    "aftershock": "여진 — 침묵, 잔해, 지연된 반응, 무감각",
}
```

### 4. quality_flags (Slot 16) — 경고 레이블 → 행동 지시

**현재**: `⚠ CONVERGENCE: Both parties exiting comfortable without earning it`
**번역 후**: `장면이 너무 쉽게 화해하고 있다. 마찰 없이 안정으로 가는 것은 허용하지 마.`

```python
_FLAG_DIRECTIVES = {
    "convergence_warning": "장면이 갈등 없이 합의에 도달하고 있다. 불편함을 유지하라.",
    "echo_warning": "NPC가 PC 감정을 따라하고 있다. NPC만의 반응을 만들어라.",
    "stagnation_warning": "3턴째 장면 에너지가 평평하다. 외부 자극을 자연스럽게 도입하라.",
    "mse_deviation": "NPC의 정신 상태가 급변했다. 이전 행동과의 일관성을 점검하고, 변화에 인과적 근거를 부여하라.",
    "dissonance_flag": "NPC가 모순된 신념/행동을 보이고 있다. 즉시 해소하지 마라 — 불편함을 행동으로 보여줘라.",
    "redemption_warning": "NPC가 근거 없이 태도를 누그러뜨리고 있다. 변화에는 대가가 필요하다. 되돌려라.",
    "symptom_cluster": "NPC가 {cluster} 증상군을 보이고 있다. 증상을 일관된 세트로 유지하라. 체리피킹 금지.",
}
```
NOTE: `symptom_cluster`는 boolean이 아닌 string(PTSD/anxiety/depression/null). null이 아닐 때만 주입, `{cluster}`에 값 삽입.

### 5. NPC attitudes (Slot 17) — 태도 라벨 제거

**현재**: `- 이름: suspicious (warming) — PC가 동생을 구해서`
**번역 후**: `- 이름: PC가 동생을 구해서 — 경계를 풀기 시작하는 기미`

```python
_TRAJECTORY_HINTS = {
    "warming": "경계를 풀기 시작하는 기미",
    "cooling": "거리를 두기 시작하는 기미",
    "stable": "현재 태도 유지",
    "volatile": "태도가 불안정, 작은 자극에도 변화 가능",
}
```

### 6. connection_depth (Slot 17) — 스코어 제거

**현재**: `- 이름: Connection=Warming(45/100) tension=65 — Behavioral boundaries lowering`
**번역 후**: `- 이름: 관계 심화 중 — 행동적 경계가 낮아지고 있다. 긴장감 높음.`

### 7. emotion_intensity (Slot 29) — 밴드명 제거

**현재**: `이름: |psyche| 75 -> OVERT — obvious physical signs`
**번역 후**: `이름: 감정이 신체에 뚜렷이 드러남 — 숨기기 어려운 수준`

```python
_INTENSITY_HINTS = [
    (30, "미세한 표정 변화 수준 — 주의 깊게 봐야 알아챔"),
    (60, "눈에 띄는 체언어 — 관찰자가 알아챌 수 있음"),
    (80, "뚜렷한 신체 반응 — 숨기기 어려움"),
    (100, "신체가 압도됨 — 평정을 유지할 수 없음"),
]
```

### 8. vigor/composure contrast (Slot 29) — 수치·해석 제거, 사실만

**현재**: `[CONTRAST] 기력 85 vs 평정 30 (차이 55) Body functional, mind fracturing.`
**번역 후**: `기력과 평정 사이에 큰 괴리가 있다. 행동으로 드러내라.`
수치(85/30/55)도 해석("Body functional, mind fracturing")도 제거. 상태창에 값이 이미 있으므로 "괴리 존재" 사실만 전달 → 모델이 상태창 보고 판단. depth 게이팅 없음 (시스템 상태이므로 항상 주입).

### 9. gm_move (Slot 30) — 타입 라벨 제거

**현재**: `type: introduce_obstacle\ndescription: 건물이 흔들린다`
**번역 후**: `건물이 흔들린다`
(description만. type은 Theoria가 내부용으로 쓴 분류일 뿐)

### 10. position_friction (Slot 30) — Slot 13에 통합, Slot 30 제거

**현재**: Position이 Slot 13 + Slot 30 두 번 주입됨. Slot 30에 2단계 마찰:
- `pos_val < 0.3`: DESPERATE — "barriers are real, no narrative convenience"
- `pos_val < 0.5`: DISADVANTAGED — "render the cost honestly"

**변경**: `translate_position_effect()`에서 tier에 따라 마찰 지시를 자동 append:
```python
_POS_FRICTION = {
    "절망적": "(friction text — 구현 시 범용적으로 작성)",
    "불리": "(friction text — 구현 시 범용적으로 작성)",
}
# tier가 "절망적"/"불리"이면 position 라인 아래에 └ friction 추가
```
Slot 30의 `[POSITION_FRICTION]` 블록 완전 제거.

### 11. telescope [Who] (Slot 34 prefill) — emotion 태그 제거

**현재**: `[Who] 이름:fear | 이름2:anger`
**번역 후**: `[Who] 이름 | 이름2` (이름만. emotion은 psyche descriptor에서 이미 전달)

### 12. narrative_chain (Slot 28) — 라벨 제거

**현재**: `chain_status: OPEN / topic_lock: None / conclusion_proximity: 30 / silence_type: heavy`
**번역 후**: `대화가 열려있다. 묵직한 침묵이 흐르고 있다.`

```python
_SILENCE_HINTS = {
    "reflective": "사색적 침묵 — 시간이 느려진다",
    "hesitant": "망설이는 침묵 — 삼킨 말이 있다",
    "heavy": "묵직한 침묵 — 둘 다 알고 있지만 말하지 않는다",
    "tense": "긴장된 침묵 — 한 마디가 모든 걸 바꿀 수 있다",
}
```

**open_threads 처리**: 카테고리 라벨(`Mystery:`, `Threat:`, `Desire:` 등) 제거, 스레드 내용만 통과.
- `"- Mystery: 소연이 숨기는 것"` → `"- 소연이 숨기는 것"`
- 가드 텍스트("[OPEN THREADS — AMBIENT ONLY]" + 지시문)는 그대로 유지 — craft 지시이므로 iceberg 대상 아님.

### 13. trait_connections (Slot 16 Apophenia Guard)

**현재**: `- 이름: OBVIOUS=피해자 연민 → INSTEAD: 이용 가능성 인식 | 표정 변화`
**번역 후**: `- 이름: 뻔한 방향(피해자 연민) 대신 → 이용 가능성 인식 | 표정 변화`
(OBVIOUS= 라벨만 한국어로. 나머지 구조 유지)

---

## slot_manager.py 변경 상세

### Slot 14 (L889-916)
```python
# 현재
psyche_lines = ["(Author reference only. NEVER echo axis names...)"]
for char_name, state in psyche_data.items():
    # Μ[...±value/emotion] Φ[...] Ι[...±value] 포맷

# 변경
import iceberg
psyche_states = iceberg.translate_psyche_states(psyche_data)
```
- anti-echo 가드 전체 제거
- 복잡한 Μ/Φ/Ι 포맷팅 제거

### Slot 13 (L692-703)
```python
# 변경: 수치 대신 서수 + reason
import iceberg
pos_eff_text = iceberg.translate_position_effect(position, effect)
if pos_eff_text:
    input_analysis_parts.append(pos_eff_text)
```

### Slot 16 (L710-792)
- energy_direction: `iceberg.translate_energy_direction()`
- quality_flags: `iceberg.translate_quality_flags()`
- trait_connections: OBVIOUS= → 한국어 라벨
- HabitusAnalysis: 키-값 그대로 (이미 행동적)
- SensoryAnchors: 그대로 (이미 감각적)
- Foreshadowing: 그대로 (ambient fact)

### Slot 17 (L797-885)
- NPCAttitudes: `iceberg.translate_npc_attitudes()` — attitude 라벨 제거, trajectory → 행동 힌트, reason 유지
- Connection Depth: `iceberg.translate_connection_depth()` — 수치/stage명 제거, hint만 한국어로
- IntimacyAnalysis: `iceberg.translate_intimacy()` — 상세 번역 규칙:
  - **기존 버그 수정**: `vulnerability` → `window_check`로 필드명 정정, `dual_control` 읽기 추가
  - `window_check`: within/above/below → 신체 상태 힌트 ("안정 범위 — 참여 가능" / "과각성 — 압도됨" / "저각성 — 얼어붙음")
  - `dual_control`: SES/SIS 라벨 제거 → "끌어당기는 것" / "멈추게 하는 것" + 내용 통과
  - `desire_type`: Basson 분류 라벨 → 행동 동기 힌트 ("확인받고 싶다" / "주도권을 쥐고 싶다" 등)
  - `power_dynamic`: 한국어 통과 (프레임워크명 strip)
  - `body_memory`: 한국어 통과 (프레임워크명 strip)
- NPCKnowledge: **내용 유지, 예측 라벨 제거** (knows/secrets/false_beliefs/deception_cues 유지, leak_risk/would_share 제거 — GM이론의 "빈칸": 내용은 알되 표면화 시점은 정하지 않음)
- Milestone hints: 그대로 (이미 행동적)

### Slot 28 (L918-940)
- `iceberg.translate_narrative_chain()`

### Slot 29 (L1022-1074)
- Emotion Intensity: `iceberg.translate_emotion_intensity()`
- Vigor/Composure: `iceberg.translate_vigor_composure()`

### Slot 30 (L942-978)
- GM Move: `iceberg.translate_gm_move()`
- Position Friction: 제거 (Slot 13으로 통합)

### Slot 33 (L1186-1237) — Scene Breathing 제거
- `_SCENE_BREATHING` 딕셔너리 + 주입 코드 제거 (L1203-1231)
  - Slot 16의 iceberg `translate_energy_direction()`이 동일 정보를 행동 힌트로 커버
  - 중복 제거 → Slot 33 부하 감소
- Format Feedback, NPC Recency Echo, 5W1H Echo는 그대로 유지
- 향후: NPC Recency Echo를 포인터 방식("Slot 7 프로필 정독하라")으로 축소 검토

### Slot 34 Prefill (L96-145)
- [Who] 블록: `iceberg.translate_telescope_who()`

---

## text_resources.py 절감

### PSYCHE_STATE_RENDERING (L1126-1191) → 대폭 축소

제거:
- L1131-1155: 데이터 구조 설명 (psyche/soma/relation 필드 설명) — ~200 tokens
- L1160-1164: INTENSITY CALIBRATION (0-30/30-60/60-80/80-100) — 코드가 처리
- L1166-1170: POLYVAGAL → BODY MAPPING (ventral/sympathetic/dorsal) — 코드가 처리
- L1171-1182: CROSS-AXIS + NEW AXIS INTERACTIONS — 코드가 조합

보존 (~80 tokens):
- L1156-1158: "Convert every psyche value to THIS character's specific body signal" (핵심 지시)
- L1184-1189: CHARACTER-SPECIFIC OVERRIDE (개인화 지시)

### COGNITIVE_DATA_INTEGRATION (L1196-1251) → 중간 축소

제거:
- L1199-1202: "Left Brain data is ANALYTICAL. Your output is EXPERIENTIAL." — 번역 레이어가 처리
- L1213-1223: Logos Dynamics 프레임워크 설명 — 코드가 behavioral hint만 전달
- L1225-1234: Four-Layer Architecture 설명 — deep_read가 이미 해석 포함

보존:
- L1204-1208: Profile Reading Protocol (프로필 꼼꼼히 읽기) — 순수 craft
- L1236-1242: Fermentation Recall (기억 왜곡) — 순수 craft
- L1244-1250: World Response Framing (세계 논리 체크) — 순수 craft

### NPC_BEHAVIOR_SYSTEM (L431-495) → 소폭 축소

제거:
- L468-475: "Behavioral Persistence (Bandura/Maruna)", "Dark Triad rendering" 프레임워크 이름
  → 행동 예시는 유지, 학자 이름만 제거

보존:
- 나머지 전부 (자율성, 지식 격리, 비밀 전파 등은 순수 서사 규칙)

### ANTI_CLICHE Pidgin Decompression (L1110-1119)

제거:
- 4단계 프레임워크 설명 (L1110-1118) — Pidgin Echo는 코드 후처리로 가능

보존:
- L1119-1120: "Label keyword as adjective = rewrite to Level 3+" 규칙

### 총 절감: ~1,200-1,600 tokens (슬롯에서 제거되는 anti-echo 가드 포함)

---

## 동적 수면 — 노브 시스템

### 노브 1: SceneType (수면 기본 높이)

```python
_WATER_LEVEL = {
    "summary":  0.9,   # 수면 최고 — 사실만, 내면 거의 없음
    "combat":   0.8,   # 수면 높음 — 신체 신호만
    "normal":   0.5,   # 수면 기본
    "social":   0.3,   # 수면 낮음 — 태도/관계 디테일 노출
    "intimate": 0.1,   # 수면 최저 — 취약성, 간극, 신체 기억
}
```

### 노브 2: EnergyDirection (수면 보정)

```python
_ENERGY_MOD = {
    "idle":       -0.1,  # 미세 디테일 더 노출
    "stagnant":   -0.05, # 약간 더 노출
    "rising":      0.0,  # 보정 없음
    "detonation": +0.2,  # 즉각 신체만 — 분석 숨김
    "aftershock": -0.15, # 잔해가 수면에 떠오름
}
```

### 수면 높이 → 노출 범위

```
depth = _WATER_LEVEL[scene_type] + _ENERGY_MOD[energy]
depth = clamp(depth, 0.0, 1.0)

depth ≥ 0.8: descriptor(soma)만. psyche/relation 제거. deep_read 제거.
depth ≥ 0.6: descriptor(soma+psyche). relation 제거. deep_read ▸평소만.
depth ≥ 0.4: 전체 descriptor. deep_read ▸평소+▸반복패턴.
depth ≥ 0.2: 전체 descriptor + self_opacity 간극. deep_read ▸극한에서만 포함.
depth < 0.2: 전부 노출 (▸절대 직접 말하지 마 포함). intimate 전용.
```

### 함수 시그니처 변경

모든 translate_* 함수가 depth를 받음:
```python
def translate_psyche_states(psyche_data: dict, scene_type: str = "normal", energy: str = "idle") -> str:
    depth = _calc_depth(scene_type, energy)
    # depth에 따라 노출 범위 결정
```

slot_manager.py에서 scene_type + energy_direction을 iceberg 함수에 전달:
```python
import iceberg
scene = dai.get("scene_type", "normal")
energy = dai.get("energy_direction", "idle")
psyche_states = iceberg.translate_psyche_states(psyche_data, scene, energy)
```

### 향후 노브 후보 (Phase 2 이후)

**NPC별 수면 조절:**
- **Autonomous Trigger**: 트리거 발동 NPC → 수면 ↓ (priority에 비례)
  - henderson(7) → -0.25, attachment(5)/secret(5) → -0.2, reactance(6) → -0.15, etc.
  - 트리거 없는 배경 NPC → 기본 수면 유지 (자연스러운 스크린 타임 배분)
- **Connection Depth**: depth 높을수록 해당 NPC 수면 ↓ (이미 추적됨: `npc_attitudes[name].depth`)
- **활성 퀘스트**: 퀘스트가 NPC/장소를 언급하면 관련 대상 수면 ↓ (이미 추적됨: `ai_memory["quests"]`)
- **노트북 언급**: 유저 기록에 NPC명 반복 등장 → 점진적 공개 트리거 (이미 저장됨: notebook)

**NPC 블록 통합 (Slot 14 + 17 + 30 → 단일 NPC 블록):**
- 현재: 같은 NPC 정보가 Slot 14(심리) / Slot 17(태도·지식·관계) / Slot 30(자율행동)에 분산
- 목표: NPC당 하나의 통합 블록으로 iceberg가 조립
  ```
  - 이름: [상태 descriptor] [행동 방향]
    └ [deep_read 가중치]
    └ [알고 있는 것 / 숨기는 것]
  ```
- Renderer가 "이 인물" 단위로 읽음 → 자연스러운 렌더링

**시간축 보정 (Session-Phase Calibration):**
- **turn 1-3**: 수면 +0.1 — 도입부, PC는 관찰자. Structural Drain이 중요한 구간
- **turn 4-8**: 기본 수면 (보정 없음) — 전개부, Causal Commitment 구간
- **turn 9+**: 수면 -0.05 — 축적된 관계가 자연스럽게 드러남. Hold Open 구간
- 데이터 소스: `ai_memory["turn_count"]` (이미 추적됨)
- 바벨2 세션 진행 원칙의 코드 구현

**Relationship State × Energy 교차 보정 (NPC별):**
- connection stage + energy_direction 조합으로 NPC별 수면 미세 조정
- RUPTURE + DETONATION = 최대 노출 (-0.3), EXPLORATION + IDLE = 최소 노출 (+0.1)
- 데이터 소스: `npc_attitudes[name].stage` + DAI `energy_direction`
- 커비 SITUATION PRIORITY 매트릭스의 코드 구현

**전역 보정:**
- **PC 기질(passives)**: "관찰력" 등 특질 → 전역 수면 미세 조정 (이미 저장됨: `participant.passives`)
- **예측 오류**: SensoryAnchors 중 memory_link가 있는 것만 우선 surface
- **점진적 공개**: 같은 대상 반복 접근 감지 기준 미확정 — Theoria 스키마 확장 or 코드 턴 카운터

---

## 실행 순서 (8단계)

### Phase 1: iceberg.py 생성
1. 번역 테이블 (dict/list) 정의
2. 13개 translate_* 함수 구현
3. 단위 테스트 가능 (독립 모듈)

### Phase 2: Slot 14 (psyche_states) 적용 — 가장 큰 변화
1. slot_manager.py L889-916 → iceberg.translate_psyche_states() 호출
2. anti-echo 가드 제거
3. 테스트: 프롬프트 출력 확인

### Phase 3: Slot 13 + 30 (position/effect 통합)
1. Slot 13: 수치 → 서수
2. Slot 30: POSITION_FRICTION 제거, 핵심 지시만 Slot 13에 통합
3. 테스트

### Phase 4: Slot 16 (scene intelligence)
1. energy_direction, quality_flags, trait_connections 번역
2. 테스트

### Phase 5: Slot 17 (extended intelligence)
1. NPCAttitudes, Connection Depth, Intimacy 번역
2. NPCKnowledge는 그대로
3. 테스트

### Phase 6: Slot 28 + 29 (narrative chain + real-time)
1. narrative_chain 번역
2. emotion_intensity, vigor/composure 번역
3. 테스트

### Phase 7: Slot 34 prefill + Slot 30 gm_move
1. telescope [Who] emotion 제거
2. gm_move type 라벨 제거
3. 테스트

### Phase 8: text_resources.py 절감
1. PSYCHE_STATE_RENDERING 축소
2. COGNITIVE_DATA_INTEGRATION 축소
3. NPC_BEHAVIOR_SYSTEM 학자명 제거
4. ANTI_CLICHE Pidgin 프레임워크 제거
5. py_compile 검증

### Phase 9: 텔레스코프 — 소진+CoT 융합 (iceberg 효과 확인 후)

**전제**: Phase 1-8 완료 후, iceberg가 입력 필터를 충분히 처리하는지 확인.

**목표**: 3단계 품질 파이프라인 완성.

```
[Theoria] → iceberg(제거) → [프롬프트] → telescope(소진+CoT) → [산문]
             "안 보여줌"                   "먼저 써버림"+"판단"
```

- **제거** (iceberg, Phase 1-8): 분석 용어가 컨텍스트에 진입 불가. 코끼리를 방에 안 들임.
- **소진** (DOA): 서사적 상투구를 telescope 안에서 먼저 생성. 이미 쓴 토큰은 autoregressive 반복회피로 재생산 확률 ↓.
- **인식** (CoT): 구조적 클리셰를 이름 붙여 의식적 선택 대상으로 전환. "인식되지 않은 패턴은 자동 실행, 인식된 패턴은 선택의 대상."

**핵심 구분 — 금지 vs 소진**:
- 금지 = "이것을 쓰지 마" → 활성화 후 억제 → 역설적 증폭 ("코끼리를 생각하지 마라")
- 소진 = "이것을 먼저 써버려" → 활성화 후 반복회피가 자동 처리 → 자연 감쇠
- 현재 [Craft] 게이트가 금지 방식 → 소진 방식으로 전환

**현재 10개 게이트** (text_resources.py TELESCOPE_SYSTEM):
```
[Who] [5W1H] [Logic] [Craft] [PC_Check] [TheoryAlign] [GenreCoherence] [Rhythm] [Render] [Final]
```

**확정: 하이브리드 구조 (Domain → Cross-Check) + 충돌 강제 발견**

선택 근거:
- 이유 출력 강제 = 이미 채택된 철학. 토큰 희생은 수용 가능
- Phase B "충돌 1개 이상 찾아 해결" 강제 → 고무도장 방지
- 바벨2 Prefill v3 구조와 일치 (Phase A = B.0-B.4, Phase B = B.5-B.7+C)
- 도메인별 깊이 + 교차 검증 = 가장 완전한 점검

```
┣ Phase A: Domain Checks (도메인별 심층 점검)

[Scene Domain] — 장면 구조 점검
  ├ 장면 진단 (현 [5W1H] + [Logic] 통합)
  │   장면의 5W1H + 인과 논리 확인
  ├ 구조 소진 [Structural] (바벨2 B.3 + B.7)
  │   ☠ 이 장면 유형의 기본 시퀀스 명명 → 의식적 변형
  │   예: ☠ "대치→한쪽 양보→화해" → 이 순서를 깬다
  └ 인과 확인 [CausalCommit] (바벨2 B.4)
      iceberg가 노출한 정보가 장면에서 작동하는가?
      예: "떨리는 손" 노출 → 누군가 알아채거나 행동에 영향

[Character Domain] — 인물 점검
  ├ 인물 계보 [Who]
  │   등장 인물 + 심리 상태 + 이번 턴 행동 방향
  ├ PC 자율성 [PC_Check]
  │   PC 대사/내면 개입 여부
  └ 묘사 수준 [Pidgin]
      descriptor를 그대로 복사하지 않았나? [1-3] 수준인가?

[Craft Domain] — 산문 품질 점검
  ├ 구문 소진 [DOA] (바벨2 B.7)
  │   ☠ 이 장면에서 쓸 법한 상투적 표현 3-5개 먼저 생성
  │   예: ☠ "숨 막히는 아름다움" "전율이 흘렀다" "압도적 기운"
  ├ Cargo Cult [CargoCult] (커비 善惡果 2: SENTENCE)
  │   "이 문장 삭제해도 장면 유지되나?" — YES면 삭제
  │   "아무 인물의 아무 장면에 넣어도 성립하나?" — YES면 삭제
  └ 리듬 [Rhythm]
      문장 길이 변주, decompression 수준, 호흡

Phase B: Cross-Check (교차 검증 — 충돌 강제 발견)

[Collision] — ⚠ 반드시 1개 이상의 도메인 간 충돌을 찾아 해결할 것
  예시 충돌 유형:
  - Character "유머로 회피" vs Scene "밀실 대치" → 물리적 회피 불가, 언어적 회피로 수정
  - Craft DOA "전율" 태움 vs Character "공포 상태" → 공포 표현에 전율 대체어 필요
  - Scene CausalCommit "떨리는 손 작동" vs Character "마스크 유지(80%)" → 떨림을 숨기려는 행동으로

[Alignment] — 장르 정합성 + 이론 정렬
  TheoryAlign + GenreCoherence (현행 유지, 이유 강제)

[Final] — 최종 산문 방향 결정
┫
```

**금지 vs 소진 — 핵심 구분 (Phase A Craft Domain의 이론적 근거)**:

```
금지 (Prohibition):
  "코끼리를 생각하지 마라"
  → 활성화(코끼리 표상 생성) → 억제(쓰지 마) → 역설적 증폭
  → 모델: "숨 막히는 아름다움을 쓰지 마" → "숨 막히는" 토큰 활성화 → 확률 ↑
  → 현재 [Craft] 게이트, ANTI_CLICHE 상수가 이 방식

소진 (Pre-exhaustion / DOA):
  "코끼리를 먼저 그려놓아라"
  → 활성화(코끼리 표상 생성) → 출력(토큰 생성됨) → 반복회피가 자동 처리
  → 모델: "☠ 숨 막히는 아름다움" 먼저 생성 → 이후 산문에서 재출현 확률 ↓
  → 바벨2 B.7의 방식. Phase A [DOA]가 이것을 구현

인식 (Recognition / Pattern Salience):
  "이것은 코끼리다. 코끼리를 의식하라."
  → 패턴에 이름 붙임 → 암묵적 자동실행이 의식적 선택으로 전환
  → 모델: "☠ Structure: 도착→경외→유대" 인식 → 변형 가능
  → Phase A [Structural] + [CargoCult]가 이것을 구현
```

3단계 파이프라인 (보완적):
- **제거**(iceberg, Phase 1-8): 분석 용어 원천 차단 — 가장 강력, 범위 = 입력 데이터
- **소진**(DOA, Phase A Craft): 상투적 표현 사전 생성 — 기계적(repetition penalty), 범위 = 구문(좁음)
- **인식**(CoT, Phase A Scene+Craft): 구조적 클리셰 명명 — 인지적(pattern salience), 범위 = 구조(넓음)

**전후 비교**:

```
현재 (10게이트 종합 순차):
[Who] [5W1H] [Logic] [Craft] [PC_Check] [TheoryAlign] [GenreCoherence] [Rhythm] [Render] [Final]
→ 게이트당 1-2줄. 중간 게이트 피로. 교차 검증 없음.

재설계 (하이브리드 Phase A+B):
Phase A: [Scene: 3점검] [Character: 3점검] [Craft: 3점검]  (도메인당 5-8줄)
Phase B: [Collision: 충돌 1+개 필수] [Alignment] [Final]   (교차 검증 5-10줄)
→ 도메인별 깊이. 교차 충돌 강제 발견. 고무도장 방지.
```

**토큰 추정**:
- Phase A: ~20-25줄 (도메인 3개 × ~7줄)
- Phase B: ~10-15줄 (충돌 발견 + alignment + final)
- 총: ~30-40줄 ≈ 300-500 토큰
- 현재: ~20-25줄 ≈ 200-300 토큰
- 차이: +100-200 토큰. iceberg 절감(~1,200-1,600)으로 충분히 상쇄

**Phase B [Collision] 프롬프트 핵심**:
```
⚠ Phase A의 Scene/Character/Craft 도메인 결정을 교차 대조하라.
반드시 1개 이상의 충돌을 찾아 해결 방안을 제시할 것.
"충돌 없음"은 허용하지 않는다 — 찾을 때까지 점검하라.
```
이 강제가 고무도장을 방지하는 핵심 메커니즘. 모델은 "없다"고 쓸 수 없으므로 실제로 교차 점검을 수행.

**진행 방식**: iceberg 구현(Phase 1-8) 효과를 실제 테스트로 확인 → 상투구 잔존 양상에 따라 소진/인식 게이트 구체 설계.

---

## 검증

1. `py_compile iceberg.py` — 문법
2. `py_compile slot_manager.py` — 문법
3. `py_compile text_resources.py` — 문법
4. 프롬프트 비교: 변경 전/후 동일 DAI 입력 → 출력 비교
   - 분석 용어 잔존 여부 확인 (polyvagal, attachment, SUBTLE, OVERT 등)
   - 행동 힌트 충분성 확인
5. 실제 봇 테스트: 산문 품질 저하 없는지 확인
6. waterfall_pipeline.py, une_facade.py, orchestration.py — **변경 없음 확인**

## 리스크

| 리스크 | 심각도 | 대응 |
|--------|--------|------|
| **bus.dai in-place 수정 시 anomaly/judgment 파손** | CRITICAL | iceberg 함수는 DAI를 읽기만 → 별도 str 반환. bus.dai 절대 수정 불가. 설계 원칙 #2 엄수 |
| **POSITION_FRICTION Slot 13 통합 시 anti-sycophancy 약화** | 중간 | translate_position_effect()에서 tier별 마찰 지시 자동 append. 실제 테스트 후 강도 조정 |
| 행동 힌트가 너무 구체적 → Renderer가 힌트를 그대로 복사 | 중간 | 힌트를 "방향"으로만 제공, 구체적 행동은 Renderer에 위임 |
| deep_read에 분석 용어 잔존 | 낮음 | `_rename_layer_labels()` + Theoria 스키마에 "behavioral only" 주석 강화 |
| NPCKnowledge secrets가 여전히 echo | 낮음 | 별도 후처리 고려 (Phase 2) |
| 번역 테이블 누락 → KeyError | 낮음 | `.get()` + fallback ("알 수 없음") |
| test_text_resources_v3.py 상수 체크 실패 | 낮음 | 상수 축소만 (삭제 아님). 테스트 통과 유지 |
| IntimacyAnalysis 필드명 불일치 (기존 버그) | 중간 | slot_manager `vulnerability` → `window_check` 정정, `dual_control` 읽기 추가. Phase 5에서 수정 |
| une_facade.py energy vocabulary 불일치 (기존 버그) | 낮음 | iceberg와 무관. 별도 수정 가능 (falling/peak/steady → idle/stagnant/detonation/aftershock) |
