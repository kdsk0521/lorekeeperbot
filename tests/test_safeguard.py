
import sys
import os
import io

# Force UTF-8 encoding for output
if sys.stdout.encoding != 'utf-8':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

# Add current directory to path
sys.path.append(os.getcwd())

from response_processor import process_bkspc, detect_pc_impersonation, filter_pc_impersonation

def test_bkspc():
    print("--- Testing BKSPC ---")
    test_cases = [
        ("The sky is green BKSPC blue.", "The sky is blue."),
        ("He go quickly BKSPC BKSPC went fast.", "He went fast."),
        ("정말 사칭이네요 BKSPC 아닙니다.", "정말 아닙니다."),
        ("Words BKSPC", ""),
        ("A B C BKSPC BKSPC D", "A D"),
    ]
    
    for input_text, expected in test_cases:
        result = process_bkspc(input_text)
        print(f"Input: {input_text} -> Result: {result}")
        assert result == expected or (not expected and not result), f"FAILED: {result} != {expected}"
    print("BKSPC Tests Passed!")

def test_impersonation():
    print("\n--- Testing Impersonation ---")
    pc_names = ["Clara"]
    
    test_cases = [
        # 1. 2nd person in narrative (Should fail)
        ("당신은 리더기를 켰다.", True),
        ("너는 고개를 끄덕였다.", True),
        ("플레이어는 생각했다.", True),
        ("당신의 눈이 빛났다.", True),
        
        # 2. 2nd person in quotes (Should pass)
        ('"당신, 제법이군요."', False),
        ("'너는 조심해야 해.'", False),
        
        # 3. Name based (Should fail)
        ("Clara는 웃었다.", True),
        ("Clara의 표정이 굳었다.", True),
        
        # 4. Mixed (Should detect only narrative)
        ('당신은 말했다. "당신이 어떻게?"', True),
    ]
    
    all_passed = True
    for text, should_fail in test_cases:
        violations = detect_pc_impersonation(text, pc_names)
        is_failed = len(violations) > 0
        print(f"Text: {text} | Detected: {is_failed}")
        if is_failed != should_fail:
             print(f"  ❌ FAILED: Expected {should_fail}, got {is_failed}")
             if violations:
                 print(f"  Violations: {violations}")
             all_passed = False
    
    if all_passed:
        print("Impersonation Tests Passed!")
    else:
        sys.exit(1)

if __name__ == "__main__":
    test_bkspc()
    test_impersonation()
