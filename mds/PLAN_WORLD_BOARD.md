# World Board (세계 게시판) — 계획서

> **상태**: 계획만 — 미구현
> **작성**: 2026-02-25

---

## 개요

서술턴과 완전 분리된 Discord 스레드에서 세계가 살아있는 느낌을 주는 게시판/소식통 모듈.
장르에 따라 형태가 자동으로 달라짐 (길드 게시판, SNS, 함선 통신 등).

**관련 기능**: Inner View (PLAN_INNER_VIEW.md) — 메인 채널 버튼 토글, 별개 기능

---

## 핵심 원칙

1. **서술턴과 완전 분리** — 스레드에서만 보임, 본문 서사 흐름 방해 없음
2. **DLC 없이도 작동** — 기본 트리거(시간/장소)로 최소 기능, DLC ON이면 풍성
3. **피드백 없음** (v1) — 순수 출력, Theoria가 읽지 않음
4. **읽기 전용** (v1) — 플레이어 상호작용 없음
5. **장르 스킨 = 장르 정보** — 별도 매핑 불필요, 로어 분석에서 이미 추출된 장르 데이터가 스킨

---

## 스레드 생명주기

```
세션 시작
  │
  ├── (첫 트리거 발생) → 스레드 생성 + 📌 핀
  │     이름: 장르에 맞게 Flash가 제안 or 기본값
  │     예: "📋 모험자 길드 게시판" / "📱 타임라인" / "📡 함선 통신 로그"
  │
  ├── 트리거마다 → 스레드에 게시
  │
  └── !클리어 → 스레드 아카이브 (또는 삭제)
```

빈 스레드 방지: 첫 게시물이 생길 때 스레드 생성.

---

## 트리거 시스템

### Base (모듈 없이 작동)

| 트리거 | 조건 | 빈도 |
|--------|------|------|
| `!시간` 시간 경과 | 유저가 시간 명령 사용 시 | 유저 의존 |
| N턴 정기 업데이트 | 예: 10턴마다 | ~10턴 |
| 장소 변경 | world_state.location 변경 감지 | 비정기 |

### DLC Enhancement (모듈 ON이면 추가 재료)

| 트리거 | 모듈 | 내용 |
|--------|------|------|
| 스토리텔러 이벤트 발생 | Storyteller | 이벤트의 "세계적 반향" — 소문, 공지, 뉴스 |
| NPC autonomous 트리거 | NPC Autonomous | NPC가 남긴 흔적 — 쪽지, 소문, 행동 결과 |
| Doom 단계 변화 | Doom | 분위기 변화 반영 — 경고문, 불길한 소식 |

**DLC 없으면**: 시간/장소 기반 기본 세계 소식만
**DLC 전부 ON**: 이벤트 + NPC 행동 + 분위기까지 반영된 풍성한 게시판

---

## 생성 방식: Flash

### 프롬프트 구조

```
시스템: 세계 게시판 콘텐츠 생성기

입력:
- 세계관: {genres.stage}
- 분위기: {genres.atmosphere}
- 세계 규칙: {world_constraints}
- 현재 장소: {world_state.location}
- 최근 사건: {트리거 데이터 — 이벤트/NPC행동/둠변화 등}
- NPC 이름 목록: {활성 NPC들}
- 기존 게시물 수: {중복 방지용}

지시:
이 세계에서 자연스러운 게시판/소식통 형태로 1-2개 짧은 글을 써라.
세계관 내부의 인물이 쓴 것처럼. 한국어. 각 100-200자.
```

장르 스킨은 별도 테이블 불필요 — `genres.stage` + `atmosphere` + `world_constraints`가 Flash에게 자연스러운 스타일을 유도.

### 출력 형식

```json
{
  "posts": [
    {
      "author": "길드장 마르코",
      "title": "북쪽 숲 실종자 수색대 모집",
      "body": "3일 전부터 북쪽 숲에서 약초꾼 2명이 돌아오지 않고 있습니다. 수색에 참여할 모험자를 모집합니다. 보상 금화 50. — 모험자 길드"
    }
  ],
  "board_name": "모험자 길드 게시판"
}
```

---

## Discord 구현

### 스레드 관리

```python
# 첫 게시 시 스레드 생성
async def ensure_world_board_thread(channel, board_name):
    # ai_session_memory에서 thread_id 확인
    thread_id = session_memory.get("world_board_thread_id")
    if thread_id:
        thread = channel.get_thread(thread_id)
        if thread:
            return thread

    # 없으면 생성
    thread = await channel.create_thread(
        name=f"📋 {board_name}",
        type=discord.ChannelType.public_thread,
        auto_archive_duration=1440  # 24시간 비활동 시 아카이브
    )
    session_memory["world_board_thread_id"] = thread.id
    return thread

# !클리어 시 정리
async def cleanup_world_board(channel, session_memory):
    thread_id = session_memory.get("world_board_thread_id")
    if thread_id:
        thread = channel.get_thread(thread_id)
        if thread:
            await thread.edit(archived=True)
```

### 게시 형식

```python
# Embed로 게시 (세계관 내 문서 느낌)
embed = discord.Embed(
    title=post["title"],
    description=post["body"],
    color=0x2F3136
)
embed.set_author(name=post["author"])
embed.set_footer(text=f"— {temporal_context}")  # 세계관 내 시간

await thread.send(embed=embed)
```

---

## 비용 영향

| 항목 | 토큰 | 비용 |
|------|------|------|
| Flash 입력 (세계 상태 + 프롬프트) | ~800 | $0.0004 |
| Flash 출력 (1-2 게시물 JSON) | ~200 | $0.0006 |
| **게시 1회당** | **~1,000** | **~$0.001** |

**빈도별 500턴 비용:**
- 10턴마다: 50회 × $0.001 = **$0.05** (₩73)
- 스토리텔러 연동: ~70회 × $0.001 = **$0.07** (₩102)
- 최대 (모든 트리거): ~100회 × $0.001 = **$0.10** (₩145)

→ 전체 비용($55) 대비 **0.1-0.2%** — 무시 가능

---

## 미래 확장 (v2+)

- **피드백 루프**: 게시판 요약 → Theoria 컨텍스트 (세계가 자기 소문을 인식)
- **플레이어 상호작용**: 리액션으로 의뢰 수락 → 퀘스트 자동 등록
- **PC 게시**: 플레이어가 게시판에 글 쓰기 → 서사에 반영
- **NPC 반응 스레드**: 특정 게시물에 NPC가 댓글 다는 형태
- **멀티 게시판**: 장소별로 다른 스레드 (술집 vs 시청 vs 암시장)

---

## 설정

```
!설정 게시판 on/off    — 기본값 OFF
!설정 게시판 빈도 N    — N턴마다 정기 업데이트 (기본 10)
```
