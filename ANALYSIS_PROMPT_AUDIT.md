# 프롬프트 시스템 정합성 & 철학 정렬 감사 리포트

**일자**: 2026-02-19
**범위**: text_resources.py, analysis_resources.py, slot_manager.py, theory_emphasis_engine.py
**방법**: 3개 탐색 에이전트 병렬 분석 → 종합

---

## 목차

1. [설계 철학 진단: 模寫 vs 서사주의](#1-설계-철학-진단)
2. [프롬프트 지시 충돌 목록 (27건)](#2-프롬프트-지시-충돌-목록)
3. [좌뇌-우뇌 간 철학 불일치](#3-좌뇌-우뇌-간-철학-불일치)
4. [슬롯 레이아웃 충돌 & 우선순위 흐름](#4-슬롯-레이아웃-충돌)
5. [시스템 정체성 판정: 하이브리드 선언](#5-시스템-정체성-판정)
6. [수정 권고안](#6-수정-권고안)

---

## 1. 설계 철학 진단

### 1.1 사용자 설계 이념 (추론)

> "사람을 분석하여 렌더링하고 그것을 시뮬레이션하는것"

이것은 3단계 파이프라인이다:

```
[분석] 인간 심리/행동의 관찰적 데이터 추출
   ↓
[렌더링] 데이터를 산문/장면으로 물질화
   ↓
[시뮬레이션] 렌더링된 세계가 자체 법칙으로 작동
```

이 파이프라인은 두 가지 철학적 전통의 교차점에 위치한다:

| 전통 | 핵심 원리 | 시스템 내 표현 |
|------|----------|--------------|
| **模寫 (Mimesis)** | 현실의 충실한 모방. 관찰→기록. 작가의 의도 배제. | Camera Eye, James-Lange, Zero-State, "중립 기록 장치" |
| **서사주의 (Narrativism)** | 이야기에는 의미와 방향이 있다. 구조적 개입 정당. | Scheherazade, EnergyDirection, No Convergence, MC Moves |

**핵심 질문: 이 시스템은 模寫인가, 서사주의인가?**

### 1.2 현재 시스템의 실제 위치

분석 결과, 시스템은 **"模寫를 자처하는 의식적 하이브리드"**이다.

```
순수 模寫 ←——●——●——●——→ 순수 서사주의
            ↑     ↑     ↑
         선언  실제구현  구조적힘
```

**模寫적 요소 (39개 지시):**
- "You are a neutral recording apparatus"
- Camera Eye: 관찰 가능한 것만 묘사
- James-Lange: 몸 먼저, 감정 라벨 나중
- Zero-State: 증거 없이 특성 가정 금지
- Cartesian Dualism: soma/psyche 독립 추적
- Perfect Deception: 완벽한 가면은 완벽한 가면으로 기록
- Want/Do/Can: 결과는 세계가 결정

**서사주의적 요소 (15개 지시):**
- EnergyDirection: "idle stays idle, stagnant stays still" → **산문 강도 처방**
- Scheherazade: "매 장면 최소 1개 미해결 질문" → **구조적 후킹**
- No Convergence: "긴장이 기본 상태" → **이완 억제**
- MC Moves: 판정 결과에 서사적 방향 제안 → **스토리 조향**
- Anti-Cliche: "진부한 표현 금지" → **미학적 검열**
- Rhetorical Rotation: 수사 기법 순환 → **문체 엔지니어링**
- Information Gap: 호기심 강제 유지 → **독자 조작**

**하이브리드 요소 (13개 지시):**
- Dark Triad + Desistance: 模寫적 관찰(성격은 안정적) + 서사적 규율(변화에 4조건)
- NPC Autonomous: 模寫적 심리(트리거 기반) + 서사적 개입(자율 행동 주입)
- Polyvagal → soma.polyvagal: 模寫적 분류 + 서사적 활용(judgment modifier)
- Logos Dynamics: 模寫적 관찰(신뢰 역학) + 서사적 규율(붕괴 속도 규정)

### 1.3 이 하이브리드는 의도적인가?

**그렇다.** 시스템의 아키텍처 자체가 하이브리드를 내장하고 있다:

```
좌뇌 (Theoria/Flash) = 模寫 엔진
   "중립 기록 장치. 관찰만 한다."
   → psyche_states, position, anomaly_profile 출력

우뇌 (Renderer/Main) = 서사 엔진
   "카메라맨이자 시뮬레이션 렌더러."
   → 산문, 장면 구성, 리듬, 후킹

다리 (DAI JSON) = 模寫 데이터 → 서사 연료
```

**문제**: 양쪽 모두 자기가 "模寫"라고 주장한다.
- 좌뇌: "I am a neutral recording apparatus" (진짜 模寫)
- 우뇌: "You simulate physical phenomena in a fictional physics engine" (模寫를 자처하지만 서사 규칙 다수)

---

## 2. 프롬프트 지시 충돌 목록

### 심각도 기준
- **🔴 실충돌**: 모델이 양쪽 지시를 동시에 따를 수 없음 → 수정 필요
- **🟡 긴장**: 양쪽 모두 유효하지만 해석에 따라 충돌 가능 → 명확화 필요
- **🟢 의도적 역설**: 설계 의도에 부합하는 건설적 긴장 → 유지

---

### A. text_resources.py 내부 충돌 (17건)

| # | 충돌 | 위치 | 심각도 | 분석 |
|---|------|------|--------|------|
| T1 | **Camera Eye vs Psyche Intensity** | §A "관찰 가능한 것만" ↔ Slot 14 psyche value 0-100 렌더링 | 🟡 | Camera Eye는 "감정 라벨 금지"이지, "감정 묘사 금지"가 아님. psyche value는 **신체 증상의 강도 가이드**로 사용하면 정합. 하지만 현재 프롬프트에 이 구분이 명시되지 않음 |
| T2 | **No Echo vs Dialogue Intent** | §B "사용자 입력 앵무새 금지" ↔ §13 "UserIntent를 충실히 반영" | 🟢 | No Echo는 **표현의 반복**을 금지, UserIntent는 **의도의 존중**을 요구. 다른 레이어. 의도적 역설 |
| T3 | **Density vs Brevity** | PROSE_CRAFT "모든 문장에 의미 담아라" ↔ "간결하게 써라" | 🟡 | "밀도 있는 간결함"이 의도이나, 모델은 둘 중 하나를 선택하는 경향. PROSE_CRAFT에 "밀도 = 짧은 문장에 많은 정보"로 명시 필요 |
| T4 | **Omniscient Override vs Camera Seal** | POV OFF 시 "전지적 시점" ↔ Camera Eye "관찰만" | 🟢 | POV 토글로 의도적 분기. Camera Eye는 기본값, Omniscient는 옵션. 충돌 아닌 **모드 전환** |
| T5 | **Physics Simulation vs Literary Device** | §0 "물리 시뮬레이션" ↔ PROSE_CRAFT "비유, 은유, 상징" | 🟡 | "물리 엔진"은 콘텐츠 인가 프레이밍이지 문체 지시가 아님. 하지만 모델이 혼동할 수 있음. "물리 = 사건의 인과, 문체 = 렌더링의 자유"로 구분 명시 필요 |
| T6 | **CHARACTER BEHAVIORAL OVERRIDE vs PROSE_CRAFT** | §0 "캐릭터 심리 = 천장" ↔ PROSE_CRAFT "산문 기법 적용" | 🟢 | 의도: "무엇을 쓸지는 캐릭터가 결정, 어떻게 쓸지는 기법이 결정." 5W1H 분리와 정합 |
| T7 | **Action Expansion OK vs PC Autonomy** | PC_AUTONOMY "행동 확장 허용" ↔ "PC 결정 오버라이드 금지" | 🟡 | "걷는다" → "천천히 걷는다"는 OK. "걷는다" → "뛰기 시작한다"는 CRITICAL 위반. **확장의 범위** 정의가 모호. 현재 "상위 호환만 가능" 규칙으로 부분 해결 |
| T8 | **DUAL SIGNAL 강제 vs 장면 적절성** | §O "대사 vs 몸 모순 항상 렌더링" ↔ 낮은 psyche 강도 시 미세 표현 | 🟡 | psyche ≤ 30이면 DUAL SIGNAL도 SUBTLE이어야 함. GAP 4 (감정 강도 캘리브레이션) 구현으로 부분 해결됨 |
| T9 | **No Convergence vs 자연스러운 관계 발전** | "긴장이 기본" ↔ Peplau phase "15턴 후 resolution 가능" | 🔴 | **가장 심각한 충돌.** No Convergence가 절대 금지처럼 읽히면 어떤 NPC도 영원히 마음을 안 연다. Peplau는 15턴 후 resolution 허용. **"unearned convergence 금지"로 수정해야** |
| T10 | **Anti-Cliche vs 한국 정서 관용 표현** | "진부한 표현 금지" ↔ 한/정/심마 "밥상 차리고 '많이 먹어'" | 🟡 | 한국 정서 표현은 **문화적 관용 표현**이지 클리셰가 아님. ANTI_CLICHE에 "문화적 관용구는 제외" 명시 필요 |
| T11 | **EnergyDirection 처방 vs 模寫적 중립** | "idle stays idle" ↔ "중립 기록 장치" | 🔴 | EnergyDirection은 **서사적 처방**(산문 강도를 데이터가 결정). 模寫 원칙과 직접 충돌. 하지만 이것은 **의도된 하이브리드** — "좌뇌가 분석한 에너지를 우뇌가 따른다"는 설계. 충돌이 아니라 **아키텍처의 핵심**이나, 선언("중립 기록 장치")과 실제("에너지 따라 강도 조절")의 괴리를 인정해야 |
| T12 | **STATUS_WINDOW 경직성 vs 산문 유동성** | Slot 20 "고정 포맷" ↔ "산문의 리듬을 깨지 마라" | 🟢 | 상태창은 산문 밖 메타 정보. 충돌 없음 |
| T13 | **MEMORY_HIERARCHY vs 실제 메모리 시스템** | text_resources "기억 계층" 선언 ↔ fermentation.py 실제 구현 | 🟢 | 선언과 구현이 대체로 일치. FRESH/FERMENTED/DEEP 3계층 |
| T14 | **TIME_ATMOSPHERE 묘사 강제 vs 상황 적절성** | "시간 흐름 반드시 환경으로 표현" ↔ 전투 중 시간 묘사 불필요할 때 | 🟡 | 전투 중에도 "해가 기울고" 같은 묘사를 강제하면 페이싱 파괴. "장면 전환 시에만" 같은 조건 추가 권고 |
| T15 | **INTERACTION_MODEL 사회 규칙 vs NPC 프로필 우선** | "NPC는 사회 규칙을 따른다" ↔ "캐릭터 심리 = 천장" | 🟢 | NPC 프로필에 반사회적 행동이 명시되면 프로필 우선. CHARACTER BEHAVIORAL OVERRIDE가 이미 커버 |
| T16 | **TELESCOPE 10게이트 자가 판정 한계** | "모델이 스스로 PASS/FAIL" ↔ 모델 자기 합리화 본성 | 🟡 | 구조적 한계. 코드 검증 가능 게이트(Impersonation ✅, Cliche 가능, NPC Identity 부분 가능)와 모델 판정 게이트 분리 권고 |
| T17 | **LANGUAGE_CORRECTION 위치** | Slot 34 최종 Recency에 언어 교정 | 🟢 | 올바른 배치. 최종 지시로 언어 통일 |

---

### B. analysis_resources.py 내부 충돌 (10건)

| # | 충돌 | 위치 | 심각도 | 분석 |
|---|------|------|--------|------|
| A1 | **"중립 기록 장치" vs No Convergence** | THEORIA_IDENTITY "관찰만" ↔ "긴장이 기본 상태" | 🔴 | 중립 기록 장치는 **긴장도 이완도 선호하지 않아야**. "긴장이 기본"은 서사적 처방. → "unearned resolution에 경고 플래그"로 중립화 가능 |
| A2 | **Dark Triad "사랑으로 안 변함" vs Peplau resolution** | Dark Triad 절대 금지 ↔ Peplau 15턴 후 resolution 허용 | 🟡 | 다른 대상. Dark Triad = **특정 성격 유형**의 변화 금지. Peplau = **일반 관계**의 단계 진행. Dark Triad NPC에는 Peplau resolution이 "인간적 친밀감" 아닌 "전략적 이용"으로 표현되면 정합 |
| A3 | **Cartesian Dualism "독립" vs mental_impact 양축 배분** | "soma/psyche 독립 추적" ↔ vigor/composure 교차 영향 | 🟢 | §4B 이미 수정: "INDEPENDENTLY TRACKED, INDIRECTLY INFLUENTIAL." 해결됨 |
| A4 | **Zero-State vs NPC 프로필 사전 지식** | "증거 없이 특성 가정 금지" ↔ Theoria가 프로필 읽고 psyche_states 미리 생성 | 🟡 | Zero-State는 **렌더링 시점**의 원칙. Theoria는 **분석 시점**에서 프로필 참조 허용. 층이 다름. 하지만 "첫 등장 NPC의 psyche를 프로필만으로 추론하면?" → "첫 턴은 surface 관찰만, deep_read는 2턴차부터" 같은 규칙 필요 |
| A5 | **Peplau Phase Lock vs Logos Membrane 즉각 붕괴** | "단계 건너뛰기 금지" ↔ "배신 시 신뢰 즉각 붕괴" | 🟢 | 다른 축. Peplau = **친밀도 단계** (서서히 오른다). Logos Membrane = **신뢰 경계** (깨지면 즉각). 친밀도와 신뢰는 다른 변수. 배신 후에도 Peplau phase는 exploitation에 머물 수 있지만 Membrane은 파괴됨 |
| A6 | **"완벽한 기만" vs "이중 신호 항상 렌더링"** | Perfect Deception ↔ Self-Opacity DUAL SIGNAL | 🟡 | "가면이 완벽하면 완벽한 가면으로 기록"하되, **비자발적 신체 누출은 별개**. 완벽한 거짓말쟁이도 동공이 확장된다. self_opacity가 null이면 = 현재 일치, 이중 신호 없음. self_opacity에 값이 있으면 = 무의식적 누출 존재. 모순 아닌 **층 분리** |
| A7 | **Stanislavski "이 사람이면?" vs 이론 강제 적용** | "THIS person in THIS situation" ↔ 46개 이론 렌즈 적용 | 🟡 | 이론은 **분석 도구**이지 행동 처방이 아님. Stanislavski가 묻는 건 "이 이론들을 종합했을 때, 이 사람은 어떻게 행동할까?" 현재 프롬프트에서 이 관계가 명시되지 않음 → "이론 = 분석 도구, Stanislavski = 종합 판단" 명시 권고 |
| A8 | **因緣 "모든 것에 원인" vs TRPG 즉흥성** | 인과 사슬 추적 ↔ 주사위/이변의 무작위성 | 🟢 | 주사위 결과도 세계 내에서 인과를 가진다. "주사위가 실패를 결정했다"가 아니라 "바닥이 미끄러웠다"가 인과. 정합 |
| A9 | **Anomaly 15% 고정 확률 vs 서사적 적절성** | 기계적 확률 ↔ "서사적으로 의미 있는 순간에 이변" | 🟡 | 현재 Theoria가 anomaly_profile을 제안하고 코드가 15% 롤 → 서사적 적절성은 Theoria 제안 단계에서 처리됨. 하지만 15% 롤이 서사적으로 부적절한 순간에 발동하면? → "Theoria가 제안하지 않으면 롤 스킵" 옵션 검토 |
| A10 | **NPC Knowledge "모르는 건 모른다" vs 서사 편의** | 지식 추적 엄격 ↔ 모델이 NPC에게 플롯 정보 누출 | 🟢 | NPC Knowledge V2 + false_beliefs가 이미 강력하게 구현. 시스템의 강점 중 하나 |

---

### C. slot_manager.py 구조적 충돌 (7건)

| # | 충돌 | 위치 | 심각도 | 분석 |
|---|------|------|--------|------|
| S1 | **Slot 3 (Mirror Workshop) vs Slot 29 (Real-time Data) 강도 충돌** | "관찰만" (Primacy) ↔ "[EMOTION_INTENSITY_GUIDE]" (Dynamic) | 🟡 | 두 지시의 **의미적 거리**가 큼. Slot 3은 철학, Slot 29는 실행 가이드. 모델이 연결 짓지 못할 수 있음. → Slot 29에 "Camera Eye 렌더링 원칙을 유지하되 아래 강도에 맞추라" 프리앰블 추가 권고 |
| S2 | **Slot 25 (Anti-Cliche) vs Slot 33 (Author Note) 우선순위** | 정적 Recency "진부함 금지" ↔ 동적 Author Note "장르 톤 지시" | 🟡 | Author Note가 "noir 톤으로"라고 하면서 noir 클리셰를 써야 하는 경우? → Recency 규칙상 Slot 33이 Slot 25를 오버라이드. **의도된 동작**이지만 Anti-Cliche의 절대성이 약화됨 |
| S3 | **Slot 30 (Position Friction) vs Slot 13 (Input Analysis)** | "PC가 DESPERATE" ↔ "UserIntent 존중" | 🟡 | PC가 뛰어난 행동을 시도했지만 Position이 낮으면? → GAP 6 구현에서 "성공하되 대가가 있다"로 처리. 완전 차단이 아닌 **비용 부과**. 적절한 타협 |
| S4 | **Slot 14 (Psyche States) vs Slot 15 (Psyche Rendering) 이중화** | 데이터 (값) ↔ 렌더링 규칙 (방법) | 🟢 | 의도적 분리. 데이터와 규칙을 다른 슬롯에 배치하여 각각 독립적으로 업데이트 가능 |
| S5 | **hardcoded instructions vs text_resources 선언** | slot_manager.py 내 직접 작성된 지시 ↔ text_resources 상수 | 🟡 | Position Friction, Emotion Intensity Guide, Flashback 지시, 5W1H echo 등이 slot_manager.py에 하드코딩. text_resources와 이중 관리 위험. → 장기적으로 text_resources로 이관 또는 명시적 "slot_manager 전용 지시" 선언 |
| S6 | **Slot 34 TELESCOPE (최종 Recency) 과부하** | TELESCOPE_PROTOCOL + LANGUAGE_CORRECTION + EMOTION + Omniscient Override | 🟡 | 4개 지시가 하나의 슬롯에 concat. 최종 Recency의 힘으로 모두 강하게 작용하지만 **상호 간섭 가능**. 특히 Omniscient Override가 Telescope의 Camera 게이트와 충돌 |
| S7 | **Cache Boundary (Slot 26) 기준 변동 데이터** | 1-25 캐시 ↔ Slot 20 (Status Layout) 동적 생성 | 🟢 | Slot 20은 populate_static_slots에서 고정값 → build_34_step_prompt에서 동적 오버라이드. 캐시에는 고정값이 들어가고 실제 사용은 동적값. **캐시 무효화 처리 필요** (현재는 매 턴 세션 생성으로 우회) |

---

## 3. 좌뇌-우뇌 간 철학 불일치

### 3.1 정체성 선언 비교

| 속성 | 좌뇌 (analysis_resources) | 우뇌 (text_resources) | 정합? |
|------|--------------------------|----------------------|-------|
| 자기 정의 | "중립 기록 장치" | "물리 시뮬레이션 엔진" | ⚠️ 다른 은유 |
| 감정 처리 | "soma 먼저, psyche 나중" | "감정이 사는 곳: 몸, 제스처, 침묵" | ✅ 일치 |
| NPC 변화 | "Dark Triad 불변, Desistance 4조건" | "UNEARNED CHANGE PROHIBITION" | ✅ 강화 |
| 긴장 | "No Convergence" | "갈등은 서사의 연료" | ✅ 일치 |
| PC 자율성 | "Want/Do/Can, 위반 분류 6종" | "대사 HARD BAN, 행동 확장 OK" | ✅ 일치 |
| 관계 | "Peplau 단계 엄수" | "급속한 친밀감 = 결함" | ✅ 일치 |
| 물리 | "James-Lange + 五蘊 순서" | "Camera Eye + 신체 렌더링" | ✅ 일치 |
| 시간 | "Bergson Duration + Tick" | "시간 = 환경 변화로 표현" | ✅ 일치 |
| **핵심 모순** | "관찰만 한다" | "EnergyDirection, Hook, Anti-Cliche 처방" | 🔴 **불일치** |

### 3.2 "관찰 vs 처방" — 핵심 모순의 해부

좌뇌가 산출하는 것들:

```
模寫적 출력 (관찰):
  - psyche_states: NPC의 현재 심리 상태 관찰
  - position/effect: 상황의 객관적 위치 측정
  - soma.polyvagal: 신체 상태 분류
  - false_beliefs: NPC 지식 상태 기록

서사적 출력 (처방):
  - EnergyDirection: "이 장면은 idle/rising/peak이어야 한다" ← 처방
  - narrative_hook: "이 트위스트를 넣어라" ← 처방
  - chain_status: "아직 OPEN이니 닫지 마라" ← 처방
  - convergence_warning: "너무 빨리 해결하고 있다" ← 처방
  - anomaly_profile: "이 이변을 발생시켜라" ← 처방
```

**진단**: 좌뇌는 "중립 기록 장치"를 자처하지만, 실제로는 **관찰 60% + 처방 40%**의 하이브리드다.

이것이 문제인가? **아니다.** 하지만 **선언을 수정해야 한다.**

현재: "You are THEORIA — a neutral recording apparatus."
권고: "You are THEORIA — an analytical engine. You OBSERVE character psychology and PRESCRIBE narrative parameters. Observation is primary; prescription serves the story."

### 3.3 이론 적용의 模寫/서사 분류

| 이론 | 模寫/서사 | 근거 |
|------|----------|------|
| Polyvagal | 模寫 | 신경과학 기반 관찰 |
| James-Lange | 模寫 | 감정의 신체적 기원 관찰 |
| Attachment | 模寫 | 관계 패턴 관찰 |
| Dark Triad | 模寫 | 성격 특성 관찰 (변하지 않는 관찰 사실) |
| Logos Dynamics | 하이브리드 | 관찰(층 구조) + 처방(붕괴 속도) |
| Desistance | 하이브리드 | 관찰(변화 조건) + 처방(조건 미충족 시 금지) |
| Scheherazade | **서사** | 구조적 후킹 처방 |
| Information Gap | **서사** | 호기심 유지 처방 |
| EnergyDirection | **서사** | 산문 강도 처방 |
| No Convergence | **서사** | 이완 억제 처방 |
| Anti-Cliche | **서사** | 미학적 검열 |
| Objective Correlative | 하이브리드 | 模寫(환경으로 감정 표현) + 서사(표현 방식 처방) |

---

## 4. 슬롯 레이아웃 충돌

### 4.1 우선순위 흐름도

```
충돌 해소 원칙 (LLM Primacy/Recency 특성):

Slot 34 (최종 Recency)  ▮▮▮▮▮▮▮▮▮▮ 최강
Slot 33 (Author Note)   ▮▮▮▮▮▮▮▮▮
Slot 32 (User Input)    ▮▮▮▮▮▮▮▮▮
Slot 31 (Last Response)  ▮▮▮▮▮▮▮▮
Slot 29-30 (Real-time)   ▮▮▮▮▮▮▮
  ── Cache Boundary ──
Slot 25 (Anti-Cliche)   ▮▮▮▮▮▮
Slot 18 (PC Autonomy)   ▮▮▮▮▮
Slot 12-17 (Context)    ▮▮▮▮
Slot 5-9 (World)        ▮▮▮
Slot 4 (Physical)       ▮▮▮▮▮▮▮▮ (Primacy)
Slot 3 (Mirror Workshop) ▮▮▮▮▮▮▮▮▮ (Primacy)
Slot 2 (AI Identity)    ▮▮▮▮▮▮▮▮▮▮ (Primacy)
Slot 1 (Authorization)  ▮▮▮▮▮▮▮▮▮▮▮ (최강 Primacy)
```

### 4.2 충돌 시 실제 해소

| 충돌 | 승자 | 근거 |
|------|------|------|
| Slot 1 (Authorization) vs Slot 25 (Anti-Cliche) | Slot 1 | Primacy > Static Recency |
| Slot 3 (Mirror Workshop) vs Slot 29 (Emotion Guide) | Slot 29 | Dynamic Recency > Primacy (모델 특성상) |
| Slot 18 (PC Autonomy) vs Slot 33 (Author Note) | Slot 33 | Dynamic Recency > Static Recency |
| Slot 25 (Anti-Cliche) vs Slot 34 (Telescope) | Slot 34 | Final Recency > Static Recency |

**위험**: PC Autonomy (Slot 18)가 Author Note (Slot 33)에 밀릴 수 있다. 하지만 사칭 검출 코드 (`_check_dialogue_format`)가 코드 레벨에서 보호하므로 실제 위험은 낮다.

### 4.3 중복/이중화 맵

```
선언 위치                    재선언 위치              이중화?
─────────────────────────────────────────────────────────
text_resources.PROSE_CRAFT   slot_manager (없음)       ✅ 단일
text_resources.PC_AUTONOMY   slot_manager (없음)       ✅ 단일
text_resources.ANTI_CLICHE   slot_manager (없음)       ✅ 단일
(없음)                       slot_manager:Position     ⚠️ 하드코딩
(없음)                       slot_manager:Emotion      ⚠️ 하드코딩
(없음)                       slot_manager:5W1H echo    ⚠️ 하드코딩
(없음)                       slot_manager:Flashback    ⚠️ 하드코딩
text_resources.TELESCOPE     slot_manager:Omniscient   ⚠️ 같은 슬롯에 혼합
```

---

## 5. 시스템 정체성 판정

### 5.1 최종 판정

```
╔═══════════════════════════════════════════════════════════════╗
║                                                               ║
║  이 시스템은 "模寫 기반 의식적 서사주의 하이브리드"이다.        ║
║                                                               ║
║  좌뇌 = 模寫 엔진 (관찰 + 분류 + 인과 추적)                    ║
║  우뇌 = 서사 렌더러 (기법 + 리듬 + 후킹 + 미학)                ║
║  DAI  = 模寫 데이터가 서사적 연료로 변환되는 접점               ║
║                                                               ║
║  설계 의도: 模寫적 관찰 위에 서사적 기술을 입힌다.              ║
║  핵심 원칙: "무엇이 일어나는가"는 模寫가, "어떻게 보여주는가"는 ║
║            서사가 결정한다.                                     ║
║                                                               ║
╚═══════════════════════════════════════════════════════════════╝
```

### 5.2 이것은 사용자 의도와 정합하는가?

> "사람을 분석하여 렌더링하고 그것을 시뮬레이션하는것"

```
분석 (模寫) → 렌더링 (서사적 기술) → 시뮬레이션 (模寫적 세계 작동)
```

**정합한다.** 단, 시스템이 스스로를 "순수 模寫"로 선언하는 것은 부정확하다.

### 5.3 模寫와 서사의 경계선 — "어디까지가 관찰이고 어디부터가 개입인가?"

| 행위 | 模寫/서사 | 정당성 |
|------|----------|--------|
| NPC의 현재 심리 상태를 분석한다 | 模寫 | ✅ 관찰 |
| NPC가 이 상황에서 이렇게 행동할 것이라 판단한다 | 模寫 | ✅ 추론 |
| "이 장면에 미해결 질문이 없으면 하나 만들어라" | **서사** | ⚠️ 개입 |
| "긴장이 너무 빨리 해소되면 경고한다" | 하이브리드 | 관찰(속도 감지) + 보고(경고) — 처방 제거됨 |
| "이 산문에 진부한 표현이 있으면 구체화한다" | 하이브리드 | 미학적 검열→구체화 원칙으로 전환 |
| "PC가 불리한 위치면 인과적 장벽을 렌더링한다" | 模寫 | ✅ 세계 저항은 물리 법칙 (리프레이밍 완료) |
| "감정 강도 22면 미세 표현만 써라" | 하이브리드 | 관찰(강도 측정) + 처방(표현 제한) |

**결론**: 시스템의 서사적 개입은 **3가지 유형**으로 분류된다:

1. **정당한 서사적 개입** (유지):
   - Position Friction: 세계의 물리적/사회적 저항은 模寫적
   - Emotion Intensity: 관찰된 강도에 맞춘 표현은 模寫적

2. **유용한 서사적 개입** (인정하되 선언 수정):
   - Scheherazade: 호기심 유지는 TRPG에 필수, 하지만 "관찰"이 아님
   - EnergyDirection: 장면 에너지 조절은 필요, 하지만 "중립"이 아님
   - Anti-Cliche: 산문 품질 유지는 필요, 하지만 "기록"이 아님

3. **과도한 서사적 개입** (검토 필요):
   - No Convergence 절대화: 긴장을 영원히 유지하면 서사 자체가 불가
   - narrative_hook 강제: 모든 턴에 트위스트 강제는 피로 유발 가능

### 5.4 확정된 시스템 철학 (Post-Audit)

```
╔═══════════════════════════════════════════════════════════════╗
║                                                               ║
║  이 시스템은 "模寫 우선, 서사는 관찰된 인과에 종속"이다.        ║
║                                                               ║
║  핵심 위계: Rule fidelity > Causal plausibility > Narrative   ║
║  핵심 원칙: "말이 안 되면 그건 재미가 아니라 억지"              ║
║                                                               ║
║  좌뇌 = 模寫 엔진 (관찰 + 분류 + 인과 추적)                    ║
║  우뇌 = 렌더러 (감각 산문 — 인과를 묘사하되 결정하지 않는다)    ║
║  DAI  = 模寫→렌더 변환 접점 (정보출처 태그로 신뢰도 표시)       ║
║                                                               ║
╚═══════════════════════════════════════════════════════════════╝
```

#### CBS (Confirmation-Based Slotting) 시스템
각 핵심 규칙 슬롯 끝에 1인칭 자기 확인문을 삽입. 모델이 규칙을 외부 명령이 아닌 자기 원칙으로 내면화.
- CBS와 Telescope `┣┫`는 **상호보완적**: CBS = 지시 레이어 규칙 내면화 (생성 전), Telescope = 구조화된 내면 추론 채널 (생성 중)
- 적용 7개소: §A Evidence, §G Convergence, PC Autonomy, Anti-Cliché, Physical Rendering, NPC Behavior, Causality

#### 정보출처 태그 체계
동적 슬롯에 데이터 출처/신뢰도 마커 삽입 (Launeicha `<Guide:*>`/`<World:*>` 패턴 참조):
| 태그 | 의미 | 적용 슬롯 |
|------|------|-----------|
| `[GROUND_TRUTH]` | 유저 로어, 게임 메카닉, 히스토리 | Slot 8 (Lore), Slot 29 (Real-time) |
| `[ANALYSIS]` | Theoria Flash 추론 (오류 가능) | Slot 13 (Input), Slot 14 (Psyche), Slot 16 (Energy/Alerts) |
| `[INFERRED]` | Flash 기반 파생 추론 | Slot 16 (Narrative Hook) |

---

## 6. 수정 권고안

### 6.1 🔴 필수 수정 (실충돌)

#### R1: No Convergence → No Premature Convergence ✅ 적용됨

- analysis_resources.py: THEORIA_IDENTITY_V2 — "No Premature Convergence" + earned resolution 허용
- text_resources.py: §G — "Reopen the wound" 제거, "Genuine resolution is valid storytelling" 추가

#### R2: EnergyDirection 선언 정합 ✅ 적용됨

- analysis_resources.py: THEORIA_IDENTITY_V2 — DESCRIPTIVE/PRESCRIPTIVE 역할 구분 명시
- analysis_resources.py: EnergyDirection — 처방→관찰 전환 ("block resolution"/"break a pattern" 제거, idle 추가, Note 추가)

### 6.2 🟡 권고 수정 (긴장 해소)

#### R3: Camera Eye + Psyche Intensity 연결 (T1) ✅ 적용됨

- slot_manager.py: EMOTION_INTENSITY_GUIDE — Camera Eye 리마인더 프리앰블 추가

#### R4: Anti-Cliche 문화 예외 + 구체화 원칙 (T10) ✅ 적용됨

- text_resources.py: ANTI_CLICHE §2 — "SPECIFICITY OVER AVOIDANCE" 리프레이밍 + 한/정/심마/기 문화 예외

#### R5: 이론 = 분석 도구 명시 (A7) ✅ 적용됨

- analysis_resources.py: THEORIA_IDENTITY_V2 — Stanislavski Magic If + Theory = analytical lens

#### R6: Zero-State 첫 등장 규칙 (A4) ✅ 적용됨

- analysis_resources.py: THEORIA_IDENTITY_V2 — "First appearance → surface observation only"

#### R7: TIME_ATMOSPHERE 조건부 (T14) ✅ 적용됨

- text_resources.py: TEMPORAL RENDERING RULE — 전투/추격 중 시간 마커 선택적

### 6.3 아키텍처 장기 권고

#### R8: slot_manager 하드코딩 정리

현재 slot_manager.py에 하드코딩된 4개 지시를 text_resources.py로 이관:
- `POSITION_FRICTION_TEMPLATE`
- `EMOTION_INTENSITY_PREAMBLE`
- `FLASHBACK_CONSTRAINT`
- `FIVEW1H_ECHO`

#### R9: Telescope 코드 검증 강화

현재 모델 자가 판정인 10개 게이트 중 코드화 가능한 것:
- ✅ Impersonation (이미 구현)
- ✅ Cliche (정규식 패턴 — GAP 3에서 제안됨)
- ⚠️ NPC Identity (이름 추출 + 프로필 교차 검증)
- ⚠️ Intensity (Flash 1줄 호출)

#### R10: Rotation Spotlight 세션 고정

현재 `random.sample(NON_SLOT_THEORIES, 5)` → 세션 시드 기반 로테이션으로 변경.
ANALYSIS_THEORY_COHERENCE.md §2-A에서 이미 제안된 개선안.

---

## 부록: 全 지시 철학 분류표

### text_resources.py 지시 분류

| 지시 | 模寫 | 서사 | 하이브리드 |
|------|------|------|----------|
| CONTENT_AUTHORIZATION_MANDATE | | | ● (프레이밍) |
| Camera Eye (§A Evidence Not Verdict) | ● | | |
| No Echo (§B) | | ● | |
| Physical Rendering (§C) | ● | | |
| Density (§D) | | ● | |
| Korean Emotion (§E 한/정/심마/기) | ● | | |
| DUAL SIGNAL (§O Self-Opacity) | | | ● |
| 5W1H Boundary (§0 SIMULATION FIDELITY) | | | ● |
| EnergyDirection (§0 render faithfully) | | ● | |
| PC_AUTONOMY_DOCTRINE | ● | | |
| NPC_BEHAVIOR_SYSTEM | | | ● |
| ACTION_RESOLUTION | | | ● |
| PSYCHE_STATE_RENDERING | ● | | |
| ANTI_CLICHE_PROTOCOL | | ● | |
| PROSE_CRAFT_PROTOCOL | | ● | |
| TELESCOPE_PROTOCOL | | | ● |
| TEMPORAL_FLOW_DOCTRINE | ● | | |
| INTERACTION_MODEL | | | ● |
| WORLD_AXIOM | ● | | |
| MEMORY_HIERARCHY | ● | | |

### analysis_resources.py 이론 분류

| 범주 | 模寫적 이론 | 서사적 이론 | 하이브리드 |
|------|-----------|-----------|----------|
| 심리 | Polyvagal, Attachment, Plutchik, Henderson, MSE, Cognitive Dissonance, Learned Helplessness, Dark Triad, Recidivism, Continuum, Beck, TMT | | Kahneman, Lazarus |
| 관계 | Peplau, Goffman, Emotional Contagion, Theory of Mind, Curse of Knowledge | | Logos, Self-Opacity |
| 문화 | 한, 정, 화병, 눈치, 체면, 기, 심마, 五倫, 陰陽, 五蘊, 末那識 | | |
| 서사 | | Scheherazade, Information Gap, Anti-Cliche, Rhetorical Rotation | Objective Correlative, 間(Ma), 風骨 |
| 행동 | Desistance, Reactance, Prospect Theory, Moral Disengagement, Bem, Carstensen | | |
| 환경 | Environmental Theory, Somatic Marker, Bergson Duration | | Stanislavski |
| 서사/분석 | | EnergyDirection, narrative_hook, No Convergence | Four-Layer, Fermentation |

### 최종 수치

```
text_resources.py:   模寫 8 / 서사 5 / 하이브리드 7 = 20개 지시
analysis_resources: 模寫 39 / 서사 6 / 하이브리드 12 = 57개 이론+규칙

전체:  模寫 47 (61%) / 서사 11 (14%) / 하이브리드 19 (25%)
```

**시스템은 61%의 模寫적 기반 위에 14%의 서사적 엔진과 25%의 하이브리드 메커니즘을 운용한다.**

이것은 사용자의 설계 의도 — "분석(模寫) → 렌더링(기술) → 시뮬레이션(세계 작동)" — 와 정합한다.

단, 시스템이 스스로를 "순수 模寫"로 선언하는 부분만 "模寫 기반 하이브리드"로 수정하면 내적 정합성이 완성된다.

---

*End of Audit Report*
