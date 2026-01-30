# Quest, Memo, and AI Memory System

## 개요 (Overview)

이 문서는 Lorekeeper Bot의 퀘스트, 메모, 소문 관리 시스템과 AI 메모리 통합에 대해 설명합니다.

## 시스템 아키텍처

### 1. 기록 저장 흐름 (Recording Flow)

```
입력 → 좌뇌 분석 → SystemAction 생성 → 저장
```

#### 상세 단계:
1. **플레이어 입력**: 플레이어가 행동/대화 입력
2. **좌뇌 분석** (`memory_system.analyze_context_nvc`):
   - 현재 컨텍스트 분석
   - 퀘스트, 메모, NPC 추가/완료 필요 여부 판단
   - `SystemAction` 생성
3. **SystemAction 처리** (`main.process_ai_system_action`):
   - Quest Add/Complete
   - Memo Add/Archive
   - NPC Add
4. **저장** (`quest_manager`, `domain_manager`):
   - quest_board에 저장
   - ai_memory에 저장

### 2. 기록 참조 흐름 (Retrieval Flow)

```
저장된 데이터 → 컨텍스트 빌드 → AI 프롬프트 → 응답 생성
```

#### 상세 단계:
1. **컨텍스트 수집**:
   - `quest_manager.get_objective_context()`: 퀘스트/메모 포맷팅
   - `domain_manager.get_full_ai_context()`: AI 메모리 컨텍스트 생성
2. **프롬프트 구성**:
   - **ACTIVE QUESTS & MEMOS**: 별도 섹션, CRITICAL INFO 강조
   - **AI MEMORY CONTEXT**: 관계, 패시브, 알려진 정보
   - **World State**: 월드 상태
3. **우뇌(페르소나) 응답 생성**:
   - OUTPUT DIRECTIVE에서 퀘스트/메모 참조 지시
   - AI 메모리 사용 지시
   - 응답에 관련 정보 반영

## 주요 기능

### 퀘스트 (Quests)

**자동 관리:**
- NPC가 임무를 주면 → `Quest Add`
- 목표 달성 시 → `Quest Complete`

**저장 위치:**
- `domain_manager`: `quest_board["active"]` → `quest_board["completed"]`

**AI 참조:**
- 프롬프트: `### [ACTIVE QUESTS & MEMOS - CRITICAL INFO]`
- 지시문: "Always reference active quests and memos when relevant"

### 메모 (Memos)

**자동 관리:**
- 중요 정보 발견 (단서, 암호, 장소) → `Memo Add`
- 정보가 쓸모없어짐 → `Memo Archive`

**지원되는 정보 유형:**
- 단서 (clues)
- NPC 이름/정보
- 암호/비밀번호
- 장소 정보
- **소문/소식 (rumors/gossip)**
- 아이템 획득 정보

**예시:**
```json
{"tool": "Memo", "type": "Add", "content": "밤에 숲이 유령들로 가득하다는 소문"}
{"tool": "Memo", "type": "Add", "content": "하수도에 암시장이 있다고 NPC가 언급"}
{"tool": "Memo", "type": "Add", "content": "비밀번호는 1234"}
```

### AI 메모리 (AI Memory)

#### PlayerMemoryUpdate (플레이어 개별 메모리)
- `appearance`: 외형 변화
- `relationships`: NPC와의 관계
- `passives`: 패시브/칭호
- `known_info`: **알게 된 정보/소문/단서**
- `foreshadowing`: 복선/떡밥
- `normalization`: 비일상 요소 적응
- `companions`: 동행자

#### SessionMemoryUpdate (세션 공통 메모리)
- `world_summary`: 세계 상황
- `current_arc`: 현재 스토리 아크
- `active_threads`: 진행 중 플롯
- `resolved_threads`: 해결된 플롯
- `npc_summaries`: NPC 요약
- `world_changes`: 세계 변화

## 소문 시스템 (Rumor System)

소문은 다음 두 가지 방식으로 저장됩니다:

### 1. Memo로 저장
```
NPC가 "밤에는 숲에 가지 마" 라고 말함
→ SystemAction: {"tool": "Memo", "type": "Add", "content": "밤의 숲 위험 소문"}
→ quest_board["memos"]에 저장
```

### 2. known_info로 저장
```
PlayerMemoryUpdate: {
  "known_info": ["철수가 마법사라는 소문", "도시 외곽에 던전이 있다는 정보"]
}
→ ai_memory[user_id]["known_info"]에 저장
```

### 차이점:
- **Memo**: 즉시 처리해야 할 단서나 정보 (Archive 가능)
- **known_info**: 영구적인 지식, 캐릭터가 아는 것 (누적)

## 명시적 지시문 (Explicit Instructions)

### 퀘스트/메모 컨텍스트
```
### [ACTIVE QUESTS & MEMOS - CRITICAL INFO]
⚠️ IMPORTANT: These are persistent records. Always reference them when relevant.

**Active Objectives (Remember these and reference when relevant):**
- [퀘스트 1]
- [퀘스트 2]

**Active Memos (Important clues and information to remember):**
- [메모 1]
- [메모 2]
```

### OUTPUT DIRECTIVE
```
## ⚠️ CRITICAL: STORY CONTINUITY & MEMORY
BEFORE generating any response, you MUST:
5. ✅ Check ACTIVE QUESTS & MEMOS — Remember ongoing objectives and important information
6. ✅ Reference AI MEMORY CONTEXT — Use stored relationships, passives, and known information

Common Mistakes to AVOID:
- ❌ Forgetting active quests when they become relevant
- ❌ Ignoring memos that contain critical clues or information
- ❌ Not using stored AI memory (relationships, passives, known info)
```

## 개선 사항 요약

### Before (이전)
- 퀘스트/메모가 World State에 묻혀있음
- AI에게 참조 지시가 명시적이지 않음
- 소문이 명시적으로 언급되지 않음

### After (개선 후)
- ✅ 퀘스트/메모를 별도 섹션으로 분리, CRITICAL INFO 강조
- ✅ OUTPUT DIRECTIVE에 명시적 참조 지시문 추가
- ✅ 소문/단서 추적 예시 추가 및 문서화
- ✅ AI 메모리에 복선 표시 추가
- ✅ known_info 필드 설명 개선

## 사용 예시

### 시나리오: 소문 듣기
```
플레이어: "주인장, 요즘 재밌는 소문 없어?"
NPC: "밤마다 숲에서 이상한 소리가 들린다는데..."

→ 좌뇌 분석:
{
  "SystemAction": {
    "tool": "Memo", 
    "type": "Add", 
    "content": "밤의 숲에서 이상한 소리 (소문)"
  },
  "PlayerMemoryUpdate": {
    "known_info": ["마을에 숲 괴담이 퍼져있음"]
  }
}

→ 저장됨:
- quest_board["memos"]: ["밤의 숲에서 이상한 소리 (소문)"]
- ai_memory[user_id]["known_info"]: ["마을에 숲 괴담이 퍼져있음"]
```

### 시나리오: 소문 활용
```
플레이어: [이름] 숲으로 간다.

→ AI 프롬프트에 포함:
### [ACTIVE QUESTS & MEMOS - CRITICAL INFO]
**Active Memos:**
- 밤의 숲에서 이상한 소리 (소문)

### [AI MEMORY CONTEXT]
**알고 있는 정보:** 마을에 숲 괴담이 퍼져있음

→ AI 응답:
"[이름]은 숲으로 향한다. 마을 사람들이 말했던 괴담이 떠오른다. 
밤이 되면 이상한 소리가 들린다던데..."
```

## 확인 체크리스트

- [x] AI가 인풋과 아웃풋에 따라 메모, 퀘스트 기록을 잘 저장하는가?
  - SystemAction으로 자동 저장
  - PlayerMemoryUpdate, SessionMemoryUpdate로 AI 메모리 저장

- [x] 저장된 메모, 퀘스트를 메모리와 페르소나에 잘 넣는가?
  - `get_objective_context()`: 퀘스트/메모 컨텍스트
  - `get_full_ai_context()`: AI 메모리 컨텍스트
  - 프롬프트에 명시적으로 포함

- [x] 해당 설정을 잊지 않고 나중에 필요하면 잘 꺼내는가?
  - OUTPUT DIRECTIVE에 명시적 지시문
  - "CRITICAL INFO" 강조
  - "Always reference when relevant" 지시

## 추가 참고사항

### 관련 파일
- `main.py`: 메인 흐름, 프롬프트 구성
- `memory_system.py`: 좌뇌 분석, SystemAction 생성
- `quest_manager.py`: 퀘스트/메모 관리
- `domain_manager.py`: AI 메모리 관리, 컨텍스트 빌드

### 디버깅 팁
1. SystemAction 확인: `nvc_res.get("SystemAction")`
2. 퀘스트 보드 확인: `domain_manager.get_quest_board(channel_id)`
3. AI 메모리 확인: `domain_manager.get_ai_memory(channel_id, user_id)`
4. 컨텍스트 확인: 로그에서 full_prompt 검색
