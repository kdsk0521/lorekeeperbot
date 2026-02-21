# 이변 모듈 → 스토리텔러 리팩터 (세계 주도권 시스템)

## 왜 바꾸는가

이변(Anomaly) 모듈의 원래 의도는 **"예측 못한 사건이 일어난다"**.
지하수로의 괴물도, 썸녀와의 등굣길도, 문 앞에 놓인 봉투도 전부 "이변".

그런데 현재 구현은 **데미지 계산기**:
- Flash가 서사적으로 판단한 이벤트를 **85% 확률로 코드가 버림** (15% 고정 난수)
- 방어롤/데미지가 산문에 실질적 기여 없음 (숫자만 변경)
- energy_direction 무시 — idle에서도 detonation에서도 동일 확률
- 판정(Judgment)과 같은 턴에 충돌해도 둘 다 발동

AI 채팅 서사의 근본 문제: **유저가 동전을 넣어야 세상이 돌아간다**.
유저가 행동하지 않으면 NPC는 얼어붙고, 날씨는 안 바뀌고, 서브플롯은 정지.

## 해법: 림월드 스토리텔러

림월드의 카산드라/랜디처럼 — **코드가 이벤트 스케줄러** 역할.

- **Flash** = 이벤트 풀 (무엇이 일어날 수 있는지 제안)
- **코드** = 스토리텔러 AI (언제, 어떤 순서로 발동할지 결정)
- **Main 모델** = 실제 산문 렌더링 (이벤트를 장면으로 변환)

---

## 핵심 전환표

| 현재 | 전환 후 |
|---|---|
| 15% 난수 트리거 | energy_direction × 턴 간격 기반 **타이밍 테이블** |
| 방어롤 + 데미지 | **제거** (mental_impact이 이미 담당) |
| 적응도 = 면역력 (damage mitigation) | **다양성 추적** = 같은 유형 반복 방지 |
| 발동 아니면 폐기 | **이월 큐** (적절한 턴까지 보관) |
| vigor/composure delta 강제 적용 | **제거** (스토리텔러는 스케줄러, 데미지 계산기 아님) |
| Flash 판단을 코드가 85% 차단 | Flash 판단을 존중, **타이밍만 코드가 조절** |

---

## 타이밍 테이블 (카산드라 커브)

energy_direction × 마지막 이벤트 이후 경과 턴 → 결정

```
              turns_since_last_event
              0턴     1턴     2턴     3턴+
idle        defer   defer    ACT     ACT      ← 조용하면 세상이 움직임
stagnant    defer    ACT     ACT     ACT      ← 정체면 빨리 개입
rising      defer   defer   defer    ACT      ← 긴장 상승 중, 신중하게
detonation  skip    skip    defer   defer     ← PC가 바쁨, 기다림
aftershock  defer   defer    ACT     ACT      ← 여파 속 조용한 변화
```

**오버라이드 규칙:**
- 판정(Judgment) 활성 → 무조건 defer (장면 과부하 방지)
- 큐 이벤트 3턴+ 보류 → 강제 ACT (기아 방지)
- 클라이맥스(Doom) → 강제 ACT
- 제안도 없고 큐도 비어있음 → skip

**결정의 의미:**
- `ACT` → 이벤트 발동, 디렉티브에 주입
- `defer` → 큐에 이월, 다음 턴에 재심사
- `skip` → 이번 제안 폐기 (detonation 중 등)

---

## DAI 연동 — 맥락 기반 보정 (개연성/핍진성)

타이밍 테이블은 "언제"를 결정. DAI 연동은 **"이것이 지금 말이 되는가"**를 판단.
로어에서 이변씨앗을 만드는 이유 자체가 세계 내 근거를 확보하기 위함 — DAI가 그 근거를 실시간으로 제공.

### 승격 조건 (defer → ACT)
| DAI 필드 | 조건 | 효과 |
|---|---|---|
| `quality_flags.stagnation_warning` | True (3턴+ 정체) | defer → ACT 승격 |
| `quality_flags.convergence_warning` | True (안일한 수렴) | defer → ACT 승격 |
| `npc_knowledge.leak_risk` | "high" NPC 존재 | 정보 유출 이벤트 승격 |
| `psyche_states` | NPC 감정 극단 상태 | npc_initiative 승격 |

### 억제 조건 (ACT → defer)
| DAI 필드 | 조건 | 효과 |
|---|---|---|
| `scene_type` | "combat" / "rest" | 비관련 이벤트 defer |
| `judgment.active` | True | 이미 테이블에 반영 (defer) |

### 폴라리티 선호 (이벤트 선택 시 가중치)
| DAI 필드 | 조건 | 효과 |
|---|---|---|
| `position` | weak | positive 이벤트 +0.2 가산점 (숨통) |
| `position` | strong | negative 이벤트 +0.2 가산점 (도전) |
| `convergence_warning` | True | negative/mixed +0.3 (수렴 깨기) |

### scene_type별 허용 이벤트 카테고리
```
normal      → 전체 허용
combat      → skip (전투 중 이변은 과부하)
social      → social/environmental/temporal 선호
exploration → supernatural/environmental/temporal 선호
rest        → environmental만 허용 (조용한 변화)
```

**구현**: `process()` 내에서 `bus.dai`를 읽어 테이블 결과에 보정 적용.
추가 API 호출 0. bus.dai는 Theoria가 이미 채운 상태.

---

## 이벤트 선택 로직 (ACT 결정 시)

후보 = 큐에 보관된 이벤트들 + Flash의 이번 턴 제안

**다양성 점수** (0.0 ~ 1.0):
- 최근 5개 이벤트와 **같은 tag** → -0.5 (강한 감점)
- 최근 5개 이벤트와 **같은 category** → -0.3 × 중복 횟수
- 기본 점수 1.0에서 감점

**폴라리티 보정** (DAI 기반):
- position weak + positive 이벤트 → +0.2
- position strong + negative 이벤트 → +0.2
- convergence_warning + negative/mixed → +0.3

**scene_type 필터**: 허용되지 않는 카테고리의 이벤트는 후보에서 제외

**선택**: 최고 점수 이벤트. 동점 시 큐 이벤트 우선 (FIFO 공정성).

---

## 스토리텔러 상태 (per-channel, world_state 내 저장)

```python
"storyteller": {
    "last_event_turn": 0,        # 마지막 이벤트 발동 턴
    "recent_categories": [],      # 최근 5개 카테고리 (다양성 추적)
    "recent_tags": [],            # 최근 5개 태그 (다양성 추적)
    "event_queue": [],            # 이월된 이벤트 큐 (최대 5개)
    "total_events_fired": 0,      # 세션 내 총 발동 횟수
}
```

---

## Flash 스키마 변경 (anomaly_profile 간소화)

### 제거 필드
- `disruption_axis` — 데미지 라우팅 용도, 더 이상 불필요
- `adaptation_group` — 면역력 계산 용도, 다양성 추적으로 대체
- `theory_basis` — 방어롤 보정 용도, 방어롤 자체 제거
- `defense_hint` — 방어 힌트, 방어롤 제거로 불필요

### 간소화 후
```
"anomaly_profile": {
    "trigger": str,
    "category": "supernatural/psychological/social/environmental/temporal",
    "intensity": "Low/Mid/High/Extreme",
    "polarity": "positive/negative/mixed",
    "perception_type": "veridical/illusory/hallucinatory/delusional/null",
    "line": "Korean - 이변의 서사적 묘사 1문장",
    "reason": "Korean"
} | null
```

---

## 수정 파일 (8개)

| # | 파일 | 변경 내용 |
|---|---|---|
| 1 | `config.py` | 상수 교체 (ANOMALY_BASE_CHANCE → STORYTELLER_*) + DEFAULT_WORLD_STATE 확장 |
| 2 | `domain_manager.py` | 스토리텔러 CRUD 2함수 추가 (get/update_storyteller_state) |
| 3 | `orchestration_context.py` | SharedBus anomaly 기본값 간소화, vigor에서 adaptation 제거 |
| 4 | `anomaly_module.py` | **전면 재작성** — 데미지 계산기 → 타이밍/큐/다양성 스케줄러 |
| 5 | `waterfall_pipeline.py` | 파싱 간소화 + 스토리텔러 상태 주입 + post-anomaly doom sync 제거 |
| 6 | `theoria_analyzer.py` | anomaly_profile 스키마에서 4필드 제거 |
| 7 | `une_facade.py` | 이벤트/인트루전 레이어 간소화 + 적응 제거 + 배치 단순화 |
| 8 | `doom_module.py` | 클라이맥스 → 큐 push 방식 전환 |

---

## 세부 변경 사항

### anomaly_module.py (핵심 — 전면 재작성)

**제거** (~200줄):
- `calculate_adaptation()` — 2단계 면역력 계산
- `_resolve_disruption_axis()` — vigor/composure 데미지 라우팅
- `_roll_defense()` — 패시브+아이템+스탯+이론 방어롤
- `_calculate_trigger_chance()` — 15% 고정 트리거
- `process()` 내 데미지/방어/적응 로직 전체

**유지**:
- `_normalize_intensity()` — Flash 출력 정규화 (Korean↔English)
- `_normalize_polarity()` — 동일

**새 `process()` 흐름:**
1. bus에서 Flash 제안 읽기 (tag, category, intensity, polarity, line)
2. storyteller_state 로드 (`bus.anomaly["_storyteller_state"]`)
3. turns_since = current_turn - last_event_turn
4. 타이밍 판정 (오버라이드 → 테이블 순서)
5. ACT → 이벤트 선택 (큐+제안 중 다양성 점수 최고), `bus.anomaly.triggered = True`
6. defer → 큐에 이월, `bus.anomaly.triggered = False`
7. skip → 무시, `bus.anomaly.triggered = False`
8. storyteller_state 업데이트 후 domain_manager에 저장

**클래스 이름**: `AnomalyModule` 유지 (import 호환성)

### waterfall_pipeline.py

- anomaly_profile 파싱에서 `adaptation_group` 로딩 제거
- anomaly_seeds 폴백에서 axis/adaptation_group 부분 제거
- anomaly 실행 전 storyteller_state를 bus.anomaly에 주입
- Post-Anomaly Doom Sync 블록 (line 230-242) 전체 제거
- 파이프라인 로그에서 defense_result → decision/decision_reason

### une_facade.py

- `_build_adaptation_line()`, `_build_adaptation_result_line()` 함수 제거
- `convert_to_game_context()`: abnormal_exposure → bus.vigor["adaptation"] 로딩 제거
- `sync_from_game_context()`: adaptation_update → abnormal_exposure 동기화 제거
- `_build_events_layer()`: defense_note, adapt_pct, output 제거
- `_build_system_message()`: defense_note 제거
- Layer 3 Intrusion: output, escalated, 적응 판정 결과 섹션 제거. **장르별 프레이밍은 유지**
- `_combine_batch_results()`: adaptation_lines 수집/출력 제거
- 배치 경로: `skip_trigger` → `_skip_storyteller`, per-PC 데미지/적응 없음

### doom_module.py

- `_trigger_climax()`: `skip_trigger` → 큐에 직접 push 방식
  (doom은 step 5, anomaly는 step 4이므로 같은 턴에 반영 불가 → 다음 턴 큐)

---

## 분기점 생성기로서의 역할

스토리텔러가 이벤트를 발동하면 → 유저가 반응해야 하는 상황 → 유저의 반응이 곧 분기 선택.
명시적 선택지 나열이 아닌, **상황 자체가 반응을 요구하는** 형태로 자연스럽게 작동.

```
세상이 행동: "골목에서 비명이 들린다"
  └─ 유저가 달려간다     → 전투/구출 분기
  └─ 유저가 무시한다     → 나중에 consequence로 재등장
  └─ 유저가 주변을 살핀다 → 탐색 분기
```

유저가 아무것도 안 하면 → idle/stagnant 감지 → 세상이 먼저 움직임 → 분기점 생성.
기존 산문 지침(선택지 나열 금지 등)과 충돌 없음.

---

## 토큰 영향

| 항목 | 변화 |
|---|---|
| Flash 스키마 | -4필드 (약 -80 토큰) |
| Flash 출력 | adaptation_group 없어져서 약간 감소 |
| Slot 디렉티브 | defense/adaptation 라인 제거 (약 -60 토큰) |
| Discord 시스템 메시지 | 적응 판정 결과 제거 → 간소화 |
| **총합** | 기존 대비 **약간 감소** (순수 절약) |

---

## 검증 계획

1. `py_compile` 전체 수정 파일 8개
2. 타이밍 테이블: energy_direction 5개 × turns_since 0-3 = 20 조합 확인
3. 큐 FIFO: 이월 → 다음 턴 발동 → 큐 비워짐
4. 다양성: 같은 tag 연속 제안 → 다른 이벤트 선택
5. 기아 방지: 큐 이벤트 3턴 보류 → 강제 발동
6. 판정 충돌: judgment active 시 defer
7. 배치: 2PC 이상에서 스토리텔러 1회만 실행
8. 모듈 OFF: `anomaly not in active_modules` → 완전 스킵
9. 하위 호환: storyteller 키 없는 기존 세션에서 기본값 동작
