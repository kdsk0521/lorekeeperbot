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
from typing import Dict, Any, Optional, List
import difflib
import config
import domain_manager
from npc_profile_harness import extract_static_traits

# =========================================================
# NPC SOURCE TYPES (출처 구분)
# =========================================================
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
        # Remove sections starting with [Secret], [Hidden], [비밀], [숨겨진]
        desc = re.sub(
            r'(?:\[(?:Secret|Hidden|비밀|숨겨진)[^\]]*\])[^\[]*',
            '', desc, flags=re.IGNORECASE
        )
        result["description"] = desc.strip()

    return result


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


def _extract_structured_fields(desc: str) -> Dict[str, str]:
    """NPC 프로필 텍스트에서 구조화 필드(role, location, tone, personality) 자동 추출."""
    fields = {}
    if not desc or len(desc) < 50:
        return fields

    # Role (예: "- Rank/Role: Emergency physician / Sharehouse resident (Room 2)")
    role_m = re.search(r'(?:Rank/Role|Occupation)[:\s]+(.+)', desc, re.IGNORECASE)
    if role_m:
        role_text = _clean_markdown(role_m.group(1))   # [1M remap] 필드캡 제거(시트 5k자, 한 줄이라 자연 바운드)
        fields["role"] = role_text

    # Location — Sharehouse resident (Room X) 패턴 우선
    loc_m = re.search(r'Sharehouse resident\s*\(([^)]+)\)', desc, re.IGNORECASE)
    if loc_m:
        fields["location"] = loc_m.group(1).strip()
    else:
        # Fallback: Occupation에서 장소명만 추출
        occ_m = re.search(r'(?:Occupation|Affiliation)[:\s]+(.+)', desc, re.IGNORECASE)
        if occ_m:
            occ_text = occ_m.group(1).strip()
            # "Dungeon 25 convenience store owner" → "Dungeon 25"
            place_m = re.search(r'(Dungeon\s*\d+|Error\s*\d+|Sunset\s*Villa|Sage\'s Chamber)', occ_text, re.IGNORECASE)
            if place_m:
                fields["location"] = place_m.group(1).strip()

    # Speech/Tone (예: "**Tone:** Low, tired, flat.")
    # [2026-07-28] v2 풀시트는 같은 정보를 `- Speaking Style:` / `- 말투:`로 쓴다 — 편입.
    tone_m = re.search(
        r'\*?\*?(?:Tone|Speaking\s*Style|Speech\s*Style|말투|어조)\*?\*?[:\s]+(.+)', desc, re.I)
    if tone_m:
        fields["tone"] = _clean_markdown(tone_m.group(1))   # [1M remap] 캡 제거

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
    if (desc_text and len(desc_text) >= 100
            and not data.get("static_traits") and not _prev.get("static_traits")):
        static_traits = extract_static_traits(name, desc_text)
        if static_traits:
            data["static_traits"] = static_traits
            logger.info(f"[NPC] Static traits extracted for '{name}': {static_traits}")
    domain_manager.update_npc(channel_id, name, data)

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

    # 2. Containment Check (Only for names >= 3 chars)
    if len(n_norm) >= 3:
        for name in existing_npcs:
            e_norm = domain_manager._normalize_npc_name(name).lower()
            if n_norm in e_norm or e_norm in n_norm:
                return name

    # 3. Fuzzy Match
    matches = difflib.get_close_matches(new_name, existing_npcs.keys(), n=1, cutoff=threshold)
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
    """
    count = 0
    for npc in npc_list:
        name = npc.get("name", "").strip()
        if not name: continue
        
        # [Check Duplicate]
        sim_name = find_similar_npc(channel_id, name)
        if sim_name:
             logger.info(f"[NPC] 로어 NPC '{name}' -> 유사한 기존 NPC '{sim_name}' 발견. 병합/스킵 처리.")
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



def add_manual_npc(channel_id: str, name: str, description: str, gender: str = None, race: str = None, **kwargs) -> bool:
    """
    수동으로 NPC 추가 (!npc add 명령 등).

    Args:
        channel_id: 채널 ID
        name: NPC 이름
        description: NPC 설명
        **kwargs: 추가 필드 (role, personality 등)

    Returns:
        성공 여부
    """
    if not name.strip():
        return False

    data = {
        "description": description,
        "source": SOURCE_MANUAL,
        "registered_at": time.strftime('%Y-%m-%d %H:%M'),
    }
    if gender: data["gender"] = gender
    if race: data["race"] = race
    data.update(kwargs)

    real_name = name.strip()
    
    # [Check Duplicate]
    sim_name = find_similar_npc(channel_id, real_name)
    if sim_name and sim_name.lower() != real_name.lower():
        # Warn user? Currently returns bool. 
        # But we assume the user intends to overwrite if they type exact name.
        # If they type SIMILAR name, they might mean the existing one or a new one.
        # Strict deduplication: prevent similar.
        # But maybe they want "Guard A" and "Guard B".
        # Let's only block if very high similarity or warn?
        # For manual add, we should probably allow it BUT maybe standardise name?
        pass

    update_npc(channel_id, real_name, data)
    logger.info(f"[NPC] 수동 NPC 추가: {name}")
    return True


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
    """세션(Scene) NPC 이름 목록 반환 (Background Extraction용)"""
    return list(get_session_npcs(channel_id).keys())


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


def mark_npc_appearance(channel_id: str, name: str, turn: int) -> None:
    """[T-A] NPC가 이 턴 실제 등장했음을 기록(구별 턴만 카운트 = turn dedup).
    lore/manual은 tier 계측 불필요(항상 established)라 스킵. 순수 부기, LLM 콜 없음."""
    data = get_npc(channel_id, name)
    if not isinstance(data, dict):
        return
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
        # [D-A] 분석(Theoria)은 전체 캐스트가 필요 → 접기 없이 폴백만(빈 description → 관찰/면모)
        desc = _npc_desc_fallback(data)
        blurb = _roster_blurb(desc, data)
        role = data.get("role", "")
        location = data.get("location", "")
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
_CORE_SECTIONS = ["Identity", "Hard Rules"]

# ⛔[2026-07-28 삭제] _SCENE_SECTION_MAP — 장면 유형별 섹션 화이트리스트.
#   정의만 있고 **참조처 0**이었다(grep 확인). `_select_profile_sections(scene_type=...)`의
#   인자는 남아 있지만 내부에서 쓰지 않는다 = 전투/사교/친밀/탐험 구분 없이 전 섹션 상시 노출.
#   레티어스 결정(2026-07-28): **이 기능은 만들지 않는다** → 오해 유발 코드라 제거.
#   되살릴 일이 생기면 git 이력에서. 당시 키: combat/social/intimate/exploration/summary/normal.
#   ※ scene_type 인자 자체는 호출부 호환 위해 존치(무해).

_MAX_TOTAL_PER_NPC = 50000  # [Sprint L 2026-04-29] 사고 방어 안전망만. 정상 운영 도달 X.

# 배경/설정류 섹션 키 — 렌더러(Pro)엔 "직접 서술 금지, 현재 잔여로만" 프레임으로 제자리 강등.
# Theoria(Flash 분석)는 원본 유지 (분석엔 배경 전체 필요). drop 아니라 wrap → Sprint L 헤더자유도 무손상.
_BACKGROUND_SECTION_KEYS = ("background", "backstory", "biography", "배경", "설정", "내력", "과거", "생애")


def _is_background_section(name: str) -> bool:
    n = (name or "").lower()
    return any(k in n for k in _BACKGROUND_SECTION_KEYS)


def _is_hybrid_profile(desc: str) -> bool:
    """Voice 섹션(1인칭 목소리 블록)을 가진 시트인가.
    [2026-07-28] h4형 시트(`#### Voice`)도 인정 — 섹션 깊이 판정과 보조를 맞춘다."""
    return bool(re.search(r'^#{3,4}(?!#)\s+Voice\b', desc or "", re.MULTILINE))


def _extract_voice_section(desc: str) -> str:
    """프로필에서 ### Voice 섹션 텍스트만 추출. 없으면 빈 문자열."""
    sections = _parse_sections(desc)
    return sections.get("Voice", "")


def _section_header_depth(desc: str) -> int:
    """이 프로필에서 **실질 섹션 구분자로 쓰인 헤더 깊이**를 판정한다(3 또는 4).

    [2026-07-28] 외부 캐릭터 시트에는 두 계열이 있다:
      · h3형 — `### Identity` `### Voice` … (h3가 여러 개, 우리 시트 관례)
      · h4형 — `### 캐릭터 이름` 하나 아래 `#### Basic Info` `#### Background` … 16개
               (커뮤니티 풀시트 템플릿 관례)
    구 코드는 `###`만 잘라서 h4형이 **통째로 한 덩어리**가 됐다. 12,000자짜리 시트에서
    `#### Background`가 섹션으로 안 잡히니 "직접 낭독 금지" 프레임도 안 붙고,
    코어 섹션 정렬도 무의미해졌다.
    판정: h3가 2개 이상이면 h3형. h3가 1개 이하인데 h4가 2개 이상이면 h4형.
    """
    h3 = len(re.findall(r'^###(?!#)\s+\S', desc or "", re.MULTILINE))
    h4 = len(re.findall(r'^####(?!#)\s+\S', desc or "", re.MULTILINE))
    return 4 if (h3 <= 1 and h4 >= 2) else 3


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
    if not desc or '###' not in desc:
        return (desc[:_MAX_TOTAL_PER_NPC] if desc else "")

    parsed = _parse_sections(desc)
    if len(parsed) <= 1:
        return desc[:_MAX_TOTAL_PER_NPC]

    result_parts = []
    included = set()

    def _maybe_demote(sec_name, sec_text):
        # 렌더러 경로에서만 배경/설정류 섹션을 "작가 참조, 직접 서술 금지" 프레임으로 감싼다.
        if demote_background and _is_background_section(sec_name):
            return (
                "[AUTHOR REFERENCE — never narrated directly]\n"
                "Backstory the writer holds. In prose it surfaces only as present residue "
                "(a hesitation, a reflex, an avoidance, a tell), never recited as history or laid out as exposition.\n"
                f"{sec_text}\n"
                "[end author reference]"
            )
        return sec_text

    # _preamble 먼저 (있고 비어있지 않으면)
    preamble = parsed.get("_preamble", "")
    if preamble and preamble.strip():
        result_parts.append(preamble)

    # _CORE 우선 매칭 (Identity + Hard Rules)
    for core_name in _CORE_SECTIONS:
        for sec_name, sec_text in parsed.items():
            if sec_name == "_preamble" or sec_name in included:
                continue
            if core_name.lower() in sec_name.lower():
                result_parts.append(_maybe_demote(sec_name, sec_text))
                included.add(sec_name)
                break

    # 나머지 모든 섹션 (parsed dict 순서대로)
    for sec_name, sec_text in parsed.items():
        if sec_name == "_preamble" or sec_name in included:
            continue
        result_parts.append(_maybe_demote(sec_name, sec_text))
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
        if data.get("location"):
            meta_parts.append(f"위치: {data['location']}")
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
    remaining = [name for name, data in npcs.items()
                 if name not in resolved_exclude
                 and (include_provisional or get_npc_tier(data) == "established")]
    if not remaining:
        return ""
    return "기타 NPC: " + ", ".join(remaining)


def get_npc_recency_reminders(channel_id: str, npc_names: list) -> str:
    """활성 NPC의 말투 + 핵심 제약을 compact하게 생성. Recency 슬롯 주입용.

    Lost-in-the-Middle 대응: Slot 7 프로필이 중간에 묻히므로 핵심만 recency에 echo.
    hybrid: Voice 섹션에서 대사 추출, legacy: tone 폴백, 둘 다 없으면 스킵.
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
        desc = _get_npc_desc(data)
        if _is_hybrid_profile(desc):
            voice_text = _extract_voice_section(desc)
            if voice_text:
                summary = _extract_voice_summary_from_section(name, voice_text)
                if summary:
                    voice_lines.append(summary)
        else:
            tone = data.get("tone", "")
            if tone:
                voice_lines.append(f"- {name}: {tone}")
        # --- Constraints ---
        constraints = data.get("constraints", "")
        if constraints:
            constraint_lines.append(f"- {name}: {constraints}")
    parts = []
    if constraint_lines:
        parts.append("[NPC HARD RULES — VIOLATING THESE = HALLUCINATION]\n" + "\n".join(constraint_lines))
    if voice_lines:
        parts.append("[NPC Voice — match these speech patterns]\n" + "\n".join(voice_lines))
    return "\n\n".join(parts)


def _extract_voice_summary_from_section(name: str, voice_section: str, cap: int = 220) -> str:
    """Voice 섹션을 recency에 다시 얹을 짧은 발췌.

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
# NPC 정체 발각 (이름 변경 추적)
# =========================================================

def handle_identity_reveal(channel_id: str, old_name: str, new_name: str, reason: str = "") -> str:
    """
    NPC 정체 발각 (OldName -> NewName) 처리
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
    
    # 구 항목 제거 (선택적: Redirect를 남길 수도 있으나, 혼동 방지 위해 제거가 깔끔)
    delete_npc(channel_id, old_name)
    
    # 태도 정보도 이동
    att = get_npc_attitude(channel_id, old_name)
    if att:
        # 기존 태도 삭제하고 새 이름으로 등록 (domain_manager 기능 한계로 직접 조작 필요할 수 있으나, update_npc_attitude 사용)
        update_npc_attitude(channel_id, new_name, att.get("attitude", "neutral"), att.get("reason", "") + " (Identity Reveal)")
        delete_npc_attitude(channel_id, old_name)
    
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
        schedule = npc_data.get("schedule", {})
        if isinstance(schedule, dict) and time_slot in schedule:
            hints.append(f"{npc_name}: {schedule[time_slot]}")
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
