# -*- coding: utf-8 -*-
"""
persistent_audit — 영속층 오염 감사 (C안, 2026-07-02).

목적: Flash 오판이 무게이트로 영구 상태에 누적되는 경로(npc_knowledge 영속+전파,
      entity_relations, world_tree 자동 노드)를 N턴마다 백그라운드에서 검사.
      "턴은 방어되는데 상태가 서서히 오염된다"([[북극성]] ①)의 관측 채널.

원칙:
  - **검출≠쓰기**: 자동 삭제/수정 절대 없음. 로그 + ai_session_memory 보고서 저장만.
    수리는 사람이 로그 보고 !npc 등으로. (자동 수리는 오검출 시 상태 파괴라 금지)
  - 백그라운드 큐 실행 (턴 지연 0), 실패 무해.
  - 호출: orchestration.execute가 turn_index % config.PERSIST_AUDIT_INTERVAL == 0 에 enqueue.
"""

import json
import logging
import time
from typing import Any, Dict, Optional

import config
import bot_utils
import domain_manager
from google.genai import types

logger = logging.getLogger("PersistAudit")

_AUDIT_SYSTEM = """You are a persistent-state auditor for a TRPG session database.
The state below accumulated automatically from per-turn LLM analysis — some entries may be polluted (a flaky turn wrote a wrong fact that then persisted or propagated).

Detect ONLY these four categories:
- contradictions: two entries asserting incompatible facts (within one NPC's knowledge, between NPCs, or between knowledge and relations).
- duplicates: the same fact stored twice — near-duplicate phrasings, or cross-NPC copies left by knowledge propagation.
- orphans: knowledge/relations referencing names absent from the roster; auto-registered locations that nothing references. Names in the PLAYER CHARACTERS list are known entities — references to them are NEVER orphans (they are simply not NPCs).
- suspicious: knowledge an NPC could not plausibly have acquired (no witnessing path), or a relation edge with no plausible origin.

Rules: detection only — do NOT propose rewrites, do NOT invent context. One telegraphic English line per finding, names included. Empty arrays when clean; clean is the common case.

Return valid JSON: {"contradictions": [str], "duplicates": [str], "orphans": [str], "suspicious": [str]}"""


def _build_state_dump(channel_id: str) -> str:
    """감사 대상 상태 덤프 (캡 있는 컴팩트 텍스트)."""
    parts = []

    npcs = domain_manager.get_npcs(channel_id) or {}
    roster = list(npcs.keys())
    parts.append("## ROSTER\n" + (", ".join(roster) if roster else "(empty)"))

    # [2026-07-19 PC 혼입 계열 수리] PC 마스크를 별도 명부로 동봉 — PC를 참조하는
    # 관계/지식("사쿠라→레이선")이 "명부에 없는 이름=고아"로 오탐되던 것 차단.
    # (07-13 npc_attitudes PC 혼입 가드와 같은 병 계열: PC/NPC 구분 누락)
    try:
        pc_masks = sorted({
            str(p.get("mask")) for p in
            (domain_manager.get_domain(channel_id).get("participants", {}) or {}).values()
            if isinstance(p, dict) and p.get("mask")
        })
        if pc_masks:
            parts.append(
                "## PLAYER CHARACTERS (valid reference targets — NOT NPCs, "
                "never orphans when referenced)\n" + ", ".join(pc_masks))
    except Exception:
        pass

    knowledge = domain_manager.get_npc_knowledge(channel_id) or {}
    if knowledge:
        k_lines = []
        for name, k in list(knowledge.items())[:20]:
            if not isinstance(k, dict):
                continue
            knows = "; ".join(str(x) for x in (k.get("knows") or [])[:12])
            secrets = "; ".join(str(x) for x in (k.get("secrets_held") or [])[:6])
            fb = "; ".join(str(x) for x in (k.get("false_beliefs") or [])[:6])
            k_lines.append(f"- {name}: knows=[{knows}] secrets=[{secrets}] false_beliefs=[{fb}]")
        parts.append("## KNOWLEDGE\n" + "\n".join(k_lines))

    try:
        import entity_relations
        edges = entity_relations.get_all_relations(channel_id) or {}
        if edges:
            r_lines = []
            for key, e in list(edges.items())[:30]:
                if isinstance(e, dict):
                    r_lines.append(
                        f"- {key}: {e.get('relation_type', '?')} "
                        f"(intensity={e.get('intensity', '?')}, reason={str(e.get('reason', ''))[:60]})"
                    )
            parts.append("## RELATIONS\n" + "\n".join(r_lines))
    except Exception:
        pass

    try:
        import world_tree
        nodes = world_tree.get_all_nodes(channel_id) or {}
        auto_nodes = []
        for nid, nd in list(nodes.items())[:40]:
            if isinstance(nd, dict):
                tags = (nd.get("properties") or {}).get("tags", []) if isinstance(nd.get("properties"), dict) else []
                mark = " [auto]" if "auto_detected" in tags else ""
                auto_nodes.append(f"- {nd.get('name', nid)}{mark}")
        if auto_nodes:
            parts.append("## LOCATIONS (world_tree)\n" + "\n".join(auto_nodes))
    except Exception:
        pass

    return "\n\n".join(parts)


async def run_persistent_audit(client, model_id: str, channel_id: str) -> Optional[Dict[str, Any]]:
    """영속층 감사 1회. 보고서 반환 (+로그/저장). 실패 시 None — 파이프 무해."""
    if not client or not channel_id:
        return None
    try:
        dump = _build_state_dump(channel_id)
        if len(dump) < 60:  # 감사할 상태가 사실상 없음
            return None

        gen_config = types.GenerateContentConfig(
            system_instruction=_AUDIT_SYSTEM,
            response_mime_type="application/json",
            max_output_tokens=4096,  # 상태가 더러울수록 보고서가 길어짐 — log-only라 여유가 정보량
            temperature=0.2,
            safety_settings=config.SAFETY_SETTINGS,
        )
        response = await client.aio.models.generate_content(
            model=model_id,
            contents=[types.Content(role="user", parts=[types.Part(text=dump)])],
            config=gen_config,
        )
        if not response or not response.text:
            return None

        cleaned = bot_utils.clean_json_text(response.text)
        try:
            report = json.loads(cleaned)
        except json.JSONDecodeError:
            report = json.loads(bot_utils.repair_json(cleaned))
        if not isinstance(report, dict):
            return None

        counts = {}
        for cat in ("contradictions", "duplicates", "orphans", "suspicious"):
            items = report.get(cat)
            if not isinstance(items, list):
                items = []
                report[cat] = []
            counts[cat] = len(items)

        total = sum(counts.values())
        _turn = domain_manager.get_world_state(channel_id).get("turn_index", 0)
        logger.info(f"[PersistAudit] turn={_turn} total={total} "
                    + " ".join(f"{k}={v}" for k, v in counts.items()))
        # [2026-08-03] 항목 전문은 verbose 채널로.
        #   종전엔 카테고리 4종 × 최대 8건 × 200자 절단을 **journal에 warning으로** 쏟아
        #   감사 도는 턴마다 최대 1600자가 흐름을 덮었다. 게다가 200자에서 잘려 정작
        #   긴 항목(모순 쌍의 양쪽 인용)은 뒷부분이 사라졌다 — 시끄러운데 안 보이는 상태.
        #   journal은 위 카운트 한 줄(훑기 충분), 전문은 tail -f logs/verbose.log.
        if total:
            try:
                bot_utils.vlog(
                    "PersistAudit",
                    json.dumps(report, ensure_ascii=False, indent=2),
                    channel_id,
                )
            except Exception:
                pass

        # 보고서 저장 (사람 리뷰용 — 자동 수리 없음)
        domain_manager.update_session_ai_memory(channel_id, {
            "persist_audit": {
                "turn": _turn,
                "ts": time.time(),
                "counts": counts,
                "report": {k: [str(x)[:200] for x in v[:10]] for k, v in report.items()
                           if k in ("contradictions", "duplicates", "orphans", "suspicious")},
            }
        })
        return report
    except Exception as e:
        logger.warning(f"[PersistAudit] failed (무해): {e}")
        return None
