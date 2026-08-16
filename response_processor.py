"""
Lorekeeper TRPG Bot - Response Processor Module
AI 응답 후처리를 담당하는 모듈입니다.

역할:
- Scene type 키워드 감지
- PC 사칭 패턴 검출 및 필터링
- 응답 유효성 검증

persona.py에서 분리된 응답 처리 로직입니다.
프롬프트 생성 로직과 분리하여 유지보수성을 높입니다.
"""

import re
import logging
from typing import Optional, List, Dict, Tuple

logger = logging.getLogger(__name__)


# =========================================================
# Em-dash 감축 (격랑 V1.6 "필살 무기" 이식, 2026-06-20)
# =========================================================
# 딥식 V4 등 엠대쉬 과용 + 컨텍스트 미러링 모델 대응.
# 출력을 강제 청소하지 않고, "모델이 보는 과거 출력"에서 엠대쉬를 줄여
# 미러링 피드백 루프를 끊는다(= 없애기보단 줄이기).
# 격랑의 editprocess regex `(\s*[—–―⸺⸻﹘－]\s*)` → 공백 치환과 동일 원리.
_EMDASH_CHARS = "—–―⸺⸻﹘－"  # em, en, horizontal bar, two/three-em, small em, fullwidth hyphen
_EMDASH_RUN = re.compile(r"\s*[" + _EMDASH_CHARS + r"]+\s*")
# 숫자 범위(1–2, 10–20)의 en-dash는 보존 (어제 트림 정책과 일치)
_DIGIT_ENDASH = re.compile(r"(?<=\d)\s*–\s*(?=\d)")


def reduce_emdashes(text: str) -> str:
    """엠대쉬류를 공백 한 칸으로 치환해 줄인다. 숫자 범위 en-dash는 보존.

    컨텍스트(모델이 읽는 과거 출력) 전용. 사용자 입력에는 적용하지 않는다.
    """
    if not text:
        return text
    protected = _DIGIT_ENDASH.sub("\x00", text)
    reduced = _EMDASH_RUN.sub(" ", protected)
    return reduced.replace("\x00", "–")


def count_emdashes(text: str) -> int:
    """텍스트 내 엠대쉬류 문자 총 개수."""
    if not text:
        return 0
    return sum(text.count(c) for c in _EMDASH_CHARS)


def emdash_density_high(text: str, per_chars: int = 5000, limit: int = 10,
                        min_len: int = 500) -> bool:
    """엠대쉬 밀도가 임계(기본 5000자당 10개) 초과면 True.

    짧은 출력은 노이즈 방지로 min_len 미만이면 항상 False.
    조건부 디렉티브 주입 게이트로 사용.
    """
    if not text or len(text) < min_len:
        return False
    return (count_emdashes(text) / len(text)) * per_chars > limit


# =========================================================
# [2026-08-16 상태창 코드 조립] Status Line 처리 — 되읽기 폐지, 머리 strip으로 교체
# =========================================================
# ⚰ parse_status_line_time (2026-05-23~2026-08-16): 모델이 그린 상태줄을 정규식으로 되읽어
#   세계 시간을 전진시키던 함수. 상태창이 코드 조립으로 넘어가면서 입력원이 사라졌다
#   (시간 전진은 배경 추출 world_state.scene_minutes_elapsed → orchestration._advance_scene_time).
#   부활 금지 — 기계 표기를 산문 모델에 위임하던 계약 자체를 접은 것이다.
#
# 아래는 그 반대 방향: 구 세션 관성으로 렌더가 상태줄을 계속 그릴 때 **머리에서만** 걷어낸다.
# (히스토리·검수·리더 입력이 전부 이 정제본을 받으므로 에코 소스도 함께 마른다.)
_HEADER_LINE1_RE = re.compile(r"^\s*위치\s+.*\|\s*시간\s")
_HEADER_DOOM_RE = re.compile(
    r"^\s*(?:활력\s*\d+\s*\|\s*평형\s*\d+\s*\|\s*)?(?:Doom|둠)\s*[:：]?\s*\d+\s*$",
    re.IGNORECASE,
)
_HEADER_CLOCK_RE = re.compile(r"^\s*(?:\[[^\[\]]+?\s+\d+\s*/\s*\d+[^\[\]]*\]\s*)+$")
_HEADER_FENCE_RE = re.compile(r"^\s*(?:```|~~~|---+|═+)\s*$")
_HEADER_SCAN_LINES = 8


def strip_status_header(text: str) -> str:
    """응답 머리에 남은 (구 계약) 상태줄 블록 제거. 못 찾으면 원문 그대로.

    앵커는 1행("위치 … | 시간 …")뿐 — 앵커 없이는 아무것도 지우지 않는다.
    앵커 앞은 빈 줄·펜스만 허용(산문이 먼저 시작했으면 헤더가 아니다).
    """
    if not text or not isinstance(text, str):
        return text
    lines = text.split("\n")
    anchor = -1
    for i in range(min(len(lines), _HEADER_SCAN_LINES)):
        if _HEADER_LINE1_RE.match(lines[i]):
            anchor = i
            break
        if lines[i].strip() and not _HEADER_FENCE_RE.match(lines[i]):
            return text          # 산문이 먼저 = 헤더 없음
    if anchor < 0:
        return text

    end = anchor + 1
    while end < len(lines):
        ln = lines[end]
        if (not ln.strip() or _HEADER_DOOM_RE.match(ln)
                or _HEADER_CLOCK_RE.match(ln) or _HEADER_FENCE_RE.match(ln)):
            end += 1
            continue
        break

    remainder = "\n".join(lines[end:]).strip()
    if not remainder:
        return text              # 헤더가 응답 전부였다면 손대지 않는다(빈 응답 방지)
    return remainder


# detect_scene_type_keywords 제거 (2026-07-06 감사): 인라인 "(scene: gore)" 키워드
# 파서 — 호출자 0. 씬 타입은 !scene 명령(cmd_scene) + Flash DAI scene_type이 담당.


def process_bkspc(text: str) -> str:
    """
    BKSPC 키워드를 처리하여 이전 단어(공백 포함)를 삭제합니다.
    공백과 줄바꿈을 보존하기 위해 정규표현식을 사용합니다.
    """
    if not text or "BKSPC" not in text:
        return text

    # "단어+공백" 패턴을 찾아서 리스트화 (줄바꿈 포함)
    # \S+ : 공백이 아닌 문자들 (단어)
    # \s* : 뒤따르는 공백들
    tokens = re.findall(r'\S+\s*|[\n\r]+', text)
    result = []
    
    for token in tokens:
        if "BKSPC" in token:
            # BKSPC가 포함된 토큰이면 이전 토큰 삭제
            # "BKSPC" 자체만 있는 경우와 "BKSPC "와 같이 공백이 포함된 경우 모두 처리
            if result:
                result.pop()
        else:
            result.append(token)
            
    return "".join(result).strip()


# =========================================================
# PC Impersonation Detection (PC 사칭 감지)
# =========================================================

# =========================================================
# [2026-08-13] 출처(provenance) 판정 — 직조 vs 발명
#
#   왜: Slot 21 DECREE는 "weave each stated action at its point of occurrence"로
#   유저 행동을 산문에 짜 넣으라 지시하는데, 검출기는 그 결과를 사칭으로 보고
#   **문장째 하드 삭제**했다. 지시를 따른 렌더가 벌받는 구조.
#   근본 결함은 동사 목록이 아니라 검출기가 **이번 턴 입력을 못 본다**는 것:
#     유저 "문을 연다"  → GM "당신은 문을 열었다" = 직조(준수)
#     유저 입력에 없음  → GM "당신은 문을 열었다" = 발명(위반)
#   두 문자열이 동일하므로 응답만 봐선 원리적으로 구분 불가.
#
#   판정 방식: 의미 판정("PC다운가")이 아니라 **출처 판정**("입력에 있었나").
#   = Contract-First의 '바닥(ground)' 원칙과 같은 수. LLM 콜 0, 결정적.
#   종성을 버린 (초성,중성) 키로 비교해 활용·불규칙(열다→연다, 돕다→도와)을 흡수한다.
#
#   안전 성질: 매칭 실패 = 현행 동작(삭제) 그대로. 오검출을 줄이기만 하고 늘리지 않는다.
#   thought 타입은 **면제 없음** — PC 내면은 입력에 있든 없든 렌더 대상이 아니다.
# =========================================================

# 조사·어미·기능 음절 (내용 음절에서 제외)
_GRAMMAR_SYLLABLES = set("은는이가을를에의로와과도만부터까지처럼보다"
                         "다했었았였셨고며서면자요네죠는걸것수있없")


def _syllable_keys(text: str) -> set:
    """한글 음절 → (초성, 중성) 키 집합. 종성을 버려 활용/불규칙을 흡수."""
    keys = set()
    for ch in text:
        code = ord(ch) - 0xAC00
        if 0 <= code < 11172:
            keys.add((code // 588, (code % 588) // 28))
    return keys


# PC 지시어 — 내용 음절에서 제외. 남겨두면 분모를 부풀리고, 종성을 버린 키가
# 무관한 음절과 우연히 충돌(당↔다)해 판정이 그 우연에 기댄다.
_PC_REFERENTS = ("플레이어", "당신", "그대", "너")


def _content_syllables(text: str, pc_names: List[str]) -> str:
    """PC 이름·지시어와 기능 음절을 제거한 내용 음절만 남긴다."""
    for pc in pc_names or []:
        if pc and pc != "Unknown":
            text = text.replace(pc, " ")
    for ref in _PC_REFERENTS:
        text = text.replace(ref, " ")
    return "".join(ch for ch in text
                   if 0 <= ord(ch) - 0xAC00 < 11172 and ch not in _GRAMMAR_SYLLABLES)


def _is_supplied_by_input(matched: str, user_input: str, pc_names: List[str],
                          threshold: float = 0.34) -> bool:
    """매칭 문장의 내용이 이번 턴 입력에서 온 것인가(직조) 판정.

    임계는 **느슨하게**(0.34) 잡는다: 유저 행동을 짜 넣으면서 GM이 움직임을 조금 잇는 것은
    정상 서술이지 발명이 아니다. 비용이 비대칭 — 오삭제는 산문을 파괴하고, 오통과는
    경고 하나에 프롬프트 규율(Slot 18)이 그대로 살아 있다. 조이는 쪽으로 튜닝 금지.
    """
    if not user_input:
        return False
    content = _content_syllables(matched, pc_names)
    if len(content) < 2:
        return False  # 판정 재료 부족 → 현행 동작(삭제) 유지
    src = _syllable_keys(user_input)
    hit = sum(1 for k in _syllable_keys(content) if k in src)
    total = len(_syllable_keys(content))
    return total > 0 and (hit / total) >= threshold


def detect_pc_impersonation(response: str, pc_names: List[str],
                            user_input: str = "") -> List[Dict]:
    """
    AI 응답에서 PC 사칭 패턴을 검출합니다.

    V2: 범용 한국어 동사 어미 패턴으로 확장.
    V3 (2026-08-13): user_input 출처 판정 추가 — 유저가 이번 턴에 공급한 행동의
      직조는 위반이 아니다(Slot 21 DECREE 준수). user_input 미전달 시 종전과 동일 동작.
    예외: 따옴표 내 대사, NPC가 PC를 언급하는 경우는 허용.
    """
    violations = []

    # 한국어 과거형 동사 어미 (범용)
    VERB_ENDING = r'(?:했다|었다|았다|였다|셨다|ㅆ다)'
    # 내면/사고 동사 (진짜 사칭)
    THOUGHT_VERBS = r'(?:생각했다|느꼈다|깨달았다|결심했다|기억했다|떠올렸다|추측했다|알았다|몰랐다|원했다|바랐다|후회했다|의심했다|확신했다|짐작했다)'
    # 반사적/불수의 반응 동사 (허용 — PC 선택이 아닌 물리 반응)
    # [2026-08-13] 환자태(세계→PC 벡터) 확장 — 전투 상해·구속·전도 동사가 목록 밖이라
    # "당신의 팔이 부러졌다" 같은 정당한 결과 묘사가 문장째 절단되던 결함 수리.
    # 기준: 세계가 PC 몸에 가한 결과(충격·상해·구속·불수의)=허용 / PC 의지 행동·내면=차단 유지.
    # 능동 동형(던졌다·올렸다·남겼다 등)은 넣지 않는다. 사로잡혔다(감정 피동)는 lookbehind로 제외.
    REFLEXIVE_VERBS = re.compile(
        r'(?:밀렸다|밀려났다|떨렸다|떨려왔다|흔들렸다|굳었다|경직됐다|경직되었다|'
        r'거칠어졌다|빨라졌다|느려졌다|멈췄다|멈추었다|떨어졌다|넘어졌다|쏠렸다|'
        r'미끄러졌다|부딪혔다|부딪쳤다|맞았다|닿았다|스쳤다|꿰뚫렸다|찔렸다|베였다|'
        r'젖었다|얼어붙었다|뜨거워졌다|차가워졌다|흐려졌다|어두워졌다|'
        r'커졌다|작아졌다|조여왔다|풀렸다|터졌다|갈라졌다|'
        r'움찔했다|휘청했다|비틀거렸다|주저앉았다|쓰러졌다|의식이\s*(?:흐려졌다|멀어졌다|끊겼다)|'
        # 전도/넉백
        r'튕겨났다|튕겨나갔다|나가떨어졌다|내동댕이쳐졌다|나뒹굴었다|고꾸라졌다|자빠졌다|'
        r'엎어졌다|처박혔다|곤두박질쳤다|떠밀렸다|밀쳐졌다|내쳐졌다|던져졌다|패대기쳐졌다|'
        # 구속/제압
        r'(?<!사로)잡혔다|붙들렸다|끌려갔다|끌려왔다|짓눌렸다|눌렸다|깔렸다|묶였다|매달렸다|'
        # 상해
        r'부러졌다|꺾였다|찢겼다|찢어졌다|뜯겼다|물렸다|긁혔다|할퀴였다|으스러졌다|뭉개졌다|'
        r'바스러졌다|뚫렸다|박혔다|데였다|데었다|그을렸다|접질렸다|삐끗했다|피를?\s*흘렸다|'
        # 의식/생리
        r'기절했다|실신했다|질식했다|(?:정신|의식)을\s*잃었다|숨이\s*막혔다|목이\s*졸렸다|'
        # 일반 피동 마커 + 피해 한자어
        r'당했다|당하고|당한\s*채|'
        # 조사 4종 — 모음 끝(마비·압도)은 가/를을 취한다. **허용 목록이라 넓히는 방향이 안전**
        # (삭제 목록을 넓히면 오삭제가 는다 — detector_census B 판정 시 이 구분을 지킬 것).
        r'(?:골절|마비|감전|중독|제압|압도|포박|구속|절단|관통)(?:이|가|을|를)?\s*(?:됐다|되었다|당했다))'
    )

    # 1. PC 이름 기반 탐지
    if pc_names:
        for pc in pc_names:
            if not pc or pc == "Unknown":
                continue
            safe_pc = re.escape(pc)

            patterns = [
                # PC 대사 생성
                (rf'{safe_pc}[이가은는]?\s*[""\u201C\u300C].*?[""\u201D\u300D]', 'dialogue'),
                (rf'[""\u201C\u300C].*?[""\u201D\u300D].*?(?:라고|하고|이라며)\s*{safe_pc}', 'dialogue'),

                # PC 내면 묘사
                (rf'{safe_pc}[은는이가]?\s*.{{0,15}}{THOUGHT_VERBS}', 'thought'),
            ]

            for pattern, vtype in patterns:
                for match in re.finditer(pattern, response, re.IGNORECASE):
                    violations.append({
                        'pc': pc,
                        'type': vtype,
                        'matched': match.group()[:60],
                        'start': match.start(),
                        'end': match.end()
                    })

    # 2. 2인칭 지칭 행동 강제 탐지
    second_person_patterns = [
        # 당신/너 + 주격조사 + 동사
        (rf'(?:당신|너|그대|플레이어)[은는이가]\s+.{{1,40}}{VERB_ENDING}', 'impersonation_2nd'),
        # 당신의 신체/감정 + 주격 동사 (NPC→PC 행동 허용)
        (rf'(?:당신|너|그대)(?:의|이)\s*(?:눈|손|몸|기억|생각|가슴|심장|호흡|얼굴|표정|시선|발|다리|팔|목소리|숨결)[이가]\s*.{{1,25}}{VERB_ENDING}', 'impersonation_2nd'),
    ]

    for pattern, vtype in second_person_patterns:
        for match in re.finditer(pattern, response, re.IGNORECASE):
            violations.append({
                'pc': "Player",
                'type': vtype,
                'matched': match.group()[:60],
                'start': match.start(),
                'end': match.end()
            })

    # 3. 따옴표(대사) 예외 필터링
    def is_inside_quotes(text, pos):
        sub = text[:pos]
        # 큰따옴표 (일반 + 유니코드)
        dq = sub.count('"') + sub.count('\u201C') + sub.count('\u201D')
        return dq % 2 == 1

    # 4. 반사적 반응 예외 필터링 (PC 선택이 아닌 물리 반응은 허용)
    def is_reflexive(matched_text):
        return bool(REFLEXIVE_VERBS.search(matched_text))

    filtered_violations = []
    for v in violations:
        if is_inside_quotes(response, v['start']):
            continue  # 따옴표 내 → 허용
        if v['type'] != 'thought' and is_reflexive(v['matched']):
            continue  # 반사적 반응 → 허용 (thought 타입은 예외 없이 차단)
        # 5. 출처 예외 — 유저가 공급한 행동의 직조는 위반이 아니다.
        #    thought는 면제 없음: PC 내면은 공급 여부와 무관하게 렌더 대상이 아니다.
        if v['type'] != 'thought' and _is_supplied_by_input(v['matched'], user_input, pc_names):
            continue
        filtered_violations.append(v)

    return filtered_violations


def _remove_violation_sentences(text: str, violations: List[Dict]) -> str:
    """사칭이 감지된 문장을 텍스트에서 제거합니다."""
    if not violations:
        return text

    # 각 violation이 속한 문장의 범위를 찾음
    remove_ranges = []
    for v in violations:
        start, end = v['start'], v['end']
        # 문장 시작 찾기 (마침표/줄바꿈 뒤)
        sent_start = start
        while sent_start > 0 and text[sent_start - 1] not in '.\n!?。':
            sent_start -= 1
        # 문장 끝 찾기
        sent_end = end
        while sent_end < len(text) and text[sent_end] not in '.\n!?。':
            sent_end += 1
        if sent_end < len(text):
            sent_end += 1  # 구두점 포함
        remove_ranges.append((sent_start, sent_end))

    # 겹치는 범위 병합
    remove_ranges.sort()
    merged = [list(remove_ranges[0])]
    for start, end in remove_ranges[1:]:
        if start <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])

    # 제거 후 텍스트 재조립
    result = []
    last_end = 0
    for start, end in merged:
        chunk = text[last_end:start].strip()
        if chunk:
            result.append(chunk)
        last_end = end
    trailing = text[last_end:].strip()
    if trailing:
        result.append(trailing)

    return ' '.join(result)


def filter_pc_impersonation(response: str, pc_names: List[str],
                            user_input: str = "") -> Tuple[str, List[Dict]]:
    """
    PC 사칭 부분을 검출하고 제거한 최종 텍스트를 반환합니다.

    Returns:
        (cleaned_text, violations): violations는 Dict 리스트 (type, matched, pc 등)
    """
    # 1. BKSPC 먼저 처리
    clean_text = process_bkspc(response)

    # 2. 사칭 검출 (user_input 있으면 출처 판정 적용)
    violations = detect_pc_impersonation(clean_text, pc_names, user_input)

    # 3. 사칭 문장 실제 제거
    if violations:
        for v in violations:
            logger.warning(f"[PC사칭 제거] [{v['type']}] \"{v['matched']}\"")
        clean_text = _remove_violation_sentences(clean_text, violations)

    return clean_text, violations


# =========================================================
# Response Validation (응답 유효성 검증)
# =========================================================

# =========================================================
# Mob Tag Cleaning (System-Level Hiding)
# =========================================================

def clean_mob_tags(text: str) -> str:
    """
    텍스트에서 모브 태그(예: #1A, #9Z)를 제거하여 사용자에게 숨깁니다.
    
    Pattern: `#` followed by exactly 2 alphanumeric characters.
    Avoids removing Markdown headers (# Title).
    """
    if not text:
        return text
        
    # Regex: Match '#' followed by 2 alphanumeric chars, ensuring word boundary or end of string?
    # Case 1: "Patient #1A says" -> "Patient says" (remove " #1A")
    # Case 2: "Patient #1A: Hello" -> "Patient: Hello" (remove " #1A")
    # Case 3: "List #1." -> "List #1." (Keep?) User said random chars.
    # Our tags are roughly 2 chars.
    # Let's match space + # + 2 alphanumeric. `\s#[A-Za-z0-9]{2}\b`?
    
    # We want to remove the tag AND the preceding space if it exists.
    # Pattern: `(\s?)#[a-zA-Z0-9]{2}(?![a-zA-Z0-9])`
    # (?![a-zA-Z0-9]) ensures we don't match #1AB (3 chars).
    
    pattern = r'(\s?)#[a-zA-Z0-9]{2}(?![a-zA-Z0-9])'
    
    def repl(match):
        # If it was "Name #1A", match group 1 is space. We remove both.
        # If it was "#1A" (start of line), match group 1 is empty. We remove tag.
        return "" 
        
    cleaned_text = re.sub(pattern, repl, text)
    return cleaned_text


# =========================================================
# Cliché Pattern Detection
# =========================================================

CLICHE_PATTERNS = [
    (re.compile(r"형언할 수 없는"), "형언할 수 없는"),
    (re.compile(r"전기가 흐르[는듯]"), "전기가 흐르"),
    (re.compile(r"심장이 멎[는은]"), "심장이 멎"),
    (re.compile(r"시간이 멈[춘추]"), "시간이 멈"),
    (re.compile(r"숨을?\s?잊[었은]"), "숨을 잊"),
    (re.compile(r"등줄기를 타고.{0,5}한기"), "등줄기 한기"),
    (re.compile(r"포식자 같은"), "포식자 같은"),
    (re.compile(r"포식자[이가였]"), "포식자 라벨링"),
    (re.compile(r"피식자[이가였]"), "피식자 라벨링"),
    (re.compile(r"공범[이가였]"), "공범 라벨링"),
    (re.compile(r"가해자[이가였]"), "가해자 라벨링"),
    (re.compile(r"피해자[이가였]"), "피해자 라벨링"),
    (re.compile(r"살기가?\s?느껴"), "살기가 느껴"),
    (re.compile(r"보이지 않는 압박"), "보이지 않는 압박"),
    (re.compile(r"모든 것이 달라[질지]"), "모든 것이 달라"),
    (re.compile(r"운명을?\s?결정짓"), "운명을 결정짓"),
    # [마나젬] 음성/톤 직접 설명 금지
    (re.compile(r'(?:건조한|차분한|냉담한|무미건조한|딱딱한)\s*(?:목소리|어조|말투)'), "voice_description"),
    (re.compile(r'(?:날카로운|부드러운|차가운|따뜻한)\s*(?:목소리|어조|음성)'), "voice_description"),
    # [마나젬] 동기없는 소품 상호작용 금지
    (re.compile(r'안경[을를]?\s*(?:고쳐|밀어|만지)'), "unmotivated_prop"),
    (re.compile(r'(?:재떨이|컵|펜)[을를]?\s*(?:만지작|돌리|굴리)'), "unmotivated_prop"),
]


# =========================================================
# P4: Scene-Type Exceptions for Cliché Detection
# =========================================================

SCENE_EXCEPTIONS: Dict[str, set] = {
    "gore": {"voice_description"},     # 해부학 용어 허용, 감정라벨 필터 강화
    "combat": {"unmotivated_prop"},     # 전술 용어 허용, 소품 필터 완화
    "mature": {"voice_description"},    # 신체 묘사 허용
}


def _check_banned_expressions(response: str, scene_type: Optional[str] = None) -> List[str]:
    """BANNED_EXPRESSIONS 딕셔너리 기반 금지어 체크.

    Args:
        response: Current turn response text
        scene_type: Optional scene type for exemptions via SCENE_EXCEPTIONS.

    Returns:
        List of "[category] 'expression'" feedback strings.
    """
    try:
        from text_resources import BANNED_EXPRESSIONS
    except ImportError:
        return []

    exempted: set = SCENE_EXCEPTIONS.get(scene_type, set()) if scene_type else set()
    feedback = []
    for category, expressions in BANNED_EXPRESSIONS.items():
        if category in exempted:
            continue
        for expr in expressions:
            if expr in response:
                feedback.append(f"[{category}] '{expr}'")
    return feedback


def detect_cliche_patterns(response: str, scene_type: Optional[str] = None) -> str:
    """Detect cliché patterns in response and return feedback string.

    Args:
        response: Current turn response text
        scene_type: Optional scene type (e.g. 'gore', 'combat', 'mature').
            Certain cliché labels are exempted per scene type via SCENE_EXCEPTIONS.
    """
    # Determine which labels to skip for this scene type
    exempted: set = SCENE_EXCEPTIONS.get(scene_type, set()) if scene_type else set()

    matched = []
    for pattern, label in CLICHE_PATTERNS:
        if label in exempted:
            continue
        if pattern.search(response):
            matched.append(label)

    # BANNED_EXPRESSIONS dict-based check (complements regex patterns)
    banned_hits = _check_banned_expressions(response, scene_type)
    for hit in banned_hits:
        matched.append(hit)

    if not matched:
        return ""
    # Deduplicate while preserving order
    seen = set()
    deduped = []
    for m in matched:
        if m not in seen:
            seen.add(m)
            deduped.append(m)
    labels = ", ".join(deduped[:3])
    return f"[CLICHE: {labels} — replace with concrete sensory detail]"


# =========================================================
# Cargo Cult Pattern Detection (BABEL Axis IV)
# "Delete the sentence — does the scene lose anything?"
# =========================================================

CARGO_PATTERNS = [
    # 날씨=감정 동기화 (비+슬픔, 어둠+우울, 바람+쓸쓸)
    (re.compile(r'비[가이]?\s*.{0,20}(?:슬[프픔펐]|우울|눈물|울[고었])'), "weather_emotion"),
    (re.compile(r'(?:어두[운움]|잿빛|먹구름).{0,15}(?:마음|기분|심정|우울)'), "weather_emotion"),
    (re.compile(r'(?:햇[빛살]|맑[은게]).{0,15}(?:기분|마음|행복|밝[아은])'), "weather_emotion"),
    (re.compile(r'바람[이이]?\s*.{0,15}(?:쓸쓸|외[로롭]|허전)'), "weather_emotion"),
    # 감정 라벨 직접 서술 (showing이 아닌 telling)
    (re.compile(r'(?:그녀는|그는|그가|그녀가)\s*(?:슬[펐프]|기[뻤쁜]|행복[했한]|두려[웠운]|불안[했한])'), "emotion_label"),
    (re.compile(r'(?:두려움|공포|슬픔|기쁨|분노|혐오)[이가]\s*(?:밀려|엄습|찾아|몰려)'), "emotion_label_wave"),
    # 감정 벽지형 날씨 (마치 ~처럼, ~인 듯, ~을 대변하듯)
    (re.compile(r'(?:마치|인 듯|대변하듯|반영하듯).{0,20}(?:하늘|날씨|비|바람|구름)'), "pathetic_fallacy"),
    # [wave4-E] Pseudo-showing: 결론 라벨을 묘사로 위장 (증거 대신 평결 — ELYSIUM 분석)
    (re.compile(r'(?:감정\s*없는|메마른|공허한|텅\s*빈)\s*(?:목소리|눈[빛동]?|표정)'), "pseudo_showing"),
    (re.compile(r'기계적[으인]\s*(?:로|움직임|동작|말투|반응)'), "pseudo_showing"),
    (re.compile(r'(?:아무런?\s*감정도?\s*(?:없|담기지|드러나지)|무표정[한하]게?\s*(?:말했|대답했|읊))'), "pseudo_showing"),
]

# 문단 내 신체반응 동시 나열 탐지용
_BODY_PARTS_RE = re.compile(r'(?:심장|가슴|호흡|숨|손[이가]|손가락|눈[이가]|눈동자|입술|턱|어깨|등[이가을]|목[이가]|다리|발[이가])')


def detect_cargo_patterns(response: str, scene_npc_count: int = 1) -> str:
    """Detect Cargo Cult patterns in response and return feedback string.

    Cargo Cult: sentences that look like 'good writing' but add nothing structural.
    Test: delete the sentence — does the scene lose anything concrete?

    [2026-06-12] scene_npc_count: 앙상블 보정 — 반응의 주인이 여럿이면 나열이 아니라 **분배**.
    1인 장면 기준 임계(문단당 신체 4종)가 다인 티타임 장면에서 3턴 연속 오탐
    (상시 점등 경고 = 맥락 우선 모델에게 양치기 소년 — 채널 권위 할인). NPC 3+ 장면은 6종으로 완화."""
    matched = []

    # Pattern-based detection
    for pattern, label in CARGO_PATTERNS:
        if pattern.search(response):
            if label not in matched:
                matched.append(label)

    # Paragraph-level: reaction catalog (분배 보정 — 다인 장면은 임계 완화)
    _catalog_threshold = 4 if scene_npc_count < 3 else 6
    paragraphs = response.split('\n\n')
    for para in paragraphs:
        if para.strip():
            body_hits = set(_BODY_PARTS_RE.findall(para))
            if len(body_hits) >= _catalog_threshold and "reaction_catalog" not in matched:
                matched.append("reaction_catalog")

    if not matched:
        return ""
    labels = ", ".join(matched[:3])
    return f"[CARGO: {labels} · cuttable with no loss; only load-bearing sentences stay]"


# =========================================================
# Premature Closure Detection (Anti-Resolution)
# =========================================================

CLOSURE_PATTERNS = [
    (re.compile(r'그렇게.{0,10}(?:끝났다|마무리|끝이\s?났)'), "narrative_closure"),
    (re.compile(r'모든\s?것이.{0,5}(?:해결|끝|풀리)'), "total_resolution"),
    (re.compile(r'다시\s?(?:평온|평화|고요)[이가해]'), "peace_restored"),
    (re.compile(r'새로운.{0,5}시작[이을]'), "fresh_start"),
    (re.compile(r'마침내.{0,10}(?:마무리|끝|해결)'), "finally_resolved"),
    (re.compile(r'한숨\s?돌릴\s?수\s?있'), "relief_marker"),
    (re.compile(r'위기[는가].{0,10}(?:지나|넘기|끝)'), "crisis_over"),
    (re.compile(r'이제.{0,5}(?:괜찮|안전|무사)'), "safety_declaration"),
    (re.compile(r'(?:사건|문제|갈등)[이가은는].{0,10}(?:봉합|종결|해소)'), "conflict_sealed"),
    # [마나젬] 마지막 문단 분위기 마무리
    (re.compile(r'(?:밤|어둠|달빛|바람)[이가].{0,15}(?:감싸|품|내려앉)'), "atmospheric_closing"),
    (re.compile(r'(?:그것은|이것은).{0,10}(?:시작|서막|예고)'), "prophetic_closing"),
]


def detect_premature_closure(response: str, conclusion_proximity: int = 50,
                             open_threads: Optional[List[str]] = None) -> str:
    """Detect premature narrative closure patterns.

    Gate: If conclusion_proximity >= 70 AND no open threads → legitimate ending, skip.
    Otherwise, closure language triggers feedback for next turn.
    """
    # Gate: legitimate conclusion
    if conclusion_proximity >= 70 and not open_threads:
        return ""

    matched = []
    for pattern, label in CLOSURE_PATTERNS:
        if pattern.search(response):
            if label not in matched:
                matched.append(label)

    if not matched:
        return ""

    labels = ", ".join(matched[:3])
    thread_note = ""
    if open_threads:
        thread_note = f" open_threads={len(open_threads)}"
    return (f"[CLOSURE: {labels} · proximity={conclusion_proximity}%{thread_note}. "
            f"the world's default is unresolved; threads the player hasn't closed stay open]")


# =========================================================
# P2: Structural Repetition Detection (구조 반복 감지)
# =========================================================

# Opening type patterns
_OPENING_PATTERNS = [
    ("dialogue", re.compile(r'^\s*["""\u201C\u300C]')),
    ("dialogue", re.compile(r'^\s*.{1,30}:\s*["""\u201C\u300C]')),  # 이름: "대사"
    ("inner_thought", re.compile(r"^\s*[\u2018\u2019'].{0,30}[\u2018\u2019']")),
    ("action", re.compile(r'^\s*(?:\S{1,10}[이가은는]\s+.{0,20}(?:했다|었다|았다|였다|한다|는다|인다))')),
    ("action", re.compile(r'(?:했다|었다|았다|였다|졌다|왔다|갔다|났다|렸다|섰다)[\.\s]*$')),  # 동사 종결
    ("environment", re.compile(r'^\s*(?:하늘|바람|빛|공기|거리|방|복도|숲|바다|밤|낮|아침|저녁|어둠|달|태양|비|눈|안개|연기)')),
    ("description", re.compile(r'^\s*\S')),  # fallback: any non-empty line
]

# Closing type patterns (applied to last paragraph)
_CLOSING_PATTERNS = [
    ("dialogue", re.compile(r'["""\u201D\u300D]\s*$')),
    ("atmosphere", re.compile(r'(?:밤|어둠|달빛|바람|고요|적막|침묵)[이가을를에].{0,20}$')),
    ("cliffhanger", re.compile(r'(?:\.{3}|—|―)\s*$')),
    ("action", re.compile(r'(?:했다|었다|았다|였다)\.\s*$')),
    ("description", re.compile(r'[다]\.?\s*$')),  # fallback
]


def _classify_opening(response: str) -> str:
    """Classify the opening type of a response."""
    first_line = response.strip().split('\n')[0] if response.strip() else ""
    for otype, pattern in _OPENING_PATTERNS:
        if pattern.search(first_line):
            return otype
    return "description"


def _classify_closing(response: str) -> str:
    """Classify the closing type of a response."""
    paragraphs = [p.strip() for p in response.strip().split('\n\n') if p.strip()]
    last_para = paragraphs[-1] if paragraphs else ""
    last_line = last_para.strip().split('\n')[-1] if last_para else ""
    for ctype, pattern in _CLOSING_PATTERNS:
        if pattern.search(last_line):
            return ctype
    return "description"


def detect_structural_repetition(response: str,
                                 recent_openings: Optional[List[str]] = None,
                                 recent_closings: Optional[List[str]] = None
                                 ) -> Tuple[str, str, str]:
    """Detect if response opening/closing structure repeats across turns.

    Opening types: dialogue, action, description, inner_thought, environment
    Closing types: dialogue, action, description, atmosphere, cliffhanger

    Args:
        response: Current turn response text
        recent_openings: List of opening types from recent turns (most recent last)
        recent_closings: List of closing types from recent turns (most recent last)

    Returns:
        (feedback_str, current_opening_type, current_closing_type)
        3 consecutive same type -> warning feedback.
    """
    current_opening = _classify_opening(response)
    current_closing = _classify_closing(response)

    warnings = []

    if recent_openings and len(recent_openings) >= 2:
        if recent_openings[-1] == recent_openings[-2] == current_opening:
            warnings.append(f"opening={current_opening}")

    if recent_closings and len(recent_closings) >= 2:
        if recent_closings[-1] == recent_closings[-2] == current_closing:
            warnings.append(f"closing={current_closing}")

    feedback = ""
    if warnings:
        detail = ", ".join(warnings)
        feedback = (f"[STRUCTURE: {detail} 3 turns running · "
                    f"a different open/close this turn (dialogue↔action↔description↔environment)]")

    return feedback, current_opening, current_closing


# =========================================================
# P3: Tension Dissolution Detection (긴장 해소 감지)
# =========================================================

TENSION_DISSOLUTION_PATTERNS = [
    (re.compile(r'(?:마침내|드디어|결국).{0,15}(?:안도|안심|한숨)'), "premature_relief"),
    (re.compile(r'(?:오해|갈등|긴장)[이가].{0,10}(?:풀리|해소|녹)'), "conflict_evaporation"),
    (re.compile(r'(?:진심|본심)[을를]?\s*(?:털어놓|고백하|밝히).{0,10}(?:받아들|이해하)'), "instant_acceptance"),
]


def detect_tension_dissolution(response: str) -> List[Tuple[str, str]]:
    """Detect premature tension resolution patterns.

    Returns:
        List of (pattern_name, matched_text) tuples.
    """
    results = []
    for pattern, label in TENSION_DISSOLUTION_PATTERNS:
        m = pattern.search(response)
        if m:
            results.append((label, m.group()))
    return results


# =========================================================
# 미완 발화(aborted speech) 클리셰 — "입술이 열렸다/말은 나오지 않았다" 류 (log-only 관측)
# 진짜 신호는 재발(recurrence). 프롬프트 SILENT COMPLIANCE가 실제 교정 담당.
# =========================================================

ABORTED_SPEECH_PATTERNS = [
    ("words_wont_come", re.compile(r'말[은이]?\s*(?:[^.。!?\n]{0,8})?(?:나오지|새어?\s*나오지|터지지|흘러나오지)\s*(?:않|못)')),
    ("lips_parted", re.compile(r'입술[이은]?\s*[^.。!?\n]{0,10}(?:열렸|벌어졌|달싹였?|들썩였?)')),
    ("voice_stuck", re.compile(r'(?:목소리|소리)[가는이]?\s*[^.。!?\n]{0,12}목(?:젖|구멍|울대|구녕)에서\s*(?:멈|막|걸)')),
    ("swallowed_words", re.compile(r'(?:말|소리|목소리)[을를]?\s*삼[켰키]')),
    ("opened_mouth_but", re.compile(r'입(?:술)?[을를]?\s*열었(?:지만|으나|다가|다)[^.。!?\n]{0,12}(?:다물|닫|멈)')),
    ("action_then_negated", re.compile(r'(?:했|졌|었|렸|혔)다\.\s*(?:그러나|하지만|그렇지만)[^.。!?\n]{0,20}(?:않|못)(?:았|었|했)?다')),
]


def detect_aborted_speech(response: str) -> List[Tuple[str, str]]:
    """미완 발화 클리셰 family 검출 (log-only). 입술 열림/말 안나옴/목소리 막힘/삼킨 말 등.
    *재발*이 진짜 문제이므로 caller가 턴별 hit를 롤링윈도우로 추적. Returns [(label, matched_text), ...]."""
    results = []
    if not response:
        return results
    for label, pattern in ABORTED_SPEECH_PATTERNS:
        for m in pattern.finditer(response):
            results.append((label, m.group()[:50]))
    return results


# =========================================================
# P3: NPC Deflection Repetition Detection (회피기법 반복 추적)
# =========================================================

_DEFLECTION_PATTERNS = [
    ("topic_change", re.compile(r'(?:그건\s*그렇고|어쨌든|그나저나|아\s*참|그것보다)')),
    ("humor", re.compile(r'(?:농담[이을]|웃으며|피식|킥킥|하하|장난[을이])')),
    ("silence", re.compile(r'(?:침묵[을이했]|말[을이]?\s*아끼|입[을]?\s*다물|대답.{0,5}않)')),
    ("counter_question", re.compile(r'(?:왜\s*물어|그건\s*왜|오히려\s*내가|되물[었어])')),
    ("denial", re.compile(r'(?:그런\s*거\s*아니|아닌데|무슨\s*소리|그럴\s*리가|말도\s*안\s*돼)')),
]


def detect_deflection_repetition(response: str,
                                 recent_deflections: Optional[List[Dict[str, str]]] = None
                                 ) -> Tuple[str, List[Dict[str, str]]]:
    """NPC deflection technique repetition detection.

    Types: topic_change, humor, silence, counter_question, denial.
    Same NPC + same type within 3 turns -> warning.

    Args:
        response: Current turn response text
        recent_deflections: List of dicts from recent turns,
            each with keys 'npc' (optional) and 'type'.

    Returns:
        (feedback_str, current_deflections)
        - current_deflections: List of dicts with 'type' keys found in this turn.
    """
    current = []
    for dtype, pattern in _DEFLECTION_PATTERNS:
        if pattern.search(response):
            current.append({"type": dtype})

    if not current:
        return "", current

    if not recent_deflections or len(recent_deflections) < 2:
        return "", current

    # Check if any current deflection type appeared in 2 most recent turns
    current_types = {d["type"] for d in current}
    warnings = []
    for dtype in current_types:
        consecutive = 0
        for past in recent_deflections[-2:]:
            if past.get("type") == dtype:
                consecutive += 1
        if consecutive >= 2:
            warnings.append(dtype)

    if not warnings:
        return "", current

    types_str = ", ".join(warnings)
    feedback = (f"[DEFLECTION: {types_str} 3턴 연속 사용 — "
                f"NPC의 회피 수단을 다양화하라 (topic_change↔humor↔silence↔counter_question↔denial)]")
    return feedback, current


# =========================================================
# Sensory Rotation Detection (Cross-Turn Body Part Tracking)
# =========================================================

def detect_sensory_repetition(response: str,
                              recent_body_parts: Optional[List[List[str]]] = None
                              ) -> Tuple[str, List[str]]:
    """Detect repetitive body part usage across turns.

    Args:
        response: Current turn response text
        recent_body_parts: Rolling window of body parts from last N turns
            e.g. [["심장", "눈"], ["심장", "입술"], ...]

    Returns:
        (feedback_str, current_turn_parts)
        - feedback_str: Empty if no repetition detected
        - current_turn_parts: Body parts found in this turn (for rolling window update)
    """
    # Extract body parts from current response
    current_parts = list(set(_BODY_PARTS_RE.findall(response)))

    if not current_parts:
        return "", current_parts

    if not recent_body_parts or len(recent_body_parts) < 2:
        return "", current_parts

    # Find parts repeated across 3 consecutive turns (current + last 2)
    repeated = []
    for part in current_parts:
        consecutive = 0
        for turn_parts in recent_body_parts[-2:]:  # last 2 turns
            if part in turn_parts:
                consecutive += 1
        if consecutive >= 2:  # present in all 3 turns (last 2 + current)
            repeated.append(part)

    if not repeated:
        return "", current_parts

    parts_str = ", ".join(repeated[:3])
    return (f"[ROTATION: {parts_str} 3 turns running · a different body part carries the emotion/state]",
            current_parts)


# =========================================================
# Pidgin Echo Detection (NPC Label → Prose Leak)
# =========================================================

# Dialogue span regex: matches quoted text in various Korean/Unicode quote styles
_DIALOGUE_SPAN_RE = re.compile(r'["""\u201C\u300C].*?["""\u201D\u300D]')


def detect_pidgin_echo(response: str,
                       npc_label_keywords: Optional[Dict[str, List[str]]] = None) -> str:
    """Detect NPC personality label keywords echoed verbatim in narrative prose.

    Pidgin Echo = Level 1 in Decompression scale.
    The label keyword appears as a descriptive adjective in prose (not dialogue).

    Args:
        npc_label_keywords: {npc_name: [keyword1, keyword2, ...]}
    """
    if not npc_label_keywords:
        return ""

    # Build mask of dialogue spans to exclude
    dialogue_spans = []
    for m in _DIALOGUE_SPAN_RE.finditer(response):
        dialogue_spans.append((m.start(), m.end()))

    def is_in_dialogue(pos: int) -> bool:
        for ds, de in dialogue_spans:
            if ds <= pos < de:
                return True
        return False

    matched_echoes = []
    for npc_name, keywords in npc_label_keywords.items():
        for kw in keywords:
            if len(kw) < 2:
                continue
            # Search for keyword in response as descriptive adjective
            # Pattern: keyword + optional suffix + space/particle
            pattern = re.compile(re.escape(kw) + r'[은는한인의]?\s')
            for m in pattern.finditer(response):
                if not is_in_dialogue(m.start()):
                    echo_label = f"{npc_name}:'{kw}'"
                    if echo_label not in matched_echoes:
                        matched_echoes.append(echo_label)

    if not matched_echoes:
        return ""

    echoes_str = ", ".join(matched_echoes[:3])
    return (f"[PIDGIN: {echoes_str} — NPC 라벨 키워드가 서술에 그대로 등장. "
            f"행동적 결과로 해압축하라 (Level 3+)]")


# =========================================================
# A7: HALLABONG Narrative Pattern Detection
# "Gemini 클리셰" — 문법적으로 정상인데 LLM이 과잉 반복하는 기본값.
# soft 경고로 다음 턴 빈도 압력만 건다. 하드 블록 없음.
# =========================================================

# A7-1: ARRIVAL — "Never arrive" (중심 명명 금지)
# attractor/theme을 문장으로 언어화하는 패턴. TELESCOPE [Craft.Attractor]의 강제 규칙.
ARRIVAL_PATTERNS = [
    # "그것은/이것은/이 모든 것은 진정한/참된/본질적인 X"
    (re.compile(r'(?:그것은|이것은|이\s*모든\s*것은)\s*(?:진정한|진짜|참된|본질적[인]|다름\s*아[닌닐])\s*\S'), "essence_naming"),
    # "원했던/바랐던 것은 X였다" — 욕망 명명
    (re.compile(r'(?:원했던|바랐던|갈망했던|찾던)\s*(?:것|바)[은을는]?\s*[^.]{1,20}(?:였다|이었다)'), "desire_naming"),
    # "이 모든 것이 의미하는 바/뜻하는 것"
    (re.compile(r'(?:이\s*모든\s*것|이것)[이은]\s*(?:의미하는|뜻하는|암시하는|시사하는)\s*(?:바|것|까닭)'), "meaning_assignment"),
    # "핵심/본질/정수/진실은 X이다"
    (re.compile(r'(?:핵심|본질|정수|진실)[은이가]\s*[^.]{1,30}(?:에\s*있|[이였]었다|[이]?다)'), "essence_declaration"),
]


def detect_arrival_patterns(response: str) -> str:
    """Detect 'Never arrive' violations — center/theme being named in prose.

    Gemini overuses essence-declaration templates. Soft warn to reduce frequency.
    """
    matched = []
    for pattern, label in ARRIVAL_PATTERNS:
        if pattern.search(response):
            if label not in matched:
                matched.append(label)
    if not matched:
        return ""
    labels = ", ".join(matched[:3])
    return (f"[ARRIVAL: {labels} · attractor stays unnamed (naming kills it into theme); "
            f"it circles in prose, not arriving]")


# A7-2: DECLARATION — 서술자 편집 선언
# PROSE_CRAFT §NARRATOR TRANSPARENCY의 텍스트 금지어를 regex로 강화.
DECLARATION_PATTERNS = [
    # "이것이야말로 / 이것이 바로 X"
    (re.compile(r'이것이(?:야말로|\s*바로)\s*\S'), "this_is_precisely"),
    # "단순한 X가 아니(었다)"
    (re.compile(r'단순한\s*\S{1,15}[이가]\s*아니[었]?[다었]'), "not_merely"),
    # "아이러니하게도 / 다름 아닌"
    (re.compile(r'(?:아이러니하게도|다름\s*아[닌닐])'), "rhetorical_flag"),
    # "(이후) 모든 것을 바꿀/뒤집을/흔들" — 운명론 코멘터리
    (re.compile(r'(?:이후|앞으로)?\s*모든\s*것을?\s*(?:바꿀|뒤집을|흔들)'), "fatalistic_commentary"),
    # A8 (2026-06-25): 잔존 "말했다 공식" — 제스처가 의미를 진술/대신함 (산문8 L17/L21, 산문9 L36/L44). declaration_fb로 자동 피드백.
    # "[제스처/사물]이 말했다/말하고 있었다" — gesture states the meaning
    (re.compile(r'(?:것|사실|진실|움직임|기울기|동작|눈빛|침묵|손길|몸짓|온도|각도|시선|표정|숨|떨림)[이가]\s*(?:말했다|말하고\s*있었다|말한다|속삭였다)'), "gesture_speaks"),
    # "[사실/것]이 ~을 대신하고 있었다" — a fact stands in for the unsaid
    (re.compile(r'(?:사실|것|눈동자|시선|침묵)[이]\s*[^.]{1,40}(?:대신하고\s*있었다|대신했다)'), "stands_in_for"),
    # "[제스처]가 아니었다. 그냥 Y" — negate-then-explain a gesture's meaning
    (re.compile(r'(?:것이|게|동작이|움직임이|움직임은|제스처[가는])\s*아니었다\.\s*그냥'), "not_X_just_Y"),
]


def detect_declaration_patterns(response: str) -> str:
    """Detect narrator editorializing — telling the reader what to feel/think."""
    matched = []
    for pattern, label in DECLARATION_PATTERNS:
        if pattern.search(response):
            if label not in matched:
                matched.append(label)
    if not matched:
        return ""
    labels = ", ".join(matched[:3])
    return (f"[DECLARATION: {labels} · narrator shows rather than declares; "
            f"meaning emerges from action, not assigned]")


# A7-3: EXPLAIN_THEN_RENDER — 예고 후 전달 (redundant pre-announcement)
EXPLAIN_THEN_RENDER_PATTERNS = [
    # "V(-ㄹ) 참/찰나/순간이었다. V했다" — about-to then did
    # 한국어 관형형 "-ㄹ/-을" 종결된 동사 뒤 "참/찰나/순간"
    (re.compile(r'\S\s*(?:참|찰나|순간)[이에]?(?:었다|였다)\.\s*[^.]{1,40}(?:했다|었다|였다)'), "about_to_then_did"),
    # "곧 ... 것이었다. V[가-힣]다" (한국어 과거형 축약 졌다/렸다 등 모두 포함)
    (re.compile(r'곧\s*[^.]{1,20}것(?:이었다|이었)\.\s*[^.]{1,30}[가-힣]다'), "soon_pre_announce"),
    # "막 V하려는 참이었다. V[가-힣]다"
    (re.compile(r'막\s*[^.]{1,15}(?:하려는|려는)\s*참이?었다\.\s*[^.]{1,30}[가-힣]다'), "just_about_to"),
]


def detect_explain_then_render_patterns(response: str) -> str:
    """Detect pre-announce-then-deliver — narrator tells what will happen, then shows it."""
    matched = []
    for pattern, label in EXPLAIN_THEN_RENDER_PATTERNS:
        if pattern.search(response):
            if label not in matched:
                matched.append(label)
    if not matched:
        return ""
    labels = ", ".join(matched[:3])
    return (f"[EXPLAIN→RENDER: {labels} · render directly; "
            f"the event arrives unannounced]")


# A7-4: VENDING — 자판기 응답 (W6 통합)
# 서술자가 "예측 가능함"을 명시하는 순간 그 장면은 자판기.
VENDING_PATTERNS = [
    # "당연하다는 / 예상했 / 역시나 / 으레"
    (re.compile(r'(?:당연하다는|예상[했된]|역시[나]?|으레|으레껏)[\s,.]'), "expected_marker"),
    # "그럴 줄 알았"
    (re.compile(r'그럴\s*줄\s*(?:알았|알고)'), "knew_it"),
    # "여느 때처럼 / 평소처럼 / 늘 그렇듯"
    (re.compile(r'(?:여느\s*때처럼|평소처럼|늘\s*그렇듯)'), "as_usual"),
    # "판에 박힌"
    (re.compile(r'판에\s*박[힌은]'), "boilerplate_signal"),
]


def detect_vending_patterns(response: str) -> str:
    """Detect vending-machine response markers — prose explicitly flags predictability."""
    matched = []
    for pattern, label in VENDING_PATTERNS:
        if pattern.search(response):
            if label not in matched:
                matched.append(label)
    if not matched:
        return ""
    labels = ", ".join(matched[:3])
    return (f"[VENDING: {labels} · naming predictability makes the scene a vending machine; "
            f"a fresh surface, not the default]")


# =========================================================
# L축: 한글 출력 저점 (Korean Floor) — 번역체 표면 결 측정
# =========================================================
# 근거: 영어 텔레스코프(English CoT) → 한국어 렌더의 번역 스텝 + 한국어 학습 prior의
# 번역체 편향(MTL/번역 웹소설)이 겹쳐, (1) 3인칭 대명사 과다(한국어는 주어 생략) +
# (2) 과거-서술 어미(-었/았/였/있었다) 단조로 나타난다. 지능 문제가 아니라 *register
# 디폴트 슬립*이라 soft 신호로 steerable. 출력 텍스트만 판정 → 모델 독립(Floor 보장).
# 임계값은 산문2(31,640자) 캘리브레이션: 그녀 10.3/1k, 과거서술 55%, 있었다 16%.
# soft 경고만 — 하드 블록 아님. 끄고 켜고 임계 조정 가능(읽히는 규칙).

_KO_3P_PRONOUNS = ["그녀", "그는", "그가", "그를", "그의", "그도", "그것", "그들"]


def detect_korean_floor(response: str,
                        pron_per_1k_warn: float = 13.0,
                        past_desc_ratio_warn: float = 0.70,
                        single_ending_warn: float = 0.32,
                        min_chars: int = 200,
                        ) -> Tuple[str, Dict]:
    # 임계 재캘리브레이션 2026-06-16: 골드 레퍼런스(산문2) 위로.
    # 산문2 실측 = pron 10.4/1k · past_desc 0.55 · single 0.21 (전부 구 임계 초과 = 골드를 잡던 상태).
    # 신 임계는 골드보다 *나쁠 때만* 발화 (log-only 관측 신호 품질용).
    """한국어 출력 저점(L축) 측정. 번역체 표면 결 — soft 경고만, 하드 블록 아님.

    측정:
      (1) 3인칭 대명사 밀도(per 1000자, 공백 제외) — 한국어는 주어 생략이 자연.
      (2) '~다.' 종결 중 과거-서술(었/았/였/있었다) 비율 — 어미 단조.
      (3) 단일 어미 지배도 — 같은 꼴 반복(예: 있었다).

    Returns: (feedback_str, stats_dict).  feedback_str 비면 통과.
    """
    text = response or ""
    chars = len(re.sub(r"\s", "", text))
    if chars < min_chars:
        return "", {}

    # (1) 대명사 밀도
    pron_total = sum(len(re.findall(p, text)) for p in _KO_3P_PRONOUNS)
    geunyeo = len(re.findall("그녀", text))
    pron_per_1k = pron_total / chars * 1000

    # (2)(3) '~다.' 종결 어미 분포
    sents = re.findall(r"[가-힣A-Za-z0-9\"'][^.!?\n]*?다\.", text)
    total_da = len(sents) or 1
    ends = {"있었다": 0, "었다": 0, "았다": 0, "였다": 0, "했다": 0, "기타다": 0}
    for s in sents:
        if s.endswith("있었다."):
            ends["있었다"] += 1
        elif s.endswith("었다."):
            ends["었다"] += 1
        elif s.endswith("았다."):
            ends["았다"] += 1
        elif s.endswith("였다."):
            ends["였다"] += 1
        elif s.endswith("했다."):
            ends["했다"] += 1
        else:
            ends["기타다"] += 1
    past_desc = ends["있었다"] + ends["었다"] + ends["았다"] + ends["였다"]
    past_ratio = past_desc / total_da
    top_ending, top_n = max(
        ((k, v) for k, v in ends.items() if k != "기타다"),
        key=lambda x: x[1], default=("", 0),
    )
    single_ratio = top_n / total_da

    stats = {
        "chars": chars,
        "pron_per_1k": round(pron_per_1k, 1),
        "그녀": geunyeo,
        "past_desc_ratio": round(past_ratio, 2),
        "top_ending": top_ending,
        "single_ending_ratio": round(single_ratio, 2),
    }

    # feedback = 모델용 가이드(수치 없음). raw 수치는 stats dict로 로그에만.
    flags = []
    if pron_per_1k > pron_per_1k_warn:
        flags.append("3인칭 대명사(그녀 등)가 잦다 → 주어 생략으로")
    if past_ratio > past_desc_ratio_warn:
        flags.append("과거서술 어미가 단조롭다 → 현재·명사형·연결 어미로 변주")
    if single_ratio > single_ending_warn and top_ending:
        flags.append(f"'{top_ending}' 어미가 반복된다 → 꼴 바꾸기")

    feedback = ("[L:한글저점] " + " | ".join(flags)) if flags else ""
    return feedback, stats


# =========================================================
# 숫자·계측 집착 (Number Fixation) — deepseek 렌더 성향 백스톱, log-only
# =========================================================
# deepseek 의 계측/바이탈 나열 성향(BPM·Hz·mm·골전도 류)을 씬 불문 관측한다.
# 프롬프트 측 교정은 PROSE_CRAFT "Felt quantity over numbers"(전역) + MATURE 인식론 제약(친밀).
# 서사적 숫자(나이·가격·층·시각·인원)는 안 잡도록 *단위/바이탈/율속 시그니처*만 타깃.
_NUMBER_FIXATION_PATTERNS = [
    (re.compile(r"\d+(?:\.\d+)?\s*(?:bpm|Hz|㎐|dB|데시벨|cm|mm|밀리미터|센티미터|kg|킬로그램|%|퍼센트|℃|헤르츠)", re.I), "unit"),
    (re.compile(r"분당\s*\d+|\d+\s*회\s*/?\s*(?:분|초)|\d+\s*번\s*(?:뛰|박동)"), "rate"),
    (re.compile(r"(?:심박|맥박|혈압|체온|호흡수|주파수|진동수)\D{0,8}\d"), "vital"),
    (re.compile(r"\d+\.\d+\s*(?:초|도|배|밀리)"), "decimal_measure"),
]


def detect_number_fixation(response: str, min_chars: int = 150) -> Tuple[str, Dict]:
    """계측/바이탈 숫자 집착(deepseek 성향) 관측 — soft 신호만, 하드 블록 아님.

    단위·바이탈·율속 시그니처만 잡아 서사적 숫자(나이/가격/시각/인원) 오탐을 피한다.
    2건 이상부터 발화(1건은 서사적/우연 가능). Returns: (feedback, stats). 비면 통과.
    """
    text = response or ""
    if len(re.sub(r"\s", "", text)) < min_chars:
        return "", {}
    hits: Dict[str, int] = {}
    samples: List[str] = []
    for rx, label in _NUMBER_FIXATION_PATTERNS:
        n = 0
        for m in rx.finditer(text):
            n += 1
            if len(samples) < 5:
                samples.append(m.group(0).strip())
        if n:
            hits[label] = n
    total = sum(hits.values())
    stats = {"total": total, "by_kind": hits, "samples": samples}
    if total >= 2:
        return ("[Num:계측집착] 측정치·단위·바이탈 수치가 산문에 누적 → 느껴진 크기로(계기 언어 회피)", stats)
    return "", {}


# =========================================================
# I축: 닫음-운율 재정착 (Cadence Echo) — verbatim 후렴 백스톱
# =========================================================
# 근거: calm의 그늘(comfort-groove) — 안전한 닫음 의식을 매 턴 verbatim 재소비.
# 기존 [Craft.Rhythm/Scheme]은 shape/type만 봐서 phrase 표면 재발을 못 잡음(로그 확증:
# Rhythm 19·Scheme 17 발화에도 후렴 7×/6×/4× 샘). 위협 프레이밍("반복 금지")은 desperate
# 재점화라 금지 — soft 신호만. *문장 단위* 비교로 motif/referent(은색 캔 재등장) 허용,
# *verbatim 문장*만 타깃(motif vs phrase 구분의 코드 구현).

_SENT_SPLIT = re.compile(r'(?<=[.!?”"])\s+|\n+')


# 상태줄/시스템 라인 — verbatim 비교에서 제외(매 턴 동일해 오탐 유발)
_STATUS_NOISE_RE = re.compile(r"로드아웃|\bDoom\b|활력\s*\d|평형\s*\d|위치 .+\| ?시간|파티\s*챗")


def _sentences(text: str, min_chars: int = 6) -> List[str]:
    """문장 분절 — 공백제외 min_chars 이상만. (짧은 후렴 '열리지 않은 약속'=7자도 포함)
    상태줄/시스템 라인은 제외 — 매 턴 동일하므로 cadence echo 오탐원."""
    parts = [s.strip() for s in _SENT_SPLIT.split(text or "") if s.strip()]
    return [s for s in parts
            if len(re.sub(r"\s", "", s)) >= min_chars and not _STATUS_NOISE_RE.search(s)]


def detect_cadence_echo(response: str,
                        recent_sents: Optional[List[str]] = None,
                        min_chars: int = 6,
                        near: float = 0.90,
                        ) -> Tuple[str, List[str], List[str]]:
    """I축: 응답 문장이 이전 턴들과 verbatim/near-verbatim 재발하는지 감지. soft 경고만.

    *문장 단위* 비교라 같은 referent(은색 캔)가 *다른 문장*에 재등장하는 건 통과(ratio<near),
    같은 *문장 템플릿*('열리지 않은 약속.', '유우의 찻잔이 받침에 닿았다.')이 재발하면 플래그.
    위협 아님 — comfort-groove 재정착의 *관찰*(산문2 스모크: 25턴 59건, 캔 모티프 오탐 0).

    Returns: (feedback_str, current_sents, echo_hits).
      current = rolling window에 append용(caller가 cap).
      echo_hits = 재발한 문장 원문 리스트 — **스크럽 대상**(카드2: 넛지로 말리는 대신 모방 대상에서 제거).
    """
    import difflib
    recent = recent_sents or []
    cur = _sentences(response, min_chars)
    hits = []
    seen = set()
    for s in cur:
        if s in seen:
            continue
        for prev in recent:
            if difflib.SequenceMatcher(None, s, prev).ratio() >= near:
                hits.append(s)
                seen.add(s)
                break
    feedback = ""
    if hits:
        shown = "; ".join(f'"{h[:24]}…"' for h in hits[:3])
        # [2026-07-27] 영어화 + **변주 허용 명시**. 종전 한글 문안은 렌더 프롬으로 주입되며(style_fb)
        #   미러링 위험 + render-facing 언어 일관성 위반이었다. 그리고 골격 변주가 의미를 진행시키는
        #   경우(예: "잡을 것을 찾는 손" → "아무것도 잡지 못한 손")는 기법이므로, 억제 대상은
        #   **문장이 통째로 돌아오는 경우**로 좁힌다(레티어스 07-27: "너무 팍팍하게 잡나").
        feedback = (f"[I:재정착] {len(hits)} sentence(s) returned whole from earlier turns: {shown} "
                    f"A motif may return, and a variation that carries it forward is craft; "
                    f"when the sentence itself comes back intact, give it a fresh surface.")
    return feedback, cur, hits


# =========================================================
# [2026-07-22 카드2] 반복 문장 스크럽 — 모방 대상 제거
# =========================================================
# 원리: verbatim 재발에 넛지는 이미 배선돼 있는데도 재발이 계속됐다(산문연구1 10문장).
# 넛지는 1턴 지연 피드백인데 미러링 원천(히스토리 전문·S31 꼬리)은 매 턴 실시간 재공급 —
# "반복하지 마"라고 말하면서 반복할 실례를 계속 보여주는 구조였다.
# → 말리는 대신 **모방 대상에서 그 문장을 뺀다**. 계보: 엠대쉬 미러 트림(히스토리 엠대쉬 삭감),
#   07-08 루프차단기(저며진 꼬리→대사 앵커 교체). 주입본만 큐레이팅, 저장본·플레이어 노출본 무손상.
# 원칙 정합: "같은 문장이 또 나왔다"는 기계 판정 가능한 형식 사실 → 넛지=형식만 원칙 통과.

def scrub_echo_sentences(text: str,
                         echo_sents: Optional[List[str]] = None,
                         near: float = 0.90,
                         min_keep_ratio: float = 0.5,
                         already_seen: Optional[set] = None) -> Tuple[str, int]:
    """주입본에서 재발 문장을 제거. Returns: (scrubbed_text, removed_count).

    **첫 등장 보존(already_seen 전달 시)**: 지시대상(모티프)이 오직 그 반복 문장으로만 언급된
    경우 전량 삭제하면 주입본에서 대상 자체가 사라진다 → 히스토리를 오래된 순으로 돌며
    첫 인스턴스는 남기고 *이후 재발분만* 제거한다. 검출기 원칙("모티프는 재등장 OK,
    문장을 새로")과 동형. already_seen=None이면 전량 제거(단발 텍스트용, 예: S31 꼬리 —
    echo 목록에 오른 문장은 이미 앞에서 최소 1회 등장했으므로 첫 인스턴스가 아니다).

    안전판: 제거 후 본문이 원본의 min_keep_ratio 미만으로 쪼그라들면 **원본을 그대로 반환**
    (통짜 삭제 방지 — 루프차단기의 '대사 0이면 raw 유지'와 같은 자세).
    """
    if not text or not echo_sents:
        return text, 0
    import difflib
    targets = [s.strip() for s in echo_sents if s and s.strip()]
    if not targets:
        return text, 0
    kept: List[str] = []
    removed = 0
    for sent in _SENT_SPLIT.split(text):
        s = sent.strip()
        if not s:
            continue
        matched: Optional[str] = None
        for t in targets:
            if s == t or difflib.SequenceMatcher(None, s, t).ratio() >= near:
                matched = t
                break
        if matched is None:
            kept.append(s)
            continue
        if already_seen is not None and matched not in already_seen:
            already_seen.add(matched)   # 첫 등장 = 지시대상 보존
            kept.append(s)
            continue
        removed += 1
    if not removed:
        return text, 0
    out = " ".join(kept).strip()
    if not out or len(re.sub(r"\s", "", out)) < len(re.sub(r"\s", "", text)) * min_keep_ratio:
        return text, 0  # 과다 삭제 → 원본 유지
    return out, removed


# =========================================================
# [2026-07-08 루프-차단기] 저미기(영어-구조 전사) 검출 + 대사-앵커 추출
# =========================================================
# Slot 31(Last_Response_Tail)이 원자화된 산문을 고recency 재주입 → 자기-미러링 루프(새 세션=클린 실측,
# 파티쳇수정/session_summary_2026-07-08.md §3). 차단기 = 리라이트가 아니라 **주입 교체**: 꼬리가 저며져
# 있으면 raw 산문 대신 대사 라인만 앵커로 주입(사건·목소리 연속성 유지, 문체 모방 실례 제거).
# 원리 = "앵무새는 못 막는다 → 모방 대상을 큐레이팅한다". 외부 선례 = risu_agents.js injectAgentNotes
# (생성 최근접엔 항상 구조화 텍스트 — agent_plugins_5way_mapping.md §6-②).
# 축 = 연결어미 밀도(실측: 저밈 ~0.11 / 건강 ~0.5) — 어미 단조(detect_korean_floor)와 별개 축.
# 임계는 보수적(골드 정지-씬 오탐 방지: 함구-소녀 샘플이 통과하도록 2분기 설계). 튜닝은 [slice-metrics] 로그로.

_CONNECTIVE_RE = re.compile(
    r'[가-힣](?:다가|지만|는데|면서|니까|므로|려고|어도|거나|든지|며|아서|어서|여서|해서)(?=[,\s])'
    r'|[가-힣]고(?=[,\s])(?!\s*(?:있|싶))'  # -고 연결어미. 단 '-고 있다/싶다'(상 표지)는 절-연쇄가 아니라 제외
)
_SENT_SPLIT_RE = re.compile(r'(?<=[.!?…])\s+')
_NEG_ECHO_RE = re.compile(r'(?:지 않|수 없|아니었|않았)')
_DIALOGUE_RE = re.compile(r'“[^”]*”|"[^"]*"|「[^」]*」|『[^』]*』')


def analyze_slicing_structure(text: str) -> Dict:
    """저미기 구조 계측. flagged 2분기(원자화 / 부정-포화형), 공통 게이트 = 문장수·초단문 평균."""
    sents = [s.strip() for s in _SENT_SPLIT_RE.split(text or '') if s.strip()]
    n = len(sents)
    if n == 0:
        return {"sentences": 0, "conn_density": 1.0, "avg_len": 0.0, "neg_ratio": 0.0, "flagged": False}
    conn = len(_CONNECTIVE_RE.findall(text))
    neg = sum(1 for s in sents if _NEG_ECHO_RE.search(s))
    avg_len = sum(len(s) for s in sents) / n
    conn_density = conn / n
    neg_ratio = neg / n
    flagged = (n >= 6 and avg_len < 19.0 and (
        conn_density < 0.10                              # 분기1: 순수 원자화 (절-연쇄 거의 0)
        or (conn_density < 0.18 and neg_ratio >= 0.25)   # 분기2: 부정-포화 저밈 (순환부정/반향형)
    ))
    return {"sentences": n, "conn_density": round(conn_density, 3),
            "avg_len": round(avg_len, 1), "neg_ratio": round(neg_ratio, 2), "flagged": flagged}


def extract_dialogue_anchor(text: str, cap: int = 500) -> str:
    """직전 응답에서 대사 라인만 추출(뒤에서부터 cap자) — 저며진 꼬리의 대체 앵커.
    대사 = 사건·목소리를 나르는 건강 채널(저미기는 지문에서 발생, 전 샘플 실측)."""
    lines = _DIALOGUE_RE.findall(text or '')
    if not lines:
        return ""
    out: List[str] = []
    total = 0
    for ln in reversed(lines):
        if total + len(ln) > cap and out:
            break
        out.insert(0, ln)
        total += len(ln)
    return "\n".join(out)


# =========================================================
# Style Feedback 합류 — 같은 처방을 가진 검출을 한 줄로
# [2026-08-03 합류점 감사]
#
# 배경: 검출기 13종의 결과가 orchestration 8.5에서 `" ".join(filter(None, [...]))`로
# 무순위·무캡 연결됐다. 공급자 층은 완전하다(13종 전부 `if not matched: return ""` 침묵
# 경로 보유) — 문제는 합류에만 있었다.
#
# ★고친 건 "몇 개를 버릴까"가 아니라 **중복 제거**다. 세어 보니 두 무리가
#   **검출 대상은 다 다른데 처방이 거의 같았다**:
#     ARRIVAL "attractor stays unnamed … not arriving"
#     DECLARATION "narrator shows rather than declares; meaning emerges from action"
#     EXPLAIN→RENDER "render directly; the event arrives unannounced"
#     VENDING "naming predictability … a fresh surface, not the default"
#       → 넷 다 "이름 붙이지 말고 렌더해라". 동시 점등 시 같은 지시를 네 번(399자).
#     ROTATION/STRUCTURE/DEFLECTION → 셋 다 "3턴 연속 → 다양화하라"(310자).
#
# 처방을 1회로 묶되 **라벨은 전부 보존**한다(뭐가 걸렸는지는 그대로 전달).
# 13블록 → 8블록, 고정분 1061자 → 약 550자. 등급 판단이 아니라 중복 제거라
# 관측을 기다릴 이유가 없다([[feedback_counterfactual_no_observation_gate]] 아님 —
# 이건 반사실형이 아니라 순수 중복).
#
# 가족에 속하지 않는 문자열은 **파싱하지 않고 원본 그대로 통과**시킨다.
# (TENSION·I:재정착처럼 태그 문법이 다른 것들이 있어 일괄 파싱은 위험.)
# 가족 줄의 위치 = 그 가족 **첫 멤버가 있던 자리** → 기존 순서 감각 보존.
# =========================================================

# 태그 → (가족, 축 이름). 축 이름은 병합 줄에서 뭐가 걸렸는지 구분하는 라벨.
_STYLE_FAMILY_MEMBERS = {
    "ARRIVAL": ("TELLING", "arrival"),
    "DECLARATION": ("TELLING", "declaration"),
    "EXPLAIN→RENDER": ("TELLING", "explain-first"),
    "VENDING": ("TELLING", "vending"),
    "ROTATION": ("REPETITION", "body-part"),
    "STRUCTURE": ("REPETITION", "open/close"),
    "DEFLECTION": ("REPETITION", "deflection"),
}

# 가족 → 처방 1회. {labels}에 축별 검출 내용이 들어간다.
_STYLE_FAMILY_TEMPLATE = {
    "TELLING": "[TELLING: {labels} · name nothing; the meaning arrives through action, not assignment]",
    "REPETITION": "[REPETITION: {labels} · 3 turns running; vary each axis this turn]",
}

# 멤버 문자열에서 라벨만 남기려고 떼는 꼬리 — 가족 줄이 한 번만 말하므로 중복.
_STYLE_LABEL_TAIL = re.compile(r'\s*(?:3\s*turns\s*running|3턴\s*연속\s*사용|3턴\s*연속)\s*$')


def _style_family_of(fb: str) -> Optional[Tuple[str, str]]:
    """피드백 문자열의 선두 태그로 가족을 판정. 비가족이면 None."""
    if not fb.startswith("["):
        return None
    head = fb[1:fb.find(":")] if ":" in fb[:32] else ""
    return _STYLE_FAMILY_MEMBERS.get(head.strip())


def _style_labels_of(fb: str) -> str:
    """`[TAG: labels · 처방]` / `[TAG: labels — 처방]`에서 labels만."""
    body = fb[fb.find(":") + 1:]
    for sep in ("·", "—"):
        idx = body.find(sep)
        if idx >= 0:
            body = body[:idx]
    return _STYLE_LABEL_TAIL.sub("", body.strip()).strip(" .")


def merge_style_feedback(parts: List[str]) -> str:
    """검출 결과 리스트 → 가족 처방을 1회로 묶은 합류 문자열.

    검출 수는 그대로, 처방만 중복 제거. 비가족은 원본 통과.
    """
    out: List[Optional[str]] = []
    slot: Dict[str, int] = {}
    items: Dict[str, List[str]] = {}

    for fb in parts:
        if not fb:
            continue
        fam = _style_family_of(fb)
        if fam is None:
            out.append(fb)
            continue
        family, axis = fam
        if family not in items:
            items[family] = []
            slot[family] = len(out)
            out.append(None)  # 자리 예약 — 첫 멤버 위치를 유지
        _lab = _style_labels_of(fb)
        items[family].append(f"{axis}={_lab}" if _lab else axis)

    for family, labs in items.items():
        out[slot[family]] = _STYLE_FAMILY_TEMPLATE[family].format(labels="; ".join(labs))

    return " ".join(x for x in out if x)


def style_feedback_tags(merged: str) -> str:
    """로그용 태그 요약. 전문은 verbose 로거로, journal엔 이것만.

    [2026-08-03] 종전 FormatCheck 로그는 앞 80자만 남겨서, **뒤쪽 검출기가 터져도
    흔적이 없었다** — "뭐가 자주 터지나"에 대한 감각이 실제 빈도가 아니라 join 순서로
    만들어지고 있었다는 뜻이다. 태그만 남기면 길이 걱정 없이 전량이 보이고,
    등급이 추측 대신 계측이 된다.
    (⚠구 코드 리터럴을 여기 적지 말 것 — 절단 지점 전수 스캔에 오탐으로 잡힌다.)
    """
    seen: List[str] = []
    text = merged or ""
    for m in re.finditer(r'\[', text):
        s = m.end()
        _c, _b = text.find(":", s), text.find("]", s)
        ends = [x for x in (_c, _b) if x >= 0]
        if not ends:
            continue
        tag = text[s:min(ends)].strip()
        # `[I:재정착]`처럼 콜론 앞이 한 글자인 접두 태그는 `]`까지 통째로 쓴다.
        if len(tag) < 3 and _b > s:
            tag = text[s:_b].strip()
        if tag and len(tag) <= 24 and tag not in seen:
            seen.append(tag)
    return f"{', '.join(seen)} ({len(seen)})" if seen else ""


# =========================================================
# [2026-08-02] 형용사 나열 검출 — 시트 tone 필드 직역 관측
# =========================================================
# 실관측: "말을 거는 톤이었다. 임상적이고, 따뜻하고, 사무적이었다."
#   원인은 NPC 시트 `tone` 필드가 **한국어 묘사문**(형용사 나열)이고, Slot 33 recency 헤더가
#   "match these speech patterns"라 그 형용사를 그대로 서술하게 만든 것. 둘 다 수리했으나
#   **기존 DB의 tone 값은 여전히 형용사 나열**이라 관측이 필요하다.
#
# ★리터럴 목록으로 안 잡는다. `BANNED_EXPRESSIONS["voice_tone"]`이 이미 있지만
#   영어 위주(dryness/measured/businesslike)이고 한국어는 3개뿐이라, 실관측 문장의
#   "임상적/따뜻한"은 하나도 안 걸렸다. 형용사는 무한하므로 **구조**로 잡는다:
#   `A이고, B하고, C이었다` = 한 문장 안 3연 이상 나열.
#
# ⚠log-only. 임계가 정당한 산문도 잡는다(대비 나열은 기법이다) —
#   [[feedback_detection_not_writing]]: 검출→사람이 로그 판독→사람이 프롬 튜닝.
#   두 형태를 다 잡는다 — 초판은 (a)만 잡고 (b)를 놓쳤다:
#     (a) 서술형 종결   "임상적이고, 따뜻하고, 사무적이었다"
#     (b) 관형형 + 명사 "차갑고 날카롭고 건조한 목소리였다"
_ADJ_STACK = re.compile(
    r"(?:[가-힣]{1,}(?:적이|스럽|롭|하)?(?:고|며|면서),?\s+){2,}"
    r"[가-힣]{2,}(?:"
    r"(?:적이|스럽|롭|하)?(?:었|았|였|다|음)"          # (a) 종결
    r"|(?:운|은|는|한|린|긴|픈)?\s*[가-힣]{2,}"          # (b) 관형형 → 명사
    r")"
)
# 톤/목소리를 **명명**하는 서술 — 대사가 아니라 대사에 대한 보고
# [2026-08-13] 조사 `는`/`가` 누락 수리 — 모음으로 끝나는 명사(목소리·어조·말투)는
#   주격/주제격이 가/는이라 **한 번도 매칭된 적이 없다**(자음 끝 톤·음성만 걸렸음).
#   가장 흔한 셋이 통째로 미검출 → 톤 명명 관측이 계속 과소계상 중이었다.
_TONE_REPORT = re.compile(r"(?:톤|어조|말투|목소리|음성)(?:이|가|은|는|였|이었|으로)")


def detect_adjective_stacking(response: str) -> Tuple[str, int]:
    """형용사 3연 이상 나열 + 톤 명명 서술을 센다. 반환=(로그 문자열, 건수).

    처방을 만들지 않는다 — 관측만. 프롬프트 수정 전후 빈도를 비교하는 게 용도다.
    """
    if not response:
        return "", 0
    hits = []
    for raw in response.split("\n"):
        line = raw.strip()
        if len(line) < 12:
            continue
        for sent in re.split(r"(?<=[.!?。])\s+|(?<=다\.)\s*", line):
            sent = sent.strip()
            if len(sent) < 12:
                continue
            _stack = bool(_ADJ_STACK.search(sent))
            _named = bool(_TONE_REPORT.search(sent))
            # [2026-08-13 오기 수리] 구 `_stack or (_named and _stack)`은 항등식(뒤 절이 앞에 흡수)이라
            #   톤 명명 **단독은 한 번도 안 세졌다** — 위 _TONE_REPORT 조사 수리 주석("관측 과소계상")이
            #   세는 의도를 증언하므로 오기로 판정(레티어스도 기억 없음 → 코드 고고학 판정).
            #   라벨 3분화로 판독 분리. log-only 불변 — 처방 미합류.
            if _stack or _named:
                _kind = "stack+named" if (_stack and _named) else ("stack" if _stack else "named")
                hits.append((_kind, sent[:70]))
    if not hits:
        return "", 0
    _log = "[AdjStack] %d건 — %s" % (
        len(hits), " | ".join(f"({k}) {s}" for k, s in hits[:3])
    )
    return _log, len(hits)
