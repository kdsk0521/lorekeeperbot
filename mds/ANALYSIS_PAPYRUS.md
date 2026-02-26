# Papyrus × Lorekeeper 교차 분석

> 외부 프롬프트 문서 "Papyrus" (롤플레이 시스템 프롬프트)의 원칙을 Lorekeeper 아키텍처와 대조.
> 분석 순서: Code → Analysis → Iceberg → Text

---

## 0. 전체 매핑 — 겹침 판정

| Papyrus 원칙 | Lorekeeper 대응 | 겹침 | 상태 |
|---|---|---|---|
| Content Policy (B) | CONTENT_AUTHORIZATION_MANDATE | 100% | 이미 구현 |
| Pidgin→Decompression | Craft Axis 2 + Anti-cliché §9 | 95% | 이미 구현 |
| Cliché Preemption (¶8) | Telescope ☠ + ANTI_CLICHE | 95% | 이미 구현 |
| Evidence Not Verdict | Mirror §A | 90% | 이미 구현 |
| No Echo/Comfort/Convergence | Mirror §E/F/G | 90% | 이미 구현 |
| Body First (James-Lange) | CORE RULES + Camera Eye | 90% | 이미 구현 |
| Silence Architecture | 間 (Ma) §N | 85% | 이미 구현 |
| Dual Signal (body disagrees) | Mirror §O + Self-Opacity | 85% | 이미 구현 |
| Scheherazade | Mirror §G-1 + Narrative Chain | 80% | 이미 구현 |
| World Beyond Edges (C) | CHARACTER AUTHORITY(제한적) | N/A | 아키텍처 차이 |
| Structured Reasoning (A/B/P/✘) | Telescope v3 | 60% | 부분 겹침 |
| **Withholding Engine** (¶1-¶3) | Scheherazade(플롯만) | 30% | **신규** |
| **Contradiction = Life** (¶4-¶6) | UNEARNED CHANGE(반대방향) | 20% | **신규** |
| **Retroactive Rewriting** (¶7) | Palimpsest(물리적만) | 25% | **신규** |
| **Varied Not-Arriving** (¶3) | RHETORICAL ROTATION(산문만) | 20% | **신규** |
| **Surprise in Consistency** (A2) | 암묵적 | 15% | **신규** |

**결론: 9개 이미 구현, 1개 아키텍처 차이, 5개 신규 개념**

---

## 1. CODE 레이어

Papyrus는 코드 레벨 메커니즘을 제안하지 않는다. 모두 프롬프트/분석 레이어의 개념.

| # | 개념 | 코드 영향 | 판정 |
|---|---|---|---|
| C-1 | Telescope 게이트 추가 (Rift/Rewrite) | `text_resources.py` TELESCOPE_PROTOCOL | Text 레이어에서 처리 |
| C-2 | deep_read에 "rift" 필드 추가? | `theoria_analyzer.py` 스키마 | Analysis에서 검토 |
| C-3 | 새 코드 모듈/파이프라인 필요? | 없음 | 삭제 |

**CODE 결론: 신규 코드 0건. 모든 채용은 프롬프트/스키마 레벨.**

---

## 2. ANALYSIS 레이어 (Theoria 프롬프트/스키마)

### A-1. Retroactive Significance Detection ★★★

**Papyrus**: "This scene can change what earlier scenes meant. A gesture that was casual in Turn 3 becomes the first sign of something that was always present."

**현재 Lorekeeper**:
- Palimpsest (Craft Axis 1): "past bleeds through present" — 물리적 흔적만
- Scene Continuity: DAI snapshot + render fingerprint — 연속성 추적, 의미 재해석 아님
- Fermentation Recall: 기억 변형 — 기억의 왜곡이지 의미의 변환이 아님

**겹침**: Palimpsest가 가장 가까우나, "과거의 물리적 흔적이 현재에 남아있다" (예: 벽의 얼룩)이고, Papyrus의 "이 장면이 3턴 전 제스처의 *의미*를 바꾼다"와는 다른 차원.

**채용 판정**: **유지** — Theoria가 `continuity_check`에서 retroactive significance를 감지할 수 있도록 확장.

**구현 방안**: THEORIA_CHAIN에 1-2줄 추가. "이번 장면이 이전 장면의 의미를 바꿀 수 있는 순간인지 감지" → `continuity_check.rewrite` 필드 (str or null).

---

### A-2. Momentary Contradiction — 원칙 추가 ★★★

**Papyrus**: "What the character does will sometimes contradict what the sheet says. Do not treat this as error. A person who acts against their own self-image is not broken — they are, in that moment, most visibly a person."

**현재 Lorekeeper**:
- UNEARNED CHANGE PROHIBITION: 변화에는 4가지 조건 필요
- §4 CHARACTER CONSISTENCY: "cold-blooded killer needs 10+ turns"
- BEHAVIORAL PERSISTENCE: "Bad people do bad things and FEEL FINE"
- Self-Opacity: "claims X — actual: Y"

**핵심 구별**:
- **Lorekeeper의 관심사**: *영구적 변화*의 방지 (관성 보존)
- **Papyrus의 관심사**: *순간적 모순*의 허용 (일탈 ≠ 성격 변화)
- 이 둘은 **시간축이 다르다**. 양립 가능.

예시:
- 차가운 인물이 한 장면에서 따뜻하게 행동 → Papyrus: OK, 가장 살아있는 순간
- 그 따뜻함이 다음 턴부터 지속 → Lorekeeper: UNEARNED CHANGE → revert

**채용 판정**: **유지** — STATE_TRACKING_V2에 원칙 1줄 추가. 새 스키마 필드보다 가벼움.

---

### A-3. Attractor Detection ★★

**Papyrus**: "Attractor: what loses its force when named directly"

**현재 Lorekeeper**:
- Self-Opacity: 개인 심리 (인물 레벨)
- deep_read Lack: 인물 구조 (무의식적 결핍)
- Scheherazade: 플롯 레벨 개방성

**겹침**: Attractor는 **장면 레벨**의 "이름 붙이면 죽는 것" — Self-Opacity(개인)/Lack(구조)/Scheherazade(플롯)과 다른 입도.

**채용 판정**: **Text로 이동** — 개념적 가치 있으나 Theoria 스키마 추가 대비 부하가 큼. deep_read Lack + Self-Opacity + Scheherazade 조합이 기능적 대체. 렌더러 원칙으로 처리.

---

### Analysis 레이어 요약

| # | 항목 | 결정 | 구현 위치 |
|---|---|---|---|
| A-1 | Retroactive Significance | **유지** | THEORIA_CHAIN 확장 |
| A-2 | Momentary Contradiction | **유지** | STATE_TRACKING_V2 원칙 추가 |
| A-3 | Attractor Detection | **Text로 이동** | 렌더러 원칙으로 처리 |

---

## 3. ICEBERG 레이어

| # | 개념 | iceberg 영향 | 판정 |
|---|---|---|---|
| I-1 | Retroactive cue → continuity hint | `translate_continuity_check()` 확장 | A-1 채용 시 자동 연결 |
| I-2 | Rift → behavioral hint | deep_read 번역 시 rift 정보 소비 | A-2가 원칙이면 별도 번역 불필요 |
| I-3 | Attractor → "이름 붙이지 마라" | 정적 원칙 | Text로 |

**ICEBERG 결론**: A-1 채용 시 `translate_continuity_check()`에 `rewrite` 필드 소비 추가 (1건). 나머지 iceberg 변환 불필요 — 정적 프롬프트 원칙으로 충분.

---

## 4. TEXT 레이어 (렌더러 프롬프트)

### T-1. Withholding Engine — 보류의 엔진 ★★★

**Papyrus**: "Write toward the center of the character. Do not arrive. The engine runs on almost."

**현재 Lorekeeper**:
- Scheherazade (§G-1): **플롯** 연속성 (열린 질문/미해결)
- NO PREMATURE CONVERGENCE (§G): 상호 이해는 획득해야 함
- AMBIENT PERSISTENCE: 세계는 미해결이 기본

**겹침 분석**: Scheherazade = **플롯**의 보류. Withholding Engine = **인물 핵심 긴장**의 보류. 전혀 다른 축.

예시:
- Scheherazade: "이 미스터리의 답을 아직 주지 마라" (플롯)
- Withholding Engine: "이 인물이 왜 이러는지 설명하지 마라. 행동으로 거의 보여주되, 도착하지 마라" (인물)

**채용**: **유지** — MIRROR_WORKSHOP에 §Q로 추가

**텍스트 초안** (~3줄):
```
### Q. WITHHOLDING ENGINE (保留)
Write toward the center of the character. Do not arrive. The moment the core tension
is named, explained, and resolved, the character stops being interesting — desire
sustained by what is almost-shown outlasts desire satisfied.
The means of not-arriving vary by scene: deflection (turn away into joke/gesture),
displacement (energy detonates somewhere irrelevant), circling (same unsaid thing
from different angles), substitution (near-equivalent offered, not quite).
A method used identically twice becomes visible as method — visibility kills it.
```

**배치**: MIRROR_WORKSHOP, §G-1 SCHEHERAZADE 다음. 플롯 보류(G-1) → 인물 보류(Q) 순서.

---

### T-2. Contradiction = Life ★★★

**Papyrus**: "A person who acts against their own self-image is not broken — they are, in that moment, most visibly a person."

**현재 Lorekeeper**:
- UNEARNED CHANGE PROHIBITION → 영구적 변화 방지 (유지)
- §4 CHARACTER CONSISTENCY → 10턴 빌드업 (유지)
- 순간적 모순의 허용 원칙은 **없음**

**핵심 충돌 해소**:
- UNEARNED CHANGE = **영구적 변화**의 gate
- CONTRADICTION = LIFE = **순간적 일탈**의 허용
- 시간축이 다르므로 양립 가능. "momentary deviation, not character change"로 경계 명시.

**채용**: **유지** — MIRROR_WORKSHOP에 §R로 추가

**텍스트 초안** (~2줄):
```
### R. CONTRADICTION IS LIFE (矛盾)
A character who acts against their own self-image — the distrustful one who trusts,
the controlled one who loses control — is not broken. They are, in that moment,
most alive. Do not repair the contradiction with flashback explanation or internal
narration. Let it generate tension that passes into the next scene unresolved.
This is momentary deviation, not character change. The contradiction does not erase
the pattern — it reveals what the pattern costs.
```

**배치**: MIRROR_WORKSHOP, §Q WITHHOLDING ENGINE 다음.

**UNEARNED CHANGE와의 관계**: §R은 순간적 일탈 허용, UNEARNED CHANGE는 영구적 변화 gate. 상호 보완.

---

### T-3. Retroactive Rewriting ★★★

**Papyrus**: "This scene can change what earlier scenes meant. A hand withdrawn is still a hand withdrawn. But its significance shifts."

**현재 Lorekeeper**:
- Palimpsest: "past bleeds through present" — 물리적 흔적
- DIALOGUE HAS MEMORY: 대사 기억 — 내용 기억이지 의미 재해석 아님

**채용**: **유지** — PROSE_CRAFT_PROTOCOL에 추가

**텍스트 초안** (~2줄):
```
### RETROACTIVE REWRITING (遡及)
This scene does not only move forward. What happens here can change what earlier
scenes meant — a withdrawn hand becomes the first sign of something that was always
present. The event does not change; its significance shifts. You are not only writing
the present scene; you are reorganizing the past.
```

**배치**: PROSE_CRAFT, NONLINEAR TIME 뒤. 비선형 시간 → 소급적 의미 변환 = 자연스러운 확장.

---

### T-4. Surprise within Consistency ★★

**Papyrus**: "Mere consistency is competence. Surprise within consistency is fiction."

**현재 Lorekeeper**: 암묵적. Scheherazade/Harpoons가 부분적으로 커버하지만 원칙 선언 없음.

**채용**: **유지** — AI_CORE_IDENTITY ANTI-MECHANIZATION에 1줄 추가

**텍스트**: `- Mere consistency is competence. Surprise within consistency is fiction. The world that only fulfills expectations is a dead world.`

---

### T-5. Telescope 게이트 확장 ★★

**Papyrus 고유 추론 요소** (Telescope에 없는 것):
- **Rift**: "where char fails to coincide with their own self-image right now"
- **Rewrite**: "what earlier moment this scene retroactively transforms"

**채용**: Telescope v3에 2개 필드 추가

[Character] 섹션:
```
  ├ [Char.Rift] Any acting NPC contradicting their own profile RIGHT NOW?
    → note what + why. Momentary, not permanent.
```

[Scene] 섹션:
```
  └ [Scene.Rewrite] Does this scene retroactively transform an earlier moment's
    significance? → cite which moment + how. "none" = forward only.
```

---

## 5. 삭제/미채용 항목 (7건)

| Papyrus 원칙 | Lorekeeper 대응 | 삭제 사유 |
|---|---|---|
| Content Policy (B) | CONTENT_AUTHORIZATION_MANDATE | 완전 중복 |
| Cliché Preemption (¶8) | Telescope ☠ + ANTI_CLICHE | 완전 중복 |
| Body First | James-Lange + Camera Eye | 완전 중복 |
| Pidgin→Decompression | Craft Axis 2 | 완전 중복 |
| Evidence Not Verdict | Mirror §A | 완전 중복 |
| World Beyond Edges (C) | CHARACTER AUTHORITY | 아키텍처 차이 (TRPG에서 렌더러 세계 발명 제한은 의도적) |
| Wrapper Philosophy | Theoria 전처리 아키텍처 | 아키텍처 차이 (데이터 슬롯은 Theoria가 전처리) |

---

## 6. 채용 최종 요약

### 채용 항목 (6건)

| # | 항목 | 레이어 | 우선순위 | 토큰 영향 |
|---|---|---|---|---|
| T-1 | Withholding Engine | Text (Mirror Workshop §Q) | P0 ★★★ | +80 |
| T-2 | Contradiction = Life | Text (Mirror Workshop §R) | P0 ★★★ | +65 |
| T-3 | Retroactive Rewriting | Text (Prose Craft) | P1 ★★★ | +55 |
| T-4 | Surprise in Consistency | Text (AI Core Identity) | P1 ★★ | +20 |
| T-5 | Telescope Rift/Rewrite | Text (Telescope) | P1 ★★ | +30 |
| A-2 | Momentary Contradiction 원칙 | Analysis (State Tracking) | P1 ★★★ | +25 |

**총 토큰 순증: ~275**

### 레이어 이동 (1건)

| 원본 | 이동 | 사유 |
|---|---|---|
| A-3 Attractor Detection (Analysis) | → Text (렌더러 원칙) | 스키마 부하 > 원칙 1줄의 효과 |

---

## 7. 기존 원칙과의 관계

| 새 원칙 | 기존 원칙 | 관계 |
|---|---|---|
| Withholding Engine | Scheherazade | **확장** (플롯→인물) |
| Contradiction = Life | UNEARNED CHANGE | **보완** (순간←→영구) |
| Retroactive Rewriting | Palimpsest | **확장** (물리→의미) |
| Surprise in Consistency | Harpoons | **상위 원칙** |
| Telescope Rift | Self-Opacity | **장면 레벨 적용** |
| Telescope Rewrite | DIALOGUE HAS MEMORY | **의미 레벨 확장** |

---

## 8. 3축 표기(♪/▶/◎) 및 디렉티브와의 연결

| 새 원칙 | 관련 축 | 연결 방식 |
|---|---|---|
| **Withholding Engine** | ♪ 음악 | ♪ diminuendo(fading) + largo(slow)가 "도달 직전 멈춤"의 산문 리듬을 구체화 |
| **Contradiction** | ◎ 광학 [편광] | [편광]이 이미 "facade crack" 표현. 순간적 모순의 시각적 등가물 |
| **Retroactive Rewriting** | ◎ 시간밀도 [다중노출] | [다중노출](time overlap)이 과거와 현재의 의미 겹침을 물리적으로 구현 |
| **Surprise** | ♪ sforzando | sfz(explosive) = 일관성 내 돌발. 예측 밖 순간의 악보 표현 |

디렉티브 파이프라인 변경 없음 — 새 원칙은 정적 슬롯에 배치, 3축이 턴별로 구체적 지시.

---

## 9. 충돌 위험 분석

### Contradiction = Life ↔ UNEARNED CHANGE PROHIBITION

| 축 | Contradiction = Life | UNEARNED CHANGE |
|---|---|---|
| 시간 | 순간 (1턴) | 영구 (다수 턴) |
| 대상 | 행동의 일탈 | 성격의 변화 |
| 결과 | 다음 장면에 미해결로 전달 | 4가지 조건 충족 시만 허용 |
| 설명 | 금지 (플래시백/내면 해설) | N/A |

**결론**: 시간축 분리로 충돌 없음. §R 텍스트에 "momentary deviation, not character change" 명시.

### Withholding Engine ↔ NO PREMATURE CONVERGENCE

| 축 | Withholding Engine | No Premature Convergence |
|---|---|---|
| 대상 | 인물의 핵심 긴장 | 관계의 상호 이해 |
| 메커니즘 | 직접 명명 금지, almost 경제 | 묘사된 투쟁 없이 해결 금지 |
| 해제 조건 | 없음 (엔진이므로) | "earned through friction" 시 해제 |

**결론**: 보완 관계. NO PREMATURE CONVERGENCE는 관계/이해, Withholding Engine은 인물 정체성.

---

## 10. Papyrus 원본에서 직접 참고할 만한 문장들

> "The distance between the sheet and the person is not a deficiency — it is the space in which the character comes alive."

> "Desire sustained by what is almost-shown outlasts desire that has been satisfied."

> "A method used identically twice becomes visible as method, and visibility kills it."

> "A person who acts against their own self-image is not broken — they are, in that moment, most visibly a person."

> "You are not only writing the present scene. You are reorganizing the past."

> "Mere consistency is competence. Surprise within consistency is fiction."

> "What you write after refusing [the first image] will be slower to arrive and harder to construct. That is the cost. The return is that the reader encounters something they have not seen."
