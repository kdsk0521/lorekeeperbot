# 3-축 연출 표기 시스템 (♪ 음악 | ▶ 카메라 | ◎ 사진)

## Context

**문제**: 현재 연출 표기(Directing Notation)가 음악·카메라 용어를 한 줄에 뒤섞어 사용한다. 특히 **시간의 밀도**를 명시적으로 제어하는 어휘가 없다 — Renderer가 "하루를 한 문단으로 압축"할지 "1초를 한 문단으로 늘릴지"를 스스로 판단하고 있다.

**해결**: 연출 표기를 3개 독립 축으로 분리하고, 사진 축(◎)에 시간 밀도 스펙트럼 + 광학 필터를 추가.

| 축 | 제어 대상 | 비유 |
|---|---|---|
| ♪ 음악 | 감정의 강도·리듬 | 지휘자의 박자 |
| ▶ 카메라 | 공간의 구성·움직임 | 카메라맨의 프레이밍 |
| ◎ 사진 | 시간의 밀도·광학 질감 | 사진가의 셔터와 렌즈 |

**시간 밀도 스펙트럼** (사용자 설계):
```
벌브       ████████████████  극압축 (백 년 → 한 줄)
타임랩스   ██████████████    고압축 (계절 → 한 문단)
장노출     ████████████      압축 (하루 → 잔상)
인터벌     ██████████        단편 나열 (스냅샷)
실시간     ████████          1:1 (일반 서술)
슬로모션   ██████            확장 (1초 → 한 문단)
프리즈     ████              정지 (한 순간에 머묾)
```

**광학 필터** — 조건부 modifier:
- `[다중노출]` — 과거와 현재의 중첩 (memory_triggers)
- `[편광]` — 표면 반사 제거, 체면 아래 가시화 (self_opacity) **+ 시점 수호: 제한적 3인칭 유지**
- `[적외선]` — 은폐된 것의 행동 누출 (leak_risk) **+ 시점 수호: 비밀 자체가 아닌 표면 왜곡만**
- `[솔라리제이션]` — 명암 반전, 익숙한 것의 소외 (doom critical)
- `[비네팅]` — 터널 시야, 주변 탈락 (desperate position)

**적외선·편광의 이중 역할**: 지시 도구이면서 동시에 **제한적 3인칭 시점 수호자**. "숨겨진 것이 있다"고 Renderer에게 알려주되, "직접 말하지 마라 — 관찰 가능한 표면 왜곡으로만 보여라"는 제약.

---

## 수정 파일

| 파일 | 변경 | 영향도 |
|---|---|---|
| `une_facade.py` | 6개 표기 테이블 3축 재작성 + 장면 시간 보정 + 광학 필터 로직 | 중 |
| `text_resources.py` | `### P. DIRECTING NOTATION` 3축 가이드로 재작성 | 소 |
| `cognition.py` | render_fingerprint에 `temporal_density` 추가 | 소 |
| `theoria_analyzer.py` | PREVIOUS FRAME에 temporal_density 피드백 | 소 |

---

## Step 1: une_facade.py — 6개 표기 테이블 재작성

**위치**: lines 192–233 (`# ── Directing Notation Tables` 블록)

**새 포맷**: `{주체} | ♪ {music} | ▶ {camera} [{lighting}] | ◎ {time_density}, {color}`

### `_POSITION_NOTATION` (상황: 위치 위험도)

```python
_POSITION_NOTATION = {
    "controlled": "상황 | ♪ mp, andante, legato | ▶ 와이드, 병렬, 팬 [하이키] | ◎ 실시간",
    "risky":      "상황 | ♪ f, allegro, marcato | ▶ 투샷, 대면, 컷 | ◎ 슬로모션",
    "desperate":  "상황 | ♪ ff, presto, staccato | ▶ 로우앵글, 등지기, 점프컷 | ◎ 프리즈",
}
```

### `_ENERGY_NOTATION` (장면: 서사 에너지)

```python
_ENERGY_NOTATION = {
    "idle":       "장면 | ♪ mp, andante, legato | ▶ 팬, 필로우, 롱테이크 [하이키] | ◎ 장노출",
    "steady":     "장면 | ♪ mf, andante, legato | ▶ 아이레벨, 병렬, 매치컷 | ◎ 실시간",
    "rising":     "장면 | ♪ f, allegro, marcato, crescendo | ▶ 투샷, 대면, 크로스컷 | ◎ 인터벌",
    "falling":    "장면 | ♪ p, adagio, legato, diminuendo | ▶ 롱테이크, 등지기, 페이드 | ◎ 장노출",
    "peak":       "장면 | ♪ ff, presto, sforzando | ▶ 클로즈업, 컷, 점프컷 | ◎ 슬로모션",
    "stagnant":   "장면 | ♪ pp, largo, legato | ▶ 롱테이크, 필로우, 높낮이차 | ◎ 장노출",
    "detonation": "장면 | ♪ sfz, presto, sforzando | ▶ 와이드, 몽타주, 컷 | ◎ 프리즈",
    "aftershock": "장면 | ♪ p, adagio, staccato | ▶ 롱테이크, 등지기, 페이드 | ◎ 장노출",
}
```

**시간 밀도 근거**:
- idle/stagnant/falling/aftershock → **장노출**: 느린 누적, 잔상, 시간이 겹침
- steady → **실시간**: 기본 1:1 흐름
- rising → **인터벌**: 축적 중 스냅샷이 나열됨
- peak → **슬로모션**: 절정에서 주관적 시간 확장
- detonation → **프리즈**: 폭발 순간의 정지 화면

### `_VIGOR_NOTATION` (신체 상태)

```python
_VIGOR_NOTATION = {
    "high":       "신체 | ♪ f, allegro, legato | ▶ 와이드, 병렬 [하이키] | ◎ 실시간, 난색",
    "strained":   "신체 | ♪ p, adagio, marcato | ▶ 클로즈업:근육, 높낮이차 | ◎ 슬로모션, 한색",
    "collapsing": "신체 | ♪ pp, largo, staccato | ▶ 클로즈업:호흡, 등지기 [저조도] | ◎ 프리즈, 탈색",
}
```

### `_COMPOSURE_NOTATION` (심리 상태)

```python
_COMPOSURE_NOTATION = {
    "high":       "심리 | ♪ mf, andante, legato | ▶ 투샷, 병렬, 매치컷 | ◎ 실시간",
    "strained":   "심리 | ♪ p, adagio, staccato | ▶ 클로즈업:시선, 높낮이차 | ◎ 슬로모션, 한색",
    "collapsing": "심리 | ♪ pp, largo, sforzando | ▶ 하이앵글, 등지기 [저조도] | ◎ 프리즈, 탈색",
}
```

### `_MIXED_NOTATION` (복합 상태)

```python
_MIXED_NOTATION = {
    "desperate": "신체+심리 | ♪ pp, largo, staccato | ▶ 하이앵글, 등지기 [저조도] | ◎ 프리즈, 탈색",
    "reckless":  "행동 | ♪ f, presto, sforzando | ▶ 와이드, 점프컷 | ◎ 슬로모션, 고채도",
    "fragile":   "의식 | ♪ p, adagio, legato | ▶ 클로즈업:눈, 필로우 | ◎ 장노출, 한색",
}
```

### `_DOOM_NOTATION` (세계 긴장)

```python
_DOOM_NOTATION = {
    "high":     "세계 | ♪ f, allegro, marcato | ▶ 와이드, 대면 [단일광원] | ◎ 인터벌, 고채도",
    "critical": "세계 | ♪ ff, presto, sforzando | ▶ 로우앵글, 점프컷 [저조도] | ◎ 슬로모션, 고채도",
}
```

---

## Step 2: une_facade.py — 장면 시간 밀도 보정 (Scene Override)

`_build_world_layer()` 내부, energy_notation 추가 직후에 삽입.

SceneType이 특정 시간 밀도를 강제하는 경우의 보정:

```python
_SCENE_PHOTO_OVERRIDE = {
    "summary": "◎ 벌브",      # 극압축: 요약 장면
    "combat":  "◎ 슬로모션",  # 전투 시간 확장
    "intimate": "◎ 실시간",   # 친밀 장면: 1:1 현존 필수
}

# _build_world_layer 내부:
scene_override = _SCENE_PHOTO_OVERRIDE.get(scene_type, "")
if scene_override:
    parts.append(f"시간 보정: {scene_override}")
```

에너지 테이블의 기본 ◎ 값과 충돌 시, 보정 라인이 우선. Renderer는 가장 구체적인 지시를 따르도록 MIRROR_WORKSHOP에서 안내.

---

## Step 3: une_facade.py — 광학 필터 로직

`_build_atmosphere_layer()` 내부, vigor/composure/mixed/doom 표기 블록 이후에 삽입.

```python
# ── 광학 필터 (◎ 사진 조건부) ──
dai = bus.dai if isinstance(bus.dai, dict) else {}
optical: List[str] = []

# [다중노출]: memory_triggers → 과거-현재 중첩
mem_triggers = dai.get("memory_triggers", [])
if isinstance(mem_triggers, list) and mem_triggers:
    optical.append("[다중노출]")

# [편광]: self_opacity → 체면 균열 (시점 수호: 표면 모순만)
psyche_states = dai.get("psyche_states", {})
if isinstance(psyche_states, dict):
    for _, npc in psyche_states.items():
        if isinstance(npc, dict):
            if (npc.get("psyche") or {}).get("self_opacity"):
                optical.append("[편광]")
                break

# [적외선]: leak_risk >= medium → 행동 누출 (시점 수호: 비밀이 아닌 왜곡만)
npc_knowledge = dai.get("npc_knowledge", {})
if isinstance(npc_knowledge, dict):
    for _, kn in npc_knowledge.items():
        if isinstance(kn, dict) and kn.get("leak_risk") in ("medium", "high"):
            optical.append("[적외선]")
            break

# [솔라리제이션]: doom >= 80 → 명암 반전
if doom_val >= 80:
    optical.append("[솔라리제이션]")

# [비네팅]: position <= 0.15 → 터널 시야
pos = dai.get("position", {}) if isinstance(dai.get("position"), dict) else {}
if _to_float(pos.get("value", 0.5), 0.5) <= 0.15:
    optical.append("[비네팅]")

if optical:
    parts.append("◎ 광학: " + " ".join(optical))
```

**NPCKnowledge 키 이름 확인 필요**: `bus.dai`에서 NPCKnowledge의 실제 키가 `"npc_knowledge"`인지 `"NPCKnowledge"`인지 확인. (Theoria 스키마는 대문자, waterfall은 소문자로 정규화할 수 있음)

---

## Step 4: text_resources.py — MIRROR_WORKSHOP 3축 가이드

**위치**: `MIRROR_WORKSHOP_PROTOCOL` 내 `### P. DIRECTING NOTATION` 섹션 (lines 217–229)

기존 내용 전체를 아래로 교체:

```
### P. DIRECTING NOTATION (3-축 연출 표기)
Directive의 연출 지시는 세 독립 축으로 장면을 구현한다. 각 축은 | 로 구분된다.

♪ 음악 — 감정 밀도
- 강도: pp(극약) p mp mf f ff(극강) sfz(폭발)
- 속도: largo(극느림) adagio andante allegro presto(극빠름)
- 질감: legato(매끄러움) staccato(끊김) marcato(강조) sforzando(즉각 강타)
- 방향: crescendo(고조) diminuendo(감소)

▶ 카메라 — 공간 구성
- 구도: 클로즈업/와이드/투샷/오버더숄더/로우앵글/하이앵글
- 배치: 대면(대립)/병렬(연대)/등지기(단절)/높낮이차(권력차)/필로우(무인)
- 전환: 몽타주/크로스컷/점프컷/매치컷/페이드
- 흐름: 팬/컷/롱테이크
- [조명]: 하이키/저조도/역광/단일광원/측면광

◎ 사진 — 시간 밀도·광학
- 시간: 벌브(극압축)→타임랩스→장노출(잔상)→인터벌(스냅샷)→실시간→슬로모션→프리즈(정지)
- 색채: 한색(고립)/난색(활력)/탈색(기억)/고채도(강렬)
- 광학: [다중노출](시간중첩) [편광](체면 균열) [적외선](행동 누출) [솔라리제이션](반전) [비네팅](터널)
- 광학 시점 규칙: [편광]과 [적외선]은 제한적 3인칭을 벗어나지 않는다 — 내면을 직접 서술하지 마라. 관찰 가능한 행동 모순과 미세 누출로만 보여라.

◎의 시간 밀도는 산문 리듬에 번역된다: 벌브=한 문장 요약, 장노출=감각 잔상이 겹침, 슬로모션=디테일 확장, 프리즈=정지 순간 머묾.
이 용어를 텍스트에 절대 노출하지 마라. 관찰 가능한 행동과 감각으로 변환하라.
```

**토큰**: ~230 (기존 ~130 대비 +100). Slot 3 정적 캐시 대상.

---

## Step 5: cognition.py — render_fingerprint에 temporal_density 추가

`_extract_batch()` 함수 내 render_fingerprint 섹션에 필드 추가:

```python
"\"temporal_density\": str, "
# ...
"\n- temporal_density: 이번 응답의 실제 시간 밀도 — "
"벌브/타임랩스/장노출/인터벌/실시간/슬로모션/프리즈 중 가장 가까운 것 (1단어)"
```

### theoria_analyzer.py — PREVIOUS FRAME 피드백

`_build_continuity_context()` 내:
```python
if fp.get("temporal_density"):
    parts.append(f"- TemporalDensity: {fp['temporal_density']}")
```

지시한 시간 밀도와 실제 Renderer 출력의 밀도를 비교해 다음 턴 Theoria가 드리프트 감지 가능.

### orchestration.py — fingerprint 저장 시 temporal_density 포함

기존 코드 (lines 888-895)에서 `render_fingerprint` dict 구성 시 `temporal_density` 필드 추가:

```python
fingerprint = {k: rfp.get(k, "") for k in ("gaze", "lighting", "palette", "rhythm", "temporal_density")}
```

---

## 토큰 예산

| 컴포넌트 | 이전 | 이후 | 차이 |
|---|---|---|---|
| MIRROR_WORKSHOP §P (Slot 3, 정적) | ~130 | ~230 | +100 |
| 표기 문자열 평균 (Slot 30, 동적) | ~20/entry | ~28/entry | +8/entry |
| 광학 필터 라인 (조건부) | 0 | ~15 | +15 |
| 시간 보정 라인 (조건부) | 0 | ~8 | +8 |
| **턴당 총 증가 (일반)** | — | — | **~40–60** |
| **턴당 총 증가 (최대)** | — | — | **~120** |

---

## 데이터 흐름

```
Turn N:
  Theoria → EnergyDirection, SceneType, Position, time_flow
           + psyche_states (self_opacity), NPCKnowledge (leak_risk)
           + memory_triggers, doom_clocks

  _build_world_layer():
    Position → _POSITION_NOTATION["risky"]
      → "상황 | ♪ f, allegro, marcato | ▶ 투샷, 대면, 컷 | ◎ 슬로모션"
    Energy → _ENERGY_NOTATION["rising"]
      → "장면 | ♪ f, allegro, marcato, crescendo | ▶ 투샷, 대면, 크로스컷 | ◎ 인터벌"
    SceneType → _SCENE_PHOTO_OVERRIDE (있으면 보정 라인)

  _build_atmosphere_layer():
    Vigor → _VIGOR_NOTATION
    Composure → _COMPOSURE_NOTATION
    Mixed → _MIXED_NOTATION (조건부)
    Doom → _DOOM_NOTATION (50+)
    DAI fields → 광학 필터 (조건부):
      memory_triggers → [다중노출]
      self_opacity → [편광] (시점 수호)
      leak_risk → [적외선] (시점 수호)
      doom >= 80 → [솔라리제이션]
      position <= 0.15 → [비네팅]

  → Slot 30: 3축 표기 + 광학 필터
  → Renderer reads Slot 3 (MIRROR_WORKSHOP) → 3축 해석 → 산문

  Cognition batch:
    → render_fingerprint.temporal_density (선택: 실제 시간 밀도 기록)
    → 다음 턴 PREVIOUS FRAME으로 피드백
```

---

## 편광·적외선의 이중 역할

| 필터 | 지시 역할 | 시점 수호 역할 |
|---|---|---|
| [편광] | NPC의 self_opacity가 있다 → 체면 균열을 보여라 | 내면을 직접 서술하지 마라 — **행동 모순**(말과 몸의 불일치)으로만 |
| [적외선] | NPC의 leak_risk가 높다 → 숨기는 것이 새어나온다 | 비밀 자체를 밝히지 마라 — **미세 누출**(말실수, 시선 회피, 과잉 부정)로만 |

MIRROR_WORKSHOP의 `광학 시점 규칙` 한 줄이 이를 강제. 기존 PC_AUTONOMY_DOCTRINE의 제한적 3인칭 원칙과 정확히 정렬.

---

## 검증

1. `py_compile` — 수정 4개 파일
2. `_build_world_layer()` 모의 호출: energy="idle" → `◎ 장노출` 확인
3. `_build_world_layer()` 모의: scene_type="summary" → 보정 라인 `◎ 벌브` 확인
4. `_build_atmosphere_layer()` 모의: memory_triggers 있음 → `[다중노출]` 확인
5. `_build_atmosphere_layer()` 모의: self_opacity 있음 → `[편광]` 확인
6. `_build_atmosphere_layer()` 모의: doom=85 → `[솔라리제이션]` 확인
7. 모든 표기 문자열에 `| ♪` / `| ▶` / `| ◎` 세 구분자 존재 확인
8. MIRROR_WORKSHOP 토큰 카운트 ~230 확인
9. 실제 턴 테스트: Renderer가 표기 용어를 산문에 노출하지 않는지 확인
10. 실제 턴 테스트: [편광] 활성 시 NPC 내면이 직접 서술되지 않는지 확인
