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
import tempfile
import shutil
from typing import Dict, Any, Optional, List, Union, Tuple

import config
from cache_manager import cache

# =========================================================
# 1. FILE I/O & CACHING (Formerly domain_io.py)
# =========================================================

def initialize_folders() -> None:
    # [2026-09-14 W0] 채널 폴더로 접힘 — 만드는 건 CHANNELS_DIR 하나뿐.
    #   sessions/ lores/ rules/ 는 더 이상 만들지 않는다(채널 폴더 안으로 이사).
    for path in [config.CHANNELS_DIR]:
        if not os.path.exists(path):
            try:
                os.makedirs(path)
                logging.info(f"Created directory: {path}")
            except Exception as e:
                logging.error(f"Failed to create {path}: {e}")

initialize_folders()  # 모듈 임포트 시 자동 실행


# =========================================================
# [2026-09-14 W0] 채널 폴더 — data/channels/{channel_id}/
#   state.json / memory.db / lore/{lore,original,rules,summary}.txt
#   수명이 같은 것끼리 한 곳 → 리셋 = 폴더 삭제.
# =========================================================

def get_channel_dir(channel_id: str) -> str:
    """채널 폴더 경로. 만들지는 않는다(순수 함수)."""
    return os.path.join(config.CHANNELS_DIR, str(channel_id))


def _ensure_channel_dir(channel_id: str) -> str:
    """채널 폴더 + lore/ 생성(멱등). 실패해도 예외를 밖으로 던지지 않는다."""
    d = get_channel_dir(channel_id)
    try:
        os.makedirs(os.path.join(d, config.CHANNEL_LORE_DIR), exist_ok=True)
    except Exception as e:
        logging.error(f"Failed to create channel dir {d}: {e}")
    return d


def _channel_lore_path(channel_id: str, fname: str) -> str:
    return os.path.join(get_channel_dir(channel_id), config.CHANNEL_LORE_DIR, fname)


def get_session_file_path(channel_id: str) -> str: return os.path.join(get_channel_dir(channel_id), config.CHANNEL_STATE_FILE)
def get_lore_file_path(channel_id: str) -> str: return _channel_lore_path(channel_id, "lore.txt")
def get_lore_original_file_path(channel_id: str) -> str: return _channel_lore_path(channel_id, "original.txt")
def get_rules_file_path(channel_id: str) -> str: return _channel_lore_path(channel_id, "rules.txt")

def load_json(filepath: str, default_val: Any) -> Any:
    # [2026-09-05 P0-2] 파싱 실패는 조용히 삼키지 않는다: 원본을 .broken-<ts>로 격리하고
    # error 로그 + default 반환. 파일 없음은 기존대로 조용히 default.
    if not os.path.exists(filepath): return default_val
    try:
        with open(filepath, 'r', encoding='utf-8') as f: return json.load(f)
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        quarantine = f"{filepath}.broken-{int(time.time())}"
        try:
            os.replace(filepath, quarantine)
            logging.error("[Domain] corrupt session file quarantined: %s -> %s (%s)", filepath, quarantine, e)
        except Exception as move_err:
            logging.error("[Domain] corrupt session file could not be quarantined: %s (%s / %s)", filepath, e, move_err)
        return default_val
    except Exception as e:
        logging.error(f"JSON load error {filepath}: {e}")
        return default_val

def _atomic_write(filepath: str, writer) -> bool:
    """[2026-09-05 P0-1] 같은 디렉토리에 임시파일 -> flush+fsync -> os.replace(원자적).
    실패 시 임시파일 정리 후 False. 기존 계약(반환 bool) 불변.

    [2026-09-14 W0 §0-④] 새 채널 첫 저장 때 채널 폴더가 아직 없다 —
    아래 `os.makedirs(directory, exist_ok=True)`가 이미 그 자리를 덮는다
    (state.json이면 채널 폴더, lore/*.txt면 lore/까지). save_json/save_text는
    channel_id를 모르는 filepath 계약이라 _ensure_channel_dir을 따로 부르지 않는다."""
    directory = os.path.dirname(filepath) or "."
    tmp_path = None
    try:
        os.makedirs(directory, exist_ok=True)
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', delete=False,
                                         dir=directory, prefix=".tmp-",
                                         suffix=os.path.splitext(filepath)[1] or ".tmp") as f:
            tmp_path = f.name
            writer(f)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, filepath)
        return True
    except Exception as e:
        logging.error(f"Atomic write error {filepath}: {e}")
        if tmp_path:
            try: os.remove(tmp_path)
            except OSError: pass
        return False

def save_json(filepath: str, data: Any) -> bool:
    # [2026-09-24 감사 §5-2 성능 — 레티어스 판정] 들여쓰기 끔 + `json.dumps` 한 번. 세션 JSON 은 사람이 읽는 문서가
    #   아니라 작업 기억의 디스크 사본(읽기는 RAM 캐시 → 재시작 때만 파일)이다. `json.dump(indent=2)`는 순수 파이썬
    #   인코더라 턴당 ~15회 저장에서 루프를 붙잡았다(275KB 합성 실측 14ms → 3ms/회). 열어 볼 땐 편집기 정렬로.
    return _atomic_write(filepath, lambda f: f.write(json.dumps(data, ensure_ascii=False)))

def load_text(filepath: str, default_val: str) -> str:
    if not os.path.exists(filepath): return default_val
    try:
        with open(filepath, 'r', encoding='utf-8') as f: return f.read()
    except Exception as e:
        logging.error(f"Text load error {filepath}: {e}")
        return default_val

def save_text(filepath: str, text: str) -> bool:
    return _atomic_write(filepath, lambda f: f.write(text))

# =========================================================
# 2. CORE SESSION ACCESS
# =========================================================

def _get_default_session() -> Dict[str, Any]:
    return {
        "participants": {},
        "npcs": {},
        "history": [],
        "quest_board": {"active": [], "completed": [], "memos": [], "archive": [], "lore": []},
        # [2026-09-24 감사] deepcopy — 얕은 사본은 중첩 list/dict(doom_clocks·storyteller 등)를 config 기본값과 공유해
        #   새 채널에서의 첫 변형이 **기본값 자체**를 오염시킬 수 있었다.
        "world_state": __import__("copy").deepcopy(config.DEFAULT_WORLD_STATE),
        "settings": {
            "response_mode": "auto", 
            "session_locked": False, 
            "growth_system": "default",
            "scene_type": "normal",  # normal / gore / nsfw / gore_nsfw
            "active_modules": ["judgment", "doom", "anomaly", "mental"]
        },
        "active_genres": ["noir"],
        "custom_tone": None,
        "ai_session_memory": {
            # [2026-09-25 스레드 장부] active_threads/resolved_threads 삭제 — 정본은 thread_log(옛 JSON 값은 무해·무독자).
            "world_summary": "", "current_arc": "",
            "key_events": [], "foreshadowing": [], "world_changes": [], "npc_summaries": {},
            "party_dynamics": "", "last_updated": ""
        },
        # [2026-09-05 P4] fermented_history·deep_memory·deep_memory_data는 default에서 뺐다 —
        # 행이 정본이고, JSON에 있으면 save_domain 이음매가 행으로 보내고 dict에서 뺀다.
        # root active_memory_triggers는 JSON 소유(world_board:610)라 그대로 남긴다.
        "active_memory_triggers": [],
        "last_export_idx": 0,
        "last_chronicle_idx": 0,
        "telescope_logs": [],
        "bot_active": True,  # Default: Bot is ON
        "notebook": notebook_default(),  # [V5.1→v2 2026-09-06] 정본은 섹션 dict
        "notebook_shared": notebook_default(),  # [v2] 채널 스코프 섹션 — 이번 단계엔 쓰는 곳 0(자리만)
        "last_execution_context": None  # [!다시] Persistent retry data
    }


# =========================================================
# NOTEBOOK v2 — 섹션 dict 정본 (2026-09-06 P0, 스펙 §4-6)
# 저장 모양: {"v":2,"sections":{"소지품":{"items":{name:{"qty":int}}},
#                               "메모":{"user":[str],"llm":[str]},
#                               "일지":{"lines":[str]}}}
# 텍스트 한 덩이는 **표시 산출물**일 뿐 — notebook_render()가 옛 모양 그대로 뽑는다.
# WHY: 텍스트를 정본으로 두면 모든 ops가 리터럴 헤더 split에 의존해 구역 오염·유저 메모
#      소실(전문 덮어쓰기)이 구조적으로 재발한다. dict가 정본이면 2색·수량이 공짜.
# =========================================================
NOTEBOOK_SOJIPIN_HEADER = "— [소지품] —"
NOTEBOOK_MEMO_HEADER = "— [메모] —"
NOTEBOOK_JOURNAL_HEADER = "— [일지] —"


def notebook_default() -> Dict[str, Any]:
    """빈 노트북 dict. 리셋/신규 참가자/파싱 실패 폴백이 전부 여기 하나를 쓴다."""
    return {"v": 2, "sections": {
        "소지품": {"items": {}},
        "메모": {"user": [], "llm": []},
        "일지": {"lines": []},
    }}


def _nb_norm_line(s: str) -> str:
    return re.sub(r"\s+", " ", str(s or "").strip())


def _nb_parse_item_line(body: str):
    """'철 ×2' → ('철', 2). 수량 표기 없으면 qty=1."""
    m = re.match(r"^(.*?)\s*[×xX]\s*(\d+)$", body.strip())
    if m and m.group(1).strip():
        try:
            return m.group(1).strip(), max(1, int(m.group(2)))
        except Exception:
            pass
    return body.strip(), 1


def notebook_parse(data) -> Dict[str, Any]:
    """무엇이 들어와도 v2 dict로. (lazy 마이그레이션 — 읽기는 쓰지 않는다)
    - dict: 정규화해서 통과
    - str : 옛 텍스트 파싱(헤더 split · '- ' 유저줄 · '> ' LLM줄 · '×N' 수량)
    - None/기타: 기본값
    """
    nb = notebook_default()
    if data is None:
        return nb
    if isinstance(data, dict):
        src = data.get("sections") if isinstance(data.get("sections"), dict) else data
        soj = (src or {}).get("소지품") or {}
        items = soj.get("items") if isinstance(soj, dict) else None
        if isinstance(items, dict):
            for k, v in items.items():
                name = str(k).strip()
                if not name:
                    continue
                try:
                    q = int(v.get("qty", 1)) if isinstance(v, dict) else int(v)
                except Exception:
                    q = 1
                nb["sections"]["소지품"]["items"][name] = {"qty": max(1, q)}
        elif isinstance(items, list):
            for it in items:
                name = (it.get("name") if isinstance(it, dict) else str(it) or "").strip()
                if name:
                    q = int(it.get("qty", 1)) if isinstance(it, dict) else 1
                    nb["sections"]["소지품"]["items"][name] = {"qty": max(1, q)}
        memo = (src or {}).get("메모") or {}
        if isinstance(memo, dict):
            for color in ("user", "llm"):
                for l in (memo.get(color) or []):
                    t = _nb_norm_line(l)
                    if t and t not in nb["sections"]["메모"][color]:
                        nb["sections"]["메모"][color].append(t)
        journal = (src or {}).get("일지") or {}
        if isinstance(journal, dict):
            for l in (journal.get("lines") or []):
                t = _nb_norm_line(l)
                if t:
                    nb["sections"]["일지"]["lines"].append(t)
        return nb
    if not isinstance(data, str):
        return nb

    cur = None  # None = 헤더 이전(프리앰블) → 정보 손실 0을 위해 [메모] user로 회수
    for raw in data.splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith("—"):
            if "소지품" in line:
                cur = "소지품"; continue
            if "메모" in line:
                cur = "메모"; continue
            if "일지" in line:
                cur = "일지"; continue
            continue  # 알 수 없는 헤더는 버린다(구역 토글만 담당)
        if cur == "소지품":
            if line.startswith("-"):
                name, qty = _nb_parse_item_line(line.lstrip("-").strip())
                if name:
                    prev = nb["sections"]["소지품"]["items"].get(name, {}).get("qty", 0)
                    nb["sections"]["소지품"]["items"][name] = {"qty": prev + qty}
        elif cur == "일지":
            t = _nb_norm_line(line.lstrip("-").strip())
            if t:
                nb["sections"]["일지"]["lines"].append(t)
        else:  # 메모 또는 프리앰블
            color = "llm" if line.startswith(">") else "user"
            t = _nb_norm_line(line.lstrip(">").lstrip("-").strip())
            if t and t not in nb["sections"]["메모"][color]:
                nb["sections"]["메모"][color].append(t)
    return nb


NOTEBOOK_SHARED_JOURNAL_TAIL = 3


def notebook_render(nb, shared=None, shared_journal_tail: int = NOTEBOOK_SHARED_JOURNAL_TAIL) -> str:
    """dict → 옛 텍스트 모양. 읽기 소비자(컨텍스트·ctx.notebook_txt·!캐릭터·B-1 입력)는
    전부 이 결과를 받는다 — 호출부는 안 바뀐다.
    shared: 채널 스코프 섹션(d['notebook_shared']).

    shared_journal_tail: 공유 일지의 **노출 꼬리**(기본 3줄, 0=전량).
      보관 축과 노출 축을 가른다 — 데이터엔 NOTEBOOK_JOURNAL_MAX 줄이 남고
      notebook_log 엔 전량이 남지만, 컨텍스트로 나가는 건 최근 며칠뿐이다.
      일지가 길어지면 그날 장면이 아니라 요약이 프롬프트를 채우기 시작한다."""
    nb = notebook_parse(nb)
    sec = nb["sections"]
    blocks = []
    journal = sec["일지"]["lines"]
    if journal:
        blocks.append(NOTEBOOK_JOURNAL_HEADER + "\n" + "\n".join(f"- {l}" for l in journal))
    soji = [NOTEBOOK_SOJIPIN_HEADER]
    for name, meta in sec["소지품"]["items"].items():
        q = int(meta.get("qty", 1))
        soji.append(f"- {name}" + (f" ×{q}" if q > 1 else ""))
    blocks.append("\n".join(soji))
    memo = [NOTEBOOK_MEMO_HEADER]
    memo += [f"- {l}" for l in sec["메모"]["user"]]
    memo += [f"> {l}" for l in sec["메모"]["llm"]]
    blocks.append("\n".join(memo))
    out = "\n\n".join(blocks)
    if shared:
        shared_nb = notebook_parse(shared)
        ssec = shared_nb["sections"]
        try:
            tail = int(shared_journal_tail or 0)
        except (TypeError, ValueError):
            tail = 0
        if tail > 0:
            ssec["일지"]["lines"] = ssec["일지"]["lines"][-tail:]
        if ssec["소지품"]["items"] or ssec["메모"]["user"] or ssec["메모"]["llm"] or ssec["일지"]["lines"]:
            # 재귀 호출엔 shared 를 안 넘기므로 꼬리 인자도 의미가 없다(이미 잘라 두었다).
            out += "\n\n" + notebook_render(shared_nb)
    return out


def get_notebook_shared(channel_id: str) -> Dict[str, Any]:
    """채널 스코프(공유) 노트북 섹션. 자리만 판 상태 — 쓰는 곳 0(P2+에서 채운다)."""
    return notebook_parse(get_domain(channel_id).get("notebook_shared"))


def get_notebook_data(channel_id: str, user_id: str = "") -> Dict[str, Any]:
    """노트북 정본 dict. 옛 텍스트가 저장돼 있으면 읽는 김에 파싱만 한다(쓰지 않음)."""
    d = get_domain(channel_id)
    if user_id:
        p = d.get("participants", {}).get(user_id, {})
        nb = p.get("notebook")
        if nb is not None:
            return notebook_parse(nb)
    return notebook_parse(d.get("notebook"))


def update_notebook_data(channel_id: str, nb: Dict[str, Any], user_id: str = "") -> None:
    """노트북 dict 저장. user_id 있으면 participant 행(없으면 생성), 없으면 채널 레벨."""
    nb = notebook_parse(nb)
    d = get_domain(channel_id)
    if user_id:
        d.setdefault("participants", {}).setdefault(user_id, {})["notebook"] = nb
    else:
        d["notebook"] = nb
    save_domain(channel_id, d)


def get_notebook(channel_id: str, user_id: str = "") -> str:
    """PC별 노트북 **렌더 텍스트**(표시/프롬프트용). 정본은 get_notebook_data()."""
    return notebook_render(get_notebook_data(channel_id, user_id), get_notebook_shared(channel_id))


def update_notebook(channel_id: str, text, user_id: str = "") -> None:
    """[legacy shim] 텍스트를 받아도 파싱해서 dict로 저장한다.
    WHY 잔류: 옛 텍스트 경로가 어디선가 남아 있어도 저장 모양이 v2로 수렴하게."""
    update_notebook_data(channel_id, notebook_parse(text), user_id)


def _append_memo_to_notebook(channel_id: str, content: str, user_id: str = "") -> None:
    """채널/유저 메모 append — **유저색(-) 줄**. dedup은 공백 정규화 기준(기존 규칙 유지)."""
    nb = get_notebook_data(channel_id, user_id)
    t = _nb_norm_line(content)
    if not t:
        return
    memo = nb["sections"]["메모"]
    if t in memo["user"] or t in memo["llm"]:
        return
    memo["user"].append(t)
    update_notebook_data(channel_id, nb, user_id)


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

    # [V10 P4] 레거시 파일에 남은 fermented/deep 세 키 정리(이주 또는 폐기). 캐시 적재 전 1회.
    _migrate_legacy_history_keys(channel_id, data)

    # Ensure keys — default에 세 키가 없으므로 여기서 다시 생기지 않는다
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
    # [V10 P4] 이음매 — 세 키가 dict에 있으면 행으로 보내고 dict에서 뺀다.
    # !다시·!복구·reset_domain·apply_ferment_result 등 세 키를 dict에 넣는 모든 옛 경로가
    # 여기 하나를 통과한다. save_json 앞이어야 JSON에 안 남는다.
    _route_history_keys_to_rows(channel_id, data)

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

    return True

# =========================================================
# [2026-09-05 발효 계약] 발효 결과 적용
# =========================================================

def apply_ferment_result(channel_id: str, result) -> bool:
    """발효 결과를 live 도메인에 얹는다. get_domain→대입→save_domain 사이 await 0 (동기 RMW)."""
    if not result.changed:
        return False
    d = get_domain(channel_id)
    # 검출 1줄 — 발효가 든 스냅샷 이후 몇 턴이 지났나 (가설 "6턴마다 뚝" 실측용)
    live_ti = int((d.get("world_state") or {}).get("turn_index", 0) or 0)
    if live_ti > result.snapshot_turn_index:
        logging.info("[Fermentation] apply over %d newer turn(s): held=%d live=%d",
                     live_ti - result.snapshot_turn_index, result.snapshot_turn_index, live_ti)
    for k, v in result.values.items():
        if k == "history":
            continue
        d[k] = v
    if result.history_after is not None:
        d["history"] = _merge_ferment_history(d.get("history") or [], result.history_before, result.history_after)
    for uid in result.participants_archive_cleared:
        mem = ((d.get("participants") or {}).get(uid) or {}).get("ai_memory")
        if isinstance(mem, dict):
            mem["archived_info"] = []
            mem["archived_foreshadowing"] = []
    # [V10 P4] 행 쓰기는 save_domain 이음매(_route_history_keys_to_rows)가 한다 — 여기서 안 쓴다.
    return save_domain(channel_id, d)


def _merge_ferment_history(live: list, before: list, after: list) -> list:
    """발효는 스냅샷 history의 **앞부분**만 손댄다(GC 마커 치환, 청크 12 절단).
    live = before + 그 사이 append된 꼬리 — 이면 after + 꼬리. (fast path, 정상 경로)
    prefix가 어긋나면(!다시 복원·수동 편집 등) 소비 집합 제거로 폴백."""
    n = len(before)
    if live[:n] == before:
        tail = live[n:]
        logging.debug("[Fermentation] history merge fast-path: +%d newer entries kept", len(tail))
        return after + tail
    # fallback: before에 있었는데 after에 없는 엔트리를 live에서 첫 일치 1개씩 제거
    removed = [e for e in before if e not in after]
    out = list(live)
    for e in removed:
        try:
            out.remove(e)   # dict 등가 = 엔트리 전체 일치(role+content+message_id+turn+game_time)
        except ValueError:
            pass
    logging.warning("[Fermentation] history prefix drifted (live=%d before=%d): fallback removed %d/%d",
                    len(live), n, len(live) - len(out), len(removed))
    return out


# =========================================================
# [V10 P4 / 2026-09-05] fermented/deep 이음매 — 행이 정본
# =========================================================
_HISTORY_ROW_KEYS = ("fermented_history", "deep_memory", "deep_memory_data")

if getattr(config, "V10_HISTORY_STRIP_JSON", False) and not getattr(config, "V10_HISTORY_READ_FROM_SQLITE", False):
    logging.error("[V10] V10_HISTORY_STRIP_JSON=True인데 V10_HISTORY_READ_FROM_SQLITE=False — "
                  "JSON에서 뺀 키를 읽을 곳이 없다. config를 맞춰라.")


def _route_history_keys_to_rows(channel_id: str, data: Dict[str, Any]) -> None:
    """[V10 P4] dict에 세 키가 있으면 행으로 보내고 dict에서 뺀다.

    행 쓰기 실패 시 그 키를 JSON에 남긴다(퇴화 모드 — 다음 save에서 재시도).
    STRIP OFF(롤백)면 행에는 계속 쓰되 pop만 안 한다 = P3 dual 그대로.
    """
    present = [k for k in _HISTORY_ROW_KEYS if k in data]
    if not present:
        return
    strip = bool(getattr(config, "V10_HISTORY_STRIP_JSON", False))
    try:
        import sqlite_store
    except Exception as _e:
        logging.error(f"[V10] history row routing 불가 (sqlite_store import 실패): {_e}")
        return
    ok_f = ok_d = True
    if "fermented_history" in data:
        fh = data.get("fermented_history")
        ok_f = sqlite_store.sync_fermented(channel_id, fh if isinstance(fh, list) else [])
    if "deep_memory" in data or "deep_memory_data" in data:
        # 한쪽만 있으면 다른 쪽은 현재 행 값으로 보충 (sync_deep은 통째 upsert라 부분 쓰기 불가)
        cur = sqlite_store.read_deep(channel_id) or {"narrative": "", "data": {}}
        nar = data.get("deep_memory", cur.get("narrative", "")) or ""
        dat = data.get("deep_memory_data", cur.get("data", {})) or {}
        ok_d = sqlite_store.sync_deep(channel_id, nar if isinstance(nar, str) else str(nar),
                                      dat if isinstance(dat, dict) else {})
    if strip:
        if ok_f:
            data.pop("fermented_history", None)
        if ok_d:
            data.pop("deep_memory", None)
            data.pop("deep_memory_data", None)
    if not (ok_f and ok_d):
        logging.error("[V10] history rows write failed (fermented=%s deep=%s) — keys kept in JSON for retry",
                      ok_f, ok_d)


def _migrate_legacy_history_keys(channel_id: str, data: Dict[str, Any]) -> None:
    """[V10 P4] get_domain 캐시 미스 시 1회 — 레거시 JSON 파일에 남은 세 키를 정리한다.

    행이 비었으면 파일 값을 행으로 이주, 행이 이미 있으면 파일 값은 stale 사본이므로 버린다.
    파일 재쓰기는 하지 않는다(다음 save_domain이 자연히 뺀다).
    주의: 여기서 get_fermented_history/get_deep_memory(게터)를 쓰면 get_domain 재귀다 — sqlite_store 직접."""
    if not getattr(config, "V10_HISTORY_STRIP_JSON", False):
        return
    if not any(k in data for k in _HISTORY_ROW_KEYS):
        return
    try:
        import sqlite_store
        migrated, dropped = [], []
        if "fermented_history" in data:
            rows = sqlite_store.read_fermented(channel_id)
            fh = data.get("fermented_history") or []
            if rows is None:
                return                     # 연결 실패 — 아무것도 버리지 않는다(퇴화 모드 유지)
            if not rows and fh:
                sqlite_store.sync_fermented(channel_id, fh); migrated.append("fermented_history")
            else:
                dropped.append("fermented_history")
            data.pop("fermented_history", None)
        if "deep_memory" in data or "deep_memory_data" in data:
            row = sqlite_store.read_deep(channel_id)
            j_nar = data.get("deep_memory", "") or ""
            j_dat = data.get("deep_memory_data", {}) or {}
            if row is None:
                return
            if not (row.get("narrative") or row.get("data")) and (j_nar or j_dat):
                sqlite_store.sync_deep(channel_id, j_nar, j_dat); migrated.append("deep_memory")
            else:
                dropped.append("deep_memory")
            data.pop("deep_memory", None)
            data.pop("deep_memory_data", None)
        if migrated:
            logging.info("[V10] legacy history keys migrated to rows: %s (%s)", ", ".join(migrated), channel_id)
        if dropped:
            logging.info("[V10] legacy history keys dropped from JSON (rows exist): %s (%s)",
                         ", ".join(dropped), channel_id)
    except Exception as _e:
        logging.warning(f"[V10] legacy history migration skipped: {_e}")


def reset_domain(channel_id: str) -> None:
    """채널의 모든 데이터 초기화.

    [2026-09-14 W0] 리셋 = 채널 폴더 삭제. 종전의 파일별 삭제(세션 JSON·lore·original·
    rules·summary)는 폴더 삭제 하나로 접힌다. 레거시 모드(sqlite_store._DB_PATH 설정)
    에서는 DB가 폴더 밖 단일 파일이라 delete_channel_rows가 행 목록 삭제를 계속 맡는다.
    !리셋은 채널을 재생성(새 id)하지만, 같은 id로 이어 써도 다음 저장에서 폴더가 다시 생긴다."""
    # 캐시 무효화 먼저 — 폴더가 사라진 뒤 옛 값이 되살아나지 않게.
    cache.invalidate_all(channel_id)

    # [V10] SQLite 행/파일 정리 — 연결 닫기 포함. 폴더 삭제 전에 해야 Windows에서 잠기지 않는다.
    try:
        import sqlite_store
        sqlite_store.delete_channel_rows(channel_id)
    except Exception as _e:
        logging.debug(f"[V10] channel rows delete skipped: {_e}")

    # [2026-09-14 W4] 휘발 회상 흔적 dict — 폴더 삭제는 프로세스 메모리를 못 건드린다.
    try:
        import fermentation
        fermentation.forget_channel_recall(channel_id)
    except Exception as _e:
        logging.debug(f"[Wiki] forget_channel_recall skipped: {_e}")

    # 폴더 통째 삭제 (없으면 무시)
    d = get_channel_dir(channel_id)
    try:
        shutil.rmtree(d, ignore_errors=True)
    except Exception as e:
        logging.warning(f"Failed to delete channel dir {d}: {e}")

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

# [2026-08-11 연대기 내보내기 통합] domain["chronicles"](자동/수동 연대기)는 10개 롤링이라
# 인덱스 커서가 못 쓰인다(앞이 잘리면 인덱스 밀림) → 타임스탬프 워터마크로 증분 추적.
def get_last_chronicle_export_ts(channel_id: str) -> float:
    try:
        return float(get_domain(channel_id).get("last_chronicle_export_ts", 0.0) or 0.0)
    except (TypeError, ValueError):
        return 0.0

def set_last_chronicle_export_ts(channel_id: str, ts: float) -> None:
    d = get_domain(channel_id)
    d["last_chronicle_export_ts"] = float(ts)
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
    # [2026-09-14 W0] 채널 폴더 lore/summary.txt
    return _channel_lore_path(channel_id, "summary.txt")

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
        # [2026-07-28] 구 코드는 레거시 키 `desc`만 읽었다. **현행 등록 경로는 전부 `description`을
        # 쓰므로**(cmd_npc 4모드·add_lore_npcs·register_ai_npc 전부, `desc` 쓰기 경로 0건)
        # 이 블록은 사실상 모든 NPC에서 설명이 '-'로 찍히고 있었다.
        # 이 함수의 산출물은 Slot 8 최종 폴백(청크 RAG·relevant_context 둘 다 실패 시)이라
        # 하필 가장 아쉬운 순간에 NPC 설명이 통째로 비었다.
        _desc = d.get("description") or d.get("desc") or "-"
        sec += f"{n} ({d.get('status','Active')})\n{_desc}\n\n"
    return lore + sec

# NPCs
# [2026-09-18 식별 허브 S1] 몹 표식 정규형 — 키는 **꼬리형 한 가지**(`경비병 #2A`).
#   산문엔 표식이 나오지 않지만(설계: identity_hub_design_v0.1_2026-09-18) 사람이 손으로 앞에
#   칠 수 있고(`#2A 경비병`), 모델·명령어가 공백·대소문자를 흔든다. 한 모양으로 접어 키가 갈리지 않게 한다.
_TAG_HEAD_RE = re.compile(r'^#([A-Za-z0-9]{2}|\d{4})\s+')
_TAG_TAIL_RE = re.compile(r'\s*#([A-Za-z0-9]{2}|\d{4})$')


def _normalize_npc_name(name: str) -> str:
    """NPC 이름 정규화: 괄호 주변 공백 통일 + 몹 표식 꼬리형 통일.
    '리미 (Limi)' → '리미(Limi)' / '#2a 경비병'·'경비병#2A' → '경비병 #2A'"""
    name = name.strip()
    name = re.sub(r'\s+\(', '(', name)
    name = re.sub(r'\(\s+', '(', name)
    name = re.sub(r'\s+\)', ')', name)
    _h = _TAG_HEAD_RE.match(name)
    if _h:
        name = (name[_h.end():].strip() + ' #' + _h.group(1).upper()).strip()
    name = _TAG_TAIL_RE.sub(lambda m: ' #' + m.group(1).upper(), name)
    return name

def _is_hangul(s: str) -> bool:
    """문자열이 전부 완성형 한글인지."""
    return bool(s) and all('가' <= c <= '힣' for c in s)

def _name_forms(s: str) -> tuple:
    """이름 문자열의 동일성 비교 단위 추출.
    Returns (base, inner, forms): base=괄호 앞, inner=괄호 안, forms={정규화전체/base/inner}(lower)."""
    base = re.split(r'[(\[（]', s)[0].strip()
    m = re.search(r'[(\[（]([^)\]）]+)[)\]）]', s)
    inner = m.group(1).strip() if m else ""
    forms = {f.lower() for f in (_normalize_npc_name(s), base, inner) if f}
    return base, inner, forms

def _short_tokens(s: str) -> set:
    """키에서 뽑는 축약형 후보 토큰. 공백 분리 토큰(≥2자) + 한글 성씨드롭 이름(3-4자→성 1자 제거).
    예: 'Kuromiya Reina(쿠로미야 레이나)' → {kuromiya, reina, 쿠로미야, 레이나}
        'Yoon Seo-rin(윤서린)' → {yoon, seo-rin, 윤서린, 서린}"""
    # [2026-09-18 식별 허브 S1] 표식 달린 키는 축약형 후보에서 제외.
    #   표식이 붙었다는 것 자체가 "역할명을 여럿이 공유한다"는 표지다 — 역할명 단독 질의(`경비병`)가
    #   표식 인물(`경비병 #2A`)에 닿으면 남의 시트에 관찰이 쌓인다(F1, 실측 오병합).
    #   ★경위: 이 흡수는 표식이 히스토리에서 벗겨지던 시절 몹에 닿는 **유일한 길**이었다.
    #   표식이 키에 보존되고 무대 명부가 급식되는 지금은 보상 동작이 해악으로 뒤집힌다.
    base = re.split(r'[(\[（]', _normalize_npc_name(s))[0].strip()
    if _TAG_TAIL_RE.search(base):
        return set()
    m = re.search(r'[(\[（]([^)\]）]+)[)\]）]', s)
    inner = m.group(1).strip() if m else ""
    toks: set = set()
    for form in (base, inner):
        if not form:
            continue
        fl = form.lower()
        for t in fl.split():
            if len(t) >= 2:
                toks.add(t)
        # 한글 글자붙임 이름: '윤서린'→'서린', '강채윤'→'채윤' (성 1자 가정)
        if _is_hangul(form) and " " not in form and 3 <= len(form) <= 4:
            toks.add(fl[1:])
    return toks

# [V10 Sprint 2-B] NPC 본체 — JSON 진실원천 + npcs 문서테이블 dual-write.
# [2026-07-04 정정] 읽기는 config.V10_NPCS_READ_FROM_SQLITE 게이트 — 현재 값 = True(읽기 ON,
#   read-through). 즉 get_npcs는 npcs 테이블에서 읽는다. 쓰기는 반드시 이 모듈의 미러(_mirror_npc/
#   upsert_npc) 경유여야 stale 안 됨(직접 d["npcs"] 변형+save_domain만 하는 경로 금지).
# 단건(get_npc)은 별칭 해상도(_find_npc_key)가 전체 dict를 요구하므로 get_npcs 경유 유지.

# [2026-09-16 시트 2차b] NPC 시트 원문의 정본 = 위키 인물 페이지 lore 절.
#   **쓰기 관문 하나**: NPC dict가 저장 관문(update_npc·_mirror_npc)에 들어오면 `description`/`desc`를
#   dict에서 **빼서** 페이지로만 보낸다(`wiki_store.set_sheet_text` → `set_lore_sections`). dict에는
#   원문 키가 저장되지 않는다. 들어온 원문이 지금 페이지 조립본과 같으면(= get_npc() 뷰를 그대로
#   되돌려 쓴 경우) 쓰기 0. 원문 키가 없으면 페이지 lore 절은 **무접촉**(부분 dict가 원문을 지우지 않는다).
#   세션 NPC(원문 없음)는 lore 절 없이 페이지만(WIKI_PAGES) — play 절 Observed는 grow_sheet 몫.
#   읽기는 `get_npcs()`가 페이지 lore 절을 조립해 `description`을 채운다(파생, 캐시 없음).
_NPC_TEXT_KEYS = ("description", "desc")
_NPC_DESC_PLACEHOLDERS = ("auto-detected by ai", "auto-detected by ai.")


def _npc_page_id(npc_name: str) -> str:
    import wiki_store
    return wiki_store.page_id_for("character", npc_name)


def _authored_source(hint: Any) -> str:
    """원문 있는 인물의 출처 표기 — 로어 업로드로 온 것은 "lore", 그 외 "manual"."""
    return "lore" if str(hint or "").lower() == "lore" else "manual"


def _npc_sheet_gate(channel_id: str, npc_name: str, data: Dict[str, Any]) -> None:
    """NPC 쓰기 관문(원문→페이지 + source 파생 도장). data를 **제자리에서** 고친다.

    [2026-09-22 voice_seed §H] `_seed_stamp`(=§G 관문이 시드 원문을 넣으며 세운 표식)가 있으면
    페이지 source 를 `"seed"` 로 찍는다. 시드도 lore 절에 앉기 때문에 도장이 없으면 그 즉시
    manual 로 읽혀 동결·몹 태그 제외가 따라온다(§H). 도장은 **여기서 pop** 한다 —
    저장소 장애로 일찍 돌아가는 길에서도 dict 에 남지 않게 맨 앞에서 뗀다."""
    if not isinstance(data, dict) or not channel_id or not npc_name:
        return
    _seed = bool(data.pop("_seed_stamp", None))
    try:
        import wiki_store
        _avail = wiki_store.store_available(channel_id)
    except Exception:
        _avail = False
    if not _avail:
        # 저장소 장애 = "원문 없음"이 아니다 — source를 session으로 떨구지 않고(클리어가 작가 시트를
        #   지우는 반사실형 실패 방지) 원문 키도 빼지 않는다(유실 방지). 다음 정상 쓰기가 페이지로 옮긴다.
        _h = str(data.get("source") or "").lower()
        data["source"] = _h if _h in ("lore", "manual") else "session"
        logging.error(f"[Wiki] npc sheet gate: 위키 저장소 불가 — 원문 보류 {channel_id}/{npc_name}")
        return
    text = ""
    for _k in _NPC_TEXT_KEYS:
        _v = data.pop(_k, None)
        if not text and isinstance(_v, str) and _v.strip():
            text = _v.strip()
    if text.lower() in _NPC_DESC_PLACEHOLDERS:
        text = ""
    try:
        import wiki_store
        pid = wiki_store.page_id_for("character", npc_name)
        aliases = data.get("aliases") if isinstance(data.get("aliases"), list) else None
        turn = data.get("updated_turn")
        # [voice_seed §H] 지금 페이지에 시드 도장이 서 있나 — 이번 쓰기가 **원문을 새로 앉히지 않으면**
        #   그 도장은 유지된다. 유지하지 않으면 세션 NPC의 흔한 부분 갱신(면모 병합·쿨다운 틱 등)이
        #   매번 source="session"으로 도장을 덮고, 그 다음 읽기가 "절은 있는데 도장은 seed가 아님"
        #   = manual 로 판정해 시드 NPC가 조용히 동결된다(§H 기각안 H-3이 뒷문으로 들어오는 꼴).
        _seed_now = (wiki_store.page_source(channel_id, pid) == wiki_store.SEED_SOURCE)
        _wrote = False
        if text and text != wiki_store.assemble_lore_text(wiki_store.get_lore_sections(channel_id, pid)):
            if wiki_store.ensure_page(channel_id, "character", npc_name, aliases=aliases,
                                      source=(wiki_store.SEED_SOURCE if _seed
                                              else _authored_source(data.get("source"))), turn=turn):
                n = wiki_store.set_sheet_text(channel_id, pid, text, turn)
                _wrote = True
                logging.info(f"[Wiki] npc sheet page={pid} sections={n}"
                             + (" source=seed" if _seed else ""))
        # [voice_seed §H] 승격 판정 = 절 유무 ∧ 도장≠seed. 절 유무만 보면 시드가 manual로 승격한다.
        authored = wiki_store.is_authored_page(channel_id, pid)
        data["source"] = _authored_source(data.get("source")) if authored else "session"
        _keep_seed = _seed or (_seed_now and not _wrote)
        if authored or getattr(config, "WIKI_PAGES", False):
            wiki_store.ensure_page(channel_id, "character", npc_name, aliases=aliases,
                                   source=(wiki_store.SEED_SOURCE if _keep_seed else data["source"]),
                                   turn=turn)
    except Exception as _e:
        logging.warning(f"[Wiki] npc sheet gate 실패: {channel_id}/{npc_name}: {_e}")
        data["source"] = derive_npc_source(data)
        if text and not any(k in data for k in _NPC_TEXT_KEYS):
            data["description"] = text          # 쓰기 실패 = 원문 보류(유실 0)


def _npc_seed_gate(channel_id: str, final_key: str, name: str, data: Dict[str, Any],
                   session: Optional[Dict[str, Any]] = None) -> None:
    """[2026-09-22 voice_seed §G] 세션 NPC 탄생 시 버퍼 시드 소비. data를 **제자리에서** 고친다.

    호출 위치 = `update_npc`, `_wiki_rename_npc`·`_npc_sheet_gate` **앞** 한 곳.
    세션 NPC 탄생 경로가 셋인데 셋 다 여기를 지나기 때문이다(단일 소비점):
      ① `orchestration._npc_roster_pass` 스텁 생성 — `npc_manager.update_npc(... status/lore_seen)`
      ② `npc_manager.register_ai_npc` (new_individual 몹 태그) — 끝에서 `update_npc(태그된 이름)`
      ③ `orchestration` psyche relation 자동생성 — `npc_manager.update_npc(... source=session)`,
         **렌더 전**이라 ①보다 먼저 등록해 버릴 수 있다. 그래서 소비를 로스터 패스에 두면 안 된다.
    (①②③ 모두 `npc_manager.update_npc` → 여기. 그 함수는 자동 추출만 얹고 그대로 넘긴다.)

    조건 둘 — data에 원문 키가 **없고**(작가·수동 쓰기가 아니고) ∧ 페이지에 lore 절이 **없다**
    (이미 시트가 있는 인물 위에 시드를 얹지 않는다, v4 §10 lore/manual 무접촉).
    소비하면 원문은 `description` 으로 넣고 기존 `_npc_sheet_gate` 경로(pop → set_sheet_text)를
    그대로 탄다 — **새 쓰기 경로 0**. 저장소 장애면 그 관문이 원문을 dict에 보류하므로 유실도 0.

    `session` = 호출자가 들고 있는 세션 스냅숏(`get_domain` 반환값). **필수에 가깝다**:
    `get_domain`은 캐시의 **deep copy**를 주므로, 소비(버퍼 쓰기)가 저장된 뒤에 호출자가
    자기 스냅숏을 `save_domain` 하면 옛 `pending_seeds`가 되살아난다(= 시드가 무한 재소비).
    그래서 소비 직후 스냅숏의 버퍼도 최신본으로 갈아 끼운다.

    VOICE_SEED OFF: `buffer_take`가 곧장 None → 무동작(버퍼도 비어 있다).
    시드는 best-effort다 — 여기서 나는 예외는 삼키고 로그만 남긴다(등록 자체를 막지 않는다).
    """
    if not isinstance(data, dict) or not channel_id or not final_key:
        return
    try:
        if any(k in data for k in _NPC_TEXT_KEYS):
            return
        import wiki_store
        if wiki_store.has_lore_sections(channel_id, _npc_page_id(final_key)):
            return
        import voice_seed
        entry = voice_seed.buffer_take(
            channel_id, [final_key, name, voice_seed.mob_base(final_key)])
        if not entry:
            return
        _sess_mem = session.get("ai_session_memory") if isinstance(session, dict) else None
        if isinstance(_sess_mem, dict):
            _sess_mem[voice_seed.BUFFER_KEY] = voice_seed.buffer_state(channel_id)
        text = voice_seed.seed_sheet_text(entry)
        if not text:
            return
        data["description"] = text
        _roll = entry.get("roll")
        if isinstance(_roll, dict):
            # §1.5 — NPC dict 유일 신설 필드. 읽는 코드 없음(관측·표 튜닝용).
            data["core_traits_roll"] = {**_roll, "turn": entry.get("born_turn")}
        data["_seed_stamp"] = True          # §H — `_npc_sheet_gate`가 읽고 pop
        logging.info(f"[Seed] 관문 소비: {final_key} (state={entry.get('state')})")
    except Exception as _e:
        logging.warning(f"[Seed] 관문 소비 실패(무시): {channel_id}/{final_key}: {_e}")


def _npc_read_view(channel_id: str, npcs: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    """저장 dict → 읽기 뷰(얕은 사본). `description` = 페이지 lore 절 조립, `source` = lore 절 유무 파생.
    페이지 조회는 채널당 1회(`character_lore_map`). 조회 실패면 저장된 source 도장을 그대로 두고
    description은 ""(원문을 지어내지 않는다).

    [2026-09-22 voice_seed §H] 같은 한 번의 조회에 page source 도장을 실어 온다(`with_source=True`) —
    시드 절(도장 "seed")을 가진 세션 NPC가 여기서 manual로 승격하지 않게. 판정식은
    `wiki_store.is_authored_page`와 **같다**: 절 있음 ∧ 도장≠seed."""
    if not isinstance(npcs, dict):
        return npcs
    try:
        import wiki_store
        lore = wiki_store.character_lore_map(channel_id, with_source=True)
    except Exception:
        wiki_store = None
        lore = None
    out: Dict[str, Dict[str, Any]] = {}
    for k, v in npcs.items():
        if not isinstance(v, dict):
            out[k] = v
            continue
        d = dict(v)
        d.pop("desc", None)
        if lore is None:
            d["description"] = ""
        else:
            secs, _psrc = wiki_store.lore_map_entry(lore.get(wiki_store.page_id_for("character", k)))
            d["description"] = wiki_store.assemble_lore_text(secs)
            d["source"] = (_authored_source(v.get("source"))
                           if (secs and _psrc != wiki_store.SEED_SOURCE) else "session")
        out[k] = d
    return out


def _wiki_rename_npc(channel_id: str, old_name: str, new_name: str, data: Dict[str, Any]) -> None:
    # [시트 2차b] lore 절이 정본이라 이관은 WIKI_PAGES와 무관하게 돈다.
    try:
        import wiki_store
        wiki_store.rename_npc_page(channel_id, old_name, new_name, data)
    except Exception as _e:
        logging.debug(f"[Wiki] npc page rename skipped: {_e}")


def _wiki_mark_deleted(channel_id: str, npc_name: str) -> None:
    if not getattr(config, "WIKI_PAGES", False):
        return
    try:
        import wiki_store
        wiki_store.mark_page_deleted(channel_id, "character", npc_name)
    except Exception as _e:
        logging.debug(f"[Wiki] npc page delete-mark skipped: {_e}")


# [2026-09-16 시트 2차 §9 승격 → 2차b] `source`는 **파생값**이다 — 페이지 lore 절 유무 하나로 정한다
#   (`wiki_store.is_authored_page`). 원문 있음 = 작가 시트("lore" 표기 유지, 그 외 "manual") / 없음 = "session".
#   [2026-09-22 voice_seed §H] 판정에 조건 하나가 더 붙었다 — **페이지 source 도장이 "seed" 가 아닐 것**.
#   시드 원문도 lore 절에 앉으므로, 절 유무만 보면 세션 NPC가 시드를 받는 순간 manual 이 된다.
#   description 쪽 판정은 삭제(dict엔 원문이 저장되지 않는다).
#   · channel_id·name을 주면 페이지를 직접 본다(쓰기 관문·JSON 원본을 도는 삭제 경로).
#   · dict만 주면 그 dict의 `source`를 읽는다 — `get_npcs()` 뷰가 페이지에서 찍은 도장이다.
def derive_npc_source(data: Any, channel_id: Optional[str] = None, name: Optional[str] = None,
                      lore_map: Optional[dict] = None) -> str:
    hint = data.get("source") if isinstance(data, dict) else ""
    if channel_id and name:
        import wiki_store
        if lore_map is not None:
            # [voice_seed §H] 절 유무 ∧ 도장≠seed. 도장 없는 옛 모양 lore_map 이면 절 유무만(종전 동작).
            _secs, _psrc = wiki_store.lore_map_entry(lore_map.get(_npc_page_id(name)))
            authored = bool(_secs) and _psrc != wiki_store.SEED_SOURCE
        else:
            authored = wiki_store.is_authored_page(channel_id, _npc_page_id(name))
        return _authored_source(hint) if authored else "session"
    h = str(hint or "").lower()
    return h if h in ("lore", "manual") else "session"


def is_authored_npc(data: Any, channel_id: Optional[str] = None, name: Optional[str] = None) -> bool:
    return derive_npc_source(data, channel_id, name) != "session"


def _mirror_npc(channel_id: str, npc_name: str, data: Dict[str, Any]) -> None:
    """NPC 1건을 방벽 통과 후 npcs 테이블에 미러. 원문 키는 쓰기 관문이 페이지로 보내고 dict에선 뺀다."""
    if isinstance(data, dict):
        _src_dict = data
        data = dict(data)
        _npc_sheet_gate(channel_id, npc_name, data)
        _src_dict["source"] = data.get("source")
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
                return _npc_read_view(channel_id, npcs)
            npcs_json = get_domain(channel_id).get("npcs", {})
            for _name, _data in npcs_json.items():
                _mirror_npc(channel_id, _name, _data)
            return _npc_read_view(channel_id, npcs_json)
        except Exception as _e:
            logging.warning(f"[V10] npcs read-through 실패, JSON 폴백: {_e}")
    return _npc_read_view(channel_id, get_domain(channel_id).get("npcs", {}))

# =========================================================
# [V10 P3 / 2026-09-05] fermented·deep read-through
# =========================================================

def get_fermented_history(channel_id: str) -> list:
    """[V10 P4] 행이 정본. 행 [] = 데이터 없음(정상). 행 None = 실패 → JSON에 남은 키로 퇴화 폴백."""
    if getattr(config, "V10_HISTORY_READ_FROM_SQLITE", False):
        try:
            import sqlite_store
            rows = sqlite_store.read_fermented(channel_id)
            if rows is not None:
                return rows
            logging.error("[V10] fermented row read failed — falling back to JSON leftovers (%s)", channel_id)
        except Exception as _e:
            logging.warning(f"[V10] fermented read-through 실패, JSON 폴백: {_e}")
    return get_domain(channel_id).get("fermented_history", []) or []


def get_deep_memory(channel_id: str) -> tuple:
    """[V10 P4] (narrative, data). 계약은 get_fermented_history와 동형."""
    if getattr(config, "V10_HISTORY_READ_FROM_SQLITE", False):
        try:
            import sqlite_store
            row = sqlite_store.read_deep(channel_id)
            if row is not None:
                return (row.get("narrative") or "", row.get("data") or {})
            logging.error("[V10] deep row read failed — falling back to JSON leftovers (%s)", channel_id)
        except Exception as _e:
            logging.warning(f"[V10] deep read-through 실패, JSON 폴백: {_e}")
    d = get_domain(channel_id)
    return (d.get("deep_memory", "") or "", d.get("deep_memory_data", {}) or {})


def _find_npc_key(npcs: dict, name: str, *, allow_token: bool = True) -> Optional[str]:
    """NPC 키 검색: 정규화 → 대칭 base/inner 매칭 → aliases → 충돌가드 토큰(축약형).

    [2026-06-12] aliases 정식 지원 (한↔영 교차 중복 차단).
    [2026-06-20] 대칭화 + 토큰 매칭 — 기존 stage 2는 키에서만 base/inner를 뽑아
    "순한글 키 + 영문(괄호) 질의"를 못 잡았고(auto-detect 게이트가 약한 _find_npc_key를
    쓰는 탓에 새 분열), 축약형/이름만 부른 경우("스텔라"→Stella Valentine, "서린"→윤서린)는
    아예 0매칭이었음 (deepseek이 풀네임/영문/이름을 턴마다 번갈아 호명 → NPC 폭증).
    이제: ① 질의·키 양쪽에서 base/inner 추출 후 full-form 교집합(토큰 아님 — 안전),
         ② 축약형은 토큰 후보가 '정확히 1명'일 때만 매칭(오병합 방지)."""
    norm = _normalize_npc_name(name)
    # 1) 정확한 정규화 매칭
    if norm in npcs:
        return norm
    for k in npcs:
        if _normalize_npc_name(k) == norm:
            return k
    # 2) 대칭 매칭: 질의/키 양쪽의 {전체,base,inner} full-form 교집합이 있으면 동일 인물.
    #    "복셀↔복셀(Voxel)", "스텔라 발렌타인↔Stella Valentine(스텔라 발렌타인)" 양방향.
    q_base, q_inner, q_forms = _name_forms(norm)
    for k in npcs:
        _, _, k_forms = _name_forms(k)
        if q_forms & k_forms:
            return k
    # 3) aliases 필드 매칭 (data dict 내 명시 별칭 리스트)
    for k, data in npcs.items():
        if not isinstance(data, dict):
            continue
        aliases = data.get("aliases")
        if isinstance(aliases, list):
            for a in aliases:
                if isinstance(a, str) and _normalize_npc_name(a).lower() in q_forms:
                    return k
    # 4) 축약형/이름 토큰 매칭 — 단일 토큰 질의가 정확히 1명의 토큰 후보에만 걸릴 때.
    #    "스텔라"→Stella Valentine, "레이나"→쿠로미야 레이나, "서린"→윤서린(성씨드롭).
    #    2명 이상 공유 토큰이면 None (애매 → 오병합 대신 명시 alias/병합에 위임).
    # [2026-09-24 감사] allow_token=False — 명부 **부분집합**(이번 턴 결과·사망자·추적 dict)에 대고 부를 때.
    #   "후보 정확히 1명" 규칙은 전체 명부에서만 유일성을 보장한다: 부분집합에 "Shirase Rin"만 있으면 "Rin"이 그리로 붙었다.
    if allow_token and q_base and " " not in q_base and len(q_base) >= 2 and not q_inner:
        ql = q_base.lower()
        hits = [k for k in npcs if ql in _short_tokens(k)]
        if len(hits) == 1:
            return hits[0]
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

    ⚠[2026-08-11 드라이브 부분dict 수리] **부분 dict 금지 — 여기는 엔트리 통째 교체
    관문이다.** 아래 _PRESERVE_KEYS에 든 것만 이월되고, 목록 밖 필드(description/desc/
    appear_count/_last_appear_turn/drives/soma/decision_cooldown/identity_history …)는
    넘긴 dict에 없으면 그냥 사라진다. 한 필드만 갱신하려면 반드시 full-copy 후 넘겨라:
        _new = dict(get_npc(...)); _new["필드"] = 값; update_npc(..., _new)
    (보존 목록에 키를 더 얹는 것은 근본 해법이 아니다 — 목록은 항상 뒤처진다.)

    [2026-09-22 voice_seed §G] 세션 NPC **탄생 경로 셋이 전부 여기를 지난다** —
    ① `_npc_roster_pass` 스텁 ② `register_ai_npc`(new_individual 몹 태그) ③ orchestration psyche
    relation 자동생성(렌더 전). 그래서 시드 버퍼 소비점도 여기 하나(`_npc_seed_gate`)다:
    로스터 패스에만 달면 ③이 먼저 등록해 시드가 영영 안 앉는다.

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
    # [2026-07-28 확대] 구 목록은 ("source", "aliases")뿐이라 **재등록 = 사실상 전체 교체**였다.
    #   `!npc추가`로 시트를 다시 넣으면 보이스카드로 뽑아둔 tone, 로어 분석이 채운
    #   role/personality/appearance/location, 누적된 play_observed, static_traits가 조용히 증발했다.
    #   (반대 방향엔 가드가 있었다 — add_lore_npcs는 source=="manual"을 건너뛴다. 이쪽만 무방비.)
    # 정책: **새 데이터에 그 키가 있으면 새 값이 이긴다**(덮어쓰기 의도 존중).
    #   새 데이터에 없을 때만 기존 값을 이월한다. 지우고 싶으면 `!npc 삭제` 후 재등록 — 레티어스 결정.
    _PRESERVE_KEYS = (
        "source", "aliases",
        # 다른 경로가 만들어낸 자산 (등록 시트에는 원래 없는 것들)
        "tone", "speech", "static_traits", "appearances",
        # [2026-09-03 R6] schedule. tone과 **같은 사유**로 보존한다: 시트에는 원래 없고
        #   `!npc 일정`(1회성 추출 콜)이 만들어 넣는 자산이라, 목록 밖이면 시트
        #   재업로드(`!npc 추가`) 한 번에 조용히 증발한다. 스펙 §6 R6 ②.
        "schedule",
        "role", "personality", "appearance", "location", "summary",
        "gender", "race", "constraints", "lore_seen",
        # 정체성/성장 층
        "high_concept", "trouble", "aspects", "background",
        # [2026-08-11 사망 파이프라인] 생존축 — **비가역성이 여기 걸려 있다.**
        #   status가 보존 목록 밖이면 `!npc추가` 재등록이나 시트 재작성 한 번에 dead가
        #   조용히 증발한다(= 죽음이 취소되는데 로그가 안 남는 반사실형 실패).
        #   명시 status를 담은 새 데이터는 여전히 이긴다(위 정책 그대로) — 자동
        #   재생성 경로는 그 앞단에서 이름 단위로 차단한다(orchestration).
        "status", "status_changed_turn", "status_evidence",
    )
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
            # 태도/지식/각인도 함께 이사 (구 키에 쌓인 관계 고아화 방지)
            # [2026-07-28] npc_imprints 추가 — 행동 각인은 iceberg·world_board가 실제로 읽는데
            #   이사 목록에 없어 개명 때마다 구 키에 고아로 남았다.
            for _dom in ("npc_knowledge", "npc_imprints"):
                _dd = d.get(_dom, {})
                if existing_key in _dd and final_key not in _dd:
                    _dd[final_key] = _dd.pop(existing_key)
                elif existing_key in _dd:
                    _dd.pop(existing_key)  # 양쪽 존재 시 final 쪽 유지

    # [2026-09-22 voice_seed §G] 버퍼 시드 소비 — 이관·쓰기 관문 **앞**. 원문 키가 여기서 생기면
    #   아래 관문이 그대로 페이지 lore 절로 옮긴다(새 쓰기 경로 0). 자세한 계약은 _npc_seed_gate.
    _npc_seed_gate(channel_id, final_key, name, data, session=d)
    # [시트 2차b] 개명이면 페이지(play·lore 절) 이관을 먼저 — 들어온 원문이 새 페이지에 앉도록.
    if existing_key and existing_key != final_key:
        _wiki_rename_npc(channel_id, existing_key, final_key, data)
    _npc_sheet_gate(channel_id, final_key, data)   # 원문 → 페이지 lore 절, dict에서 제거 + source 파생 도장
    npcs[final_key] = data
    save_domain(channel_id, d)
    # [2026-09-24 감사] 키 승격(괄호식 `레나`→`레나(Rena)`)이면 world_state 부수 저장소(감정·soma — 여긴 인라인
    #   이관 목록 밖)와 world_tree 출석도 옮긴다. 전엔 옛 키에 고아로 남아 감정 연속성이 끊기고, 노드에 옛
    #   이름이 유령 출석으로 남아 같은 인물이 0단에 두 번 떴다. (도메인 쪽은 위에서 이미 옮겨 no-op)
    if existing_key and existing_key != final_key:
        try:
            migrate_npc_side_data(channel_id, existing_key, final_key)
        except Exception as _e_msd:
            logging.debug(f"[NPC] 키 승격 부수 이관 skip: {_e_msd}")
        try:
            import world_tree as _wt_up
            _loc_up = _wt_up.get_npc_location(channel_id, existing_key)
            if _loc_up:
                _wt_up.remove_npc_presence(channel_id, existing_key)
                if not _wt_up.get_npc_location(channel_id, final_key):
                    _wt_up.set_npc_location(channel_id, final_key, _loc_up)
        except Exception as _e_wtu:
            logging.debug(f"[NPC] 키 승격 출석 이관 skip: {_e_wtu}")
        d = get_domain(channel_id)   # 위 두 이관이 도메인을 다시 저장했다 — 이하 코드가 옛 사본을 쓰지 않게
        npcs = d.get("npcs", {})

    # [V10 Sprint 2-B] 미러 — 키 마이그레이션 발생 시 구 행 삭제 + 신 행, 한 트랜잭션
    try:
        import sqlite_store
        import state_guards
        clean = state_guards.validate_npc_write(final_key, data)
        if clean is not None:
            if existing_key and existing_key != final_key:
                sqlite_store.rename_npc(channel_id, existing_key, final_key, clean)
                sqlite_store.rename_edge_entity(channel_id, existing_key, final_key)  # [관계 통합] 엣지 이름 이관
                sqlite_store.delete_knowledge(channel_id, existing_key)
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
        save_domain(channel_id, d)
        # [2026-07-28] 고아 정리 확대 — 구 코드는 태도/지식만 지워서 각인·관계엣지·감정이
        # 이름만 남은 고아로 누적됐다(장기 세션일수록). 이관(migrate)의 짝으로 한 곳에 모음.
        _purged = purge_npc_side_data(channel_id, target)
        if _purged:
            logging.debug(f"[NPC] 삭제 부수정리 {target}: {', '.join(_purged)}")
        try:
            import sqlite_store
            sqlite_store.delete_npc_row(channel_id, target)
            sqlite_store.delete_knowledge(channel_id, target)
        except Exception as _e:
            logging.debug(f"[V10] npc delete mirror skipped: {_e}")
        # [2026-09-14 W1] 페이지는 **지우지 않는다** — status='deleted' 표시만(삭제 0 원칙).
        #   플레이가 쌓은 play 절은 인물이 퇴장해도 사건의 증거로 남는다.
        _wiki_mark_deleted(channel_id, target)
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

    # 3) 관계 엣지 이관 — [2026-09-15 관계 통합] 엣지 PK가 이름이라 이름만 옮긴다.
    #   양쪽에 같은 방향 엣지가 있으면 본체(canon) 쪽 유지(rename_edge_entity 충돌 규칙).
    try:
        import sqlite_store as _ss_m
        _ss_m.rename_edge_entity(channel_id, dup_key, canon_key)
    except Exception as _e_em:
        logging.debug(f"[NPC 병합] 엣지 이관 skip: {_e_em}")

    # 4) 지식 병합 (합집합)
    knowledge = d.get("npc_knowledge", {})
    dup_kn = knowledge.pop(dup_key, None)
    if dup_kn:
        canon_kn = knowledge.get(canon_key)
        if canon_kn:
            # M-5 fix: set() 합집합은 프로세스 간 순서 비결정 → [-20:] 절단 시 어느 20개가 살지 예측 불가.
            # dict.fromkeys로 순서 보존 dedup(결정론) 후 최근 20개.
            canon_kn["knows"] = list(dict.fromkeys(
                (canon_kn.get("knows", []) or []) + (dup_kn.get("knows", []) or [])))[-20:]
            canon_kn["secrets_held"] = list(dict.fromkeys(
                (canon_kn.get("secrets_held", []) or []) + (dup_kn.get("secrets_held", []) or [])))
            canon_kn["would_share"] = bool(canon_kn.get("would_share")) or bool(dup_kn.get("would_share"))
        else:
            knowledge[canon_key] = dup_kn

    save_domain(channel_id, d)

    # [2026-07-28] 부수 저장소 이관 — 구 병합은 태도·지식만 다뤄서 각인·관계엣지·감정이
    # 중복 이름 아래 고아로 남았다(개명 경로와 비대칭이었음). 같은 헬퍼로 대칭을 맞춘다.
    _migrated = migrate_npc_side_data(channel_id, dup_key, canon_key)
    if _migrated:
        logging.info(f"[NPC 병합] 부수 이관 {dup_key}→{canon_key}: {', '.join(_migrated)}")
    d = get_domain(channel_id)   # 헬퍼가 저장했으므로 최신 상태 재로드

    # 5) SQLite 미러: 중복 행 삭제 + 본체 재미러
    try:
        import sqlite_store
        sqlite_store.delete_npc_row(channel_id, dup_key)
        sqlite_store.delete_knowledge(channel_id, dup_key)
    except Exception as _e:
        logging.debug(f"[V10] merge dup row cleanup skipped: {_e}")
    # [시트 2차b] 원문 = 페이지 lore 절. 본체 우선·빈 자리만 채움(필드 병합과 같은 정책).
    #   중복 페이지는 지우지 않는다(삭제 0) — 본체에 원문이 없을 때만 중복의 lore 절을 복사한다.
    _canon_w = dict(canon)                 # 원문 키는 JSON 본체에 얹지 않는다(관문 입력 사본만)
    try:
        import wiki_store as _ws_m
        _dup_lore = _ws_m.get_lore_sections(channel_id, _npc_page_id(dup_key))
        if _dup_lore and not _ws_m.has_lore_sections(channel_id, _npc_page_id(canon_key)):
            _canon_w["description"] = _ws_m.assemble_lore_text(_dup_lore)
    except Exception as _e_ml:
        logging.debug(f"[NPC 병합] 원문 이관 skip: {_e_ml}")
    _mirror_npc(channel_id, canon_key, _canon_w)
    if canon_key in d.get("npc_knowledge", {}):
        _mirror_knowledge(channel_id, canon_key, d["npc_knowledge"][canon_key])

    alias_note = f" (별칭: {', '.join(merged_aliases)})" if merged_aliases else ""
    return True, f"'{dup_key}' → '{canon_key}' 병합 완료{alias_note}"

def bulk_update_npcs(channel_id: str, npcs: Dict[str, Dict[str, Any]]) -> None:
    """[V10 §3b] NPC dict 전체 교체 (tick_all_cooldowns 등 bulk 쓰기 정식화).
    JSON + SQLite 동시, SQLite는 단일 트랜잭션."""
    # [시트 2차b] 뷰(get_npcs)를 그대로 되돌려 쓰는 bulk 경로 — 조립된 원문 키는 저장하지 않는다
    #   (원문 제출 관문이 아니다: 쿨다운 틱 등). 페이지 lore 절 무접촉.
    npcs = {k: ({_k: _v for _k, _v in v.items() if _k not in _NPC_TEXT_KEYS} if isinstance(v, dict) else v)
            for k, v in (npcs or {}).items()}
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
    # [2026-07-28] 기본값 리터럴 "session" — npc_manager.SOURCE_SESSION으로 승격된 값.
    # (순환 import 회피를 위해 여기서는 리터럴 유지, 의미는 동일: source 미상 = 세션 파생)
    try:
        import wiki_store as _ws_d
        _lm = _ws_d.character_lore_map(channel_id, with_source=True)   # [§H] 시드 도장 동반 — 시드 NPC는 세션 소속으로 지워진다
    except Exception:
        _lm = None
    to_delete = [name for name, data in npcs.items()
                 if (derive_npc_source(data, channel_id, name, lore_map=_lm) if _lm is not None
                     else derive_npc_source(data)) not in keep_sources]
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

# =========================================================
# [2026-09-15 관계 통합 1차] 관계 = relations 엣지 하나 (sqlite_store 소유).
#   설계: 파티쳇수정/state_v10/relation_unify_design_v0.1_2026-09-15.md §2~§5.
#   옛 저장소 셋(npc_attitudes JSON+npc_relations 미러 / ai_memory.relationships /
#   entity_relations.edges)은 **마이그레이션 없이 삭제** — 읽지도 쓰지도 않는다.
#   쓰기 경로 3: Theoria relation 층(write_theoria_relations) / 배치 npc_relations
#   (entity_relations.process_batch_relations) / OOC(apply_ooc_relation_edits). 감쇠 1: decay_relation_edges.
#   게터(get_npc_attitudes/get_npc_attitude)는 이름·반환 모양 유지, 값은 엣지에서 **파생**.
# =========================================================

# attitude는 저장하지 않는다 — bond 구간에서 코드가 파생(설계 §2 파생값, 지시서 §3).
_ATTITUDE_BANDS = ((-60, "hostile"), (-20, "unfriendly"))   # bond ≤ 경계
_ATTITUDE_BANDS_UP = ((20, "neutral"), (60, "friendly"))    # bond < 경계
# [2026-09-25 관계 정성] tension 구간 — 모델에게 가는 말(friction low/strained/high/breaking). 경계는 하류 문턱과 맞춤
#   (slot 연결 깊이 >20 · iceberg marcato >50 · emotion Tier 5.5 friction ≥60 사이).
_TENSION_BANDS = ((20, "low"), (50, "strained"), (80, "high"))  # tension < 경계


def tension_band(tension: Any) -> str:
    """tension 0~100 → low / strained / high / breaking."""
    try:
        t = int(tension)
    except (TypeError, ValueError):
        return "low"
    for edge_v, label in _TENSION_BANDS:
        if t < edge_v:
            return label
    return "breaking"


def relation_words(att: Any, with_attitude: bool = True) -> str:
    """[2026-09-25 관계 정성] 관계 수치 → 모델에게 가는 말 한 벌(숫자 0).
    "friendly, warming · friction strained". Theoria 4b · 월드보드 편지 · 속마음 toward_pc 가 같이 쓴다."""
    if not isinstance(att, dict):
        return ""
    parts = []
    if with_attitude:
        _a = str(att.get("attitude") or "").strip()
        if not _a or _a.lower() == "null":
            _b = att.get("bond", att.get("depth"))
            _a = attitude_from_bond(_b) if _b is not None else ""
        if _a:
            parts.append(_a)
    _tr = att.get("trajectory")
    if _tr in ("improving", "declining"):
        parts.append("warming" if _tr == "improving" else "cooling")
    out = ", ".join(parts)
    if isinstance(att.get("tension"), (int, float)) and not isinstance(att.get("tension"), bool):
        out = (out + " · " if out else "") + f"friction {tension_band(att['tension'])}"
    return out


def attitude_from_bond(bond: Any) -> str:
    """bond ≤−60 hostile / ≤−20 unfriendly / <20 neutral / <60 friendly / ≥60 devoted."""
    try:
        b = int(bond)
    except (TypeError, ValueError):
        return "neutral"
    for edge_v, label in _ATTITUDE_BANDS:
        if b <= edge_v:
            return label
    for edge_v, label in _ATTITUDE_BANDS_UP:
        if b < edge_v:
            return label
    return "devoted"


def _relation_turn(channel_id: str) -> int:
    """엣지 시계에 쓸 현재 턴 — world_state.turn_index."""
    try:
        return int((get_domain(channel_id).get("world_state") or {}).get("turn_index", 0) or 0)
    except Exception:
        return 0


def get_pc_masks(channel_id: str) -> set:
    """참가자 마스크 집합 — 엣지 방향 가드의 기준(PC 노드)."""
    out = set()
    try:
        for _p in (get_domain(channel_id).get("participants") or {}).values():
            if isinstance(_p, dict) and _p.get("mask"):
                out.add(str(_p["mask"]))
    except Exception:
        pass
    return out


def get_relation_edges(channel_id: str, source: Optional[str] = None,
                       target: Optional[str] = None) -> List[Dict[str, Any]]:
    """엣지 원본 조회(얇은 위임)."""
    try:
        import sqlite_store
        return sqlite_store.get_edges(channel_id, source=source, target=target)
    except Exception as _e:
        logging.debug(f"[Relation] get_edges skip: {_e}")
        return []


def upsert_relation_edge(channel_id: str, source: str, target: str, *,
                         bond: Optional[int] = None, tension: Optional[int] = None,
                         stance: Optional[str] = None, kind: Optional[str] = None,
                         turn: Optional[int] = None, origin: str = "theoria") -> Optional[Dict[str, Any]]:
    """엣지 쓰기 앞단 — 방향 가드 + 이름 해상도 + 시트 seed, 그다음 sqlite_store.upsert_edge(클램프).

    방향 가드(구 orch:357 PC 혼입 가드의 이사):
      - source가 PC 마스크면 거부(PC→X 방향은 두지 않는다, 설계 §7-6).
      - kind 없음(NPC→PC): target이 PC 마스크가 **아니면** 거부.
      - kind 있음(NPC↔NPC): target이 PC 마스크면 거부.
    seed: NPC→PC 엣지가 처음 생길 때 NPC 시트의 initial_depth/initial_tension을 source="seed"로 먼저 심는다.
    """
    if not channel_id or not isinstance(source, str) or not isinstance(target, str):
        return None
    source, target = source.strip(), target.strip()
    if not source or not target or source == target:
        return None
    masks = get_pc_masks(channel_id)
    if source in masks:
        logging.info("[Relation] 거부: source가 PC(%s) — PC→X 엣지 없음", source)
        return None
    if kind is None and target not in masks:
        logging.info("[Relation] 거부: NPC→PC 쓰기인데 target(%s)이 PC 마스크 아님", target)
        return None
    if kind is not None and target in masks:
        logging.info("[Relation] 거부: NPC↔NPC 쓰기인데 target(%s)이 PC", target)
        return None
    try:
        import sqlite_store
        d = get_domain(channel_id)
        source = _resolve_npc_name(d, source)
        if kind is not None:
            target = _resolve_npc_name(d, target)
        t = _relation_turn(channel_id) if turn is None else int(turn)
        if kind is None and origin not in ("seed", "npc_sheet_initial") \
                and sqlite_store.get_edge(channel_id, source, target) is None:
            # [2026-09-24 감사 §5-2 #12 — 레티어스 판정] 시드 = 시트 중 **이 PC 를 가리키는 문장**의 관계 키워드.
            #   저장된 initial_depth/initial_tension 은 읽지 않는다 — 유일한 생산자가 시트 전문 키워드 스캔이라
            #   제3자 서술("가족을 잃었다")이 PC 에게 +55 로 박힌 값이 섞여 있다(옛 세션 dict 에 남은 키는 무해).
            _ib, _it = _npc_pc_seed(channel_id, source, target)  # [2026-09-25] 헬퍼로 이사(미리보기와 공유)
            if _ib or _it:
                sqlite_store.upsert_edge(channel_id, source, target, bond=_ib, tension=_it,
                                         turn=t, origin="seed")
        _prev = sqlite_store.get_edge(channel_id, source, target) if kind is None else None
        r = sqlite_store.upsert_edge(channel_id, source, target, bond=bond, tension=tension,
                                     stance=stance, kind=kind, turn=t, origin=origin)
        # attitude_log(narrative_queries 실전이 급식 원천)은 파생 attitude 구간이 바뀔 때만 적립.
        #   구 M5 게이트가 유일 적립자였다 — 게이트 삭제로 로그가 굶지 않게 같은 뜻을 여기서 잇는다.
        if r and kind is None:
            _fa = attitude_from_bond(_prev["bond"]) if _prev else ""
            _ta = attitude_from_bond(r["bond"])
            if _fa != _ta:
                try:
                    sqlite_store.append_attitude_log(channel_id, t, source, _fa, _ta,
                                                     "initial" if not _prev else origin,
                                                     r.get("stance", "") or "")
                except Exception:
                    pass
        return r
    except Exception as _e:
        logging.warning(f"[Relation] upsert 실패(무시): {source}->{target}: {_e}")
        return None


def _npc_pc_seed(channel_id: str, source: str, target: str) -> Tuple[int, int]:
    """NPC→PC 첫 엣지 시드 (bond, tension). 시트 중 그 PC 를 가리키는 문장의 관계 키워드(§5-2 #12).
    upsert_relation_edge(실제 심기)와 preview_theoria_relation(미리보기)이 같이 쓴다."""
    _ib, _it = 0, 0
    try:
        import npc_manager as _nm_seed
        _desc_seed = ((get_npcs(channel_id) or {}).get(source) or {}).get("description") or ""
        _sd = _nm_seed.relation_seed_for(_desc_seed, [target])
        if _sd:
            _ib, _it = int(_sd[0]), int(_sd[1])
    except Exception as _e_seed:
        logging.debug(f"[Relation] 시드 계산 건너뜀: {_e_seed}")
    return _ib, _it


def _theoria_edge_view(channel_id: str, npc_name: str, pc_mask: str, turn: int):
    """미리보기 공통부 — (저장 엣지 또는 시드 가상 엣지, turn). NPC→PC 방향이 아니면 None. 쓰기 0.
    이름 해상도·시드는 저장 경로(upsert_relation_edge)와 같은 함수(_resolve_npc_name / _npc_pc_seed)."""
    if not channel_id or not isinstance(npc_name, str) or not npc_name.strip() or not pc_mask:
        return None
    masks = get_pc_masks(channel_id)
    if npc_name.strip() in masks or pc_mask not in masks:
        return None
    import sqlite_store
    source = _resolve_npc_name(get_domain(channel_id), npc_name.strip())
    if not source or source == pc_mask:
        return None
    t = int(turn or 0)
    existing = sqlite_store.get_edge(channel_id, source, pc_mask)
    if existing is None:
        _ib, _it = _npc_pc_seed(channel_id, source, pc_mask)
        if _ib or _it:
            _ib, _it = max(-100, min(100, _ib)), max(0, min(100, _it))
            existing = {"bond": _ib, "tension": _it,
                        "history": [{"turn": t, "bond": _ib, "tension": _it, "source": "seed"}]}
    return existing, t


def preview_theoria_relation(channel_id: str, npc_name: str, pc_mask: str,
                             bond: Optional[int], tension: Optional[int],
                             turn: int) -> Optional[Tuple[int, int]]:
    """Theoria relation 값 → 이번 턴 upsert_relation_edge(origin="theoria")가 **저장할** (bond, tension). 쓰기 0.

    [2026-09-25 관계 한 숫자] 이름 해상도·시드·턴당 캡·하드 범위를 저장 경로와 같은 함수로 계산한다
    (sqlite_store.edge_step_values / _edge_base / _npc_pc_seed). 방향이 NPC→PC 가 아니면 None."""
    v = _theoria_edge_view(channel_id, npc_name, pc_mask, turn)
    if v is None:
        return None
    import sqlite_store
    return sqlite_store.edge_step_values(v[0], v[1], bond, tension, "theoria")


def resolve_theoria_shift(channel_id: str, npc_name: str, pc_mask: str,
                          bond_shift: Optional[str], tension_shift: Optional[str],
                          turn: int) -> Optional[Tuple[int, int]]:
    """[2026-09-25 관계 정성] 이동 말 → 이번 턴 저장될 (bond, tension). 기준점 = 이번 턴 **이전** 값(_edge_base —
    같은 턴 재쓰기면 그 전 값), 폭 = config.REL_BOND_SHIFT / REL_TENSION_SHIFT. 말이 둘 다 없거나 방향이 아니면 None."""
    _bmap = getattr(config, "REL_BOND_SHIFT", {}) or {}
    _tmap = getattr(config, "REL_TENSION_SHIFT", {}) or {}
    db = _bmap.get(bond_shift) if bond_shift else None
    dt = _tmap.get(tension_shift) if tension_shift else None
    if db is None and dt is None:
        return None
    v = _theoria_edge_view(channel_id, npc_name, pc_mask, turn)
    if v is None:
        return None
    import sqlite_store
    base_b, base_t = sqlite_store._edge_base(v[0], v[1], "theoria")
    tb = base_b + int(db) if db is not None else None
    tt = base_t + int(dt) if dt is not None else None
    return sqlite_store.edge_step_values(v[0], v[1], tb, tt, "theoria")


def _rel_int(v) -> Optional[int]:
    if v is None or isinstance(v, bool):
        return None
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return None


def align_theoria_relations(channel_id: str, psyche_states: Dict[str, Any],
                            pc_mask: Optional[str], turn: int) -> int:
    """[2026-09-25 관계 한 숫자] Theoria 직후·감정 단계 전 — psyche_states[NPC].relation 의 bond/tension 을
    **이번 턴 저장될 값**으로 제자리 교체한다. 감정 엔진·iceberg·저장이 같은 숫자 하나를 본다.

    - 모델이 낸 칸만 만진다(없는 칸은 채우지 않음 — 채우면 쓰기 경로가 매턴 last_turn 을 갱신해 감쇠가 멈춘다).
    - 옛 `value` 키로 왔으면 `bond` 에도 같은 값을 둔다(읽는 쪽이 bond 우선).
    - 행동 PC 마스크가 없거나 PC 이름 항목이면 무접촉(쓰기 경로도 안 쓴다).
    Returns: 정렬한 칸 수(이동 말 환산 + 캡에 걸린 숫자)."""
    if not channel_id or not pc_mask or not isinstance(psyche_states, dict):
        return 0
    changed = []
    for name, st in psyche_states.items():
        if not isinstance(name, str) or not isinstance(st, dict):
            continue
        rel = st.get("relation")
        if not isinstance(rel, dict):
            continue
        # [2026-09-25 관계 정성] 모델은 이동 말(bond_shift/tension_shift)을 낸다 → 여기서 숫자로(저장될 값과 같은 식).
        #   숫자(bond/tension)가 같이 와도 말이 이긴다. 말이 없을 때만 아래 옛 숫자 경로(폴백).
        _bs = str(rel.get("bond_shift") or "").strip().lower()
        _ts = str(rel.get("tension_shift") or "").strip().lower()
        _bs = _bs if _bs in (getattr(config, "REL_BOND_SHIFT", {}) or {}) else ""
        _ts = _ts if _ts in (getattr(config, "REL_TENSION_SHIFT", {}) or {}) else ""
        if _bs or _ts:
            try:
                res = resolve_theoria_shift(channel_id, name, pc_mask, _bs or None, _ts or None, turn)
            except Exception as _e_sh:
                logging.debug(f"[Relation] shift skip {name}: {_e_sh}")
                res = None
            if res is not None:
                nb, nt = res
                if _bs:
                    rel["bond"] = nb
                    if "value" in rel:
                        rel["value"] = nb
                    changed.append(f"{name} {_bs}→bond {nb}")
                if _ts:
                    rel["tension"] = nt
                    changed.append(f"{name} tension {_ts}→{nt}")
            continue
        _b = _rel_int(rel.get("bond", rel.get("value")))
        _t = _rel_int(rel.get("tension"))
        if _b is None and _t is None:
            continue
        try:
            res = preview_theoria_relation(channel_id, name, pc_mask, _b, _t, turn)
        except Exception as _e_al:
            logging.debug(f"[Relation] align skip {name}: {_e_al}")
            continue
        if res is None:
            continue
        nb, nt = res
        if _b is not None:
            if nb != _b:
                changed.append(f"{name} bond {_b}→{nb}")
            rel["bond"] = nb
            if "value" in rel:
                rel["value"] = nb
        if _t is not None:
            if nt != _t:
                changed.append(f"{name} tension {_t}→{nt}")
            rel["tension"] = nt
    if changed:
        logging.info("[Relation] align(pre-emotion): " + "; ".join(changed))
    return len(changed)


def write_theoria_relations(channel_id: str, psyche_states: Dict[str, Any],
                            pc_mask: Optional[str], turn: Optional[int] = None,
                            skip: Optional[set] = None) -> int:
    """Theoria relation 층 → NPC→행동PC 엣지. 새 LLM 콜 0(이미 받은 DAI만 읽음).

    - pc_mask 없으면 쓰지 않는다(§7-12: 마스크 없는 uid는 참가자가 아니다).
    - bond는 `bond`, 없으면 옛 `value` 폴백. tension·descriptor(stance)는 있으면 쓴다.
    - 턴당 캡은 upsert_edge(origin="theoria")가 자른다.
    Returns: 쓴 엣지 수."""
    if not pc_mask or not isinstance(psyche_states, dict):
        return 0
    n = 0
    for npc_name, st in psyche_states.items():
        if not isinstance(npc_name, str) or (skip and npc_name in skip) or not isinstance(st, dict):
            continue
        rel = st.get("relation")
        if not isinstance(rel, dict):
            continue
        _b = rel.get("bond", rel.get("value"))
        _t = rel.get("tension")
        try:
            _b = None if _b is None or isinstance(_b, bool) else int(float(_b))
        except (TypeError, ValueError):
            _b = None
        try:
            _t = None if _t is None or isinstance(_t, bool) else int(float(_t))
        except (TypeError, ValueError):
            _t = None
        _stance = rel.get("descriptor")
        _stance = _stance.strip() if isinstance(_stance, str) and _stance.strip() else None
        if _b is None and _t is None and _stance is None:
            continue
        if upsert_relation_edge(channel_id, npc_name, pc_mask, bond=_b, tension=_t,
                                stance=_stance, turn=turn, origin="theoria"):
            n += 1
    return n


def apply_ooc_relation_edits(channel_id: str, uid: str, edits: List[Dict[str, Any]]) -> List[str]:
    """OOC 관계 편집 → 엣지 직접 set(캡 면제, source="ooc"). 행동 PC 마스크가 target.
    edit = {"field": "relation", "action": "set|remove", "key": NPC, "stance"?, "bond"?, "tension"?}
    Returns: 되비침 줄."""
    lines: List[str] = []
    p = get_participant_data(channel_id, uid) or {}
    mask = p.get("mask")
    if not mask:
        return lines
    for e in edits or []:
        if not isinstance(e, dict):
            continue
        npc = str(e.get("key") or "").strip()
        if not npc:
            continue
        action = str(e.get("action") or "set").lower()
        if action == "remove":
            try:
                import sqlite_store
                _k = _resolve_npc_name(get_domain(channel_id), npc)
                if sqlite_store.get_edge(channel_id, _k, mask) is not None:
                    conn = sqlite_store._get_conn(channel_id)
                    conn.execute("DELETE FROM relations WHERE channel_id=? AND source=? AND target=?",
                                 (channel_id, _k, mask))
                    conn.commit()
                    lines.append(f"관계 삭제: {_k}")
            except Exception as _e:
                logging.debug(f"[OOC] 관계 삭제 skip: {_e}")
            continue

        def _int(v):
            try:
                return None if v is None or isinstance(v, bool) else int(float(v))
            except (TypeError, ValueError):
                return None
        stance = e.get("stance", e.get("value"))
        stance = str(stance).strip() if isinstance(stance, (str, int, float)) and str(stance).strip() else None
        r = upsert_relation_edge(channel_id, npc, mask, bond=_int(e.get("bond")),
                                 tension=_int(e.get("tension")), stance=stance, origin="ooc")
        if r:
            lines.append(f"관계: {r['source']} → bond {r['bond']} / tension {r['tension']}"
                         + (f" — {r['stance']}" if r.get("stance") else ""))
    return lines


def decay_relation_edges(channel_id: str, current_turn: int) -> int:
    """감쇠 한 곳 — sqlite_store.decay_edges 위임(last_turn 시계, bond→0, tension 하한 0)."""
    try:
        import sqlite_store
        return sqlite_store.decay_edges(channel_id, int(current_turn))
    except Exception as _e:
        logging.debug(f"[Relation] decay skip: {_e}")
        return 0


def trajectory_from_delta(delta: Any) -> str:
    """bond 한 걸음의 **부호**만 → improving/declining/stable (설계 §2 1차 파생 = 구간·부호까지).
    불감대 = 턴당 캡(EDGE_BOND_STEP_CAP): 캡만큼 밀려야 방향으로 친다 — T=0.1 판독 흔들림(±1~2)을 방향으로 안 읽는다."""
    try:
        import sqlite_store
        dead = int(getattr(sqlite_store, "EDGE_BOND_STEP_CAP", 5))
        # [2026-09-25 관계 정성] 불감대 = 가장 작은 이동 말(warmer 3). 말은 흔들림이 없어서 warmer 한 걸음도 방향이다.
        #   (옛 불감대=캡은 T=0.1 숫자 판독 흔들림 ±1~2 를 거르려던 것 — 숫자 폴백 경로에도 3이면 충분하다.)
        _steps = [abs(int(v)) for v in (getattr(config, "REL_BOND_SHIFT", {}) or {}).values() if v]
        if _steps:
            dead = min(dead, min(_steps))
        d = int(delta)
    except (TypeError, ValueError):
        return "stable"
    return "improving" if d >= dead else "declining" if d <= -dead else "stable"


def _edge_last_change(edge: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """history 마지막 두 항목 → 옛 `last_change` 도장 모양(status_panel 방향 기호용). 파생, 저장 안 함."""
    hist = [h for h in (edge.get("history") or []) if isinstance(h, dict)]
    if not hist:
        return None
    cur = hist[-1]
    prev = hist[-2] if len(hist) >= 2 else {"bond": 0, "tension": 0}
    moved = [(f, prev.get(k, 0), cur.get(k, 0)) for f, k in (("depth", "bond"), ("tension", "tension"))
             if prev.get(k, 0) != cur.get(k, 0)]
    if not moved:
        return None
    if len(moved) == 1:
        field, old, new = moved[0]
    else:
        field = "+".join(m[0] for m in moved)
        old, new = [m[1] for m in moved], [m[2] for m in moved]
    return {"turn": cur.get("turn"), "field": field, "from": old, "to": new,
            "source": cur.get("source", ""), "note": ""}


def _edge_to_attitude(edge: Dict[str, Any]) -> Dict[str, Any]:
    """NPC→PC 엣지 → 옛 attitude dict 모양(소비자 무변경용 파생 뷰)."""
    bond = int(edge.get("bond", 0) or 0)
    out = {
        "attitude": attitude_from_bond(bond),
        "reason": edge.get("stance", "") or "",
        "depth": bond,                         # 옛 depth 자리 = bond(−100~+100)
        "tension": int(edge.get("tension", 0) or 0),
        "bond": bond,
        "stance": edge.get("stance", "") or "",
        "target": edge.get("target"),
    }
    _h = [h for h in (edge.get("history") or []) if isinstance(h, dict)]
    out["trajectory"] = (trajectory_from_delta(int(_h[-1].get("bond", 0) or 0) - int(_h[-2].get("bond", 0) or 0))
                         if len(_h) >= 2 else "stable")
    # [2026-09-24 감사 §5-2 #11] 이 관계의 최저 bond(history 창 안) — desistance "회복폭" 재료.
    _lows = [bond]
    for _hh in _h:
        try:
            _lows.append(int(_hh.get("bond")))
        except (TypeError, ValueError):
            pass
    out["bond_low"] = min(_lows)
    if edge.get("last_turn") is not None:
        out["last_change_turn"] = edge["last_turn"]
    lc = _edge_last_change(edge)
    if lc:
        out["last_change"] = lc
    return out


def get_npc_attitudes(channel_id: str, pc: Optional[str] = None) -> Dict[str, Dict]:
    """NPC→PC 관계 조회 (전체) — 이름·반환 모양 유지, 내부는 엣지(kind NULL) 파생.

    pc 지정: 그 PC를 target으로 하는 엣지만.
    pc 생략: NPC마다 **가장 최근 관측(last_turn)** 엣지 하나(동률은 |bond| 큰 쪽) — 솔로면 유일 엣지와 같다.
    """
    out: Dict[str, Dict] = {}
    best: Dict[str, Dict[str, Any]] = {}
    for e in get_relation_edges(channel_id, target=pc):
        if e.get("kind") is not None:
            continue
        src = e.get("source")
        prev = best.get(src)
        key = (int(e.get("last_turn") or -1), abs(int(e.get("bond") or 0)))
        if prev is None or key > (int(prev.get("last_turn") or -1), abs(int(prev.get("bond") or 0))):
            best[src] = e
    for src, e in best.items():
        out[src] = _edge_to_attitude(e)
    return out


def get_npc_attitude(channel_id: str, npc_name: str, pc: Optional[str] = None) -> Optional[Dict]:
    """특정 NPC의 NPC→PC 관계 조회 (단건) — get_npc_attitudes 파생 규칙 동일."""
    if not npc_name:
        return None
    d = get_domain(channel_id)
    key = _resolve_npc_name(d, npc_name)
    return get_npc_attitudes(channel_id, pc=pc).get(key)


def delete_npc_attitude(channel_id: str, npc_name: str) -> bool:
    """NPC→PC 엣지 삭제 (identity reveal 등). NPC↔NPC 엣지는 남긴다."""
    try:
        import sqlite_store
        d = get_domain(channel_id)
        key = _resolve_npc_name(d, npc_name)
        conn = sqlite_store._get_conn(channel_id) if sqlite_store._ensure_schema(channel_id) else None
        if conn is None:
            return False
        cur = conn.execute("DELETE FROM relations WHERE channel_id=? AND source=? AND kind IS NULL",
                           (channel_id, key))
        conn.commit()
        return (cur.rowcount or 0) > 0
    except Exception as _e:
        logging.debug(f"[Relation] delete skip: {_e}")
        return False

# NPC Knowledge Persistence
# [V10 Sprint 2-A] JSON 진실원천 + npc_knowledge 테이블 dual-write.
# 읽기는 config.V10_KNOWLEDGE_READ_FROM_SQLITE 게이트 (현재 값 = True, 읽기 ON).

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

def update_npc_knowledge(channel_id: str, npc_name: str, knowledge_data: Dict[str, Any],
                        src: Optional[Dict[str, Any]] = None) -> None:
    """NPC의 지식 상태 업데이트 (Theoria 분석 결과 저장)

    주의: knows는 set union 머지라 순서 비결정 (기존 동작 — parity 비교는 set 기준).
    DAI의 false_beliefs는 여기 저장 안 됨 (턴 내 소비 전용, spec §A-1).

    [2026-09-14 S5a] src={"turn": int|None, "message_id": int|None} — 주면 이번 호출로
    **실제 새로 들어간** knows 항목의 첫 출처를 fact_sources 원장에 적립.
    src 없음(예: rename 경로) = 출처 기록 생략. knows 형태는 무변경."""
    d = get_domain(channel_id)
    if "npc_knowledge" not in d:
        d["npc_knowledge"] = {}
    npc_name = _resolve_npc_name(d, npc_name)

    existing = d["npc_knowledge"].get(npc_name, {})
    # Merge: 기존 knows에 새 항목 추가 (중복 제거)
    old_knows = set(existing.get("knows", []))
    new_knows = knowledge_data.get("knows", []) or []

    # [2026-07-19 PersistAudit 처방] 근사중복 흡수 — set union은 exact-match만 걸러
    # "PC is VIP guest"/"PC is a VIP guest" 류 표면형 변형이 무한 누적됐다.
    # 토큰 자카드 ≥0.75면 더 긴(정보 많은) 항목만 유지. 결정론·콜0.
    # [2026-09-24 감사] 순서 보존 — 전엔 set 합집합을 **길이 내림차순**으로 정렬한 뒤 `[-20:]`로 잘라,
    #   20개가 차면 정보가 가장 많은 긴 사실부터 버렸다(새로 들어온 긴 사실은 저장 즉시 탈락).
    #   이제 입력 순서(기존 → 새) 그대로 두고, 근사중복은 더 긴 쪽을 **최신 자리**에 남긴다 → [-20:]=최신 20.
    def _absorb_near_dupes(items):
        out = []   # [(text, toks)] 오래된→최신
        seen = set()
        for it in (str(x) for x in items if x):
            if it in seen:
                continue
            seen.add(it)
            toks = {t for t in it.lower().split() if len(t) > 1}
            dup = None
            if toks:
                for i, (_t, kt) in enumerate(out):
                    if kt and len(toks & kt) / max(len(toks | kt), 1) >= 0.75:
                        dup = i
                        break
            if dup is None:
                out.append((it, toks))
                continue
            prev = out.pop(dup)
            out.append((it, toks) if len(it) >= len(prev[0]) else prev)
        return [t for t, _ in out]

    merged_knows = _absorb_near_dupes(list(existing.get("knows", []) or []) + list(new_knows))

    # [V10 지식 lite] suspects(의심) 누적 + misbeliefs(=DAI false_beliefs 영속화).
    # knows로 확정(승격)된 항목은 suspects에서 제거(의심→확신 전이).
    _susp_all = list(existing.get("suspects", []) or []) + list(knowledge_data.get("suspects", []) or [])
    _susp_left = set(_susp_all) - set(merged_knows)
    _merged_susp = _absorb_near_dupes([x for x in _susp_all if x in _susp_left])
    _misbeliefs = knowledge_data.get("misbeliefs", knowledge_data.get("false_beliefs", existing.get("misbeliefs", [])))

    d["npc_knowledge"][npc_name] = {
        "knows": merged_knows[-20:],  # 최대 20개 유지
        "secrets_held": knowledge_data.get("secrets_held", existing.get("secrets_held", [])),
        "would_share": knowledge_data.get("would_share", existing.get("would_share", False)),
        "leak_risk": knowledge_data.get("leak_risk", existing.get("leak_risk", "none")),
        "suspects": _merged_susp[-20:],
        "misbeliefs": _misbeliefs[-20:] if isinstance(_misbeliefs, list) else [],
        "last_updated": time.strftime('%Y-%m-%d %H:%M')
    }
    save_domain(channel_id, d)
    _mirror_knowledge(channel_id, npc_name, d["npc_knowledge"][npc_name])

    # [2026-09-14 S5a] 출처 원장 — 근사중복 흡수·20개 트림을 **통과해 실제로 남은** 신규 항목만.
    # 흡수돼 사라진 표면형 변형은 출처를 남기지 않는다(원장이 knows 실물과 어긋나지 않게).
    try:
        if getattr(config, "V10_FACT_SOURCES", False) and isinstance(src, dict):
            _new_facts = [k for k in d["npc_knowledge"][npc_name].get("knows", [])
                          if k not in old_knows]
            if _new_facts:
                import sqlite_store
                _n = sqlite_store.append_fact_sources(
                    channel_id, npc_name, _new_facts,
                    src.get("turn"), src.get("message_id"))
                if _n and _n > 0:
                    logging.info(f"[Evidence] fact_sources npc={npc_name} new={_n} turn={src.get('turn')}")
    except Exception as _e:
        logging.debug(f"[Evidence] fact_sources skipped: {_e}")

# =========================================================
# [V10 Secret Ledger] NPC 지식경계 상태 기계 (에로스 타워 E3, 2026-07-14)
# 스펙: 파티쳇수정/state_v10/v10_secret_ledger_spec.md
# 원칙: 추출은 재료 공급, 압력 계산은 코드(leak_pressure_score — 죽은 배선 승격),
#       truth는 렌더러 직행 금지(iceberg는 surface 우선), 삭제 대신 retire.
# =========================================================

def _secret_id(npc_name: str, truth: str) -> str:
    import hashlib
    h = hashlib.sha1(truth.strip().encode("utf-8")).hexdigest()[:10]
    return f"{npc_name}:{h}"


def sync_secret_ledger(
    channel_id: str,
    npc_name: str,
    kn_data: Dict[str, Any],
    attitude: Dict[str, Any],
) -> Dict[str, Any]:
    """DAI NPCKnowledge → secret_ledger 델타 동기화 (턴당 1회/NPC).

    - secrets_held 각 항목을 원장 행으로 upsert (기존 행은 turn_count+1, 압력 재계산)
    - kn_data["secret_updates"] (추출 Optional 필드) 매칭 시 surface/gate/인식등급 갱신
    - 반환: {"computed_risk": label, "max_pressure": int, "surfaces": {truth: surface}}
      — 호출자가 leak_risk 상향 + 당턴 surface 주입에 사용
    실패는 전부 삼킴 (봇 무영향)."""
    result = {"computed_risk": "none", "max_pressure": 0, "surfaces": {}}
    try:
        import sqlite_store
        from npc_autonomous import leak_pressure_score, leak_risk_label
        secrets = [s for s in (kn_data.get("secrets_held") or []) if isinstance(s, str) and s.strip()]
        updates = kn_data.get("secret_updates") or []
        if not secrets and not updates:
            return result
        existing = {s["secret_id"]: s for s in sqlite_store.read_secrets(channel_id, include_closed=True)}
        tension = attitude.get("tension", 0) if isinstance(attitude, dict) else 0
        depth = attitude.get("depth", 0) if isinstance(attitude, dict) else 0
        now = time.strftime('%Y-%m-%d %H:%M')
        max_pressure = 0
        for truth in secrets:
            sid = _secret_id(npc_name, truth)
            row = existing.get(sid) or {
                "secret_id": sid, "truth": truth, "surface": "",
                "owners": [npc_name], "knowers": [], "suspecters": [],
                "cannot_know": [], "reveal_gate": "", "risk_if_revealed": "",
                "status": "kept", "canon_level": "established", "turn_count": 0,
            }
            row["turn_count"] = int(row.get("turn_count", 0)) + 1
            if row.get("status") in ("kept", "leaking"):
                # kept↔leaking은 압력 파생 상태(왕복 가능) — 비가역은 revealed/retired만
                _prev_status = row.get("status")
                # [2026-08-11 리더 소비자] C2 독자 관측 가산 — leak_pressure는 매 sync **재계산되는 파생값**이라
                # 직접 += 는 다음 sync에 덮인다. 저장 필드 reader_exposure(관측 턴수)를 여기서 가산항으로 환산.
                # 주체 라벨: source="reader". BUMP=0이면 가산 0 = 07-14 공식 그대로.
                _rx = int(row.get("reader_exposure", 0) or 0)
                _rbump = min(_rx * int(getattr(config, "READER_LEAK_BUMP", 3)),
                             int(getattr(config, "READER_LEAK_CAP", 12))) if _rx > 0 else 0
                row["leak_pressure"] = leak_pressure_score(
                    tension, depth, row["turn_count"], reader_bump=_rbump)
                if _rbump > 0:
                    logging.info(
                        f"[ReaderLeak] source=reader {npc_name}: exposure={_rx} bump=+{_rbump} "
                        f"→ pressure={row['leak_pressure']} truth~'{(row.get('truth') or '')[:30]}'")
                row["status"] = "leaking" if row["leak_pressure"] >= 60 else "kept"
                if _prev_status == "kept" and row["status"] == "leaking":
                    # [v1.1 게이트 로그] 사후판독용 — 임계 진입 시 게이트 조건 노출 (log-only)
                    logging.info(f"[secret-ledger] LEAKING {npc_name}: gate='{row.get('reveal_gate','') or '(none)'}' truth~'{(row.get('truth') or '')[:40]}'")
                max_pressure = max(max_pressure, row["leak_pressure"])
            row["updated_at"] = now
            existing[sid] = row
        # 추출 Optional 필드 반영 — truth_ref 부분일치로 행 매칭
        for up in updates:
            if not isinstance(up, dict):
                continue
            ref = str(up.get("truth_ref", "") or "").strip().lower()
            if not ref:
                continue
            for row in existing.values():
                if npc_name not in row.get("owners", []):
                    continue
                if ref not in (row.get("truth") or "").lower():
                    continue
                for k_src, k_dst in (("surface", "surface"), ("reveal_gate", "reveal_gate"),
                                     ("knowers", "knowers"), ("suspecters", "suspecters"),
                                     ("cannot_know", "cannot_know")):
                    v = up.get(k_src)
                    if v:
                        row[k_dst] = v
                # LLM status 입력은 revealed/retired 전이만 수용 — kept/leaking은
                # 압력 파생 상태라 LLM이 리셋 불가 (게이트③ 보강)
                if up.get("status") in ("revealed", "retired"):
                    if up["status"] == "revealed" and not row.get("reveal_gate"):
                        # [v1.1 게이트 로그] 게이트 없는 공개 — 편의 공개 감시 (log-only, 차단 아님)
                        logging.info(f"[secret-ledger] GATELESS-REVEAL {npc_name}: truth~'{(row.get('truth') or '')[:40]}'")
                    row["status"] = up["status"]
                row["updated_at"] = now
                break
        wrote = 0
        for row in existing.values():
            if row.get("updated_at") == now and sqlite_store.upsert_secret(channel_id, row):
                wrote += 1
        if max_pressure > 0:
            result["max_pressure"] = max_pressure
            result["computed_risk"] = leak_risk_label(max_pressure)
        # [v1.1 크로스턴 surface] 이 NPC의 활성 비밀 surface 지도 — 호출자가 당턴
        # secret_updates에 합성 주입 → iceberg가 truth 대신 surface 공급 (게이트① 영속화)
        result["surfaces"] = {
            row["truth"]: row["surface"]
            for row in existing.values()
            if npc_name in row.get("owners", []) and row.get("surface")
            and row.get("status") in ("kept", "leaking")
        }
        if wrote:
            logging.info(f"[secret-ledger] {npc_name}: rows={wrote} max_pressure={max_pressure} risk={result['computed_risk']}")
    except Exception as e:
        logging.warning(f"[secret-ledger] sync 실패 (무시): {npc_name}: {e}")
    return result


def get_secret_ledger(channel_id: str, include_closed: bool = False) -> List[Dict[str, Any]]:
    """비밀 원장 조회 (기본: kept/leaking만). 실패 시 빈 리스트."""
    try:
        import sqlite_store
        return sqlite_store.read_secrets(channel_id, include_closed=include_closed)
    except Exception:
        return []


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

    # [v1.1 게이트②] cannot_know 전파 필터 — 원장 비밀의 truth와 내용어가 겹치는
    # 사실은 해당 비밀의 cannot_know NPC에게 전파 금지. 자기 비밀 정확일치 제외만으로는
    # 타 NPC 비밀 파편(knows에 실린)이 경계를 넘는 구멍이 있었음.
    _guard_rows = []
    try:
        for _r in get_secret_ledger(channel_id):
            if _r.get("cannot_know"):
                _tw = {w for w in _r["truth"].lower().split() if len(w) >= 4}
                if _tw:
                    _guard_rows.append((_tw, set(_r["cannot_know"]), _r["truth"].lower()))
    except Exception:
        _guard_rows = []

    def _blocked_for(npc: str, fact: str) -> bool:
        f_low = fact.lower()
        f_words = {w for w in f_low.split() if len(w) >= 4}
        for _tw, _ck, _tl in _guard_rows:
            if npc not in _ck:
                continue
            if _tl in f_low or f_low in _tl or len(_tw & f_words) >= 3:
                return True
        return False

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
            if _guard_rows:
                _shareable_b = [f for f in shareable if not _blocked_for(npc_b, f)]
            else:
                _shareable_b = shareable
            kn_b = all_knowledge.get(npc_b, {})
            # [2026-09-24 감사] 저장값은 `사실 (via A)` 꼬리가 붙는데 dedup 은 원문 사실로 비교해 영원히 안 맞았다 →
            #   같은 사실이 매 턴 재전파(suspects 중복 누적 + 턴마다 불필요한 save_domain·bulk upsert). 꼬리를 떼고 비교.
            _via_re = re.compile(r"\s*\(via [^)]*\)\s*$")
            existing_b = {_via_re.sub("", str(x)) for x in kn_b.get("knows", [])}
            if getattr(config, "V10_KNOWLEDGE_BOUNDARY_INJECT", False):
                # [V10 지식 lite] 들은 건 의심(suspects)으로 착지 — 직접 목격해야 knows 승격(정보 비대칭)
                _sus_list = list(kn_b.get("suspects", []) or [])
                existing_sus = {_via_re.sub("", str(x)) for x in _sus_list}
                new_facts = [f for f in _shareable_b
                             if _via_re.sub("", str(f)) not in existing_b and _via_re.sub("", str(f)) not in existing_sus][:3]
                if new_facts:
                    tagged = [f"{_via_re.sub('', str(f))} (via {npc_a})" for f in new_facts]
                    kn_b_updated = dict(kn_b)
                    kn_b_updated["suspects"] = (_sus_list + tagged)[-20:]
                    all_knowledge[npc_b] = kn_b_updated
                    propagated += len(new_facts)
            else:
                new_facts = [f for f in _shareable_b if _via_re.sub("", str(f)) not in existing_b][:3]
                if new_facts:
                    tagged = [f"{_via_re.sub('', str(f))} (via {npc_a})" for f in new_facts]
                    merged = list(kn_b.get("knows", []) or []) + tagged
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
_NPC_SIDE_DOMAINS = ("npc_knowledge", "npc_imprints")
# [2026-08-11 사망 파이프라인] 같은 성격인데 **domain이 아니라 world_state**에 사는 것들.
#   도메인 순회 루프로는 구조적으로 안 잡혀서 이관·청소 때마다 개별 손코딩이었고,
#   그래서 08-02에 신설된 `npc_soma_states`가 두 자리 모두에서 빠졌다
#   (오프스테이지 잔존이 붙은 뒤로는 지워지지도 않는 영구 고아).
#   목록을 만들어 둔다 — 다음에 world_state 하위 NPC 저장소가 늘면 여기만 고친다.
_NPC_SIDE_WORLD_KEYS = ("npc_emotion_states", "npc_soma_states")


def migrate_npc_side_data(channel_id: str, old_name: str, new_name: str) -> list:
    """[2026-07-28 신설] 개명/병합 시 NPC 이름을 키로 쓰는 **부수 저장소**를 통째로 옮긴다.

    NPC 본체(npcs dict) 밖에도 이름을 키로 붙들고 있는 곳이 여럿인데, 그동안 경로마다
    옮기는 목록이 달라서(개명은 태도·지식만, 병합은 태도·지식만) 각인·관계엣지·감정이
    옛 이름 아래 고아로 남았다. 옮길 목록을 여기 한 곳에 모은다.
    ★특히 `npc_emotion_states`는 domain이 아니라 **world_state**에 살아서
      도메인 순회 루프로는 구조적으로 안 잡혔다 — 여기서만 처리된다.
    Returns: 옮긴 항목 이름 리스트(로그용).
    """
    moved = []
    if not old_name or not new_name or old_name == new_name:
        return moved
    d = get_domain(channel_id)
    for _dom in _NPC_SIDE_DOMAINS:
        _dd = d.get(_dom)
        if isinstance(_dd, dict) and old_name in _dd:
            if new_name not in _dd:
                _dd[new_name] = _dd.pop(old_name)
                moved.append(_dom)
            else:
                _dd.pop(old_name, None)     # 양쪽 존재 시 새 이름 유지
    save_domain(channel_id, d)
    try:
        import sqlite_store as _ss_mv
        if _ss_mv.rename_edge_entity(channel_id, old_name, new_name):
            moved.append("relations")
    except Exception as _e_mv:
        logging.debug(f"[NPC] 엣지 이관 skip: {_e_mv}")

    # 감정 이력·soma 스냅샷 — world_state 소관(별도 저장소)
    try:
        _w = get_world_state(channel_id) or {}
        _dirty = False
        for _wk in _NPC_SIDE_WORLD_KEYS:
            _wd = _w.get(_wk)
            if isinstance(_wd, dict) and old_name in _wd:
                if new_name not in _wd:
                    _wd[new_name] = _wd.pop(old_name)
                else:
                    _wd.pop(old_name, None)   # 양쪽 존재 시 새 이름 유지
                _dirty = True
                moved.append(_wk)
        if _dirty:
            update_world_state(channel_id, _w)
    except Exception as _e:
        logging.debug(f"[NPC] world_state 부수 저장소 이관 skip: {_e}")
    return moved


def purge_npc_side_data(channel_id: str, name: str) -> list:
    """[2026-07-28 신설] NPC 삭제 시 부수 저장소 청소. 이관의 짝.

    구 delete_npc는 npcs/attitudes/knowledge(+SQLite)만 지워서 각인·관계엣지·감정이
    이름만 남은 고아가 됐다(장기 세션일수록 누적).
    """
    purged = []
    if not name:
        return purged
    d = get_domain(channel_id)
    for _dom in _NPC_SIDE_DOMAINS:
        _dd = d.get(_dom)
        if isinstance(_dd, dict) and _dd.pop(name, None) is not None:
            purged.append(_dom)
    save_domain(channel_id, d)
    # 관계 엣지: 이 이름이 걸린 방향 전부 제거 (relations 테이블)
    try:
        import sqlite_store as _ss_pg
        _n_edges = _ss_pg.delete_edges(channel_id, name)
        if _n_edges:
            purged.append(f"relations({_n_edges})")
    except Exception:
        pass
    try:
        _w = get_world_state(channel_id) or {}
        _dirty = False
        for _wk in _NPC_SIDE_WORLD_KEYS:
            _wd = _w.get(_wk)
            if isinstance(_wd, dict) and _wd.pop(name, None) is not None:
                _dirty = True
                purged.append(_wk)
        if _dirty:
            update_world_state(channel_id, _w)
    except Exception:
        pass
    # [2026-08-11 사망 파이프라인] world_tree 잔존 presence.
    #   `remove_npc_presence`는 독스트링에 "개명/퇴장 시"라 적혀 있는데 **퇴장 호출자가 0**이었다
    #   — 삭제된 인물이 노드의 npcs_present에 영원히 서 있었고, 그 목록은 장면 조회로 흘러간다.
    #   (import는 지연 — world_tree가 domain_manager를 모듈 최상단에서 import한다.)
    try:
        import world_tree as _wt
        if _wt.remove_npc_presence(channel_id, name):
            purged.append("world_tree")
    except Exception as _e:
        logging.debug(f"[NPC] world_tree presence 정리 skip: {_e}")
    return purged


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
        "notebook": notebook_default(),  # [notebook v2] 섹션 dict 정본
        "status_effects": [],
        "ai_memory": {
            # [2026-09-16 시트 2차] 서술 필드(appearance/personality/background/description) 삭제 —
            #   PC 서술의 정본은 위키 PC 페이지 lore 절(`!가면`이 페이지를 세운다).
            "passives": [], "notes": "", "archived_info": [],
            # [V7→V3.0] Core Systems: 2-Axis (Vigor/Composure)
            "vigor": {"value": 100, "last_delta": 0},
            "composure": {"value": 100, "last_delta": 0},

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

# [2026-09-16 시트 2차 §8] `default_pc_info` = **대기 상자**. 모양:
#   {"name", "aliases"?, "species"?, "sheet_text"(시트 원문), "passives"?, "inventory"?}
#   마스크가 맞는 참가자(PC 페이지 보유)가 나타나면 원문은 페이지 lore 절로, passives/inventory는
#   지금 자리(ai_memory·노트북 [소지품]) 그대로 이월하고 상자를 **비운다**.
def pc_mask_matches(mask: Any, name: Any, aliases: Any = None) -> bool:
    """마스크 매칭 하나 — `_norm_quote` 정규화 후 정확일치 또는 aliases. 부분일치 없음."""
    try:
        from fermentation import _norm_quote as _nq
    except Exception:
        _nq = lambda x: re.sub(r"\s+", " ", str(x or "")).strip()
    m = _nq(str(mask or "")).lower()
    if not m:
        return False
    cands = [name] + (list(aliases) if isinstance(aliases, list) else [])
    return any(isinstance(c, str) and c.strip() and _nq(c).lower() == m for c in cands)


def get_pc_page_id(channel_id: str, uid: Any) -> Optional[str]:
    try:
        import wiki_store
        return wiki_store.pc_page_id(channel_id, uid)
    except Exception:
        return None


def apply_pc_info_to_user(channel_id: str, user_id: str) -> bool:
    """대기 상자 → 이 참가자에 흡수. 페이지(`!가면`) 없으면 흡수 안 함(False, 상자 유지).
    매칭 판정은 호출자가 한다(`sync_matching_participants`·`!가면`은 `pc_mask_matches`, `!설명`은 본인)."""
    pc_info = get_default_pc_info(channel_id)
    if not pc_info: return False

    p = get_participant_data(channel_id, user_id)
    if not p: return False
    pid = get_pc_page_id(channel_id, user_id)
    if not pid:
        return False

    _text = str(pc_info.get("sheet_text") or "").strip()
    if _text:
        try:
            import wiki_store
            wiki_store.set_sheet_text(channel_id, pid, _text, as_notes=bool(pc_info.get("sheet_fallback")))
        except Exception as _e_ws:
            logging.warning(f"[PC Sync] 시트 원문 → 페이지 실패: {_e_ws}")
            return False

    mem = p.get("ai_memory", {})
    if not mem:
        mem = _create_default_participant("")["ai_memory"]
        p["ai_memory"] = mem

    # [2026-09-16 3차] 조각 이월 — 새 모양 {name, desc, value, origin=sheet}, 이름 기준 교체.
    new_passives = pc_info.get("passives", [])
    if new_passives:
        from game_character import merge_fragments
        mem["passives"], _ = merge_fragments(mem.get("passives", []), new_passives, "sheet")

    save_participant_data(channel_id, user_id, p)

    # inventory → 노트북 [소지품] (add_item_to_sojipin 정식 경로, dedup 내장). 위치 무변경.
    try:
        import game_character as _gc
        _inv = pc_info.get("inventory")
        _names = []
        if isinstance(_inv, list):
            for _it in _inv:
                if isinstance(_it, dict) and _it.get("name"):
                    _names.append(str(_it["name"]).strip())
                elif isinstance(_it, str) and _it.strip():
                    _names.append(_it.strip())
        elif isinstance(_inv, dict):
            _names = [str(_k).strip() for _k in _inv.keys() if str(_k).strip()]
        for _nm in _names:
            if _nm:
                _gc.add_item_to_sojipin(channel_id, _nm, user_id)
    except Exception as _e_nb:
        logging.debug(f"[PC Sync] 노트북 인벤 반영 skipped: {_e_nb}")

    clear_default_pc_info(channel_id)   # 흡수 후 상자는 비운다(동기화 대상 소멸)
    return True

def sync_matching_participants(channel_id: str, pc_info: Dict[str, Any]) -> List[str]:
    """대기 상자의 이름(또는 aliases)과 마스크가 맞는 **PC 페이지 보유** 참가자 하나에 흡수.
    흡수는 상자를 비우므로 첫 매치 하나만 받는다."""
    if not pc_info or not pc_info.get("name"): return []
    d = get_domain(channel_id)
    for uid, p_data in d.get("participants", {}).items():
        if not isinstance(p_data, dict) or not get_pc_page_id(channel_id, uid):
            continue
        if pc_mask_matches(p_data.get("mask", ""), pc_info.get("name"), pc_info.get("aliases")):
            if apply_pc_info_to_user(channel_id, uid):
                return [uid]
            return []
    logging.info("[PC Sync] 대기 상자 name=%r — 마스크 맞는 PC 페이지 없음, 대기 유지.", pc_info.get("name"))
    return []

def get_ai_memory(channel_id: str, uid: str) -> Dict[str, Any]:
    p = get_participant_data(channel_id, uid)
    return p.get("ai_memory", {}) if p else {}

# [일지 전체 로그 2026-07-04] 표시(노트북 [일지] 최근 N줄)와 저장(전체 이력)을 분리.
# 노트북엔 최근 N줄만 렌더되어 매 턴 프롬프트 부담↓, 전체는 ai_memory.journal_log에 영속.
_JOURNAL_LOG_CAP = 500  # 안전 상한 (초과 시 오래된 것부터 드롭 — 사실상 무제한에 가까움)

def get_journal_log(channel_id: str, uid: str) -> List[str]:
    """PC 일지 전체 이력(리스트). 노트북 [일지] 섹션은 이 로그의 최근 N줄 렌더."""
    mem = get_ai_memory(channel_id, uid)
    log = mem.get("journal_log", [])
    return [str(x) for x in log] if isinstance(log, list) else []

def append_journal_log(channel_id: str, uid: str, entry: str) -> List[str]:
    """일지 1건을 전체 로그에 append(직전 항목과 정규화 중복이면 스킵) 후 저장. 갱신된 로그 반환."""
    entry = str(entry or "").strip()
    if not entry or not uid:
        return get_journal_log(channel_id, uid) if uid else []
    _norm = lambda s: re.sub(r'\s+', ' ', str(s).strip())
    p = get_participant_data(channel_id, uid)
    if not p:
        return []
    mem = p.get("ai_memory", {})
    if not isinstance(mem, dict):
        mem = {}
    log = mem.get("journal_log", [])
    if not isinstance(log, list):
        log = []
    if not log or _norm(log[-1]) != _norm(entry):  # 연속 중복만 방지(재발은 허용)
        log.append(entry)
    log = log[-_JOURNAL_LOG_CAP:]
    mem["journal_log"] = log
    p["ai_memory"] = mem
    save_participant_data(channel_id, uid, p)
    return log

def update_ai_memory(channel_id: str, uid: str, updates: Dict[str, Any]) -> None:
    p = get_participant_data(channel_id, uid)
    if not p: return
    
    mem = p.get("ai_memory", {})
    
    for k, v in updates.items():
        mem[k] = v
            
    p["ai_memory"] = mem
    save_participant_data(channel_id, uid, p)

def return_removed_play_fragments(channel_id: str, uid: str, old_mem: Dict[str, Any],
                                  new_mem: Dict[str, Any]) -> int:
    """[2026-09-16 3차] OOC로 사라진 origin=play 조각의 desc 를 PC 페이지 Observed 끝에 되돌린다(가역).
    쓰기는 wiki_store.append_observed_tail 하나. Returns: 되돌린 줄 수."""
    import memory_system
    import wiki_store
    gone = memory_system.removed_play_fragments(old_mem, new_mem)
    pid = wiki_store.pc_page_id(channel_id, uid) if gone else None
    n = 0
    for fr in gone:
        desc = str(fr.get("desc") or "").strip()
        if pid and desc and wiki_store.append_observed_tail(channel_id, pid, desc).get("ok"):
            n += 1
    return n


def add_to_ai_memory_list(channel_id: str, uid: str, key: str, item: Union[str, Dict[str, Any]]) -> None:
    p = get_participant_data(channel_id, uid)
    if not p: return
    
    mem = p.get("ai_memory", {})
    if key not in mem: mem[key] = []
    if key == "passives":
        # [2026-09-16 3차] 조각은 새 모양으로만 저장된다(옛 tags/theory_links/modifiers 경로 0).
        from game_character import normalize_fragment
        item = normalize_fragment(item, "sheet")
        if not item:
            return

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
# [2026-07-18 고아 삭제] get_psych_profile — 구세대(Phase 2) Maslow 심리 프로필 — 현행 DAI psyche/deep_read/emotion_engine이 대체 (dead_scan 참조0 확인, git 이력 복원 가능)

# [2026-07-18 고아 삭제] update_psych_profile — 구세대(Phase 2) Maslow 심리 프로필 — 현행 DAI psyche/deep_read/emotion_engine이 대체 (dead_scan 참조0 확인, git 이력 복원 가능)

# UI Helpers
def get_pc_sheet_text(channel_id: str, user_id: str, sections: Optional[list] = None) -> str:
    """PC 페이지 lore 절(+Observed) 발췌 렌더. 발췌 규칙은 `render_page` 기존 규칙(절당 600) 그대로.
    페이지 없으면 ""."""
    pid = get_pc_page_id(channel_id, user_id)
    if not pid:
        return ""
    try:
        import wiki_store
        if sections is None:
            sections = list(getattr(config, "WIKI_LORE_SECTIONS", {}).get("character", ())) + ["Observed"]
        txt = wiki_store.render_page(channel_id, pid, sections)
        # 첫 줄 `## 이름`은 호출자 헤더와 겹친다 — 절만 돌려준다.
        return txt.split("\n", 1)[1].strip() if "\n" in txt else ""
    except Exception as _e:
        logging.debug(f"[PC Sheet] render skipped: {_e}")
        return ""


def apply_ooc_sheet_edits(channel_id: str, uid: str, edits: List[Dict[str, Any]]) -> List[str]:
    """OOC 절 편집(field=절 이름) → PC 페이지 lore 절. 되비침 줄 반환."""
    pid = get_pc_page_id(channel_id, uid)
    if not pid:
        return ["⚠️ 시트 편집: `!가면`으로 캐릭터 페이지를 먼저 세워 주세요."]
    out = []
    try:
        import wiki_store
        for e in edits or []:
            sec = str(e.get("field") or "")
            act = str(e.get("action") or "set")
            if wiki_store.edit_lore_section(channel_id, pid, sec, str(e.get("value") or ""), action=act):
                out.append(f"📄 {sec} {act}")
    except Exception as _e:
        logging.error(f"[OOC] 시트 절 편집 실패: {_e}")
    return out


def get_unified_player_info(channel_id: str, user_id: str, *, shape: str = "render") -> Any:
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
    if shape == "anchor":
        # [2026-09-16 시트 2차 §8 뷰 하나] 좌뇌(une anchors → theoria)용 모양 — 같은 원천(페이지 절).
        if not p:
            return {"mask": "Unknown", "sheet": "", "passives": []}
        _m = p.get("ai_memory", {}) or {}
        return {"mask": p.get("mask", "Unknown"), "sheet": get_pc_sheet_text(channel_id, user_id),
                "passives": _m.get("passives", [])}
    if not p:
        return "## 🎭 Unknown Player\n(No data available)"

    name = p.get("mask", "Unknown")
    mem = p.get("ai_memory", {})

    # 1. Sheet — PC 페이지 lore 절 + Observed(렌더 발췌 규칙은 render_page 그대로)
    desc_text = get_pc_sheet_text(channel_id, user_id) or "No description available."

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
    # [2026-09-15 관계 통합] 채널 전역이 아니라 **이 PC를 target으로 하는 엣지**(설계 §5 Slot 6).
    attitudes = get_npc_attitudes(channel_id, pc=name) if name and name != "Unknown" else {}
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
    # [2026-08-18 Phase 2.5] 기력 = 레지스트리 값(custom_var_values["기력"][uid]). 표시 무변경.
    vigor = mem.get("vigor", mem.get("mental", {}))
    try:
        import custom_vars as _cv_pb
        vigor_val = _cv_pb.vigor_value(channel_id, user_id, mem)
    except Exception as _e_cvpb:
        vigor_val = vigor.get("value", 100)
    # [2026-09-24 감사] 평형도 레지스트리 문 경유 — P8b 이후 ai_memory.composure 는 동결이라
    #   Slot 6 이 옛 값을, Slot 29 가 레지스트리 값을 찍어 한 프롬프트에 평형이 두 값이었다.
    #   (위 except 의 `logger` 는 이 모듈에 정의가 없어 폴백 경로에서 NameError → 제거)
    composure = mem.get("composure", {})
    try:
        import custom_vars as _cv_pb2
        composure_val = _cv_pb2.composure_value(channel_id, user_id, mem)
    except Exception:
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
    lines.append(f"- Sheet:\n{desc_text}")
    lines.append(f"\n### 📓 Player Notebook (Inventory & Memos)\n{notebook}")
    lines.append(f"\n⚠️ CRITICAL: YOU ARE THE GM. {name} IS THE PLAYER.\nDO NOT speak for {name}. DO NOT describe {name}'s actions.\nOnly describe the world's reaction to {name}.")
    return "\n".join(lines)

# =========================================================
# 5. STATE ACCESSORS (From legacy domain_manager)
# =========================================================

# ---------------------------------------------------------
# [2026-09-06 P1 선언 층] output_decl — 유저가 **선언한 출력 저작**의 자리.
#   world_state 가 아니라 **도메인 루트**에 산다. 이유 하나: 수명.
#   world_state 는 `!클리어`(reset_session_state)가 통째로 DEFAULT 로 갈아엎는 곳이라
#   거기 둔 저작(패널 형식)은 세션 리셋마다 사라졌다 — 저작은 세션이 아니라 채널의 것이다.
#   생존은 **무접촉**으로 얻는다: reset_session_state 에 이 키를 적는 줄이 한 줄도 없어야
#   생존이 코드 변경에 안 흔들린다(적으면 그 줄이 곧 삭제 후보가 된다).
#   reset_domain 은 파일을 지우므로 여기도 함께 소멸 — 그건 의도한 수명이다.
# 이 단계(P1)엔 panel_sections 만 담는다. custom_vars·output_rules 이관은 P7.
OUTPUT_DECL_VERSION = 1
# 선언 층의 dict 칸 — 화이트리스트가 한 곳이라야 새 칸이 다른 모듈의 저장에
# 조용히 지워지지 않는다(expr_engine·status_panel 은 층 전체를 읽어 되쓴다).
_DECL_DICT_KEYS = ("panel_sections", "derives", "transitions",
                   "custom_vars", "output_rules",
                   # [2026-09-13 P16] 조건부 지시. 화이트리스트 밖이면 저장이 조용히 증발한다.
                   "directives")


def output_decl_default() -> Dict[str, Any]:
    # [2026-09-06 P3] derives·transitions 가 panel_sections 옆에 선다 — 셋 다 **선언**이라
    #   같은 층(클리어 생존)에 살아야 한다(스펙 ④-7). 값·발화 이력은 world_state 다.
    # [2026-09-06 P7] custom_vars(변수 선언)·output_rules(형식·헤더 저작)가 합류한다 —
    #   P6 보고서 §5 "한 파일의 등록물이 수명이 갈린다"를 닫는 자리다. 값(custom_var_values)
    #   은 따라오지 않는다: 선언은 채널의 것이고 값은 세션의 것이라 수명이 다르다.
    return {"v": OUTPUT_DECL_VERSION, "panel_sections": {}, "derives": {}, "transitions": {},
            "custom_vars": {}, "output_rules": {}, "directives": {}}


def get_output_decl(channel_id: str) -> Dict[str, Any]:
    """선언 층 읽기. 없으면 기본값 **사본**(읽기가 파일을 만들지 않는다)."""
    decl = get_domain(channel_id).get("output_decl")
    out = output_decl_default()
    if isinstance(decl, dict):
        out.update(decl)
    for _k in _DECL_DICT_KEYS:
        if not isinstance(out.get(_k), dict):
            out[_k] = {}
    if not isinstance(out.get("v"), int):
        out["v"] = OUTPUT_DECL_VERSION
    return out


def update_output_decl(channel_id: str, decl: Dict[str, Any]) -> None:
    """선언 층 쓰기. 형태가 아니면 기본값으로 눕힌다(저장은 항상 같은 모양)."""
    if not isinstance(decl, dict):
        decl = output_decl_default()
    d = get_domain(channel_id)
    out = {"v": decl.get("v") if isinstance(decl.get("v"), int) else OUTPUT_DECL_VERSION}
    for _k in _DECL_DICT_KEYS:
        out[_k] = decl.get(_k) if isinstance(decl.get(_k), dict) else {}
    d["output_decl"] = out
    save_domain(channel_id, d)


# ---------------------------------------------------------
# [2026-09-06 P7] 형식·헤더 저작(`!출력룰`)의 단일 관문.
#   종전엔 world_state["output_rules"] 를 7개 파일이 각자 첨자로 열었다 — 그래서
#   `!클리어`(world_state 통째 교체)가 저작을 지우는 것을 아무도 못 막았다.
#   여기 한 곳으로 모으면 수명은 함수 두 개의 성질이 된다.
# **lazy 이월**(08-18 규율): 읽기는 옛 자리를 볼 뿐 옮기지 않는다 — 읽기가 파일을
#   바꾸면 "언제 이사했는가"가 사용 기록에 녹아 롤백이 불가능해진다. 첫 쓰기가 옮긴다.

def get_output_rules(channel_id: str) -> Dict[str, Any]:
    """{키: {desc, created_at, source}}. 선언 층이 비면 옛 자리(world_state)를 읽는다."""
    try:
        cur = get_output_decl(channel_id).get("output_rules")
    except Exception as e:
        logging.debug(f"[P7] output_rules decl read skipped: {e}")
        cur = None
    if isinstance(cur, dict) and cur:
        return dict(cur)
    try:
        legacy = (get_world_state(channel_id) or {}).get("output_rules")
    except Exception as e:
        logging.debug(f"[P7] output_rules legacy read skipped: {e}")
        legacy = None
    return dict(legacy) if isinstance(legacy, dict) else {}


def set_output_rules(channel_id: str, rules: Dict[str, Any]) -> None:
    """선언 층에 쓰고 **옛 자리는 비운다** — 두 자리에 남으면 폴백이 삭제를 되살린다
    (`!출력룰 초기화` 가 조용히 아무것도 못 지우는 사고)."""
    decl = get_output_decl(channel_id)
    decl["output_rules"] = dict(rules) if isinstance(rules, dict) else {}
    update_output_decl(channel_id, decl)
    try:
        ws = get_world_state(channel_id) or {}
        if ws.get("output_rules") is not None:
            ws["output_rules"] = {}
            update_world_state(channel_id, ws)
    except Exception as e:
        logging.debug(f"[P7] legacy output_rules purge skipped: {e}")


def get_world_state(channel_id: str) -> Dict[str, Any]:
    ws = get_domain(channel_id).get("world_state")
    if not isinstance(ws, dict):
        ws = __import__("copy").deepcopy(config.DEFAULT_WORLD_STATE)  # [2026-09-24 감사] 얕은 사본 공유 방지
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
    # [2026-07-15 D1 환경 노화] 장면 경계 = 장소 변경. 여기가 유일한 진입점
    # (orchestration L166 단일 호출)이라 앵커 리셋을 여기 둔다.
    # ⚠ 매 턴 같은 장소로도 호출되므로 **실제 변경일 때만** 리셋 — 안 그러면
    #    경과가 매 턴 0으로 깎여 노화가 영원히 임계를 못 넘는다.
    _prev = ws.get("current_location")
    # [2026-09-02 R3 갱신 누락 관측] 스펙 §5 / §6 R3.
    # 병: 위치 기반 출석으로 넘어가면 임계 경로가 **PC 이동 감지 하나**로 이사한다.
    #   지금은 매 턴 전량 재판정이라 1~2턴이면 자기 치유되지만, 위치가 stale해지면
    #   장면이 통째로 안 바뀐다(치명). `get_npc_current_location` 주석이 경고하는 그 병
    #   ("갱신 로직 전무 → 시간이 갈수록 stale")이 출석 축으로 옮겨오는 형태다.
    # 처방: 값 1개짜리 관측 — 같은 장소로 몇 턴째 호출되는지 세고, 10턴마다 한 줄.
    #   임계를 넘겼다고 아무것도 고치지 않는다(사람이 로그를 보고 판단). 동작 무변경.
    if _prev != location:
        ws["scene_elapsed_min"] = 0
        ws["_loc_stale_turns"] = 0
    else:
        try:
            _stale = int(ws.get("_loc_stale_turns", 0) or 0) + 1
        except (TypeError, ValueError):
            _stale = 1
        ws["_loc_stale_turns"] = _stale
        if _stale % 10 == 0:
            logging.info("[presence-check] location stale %d turns: %s", _stale, location)
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

# [2026-08-17 기본 ON 부가 모듈]
# 구 모델은 "리스트에 있으면 켜짐" 하나뿐이라 **미설정 = 꺼짐**이었다. 그래서 world_board는
# `!게시판 on`을 친 채널에서만 돌았고 실측 가동률이 0이었다(기능은 있는데 아무도 안 켬).
# 기본을 뒤집되 "명시적 off"는 살려야 하므로 축을 하나 더 둔다:
#   settings.active_modules   = 명시적 ON 기록 (구 스키마 그대로 — 옛 `!게시판 on` 채널 보존)
#   settings.disabled_modules = 명시적 OFF 기록 (신설 — 기본 ON을 이기는 유일한 값)
# 판정 = CORE ∪ 명시ON ∪ (기본ON − 명시OFF). 미설정 채널만 기본이 바뀐다.
# ⚠ 구 off 경로는 "리스트에서 제거"였다 = 미설정과 **구별 불가**했다. 그 시절 off를 친
#   채널은 이번 전환에서 ON으로 돌아온다(기록이 존재하지 않으므로 존중할 값이 없다).
#   지금부터의 off는 disabled_modules에 남아 영구히 존중된다.
DEFAULT_ON_MODULES = {"board", "mind"}

def get_active_modules(channel_id: str) -> List[str]:
    """현재 활성화된 모듈 리스트를 반환합니다.
    핵심 4모듈(judgment, doom, anomaly, mental)은 항상 활성.
    board/mind 등 부가 모듈은 **기본 ON**이며 명시적 off(disabled_modules)만 이를 끈다."""
    d = get_domain(channel_id)
    settings = d.get("settings", {}) or {}
    stored = set(settings.get("active_modules", []) or [])
    disabled = set(settings.get("disabled_modules", []) or [])
    # 핵심 모듈은 항상 포함 / 기본 ON 모듈은 명시적으로 껐을 때만 빠진다
    return list(CORE_MODULES | (stored - disabled) | (DEFAULT_ON_MODULES - disabled))

def toggle_module(channel_id: str, module_name: str, active: bool) -> None:
    """부가 모듈(board/mind 등)을 켜거나 끕니다.
    핵심 4모듈은 토글 불가 (항상 활성).

    두 축을 **항상 반대로** 갱신한다 — on은 disabled에서 지우고, off는 disabled에 적는다.
    (한 축만 만지면 기본 ON 모듈의 off가 무시되거나, 켠 기록이 남아 off를 이긴다.)
    구 구현은 `set(get_active_modules(...))`를 읽어 CORE·기본ON까지 저장 리스트에 눌러
    담았다 — 저장본이 판정에 못 미치는 노이즈였다. 저장 리스트만 읽는다.
    """
    if module_name in CORE_MODULES:
        return  # 핵심 모듈은 항상 활성 — 토글 무시
    d = get_domain(channel_id)
    settings = d.get("settings", {}) or {}
    modules = set(settings.get("active_modules", []) or [])
    disabled = set(settings.get("disabled_modules", []) or [])
    if active:
        modules.add(module_name)
        disabled.discard(module_name)
    else:
        modules.discard(module_name)
        disabled.add(module_name)
    update_settings(channel_id,
                    active_modules=sorted(modules),
                    disabled_modules=sorted(disabled))

def is_vigor_composure_active(channel_id: str) -> bool:
    """기력/평형(활력/평형) 모듈 활성 여부.
    명시적으로 끄지 않은 한 항상 ON (기본 True → 레거시 채널 무손실)."""
    d = get_domain(channel_id)
    return bool(d.get("settings", {}).get("vigor_composure_enabled", True))

def set_vigor_composure_active(channel_id: str, active: bool) -> None:
    """기력/평형 모듈을 채널 단위로 켜고 끈다.
    off면 파이프라인 prime/process 스킵 + 프롬프트 주입 스킵 (수치 동결)."""
    update_settings(channel_id, vigor_composure_enabled=bool(active))

def set_response_mode(channel_id: str, mode: str) -> None:
    d = get_domain(channel_id)
    d["settings"]["response_mode"] = mode
    save_domain(channel_id, d)

def get_response_mode(channel_id: str) -> str:
    d = get_domain(channel_id)
    return d["settings"].get("response_mode", "auto")

# [2026-08-11 비일상적응도 삭제] get_abnormal_mode / set_abnormal_mode 및 settings.abnormal_mode
# 기본값 제거 — 토글할 대상(노출 카운트 누적)이 코드에 없어 참조 0인 스위치였음.
# 참가자 스키마 abnormal_exposure / 레거시 normalization 기본값·리셋도 같이 철거.
# 저장된 세션의 고아 키는 그대로 둔다(읽는 코드가 없어 무해). 복원은 git 이력.

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
        # [2026-08-11 arc digest 부활] 턴 도장 — 발효 청크의 턴범위를 알 유일한 단서였는데 없었다.
        # 소스는 world_state.turn_index: emotion_log/attitude_log/turn_snapshot이 쓰는 바로 그 카운터
        # (waterfall_pipeline:454 current_turn / npc_manager:1625 current_turn ← 둘 다 turn_index).
        # read_arc_window가 그 turn으로 조회하므로 소스가 갈리면 창이 통째로 빈다. 비용 0 (world 재사용).
        # 0(첫 턴 진입 전)이면 키 자체 생략 — legacy 엔트리와 동형으로 조용히 스킵.
        _ti = int(world.get("turn_index", 0) or 0)
        if _ti > 0:
            entry["turn"] = _ti
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
    for _uid_look, p in participants.items():
        if p.get("status") != "active": continue

        mask = p.get("mask", "Unknown")
        _pid_look = get_pc_page_id(channel_id, _uid_look)
        try:
            import wiki_store as _ws_look
            look = (_ws_look.get_lore_sections(channel_id, _pid_look).get("Identity", "") if _pid_look else "") or "Unknown"
        except Exception:
            look = "Unknown"
        look = look[:50]
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


def get_prev_fingerprint(channel_id: str) -> Dict[str, Any]:
    """지문이 실제로 찍힌 가장 최근 프레임의 render_fingerprint를 반환(없으면 {}).

    [2026-08-12 fingerprint 프레임 소급] `get_latest_frame`은 frames[-1]을 주는데, 그 프레임은
    **이번 턴 시작에 push된 빈 프레임**이라 render_fingerprint가 항상 `{}`다(지문은 턴 종료 후
    배경 추출이 frames[-1]에 UPDATE — 즉 지문은 늘 한 프레임 뒤). 따라서 지문 소비자는
    get_latest_frame이 아니라 **이 함수 하나**를 쓴다. 소비자마다 조건식을 따로 심으면
    자매 자리 소급 누락이 재발한다(단일 관문).

    판정은 값 truthiness가 아니라 **키 존재**(fingerprint dict 비어있지 않음) 기준 —
    "none"도 유효값(직전 렌더가 그 수법을 안 썼다)이므로 거기서 멈춘다.
    """
    try:
        frames = (get_scene_continuity(channel_id) or {}).get("frames", []) or []
    except Exception:
        return {}
    for f in reversed(frames):
        fp = (f or {}).get("render_fingerprint") or {}
        if isinstance(fp, dict) and fp:
            return fp
    return {}


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

# [2026-08-11 로드아웃 삭제] pending_flashback 3함수 + loadout 3함수 제거 — !회상 명령 폐기로 호출처 0.
# 기존 세션에 남은 domain["pending_flashback"] / ai_memory["loadout"] 키는 읽는 코드가 없어 무해한 고아
# (마이그레이션 없음 — 세션 리셋 때 자연 소멸).

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
    # [2026-09-24 감사] deep_memory_data 를 안 비우면 P4 이음매가 기존 행 값을 이어 써 옛 세션 active triggers·
    #   [chronicle hook] 이 새 세션 Slot 9·게시판에 급식됐다. 발효 소유 루트 키도 같이 초기화(연대기·GC 카운터 포함).
    d["deep_memory_data"] = {}
    for _fk in ("active_memory_triggers", "chronicles", "chronicle_unresolved", "structured_slots",
                "memory_gc_backup", "ferment_fail_streak", "ferment_empty_streak",
                "_last_chronicle_ferment_count", "_last_memory_gc_ferment_count"):
        d.pop(_fk, None)
    d["ai_session_memory"] = _get_default_session()["ai_session_memory"]
    # [V10 Sprint 3] 영구 로그도 삭제 — 리셋=완전 새 이야기 (사용자 결정 2026-06-10).
    # [V10 P4] fermented/deep 테이블은 함수 끝 save_domain의 이음매가 위 빈 값으로 비운다.
    try:
        import sqlite_store
        sqlite_store.clear_history_log(channel_id)
        sqlite_store.clear_ledger(channel_id)  # [Sprint 4] 막간 장부도 동일 정책
        # [2026-07-05 혼입 수리] 세션 파생 8테이블(관계/지식/계측 로그)도 동일 정책 —
        # 안 지우면 narrative_queries 계측·AttitudeGate 쿨다운·NPC 지식이 옛 세션을 새 세션에 급식.
        sqlite_store.clear_session_scoped(channel_id)
    except Exception as _e:
        logging.debug(f"[V10] history log clear skipped: {_e}")

    # [2026-07-05 혼입 수리] 플레이 파생 도메인 루트 키 — 태도/지식/엔티티관계는 세션 소속.
    # (!클리어 스펙 "유지=로어북·참가자·룰·등록 NPC"에서 유지 대상은 NPC '시트'지 플레이 상태가 아님.
    #  실측: 턴1에 AttitudeGate cooldown -64, 옛 지식 6 facts, 'Deep(은색 캔 약속)' 혼입.)
    # [2026-09-15 관계 통합] 옛 관계 키 둘은 비우지 않고 **삭제**(relations 엣지는 clear_session_scoped가 지운다).
    d.pop("npc_attitudes", None)
    d["npc_knowledge"] = {}
    d.pop("entity_relations", None)
    # [2026-07-28] npc_imprints 추가 — 다른 세션 파생 데이터는 다 지우면서 각인만 남아
    # 리셋 후에도 옛 행동 기록이 따라왔다(감정 이력은 world_state 리셋으로 함께 사라짐).
    d["npc_imprints"] = {}

    # [2026-08-11 리더 §7] reader 유래 이변 시드 청소 — 영속/휘발 비대칭 해소.
    # 시드는 lore_summary_data(영속)인데 근거인 reader_log는 clear_session_scoped로 사라져,
    # 새 세션에 "왜 있는지 모르는" 옛 독자 시드가 잔류했다. cognition 유래 시드는 로어 채굴
    # 산물이라 존치(로어를 유지하는 !클리어 스펙과 정합). reader_blurb도 존치 — 로어 sha1
    # 해시 기반이라 로어가 같으면 재사용이 정당하고, 바뀌면 해시 미스로 자동 재생성된다.
    try:
        _lsd = d.get("lore_summary_data") or {}
        _seeds = _lsd.get("anomaly_seeds")
        if isinstance(_seeds, list):
            _kept = [s for s in _seeds
                     if not (isinstance(s, dict) and s.get("source") == "reader")]
            if len(_kept) != len(_seeds):
                _lsd["anomaly_seeds"] = _kept
                d["lore_summary_data"] = _lsd
                logging.info(f"[Reset] reader 유래 이변 시드 {len(_seeds) - len(_kept)}개 제거 "
                             f"(근거 reader_log가 함께 삭제됨)")
    except Exception as _e_rs:
        logging.debug(f"[Reset] reader seed purge skipped: {_e_rs}")

    # 2. Reset World State
    # [2026-09-24 감사] `!룰 추가`의 하우스 룰은 world_state(rules_text·location_rules)에 산다 — 통째 교체로
    #   매 `!클리어`마다 사라졌는데 완료 안내는 "유지됨: …룰"이었다. 두 키는 이월한다.
    _old_ws = d.get("world_state") or {}
    import copy as _copy_rs
    d["world_state"] = _copy_rs.deepcopy(config.DEFAULT_WORLD_STATE)  # 얕은 .copy()는 중첩 list(doom_clocks 등)를 기본값과 공유
    for _rk in ("rules_text", "location_rules"):
        if _old_ws.get(_rk):
            d["world_state"][_rk] = _old_ws[_rk]
    d["settings"]["session_locked"] = False # Unlock for re-start
    
    # 3. Reset Quests & Notebook (Keep Lore Items if any? No, reset all dynamic)
    d["quest_board"] = {"active": [], "completed": [], "memos": [], "archive": [], "lore": []}
    d["notebook"] = notebook_default()
    d["notebook_shared"] = notebook_default()
    d["ooc_mode"] = False
    
    # 4. Reset Session NPCs (Keep 'lore' + 'manual' NPCs)
    # AI가 생성한 세션 NPC만 제거, 유저가 직접 등록한 NPC는 보존
    if "npcs" in d:
        kept_npcs = {}
        try:
            import wiki_store as _ws_r
            _lm_r = _ws_r.character_lore_map(channel_id, with_source=True)   # [§H] 시드 도장 동반
        except Exception:
            _lm_r = None
        for name, data in d["npcs"].items():
            if (derive_npc_source(data, channel_id, name, lore_map=_lm_r) if _lm_r is not None
                    else derive_npc_source(data)) in ("lore", "manual"):
                # [2026-07-05 혼입 수리] 시트 원본은 유지하되 플레이 파생 필드는 세션 소속 → 제거.
                #   (관찰은 2026-09-16부터 페이지 play 절 — wiki_store.clear_play가 비운다)
                if isinstance(data, dict):
                    data.pop("appearances", None)
                kept_npcs[name] = data
        d["npcs"] = kept_npcs
        # [V10 Sprint 2-B] npcs 테이블 미러 (clear_session_npcs와 동일 보존 정책)
        try:
            import sqlite_store
            sqlite_store.delete_npcs_except_sources(channel_id, ("lore", "manual"))
        except Exception as _e:
            logging.debug(f"[V10] reset npc mirror skipped: {_e}")

    # [2026-09-14 W4] 내부 위키 수명 — 페이지는 npcs와 **같은 정책**이다.
    #   play 절·리비전 전량 + source ∉ {lore, manual} 페이지 삭제. lore 절·lore 페이지는 생존.
    #   (npcs 블록 밖에 둔다: 위키 페이지는 d["npcs"]가 비어 있어도 DB에 남아 있을 수 있다.)
    if getattr(config, "WIKI_PAGES", False):
        try:
            import wiki_store
            wiki_store.clear_play(channel_id)
        except Exception as _e:
            logging.debug(f"[Wiki] clear_play skipped: {_e}")

    # [2026-09-14 W4] 휘발 회상 흔적 — 프로세스 메모리라 DB 삭제가 못 건드린다.
    #   안 비우면 옛 세션 이름이 다음 턴 [Recall] trace 영수증에 섞인다.
    try:
        import fermentation
        fermentation.forget_channel_recall(channel_id)
    except Exception as _e:
        logging.debug(f"[Wiki] forget_channel_recall skipped: {_e}")

    # 5. Reset Participant Runtime State (vigor/composure/notebook — 로어 프로필은 유지)
    # [2026-08-18 Phase 2.5] 기력의 정본은 레지스트리로 옮겨갔다.
    #   (옛 자리는 계속 100으로 되돌린다: 레지스트리 off 채널의 폴백값이라 같이 리셋돼야 한다.)
    # [2026-09-06 P7 삭제] 여기 있던 레지스트리 값 비우기 한 줄은 **no-op** 였다 —
    #   2단(위)에서 world_state 를 DEFAULT 사본으로 통째 갈아엎은 뒤라 값 층 자체가 이미 없다.
    #   남겨두면 "값 층이 여기서 관리된다"는 거짓 신호가 되고, 선언/값 층 분리(P7) 뒤엔
    #   값 초기화의 정본이 custom_vars._initial_value 폴백이라는 사실을 가린다.
    for uid, pdata in d.get("participants", {}).items():
        mem = pdata.get("ai_memory", {})
        mem["vigor"] = {"value": 100, "last_delta": 0}
        mem["composure"] = {"value": 100, "last_delta": 0}
        mem.pop("mental", None)  # 레거시 제거
        mem["judgment_momentum"] = 0
        pdata["notebook"] = notebook_default()
        pdata["status_effects"] = []

    save_domain(channel_id, d)

# =========================================================
# 5b. CHRONICLE STORAGE
# =========================================================

def get_chronicles(channel_id: str) -> list:
    """저장된 연대기 목록 조회."""
    return get_domain(channel_id).get("chronicles", [])

# [2026-07-18 고아 삭제] get_latest_chronicle — 연대기 주입은 별도 경로(S9/chronicle) 생존, 이 편의 게터만 잉여 (dead_scan 참조0 확인, git 이력 복원 가능)

# =========================================================
# 6. UNE ADAPTER (Bridge)
# =========================================================



