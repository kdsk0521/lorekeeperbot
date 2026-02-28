# 무대장치 분석서 — Lorekeeper 서사 엔진의 지시 생태계

> **최종 업데이트**: 2026-02-28
> **목적**: 서사 출력을 형성하는 모든 프롬프트 장치의 방향성·상호작용·누적 효과 분석

---

## 1. 핵심 발견

| 항목 | 수치 |
|------|------|
| **총 무대장치** | 52개 |
| **토큰 할당** | ~6,500-7,000 (프롬프트의 25-30%) |
| **PULL(억제) : PUSH(허용)** | **70% : 30%** |
| **따뜻함** | "bias" 경고 (Rubin Vase) |
| **위로** | 명시적 금지 (No Comfort) — 1개 예외 |
| **유머** | 중립 (player-driven만) |

> **결론**: 규칙 체계가 전방위적으로 "빼라"에 치우쳐, 모델이 가장 안전한 선택인 "차갑고 절제된 톤"으로 수렴.

---

## 2. 방향성 분포 (Direction Spectrum)

```
극 PULL(억제)                    중립                    극 PUSH(허용)
████████████████░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░
     70%                        16%                    30%
```

### Primacy Zone (Slot 1-4) — 모델이 가장 강하게 기억

| Slot | 이름 | 방향 | 토큰 |
|------|------|------|------|
| 1 | Content Authorization | PUSH극 | 280 |
| 2 | AI Identity | PUSH | 150 |
| 3 | Mirror Workshop (8원칙) | **PULL극** | 660 |
| 4 | Physical Rendering (Camera Eye) | **PULL극** | 240 |

> Primacy 1,330토큰 중 **900토큰이 억제 지시**. 모델의 "첫인상"이 제약.

### Rules Zone (Slot 18-25) — 행동 규칙

| Slot | 이름 | 방향 | 토큰 |
|------|------|------|------|
| 18 | PC Autonomy | PULL극 | 60 |
| 21 | Action Resolution | PULL | 90 |
| 25 | Anti-Cliché + Prose Craft | **PULL극** | 780 |

### Dynamic Zone (Slot 27-34) — 실시간

- 70% mixed (서사 훅 PUSH + quality flag PULL)
- Telescope: 10+5게이트 자기검증 → **분석 모드 유지**

---

## 3. 전체 무대장치 인벤토리

### A. 철학/정체성 (What the AI IS)

| # | 이름 | 방향 | 효과 |
|---|------|------|------|
| 1 | Content Authorization | PUSH극 | 모든 콘텐츠 허용 |
| 2 | AI Core Identity (3 Pillars) | PUSH | 재판관+렌더러+촉진자 |
| 3 | World Axiom | 중립 | 비동기 세계, 평등 인과, 의식 불투명 |

### B. 관찰 규칙 — "무엇을 볼 수 있는가" (Camera)

| # | 이름 | 방향 | 효과 |
|---|------|------|------|
| 4 | Camera Eye / Physical Rendering | **PULL극** | PC 내면 절대 금지, 신체 신호만 |
| 5 | Epistemic Boundary (Perception) | **PULL극** | 모르면 이름 없다, 현상학만 |
| 6 | Evidence not Verdict | **PULL극** | 판단 금지, punctum만 |
| 7 | Omniscient Override | PUSH | NPC 내면은 허용 (PC 제외) |

### C. 감정/반응 규칙 — "어떻게 느끼는가"

| # | 이름 | 방향 | 효과 | 문제점 |
|---|------|------|------|--------|
| 8 | Delayed & Imperfect Response | **PULL** | 즉각 반응 = 예외 | ~~모든 접촉~~ 미해결 감정만 (수정됨) |
| 9 | Emotion Wave Model | PUSH | 감정 변동 + lull 필수 | |
| 10 | Rubin Vase | **PULL→양방향** | ~~따뜻함 의심~~ 양방향 질문 (수정됨) | |
| 11 | Signal vs Emergence Gap | PULL | Intent ≠ Output | 친밀한 사이에도 gap 적용됨 |
| 12 | No Comfort (Mirror §E) | **PULL극** | 위로 = 대가 필수 | "earned"만 허용 |
| 13 | No Premature Convergence | **PULL극** | 갈등 유지 강제 | |
| 14 | Contradiction is Life | PUSH | 자기모순 = 가장 생생 | |
| 15 | Korean Emotions (한/정/심마/기) | PUSH극 | 문화 특화 감정 체계 | |

### D. NPC 규칙 — "타인은 어떻게 행동하는가"

| # | 이름 | 방향 | 효과 |
|---|------|------|------|
| 16 | NPC Autonomy / Decision Matrix | PULL | 독립 목표, Want×Know×How×Stakes |
| 17 | No Echo | PULL | PC 감정 따라하기 금지 |
| 18 | Zero-State | PULL | 부정 특성 = 인과 전까지 없음 |
| 19 | Perfect Deception | PULL | 가면 완벽, 직감 금지 |
| 20 | Unearned Change 금지 | **PULL극** | 1 kind act ≠ transformation |
| 21 | NPC Decision Pacing | PULL | 3턴 최소 (위협 제외) |
| 22 | Behavioral Persistence | PULL | 악인 = 편안하게 악행 |
| 23 | NPC Attitude/Depth Knobs | 중립 | per-NPC 노출 제어 |
| 24 | Dialogue Directives (4축) | PUSH | Purpose/Strategy/Hidden/Revealed |
| 25 | 눈치 & 체면 | PUSH | 한국 문화 관계 역학 |

### E. 산문 규칙 — "어떻게 쓰는가"

| # | 이름 | 방향 | 효과 | 문제점 |
|---|------|------|------|--------|
| 26 | Sentence Rhythm & Density | PUSH | 에너지 = 문장 구조 | |
| 27 | Paragraph as Breath | PUSH | 1문단 = 1 sensory focus | |
| 28 | Objective Correlative | PUSH | 감정의 물리 등가물 | |
| 29 | Metaphor Vehicle | PULL | 장면 근거 필수, 외부 금지 | |
| 30 | Afterglow | PUSH | 감각 잔상 허용 | |
| 31 | Rhetorical Rotation | **PULL** | 1회/turn max, 연속 금지 | |
| 32 | Rendering Gate | **PULL** | 특성 1회 후 무음 건축 | |
| 33 | Cargo Check | **PULL→완화** | ~~기능 없으면 삭제~~ 이완도 기능 (수정됨) | |
| 34 | DOA (Dead on Arrival) | **PULL** | 3-5 죽은 표현 매턴 명명 → 사용 금지 | |
| 35 | Korean Sentence Doctrine | PULL | ~다 2연속 max, 체언종결 2/문단 | |
| 36 | Dead Words (25개) | **PULL극** | delve, embark, meticulous 등 완전 금지 | |
| 37 | Lapalissade (동어반복) | **PULL극** | emotion label / verdict 금지 | |
| 38 | Anti-Template | PULL | Edgelord/Glutton/Miser 금지 | |
| 39 | 3-Axis Directing (♪▶◎) | PUSH | 음악/카메라/사진 기법 표기 | |

### F. 구조 규칙 — "이야기는 어떻게 흐르는가"

| # | 이름 | 방향 | 효과 |
|---|------|------|------|
| 40 | Pacing Control (Beat Budget) | PULL | 1-2 beat/turn max |
| 41 | Scene Termination | PULL | 퇴장 = 계속 (종료 거부) |
| 42 | Scheherazade | PUSH | 매턴 새로운 흥미 삽입 |
| 43 | Temporal Flow (10 rules) | PULL | 인과 강제, teleport 금지 |
| 44 | Off-Screen Persistence | PUSH | 돌아온 몸 = 부재의 기록 |
| 45 | Ambient Flux | PUSH | 세계 자동 변화 |
| 46 | Rendered Once (無常) | PULL | 반복 금지, 변화 필수 |
| 47 | Departure (떠남) | PUSH | 마지막 문장 = springboard |
| 48 | Withholding Engine (4수법) | **PULL극** | 핵심 접근 금지, 수법 회전 |

### G. 시스템 장치 — "코드가 주입하는 것"

| # | 이름 | 방향 | 효과 |
|---|------|------|------|
| 49 | Quality Flags (7종) | PULL | convergence/echo/stagnation 등 경고 |
| 50 | Continuity Check | PUSH | 불연속 감지→보정 |
| 51 | Telescope (10+5 gates) | PUSH | 강제 추론, Phase A+B 교차검증 |
| 52 | Emotion Intensity | PUSH | 관찰 가능 범위 표시 |

---

## 4. 누적 효과 분석 — "얼음장의 팽팽함"

### 왜 글이 차가워지는가

```
[PULL 층 1] Camera Eye     — 내면 못 봄
[PULL 층 2] No Comfort     — 위로 금지
[PULL 층 3] Delayed Response — 즉각 반응 금지
[PULL 층 4] Signal Gap     — 의도 ≠ 출력
[PULL 층 5] Rubin Vase     — 따뜻함 의심
[PULL 층 6] No Echo        — NPC 공감 억제
[PULL 층 7] No Convergence — 갈등 해소 억제
[PULL 층 8] Cargo Check    — 여유 문장 삭제
[PULL 층 9] Rendering Gate — 반복 표현 금지
[PULL 층 10] Rhetorical Rotation — 같은 기법 금지
────────────────────────────────────────────
누적: 모델이 매 문장마다 10개 규칙에 걸리지 않는지 자기검열
→ 가장 안전한 선택 = 차갑고 절제된 톤
→ "항상 팽팽한 이야기에는 긴장이 없다 — 피로만 있다"
```

### 수정 완료 항목 (2026-02-28)

| 규칙 | 기존 | 수정 |
|------|------|------|
| Rubin Vase | 따뜻함 느끼면 의심 | **양방향** — 긴장 느낄 때도 의심 |
| Cargo Check | 기능 없는 문장 = 삭제 | 이완도 기능 (baseline establish) |
| Delayed Response | 모든 반응 = 지연 | **미해결 감정만**. 안정된 친밀함 = 직접적 |

### 추가 수정 후보

| 규칙 | 현재 문제 | 수정 방향 | 우선순위 |
|------|----------|----------|---------|
| No Comfort (§E) | "위로 = 대가 필수" — 모든 위로에 비용 부과 | "earned comfort = free" 예외 추가 | 높음 |
| No Echo | NPC가 PC 감정에 절대 공감 안 함 | "alignment ≠ echo" — 같은 방향 가능 | 중간 |
| Quality Flag: convergence | 수렴 경고가 자연스러운 화해도 차단 | threshold 완화 또는 context 체크 | 중간 |
| Rendering Gate | 한번 보여준 특성은 다시 못 보여줌 | "evolution" 표현은 허용 | 낮음 |

---

## 5. 무대장치 상호작용 — 긴장 유지 사이클

```
턴 N:   Input → Want/Do/Can → World Response (DAI 기반)
턴 N+1: Quality Flags 체크
        ├ convergence_warning → "불편함 유지" 지시
        ├ echo_warning → "NPC 독립성" 강화
        ├ stagnation_warning → "외부 자극" 자연스럽게
        └ none → 정상 진행

긴장 유지 장치들:
  Withholding Engine ──→ 핵심 접근 금지
  No Premature Convergence ──→ 갈등 해소 억제
  NPC Decision Pacing ──→ 최소 3턴
  Signal vs Emergence Gap ──→ 의도 ≠ 결과

이완 허용 장치들 (수정 후):
  Cargo Check 예외 ──→ 일상 이완 = 기능적
  Rubin Vase 역방향 ──→ 따뜻함도 진실
  Delayed Response 예외 ──→ 안정된 친밀함 = 직접적
  Afterglow ──→ 감각 잔상 = 여유
  Off-Screen Persistence ──→ 일상 디테일 허용
```

---

## 6. 따뜻함 / 위로 / 유머 — 현재 지위

### 따뜻함 (Warmth)
- **기존**: "bias" 경고 → 모델이 따뜻함을 결함으로 인식
- **수정 후**: 양방향 — 차가움도 따뜻함도 캐릭터에 맞으면 진실
- **명시적 허용**: earned intimacy, close people, settled emotions

### 위로 (Comfort)
- **기존**: §E No Comfort — "Resolution is earned, not promised"
- **현재**: 유일 예외 = baseline establish (다음 disruption 설정용)
- **추가 후보**: "earned comfort = free" (충분한 buildup 후 비용 면제)

### 유머 (Humor)
- **할당 토큰**: 0 — 명시적 지시 없음
- **유일 경로**: player intent 존중 / deflection 수법 / character motivation
- **추가 후보**: NPC 성격에 유머가 있으면 허용하는 명시적 허가

---

## 7. 설계 원칙 요약

무대장치 체계는 **"사람이 쓴 것 같은 글"**을 목표로 설계되었으나, 수단이 **"금지 목록"**에 과도하게 의존. 인간 작가는 이런 규칙을 내면화하되 직관적으로 무시할 타이밍을 알지만, LLM은 규칙을 문자 그대로 **동시에 전부** 적용하려 하므로 산문이 경직됨.

**해법**: 규칙 삭제가 아니라 **"이완 허가"**를 같은 무게로 추가. 모든 PULL에 대응하는 예외 조건을 명시하여, 모델이 "이 상황에서는 규칙을 내려놓아도 된다"는 판단을 할 수 있게 함.
