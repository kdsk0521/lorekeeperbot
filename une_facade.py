"""
Lorekeeper - Universal Narrative Engine (UNE) Facade
The main entry point for the UNE engine.
"""

import logging
import random
from typing import Dict, Any

from orchestration_context import GameContext
from waterfall_pipeline import WaterfallPipeline
import domain_manager

logger = logging.getLogger("UNE")

def _pick(items):
    return random.choice(items) if items else ""

def _build_adaptation_line(adapt_pct: int, category: str, tag: str) -> str:
    if adapt_pct is None:
        return ""
    key = category or tag or "이변"
    if adapt_pct < 25:
        tone = "아직 낯설다"
    elif adapt_pct < 50:
        tone = "조금 익숙해졌다"
    elif adapt_pct < 75:
        tone = "확실히 익숙해졌다"
    else:
        tone = "거의 몸에 익었다"
    templates = [
        f"{key}에 대한 익숙함이 쌓인다. ({tone}, 적응도 {adapt_pct}%)",
        f"반복된 경험이 내성을 만든다. ({tone}, 적응도 {adapt_pct}%)",
        f"{key}에 대한 감각이 또렷해진다. ({tone}, 적응도 {adapt_pct}%)"
    ]
    return _pick(templates)

def _build_adaptation_result_line(
    mask: str,
    success: bool,
    note: str,
    old_pct: int,
    new_pct: int
) -> str:
    name = mask or "PC"
    if success is True:
        status = "적응 성공!"
    elif success is False:
        status = "적응 실패!"
    else:
        status = "적응 진행"
    note_txt = f" ({note})" if note else ""
    if old_pct is not None and new_pct is not None:
        return f"{name}: {status}{note_txt} [Adapt {old_pct}%->{new_pct}%]"
    return f"{name}: {status}{note_txt}"

def convert_to_game_context(channel_id: str, user_id: str, user_input: str) -> GameContext:
    """[UNE Bridge] ParticipantData -> GameContext"""
    from orchestration_context import GameContext, RequestData, SharedBus

    p_data = domain_manager.get_participant_data(channel_id, user_id)
    mem = p_data.get("ai_memory", {}) if p_data else {}
    world = domain_manager.get_world_state(channel_id)

    # Genre mapping
    genres_raw = domain_manager.get_active_genres(channel_id)
    if isinstance(genres_raw, dict) and "layers" in genres_raw:
        layers = genres_raw["layers"]
        genres = {
            "stage": layers.get("world_setting", ""),
            "flavor": layers.get("style_tech", ""),
            "lens": layers.get("narrative_tone", "")
        }
    else:
        genres = {
            "stage": genres_raw[0] if isinstance(genres_raw, list) and genres_raw else str(genres_raw),
            "flavor": "",
            "lens": ""
        }

    # Active Modules
    active_modules = domain_manager.get_active_modules(channel_id)

    # Lore Summary (V4)
    lore_summary = domain_manager.get_lore_summary_data(channel_id)

    # History & Lore Text (V4 - for THEORIA)
    history = domain_manager.get_history(channel_id)
    recent_history = history[-30:] if history else []  # Last 30 turns
    history_text = "\n".join([f"{h['role']}: {h['content']}" for h in recent_history])
    lore_text = domain_manager.get_lore(channel_id)

    # Narrative Anchors (행동자 PC)
    anchors = {
        "appearance": mem.get("appearance", ""),
        "personality": mem.get("personality", ""),
        "background": mem.get("background", ""),
        "relations": mem.get("relationships", {}),
        "passives": mem.get("passives", []),
        "inventory": [],
        "memos": []
    }

    # 모든 활성 PC 정보 수집 (다인 플레이 지원)
    all_participants = domain_manager.get_domain(channel_id).get("participants", {})
    all_pcs = {}
    for uid, pdata in all_participants.items():
        if pdata.get("status") == "active":
            pmem = pdata.get("ai_memory", {})
            all_pcs[uid] = {
                "mask": pdata.get("mask", "Unknown"),
                "appearance": pmem.get("appearance", ""),
                "personality": pmem.get("personality", ""),
                "passives": pmem.get("passives", []),
                "mental_value": pmem.get("mental", {}).get("value", 100),
            }
    anchors["all_pcs"] = all_pcs
    anchors["acting_user_id"] = user_id

    # NPC Knowledge & Attitudes (피드백용)
    anchors["stored_npc_knowledge"] = domain_manager.get_npc_knowledge(channel_id)
    anchors["stored_npc_attitudes"] = domain_manager.get_npc_attitudes(channel_id)

    # NPC Roster (Theoria용 이름+역할 요약)
    import npc_manager as _npc_mgr
    anchors["npc_roster"] = _npc_mgr.get_npc_roster(channel_id)

    # Session Memory (World State Updater 피드백용)
    anchors["session_memory"] = domain_manager.get_session_ai_memory(channel_id)

    # Bus initialization
    bus = SharedBus()
    bus.doom["value"] = world.get("doom", 40)
    mental_data = mem.get("mental", {"value": 100, "last_delta": 0})
    bus.mental["value"] = mental_data.get("value", 100)
    bus.mental["last_delta"] = mental_data.get("last_delta", 0)
    adaptation = mem.get("abnormal_exposure", {})
    if not adaptation and p_data:
        adaptation = p_data.get("abnormal_exposure", {})
    bus.mental["adaptation"] = adaptation

    context = GameContext(
        request=RequestData(
            user_input=user_input,
            genres=genres,
            active_modules=active_modules,
            lore_summary=lore_summary,
            history_text=history_text,
            lore_text=lore_text
        ),
        narrative_anchors=anchors,
        shared_bus=bus
    )

    return context

def sync_from_game_context(channel_id: str, user_id: str, ctx: Any) -> None:
    """[UNE Bridge] GameContext -> ParticipantData/WorldState Sync"""
    from orchestration_context import GameContext
    if isinstance(ctx, dict):
        ctx = GameContext.from_dict(ctx)
    bus = ctx.shared_bus

    # 1. World State Sync (Doom)
    if bus.doom.get("active"):
        world = domain_manager.get_world_state(channel_id)
        world["doom"] = bus.doom["value"]
        domain_manager.update_world_state(channel_id, world)

    # 2. Participant Data Sync (Mental, Adaptation)
    p_data = domain_manager.get_participant_data(channel_id, user_id)
    if p_data:
        mem = p_data.setdefault("ai_memory", {})
        if bus.mental.get("active"):
            mental_sys = mem.setdefault("mental", {"value": 100, "last_delta": 0})
            mental_sys["value"] = bus.mental["value"]
            mental_sys["last_delta"] = bus.mental.get("last_delta", 0)

            # Trauma Trigger
            if bus.mental.get("trauma_trigger"):
                passives = mem.setdefault("passives", [])
                trauma_name = "트라우마 (각성)"
                if not any(p.get("name") == trauma_name for p in passives if isinstance(p, dict)):
                    passives.append({
                        "name": trauma_name,
                        "tags": ["Trauma", "Hard-to-cure"],
                        "modifier": -5,
                        "desc": "붕괴한 무의식에서 깨어난 트라우마입니다. 모든 판정에 -5 패널티를 받습니다."
                    })

            # Adaptation Updates
            updates = bus.mental.get("adaptation_update")
            if updates:
                mem.setdefault("abnormal_exposure", {}).update(updates)
                p_data.setdefault("abnormal_exposure", {}).update(updates)

        domain_manager.save_participant_data(channel_id, user_id, p_data)

class UniversalNarrativeEngine:
    def __init__(self, client, model_id: str):
        self.pipeline = WaterfallPipeline(client, model_id)

    async def run(self, channel_id: str, user_id: str, user_input: str) -> Dict[str, Any]:
        """단일 PC 행동 처리 (솔로/자동 모드용)"""
        context = convert_to_game_context(channel_id, user_id, user_input)
        p_data = domain_manager.get_participant_data(channel_id, user_id)
        mask = p_data.get("mask") if p_data else "PC"

        updated_context = await self.pipeline.execute(context)
        sync_from_game_context(channel_id, user_id, updated_context)

        result = self._extract_pc_result(updated_context, mask)
        return {
            "game_context": updated_context,
            "directive": result["directive"],
            "system_message": result["system_msg"]
        }

    async def run_batch(self, channel_id: str, pending_actions: Dict[str, Dict]) -> Dict[str, Any]:
        """다인 동시 행동 처리. pending_actions = {uid: {"mask":str, "actions":[str]}}"""
        all_results = []
        anomaly_data = None
        last_context = None

        for uid, info in pending_actions.items():
            combined_input = "\n".join(info["actions"])
            context = convert_to_game_context(channel_id, uid, combined_input)

            # 이변 중복 방지: 이미 발동했으면 skip_trigger + 동일 이변 정보 주입
            if anomaly_data:
                context.shared_bus.anomaly["skip_trigger"] = True
                context.shared_bus.anomaly.update(anomaly_data)

            updated = await self.pipeline.execute(context)

            # 이변 정보 보존 (첫 발동분)
            if updated.shared_bus.anomaly.get("triggered") and not anomaly_data:
                anomaly_data = {
                    "tag": updated.shared_bus.anomaly.get("tag"),
                    "intensity": updated.shared_bus.anomaly.get("intensity"),
                    "polarity": updated.shared_bus.anomaly.get("polarity"),
                    "category": updated.shared_bus.anomaly.get("category"),
                    "potential": True,
                }

            sync_from_game_context(channel_id, uid, updated)
            all_results.append(self._extract_pc_result(updated, info["mask"]))
            last_context = updated

        return self._combine_batch_results(all_results, last_context)

    async def run_observation(self, channel_id: str) -> Dict[str, Any]:
        """관찰 모드: PC 행동 없이 세계 묘사"""
        participants = domain_manager.get_domain(channel_id).get("participants", {})
        base_uid = None
        for uid, p in participants.items():
            if p.get("status") == "active":
                base_uid = uid
                break

        if not base_uid:
            return {"game_context": None, "directive": "", "system_message": ""}

        observation_input = "[관찰 모드 — 직접적인 행동 없이 주변을 지켜본다]"
        context = convert_to_game_context(channel_id, base_uid, observation_input)

        # 판정 비활성화 (관찰은 행동이 아님)
        context.shared_bus.judgment["active"] = False

        updated = await self.pipeline.execute(context)
        sync_from_game_context(channel_id, base_uid, updated)

        result = self._extract_pc_result(updated, "")
        return {
            "game_context": updated,
            "directive": "[관찰 모드] 세계와 NPC의 자연스러운 활동을 묘사하라. PC의 행동은 없다.\n" + result["directive"],
            "system_message": result["system_msg"]
        }

    def _extract_pc_result(self, context, mask: str) -> Dict[str, Any]:
        """단일 PC 파이프라인 결과에서 directive + system_msg 추출"""
        bus = context.shared_bus
        directive_parts = []
        system_msg = ""

        # Judgment
        if bus.judgment and bus.judgment.get("active"):
            j_mask = bus.judgment.get("mask", mask)
            reason_txt = f" (근거: {bus.judgment.get('reason')})" if bus.judgment.get('reason') else ""
            directive_parts.append(f"[{j_mask}의 판정 결과]: {bus.judgment.get('result')} ({bus.judgment.get('roll')}){reason_txt}")
            system_msg += bus.judgment.get("output", "")
            if bus.judgment.get("party_wide_hook"):
                system_msg += "\n⚠️ **[전체 파티 영향]** — 이 결과는 모든 동료에게 영향을 미칩니다."

        # Anomaly
        anomaly_directive = ""
        anomaly_sys = ""
        if bus.anomaly and bus.anomaly.get("triggered"):
            tag = bus.anomaly.get("tag")
            intensity = bus.anomaly.get("intensity")
            polarity = bus.anomaly.get("polarity")
            category = bus.anomaly.get("category")
            cat_txt = f" / 적응키 {category}" if category and category != tag else ""
            anomaly_directive = f"[이변 활성화]: {tag} - {intensity} / {polarity}{cat_txt}"
            if bus.anomaly.get("output"):
                anomaly_directive += f"\n{bus.anomaly.get('output')}"

            # Anomaly system message
            line = bus.anomaly.get("line")
            header = f"⚡ 이변 발생: [[{tag or '이변'}]]"
            anomaly_sys += f"\n{header}"
            if line:
                anomaly_sys += f"\n{line}"
            else:
                info_parts = []
                if tag: info_parts.append(f"태그: {tag}")
                if intensity: info_parts.append(f"강도: {intensity}")
                if polarity: info_parts.append(f"성격: {polarity}")
                if category and category != tag: info_parts.append(f"적응키: {category}")
                anomaly_sys += f"\n{' / '.join(info_parts) if info_parts else '이변 정보: (미상)'}"

            divider = "━" * 20
            anomaly_sys += f"\n{divider}\n{divider}"
            anomaly_sys += f"\n🎲 적응 판정 결과: [[{category or tag or '이변'}]]"
            result_line = _build_adaptation_result_line(
                mask,
                bus.anomaly.get("defense_success"),
                bus.anomaly.get("defense_note", ""),
                bus.anomaly.get("adapt_pct"),
                bus.anomaly.get("adapt_new_pct")
            )
            if result_line:
                anomaly_sys += f"\n{result_line}"

        if anomaly_directive:
            directive_parts.append(anomaly_directive)
        system_msg += anomaly_sys

        # Doom logs
        if bus.doom and bus.doom.get("relief_log"):
            system_msg += f"\n{bus.doom.get('relief_log')}"
        if bus.doom and bus.doom.get("mental_pressure_log"):
            system_msg += f"\n{bus.doom.get('mental_pressure_log')}"

        # Mental
        if bus.mental:
            mental_parts = []
            if bus.mental.get("impact_log"):
                impact_log = bus.mental.get("impact_log")
                if "(" in impact_log and ")" in impact_log:
                    reason = impact_log.split("(", 1)[1].rsplit(")", 1)[0]
                    mental_parts.append(reason)
            if bus.mental.get("log"):
                mental_parts.append(bus.mental.get("log"))
            if mental_parts:
                system_msg += f"\n{' → '.join(mental_parts)}"

        # Fallbacks
        fallback_msg = self.pipeline.get_fallback_directives(context.request.active_modules)
        if fallback_msg:
            directive_parts.append(f"\n[모듈 제약 지침]:\n{fallback_msg}")

        return {
            "directive": "\n".join(directive_parts),
            "system_msg": system_msg,
            "has_anomaly": bool(bus.anomaly and bus.anomaly.get("triggered")),
            "anomaly_header": anomaly_sys.split("━━")[0] if anomaly_sys else "",
            "adaptation_line": result_line if (bus.anomaly and bus.anomaly.get("triggered")) else "",
            "mental_log": bus.mental.get("log", "") if bus.mental else "",
        }

    def _combine_batch_results(self, results: list, last_context) -> Dict[str, Any]:
        """다인 배치 결과를 통합 출력으로 합침"""
        all_directives = []
        judgment_msgs = []
        anomaly_header = ""
        adaptation_lines = []
        mental_lines = []
        doom_lines = []

        for r in results:
            if r["directive"]:
                all_directives.append(r["directive"])

            # 판정 부분만 추출 (system_msg에서 이변/멘탈 제외)
            sys = r["system_msg"]
            # 판정 출력은 🎲으로 시작, ⚡ 이변 전까지
            if "🎲" in sys:
                judgment_part = sys.split("⚡")[0].split("⏳")[0]
                # doom/mental 로그 제거
                for marker in ["\n📈", "\n📉", "\n🧠"]:
                    if marker in judgment_part:
                        judgment_part = judgment_part[:judgment_part.index(marker)]
                judgment_msgs.append(judgment_part.strip())

            # 이변 헤더는 1회만
            if r["has_anomaly"] and not anomaly_header:
                anomaly_header = r["anomaly_header"]

            if r["adaptation_line"]:
                adaptation_lines.append(r["adaptation_line"])

            if r["mental_log"]:
                mental_lines.append(r["mental_log"])

        # 통합 시스템 메시지 구성
        combined_sys = ""

        # 1. 모든 판정 결과
        if judgment_msgs:
            combined_sys += "\n\n".join(judgment_msgs)

        # 2. 이변 (헤더 1회 + 적응 PC별)
        if anomaly_header:
            combined_sys += f"\n\n{anomaly_header.strip()}"
            divider = "━" * 20
            combined_sys += f"\n{divider}\n{divider}"
            tag = results[0].get("anomaly_tag", "이변")
            combined_sys += f"\n🎲 적응 판정 결과:"
            for line in adaptation_lines:
                combined_sys += f"\n{line}"

        # 3. 멘탈 변동 (PC별)
        if mental_lines:
            combined_sys += "\n"
            for line in mental_lines:
                combined_sys += f"\n{line}"

        return {
            "game_context": last_context,
            "directive": "\n\n".join(all_directives),
            "system_message": combined_sys.strip()
        }
