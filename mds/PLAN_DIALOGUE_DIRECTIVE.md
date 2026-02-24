# 대사 지시 시스템 (Dialogue Directive)

## Context

**문제**: 시스템은 장면을 "어떻게 보여줄까"(연출 표기)로 제어하지만, 대사를 "뭘 집을까"로 제어하지 않는다.

- **장면** = 존재한다. 목적이 없다. → 연출로 제어 (How)
- **대사** = 행위한다. 목적이 있다. → 가리킴으로 제어 (What to pick)

Theoria가 분석한 `logos_layer`, `self_opacity`, `active_needs`, `coping`, `secrets_held`, `false_beliefs` 등의 풍부한 구조화 데이터가 iceberg 번역 과정에서 **사라진다**. `translate_psyche_states()`는 `descriptor` 문자열만 추출하고, 이 필드들은 Renderer에 전달되지 않는다.

**핵심 연결**: Scene Continuity의 `render_fingerprint.gaze`(초점)가 장면과 대사의 **공유 앵커** 역할. Gaze가 가리키는 NPC = 대사 지시 풍부, 배경 NPC = 최소 지시. 영화의 심도(depth of field)처럼.

**두 컴포넌트**:
1. **보이스카드** (정적) — NPC가 평소에 어떻게 말하는가. 이미 존재 (`npc_manager.extract_voice_card()` → Slot 33)
2. **대사 지시** (동적) — 이번 턴 NPC가 대사로 뭘 이루려 하는가. **새로 추가**

---

## 수정 파일

| 파일 | 변경 | 상태 |
|---|---|---|
| `iceberg.py` | `compose_dialogue_directives()` + 헬퍼 함수 추가 | ✅ 완료 |
| `slot_manager.py` | Slot 17에 대사 지시 주입 (line 843 이후) | ✅ 완료 |
| `text_resources.py` | PSYCHE_STATE_RENDERING에 대사 지시 해석 가이드 추가 | ✅ 완료 |

---

## 데이터 소스 → 4축 매핑

기존 DAI 출력에서 재조합. 추가 API 콜 없음.

### 목적 (Purpose) — 이 발화로 뭘 이루려는가
- `psyche.active_needs` → 주요 동인 (Henderson/Erikson enum → 한국어 변환)
- `relation.value_conflict` → 갈등 시 해소 방향

### 전략 (Strategy) — 어떤 방식으로
- `relation.logos_layer` → **핵심 필드** ("THIS TURN behavioral hint")
- `psyche.coping` → problem_focused=직접 / emotion_focused=우회 / avoidant=회피
- `relation.stage` → front=체면 / back=꾸밈없이
- `psyche.decision_mode` → reactive=즉흥 / deliberate=계산
- `relation.negotiation_stance` → cooperative=협력 / competitive=경쟁 / exploitative=착취 (NEGOTIATION 모듈)
- `relation.group_dynamic` → conformity=동조 / obedience=복종 / groupthink=집단사고 (GROUP_DYNAMICS 모듈)

### 숨김 (Hidden) — 뭘 안 보여줄지
- `psyche.self_opacity` → "claims X — actual: Y"에서 Y
- `NPCKnowledge.secrets_held` → 명시적 비밀
- `NPCKnowledge.false_beliefs` → 잘못된 믿음 (본인은 모름)
- `NPCKnowledge.deception_cues` → 거짓말 단서 (FORENSIC 모듈: Statement Analysis)

### 드러냄 (Revealed) — 뭘 보여줄지
- `relation.stage` → front/back 경계
- `relation.phase` (Peplau) → 관계 단계가 허용하는 깊이
- `NPCKnowledge.leak_risk` → 의도치 않은 노출 확률

---

## Gaze 기반 심도 (Depth of Field)

이전 턴 `render_fingerprint.gaze`로 대사 지시 깊이 결정:

| 조건 | 지시 깊이 | 내용 |
|---|---|---|
| NPC 이름이 gaze에 포함 | **Full** | logos_layer + purpose + hidden + strategy |
| NPC 이름이 gaze에 없음 | **Minimal** | logos_layer만 |
| gaze 없음 (첫 턴) | **Moderate** | logos_layer + purpose |

NPC 이름 매칭: `npc_name in gaze_text` (한국어 이름은 2-4자, 조사 붙어도 substring 매칭 작동)

---

## 과잉 지시 방지 규칙

| 축 | 출력 조건 | 이유 |
|---|---|---|
| `logos_layer` | 항상 (비어있으면 해당 NPC 건너뜀) | 핵심 턴별 힌트 |
| `active_needs` | 비어있지 않을 때만 | 기본 상태는 needs 없음 |
| `self_opacity` | non-null일 때만 | null = 자기인식 일치, 빈칸 없음 |
| `secrets/false_beliefs` | `leak_risk >= "medium"`일 때만 | 낮은 위험 = 이번 턴 관련 없음 |
| `value_conflict` | non-null일 때만 | null = 갈등 없음 |
| `coping/stage/decision_mode` | 전략 수식어로만 (독립 출력 안 함) | 짧은 조각, 보조 역할 |
| `negotiation_stance` | non-null이고 NEGOTIATION 모듈 활성 시 | 협상 장면에서만 의미 |
| `group_dynamic` | non-null이고 3+ NPC 장면에서 | 집단 압력이 발화에 영향 |
| `deception_cues` | NPCKnowledge에 있고 full directive 시 | 거짓말 단서가 대사에 배어나옴 |

---

## 구현 상세

### iceberg.py — `compose_dialogue_directives()`

`translate_npc_knowledge()` 뒤에 새 섹션 추가.

**주요 구성요소**:
- `_STRATEGY_HINTS`: coping/stage/decision_mode/negotiation_stance/group_dynamic → 한국어 행동 수식어
- `_NEEDS_HINTS`: 15개 Henderson/Erikson 욕구 enum → 한국어 목적 구문
- `_FRAMEWORK_TERMS_RE`: 학술 용어 제거 정규식 (membrane, monolithic, logos, peplau 등)
- `_strip_framework_terms(text)`: logos_layer 등에서 프레임워크 용어 제거
- `_extract_actual(opacity_str)`: "claims X — actual: Y" 포맷에서 Y 추출

**내부 로직**:
1. `has_gaze = bool(prev_gaze.strip())` 로 첫턴/이후턴 구분
2. NPC별 루프:
   - `npc_depths[name] >= 0.8` → skip (요약 장면 등 얕은 수면)
   - `logos_layer` 없으면 skip (분석 안 된 NPC)
   - `name in prev_gaze` → full, 아니면 minimal
3. **Minimal**: `_strip_framework_terms(logos)` 한 줄
4. **Full/Moderate**: purpose(needs) + strategy(logos+coping+stage+negotiation+group) + hidden(opacity+secrets+deception) + conflict
5. 프레임워크 용어 제거로 Renderer가 학술 용어를 산문에 노출하지 않게 방어

**출력 형태**:
```
### 대사 방향
(NPC 대사의 목적과 전략. 이 용어를 산문에 쓰지 마 — 대사가 수행하게 하라.)
- 리미: 안전을 확보하려. 경계를 시험하는 중 — 간접 질문으로. 체면을 유지하며. (실제로는 두렵다)
- 옥상 남자: 관찰 모드, 반응 최소화
```

### slot_manager.py — Slot 17 주입

**삽입 위치**: line 843 이후 (npc_depths 계산 완료 직후)

이유: `psyche_data`(line 814), `npc_knowledge`(line 772), `npc_depths`(line 843) 모두 이 시점에 사용 가능.
`extended_intelligence`(line 810에서 join됨)에 문자열 append.

```python
# [Slot 17 보충] 대사 방향 지시 (gaze 기반 심도)
_prev_gaze = ""
if channel_id:
    _sc = domain_manager.get_scene_continuity(channel_id)
    _prev_gaze = _sc.get("render_fingerprint", {}).get("gaze", "")

_dialogue_dir = iceberg.compose_dialogue_directives(
    psyche_data, npc_knowledge,
    prev_gaze=_prev_gaze, npc_depths=npc_depths,
)
if _dialogue_dir:
    if extended_intelligence:
        extended_intelligence += "\n\n" + _dialogue_dir
    else:
        extended_intelligence = _dialogue_dir
```

### text_resources.py — 대사 지시 해석 가이드

PSYCHE_STATE_RENDERING 닫는 `"""` 직전에 추가:

```
### DIALOGUE DIRECTIVE → SPEECH ACT
대사 방향(### 대사 방향)이 있을 때:
- 목적 → 서브텍스트. 전략 → 단어 선택과 리듬. 숨김 → 말에서 눈에 띄게 빠진 것.
- 숨기는 NPC는 그것을 돌아서 말한다. 목적이 있는 NPC는 그것을 향해 말한다. 둘 다 이름 붙이지 않고.
```

---

## 토큰 예산

| 컴포넌트 | 토큰 | 비고 |
|---|---|---|
| 헤더 | ~15 | "### 대사 방향" + 가이드 |
| Full NPC (1) | ~25-35 | 초점 NPC |
| Minimal NPC (2) | ~8-12 × 2 | 배경 NPC |
| 해석 가이드 (Slot 15) | ~30 | 정적, 1회 |
| **턴당 합계** | **~65-80** | 추가 API 콜 0 |

---

## 데이터 흐름 다이어그램

```
Turn N:
  Theoria → psyche_states + NPCKnowledge (DAI)
  Renderer → 응답 텍스트
  Cognition batch → render_fingerprint.gaze 추출
  → domain_manager.update_scene_continuity(gaze)

Turn N+1:
  slot_manager.build_34_step_prompt():
    ├─ L772: npc_knowledge = dai["NPCKnowledge"]
    ├─ L810: extended_intelligence 조립 (기존 Slot 17 내용)
    ├─ L814: psyche_data = dai["psyche_states"]
    ├─ L843: npc_depths = iceberg.compute_npc_depths()
    ├─ L843+: prev_gaze = domain_manager.get_scene_continuity().gaze  ← NEW
    ├─ L843+: dialogue_dir = iceberg.compose_dialogue_directives(     ← NEW
    │           psyche_data, npc_knowledge, prev_gaze, npc_depths)
    └─ L843+: extended_intelligence += dialogue_dir                    ← NEW

  Slot 17 → Renderer:
    기존: NPC 태도 + NPC 지식 + 친밀 + 관계 깊이
    추가: 대사 방향 (목적/전략/숨김)

  Slot 33 (별도): 보이스카드 (정적 말투) ← 기존 유지
  Slot 15 (별도): 해석 가이드 ← 3줄 추가
```

---

## Theory Emphasis Engine 연결

`theory_emphasis_engine.py`의 장르별 가중치가 대사 지시에 **암묵적으로** 영향:

- Theoria가 noir 장르에서 `Self-Opacity`를 강조 적용 → `self_opacity` 필드가 더 풍부 → 숨김 축 자연 강화
- Romance에서 `Logos Dynamics` 강조 → `logos_layer`가 membrane 상태를 상세히 기술 → 전략 축 풍성
- Comedy에서 `Goffman mask failures` 강조 → `stage` 전환이 빈번 → 전략 축의 아이러니

**조건부 모듈 → 추가 필드 활성화**:
- FORENSIC (noir/cyberpunk/cosmic_horror) → `deception_cues` 풍성 → 숨김 축에 거짓말 단서
- NEGOTIATION (noir/cyberpunk/steampunk/post_apoc/space_opera) → `negotiation_stance` + `logos_layer`에 신뢰 경계
- GROUP_DYNAMICS (wuxia/post_apoc/space_opera) → `group_dynamic` → 집단 압력 하 발화 패턴
- COSMIC_HORROR → `soma.dissociation` → (향후 확장 가능: 해리 수준별 발화 변질)

**결론**: `compose_dialogue_directives()`에 장르 분기 불필요. Theoria가 이미 장르 가중치를 적용한 결과를 읽는다.

---

## 검증 결과

1. ✅ `py_compile` 수정 3개 파일 통과
2. 첫 턴: gaze 없음 → 모든 relevant NPC에 moderate 지시
3. 둘째 턴: gaze 기반 필터링 → 초점 NPC만 full, 나머지 minimal
4. `self_opacity = null` 인 NPC → "숨김" 축 안 나옴
5. `leak_risk = "none"` → secrets 안 나옴
6. 요약 장면 (depth ≥ 0.8) → 대사 지시 전체 skip
7. logos_layer 비어있는 NPC → skip (directive 없음)
