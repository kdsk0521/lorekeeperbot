# 분석 엔진 스키마-리소스 동기화

## Context

스키마(`theoria_analyzer.py _get_output_schema`)가 요구하는 필드에 대해 `analysis_resources.py`의 이론 정의와 `STATE_TRACKING_V2`의 필드 목록이 누락되어 있음.
- **정의 0** (3건): HabitusAnalysis, trait_connections, Momentum — 이론 정의 자체가 없음
- **Launeicha 미착지** (2건): apprehension_gap, resurfacing — 스키마 inline만, STATE_TRACKING에 없음
- **조건부↔무조건 불일치** (6건): 이론이 장르 조건부 모듈에만 있는데 스키마 필드는 항상 출력 요구

**수정 파일**: `analysis_resources.py`, `theoria_analyzer.py` (2개)

---

## Phase 1: 이론 정의 추가 (analysis_resources.py)

### 1-1. Habitus (Bourdieu) → ANALYTICAL_LENSES_ESTABLISHED에 추가

위치: Prospect Theory 뒤, DSM-5 앞 (### Behavioral Persistence & Change 앞)

```
### Social Position Analysis
- Habitus (Bourdieu): Three capitals that produce observable class signals → HabitusAnalysis
  Economic: material resources, consumption patterns, visible wealth/scarcity.
  Cultural: vocabulary range, taste markers, education signals, comfort with formality.
  Social: who they know, whose call they take, who defers to whom.
  Habitus is EMBODIED — accent, posture, table manners, reaction to authority.
  Not what they own but how they CARRY themselves. Mismatch between capitals = friction.
```

> 이미 GENRE_THEORY_WEIGHTS에서 5개 장르(cyberpunk, space_opera, modern, steampunk, high_fantasy)가 Habitus를 emphasize/reframe하는데 정의가 없었음. 이제 모델이 참조 가능.

### 1-2. Trait Deflection → ANALYTICAL_LENSES_CUSTOM에 추가

위치: Four-Layer Architecture 뒤

```
### Trait Deflection [CUSTOM] → .trait_connections
When two NPC profile traits activate in the same scene:
1. primary_link: the OBVIOUS connection — the first, most cliché interpretation
2. deflection: the RICHER alternative — complicate, invert, or compound the primary
Primary link is diagnostic ("cold + intelligent = calculating"). Deflection is fiction ("cold + intelligent = terrified of being wrong").
Deflection methods: inversion (trait A suppresses B), compounding (A amplifies B in unexpected axis), friction (A and B contradict, producing visible tension).
OUTPUT FORMAT: trait_pair="trait_A × trait_B", primary_link=Korean obvious, deflection=Korean richer, render_hint=Korean 1-sentence scene direction.
null when no NPC traits are being actively expressed this turn.
```

### 1-3. Momentum → OBSERVATION_INTENT에 추가

위치: EnergyDirection 설명 뒤

```
### Momentum (InputAnalysis)
- Open: active tension, unanswered question, or unresolved force in play. The scene is PULLING.
- Closed: current thread settled, breath taken, natural pause. The scene is RESTING.
Momentum is NOT EnergyDirection. Energy=scene intensity. Momentum=narrative pull.
idle+Open = quiet but something unspoken hangs. detonation+Closed = explosion just resolved.
```

---

## Phase 2: STATE_TRACKING_V2 동기화 (analysis_resources.py)

### 2-1. psyche 섹션에 apprehension_gap 추가

위치: `- coping:` 줄 뒤

```
- apprehension_gap: "Absence/Approximation/Distortion" or null (Schema Refraction: what THIS character failed to perceive, roughly approximated, or distorted through their own schema/defense. null = accurate apprehension)
```

### 2-2. soma 섹션에 dissociation 추가

위치: `- env_influence:` 줄 뒤

```
- dissociation: none / mild / moderate / severe / null (Dissociation Spectrum: dorsal→entry point. mild=flat affect,delayed response. moderate=third-person self-reference,time gaps. severe=autopilot,recognition failure. Track across turns. null = no trigger)
```

### 2-3. relation 섹션에 group_dynamic, negotiation_stance 추가

위치: `- stage:` 줄 뒤

```
- group_dynamic: conformity / obedience / groupthink / diffusion / null (Group Dynamics: active in 3+ character scenes. null = no group pressure)
- negotiation_stance: cooperative / competitive / exploitative / null (BATNA: stance reflects Position value. null = no negotiation active)
```

### 2-4. psyche_states 최상위에 resurfacing 추가

위치: `deep_read` 설명 뒤

```
resurfacing (Resurgence)
- str or null. Past trauma, contradictory desire, or 'resolved' emotion re-emerging through current interaction. What resurfaces and what triggered it. null = no resurgence.
```

---

## Phase 3: 조건부 null 가이드 코드 (theoria_analyzer.py)

### 3-1. _build_system_instruction()에 null 가이드 삽입

위치: line 193, return 문 직전

```python
from theory_emphasis_engine import get_active_modules

active_mods = set(get_active_modules(active_genres))
null_hints = []
if 'COSMIC_HORROR_MODULE' not in active_mods:
    null_hints.append("- soma.dissociation: null unless extreme trauma/shutdown observed")
    null_hints.append("- anomaly_profile.perception_type: null unless supernatural elements confirmed in setting")
if 'GROUP_DYNAMICS_MODULE' not in active_mods:
    null_hints.append("- relation.group_dynamic: null unless 3+ characters actively pressuring each other")
if 'NEGOTIATION_MODULE' not in active_mods:
    null_hints.append("- relation.negotiation_stance: null unless active bargaining/trade in scene")
if 'FORENSIC_MODULE' not in active_mods:
    null_hints.append("- NPCKnowledge.deception_cues: null unless strong behavioral deception signals")
    null_hints.append("- QualityFlags.label_internalization: false unless labeling pattern clearly evident")

null_guide = ""
if null_hints:
    null_guide = "\n\n<module_absent_guidance>\nThese fields' full theory modules are not loaded for current genre. Default to null/false unless clear evidence:\n" + "\n".join(null_hints) + "\n</module_absent_guidance>"
```

return 문 수정:
```python
return directive + "\n\n" + spotlight + "\n\n" + self._get_output_schema() + null_guide + "\n</THEORIA>"
```

> 모듈 미로딩 시에만 ~50 tokens 추가. 로딩 시 0. 모든 소비자가 null-safe 확인 완료.

---

## Phase 4: Spatial Palette 정리 — 이론 이동 (analysis_resources.py + theoria_analyzer.py)

스키마 인라인에 섞여있는 spatial_read 프레임워크를 OBSERVATION_INTENT로 이동. 스키마에는 JSON만 남김.

### 4-1. OBSERVATION_INTENT에 Spatial Palette 프레임워크 추가

위치: EnergyDirection 뒤 (Momentum 추가 뒤, UNFAMILIAR DISCOVERY 앞)

```
### Spatial Palette → spatial_read
Observe the physical space. Lighting and color are mood, not clock.
- base: ambient atmosphere. What the space looks and feels like before anyone acts.
- mutation: space changed by presence or action.
  A=body/presence(involuntary territory), B=action(physical territory), C=perceptual(subjective lens, POV only).
  A/B are Territory (objective). C is Lens (subjective) — MUST separate.
- tension (Lefebvre Production of Space): "designed X <-> lived Y" — mismatch between the space's intended purpose and how characters actually inhabit it. null when no mismatch.
- spatial_type: enclosed(traces linger), resonant(echoes, emptiness), open(wind erases), elevated(exposed), crowded(traces drown), moving(transient).
- weight: ambient=default(base palette only). render=mutation occurred.
```

### 4-2. theoria_analyzer.py 스키마에서 인라인 설명 제거

**제거** (lines 349-354):
```
## SPATIAL PALETTE
Observe the physical space. base = what this scene's atmosphere looks and feels like. Lighting and color are mood, not clock.
mutation = did anyone/anything change the space? A=body/presence(involuntary), B=action(physical), C=perceptual(subjective POV lens).
A/B are Territory (objective). C is Lens (subjective) — MUST separate.
spatial_type = classify from context. enclosed(scent/heat linger, silence heavy), resonant(echoes, emptiness has presence), open(wind erases, distance separates), elevated(wind steals heat, exposed), crowded(traces drown in noise, no privacy), moving(no lasting trace, vibration, transient).
weight: ambient = default (base palette always). render = when mutation occurs.
```

**교체** (헤더만):
```
## SPATIAL PALETTE
```

> JSON 스키마 자체(lines 356-376)는 그대로 유지. 인라인 설명만 제거.
> 토큰 변화: ~0 (OBSERVATION_INTENT +7줄, 스키마 -5줄)

---

## 토큰 영향

| Phase | 추가 | 비고 |
|-------|------|------|
| 1 (이론 3건) | ~180 tokens | system_instruction에 항상 로딩 |
| 2 (STATE_TRACKING 5필드) | ~80 tokens | system_instruction에 항상 로딩 |
| 3 (null 가이드) | ~50 tokens | 모듈 미로딩 시에만 |
| 4 (Spatial 이동) | ~0 tokens | 순수 이동 (OBSERVATION_INTENT +7줄, 스키마 -5줄) |
| **합계** | **~260 (최대 310)** | |

---

## 구현 순서

```
1. analysis_resources.py: Habitus → ANALYTICAL_LENSES_ESTABLISHED
2. analysis_resources.py: Trait Deflection → ANALYTICAL_LENSES_CUSTOM
3. analysis_resources.py: Momentum → OBSERVATION_INTENT
4. analysis_resources.py: Spatial Palette → OBSERVATION_INTENT (Momentum 뒤)
5. analysis_resources.py: STATE_TRACKING_V2에 5 필드 동기화
6. theoria_analyzer.py: 스키마 인라인 설명 제거 (## SPATIAL PALETTE 헤더만 남김)
7. theoria_analyzer.py: _build_system_instruction()에 null 가이드 코드 추가
→ py_compile 검증
```

## 검증
- `py_compile analysis_resources.py` — 문법 오류 확인
- `py_compile theoria_analyzer.py` — 문법 오류 확인
- 기존 GENRE_THEORY_WEIGHTS에서 "Habitus" 참조 → 이제 정의와 매칭 확인
- STATE_TRACKING_V2 필드 목록 = 스키마 psyche_states 필드 목록 동일 확인
- null 가이드: `get_active_modules(["modern", "drama"])` → [] (빈 리스트) → 6개 null hint 전부 출력 확인
- Spatial: OBSERVATION_INTENT에 프레임워크 존재 + 스키마에 인라인 설명 없음 확인
