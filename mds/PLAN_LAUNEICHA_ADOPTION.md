# Launeicha/Telescope 개념 도입 계획서

## Context

**출처**: Launeicha Telescope 프롬프트 시스템 (외부 문서)
**목적**: Lorekeeper에 없거나 빈약한 개념만 선별 도입. 기존 구조(Dual-Brain, 34-Slot, 3축 표기, iceberg)와 양립.
**원칙**: 중복 도입 금지. Lorekeeper 용어로 재해석. 금지 표현("~하지 마라") 최소화.

**정리 순서**: Code → Analysis → Iceberg → Text (구현 난이도/우선순위 순)

---

## 0. Launeicha ↔ Lorekeeper 전체 매핑표

| Launeicha 개념 | Lorekeeper 대응 | 상태 | 비고 |
|---|---|---|---|
| Z_Synthesis (3+ undercurrents) | deep_read.undercurrents | **빈약** → 1-1, 2-1 | 필드 존재, 검증 없음 |
| Model Bias Control | Anti-cliche (text_resources) | **빈약** → 2-2 | 결과물만 잡음, 분석 차원 미보호 |
| Habitual Automaticity | decision_mode (intuitive/deliberative) | **삭제** ~~2-3~~ | decision_mode가 이미 커버. 분석 소극화 위험 |
| Retroactive Reasoning | coping_mechanism, decision_mode | **삭제** ~~2-4~~ | decision_mode + coping + self_opacity로 이미 커버 |
| Sensory Decay / Habituation | RENDERED ONCE + AMBIENT PERSISTENCE | **빈약** → 1-2, 4-4 | 원칙만 있고 추적/재활성화 조건 없음 |
| Schema Refraction (어휘 범위) | Camera Eye + Korean Landscape | **빈약** → 1-4, 4-1 | Code→프롬프트 전환. "수학자는 배관공 지식을 모른다" 수준의 원칙 |
| Sensory Metaphor Vehicle | Camera Eye | **없음** → 4-1 | 은유 매체의 물리성 제약 없음 |
| Prose Quantification | iceberg 수치→행동 번역 | **빈약** → 4-2 | vigor/doom만, 시간/거리/온도 미커버 |
| Residual Tracking (pratītya) | open_threads, unresolved | **빈약** → 1-3 | "성공의 잔여물" 관점 없음 |
| Causal Proportionality | 3축 temporal_density | **삭제** ~~1-5~~ | 3축 temporal_density가 이미 같은 역할 |
| Paragraph Structure | SENTENCE RHYTHM & DENSITY | **빈약** → 4-3 | 문장 단위만, 문단 단위 없음 |
| Pre-Emulation Check | Theoria → iceberg 파이프라인 | **삭제** ~~3-1~~ | 현재 iceberg 충분 |
| Trait Active/Dormant | trait_connections + translate | **삭제** ~~1-6~~ | Theoria가 이미 수행 |
| ACL Trait Conflict | trait_connections | **삭제** ~~3-2~~ | Theoria가 이미 암묵 수행 |
| Off-Screen Persistence | TEMPORAL_FLOW §5 | **구현됨** (강화 가능) | 5대 원칙 계획에서 별도 강화 예정 |
| Inline Computation | Telescope v2 (┣┫ 10 gates) | **구현됨** | 더 정교한 구현. 추가 불필요 |
| Readability (감각 앵커) | Camera Eye + FULL SENSORIUM | **구현됨** | 추가 불필요 |
| Delayed Response | DELAYED & IMPERFECT RESPONSE | **구현됨** | 5대 원칙에서 선언부 강화 예정 |
| Epistemological Boundary (3층) | EPISTEMIC BOUNDARY (1층) | **빈약** → 2-5 | Analysis→스키마 필드(apprehension_gap). 서술형 |
| Knowledge Isolation | KNOWLEDGE ISOLATION (NPC 행동) | **구현됨** | 행동 제약은 있음, 서술 제약은 4-1에서 보완 |
| Uniqueness (anitya, 無常) | RENDERED ONCE | **없음** → 4-5 | 묘사 반복 금지만, 반응 변주 원칙 없음 |
| Oscillating Imagination | DELAYED & IMPERFECT RESPONSE | **없음** → 2-6 | Analysis→스키마 필드(resurfacing). 서술형 |
| Value Pluralism (Berlin) | active_needs (단일 우선순위) | **없음** → 2-7 | Analysis→스키마 필드(value_conflict). 서술형 |
| Ethical Encounter (Lévinas) | moral_disengagement (적대만) | **없음** → 5-10 | 도덕적 각성 트리거 없음 |
| Dual Signal | DUAL SIGNAL — WHEN THE BODY DISAGREES | **구현됨** | 추가 불필요 |
| Objective Correlative | OBJECTIVE CORRELATIVE (T.S. Eliot) | **구현됨** | 추가 불필요 |
| **— NPC 관련 —** | | | |
| Psyche→Physical Manifestation | polyvagal 분석 (Theoria) | **빈약** → 5-1 | 분석은 있지만 렌더링이 소비 안 함 |
| Multi-scene NPC Consistency | render_fingerprint (PC만) | **없음** → 5-2 | NPC 행동 흔적 크로스장면 지속 없음 |
| NPC Cultural Encoding | Korean Landscape (전체 적용) | **빈약** → 5-3 | NPC별 문화 코드 자동 적용 없음 |
| Depth↔Psyche Feedback | Helena depth + self_opacity | **빈약** → 5-4 | depth 높아도 psyche 자동 변화 없음 |
| NPC Knowledge Propagation | npc_knowledge (개별) | **없음** → 5-5 | NPC 간 정보 공유 메커닉 없음 |
| Secret Leak Mechanics | leak_risk 플래그 | **빈약** → 5-6 | 플래그만, 실제 누설 트리거 없음 |
| False Belief→Conflict | false_beliefs 추적 | **빈약** → 5-7 | 추적만, 서사 긴장 유발 없음 |
| Incremental Moral Shift | desistance (4조건 전부) | **빈약** → 5-8 | 점진적 변화 없이 이진적 트리거 |
| Voice Card→Rendering | voice_card 저장 | **빈약** → 5-9 | 저장·에코만, 렌더러 적극 활용 안 함 |

> **요약**: 구현됨 7 / 삭제 8 / 유지 22건 (스키마 전환 3, 레이어 이동 1)

---

## 1. CODE — 코드 구현 (cognition.py, slot_manager.py, config.py 등)

### 1-1. Unfamiliar Discovery Y-axis 검증 ★★★

**Launeicha 원본**: Z_Synthesis — 표면 읽기 너머 3개 이상의 undercurrent를 강제. "If analysis has fewer than 3 hidden forces, dig deeper."

**Lorekeeper 매핑**:
- Theoria JSON `deep_read.undercurrents` 필드 (현재 자유 형식)
- 코드에서 undercurrents 개수 검증 가능

**구현 방안**:
```
# theoria_analyzer.py — JSON 스키마 검증부
def _validate_deep_read(dai: dict) -> dict:
    undercurrents = dai.get("deep_read", {}).get("undercurrents", [])
    if len(undercurrents) < 3:
        dai["quality_flags"] = dai.get("quality_flags", []) + ["shallow_read"]
    return dai
```

**영향**: Theoria가 얕은 분석을 내면 `shallow_read` 플래그 → iceberg에서 "더 깊이 관찰하라" 힌트 가능
**파일**: `theoria_analyzer.py` (스키마 검증), `slot_manager.py` (플래그 반응)
**토큰**: 0 (코드만)
**우선순위**: 높음 — 분석 품질의 하한선 보장

---

### 1-2. Sensory Decay / Habituation 추적 ★★★

**Launeicha 원본**: "감각은 적응한다. 동일 자극의 반복 묘사는 현실적이지 않다. 재활성화는 변이(anomaly)에서만."

**Lorekeeper 매핑**:
- `render_fingerprint`에 이미 `lighting`, `palette`, `rhythm` 존재
- 누적 히스토리가 없어서 "같은 묘사 반복" 감지 불가

**구현 방안**:
```
# cognition.py — render_fingerprint 추출 후
def _track_sensory_history(scene_continuity: dict, new_fingerprint: dict):
    history = scene_continuity.get("sensory_history", [])
    history.append({
        "turn": current_turn,
        "location": current_location,
        "palette": new_fingerprint.get("palette"),
        "lighting": new_fingerprint.get("lighting")
    })
    # 최근 5턴만 유지
    scene_continuity["sensory_history"] = history[-5:]
```

- 같은 location에서 3턴 이상 동일 palette/lighting → `sensory_habituated` 플래그
- iceberg에서 "감각 전환 필요" 또는 "미세 변화만 포착" 힌트

**파일**: `cognition.py` (히스토리 저장), `domain_manager.py` (scene_continuity 확장), `slot_manager.py` (플래그 → 힌트)
**토큰**: 0 (코드만) + iceberg 힌트 ~15토큰/발동시
**우선순위**: 높음 — 반복 묘사는 산문 품질의 가장 흔한 하락 원인

---

### 1-3. Residual Tracking (결과→원인 순환) ★★

**Launeicha 원본**: pratītyasamutpāda — "결과는 새로운 원인이 된다. 성공 안의 미해결을 추적하라."

**Lorekeeper 매핑**:
- `render_fingerprint.unresolved` 필드가 이미 존재 (Korean, 자유형)
- `narrative_chain.open_threads` (DAI)도 존재
- 하지만 **"성공한 행동의 부작용"**은 별도 추적 없음

**구현 방안**:
```
# cognition.py — _extract_batch() world_state 섹션 확장
# 기존 prompt에 추가:
# "residual_effects": "성공한 행동이 남긴 부수적 결과 (있으면)"
```

- Theoria가 이미 `observation`과 `open_threads`를 추출
- world_state에 `residual_effects` 필드 추가 (optional)
- 다음 턴 Theoria에 전달 → 분석 깊이 증가

**파일**: `cognition.py` (추출 프롬프트 확장), `theoria_analyzer.py` (입력 컨텍스트)
**토큰**: 프롬프트 ~20토큰, 추출 결과 ~30토큰/턴
**우선순위**: 중간 — open_threads와 일부 중복, 하지만 "성공의 잔여물" 관점은 새로움

---

### 1-4. Schema Refraction 플래그 ★★

> **레이어 이동**: Code→프롬프트 원칙 (Text/Analysis). 코드 플래그 대신 Theoria 프롬프트 한 줄.

**Launeicha 원본**: "나이, 지능, 경험이 어휘와 은유의 범위를 제한한다. 10세 아이는 '실존적 불안'을 모른다."

**Lorekeeper 매핑**:
- PC 데이터에 나이/직업/배경 존재 (lore에서 추출)
- 하지만 이 정보가 **서술 어휘 제한**으로 변환되지 않음

**구현 방안**:
```
# config.py 또는 cognition.py — PC 데이터 파싱 시
def build_schema_flags(pc_data: dict) -> dict:
    flags = {}
    age = pc_data.get("age")
    if age and age < 15:
        flags["vocabulary_ceiling"] = "child"
    elif age and age < 20:
        flags["vocabulary_ceiling"] = "adolescent"

    background = pc_data.get("background", "")
    if any(w in background for w in ["학자", "교수", "연구", "scholar", "professor"]):
        flags["technical_access"] = True

    return flags
```

- schema_flags → Slot 6 (PC_DATA)에 자연어로 변환하여 추가
- "이 캐릭터의 인지 범위: 청소년 수준, 기술 용어 접근 제한"

**파일**: `cognition.py` (PC 파싱), `slot_manager.py` (Slot 6 확장)
**토큰**: ~20토큰/세션 (정적, 첫 턴에 한번)
**우선순위**: 중간 — 몰입도 향상, 하지만 대부분 PC가 성인이면 효과 제한적

---

### 1-5. Causal Proportionality 신호 ★★

> **삭제**: 3축 temporal_density + ♪ dynamics가 이미 같은 역할. 코드로 재계산하면 이중 판단 + 신호 충돌.

**Launeicha 원본**: "Explicit content receives rendering density proportional to its **causal weight in the current scene** — not proportional to its **presence in character profiles**." 핵심: 프로필에 폭력적/성적 특질이 강해도, 그것이 장면의 인과적 무게와 무관하면 묘사 밀도를 올리지 않는다. **프로필 변수의 장면 오염 방지**가 본질.

**Lorekeeper 매핑**:
- DAI의 `scene_type` + `energy_direction` 이미 존재
- 3축 표기의 `temporal_density`가 유사 역할 (벌브→프리즈)
- 하지만 **"프로필 강도 ≠ 장면 밀도"**라는 명시적 경고가 없음
- 모델이 프로필에 "폭력적", "관능적" 등이 있으면 매 장면에 해당 묘사를 과잉 투입하는 경향

**구현 방안**:
```
# iceberg.py 또는 slot_manager.py
def compute_causal_weight(dai: dict) -> str:
    scene = dai.get("scene_type", "exploration")
    energy = dai.get("energy_direction", "neutral")
    position = dai.get("position", 50)

    # 높은 인과 무게: climax + rising + 극단 position
    if scene in ("climax", "crisis") or energy == "rising" and position > 70:
        return "high"  # 밀도 높은 묘사
    elif scene == "resolution" or energy == "falling":
        return "settling"  # 여운, 잔상
    else:
        return "ambient"  # 일상적 밀도
```

- causal_weight → iceberg 힌트에 반영 ("이 행동의 무게가 높다 — 디테일을 집중하라")
- temporal_density와 보완 관계 (temporal_density = 시간 속도, causal_weight = 묘사 밀도)

**파일**: `slot_manager.py` (계산 + 힌트)
**토큰**: ~15토큰/턴
**우선순위**: 중간 — 3축 표기가 이미 유사 역할, 하지만 명시적 "밀도 신호"는 가치 있음

---

### 1-6. Trait Active/Dormant 상태 ★

> **삭제**: Theoria가 이미 관련 특질만 선택, 나머지 암묵 생략.

**Launeicha 원본**: "특질은 항상 활성이 아니다. 상황이 특질을 활성화/비활성화한다."

**Lorekeeper 매핑**:
- `trait_connections` (Theoria 출력)이 이미 "어떤 특질이 이번 장면에 관련되는가" 분석
- iceberg가 `translate_trait_connections()`로 변환

**현재 상태**: 이미 **부분적으로 구현됨**. Theoria가 관련 특질만 선택 → iceberg 번역.
**추가 필요**: 명시적 "dormant" 표시 (현재는 선택 안 되면 그냥 누락)

**구현 방안**: Theoria 프롬프트에 "선택하지 않은 특질은 dormant — 이유 불필요" 명시
**토큰**: ~10토큰 (프롬프트 추가)
**우선순위**: 낮음 — 현재 구현으로 충분히 작동

---

## 2. ANALYSIS — 분석 프롬프트 (analysis_resources.py / theoria_analyzer.py)

### 2-1. Unfamiliar Discovery 프롬프트 강화 ★★★

**Launeicha 원본**: "표면 읽기를 넘어서라. 최소 3개의 숨겨진 힘을 찾아라."

**Lorekeeper 현재**:
- `OBSERVATION_INTENT` (§9): "narrative_implications"과 "hypothesis"가 있지만 **최소 개수 강제 없음**
- `deep_read` 구조: undercurrents 필드 존재하지만 검증 없음

**배치**: `OBSERVATION_INTENT` (§9, line 340) — observation 지시 뒤에 추가

**추가 텍스트** (~3줄):
```
### UNFAMILIAR DISCOVERY
Surface reading is the minimum, not the goal. For each PC action, identify at least 3 forces operating beneath the obvious interpretation — unacknowledged needs, environmental pressures, relational debts, habitual patterns, or somatic states. If you find fewer than 3, your observation is still on the surface.
The goal is not complexity for its own sake but acknowledging that human action is overdetermined — every act serves multiple masters simultaneously.
```

**파일**: `analysis_resources.py` (§9 확장)
**토큰**: ~60
**우선순위**: 높음 — 1-1 코드 검증과 함께 분석 품질 하한선 보장

---

### 2-2. Model Bias Control ★★★

**Launeicha 원본**: "모델의 학습 데이터에서 온 서사적 직감은 유효한 인과 근거가 아니다. '보통 이런 상황에서는...'이 아니라 '이 캐릭터의 이 맥락에서는...'으로."

**Lorekeeper 현재**:
- `ANALYTICAL_LENSES_LITERARY` (§E): 이론 목록은 있지만 **모델 편향 경고 없음**
- Anti-cliche (text_resources.py)가 결과물 차원에서 잡지만, 분석 차원에서는 미보호

**배치**: `THEORIA_IDENTITY_V2` (§A, line 14) — 분석자 정체성 뒤에 추가

**추가 텍스트** (~3줄):
```
### ANALYTICAL INTEGRITY
Your training data contains narrative patterns — "trauma leads to growth," "love triangles resolve toward the kindest," "villains monologue before acting." These are statistical artifacts, not causal laws. Never use narrative familiarity as evidence for what a character would do.
Ground every prediction in THIS character's established behavior, THIS world's demonstrated rules, and THIS situation's specific pressures. When uncertain, say uncertain — do not fill gaps with the most common story.
```

**파일**: `analysis_resources.py` (§A 확장)
**토큰**: ~70
**우선순위**: 높음 — 분석 편향은 모든 하위 모듈에 전파됨

---

### 2-3. Habitual Automaticity ★★

> **삭제**: 분석 소극화 위험. 2-1(undercurrents 3개 강제)과 정면 충돌.

**Launeicha 원본**: "의식적 숙고는 예외다. 기본값은 습관, 자동 반응, 학습된 패턴."

**Lorekeeper 현재**:
- `DELAYED & IMPERFECT RESPONSE` (text_resources.py): 강한 자극 → 지연/blanking 서술
- 하지만 **일상적 행동의 자동성**은 분석에서 다루지 않음
- Theoria가 모든 행동을 "의도적 선택"으로 과분석하는 경향

**배치**: `STATE_TRACKING_V2` (§8, line 292) — 상태 추적 내 행동 분석 부분

**추가 텍스트** (~2줄):
```
### HABITUAL DEFAULT
Conscious deliberation is the exception, not the rule. Most actions emerge from habit, routine, and learned automaticity. Mark when a character is genuinely deliberating vs. running on autopilot — this distinction shapes whether the action reveals character or merely maintains it.
```

**파일**: `analysis_resources.py` (§8 확장)
**토큰**: ~45
**우선순위**: 중간 — 과분석 방지, 하지만 현재도 심각한 문제는 아님

---

### 2-4. Retroactive Reason ★

> **삭제**: decision_mode + coping_mechanism + self_opacity가 이미 커버.

**Launeicha 원본**: "행동이 먼저, 이유는 나중에 만들어진다. 사후 합리화를 사후 합리화로 표시하라."

**Lorekeeper 현재**:
- `decision_mode` (Theoria 출력)가 이미 intuitive/deliberative/automatic 구분
- coping_mechanism이 방어기제 포함
- **사후 합리화 자체를 라벨링**하는 것은 없음

**배치**: `OBSERVATION_INTENT` (§9) — Unfamiliar Discovery 뒤에 추가

**추가 텍스트** (~2줄):
```
### RETROACTIVE REASONING
When a character explains their own action, evaluate: did the reason precede the act, or was it constructed afterward? Post-hoc rationalization is itself data — it reveals what the character needs to believe about themselves, which may differ from what drove the action.
```

**파일**: `analysis_resources.py` (§9 확장)
**토큰**: ~40
**우선순위**: 낮음 — decision_mode가 이미 유사 기능

---

### 2-5. Epistemological Boundary 3층 구조 ★★

> **레이어 이동**: Analysis→Code. 스키마 필드 `apprehension_gap` (서술형, null 허용).

**Launeicha 원본** (D-a): 캐릭터 인식의 3층 경계:
1. **Absence**: 파악 실패한 정보 → 인지에 존재하지 않음 (고유명사→대명사, 비밀→스키마에 부재)
2. **Approximation**: 감각 직관만 통과 → 개념 합성 없이 물리적 근사만 (정확한 숫자→X, 복장→직업 추론→O)
3. **Distortion**: 성공적으로 파악해도 주체의 방어기제/트라우마/세계관에 의해 왜곡된 '진실'로 도착

**Lorekeeper 현재**:
- `EPISTEMIC BOUNDARY` (MIRROR_WORKSHOP H): "모르면 모르는 것" — **Absence 1층만** 커버
- PERCEPTION BOUNDARY (5대 원칙): "모르는 것은 이름이 없다" — Absence + 약간의 Approximation
- **Distortion 층 없음**: 캐릭터가 정보를 "정확하게" 인식한다고 가정

**배치**: `OBSERVATION_INTENT` (§9) — 기존 observation 분석 뒤에 추가

**추가 텍스트** (~3줄):
```
### APPREHENSION LAYERS
1. Absence: Information that failed apprehension does not exist in cognition — omit or use unspecific reference.
2. Approximation: Sensory intuition passed but conceptual synthesis absent — physical approximation only (attire → occupation inference OK, exact numbers → never).
3. Distortion: Successfully apprehended data passes through the subject's defense mechanisms, traumas, and worldview. The result acquires the status of 'truth' as a distorted phenomenon. Flag distortion when schemas contaminate observation.
```

**파일**: `analysis_resources.py` (§9 확장)
**토큰**: ~70
**우선순위**: 중간 — Absence만으로도 작동하지만, Distortion 없이는 "신뢰할 수 없는 관찰자" 패턴이 불가능

---

### 2-6. Oscillating Imagination ★★

> **레이어 이동**: Analysis→Code. 스키마 필드 `resurfacing` (서술형, null 허용).

**Launeicha 원본** (D-b): "Trauma/contradictory desires = nonlinear; frustration·delay·relapse impulses randomly disrupt cognition as complex waves."

트라우마나 모순적 욕망은 선형적으로 해소되지 않는다. 좌절·지연·재발 충동이 비선형적 파동으로 인지를 교란한다.

**Lorekeeper 현재**:
- `DELAYED & IMPERFECT RESPONSE` (text_resources.py): 강한 자극 → blanking/지연 (단발)
- 하지만 **트라우마 회귀, 해결된 줄 알았던 감정의 재발, 모순적 욕망의 진동**은 분석에 없음
- Theoria가 현재 자극에 대한 반응만 분석, 과거 트라우마의 비선형 침투는 미추적

**배치**: `STATE_TRACKING_V2` (§8) — Habitual Automaticity 뒤에 추가

**추가 텍스트** (~2줄):
```
### OSCILLATING COGNITION
Trauma and contradictory desires do not resolve linearly. Frustration, delay, and relapse impulses disrupt cognition as irregular waves — a character who "moved on" three turns ago may find the unresolved emotion resurface through an unrelated trigger. Track oscillation when trauma or internal contradiction is active.
```

**파일**: `analysis_resources.py` (§8 확장)
**토큰**: ~50
**우선순위**: 중간 — 깊은 심리 서사에서 필수, 하지만 가벼운 세팅에서는 과분석 위험

---

### 2-7. Value Pluralism ★★

> **레이어 이동**: Analysis→Code. 스키마 필드 `value_conflict` (서술형, null 허용).

**Launeicha 원본** (D-c, Berlin): "Values are not a hierarchy on a single axis but a pluralistic coordinate system where each value occupies its own dimension. The center of gravity between values shifts with context."

**Lorekeeper 현재**:
- `active_needs` (Theoria): 활성 욕구 목록은 있지만 **단일 우선순위**로 처리
- NPC 자율 트리거: `henderson_need_critical`이 2+ needs 감지하지만 **경쟁**은 모델링 안 함
- "의무 vs 욕망 vs 충성" 같은 가치 충돌이 분석에서 명시적으로 다뤄지지 않음

**배치**: `OBSERVATION_INTENT` (§9) — Unfamiliar Discovery 또는 Retroactive Reason 뒤

**추가 텍스트** (~2줄):
```
### VALUE AXIS COLLISION
When a character holds multiple active values (duty, desire, loyalty, self-preservation), they do not form a hierarchy — they form a coordinate system where context shifts the center of gravity. Identify when values compete rather than align, and which value the character is sacrificing in this moment.
```

**파일**: `analysis_resources.py` (§9 확장)
**토큰**: ~45
**우선순위**: 중간 — 복잡한 NPC 의사결정에서 핵심, Want/Can/Do와 자연스럽게 연결

---

## 3. ICEBERG — 아이스버그 번역 (iceberg.py, slot_manager.py)

### 3-1. Pre-Emulation 구조 (Before Acting, Check) ★★

> **삭제**: 현재 iceberg 번역이 충분히 작동.

**Launeicha 원본**: 캐릭터 행동 전 체크리스트 — "이 행동이 이 캐릭터의 스키마/감정/신체 상태와 일치하는가?"

**Lorekeeper 매핑**:
- Theoria가 이미 분석 → iceberg 번역 → Renderer 과정에서 "분석된 상태"를 힌트로 전달
- **하지만 "행동 전 체크" 형식은 아님** — 현재는 "상태 묘사" 형식

**구현 방안**:
- iceberg 번역 시 quality_flags에 `consistency_alert` 추가
- Theoria의 `observation`과 PC의 직전 행동이 불일치하면 플래그
- Slot 16에서 "이 행동과 현재 상태 사이의 긴장" 힌트

**예시 변환**:
```
# 현재: "기력이 낮다 — 느린 움직임, 얕은 호흡"
# Pre-Emulation: "기력 낮음 → 격한 행동 시 몸이 먼저 항의한다 (떨림, 어지러움, 시야 수축)"
```

**파일**: `iceberg.py` (번역 함수 확장), `slot_manager.py` (Slot 16)
**토큰**: ~10토큰/턴 (상태-행동 불일치 시에만)
**우선순위**: 중간 — 일관성 보장, 하지만 Theoria 분석이 이미 유사 역할

---

### 3-2. ACL 특질 슬롯 확장 ★

> **삭제**: Theoria가 이미 암묵적으로 수행.

**Launeicha 원본**: "Active Character Layer — 특질, 기술, 관계가 현재 장면에서 어떻게 발현되는가"

**Lorekeeper 현재**:
- `trait_connections` → `translate_trait_connections()` (Slot 16)
- 이미 "이번 장면에서 관련된 특질" 변환 존재

**추가 필요**:
- 현재 trait → behavior 변환만 있음
- 특질 간 **충돌** (예: "용감함" vs "신중함")을 힌트로 제공하면 더 풍부

**구현 방안**:
```python
# iceberg.py — translate_trait_connections 확장
def translate_trait_connections(traits: list) -> str:
    # 기존: 각 trait → behavioral hint
    # 추가: 2개 이상 trait가 상충하면 "내적 갈등" 힌트
    if len(traits) >= 2:
        # 간단한 대립 감지 (키워드 기반)
        hint += " — 이 특질들이 같은 방향을 가리키지 않는다"
```

**파일**: `iceberg.py`
**토큰**: ~10토큰 (충돌 발생 시)
**우선순위**: 낮음 — 현재 구현으로 충분, 충돌 감지는 Theoria가 이미 암묵적으로 수행

---

### 3-3. Inline Computation Markers ★

**Launeicha 원본**: "계산 과정을 인라인으로 표시 — [COMPUTING: causal weight] → result"

**Lorekeeper 매핑**:
- Telescope (┣ ... ┫)가 이미 CoT 마커 역할
- 10 gates로 구조화됨

**현재 상태**: **이미 구현됨** (Telescope v2).
**추가 필요 없음** — Telescope이 더 정교한 구현.

---

## 4. TEXT — 텍스트 프롬프트 (text_resources.py)

### 4-1. Sensory Metaphor 제약 ★★★

**Launeicha 원본**: 두 가지 제약:
1. **Vehicle 제약**: "Metaphor vehicle = physical/sensory domain only (temperature, weight, texture, pressure, color)" — 은유의 매체가 반드시 물리적이어야 함
2. **감정 매핑 금지**: "Metaphors mapping sensation to emotion category ('heart tightened with grief') = ┣┫ material" — 감각→감정 범주 매핑은 산문이 아니라 분석 영역

**Lorekeeper 현재**:
- Camera Eye + KOREAN EMOTIONAL LANDSCAPE가 감각/한국어 제약
- Schema Refraction (1-4 코드)이 어휘 범위 제한
- **은유 매체의 물리성 제약**도, **감각→감정 매핑 차단**도 명시되지 않음

**배치**: `PROSE_CRAFT_PROTOCOL` — `OBJECTIVE CORRELATIVE` 뒤, `AFTERGLOW` 앞 (line ~525)

**추가 텍스트** (~3줄):
```
### METAPHOR VEHICLE
은유의 매체는 물리적 감각이다 — 온도, 무게, 질감, 압력, 색. 감각을 감정 범주에 매핑하는 은유('가슴이 슬픔으로 조여왔다')는 판정이지 묘사가 아니다.
은유의 출처는 장면에 뿌리를 둔다. 부엌에서는 불과 칼로, 바다에서는 조류와 소금으로. 장면 밖의 은유는 침입자다.
```

**파일**: `text_resources.py` (PROSE_CRAFT_PROTOCOL)
**토큰**: ~55
**우선순위**: 높음 — Camera Eye의 자연 확장, 두 축(매체 물리성 + 출처 접지) 모두 커버

---

### 4-2. Prose Quantification ★★

**Launeicha 원본**: "숫자를 산문에 넣지 말라. '30분'이 아니라 '커피가 식을 만큼'. 수치를 물리적 경험으로 변환하라."

**Lorekeeper 현재**:
- iceberg가 수치→행동 힌트 변환 (vigor/composure/doom)
- **하지만 시간, 거리, 온도 등의 산문 내 수치화**는 제한 없음
- 모델이 "약 200미터 떨어진 곳" 같은 정확한 수치를 산문에 쓰는 경향

**배치**: `PHYSICAL_RENDERING_DOCTRINE` — `SENSORY RENDERING` 내부 (line ~302)

**추가 텍스트** (~2줄):
```
### FELT QUANTITY
숫자는 계기판에 있다, 산문에는 없다. 거리는 걸음으로, 시간은 변화로, 온도는 피부로, 무게는 근육으로 표현한다. 캐릭터가 측정 도구를 들고 있지 않다면, 정확한 수치는 존재하지 않는다.
```

**파일**: `text_resources.py` (PHYSICAL_RENDERING_DOCTRINE)
**토큰**: ~40
**우선순위**: 높음 — Camera Eye의 자연 확장, 몰입도 직접 향상

---

### 4-3. Paragraph Rhythm ★★

**Launeicha 원본**: "문단은 호흡이다. 한 문단 = 하나의 감각 초점 또는 하나의 행동 단위."

**Lorekeeper 현재**:
- `SENTENCE RHYTHM & DENSITY`: 문장 단위 리듬은 있음
- **문단 단위 구조**는 없음

**배치**: `PROSE_CRAFT_PROTOCOL` — `SENTENCE RHYTHM & DENSITY` 뒤 (line ~515)

**추가 텍스트** (~2줄):
```
### PARAGRAPH AS BREATH
한 문단은 하나의 감각 초점 또는 하나의 행동 단위다. 초점이 이동하면 문단이 바뀐다. 짧은 문단은 속도, 긴 문단은 침잠 — 리듬은 내용이 결정한다, 균일한 길이가 결정하지 않는다.
```

**파일**: `text_resources.py` (PROSE_CRAFT_PROTOCOL)
**토큰**: ~35
**우선순위**: 중간 — 산문 구조 개선, 하지만 모델이 이미 자연스럽게 하는 영역

---

### 4-4. Sensory Decay 서술 원칙 ★★

**Launeicha 원본**: "같은 감각을 반복 서술하지 마라. 적응 후에는 변이에서만 재활성화."

**Lorekeeper 현재**:
- `AMBIENT PERSISTENCE` (PHYSICAL_RENDERING): "환경은 언급 없어도 지속"
- `RENDERED ONCE` (TEMPORAL_FLOW): "같은 것을 두 번 렌더링하지 마라"
- **하지만 "적응 후 변이에서만 재활성화"**는 명시 없음

**배치**: `TEMPORAL_FLOW_DOCTRINE` — `RENDERED ONCE` (line ~429) 확장

**추가 텍스트** (~1줄):
```
감각은 적응한다 — 동일한 환경음, 냄새, 온도는 등장 후 배경으로 녹는다. 다시 서술하려면 변이가 필요하다: 소리가 멈추거나, 냄새가 강해지거나, 새로운 감각이 기존 것을 밀어낼 때.
```

**파일**: `text_resources.py` (TEMPORAL_FLOW_DOCTRINE)
**토큰**: ~30
**우선순위**: 중간 — 1-2 코드 추적과 시너지. 코드가 플래그 → 텍스트가 원칙 제공

---

### 4-5. Uniqueness Principle (anitya, 無常) ★★

**Launeicha 원본** (A-a.6): "Even identical stimuli do not yield identical output when the temporal context has shifted. Each perceiver weaves it into subjective reality through heuristics grounded in their own schemas, memories, and states."

동일한 자극이라도 시간 맥락이 달라지면 동일한 반응이 나오지 않는다. 캐릭터는 항상 자신의 스키마·기억·상태를 통해 현실을 주관적으로 재구성한다.

**Lorekeeper 현재**:
- `RENDERED ONCE` (TEMPORAL_FLOW): 같은 **묘사**를 반복하지 마라 — 하지만 이건 서술 중복 방지
- 같은 자극에 대한 **반응 변주** 원칙은 없음
- 모델이 유사 상황에서 동일한 반응 패턴을 재생산하는 경향 (NPC가 같은 방식으로 화내기 등)

**배치**: `TEMPORAL_FLOW_DOCTRINE` — `RENDERED ONCE` 뒤 (line ~429)

**추가 텍스트** (~2줄):
```
### RESPONSE VARIANCE (無常)
동일한 자극도 시간 맥락이 다르면 다른 반응을 만든다. 어제 웃긴 농담이 오늘은 아프고, 세 번째 사과는 첫 번째와 같은 무게를 갖지 않는다. 반응의 변주를 기본값으로 — 캐릭터가 같은 패턴을 반복한다면, 그것이 의식적 선택이거나 자동화된 습관일 때만.
```

**파일**: `text_resources.py` (TEMPORAL_FLOW_DOCTRINE)
**토큰**: ~45
**우선순위**: 중간 — 장기 세션에서 반응 단조로움 방지. Habitual Automaticity(2-3)와 보완 (습관 반복 vs 변주)

---

### 4-6. Readability 원칙 ★

> **삭제**: Camera Eye + FULL SENSORIUM으로 이미 커버.

**Launeicha 원본**: "독자가 행동을 시각화할 수 있어야 한다. 추상적 묘사는 감각적 앵커 없이 서면 안 된다."

**Lorekeeper 현재**:
- `EVIDENCE, NOT VERDICT` + `FULL SENSORIUM` + Camera Eye가 이미 커버
- **추가 불필요** — 기존 원칙이 더 정교함

---

## 5. NPC — NPC 시스템 발전 (npc_manager.py, npc_autonomous.py, iceberg.py 등)

> **현재 잘 작동하는 것**: CRUD/소스 추적, 프로필 컴팩션(장면별), 자율 트리거 9종, Helena depth/tension, Peplau 태도 위상, 보이스카드 추출, iceberg 깊이 조절.
> **발전 방향**: 분석→렌더링 연결 강화, NPC 간 상호작용, 크로스장면 일관성.

---

### 5-1. Psyche→Physical Manifestation ★★★

**현재**: Theoria가 polyvagal(ventral/sympathetic/dorsal), decision_mode, coping 분석. 하지만 렌더러에 **"이 NPC는 지금 교감신경 활성 상태 → 안절부절"** 같은 물리적 변환이 없음.

**구현 방안**:
- iceberg에서 NPC psyche 번역 시 polyvagal → 물리 힌트 매핑 추가
```
ventral → 가까이, 눈맞춤, 열린 자세, 목소리 안정
sympathetic → 안절부절, 시선 분산, 움직임 증가, 목소리 빨라짐
dorsal → 멀어짐, 시선 고정/공허, 최소 움직임, 목소리 단조
```
- Slot 17 `translate_npc_attitudes()` 확장 — attitude 옆에 soma 힌트 1줄 추가

**파일**: `iceberg.py` (번역 확장)
**토큰**: ~15토큰/NPC (초점 NPC만, gaze=Full인 경우)
**우선순위**: 높음 — 분석 투자 대비 렌더링 활용률 가장 낮은 영역

---

### 5-2. Multi-scene NPC Behavioral Imprint ★★★

**현재**: psyche_states는 매 턴 Theoria가 새로 계산. 주요 사건(배신, 부상, 고백) 후에도 NPC 행동에 **흔적이 남지 않음**.

**구현 방안**:
- `domain_manager`에 `npc_imprints` 필드 추가 (per-NPC)
```python
"npc_imprints": {
    "NpcName": [
        {"event": "PC에게 배신당함", "turn": 15, "behavioral_mark": "눈 피함, 대화 짧아짐"},
        {"event": "부상 (왼팔)", "turn": 22, "behavioral_mark": "왼팔 사용 회피"}
    ]
}
```
- cognition.py에서 주요 NPC 사건 시 imprint 추출 (Flash batch 확장)
- Slot 7 NPC 프로필 또는 Slot 17에 활성 imprint 1-2개 주입

**파일**: `cognition.py` (추출), `domain_manager.py` (저장), `slot_manager.py` (주입)
**토큰**: ~20토큰/활성 NPC
**우선순위**: 높음 — NPC가 "살아있다"는 느낌의 핵심. Residual Tracking(1-3)의 NPC 버전

---

### 5-3. NPC Cultural Schema ★★

**현재**: Korean Landscape(한/정/눈치/체면)가 전체 세션에 적용. NPC별 문화 코드가 없어서 서양 캐릭터에게도 "눈치" 분석이 적용될 수 있음.

**구현 방안**:
- NPC 프로필 파싱 시 문화 힌트 추출 (배경/이름/언어 기반)
- `_extract_structured_fields()` 확장: `cultural_context` 필드
```python
"cultural_context": "korean" | "western" | "mixed" | "fantasy" | None
```
- Theoria에 NPC별 "이 NPC의 감정 어휘 범위" 힌트 제공
- Schema Refraction(1-4)과 통합 가능 — PC는 코드, NPC는 프로필에서

**파일**: `npc_manager.py` (추출), `slot_manager.py` (Slot 7 확장)
**토큰**: ~10토큰/NPC (메타 1줄)
**우선순위**: 중간 — 다문화 세팅에서만 의미, 한국 전용 세팅에서는 효과 제한

---

### 5-4. Depth↔Psyche Feedback Loop ★★

**현재**: Helena depth(0-100)와 psyche(self_opacity, coping)가 독립적. depth 80+인 NPC도 여전히 높은 self_opacity 유지 가능 — 비현실적.

**구현 방안**:
- `une_facade.py` 또는 `waterfall_pipeline.py`에서 depth tier → psyche modifier
```python
def apply_depth_feedback(npc_name, depth, psyche):
    if depth >= 70:
        # 깊은 관계: 방어 낮아짐
        psyche["self_opacity_modifier"] = "reduced — bond weakens mask"
        psyche["share_willingness"] = "high"
    elif depth >= 40:
        psyche["self_opacity_modifier"] = "selective — shows cracks"
    # depth < 20: no modifier (default guarding)
```
- modifier → Theoria 입력에 포함 → 분석이 depth에 맞게 조정
- 코드가 직접 psyche를 바꾸는 게 아니라 Theoria에 **힌트** 제공

**파일**: `une_facade.py` (modifier 생성), `theoria_analyzer.py` (컨텍스트 주입)
**토큰**: ~10토큰/NPC
**우선순위**: 중간 — 장기 관계에서 의미, 단발 세션에서는 효과 제한

---

### 5-5. NPC Knowledge Propagation ★★

**현재**: NPC-A가 아는 사실을 NPC-B가 알 수 없음. 가십/소문 메커닉 없음.

**구현 방안**:
- `npc_knowledge`에 `shareable_facts` 필드 추가
- 같은 location에 있는 NPC 간 자동 전파 규칙:
```python
def propagate_knowledge(npcs_in_scene: list, npc_knowledge: dict):
    for npc_a in npcs_in_scene:
        if npc_knowledge[npc_a].get("would_share"):
            shareable = [f for f in npc_knowledge[npc_a]["knows"]
                        if f not in npc_knowledge[npc_a].get("secrets_held", [])]
            for npc_b in npcs_in_scene:
                if npc_b != npc_a:
                    # attitude 기반 필터: hostile NPC에겐 공유 안 함
                    # 공유된 사실 → npc_b.knows에 추가 (출처 표시)
```
- 전파된 정보에 `source: "heard_from_{npc_a}"` 태그 → 신뢰도 계층

**파일**: `domain_manager.py` (전파 로직), `une_facade.py` (턴마다 호출)
**토큰**: 0 (코드만)
**우선순위**: 중간 — 3인 이상 장면에서 강력. 1:1 장면에서는 불필요

---

### 5-6. Secret Leak Trigger ★★

**현재**: `leak_risk=high`여도 실제 비밀 누설 코드 없음. 자율 트리거 `secret_pressure`가 디렉티브 텍스트만 생성.

**구현 방안**:
- `npc_autonomous.py` `_check_secret_pressure()` 확장
- leak_risk=high + tension≥60 → `leaked_hint` 디렉티브에 **구체적 비밀 조각** 포함
```python
if leak_risk == "high" and tension >= 60:
    secret = secrets_held[0]  # 가장 압력 높은 비밀
    directive = f"압력 한계 — '{secret[:20]}...'의 조각이 행동이나 말실수로 새어나온다"
```
- 전체 비밀이 아닌 **조각**(처음 20자 또는 키워드)만 누출 → 서사 긴장 유지

**파일**: `npc_autonomous.py` (트리거 확장)
**토큰**: ~15토큰 (발동 시)
**우선순위**: 중간 — 미스터리/스릴러 장르에서 핵심, 일상물에서는 빈도 낮음

---

### 5-7. False Belief→Conflict Engine ★★

**현재**: `false_beliefs` 추적하지만 서사 긴장 유발하지 않음. info_gap 트리거가 "조사하려 한다"만 생성.

**구현 방안**:
- false_belief가 PC 행동과 **직접 충돌**하면 갈등 디렉티브 생성
```python
def check_belief_collision(npc_knowledge, pc_action_summary):
    for belief in npc_knowledge.get("false_beliefs", []):
        # PC가 belief와 반대되는 행동/정보를 보여주면
        # → "NPC의 믿음이 흔들린다 — 방어하거나 의심하거나 무시한다"
```
- 결과: attitude 변동 + 새로운 open_thread 생성

**파일**: `npc_autonomous.py` (새 트리거 추가), `cognition.py` (충돌 감지)
**토큰**: ~15토큰 (발동 시)
**우선순위**: 중간 — 서사 깊이 증가, 하지만 false_beliefs 자체가 Theoria 품질에 의존

---

### 5-8. Incremental Moral Shift ★

**현재**: desistance 트리거가 4조건 전부 필요. 적대 NPC의 점진적 변화 불가능.

**구현 방안**:
- Helena `trajectory`를 활용한 점진적 태도 연화
```python
# 현재: hostile + 4조건 → 갑자기 "변화의 조짐"
# 개선: hostile + trajectory="improving" + depth≥30 →
#   단계별 연화 (적대적 발언 감소 → 중립적 관찰 → 조심스러운 협력)
```
- desistance 트리거의 조건을 **계층화**: 1조건 → 미세 변화, 2조건 → 주목할 변화, 4조건 → 전환점

**파일**: `npc_autonomous.py` (트리거 계층화)
**토큰**: 0 (디렉티브 텍스트만 변경)
**우선순위**: 낮음 — 장기 캠페인에서만 의미. 단발 세션에서는 hostile NPC가 남아있는 게 자연스러움

---

### 5-9. Voice Card Active Rendering ★

**현재**: voice_card가 Slot 24 recency에 에코되지만, 렌더러가 "이 NPC의 말투를 이렇게 하라"로 적극 활용하지 않음.

**구현 방안**:
- 이미 dialogue_directive가 Slot 17에서 대사 지시 생성
- voice_card의 Quirks/Shifts/Catch를 dialogue_directive와 **합성**
```python
# iceberg.py — compose_dialogue_directives 확장
def compose_dialogue_directives(npc_name, dialogue_dir, voice_card):
    # dialogue_dir: 동적 (이번 턴의 목적/전략/숨김/드러냄)
    # voice_card: 정적 (톤/버릇/전환/캐치프레이즈)
    # 합성: "이번 턴의 전략을 이 NPC의 말투로 실행하라"
```
- gaze=Full인 NPC만 적용 (배경 NPC는 불필요)

**파일**: `iceberg.py` (합성 로직)
**토큰**: ~10토큰/초점 NPC (기존 voice_card + dialogue_directive 병합, 순증 적음)
**우선순위**: 낮음 — 현재 Slot 24 에코 + Slot 33 보이스카드로 이미 작동. 합성은 정밀도 향상이지 필수 아님

---

### 5-10. Ethical Encounter Trigger (Lévinas) ★★

**Launeicha 원본** (D-c): "Confrontation with the Other's vulnerability (pain, helplessness) can generate a pre-rational ethical call before Want/Can/Do computation. When this call collides with existing cognitive dynamics, which is foregrounded is determined by initial conditions, context, and schema."

타자의 취약성(고통, 무력함)에 직면하면 Want/Can/Do **이전에** 전-이성적 윤리적 호출이 발생할 수 있다.

**현재**: `moral_disengagement` 트리거는 적대 NPC의 **도덕적 둔감화**만 추적 — 반대 방향인 **도덕적 각성**은 없음. NPC가 타자의 고통을 목격했을 때 적대적 행동을 멈추거나 주저하는 메커닉이 부재.

**구현 방안**:
- `npc_autonomous.py`에 새 트리거 `ethical_arrest` 추가
```python
def _check_ethical_arrest(self, npc_name, psyche, npc_knowledge, scene_context):
    # 조건: NPC가 적대적이거나 무관심 + 타자의 취약성이 장면에 노출
    # 결과: "행동이 멈춘다 — 의무도 욕망도 아닌, 더 원초적인 것이 끼어들었다"
    if attitude in ("hostile", "unfriendly") and scene_has_vulnerability:
        return TriggerResult("ethical_arrest", npc_name,
            "타자의 고통이 보였다 — 행동이 멈추거나, 외면하거나, 더 잔인해진다",
            priority=3)
```
- moral_disengagement의 **정반대 쌍** — 둘 중 어느 쪽이 우세한지는 NPC 초기조건(depth, trajectory)이 결정
- desistance(5-8)보다 즉각적: desistance=장기 변화, ethical_arrest=순간적 멈춤

**파일**: `npc_autonomous.py` (트리거 추가)
**토큰**: ~15토큰 (발동 시)
**우선순위**: 중간 — 도덕적 복잡성의 핵심, 하지만 "취약성 노출" 감지가 Theoria 품질에 의존

---

## 구현 우선순위 매트릭스 (수정 후)

| # | 항목 | 레이어 | 난이도 | 가치 | 우선순위 |
|---|---|---|---|---|---|
| 2-2 | Model Bias Control | Analysis | 낮음 | ★★★ | **P0** |
| 2-1 | Unfamiliar Discovery | Analysis | 낮음 | ★★★ | **P0** |
| 4-1 | Sensory Metaphor Vehicle | Text | 낮음 | ★★★ | **P0** |
| 4-2 | Prose Quantification | Text | 낮음 | ★★★ | **P0** |
| 1-1 | Y-axis 검증 | Code | 중간 | ★★★ | **P1** |
| 1-2 | Sensory Decay 추적 | Code | 중간 | ★★★ | **P1** |
| 5-1 | Psyche→Physical | NPC/Iceberg | 낮음 | ★★★ | **P1** |
| 5-2 | NPC Behavioral Imprint | NPC/Code | 중간 | ★★★ | **P1** |
| 4-4 | Sensory Decay 원칙 | Text | 낮음 | ★★ | **P1** |
| 4-3 | Paragraph Rhythm | Text | 낮음 | ★★ | **P1** |
| 4-5 | Uniqueness (anitya) | Text | 낮음 | ★★ | **P1** |
| 2-5 | Epistemological 3층 (스키마) | Code | 중간 | ★★ | **P1** |
| 2-6 | Oscillating Imagination (스키마) | Code | 중간 | ★★ | **P2** |
| 2-7 | Value Pluralism (스키마) | Code | 중간 | ★★ | **P2** |
| 1-3 | Residual Tracking | Code | 중간 | ★★ | **P2** |
| 1-4 | Schema Refraction (프롬프트) | Text | 낮음 | ★★ | **P2** |
| 5-4 | Depth↔Psyche Feedback | NPC/Code | 중간 | ★★ | **P2** |
| 5-5 | Knowledge Propagation | NPC/Code | 중간 | ★★ | **P2** |
| 5-6 | Secret Leak Trigger | NPC/Code | 낮음 | ★★ | **P2** |
| 5-7 | False Belief→Conflict | NPC/Code | 중간 | ★★ | **P2** |
| 5-10 | Ethical Encounter | NPC/Code | 중간 | ★★ | **P2** |
| 5-3 | NPC Cultural Schema | NPC/Code | 중간 | ★★ | **P3** |
| 5-8 | Incremental Moral Shift | NPC/Code | 낮음 | ★ | **P3** |
| 5-9 | Voice Card Active | NPC/Iceberg | 낮음 | ★ | **P3** |

### 우선순위 그룹 (수정 후)

**P0 (즉시 — 프롬프트만)**: 2-2, 2-1, 4-1, 4-2 → 토큰 +~225, 코드 변경 0
**P1 (단기 — 프롬프트 + 코드)**: 1-1, 1-2, 5-1, 5-2, 4-4, 4-3, 4-5, 2-5 → 토큰 +~205, 코드 ~130줄
**P2 (중기)**: 2-6, 2-7, 1-3, 1-4, 5-4, 5-5, 5-6, 5-7, 5-10 → 토큰 +~120, 코드 ~200줄
**P3 (장기/선택)**: 5-3, 5-8, 5-9 → 토큰 +~30, 코드 ~60줄

---

## 기존 시스템과의 충돌 검증

| 신규 개념 | 기존 시스템 | 관계 |
|---|---|---|
| Unfamiliar Discovery | deep_read.undercurrents | **강화** — 최소 기준 추가 |
| Model Bias Control | Anti-cliche (text) | **보완** — 분석 vs 결과물 |
| Sensory Decay | RENDERED ONCE + AMBIENT PERSISTENCE | **확장** — 재활성화 조건 명시 |
| Schema Refraction | Camera Eye + Korean Landscape | **확장** — 캐릭터별 어휘 범위 |
| Prose Quantification | iceberg 수치→행동 번역 | **확장** — 시간/거리/온도까지 |
| Habitual Automaticity | decision_mode (intuitive/deliberative) | **보완** — 분석 기본값 제시 |
| Causal Proportionality | 3축 temporal_density | **보완** — 묘사 밀도 vs 시간 속도 |
| Metaphor Vehicle | Camera Eye | **확장** — 은유 매체 물리성 + 출처 접지 |
| Psyche→Physical | polyvagal 분석 + iceberg 번역 | **연결** — 분석→렌더링 파이프라인 완성 |
| NPC Behavioral Imprint | render_fingerprint (PC) + open_threads | **확장** — NPC에도 크로스장면 흔적 |
| NPC Cultural Schema | Korean Landscape + Schema Refraction | **세분화** — 전체→NPC별 문화 코드 |
| Depth↔Psyche Feedback | Helena depth + Theoria psyche | **연결** — 독립적 시스템 간 피드백 루프 |
| Knowledge Propagation | npc_knowledge (개별) | **확장** — 개별→네트워크 |
| Secret Leak Trigger | secret_pressure 트리거 | **강화** — 디렉티브만→구체적 조각 포함 |
| False Belief→Conflict | false_beliefs + info_gap 트리거 | **강화** — 추적만→갈등 유발 |
| Incremental Moral Shift | desistance 트리거 | **확장** — 이진→계층적 |
| Voice Card Active | voice_card + dialogue_directive | **합성** — 정적+동적 병합 |
| Epistemological 3층 | EPISTEMIC BOUNDARY (1층) | **확장** — Absence→Approximation→Distortion 추가 |
| Uniqueness (anitya) | RENDERED ONCE | **보완** — 묘사 중복 방지 ≠ 반응 변주. 별도 축 |
| Oscillating Imagination | DELAYED & IMPERFECT RESPONSE | **확장** — 단발 지연→비선형 회귀/재발 |
| Value Pluralism | active_needs + henderson 트리거 | **확장** — 단일 우선순위→다축 경쟁 |
| Ethical Encounter | moral_disengagement (적대만) | **보완** — 둔감화의 정반대 쌍 (각성) |

**충돌 0건** — 모든 항목이 기존 시스템의 확장, 보완, 또는 연결.

---

## 총 토큰 영향 (수정 후)

| 그룹 | 프롬프트 토큰 | 코드 변경 |
|---|---|---|
| P0 | +~225 | 0 |
| P1 | +~205 | ~130줄 |
| P2 | +~120 | ~200줄 |
| P3 | +~30 | ~60줄 |
| **전체** | **+~580** | **~390줄** |

> 원본 30건→22건 (8건 삭제, 4건 레이어 이동). 토큰 110 절감, 코드 90줄 감소.
