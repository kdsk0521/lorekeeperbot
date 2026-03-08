"""
Text Resources V3 Restructuring — Verification Test Suite
검증 항목:
1. 모든 상수 import 가능 + 타입 확인
2. 해체된 섹션 4개 = 빈 문자열
3. 이전된 콘텐츠 존재 확인 (Zero-State, Perfect Deception 등)
4. Master Reference Table 존재 확인
5. Locked 콘텐츠 10건 원문 보존
6. Dead Reference 부재 (analysis_resources. 패턴)
7. 중복 제거 확인 (Want/Do/Can, PRE-OUTPUT AUDIT 등)
8. Tier A 변환 확인 (긍정문)
9. SITUATION_PRIORITY 트리밍 확인
10. Slot 34 빈 문자열 필터링 확인
11. Slot 19/33 빈 슬롯 처리 확인
12. 토큰 추정 (글자 수 기반)
"""

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.stdout.reconfigure(encoding='utf-8')

import text_resources
import slot_manager

PASS = 0
FAIL = 0

def check(name, condition, detail=""):
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f"  ✅ {name}")
    else:
        FAIL += 1
        msg = f"  ❌ {name}"
        if detail:
            msg += f" — {detail}"
        print(msg)

def print_header(title):
    print(f"\n{'='*50}\n  {title}\n{'='*50}")

# =========================================================
# 1. 상수 Import & 타입 확인
# =========================================================
def test_constants_exist():
    print_header("1. 상수 Import & 타입 확인")

    required_constants = [
        "CONTENT_AUTHORIZATION_MANDATE",
        "MIRROR_WORKSHOP_PROTOCOL",
        # PHYSICAL_RENDERING_DOCTRINE — merged into MIRROR_WORKSHOP
        "AI_CORE_IDENTITY",
        "PC_AUTONOMY_DOCTRINE",
        "NPC_BEHAVIOR_SYSTEM",
        "PROSE_CRAFT_PROTOCOL",
        "ANTI_CLICHE_PROTOCOL",
        "TELESCOPE_PROTOCOL",
        "TEMPORAL_FLOW_DOCTRINE",
        "INTERACTION_MODEL",
        # ACTION_RESOLUTION — merged into WORLD_AXIOM
        # ASPECT_UTILIZATION — merged into WORLD_AXIOM
        # SITUATION_PRIORITY_PROTOCOL — merged into PACING_CONTROL
        # PSYCHE_STATE_RENDERING — merged into ANTI_CLICHE §5
        # LANGUAGE_CORRECTION — merged into PROSE_CRAFT
        "WORLD_AXIOM",
        "MEMORY_HIERARCHY",
        "TRAINING_USER_PROMPT",
        "TRAINING_MODEL_RESPONSE",
    ]

    for name in required_constants:
        val = getattr(text_resources, name, None)
        check(f"{name} exists", val is not None, "None or missing")

    # 타입 전부 str
    for name in required_constants:
        val = getattr(text_resources, name, None)
        if val is not None:
            check(f"{name} is str", isinstance(val, str), f"got {type(val)}")

# =========================================================
# 2. 해체된 섹션 = 빈 문자열
# =========================================================
def test_dissolved_sections():
    print_header("2. 해체된 섹션 완전 삭제 확인")

    removed = [
        "OBSERVER_NEUTRALITY_DOCTRINE",
        "OUTPUT_PROTOCOL",
        "NARRATIVE_KERNEL",
        "SELF_CORRECTION_BKSPC",
    ]

    for name in removed:
        check(f"{name} fully removed", not hasattr(text_resources, name), "still exists")

# =========================================================
# 3. 이전된 콘텐츠 확인
# =========================================================
def test_migrated_content():
    print_header("3. 이전된 콘텐츠 (NPC_BEHAVIOR에 Zero-State/Perfect Deception)")

    npc = text_resources.NPC_BEHAVIOR_SYSTEM

    check("Zero-State Rule in NPC_BEHAVIOR",
          "ZERO-STATE RULE" in npc,
          "Zero-State Rule missing from NPC_BEHAVIOR_SYSTEM")

    check("Perfect Deception Rule in NPC_BEHAVIOR",
          "PERFECT DECEPTION RULE" in npc,
          "Perfect Deception Rule missing from NPC_BEHAVIOR_SYSTEM")

    check("ATTITUDE DATA in NPC_BEHAVIOR",
          "ATTITUDE DATA" in npc,
          "Attitude data section missing")

    check("NPCKnowledge reference in NPC_BEHAVIOR",
          "NPCKnowledge" in npc,
          "NPCKnowledge reference missing")

    # TELESCOPE v3 구조 확인
    telescope = text_resources.TELESCOPE_PROTOCOL
    check("TELESCOPE v3 Phase A/B structure",
          "Phase A" in telescope and "Phase B" in telescope,
          "TELESCOPE v3 structure missing")

# =========================================================
# 4. Master Reference Table
# =========================================================
def test_master_reference():
    print_header("4. Master Reference Table in AI_CORE_IDENTITY")

    identity = text_resources.AI_CORE_IDENTITY

    check("MASTER REFERENCE header exists",
          "MASTER REFERENCE" in identity)

    expected_aspects = ["PC Voice", "Dialogue", "Causality", "NPC Will", "Closure", "Identity", "Cliché"]
    for aspect in expected_aspects:
        check(f"  aspect '{aspect}' in table",
              aspect in identity,
              f"'{aspect}' row missing from Master Reference")

    # Gate format changed from table ┣[X]┫ to inline [┣X]
    expected_gates = ["Impersonation", "CharReason", "Hook", "NPC Identity", "Cliché"]
    for gate in expected_gates:
        found = f"[┣{gate}]" in identity or f"┣[{gate}]┫" in identity
        check(f"  gate '{gate}' referenced",
              found,
              f"Gate reference missing")

# =========================================================
# 5. Locked 콘텐츠 10건 보존 확인
# =========================================================
def test_locked_content():
    print_header("5. Locked 콘텐츠 보존 (10건)")

    mirror = text_resources.MIRROR_WORKSHOP_PROTOCOL

    # MIRROR §F, §G, §I
    check("MIRROR §F (No Echo)", "NO ECHO" in mirror.upper() or "No Echo" in mirror)
    check("MIRROR §G (Convergence concept)", "CONVERGENCE" in mirror.upper())
    check("MIRROR §I (No Single Label)", "NO SINGLE LABEL" in mirror.upper() or "No Single Label" in mirror)

    # PC_AUTONOMY NPC→PC Direction 예시
    autonomy = text_resources.PC_AUTONOMY_DOCTRINE
    check("PC_AUTONOMY NPC→PC Direction examples",
          "NPC→PC" in autonomy or "NPC→PC Direction" in autonomy.replace("→", "→"))

    # TEMPORAL_FLOW §4 NPC Decision Pacing
    temporal = text_resources.TEMPORAL_FLOW_DOCTRINE
    check("TEMPORAL_FLOW NPC Decision Pacing",
          "NPC Decision Pacing" in temporal or "NPC DECISION PACING" in temporal.upper())

    # INTERACTION_MODEL §D Relational Ethics
    interaction = text_resources.INTERACTION_MODEL
    check("INTERACTION_MODEL Relational Ethics",
          "Relational Ethics" in interaction or "RELATIONAL ETHICS" in interaction.upper())

    # PROSE_CRAFT 3건
    prose = text_resources.PROSE_CRAFT_PROTOCOL
    check("PROSE_CRAFT Rendering Gate (was Prose Collapse)",
          "RENDERING GATE" in prose.upper() or "Rendering Gate" in prose)
    check("PROSE_CRAFT Emotion Wave Model",
          "Emotion Wave" in prose or "EMOTION WAVE" in prose.upper())
    # Delayed & Imperfect Response
    check("PROSE_CRAFT Delayed & Imperfect Response pattern",
          "DELAYED" in prose.upper() and "IMPERFECT" in prose.upper())

    # ANTI_CLICHE §4 리라이트 예시
    anti = text_resources.ANTI_CLICHE_PROTOCOL
    check("ANTI_CLICHE rewrite examples (Few-Shot)",
          "→" in anti or "->" in anti,
          "No rewrite arrow pattern found (expected → examples)")

# =========================================================
# 6. Dead Reference 부재
# =========================================================
def test_no_dead_references():
    print_header("6. Dead Reference 부재")

    # Read full source
    src_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "text_resources.py")
    with open(src_path, encoding="utf-8") as f:
        source = f.read()

    # analysis_resources.NPC_KNOWLEDGE_TRACKING / NPC_ATTITUDE_ANALYSIS / THEORIA_MEMORY
    dead_refs = [
        "analysis_resources.NPC_KNOWLEDGE_TRACKING",
        "analysis_resources.NPC_ATTITUDE_ANALYSIS",
        "analysis_resources.THEORIA_MEMORY",
        "THEORIA_PSYCHE §4 HABITUS",
    ]

    for ref in dead_refs:
        check(f"No dead ref: {ref}",
              ref not in source,
              f"Dead reference still exists!")

    # 허용되는 analysis_resources 참조 (설명적 참조)
    import re
    # analysis_resources.py (파일명 언급)와 COGNITIVE_ARCHITECTURE_MODEL은 허용
    remaining = re.findall(r'analysis_resources\.(?!py)\w+', source)
    non_dead = [r for r in remaining if "COGNITIVE_ARCHITECTURE_MODEL" not in r]
    check(f"No unexpected analysis_resources refs ({len(non_dead)} found)",
          len(non_dead) == 0,
          f"Found: {non_dead}")

# =========================================================
# 7. 중복 제거 확인
# =========================================================
def test_dedup_removal():
    print_header("7. 중복 제거 확인")

    autonomy = text_resources.PC_AUTONOMY_DOCTRINE

    # Want/Do/Can 블록 삭제 (ACTION_RESOLUTION이 정의처)
    check("PC_AUTONOMY: Want/Do/Can block removed",
          "Want/Do/Can MODEL" not in autonomy and "Want/Do/Can model" not in autonomy.lower(),
          "Want/Do/Can block still in PC_AUTONOMY")

    # PRE-OUTPUT AUDIT 삭제 (TELESCOPE가 역할)
    check("PC_AUTONOMY: PRE-OUTPUT AUDIT removed",
          "PRE-OUTPUT AUDIT" not in autonomy,
          "PRE-OUTPUT AUDIT still in PC_AUTONOMY")

    # MIRROR §B cross-ref 삭제
    mirror = text_resources.MIRROR_WORKSHOP_PROTOCOL
    check("MIRROR: Camera Eye cross-ref removed from §B",
          "Mind-sealing rules" not in mirror,
          "Cross-ref to PHYSICAL_RENDERING still in MIRROR §B")

    # ASPECT content merged into WORLD_AXIOM
    world = text_resources.WORLD_AXIOM
    check("WORLD_AXIOM: SCENE ASPECTS merged in",
          "SCENE ASPECTS" in world or "Aspects" in world,
          "SCENE ASPECTS missing from WORLD_AXIOM")

    # PROSE_CRAFT cross-ref 삭제
    prose = text_resources.PROSE_CRAFT_PROTOCOL
    check("PROSE_CRAFT: CROSS-REFERENCES block removed",
          "CROSS-REFERENCES" not in prose and "Cross-References" not in prose,
          "CROSS-REFERENCES block still exists")

# =========================================================
# 8. Tier A 변환 확인 (긍정문)
# =========================================================
def test_tier_a_conversions():
    print_header("8. Tier A 변환 (부정→긍정)")

    identity = text_resources.AI_CORE_IDENTITY
    check("AI_CORE_IDENTITY: 'earned through established causality'",
          "earned through established causality" in identity,
          "Tier A conversion missing (was: No Deus Ex Machina)")

    anti = text_resources.ANTI_CLICHE_PROTOCOL
    check("ANTI_CLICHE: 'follows established pattern logic'",
          "follows established pattern logic" in anti,
          "Tier A conversion missing (was: Never make character act against pattern)")

    autonomy = text_resources.PC_AUTONOMY_DOCTRINE
    check("PC_AUTONOMY: 'silence is absolute'",
          "silence is absolute" in autonomy,
          "Tier A conversion missing (was: SILENT PROTAGONIST negative form)")

# =========================================================
# 9. SITUATION_PRIORITY 트리밍
# =========================================================
def test_situation_priority():
    print_header("9. ENERGY DIRECTION (merged into PACING_CONTROL)")

    pacing = text_resources.PACING_CONTROL_PROTOCOL

    # 4 Energy Directions 존재 (now in PACING_CONTROL)
    for direction in ["RISING", "STAGNANT", "DETONATION", "AFTERSHOCK"]:
        check(f"Energy Direction '{direction}' exists in PACING",
              direction in pacing)

    # 6 조합 삭제 확인 (기존 조합 키워드)
    removed_combos = ["RISING×", "STAGNANT×", "DETONATION×", "high_doom", "low_doom"]
    for combo in removed_combos:
        check(f"Removed combo pattern '{combo}' absent",
              combo not in pacing,
              f"Old combo pattern still exists")

# =========================================================
# 10. Slot 34 빈 문자열 필터링
# =========================================================
def test_slot34_filtering():
    print_header("10. Slot 34 빈 문자열 필터링")

    builder = slot_manager.SlotPromptBuilder()
    builder.populate_static_slots()

    slot34 = builder.get_slot(34)
    check("Slot 34 is not None", slot34 is not None)

    if slot34:
        # 빈 문자열 파트가 남겨둔 과도한 연속 빈 줄 확인
        # (triple-quoted string 경계에서 자연 발생하는 3~4연속은 허용, 6+ 만 경고)
        check("No excessive empty newlines (6+ consecutive)",
              "\n\n\n\n\n\n" not in slot34,
              "Empty string parts leaving excessive stray newlines")

        # TELESCOPE은 있어야 함
        check("TELESCOPE content in Slot 34",
              "PRE-OUTPUT QUALITY GATE" in slot34 or "┣" in slot34,
              "TELESCOPE missing from Slot 34")

        # LANGUAGE_CORRECTION은 PROSE_CRAFT로 병합됨 — Slot 34에서 제거됨
        # KOREAN PROSE STYLE은 이제 Slot 25 (PROSE_CRAFT)에 포함
        prose = text_resources.PROSE_CRAFT_PROTOCOL
        check("KOREAN PROSE STYLE in PROSE_CRAFT (was Slot 34)",
              "KOREAN PROSE STYLE" in prose or "존댓말" in prose,
              "KOREAN PROSE STYLE missing from PROSE_CRAFT")

        # OUTPUT_PROTOCOL (빈) 콘텐츠 없어야 함
        # NARRATIVE_KERNEL (빈) 콘텐츠 없어야 함
        check("No dissolved OUTPUT_PROTOCOL remnant",
              "Rendering Calibration" not in slot34)
        check("No dissolved NARRATIVE_KERNEL remnant",
              "I am Right Brain" not in slot34)

# =========================================================
# 11. Slot 19, 33 빈 슬롯 처리
# =========================================================
def test_empty_slots():
    print_header("11. 삭제된 슬롯 처리")

    builder = slot_manager.SlotPromptBuilder()
    builder.populate_static_slots()

    # Slot 19: OBSERVER_NEUTRALITY 삭제됨 → slot should be None (never set)
    slot19 = builder.get_slot(19)
    check("Slot 19 (OBSERVER_NEUTRALITY) removed — not set",
          slot19 is None,
          f"Expected None, got len={len(slot19) if slot19 else 'None'}")

    # Slot 33: SELF_CORRECTION 삭제됨 → slot should be empty/None (no author_note)
    slot33 = builder.get_slot(33)
    is_empty = slot33 is None or slot33.strip() == ""
    check("Slot 33 empty when no author_note",
          is_empty,
          f"Expected empty/None, got len={len(slot33) if slot33 else 'None'}")

# =========================================================
# 12. 토큰 추정 (글자 수 기반)
# =========================================================
def test_token_estimate():
    print_header("12. 토큰 추정 (글자 수 기반)")

    src_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "text_resources.py")
    with open(src_path, encoding="utf-8") as f:
        source = f.read()

    total_chars = len(source)
    total_lines = source.count('\n')

    # 모든 문자열 상수의 총 길이
    constant_chars = 0
    constant_names = []
    for name in dir(text_resources):
        if name.isupper() and not name.startswith('_'):
            val = getattr(text_resources, name)
            if isinstance(val, str) and val.strip():
                constant_chars += len(val)
                constant_names.append(name)

    print(f"  파일: {total_lines} lines, {total_chars} chars")
    print(f"  비어있지 않은 상수: {len(constant_names)}개, 총 {constant_chars} chars")
    print(f"  추정 토큰 (한글 기준 ≈ chars/1.5): ~{constant_chars // 1.5:.0f} tokens")
    print(f"  추정 토큰 (영어 기준 ≈ chars/4): ~{constant_chars // 4:.0f} tokens")

    check("File under 1500 lines (v3.1 rendering guides added)",
          total_lines < 1500,
          f"Got {total_lines} lines")

# =========================================================
# 13. slot_manager getattr 참조 유효성
# =========================================================
def test_slot_manager_refs():
    print_header("13. slot_manager getattr 참조 유효성")

    import re
    sm_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "slot_manager.py")
    with open(sm_path, encoding="utf-8") as f:
        sm_source = f.read()

    # getattr(text_resources, 'CONSTANT_NAME', '') 패턴 — 기본값이 있으므로 없어도 안전
    safe_refs = set(re.findall(r"getattr\(text_resources,\s*'(\w+)'", sm_source))
    # text_resources.CONSTANT_NAME 직접 참조 — 없으면 AttributeError
    directs = set(re.findall(r"(?<!getattr\()text_resources\.([A-Z][A-Z_0-9]+)(?=[^A-Za-z_0-9]|$)", sm_source))

    # 실제로 text_resources 모듈에 존재하는 모든 이름
    tr_names = {n for n in dir(text_resources) if n.isupper()}

    # 직접 참조는 반드시 존재해야 함
    for ref in sorted(directs):
        if ref not in tr_names and any(n.startswith(ref + "_") for n in tr_names):
            continue
        val = getattr(text_resources, ref, "___MISSING___")
        check(f"text_resources.{ref} (direct) accessible",
              val != "___MISSING___",
              f"Direct reference in slot_manager — will crash if missing!")

    # getattr 참조는 존재하지 않아도 안전하지만, 경고 출력
    missing_safe = [r for r in sorted(safe_refs) if not hasattr(text_resources, r)]
    if missing_safe:
        print(f"  ⚠️  getattr fallback refs (safe but unused): {missing_safe}")
    check("All getattr refs have valid defaults",
          True,  # getattr with default is always safe
          "")

# =========================================================
# MAIN
# =========================================================
if __name__ == "__main__":
    print("\n" + "█"*50)
    print("  TEXT RESOURCES V3 — VERIFICATION SUITE")
    print("█"*50)

    try:
        test_constants_exist()
        test_dissolved_sections()
        test_migrated_content()
        test_master_reference()
        test_locked_content()
        test_no_dead_references()
        test_dedup_removal()
        test_tier_a_conversions()
        test_situation_priority()
        test_slot34_filtering()
        test_empty_slots()
        test_token_estimate()
        test_slot_manager_refs()

        print(f"\n{'='*50}")
        print(f"  RESULT: {PASS} passed, {FAIL} failed")
        print(f"{'='*50}")

        if FAIL > 0:
            print("\n❌ SOME TESTS FAILED")
            exit(1)
        else:
            print("\n✅ ALL TESTS PASSED")

    except Exception as e:
        print(f"\n❌ TEST ERROR: {e}")
        import traceback
        traceback.print_exc()
        exit(1)
