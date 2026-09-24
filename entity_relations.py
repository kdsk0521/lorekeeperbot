"""
Lorekeeper UNE - Entity Relation Network (v1.0)
NPC↔NPC 관계 그래프 관리. 기존 PC↔NPC 태도 시스템(npc_manager)과 공존.

LIBRA EntityManager에서 이식:
- NPC 간 관계 추적 (alliance, rivalry, fear, respect, etc.)
- 관계 강도 + 방향성 (A→B ≠ B→A)
- 관계 변화 이력 (delta-only)
- 서사용 관계 컨텍스트 생성

[2026-09-15 관계 통합 1차] 저장소 없음 — **엣지(relations 테이블) 위 NPC↔NPC 파생 뷰 + 배치 쓰기 변환기**.
  옛 domain_data["entity_relations"]["edges"] 저장·감쇠(set_relation/adjust_intensity/cleanup_stale_relations)는
  삭제(마이그레이션 없음). 게터 이름·반환 모양은 유지: type=kind, intensity=abs(bond)/100, reason=stance.
  감쇠는 domain_manager.decay_relation_edges 한 곳. 모듈을 흡수하지 않고 남긴 이유: 소비자 5곳
  (slot_manager·story_director·interim·world_board·persistent_audit)이 이 이름으로 import — 소비자 무변경.
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

# Intensity thresholds for narrative importance
INTENSITY_THRESHOLDS = {
    "whisper": (0.0, 0.3),   # Background, barely noticeable
    "steady":  (0.3, 0.6),   # Notable, mentioned when relevant
    "strong":  (0.6, 0.8),   # Drives behavior, affects decisions
    "burning": (0.8, 1.01),  # Defines character, impossible to ignore
}


# =========================================================
# Edge view (relations 테이블 → 옛 edge dict 모양)
# =========================================================

def _edge_key(source: str, target: str) -> str:
    """방향성 엣지 키 생성 (A→B)."""
    return f"{source}→{target}"


def _valence_sign(kind: Optional[str]) -> int:
    return -1 if RELATION_TYPES.get(kind or "neutral", RELATION_TYPES["neutral"])["valence"] < 0 else 1


def get_all_relations(channel_id: str) -> Dict[str, Dict[str, Any]]:
    """모든 NPC↔NPC 관계 엣지(kind 있는 엣지) — 파생 뷰. {"A→B": {source,target,type,intensity,reason,last_turn,history}}"""
    out: Dict[str, Dict[str, Any]] = {}
    for e in domain_manager.get_relation_edges(channel_id):
        if e.get("kind") is None:
            continue
        hist = []
        for h in e.get("history") or []:
            if isinstance(h, dict):
                hh = dict(h)
                hh.setdefault("new_type", e["kind"])
                hh.setdefault("new_intensity", round(abs(int(h.get("bond", 0) or 0)) / 100.0, 2))
                hist.append(hh)
        out[_edge_key(e["source"], e["target"])] = {
            "source": e["source"],
            "target": e["target"],
            "type": e["kind"],
            "relation_type": e["kind"],
            "intensity": round(abs(int(e.get("bond", 0) or 0)) / 100.0, 2),
            "bond": int(e.get("bond", 0) or 0),
            "tension": int(e.get("tension", 0) or 0),
            "reason": e.get("stance", "") or "",
            "last_turn": e.get("last_turn"),
            "history": hist,
        }
    return out


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

def process_batch_relations(
    channel_id: str,
    relation_updates: List[Dict[str, Any]],
    current_turn: int = 0
) -> int:
    """배치 추출 `social.npc_relations` → NPC↔NPC 엣지(upsert, origin="batch").

    프롬 계약(intensity 0~1 / delta ±0.1~0.3)은 그대로 받아 bond로 옮긴다:
      bond = ±intensity×100 (부호 = kind valence, 음수 계열 rivalry/fear/distrust/grudge는 −)
      delta 모드 = |bond| += delta×100 (delta는 선언 캡 ±0.3 유지), 기존 엣지 없으면 무시(옛 동작).
    Returns: 쓴 엣지 수."""
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
        rtype = str(upd.get("type") or "neutral").lower().strip()
        if rtype not in RELATION_TYPES:
            rtype = "neutral"
        reason = upd.get("reason", "")
        reason = reason.strip() if isinstance(reason, str) and reason.strip() else None
        # [2026-09-25 관계 정성] 모델은 말(strength / shift)을 낸다 → 숫자는 여기서. 옛 숫자(intensity / delta)는 폴백.
        import config as _cfg_rel
        _pair_str = getattr(_cfg_rel, "REL_PAIR_STRENGTH", {}) or {}
        _pair_shift = getattr(_cfg_rel, "REL_PAIR_SHIFT", {}) or {}
        _shift = str(upd.get("shift") or "").strip().lower()
        _strength = str(upd.get("strength") or "").strip().lower()
        _shift = _shift if _shift in _pair_shift else ""
        _strength = _strength if _strength in _pair_str else ""
        try:
            if _shift or "delta" in upd:
                if _shift:
                    delta = _pair_shift[_shift] / 100.0
                else:
                    delta = float(upd.get("delta", 0))
                    import bot_utils as _bu_cap
                    delta, _ = _bu_cap.cap_llm_delta(
                        delta, "relation.intensity", "delta", subject=f"{src}->{tgt}")
                # [2026-09-24 감사] 조회도 쓰기 쪽(upsert_relation_edge → _resolve_npc_name)처럼 이름을 정본 키로 푼다 —
                #   정확일치 조회라 배치가 별칭·변형 이름으로 delta 를 주면 증감이 로그 없이 버려졌다.
                try:
                    _npcs_er = domain_manager.get_npcs(channel_id) or {}
                    _src_q = domain_manager._find_npc_key(_npcs_er, src) or src
                    _tgt_q = domain_manager._find_npc_key(_npcs_er, tgt) or tgt
                except Exception:
                    _src_q, _tgt_q = src, tgt
                cur = domain_manager.get_relation_edges(channel_id, source=_src_q, target=_tgt_q)
                cur = [e for e in cur if e.get("kind") is not None]
                if not cur:
                    # 말로 온 이동인데 엣지가 없으면 새 관계로 연다(세기 = strength, 없으면 clear). 옛 숫자 delta 는 종전대로 무시.
                    if _shift:
                        _mag0 = int(_pair_str.get(_strength or "clear", 50))
                        r0 = domain_manager.upsert_relation_edge(
                            channel_id, src, tgt, bond=_valence_sign(rtype) * _mag0, stance=reason,
                            kind=rtype, turn=current_turn, origin="batch")
                        if r0:
                            count += 1
                    continue
                kind = cur[0]["kind"]
                mag = max(0, min(100, abs(int(cur[0]["bond"])) + int(round(delta * 100))))
                r = domain_manager.upsert_relation_edge(
                    channel_id, src, tgt, bond=_valence_sign(kind) * mag, stance=reason,
                    kind=kind, turn=current_turn, origin="batch")
            else:
                if _strength:
                    intensity = _pair_str[_strength] / 100.0
                else:
                    intensity = max(0.0, min(1.0, float(upd.get("intensity", 0.5))))
                r = domain_manager.upsert_relation_edge(
                    channel_id, src, tgt, bond=_valence_sign(rtype) * int(round(intensity * 100)),
                    stance=reason, kind=rtype, turn=current_turn, origin="batch")
            if r:
                count += 1
        except (TypeError, ValueError):
            logger.warning("[EntityRelations] Skipping invalid update: %s", upd)
            continue
    return count
