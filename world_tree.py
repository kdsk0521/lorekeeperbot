"""
Lorekeeper UNE - Hierarchical World Tree (v1.0)
LIBRA HierarchicalWorldManager에서 이식.

계층적 위치 관리: Region > Area > Room
각 노드: 속성(risk, atmosphere, tags), NPC 목록, 환경 효과, 연결 경로.

기존 domain_manager.current_location(단일 문자열)과 호환:
- current_location = "숲속 오두막" → world_tree에서 매칭하여 부가 정보 제공
- world_tree 미설정 시 기존 동작 그대로 유지

저장: domain_data["world_tree"] (dict 기반, 새 키 추가만으로 동작)
"""

import re
import logging
from typing import Any, Dict, List, Optional, Tuple
import domain_manager

logger = logging.getLogger("WorldTree")

# =========================================================
# Node Types
# =========================================================
NODE_TYPES = {"region", "area", "room"}

# Default properties per type
DEFAULT_PROPERTIES: Dict[str, Dict[str, Any]] = {
    "region": {"risk": "Low", "atmosphere": "", "tags": [], "traversal_time": "hours"},
    "area":   {"risk": "Low", "atmosphere": "", "tags": [], "traversal_time": "minutes"},
    "room":   {"risk": "Low", "atmosphere": "", "tags": [], "traversal_time": "instant"},
}

# Maximum tree size
MAX_NODES = 100


# =========================================================
# Core: Tree Storage
# =========================================================

def _get_tree(channel_id: str) -> Dict[str, Any]:
    """world_tree 데이터 로드."""
    d = domain_manager.get_domain(channel_id)
    return d.get("world_tree", {"nodes": {}, "root_ids": []})


def _save_tree(channel_id: str, tree: Dict[str, Any]) -> None:
    """world_tree 저장."""
    d = domain_manager.get_domain(channel_id)
    d["world_tree"] = tree
    domain_manager.save_domain(channel_id, d)


def _make_node_id(name: str) -> str:
    """노드 ID 생성 (소문자, 공백→언더스코어)."""
    return name.strip().lower().replace(" ", "_").replace("/", "_")


# =========================================================
# [2026-09-02 R0] 장소 이름 해상도 — 스펙 §3 / §6 R0
# =========================================================
# 병: `_make_node_id`는 strip/lower/치환뿐이라 **별칭도 유사 매칭도 정규화 사전도 없다**.
#     `서재` / `저택 서재` / `2층 서재`가 서로 다른 노드가 된다. 출석이 위치에 걸리는
#     순간 이 병의 대가가 바뀐다 — 노드가 갈리면 **장면 인원이 통째로 흩어진다**
#     (같은 방에 있는데 present 0명). 스펙 §3.
# 처방: NPC 쪽에서 이미 푼 층(`domain_manager._find_npc_key`: 정규화 → aliases →
#     **단일 후보일 때만** 토큰)을 장소로 그대로 복제한다. 새 패턴 발명 0.
# 근거: 스펙 §3 "장소에는 그 층이 통째로 없다" + §6 R0(선행 조건).
# ⚠ 조작면 최소주의 — 여기서 디스코드 명령은 만들지 않는다. 함수만 둔다.

def _loc_tokens(name: str) -> set:
    """노드 이름에서 뽑는 축약 호명 후보 토큰(공백·중점·슬래시 분리, 2자 이상).
    '저택 서재' → {저택, 서재} / '아르카디아·중앙구' → {아르카디아, 중앙구}
    (`domain_manager._short_tokens`의 장소판 — 성씨드롭 같은 인명 전용 규칙은 뺐다.)"""
    toks: set = set()
    for t in re.split(r"[\s·/]+", str(name or "").strip().lower()):
        t = t.strip()
        if len(t) >= 2:
            toks.add(t)
    return toks


def resolve_node_id(channel_id: str, name: str, allow_token: bool = True) -> Optional[str]:
    """장소 이름 → 노드 ID. 못 찾으면 None (`_find_npc_key`와 같은 규율).

    ① 정규화 정확 일치(`_make_node_id`) — 저장 키와 노드 name 양쪽을 본다.
    ② 노드 `aliases` 리스트 매칭 (병합이 남기는 흡수된 이름이 여기 쌓인다).
    ③ 축약 호명 토큰 매칭 — **후보가 정확히 1개일 때만.** 2개 이상이면 None:
       애매한 이름을 억지로 붙이면 오병합이라, 명시 alias/`merge_nodes`에 위임한다.

    ⚠ `allow_token=False`는 **구조 변경 경로(계층 생성)** 전용이다. [2026-09-02 검수 실측]
      기존에 평평한 `저택 서재`만 있을 때 path `["저택","저택 서재"]`가 오면 ③이 "저택"을
      **자식 노드로 해상**해 부모가 영영 안 생겼다(그 뒤 자기-부모 시도는 순환 가드에 막혀
      조용한 no-op). ③은 **호명**(사람이 "서재"라고 부를 때)용이지 Flash가 준 정본 경로를
      노드에 대응시키는 용도가 아니다. 경로 원소는 정확·별칭만 본다.
    """
    if not name:
        return None
    nodes = (_get_tree(channel_id).get("nodes", {}) or {})
    if not nodes:
        return None
    q = str(name).strip()
    if not q:
        return None

    # ① 정규화 정확 일치
    nid = _make_node_id(q)
    if nid in nodes:
        return nid
    for k, node in nodes.items():
        if _make_node_id(str(node.get("name", k))) == nid:
            return k

    # ② aliases (기존 노드 dict엔 이 필드가 없을 수 있다 — 반드시 .get 폴백)
    for k, node in nodes.items():
        for a in (node.get("aliases", []) or []):
            if isinstance(a, str) and _make_node_id(a) == nid:
                return k

    # ③ 단일 후보 토큰 (질의가 공백 없는 축약형일 때만 — _find_npc_key 4단계와 동일 게이트)
    if allow_token and " " not in q and len(q) >= 2:
        ql = q.lower()
        hits = [k for k, node in nodes.items()
                if ql in _loc_tokens(str(node.get("name", k)))]
        if len(hits) == 1:
            return hits[0]
    return None


def infer_parent_by_prefix(channel_id: str, name: str) -> str:
    """(b) 폴백 — 이름 접두로 부모 추론. '저택 서재'는 '저택' 노드가 있으면 그 밑.

    스펙 §2.5 ⓑ: Flash `location_path`가 원본이고 **이것은 path가 없을 때만** 쓰는 폴백.
    가장 긴 접두가 이긴다('저택'과 '저택 동관'이 둘 다 있으면 '저택 동관 서재'는 후자 밑).
    Returns: 부모 노드의 표시 이름(없으면 빈 문자열).
    """
    if not name:
        return ""
    q = str(name).strip()
    q_id = _make_node_id(q)
    best = ""
    for k, node in (_get_tree(channel_id).get("nodes", {}) or {}).items():
        nm = str(node.get("name", k)).strip()
        if not nm or _make_node_id(nm) == q_id:
            continue
        if q.startswith(nm) and len(q) > len(nm) and q[len(nm)] in " ·/":
            if q[len(nm):].strip() and len(nm) > len(best):
                best = nm
    return best


# =========================================================
# CRUD Operations
# =========================================================

def add_node(
    channel_id: str,
    name: str,
    node_type: str = "area",
    parent_id: str = "",
    properties: Dict[str, Any] = None,
    description: str = ""
) -> str:
    """
    위치 노드 추가.

    Args:
        name: 표시 이름
        node_type: region / area / room
        parent_id: 부모 노드 ID (빈 문자열 = 루트)
        properties: {risk, atmosphere, tags, traversal_time, ...}
        description: 설명

    Returns: "created", "exists", "capped", "invalid_parent"
    """
    tree = _get_tree(channel_id)
    nodes = tree.setdefault("nodes", {})
    root_ids = tree.setdefault("root_ids", [])

    node_id = _make_node_id(name)
    if node_id in nodes:
        return "exists"

    if len(nodes) >= MAX_NODES:
        return "capped"

    if node_type not in NODE_TYPES:
        node_type = "area"

    # Validate parent
    if parent_id:
        parent_id = _make_node_id(parent_id)
        if parent_id not in nodes:
            return "invalid_parent"

    # Build node
    defaults = DEFAULT_PROPERTIES.get(node_type, DEFAULT_PROPERTIES["area"])
    props = {**defaults, **(properties or {})}

    node: Dict[str, Any] = {
        "id": node_id,
        "name": name,
        "type": node_type,
        "parent_id": parent_id,
        "children": [],
        "properties": props,
        "description": description,
        "npcs_present": [],
        # [2026-09-02 R0] 별칭 — resolve_node_id ②단계가 소비, merge_nodes가 채운다.
        "aliases": [],
        "connections": [],  # [{target_id, direction, description}]
        "environmental_effects": [],  # [{tag, intensity, description}]
    }

    nodes[node_id] = node

    # Link to parent or add as root
    if parent_id and parent_id in nodes:
        parent = nodes[parent_id]
        if node_id not in parent.get("children", []):
            parent.setdefault("children", []).append(node_id)
    else:
        if node_id not in root_ids:
            root_ids.append(node_id)

    _save_tree(channel_id, tree)
    logger.info("[WorldTree] Created %s '%s' (parent=%s)", node_type, name, parent_id or "root")
    return "created"


def update_node(
    channel_id: str,
    name: str,
    properties: Dict[str, Any] = None,
    description: str = None,
    environmental_effects: List[Dict[str, Any]] = None,
    parent_id: str = None
) -> str:
    """노드 속성 업데이트. 존재하는 필드만 갱신.

    [2026-09-02 R1] `parent_id` 인자 신설(선택).
    병: 자동 생성(`orchestration` Tier 2)이 parent 미지정이라 **전부 루트**로 앉았고,
        이미 그렇게 앉은 노드를 나중에 부모 밑으로 옮길 수단이 없었다 — 스펙 §4.
    처방: 이미 있는 노드가 루트인데 path상 부모가 명확해지면 여기서 붙인다(평평 노드 승격).
    None = 손대지 않음, "" = 루트로 내림. 순환(자기 조상 아래로 들어감)은 거부.
    """
    tree = _get_tree(channel_id)
    nodes = tree.get("nodes", {})
    root_ids = tree.setdefault("root_ids", [])
    node_id = _make_node_id(name)
    node = nodes.get(node_id)
    if not node:
        return "not_found"

    if parent_id is not None:
        new_pid = resolve_node_id(channel_id, parent_id) if str(parent_id).strip() else ""
        if str(parent_id).strip() and not new_pid:
            return "invalid_parent"
        if new_pid == node_id:
            return "invalid_parent"
        # 순환 가드 — 새 부모의 조상 사슬에 자신이 있으면 트리가 끊어진다
        _cur, _seen = new_pid, set()
        while _cur and _cur in nodes and _cur not in _seen:
            if _cur == node_id:
                return "invalid_parent"
            _seen.add(_cur)
            _cur = nodes[_cur].get("parent_id", "")
        old_pid = node.get("parent_id", "")
        if old_pid != new_pid:
            if old_pid and old_pid in nodes:
                _oc = nodes[old_pid].get("children", [])
                if node_id in _oc:
                    _oc.remove(node_id)
            node["parent_id"] = new_pid
            if new_pid:
                if node_id in root_ids:
                    root_ids.remove(node_id)
                _nc = nodes[new_pid].setdefault("children", [])
                if node_id not in _nc:
                    _nc.append(node_id)
            elif node_id not in root_ids:
                root_ids.append(node_id)

    if properties:
        node["properties"] = {**node.get("properties", {}), **properties}
    if description is not None:
        node["description"] = description
    if environmental_effects is not None:
        node["environmental_effects"] = environmental_effects

    _save_tree(channel_id, tree)
    return "updated"


def remove_node(channel_id: str, name: str, recursive: bool = False) -> str:
    """노드 제거. recursive=True면 자식도 제거."""
    tree = _get_tree(channel_id)
    nodes = tree.get("nodes", {})
    root_ids = tree.get("root_ids", [])
    node_id = _make_node_id(name)

    if node_id not in nodes:
        return "not_found"

    def _remove(nid: str):
        n = nodes.pop(nid, None)
        if not n:
            return
        if recursive:
            for child_id in n.get("children", []):
                _remove(child_id)
        if nid in root_ids:
            root_ids.remove(nid)

    # Unlink from parent
    node = nodes[node_id]
    parent_id = node.get("parent_id", "")
    if parent_id and parent_id in nodes:
        parent = nodes[parent_id]
        children = parent.get("children", [])
        if node_id in children:
            children.remove(node_id)

    _remove(node_id)
    _save_tree(channel_id, tree)
    return "removed"


def get_node(channel_id: str, name: str) -> Optional[Dict[str, Any]]:
    """노드 조회. [2026-09-02 R0] 이름→ID는 resolve_node_id로 통일(별칭·축약 호명 흡수)."""
    node_id = resolve_node_id(channel_id, name)
    if not node_id:
        return None
    return _get_tree(channel_id).get("nodes", {}).get(node_id)


def get_all_nodes(channel_id: str) -> Dict[str, Dict[str, Any]]:
    """모든 노드 반환."""
    tree = _get_tree(channel_id)
    return tree.get("nodes", {})


def merge_nodes(channel_id: str, dup_name: str, canon_name: str) -> str:
    """중복 장소 노드를 본체로 흡수. [2026-09-02 R0] 스펙 §6 R0 "평평 노드 병합".

    병: 이름 해상도가 없던 시절에 `서재`/`저택 서재`/`2층 서재`가 각자 노드로 앉았다.
        해상도(resolve_node_id)를 붙여도 **이미 갈라진 노드**는 저절로 안 합쳐진다 —
        인원이 두 노드에 나뉘어 있으면 같은 방인데 present가 쪼개진다(스펙 §3).
    처방: `domain_manager.merge_npc`의 장소판. dup의 인원·자식·연결을 canon으로 옮기고
        dup 이름을 canon의 aliases에 넣은 뒤(재발 방지 — resolve ②단계가 소비) dup을 지운다.
    ⚠ 조작면 최소주의(ⓖ): 여기에 디스코드 명령을 붙이지 않는다. 함수만 둔다.

    Returns: "merged" / "dup_not_found" / "canon_not_found" / "same_node"
    """
    tree = _get_tree(channel_id)
    nodes = tree.get("nodes", {})
    root_ids = tree.setdefault("root_ids", [])

    dup_id = resolve_node_id(channel_id, dup_name)
    canon_id = resolve_node_id(channel_id, canon_name)
    if not dup_id:
        return "dup_not_found"
    if not canon_id:
        return "canon_not_found"
    if dup_id == canon_id:
        return "same_node"

    dup = nodes[dup_id]
    canon = nodes[canon_id]

    # 0) dup이 canon의 부모였다면 canon을 조부모(또는 루트)로 승계 — 아니면 트리가 끊어진다
    if canon.get("parent_id", "") == dup_id:
        _gp = dup.get("parent_id", "")
        canon["parent_id"] = _gp if _gp in nodes else ""
        if canon["parent_id"]:
            _gpc = nodes[canon["parent_id"]].setdefault("children", [])
            if canon_id not in _gpc:
                _gpc.append(canon_id)
        elif canon_id not in root_ids:
            root_ids.append(canon_id)

    # 1) 인원 이동 (같은 방이었으므로 합집합)
    c_npcs = canon.setdefault("npcs_present", [])
    for n in (dup.get("npcs_present", []) or []):
        if n not in c_npcs:
            c_npcs.append(n)

    # 2) 자식 이동
    c_children = canon.setdefault("children", [])
    for cid in list(dup.get("children", []) or []):
        if cid == canon_id or cid not in nodes:
            continue
        nodes[cid]["parent_id"] = canon_id
        if cid not in c_children:
            c_children.append(cid)

    # 3) 연결 이동 — dup→X를 canon→X로, 그리고 남이 dup을 가리키던 간선을 canon으로 재지향
    c_conns = canon.setdefault("connections", [])
    for conn in (dup.get("connections", []) or []):
        t = conn.get("target_id", "")
        if not t or t in (canon_id, dup_id):
            continue
        if not any(c.get("target_id") == t for c in c_conns):
            c_conns.append(dict(conn))
    for nid, node in nodes.items():
        if nid == dup_id:
            continue
        new_conns: List[Dict[str, Any]] = []
        for c in (node.get("connections", []) or []):
            if c.get("target_id") == dup_id:
                c = {**c, "target_id": canon_id}
            tgt = c.get("target_id", "")
            if not tgt or tgt == nid:
                continue  # self-loop 제거
            if any(x.get("target_id") == tgt for x in new_conns):
                continue  # 중복 제거
            new_conns.append(c)
        node["connections"] = new_conns

    # 4) 흡수된 이름을 canon의 별칭으로 (재발 방지 — 다음부터는 resolve ②가 잡는다)
    aliases = canon.setdefault("aliases", [])
    for cand in [dup.get("name", dup_id)] + list(dup.get("aliases", []) or []):
        cand = str(cand or "").strip()
        if not cand or _make_node_id(cand) == canon_id:
            continue
        if not any(_make_node_id(a) == _make_node_id(cand) for a in aliases if isinstance(a, str)):
            aliases.append(cand)

    # 5) dup 제거 (부모/루트 링크 해제)
    _dp = dup.get("parent_id", "")
    if _dp and _dp in nodes:
        _dpc = nodes[_dp].get("children", [])
        if dup_id in _dpc:
            _dpc.remove(dup_id)
    if dup_id in root_ids:
        root_ids.remove(dup_id)
    nodes.pop(dup_id, None)
    if canon_id in c_children:
        c_children.remove(canon_id)  # 자기 자식 방지

    _save_tree(channel_id, tree)
    logger.info("[WorldTree] merged '%s' → '%s'", dup_name, canon.get("name", canon_id))
    return "merged"


# =========================================================
# Connections (passage/routes between locations)
# =========================================================

def add_connection(
    channel_id: str,
    from_name: str, to_name: str,
    direction: str = "",
    description: str = "",
    bidirectional: bool = True
) -> str:
    """두 노드 사이의 연결 추가."""
    tree = _get_tree(channel_id)
    nodes = tree.get("nodes", {})
    # [2026-09-02 R0] 이름→ID를 resolve_node_id로 통일 — 별칭/축약 호명으로 들어온 간선이
    #   조용히 "node_not_found"로 떨어지면 §2.4의 1단(근접)이 영원히 안 자란다.
    from_id = resolve_node_id(channel_id, from_name)
    to_id = resolve_node_id(channel_id, to_name)

    if not from_id or not to_id or from_id not in nodes or to_id not in nodes:
        return "node_not_found"

    def _add_conn(source_id: str, target_id: str, dir_label: str):
        node = nodes[source_id]
        conns = node.setdefault("connections", [])
        # Prevent duplicates
        if not any(c.get("target_id") == target_id for c in conns):
            conns.append({
                "target_id": target_id,
                "direction": dir_label,
                "description": description,
            })

    _add_conn(from_id, to_id, direction)
    if bidirectional:
        reverse_dir = {"north": "south", "south": "north",
                       "east": "west", "west": "east",
                       "up": "down", "down": "up"}.get(direction.lower(), direction)
        _add_conn(to_id, from_id, reverse_dir)

    _save_tree(channel_id, tree)
    return "connected"


# =========================================================
# NPC Presence Tracking
# =========================================================

def set_npc_location(channel_id: str, npc_name: str, location_name: str) -> str:
    """NPC를 특정 위치에 배치."""
    tree = _get_tree(channel_id)
    nodes = tree.get("nodes", {})
    # [2026-09-02 R0] 이름→ID 통일. 여기서 노드가 갈리면 같은 방 인원이 두 노드로 흩어진다(§3).
    target_id = resolve_node_id(channel_id, location_name)

    if not target_id or target_id not in nodes:
        return "location_not_found"

    # Remove from previous location
    for nid, node in nodes.items():
        npcs = node.get("npcs_present", [])
        if npc_name in npcs:
            npcs.remove(npc_name)

    # Add to new location
    target = nodes[target_id]
    npcs = target.setdefault("npcs_present", [])
    if npc_name not in npcs:
        npcs.append(npc_name)

    _save_tree(channel_id, tree)
    return "placed"


def remove_npc_presence(channel_id: str, npc_name: str) -> bool:
    """NPC를 모든 위치에서 제거 (개명/퇴장 시). [2026-07-18 identity reveal 배선용]"""
    tree = _get_tree(channel_id)
    removed = False
    for nid, node in tree.get("nodes", {}).items():
        npcs = node.get("npcs_present", [])
        if npc_name in npcs:
            npcs.remove(npc_name)
            removed = True
    if removed:
        _save_tree(channel_id, tree)
    return removed


def get_npcs_at_location(channel_id: str, location_name: str) -> List[str]:
    """특정 위치의 NPC 목록. [2026-09-02 R0] 이름 해상도는 get_node(→resolve_node_id) 경유."""
    node = get_node(channel_id, location_name)
    if not node:
        return []
    return node.get("npcs_present", [])


def get_npc_location(channel_id: str, npc_name: str) -> Optional[str]:
    """NPC의 현재 위치 반환."""
    tree = _get_tree(channel_id)
    for nid, node in tree.get("nodes", {}).items():
        if npc_name in node.get("npcs_present", []):
            return node.get("name", nid)
    return None


# =========================================================
# Query: Path & Hierarchy
# =========================================================

def get_ancestors(channel_id: str, name: str) -> List[str]:
    """노드의 조상 경로 (자신 포함, 루트부터 순서)."""
    tree = _get_tree(channel_id)
    nodes = tree.get("nodes", {})
    node_id = _make_node_id(name)

    path = []
    current = node_id
    visited = set()
    while current and current in nodes and current not in visited:
        visited.add(current)
        path.append(nodes[current].get("name", current))
        current = nodes[current].get("parent_id", "")

    path.reverse()
    return path


def get_children(channel_id: str, name: str) -> List[Dict[str, Any]]:
    """직속 자식 노드 목록."""
    tree = _get_tree(channel_id)
    nodes = tree.get("nodes", {})
    node_id = _make_node_id(name)
    node = nodes.get(node_id)
    if not node:
        return []

    children = []
    for child_id in node.get("children", []):
        child = nodes.get(child_id)
        if child:
            children.append(child)
    return children


def get_nearby_locations(channel_id: str, name: str) -> List[Dict[str, Any]]:
    """연결된 이웃 위치 + 같은 부모의 형제 노드."""
    tree = _get_tree(channel_id)
    nodes = tree.get("nodes", {})
    node_id = _make_node_id(name)
    node = nodes.get(node_id)
    if not node:
        return []

    nearby = []

    # Connections
    for conn in node.get("connections", []):
        target = nodes.get(conn.get("target_id", ""))
        if target:
            nearby.append({
                "name": target.get("name", ""),
                "type": target.get("type", "area"),
                "direction": conn.get("direction", ""),
                "relation": "connected",
            })

    # Siblings (same parent)
    parent_id = node.get("parent_id", "")
    if parent_id and parent_id in nodes:
        parent = nodes[parent_id]
        for sibling_id in parent.get("children", []):
            if sibling_id != node_id:
                sibling = nodes.get(sibling_id)
                if sibling:
                    nearby.append({
                        "name": sibling.get("name", ""),
                        "type": sibling.get("type", "area"),
                        "direction": "",
                        "relation": "sibling",
                    })

    return nearby


# =========================================================
# Context Builders (for prompt/response injection)
# =========================================================

def resolve_location_context(channel_id: str, location_name: str = "") -> Dict[str, Any]:
    """
    현재 위치의 풍부한 컨텍스트 생성.
    location_name이 없으면 domain_manager.current_location 사용.

    Returns:
        {
            "name": str,
            "path": [ancestor names...],
            "properties": {risk, atmosphere, tags, ...},
            "description": str,
            "npcs_here": [str],
            "nearby": [{name, type, direction, relation}],
            "environmental_effects": [{tag, intensity, description}],
            "connections": [{target_id, direction, description}],
        }
    """
    if not location_name:
        location_name = domain_manager.get_current_location(channel_id)

    # [2026-09-02 R0] get_node는 별칭/축약 호명을 해상하지만 아래 get_ancestors·
    #   get_nearby_locations는 여전히 _make_node_id로 조회한다. 해상된 노드의 **표준 이름**으로
    #   갈아끼우지 않으면 노드는 잡히는데 path·nearby만 빈손이 되는 반쪽 컨텍스트가 나간다.
    node = get_node(channel_id, location_name)
    if node:
        location_name = str(node.get("name", location_name))
    if not node:
        # world_tree에 없으면 기본 정보만 반환 (하위호환)
        return {
            "name": location_name,
            "path": [location_name],
            "properties": {"risk": domain_manager.get_current_risk(channel_id),
                           "atmosphere": "", "tags": []},
            "description": "",
            "npcs_here": [],
            "nearby": [],
            "environmental_effects": [],
            "connections": [],
        }

    return {
        "name": node.get("name", location_name),
        "path": get_ancestors(channel_id, location_name),
        "properties": node.get("properties", {}),
        "description": node.get("description", ""),
        "npcs_here": node.get("npcs_present", []),
        "nearby": get_nearby_locations(channel_id, location_name),
        "environmental_effects": node.get("environmental_effects", []),
        "connections": node.get("connections", []),
    }


def build_location_context_text(channel_id: str, location_name: str = "") -> str:
    """프롬프트 삽입용 위치 컨텍스트 텍스트."""
    ctx = resolve_location_context(channel_id, location_name)
    if not ctx.get("name"):
        return ""

    parts = []

    # Path
    path = ctx.get("path", [])
    if len(path) > 1:
        parts.append(f"[Location: {' > '.join(path)}]")
    else:
        parts.append(f"[Location: {ctx['name']}]")

    # Properties
    props = ctx.get("properties", {})
    risk = props.get("risk", "")
    atmo = props.get("atmosphere", "")
    tags = props.get("tags", [])
    prop_parts = []
    if risk and risk != "Low":
        prop_parts.append(f"Risk:{risk}")
    if atmo:
        prop_parts.append(f"Atmosphere:{atmo}")
    if tags:
        prop_parts.append(f"Tags:{','.join(tags[:5])}")
    if prop_parts:
        parts.append(" | ".join(prop_parts))

    # Description (truncated)
    desc = ctx.get("description", "")
    if desc:
        parts.append(desc[:120])

    # NPCs here
    npcs = ctx.get("npcs_here", [])
    if npcs:
        parts.append(f"NPCs present: {', '.join(npcs[:8])}")

    # Environmental effects
    effects = ctx.get("environmental_effects", [])
    if effects:
        eff_strs = [f"{e.get('tag','')}({e.get('intensity','')})" for e in effects[:3]]
        parts.append(f"Effects: {', '.join(eff_strs)}")

    # Nearby
    nearby = ctx.get("nearby", [])
    if nearby:
        near_strs = []
        for n in nearby[:4]:
            dir_str = f"({n['direction']})" if n.get("direction") else ""
            near_strs.append(f"{n['name']}{dir_str}")
        parts.append(f"Nearby: {', '.join(near_strs)}")

    return "\n".join(parts)


# =========================================================
# Batch Import (from Lore/Flash)
# =========================================================

def import_locations_from_lore(
    channel_id: str,
    locations: List[Dict[str, Any]]
) -> int:
    """
    로어 분석 결과로 위치 트리 일괄 구축.

    Expected format:
    [
        {"name": "어둠의 숲", "type": "region", "description": "...", "risk": "High", "atmosphere": "ominous"},
        {"name": "오두막", "type": "room", "parent": "어둠의 숲", "description": "..."},
        ...
    ]

    Returns: number of nodes created.
    """
    count = 0
    for loc in locations:
        if not isinstance(loc, dict):
            continue
        name = loc.get("name", "").strip()
        if not name:
            continue

        node_type = loc.get("type", "area")
        parent = loc.get("parent", "")
        description = loc.get("description", "")
        properties = {}
        if loc.get("risk"):
            properties["risk"] = loc["risk"]
        if loc.get("atmosphere"):
            properties["atmosphere"] = loc["atmosphere"]
        if loc.get("tags"):
            properties["tags"] = loc["tags"] if isinstance(loc["tags"], list) else [loc["tags"]]

        result = add_node(channel_id, name, node_type, parent, properties, description)
        if result == "created":
            count += 1

    # Process connections if provided
    for loc in locations:
        if not isinstance(loc, dict):
            continue
        name = loc.get("name", "").strip()
        connections = loc.get("connections", [])
        for conn in connections:
            if isinstance(conn, str):
                add_connection(channel_id, name, conn)
            elif isinstance(conn, dict):
                add_connection(
                    channel_id, name,
                    conn.get("target", ""),
                    conn.get("direction", ""),
                    conn.get("description", "")
                )

    logger.info("[WorldTree] Imported %d locations from lore", count)
    return count

