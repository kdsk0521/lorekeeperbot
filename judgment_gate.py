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
    r'(?:잡|뻗|밀|당기|건네|꺼내|부수|올라|뛰)',         # 물리/일상
    r'(?:attack|strike|dodge|persuade|sneak|craft)',       # 영어 폴백
]
ACTION_RE = re.compile('|'.join(ACTION_PATTERNS))

NON_ACTION_PATTERNS = [
    r'^[\s]*["\'].*["\'][\s]*$',                           # 순수 대사
    r'(?:생각하|느끼|기억하|떠올리|바라보|지켜보)',          # 내면/관찰
    r'(?:대화하|이야기하|말하|물어보|대답하)',              # 대화
]
NON_ACTION_RE = re.compile('|'.join(NON_ACTION_PATTERNS))


def gate_judgment(user_input: str, flash_needs_judgment: bool) -> tuple:
    """
    Flash의 needs_judgment를 검증/오버라이드.
    Returns: (final_needs_judgment: bool, reason: str)

    [2026-10-15 C40] Rule 2(직전 턴 판정 쿨다운·desperate 예외) 삭제 — 연속 판정 허용(레티어스: "실패했다가
      성공하는 맛도 있고"). 쿨다운 턴은 굴림 없이 넘어가 사실상 기본 성공이 되던 구멍이었다(억까 4단계).
      장부(waterfall load/save_last_judgment_turn)도 읽는 곳이 0이 되어 같이 걷었다.
      스펙 composition/C40_인풋해석·억까_스펙_2026-10-15.md §3 B3.
    """
    has_action = bool(ACTION_RE.search(user_input))
    # 대사 부분 제거 후 NON_ACTION 판정 (혼합 입력 오탐 방지: "대사" 행동 / 행동 "대사")
    _dialogue_stripped = re.sub(r'[\u201c\u201d\u2018\u2019\'\""].*?[\u201c\u201d\u2018\u2019\'\""]', '', user_input)
    is_non_action = bool(NON_ACTION_RE.search(_dialogue_stripped)) and not has_action

    # Rule 1: 비행동 입력 + Flash true → 오탐 차단
    if flash_needs_judgment and is_non_action:
        logger.info("Gate override: non-action input blocked (input=%s)", user_input[:50])
        return False, "gate_override: non-action input (dialogue/thought only)"

    # Rule 3: Flash false + 행동 동사 → 로그만 (오버라이드 안 함)
    if not flash_needs_judgment and has_action:
        logger.info(
            "Flash skipped judgment but action verb detected: %s",
            user_input[:50]
        )

    return flash_needs_judgment, "flash_decision_respected"
