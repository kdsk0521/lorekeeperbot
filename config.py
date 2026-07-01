"""
Lorekeeper TRPG Bot - Configuration Module
Consolidates all constants and configuration settings.
"""

import os
import contextlib
import contextvars
from typing import Dict, Any, Union
from dotenv import load_dotenv
from google.genai import types

# Load Environment Variables
load_dotenv()

# =========================================================
# System Constants
# =========================================================
VERSION = "8.0"

# =========================================================
# API Keys & Models
# =========================================================
DISCORD_TOKEN = os.getenv('DISCORD_TOKEN')
GEMINI_API_KEY = os.getenv('GEMINI_API_KEY')

MODEL_ID_PRO = "gemini-3.1-pro-preview"
MODEL_ID_FLASH = "gemini-3-flash-preview"
MODEL_ID = MODEL_ID_PRO

# Renderer Backend: "gemini" or "openai" (OpenAI-compatible; 현 운영=Ollama Cloud)
RENDERER_BACKEND = os.getenv("RENDERER_BACKEND", "openai").lower()

# OpenAI-compatible renderer — Ollama Cloud (https://ollama.com/v1, 모델명 :cloud 접미사)
OPENAI_API_KEY = os.getenv("OPENAI_RENDERER_API_KEY", "")  # ollama.com/settings/keys 키 env로
OPENAI_BASE_URL = os.getenv("OPENAI_RENDERER_BASE_URL", "https://ollama.com/v1")
OPENAI_MODEL_ID = os.getenv("OPENAI_RENDERER_MODEL", "deepseek-v4-pro:cloud")
# OpenAI-compatible generation parameters (top_k 미지원 → frequency/presence_penalty로 보정)
OPENAI_TEMPERATURE = float(os.getenv("OPENAI_TEMPERATURE", "0.8"))  # 추론 ON 시 0.7~0.8 권장(추론이 탐색 담당 → 출력은 일관성)
OPENAI_TOP_P = float(os.getenv("OPENAI_TOP_P", "0.80"))
OPENAI_FREQUENCY_PENALTY = float(os.getenv("OPENAI_FREQUENCY_PENALTY", "0.3"))
OPENAI_TOP_K = int(os.getenv("OPENAI_TOP_K", "40"))
OPENAI_REASONING_EFFORT = os.getenv("OPENAI_REASONING_EFFORT", "high")  # none / low / medium / high — Ollama /v1 reasoning_effort로 전송(thinking 모델만 발동)
OPENAI_PRESENCE_PENALTY = float(os.getenv("OPENAI_PRESENCE_PENALTY", "0.1"))

# =========================================================
# Analysis Backend (좌뇌/Flash) — Gemini vs OpenAI 호환(wellspring DeepSeek)
# "gemini"(기본, 현행 genai.Client) 또는 "openai"(analysis_backend.GenaiCompatClient).
# Gemini 키가 죽으면 "openai"로 전환 → Flash 분석을 deepseek-v4-flash로 라우팅.
# =========================================================
ANALYSIS_BACKEND = os.getenv("ANALYSIS_BACKEND", "gemini").lower()
# 미지정 시 렌더러(OPENAI_*) 설정 재사용 — Ollama Cloud면 키/URL 자동 공유(상속)
ANALYSIS_OPENAI_API_KEY = os.getenv("ANALYSIS_OPENAI_API_KEY", OPENAI_API_KEY)
ANALYSIS_OPENAI_BASE_URL = os.getenv("ANALYSIS_OPENAI_BASE_URL", OPENAI_BASE_URL)
ANALYSIS_OPENAI_MODEL_FLASH = os.getenv("ANALYSIS_OPENAI_MODEL_FLASH", "deepseek-v4-flash:cloud")
ANALYSIS_OPENAI_MODEL_PRO = os.getenv("ANALYSIS_OPENAI_MODEL_PRO", "deepseek-v4-pro:cloud")
# 임베딩 = Voyage AI (Ollama Cloud는 임베딩 서빙 X → 전용 엔드포인트/키 분리). 200M 토큰 무료(voyage-4 계열).
# ⚠ ZDR은 Voyage 대시보드에서 opt-out 필요(기본 학습 ON). 차원 기본 1024. 공급자 바꿨으니 기존 벡터 1회 재임베딩 필요.
ANALYSIS_OPENAI_EMBED_BASE_URL = os.getenv("ANALYSIS_OPENAI_EMBED_BASE_URL", "https://api.voyageai.com/v1")
ANALYSIS_OPENAI_EMBED_API_KEY = os.getenv("ANALYSIS_OPENAI_EMBED_API_KEY", "")  # Voyage 키 env로
ANALYSIS_OPENAI_EMBED_MODEL = os.getenv("ANALYSIS_OPENAI_EMBED_MODEL", "voyage-4")
# 분석 = JSON 스키마 채우기(결정적 추출). extended thinking 불필요 → 기본 off(출력/지연 절감).
# DAI 품질 부족 관측 시 .env 에서 "low"/"medium" 으로 상향.
ANALYSIS_OPENAI_REASONING_EFFORT = os.getenv("ANALYSIS_OPENAI_REASONING_EFFORT", "none")

# ── 1회성 무거운 분석만 추론 ON ──────────────────────────────────────────────
# 로어 적재(analyze_lore_unified) / 캐릭터 시트(analyze_character_sheet)는 제약-만족
# 분류라 추론 한 패스의 ROI가 크고, 캠페인당 드물게 돌아 지연 부담이 없다. per-turn DAI 는
# 위의 기본값(none / ANALYSIS_TEMPERATURE) 유지. 호출별 격리는 async-safe contextvar 로.
# 효과는 ANALYSIS_BACKEND="openai"(DeepSeek 등 wellspring) 경로에서 적용된다.
ANALYSIS_OPENAI_REASONING_EFFORT_HEAVY = os.getenv("ANALYSIS_OPENAI_REASONING_EFFORT_HEAVY", "low")
ANALYSIS_TEMPERATURE_HEAVY = float(os.getenv("ANALYSIS_TEMPERATURE_HEAVY", "0.05"))
# 1회성 콜이 라우팅될 추론-가능 모델. Flash(ANALYSIS_OPENAI_MODEL_FLASH)가 V3.2 같은 비추론
# 모델이면 effort 격상이 무의미하므로, heavy 컨텍스트에선 이 모델로 강제 라우팅한다.
# 기본은 이미 떠 있는 PRO(예: deepseek-v4-pro) 재사용 — 새 모델 안 띄움.
ANALYSIS_OPENAI_MODEL_HEAVY = os.getenv("ANALYSIS_OPENAI_MODEL_HEAVY", ANALYSIS_OPENAI_MODEL_PRO)
ANALYSIS_HEAVY_EFFORT_VAR = contextvars.ContextVar("analysis_heavy_effort", default=False)

# ── Reasoning tier (semantic; reasoning_policy.py 가 모델별 값/파라미터로 매핑) ──────────
# .env 는 키 + 모델 ID 만. "어느 role 이 어느 tier" 는 여기 코드 기본값(off/light/deep).
# 모델을 GLM(high/max only) · deepseek(none/high/max) · 기타로 교체해도 policy 가 흡수한다.
# (위의 OPENAI_REASONING_EFFORT / ANALYSIS_OPENAI_REASONING_EFFORT* 는 레거시 — 이제 미사용.)
RENDERER_REASONING_TIER = os.getenv("RENDERER_REASONING_TIER", "off")               # 메인 렌더 = OFF (TELESCOPE 프롬프트-CoT 로 추론 대체)
ANALYSIS_REASONING_TIER = os.getenv("ANALYSIS_REASONING_TIER", "light")             # 보조 per-turn 분석 = LIGHT (추론 ON)
ANALYSIS_REASONING_TIER_HEAVY = os.getenv("ANALYSIS_REASONING_TIER_HEAVY", "deep")  # 1회성 무거운 추출 = DEEP


@contextlib.contextmanager
def heavy_analysis():
    """이 블록 안에서 도는 분석 콜만 reasoning_effort 를 HEAVY 로 격상한다(1회성 추출 전용).

    contextvar 라 async 태스크 단위로 격리되며 await 를 건너 전파된다. 블록을 벗어나면
    자동으로 원복되므로 per-turn 경로에는 절대 새지 않는다.
    """
    _token = ANALYSIS_HEAVY_EFFORT_VAR.set(True)
    try:
        yield
    finally:
        ANALYSIS_HEAVY_EFFORT_VAR.reset(_token)

# Generation Parameters - Analysis (Flash/Left Brain)
ANALYSIS_TEMPERATURE = 0.1
ANALYSIS_TOP_K = 20
ANALYSIS_TOP_P = 0.8
# [Gemini 3] presence_penalty/frequency_penalty not supported - removed

# Generation Parameters - Narrative (Pro/Right Brain)
NARRATIVE_TEMPERATURE = 1.15  # 1.4 → 1.15 (감정 과장 어휘 fan-out 축소)
NARRATIVE_TOP_K = 60          # 70 → 60 (wild 후보 trim)
NARRATIVE_TOP_P = 0.80
NARRATIVE_MAX_OUTPUT_TOKENS = 12288
# 서사 출력 길이: 인원당 동적 조절 (텔레스코프 제거 후 기준)
NARRATIVE_CHARS_BASE = 2200      # 기본 (1인 이하) [2026-06-11: 1500→2200, 산문 목표 2000자대 (1인 max 3000/floor 1800)]
NARRATIVE_CHARS_PER_PLAYER = 800 # 참여 인원당 추가


def get_narrative_char_limit(player_count: int = 1) -> int:
    """참여 인원 기반 서사 출력 최대 글자수. 1인=2300, 2인=3100, 3인=3900..."""
    return NARRATIVE_CHARS_BASE + NARRATIVE_CHARS_PER_PLAYER * max(1, player_count)
# [Gemini 3] presence_penalty/frequency_penalty not supported - removed

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
MAX_TEXT_INPUT_LENGTH = 100000  # [V4] Doubled from 50k for detailed lore
SUPPORTED_TEXT_EXTENSIONS = ['.txt', '.md', '.json', '.log', '.py', '.yaml', '.yml']

# =========================================================
# Domain & Session Defaults
# =========================================================
# Memory/History Limits
MAX_HISTORY_LENGTH = 2000             # [Anti-Gravity] Expanded History (80 -> 2000)
MAX_DESC_LENGTH = 50                # Summary description length
RECENT_HISTORY_FOR_ANALYSIS = 30    # Number of recent messages sent to Left Brain Analysis
FRAME_HISTORY_DEPTH = 10            # Scene Continuity rolling window size

# Fermentation (Memory) Settings

FERMENT_CHUNK_SIZE = 12             # [Cost-Diet] Chunk size matches render window (6턴=12msg)
FERMENTED_THRESHOLD = 8             # Max fermented summaries before Deep Memory compression

# UI/Display
NPC_PREVIEW_LIMIT = 5               # Max NPCs shown in preview

# =========================================================
# World Manager Constants
# =========================================================
# Domain & Session Defaults
DEFAULT_TIME_SLOTS = ["새벽", "오전", "오후", "황혼", "저녁", "심야"]

# 시간 슬롯 → 시각 범위 매핑 (hour)
TIME_SLOT_HOURS = {
    "새벽": (4, 6),     # 04:00 ~ 06:59
    "오전": (7, 11),    # 07:00 ~ 11:59
    "오후": (12, 16),   # 12:00 ~ 16:59
    "황혼": (17, 18),   # 17:00 ~ 18:59
    "저녁": (19, 22),   # 19:00 ~ 22:59
    "심야": (23, 3),    # 23:00 ~ 03:59 (wrap)
}
DEFAULT_WEATHER_TYPES = ["맑음", "흐림", "비", "안개", "돌풍"]
DEFAULT_LORE = "기본 세계관: 어두운 도시, 수수께끼의 사건들..."


# Calendar Rules (V8.5 단순 게임 캘린더 — 2026-05-23)
# 1년 = 12달, 1달 = 30일, 1주 = 7일. 윤년/달별 일수 차이 없음. fantasy/추가룰 도입 시 변경 가능.
CALENDAR_DAYS_PER_MONTH = 30
CALENDAR_MONTHS_PER_YEAR = 12
CALENDAR_DAYS_PER_YEAR = CALENDAR_DAYS_PER_MONTH * CALENDAR_MONTHS_PER_YEAR  # 360
CALENDAR_DAYS_PER_WEEK = 7  # 발효 시스템 연동용 (3단계)


# Time Settings
TICK_DURATION_MIN = 1   # Minutes (Micro-pacing enabled)
TICK_DURATION_MAX = 5   # Minutes

TIME_TICKS_PER_SLOT = 240  # 4 hours / 1 min = 240 ticks

# Time Flow v5 — 장면별 시간 규칙. 1틱=2분. 렌더러에 시간 감각 전달용.
SCENE_TIME_RULES = {
    "normal":   {"base_ticks": 1,  "max_ticks": 2},    # 최대 4분
    "combat":   {"base_ticks": 0,  "max_ticks": 1},    # 최대 2분
    "social":   {"base_ticks": 1,  "max_ticks": 1},    # 최대 2분
    "intimate": {"base_ticks": 0,  "max_ticks": 1},    # 최대 2분, 기본 정지
    "travel":   {"base_ticks": 10, "max_ticks": 60},   # 최대 120분
    "summary":  {"base_ticks": 0,  "max_ticks": 999},
}

# Doom Max
DOOM_MAX = 100

# (DOOM_INCREASE_* / DOOM_DICE_BASELINE 제거 2026-06-25 — 옛 파멸-모델 잔재, 0소비)
NEMESIS_THRESHOLD = -10

# =========================================================
# Doom Chapter Volume — Phase × Lens × Scene 결합
# =========================================================
# 페이즈 boundary가 lens별로 다름 (climax_threshold 따라). 起承轉結間 4+1단.
# raw doom delta는 doom_module.process() 내부 자동 변동에만 multiplier 적용.
# game_world.change_doom (OOC `!둠`, quest 보상)은 직접 amount 반영 (사용자 의도 보존).

DOOM_RAW_GAIN_BASE = 2.0  # turn당 raw doom gain base (energy tension_factor 변조). 3.0→2.0 (0626): base=floor 트리클로 낮춤, 완성(시계/퀘스트)이 주 상승원. 아크 4~6챕터/300+턴 타깃
# energy_direction → per-turn base doom gain 변조 (활성도 게이트, 2026-06-25 배선). calm 거의정지 / 긴장 상승.
DOOM_TENSION_FACTOR = {
    "detonation": 1.5,
    "rising":     1.0,
    "aftershock": 0.5,
    "idle":       0.04,   # 0.15→0.04 (2026-06-26): calm 둠 ~0.12/턴(3.0×0.04)으로 늦춤 — 소수누적 천천히. base/rising 유지라 tense는 300-타깃 보존, calm씬만 길어짐(300 넘어도 OK)
    "stagnant":   0.03,   # stagnant<idle 순서 유지 위해 비례 하향 (deadlock도 calm-low)
}
CHAPTER_INTERMISSION_DECAY = 3  # 間 페이즈 자연 감쇠 (-3/턴)
CHAPTER_RESET_FLOOR = 10  # 새 챕터 시작 floor (climax 후 doom 10 도달 시 起 진입)
INTIMATE_LENS_GROUP = {"romance", "comedy", "noir", "drama"}  # manual climax (spike 발화)

# Scene type doom modifier
SCENE_DOOM_MODIFIER = {
    "combat":      1.5,
    "exploration": 1.2,
    "normal":      1.0,
    "social":      1.0,
    "intimate":    0.5,
    "rest":        0.3,
    "summary":     0.0,
}

# Doom v3 — Situation Clocks
DOOM_CLIMAX_THRESHOLD = 95  # Default climax threshold (intense). intimate는 lens별 가변.
CLOCK_COMPLETE_DOOM = {4: 2, 6: 3, 8: 4}  # 완성(이벤트 발화)→상승. 10/15/20→2/3/4 (0626): 시계는 양념, 완성=활동 beat. 수식(phase×lens) 통과
CLOCK_RESOLVE_DOOM = {4: 0, 6: 0, 8: 0}  # 해결=NEUTRAL (0626): doom=활동 모델선 해결도 능동 beat지 relief 아님. fall은 間(챕터휴식)만. (옛 −5/−10/−15 = 위협-미터 잔재, cut)

# Clock Defense Rewards (→ primary vigor/composure axis)
CLOCK_MITIGATE_REWARD = 1           # 시계 1칸 감소당 활력/평형 +1
CLOCK_RESOLVE_REWARD = {4: 2, 6: 3, 8: 5}  # 시계 해결 시 segments별 보상

# (JUDGMENT_DOOM_DELTA / DOOM_DICE_MODIFIER_STEP 제거 2026-06-25 — 옛 파멸-모델 잔재(0소비). 판정→doom 폐기, JUDGMENT_CONSEQUENCES가 대체.)

# Judgment Consequences v4 — 결과별 기계적 세계 변경
# primary_delta: 주축(vigor/composure) 직접 효과
# momentum: 다음 턴 판정 보너스/페널티 (±10 cap)
# clock_effect: 활성 시계 변경 (+전진/-후퇴), clock_all: 모든 시계 대상
# (2026-06-25: doom_delta 키 제거 — 판정→doom 폐기. 둠=이야기 활성도라 판정 성패와 무관. primary/momentum/clock만.)
JUDGMENT_CONSEQUENCES = {
    "critical_success": {
        "primary_delta": 5,
        "momentum": 10,
        "clock_effect": -1,
    },
    "success": {
        "primary_delta": 0,
        "momentum": 0,
        "clock_effect": 0,
    },
    "partial": {
        "primary_delta": -2,
        "momentum": 0,
        "clock_effect": 0,
    },
    "failure": {
        "primary_delta": -3,
        "momentum": -5,
        "clock_effect": 1,
    },
    "critical_failure": {
        "primary_delta": -5,
        "momentum": -10,
        "clock_effect": 1,
        "clock_all": True,
    },
}

# Judgment v3 (incremental)
ASPECT_VALUE = 5
MOD_SOURCE_CAPS = {
    "mental": 20,
    "doom": 20,
    "theory": 20,
    "passive": 20,
    "aspect": 20,
    "dai": 20,
    "status": 20,
}

# Memory Optimization
FRESH_THRESHOLD = 24  # [Cost-Diet] 24개 초과 시 발효 (렌더 12 + 여유 12)

# =========================================================
# V7 Core Systems: Mental, Doom, Abnormal
# =========================================================
# Storyteller (World Initiative) Constants
STORYTELLER_QUEUE_MAX = 5
STORYTELLER_DIVERSITY_WINDOW = 5
STORYTELLER_STARVATION_TURNS = 3
# event_queue 만료: queued_turn으로부터 N턴 이상 묵힌 일반 이벤트는 폐기.
# clock_completion 타입은 제외 — 기계적 약속이라 만료 없음.
# 이미 시간이 지난 anomaly가 뒤늦게 발사되는 것을 방지.
EVENT_QUEUE_EXPIRY_TURNS = 8

# Active Condition (Storyteller v4.1 — Fate Aspect + Location)
ACTIVE_CONDITION_CAP = 3

# Judgment condition modifier (polarity × intensity → ±mod)
CONDITION_MOD_SCALE = {"Low": 2, "Mid": 4, "High": 7, "Extreme": 12}
CONDITION_MOD_CAP = 15

# 기력 Stages (0-100) — PC의 총체적 컨디션 (체력/집중/평판/정신)
# Key: Stage ID (0-3)
MENTAL_STAGES = {
    0: {"name": "충만", "emoji": "😌", "range": (70, 101), "desc": "신체와 의지가 충실한 상태"},
    1: {"name": "둔화", "emoji": "😰", "range": (40, 70),  "desc": "출력이 느려지고 체력이 흔들립니다"},
    2: {"name": "고갈", "emoji": "😱", "range": (15, 40),  "desc": "한계에 가깝습니다. 판단과 행동이 둔해집니다"},
    3: {"name": "붕괴", "emoji": "🫥", "range": (0, 15),   "desc": "신체와 의지가 한계를 넘겼습니다. (트라우마 위험)"}
}

# DOOM_STAGES 제거 — LENS_DOOM_PHASE_RANGES + LENS_DOOM_ATMOSPHERE로 대체 (line 698~)

# Doom Stage (0-5) -> Mental Recovery Multiplier
DOOM_MENTAL_RECOVERY_MOD = {
    0: 1.0, 1: 0.9, 2: 0.8, 3: 0.6, 4: 0.4, 5: 0.2
}

# =========================================================
# Aspects 부활 (시스템 교차 결합 신호)
# 자세히: 파티쳇수정/vigor_composure_design_brief.md (관련 메모)
# Arc 사이클 시 백업, 부활 사이클로 V3 재이식.
# 내부 라벨 + 산문 typological palette (Arc 패턴 D).
# =========================================================

ASPECTS_RESOURCE_THRESHOLD = 29   # Body Erosion / Mind Fracture 자원 임계 (≤29)
ASPECTS_ABYSS_THRESHOLD = 14      # Abyss 자원 한계 (≤14)
ASPECTS_ARC_PROXIMITY_THRESHOLD = 0.3  # Arc proximity 외부 사건 인식 임계

# 8개 라벨 → typological 산문 디렉티브. 라벨 자체는 Pro 산문에 직접 노출 X.
ASPECTS_DIRECTIVES = {
    "Failure Resonance":        "실패의 결이 외부 흐름과 공명한다. 의도하지 못한 결이 표면에 새겨진다.",
    "Glory's Shadow":           "성공의 빛 아래 다른 결이 자리잡는다. 영광의 표면이 어두운 결을 함께 운반한다.",
    "Body Erosion":             "한계 가까운 신체가 외부 흐름을 더 깊이 받는다. 사건이 몸의 결에 짙게 새겨진다.",
    "Mind Fracture":            "한계 가까운 정신이 외부 흐름을 그대로 통과시킨다. 균열의 결이 표면에 선명해진다.",
    "Inner-Outer Convergence":  "내면의 결과 외부 사건이 한 자리에서 만난다. 두 흐름이 같은 시점에 표면으로 떠오른다.",
    "Resurgence":               "지나간 결이 이번 결정에 다시 들어온다. 묻혔던 흔적이 표면에 새로 새겨진다.",
    "Abyss":                    "한계의 결 끝에서 마지막 잔여가 깎인다. 회복할 자리가 남지 않은 결이 표면에 새겨진다.",
    "Loss of Control":          "사건이 통제의 결을 넘었다. 흐름이 자기 결을 가지고 표면을 벗어난다.",
}


# =========================================================
# Phase 2 (Vigor/Composure 리브랜딩) — F+G 메커닉
# 자세히: 파티쳇수정/vigor_composure_rebrand_log.md
# =========================================================

# F. 자동 소비 baseline 매트릭스 — 장르 × 축 (Y/N)
# Y=True면 그 축에 -1 baseline drain (layer-cap: 같은 layer 다중 Y는 단일 -1)
GENRE_BASELINE_DRAIN = {
    # A. The Stage
    "high_fantasy":    {"vigor": True,  "composure": False},
    "wuxia":           {"vigor": True,  "composure": False},
    "cyberpunk":       {"vigor": False, "composure": True},
    "post_apocalypse": {"vigor": True,  "composure": True},
    "space_opera":     {"vigor": False, "composure": False},
    "modern":          {"vigor": False, "composure": False},
    # B. The Flavor
    "urban_fantasy":   {"vigor": False, "composure": False},
    "steampunk":       {"vigor": False, "composure": False},
    "cosmic_horror":   {"vigor": False, "composure": True},
    "game_system":     {"vigor": False, "composure": True},
    # C. The Lens
    "noir":            {"vigor": False, "composure": True},
    "comedy":          {"vigor": False, "composure": False},
    "romance":         {"vigor": False, "composure": True},
    "drama":           {"vigor": False, "composure": True},
}

# Genre Layer 분류 (layer-cap 계산용)
GENRE_LAYERS = {
    "A": {"high_fantasy", "wuxia", "cyberpunk", "post_apocalypse", "space_opera", "modern"},
    "B": {"urban_fantasy", "steampunk", "cosmic_horror", "game_system"},
    "C": {"noir", "comedy", "romance", "drama"},
}

# F. 씬타입 × 축 baseline (5 SceneType)
ACTION_BASELINE_DRAIN = {
    "combat":   {"vigor": True,  "composure": True},
    "intimate": {"vigor": False, "composure": False},  # cap이 본업
    "social":   {"vigor": False, "composure": True},
    "normal":   {"vigor": False, "composure": False},  # 장르가 결정
    "summary":  {"vigor": False, "composure": False},
}

# F. Flash mental_impact severity enum → 수치 매핑
# MI-2(2026-06-18): heavy 정의를 "드문 중대 사건"으로 상향 → 값도 severe쪽으로 재배치.
# mild(기본)→heavy 점프(-2→-10, gap 8)를 heavy→extreme(gap 5)보다 크게 둬서
# heavy가 '중간값'이 아니라 extreme과 한 묶음(중대)으로 읽히게 함. 운영 관찰 후 한 줄로 조정 가능.
MENTAL_IMPACT_ENUM_SCALE = {
    "none":    0,
    "mild":    -2,   # 기본값. 대부분 턴
    "heavy":   -10,  # 드문 중대 사건 (severe band)
    "extreme": -15,
}

# G. 회복 균형
NATURAL_RECOVERY_THRESHOLD = 2   # |event_delta| ≤ T 시 자연 회복 가산 (구조 드레인 baseline/cascade/status 제외, 사건성 델타만 판정)
NATURAL_RECOVERY_AMOUNT = 1      # 양축 각각 +1
CHAPTER_REFRESH_THRESHOLD = 60   # intermission_active 시 max(value, 60)

# H. 축당 턴 낙폭 안전캡 (magnitude, 양수). mis-mapping/소스 스택이 한 턴에 축을 폭락시키는 것 방지.
#    설계 의도 최대치(impact cap + consequence/cascade 여유)에 맞춤. combat만 큰 피해 허용.
#    낙폭만 제한 — 회복/상승은 무제한. 초과 시 _process_axis가 WARNING 로그(과차감 관측 채널).
MAX_AXIS_DROP_PER_TURN = {
    "intimate": 10,
    "social":   12,
    "normal":   18,
    "combat":   25,
    "summary":  6,
    "default":  18,
}

# G-2. 트라우마 각성 (붕괴 dwell 기반 리바운드 + 일시적 판정 디버프)
# delta 부호가 아니라 "stage 3(붕괴)에 연속으로 머문 턴 수"로 발동 → 회복 수학과 분리.
TRAUMA_DWELL_TURNS = 2        # stage 3 연속 N턴 후 리바운드 발동
TRAUMA_REBOUND_VALUE = 40     # 리바운드 목표값 (stage 1 바닥 = '겨우 기능'. 과거 90은 공짜 풀힐이라 폐기)
TRAUMA_DEBUFF_TURNS = 3       # 리바운드 시 부여되는 판정 디버프 지속 턴 (turns-duration status_effect로 자동 만료)
TRAUMA_DEBUFF_MODIFIER = -5   # 모든 판정 페널티 (status modifiers.judgment 경로 — 실제 롤에 반영)


# [Phase 3 DEPRECATED] Effort (각오) — Phase 2 Flash modulator(extreme)가 자동 흡수 예정.
# 단순 dead 보존 (미래 재활성화 가능). 코드 호출처: judgment_engine effort_mod (effort_used가 None이면 자동 skip).
EFFORT_BONUS = 10    # 판정 +10
EFFORT_COST = 8      # 활력/평형 선불 (흡수 보험 포함, 추가 비용 없음)

# [Phase 3 DEPRECATED] Flashback (회상) — 능동적 기력 소비.
# Theoria.flashback_eval 자동 감지는 유지 (산문 반영). 차감 코드는 orchestration에서 비활성화.
FLASHBACK_COST_TIERS = {"trivial": 3, "standard": 8, "bold": 15}
FLASHBACK_PASSIVE_DISCOUNT = 0.5  # 관련 특질 매칭 시 비용 50%
FLASHBACK_MIN_MENTAL = 10  # 이 이하면 회상 불가

# Rest Recovery (휴식) — 능동적 기력 회복
REST_RECOVERY = {"full": 20, "brief": 10, "interrupted": 5}
REST_UNSAFE_MODIFIER = 0.5  # 위험 장소 회복량 반감
REST_COMPOSURE_RATIO = 0.6  # composure = 60% of vigor's rest rate

# Cross-Axis Cascade: one axis's bad state drains the other
CROSS_AXIS_CASCADE = {
    0: 0,    # Fullness — no cascade
    1: 0,    # Agitation — no cascade
    2: -2,   # Exhaustion — mild drain on other axis
    3: -5,   # Collapse — severe drain on other axis
}

# [Phase 3 DEPRECATED] Loadout (로드아웃) — flashback과 함께 비활성화.
# 단순 dead 보존 (미래 재활성화 가능).
LOADOUT_SLOTS = 4
LOADOUT_SLOT_COST = {1: 3, 2: 6, 3: 10}
# 하위 호환용 (제거 예정)
LOADOUT_TYPES = {
    "standard": {"slots": 4, "label": "표준"},
}

# =========================================================
# Inventory System (N2 — 아이템 영속 + 인벤토리 검증)
# =========================================================
INVENTORY_SLOT_CAP = 4  # 로드아웃 고정 슬롯과 동기화
ITEM_PERSISTENCE_RULES = {
    "consumable": "remove_on_use",
    "weapon": "persist",
    "armor": "persist",
    "quest": "persist_until_complete",
    "misc": "persist",
}

# =========================================================
# Vector Search (N3 — 시맨틱 로어 검색)
# =========================================================
VECTOR_EMBEDDING_MODEL = "gemini-embedding-2"
VECTOR_TOP_K = 10          # [1M remap 2026-06-22] 5→10 (프롬 2.92%/1M, 로어만 활성 truncation이라 살짝 확대)
VECTOR_MIN_SCORE = 0.2     # [1M remap] 0.3→0.2 (관련도 문턱 낮춰 더 admit)

# Weighted Memory Retrieval (LIBRA-inspired scoring)
MEMORY_SCORE_W_SIMILARITY = 0.4
MEMORY_SCORE_W_RECENCY = 0.35
MEMORY_SCORE_W_IMPORTANCE = 0.25
# Downtime (다운타임) — 목적 있는 시간 투자 활동 (BITD Downtime)
DOWNTIME_RECOVER = {"safe": {"vigor": 25, "composure": 15}, "unsafe": {"vigor": 15, "composure": 10}}
DOWNTIME_VICE = {"base_vigor": 25, "base_composure": 20, "overindulge_threshold": 85, "overindulge_penalty": -15}
DOWNTIME_TRAIN = {"vigor_cost": 5, "composure_cost": 5, "progress_per_session": 1, "required_progress": 3}
DOWNTIME_SOCIALIZE = {"vigor": 5, "composure": 15, "depth_delta_range": (10, 15)}
DOWNTIME_PROJECT = {"vigor_cost": 3, "composure_cost": 3, "clock_progress": 1}

# Doom Clock Pacing: stage → extra auto-tick for time/hybrid clocks
DOOM_CLOCK_ACCELERATION = {
    0: 0,  # 평온 — decelerated (half-speed, handled by turn parity)
    1: 0,  # 불안 — normal
    2: 0,  # 경계 — normal
    3: 0,  # 위험 — normal (서사적 긴장은 높지만 기계적 가속 없음)
    4: 1,  # 임계 — world accelerates (+2 total auto-tick)
}

# Deceleration: stage 0에서 time/hybrid 시계 2턴에 1번 tick
DOOM_DECELERATION_STAGE = 0

# Fast-Track: new clocks start pre-filled at high doom (stage 4+)
DOOM_FAST_TRACK_THRESHOLD = 80
DOOM_FAST_TRACK_FILL = {4: 1, 6: 2, 8: 3}  # segments → initial filled

# Clock cap: max active (unresolved) clocks
DOOM_CLOCK_CAP = 5

# Clock staleness fade: 시계가 segments + bonus 턴 동안 진행 0이면 silent resolve.
# segments 4 → 10턴, 6 → 12턴, 8 → 14턴. pending_completion / do_not_resolve_yet 제외.
# Doom delta 0, 보상 없음 — "잊혀짐" 처리(완료/실패 아님).
CLOCK_STALE_BONUS_TURNS = 6

# Clock context drift fade: 시계의 linked_entity/tags가 현재 씬의 relevant_npcs /
# current_location과 N턴 동안 한 번도 안 겹치면 silent fade.
# 앵커 없는 시계(linked_entity 빈 + tags 빈)는 면제 — staleness가 처리.
# 진행 중이지만 맥락이 떠난 시계 처리용 (예: 도시 떠난 뒤 도시 외곽 시계).
CLOCK_DRIFT_TURNS = 8

# Clock oscillation fade: 시계 filled이 등락(+1, -1, +1, -1)만 반복하면 silent fade.
# 알고리즘: WINDOW 턴 안에 direction change ≥ DIR_MIN AND net change ≤ NET_THRESHOLD.
# direction change = non-zero diff의 sign 변화 횟수. zero diff는 무시 (정지 구간).
# 의미 없이 전진후진만 반복하는 시계 정리 — "그 의미가 없으니" (사용자 정책).
# pending_completion / do_not_resolve_yet 시계는 면제.
CLOCK_OSCILLATION_WINDOW = 6              # filled_history 깊이
CLOCK_OSCILLATION_NET_THRESHOLD = 1       # |last - first| ≤ 이 값 (진행 방향성 없음)
CLOCK_OSCILLATION_DIRECTION_CHANGES_MIN = 3  # non-zero diff sign 변화 ≥ 이 값이면 oscillating

# =========================================================
# Quest Manager Constants
# =========================================================
MAX_RETRY_COUNT = 3
RETRY_DELAY_SECONDS = 1
MAX_ARCHIVE_DISPLAY = 3
MAX_HISTORY_FOR_CHRONICLE = 50
EMPTY_QUEST_MEMO_MSG = "No active quests or memos."

# Quest staleness archive: 마지막 진행으로부터 N턴 이상 멈춘 active 퀘스트는 archive로 조용히 이동.
# une_facade의 8턴 directive softening은 유지 — 8~11턴 사이에는 약화된 채 살아있고, 12턴에 archive.
# Doom delta 0, 실패/완료 처리 아님. 단순히 active 슬롯에서 빠지는 것.
QUEST_STALE_ARCHIVE_TURNS = 12

# =========================================================
# ARC SYSTEM (호흡 3단 위계의 큰 호흡 — 좌표 모델)
# =========================================================
# 시계 = 0D 압력 누적 / 퀘스트 = 1D 단계 진행 / Arc = 5D 좌표 자율 표류
# Storyline 확장 (is_arc=True) — 별도 자료구조 X, carrier 재활용
# 자세히: 파티쳇수정/arc_spec_v2.md
# Phase 1 (스키마만, tick_arcs는 Phase 3)

# 호흡 길이 (운영 측정 산출)
ARC_EXPECTED_VOLUME_LENGTH = 150        # 1턴 ≈ 2000자 × 챕터 50턴 × 3 (최소치)
                                         # PC 무관심 시 자연 소멸 기준 (decay rate 분모)
ARC_DORMANT_BASE_TURNS = 30             # last_advanced_turn 무진행 임계 (compute_dormant_threshold fallback)

# 노출 / 거부 임계
ARC_PROXIMITY_EXPOSURE_THRESHOLD = 0.3  # 산문 노출 최소 proximity (전경/배경 분기 임계도 동일)
ARC_PHASES_CAP = 10                     # phases 누적 history 상한 (ring buffer)
ARC_TRAJECTORY_CAP = 20                 # trajectory ring buffer
ARC_FORESHADOWING_CAP = 20              # sensory_foreshadowing 안전망 (거부 게이트가 1차 통제)
ARC_OFFSCREEN_ACTIONS_CAP = 20          # offscreen_actions 안전망
ARC_FORESHADOWING_DISPLAY_CAP = 5       # 산문 노출 최대 (토큰 budget)
ARC_OFFSCREEN_DISPLAY_CAP = 5           # 산문 노출 최대

# Promote 트리거
ARC_PROMOTE_CATEGORY_MIN = 3            # 같은 카테고리 누적 임계 + High/Extreme 1+ 필수

# Supernova (weight armed/분기)
ARC_SUPERNOVA_ARMED_THRESHOLD = 0.95    # weight armed 진입
ARC_SUPERNOVA_FORCED_THRESHOLD = 0.7    # 분기 score → forced_climax
ARC_SUPERNOVA_VANISH_THRESHOLD = 0.3    # 분기 score → vanish

# Multi-arc 합성 (산문 노출)
ARC_FOREGROUND_CAP = 1                  # 동시 노출 전경
ARC_BACKGROUND_CAP = 2                  # 동시 노출 배경

# Quest Progress Track (DC-linked 5 Ranks)
QUEST_RANK_SETTINGS = {
    # doom_reward: 0626 −하락→+상승 flip (퀘스트 완성=서사 활동↑, 주 상승원, quest>clock). 옛 키명 유지.
    "easy":    {"max_progress": 4,  "doom_reward": 2,  "display": "쉬움"},
    "normal":  {"max_progress": 6,  "doom_reward": 3,  "display": "보통"},
    "hard":    {"max_progress": 8,  "doom_reward": 5,  "display": "어려움"},
    "extreme": {"max_progress": 10, "doom_reward": 7,  "display": "극난"},
    "epic":    {"max_progress": 12, "doom_reward": 9,  "display": "전설"},
}
QUEST_DEFAULT_RANK = "normal"

# NPC Connection Stages (depth-based, 0-100)
NPC_CONNECTION_STAGES = {
    "면식":  {"range": (0, 20),   "hint_en": "Acquaintance — may acknowledge PC but no bond",                        "hint_kr": "얼굴을 아는 정도"},
    "지인":  {"range": (20, 40),  "hint_en": "Known — casual familiarity, small talk",                               "hint_kr": "가벼운 대화 상대"},
    "친분":  {"range": (40, 60),  "hint_en": "Friendly — willing to help, shares opinions",                          "hint_kr": "호의적, 부탁을 들어줄 수 있음"},
    "신뢰":  {"range": (60, 80),  "hint_en": "Trusted — shares secrets, offers real help, shows vulnerability",       "hint_kr": "비밀을 나눌 수 있음, 취약함을 보임"},
    "유대":  {"range": (80, 101), "hint_en": "Bonded — deep loyalty, will sacrifice, reveals full self",              "hint_kr": "깊은 유대, 헌신적"},
}

NPC_TRAJECTORY_DEPTH_MAP = {
    "improving": (5, 10),
    "stable": (0, 0),
    "declining": (-5, -3),
}

NPC_TENSION_DRAMA_THRESHOLD = 50

def get_connection_stage(depth: int) -> Dict[str, Any]:
    """커넥션 단계 정보 반환."""
    for stage_name, info in NPC_CONNECTION_STAGES.items():
        low, high = info["range"]
        if low <= depth < high:
            return {"name": stage_name, **info}
    return {"name": "유대", **NPC_CONNECTION_STAGES["유대"]}

def get_connection_stage_name(depth: int) -> str:
    """커넥션 단계명만 반환."""
    return get_connection_stage(depth)["name"]


# =========================================================
# Default Contents
# =========================================================


DEFAULT_RULES = """
[Lorekeeper 기본 룰: 서사 중심 TRPG]

## 📜 판정 시스템 (1d100)
성공 기준치 (DC: Difficulty Check)
- 매우 쉬움 (Trivial): DC 0
- 쉬움 (Easy): DC 20
- 보통 (Normal): DC 40 (표준)
- 어려움 (Hard): DC 60
- 매우 어려움 (Extreme): DC 80

판정 결과 계산
- `[주사위 1d100] + [특성/상태 보정치] >= [DC]` 이면 성공
- 대성공 (Critical Success): 주사위 96~100 & 최종값 >= DC
- 대실패 (Critical Failure): 주사위 1~5 (보정치와 상관없이 실패)

기본 원칙: 서사적 판정 우선
- 판정 모듈이 캐릭터의 특질, 상황적 유불리를 종합하여 자동 판정합니다.

## 🎭 캐릭터 성장
성장은 경험치나 레벨이 아닌 서사적 성취를 통해 이루어집니다:
- 특질: 반복된 행동이나 경험을 통해 습득하는 특성 (독 내성, 야간 시야, 파이어볼 등)

## 🌓 비일상 적응
초자연적/비일상적 존재나 현상에 반복 노출되면 점차 익숙해집니다:
- 처음: 공포, 혼란, 패닉
- 적응 중: 경계하지만 대처 가능
- 일상화: 담담하게 받아들임

## ⚔️ 전투
- 캐릭터 능력, 특질, 상황을 종합하여 서사적으로 결정
- 피해: 서사적으로 묘사 (HP 수치 없음)
- 상태이상: 부상, 중독, 공포 등이 행동에 영향

## 💰 소지품
- 화폐: 세계관에 맞는 단위 사용 (골드, 은화, 크레딧 등)
- 인벤토리: 소지품 목록
- 거래: 협상과 상황에 따라 가격 변동

## 📝 특수 규칙
- OOC 수정: `(OOC: 요청)` 형식으로 캐릭터 정보 수정 가능
- 정보 확인: `!정보`로 캐릭터 상태 조회
"""

DEFAULT_WORLD_STATE = {
    "time_slot": "오후",
    "weather": "맑음",
    "day": 1,
    "doom": 30, # [Unsettled Start] User Request
    "risk_level": "None",
    "current_location": "Unknown",
    "location_rules": {},
    "world_constraints": {},
    "last_temporal_context": {},
    "doom_clocks": [],
    "turn_index": 0,
    "storyteller": {
        "last_event_turn": 0,
        "recent_categories": [],
        "recent_tags": [],
        "event_queue": [],
        "total_events_fired": 0,
        "recent_dice": [],  # W9: Seven Dice history
    },
}

# =========================================================
# W5: Pipeline Degradation Rule
# =========================================================
PIPELINE_DEGRADATION = {
    "theoria_analysis": {
        "absent_behavior": "visible_context_only",
        "fallback_dai": {"energy_direction": "rising", "scene_type": "normal", "quality_flags": {}},
    },
    "emotion_engine":  {"absent_behavior": "skip_emotion"},
    "judgment_engine":  {"absent_behavior": "skip_judgment"},
    "anomaly_module":   {"absent_behavior": "skip_anomaly"},
    "story_director":   {"absent_behavior": "no_direction"},
    "doom_module":      {"absent_behavior": "preserve_doom"},
    "vigor_composure":  {"absent_behavior": "preserve_mental"},
}

# =========================================================
# W9: Seven Dice — Narrative Entropy Management
# =========================================================
SEVEN_DICE = {
    # 가시 3개: 능동적 창작 마찰 (AGELAST 원본의 구문 제약 계열 — Slot 19로 라우팅)
    "agon":    {"name": "Agon/적",     "visible": True,  "effect": "이번 응답에서 NPC 대사 최소 한 줄은 의문문으로. 명제문 비중 축소."},
    "alea":    {"name": "Alea/운",     "visible": True,  "effect": "장면에 예정되지 않았던 사물·소리·기척 하나를 불쑥 등장시킨다."},
    "mimicry": {"name": "Mimicry/역할", "visible": True,  "effect": "감각 왜곡 하나를 삽입한다 — 시점 전환·시간 감각·색 편향·공감각 중 자유 선택."},
    # 은닉 4개: 수동적/부재의 힘
    "silence": {"name": "Silence/침묵", "visible": False, "effect": "말해야 할 NPC가 침묵. 행동해야 할 순간에 부재. 빈자리가 이야기."},
    "broken":  {"name": "Broken/오류",  "visible": False, "effect": "시스템 규칙의 예상치 못한 상호작용이 서사가 된다."},
    "ghost":   {"name": "Ghost/유령",   "visible": False, "effect": "하지 않은 행동의 결과가 분위기로 스며든다. 대안 현실의 잔향."},
    "yours":   {"name": "Yours/미정",   "visible": False, "effect": "플레이어의 다음 입력이 방향을 결정. 열린 결말."},
}

DICE_WEIGHTS = {
    "default":          {"silence": 0.30, "ghost": 0.30, "yours": 0.20, "broken": 0.10,
                         "agon": 0.05, "alea": 0.03, "mimicry": 0.02},
    "scene_stagnant":   {"agon": 0.30, "alea": 0.25, "mimicry": 0.15,
                         "silence": 0.10, "ghost": 0.10, "yours": 0.05, "broken": 0.05},
    "scene_repetitive": {"mimicry": 0.30, "agon": 0.20, "broken": 0.15,
                         "silence": 0.15, "ghost": 0.10, "alea": 0.05, "yours": 0.05},
}

DICE_HISTORY_CAP = 10

# =========================================================
# Simulation Constants (Restored)
# =========================================================

# Storyteller Scene-Type Allowed Categories
STORYTELLER_SCENE_CATEGORIES = {
    "normal": None,  # None = all allowed
    "combat": set(),  # empty = skip all events during combat
    "social": {"social", "environmental", "temporal"},
    "intimate": set(),  # intimate = all events defer
    "exploration": {"supernatural", "environmental", "temporal"},
    "rest": {"environmental"},
}

# 시계 라이프사이클 씬타입별 억제
CLOCK_SCENE_RULES = {
    "normal":   {"create": True,  "auto_tick": True,  "flash_tick": True},
    "intimate": {"create": False, "auto_tick": False, "flash_tick": False},  # 전면 동결
    "combat":   {"create": False, "auto_tick": False, "flash_tick": True},   # 전투 행동 틱만
    "social":   {"create": True,  "auto_tick": False, "flash_tick": True},   # 자동 틱 억제
    "summary":  {"create": True,  "auto_tick": True,  "flash_tick": True},
}

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

# Status Effects v3 (Structured)
DURATION_TYPES = {"persistent", "turns", "scene", "until_rest", "until_recovery"}

# Default severity modifiers (used when an effect doesn't define modifiers)
SEVERITY_EFFECTS = {
    0: {"judgment": 0,   "doom_impact": 0, "vigor_drain": 0},
    1: {"judgment": -5,  "doom_impact": 1, "vigor_drain": -3},
    2: {"judgment": -10, "doom_impact": 3, "vigor_drain": -7},
    3: {"judgment": -15, "doom_impact": 5, "vigor_drain": -15},
}

# Canonical status tags (tag -> definition)
STATUS_TAGS = {
    # Debuffs
    "injury": {"name": "부상", "type": "debuff", "severity": 1, "modifiers": {"judgment_combat": -5}},
    "wound": {"name": "중상", "type": "debuff", "severity": 2, "modifiers": {"judgment_combat": -10}},
    "mortal_wound": {"name": "치명상", "type": "debuff", "severity": 3, "modifiers": {"judgment_combat": -15}},
    "poison": {"name": "중독", "type": "debuff", "severity": 1, "modifiers": {"judgment": -5}},
    "deadly_poison": {"name": "맹독", "type": "debuff", "severity": 2, "modifiers": {"judgment": -10}},
    "burn": {"name": "화상", "type": "debuff", "severity": 1, "modifiers": {"judgment_combat": -5}},
    "frostbite": {"name": "동상", "type": "debuff", "severity": 1, "modifiers": {"judgment_combat": -5}},
    "fear": {"name": "공포", "type": "debuff", "severity": 1, "modifiers": {"judgment_social": -5}},
    "panic": {"name": "패닉", "type": "debuff", "severity": 2, "modifiers": {"judgment_social": -10}},
    "blind": {"name": "실명", "type": "debuff", "severity": 2, "modifiers": {"judgment_combat": -10}},
    "fracture": {"name": "골절", "type": "debuff", "severity": 2, "modifiers": {"judgment_combat": -10}},
    "bleeding": {"name": "출혈", "type": "debuff", "severity": 1, "modifiers": {"judgment_combat": -5}},
    "exhaustion": {"name": "탈진", "type": "debuff", "severity": 1, "modifiers": {"judgment": -5}},
    "stunned": {"name": "기절", "type": "debuff", "severity": 3, "modifiers": {"judgment": -20}},
    "confusion": {"name": "혼란", "type": "debuff", "severity": 1, "modifiers": {"judgment": -5}},

    # Buffs
    "vigor": {"name": "활력", "type": "buff", "severity": 0, "modifiers": {"judgment": 5}},
    "defensive_stance": {"name": "방어태세", "type": "buff", "severity": 0, "modifiers": {"judgment_combat": 5}},
    "focus": {"name": "집중", "type": "buff", "severity": 0, "modifiers": {"judgment": 5}},
    "stealth": {"name": "은신", "type": "buff", "severity": 0, "modifiers": {"judgment": 5}},
    "flight": {"name": "비행", "type": "buff", "severity": 0, "modifiers": {"judgment": 5}},
    "night_vision": {"name": "야간시야", "type": "buff", "severity": 0, "modifiers": {"judgment": 5}},
    "blessing": {"name": "가호", "type": "buff", "severity": 0, "modifiers": {"judgment": 5}},
    "haste": {"name": "신속", "type": "buff", "severity": 0, "modifiers": {"judgment": 5}},
}

# Legacy name -> tag mapping (for string-based status lists)
LEGACY_TAG_MAP = {
    "부상": "injury",
    "중상": "wound",
    "치명상": "mortal_wound",
    "중독": "poison",
    "맹독": "deadly_poison",
    "화상": "burn",
    "동상": "frostbite",
    "공포": "fear",
    "패닉": "panic",
    "실명": "blind",
    "골절": "fracture",
    "출혈": "bleeding",
    "탈진": "exhaustion",
    "기절": "stunned",
    "혼란": "confusion",
    "활력": "vigor",
    "방어태세": "defensive_stance",
    "집중": "focus",
    "은신": "stealth",
    "비행": "flight",
    "야간시야": "night_vision",
    "가호": "blessing",
    "신속": "haste",
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

def get_normality_stage_info(val: Union[int, str, float]) -> Dict[str, Any]:
    """정상화 단계 정보를 반환합니다."""
    try:
        val = int(val)
    except (ValueError, TypeError):
        val = 0

    for (low, high), info in NORMALITY_STAGES.items():
        if low <= val < high:
            return info
    return NORMALITY_STAGES[(90, 101)]

# =========================================================
# UNE (Universal Narrative Engine) Configs
# =========================================================

# =========================================================
# Lens-Based Mechanic Profile Tables (Genre Detection v3)
# =========================================================
# C계층(Lens/narrative_tone)이 메카닉을 주도.
# build_mechanic_profile()이 이 테이블들로 결정론적 프로필 생성.

LENS_AXIS_MAP = {
    "noir":    ["information", "position"],
    "comedy":  ["complication", "schedule"],
    "romance": ["relation"],
    "drama":   ["mental", "relation"],
}

LENS_DEFENSE_MAP = {
    "noir":    ["info_control", "composure"],
    "comedy":  ["chaemyeon", "wit"],
    "romance": ["nunchi", "composure"],
    "drama":   ["vigor", "composure"],
}

# =========================================================
# Lens × Phase Doom System — 起承轉結 + 間 4+1단
# =========================================================
# 페이즈 boundary는 lens별 climax_threshold 따라 달라짐. range 마지막(間)은 climax_threshold 이후.

LENS_DOOM_PHASE_RANGES = {
    # noir climax 90, comedy 85, romance 80, drama 75, default 80
    "noir":    {"起": (0, 25), "承": (25, 55), "轉": (55, 85), "結": (85, 90),  "間": (90, 101)},
    "comedy":  {"起": (0, 25), "承": (25, 55), "轉": (55, 80), "結": (80, 85),  "間": (85, 101)},
    "romance": {"起": (0, 25), "承": (25, 55), "轉": (55, 75), "結": (75, 80),  "間": (80, 101)},
    "drama":   {"起": (0, 25), "承": (25, 55), "轉": (55, 70), "結": (70, 75),  "間": (75, 101)},
    "default": {"起": (0, 25), "承": (25, 55), "轉": (55, 75), "結": (75, 80),  "間": (80, 101)},
}

# Phase × Lens curve multipliers (장르 거장 라인 반영). default는 flat (의견 없음).
LENS_DOOM_CURVE = {
    #            起    承    轉    結    climax_threshold
    "noir":    {"起": 0.8, "承": 1.0, "轉": 0.7, "結": 2.0, "climax": 90},  # slow burn + cold reveal
    "comedy":  {"起": 0.7, "承": 1.8, "轉": 1.2, "結": 1.5, "climax": 85},  # screwball oscillation
    "romance": {"起": 0.5, "承": 1.5, "轉": 0.4, "結": 2.0, "climax": 80},  # dwelling 정체기
    "drama":   {"起": 0.7, "承": 1.5, "轉": 0.7, "結": 1.5, "climax": 75},  # quiet 轉
    "default": {"起": 1.0, "承": 1.0, "轉": 1.0, "結": 1.0, "climax": 80},  # flat fallback
}

# Atmosphere block (산문 주입의 진짜 매체). suture_tone 패턴 — multi-line directive.
# 4원리 적용: state-only (no imperatives), no negation, typological palette, no author names.
# 다중 lens 활성 시 양쪽 block 모두 노출 + "neither erases" hybrid 디렉티브 (une_facade 처리).
LENS_DOOM_ATMOSPHERE = {
    "noir": {
        "起": ("Surface routine over latent currents. Information moving below speech.\n"
              "Voice: present-tense, hardboiled. Direct, kinetic, terse.\n"
              "Procedural moments carrying weight beyond their surface."),
        "承": ("A name dropped with weight. Eye contact extending past comfort.\n"
              "Information surfacing as currency. Watching becomes mutual.\n"
              "Voice: present-tense, kinetic. Black humor under pressure."),
        "轉": ("Surveillance closing in. Paths narrowing. Pressure amplifying through quiet.\n"
              "Voice: hardboiled, present, kinetic. Black humor as armor.\n"
              "Procedural moments carrying menace. Information as weapon."),
        "結": ("Cold reveal in stark light. Information landing as betrayal as recognition.\n"
              "Clipped dialogue. Cuts that hit.\n"
              "Truth surfacing through evidence and pressure."),
        "間": ("Aftermath absorbing into routine. Tension receding.\n"
              "Voice still present but lower. Scars settling.\n"
              "New stories already moving beneath surfaces."),
    },
    "comedy": {
        "起": ("Easy rhythms, predictable beats. Small obstacles played for warmth.\n"
              "Voice: theatrical, attentive to incongruity. Light tone holding.\n"
              "Body humor and verbal wit in equal measure."),
        "承": ("Small fictions multiplying. Schedules colliding under their own logic.\n"
              "Voice still light but quicker. Wit sharpening through pressure.\n"
              "Audience seeing more than the players. Sympathy and ridicule intertwined."),
        "轉": ("Cover stories spawning new layers. Each fix introducing two new tangles.\n"
              "Voice accelerating. Body humor amplifying — slips, doubles, mistimings.\n"
              "Absurdity peaking. Characters caught in their own webs."),
        "結": ("Peak chaos. Masks slipping in overlapping confrontations.\n"
              "Voice fastest, theatrical and self-aware. Body humor at maximum.\n"
              "Recognition through absurdity itself. Laughter as resolution mechanism."),
        "間": ("Aftermath played soft. Embarrassment lingering with warmth.\n"
              "Voice settling, lighter again. Insight emerging through the absurd.\n"
              "What was uncovered moving into shared memory, easier now."),
    },
    "romance": {
        "起": ("Easy peace, days holding their shape. Glances starting to register.\n"
              "Voice: free indirect, attentive to interiority. Restraint as default.\n"
              "Internal weather setting in. Body cues registering before mind admits."),
        "承": ("Attraction sharpening. Banter carrying weight under wit.\n"
              "Withholding louder than utterance. Restraint shaping every choice.\n"
              "Internal weather thickening. Shared air growing dense."),
        "轉": ("The misunderstanding crystallizing. Distance opening through what was unsaid.\n"
              "Voice still restrained, more so. Internal storm beneath the calm exterior.\n"
              "Time stretching across the held silence. Long dwell before any movement."),
        "結": ("The decisive vulnerability. Restraint giving way through gesture.\n"
              "Voice softening — first direct utterance carrying weight built across the dwell.\n"
              "Body and word arriving together. Recognition through declared feeling."),
        "間": ("A new equilibrium. Internal weather settled but altered.\n"
              "Voice quieter, intimate now. Shared atmosphere thickening into permanence.\n"
              "What was declared moving into shared body of routine."),
    },
    "drama": {
        "起": ("Quiet inhabited atmosphere. Routine has its own gravity.\n"
              "Voice: restrained, attentive. Observation-led.\n"
              "Body knowing before mind notices. Small details bearing the weight."),
        "承": ("Subtle dissonance under a restrained surface. Atmosphere a slow barometer.\n"
              "Voice carrying restraint. Observation-led.\n"
              "Silence speaking louder than utterance."),
        "轉": ("Quiet recognition through muted gesture. Meaning slipping sideways.\n"
              "Time stretching around the moment of seeing. The dwell holds.\n"
              "Small over large. Recognition over revelation."),
        "結": ("A decisive small moment. Truth surfacing in what is seen.\n"
              "Body bearing what stays outside speech. Restraint holding.\n"
              "Quiet resolution; the thing becoming known."),
        "間": ("The residue of recognition. Familiar surfaces engaged differently after seeing.\n"
              "Voice quieter still. Body remembering, anchored in itself.\n"
              "What was learned moving into bone."),
    },
    "default": {
        "起": ("Light slice-of-life pacing. Daily activities, casual conversation.\n"
              "Environmental texture, body-anchored emotion."),
        "承": ("Activity rises. Threads surface, characters moving with intention.\n"
              "Mid-tempo, attentive sensory detail."),
        "轉": ("Something shifts under the surface. Tighter focus, dialogue carrying weight.\n"
              "Bodies signaling what voices hold back."),
        "結": ("Convergence approaching. Decisive moments crystallizing.\n"
              "Tone hardening or sharpening depending on character."),
        "間": ("Aftermath. Receding pulse, scars settling.\n"
              "Atmosphere absorbing what just happened."),
    },
}

# Flavor doom modifier (B-Layer)
FLAVOR_DOOM_MODIFIER = {
    "urban_fantasy": {"gain_mult": 1.0,  "threshold_offset": 0},
    "steampunk":     {"gain_mult": 1.1,  "threshold_offset": 0},
    "cosmic_horror": {"gain_mult": 1.2,  "threshold_offset": 5},
    "game_system":   {"gain_mult": 1.15, "threshold_offset": 0},
}

LENS_ICON = {
    "noir": "🕸️",
    "comedy": "💥",
    "romance": "❤️‍🔥",
    "drama": "⚡",
}

LENS_PRIMARY_RESOURCE = {
    "noir": "composure",
    "comedy": "composure",
    "romance": "composure",
    "drama": "vigor",
}

FLAVOR_BONUS = {
    "cosmic_horror": {
        "anomaly_tag_pool_extend": ["eldritch", "madness", "void"],
        "defense_bonus": {"composure_drain_supernatural": 1.2},
    },
    "urban_fantasy": {
        "anomaly_tag_pool_extend": ["supernatural", "mundane_crack", "contract"],
        "defense_bonus": {},
    },
    "steampunk": {
        "anomaly_tag_pool_extend": ["mechanical", "pressure", "malfunction"],
        "defense_bonus": {},
    },
    "game_system": {
        "anomaly_tag_pool_extend": ["glitch", "system_error", "level_shift"],
        "defense_bonus": {},
    },
}


def build_mechanic_profile(narrative_tone: list, style_tech: list = None) -> dict:
    """감지된 장르 Lens에서 메카닉 프로필을 결정론적으로 생성.
    Flash가 아닌 코드가 생성 — 세션 내 일관성 보장."""
    if style_tech is None:
        style_tech = []

    primary = narrative_tone[0] if narrative_tone else "drama"
    secondary = narrative_tone[1] if len(narrative_tone) > 1 else None

    # disruption_axes: primary + secondary 합산
    axes = list(LENS_AXIS_MAP.get(primary, ["mental"]))
    if secondary:
        axes += [a for a in LENS_AXIS_MAP.get(secondary, []) if a not in axes]

    # defense_stats: 합산
    defense = list(LENS_DEFENSE_MAP.get(primary, ["vigor"]))
    if secondary:
        defense += [d for d in LENS_DEFENSE_MAP.get(secondary, []) if d not in defense]

    # doom_stages 폐기 — 외부 참조 없음. 페이즈 시스템이 LENS_DOOM_PHASE_RANGES + LENS_DOOM_ATMOSPHERE를 직접 참조.
    doom_stages = {}

    # doom_icon: primary + secondary
    icon = LENS_ICON.get(primary, "⏰")
    if secondary:
        icon += LENS_ICON.get(secondary, "")

    # primary_resource
    resource = LENS_PRIMARY_RESOURCE.get(primary, "vigor")

    # flavor_bonus: B계층 (style_tech)
    flavor_bonus = {}
    for f in style_tech:
        if f in FLAVOR_BONUS:
            flavor_bonus[f] = FLAVOR_BONUS[f]

    return {
        "primary_lens": primary,
        "secondary_lens": secondary,
        "disruption_axes": axes,
        "defense_stats": defense,
        "doom_stages": doom_stages,
        "doom_icon": icon,
        "primary_resource": resource,
        "flavor_bonus": flavor_bonus,
    }


# =========================================================
# Anomaly Adaptation Taxonomy (2-Level, 33 Sub-Groups)
# =========================================================
# 직접 매칭 100% + 같은 상위 카테고리 내 전이 50%
ADAPTATION_TAXONOMY = {
    "supernatural": ["undead", "dragon", "eldritch", "cursed", "spirit", "divine", "demonic", "shapeshifter"],
    "psychological": ["fear", "deception", "exposure", "betrayal", "madness", "guilt", "obsession"],
    "relational": ["encounter", "jealousy", "intimacy", "separation", "rivalry", "loyalty"],
    "situational": ["timing", "cascade", "authority", "environment", "resource", "crowd"],
    "informational": ["evidence", "surveillance", "leak", "secret", "misinformation"],
}


def get_parent_category(group: str):
    """하위 그룹의 상위 카테고리를 반환. 없으면 None."""
    for parent, children in ADAPTATION_TAXONOMY.items():
        if group in children:
            return parent
    return None

# =========================================================
# Genre Doom Sources (서사 긴장도 변동 사유 — Flash 참조용)
# =========================================================
# 곡선/단계 시스템은 LENS_DOOM_PHASE_RANGES + LENS_DOOM_ATMOSPHERE로 통합.
# 이 표는 Flash가 doom delta 결정 시 "어떤 사건이 doom을 변동시키는가" 참조.

GENRE_DOOM_SOURCES = {
    "cosmic_horror": {
        "increase": ["판정 실패", "이변", "밤 시간대", "고립", "정보 획득(진실)"],
        "decrease": ["이변 대응 성공", "안전지대 확보", "동료 합류", "의미 체계 재구축"],
    },
    "romance": {
        "increase": ["오해 발생", "라이벌 등장", "비밀 노출 위기", "친밀감 급진전", "질투 트리거"],
        "decrease": ["오해 해소", "진심 전달 성공", "신뢰 확인", "일상 공유"],
    },
    "comedy": {
        "increase": ["거짓말 추가", "목격자 증가", "이중 약속", "정체 노출 위기"],
        "decrease": ["성공적 수습", "오해가 우연히 풀림", "공범 확보"],
    },
    "noir": {
        "increase": ["증거 노출", "배신", "새 관계자 등장", "시간 압박"],
        "decrease": ["증거 은폐 성공", "정보원 확보", "추적자 따돌림"],
    },
    "action": {
        "increase": ["적 증원", "장비 손상", "민간인 위험", "시간 압박"],
        "decrease": ["전략적 후퇴 성공", "아군 합류", "적 약점 발견"],
    },
    "slice_of_life": {
        "increase": ["일정 충돌", "예상치 못한 방문자", "소문", "오해 누적"],
        "decrease": ["대화로 해소", "일상 회복", "소소한 성취"],
    },
}


def get_lens_phase(doom_value: int, lens: str = "default") -> str:
    """doom value → 페이즈 라벨(起承轉結間) 변환. lens별 boundary 다름."""
    ranges = LENS_DOOM_PHASE_RANGES.get(lens, LENS_DOOM_PHASE_RANGES["default"])
    for phase, (low, high) in ranges.items():
        if low <= doom_value < high:
            return phase
    return "間"  # doom > 100 등 fallback


def get_lens_atmosphere(lens: str, phase: str) -> str:
    """렌즈 + 페이즈 → atmosphere block. lens placeholder면 default fallback."""
    block = LENS_DOOM_ATMOSPHERE.get(lens, {}).get(phase)
    if block:
        return block
    return LENS_DOOM_ATMOSPHERE["default"].get(phase, "")

# =========================================================
# Vigor / Composure 2-Axis System (v3.0)
# =========================================================
# Vigor = 기력 (신체 + 의지): 전투, 부상, 수면 부족, 과로, 공포
# Composure = 평정 (정신 + 사회): 정신 충격, 배신, 수치, 고립, 정보 과부하

VIGOR_STAGES = MENTAL_STAGES  # 동일 단계 (충만/동요/고갈/붕괴)

COMPOSURE_STAGES = {
    0: {"name": "안정", "emoji": "😌", "range": (70, 101), "desc": "정신적으로 안정된 상태"},
    1: {"name": "흔들림", "emoji": "😰", "range": (40, 70), "desc": "감정적 동요가 있습니다"},
    2: {"name": "동요", "emoji": "😱", "range": (15, 40), "desc": "정신적 한계에 가깝습니다"},
    3: {"name": "붕괴", "emoji": "🫥", "range": (0, 15), "desc": "정신이 무너진 상태입니다. (트라우마 위험)"},
}

GENRE_PRIMARY_RESOURCE = {
    "cosmic_horror": "vigor",
    "action": "vigor",
    "romance": "composure",
    "comedy": "composure",
    "noir": "composure",
    "slice_of_life": "composure",
}

# Genre-Specific Disruption Axis (이변 교란 설정)
# Phase 3: Anomaly 장르 교란 엔진
GENRE_DISRUPTION_AXIS = {
    "cosmic_horror": {
        "primary_axis": "vigor",
        "defense_stat": "vigor",
        "defense_theory": "Continuum+TMT",
        "trigger_bonus": 10,
        "secondary_ratio": 0.3,
        "desc": "공포와 존재적 위협이 체력과 의지를 갉아먹는다",
    },
    "romance": {
        "primary_axis": "composure",
        "defense_stat": "composure",
        "defense_theory": "Nunchi+Chaemyeon",
        "trigger_bonus": -5,
        "secondary_ratio": 0.3,
        "desc": "감정적 혼란과 사회적 압박이 평정을 흔든다",
    },
    "comedy": {
        "primary_axis": "composure",
        "defense_stat": "composure",
        "defense_theory": "Chaemyeon+Goffman",
        "trigger_bonus": 5,
        "secondary_ratio": 0.2,
        "desc": "체면 위기와 상황 폭주가 평정을 시험한다",
    },
    "noir": {
        "primary_axis": "composure",
        "defense_stat": "composure",
        "defense_theory": "ToM+CoK+Statement",
        "trigger_bonus": 0,
        "secondary_ratio": 0.3,
        "desc": "심리적 압박과 진실의 무게가 평정을 갉아먹는다",
    },
    "action": {
        "primary_axis": "vigor",
        "defense_stat": "vigor",
        "defense_theory": "Prospect+BATNA",
        "trigger_bonus": 5,
        "secondary_ratio": 0.3,
        "desc": "물리적 위협과 전장의 혼란이 기력을 소모시킨다",
    },
    "slice_of_life": {
        "primary_axis": "composure",
        "defense_stat": "composure",
        "defense_theory": "Lazarus+Reactance",
        "trigger_bonus": -10,
        "secondary_ratio": 0.2,
        "desc": "일상의 변화와 자율성 위협이 평정을 흔든다",
    },
}

# NPC Autonomous Behavior (Phase 7)
NPC_AUTONOMOUS_ENABLED = True

# =========================================================
# V10 — 상태 우선 아키텍처 플래그
# =========================================================
# Sprint 1: 관계(npc_attitudes) 읽기를 SQLite npc_relations에서.
# False = V9 동작 (읽기 JSON, 쓰기는 dual-write로 테이블 쌓임).
# 서버에서 쓰기 며칠 돌려 parity 확인 후 True로. 문제 시 이 한 줄로 즉시 복귀.
V10_RELATIONS_READ_FROM_SQLITE = True
# Sprint 2-A: NPC 지식(npc_knowledge) / 2-B: NPC 본체(npcs).
# 2026-06-10 셋 동시 ON (사용자 결정): 도메인 독립 + lazy migration이라 빈 테이블에서도 안전
# (첫 읽기는 JSON 폴백 → 자동 이주). 문제 시 셋 다 False로 즉시 V9 복귀.
V10_KNOWLEDGE_READ_FROM_SQLITE = True
V10_NPCS_READ_FROM_SQLITE = True
# Sprint 4: 막간 장부 (침묵 틱) — 장면 밖 NPC 행적을 코드로 전진시켜 기록.
# 발화 0/콜 0/루프 0. OFF = 완전 무동작. spec: v10_sprint4_interim_ledger_spec.md
# 2026-06-11 ON (스모크 35케이스 PASS 후). 문제 시 이 줄 False = 즉시 완전 무동작.
V10_INTERIM_LEDGER = True
# [V10 적립 활용] 발효 압축 시, 그 청크 턴범위의 감정/태도/페이즈 호(弧)를 코드로 뽑아
# 발효 프롬프트에 보조 주입(콜 0, 영어 텔레그래픽=echo-safe). graceful-empty: 장부 비면 무주입,
# 데이터 쌓이는 만큼 자동 활성. 문제 시 이 줄 False = 즉시 무동작.
# 2026-06-19 ON (사용자 결정 — 장부 적립 중, graceful-empty라 위험 0).
V10_ARC_DIGEST_FERMENT = True
# [V10 지식 lite] npc_knowledge의 suspects/misbeliefs(의심·오해) 버킷 — 저장/추출은 무위험(상시),
# 이 플래그는 *주입/전파-state*(읽기경로 변경: "A는 X를 의심한다" 산문 주입, knows→hearer suspects 착지)만 게이트.
# 저장 스키마는 항상 쌓이고(populate 무조건), 이 플래그는 전파(knows→suspects 착지)+주입(iceberg suspects 렌더)만 게이트.
# graceful-empty(버킷 빈 동안 무동작) + echo-safe(영어 텔레그래픽, 기존 knows/false_beliefs 주입과 동일 register).
# 2026-06-19 ON (사용자 "바로 배선"). 문제 시 이 줄 False = 전파/주입 즉시 무동작(저장은 유지).
V10_KNOWLEDGE_BOUNDARY_INJECT = True

# I축(재정착): cadence_echo 턴-간 verbatim 후렴 되먹임. 2026-06-24 ON (추론ON도 cross-turn recall은 못 잡음 — 산문6 실증).
# False = 탐지·로그는 유지, 다음턴 주입만 무동작.
CADENCE_ECHO_INJECT = True

# iceberg mirror register 노테이션 (자기기만/내면). 2026-06-21 스키마드리프트 검수로 부활.
# 내면 노테이션이라 과내면 우려와 충돌 가능 → 산문 과내면화 관측 시 이 줄 False = mirror 즉시 무동작.
# (Flash-direct·inferred 양쪽 다 억제. 나머지 register/propagation은 무관.)
ICEBERG_MIRROR_ENABLED = True

# =========================================================
# Passive Theory Tag System (Phase 4-1)
# =========================================================
# Legacy keyword → modifier fallback (for passives without explicit modifiers)
# New passives will have Flash-generated "modifiers" dict; this table handles old-format passives.
PASSIVE_KEYWORD_MODIFIERS = {
    # Positive traits
    "용감": {"anomaly_defense": 15, "judgment_combat": 5},
    "냉정": {"anomaly_defense": 15, "judgment_social": 5},
    "강인": {"anomaly_defense": 15, "vigor_drain": 0.85},
    "침착": {"anomaly_defense": 10, "composure_drain": 0.85},
    "민첩": {"judgment_combat": 10},
    "지혜": {"judgment_social": 10},
    "직감": {"anomaly_defense": 10, "judgment_social": 5},
    "인내": {"anomaly_defense": 10, "vigor_drain": 0.85},
    "카리스마": {"judgment_social": 10},
    "은밀": {"judgment_combat": 5, "anomaly_defense": 5},
    # Negative traits
    "겁쟁이": {"anomaly_defense": -15, "judgment_combat": -5},
    "나약": {"anomaly_defense": -15, "vigor_drain": 1.2},
    "불안": {"anomaly_defense": -10, "composure_drain": 1.2},
    "공포": {"anomaly_defense": -15},
    "무모": {"judgment_social": -5},
    "우유부단": {"judgment_combat": -5, "judgment_social": -5},
    # Trauma (from Trauma Awakening)
    "Trauma": {"anomaly_defense": -5, "composure_drain": 1.15},
}


def get_passive_modifiers(passive) -> dict:
    """패시브에서 modifier dict를 추출. explicit modifiers 우선, 없으면 keyword fallback.
    str과 dict 양쪽 패시브 형식 모두 지원."""
    # str 패시브 (legacy): keyword fallback만 시도
    if isinstance(passive, str):
        for keyword, kw_mods in PASSIVE_KEYWORD_MODIFIERS.items():
            if keyword in passive:
                return kw_mods
        return {}
    if not isinstance(passive, dict):
        return {}
    # 1. Explicit modifiers (new format)
    mods = passive.get("modifiers")
    if mods and isinstance(mods, dict):
        return mods
    # 2. Legacy single modifier field (e.g., Trauma: {"modifier": -5})
    legacy_mod = passive.get("modifier")
    if isinstance(legacy_mod, (int, float)):
        return {"anomaly_defense": int(legacy_mod)}
    # 3. Keyword fallback — match passive name against known keywords
    p_name = passive.get("name", "")
    for keyword, kw_mods in PASSIVE_KEYWORD_MODIFIERS.items():
        if keyword in p_name:
            return kw_mods
    return {}


# =========================================================
# Item Theory Tag System (Phase 4-1b)
# =========================================================
# Legacy keyword → modifier fallback (for items without explicit modifiers)
# New items will have Flash-generated "modifiers" dict; this table handles old-format items.
ITEM_KEYWORD_MODIFIERS = {
    # Protective/holy items
    "성수": {"anomaly_defense_cosmic_horror": 15, "anomaly_defense": 10},
    "부적": {"anomaly_defense_cosmic_horror": 10, "anomaly_defense": 5},
    "십자가": {"anomaly_defense_cosmic_horror": 10},
    "횃불": {"anomaly_defense_cosmic_horror": 5, "anomaly_defense": 5},
    "해독제": {"anomaly_defense": 10},
    "가면": {"anomaly_defense": 5},
    # Weapons
    "단검": {"judgment_combat": 5},
    "검": {"judgment_combat": 10},
    "총": {"judgment_combat": 15},
    "방패": {"judgment_combat": 5, "anomaly_defense": 5},
    # Social/relationship items
    "목걸이": {"composure_drain_loneliness": 0.8},
    "편지": {"composure_drain_loneliness": 0.8},
    # Utility
    "망원경": {"judgment_perception": 5},
    "열쇠": {"judgment_craft": 5},
    "로프": {"judgment_combat": 3, "judgment_craft": 5},
}


def get_item_modifiers(item) -> dict:
    """아이템에서 modifier dict를 추출. explicit modifiers 우선, 없으면 keyword fallback.
    str과 dict 양쪽 아이템 형식 모두 지원."""
    # str 아이템 (legacy): keyword fallback만 시도
    if isinstance(item, str):
        for keyword, kw_mods in ITEM_KEYWORD_MODIFIERS.items():
            if keyword in item:
                return kw_mods
        return {}
    if not isinstance(item, dict):
        return {}
    # 1. Explicit modifiers (new format)
    mods = item.get("modifiers")
    if mods and isinstance(mods, dict):
        return mods
    # 2. Keyword fallback — match item name against known keywords
    i_name = item.get("name", "")
    for keyword, kw_mods in ITEM_KEYWORD_MODIFIERS.items():
        if keyword in i_name:
            return kw_mods
    return {}


DEFAULT_MODULE_SETTINGS = {
    "active_modules": ["judgment", "doom", "anomaly", "mental"],
    "doom_fallback": 40,
    "judgment_fallback": "LLM_DECISION"
}

# DLC Module Descriptions (for help/display)
DLC_MODULE_DESCRIPTIONS = {
    "judgment": {
        "name": "판정",
        "emoji": "⚖️",
        "desc": "1d100 + FitD Position 기반 행동 판정. 대성공/대실패 시 서사 분기."
    },
    "doom": {
        "name": "둠",
        "emoji": "⏰",
        "desc": "8단계 위협 시계. 위험한 행동·실패가 누적되며 세계 긴장도 상승."
    },
    "anomaly": {
        "name": "이변",
        "emoji": "🌪️",
        "desc": "둠 수치에 비례해 예측 불가능한 사건 발생. 로어에 등록된 징후 우선."
    },
    "mental": {
        "name": "기력",
        "emoji": "💪",
        "desc": "PC의 총체적 컨디션 (체력/집중/평판/정신). 판정 보정·이변 방어에 영향."
    }
}

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
