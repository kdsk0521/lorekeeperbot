"""
Lorekeeper UNE - Entity Relation Network (v1.0)
NPC↔NPC 관계 그래프 관리. 기존 PC↔NPC 태도 시스템(npc_manager)과 공존.

LIBRA EntityManager에서 이식:
- NPC 간 관계 추적 (alliance, rivalry, fear, respect, etc.)
- 관계 강도 + 방향성 (A→B ≠ B→A)
- 관계 변화 이력 (delta-only)
- 서사용 관계 컨텍스트 생성

저장: domain_data["entity_relations"] (기존 dict 기반, 스키마 변경 불필요)
"""

import logging
from typing import Any, Dict, List, Optional, Tuple
import domain_manager

logger = logging.getLogger("EntityRelations")

# =========================================================
# Relation Types & Scoring
# =========================================================
RELATION_TYPES = {
    "alliance":  {"label": "ally",      "emoji": "🤝", "valence": +1},
    "rivalry":   {"label": "rival",     "emoji": "⚔️",  "valence": -1},
    "fear":      {"label": "fear",      "emoji": "😨", "valence": -1},
    "respect":   {"label": "respect",   "emoji": "🙏", "valence": +1},
    "distrust":  {"label": "distrust",  "emoji": "🔍", "valence": -1},
    "affection": {"label": "affection", "emoji": "💗", "valence": +1},
    "debt":      {"label": "debt",      "emoji": "⚖️",  "valence":  0},
    "mentor":    {"label": "mentor",    "emoji": "📖", "valence": +1},
    "grudge":    {"label": "grudge",    "emoji": "🔥", "valence": -1},
    "neutral":   {"label": "neutral",   "emoji": "⚪", "valence":  0},
}

# Maximum relations per channel (prevent unbounded growth)
MAX_RELATIONS = 50
# History cap per relation edge
MAX_HISTORY_PER_EDGE = 10

# Intensity thresholds for narrative importance
INTENSITY_THRESHOLDS = {
    "whisper": (0.0, 0.3),   # Background, barely noticeable
    "steady":  (0.3, 0.6),   # Notable, mentioned when relevant
    "strong":  (0.6, 0.8),   # Drives behavior, affects decisions
    "burning": (0.8, 1.01),  # Defines character, impossible to ignore
}


# =========================================================
# Core CRUD
# =========================================================

def _get_relations_store(channel_id: str) -> Dict[str, Any]:
    """domain_data에서 entity_relations 가져오기."""
    d = domain_manager.get_domain(channel_id)
    return d.get("entity_relations", {})


def _save_relations_store(channel_id: str, store: Dict[str, Any]) -> None:
    """entity_relations 저장."""
    d = domain_manager.get_domain(channel_id)
    d["entity_relations"] = store
    domain_manager.save_domain(channel_id, d)


def _edge_key(source: str, target: str) -> str:
    """방향성 엣지 키 생성 (A→B)."""
    return f"{source}→{target}"


def get_relation(channel_id: str, source: str, target: str) -> Optional[Dict[str, Any]]:
    """특정 방향의 관계 조회."""
    store = _get_relations_store(channel_id)
    edges = store.get("edges", {})
    return edges.get(_edge_key(source, target))


def get_all_relations(channel_id: str) -> Dict[str, Dict[str, Any]]:
    """모든 관계 엣지 반환."""
    store = _get_relations_store(channel_id)
    return store.get("edges", {})


def set_relation(
    channel_id: str,
    source: str,
    target: str,
    relation_type: str,
    intensity: float = 0.5,
    reason: str = "",
    current_turn: int = 0,
    bidirectional: bool = False
) -> str:
    """
    관계 설정 또는 업데이트.

    Args:
        source: 관계의 주체 NPC
        target: 관계의 대상 NPC
        relation_type: RELATION_TYPES 키 중 하나
        intensity: 0.0 ~ 1.0
        reason: 변경 사유
        current_turn: 현재 턴
        bidirectional: True면 역방향도 동일하게 설정

    Returns:
        "created", "updated", "capped"
    """
    relation_type = relation_type.lower().strip()
    if relation_type not in RELATION_TYPES:
        relation_type = "neutral"

    intensity = max(0.0, min(1.0, float(intensity)))

    store = _get_relations_store(channel_id)
    if "edges" not in store:
        store["edges"] = {}

    edges = store["edges"]
    key = _edge_key(source, target)

    # Cap check
    if key not in edges and len(edges) >= MAX_RELATIONS:
        logger.warning("[EntityRelations] Edge cap reached (%d), skipping %s", MAX_RELATIONS, key)
        return "capped"

    existing = edges.get(key)
    result = "created" if not existing else "updated"

    # Build edge
    edge: Dict[str, Any] = existing or {
        "source": source,
        "target": target,
        "history": [],
        "created_turn": current_turn,
    }

    # Record history if type or intensity changed
    if existing:
        old_type = existing.get("type", "neutral")
        old_intensity = existing.get("intensity", 0.5)
        if old_type != relation_type or abs(old_intensity - intensity) > 0.1:
            edge.setdefault("history", []).append({
                "turn": current_turn,
                "old_type": old_type,
                "old_intensity": round(old_intensity, 2),
                "new_type": relation_type,
                "new_intensity": round(intensity, 2),
                "reason": reason,
            })
            # Cap history
            if len(edge["history"]) > MAX_HISTORY_PER_EDGE:
                edge["history"] = edge["history"][-MAX_HISTORY_PER_EDGE:]

    edge["type"] = relation_type
    edge["intensity"] = round(intensity, 2)
    edge["reason"] = reason
    edge["last_turn"] = current_turn

    edges[key] = edge
    store["edges"] = edges

    # Bidirectional
    if bidirectional:
        reverse_key = _edge_key(target, source)
        if reverse_key not in edges and len(edges) < MAX_RELATIONS:
            reverse_edge = {
                "source": target,
                "target": source,
                "type": relation_type,
                "intensity": round(intensity, 2),
                "reason": reason,
                "last_turn": current_turn,
                "created_turn": current_turn,
                "history": [],
            }
            edges[reverse_key] = reverse_edge

    _save_relations_store(channel_id, store)
    logger.info("[EntityRelations] %s: %s→%s [%s %.2f] %s",
                result, source, target, relation_type, intensity, reason[:50])
    return result


def remove_relation(channel_id: str, source: str, target: str) -> bool:
    """관계 엣지 제거."""
    store = _get_relations_store(channel_id)
    edges = store.get("edges", {})
    key = _edge_key(source, target)
    if key in edges:
        del edges[key]
        _save_relations_store(channel_id, store)
        return True
    return False


def adjust_intensity(
    channel_id: str,
    source: str, target: str,
    delta: float,
    reason: str = "",
    current_turn: int = 0
) -> Optional[float]:
    """기존 관계의 intensity를 delta만큼 조정. 관계가 없으면 None 반환."""
    store = _get_relations_store(channel_id)
    edges = store.get("edges", {})
    key = _edge_key(source, target)
    edge = edges.get(key)
    if not edge:
        return None

    old_intensity = edge.get("intensity", 0.5)
    new_intensity = max(0.0, min(1.0, old_intensity + delta))
    edge["intensity"] = round(new_intensity, 2)
    edge["last_turn"] = current_turn

    if abs(delta) >= 0.1:
        edge.setdefault("history", []).append({
            "turn": current_turn,
            "old_type": edge["type"],
            "old_intensity": round(old_intensity, 2),
            "new_type": edge["type"],
            "new_intensity": round(new_intensity, 2),
            "reason": reason,
        })
        if len(edge["history"]) > MAX_HISTORY_PER_EDGE:
            edge["history"] = edge["history"][-MAX_HISTORY_PER_EDGE:]

    _save_relations_store(channel_id, store)
    return new_intensity


# =========================================================
# Query Helpers
# =========================================================

def get_relations_of(channel_id: str, npc_name: str) -> Dict[str, List[Dict[str, Any]]]:
    """특정 NPC의 모든 관계 (outgoing + incoming)."""
    edges = get_all_relations(channel_id)
    outgoing = []
    incoming = []
    for key, edge in edges.items():
        if edge.get("source") == npc_name:
            outgoing.append(edge)
        elif edge.get("target") == npc_name:
            incoming.append(edge)
    return {"outgoing": outgoing, "incoming": incoming}


def get_strongest_relations(channel_id: str, top_n: int = 5) -> List[Dict[str, Any]]:
    """intensity 기준 상위 N개 관계."""
    edges = get_all_relations(channel_id)
    sorted_edges = sorted(edges.values(), key=lambda e: e.get("intensity", 0), reverse=True)
    return sorted_edges[:top_n]


def get_conflict_pairs(channel_id: str) -> List[Tuple[str, str, Dict[str, Any]]]:
    """적대적 관계 쌍 (rivalry, grudge, fear, distrust 중 intensity > 0.5)."""
    edges = get_all_relations(channel_id)
    conflicts = []
    negative_types = {"rivalry", "grudge", "fear", "distrust"}
    for key, edge in edges.items():
        if edge.get("type") in negative_types and edge.get("intensity", 0) > 0.5:
            conflicts.append((edge["source"], edge["target"], edge))
    return conflicts


def get_alliance_clusters(channel_id: str) -> List[List[str]]:
    """동맹 관계로 연결된 NPC 클러스터 (간단한 Union-Find)."""
    edges = get_all_relations(channel_id)
    alliance_types = {"alliance", "affection", "respect", "mentor"}

    # Build adjacency
    adj: Dict[str, set] = {}
    for key, edge in edges.items():
        if edge.get("type") in alliance_types and edge.get("intensity", 0) > 0.4:
            src = edge["source"]
            tgt = edge["target"]
            adj.setdefault(src, set()).add(tgt)
            adj.setdefault(tgt, set()).add(src)

    # BFS clustering
    visited = set()
    clusters = []
    for node in adj:
        if node in visited:
            continue
        cluster = []
        queue = [node]
        while queue:
            n = queue.pop(0)
            if n in visited:
                continue
            visited.add(n)
            cluster.append(n)
            for neighbor in adj.get(n, set()):
                if neighbor not in visited:
                    queue.append(neighbor)
        if len(cluster) >= 2:
            clusters.append(sorted(cluster))

    return clusters


# =========================================================
# Context Builders (for prompt injection)
# =========================================================

def _intensity_label(intensity: float) -> str:
    """Intensity를 서사적 레이블로 변환."""
    for label, (lo, hi) in INTENSITY_THRESHOLDS.items():
        if lo <= intensity < hi:
            return label
    return "steady"


def build_relation_context(channel_id: str, relevant_npcs: List[str] = None, max_lines: int = 8) -> str:
    """
    프롬프트에 삽입할 NPC 관계 컨텍스트 생성.
    relevant_npcs가 주어지면 해당 NPC 관련 관계만 필터링.

    DC-06 배선: 단순 랭킹 리스트 위에 구조적 강조(갈등 페어 + 동맹 클러스터)를
    먼저 배치하여 모델이 관계망을 "봐야 할 축"을 먼저 인식하도록 한다.
    """
    edges = get_all_relations(channel_id)
    if not edges:
        return ""

    # Filter by relevance
    filtered = []
    for key, edge in edges.items():
        if relevant_npcs:
            if edge.get("source") not in relevant_npcs and edge.get("target") not in relevant_npcs:
                continue
        filtered.append(edge)

    if not filtered:
        return ""

    # Sort by intensity (strongest first)
    filtered.sort(key=lambda e: e.get("intensity", 0), reverse=True)
    ranked = filtered[:max_lines]

    blocks: List[str] = []

    # ── 구조적 강조 1: 갈등 페어 (rivalry/grudge/fear/distrust, intensity > 0.5) ──
    # get_conflict_pairs를 직접 쓰면 relevant_npcs 필터링이 안 되므로
    # filtered 리스트에서 추린다.
    negative_types = {"rivalry", "grudge", "fear", "distrust"}
    conflict_edges = [
        e for e in filtered
        if e.get("type") in negative_types and e.get("intensity", 0) > 0.5
    ]
    if conflict_edges:
        conflict_edges.sort(key=lambda e: e.get("intensity", 0), reverse=True)
        conflict_parts = []
        for edge in conflict_edges[:3]:  # 최대 3쌍
            src = edge.get("source", "?")
            tgt = edge.get("target", "?")
            rtype = edge.get("type", "neutral")
            rinfo = RELATION_TYPES.get(rtype, RELATION_TYPES["neutral"])
            conflict_parts.append(f"{src} {rinfo['emoji']} {tgt}")
        blocks.append(f"[Conflicts]: {' | '.join(conflict_parts)}")

    # ── 구조적 강조 2: 동맹 클러스터 (alliance/affection/respect/mentor ≥ 0.4) ──
    alliance_types = {"alliance", "affection", "respect", "mentor"}
    adj: Dict[str, set] = {}
    for edge in filtered:
        if edge.get("type") in alliance_types and edge.get("intensity", 0) >= 0.4:
            src = edge.get("source", "")
            tgt = edge.get("target", "")
            if src and tgt:
                adj.setdefault(src, set()).add(tgt)
                adj.setdefault(tgt, set()).add(src)

    if adj:
        visited = set()
        clusters: List[List[str]] = []
        for node in adj:
            if node in visited:
                continue
            cluster = []
            queue = [node]
            while queue:
                n = queue.pop(0)
                if n in visited:
                    continue
                visited.add(n)
                cluster.append(n)
                for neighbor in adj.get(n, set()):
                    if neighbor not in visited:
                        queue.append(neighbor)
            if len(cluster) >= 2:
                clusters.append(sorted(cluster))
        if clusters:
            cluster_parts = [" & ".join(c) for c in clusters[:2]]  # 최대 2 클러스터
            blocks.append(f"[Alliances]: {' || '.join(cluster_parts)}")

    # ── 랭킹 리스트 (기존 동작) ──
    blocks.append("[NPC RELATIONSHIPS]")
    for edge in ranked:
        src = edge.get("source", "?")
        tgt = edge.get("target", "?")
        rtype = edge.get("type", "neutral")
        intensity = edge.get("intensity", 0.5)
        rinfo = RELATION_TYPES.get(rtype, RELATION_TYPES["neutral"])
        label = _intensity_label(intensity)
        reason = edge.get("reason", "")

        line = f"- {src} {rinfo['emoji']}{rinfo['label']}→ {tgt} ({label})"
        if reason:
            line += f" [{reason[:30]}]"
        blocks.append(line)

    return "\n".join(blocks)


def build_npc_relation_summary(channel_id: str, npc_name: str) -> str:
    """특정 NPC의 관계망 요약 (NPC 프로필에 삽입)."""
    rels = get_relations_of(channel_id, npc_name)
    if not rels["outgoing"] and not rels["incoming"]:
        return ""

    parts = []
    for edge in rels["outgoing"]:
        tgt = edge.get("target", "?")
        rtype = edge.get("type", "neutral")
        rinfo = RELATION_TYPES.get(rtype, RELATION_TYPES["neutral"])
        intensity = edge.get("intensity", 0.5)
        label = _intensity_label(intensity)
        parts.append(f"→{tgt}({rinfo['label']},{label})")

    for edge in rels["incoming"]:
        src = edge.get("source", "?")
        rtype = edge.get("type", "neutral")
        rinfo = RELATION_TYPES.get(rtype, RELATION_TYPES["neutral"])
        intensity = edge.get("intensity", 0.5)
        label = _intensity_label(intensity)
        parts.append(f"←{src}({rinfo['label']},{label})")

    if not parts:
        return ""
    return f"[Relations: {', '.join(parts[:6])}]"


# =========================================================
# Batch Update (for Theoria/Flash output processing)
# =========================================================

def process_flash_relations(
    channel_id: str,
    relation_updates: List[Dict[str, Any]],
    current_turn: int = 0
) -> int:
    """
    Flash/Theoria가 출력한 NPC 관계 변화를 일괄 처리.

    Expected format:
    [
        {"source": "Alice", "target": "Bob", "type": "rivalry", "intensity": 0.7, "reason": "..."},
        {"source": "Carol", "target": "Alice", "type": "alliance", "delta": +0.2, "reason": "..."},
    ]

    Returns: number of successfully processed updates.
    """
    if not relation_updates or not isinstance(relation_updates, list):
        return 0

    count = 0
    for upd in relation_updates:
        if not isinstance(upd, dict):
            continue
        src = (upd.get("source") or "").strip()
        tgt = (upd.get("target") or "").strip()
        if not src or not tgt or src == tgt:
            continue

        rtype = (upd.get("type") or "neutral").lower().strip()
        reason = upd.get("reason", "")

        # Delta mode vs absolute mode
        try:
            if "delta" in upd:
                delta = float(upd.get("delta", 0))
                result = adjust_intensity(channel_id, src, tgt, delta, reason, current_turn)
                if result is not None:
                    count += 1
            else:
                intensity = float(upd.get("intensity", 0.5))
                result = set_relation(channel_id, src, tgt, rtype, intensity, reason, current_turn)
                if result in ("created", "updated"):
                    count += 1
        except (TypeError, ValueError):
            logger.warning("[EntityRelations] Skipping invalid update: %s", upd)
            continue

    return count


# =========================================================
# Cleanup
# =========================================================

def cleanup_stale_relations(channel_id: str, current_turn: int, max_age: int = 50) -> int:
    """오래된 약한 관계 정리. whisper 이하 + max_age 턴 미갱신."""
    store = _get_relations_store(channel_id)
    edges = store.get("edges", {})
    remove_keys = []

    for key, edge in edges.items():
        intensity = edge.get("intensity", 0.5)
        last_turn = edge.get("last_turn", 0)
        age = current_turn - last_turn

        if intensity < 0.3 and age > max_age:
            remove_keys.append(key)

    for key in remove_keys:
        del edges[key]

    if remove_keys:
        _save_relations_store(channel_id, store)
        logger.info("[EntityRelations] Cleaned %d stale relations", len(remove_keys))

    return len(remove_keys)
