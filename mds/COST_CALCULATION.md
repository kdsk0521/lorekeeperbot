# Lorekeeper API 비용 계산서

> **최종 업데이트**: 2026-03-09 (코드화 반영 — PACING/MEMORY/TIME 테이블→iceberg 조건부 주입, NPC/ANTI_CLICHE/MW/PROSE 축소)

---

## 1. 가격표 (유료 등급)

| 모델 | 용도 | 입력 ($/1M토큰) | 출력 ($/1M토큰) |
|------|------|----------------|----------------|
| **Gemini 3 Flash Preview** | Theoria + Physical + Batch + 발효 | $0.50 | $3.00 |
| **Gemini 3.1 Pro Preview** | Main Renderer (≤200K 프롬프트) | $2.00 | $12.00 |
| Gemini 3.1 Pro Preview | Main Renderer (>200K 프롬프트) | $4.00 | $18.00 |

> Flash 무료 등급 사용 시 Flash 비용 = $0. 단, RPM/TPM 제한 있음.

---

## 2. 시나리오 파라미터

| 파라미터 | 값 | 비고 |
|----------|-----|------|
| **플레이어 수** | 1명 | `NARRATIVE_CHARS_BASE(1500) + PER_PLAYER(800) = 2,300자` |
| **로어북 용량** | 최대의 70% | ~7 청크 × 4,000자, Theoria가 5개 선택 |
| **NPC 수** | 19명 | 5명 장면별 섹션 선택(~6,000-7,000자) + 14명 이름만 |
| **모듈** | 전부 ON | Storyteller + Doom + Vigor/Composure + NPC Autonomous |
| **장면 명령어** | ON | scene_directive 추가 (~200토큰) |
| **발효 트리거** | 24 히스토리 ≈ 12턴마다 | `FRESH_THRESHOLD=24`, `FERMENT_CHUNK_SIZE=12` |
| **DEEP 압축** | ~96턴마다 | 8회 발효 후 (`FERMENTED_THRESHOLD=8`) |

---

## 3. Pro 렌더러 구조 (컨텍스트 윈도우)

Pro 렌더러는 `system_instruction`을 사용하지 않음. 34-slot 프롬프트가 **매 턴 세션 재생성**되어
대화 히스토리의 Message 1로 삽입됨. 따라서 **전체 contents 배열이 매 턴 입력 토큰으로 과금**.

```
contents 배열 (매 턴 전송):
├── Message 1 (user):  34-Slot 프롬프트 전체 (정적+동적)
├── Message 2 (model): "[SYSTEM] Standing by..."
├── Message 3 (user):  TRAINING_USER_PROMPT
├── Message 4 (model): TRAINING_MODEL_RESPONSE
├── Message 5~N:       smart_history (최소 6턴 = 12메시지)
├── Message N+1 (user): 현재 사용자 입력
└── Message N+2 (model): 텔레스코프 프리필 (모델이 이어서 생성)
```

**히스토리 관리**:
- `smart_history`: 최소 6턴(12메시지), `target_tokens=10,000` (BPE 추정) 이상까지 확장
- `_trim_history()`: 전체 contents ≤ 100,000자 제한, FIFO로 오래된 메시지 삭제 (Message 1-2 보존)
- CoT(텔레스코프 블록)는 히스토리 저장 시 자동 제거 → 모델 출력에서 서사만 남음

---

## 4. 턴당 API 호출 (4회 고정 + 비정기 발효)

### Call 1: THEORIA (Flash — 좌뇌 분석)

| 구성요소 | 토큰 | 비고 |
|----------|------|------|
| **시스템 프롬프트** | | |
| ├ Core theories (5개) | ~4,770 | IDENTITY+LENSES(4종) |
| ├ Rule tables (14개 상시) | ~5,910 | STATE_TRACK~ITEM_AWARE |
| ├ Conditional rules (~1개 평균) | ~1,100 | SEXUAL_PSYCH 또는 FLASHBACK |
| ├ Output schema | ~3,750 | 전체 DAI JSON 스키마 |
| ├ Theory emphasis + spotlight | ~1,420 | GENRE_WEIGHTS+modules+spotlight |
| └ Module absent guide | ~150 | |
| **시스템 프롬프트 소계** | **~17,100** | |
| **유저 메시지** | | |
| ├ MANDATE + "Begin analysis" | ~890 | contents[0] |
| ├ Model confirmation | ~43 | contents[1] |
| └ 분석 프롬프트 (_build_prompt) | ~7,500 | 아래 내역 |
| 　├ 현재 상태 + PC + 둠시계 + 조건 | ~1,300 | |
| 　├ NPC context (태도+지식 5명) | ~800 | |
| 　├ NPC 로스터 19명 | ~430 | |
| 　├ 히스토리 30메시지 | ~4,300 | RECENT_HISTORY_FOR_ANALYSIS=30 |
| 　├ 연속성 + 세션메모리 + 로어인덱스 | ~610 | |
| 　└ 사용자 입력 | ~60 | |
| **입력 합계** | **~25,533** | |
| **출력 (DAI JSON)** | **~2,000** | NPC 3명 기준 |

### Call 2: MAIN (Pro — 우뇌 렌더러)

| 구성요소 | 토큰 | 비고 |
|----------|------|------|
| **Message 1: 34-Slot 프롬프트** | | |
| ├ 정적 상수 (text_resources) | **~7,146** | ★ was 7,850 → 코드화 -704 |
| ├ Slot 6: PC 데이터 | ~430 | |
| ├ Slot 7: NPC 5명 장면별 + PIDGIN + 14명 | ~10,400 | |
| ├ Slot 8: 로어 청크 5개 | ~4,500 | |
| ├ Slot 9: 발효 기억 | ~600 | |
| ├ Slot 11: 챕터 컨텍스트 | ~300 | |
| ├ Slot 13-17: DAI 아이스버그+심리+장면+대사지시 | ~1,250 | ★ +50 (memory/time 조건부 주입) |
| ├ Slot 20: 상태 윈도우 | ~150 | |
| ├ Slot 22-24: 콘텐츠 수위 | ~200 | (조건부) |
| ├ Slot 27: 시간 우선순위 | ~43 | |
| ├ Slot 28: 서사 체인 | ~250 | ★ PACING 코드화 (iceberg) |
| ├ Slot 29: 실시간 데이터 | ~300 | |
| ├ Slot 30: GM 무버 | ~270 | |
| ├ Slot 31-33: 직전응답+입력+노트 | ~500 | |
| └ XML 태그+오버헤드 | ~200 | |
| **Message 1 소계** | **~26,539** | |
| **Message 2-4: 더미 + 트레이닝** | ~200 | |
| **Message 5+: smart_history** | **~10,000** | target_tokens=10,000 |
| **현재 사용자 메시지 + 프리필** | ~250 | |
| **입력 합계** | **~36,989** | |
| **출력 (텔레스코프 + 서사 + 태그)** | **~1,460** | |

> 텔레스코프 10게이트+5필드 (~600토큰, 실측 ~2,100자) + 서사 텍스트 (~710토큰) + 태그 오버헤드 (~150토큰)

#### 정적 상수 내역 (코드화 후 실측)

| 슬롯 | 내용 | 글자 수 | 토큰 | 변경 |
|------|------|---------|------|------|
| 1 | MANDATE (CONTENT_AUTHORIZATION) | 3,101 | ~886 | |
| 2 | AI_CORE (+ PROFILE READING, WORLD RESPONSE) | 1,574 | ~450 | |
| 3 | MIRROR_WORKSHOP (+ CAMERA, SENSORY RULES) | 7,006 | ~2,002 | §P 압축 -169자 |
| 5 | WORLD_AXIOM (+ ACTION_RES, ASPECTS) | 1,312 | ~375 | MEMORY 코드화 |
| 10 | TEMPORAL_FLOW (10규칙만) | 1,104 | ~315 | TIME/DURATION 코드화 -391자 |
| 12 | INTERACTION + NPC_BEHAVIOR | 2,697 | ~771 | COMBAT/ATTITUDE 제거 -333자 |
| 18 | PC_AUTONOMY | 1,077 | ~308 | |
| 25 | ANTI_CLICHE + PROSE_CRAFT (+ KOREAN STYLE) | 4,525 | ~1,293 | §2/§4/§5+ROTATION 축소 -681자 |
| 34 | TELESCOPE_PROTOCOL | 2,614 | ~747 | |
| | **합계** | **25,010** | **~7,146** | **-2,461자 / -703 tok** |

> 해제된 슬롯: 4 (→Slot 3), 15 (→Slot 25 §5), 21 (→Slot 5)
> 코드화: PACING→iceberg.translate_energy_direction(), MEMORY→translate_memory_type(), TIME-OF-DAY→translate_time_atmosphere()

#### 조건부 상수 (콘텐츠 수위별)

| 상수 | 글자 수 | 토큰 | 조건 |
|------|---------|------|------|
| VISCERAL_CONTENT | 953 | ~272 | content_level=visceral |
| MATURE_CONTENT | 1,812 | ~518 | content_level=mature |
| HYBRID_CONTENT | 1,657 | ~473 | content_level=hybrid |
| OMNISCIENT_MODE | 862 | ~246 | content_level=omniscient |
| **최대 합계** | **5,284** | **~1,510** | (전부 ON은 불가, 실 사용 ~200-500) |

### Call 3: PHYSICAL FLASH (신체 추출)

| 구성요소 | 토큰 |
|----------|------|
| **입력** (지시 + MANDATE + 응답 + 상태) | **~1,300** |
| **출력** (JSON) | **~85** |

### Call 4: BATCH FLASH (사회/서사/퀘스트/세계/지문 추출)

| 구성요소 | 토큰 |
|----------|------|
| **입력** (5섹션 스키마 + MANDATE + 응답 + NPC 상태) | **~1,505** |
| **출력** (JSON) | **~433** |

### 비정기: 발효 (Flash, ~12턴마다)

| 구성요소 | 토큰 | 비고 |
|----------|------|------|
| **입력** (FERMENT_PROMPT 570 + MANDATE 886 + 12메시지 ~3,000) | **~4,500** | CHUNK_SIZE=12 |
| **출력** (요약 JSON) | **~150** | |

### 비정기: DEEP 압축 (Flash, ~96턴마다)

| 구성요소 | 토큰 | 비고 |
|----------|------|------|
| **입력** (DEEP_PROMPT 667 + MANDATE 886 + 8개 요약 ~1,500) | **~3,000** | THRESHOLD=8 |
| **출력** (압축 JSON) | **~1,000** | |

---

## 5. 턴당 비용 계산

### Flash (Call 1 + 3 + 4 + 발효 상각)

| | 입력 토큰 | 출력 토큰 |
|-|-----------|-----------|
| Theoria | 25,533 | 2,000 |
| Physical | 1,300 | 85 |
| Batch | 1,505 | 433 |
| 발효 상각 (÷12) | 375 | 13 |
| **소계** | **28,713** | **2,531** |

```
Flash 입력: 28,713 × $0.50 / 1,000,000 = $0.01436
Flash 출력:  2,531 × $3.00 / 1,000,000 = $0.00759
─────────────────────────────────────────────────
Flash 턴당 합계                         = $0.02195
```

### Pro (Call 2)

```
Pro 입력: 36,989 × $2.00 / 1,000,000 = $0.07398
Pro 출력:  1,460 × $12.00 / 1,000,000 = $0.01752
─────────────────────────────────────────────────
Pro 턴당 합계                          = $0.09150
```

### 턴당 총합

```
$0.02195 (Flash) + $0.09150 (Pro) = $0.11345 / turn
                                   ≈ ₩170 / turn (환율 ₩1,500)
```

### 이전 계산과 비교

```
구분          이전(02-27)  Diet(03-08)   통합(03-09)  코드화(03-09)
정적 슬롯      14,135        8,300        7,850        7,146     ← 코드화 -704
동적 슬롯      17,590       19,490       19,340       19,390     ← +50 (iceberg 주입)
히스토리       10,000       10,000       10,000       10,000
기타              600          500          453          453
──────────────────────────────────────────────────────────
Pro 입력합     42,325       38,290       37,643       36,989
발효 상각         180          180          375          375
턴당 비용      $0.1265      $0.1155      $0.1148      $0.1135
```

> 통합(03-09) 대비 Pro 입력 -654 tok 절감 (정적 -704, 동적 +50).
> 코드화의 목적: 매 턴 불필요한 정적 테이블 제거 → 해당 에너지/기억/시간에 맞는 힌트만 주입.

---

## 6. 누적 비용표

| 턴 수 | Flash | Pro | **합계 (USD)** | **합계 (KRW)** |
|--------|-------|-----|---------------|---------------|
| **100** | $2.20 | $9.15 | **$11.35** | **₩17,000** |
| **200** | $4.39 | $18.30 | **$22.69** | **₩34,000** |
| **300** | $6.59 | $27.45 | **$34.04** | **₩51,100** |
| **400** | $8.78 | $36.60 | **$45.38** | **₩68,100** |
| **500** | $10.98 | $45.75 | **$56.73** | **₩85,100** |

> DEEP 압축 비용 (~$0.003/회, ~96턴마다) 포함 시 500턴 +$0.02 — 무시 가능

---

## 7. 비용 분해 비율

```
전체 비용 중:
├── Pro 입력 (Main 프롬프트+히스토리) 65.2%  ← 최대 비용 드라이버
│     ├── NPC 프로필 (Slot 7)         18.3%  ← 단일 최대 항목
│     ├── 히스토리 (smart_history)    17.6%
│     ├── 정적 상수 (text_resources)  12.6%  ← diet+통합+코드화 완료
│     ├── 로어 (Slot 8)               7.9%
│     └── 기타 동적 슬롯              8.8%
├── Pro 출력 (서사+텔레스코프)        15.5%
├── Flash 입력 (Theoria+추출)        12.7%
├── Flash 출력 (DAI+추출 JSON)        6.7%
└── 발효 상각                         2.0%
```

**최적화 우선순위**: NPC 프로필(18%) ≈ 히스토리(18%) > Pro 출력(16%) > 정적 상수(13%, 완료) > Flash(13%)

---

## 8. Flash 무료 등급 사용 시

Flash를 무료 등급으로 사용하면 Flash 비용 = $0:

| 턴 수 | Pro만 (USD) | Pro만 (KRW) | 절감률 |
|--------|------------|------------|--------|
| **100** | $9.15 | ₩13,700 | 19.4% |
| **200** | $18.30 | ₩27,500 | 19.4% |
| **300** | $27.45 | ₩41,200 | 19.4% |
| **400** | $36.60 | ₩54,900 | 19.4% |
| **500** | $45.75 | ₩68,600 | 19.4% |

> 단, 무료 등급 RPM 제한으로 턴당 3-4회 Flash 호출이 병목될 수 있음

---

## 9. 변수별 비용 민감도

| 변수 | 변경 | 턴당 영향 | 500턴 영향 |
|------|------|-----------|-----------|
| NPC 19→10명 | Slot 7 축소 ~2,000토큰 | -$0.004 | -$2.00 |
| NPC 프로필 장면선택→2000자압축 | Slot 7 축소 ~5,000토큰 | -$0.010 | -$5.00 |
| 로어 70%→30% | Slot 8 축소 ~2,500토큰 | -$0.005 | -$2.50 |
| 로어 0% | Slot 8 = 0 | -$0.009 | -$4.50 |
| 플레이어 1→2명 | 출력 +226토큰 | +$0.003 | +$1.36 |
| 발효 해제 | Slot 9 = 0, 발효 호출 0 | -$0.003 | -$1.50 |
| Theoria 히스토리 30→20 | -~1,400 Flash입력 | -$0.001 | -$0.35 |
| 텔레스코프 OFF | Pro 출력 -~400토큰 | -$0.005 | -$2.40 |
| smart_history 6턴→4턴 | Pro입력 -~3,300토큰 | -$0.007 | -$3.30 |
| **최소 구성** (NPC 5, 로어 0, 히스토리 4턴) | | **~$0.065** | **~$32.50** |

---

## 10. 비용 변경 이력

| 날짜 | 변경 내용 | 절감 (토큰/턴) | 절감 ($/턴) |
|------|----------|---------------|------------|
| 2026-02-24 | text_resources 슬림화 (~2,500토큰) | ~2,500 Pro입력 | ~$0.005 |
| 2026-02-24 | GENRE_MC_MOVES 제거 (~150줄) | ~100 Flash입력 | ~$0.0001 |
| 2026-02-24 | STATUS_WINDOW_LAYOUT 제거 | ~600 Pro입력 | ~$0.0012 |
| 2026-02-25 | NPC 장면별 섹션 선택 (5000자→~6500자 원문) | +~3,200 Pro입력 | +~$0.0064 |
| 2026-02-27 | Telescope v4 (Attractor, Scheme, Gravity, Unshown) | +~95 Pro입력, +~60 Pro출력, +~8 Flash | +$0.0010 |
| 2026-02-27 | Misel 5개 개념 채택 (Dialogue Boundary 등) | +~200 Pro입력 | +$0.0004 |
| 2026-03-08 | Token Diet: 정적 슬롯 압축 46% | ~5,835 Pro입력 | ~$0.0117 |
| 2026-03-08 | Cost-Diet: 발효 주기 변경 (÷25→÷12) | +195 Flash입력 | +$0.0001 |
| 2026-03-09 | 상수 통합: 8개→부모 모듈, Slot 4/15/21 해제 | ~647 Pro입력 | ~$0.0013 |
| **2026-03-09** | **코드화: PACING/MEMORY/TIME→iceberg, NPC/ANTI/MW/PROSE 축소** | **~654 Pro입력** | **~$0.0013** |
| | 누적 | Diet+통합+코드화: ~7,136 Pro | Diet+통합+코드화: ~$0.016 |

---

## 11. 계산 공식 (재계산용)

```
턴당_비용 = (Flash입력토큰 × Flash입력단가) + (Flash출력토큰 × Flash출력단가)
          + (Pro입력토큰 × Pro입력단가) + (Pro출력토큰 × Pro출력단가)
          + (발효비용 / 12) + (DEEP비용 / 96)

N턴_비용 = 턴당_비용 × N

Flash입력토큰 = Theoria시스템(17,100) + Theoria유저(8,433) + Physical(1,300) + Batch(1,505)
Flash출력토큰 = DAI(2,000) + Physical(85) + Batch(433)

Pro입력토큰 = [Message 1: 34-Slot Prompt]
            + 정적상수(7,850) + PC(430) + NPC슬롯7(변동) + 로어슬롯8(변동)
            + 발효(600) + 챕터(300) + DAI아이스버그(1,200) + 상태윈도우(150) + 콘텐츠(200)
            + Slot27(43) + Slot28동적(250) + Slot29(300) + Slot30(270)
            + Slot31-33(500) + 오버헤드(200)
            + [Message 2-4: 더미+트레이닝](200)
            + [Message 5+: smart_history](10,000)
            + [현재 입력+프리필](250)
Pro출력토큰 = 텔레스코프(600) + 서사(710) + 태그(150)

NPC_슬롯7 ≈ PIDGIN(192) + (선택NPC수 × 장면별섹션~6500자/3.5) + (나머지NPC × 80/3.5) + (선택NPC × 보이스카드300/3.5)
           장면별섹션 = scene_type에 따라 Core+필요섹션만 선택 (안전캡 15000자)
로어_슬롯8 ≈ 선택청크수 × 평균청크크기 / 3.5
smart_history ≈ 최소 6턴(12메시지), target_tokens=10,000 (BPE 추정)
```

---

## 12. 한국어 BPE 보정

> 위 계산은 `3.5자/토큰` (한영 혼합 보수적) 기준. 실제 Gemini 과금은 SentencePiece 토크나이저 기반.

| 콘텐츠 유형 | 3.5자/tok | Gemini BPE | 보정 계수 |
|-------------|-----------|-----------|-----------|
| 영문 주도 (정적 슬롯) | ~3.8자/tok | ~3.8자/tok | ×1.0 |
| 한영 혼합 (NPC, DAI) | ~3.5자/tok | ~2.5자/tok | ×1.4 |
| 한글 주도 (히스토리, 서사) | ~3.5자/tok | ~2.0자/tok | ×1.7 |

실제 API 과금이 BPE 기반이라면, 한글 비중이 높은 항목(히스토리, NPC, 로어)의 실비용은 위 계산의 **1.3~1.5배** 수준 가능.

**BPE 보정 적용 시 추정**: ~$0.15/turn (≈ ₩225)

---

## 13. 슬롯 레이아웃 (V2.1 — 코드화 후)

```
=== PRIMACY (1-3) ===
 1  MANDATE (CONTENT_AUTHORIZATION)        886 tok
 2  AI_CORE (+ Profile Reading, World Response)  450 tok
 3  MIRROR_WORKSHOP (+ Camera, Sensory, §P압축)  2,002 tok
--- Slot 4: 해제 (→ Slot 3에 병합) ---

=== WORLD (5-9) ===
 5  WORLD_AXIOM (+ Action Res, Aspects)         375 tok   ← MEMORY 코드화
 6  PC_DATA                                    ~430 tok  [동적]
 7  NPC_ROLES (+ PIDGIN header)            ~10,400 tok  [동적]
 8  LORE                                    ~4,500 tok  [동적]
 9  FERMENTED_HISTORY                         ~600 tok  [동적]

=== CONTEXT (10-12) ===
10  TEMPORAL_FLOW (10규칙만)                    315 tok   ← TIME/DURATION 코드화
11  CHAPTER_CONTEXT                            ~300 tok  [동적]
12  INTERACTION + NPC_BEHAVIOR                  771 tok   ← COMBAT/ATTITUDE 제거
--- Slot 15: 해제 (→ Slot 25 §5에 병합) ---

=== COGNITION (13-17) ===
13  INPUT_ANALYSIS                             ~250 tok  [동적]
14  PSYCHE_STATES                              ~350 tok  [동적]
16  SCENE_INTELLIGENCE + memory/time iceberg   ~250 tok  [동적] ← +~50 조건부
17  EXTENDED_INTELLIGENCE                      ~200 tok  [동적]

=== RULES (18-25) ===
18  PC_AUTONOMY                                 308 tok
20  STATUS_WINDOW                              ~150 tok  [동적]
--- Slot 21: 해제 (→ Slot 5에 병합) ---
22  CONTENT_LEVEL                              ~200 tok  [조건부]
25  ANTI_CLICHE + PROSE_CRAFT (축소)          1,293 tok   ← §2/§4/§5+ROTATION 축소

=== CACHE BOUNDARY (26) ===

=== DYNAMIC (27-34) ===
27  TEMPORAL_PRIORITY                           ~43 tok  [동적]
28  NARRATIVE_CHAIN                            ~250 tok  [동적] ← PACING 코드화
29  REAL_TIME_DATA                             ~300 tok  [동적]
30  GM_MOVER                                   ~270 tok  [동적]
31  LAST_RESPONSE                              ~150 tok  [동적]
32  USER_INPUT                                  ~50 tok  [동적]
33  AUTHOR_NOTE                                ~200 tok  [동적]
34  TELESCOPE (747 tok) + prefill              ~847 tok  [정적+동적]
```

---

## 참고

- **토큰 환산**: 한영 혼합 텍스트 ~3.5자/토큰 (보수적). 한국어 BPE 보정 §12 참조
- **환율**: ₩1,500/USD (2026-03 기준, 변동 있음)
- **프롬프트 ≤200K**: 현재 Pro 입력은 ~38K로 200K 미만 → 낮은 단가 적용
- **Context Caching**: Gemini 캐시 사용 시 정적 상수(~7,850토큰) 재전송 비용 절감 가능 (현재 미사용)
- **사고 토큰**: Gemini 3.1 Pro의 사고(thinking) 기능 활성화 시 출력 토큰 대폭 증가 가능 — 현재 비활성 가정
- **세션 구조**: Pro는 system_instruction 미사용. 34-slot 프롬프트가 contents[0]으로 매 턴 재전송
