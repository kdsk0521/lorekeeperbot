"""
Lorekeeper TRPG Bot - Domain Manager (Unified)
Centralizes Data Access, caching, and core entity management.
Consolidates: domain_io, domain_participant, domain_content, character_sheet
"""

import os
import json
import re
import logging
import time
from typing import Dict, Any, Optional, List, Union

import config
from cache_manager import cache

# =========================================================
# 1. FILE I/O & CACHING (Formerly domain_io.py)
# =========================================================

def initialize_folders() -> None:
    for path in [config.SESSIONS_DIR, config.LORE_DIR, config.RULES_DIR]:
        if not os.path.exists(path):
            try:
                os.makedirs(path)
                logging.info(f"Created directory: {path}")
            except Exception as e:
                logging.error(f"Failed to create {path}: {e}")

initialize_folders()  # 모듈 임포트 시 자동 실행

def get_session_file_path(channel_id: str) -> str: return os.path.join(config.SESSIONS_DIR, f"{channel_id}.json")
def get_lore_file_path(channel_id: str) -> str: return os.path.join(config.LORE_DIR, f"{channel_id}.txt")
def get_lore_original_file_path(channel_id: str) -> str: return os.path.join(config.LORE_DIR, f"{channel_id}_original.txt")
def get_rules_file_path(channel_id: str) -> str: return os.path.join(config.RULES_DIR, f"{channel_id}.txt")

def load_json(filepath: str, default_val: Any) -> Any:
    if not os.path.exists(filepath): return default_val
    try:
        with open(filepath, 'r', encoding='utf-8') as f: return json.load(f)
    except Exception as e:
        logging.error(f"JSON load error {filepath}: {e}")
        return default_val

def save_json(filepath: str, data: Any) -> bool:
    try:
        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        return True
    except Exception as e:
        logging.error(f"JSON save error {filepath}: {e}")
        return False

def load_text(filepath: str, default_val: str) -> str:
    if not os.path.exists(filepath): return default_val
    try:
        with open(filepath, 'r', encoding='utf-8') as f: return f.read()
    except Exception as e:
        logging.error(f"Text load error {filepath}: {e}")
        return default_val

def save_text(filepath: str, text: str) -> bool:
    try:
        with open(filepath, 'w', encoding='utf-8') as f: f.write(text)
        return True
    except Exception as e:
        logging.error(f"Text save error {filepath}: {e}")
        return False

# =========================================================
# 2. CORE SESSION ACCESS
# =========================================================

def _get_default_session() -> Dict[str, Any]:
    return {
        "participants": {},
        "npcs": {},
        "history": [],
        "quest_board": {"active": [], "completed": [], "memos": [], "archive": [], "lore": []},
        "world_state": config.DEFAULT_WORLD_STATE.copy(),
        "settings": {
            "response_mode": "auto", 
            "session_locked": False, 
            "growth_system": "default", 
            "abnormal_mode": True,
            "scene_type": "normal",  # normal / gore / nsfw / gore_nsfw
            "active_modules": ["judgment", "doom", "anomaly", "mental"]
        },
        "active_genres": ["noir"],
        "custom_tone": None,
        "ai_session_memory": {
            "world_summary": "", "current_arc": "", "active_threads": [], "resolved_threads": [],
            "key_events": [], "foreshadowing": [], "world_changes": [], "npc_summaries": {},
            "party_dynamics": "", "last_updated": ""
        },
        "fermented_history": [],
        "deep_memory": "",
        "last_export_idx": 0,
        "last_chronicle_idx": 0,
        "telescope_logs": [],
        "bot_active": True,  # Default: Bot is ON
        "notebook": "— [소지품] —\n\n— [메모] —", # [V5.1] Unified Notebook
        "last_execution_context": None  # [!다시] Persistent retry data
    }

def get_notebook(channel_id: str, user_id: str = "") -> str:
    """PC별 노트북 반환. user_id 있으면 participant에서 조회, 없으면 채널 fallback."""
    d = get_domain(channel_id)
    if user_id:
        p = d.get("participants", {}).get(user_id, {})
        nb = p.get("notebook")
        if nb is not None:
            return nb
    # Fallback: 기존 채널 레벨 (마이그레이션 전 호환)
    return d.get("notebook", "— [소지품] —\n\n— [메모] —")

def update_notebook(channel_id: str, text: str, user_id: str = "") -> None:
    """PC별 노트북 저장. user_id 있으면 participant에 저장."""
    d = get_domain(channel_id)
    if user_id and user_id in d.get("participants", {}):
        d["participants"][user_id]["notebook"] = text
    else:
        d["notebook"] = text  # Fallback
    save_domain(channel_id, d)

def _append_memo_to_notebook(channel_id: str, content: str, user_id: str = "") -> None:
    current_nb = get_notebook(channel_id, user_id)
    if f"- {content}" in current_nb:
        return

    if "— [메모] —" in current_nb:
        parts = current_nb.split("— [메모] —")
        new_nb = parts[0] + "— [메모] —" + parts[1] + f"\n- {content}"
    else:
        new_nb = current_nb + f"\n\n— [메모] —\n- {content}"

    update_notebook(channel_id, new_nb, user_id)

# =========================================================
# MATURE MODE MANAGEMENT (via settings.scene_type)
# =========================================================
VALID_MATURE_MODES = {"normal", "gore", "nsfw", "gore_nsfw"}

def get_mature_mode(channel_id: str) -> str:
    """현재 채널의 성인 콘텐츠 모드를 반환합니다. (settings.scene_type 사용)"""
    settings: Dict[str, Any] = get_domain(channel_id).get("settings", {})
    return settings.get("scene_type", "normal")

def set_mature_mode(channel_id: str, mode: str) -> bool:
    """
    채널의 성인 콘텐츠 모드를 설정합니다.
    
    Args:
        mode: 'normal', 'gore', 'nsfw', 'gore_nsfw' 중 하나
    
    Returns:
        성공 여부
    """
    mode = mode.lower().strip()
    if mode not in VALID_MATURE_MODES:
        return False
    
    update_settings(channel_id, scene_type=mode)
    return True

def get_domain(channel_id: str) -> Dict[str, Any]:
    # 캐시에서 먼저 조회
    cached = cache.get_session(channel_id)
    if cached is not None:
        return cached

    # 캐시 미스: 파일에서 로드
    default = _get_default_session()
    data = load_json(get_session_file_path(channel_id), default)

    if not isinstance(data, dict):
        data = default

    # Ensure keys
    for k in default:
        if k not in data:
            data[k] = default[k]

    # 캐시에 저장
    cache.set_session(channel_id, data)
    return data

# =========================================================
# 3. DOMAIN METADATA & RETRY CONTEXT
# =========================================================

def save_last_execution_context(channel_id: str, context: Dict[str, Any]) -> None:
    """마지막 실행 컨텍스트를 저장합니다. (!다시 기능용)"""
    d = get_domain(channel_id)
    d["last_execution_context"] = context
    save_domain(channel_id, d)

def get_last_execution_context(channel_id: str) -> Optional[Dict[str, Any]]:
    """마지막 실행 컨텍스트를 조회합니다."""
    return get_domain(channel_id).get("last_execution_context")

def save_domain(channel_id: str, data: Dict[str, Any]) -> bool:
    """세션 데이터 저장 (파일 + 캐시 동기화)

    [V10 Sprint 0] JSON 저장 성공 후 SQLite에도 미러(dual-write).
    SQLite 실패는 봇에 영향 없음 — JSON이 진실의 원천. 롤백 = 아래 dual-write 블록 삭제.
    """
    # 파일 저장 성공 후 캐시 업데이트 (동기화 안전성)
    if not save_json(get_session_file_path(channel_id), data):
        return False
    cache.set_session(channel_id, data)

    # [V10 Sprint 0] Dual-write to SQLite (shadow mirror, 읽기는 아직 JSON)
    try:
        import sqlite_store
        sqlite_store.write_session(channel_id, data)
    except Exception as _e:
        logging.debug(f"[V10] dual-write skipped: {_e}")

    # [V10 Sprint 3] 이력 도메인 스냅샷 미러 — 발효(auto_ferment)의 모든 저장이
    # save_domain(=save_cb)으로 수렴하므로 여기가 유일한 미러 지점 (발효 코드 무수정).
    _sync_history_domain(channel_id, data)

    return True

# [V10 Sprint 3] 변경 감지 가드 — save_domain은 모든 도메인 저장마다 불리므로,
# fermented/deep이 안 변했으면 미러 skip (무의미한 DELETE+INSERT 방지).
_hist_sync_cache: Dict[str, tuple] = {}

def _sync_history_domain(channel_id: str, data: Dict[str, Any]) -> None:
    try:
        import sqlite_store
        fermented = data.get("fermented_history", [])
        deep = data.get("deep_memory", "")
        deep_data = data.get("deep_memory_data", {})
        f_key = hash(json.dumps(fermented, ensure_ascii=False, sort_keys=True, default=str))
        d_key = hash(json.dumps([deep, deep_data], ensure_ascii=False, sort_keys=True, default=str))
        cached = _hist_sync_cache.get(channel_id)
        f_ok = d_ok = True
        if cached is None or cached[0] != f_key:
            f_ok = sqlite_store.sync_fermented(channel_id, fermented if isinstance(fermented, list) else [])
        if cached is None or cached[1] != d_key:
            d_ok = sqlite_store.sync_deep(channel_id, deep, deep_data)
        if f_ok and d_ok:
            _hist_sync_cache[channel_id] = (f_key, d_key)
    except Exception as _e:
        logging.debug(f"[V10] history domain sync skipped: {_e}")

def reset_domain(channel_id: str) -> None:
    """채널의 모든 데이터 초기화 (파일 삭제 + 캐시 무효화)"""
    paths = [get_session_file_path(channel_id), get_lore_file_path(channel_id),
             get_lore_original_file_path(channel_id), get_rules_file_path(channel_id)]
    for p in paths:
        if os.path.exists(p):
            try:
                os.remove(p)
            except (OSError, PermissionError) as e:
                logging.warning(f"Failed to delete {p}: {e}")

    # 모든 캐시 무효화
    cache.invalidate_all(channel_id)

    # [V10] SQLite 행도 삭제 — 안 지우면 읽기 플래그 ON 시 유령 데이터 부활 (Sprint 1 누락분 보강)
    try:
        import sqlite_store
        sqlite_store.delete_channel_rows(channel_id)
    except Exception as _e:
        logging.debug(f"[V10] channel rows delete skipped: {_e}")

# Export Indices
def get_last_export_idx(channel_id: str) -> int:
    return get_domain(channel_id).get("last_export_idx", 0)

def set_last_export_idx(channel_id: str, idx: int) -> None:
    d = get_domain(channel_id)
    d["last_export_idx"] = idx
    save_domain(channel_id, d)

def get_last_chronicle_idx(channel_id: str) -> int:
    return get_domain(channel_id).get("last_chronicle_idx", 0)

def set_last_chronicle_idx(channel_id: str, idx: int) -> None:
    d = get_domain(channel_id)
    d["last_chronicle_idx"] = idx
    save_domain(channel_id, d)

# =========================================================
# 3. LORE & CONTENT MANAGEMENT (Formerly domain_content.py)
# =========================================================

def get_lore(channel_id: str) -> str:
    """로어 텍스트 조회 (캐시 우선)"""
    cached = cache.get_lore(channel_id)
    if cached is not None:
        return cached
    text = load_text(get_lore_file_path(channel_id), config.DEFAULT_LORE)
    cache.set_lore(channel_id, text)
    return text

def append_lore(channel_id: str, text: str) -> None:
    """로어에 텍스트 추가"""
    cur = get_lore(channel_id)
    new_t = text if cur.strip() == config.DEFAULT_LORE.strip() else f"{cur}\n\n{text}"
    cache.set_lore(channel_id, new_t)
    save_text(get_lore_file_path(channel_id), new_t)

def set_lore(channel_id: str, text: str) -> None:
    """로어 텍스트 덮어쓰기 (파일 업로드 시)"""
    cache.set_lore(channel_id, text)
    save_text(get_lore_file_path(channel_id), text)

def reset_lore(channel_id: str) -> None:
    reset_domain(channel_id) # Simplify: reset all if lore reset requested usually implies restart

def save_lore_original(channel_id: str, text: str) -> None:
    """원본 로어 저장"""
    cache.set_lore_original(channel_id, text)
    save_text(get_lore_original_file_path(channel_id), text)

def get_lore_original(channel_id: str) -> Optional[str]:
    """원본 로어 조회"""
    cached = cache.get_lore_original(channel_id)
    if cached is not None:
        return cached
    path = get_lore_original_file_path(channel_id)
    if os.path.exists(path):
        t = load_text(path, "")
        cache.set_lore_original(channel_id, t)
        return t
    return None

def get_event_lore_summary_file_path(channel_id: str) -> str:
    return os.path.join(config.LORE_DIR, f"{channel_id}_summary.txt")

def get_event_lore_summary(channel_id: str) -> str:
    path = get_event_lore_summary_file_path(channel_id)
    return load_text(path, "")

def set_event_lore_summary(channel_id: str, text: str) -> None:
    path = get_event_lore_summary_file_path(channel_id)
    save_text(path, text)

# [V4 Deep Analysis]
def get_lore_summary_data(channel_id: str) -> Dict[str, Any]:
    """구체적인 로어 요약 데이터(Theme, Anomaly Seeds 등)를 반환합니다."""
    d = get_domain(channel_id)
    return d.get("lore_summary_data", {})

def set_lore_summary_data(channel_id: str, data: Dict[str, Any]) -> None:
    """구체적인 로어 요약 데이터를 저장합니다."""
    d = get_domain(channel_id)
    d["lore_summary_data"] = data
    save_domain(channel_id, d)

def get_lore_chunks(channel_id: str) -> list:
    """청크 분할된 로어 데이터를 반환합니다."""
    d = get_domain(channel_id)
    return d.get("lore_chunks", [])

def set_lore_chunks(channel_id: str, chunks: list) -> None:
    """청크 분할된 로어 데이터를 저장합니다."""
    d = get_domain(channel_id)
    d["lore_chunks"] = chunks
    save_domain(channel_id, d)

def get_lore_with_npcs(channel_id: str) -> str:
    lore = get_lore(channel_id)
    npcs = get_npcs(channel_id)
    if not npcs: return lore
    sec = "\n\n### 📋 NPC 정보\n\n"
    for n, d in npcs.items():
        sec += f"{n} ({d.get('status','Active')})\n{d.get('desc','-')}\n\n"
    return lore + sec

# NPCs
def _normalize_npc_name(name: str) -> str:
    """NPC 이름 정규화: 괄호 주변 공백 통일. '리미 (Limi)' → '리미(Limi)'"""
    name = name.strip()
    name = re.sub(r'\s+\(', '(', name)
    name = re.sub(r'\(\s+', '(', name)
    name = re.sub(r'\s+\)', ')', name)
    return name

# [V10 Sprint 2-B] NPC 본체 — JSON 진실원천 + npcs 문서테이블 dual-write.
# 읽기는 config.V10_NPCS_READ_FROM_SQLITE 게이트 (기본 OFF).
# 단건(get_npc)은 별칭 해상도(_find_npc_key)가 전체 dict를 요구하므로 get_npcs 경유 유지.

def _mirror_npc(channel_id: str, npc_name: str, data: Dict[str, Any]) -> None:
    """NPC 1건을 방벽 통과 후 npcs 테이블에 미러."""
    try:
        import sqlite_store
        import state_guards
        clean = state_guards.validate_npc_write(npc_name, data)
        if clean is not None:
            sqlite_store.upsert_npc(channel_id, npc_name, clean)
    except Exception as _e:
        logging.debug(f"[V10] npc mirror skipped: {_e}")

def get_npcs(channel_id: str) -> Dict[str, Dict[str, Any]]:
    """[V10] 플래그 ON 시 read-through: SQLite 우선, 없으면 JSON 폴백 + lazy migration."""
    if getattr(config, "V10_NPCS_READ_FROM_SQLITE", False):
        try:
            import sqlite_store
            npcs = sqlite_store.read_npcs(channel_id)
            if npcs is not None:
                return npcs
            npcs_json = get_domain(channel_id).get("npcs", {})
            for _name, _data in npcs_json.items():
                _mirror_npc(channel_id, _name, _data)
            return npcs_json
        except Exception as _e:
            logging.warning(f"[V10] npcs read-through 실패, JSON 폴백: {_e}")
    return get_domain(channel_id).get("npcs", {})

def _find_npc_key(npcs: dict, name: str) -> Optional[str]:
    """NPC 키 검색: 정규화 매칭 → 부분 이름 매칭 (괄호 앞/안) → aliases 필드.

    [2026-06-12] aliases 정식 지원 — 괄호 컨벤션은 키에 별칭이 박힌 경우만 커버해서,
    "리리스"로 등록된 NPC를 모델이 "Lilith"로 부르면 자동 등록이 중복 생성하던 구멍
    (한↔영 교차는 통제 밖, deepseek은 특히 영문명 빈발). NPC data에 aliases: [".."] 리스트."""
    norm = _normalize_npc_name(name)
    # 1) 정확한 정규화 매칭
    if norm in npcs:
        return norm
    for k in npcs:
        if _normalize_npc_name(k) == norm:
            return k
    # 2) 부분 매칭: "복셀" → "복셀(Voxel)", "Voxel" → "복셀(Voxel)"
    nl = norm.lower()
    for k in npcs:
        # 괄호 앞 한글 이름으로 매칭
        base = re.split(r'[(\[（]', k)[0].strip().lower()
        if base == nl:
            return k
        # 괄호 안 영문 이름으로 매칭
        m = re.search(r'[(\[（]([^)\]）]+)[)\]）]', k)
        if m and m.group(1).strip().lower() == nl:
            return k
    # 3) aliases 필드 매칭 (data dict 내 명시 별칭 리스트)
    for k, data in npcs.items():
        if not isinstance(data, dict):
            continue
        aliases = data.get("aliases")
        if isinstance(aliases, list):
            for a in aliases:
                if isinstance(a, str) and _normalize_npc_name(a).lower() == nl:
                    return k
    return None

def _resolve_npc_name(d: dict, name: str) -> str:
    """AI 출력 이름 → 저장된 NPC 키로 해상도. 일치 없으면 원본 반환."""
    npcs = d.get("npcs", {})
    matched = _find_npc_key(npcs, name)
    return matched if matched else name

def get_npc(channel_id: str, name: str) -> Optional[Dict[str, Any]]:
    npcs = get_npcs(channel_id)
    key = _find_npc_key(npcs, name)
    return npcs[key] if key else None

def find_equivalent_npc_key(npcs: dict, name: str) -> Optional[str]:
    """양방향 동일 인물 매칭. _find_npc_key(이름→키)에 더해 역방향
    (새 이름의 괄호 앞/안 → 기존 키)도 본다.

    [2026-06-12] 리리스/Lilith 4중 분열의 두 번째 원인 — 등록 경로의 중복 탐지가
    한 방향(새 맨이름 → 기존 키 괄호 안)뿐이라, 맨이름 "리리스"가 있는 상태에서
    "리리스(Lilith)"로 재등록하면 병합이 아니라 병렬 생성됐음."""
    key = _find_npc_key(npcs, name)
    if key:
        return key
    norm = _normalize_npc_name(name)
    base = re.split(r'[(\[（]', norm)[0].strip()
    m = re.search(r'[(\[（]([^)\]）]+)[)\]）]', norm)
    inner = m.group(1).strip() if m else ""
    for cand in (base, inner):
        if cand and cand.lower() != norm.lower():
            key = _find_npc_key(npcs, cand)
            if key:
                return key
    return None

def update_npc(channel_id: str, name: str, data: Dict[str, Any]) -> None:
    """NPC 등록/갱신.

    [2026-06-12] 중복 탐지 보강 — 기존엔 정규화 동일성만 봐서 (등록 경로별로
    매칭이 제각각이라) 같은 인물이 키 형태마다 병렬 생성됐음. 이제:
    ① find_equivalent_npc_key로 양방향 매칭
    ② 키 선호: 괄호 별칭 가진 쪽 (riche key) — DAI가 맨이름으로 update해도 다운그레이드 안 됨
    ③ 키 마이그레이션 시 태도/지식도 함께 이사 + 버려지는 키 형태는 aliases로 흡수"""
    d = get_domain(channel_id)
    npcs = d.setdefault("npcs", {})

    norm_name = _normalize_npc_name(name)

    # 동일 인물 기존 키 탐지 (정규화 동일성 → 양방향 풀 매칭)
    existing_key = None
    for k in list(npcs.keys()):
        if _normalize_npc_name(k) == norm_name:
            existing_key = k
            break
    if existing_key is None:
        existing_key = find_equivalent_npc_key(npcs, name)

    # 최종 키 결정: 기존 키 안정 유지가 기본 (DAI가 변형 호칭으로 update해도 개명 X).
    # 새 이름이 괄호식일 때만 업그레이드 — 괄호식 = 사용자의 명시 등록 의도.
    def _has_paren(k: str) -> bool:
        return bool(re.search(r'[(\[（]', k))
    if existing_key:
        final_key = norm_name if _has_paren(norm_name) else _normalize_npc_name(existing_key)
    else:
        final_key = norm_name

    # 기존 데이터에서 보존할 필드 (재등록 시 유실 방지)
    _PRESERVE_KEYS = ("source", "aliases")
    if existing_key:
        existing = npcs[existing_key]
        if isinstance(existing, dict):
            for pk in _PRESERVE_KEYS:
                if pk not in data and existing.get(pk):
                    data[pk] = existing[pk]
        if existing_key != final_key:
            del npcs[existing_key]
            # 버려지는 키의 고유 형태(전체/base/괄호 안)를 aliases로 흡수 — 재분열 방지
            fk_base = re.split(r'[(\[（]', final_key)[0].strip().lower()
            fm = re.search(r'[(\[（]([^)\]）]+)[)\]）]', final_key)
            fk_inner = fm.group(1).strip().lower() if fm else ""
            covered = {final_key.lower(), _normalize_npc_name(final_key).lower(), fk_base, fk_inner}
            aliases = data.setdefault("aliases", [])
            if isinstance(aliases, list):
                seen = {_normalize_npc_name(a).lower() for a in aliases if isinstance(a, str)}
                om = re.search(r'[(\[（]([^)\]）]+)[)\]）]', existing_key)
                for cand in (existing_key, re.split(r'[(\[（]', existing_key)[0].strip(),
                             om.group(1).strip() if om else ""):
                    nc = _normalize_npc_name(cand).lower() if cand else ""
                    if nc and nc not in covered and nc not in seen:
                        aliases.append(cand.strip())
                        seen.add(nc)
                if not aliases:
                    data.pop("aliases", None)
            # 태도/지식도 함께 이사 (구 키에 쌓인 관계 고아화 방지)
            for _dom in ("npc_attitudes", "npc_knowledge"):
                _dd = d.get(_dom, {})
                if existing_key in _dd and final_key not in _dd:
                    _dd[final_key] = _dd.pop(existing_key)
                elif existing_key in _dd:
                    _dd.pop(existing_key)  # 양쪽 존재 시 final 쪽 유지

    npcs[final_key] = data
    save_domain(channel_id, d)

    # [V10 Sprint 2-B] 미러 — 키 마이그레이션 발생 시 구 행 삭제 + 신 행, 한 트랜잭션
    try:
        import sqlite_store
        import state_guards
        clean = state_guards.validate_npc_write(final_key, data)
        if clean is not None:
            if existing_key and existing_key != final_key:
                sqlite_store.rename_npc(channel_id, existing_key, final_key, clean)
                sqlite_store.delete_relation(channel_id, existing_key)
                sqlite_store.delete_knowledge(channel_id, existing_key)
                if final_key in d.get("npc_attitudes", {}):
                    _mirror_relation(channel_id, final_key, d["npc_attitudes"][final_key])
                if final_key in d.get("npc_knowledge", {}):
                    _mirror_knowledge(channel_id, final_key, d["npc_knowledge"][final_key])
            else:
                sqlite_store.upsert_npc(channel_id, final_key, clean)
    except Exception as _e:
        logging.debug(f"[V10] npc mirror skipped: {_e}")

def delete_npc(channel_id: str, name: str) -> tuple:
    """NPC 삭제. Returns (success: bool, matched_key: str or None)

    [2026-06-12] 고아 정리 추가 — 초기 코드는 본체만 지워서 태도/지식 행이
    유령으로 남았음 (삭제 후 그 이름이 분석에 재등장하면 죽은 관계가 부활)."""
    d = get_domain(channel_id)
    npcs = d.get("npcs", {})
    target = _find_npc_key(npcs, name)
    if target:
        del npcs[target]
        # 고아 정리: 같은 키의 태도/지식도 함께 (JSON)
        d.get("npc_attitudes", {}).pop(target, None)
        d.get("npc_knowledge", {}).pop(target, None)
        save_domain(channel_id, d)
        try:
            import sqlite_store
            sqlite_store.delete_npc_row(channel_id, target)
            sqlite_store.delete_relation(channel_id, target)
            sqlite_store.delete_knowledge(channel_id, target)
        except Exception as _e:
            logging.debug(f"[V10] npc delete mirror skipped: {_e}")
        return True, target
    return False, None

def add_npc_alias(channel_id: str, name: str, alias: str) -> tuple:
    """NPC에 별칭 추가. Returns (success: bool, matched_key: str or None)

    [2026-06-12] 모델이 등록명과 다른 언어로 NPC를 부르면 (리리스 ↔ Lilith)
    자동 등록이 중복 생성하던 구멍의 입구. aliases 리스트는 _find_npc_key 3단계가 소비."""
    alias = (alias or "").strip()
    if not alias:
        return False, None
    d = get_domain(channel_id)
    npcs = d.get("npcs", {})
    key = _find_npc_key(npcs, name)
    if not key:
        return False, None
    # 별칭이 이미 다른 NPC로 해상되면 거부 (모호성 생성 방지)
    existing = _find_npc_key(npcs, alias)
    if existing and existing != key:
        logging.warning(f"[NPC] 별칭 거부: '{alias}'는 이미 '{existing}'로 해상됨")
        return False, existing
    data = npcs[key]
    if not isinstance(data, dict):
        return False, None
    aliases = data.setdefault("aliases", [])
    if not isinstance(aliases, list):
        aliases = data["aliases"] = []
    norm_new = _normalize_npc_name(alias).lower()
    if any(isinstance(a, str) and _normalize_npc_name(a).lower() == norm_new for a in aliases):
        return True, key  # 이미 있음 = 성공
    aliases.append(alias)
    save_domain(channel_id, d)
    _mirror_npc(channel_id, key, data)
    return True, key

def split_npc_pair(npcs: dict, text: str, both_npc: bool = True) -> tuple:
    """명령 인자 텍스트를 (이름A, 이름B)로 분할. 이름 중간 띄어쓰기 지원.

    [2026-06-12] "이름없는 유령" 같은 공백 포함 키가 split(None,1)에서 잘리던 문제.
    ① 명시 구분자 우선: ->, =>, →, |, 쉼표
    ② 없으면 등록 키 기반 스마트 분할: 양쪽(both_npc) 또는 왼쪽(별칭 모드)이
       실제 NPC로 해상되는 분할점 탐색. 별칭 모드는 가장 긴 왼쪽 우선.
    Returns (a, b, error_msg) — 성공 시 error_msg는 ""."""
    text = (text or "").strip()
    if not text:
        return None, None, "인자 없음"
    seg = [s.strip() for s in re.split(r'\s*(?:->|=>|→|\||,)\s*', text) if s.strip()]
    if len(seg) == 2:
        return seg[0], seg[1], ""
    if len(seg) > 2:
        return None, None, "구분자가 너무 많습니다. `이름A -> 이름B` 형식으로."
    toks = text.split()
    if len(toks) < 2:
        return None, None, "이름 두 개가 필요합니다."
    if both_npc:
        cands = []
        for i in range(1, len(toks)):
            l, r = " ".join(toks[:i]), " ".join(toks[i:])
            if _find_npc_key(npcs, l) and _find_npc_key(npcs, r):
                cands.append((l, r))
        if len(cands) == 1:
            return cands[0][0], cands[0][1], ""
        if len(cands) > 1:
            opts = " / ".join(f"'{l}'+'{r}'" for l, r in cands[:3])
            return None, None, f"분할이 모호합니다 ({opts}). `이름A -> 이름B` 구분자를 쓰세요."
        return None, None, "두 이름을 NPC로 해상하지 못했습니다. `이름A -> 이름B` 구분자를 쓰거나 이름을 확인하세요."
    # 별칭 모드: 왼쪽만 NPC면 됨 — 가장 긴 왼쪽 우선 (나머지 = 별칭)
    for i in range(len(toks) - 1, 0, -1):
        l, r = " ".join(toks[:i]), " ".join(toks[i:])
        if _find_npc_key(npcs, l):
            return l, r, ""
    return None, None, "NPC를 찾을 수 없습니다. `이름 -> 별칭` 구분자를 쓰거나 이름을 확인하세요."

def merge_npc(channel_id: str, dup_name: str, canon_name: str) -> tuple:
    """중복 NPC를 본체로 흡수. Returns (success: bool, message: str)

    [2026-06-12] 리리스/Lilith 류 이중 등록 청소용. 정책:
    - 본체(canon) 필드 우선, 중복(dup)은 빈 필드만 채움
    - 흡수된 이름(+괄호 별칭)은 자동으로 본체 aliases에 — 재발 방지
    - 태도: 본체 우선, depth/tension은 둘 중 큰 값 (쌓인 관계 보존)
    - 지식: knows/secrets 합집합, would_share OR
    - 중복은 본체/태도/지식 전 도메인에서 삭제 (JSON+SQLite)"""
    d = get_domain(channel_id)
    npcs = d.get("npcs", {})
    dup_key = _find_npc_key(npcs, dup_name)
    canon_key = _find_npc_key(npcs, canon_name)
    if not dup_key:
        return False, f"중복 NPC '{dup_name}' 없음"
    if not canon_key:
        return False, f"본체 NPC '{canon_name}' 없음"
    if dup_key == canon_key:
        return False, f"'{dup_name}'와 '{canon_name}'는 이미 같은 NPC ({canon_key})"

    canon = npcs[canon_key] if isinstance(npcs[canon_key], dict) else {}
    dup = npcs[dup_key] if isinstance(npcs[dup_key], dict) else {}

    # 1) 본체 필드 보강 (빈 필드만 dup에서)
    for k, v in dup.items():
        if k == "aliases":
            continue
        if k not in canon or canon[k] in (None, "", [], {}):
            canon[k] = v

    # 2) 별칭 합치기 + 흡수된 이름 자동 별칭화
    merged_aliases = [a for a in canon.get("aliases", []) if isinstance(a, str)]
    seen = {_normalize_npc_name(a).lower() for a in merged_aliases}

    def _add_alias(cand: str):
        cand = (cand or "").strip()
        if not cand:
            return
        nc = _normalize_npc_name(cand).lower()
        # 본체 키 자신(괄호 앞/안 포함)으로 이미 해상되는 이름은 별칭 불필요
        base = re.split(r'[(\[（]', canon_key)[0].strip().lower()
        m = re.search(r'[(\[（]([^)\]）]+)[)\]）]', canon_key)
        inner = m.group(1).strip().lower() if m else ""
        if nc in (canon_key.lower(), _normalize_npc_name(canon_key).lower(), base, inner):
            return
        if nc not in seen:
            merged_aliases.append(cand)
            seen.add(nc)

    for a in dup.get("aliases", []):
        if isinstance(a, str):
            _add_alias(a)
    _add_alias(dup_key)
    _add_alias(re.split(r'[(\[（]', dup_key)[0])
    m = re.search(r'[(\[（]([^)\]）]+)[)\]）]', dup_key)
    if m:
        _add_alias(m.group(1))
    if merged_aliases:
        canon["aliases"] = merged_aliases
    npcs[canon_key] = canon
    del npcs[dup_key]

    # 3) 태도 병합 (본체 우선, depth/tension은 max)
    attitudes = d.get("npc_attitudes", {})
    dup_att = attitudes.pop(dup_key, None)
    if dup_att:
        canon_att = attitudes.get(canon_key)
        if canon_att:
            canon_att["depth"] = max(canon_att.get("depth", 0) or 0, dup_att.get("depth", 0) or 0)
            canon_att["tension"] = max(canon_att.get("tension", 0) or 0, dup_att.get("tension", 0) or 0)
        else:
            attitudes[canon_key] = dup_att
            canon_att = dup_att

    # 4) 지식 병합 (합집합)
    knowledge = d.get("npc_knowledge", {})
    dup_kn = knowledge.pop(dup_key, None)
    if dup_kn:
        canon_kn = knowledge.get(canon_key)
        if canon_kn:
            canon_kn["knows"] = list(set(canon_kn.get("knows", [])) | set(dup_kn.get("knows", [])))[-20:]
            canon_kn["secrets_held"] = list(set(canon_kn.get("secrets_held", [])) | set(dup_kn.get("secrets_held", [])))
            canon_kn["would_share"] = bool(canon_kn.get("would_share")) or bool(dup_kn.get("would_share"))
        else:
            knowledge[canon_key] = dup_kn

    save_domain(channel_id, d)

    # 5) SQLite 미러: 중복 행 삭제 + 본체 재미러
    try:
        import sqlite_store
        sqlite_store.delete_npc_row(channel_id, dup_key)
        sqlite_store.delete_relation(channel_id, dup_key)
        sqlite_store.delete_knowledge(channel_id, dup_key)
    except Exception as _e:
        logging.debug(f"[V10] merge dup row cleanup skipped: {_e}")
    _mirror_npc(channel_id, canon_key, canon)
    if canon_key in d.get("npc_attitudes", {}):
        _mirror_relation(channel_id, canon_key, d["npc_attitudes"][canon_key])
    if canon_key in d.get("npc_knowledge", {}):
        _mirror_knowledge(channel_id, canon_key, d["npc_knowledge"][canon_key])

    alias_note = f" (별칭: {', '.join(merged_aliases)})" if merged_aliases else ""
    return True, f"'{dup_key}' → '{canon_key}' 병합 완료{alias_note}"

def bulk_update_npcs(channel_id: str, npcs: Dict[str, Dict[str, Any]]) -> None:
    """[V10 §3b] NPC dict 전체 교체 (tick_all_cooldowns 등 bulk 쓰기 정식화).
    JSON + SQLite 동시, SQLite는 단일 트랜잭션."""
    d = get_domain(channel_id)
    d["npcs"] = npcs
    save_domain(channel_id, d)
    try:
        import sqlite_store
        import state_guards
        cleaned = {}
        for _name, _data in npcs.items():
            _c = state_guards.validate_npc_write(_name, _data)
            if _c is not None:
                cleaned[_name] = _c
        if cleaned:
            sqlite_store.bulk_upsert_npcs(channel_id, cleaned)
    except Exception as _e:
        logging.debug(f"[V10] npc bulk mirror skipped: {_e}")

def delete_npcs_by_source(channel_id: str, keep_sources: tuple = ("lore", "manual")) -> int:
    """[V10 §3b] keep_sources 외 NPC 일괄 삭제 (clear_session_npcs/세션 리셋 공용).
    Returns: 삭제된 NPC 수."""
    d = get_domain(channel_id)
    npcs = d.get("npcs", {})
    to_delete = [name for name, data in npcs.items()
                 if data.get("source", "session") not in keep_sources]
    for name in to_delete:
        del npcs[name]
    if to_delete:
        save_domain(channel_id, d)
        try:
            import sqlite_store
            sqlite_store.delete_npcs_except_sources(channel_id, keep_sources)
        except Exception as _e:
            logging.debug(f"[V10] npc bulk delete mirror skipped: {_e}")
    return len(to_delete)

# NPC Attitude System
# [V10 Sprint 1] 관계 도메인 — JSON 진실원천 + npc_relations 정규화 테이블 dual-write.
# 읽기는 config.V10_RELATIONS_READ_FROM_SQLITE 플래그 게이트 (기본 OFF = V9 동작).
# spec: 파티쳇수정/v10_sprint1_relations_spec.md

def _mirror_relation(channel_id: str, npc_name: str, rel: Dict[str, Any]) -> None:
    """JSON에 쓰인 최종 상태를 방벽 통과 후 npc_relations에 미러.
    철칙: JSON이 받은 건 다 받는다 (existing_npcs 체크 안 함 — parity 우선).
    실패해도 봇 무영향 (JSON이 진실원천)."""
    try:
        import sqlite_store
        import state_guards
        clean = state_guards.validate_relation_write(npc_name, rel)
        if clean is not None:
            sqlite_store.upsert_relation(channel_id, npc_name, clean)
    except Exception as _e:
        logging.debug(f"[V10] relation mirror skipped: {_e}")

def update_npc_attitude(channel_id: str, npc_name: str, attitude: str, reason: str = "") -> None:
    """NPC의 PC에 대한 태도 업데이트 (depth/tension 보존)

    주의: dict 재구성이므로 last_change_turn은 의도적으로 소실됨 (기존 동작 보존, §1b quirk).
    gated 경로에선 직후 set_attitude_turn이 재기록."""
    d = get_domain(channel_id)
    if "npc_attitudes" not in d:
        d["npc_attitudes"] = {}
    npc_name = _resolve_npc_name(d, npc_name)

    existing = d["npc_attitudes"].get(npc_name, {})
    d["npc_attitudes"][npc_name] = {
        "attitude": attitude,
        "reason": reason,
        "depth": existing.get("depth", 0),
        "tension": existing.get("tension", 0),
        "last_updated": time.strftime('%Y-%m-%d %H:%M')
    }
    save_domain(channel_id, d)
    _mirror_relation(channel_id, npc_name, d["npc_attitudes"][npc_name])

def get_npc_attitudes(channel_id: str) -> Dict[str, Dict]:
    """저장된 NPC 태도 조회 (전체)

    [V10] 플래그 ON 시 read-through: SQLite 우선, 행 없으면 JSON 폴백 + lazy migration."""
    if getattr(config, "V10_RELATIONS_READ_FROM_SQLITE", False):
        try:
            import sqlite_store
            rels = sqlite_store.read_relations(channel_id)
            if rels is not None:
                return rels  # B 발동: 통짜 JSON 안 거침
            # 구 세션 — JSON에서 읽고 그 자리에서 SQLite로 lazy 이주
            att = get_domain(channel_id).get("npc_attitudes", {})
            for _name, _rel in att.items():
                _mirror_relation(channel_id, _name, _rel)
            return att
        except Exception as _e:
            logging.warning(f"[V10] relations read-through 실패, JSON 폴백: {_e}")
    d = get_domain(channel_id)
    return d.get("npc_attitudes", {})

def get_npc_attitude(channel_id: str, npc_name: str) -> Optional[Dict]:
    """특정 NPC의 태도 조회 (단건)

    [V10] 플래그 ON 시 포인트 질의 (npc_relations 단일 행 SELECT)."""
    d = get_domain(channel_id)
    npc_name = _resolve_npc_name(d, npc_name)
    if getattr(config, "V10_RELATIONS_READ_FROM_SQLITE", False):
        try:
            import sqlite_store
            rel = sqlite_store.read_relation(channel_id, npc_name)
            if rel is not None:
                return rel
        except Exception as _e:
            logging.warning(f"[V10] relation 단건 read-through 실패, JSON 폴백: {_e}")
    return d.get("npc_attitudes", {}).get(npc_name)

def delete_npc_attitude(channel_id: str, npc_name: str) -> bool:
    """NPC 태도 삭제 (identity reveal 등). [V10 §3b] 직접 조작 정식화 — JSON+SQLite 동시."""
    d = get_domain(channel_id)
    npc_name = _resolve_npc_name(d, npc_name)
    attitudes = d.get("npc_attitudes", {})
    if npc_name not in attitudes:
        return False
    del attitudes[npc_name]
    save_domain(channel_id, d)
    try:
        import sqlite_store
        sqlite_store.delete_relation(channel_id, npc_name)
    except Exception as _e:
        logging.debug(f"[V10] relation delete mirror skipped: {_e}")
    return True

def set_attitude_turn(channel_id: str, npc_name: str, turn: int) -> None:
    """attitude에 last_change_turn 기록 (쿨다운 게이트용). [V10 §3b] 직접 조작 정식화.
    기존 _save_attitude_turn 동작 보존: NPC 태도가 존재할 때만."""
    d = get_domain(channel_id)
    npc_name = _resolve_npc_name(d, npc_name)
    attitudes = d.get("npc_attitudes", {})
    if npc_name not in attitudes:
        return
    attitudes[npc_name]["last_change_turn"] = int(turn)
    save_domain(channel_id, d)
    try:
        import sqlite_store
        sqlite_store.set_relation_turn(channel_id, npc_name, int(turn))
    except Exception as _e:
        logging.debug(f"[V10] relation turn mirror skipped: {_e}")

# NPC Knowledge Persistence
# [V10 Sprint 2-A] JSON 진실원천 + npc_knowledge 테이블 dual-write.
# 읽기는 config.V10_KNOWLEDGE_READ_FROM_SQLITE 게이트 (기본 OFF).

def _mirror_knowledge(channel_id: str, npc_name: str, kn: Dict[str, Any]) -> None:
    """JSON에 쓰인 최종 지식 상태를 방벽 통과 후 npc_knowledge 테이블에 미러."""
    try:
        import sqlite_store
        import state_guards
        clean = state_guards.validate_knowledge_write(npc_name, kn)
        if clean is not None:
            sqlite_store.upsert_knowledge(channel_id, npc_name, clean)
    except Exception as _e:
        logging.debug(f"[V10] knowledge mirror skipped: {_e}")

def update_npc_knowledge(channel_id: str, npc_name: str, knowledge_data: Dict[str, Any]) -> None:
    """NPC의 지식 상태 업데이트 (Theoria 분석 결과 저장)

    주의: knows는 set union 머지라 순서 비결정 (기존 동작 — parity 비교는 set 기준).
    DAI의 false_beliefs는 여기 저장 안 됨 (턴 내 소비 전용, spec §A-1)."""
    d = get_domain(channel_id)
    if "npc_knowledge" not in d:
        d["npc_knowledge"] = {}
    npc_name = _resolve_npc_name(d, npc_name)

    existing = d["npc_knowledge"].get(npc_name, {})
    # Merge: 기존 knows에 새 항목 추가 (중복 제거)
    old_knows = set(existing.get("knows", []))
    new_knows = knowledge_data.get("knows", [])
    merged_knows = list(old_knows | set(new_knows))

    d["npc_knowledge"][npc_name] = {
        "knows": merged_knows[-20:],  # 최대 20개 유지
        "secrets_held": knowledge_data.get("secrets_held", existing.get("secrets_held", [])),
        "would_share": knowledge_data.get("would_share", existing.get("would_share", False)),
        "leak_risk": knowledge_data.get("leak_risk", existing.get("leak_risk", "none")),
        "last_updated": time.strftime('%Y-%m-%d %H:%M')
    }
    save_domain(channel_id, d)
    _mirror_knowledge(channel_id, npc_name, d["npc_knowledge"][npc_name])

def get_npc_knowledge(channel_id: str) -> Dict[str, Dict]:
    """저장된 전체 NPC 지식 상태 조회

    [V10] 플래그 ON 시 read-through: SQLite 우선, 없으면 JSON 폴백 + lazy migration."""
    if getattr(config, "V10_KNOWLEDGE_READ_FROM_SQLITE", False):
        try:
            import sqlite_store
            kns = sqlite_store.read_knowledge_all(channel_id)
            if kns is not None:
                return kns
            kn_json = get_domain(channel_id).get("npc_knowledge", {})
            for _name, _kn in kn_json.items():
                _mirror_knowledge(channel_id, _name, _kn)
            return kn_json
        except Exception as _e:
            logging.warning(f"[V10] knowledge read-through 실패, JSON 폴백: {_e}")
    d = get_domain(channel_id)
    return d.get("npc_knowledge", {})

def get_npc_knowledge_for(channel_id: str, npc_name: str) -> Optional[Dict]:
    """특정 NPC의 지식 상태 조회 (단건 포인트 질의)"""
    d = get_domain(channel_id)
    npc_name = _resolve_npc_name(d, npc_name)
    if getattr(config, "V10_KNOWLEDGE_READ_FROM_SQLITE", False):
        try:
            import sqlite_store
            kn = sqlite_store.read_knowledge(channel_id, npc_name)
            if kn is not None:
                return kn
        except Exception as _e:
            logging.warning(f"[V10] knowledge 단건 read-through 실패, JSON 폴백: {_e}")
    return d.get("npc_knowledge", {}).get(npc_name)

def propagate_npc_knowledge(channel_id: str, scene_npcs: list) -> int:
    """같은 장면 NPC 간 지식 전파. would_share=True인 NPC의 비밀 아닌 지식을 공유.
    Returns: 전파된 사실 수."""
    if len(scene_npcs) < 2:
        return 0
    all_knowledge = get_npc_knowledge(channel_id)
    attitudes = get_npc_attitudes(channel_id)
    propagated = 0

    for npc_a in scene_npcs:
        kn_a = all_knowledge.get(npc_a, {})
        if not kn_a.get("would_share"):
            continue
        secrets = set(kn_a.get("secrets_held", []))
        shareable = [f for f in kn_a.get("knows", []) if f not in secrets]
        if not shareable:
            continue

        for npc_b in scene_npcs:
            if npc_b == npc_a:
                continue
            # hostile NPC에게는 공유 안 함
            att_b = attitudes.get(npc_b, {}).get("attitude", "neutral")
            if att_b in ("hostile", "unfriendly"):
                continue
            kn_b = all_knowledge.get(npc_b, {})
            existing_b = set(kn_b.get("knows", []))
            new_facts = [f for f in shareable if f not in existing_b][:3]
            if new_facts:
                tagged = [f"{f} (via {npc_a})" for f in new_facts]
                merged = list(existing_b) + tagged
                kn_b_updated = dict(kn_b)
                kn_b_updated["knows"] = merged[-20:]
                all_knowledge[npc_b] = kn_b_updated
                propagated += len(new_facts)

    if propagated > 0:
        d = get_domain(channel_id)
        d["npc_knowledge"] = all_knowledge
        save_domain(channel_id, d)
        # [V10 Sprint 2-A] bulk 미러 — 방벽 통과분만, 단일 트랜잭션
        try:
            import sqlite_store
            import state_guards
            cleaned = {}
            for _name, _kn in all_knowledge.items():
                _c = state_guards.validate_knowledge_write(_name, _kn)
                if _c is not None:
                    cleaned[_name] = _c
            if cleaned:
                sqlite_store.upsert_knowledge_bulk(channel_id, cleaned)
        except Exception as _e:
            logging.debug(f"[V10] knowledge bulk mirror skipped: {_e}")
    return propagated

# NPC Behavioral Imprints
def update_npc_imprints(channel_id: str, imprints: Dict[str, Dict[str, str]], turn: int = 0) -> None:
    """NPC 행동 각인 저장. imprints: {NpcName: {"event": str, "mark": str}}"""
    d = get_domain(channel_id)
    all_imprints = d.setdefault("npc_imprints", {})
    for npc_name, imp in imprints.items():
        if not isinstance(imp, dict) or not imp.get("event"):
            continue
        resolved = _resolve_npc_name(d, npc_name)
        npc_list = all_imprints.setdefault(resolved, [])
        npc_list.append({"event": imp["event"], "mark": imp.get("mark", ""), "turn": turn})
        # 최근 5개만 유지
        all_imprints[resolved] = npc_list[-5:]
    save_domain(channel_id, d)

def get_npc_imprints(channel_id: str) -> Dict[str, list]:
    """전체 NPC 행동 각인 조회"""
    return get_domain(channel_id).get("npc_imprints", {})

# Rules & Genres
def get_rules(channel_id: str) -> str:
    """룰 텍스트 조회 (캐시 우선)"""
    cached = cache.get_rules(channel_id)
    if cached is not None:
        return cached
    text = load_text(get_rules_file_path(channel_id), config.DEFAULT_RULES)
    cache.set_rules(channel_id, text)
    return text

def append_rules(channel_id: str, text: str) -> None:
    """룰에 텍스트 추가"""
    d = get_domain(channel_id)
    if d.get("rules_mode") == "custom":
        cur = get_rules(channel_id)
        new_t = f"{cur}\n\n{text}"
    else:
        cust = d.get("custom_rules", "")
        cust = f"{cust}\n\n{text}" if cust else text
        d["custom_rules"] = cust
        d["rules_mode"] = "hybrid"
        save_domain(channel_id, d)
        new_t = f"{config.DEFAULT_RULES}\n\n[커스텀 추가]\n{cust}"

    cache.set_rules(channel_id, new_t)
    save_text(get_rules_file_path(channel_id), new_t)

def reset_rules(channel_id: str) -> None:
    """룰 초기화"""
    path = get_rules_file_path(channel_id)
    if os.path.exists(path):
        os.remove(path)
    cache.invalidate_rules(channel_id)
    d = get_domain(channel_id)
    d["custom_rules"] = ""
    d["rules_mode"] = "default"
    save_domain(channel_id, d)

def set_custom_rules_from_file(channel_id: str, content: str) -> None:
    """파일에서 커스텀 룰 설정"""
    cache.set_rules(channel_id, content)
    save_text(get_rules_file_path(channel_id), content)
    d = get_domain(channel_id)
    d["rules_mode"] = "custom"
    d["settings"]["growth_system"] = "custom"
    save_domain(channel_id, d)

def _coerce_genre_list(value: Any) -> List[str]:
    """str/list 기반 장르 값을 List[str]로 정규화."""
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list):
        return []

    out: List[str] = []
    for item in value:
        s = str(item).strip()
        if not s or s in out:
            continue
        out.append(s)
    return out


def normalize_active_genres(genres_raw: Any) -> List[str]:
    """active_genres(raw)를 평탄한 List[str]로 정규화.
    지원 형식:
    - "noir"
    - ["noir", "romance"]
    - {"stage"/"flavor"/"lens": [...]}
    - {"layers": {"world_setting"/"style_tech"/"narrative_tone": [...]}, ...}
    """
    if isinstance(genres_raw, (str, list)):
        return _coerce_genre_list(genres_raw)

    if isinstance(genres_raw, dict):
        merged: List[str] = []

        layers = genres_raw.get("layers", {})
        if isinstance(layers, dict):
            for key in ("world_setting", "style_tech", "narrative_tone"):
                for genre in _coerce_genre_list(layers.get(key, [])):
                    if genre not in merged:
                        merged.append(genre)
            if merged:
                return merged

        for key in ("stage", "flavor", "lens"):
            for genre in _coerce_genre_list(genres_raw.get(key, [])):
                if genre not in merged:
                    merged.append(genre)
        if merged:
            return merged

    return ["noir"]


def get_active_genres(channel_id: str) -> Any:
    """세션에 저장된 active_genres 원본 값을 반환."""
    return get_domain(channel_id).get("active_genres", ["noir"])


def get_active_genre_list(channel_id: str) -> List[str]:
    """active_genres를 표시/프롬프트용 List[str]로 반환."""
    return normalize_active_genres(get_active_genres(channel_id))


def set_active_genres(channel_id: str, genres: Any) -> None:
    d = get_domain(channel_id)
    d["active_genres"] = genres
    save_domain(channel_id, d)

def get_custom_tone(channel_id: str) -> Optional[str]:
    return get_domain(channel_id).get("custom_tone")

def set_custom_tone(channel_id: str, tone: Optional[str]) -> None:
    d = get_domain(channel_id)
    d["custom_tone"] = tone
    save_domain(channel_id, d)

def get_rules_mode(channel_id: str) -> str: return get_domain(channel_id).get("rules_mode", "default")

def get_growth_system(channel_id: str) -> str:
    settings: Dict[str, Any] = get_domain(channel_id).get("settings", {})
    return settings.get("growth_system", "default")


# =========================================================
# 4. PARTICIPANT & PC MANAGEMENT (Formerly domain_participant.py)
# =========================================================

def _create_default_participant(display_name: str) -> Dict[str, Any]:
    return {
        "mask": display_name, "status": "active",
        "notebook": "— [소지품] —\n\n— [메모] —",
        "status_effects": [],
        "ai_memory": {
            "appearance": "", "personality": "", "background": "", "relationships": {},
            "passives": [], "normalization": {}, "notes": "", "archived_info": [],
            # [V7→V3.0] Core Systems: 2-Axis (Vigor/Composure)
            "vigor": {"value": 100, "last_delta": 0},
            "composure": {"value": 100, "last_delta": 0},
            "abnormal_exposure": {}, # {Tag: {count: N, level: N}}
            
            # [Phase 2] Mnemosyne: PsychProfile
            "psych_profile": {
                "needs": {"survival": 50, "safety": 50, "love": 50, "esteem": 50, "self_actualization": 50},
                "values": ["security", "conformity"], # Default safe values
                "instinct": "neutral"
            }
        }
    }

def update_participant(channel_id: str, user, reset: bool = False, **kwargs) -> bool:
    d = get_domain(channel_id)
    uid = str(user.id)
    d.setdefault("participants", {})

    if reset or uid not in d["participants"]:
        d["participants"][uid] = _create_default_participant(user.display_name)
    else:
        d["participants"][uid]["status"] = "active"
        # Ensure schema
        if "ai_memory" not in d["participants"][uid]:
             d["participants"][uid]["ai_memory"] = _create_default_participant("")["ai_memory"]
    
    # Apply additional fields
    for k, v in kwargs.items():
        d["participants"][uid][k] = v
             
    save_domain(channel_id, d)
    return True

def get_participant_data(channel_id: str, user_id: str) -> Optional[Dict[str, Any]]:
    participants: Dict[str, Any] = get_domain(channel_id).get("participants", {})
    p = participants.get(str(user_id))
    if p is not None and not isinstance(p, dict):
        raise ValueError(f"Corrupted Participant Data for {user_id}: Expected dict, got {type(p).__name__} ({p})")
    return p

def get_active_participants(channel_id: str) -> Dict[str, Any]:
    """[V7] 활성 상태인 플레이어 데이터만 반환"""
    d = get_domain(channel_id)
    active = {}
    for uid, p in d.get("participants", {}).items():
        if p.get("status") == "active":
            active[uid] = p
    return active

def get_participant_status(channel_id: str, uid: str) -> str:
    p = get_participant_data(channel_id, uid)
    return p.get("status", "active") if p else "unknown"

def set_participant_status(channel_id: str, uid: str, status: str) -> None:
    d = get_domain(channel_id)
    if str(uid) in d["participants"]:
        d["participants"][str(uid)]["status"] = status
        save_domain(channel_id, d)

def save_participant_data(channel_id: str, user_id: str, data: Dict[str, Any]) -> None:
    d = get_domain(channel_id)
    d.setdefault("participants", {})[str(user_id)] = data
    save_domain(channel_id, d)

# PC Info & Masks
def set_default_pc_info(channel_id: str, pc_info: Dict[str, Any]) -> None:
    d = get_domain(channel_id)
    d["default_pc_info"] = pc_info
    save_domain(channel_id, d)

def get_default_pc_info(channel_id: str) -> Optional[Dict[str, Any]]:
    return get_domain(channel_id).get("default_pc_info")

def clear_default_pc_info(channel_id: str) -> None:
    d = get_domain(channel_id)
    d.pop("default_pc_info", None)
    save_domain(channel_id, d)

def get_user_mask(channel_id: str, uid: str) -> str:
    p = get_participant_data(channel_id, uid)
    return p.get("mask", "Unknown") if p else "Unknown"

def set_user_mask(channel_id: str, uid: str, mask: str) -> None:
    d = get_domain(channel_id)
    if str(uid) in d["participants"]:
        d["participants"][str(uid)]["mask"] = mask
        save_domain(channel_id, d)

def set_user_description(channel_id: str, uid: str, desc: str) -> None:
    # Used for simple storage
    p = get_participant_data(channel_id, uid)
    if p:
        p["ai_memory"]["appearance"] = desc # Map to AI memory
        save_participant_data(channel_id, uid, p)

def apply_pc_info_to_user(channel_id: str, user_id: str) -> bool:
    pc_info = get_default_pc_info(channel_id)
    if not pc_info: return False
    
    p = get_participant_data(channel_id, user_id)
    if not p: return False
    
    mem = p.get("ai_memory", {})
    if not mem:
        mem = _create_default_participant("")["ai_memory"]
        p["ai_memory"] = mem
    
    # Map basic PC info
    if pc_info.get("appearance"): mem["appearance"] = pc_info["appearance"]
    if pc_info.get("description"): mem["description"] = pc_info["description"]
    elif pc_info.get("personality"): mem["description"] = pc_info["personality"]
    
    if pc_info.get("background"): mem["background"] = pc_info["background"]
    
    # Passives Merge (Prevent Duplicates)
    new_passives = pc_info.get("passives", [])
    if new_passives:
        if "passives" not in mem: mem["passives"] = []
        current_names = [item['name'] if isinstance(item, dict) else str(item) for item in mem["passives"]]
        for np in new_passives:
            np_obj = np if isinstance(np, dict) else {"name": str(np), "desc": "Extracted"}
            name_key = np_obj.get("name", "Unknown")
            if name_key not in current_names:
                passive_entry = {
                    "name": name_key,
                    "desc": np_obj.get("desc", "Extracted from Template"),
                    "tags": np_obj.get("tags", ["Sync", "+Auto"]),
                    "acquired_at": time.strftime('%Y-%m-%d')
                }
                # Carry over theory_links and modifiers if present (Phase 4-1)
                if np_obj.get("theory_links"):
                    passive_entry["theory_links"] = np_obj["theory_links"]
                if np_obj.get("modifiers"):
                    passive_entry["modifiers"] = np_obj["modifiers"]
                mem["passives"].append(passive_entry)

    # Memos/Inventory Merge (Integrated with Notebook, per-user)
    # Notes/Memos
    notes = pc_info.get("notes") or pc_info.get("memos") or pc_info.get("background")
    if notes and isinstance(notes, (str, list)):
        _append_memo_to_notebook(channel_id, f"설정 동기화: {notes[:100]}...", user_id)

    # Inventory (v3 structured list + legacy dict 하위 호환)
    inv = pc_info.get("inventory")
    if inv:
        if isinstance(inv, list):
            # v3 구조화 형식: [{name, qty, tags, modifiers}]
            for item_obj in inv:
                if isinstance(item_obj, dict):
                    name = item_obj.get("name", "")
                    qty = item_obj.get("qty", 1)
                    if not name:
                        continue
                    structured = {"name": name, "qty": qty, "tags": item_obj.get("tags", [])}
                    if item_obj.get("modifiers"):
                        structured["modifiers"] = item_obj["modifiers"]
                    add_to_ai_memory_list(channel_id, user_id, "inventory", structured)
                    _append_memo_to_notebook(channel_id, f"{name} ({qty}개) - 설정 동기화", user_id)
                elif isinstance(item_obj, str) and item_obj.strip():
                    add_to_ai_memory_list(channel_id, user_id, "inventory", {"name": item_obj.strip(), "qty": 1, "tags": []})
                    _append_memo_to_notebook(channel_id, f"{item_obj.strip()} (1개) - 설정 동기화", user_id)
        elif isinstance(inv, dict):
            # Legacy {"Item": "Qty"} 형식 → 구조화 변환
            for item, qty in inv.items():
                add_to_ai_memory_list(channel_id, user_id, "inventory", {"name": item, "qty": int(qty) if str(qty).isdigit() else 1, "tags": []})
                _append_memo_to_notebook(channel_id, f"{item} ({qty}) - 설정 동기화", user_id)
    
    save_participant_data(channel_id, user_id, p)
    return True

def sync_matching_participants(channel_id: str, pc_info: Dict[str, Any]) -> List[str]:
    """[V4] 캐릭터 이름(Mask)이 일치하는 모든 플레이어에게 기본 설정을 자동 동기화합니다."""
    if not pc_info or not pc_info.get("name"): return []
    
    target_name = pc_info["name"].lower()
    d = get_domain(channel_id)
    updated_uids = []
    
    for uid, p_data in d.get("participants", {}).items():
        if p_data.get("mask", "").lower() == target_name:
            if apply_pc_info_to_user(channel_id, uid):
                updated_uids.append(uid)
                
    return updated_uids

def get_ai_memory(channel_id: str, uid: str) -> Dict[str, Any]:
    p = get_participant_data(channel_id, uid)
    return p.get("ai_memory", {}) if p else {}

def update_ai_memory(channel_id: str, uid: str, updates: Dict[str, Any]) -> None:
    p = get_participant_data(channel_id, uid)
    if not p: return
    
    mem = p.get("ai_memory", {})
    
    # Special handling for dictionaries (deep merge)
    for k, v in updates.items():
        if k == "relationships" and isinstance(v, dict):
            current_rels = mem.get("relationships", {})
            current_rels.update(v)
            mem[k] = current_rels
        else:
            mem[k] = v
            
    p["ai_memory"] = mem
    save_participant_data(channel_id, uid, p)

def update_npc_relationship(channel_id: str, uid: str, npc_name: str, rel_text: Union[str, int]) -> Union[str, int]:
    """[Extracted from Memory] Update specific NPC relationship in Player AI Memory"""
    d = get_domain(channel_id)
    npc_name = _resolve_npc_name(d, npc_name)
    update_ai_memory(channel_id, uid, {"relationships": {npc_name: rel_text}})
    return rel_text

def add_to_ai_memory_list(channel_id: str, uid: str, key: str, item: Union[str, Dict[str, Any]]) -> None:
    p = get_participant_data(channel_id, uid)
    if not p: return
    
    mem = p.get("ai_memory", {})
    if key not in mem: mem[key] = []
    
    if isinstance(mem[key], list):
        # [Fix] Deep Deduplication for Dict items (Passives, Inventory)
        is_duplicate = False
        if key in ("passives", "inventory") and isinstance(item, dict):
            new_name = item.get("name", "Unknown")
            for existing in mem[key]:
                if isinstance(existing, dict) and existing.get("name") == new_name:
                    is_duplicate = True
                    break
                elif isinstance(existing, str) and existing == new_name:
                    is_duplicate = True
                    break
        elif item in mem[key]:
             is_duplicate = True
             
        if not is_duplicate:
            mem[key].append(item)
        
    p["ai_memory"] = mem
    save_participant_data(channel_id, uid, p)

# [Phase 2] PsychProfile Accessors
def get_psych_profile(channel_id: str, uid: str) -> Dict[str, Any]:
    p = get_participant_data(channel_id, uid)
    if not p: return {}
    ai_mem: Dict[str, Any] = p.get("ai_memory", {})
    return ai_mem.get("psych_profile", {})

def update_psych_profile(channel_id: str, uid: str, profile_updates: Dict[str, Any]) -> None:
    p = get_participant_data(channel_id, uid)
    if not p: return
    
    if "psych_profile" not in p["ai_memory"]:
        p["ai_memory"]["psych_profile"] = {
             "needs": {"survival": 50, "safety": 50, "love": 50, "esteem": 50, "self_actualization": 50},
             "values": ["security"], "instinct": "neutral"
        }
    
    current = p["ai_memory"]["psych_profile"]
    
    # Deep merge for 'needs'
    if "needs" in profile_updates:
        current["needs"].update(profile_updates["needs"])
        del profile_updates["needs"]
        
    current.update(profile_updates)
    p["ai_memory"]["psych_profile"] = current
    save_participant_data(channel_id, uid, p)

def update_helena_metric(channel_id: str, npc_name: str, depth_delta: int = 0, tension_delta: int = 0) -> None:
    """[Phase 2] Update Helena metrics (Depth/Tension) for an NPC relation"""
    d = get_domain(channel_id)
    if "npc_attitudes" not in d: d["npc_attitudes"] = {}
    npc_name = _resolve_npc_name(d, npc_name)
    if npc_name not in d["npc_attitudes"]: return # Must exist first

    target = d["npc_attitudes"][npc_name]
    
    # Initialize if missing (Migration support)
    if "depth" not in target: target["depth"] = 0
    if "tension" not in target: target["tension"] = 0
    
    # Update and Clamp (0-100)
    target["depth"] = max(0, min(100, target["depth"] + depth_delta))
    target["tension"] = max(0, min(100, target["tension"] + tension_delta))
    target["last_updated"] = time.strftime('%Y-%m-%d %H:%M')

    save_domain(channel_id, d)
    _mirror_relation(channel_id, npc_name, target)  # [V10 Sprint 1] dual-write

# UI Helpers
def get_unified_player_info(channel_id: str, user_id: str) -> str:
    """
    [V8] 통합 플레이어 정보 반환 (프롬프트 주입용)
    - 캐릭터 이름/외모/배경
    - 상태 이상
    - 특질 (이름 + 설명)
    - 관계 (단계명 + 태도)
    - 기력/평정
    - 알고 있는 정보
    - 노트북
    """
    p = get_participant_data(channel_id, user_id)
    if not p:
        return "## 🎭 Unknown Player\n(No data available)"

    name = p.get("mask", "Unknown")
    mem = p.get("ai_memory", {})

    # 1. Description (Appearance + Personality + Background)
    desc_parts = []
    if mem.get("appearance"): desc_parts.append(f"Appearance: {mem['appearance']}")
    if mem.get("description"): desc_parts.append(f"Description: {mem['description']}")
    if mem.get("background"): desc_parts.append(f"Background: {mem['background']}")

    desc_text = "\n".join(desc_parts) if desc_parts else "No description available."

    # 2. Status Effects
    status_effects = p.get("status_effects", [])
    from game_character import format_status_effects
    status_text = format_status_effects(status_effects) or "Healthy (Normal)"

    # 3. Passives (Traits) — 이름 + 설명
    passives = mem.get("passives", [])
    passive_lines = []
    for pas in passives:
        if isinstance(pas, dict):
            pname = pas.get("name", "Unknown")
            pdesc = pas.get("desc", "")
            passive_lines.append(f"{pname}: {pdesc}" if pdesc else pname)
        else:
            passive_lines.append(str(pas))
    passive_text = " / ".join(passive_lines) if passive_lines else "None"

    # 4. Relationships — 태도(attitude) + 친밀 단계(depth stage)
    attitudes = get_npc_attitudes(channel_id)
    rel_parts = []
    if attitudes:
        from config import get_connection_stage_name
        for npc_name, att_data in attitudes.items():
            attitude = att_data.get("attitude", "neutral")
            depth = att_data.get("depth", 0)
            stage = get_connection_stage_name(depth)
            rel_parts.append(f"{npc_name}: {attitude} ({stage})")
    rel_text = ", ".join(rel_parts) if rel_parts else "None"

    # 5. Vigor/Composure Status
    vigor = mem.get("vigor", mem.get("mental", {}))
    vigor_val = vigor.get("value", 100)
    composure = mem.get("composure", {})
    composure_val = composure.get("value", 100)
    vc_text = f"기력 {vigor_val}/100 | 평정 {composure_val}/100"

    # 6. Known Info (PC가 알고 있는 정보)
    known_info = mem.get("known_info", [])
    if isinstance(known_info, list) and known_info:
        ki_text = " / ".join(str(k) for k in known_info[:10])
    elif isinstance(known_info, str) and known_info:
        ki_text = known_info
    else:
        ki_text = ""

    # 7. Notebook (per-user)
    notebook = get_notebook(channel_id, user_id)

    # 8. Construct Block
    lines = [f"## 🎭 {name} (Player Character)"]
    lines.append(f"- Status Condition: {status_text}")
    lines.append(f"- Vigor/Composure: {vc_text}")
    lines.append(f"- Traits: {passive_text}")
    lines.append(f"- Relationships: {rel_text}")
    if ki_text:
        lines.append(f"- Known Info: {ki_text}")
    lines.append(f"- Description:\n{desc_text}")
    lines.append(f"\n### 📓 Player Notebook (Inventory & Memos)\n{notebook}")
    lines.append(f"\n⚠️ CRITICAL: YOU ARE THE GM. {name} IS THE PLAYER.\nDO NOT speak for {name}. DO NOT describe {name}'s actions.\nOnly describe the world's reaction to {name}.")
    return "\n".join(lines)

# =========================================================
# 5. STATE ACCESSORS (From legacy domain_manager)
# =========================================================

def get_world_state(channel_id: str) -> Dict[str, Any]:
    ws = get_domain(channel_id).get("world_state", config.DEFAULT_WORLD_STATE.copy())
    # Backfill new fields for legacy sessions
    if "doom_clocks" not in ws or not isinstance(ws.get("doom_clocks"), list):
        ws["doom_clocks"] = []
    if "turn_index" not in ws or not isinstance(ws.get("turn_index"), int):
        ws["turn_index"] = 0
    return ws

def update_world_state(channel_id: str, state: Dict[str, Any]) -> None:
    d = get_domain(channel_id)
    d["world_state"] = state
    save_domain(channel_id, d)

def get_current_location(channel_id: str) -> str:
    ws = get_world_state(channel_id)
    return ws.get("current_location") or ws.get("location", "Unknown")

def set_current_location(channel_id: str, location: str) -> None:
    ws = get_world_state(channel_id)
    ws["current_location"] = location
    update_world_state(channel_id, ws)

def get_current_risk(channel_id: str) -> str:
    ws = get_world_state(channel_id)
    return ws.get("risk_level", "Low")

def set_current_risk(channel_id: str, risk: str) -> None:
    ws = get_world_state(channel_id)
    ws["risk_level"] = risk
    update_world_state(channel_id, ws)

def get_quest_board(channel_id: str) -> Dict[str, Any]:
    return get_domain(channel_id).get("quest_board")

def update_quest_board(channel_id: str, board: Dict[str, Any]) -> None:
    d = get_domain(channel_id)
    d["quest_board"] = board
    save_domain(channel_id, d)

# Settings
def is_session_locked(channel_id: str) -> bool:
    d = get_domain(channel_id)
    settings: Dict[str, Any] = d.get("settings", {})
    return settings.get("session_locked", False)

def set_session_lock(channel_id: str, locked: bool) -> None:
    d = get_domain(channel_id)
    d["settings"]["session_locked"] = locked
    save_domain(channel_id, d)

def update_settings(channel_id: str, **kwargs) -> None:
    d = get_domain(channel_id)
    if "settings" not in d: d["settings"] = {}
    for k, v in kwargs.items():
        d["settings"][k] = v
    save_domain(channel_id, d)

CORE_MODULES = {"judgment", "doom", "anomaly", "mental"}

def get_active_modules(channel_id: str) -> List[str]:
    """현재 활성화된 모듈 리스트를 반환합니다.
    핵심 4모듈(judgment, doom, anomaly, mental)은 항상 활성.
    board 등 부가 모듈만 토글 가능."""
    d = get_domain(channel_id)
    stored = set(d.get("settings", {}).get("active_modules", []))
    # 핵심 모듈은 항상 포함
    return list(CORE_MODULES | stored)

def toggle_module(channel_id: str, module_name: str, active: bool) -> None:
    """부가 모듈(board 등)을 켜거나 끕니다.
    핵심 4모듈은 토글 불가 (항상 활성)."""
    if module_name in CORE_MODULES:
        return  # 핵심 모듈은 항상 활성 — 토글 무시
    modules = set(get_active_modules(channel_id))
    if active:
        modules.add(module_name)
    else:
        modules.discard(module_name)
    update_settings(channel_id, active_modules=list(modules))

def set_response_mode(channel_id: str, mode: str) -> None:
    d = get_domain(channel_id)
    d["settings"]["response_mode"] = mode
    save_domain(channel_id, d)

def get_response_mode(channel_id: str) -> str:
    d = get_domain(channel_id)
    return d["settings"].get("response_mode", "auto")

def get_abnormal_mode(channel_id: str) -> bool:
    """비일상 적응도 시스템 활성화 여부 (Default: True)"""
    settings: Dict[str, Any] = get_domain(channel_id).get("settings", {})
    return settings.get("abnormal_mode", True)

def set_abnormal_mode(channel_id: str, enabled: bool) -> None:
    d = get_domain(channel_id)
    d["settings"]["abnormal_mode"] = enabled
    save_domain(channel_id, d)

# History
def append_history(channel_id: str, role: str, content: str, message_id: Optional[int] = None) -> None:
    """히스토리에 메시지를 추가합니다 (중복 제거).

    [LIBRA #2 C1 2026-04-28] message_id (Discord msg ID, optional) 보존.
    축약 자세 — DMA 12개 ID 보존이 아니라 1개. 사람의 흐릿한 출처 회상 비유.
    None이면 키 자체 생략 (legacy entry 호환).
    """
    d = get_domain(channel_id)
    
    # 중복 제거: 마지막 메시지와 동일한 content는 추가하지 않음
    if d["history"] and d["history"][-1].get("role") == role and d["history"][-1].get("content") == content:
        logging.debug(f"[History] 중복 메시지 무시: {role}")
        return
    
    entry = {"role": role, "content": content}
    if message_id is not None:
        entry["message_id"] = message_id

    # V8.5 (2026-05-23): 발효 시간 연동 — 메시지 생성 시점의 게임 시간 메타 저장.
    # 발효/Deep 요약 시 시간 거리 표현용. 트리거에는 영향 없음 (3a 안전 모드).
    try:
        world = get_world_state(channel_id)
        # 캘린더 마이그레이션 보장
        try:
            from game_world import _init_clock
            _init_clock(world)
        except Exception:
            pass
        entry["game_time"] = {
            "year": world.get("year", 1),
            "month": world.get("month", 1),
            "day": world.get("day", 1),
            "hour": world.get("hour", 12),
            "minute": world.get("minute", 0),
            "slot": world.get("time_slot", "오후"),
        }
    except Exception as _e_gt:
        logging.debug(f"[History] game_time meta skip: {_e_gt}")

    d["history"].append(entry)

    # 히스토리 길이 제한 (최근 항목 유지)
    if len(d["history"]) > config.MAX_HISTORY_LENGTH:
        removed = d["history"][:len(d["history"]) - config.MAX_HISTORY_LENGTH]
        d["history"] = d["history"][-config.MAX_HISTORY_LENGTH:]
        logging.debug(f"[History] 오래된 {len(removed)}개 메시지 제거 (최대: {config.MAX_HISTORY_LENGTH})")

    save_domain(channel_id, d)

    # [V10 Sprint 3] 영구 로그 append — JSON은 작업 창(trim/발효 소비), history_log는 전체 기록.
    # 의도적 비대칭: 여기서 INSERT만, trim/발효가 지워도 로그엔 남는다 (무한 기억 토대).
    try:
        import sqlite_store
        import state_guards
        clean = state_guards.validate_history_write(entry)
        if clean is not None:
            sqlite_store.append_history(channel_id, clean)
    except Exception as _e:
        logging.debug(f"[V10] history append mirror skipped: {_e}")

def get_history(channel_id: str) -> List[Dict[str, str]]:
    return get_domain(channel_id).get("history", [])

def get_pending_actions(channel_id: str) -> Dict[str, Dict]:
    """수동 모드에서 축적된 PC 행동을 수집.
    마지막 Model 응답 이후의 PC 메시지를 역매핑하여 반환.
    Returns: { user_id: {"mask": str, "actions": [str]} }
    """
    d = get_domain(channel_id)
    history = d.get("history", [])
    participants = d.get("participants", {})

    # mask → user_id 역매핑
    mask_to_uid = {}
    for uid, pdata in participants.items():
        if pdata.get("status") == "active":
            mask_to_uid[pdata.get("mask", "")] = uid

    # 마지막 "Model" 응답 이후의 PC 메시지 수집
    pending: Dict[str, Dict] = {}
    for entry in reversed(history):
        if entry.get("role") == "Model":
            break
        role = entry.get("role", "")
        uid = mask_to_uid.get(role)
        if uid:
            if uid not in pending:
                pending[uid] = {"mask": role, "actions": []}
            pending[uid]["actions"].append(entry.get("content", ""))

    # reverse로 시간순 복원 (append+reverse는 insert(0)보다 O(n) 효율)
    for uid in pending:
        pending[uid]["actions"].reverse()

    return pending

# =========================================================
# 6. CONTEXT GENERATORS (For AI)
# =========================================================

def get_party_status_context(channel_id: str) -> str:
    participants = get_domain(channel_id).get("participants", {})
    if not participants: return "Active Players: None"
    from game_character import format_status_effects
    active = []
    for _, p in participants.items():
        if p.get("status") != "active": continue

        mem = p.get("ai_memory", {})
        mask = p.get("mask", "Unknown")
        look = mem.get("appearance", "Unknown")[:50]
        cond = format_status_effects(p.get("status_effects", [])) or "Normal"
        active.append(f"[{mask}] Look:{look}, Cond:{cond}")
        
    return "### PARTY\n" + "\n".join(active) if active else "All players inactive."

# NPC Memory
def get_session_ai_memory(channel_id: str) -> Dict[str, Any]:
    return get_domain(channel_id).get("ai_session_memory", {})

def update_session_ai_memory(channel_id: str, updates: Dict[str, Any]) -> None:
    d = get_domain(channel_id)
    mem = d.get("ai_session_memory", {})
    mem.update(updates)
    mem["last_updated"] = time.strftime('%Y-%m-%d %H:%M')
    d["ai_session_memory"] = mem
    save_domain(channel_id, d)

def set_session_ai_memory(channel_id: str, data: Dict[str, Any]) -> None:
    d = get_domain(channel_id)
    d["ai_session_memory"] = data
    save_domain(channel_id, d)


# Narrative Tracker State (ai_session_memory 내 중첩)
def get_narrative_tracker_state(channel_id: str) -> Dict[str, Any]:
    mem = get_session_ai_memory(channel_id)
    import narrative_tracker
    return mem.get("narrative_tracker") or narrative_tracker.get_default_state()

def update_narrative_tracker_state(channel_id: str, state: Dict[str, Any]) -> None:
    d = get_domain(channel_id)
    mem = d.get("ai_session_memory", {})
    mem["narrative_tracker"] = state
    d["ai_session_memory"] = mem
    save_domain(channel_id, d)


# Scene Continuity (롤링 프레임 윈도우)
def get_scene_continuity(channel_id: str) -> Dict[str, Any]:
    """Scene continuity 데이터 조회. 구 포맷 자동 마이그레이션."""
    mem = get_session_ai_memory(channel_id)
    sc = mem.get("scene_continuity", {})

    # 마이그레이션: 구 포맷(dai_snapshot 직접) → 신 포맷(frames 배열)
    if "frames" not in sc:
        old_snap = sc.get("dai_snapshot", {})
        old_fp = sc.get("render_fingerprint", {})
        frames = []
        if old_snap or old_fp:
            frames.append({"dai_snapshot": old_snap, "render_fingerprint": old_fp, "turn": 0})
        return {"frames": frames, "discontinuity_flags": sc.get("discontinuity_flags", [])}

    return sc

def update_scene_continuity(
    channel_id: str,
    dai_snapshot: Dict[str, Any] = None,
    render_fingerprint: Dict[str, Any] = None,
    discontinuity_flags: list = None,
    turn_number: int = None
) -> None:
    """Scene continuity 갱신.
    - dai_snapshot → 새 프레임 PUSH (턴 시작)
    - render_fingerprint → 최신 프레임 UPDATE (배경 추출 완료 후)
    """
    sc = get_scene_continuity(channel_id)
    frames = sc.get("frames", [])

    if dai_snapshot is not None:
        frames.append({
            "dai_snapshot": dai_snapshot,
            "render_fingerprint": {},
            "turn": turn_number or 0
        })
        if len(frames) > config.FRAME_HISTORY_DEPTH:
            frames = frames[-config.FRAME_HISTORY_DEPTH:]
        sc["frames"] = frames

    if render_fingerprint is not None:
        if frames:
            frames[-1]["render_fingerprint"] = render_fingerprint
            sc["frames"] = frames

    if discontinuity_flags is not None:
        sc["discontinuity_flags"] = discontinuity_flags[:5]

    update_session_ai_memory(channel_id, {"scene_continuity": sc})

def check_sensory_habituation(channel_id: str) -> bool:
    """최근 3+ 프레임이 같은 location + 유사 palette/lighting이면 True."""
    sc = get_scene_continuity(channel_id)
    frames = sc.get("frames", [])
    if len(frames) < 3:
        return False
    recent = frames[-3:]
    locations = []
    palettes = []
    lightings = []
    for f in recent:
        snap = f.get("dai_snapshot", {})
        fp = f.get("render_fingerprint", {})
        locations.append(snap.get("location", ""))
        palettes.append(fp.get("palette", ""))
        lightings.append(fp.get("lighting", ""))
    # 같은 위치 + palette/lighting 모두 동일(빈 문자열 제외)
    if not locations[0]:
        return False
    if len(set(locations)) == 1 and len(set(p for p in palettes if p)) <= 1 and len(set(l for l in lightings if l)) <= 1:
        return True
    return False


def get_latest_frame(channel_id: str) -> Dict[str, Any]:
    """최신 프레임을 구 포맷({dai_snapshot, render_fingerprint})으로 반환."""
    sc = get_scene_continuity(channel_id)
    frames = sc.get("frames", [])
    if not frames:
        return {"dai_snapshot": {}, "render_fingerprint": {}}
    latest = frames[-1]
    return {
        "dai_snapshot": latest.get("dai_snapshot", {}),
        "render_fingerprint": latest.get("render_fingerprint", {})
    }


# Bot Active State
def get_bot_active(channel_id: str) -> bool:
    return get_domain(channel_id).get("bot_active", True)

def set_bot_active(channel_id: str, active: bool) -> None:
    d = get_domain(channel_id)
    d["bot_active"] = active
    save_domain(channel_id, d)
# OOC Mode Toggle (channel-level)
def get_ooc_mode(channel_id: str) -> bool:
    return get_domain(channel_id).get("ooc_mode", False)

def set_ooc_mode(channel_id: str, active: bool) -> None:
    d = get_domain(channel_id)
    d["ooc_mode"] = active
    save_domain(channel_id, d)

# Pending Flashback (회상 대기)
def get_pending_flashback(channel_id: str) -> Optional[Dict]:
    """대기 중인 회상 선언 조회. Returns {"content": str, "user_id": str} or None."""
    return get_domain(channel_id).get("pending_flashback")

def set_pending_flashback(channel_id: str, content: str, user_id: str) -> None:
    """회상 선언을 대기열에 저장."""
    d = get_domain(channel_id)
    d["pending_flashback"] = {"content": content, "user_id": user_id}
    save_domain(channel_id, d)

def clear_pending_flashback(channel_id: str) -> None:
    """회상 대기열 초기화."""
    d = get_domain(channel_id)
    d.pop("pending_flashback", None)
    save_domain(channel_id, d)

# Loadout (로드아웃 — BITD Load)
def get_loadout(channel_id: str, user_id: str) -> Optional[Dict]:
    """유저의 로드아웃 설정 조회. Returns {"total_slots": int, "used_slots": int, "items": list, "load_type": str} or None."""
    mem = get_ai_memory(channel_id, user_id)
    return mem.get("loadout")

def set_loadout(channel_id: str, user_id: str, load_type: str, slots: int, label: str) -> None:
    """로드아웃 초기 설정."""
    update_ai_memory(channel_id, user_id, {
        "loadout": {"total_slots": slots, "used_slots": 0, "items": [], "load_type": load_type, "label": label}
    })

def consume_loadout_slot(channel_id: str, user_id: str, slots_needed: int, item_name: str) -> bool:
    """로드아웃 슬롯 소비. 성공 시 True."""
    mem = get_ai_memory(channel_id, user_id)
    loadout = mem.get("loadout")
    if not loadout:
        return False
    remaining = loadout["total_slots"] - loadout.get("used_slots", 0)
    if slots_needed > remaining:
        return False
    loadout["used_slots"] = loadout.get("used_slots", 0) + slots_needed
    loadout.setdefault("items", []).append(item_name)
    update_ai_memory(channel_id, user_id, {"loadout": loadout})
    return True

# Training / Project Progress (다운타임 진행도)
def advance_training(channel_id: str, user_id: str, skill_name: str, progress: int = 1) -> Dict:
    """훈련 진행도 업데이트. Returns updated training entry."""
    mem = get_ai_memory(channel_id, user_id)
    training = mem.get("training_progress", {})
    entry = training.get(skill_name, {"progress": 0, "target": config.DOWNTIME_TRAIN.get("required_progress", 3)})
    entry["progress"] = entry.get("progress", 0) + progress
    training[skill_name] = entry
    update_ai_memory(channel_id, user_id, {"training_progress": training})
    return entry

def advance_project(channel_id: str, user_id: str, project_name: str) -> Optional[Dict]:
    """프로젝트 진행도 +1. Returns updated project or None."""
    mem = get_ai_memory(channel_id, user_id)
    projects = mem.get("projects", [])
    for proj in projects:
        if isinstance(proj, dict) and proj.get("name") == project_name:
            proj["filled"] = proj.get("filled", 0) + 1
            update_ai_memory(channel_id, user_id, {"projects": projects})
            return proj
    return None

# Storyteller State (세계 주도권 시스템)
def get_storyteller_state(channel_id: str) -> dict:
    ws = get_world_state(channel_id)
    st = ws.get("storyteller", {
        "last_event_turn": 0, "recent_categories": [],
        "recent_tags": [], "event_queue": [], "total_events_fired": 0,
        "recent_dice": [],
        # SD-Ba1 (2026-04-22): next_beats 큐 — LIBRA StoryAuthor nextBeats 최소 이식.
        #   beats: 자연어 비트 문자열 리스트 (최대 cap)
        #   last_planned_turn: 마지막 재계획 턴 (turn_index 기준)
        #   cap: 큐 최대 크기
        "next_beats": [], "last_planned_turn": 0, "beats_cap": 6,
    })
    # 기존 세션 하위호환: 필드가 없으면 기본값 주입
    if "next_beats" not in st:
        st["next_beats"] = []
    if "last_planned_turn" not in st:
        st["last_planned_turn"] = 0
    if "beats_cap" not in st:
        st["beats_cap"] = 6
    return st

def update_storyteller_state(channel_id: str, state: dict) -> None:
    ws = get_world_state(channel_id)
    ws["storyteller"] = state
    update_world_state(channel_id, ws)


def save_telescope_log(channel_id: str, turn: int, telescope_data: Dict[str, Any]) -> None:
    """Persist parsed telescope gate results with a rolling window of 10 turns."""
    d = get_domain(channel_id)
    logs = d.setdefault("telescope_logs", [])
    if not isinstance(logs, list):
        logs = []

    entry = dict(telescope_data or {})
    entry["turn"] = int(turn) if isinstance(turn, int) or str(turn).isdigit() else 0
    logs.append(entry)
    d["telescope_logs"] = logs[-10:]
    save_domain(channel_id, d)


def get_telescope_logs(channel_id: str, limit: Optional[int] = None) -> List[Dict[str, Any]]:
    """Return stored telescope logs (optionally last N)."""
    logs = get_domain(channel_id).get("telescope_logs", [])
    if not isinstance(logs, list):
        return []
    if isinstance(limit, int) and limit > 0:
        return logs[-limit:]
    return logs


def build_telescope_context(channel_id: str, n: int = 3) -> str:
    """V3: Telescope는 이제 5W1H 추론 기록 채널. FAIL 피드백 불필요."""
    return ""


def reset_session_state(channel_id: str) -> None:
    """
    세션을 '준비 완료' 상태로 초기화합니다.
    - 로어, 룰, 참가자 명단 유지
    - 히스토리, 발효 기억, 심층 기억 삭제
    - 월드 상태 초기화 (1일차, 오후)
    - 세션 NPC 및 퀘스트 초기화
    """
    d = get_domain(channel_id)

    # 1. Reset History
    d["history"] = []
    d["fermented_history"] = []
    d["deep_memory"] = ""
    d["ai_session_memory"] = _get_default_session()["ai_session_memory"]
    # [V10 Sprint 3] 영구 로그도 삭제 — 리셋=완전 새 이야기 (사용자 결정 2026-06-10).
    # fermented/deep 테이블은 함수 끝 save_domain의 스냅샷 미러가 빈 상태로 동기화.
    try:
        import sqlite_store
        sqlite_store.clear_history_log(channel_id)
        sqlite_store.clear_ledger(channel_id)  # [Sprint 4] 막간 장부도 동일 정책
    except Exception as _e:
        logging.debug(f"[V10] history log clear skipped: {_e}")
    
    # 2. Reset World State
    d["world_state"] = config.DEFAULT_WORLD_STATE.copy()
    d["settings"]["session_locked"] = False # Unlock for re-start
    
    # 3. Reset Quests & Notebook (Keep Lore Items if any? No, reset all dynamic)
    d["quest_board"] = {"active": [], "completed": [], "memos": [], "archive": [], "lore": []}
    d["notebook"] = "— [소지품] —\n\n— [메모] —"
    d["ooc_mode"] = False
    
    # 4. Reset Session NPCs (Keep 'lore' + 'manual' NPCs)
    # AI가 생성한 세션 NPC만 제거, 유저가 직접 등록한 NPC는 보존
    if "npcs" in d:
        kept_npcs = {}
        for name, data in d["npcs"].items():
            if data.get("source") in ("lore", "manual"):
                kept_npcs[name] = data
        d["npcs"] = kept_npcs
        # [V10 Sprint 2-B] npcs 테이블 미러 (clear_session_npcs와 동일 보존 정책)
        try:
            import sqlite_store
            sqlite_store.delete_npcs_except_sources(channel_id, ("lore", "manual"))
        except Exception as _e:
            logging.debug(f"[V10] reset npc mirror skipped: {_e}")

    # 5. Reset Participant Runtime State (vigor/composure/notebook — 로어 프로필은 유지)
    for uid, pdata in d.get("participants", {}).items():
        mem = pdata.get("ai_memory", {})
        mem["vigor"] = {"value": 100, "last_delta": 0}
        mem["composure"] = {"value": 100, "last_delta": 0}
        mem.pop("mental", None)  # 레거시 제거
        mem["abnormal_exposure"] = {}
        mem["normalization"] = {}
        mem["judgment_momentum"] = 0
        pdata["notebook"] = "— [소지품] —\n\n— [메모] —"
        pdata["status_effects"] = []

    save_domain(channel_id, d)

# =========================================================
# 5b. CHRONICLE STORAGE
# =========================================================

def get_chronicles(channel_id: str) -> list:
    """저장된 연대기 목록 조회."""
    return get_domain(channel_id).get("chronicles", [])

def get_latest_chronicle(channel_id: str) -> Optional[Dict]:
    """가장 최근 연대기 조회."""
    chronicles = get_chronicles(channel_id)
    return chronicles[-1] if chronicles else None

# =========================================================
# 6. UNE ADAPTER (Bridge)
# =========================================================



