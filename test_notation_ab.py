"""
N8 Notation Compositor A/B 테스트 — 토큰 0 소모, 로컬 실행.
여러 예시 장면에서 기존(개별 나열) vs 합성(compose_notations) 비교.
"""
import sys, os
sys.path.insert(0, os.path.dirname(__file__))

from une_facade import (
    _VIGOR_NOTATION, _COMPOSURE_NOTATION, _MIXED_NOTATION,
    _DOOM_NOTATION, _ENERGY_NOTATION, _POSITION_NOTATION,
)
from notation_compositor import compose_notations, compose_transition

# ========================================
# 테스트 시나리오 정의
# ========================================
SCENARIOS = [
    {
        "name": "1. 평화로운 일상 (slice_of_life, normal)",
        "scene_type": "normal",
        "genre": "slice_of_life",
        "vigor": 85, "composure": 90, "doom": 15,
        "energy": "idle", "position": "controlled",
        "prev_energy": "idle",
    },
    {
        "name": "2. 전투 직전 긴장 (action, combat)",
        "scene_type": "combat",
        "genre": "action",
        "vigor": 72, "composure": 45, "doom": 65,
        "energy": "rising", "position": "risky",
        "prev_energy": "steady",
    },
    {
        "name": "3. 보스전 클라이맥스 (horror, combat)",
        "scene_type": "combat",
        "genre": "horror",
        "vigor": 20, "composure": 12, "doom": 92,
        "energy": "detonation", "position": "desperate",
        "prev_energy": "peak",
    },
    {
        "name": "4. 로맨스 고백 장면 (romance, intimate)",
        "scene_type": "intimate",
        "genre": "romance",
        "vigor": 95, "composure": 35, "doom": 10,
        "energy": "steady", "position": "controlled",
        "prev_energy": "idle",
    },
    {
        "name": "5. 느와르 심문실 (noir, social)",
        "scene_type": "social",
        "genre": "noir",
        "vigor": 55, "composure": 30, "doom": 58,
        "energy": "rising", "position": "risky",
        "prev_energy": "rising",
    },
    {
        "name": "6. 폭발 직후 잔해 속 (drama, normal)",
        "scene_type": "normal",
        "genre": "drama",
        "vigor": 10, "composure": 8, "doom": 85,
        "energy": "aftershock", "position": "desperate",
        "prev_energy": "detonation",
    },
    {
        "name": "7. 코미디 소동극 (comedy, social)",
        "scene_type": "social",
        "genre": "comedy",
        "vigor": 80, "composure": 25, "doom": 5,
        "energy": "peak", "position": "risky",
        "prev_energy": "rising",
    },
]

def collect_notations(s):
    """시나리오에서 notation (layer, string) 쌍 수집 — _build_atmosphere_layer 로직 재현."""
    pairs = []

    # Position
    pos = s["position"]
    n = _POSITION_NOTATION.get(pos, "")
    if n: pairs.append(("situation", n))

    # Energy
    n = _ENERGY_NOTATION.get(s["energy"], "")
    if n: pairs.append(("scene", n))

    # Vigor
    v = s["vigor"]
    if v >= 70:   n = _VIGOR_NOTATION["high"]
    elif v >= 40: n = ""
    elif v >= 15: n = _VIGOR_NOTATION["strained"]
    else:         n = _VIGOR_NOTATION["collapsing"]
    if n: pairs.append(("body", n))

    # Composure
    c = s["composure"]
    if c >= 70:   n = _COMPOSURE_NOTATION["high"]
    elif c >= 40: n = ""
    elif c >= 15: n = _COMPOSURE_NOTATION["strained"]
    else:         n = _COMPOSURE_NOTATION["collapsing"]
    if n: pairs.append(("psyche", n))

    # Mixed
    v_low, c_low = v <= 39, c <= 39
    if v_low and c_low:
        pairs.append(("body+psyche", _MIXED_NOTATION["desperate"]))
    elif v >= 70 and c_low:
        pairs.append(("body+psyche", _MIXED_NOTATION["reckless"]))
    elif c >= 70 and v_low:
        pairs.append(("body+psyche", _MIXED_NOTATION["fragile"]))

    # Doom
    d = s["doom"]
    if d >= 80:   pairs.append(("world", _DOOM_NOTATION["critical"]))
    elif d >= 50: pairs.append(("world", _DOOM_NOTATION["high"]))

    return pairs

def run_test():
    print("=" * 70)
    print("  N8 Notation Compositor A/B Test")
    print("=" * 70)

    for s in SCENARIOS:
        pairs = collect_notations(s)

        print(f"\n{'─' * 70}")
        print(f"  {s['name']}")
        print(f"  vigor={s['vigor']}  composure={s['composure']}  doom={s['doom']}")
        print(f"  energy={s['energy']}  position={s['position']}  genre={s['genre']}")
        print(f"{'─' * 70}")

        # A: 기존 방식 (개별 나열)
        print("\n  [A] 기존: 개별 나열")
        for layer, note in pairs:
            print(f"    {note}")

        # B: 합성 방식
        composed = compose_notations(pairs, scene_type=s["scene_type"], genre=s["genre"])
        transition = compose_transition(
            {"dai_snapshot": {"energy_direction": s["prev_energy"]}},
            s["energy"]
        )

        print(f"\n  [B] 합성: compose_notations (scene={s['scene_type']}, genre={s['genre']})")
        if transition:
            print(f"    {transition}")
        for line in composed.split("\n"):
            print(f"    {line}")

        # 차이 요약
        a_lines = len(pairs)
        b_lines = len(composed.split("\n")) + (1 if transition else 0)
        conflicts = composed.count("[갈등]")
        print(f"\n  → A: {a_lines}줄 / B: {b_lines}줄 / 충돌감지: {conflicts}건")

    print(f"\n{'=' * 70}")
    print("  테스트 완료. NOTATION_COMPOSITOR_ENABLED=True로 실전 적용 가능.")
    print(f"{'=' * 70}")

if __name__ == "__main__":
    run_test()
