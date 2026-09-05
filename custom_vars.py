# -*- coding: utf-8 -*-
"""
Custom Vars — 대형식화(선언형 변수 레지스트리) v0  [2026-08-18 Phase 1]

정본 스펙: `파티쳇수정/대형식화_스펙_v0_2026-08-18.md`

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
  표시   += **헤더 자리표시자**(`[마나]` 치환 — game_world.build_status_header)
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

MAX_VARS = 12               # 채널당 선언 상한. mentions 게이트가 있어도 목록 자체가 프롬프트에 실린다.
NAME_MAX = 16               # 표시명 겸 어휘 게이트 키. 길면 mentions 게이트가 무뎌진다.
RULE_MAX = 160              # rule 은 추출 콜 급식분 — 문단이 아니라 한 줄.
EVIDENCE_MAX = 120
SPAN_MAX = 1_000_000        # range 폭 상한(정수 오염 방지)

# --- v1 (Phase 2) 코드 상수 ---
MAX_STAGES = 8              # enum 단계 수 상한. 단계가 많아지면 그건 게이지지 단계가 아니다.
STAGE_NAME_MAX = 12
MAX_LIST_ITEMS = 8          # list 항목 수 상한(스펙 "8 근방"). 넘으면 새 항목이 거절된다.
ITEM_NAME_MAX = 20
DEFAULT_MAX_STEP = 1        # enum 상승은 한 번에 1단계 — set_drive_gated 의 DRIVE_RISE_MAX_STEP 계보.
DEFAULT_ITEM_GOAL = 0       # stock 형에서 목표를 안 준 항목 = 목표 없음(0 = 미설정)
FORMAT_MAX = 40             # `{v} 골드` 정도. 표시 문자열이 문단이 되면 패널이 무너진다.
DELTA_CAP_MAX = SPAN_MAX    # 비대칭 캡 자체의 상한(정수 오염 방지)
MAX_NPC_VALUES = 12         # NPC 스코프 변수 하나가 인물별 값을 몇 명까지 들 수 있나

# --- 산문(렌더) 급식 캡 — 패널은 장부고 산문은 재료다. 여기선 짧은 쪽이 정답. ---
PROSE_FEED_MAX = 400        # 블록 본문 전량 캡(문자). Slot 29 는 한 번 읽는 지면이지 장부가 아니다.
PROSE_LIST_ITEMS = 4        # list 요약에 실리는 항목 수 — 패널(MAX_LIST_ITEMS=8)의 절반.
PROSE_NPC_MAX = 4           # 온스테이지라도 한 변수가 인물 넷을 넘으면 그건 인물이 아니라 군중이다.

VAR_TYPES = ("gauge", "counter", "enum", "list")
VAR_SCOPES = ("global", "pc", "npc")

# list 항목 모드. progress = 항목 하나가 0~100 진행률 / stock = 항목 하나가 현재/목표 재고.
ITEM_MODES = ("progress", "stock")
PROGRESS_RANGE = [0, 100]
STOCK_RANGE = [0, 9999]

# 코드 기관이 여전히 소유한 이름 — 레지스트리가 같은 이름을 만들면 패널에 두 번 그려진다.
# (2026-08-18 Phase 2.5: 기력은 여기서 빠져 **시스템 선언**으로 갔다. 평형은 잔류 —
#  회복 로직·트라우마 dwell·prime/sync 2회 실행이 얽혀 있어 파생·쿨다운(v2)이 먼저다.)
RESERVED_NAMES = ("평형", "composure")

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
    },
}

# 유저가 `!출력룰` 로 고칠 수 있는 칸. 나머지는 코드 소유(모양 잠금).
SYSTEM_EDITABLE = ("rule", "max_gain", "max_loss", "format")

# 같은 것을 가리키는 다른 표기 — 유저가 `vigor`/`활력`로 적어도 기력 개정으로 흡수한다
# (새 변수로 만들어지면 패널에 두 번 그려진다 — 예약 이름 규율과 같은 이유).
SYSTEM_ALIASES = {"vigor": "기력", "활력": "기력", "기력": "기력"}


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
    "\n**목록형(list)** — 항목마다 수치가 붙는 것\n"
    "`재료 | 목록 현재/목표 | global | 채집하면 늘고 조합하면 준다`\n"
    "`연구중 | 목록 진행%% | global | 작업하면 오른다`\n"
    "- 항목은 %d개까지. 항목 신설·삭제도 근거가 있어야 합니다.\n"
    "\n**표시 형식** — `표시: {v} 골드` 를 칸에 넣으면 패널·헤더가 그대로 씁니다.\n"
    "**비대칭 캡** — `상승 5 하강 20` 을 칸에 넣으면 한 턴에 그만큼까지만 움직입니다.\n"
    "**헤더** — `!출력룰 추가 헤더 잔고 [빚] / 평판 [평판]` 로 상단 줄에 값을 꽂습니다.\n"
    "\n**시스템 변수** — `기력`은 코드가 심어 둔 변수라 지울 수 없습니다. 대신 **규칙과 캡은 고칠 수 있습니다**:\n"
    "`!출력룰 수정 변수 기력 | 상승 5 하강 7 | 능력을 쓰면 크게 깎이고 쉬면 찬다`\n"
    "(범위·타입·스코프는 코드 소유라 바뀌지 않습니다.)"
) % (NAME_MAX, RULE_MAX, MAX_VARS, MAX_STAGES, STAGE_NAME_MAX, MAX_LIST_ITEMS)


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
    try:
        decl = (domain_manager.get_world_state(channel_id) or {}).get(KEY_DECL)
    except Exception as e:
        logger.debug("[CustomVar] declaration read skipped: %s", e)
        decl = None
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
    ws = domain_manager.get_world_state(channel_id) or {}
    ws[KEY_DECL] = _reduce_system(decl)
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
    stage_done = fmt_done = cap_done = mode_done = False

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
        default = getattr(_npm, "SOURCE_SESSION", "session")
        npcs = _npm.get_npcs(channel_id) or {}
    except Exception as e:
        logger.debug("[CustomVar] NPC 출처 게이트 조회 실패: %s", e)
        return []
    out = []
    for nm, rec in (npcs or {}).items():
        if not isinstance(rec, dict):
            continue
        if str(rec.get("source", default) or default).lower() in allowed:
            out.append(str(nm))
    return out


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


def _item_text(spec: Dict[str, Any], item: str, rec: Any) -> str:
    """list 항목 한 줄. progress = `이름 40%` / stock = `이름 3/5`(목표 없으면 `이름 3`)."""
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
    if spec.get("stages") and vtype in ("gauge", "counter"):
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
        return {
            "name": name, "type": "list", "scope": scope, "rule": rule,
            "item_mode": mode, "item_range": irng,
            **({"max_gain": gain} if gain else {}),
            **({"max_loss": loss} if loss else {}),
            **({"format": fmt} if fmt else {}),
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

    return {
        "name": name,
        "type": vtype,
        "range": [lo, hi],
        "init": init,
        "scope": scope,
        "rule": rule,
        **({"max_gain": gain} if gain else {}),
        **({"max_loss": loss} if loss else {}),
        **({"format": fmt} if fmt else {}),
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
        for k, rec in list(items.items())[:MAX_LIST_ITEMS]:
            n = rec.get("n") if isinstance(rec, dict) else rec
            try:
                n = int(n)
            except (TypeError, ValueError):
                continue
            goal = rec.get("goal", 0) if isinstance(rec, dict) else 0
            try:
                goal = int(goal or 0)
            except (TypeError, ValueError):
                goal = 0
            clean[str(k)] = {"n": max(int(lo), min(int(hi), n)), "goal": goal}
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
            shape = f"{spec.get('item_mode', 'stock')} {lo}-{hi}, 항목 {len(cur or {})}/{MAX_LIST_ITEMS}"
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
        # [Phase 2.5] **mentions 면제**(always_feed). 능력을 쓴 장면에 "기력"이라는 낱말이
        #   없어도 소모는 일어난다 — 어휘 게이트는 유저 변수의 프롬프트 비대를 막는 장치지
        #   상시 자원의 관측을 끊는 장치가 아니다. 동결된(토글 off) 시스템 변수는 급식 안 한다.
        if spec.get("always_feed"):
            if spec.get("system") and not _system_active(channel_id, spec):
                continue
        elif not (nm in blob or nm.lower() in low):
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
        # [v1] 목록형은 **이미 있는 항목**을 알아야 신설과 이동을 구분한다.
        elif vtype == "list":
            entry["item_mode"] = str(spec.get("item_mode", "stock"))
            entry["item_range"] = list(spec.get("item_range") or STOCK_RANGE)[:2]
            entry["items"] = [
                _item_text(spec, k, v) for k, v in list((cur or {}).items())[:MAX_LIST_ITEMS]
            ]
        # [v1] NPC 스코프는 **값을 가질 자격이 있는 인물**을 알아야 헛신고가 줄어든다.
        if entry["scope"] == "npc":
            entry["npcs"] = allowed_npc_names(channel_id)[:MAX_NPC_VALUES]
            entry["current_by_npc"] = {
                k: (format_value(spec, v) if vtype != "enum" else v)
                for k, v in list((cur or {}).items())[:MAX_NPC_VALUES]
            } if isinstance(cur, dict) else {}
        out.append(entry)
    return out


# =========================================================
# 델타 적용 (집행)
# =========================================================

def apply_deltas(channel_id: str, deltas: Any, turn: Optional[int] = None,
                 actor: str = "") -> List[Dict[str, Any]]:
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
        vtype = str(spec.get("type", "gauge"))
        try:
            if vtype == "enum":
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
        who = str(item.get("actor") or actor or _default_actor(channel_id) or "").strip()
        if not who:
            logger.info("[CustomVar] %s 대상 참가자 미상 → 폐기", name)
            return None
        per = dict(entry.get("value") or {}) if isinstance(entry.get("value"), dict) else {}
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
        return {"name": name, "actor": who, "from": old, "to": new,
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
      - 항목 수 캡(MAX_LIST_ITEMS)을 넘는 신설은 거절 + 로그 1줄.
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
        if len(items) >= MAX_LIST_ITEMS:
            logger.info("[CustomVar] %s 항목 상한 %d 도달 → 신설 거부 (%s)", name, MAX_LIST_ITEMS, item_name)
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
        items[item_name] = {"n": start, "goal": goal}
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
    items[key] = {"n": new, "goal": goal}
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
            lines = [_item_text(spec, k, v) for k, v in list(items.items())[:MAX_LIST_ITEMS]]
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

        if vtype == "enum":
            if not val:
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


def build_prose_feed(channel_id: str, onstage: Optional[List[str]] = None) -> str:
    """선언 변수 현재값 → 산문 재료 블록(머리 1절 + 값 한 줄). 없으면 "".

    표기 문법은 패널과 같은 소스(format_value·_item_text)를 쓴다 — 두 표시가 어긋나면
    유저가 본 값과 모델이 본 값이 달라진다.
      gauge/counter → `이름 80/100` · enum → `이름 안면` ·
      list → `이름 항목 3/5, 항목 40%`(항목 캡) · npc → `이름 인물 값`(온스테이지분만)
    최근 변화 도장(`t12 +5`)은 **싣지 않는다** — 변화량은 패널의 몫이고, 산문에 주면
    "5 줄었다"를 옮겨 적을 재료가 된다(자판기화 위험, 스펙 §8).
    """
    if not is_enabled():
        return ""
    decl = get_declarations(channel_id)
    if not decl:
        return ""
    vals = get_values(channel_id)
    onstage_set = {str(n).strip() for n in (
        onstage if onstage is not None else _onstage_names(channel_id)) if str(n).strip()}

    parts: List[str] = []
    for name, spec in decl.items():
        if not isinstance(spec, dict):
            continue
        nm = str(name).strip()
        if not nm:
            continue
        # 시스템 변수는 바로 윗줄(`활력 | 평형 | Doom`)이 이미 싣는다 — 두 번 주면
        # 모델이 같은 수치를 두 사실로 읽는다.
        if spec.get("system"):
            continue
        raw = (vals.get(name) or {}).get("value", spec.get("init"))
        vtype = str(spec.get("type", "gauge"))

        # NPC 스코프 — 무대 위 인물분만. 무대에 아무도 없으면 이 변수는 통째로 침묵.
        if str(spec.get("scope")) == "npc":
            per = raw if isinstance(raw, dict) else {}
            shown = [(k, v) for k, v in per.items()
                     if str(k).strip() in onstage_set][:PROSE_NPC_MAX]
            if not shown:
                continue
            parts.append(f"{nm} " + ", ".join(f"{k} {format_value(spec, v)}" for k, v in shown))
            continue

        if vtype == "list":
            items = raw if isinstance(raw, dict) else {}
            if not items:
                continue
            parts.append(f"{nm} " + ", ".join(
                _item_text(spec, k, v) for k, v in list(items.items())[:PROSE_LIST_ITEMS]))
            continue

        if vtype == "enum":
            if not raw:
                continue
        elif not isinstance(raw, (int, float)):
            continue
        parts.append(f"{nm} {format_value(spec, raw)}")

    if not parts:
        return ""
    # 전량 캡은 **항목 단위**로 문다 — 문자 단위로 자르면 반쪽 수치("마나 8")가 남고,
    # 반쪽 수치는 없는 값보다 나쁘다(모델은 그걸 사실로 읽는다).
    body = ""
    for p in parts:
        cand = f"{body} | {p}" if body else p
        if len(cand) > PROSE_FEED_MAX:
            body = f"{body} …" if body else (p[:PROSE_FEED_MAX] + " …")
            break
        body = cand
    return f"{PROSE_FEED_HEADER}\n{body}"


# =========================================================
# [v1] 헤더 자리표시자 — `[마나]` 치환
# =========================================================

_PLACEHOLDER_RE = re.compile(r"\[([^\[\]\n]{1,%d})\]" % NAME_MAX)


def render_placeholders(channel_id: str, template: str) -> str:
    """유저 형식 문자열의 `[변수명]` 을 현재값으로 치환. **선언 안 된 자리표시자는 원문 그대로.**

    ★그대로 두는 것이 계약이다 — 헤더 형식엔 `[전투]` 같은 유저의 장식 대괄호가 섞이고,
      코드가 그걸 빈칸으로 지워 버리면 유저 저작이 조용히 훼손된다.
    표시 계층 전용(저장·검수·히스토리 무접촉). format 선언이 있으면 그것이 이긴다.
    """
    text = str(template or "")
    if not text or not is_enabled():
        return text
    decl = get_declarations(channel_id)
    if not decl:
        return text
    vals = get_values(channel_id)

    def _sub(m: "re.Match") -> str:
        key = _resolve_name(m.group(1), decl)
        if not key:
            return m.group(0)
        spec = decl.get(key) or {}
        raw = (vals.get(key) or {}).get("value", spec.get("init"))
        vtype = str(spec.get("type", "gauge"))
        # per_actor(시스템) 변수는 헤더에 actor 문맥이 없다 — 기본 참가자로 읽는다.
        if spec.get("per_actor"):
            return format_value(spec, get_system_value(channel_id, key))
        if vtype == "list":
            items = raw if isinstance(raw, dict) else {}
            return ", ".join(_item_text(spec, k, v) for k, v in list(items.items())[:MAX_LIST_ITEMS]) or "—"
        if str(spec.get("scope")) == "npc":
            per = raw if isinstance(raw, dict) else {}
            return ", ".join(f"{nm} {format_value(spec, v)}"
                             for nm, v in list(per.items())[:3]) or "—"
        return format_value(spec, raw)

    return _PLACEHOLDER_RE.sub(_sub, text)
