# 서사 이론 체계 정합성 분석

## 1. 아키텍처 개요

```
Left Brain (Flash)                    Right Brain (Main Renderer)
───────────────                       ─────────────────────────
analysis_resources.py (46개 이론)  →   text_resources.py (렌더링 규칙)
theory_emphasis_engine.py (가중치)  →   slot_manager.py (34슬롯 조립)
                                  ↕
               JSON DAI (psyche_states, narrative_chain, etc.)
```

Flash가 이론으로 "분석" → Main이 데이터를 "산문"으로 변환하는 2단 번역 구조.

---

## 2. 잘 맞는 조합들

### A. James-Lange + 五蘊 + Camera Eye (시스템 척추)
- 좌뇌: soma 먼저 분석 → psyche는 그 다음
- 우뇌: 카메라가 찍을 수 있는 것만 렌더링
- "슬프다" 대신 "손이 떨렸다" — 양쪽에서 동일 원칙 강제
- **평가: 완전히 정합. 시스템의 가장 강한 축.**

### B. Desistance + Recidivism + Dark Triad (AI 고질병 치료)
- Dark Triad: 안정 특질, 사랑으로 안 변함
- Recidivism: 기본값 = 패턴 지속, 표현된 후회 = 가장 약한 예측자
- Desistance: 변화에 4가지 조건 ALL 필요 (대체 정체성 + 사회 지원 + 생성 동기 + 구속 서사)
- 우뇌 NPC_BEHAVIOR_SYSTEM에서 UNEARNED CHANGE PROHIBITION으로 이중 강제
- **평가: 세 이론이 상호 강화. AI 쉬운 구원 방지에 매우 효과적.**

### C. Self-Opacity + Dual Signal + 間(Ma)
- 좌뇌: `self_opacity: "claims X — actual: Y"` 형식 출력
- 우뇌: MIRROR_WORKSHOP §O DUAL SIGNAL — 대사 vs 몸 모순 렌더링
- 침묵 유형(reflective/hesitant/heavy/tense)까지 분류
- **평가: "말하는 것 ≠ 느끼는 것 ≠ 실제 동기" 3층 분리. 산문 깊이의 핵심.**

### D. 한국 정서 렌더링 (한/정/심마/기)
- 단순 라벨이 아니라 행동 명세:
  - 한: "울지 않는다. 한숨 쉰다. 밥상 차리고 '많이 먹어'라고 한다."
  - 정: "고백하지 않는다. 밥을 하나 더 차린다."
  - 심마: "좋은 일 생기면 더 강해진다. '넌 이럴 자격 없어.'"
  - 기: "팔에서 힘 빠지고, 등 구부러지고, 말끝 흐려진다."
- **평가: 구체적 행동 가이드. LLM에게 매우 효과적.**

### E. Scheherazade + Information Gap = 서사 지속력
- Loewenstein의 호기심 이론 → "매 장면 최소 1개 미해결 질문"
- narrative_chain.chain_status로 OPEN/CLOSED/DORMANT 추적
- Telescope [Hook] 게이트로 "종결 없이 끝남" 검증
- **평가: 학술적 근거와 실용적 메커당즘 정합.**

### F. Logos Dynamics (커스텀) — 관계 역학의 핵심
- Monolithic(핵심 신념, 고관성) / Transient(기분, 저관성) / Membrane(신뢰 경계)
- 신뢰: 선형 구축, 배신 시 즉각 붕괴
- 우뇌 렌더링 가이드:
  - Monolithic 노출 = "자기 말에 놀란 얼굴. 즉시 철회."
  - Membrane crack = "무의도적 진실 누출 → 즉시 사르카즘으로 커버"
- **평가: 확립된 이론의 빈자리를 메우는 잘 설계된 커스텀 프레임워크.**

---

## 3. 이론적으로 맞지만 실전 문제 있는 것들

### A. 46개 이론 + 턴마다 랜덤 스포트라이트 5개
- `NON_SLOT_THEORIES` 34개에서 매 턴 `random.sample(5)`
- 문제: 동일 NPC의 동일 행동이 턴마다 다른 이론 렌즈로 해석 가능
- 턴 1: Prospect Theory → "손실 회피" / 턴 2: Reactance → "자유 저항"
- Flash는 이전 턴 분석 기억 안 함 → 분석 일관성 위험
- **제안: 세션 시작 시 NPC별 주 이론 2-3개 고정, 스포트라이트는 보조적으로만**

### B. Peplau 단계 건너뛰기 금지 vs TRPG 페이싱
- orientation(0-3턴) → identification(3-8) → exploitation(8-15) → resolution(15+)
- TRPG에서 15턴 = 세션 2-3개 분량. 플레이어 답답함 가능
- **실제 코드에서 Peplau phase 강제 메커니즘 없음** — 선언만 있고 검증 없음
- **제안: 엄격 강제보다 "속도 제한"으로 — 1턴 최대 phase 진전 1, 후퇴는 무제한**

### C. 이론→데이터→산문 이중 번역 정보 손실
- 이론(Polyvagal dorsal) → Flash 출력(soma.polyvagal: "dorsal") → Renderer(얼어붙는 묘사)
- Flash가 "dorsal"만 표기하면 WHY가 사라짐 (트라우마? 과부하? 학습된 무력감?)
- Renderer는 변환표(PSYCHE_STATE_RENDERING)만 봄, 이론을 이해하는 게 아님
- deep_read 필드가 이 갭을 부분적으로 메움
- **구조적 한계이나 치명적이진 않음**

---

## 4. 충돌/의심스러운 부분들

### A. theory_mod (judgment_engine.py) 시뮬레이션 오류
```python
polyvagal == "dorsal" → combat +10  # ❌ 셧다운 상태에서 전투 보너스?
cultural_affect == "han" → social +5  # ❌ 축적된 슬픔이 사회적 보너스?
```
- dorsal = 자발적 행동 불가 → 전투 보너스 모순
- 한 = 사회적 위축 경향 → +5 근거 약함
- **수정 필요 (시뮬레이션 정확도 직접 영향)**

### B. Cartesian Dualism 선언 vs 실제 메커니즘
- THEORIA_IDENTITY: "soma와 psyche는 독립 트랙"
- 하지만 mental_impact가 이변→vigor/composure 직접 영향 (soma→psyche 연결)
- 의도된 설계("독립 추적하되 상호 영향")인지 모순인지 불명확
- **명시적 정리 필요: "독립 추적, 간접 영향" 같은 선언**

### C. 장르 REFRAME 지시의 모호성
- `"Attachment: read as LOYALTY patterns"` (wuxia)
- Flash에게 실제로 뭘 하라는 건지 불분명
- attachment 필드에 "secure" 쓰되 충성 의미? / 새 개념? / 무시?
- **REFRAME이 가장 추상적 지시 — Flash 해석에 전적으로 의존**

---

## 5. 핵심 8축 vs 보조 이론

### 산문 품질에 직접적 영향을 주는 핵심 이론 (8개)
1. **Polyvagal** — 신체 상태 기반 행동 렌더링
2. **Attachment** — 관계 역학 엔진
3. **Four-Layer + Logos** — 캐릭터 심층 구조
4. **Self-Opacity** — 이중 신호 생성
5. **한/정/심마/기** — 한국 정서 구체적 렌더링
6. **Desistance/Dark Triad** — 변화 저항 시뮬레이션
7. **Scheherazade/Information Gap** — 서사 지속력
8. **Objective Correlative** — 감정→물질 변환

### 보조 이론 (38개) — 있으면 좋지만 필수 아님
- Flash 주의력 분산, 토큰 낭비, 환각 위험 증가 가능
- 장르별 가중치(EMPHASIZE/SUPPRESS)가 부분적으로 관리
- **제안: 핵심 8축 출력 필수 + 보조 이론은 장르별 3-5개로 축소**

---

## 6. 장르별 가중치 시스템 평가

### 강점
- EMPHASIZE > SUPPRESS 충돌 해소 규칙 명확
- 장르 조합(Stage×Flavor×Lens)에 따른 동적 가중치 잘 설계
- 조건부 모듈(Forensic/Negotiation/Group/Cosmic Horror) 장르 태그로 자동 활성화

### 약점
- 3개 이상 장르 혼합 시 REFRAME 우선순위 불명확
- `dedup()` 함수가 이론 이름의 첫 단어만으로 중복 판단 → 이름이 비슷한 다른 이론 충돌 가능
- SUPPRESS된 이론도 NON_SLOT_THEORIES의 랜덤 스포트라이트에 잡힐 수 있음 → 가중치 무효화
