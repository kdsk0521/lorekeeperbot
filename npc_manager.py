"""
Lorekeeper TRPG Bot - NPC Manager
NPC의 완벽한 관리를 담당하는 비즈니스 로직 모듈.

책임:
- 로어 NPC / 수동 추가 NPC / AI 생성 NPC 구분 관리
- NPC 이름 변경 추적 (정체 발각)
- PC와의 관계(태도) 관리
- NPC 추출 및 등록

domain_manager.py는 저장소 역할만 담당.
"""

import re
import time
import random
import logging
from typing import Dict, Any, Optional, List, Tuple
import difflib
import config
import domain_manager
from npc_profile_harness import extract_static_traits

# =========================================================
# NPC SOURCE TYPES (출처 구분)
# =========================================================
# ★설계 의도(레티어스 2026-07-28) — 이 3종 구분을 통합하려 들지 말 것.
#   lore/manual = 사람이 쓴 확정 데이터 → **고장 확률 거의 0**. 손댈 건 "보조가 붙었나"뿐.
#   session/ai_generated = 플레이 중 모델 생성 → **고장 확률 높음**. 그래서 별도 저장 +
#     관찰 성장 + 증류 재작성이라는 전용 기관을 얹고, 거기에 앞 둘용 보조도 함께 물렸다.
#   점검 축도 다르다: 안정 2종은 "붙어야 할 게 붙었나", 세션 NPC는 "잃는 게 없나".
SOURCE_LORE = "lore"              # 로어 분석으로 추출된 NPC
SOURCE_MANUAL = "manual"          # !npc추가 등 수동 등록
SOURCE_AI_GENERATED = "ai_generated"  # 세션 중 AI가 생성한 NPC (register_ai_npc 경로)
# [2026-07-28] "session"은 상수가 없는데 **실제로 가장 많이 쓰이던 값**이었다 —
#   쓰기 4곳(command_handler 툴콜 등록, npc_manager 개명, orchestration 2곳)이 리터럴로 넣고
#   있었는데 VALID_SOURCES엔 없어서, get_npcs_by_source(AI_GENERATED)로는 안 잡혔다.
#   데이터 마이그레이션 없이 정식 값으로 승격한다(기존 DB 그대로 유효).
SOURCE_SESSION = "session"        # 플레이 중 자동 등록 (AI_GENERATED와 동급 — 둘 다 세션 파생)

VALID_SOURCES = {SOURCE_LORE, SOURCE_MANUAL, SOURCE_AI_GENERATED, SOURCE_SESSION}
# 시트가 동결되는(자동 증류가 덮지 않는) 출처 — lore/manual 판정의 단일 출처
FROZEN_SOURCES = (SOURCE_LORE, SOURCE_MANUAL)

# 태도 레벨 정의 (0-4 scale for gating distance calculation)
ATTITUDE_LEVELS = {
    "hostile": 0,
    "unfriendly": 1,
    "neutral": 2,
    "friendly": 3,
    "loyal": 4,
    "devoted": 5,
}

logger = logging.getLogger(__name__)

# =========================================================
# PEPLAU PHASE VALIDATION
# =========================================================
PEPLAU_ORDER = ['orientation', 'identification', 'exploitation', 'resolution']


# ⛔[2026-07-28 삭제] validate_peplau_transition — 호출처 0.
#   단계 건너뛰기 방지는 프롬프트 지시(theoria "cannot skip stages")가 담당 중.
#   PEPLAU_ORDER 상수는 아래에 남긴다(단계 이름 자체는 여전히 참조 가치).


# =========================================================
# NPC CRUD operations (Wraps domain_manager for now)
# =========================================================

def get_npcs(channel_id: str) -> Dict[str, Dict[str, Any]]:
    return domain_manager.get_npcs(channel_id)

def get_npc(channel_id: str, name: str) -> Optional[Dict[str, Any]]:
    return domain_manager.get_npc(channel_id, name)


def get_npc_current_location(channel_id: str, npc_name: str, data: dict = None) -> str:
    """NPC의 **현재 위치**. world_tree가 단일 진실, 시트 값은 폴백.

    [2026-07-28 결정(레티어스): "로케이션은 월드트리를 주축으로"]
    그동안 같은 이름의 두 시스템이 동기화 없이 병존했다:
      · `npc_data["location"]` — 등록 시점 1회 추출, **갱신 로직 전무**(시간이 갈수록 stale)
      · `world_tree`          — set/get/remove로 실시간 추적하는 그래프 (이쪽이 설계 의도)
    프로필·로스터가 전자를 표시하는 바람에, 인물이 움직여도 표시는 등록 시점에 머물렀다.
    이제 이 함수 하나가 우선순위를 정한다: **world_tree → 없으면 시트의 거처 → 빈 문자열.**
    """
    if not npc_name:
        return ""
    try:
        import world_tree
        _loc = world_tree.get_npc_location(channel_id, npc_name)
        if _loc:
            return str(_loc)
    except Exception as _e:
        logger.debug(f"[NPC Location] world_tree 조회 skip: {_e}")
    if data is None:
        data = get_npc(channel_id, npc_name) or {}
    return str((data or {}).get("location", "") or "")


def get_npc_static_traits(channel_id: str, npc_name: str) -> dict:
    """NPC의 정적 심리 특성을 반환. 없으면 빈 dict."""
    npc = get_npc(channel_id, npc_name)
    if not npc:
        return {}
    return npc.get("static_traits", {})


# =========================================================
# P5: 프로필-서사 분리 (Renderer strip)
# =========================================================
# Renderer에는 관찰 가능 데이터만 전달. Flash에는 전체.
RENDERER_STRIP_KEYS = {
    "hidden_motivation", "secret_knowledge", "true_identity",
    "betrayal_plan", "inner_conflict", "secret", "secrets",
    "hidden_agenda", "deception_plan",
}

def get_npc_context_for_renderer(channel_id: str, npc_name: str) -> dict:
    """Renderer에는 행동 관찰 가능 데이터만 전달.
    Flash(Theoria)에는 전체 프로필 전달.
    코드가 데이터를 물리적으로 빼면 LLM이 쓰고 싶어도 못 씀."""
    npc = get_npc(channel_id, npc_name)
    if not npc:
        return {}

    # Description 내부의 비밀 섹션도 제거
    result = {}
    for k, v in npc.items():
        if k in RENDERER_STRIP_KEYS:
            continue
        result[k] = v

    # Description text에서 [Secret]/[Hidden] 섹션 제거
    desc = result.get("description", "")
    if desc:
        result["description"] = strip_hidden_markers(desc)

    return result


# [2026-08-17] 마커 은닉 제거를 상수+함수로 승격. 구 코드는 이 정규식이 위 함수 안에
#   리터럴로 있었고, 시트를 읽는 두 번째 소비자(속마음 시트 요지)가 생기면서 **같은
#   판단이 두 벌**이 될 자리였다. 규칙을 베끼는 순간 한쪽만 고쳐지는 날이 온다.
_HIDDEN_MARKER_RE = re.compile(
    r'(?:\[(?:Secret|Hidden|비밀|숨겨진)[^\]]*\])[^\[]*', re.IGNORECASE)


def strip_hidden_markers(desc: str) -> str:
    """`[Secret]`/`[Hidden]`/`[비밀]`/`[숨겨진]` 마커와 그 뒤 조각을 제거."""
    return _HIDDEN_MARKER_RE.sub('', str(desc or "")).strip()


def migrate_npc_fields(channel_id: str) -> int:
    """기존 세션의 NPC 데이터를 마이그레이션: desc→description 통일 + 구조화 필드 추출."""
    npcs = get_npcs(channel_id)
    if not npcs:
        return 0
    migrated = 0
    for name, data in npcs.items():
        changed = False
        # desc → description 통일
        if "desc" in data and "description" not in data:
            data["description"] = data.pop("desc")
            changed = True
        # 구조화 필드 추출 (없는 경우만)
        desc_text = data.get("description", "")
        if desc_text and len(desc_text) > 200:
            extracted = _extract_structured_fields(desc_text)
            for key, val in extracted.items():
                if key not in data or not data[key]:
                    data[key] = val
                    changed = True
        # [2026-07-28] static_traits 백필 — 구 코드는 구조화 필드만 복구하고
        #   static_traits는 손대지 않은 채 domain_manager.update_npc를 직접 불렀다.
        #   그 결과 "치료제"인 이 함수가 정작 static_traits는 영영 못 고쳤고,
        #   그에 의존하는 기능(npc_autonomous 등)이 구 NPC에서 계속 비활성이었다.
        if desc_text and len(desc_text) >= 100 and not data.get("static_traits"):
            _st = extract_static_traits(name, desc_text)
            if _st:
                data["static_traits"] = _st
                changed = True
        if changed:
            domain_manager.update_npc(channel_id, name, data)
            migrated += 1
    if migrated:
        logger.info(f"[NPC Migration] {channel_id}: {migrated}/{len(npcs)} NPCs migrated")
    return migrated

def _clean_markdown(s: str) -> str:
    """마크다운 아티팩트(**등) 제거."""
    return re.sub(r'\*{1,2}', '', s).strip().strip('"').strip()


# [2026-09-02] 라벨 값 추출 단일 관문 — 구 정규식 3병 수리.
#   병1 `[:\s]+` = **콜론 없이 공백만으로도** 매칭 → 헤더 줄 `## 6. Speech Style & Tone`이
#        값 줄로 오인돼 `tone="& Tone"`이 저장됐다. role도 같은 형태였다.
#   병2 role에는 라벨 볼드 허용이 없어 `**Occupation**: Physician`이 **통째로 유실**됐다
#        (tone엔 있었다 = 자매 자리 소급 누락).
#   병3 두 벌의 정규식이 같은 판단을 따로 했다 → 한쪽만 고쳐지는 자리.
#   ⚠ `tone`은 domain_manager._PRESERVE_KEYS라 오염 값이 **재등록으로 안 지워진다**.
#      마이그레이션은 돌리지 않기로 함(레티어스 09-02) — 신규 등록만 깨끗해진다.
_HEADER_LN = re.compile(r'^\s*#')


def _label_value(desc: str, label_re: str) -> str:
    r"""`라벨: 값` 한 줄에서 값만. 2단 판정.

    ① 헤더가 아닌 줄의 `라벨: 값` (콜론 **필수**, 라벨 볼드 허용)
    ② 없으면 **헤더 이름이 라벨 그 자체**인 섹션의 첫 본문 줄

    ②를 남기는 이유: `### Tone\n무뚝뚝하다.` 형 시트가 실제로 있고 그건 옳은 추출이었다.
    ②는 헤더 이름이 라벨과 **같을 때만** 성립한다 — 부분일치를 허용하면 병1이 그대로 돌아온다
    (`### 6. Speech Style & Tone`은 라벨이 아니라 라벨을 **포함한** 이름이다).
    """
    if not desc:
        return ""
    lines = desc.splitlines()
    pat = re.compile(r'^[-*>\s]*\**\s*(?:' + label_re + r')\s*\**\s*[:：]\s*(.+)$', re.I)
    for ln in lines:
        if _HEADER_LN.match(ln):
            continue
        m = pat.match(ln.strip())
        if m:
            return _clean_markdown(m.group(1))
    head = re.compile(r'^\s*#{1,4}\s*(?:\d+[.)]\s*)?(?:' + label_re + r')\s*$', re.I)
    for i, ln in enumerate(lines):
        if head.match(ln):
            for nxt in lines[i + 1:]:
                if nxt.strip() and not _HEADER_LN.match(nxt):
                    return _clean_markdown(nxt.strip())
            break
    return ""


_ROLE_LABELS = r'Rank/Role|Occupation'
_TONE_LABELS = r'Tone|Speaking\s*Style|Speech\s*Style|말투|어조'


def _extract_structured_fields(desc: str) -> Dict[str, str]:
    """NPC 프로필 텍스트에서 구조화 필드(role, location, tone, personality) 자동 추출."""
    fields = {}
    if not desc or len(desc) < 50:
        return fields

    # Role (예: "- Rank/Role: Emergency physician / Sharehouse resident (Room 2)")
    _role = _label_value(desc, _ROLE_LABELS)   # [1M remap] 필드캡 제거(시트 5k자, 한 줄이라 자연 바운드)
    if _role:
        fields["role"] = _role

    # Location — [2026-07-28 재정의] **현재 위치의 진실은 world_tree**다(레티어스 결정).
    #   여기서 뽑는 값은 "시트에 적힌 거처/소속 장소" = world_tree에 기록이 아직 없을 때의
    #   표시용 폴백일 뿐이다. get_npc_current_location이 우선순위를 관리한다.
    #   구 코드는 `Dungeon N|Sunset Villa|Sage's Chamber` 같은 **특정 캠페인 지명 하드코딩**이라
    #   새 세계관에선 아무것도 못 잡았다 → 일반 라벨 인식으로 교체.
    loc_m = re.search(
        r'(?:^|\n)\s*[-*]?\s*(?:Residence|Location|Home|Base|Quarters|거처|주소|위치)'
        r'\s*[:：]\s*(.+)', desc, re.IGNORECASE)
    if not loc_m:
        # "Sharehouse resident (Room 3)" 류 괄호 표기 (기존 시트 호환)
        loc_m = re.search(r'resident\s*\(([^)]+)\)', desc, re.IGNORECASE)
    if loc_m:
        _loc = _clean_markdown(loc_m.group(1)).strip()
        if _loc and len(_loc) <= 60:
            fields["location"] = _loc

    # Speech/Tone (예: "**Tone:** Low, tired, flat.")
    # [2026-07-28] v2 풀시트는 같은 정보를 `- Speaking Style:` / `- 말투:`로 쓴다 — 편입.
    _tone = _label_value(desc, _TONE_LABELS)   # [1M remap] 캡 제거
    if _tone:
        fields["tone"] = _tone

    # Personality (Core Operating Principle에서 한 줄)
    personality_m = re.search(r'### Core Operating Principle\s*\n+(.+)', desc)
    if personality_m:
        fields["personality"] = _clean_markdown(personality_m.group(1))   # [1M remap] 캡 제거

    # Hard Constraints (ALL-CAPS 마커: CANNOT, NEVER, MUST NOT 등)
    # 프로필 중간에 묻힌 핵심 제약을 자동 추출 → recency echo용
    constraints = []
    for sentence in re.split(r'(?<=[.!])\s+|\n+', desc):
        sentence = sentence.strip()
        if not sentence:
            continue
        if re.search(r'\b(CANNOT|NEVER|MUST NOT|MUST NEVER|does NOT|CLOSED LIST|SPECIES NOTE)\b', sentence):
            clean = _clean_markdown(sentence.rstrip('.!'))
            if 20 < len(clean) < 200:
                constraints.append(clean)
    if constraints:
        # 최대 3개, 가장 짧은 것 우선 (핵심일수록 짧음)
        constraints.sort(key=len)
        fields["constraints"] = " | ".join(constraints)   # [1M remap] 캡 제거(전 constraint, 항목당 20~200자 필터는 유지)

    # Relation Keywords — 프로필에서 관계 키워드 스캔 → initial_depth/tension
    _RELATION_KEYWORDS = {
        # (depth, tension) 초기값
        # 친밀/가족
        "소꿉친구": (60, 5), "childhood friend": (60, 5),
        "절친": (65, 5), "best friend": (65, 5),
        "가족": (55, 10), "family": (55, 10),
        "형제": (50, 15), "자매": (50, 15), "sibling": (50, 15),
        "부모": (55, 15), "parent": (55, 15),
        "연인": (70, 10), "lover": (70, 10), "애인": (70, 10),
        "partner": (60, 10), "배우자": (65, 10), "spouse": (65, 10),
        # 중립/직업
        "동료": (30, 5), "colleague": (30, 5),
        "이웃": (20, 5), "neighbor": (20, 5),
        "지인": (15, 5), "acquaintance": (15, 5),
        "스승": (40, 10), "mentor": (40, 10),
        "제자": (35, 10), "student": (35, 10),
        "친구": (40, 5), "friend": (40, 5),
        # 적대/갈등
        "원수": (40, 70), "enemy": (40, 70),
        "라이벌": (35, 50), "rival": (35, 50),
        "적": (30, 60),
    }
    desc_lower = desc.lower()
    best_depth, best_tension = 0, 0
    for keyword, (d, t) in _RELATION_KEYWORDS.items():
        if keyword in desc_lower:
            if d > best_depth:
                best_depth, best_tension = d, t
    if best_depth > 0:
        fields["initial_depth"] = best_depth
        fields["initial_tension"] = best_tension

    return fields


# Generic labels to exclude from pidgin echo detection
_LABEL_EXCLUDE = frozenset([
    "있는", "없는", "하는", "되는", "같은", "다른", "모든", "이런", "그런",
    "좋은", "나쁜", "큰", "작은", "많은", "적은", "새로운", "오래된",
])

# Korean adjective-like pattern: 2+ chars ending in typical adjective suffixes
_KO_ADJ_RE = re.compile(r'[가-힣]{1,6}[운은한적인스러운]')


def get_npc_label_keywords(channel_id: str, npc_names: List[str]) -> Dict[str, List[str]]:
    """NPC personality/tone 필드에서 라벨 키워드를 추출.

    Pidgin Echo 검출용: NPC 프로필의 형용사를 추출하여
    서술에 그대로 등장하는지 확인할 수 있게 함.

    Returns:
        {npc_name: [keyword1, keyword2, ...]} max 5 per NPC
    """
    npcs = domain_manager.get_npcs(channel_id)
    if not npcs:
        return {}

    result = {}
    for name in npc_names:
        if not name or name not in npcs:
            continue
        data = npcs[name]
        # Collect text from personality and tone fields
        sources = []
        for field in ("personality", "tone"):
            val = data.get(field, "")
            if val:
                sources.append(val)

        if not sources:
            continue

        combined = " ".join(sources)
        # Extract Korean adjective-like words
        keywords = []
        for m in _KO_ADJ_RE.finditer(combined):
            word = m.group()
            if word not in _LABEL_EXCLUDE and word not in keywords:
                keywords.append(word)
            if len(keywords) >= 5:
                break

        if keywords:
            result[name] = keywords

    return result








def update_npc(channel_id: str, name: str, data: Dict[str, Any]) -> None:
    """NPC 등록/갱신의 정본 관문. 자동 추출을 얹은 뒤 domain_manager로 넘긴다.

    [2026-07-28] 우선순위 3단으로 정리:
      ① 새 data에 명시된 값  ② 기존 저장값(보존)  ③ 자동 추출값
    구 코드는 기존값을 보지 않아 ①>③>② 순이었다 — 재등록 때 자동 추출이 **먼저** 값을
    채워버려서 domain_manager의 _PRESERVE_KEYS(`pk not in data` 조건)가 무력화됐다.
    그 결과 보이스카드로 뽑아둔 tone, 최초 등록 시점 static_traits가 재등록마다
    새 추출값으로 조용히 갈렸다(스모크 A2가 검출).
    """
    _prev = domain_manager.get_npc(channel_id, name) or {}
    if not isinstance(_prev, dict):
        _prev = {}
    # desc/description 텍스트에서 구조화 필드 자동 추출 (새 데이터·기존값 둘 다 없을 때만)
    desc_text = data.get("description") or data.get("desc", "")
    if desc_text and len(desc_text) > 200:
        extracted = _extract_structured_fields(desc_text)
        _applied = []
        for key, val in extracted.items():
            if not data.get(key) and not _prev.get(key):
                data[key] = val
                _applied.append(key)
        if _applied:
            logger.info(f"[NPC] Auto-extracted fields for '{name}': {_applied}")
    # N6: 정적 심리 특성 추출 (프로필 충분할 때만, 신규이거나 기존에 없을 때만)
    # [2026-07-28 판정] "한 번만 뽑는다"는 **의도된 설계**다(결함 아님).
    #   변하는 것을 추적하는 층은 이미 셋 — npc_attitudes(매 턴)/psyche.coping(매 턴)/
    #   세션 NPC 시트 재작성(관찰 250자마다). static_traits는 그 밑의 바닥이라
    #   여기까지 움직이면 층이 겹친다. 갱신이 필요하면 `!npc 삭제` 후 재등록.
    if (desc_text and len(desc_text) >= 100
            and not data.get("static_traits") and not _prev.get("static_traits")):
        static_traits = extract_static_traits(name, desc_text)
        if static_traits:
            data["static_traits"] = static_traits
            logger.info(f"[NPC] Static traits extracted for '{name}': {static_traits}")
    domain_manager.update_npc(channel_id, name, data)

    # [2026-09-02 R4] 시트 거처 폴백 배치 — 스펙 §2.6 표 3행("시트 거처 폴백 배치").
    # 병: 출석이 `get_npcs_at_location`이 되는 순간, 어느 노드에도 없는 인물은
    #   **영원히 등장 못 하는 사람**이 된다(현행 Flash 기반엔 없던 구멍). `!npc추가`로
    #   등록만 된 인물이 여기 해당한다.
    # 처방: 시트에 거처(`location`: "Room 2" 등)가 적혀 있으면 **쓰기 시점에 앉힌다**
    #   (`get_npc_current_location`의 읽기 폴백을 앞당긴 형태). 노드가 없으면 만든다.
    #   등록 정본 관문이 여기 하나라 배치 지점도 하나여야 한다.
    # ⚠ 멱등 — `update_npc`는 매 턴 불린다(mark_npc_appearance가 부기를 여기로 흘린다).
    #   **미배치일 때만** 실행한다. 이미 어딘가 있으면 손대지 않는다: 관찰(gaze)이 옮겨 놓은
    #   위치를 시트 거처가 매 턴 되돌리면 그건 배치가 아니라 순간이동이다(관찰 > 시트).
    # ⚠ `add_node`가 "capped"(MAX_NODES)면 배치를 생략한다 — 노드 없이 set_npc_location을
    #   부르면 조용히 "location_not_found"라, 실패를 debug 한 줄로 남기고 넘어간다.
    try:
        _all = get_npcs(channel_id) or {}
        _key = domain_manager._find_npc_key(_all, name) or name
        _sheet_loc = str((_all.get(_key) or {}).get("location", "") or "").strip()
        if _sheet_loc:
            import world_tree as _wt_res
            if not _wt_res.get_npc_location(channel_id, _key):
                _ok = True
                if not _wt_res.resolve_node_id(channel_id, _sheet_loc):
                    _ar = _wt_res.add_node(
                        channel_id, _sheet_loc, node_type="area",
                        properties={"tags": ["sheet_residence"]},
                    )
                    _ok = (_ar == "created")
                    if not _ok:
                        logger.debug("[presence-check] sheet residence node '%s' not created: %s",
                                     _sheet_loc, _ar)
                if _ok:
                    _pr = _wt_res.set_npc_location(channel_id, _key, _sheet_loc)
                    if _pr == "placed":
                        logger.info("[presence-check] sheet residence placed: %s @ %s",
                                    _key, _sheet_loc)
                    else:
                        logger.debug("[presence-check] sheet residence place failed: %s @ %s (%s)",
                                     _key, _sheet_loc, _pr)
    except Exception as _e_sheet:
        logger.debug(f"[presence-check] sheet residence skip: {_e_sheet}")


def delete_npc(channel_id: str, name: str) -> tuple:
    """Returns (success: bool, matched_key: str or None)"""
    return domain_manager.delete_npc(channel_id, name)


def npc_to_pc_info(channel_id: str, name: str) -> Optional[tuple]:
    """NPC를 PC(pc_info) 스키마로 재매핑한다. (matched_key, pc_info) 반환, 못 찾으면 None.

    [2026-06-18] 로어북 분석이 주인공을 PC가 아닌 NPC로 분류하는 케이스(A) 대응.
    정보는 이미 NPC 버킷에 추출돼 있으므로 새 LLM 콜 없이 필드만 재매핑한다.
    매칭은 등록 경로와 동일하게 양방향(find_equivalent_npc_key) → fuzzy(find_similar_npc).
    """
    npcs = domain_manager.get_npcs(channel_id)
    if not npcs:
        return None
    key = domain_manager.find_equivalent_npc_key(npcs, name) or find_similar_npc(channel_id, name)
    if not key or key not in npcs:
        return None
    npc = npcs[key]
    pc_info = {
        "name": npc.get("name") or key,
        "role": npc.get("role", ""),
        "species": npc.get("species") or npc.get("race", ""),  # NPC는 'race', PC는 'species'
        "appearance": npc.get("appearance", ""),
        "description": npc.get("description") or npc.get("personality", ""),
        "background": npc.get("background", ""),
    }
    # NPC가 우연히 갖고 있을 수 있는 선택 필드만 그대로 이월 (없으면 생략)
    for opt in ("sexual_characteristics", "secret_info", "passives", "inventory", "personality", "gender"):
        if npc.get(opt):
            pc_info[opt] = npc[opt]
    return key, pc_info


def merge_character_sheet_into_pc(pc_info: dict, sheet: dict) -> dict:
    """analyze_character_sheet 결과(Tier 3)를 승격 pc_info(Tier 1)에 병합한다.

    [2026-06-18] 승격 enrich(B). NPC 추출은 passives/inventory를 안 뽑으므로 승격된 PC는
    기계 필드가 빈다. 보존된 원문에 캐릭터 시트 분석을 돌려 그 필드를 복원.

    병합 규칙:
    - 기본 서술 필드: 비어 있을 때만 시트로 채움 (이미 든 raw 원문 description을 시트 요약으로
      덮어쓰지 않기 위함 — 원문 보존이 우선).
    - 기계 필드(passives/inventory): 시트가 뽑았으면 우선 채택 (modifiers 포함 구조화 버전).
    """
    if not sheet:
        return pc_info
    for k in ("name", "role", "species", "appearance", "description",
              "background", "sexual_characteristics", "secret_info"):
        if sheet.get(k) and not pc_info.get(k):
            pc_info[k] = sheet[k]
    for k in ("passives", "inventory"):
        if sheet.get(k):
            pc_info[k] = sheet[k]
    return pc_info



def find_similar_npc(channel_id: str, new_name: str, threshold: float = 0.85) -> Optional[str]:
    """
    유사한 이름을 가진 NPC가 있는지 확인합니다.
    (Normalized -> Containment -> Fuzzy)
    """
    existing_npcs = get_npcs(channel_id)
    if not existing_npcs: return None

    n_norm = domain_manager._normalize_npc_name(new_name).lower()

    # 1. Normalized Match (괄호 공백 정규화 + case-insensitive)
    for name in existing_npcs:
        if domain_manager._normalize_npc_name(name).lower() == n_norm:
            return name

    # 2. 토큰 매칭 — [2026-09-02] 구 코드는 **부분문자열 포함**이었다.
    #    실측 로그: 'Shirase Rin'→'Rin' / 'Endo Rina'→'Rin' / 'Reina'→'Rei'.
    #    짧은 기존 이름 하나가 그것을 문자열로 품은 **모든** 새 이름을 삼킨다.
    #    일본·한국식 이름(Rin/Rina/Reina/Rei/Ren)에서 사실상 상시 오작동이고,
    #    호출부(add_lore_npcs)는 매칭되면 **등록을 건너뛰므로** 인물이 조용히 유실된다.
    #    ★오늘 `_section_family`에서 고친 것과 **같은 병**: 부분일치는 낱말 경계를 모른다.
    #    처방: 낱말 토큰 교집합 + **후보가 정확히 1명일 때만**
    #    (domain_manager._find_npc_key 4단계의 "2명 이상 공유 토큰이면 None"과 같은 규율).
    if len(n_norm) >= 3:
        _split = lambda x: {t for t in re.split(r'[\s·・,./\-]+', x) if t}
        q_tok = _split(n_norm)
        hits = [name for name in existing_npcs
                if q_tok & _split(domain_manager._normalize_npc_name(name).lower())]
        if len(hits) == 1:
            return hits[0]
        if len(hits) > 1:
            return None      # 애매하면 오병합 대신 별개 등록에 위임

    # 3. Fuzzy Match — 짧은 이름에는 적용하지 않는다.
    #    difflib 비율은 짧을수록 쉽게 넘는다: "Rina"↔"Rin" = 0.857 ≥ 0.85 → 통과해버린다.
    #    (2단계를 토큰으로 고쳐도 여기서 같은 오병합이 되살아나던 자리.)
    if len(n_norm) >= 5:
        _cands = [k for k in existing_npcs
                  if len(domain_manager._normalize_npc_name(k)) >= 5]
        matches = difflib.get_close_matches(new_name, _cands, n=1, cutoff=threshold)
        if matches:
            return matches[0]

    return None


# 헤더로 인정하는 라인 포맷들 (마크다운 / 대괄호 / 볼드 / 기호). 한 줄 전체가 헤더여야 함.
_HEADER_LINE = re.compile(
    r'^\s*(?:'
    r'#{1,4}\s+(?P<md>.+?)'                              # ## 이름
    r'|\[(?P<br>[^\]]+)\]'                               # [이름]
    r'|\*\*(?P<bd>[^*]+)\*\*'                            # **이름**
    r'|<(?P<xml>[^/<>]+?)>'                              # <이름>
    r'|[■◆●▶★※□◇]+\s*(?P<sym>.+?)(?:\s*[■◆●▶★※□◇]+)?'   # ■ 이름 / ◆이름◆
    r')\s*$'
)


def _header_name(line: str):
    """라인이 헤더 포맷이면 헤더 텍스트를, 아니면 None을 반환."""
    m = _HEADER_LINE.match(line)
    if not m:
        return None
    return (m.group('md') or m.group('br') or m.group('bd')
            or m.group('xml') or m.group('sym') or '').strip()


def extract_npc_sections_from_lore(lore_text: str, npc_names: List[str]) -> Dict[str, str]:
    """로어북 원문에서 NPC별 전체 섹션 텍스트 추출. Flash 요약 대신 원문을 보존하기 위한 용도.

    [2026-06-18] 보존 범위 확대: 마크다운(#/##) 헤더뿐 아니라 [이름]/**이름**/<이름>/
    기호(■◆) 헤더 포맷도 인식. RisuAI 로어북은 비-마크다운 구분자가 흔해 기존엔 Flash
    요약만 남던 케이스가 많았음.

    경계는 'NPC 이름과 매칭되는 헤더 라인'만 사용한다(이름-앵커). 이름과 무관한 굵은글씨/
    대괄호 줄이 실제 NPC 섹션을 중간에서 쪼개는 회귀를 막기 위함.
    """
    if not lore_text or not npc_names:
        return {}

    lines = lore_text.split('\n')
    # "리미(Limi)" → ["리미", "Limi"]
    name_parts_map = {
        name: [p.strip() for p in re.split(r'[()]', name) if p.strip()]
        for name in npc_names
    }

    # 1) NPC 이름과 매칭되는 헤더 라인만 섹션 경계로 수집
    boundaries = []  # [(line_idx, matched_name)]
    for i, ln in enumerate(lines):
        h = _header_name(ln)
        if h is None:
            continue
        h_low = h.lower()
        for name, parts in name_parts_map.items():
            if any(part.lower() in h_low for part in parts):
                boundaries.append((i, name))
                break

    # 2) 각 경계부터 다음 경계 전까지를 본문으로 슬라이스 (첫 매칭만, 500자 초과만)
    result: Dict[str, str] = {}
    for b_idx, (start, name) in enumerate(boundaries):
        if name in result:
            continue
        end = boundaries[b_idx + 1][0] if b_idx + 1 < len(boundaries) else len(lines)
        body = '\n'.join(lines[start:end]).strip()
        if len(body) > 500:
            result[name] = body

    if result:
        logger.info(f"[NPC] 로어 원문에서 {len(result)}명 NPC 섹션 추출: {list(result.keys())}")
    return result


def add_lore_npcs(channel_id: str, npc_list: List[Dict[str, Any]]) -> int:
    """
    로어 분석 결과로 NPC 일괄 등록.
    [Deduplication Added] 유사한 이름이 있으면 스킵하거나 병합합니다.

    ⚠[2026-09-02] **기본 비활성**(`config.LORE_NPC_AUTO_REGISTER`, 기본 0).
      레티어스 판정: NPC는 `!npc추가`로 따로 넣는 워크플로로 바뀌었고, 출처가 둘이면
      판정이 두 벌이 된다. 로어 추출은 이름 정확도가 낮아(로어 본문에서 뽑는다) 짧은
      이름의 **유령 NPC**를 만들고, 이후 수동 등록은 별개 항목이 되어 둘이 공존한다
      (수동 경로 `_find_npc_key`는 안전하게 분리하므로) — 청소 경로가 없다.
      코드는 남긴다: 로어북만 넣고 시작하는 워크플로로 되돌릴 때 환경변수 1줄이면 된다.
    ※ Flash의 NPC 추출 자체는 통합 분석의 일부라 그대로 돈다(콜 절감 아님). 여기서 막는
      것은 **쓰기**뿐이고, pc_info·genres·lore_summary는 영향 없다.
    """
    if not getattr(config, "LORE_NPC_AUTO_REGISTER", False):
        if npc_list:
            logger.info("[NPC] 로어 NPC 자동등록 비활성 — %d명 건너뜀 "
                        "(LORE_NPC_AUTO_REGISTER=1 로 재활성)", len(npc_list))
        return 0
    count = 0
    for npc in npc_list:
        name = npc.get("name", "").strip()
        if not name: continue
        
        # [Check Duplicate]
        sim_name = find_similar_npc(channel_id, name)
        if sim_name:
             # [2026-09-02] 구 문구는 "병합/스킵"이었지만 코드는 **스킵만** 한다.
             #   문서와 동작이 어긋나면 로그를 읽는 사람이 유실을 병합으로 오독한다.
             logger.info(f"[NPC] 로어 NPC '{name}' -> 기존 '{sim_name}'와 동일 인물로 판정. **등록 건너뜀**(병합 아님).")
             # Merge logic: Append description if source matches or just update timestamps?
             # For Lore extraction, usually we want to enrich existing if possible, 
             # but often extract might be repetitive.
             # Simple Strategy: Update description only if new one is longer?
             # OR just skip to preserve manually edited data.
             # Existing logic blindly overwrote.
             # NEW Logic: Skip overwrite if manual source, strictly update if lore source.
             
             existing = get_npc(channel_id, sim_name)
             if existing and existing.get("source") == SOURCE_MANUAL:
                 continue # Manual overrides Lore usually
             
             # If both are Lore/AI, we merge descriptions?
             # For now, let's just Log and Skip to prevent duplicates cluttering.
             # Or maybe we DO want to update if the Lore has changed?
             # Let's Skip for now to be safe against "Infinite Extraction Loop".
             continue

        data = {
            "description": npc.get("description", ""),
            "source": SOURCE_LORE,
            "registered_at": time.strftime('%Y-%m-%d %H:%M'),
        }

        # 추가 필드 복사 (role, personality, schedule 등)
        for key in ["role", "personality", "appearance", "location", "gender", "race", "schedule"]:
            if key in npc:
                data[key] = npc[key]

        update_npc(channel_id, name, data)
        count += 1
        logger.debug(f"[NPC] 로어 NPC 등록: {name}")

    return count



# ⛔[2026-07-28 삭제] add_manual_npc — 이름과 달리 **호출처 0**(grep 확인).
#   실제 수동 등록은 command_handler._register_npc → npc_manager.update_npc 경로다
#   (2026-07-28 등록 관문 단일화). 이름만 보고 "여기가 !npc추가 진입점"이라 오인하기 쉬워
#   제거한다 — 등록 진입점을 셀 때 혼동을 만들던 잔재.


def register_ai_npc(channel_id: str, name: str, description: str = "", context: str = "", gender: str = None, race: str = None) -> bool:
    """
    세션 중 AI가 생성한 NPC 등록.
    예: 이름 없던 '상인'이 '한스'로 이름이 밝혀졌을 때

    Args:
        channel_id: 채널 ID
        name: NPC 이름
        description: 간단한 설명
        context: 등장 맥락 (어떤 상황에서 등장했는지)

    Returns:
        등록된 최종 이름(동명 충돌 시 '병사 #1A' 식 태그가 붙은 이름). 실패 시 None.
    """
    if not name.strip():
        return None

    # [Anti-Gravity] Mob Tagging Logic
    # 1. Check for Exact Collision in Session NPCs
    existing = get_npc(channel_id, name)
    
    # If it exists, we have a dilemma: Is it an update or a new mob?
    # If the AI explicitly provides a generic name like "Soldier" that already exists,
    # and the descriptions significantly differ, it's likely a new mob.
    # However, for safety and user request, we will assume 'generic' names might need tagging.
    # But we don't want to break updates to "John".
    
    # Heuristic: If it exists, and the source is technically ours (AI/Session), 
    # AND we want to support multiple mobs... 
    # Actually, the user wants distinction.
    # Let's check if the name already HAS a tag.
    if is_mob_tag(name):
        # Update existing tagged mob
        pass
    elif existing:
        # Collision! It's likely a generic mob collision (or a persistent NPC).
        # We will generate a NEW tagged name for this NEW entry.
        # But wait, what if it's just an update?
        # We can't know for sure. 
        # But usually `register_ai_npc` is called when a *new* entity is detected or explicitly named.
        # Let's try to TAG the NEW one if the name is "simple" or collision happens.
        
        # Exception: Identity Reveal handled elsewhere.
        
        # Logic: Auto-tag the NEW one.
        tag = generate_mob_tag()
        tagged_name = f"{name} {tag}"
        
        # Uniqueness check (with iteration limit)
        for _attempt in range(50):
            if not get_npc(channel_id, tagged_name):
                break
            tag = generate_mob_tag()
            tagged_name = f"{name} {tag}"
        else:
            tagged_name = f"{name} #{int(time.time()) % 10000}"
            
        logger.info(f"[NPC] Name Collision '{name}' -> Auto-tagged as '{tagged_name}'")
        name = tagged_name
        # Proceed to register as NEW entry (data below)

    data = {
        "description": description,
        "source": SOURCE_AI_GENERATED,
        "registered_at": time.strftime('%Y-%m-%d %H:%M'),
        "appearances": [{"context": context, "at": time.strftime('%Y-%m-%d %H:%M')}] if context else []
    }
    if gender: data["gender"] = gender
    if race: data["race"] = race

    final_name = name.strip()
    # [2026-08-11 드라이브 부분dict 수리] 위 `is_mob_tag(name)` 분기는 **기존** 태그 몹을
    #   갱신하는 경로다(신규 태그는 바로 위에서 미존재를 확인함). 그 경우 이 부분 dict가
    #   통째 교체 관문에 들어가 _PRESERVE_KEYS 밖 필드를 날린다 → 기존값 위에 덮는다.
    _prev = get_npc(channel_id, final_name)
    if isinstance(_prev, dict):
        _merged = dict(_prev)
        _merged.update(data)
        data = _merged
    update_npc(channel_id, final_name, data)
    logger.info(f"[NPC] AI 생성 NPC 등록: {final_name}")
    return final_name


def generate_mob_tag() -> str:
    """
    Generates a random mob tag (e.g., #1A, #B7).
    User Requirement: Random Number + Letter (Random Order).
    Format: #{Char1}{Char2}
    """
    chars = "0123456789"
    alphas = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    
    # Randomly decide order: (Digit, Letter) or (Letter, Digit)
    if random.choice([True, False]):
        c1 = random.choice(chars)
        c2 = random.choice(alphas)
    else:
        c1 = random.choice(alphas)
        c2 = random.choice(chars)
        
    return f"#{c1}{c2}"

def is_mob_tag(name: str) -> bool:
    """Checks if the name ends with a mob tag pattern (#XY or 폴백 #NNNN).
    [2026-07-18] 폴백 태그(#4자리, 50회 재추첨 전부 충돌 시) 인식 추가 —
    미인식 시 폴백 개체가 재차 태깅 대상이 돼 이중 태그('병사 #1234 #2B') 위험."""
    if "#" not in name:
        return False
    tail = name.split("#")[-1]
    return len(tail) == 2 or (len(tail) == 4 and tail.isdigit())


# =========================================================
# NPC 조회 (소스별 필터링)
# =========================================================

def get_npcs_by_source(channel_id: str, source: str) -> Dict[str, Dict[str, Any]]:
    """특정 소스의 NPC만 조회"""
    all_npcs = get_npcs(channel_id)
    return {
        name: data for name, data in all_npcs.items()
        # [2026-07-28] 기본값 AI_GENERATED → SESSION. source 미상 NPC의 실제 다수는
        # 리터럴 "session"으로 등록된 것들이라 기본값도 그쪽이 맞다.
        if data.get("source", SOURCE_SESSION) == source
    }


def get_lore_npcs(channel_id: str) -> Dict[str, Dict[str, Any]]:
    """로어 NPC만 조회"""
    return get_npcs_by_source(channel_id, SOURCE_LORE)

def get_lore_npc_names(channel_id: str) -> List[str]:
    """로어 NPC 이름 목록 반환 (Background Extraction용)"""
    return list(get_lore_npcs(channel_id).keys())


def get_session_npcs(channel_id: str) -> Dict[str, Dict[str, Any]]:
    """세션 중 생성된 NPC (manual + ai_generated)"""
    all_npcs = get_npcs(channel_id)
    return {
        name: data for name, data in all_npcs.items()
        if data.get("source", SOURCE_AI_GENERATED) != SOURCE_LORE
    }

def get_scene_npc_names(channel_id: str) -> List[str]:
    """⚠ 이름과 달리 **'장면 인물'이 아니다** — 로어가 아닌 **전체** 등록 NPC 목록이다.
    (등장 여부와 무관. 이름이 오해를 불러 실제로 2곳에서 오용됐다 — 2026-07-28 수리.)
    용도: 추출 콜에 "이 이름들 중에서 고르라"고 줄 **후보 전체 명부**.
    이번 턴 실제로 장면에 있는 인물이 필요하면 get_onstage_npc_names를 쓸 것.
    """
    return list(get_session_npcs(channel_id).keys())


# [2026-09-02 R4] 레거시 폴백 로그의 턴당 1회 게이트 — {channel_id: turn_index}.
_ONSTAGE_FALLBACK_LOGGED: Dict[str, int] = {}


def get_onstage_npc_names(channel_id: str, within_turns: int = 1) -> List[str]:
    """지금 무대에 있는 NPC 이름 = **PC와 같은 위치 노드의 인원**(0단).

    [2026-09-02 R4 판정 뒤집기] 스펙 §2 / §6 R4 — **출석은 위치의 함수다.**
    병: 출석의 원본이 Flash였다. `psyche_states ∪ npc_attitudes`가 `mark_npc_appearance`로
        `_last_appear_turn`에 찍히고 이 판독기가 그걸 되읽었다 — 매 턴 **N명분의 판정**이
        독립적으로 틀릴 수 있고, 한 번 잘못 찍히면 그 턴의 소비처 여섯이 같이 틀린다(§1.4).
    처방: **판독기 내부만** 0단(`get_presence_tiers`의 scene)으로 바꾼다. 시그니처도
        소비처 6곳도 그대로다 — 판독기가 여기 하나로 모여 있어서 가능한 전환(§6 ★).
        오류 표면이 N → **위치 1개**로 줄고(§2.3), 사람은 턴을 넘어 지속한다
        (latch: 이동이 곧 퇴장 — `set_npc_location`의 remove-then-place, §2.1).
    ⚠ `within_turns`는 **호환용 잔존 인자**다(시그니처 무변경이 이 전환의 전제).
        위치는 턴 창이 아니라 **상태**라 0단에는 창이 없다 — 퇴장 이벤트 없이도 이동이
        인원을 옮기므로 "몇 턴 안에"라는 물음 자체가 사라진다. 아래 레거시 폴백에서만 쓰인다.
    폴백(단 하나): PC 노드를 해상 못 하면(`unresolved` — 전환 직후·위치 미기록 세션)
        레거시 `_last_appear_turn` 경로로 내려간다. 위치 그래프가 아직 비어 있는 판에서
        무대가 상시 빈손이면 상태창·속마음·출석 변수가 통째로 죽는다 — 그건 판정 개선이
        아니라 봇이 벙어리가 되는 것이다. 들어갈 때 `[presence-check]` 한 줄을 남긴다.
    ★`mark_npc_appearance`/`_last_appear_turn`은 **계속 쓴다** — `get_npc_tier`의
        appear_count 계측이 그 부기를 소비한다. R4가 바꾸는 것은 **읽기**뿐이다.
    """
    _tiers = get_presence_tiers(channel_id)
    if not _tiers.get("unresolved"):
        return [str(n) for n in (_tiers.get("scene") or []) if str(n).strip()]

    try:
        _pc_loc = domain_manager.get_current_location(channel_id)
    except Exception:
        _pc_loc = ""
    # 턴당 한 줄 — 이 판독기는 한 턴에 소비처 여섯에서 불린다(§1.4 표). 매 호출 로그면
    # 전환 직후 세션 로그가 같은 줄 여섯 개로 도배되고, 그러면 아무도 안 읽는다.
    try:
        _t_key = int((domain_manager.get_world_state(channel_id) or {}).get("turn_index", 0) or 0)
    except Exception:
        _t_key = 0
    if _ONSTAGE_FALLBACK_LOGGED.get(channel_id) != _t_key:
        _ONSTAGE_FALLBACK_LOGGED[channel_id] = _t_key
        logger.info("[presence-check] onstage fallback=legacy (pc location unresolved: %s)",
                    _pc_loc or "-")

    # ── 레거시 경로 (2026-07-28 원본 그대로) ─────────────────────────────
    # 판정 재료는 mark_npc_appearance가 매 턴 갱신하는 `_last_appear_turn`.
    # 턴 정보를 못 읽으면 빈 목록(호출부는 폴백을 갖는다) — 잘못된 전체 명부보다 안전하다.
    try:
        turn = int((domain_manager.get_world_state(channel_id) or {}).get("turn_index", 0) or 0)
    except (TypeError, ValueError, AttributeError):
        return []
    if turn <= 0:
        return []
    cutoff = turn - max(0, int(within_turns))
    names = []
    for name, data in (get_npcs(channel_id) or {}).items():
        if not isinstance(data, dict):
            continue
        try:
            last = int(data.get("_last_appear_turn", -1))
        except (TypeError, ValueError):
            continue
        if last >= cutoff:
            names.append(name)
    return names


# [2026-09-02 R4] 미배치·비연결 인물의 홉 거리. 정렬 안정성을 위해 무한대가 아닌 유한값.
UNPLACED_HOPS = 999


def get_presence_tiers(channel_id: str) -> Dict[str, Any]:
    """위치 그래프가 말하는 3단 출석 + 홉 거리. 스펙 §2.4.

    [2026-09-02 R2] log-only 검증자로 신설 → **[R4] 출석의 정본**이 됐다.
      `get_onstage_npc_names`가 여기 scene(0단)을 그대로 돌려주므로, 이 함수의 판정이
      곧 소비처 6곳의 출석이다(§1.4 표). 바꿀 때 그 여섯을 같이 생각할 것.

    출석은 이진이 아니라 **거리**다(스펙 ⓐ). 트리는 애초에 거리를 표현하려고 설계돼 있다
    (`connections` 간선 + 형제/부모/자식):
      · scene(0단)     = PC와 같은 노드 — 지금 여기 있다
      · reachable(1단) = 부모 / 자식 / 연결 이웃 / 형제 — **들어올 수 있다**
      · absent(2단)    = 그 외 등록 NPC 중 활성(`is_npc_active`)인 사람
      · hops           = PC 노드로부터의 그래프 거리(부모·자식·간선 BFS)

    ⚠ 티어와 홉은 **같은 축이 아니다.** 티어는 규칙이고 홉은 거리다 — 형제는 부모를 거쳐
      2홉인데도 1단이고, 간선 이웃은 1홉이다. 둘이 어긋나는 게 정상이고, 2단 정렬(§2.4
      "옆방 흔적이 다른 도시 흔적보다 먼저")은 규칙이 아니라 거리를 봐야 한다.

    PC 노드를 해상 못 하면 전원을 absent로 몰지 **않는다** — 그건 "아무도 없다"는
    거짓 단정이고, 위치 미기록과 부재는 다른 사실이다. 대신 `unresolved: True`를 얹은
    빈 3단을 돌려주고 호출부가 폴백하게 한다(R4: `get_onstage_npc_names`의 레거시 경로).

    Returns: {"scene": [...], "reachable": [...], "absent": [...], "hops": {name: int}}
             (+ PC 노드 미해상 시 "unresolved": True)
    """
    empty: Dict[str, Any] = {"scene": [], "reachable": [], "absent": [],
                             "hops": {}, "unresolved": True}
    try:
        import world_tree
    except Exception as _e:
        logger.debug(f"[presence-tiers] world_tree import skip: {_e}")
        return empty

    try:
        pc_loc = domain_manager.get_current_location(channel_id)
    except Exception:
        pc_loc = ""
    if not pc_loc or str(pc_loc).strip().lower() in ("", "unknown"):
        return empty

    node_id = world_tree.resolve_node_id(channel_id, pc_loc)
    if not node_id:
        return empty
    nodes = world_tree.get_all_nodes(channel_id) or {}
    node = nodes.get(node_id)
    if not isinstance(node, dict):
        return empty
    node_name = str(node.get("name", node_id))

    # 0단 — PC와 같은 노드
    scene: List[str] = []
    for n in (node.get("npcs_present", []) or []):
        if n and n not in scene:
            scene.append(n)

    # 1단 — 연결 이웃 + 형제(get_nearby_locations) + 부모 노드의 인원
    reachable: List[str] = []

    def _add_reach(names):
        for _n in (names or []):
            if _n and _n not in scene and _n not in reachable:
                reachable.append(_n)

    try:
        for nb in (world_tree.get_nearby_locations(channel_id, node_name) or []):
            _add_reach(world_tree.get_npcs_at_location(channel_id, nb.get("name", "")))
    except Exception as _e:
        logger.debug(f"[presence-tiers] nearby skip: {_e}")
    _parent = nodes.get(node.get("parent_id", "") or "")
    if isinstance(_parent, dict):
        _add_reach(_parent.get("npcs_present", []))
    # [2026-09-02 검수 수리] **자식 노드도 1단.** 스펙 §2.4 문면("부모/연결 이웃/형제")엔 자식이
    #   빠져 있었고 구현자가 그대로 옮겨 방향 비대칭을 만들었다 — PC가 "저택 서재"면 "저택"의
    #   인물은 근접인데, PC가 "저택"이면 "저택 서재"의 인물은 부재였다. 근접(들어올 수 있다)은
    #   대칭이어야 한다: 로비에 있으면 서재 사람이 문을 열고 나올 수 있다. 트리 거리로도
    #   부모·자식은 같은 1홉이다(형제는 2홉인데도 포함되므로 자식 제외는 더더욱 근거가 없다).
    for _cid in (node.get("children", []) or []):
        _child = nodes.get(_cid)
        if isinstance(_child, dict):
            _add_reach(_child.get("npcs_present", []))

    # 2단 — 나머지. 생존축 필터는 is_npc_active 하나로만 판정한다(자매 자리 소급용 단일 관문).
    absent: List[str] = []
    for name, data in (get_npcs(channel_id) or {}).items():
        if not isinstance(data, dict):
            continue
        if name in scene or name in reachable:
            continue
        if not is_npc_active(data):
            continue
        absent.append(name)

    # [2026-09-02 R4] 홉 거리 — PC 노드에서 부모·자식·연결 간선 BFS. 스펙 §2.4.
    # 왜 필요한가: 2단(부재)은 평평한 목록이면 "다른 도시 사람"과 "옆방 사람"이 같은 무게로
    #   offscreen_trace 후보에 오른다. 거리를 실어야 옆방 흔적이 먼저 온다.
    # 미배치(어느 노드에도 없음)·비연결 노드는 UNPLACED_HOPS — 큰 유한값이라 정렬 맨 뒤에
    #   서면서도 비교가 터지지 않는다.
    hops: Dict[str, int] = {}
    try:
        _dist = {node_id: 0}
        _queue = [node_id]
        while _queue:
            _cur = _queue.pop(0)
            _cn = nodes.get(_cur) or {}
            _adj: List[str] = []
            _pid = _cn.get("parent_id", "") or ""
            if _pid:
                _adj.append(_pid)
            _adj.extend([c for c in (_cn.get("children", []) or []) if c])
            for _conn in (_cn.get("connections", []) or []):
                _tid = (_conn or {}).get("target_id", "") or ""
                if _tid:
                    _adj.append(_tid)
            for _nb in _adj:
                if _nb in nodes and _nb not in _dist:
                    _dist[_nb] = _dist[_cur] + 1
                    _queue.append(_nb)
        for _nid, _nd in nodes.items():
            if not isinstance(_nd, dict):
                continue
            _d = _dist.get(_nid, UNPLACED_HOPS)
            for _n in (_nd.get("npcs_present", []) or []):
                if _n and _d < hops.get(_n, UNPLACED_HOPS + 1):
                    hops[_n] = _d
    except Exception as _e_hop:
        logger.debug(f"[presence-tiers] hops skip: {_e_hop}")
    for _n in (scene + reachable + absent):
        hops.setdefault(_n, UNPLACED_HOPS)

    return {"scene": scene, "reachable": reachable, "absent": absent, "hops": hops}


def _get_npc_desc(data: dict) -> str:
    """NPC 설명 필드 읽기 (description/desc 호환).
    레거시 자동생성 플레이스홀더("Auto-detected by AI")는 빈 문자열로 취급 →
    이미 그 값으로 저장된 기존 NPC도 DB 마이그레이션 없이 산문 노출이 사라진다."""
    d = data.get("description") or data.get("desc", "")
    if str(d).strip().lower() in ("auto-detected by ai", "auto-detected by ai."):
        return ""
    return d


def _npc_desc_fallback(data: dict) -> str:
    """[D-A] 표시용 설명 폴백 체인. description이 비면(자동 NPC 흔함) 실제로 채워진
    관찰/면모로 대체 — 렌더러(get_npc_renderer_profiles)의 폴백을 명령/로스터에도 복제.
    순서: description → play_observed → 면모(정체성/aspects/외형/역할)."""
    if not isinstance(data, dict):
        return ""
    d = _get_npc_desc(data)
    if str(d).strip():
        return d
    obs = str(data.get("play_observed", "") or "").strip()
    if obs:
        return obs
    parts = []
    if data.get("high_concept"):
        parts.append(str(data["high_concept"]))
    _asp = data.get("aspects")
    if isinstance(_asp, list) and _asp:
        parts.append(" · ".join(str(a) for a in _asp))
    if data.get("appearance"):
        parts.append(str(data["appearance"]))
    if data.get("role"):
        parts.append(str(data["role"]))
    return " / ".join(parts)


def get_npc_tier(data: dict) -> str:
    """[T-A] 자동생성 NPC의 1회성/다회성 tier. lore/manual=작가권위라 항상 established.
    session은: 시트 증류됨(관찰 재작성/면모 보유) OR 5개 구별 턴 이상 등장 → established, 그 외 provisional."""
    if not isinstance(data, dict):
        return "established"
    src = str(data.get("source", SOURCE_SESSION)).lower()
    if src in FROZEN_SOURCES:
        return "established"
    # 시트가 이미 재작성됐거나(관찰 증류) 면모가 있으면 비중 있는 조연 → established (가드레일 a)
    if int(data.get("_obs_built_len", 0) or 0) > 0:
        return "established"
    _asp = data.get("aspects")
    if data.get("high_concept") or (isinstance(_asp, list) and _asp):
        return "established"
    if int(data.get("appear_count", 0) or 0) >= 5:
        return "established"
    return "provisional"


# =========================================================
# [2026-08-11 사망 파이프라인] 생존축 — 단일 판정 함수 + 전이 관문
# =========================================================

def get_npc_status(npc_data: dict) -> str:
    """저장된 생존축 값(정규화). 부재·미지 값은 전부 active.

    관용이 기본인 이유: 이 필드는 08-11에 처음 값을 갖는다. 그 전에 등록된 NPC는
    전원 `""` 또는 `"Active"`(대문자 생성 도장)라, 엄격하게 읽으면 **기존 캐스트가
    통째로 무대에서 사라진다**. 모르는 값 = 살아있다(=아무것도 안 한다)가 안전측.
    """
    if not isinstance(npc_data, dict):
        return "active"
    s = str(npc_data.get("status", "") or "").strip().lower()
    return s if s in getattr(config, "NPC_STATUS_VALUES", ("active",)) else "active"


def is_npc_active(npc_data: dict) -> bool:
    """무대 후보 자격. **생존축 필터는 전부 이 함수 하나로 판정한다.**

    소비처(막간·오프스크린·world_board·로스터·soma 렌더)마다 `!= "dead"` 같은
    조건식을 직접 심으면, 다음에 enum이 늘 때 자매 자리가 소급을 못 받는다
    (VISCERAL/MATURE·GRADIA 이중투입과 같은 병 — 규약을 세울 때 형제 자리를
    같이 세지 않는 습관).
    ⚠회상·발효·감쇠에는 대지 말 것 — 죽은 자의 과거는 정당한 기억이고,
      값만 내리는 감쇠는 시체에도 무해하다. 이 필터는 **능동 후보 조립 전용**.
    """
    return get_npc_status(npc_data) not in ("down", "dead")


def set_npc_status_gated(channel_id: str, name: str, new_status: str,
                         source: str, evidence: str = "",
                         current_turn: Optional[int] = None) -> str:
    """생존축 전이 관문. LLM이 만들 수 있는 상태를 코드가 제한한다.

    Rules
      1. →down : source 불문 허용. 단 **자동(source != "manual")은 evidence 필수** —
         근거 없는 하강은 관측이 아니라 추측이고, 하류는 그 인물을 조용히 접는다.
      2. down→active : 항상 허용. 가역이 down의 정의다(재등장 관측·수동 둘 다).
      3. *→dead / dead→* : **source == "manual"만.** 자동 시도는 거부 + 로그 1줄
         (사람 판독용 — 모델이 누구를 죽이려 했는지가 관측 재료).
      4. 같은 상태면 no-op, 도장도 안 찍는다(status_changed_turn 시계 보존 —
         set_drive_gated L1946과 같은 규율).

    Returns: "accepted" | "rejected_authority" | "rejected_invalid" | "unchanged"
    """
    _values = getattr(config, "NPC_STATUS_VALUES", ("active",))
    target = str(new_status or "").strip().lower()
    if target not in _values:
        logger.warning("[NPC Status] 알 수 없는 상태 %r (%s) — 무시", new_status, name)
        return "rejected_invalid"
    data = get_npc(channel_id, name)
    if not isinstance(data, dict):
        return "rejected_invalid"
    cur = get_npc_status(data)
    if cur == target:
        return "unchanged"
    _manual = str(source or "").strip().lower() == "manual"
    _irrev = getattr(config, "NPC_STATUS_IRREVERSIBLE", ("dead",))
    if (target in _irrev or cur in _irrev) and not _manual:
        logger.info("[NPC Status] %s: %s→%s 거부 — 비가역 전이는 수동만 (source=%s) %s",
                    name, cur, target, source, str(evidence or "")[:80])
        return "rejected_authority"
    if target == "down" and not _manual and not str(evidence or "").strip():
        logger.info("[NPC Status] %s: →down 거부 — 자동 경로는 근거 필수 (source=%s)",
                    name, source)
        return "rejected_authority"
    if current_turn is None:
        try:
            current_turn = int((domain_manager.get_world_state(channel_id) or {}).get(
                "turn_index", 0) or 0)
        except (TypeError, ValueError, AttributeError):
            current_turn = 0
    try:
        current_turn = int(current_turn)
    except (TypeError, ValueError):
        current_turn = 0   # 호출부가 문자열 턴을 넘겨도 전이 자체는 살린다
    # ★update_npc는 엔트리 **통째 교체** 관문이다 — 부분 dict를 넘기면 시트가 날아간다.
    #   mark_npc_appearance와 같은 full-copy 패턴을 지킨다.
    _new = dict(data)
    _new["status"] = target
    _new["status_changed_turn"] = current_turn
    _ev = str(evidence or "").strip()
    if _ev:
        _new["status_evidence"] = _ev[:300]
    update_npc(channel_id, name, _new)
    logger.info("[NPC Status] %s: %s→%s (source=%s, turn=%d) %s",
                name, cur, target, source, current_turn, _ev[:80])
    return "accepted"


def mark_npc_appearance(channel_id: str, name: str, turn: int) -> None:
    """[T-A] NPC가 이 턴 실제 등장했음을 기록(구별 턴만 카운트 = turn dedup).
    lore/manual은 tier 계측 불필요(항상 established)라 스킵. 순수 부기, LLM 콜 없음."""
    data = get_npc(channel_id, name)
    if not isinstance(data, dict):
        return
    # [2026-08-11 사망 파이프라인] down→active 자동 복귀.
    #   등장 관측의 단일 관문이 여기라 복귀 판단도 여기 하나뿐이다.
    #   ★frozen 조기반환보다 **위**에 둔다 — lore/manual NPC도 쓰러지고 돌아온다
    #     (아래 return은 tier 계측 스킵일 뿐, 생존축과는 무관한 사유다).
    #   dead는 복귀시키지 않는다: 자동 경로엔 비가역 해제 권한이 없다.
    #     로그만 남긴다 — 죽은 이름이 장면에 다시 뜬 것 자체가 환각 신호다.
    _st = get_npc_status(data)
    if _st == "down":
        if set_npc_status_gated(channel_id, name, "active", source="reappearance",
                                evidence="on-stage this turn", current_turn=turn) == "accepted":
            # 아래 부기가 **갱신 전 스냅샷**으로 덮어써 status를 되돌리지 않도록 재조회
            data = get_npc(channel_id, name) or data
    elif _st == "dead":
        logger.info("[NPC Status] %s: dead인데 등장 관측 — 복귀 없음 (환각 등장 신호)", name)
    if str(data.get("source", SOURCE_SESSION)).lower() in FROZEN_SOURCES:
        return
    try:
        last = int(data.get("_last_appear_turn", -1))
    except (TypeError, ValueError):
        last = -1
    try:
        turn = int(turn)
    except (TypeError, ValueError):
        return
    if last == turn:
        return  # 같은 턴 중복 카운트 방지
    _new = dict(data)
    _new["appear_count"] = int(data.get("appear_count", 0) or 0) + 1
    _new["_last_appear_turn"] = turn
    update_npc(channel_id, name, _new)


def get_npc_roster(channel_id: str) -> str:
    """전체 NPC 이름+역할+위치 1줄 요약 목록 (Theoria용)."""
    npcs = get_npcs(channel_id)
    if not npcs:
        return ""
    lines = []
    for name, data in npcs.items():
        # [2026-08-11 사망 파이프라인] dead만 제외. 분석 콜에 "지금 부를 수 있는 사람"을
        #   주는 자리라 시체가 섞이면 그대로 후보가 된다.
        #   down은 남긴다 — 가역 상태라 분석이 "깨어나는가"를 판단할 재료가 필요하다.
        if get_npc_status(data) == "dead":
            continue
        # [D-A] 분석(Theoria)은 전체 캐스트가 필요 → 접기 없이 폴백만(빈 description → 관찰/면모)
        desc = _npc_desc_fallback(data)
        blurb = _roster_blurb(desc, data)
        role = data.get("role", "")
        # [2026-07-28] world_tree 우선 — 등록 시점에 굳은 시트 값이 아니라 지금 있는 곳
        location = get_npc_current_location(channel_id, name, data)
        tag = f" [{role}]" if role else ""
        tag += f" @{location}" if location else ""
        lines.append(f"- {name}{tag}: {blurb}")
    return "\n".join(lines)


# 로스터 요약에서 건너뛸 라벨 — 판별력이 없거나 이미 다른 자리에 있는 것들.
#   이름/별칭은 `- {name}` 자리에, 종족은 summary에 이미 있다.
#   나이·성별·신체·복장은 "이 인물을 이번 장면에 부를까"와 무관한 정보라 자리만 먹는다.
_ROSTER_SKIP_LABELS = (
    "name", "이름", "alias", "별칭", "aka",
    "age", "나이", "sex", "gender", "성별",
    "species", "종족", "race", "physical", "외모", "외형", "attire", "복장",
    "abilities", "능력", "outfit", "body", "aura", "overall look", "hair", "eyes",
    # v2 풀시트 계열의 저정보 항목
    "birthday", "생일", "faith", "religion", "종교", "nationality", "국적",
    "class", "hobby", "hobbies", "취미", "like", "dislike", "hate",
)
# 반대로 **가장 판별력 있는** 라벨 — 있으면 이걸 먼저 쓴다.
# ※ 역할/직업 계열(role·job·occupation·duty)은 여기 넣지 않는다 — 이미 summary로 앞에 붙으므로
#   preferred로 또 고르면 같은 말이 두 번 나가고 정작 서사 재료(과거·평판)를 밀어낸다.
_ROSTER_PREFER_LABELS = (
    "background", "배경", "past", "과거", "history",
    "reputation", "평판", "social status", "lifestyle", "residence",
)


def _roster_blurb(desc: str, data: dict = None, cap: int = 90) -> str:
    """Theoria가 '이번 턴 이 인물을 부를까'를 판단하는 유일한 재료.

    [2026-07-28] 구 코드는 `desc.split("\\n")[0][:50]` — **첫 줄을 그대로** 썼다.
    시트가 `### Identity`로 시작하는 흔한 포맷(외부 캐릭터 시트 관례)에서는
    전 인물의 요약이 똑같이 "### Identity"가 되어 **선별이 사실상 무작위**였다.
    시트 쪽에 "첫 줄은 평문으로 쓰라"를 요구하는 대신 읽는 쪽을 고친다.

    순서: ① summary(종족/역할 라벨에서 조립된 것) ② Background/Occupation 값이 있으면 그것
    ③ 없으면 첫 실질 문장 — 헤더·구분선·저정보 라벨(나이/성별/신체/복장)을 건너뛰고,
    불릿이면 `라벨: 값`의 값 쪽을 쓴다.
    """
    _d = data or {}
    parts = []
    _summary = str(_d.get("summary", "") or "").strip()
    if _summary:
        # v2 풀시트는 종족/역할 값이 길다("Sentient Subterranean Supercomputer Complex …").
        # 그대로 두면 summary가 캡을 다 먹고 정작 판별에 쓸 문장이 안 들어간다 → 절반까지만.
        _half = max(30, cap // 2)
        if len(_summary) > _half:
            _summary = _summary[:_half].rstrip(" /-—") + "…"
        parts.append(_summary)

    preferred, fallback = "", ""
    for raw in (desc or "").split("\n"):
        s = raw.strip()
        if not s or s.startswith("#") or re.match(r'^[=\-*~]{3,}$', s):
            continue
        s = s.lstrip("-*> ").strip()
        if not s:
            continue
        _label = ""
        if ":" in s:
            _k, _v = s.split(":", 1)
            _label = _k.strip().lower()
            if _label in _ROSTER_SKIP_LABELS:
                continue
            # 불릿 라벨이면 값 쪽이 내용이다 (`- Background: …` → `…`)
            if len(_k.strip()) <= 20 and _v.strip():
                s = _v.strip()
        if len(s) < 8:      # "Female", "Mm~" 같은 단발 값은 판별력이 없다
            continue
        if _label in _ROSTER_PREFER_LABELS and not preferred:
            preferred = s
            break
        if not fallback:
            fallback = s
    if preferred or fallback:
        parts.append(preferred or fallback)

    return " — ".join(parts)[:cap] if parts else ""


# =========================================================
# Scene-Aware Section Selection
# =========================================================
# 항상 포함되는 코어 섹션 (이름에 이 문자열이 있으면 프로필 맨 앞으로 당김)
# 우선 배치 — **가족**으로 판정한다(구 코드는 정확일치 리스트라 `## 1. Basic Info`처럼
#   번호·자유 명명 시트에서 전부 빗나갔다). 순서 = 인물이 먼저, 규율이 다음(09-02 결정).
_CORE_FAMILIES = ("identity", "rules")
_CORE_SECTIONS = ["Identity", "Hard Rules"]   # 호환 표기(도구 npc_section_gui가 import한다)

# ⛔[2026-07-28 삭제] _SCENE_SECTION_MAP — 장면 유형별 섹션 화이트리스트.
#   정의만 있고 **참조처 0**이었다(grep 확인). `_select_profile_sections(scene_type=...)`의
#   인자는 남아 있지만 내부에서 쓰지 않는다 = 전투/사교/친밀/탐험 구분 없이 전 섹션 상시 노출.
#   레티어스 결정(2026-07-28): **이 기능은 만들지 않는다** → 오해 유발 코드라 제거.
#   되살릴 일이 생기면 git 이력에서. 당시 키: combat/social/intimate/exploration/summary/normal.
#   ※ scene_type 인자 자체는 호출부 호환 위해 존치(무해).

_MAX_TOTAL_PER_NPC = 50000  # [Sprint L 2026-04-29] 사고 방어 안전망만. 정상 운영 도달 X.

# 배경/설정류 섹션 키 — 렌더러(Pro)엔 "직접 서술 금지, 현재 잔여로만" 프레임으로 제자리 강등.
# Theoria(Flash 분석)는 원본 유지 (분석엔 배경 전체 필요). drop 아니라 wrap → Sprint L 헤더자유도 무손상.
# ══ 섹션 가족 판정 — 공용 단일 관문 (2026-09-02) ══════════════════════════
# 병: 같은 판단("이 섹션은 무엇인가")이 세 자리에 흩어져 있었다 —
#   _BACKGROUND_SECTION_KEYS(여기) / _VOICE_SECTION_KEYS·_HIDDEN_SECTION_KEYS(build_voice_digest
#   근처) / _CORE_SECTIONS(정확일치 리스트). 그중 은닉·목소리 사전은 **소비자가
#   build_voice_digest 하나뿐**이라 렌더러 프로필은 아무 혜택도 못 받고 있었다.
#   사전이 여러 벌이면 한쪽만 고쳐지는 날이 온다 → 한 자리로 모으고 판정 함수 하나만 쓴다.
#
# ★정본 가족은 시트 관례가 아니라 **코드가 실제로 다르게 대우하는 기능**에서 나온다:
#     hidden=렌더러에서 감싸기 / voice=목소리 재료 / rules·identity=우선 배치 /
#     background=강등 / **미매칭=원문 순서 통과(이름을 만들지 않는다)**
#   이 다섯에 안 걸리는 정본 이름을 만들어봐야 아무 일도 하지 않는다.
# ★사전이 뒤처져도 안전한 이유(= _PRESERVE_KEYS와 다른 점): 여기서 뒤처짐의 손해는
#   "기능이 안 걸림"뿐이고 섹션 내용은 통과 경로로 온전히 남는다(유실 0·가역).
# 설계: 파티쳇수정/npc_sheet_ingest_spec_2026-09-02.md §5

# 은닉 — 목소리보다 **먼저** 걸린다("Secret Voice"는 목소리가 아니라 비밀이다).
# "정체"는 넣지 않는다: 흔한 섹션명 `정체성`(=Identity)을 오폭한다.
_HIDDEN_SECTION_KEYS = (
    "secret", "hidden", "true identity", "agenda", "betrayal", "deception",
    "비밀", "숨겨진", "기밀", "속내",
)
# 목소리 — v7/v6=Core Traits·Aside, v5=Voice, 그 외 외부 시트의 성격·말투 계열.
# ("speech"가 `Speech Style & Tone`을 이미 덮으므로 조합형 항목을 따로 넣지 않는다.)
_VOICE_SECTION_KEYS = (
    "core traits", "aside", "voice", "personality", "speech", "tone",
    "성격", "말투", "어조", "핵심 특성", "방백",
)
# 행동 규율 — v7 정본의 `Direction`이 여기다(구 사전엔 어디에도 없어 통과되고 있었다).
_RULES_SECTION_KEYS = (
    "direction", "hard rules", "rules", "guideline", "guidelines",
    "directive", "discipline", "규칙", "규율", "원칙", "금칙",
)
# 배경 — 강등(작가 참조) 대상.
_BACKGROUND_SECTION_KEYS = (
    "background", "backstory", "biography", "lore", "history",
    "배경", "설정", "내력", "과거", "생애",
)
# 정체 — 우선 배치 1순위. `core` 단독은 넣지 않는다(`Core Wound`가 끌려 올라온다).
_IDENTITY_SECTION_KEYS = (
    "identity", "basic info", "basic profile", "overview",
    "기본", "정체", "개요",
)

# 판정 순서 = 특수 → 일반. 첫 매치 승. 겹치는 이름에서 결과가 흔들리지 않게 **고정**한다.
_SECTION_FAMILIES = (
    ("hidden", _HIDDEN_SECTION_KEYS),
    ("voice", _VOICE_SECTION_KEYS),
    ("rules", _RULES_SECTION_KEYS),
    ("background", _BACKGROUND_SECTION_KEYS),
    ("identity", _IDENTITY_SECTION_KEYS),
)

_SEC_NUM_PREFIX = re.compile(r'^\s*\d+[.)]\s*')
# ASCII 키는 낱말 경계를 요구한다 — "stone"이 `tone`에, "milestone"이 목소리에 걸리는 걸 막는다.
# 단 **뒤쪽 복수형 s는 허용**한다: v7 정본의 실제 섹션명이 `Secrets`인데 키는 `secret`이라,
#   경계만 걸면 정작 잡아야 할 섹션이 튕긴다(2026-09-02 스모크가 검출). `secretary`는
#   s? 뒤의 경계가 여전히 막으므로 오탐은 열리지 않는다.
# 한글 키는 경계 개념이 없으므로 부분일치 그대로(오탐 사례 없음).
_SEC_KEY_RE = {}


def _normalize_section_name(name: str) -> str:
    """번호 접두·마크다운 장식 제거 후 소문자.

    외부 시트는 `## 6. Speech Style & Tone`처럼 번호를 붙인다 — 정규화 없이 정확일치를
    쓰면(구 _CORE_SECTIONS) 그런 시트에서 전부 빗나간다.
    """
    n = _SEC_NUM_PREFIX.sub("", str(name or ""))
    n = re.sub(r'[*_#]', '', n)
    return n.strip().lower()


def _section_family(name: str) -> str:
    """섹션 이름 → 정본 가족(hidden/voice/rules/background/identity). 못 붙으면 ""(=통과)."""
    n = _normalize_section_name(name)
    if not n:
        return ""
    for fam, keys in _SECTION_FAMILIES:
        for k in keys:
            if k.isascii():
                r = _SEC_KEY_RE.get(k)
                if r is None:
                    r = _SEC_KEY_RE[k] = re.compile(
                        r'(?<![a-z0-9])' + re.escape(k) + r's?(?![a-z0-9])')
                if r.search(n):
                    return fam
            elif k in n:
                return fam
    return ""


def _is_background_section(name: str) -> bool:
    return _section_family(name) == "background"


# ⚠ **넓은 _VOICE_SECTION_KEYS와 일부러 구분한다.** 그쪽은 "목소리 **재료**"(성격·말투·핵심
#   특성까지 넓게)이고, 이쪽은 "**1인칭 목소리 블록**을 가졌는가"라는 좁은 판정이다.
#   가족 판정으로 넓히면 `### Personality`만 있는 시트가 hybrid로 잡혀 보이스카드 증류를
#   건너뛴다 = 정작 말투 추출이 필요한 시트가 빠진다. 저쪽이 묶어 놓은 걸 분리해서 판정한다.
_VOICE_BLOCK_RE = re.compile(r'^(?:voice|aside)\b')


def _is_hybrid_profile(desc: str) -> bool:
    """Voice 섹션(1인칭 목소리 블록)을 가진 시트인가.
    [2026-07-28] h4형 시트(`#### Voice`)도 인정 — 섹션 깊이 판정과 보조를 맞춘다.
    [2026-08-10] Aside판(방백 생성 템플릿, v5 후계) 인식 — 섹션명만 다르고 역할은 Voice와 동일.
      미인식 시 보이스카드 증류 대상 + echo 스킵 미적용(접은 고정조각 반복이 재입장).
    [2026-09-02] 구 코드는 `#{3,4}` 리터럴이라 h1/h2형 시트의 `## Voice`를 못 봤다 →
      섹션 파서를 거쳐 **시트 자신의 깊이**로 판정한다(번호 접두도 여기서 벗겨진다)."""
    return any(_n != "_preamble" and _VOICE_BLOCK_RE.match(_normalize_section_name(_n))
               for _n in _parse_sections(desc or ""))


def _extract_voice_section(desc: str) -> str:
    """프로필에서 Voice(또는 Aside) 섹션 텍스트만 추출. 없으면 빈 문자열.

    [2026-09-02] 구 코드는 `sections.get("Voice")` **정확일치**라 `## 6. Voice Notes`나
      번호 접두가 붙은 시트에서 통째로 빗나갔다. 판정을 _is_hybrid_profile과 한 벌로 맞춘다.
    """
    for _n, _t in _parse_sections(desc or "").items():
        if _n != "_preamble" and _VOICE_BLOCK_RE.match(_normalize_section_name(_n)):
            return _t
    return ""


def _section_header_depth(desc: str) -> int:
    """이 프로필에서 **실질 섹션 구분자로 쓰인 헤더 깊이**를 판정한다(3 또는 4).

    [2026-07-28] 외부 캐릭터 시트에는 두 계열이 있다:
      · h3형 — `### Identity` `### Voice` … (h3가 여러 개, 우리 시트 관례)
      · h4형 — `### 캐릭터 이름` 하나 아래 `#### Basic Info` `#### Background` … 16개
               (커뮤니티 풀시트 템플릿 관례)
    구 코드는 `###`만 잘라서 h4형이 **통째로 한 덩어리**가 됐다. 12,000자짜리 시트에서
    `#### Background`가 섹션으로 안 잡히니 "직접 낭독 금지" 프레임도 안 붙고,
    코어 섹션 정렬도 무의미해졌다.
    [2026-09-02] h1·h2형 편입. 외부 시트에는 `# 이름 Profile` 한 줄 아래 `## 1. Basic Info`
      … 형이 흔한데, 구 판정은 h3/h4만 봐서 **3을 반환하고 섹션을 하나도 못 잘랐다**
      (통짜 → 배경 강등도 은닉 감싸기도 호출조차 안 됨).
    판정: **깊이 1~4 중 헤더가 2개 이상인 가장 얕은 깊이.** 없으면 3.
      구 두 계열을 그대로 재현한다 — h3형은 h3 채택, `### 이름` 1개 + `#### 섹션` 16개는
      h3가 2개 미만이라 탈락하고 h4 채택. 회귀 없이 h1·h2만 얹는 확장이다.
    "2개 이상"이 하는 일: `# Airi Profile`처럼 **하나뿐인 제목 헤더**를 구분자에서 뺀다.
      안 빼면 파일 전체가 한 섹션이 되어 아무것도 안 잘린다.
    ★깊이가 중요한 이유는 개수가 아니라 **범위**다 — 어느 깊이를 구분자로 삼느냐가
      하위 헤더를 부모 안에 남길지 형제로 쪼갤지를 정하고, 그게 강등·은닉이 걸리는 범위다.
    """
    for _d in (1, 2, 3, 4):
        if len(re.findall(r'^' + '#' * _d + r'(?!#)\s+\S', desc or "", re.MULTILINE)) >= 2:
            return _d
    return 3


def _parse_sections(desc: str) -> Dict[str, str]:
    """마크다운 헤더 기준으로 프로필을 섹션 dict로 분할.

    구분자 깊이는 _section_header_depth가 시트 형태를 보고 정한다(h3형/h4형).
    """
    sections: Dict[str, str] = {}
    depth = _section_header_depth(desc)
    hashes = "#" * depth
    split_re = re.compile(r'\n(?=' + hashes + r'(?!#)\s)')
    head_re = re.compile(hashes + r'(?!#)\s+(.+)')
    for part in split_re.split(desc or ""):
        header_m = head_re.match(part)
        if header_m:
            sections[header_m.group(1).strip()] = part.strip()
        elif not sections:
            sections["_preamble"] = part.strip()
    return sections


def _select_profile_sections(desc: str, scene_type: str = "normal", demote_background: bool = False) -> str:
    """모든 섹션을 _CORE 우선으로 순서대로 노출.

    [Sprint L 2026-04-29] 시트 별 헤더 자유도 + 섹션 누락 방지.
    scene_type 인자는 호환성 위해 유지 (내부 사용 X — 미래 exclusion 후보).
    _MAX_TOTAL_PER_NPC = 50000은 사고 방어 안전망 (정상 운영 도달 X).
    """
    # [2026-09-02] 구 조기 반환 `'###' not in desc`는 h1/h2형 시트를 통째로 돌려보냈다
    #   (섹션이 안 잘리니 강등·은닉이 호출조차 안 됨). 판정은 깊이 함수 하나에 맡긴다 —
    #   헤더가 없으면 아래 `len(parsed) <= 1`이 같은 일을 한다.
    if not desc:
        return ""

    parsed = _parse_sections(desc)
    if len(parsed) <= 1:
        return desc[:_MAX_TOTAL_PER_NPC]

    result_parts = []
    included = set()

    def _maybe_frame(sec_name, sec_text):
        """렌더러 경로에서만 배경·은닉 섹션에 **태그**를 씌운다.

        [2026-09-02] 구 코드는 배경에 241자짜리 산문 프레임을 **섹션마다·NPC마다** 붙였다
        → 무대 인원에 선형으로 불어난다(5명이 배경+비밀이면 프레임만 ~2.9k자/턴).
        크래프트는 이미 상시로 있다 — NPC_BEHAVIOR_SYSTEM(언제 풀리는가)·
        PROSE_CRAFT_PROTOCOL(어떻게 쓰는가)·조교 패턴. 여기서 다시 가르치면 **이중 투입**이다.
        이 자리에 필요한 건 "이 텍스트가 그것이다"라는 **표시**뿐이라 태그만 남긴다.
        태그 낱말 자체가 지시를 진다(withheld/backstory) — 선언이 멀어진 값을 그걸로 치른다.

        ★되돌릴 땐 길이가 아니라 **태그 낱말**부터: [backstory] → [backstory — residue only]
          → (그래도 안 되면) 프레임 복귀. 길이부터 되돌리면 없앤 이중 투입이 그대로 돌아온다.

        은닉은 **여기가 첫 배선**이다. 구 코드는 필드 키(RENDERER_STRIP_KEYS)와 대괄호 마커
        (strip_hidden_markers)만 막고 **섹션은 그냥 통과**시켜, v7 `### Secrets`가 렌더러
        프로필로 새고 있었다(내심 콜에서만 막히던 역전). 드롭이 아니라 감싸기인 이유:
        드롭하면 비밀이 **행동을 물들이지 못한다** — 값은 발설이 아니라 회피의 모양에 있다.
        설계: 파티쳇수정/npc_sheet_ingest_spec_2026-09-02.md §7-B·§7-C
        """
        if not demote_background:
            return sec_text
        _fam = _section_family(sec_name)
        if _fam == "hidden":
            return f"[withheld]\n{sec_text}\n[/withheld]"
        if _fam == "background":
            return f"[backstory]\n{sec_text}\n[/backstory]"
        return sec_text

    # _preamble 먼저 (있고 비어있지 않으면)
    preamble = parsed.get("_preamble", "")
    if preamble and preamble.strip():
        result_parts.append(preamble)

    # _CORE 우선 매칭 — identity → rules (가족 판정, 번호 접두·자유 명명 무관)
    for core_name in _CORE_FAMILIES:
        for sec_name, sec_text in parsed.items():
            if sec_name == "_preamble" or sec_name in included:
                continue
            if _section_family(sec_name) == core_name:
                result_parts.append(_maybe_frame(sec_name, sec_text))
                included.add(sec_name)
                break

    # 나머지 모든 섹션 (parsed dict 순서대로)
    for sec_name, sec_text in parsed.items():
        if sec_name == "_preamble" or sec_name in included:
            continue
        result_parts.append(_maybe_frame(sec_name, sec_text))
        included.add(sec_name)

    result = "\n\n".join(result_parts)

    if len(result) > _MAX_TOTAL_PER_NPC:
        result = result[:_MAX_TOTAL_PER_NPC].rstrip()

    return result


# ⛔[2026-07-28 삭제] get_npc_full_profiles — "비밀 제거 없는 전문" 조립기.
#   호출처 0(grep 확인). Theoria(분석)는 프로필 전문이 아니라 get_npc_roster의
#   **인물당 첫 줄 50자 요약**만 받는 구조라 이 함수가 쓰일 자리가 없었다.
#   렌더러용은 get_npc_renderer_profiles(비밀 스트립 + {{char}} 치환 + 관찰 병기)가 정본.
#   ★비밀이 제거되지 않는 경로였으므로, 되살릴 땐 스트립 여부를 먼저 결정할 것.


def get_npc_renderer_profiles(channel_id: str, names: list, scene_type: str = "normal", user_mask: str = "") -> str:
    """P5: Renderer용 NPC 프로필 (비밀/숨겨진 정보 제거). **프로필 조립의 유일한 정본**
    (2026-07-28: 쌍이던 get_npc_full_profiles는 호출처 0으로 삭제).

    [2026-07-13] user_mask: RisuAI 관례 플레이스홀더 치환용 — 외부 시트의 {{char}}/{{user}}가
    리터럴로 프롬에 새지 않게. 미지정 시 {{user}}는 보존(정보 손실 방지), {{char}}는 항상 치환."""
    npcs = get_npcs(channel_id)
    parts = []
    for name in names:
        key = domain_manager._find_npc_key(npcs, name) or name
        raw = npcs.get(key)
        if not raw:
            continue
        data = get_npc_context_for_renderer(channel_id, key)
        if not data:
            continue
        name = key
        desc = _get_npc_desc(data)
        desc = _select_profile_sections(desc, scene_type, demote_background=True)
        header = f"### {name}"
        meta_parts = []
        if data.get("role"):
            meta_parts.append(f"역할: {data['role']}")
        # [2026-07-28] world_tree 우선 — 인물이 움직였는데 등록 시점 위치가 표시되던 것 해소
        _cur_loc = get_npc_current_location(channel_id, name, data)
        if _cur_loc:
            meta_parts.append(f"위치: {_cur_loc}")
        if data.get("personality"):
            meta_parts.append(f"성격: {data['personality']}")
        if data.get("tone") or data.get("speech"):
            meta_parts.append(f"말투: {data.get('tone') or data.get('speech')}")
        if data.get("appearance"):
            meta_parts.append(f"외형: {data['appearance']}")
        meta_line = " | ".join(meta_parts)
        if meta_line:
            profile_text = f"{header}\n**[{meta_line}]**\n{desc}"
        else:
            profile_text = f"{header}\n{desc}"
        _src_r = str(raw.get("source", "")).lower() if isinstance(raw, dict) else ""
        # 세션 즉석 NPC + 면모(정체성/불씨/면모)가 증류됐으면 → 면모 시트로 대체 렌더(주력).
        # 아직 증류 전이면 위의 seed description 그대로. (Fate-하이브리드 시트)
        # [2026-07-13 manual 동결] manual도 lore처럼 원문 렌더 — 면모 대체가 수제 프로필
        # (### Voice/Hard Rules)을 5줄 시트로 갈아치우던 충돌 수리 (재작성 동결과 짝).
        _aspects = raw.get("aspects") if isinstance(raw, dict) else None
        _has_aspect = bool(raw.get("high_concept") or raw.get("trouble") or (isinstance(_aspects, list) and _aspects))
        if _src_r not in ("lore", "manual") and _has_aspect:
            _lines = [header]
            if raw.get("high_concept"):
                _lines.append(f"**[정체성]** {raw['high_concept']}")
            if raw.get("trouble"):
                _lines.append(f"**[불씨]** {raw['trouble']}")
            if isinstance(_aspects, list) and _aspects:
                _lines.append("**[면모]** " + " · ".join(str(a) for a in _aspects))
            if data.get("appearance"):
                _lines.append(f"**[외형]** {data['appearance']}")
            if data.get("role"):
                _lines.append(f"**[역할]** {data['role']}")
            profile_text = "\n".join(_lines)
        elif _src_r not in ("lore", "manual"):
            # 세션 NPC + 아직 면모 증류 전 + desc 없음(플레이스홀더 숨김) → 관찰로 폴백.
            _obs_s = raw.get("play_observed") if isinstance(raw, dict) else None
            if _obs_s and str(_obs_s).strip() and not str(desc).strip():
                profile_text = f"{header}\n{str(_obs_s).strip()[-600:]}"
        # 로어/수제(manual) NPC: 원문 시트는 동결하되 플레이 중 관찰(play_observed)을
        # 별도 섹션으로 렌더 → 작가 설정 권위 보존 + 세션 중 드러난 새 면모를 장기기억으로 축적.
        _obs = raw.get("play_observed") if isinstance(raw, dict) else None
        if _src_r in ("lore", "manual") and _obs and str(_obs).strip():
            profile_text += f"\n**[플레이 중 관찰]**\n{str(_obs).strip()[-600:]}"
        # [2026-07-13] 외부 시트 플레이스홀더 치환 ({{char}}=NPC 자신, {{user}}=현재 PC 가면)
        if "{{" in profile_text:
            profile_text = profile_text.replace("{{char}}", name).replace("{{Char}}", name)
            if user_mask:
                profile_text = profile_text.replace("{{user}}", user_mask).replace("{{User}}", user_mask)
        parts.append(profile_text)
    return "\n\n".join(parts)


def get_npc_names_only(channel_id: str, exclude: list, include_provisional: bool = False) -> str:
    """지정된 NPC 제외한 나머지의 이름만 반환 (렌더러 배경 버킷).
    [T-B] 기본적으로 provisional(1회성 등 자동 NPC)은 배경 로스터에서 접는다.
    현재 장면 NPC(relevant_npcs)는 이미 exclude로 빠진 뒤 풀 프로필로 렌더되므로 영향 없음."""
    npcs = get_npcs(channel_id)
    # DAI 이름 → 저장 키 해상도
    resolved_exclude = set()
    for ex in exclude:
        key = domain_manager._find_npc_key(npcs, ex)
        resolved_exclude.add(key if key else ex)
    # [2026-08-11 사망 파이프라인] 이 줄은 렌더러가 "가용 캐스트"로 읽는 자리다 —
    #   dead가 섞여 있으면 부를 수 있는 사람 명부에 시체가 앉아 있는 셈.
    #   대신 **삭제하지 않고 별도 1줄로 옮긴다**: 죽음 사실을 발효·히스토리 원문의
    #   재독 확률에 맡기지 않고 구조로 잔존시키기 위해(LLM 콜 0, 한 줄).
    #   down은 표기하지 않는다 — 능동 제외만 하고, 상태 판단은 분석 콜의 몫.
    remaining, departed = [], []
    for name, data in npcs.items():
        if get_npc_status(data) == "dead":
            departed.append(name)
            continue
        if name in resolved_exclude:
            continue
        if include_provisional or get_npc_tier(data) == "established":
            remaining.append(name)
    lines = []
    if remaining:
        lines.append("기타 NPC: " + ", ".join(remaining))
    if departed:
        lines.append("Departed: " + ", ".join(departed))
    return "\n".join(lines)


def get_npc_recency_reminders(channel_id: str, npc_names: list) -> str:
    """활성 NPC의 말투 + 핵심 제약을 compact하게 생성. Recency 슬롯 주입용.

    Lost-in-the-Middle 대응: Slot 7 프로필이 중간에 묻히므로 핵심만 recency에 echo.
    hybrid(Voice 섹션 보유): echo 스킵 — Slot 7 전문이 시드. legacy: tone echo 유지.
    """
    if not channel_id or not npc_names:
        return ""
    voice_lines = []
    constraint_lines = []
    for name in npc_names:
        data = get_npc(channel_id, name)
        if not data:
            continue
        # --- Voice ---
        # [2026-08-10] hybrid 시트는 Voice recency 재주입 **스킵** (레티어스 판정).
        #   Voice 전문이 Slot 7로 이미 가는데 앞 220자 고정 조각을 생성 최근접에 매턴
        #   반복하면, v5류 시트가 "고정 표본은 반복된다"며 지운 예시-대사를 시스템이
        #   재도입하는 꼴 — 게다가 카메라 모놀로그 레지스터는 장면 대사 레지스터가
        #   아니고, 조각은 항상 첫 화제(복장)다. 07-28에 같은 사유(중복·대표성 없음)로
        #   Slot 17 quirks 3중 주입을 지웠고, 오늘 2중의 나머지 반쪽을 접는다.
        #   tone-only 레거시는 유지 — tone은 Slot 7에 원문이 없어 echo가 유일한 상기
        #   (08-02 위임형 헤더 하에서 무해). 말투 표류 관측 시 이 분기 복원이 롤백.
        desc = _get_npc_desc(data)
        if not _is_hybrid_profile(desc):
            tone = data.get("tone", "")
            if tone:
                voice_lines.append(f"- {name}: {tone}")
        # --- Constraints ---
        constraints = data.get("constraints", "")
        if constraints:
            constraint_lines.append(f"- {name}: {constraints}")
    parts = []
    if constraint_lines:
        # em-dash 쓸이(커미션 전환규칙 ⑦). firmness 자체는 §7에서 정당 — 문구는 그대로.
        parts.append("[NPC HARD RULES: VIOLATING THESE = HALLUCINATION]\n" + "\n".join(constraint_lines))
    if voice_lines:
        # [2026-08-02] 구 헤더 "[NPC Voice — match these speech patterns]"가 증상의 직접 원인.
        #   tone 필드는 **한국어 묘사문**("임상적이고 따뜻하고 사무적인 어조")이라, "이 패턴을
        #   맞춰라"로 받으면 렌더러가 그 형용사를 **그대로 서술**한다 — 실관측:
        #   "말을 거는 톤이었다. 임상적이고, 따뜻하고, 사무적이었다."
        #   게다가 여기는 Slot 33(recency)이라 생성 최근접이다.
        #   theoria L563이 이미 진단을 적어 뒀다 — "Korean here gets transcribed verbatim
        #   into prose = BUG". 그 ENGLISH-ONLY 목록에 tone만 빠져 있었다.
        #   ⚠기존 DB 값이 이미 형용사 나열이므로 생성 프롬프트 수정만으론 안 낫는다.
        #   주입 지점에서 **읽는 법**을 계약으로 준다.
        # [재작성] 초판이 "never as the description itself: no sentence names the tone,
        #   lists its adjectives, or reports how the speaking felt"였다. 형용사 나열을
        #   막으려고 **동사 3연 나열**을 쓴 셈 — 커미션 전환규칙 ①(명령→초대)·④(실패를
        #   명명하지 마라)에 어긋나고, 여기가 recency 자리라 그 캐던스가 산문에 미러링될
        #   자리이기도 하다. 계약은 그대로 두고 방향만 뒤집는다: 금지가 아니라 **위임**.
        parts.append(
            "[NPC Voice]\n"
            "Notes for the writer on how each one sounds. They stay on your side of the page: "
            "the reader meets the voice in the line itself, in word choice, sentence length, "
            "where it breaks, and what gets asked or held back.\n"
            + "\n".join(voice_lines)
        )
    return "\n\n".join(parts)


def _extract_voice_summary_from_section(name: str, voice_section: str, cap: int = 220) -> str:
    """Voice 섹션을 recency에 다시 얹을 짧은 발췌.

    ⏸[2026-08-10] 휴면(호출 0) — hybrid echo 스킵으로 유일 호출 제거. 롤백 대비 보존,
    orphan 아님(staged).

    [2026-07-28] 구 코드는 **따옴표로 시작하거나 `~`로 끝나는 줄**을 사냥해 최대 3줄을
    이어붙였다. Voice를 1인칭 산문으로 쓰는 시트(섹션 전체가 목소리 겸 인물 설명)에서는
    그런 줄이 없거나, 있어도 `"What I'm bad at~"` 같은 **소제목**이 뽑혀 그 한 줄이
    "이 인물의 목소리"로 강조되는 역효과가 났다.
    Voice 섹션은 앞부분부터가 이미 그 인물의 말투다 — 사냥하지 말고 앞을 쓴다.
    """
    body = "\n".join(
        l.strip() for l in (voice_section or "").split("\n")
        if l.strip() and not l.strip().startswith("###")
    ).strip()
    if not body:
        return ""
    excerpt = body[:cap].rstrip()
    if len(body) > len(excerpt):
        # 문장 중간에서 끊기면 마지막 종결부까지만
        _cut = max(excerpt.rfind("."), excerpt.rfind("~"), excerpt.rfind("?"), excerpt.rfind("!"))
        if _cut > cap * 0.5:
            excerpt = excerpt[:_cut + 1]
    return f"- {name}: {excerpt}"


# =========================================================
# [2026-08-17] 시트 요지 — 목소리 조각 (속마음 콜 접지)
# =========================================================
# 병: 💭 전용 콜(turn_mail)의 per-NPC 재료는 psyche(해석층) + 상태층(soma·toward_pc)뿐이었다.
#   "지금 무엇이 움직이는가"는 있고 **"이 사람이 어떻게 말하는가"가 없다** → 인물이 달라도
#   내심의 목소리가 같아진다(시트 없는 목소리 = 화자 한 명).
# 왜 이 모듈인가: 섹션 파서(`_parse_sections`)·헤더 깊이 판정·은닉 마커·RENDERER_STRIP_KEYS가
#   전부 여기 산다. 소비자 쪽에서 시트를 다시 파싱하면 "어디까지가 비밀인가"가 두 벌이 되고,
#   그때부터 한쪽만 고쳐진다(=조용히 새는 날).
# ⚠ 이 함수는 **secret_ledger 대조를 하지 않는다** — 그건 채널 상태(런타임)고 소비자의 일이다.
#   여기서 막는 것은 시트 **구조상** 은닉인 것: v6 `### Secrets` 섹션 · `[Secret]` 마커 ·
#   RENDERER_STRIP_KEYS 계열 필드. 두 겹은 층이 다르고, 둘 다 필요하다.
# ⚠ Direction(v6)은 일부러 뺐다 — 발화조건 붙은 **행동 규칙**이라, 내심을 쓰는 콜에
#   넣으면 "이렇게 하라"는 지시로 읽힌다(목소리가 아니라 연출 주문).

# 목소리·은닉 사전은 **위 §섹션 가족 판정 블록으로 이관**(2026-09-02) — 소비자가 여기
# 하나뿐이라 렌더러 프로필이 혜택을 못 받던 것을 공용화. 판정은 `_section_family()`.

VOICE_DIGEST_CHARS = 400      # 요지 총량 캡 — **코드 상수**(env 레버 신설 안 함)
_VOICE_FRAGMENT_CHARS = 160   # 조각 하나 캡


def _voice_trim(text: str, cap: int) -> str:
    """캡에서 자르되 문장 중간이면 마지막 종결부까지만(발췌가 말을 끊지 않게)."""
    t = str(text or "").strip()
    if len(t) <= cap:
        return t
    cut = t[:cap].rstrip()
    _end = max(cut.rfind("."), cut.rfind("?"), cut.rfind("!"),
               cut.rfind("~"), cut.rfind("다 "), cut.rfind("…"))
    if _end > cap * 0.5:
        cut = cut[:_end + 1]
    return cut.rstrip()


def build_voice_digest(data: Dict[str, Any], name: str = "",
                       cap: int = VOICE_DIGEST_CHARS) -> List[str]:
    """시트 → **목소리 조각 목록**(라벨 붙은 짧은 문자열). 재료가 없으면 [].

    담는 것: 말투(tone)·성격 한 줄(personality)·핵심 트레잇/방백 발췌.
    빼는 것: 은닉 섹션·비밀 필드·배경/설정(과거는 목소리가 아니다)·관계표·수치.

    반환이 문자열 한 덩어리가 아니라 **리스트**인 이유: 소비자가 원장(secret_ledger)과
    대조해 **조각 단위로 떨어뜨릴** 수 있어야 한다. 미리 이어 붙이면 한 문장이 비밀에
    닿았을 때 선택지가 "전부 버리기"뿐이다.
    값이 없는 라벨은 만들지 않는다(빈 라벨은 재료가 아니라 소음 — 기존 관례).
    `name`이 오면 외부 시트 플레이스홀더 `{{char}}`를 치환한다(v6 중심문장이 이 표기를 쓴다).
    렌더러 경로(`get_npc_renderer_profiles`)와 같은 규율 — 리터럴이 프롬에 새지 않게.
    """
    if not isinstance(data, dict):
        return []

    frags: List[str] = []
    seen: set = set()
    total = 0

    def _add(label: str, text: Any) -> None:
        nonlocal total
        if total >= cap:
            return
        t = _clean_markdown(re.sub(r"\s+", " ", str(text or ""))).strip()
        if name:
            t = t.replace("{{char}}", str(name)).replace("{{Char}}", str(name))
        if len(t) < 4:
            return
        _key = t[:40].lower()
        if _key in seen:
            return                      # 같은 값이 필드와 섹션 양쪽에 있는 시트가 흔하다
        seen.add(_key)
        _lab = str(label or "").strip().lower()[:24] or "voice"
        _room = cap - total - len(_lab) - 2          # 캡은 **라벨 포함** 총량이다
        t = _voice_trim(t, min(_VOICE_FRAGMENT_CHARS, max(1, _room)))
        if len(t) < 4:
            return
        # 라벨 구분자는 기존 material 과 같은 `k: v` — 엠대쉬를 새로 들이지 않는다
        # (내심은 한국어 산문으로 나가고, 재료의 문장부호는 그대로 베껴지는 축이다).
        _frag = f"{_lab}: {t}"
        frags.append(_frag)
        total += len(_frag)

    # ① 구조화 필드(= `_extract_structured_fields`가 시트에서 이미 증류해 둔 한 줄).
    #    비밀 계열 키는 애초에 읽지 않는다(RENDERER_STRIP_KEYS와 같은 명단).
    for _k, _lab in (("tone", "speech"), ("personality", "core")):
        if _k in RENDERER_STRIP_KEYS:
            continue
        _add(_lab, data.get(_k))

    # ② 시트 섹션 — 은닉 판정이 목소리 판정보다 앞선다.
    desc = strip_hidden_markers(_get_npc_desc(data))
    if desc:
        for _name, _body in _parse_sections(desc).items():
            if total >= cap:
                break
            if _name == "_preamble":
                continue
            # [2026-09-02] 자체 루프 → 공용 관문(_section_family). 판정 순서(은닉 우선)는
            #   그 안에 고정돼 있고, 번호 접두(`## 6. Speech Style & Tone`)도 거기서 벗겨진다.
            if _section_family(_name) != "voice":
                continue
            _text = re.sub(r'^#{1,6}\s+.*', '', _body, count=1).strip()
            _add(_name, _text)

    return frags


# =========================================================
# NPC 개명 — 주 용도는 "모브가 이름을 얻는 것"
# =========================================================
# 함수명이 "정체 발각"이라 극적 폭로처럼 읽히지만, 실무상 대부분은
#   `경비병 #2A` → `한스`
# 처럼 **몹 태그가 고유명으로 승격**되는 흔한 사건이다. 극 중에 명명 장면이 따로 있는 것도,
# 명명을 저장하는 별도 경로가 있는 것도 아니라서 코드가 대신 처리한다.
# → 그래서 "드물게 일어나는 특수 이벤트"가 아니라 **자주 도는 통로**로 보고 다뤄야 한다.
#   (2026-07-28: 여기서 태도 이관이 매번 실패하고 있었다 = 몹이 이름을 얻는 순간마다
#    그 몹으로 쌓은 관계가 0으로 리셋됐다는 뜻.)

def handle_identity_reveal(channel_id: str, old_name: str, new_name: str, reason: str = "") -> str:
    """NPC 개명 (OldName → NewName). 몹 태그 → 고유명 승격이 주 용도.

    본체(npc_data)는 copy()로 통째 이동하므로 appear_count·play_observed·static_traits 등은
    자동으로 따라간다. 별도 도메인(태도/지식/각인/관계 엣지)은 아래에서 손으로 옮긴다.
    """
    if old_name == new_name: return "⚠️ 이름이 동일합니다."
    
    # [Anti-Gravity] Mob Tag Handling
    # If the user tries to rename "Patient" to "John", but we only have "Patient #1A",
    # we might want to auto-detect. 
    # But safer is to assume Exact Match first.
    
    npc_data = get_npc(channel_id, old_name)
    if not npc_data:
        # Fallback: Check if there's a unique tagged version?
        # (Optional, skipping for safety)
        pass
    if not npc_data:
        # 혹시 이미 바뀌었거나 로어 NPC일 수 있음.
        # 로어 NPC라면 새 세션 NPC 항목을 생성?
        # 여기서는 세션 데이터 내에서만 처리한다고 가정.
        return f"⚠️ NPC '{old_name}' 데이터가 없습니다."

    # [2026-07-18 배선 보강] 대상 이름 충돌 가드 — new_name이 이미 다른 엔티티면
    # 덮어쓰기(기존 한스 소멸) 대신 중단. 병합은 사람이 !npc 병합으로.
    if get_npc(channel_id, new_name):
        return f"⚠️ '{new_name}'은(는) 이미 존재하는 NPC입니다. 병합이 필요하면 !npc 병합을 사용하세요."

    # 데이터 복사 및 메타데이터 추가
    new_data = npc_data.copy()

    # [FIX] Source 보존 (기본값 보존)
    if "source" not in new_data:
        new_data["source"] = "session"

    new_data["identity_history"] = new_data.get("identity_history", [])
    new_data["identity_history"].append({
        "old_name": old_name,
        "revealed_at": time.strftime('%Y-%m-%d %H:%M'),
        "reason": reason
    })

    # [2026-07-18 배선 보강] 구명을 aliases로 보존 — 과거 기록·발효기억 속 "경비병 #2A"
    # 참조가 domain_manager._find_npc_key aliases 매칭으로 새 키에 해소되도록.
    _aliases = new_data.get("aliases")
    _aliases = list(_aliases) if isinstance(_aliases, list) else []
    if old_name not in _aliases:
        _aliases.append(old_name)
    new_data["aliases"] = _aliases

    # 새 항목 생성
    update_npc(channel_id, new_name, new_data)

    # [2026-07-18 배선 보강] world_tree presence 이관 (구명 제거 + 신명 배치)
    try:
        import world_tree
        _loc = world_tree.get_npc_location(channel_id, old_name)
        world_tree.remove_npc_presence(channel_id, old_name)
        if _loc:
            world_tree.set_npc_location(channel_id, new_name, _loc)
    except Exception:
        pass
    
    # [2026-07-28 순서 버그 수리] 구 코드는 **delete_npc(구명) 뒤에** 태도를 읽었다.
    #   delete_npc는 npc_attitudes/npc_knowledge도 함께 지우므로 get_npc_attitude가 항상 None →
    #   `if att:`가 영영 False = **태도 이관이 매번 조용히 실패**했다.
    #   결과: 정체 발각(가면 NPC의 핵심 서사 이벤트)마다 PC와 쌓은 depth/tension이 전량 소실.
    #   게다가 지식(knows/secrets_held/suspects)은 이관 코드 자체가 없었다.
    # 처방: 삭제 **전에** 통째로 캡처 → 삭제 → 새 이름으로 dict 그대로 복원.
    #   (update_npc_attitude는 attitude/reason만 받아 depth·tension을 잃으므로 직접 대입한다.)
    _old_att = get_npc_attitude(channel_id, old_name)
    _old_know = None
    _old_imprint = None
    try:
        _old_know = domain_manager.get_npc_knowledge(channel_id).get(old_name)
    except Exception:
        pass
    try:
        _old_imprint = (domain_manager.get_npc_imprints(channel_id) or {}).get(old_name)
    except Exception:
        pass

    # 구 항목 제거 (선택적: Redirect를 남길 수도 있으나, 혼동 방지 위해 제거가 깔끔)
    delete_npc(channel_id, old_name)

    if isinstance(_old_att, dict) and _old_att:
        try:
            _d = domain_manager.get_domain(channel_id)
            _atts = _d.setdefault("npc_attitudes", {})
            _moved = dict(_old_att)
            _reason = str(_moved.get("reason", "") or "").strip()
            _moved["reason"] = (_reason + " (개명)").strip()
            _atts[new_name] = _moved            # depth/tension/trajectory 통째 이관
            _atts.pop(old_name, None)
            domain_manager.save_domain(channel_id, _d)
            domain_manager._mirror_relation(channel_id, new_name, _moved)
            logger.info("[개명] 태도 이관 %s → %s (depth=%s)",
                        old_name, new_name, _moved.get("depth"))
        except Exception as _e_att:
            logger.warning("[개명] 태도 이관 실패: %s", _e_att)

    if isinstance(_old_know, dict) and _old_know:
        try:
            domain_manager.update_npc_knowledge(channel_id, new_name, _old_know)
            logger.info("[개명] 지식 이관 %s → %s (%d knows)",
                        old_name, new_name, len(_old_know.get("knows") or []))
        except Exception as _e_kn:
            logger.warning("[개명] 지식 이관 실패: %s", _e_kn)

    # 각인·관계엣지·감정이력 — 이름을 키로 붙들고 있는 부수 저장소 전부(공용 헬퍼).
    # 구 이름은 위 delete_npc에서 이미 지워졌으므로, 남아 있는 부수 데이터만 옮겨진다.
    try:
        if _old_imprint:
            _d2 = domain_manager.get_domain(channel_id)
            _d2.setdefault("npc_imprints", {})[old_name] = _old_imprint
            domain_manager.save_domain(channel_id, _d2)
        _migrated = domain_manager.migrate_npc_side_data(channel_id, old_name, new_name)
        if _migrated:
            logger.info("[개명] 부수 이관 %s → %s: %s", old_name, new_name, ", ".join(_migrated))
    except Exception as _e_er:
        logger.debug("[개명] 부수 이관 skip: %s", _e_er)

    return f"🎭 **정체 드러남:** {old_name} ➔ {new_name}"

# =========================================================
# NPC ATTITUDE SYSTEM
# =========================================================

def update_npc_attitude(channel_id: str, npc_name: str, attitude: str, reason: str = "") -> None:
    """NPC의 PC에 대한 태도 업데이트"""
    domain_manager.update_npc_attitude(channel_id, npc_name, attitude, reason)

def get_npc_attitudes(channel_id: str) -> Dict[str, Dict]:
    """저장된 NPC 태도 조회"""
    return domain_manager.get_npc_attitudes(channel_id)

def get_npc_attitude(channel_id: str, npc_name: str) -> Optional[Dict]:
    """특정 NPC의 태도 조회"""
    return domain_manager.get_npc_attitude(channel_id, npc_name)


def delete_npc_attitude(channel_id: str, npc_name: str) -> bool:
    """NPC 태도 정보 삭제 [V10 Sprint 1: domain_manager 정식 API로 위임 (JSON+SQLite 동시)]"""
    return domain_manager.delete_npc_attitude(channel_id, npc_name)


# ⛔[2026-07-28 삭제] get_relationship_summary(44줄) — 호출처 0.
#   프롬프트용 관계 요약 문자열 조립기였으나 소비처가 사라졌다.
#   현행 표시 경로는 get_connection_display(!관계 명령), 프롬프트 급식은 une_facade 앵커.


# ⛔[2026-07-28 삭제] get_attitude_for_prompt — 호출처 0.
#   une_facade가 anchors["stored_npc_attitudes"] 원본 dict를 직접 넘기는 방식으로 대체된 잔재.


# =========================================================
# NPC CONNECTION TRACK
# =========================================================

def get_connection_display(channel_id: str) -> str:
    """전체 NPC 관계 현황 (Discord UI용)."""
    attitudes = get_npc_attitudes(channel_id)
    if not attitudes:
        return "📭 기록된 NPC 관계가 없습니다."

    att_emoji_map = {"hostile": "🔴", "unfriendly": "🟠", "neutral": "⚪",
                     "friendly": "🟢", "loyal": "💚", "devoted": "💜"}
    lines = ["🤝 **NPC 관계 현황**"]
    for npc_name, att in attitudes.items():
        depth = att.get("depth", 0)
        tension = att.get("tension", 0)
        attitude = att.get("attitude", "neutral")
        stage_info = config.get_connection_stage(depth)

        depth_filled = min(10, depth // 10)
        depth_bar = "▮" * depth_filled + "▯" * (10 - depth_filled)

        tension_str = ""
        if tension > config.NPC_TENSION_DRAMA_THRESHOLD:
            tension_str = f" ⚡{tension}"
        elif tension > 20:
            tension_str = f" 💢{tension}"

        att_emoji = att_emoji_map.get(attitude, "⚪")
        lines.append(f"**{npc_name}** {att_emoji} {attitude}")
        lines.append(f"  친밀: {depth_bar} {depth}/100 ({stage_info['name']}){tension_str}")

    return "\n".join(lines)


def get_connection_milestone_hints(channel_id: str) -> List[str]:
    """단계 경계를 넘은 NPC에 대한 서사적 힌트 반환. 1회성 (다음 턴 소비)."""
    attitudes = get_npc_attitudes(channel_id)
    if not attitudes:
        return []

    d = domain_manager.get_domain(channel_id)
    tracking = d.get("npc_milestone_tracking", {})
    hints = []
    changed = False

    for npc_name, att in attitudes.items():
        depth = att.get("depth", 0)
        current_stage = config.get_connection_stage_name(depth)
        last_stage = tracking.get(npc_name, "")

        if current_stage != last_stage and last_stage != "":
            stage_info = config.get_connection_stage(depth)
            # [2026-07-02] 문구 중립화: 단계 '하락'에도 "deepened"로 찍히던 것 — 방향 무가정.
            hints.append(
                f"[NPC Connection Shift: {npc_name}] "
                f"The relationship has crossed into different territory — {stage_info['hint_en']} "
                f"(Show through behavior. Never name stage or score in prose.)"
            )

        if current_stage != tracking.get(npc_name):
            tracking[npc_name] = current_stage
            changed = True

    if changed:
        d["npc_milestone_tracking"] = tracking
        domain_manager.save_domain(channel_id, d)

    return hints


# =========================================================
# NPC SIMULATION
# =========================================================

def _schedule_entry(npc_data: dict, slot: str) -> Tuple[str, str]:
    """NPC `schedule`의 한 시간대를 (활동, 장소)로 읽는다. 스펙 §6 R6 ① / §2.8.

    [2026-09-03 R6] 병: `schedule` 값이 두 형태다. 레거시 `{슬롯: "루틴 문자열"}`(현행
      P2가 읽던 것)과 신형 `{슬롯: {"activity":.., "location":..}}`(`!npc 일정`이 쓰는 것).
      형태 분기를 소비처마다 심으면 힌트(P2)와 스케줄 틱 두 자리에서 규칙이 갈리고,
      "힌트는 신형을 읽는데 이동은 레거시를 읽는다" 같은 조용한 어긋남이 생긴다.
    처방: 읽기 관문을 이 함수 하나로 모은다. 소비자는 형태를 몰라도 된다.
      · 레거시 문자열 -> (문자열, "") = **장소가 없으니 이동도 없다**(스펙 문면).
      · 신형이라도 location이 비면 같은 판정: 활동은 힌트로 살고 이동은 안 한다.
      · 없거나 형태가 어긋나면 ("", "").
    """
    if not isinstance(npc_data, dict) or not slot:
        return ("", "")
    sched = npc_data.get("schedule")
    if not isinstance(sched, dict):
        return ("", "")
    entry = sched.get(slot)
    if isinstance(entry, str):
        return (entry.strip(), "")
    if isinstance(entry, dict):
        return (str(entry.get("activity", "") or "").strip(),
                str(entry.get("location", "") or "").strip())
    return ("", "")


def get_npc_time_progression(channel_id: str) -> List[str]:
    """
    시간 경과에 따른 NPC 상태 변화 힌트 생성 (3-Tier Hybrid)

    Priority:
      P1: ai_session_memory.npc_summaries — AI가 세션 중 관찰/추론한 활동
      P2: NPC data의 schedule[time_slot] — 로어북/수동 등록 시 프리셋 루틴
      P3: 시간대별 일반 활동 랜덤 폴백
    """
    npcs = get_npcs(channel_id)
    if not npcs:
        return []

    world = domain_manager.get_world_state(channel_id)
    time_slot = world.get("time_slot", "오후")

    # P1: AI-observed activity from session memory
    ai_mem = domain_manager.get_session_ai_memory(channel_id)
    npc_summaries = ai_mem.get("npc_summaries", {})

    # P3 fallback: 시간대별 일반 활동
    _generic_activities = {
        "새벽": ["잠들어 있다", "이른 기상 준비", "야간 근무 마무리", "깊은 잠에 빠져 있다"],
        "오전": ["아침 식사", "일과 시작", "청소/정리", "분주하게 움직임"],
        "오후": ["업무 중", "점심 후 활동", "외출", "나른하게 휴식"],
        "황혼": ["퇴근 준비", "저녁 준비", "휴식", "하루를 정리함"],
        "저녁": ["저녁 식사", "여가 활동", "TV 시청", "술자리"],
        "심야": ["잠자리 준비", "야식", "늦은 작업", "비밀스러운 만남"]
    }
    fallback_pool = _generic_activities.get(time_slot, ["활동 중"])

    hints = []
    for npc_name, npc_data in npcs.items():
        # P1: session memory에 AI가 기록한 최근 활동/상태
        summary = npc_summaries.get(npc_name, {})
        if isinstance(summary, str) and summary:
            hints.append(f"{npc_name}: {summary}")
            continue
        if isinstance(summary, dict) and summary.get("activity"):
            hints.append(f"{npc_name}: {summary['activity']}")
            continue

        # P2: 프리셋 스케줄 (lore/manual NPC에 schedule 필드가 있을 때)
        # [2026-09-03 R6] 형태 분기(문자열/딕셔너리)는 _schedule_entry 하나가 흡수한다.
        #   신형 스케줄의 activity도 여기서 그대로 힌트가 된다. location은 힌트가 아니라
        #   스케줄 틱(orchestration)이 쓰는 재료라 여기선 읽지 않는다.
        _sch_act, _ = _schedule_entry(npc_data, time_slot)
        if _sch_act:
            hints.append(f"{npc_name}: {_sch_act}")
            continue

        # P3 랜덤 폴백 제거 (2026-07-14 경로 감사): "TV 시청/술자리" 무근거 발명 활동
        # = Contract-First 위반 + 자판기 노이즈. 근거(관찰 P1/스케줄 P2) 있는 NPC만 힌트.
        # (fallback_pool·random import는 P3 부활 대비 잔존 — 소비자는 서사 콜 ABSENT CAST)

    return hints

def clear_session_npcs(channel_id: str) -> int:
    """
    세션 전용 NPC 일괄 삭제 — lore + manual(수동 등록)은 보존.
    [2026-06-10 Fix] 기존엔 source != 'lore'만 보존 제외라 manual도 삭제됨 (세션 리셋의
    보존 정책과 불일치). 사용자 확인: 수동 추가 NPC는 살린다.
    [V10 Sprint 2: domain_manager 정식 API로 위임 (JSON+SQLite 동시)]
    Returns: 삭제된 NPC 수
    """
    return domain_manager.delete_npcs_by_source(channel_id, ("lore", "manual"))


# =========================================================
# M2: NPC 결정 페이싱 쿨다운
# =========================================================

def check_decision_cooldown(channel_id: str, npc_name: str) -> int:
    """NPC의 남은 결정 쿨다운 턴 수를 반환. 0이면 결정 가능."""
    npc_data = get_npc(channel_id, npc_name)
    if not npc_data:
        return 0
    return max(0, npc_data.get("decision_cooldown", 0))


def set_decision_cooldown(channel_id: str, npc_name: str, turns: int = 3) -> None:
    """NPC가 중대한 결정을 내린 후 쿨다운 설정."""
    npc_data = get_npc(channel_id, npc_name)
    if not npc_data:
        logger.warning(f"[DecisionCooldown] NPC '{npc_name}' not found in {channel_id}")
        return
    npc_data["decision_cooldown"] = max(0, turns)
    domain_manager.update_npc(channel_id, npc_name, npc_data)
    logger.debug(f"[DecisionCooldown] {npc_name}: cooldown set to {turns}")


def tick_all_cooldowns(channel_id: str) -> None:
    """매 턴 호출: 모든 NPC의 decision_cooldown을 1씩 감소."""
    npcs = get_npcs(channel_id)
    if not npcs:
        return
    changed = False
    for name, data in npcs.items():
        cd = data.get("decision_cooldown", 0)
        if cd > 0:
            data["decision_cooldown"] = max(0, cd - 1)
            changed = True
    if changed:
        # bulk update — update_npc 반복 호출보다 효율적
        # [V10 Sprint 2: domain_manager 정식 API로 위임 (JSON+SQLite 트랜잭션 동시)]
        domain_manager.bulk_update_npcs(channel_id, npcs)
        logger.debug(f"[DecisionCooldown] Ticked cooldowns for {channel_id}")


# =========================================================
# M5: NPC 태도 변경 쿨다운 게이트 (3턴 + 1단계)
# =========================================================

_ATTITUDE_LEVEL_REVERSE = {v: k for k, v in ATTITUDE_LEVELS.items()}


def update_npc_attitude_gated(
    channel_id: str,
    npc_name: str,
    new_attitude: str,
    current_turn: int,
    reason: str = ""
) -> str:
    """NPC 태도 변경에 쿨다운(3턴) + 최대 1단계 제한 적용.

    Returns:
        "accepted"  — 변경 승인, 저장 완료
        "cooldown"  — 3턴 미경과로 거부
        "clamped"   — 2단계 이상 점프 → ±1로 클램핑 후 저장
    """
    new_attitude = new_attitude.lower().strip()
    if new_attitude not in ATTITUDE_LEVELS:
        logger.warning(f"[AttitudeGate] Unknown attitude '{new_attitude}' for {npc_name}")
        return "accepted"  # 알 수 없는 태도는 그냥 통과 (기존 동작 유지)

    existing = get_npc_attitude(channel_id, npc_name)

    # --- Rule 1: 이전 태도가 없으면 neutral 기준 ±1 클램프 적용 ---
    if not existing or "attitude" not in existing:
        new_level = ATTITUDE_LEVELS[new_attitude]
        neutral_level = ATTITUDE_LEVELS["neutral"]  # 2
        diff = new_level - neutral_level
        if abs(diff) > 1:
            clamped_level = neutral_level + (1 if diff > 0 else -1)
            clamped_level = max(0, min(len(ATTITUDE_LEVELS) - 1, clamped_level))
            new_attitude = _ATTITUDE_LEVEL_REVERSE.get(clamped_level, "neutral")
            logger.info(f"[AttitudeGate] {npc_name}: initial → {new_attitude} (clamped from jump {diff})")
        else:
            logger.info(f"[AttitudeGate] {npc_name}: initial → {new_attitude} (accepted)")
        update_npc_attitude(channel_id, npc_name, new_attitude, reason)
        _save_attitude_turn(channel_id, npc_name, current_turn)
        try:  # [V10 적립] attitude_log — 최초 태도 확립
            import sqlite_store
            sqlite_store.append_attitude_log(channel_id, current_turn, npc_name, "", new_attitude, "initial", reason)
        except Exception:
            pass
        return "accepted"

    old_attitude = existing.get("attitude", "neutral").lower().strip()
    old_level = ATTITUDE_LEVELS.get(old_attitude, 2)  # default neutral
    new_level = ATTITUDE_LEVELS[new_attitude]

    # --- Rule 2: 3턴 쿨다운 ---
    last_turn = existing.get("last_change_turn", -999)
    if current_turn - last_turn < 3:
        logger.info(f"[AttitudeGate] {npc_name}: cooldown ({current_turn - last_turn}/3 turns)")
        return "cooldown"

    # --- Rule 3: 최대 1단계 점프 ---
    diff = new_level - old_level
    if abs(diff) > 1:
        clamped_level = old_level + (1 if diff > 0 else -1)
        clamped_level = max(0, min(4, clamped_level))
        clamped_attitude = _ATTITUDE_LEVEL_REVERSE.get(clamped_level, "neutral")
        update_npc_attitude(channel_id, npc_name, clamped_attitude, reason)
        _save_attitude_turn(channel_id, npc_name, current_turn)
        try:  # [V10 적립] attitude_log — 클램프 전이(실 1단계 변화)
            import sqlite_store
            sqlite_store.append_attitude_log(channel_id, current_turn, npc_name, old_attitude, clamped_attitude, "clamped", reason)
        except Exception:
            pass
        logger.info(
            f"[AttitudeGate] {npc_name}: {old_attitude}→{new_attitude} "
            f"clamped to {clamped_attitude} (jump {diff}→±1)"
        )
        return "clamped"

    # --- 정상 수락 ---
    update_npc_attitude(channel_id, npc_name, new_attitude, reason)
    _save_attitude_turn(channel_id, npc_name, current_turn)
    if old_attitude != new_attitude:  # [V10 적립] attitude_log — 실 전이만(no-op accept 제외)
        try:
            import sqlite_store
            sqlite_store.append_attitude_log(channel_id, current_turn, npc_name, old_attitude, new_attitude, "accepted", reason)
        except Exception:
            pass
    logger.info(f"[AttitudeGate] {npc_name}: {old_attitude}→{new_attitude} (accepted)")
    return "accepted"


def _save_attitude_turn(channel_id: str, npc_name: str, turn: int) -> None:
    """attitude 데이터에 last_change_turn을 기록.
    [V10 Sprint 1: domain_manager 정식 API로 위임 (JSON+SQLite 동시)]"""
    domain_manager.set_attitude_turn(channel_id, npc_name, turn)


# =========================================================
# N4: NPC 페르소나 스냅샷
# =========================================================

# ⛔[2026-07-28 삭제] N4 NPC 페르소나 스냅샷 서브시스템
#   apply_persona_snapshot(80줄) + get_persona_snapshot — 둘 다 호출처 0(grep 확인).
#   여기 있던 Peplau 단계 클램프는 **프롬프트 레벨에서 이미 살아 돌고 있다**
#   (relation.phase + theoria/analysis_resources의 "cannot skip stages" 지시).
#   즉 같은 규칙의 코드판 중복이었고, 죽은 쪽이 이 함수들이었다.
#   ★기능이 사라진 게 아니라 이중 구현 중 안 쓰는 쪽을 걷어낸 것.


# =========================================================
# [2026-07-22 카드3] 증류 로어 접지 — 이름 매칭 단독의 사문화 수리
# =========================================================
# 문제: analyze_character_sheet 증류 직전의 로어 접지가 **NPC 이름 리터럴 검색**이었다.
#   프로필 NPC(이름이 로어북에 있음)에선 걸리지만, 모델이 방금 지어낸 **세션 NPC 이름은
#   로어에 없으므로 영구 미스** — 정작 접지가 필요한 쪽에서만 사문화되는 구조였다.
# 수리: 3단 폴백(이름 → 동시출현 청크 → 관찰↔청크 의미 유사도) + 규칙부 발췌.
#
# [2026-07-28 임베딩 배선] 구 주석은 "임베딩 RAG는 인프라 미구축(로드맵 E)이라 범위 밖"이라
#   적혀 있었으나 **stale**이었다 — vector_search.py는 이미 있었고, 심지어 **같은 lore_chunks
#   풀**을 orchestration L1559가 벡터로 랭킹 중이었다(같은 데이터, 한쪽은 임베딩, 한쪽은 2-gram).
#   3단을 의미 유사도로 교체하고 한글 bigram은 3-b 폴백으로 강등. 1·2단은 무변경
#   (이름 리터럴=정확 신호, 동시출현=실제 플레이 증거 — 임베딩보다 강하다).
#   client 미전달·API 실패·빈 결과면 전 구간이 구 결정론 경로로 온전히 떨어진다.

_KO_TOKEN_RE = re.compile(r"[가-힣]{2,}")


def _ko_bigrams(text: str, cap: int = 400) -> set:
    """한글 2-gram 집합 — 고유명사·세계관 용어 겹침 판정용(Reader-GM 매칭 선례와 동형)."""
    grams = set()
    for tok in _KO_TOKEN_RE.findall(text or "")[:cap]:
        for i in range(len(tok) - 1):
            grams.add(tok[i:i + 2])
    return grams


async def build_distill_grounding(channel_id: str,
                                  npc_name: str,
                                  aliases: Optional[List[str]] = None,
                                  observations: str = "",
                                  seen_labels: Optional[Dict[str, int]] = None,
                                  max_chunks: int = 2,
                                  chunk_chars: int = 600,
                                  rules_chars: int = 1200,
                                  client=None) -> str:
    """증류 입력에 얹을 접지 블록(로어 발췌 + 규칙부 발췌). 없으면 빈 문자열.

    client 전달 시 3단이 임베딩 의미 유사도로 동작(공용 엔진 캐시 사용, 쿼리 1건).
    미전달이면 구 결정론 경로 그대로 — 호출부가 client를 못 주는 상황에서도 안전."""
    parts: List[str] = []

    # --- 규칙부: 청킹되지 않고 항상 로딩되는 영역(인물 불변 규칙이 사는 자리) ---
    try:
        _rules = (domain_manager.get_rules(channel_id) or "").strip()
        if _rules:
            parts.append(
                "[규칙 참고 — 이 세계의 상시 규칙. 관찰 해석의 접지로만 쓰고 문장을 통복사하지 말 것]\n"
                + _rules[:rules_chars]
            )
    except Exception:
        pass

    # --- 로어 청크: 3단 폴백 ---
    try:
        chunks = domain_manager.get_lore_chunks(channel_id) or []
        if chunks:
            names = [npc_name] + [str(a) for a in (aliases or []) if a]
            names += [n.split("(")[0].strip() for n in list(names) if "(" in n]
            names = [n for n in dict.fromkeys(names) if n]

            picked: List[str] = []       # 렌더용 발췌
            picked_idx: set = set()

            def _add(i, chunk):
                if i in picked_idx or len(picked) >= max_chunks:
                    return
                lbl = str(chunk.get("label", "") or "") if isinstance(chunk, dict) else ""
                txt = str(chunk.get("content", "") or "") if isinstance(chunk, dict) else str(chunk or "")
                if not txt.strip():
                    return
                picked_idx.add(i)
                picked.append((f"({lbl}) " if lbl else "") + txt.strip()[:chunk_chars])

            # 1단: 이름·별칭 리터럴 (프로필/로어 NPC에서 유효 — 기존 동작 보존)
            for i, chunk in enumerate(chunks):
                lbl = str(chunk.get("label", "") or "") if isinstance(chunk, dict) else ""
                txt = str(chunk.get("content", "") or "") if isinstance(chunk, dict) else str(chunk or "")
                if any(n in txt or n in lbl for n in names):
                    _add(i, chunk)
                if len(picked) >= max_chunks:
                    break

            # 2단: 동시출현 — 이 NPC가 등장한 턴들에 실제로 주입됐던 청크(빈도순)
            if len(picked) < max_chunks and seen_labels:
                for lbl, _cnt in sorted(seen_labels.items(), key=lambda kv: (-kv[1], kv[0])):
                    for i, chunk in enumerate(chunks):
                        _l = str(chunk.get("label", "") or "") if isinstance(chunk, dict) else ""
                        if _l and _l == lbl:
                            _add(i, chunk)
                            break
                    if len(picked) >= max_chunks:
                        break

            # 3단: 관찰 ↔ 청크 **의미 유사도**(임베딩). 구 bigram은 3-b로 강등.
            #   쿼리=관찰 텍스트. 청크 벡터는 공용 엔진 캐시에 남아 로어 랭킹과 공유된다.
            if len(picked) < max_chunks and observations and client is not None:
                try:
                    import vector_search as _vs_mod
                    _pool = []
                    for i, chunk in enumerate(chunks):
                        if i in picked_idx:
                            continue
                        _txt = str(chunk.get("content", "") or "") if isinstance(chunk, dict) else str(chunk or "")
                        if _txt.strip():
                            _pool.append({"content": _txt, "_idx": i})
                    if _pool:
                        _eng = _vs_mod.get_shared_engine(client)
                        _res = await _eng.search(
                            observations[:1500], _pool,
                            top_k=max(1, max_chunks - len(picked)),
                            min_score=getattr(config, "VECTOR_MIN_SCORE", 0.2),
                        )
                        for _c, _score in _res:
                            if isinstance(_c, dict) and "_idx" in _c:
                                _add(_c["_idx"], chunks[_c["_idx"]])
                                if len(picked) >= max_chunks:
                                    break
                        if _res:
                            logger.debug("[DistillGrounding] vector picked %d for %s", len(_res), npc_name)
                except Exception as _e_vs:
                    logger.debug("[DistillGrounding] vector unavailable (%s) — bigram fallback", _e_vs)

            # 3-b단(폴백): 관찰 ↔ 청크 내용 한글 bigram 겹침(세계관 용어 공유)
            if len(picked) < max_chunks and observations:
                obs_g = _ko_bigrams(observations)
                if obs_g:
                    scored = []
                    for i, chunk in enumerate(chunks):
                        if i in picked_idx:
                            continue
                        txt = str(chunk.get("content", "") or "") if isinstance(chunk, dict) else str(chunk or "")
                        ov = len(obs_g & _ko_bigrams(txt))
                        if ov >= 3:
                            scored.append((ov, i))
                    for _ov, i in sorted(scored, key=lambda x: (-x[0], x[1])):
                        _add(i, chunks[i])
                        if len(picked) >= max_chunks:
                            break

            if picked:
                parts.append(
                    "[세계관 참고 — 로어 원문 발췌. 관찰 해석의 접지로만 쓰고 시트로 문장을 "
                    "통복사하지 말 것. 관찰과 충돌하면 로어 우선]\n" + "\n---\n".join(picked)
                )
    except Exception:
        pass

    return ("\n\n".join(parts) + "\n\n") if parts else ""


# =========================================================
# [2026-08-02] C축 DRIVE — 해소되지 않은 충동 압력 (per-NPC, enum 상태기계)
# =========================================================
# ★수치 게이지를 만들지 않는다. 저장은 단계 문자열 + 턴 도장 둘뿐.
#   본은 `update_npc_attitude_gated`(위) — enum + 쿨다운 + ±1단계 클램프.
#   LLM은 "다음 단계 이름"만 내므로 델타 캡(cap_llm_delta)이 필요 없다. 캡할 수치가 없다.
#
# 저장 위치는 `npcs[name]["drives"]` = **자유 문서 컬럼**(npcs.data).
#   npc_relations는 화이트리스트 방벽이라 새 키가 조용히 증발한다(실측 확인).
#   실험 단계인 축은 자유 문서에 두고, 값이 굳으면 컬럼으로 승격한다.
#
# 압력형의 비대칭: 상승은 게이팅(천천히), 해소는 자유(빠르게), 방치는 자연 하강.

_DRIVE_ROOT = "drives"


def _drive_cfg(key, default):
    return getattr(config, key, default)


def _drive_level(stage: str) -> int:
    return int((_drive_cfg("DRIVE_STAGES", {}).get(str(stage).lower()) or {}).get("level", 0))


def _drive_stage_name(level: int) -> str:
    _map = _drive_cfg("DRIVE_LEVEL_TO_STAGE", {0: "none"})
    _max = max(_map) if _map else 0
    return _map.get(max(0, min(_max, int(level))), "none")


def get_drive(channel_id: str, npc_name: str, axis: str = "lust") -> Dict[str, Any]:
    """현재 단계 조회. 없으면 none 기준값. (읽기는 항상 안전 — 없으면 0단계)"""
    data = get_npc(channel_id, npc_name)
    if not isinstance(data, dict):
        return {"stage": "none", "level": 0, "last_change_turn": -1}
    rec = ((data.get(_DRIVE_ROOT) or {}).get(axis) or {}) if isinstance(data.get(_DRIVE_ROOT), dict) else {}
    stage = str(rec.get("stage", "none")).lower()
    if stage not in _drive_cfg("DRIVE_STAGES", {}):
        stage = "none"
    return {
        "stage": stage,
        "level": _drive_level(stage),
        "last_change_turn": int(rec.get("last_change_turn", -1) or -1),
    }


def set_drive_gated(channel_id: str, npc_name: str, target_stage: str,
                    current_turn: int, axis: str = "lust",
                    released: bool = False, reason: str = "") -> str:
    """단계 전이 관문. LLM이 제안한 단계를 코드가 클램프한다.

    Rules
      1. 하강은 항상 자유롭다 — 압력은 빠르게 빠진다(해소·중단·목표 전환).
         `released=True`면 쿨다운도 면제하고 제안 단계로 바로 내린다.
      2. 상승은 쿨다운(DRIVE_RISE_COOLDOWN) + 최대 DRIVE_RISE_MAX_STEP 단계.
      3. 같은 단계면 no-op (도장도 안 찍는다 — 자연 하강 시계를 살려두기 위해).

    Returns: "accepted" | "clamped" | "cooldown" | "unchanged" | "disabled" | "invalid"
    """
    if not _drive_cfg("DRIVE_ENABLED", False):
        return "disabled"
    stages = _drive_cfg("DRIVE_STAGES", {})
    target = str(target_stage or "").lower().strip()
    if target not in stages:
        logger.warning("[Drive] 알 수 없는 단계 %r (%s) — 무시", target_stage, npc_name)
        return "invalid"
    data = get_npc(channel_id, npc_name)
    if not isinstance(data, dict):
        return "invalid"

    cur = get_drive(channel_id, npc_name, axis)
    old_level, new_level = cur["level"], _drive_level(target)
    if new_level == old_level:
        return "unchanged"

    result = "accepted"
    if new_level > old_level:
        # --- 상승: 쿨다운 + 단계 제한 ---
        last = cur["last_change_turn"]
        cd = int(_drive_cfg("DRIVE_RISE_COOLDOWN", 2))
        if last >= 0 and current_turn - last < cd:
            logger.info("[Drive] %s/%s: cooldown (%d/%d)", npc_name, axis,
                        current_turn - last, cd)
            return "cooldown"
        step = int(_drive_cfg("DRIVE_RISE_MAX_STEP", 1))
        if new_level - old_level > step:
            new_level = old_level + step
            result = "clamped"
    elif not released and not _drive_cfg("DRIVE_RELEASE_FREE", True):
        # 해소 플래그가 없고 자유 하강도 꺼져 있으면 상승과 같은 제한
        step = int(_drive_cfg("DRIVE_RISE_MAX_STEP", 1))
        if old_level - new_level > step:
            new_level = old_level - step
            result = "clamped"

    _stage = _drive_stage_name(new_level)
    _root = data.get(_DRIVE_ROOT)
    if not isinstance(_root, dict):
        _root = {}
    _root[axis] = {"stage": _stage, "last_change_turn": int(current_turn)}
    # [2026-08-11 드라이브 부분dict 수리] update_npc는 엔트리 **통째 교체** 관문이다.
    #   구 코드는 `{_DRIVE_ROOT: _root}`만 넘겨서, 단계 전이가 일어날 때마다
    #   _PRESERVE_KEYS 밖 필드(description/desc/appear_count/_last_appear_turn/
    #   decision_cooldown/identity_history 등)가 조용히 증발했다.
    #   mark_npc_appearance와 같은 full-copy 패턴으로 통일한다.
    _new = dict(data)
    _new[_DRIVE_ROOT] = _root
    update_npc(channel_id, npc_name, _new)
    logger.info("[Drive] %s/%s: %s→%s (%s, turn=%d) %s",
                npc_name, axis, cur["stage"], _stage, result, current_turn, reason)
    return result


def tick_drive_decay(channel_id: str, current_turn: int, axis: str = "lust") -> int:
    """매 턴 호출: 무변화 DRIVE_IDLE_TURNS 턴마다 1단계 자연 하강.

    A축 감쇠(domain_manager.decay_stale_relations)와 같은 턴-종료 자리에서 돈다.
    다른 점: A축 시계는 **등장**(안 만나면 식음), C축 시계는 **무변화**(안 건드리면 가라앉음).
    압력은 만나지 않아도 스스로 가라앉는다.
    """
    idle = int(_drive_cfg("DRIVE_IDLE_TURNS", 0))
    if not _drive_cfg("DRIVE_ENABLED", False) or idle <= 0:
        return 0
    npcs = get_npcs(channel_id) or {}
    lowered = 0
    # [2026-08-11 드라이브 부분dict 수리] list() — 루프 안에서 update_npc가 돌므로
    #   (키 정규화 이사 시 del/재삽입) 라이브 dict 직접 순회는 RuntimeError 위험.
    for name, data in list(npcs.items()):
        if not isinstance(data, dict):
            continue
        rec = ((data.get(_DRIVE_ROOT) or {}).get(axis) or {}) if isinstance(data.get(_DRIVE_ROOT), dict) else {}
        if not rec:
            continue
        lvl = _drive_level(rec.get("stage", "none"))
        if lvl <= 0:
            continue
        last = int(rec.get("last_change_turn", -1) or -1)
        if last < 0 or current_turn - last < idle:
            continue
        _root = dict(data.get(_DRIVE_ROOT) or {})
        _root[axis] = {"stage": _drive_stage_name(lvl - 1),
                       "last_change_turn": int(current_turn)}
        # [2026-08-11 드라이브 부분dict 수리] 자연 하강도 같은 병이었다 —
        #   부분 dict를 넘기면 하강 한 번에 해당 NPC 시트 본문이 날아간다.
        #   data는 이 루프가 도는 npcs[name] 본인 것이므로 그대로 full-copy 한다.
        _new = dict(data)
        _new[_DRIVE_ROOT] = _root
        update_npc(channel_id, name, _new)
        lowered += 1
    if lowered:
        logger.info("[Drive] %d NPC 압력 자연 하강 (idle=%d, turn=%d)", lowered, idle, current_turn)
    return lowered
