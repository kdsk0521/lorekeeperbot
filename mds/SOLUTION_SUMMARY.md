# 로어 및 NPC 반영 문제 해결 완료

## 문제 요약

사용자가 제공한 로어 파일의 내용과 그 안에 포함된 NPC 정보가 AI 응답 생성 시 제대로 반영되지 않는 문제가 있었습니다.

## 원인 분석

1. **로어 저장 구조**
   - 로어 저장 시 AI가 NPC를 추출하여 별도로 저장
   - 원본 로어 (NPC 포함) → `lore_original.txt`
   - 정리된 로어 (NPC 제외) → `lore.txt`
   - NPC 정보 → domain 데이터의 `npcs` 딕셔너리

2. **문제점**
   - AI에게 로어를 전달할 때 `get_lore()` 함수 사용
   - 이 함수는 NPC가 제거된 `lore.txt`만 반환
   - 결과적으로 AI는 NPC 정보를 알 수 없었음

## 해결 방법

### 1. 새로운 함수 추가: `get_lore_with_npcs()`

**위치**: `domain_manager.py`

```python
def get_lore_with_npcs(channel_id: str) -> str:
    """
    로어와 NPC 정보를 합쳐서 반환합니다.
    AI에게 전달할 때 사용합니다.
    """
    lore = get_lore(channel_id)
    npcs = get_npcs(channel_id)
    
    if not npcs:
        return lore
    
    # NPC 섹션 생성
    npc_section = "\n\n### 📋 NPC 정보 (캐릭터들)\n\n"
    for name, data in npcs.items():
        desc = data.get("desc", "설명 없음")
        status = data.get("status", "Active")
        status_emoji = "✅" if status == "Active" else "💀" if status == "Dead" else "❓"
        npc_section += f"**{name}** ({status_emoji} {status})\n{desc}\n\n"
    
    return lore + npc_section
```

**기능**:
- 정리된 로어와 NPC 목록을 결합
- NPC 정보를 마크다운 형식으로 포맷팅
- 상태 표시 (Active, Dead 등)

### 2. AI 컨텍스트 수정

**변경된 위치들** (`main.py`):

1. **AI 응답 생성** (line ~1538)
   ```python
   lore_txt = domain_manager.get_lore_with_npcs(channel_id)
   ```

2. **!분석 명령어** (line ~1127)
   ```python
   lore = domain_manager.get_lore_with_npcs(channel_id)
   ```

3. **!일관성 명령어** (line ~1170)
   ```python
   lore = domain_manager.get_lore_with_npcs(channel_id)
   ```

4. **!세계규칙 명령어** (line ~1206)
   ```python
   lore = domain_manager.get_lore_with_npcs(channel_id)
   ```

5. **!연대기 추출 명령어** (line ~386)
   ```python
   lore = domain_manager.get_lore_with_npcs(channel_id)
   ```

### 3. UI 개선

**!로어 명령어 개선** (line ~168-213):

```python
# 기존 출력
📜 로어 정보
📚 원본 (NPC 포함): 1,234자
📖 정리된 로어 (NPC 제외): 987자

# 개선된 출력
📜 로어 정보
📚 원본 (NPC 포함): 1,234자
📖 정리된 로어 (NPC 제외): 987자
👥 추출된 NPC: 3명

👥 NPC 목록 (미리보기):
• 리엘: 엘프 궁수로, 과묵하지만 활솜씨가 뛰어남...
• 가렌: 용감한 전사로, 정의감이 강함...
• 마리아: 신비한 마법사, 과거가 불분명함...
```

## 데이터 흐름

### Before (문제 상황)
```
[로어 파일 업로드]
    ↓
[AI가 NPC 추출]
    ↓
[원본 저장] → lore_original.txt (NPC 포함)
[정리본 저장] → lore.txt (NPC 제거됨)
[NPC 저장] → domain['npcs']
    ↓
[AI 응답 생성]
    ↓
[get_lore() 호출] → lore.txt만 읽음 ❌
    ↓
[AI에게 NPC 정보 없이 전달] ❌
```

### After (해결 후)
```
[로어 파일 업로드]
    ↓
[AI가 NPC 추출]
    ↓
[원본 저장] → lore_original.txt (NPC 포함)
[정리본 저장] → lore.txt (NPC 제거됨)
[NPC 저장] → domain['npcs']
    ↓
[AI 응답 생성]
    ↓
[get_lore_with_npcs() 호출] → lore.txt + NPC 목록 결합 ✅
    ↓
[AI에게 로어 + NPC 정보 전달] ✅
```

## 영향을 받는 기능

### ✅ 개선된 기능들

1. **AI 응답 생성**
   - NPC의 성격, 외모, 역할 등을 정확히 반영
   - NPC와의 상호작용이 일관성 있게 진행됨

2. **!분석 명령어**
   - NPC에 대한 질문에 정확한 답변
   - 예: "리엘의 성격은?" → "과묵하고 활솜씨가 뛰어난 엘프 궁수"

3. **!일관성 명령어**
   - NPC 관련 서사의 일관성 검사 가능
   - 캐릭터 설정에 맞지 않는 행동 감지

4. **!세계규칙 명령어**
   - 세계관 분석 시 NPC 정보 포함
   - 더 완전한 세계관 이해

5. **!연대기 추출 명령어**
   - 저장된 파일에 NPC 정보 포함
   - 백업 및 재사용 시 NPC 정보 유지

6. **!로어 명령어**
   - NPC 목록 미리보기 제공
   - NPC 개수 표시

## 테스트 방법

상세한 테스트 시나리오는 `TEST_LORE_NPC.md` 파일을 참조하세요.

### 간단한 테스트 절차

1. **로어 저장 및 NPC 추출 확인**
   ```
   !로어 [파일 또는 텍스트]
   
   예상 결과:
   ✅ [분석 완료]
   장르: ['high_fantasy']
   NPC 추출: 2명
   ```

2. **로어 조회로 NPC 확인**
   ```
   !로어
   
   예상 결과:
   📜 로어 정보
   👥 추출된 NPC: 2명
   👥 NPC 목록 (미리보기):
   • 리엘: ...
   • 가렌: ...
   ```

3. **AI 응답에서 NPC 정보 반영 확인**
   ```
   플레이어: "리엘을 찾아간다"
   
   예상 AI 응답:
   도시 외곽의 경비 초소에서 엘프 궁수 리엘을 발견한다.
   그녀는 과묵하게 당신을 맞이한다...
   ```

## 추가 개선 사항

### 코드 품질
- 상세한 docstring 추가 (NPC 섹션 형식 설명)
- 중복 주석 제거로 가독성 향상
- 사용하지 않는 변수 수정 (`i` → `_`)

### 문서화
- `TEST_LORE_NPC.md`: 포괄적인 테스트 가이드
- 이 파일 (`SOLUTION_SUMMARY.md`): 솔루션 요약

## 결론

이제 로어 파일에 포함된 모든 NPC 정보가 AI 응답 생성 시 올바르게 반영됩니다. AI는 NPC의 특성, 역할, 상태를 정확히 알고 있으며, 이를 바탕으로 일관성 있는 서사를 생성할 수 있습니다.

## 관련 파일

- `domain_manager.py`: 핵심 함수 구현
- `main.py`: 모든 AI 컨텍스트 사용처 수정
- `TEST_LORE_NPC.md`: 테스트 가이드
- `SOLUTION_SUMMARY.md`: 이 파일
