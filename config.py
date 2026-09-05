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

# Renderer Backend: "gemini" or "openai" (OpenAI-compatible; 현 운영=Ollama Cloud)
RENDERER_BACKEND = os.getenv("RENDERER_BACKEND", "openai").lower()

# OpenAI-compatible renderer — Ollama Cloud (https://ollama.com/v1, 모델명 :cloud 접미사)
OPENAI_API_KEY = os.getenv("OPENAI_RENDERER_API_KEY", "")  # ollama.com/settings/keys 키 env로
OPENAI_BASE_URL = os.getenv("OPENAI_RENDERER_BASE_URL", "https://ollama.com/v1")
OPENAI_MODEL_ID = os.getenv("OPENAI_RENDERER_MODEL", "")
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
# 스펙: 파티쳇수정/deepseek_v4_trait_playbook_2026-07-05.md §4 R1, 지도: 리더GM_지도_2026-08-11.md.
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
RENDERER_REASONING_TIER = os.getenv("RENDERER_REASONING_TIER", "off")               # 코드 기본 off. [2026-07-05 GLM 스왑] .env가 light로 상향 — 추론+TELESCOPE 병행(제미니 시절 원 구조 복원). 캡은 persona 배선
# 렌더 전용 추론 캡(문자). 분석 light(1200)와 분리 — 1턴 실측 ~1900자에서 결과 좋았고(V1 갭 닫힘)
# GLM RP 강점=thinking 결합이라 렌더만 소폭 상향(레티어스 2026-07-05). 0=tier 기본값(1200) 사용.
# [2026-08-01] 2000 → 3500. KOREAN PROSE 3단 변환(영→일→한) 배포에 따른 실행 공간 확보:
#   다리는 추론에서만 건넌다("The bridge runs in reasoning alone") → 캡이 다리의 실제 예산.
#   추정 소요 = 영어 beat 구성 800~1200 + 일어 재구조화 500~1000 + 기존 사고 ~1000 ≈ 2300~3200자.
#   현행 2000(실측 ~2582, 소프트캡이라 초과 사용)으로는 변환분 자리가 없다. 3500 = 추정 상한 +여유.
#   ⚠V4-Pro 추론 폭주 이력(7.6~13.6k, off tier의 원 사유)이 있어 무제한 아님 — 소프트캡 유지.
#   판별: persona의 reasoning_trace_len 로그. 캡 근처 상시 = 부족 / 짧은데 보조용언 무변화 = ritual화.
RENDERER_REASONING_CAP_CHARS = int(os.getenv("RENDERER_REASONING_CAP_CHARS", "3500"))
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

# [C안 2026-07-02] 백그라운드 유지보수 (전부 async — 턴 지연 0. 0 = 비활성)
PERSIST_AUDIT_INTERVAL = int(os.getenv("PERSIST_AUDIT_INTERVAL", "20"))       # N턴마다 영속층 감사 (log-only, 검출≠쓰기)
MEMORY_GC_FERMENT_INTERVAL = int(os.getenv("MEMORY_GC_FERMENT_INTERVAL", "5"))  # 발효 M회마다 deep_memory_data GC (백업+보수적)
# [Gemini 3] presence_penalty/frequency_penalty not supported - removed

# Generation Parameters - Narrative (Pro/Right Brain)
NARRATIVE_TEMPERATURE = 1.15  # 1.4 → 1.15 (감정 과장 어휘 fan-out 축소)
NARRATIVE_TOP_K = 60          # 70 → 60 (wild 후보 trim)
NARRATIVE_TOP_P = 0.80
NARRATIVE_MAX_OUTPUT_TOKENS = 16384  # 12288→16384 [2026-07-05 GLM 스왑]: 렌더 추론 ON — Ollama /v1이 thinking을 max_tokens에 포함할 가능성 대비(산문 잘림=비대칭 실패, 서사 콜 8192 인상과 동일 논리)
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
NEMESIS_THRESHOLD = -10  # ⚠ 미배선 (2026-07-06 감사): 소비자 0 — 튜닝해도 아무 효과 없음

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
# DOOM_CLIMAX_THRESHOLD·CLOCK_RESOLVE_DOOM 제거 (2026-07-06 감사): 소비자 0.
# climax는 LENS_DOOM_PHASE_RANGES가, 해결=NEUTRAL(0626 결정)은 doom_module 코드가 담당.
CLOCK_COMPLETE_DOOM = {4: 2, 6: 3, 8: 4}  # 완성(이벤트 발화)→상승. 10/15/20→2/3/4 (0626): 시계는 양념, 완성=활동 beat. 수식(phase×lens) 통과

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

# Judgment condition modifier (polarity × intensity → ±mod)
CONDITION_MOD_SCALE = {"Low": 2, "Mid": 4, "High": 7, "Extreme": 12}
CONDITION_MOD_CAP = 15

# 기력 Stages (0-100) — PC의 총체적 컨디션 (체력/집중/평판/정신)
# Key: Stage ID (0-3)
MENTAL_STAGES = {
    0: {"name": "충만", "emoji": "😌", "range": (70, 101), "desc": "신체와 의지가 충실한 상태"},
    1: {"name": "둔화", "emoji": "😰", "range": (40, 70),  "desc": "출력이 느려지고 체력이 흔들립니다"},
    2: {"name": "고갈", "emoji": "😱", "range": (15, 40),  "desc": "한계에 가깝습니다. 판단과 행동이 둔해집니다"},
    3: {"name": "붕괴", "emoji": "🫥", "range": (0, 15),   "desc": "신체와 의지가 한계를 넘겼습니다."}
}

# DOOM_STAGES 제거 — LENS_DOOM_PHASE_RANGES + LENS_DOOM_ATMOSPHERE로 대체 (line 698~)

# DOOM_MENTAL_RECOVERY_MOD 제거 (2026-07-06 감사): 소비자 0 — 옛 Doom Stage(0-5)
# 멘탈 회복 배율. 회복은 vigor_composure_module 하이브리드 로직이 대체.

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
    "uplift":  3,    # [2026-07-06 회복 리워크] 흔한 양의 비트 — 위안/유대/성취/안도
    "restore": 8,    # 드문 깊은 회복 — 카타르시스/화해/고생 끝 승리/지속 위험 후 진짜 안전
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

# G-2. 트라우마 각성 폐지 (2026-07-06 레티어스 결정): TRAUMA_DWELL_TURNS/REBOUND_VALUE/
# DEBUFF_TURNS/DEBUFF_MODIFIER 제거. 바닥 탈출 = 갭 비례 자연회복+휴식+間 리프레시.


# [Phase 3 DEPRECATED] Effort (각오) — Phase 2 Flash modulator(extreme)가 자동 흡수 예정.
# 단순 dead 보존 (미래 재활성화 가능). 코드 호출처: judgment_engine effort_mod (effort_used가 None이면 자동 skip).
EFFORT_BONUS = 10    # 판정 +10
EFFORT_COST = 8      # 활력/평형 선불 (흡수 보험 포함, 추가 비용 없음)

# [2026-08-11 로드아웃 삭제] 회상 비용(FLASHBACK_COST_TIERS/PASSIVE_DISCOUNT/MIN_MENTAL)과
# 로드아웃 슬롯(LOADOUT_SLOTS/SLOT_COST/TYPES) 상수 제거 — !회상 명령 폐기로 소비자 0.
# 자동 감지 산문 반영은 상수 없이 동작(플래그·문장만).

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

# =========================================================
# Inventory System (N2 — 아이템 영속 + 인벤토리 검증)
# =========================================================
INVENTORY_SLOT_CAP = 4  # 인벤토리 고정 4칸
# ⚠ 미배선 (2026-07-06 감사): 소비자 0 — 실제 아이템 영속은 notebook [소지품] 라인
# (cognition item_usage → merge_notebook_preserve_inventory)이 담당. 참고 테이블로 보존.
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
    # cognition.py `### social` — "depth_delta (+1~+5 bonding, -1~-3 distancing)
    #                              and tension_delta (+1~+10 conflict, -1~-5 resolution)"
    "helena.cognition":    {"depth": (-3, 5),     "tension": (-5, 10)},
    # fermentation.py FERMENT_PROMPT_V4 — "## helena_delta (범위: -10 ~ +10)"
    "helena.fermentation": {"depth": (-10, 10),   "tension": (-10, 10)},
    # theoria_analyzer.py — "clock_updates": [{"delta": int(-1~+2)}]
    "doom.clock":          {"delta": (-1, 2)},
    # cognition.py `### social` — "delta ±0.1~0.3 (modify existing)"
    "relation.intensity":  {"delta": (-0.3, 0.3)},
}
MEMORY_DEDUP_JACCARD = 0.6             # 선발 시 기선발과 토큰 자카드 ≥ 이 값이면 스킵 (0=끔)
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

# [2026-08-02 A축 감쇠] depth/tension은 `update_helena_metric`의 max(0,min(100,cur+delta))
#   단조 누적뿐이라 **한 번 오른 값이 절대 안 내려왔다** — 관계가 식지 않는다.
#   같은 코드베이스에 감쇠 전례가 넷(entity_relations fade / EMOTION_DECAY / 태도 3턴 쿨다운 /
#   vigor 자연회복)인데 여기만 빠져 있었다.
#   형태는 entity_relations.cleanup_stale_relations의 grace/fade/floor를 따른다:
#   **삭제가 아니라 흐려짐.** 활성 관계(grace 안)는 건드리지 않는다.
#   끄기: RELATION_DECAY_GRACE = 0  → 전면 no-op.
RELATION_DECAY_GRACE = 10      # 이 턴 수만큼 무변화면 그때부터 감쇠 (0 = 기능 끔)
RELATION_DECAY_DEPTH = 1       # 감쇠 턴당 depth 하락폭
RELATION_DECAY_TENSION = 2     # tension은 더 빨리 식는다 (갈등은 관계보다 휘발성)
RELATION_DECAY_FLOOR = 0       # 이 값 아래로는 안 내려간다 (엔트리 제거는 안 함)

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
#   그래서 본은 `cap_llm_delta`(수치 캡)가 아니라 **`update_npc_attitude_gated`**다:
#   enum 단계 + 쿨다운 + ±1단계 클램프. LLM은 단계 이름만 낸다 → 캡할 수치가 없다.
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
# [2026-07-22 카드1] 감정 압력 공급 · 포어그라운드 선별
# 스펙: 파티쳇수정/phase3_card1_emotion_pressure_spec_v0.4.md
# 원칙: 렌더러가 받는 감정을 "정체(what it is)"에서 "압력(what it does)"으로.
#       감정 벡터(emotion_engine)는 말하지 않고 **밸브**로 일한다(선별·노출량).
# =========================================================
# 서사 콜 psyche_narrative에 pressure(drives/cannot) 요청·병합. False = 스키마 미요청(구 동작).
PRESSURE_SUPPLY = True
# 압력을 Slot 14로 방출. False = 생성만 하고 주입 0(관측 구간).
PRESSURE_EMIT = True
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
    # "Trauma" 엔트리 제거 (2026-07-06): 트라우마 각성 폐지. 레거시 세이브의 Trauma
    # 패시브는 매치 실패로 무해한 no-op.
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
        "name": "기력",
        "emoji": "💪",
        "desc": "PC의 총체적 컨디션 (체력/집중/평판/정신). 판정 보정·이변 방어에 영향."
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
# [2026-08-17 v1.1 §4] 월드보드 게시 빈도(최소 간격 턴). 채널별 설정(`!게시판 빈도 sns 5`) >
#   유저 전체 설정(`!게시판 빈도 10`) > 아래 채널별 기본 > 전역 기본 순으로 읽힌다
#   (world_board.get_board_frequency = 표시·판정 단일 함수). 구 하드코딩 상수를 승격한 것.
BOARD_FREQUENCY_DEFAULT = int(os.getenv("BOARD_FREQUENCY_DEFAULT", "10"))
BOARD_FREQUENCY_BULLETIN = int(os.getenv("BOARD_FREQUENCY_BULLETIN", "10"))
BOARD_FREQUENCY_SNS = int(os.getenv("BOARD_FREQUENCY_SNS", "11"))
BOARD_FREQUENCY_MESSAGE = int(os.getenv("BOARD_FREQUENCY_MESSAGE", "12"))
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
#   ── 속마음: 쿼리 = 구조 재료 조합(앵커의 Observation + 현재 위치 라벨). 산문이 없으니
#      쿼리도 산문에서 오지 않는다(앵커 교체 원칙 유지). 발췌 = 인물이 아는 세계의 결
#      (생각이 접지할 지형)이지 이번 턴 사건이 아니다. 한 호흡(160자) 출력이라 가장 조인다.
#      쿼리 재료가 다 비면 검색 자체를 안 한다(빈 쿼리 = 임베딩 콜 0).
TURN_MIND_LORE_TOP_K = int(os.getenv("TURN_MIND_LORE_TOP_K", "2"))
TURN_MIND_LORE_CHUNK_CHARS = int(os.getenv("TURN_MIND_LORE_CHUNK_CHARS", "300"))

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
