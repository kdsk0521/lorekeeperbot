# -*- coding: utf-8 -*-
"""
Custom Vars — 대형식화(선언형 변수 레지스트리) v0  [2026-08-18 Phase 1]

정본 스펙: `파티쳇수정/composition/대형식화_스펙_v0_2026-08-18.md`

한 문장: **리수가 Lua 스크립트로 푸는 "세계마다 다른 상태창·변수"를, 우리는 선언 한 줄로 푼다.**
유저가 변수를 선언하면 → 갱신은 기존 추출 콜에 묻어가고 → 클램프·캡은 코드가 집행하고 →
표시는 패널이 자동 수용한다. **새 매턴 콜 0.**

v0 범위(스펙 §7 Phase 1):
  타입 = gauge · counter 만 (flag·text 는 v1)
  스코프 = global · pc 만 (NPC 스코프는 v1)
  볼륨 캡 = 선언 12개 (코드 상수 — env 아님)

v1 범위(스펙 §7 Phase 2 — 2026-08-18. **형식이 풍부해진다**):
  타입 += **enum**(단계 목록. 단조/양방향 제약 + 한 번에 1단계 — 3축 상태기계
                   set_drive_gated 계보를 그대로 재사용한다. LLM은 델타가 아니라 **목표 단계명**)
        += **list**(항목별 수치 목록. `항목(진행%)` 형과 `재료(현재/목표)` 형.
                   연산 add/remove/delta — 항목 신설·제거도 evidence 필수)
  gauge  += **비대칭 델타캡**(상승 5 / 하강 20 — "천천히 쌓이고 빨리 식는")
  전 타입 += **format**(`{v} 골드` 값-표시 분리. 표시 계층에만 적용 — 저장은 여전히 수치)
  스코프 += **npc**(source∈{lore,manual} 인물만 — npc_manager.FROZEN_SOURCES 재사용.
                   값 저장은 변수당 {인물: 값})
  표시   += **헤더 자리표시자**(`[마나]` 치환 — status_panel._header_template_row) [09-07 P9 이사]
  ★새 매턴 콜 0(전부 기존 추출 콜 섹션 확장), env 레버 0(킬스위치는 여전히 하나).

  100% 도달 항목의 이동(연구중 → 완성품)은 **자동화하지 않는다** — rule 자연어와 산문의 몫.
  코드가 여기서 상태 전이를 발명하기 시작하면 이 설계가 피하려던 그 스크립트가 된다.

v2.5 범위(스펙 §5 — 2026-08-18. **기력이 들어온다**):
  **시스템 선언**(SYSTEM_VARS) — 코드가 심는 내장 변수. 유저 삭제 불가·모양 잠금,
    rule 과 델타캡·표시형식만 `!출력룰` 로 개정(= 이관의 요점: 캡과 rule 이 조정 가능한 값이 됐다).
  += **per_actor 값**(키=user_id) — 다인 플레이의 PC별 기력 보존. NPC 스코프와 같은 저장 모양.
  += **이월 승계**(lazy) — 레지스트리가 비었으면 옛 자리(ai_memory.vigor)를 읽고, 첫 델타가
    그 값을 기준선으로 삼는다. **마이그레이션 스크립트 없음 · 읽기는 쓰지 않음.**
  += **mentions 면제**(always_feed) — 상시 자원은 낱말이 없어도 관측한다.
  += **코드 소유 쓰기**(apply_system_delta) — 판정 Effort 선불. evidence 는 코드가 붙이고
    **비대칭 캡은 면제**(캡은 모델의 과장에 거는 재갈이지 규칙이 정한 선불을 깎을 근거가 아니다).
  ★기력의 코드 공식(다운타임 회복·baseline drain·cascade·자연회복·챕터 리프레시·서사 impact)은
    vigor_composure_module 쪽에서 **전량 삭제**됐다. 평형은 잔류 — 그 모듈은 이제 평형 전담이다.

값의 주인 분리:
  - 선언(스키마)·현재값 = **코드 소유**. 저장은 도메인 world_state 라 !다시 스냅샷 롤백이
    공짜로 따라온다(retry_last 의 전체 도메인 복원 — 새 워터마크 불필요).
  - 델타 = LLM 소유. **절대값 금지** — 이전 값은 코드가 쥐고 있으니 검증 가능하다
    (npc매니저가 실패한 "집행 수단 없는 델타 밴드"의 해소법).
  - rule(자연어 1줄) = **코드가 해석하지 않는다.** 추출 콜에 그대로 급식되는 LLM 몫.

⚠ 이 모듈은 discord 를 import 하지 않는다(스모크가 스텁 없이 돈다).
"""

import logging
import re
import time
from typing import Any, Dict, List, Optional, Tuple

import config
import domain_manager

logger = logging.getLogger("CustomVars")

# =========================================================
# 코드 상수 (env 아님 — 스펙 §7: env 레버는 CUSTOM_VARS_ENABLED 하나뿐)
# =========================================================

MAX_VARS = 80               # 채널당 선언 상한. mentions 게이트가 있어도 목록 자체가 프롬프트에 실린다.
#   [2026-09-13 P16] 12 → 80. 12 는 SimCore 실측(전형 7~18 · idol 66 · 아틀리에 68) **아래**였다 —
#   아틀리에 한 장을 그대로 옮기면 선언이 68번째에서 거부되고, 유저는 그걸 "준비물 파일이
#   반만 읽혔다"로 읽는다. 급식 캡(config.PROSE_FEED_MAX["values"]=40, P15)이 프롬프트 비대를
#   막는 관문이므로 선언 상한은 저장의 상한이지 프롬프트의 상한이 아니다.
NAME_MAX = 16               # 표시명 겸 어휘 게이트 키. 길면 mentions 게이트가 무뎌진다.
RULE_MAX = 160              # rule 은 추출 콜 급식분 — 문단이 아니라 한 줄.
# [2026-09-13 P17] text 값 길이. 이름표·별명·한 줄 소문이 사는 자리지 문단이 아니다 —
#   급식 줄·임베드 행·자리표시자가 전부 한 줄 안에 들어가야 한다(RULE_MAX 와 같은 급).
TEXT_MAX = 60
EVIDENCE_MAX = 120
SPAN_MAX = 1_000_000        # range 폭 상한(정수 오염 방지)

# --- v1 (Phase 2) 코드 상수 ---
MAX_STAGES = 8              # enum 단계 수 상한. 단계가 많아지면 그건 게이지지 단계가 아니다.
STAGE_NAME_MAX = 12
MAX_LIST_ITEMS = 20         # list 항목 수 **기본** 상한. 넘으면 새 항목이 거절된다.
#   [2026-09-13 P15] 8 → 20 + 선언별 `max`. 8은 값 층에서 거부하는 상한이라
#   "가장 먼저 터지는 상한"이었다 — SimCore 전형 15종(inventory·bag·memories)이
#   6~15 를 쓰고 아틀리에 materials 는 99 다. 선언이 규모를 말할 수 있어야
#   기본값이 장르를 고르지 않는다. 판정 자리는 `list_max(spec)` **한 곳**.
LIST_MAX_HARD = 99          # 선언별 `max` 의 하드 상한(1~99). 밖이면 기본값으로 되돌린다.
ITEM_NAME_MAX = 20
DEFAULT_MAX_STEP = 1        # enum 상승은 한 번에 1단계 — set_drive_gated 의 DRIVE_RISE_MAX_STEP 계보.
DEFAULT_ITEM_GOAL = 0       # stock 형에서 목표를 안 준 항목 = 목표 없음(0 = 미설정)
FORMAT_MAX = 40             # `{v} 골드` 정도. 표시 문자열이 문단이 되면 패널이 무너진다.
DELTA_CAP_MAX = SPAN_MAX    # 비대칭 캡 자체의 상한(정수 오염 방지)
MAX_NPC_VALUES = 12         # NPC 스코프 변수 하나가 인물별 값을 몇 명까지 들 수 있나

# --- 산문(렌더) 급식 캡 — 패널은 장부고 산문은 재료다. 여기선 짧은 쪽이 정답. ---
PROSE_FEED_MAX = 1200       # 블록 본문 전량 캡(문자). Slot 29 는 한 번 읽는 지면이지 장부가 아니다.
#   [2026-09-13 P15] 400 → 1200. 우리 줄은 SimCore promptState(89~379자, idol 544·아틀리에
#   932)보다 **길다**(한국어 이름 + 표시형식 + 변화 꼬리). 값 수 캡을 40 으로 올려 놓고
#   문자 캡을 400 에 두면 둘째 관문에서 같은 자리가 다시 잘린다.
PROSE_LIST_ITEMS = 8        # list 요약에 실리는 항목 수 — 패널 기본 상한(MAX_LIST_ITEMS)의 절반.
PROSE_NPC_MAX = 4           # 온스테이지라도 한 변수가 인물 넷을 넘으면 그건 인물이 아니라 군중이다.

# --- [2026-09-06 P3] 산문 급식 게이트. 선언 필드 `feed: {"prose": …}` 의 값 셋. ---
# ★기본이 **mentioned** 다. 옛 동작("유저 변수 전량")은 여기서 끝난다 — 선언 30개짜리
#   영지물이면 매턴 30줄이 Slot 29 에 실렸고, 그건 재료가 아니라 장부였다(스펙 §3.5 "빈 자리 ①").
FEED_MODES = ("always", "mentioned", "never")
DEFAULT_FEED_MODE = "mentioned"
_FEED_ALIASES = {
    "항상": "always", "always": "always", "상시": "always",
    "언급": "mentioned", "mentioned": "mentioned", "등장": "mentioned",
    "숨김": "never", "never": "never", "안함": "never", "없음": "never",
}
_FEED_PREFIX_RE = re.compile(r"^(표시|급식|feed)\s*[:=]?\s*", re.I)

VAR_TYPES = ("gauge", "counter", "enum", "list", "text")
VAR_SCOPES = ("global", "pc", "npc")

# list 항목 모드. progress = 항목 하나가 0~100 진행률 / stock = 항목 하나가 현재/목표 재고.
ITEM_MODES = ("progress", "stock")
PROGRESS_RANGE = [0, 100]
STOCK_RANGE = [0, 9999]

# [2026-09-09 P11] 레코드 목록 — list 항목이 **이름 붙은 숫자 필드**를 여럿 갖는다.
#   옛 list(수량 하나)는 마이그레이션 0 이다: `fields` 가 없는 선언은 **읽기에서만**
#   필드 1개(`n`) 레코드로 접힌다(`item_fields`). 저장 모양은 한 글자도 바뀌지 않는다.
MAX_ITEM_FIELDS = 6         # 한 항목이 드는 필드 수. 넘으면 그건 레코드가 아니라 표다.
ITEM_DEFAULT_FIELD = "n"    # 필드 선언이 없을 때 옛 수량이 서는 자리

# 코드 기관이 여전히 소유한 이름 — 레지스트리가 같은 이름을 만들면 패널에 두 번 그려진다.
# [2026-09-06 P8b] **비었다.** 평형이 기력을 따라 시스템 선언으로 넘어오면서 이 목록의 마지막
#   항목이 사라졌다. 예약은 "코드가 값을 계산하니 레지스트리는 손대지 마라"는 팻말이었는데,
#   이제 두 축 다 계산하는 코드가 없다 — 이름은 SYSTEM_VARS 가 소유하고, 유저가 같은 이름을
#   적으면 거부가 아니라 **개정으로 흡수**된다(_validate_system_override). 목록 자체는 남긴다:
#   다음에 코드 기관이 생기면 여기 한 줄이 다시 그 팻말이다.
RESERVED_NAMES: tuple = ()

# 도메인 world_state 키 2개. 새 테이블 없음.
KEY_DECL = "custom_vars"
KEY_VALS = "custom_var_values"

# =========================================================
# 시스템 선언 [2026-08-18 Phase 2.5 — 기력 이관]
# =========================================================
# ★코드가 심는 내장 선언이다. 유저 선언과 **같은 레지스트리·같은 집행기**를 쓰되 세 가지가 다르다:
#   1) 항상 존재한다 — 저장된 선언이 없어도 get_declarations 가 기본형을 얹는다(가상 선언).
#      저장되는 건 유저가 고친 칸(SYSTEM_EDITABLE)뿐이라, 여기 기본값을 고치면 기존 채널에도 퍼진다.
#   2) **삭제 불가 · 모양 잠금** — 타입/범위/스코프/초기값은 코드 소유. 유저가 `!출력룰` 로 만질 수
#      있는 건 rule 과 델타캡·표시형식뿐이다(= 이관의 요점: 캡과 rule 이 **조정 가능한 값**이 됐다).
#   3) **mentions 면제**(always_feed) — 능력을 쓴 장면에 "기력"이라는 낱말이 없어도 소모는 일어난다.
#      어휘 게이트는 유저 변수의 프롬프트 비대를 막는 장치지, 상시 자원의 관측을 끊는 장치가 아니다.
#
# 기력 = **정신 축의 뭉뚱그림**(집중력·정신력·MP류의 우산). 07-06 이전의 코드 공식
#   (baseline drain / cascade / status severity / 자연회복 / 휴식·다운타임 회복 / 챕터 리프레시)은
#   Phase 2.5 에서 전량 삭제됐고, 그 자리를 **rule 자연어 + 추출 콜의 관측 델타**가 대신한다.
#   코드가 지키는 건 이제 셋뿐 — 범위 클램프 / 비대칭 델타캡 / 판정 구간표.
SYSTEM_VARS: Dict[str, Dict[str, Any]] = {
    "기력": {
        "name": "기력",
        "type": "gauge",
        "scope": "pc",
        "range": [0, 100],
        "init": 100,                      # domain_manager 신규 참가자 초기값과 같은 수
        "rule": ("Drains hard on abilities, magic, and deep focus; wears down a little under "
                 "strain and pressure. Refills with rest, sleep, and calm."),
        # 비대칭 캡(레티어스 지정): 한 턴 최대 하강 7 · 상승 5. 삭제된 공식들이 하던 "속도 규율"을
        # 캡 하나가 대신한다 — 그리고 이제 이 두 수는 !출력룰 로 조정 가능한 값이다.
        "max_loss": 7,
        "max_gain": 5,
        "system": True,
        "per_actor": True,                # 값 = {user_id: int}. 다인 플레이의 PC별 기력 보존.
        "always_feed": True,              # mentions 면제
        "legacy_keys": ("vigor", "mental"),   # 이월 승계 소스(participants[uid].ai_memory)
        "toggle": "vigor_composure",          # !기력모듈 off = 동결(구 semantics 보존)
        # [2026-09-06 P8c] **켰다.** 값 dict 가 user_id 키와 인물 키를 **함께** 받는다
        #   (user_id 는 숫자 문자열, 인물은 이름 — 한 dict 안에서 충돌 0). 이관이 아니라
        #   키를 하나 더 받는 것이라, 급식·적용·첨자·표시가 전부 npc 스코프 변수와 같은
        #   경로를 탄다(새 rule 0 · 새 캡 0 · 새 문안 0). PC 값만 bus 에 실린다.
        "npc_enabled": True,
    },
    # [2026-09-06 P8b] **평형 = 시스템 선언 둘째.** 기력과 완전 대칭이다 — 08-18 에 기력에서
    #   지운 코드 공식(baseline drain / cross-axis cascade / 자연회복 / 휴식 회복 / status
    #   drain / 챕터 리프레시)이 평형에도 전량 삭제됐고, 그 자리를 이 rule 한 문장 + 전담
    #   추출 콜의 관측 델타가 대신한다. 코드가 지키는 건 범위 클램프·비대칭 캡·판정 구간표뿐.
    "평형": {
        "name": "평형",
        "type": "gauge",
        "scope": "pc",
        "range": [0, 100],
        "init": 100,
        # 중합 게이지 정의 한 문장 — MP·정신력·집중력의 우산. 장르별 공식이 애초에 말이 안 됐던
        #   이유가 여기 있다: 하나의 축이 여러 자원을 뭉뚱그리므로 "이 장르는 얼마 깎인다"가
        #   성립하지 않는다. 무엇이 깎고 무엇이 채우는지는 rule 이 말하고, 얼마인지는 캡이 문다.
        "rule": ("Mental footing: composure, focus, willpower. Cracks under fear, shock, "
                 "humiliation, prolonged strain; steadies with safety, rest, resolve, "
                 "small victories."),
        "max_loss": 7,
        "max_gain": 5,
        "system": True,
        "per_actor": True,
        "always_feed": True,
        "legacy_keys": ("composure",),        # 이월 승계 소스(participants[uid].ai_memory)
        "toggle": "vigor_composure",          # 기력과 같은 채널 토글 = 두 축 동시 동결
        "npc_enabled": True,                  # [2026-09-06 P8c] 기력과 대칭 — 무대 위 인물도 값을 갖는다
    },
}

# 유저가 `!출력룰` 로 고칠 수 있는 칸. 나머지는 코드 소유(모양 잠금).
SYSTEM_EDITABLE = ("rule", "max_gain", "max_loss", "format")

# 같은 것을 가리키는 다른 표기 — 유저가 `vigor`/`활력`로 적어도 기력 개정으로 흡수한다
# (새 변수로 만들어지면 패널에 두 번 그려진다 — 예약 이름 규율과 같은 이유).
SYSTEM_ALIASES = {"vigor": "기력", "활력": "기력", "기력": "기력",
                  "composure": "평형", "평정": "평형", "평형": "평형"}


def system_name(name: Any) -> str:
    """이름 → 시스템 변수 정본명. 시스템 변수가 아니면 ""."""
    key = str(name or "").strip()
    if not key:
        return ""
    if key in SYSTEM_VARS:
        return key
    return SYSTEM_ALIASES.get(key.lower(), "") or SYSTEM_ALIASES.get(key, "")

# 검증 규칙 요지 — 에러 메시지에 **그대로 동봉**한다(simcore "어긋나면 코드가 정답"의 축소판).
RULES_TEXT = (
    "**변수 선언 규칙**\n"
    "`!출력룰 추가 변수 이름 | 범위 | 시작값 | 스코프 | 규칙`\n"
    "- 이름: 1~%d자, `|` 없이. 기력/평형은 코드가 이미 씁니다.\n"
    "- 범위: `0-100` 형식. 왼쪽 < 오른쪽.\n"
    "- 시작값: 범위 안의 정수.\n"
    "- 스코프: `global`(세계) / `PC` / `NPC`(인물별 값 — 로어·수동 등록 인물만).\n"
    "- 규칙: 언제 오르고 내리는지 한국어 한 줄 (2자 이상, %d자 이내).\n"
    "- 타입: 생략하면 게이지. `counter`(카운터)를 적으면 카운터.\n"
    "- 채널당 %d개까지.\n"
    "예) `마나 | 0-100 | 시작 80 | PC | 마법을 쓰면 줄고 휴식하면 찬다`\n"
    "\n**단계형(enum)** — 수치가 아니라 이름 붙은 단계로 움직이는 것\n"
    "`평판 | 단계: 무명>안면>단골>소문난 | 시작 무명 | 단조 | global | 손님이 좋게 말하면 오른다`\n"
    "- 단계: `A>B>C` (2~%d개, 각 %d자 이내). 왼쪽이 낮고 오른쪽이 높습니다.\n"
    "- `단조`를 적으면 역행하지 않습니다(기본은 양방향). 어느 쪽이든 **한 번에 한 단계**.\n"
    "\n**문자열(text)** — 수가 아니라 **한 줄 글자**인 것 (별명·현 위치·한 줄 소문)\n"
    "`별명 | 문자열 | 시작 루나 | global | 사람들이 달리 부르기 시작하면 바뀐다`\n"
    "- 범위도 단계도 캡도 없습니다. %d자까지, 넘으면 잘립니다. 빈 값은 `—` 로 보입니다.\n"
    "\n**목록형(list)** — 항목마다 수치가 붙는 것\n"
    "`재료 | 목록 현재/목표 | global | 채집하면 늘고 조합하면 준다`\n"
    "`연구중 | 목록 진행%% | global | 작업하면 오른다`\n"
    "- 항목은 %d개까지. 항목 신설·삭제도 근거가 있어야 합니다.\n"
    "\n**표시 형식** — `표시: {v} 골드` 를 칸에 넣으면 패널·헤더가 그대로 씁니다.\n"
    "**비대칭 캡** — `상승 5 하강 20` 을 칸에 넣으면 한 턴에 그만큼까지만 움직입니다.\n"
    "**헤더** — `!출력룰 추가 헤더 잔고 [빚] / 평판 [평판]` 로 상단 줄에 값을 꽂습니다.\n"
    "**남은·퍼센트** — `[평판.남은]`(끝까지 얼마)·`[평판.퍼센트]`(얼마나 왔나)를 표시 줄에 쓸 수 있습니다.\n"
    "  끝이 없는 값(카운터)에는 남은 것도 없어 빈칸이 됩니다. 레코드 목록은 `[화분/양파/기한.남은]`.\n"
    "**날** — `날`은 코드가 세는 **누적 날짜**입니다(`일`은 달 안 날짜라 달을 넘으면 되감깁니다).\n"
    "  기한은 `기한 = 날 + 3` 처럼 **끝을 한 번** 적어 두면 상태창이 `남은 3일` 로 그립니다.\n"
    "\n**시스템 변수** — `기력`·`평형`은 코드가 심어 둔 변수라 지울 수 없습니다. 대신 **규칙과 캡은 고칠 수 있습니다**:\n"
    "`!출력룰 수정 변수 기력 | 상승 5 하강 7 | 능력을 쓰면 크게 깎이고 쉬면 찬다`\n"
    "(범위·타입·스코프는 코드 소유라 바뀌지 않습니다.)"
) % (NAME_MAX, RULE_MAX, MAX_VARS, MAX_STAGES, STAGE_NAME_MAX, TEXT_MAX, MAX_LIST_ITEMS)


def is_enabled() -> bool:
    """킬스위치. 0이면 저작·급식·적용·표시 전부 죽는다."""
    try:
        return int(getattr(config, "CUSTOM_VARS_ENABLED", 1) or 0) != 0
    except (TypeError, ValueError):
        return True


# =========================================================
# 저장 (도메인 world_state — !다시 롤백 자동 포함)
# =========================================================

def get_declarations(channel_id: str) -> Dict[str, Dict[str, Any]]:
    """{name: spec}. 없으면 {}.

    [Phase 2.5] **시스템 선언은 가상으로 얹힌다** — 저장된 것이 없어도 SYSTEM_VARS 기본형이
    항상 목록에 있고, 저장분은 유저가 고친 칸(SYSTEM_EDITABLE)만 덮어쓴다. 그래서
    마이그레이션 스크립트 없이도 모든 채널이 같은 날 같은 선언을 갖는다.
    """
    if not is_enabled():
        return {}
    decl = _read_decl(channel_id)
    out: Dict[str, Dict[str, Any]] = dict(decl) if isinstance(decl, dict) else {}
    for nm, base in SYSTEM_VARS.items():
        stored = out.get(nm) if isinstance(out.get(nm), dict) else {}
        merged = dict(base)
        for k in SYSTEM_EDITABLE:
            v = stored.get(k)
            if v not in (None, ""):
                merged[k] = v
        out[nm] = merged
    return out


def _read_decl(channel_id: str) -> Optional[Dict[str, Any]]:
    """[2026-09-06 P7] 선언은 선언 층(도메인 루트 output_decl)에 산다 — `!클리어` 생존.
    **lazy 이월**: 선언 층이 비었고 옛 자리(world_state[KEY_DECL])에 있으면 그걸 읽되
    옮기지 않는다(08-18 규율: 읽기가 파일을 바꾸면 롤백이 불가능해진다). 첫 쓰기가 옮긴다.
    """
    try:
        cur = domain_manager.get_output_decl(channel_id).get(KEY_DECL)
        if isinstance(cur, dict) and cur:
            return cur
    except Exception as e:
        logger.debug("[CustomVar] declaration layer read skipped: %s", e)
    try:
        legacy = (domain_manager.get_world_state(channel_id) or {}).get(KEY_DECL)
    except Exception as e:
        logger.debug("[CustomVar] declaration read skipped: %s", e)
        return None
    return legacy if isinstance(legacy, dict) else None


def _system_active(channel_id: str, spec: Dict[str, Any]) -> bool:
    """시스템 변수의 채널 토글. `!기력모듈 off` = 수치 동결(구 vigor_composure semantics 보존)."""
    if str((spec or {}).get("toggle", "")) != "vigor_composure":
        return True
    try:
        return bool(domain_manager.is_vigor_composure_active(channel_id))
    except Exception:
        return True


def get_values(channel_id: str) -> Dict[str, Dict[str, Any]]:
    """{name: {"value": int, "last_change": {...}|None}}. 없으면 {}."""
    if not is_enabled():
        return {}
    try:
        vals = (domain_manager.get_world_state(channel_id) or {}).get(KEY_VALS)
    except Exception as e:
        logger.debug("[CustomVar] value read skipped: %s", e)
        return {}
    return vals if isinstance(vals, dict) else {}


def _current_turn(channel_id: str) -> int:
    try:
        return int((domain_manager.get_world_state(channel_id) or {}).get("turn_index", 0) or 0)
    except Exception:
        return 0


def _save(channel_id: str, decl: Dict[str, Any], vals: Dict[str, Any]) -> None:
    """[2026-09-06 P7] **선언과 값이 갈라선다** — 선언은 선언 층(채널 수명), 값은
    world_state(세션 수명). 이 함수가 이월 지점이다: 옛 자리에 남은 선언을 여기서 비운다
    (두 자리에 남으면 읽기 폴백이 삭제·개정을 되살린다)."""
    layer = domain_manager.get_output_decl(channel_id)
    _reduced = _reduce_system(decl)
    # [2026-09-24 감사] 값 쓰기마다 선언 층까지 통째 저장(save_domain = JSON 전체 + SQLite 미러)
    #   하던 것을, 선언이 실제로 달라졌을 때만 쓴다. 같으면 쓰기 결과가 같으므로 동작 무변경
    #   (이월 — 옛 자리 선언이 층에 없으면 서로 달라 여기서 그대로 옮겨진다).
    if layer.get(KEY_DECL) != _reduced:
        layer[KEY_DECL] = _reduced
        domain_manager.update_output_decl(channel_id, layer)
    ws = domain_manager.get_world_state(channel_id) or {}
    if ws.get(KEY_DECL):
        ws[KEY_DECL] = {}
    ws[KEY_VALS] = vals
    domain_manager.update_world_state(channel_id, ws)


def _reduce_system(decl: Dict[str, Any]) -> Dict[str, Any]:
    """저장 직전 시스템 선언을 **개정분만** 남긴다.

    ★전문을 그대로 저장하면 SYSTEM_VARS 기본값을 고쳐도 기존 채널엔 영영 안 닿는다
      (선언 드리프트, 스펙 §8). 저장은 "유저가 무엇을 바꿨나"만 담는 것이 정답이다.
    """
    out: Dict[str, Any] = {}
    for k, v in (decl or {}).items():
        if not isinstance(v, dict):
            continue
        if not v.get("system"):
            out[k] = v
            continue
        base = SYSTEM_VARS.get(k, {})
        ov = {kk: v[kk] for kk in SYSTEM_EDITABLE
              if v.get(kk) not in (None, "") and v.get(kk) != base.get(kk)}
        if ov:
            out[k] = {"name": k, "system": True, **ov}
    return out


# =========================================================
# 선언 — 파서 (a) 반구조 파이프 문법. 결정론, LLM 경로의 폴백.
# =========================================================

_RANGE_RE = re.compile(r"(-?\d+)\s*(?:~|-|—|–|to)\s*(-?\d+)")
_INT_RE = re.compile(r"-?\d+")

_SCOPE_ALIASES = {
    "global": "global", "전역": "global", "세계": "global", "월드": "global", "world": "global",
    "pc": "pc", "플레이어": "pc", "주인공": "pc", "player": "pc", "캐릭터": "pc",
    "npc": "npc", "인물": "npc", "인물별": "npc", "npc별": "npc", "character": "npc",
}
_TYPE_ALIASES = {
    "gauge": "gauge", "게이지": "gauge", "수치": "gauge",
    "counter": "counter", "카운터": "counter", "개수": "counter", "횟수": "counter",
    "enum": "enum", "단계": "enum", "단계형": "enum", "stage": "enum", "stages": "enum",
    "list": "list", "목록": "list", "목록형": "list", "리스트": "list", "항목": "list",
    # [2026-09-13 P17] text — 값이 수가 아니라 **한 줄 문자열**인 것(별명·현 위치명·소문).
    #   수가 아니므로 범위도 델타도 없다: 쓰기는 늘 set 이고 캡은 길이 하나뿐이다.
    "text": "text", "문자열": "text", "이름": "text", "텍스트": "text", "string": "text",
}
_ITEM_MODE_ALIASES = {
    "progress": "progress", "진행": "progress", "진행률": "progress", "진행%": "progress",
    "퍼센트": "progress", "%": "progress",
    "stock": "stock", "재고": "stock", "현재/목표": "stock", "수량": "stock", "재료": "stock",
}

# 단계 목록 칸: `단계: 무명>안면>단골` / `무명 > 안면 > 단골` / `무명→안면→단골`
_STAGE_PREFIX_RE = re.compile(r"^(?:단계|스테이지|stages?|phase)\s*[:=]?\s*", re.I)
_STAGE_SPLIT_RE = re.compile(r"\s*(?:>|→|->|»)\s*")
# 비대칭 캡 칸: `상승 5 하강 20` / `+5 -20` / `max_gain 5`
_GAIN_RE = re.compile(r"(?:상승|증가|오름|max[_ ]?gain|gain)\s*[:=]?\s*(\d+)", re.I)
_LOSS_RE = re.compile(r"(?:하강|감소|내림|max[_ ]?loss|loss)\s*[:=]?\s*(\d+)", re.I)
_SIGNED_CAP_RE = re.compile(r"^\s*\+\s*(\d+)\s*/?\s*[-−]\s*(\d+)\s*$")
# 표시 형식 칸: `표시: {v} 골드`
_FORMAT_PREFIX_RE = re.compile(r"^(?:표시|형식|format|display)\s*[:=]\s*", re.I)
_MONOTONIC_WORDS = ("단조", "역행불가", "역행 불가", "monotonic", "irreversible", "일방")
_BIDIRECTIONAL_WORDS = ("양방향", "가역", "오르내림", "bidirectional", "reversible")


def _parse_stage_cell(part: str) -> Optional[List[str]]:
    """`단계: A>B>C` → ["A","B","C"]. 모양이 아니면 None (rule 문장 오식별 방지)."""
    body = _STAGE_PREFIX_RE.sub("", str(part or "").strip())
    if not body:
        return None
    tokens = [t.strip() for t in _STAGE_SPLIT_RE.split(body)]
    tokens = [t for t in tokens if t]
    if len(tokens) < 2:
        return None
    # 단계 이름은 짧다. 문장이 `>` 를 품고 들어오는 경우를 여기서 거른다.
    if any(len(t) > STAGE_NAME_MAX for t in tokens):
        return None
    return tokens[:MAX_STAGES]


def parse_pipe_declaration(text: str) -> Tuple[Optional[Dict[str, Any]], str]:
    """`마나 | 0-100 | 시작 80 | PC | 마법 쓰면 줄고 휴식하면 찬다` → spec dict.

    ★순서 의존이 아니라 **모양 인식**이다: 이름은 첫 칸, 나머지는 범위/시작/스코프/타입
      패턴에 걸리는 칸을 집어가고 **남은 칸이 rule**. 유저가 칸 순서를 바꿔도 통과한다.
    Returns: (spec, "") 또는 (None, 에러문).
    """
    raw = str(text or "").strip()
    if not raw:
        return None, "변수 선언이 비어 있습니다."
    parts = [p.strip() for p in raw.split("|")]
    parts = [p for p in parts if p]
    if len(parts) < 2:
        return None, "`|` 로 칸을 나눠 주세요 (이름 다음에 최소한 범위가 필요합니다)."

    name = parts[0]
    rest = parts[1:]

    spec: Dict[str, Any] = {"name": name, "type": "gauge", "scope": "global"}
    leftovers: List[str] = []
    rng_done = init_done = scope_done = type_done = False
    stage_done = fmt_done = cap_done = mode_done = feed_done = False

    for part in rest:
        low = part.lower().strip()
        # 스코프/타입은 **칸 전체가 토큰일 때만** 인식한다(rule 문장 오식별 방지).
        bare = re.sub(r"^(스코프|scope|범위|type|타입|대상)\s*[:=]?\s*", "", low).strip()
        if not scope_done and bare in _SCOPE_ALIASES:
            spec["scope"] = _SCOPE_ALIASES[bare]
            scope_done = True
            continue
        # [v1] 목록형: `목록`, `목록 진행%`, `목록 현재/목표` — 타입과 항목 모드를 한 칸에서 읽는다.
        _mode_body = re.sub(r"^(목록형?|리스트|list|항목)\s*[:=]?\s*", "", bare).strip()
        _mode_hit = (
            not type_done and bare != _mode_body
            and (not _mode_body or _mode_body in _ITEM_MODE_ALIASES
                 or ("/" in _mode_body and len(_mode_body) <= 10))
        )
        if _mode_hit:
            spec["type"] = "list"
            type_done = True
            if _mode_body:
                spec["item_mode"] = _ITEM_MODE_ALIASES.get(_mode_body, "stock")
                mode_done = True
            continue
        if not mode_done and bare in _ITEM_MODE_ALIASES and spec.get("type") == "list":
            spec["item_mode"] = _ITEM_MODE_ALIASES[bare]
            mode_done = True
            continue
        if not type_done and bare in _TYPE_ALIASES:
            spec["type"] = _TYPE_ALIASES[bare]
            type_done = True
            continue
        # [v1] 표시 형식 — `표시: {v} 골드`. 값-표시 분리는 표시 계층에만 산다.
        if not fmt_done and _FORMAT_PREFIX_RE.match(part.strip()):
            spec["format"] = _FORMAT_PREFIX_RE.sub("", part.strip())
            fmt_done = True
            continue
        # [v1] 단계 목록 — 범위 인식보다 **먼저** 본다(`0>1>2` 같은 단계도 단계다).
        if not stage_done:
            _stages = _parse_stage_cell(part)
            if _stages:
                spec["stages"] = _stages
                spec["type"] = "enum"
                stage_done = True
                type_done = True
                continue
        # [v1] 비대칭 델타캡 — `상승 5 하강 20` / `+5/-20`
        if not cap_done:
            _sc = _SIGNED_CAP_RE.match(part)
            if _sc:
                spec["max_gain"], spec["max_loss"] = int(_sc.group(1)), int(_sc.group(2))
                cap_done = True
                continue
            _g, _l = _GAIN_RE.search(part), _LOSS_RE.search(part)
            if _g or _l:
                if _g:
                    spec["max_gain"] = int(_g.group(1))
                if _l:
                    spec["max_loss"] = int(_l.group(1))
                cap_done = True
                continue
        # [2026-09-06 P3] 급식 게이트 — `표시 항상` / `항상` 낱말 하나. 칸 전체가 토큰일 때만.
        if not feed_done:
            _fb = _FEED_PREFIX_RE.sub("", part.strip()).strip().lower()
            if (_fb in _FEED_ALIASES) and (_fb != bare or bare in _FEED_ALIASES):
                spec["feed"] = {"prose": _FEED_ALIASES[_fb]}
                feed_done = True
                continue
        # [v1] 단조/양방향 — 칸 전체가 토큰일 때만.
        if bare in _MONOTONIC_WORDS:
            spec["monotonic"] = True
            continue
        if bare in _BIDIRECTIONAL_WORDS:
            spec["monotonic"] = False
            continue
        m = _RANGE_RE.search(part)
        if not rng_done and m:
            spec["range"] = [int(m.group(1)), int(m.group(2))]
            rng_done = True
            continue
        if not init_done and ("시작" in part or "init" in low or "start" in low):
            mi = _INT_RE.search(part)
            if mi:
                spec["init"] = int(mi.group(0))
                init_done = True
                continue
            # [v1] 단계형 시작값은 정수가 아니라 **단계 이름**이다.
            _iv = re.sub(r"^(시작값?|init|start)\s*[:=]?\s*", "", part.strip(), flags=re.I).strip()
            if _iv:
                spec["init"] = _iv
                init_done = True
                continue
        if not init_done and _INT_RE.fullmatch(part.strip()):
            spec["init"] = int(part.strip())
            init_done = True
            continue
        leftovers.append(part)

    if leftovers:
        spec["rule"] = " ".join(leftovers).strip()
    return spec, ""


# =========================================================
# [v1] 공용 해석기 — 단계 이름 / NPC 출처 게이트 / 값 표기
# =========================================================

def _match_stage(target: Any, stages: List[str]) -> str:
    """모델이 적은 단계 이름을 선언된 단계로 해석. 못 찾으면 "".

    ★관용은 표기까지만이다 — 목록 밖의 단계는 **만들어지지 않는다**
      (set_drive_gated 의 `target not in stages → invalid` 와 같은 규율).
    """
    key = str(target or "").strip()
    if not key:
        return ""
    for s in stages:
        if key == s:
            return s
    low = key.lower()
    for s in stages:
        if str(s).strip().lower() == low:
            return s
    # 조사·수식이 붙어 온 경우(`단골로`, `소문난 상태`)만 접두 일치로 구제.
    for s in stages:
        if low.startswith(str(s).strip().lower()):
            return s
    return ""


def allowed_npc_names(channel_id: str) -> List[str]:
    """NPC 스코프 변수가 값을 가질 수 있는 인물 = source∈{lore, manual}.

    ★새 분류를 만들지 않는다 — npc_manager.FROZEN_SOURCES(사람이 쓴 확정 시트)를 그대로
      읽는다. turn_mail 의 💭 출처 게이트와 같은 계보이고, 즉석 군중(session/자동 등록)이
      인물별 값을 만들어 폭주시키는 것을 여기서 막는다.
    """
    try:
        import npc_manager as _npm
        allowed = {str(s).lower() for s in getattr(_npm, "FROZEN_SOURCES", ("lore", "manual"))}
        npcs = _npm.get_npcs(channel_id) or {}
    except Exception as e:
        logger.debug("[CustomVar] NPC 출처 게이트 조회 실패: %s", e)
        return []
    out = []
    for nm, rec in (npcs or {}).items():
        if not isinstance(rec, dict):
            continue
        if _npm.npc_source(rec) in allowed:   # [2026-09-16] 파생 source
            out.append(str(nm))
    return out


def npc_keyed(spec: Any) -> bool:
    """이 선언의 값 dict 가 **인물 키**를 받는가.

    [2026-09-06 P8c] 둘이 같은 답을 쓴다: npc 스코프 변수(키=인물뿐) / `npc_enabled` 시스템
    변수(키=user_id + 인물). 분기가 자리마다 흩어지면 "여긴 되고 저긴 안 되는" 첨자가 생긴다 —
    급식·적용·expr·표시가 전부 이 한 문을 본다.
    """
    if not isinstance(spec, dict):
        return False
    return str(spec.get("scope")) == "npc" or bool(spec.get("npc_enabled"))


def feed_npc_names(channel_id: str) -> List[str]:
    """`npc_enabled` 시스템 변수가 급식·표시에 싣는 인물 = **무대 위 ∩ 허용 출처**.

    npc 스코프 변수는 명부 전체(allowed_npc_names)를 급식하고 표시에서만 무대를 본다.
    시스템 변수는 매턴 실리므로(always_feed) 급식 단계에서 이미 무대를 판정한다 —
    무대 밖 인물의 기력을 매턴 신고 대상으로 올리면 그 인물이 있는 것처럼 읽힌다.
    """
    allowed = set(allowed_npc_names(channel_id))
    if not allowed:
        return []
    return [n for n in _onstage_names(channel_id) if n in allowed][:MAX_NPC_VALUES]


def _npc_keys(channel_id: str, per: Dict[str, Any]) -> List[str]:
    """값 dict 안에서 **인물 키만** 골라낸다 — 인물별 값 상한(MAX_NPC_VALUES)이 셀 대상.

    [2026-09-06 P8c] npc_enabled 시스템 변수의 dict 엔 user_id 키가 섞여 있다. 참가자 수가
    상한을 먹으면 무대 위 인물이 값을 못 갖는다 — 상한은 인물 폭주를 막는 장치지 참가자를
    세는 장치가 아니다(키 모양으로 추측하지 않고 허용 명부를 본다).
    """
    allowed = set(allowed_npc_names(channel_id))
    return [k for k in per if str(k) in allowed]


def resolve_npc(channel_id: str, name: Any) -> str:
    """모델이 적은 인물명 → 허용 인물의 정본 이름. 미허용·미상이면 "" (= 그 항목은 폐기)."""
    key = str(name or "").strip()
    if not key:
        return ""
    allowed = allowed_npc_names(channel_id)
    for nm in allowed:
        if nm == key:
            return nm
    low = key.lower()
    for nm in allowed:
        if nm.lower() == low:
            return nm
    return ""


# =========================================================
# [Phase 2.5] per-actor 값 — 참가자별 슬롯 + 이월 승계
# =========================================================
# ★NPC 스코프의 인물별 dict 와 **같은 저장 모양**(값={키: 값}, 도장={키: 도장})이다.
#   키만 인물명 대신 user_id 다. 다인 플레이에서 PC 마다 기력이 따로 있어야 하기 때문이고,
#   그래서 표시(패널 PC별 줄)가 이관 전후로 같다.

def _default_actor(channel_id: str) -> str:
    """actor 를 못 받은 표시 경로(헤더 자리표시자 등)가 쓰는 기본 참가자."""
    try:
        return next(iter(domain_manager.get_active_participants(channel_id) or {}), "")
    except Exception:
        return ""


def _actor_label(channel_id: str, uid: str) -> str:
    """user_id → 가면. 못 찾으면 uid 그대로(표시 전용)."""
    try:
        p = domain_manager.get_participant_data(channel_id, uid) or {}
        return str(p.get("mask") or uid)
    except Exception:
        return str(uid)


def _legacy_value(channel_id: str, spec: Dict[str, Any], actor: str) -> Optional[int]:
    """이월 승계 소스 — 기존 채널이 들고 있던 값(participants[uid].ai_memory.vigor.value).

    ★마이그레이션 스크립트를 쓰지 않는다: 레지스트리에 엔트리가 생기기 전까지 **읽기가 옛
      자리를 본다**. 첫 델타가 들어오는 순간 그 값을 기준선으로 삼아 엔트리가 생기고, 그 뒤로
      옛 자리는 다시 읽히지 않는다(쓰기도 끊겼다 — vigor_composure 다이어트).
    """
    keys = spec.get("legacy_keys") or ()
    if not keys or not actor:
        return None
    try:
        p = domain_manager.get_participant_data(channel_id, actor) or {}
        mem = p.get("ai_memory", {}) or {}
    except Exception as e:
        logger.debug("[CustomVar] 이월 소스 조회 실패: %s", e)
        return None
    for k in keys:
        src = mem.get(k)
        if isinstance(src, dict) and isinstance(src.get("value"), (int, float)):
            return int(src["value"])
    return None


def _actor_base(channel_id: str, spec: Dict[str, Any],
                entry: Dict[str, Any], actor: str) -> int:
    """per_actor 변수의 현재값 결정: 레지스트리 → 이월 승계 → init. **읽기는 쓰지 않는다.**"""
    try:
        lo, hi = int((spec.get("range") or [0, 100])[0]), int((spec.get("range") or [0, 100])[1])
    except (TypeError, ValueError, IndexError):
        lo, hi = 0, 100
    per = entry.get("value") if isinstance(entry, dict) else None
    if isinstance(per, dict) and actor in per:
        try:
            return max(lo, min(hi, int(per[actor])))
        except (TypeError, ValueError):
            pass
    legacy = _legacy_value(channel_id, spec, actor)
    if legacy is not None:
        return max(lo, min(hi, legacy))
    try:
        return max(lo, min(hi, int(spec.get("init", hi))))
    except (TypeError, ValueError):
        return hi


def get_system_value(channel_id: str, name: Any, actor: str = "") -> Optional[int]:
    """시스템 변수의 현재값. 소비자 전원이 여기 하나만 읽는다(값의 위치가 여기로 옮겨졌다).

    Returns: int / 기능이 꺼졌거나 그런 변수가 없으면 **None** — 호출부가 옛 경로로 폴백한다.
    """
    nm = system_name(name)
    if not nm or not is_enabled():
        return None
    decl = get_declarations(channel_id)
    spec = decl.get(nm)
    if not isinstance(spec, dict):
        return None
    entry = get_values(channel_id).get(nm)
    entry = entry if isinstance(entry, dict) else {}
    if spec.get("per_actor"):
        return _actor_base(channel_id, spec, entry, actor or _default_actor(channel_id))
    raw = entry.get("value", spec.get("init"))
    return int(raw) if isinstance(raw, (int, float)) else None


def vigor_value(channel_id: str, actor: str = "", mem: Optional[Dict[str, Any]] = None) -> int:
    """기력 현재값 한 줄 조회 — **표시 소비자 공용 문**(패널·헤더·산문·슬롯·명령어·분석).

    ★소비자가 저마다 폴백을 적으면 이관 후에도 자리마다 다른 값이 보인다. 폴백은 여기 한 곳:
      레지스트리 → 이월 승계(ai_memory) → 넘겨받은 mem → 100.
    """
    try:
        v = get_system_value(channel_id, "기력", actor)
        if v is not None:
            return int(v)
    except Exception as e:
        logger.debug("[CustomVar] 기력 조회 실패: %s", e)
    src = (mem or {}).get("vigor") or (mem or {}).get("mental") or {}
    try:
        return int(src.get("value", 100))
    except (TypeError, ValueError, AttributeError):
        return 100


def composure_value(channel_id: str, actor: str = "", mem: Optional[Dict[str, Any]] = None) -> int:
    """평형 현재값 한 줄 조회 — **표시 소비자 공용 문**(패널·헤더·산문·슬롯·명령어·분석).

    [2026-09-06 P8b] `vigor_value` 와 같은 계단이다: 레지스트리 → 이월 승계(ai_memory) →
      넘겨받은 mem → 100. 소비자가 저마다 폴백을 적으면 자리마다 다른 값이 보인다 —
      두 축의 폴백은 이 파일 두 함수뿐이다.
    """
    try:
        v = get_system_value(channel_id, "평형", actor)
        if v is not None:
            return int(v)
    except Exception as e:
        logger.debug("[CustomVar] 평형 조회 실패: %s", e)
    src = (mem or {}).get("composure") or {}
    try:
        return int(src.get("value", 100))
    except (TypeError, ValueError, AttributeError):
        return 100


def format_value(spec: Dict[str, Any], raw: Any) -> str:
    """값 → 표시 문자열. `format`("{v} 골드")가 있으면 그것이 이긴다.

    ★값-표시 분리: 저장은 언제나 수치·단계명이고 format 은 **표시 계층에만** 산다
      (헤더 자리표시자도 같은 함수를 쓴다 — 두 표시가 어긋나지 않게).
    """
    if not isinstance(spec, dict):
        return str(raw)
    vtype = str(spec.get("type", "gauge"))
    fmt = str(spec.get("format", "") or "")
    if vtype == "enum":
        base = str(raw or "")
    elif isinstance(raw, (int, float)):
        try:
            lo, hi = int((spec.get("range") or [0, 0])[0]), int((spec.get("range") or [0, 0])[1])
        except (TypeError, ValueError, IndexError):
            lo, hi = 0, 0
        if fmt:
            base = str(int(raw))
        elif vtype == "counter":
            base = f"{int(raw)}"
        else:
            base = f"{int(raw)}/{hi}" if lo == 0 else f"{int(raw)} ({lo}-{hi})"
    else:
        base = str(raw)
    if not fmt:
        return base
    try:
        lo, hi = (spec.get("range") or [0, 0])[:2]
    except (TypeError, ValueError, IndexError):
        lo, hi = 0, 0
    return (fmt.replace("{v}", base).replace("{max}", str(hi)).replace("{min}", str(lo)))[:200]


def item_fields(spec: Any) -> Dict[str, List[int]]:
    """이 목록 항목이 드는 필드표 `{이름: [lo, hi]}`. **선언 순서 보존.**

    [2026-09-09 P11] `fields` 가 없는 옛 list 는 여기서 필드 1개(`n`)짜리 레코드로 접힌다 —
    옛 저장분을 건드리지 않고 읽는 쪽만 하나로 만드는 자리다(마이그레이션 0).
    """
    if not isinstance(spec, dict):
        return {ITEM_DEFAULT_FIELD: list(STOCK_RANGE)}
    flds = spec.get("fields")
    if isinstance(flds, dict) and flds:
        return {str(k): list(v)[:2] for k, v in flds.items()}
    return {ITEM_DEFAULT_FIELD: list(spec.get("item_range") or STOCK_RANGE)[:2]}


def list_max(spec: Any) -> int:
    """이 목록 선언의 항목 수 상한. 선언별 `max` 가 있으면 그것, 없으면 MAX_LIST_ITEMS.

    [2026-09-13 P15] 값 층 거부(신설 거부)와 표시 슬라이스가 **같은 수**를 봐야 한다 —
    둘이 갈라지면 "저장은 됐는데 화면·급식엔 없는 항목"이 생기고, 그건 GM 이 모르는
    핸드아웃의 목록판이다. 그래서 판정은 이 함수 한 곳뿐이다.
    범위 밖(1 미만·LIST_MAX_HARD 초과)·수 아님은 **무시하고 기본값**으로 돌아간다 —
    라우터가 채우는 칸이라 모델의 오타가 상한을 지워선 안 된다.
    """
    if isinstance(spec, dict):
        raw = spec.get("max")
        if raw not in (None, ""):
            try:
                n = int(raw)
            except (TypeError, ValueError):
                return MAX_LIST_ITEMS
            if 1 <= n <= LIST_MAX_HARD:
                return n
    return MAX_LIST_ITEMS


def item_stage(spec: Any, rec: Any) -> str:
    """레코드의 단계 이름. `stage_of` 필드값이 `stage_at` 문턱 중 어디에 서 있나.

    문턱이 없으면 필드 범위를 단계 수로 등분한다. 단계 선언이 없으면 빈 문자열(표시 없음).
    """
    if not isinstance(spec, dict):
        return ""
    stages = [str(x) for x in (spec.get("stages") or [])]
    fld = str(spec.get("stage_of", "") or "")
    if len(stages) < 2 or not fld:
        return ""
    flds = item_fields(spec)
    if fld not in flds:
        return ""
    lo, hi = int(flds[fld][0]), int(flds[fld][1])
    try:
        v = int((rec or {}).get(fld, lo)) if isinstance(rec, dict) else int(rec or lo)
    except (TypeError, ValueError):
        v = lo
    at = spec.get("stage_at")
    if isinstance(at, (list, tuple)) and len(at) == len(stages):
        idx = 0
        for i, t in enumerate(at):
            try:
                if v >= int(t):
                    idx = i
            except (TypeError, ValueError):
                continue
        return stages[idx]
    span = max(1, hi - lo + 1)
    idx = int((v - lo) * len(stages) // span)
    return stages[max(0, min(len(stages) - 1, idx))]


def value_stage(spec: Any, raw: Any) -> str:
    """[2026-09-13 P17] 값 하나가 선 **단계 이름**. `stage_at` 경계가 있으면 그 경계로,
    없으면 range 등분(item_stage 와 같은 규율). 단계 선언이 없으면 빈 문자열."""
    if not isinstance(spec, dict):
        return ""
    stages = [str(x) for x in (spec.get("stages") or [])]
    if len(stages) < 2:
        return ""
    try:
        v = int(raw)
    except (TypeError, ValueError):
        return ""
    at = spec.get("stage_at")
    if isinstance(at, (list, tuple)) and len(at) == len(stages):
        idx = 0
        for i, t in enumerate(at):
            try:
                if v >= int(t):
                    idx = i
            except (TypeError, ValueError):
                continue
        return stages[idx]
    try:
        lo, hi = int((spec.get("range") or [0, 0])[0]), int((spec.get("range") or [0, 0])[1])
    except (TypeError, ValueError, IndexError):
        return ""
    span = max(1, hi - lo + 1)
    idx = int((v - lo) * len(stages) // span)
    return stages[max(0, min(len(stages) - 1, idx))]


def value_bounds(spec: Any, raw: Any) -> Optional[Tuple[int, int]]:
    """[2026-09-13 P17] 지금 값이 든 **구간의 (시작, 끝)**. 끝이 없으면 None.

    ★한 쌍의 세 표기(09-13): 차오르는 쪽(`값/최대`)과 줄어드는 쪽(`남은`)은 같은 쌍의
      두 읽기다. 그 쌍을 정하는 자리는 여기 하나뿐이라 `.남은`·`.퍼센트`가 갈리지 않는다.
      stages 가 있는 게이지는 **끝 = 다음 단계 경계**(경험치바), 마지막 단계는 range hi.
      counter·enum·text·list 는 끝이 없다 — 세는 값에는 천장이 뜻이 없다.
    """
    if not isinstance(spec, dict):
        return None
    if str(spec.get("type", "gauge")) != "gauge":
        return None
    try:
        lo, hi = int((spec.get("range") or [])[0]), int((spec.get("range") or [])[1])
    except (TypeError, ValueError, IndexError):
        return None
    try:
        v = int(raw)
    except (TypeError, ValueError):
        v = lo
    stages = [str(x) for x in (spec.get("stages") or [])]
    at = spec.get("stage_at")
    if len(stages) >= 2 and isinstance(at, (list, tuple)) and len(at) == len(stages):
        idx = 0
        for i, t in enumerate(at):
            try:
                if v >= int(t):
                    idx = i
            except (TypeError, ValueError):
                continue
        start = int(at[idx])
        end = int(at[idx + 1]) if idx + 1 < len(at) else hi
        return (start, end)
    return (lo, hi)


def _clean_record(spec: Dict[str, Any], rec: Any) -> Optional[Dict[str, Any]]:
    """저장 모양 하나를 선언에 맞춰 정돈한다. **옛 모양은 옛 모양 그대로 나간다.**

    필드 선언이 없으면 `{n, goal}` 한 쌍(옛 list) — 수가 아니면 None(옛 동작: 그 항목 폐기).
    필드 선언이 있으면 필드별 클램프 + 존재 표식 `n=1`(has/count·_item_n 무회귀).
    """
    src = dict(rec) if isinstance(rec, dict) else {ITEM_DEFAULT_FIELD: rec}
    flds = item_fields(spec)
    inits = spec.get("field_init") if isinstance(spec.get("field_init"), dict) else {}
    out: Dict[str, Any] = {}
    for fname, rng in flds.items():
        try:
            lo, hi = int(rng[0]), int(rng[1])
        except (TypeError, ValueError, IndexError):
            lo, hi = STOCK_RANGE
        raw = src.get(fname, inits.get(fname, lo))
        try:
            v = int(raw)
        except (TypeError, ValueError):
            if not spec.get("fields"):
                return None                 # 옛 list 무회귀: 수가 아닌 항목은 폐기
            v = int(inits.get(fname, lo) or lo)
        out[fname] = max(lo, min(hi, v))
    if ITEM_DEFAULT_FIELD not in out:
        out[ITEM_DEFAULT_FIELD] = 1         # 레코드의 **존재** 한 칸(수량이 아니다)
    try:
        out["goal"] = max(0, int(src.get("goal", 0) or 0))
    except (TypeError, ValueError):
        out["goal"] = 0
    return out


# [2026-09-13 P17] 기한 필드 별칭 — **고정된 끝**을 저장하는 필드. 저장은 절대 일수(`날`)고
#   표시는 `남은 n일`이다: 유저가 읽는 건 "언제까지"가 아니라 "며칠 남았나"다.
DEADLINE_FIELDS = ("기한", "마감", "만료", "유효기간", "deadline", "expires", "expiry")


def is_deadline_field(name: Any) -> bool:
    return str(name or "").strip().lower() in {d.lower() for d in DEADLINE_FIELDS}


def today_index(channel_id: str) -> Optional[int]:
    """360일 달력의 누적 일수(`날`). 원천은 expr_engine.Resolver 와 **같은 산법**이다."""
    if not channel_id:
        return None
    try:
        import domain_manager as _dm
        w = _dm.get_world_state(channel_id) or {}
        dpy = int(getattr(config, "CALENDAR_DAYS_PER_YEAR", 360) or 360)
        dpm = int(getattr(config, "CALENDAR_DAYS_PER_MONTH", 30) or 30)
        return ((int(w.get("year", 1) or 1) - 1) * dpy
                + (int(w.get("month", 1) or 1) - 1) * dpm
                + (int(w.get("day", 1) or 1) - 1))
    except Exception as e:
        logger.debug("[CustomVars] 날 읽기 skip: %s", e)
        return None


def _item_text(spec: Dict[str, Any], item: str, rec: Any, channel_id: str = "") -> str:
    """list 항목 한 줄. progress = `이름 40%` / stock = `이름 3/5`(목표 없으면 `이름 3`)."""
    # [2026-09-09 P11] 필드 선언이 있으면 **필드 표기**다: `양파(싹 · 3/2/4/4)`.
    #   단계 필드는 이름으로 서고 나머지 필드는 선언 순서대로 `/` 로 잇는다.
    if spec.get("fields"):
        flds = item_fields(spec)
        stage_f = str(spec.get("stage_of", "") or "")
        stage = item_stage(spec, rec)
        vals = []
        _today = today_index(channel_id)
        for fname in flds:
            if fname == stage_f or fname == ITEM_DEFAULT_FIELD:
                continue
            try:
                _fv = int((rec or {}).get(fname, 0))
            except (TypeError, ValueError, AttributeError):
                vals.append("0")
                continue
            # 기한 필드는 저장된 **끝**이 아니라 남은 날로 선다. 시계를 모르면 수 그대로.
            if is_deadline_field(fname) and _today is not None:
                vals.append(f"남은 {max(0, _fv - _today)}일")
            else:
                vals.append(str(_fv))
        body = " · ".join(x for x in (stage, "/".join(vals)) if x)
        return f"{item}({body})" if body else str(item)
    if isinstance(rec, dict):
        n = rec.get("n", 0)
        goal = rec.get("goal") or 0
    else:
        n, goal = rec, 0
    try:
        n = int(n)
    except (TypeError, ValueError):
        n = 0
    if str(spec.get("item_mode", "stock")) == "progress":
        return f"{item} {n}%"
    return f"{item} {n}/{int(goal)}" if goal else f"{item} {n}"


# =========================================================
# [2026-09-06 P3] 급식 게이트 · 파생값 표시 — 선언 필드 둘
# =========================================================

def _clean_feed(raw: Any) -> Optional[Dict[str, str]]:
    """`feed` 칸 정규화. 모르는 값·부재는 None(= 기본 mentioned 가 적용된다)."""
    if isinstance(raw, str):
        raw = {"prose": raw}
    if not isinstance(raw, dict):
        return None
    mode = _FEED_ALIASES.get(str(raw.get("prose", "") or "").strip().lower(), "")
    return {"prose": mode} if mode in FEED_MODES else None


def feed_mode(spec: Any) -> str:
    """이 선언이 산문 급식에 어떻게 서는가. **기본은 mentioned**(스펙 §3.5 kind별 기본값).

    시스템 변수는 always — 상시 자원의 관측을 어휘 게이트가 끊지 않는다는 `always_feed`
    규율(Phase 2.5)과 같은 판단이다. 다만 시스템 변수는 build_prose_feed 에서 애초에
    빠진다(윗줄 `활력 | 평형` 이 이미 싣는다) — 여기 값은 그 배제가 풀렸을 때의 자리다.
    """
    if not isinstance(spec, dict):
        return DEFAULT_FEED_MODE
    f = _clean_feed(spec.get("feed"))
    if f:
        return f["prose"]
    if spec.get("system") or spec.get("always_feed"):
        return "always"
    return DEFAULT_FEED_MODE


def set_feed_mode(channel_id: str, name: str, mode: str) -> bool:
    """선언의 급식 칸만 고친다(값 무접촉). 라우터(P6)·스모크가 쓰는 내부 API — 명령 신설 0."""
    m = _FEED_ALIASES.get(str(mode or "").strip().lower(), "")
    if m not in FEED_MODES:
        return False
    decl = dict(get_declarations(channel_id))
    spec = decl.get(name)
    if not isinstance(spec, dict):
        return False
    spec = dict(spec)
    spec["feed"] = {"prose": m}
    decl[name] = spec
    _save(channel_id, decl, dict(get_values(channel_id)))
    return True


def is_derived(spec: Any) -> bool:
    """파생값인가 — LLM 델타·추출 급식에서 빠지는 유일한 근거."""
    return bool(isinstance(spec, dict) and spec.get("derived"))


def mark_derived(channel_id: str, name: str, flag: bool = True) -> bool:
    """expr_engine.register_derive 가 부르는 내부 표시.

    ★표시가 선언 쪽에 서는 이유: 소비자가 둘(apply_deltas · select_mentioned)이고 둘 다
      선언을 읽는다. 값 층에 두면 클리어에 표시가 사라져 파생값이 다시 LLM 소유가 된다.
    """
    decl = dict(get_declarations(channel_id))
    spec = decl.get(name)
    if not isinstance(spec, dict):
        return False
    spec = dict(spec)
    if flag:
        spec["derived"] = True
    else:
        spec.pop("derived", None)
    decl[name] = spec
    _save(channel_id, decl, dict(get_values(channel_id)))
    return True


# =========================================================
# 선언 — 검증기. 거부 사유에 규칙 요지를 동봉한다.
# =========================================================

def validate_declaration(
    spec: Any,
    existing: Optional[Dict[str, Any]] = None,
    replacing: str = "",
) -> Tuple[Optional[Dict[str, Any]], str]:
    """스키마 검증 + 정규화. Returns: (정규화 spec, "") 또는 (None, 에러문+규칙요지).

    ★거부는 전부 여기 한 곳. 파이프 파서와 자연어 변환 콜이 같은 관문을 지난다.
    """
    def _fail(msg: str) -> Tuple[None, str]:
        return None, f"{msg}\n\n{RULES_TEXT}"

    if not isinstance(spec, dict):
        return _fail("변수 선언을 읽지 못했습니다.")

    name = str(spec.get("name", "") or "").strip()
    if not name:
        return _fail("변수 이름이 없습니다.")
    if len(name) > NAME_MAX:
        return _fail(f"변수 이름이 깁니다 ({len(name)}자 > {NAME_MAX}자): `{name}`")
    if "|" in name or "\n" in name:
        return _fail(f"변수 이름에 `|` 나 줄바꿈을 쓸 수 없습니다: `{name}`")

    # [Phase 2.5] 시스템 이름은 **거부가 아니라 개정으로 흡수**된다 — 모양(타입·범위·스코프·
    #   초기값)은 코드 소유라 무시하고, rule·캡·표시형식만 받는다. 유저가 `!출력룰 추가 변수
    #   기력 …` 이라고 써도 새 변수가 생기지 않고 기존 기력이 고쳐진다(중복 표시 방지).
    _sys = system_name(name)
    if _sys:
        return _validate_system_override(_sys, spec, fmt=str(spec.get("format", "") or "").strip())

    if name.lower() in [r.lower() for r in RESERVED_NAMES]:
        return _fail(f"`{name}` 은(는) 코드가 이미 쓰는 이름입니다 (패널에 두 번 그려집니다).")

    vtype = str(spec.get("type", "gauge") or "gauge").strip().lower()
    vtype = _TYPE_ALIASES.get(vtype, vtype)
    if vtype not in VAR_TYPES:
        return _fail(f"모르는 타입입니다: `{vtype}` ({', '.join(VAR_TYPES)} 중 하나여야 합니다).")
    # 단계 목록이 왔으면 타입은 enum 이다(선언이 모양으로 말한다).
    # [2026-09-13 P17] 단, **경계 수치(`stage_at`)가 함께 왔으면 그건 게이지의 눈금**이다:
    #   경험치바(레벨)는 단계 이름으로 움직이는 값이 아니라 수치가 경계를 넘는 값이고,
    #   리셋 없이 누적돼야 `.남은`/`.퍼센트` 가 바가 된다.
    _has_at = isinstance(spec.get("stage_at"), (list, tuple)) and len(spec.get("stage_at")) >= 2
    if spec.get("stages") and vtype in ("gauge", "counter") and not _has_at:
        vtype = "enum"

    scope = str(spec.get("scope", "global") or "global").strip().lower()
    scope = _SCOPE_ALIASES.get(scope, scope)
    if scope not in VAR_SCOPES:
        return _fail(f"모르는 스코프입니다: `{scope}` ({', '.join(VAR_SCOPES)} 중 하나여야 합니다).")

    rule = str(spec.get("rule", "") or "").strip()
    if len(rule) < 2:
        return _fail(
            "규칙(언제 오르고 내리는지)이 없습니다 — 규칙이 없으면 추출 콜이 이 변수를 못 움직입니다."
        )
    if len(rule) > RULE_MAX:
        rule = rule[:RULE_MAX]

    fmt = str(spec.get("format", "") or "").strip()
    if fmt:
        if "{v}" not in fmt:
            return _fail(f"표시 형식에 값 자리 `{{v}}` 가 없습니다: `{fmt}`")
        if len(fmt) > FORMAT_MAX:
            return _fail(f"표시 형식이 깁니다 ({len(fmt)}자 > {FORMAT_MAX}자).")

    def _cap_ok(v: Any, label: str) -> Tuple[Optional[int], str]:
        if v in (None, ""):
            return None, ""
        try:
            n = int(v)
        except (TypeError, ValueError):
            return None, f"{label} 캡은 정수여야 합니다: `{v}`"
        if n <= 0 or n > DELTA_CAP_MAX:
            return None, f"{label} 캡이 범위를 벗어났습니다: `{n}` (1 이상)."
        return n, ""

    # 볼륨 캡 — 선언 기준. 기존 이름 수정은 캡을 소모하지 않는다.
    # ★시스템 선언은 세지 않는다 — 코드가 심은 것이 유저 몫을 잡아먹으면 캡이 캡이 아니다.
    existing = existing if isinstance(existing, dict) else {}
    _user_count = sum(1 for _k, _v in existing.items()
                      if not (isinstance(_v, dict) and _v.get("system")))
    is_new = name not in existing and name != (replacing or "")
    if is_new and _user_count >= MAX_VARS:
        return _fail(f"변수는 채널당 {MAX_VARS}개까지입니다 (현재 {_user_count}개). 먼저 지워 주세요.")

    # =====================================================
    # [2026-09-13 P17] text — 값이 한 줄 문자열. 범위·단계·캡이 없다.
    # =====================================================
    if vtype == "text":
        if spec.get("range") or spec.get("stages"):
            return _fail("문자열 값에는 범위나 단계가 없습니다 (`text` 는 한 줄 글자입니다).")
        t_init = spec.get("init", "")
        if isinstance(t_init, (int, float)):
            t_init = str(t_init)
        if not isinstance(t_init, str):
            t_init = ""
        _feed_t = _clean_feed(spec.get("feed"))
        return {
            "name": name,
            "type": "text",
            "init": t_init.strip()[:TEXT_MAX],
            "scope": scope,
            "rule": rule,
            **({"format": fmt} if fmt else {}),
            **({"feed": _feed_t} if _feed_t else {}),
        }, ""

    # =====================================================
    # [v1] enum — 단계 목록. 값은 수치가 아니라 단계 이름이다.
    # =====================================================
    if vtype == "enum":
        stages = spec.get("stages")
        if isinstance(stages, str):
            stages = _parse_stage_cell(stages)
        if not (isinstance(stages, (list, tuple)) and len(stages) >= 2):
            return _fail("단계 목록을 읽지 못했습니다 (`단계: 무명>안면>단골` 형식, 2개 이상).")
        stages = [str(s).strip() for s in stages if str(s).strip()]
        if len(stages) < 2:
            return _fail("단계는 2개 이상이어야 합니다.")
        if len(stages) > MAX_STAGES:
            return _fail(f"단계가 너무 많습니다 ({len(stages)} > {MAX_STAGES}). 그건 단계가 아니라 게이지입니다.")
        if len(set(stages)) != len(stages):
            return _fail(f"단계 이름이 겹칩니다: {' > '.join(stages)}")
        for s in stages:
            if len(s) > STAGE_NAME_MAX:
                return _fail(f"단계 이름이 깁니다 ({len(s)}자 > {STAGE_NAME_MAX}자): `{s}`")

        init_stage = str(spec.get("init", "") or "").strip()
        if not init_stage:
            init_stage = stages[0]
        _resolved = _match_stage(init_stage, stages)
        if not _resolved:
            return _fail(f"시작 단계 `{init_stage}` 가 단계 목록에 없습니다: {' > '.join(stages)}")

        try:
            max_step = int(spec.get("max_step", DEFAULT_MAX_STEP) or DEFAULT_MAX_STEP)
        except (TypeError, ValueError):
            max_step = DEFAULT_MAX_STEP
        max_step = max(1, min(len(stages) - 1, max_step))

        return {
            "name": name, "type": "enum", "scope": scope, "rule": rule,
            "stages": stages, "init": _resolved,
            "monotonic": bool(spec.get("monotonic")),
            "max_step": max_step,
            **({"format": fmt} if fmt else {}),
            **({"feed": _clean_feed(spec.get("feed"))} if _clean_feed(spec.get("feed")) else {}),
        }, ""

    # =====================================================
    # [v1] list — 항목별 수치 목록. 선언은 **모양**만 정하고 항목은 플레이가 만든다.
    # =====================================================
    if vtype == "list":
        if scope == "npc":
            return _fail("목록형은 아직 인물별 스코프를 지원하지 않습니다 (global 또는 PC).")
        mode = str(spec.get("item_mode", "stock") or "stock").strip().lower()
        mode = _ITEM_MODE_ALIASES.get(mode, mode)
        if mode not in ITEM_MODES:
            return _fail(f"모르는 항목 모드입니다: `{mode}` ({', '.join(ITEM_MODES)} 중 하나).")
        irng = list(PROGRESS_RANGE if mode == "progress" else STOCK_RANGE)
        _r = spec.get("range")
        if isinstance(_r, str):
            _m = _RANGE_RE.search(_r)
            _r = [int(_m.group(1)), int(_m.group(2))] if _m else None
        if isinstance(_r, (list, tuple)) and len(_r) == 2:
            try:
                if int(_r[0]) < int(_r[1]):
                    irng = [int(_r[0]), int(_r[1])]
            except (TypeError, ValueError):
                pass
        gain, ge = _cap_ok(spec.get("max_gain"), "상승")
        if ge:
            return _fail(ge)
        loss, le = _cap_ok(spec.get("max_loss"), "하강")
        if le:
            return _fail(le)
        # [2026-09-13 P15] 선언별 항목 수 상한. **유저 문법 0** — 라우터가 원문의
        #   "최대 30개" 를 읽어 이 칸을 채운다(파이프 칸도 명령도 늘지 않는다).
        #   범위 밖·수 아님은 **거부가 아니라 무시**다: 기본값(MAX_LIST_ITEMS)으로
        #   돌아가면 선언 자체는 살아 있고, 상한만 코드 기본으로 선다.
        item_cap = None
        _raw_cap = spec.get("max")
        if _raw_cap not in (None, ""):
            try:
                _n_cap = int(_raw_cap)
            except (TypeError, ValueError):
                _n_cap = 0
            if 1 <= _n_cap <= LIST_MAX_HARD:
                item_cap = _n_cap
            else:
                logger.info("[CustomVar] %s 항목 상한 선언 %r 범위 밖 → 기본 %d",
                            name, _raw_cap, MAX_LIST_ITEMS)
        # [2026-09-09 P11] 레코드 필드. **없으면 옛 list 그대로** — 이 칸이 비면 아래 dict 에
        #   `fields` 키 자체가 안 실리고, 저장·표시·급식이 전부 옛 경로를 탄다(마이그레이션 0).
        fields: Dict[str, List[int]] = {}
        finit: Dict[str, int] = {}
        raw_f = spec.get("fields") or spec.get("필드")
        if isinstance(raw_f, dict) and raw_f:
            if len(raw_f) > MAX_ITEM_FIELDS:
                return _fail(f"항목 필드가 너무 많습니다 ({len(raw_f)} > {MAX_ITEM_FIELDS}). "
                             "그건 레코드가 아니라 표입니다.")
            for fk, fv in raw_f.items():
                fn = str(fk).strip()[:NAME_MAX]
                if not fn:
                    return _fail("항목 필드 이름이 비었습니다.")
                if fn in fields:
                    return _fail(f"항목 필드 이름이 겹칩니다: {fn}")
                fr = fv
                if isinstance(fr, str):
                    _m = _RANGE_RE.search(fr)
                    fr = [int(_m.group(1)), int(_m.group(2))] if _m else None
                if not (isinstance(fr, (list, tuple)) and len(fr) == 2):
                    return _fail(f"항목 필드 `{fn}` 의 범위를 읽지 못했습니다 (예: [0, 5]).")
                try:
                    flo, fhi = int(fr[0]), int(fr[1])
                except (TypeError, ValueError):
                    return _fail(f"항목 필드 `{fn}` 의 범위가 수가 아닙니다.")
                if flo >= fhi:
                    return _fail(f"항목 필드 `{fn}` 의 범위가 뒤집혔습니다: {flo}-{fhi}")
                fields[fn] = [flo, fhi]
            raw_i = spec.get("init") if isinstance(spec.get("init"), dict) else {}
            for fn, (flo, fhi) in fields.items():
                try:
                    finit[fn] = max(flo, min(fhi, int(raw_i.get(fn, flo))))
                except (TypeError, ValueError):
                    finit[fn] = flo
        stage_of = str(spec.get("stage_of", "") or "").strip()
        lstages = spec.get("stages")
        if isinstance(lstages, str):
            lstages = _parse_stage_cell(lstages)
        lstages = [str(x).strip() for x in (lstages or []) if str(x).strip()]
        stage_at = spec.get("stage_at")
        if stage_of or lstages:
            if not fields:
                return _fail("단계는 필드 선언(`fields`)이 있는 목록에만 붙습니다.")
            if stage_of not in fields:
                return _fail(f"`stage_of` 가 필드에 없습니다: {stage_of or '(빈칸)'}")
            if len(lstages) < 2:
                return _fail("단계는 둘 이상이어야 합니다.")
            if len(lstages) > MAX_STAGES:
                return _fail(f"단계가 너무 많습니다 ({len(lstages)} > {MAX_STAGES}).")
            if isinstance(stage_at, (list, tuple)) and len(stage_at) == len(lstages):
                try:
                    stage_at = [int(x) for x in stage_at]
                except (TypeError, ValueError):
                    stage_at = None
            else:
                stage_at = None
        return {
            "name": name, "type": "list", "scope": scope, "rule": rule,
            "item_mode": mode, "item_range": irng,
            **({"fields": fields} if fields else {}),
            **({"field_init": finit} if finit else {}),
            **({"stage_of": stage_of, "stages": lstages} if (fields and stage_of and lstages) else {}),
            **({"stage_at": stage_at} if (fields and stage_of and lstages and stage_at) else {}),
            **({"max": item_cap} if item_cap else {}),
            **({"max_gain": gain} if gain else {}),
            **({"max_loss": loss} if loss else {}),
            **({"format": fmt} if fmt else {}),
            **({"feed": _clean_feed(spec.get("feed"))} if _clean_feed(spec.get("feed")) else {}),
        }, ""

    # =====================================================
    # gauge / counter (v0 본체 — 범위·시작값 + v1 비대칭 캡)
    # =====================================================
    rng = spec.get("range")
    if isinstance(rng, str):
        m = _RANGE_RE.search(rng)
        rng = [int(m.group(1)), int(m.group(2))] if m else None
    if not (isinstance(rng, (list, tuple)) and len(rng) == 2):
        return _fail("범위를 읽지 못했습니다 (`0-100` 형식).")
    try:
        lo, hi = int(rng[0]), int(rng[1])
    except (TypeError, ValueError):
        return _fail("범위는 정수여야 합니다 (`0-100` 형식).")
    if lo >= hi:
        return _fail(f"범위가 뒤집혔습니다: `{lo}-{hi}` (왼쪽이 더 작아야 합니다).")
    if hi - lo > SPAN_MAX:
        return _fail(f"범위가 너무 넓습니다: `{lo}-{hi}` (최대 폭 {SPAN_MAX}).")

    init = spec.get("init", lo)
    try:
        init = int(init)
    except (TypeError, ValueError):
        return _fail("시작값은 정수여야 합니다.")
    if not (lo <= init <= hi):
        return _fail(f"시작값 {init} 이(가) 범위 {lo}-{hi} 밖입니다.")

    # [v1] 비대칭 델타캡 — "천천히 쌓이고 빨리 식는". 범위 클램프≠델타 클램프(SimCore 교훈).
    gain, ge = _cap_ok(spec.get("max_gain"), "상승")
    if ge:
        return _fail(ge)
    loss, le = _cap_ok(spec.get("max_loss"), "하강")
    if le:
        return _fail(le)

    # [2026-09-06 P3] 급식 게이트. 화이트리스트 반환이라 여기서 통과시키지 않으면 필드가 증발한다.
    _feed = _clean_feed(spec.get("feed"))

    # [2026-09-13 P17] 눈금 있는 게이지 — `stages`(이름) + `stage_at`(경계, 오름차순, 같은 길이).
    g_stages: List[str] = []
    g_at: List[int] = []
    _rs = spec.get("stages")
    if isinstance(_rs, str):
        _rs = _parse_stage_cell(_rs)
    _rs = [str(x).strip() for x in (_rs or []) if str(x).strip()]
    _ra = spec.get("stage_at")
    if isinstance(_ra, (list, tuple)) and len(_ra) == len(_rs) and len(_rs) >= 2:
        if len(_rs) > MAX_STAGES:
            return _fail(f"단계가 너무 많습니다 ({len(_rs)} > {MAX_STAGES}).")
        try:
            _ra = [int(x) for x in _ra]
        except (TypeError, ValueError):
            _ra = []
        if _ra and _ra == sorted(_ra) and lo <= _ra[0] and _ra[-1] <= hi:
            g_stages, g_at = _rs, _ra

    return {
        "name": name,
        "type": vtype,
        "range": [lo, hi],
        "init": init,
        "scope": scope,
        "rule": rule,
        **({"stages": g_stages, "stage_at": g_at} if g_stages else {}),
        **({"max_gain": gain} if gain else {}),
        **({"max_loss": loss} if loss else {}),
        **({"format": fmt} if fmt else {}),
        **({"feed": _feed} if _feed else {}),
    }, ""


def _validate_system_override(name: str, spec: Any,
                              fmt: str = "") -> Tuple[Optional[Dict[str, Any]], str]:
    """시스템 변수 개정. **기본형 위에 SYSTEM_EDITABLE 칸만 덮는다.**

    유저가 범위·타입·스코프를 적어 보내도 조용히 무시한다 — 거부하면 파이프 문법의 필수 칸
    (`0-100 | 시작 80 | PC`)을 그대로 적은 선언이 전부 튕기고, 그건 저작 문법이 하나라는
    이 설계의 약속을 깬다. 모양은 코드가 지키고, 유저가 말한 것 중 **고칠 수 있는 것만** 받는다.
    """
    base = dict(SYSTEM_VARS[name])
    spec = spec if isinstance(spec, dict) else {}
    out = dict(base)

    rule = str(spec.get("rule", "") or "").strip()
    if rule and len(rule) >= 2:
        out["rule"] = rule[:RULE_MAX]

    if fmt:
        if "{v}" not in fmt:
            return None, f"표시 형식에 값 자리 `{{v}}` 가 없습니다: `{fmt}`\n\n{RULES_TEXT}"
        if len(fmt) > FORMAT_MAX:
            return None, f"표시 형식이 깁니다 ({len(fmt)}자 > {FORMAT_MAX}자).\n\n{RULES_TEXT}"
        out["format"] = fmt

    for key, label in (("max_gain", "상승"), ("max_loss", "하강")):
        v = spec.get(key)
        if v in (None, ""):
            continue
        try:
            n = int(v)
        except (TypeError, ValueError):
            return None, f"{label} 캡은 정수여야 합니다: `{v}`\n\n{RULES_TEXT}"
        if n <= 0 or n > DELTA_CAP_MAX:
            return None, f"{label} 캡이 범위를 벗어났습니다: `{n}` (1 이상).\n\n{RULES_TEXT}"
        out[key] = n

    return out, ""


# =========================================================
# 등록 / 해제
# =========================================================

def register(channel_id: str, spec: Dict[str, Any]) -> Tuple[bool, str]:
    """검증된 spec 을 저장. 기존 이름이면 선언만 갱신하고 **현재값은 보존**한다
    (범위가 좁아졌으면 그 범위로 클램프 — 선언 개정이 값을 날리지 않는다)."""
    if not is_enabled():
        return False, "변수 기능이 꺼져 있습니다 (CUSTOM_VARS_ENABLED=0)."
    name = spec["name"]
    decl = dict(get_declarations(channel_id))
    vals = dict(get_values(channel_id))
    existed = name in decl

    entry = dict(spec)
    entry["created_at"] = (decl.get(name, {}) or {}).get("created_at") or time.strftime("%Y-%m-%d")
    decl[name] = entry

    prev = vals.get(name) if isinstance(vals.get(name), dict) else None
    vals[name] = _initial_value(entry, prev)

    _save(channel_id, decl, vals)
    logger.info("[CustomVar] %s %s (%s %s scope=%s) → %r",
                "수정" if existed else "등록", name, entry["type"],
                entry.get("range") or entry.get("stages") or entry.get("item_mode", ""),
                entry["scope"], vals[name]["value"])
    return True, ("수정" if existed else "등록")


def _initial_value(entry: Dict[str, Any], prev: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """선언(개정 포함) 직후의 값 엔트리. **개정이 값을 날리지 않는다**가 계약이다.

    타입별로 보존 모양이 다르다:
      gauge/counter — 새 범위로 클램프 / enum — 여전히 있는 단계면 유지, 없으면 init
      list          — 항목 유지 + 새 항목 범위로 클램프 / npc 스코프 — 인물별 dict 그대로
    """
    vtype = str(entry.get("type", "gauge"))
    scope = str(entry.get("scope", "global"))
    old = prev.get("value") if isinstance(prev, dict) else None
    stamp = prev.get("last_change") if isinstance(prev, dict) else None

    if vtype == "list":
        items = old if isinstance(old, dict) else {}
        lo, hi = (entry.get("item_range") or STOCK_RANGE)[:2]
        clean = {}
        for k, rec in list(items.items())[:list_max(entry)]:
            # [2026-09-09 P11] 정돈은 `_clean_record` 하나로 모았다 — 필드 선언이 없으면
            #   `{n, goal}` 한 쌍이 그대로 나오고(옛 개정 동작과 같다), 있으면 필드별 클램프다.
            fixed = _clean_record(entry, rec)
            if fixed is None:
                continue
            clean[str(k)] = fixed
        return {"value": clean, "last_change": stamp}

    # per_actor(시스템 변수)도 NPC 스코프와 **같은 저장 모양**이라 같은 가지를 탄다.
    if scope == "npc" or entry.get("per_actor"):
        per = old if isinstance(old, dict) else {}
        clean = {}
        for nm, v in list(per.items())[:MAX_NPC_VALUES]:
            fixed = _clamp_scalar(entry, v)
            if fixed is not None:
                clean[str(nm)] = fixed
        return {"value": clean, "last_change": stamp if isinstance(stamp, dict) else {}}

    fixed = _clamp_scalar(entry, old)
    if fixed is None:
        return {"value": entry.get("init"), "last_change": None}
    return {"value": fixed, "last_change": stamp}


def _clamp_scalar(entry: Dict[str, Any], raw: Any) -> Any:
    """스칼라 하나를 선언에 맞춰 접는다. 담을 수 없으면 None(= init 으로 되돌린다)."""
    vtype = str(entry.get("type", "gauge"))
    if vtype == "text":
        return str(raw).strip()[:TEXT_MAX] if isinstance(raw, str) else None
    if vtype == "enum":
        return _match_stage(raw, list(entry.get("stages") or [])) or None
    if isinstance(raw, (int, float)):
        try:
            lo, hi = int((entry.get("range") or [0, 0])[0]), int((entry.get("range") or [0, 0])[1])
        except (TypeError, ValueError, IndexError):
            return None
        return max(lo, min(hi, int(raw)))
    return None


def unregister(channel_id: str, name: str) -> bool:
    """선언+값 동시 삭제. 없으면 False. **시스템 변수는 삭제 불가**(코드가 심은 기관)."""
    if not is_enabled():
        return False
    name = str(name or "").strip()
    if system_name(name):
        logger.info("[CustomVar] 시스템 변수 삭제 거부: %s", name)
        return False
    decl = dict(get_declarations(channel_id))
    vals = dict(get_values(channel_id))
    target = name if name in decl else _resolve_name(name, decl)
    if not target:
        return False
    decl.pop(target, None)
    vals.pop(target, None)
    _save(channel_id, decl, vals)
    logger.info("[CustomVar] 삭제 %s", target)
    return True


def _resolve_name(name: str, decl: Dict[str, Any]) -> str:
    """대소문자·공백 관용 해석. 없으면 ""."""
    key = str(name or "").strip()
    if not key:
        return ""
    if key in decl:
        return key
    low = key.lower()
    for k in decl:
        if str(k).strip().lower() == low:
            return k
    return ""


def format_list(channel_id: str) -> str:
    """`!출력룰 목록 변수` 표시문."""
    decl = get_declarations(channel_id)
    if not decl:
        return "📊 선언된 변수가 없습니다.\n\n" + RULES_TEXT
    vals = get_values(channel_id)
    _user_n = sum(1 for _v in decl.values() if isinstance(_v, dict) and not _v.get("system"))
    lines = [f"📊 **선언 변수** ({_user_n}/{MAX_VARS})"]
    for name, spec in decl.items():
        if not isinstance(spec, dict):
            continue
        cur = (vals.get(name) or {}).get("value")
        scope = spec.get("scope", "global")
        # 시스템 변수 — 🔒 로 "지울 수 없음"을 표시하고, PC별 현재값을 가면으로 읽는다.
        if spec.get("system"):
            lo, hi = (spec.get("range") or [0, 0])[:2]
            _caps = f"캡 +{spec.get('max_gain', '∞')}/-{spec.get('max_loss', '∞')}"
            if spec.get("per_actor"):
                _per = cur if isinstance(cur, dict) else {}
                _uids = list(_per.keys()) or ([_default_actor(channel_id)] if _default_actor(channel_id) else [])
                _txt = ", ".join(
                    f"{_actor_label(channel_id, u)} {format_value(spec, get_system_value(channel_id, name, u))}"
                    for u in _uids[:MAX_NPC_VALUES]) or "—"
            else:
                _txt = format_value(spec, cur if cur is not None else spec.get("init"))
            lines.append(f"- 🔒 **{name}** `{_txt}` ({lo}-{hi}, {_caps}, 시스템) — {spec.get('rule', '')}")
            continue
        vtype = spec.get("type", "gauge")
        if vtype == "enum":
            shape = " > ".join(spec.get("stages") or [])
            if spec.get("monotonic"):
                shape += " (단조)"
        elif vtype == "list":
            lo, hi = (spec.get("item_range") or [0, 0])[:2]
            shape = f"{spec.get('item_mode', 'stock')} {lo}-{hi}, 항목 {len(cur or {})}/{list_max(spec)}"
        else:
            lo, hi = (spec.get("range") or [0, 0])[:2]
            shape = f"{lo}-{hi}"
            if spec.get("max_gain") or spec.get("max_loss"):
                shape += f", 캡 +{spec.get('max_gain', '∞')}/-{spec.get('max_loss', '∞')}"
        if isinstance(cur, dict):
            cur_text = ", ".join(
                f"{k} {_item_text(spec, k, v) if vtype == 'list' else format_value(spec, v)}"
                if vtype != "list" else _item_text(spec, k, v)
                for k, v in list(cur.items())[:4]
            ) or "—"
        else:
            cur_text = format_value(spec, cur)
        lines.append(f"- **{name}** `{cur_text}` ({shape}, {vtype}, {scope}) — {spec.get('rule', '')}")
    return "\n".join(lines)


# =========================================================
# mentions 게이트 (simcore 1순위 차용)
# =========================================================

def mentioned_names(names: Any, *texts: str) -> set:
    """이름 목록 중 **이번 턴 텍스트에 등장한 것**만. 판정면 하나를 여럿이 나눠 쓴다.

    [2026-09-06 P4/P5] `select_mentioned` 는 선언 dict 전용이라 전이 큐(P4)·연산 항목(P5)이
    같은 규칙을 못 빌려 썼다. 규칙 자체(부분 일치 + 소문자 대조)를 여기 한 곳에 두고
    `select_mentioned` 도 이걸 부른다 — 게이트가 둘이 되면 "왜 여긴 잡히고 저긴 안 잡히나"가
    영영 안 풀린다. 한국어는 조사가 이름에 붙으므로(마나가/마나를) 부분 일치가 정답이다.
    """
    blob = " ".join(str(t or "") for t in texts)
    low = blob.lower()
    hit = set()
    for n in (names or []):
        nm = str(n or "").strip()
        if not nm:
            continue
        if nm in blob or nm.lower() in low:
            hit.add(nm)
    return hit


def select_mentioned(channel_id: str, *texts: str) -> List[Dict[str, Any]]:
    """이번 턴 산문·입력에 **이름이 등장한** 변수만 골라 추출 콜 급식분으로 돌려준다.

    ★"캡은 크기를 막지 **빈도**를 못 막는다" — 변수 10개 시대에 선언 전량을 매 턴
      프롬프트에 싣지 않기 위한 관문. 한국어는 조사가 이름에 붙으므로(마나가/마나를)
      부분 일치가 정답이다.
    Returns: [{"name","type","range","rule","scope"}] — 등장 0이면 [].
    """
    if not is_enabled():
        return []
    decl = get_declarations(channel_id)
    if not decl:
        return []
    blob = " ".join(str(t or "") for t in texts)
    low = blob.lower()
    vals = get_values(channel_id)
    out: List[Dict[str, Any]] = []
    for name, spec in decl.items():
        if not isinstance(spec, dict):
            continue
        nm = str(name).strip()
        if not nm:
            continue
        # [2026-09-06 P3] 파생값은 추출 급식에서 뺀다 — 모델이 못 움직이는 값을 스키마에
        #   실으면 그 자리는 폐기될 델타를 부르는 초대장이다(추출 스키마 비대 감시, 스펙 §3.5).
        if is_derived(spec):
            continue
        # [Phase 2.5] **mentions 면제**(always_feed). 능력을 쓴 장면에 "기력"이라는 낱말이
        #   없어도 소모는 일어난다 — 어휘 게이트는 유저 변수의 프롬프트 비대를 막는 장치지
        #   상시 자원의 관측을 끊는 장치가 아니다. 동결된(토글 off) 시스템 변수는 급식 안 한다.
        if spec.get("always_feed"):
            if spec.get("system") and not _system_active(channel_id, spec):
                continue
        elif not mentioned_names((nm,), blob):
            continue
        vtype = str(spec.get("type", "gauge"))
        cur = (vals.get(nm) or {}).get("value")
        entry: Dict[str, Any] = {
            "name": nm,
            "type": vtype,
            "range": list(spec.get("range") or [0, 0])[:2],
            "rule": str(spec.get("rule", "") or ""),
            "scope": spec.get("scope", "global"),
        }
        # [v1] 단계형은 **지금 어느 단계인지**를 알아야 목표 단계를 고를 수 있다.
        #   (수치 총량과 달리 단계는 열거된 상태 이름이라 급식해도 절대값 위임이 아니다 —
        #    C축 DRIVE 가 단계 이름만 주고받는 것과 같은 문법.)
        if vtype == "enum":
            entry["stages"] = list(spec.get("stages") or [])
            entry["monotonic"] = bool(spec.get("monotonic"))
            entry["current"] = cur
        # [2026-09-13 P17] text — 지금 문자열을 알아야 "바꿀 이유가 있나"를 잴 수 있다.
        elif vtype == "text":
            entry.pop("range", None)
            entry["max_chars"] = TEXT_MAX
            entry["current"] = str(cur or spec.get("init", "") or "")
        # [v1] 목록형은 **이미 있는 항목**을 알아야 신설과 이동을 구분한다.
        elif vtype == "list":
            entry["item_mode"] = str(spec.get("item_mode", "stock"))
            entry["item_range"] = list(spec.get("item_range") or STOCK_RANGE)[:2]
            entry["items"] = [
                _item_text(spec, k, v, channel_id) for k, v in list((cur or {}).items())[:list_max(spec)]
            ]
        # [v1] NPC 스코프는 **값을 가질 자격이 있는 인물**을 알아야 헛신고가 줄어든다.
        if entry["scope"] == "npc":
            entry["npcs"] = allowed_npc_names(channel_id)[:MAX_NPC_VALUES]
            entry["current_by_npc"] = {
                k: (format_value(spec, v) if vtype != "enum" else v)
                for k, v in list((cur or {}).items())[:MAX_NPC_VALUES]
            } if isinstance(cur, dict) else {}
        # [2026-09-06 P8c] npc_enabled 시스템 변수 — **항목은 여전히 하나**다. PC 줄(이름·범위·
        #   rule)은 그대로 두고 인물 칸만 붙는다: 프롬프트 블록이 `characters:` 줄을 이 두 키로
        #   그리므로 문안·스키마 신설 0(모델이 보는 신고 모양도 `npc` 키 하나로 같다).
        elif npc_keyed(spec):
            chars = feed_npc_names(channel_id)
            if chars:
                per = cur if isinstance(cur, dict) else {}
                entry["npcs"] = chars
                entry["current_by_npc"] = {
                    n: format_value(spec, per.get(n, spec.get("init")))
                    for n in chars
                }
        out.append(entry)
    return out


# =========================================================
# 델타 적용 (집행)
# =========================================================

def apply_deltas(channel_id: str, deltas: Any, turn: Optional[int] = None,
                 actor: str = "", op_names: Optional[Any] = None) -> List[Dict[str, Any]]:
    """LLM 신고 `[{"name","delta","evidence"}]` → 이전 값+델타 → 범위 클램프 → 저장.

    [v1] 타입이 넷이라 **한 배열 안에서 세 모양**이 온다 — 분기는 여기 한 곳뿐이다:
      gauge/counter → delta (+비대칭 캡)  ·  enum → stage(목표 단계명)  ·  list → op/item/delta
    셋 다 공통 관문(선언 존재·evidence·no-op 보존)을 먼저 지나고, 그 뒤 타입별 집행기로 간다.

    계약:
      - **evidence 없으면 폐기**(사망 파이프라인 문법). 근거 없는 수치 이동은 없다.
      - 선언되지 않은 이름은 폐기(모델이 변수를 발명하지 못한다).
      - delta 0 또는 이미 바닥/천장 = **no-op**. 도장을 안 찍는다
        (매 턴 갱신되는 도장은 판독값이 0 — 관계 도장과 같은 규율).
      - 절대값 방어: 델타가 범위 폭을 넘으면 클램프가 자연히 삼킨다.

    Returns: 실제로 움직인 항목 [{"name","from","to","delta","evidence"}].
    """
    if not is_enabled() or not deltas:
        return []
    if isinstance(deltas, dict):        # 모델이 {name: delta} 로 흘리는 경우 관용 접기
        deltas = [{"name": k, "delta": v} for k, v in deltas.items()]
    if not isinstance(deltas, list):
        return []

    decl = get_declarations(channel_id)
    if not decl:
        return []
    vals = dict(get_values(channel_id))
    t = int(turn) if turn is not None else _current_turn(channel_id)

    # [2026-09-06 P5] **이중 차감 방지**. 연산(operation) 전이가 쓰는 이름은 코드가 이미
    #   그 턴에 밀었다 — 같은 사건을 모델이 한 번 더 밀면 재료가 두 번 빠진다. 추출은
    #   배경이라 여기가 그 델타의 마지막 관문이다(집행 턴 뒤의 배경 추출분).
    _op_targets: set = set()
    try:
        import expr_engine as _ee
        # [2026-09-24 감사 §5-2 #18a] op_names = 같은 추출의 연산 시도 신고 — 그 연산들의 대상만 막는다.
        _op_targets = _ee.operation_write_targets(channel_id, names=op_names)
    except Exception as _e_op:
        logger.debug("[CustomVar] 연산 쓰기 대상 조회 skip: %s", _e_op)

    applied: List[Dict[str, Any]] = []
    for item in deltas:
        if not isinstance(item, dict):
            continue
        name = _resolve_name(item.get("name", ""), decl)
        if not name:
            logger.debug("[CustomVar] 미선언 이름 폐기: %r", item.get("name"))
            continue
        evidence = str(item.get("evidence", "") or "").strip()
        if len(evidence) < 2:
            logger.info("[CustomVar] %s 근거 없음 → 폐기 (%r)", name, item)
            continue
        evidence = evidence[:EVIDENCE_MAX]

        spec = decl.get(name) or {}
        if spec.get("system") and not _system_active(channel_id, spec):
            logger.debug("[CustomVar] %s 토글 off → 델타 동결", name)
            continue
        # [2026-09-06 P3] **파생값은 모델 소유가 아니다** — 식이 매턴 다시 계산하므로
        #   델타를 받아 봐야 ⑥ 에서 덮인다. 조용히 덮이면 "모델이 밀었는데 왜 안 움직이나"가
        #   되므로 폐기를 로그 1줄로 남긴다(스펙 ④-1 "파생값은 델타 스키마에서 제외").
        if is_derived(spec):
            logger.info("[CustomVar] %s 는 파생값 → LLM 델타 폐기 (%r)", name, item)
            continue
        if name in _op_targets:
            logger.info("[CustomVar] %s 는 연산 쓰기 대상 → LLM 델타 폐기(이중 차감 방지) (%r)",
                        name, item)
            continue
        vtype = str(spec.get("type", "gauge"))
        try:
            if vtype == "text":
                rec = _apply_text(channel_id, name, spec, vals, item, evidence, t)
            elif vtype == "enum":
                rec = _apply_enum(channel_id, name, spec, vals, item, evidence, t)
            elif vtype == "list":
                rec = _apply_list(name, spec, vals, item, evidence, t)
            else:
                rec = _apply_numeric(channel_id, name, spec, vals, item, evidence, t, actor=actor)
        except Exception as e:            # 한 항목의 기형이 나머지를 죽이지 않는다
            logger.debug("[CustomVar] %s 적용 실패: %s (%r)", name, e, item)
            rec = None
        if rec:
            applied.append(rec)

    if applied:
        _save(channel_id, get_declarations(channel_id), vals)
    return applied


def apply_system_delta(channel_id: str, name: Any, delta: int, evidence: str,
                       actor: str = "", turn: Optional[int] = None,
                       exempt_cap: bool = True,
                       source: str = "code") -> Optional[Dict[str, Any]]:
    """**코드 소유 쓰기** — 판정 Effort 선불처럼 기계가 스스로 값을 미는 자리.

    LLM 신고 경로(apply_deltas)와 다른 점 둘:
      - evidence 는 코드가 붙인다(`"effort"`). 근거 필수 규율은 그대로지만 근거의 출처가 다르다.
      - **델타캡 면제**가 기본이다 — 비대칭 캡은 모델의 과장에 거는 재갈이지, 규칙이 정한
        선불(EFFORT_COST)을 깎을 근거가 아니다. 범위 클램프는 여전히 문다.
    """
    nm = system_name(name)
    if not nm or not is_enabled():
        return None
    decl = get_declarations(channel_id)
    spec = decl.get(nm)
    if not isinstance(spec, dict) or not _system_active(channel_id, spec):
        return None
    try:
        d = int(delta)
    except (TypeError, ValueError):
        return None
    if not d:
        return None
    ev = str(evidence or "").strip()[:EVIDENCE_MAX]
    if len(ev) < 2:
        return None
    vals = dict(get_values(channel_id))
    t = int(turn) if turn is not None else _current_turn(channel_id)
    rec = _apply_numeric(channel_id, nm, spec, vals, {"delta": d}, ev, t,
                         actor=actor, exempt_cap=exempt_cap, source=source)
    if rec:
        _save(channel_id, decl, vals)
    return rec


def apply_code_item(channel_id: str, name: Any, item: Any, op: Any, *,
                    to: str = "", evidence: str = "", turn: Optional[int] = None,
                    source: str = "expr") -> Optional[Dict[str, Any]]:
    """[2026-09-09 P11] 코드가 목록 **항목 자체**를 생멸시킨다 — `add` / `remove`.

    수치 쓰기(`apply_code_write`)와 갈라 둔 이유: 생멸은 값의 변화가 아니라 **레코드의
    존재**다. 항목을 지우면 그 항목의 다른 필드도 같이 죽고(그게 "시든 화분"이다), 다시
    심으면 `field_init` 로 처음부터 센다(그게 "나이 0" 이다). 되돌리는 연산은 없다.
    """
    nm = str(name or "").strip()
    key = str(item or "").strip()[:ITEM_NAME_MAX]
    _raw_op = str(op or "").strip().lower()
    # [2026-09-10 P13] `rename` 은 _LIST_OPS 에 얹지 않는다 — 그 표는 LLM 델타 경로(`_apply_list`)도
    #   읽고, 모델이 항목 이름을 갈아 끼우는 문은 이번에 여는 문이 아니다(유저 OOC 전용).
    o = "rename" if _raw_op in _RENAME_OPS else _LIST_OPS.get(_raw_op, "")
    if not nm or not key or o not in ("add", "remove", "rename"):
        return None
    decl = get_declarations(channel_id)
    spec = decl.get(nm)
    if not isinstance(spec, dict) or str(spec.get("type")) != "list":
        return None
    if spec.get("system") and not _system_active(channel_id, spec):
        return None
    ev = (str(evidence or "").strip() or f"{source} write")[:EVIDENCE_MAX]
    t = int(turn) if turn is not None else _current_turn(channel_id)
    vals = dict(get_values(channel_id))
    entry = vals.get(nm) if isinstance(vals.get(nm), dict) else {}
    items = dict(entry.get("value") or {}) if isinstance(entry.get("value"), dict) else {}
    real = key if key in items else next(
        (k for k in items if str(k).strip().lower() == key.lower()), "")

    # [2026-09-10 P13] rename — **키만 바꾸고 필드는 그대로 든다.** "당근을 키웠는데 사실
    #   양파였다"는 이름 정정이지 새 레코드가 아니다(나이 0 으로 되돌아가면 안 된다).
    #   "새로 심었다"는 remove+add 로 낸다 — 그쪽이 필드 init 이다.
    if o == "rename":
        dst = str(to or "").strip()[:ITEM_NAME_MAX]
        if not real or not dst:
            return None
        if dst != real and next((k for k in items if str(k).strip().lower() == dst.lower()), ""):
            return None                     # 이미 그 이름이 있다 — 합치기는 rename 이 아니다
        if dst == real:
            return None
        rebuilt: Dict[str, Any] = {}
        for k, v in items.items():          # 순서 보존(표시·급식이 저장 순서를 읽는다)
            rebuilt[dst if k == real else k] = v
        items = rebuilt
        vals[nm] = {"value": items,
                    "last_change": _stamp(t, ev, source, item=dst, op="rename", prev=real)}
        _save(channel_id, decl, vals)
        logger.info("[CustomVar] %s 항목 이름 정정 %s→%s src=%s ev=%s",
                    nm, real, dst, source, ev[:60])
        return {"name": nm, "item": dst, "from_item": real, "op": "rename", "evidence": ev}

    if o == "remove":
        if not real:
            return None
        items.pop(real, None)
        vals[nm] = {"value": items, "last_change": _stamp(t, ev, source, item=real, op="remove")}
        _save(channel_id, decl, vals)
        logger.info("[CustomVar] %s 항목 제거 %s src=%s ev=%s", nm, real, source, ev[:60])
        return {"name": nm, "item": real, "op": "remove", "evidence": ev}

    if real:
        return None                         # 이미 있는 항목 — 신설이 아니다(값은 건드리지 않는다)
    if len(items) >= list_max(spec):
        logger.info("[CustomVar] %s 항목 상한 %d 도달 → %s 신설 거부", nm, list_max(spec), key)
        return None
    # [2026-09-13 P17] stock 목록의 `add` 는 **수량 1**로 선다. 0 으로 세우면 `count`/`has`
    #   (수량>0 만 센다)가 방금 만든 항목을 못 보고, "의뢰가 3건이면"이 영영 안 돈다.
    #   progress 목록은 0 유지 — 거긴 수량이 아니라 진행률이고 새 과제는 0% 다.
    _seed: Dict[str, Any] = {}
    if not spec.get("fields") and str(spec.get("item_mode", "stock")) != "progress":
        _seed = {ITEM_DEFAULT_FIELD: 1}
    items[key] = _clean_record(spec, _seed) or {"n": 1, "goal": 0}
    vals[nm] = {"value": items, "last_change": _stamp(t, ev, source, item=key, op="add")}
    _save(channel_id, decl, vals)
    logger.info("[CustomVar] %s 항목 신설 %s = %r src=%s ev=%s",
                nm, key, items[key], source, ev[:60])
    return {"name": nm, "item": key, "op": "add", "evidence": ev}


def apply_code_write(channel_id: str, name: Any, *, delta: Any = None, value: Any = None, field: Any = None,
                     npc: str = "", item: str = "", actor: str = "",
                     evidence: str = "", turn: Optional[int] = None,
                     source: str = "expr") -> Optional[Dict[str, Any]]:
    """**코드 소유 쓰기 — 전 타입·전 스코프.** expr 의 `do`/`derive` 가 값에 닿는 유일한 문.

    왜 `apply_system_delta` 를 못 쓰는가(§0 재확인 2의 답): 그 함수는 `system_name(name)` 으로
    시작한다 — **SYSTEM_VARS 전용**이고 `_apply_numeric` 한 갈래만 탄다. 유저 선언·npc 스코프·
    list 항목·enum 단계는 애초에 그 문을 지나지 못한다.

    LLM 경로(`apply_deltas`)와 다른 점 셋:
      - **비대칭 델타캡 0.** 캡은 모델의 과장에 거는 재갈이다. 유저가 승인한 식이 정한
        차감을 코드가 스스로 깎을 근거가 없다(§0.7 a 의 "소지품 stock 캡 0"과 같은 판단).
      - **enum max_step 클램프 0.** 한 번에 1단계는 모델이 단계를 건너뛰지 못하게 하는 관문이지
        `품질 = "상"` 이라고 쓴 식을 되돌릴 근거가 아니다.
      - **범위 클램프는 여전히 문다.** 범위는 선언이 정한 세계의 모양이라 누가 쓰든 같다.

    delta / value 중 하나만 준다. 실제로 움직였으면 rec, 아니면 None(no-op 은 도장을 안 찍는다).
    """
    if not is_enabled():
        return None
    nm = str(name or "").strip()
    decl = get_declarations(channel_id)
    spec = decl.get(nm)
    if not isinstance(spec, dict):
        return None
    if spec.get("system") and not _system_active(channel_id, spec):
        logger.debug("[CustomVar] %s 토글 off → 코드 쓰기 동결", nm)
        return None

    ev = (str(evidence or "").strip() or f"{source} write")[:EVIDENCE_MAX]
    t = int(turn) if turn is not None else _current_turn(channel_id)
    vals = dict(get_values(channel_id))
    entry = vals.get(nm) if isinstance(vals.get(nm), dict) else {}
    vtype = str(spec.get("type", "gauge"))

    def _num(x: Any) -> Optional[int]:
        try:
            return int(round(float(x)))
        except (TypeError, ValueError):
            return None

    # --- list: 항목별 수치. 항목이 없으면 **신설**한다(식이 그 이름을 이미 선언했다) ---
    if vtype == "list":
        key = str(item or "").strip()[:ITEM_NAME_MAX]
        if not key:
            return None
        try:
            lo = int((spec.get("item_range") or STOCK_RANGE)[0])
            hi = int((spec.get("item_range") or STOCK_RANGE)[1])
        except (TypeError, ValueError, IndexError):
            lo, hi = STOCK_RANGE
        items = dict(entry.get("value") or {}) if isinstance(entry.get("value"), dict) else {}
        real = key if key in items else next(
            (k for k in items if str(k).strip().lower() == key.lower()), "")
        rec = items.get(real) if real else None
        # [2026-09-09 P11] 필드 쓰기 — `화분["양파"]["수분"] += 1`. 범위는 그 **필드의** 범위고
        #   나머지 필드는 손대지 않는다. 없는 필드는 조용히 폐기가 아니라 None(호출자가 본다).
        fld = str(field or "").strip()
        if fld:
            flds = item_fields(spec)
            if fld not in flds or not real:
                return None
            try:
                flo, fhi = int(flds[fld][0]), int(flds[fld][1])
            except (TypeError, ValueError, IndexError):
                flo, fhi = STOCK_RANGE
            cur_rec = dict(rec) if isinstance(rec, dict) else {}
            try:
                old_f = int(cur_rec.get(fld, (spec.get("field_init") or {}).get(fld, flo)))
            except (TypeError, ValueError):
                old_f = flo
            new_f = _num(value) if value is not None else old_f + (_num(delta) or 0)
            if new_f is None:
                return None
            new_f = max(flo, min(fhi, new_f))
            if new_f == old_f:
                return None
            cur_rec[fld] = new_f
            items[real] = cur_rec
            vals[nm] = {"value": items,
                        "last_change": _stamp(t, ev, source, item=real, op="delta",
                                              delta=new_f - old_f)}
            _save(channel_id, decl, vals)
            logger.info("[CustomVar] %s 항목 %s.%s %s→%s src=%s ev=%s",
                        nm, real, fld, old_f, new_f, source, ev[:60])
            return {"name": nm, "item": real, "field": fld, "from": old_f, "to": new_f,
                    "delta": new_f - old_f, "evidence": ev}
        old_n = int(rec.get("n", 0)) if isinstance(rec, dict) else (_num(rec) or 0)
        goal = int(rec.get("goal", 0) or 0) if isinstance(rec, dict) else 0
        if not real and len(items) >= list_max(spec):
            logger.info("[CustomVar] %s 항목 상한 %d 도달 → %s 신설 거부", nm, list_max(spec), key)
            return None
        new_n = _num(value) if value is not None else old_n + (_num(delta) or 0)
        if new_n is None:
            return None
        new_n = max(lo, min(hi, new_n))
        if real and new_n == old_n:
            return None
        # [2026-09-09 P11] 레코드의 다른 필드를 지우지 않는다 — 옛 두 칸만 갈아 끼운다.
        _base = dict(rec) if isinstance(rec, dict) else {}
        _base.update({"n": new_n, "goal": goal})
        items[real or key] = _base
        vals[nm] = {"value": items,
                    "last_change": _stamp(t, ev, source, item=real or key, op="delta",
                                          delta=new_n - old_n)}
        _save(channel_id, decl, vals)
        logger.info("[CustomVar] %s 항목 %s %s→%s src=%s ev=%s",
                    nm, real or key, old_n, new_n, source, ev[:60])
        return {"name": nm, "item": real or key, "from": old_n, "to": new_n,
                "delta": new_n - old_n, "evidence": ev}

    # --- [2026-09-13 P17] text: 한 줄 문자열. 델타가 없다 — 쓰기는 늘 set 이다 ---
    if vtype == "text":
        if value is None:
            return None
        new_t = str(value).strip()[:TEXT_MAX]
        old_t = entry.get("value")
        old_t = str(old_t) if isinstance(old_t, str) else str(spec.get("init", "") or "")
        if new_t == old_t:
            return None
        vals[nm] = {"value": new_t, "last_change": _stamp(t, ev, source, prev=old_t)}
        _save(channel_id, decl, vals)
        logger.info("[CustomVar] %s → %r src=%s ev=%s", nm, new_t[:40], source, ev[:60])
        return {"name": nm, "from": old_t, "to": new_t, "evidence": ev}

    # --- enum: 단계 이름. max_step 클램프 없음(코드 소유 쓰기) ---
    if vtype == "enum":
        stages = [str(x) for x in (spec.get("stages") or [])]
        target = _match_stage(value, stages)
        if not target:
            logger.info("[CustomVar] %s 알 수 없는 단계 %r → 폐기 (목록: %s)",
                        nm, value, " > ".join(stages))
            return None
        if str(spec.get("scope")) == "npc":
            who = resolve_npc(channel_id, npc)
            if not who:
                return None
            per = dict(entry.get("value") or {}) if isinstance(entry.get("value"), dict) else {}
            cur = _match_stage(per.get(who), stages) or _match_stage(spec.get("init"), stages) or stages[0]
            if cur == target:
                return None
            per[who] = target
            stamps = dict(entry.get("last_change") or {}) if isinstance(entry.get("last_change"), dict) else {}
            stamps[who] = _stamp(t, ev, source, stage=target, prev=cur)
            vals[nm] = {"value": per, "last_change": stamps}
        else:
            cur = _match_stage(entry.get("value"), stages) or _match_stage(spec.get("init"), stages) or stages[0]
            if cur == target:
                return None
            vals[nm] = {"value": target,
                        "last_change": _stamp(t, ev, source, stage=target, prev=cur)}
        _save(channel_id, decl, vals)
        logger.info("[CustomVar] %s → %s src=%s ev=%s", nm, target, source, ev[:60])
        return {"name": nm, "to": target, "evidence": ev}

    # --- gauge / counter (global · pc · npc · per_actor) ---
    try:
        lo = int((spec.get("range") or [0, 0])[0])
        hi = int((spec.get("range") or [0, 0])[1])
    except (TypeError, ValueError, IndexError):
        return None

    keyed = ""
    per: Dict[str, Any] = {}
    # [2026-09-06 P8c] per_actor 시스템 변수에 인물 첨자가 오면(`기력[리나] -= 10`) 그 인물 키를
    #   쓴다 — expr 쪽 문법이 npc 스코프 변수와 같아진다. 첨자가 없으면 종전대로 acting user.
    if spec.get("per_actor") and spec.get("npc_enabled") and str(npc or "").strip():
        keyed = resolve_npc(channel_id, npc)
        if not keyed:
            return None
        per = dict(entry.get("value") or {}) if isinstance(entry.get("value"), dict) else {}
        if keyed not in per and len(_npc_keys(channel_id, per)) >= MAX_NPC_VALUES:
            logger.info("[CustomVar] %s 인물별 값 상한 %d 도달 → %s 폐기", nm, MAX_NPC_VALUES, keyed)
            return None
        try:
            old = int(per.get(keyed, spec.get("init", lo)))
        except (TypeError, ValueError):
            old = int(spec.get("init", lo) or lo)
    elif spec.get("per_actor"):
        keyed = str(actor or _default_actor(channel_id) or "").strip()
        if not keyed:
            return None
        per = dict(entry.get("value") or {}) if isinstance(entry.get("value"), dict) else {}
        old = _actor_base(channel_id, spec, entry, keyed)
    elif str(spec.get("scope")) == "npc":
        keyed = resolve_npc(channel_id, npc)
        if not keyed:
            return None
        per = dict(entry.get("value") or {}) if isinstance(entry.get("value"), dict) else {}
        try:
            old = int(per.get(keyed, spec.get("init", lo)))
        except (TypeError, ValueError):
            old = int(spec.get("init", lo) or lo)
        if keyed not in per and len(per) >= MAX_NPC_VALUES:
            logger.info("[CustomVar] %s 인물별 값 상한 %d 도달 → %s 폐기", nm, MAX_NPC_VALUES, keyed)
            return None
    else:
        try:
            old = int(entry.get("value", spec.get("init", lo)))
        except (TypeError, ValueError):
            old = int(spec.get("init", lo) or lo)

    new = _num(value) if value is not None else old + (_num(delta) or 0)
    if new is None:
        return None
    new = max(lo, min(hi, new))          # 범위 클램프는 누가 쓰든 문다
    if new == old:
        return None

    if keyed:
        per[keyed] = new
        stamps = dict(entry.get("last_change") or {}) if isinstance(entry.get("last_change"), dict) else {}
        stamps[keyed] = _stamp(t, ev, source, delta=new - old)
        vals[nm] = {"value": per, "last_change": stamps}
    else:
        vals[nm] = {"value": new, "last_change": _stamp(t, ev, source, delta=new - old)}
    _save(channel_id, decl, vals)
    logger.info("[CustomVar] %s%s %s→%s (%+d) src=%s ev=%s", nm,
                f"/{keyed}" if keyed else "", old, new, new - old, source, ev[:60])
    return {"name": nm, **({"key": keyed} if keyed else {}), "from": old, "to": new,
            "delta": new - old, "evidence": ev}


# =========================================================
# [2026-09-10 P13] OOC 편집 길의 선언 칸 — 새 길 0, 칸만 늘린다
#
# `(ooc: …)` 편집 콜은 이미 1회성이라 콜 ±0 이다. 늘어난 것은 셋뿐:
#   ① 분류기가 보는 이름 목록(`declared_mention_names`)
#   ② 편집 콜 입력에 실리는 선언 블록(`build_declared_block`, 콜 0)
#   ③ 적용 관문 하나(`apply_ooc_edits`) — `field=="declared"` 만 받는다.
#
# 권한 판단: **유저 손이 쓰는 값에는 캡도 evidence 도 걸지 않는다.** 캡은 모델의 과장에
# 거는 재갈이고 evidence 는 "산문이 그렇게 말했나"를 묻는 관문인데, 유저는 그 산문의
# 저자다. 남는 관문은 둘 — **선언에 없는 이름은 못 만든다**(OOC 로 세계가 늘어나면 선언
# 층이 무의미해진다)와 **range 클램프**(범위는 선언이 정한 세계의 모양이라 누가 쓰든 같다).
# 그래서 선언 **자체**(rule·range·형식 원문·전이식)는 여기서 못 바꾼다 — 그건 파일의 몫이다.
# =========================================================

OOC_SOURCE = "ooc"

_OOC_OPS = {
    "set": "set", "설정": "set", "수정": "set", "변경": "set", "update": "set",
    "delta": "delta", "증감": "delta", "가감": "delta", "adjust": "delta",
    "add": "add", "추가": "add", "신설": "add", "new": "add", "create": "add",
    "remove": "remove", "삭제": "remove", "제거": "remove", "delete": "remove",
    "rename": "rename", "이름": "rename", "정정": "rename", "개명": "rename",
    "append": "append", "기록": "append", "적립": "append", "log": "append",
}

# 종류 → 되비침 문안. value/append 밖은 전부 "파일로".
_OOC_FILE_ONLY = {"format": "형식", "transition": "전이", "narrative": "서술 섹션"}


def declared_index(channel_id: str) -> Dict[str, str]:
    """이 채널이 **이름으로 아는 것** 전부 → `{이름: 종류}`.

    종류 다섯: `value`(선언 변수 + 시스템 변수) · `append`(쌓는 기록 섹션) ·
    `narrative`(매턴 다시 쓰는 섹션) · `format`(출력 형식) · `transition`(전이).

    ★분류기와 적용 관문이 **같은 목록**을 본다. 둘이 갈리면 "edit 으로는 잡혔는데
      모르는 이름이라 한다"가 영영 안 풀린다. 이름이 겹치면 값이 이긴다(선언 층이 먼저다).
    """
    out: Dict[str, str] = {}
    try:
        for nm, spec in (get_declarations(channel_id) or {}).items():
            key = str(nm).strip()
            if key and isinstance(spec, dict):
                out[key] = "value"
    except Exception as e:
        logger.debug("[OOC] 선언 읽기 skip: %s", e)
    try:
        import status_panel as _sp_ix
        appends = {str(n).strip() for n in (_sp_ix.list_append_sections(channel_id) or [])}
        for nm in (_sp_ix.list_panel_sections(channel_id) or {}):
            key = str(nm).strip()
            if key and key not in out:
                out[key] = "append" if key in appends else "narrative"
    except Exception as e:
        logger.debug("[OOC] 섹션 읽기 skip: %s", e)
    try:
        for nm in (domain_manager.get_output_rules(channel_id) or {}):
            key = str(nm).strip()
            if key and key not in out:
                out[key] = "format"
    except Exception as e:
        logger.debug("[OOC] 형식 읽기 skip: %s", e)
    try:
        import expr_engine as _ee_ix
        for nm in (_ee_ix.list_transitions(channel_id) or {}):
            key = str(nm).strip()
            if key and key not in out:
                out[key] = "transition"
    except Exception as e:
        logger.debug("[OOC] 전이 읽기 skip: %s", e)
    return out


def declared_mention_names(channel_id: str) -> List[str]:
    """분류기가 본문에서 찾을 이름 — 선언 이름 + **레코드 항목 이름**.

    항목 이름을 여기에만 넣고 `declared_index` 에는 안 넣는 이유: "양파에 물 줬어"는
    편집 요청이 맞지만(분류), 양파는 변수 이름이 아니라 화분의 한 칸이다(적용).
    두 목록의 쓰임이 달라서 한 목록으로 합치면 관문이 헛되이 열린다.
    """
    names = list(declared_index(channel_id))
    try:
        vals = get_values(channel_id)
        for nm, spec in (get_declarations(channel_id) or {}).items():
            if not isinstance(spec, dict) or str(spec.get("type")) != "list":
                continue
            cur = (vals.get(nm) or {}).get("value")
            if isinstance(cur, dict):
                names.extend(str(k).strip() for k in list(cur)[:list_max(spec)])
    except Exception as e:
        logger.debug("[OOC] 항목 이름 skip: %s", e)
    return [n for n in names if n]


def mentions_declared(channel_id: str, text: str) -> bool:
    """분류기 전용 판정 — 이 채널이 아는 이름이 본문에 **낱말로** 있나.

    급식 게이트(`mentioned_names`)와 갈라 둔 한 가지: **왼쪽 경계를 하나 더 문다.**
    `금` 같은 한 글자 이름은 `지금`·`요금` 안에 그냥 들어 있고, 급식에서는 줄 하나가
    괜히 실리는 값싼 사고지만 여기서는 OOC 질문 한 줄이 **편집 콜 하나**를 태운다
    (오분류 비용 = 콜 1 + 안내 메시지 2). 오른쪽은 한국어 조사가 이름에 붙으므로
    (금을/금이) 종전대로 열어 둔다.
    """
    blob = str(text or "")
    if not blob:
        return False
    for nm in declared_mention_names(channel_id):
        start = 0
        while True:
            i = blob.find(nm, start)
            if i < 0:
                break
            prev = blob[i - 1] if i > 0 else ""
            if not (prev and (prev.isalnum() or "\uac00" <= prev <= "\ud7a3")):
                return True
            start = i + 1
    return False


def build_declared_block(channel_id: str) -> str:
    """편집 콜 입력에 실리는 선언 한 블록. **콜 0**(전부 저장분 읽기).

    급식(`select_mentioned`)과 달리 어휘 게이트가 없다 — OOC 편집 콜은 1회성이고,
    모델이 "무엇을 고칠 수 있나"를 알려면 목록 전체가 보여야 한다. 파일로만 고칠 수
    있는 것들도 **이름만** 싣는다: 모델이 그 이름을 값으로 착각해 지어내는 것보다
    "이건 파일로"라고 되비칠 근거를 주는 쪽이 싸다.
    """
    if not is_enabled():
        return ""
    idx = declared_index(channel_id)
    if not idx:
        return ""
    decl = get_declarations(channel_id)
    vals = get_values(channel_id)
    lines: List[str] = ["[DECLARED — OOC 편집 대상]"]
    for nm, kind in idx.items():
        if kind != "value":
            continue
        spec = decl.get(nm) or {}
        vtype = str(spec.get("type", "gauge"))
        cur = (vals.get(nm) or {}).get("value")
        tag = " (시스템)" if spec.get("system") else ""
        if vtype == "text":
            shape = f"문자열 {TEXT_MAX}자까지"
            now = str(cur if cur is not None else spec.get("init", "")) or "—"
        elif vtype == "enum":
            shape = "단계 " + " > ".join(str(x) for x in (spec.get("stages") or []))
            now = str(cur if cur is not None else spec.get("init", ""))
        elif vtype == "list":
            flds = item_fields(spec)
            shape = "목록 필드[" + ", ".join(
                f"{f} {r[0]}~{r[1]}" for f, r in flds.items()) + "]"
            now = ", ".join(_item_text(spec, k, v, channel_id)
                            for k, v in list((cur or {}).items())[:list_max(spec)]) or "—"
        else:
            lo, hi = (spec.get("range") or [0, 0])[:2]
            shape = f"{vtype} {lo}~{hi}"
            now = format_value(spec, cur if cur is not None else spec.get("init"))
            if isinstance(cur, dict):
                now = ", ".join(f"{k} {v}" for k, v in list(cur.items())[:MAX_NPC_VALUES]) or "—"
        lines.append(f"- 값 {nm}: {shape}{tag} | 현재 {now} | {spec.get('rule', '')}"[:400])
    appends = [n for n, k in idx.items() if k == "append"]
    if appends:
        lines.append("- 기록(쌓기 전용, op=append 만): " + " · ".join(appends))
    locked = [f"{n}({_OOC_FILE_ONLY[k]})" for n, k in idx.items() if k in _OOC_FILE_ONLY]
    if locked:
        lines.append("- 파일로만(OOC 로 못 고침): " + " · ".join(locked))
    return "\n".join(lines)


def _ooc_line(nm: str, rec: Optional[Dict[str, Any]]) -> str:
    """되비침 한 줄. 새 메시지 0 — 기존 결과 메시지 목록에 합류하는 조각이다."""
    if not rec:
        return f"✗ {nm} — 변화 없음"
    op = str(rec.get("op") or "")
    if op == "rename":
        return f"✓ {nm} {rec.get('from_item')} → {rec.get('item')}(이름 정정)"
    if op == "add":
        return f"✓ {nm} + {rec.get('item')}"
    if op == "remove":
        return f"✓ {nm} − {rec.get('item')}"
    if rec.get("item"):
        head = f"{nm} {rec.get('item')}"
        if rec.get("field"):
            head += f" {rec.get('field')}"
        return f"✓ {head} {rec.get('from')} → {rec.get('to')}"
    if "from" in rec:
        key = f"/{rec.get('key')}" if rec.get("key") else ""
        return f"✓ {nm}{key} {rec.get('from')} → {rec.get('to')}"
    return f"✓ {nm} → {rec.get('to')}"


def apply_ooc_edits(channel_id: str, user_id: str, edits: Any) -> List[str]:
    """`field=="declared"` 편집만 받아 적용하고 **되비침 줄 목록**을 돌려준다.

    관문 순서: 이름(선언에 있나) → 종류(값·기록만 통과) → op → 쓰기.
    쓰기는 전부 코드 소유 문(`apply_code_write` / `apply_code_item`)을 지난다 —
    캡·evidence 는 그 문이 애초에 안 걸고, range 클램프는 그 문이 여전히 문다.
    """
    out: List[str] = []
    if not edits or not is_enabled():
        return out
    idx = declared_index(channel_id)
    lower = {k.lower(): k for k in idx}
    decl = get_declarations(channel_id)
    uid = str(user_id or "")
    for ed in (edits if isinstance(edits, (list, tuple)) else []):
        if not isinstance(ed, dict):
            continue
        if str(ed.get("field") or "").strip() != "declared":
            continue
        raw = str(ed.get("name") or ed.get("key") or "").strip()
        nm = raw if raw in idx else lower.get(raw.lower(), "")
        if not nm:
            out.append(f"✗ 모르는 이름: {raw or '(비어 있음)'}")
            continue
        kind = idx.get(nm, "")
        op = _OOC_OPS.get(str(ed.get("op") or ed.get("action") or "").strip().lower(), "")
        value = ed.get("value")
        item = str(ed.get("item") or "").strip()
        to = str(ed.get("to") or ed.get("new_name") or "").strip()
        record = str(ed.get("record") or ed.get("item_field") or "").strip()
        npc = str(ed.get("npc") or "").strip()

        if kind in _OOC_FILE_ONLY:
            out.append(f"✗ {nm} — 이 종류({_OOC_FILE_ONLY[kind]})는 파일로 (`!출력룰 추가`)")
            continue
        if kind == "append":
            if op != "append":
                out.append(f"✗ {nm} — 기록은 쌓기만 한다. 고쳐 쓰려면 파일로")
                continue
            text = str(value if value is not None else ed.get("text") or "").strip()
            n = 0
            if text:
                try:
                    import status_panel as _sp_oe
                    n = _sp_oe.apply_append_entries(
                        channel_id, [{"name": nm, "text": text, "evidence": OOC_SOURCE}], uid)
                except Exception as e:
                    logger.debug("[OOC] append 적립 skip: %s", e)
            out.append(f"✓ {nm} + {text[:30]}" if n else f"✗ {nm} — 적립하지 못했다")
            continue

        spec = decl.get(nm) if isinstance(decl.get(nm), dict) else {}
        vtype = str(spec.get("type", "gauge"))
        kw = {"evidence": "", "source": OOC_SOURCE, "actor": uid, "npc": npc}
        if vtype == "list":
            if op in ("add", "remove", "rename"):
                if not item:
                    out.append(f"✗ {nm} — 항목 이름이 없다")
                    continue
                rec = apply_code_item(channel_id, nm, item, op, to=to,
                                      evidence="", source=OOC_SOURCE)
                out.append(_ooc_line(nm, rec))
                continue
            if op in ("set", "delta"):
                if not item:
                    out.append(f"✗ {nm} — 항목 이름이 없다")
                    continue
                kw["item"] = item
                if record:
                    kw["field"] = record
                if op == "set":
                    rec = apply_code_write(channel_id, nm, value=value, **kw)
                else:
                    rec = apply_code_write(channel_id, nm,
                                           delta=ed.get("delta", value), **kw)
                out.append(_ooc_line(nm, rec))
                continue
            out.append(f"✗ {nm} — 목록에 쓸 수 없는 지시({op or '?'})")
            continue

        if op == "set":
            out.append(_ooc_line(nm, apply_code_write(channel_id, nm, value=value, **kw)))
        elif op == "delta":
            out.append(_ooc_line(nm, apply_code_write(
                channel_id, nm, delta=ed.get("delta", value), **kw)))
        else:
            out.append(f"✗ {nm} — 값에는 쓸 수 없는 지시({op or '?'})")
    return out


def _cap_delta(spec: Dict[str, Any], delta: int) -> int:
    """[v1] 비대칭 델타캡. 선언에 없으면 무제한(=범위 클램프만).

    ★**범위 클램프 ≠ 델타 클램프**(SimCore 교훈) — 0-100 안이라고 한 턴에 0→100 이
      정당해지지는 않는다. 상승·하강을 따로 잡는 것이 요점이다("천천히 쌓이고 빨리 식는").
    """
    try:
        gain = int(spec.get("max_gain") or 0)
        loss = int(spec.get("max_loss") or 0)
    except (TypeError, ValueError):
        return delta
    if delta > 0 and gain:
        return min(delta, gain)
    if delta < 0 and loss:
        return max(delta, -loss)
    return delta


def _read_delta(item: Dict[str, Any]) -> Optional[int]:
    try:
        return int(round(float(item.get("delta", 0) or 0)))
    except (TypeError, ValueError):
        return None


def _stamp(t: int, evidence: str, source: str = "cognition.custom_var_deltas",
           **extra: Any) -> Dict[str, Any]:
    out = {"turn": t, "evidence": evidence, "source": source}
    out.update(extra)
    return out


def _apply_numeric(channel_id: str, name: str, spec: Dict[str, Any],
                   vals: Dict[str, Any], item: Dict[str, Any],
                   evidence: str, t: int, actor: str = "",
                   exempt_cap: bool = False,
                   source: str = "cognition.custom_var_deltas") -> Optional[Dict[str, Any]]:
    """gauge/counter — 이전 값 + (캡된)델타 → 범위 클램프.

    키 있는 저장 모양이 둘 있고 **같은 가지를 공유한다**: NPC 스코프(키=인물명) / per_actor
    시스템 변수(키=user_id). 후자만 이전 값 결정에 **이월 승계**가 끼어든다(_actor_base).

    exempt_cap — 코드 소유 쓰기(Effort 선불)만 True. 비대칭 델타캡은 **LLM 신고에 거는 재갈**
      이지 코드가 스스로에게 물릴 재갈이 아니다. 범위 클램프는 여전히 적용된다.
    """
    delta = _read_delta(item)
    if not delta:
        return None
    try:
        lo, hi = int((spec.get("range") or [0, 0])[0]), int((spec.get("range") or [0, 0])[1])
    except (TypeError, ValueError, IndexError):
        return None
    capped = delta if exempt_cap else _cap_delta(spec, delta)

    entry = vals.get(name) if isinstance(vals.get(name), dict) else {}

    # --- per_actor(시스템 변수) — 키=user_id, 이전 값=레지스트리→이월→init ---
    if spec.get("per_actor"):
        # [2026-09-06 P8c] **인물 지정 키 하나로 갈린다.** npc_enabled 시스템 변수는 같은 dict
        #   안에서 인물 키도 받는다 — 항목에 `npc` 가 있으면 그 인물 값, 없으면 acting user.
        #   캡·클램프·evidence 게이트는 갈래와 무관하게 같다(NPC 전용 규율 0).
        npc_key = ""
        if spec.get("npc_enabled") and str(item.get("npc") or "").strip():
            npc_key = resolve_npc(channel_id, item.get("npc"))
            if not npc_key:
                logger.info("[CustomVar] %s 인물 미허용/미상 → 폐기 (npc=%r)", name, item.get("npc"))
                return None
        who = npc_key or str(item.get("actor") or actor or _default_actor(channel_id) or "").strip()
        if not who:
            logger.info("[CustomVar] %s 대상 참가자 미상 → 폐기", name)
            return None
        per = dict(entry.get("value") or {}) if isinstance(entry.get("value"), dict) else {}
        if npc_key:
            if npc_key not in per and len(_npc_keys(channel_id, per)) >= MAX_NPC_VALUES:
                logger.info("[CustomVar] %s 인물별 값 상한 %d 도달 → %s 폐기",
                            name, MAX_NPC_VALUES, npc_key)
                return None
            try:
                old = int(per.get(npc_key, spec.get("init", lo)))
            except (TypeError, ValueError):
                old = int(spec.get("init", lo) or lo)
        else:
            old = _actor_base(channel_id, spec, entry, who)
        new = max(lo, min(hi, old + capped))
        if new == old:
            logger.debug("[CustomVar] %s/%s no-op (%s%+d)", name, who, old, delta)
            return None
        per[who] = new
        stamps = dict(entry.get("last_change") or {}) if isinstance(entry.get("last_change"), dict) else {}
        stamps[who] = _stamp(t, evidence, source, delta=new - old)
        vals[name] = {"value": per, "last_change": stamps}
        logger.info("[CustomVar] %s/%s %s→%s (%+d, 신고 %+d%s) src=%s ev=%s",
                    name, who, old, new, new - old, delta,
                    ", 캡면제" if exempt_cap else (", 캡" if capped != delta else ""),
                    source, evidence[:60])
        return {"name": name, ("npc" if npc_key else "actor"): who, "from": old, "to": new,
                "delta": new - old, "evidence": evidence}

    npc = ""
    if str(spec.get("scope")) == "npc":
        npc = resolve_npc(channel_id, item.get("npc"))
        if not npc:
            logger.info("[CustomVar] %s 인물 미허용/미상 → 폐기 (npc=%r)", name, item.get("npc"))
            return None
        per = dict(entry.get("value") or {}) if isinstance(entry.get("value"), dict) else {}
        try:
            old = int(per.get(npc, spec.get("init", lo)))
        except (TypeError, ValueError):
            old = int(spec.get("init", lo) or lo)
        new = max(lo, min(hi, old + capped))
        if new == old:
            return None
        if npc not in per and len(per) >= MAX_NPC_VALUES:
            logger.info("[CustomVar] %s 인물별 값 상한 %d 도달 → %s 폐기", name, MAX_NPC_VALUES, npc)
            return None
        per[npc] = new
        stamps = dict(entry.get("last_change") or {}) if isinstance(entry.get("last_change"), dict) else {}
        stamps[npc] = _stamp(t, evidence, source, delta=new - old)
        vals[name] = {"value": per, "last_change": stamps}
        logger.info("[CustomVar] %s/%s %s→%s (%+d, 신고 %+d) ev=%s",
                    name, npc, old, new, new - old, delta, evidence[:60])
        return {"name": name, "npc": npc, "from": old, "to": new,
                "delta": new - old, "evidence": evidence}

    try:
        old = int(entry.get("value", spec.get("init", lo)))
    except (TypeError, ValueError):
        old = int(spec.get("init", lo) or lo)
    new = max(lo, min(hi, old + capped))
    if new == old:
        logger.debug("[CustomVar] %s no-op (%s%+d, 범위 %s-%s)", name, old, delta, lo, hi)
        return None
    vals[name] = {"value": new, "last_change": _stamp(t, evidence, delta=new - old)}
    logger.info("[CustomVar] %s %s→%s (%+d, 신고 %+d%s) ev=%s",
                name, old, new, new - old, delta,
                ", 캡" if capped != delta else "", evidence[:60])
    return {"name": name, "from": old, "to": new, "delta": new - old, "evidence": evidence}


def _apply_text(channel_id: str, name: str, spec: Dict[str, Any],
                vals: Dict[str, Any], item: Dict[str, Any],
                evidence: str, t: int) -> Optional[Dict[str, Any]]:
    """[2026-09-13 P17] text — LLM 은 **새 문자열**을 낸다. 캡이 없는 이유: 캡은 수의
    속도에 거는 재갈인데 문자열엔 속도가 없다. 무는 건 길이(TEXT_MAX) 하나다.

    공통 관문(선언 존재·evidence·no-op)은 apply_deltas 가 이미 지났다.
    """
    raw = item.get("value", item.get("text", item.get("to")))
    if raw is None:
        return None
    new = str(raw).strip()[:TEXT_MAX]
    if not new:
        return None
    entry = vals.get(name) if isinstance(vals.get(name), dict) else {}
    old = entry.get("value")
    old = str(old) if isinstance(old, str) else str(spec.get("init", "") or "")
    if new == old:
        return None                       # no-op 은 도장을 안 찍는다
    vals[name] = {"value": new, "last_change": _stamp(t, evidence, prev=old)}
    logger.info("[CustomVar] %s %r→%r ev=%s", name, old[:20], new[:20], evidence[:60])
    return {"name": name, "from": old, "to": new, "evidence": evidence}


def _apply_enum(channel_id: str, name: str, spec: Dict[str, Any],
                vals: Dict[str, Any], item: Dict[str, Any],
                evidence: str, t: int) -> Optional[Dict[str, Any]]:
    """[v1] enum — LLM은 **목표 단계 이름**을 낸다. 코드가 클램프한다.

    Rules (set_drive_gated 계보 — 새 문법 발명이 아니라 그 관문의 유저-정의판):
      1. 목록 밖의 단계 = invalid. 단계는 만들어지지 않는다.
      2. `단조` 선언이면 하강 자체가 거부(역행 불가).
      3. 어느 방향이든 한 번에 max_step(기본 1)단계 — 넘으면 **거부가 아니라 클램프**.
      4. 같은 단계면 no-op. 도장도 안 찍는다(도장이 매 턴 갱신되면 판독값이 0).
    """
    stages = [str(s) for s in (spec.get("stages") or [])]
    if len(stages) < 2:
        return None
    target_raw = item.get("stage", item.get("value", item.get("to")))
    target = _match_stage(target_raw, stages)
    if not target:
        logger.info("[CustomVar] %s 알 수 없는 단계 %r → 폐기 (목록: %s)",
                    name, target_raw, " > ".join(stages))
        return None
    try:
        step_cap = max(1, int(spec.get("max_step", DEFAULT_MAX_STEP) or DEFAULT_MAX_STEP))
    except (TypeError, ValueError):
        step_cap = DEFAULT_MAX_STEP

    entry = vals.get(name) if isinstance(vals.get(name), dict) else {}
    npc = ""
    per: Dict[str, Any] = {}
    if str(spec.get("scope")) == "npc":
        npc = resolve_npc(channel_id, item.get("npc"))
        if not npc:
            logger.info("[CustomVar] %s 인물 미허용/미상 → 폐기 (npc=%r)", name, item.get("npc"))
            return None
        per = dict(entry.get("value") or {}) if isinstance(entry.get("value"), dict) else {}
        cur = _match_stage(per.get(npc), stages) or stages[0]
        if npc not in per and len(per) >= MAX_NPC_VALUES:
            logger.info("[CustomVar] %s 인물별 값 상한 %d 도달 → %s 폐기", name, MAX_NPC_VALUES, npc)
            return None
    else:
        cur = _match_stage(entry.get("value"), stages) or _match_stage(spec.get("init"), stages) or stages[0]

    old_i, new_i = stages.index(cur), stages.index(target)
    if new_i == old_i:
        return None
    if new_i < old_i and spec.get("monotonic"):
        logger.info("[CustomVar] %s 단조 위반 %s→%s 거부 (역행 불가) ev=%s",
                    name, cur, target, evidence[:60])
        return None
    step = new_i - old_i
    clamped_i = old_i + (step_cap if step > 0 else -step_cap) if abs(step) > step_cap else new_i
    clamped_i = max(0, min(len(stages) - 1, clamped_i))
    new_stage = stages[clamped_i]
    if new_stage == cur:
        return None

    rec = {"name": name, "from": cur, "to": new_stage, "evidence": evidence}
    if npc:
        per[npc] = new_stage
        stamps = dict(entry.get("last_change") or {}) if isinstance(entry.get("last_change"), dict) else {}
        stamps[npc] = _stamp(t, evidence, stage=new_stage, prev=cur)
        vals[name] = {"value": per, "last_change": stamps}
        rec["npc"] = npc
    else:
        vals[name] = {"value": new_stage,
                      "last_change": _stamp(t, evidence, stage=new_stage, prev=cur)}
    logger.info("[CustomVar] %s%s %s→%s%s ev=%s", name, f"/{npc}" if npc else "",
                cur, new_stage, f" (신고 {target}, 클램프)" if new_stage != target else "",
                evidence[:60])
    return rec


# [2026-09-10 P13] 이름 정정 낱말. `_LIST_OPS` 와 갈라 둔 이유는 apply_code_item 주석 참조.
_RENAME_OPS = {"rename", "이름", "이름정정", "정정", "개명", "바꿔", "고쳐"}

_LIST_OPS = {
    "add": "add", "신설": "add", "new": "add", "추가": "add", "create": "add",
    "remove": "remove", "삭제": "remove", "제거": "remove", "delete": "remove", "drop": "remove",
    "delta": "delta", "update": "delta", "set": "delta", "수정": "delta", "진행": "delta",
}


def _apply_list(name: str, spec: Dict[str, Any], vals: Dict[str, Any],
                item: Dict[str, Any], evidence: str, t: int) -> Optional[Dict[str, Any]]:
    """[v1] list — 항목별 수치. 연산 add / remove / delta.

    계약:
      - 항목 **신설·제거도 evidence 필수**(상위 관문에서 이미 걸렀다) — 목록이 조용히
        불어나거나 사라지지 않는다.
      - 수치는 언제나 **델타**. 항목이 생길 때의 초기값만 절대값이고 그마저 범위로 클램프된다.
      - 항목 수 캡(`list_max(spec)` = 선언별 `max` 또는 MAX_LIST_ITEMS)을 넘는 신설은 거절 + 로그 1줄.
      - 100% / 목표 도달 항목의 **이동은 하지 않는다** — 어디로 옮길지는 rule 과 산문의 몫이지
        코드가 정할 일이 아니다(과공학 금지).
    """
    op = _LIST_OPS.get(str(item.get("op", "") or "").strip().lower(), "")
    item_name = str(item.get("item", item.get("key", "")) or "").strip()[:ITEM_NAME_MAX]
    if not item_name:
        logger.debug("[CustomVar] %s 항목 이름 없음 → 폐기 (%r)", name, item)
        return None
    try:
        lo, hi = int((spec.get("item_range") or STOCK_RANGE)[0]), int((spec.get("item_range") or STOCK_RANGE)[1])
    except (TypeError, ValueError, IndexError):
        lo, hi = STOCK_RANGE

    entry = vals.get(name) if isinstance(vals.get(name), dict) else {}
    items = dict(entry.get("value") or {}) if isinstance(entry.get("value"), dict) else {}
    # 항목 이름 관용 해석(조사·대소문자) — 없는 항목에 delta 를 쏘면 신설이 아니라 폐기다.
    key = item_name if item_name in items else next(
        (k for k in items if str(k).strip().lower() == item_name.lower()), "")
    if not op:
        op = "delta" if key else "add"

    delta = _read_delta(item)

    if op == "remove":
        if not key:
            return None
        items.pop(key, None)
        vals[name] = {"value": items, "last_change": _stamp(t, evidence, item=key, op="remove")}
        logger.info("[CustomVar] %s 항목 제거 %s ev=%s", name, key, evidence[:60])
        return {"name": name, "item": key, "op": "remove", "evidence": evidence}

    if op == "add" and not key:
        if len(items) >= list_max(spec):
            logger.info("[CustomVar] %s 항목 상한 %d 도달 → 신설 거부 (%s)", name, list_max(spec), item_name)
            return None
        try:
            start = int(round(float(item.get("value", delta if delta and delta > 0 else lo) or lo)))
        except (TypeError, ValueError):
            start = lo
        start = max(lo, min(hi, start))
        goal = 0
        if str(spec.get("item_mode", "stock")) == "stock":
            try:
                goal = max(0, min(hi, int(round(float(item.get("goal", 0) or 0)))))
            except (TypeError, ValueError):
                goal = DEFAULT_ITEM_GOAL
        # [2026-09-09 P11] 레코드 목록이면 LLM 의 `{추가: 양파}` 도 **선언 init 로** 선다 —
        #   재심기가 "날짜를 0으로 돌리는 연산"이 아니라 새 레코드의 탄생인 이유가 이 줄이다.
        items[item_name] = (_clean_record(spec, {}) if spec.get("fields")
                            else {"n": start, "goal": goal})
        vals[name] = {"value": items, "last_change": _stamp(t, evidence, item=item_name, op="add")}
        logger.info("[CustomVar] %s 항목 신설 %s = %s(목표 %s) ev=%s",
                    name, item_name, start, goal, evidence[:60])
        return {"name": name, "item": item_name, "op": "add", "to": start, "evidence": evidence}

    # delta (또는 이미 있는 항목에 대한 add)
    if not key:
        logger.debug("[CustomVar] %s 없는 항목에 델타 → 폐기 (%s)", name, item_name)
        return None
    rec = items.get(key)
    old = rec.get("n", 0) if isinstance(rec, dict) else rec
    try:
        old = int(old)
    except (TypeError, ValueError):
        old = lo
    if not delta:
        return None
    new = max(lo, min(hi, old + _cap_delta(spec, delta)))
    # 목표(goal)는 신고에 실려 오면 갱신한다 — 수치가 아니라 **계획**이라 델타 대상이 아니다.
    goal = rec.get("goal", 0) if isinstance(rec, dict) else 0
    if item.get("goal") is not None:
        try:
            goal = max(0, min(hi, int(round(float(item.get("goal") or 0)))))
        except (TypeError, ValueError):
            pass
    if new == old and goal == (rec.get("goal", 0) if isinstance(rec, dict) else 0):
        return None
    # [2026-09-24 감사] `{"n","goal"}` 통째 대입이 레코드 목록(fields 선언)의 나머지 필드를
    #   지웠다(LLM 델타 한 번에 양파의 수분·나이가 증발). apply_code_write 와 같은 보존 규약 —
    #   기존 dict 를 두고 옛 두 칸만 갈아 끼운다.
    _base = dict(rec) if isinstance(rec, dict) else {}
    _base.update({"n": new, "goal": goal})
    items[key] = _base
    vals[name] = {"value": items,
                  "last_change": _stamp(t, evidence, item=key, op="delta", delta=new - old)}
    logger.info("[CustomVar] %s 항목 %s %s→%s (%+d) ev=%s", name, key, old, new, new - old, evidence[:60])
    return {"name": name, "item": key, "op": "delta", "from": old, "to": new,
            "delta": new - old, "evidence": evidence}


# =========================================================
# 저작 (b) — 자연어 1회성 변환 콜 (light)
# =========================================================
# ★**저작 시 1회**다. 매턴 콜 순증 0 — 스펙 §3-2 의 콜 규율.
#   실패하면 파이프 문법이 폴백이고, 산출은 위 validate_declaration 을 **반드시** 지난다
#   (콜은 초안을 쓸 뿐 관문이 아니다).

_NL_PROMPT = """You convert one sentence of a player's intent into a variable declaration for a TTRPG bot.

The player writes in Korean and describes a quantity their world needs to track
(mana, corruption, debt, reputation, supplies …). Turn it into one JSON object.

## SCHEMA
{{
  "name": str,      // display name, Korean allowed, <= {name_max} chars. Take the player's own word for it.
  "type": "gauge" | "counter" | "enum" | "list",
                    // gauge = a level that fills and drains. counter = a tally spent and gained.
                    // enum  = named steps it moves between (무명 → 안면 → 단골). Not a number.
                    // list  = many named entries, each carrying its own number.
  "scope": "global" | "pc" | "npc",
                    // pc = belongs to the player character. global = belongs to the world.
                    // npc = one value per character (only for gauge/counter/enum).
  "rule": str,      // ONE Korean line: when it goes up, when it goes down. This line is read by the
                    // extractor every turn, so write the conditions, not a description of the concept.

  // gauge / counter only:
  "range": [low, high],          // integers, low < high. Pick what the sentence implies; 0-100 when it implies nothing.
  "init": int,                   // starting value, inside the range
  "max_gain": int,               // optional. Most a single exchange may add. Use it when the sentence says
  "max_loss": int,               // optional. Most a single exchange may take. something rises slowly or falls fast.

  // enum only:
  "stages": [str, ...],          // 2..{stage_max} step names, lowest first, each <= {stage_name_max} chars
  "init": str,                   // one of the stages
  "monotonic": bool,             // true when the sentence says it never goes back down

  // list only:
  "item_mode": "progress" | "stock",   // progress = each entry is a 0-100 percentage.
                                       // stock = each entry is a current amount against a target.

  // any type, optional:
  "format": str                  // display only, must contain {{v}} — e.g. "{{v}} 골드"
}}

Names already declared in this world (do not duplicate, do not rename them): {existing}

Take the values the sentence actually states. Where the sentence is silent, choose the plainest
default that fits the schema rather than inventing detail.

## PLAYER'S SENTENCE
{text}

## OUTPUT
JSON object only."""


async def convert_natural_declaration(
    client: Any,
    model_id: str,
    text: str,
    existing_names: Optional[List[str]] = None,
) -> Tuple[Optional[Dict[str, Any]], str]:
    """자연어 한 문장 → 스키마 초안. Returns: (raw spec, "") 또는 (None, 사유).

    ⚠ 여기서 돌려주는 건 **초안**이다. 호출부가 validate_declaration 을 통과시켜야 한다.
    """
    if not is_enabled():
        return None, "변수 기능이 꺼져 있습니다."
    body = str(text or "").strip()
    if not body:
        return None, "변환할 문장이 비어 있습니다."

    import json as _json
    try:
        from google.genai import types  # type: ignore
        from memory_system import api_call_with_retry
        import bot_utils as _bu
        import text_resources as _tr
    except Exception as e:      # 스모크·오프라인 환경
        return None, f"변환 콜 준비 실패: {e}"

    prompt = _NL_PROMPT.format(
        name_max=NAME_MAX,
        stage_max=MAX_STAGES,
        stage_name_max=STAGE_NAME_MAX,
        existing=", ".join(existing_names or []) or "(none)",
        text=body[:600],
    )
    cfg = types.GenerateContentConfig(
        system_instruction=getattr(_tr, "CONTENT_AUTHORIZATION_MANDATE", ""),
        temperature=0.2,            # 스키마 채우기 — 창작이 아니다
        max_output_tokens=512,
        response_mime_type="application/json",
        safety_settings=config.SAFETY_SETTINGS,
    )
    contents = [types.Content(role="user", parts=[types.Part(text=prompt)])]

    try:
        with config.light_call():
            result = await api_call_with_retry(
                client, model_id, contents, cfg, operation_name="CustomVarAuthor",
            )
        if not result:
            return None, "변환 콜이 빈 응답을 돌려줬습니다."
        cleaned = _bu.clean_json_text(result)
        try:
            data = _json.loads(cleaned)
        except Exception:
            data = _json.loads(_bu.repair_json(cleaned))
        if isinstance(data, list) and data:
            data = data[0]
        if not isinstance(data, dict):
            return None, "변환 결과가 스키마 모양이 아닙니다."
        return data, ""
    except Exception as e:
        logger.warning("[CustomVar] 자연어 변환 실패: %s", e)
        return None, f"변환 실패: {e}"


# =========================================================
# 표시 재료 (패널 합성이 읽는다 — discord 비의존)
# =========================================================

def build_display_rows(channel_id: str) -> List[Tuple[str, str]]:
    """[(변수명, "80/100 · t12 +5")] — status_panel 이 코드 소유값으로 얹는다.

    gauge 는 `현재/최대`, counter 는 `현재`. 최근 변화가 있으면 꼬리에 붙인다.
    """
    if not is_enabled():
        return []
    decl = get_declarations(channel_id)
    if not decl:
        return []
    vals = get_values(channel_id)
    rows: List[Tuple[str, str]] = []
    for name, spec in decl.items():
        if not isinstance(spec, dict):
            continue
        # 시스템 변수는 **코드 기관 줄**이 이미 그린다(status_panel._code_owned_fields).
        # 여기서도 그리면 패널에 두 번 뜬다 — 예약 이름 규율이 막던 바로 그 사고.
        if spec.get("system"):
            continue
        entry = vals.get(name) if isinstance(vals.get(name), dict) else {}
        val = entry.get("value", spec.get("init"))
        vtype = str(spec.get("type", "gauge"))
        lc = entry.get("last_change")

        # [v1] list — 항목별 줄. 항목이 없으면 그 변수는 아직 표시할 게 없다.
        if vtype == "list":
            items = val if isinstance(val, dict) else {}
            if not items:
                continue
            lines = [_item_text(spec, k, v, channel_id) for k, v in list(items.items())[:list_max(spec)]]
            text = "\n".join(lines)
            if isinstance(lc, dict) and lc.get("item"):
                text += f"\n↕ {lc.get('item')} ({lc.get('op', '')} t{lc.get('turn', 0)})"
            rows.append((str(name)[:250], text[:1000]))
            continue

        # [v1] NPC 스코프 — **변수당 인물별 줄**. 값이 없는 인물은 줄도 없다.
        if str(spec.get("scope")) == "npc":
            per = val if isinstance(val, dict) else {}
            if not per:
                continue
            stamps = lc if isinstance(lc, dict) else {}
            lines = []
            for nm, v in list(per.items())[:MAX_NPC_VALUES]:
                line = f"{nm} {format_value(spec, v)}"
                st = stamps.get(nm) if isinstance(stamps.get(nm), dict) else None
                if st:
                    line += _stamp_tail(st)
                lines.append(line)
            rows.append((str(name)[:250], "\n".join(lines)[:1000]))
            continue

        if vtype in ("enum", "text"):
            if not str(val or "").strip():
                continue
            text = format_value(spec, val)
        else:
            if not isinstance(val, (int, float)):
                continue
            text = format_value(spec, val)
        if isinstance(lc, dict):
            text += _stamp_tail(lc)
        rows.append((str(name)[:250], text[:1000]))
    return rows


def _stamp_tail(lc: Dict[str, Any]) -> str:
    """최근 변화 꼬리. 도장이 없으면 꼬리도 없다(no-op 보존 규율의 표시판)."""
    if not isinstance(lc, dict):
        return ""
    if isinstance(lc.get("delta"), (int, float)) and lc["delta"]:
        return f" · t{lc.get('turn', 0)} {int(lc['delta']):+d}"
    if lc.get("stage") and lc.get("prev"):
        return f" · t{lc.get('turn', 0)} {lc.get('prev')}→{lc.get('stage')}"
    return ""


# =========================================================
# 산문(렌더) 급식 — Slot 29 <Real_Time_Status> 안, 활력·평형·Doom 줄과 같은 자리
# =========================================================
# 스펙 §3-5: "산문: 값을 **재료로만** 급식(수치 낭독 방지는 기존 산문 규율)".
# 자리 선택 근거(실측): 코드 소유 수치가 렌더에 닿는 유일한 문법이 game_world.
#   build_real_time_display 의 `활력 80 | 평형 90 | Doom 0` 줄이다 — 선언 변수도 같은
#   종류의 값이므로 새 슬롯·새 블록을 만들지 않고 그 줄 옆에 선다.
#
# ★잘 닫기 — 급식과 낭독 방지는 **한 몸**이다.
#   전례 1: text_resources PROSE_CRAFT_PROTOCOL "System panels carry figures; prose carries
#           the body." (수치를 주되 지면에 옮기지 말라는 판정)
#   전례 2: iceberg.translate_vigor_composure — 두 축의 **방향만** 넘기고 스탯명·수치는
#           애초에 넘기지 않는다(급식 자체를 좁혀 방어).
#   레지스트리는 전례 2를 쓸 수 없다 — 유저가 정의한 변수라 코드가 "방향"으로 번역할
#   의미론을 갖고 있지 않다(rule 은 코드가 해석하지 않는다는 게 이 설계의 정체성).
#   그러므로 값은 그대로 주고 **머리 1절이 처분을 확정한다** — 전례 1과 같은 계보의 판정문.
#   팔레트 없음(어휘 예시를 주면 순회한다 — 오감 팔레트 제거 교훈), 처방 없음
#   ("X하면 Y가 된다"는 추론에 재사용된다 — 판정만 남긴다).
PROSE_FEED_HEADER = (
    "[DECLARED STATE] figures the machine is holding for this world. "
    "A figure here is a fact of the world, not a line to say: "
    "the panel carries the number, the prose carries what the number has already made of the scene."
)


def _onstage_names(channel_id: str) -> List[str]:
    """지금 무대에 선 인물. 못 읽으면 빈 목록 = NPC 스코프 변수는 침묵한다.

    ★출석 정본은 npc_manager.get_onstage_npc_names — [2026-09-02 R4] 내부가 **위치(0단)** 기반으로
      바뀌었다(`_last_appear_turn`은 PC 위치 미해상 시 폴백). 이 호출부는 무변경. 전체 명부로
      폴백하지 않는다(그 함수 자신의 규율: "잘못된 전체 명부보다 안전하다").
      여기선 특히 그렇다 — 무대 밖 인물의 값이 산문에 실리면 그 인물이 있는 것처럼 읽힌다.
    """
    try:
        import npc_manager as _npm
        return [str(n) for n in (_npm.get_onstage_npc_names(channel_id, within_turns=1) or [])]
    except Exception as e:
        logger.debug("[CustomVar] 온스테이지 조회 실패: %s", e)
        return []


def _feed_row(channel_id: str, name: str, spec: Dict[str, Any], vals: Dict[str, Any],
              onstage_set: set) -> str:
    """값 하나 → 급식 한 조각. 표기 소스는 패널과 같다(format_value·_item_text).

    못 실을 값이면 "" — 무대 밖 인물뿐인 npc 변수, 빈 목록, 수가 아닌 gauge.
    """
    raw = (vals.get(name) or {}).get("value", spec.get("init"))
    vtype = str(spec.get("type", "gauge"))
    # [2026-09-06 P8c] npc 스코프와 npc_enabled 시스템 변수가 **같은 줄**을 쓴다. 무대 판정이
    #   곧 필터라 시스템 변수의 user_id 키는 여기서 자연히 빠진다(무대에 선 것은 인물뿐).
    if npc_keyed(spec):
        per = raw if isinstance(raw, dict) else {}
        shown = [(k, v) for k, v in per.items()
                 if str(k).strip() in onstage_set][:PROSE_NPC_MAX]
        if not shown:
            return ""
        return f"{name} " + ", ".join(f"{k} {format_value(spec, v)}" for k, v in shown)
    if vtype == "list":
        items = raw if isinstance(raw, dict) else {}
        if not items:
            return ""
        return f"{name} " + ", ".join(
            _item_text(spec, k, v, channel_id) for k, v in list(items.items())[:PROSE_LIST_ITEMS])
    if vtype in ("enum", "text"):
        if not str(raw or "").strip():
            return ""
    elif not isinstance(raw, (int, float)):
        return ""
    return f"{name} {format_value(spec, raw)}"


def _changed_this_turn(entry: Any, now: int) -> bool:
    """이번 턴에 움직인 값인가 — 절단 우선순위 2순위(스펙 §3.5 "이번 턴 변화분")."""
    lc = entry.get("last_change") if isinstance(entry, dict) else None
    if isinstance(lc, dict):
        if lc.get("turn") is not None:
            try:
                return int(lc.get("turn")) == now
            except (TypeError, ValueError):
                return False
        # npc/per_actor 는 도장이 키별 dict 다 — 하나라도 이번 턴이면 변화분.
        for v in lc.values():
            if isinstance(v, dict):
                try:
                    if int(v.get("turn")) == now:
                        return True
                except (TypeError, ValueError):
                    continue
    return False


def _take_events(channel_id: str, cap: int) -> List[str]:
    """전이 발화 줄을 **읽고 비운다**. 1턴만 사는 재료라 소비가 곧 소멸이다(스펙 §1-10).

    ★앵커 밖 일시 재료라는 게 모양으로 드러나야 모델이 상태로 안 읽는다 — 그래서
      척추(매턴 최종값)와 달리 이 줄은 다음 턴에 없다.
    """
    try:
        ws = domain_manager.get_world_state(channel_id) or {}
    except Exception:
        return []
    rows = ws.get("pending_events")
    if not isinstance(rows, list) or not rows:
        return []
    out = [str(r) for r in rows if str(r or "").strip()][:max(0, int(cap))]
    ws["pending_events"] = []
    try:
        domain_manager.update_world_state(channel_id, ws)
    except Exception as e:
        logger.debug("[CustomVar] 사건 줄 소비 저장 실패: %s", e)
    return out


# [2026-09-09 P12] append 기록 급식 줄의 머리표. `<사건>` 과 구분되어야 모델이 둘을
#   같은 사실로 읽지 않는다 — 저건 방금 일어난 일, 이건 장부의 마지막 줄이다.
APPEND_FEED_LABEL = "<기록>"

# [2026-09-13 P14] 도착물 핸드아웃 머리표. TRPG 로 치면 GM 이 묘사 **전에** 미는 종이다 —
#   `<사건>`(방금 일어난 일)도 `<기록>`(장부)도 아니고, 이번 턴 산문이 **읽은 채로 시작**할
#   물건이다. `<사건>` 과 같은 1턴 재료라 소비가 곧 소멸이다(상태로 안 읽히게).
HANDOUT_FEED_LABEL = "<핸드아웃>"
# [2026-09-13 P16] 조건부 지시 머리표. 값 줄·`<사건>`·`<기록>`·`<핸드아웃>` 과 **갈라 놓는다** —
#   섞이면 모델이 지시를 "방금 일어난 일"로 읽고 그 문장을 산문에 재발로 베껴 쓴다.
DIRECTIVE_FEED_LABEL = "<지시>"
HANDOUT_KEY = "pending_handout"
# [2026-09-13 P16] 이번 턴 소비분을 **한 턴만** 남기는 자리. 급식(소비)은 산문 앞이고
#   배경 추출은 산문 뒤라, 소비가 곧 소멸이면 추출은 편지를 영영 못 본다("의뢰가 오면
#   `의뢰` 목록에 추가"가 안 도는 자리가 여기였다). 다음 소비 때 통째로 교체된다.
HANDOUT_LAST_KEY = "last_handouts"
HANDOUT_MAX_CHARS = 1400


def queue_handout(channel_id: str, text: Any) -> bool:
    """핸드아웃 본문 1건을 1턴 큐에 적재. 빈 텍스트는 안 든다(빈 머리표 = 노이즈).

    쓰는 쪽은 orchestration 4.75(산문 앞 도착물 콜), 읽는 쪽은 `build_prose_feed` 하나다.
    """
    body = str(text or "").strip()
    if not body:
        return False
    try:
        ws = domain_manager.get_world_state(channel_id) or {}
        rows = ws.get(HANDOUT_KEY)
        if not isinstance(rows, list):
            rows = []
        rows.append(body[:HANDOUT_MAX_CHARS])
        ws[HANDOUT_KEY] = rows[-2:]
        domain_manager.update_world_state(channel_id, ws)
        return True
    except Exception as e:
        logger.debug("[CustomVar] 핸드아웃 적재 실패: %s", e)
        return False


def _take_handouts(channel_id: str) -> List[str]:
    """핸드아웃을 **읽고 비운다**. `_take_events` 와 같은 규율 — 1턴만 산다."""
    try:
        ws = domain_manager.get_world_state(channel_id) or {}
    except Exception:
        return []
    rows = ws.get(HANDOUT_KEY)
    out = [str(r) for r in rows if str(r or "").strip()] if isinstance(rows, list) else []
    prev_last = ws.get(HANDOUT_LAST_KEY)
    if not out and not rows and not prev_last:
        return []
    ws[HANDOUT_KEY] = []
    # [2026-09-13 P16] 소비분을 last 로 **교체**한다 — 비어도 교체한다(안 그러면 편지 없는
    #   턴에 옛 편지가 추출 입력에 다시 실리고, 그건 "어제 온 의뢰"가 매턴 새 의뢰가 되는 길이다).
    ws[HANDOUT_LAST_KEY] = out
    try:
        domain_manager.update_world_state(channel_id, ws)
    except Exception as e:
        logger.debug("[CustomVar] 핸드아웃 소비 저장 실패: %s", e)
    return out


def last_handouts(channel_id: str) -> str:
    """이번 턴 급식이 읽은 핸드아웃 본문(한 덩이). 없으면 "".

    읽는 쪽은 배경 추출 하나다(`select_mentioned` 게이트 + `extract_outputs` 입력).
    쓰기 0 — 다음 소비가 이 칸을 갈아 끼운다.
    """
    try:
        ws = domain_manager.get_world_state(channel_id) or {}
    except Exception:
        return ""
    rows = ws.get(HANDOUT_LAST_KEY)
    if not isinstance(rows, list) or not rows:
        return ""
    return "\n".join(str(r) for r in rows if str(r or "").strip())[:HANDOUT_MAX_CHARS]


def directive_feed_rows(channel_id: str, user_id: str = "") -> List[str]:
    """[2026-09-13 P16] 지금 참인 지시의 문장들 — 급식 꼬리에 실릴 줄. 콜 0 · 쓰기 0.

    판정은 `expr_engine.active_directives`(전이 ⑤ 와 **같은 when 평가 함수**)가 하고,
    여기서는 자리표시자 렌더와 예산만 문다. `<사건>` 과 달리 **1턴 소비가 아니다** —
    조건이 참인 동안 매턴 실린다(지시는 사건이 아니라 상태에 붙은 규칙이다).
    """
    try:
        import expr_engine as _ee
        # [2026-09-24 감사] ctx 없이 불러 행위자가 비었다 — per_actor 조건(기력 < 30)은
        #   첫 참가자로, `재고`·`조각` 조건은 ExprError→거짓으로 **영구** 접혔다.
        #   행위자를 실어 보낸다(못 받으면 per_actor 와 같은 _default_actor 규약).
        _uid = str(user_id or "").strip() or _default_actor(channel_id)
        _ctx = None
        if _uid:
            import types as _types
            _ctx = _types.SimpleNamespace(user_id=_uid)
        live = _ee.active_directives(channel_id, _ctx)
    except Exception as e:
        logger.debug("[CustomVar] 지시 급식 skip: %s", e)
        return []
    if not live:
        return []
    try:
        _cap = getattr(config, "DIRECTIVE_FEED_MAX", {}) or {}
        cap_n = int(_cap.get("count", 8))
        cap_c = int(_cap.get("chars", 800))
    except (TypeError, ValueError, AttributeError):
        cap_n, cap_c = 8, 800

    rows: List[str] = []
    used = 0
    dropped = 0
    for d in live:
        text = str(d.get("text") or "").strip()
        if not text:
            continue
        try:
            text = render_placeholders(channel_id, text)
        except Exception as e:
            logger.debug("[CustomVar] 지시 자리표시자 skip: %s", e)
        if len(rows) >= cap_n or used + len(text) > cap_c:
            dropped += 1
            continue
        rows.append(text)
        used += len(text)
    if dropped:
        # 선언 순서가 우선이고 넘치면 뒤엣것이 떨어진다. 어느 관문이 먹었는지 한 줄로 낸다.
        logger.info("[Directive] drop n=%d (실림 %d/%d, 캡 %d줄·%d자, 문자 %d)",
                    dropped, len(rows), len(live), cap_n, cap_c, used)
    return rows


def build_prose_feed(channel_id: str, onstage: Optional[List[str]] = None,
                     mentioned_text: str = "", user_id: str = "") -> str:
    """선언 변수 현재값 → 산문 재료 블록. 없으면 "".

    [2026-09-06 P3] 블록은 세 단으로 고정된다(스펙 §1-10 · §3.5):
      [척추]  feed.prose == always — **선언 순서 고정, ⑥ 뒤 최종값, 매턴**.
              재등록으로 이름이 늘어도 기존 순서가 안 밀리는 게 "입력 안정성 키"의 실물이다.
      [가변]  feed.prose == mentioned — 이번 턴 산문·입력에 이름이 등장한 것만.
              판정은 `select_mentioned` 와 **같은 부분 일치**(한국어 조사 때문).
      [사건]  전이 발화 줄(`<사건> …`). **1턴만** 살고 읽는 즉시 비워진다.

    ★옛 동작("유저 변수 전량")은 여기서 끝난다. 게이트가 없으면 선언 30개짜리 영지물이
      매턴 30줄을 Slot 29 에 실었고, 그건 재료가 아니라 장부였다(스펙 §3.5 "빈 자리 ①").
      기본값이 mentioned 라 선언을 그대로 둔 채널은 **줄이 준다** — 늘 보여야 할 값은
      `표시 항상`(feed.prose=always)으로 척추에 세운다.

    시스템 변수는 여전히 빠진다 — 바로 윗줄(`활력 | 평형`)이 이미 싣는다. 두 번 주면
    모델이 같은 수치를 두 사실로 읽는다(종전 판단 유지).
    """
    if not is_enabled():
        return ""
    # [2026-09-09 P12] append 기록 줄. **척추 밖·값 줄 밖**의 제 줄이다 — parts 에 섞으면
    #   값 예산(PROSE_FEED_MAX["values"])을 먹고 선언 순서가 두 층(변수·섹션)으로 갈린다.
    #   `<사건>` 과도 다른 줄이다: 저건 1턴만 사는 발화고 이건 지금 쌓여 있는 것의 마지막 줄이다.
    append_rows: List[str] = []
    try:
        import status_panel as _sp_ap
        append_rows = _sp_ap.append_feed_rows(channel_id)
    except Exception as _e_ap:
        logger.debug(f"[CustomVars] append 급식 skip: {_e_ap}")
    # [2026-09-13 P14] 핸드아웃은 선언과 무관하다 — 선언 0 인 채널에도 편지는 온다.
    #   그래서 소비(=소멸)를 조기 반환 **앞**에서 한다: 안 그러면 선언 없는 채널에서
    #   큐가 영원히 안 비고, 다음 턴 편지가 옛 편지 뒤에 쌓인다.
    handouts = _take_handouts(channel_id)
    # [2026-09-13 P16] 지시도 선언과 무관하다 — `when` 이 읽기 전용 이름(기력·날씨)만 볼 수도
    #   있으니 선언 0 인 채널에도 지시는 선다. 그래서 조기 반환 **앞**에서 만든다.
    # [2026-09-24 감사] 행위자 전달 — 호출부(game_world.build_real_time_display)가 user_id 를
    #   넘겨야 다인 채널에서 맞는 PC 로 판정된다(안 넘기면 _default_actor).
    directive_rows = directive_feed_rows(channel_id, user_id)
    decl = get_declarations(channel_id)
    if not decl:
        _tail = []
        if append_rows:
            _tail.append(APPEND_FEED_LABEL + " " + " | ".join(append_rows))
        _tail += [DIRECTIVE_FEED_LABEL + " " + d for d in directive_rows]
        _tail += [HANDOUT_FEED_LABEL + "\n" + h for h in handouts]
        return "\n".join([PROSE_FEED_HEADER] + _tail) if _tail else ""
    vals = get_values(channel_id)
    now = _current_turn(channel_id)
    onstage_set = {str(n).strip() for n in (
        onstage if onstage is not None else _onstage_names(channel_id)) if str(n).strip()}
    blob = str(mentioned_text or "")
    low = blob.lower()

    spine: List[Tuple[str, bool]] = []      # (조각, 이번 턴 변화분)
    variable: List[Tuple[str, bool]] = []
    for name, spec in decl.items():
        if not isinstance(spec, dict):
            continue
        nm = str(name).strip()
        if not nm:
            continue
        if spec.get("system"):
            # [2026-09-06 P8c] 시스템 변수의 **PC 값**은 여전히 빠진다 — 바로 윗줄
            #   (`활력 | 평형`, game_world.build_real_time_display)이 이미 싣는다. 여기 실리는 건
            #   그 줄이 말하지 않는 것, 즉 **무대 위 인물 값**뿐이다(중복 0).
            if not spec.get("npc_enabled"):
                continue
            row = _feed_row(channel_id, nm, spec, vals, onstage_set)
            if row:
                spine.append((row, _changed_this_turn(vals.get(nm), now)))
            continue
        mode = feed_mode(spec)
        if mode == "never":
            continue
        if mode == "mentioned" and not (nm in blob or nm.lower() in low):
            continue
        row = _feed_row(channel_id, nm, spec, vals, onstage_set)
        if not row:
            continue
        changed = _changed_this_turn(vals.get(nm), now)
        (spine if mode == "always" else variable).append((row, changed))

    # --- 예산 고정: 값 줄 상한. 절단 순서 = always 절대 안 자름 → 변화분 → 나머지 ---
    try:
        cap_vals = int((getattr(config, "PROSE_FEED_MAX", {}) or {}).get("values", 12))
        cap_events = int((getattr(config, "PROSE_FEED_MAX", {}) or {}).get("events", 3))
    except (TypeError, ValueError, AttributeError):
        cap_vals, cap_events = 12, 3

    parts: List[str] = [r for r, _c in spine][:max(cap_vals, len(spine))]
    if len(parts) > cap_vals:
        # always 가 상한을 넘으면 그 안에서 변화분을 앞세운다(자르는 건 always 끼리뿐).
        parts = ([r for r, c in spine if c] + [r for r, c in spine if not c])[:cap_vals]
    room = cap_vals - len(parts)
    if room > 0 and variable:
        parts += ([r for r, c in variable if c] + [r for r, c in variable if not c])[:room]
    # [2026-09-13 P15] 탈락 관측 — "GM 이 모르는 핸드아웃"의 지표다. 임베드(플레이어 뷰)는
    #   6000자/10장이라 값을 다 싣는데 급식(모델 뷰)에서만 빠지면 화면엔 있고 GM 은 모르는
    #   값이 된다. 그 수를 **로그 1줄**로 낸다(문구 0·동작 0 — 관측만).
    _dropped_n = (len(spine) + len(variable)) - len(parts)
    if _dropped_n > 0:
        logger.info("[Feed] drop n=%d (값 %d/%d, 캡 %d)",
                    _dropped_n, len(parts), len(spine) + len(variable), cap_vals)

    events = [f"<사건> {e}" for e in _take_events(channel_id, cap_events)]
    if not parts and not events and not append_rows and not handouts and not directive_rows:
        return ""

    # 전량 캡은 **항목 단위**로 문다 — 문자 단위로 자르면 반쪽 수치("마나 8")가 남고,
    # 반쪽 수치는 없는 값보다 나쁘다(모델은 그걸 사실로 읽는다).
    body = ""
    _kept_n = 0
    for p in parts:
        cand = f"{body} | {p}" if body else p
        if len(cand) > PROSE_FEED_MAX:
            body = f"{body} …" if body else (p[:PROSE_FEED_MAX] + " …")
            break
        body = cand
        _kept_n += 1
    if _kept_n < len(parts):
        # 개수 캡과 **같은 축의 탈락**이다(문자 캡). 한 줄에 같은 이름표로 낸다 —
        # 두 관문이 각각 몇을 먹었는지 로그에서 갈라 보여야 캡을 고칠 수 있다.
        logger.info("[Feed] drop n=%d (문자 %d/%d)",
                    len(parts) - _kept_n, len(body), PROSE_FEED_MAX)

    lines = [PROSE_FEED_HEADER]
    if body:
        lines.append(body)
    if append_rows:
        lines.append(APPEND_FEED_LABEL + " " + " | ".join(append_rows))
    if events:
        lines.append(" | ".join(events))
    # 핸드아웃은 **맨 끝**에 제 줄로 — 값 예산(PROSE_FEED_MAX)을 안 먹고, 한 덩이라
    # `|` 로 이어 붙이지 않는다(편지는 값이 아니다).
    # 지시는 **제 줄**이다 — 값 예산도 `<사건>` 예산도 안 먹는다(제 예산 DIRECTIVE_FEED_MAX).
    for d in directive_rows:
        lines.append(DIRECTIVE_FEED_LABEL + " " + d)
    for h in handouts:
        lines.append(HANDOUT_FEED_LABEL + "\n" + h)
    return "\n".join(lines)


# =========================================================
# [v1] 헤더 자리표시자 — `[마나]` 치환
# =========================================================

_PLACEHOLDER_RE = re.compile(r"\[([^\[\]\n]{1,%d})\]" % NAME_MAX)

# [2026-09-07 P10] 시스템 이름표 — **코드가 소유한 읽기 전용 자리표시자**.
#   정의는 여기 한 곳뿐이다: 라우터 프롬프트가 모델에게 찍어 보내는 이름줄도 이 상수를
#   그대로 찍는다(두 곳에 적으면 프롬프트가 코드보다 늘 하루 늦는다).
#   ★`요일`이 없는 이유: 이 세계의 달력(`game_world.format_calendar`)은 360일/30일 달이고
#     요일 개념 자체가 없다. 원천 없는 이름을 이름표에 두면 그 자리는 영영 미해석으로
#     남고(원문 유지 계약), 유저는 "코드가 약속해 놓고 안 그린다"로 읽는다.
#   `날씨`는 world_state["weather"] 가 원천이다(game_world 가 매 시간대 굴린다).
SYSTEM_PLACEHOLDERS = ("시간", "날짜", "시간대", "위치", "인물", "턴", "날씨")

# `[MP.max]` — 선언 range 의 양 끝. 값이 아니라 **선언**을 읽으므로 콜도 저장도 없다.
# [2026-09-13 P17] `.남은`·`.퍼센트` — 같은 쌍의 줄어드는 쪽. 끝을 정하는 자리는
#   `value_bounds` 하나다(바 렌더는 여전히 표시층의 몫 — 여기선 수만 낸다).
_PROGRESS_SUFFIXES = ("남은", "퍼센트")
_RANGE_SUFFIXES = ("max", "min") + _PROGRESS_SUFFIXES
_SECTION_SEP = "/"


def system_placeholder_value(channel_id: str, name: Any) -> str:
    """시스템 이름표 하나 → 표시 문자열. 이름표 밖이면 "".

    원천은 P9 footer 와 **같은 함수들**이다 — 임베드 하단과 유저 템플릿이 다른 시각을
    말하는 일이 없도록(둘이 갈리면 어느 쪽이 진짜인지 물어볼 데가 없다).
    """
    key = str(name or "").strip()
    if key not in SYSTEM_PLACEHOLDERS:
        return ""
    try:
        import domain_manager as _dm
        world = _dm.get_world_state(channel_id) or {}
    except Exception as e:
        logger.debug(f"[CustomVars] system placeholder world skipped: {e}")
        world = {}
    if key == "인물":
        try:
            import status_panel as _sp
            return ", ".join(_sp._present_names(channel_id) or [])
        except Exception as e:
            logger.debug(f"[CustomVars] present names skipped: {e}")
            return ""
    try:
        import game_world as _gw
        _gw._init_clock(world)          # 읽기 전용 인메모리 백필 — status_panel 과 같은 경로
    except Exception as e:
        logger.debug(f"[CustomVars] clock init skipped: {e}")
        _gw = None
    if key == "시간":
        try:
            return f"{int(world.get('hour', 0) or 0):02d}:{int(world.get('minute', 0) or 0):02d}"
        except (TypeError, ValueError):
            return ""
    if key == "날짜":
        try:
            return str(_gw.format_calendar(world) or "") if _gw else ""
        except Exception:
            return ""
    if key == "시간대":
        return str(world.get("time_slot", "") or "")
    if key == "위치":
        return str(world.get("current_location") or world.get("location", "") or "")
    if key == "턴":
        try:
            return str(int(world.get("turn_index", 0) or 0))
        except (TypeError, ValueError):
            return "0"
    if key == "날씨":
        return str(world.get("weather", "") or "")
    return ""


def _split_suffix(token: str) -> Tuple[str, str]:
    """`MP.max` → ("MP", "max"). 접미가 없으면 ("MP", "")."""
    t = str(token or "").strip()
    if "." not in t:
        return t, ""
    base, _, suf = t.rpartition(".")
    suf = suf.strip().lower()
    return (base.strip(), suf) if base.strip() and suf in _RANGE_SUFFIXES else (t, "")


def _panel_field_value(channel_id: str, token: str) -> Optional[str]:
    """`섹션/필드` → 직전 저장 패널의 그 필드. 그런 필드가 없으면 None(원문 유지)."""
    t = str(token or "").strip()
    if _SECTION_SEP not in t:
        return None
    try:
        import status_panel as _sp
        fields = (_sp.get_saved_panel(channel_id) or {}).get("fields")
    except Exception as e:
        logger.debug(f"[CustomVars] panel field lookup skipped: {e}")
        return None
    if not isinstance(fields, dict):
        return None
    if t in fields:
        return str(fields[t] if fields[t] is not None else "")
    low = t.lower()
    for fk, fv in fields.items():
        if str(fk).strip().lower() == low:
            return str(fv if fv is not None else "")
    return None


def _record_field(channel_id: str, token: str,
                  decl: Dict[str, Any], vals: Dict[str, Any]) -> Optional[Tuple[Dict[str, Any], str, int]]:
    """`목록/항목/필드` → (선언, 필드명, 지금 값). 그런 필드가 없으면 None(원문 유지).

    [2026-09-13 P17] 표시 전용 읽기다 — 저장도 콜도 없다. 값 자리표시자가 스칼라만 가리키면
    레코드 목록의 기한은 영영 화면에 못 선다(그 수가 사는 곳이 레코드 안뿐이다).
    """
    parts = [x.strip() for x in str(token or "").split(_SECTION_SEP)]
    if len(parts) != 3 or not all(parts):
        return None
    lname = _resolve_name(parts[0], decl)
    if not lname:
        return None
    spec = decl.get(lname) or {}
    if str(spec.get("type")) != "list":
        return None
    flds = item_fields(spec)
    fld = parts[2] if parts[2] in flds else next(
        (f for f in flds if f.lower() == parts[2].lower()), "")
    if not fld:
        return None
    items = (vals.get(lname) or {}).get("value")
    items = items if isinstance(items, dict) else {}
    key = parts[1] if parts[1] in items else next(
        (k for k in items if str(k).strip().lower() == parts[1].lower()), "")
    if not key:
        return None
    try:
        return spec, fld, int((items.get(key) or {}).get(fld, 0))
    except (TypeError, ValueError, AttributeError):
        return None


def _progress_text(channel_id: str, spec: Dict[str, Any], raw: Any, suf: str) -> str:
    """`.남은` / `.퍼센트` 한 값. 끝이 없으면 빈 문자열(= 유저의 `empty` 표가 채운다)."""
    b = value_bounds(spec, raw)
    if b is None:
        return ""
    start, end = b
    try:
        v = int(raw)
    except (TypeError, ValueError):
        return ""
    if suf == "남은":
        return str(max(0, end - v))
    span = end - start
    if span <= 0:
        return "100"
    return str(int((v - start) * 100 // span))


def _field_progress_text(channel_id: str, spec: Dict[str, Any], fld: str,
                         cur: int, suf: str) -> str:
    """레코드 필드의 `.남은` / `.퍼센트`. 기한 필드면 **끝 − 날**(D-day)이다."""
    if is_deadline_field(fld):
        today = today_index(channel_id)
        if today is None:
            return ""
        if suf == "남은":
            return str(max(0, cur - today))
        return "100" if cur <= today else "0"
    try:
        flo, fhi = int(item_fields(spec)[fld][0]), int(item_fields(spec)[fld][1])
    except (TypeError, ValueError, IndexError, KeyError):
        return ""
    if suf == "남은":
        return str(max(0, fhi - cur))
    span = fhi - flo
    return "100" if span <= 0 else str(int((cur - flo) * 100 // span))


def _declared_text(channel_id: str, key: str, spec: Dict[str, Any],
                   vals: Dict[str, Any]) -> str:
    """선언 변수 하나의 표시 문자열. **빈 것은 빈 문자열로** 돌려준다 —
    `empty` 표가 채울 자리를 코드가 먼저 `—` 로 메워 버리면 유저 표기가 사라진다."""
    raw = (vals.get(key) or {}).get("value", spec.get("init"))
    vtype = str(spec.get("type", "gauge"))
    if spec.get("per_actor"):
        return format_value(spec, get_system_value(channel_id, key))
    if vtype == "list":
        items = raw if isinstance(raw, dict) else {}
        return ", ".join(_item_text(spec, k, v, channel_id) for k, v in list(items.items())[:list_max(spec)])
    if str(spec.get("scope")) == "npc":
        per = raw if isinstance(raw, dict) else {}
        return ", ".join(f"{nm} {format_value(spec, v)}" for nm, v in list(per.items())[:3])
    return format_value(spec, raw)


def resolve_placeholder(channel_id: str, token: str,
                        decl: Optional[Dict[str, Any]] = None,
                        vals: Optional[Dict[str, Any]] = None) -> Optional[str]:
    """자리표시자 하나 → 표시 문자열. **해석할 수 없으면 None** (= 원문 유지).

    순서: 선언 변수 → `이름.max/.min`(선언 range) → 시스템 이름표 → `섹션/필드`.
    """
    tok = str(token or "").strip()
    if not tok:
        return None
    if decl is None:
        decl = get_declarations(channel_id) or {}
    if vals is None:
        vals = get_values(channel_id) or {}

    key = _resolve_name(tok, decl)
    if key:
        return _declared_text(channel_id, key, decl.get(key) or {}, vals)

    rf = _record_field(channel_id, tok, decl, vals)
    if rf is not None:
        return str(rf[2])

    base, suf = _split_suffix(tok)
    if suf:
        bkey = _resolve_name(base, decl)
        if bkey:
            if suf in _PROGRESS_SUFFIXES:
                _sp_b = decl.get(bkey) or {}
                _raw_b = (vals.get(bkey) or {}).get("value", _sp_b.get("init"))
                # [2026-09-24 감사] per_actor(기력·평형)의 저장값은 {user_id: int} dict 라
                #   첫 쓰기 이후 _progress_text 의 int() 가 실패해 `[기력.퍼센트]` 가 "—" 였다.
                #   _declared_text 와 같은 문(get_system_value, 행위자 없으면 _default_actor)으로 읽는다.
                if _sp_b.get("per_actor"):
                    _raw_b = get_system_value(channel_id, bkey)
                return _progress_text(channel_id, _sp_b, _raw_b, suf)
            rng = (decl.get(bkey) or {}).get("range")
            if isinstance(rng, (list, tuple)) and len(rng) == 2:
                try:
                    return str(int(rng[1 if suf == "max" else 0]))
                except (TypeError, ValueError):
                    return None
            return None
        rf2 = _record_field(channel_id, base, decl, vals)
        if rf2 is not None:
            if suf in _PROGRESS_SUFFIXES:
                return _field_progress_text(channel_id, rf2[0], rf2[1], rf2[2], suf)
            try:
                _fr = item_fields(rf2[0])[rf2[1]]
                return str(int(_fr[1 if suf == "max" else 0]))
            except (TypeError, ValueError, IndexError, KeyError):
                return None

    if tok in SYSTEM_PLACEHOLDERS:
        return system_placeholder_value(channel_id, tok)

    return _panel_field_value(channel_id, tok)


def placeholder_names(text: Any) -> List[str]:
    """문자열 안의 `[…]` 이름들(중복 제거, 등장 순)."""
    out: List[str] = []
    for m in _PLACEHOLDER_RE.finditer(str(text or "")):
        nm = m.group(1).strip()
        if nm and nm not in out:
            out.append(nm)
    return out


def unresolved_placeholders(channel_id: str, text: Any,
                            extra_names: Any = (),
                            extra_sections: Any = ()) -> List[str]:
    """템플릿의 자리표시자 중 **문법 밖**인 이름. 라우터 관문이 이걸로 이분한다.

    안: 선언 변수 ∪ `extra_names`(같은 배치 신규 이름) ∪ 시스템 이름표 ∪
        `이름.max/.min` ∪ `섹션/필드`(등록 섹션 ∪ `extra_sections`).
    """
    decl = get_declarations(channel_id) or {}
    extra = {str(n).strip() for n in (extra_names or ()) if str(n).strip()}
    extra_low = {n.lower() for n in extra}
    try:
        import status_panel as _sp
        secs = set(_sp.list_panel_sections(channel_id) or {})
    except Exception as e:
        logger.debug(f"[CustomVars] section list skipped: {e}")
        secs = set()
    secs |= {str(n).strip() for n in (extra_sections or ()) if str(n).strip()}
    secs |= extra
    secs_low = {s.lower() for s in secs}

    bad: List[str] = []
    for tok in placeholder_names(text):
        if _resolve_name(tok, decl) or tok in extra or tok.lower() in extra_low:
            continue
        if tok in SYSTEM_PLACEHOLDERS:
            continue
        base, suf = _split_suffix(tok)
        if suf:
            bk = _resolve_name(base, decl)
            if bk and suf in _PROGRESS_SUFFIXES:
                # [2026-09-13 P17] 끝이 없는 값(counter·범위 없음)엔 "남은"이 없다.
                #   원문 유지로 흘리면 화면에 `[걸음.남은]` 이 그대로 박히므로 여기서 갈라
                #   낸다(라우터 관문이 ↓ 로 접는다).
                if value_bounds(decl.get(bk) or {}, None) is None:
                    bad.append(tok)
                continue
            if bk or base in extra or base.lower() in extra_low:
                continue
            if _record_field(channel_id, base, decl, get_values(channel_id) or {}) is not None:
                continue
        if _record_field(channel_id, tok, decl, get_values(channel_id) or {}) is not None:
            continue
        if _SECTION_SEP in tok:
            sec = tok.split(_SECTION_SEP, 1)[0].strip()
            if sec in secs or sec.lower() in secs_low:
                continue
        bad.append(tok)
    return bad


def render_placeholders(channel_id: str, template: str,
                        empty: Optional[Dict[str, Any]] = None) -> str:
    """유저 형식 문자열의 `[변수명]` 을 현재값으로 치환. **선언 안 된 자리표시자는 원문 그대로.**

    ★그대로 두는 것이 계약이다 — 헤더 형식엔 `[전투]` 같은 유저의 장식 대괄호가 섞이고,
      코드가 그걸 빈칸으로 지워 버리면 유저 저작이 조용히 훼손된다.
    표시 계층 전용(저장·검수·히스토리 무접촉). format 선언이 있으면 그것이 이긴다.
    """
    text = str(template or "")
    if not text or not is_enabled():
        return text
    decl = get_declarations(channel_id) or {}
    vals = get_values(channel_id) or {}
    blanks = empty if isinstance(empty, dict) else {}

    def _sub(m: "re.Match") -> str:
        tok = m.group(1).strip()
        out = resolve_placeholder(channel_id, tok, decl, vals)
        if out is None:                       # 08-18 계약: 미해석은 원문 그대로
            return m.group(0)
        if str(out).strip():
            return str(out)
        # [2026-09-07 P10] 빈 값의 표기는 **유저가 정한다**(형식 항목의 `empty` 표).
        #   표에 없으면 종전 그대로 `—`. 유저 노출 문법은 늘지 않는다.
        base, _suf = _split_suffix(tok)
        for k in (tok, base):
            if k in blanks and str(blanks[k]).strip():
                return str(blanks[k])
        return "—"

    return _PLACEHOLDER_RE.sub(_sub, text)
