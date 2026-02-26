# Misel × Lorekeeper Comparative Analysis

**작성**: 2026-02-27
**대상**: SillyTavern Misel 프롬프트 vs Lorekeeper Bot 아키텍처
**범위**: 18개 주요 개념 비교 + 아키텍처 차이 분석

---

## I. 아키텍처 차이 (Architectural Gap)

### Misel: 클라이언트 단일 모델 기반
- **배포**: SillyTavern 프롬프트 (사용자 머신 로컬)
- **모델 구조**: 단일 LLM (토글 기반 조건부 실행)
- **커스터마이징**: `{{if_pure}}` 조건부 — 메모리 모드/POV 토글
- **제어 수준**: 프롬프트 텍스트 명령에 100% 의존
- **상태 관리**: 사용자 채팅 히스토리 (데이터 구조화 없음)

### Lorekeeper: 서버 기반 Dual-Brain 파이프라인
- **배포**: Discord 봇 (Python 백엔드)
- **모델 구조**:
  - **Theoria** (Flash): 분석 전담 (DAI 추출, 심리상태 분해)
  - **Renderer** (Main): 서사 생성 (34-slot 프롬프트)
- **커스터마이징**:
  - 자동 장르 감지 (3계층: Stage/Flavor/Lens)
  - 코드 기반 mechanic_profile 생성 (결정론적)
- **제어 수준**:
  - 프롬프트 (34-slot system with Primacy/Recency zones)
  - 코드 파이프라인 (waterfall, autonomy triggers, batch extraction)
- **상태 관리**: 구조화된 JSON (ai_memory, world_state, npc_profiles, scene_continuity)

**핵심 차이**: Misel은 prompt-only, Lorekeeper는 **prompt + code enforcement**

---

## II. 18개 주요 개념 비교표

### A. 완전히 구현된 개념 (Fully Implemented in Lorekeeper)

| # | Misel 개념 | Lorekeeper 구현체 | 설명 | 차이점 |
|---|-----------|------------------|------|--------|
| 1 | Documentary Camera | MIRROR_WORKSHOP (Slot 3) + Camera Eye | 감정 없는 객관적 묘사 | Misel은 절대적 금지; Lorekeeper는 제어된 문학적 장치 허용 |
| 2 | Epistemic Filter / POV-Bound | Perception Boundary (5대 원칙) | 알지 못하는 것은 이름 없다 | Lorekeeper: TRPG 3인칭 한정 (고정); Misel: 토글 가능 |
| 3 | Negation-Redefinition Ban | §9 CONSEQUENCE RENDERING (Slot 20) | 결과를 무효화하는 해석 금지 | 동일 원리, 텍스트 배치만 다름 |
| 4 | Profile-to-Narrative Translation | Theoria→Iceberg→Prompt 파이프라인 | 캐릭터 시트 데이터 직접 노출 금지 | Lorekeeper: 기계적 추출 (코드), Misel: 프롬프트 명령 |
| 5 | Anti-Repetition Protocol | DOA in Telescope (Gate 9) + ANTI_CLICHE (Slot 22) | 죽은 구문/표현 추적 | Lorekeeper: 코드 레벨 강제, Misel: 프롬프트 요청 |
| 6 | Proof Through Causality | Off-Screen Persistence + 5대 원칙 | "세계는 단순히 이런 상태다" | 동일 철학, 실장 전략만 다름 |
| 7 | Information Boundaries (Fog of War) | NPC Knowledge (Theoria schema) + secrets, would_share | 정보의 비대칭성 | Lorekeeper: 9-trigger 자율성 시스템, Misel: 명령형 |
| 8 | Sheet Data Forbidden in Prose | _strip_framework_terms() + Iceberg 번역 | 학술 용어/프레임워크 제거 | Lorekeeper: 자동 정규표현식, Misel: 프롬프트 지시 |
| 9 | Character Agency & Autonomy | NPC Autonomous (9 trigger types) | NPC 독립 행동 생성 | Lorekeeper: 코드 기반 트리거, Misel: 프롬프트 권장사항 |
| 10 | Physical Rendering | PHYSICAL_RENDERING_DOCTRINE (Slot 4) | 미시 동작·촉각·중력 추적 | 동일 개념, Lorekeeper이 더 상세 구조화 |
| 11 | Anti-Cliché Protocol | ANTI_CLICHE + PROSE_CRAFT (Slots 23-24) | 장르 전형 회피 | Lorekeeper: 장르 적응형, Misel: 고정 규칙 |
| 12 | Desire Stratification | Dialogue Directive 4축 (Purpose/Strategy/Hidden/Revealed) | 코어욕망 vs 표현욕망 분화 | Lorekeeper: 대사 지시로 운영, Misel: 캐릭터 분석 지침 |

### B. 부분 겹침 — Lorekeeper 채택 가능 개념 (Potential Integration)

| # | Misel 개념 | Lorekeeper 현황 | 채택 방안 | 토큰 비용 | 우선순위 |
|---|-----------|-----------------|----------|----------|---------|
| 13 | **Rendering Gate** | DOA (죽은 구문만). 이미 렌더링된 **캐릭터 특질**은 미추적 | Telescope [Craft.Rendering] 확장 또는 별도 Gate | ~20 | MED |
| 14 | **Dialogue ≠ Narrator Compensation** | 불명시 규칙 (암묵적) | PC_AUTONOMY 또는 Slot 17 대사 지시에 1줄 추가 | ~15 | **HIGH** |
| 15 | **Scene-Ending Rules** | 없음 | MIRROR_WORKSHOP (Slot 3) 또는 Slot 25 CRITICAL에 2-3줄 | ~30 | **MED-HIGH** |
| 16 | **Physical Positional Checklist** | PHYSICAL_RENDERING_DOCTRINE 있음 (비구조화) | 6-point 체크리스트 압축 추가 | ~25 | MED |

**상세 분석**:

#### 13) Rendering Gate — 이미 렌더링된 특질 추적

**Misel 정의**:
```
"Has this pattern already been rendered in a prior response?
→ SKIP. After first rendering, patterns become invisible architecture."
```

**Lorekeeper 현황**:
- Telescope Gate 9에서 DOA(Dead Phrase Occurrence)만 추적 — 문구 반복 방지
- **누락**: 캐릭터 특질(웃음, 제스처, 음성 톤) 재현 추적

**채택 시나리오**:
```
Turn 1: Elara "laughs softly" (← 첫 렌더링)
Turn 2-5: "laughs softly" 없음 (사라진 건축학)
Turn 6: 새 상황에서 "laughs softly" 가능 (심리 변화면 OK)
```

**구현 위치**: Telescope Gate 추가 또는 Cognition batch에 pattern_reuse 필드
- **가치**: 중간 (캐릭터 굳어짐 방지, 하지만 DOA로 대부분 커버됨)
- **우선순위**: MED (다른 개선이 먼저)

---

#### 14) Dialogue ≠ Narrator Compensation (HIGH 우선순위)

**Misel 규칙**:
```
"If information cannot be conveyed through narration,
it does not migrate to dialogue. Dialogue must never
compensate for narrator restrictions."
```

**Lorekeeper 현황**:
- Slot 17 (Dialogue Directive)에 4축 있음 (Purpose/Strategy/Hidden/Revealed)
- **하지만**: 대사로 "서술 불가능한 정보"를 대체하는 금지 조항 없음
- **문제**: PC 행동이 불명확하면 NPC 대사로 "너 뭐하는 거야?"로 명확화하는 경향

**채택 시나리오**:
```
❌ 나레이터 제약:
"Elara는 (PC 의도를 모르므로) 뭔가 일어나는 건 알지만"

❌ 대사로 보상 시도:
Elara: "무슨 일이야? 뭐하고 있어?"

✓ 대사 제약:
Elara는 관찰할 수 있는 것만 언급 → 침묵, 관찰, 추측만
```

**구현 위치**: PC_AUTONOMY (Slot 2) 1-2줄 또는 Dialogue Directive (Slot 17) 추가
```python
"NPC 대사는 자신이 감각한 것만 참조한다.
나레이션에서 불가능한 정보는 대사에서도 불가능하다."
```

**가치**: **높음** (동일 원칙이 더 명확해짐, TRPG 몰입감↑)
- **우선순위**: **HIGH** (1–2주일 내 추가)

---

#### 15) Scene-Ending Rules (MED-HIGH 우선순위)

**Misel 규칙**:
```
"End scenes with 2+ characters in shared space.
Flight/avoidance = mid-scene tension, NOT completed action.
Cut BEFORE the fleeing character exits."
```

**Lorekeeper 현황**:
- MIRROR_WORKSHOP (Slot 3)에 "카메라는 공간을 따라간다" 있음
- **누락**: 장면 종료 시점의 명시적 규칙
- **현상**: 갈등 → NPC 도망치기 → "장면 끝" (조기 해결)

**채택 시나리오**:
```
✗ 현재:
[NPC 분노] → [PC 말 막음] → [NPC 입장 떠남] = 장면 종료

✓ Misel 원칙:
[NPC 분노] → [PC 말 막음] →
"NPC가 돌아서면서 입장 바깥을 향해 움직인다.
하지만 아직 떠나지 않았다. 문 손잡이에 손을 올렸다."
(← 장면은 계속. 관찰적 묘사, 대사 기회 남음)
```

**구현 위치**: MIRROR_WORKSHOP (Slot 3) 또는 새로운 Slot 25 CRITICAL 조항
```python
"""
### Scene Termination
- 캐릭터 퇴장은 장면 종료가 아니다.
- 퇴장 *행위* 종료 직전에 컷. (문을 여는 순간 전에)
- 공유 공간에 2인 이상 있을 때만 종료 타당.
"""
```

**가치**: **높음** (구조적 긴장 유지, 플레이어 개입 시간↑)
- **우선순위**: **MED-HIGH** (3–4주일 내)

---

#### 16) Physical Positional Checklist (MED 우선순위)

**Misel 6-Point Checklist**:
1. **Position**: 몸의 위치 (서, 앉음, 누움)
2. **Perception**: 그 위치에서 보이는 것
3. **Gravity**: 중력 작용 (안정/불안정)
4. **Anatomy**: 생리학적 제약 (손 위치, 호흡)
5. **Motivation**: 위치 선택의 이유
6. **Process**: 위치 변화 과정

**Lorekeeper 현황**:
- PHYSICAL_RENDERING_DOCTRINE (Slot 4): 미시동작·생리·감각
- **형식**: 자유로운 문단 지침
- **부족**: 장면별 체크리스트 구조화

**채택 시나리오**:
```
현재 (자유형):
"물리적 렌더링은 동작을 통해 감정을 전달한다..."

개선 (체크리스트):
□ 위치: 의자 가장자리, 등은 기대지 않음
□ 인식: 테이블 모서리 유리잔 (손 닿을 거리)
□ 중력: 불안정 (언제든 일어날 수 있음)
□ 해부: 손은 쥐었고, 발은 바닥에 불완전 착지
□ 이유: 대화 도중 떠날 준비
□ 과정: 천천히 일어나기, 또는 곧 앉기
```

**구현 위치**: PHYSICAL_RENDERING_DOCTRINE (Slot 4) 기존 텍스트 아래 부표로 추가
```python
### Physical Rendering — 6-Point Checklist
위치 → 인식 범위 → 중력상태 → 해부학적 제약 → 심리 기초 → 동작 과정
```

**가치**: 중간 (구조화로 일관성↑, 하지만 현재 시스템도 충분히 상세함)
- **우선순위**: MED (4–6주일 이후)

---

### C. Lorekeeper에 비적용 개념 (Not Applicable)

| # | Misel 개념 | 비적용 이유 |
|---|-----------|-----------|
| 17 | **Processing Protocol** (Anti-Sparse-Attention) | Lorekeeper는 34-slot system으로 읽기 순서 아키텍처 기반 구성 (프롬프트 명령 불필요) |
| 18 | **Toggle System** (POV, Gore, Length) | 클라이언트 SillyTavern 기능. 서버 봇으로는 !set 커맨드로 충분함 |

---

## III. 고유 Misel 개념 (Lorekeeper 미구현)

### 1. Memory Degradation Principle

**Misel 원칙**:
```
Recent (1-5 turns) = 정확한 세부
Mid-range (5-20 turns) = 흐릿한 윤곽
Distant (20+ turns) = 단편적, 재구성됨
```

**Lorekeeper 현황**:
- Fermentation v4: arc_observations (turn 10+)
- **미구현**: 명시적 기억 퇴화 메커니즘

**평가**:
- 흥미로운 개념이지만 구현 복잡도 높음
- TRPG 세션은 보통 5–20 턴 범위 (퇴화 효과 약함)
- **선택사항**: 장기 캠페인 모드에서만 고려

---

### 2. Rendering Benchmark Example

**Misel 접근**:
```
프롬프트에 "목표 밀도" 예시 문단 포함
사용자가 시각적으로 비교 가능
```

**Lorekeeper 현황**:
- text_resources.py에 지침 텍스트만 (예시 없음)
- Token 비용: 예시 1단락 = ~80 tokens

**평가**:
- **가치**: 낮음 (Lorekeeper는 자동 장르 감지이므로 예시 일반화 어려움)
- **비용**: 높음
- **선택**: 불필요

---

### 3. Zero Figurative Language Ban

**Misel 규칙**:
```
모든 은유/비유/의인법 금지
"마음이 무거워졌다" ✗ → "숨이 얕아졌다" ✓
```

**Lorekeeper 철학**:
- 장르 적응형 (로맨스, 판타지는 문학적 장치 요구)
- 제어된 사용만 가능

**평가**:
- **차이**: 절대적 vs 조건적
- Misel은 "하드보일드 현실주의", Lorekeeper는 "장르 유연성"
- **Lorekeeper 선택이 맞음**: TRPG 다중장르 요구사항 맞춤

---

## IV. 핵심 철학 차이 (Philosophical Gap)

### Misel
- **목표**: 절대적 현실주의 (제로 해석)
- **대상**: 개인 창작 워크숍
- **제어**: 프롬프트 텍스트 (100%)
- **유연성**: 낮음 (절대 규칙)
- **아키텍처**: 단일 모델 + 조건부

### Lorekeeper
- **목표**: 장르 적응형 서사 생성
- **대상**: TRPG 세션 디렉터/보조 AI
- **제어**: 프롬프트 (60%) + 코드 (40%)
- **유연성**: 높음 (기계적 자동조정)
- **아키텍처**: Dual-Brain (분석 + 생성) + Waterfall Pipeline

**결론**: 철학은 다르지만 **상충하지 않음**. Misel 개념 중 4-5개를 코드 레벨에서 강화하면 Lorekeeper의 품질이 향상됨.

---

## V. 통합 권장사항 (Integration Recommendations)

### Phase 1: 즉시 (1–2주)
| # | 항목 | 위치 | 한국어 텍스트 | 토큰 |
|---|------|------|------------|------|
| 14 | Dialogue ≠ Narrator Compensation | Slot 2 (PC_AUTONOMY) 또는 Slot 17 (Dialogue Directive) | "NPC 대사는 자신이 감각한 정보만 참조한다. 서술 불가능한 것을 대사로 설명하지 않는다." | ~20 |

### Phase 2: 단기 (3–4주)
| # | 항목 | 위치 | 구현 형식 | 토큰 |
|---|------|------|---------|------|
| 15 | Scene-Ending Rules | Slot 3 (MIRROR_WORKSHOP) 또는 Slot 25 | 한 줄 지침: "퇴장은 행위 종료 직전에 컷. 공유 공간에 인물 2인 이상 남을 것." | ~25 |

### Phase 3: 중기 (1–2개월)
| # | 항목 | 위치 | 구현 형식 | 토큰 |
|---|------|------|---------|------|
| 16 | Physical Positional Checklist | Slot 4 (PHYSICAL_RENDERING_DOCTRINE) | 부표 추가: "위치→인식→중력→해부→동기→과정" | ~30 |
| 13 | Rendering Gate (Trait Re-performance) | Telescope Gate 확장 또는 Cognition 배치 | [Craft.Rendering] 논리 | ~20 |

**총 추가 토큰**: ~95 tokens across 4 locations
**아키텍처 변경 필요**: 없음 (모두 additive)
**API 콜 증가**: 0

---

## VI. 구현 세부사항 (Implementation Sketch)

### 14) PC_AUTONOMY (Slot 2) 1줄 추가

**변경 전**:
```
### 2. AI IDENTITY (PC 자율성 교리)
[기존 텍스트 ~200 tokens]
```

**변경 후**:
```
### 2. AI IDENTITY (PC 자율성 교리)
[기존 텍스트 ~200 tokens]

**대사의 한계**: NPC 대사는 자신이 감각한 정보에만 기초한다.
나레이션에서 불가능한 정보(PC 내심, 불가시 행동)를 대사로 설명하지 않는다.
```

**코드 변경**: slot_manager.py에 slot 2 텍스트만 갱신 (로직 변경 없음)

---

### 15) MIRROR_WORKSHOP (Slot 3) 또는 Slot 25 CRITICAL

**삽입 위치**: MIRROR_WORKSHOP 끝부분 또는 새 subsection

```
### Scene Termination Protocol
캐릭터 퇴장은 장면 종료가 아니다.
- 물리적 계기: 문을 여는 순간 *전에* 컷
- 사회적 조건: 공유 공간에 인물 2인 이상 남아있을 것
- 심리적 기저: 긴장/미결정상태 유지 (도망=진행 중 사건, 완료 아님)

예: "발을 들려는 움직임. 손이 문 손잡이에 향한다."
(← 실제 퇴장 행위 전에 중단)
```

**코드 변경**: text_resources.py slot 3 또는 25 상수 갱신

---

### 16) PHYSICAL_RENDERING_DOCTRINE (Slot 4) 부표

**기존 구조**:
```python
PHYSICAL_RENDERING_DOCTRINE = """
미세한 동작으로 심리를 드러낸다...
"""
```

**개선**:
```python
PHYSICAL_RENDERING_DOCTRINE = """
[기존 텍스트]

### 위치 체계 (6-Point Checklist)
1. 위치(Position): 몸의 공간 좌표
2. 인식(Perception): 그 위치에서 보이는 것
3. 중력(Gravity): 안정성/긴장도
4. 해부(Anatomy): 생리학적 제약, 근육 상태
5. 동기(Motivation): 위치 선택의 심리
6. 과정(Process): 위치 변화의 시간

각 요소가 유기적으로 연결될 때,
행동은 투명한 심리 X선이 된다.
"""
```

**코드 변경**: text_resources.py PHYSICAL_RENDERING_DOCTRINE 갱신

---

### 13) Rendering Gate (선택적, 2개월 이후)

**Telescope Gate 9 (DOA) 확장**:

현재:
```python
# Gate 9: DOA (Dead Phrase Occurrence)
dead_phrases = extract_dead_phrases(prev_response)
if phrase in current_draft:
    flag("[DOA] " + phrase)
```

개선 안:
```python
# Gate 9A: Phrase-level DOA
# [기존]

# Gate 9B: Trait-level DOA (Rendering Gate)
rendered_traits = extract_character_traits(prev_response)
# e.g., ["laughs", "sighs", "fidgets"]
current_traits = extract_character_traits(current_draft)
overlap = rendered_traits ∩ current_traits

if overlap and not scene_type_changed:
    flag("[Rendering] " + str(overlap))
    # 제안: 이 특질은 이미 렌더링됨. 다른 특질로 변경 검토.
```

**구현**: cognition.py 또는 telescope.py 추가 함수

---

## VII. 기존 Lorekeeper 시스템과의 일관성 검증

### ✓ Primacy/Recency 최적화 유지
- Slot 2 추가 (PC_AUTONOMY): Primacy zone (유지 ✓)
- Slot 3 추가 (MIRROR_WORKSHOP): Primacy zone (유지 ✓)
- Slot 4 갱신 (PHYSICAL_RENDERING): Primacy zone (유지 ✓)
- Slot 25 옵션 (CRITICAL): Recency zone (유지 ✓)

### ✓ 34-Slot 구조 미변경
- 총 슬롯 수: 34 (변경 없음)
- 토큰 증분: ~95 tokens (전체 프롬프트의 ~2%, 크리티컬 정보이므로 추가 가치 충분)

### ✓ 코드 파이프라인 미영향
- waterfall, cognition, npc_autonomous, telescope: 로직 변경 없음
- text_resources.py만 상수 갱신

### ✓ 장르 적응성 유지
- 14번 (대사 제약): 모든 장르에 적용 ✓
- 15번 (장면 종료): 모든 장르에 적용 ✓
- 16번 (위치 체크리스트): 장르무관 기초 기술 ✓

---

## VIII. 예상 효과 (Expected Impact)

### 정량적 (Quantitative)
- **새 토큰**: +95 in primacy/recency zones (크리티컬 정보)
- **API 콜**: 0 증가
- **처리 시간**: 0 ms 증가 (text generation 내재)

### 정성적 (Qualitative)

#### 14) Dialogue ≠ Narrator Compensation
- **개선**: NPC 대사가 PC의 불명확한 의도를 "질문"으로 대체하지 않음
- **영향**: TRPG 플레이어 자율성 ↑↑
- **위험**: 없음 (제약 추가만)

#### 15) Scene-Ending Rules
- **개선**: 갈등이 도망으로 조기 종료되지 않음
- **영향**: 플레이 시간 및 긴장 지속력 ↑
- **위험**: "장면 종료 타이밍"을 플레이어가 결정하지 못할 수 있음 → 반대로 플레이어 자율성 하락?
  - **완화책**: "공유 공간 2인 조건" 명시로 명확한 경계 제공

#### 16) Physical Positional Checklist
- **개선**: 미세동작 일관성 ↑
- **영향**: 캐릭터 구체성 ↑, 묘사 밀도는 변화 없음
- **위험**: 없음

#### 13) Rendering Gate (Trait)
- **개선**: 캐릭터 특질이 굳어지지 않음
- **영향**: 캐릭터 진화감 ↑↑
- **위험**: False positive (같은 감정 상황은 같은 제스처가 자연스러움)
  - **완화책**: DOA가 아니라 "경고"로만 제시

---

## IX. 최종 결론

### Misel ↔ Lorekeeper 호환성

**공존 가능한 개념**: 14, 15, 16, 13 (모두 기존 시스템 호환)
**적용 불가 이유**: 17 (아키텍처 선택), 18 (클라이언트-서버 환경)
**철학적 차이**: 있으나 상충하지 않음 (추상화 수준 다름)

### 추천 우선순위

| 순위 | 항목 | 난도 | 가치 | 일정 |
|------|------|------|------|------|
| 1 | **14: Dialogue 제약** | 낮음 | 높음 | 1주 |
| 2 | **15: Scene-Ending Rules** | 낮음 | 중상 | 3주 |
| 3 | **16: Physical Checklist** | 낮음 | 중간 | 4주 |
| 4 | 13: Rendering Gate | 중간 | 중간 | 8주 |

### 전체 추정 시간
- 구현: 4–5시간 (모두 text_resources.py + 선택적 cognition 확장)
- 테스트: 2–3시간 (프롬프트 점진적 검증)
- **총**: 1–2개월 (다른 업무와 병행 가능)

### 결론 문장

**Misel의 18개 개념 중 12개는 이미 Lorekeeper에 구현되어 있다.**
4개 개념(Dialogue 제약, Scene-Ending, Physical Checklist, Rendering Gate)은
작은 텍스트 추가로 Lorekeeper의 품질을 단계적으로 개선할 수 있다.
2개 개념(Processing Protocol, Toggle System)은
아키텍처 차이로 비적용이다.
결과적으로 두 시스템은 **상충하지 않으며 상호보완적이다.**

---

**문서 버전**: 1.0
**최종 검토**: 2026-02-27
**다음 리뷰**: Phase 1 구현 후 (2026-03-15 예정)
