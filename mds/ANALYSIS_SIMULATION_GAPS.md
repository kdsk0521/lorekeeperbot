# 시뮬레이션/렌더링 갭 분석

## LLM 롤플레이 고질병 대응 현황

### 이미 잘 잡힌 것
| 고질병 | 대응 | 강제력 레벨 |
|--------|------|------------|
| PC 사칭 | 3-Layer (프롬프트+텔레스코프+코드 `_check_dialogue_format`) | **코드** |
| 쉬운 구원 | Dark Triad + Desistance + Recidivism | 프롬프트 |
| 감정 라벨링 | Camera Eye + James-Lange + 五蘊 | 프롬프트 |
| 지식 누출 | NPC Knowledge V2 + false_beliefs | Flash 분석 |
| 시공간 비약 | Tick 시스템 + Spatial 게이트 | 텔레스코프 |
| 진부한 표현 | Anti-Cliche Protocol + Rhetorical Rotation | 프롬프트 |
| 기억 소실 | Fermentation 3계층 + Smart History | 코드 + Flash |

---

## 미해결 갭 (우선순위순)

### GAP 1: 수렴(Convergence) 검출 부재 — 최대 문제

**현황**: Mirror Workshop "No Convergence" 선언만 있음. 코드 검출 없음.

**LLM 본성(RLHF)이 만드는 증상:**
- NPC가 3턴 만에 마음 열기
- 갈등 한 턴 만에 해소
- 적대 NPC가 PC 한마디에 동요
- 장면이 "따뜻한 이해"로 종결

**사칭 검출과의 비교:**
```
사칭:  코드 검출(_check_dialogue_format) → [IMPERSONATION] → 재생성 루프
수렴:  (없음) → 모델 자기 규율에 의존
```

**구현 방안 (사칭 패턴 재활용):**
1. cognition.py 배치 추출에서 NPC 태도 변화 속도 검출
2. 1턴 relation delta > +15 → `convergence_warning` 플래그
3. 다음 턴 Theoria에 피드백 주입 (format_feedback 패턴 동일)
4. 선택적: 렌더러에도 Slot 29에 `"⚠️ 수렴 경고: [NPC] 태도 변화 과속"` 주입

**난이도: 중 / 임팩트: 최상**

---

### GAP 2: theory_mod 시뮬레이션 오류 — judgment_engine.py

**현재 코드 (잘못된 매핑):**
```python
# Polyvagal
if polyvagal == "dorsal":
    mod += 10 if is_combat else -10  # ❌ 셧다운=전투보너스?

# Cultural affect
if cultural_affect == "han" and is_social:
    mod += 5  # ❌ 축적된 슬픔=사회적 보너스?
```

**이론 기반 수정안:**
```python
# Polyvagal
if polyvagal == "dorsal":
    mod -= 15 if is_combat else -10  # 셧다운: 전투/사회 모두 불리
elif polyvagal == "sympathetic":
    mod += 5 if is_combat else -5    # 투쟁도주: 전투 유리, 사회 불리
elif polyvagal == "ventral":
    mod += 5 if is_social else 0     # 안전: 사회 유리

# Cultural affect
if cultural_affect == "han":
    mod -= 3 if is_social else 0     # 한: 사회적 위축
elif cultural_affect == "jeong" and is_social:
    mod += 5                          # 정: 사회적 유대 강화
elif cultural_affect == "hwabyung":
    mod -= 8 if is_social else -3    # 화병: 사회적 폭발 위험
elif cultural_affect == "nunchi" and is_social:
    mod += 3                          # 눈치: 사회적 상황 파악 이점
elif cultural_affect == "chaemyeon" and is_social:
    mod += 5                          # 체면: 사회적 수행력
elif cultural_affect == "gi" and is_combat:
    mod += 5                          # 기: 전투 활력
```

**난이도: 하 / 임팩트: 상**

---

### GAP 3: 클리셰 코드 레벨 검출

**현재**: Anti-Cliche Protocol은 프롬프트 레벨만. 텔레스코프 [Cliche] 게이트는 모델 자가 판정.

**코드로 올릴 수 있는 패턴:**
```python
CLICHE_PATTERNS = [
    r"형언할 수 없는",
    r"전기가 흐르[는듯]",
    r"심장이 멎[는은]",
    r"시간이 멈[춘추]",
    r"숨을? ?잊[었은]",
    r"등줄기를 타고.{0,5}한기",
    r"포식자 같은",
    r"살기가? ?느껴",
    r"보이지 않는 압박",
    r"모든 것이 달라[질지]",
    r"운명을? ?결정짓",
]
```

**구현**: response_processor.py에서 사칭 검출과 동일 패턴
- 매칭 시 `[CLICHE: "패턴"]` 피드백 → 다음 턴 format_feedback에 추가
- 재생성까지는 불필요 — 다음 턴 교정이면 충분

**난이도: 중 / 임팩트: 상**

---

### GAP 4: 감정 강도 캘리브레이션

**문제**: psyche value 0-100 스케일 존재하지만, Renderer가 저강도(0-30)를 진짜 조용하게 쓰는지 강제 불가. LLM은 모든 장면을 "의미심장하게" 씀.

**PSYCHE_STATE_RENDERING 정의:**
```
0-30:  미세한 미시표정, 겨우 감지 가능
30-60: 눈에 띄는 바디랭귀지
60-80: 명백한 신체 신호, 숨기기 불가
80-100: 압도적 신체 반응, 이성 상실
```

**구현 방안:**
- Slot 29(REAL_TIME_DATA)에 현재 NPC별 감정 강도 레벨 명시:
  ```
  ⚠️ 감정 강도 가이드:
  [NPC_A] psyche 22 → SUBTLE (미세 표현만)
  [NPC_B] psyche 71 → OVERT (명백한 신체 신호)
  ```
- 또는 Telescope에 `[Intensity]` 게이트 추가
- 사후 검출: Flash에 1줄 호출 "이 산문의 감정 강도가 데이터와 일치하는가?" → 불일치 시 피드백

**난이도: 중 / 임팩트: 상**

---

### GAP 5: NPC 음성 구별

**현재**: NPC 심리는 4축으로 정교하게 추적. 하지만 대사의 실제 음성(말투, 어감, 화법)은 compact된 프로필 2000자에 의존.

**증상**: 다른 심리 상태의 NPC들이 비슷한 어투로 말함.

**구현 방안:**
- NPC 프로필에 `speech_sample` 필드 추가 (2-3줄 예시 대사)
- 또는 `speech_register` 태그: `{"formality": "반말", "dialect": "경상", "tempo": "느림", "quirk": "말끝 흐림"}`
- Slot 7(NPC)에 프로필과 함께 음성 가이드 포함
- Theoria 분석에서 NPC별 `speech_pattern` 요약 출력 추가 가능

**난이도: 하 / 임팩트: 중**

---

### GAP 6: 긍정 편향(Sycophancy) 대항

**현재**: Want/Do/Can 모델은 이론적으로 훌륭하지만, Renderer가 Can을 가혹하게 적용하는지 검증 없음.

**문제**: Judgment 모듈 ON이면 주사위가 강제. **OFF이면** Renderer의 자발적 실패 렌더링에 의존 → RLHF 본성으로 "성공 편향"

**구현 방안:**
- Judgment OFF 상태에서도 Theoria의 position/effect 값 활용
- Position < 0.3 (Desperate/Risky)이면 Slot 30에 `"⚠️ 불리한 상황: 성공보다 마찰 우선"` 주입
- 환경 저항 레벨을 명시: `"환경 저항: HIGH — 물리적/사회적 장벽 존재"`

**난이도: 중 / 임팩트: 중**

---

### GAP 7: 텔레스코프 게이트 신뢰성

**현재**: 10게이트 모두 모델 자가 판정. MEMORY.md: "모델이 자기 산문에 FAIL을 잘 안 줌. 합리화."

**코드로 올릴 수 있는 게이트:**
| 게이트 | 코드화 가능? | 방법 |
|--------|------------|------|
| Impersonation | ✅ **이미 구현** | `_check_dialogue_format()` |
| Cliche | ✅ 가능 | 정규식 패턴 매칭 (GAP 3) |
| NPC Identity | ⚠️ 부분 가능 | 응답 NPC 이름 추출 → 프로필 role/location 교차 검증 |
| Intensity | ⚠️ 부분 가능 | Flash 1줄 호출로 강도 일치 확인 (GAP 4) |
| Physics | ❌ 어려움 | 물리 법칙 위반은 정규식으로 못 잡음 |
| Camera | ❌ 어려움 | "감정 라벨" vs "신체 묘사" 구분 자동화 어려움 |
| Hook | ❌ 어려움 | "끝이 열려있는지" 판단 어려움 |

**전략: 코드화 가능한 게이트 먼저 → 나머지는 Flash 사후 검증으로**

**난이도: 중~상 / 임팩트: 중**

---

## 구현 우선순위 매트릭스

```
임팩트
  ↑
최상 │ ① 수렴검출
     │
  상 │ ② theory_mod   ③ 클리셰검출   ④ 감정강도
     │
  중 │ ⑤ NPC음성      ⑥ 긍정편향     ⑦ 게이트강화
     │
  하 │
     └────────────────────────────────→ 난이도
       하          중          상
```

**권장 순서: ② → ① → ③ → ④ → ⑤ → ⑥ → ⑦**
(②가 가장 빠르고 확실한 수정, ①이 가장 큰 임팩트)

---

## 스포트라이트 일관성 문제 상세

### 현재
```python
def get_turn_spotlight(n=5):
    selected = random.sample(NON_SLOT_THEORIES, min(n, len(NON_SLOT_THEORIES)))
```
매 턴 34개 중 5개 완전 랜덤 → 분석 일관성 보장 안 됨.

### 개선안: 세션 고정 + 로테이션
```python
def get_session_spotlight(session_seed, turn_number, n=5):
    rng = random.Random(session_seed)  # 세션별 고정 시드
    # 34개를 7그룹(5개씩)으로 나눔, 턴마다 순환
    all_shuffled = NON_SLOT_THEORIES[:]
    rng.shuffle(all_shuffled)
    group_idx = turn_number % 7
    start = group_idx * n
    return all_shuffled[start:start+n]
```
- 같은 세션 내 7턴마다 전체 이론 1회전
- 세션 간 다른 조합 (seed 다름)
- NPC별 주 이론 2-3개는 별도 고정 (Theoria 프롬프트에 명시)
