# 반팽팽함 수정 — 무대장치 PULL/PUSH 균형 회복

## Context

**출처**: `ANALYSIS_STAGE_DEVICES.md` (52개 무대장치 분석)

**진단**: 52개 장치 중 70%가 PULL(억제), 30%가 PUSH(허용). 모델이 매 문장마다 10개 규칙을 자기검열 → "가장 안전한 선택 = 차갑고 절제된 톤"으로 수렴. 느린 장면도 느리게 느껴지지 않는 원인.

**이전 수정 (이미 완료)**:
- ✅ Rubin Vase — 양방향 질문 (따뜻함도 진실일 수 있다)
- ✅ Cargo Check — 이완도 기능 (일상적 따뜻함 = baseline)
- ✅ Delayed Response — 미해결 감정만 지연 (안정된 친밀함 = 직접적)
- ✅ Telescope v4 — Attractor/Scheme/Gravity/Unshown 필드 추가 완료

**남은 고순위 항목 4개**:
1. **§E No Comfort** — "Comfort costs" 절대 규칙. 모든 위로에 비용 부과 → earned comfort도 차단
2. **§F No Echo** — NPC 공감 전면 억제 → 자연스러운 감정 정렬도 "echo"로 취급
3. **convergence_warning 지시문** — "불편함을 유지하라" 무조건 지시 → earned resolution도 경직
4. **NPC 관계 초기값** — 로어북에 "소꿉친구"라 써도 depth=0으로 시작 → 첫 세션부터 차가움

---

## Part 1: 기본 레이어 수정 (DLC 무관)

### Step 1. §E No Comfort 수정

**파일**: `text_resources.py` (line 124-126, MIRROR_WORKSHOP 내)

**현재**:
```
### E. NO COMFORT
Resolution is earned, not promised. Wounds persist until depicted struggle heals them. Comfort costs.
- ✅ Months later, she still couldn't say his name. She'd tried once, at a party. Her voice cracked. She didn't try again.
```

**수정 후**:
```
### E. NO COMFORT
Resolution is earned, not promised. Wounds persist until depicted struggle heals them. Comfort costs.
- ✅ Months later, she still couldn't say his name. She'd tried once, at a party. Her voice cracked. She didn't try again.
But earned comfort is free. When struggle has been depicted — when the character has fought through silence, distance, and misunderstanding — the warmth that follows needs no price tag. A meal shared after reconciliation, a hand held without flinching, laughter without an edge. Not every warm moment must be purchased with new pain.
```

**설계 근거**:
- §G No Premature Convergence에는 이미 "But when characters HAVE earned resolution... render the peace" 예외가 있음 (line 137)
- §E에만 대응 예외가 없어서, 모델이 earned comfort에도 비용을 부과
- "earned comfort is free" = §G의 earned resolution과 동일한 논리를 §E에 적용
- 예시를 구체적으로 제시하여 모델이 "어떤 상황이 free인지" 판단 가능

### Step 2. §F No Echo 수정

**파일**: `text_resources.py` (line 128-131, MIRROR_WORKSHOP 내)

**현재**:
```
### F. NO ECHO
Characters respond from their own disruption, not by mirroring the other's emotion.
- ✅ He told her. She didn't say anything. Then she started clearing the dishes. "These need to soak," she said.
The gap between characters—where each misreads, assumes, projects—is where the story lives.
```

**수정 후**:
```
### F. NO ECHO
Characters respond from their own disruption, not by mirroring the other's emotion.
- ✅ He told her. She didn't say anything. Then she started clearing the dishes. "These need to soak," she said.
The gap between characters—where each misreads, assumes, projects—is where the story lives.
But alignment is not echo. Two people can feel the same thing for different reasons. She grieves the loss; he grieves his failure to prevent it. Same tears, different wells. When characters genuinely arrive at the same emotional place through their own paths, render both paths — that is convergence, not mirroring.
```

**설계 근거**:
- 현재 규칙은 "NPC가 PC 감정을 전혀 반영하지 않음" → 인간관계의 자연스러운 공명도 차단
- 핵심 구분: echo = 무조건 따라하기 (자기 이유 없음) vs alignment = 각자의 이유로 같은 방향
- "Same tears, different wells" — 같은 감정이되 다른 근원. 이것은 echo가 아니라 convergence
- 예시로 "두 사람의 경로"를 보여주라는 지시 → 모델이 NPC의 독립적 이유를 렌더링하면서도 같은 방향 허용

### Step 3. convergence_warning 지시문 정밀화

**파일**: `iceberg.py` (line 391, `_FLAG_DIRECTIVES`)

**현재**:
```python
"convergence_warning": "장면이 갈등 없이 합의에 도달하고 있다. 불편함을 유지하라.",
```

**수정 후**:
```python
"convergence_warning": "관계 변화가 빠르다. 이 속도에 맞는 인과적 근거가 있는지 점검하라. 근거 없으면 속도를 늦춰라.",
```

**설계 근거**:
- 현재 지시문은 "불편함을 유지하라" → 무조건 불편한 톤 강제
- analysis_resources.py의 Theoria-side 정의는 이미 정밀함: "Earned resolution after sufficient buildup is valid storytelling"
- 문제: Theoria가 convergence_warning=true를 보내면, iceberg가 이를 "불편함 유지"로 번역 → 뉘앙스 손실
- 수정: "불편함 유지" → "인과적 근거 점검". 근거가 있으면 자연스럽게 진행, 없으면 감속
- Theoria의 판단 의도(속도 과다)를 보존하면서, 렌더러에게 "무조건 차단"이 아닌 "점검 후 판단" 재량 부여

### Step 4. echo_warning 지시문 정밀화 (연동)

**파일**: `iceberg.py` (line 392, `_FLAG_DIRECTIVES`)

**현재**:
```python
"echo_warning": "NPC가 PC 감정을 따라하고 있다. NPC만의 반응을 만들어라.",
```

**수정 후**:
```python
"echo_warning": "NPC가 PC 감정을 그대로 반사하고 있다. NPC 자신의 이유에서 나온 반응인지 점검하라.",
```

**설계 근거**:
- §F 수정과 일관성 유지
- "NPC만의 반응을 만들어라" → NPC가 PC와 다른 감정을 가져야 한다는 뜻으로 해석됨
- "NPC 자신의 이유에서 나온 반응인지 점검하라" → 같은 방향도 가능, 단 자기 이유 필요

### Step 5. NPC 관계 초기값 — 로어북 키워드 파싱

**파일**: `npc_manager.py` (`_extract_structured_fields()`, line ~86)

**문제**:
- `_extract_structured_fields()`가 role/location/tone/personality만 추출, 관계 깊이는 무시
- `domain_manager.update_npc_attitude()`에서 depth/tension이 0으로 시작 (line 415-416)
- Theoria는 NPC 프로필을 보고 관계를 분석하지만, persistent depth에 반영 안 됨
- 분석 API 실패 시 아무 초기값도 없음

**구현**: `_extract_structured_fields()`에 관계 키워드 매칭 추가

```python
# npc_manager.py — _extract_structured_fields() 내 추가

_RELATION_KEYWORDS = {
    # (depth, tension) 초기값
    # 친밀/가족
    "소꿉친구": (60, 5),
    "childhood friend": (60, 5),
    "절친": (65, 5),
    "best friend": (65, 5),
    "친구": (40, 5),
    "friend": (40, 5),
    "가족": (55, 10),
    "family": (55, 10),
    "형제": (50, 15),
    "자매": (50, 15),
    "sibling": (50, 15),
    "부모": (55, 15),
    "parent": (55, 15),
    "연인": (70, 10),
    "lover": (70, 10),
    "애인": (70, 10),
    "partner": (60, 10),
    "배우자": (65, 10),
    "spouse": (65, 10),
    # 중립/직업
    "동료": (30, 5),
    "colleague": (30, 5),
    "이웃": (20, 5),
    "neighbor": (20, 5),
    "지인": (15, 5),
    "acquaintance": (15, 5),
    "스승": (40, 10),
    "mentor": (40, 10),
    "제자": (35, 10),
    "student": (35, 10),
    # 적대/갈등
    "원수": (40, 70),
    "enemy": (40, 70),
    "라이벌": (35, 50),
    "rival": (35, 50),
    "적": (30, 60),
}

# 프로필 텍스트에서 관계 키워드 스캔
desc_lower = desc.lower()
best_depth, best_tension = 0, 0
for keyword, (d, t) in _RELATION_KEYWORDS.items():
    if keyword in desc_lower:
        if d > best_depth:  # 가장 깊은 관계 우선
            best_depth, best_tension = d, t
if best_depth > 0:
    fields["initial_depth"] = best_depth
    fields["initial_tension"] = best_tension
```

**depth 적용 시점**: `orchestration.py`의 NPC 태도 첫 sync 시

```python
# orchestration.py — NPC 태도 업데이트 부분 (line ~146-172)

# 첫 등장 NPC: 프로필에서 초기 depth 가져오기
existing_att = domain_manager.get_npc_attitudes(channel_id).get(n_name, {})
if existing_att.get("depth", 0) == 0:  # 아직 초기값
    npc_data = npc_manager.get_npc(channel_id, n_name) or {}
    initial_depth = npc_data.get("initial_depth", 0)
    initial_tension = npc_data.get("initial_tension", 0)
    if initial_depth > 0:
        domain_manager.update_helena_metric(
            channel_id, n_name,
            depth_delta=initial_depth,
            tension_delta=initial_tension
        )
```

**설계 근거**:
- API 콜 0 — 순수 문자열 매칭, 분석 실패와 무관
- 결정론적 — 같은 프로필 = 같은 초기값
- 기존 패턴 재활용 — `_extract_structured_fields()`에 필드 추가만
- depth > 0이면 Theoria의 depth 피드백 힌트가 첫 턴부터 작동 (`_DEPTH_PSYCHE_HINTS`)
- §E/§F 프롬프트 수정과 결합: 코드가 "이미 친함" 제공 → 프롬프트가 "earned comfort" 허용
- 적대 관계도 지원: depth(서로 잘 앎) + tension(긴장 높음) → 원수 관계 표현

**데이터 흐름**:
```
NPC 프로필 등록 → _extract_structured_fields() → initial_depth/initial_tension 저장
                                                          ↓
첫 턴 orchestration → depth==0 체크 → initial_depth 적용 → update_helena_metric()
                                                          ↓
다음 턴 Theoria → _DEPTH_PSYCHE_HINTS(depth=60) → "친밀한 관계" 피드백
                                                          ↓
§E/§F 프롬프트 → earned comfort 허용 → 자연스러운 첫 세션 톤
```

---

## Part 1 토큰 영향

| 항목 | 토큰 변화 | 위치 |
|---|---|---|
| §E No Comfort 추가 | +40 | text_resources.py (정적) |
| §F No Echo 추가 | +45 | text_resources.py (정적) |
| convergence_warning | ±0 (교체) | iceberg.py (동적) |
| echo_warning | ±0 (교체) | iceberg.py (동적) |
| NPC 관계 키워드 테이블 | 0 (코드 전용) | npc_manager.py |
| NPC 초기 depth 적용 | 0 (코드 전용) | orchestration.py |
| **합계** | **~+85** | 정적 프롬프트, 코드 변경은 토큰 무관 |

MIRROR_WORKSHOP은 Slot 3 (Primacy Zone)이므로 +85 토큰은 모델의 첫인상에 직접 영향. PULL 900토큰 → PULL 900 + PUSH 85 = 약간의 균형 회복.

---

## Part 1 수정 파일 요약

| 파일 | 변경 | 라인 |
|---|---|---|
| `text_resources.py` | §E No Comfort — earned comfort 예외 추가 | ~124-126 |
| `text_resources.py` | §F No Echo — alignment ≠ echo 추가 | ~128-131 |
| `iceberg.py` | convergence_warning 지시문 교체 | ~391 |
| `iceberg.py` | echo_warning 지시문 교체 | ~392 |
| `npc_manager.py` | `_extract_structured_fields()`에 관계 키워드 매칭 추가 | ~86-149 |
| `orchestration.py` | 첫 등장 NPC에 initial_depth 적용 로직 추가 | ~146-172 |

---

## Part 2: 장면 팔레트 시스템 — 조명 × 색채 × 에너지

### 설계 철학

**장면 분위기 = 조명 × 색채 × 에너지 (3축 조합)**

```
기존 문제:
  따뜻함 어휘 = "난색" 1개
  차가움 어휘 = 한색, 탈색, 저조도, 단일광원, 단색조... 6개+
  → 모델이 차가움은 풍부하게 표현, 따뜻함은 표현할 도구가 없음

해결:
  조명 10종 (따뜻한 것 4 + 중립 2 + 차가운 것 4)
  색채 10종 (따뜻한 것 4 + 중립 1 + 차가운/강렬 5)
  에너지 8종 (기존 유지: idle, steady, rising, falling, peak, stagnant, detonation, aftershock)
  → 10 × 10 × 8 = 800 조합. 분위기가 프리셋이 아니라 조합에서 생성됨.
```

**구조 계층**:
```
§P 팔레트 = 조명 × 색채 (장면 전체 — "방의 빛과 색")
  └ 에너지 = 기존 energy_direction (장면 리듬)
    └ ♪▶◎ = 개체별 연출 (방 안에서 카메라가 비추는 것)
```

§P는 "방의 조명을 켜는 것". ♪▶◎는 "그 방 안에서 촬영하는 것".
같은 pp(조용)라도 §P가 골든아워+앰버면 따뜻한 조용함, 저조도+한색이면 차가운 조용함.

### 어휘 체계

#### 조명 (Lighting) — 10종

**따뜻한 빛 (4종)**:

| 이름 | 물성 | 효과 | 사용 맥락 |
|------|------|------|----------|
| **골든아워** | 일몰·일출의 수평 황금빛 | 따뜻함, 안전, 시간의 무게 | 저녁 대화, 하교길, 창가 |
| **실내등** | 백열등, 스탠드, 간접조명 | 생활감, 편안함, 집 | 부엌, 거실, 일상 |
| **확산광** | 경계 없는 부드러운 빛 | 몽환, 안도, 경계 해소 | 꿈, 회복, 안도의 순간 |
| **창문빛** | 커튼 넘어 들어오는 자연광 | 평화, 아침, 세계와의 연결 | 기상, 조용한 오후, 병실 |

**중립 (2종)**:

| 이름 | 물성 | 효과 | 사용 맥락 |
|------|------|------|----------|
| **자연광** | 있는 그대로의 빛 | 중립, 사실주의 | 기본값, 특별한 연출 없음 |
| **하이키** | 고르게 밝은 빛 | 명확, 안전, 공적 | 낮, 사무실, 공공장소 |

**차가운/극적 빛 (4종)**:

| 이름 | 물성 | 효과 | 사용 맥락 |
|------|------|------|----------|
| **저조도** | 그림자가 지배하는 어둠 | 위험, 비밀, 은밀 | 밤, 뒷골목, 밀담 |
| **단일광원** | 한 곳에서만 빛 | 고립, 초점, 심문 | 고백, 결단, 대치 |
| **역광** | 뒤에서 오는 빛 = 실루엣 | 정체 숨김, 신비, 위압 | 등장, 퇴장, 정체 불명 |
| **측면광** | 얼굴 반만 비추는 빛 | 이중성, 반진실, 경계 | 거짓말, 도덕적 회색지대 |

#### 색채 (Color) — 10종

**따뜻한 색 (4종)**:

| 이름 | 물성 | 효과 | 사용 맥락 |
|------|------|------|----------|
| **앰버** | 호박색, 꿀빛, 나무결 | 편안함, 생활감, 오래된 친밀 | 부엌, 술자리, 난로 |
| **파스텔** | 연한 색, 낮은 채도 | 부드러움, 자극 없음, 보호 | 아이, 봄, 회복기 |
| **선셋** | 주황+분홍+보라 그라데이션 | 전환, 아쉬움, 시간의 흐름 | 이별, 약속, 하루의 끝 |
| **세피아** | 바랜 사진, 갈색 톤 | 추억, 그리움, 오래된 따뜻함 | 회상, 옛 장소, 재회 |

**중립 (1종)**:

| 이름 | 물성 | 효과 | 사용 맥락 |
|------|------|------|----------|
| **자연색** | 있는 그대로 | 기본값, 연출 의도 없음 | 기본 |

**차가운/강렬한 색 (5종)**:

| 이름 | 물성 | 효과 | 사용 맥락 |
|------|------|------|----------|
| **한색** | 파랑, 강철, 겨울 공기 | 고립, 거리, 형식 | 대치, 거절, 고독 |
| **탈색** | 표백, 안개, 과다노출 | 기억, 해리, 무감각 | 충격 후, 꿈, 해리 |
| **고채도** | 포화된 원색 | 강렬, 위기, 열정 | 전투, 고백, 분노 |
| **보색대비** | 따뜻한 얼굴 + 차가운 그림자 | 충돌, 이중성, 분열 | 내적 갈등, 모순 |
| **단색조** | 한 가지 색이 프레임 지배 | 집착, 정지, 매몰 | 집중, 집착, 폐쇄 |

### 기본 레이어 배치 (DLC 무관)

**정적 어휘 (text_resources.py — MIRROR_WORKSHOP 또는 PHYSICAL_RENDERING)**:

```
### §P. SCENE PALETTE
[§P light, color] in telescope = this turn's literal sensory vocabulary.
Write the physical textures of the given light and color. Not metaphor.
  golden_hour+amber → steam, wood grain, honey light, skin warmth.
  low_key+cool → steel, tile, fluorescent hum, cold concrete.
  diffused+pastel → cotton, morning haze, pale sky, quiet skin.
```

~35 토큰. 영어 통일. 옵션 목록 불필요 — 모델은 Telescope prefill에서 `[§P X, Y]`를 직접 받음.

**동적 주입 (slot_manager.py — Telescope prefill)**:

```python
# slot_manager.py — _build_telescope_prefill() 확장

# scene_type 7종 → (기본 조명, 기본 색채) — 시작점. 모델이 Telescope에서 조정 가능.
_SCENE_PALETTE_DEFAULT = {
    "intimate":    ("indoor_lamp", "amber"),
    "social":      ("high_key", "natural"),
    "combat":      ("low_key", "vivid"),
    "exploration": ("natural", "natural"),
    "tension":     ("single_source", "cool"),
    "summary":     ("diffused", "washed"),
    "normal":      ("natural", "natural"),
}

# energy_direction 8종 → 보정 (None = 기본 유지)
_ENERGY_PALETTE_MOD = {
    "idle":        (None, None),
    "steady":      (None, None),
    "rising":      (None, "vivid"),
    "falling":     ("diffused", None),
    "peak":        ("single_source", "vivid"),
    "stagnant":    (None, "mono"),
    "detonation":  ("low_key", "vivid"),
    "aftershock":  ("diffused", "washed"),
}

def _resolve_palette(scene_type: str, energy: str) -> str:
    base_light, base_color = _SCENE_PALETTE_DEFAULT.get(scene_type, ("natural", "natural"))
    energy_mod = _ENERGY_PALETTE_MOD.get(energy, (None, None))
    light = energy_mod[0] if energy_mod[0] else base_light
    color = energy_mod[1] if energy_mod[1] else base_color
    return f"[§P {light}, {color}]"
```

Telescope prefill에 `[§P 골든아워, 앰버]` 형태로 삽입 → ★ 마크 필드처럼 참조.
모델은 이 기본값을 참고하되, Telescope 출력에서 장면에 더 맞는 조합으로 조정 가능.

### 3축과의 관계

```
§P 팔레트 (장면 전체)
  = 조명(빛의 방향·온도) × 색채(프레임의 색온도)
  → 산문의 감각 어휘 결정: 어떤 질감, 어떤 온도, 어떤 물성으로 쓸 것인가

  └ 에너지 (장면 리듬 — 기존 energy_direction)
    → 문장 밀도, 페이싱, 긴장도

    └ ♪▶◎ (개체별 연출)
      ♪ 음악 = 감정 밀도 (다이나믹, 템포, 아티큘레이션)
      ▶ 카메라 = 공간 구성 (구도, 배치, 전환)
      ◎ 사진 = 시간 밀도 (압축/확장, 광학 필터)

§P가 골든아워+앰버면 → 개체별 ♪가 pp(조용)여도 따뜻한 조용함.
§P가 저조도+한색이면 → 개체별 ♪가 pp여도 차가운 조용함.
같은 에너지 idle이라도 §P에 따라 "평화로운 정적" vs "불안한 정적"이 결정됨.
```

### 기존 3축 테이블에서 색채·조명 분리

현재 `_VIGOR_NOTATION["high"]`에 `난색`이, `_MIXED_NOTATION["desperate"]`에 `탈색, [저조도]`가 포함됨.
§P 도입 후: 개체별 notation에서 색채/조명 제거, §P로 이관.

```
변경 전: "신체 | ♪ f, allegro, legato | ▶ 와이드, 병렬 [하이키] | ◎ 실시간, 난색"
변경 후: "신체 | ♪ f, allegro, legato | ▶ 와이드, 병렬 | ◎ 실시간"
         (조명·색채는 §P가 장면 단위로 결정)
```

→ DLC 레이어(une_facade.py) 수정이므로 Part 2 구현 시 같이 처리.
→ 기존 "난색" → §P의 `amber`+`indoor_lamp` 등으로 분해되어 더 구체적으로 표현됨.

### 데이터 흐름 (§P 결정 → 렌더링 → 피드백)

```
턴 N:
  Theoria → EnergyDirection + SceneType
  ↓
  waterfall_pipeline.py:91 → bus.dai["energy_direction"], bus.dai["scene_type"]
  ↓
  slot_manager._resolve_palette(scene_type, energy) → [§P indoor_lamp, amber]
  ↓ (Telescope prefill에 주입)
  모델 → §P에 따른 감각 어휘로 산문 렌더링
  ↓
  cognition.py:301-313 → render_fingerprint 추출:
    lighting: "부엌 간접등의 노란빛"      (실제 렌더링 결과, 한국어)
    palette: "꿀색, 나무결, 따뜻한 색감"  (실제 렌더링 결과, 한국어)
  ↓
  orchestration.py:238-241 → dai_snapshot 저장 (energy, scene_type 포함)
  domain_manager.update_scene_continuity() → 양쪽 저장

턴 N+1:
  theoria_analyzer.py:548-574 → ### 4d. SCENE CONTINUITY:
    CURRENT FRAME:
      - Lighting: 부엌 간접등의 노란빛
      - Palette: 꿀색, 나무결, 따뜻한 색감
  ↓
  cognition.py:315-335 → 배치 추출 컨텍스트:
    [RenderFP] Previous: Lighting=... | Palette=...
  ↓
  Theoria → 새 EnergyDirection + SceneType (이전 프레임 참고)
  ↓
  _resolve_palette() → 새 [§P ...] 계산
```

**기존 인프라 재활용**:
- `cognition.py:305-306` — lighting, palette 필드 이미 한국어 서술형 추출
- `theoria_analyzer.py:564-567` — CURRENT FRAME에 Lighting, Palette 표시
- `cognition.py:324-327` — 배치 추출 컨텍스트에 Previous Lighting/Palette 전달
- `slot_manager.py:791-793` — withholding_scheme도 같은 패턴으로 피드백 중

**§P = 지시(instruction), render_fingerprint = 관찰(observation)**.
추가 코드는 `_resolve_palette()` + Telescope prefill 삽입뿐. 피드백 루프는 기존 인프라가 처리.

### Part 2 토큰 영향

| 항목 | 토큰 | 위치 |
|---|---|---|
| §P 어휘 정의 (정적) | ~35 | text_resources.py |
| Telescope prefill `[§P X, Y]` | ~5 | slot_manager.py (동적) |
| 3축 테이블 색채/조명 제거 | -15~-20 | une_facade.py (DLC) |
| **순증** | **~20** | |

### Part 2 수정 파일

| 파일 | 변경 | 레이어 |
|---|---|---|
| `text_resources.py` | §P 어휘 정의 추가 (MIRROR_WORKSHOP 또는 PHYSICAL_RENDERING) | 기본 |
| `slot_manager.py` | `_resolve_palette()` + `_SCENE_PALETTE_DEFAULT` + `_ENERGY_PALETTE_MOD` 추가, `_build_telescope_prefill()`에서 호출 | 기본 |
| `une_facade.py` | 기존 notation 테이블에서 색채/조명 제거 (§P로 이관) | DLC |

---

## 전체 검증

### Part 1 검증
1. `py_compile` — 수정 파일 전체 문법 확인
2. 텍스트 검토: §E, §F 수정문이 §G의 earned resolution 문구와 일관되는지 확인
3. NPC 관계 초기화 테스트:
   - "소꿉친구" 프로필 등록 → `initial_depth=60` 추출 확인
   - 첫 턴 orchestration → `depth=60` 적용 확인
   - Theoria 피드백에 depth 힌트가 반영되는지 확인
4. 실사용 테스트:
   - 로어북에 "친한 사이"로 설정된 NPC와의 첫 세션이 자연스러운지
   - 관계가 10턴+ 쌓인 NPC와의 장면에서 따뜻한 순간이 자연스럽게 나오는지
   - NPC가 PC와 같은 감정을 가질 때, 자기 이유가 렌더링되는지 (echo vs alignment)
   - convergence_warning이 발동했을 때, 모델이 무조건 불편함 강제가 아니라 속도 점검을 하는지
   - 새로운 NPC/초기 관계에서는 여전히 거리감이 유지되는지 (기존 PULL 기능 보존)

### Part 2 검증
1. DLC OFF 상태에서:
   - intimate 장면 → `[§P indoor_lamp, amber]` prefill 확인
   - 산문에 따뜻한 감각 어휘(김, 나무, 피부, 호박색)가 나타나는지
   - combat 장면 → `[§P low_key, vivid]` → 강렬한 톤 유지
2. DLC ON 상태에서:
   - §P와 개체별 3축이 충돌 없이 공존하는지
   - 기존 notation에서 색채/조명 제거 후에도 개체별 연출이 정상인지
3. energy 보정:
   - social + rising → 기본 `high_key,natural` → color override `vivid` → `[§P high_key, vivid]`
   - intimate + aftershock → 기본 `indoor_lamp,amber` → override `diffused,washed` → `[§P diffused, washed]`
4. 프레임 연속성:
   - render_fingerprint에 lighting/palette가 정상 추출되는지
   - 다음 턴 Theoria의 ### 4d에 이전 프레임 정보 표시되는지
