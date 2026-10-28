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

# [2026-10-27 C87] 제미니를 Vertex AI 로 부르기. GCP 크레딧(신규 300달러)은 Vertex 에만 붙는다 — AI Studio 키(Gemini API)는
#   결제가 따로라 크레딧이 안 깎이고 카드로 나간다. 청구 길은 키가 아니라 **부르는 주소**가 정한다
#   (generativelanguage = AI Studio · aiplatform = Vertex). 켜면 genai.Client(vertexai=True), 인증 = ADC
#   (GOOGLE_APPLICATION_CREDENTIALS = 서비스 계정 JSON 경로. .env 에 둬도 된다 — 위 load_dotenv 가 환경에 싣는다).
#   프로젝트를 비우면 SDK 가 JSON 의 project_id 를 쓴다. 끄면 지금 그대로(GEMINI_API_KEY).
GEMINI_VERTEX = (os.getenv("GEMINI_VERTEX") or "0").strip().lower() not in ("", "0", "false", "off", "no")
GOOGLE_CLOUD_PROJECT = (os.getenv("GOOGLE_CLOUD_PROJECT") or "").strip()
GOOGLE_CLOUD_LOCATION = (os.getenv("GOOGLE_CLOUD_LOCATION") or "").strip() or "global"


def gemini_client_kwargs() -> dict:
    """genai.Client(**…) 인자 — 켜면 Vertex(ADC), 끄면 AI Studio 키. 렌더 · 기동 게이트가 같이 쓴다."""
    if GEMINI_VERTEX:
        kw = {"vertexai": True, "location": GOOGLE_CLOUD_LOCATION}
        if GOOGLE_CLOUD_PROJECT:
            kw["project"] = GOOGLE_CLOUD_PROJECT
        return kw
    return {"api_key": GEMINI_API_KEY}


def gemini_auth() -> tuple:
    """(준비됐나, 필요한 이름). 켜면 GOOGLE_APPLICATION_CREDENTIALS 가 **있는 파일**이어야 한다."""
    if GEMINI_VERTEX:
        _p = (os.getenv("GOOGLE_APPLICATION_CREDENTIALS") or "").strip()
        return (bool(_p) and os.path.isfile(_p)), "GOOGLE_APPLICATION_CREDENTIALS"
    return bool(GEMINI_API_KEY), "GEMINI_API_KEY"

# [2026-10-22 C54] Activity(디스코드 안 화면) — 포털 OAuth2 값 + Funnel 주소. ID·SECRET 중 하나라도 비면 화면은 꺼진다.
#   모델 env 부팅 게이트(validate_model_env)엔 넣지 않는다 — 선택 기능이라 없으면 꺼질 뿐 기동은 막지 않는다.
DISCORD_CLIENT_ID = (os.getenv('DISCORD_CLIENT_ID') or '').strip()
DISCORD_CLIENT_SECRET = (os.getenv('DISCORD_CLIENT_SECRET') or '').strip()
ACTIVITY_HOST = (os.getenv('ACTIVITY_HOST') or '').strip()
try:
    ACTIVITY_PORT = int(os.getenv('ACTIVITY_PORT') or 8080)
except ValueError:
    ACTIVITY_PORT = 8080
ACTIVITY_ENABLED = bool(DISCORD_CLIENT_ID and DISCORD_CLIENT_SECRET)

# [2026-08-14 조사] gemini-3.7-flash = Stable(08-13 출시), 일반 API 키로 사용 가능(Vertex 전용 아님).
#   입력 1,048,576 / 출력 65,536, thinking low·medium·high("minimal"은 에러 반환).
#   제미니 복귀 시 후보값: PRO=FLASH="gemini-3.7-flash".
#   ★★ [2026-08-18 완전 해소 — 라우팅 전면 개편] 이 셋은 **제미니 경로 전용 실제 모델 ID**다.
#      openai 경로 라우팅과 **완전히 무관**하다 — 콜사이트가 이제 모델명이 아니라 역할
#      (config.role_model("reader") 등)을 선언하고, 아래 _ROLE_CHAINS 가 env 슬롯을 지목한다.
#      PRO="gemini-3.7-flash" 든 PRO=FLASH 동일값이든 openai 해석은 1비트도 안 움직인다.
#      _(구 경고 2종 — "PRO를 3.7-flash로 두면 reader_gm 이 조용히 GLM으로", "PRO=FLASH면
#        부분문자열 폴백에 맡겨짐" — 둘 다 사문. 이름표 역할 자체가 폐지됐다.)_
#   아래 두 preview 모델은 2026-08-14 기준 생존 확인됨.
# [2026-08-18 기본값 전량 제거 — env 단일 레버] 모델 이름은 **오직 .env 에서만** 온다.
#   폴백 리터럴이 있으면 "env 를 고쳤는데 안 바뀐다 / 지웠는데 계속 돈다"가 생긴다.
#   미설정 = "" → gemini 백엔드로 기동하면 validate_model_env() 가 이름을 나열하고 기동 거부.
#   (import 시점엔 절대 안 죽는다 — 스모크·도구가 env 없이 config 를 import 한다.)
MODEL_ID_PRO = os.getenv("GEMINI_MODEL_PRO", "")
MODEL_ID_FLASH = os.getenv("GEMINI_MODEL_FLASH", "")
MODEL_ID = os.getenv("GEMINI_MODEL_ID", MODEL_ID_PRO)

# Renderer Backend: "gemini" or "openai" (OpenAI-compatible — Ollama Cloud 등).
#   [2026-10-26] 운영 값은 .env 가 쥔다 — 이 기본값 · 주석으로 운영 백엔드를 읽지 말 것(옛 주석 "현 운영=Ollama" 가 낡아 있었다).
RENDERER_BACKEND = os.getenv("RENDERER_BACKEND", "openai").lower()

# OpenAI-compatible renderer — Ollama Cloud (https://ollama.com/v1, 모델명 :cloud 접미사)
OPENAI_API_KEY = os.getenv("OPENAI_RENDERER_API_KEY", "")  # ollama.com/settings/keys 키 env로
OPENAI_BASE_URL = os.getenv("OPENAI_RENDERER_BASE_URL", "https://ollama.com/v1")
OPENAI_MODEL_ID = os.getenv("OPENAI_RENDERER_MODEL", "")
# OpenAI-compatible generation parameters — 렌더 전용(persona.py 렌더 어댑터만 읽는다).
# [2026-10-02] 추론 켬 기준값 = DeepSeek 공식 영역: 온도 1.0 / top_p 0.95 / 페널티 0.
#   DeepSeek API thinking_mode 문서: 추론 모드는 temperature·presence_penalty·frequency_penalty를 받지 않고,
#   top_p는 0.95–1.0만 받는다(아래 값은 0.95로 처리). 옛 값 0.8 / 0.8 / 0.3 / 0.1은 그 영역 밖이었다.
#   리플레이 측정: 옛 값 폭주 5/20 → 공식 영역 0/28. 근거 analysis_line/딥식V4_추론루프_자료조사_2026-10-01.md §5,
#   스펙 composition/렌더샘플링_공식영역_스펙_2026-10-02.md. top_k는 Ollama /v1 미지원이라 싣지 않는다.
OPENAI_TEMPERATURE = float(os.getenv("OPENAI_TEMPERATURE", "1.0"))
OPENAI_TOP_P = float(os.getenv("OPENAI_TOP_P", "0.95"))
OPENAI_FREQUENCY_PENALTY = float(os.getenv("OPENAI_FREQUENCY_PENALTY", "0"))
OPENAI_TOP_K = int(os.getenv("OPENAI_TOP_K", "40"))
OPENAI_REASONING_EFFORT = os.getenv("OPENAI_REASONING_EFFORT", "high")  # none / low / medium / high — Ollama /v1 reasoning_effort로 전송(thinking 모델만 발동)
OPENAI_PRESENCE_PENALTY = float(os.getenv("OPENAI_PRESENCE_PENALTY", "0"))

# =========================================================
# Analysis Backend (좌뇌/Flash) — Gemini vs OpenAI 호환(wellspring DeepSeek)
# "gemini"(기본, 현행 genai.Client) 또는 "openai"(analysis_backend.GenaiCompatClient).
# Gemini 키가 죽으면 "openai"로 전환 → Flash 분석을 deepseek-v4-flash로 라우팅.
# =========================================================
ANALYSIS_BACKEND = os.getenv("ANALYSIS_BACKEND", "gemini").lower()
# 미지정 시 렌더러(OPENAI_*) 설정 재사용 — Ollama Cloud면 키/URL 자동 공유(상속)
ANALYSIS_OPENAI_API_KEY = os.getenv("ANALYSIS_OPENAI_API_KEY", OPENAI_API_KEY)
ANALYSIS_OPENAI_BASE_URL = os.getenv("ANALYSIS_OPENAI_BASE_URL", OPENAI_BASE_URL)
ANALYSIS_OPENAI_MODEL_FLASH = os.getenv("ANALYSIS_OPENAI_MODEL_FLASH", "")
ANALYSIS_OPENAI_MODEL_PRO = os.getenv("ANALYSIS_OPENAI_MODEL_PRO", "")
# 임베딩 = Voyage AI (Ollama Cloud는 임베딩 서빙 X → 전용 엔드포인트/키 분리). 200M 토큰 무료(voyage-4 계열).
# ⚠ ZDR은 Voyage 대시보드에서 opt-out 필요(기본 학습 ON). 차원 기본 1024. 공급자 바꿨으니 기존 벡터 1회 재임베딩 필요.
ANALYSIS_OPENAI_EMBED_BASE_URL = os.getenv("ANALYSIS_OPENAI_EMBED_BASE_URL", "https://api.voyageai.com/v1")
ANALYSIS_OPENAI_EMBED_API_KEY = os.getenv("ANALYSIS_OPENAI_EMBED_API_KEY", "")  # Voyage 키 env로
ANALYSIS_OPENAI_EMBED_MODEL = os.getenv("ANALYSIS_OPENAI_EMBED_MODEL", "")  # 기본값 없음 — .env 필수(분석=openai)
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

# ── 서사 콜 전용 모델 라우팅 (2026-07-05 GLM 스왑) ─────────────────────────────
# 추출 콜=기계 읽기 → FLASH(deepseek-v4-flash: V4의 추론↔본문 갭이 무해한 자리).
# 서사 콜(beats/hook/offscreen/psyche_narrative)=생성계 → GLM 잔류(V4 positivity bias가
# 능동성 공급 재료를 순화시키는 것 방지). ""(기본)=분리 없음, FLASH 그대로 → env 삭제가 곧 롤백.
ANALYSIS_OPENAI_MODEL_NARRATIVE = os.getenv("ANALYSIS_OPENAI_MODEL_NARRATIVE", "")
ANALYSIS_NARRATIVE_VAR = contextvars.ContextVar("analysis_narrative_call", default=False)

# ── 추출 콜 전용 모델 라우팅 (2026-07-05 후속) ─────────────────────────────────
# per-turn 추출(analyze_input)만 별도 모델. 추출=기계 읽기 = V4 약점(추론↔본문 갭·positivity)이
# 안 닿는 자리 + 오독은 psyche/지식→영속층에 무게이트로 박히는 "성공했는데 틀린 값" 클래스라
# 독해 충실도가 오염의 상류 방어 → V4-Pro 승격. FLASH(ds-flash)는 배경 콜(발효/연대기/루카/감사/GC)
# 전용으로 잔류(스코프 정밀 분리 — FLASH 통째 교체는 배경까지 Pro가 되는 과잉).
# ""(기본)=분리 없음 → env 삭제가 곧 롤백. heavy > narrative > extract > 이름 순.
ANALYSIS_OPENAI_MODEL_EXTRACT = os.getenv("ANALYSIS_OPENAI_MODEL_EXTRACT", "")
ANALYSIS_EXTRACT_VAR = contextvars.ContextVar("analysis_extract_call", default=False)

# ── Reader-GM "GM의 시선의 독자" (2026-07-05 개설 → 2026-08-11 소비 전량 배선) ──
# 렌더 후 async로 텔레스코프+산문만 blind read → reader_log 적립 후, 다음 턴 방향 재료로만 환류.
# 렌더 프롬프트 직행은 여전히 금지(읽는 눈은 쓰는 손이 아니다) — 좌뇌 서사 콜·디렉터·장부·계측까지.
# 스펙: 파티쳇수정/analysis_line/deepseek_v4_trait_playbook_2026-07-05.md §4 R1, 지도: 리더GM_지도_2026-08-11.md.
READER_GM_INTERVAL = int(os.getenv("READER_GM_INTERVAL", "1"))  # 매 N턴 실행 (0=비활성)
# [R4 구조 환류] 마스터 스위치 — 0=적립·계측만, 1(현행 기본)=소비 개시.
# 소비 경로: SD idle 거부권·이변 가점·아크 승격 1표 + [2026-08-11 리더 소비자] fog 급식·momentum 방향 후보.
READER_GM_FEED = int(os.getenv("READER_GM_FEED", "1"))
# [2026-08-11 리더 소비자] 뒤표지 토글 — reader_gm._get_or_build_blurb가 읽는데 정의가 없던 유령. 0=뒤표지 없이 독자 콜.
READER_GM_BLURB = int(os.getenv("READER_GM_BLURB", "1"))
# 지속성 게이트: 같은 축이 W턴 창에서 M턴 이상 수신될 때만 후보 승격(오독 1회의 상태화 차단).
READER_PERSIST_WINDOW = int(os.getenv("READER_PERSIST_WINDOW", "5"))
READER_PERSIST_MIN = int(os.getenv("READER_PERSIST_MIN", "3"))
# [2026-08-11 리더 소비자] C1 comprehension_fog → 서사 콜 "독자가 놓친 것" 블록. 지속성 통과분 최대 N개. 0=중화.
READER_FOG_CAP = int(os.getenv("READER_FOG_CAP", "3"))
# [2026-08-11 리더 소비자] C3 예측가능성 자기 채점(expectation ↔ 다음 턴 what_happened).
# 최근 WINDOW턴 중 HIGH턴 이상 적중이면 서사 콜에 굴절 1줄. HIGH > WINDOW = 중화(채점·로그는 유지).
READER_PREDICT_WINDOW = int(os.getenv("READER_PREDICT_WINDOW", "8"))
READER_PREDICT_HIGH = int(os.getenv("READER_PREDICT_HIGH", "6"))
# [2026-08-11 리더 소비자] C2 established → 비밀 누설 압력. leak_pressure는 매 sync 재계산되는 **파생값**이라
# 직접 += 는 덮인다 → 저장 필드 secret_ledger.reader_exposure(관측 턴수)를 두고 점수 계산부가 가산항으로 읽는다.
# 가산 = BUMP × exposure, 총 가산 상한 CAP. BUMP=0 = 중화(관측 적립은 계속, 압력만 0).
READER_LEAK_BUMP = int(os.getenv("READER_LEAK_BUMP", "3"))
READER_LEAK_CAP = int(os.getenv("READER_LEAK_CAP", "12"))
# [2026-08-11 리더 소비자] C4 momentum → SD idle 방향 후보(ambient 폴백 직전). 힌트 글자 캡, 0=중화.
READER_MOMENTUM_CAP = int(os.getenv("READER_MOMENTUM_CAP", "120"))
# [Stage 3-A 수신형 시드] 間(intermission) 진입마다 승격 축→이변 시드 번역(V4 배경 콜, 독자 계열 첫 영속 쓰기).
# [2026-08-11 리더 §7] 4중 게이트: 지속성 통과·번역기 경유·source=reader 태그·캡. 0=비활성.
# (구 "5중"의 persist_audit 편입 항목은 허위 — persist_audit는 시드를 감사하지 않는다.)
READER_GM_SEED = int(os.getenv("READER_GM_SEED", "1"))
READER_SEED_CAP = int(os.getenv("READER_SEED_CAP", "6"))  # reader-유래 시드 최대 보유(FIFO)
# [2026-08-11 리더 §7 → 당일 정정(레티어스)] reader_log는 계측 로그 계열이 아니라
# history_log와 같은 **영구 사료**(독자 공책 원본 — 챕터 회고 등 미래 소비자 재료) → **무캡이 기본**.
# 읽기는 항상 LIMIT≤40이라 조회 비용 불변. >0 설정 시에만 롤링(손잡이 잔존).
READER_LOG_KEEP = int(os.getenv("READER_LOG_KEEP", "0"))
# [2026-08-17 장면 연관 로어 급식] 리더 본 콜에 "출판된 설정 부록 — 이 장면 관련 발췌" 블록 주입.
# 전제(레티어스 확정): 리더=독자가 아니라 **서브 GM**(GM의 다른 시선) — 세계(로어)는 알아도 된다.
# blind 게이트의 진의는 "저자의 내부 상태·DAI·지시문을 모른다"이지 "세계를 모른다"가 아니었다.
# 쿼리=이번 턴 산문(기존 _build_notebook_recall과 같은 축), 풀=lore_chunks, 엔진=공용 싱글턴.
# TOP_K=0 = 블록 완전 비활성(현행 폴백 = 부록 없는 리더 콜). 청크당 문자 캡은 CHARS.
# ⚠비밀 스크럽 필수: secret_ledger truth에 닿는 청크는 드롭 — 안 그러면 `_apply_reader_exposure`가
#   재는 "리더가 비밀을 만졌나"가 '읽어서 안 것'과 '급식받아 안 것'을 구분 못 해 계측이 오염된다.
READER_LORE_TOP_K = int(os.getenv("READER_LORE_TOP_K", "3"))
READER_LORE_CHUNK_CHARS = int(os.getenv("READER_LORE_CHUNK_CHARS", "500"))
# 독자 모델: ""(기본)=PRO 폴스루(V4-Pro). Gemma 후보 시 env로 gemma4:31b-cloud 등 지정.
ANALYSIS_OPENAI_MODEL_READER = os.getenv("ANALYSIS_OPENAI_MODEL_READER", "")
ANALYSIS_READER_VAR = contextvars.ContextVar("analysis_reader_call", default=False)

# ── 로어 통합 분석 전용 추론 tier (2026-09-02) ─────────────────────────────
# heavy/extract/reader와 동일 패턴(5번째 자매). heavy 블록 안에서 돌지만 **로어가 이긴다**.
# ★근거 = 폭주 실측. 같은 콜에서 reasoning_chars 851 → **13,562**(캡 지시문 3000자의 4.5배).
#   OpenAI 호환 라우트는 추론이 출력과 **같은 max_tokens 예산**을 쓰므로, 추론 폭주가 곧
#   JSON 잘림이 된다(실측: `[Unified Lore Analysis] 출력 토큰 한도 초과`).
#   [[feedback-reasoning-on-doctrine]]의 "off 처방은 **폭주 실측 시만**" 조건이 충족된 자리다.
# 이 콜의 성격도 근거를 보탠다 — 대부분이 추출(NPC 목록·장소·세력·규칙)이고 해석이 필요한 건
#   wingbeat 시드 정도다. deep으로 되돌리려면 env를 deep으로.
ANALYSIS_REASONING_TIER_LORE = os.getenv("ANALYSIS_REASONING_TIER_LORE", "light")
ANALYSIS_LORE_VAR = contextvars.ContextVar("analysis_lore_call", default=False)

# ── light 라우트 — 단문 배경 콜 3종 전용 모델 (2026-08-17) ─────────────────────
# 대상: 게시판(world_board.trigger_board_update → generate_posts) / 하단 상태 패널
# (status_panel.generate_panel) / 💭 속마음(turn_mail.generate_mind_call).
# 셋의 공통 성질 = **짧은 산출 + 단발 완결 + 턴 임계경로 밖**(전부 배경 큐/태스크).
# 판을 크게 읽을 필요가 없는 자리라 큰 모델을 태울 이유가 없다 — 값싼 모델로 분리.
# 발효·연대기·추출 등 나머지 FLASH 경로 콜은 **무변경**(이 컨텍스트에 안 들어옴).
# ""(기본)=분리 없음 → 기존 판정 그대로 폴스루 = 배포 시점 행동 변화 0. env 삭제가 곧 롤백.
# 권장값: deepseek-v4-flash:cloud   (사다리 = heavy > narrative > extract > reader > light > 이름)
ANALYSIS_OPENAI_MODEL_LIGHT = os.getenv("ANALYSIS_OPENAI_MODEL_LIGHT", "")
ANALYSIS_LIGHT_VAR = contextvars.ContextVar("analysis_light_call", default=False)

# ── Reasoning tier (semantic; reasoning_policy.py 가 모델별 값/파라미터로 매핑) ──────────
# .env 는 키 + 모델 ID 만. "어느 role 이 어느 tier" 는 여기 코드 기본값(off/light/deep).
# 모델을 GLM(high/max only) · deepseek(none/high/max) · 기타로 교체해도 policy 가 흡수한다.
# (위의 OPENAI_REASONING_EFFORT / ANALYSIS_OPENAI_REASONING_EFFORT* 는 레거시 — 이제 미사용.)
# [2026-10-02] 렌더 기본 = deep(→ reasoning_effort "high", DeepSeek 기본값). 리플레이 품질 비교에서 high가 정합·물리 사슬을
#   더 따졌다(추론 ~3할 길어짐, 폭주 0/5) — 레티어스 "하이로". 근거 composition/렌더샘플링_공식영역_스펙_2026-10-02.md §4.
#   ⚠ .env에 RENDERER_REASONING_TIER 줄(현재 light)이 있으면 이 기본값을 덮는다 — 그 줄을 지워야 high가 된다.
RENDERER_REASONING_TIER = os.getenv("RENDERER_REASONING_TIER", "deep")              # [10-02 이전] 코드 기본 off. [2026-07-05 GLM 스왑] .env가 light로 상향 — 추론+TELESCOPE 병행(제미니 시절 원 구조 복원). 캡은 persona 배선
# 렌더 전용 추론 캡(문자). 분석 light(1200)와 분리 — 1턴 실측 ~1900자에서 결과 좋았고(V1 갭 닫힘)
# GLM RP 강점=thinking 결합이라 렌더만 소폭 상향(레티어스 2026-07-05). 0=tier 기본값(1200) 사용.
# [2026-08-01] 2000 → 3500. KOREAN PROSE 3단 변환(영→일→한) 배포에 따른 실행 공간 확보:
#   다리는 추론에서만 건넌다("The bridge runs in reasoning alone") → 캡이 다리의 실제 예산.
#   추정 소요 = 영어 beat 구성 800~1200 + 일어 재구조화 500~1000 + 기존 사고 ~1000 ≈ 2300~3200자.
#   현행 2000(실측 ~2582, 소프트캡이라 초과 사용)으로는 변환분 자리가 없다. 3500 = 추정 상한 +여유.
#   ⚠V4-Pro 추론 폭주 이력(7.6~13.6k, off tier의 원 사유)이 있어 무제한 아님 — 소프트캡 유지.
#   판별: persona의 reasoning_trace_len 로그. 캡 근처 상시 = 부족 / 짧은데 보조용언 무변화 = ritual화.
# [2026-09-24 감사 후속] 3500 → 5000. 위 추정(2300~3200)은 08-01 기준 — 08-16 DSH 앵커가 4단계
#   "KO prose draft"를 추론 안에 넣었는데 캡은 그대로였다. 한국어 초안 ≈ 출력 산문 길이(1턴 ≈ 2000자,
#   ARC_EXPECTED_VOLUME_LENGTH 주석) → 1500~2000 추가 ≈ 3800~5200. 5000 = 그 상단(레티어스: 초안 작성은
#   "생각 금지"와 다른 것 — 캡이 초안 자리를 먹으면 안 된다). V4-Pro 폭주 구간(7.6k~)과는 여전히 거리.
#   max_tokens(NARRATIVE_MAX_OUTPUT_TOKENS 16384)가 추론을 포함해도 추론 ~5k자 + 출력 ~3k자로 여유.
RENDERER_REASONING_CAP_CHARS = int(os.getenv("RENDERER_REASONING_CAP_CHARS", "5000"))  # [2026-10-01 1차] 읽는 곳 0 — 렌더 추론 앵커·꼬리 캡 삭제(분석렌더_1차_구현스펙 §5)
ANALYSIS_REASONING_TIER = os.getenv("ANALYSIS_REASONING_TIER", "light")             # 보조 per-turn 분석 = LIGHT (추론 ON)
ANALYSIS_REASONING_TIER_HEAVY = os.getenv("ANALYSIS_REASONING_TIER_HEAVY", "deep")  # 1회성 무거운 추출 = DEEP

# 1회성 heavy 분석(로어 통합 분석 등)의 출력 토큰 상한.
# [2026-09-02] ⚠**명시하지 않으면 "무제한"이 아니라 "제공자 기본값"이다.** OpenAI 호환 라우트는
#   max_output_tokens 가 없으면 max_tokens 를 아예 싣지 않아 ollama 기본값(보통 4k대)이 걸린다.
#   구 코드는 "대형 로어북도 잘리지 않도록" 상한을 **지웠는데**, 그게 오히려 잘림을 만들었다
#   (실측: `[Unified Lore Analysis] 출력 토큰 한도 초과`). 잘림 감지 자체도 죽어 있어서
#   증상이 "분석 결과 비어있음"으로만 보였다(_RespShim finish_reason 수리로 되살림).
#   여전히 잘리면 이 값을 올린다. 제공자가 모델 상한을 넘는 값을 거부하면 내린다.
ANALYSIS_MAX_OUTPUT_TOKENS_HEAVY = int(os.getenv("ANALYSIS_MAX_OUTPUT_TOKENS_HEAVY", "8192"))
# [2026-09-13 레티어스] 출력룰 라우터(`output_router._call_router`)는 1회성 콜인데 heavy 상한 8192를
#   그대로 썼다 — 이유는 없었다(09-02 로어 분석 값을 나눠 쓴 것). heavy tier=deep 이라 추론이
#   **같은 예산**을 먹고(09-02 실측 추론 13,562자), 심코어급 파일(값 68·항목 100+)은 JSON만 12k라
#   8192엔 반도 못 넣는다 → 전용 상한. 항목 100개 JSON ≈12k + 추론 몫 → 32k. 아낄 이유 0(1회성).
#   제공자가 모델 상한을 넘는 값을 거부하면 내린다(env).
ROUTER_MAX_OUTPUT_TOKENS = int(os.getenv("ROUTER_MAX_OUTPUT_TOKENS", "32768"))
# [2026-10-09 레티어스] 로어 통합 분석(`cognition.analyze_lore_unified`)도 1회성 콜이라 라우터와 같은 논리로 전용 상한.
#   프리즘 로어북(54K, NPC 24명)이 8192에서 JSON 9.5K자 근처로 잘려 "분석 결과 비어있음 — 로어 텍스트만 저장"이 됐다
#   (heavy tier 추론이 같은 예산을 먹는다). 32k로는 끝까지 나왔다. 제공자가 거부하면 env로 내린다.
LORE_ANALYSIS_MAX_OUTPUT_TOKENS = int(os.getenv("LORE_ANALYSIS_MAX_OUTPUT_TOKENS", "32768"))
# [2026-08-11 노선 갱신(레티어스)] **추론은 이제 어지간하면 켠다 — 축은 on/off가 아니라 "얼마나"(tier).**
# .env의 EXTRACT=light는 잔재가 아니라 이 노선의 의도적 재론이다. 아래 07-05 기록은 당시 실측
# 근거로 보존(코드 기본값 off도 유지 — env가 노선을 싣는다. V4-Pro 추론 폭주 이력은 캡 소프트 유지 사유):
#   구) per-turn 추출 콜만 OFF [2026-07-05 수처1 실측]: V4-Pro가 캡 지시(1200자)를 무시하고 매턴
#   7.6~13.6k자 사고(6회 측정) = 턴 지연 주범. 추출=기계 읽기라 추론 가치 낮음.
ANALYSIS_REASONING_TIER_EXTRACT = os.getenv("ANALYSIS_REASONING_TIER_EXTRACT", "off")
# [2026-08-11 리더 §7] 독자 콜 전용 tier — **손잡이만**. ""(기본)=미설정 → 위 공용
# ANALYSIS_REASONING_TIER("light") 폴스루 = 배포 시점 행동 변화 0.
# 배경 매턴 콜이라 추출처럼 off로 뺄 후보이긴 하나, 독자는 "읽고 해석"이라 추론 가치가
# 기계 읽기와 다르다(추출 off의 근거를 그대로 못 옮긴다) → 실측 전까지 기본 유지.
# 끄려면 .env에 ANALYSIS_REASONING_TIER_READER=off.
ANALYSIS_REASONING_TIER_READER = os.getenv("ANALYSIS_REASONING_TIER_READER", "")


# =========================================================
# 역할 레지스트리 (2026-08-18 라우팅 전면 개편)
# =========================================================
# 병: 콜사이트가 **제미니 모델명**(MODEL_ID_PRO/FLASH/MODEL_ID)을 이름표로 넘기고,
#     analysis_backend 가 그 이름을 부분문자열("pro" in m …)로 해석했다. 제미니 이름이
#     "실제 모델 ID"와 "라우팅 라벨" 두 역할을 겸한 것이 뿌리 — GEMINI_MODEL_PRO 에
#     flash 든 이름을 넣으면 리더·heavy 가 조용히 갈아탔다.
# 처방: **콜사이트가 역할을 선언한다.** config.role_model("reader") 처럼.
#     · ANALYSIS_BACKEND=gemini → 아래 _ROLE_GEMINI 로 **실명** 반환(제미니 경로 무변경).
#     · ANALYSIS_BACKEND=openai → "role:reader" **토큰** 반환. analysis_backend 가
#       _ROLE_CHAINS 로 최종 해석. 해석 지점은 이 파일 + analysis_backend 두 곳뿐.
# 렌더러 역할만 RENDERER_BACKEND 를 본다(백엔드 두 개가 갈라져 설정될 수 있으므로).
_ROLE_TOKEN_PREFIX = "role:"

# openai 경로: 앞에서부터 **비어있지 않은 첫 슬롯**. (env 이름 문자열 — 값은 호출 시점 조회라
# 스모크가 config 속성을 monkeypatch 해도 그대로 먹는다.)
_ROLE_CHAINS = {
    "renderer":  ("OPENAI_MODEL_ID",),                                          # 우뇌 렌더(persona 는 실제론 OPENAI_MODEL_ID 직접 사용)
    "main":      ("ANALYSIS_OPENAI_MODEL_PRO",),                                # 구 MODEL_ID 자리(발효·연대기·GC·OOC 편집)
    "pro":       ("ANALYSIS_OPENAI_MODEL_PRO",),
    "flash":     ("ANALYSIS_OPENAI_MODEL_FLASH",),
    "extract":   ("ANALYSIS_OPENAI_MODEL_EXTRACT", "ANALYSIS_OPENAI_MODEL_FLASH"),
    "narrative": ("ANALYSIS_OPENAI_MODEL_NARRATIVE", "ANALYSIS_OPENAI_MODEL_FLASH"),
    "reader":    ("ANALYSIS_OPENAI_MODEL_READER", "ANALYSIS_OPENAI_MODEL_PRO"),
    "heavy":     ("ANALYSIS_OPENAI_MODEL_HEAVY", "ANALYSIS_OPENAI_MODEL_PRO"),
    "light":     ("ANALYSIS_OPENAI_MODEL_LIGHT", "ANALYSIS_OPENAI_MODEL_FLASH"),
}

# gemini 경로: 역할 → 실제 제미니 모델 상수. **개편 전 각 콜사이트가 실제로 넘기던 값**을
# 실측해서 박았다(래칫). 주의 — heavy/extract/narrative 는 PRO 가 아니라 FLASH 다:
# 그 콜들의 콜사이트가 전부 MODEL_ID_FLASH 를 넘기고 있었고(모델 분리는 openai 전용
# contextvar/env 로만 걸려 있었다), 제미니 경로에선 분리 자체가 없었다.
_ROLE_GEMINI = {
    "renderer":  "MODEL_ID",
    "main":      "MODEL_ID",
    "pro":       "MODEL_ID_PRO",
    "reader":    "MODEL_ID_PRO",
    "flash":     "MODEL_ID_FLASH",
    "extract":   "MODEL_ID_FLASH",
    "narrative": "MODEL_ID_FLASH",
    "heavy":     "MODEL_ID_FLASH",
    "light":     "MODEL_ID_FLASH",
}

# 이 역할만 RENDERER_BACKEND 로 게이트(나머지는 ANALYSIS_BACKEND).
_ROLE_RENDERER_GATED = frozenset({"renderer"})


def known_roles() -> tuple:
    """등록된 역할 이름 전량 — 스모크/판독기용."""
    return tuple(sorted(_ROLE_CHAINS))


def parse_role_token(label) -> "Union[str, None]":
    """'role:reader' → 'reader'. 역할 토큰이 아니면 None."""
    if isinstance(label, str):
        s = label.strip().lower()
        if s.startswith(_ROLE_TOKEN_PREFIX):
            return s[len(_ROLE_TOKEN_PREFIX):].strip() or None
    return None


def resolve_role_chain(role: str) -> str:
    """openai 경로 최종 해석 — 역할의 env 슬롯 체인에서 비어있지 않은 첫 값.

    미지 역할은 FLASH(종전 이름 판정의 기본값과 동일)로 떨어진다.

    [2026-08-18 기본값 제거] 체인이 **끝까지 비면 조용한 폴백을 하지 않는다** — 어느 역할의
    어느 체인이 비었는지 이름을 달아 RuntimeError. 모델 이름의 유일한 주인이 .env 가 된 이상,
    빈 슬롯은 "기본값으로 굴러갔다"가 아니라 **설정 사고**다. 조용히 엉뚱한 모델로 가는 것보다
    시끄럽게 죽는 편이 싸다(부팅 시점 방어는 validate_model_env()).
    """
    _g = globals()
    r = (role or "").strip().lower()
    chain = _ROLE_CHAINS.get(r)
    for _name in (chain or ()):
        _v = (_g.get(_name) or "").strip()
        if _v:
            return _v
    # **등록된** 역할인데 체인이 끝까지 비었다 = 설정 사고. FLASH 로 몰래 접지 않는다
    # (renderer 가 분석 FLASH 로 조용히 갈아타는 것이 정확히 막고 싶은 사고다).
    if chain:
        raise RuntimeError(
            f"[config] 모델 env 미설정 — 역할 {r!r} 의 체인이 끝까지 비었습니다: "
            + " > ".join(chain)
            + " (.env 에서 이 중 하나를 채우세요)"
        )
    # 미등록 역할만 FLASH 로 (종전 이름 판정의 기본값과 동일). 그것마저 비면 역시 예외.
    _fallback = (_g.get("ANALYSIS_OPENAI_MODEL_FLASH") or "").strip()
    if _fallback:
        return _fallback
    raise RuntimeError(
        f"[config] 모델 env 미설정 — 미등록 역할 {r!r} 의 기본 슬롯"
        " ANALYSIS_OPENAI_MODEL_FLASH 도 비어 있습니다 (.env 를 확인하세요)"
    )


# =========================================================
# 부팅 검증 — 현재 백엔드가 요구하는 모델 env 슬롯 (2026-08-18)
# =========================================================
# 원칙: 모델 이름의 주인은 .env 단독. 코드 기본값이 없으므로 "빠졌는데 조용히 돌아감"이
# 성립하지 않아야 한다 → 기동 시 **빠진 이름을 전부 나열**하고 거부한다.
# ⚠ 이 함수는 **호출될 때만** 검사한다(import 부작용 0). 스모크·도구가 env 없이 config 를
#    import 하므로 모듈 최상단에서 부르면 안 된다 — 호출 자리는 main.py 기동 게이트 하나뿐.
# (const 이름, env 이름) — 운영자가 고치는 건 env 이름이므로 그쪽을 출력한다.
_MODEL_ENV_NAMES = {
    "OPENAI_MODEL_ID": "OPENAI_RENDERER_MODEL",
    "ANALYSIS_OPENAI_MODEL_PRO": "ANALYSIS_OPENAI_MODEL_PRO",
    "ANALYSIS_OPENAI_MODEL_FLASH": "ANALYSIS_OPENAI_MODEL_FLASH",
    "ANALYSIS_OPENAI_EMBED_MODEL": "ANALYSIS_OPENAI_EMBED_MODEL",
    "MODEL_ID_PRO": "GEMINI_MODEL_PRO",
    "MODEL_ID_FLASH": "GEMINI_MODEL_FLASH",
    "VECTOR_EMBEDDING_MODEL": "VECTOR_EMBEDDING_MODEL",
}


def required_model_slots() -> tuple:
    """현재 백엔드 조합이 요구하는 (const 이름) 튜플. 렌더/분석 백엔드는 갈라질 수 있다."""
    _g = globals()
    need = []
    if (_g.get("RENDERER_BACKEND") or "").lower() == "openai":
        need.append("OPENAI_MODEL_ID")                    # 우뇌 렌더
    else:
        need.append("MODEL_ID_PRO")                       # MODEL_ID 의 파생 원천
    if (_g.get("ANALYSIS_BACKEND") or "").lower() == "openai":
        need += ["ANALYSIS_OPENAI_MODEL_PRO",             # main/pro/reader·heavy 체인 끝
                 "ANALYSIS_OPENAI_MODEL_FLASH",           # flash/extract/narrative/light 체인 끝
                 "ANALYSIS_OPENAI_EMBED_MODEL"]           # 임베딩(Voyage)
    else:
        need += ["MODEL_ID_PRO", "MODEL_ID_FLASH",        # 제미니 실명 2종
                 "VECTOR_EMBEDDING_MODEL"]                # 임베딩(gemini 경로 주인)
    out = []
    for n in need:
        if n not in out:
            out.append(n)
    return tuple(out)


def validate_model_env() -> list:
    """빠진 env 이름 전량(정렬)을 반환. 빈 리스트 = 정상. **예외를 던지지 않는다** —
    호출자(main 기동 게이트)가 전부 나열해서 출력하고 거부하도록."""
    _g = globals()
    missing = []
    for const in required_model_slots():
        if not (_g.get(const) or "").strip():
            env_name = _MODEL_ENV_NAMES.get(const, const)
            if env_name not in missing:
                missing.append(env_name)
    return sorted(missing)


def role_model(role: str) -> str:
    """콜사이트가 부르는 유일한 함수. 백엔드에 따라 실명(gemini) 또는 역할 토큰(openai).

    사용: `await client.aio.models.generate_content(model=config.role_model("reader"), …)`
    """
    r = (role or "").strip().lower()
    _g = globals()
    backend = _g.get("RENDERER_BACKEND") if r in _ROLE_RENDERER_GATED else _g.get("ANALYSIS_BACKEND")
    if (backend or "").lower() == "openai":
        return _ROLE_TOKEN_PREFIX + r
    return _g.get(_ROLE_GEMINI.get(r, "MODEL_ID_FLASH")) or _g.get("MODEL_ID_FLASH")


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


@contextlib.contextmanager
def narrative_analysis():
    """이 블록 안에서 도는 분석 콜만 서사 전용 모델(ANALYSIS_OPENAI_MODEL_NARRATIVE)로 라우팅.

    contextvar 라 async 태스크 단위 격리 + await 전파. ANALYSIS_OPENAI_MODEL_NARRATIVE 가
    비어 있으면 아무 효과 없음(FLASH 그대로) — heavy_analysis() 와 동일 패턴.
    """
    _token = ANALYSIS_NARRATIVE_VAR.set(True)
    try:
        yield
    finally:
        ANALYSIS_NARRATIVE_VAR.reset(_token)


@contextlib.contextmanager
def extract_analysis():
    """이 블록 안에서 도는 분석 콜만 추출 전용 모델(ANALYSIS_OPENAI_MODEL_EXTRACT)로 라우팅.

    heavy_analysis()/narrative_analysis() 와 동일 패턴. env 미설정이면 no-op.
    """
    _token = ANALYSIS_EXTRACT_VAR.set(True)
    try:
        yield
    finally:
        ANALYSIS_EXTRACT_VAR.reset(_token)


@contextlib.contextmanager
def reader_analysis():
    """이 블록 안의 분석 콜만 독자 전용 모델(ANALYSIS_OPENAI_MODEL_READER)로 라우팅.

    heavy/narrative/extract와 동일 패턴(4번째). env 미설정이면 no-op — 호출부가 넘긴
    모델 이름("pro")으로 폴스루 = V4-Pro.
    """
    _token = ANALYSIS_READER_VAR.set(True)
    try:
        yield
    finally:
        ANALYSIS_READER_VAR.reset(_token)


@contextlib.contextmanager
def lore_analysis():
    """이 블록 안의 분석 콜만 로어 전용 추론 tier(ANALYSIS_REASONING_TIER_LORE)를 쓴다.

    heavy/narrative/extract/reader와 동일 패턴(5번째). heavy_analysis()와 **중첩**해서 쓰며,
    tier 사다리에서 로어가 heavy를 이긴다(analysis_backend). 모델 라우팅은 heavy 그대로 —
    바꾸는 것은 추론 예산뿐이다.
    """
    _token = ANALYSIS_LORE_VAR.set(True)
    try:
        yield
    finally:
        ANALYSIS_LORE_VAR.reset(_token)


@contextlib.contextmanager
def light_analysis():
    """이 블록 안의 분석 콜만 경량 모델(ANALYSIS_OPENAI_MODEL_LIGHT)로 라우팅.

    용도 = 단문 배경 콜 3종(게시판·상태 패널·속마음). heavy/narrative/extract/reader 와
    동일 패턴(5번째). env 미설정이면 no-op — 호출부가 넘긴 모델 이름(flash)으로 폴스루.

    async: contextvar 라 await 를 건너 전파되고 태스크 단위로 격리된다. 배경 큐로 넘기는
    코루틴은 **코루틴 안에서** 감쌀 것(create_task 바깥에서 감싸면 컨텍스트 복사 시점에
    의존하게 된다 — 3 콜사이트는 전부 실제 콜을 감싼다).
    """
    _token = ANALYSIS_LIGHT_VAR.set(True)
    try:
        yield
    finally:
        ANALYSIS_LIGHT_VAR.reset(_token)


# 호출부 표기 별칭 — light_call() 로도 부른다(라우트 이름이 'light' 라 _analysis 접미가 장황).
light_call = light_analysis

# Generation Parameters - Analysis (Flash/Left Brain)
# [2026-07-02 A안 분할] 추출 콜=냉(0.1, 모달 읽기 복귀 — 생성계 필드가 서사 콜로 이사했으므로),
# 서사 콜=온(0.7, beats/hook/offscreen/chain의 다양성 = 능동성 엔진). 둘 다 env 튜닝 가능.
# (경과: 0.1→0.25 한 콜 절충안을 거쳐, 2분할로 온도 동거 자체를 해소)
ANALYSIS_TEMPERATURE = float(os.getenv("ANALYSIS_TEMPERATURE", "0.1"))
ANALYSIS_TEMPERATURE_NARRATIVE = float(os.getenv("ANALYSIS_TEMPERATURE_NARRATIVE", "0.7"))
ANALYSIS_TOP_K = 20
ANALYSIS_TOP_P = 0.8
# [2026-10-11 C21] 추론을 켠 분석 콜은 위 호출부 온도·top_p 대신 공식 영역(렌더와 같은 값)으로 돈다 — analysis_backend 한 자리.
#   위 값과 호출부 온도는 추론 off 콜(tier off)과 gemini 백엔드에서만 산다.
#   측정(86콜 재생): 옛 값 43판 중 실패 3(JSON 1·잘림 1·빈칸 1) → 공식 0, 근거 인용 그대로, Theoria 추론 +2.2k자(턴 +수 초).
#   레티어스 "그냥 다 공식 영역 쓰는 게 안정적이겠다". 근거 analysis_line/C21_분석샘플링_측정·스펙_2026-10-11. 끄려면 env 0.
ANALYSIS_OFFICIAL_SAMPLING = os.getenv("ANALYSIS_OFFICIAL_SAMPLING", "1").strip().lower() not in ("0", "false", "off", "no")
ANALYSIS_OFFICIAL_TEMPERATURE = float(os.getenv("ANALYSIS_OFFICIAL_TEMPERATURE", "1.0"))
ANALYSIS_OFFICIAL_TOP_P = float(os.getenv("ANALYSIS_OFFICIAL_TOP_P", "0.95"))

# [C안 2026-07-02] 백그라운드 유지보수 (전부 async — 턴 지연 0. 0 = 비활성)
PERSIST_AUDIT_INTERVAL = int(os.getenv("PERSIST_AUDIT_INTERVAL", "20"))       # N턴마다 영속층 감사 (log-only, 검출≠쓰기)
MEMORY_GC_FERMENT_INTERVAL = int(os.getenv("MEMORY_GC_FERMENT_INTERVAL", "5"))  # 발효 M회마다 deep_memory_data GC (백업+보수적)
# [Gemini 3] presence_penalty/frequency_penalty not supported - removed

# Generation Parameters - Narrative (Pro/Right Brain)
NARRATIVE_TEMPERATURE = 1.15  # 1.4 → 1.15 (감정 과장 어휘 fan-out 축소)
NARRATIVE_TOP_K = 60          # 70 → 60 (wild 후보 trim)
NARRATIVE_TOP_P = 0.80
NARRATIVE_MAX_OUTPUT_TOKENS = 16384  # 12288→16384 [2026-07-05 GLM 스왑]: 렌더 추론 ON — Ollama /v1이 thinking을 max_tokens에 포함할 가능성 대비(산문 잘림=비대칭 실패, 서사 콜 8192 인상과 동일 논리)
# [2026-10-14] 제미니 렌더러 추론 레벨 ("low" | "medium" | "high", ""=미지정)
GEMINI_RENDERER_THINKING_LEVEL = os.getenv("GEMINI_RENDERER_THINKING_LEVEL", "medium").strip().lower()
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
# [2026-09-14 W0] 아래 셋은 **W0 이후 미사용(레거시 도구 참조용)** — 지우지 않는다.
#   옛 판독 도구·옛 스모크가 import 하므로 이름만 남긴다. 봇 실행 경로는 CHANNELS_DIR.
SESSIONS_DIR = os.path.join(DATA_DIR, "sessions")   # W0 이후 미사용(레거시 도구 참조)
LORE_DIR = os.path.join(DATA_DIR, "lores")          # W0 이후 미사용(레거시 도구 참조)
RULES_DIR = os.path.join(DATA_DIR, "rules")         # W0 이후 미사용(레거시 도구 참조)

# [2026-09-14 W0] 채널 폴더 — 수명이 같은 것끼리 한 곳. 리셋 = 폴더 삭제.
CHANNELS_DIR = os.path.join(DATA_DIR, "channels")
CHANNEL_STATE_FILE = "state.json"
CHANNEL_DB_FILE = "memory.db"
CHANNEL_LORE_DIR = "lore"      # lore.txt / original.txt / rules.txt / summary.txt

# =========================================================
# Verbose 로그 채널 (전문 전용) — [2026-08-03]
# 전문(텔레스코프 ┣ 블록·FormatCheck 전량 등)과 흐름 한 줄이 같은 스트림에 섞여
# 있어 둘 중 하나를 포기해야 했다(전문 남기면 journal 도배 / 자르면 뒤쪽 증발).
# 전용 로거 `lk.verbose` + propagate=False로 분리. 설치는 bot_utils.setup_verbose_log().
#   journal : journalctl -u bot -f -o cat     (흐름 + 태그 요약)
#   전문    : tail -f logs/verbose.log
# 0으로 두면 완전 무동작(핸들러 미설치 → vlog가 조용히 버림).
# =========================================================
VERBOSE_LOG_ENABLED = os.getenv("VERBOSE_LOG_ENABLED", "1") == "1"
VERBOSE_LOG_PATH = os.getenv("VERBOSE_LOG_PATH", os.path.join("logs", "verbose.log"))
VERBOSE_LOG_MAX_BYTES = int(os.getenv("VERBOSE_LOG_MAX_BYTES", "20000000"))   # 20MB × 3 = 60MB
VERBOSE_LOG_BACKUP = int(os.getenv("VERBOSE_LOG_BACKUP", "3"))

# =========================================================
# Discord & Input Limits
# =========================================================
MAX_DISCORD_MESSAGE_LENGTH = 2000
MAX_FILE_SIZE_MB = 10
MAX_FILE_SIZE_BYTES = MAX_FILE_SIZE_MB * 1024 * 1024
MAX_TEXT_INPUT_LENGTH = 100000  # [V4] Doubled from 50k for detailed lore
# ⚠**바이트가 아니라 글자 수**다(bot_utils.read_attachment_text). 영어 시트는 1자=1바이트라
#   178KB 파일이 그대로 178,000자가 되어 걸린다(한글이면 3바이트/자라 같은 용량도 통과).
#   올릴 때 주의: 이 캡은 NPC·로어·룰·복원 첨부가 **공유**한다.

# 로어북 분석에서 뽑힌 NPC를 자동 등록할지. [2026-09-02] 기본 오프 —
#   NPC는 `!npc추가`로 따로 넣는 워크플로로 바뀌었고, 출처가 둘이면 판정이 두 벌이 된다.
#   로어 추출은 이름 정확도가 낮아 짧은 이름의 유령 NPC를 만들고 청소 경로가 없다.
#   로어북만 넣고 시작하는 워크플로로 되돌리려면 1로.
LORE_NPC_AUTO_REGISTER = os.getenv("LORE_NPC_AUTO_REGISTER", "0") == "1"
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
FERMENT_MAX_FAIL_STREAK = 3         # [2026-08-01] FRESH 발효 JSON 파싱 연속 실패 허용 횟수.
                                    # 이 횟수 전까지는 원본 history를 보존하고 재시도한다.
                                    # 도달하면 저품질 stub을 수용해 정체를 푼다(무한 누적 방지).
FERMENT_MAX_EMPTY_STREAK = int(os.getenv("FERMENT_MAX_EMPTY_STREAK", "6"))
                                    # [2026-09-24 감사 §3] FRESH 빈 응답·API 예외 연속 허용 횟수(파싱 실패와 별도).
                                    # 짧은 장애는 재시도로 넘기고, 닿으면 LLM 없이 코드 발췌 stub 으로 진행.

# [2026-10-26 C80] 기억층 v2 — DEEP 은 색인(코드 · LLM 0), 연대기는 원경 장(엔트리 N개 → 한 장, 롤링 없음).
#   ON: 엔트리를 안 지운다 · DEEP 압축 콜 0 · 자동 연대기(3회마다 · 10롤링) 대신 장 · 메모리 GC 호출 0 ·
#       turn_index(턴 → 이름 · 원문 구간) 적립 · T1 재등장 근거가 색인으로 원문을 찾는다.
#   OFF: 옛 DEEP · 연대기 갈래 그대로(바이트 동일). 관측 뒤 옛 갈래 삭제. 근거 = 파티쳇수정 C80 지도·스펙.
MEMORY_LAYERS_V2 = os.getenv("MEMORY_LAYERS_V2", "1").strip().lower() not in ("0", "false", "off", "no")
CHRONICLE_SPAN = int(os.getenv("CHRONICLE_SPAN", "8"))      # 장 하나 = 장에 안 묶인 엔트리 중 가장 오래된 N개(≈6N턴)
CHRONICLE_CHAPTER_CHARS = int(os.getenv("CHRONICLE_CHAPTER_CHARS", "2400"))  # 장 본문 저장 상한(문단 경계 절단)
CHRONICLE_SHOW_MAX = int(os.getenv("CHRONICLE_SHOW_MAX", "3"))  # `!연대기` 가 통째로 보여 줄 최근 장 수(나머지는 내보내기)
CHRONICLE_SLOT_CHARS = int(os.getenv("CHRONICLE_SLOT_CHARS", "700"))  # Slot 9 장 한 덩이(서사 요약 절) 상한 — 한 장이 예산을 통째로 막지 않게
CHRONICLE_MAX_FAIL = int(os.getenv("CHRONICLE_MAX_FAIL", "3"))  # 같은 범위 장 콜 연속 실패 문턱 — 닿으면 엔트리 요약 이어 붙인 stub 장
EMBED_BATCH_MAX = int(os.getenv("EMBED_BATCH_MAX", "100"))  # 임베딩 한 콜 상한 — 엔트리가 안 지워져 콜드 캐시 한 번에 수백 개가 갈 수 있다
ROOT_TRIGGER_WINDOW = int(os.getenv("ROOT_TRIGGER_WINDOW", "8"))  # 루트 떡밥 = 최근 N 엔트리 memory_triggers 합집합(2단계 장부 전 임시)
# [2026-10-26 C81] 떡밥 집 하나로 — 미해결 떡밥(열림 · 닫힘)은 장부(thread_log)만 안다. 켜면: Slot 9 머리에서 F1 떡밥(루트
#   active_memory_triggers)을 빼고 머리 이름을 회상(Theoria 회상 메아리)으로 바로잡는다 · 루트 창 안 셈 · 게시판 Scanner 10 이
#   장부 열린 약속 · 사안을 읽는다 · F1 떡밥 ↔ 장부 대조 관측 줄(log-only). 끄면 C80 상태 그대로. 근거 = C81 지도·스펙.
HOOKS_LEDGER_HOME = os.getenv("HOOKS_LEDGER_HOME", "1").strip().lower() not in ("0", "false", "off", "no")
# [2026-10-27 C82] 성장발효 — 인물 · 장소 · 세력 페이지 play 절을 쓰는 손을 하나로. 페이지가 성장 도장 뒤 등장 턴을 문턱만큼
#   모으면 그 턴들의 원문(C80 턴 색인 → history_log)으로 한 콜(옛 heavy 정리 자리를 넓힌 것) → play 절 전부(Observed 다시 쓰기 ·
#   항목 절 덧붙이기) · 정정 가드 · 사유 growth. 켜면 F1 은 page_patches 를 내려놓고(노트 · 부록 0) 잎 승격은 턴 색인으로 센다.
#   MEMORY_LAYERS_V2 가 켜져 있어야 돈다(턴 색인이 원문 다리). 끄면 C81 그대로. 근거 = C82 지도·스펙.
GROWTH_FERMENT = os.getenv("GROWTH_FERMENT", "1").strip().lower() not in ("0", "false", "off", "no")
GROWTH_TURNS = int(os.getenv("GROWTH_TURNS", "6"))              # 인물 페이지 문턱 — 성장 도장 뒤 등장 턴 수
GROWTH_TURNS_PLACE = int(os.getenv("GROWTH_TURNS_PLACE", "10"))  # 장소 · 세력 페이지 문턱
GROWTH_MAX_PAGES = int(os.getenv("GROWTH_MAX_PAGES", "2"))       # 한 턴(기억 차선 꼬리)에 키우는 페이지 상한 — 오래 밀린 것부터
GROWTH_INPUT_CHARS = int(os.getenv("GROWTH_INPUT_CHARS", "12000"))  # 성장 콜 원문 입력 상한(턴마다 나눠 쓴다, 이름 든 문단 먼저)
WIKI_PROMOTE_MIN_TURNS = int(os.getenv("WIKI_PROMOTE_MIN_TURNS", "3"))  # 켜면 잎(모음 줄) 승격 = 턴 색인 extra 등장 턴 수
# [2026-10-27 C83] F1 V5 — 리팩토링 4단계 정리(떡밥 · 관계 칸 빼고, 결과 상태 · 귀속 · 앎의 경계 · 화자 확인 규칙 더함).
#   HOOKS_LEDGER_HOME 이 켜져 있을 때만(V5 는 memory_triggers 를 안 낸다). 끄면 V4 바이트 동일. 근거 = C83 지도·스펙·보고.
FERMENT_PROMPT_V5 = os.getenv("FERMENT_PROMPT_V5", "1").strip().lower() not in ("0", "false", "off", "no")
GROWTH_MAX_FAIL = int(os.getenv("GROWTH_MAX_FAIL", "3"))        # 같은 범위 성장 연속 실패(콜 실패 · hash 충돌) 문턱 — 닿으면 도장만 넘김
GROWTH_MAX_TURNS = int(os.getenv("GROWTH_MAX_TURNS", "12"))      # 한 번 성장에 삭힐 턴 상한(오래된 쪽부터, 나머지는 다음 성장)
GROWTH_ITEMS_SHOWN = int(os.getenv("GROWTH_ITEMS_SHOWN", "40"))  # 성장 프롬프트에 보이는 항목 절 줄 수(절마다 최근 N, 중복 판정은 전체)
# [2026-10-27 C85] 현재값 · 앎의 경계(리팩토링 5단계). 켜면: 항목 절 발췌 = 상대별 최신 먼저(Relationships+ · Members+),
#   Theoria 4b 장면 인물에 페이지 Knowledge 최근 N줄, OOC 앎 편집 = 두 집(npc_knowledge + 페이지 Knowledge),
#   성장 · Theoria 앎 문안 한 구절, T6 오해 칸 키(misbeliefs). 끄면 C83 바이트 동일. 근거 = C85 지도·스펙.
MEMORY_CURRENT = os.getenv("MEMORY_CURRENT", "1").strip().lower() not in ("0", "false", "off", "no")
KNOW_PAGE_LINES_T1 = int(os.getenv("KNOW_PAGE_LINES_T1", "5"))   # Theoria 4b 인물당 페이지 Knowledge 줄 수(최근부터)
KNOW_PAGE_NPCS_T1 = int(os.getenv("KNOW_PAGE_NPCS_T1", "6"))     # 그 줄을 붙일 장면 인물 상한(장면 순서)
KNOW_PAGE_LINE_CHARS = int(os.getenv("KNOW_PAGE_LINE_CHARS", "200"))  # 그 줄 하나 글자 상한(넘으면 …)
# [2026-10-27 C85 D2] 같은 장면 NPC 앎 전파(would_share → 다른 인물 suspects `(via A)`). 레티어스 "그럼 끄자" — 기본 끔.
#   1 = 옛 전파 그대로. 원문에 말한 장면이 없어도 "말해 줬다"를 지어내서 끈다(확인된 말 · 엿들음은 Theoria 가 매 턴 가른다).
KNOWLEDGE_PROPAGATE = os.getenv("KNOWLEDGE_PROPAGATE", "0").strip().lower() not in ("0", "false", "off", "no")
# [2026-10-27 C86] 발효 트리거 v2(리팩토링 6단계). 켜면: 렌더 원문 창 = 안 삭힌 원문 전부(공백 0), 발효 = 안 삭힌 원문이
#   UNFERMENTED_MAX 개 또는 UNFERMENTED_MAX_CHARS 자를 넘을 때, 자르는 자리 = 턴 경계(되도록 장소 · 작중 시간 장면 경계),
#   최근 UNFERMENTED_MIN 개는 늘 원문으로 남긴다. 중요도 유예 · 죽은 GC 안 씀. 끄면 C85 그대로. 근거 = C86 지도·스펙.
FERMENT_TRIGGER_V2 = os.getenv("FERMENT_TRIGGER_V2", "1").strip().lower() not in ("0", "false", "off", "no")
UNFERMENTED_MIN = int(os.getenv("UNFERMENTED_MIN", "12"))            # 발효 뒤 남기는 최소 원문(메시지) = 렌더가 늘 보는 최근 몫
UNFERMENTED_MAX = int(os.getenv("UNFERMENTED_MAX", "24"))            # 넘으면 발효(레티어스 D1 가)
UNFERMENTED_MAX_CHARS = int(os.getenv("UNFERMENTED_MAX_CHARS", "40000"))  # 글자로도 — 넘으면 개수와 상관없이 발효
FERMENT_CHUNK_MIN = int(os.getenv("FERMENT_CHUNK_MIN", "6"))         # 한 번에 삭히는 최소 메시지(개수로 걸렸을 때)
FERMENT_CHUNK_MAX = int(os.getenv("FERMENT_CHUNK_MAX", "0"))         # 한 번에 삭히는 최대 메시지. 0 = 없음(레티어스: 양을 자르지 말고 F1 출력 상한을 푼다)
FERMENT_MAX_OUTPUT_TOKENS = int(os.getenv("FERMENT_MAX_OUTPUT_TOKENS", "32768"))  # F1 출력 상한(켜면 이것 · 끄면 옛 8192). 사실상 없음 —
#   칸을 비우면 "무제한"이 아니라 제공자 기본값(4k대)이 걸려 오히려 잘린다(ANALYSIS_MAX_OUTPUT_TOKENS_HEAVY 주석 09-02). 로어 분석 · 라우터와 같은 값.
SCENE_GAP_MINUTES = int(os.getenv("SCENE_GAP_MINUTES", "180"))       # 작중 시간이 이만큼 건너뛰면 장면 경계
RENDER_WINDOW_MAX_CHARS = int(os.getenv("RENDER_WINDOW_MAX_CHARS", "60000"))  # 렌더 원문 창 안전 상한 — 최근 UNFERMENTED_MIN 개는 이 상한과 상관없이 싣는다(옛 창도 늘 12)

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

# [2026-09-25 스레드 장부] 약속·사안 장부(thread_ledger.py). 스펙 state_v10/thread_ledger_spec_v0.1_2026-09-25.md
#   끄면: 배치 스키마 threads 줄·Slot 29 블록·Theoria 4c·루카 줄 전부 무동작(옛 active_threads 로 폴백 없음).
THREAD_LEDGER = os.getenv("THREAD_LEDGER", "1") not in ("0", "false", "False")
THREAD_EXTRACT_MAX = 15          # 배치 입력 장부 줄 수(열림·멈춤, 최근 활동순)
THREAD_RENDER_MAX = 6            # 약속 · 사안 줄 수(Slot 15 진행 중 블록 · C74 판4 — 옛 Slot 29)
THREAD_TITLE_MAX = 40            # 라벨 길이(렌더엔 라벨만 — 인용 0)
THREAD_SOON_MIN = 60             # '곧' 문턱(분)
THREAD_MINUTE_GRACE = 60         # 시각 지정 기한의 창 폭(분)
THREAD_OVERDUE_HIDE_DAYS = 7     # 기한이 이만큼 지나면 렌더에서만 뺀다(닫지 않음)
THREAD_WITNESS_LCS = 12          # 리더 증인 인용 겹침 최소 공통 길이

# [2026-09-25 관계 정성] 모델은 관계를 **말**로 내고 받는다 — 숫자는 코드 소유(09-16 메모 "숫자 델타는 코드가 환산").
#   NPC→PC(Theoria relation): 이동 말 → 턴 기준점 + 폭. 폭은 턴당 캡(sqlite_store 5 / +10·−5) 안이라 much_ = 캡.
#   스펙 파티쳇수정/state_v10/relation_words_spec_v0.1_2026-09-25.md
REL_BOND_SHIFT = {"much_warmer": 5, "warmer": 3, "holds": 0, "cooler": -3, "much_cooler": -5}
REL_TENSION_SHIFT = {"spikes": 10, "rises": 5, "holds": 0, "eases": -5}
#   NPC↔NPC(배치 npc_relations): 새 관계 세기 → |bond|, 기존 관계 이동 → ±|bond|.
REL_PAIR_STRENGTH = {"faint": 25, "clear": 50, "strong": 75}
REL_PAIR_SHIFT = {"deepens": 10, "weakens": -10}
# [2026-10-11 C28 게이지 말] 배경 추출(B-Outputs)이 게이지 변화를 **말**(shift)로 낸다 — 숫자는 코드가 환산(관계 이동 말과 같은 결).
#   폭: 캡이 있는 방향은 캡 × 비율(heavy = 캡이라 캡이 결정자 노릇을 안 한다), 없는 방향은 범위 폭 × 비율. 최소 1.
#   rule 글에 숫자로 적힌 양(전체 양 delta · 얹는 양 extra)은 그 수 그대로 받고 캡 면제(범위 클램프만).
#   카운터·목록 수량은 숫자 그대로(글에 적힌 수를 센다). 스펙 output_artifact/C28_게이지델타말로_지도·스펙_2026-10-11.md
GAUGE_SHIFT_TIER = {"down_slight": (-1, 0), "down": (-1, 1), "down_heavy": (-1, 2),
                    "up_slight": (1, 0), "up": (1, 1), "up_heavy": (1, 2)}
GAUGE_SHIFT_CAP_RATIO = (0.25, 0.6, 1.0)      # slight · 기본 · heavy — 캡 대비
GAUGE_SHIFT_RANGE_RATIO = (0.03, 0.07, 0.15)  # 캡이 없을 때 범위 폭 대비
# [2026-09-26 관계 시작값] 시작점 말 → 숫자. 어휘 = 4b `Stands:` 구간 이름(attitude_from_bond) · 마찰 구간(tension_band).
#   양 끝 ±70: 구간 가운데(±80)가 아니라 오를 여지를 남긴 자리(70도 devoted 구간 안). OOC 말 입력(stands/friction)도 이 표.
#   스펙 파티쳇수정/state_v10/relation_start_ooc_spec_v0.1_2026-09-26.md §1 S2
REL_START_BOND = {"devoted": 70, "friendly": 40, "neutral": 0, "unfriendly": -40, "hostile": -70}
REL_START_TENSION = {"low": 10, "strained": 35, "high": 65, "breaking": 85}
RELATION_START_MAX_NPCS = 4       # Theoria 4b "no record yet" 줄을 받는 NPC 상한(장면>근처>입력>히스토리 순)
RELATION_START_LINE_CHARS = 160   # PC를 가리키는 시트 문장 1개 길이 상한
RELATION_START_MAX_LINES = 2      # NPC당 문장 수
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
NEMESIS_THRESHOLD = -10  # ⚠ 미배선 (2026-07-06 감사): 소비자 0 — 튜닝해도 아무 효과 없음

# =========================================================
# Doom Chapter Volume — Phase × Lens × Scene 결합
# =========================================================
# 페이즈 boundary가 lens별로 다름 (climax_threshold 따라). 起承轉結間 4+1단.
# raw doom delta는 doom_module.process() 내부 자동 변동에만 multiplier 적용.
# game_world.change_doom (OOC `!둠`, quest 보상)은 직접 amount 반영 (사용자 의도 보존).

# [C90 10-28] 2.0 → 1.2 · rising 1.0 → 0.7 — 실측(서버 한 채널 25턴): 자동분 87% 가 rising, 轉 15턴째. 과녁 = 챕터 클라이맥스
#   110~120턴(목표가 15~25턴마다 끝날 때). 자동분만이면 더 늦다 — 남은 몫은 목표 완료 보상(doom_reward)이 채운다(설계 의도).
#   판독 · 가정 굴림 = tools/doom_pace_report.py. 스펙 파티쳇수정/output_artifact/C90_챕터볼륨속도·렌즈멈춤_지도·스펙_2026-10-28.md
DOOM_RAW_GAIN_BASE = 1.2  # turn당 raw doom gain base (energy tension_factor 변조). 3.0→2.0 (0626): base=floor 트리클로 낮춤, 완성(시계/퀘스트)이 주 상승원. 아크 4~6챕터/300+턴 타깃
# energy_direction → per-turn base doom gain 변조 (활성도 게이트, 2026-06-25 배선). calm 거의정지 / 긴장 상승.
DOOM_TENSION_FACTOR = {
    "detonation": 1.5,
    "rising":     0.7,    # [C90 10-28] 1.0 → 0.7
    "aftershock": 0.5,
    "idle":       0.04,   # 0.15→0.04 (2026-06-26): calm 둠 ~0.12/턴(3.0×0.04)으로 늦춤 — 소수누적 천천히. base/rising 유지라 tense는 300-타깃 보존, calm씬만 길어짐(300 넘어도 OK)
    "stagnant":   0.03,   # stagnant<idle 순서 유지 위해 비례 하향 (deadlock도 calm-low)
}
CHAPTER_INTERMISSION_DECAY = 3  # 間 페이즈 자연 감쇠 (-3/턴)
CHAPTER_RESET_FLOOR = 0  # 새 챕터 시작 floor (climax 후 이 값에 닿으면 起 진입). [C90 10-28] 10 → 0 — 첫 챕터(시작 0)와 같은 길이
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
# DOOM_CLIMAX_THRESHOLD·CLOCK_RESOLVE_DOOM 제거 (2026-07-06 감사): 소비자 0.
# climax는 LENS_DOOM_PHASE_RANGES가, 해결=NEUTRAL(0626 결정)은 doom_module 코드가 담당.
CLOCK_COMPLETE_DOOM = {4: 2, 6: 3, 8: 4}  # 완성(이벤트 발화)→상승. 10/15/20→2/3/4 (0626): 시계는 양념, 완성=활동 beat. 수식(phase×lens) 통과

# Clock Defense Rewards (→ primary vigor/composure axis)
CLOCK_MITIGATE_REWARD = 1           # 시계 1칸 감소당 활력/평형 +1
CLOCK_RESOLVE_REWARD = {4: 2, 6: 3, 8: 5}  # 시계 해결 시 segments별 보상
# [2026-10-28 C89] 시계 감지 문(손잡이 — 끄면 옛 동작). 스펙 파티쳇수정/output_artifact/C89_목표·시계감지_지도·스펙_2026-10-27.md
CLOCK_SEEN_GATE = True             # ㄴ Theoria clock_new.seen=foreseen 버림 · clock_updates.by=planned 버림
CLOCK_OPPORTUNITY_RETIRED = True   # ㄹ 새 시계의 doom_on_complete 음수(기회) → 0(타이머). PC가 쫓는 것은 목표
CLOCK_HYBRID_RETIRED = True        # ㅂ 새 시계 tick_mode hybrid → action. 자동 진행은 time 을 고른 것만
CLOCKS_ENDED_ON_PAGE = True        # ㅁ 렌더 뒤 배치 world_state.clocks_ended → 그 시계 averted(걸린 목표 +1 · 해소 보상)

# (JUDGMENT_DOOM_DELTA / DOOM_DICE_MODIFIER_STEP 제거 2026-06-25 — 옛 파멸-모델 잔재(0소비). 판정→doom 폐기, JUDGMENT_CONSEQUENCES가 대체.)

# Judgment Consequences v4 — 결과별 기계적 세계 변경
# primary_delta: 주축(vigor/composure) 직접 효과
# momentum: 다음 턴 판정 보너스/페널티 (±10 cap)
# clock_effect: 활성 시계 변경 (+전진/-후퇴), clock_all: 모든 시계 대상
# (2026-06-25: doom_delta 키 제거 — 판정→doom 폐기. 둠=이야기 활성도라 판정 성패와 무관. primary/momentum/clock만.)
# [2026-10-15 C40 C4] 시계만 남김 — primary_delta(활력·평형)·momentum(기세) 삭제. 레티어스: "활력평형 깎는 건 뺄 듯,
#   이제 주사위를 보고 들어가니까. 시계 진행만 체크." 활력·평형은 출력물 콜이 장면을 읽고 움직인다.
JUDGMENT_CONSEQUENCES = {
    "critical_success": {
        "clock_effect": -1,
    },
    "success": {
        "clock_effect": 0,
    },
    "partial": {
        "clock_effect": 0,
    },
    "failure": {
        "clock_effect": 1,
    },
    "critical_failure": {
        "clock_effect": 1,
        "clock_all": True,
    },
}

# [2026-10-15 C42] Judgment v3 의 ASPECT_VALUE·MOD_SOURCE_CAPS 퇴역 — 4dF 는 항마다 ±1 이고,
#   수정치 폭은 주사위 개수가 잡는다(judgment_engine.mod_bounds: 하한 −(N−1) · 상한 min(3, N)).

# Memory Optimization
FRESH_THRESHOLD = 24  # [Cost-Diet] 24개 초과 시 발효 (렌더 12 + 여유 12)

# =========================================================
# V7 Core Systems: Mental, Doom
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

# [2026-10-15 C42] CONDITION_MOD_SCALE·CONDITION_MOD_CAP 퇴역 — 세계상황은 판정 수정치가 아니라
#   PC 위치에 걸려 있으면 주사위 +1개(혼전도)로 들어간다. 극성은 묻지 않는다(mixed 포함).

# 활력 Stages (0-100) — PC의 몸 축(체력·지구력). [2026-10-07] 옛 이름 기력·옛 정의 "총체적 컨디션"
# Key: Stage ID (0-3)
MENTAL_STAGES = {
    0: {"name": "충만", "emoji": "😌", "range": (70, 101), "desc": "몸이 충실한 상태"},
    1: {"name": "둔화", "emoji": "😰", "range": (40, 70),  "desc": "출력이 느려지고 체력이 흔들립니다"},
    2: {"name": "고갈", "emoji": "😱", "range": (15, 40),  "desc": "한계에 가깝습니다. 판단과 행동이 둔해집니다"},
    3: {"name": "붕괴", "emoji": "🫥", "range": (0, 15),   "desc": "몸이 한계를 넘겼습니다."}
}

# DOOM_STAGES 제거 — LENS_DOOM_PHASE_RANGES + LENS_DOOM_ATMOSPHERE로 대체 (line 698~)

# DOOM_MENTAL_RECOVERY_MOD 제거 (2026-07-06 감사): 소비자 0 — 옛 Doom Stage(0-5)
# 멘탈 회복 배율. 회복은 vigor_composure_module 하이브리드 로직이 대체.

# =========================================================
# Aspects 부활 (시스템 교차 결합 신호)
# 자세히: 파티쳇수정/narrative/vigor_composure_design_brief.md (관련 메모)
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
# 자세히: 파티쳇수정/narrative/vigor_composure_rebrand_log.md
# =========================================================

# =========================================================
# [2026-09-06 P8b] **Vigor/Composure 코드 공식 상수 전량 삭제.**
# 판정 기준 한 줄: *장면을 분류해서 숫자를 정하는 코드는 전부 rule 의 몫*.
#   - GENRE_BASELINE_DRAIN / GENRE_LAYERS / ACTION_BASELINE_DRAIN (장르 14태그 × 씬타입 5)
#       → 대체: 기력·평형의 rule 문장. 중합 게이지(기력=HP·체력·지구력, 평형=MP·정신력·집중력)에
#         장르별 상시 소모율을 매기는 것은 애초에 성립하지 않았다.
#   - MENTAL_IMPACT_ENUM_SCALE → 대체: 전담 추출 콜의 deltas(캡 7/5). Theoria mental_impact 폐기.
#   - NATURAL_RECOVERY_THRESHOLD / _AMOUNT (조용한 턴 트리클) → 대체: 없음. 회복은 산문이
#         보여줬을 때 관측 델타로 들어온다. "아무것도 안 함"은 감지 대상이 아니다.
#   - CHAPTER_REFRESH_THRESHOLD (間 리프레시) → 대체: 없음(같은 이유).
#   - MAX_AXIS_DROP_PER_TURN (턴당 낙폭 안전캡) → 대체: 선언의 비대칭 캡(max_loss 7/max_gain 5)
#         하나가 속도 규율을 전담한다. 소스가 스택되던 구조 자체가 사라져 안전캡이 무의미해졌다.
#   - REST_RECOVERY / REST_UNSAFE_MODIFIER / REST_COMPOSURE_RATIO → 대체: rule 문장(휴식).
#   - CROSS_AXIS_CASCADE → 대체: 없음. 한 축의 나쁨이 다른 축을 깎는 것은 산문이 말할 일이다.
#   - DOWNTIME_RECOVER / _VICE / _SOCIALIZE / _PROJECT → 대체: 없음(다운타임 감지 폐지).
#     DOWNTIME_TRAIN 만 남는다 — domain_manager.advance_training 의 목표치 상수로 쓰인다.
# =========================================================

# G-2. 트라우마 각성 폐지 (2026-07-06 레티어스 결정): TRAUMA_DWELL_TURNS/REBOUND_VALUE/
# DEBUFF_TURNS/DEBUFF_MODIFIER 제거. 바닥 탈출 = 갭 비례 자연회복+휴식+間 리프레시.


# [2026-10-15 C42] Effort(각오) 퇴역 — EFFORT_BONUS·EFFORT_COST 삭제. 레티어스: "각오는 안 쓸 건데.
#   판정하기도 어렵고 정규식으로 파싱하자니 좀 그렇고." resolve 칸·활력 선불·Absorb 가 같이 갔다.

# [2026-08-11 로드아웃 삭제] 회상 비용(FLASHBACK_COST_TIERS/PASSIVE_DISCOUNT/MIN_MENTAL)과
# 로드아웃 슬롯(LOADOUT_SLOTS/SLOT_COST/TYPES) 상수 제거 — !회상 명령 폐기로 소비자 0.
# 자동 감지 산문 반영은 상수 없이 동작(플래그·문장만).

# =========================================================
# Inventory System (N2 — 아이템 영속 + 인벤토리 검증)
# =========================================================
INVENTORY_SLOT_CAP = 4  # 인벤토리 고정 4칸
# [notebook v2 2026-09-06] LLM이 쓰는 [메모] 줄('>' 색)의 상한. 킬스위치가 아니라 상한 상수다 —
# 유저 줄('-')은 이 상한과 무관하게 전부 보존된다. 초과 시 오래된 LLM 줄부터 탈락.
NOTEBOOK_LLM_MEMO_MAX = 12
# [경계 틱 2026-09-06 P2] 채널 스코프 일지(notebook_shared["일지"])의 줄 상한.
# 하루 1줄~3줄이 쌓이므로 열흘치가 컨텍스트 상한. 초과 시 오래된 날부터 탈락(행은 남는다).
NOTEBOOK_JOURNAL_MAX = 10
# 경계 콜 입력에 넣는 **어제 산문** 행 수 상한(최근 순). 각 행은 코드가 다시 1200자로 자른다.
BOUNDARY_PROSE_ROWS = 12
# [expr 2026-09-06 P3] 산문 재료 블록 예산(스펙 §3.5 "예산 고정"). env 레버 아님.
#   values = 값 줄 상한 / events = 전이 발화 `<사건>` 줄 상한(1턴만 사는 재료).
#   절단 순서는 코드가 안다: always 절대 안 자름 → 이번 턴 변화분 → 나머지 침묵.
#   ⚠ custom_vars.PROSE_FEED_MAX(=1200)와 이름이 겹치지만 축이 다르다 — 저쪽은 **문자** 캡,
#     여기는 **줄** 캡이다. 둘 다 물어야 "장부가 아니라 재료"가 유지된다.
#   [2026-09-13 P15] 12 → 40. 근거는 SimCore 내장 템플릿 실측이다: 전형 14종 중 7종
#     (rpg 19·business 15·survival 16·politics 14·vtuber 16·delve 17·zombie 17)이 12에서
#     이미 잘렸고, idol 의 promptState 자리표시자는 45, 아틀리에는 38이다. 임베드(핸드아웃)는
#     6000자/10장이라 그 규모를 다 싣는데 급식(앵커)만 12에서 잘리면 **GM 이 모르는
#     핸드아웃**이 생긴다 — 두 뷰가 같은 원천을 다른 규칙으로 자르던 자리가 여기다.
PROSE_FEED_MAX = {"values": 40, "events": 3}
# [조건부 지시 2026-09-13 P16] 조건이 참인 동안 매턴 산문에 실리는 유저 문장(`<지시>` 줄).
#   count = 한 턴에 실리는 지시 줄 수 / chars = 그 줄들의 글자 합. 선언 순서가 우선이고
#   넘치면 뒤엣것부터 탈락한다(로그 `[Directive] drop`). env 레버 아님 — 급식 예산은 코드 소유다.
#   ★값 줄(PROSE_FEED_MAX)과 갈라 둔 이유: 지시는 값이 아니라 **문장**이라 길이 축이 다르다.
#     한 예산으로 묶으면 지시 두 줄이 값 서른 줄을 밀어낸다.
DIRECTIVE_FEED_MAX = {"count": 8, "chars": 800}
# 채널당 지시 선언 상한. SimCore directives 실측(아틀리에 105·전형 3~7·idol 13) 위로 넉넉히.
MAX_DIRECTIVES = 40
# [append 기록 2026-09-09 P12] 이름 붙은 append-only 패널 섹션(mode="append").
#   저장은 무한(sqlite notebook_log 행) — 여기 있는 건 전부 **표시 캡**이다.
#   KEEP_DEFAULT = 턴 임베드 한 행이 보여주는 최근 줄 수(선언이 keep 을 말 안 했을 때).
#   KEEP_MAX     = 선언 keep 의 상한이자 💠 섹션 장이 보여주는 줄 수.
#   TEXT_MAX     = 한 항목 본문(도장 접두 제외)의 글자 캡. 항목 한 줄 = 몇 글자짜리 메모다.
#   FEED         = 패널 급식(Slot 29)에 각 append 섹션의 최근 1줄을 얹을지. 콜 0·줄 1.
APPEND_LOG_KEEP_DEFAULT = 3
APPEND_LOG_KEEP_MAX = 10
APPEND_LOG_TEXT_MAX = 120
APPEND_LOG_FEED = True
# 4.7 진입 시 채널 배경 큐 drain 대기(초). 스펙 §0.7(e) — 타임아웃이면 로그 1줄 + 진행.
#   렌더를 잡지 않는 것이 우선이라 짧다. `!다시` 가 쓰는 45.0(orchestration.retry_last)과 다른 수인 이유가 그것이다.
#   [2026-10-24 C74 W1-9] 옛 주석은 "10.0" — 코드는 45.0 이다.
EXPR_DRAIN_TIMEOUT = 3.0
# ⚠ 미배선 (2026-07-06 감사): 소비자 0 — 실제 아이템 영속은 notebook [소지품] 라인
# (cognition item_usage → add/remove_item_to_sojipin)이 담당. 참고 테이블로 보존.
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
# [2026-08-18 기본값 제거] 값의 주인은 .env 단독(VECTOR_EMBEDDING_MODEL). 코드 폴백 없음.
# 발화 경로: **gemini 분석 백엔드일 때만** 실제 임베딩 모델 ID로 콜에 실린다.
# openai(현행) 경로에선 analysis_backend.embed_content 가 model 인자를 무시하고
# ANALYSIS_OPENAI_EMBED_MODEL(voyage-4)을 쓰므로 이 값은 무효 — 손잡이는 그쪽.
VECTOR_EMBEDDING_MODEL = os.getenv("VECTOR_EMBEDDING_MODEL", "")
VECTOR_TOP_K = 10          # [1M remap 2026-06-22] 5→10 (프롬 2.92%/1M, 로어만 활성 truncation이라 살짝 확대)
VECTOR_MIN_SCORE = 0.2     # [1M remap] 0.3→0.2 (관련도 문턱 낮춰 더 admit)

# Weighted Memory Retrieval (LIBRA-inspired scoring)
MEMORY_SCORE_W_SIMILARITY = 0.4
MEMORY_SCORE_W_RECENCY = 0.35
MEMORY_SCORE_W_IMPORTANCE = 0.25
# [F1 2026-07-18] 발효 회상 evidence gate (FLASHBACK 이식 — Contract-First 조작화)
MEMORY_EVIDENCE_GATE = True
MEMORY_GATE_HIGH_SIM = 0.55       # 이 이상 벡터 유사도면 토큰 증거 없이도 통과
MEMORY_GATE_MIN_OVERLAP = 1       # 최소 쿼리-엔트리 토큰 겹침 (구체 증거)
MEMORY_GATE_RECENT_KEEP = 2       # 최신 K개 엔트리는 게이트 면제 (장면 꼬리 보장)
# [F3 2026-07-18] 회상 recency 반감기 + 선발 중복 억제
MEMORY_RECENCY_HALF_LIFE_ENTRIES = 4   # 엔트리 단위 반감기 (0.5^(age/H))

# [H3 2026-08-01] 작중 시간 감쇠 (HypaPlus 이식). recency에 곱해지는 두 번째 축.
#   recency = 0.5^(순번age/H) * story_factor**W,  story_factor = 1/sqrt(1 + 작중일수/S)
#   W=0 이면 story_factor**0 = 1 → 기존 동작과 완전 동일(한 줄 롤백).
#   타임스킵 없는 캠페인은 작중일수≈0 → story_factor≈1 → W와 무관하게 no-op.
# S 값별 감쇠 (W=1 기준): 작중 7일→0.90 / 30일→0.71 / 90일→0.50 / 360일→0.28  (S=30)
#                          작중 7일→0.96 / 30일→0.85 / 90일→0.69 / 360일→0.45  (S=90)
# 곡선을 지수가 아니라 1/sqrt로 잡은 이유: 타임스킵 이전 기억이 통째로 회상 불가가
# 되면 안 되기 때문. 완만한 꼬리를 남긴다.
MEMORY_RECENCY_STORY_WEIGHT = 1.0      # 0.0=끄기(기존 동작), 1.0=완전 적용
MEMORY_RECENCY_STORY_SCALE_DAYS = 30.0 # 작중 며칠이 지나야 0.71배가 되는가

# [H2 2026-08-01] noisy-OR 구제 슬롯. 가중합(AND 성향)이 밀어내는
# "오래됐지만 질의와 정확히 일치하는" 엔트리를 건져 올린다.
#   가중합 상위 POSITION개는 그대로 두고, 그 밖에서 noisy-OR `1-(1-sim)(1-rec)`
#   최상위 SLOTS개를 POSITION 자리에 끼워 넣는다. 점수는 안 건드리고 순위만 바꾼다.
#   밀려나는 건 가중합 기준 경계선 항목뿐 → 비용 유계. SLOTS=0이면 완전 no-op.
MEMORY_RESCUE_SLOTS = 1                # 0=끄기. 한 턴에 건져 올릴 최대 개수
MEMORY_RESCUE_POSITION = 3             # 이 순위까지는 가중합 결과 불가침

# ── [2026-08-11 엔티티 회상 채널] 발효 회상의 세 번째 축 ──
# 기존 스코어는 텍스트(sim)·시간(recency)·중요도뿐이라 **"누가 얽혀 있었나"를 보지 못했다** —
# 지금 무대에 선 인물이 등장하는 과거 엔트리를 우대할 채널이 0이었다.
#   쓰기: 발효 시 청크 턴범위의 narrative_tracker turn_log entities 빈도 상위 N개를
#         엔트리에 `entities`로 도장(코드 계측, LLM 콜 0).
#   읽기: (전경 NPC ∪ 쿼리 텍스트에 나온 이름) ∩ 엔트리 entities 겹침 수로 부스트.
# 결합은 무드일치 부스트와 **같은 문법의 유계 곱셈**. 가중합 3항(0.4/0.35/0.25)은 불변 —
# 재배분하면 기존 튜닝이 전부 흔들린다.
# no-op 3중: ①엔트리에 entities 키 없음(옛 엔트리) ②전경·쿼리 이름 집합 공집합
#            ③BOOST=1.0 설정 → 셋 다 부스트 1.0.
MEMORY_ENTITY_BOOST = 1.25             # 겹침 1명 (1.0=끄기)
MEMORY_ENTITY_BOOST_STRONG = 1.4       # 겹침 2명 이상
# 무드일치×엔티티 총 부스트 상한. ⚠ saliency 부스트 단독 최대가 2.0(IMPORTANCE_BOOST_CURVE)이라
# 이 캡보다 크다 — 그래서 코드는 **캡이 무드 단독값보다 낮으면 무드 단독까지만** 내린다.
# 엔티티 채널이 감점원이 되면 안 되기 때문(부스트는 더하기만 한다).
MEMORY_ENTITY_BOOST_MAX = 1.8
MEMORY_ENTITY_STAMP_TOP_N = 8          # 발효 엔트리에 도장 찍을 인물 수 (빈도 상위)

# ── [C1 2026-08-01] LLM 제안 수치 델타의 코드측 상한 (SimCore "선언 = 집행") ──
# 프롬프트에 적은 범위를 **여기에 같은 값으로** 둔다. 지금까지는 프롬프트에만 있어서
# 집행이 없었다 — 범위 클램프(0~100)는 델타 클램프가 아니라, 한 턴에 5→100이 통과했다.
# 적용은 bot_utils.cap_llm_delta. 잘리면 [DeltaCap] WARNING 1줄(조작면 순증 0).
#
# ⚠ **키는 주체(source) 라벨이다.** 같은 세터를 코드도 쓴다(다운타임 사교 depth +10~15,
#    NPC 시트 initial_depth, trajectory 맵) — 그 경로는 여기 없으므로 무캡이다.
#    무차별 캡은 정상 설계를 자른다(SimCore "정상 설계에 경고를 내지 않는다"의 코드판).
#
# 동기화 검증: smoke_llm_delta_caps.py 가 프롬프트 원문의 선언 문구를 파싱해 이 표와 대조.
# 값을 고치면 프롬프트도 같이 고쳐야 스모크가 통과한다.
LLM_DELTA_CAPS = {
    # [2026-09-15 관계 통합] helena.cognition(npc_depth_hints)·helena.fermentation(helena_delta) 삭제 —
    #   두 델타 질문 자체가 없어졌다. NPC→PC 이동폭 캡은 sqlite_store.upsert_edge(origin="theoria")가 쥔다.
    # theoria_analyzer.py — "clock_updates": [{"delta": int(-1~+2)}]
    "doom.clock":          {"delta": (-1, 2)},
    # cognition.py `### social` — "delta ±0.1~0.3 (modify existing)"
    "relation.intensity":  {"delta": (-0.3, 0.3)},
}
MEMORY_DEDUP_JACCARD = 0.6             # 선발 시 기선발과 토큰 자카드 ≥ 이 값이면 스킵 (0=끔)
# [2026-09-06 P8b] 다운타임 활동별 회복/차감 테이블 삭제(위 WHY 블록). 훈련 목표치만 남는다.
DOWNTIME_TRAIN = {"vigor_cost": 5, "composure_cost": 5, "progress_per_session": 1, "required_progress": 3}

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
# [2026-10-24 C74 판2] 이제 장부 목표: N턴 동안 진행도(step)가 안 움직이면 pause 행(code:stale — 실패 아님, 진전 = 자동
#   resume). 자리는 game_world.increment_turn_index(thread_ledger.pause_stale_goals). 이름은 옛 그대로.
QUEST_STALE_ARCHIVE_TURNS = 12
# [2026-10-25 C74 판4] 진행 중 블록(in_motion) — 아크 덩이 상한 · 진전 없는 목표 꼬리표 턴(옛 une_facade 8턴 약화 줄).
IN_MOTION_ARCS_MAX = 5
IN_MOTION_STALE_TURNS = 8

# =========================================================
# ARC SYSTEM (호흡 3단 위계의 큰 호흡 — 중력 우물)
# =========================================================
# 시계 = 천 위의 점(질량의 주인) / 목표 = 빛 / Arc = 시계가 쌓인 우물(중심 고정, 무게 = id 수, 거리 = 홉)
# [2026-10-24 C75 G2] 아크는 줄거리(is_arc)에서 중력 우물(well_store · sqlite well_log)로 이사했다. 옛 좌표 5축 ·
#   감쇠 · 잠듦 · 격상 · 초신성 상수(ARC_EXPECTED_VOLUME_LENGTH · ARC_DORMANT_BASE_TURNS · ARC_TRAJECTORY_CAP ·
#   ARC_PROMOTE_CATEGORY_MIN · ARC_SUPERNOVA_*)는 읽는 곳과 함께 은퇴. 남은 것 = 노출 문턱 · 고리 · 표시 상한.
#   스펙: 파티쳇수정/output_artifact/C75_G2_옮기기·강착·관측_지도·스펙_2026-10-24.md

# 노출 / 고리
ARC_PROXIMITY_EXPOSURE_THRESHOLD = 0.3  # 산문 노출 최소 근접(1/(d+1) — 2홉 안). 전경/배경 분기 임계도 동일
ARC_PHASES_CAP = 10                     # 지나온 장면 이름표 고리
ARC_FORESHADOWING_CAP = 20              # 전조 칸 고리
ARC_OFFSCREEN_ACTIONS_CAP = 20          # 막후 칸 고리
ARC_FORESHADOWING_DISPLAY_CAP = 5       # 산문 노출 최대 (토큰 budget)
ARC_OFFSCREEN_DISPLAY_CAP = 5           # 산문 노출 최대

# Multi-arc 합성 (산문 노출)
ARC_FOREGROUND_CAP = 1                  # 동시 노출 전경
ARC_BACKGROUND_CAP = 2                  # 동시 노출 배경
# [2026-10-24 C75] 중력 우물 잠정값 — 스펙 C75 §18 · G2 §12. G1 의 WELL_SHADOW(그림자 켬끔)는 G2 에서 은퇴.
WELL_MASS_LIMIT = 16                    # N — 무게 = 질량 ÷ N (옛 박자: 0.3 → 0.95 를 전경 흡수 +0.04 로 16번)
WELL_P_CAP = 1.0                        # 잡힘 · 무대 위 — 먼지가 제자리 다음 시계를 잡는 값
WELL_P_BEND = 0.25                      # 휨 · 배경 — 빛 출처 · 보이는 우물 문턱
WELL_NAME_MASS = 4                      # 이름 얻기 후보 질량(닿는 거리 1칸)
WELL_HAWKING_K = 4                      # 호킹 수명 τ = k · M³ 턴(먼지 4 · 둘 32 · 이름 우물 256)
WELL_RECONCILE_WINDOW = 3               # 시계 맞춤이 끝난 시계를 보는 턴 창(그보다 옛 끝으로 우물을 거꾸로 짓지 않음)
WELL_LIGHT_FROM = "emission"            # 이변 밝기: 빛 난 자리 → PC(레티어스 10-24 밤). "center" = 본 스펙 옛 식(출처 중심 → PC)
# [2026-10-25 C75 G3] 결전 · 멀리서 무너짐 · 충돌파 · 목표 휨 — 스펙 C75_G3_시드·결전·무너짐·충돌파_지도·스펙_2026-10-25 §8
WELL_GOAL_BEND = 2.0                    # 끝난 목표가 그 우물 쪽으로 휜 것으로 치는 속도 M ÷ (r+1) 문턱(성장 셈)
WELL_COLLIDE_MASS = 9                   # 충돌파 — 둘 다 이 질량 이상(닿는 거리 2칸)
WELL_COLLIDE_REGROW = 4                 # 같은 쌍이 다시 부딪히려면 둘 다 이만큼 더 먹어야
WELL_CLIMAX_MAX = 12                    # [G3 검토 #4] 결전 최대 길이(턴) — 넘기면 결판(배치가 장면 넘어감을 못 냈어도). 0 = 끝없음
WELL_REMNANT = 4                        # 멀리서 무너진 우물이 남기는 id 수(이름은 남는 최소)

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

NPC_TENSION_DRAMA_THRESHOLD = 50

# =========================================================
# [2026-08-11 사망 파이프라인] NPC 생존축 enum
# =========================================================
# `npcs.data["status"]` 자리는 06월부터 있었지만(WHERE 인덱스까지 완비) 값은 생성 도장
# "active" 리터럴 하나뿐이었다 — 쓰는 코드 0, 읽는 소비자 0. 죽음은 오직 "모델이 그
# 텍스트를 다시 읽기를" 기대하는 방식으로만 유지됐고, 막간·오프스크린·자동 재등록이
# 시체를 매 턴 무대로 되돌렸다.
#
#   active … 기본. 무대 후보로 정상 순환.
#   down   … 생사불명·무력화. **가역** — 재등장 관측 하나로 풀린다.
#   dead   … **비가역**. 자동 경로는 이 값을 못 쓰고, 해제도 수동 명령만.
#
# ★권한 분리가 이 enum의 본체다. 자동 경로(LLM 관측)는 가역 상태까지만 만든다.
#   근거=실패 비용 비대칭. 미탐(죽었는데 못 잡음)은 **발생형**이라 다음 턴 산문에서
#   눈에 띄고 수동 명령 한 줄로 고친다. 오탐(안 죽었는데 dead)은 **반사실형**이라
#   그 인물이 조용히 캐스트에서 사라질 뿐 로그에 아무것도 안 찍힌다.
#   비가역 전이를 LLM에 주면 안 되는 이유가 이것이고, 같은 문법이 비밀 원장의
#   revealed/retired 후퇴 금지 게이트(sqlite_store.upsert_secret)에 이미 서 있다.
NPC_STATUS_VALUES = ("active", "down", "dead")
NPC_STATUS_IRREVERSIBLE = ("dead",)   # 이 값으로/에서 나가는 전이는 source=="manual"만

# [2026-09-15 관계 통합 1차] 감쇠는 한 곳 — sqlite_store.decay_edges(relations 엣지).
#   시계 = 엣지 last_turn(마지막 관측). grace 턴 넘게 안 관측되면 bond는 0으로 수렴, tension은 하한 0.
#   **삭제가 아니라 흐려짐**(엣지는 남는다). 끄기: RELATION_DECAY_GRACE = 0.
#   (구: npc_attitudes depth/tension 감쇠 + entity_relations intensity fade 둘 따로, 시계도 달랐다.)
# [2026-09-26 S7] 감쇠 목표 = 엣지 기준선(base_bond/base_tension — 시작값). 기준선 0이면 옛 동작(0 수렴) 그대로.
RELATION_DECAY_GRACE = 10      # 이 턴 수만큼 무관측이면 그때부터 감쇠 (0 = 기능 끔)
RELATION_DECAY_DEPTH = 1       # 감쇠 턴당 |bond| 하락폭 (0 쪽으로)
RELATION_DECAY_TENSION = 2     # tension은 더 빨리 식는다 (갈등은 관계보다 휘발성)

# =========================================================
# [2026-08-02 C축] DRIVE — 해소되지 않은 충동이 누적되어 행동을 강제하는 압력
# =========================================================
# 왜 신설: A축(관계·축적형)·B축(신체·반응형)은 재료가 있는데 **압력형만 없었다**.
#   인접 후보 전수 기각 — doom=씬 레벨 / Arc=장기 호흡 / needs=상태(누적 아님) /
#   D2 감정부채=로그 판독(변수 아님) / entity_relations "debt"=관계 종류 라벨.
#
# ★수치 게이지를 만들지 않는다. 레티어스 원칙 "일부러 수치적 상태를 안 준 거야" —
#   노출 층이 전부 수치를 단계로 덮고 있고(depth→5단계, intensity→light/medium/deep,
#   vigor→4단계, spike 델타→↑↓만), LLM에겐 아예 수치를 안 준다.
#   그래서 본은 `cap_llm_delta`(수치 캡)가 아니라 **`npc_manager.set_drive_gated`**다(구 M5 태도 게이트와
#   같은 문법 — 태도 게이트는 2026-09-15 관계 통합으로 삭제): enum 단계 + 쿨다운 + ±1단계 클램프.
#
# 일반형이라 NSFW 밖에서도 산다: 전투=전의·복수심, 연애=갈망, 드라마=못 한 말.
# 끄기: DRIVE_ENABLED = False → 전면 no-op.
DRIVE_ENABLED = True

DRIVE_STAGES = {
    "none":       {"level": 0, "hint_en": "no unresolved pull; attention is free"},
    "faint":      {"level": 1, "hint_en": "a pull at the edge; noticed, set aside"},
    "disrupted":  {"level": 2, "hint_en": "the pull competes with thought; attention keeps returning"},
    "driven":     {"level": 3, "hint_en": "the pull changes what is chosen; goals bend around it"},
    "impulse":    {"level": 4, "hint_en": "impulse moves before deliberation, without erasing "
                                          "cognition, identity, target, or defense"},
}
DRIVE_LEVEL_TO_STAGE = {v["level"]: k for k, v in DRIVE_STAGES.items()}

# 압력형의 비대칭 — 천천히 차고 빠르게 빠진다.
DRIVE_RISE_COOLDOWN = 2   # 상승은 이 턴 수 간격 (관계 3턴보다 짧게: 압력은 빠르게 찬다)
DRIVE_RISE_MAX_STEP = 1   # 상승은 한 번에 1단계
DRIVE_RELEASE_FREE = True # 해소 이벤트는 쿨다운·단계 제한 면제 (다단 하강 허용)
DRIVE_IDLE_TURNS = 6      # 무변화 이 턴 수마다 1단계 자연 하강 (0 = 자연 하강 끔)

def get_connection_stage(depth: int) -> Dict[str, Any]:
    """커넥션 단계 정보 반환.

    [2026-09-15 관계 통합] depth 자리가 bond(−100~+100)가 됐다. 음수(적대 쪽)는 표의 어느 구간에도
    안 들어가 **맨 끝 폴백 '유대'로 떨어지던** 자리 — 음수는 가장 낮은 단계로 고정한다."""
    try:
        if depth < 0:
            _first = next(iter(NPC_CONNECTION_STAGES))
            return {"name": _first, **NPC_CONNECTION_STAGES[_first]}
    except TypeError:
        pass
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
    "doom": 0,  # [C90 10-28] 30(옛 "Unsettled Start") → 0 — 첫 챕터도 起부터(레티어스). 새 채널 · !클리어 에만 닿는다
    "risk_level": "None",
    "current_location": "Unknown",
    "location_rules": {},
    # [2026-10-24 C76 §5] "world_constraints" 은퇴(로어 분석이 산문용으로 다시 쓴 세계 규칙 — 읽는 곳 0).
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
    # 가시 3개: 능동적 창작 마찰 (AGELAST 원본의 구문 제약 계열 — 자료 존 끝(Slot 31 뒤)으로 라우팅, C43 전엔 Slot 19)
    # [2026-09-29 배치2 2a-4] 가시 3면 효과문 → 영어 값(턴 존의 다른 값과 같은 언어). 뜻은 그대로.
    #   Agon은 slot_manager `_any_speaking_npc` 게이트를 지난 턴에만 실린다.
    "agon":    {"name": "Agon/적",     "visible": True,  "effect": "At least one NPC line this turn is a question; flat statements thin out."},
    "alea":    {"name": "Alea/운",     "visible": True,  "effect": "One object, sound, or presence the scene had not planned enters."},
    "mimicry": {"name": "Mimicry/역할", "visible": True,  "effect": "One sensory distortion enters: a POV shift, a slip in time sense, a colour bias, or synaesthesia."},
    # 은닉 4개: 수동적/부재의 힘 — [2026-09-29 배치2 2a-3] 프롬프트에 싣지 않는다(굴림·기록 전용).
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
    "고양": {"type": "buff", "severity": 0},   # [2026-10-07 B6] 옛 "활력" — 게이지 이름과 겹쳐 개명
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

# [2026-10-07 활력평형 B6] 저장돼 있던 옛 버프 → 새 이름. game_character._normalize_effect_dict 가 읽을 때 바꾼다.
STATUS_TAG_RENAMES = {"vigor": "inspired"}
STATUS_NAME_RENAMES = {"활력": "고양"}

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
    "inspired": {"name": "고양", "type": "buff", "severity": 0, "modifiers": {"judgment": 5}},   # [2026-10-07 B6] 옛 tag vigor·이름 활력
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
    "고양": "inspired",
    "활력": "inspired",   # [2026-10-07 B6] 옛 버프 이름 입력 호환(게이지 활력과는 다른 것 — 상태효과 표 안에서만)
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

# SEVERITY_DOOM_IMPACT 제거 (2026-07-06 감사): 소비자 0 — status-impact→doom
# 라인(D-1)이 이미 잘린 뒤 남은 테이블.

# [2026-08-11 비일상적응도 삭제] 이 파일에서 함께 제거된 것들:
#   ① NORMALITY_STAGES + get_normality_stage_info (정상성 5단계 — 소비자 0)
#   ② ADAPTATION_TAXONOMY + get_parent_category (노출 태그 33종 + 상위 전이 규칙 — 소비자 0)
#   ③ DEFAULT_RULES 안의 "## 🌓 비일상 적응" 섹션 — 노출 카운트를 올리는 코드가 없는데도
#      규칙문이 적응 진행을 약속하고 있었고, get_rules → NPC 증류 접지 블록으로 LLM에 실렸음.
# 복원은 git 이력.

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
# [2026-10-06 어휘 V3] 서술 결을 말하는 줄의 라벨 "Voice" → "Narration"(정적 규칙에서 Voice = 인물 목소리,
#   narration = 서술자의 글). 느와르 起·承·轉의 "present-tense"/"present" 삭제 — 추론 43%가 시제를 저울질했고
#   과거형 히스토리 위에서 현재형으로 뒤집힌 판이 나왔다(산문 4.4%, 그중 87%가 이 줄 인용).
#   인물 말을 그리는 줄(romance 結 "Voice softening — first direct utterance…")은 Voice 유지.
#   스펙 composition/어휘엇갈림·이중주입_수리스펙_2026-10-06.md §2.1.
# 4원리 적용: state-only (no imperatives), no negation, typological palette, no author names.
# 다중 lens 활성 시 양쪽 block 모두 노출 + "neither erases" hybrid 디렉티브 (une_facade 처리).
LENS_DOOM_ATMOSPHERE = {
    "noir": {
        "起": ("Surface routine over latent currents. Information moving below speech.\n"
              "Narration: hardboiled. Direct, kinetic, terse.\n"
              "Procedural moments carrying weight beyond their surface."),
        "承": ("A name dropped with weight. Eye contact extending past comfort.\n"
              "Information surfacing as currency. Watching becomes mutual.\n"
              "Narration: kinetic. Black humor under pressure."),
        "轉": ("Surveillance closing in. Paths narrowing. Pressure amplifying through quiet.\n"
              "Narration: hardboiled, kinetic. Black humor as armor.\n"
              "Procedural moments carrying menace. Information as weapon."),
        "結": ("Cold reveal in stark light. Information landing as betrayal as recognition.\n"
              "Clipped dialogue. Cuts that hit.\n"
              "Truth surfacing through evidence and pressure."),
        "間": ("Aftermath absorbing into routine. Tension receding.\n"
              "Narration lower now, still hardboiled. Scars settling.\n"
              "New stories already moving beneath surfaces."),
    },
    "comedy": {
        "起": ("Easy rhythms, predictable beats. Small obstacles played for warmth.\n"
              "Narration: theatrical, attentive to incongruity. Light tone holding.\n"
              "Body humor and verbal wit in equal measure."),
        "承": ("Small fictions multiplying. Schedules colliding under their own logic.\n"
              "Narration still light but quicker. Wit sharpening through pressure.\n"
              "Audience seeing more than the players. Sympathy and ridicule intertwined."),
        "轉": ("Cover stories spawning new layers. Each fix introducing two new tangles.\n"
              "Narration accelerating. Body humor amplifying — slips, doubles, mistimings.\n"
              "Absurdity peaking. Characters caught in their own webs."),
        "結": ("Peak chaos. Masks slipping in overlapping confrontations.\n"
              "Narration fastest, theatrical and self-aware. Body humor at maximum.\n"
              "Recognition through absurdity itself. Laughter as resolution mechanism."),
        "間": ("Aftermath played soft. Embarrassment lingering with warmth.\n"
              "Narration settling, lighter again. Insight emerging through the absurd.\n"
              "What was uncovered moving into shared memory, easier now."),
    },
    "romance": {
        "起": ("Easy peace, days holding their shape. Glances starting to register.\n"
              "Narration: free indirect, attentive to interiority. Restraint as default.\n"
              "Internal weather setting in. Body cues registering before mind admits."),
        "承": ("Attraction sharpening. Banter carrying weight under wit.\n"
              "Withholding louder than utterance. Restraint shaping every choice.\n"
              "Internal weather thickening. Shared air growing dense."),
        "轉": ("The misunderstanding crystallizing. Distance opening through what was unsaid.\n"
              "Narration still restrained, more so. Internal storm beneath the calm exterior.\n"
              "Time stretching across the held silence. Long dwell before any movement."),
        "結": ("The decisive vulnerability. Restraint giving way through gesture.\n"
              "Voice softening — first direct utterance carrying weight built across the dwell.\n"
              "Body and word arriving together. Recognition through declared feeling."),
        "間": ("A new equilibrium. Internal weather settled but altered.\n"
              "Narration quieter, intimate now. Shared atmosphere thickening into permanence.\n"
              "What was declared moving into shared body of routine."),
    },
    "drama": {
        "起": ("Quiet inhabited atmosphere. Routine has its own gravity.\n"
              "Narration: restrained, attentive. Observation-led.\n"
              "Body knowing before mind notices. Small details bearing the weight."),
        "承": ("Subtle dissonance under a restrained surface. Atmosphere a slow barometer.\n"
              "Narration carrying restraint. Observation-led.\n"
              "Silence speaking louder than utterance."),
        "轉": ("Quiet recognition through muted gesture. Meaning slipping sideways.\n"
              "Time stretching around the moment of seeing. The dwell holds.\n"
              "Small over large. Recognition over revelation."),
        "結": ("A decisive small moment. Truth surfacing in what is seen.\n"
              "Body bearing what stays outside speech. Restraint holding.\n"
              "Quiet resolution; the thing becoming known."),
        "間": ("The residue of recognition. Familiar surfaces engaged differently after seeing.\n"
              "Narration quieter still. Body remembering, anchored in itself.\n"
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


def chapter_climax(lens_tags=None, flavor_tags=None) -> int:
    """[C90 10-28] 이번 턴 클라이맥스 문턱 = 렌즈 climax 평균(없으면 default) + 플레이버 문턱 오프셋 합.
    doom_module._compute_climax_threshold 와 페이즈 경계(chapter_phase)가 같은 값을 쓴다."""
    lens_tags = [l for l in (lens_tags or []) if isinstance(l, str)]
    if lens_tags:
        ths = [LENS_DOOM_CURVE.get(l, LENS_DOOM_CURVE["default"]).get("climax", 80) for l in lens_tags]
        base = sum(ths) / len(ths)
    else:
        base = LENS_DOOM_CURVE["default"]["climax"]
    offset = 0
    for f in flavor_tags or []:
        offset += (FLAVOR_DOOM_MODIFIER.get(f) or {}).get("threshold_offset", 0)
    return int(base + offset)


def chapter_phase(doom_value, lens_tags=None, flavor_tags=None) -> str:
    """[C90 10-28] 볼륨 → 페이즈. 경계를 문턱 c(chapter_climax)에서 만든다:
    起 0~25 · 承 25~55 · 轉 55~(c−5) · 結 (c−5)~c · 間 c~.
    렌즈 하나 · 플레이버 없음이면 옛 LENS_DOOM_PHASE_RANGES 와 같다(스모크 대조). 옛 길(get_lens_phase)은 첫 렌즈의
    표를 써서, 렌즈 둘 · 플레이버 오프셋이면 間 경계 < 문턱 → 배율 0 에서 클라이맥스가 안 터지고 멈췄다."""
    c = chapter_climax(lens_tags, flavor_tags)
    try:
        v = float(doom_value)
    except (TypeError, ValueError):
        v = 0.0
    if v < 25:
        return "起"
    if v < 55:
        return "承"
    if v < c - 5:
        return "轉"
    if v < c:
        return "結"
    return "間"


def get_lens_atmosphere(lens: str, phase: str) -> str:
    """렌즈 + 페이즈 → atmosphere block. lens placeholder면 default fallback."""
    block = LENS_DOOM_ATMOSPHERE.get(lens, {}).get(phase)
    if block:
        return block
    return LENS_DOOM_ATMOSPHERE["default"].get(phase, "")

# =========================================================
# Vigor / Composure 2-Axis System (v3.0)
# =========================================================
# Vigor = 활력 (몸 축): 전투, 부상, 수면 부족, 과로 — [2026-10-07] 의지·공포는 평형 축
# Composure = 평정 (정신 + 사회): 정신 충격, 배신, 수치, 고립, 정보 과부하

# VIGOR_STAGES alias 제거 (2026-07-06 감사): 소비자 0. 단계 판정은 vigor_composure._get_stage.

COMPOSURE_STAGES = {
    0: {"name": "안정", "emoji": "😌", "range": (70, 101), "desc": "정신적으로 안정된 상태"},
    1: {"name": "흔들림", "emoji": "😰", "range": (40, 70), "desc": "감정적 동요가 있습니다"},
    2: {"name": "동요", "emoji": "😱", "range": (15, 40), "desc": "정신적 한계에 가깝습니다"},
    3: {"name": "붕괴", "emoji": "🫥", "range": (0, 15), "desc": "정신이 무너진 상태입니다."},
}

# ⚠ 미배선 (2026-07-06 감사): 소비자 0 — 실제 primary axis는 렌즈 계열
# LENS_PRIMARY_RESOURCE → build mechanic(primary_resource) 경로가 결정. 여길 고쳐도 무효.
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
# ⚠ 미배선 (2026-07-06 감사): 소비자 0 — 실제 disruption_axes는 렌즈 계열(mechanic dict)이 공급.
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
        "desc": "감정적 혼란과 사회적 압박이 평형을 흔든다",
    },
    "comedy": {
        "primary_axis": "composure",
        "defense_stat": "composure",
        "defense_theory": "Chaemyeon+Goffman",
        "trigger_bonus": 5,
        "secondary_ratio": 0.2,
        "desc": "체면 위기와 상황 폭주가 평형을 시험한다",
    },
    "noir": {
        "primary_axis": "composure",
        "defense_stat": "composure",
        "defense_theory": "ToM+CoK+Statement",
        "trigger_bonus": 0,
        "secondary_ratio": 0.3,
        "desc": "심리적 압박과 진실의 무게가 평형을 갉아먹는다",
    },
    "action": {
        "primary_axis": "vigor",
        "defense_stat": "vigor",
        "defense_theory": "Prospect+BATNA",
        "trigger_bonus": 5,
        "secondary_ratio": 0.3,
        "desc": "물리적 위협과 전장의 혼란이 활력을 소모시킨다",
    },
    "slice_of_life": {
        "primary_axis": "composure",
        "defense_stat": "composure",
        "defense_theory": "Lazarus+Reactance",
        "trigger_bonus": -10,
        "secondary_ratio": 0.2,
        "desc": "일상의 변화와 자율성 위협이 평형을 흔든다",
    },
}

# NPC Autonomous Behavior (Phase 7)
NPC_AUTONOMOUS_ENABLED = True

# =========================================================
# V10 — 상태 우선 아키텍처 플래그
# =========================================================
# Sprint 1 관계 플래그(V10_RELATIONS_READ_FROM_SQLITE)는 2026-09-15 관계 통합으로 삭제 —
#   관계는 relations 엣지 하나뿐이라 JSON 폴백 경로 자체가 없다.
# Sprint 2-A: NPC 지식(npc_knowledge) / 2-B: NPC 본체(npcs).
# 2026-06-10 셋 동시 ON (사용자 결정): 도메인 독립 + lazy migration이라 빈 테이블에서도 안전
# (첫 읽기는 JSON 폴백 → 자동 이주). 문제 시 셋 다 False로 즉시 V9 복귀.
V10_KNOWLEDGE_READ_FROM_SQLITE = True
V10_NPCS_READ_FROM_SQLITE = True
# Sprint 3 Phase 3 (2026-09-05): fermented/deep 읽기를 SQLite에서. JSON 키는 dual로 계속 씀(P4 전까지).
# False = 읽기 JSON(현행). 테이블은 _sync_history_domain 미러로 이미 채워져 있어 ON이 안전. 문제 시 이 줄 False.
V10_HISTORY_READ_FROM_SQLITE = True
# Sprint 3 Phase 4 (2026-09-05): 세 키(fermented_history·deep_memory·deep_memory_data)를
# JSON에서 뺀다 — 행이 정본. save_domain 이음매(_route_history_keys_to_rows)가 라우팅한다.
# False = P3 상태(dual)로 즉시 복귀(행은 계속 씀, JSON에도 다시 쌓임).
# ★ V10_HISTORY_READ_FROM_SQLITE=True 전제 — STRIP만 켜면 읽을 곳이 사라진다.
V10_HISTORY_STRIP_JSON = True
# Sprint 4: 막간 장부 (침묵 틱) — 장면 밖 NPC 행적을 코드로 전진시켜 기록.
# 발화 0/콜 0/루프 0. OFF = 완전 무동작. spec: v10_sprint4_interim_ledger_spec.md
# 2026-06-11 ON (스모크 35케이스 PASS 후). 문제 시 이 줄 False = 즉시 완전 무동작.
V10_INTERIM_LEDGER = True
# [V10 적립 활용] 발효 압축 시, 그 청크 턴범위의 감정/태도/페이즈 호(弧)를 코드로 뽑아
# 발효 프롬프트에 보조 주입(콜 0, 영어 텔레그래픽=echo-safe). graceful-empty: 장부 비면 무주입,
# 데이터 쌓이는 만큼 자동 활성. 문제 시 이 줄 False = 즉시 무동작.
# 2026-06-19 ON (사용자 결정 — 장부 적립 중, graceful-empty라 위험 0).
V10_ARC_DIGEST_FERMENT = True

# [2026-09-13 S0] 발효 인용 게이트 — 프롬프트 evidence 요청 + 코드 substring 판정 + 시제/확신 필드.
#   OFF = 종전 동작 그대로(프롬프트·엔트리·로그 무변경).
V10_HISTORY_EVIDENCE = True           # 09-13 S0 스모크 51/51 GREEN → ON
FERMENT_EVIDENCE_MAX_PER_BLOCK = 2    # important 블록당 인용 상한
FERMENT_EVIDENCE_MAX_CHARS = 160      # 인용 1개 상한(정규화 후). 하한 2
FERMENT_OUTPUT_CAP_WARN_RATIO = 0.95  # 출력 예산 대비 경고 임계(LIBRA 42641)

# [2026-09-13 S2] T1 재등장 인물 원문 근거 + F2 원문 승격. V10_HISTORY_EVIDENCE 하위.
RECALL_EVIDENCE_MAX_NPCS = 3
RECALL_EVIDENCE_ROWS_PER_NPC = 2
RECALL_EVIDENCE_LINE_CHARS = 200
RECALL_EVIDENCE_SEARCH_LIMIT = 4

# [2026-09-13 S1] Slot 9 읽기 계약 보강·꼬리표·시간순 구획·회상 영수증. OFF = 종전 렌더 바이트 동일.
MEMORY_READ_CONTRACT = True
MEMORY_TRACE_MIN_TOKEN_LEN = 2   # ④→① 흔적 겹침에서 세는 토큰 최소 길이

# [2026-09-13 S3] 회상 선택 산수 v2. OFF = S1 상태 그대로.
MEMORY_SELECT_V2 = True
MEMORY_PAST_INTENT_MARKERS = ("언제", "그때", "그 때", "전에", "했었", "기억나", "기억 나", "처음에", "지난", "예전", "옛날", "그날", "그 날")
MEMORY_PAST_RECENCY_CAP = 0.06          # 과거 의도면 w_rec <= 이 값(LIBRA 30098)
MEMORY_LADDER_CHARS = (5000, 6500, 8400)  # 에피소드 예산 사다리(자). 마지막 = 종전 상한
MEMORY_LADDER_MIN_REL_SCORE = 0.6       # 다음 후보 점수 >= 상위 점수 x 이 값일 때만 확장
MEMORY_GREEDY_FILL = True               # 미적합 엔트리를 건너뛰고 더 작은 후보로 채움
MEMORY_STABILITY_BONUS = 0.025          # 지난 턴 실제 주입된 엔트리 가산(HAYAKU 29149)
MEMORY_STABILITY_TTL_SEC = 600
MEMORY_JOINT_QUERY_TAIL = 2             # 키워드 쿼리에 붙일 직전 메시지 수(벡터 경로와 동일)

# [2026-09-13 S4] 루카 과거형 질문 -> history_log 발췌. V10_HISTORY_EVIDENCE 하위.
#   마커는 MEMORY_PAST_INTENT_MARKERS(S3) 공유 — 여기서 재정의하지 않는다.
LUKA_RECALL_ROWS = 6
LUKA_RECALL_CHARS = 1200
LUKA_RECALL_LINE_CHARS = 160
LUKA_RECALL_KEYWORDS = 3

# [2026-09-14 S5a] 지식 사실 출처 원장 + 영속 감사 각주. OFF = 쓰기·각주 모두 없음.
V10_FACT_SOURCES = True            # 09-14 S5a 스모크 81/81 GREEN → ON
FACT_SOURCE_FACT_CHARS = 200     # fact_norm 저장 상한(정규화 후)
FACT_SOURCE_AUDIT_MAX = 20       # 감사 각주 조회 상한(항목 수)

# [2026-09-14 S5b] 계보 잔여(E12 기각 전파 · E14 Observed 가산성 · F3 GC 보호).
#   전부 바닥(표시·감점·복원) — 삭제·드롭 0. OFF = 종전 바이트 동일.
MEMORY_LINEAGE = True            # 09-14 S5b 스모크 73/73 GREEN → ON
MEMORY_UNGROUNDED_PENALTY = 0.85        # E12: 인용을 하나도 못 댄 important 블록의 엔트리 점수 배율
MEMORY_UNGROUNDED_MARK = "(미검증)"      # E12: Slot 9 렌더 꼬리 · 패치 content 머리
WIKI_OBSERVED_ADDITIVE = True           # E14: Observed upsert를 문장 단위 병합으로(누락 보존, retract만 삭제)
WIKI_OBSERVED_KEEP_ORDER = "new_first"  # 병합 순서: F1이 낸 본문 먼저, 보존 문장 뒤
MEMORY_GC_PROTECT = True                # F3: 보호 항목을 GC 결과에 복원

# [2026-09-14 W1] 내부 위키 페이지. OFF = 테이블은 생겨도 쓰기·읽기 0.
#   설계: memory_lore/internal_wiki_spec_2026-09-14.md §2·§3·§4·§7 / 지시서 wiki_w1_pages_todo.
#   W1 범위 = character 페이지만(생산자가 있는 kind). 나머지 kind는 테이블·API만 받는다(W2).
WIKI_PAGES = True             # 09-14 W1 스모크 117/117 + 회귀 63 GREEN → ON
# [2026-09-16 레티어스] 저장 캡 없음 — 아래 두 값은 더 이상 저장 거부·절단에 쓰지 않는다(스모크 픽스처 크기 기준으로만 잔존).
#   lore 절(작가 원문)은 정본이라 절단 불가, Observed는 grow_sheet 정리 콜(+250자)이 길이 관리.
WIKI_PAGE_MAX_CHARS = 16000
WIKI_TEXT_SECTION_MAX_CHARS = 4000
WIKI_ITEM_SECTION_MAX = 60
WIKI_KINDS = ("character", "location", "faction", "item", "concept", "other")
WIKI_LORE_SECTIONS = {"character": ("Identity", "Core Traits", "Aside", "Direction", "Relationships", "Secrets", "Background", "Notes"),
                      "location": ("Identity", "Layout"), "faction": ("Identity", "Stance"), "item": ("Identity",),
                      "concept": ("Body",), "other": ("Body",)}
WIKI_PLAY_SECTIONS = {"character": ("Observed", "Relationships+", "Knowledge", "History"),
                      "location": ("Observed", "History"), "faction": ("Members+", "Observed", "History"),
                      "item": ("Observed", "History"),
                      # [2026-09-14 W5] concept = 모음 페이지(장소 모음·세력 모음) — `Entries`가 잎 줄이다.
                      "concept": ("Entries", "Observed", "History"), "other": ("Observed", "History")}
WIKI_ITEM_SECTIONS = ("Relationships+", "Knowledge", "History", "Members+", "Entries")   # 항목 절(줄 단위), 나머지는 텍스트 절

# [2026-09-14 W2] F1 발효가 페이지 play 절을 패치한다. OFF = 프롬프트·엔트리·행 종전 동일.
#   설계 §4(F1 행·패치 적용·되감기·영수증) / 지시서 wiki_w2_patches_todo. WIKI_PAGES 하위.
WIKI_PATCHES = True           # 09-14 W2 스모크 81/81 + 회귀 64 GREEN → ON
WIKI_PATCH_MAX_PAGES = 6          # 한 발효에서 노트를 주고 패치를 받는 페이지 상한(청크 entities 상위)
WIKI_PATCH_NOTE_CHARS = 1500      # 페이지당 F1 입력 노트 상한(play 절 발췌)
WIKI_PATCH_MAX_PER_PAGE = 3       # 페이지당 패치 절 상한

# [2026-09-14 W3a] 위키 play 절 분배(독자별 컴파일러, LLM 0콜). OFF = 세 입 모두 종전 문자열 바이트 동일.
#   설계 §5(독자별 섹션 프로파일·렌더·영수증) / 지시서 wiki_w3a_compile_todo. WIKI_PAGES 하위.
#   W3a = 직접 시드(이름)만. 벡터 시드·두 레인 예산 공유·cache.db는 W3b.
WIKI_COMPILE = True          # 09-14 W3a 스모크 77/77 + 회귀 65 GREEN → ON
WIKI_COMPILE_EXCERPT_CHARS = 600
WIKI_COMPILE_PROFILES = {   # 독자 -> (허용 절 순서, 페이지당 절 발췌 상한, 총 상한)
    # [2026-09-14 W5] `Members+`(faction 항목 절) + 투영 절 `Occupants`·`Layout`(location, 저장 0)
    #   추가. character 페이지엔 이 절들이 아예 없으므로 W5 OFF면 출력 바이트는 종전과 같다.
    "T3": (("Observed", "Relationships+", "Members+", "Occupants", "Layout"), 600, 2400),
    "T1": (("Observed", "Relationships+", "Members+", "Occupants", "Layout", "History"), 400, 2000),
    "C1": (("Observed", "Relationships+", "Knowledge", "Members+", "History"), 300, 1200),
    # [2026-10-17 C47] 속마음 — 그 인물 페이지 하나만(벡터 시드 없음: WIKI_LANE_FLOOR 에 T6 없음). 총 상한은
    #   네 절 × 600 + 머리보다 넉넉히(넘으면 페이지 통째 탈락이라).
    "T6": (("Observed", "Relationships+", "Knowledge", "History"), 600, 3000),
}
WIKI_COMPILE_MAX_PAGES = 6
WIKI_COMPILE_HISTORY_LINKS = 3

# [2026-09-14 W5] 장소·세력 페이지. 뿌리·부모만 페이지, 잎은 모음의 한 줄. OFF = 종전 바이트 동일.
#   설계 §3·§4·§5 / 지시서 wiki_w5_places_factions_todo. WIKI_PAGES 하위.
WIKI_PLACES = True           # 09-14 W5 스모크 75/75 GREEN → ON
WIKI_LOC_PAGE_TYPES = ("region", "area")     # 개별 페이지 자격 노드 타입(room 제외)
WIKI_PROMOTE_MIN_PATCHES = 3                 # 모음 줄이 이 횟수 패치를 받으면 페이지 승격
WIKI_AGGREGATE_PAGES = {"location": "장소 모음", "faction": "세력 모음"}   # kind concept, source lore
WIKI_TURNLOG_EXTRA_MAX = 4                   # 턴당 extra_entities 상한(장소 1 + 세력 <=3)
CHANNEL_CACHE_FILE = "cache.db"   # [2026-09-14 W3b] 채널 폴더의 재생성 가능 캐시 DB(절 벡터)

# [2026-09-14 W3b] 섹션 벡터 시드 + 두 레인 예산 공유. 상수는 바닥(보장)이지 문턱이 아니다.
#   설계 §5·§9 / 지시서 wiki_w3b_vectors_todo. WIKI_COMPILE 하위. 새 LLM 콜 0(임베딩만).
WIKI_VECTORS = True          # 09-14 W3b 스모크 55/55 + 회귀 GREEN → ON
WIKI_VEC_MIN_SCORE = 0.12         # FLASHBACK 바닥. 이 아래는 시드 후보에서 제외(직접 시드는 무관)
WIKI_VEC_TOP_PAGES = 4            # 벡터 시드로 더할 페이지 상한(직접 시드와 별도)
WIKI_VEC_SECTION_CHARS = 2000     # 절 본문 임베딩 입력 절단(앞부분)
WIKI_LANE_FLOOR = {"T3": 800, "T1": 600, "C1": 400}   # 독자별 위키 레인 보장 자수(직접 시드 우선)
WIKI_LANE_RETURN_MAX = 1500       # 위키 레인이 바닥보다 덜 쓰면 Slot 9 사다리 천장에 돌려주는 상한
WIKI_VEC_BATCH = 32               # 한 턴에 임베딩할 절 수 상한(나머지는 다음 턴)
# [2026-08-11 soma 지속] B축(신체) 지속 시계 — `dissociation: Track across turns` 집행 재료.
# npc_soma_states에 since_turn(무변화 시작 턴)을 도장하고, 추출 콜에 지속 턴수를 되돌린다.
# MIN_TURNS: 이 턴수 미만 지속은 **침묵**(1턴짜리 = 노이즈, 어차피 값 자체가 이미 붙어 나간다).
# LOG_KEEP: soma_log 채널당 롤링 상한 (attitude_log 1000 / emotion_log 1500과 같은 계열,
#           전이 이벤트만 쌓이므로 더 성김 → 800).
SOMA_PERSIST_MIN_TURNS = 2
SOMA_LOG_KEEP = 800
# [V10 지식 lite] npc_knowledge의 suspects/misbeliefs(의심·오해) 버킷 — 저장/추출은 무위험(상시),
# 이 플래그는 *주입/전파-state*(읽기경로 변경: "A는 X를 의심한다" 산문 주입, knows→hearer suspects 착지)만 게이트.
# 저장 스키마는 항상 쌓이고(populate 무조건), 이 플래그는 전파(knows→suspects 착지)+주입(iceberg suspects 렌더)만 게이트.
# graceful-empty(버킷 빈 동안 무동작) + echo-safe(영어 텔레그래픽, 기존 knows/false_beliefs 주입과 동일 register).
# 2026-06-19 ON (사용자 "바로 배선"). 문제 시 이 줄 False = 전파/주입 즉시 무동작(저장은 유지).
V10_KNOWLEDGE_BOUNDARY_INJECT = True

# I축(재정착): cadence_echo 턴-간 verbatim 후렴 되먹임. 2026-06-24 ON (추론ON도 cross-turn recall은 못 잡음 — 산문6 실증).
# False = 탐지·로그는 유지, 다음턴 주입만 무동작.
CADENCE_ECHO_INJECT = True

# [2026-07-22 카드2] 반복 문장 스크럽 — 재발한 verbatim 문장을 다음 턴 *주입본*(히스토리·S31)에서 제거.
# 넛지(위 INJECT)는 "반복하지 마"라고 말하는 것이고 이건 "베낄 원본을 치우는" 것 — 후자가 이 스택의
# 검증된 반복 억제 계보(엠대쉬 미러 트림, 07-08 루프차단기). 저장본·플레이어 노출본은 무손상.
# False = 탐지·로그·넛지는 유지, 스크럽만 무동작(1줄 롤백).
ECHO_SCRUB = True

# [2026-08-11 S31 꼬리주입 오프] Slot 31(직전 응답 500자 꼬리) 주입 게이트.
# S27과 동병 소급: 원문은 히스토리 마지막 assistant에 전문으로 실리고 방어 3종도 그쪽에
# 동일 적용 — 이중 주입은 비용+저미기 미러링 벡터만 더함. True = 구 동작 복귀(기계 휴면 보존).
SLOT31_TAIL_INJECT = False

# =========================================================
# [2026-10-23 탈옥 통합] 인가 한 층 — 문답(C1) · 세션 목적 강화문(C5 warrant)
#   스펙: 지시문/탈옥통합/탈옥통합_구현스펙_2026-10-23.md
# =========================================================
# C1 문답 — 규칙 존 꼬리의 ask + pinned [Luka] 첫 턴의 answer. **Gemini 경로 전용**
#   (규칙 존=user 턴, [Luka]=model 턴이라야 문답이 성립한다. openai 경로는 system 턴이라 no-op).
#   False = ask·answer 모두 구 문안으로(1줄 롤백).
RENDER_COMMISSION_HANDSHAKE = os.getenv("RENDER_COMMISSION_HANDSHAKE", "1").strip().lower() in ("1", "true", "on", "yes")

# C5 강화문(warrant) — Syuriel 원본의 "탈옥 강화문". **기본 0** (레티어스 10-23: 굳이 넣을 필요 없다).
#   True = FRAME과 PARTNER 사이에 2,793자 삽입(원본 "강화문 ON" 상태, 3,556자).
RENDER_SESSION_PURPOSE_WARRANT = os.getenv("RENDER_SESSION_PURPOSE_WARRANT", "0").strip().lower() in ("1", "true", "on", "yes")

# =========================================================
# [2026-07-22 카드1] 감정 압력 공급 · 포어그라운드 선별
# 스펙: 파티쳇수정/narrative/phase3_card1_emotion_pressure_spec_v0.4.md
# 원칙: 렌더러가 받는 감정을 "정체(what it is)"에서 "압력(what it does)"으로.
#       감정 벡터(emotion_engine)는 말하지 않고 **밸브**로 일한다(선별·노출량).
# =========================================================
# 서사 콜 psyche_narrative에 pressure(drives/cannot) 요청·병합. False = 스키마 미요청(구 동작).
PRESSURE_SUPPLY = True
# [2026-09-15 §12] 압력 방출 on/off 플래그 삭제 — True 경로만 남음(Slot 14 압력 방출 상시,
#   emotion_engine 라벨 노출 롤백 경로 폐기).
# deep_read 렌더러 방출. True = 구 경로 유지(롤백용). 신설계 기본 = False(상류 전용).
DEEPREAD_EMIT = False

# 포어그라운드 상한 — 2 확정(대비는 개수가 아니라 비율에서 나옴 + V4 전량이행 성향 방어).
# 유저가 직접 지목·대화한 NPC는 이 상한을 넘어 강제 fg.
FOREGROUND_CAP = 2
# 점수 차가 이 값 미만이면 직전 턴 fg 유지(튐 방지). rotation penalty보다 커야 서로 삼키지 않음.
FOREGROUND_HYSTERESIS = 0.15
# 직전 턴 fg였던 NPC 감점(고착 방지). 히스테리시스보다 작게.
FOREGROUND_ROTATION_PENALTY = 0.08

# iceberg mirror register 노테이션 (자기기만/내면). 2026-06-21 스키마드리프트 검수로 부활.
# 내면 노테이션이라 과내면 우려와 충돌 가능 → 산문 과내면화 관측 시 이 줄 False = mirror 즉시 무동작.
# (Flash-direct·inferred 양쪽 다 억제. 나머지 register/propagation은 무관.)
ICEBERG_MIRROR_ENABLED = True

# =========================================================
# 시트 조각("패시브") — 2026-09-16 3차 (설계 relation_unify §10.2)
# =========================================================
# 조각 = {name, desc, value:{edge: ±1}, origin: sheet|play}.
# [2026-10-15 C42] **이름 판단.** Theoria 가 집은 조각이 이번 행동에 걸리는지는 집는 것으로 이미 판단됐으니
#   행동 종류 키(roll_<type>)는 판정이 안 본다. 값은 강점(+1)/약점(−1) 부호 하나.
#   옛 값(roll_<type>: ±n · cost: −n)은 읽을 때 roll 합의 부호로 접는다(0·{} = 강점). cost(각오 할인)는 각오 퇴역과 함께 소비 0.
#   FRAGMENT_ROLL_CAP·FRAGMENT_COST_MAX 퇴역.
# type enum 은 **한 곳** — theoria action_meta.type 이 읽는다(좋은 상태의 행동 종류 매칭에도 쓴다).
ACTION_TYPES = ("combat", "social", "exploration", "stealth", "survival", "crafting", "general")
FRAGMENT_VALUE_KEYS = ("edge",)
FRAGMENT_ORIGINS = ("sheet", "play")


def normalize_fragment_value(raw) -> dict:
    """조각 value 정규화 → {"edge": 1} 또는 {"edge": -1}. 옛 roll_<type> 은 합의 부호로 접고, 근거가 없으면 강점."""
    if not isinstance(raw, dict):
        return {"edge": 1}
    e = raw.get("edge")
    if e is not None and not isinstance(e, bool):
        try:
            n = int(e)
            if n:
                return {"edge": 1 if n > 0 else -1}
        except (TypeError, ValueError):
            pass
    total = 0
    for k, v in raw.items():
        if str(k).startswith("roll_") and not isinstance(v, bool):
            try:
                total += int(v)
            except (TypeError, ValueError):
                continue
    return {"edge": -1 if total < 0 else 1}


def fragment_edge(passive) -> int:
    """조각 하나의 판정 기여 부호(+1 강점 / −1 약점)."""
    if not isinstance(passive, dict):
        return 1
    return int(normalize_fragment_value(passive.get("value")).get("edge", 1))


def get_passive_modifiers(passive) -> dict:
    """조각 → value dict({"edge": ±1})."""
    if not isinstance(passive, dict):
        return {"edge": 1}
    return normalize_fragment_value(passive.get("value"))


# =========================================================
# [2026-10-15 C42] Item Theory Tag System 퇴역 — ITEM_KEYWORD_MODIFIERS · _roll_keys_only · get_item_modifiers 삭제.
#   소지품은 Theoria 가 이번 행동에 쓰는 것을 이름 그대로 집고(action_meta.active_items) 코드가 소지를 확인해
#   하나당 +1 로 센다. 옛 키워드표는 부분 일치라 "검은 망토"가 전투 +10 을 받았다.


# ⚠ 미배선 (2026-07-06 감사): 소비자 0 — 모듈 목록은 cmd_modules가 하드코딩
# (core=judgment/doom/anomaly, extra=board). "mental" 항목은 폐지된 V7 잔재.
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
        "name": "활력",
        "emoji": "💪",
        "desc": "PC의 몸 축 — 체력·지구력. 마음 축은 평형. 판정 보정·이변 방어에 영향."
    }
}

# =========================================================
# [2026-08-18 대형식화 v0] 선언형 변수 레지스트리 — 킬스위치 **하나**
# =========================================================
# 0 이면 전면 no-op: 저작 명령 거부 · 추출 급식 0 · 델타 적용 0 · 패널 합성 0.
# ★env 레버는 이것뿐이다. 볼륨 캡·이름 길이·타입 목록은 전부 custom_vars.py 의
#   **코드 상수** — 유저가 튜닝할 축이 아니라 계약이라서(스펙 §7 원칙).
CUSTOM_VARS_ENABLED = int(os.getenv("CUSTOM_VARS_ENABLED", "1"))

# =========================================================
# [2026-08-16 도착물 라우트] 턴 도착물 (💌 편지 · 💭 속마음)
# =========================================================
# 월드보드 스레드가 **공개**라 남에게 보이는 문제의 해법 — 도착물은 그 턴 산문 메시지의
# 버튼에서 ephemeral 로 열린다. KEEP = 채널당 turn_mail 행 롤링 상한(버튼이 만료되는 지점).
TURN_MAIL_KEEP = int(os.getenv("TURN_MAIL_KEEP", "500"))
# 💭 속마음 — **기본 on** (2026-08-17 v1). 전역 킬스위치이자 기본값이고, 채널 단위 off는
# `!모듈 속마음 off`(domain_manager.DEFAULT_ON_MODULES/"mind")가 소유한다. 0이면 채널 설정
# 무관하게 전면 정지(콜 0·저장 0).
TURN_MIND_ENABLED = int(os.getenv("TURN_MIND_ENABLED", "1"))
# [2026-08-17 속마음 v1] 전용 배경 콜. 0이면 콜을 안 돌고 구 경로(psyche_narrative 원문
# 선별, 콜 0)만 남는다 — 롤백 한 줄. 콜 실패 시에도 같은 구 경로가 폴백으로 선다.
TURN_MIND_CALL = int(os.getenv("TURN_MIND_CALL", "1"))
# [2026-10-22 C59] 턴 임베드 압축 — Activity 가 떠 있으면 산문 아래는 PC 활력·평형·타격 + 시간·장소·인물만.
#   0 이면 옛 전 장(세계·관계·장면·기록·섹션 장). Activity 가 안 떠 있으면 이 값과 무관하게 옛 전 장.
TURN_EMBED_COMPACT = os.getenv("TURN_EMBED_COMPACT", "1").strip().lower() not in ("0", "false", "off", "no")
# 대상 게이트: **무대에 선 인물**(get_onstage_npc_names ∪ gaze) ∩ **점수 임계**.
#   점수 = 이번 턴 emotion intensity(0~1) + spike 가산 + foreground 가산.
#   FG 가산 = 임계와 같은 값 → 전경 인물은 무조건 통과, 배경 인물은 제 감정으로만 통과.
TURN_MIND_SCORE_MIN = float(os.getenv("TURN_MIND_SCORE_MIN", "0.35"))
TURN_MIND_FOREGROUND_BONUS = float(os.getenv("TURN_MIND_FOREGROUND_BONUS", "0.35"))
TURN_MIND_SPIKE_BONUS = float(os.getenv("TURN_MIND_SPIKE_BONUS", "0.25"))
# [2026-08-17 v1.1 §2] 전경 무임승차 제거 — FG 가산 **이전의 생값**(intensity + spike 가산)에
#   걸리는 최소 바. 전경이라는 이유만으로 감정 0인 인물이 통과하던 자리를 "살짝" 거른다.
#   ⚠ 이 바는 **계측이 있을 때만** 선다. 감정층이 통째로 비어 있는 턴(엔진 미가동)은
#     계측이 낮은 게 아니라 **없는** 것이므로 바를 세우지 않는다(재료 죽음 ≠ 기능 침묵).
TURN_MIND_EMOTION_FLOOR = float(os.getenv("TURN_MIND_EMOTION_FLOOR", "0.15"))
# [2026-08-17 v1.1 §1] 대상 NPC의 **출처** 게이트(npc_manager.SOURCE_* 값, 쉼표 구분).
#   lore/manual = 사람이 쓴 확정 시트 → 내면이라 부를 축적이 있다.
#   ai_generated/session = 그 턴에 즉석 등재된 인물 → 시트 자체가 방금 생긴 산물이라
#     "속"을 열면 심리가 아니라 즉흥 설정이 나온다. 기본은 앞의 둘만.
#   빈 문자열 = 필터 끔(전 출처 허용). source 필드가 없는 구 레코드는 npc_manager 관례대로
#   SOURCE_SESSION 으로 접힌다 → 기본 설정에서는 제외된다.
TURN_MIND_SOURCES = os.getenv("TURN_MIND_SOURCES", "lore,manual")
# ⚰[2026-09-13 P14] `BOARD_FREQUENCY_*` 넷 삭제. WHY: 이 레버가 돌리던 턴 간격 게이트가
#   같은 카드에서 사라졌다(도착물은 선언 전이 `deliver`·분석 신호 `arrival` 이 부를 때만 온다).
#   읽는 코드가 0 인 env 를 남기면 .env 의 숫자가 여전히 무언가를 조종하는 것처럼 보인다.
# 콜 1회당 인물 상한(선별이 이보다 많으면 점수순 절단). 표시 상한은 turn_mail.MAX_MIND_ENTRIES.
TURN_MIND_MAX_NPCS = int(os.getenv("TURN_MIND_MAX_NPCS", "3"))
# 인물당 속마음 길이 캡(문자). 속마음은 장면 요약이 아니라 한 호흡 — 짧게.
TURN_MIND_CHARS = int(os.getenv("TURN_MIND_CHARS", "160"))
# [2026-08-17 앵커 교체] 구 `TURN_MIND_PROSE_TAIL`(산문 꼬리 1200자) **제거**. 속마음 콜은
#   더 이상 렌더 산문을 받지 않는다 — 복붙 금지 계약이 필요했던 원천이 그 꼬리였고,
#   원천 제거가 규칙보다 싸다. 대체 = `turn_mail._build_scene_anchor`(구조 앵커).
#   아래는 그 앵커에서 유일하게 자유 문자열인 칸(추출 콜 Observation)의 길이 캡.
#   나머지 칸은 전부 enum·이름이라 캡이 필요 없다. status_panel 의 PROSE_TAIL_CHARS(2500)와
#   비교 대상이 아니다 — 패널은 산문에서 값을 읽고, 속마음은 타이밍만 필요하다.
TURN_MIND_ANCHOR_CHARS = int(os.getenv("TURN_MIND_ANCHOR_CHARS", "300"))
# [2026-08-17 장면 연관 로어 — 2·3번째 소비자] 리더 부록(READER_LORE_*)과 같은 검색층
#   진입점(`vector_search.get_scrubbed_scene_chunks`)을 쓰되, 레버는 소비자별로 따로 쥔다.
#   ── 게시판: 쿼리 = 이번 이벤트 브리핑(`detail_kr`). 발췌 = 작성자가 아는 **공적 세계 지식**
#      (지리·관습·내력)이지 이번 사건의 목격이 아니다 — `_BOARD_AUTHORSHIP` 정보 격리와 정합.
#      게시물은 짧고(40~250자) 발췌가 길면 그 문장을 베끼므로 리더(3/500)보다 조인다.
BOARD_LORE_TOP_K = int(os.getenv("BOARD_LORE_TOP_K", "2"))
BOARD_LORE_CHUNK_CHARS = int(os.getenv("BOARD_LORE_CHUNK_CHARS", "400"))
# [2026-10-17 C46] 도착물(산문 앞 핸드아웃) 재료 — 렌더 자료 존 + 로어(자르지 않음) + 원문 발췌.
#   위 BOARD_LORE_* 두 손잡이는 옛 길(도착물 밖 게시 · 아래 플래그 off)용으로 그대로 둔다.
HANDOUT_WORLD_REF = os.getenv("HANDOUT_WORLD_REF", "1").strip().lower() not in ("0", "false", "off", "no")
HANDOUT_LORE_TOP_K = int(os.getenv("HANDOUT_LORE_TOP_K", "4"))
HANDOUT_LORE_CHUNK_CHARS = int(os.getenv("HANDOUT_LORE_CHUNK_CHARS", "0"))   # 0 = 청크 전문(10-17 "로어는 자르지 말자")
HANDOUT_REF_MAX_CHARS = int(os.getenv("HANDOUT_REF_MAX_CHARS", "60000"))     # 자료 존 블록에만(9→7→6 순). 로어는 상한 밖
HANDOUT_RAW_EPISODES = int(os.getenv("HANDOUT_RAW_EPISODES", "3"))
HANDOUT_RAW_MIN_SCORE = float(os.getenv("HANDOUT_RAW_MIN_SCORE", "0.3"))
HANDOUT_RAW_ROWS = int(os.getenv("HANDOUT_RAW_ROWS", "24"))                  # 에피소드 하나에서 읽는 원문 행 상한
HANDOUT_RAW_SNIPPET = int(os.getenv("HANDOUT_RAW_SNIPPET", "150"))           # 조각 = 걸린 자리 ±N자
HANDOUT_RAW_CHARS = int(os.getenv("HANDOUT_RAW_CHARS", "3000"))
# [2026-10-17 C49] 도착물 원문 남기기 · 위키 연동. 끄면 옛 동작(원문은 turn_mail·디스코드 메시지에만).
HANDOUT_LOG_RAW = os.getenv("HANDOUT_LOG_RAW", "1").strip().lower() not in ("0", "false", "off", "no")      # R1 영구 로그 한 행
HANDOUT_WIKI_LINK = os.getenv("HANDOUT_WIKI_LINK", "1").strip().lower() not in ("0", "false", "off", "no")  # R2 위키 History 한 줄
HANDOUT_WIKI_MAX_PAGES = int(os.getenv("HANDOUT_WIKI_MAX_PAGES", "4"))   # 도착물 하나가 줄을 남기는 페이지 상한
# [2026-10-18 C50 R4] 정정 되묻기 보류 — 이 턴 수가 지나도록 안 고르면 버린다(반영 0).
CORRECTION_PENDING_TURNS = int(os.getenv("CORRECTION_PENDING_TURNS", "10"))
# [2026-10-18 C51] 미성년 정화기(cognition.sanitize_minor_tokens) 범위. 레티어스 "시트 정리 콜이랑 랜더 요청은 안거치게 해보자".
#   꺼짐 = 렌더 요청(자료 존·이번 턴 문서·프리필·히스토리 주입)·시트 정리 콜(condense_play_section)에 원문이 간다.
#   켜면 옛 동작. 로어 통합 분석·시트 분석·보이스 카드·일과 추출은 이 손잡이와 무관하게 늘 정화한다.
# [2026-10-22 C57+] 렌더 정화 기본 켬 — 레티어스 "컨피그를 키자". C57로 자료 존(NPC 시트·로어의 나이·학년)이 다시
#   Gemini 로 간다. C51 뒤 렌더가 안 막힌 건 자료 존이 매 턴 잘려 그 글이 안 갔기 때문이었다. 끄려면 env =0.
MINOR_SANITIZE_RENDER = os.getenv("MINOR_SANITIZE_RENDER", "1").strip().lower() in ("1", "true", "on", "yes")
MINOR_SANITIZE_CONDENSE = os.getenv("MINOR_SANITIZE_CONDENSE", "0").strip().lower() in ("1", "true", "on", "yes")
# [2026-10-17 C46 R3] 끝난 시계·퀘스트가 도착물 소재가 되는 창(턴). 밖이면 조용히 넘긴다.
BOARD_CLOCK_RECENT_TURNS = int(os.getenv("BOARD_CLOCK_RECENT_TURNS", "10"))
#   ── 속마음: 쿼리 = 구조 재료 조합(앵커의 Observation + 현재 위치 라벨). 산문이 없으니
#      쿼리도 산문에서 오지 않는다(앵커 교체 원칙 유지). 발췌 = 인물이 아는 세계의 결
#      (생각이 접지할 지형)이지 이번 턴 사건이 아니다. 한 호흡(160자) 출력이라 가장 조인다.
#      쿼리 재료가 다 비면 검색 자체를 안 한다(빈 쿼리 = 임베딩 콜 0).
TURN_MIND_LORE_TOP_K = int(os.getenv("TURN_MIND_LORE_TOP_K", "2"))
TURN_MIND_LORE_CHUNK_CHARS = int(os.getenv("TURN_MIND_LORE_CHUNK_CHARS", "0"))   # [2026-10-17 C47 R3] 300 → 0(전문). "로어는 자르지 말자"
# [2026-10-17 C47 R2] 속마음 재료 = 그 사람 몫(위키 페이지 play 절 · NPC 지식 · 원문 조각). 0 = 끔(종전 재료).
TURN_MIND_OWN_RECORD = int(os.getenv("TURN_MIND_OWN_RECORD", "1"))
TURN_MIND_RECORD_CHARS = int(os.getenv("TURN_MIND_RECORD_CHARS", "900"))     # 인물당 원문 조각 합계
TURN_MIND_RECORD_SNIPPET = int(os.getenv("TURN_MIND_RECORD_SNIPPET", "120"))  # 조각 = 이름 자리 ±N자
TURN_MIND_RECORD_ROWS = int(os.getenv("TURN_MIND_RECORD_ROWS", "6"))          # 이름 하나당 찾는 원문 행
# [2026-10-25 C77 A-2 · Q4] 직전 💭 (`thought_before`)를 이 턴 수 안의 것만 싣는다. 0 = 안 실음.
TURN_MIND_BEFORE_TURNS = int(os.getenv("TURN_MIND_BEFORE_TURNS", "5"))
# [2026-10-25 C77 C-2] 턴 시작 `!다시` 스냅샷 앞에서 앞 턴 배경 작업을 기다리는 상한(초). 넘기면 그 턴 `!다시` 는 거절. 0 = 안 기다림.
RETRY_SNAPSHOT_BG_WAIT = float(os.getenv("RETRY_SNAPSHOT_BG_WAIT", "15"))
# [2026-10-25 C78 D5] 없는 `!명령` 에 한 줄 안내(가까운 이름). 0 = 옛 꼴(조용히 무시) — 같은 채널에 `!` 쓰는 다른 봇이 있으면 끈다.
UNKNOWN_COMMAND_HINT = int(os.getenv("UNKNOWN_COMMAND_HINT", "1"))

# =========================================================
# [2026-08-17 발신자 긴밀화] 문자·쪽지(message 채널)의 **발신 NPC 선정 가중**
# =========================================================
# 병: 이벤트 weight 만 보고 고르니 관계가 0인 인물이 PC에게 사적 편지를 보냈다.
#   공지·SNS는 공적 매체라 낯선 이름이 정상이지만, 사적 매체의 발신자는 **관계가 매체다**.
# 처방은 하드 필터가 아니라 **가중 우선** — 낯선 발신도 가능은 하게 둔다(세계 생동감).
#   하드로 바꾸고 싶으면 MIN_DEPTH 를 올린다(0 = 가중만, 기본).
BOARD_SENDER_MIN_DEPTH = int(os.getenv("BOARD_SENDER_MIN_DEPTH", "0"))
# depth(0~100) 만점 시 더해지는 가중. 주 가중이라 가장 크다.
BOARD_SENDER_DEPTH_BONUS = float(os.getenv("BOARD_SENDER_DEPTH_BONUS", "0.40"))
# 등장 횟수 보조 가중(포화형). SAT 회 이상 등장하면 만점 — 관계 깊이의 대용이지 대체가 아니다.
BOARD_SENDER_APPEAR_BONUS = float(os.getenv("BOARD_SENDER_APPEAR_BONUS", "0.15"))
BOARD_SENDER_APPEAR_SAT = int(os.getenv("BOARD_SENDER_APPEAR_SAT", "6"))
# 사람이 쓴 확정 시트(lore/manual)에서 온 발신자 가산. 계보 = turn_mail._allowed_mind_sources.
BOARD_SENDER_SOURCE_BONUS = float(os.getenv("BOARD_SENDER_SOURCE_BONUS", "0.10"))
BOARD_SENDER_SOURCES = os.getenv("BOARD_SENDER_SOURCES", "lore,manual")

# =========================================================
# [2026-08-17 쪽지 서사 접지] 보낸 문자·쪽지를 다음 턴 서사 콜 재료로
# =========================================================
# 세계가 PC에게 편지를 보내 놓고 이야기는 그 사실을 모르는 상태였다(표시층에서 끝났다).
#   1턴 큐(ai_session_memory.recent_world_mail) → 다음 턴 좌뇌 **서사 콜**이 소비하고 비운다.
#   렌더 직행 아님 — 방향을 정하는 콜이 받아야 산문이 자연히 그 편지를 딛는다.
WORLD_MAIL_QUEUE = int(os.getenv("WORLD_MAIL_QUEUE", "1"))
# 소비 시 폐기 임계(턴). 적재 후 이 턴수 넘게 안 먹힌 항목은 버린다 — 지연 도착한 편지가
#   열 턴 뒤 산문에 튀어나오는 게 더 나쁘다.
WORLD_MAIL_MAX_AGE = int(os.getenv("WORLD_MAIL_MAX_AGE", "2"))
# 큐에 담는 본문 요약 캡(문자). 전문이 아니라 **무엇을 보냈는지**만 실린다.
WORLD_MAIL_SUMMARY_CHARS = int(os.getenv("WORLD_MAIL_SUMMARY_CHARS", "160"))

# =========================================================
# [2026-09-22 voice_seed] 세션 NPC 시드 — 배선 스펙 §3
# =========================================================
# 마스터 OFF 가 곧 롤백이다(별도 롤백 게이트 없음). 실패 바닥 = 현행 콜드 즉흥.
VOICE_SEED = os.getenv("VOICE_SEED", "1") not in ("0", "false", "False")
# 굴려 놓고 등록 안 된 시드의 수명(턴). 넘으면 조용히 퍼지 — 만료는 "장면 소품이었다"는 뜻.
VOICE_SEED_TTL = int(os.getenv("VOICE_SEED_TTL", "5"))
# 한 턴에 굴리는 신규 인물 상한. 프로필 블록 팽창 방지(RelevantNPCs 는 최대 5).
VOICE_SEED_MAX_PER_TURN = int(os.getenv("VOICE_SEED_MAX_PER_TURN", "3"))
# 무대 차집합 재굴림 상한. 고갈하면 겹침을 허용한다 — 인물을 못 만드는 것보다 낫다.
VOICE_SEED_STAGE_REROLL = int(os.getenv("VOICE_SEED_STAGE_REROLL", "5"))

# =========================================================
# Safety Settings
# =========================================================
# [2026-10-26] 전부 OFF(필터 자체를 끔) + JAILBREAK 추가. 옛 값은 BLOCK_NONE(안 막음).
#   JAILBREAK = "안전 필터를 우회하도록 설계된 프롬프트" — **입력** 쪽 필터다. Slot 1(CONTENT_AUTHORIZATION_MANDATE)이
#   그 꼴 그대로라, 안 정한 카테고리의 기본값(2.5 이상 꺼짐)이 바뀌면 렌더 콜이 입력에서 막힌다 — 그래서 명시한다.
#   SDK 에 그 이름이 없으면(옛 google-genai) 넣지 않는다 — 모르는 이름을 받는 꼴이 SDK 판마다 달라서(2.33 은 경고 뒤 그대로,
#   옛 판은 검증 오류일 수 있다 — 그러면 config import 째 죽는다) 거르는 쪽이 안전하다. 안 넣어도 기본값은 꺼짐.
#   아동 안전 등 핵심 유해는 조정 불가(API 쪽 고정). 제미니 콜(RENDERER_BACKEND=gemini 면 매 턴 렌더)에만 실리고,
#   openai 호환 백엔드(분석 등)는 이 목록을 버린다(analysis_backend).
_SAFETY_CATEGORIES = (
    "HARM_CATEGORY_HARASSMENT",
    "HARM_CATEGORY_HATE_SPEECH",
    "HARM_CATEGORY_SEXUALLY_EXPLICIT",
    "HARM_CATEGORY_DANGEROUS_CONTENT",
    "HARM_CATEGORY_CIVIC_INTEGRITY",
    "HARM_CATEGORY_JAILBREAK",
)
SAFETY_SETTINGS = [
    types.SafetySetting(category=_c, threshold="OFF")
    for _c in _SAFETY_CATEGORIES
    if _c in getattr(types.HarmCategory, "__members__", {})
]
