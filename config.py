"""
Lorekeeper TRPG Bot - Configuration Module
Consolidates all constants and configuration settings.
"""

import os
from dotenv import load_dotenv
from google.genai import types

# Load Environment Variables
load_dotenv()

# =========================================================
# System Constants
# =========================================================
VERSION = "3.2"

# =========================================================
# API Keys & Models
# =========================================================
DISCORD_TOKEN = os.getenv('DISCORD_TOKEN')
GEMINI_API_KEY = os.getenv('GEMINI_API_KEY')

MODEL_ID_PRO = "gemini-3-pro-preview"
MODEL_ID_FLASH = "gemini-3-flash-preview"
MODEL_ID = MODEL_ID_PRO

# =========================================================
# File & Data Paths
# =========================================================
DATA_DIR = "data"
SESSIONS_DIR = os.path.join(DATA_DIR, "sessions")
LORE_DIR = os.path.join(DATA_DIR, "lores")
RULES_DIR = os.path.join(DATA_DIR, "rules")

# =========================================================
# Discord & Input Limits
# =========================================================
MAX_DISCORD_MESSAGE_LENGTH = 2000
MAX_FILE_SIZE_MB = 10
MAX_FILE_SIZE_BYTES = MAX_FILE_SIZE_MB * 1024 * 1024
MAX_TEXT_INPUT_LENGTH = 50000
SUPPORTED_TEXT_EXTENSIONS = ['.txt', '.md', '.json', '.log', '.py', '.yaml', '.yml']

# =========================================================
# Domain & Session Defaults
# =========================================================
# Memory/History Limits
MAX_HISTORY_LENGTH = 80             # Max history items stored in domain_data (sync with fermentation)
MAX_DESC_LENGTH = 50                # Summary description length
RECENT_HISTORY_FOR_ANALYSIS = 20    # Number of recent messages sent to Left Brain Analysis

# Fermentation (Memory) Settings

FERMENT_CHUNK_SIZE = 25             # Chunk size for fermentation
FERMENTED_THRESHOLD = 8             # Max fermented summaries before Deep Memory compression

# UI/Display
NPC_PREVIEW_LIMIT = 5               # Max NPCs shown in preview

# =========================================================
# World Manager Constants
# =========================================================
# Domain & Session Defaults
DEFAULT_TIME_SLOTS = ["새벽", "오전", "오후", "황혼", "저녁", "심야"]
DEFAULT_WEATHER_TYPES = ["맑음", "흐림", "비", "안개", "돌풍"]
DEFAULT_LORE = "기본 세계관: 어두운 도시, 수수께끼의 사건들..."


# Time Settings
TICK_DURATION_MIN = 5  # Minutes
TICK_DURATION_MAX = 10 # Minutes

TIME_TICKS_PER_SLOT = 24  # 4 hours / 10 mins = 24 ticks

# Doom Thresholds
DOOM_THRESHOLD_WARNING = 30
DOOM_THRESHOLD_DANGER = 70
DOOM_THRESHOLD_CRITICAL = 90
DOOM_MAX = 100

# Doom Increase Rates
DOOM_INCREASE_NIGHT = 1
DOOM_INCREASE_NEMESIS_MIN = 1
DOOM_INCREASE_NEMESIS_MAX = 2
DOOM_INCREASE_HIGH_RISK = 3
DOOM_INCREASE_MEDIUM_RISK = 2
DOOM_INCREASE_LORE_RULE = 1

NEMESIS_THRESHOLD = -10

# Doom Dice Modifier
DOOM_DICE_BASELINE = 50
DOOM_DICE_MODIFIER_STEP = 5

# Memory Optimization
FRESH_THRESHOLD = 70  # Increased from 50 (V2 Restoration)

# =========================================================
# Quest Manager Constants
# =========================================================
MAX_RETRY_COUNT = 3
RETRY_DELAY_SECONDS = 1
MAX_ARCHIVE_DISPLAY = 3
MAX_HISTORY_FOR_CHRONICLE = 50
EMPTY_QUEST_MEMO_MSG = "No active quests or memos."


# =========================================================
# Default Contents
# =========================================================


DEFAULT_RULES = """
[Lorekeeper 기본 룰: 서사 중심 TRPG]

## 📜 판정 시스템 (1d100)
**성공 기준치 (DC: Difficulty Check)**
- **매우 쉬움 (Trivial):** DC 0
- **쉬움 (Easy):** DC 20
- **보통 (Normal):** DC 40 (표준)
- **어려움 (Hard):** DC 60
- **매우 어려움 (Extreme):** DC 80

**판정 결과 계산**
- `[주사위 1d100] + [특성/상태 보정치] >= [DC]` 이면 성공
- **대성공 (Critical Success):** 주사위 **96~100** & 최종값 >= DC
- **대실패 (Critical Failure):** 주사위 **1~5** (보정치와 상관없이 실패)

**기본 원칙: 서사적 판정 우선**
- 플레이어 요청 시 `!r` 명령어로 주사위를 굴릴 수 있습니다.
- AI는 주사위 없이도 캐릭터의 패시브, 상황적 유불리를 종합하여 판정할 수 있습니다.

## 🎭 캐릭터 성장
성장은 경험치나 레벨이 아닌 **서사적 성취**를 통해 이루어집니다:
- **패시브**: 반복된 행동이나 경험을 통해 습득하는 특성
  예) "독 내성" - 독에 여러 번 노출된 후 획득
  예) "야간 시야" - 어둠 속에서 오래 활동한 후 획득
- **칭호**: 특별한 업적이나 인정을 통해 얻는 명예
  예) "드래곤 슬레이어" - 드래곤을 처치한 후 획득
  예) "숲의 친구" - 엘프들에게 인정받은 후 획득

## 🌓 비일상 적응
초자연적/비일상적 존재나 현상에 반복 노출되면 점차 익숙해집니다:
- 처음: 공포, 혼란, 패닉
- 적응 중: 경계하지만 대처 가능
- 일상화: 담담하게 받아들임
AI가 캐릭터의 노출 횟수와 반응을 추적하여 자연스럽게 적응도를 부여합니다.

## ⚔️ 전투
- 선제권: 상황과 캐릭터 특성에 따라 판단
- 성공/실패: 캐릭터 능력, 패시브, 상황을 종합하여 서사적으로 결정
- 피해: 서사적으로 묘사 (HP 수치 없음)
- 상태이상: 부상, 중독, 공포 등이 행동에 영향

## 💰 소지품
- 화폐: 세계관에 맞는 단위 사용 (골드, 은화, 크레딧 등)
- 인벤토리: 소지품 목록
- 거래: 협상과 상황에 따라 가격 변동
- AI가 세계관에 맞게 화폐 단위를 자동 판단

## 📝 특수 규칙
- OOC 수정: `(OOC: 요청)` 형식으로 캐릭터 정보 수정 가능
- 정보 확인: `!정보`로 캐릭터 상태 조회
"""

DEFAULT_WORLD_STATE = {
    "time_slot": "오후",
    "weather": "맑음",
    "day": 1,
    "doom": 30, # [Unsettled Start] User Request
    "doom_name": "위기",
    "risk_level": "None",
    "current_location": "Unknown",
    "location_rules": {},
    "world_constraints": {},
    "active_threads": [],
    "last_temporal_context": {}
}

# =========================================================
# Simulation Constants (Restored)
# =========================================================

# Anomaly / Doom Equilibrium Settings
ANOMALY_TRIGGER_CHANCE_MULTIPLIER = 1.0 # Doom * 1.0 = % Chance
ANOMALY_DOOM_COST = -5 # Tension Release when Anomaly occurs

STATUS_EFFECTS = {
    # Debuffs (Severity 1-3)
    "부상": {"type": "debuff", "severity": 1},
    "중상": {"type": "debuff", "severity": 2},
    "치명상": {"type": "debuff", "severity": 3},
    "중독": {"type": "debuff", "severity": 1},
    "맹독": {"type": "debuff", "severity": 2},
    "화상": {"type": "debuff", "severity": 1},
    "동상": {"type": "debuff", "severity": 1},
    "공포": {"type": "debuff", "severity": 1},
    "패닉": {"type": "debuff", "severity": 2},
    "실명": {"type": "debuff", "severity": 2},
    "골절": {"type": "debuff", "severity": 2},
    "출혈": {"type": "debuff", "severity": 1},
    "탈진": {"type": "debuff", "severity": 1},
    "기절": {"type": "debuff", "severity": 3},
    "혼란": {"type": "debuff", "severity": 1},
    
    # Buffs
    "활력": {"type": "buff", "severity": 0},
    "방어태세": {"type": "buff", "severity": 0},
    "집중": {"type": "buff", "severity": 0},
    "은신": {"type": "buff", "severity": 0},
    "비행": {"type": "buff", "severity": 0},
    "야간시야": {"type": "buff", "severity": 0},
    "가호": {"type": "buff", "severity": 0},
    "신속": {"type": "buff", "severity": 0},
}

NEGATIVE_STATUS_EFFECTS = {k: v["severity"] for k, v in STATUS_EFFECTS.items() if v["type"] == "debuff"}
POSITIVE_STATUS_EFFECTS = {k: 1 for k, v in STATUS_EFFECTS.items() if v["type"] == "buff"}

SEVERITY_DOOM_IMPACT = {
    1: 1, # Minor -> +1 Doom
    2: 3, # Major -> +3 Doom
    3: 5, # Critical -> +5 Doom
}

# Normality Stages (Range: 0-100)
# Key: (min_inclusive, max_exclusive)
NORMALITY_STAGES = {
    (0, 10): {"stage": 0, "name": "초기", "reaction_hint": "Shock, Fear, Confusion"},
    (10, 30): {"stage": 1, "name": "접촉", "reaction_hint": "Wariness, Unease"},
    (30, 60): {"stage": 2, "name": "적응", "reaction_hint": "Acceptance, Coping"},
    (60, 90): {"stage": 3, "name": "익숙함", "reaction_hint": "Familiarity, Routine"},
    (90, 101): {"stage": 4, "name": "일상화", "reaction_hint": "Indifference, Mastery"},
}

def get_normality_stage_info(val):
    try:
        val = int(val)
    except (ValueError, TypeError):
        val = 0
        
    for (l, h), info in NORMALITY_STAGES.items():
        if l <= val < h: return info
    return NORMALITY_STAGES[(90, 101)]

# =========================================================
# Safety Settings
# =========================================================
SAFETY_SETTINGS = [
    types.SafetySetting(
        category="HARM_CATEGORY_HARASSMENT",
        threshold="BLOCK_NONE",
    ),
    types.SafetySetting(
        category="HARM_CATEGORY_HATE_SPEECH",
        threshold="BLOCK_NONE",
    ),
    types.SafetySetting(
        category="HARM_CATEGORY_SEXUALLY_EXPLICIT",
        threshold="BLOCK_NONE",
    ),
    types.SafetySetting(
        category="HARM_CATEGORY_DANGEROUS_CONTENT",
        threshold="BLOCK_NONE",
    ),
    types.SafetySetting(
        category="HARM_CATEGORY_CIVIC_INTEGRITY",
        threshold="BLOCK_NONE",
    ),
]
