"""
Judgment Gate (N1) — 판정 트리거 프리필터.

설계 철학: "코드는 GM의 NO를 강화하되, GM의 YES를 뒤집지 않는다"
- Flash가 true → 코드가 false로 내림: 가능 (비행동 오탐 차단, 쿨다운)
- Flash가 false → 코드가 true로 올림: 하지 않음 (GM 재량 존중)
- 난이도(difficulty): 일절 건드리지 않음 (GM 재량)
"""

import re
import logging

logger = logging.getLogger("JudgmentGate")

# PbtA 원칙: "대화만으로는 Move가 아니다"
ACTION_PATTERNS = [
    r'(?:공격|때리|찌르|쏘|베|던지|막|피하|방어)',       # 전투
    r'(?:설득|협박|속이|유혹|위협|거짓말|간청)',          # 사회
    r'(?:탐색|수색|조사|살피|열|잠입|숨|도망|달려)',      # 탐색/은신
    r'(?:제작|수리|조합|해체|치료|요리|연금)',            # 제작/생존
    r'(?:attack|strike|dodge|persuade|sneak|craft)',       # 영어 폴백
]
ACTION_RE = re.compile('|'.join(ACTION_PATTERNS))

NON_ACTION_PATTERNS = [
    r'^[\s]*["\'].*["\'][\s]*$',                           # 순수 대사
    r'(?:생각하|느끼|기억하|떠올리|바라보|지켜보)',          # 내면/관찰
    r'(?:대화하|이야기하|말하|물어보|대답하)',              # 대화
]
NON_ACTION_RE = re.compile('|'.join(NON_ACTION_PATTERNS))


def gate_judgment(user_input: str, flash_needs_judgment: bool,
                  last_judgment_turn: int, current_turn: int,
                  resolve: str = "none") -> tuple:
    """
    Flash의 needs_judgment를 검증/오버라이드.
    Returns: (final_needs_judgment: bool, reason: str)
    """
    has_action = bool(ACTION_RE.search(user_input))
    is_non_action = bool(NON_ACTION_RE.search(user_input)) and not has_action

    # Rule 1: 비행동 입력 + Flash true → 오탐 차단
    if flash_needs_judgment and is_non_action:
        logger.info("Gate override: non-action input blocked (input=%s)", user_input[:50])
        return False, "gate_override: non-action input (dialogue/thought only)"

    # Rule 2: 쿨다운 — 직전 턴 판정 시 연속 차단
    # 예외: resolve=desperate (각오한 행동은 쿨다운 무시)
    if flash_needs_judgment and (current_turn - last_judgment_turn <= 1):
        if resolve != "desperate":
            logger.info("Gate override: cooldown (last=%d, current=%d)", last_judgment_turn, current_turn)
            return False, "gate_override: judgment cooldown (consecutive roll blocked)"

    # Rule 3: Flash false + 행동 동사 → 로그만 (오버라이드 안 함)
    if not flash_needs_judgment and has_action:
        logger.info(
            "Flash skipped judgment but action verb detected: %s",
            user_input[:50]
        )

    return flash_needs_judgment, "flash_decision_respected"
