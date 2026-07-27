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
    environmental_effects: List[Dict[str, Any]] = None
) -> str:
    """노드 속성 업데이트. 존재하는 필드만 갱신."""
    tree = _get_tree(channel_id)
    nodes = tree.get("nodes", {})
    node_id = _make_node_id(name)
    node = nodes.get(node_id)
    if not node:
        return "not_found"

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
    """노드 조회."""
    tree = _get_tree(channel_id)
    return tree.get("nodes", {}).get(_make_node_id(name))


def get_all_nodes(channel_id: str) -> Dict[str, Dict[str, Any]]:
    """모든 노드 반환."""
    tree = _get_tree(channel_id)
    return tree.get("nodes", {})


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
    from_id = _make_node_id(from_name)
    to_id = _make_node_id(to_name)

    if from_id not in nodes or to_id not in nodes:
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
    target_id = _make_node_id(location_name)

    if target_id not in nodes:
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
    """특정 위치의 NPC 목록."""
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

    node = get_node(channel_id, location_name)
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

