"""
Cognition → Narrative 파이프라인 테스트
인지 엔진 분석 → 서사 생성 연결 검증

[V3 UPDATE]
- Test 2, 3: PromptBuilder 클래스가 제거되어 스킵 처리됨
- Test 1: _build_nvc_summary 테스트는 유지
- 향후 SlotPromptBuilder 기반 테스트로 교체 예정
"""

import sys
import os
import io
import asyncio
import json

# Force UTF-8 Output
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
sys.path.append(r"c:\Users\kdsk\Desktop\lorekeeperbot\lorekeeperbot")

# =========================================================
# MOCK DATA
# =========================================================

MOCK_HISTORY = """
[2시간 전]
Luna: "오늘 날씨가 좋네요."
당신은 Luna와 함께 공원을 걸었다.

[1시간 전]
Luna: "우리... 벌써 3년이나 됐네요."
Luna가 살짝 얼굴을 붉히며 당신의 손을 잡았다.

[현재]
당신은 주머니에서 작은 상자를 꺼냈다.
"""

MOCK_LORE = """
### NPC: Luna
- 나이: 25세
- 성격: 다정하고 수줍음이 많음
- 관계: 플레이어와 3년간 교제 중 (비공식)
- 특징: 감정 표현에 서툴지만 진심을 담는 타입
"""

MOCK_RULES = """
### 연애 시스템
- 고백 성공률은 관계도에 비례
- 진정성 있는 고백은 보너스
"""

MOCK_PLAYER_CONTEXT = """
### [PLAYER STATUS]
Name: 주인공
Companions: Luna
Inventory: 반지 (Luna를 위해 준비한 것)
"""

USER_INPUT = "오래 지낸 여인에게 반지를 건내주며 말합니다 '나와 정식으로 사귀어줄래?'"

# =========================================================
# TEST 1: Mock Cognition Data
# =========================================================

def test_mock_cognition_data():
    """인지 엔진 출력을 시뮬레이션하고 NVC Summary 빌드 테스트"""
    print("=" * 60)
    print("TEST 1: Mock Cognition Data → NVC Summary")
    print("=" * 60)
    
    # 시뮬레이션된 인지 엔진 출력
    mock_nvc_result = {
        "Observation": "플레이어가 Luna에게 반지를 건네며 정식 교제를 요청함",
        "UserIntent": "Luna에게 공식적인 연인 관계를 제안",
        "InputAnalysis": {
            "Original": USER_INPUT,
            "Enhanced": "플레이어가 3년간 함께한 Luna에게 반지를 건네며 정식 교제를 요청한다",
            "Plausibility": "High",
            "LogicTrace": ["Contextualize", "EmotionInfer"],
            "Momentum": "Closed"  # 결정적 순간
        },
        "Position": {
            "value": 0.7,
            "reason": "거절 시 관계 손상 가능성. 3년의 관계가 걸림."
        },
        "Effect": {
            "value": 0.9,
            "reason": "성공 시 관계의 질적 변화. 공식 연인으로 전환."
        },
        "Aspects": ["공원의 따스한 햇살", "손에 쥔 반지", "긴장된 분위기", "Luna의 붉어진 볼"],
        "psyche_states": {
            "Luna": {
                "mental": {"value": 4, "descriptor": "elv", "intensity": "High"},  # 고양
                "soma": {"value": 2, "descriptor": "flush", "intensity": "Medium"},  # 상기
                "relation": {"value": 3, "descriptor": "vulnerable", "intensity": "High"}  # 취약/열림
            }
        },
        "narrative_chain": {
            "chain_status": "CLOSING",
            "topic_lock": "고백",
            "conclusion_proximity": "85%",
            "pending_decisions": ["Luna의 대답"]
        },
        "memory_triggers": [
            {
                "trigger": "반지",
                "character": "Luna", 
                "echo": "처음 만났던 날, 그가 건넨 작은 꽃"
            }
        ],
        "GMMove": {
            "type": "reveal_truth",
            "description": "Luna의 숨겨진 감정이 표면으로"
        },
        "TemporalOrientation": {
            "suggested_focus": "Luna의 반응과 내면의 갈등",
            "offscreen_npcs": ["공원의 다른 연인들이 지나감"],
            "active_threads": ["3년간의 애매한 관계", "Luna의 수줍은 성격"]
        }
    }
    
    # Mock Context 생성
    class MockResponseContext:
        def __init__(self):
            self.nvc_result = mock_nvc_result
            self.existing_attitudes = {
                "Luna": {"attitude": "devoted", "reason": "3년간 함께함", "last_updated": "2026-02-02 10:00"}
            }
            self.judgment_context = None
    
    class MockFilterConfig:
        filter_stale_data = True
        max_attitude_age_hours = 24
    
    # Import and test
    from orchestration_response import _build_nvc_summary
    
    ctx = MockResponseContext()
    filter_config = MockFilterConfig()
    
    nvc_summary = _build_nvc_summary(ctx, filter_config)
    
    print("\n[Generated NVC Summary]")
    print("-" * 40)
    print(nvc_summary)
    print("-" * 40)
    
    # 검증
    checks = [
        ("INPUT_ANALYSIS" in nvc_summary, "InputAnalysis 섹션"),
        ("PSYCHE_STATES" in nvc_summary, "Psyche States 섹션"),
        ("NARRATIVE_CHAIN" in nvc_summary, "Narrative Chain 섹션"),
        ("MEMORY_TRIGGERS" in nvc_summary, "Memory Triggers 섹션"),
        ("Luna" in nvc_summary, "NPC 이름"),
        ("반지" in nvc_summary, "Memory Trigger 키워드"),
        ("devoted" in nvc_summary, "NPC Attitude"),
    ]
    
    print("\n[Validation Results]")
    all_passed = True
    for check, desc in checks:
        status = "✅" if check else "❌"
        print(f"  {status} {desc}")
        if not check:
            all_passed = False
    
    return all_passed, nvc_summary


# =========================================================
# TEST 2: System Prompt Check
# =========================================================

def test_system_prompt_includes_new_constants():
    """[SKIPPED - V3] PromptBuilder 제거됨. SlotPromptBuilder 테스트로 교체 예정."""
    print("\n" + "=" * 60)
    print("TEST 2: [SKIPPED] PromptBuilder removed in V3")
    print("=" * 60)
    print("  -> Use slot_manager.SlotPromptBuilder for V3 testing")
    return True, "SKIPPED"


# =========================================================
# TEST 3: Full Dynamic Prompt Build
# =========================================================

def test_full_prompt_build(nvc_summary: str):
    """[SKIPPED - V3] PromptBuilder 제거됨. SlotPromptBuilder 테스트로 교체 예정."""
    print("\n" + "=" * 60)
    print("TEST 3: [SKIPPED] PromptBuilder removed in V3")
    print("=" * 60)
    print("  -> Use slot_manager.build_34_step_prompt for V3 testing")
    return True


# =========================================================
# MAIN
# =========================================================

def main():
    print("\n" + "=" * 60)
    print("🧠 COGNITION → NARRATIVE PIPELINE TEST")
    print("Input: " + USER_INPUT[:50] + "...")
    print("=" * 60)
    
    results = []
    
    # Test 1
    passed1, nvc_summary = test_mock_cognition_data()
    results.append(("Mock Cognition → NVC Summary", passed1))
    
    # Test 2
    passed2, _ = test_system_prompt_includes_new_constants()
    results.append(("System Prompt Constants", passed2))
    
    # Test 3
    passed3 = test_full_prompt_build(nvc_summary)
    results.append(("Full Dynamic Prompt", passed3))
    
    # Summary
    print("\n" + "=" * 60)
    print("📊 TEST SUMMARY")
    print("=" * 60)
    
    all_passed = True
    for name, passed in results:
        status = "✅ PASS" if passed else "❌ FAIL"
        print(f"  {status}: {name}")
        if not passed:
            all_passed = False
    
    print("\n" + ("🎉 ALL TESTS PASSED!" if all_passed else "⚠️ SOME TESTS FAILED"))
    return all_passed


if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)
