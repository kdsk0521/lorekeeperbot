# Iceberg 축 전환 — NPC ♪▶◎ + 장면 [lighting, hue, saturation]

## Context

iceberg.py의 NPC 심리 힌트가 임상/심리학 용어(과각성, 저각성, 해리, 내면화 등)로 되어 있어 모델이 산문에 투과시킬 위험.
UNE가 이미 PC 상태/장면에 ♪▶◎ 연출 표기를 쓰고 있으므로, NPC 심리도 같은 체계로 통일.
장면 분위기는 별도 시각 축([lighting, hue, saturation])으로 보충.

**수정 파일**: `iceberg.py` 1개
**어휘 원칙**: **전부 영어** — MIRROR_WORKSHOP §P + theoria spatial_read.base 어휘 통일
- ♪ Music: pp~ff, largo~presto, legato/staccato/marcato/sforzando, crescendo/diminuendo
- ▶ Camera: close-up/wide/two-shot/over-the-shoulder/high-angle/eye-level + facing/parallel/back-to-back/height-gap/pillow + match-cut/cut/fade + pan/cut/long-take + [lighting]
- ◎ Photo: bulb/long-exposure/interval/real-time/slow-motion/freeze
- [bracket]: theoria vocabulary — lighting(9) + hue(7) + saturation(4)
- Subject labels: soma/affect/consciousness/attitude/distance

**변환 원칙**:
- NPC 개인 상태(어떻게 보이는가) → ♪▶◎ + ▶ bracket에 [lighting, hue, saturation]
- 장면 분위기(어떤 빛인가) → [lighting, hue, saturation]
- NPC 관계/대화(무엇을 원하는가) → 한국어 행동 문장 (정비만)
- 품질 플래그(메타 지시) → 한국어 행동 문장 (정비만)

---

## Phase 1: NPC 개인 상태 → ♪▶◎ 연출 표기

### 1-1. _POLYVAGAL_HINTS → _POLYVAGAL_NOTATION (line 169)
```python
_POLYVAGAL_NOTATION = {
    "ventral":     "soma | ♪ mp, andante, legato | ▶ two-shot, parallel [diffused, warm, solid] | ◎ real-time",
    "sympathetic": "soma | ♪ f, allegro, staccato | ▶ close-up, back-to-back, cut [side_light, cool, vivid] | ◎ slow-motion",
    "dorsal":      "soma | ♪ pp, largo, legato | ▶ long-take, pillow [single_source, grey, washed] | ◎ freeze",
}
```
> [bracket]은 theoria spatial_read.base 스키마 어휘 그대로: lighting(9종) + hue(7종) + saturation(4종).

### 1-2. _CULTURAL_AFFECT_HINTS → _CULTURAL_AFFECT_NOTATION (line 175)
```python
_CULTURAL_AFFECT_NOTATION = {
    "han":       "affect | ♪ p, adagio, legato, diminuendo | ▶ long-take, back-to-back [backlight, grey, washed] | ◎ long-exposure",
    "jeong":     "affect | ♪ mp, andante, legato | ▶ two-shot, pillow [diffused, amber, solid] | ◎ real-time",
    "hwabyung":  "affect | ♪ ff, presto, sforzando | ▶ close-up, facing [side_light, crimson, vivid] | ◎ slow-motion",
    "nunchi":    "affect | ♪ p, andante, staccato | ▶ over-the-shoulder, height-gap [side_light, grey] | ◎ slow-motion",
    "chaemyeon": "affect | ♪ mf, andante, legato | ▶ two-shot, facing [high_key, solid] | ◎ real-time",
    "simma":     "affect | ♪ mf, allegro, staccato, crescendo | ▶ close-up [side_light, amber, vivid] | ◎ slow-motion",
    "gi":        "affect | ♪ f, allegro, marcato | ▶ wide, height-gap [single_source, vivid] | ◎ real-time",
}
```

### 1-3. _DISSOCIATION_HINTS → _DISSOCIATION_NOTATION (line 185)
```python
_DISSOCIATION_NOTATION = {
    "mild":     "consciousness | ♪ pp, adagio, legato | ▶ eye-level, pillow [diffused, grey, pastel] | ◎ long-exposure",
    "moderate": "consciousness | ♪ pp, largo, staccato | ▶ high-angle, height-gap [single_source, grey, washed] | ◎ interval",
    "severe":   "consciousness | ♪ pp, largo, legato | ▶ long-take, back-to-back [low_key, grey, washed] | ◎ freeze",
}
```
> 해리 깊어질수록 빛 빠짐 — mild(diffused+pastel), moderate(single_source+washed), severe(low_key+washed).

### 1-4. _WINDOW_HINTS → _WINDOW_NOTATION (line 611)
```python
_WINDOW_NOTATION = {
    "within": "♪ mf, andante, legato | ◎ real-time",
    "above":  "♪ ff, presto, staccato | ◎ slow-motion",
    "below":  "♪ pp, largo, legato | ◎ freeze",
}
```
> ▶ 카메라 생략 — 친밀 맥락에서는 프레이밍이 별도 결정됨.

### 1-5. _INTENSITY_HINTS → _INTENSITY_NOTATION (line 686)
```python
_INTENSITY_NOTATION = [
    (30,  "♪ pp"),
    (60,  "♪ mf"),
    (80,  "♪ f"),
    (100, "♪ ff"),
]
```
> ♪ dynamics만. 강도는 음량이 가장 직관적.

### 1-6. translate_psyche_states() 출력 형식 변경 (line 241)
현재: 모든 힌트를 `. `로 join → 한 줄
변경: ♪▶◎ notation은 별도 들여쓰기 줄, prose는 기존대로

```
# Before:
- 김소연: 안절부절, 시선 분산, 움직임 증가, 목소리 빨라짐. 삼킨 감정이 축적 — 한숨, 먼 시선, 과묵

# After:
- 김소연:
  soma | ♪ f, allegro, staccato | ▶ close-up, back-to-back, cut [side_light, cool, vivid] | ◎ slow-motion
  affect | ♪ p, adagio, legato, diminuendo | ▶ long-take, back-to-back [backlight, grey, washed] | ◎ long-exposure
  경계를 풀기 시작하는 기미
```
> soma descriptor, env_influence, psyche descriptor, relation descriptor는 한국어 prose 유지. ♪▶◎ notation과 분리.
> polyvagal/cultural_affect/dissociation → notation 줄로 출력. 나머지 descriptor는 prose join.

### 1-7. translate_intimacy() 내 window 변환 (line 627)
_WINDOW_HINTS → _WINDOW_NOTATION 사용.

### 1-8. translate_emotion_intensity() 변환 (line 694)
_INTENSITY_HINTS → _INTENSITY_NOTATION 사용.
헤더 변경:
```
기존: "[감정 강도]\n감정을 신체 증거로 렌더링하라. 감정명, 강도 라벨, 수치를 산문에 쓰지 마.\n"
수정: "[감정 강도]\n감정은 몸으로 보여줘라. 감정명, 강도 라벨, 수치를 산문에 쓰지 마.\n"
```

### 1-9. _ATTITUDE_BASELINE → _ATTITUDE_NOTATION (line 544)
```python
_ATTITUDE_NOTATION = {
    "hostile":    "attitude | ♪ f, allegro, staccato | ▶ facing, height-gap [side_light, cool, vivid] | ◎ slow-motion",
    "unfriendly": "attitude | ♪ mf, andante, staccato | ▶ back-to-back [side_light, cool] | ◎ real-time",
    "neutral":    "",
    "friendly":   "attitude | ♪ mp, andante, legato | ▶ two-shot, parallel [diffused, amber, solid] | ◎ real-time",
    "devoted":    "attitude | ♪ mp, adagio, legato | ▶ close-up, pillow [golden_hour, amber, solid] | ◎ real-time",
}
```

### 1-10. _TRAJECTORY_HINTS → _TRAJECTORY_NOTATION (line 534)
```python
_TRAJECTORY_NOTATION = {
    "warming":   "crescendo",
    "cooling":   "diminuendo",
    "stable":    "",
    "volatile":  "sforzando",
    "declining": "diminuendo",
    "improving": "crescendo",
}
```
> trajectory는 attitude ♪의 방향 수식어. 합성 예: `attitude | ♪ f, allegro, staccato, diminuendo | ▶ ...`

### 1-11. _STAGE_HINTS → _STAGE_NOTATION (line 581)
```python
_STAGE_NOTATION = {
    "Initial":     "distance | ♪ p, andante, staccato | ▶ wide, height-gap | ◎ real-time",
    "Warming":     "distance | ♪ mp, andante, legato, crescendo | ▶ two-shot | ◎ real-time",
    "Established": "distance | ♪ mf, andante, legato | ▶ two-shot, match-cut | ◎ real-time",
    "Intimate":    "distance | ♪ mp, adagio, legato | ▶ close-up, pillow | ◎ real-time",
    "Ruptured":    "distance | ♪ f, allegro, staccato | ▶ back-to-back, cut | ◎ freeze",
}
```

### 1-12. _SILENCE_HINTS → _SILENCE_NOTATION (line 777)
```python
_SILENCE_NOTATION = {
    "reflective": "♪ pp, adagio, legato | ◎ long-exposure",
    "hesitant":   "♪ p, andante, staccato | ◎ slow-motion",
    "heavy":      "♪ pp, largo, legato | ◎ freeze",
    "tense":      "♪ p, allegro, staccato | ◎ slow-motion",
}
```
> ▶ 카메라 생략 — 침묵은 청각/시간 차원. 장면 카메라는 UNE가 담당.

### 1-13. translate_npc_attitudes() 재구성 (line 553)
- attitude → _ATTITUDE_NOTATION 조회
- trajectory → _TRAJECTORY_NOTATION 조회 → attitude의 ♪에 방향 수식어로 합성
- reason → 한국어 prose 별도 줄
```
# Before:
- 김소연: 적의를 품고 있다 — PC가 동생을 배신했기 때문 — 거리를 두기 시작하는 기미

# After:
- 김소연:
  attitude | ♪ f, allegro, staccato, diminuendo | ▶ facing, height-gap [side_light, cool, vivid] | ◎ slow-motion
  PC가 동생을 배신했기 때문
```

### 1-14. translate_connection_depth() 재구성 (line 590)
- stage → _STAGE_NOTATION 조회
- tension > 50 → ♪에 marcato 추가
```
# Before:
- 김소연: 관계 파열 — 신뢰가 깨졌거나 위기 상태. 긴장감이 매우 높다.

# After:
- 김소연:
  distance | ♪ f, allegro, staccato, marcato | ▶ back-to-back, cut | ◎ freeze
```

### 1-15. translate_narrative_chain() 내 silence 변환 (line 785)
silence_type → _SILENCE_NOTATION. prose 부분과 분리하여 별도 줄.

---

## Phase 2: 장면 에너지 → [lighting, hue, saturation]

### 2-1. _ENERGY_HINTS → _ENERGY_VISUAL (line 367)
```python
_ENERGY_VISUAL = {
    "idle":       "[diffused, amber, pastel]",
    "rising":     "[side_light, cool, solid]",
    "stagnant":   "[single_source, grey, washed]",
    "detonation": "[single_source, crimson, vivid]",
    "aftershock": "[backlight, sepia, washed]",
}
```
> theoria spatial_read.base 어휘 그대로. UNE가 ♪▶◎로 장면 리듬 커버, iceberg는 시각(빛/색/채도) 보충.

### 2-2. translate_energy_direction() 헤더 변경 (line 376)
```
기존: "### 장면 호흡: {hint}"
수정: "### 장면 빛: {hint}"
```

---

## Phase 3: 한국어 행동 문장 정비

### 대상: 관계/대화 힌트 + 품질 플래그 + 헤더

이 항목들은 ♪▶◎로 변환하지 않는다 — NPC가 "무엇을 원하는지/어떻게 말하는지"는 행동 문장이 적합.

### 3-1. _DESIRE_HINTS 심리 용어 제거 (line 617)
```python
"power":      "주도권을 쥐고 싶다 — 상황을 쥐려 한다",
"escape":     "여기서 벗어나고 싶다 — 지금 이 자리에서 빠지려 한다",
"validation": "인정받고 싶다 — 나를 봐달라는 것",
"sensation":  "느끼고 싶다 — 감각 그 자체를 원한다",
```
> attachment, connection은 이미 행동 문장이라 그대로.

### 3-2. _FLAG_DIRECTIVES 정비 (line 391-399)
```python
"convergence_warning":  "관계 변화가 빠르다. 이 속도에 맞는 근거가 있는지 확인하라.",
"echo_warning":         "NPC가 PC 감정을 따라가고 있다. NPC 자신의 이유가 있는 반응인지 확인하라.",
"mse_deviation":        "NPC 행동이 급변했다. 이전과 일관되는지 확인하고, 변화에 근거를 부여하라.",
"dissonance_flag":      "NPC의 말과 행동이 어긋나고 있다. 바로 해소하지 마 — 불편함을 몸으로 보여줘라.",
"redemption_warning":   "NPC가 근거 없이 누그러지고 있다. 이전 패턴을 유지하라.",
"shallow_read":         "분석이 표면에 머물렀다. 드러난 행동 아래를 더 보라 — 입 밖에 안 낸 것, 공간이 주는 압박, 갚지 못한 빚.",
"label_internalization": "NPC가 자기에게 붙은 라벨을 믿기 시작했다. 라벨을 입으로 말하지 마 — 습관, 자세, 반응으로 보여줘라.",
```

### 3-3. _SYMPTOM_TEMPLATE (line 402)
```python
기존: "NPC가 {cluster} 증상군을 보이고 있다. 증상을 일관된 세트로 유지하라. 체리피킹 금지."
수정: "NPC가 {cluster} 증상을 보이고 있다. 한 세트로 일관되게 유지하라."
```

### 3-4. _LAYER_RENAMES["Lack"] (line 195)
```python
기존: "▸절대 직접 말하지 마"
수정: "▸직접 드러내지 마"
```

### 3-5. 헤더 톤 완화
- spatial_inscription (line 481): "렌더링하라. 분석 용어 금지." → "보여줘라. 분석 용어 쓰지 마."
- emotion_intensity (line 713): "신체 증거로 렌더링하라" → "몸으로 보여줘라"

---

## 변환하지 않는 것 — 재검토 완료

| 테이블 | 변환? | 이유 |
|--------|:---:|------|
| _NEEDS_HINTS | ❌ | **동기**(왜 말하는가) — ♪▶◎=질감, 조명색채도=시각. 차원이 다름 |
| _STRATEGY_HINTS | ❌ | **담화 전략**(어떻게 말하는가) — 부사/접근법이지 감각이 아님 |
| _PHASE_HINTS | ❌ | **대화 전술** — _STAGE_NOTATION이 관계 거리, phase는 그 안의 대화 접근법 |
| _CHAIN_STATUS_HINTS | ❌ | **구조 메타** — "대화가 열려있다"는 감각도 시각도 아닌 상태 |
| _PROXIMITY_HINTS | ❌ | **서사 페이싱** — NPC가 아닌 이야기 자체의 리듬 |
| _POS_FRICTION | ❌ | **세계 규칙** — "장벽은 실제로 존재한다"는 렌더링 아닌 규칙 지시 |
| _SCHEME_KR | ❌ | **코드 분류 라벨** — 프롬프트에 직접 안 나감 |
| _SHIFT_HINTS | ❌ | 이미 시각 전환 서술이라 깔끔. 공간 각인 맥락에서 행동 문장이 더 직관적 |
| _THRESHOLD_HINTS | ❌ | 감각 낙차 서술. 행동 문장이 적합 |

---

## 검증
- `py_compile iceberg.py` — 문법 오류 확인
- 어휘 일관성: 모든 ♪▶◎ 용어 = MIRROR_WORKSHOP §P 영어 어휘
- [bracket] 어휘: theoria spatial_read.base lighting/hue/saturation 범위 내
- Subject labels: soma/affect/consciousness/attitude/distance — theoria 필드명과 대응
