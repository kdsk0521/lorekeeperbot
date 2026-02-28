# 서사 물성 강화 — 논리적 구현 순서

## Context

감정(마음) → 몸(행동) → 공간(분위기) → 다시 마음으로 돌아오는 순환 완성.
모두 **코드/분석 레이어**. API 콜 추가 0. DLC 4종(Storyteller/Doom/Vigor·Composure/Judgment)과 **양방향 격리** — 우리 코드는 `bus.*` DLC 상태를 읽지 않고, DLC도 우리 번역 텍스트를 읽지 않음. 모든 입력은 DAI(Theoria 출력) 전용.

---

## Phase A. 기반 인프라

### A1. `_get_soma_state()` 공통 헬퍼 — iceberg.py

**위치**: `_DISSOCIATION_HINTS` (line 191) 뒤, `translate_psyche_states()` 앞

Phase B/C 전체에서 반복되는 soma 추출을 1곳으로 통합. 5중 중첩 if 제거.

```python
def _get_soma_state(pdata) -> tuple:
    """NPC psyche_states entry → (polyvagal, dissociation). 기본값 ("ventral", "none")."""
    if not isinstance(pdata, dict):
        return ("ventral", "none")
    soma = pdata.get("soma")
    if not isinstance(soma, dict):
        return ("ventral", "none")
    return (soma.get("polyvagal", "ventral"), soma.get("dissociation", "none"))
```

**사용처**: Phase B1 (Speech Register), B2 (Emotion Intensity), B3 (Intimacy Window)

### A2. `_SPATIAL_KEYWORDS` / `_SPATIAL_HINTS` / `_DECAY_PROFILE` / `_resolve_spatial()` — slot_manager.py

**위치**: 기존 `_SCENE_PALETTE_DEFAULT` (line 101) 자리를 대체

Phase C에서 §S 태그와 §P 팔레트가 모두 이 테이블에 의존.

```python
_SPATIAL_KEYWORDS = {
    # 밀폐
    "방": "enclosed", "실내": "enclosed", "closet": "enclosed",
    "room": "enclosed", "bedroom": "enclosed", "bathroom": "enclosed",
    "부엌": "enclosed", "kitchen": "enclosed", "사무실": "enclosed", "office": "enclosed",
    "창고": "enclosed", "storage": "enclosed",
    # 반향
    "복도": "resonant", "홀": "resonant", "성당": "resonant", "지하": "resonant",
    "hallway": "resonant", "hall": "resonant", "cathedral": "resonant", "cave": "resonant",
    "계단": "resonant", "stairway": "resonant", "터널": "resonant", "tunnel": "resonant",
    # 개방
    "거리": "open", "공원": "open", "숲": "open", "들판": "open",
    "street": "open", "park": "open", "forest": "open", "field": "open",
    "바다": "open", "해변": "open", "beach": "open",
    # 고소
    "옥상": "elevated", "rooftop": "elevated", "절벽": "elevated", "cliff": "elevated",
    "탑": "elevated", "tower": "elevated", "발코니": "elevated", "balcony": "elevated",
    # 군중
    "시장": "crowded", "카페": "crowded", "바": "crowded", "클럽": "crowded",
    "market": "crowded", "cafe": "crowded", "bar": "crowded", "club": "crowded",
    "식당": "crowded", "restaurant": "crowded",
    # 이동
    "차": "moving", "버스": "moving", "기차": "moving", "지하철": "moving",
    "bus": "moving", "train": "moving", "subway": "moving", "car": "moving",
    "배": "moving", "ship": "moving", "boat": "moving",
}

_SPATIAL_HINTS = {
    "enclosed":  "[§S] 밀폐 — 냄새와 체온이 오래 남는다. 시선을 피하기 어렵고, 침묵이 무겁다",
    "resonant":  "[§S] 반향 — 발소리가 벽을 타고 돌아온다. 빈 공간이 존재감을 갖고, 속삭임도 멀리 간다",
    "open":      "[§S] 개방 — 바람이 흔적을 지운다. 발자국만 남고, 거리가 몸 사이를 벌린다",
    "elevated":  "[§S] 고소 — 바람이 체온을 앗아간다. 소리는 아래로 떨어지고, 몸이 노출된다",
    "crowded":   "[§S] 군중 — 개별 흔적이 소음에 묻힌다. 가까이 붙어야 하고, 사적 공간이 사라진다",
    "moving":    "[§S] 이동 — 흔적을 남길 수 없다. 진동이 몸에 전해지고, 공간 자체가 일시적이다",
}

# Architecture.decay_profile — 코드 보관, 후속 확장용 (현재 프롬프트 미사용)
_DECAY_PROFILE = {
    "enclosed":  {"scent": "high", "thermal": "high", "acoustic": "absorbed", "visual": "high"},
    "resonant":  {"scent": "low",  "thermal": "low",  "acoustic": "high",     "visual": "mid"},
    "open":      {"scent": "none", "thermal": "none", "acoustic": "none",     "visual": "low"},
    "elevated":  {"scent": "none", "thermal": "none", "acoustic": "none",     "visual": "mid"},
    "crowded":   {"scent": "noise","thermal": "noise","acoustic": "noise",    "visual": "noise"},
    "moving":    {"scent": "none", "thermal": "none", "acoustic": "none",     "visual": "none"},
}

def _resolve_spatial(location: str) -> str:
    """위치 문자열에서 공간 유형 추론 → §S 힌트. 매칭 없으면 빈 문자열."""
    if not location:
        return ""
    loc_lower = location.lower()
    for keyword, space_type in _SPATIAL_KEYWORDS.items():
        if keyword in loc_lower:
            return _SPATIAL_HINTS[space_type]
    return ""
```

---

## Phase B. 마음 → 몸 (polyvagal/soma 전파)

DAI의 `psyche_states[NPC].soma` → iceberg 번역에서 신체 상태 검증. DLC 무관 (DAI 직접 읽기).

### B1. Speech Register — compose_dialogue_directives()

**파일**: `iceberg.py` line 1087 (voice_quirks `말투: {vq}` 바로 뒤)

NPC별 자율신경 상태에 따라 대사의 어휘 밀도/문장 구조 변화.

```python
_REGISTER_HINTS = {
    "ventral": "",
    "sympathetic": "문장이 짧아진다. 빨라지고, 군더더기를 뺀다.",
    "dorsal": "말을 잃었다. 단음절, 긴 침묵, 숨만 쉰다.",
}
```

**삽입** (FULL directive 블록 내, `in_focus` 조건 하):
```python
        # Speech Register: polyvagal → 어휘 밀도 변화
        pvg, dis = _get_soma_state(state)
        if in_focus:
            if dis in ("moderate", "severe"):
                pvg = "dorsal"
            reg_hint = _REGISTER_HINTS.get(pvg, "")
            if reg_hint:
                directive_parts.append(reg_hint)
```

**조건**: gaze Full인 NPC만. ventral → +0토큰, 나머지 → +8~12.

### B2. Emotion Intensity — translate_emotion_intensity() soma 억제 + comedown

**파일**: `iceberg.py` line 647~655 (메인 루프)

**문제**: `psyche.value`만 보고 강도 힌트 생성. dorsal NPC가 "신체가 압도됨" 힌트를 받는 모순.

**수정**:
```python
for name, pdata in psyche_states.items():
    if not isinstance(pdata, dict):
        continue
    psyche = pdata.get("psyche", pdata.get("mental", {}))
    if not isinstance(psyche, dict):
        continue

    pvg, dis = _get_soma_state(pdata)
    # dorsal/dissociation → 감정이 표면에 도달하지 않음
    if pvg == "dorsal" or dis in ("moderate", "severe"):
        lines.append(f"  {name}: 몸이 닫혔다 — 감정이 표면에 도달하지 않는다")
        continue
    # Comedown: ventral + mild dissociation = 고강도에서 돌아오는 중
    if pvg == "ventral" and dis == "mild":
        lines.append(f"  {name}: 돌아오는 중 — 호흡 불안정, 감각 과민 또는 둔화")
        continue

    val = abs(psyche.get("value", 0))
    hint = _to_tier(val, _INTENSITY_HINTS)
    lines.append(f"  {name}: {hint}")
```

**토큰 변화**: ±0.

### B3. Intimacy Window — translate_intimacy() polyvagal 검증

**파일**: `iceberg.py` line 575 (시그니처) + line 583~587 (window_check 루프)

**문제**: dorsal NPC에게 "참여 가능" window가 전달되는 모순.

**시그니처 확장**:
```python
def translate_intimacy(intimacy_data: Optional[dict],
                       psyche_states: Optional[dict] = None) -> str:
```

**window_check 루프** (A1 헬퍼로 플랫하게):
```python
    if window and isinstance(window, dict):
        for char_name, state in window.items():
            ps = psyche_states.get(char_name, {}) if isinstance(psyche_states, dict) else {}
            pvg, dis = _get_soma_state(ps)
            if pvg == "dorsal" or dis in ("moderate", "severe"):
                lines.append(f"- {char_name}: 셧다운 — 열림 자체가 불가능한 상태")
                continue
            state_lower = str(state).lower().strip()
            hint = _WINDOW_HINTS.get(state_lower, state)
            lines.append(f"- {char_name}: {hint}")
```

**콜사이트** (`slot_manager.py` line 889):
```python
# 기존: intim_text = iceberg.translate_intimacy(intimacy)
intim_text = iceberg.translate_intimacy(intimacy, psyche_states=dai.get("psyche_states", {}))
```
주의: `psyche_data`는 line 924에서 정의 → line 889에서 `dai.get()` 직접 사용.

---

## Phase C. 몸 → 공간 (공간 각인 + 팔레트)

### C1. Theoria 스키마 — `spatial_read` 필드 추가

**파일**: `theoria_analyzer.py` — `## SCENE CONTINUITY` (line 349) 바로 위에 삽입

Flash에게 공간 읽기를 지시. `[§S]` 태그(코드 주입)를 decay physics 참조로 사용하라고 명시.

```
## SPATIAL PALETTE
Observe the physical space. base = what this scene's atmosphere looks and feels like. Lighting and color are mood, not clock.
mutation = did anyone/anything change the space? A=body/presence(involuntary), B=action(physical), C=perceptual(subjective POV lens).
A/B are Territory (objective). C is Lens (subjective) — MUST separate.
[§S] tag = architectural base (code-injected). Use as decay physics for traces. Do NOT re-analyze.
weight: skip = default (most turns). Output null when skip.

- "spatial_read": null OR {
    "active_traces": [{"type": "thermal/scent/acoustic/surface/object", "detail": "Korean 1 sentence"}] | null,
    "base": {
      "lighting": "natural/indoor_lamp/high_key/low_key/single_source/diffused/golden_hour/window_light/backlight/side_light",
      "color": "natural/amber/vivid/cool/washed/mono/sunset/sepia/pastel/complementary"
    },
    "mutation": null OR {
      "type": "A/B/C",
      "source": "Korean — 무엇이 변화를 일으켰는가",
      "lighting": "변이 결과 lighting",
      "color": "변이 결과 color"
    },
    "filter": "Korean or null — C-type만. POV 캐릭터의 지각 렌즈. A/B와 별도",
    "tension": "designed X <-> lived Y (Lefebvre)" | null,
    "shift": null | "gradual" | "sudden",
    "threshold": null | "mild" | "sharp",
    "weight": "skip/ambient/render"
  }
```

**토큰**: 스키마 ~100. 출력: skip → null(1), render → ~50. 평균 ~5/턴.

### C2. §P 팔레트 교체 — slot_manager.py

**삭제**:
- `_SCENE_PALETTE_DEFAULT` (line 101~109)
- `_ENERGY_PALETTE_MOD` (line 111~120)
- `_resolve_palette(scene_type, energy)` (line 123~129)

**교체** (`_resolve_spatial()` 바로 뒤):
```python
_VALID_LIGHTS = {
    "natural", "indoor_lamp", "high_key", "low_key", "single_source",
    "diffused", "golden_hour", "window_light", "backlight", "side_light",
}
_VALID_COLORS = {
    "natural", "amber", "vivid", "cool", "washed",
    "mono", "sunset", "sepia", "pastel", "complementary",
}

def _resolve_palette(dai: dict) -> str:
    """spatial_read → [§P light, color] 태그 + 선택적 filter 힌트.

    우선순위: flashback(코드 강제) > mutation(변이 결과) > base(장면 분위기) > natural(fallback)
    weight=skip 또는 null → natural/natural (§P 무강화)
    """
    light, color = "natural", "natural"
    filter_hint = ""

    spatial = dai.get("spatial_read")
    if spatial and isinstance(spatial, dict):
        weight = spatial.get("weight", "skip")
        if weight != "skip":
            base = spatial.get("base", {})
            if isinstance(base, dict):
                bl = base.get("lighting", "natural")
                bc = base.get("color", "natural")
                if bl in _VALID_LIGHTS:
                    light = bl
                if bc in _VALID_COLORS:
                    color = bc

            mut = spatial.get("mutation")
            if mut and isinstance(mut, dict):
                ml = mut.get("lighting")
                mc = mut.get("color")
                if ml and ml in _VALID_LIGHTS:
                    light = ml
                if mc and mc in _VALID_COLORS:
                    color = mc

            flt = spatial.get("filter")
            if flt and isinstance(flt, str):
                filter_hint = f" ({flt})"

    fb = dai.get("flashback_eval")
    if fb and isinstance(fb, dict) and fb.get("detected"):
        light, color = "diffused", "sepia"
        filter_hint = ""

    return f"[§P {light}, {color}]{filter_hint}"
```

### C3. Telescope prefill — §P + §S 주입

**파일**: `slot_manager.py` `_build_telescope_prefill()` (line 167~171)

**§P** (기존 3줄 교체):
```python
# 기존 삭제: scene_type = dai.get(...) / energy = dai.get(...) / palette_tag = _resolve_palette(scene_type, energy)
palette_tag = _resolve_palette(dai)
scene_lines.append(f"  ├ {palette_tag}")
```

**§S** (§P 바로 뒤 추가):
```python
    # [§S] Spatial Sense
    location = ""
    obs = dai.get("observation", {})
    if isinstance(obs, dict):
        location = obs.get("location", "")
    elif isinstance(obs, str):
        location = obs
    spatial_hint = _resolve_spatial(location)
    if spatial_hint:
        scene_lines.append(f"  ├ {spatial_hint}")
```

`scene_type`/`energy`는 §P에서만 제거. 나머지(storyteller, notation, iceberg depth 등 16곳+)에서 계속 사용 → DAI 필드 자체 유지.

### C4. 공간 각인 번역 — iceberg.py 새 함수

**위치**: `translate_continuity_check()` 근처 (공간 관련 번역 함수들 모음)

```python
_SHIFT_HINTS = {
    "gradual": "빛/색이 서서히 바뀌고 있다",
    "sudden": "빛/색이 급변했다 — 신체 충격 수반",
}
_THRESHOLD_HINTS = {
    "mild": "공간이 바뀌었다 — 감각 한 문장 전환",
    "sharp": "감각 낙차가 크다 — 눈부심/한기/바람 등 신체 반응",
}

def translate_spatial_inscription(spatial_read: Optional[dict]) -> str:
    """spatial_read → 공간 각인/전환 렌더링 힌트.
    weight=skip이면 빈 문자열. ambient이면 traces만. render이면 전부."""
    if not spatial_read or not isinstance(spatial_read, dict):
        return ""
    weight = spatial_read.get("weight", "skip")
    if weight == "skip":
        return ""

    lines = []

    traces = spatial_read.get("active_traces")
    if traces and isinstance(traces, list):
        for t in traces[:4]:
            if isinstance(t, dict) and t.get("detail"):
                lines.append(f"  {t['detail']}")

    flt = spatial_read.get("filter")
    if flt and isinstance(flt, str):
        lines.append(f"  [지각 편향] {flt} — 물리적 변화 아님")

    tension = spatial_read.get("tension")
    if tension and isinstance(tension, str) and tension != "null":
        lines.append(f"  [공간 간극] {tension}")

    shift = spatial_read.get("shift")
    if shift and shift != "null":
        hint = _SHIFT_HINTS.get(shift, "")
        if hint:
            lines.append(f"  {hint}")
    threshold = spatial_read.get("threshold")
    if threshold and threshold != "null":
        hint = _THRESHOLD_HINTS.get(threshold, "")
        if hint:
            lines.append(f"  {hint}")

    if not lines:
        return ""
    header = ("### 공간 각인\n(배경 질감. 전개하지 마.)\n"
              if weight == "ambient" else
              "### 공간 각인\n(공간이 겪은 것을 감각으로 렌더링하라. 분석 용어 금지.)\n")
    return header + "\n".join(lines)
```

### C5. 공간 각인 → extended_intelligence 주입 — slot_manager.py

`build_34_step_prompt()` 내 IntimacyAnalysis (line 895) 뒤:
```python
    # Spatial Inscription: 공간 각인 렌더링 힌트
    spatial_read = dai.get("spatial_read", {})
    spatial_text = iceberg.translate_spatial_inscription(spatial_read)
    if spatial_text:
        extended_intel_parts.append(spatial_text)
```

---

## Phase D. 정리

### D1. cultural_context 제거 — npc_manager.py

범용성 확보. 이름 기반 문화권 자동 분류 삭제.

**삭제 1** — `_extract_structured_fields()` (line 171~179):
```python
# 삭제:
#     if any(w in desc_lower for w in ("한국", "korea", "서울", ...)):
#         fields["cultural_context"] = "korean"
#     elif ... (japanese/chinese/western)
```

**삭제 2** — `get_npc_roster()` (line 650~653):
```python
# 삭제:
#     cultural = data.get("cultural_context", "")
#     tag += f" ({cultural})" if cultural else ""
```

---

## DLC 격리 확인

| | Storyteller | Doom | Vigor/Composure | Judgment |
|---|---|---|---|---|
| **우리 코드가 읽는가** | NO | NO | NO | NO |
| **DLC가 우리 텍스트를 읽는가** | NO | NO | NO | NO |
| **공유 DAI 필드** | scene_type, energy | position | rest_eval | psyche_states.soma |

- `psyche_states.soma`: Judgment가 `polyvagal`을 theory_mod로 읽고, 우리도 읽음. **같은 소스(DAI) 독립 소비** — 충돌 없음.
- `scene_type`/`energy_direction`: §P에서만 제거. Storyteller 타이밍, iceberg depth, notation 등 **16곳+에서 계속 사용**.
- `spatial_read`: 완전 새 필드. **DLC 어디에서도 참조 없음**.

---

## 수정 파일 요약

| 파일 | Phase | 변경 | 토큰 |
|---|---|---|---|
| `iceberg.py` | A1 | `_get_soma_state()` 공통 헬퍼 | 0 |
| `iceberg.py` | B1 | `_REGISTER_HINTS` + speech register in compose_dialogue_directives() | +0~12 |
| `iceberg.py` | B2 | translate_emotion_intensity() soma 억제 + comedown | ±0 |
| `iceberg.py` | B3 | translate_intimacy() 시그니처 확장 + polyvagal 검증 | ±0 |
| `iceberg.py` | C4 | `translate_spatial_inscription()` 새 함수 | +0~40 |
| `slot_manager.py` | A2 | `_SPATIAL_KEYWORDS/HINTS` + `_DECAY_PROFILE` + `_resolve_spatial()` | 0 (코드만) |
| `slot_manager.py` | B3 | translate_intimacy 콜사이트 psyche_states 전달 | 0 |
| `slot_manager.py` | C2 | 팔레트 삭제 + `_resolve_palette(dai)` 교체 | ±0 |
| `slot_manager.py` | C3 | Telescope prefill §P 교체 + §S 주입 | +0~18 |
| `slot_manager.py` | C5 | spatial inscription → extended_intelligence | 0 |
| `theoria_analyzer.py` | C1 | `spatial_read` 스키마 | +~105/턴 |
| `npc_manager.py` | D1 | `cultural_context` 탐지 + 소비 코드 삭제 | 0 |

**순증**: Theoria +~105/턴 (스키마+평균출력). Telescope +0~18. 렌더러 +0~52. API 콜 추가 0.

---

## 구현 순서 (의존성 기반)

```
A1 (_get_soma_state)  ─┬→ B1 (Speech Register)
                       ├→ B2 (Emotion Intensity)
                       └→ B3 (Intimacy Window)

A2 (_SPATIAL 테이블)  ─→ C3 (§S prefill 주입)

C1 (Theoria 스키마)   ─┬→ C2 (§P 팔레트 교체)
                       ├→ C3 (§P+§S prefill)
                       ├→ C4 (translate_spatial_inscription)
                       └→ C5 (extended_intelligence 주입)

D1 (cultural_context)  ─→ 독립
```

**실제 작업 순서**: A1 → A2 → B1 → B2 → B3 → C1 → C2 → C3 → C4 → C5 → D1

---

## 검증

1. `py_compile` — iceberg.py, slot_manager.py, theoria_analyzer.py, npc_manager.py
2. **_get_soma_state**:
   - `pdata=None` → ("ventral", "none")
   - `pdata={"soma": {"polyvagal": "dorsal"}}` → ("dorsal", "none")
   - `pdata={"soma": {"dissociation": "severe"}}` → ("ventral", "severe")
3. **Speech Register (B1)**:
   - ventral → hint 없음
   - sympathetic → "문장이 짧아진다"
   - dorsal → "말을 잃었다"
   - dissociation=moderate → dorsal 강제
   - 배경 NPC (not in_focus) → 미적용
4. **Emotion Intensity (B2)**:
   - dorsal + high value → "몸이 닫혔다"
   - dissociation=severe → "몸이 닫혔다"
   - ventral + mild dissociation → "돌아오는 중"
   - ventral + none + high value → 기존 _INTENSITY_HINTS
5. **Intimacy Window (B3)**:
   - dorsal + window=within → "셧다운 — 열림 불가능"
   - ventral + window=within → 기존 "안정 범위"
   - psyche_states=None → 기존 동작 (backward compat)
6. **§S Spatial Sense (C3)**:
   - "학교 복도" → `[§S] 반향`
   - "옥상" → `[§S] 고소`
   - "기차" → `[§S] 이동`
   - "" → hint 없음
7. **§P Spatial Palette (C2/C3)**:
   - spatial_read=null → `[§P natural, natural]`
   - weight=ambient → §P 정상
   - weight=render + mutation → base 덮어쓰기
   - filter → §P 인라인 힌트
   - flashback → `[§P diffused, sepia]` 강제
   - 잘못된 값 → "natural" fallback
   - 기존 `_SCENE_PALETTE_DEFAULT`/`_ENERGY_PALETTE_MOD` 삭제 확인
8. **Spatial Inscription (C4/C5)**:
   - weight=skip → 빈 문자열
   - weight=ambient → traces만 (배경 질감, 전개 금지)
   - weight=render → 전체 (traces/filter/tension/shift/threshold)
   - shift=sudden → "신체 충격 수반"
   - threshold=sharp → "감각 낙차"
9. **cultural_context (D1)**:
   - 이름 기반 분류 코드 삭제 확인
   - roster 태그 cultural 표시 제거 확인
10. **DLC 격리**:
    - 새 코드에 `bus.anomaly/doom/vigor/composure/judgment` 참조 없음 확인
