# Lorekeeper Bot v8.0 — 종합 연구 리포트

> 분석 일시: 2026-02-19
> 대상: lorekeeperbot/ 전체 코드베이스 (47 Python 파일, 20,688 LOC)
> 분석 범위: 아키텍처, 모듈 설계, 데이터 흐름, 코드 품질, 테스트, 잠재 이슈

---

## 목차

1. [프로젝트 개요](#1-프로젝트-개요)
2. [아키텍처 총론](#2-아키텍처-총론)
3. [핵심 시스템 심층 분석](#3-핵심-시스템-심층-분석)
4. [데이터 흐름 분석](#4-데이터-흐름-분석)
5. [코드 규모 및 복잡도 분석](#5-코드-규모-및-복잡도-분석)
6. [잠재적 이슈 및 리스크](#6-잠재적-이슈-및-리스크)
7. [테스트 커버리지 분석](#7-테스트-커버리지-분석)
8. [설계 강점 및 혁신점](#8-설계-강점-및-혁신점)
9. [개선 권장사항](#9-개선-권장사항)
10. [결론](#10-결론)

---

## 1. 프로젝트 개요

### 1.1 정체성
Lorekeeper Bot은 **Discord 기반 AI TRPG 내러티브 엔진**으로, Gemini-3 API를 활용하여 게임 마스터 역할을 수행합니다. 단순한 챗봇이 아닌, 심리학 이론 46개 + 문화 이론 11개를 기반으로 NPC 행동을 시뮬레이션하고 장르 적응형 서사를 생성하는 **인지-서사 통합 시스템**입니다.

### 1.2 핵심 지표

| 항목 | 수치 |
|------|------|
| 총 Python 파일 | 47개 (메인 36 + 테스트 11) |
| 총 코드 라인 | 20,688 LOC |
| 프롬프트 상수 | 73,966 bytes (text_resources.py) |
| 분석 리소스 | 45,914 bytes (analysis_resources.py) |
| API 호출/턴 | 2~4회 (Theoria + Main + Physical Flash + Batch Flash) |
| 지원 장르 | 14개 우산 장르 × 46개 이론 조합 |
| NPC 자율 트리거 | 9가지 심리학적 유형 |
| 텔레스코프 게이트 | 10개 자체 검증 단계 |

### 1.3 파일 규모 상위 10

| 파일 | 라인 수 | 책임 |
|------|---------|------|
| command_handler.py | 1,647 | Discord 명령어 처리 |
| fermentation.py | 1,568 | 장기 기억 압축 |
| text_resources.py | 1,394 | 프롬프트 상수 라이브러리 |
| une_facade.py | 1,315 | UNE 통합 진입점 |
| domain_manager.py | 1,274 | 데이터 영속화 |
| orchestration.py | 1,143 | 오케스트레이션 |
| game_character.py | 1,108 | 캐릭터 시스템 |
| slot_manager.py | 922 | 34슬롯 프롬프트 빌더 |
| config.py | 882 | 설정 및 테이블 |
| analysis_resources.py | 868 | 분석 프롬프트 리소스 |

---

## 2. 아키텍처 총론

### 2.1 Dual-Brain 아키텍처

```
┌─────────────────────────────────────────────────────────────┐
│                    사용자 Discord 메시지                      │
└──────────────────────────┬──────────────────────────────────┘
                           │
              ┌────────────▼────────────┐
              │     main.py (진입점)     │
              │  채널별 Lock + 라우팅    │
              └────────────┬────────────┘
                           │
              ┌────────────▼────────────┐
              │  orchestration.py       │
              │  7단계 파이프라인 조율   │
              └────┬───────────────┬────┘
                   │               │
      ┌────────────▼──┐    ┌──────▼──────────────┐
      │  LEFT BRAIN   │    │    RIGHT BRAIN       │
      │  (Flash 모델) │    │    (Pro 모델)         │
      │               │    │                      │
      │  theoria_     │    │  slot_manager.py     │
      │  analyzer.py  │    │  34슬롯 프롬프트      │
      │               │    │         ↓            │
      │  → JSON DAI   │───▶│  persona.py          │
      │  분석/관찰     │    │  서사 렌더링          │
      └───────────────┘    └──────────────────────┘
              │                        │
              │               ┌────────▼────────┐
              │               │ 응답 + Telescope │
              │               │ 제거 + 추출      │
              │               └────────┬────────┘
              │                        │
      ┌───────▼────────┐    ┌─────────▼────────┐
      │  cognition.py  │    │ 사용자에게 전달    │
      │  배경 추출     │    └──────────────────┘
      │  (Physical +   │
      │   Batch Flash) │
      └────────────────┘
```

**핵심 원칙**: Flash(분석/추출)와 Pro(렌더링)의 역할 분리로 토큰 효율 60% 개선

### 2.2 34-Slot 프롬프트 시스템

LLM의 Primacy/Recency 효과를 활용한 슬롯 배치 전략:

```
[Primacy Zone 1-4]     ← AI가 가장 강하게 기억 (정체성/철학)
│ 1. CONTENT_AUTHORIZATION_MANDATE
│ 2. AI_CORE_IDENTITY
│ 3. MIRROR_WORKSHOP_PROTOCOL (거울공방 8원칙)
│ 4. PHYSICAL_RENDERING_DOCTRINE
│
[World Zone 5-9]       ← 참조 데이터 (중간 배치)
│ 5. WORLD_AXIOM + MEMORY_HIERARCHY
│ 6. PC_DATA (솔로/다인 자동 분기)
│ 7. NPC_ROLES (Smart Loading: 풀 프로필 + 이름만)
│ 8. LORE (3-Priority RAG: Chunk > relevant > full)
│ 9. FERMENTED_HISTORY + MEMORY_TRIGGERS
│
[Context Zone 10-12]   ← 현재 상황
[Cognition Zone 13-17] ← Theoria 분석 데이터
[Rules Zone 18-25]     ← Static Recency (행동 규칙)
│
[=== CACHE BOUNDARY 26 ===]  ← Google API 캐시 경계
│
[Dynamic Zone 27-34]   ← 최강 Recency (실시간 + 최종 지시)
│ 27. OLDER_HISTORY
│ 28. NARRATIVE_CHAIN
│ 29. REAL_TIME_DATA (Doom, HP, 시간)
│ 30. GM_MOVER (+ 회상 지시)
│ 31. LAST_RESPONSE (SillyTavern 패턴)
│ 32. USER_INPUT
│ 33. AUTHOR_NOTE (+ 장르/포맷 피드백)
│ 34. TELESCOPE + LANGUAGE + EMOTION
```

**캐시 전략**: 슬롯 1-25는 동일 채널에서 캐시 재사용 → 토큰 비용 ~90% 절감

### 2.3 UNE v3.0 (Universal Narrative Engine)

```
┌──────────────────────────────────────────────────┐
│              UNE Waterfall Pipeline               │
│                                                  │
│  Step 1: Theoria.analyze_input()                 │
│    → bus.dai (position, psyche, needs_judgment)   │
│    → bus.anomaly (tag, intensity, adapt_group)    │
│    → bus.vigor/composure (impact)                 │
│                                                  │
│  Step 2: VigorComposure.prime()                  │
│    → bus.vigor/composure["stage"] = snapshot      │
│                                                  │
│  Step 3: AnomalyModule.process()                 │
│    → 15% 발동 → 적응도 계산 → 방어 롤           │
│    → bus.vigor/composure["delta"] -= 피해         │
│                                                  │
│  Step 4: Post-Anomaly Doom Sync                  │
│    → bus.doom["value"] += bus.doom["delta"]       │
│                                                  │
│  Step 5: DoomModule.process()                    │
│    → Clock Tick + 완성 → Doom +                  │
│    → bus.vigor/composure 압박 (Doom 기반)         │
│                                                  │
│  Step 6: VigorComposure.process()                │
│    → Rest Recovery + Judgment Emotion             │
│    → Inertia (10% 증폭) + Clamping (2 Stage)     │
│    → Trauma Awakening (Stage 3 → 90)              │
│                                                  │
│  Step 7: JudgmentEngine.process()                │
│    → 1d100 + 수정치 vs DC                        │
│    → Theory Modifier ±20                         │
│    → 5단계 결과 (Critical Success ~ Failure)      │
│                                                  │
│  Step 8: Sync → domain_manager 저장               │
└──────────────────────────────────────────────────┘
```

---

## 3. 핵심 시스템 심층 분석

### 3.1 Theoria 분석 엔진 (theoria_analyzer.py, 579줄)

**역할**: 좌뇌. 플레이어 입력을 받아 16개 필드 JSON(DAI)을 생성.

**출력 스키마 핵심 필드**:
- `InputAnalysis`: Original/Enhanced/Plausibility/Momentum
- `psyche_states`: NPC별 psyche(정신)/soma(육체)/relation(관계)/deep_read
- `narrative_chain`: topic_lock, chain_status, conclusion_proximity
- `anomaly_profile`: trigger, category, intensity, polarity, adaptation_group
- `needs_judgment`: boolean + action_meta(난이도/행동)
- `Position/Effect`: FitD 시스템 (0-1)

**이론 가중치 시스템** (theory_emphasis_engine.py, 677줄):
- 14개 장르 우산 × 3가지 가중치(EMPHASIZE/SUPPRESS/REFRAME)
- 4개 조건부 모듈: Forensic, Negotiation, Group Dynamics, Cosmic Horror
- 매턴 5개 이론 Rotation Spotlight (46개 범용 이론의 균등 적용 보장)
- 충돌 해소: EMPHASIZE > SUPPRESS

### 3.2 Cognition 추출 시스템 (cognition.py, 635줄)

**역할**: 응답 후 배경에서 게임 상태 업데이트 추출.

**2-레벨 병렬 구조**:
```
Physical (HIGH 우선순위)   ─┐
  → notebook, status_add/remove  ├→ asyncio.gather()
Batch (사회+서사+퀘스트+세계) ─┘
  → relationships, passives, quest_progress, world_state
```

**안전장치**:
- ACQUISITION vs OBSERVATION 구분 (보기 ≠ 소유)
- Professional Bias 인정 (의사에게 gore는 정상)
- quest_progress는 기존 퀘스트 정확한 이름만 허용

### 3.3 기억 발효 시스템 (fermentation.py, 1,568줄)

**3단계 메모리 계층**:
```
FRESH (60%, 최신 히스토리)
  ↓ [50개 초과 시 발효]
FERMENTED (30%, 이벤트 압축 + 대사 보존)
  ↓ [8개 초과 시 결정화]
DEEP (10%, 핵심 서사 + 결정화 대사)
```

**Mneme-Psyche 하이브리드 압축**:
- compressed_blocks: 이벤트 요약 + 원문 대사 보존
- psych_delta: 욕구 5축(survival/safety/love/esteem/growth) → Mental 변환
- helena_delta: depth/tension → NPC Attitude 자동 추론
- memory_triggers: 떡밥 추적 (미해결 질문 보존)

**Context Caching**: Gemini API 캐시로 로어/DEEP 반복 사용 토큰 절감

### 3.4 NPC 시스템

#### Smart Loading (npc_manager.py, 834줄)
```
Theoria 분석 → relevant_npcs 선택 (최대 5명)
  ↓
관련 NPC: 풀 프로필 (컴팩션 3,500자 이내)
기타 NPC: 이름만 (토큰 절약)
```

**컴팩션 전략** ("넓게 얕게"):
- 우선 섹션: Identity(300자), Core(500자), Speech(500자)
- 2차 섹션: Values(300자), Background(300자)
- 기타: 남은 공간에 200자씩

#### 자율 행동 트리거 (npc_autonomous.py, 277줄)

9가지 심리학 기반 트리거:

| 트리거 | 이론 근거 | 우선도 |
|--------|---------|--------|
| henderson_need_critical | Henderson 욕구 이론 | 7 (최고) |
| reactance | 심리적 반발 이론 | 6 |
| attachment_activation | 애착 이론 | 5 |
| secret_pressure | 정보 누설 압력 | 3-5 |
| information_gap_fill | 정보 간격 이론 | 4 |
| moral_disengagement | 도덕적 이탈 | 4 |
| emotional_contagion | 감정 전염 | 2 |
| desistance_check | Maruna 탈범죄 이론 | 1 (최저) |
| off_screen_persistence | 오프스크린 연속성 | 미구현 |

### 3.5 장르 적응 시스템

**3계층 장르 구조**:
- **A (Stage)**: 물리 배경 — high_fantasy, cyberpunk, modern, wuxia...
- **B (Flavor)**: 스타일 기술 — urban_fantasy, steampunk, cosmic_horror...
- **C (Lens)**: 서사 톤 — noir, comedy, romance, drama, action, slice_of_life

**결정론적 Mechanic Profile** (config.py의 `build_mechanic_profile()`):
- C레이어(Lens)에서 코드로 자동 생성 (Flash 의존 없음)
- Fields: primary_lens, disruption_axes, defense_stats, doom_stages, primary_resource
- 세션 내 일관성 보장

**장르별 차별화**:

| 장르 | Primary Resource | Doom 이름 | MC Move 예시 |
|------|-----------------|-----------|-------------|
| Horror | Vigor | 공포→파멸 | "세계의 질서가 깨졌다" |
| Romance | Composure | 설렘→결정적순간 | "마음이 드러난 순간" |
| Noir | Vigor | 의혹→파국 | "완벽한 수, 상대는 모른다" |
| Comedy | Composure | 소동→총체적난국 | "최악의 타이밍에 최악의 소식" |

### 3.6 Telescope Protocol v2

**10-Gate 자체 검증** (응답 앞에 숨겨진 CoT 블록):

| Gate | 검증 대상 | 실패 시 |
|------|----------|--------|
| Physics | 물리 법칙 위반 | 재작성 |
| Camera | 감정 라벨 → 신체신호 | Camera Eye 교정 |
| Cliche | 진부한 표현 | 대체 표현 |
| Hook | 미해결 질문 존재 | 정보 간격 주입 |
| Impersonation | PC 대사/내면 침범 | 삭제 |
| Spatial | 공간 일관성 | 교정 |
| NPC Identity | NPC 성격/역할 유지 | 교정 |
| CharReason | 행동 동기 유효성 | 재동기화 |
| TheoryAlign | Flash 데이터 반영도 | DAI 재참조 |
| GenreCoherence | 장르 톤 일치 | 톤 조정 |

**3-Layer 제거 알고리즘**:
1. Layer 1: 블록 패턴 제거 (┣...┫, <TELESCOPE>...)
2. Layer 2: 개별 게이트 라인 제거
3. Layer 3: 고아 마커 제거 (잔존 ┣, ┫)

### 3.7 사칭 감지 3-Layer

| Layer | 위치 | 메커니즘 |
|-------|------|---------|
| 1 (프롬프트) | PC_AUTONOMY_DOCTRINE | 대사 HARD BAN, 내면 봉인 |
| 2 (Telescope) | Gate 5 자체검사 | 모델 자기 검증 (불안정) |
| 3 (코드) | _check_dialogue_format() | PC이름 + 5자 매칭 → [IMPERSONATION] |

---

## 4. 데이터 흐름 분석

### 4.1 한 턴의 전체 흐름

```
[사용자 메시지]
     │
     ▼
main.py → channel_locks[ch_id] 획득
     │
     ▼
orchestration.execute()
     │
     ├─ [Step 1] gather_context()
     │    ├─ domain_manager: lore, rules, npcs, history
     │    ├─ game_system: world_context, objective
     │    ├─ game_character: passives, quests, notebook
     │    ├─ fermentation: fermented_summary, memory_triggers
     │    └─ Anti-Gravity: 동적 히스토리 슬라이싱 (100K 문자 목표)
     │
     ├─ [Step 2] 도메인 스냅샷 저장 (!다시 재시도용)
     │    └─ _retry_snapshots[ch_id] = deep_copy(domain)
     │
     ├─ [Step 3] process_une_logic()
     │    ├─ une_facade.run() → Theoria + Waterfall Pipeline
     │    ├─ _process_flashback() → 2축 비용 차감
     │    └─ _process_item_usage()
     │
     ├─ [Step 4] build_prompt()
     │    └─ slot_manager.build_34_step_prompt(ctx)
     │
     ├─ [Step 5] generate_response()
     │    ├─ persona.create_risu_style_session()
     │    ├─ smart_history 주입
     │    ├─ generate_response_with_retry() (5단계 재시도)
     │    ├─ system_update 제거
     │    ├─ Telescope 파싱 + 3-Layer 제거
     │    ├─ Inline Extraction [SYS_EXTRACT] 파싱
     │    └─ 길이 검사
     │
     ├─ [Step 5.5] 사칭 감지 루프 (최대 3회)
     │    └─ _check_dialogue_format() → [IMPERSONATION] → 재생성
     │
     ├─ [Step 6] 발송 + 히스토리 저장
     │    ├─ send_long_message() (2000자 자동 분할)
     │    └─ append_history() × 2 (User + Model)
     │
     └─ [Step 7] 백그라운드 작업
          ├─ cognition.extract_all_updates() (Physical + Batch Flash)
          │    ├─ notebook_update, status_add/remove
          │    ├─ relationships, npc_depth_hints
          │    ├─ passives, abnormal_trigger
          │    ├─ quest_add/complete/progress
          │    └─ active_threads, world_changes
          │
          └─ auto_ferment() (FRESH→FERMENTED→DEEP)
               ├─ psych_delta → Mental 변환
               ├─ helena_delta → NPC Attitude 변환
               └─ memory_triggers 보존
```

### 4.2 모듈 간 의존성 그래프

```
orchestration.py (중앙 조율)
  ├─→ orchestration_context.py (데이터 구조 + Context Gathering)
  │    ├─→ domain_manager.py (세션 데이터 CRUD)
  │    ├─→ game_system.py → game_world.py + game_character.py
  │    └─→ fermentation.py (발효 컨텍스트)
  │
  ├─→ orchestration_response.py (프롬프트 빌드 + 응답 생성)
  │    ├─→ slot_manager.py (34슬롯 조립)
  │    │    ├─→ text_resources.py (프롬프트 상수)
  │    │    ├─→ npc_manager.py (Smart Loading)
  │    │    └─→ config.py (테이블/설정)
  │    └─→ persona.py (API 호출 + 재시도)
  │
  ├─→ une_facade.py (UNE 진입점)
  │    ├─→ theoria_analyzer.py → theory_emphasis_engine.py
  │    │                        → analysis_resources.py
  │    ├─→ waterfall_pipeline.py
  │    │    ├─→ anomaly_module.py
  │    │    ├─→ doom_module.py
  │    │    ├─→ vigor_composure_module.py
  │    │    └─→ judgment_engine.py
  │    └─→ npc_autonomous.py (자율 행동 평가)
  │
  └─→ cognition.py (배경 추출)
       └─→ Gemini Flash API
```

### 4.3 SharedBus 상태 흐름

```
SharedBus (모듈 간 공유 상태)
├─ dai: Dict        ← Theoria가 기록, 모든 모듈이 참조
├─ judgment: Dict   ← JudgmentEngine이 기록
├─ doom: Dict       ← DoomModule이 갱신
├─ anomaly: Dict    ← AnomalyModule이 기록
├─ vigor: Dict      ← VigorComposure가 최종 확정
└─ composure: Dict  ← VigorComposure가 최종 확정

쓰기 순서:
  Theoria → Anomaly → Doom → VigorComposure → Judgment
  (순환 참조 방지를 위한 엄격한 순서)
```

---

## 5. 코드 규모 및 복잡도 분석

### 5.1 모듈별 분류

| 계층 | 파일 | 총 라인 | 비중 |
|------|------|---------|------|
| **프롬프트/리소스** | text_resources, analysis_resources, theory_emphasis_engine | 2,939 | 14.2% |
| **오케스트레이션** | orchestration, orchestration_response, orchestration_context | 1,891 | 9.1% |
| **UNE 엔진** | une_facade, waterfall, judgment, doom, anomaly, vigor_composure | 2,554 | 12.3% |
| **AI 뇌** | theoria_analyzer, cognition, fermentation | 2,782 | 13.4% |
| **게임 로직** | game_character, game_world, game_system, npc_manager, npc_autonomous | 2,558 | 12.4% |
| **인프라** | config, domain_manager, main, command_handler, persona | 4,501 | 21.8% |
| **유틸/기타** | 나머지 | 3,463 | 16.7% |

### 5.2 복잡도 핫스팟

**God Function 후보**:
1. `slot_manager.build_34_step_prompt()` — **482줄**, 5개 단계가 하나의 함수에 집중
2. `une_facade.run()` — 다중 모듈 조율 + 동기화
3. `orchestration.execute()` — 7단계 전체 파이프라인

**순환 복잡도 높은 영역**:
- `command_handler.py` 내 `cmd_lore()` — 4단계 처리 + 3가지 장르 계층 정규화
- `orchestration_context.RequestData.__post_init__()` — str/list/dict 3가지 모드 장르 정규화
- `fermentation.auto_ferment()` — FRESH→FERMENTED→DEEP + Psyche/Helena 변환

---

## 6. 잠재적 이슈 및 리스크

### 6.1 Critical (즉시 주의 필요)

#### [C1] 메모리 누수 — _retry_snapshots
- **위치**: orchestration.py:115
- **문제**: `_retry_snapshots[channel_id] = copy.deepcopy(domain)` 무한 누적
- **영향**: 채널 삭제 시 정리 메커니즘 없음, 대규모 히스토리 포함 시 GC 대상 아님
- **권장**: WeakValueDictionary 또는 TTL 기반 정리

#### [C2] Trauma Passive 중복 생성
- **위치**: une_facade.py (sync_from_game_context)
- **문제**: `trauma_trigger` 플래그가 reset 되지 않으면 매 턴 Passive 재추가
- **권장**: sync 후 `bus.vigor.pop("trauma_trigger", None)` 또는 중복 체크 강화

#### [C3] Doom Pressure 나선형 악화
- **문제**: Doom ↑ → 기력 ↓ → 판정 ↓ → 이변 방어 실패 → Doom ↑ (재귀적 악화)
- **영향**: 특정 조건에서 게임이 회복 불가능한 상태로 진입
- **권장**: Doom Pressure를 suggestion 모드로 전환하거나 cap 추가

### 6.2 High (중요)

#### [H1] Theoria JSON 할루시네이션
- **문제**: Flash 모델이 복잡한 16-필드 JSON에서 환각 가능
- **현 대응**: clean_json_text() + try/except (theoria_analyzer.py:101-105)
- **권장**: 필드별 분리 호출 또는 schema validation 강화

#### [H2] 토큰 측정 부정확
- **위치**: orchestration_context.py:316 (Anti-Gravity)
- **문제**: 100,000 "토큰" 목표 → 실제로는 문자 수 기준 (한글 1자 ≈ 0.3토큰)
- **영향**: 실제 토큰이 예상보다 부족할 수 있음
- **권장**: tiktoken 또는 Gemini 토큰 카운터 사용

#### [H3] Secondary Axis Ratio 하드코딩
- **위치**: anomaly_module.py
- **문제**: Anomaly 피해의 secondary axis ratio가 0.3으로 고정
- **권장**: `mechanic_profile["secondary_damage_ratio"]`로 외부화

#### [H4] off_screen_persistence 미구현
- **위치**: npc_autonomous.py:28-30
- **문제**: 트리거 정의만 있고 `_check_off_screen()` 함수 없음
- **권장**: NPC schedule 기반 off-screen 활동 구현

#### [H5] Race Condition 위험
- **위치**: orchestration.py (background_extraction_task)
- **문제**: 배경 추출 중 fresh_data 재로드 안 함, 동시 재시도 시 경합
- **권장**: 백그라운드 작업 시 fresh load 강제 또는 optimistic locking

### 6.3 Medium (개선 권장)

#### [M1] build_34_step_prompt God Function
- 482줄 단일 함수, 5개 단계가 혼재
- **권장**: 단계별 분리 (build_pc_slot, build_npc_slot, build_history_slot 등)

#### [M2] 장르 정규화 복잡도
- RequestData.__post_init__()에서 str/list/dict 3가지 모드 지원
- **권장**: 입력 단계에서 정규화 후 단일 dict 형식으로 통일

#### [M3] Memo 삭제 부분 매칭
- game_character.py:215 — `content in line` 으로 검색
- "불 사용" 메모에서 "불" 삭제 요청 시 오삭제 위험
- **권장**: 정확 매칭 또는 인덱스 기반 삭제

#### [M4] NPC Identity Reveal 후 태도 고아화
- npc_manager.py:591 — 태도 삭제 API 없어 orphaned 가능
- **권장**: reveal 시 태도 데이터도 동시 이전

#### [M5] Context Caching 해시 충돌
- fermentation.py:1462 — Python hash() 기반 = 세션 간 불안정
- **권장**: SHA256 기반 영구 해시로 교체

#### [M6] Mob Tag 무한 루프 위험
- npc_manager.py:316-318 — 태그 생성 시 충돌 회피 무한 루프 가능
- **권장**: 최대 시도 횟수 제한 (예: 100회)

### 6.4 Low (참고)

- [L1] mental_module.py 레거시 코드 잔존 (제거 권장)
- [L2] Inline Extraction JSON 유연성 부족 (추가 키 무시)
- [L3] NVC 필터링 매 턴 시간 파싱 (캐싱 없음)
- [L4] Passive Modifier 누적 오버플로우 (중간 cap 적용 후 손실)

---

## 7. 테스트 커버리지 분석

### 7.1 테스트 스위트 현황

| 테스트 파일 | 유형 | 커버리지 품질 | 핵심 검증 대상 |
|------------|------|-------------|---------------|
| test_simulation.py (32.4KB) | 통합 시뮬레이션 | **우수** | 4개 시나리오, DAI 스키마, 슬롯 빌드 |
| test_text_resources_v3.py (18.5KB) | 단위 + 정적분석 | **우수** | 21개 상수, AST 참조 검증 |
| health_check.py (12.9KB) | 스모크 | **중상** | 18개 모듈 임포트, 정적 분석 |
| test_suite.py (8.4KB) | 통합 | **중상** | 월드/캐릭터/NPC 메커니즘 |
| test_doom.py (2.7KB) | 단위 | **중상** | 둠 틱/페널티/이변 비용 |
| test_safeguard.py (2.4KB) | 단위 | **중상** | BKSPC, PC 사칭 감지 |
| test_v7_simulation.py (5.4KB) | 시뮬레이션 | **중** | 기력/평정, 적응도, 이변 |
| 나머지 5개 | 기초 | **낮음** | 로어 로드, 명령어 존재성 등 |

### 7.2 커버리지 갭 분석

| 시스템 영역 | 현재 커버리지 | 상태 |
|-----------|-------------|------|
| 프롬프트 시스템 | 높음 | OK |
| 데이터 구조/스키마 | 높음 | OK |
| Doom/시간/기력 | 중상 | 개선 필요 |
| PC 사칭 방지 | 중상 | OK |
| **실제 API 통합** | **없음** | **심각** |
| **UNE 워터폴 파이프라인** | **없음** | **심각** |
| **NPC 자율 행동** | **없음** | **심각** |
| **비동기 파이프라인** | **거의 없음** | **심각** |
| **멀티채널/멀티유저** | **없음** | **개선 필요** |
| **경계값 테스트** | **부족** | **개선 필요** |

### 7.3 CI/CD
- `.github/workflows` 설정 **없음**
- 수동 테스트 기반 개발
- 자동화된 배포/회귀 파이프라인 부재

---

## 8. 설계 강점 및 혁신점

### 8.1 아키텍처 혁신

1. **Dual-Brain 분업**: Flash(분석)+Pro(렌더링) 분리로 비용 60% 절감 + 각 모델 최적 역할
2. **34-Slot Primacy/Recency**: LLM 토큰 가중치 연구 기반 프롬프트 배치
3. **Cache Boundary**: 정적 슬롯 캐시로 토큰 비용 ~90% 절감
4. **Anti-Gravity 히스토리**: 동적 역순 확장으로 컨텍스트 윈도우 최적화

### 8.2 서사 엔진 혁신

5. **46+11 이론 체계**: Plutchik, Polyvagal, Attachment, Henderson 등 과학적 근거 기반
6. **한국 문화 코드화**: 한/정/화병/눈치/체면/심마/기/오륜/음양 — 정식 프레임워크 통합
7. **거울공방 8원칙**: Show Don't Tell + Camera Eye + Cargo Check
8. **Scheherazade 원칙**: 모든 장면에 최소 1개 미해결 질문 필수
9. **PC 자율성 3-Layer**: 프롬프트 + Telescope + 코드 레벨 다중 방어

### 8.3 게임 메커니즘 혁신

10. **2축 자원 시스템**: Vigor(체력+의지) + Composure(정신+사회) — 장르별 주축 전환
11. **적응도 2-Level**: 직접 노출 100% + 형제 전이 50% (Log 스케일)
12. **Inertia 메커니즘**: 연쇄 변화 10% 증폭 (심리적 모멘텀)
13. **Trauma Awakening**: Stage 3(붕괴)에서 회복 시 90으로 리셋 + 트라우마 패시브
14. **Theory Modifier**: NPC 심리상태(polyvagal, decision_mode, cultural_affect, attachment)가 판정에 ±20 영향

### 8.4 NPC 시스템 혁신

15. **9가지 자율 트리거**: 심리학 이론 기반 NPC 자발적 행동
16. **Smart Loading**: Theoria 선택 NPC만 풀 프로필, 나머지 이름만
17. **프로필 컴팩션**: 우선도 기반 "넓게 얕게" 압축 전략
18. **정체 발각 시스템**: OldName→NewName 이전 + identity_history 추적

### 8.5 메모리 혁신

19. **3단계 발효**: FRESH→FERMENTED→DEEP 자동 압축
20. **Psyche→Mental 변환**: 욕구 5축 → 기력/평정 자동 동기화
21. **Helena→Attitude 변환**: depth/tension → NPC 관계 자동 추론
22. **Memory Trigger 보존**: 발효 과정에서 떡밥 유실 방지

---

## 9. 개선 권장사항

### 9.1 즉시 (P0)

| 항목 | 이슈 ID | 예상 작업량 |
|------|---------|-----------|
| _retry_snapshots 메모리 누수 수정 | C1 | 소 |
| Trauma Passive 중복 방지 | C2 | 소 |
| off_screen_persistence 구현 | H4 | 중 |

### 9.2 단기 (P1)

| 항목 | 이슈 ID | 예상 작업량 |
|------|---------|-----------|
| 토큰 측정 정확도 개선 | H2 | 중 |
| Race Condition 방지 | H5 | 중 |
| UNE 워터폴 통합 테스트 작성 | 테스트 갭 | 대 |
| Secondary Axis Ratio 외부화 | H3 | 소 |

### 9.3 중기 (P2)

| 항목 | 이슈 ID | 예상 작업량 |
|------|---------|-----------|
| build_34_step_prompt 리팩토링 | M1 | 대 |
| CI/CD 파이프라인 구축 | 테스트 인프라 | 중 |
| 장르 정규화 단순화 | M2 | 중 |
| Context Caching SHA256 전환 | M5 | 소 |
| mental_module.py 완전 제거 | L1 | 소 |

### 9.4 장기 (P3)

| 항목 | 설명 | 예상 작업량 |
|------|------|-----------|
| Multi-Provider API | Gemini ↔ Claude 전환 가능 | 특대 |
| NPC 관계망 시각화 | 그래프 구조 동적 표시 | 대 |
| Doom Pressure Suggestion 모드 | 나선형 악화 방지 | 중 |
| 실제 API 통합 테스트 | Gemini Mock 서버 구축 | 대 |

---

## 10. 결론

### 10.1 종합 평가

Lorekeeper Bot v8.0은 **TRPG AI 내러티브 엔진으로서 매우 높은 수준의 설계 성숙도**를 보여줍니다.

**아키텍처 성숙도**: ★★★★☆
- Dual-Brain, 34-Slot, UNE Pipeline 등 독창적이고 체계적인 설계
- 다만 일부 God Function과 결합도 이슈 존재

**서사 품질 시스템**: ★★★★★
- 46+11개 이론 체계, 10-Gate Telescope, 거울공방 8원칙
- 한국 문화 코드 정식 통합 — 세계적으로도 유례없는 수준

**게임 메커니즘**: ★★★★☆
- 2축 자원, 적응도, Inertia, Trauma Awakening 등 정교한 설계
- Doom 나선형 악화 위험과 일부 하드코딩 이슈 존재

**코드 품질**: ★★★☆☆
- 모듈 분리는 양호하나 일부 함수 과대화
- 에러 처리가 사일런트한 경우 다수
- 테스트 커버리지 개선 필요

**운영 안정성**: ★★★☆☆
- 메모리 누수, Race Condition 등 잠재 위험 존재
- CI/CD 부재로 회귀 방지 취약
- 채널별 Lock으로 기본적 동시성은 보장

### 10.2 핵심 수치 요약

```
프로젝트 규모:     20,688 LOC / 47 파일
프롬프트 리소스:   ~120KB (text_resources + analysis_resources)
이론 체계:         46 범용 + 11 문화 + 4 조건부 = 61개 이론
장르 조합:         14개 우산 × 3계층 = 동적 프로필 생성
NPC 트리거:        9가지 심리학적 자율 행동
API 호출/턴:       2~4회 (최적화됨)
자체 검증:         10-Gate Telescope + 3-Layer 사칭 감지
메모리 계층:       3단계 발효 (FRESH→FERMENTED→DEEP)
테스트 커버리지:   프롬프트/스키마 우수, API 통합 부재
알려진 이슈:       Critical 3건, High 5건, Medium 6건, Low 4건
```

### 10.3 최종 소견

이 프로젝트는 단순한 챗봇을 넘어, **인지과학·심리학·서사학·문화이론을 코드로 구현한 학제적 시스템**입니다. Dual-Brain 아키텍처와 34-Slot 프롬프트 엔지니어링은 LLM 활용의 모범 사례로 평가할 수 있으며, 한국 문화 이론의 정식 프레임워크 통합은 이 분야에서 독보적인 시도입니다.

주요 개선 방향은 (1) 운영 안정성 강화 (메모리 누수, Race Condition), (2) 코드 품질 개선 (God Function 리팩토링), (3) 테스트 인프라 구축 (CI/CD + API 통합 테스트)에 집중되어야 합니다.

---

*이 리포트는 전체 코드베이스 47개 파일의 심층 분석을 기반으로 작성되었습니다.*
