# 연출 연속성 시스템 — 어휘 확장 + 2층 인과 데이터 흐름

## Context

두 가지 문제가 하나의 파이프라인으로 연결된다:

**문제 A: 연출 어휘 부족** — 현재 Directing Notation은 음악(강도·속도·질감) + 카메라(구도) + 조명 2종만 있다. 인물 배치(관계의 공간화), 장면 전환(편집 리듬), 색채(분위기 색온도)가 빠져 있다.

**문제 B: 프레임 소멸** — Theoria가 매 턴 분석하는 DAI(CurrentLocation, EnergyDirection, QualityFlags 등)는 사용 후 소멸. Renderer 출력(조명, 색감, 리듬, 소실점)은 Theoria가 볼 수조차 없다. 이전 프레임이 기록되지 않으니 불연속이 발생한다.

**해결: 어휘 → 기록 → 피드백**
```
[Phase 1] 연출 어휘 확장 — 모델에게 더 풍부한 도구를 준다
     ↓
[Phase 2] DAI 스냅샷 — Theoria가 이미 분석한 것을 기록 (코드만, API 0)
     ↓
[Phase 3] 렌더링 지문 — 응답에만 존재하는 것을 추출 (Cognition batch 소섹션)
     ↓
[Phase 4] 피드백 루프 — 다음 턴 Theoria에 이전 프레임 전달 → 불연속 감지 → 보정 지시
```

---

## 불연속 방어 매트릭스

| 불연속 유형 | 현재 방어 | 커버 수준 | 해결 |
|---|---|---|---|
| **톤 급변** | QualityFlags, EnergyDirection | 부분 | Phase 2 (EnergyDirection 기록) |
| **캐릭터 변질** | psyche_states, NPC attitudes | 커버됨 | 불필요 |
| **설정 모순** | notebook, world_state, lore_summary | 커버됨 | 불필요 |
| **열린 질문 무시** | open_threads (매크로만) | 부분 | Phase 3 `unresolved` |
| **감각 단절** | SensoryAnchors (심리용) | **빈틈** | Phase 1 (색채 어휘) + Phase 3 `sensory` |
| **공간 점프** | CurrentLocation (위치명만) | 부분 | Phase 2 (위치) + Phase 3 (시선/배치) |
| **리듬 단절** | — | **빈틈** | Phase 1 (전환 어휘) + Phase 3 `rhythm` |

---

## 수정 파일

| 파일 | Phase | 변경 |
|---|---|---|
| une_facade.py | 1 | 6개 notation 테이블 확장 |
| text_resources.py | 1 | P. 확장 + VOICE CALIBRATION 제거 + ENERGY DIRECTION 축소 |
| domain_manager.py | 2 | `get/update_scene_continuity()` |
| orchestration.py | 2+3 | DAI 스냅샷 저장 + 렌더링 지문 배선 |
| cognition.py | 3 | `render_fingerprint` batch 섹션 |
| theoria_analyzer.py | 4 | 이전 프레임 입력 + `continuity_check` 출력 |
| iceberg.py | 4 | `translate_continuity_check()` |
| slot_manager.py | 4 | Slot 16에 보정 지시 주입 |

---

## Phase 1: 연출 어휘 확장 + text_resources 조정

### Step 1-0: text_resources.py 프롬프트 정리

notation 확장으로 인해 겹치게 된 부분 조정:

**A. VOICE CALIBRATION 제거** (L594-598)
```
### VOICE CALIBRATION (per scene beat)  ← 삭제
- Gaze: Close-up vs. Wide shot          ← notation 구도+배치가 커버
- Language: Raw vs. Polished             ← 유일 독립축이지만 SENTENCE ARCHITECTURE(L514)가 커버
- Atmosphere: Sparse vs. Saturated       ← notation 색채+조명이 커버
- Restraint: Explosive vs. Contained     ← notation 강도(pp~ff)가 커버
```
4개 축 모두 다른 섹션 또는 notation 테이블이 더 정밀하게 커버.
→ VOICE CALIBRATION 섹션 전체 삭제. (~20 tok 절약)

**B. ENERGY DIRECTION 축소** (L698-706, SITUATION_PRIORITY_PROTOCOL 내)
기존: 각 에너지 상태별 2-3문장 산문 설명.
notation 테이블이 이제 카메라+편집+색채로 파라미터를 전달하므로, 산문 설명을 **1문장**으로 축소:
```
## ENERGY DIRECTION
- IDLE: 일상의 질감. 필로우 — 사물과 환경이 말한다.
- RISING: 축적. 크로스컷 — 시선이 교차하고 무게가 쌓인다.
- STAGNANT: 정체. 에너지가 있지만 움직이지 않는다. 기다림과 회피.
- DETONATION: 폭발. 몽타주 — 산문이 변형된다.
- AFTERSHOCK: 잔해. 페이드 — 판단 없이 잔상만.
```
→ notation 용어를 자연스럽게 참조. 모델이 notation→산문 변환 연결을 학습. (~40 tok 절약)

**C. PARAGRAPH DENSITY 유지** (L521-527)
편집 어휘와 겹쳐 보이지만 스케일이 다름 (장면 vs 문단). 변경 없음.

**D. 프롬프트 가이드 P. 확장** (이전 Step 1-2와 동일, 아래 참조)

### Step 1-1: 테이블 확장 (une_facade.py)

```python
_POSITION_NOTATION = {
    "controlled": "상황 — mp, andante, 와이드, legato, 병렬 [하이키]",
    "risky":      "상황 — f, allegro, 투샷, marcato, 대면",
    "desperate":  "상황 — ff, presto, 로우앵글, staccato, 등지기",
}

_ENERGY_NOTATION = {
    "idle":       "장면 — mp, andante, 팬, legato [하이키]",
    "steady":     "장면 — mf, andante, 아이레벨, legato, 매치컷",
    "rising":     "장면 — f, allegro, 투샷, marcato, 크로스컷, crescendo",
    "falling":    "장면 — p, adagio, 롱테이크, legato, 페이드, diminuendo",
    "peak":       "장면 — ff, presto, 컷, sforzando, 점프컷",
    "stagnant":   "장면 — pp, largo, 롱테이크, legato, 필로우",
    "detonation": "장면 — sfz, presto, 와이드, sforzando, 몽타주",
    "aftershock": "장면 — p, adagio, 롱테이크, staccato, 페이드",
}

_VIGOR_NOTATION = {
    "high":       "신체 — f, allegro, 와이드, legato, 난색 [하이키]",
    "strained":   "신체 — p, adagio, 클로즈업:근육, marcato, 한색",
    "collapsing": "신체 — pp, largo, 클로즈업:호흡, staccato, 탈색 [저조도]",
}

_COMPOSURE_NOTATION = {
    "high":       "심리 — mf, andante, 투샷, legato, 병렬",
    "strained":   "심리 — p, adagio, 클로즈업:시선, staccato, 높낮이차, 한색",
    "collapsing": "심리 — pp, largo, 하이앵글, sforzando, 등지기, 탈색 [저조도]",
}

_MIXED_NOTATION = {
    "desperate": "신체+심리 — pp, largo, 하이앵글, staccato, 등지기, 탈색 [저조도]",
    "reckless":  "행동 — f, presto, 와이드, sforzando, 점프컷, 고채도",
    "fragile":   "의식 — p, adagio, 클로즈업:눈, legato, 필로우, 한색",
}

_DOOM_NOTATION = {
    "high":     "세계 — f, allegro, marcato, 고채도 [단일광원]",
    "critical": "세계 — ff, presto, sforzando, 고채도 [저조도]",
}
```

**추가 근거:**

| 테이블.키 | 추가 | 근거 |
|---|---|---|
| Position.controlled | 병렬, [하이키] | 안정=나란히+밝음 |
| Position.risky | 대면 | 대립=마주 봄 |
| Position.desperate | 등지기 | 절망=등 돌림 |
| Energy.idle | [하이키] | 평온=밝은 환경 |
| Energy.steady | 매치컷 | 안정 흐름=유사 형태 전환 |
| Energy.rising | 크로스컷 | 긴장 상승=두 시선 교차 |
| Energy.falling | 페이드 | 여운=천천히 사라짐 |
| Energy.peak | 점프컷 | 절정=시간 건너뜀 |
| Energy.stagnant | 필로우 | 정체=인물 없는 환경 |
| Energy.detonation | 몽타주 | 폭발=빠른 이미지 나열 |
| Energy.aftershock | 페이드 | 여파=서서히 가라앉음 |
| Vigor.high | 난색, [하이키] | 활력=따뜻한 색+밝음 |
| Vigor.strained | 한색 | 고갈=차가운 색조 |
| Vigor.collapsing | 탈색 | 소진=색 빠짐 |
| Composure.high | 병렬 | 사회 안정=나란히 |
| Composure.strained | 높낮이차, 한색 | 불안=권력차+차가움 |
| Composure.collapsing | 등지기, 탈색 | 붕괴=단절+탈색 |
| Mixed.desperate | 등지기, 탈색 | 이중 붕괴 |
| Mixed.reckless | 점프컷, 고채도 | 무모=초조+강렬 |
| Mixed.fragile | 필로우, 한색 | 취약=침묵+차가움 |
| Doom.high | 고채도, [단일광원] | 위기=강렬+이중성 |
| Doom.critical | 고채도 | 임계=극강렬 |

### Step 1-2: 프롬프트 가이드 확장 (text_resources.py)

기존 8줄 → 10줄. +2 신규 라인(배치, 전환), 조명 라인 확장, 색채 라인 추가:

```
### P. DIRECTING NOTATION (연출 표기)
Directive의 연출 지시는 음악·영상 어휘로 장면을 구현하는 파라미터다.
- 강도: pp(극약)~ff(극강), sfz(폭발)
- 속도: largo(극느림)~presto(극빠름)
- 질감: legato(매끄러움) staccato(끊김) marcato(강조)
- 구도: 클로즈업/와이드/투샷/오버더숄더/로우앵글/하이앵글
- 배치: 대면(대립)/병렬(연대)/등지기(단절)/높낮이차(권력차)/필로우(인물 없는 환경)
- 전환: 몽타주(빠른 나열)/크로스컷(교차)/점프컷(건너뜀)/매치컷(유사 전환)/페이드(여운)
- 흐름: 팬/컷/롱테이크, crescendo/diminuendo
- [조명]: 하이키(밝음)/저조도(그림자)/역광(실루엣)/단일광원(이중성)/측면광
- [색채]: 한색(고립)/난색(활력)/탈색(기억)/고채도(강렬)
이 용어를 텍스트에 절대 노출하지 마라. 관찰 가능한 행동과 감각으로 변환하라.
```

---

## Phase 2: DAI 스냅샷 저장 (1층)

### Step 2-1: 저장 함수 (domain_manager.py)

```python
def get_scene_continuity(channel_id: str) -> Dict[str, Any]:
    """Scene continuity 데이터 조회 (DAI 스냅샷 + 렌더링 지문)."""
    mem = get_session_ai_memory(channel_id)
    return mem.get("scene_continuity", {
        "dai_snapshot": {},
        "render_fingerprint": {},
        "discontinuity_flags": []
    })

def update_scene_continuity(
    channel_id: str,
    dai_snapshot: Dict[str, Any] = None,
    render_fingerprint: Dict[str, Any] = None,
    discontinuity_flags: list = None
) -> None:
    """Scene continuity 갱신."""
    mem = get_session_ai_memory(channel_id)
    sc = mem.get("scene_continuity", {
        "dai_snapshot": {},
        "render_fingerprint": {},
        "discontinuity_flags": []
    })
    if dai_snapshot is not None:
        sc["dai_snapshot"] = dai_snapshot
    if render_fingerprint is not None:
        sc["render_fingerprint"] = render_fingerprint
    if discontinuity_flags is not None:
        sc["discontinuity_flags"] = discontinuity_flags[:5]
    update_session_ai_memory(channel_id, {"scene_continuity": sc})
```

### Step 2-2: DAI 스냅샷 캡처 (orchestration.py — Theoria 실행 후)

```python
# [Scene Continuity 1층] DAI 스냅샷 — 이미 분석된 것을 기록
dai = ctx.dai or {}
dai_snapshot = {
    "location": str(dai.get("CurrentLocation", "")),
    "energy": str(dai.get("EnergyDirection", "")),
    "scene_type": str(dai.get("SceneType", "")),
    "position": dai.get("Position", {}).get("value", 0.5) if isinstance(dai.get("Position"), dict) else 0.5,
    "observation": str(dai.get("Observation", ""))[:200],
    "quality_flags": {k: v for k, v in (dai.get("QualityFlags") or dai.get("quality_flags") or {}).items()
                      if v and v != "null"},
    "chain_status": (dai.get("narrative_chain") or {}).get("chain_status", ""),
    "open_threads": (dai.get("narrative_chain") or {}).get("open_threads", [])[:5],
}
domain_manager.update_scene_continuity(channel_id, dai_snapshot=dai_snapshot)
```

**토큰 비용: 0** — 코드만으로 기존 DAI에서 추출.

---

## Phase 3: 렌더링 지문 추출 (2층)

### Step 3-1: cognition.py — `extract_all_updates()` 시그니처 + batch_sections

```python
previous_continuity=None  # NEW 파라미터

batch_sections = [s for s in ["social", "narrative", "quest", "world_state", "render_fingerprint"]
                  if extraction_hints.get(s, False)]
```

### Step 3-2: cognition.py — `_extract_batch()` 새 섹션

```python
if "render_fingerprint" in sections:
    sys_parts.append(
        "\n### render_fingerprint"
        "\nAnalyze the AI RESPONSE's rendering properties (not story content)."
        "\nOutput: `{\"gaze\": str, \"lighting\": str, \"palette\": str, "
        "\"rhythm\": str, \"unresolved\": []}`"
        "\n- gaze: 서사의 시선/초점 — 무엇을 클로즈업했고 무엇이 배경인가 (1문장 Korean)"
        "\n- lighting: 장면의 명암 — 밝기, 그림자, 광원 (1구절 Korean)"
        "\n- palette: 색감/온도감 — 따뜻함/차가움, 지배적 색조 (1구절 Korean)"
        "\n- rhythm: 산문 리듬 — 문장 길이 패턴, 쉼표/느낌표/온점 밀도, 호흡 (1구절 Korean)"
        "\n- unresolved: 씬 레벨 미결 디테일 — 응답되지 않은 것, 열린 감각, 중단된 행동. max 3. Korean."
    )
    prev = kwargs.get("previous_continuity", {})
    if prev:
        snap = prev.get("dai_snapshot", {})
        fp = prev.get("render_fingerprint", {})
        prev_parts = []
        if snap.get("location"):
            prev_parts.append(f"Location={snap['location']}")
        if snap.get("energy"):
            prev_parts.append(f"Energy={snap['energy']}")
        if fp.get("lighting"):
            prev_parts.append(f"Lighting={fp['lighting']}")
        if fp.get("palette"):
            prev_parts.append(f"Palette={fp['palette']}")
        if fp.get("rhythm"):
            prev_parts.append(f"Rhythm={fp['rhythm']}")
        if fp.get("unresolved"):
            prev_parts.append(f"Unresolved={fp['unresolved']}")
        if prev_parts:
            ctx_parts.append(f"[RenderFP] Previous: {' | '.join(prev_parts)}")
    else:
        ctx_parts.append("[RenderFP] No previous data")
```

### Step 3-3: cognition.py — 결과 언패킹

```python
rfp: Dict[str, Any] = batch.get("render_fingerprint", {})
# return dict에:
"RenderFingerprint": rfp if rfp else None
```

### Step 3-4: orchestration.py — 배선

**hints:**
```python
"render_fingerprint": True  # 항상 실행
```

**이전 데이터 전달:**
```python
prev_continuity = domain_manager.get_scene_continuity(channel_id)

updates = await cognition.extract_all_updates(
    ...,
    previous_continuity=prev_continuity
)
```

**결과 적용:**
```python
rfp = updates.get("RenderFingerprint")
if rfp and isinstance(rfp, dict):
    fingerprint = {k: rfp.get(k, "") for k in ("gaze", "lighting", "palette", "rhythm")}
    fingerprint["unresolved"] = rfp.get("unresolved", [])
    domain_manager.update_scene_continuity(channel_id, render_fingerprint=fingerprint)
```

---

## Phase 4: 피드백 루프

### Step 4-1: theoria_analyzer.py — `_build_continuity_context()` 새 메서드

```python
def _build_continuity_context(self, anchors: dict) -> str:
    """이전 DAI 스냅샷 + 렌더링 지문 → ### 4d. PREVIOUS FRAME (~100-150 tokens)"""
    mem = anchors.get("session_memory", {})
    sc = mem.get("scene_continuity", {})
    if not sc:
        return ""
    snap = sc.get("dai_snapshot", {})
    fp = sc.get("render_fingerprint", {})
    if not snap and not fp:
        return ""

    parts = ["### 4d. PREVIOUS FRAME"]

    if snap:
        if snap.get("location"):   parts.append(f"- Location: {snap['location']}")
        if snap.get("energy"):     parts.append(f"- Energy: {snap['energy']}")
        if snap.get("observation"): parts.append(f"- Observation: {snap['observation']}")
        if snap.get("chain_status"): parts.append(f"- Chain: {snap['chain_status']}")
    if fp:
        if fp.get("gaze"):     parts.append(f"- Gaze: {fp['gaze']}")
        if fp.get("lighting"): parts.append(f"- Lighting: {fp['lighting']}")
        if fp.get("palette"):  parts.append(f"- Palette: {fp['palette']}")
        if fp.get("rhythm"):   parts.append(f"- Rhythm: {fp['rhythm']}")
        unresolved = fp.get("unresolved", [])
        if unresolved:
            parts.append(f"- Unresolved: {'; '.join(str(u) for u in unresolved[:3])}")

    flags = sc.get("discontinuity_flags", [])
    if flags:
        parts.append("- ⚠ DISCONTINUITY:")
        for f in flags[:3]:
            parts.append(f"  [{f.get('type', '?')}] {f.get('desc', '')}")

    return "\n".join(parts) if len(parts) > 1 else ""
```

### Step 4-2: theoria_analyzer.py — `_build_prompt()`에 주입

`{session_memory_context}` 다음에 `{continuity_context}` 삽입.

### Step 4-3: theoria_analyzer.py — `_get_output_schema()`에 출력 필드 추가

```
## SCENE CONTINUITY
- "continuity_check": {
    "flags": [{"type": "spatial_break|sensory_break|object_break|tone_break|npc_break|rhythm_break",
               "risk": "Korean — 무엇이 불연속 위험이 있는지",
               "correction": "Korean — 자연스러운 연결 방법"}],
    "anchor_consumed": boolean
  } | null
```

### Step 4-4: iceberg.py — `translate_continuity_check()`

```python
_CONTINUITY_TYPE_KR = {
    "spatial_break": "공간 불연속",
    "sensory_break": "감각 불연속",
    "object_break": "사물 불연속",
    "tone_break": "분위기 불연속",
    "npc_break": "인물 불연속",
    "rhythm_break": "리듬 불연속",
}

def translate_continuity_check(check_data: Optional[dict]) -> str:
    """continuity_check → 보정 지시."""
    if not check_data or not isinstance(check_data, dict):
        return ""
    flags = check_data.get("flags", [])
    if not flags or not isinstance(flags, list):
        return ""
    directives = []
    for f in flags[:3]:
        if not isinstance(f, dict):
            continue
        ftype = f.get("type", "")
        correction = f.get("correction", "") or f.get("risk", "")
        type_kr = _CONTINUITY_TYPE_KR.get(ftype, ftype)
        if correction:
            directives.append(f"- {type_kr}: {correction}")
    if not directives:
        return ""
    return ("### 씬 연속성 보정\n"
            "이전 장면과의 불연속이 감지되었다. 자연스러운 연결을 만들어라.\n"
            + "\n".join(directives))
```

### Step 4-5: slot_manager.py — Slot 16 주입 (QualityFlags 패턴 동일)

```python
continuity_data = dai.get("continuity_check", {})
continuity_text = iceberg.translate_continuity_check(continuity_data)
if continuity_text:
    scene_intel_parts.append(continuity_text)
```

---

## 토큰 예산 (전체)

| 컴포넌트 | 입력 | 출력 | 비고 |
|---|---|---|---|
| Phase 1: 프롬프트 가이드 P. | +30 | — | 2줄 추가 |
| Phase 1: VOICE CALIBRATION 제거 | **-20** | — | 섹션 삭제 |
| Phase 1: ENERGY DIRECTION 축소 | **-40** | — | 산문→1줄 요약 |
| Phase 1: 테이블 항목 | +15-20 | — | 턴당 활성 3-5개 |
| Phase 2: DAI 스냅샷 | 0 | 0 | 코드만 |
| Phase 3: 렌더링 지문 | +120-150 sys, +40-60 ctx | +50-80 | batch 섹션 |
| Phase 4: Theoria 입력 | +100-150 | — | ### 4d 섹션 |
| Phase 4: Theoria 출력 | — | +30-50 | continuity_check |
| **합계** | **+245-350** | **+80-130** | **추가 API 콜 0** |

Phase 1 text_resources 정리로 ~60 tok 절약 → 순증가 더 줄어듦.

---

## 산출물

구현 완료 후 프로젝트 루트에 `PLAN_CONTINUITY_SYSTEM.md`로 이 플랜을 저장. ✅

---

## 검증

1. ✅ `py_compile` 수정 8개 파일
2. Phase 1: 기력 낮은 상태에서 `한색`, `탈색`이 directive에 포함되는지 로그 확인
3. Phase 1: energy=detonation에서 `몽타주`, doom=high에서 `[단일광원]` 확인
4. Phase 2: 첫 턴 DAI 스냅샷 저장 확인
5. Phase 3: 렌더링 지문 추출 확인 (gaze, lighting, palette, rhythm, unresolved)
6. Phase 4 첫 턴: continuity_check = null (이전 프레임 없음)
7. Phase 4 둘째 턴: ### 4d에 이전 프레임 → continuity_check 동작
8. 불연속 테스트: "비 오는 밤 → 갑자기 맑은 낮" → sensory_break 감지
9. 세션 리셋 → scene_continuity 자동 클리어 확인
