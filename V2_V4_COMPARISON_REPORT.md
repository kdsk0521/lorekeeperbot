# 로어키퍼봇 V2 vs V4 비교 분석 리포트

## 목차
1. [개요](#1-개요)
2. [아키텍처 비교](#2-아키텍처-비교)
3. [모듈별 상세 비교](#3-모듈별-상세-비교)
4. [기능 비교 매트릭스](#4-기능-비교-매트릭스)
5. [장단점 분석](#5-장단점-분석)
6. [최적 병합 전략 제안](#6-최적-병합-전략-제안)

---

## 1. 개요

### 버전 정보
- **V2**: 커밋 `eb3ea76` (463bf71 이전)
- **V4**: 현재 HEAD (`6e41877`)
- **커밋 차이**: 50개 커밋

### 코드 변화량
```
삭제된 파일 (V2 → V4):
  - character_sheet.py (774줄)
  - left_brain_analysis.py (411줄)
  - left_brain_extraction.py (510줄)
  - quest_manager.py (793줄)
  - simulation_manager.py (767줄)
  - world_manager.py (359줄)

추가된 파일:
  - bot_utils.py (136줄)
  - cognition.py (369줄)
  - command_handler.py (848줄)
  - config.py (253줄)
  - game_system.py (814줄)

대폭 변경된 파일:
  - domain_manager.py: 1932줄 → 약 700줄 (간소화)
  - main.py: 2603줄 → 약 800줄 (간소화)
  - persona.py: 469줄 → 약 1500줄 (대폭 확장)

총계: -7,682줄 삭제, +3,971줄 추가
```

---

## 2. 아키텍처 비교

### V2 아키텍처 (세분화된 모듈)
```
┌─────────────────────────────────────────────────────────────┐
│                        main.py (진입점)                      │
└─────────────────────────────────────────────────────────────┘
                              │
     ┌────────────┬───────────┼───────────┬────────────┐
     ▼            ▼           ▼           ▼            ▼
┌─────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐
│character│ │simulation│ │  quest   │ │  world   │ │  domain  │
│ _sheet  │ │ _manager │ │ _manager │ │ _manager │ │ _manager │
└─────────┘ └──────────┘ └──────────┘ └──────────┘ └──────────┘
     │            │           │           │
     └────────────┴───────────┴───────────┘
                      │
        ┌─────────────┼─────────────┐
        ▼             ▼             ▼
   ┌─────────┐  ┌──────────┐  ┌──────────┐
   │left_brain│  │left_brain│  │  memory  │
   │_analysis │  │_extraction│ │ _system  │
   └─────────┘  └──────────┘  └──────────┘
```

### V4 아키텍처 (통합된 모듈)
```
┌─────────────────────────────────────────────────────────────┐
│                        main.py (파이프라인)                   │
└─────────────────────────────────────────────────────────────┘
                              │
          ┌───────────────────┼───────────────────┐
          ▼                   ▼                   ▼
   ┌─────────────┐     ┌─────────────┐     ┌─────────────┐
   │  command    │     │  cognition  │     │   persona   │
   │  _handler   │     │  (좌뇌통합) │     │  (우뇌통합) │
   └─────────────┘     └─────────────┘     └─────────────┘
          │                   │                   │
          └───────────┬───────┴───────────────────┘
                      ▼
              ┌─────────────┐
              │   domain    │
              │  _manager   │
              │ (중앙저장소) │
              └─────────────┘
                      │
          ┌───────────┼───────────┐
          ▼           ▼           ▼
   ┌──────────┐ ┌──────────┐ ┌──────────┐
   │  config  │ │game_system││fermentation│
   └──────────┘ └──────────┘ └──────────┘
```

### 아키텍처 비교 요약

| 측면 | V2 | V4 | 승자 |
|------|-----|-----|------|
| **모듈 수** | 13개 | 12개 | 동등 |
| **코드 총량** | ~8,500줄 | ~5,500줄 | **V4** ✓ |
| **관심사 분리** | 세분화 (기능별) | 통합 (역할별) | 상황에 따라 |
| **결합도** | 높음 (상호의존) | 낮음 (중앙화) | **V4** ✓ |
| **확장성** | 새 기능 추가 용이 | 기존 구조에 통합 필요 | **V2** ✓ |
| **유지보수** | 파일 많아 복잡 | 파일 적어 단순 | **V4** ✓ |

---

## 3. 모듈별 상세 비교

### 3.1 좌뇌 시스템 (분석 & 추출)

#### V2: 분리된 좌뇌 모듈
```python
# left_brain_analysis.py (411줄)
- analyze_context_nvc(): 상황 분석
- ActionJudgment 생성
- NPC 태도 분석
- 경험 카운터 추출

# left_brain_extraction.py (510줄)
- B-1: extract_physical_updates() - 인벤토리/골드/상태
- B-2: extract_social_updates() - 관계/동료
- B-3: extract_narrative_updates() - 패시브
- B-4: extract_quest_updates() - 퀘스트/메모
- extract_all_updates() - 4개 병렬 실행
```

#### V4: 통합된 cognition.py
```python
# cognition.py (369줄)
- analyze_context_nvc(): Theoria 분석 (V2 계승)
- extract_all_updates(): Logos 추출 (V2 계승)
  - _extract_physical()
  - _extract_social()
  - _extract_narrative()
  - _extract_quest()
- build_action_judgment_with_roll(): 주사위 판정
```

#### 비교 분석

| 기능 | V2 | V4 | 권장 |
|------|-----|-----|------|
| **코드 구조** | 2개 파일 분리 | 1개 파일 통합 | **V4** (관리 용이) |
| **병렬 추출** | asyncio.gather() | asyncio.gather() | 동등 |
| **분석 깊이** | 상세 (600줄 프롬프트) | 간결 (300줄 프롬프트) | **V2** (더 정교) |
| **주사위 시스템** | DC 테이블 기반 | 상태이상 보정 추가 | **V4** (더 완성) |

**결론**: V4의 통합 구조 + V2의 상세 프롬프트 조합 권장

---

### 3.2 우뇌 시스템 (서사 생성)

#### V2: persona.py (469줄)
```python
핵심 원칙:
- PC_AUTONOMY_DOCTRINE: PC 자율권 절대 보장
- ACTION_RESOLUTION: 판정 강제 실행
- RECORDER_IDENTITY: 익명 내레이터

응답 길이:
- MIN: 500자
- MAX: 7000자
```

#### V4: persona.py (1500줄+)
```python
추가된 기능:
- PromptBuilder 클래스: 13단계 프롬프트 조립
- ChatSessionAdapter: Gemini 세션 관리
- Context Caching: 토큰 90% 절감
- 4중 PC 사칭 방지 시스템
- SillyTavern Preset 순서 엄격 준수
- 장르/톤 기반 동적 스크립트
- 인지 아키텍처 모델 (Polyvagal, Plutchik)
```

#### 비교 분석

| 기능 | V2 | V4 | 권장 |
|------|-----|-----|------|
| **PC 사칭 방지** | 프롬프트만 | 4중 안전장치 | **V4** ✓ |
| **프롬프트 구조** | 단순 | 13단계 체계 | **V4** ✓ |
| **인지 모델** | 기본 | Polyvagal+Plutchik | **V4** ✓ |
| **Context Caching** | 없음 | 있음 (비용 90%↓) | **V4** ✓ |
| **코드 복잡도** | 낮음 | 높음 | **V2** (유지보수) |
| **프롬프트 길이** | 적절 | 과다 | **V2** (효율) |

**결론**: V4가 대부분 우수, 단 프롬프트 길이 최적화 필요

---

### 3.3 게임 상태 관리

#### V2: 분리된 관리자들
```python
# simulation_manager.py (767줄)
- 인벤토리 관리
- 상태이상 시스템 (심각도 1-3)
- 비일상 적응도 (로그 스케일)
- AI 패시브 부여

# world_manager.py (359줄)
- 시간 진행 (6개 시간대)
- 날씨 관리
- Doom(위기 수치) 시스템
- 위치 기반 규칙

# quest_manager.py (793줄)
- 퀘스트 보드
- 메모 시스템 (활성/보관/삭제)
- 연대기 생성
```

#### V4: 통합된 game_system.py
```python
# game_system.py (814줄)
- 시간 진행 (advance_time)
- Doom 계산 (calculate_doom_increase)
- 퀘스트 관리 (add/complete/remove)
- 메모 관리 (add/resolve_auto)
- 인벤토리 (update_inventory)
- 상태이상 (update_status_effect)
- 비일상 적응 (expose_to_abnormal)
- 주사위 판정 (perform_check)
```

#### 비교 분석

| 기능 | V2 | V4 | 권장 |
|------|-----|-----|------|
| **상태이상 심각도** | 3단계 시스템 | 단순 리스트 | **V2** ✓ |
| **Doom 감소** | 없음 | 없음 | 둘 다 개선 필요 |
| **시간 진행** | 수동 | tick 기반 자동 | **V4** ✓ |
| **NPC 시간 활동** | 없음 | 자동 생성 | **V4** ✓ |
| **연대기 생성** | 있음 | 없음 | **V2** ✓ |
| **패시브 부여** | AI 제안 기반 | 서사적 마일스톤 | **V2** (더 신중) |

**결론**: V2의 심각도 시스템 + V4의 자동화 조합 권장

---

### 3.4 캐릭터 관리

#### V2: character_sheet.py (774줄)
```python
# PlayerCharacterManager
- get_character(): 전체 데이터
- apply_memory_updates(): 좌뇌 B-2, B-3 적용
- apply_player_updates(): 좌뇌 B-1 적용
- get_for_prompt(): AI 프롬프트용 컨텍스트

# NPCManager
- add_npc(): NPC 추가/업데이트
- update_npc_relationship(): 관계 업데이트
- get_npc_summary(): 모든 NPC 요약
- clear_npcs_by_source(): 출처별 초기화

특징:
- 로어 출처 vs 세션 감지 NPC 구분
- Identity Reveal 추적 (OldName > NewName)
```

#### V4: domain_manager.py 통합
```python
# domain_manager.py 내 캐릭터 관련
- update_participant(): 참가자 업데이트
- get_participant_data(): 참가자 데이터
- apply_pc_info_to_user(): PC 정보 적용
- get_ai_memory(): AI 메모리
- update_npc_attitude(): NPC 태도 (신규!)
- get_npc_attitudes(): NPC 태도 조회

신규 기능:
- NPC 태도 시스템 (hostile/unfriendly/neutral/friendly/devoted)
- 태도 변화 이유 기록
- 마지막 업데이트 시간 기록
```

#### 비교 분석

| 기능 | V2 | V4 | 권장 |
|------|-----|-----|------|
| **NPC 소스 구분** | 로어/세션 구분 | 없음 | **V2** ✓ |
| **Identity Reveal** | 이름 변경 추적 | 없음 | **V2** ✓ |
| **NPC 태도 시스템** | 없음 | 5단계 태도 | **V4** ✓ |
| **캐릭터 관리 API** | 전용 클래스 | 통합 함수 | **V2** (명확) |
| **관계도 저장** | ai_memory 내 | npc_attitudes 분리 | **V4** ✓ |

**결론**: V2의 NPC 관리 + V4의 태도 시스템 조합 권장

---

### 3.5 메모리 시스템

#### V2: fermentation.py + memory_system.py
```python
# fermentation.py (~1000줄)
3계층 메모리:
- FRESH: 최대 80개 (원본)
- FERMENTED: 최대 8개 (30개→1개 요약)
- DEEP: 초압축 (8개 FERMENTED→1개)

컨텍스트 비율:
- DEEP: 10%
- FERMENTED: 30%
- FRESH: 60%

# memory_system.py (~600줄)
- api_call_with_retry()
- safe_parse_json()
- 공유 프롬프트 상수
```

#### V4: fermentation.py (개선됨)
```python
# fermentation.py
변경점:
- FRESH_THRESHOLD: 80 → 50
- FERMENT_CHUNK_SIZE: 30 → 25
- Context Caching 추가 (Gemini API)

# memory_system.py
추가 기능:
- analyze_genre_from_lore()
- extract_npcs_only()
- extract_pc_info()
- analyze_brainstorming()
- process_ooc_memory_edit()
```

#### 비교 분석

| 기능 | V2 | V4 | 권장 |
|------|-----|-----|------|
| **FRESH 임계값** | 80 | 50 | **V2** (더 많은 컨텍스트) |
| **청크 크기** | 30 | 25 | 상황에 따라 |
| **Context Caching** | 없음 | 있음 | **V4** ✓ |
| **OOC 메모리 편집** | 없음 | 있음 | **V4** ✓ |
| **장르 분석** | 없음 | 자동 분석 | **V4** ✓ |
| **PC 정보 추출** | 없음 | 자동 추출 | **V4** ✓ |

**결론**: V2의 더 큰 컨텍스트 + V4의 캐싱/자동화 조합 권장

---

## 4. 기능 비교 매트릭스

### 핵심 기능 비교

| 기능 | V2 | V4 | 최적 선택 |
|------|:---:|:---:|:---:|
| **이중 반구 아키텍처** | ✓ | ✓ | 동등 |
| **PC 자율권 보호** | ✓ | ✓✓✓ | **V4** |
| **ActionJudgment** | ✓ | ✓ | 동등 |
| **주사위 판정** | 기본 | 상태보정 | **V4** |
| **상태이상 심각도** | 3단계 | 없음 | **V2** |
| **비일상 적응도** | ✓ | ✓ | 동등 |
| **Doom 시스템** | ✓ | ✓ | 동등 |
| **시간 자동 진행** | 수동 | tick 기반 | **V4** |
| **NPC 시간 활동** | 없음 | 자동 생성 | **V4** |
| **NPC 태도 시스템** | 없음 | 5단계 | **V4** |
| **NPC 소스 구분** | ✓ | 없음 | **V2** |
| **Identity Reveal** | ✓ | 없음 | **V2** |
| **연대기 생성** | ✓ | 없음 | **V2** |
| **Context Caching** | 없음 | ✓ | **V4** |
| **4중 사칭 방지** | 없음 | ✓ | **V4** |
| **인지 모델** | 기본 | Polyvagal | **V4** |
| **OOC 메모리 편집** | 없음 | ✓ | **V4** |
| **장르 자동 분석** | 없음 | ✓ | **V4** |
| **명령어 체계** | 기본 | 30개+ | **V4** |

### 성능 비교

| 지표 | V2 | V4 | 최적 선택 |
|------|:---:|:---:|:---:|
| **코드 줄 수** | ~8,500 | ~5,500 | **V4** |
| **API 호출 수/메시지** | 6회 | 4회 | **V4** |
| **토큰 사용량** | 높음 | 낮음 (캐싱) | **V4** |
| **응답 속도** | 보통 | 빠름 | **V4** |
| **메모리 사용** | 보통 | 높음 | **V2** |
| **확장성** | 높음 | 보통 | **V2** |

---

## 5. 장단점 분석

### V2 장점 (V4에서 잃어버린 것)

#### 🟢 1. 세분화된 모듈 구조
```
V2의 장점:
- quest_manager.py, world_manager.py 등 독립 모듈
- 새 기능 추가 시 해당 모듈만 수정
- 단위 테스트가 용이함

V4의 문제:
- game_system.py에 모든 것이 통합
- 하나의 변경이 여러 기능에 영향
- 모듈 경계가 불명확
```

#### 🟢 2. 상태이상 심각도 시스템
```python
# V2의 3단계 심각도
STATUS_EFFECTS = {
    "부상": {"severity": 1, "doom_increase": 0},
    "중상": {"severity": 2, "doom_increase": 1},
    "골절": {"severity": 3, "doom_increase": 2},
}

# V4에서는 단순 리스트로 대체됨
status_effects = ["부상", "중상", "골절"]  # 심각도 정보 없음
```

#### 🟢 3. NPC 소스 구분
```python
# V2의 NPC 구조
npcs = {
    "NPC_NAME": {
        "source": "lore" | "session",  # 출처 구분
        "desc": "설명",
        "status": "Active" | "Dead"
    }
}

# V4에서는 source 구분 없음
```

#### 🟢 4. Identity Reveal 추적
```python
# V2: 이름 변경 추적
"OldName > NewName"  # 정체 밝혀짐

# V4: 해당 기능 없음
```

#### 🟢 5. 연대기(Chronicle) 생성
```python
# V2: quest_manager.py
def generate_chronicle_from_history():
    # 히스토리 50개 요약
    # 타임스탬프와 함께 저장

# V4: 해당 기능 없음
```

#### 🟢 6. 패시브 부여의 신중함
```python
# V2: 엄격한 기준
- "명확한 서사적 마일스톤" 필요
- 반복 경험 5회 이상 누적
- 시스템 메시지 스타일로 획득

# V4: 상대적으로 느슨함
```

---

### V4 장점 (V2에서 개선된 것)

#### 🟢 1. Context Caching (비용 90% 절감)
```python
# V4의 캐싱 시스템
- 프리셋 1-7 (정적 콘텐츠) 캐시
- 로어/DEEP 변경 시에만 무효화
- API 비용 대폭 절감
```

#### 🟢 2. 4중 PC 사칭 방지
```python
# V4의 안전장치
1. 프롬프트: PC_AUTONOMY_DOCTRINE
2. 페르소나: "기록자" 정체성
3. 필터링: 응답 후 정규식 검출
4. 경고: 위반 시 메시지 전송
```

#### 🟢 3. NPC 태도 시스템
```python
# V4의 새 기능
npc_attitudes = {
    "NPC이름": {
        "attitude": "hostile/unfriendly/neutral/friendly/devoted",
        "reason": "태도 변화 이유",
        "last_updated": "timestamp"
    }
}
```

#### 🟢 4. 시간 자동 진행 (tick 기반)
```python
# V4의 TimeFlow
TimeFlow = {
    "duration": "instant/short/medium/long/explicit",
    "ticks": 0-3,  # 1 tick = 4 hours
}
# 자동으로 시간대 변경, Doom 증가, 날씨 변경
```

#### 🟢 5. NPC 시간 활동 생성
```python
# V4: 백그라운드에서 NPC 활동 자동 생성
"### [OFFSCREEN WORLD]"
- 플레이어를 기다리지 않는 세상
- NPC들의 독립적 활동
```

#### 🟢 6. 상세한 프롬프트 엔지니어링
```python
# V4: 13단계 SillyTavern Preset
1. AI Mandate
2. World Axiom
3. Lore
...
13. Language Correction
```

#### 🟢 7. 인지 아키텍처 모델
```python
# V4: 심리학 기반 모델
- Polyvagal Theory (신체 상태)
- Plutchik Theory (8가지 감정)
- Schwartz Value (가치 체계)
```

#### 🟢 8. OOC 메모리 편집
```python
# V4: OOC로 메모리 직접 수정
!ooc 인벤토리에 "마법검" 추가해줘
→ process_ooc_memory_edit()로 처리
```

#### 🟢 9. 장르 자동 분석
```python
# V4: 로어에서 장르 추출
analyze_genre_from_lore() → "다크 판타지"
→ 장르별 톤 자동 조절
```

#### 🟢 10. 상태이상 판정 보정
```python
# V4: 상태에 따른 주사위 보정
perform_check():
  - 부상: -10
  - 중상: -20
  - 피로: -15
```

---

## 6. 최적 병합 전략 제안

### 6.1 아키텍처 전략

```
권장: V4의 통합 구조 유지 + V2의 확장성 보완

구체적 방안:
1. cognition.py 유지 (좌뇌 통합)
2. persona.py 유지 (우뇌 통합)
3. game_system.py를 2개로 분리:
   - game_world.py: 시간/날씨/Doom
   - game_character.py: 인벤토리/상태/퀘스트
4. npc_manager.py 복원 (V2에서)
```

### 6.2 기능 병합 우선순위

#### 🔴 최우선 (V2에서 복원)
1. **상태이상 심각도 시스템**
   - V2의 3단계 심각도 구조
   - config.py에 STATUS_EFFECTS 확장

2. **NPC 소스 구분**
   - `source: "lore" | "session"` 필드 추가
   - 세션 리셋 시 세션 NPC만 제거

3. **Identity Reveal 추적**
   - `"OldName > NewName"` 형식 지원
   - NPC 정체 밝혀짐 이벤트 처리

4. **연대기 생성**
   - quest_manager.py의 generate_chronicle_from_history() 복원
   - 주기적 요약 저장

#### 🟡 중요 (V4 유지 + 개선)
5. **Doom 감소 메커니즘 추가**
   - 퀘스트 완료 시 Doom 감소
   - 안전 지역 진입 시 Doom 감소
   - 휴식 시 Doom 감소

6. **프롬프트 최적화**
   - persona.py 프롬프트 길이 30% 축소
   - 중복 지시사항 제거
   - 핵심만 남기기

7. **메모리 임계값 조정**
   - FRESH_THRESHOLD: 50 → 70 (V2에 가깝게)
   - 더 많은 컨텍스트 유지

#### 🟢 선택 (새 기능)
8. **하이브리드 패시브 시스템**
   - V2의 엄격한 기준 + V4의 자동 추적
   - 반복 경험 5회 → 패시브 제안 (자동)
   - 서사적 마일스톤 → 패시브 부여 (AI)

9. **Doom 시각화 강화**
   - 현재 Doom 레벨에 따른 서사 힌트
   - 임계값 도달 시 경고 메시지

### 6.3 구현 로드맵

```
Phase 1: 핵심 복원 (예상 작업량: 중)
├── config.py에 STATUS_EFFECTS 심각도 추가
├── domain_manager.py에 NPC source 필드 추가
├── domain_manager.py에 Identity Reveal 로직 추가
└── game_system.py에 연대기 생성 함수 추가

Phase 2: 밸런스 개선 (예상 작업량: 소)
├── Doom 감소 메커니즘 구현
├── 프롬프트 최적화 (persona.py)
└── 메모리 임계값 조정 (config.py)

Phase 3: 고급 기능 (예상 작업량: 중)
├── 하이브리드 패시브 시스템
├── Doom 시각화 강화
└── 종합 테스트
```

### 6.4 병합 후 예상 구조

```
lorekeeperbot/
├── main.py              (V4 유지)
├── config.py            (V4 + V2 심각도)
├── input_handler.py     (V4 유지)
├── command_handler.py   (V4 유지)
├── cognition.py         (V4 유지)
├── persona.py           (V4 최적화)
├── domain_manager.py    (V4 + V2 NPC 관리)
├── game_system.py       (V4 + V2 심각도/연대기)
├── fermentation.py      (V4 + V2 임계값)
├── memory_system.py     (V4 유지)
├── session_manager.py   (V4 유지)
└── bot_utils.py         (V4 유지)
```

---

## 결론

### 최종 권장사항

| 영역 | 기반 | 추가 요소 |
|------|------|----------|
| **아키텍처** | V4 | - |
| **좌뇌 시스템** | V4 | V2 프롬프트 깊이 |
| **우뇌 시스템** | V4 | 프롬프트 최적화 |
| **게임 상태** | V4 | V2 심각도 시스템 |
| **NPC 관리** | V4 | V2 소스 구분, Identity Reveal |
| **메모리** | V4 | V2 임계값 |
| **퀘스트** | V4 | V2 연대기 생성 |
| **Doom** | V4 | 감소 메커니즘 추가 |

### 예상 효과

```
✓ V4의 효율성 유지 (코드량 ↓, API 비용 ↓)
✓ V2의 깊이 복원 (심각도, NPC 관리, 연대기)
✓ 밸런스 개선 (Doom 감소)
✓ 사용성 향상 (프롬프트 최적화)
```

---

## 7. 추가 기능: 채널 화이트리스트

### 7.1 기능 설명

봇이 특정 채널에서만 활동하도록 제한하는 기능입니다.
불필요한 채널에서의 봇 반응을 방지하고, 리소스를 절약합니다.

### 7.2 구현 내용

#### config.py 설정
```python
# 환경 변수로 설정
ALLOWED_CHANNELS = os.getenv('ALLOWED_CHANNELS', '')  # 쉼표 구분 채널 ID
CHANNEL_WHITELIST_ENABLED = os.getenv('CHANNEL_WHITELIST_ENABLED', 'false')

# 함수
is_channel_allowed(channel_id)      # 채널 허용 여부 확인
add_allowed_channel(channel_id)     # 런타임 채널 추가
remove_allowed_channel(channel_id)  # 런타임 채널 제거
get_allowed_channels()              # 허용 채널 목록 조회
```

#### main.py 적용
```python
@client_discord.event
async def on_message(message):
    # ... 기존 체크 ...

    # 채널 화이트리스트 체크
    if not config.is_channel_allowed(message.channel.id):
        return  # 허용되지 않은 채널에서는 봇 무시
```

### 7.3 사용 방법

#### 환경 변수 설정 (.env)
```bash
# 화이트리스트 활성화
CHANNEL_WHITELIST_ENABLED=true

# 허용할 채널 ID 목록 (쉼표 구분)
ALLOWED_CHANNELS=123456789012345678,987654321098765432
```

#### 동작 방식
| 설정 상태 | 동작 |
|----------|------|
| `ENABLED=false` | 모든 채널에서 활동 (기본) |
| `ENABLED=true` + 목록 비어있음 | 모든 채널에서 활동 |
| `ENABLED=true` + 목록 있음 | 목록에 있는 채널에서만 활동 |

### 7.4 확장 가능성

- 관리자 명령어로 런타임 채널 추가/제거
- 서버별 화이트리스트 설정
- 카테고리별 허용/차단

---

*이 리포트는 V2(eb3ea76)와 V4(6e41877) 커밋을 기준으로 분석되었습니다.*
*업데이트: 채널 화이트리스트 기능 추가*
*작성일: 2026-01-26*
