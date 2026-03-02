# 슬롯 매니저 산문 품질 위협 수정

## Context

34슬롯 전체 감사 결과, 산문 품질에 영향을 미치는 문제 11건 발견.
치명적 4건 + 중등도 7건. 수정 파일: `slot_manager.py`, `iceberg.py` (2개)

이전 작업 (스키마-리소스 동기화)은 이미 완료됨.

---

## A. 실질 수정 7건

### A-1. Slot 3 — MIRROR_WORKSHOP silent drop 경고 (slot_manager.py:372)

```python
mirror_workshop = getattr(text_resources, 'MIRROR_WORKSHOP_PROTOCOL', '')
if not mirror_workshop:
    logger.warning("[Slot 3] MIRROR_WORKSHOP_PROTOCOL missing — primacy philosophy slot empty")
self.set_slot(3, mirror_workshop)
```

### A-2. Slot 4 — PHYSICAL_RENDERING 폴백 경고 (slot_manager.py:376-377)

```python
phys = getattr(text_resources, 'PHYSICAL_RENDERING_DOCTRINE', '') or getattr(text_resources, 'PHYSICAL_RENDER', '')
if not phys:
    logger.warning("[Slot 4] PHYSICAL_RENDERING_DOCTRINE missing — rendering philosophy slot empty")
self.set_slot(4, phys)
```

### A-3. Slot 13 — N/A 노이즈 제거 (slot_manager.py:807-812)

```python
# 현재: 빈 dict → "Original: N/A\nEnhanced: N/A..." 노이즈
# 수정: 있는 필드만 append
_ia_fields = [
    ("Original", input_analysis_data.get("Original")),
    ("Enhanced", input_analysis_data.get("Enhanced")),
    ("Plausibility", input_analysis_data.get("Plausibility")),
]
_ia_lines = [f"{k}: {v}" for k, v in _ia_fields if v]
momentum = input_analysis_data.get("Momentum", "OPEN")
_ia_lines.append(f"Momentum: {momentum}")
if _ia_lines:
    input_analysis_parts.append("\n".join(_ia_lines))
```

### A-4. Slot 17 — gaze 위양성 수정 (iceberg.py:1050)

```python
# 현재: name in prev_gaze (substring match)
# 수정: split → set → exact match
if has_gaze:
    _gaze_names = {g.strip() for g in prev_gaze.replace("\n", ",").split(",") if g.strip()}
    in_focus = name in _gaze_names
```

### A-5. Slot 29 — 이모지 제거 (slot_manager.py:1179-1180)

```python
# 현재: f"\n\n🎭 PC_AUTONOMY_REMINDER:\n"
# 수정: 이모지만 제거, 영어 유지
f"\n\nPC_AUTONOMY_REMINDER:\n"
```

### A-6. Slot 30 — 플래시백 지시문 정리 (slot_manager.py:1094-1098)

```python
# 현재
f"\n[FLASHBACK] 회상 발동: \"{fb_decl}\"\n"
f"유형: {type_hint} | 무게: {fb_tier}.{plaus_hint}\n"
"회상 장면을 2-3문장으로 쓰고 현재로 복귀하라.\n"
"상황/위치만 바꾼다. 수치(기력, 둠)는 코드가 처리한다."

# 수정: 영어로 깔끔히, 수치 언급 삭제
f"\n[FLASHBACK] \"{fb_decl}\"\n"
f"Type: {type_hint} | Weight: {fb_tier}.{plaus_hint}\n"
"Render 2-3 sentences of memory, then return to present."
```

### A-7. Slot 16 temporal → iceberg DRY 이동

**iceberg.py에 추가**:

```python
_TEMPORAL_KR = {
    "past": "인물의 시선이 과거를 향한다",
    "future": "인물의 시선이 앞을 향한다",
    "present": "인물이 지금 이 순간에 머문다",
}

def translate_temporal_orientation(temporal_data: Optional[dict]) -> str:
    if not temporal_data or not isinstance(temporal_data, dict):
        return ""
    focus = temporal_data.get("focus", "")
    intensity = temporal_data.get("intensity", 0)
    if not focus or not isinstance(intensity, (int, float)) or intensity <= 0.3:
        return ""
    hint = _TEMPORAL_KR.get(focus, "")
    if not hint:
        return ""
    if intensity > 0.7:
        hint += " — 강하게"
    return f"### 시간 방향\n{hint}"
```

**slot_manager.py에서 교체** (10줄 → 2줄):

```python
temporal_text = iceberg.translate_temporal_orientation(dai.get("TemporalOrientation"))
if temporal_text:
    scene_intel_parts.append(temporal_text)
```

---

## B. 주석만 (코드 변경 없음)

### B-1. Slot 17 depth — 공유 확인 주석 (slot_manager.py:1010)

```python
# npc_depths는 여기서 1회 계산, slot 14 psyche_states + slot 17 dialogue directives 모두 공유.
npc_depths = iceberg.compute_npc_depths(...)
```

---

## 구현 순서

```
1. iceberg.py: _TEMPORAL_KR + translate_temporal_orientation() 추가
2. iceberg.py: gaze 매칭 수정 (A-4)
3. slot_manager.py: Slot 3 warning (A-1)
4. slot_manager.py: Slot 4 warning (A-2)
5. slot_manager.py: Slot 13 N/A 제거 (A-3)
6. slot_manager.py: Slot 29 이모지 제거 (A-5)
7. slot_manager.py: Slot 30 플래시백 정리 (A-6)
8. slot_manager.py: Slot 16 temporal → iceberg 호출 (A-7)
9. slot_manager.py: Slot 17 depth 주석 (B-1)
→ py_compile 검증
```

## 검증
- `py_compile slot_manager.py` + `py_compile iceberg.py`
- Slot 13: input_analysis 빈 dict → N/A 미출력
- Slot 17 gaze: "마리오" gaze에서 "마리" 비매칭
- Slot 29: 이모지 없음
- temporal: translate_temporal_orientation({"focus":"past","intensity":0.8}) → "### 시간 방향\n인물의 시선이 과거를 향한다 — 강하게"
