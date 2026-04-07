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
# Scene Type Detection (씬 타입 감지)
# =========================================================

def detect_scene_type_keywords(text: str) -> Optional[str]:
    """
    텍스트에서 씬 타입 전환 키워드를 감지합니다.

    사용자가 특정 명령어를 입력하면 씬 타입이 변경됩니다.

    Args:
        text: 입력 텍스트

    Returns:
        감지된 씬 타입 또는 None
        - 'gore': 고어 모드
        - 'nsfw': NSFW 모드
        - 'gore_nsfw': 고어+NSFW 모드
        - 'normal': 일반 모드
    """
    # Scene type transition patterns
    patterns = {
        # Gore mode entry
        'gore': [
            r'\(scene:\s*gore\)',
            r'\[gore\s*mode\]',
        ],
        # NSFW mode entry
        'nsfw': [
            r'\(scene:\s*nsfw\)',
            r'\[nsfw\s*mode\]',
        ],
        # Gore+NSFW mode entry
        'gore_nsfw': [
            r'\(scene:\s*gore\+nsfw\)',
            r'\[gore\+nsfw\s*mode\]',
            r'\[all\s*mode\]',
        ],
        # Normal mode return
        'normal': [
            r'\(scene:\s*normal\)',
            r'\[normal\s*mode\]',
            r'\(scene\s*end\)',
            r'\[scene\s*end\]',
        ],
    }

    text_lower = text.lower()

    for scene_type, pattern_list in patterns.items():
        for pattern in pattern_list:
            if re.search(pattern, text_lower):
                return scene_type

    return None


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

def detect_pc_impersonation(response: str, pc_names: List[str]) -> List[Dict]:
    """
    AI 응답에서 PC 사칭 패턴을 검출합니다.

    V2: 범용 한국어 동사 어미 패턴으로 확장.
    예외: 따옴표 내 대사, NPC가 PC를 언급하는 경우는 허용.
    """
    violations = []

    # 한국어 과거형 동사 어미 (범용)
    VERB_ENDING = r'(?:했다|었다|았다|였다|셨다|ㅆ다)'
    # 내면/사고 동사 (진짜 사칭)
    THOUGHT_VERBS = r'(?:생각했다|느꼈다|깨달았다|결심했다|기억했다|떠올렸다|추측했다|알았다|몰랐다|원했다|바랐다|후회했다|의심했다|확신했다|짐작했다)'
    # 반사적/불수의 반응 동사 (허용 — PC 선택이 아닌 물리 반응)
    REFLEXIVE_VERBS = re.compile(
        r'(?:밀렸다|밀려났다|떨렸다|떨려왔다|흔들렸다|굳었다|경직됐다|경직되었다|'
        r'거칠어졌다|빨라졌다|느려졌다|멈췄다|멈추었다|떨어졌다|넘어졌다|쏠렸다|'
        r'미끄러졌다|부딪혔다|부딪쳤다|맞았다|닿았다|스쳤다|꿰뚫렸다|찔렸다|베였다|'
        r'젖었다|얼어붙었다|뜨거워졌다|차가워졌다|흐려졌다|어두워졌다|'
        r'커졌다|작아졌다|조여왔다|풀렸다|터졌다|갈라졌다|'
        r'움찔했다|휘청했다|비틀거렸다|주저앉았다|쓰러졌다|의식이\s*(?:흐려졌다|멀어졌다|끊겼다))'
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


def filter_pc_impersonation(response: str, pc_names: List[str]) -> Tuple[str, List[Dict]]:
    """
    PC 사칭 부분을 검출하고 제거한 최종 텍스트를 반환합니다.

    Returns:
        (cleaned_text, violations): violations는 Dict 리스트 (type, matched, pc 등)
    """
    # 1. BKSPC 먼저 처리
    clean_text = process_bkspc(response)

    # 2. 사칭 검출
    violations = detect_pc_impersonation(clean_text, pc_names)

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
]

# 문단 내 신체반응 동시 나열 탐지용
_BODY_PARTS_RE = re.compile(r'(?:심장|가슴|호흡|숨|손[이가]|손가락|눈[이가]|눈동자|입술|턱|어깨|등[이가을]|목[이가]|다리|발[이가])')


def detect_cargo_patterns(response: str) -> str:
    """Detect Cargo Cult patterns in response and return feedback string.

    Cargo Cult: sentences that look like 'good writing' but add nothing structural.
    Test: delete the sentence — does the scene lose anything concrete?
    """
    matched = []

    # Pattern-based detection
    for pattern, label in CARGO_PATTERNS:
        if pattern.search(response):
            if label not in matched:
                matched.append(label)

    # Paragraph-level: reaction catalog (4+ distinct body parts in one paragraph)
    paragraphs = response.split('\n\n')
    for para in paragraphs:
        if para.strip():
            body_hits = set(_BODY_PARTS_RE.findall(para))
            if len(body_hits) >= 4 and "reaction_catalog" not in matched:
                matched.append("reaction_catalog")

    if not matched:
        return ""
    labels = ", ".join(matched[:3])
    return f"[CARGO: {labels} — 삭제해도 장면이 잃는 게 없는 문장. 구조적 기능이 있는 문장만 유지하라]"


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
    return (f"[CLOSURE: {labels} — proximity={conclusion_proximity}%{thread_note}. "
            f"세계의 기본 상태는 미해결이다. 유저가 직접 해결하지 않은 스레드를 닫지 마라]")


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
        feedback = (f"[STRUCTURE: {detail} 3턴 연속 반복 — "
                    f"다른 구조로 시작/종결하라 (dialogue↔action↔description↔environment)]")

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
    return (f"[ROTATION: {parts_str} 3턴 연속 — 다른 신체 부위로 감정/상태를 표현하라]",
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
