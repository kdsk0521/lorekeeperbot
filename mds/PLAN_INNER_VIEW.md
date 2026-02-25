# Inner View (이면 보기) — 계획서

> **상태**: 계획만 — 미구현
> **작성**: 2026-02-25

---

## 개요

서사 응답 아래 접기/펼치기 버튼으로 NPC 속마음·숨겨진 맥락을 보여주는 기능.

**영감**: 다른 AI RP 봇의 "히든 스포일러" Embed 접기 UI

---

## UI 동작

```
[서사 본문 — 일반 메시지]

        [✦ 이면 보기]  ← discord.ui.Button
```

버튼 클릭 → `message.edit(embed=...)` 로 Embed 추가:

```
[서사 본문]

┌─ ✦ {NPC이름}의 이면 ─────────
│ 서사적 내면 텍스트 (2-3문장)
│ > '속마음 인용'
└───────────────────────────────

        [✦ 이면 접기]  ← 토글
```

재클릭 → `message.edit(embed=None)` 로 Embed 제거.

---

## 생성 방식: Flash Batch 피기백 (방식 B)

### 왜 Pro가 아닌가

Pro한테 시키면 아이스버그 모순 발생:
- 본문: "숨겨라, 암시해라" (iceberg 규율)
- 이면: "다 보여줘라"
- 같은 모델이 동시에 양립 → 본문 품질 오염 리스크

### Flash 분리의 장점

```
Pro: 아이스버그 규율대로 서사 렌더링 (변경 없음)
  ↓ 응답 완료
Flash(Batch): DAI + 서사 응답 → "이면" 텍스트 생성
```

- Pro의 규율 간섭 없음
- 이면은 "부록" — 본문 수준의 문학성 불필요
- 비용 6배 차이 (Flash $0.0005 vs Pro $0.003/턴)

---

## 데이터 소스

Flash Batch에 전달할 컨텍스트 (이미 Batch 콜에 존재):

| 소스 | 필드 | 용도 |
|------|------|------|
| DAI | `psyche_states` | NPC 심리 상태, 방어기제 |
| DAI | `self_opacity` | 자기인식 수준 |
| DAI | `deception_cues` | 거짓말 단서 |
| DAI | `secrets` | 비밀 관련 |
| DAI | `leak_risk` | 누출 위험도 |
| 대사 지시 | Hidden 축 | 숨긴 것 요약 |
| Pro 응답 | 서사 본문 | 무엇이 "보여진" 건지 |

---

## 구현 개요

### 1. Flash Batch 확장 (cognition.py)

기존 Batch 섹션(physical/social/narrative/quest/world_state/render_fingerprint)에 `inner_view` 섹션 추가:

```
### inner_view
이 장면에서 초점 NPC의 숨겨진 내면을 2-3문장의 한국어 서사로 작성.
카메라에 잡히지 않은 감정, 속마음, 거짓말의 이유.
> 인용 형식으로 핵심 속마음 1줄 포함.
```

출력: `{"inner_view": {"npc_name": "...", "text": "..."}}`

### 2. Discord UI (main.py 또는 별도 파일)

```python
class InnerViewButton(discord.ui.View):
    def __init__(self, inner_data: dict):
        super().__init__(timeout=300)  # 5분 후 버튼 비활성
        self.inner_data = inner_data
        self.expanded = False

    @discord.ui.button(label="✦ 이면 보기", style=discord.ButtonStyle.secondary)
    async def toggle(self, interaction, button):
        if self.expanded:
            await interaction.response.edit_message(embed=None)
            button.label = "✦ 이면 보기"
        else:
            embed = discord.Embed(
                title=f"✦ {self.inner_data['npc_name']}의 이면",
                description=self.inner_data['text'],
                color=0x2F3136  # 어두운 색
            )
            await interaction.response.edit_message(embed=embed)
            button.label = "✦ 이면 접기"
        self.expanded = not self.expanded
```

### 3. 응답 전송 수정 (orchestration.py / main.py)

```python
# inner_view가 있으면 버튼 View 첨부
if inner_data := cognition_result.get("inner_view"):
    view = InnerViewButton(inner_data)
    await message.edit(content=narrative_text, view=view)
```

### 4. 설정 토글

`!설정 이면보기 on/off` — 기본값 OFF. ON일 때만 Batch에 inner_view 섹션 추가.

---

## 비용 영향

| 항목 | 추가 토큰 | 추가 비용/턴 |
|------|----------|-------------|
| Flash Batch 입력 (프롬프트 확장) | +~100 | +$0.00005 |
| Flash Batch 출력 (inner_view JSON) | +~150 | +$0.00045 |
| **합계** | **+~250** | **~$0.0005/턴** |

500턴 누적: **+$0.25 (₩363)** — 무시 가능

---

## 고려사항

- **멀티플레이어**: 버튼은 누구나 누를 수 있음. ephemeral 응답으로 바꾸면 개인만 볼 수 있지만 UX가 달라짐
- **NPC 없는 턴**: 독백/탐색 장면에선 inner_view 생략 (DAI gaze가 None이면 스킵)
- **히스토리 미포함**: inner_view 텍스트는 세션 히스토리에 넣지 않음 (Pro 입력 증가 방지)
- **timeout**: 5분 후 버튼 비활성 — 오래된 메시지 인터랙션 에러 방지
