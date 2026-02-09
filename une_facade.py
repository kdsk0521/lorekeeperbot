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
                        "desc": "기력 붕괴에서 깨어난 트라우마입니다. 모든 판정에 -5 패널티를 받습니다."
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
        """Extract 5-Layer Directive + system_msg from single PC pipeline result.

        Layer 0: [Base Directive] — DAI soft hints (when Judgment OFF)
        Layer 1: [Narrative] — FitD Position + PbtA MC Move (Judgment result)
        Layer 2: [Aspects] — Fate Aspect declaration (cross-module interaction)
        Layer 3: [Intrusion] — Cypher GM Intrusion (Anomaly event)
        Layer 4: [Atmosphere] — Doom Clock progress + Vigor state
        """
        bus = context.shared_bus
        directive_parts = []
        system_msg = ""
        result_line = ""

        doom_val = bus.doom.get("value", 0) if bus.doom else 0
        mental_val = bus.mental.get("value", 100) if bus.mental else 100

        # ── Layer 1: [Narrative] — Position + MC Move ──
        j_active = bus.judgment and bus.judgment.get("active")
        j_result = ""
        if j_active:
            j_mask = bus.judgment.get("mask", mask)
            meta = bus.judgment.get("meta", {})
            action = meta.get("action", "행동")
            j_result = bus.judgment.get("result", "failure")
            reason_txt = bus.judgment.get("reason", "")

            # Position from Theoria (FitD)
            pos_val = bus.dai.get("position", {}).get("value", 0.5) if bus.dai else 0.5
            if pos_val <= 0.25:
                pos_tier = "desperate"
            elif pos_val <= 0.5:
                pos_tier = "risky"
            else:
                pos_tier = "controlled"

            # MC Move: Position × Result matrix (PbtA)
            if j_result in ("failure", "critical_failure"):
                mc_moves = {
                    "desperate": "Make the threat real — irreversible consequences",
                    "risky": "Escalate the situation — a new danger reveals itself",
                    "controlled": "Demand a minor cost — a small setback occurs",
                }
                if j_result == "critical_failure":
                    mc_moves = {
                        "desperate": "Catastrophic outcome — something irreversible happens",
                        "risky": "Worst case unfolds — the danger becomes reality",
                        "controlled": "Unexpected reversal — safety shatters",
                    }
            elif j_result == "partial":
                mc_moves = {
                    "desperate": "Heavy price paid — gain what was sought but lose something",
                    "risky": "Success with cost — complications follow",
                    "controlled": "Minor friction — less smooth than expected",
                }
            else:  # success / critical_success
                mc_moves = {
                    "desperate": "Dramatic turnaround — shining in the direst moment",
                    "risky": "Danger cleared — competent execution",
                    "controlled": "Clean success — smooth and effortless",
                }
                if j_result == "critical_success":
                    mc_moves = {
                        "desperate": "Miraculous reversal — a transcendent moment",
                        "risky": "Brilliant success — impressive result against the odds",
                        "controlled": "Overwhelming mastery — exceeds all expectations",
                    }

            move = mc_moves.get(pos_tier, mc_moves.get("risky", ""))
            reason_part = f" ({reason_txt})" if reason_txt else ""
            directive_parts.append(
                f"[Narrative: {j_mask} '{action}'{reason_part} — {pos_tier}] {move}"
            )
            system_msg += bus.judgment.get("output", "")
            if bus.judgment.get("party_wide_hook"):
                system_msg += "\n⚠️ **[전체 파티 영향]** — 이 결과는 모든 동료에게 영향을 미칩니다."

        # ── Layer 0: [Base Directive] — DAI soft hints (Judgment OFF) ──
        if not j_active and bus.dai and bus.dai.get("active"):
            hints = []

            # Position → narrative tone
            pos_data = bus.dai.get("position", {})
            pos_val = pos_data.get("value", 0.5)
            if pos_val <= 0.25:
                hints.append("Position: Desperate — stakes are lethal, consequences loom")
            elif pos_val <= 0.5:
                hints.append("Position: Risky — danger present, outcome uncertain")
            else:
                hints.append("Position: Controlled — situation favors the actor")

            # SceneType → scene-specific guidance
            scene_type = bus.dai.get("scene_type", "normal")
            scene_hints = {
                "combat": "Combat scene: emphasize physicality, positioning, and threat",
                "tension": "Tension scene: build suspense, restrict information flow",
                "intimate": "Intimate scene: focus on emotion, subtlety, and vulnerability",
                "exploration": "Exploration scene: reward curiosity, reveal the world",
                "social": "Social scene: weigh reputation, leverage, and hidden agendas",
            }
            if scene_type in scene_hints:
                hints.append(scene_hints[scene_type])

            # EnergyDirection → pacing
            energy = bus.dai.get("energy_direction", "steady")
            energy_hints = {
                "rising": "Energy rising — escalate tension, accelerate pacing",
                "falling": "Energy falling — allow breathing room, reflect on aftermath",
                "peak": "Energy at peak — climactic moment, maximum intensity",
                "steady": "Energy steady — maintain current rhythm",
            }
            if energy in energy_hints:
                hints.append(energy_hints[energy])

            # needs_judgment=True but module OFF → soft probability hint
            if bus.dai.get("needs_judgment"):
                action_meta = bus.dai.get("action_meta", {})
                action_name = action_meta.get("action", "")
                if action_name:
                    hints.append(f"Action '{action_name}' attempted — judge outcome by situational probability, no dice")
                else:
                    hints.append("Meaningful action attempted — judge outcome by situational probability, no dice")

            if hints:
                directive_parts.append("[Base Directive]\n" + "\n".join(hints))

        # ── Layer 3: [Intrusion] — Cypher GM Intrusion ──
        anomaly_sys = ""
        a_triggered = bus.anomaly and bus.anomaly.get("triggered")
        if a_triggered:
            tag = bus.anomaly.get("tag") or "이변"
            intensity = bus.anomaly.get("intensity")
            polarity = bus.anomaly.get("polarity")
            category = bus.anomaly.get("category")
            line = bus.anomaly.get("line", "")

            # GM Intrusion framing: anomaly is "situation shift", not "punishment"
            polarity_frame = {
                "positive": "may serve as opportunity",
                "negative": "arrives as threat",
                "mixed": "both opportunity and threat",
            }.get(polarity, "shifts the situation")
            intrusion = f"[Intrusion: {tag}] {polarity_frame}"
            if line:
                intrusion += f"\n{line}"
            if bus.anomaly.get("output"):
                intrusion += f"\n{bus.anomaly.get('output')}"
            directive_parts.append(intrusion)

            # Anomaly system message (Discord)
            header = f"⚡ 이변 발생: [[{tag}]]"
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

            if bus.anomaly.get("escalated"):
                anomaly_sys += f"\n⚠️ **대실패 공명**: 이변 강도 상승!"
            divider = "━" * 20
            anomaly_sys += f"\n{divider}\n{divider}"
            anomaly_sys += f"\n🎲 적응 판정 결과: [[{category or tag}]]"
            result_line = _build_adaptation_result_line(
                mask,
                bus.anomaly.get("defense_success"),
                bus.anomaly.get("defense_note", ""),
                bus.anomaly.get("adapt_pct"),
                bus.anomaly.get("adapt_new_pct")
            )
            if result_line:
                anomaly_sys += f"\n{result_line}"
        system_msg += anomaly_sys

        # ── System Logs (Discord) ──
        if bus.doom and bus.doom.get("relief_log"):
            system_msg += f"\n{bus.doom.get('relief_log')}"
        if bus.doom and bus.doom.get("mental_pressure_log"):
            system_msg += f"\n{bus.doom.get('mental_pressure_log')}"
        if bus.mental:
            log_parts = []
            if bus.mental.get("impact_log"):
                impact_log = bus.mental.get("impact_log")
                if "(" in impact_log and ")" in impact_log:
                    reason = impact_log.split("(", 1)[1].rsplit(")", 1)[0]
                    log_parts.append(reason)
            if bus.mental.get("log"):
                log_parts.append(bus.mental.get("log"))
            if log_parts:
                system_msg += f"\n{' → '.join(log_parts)}"

        # ── Layer 2: [Aspects] — Fate Aspect declaration ──
        aspects = []
        m_trauma = bus.mental and bus.mental.get("trauma_trigger")
        if j_active and a_triggered:
            if j_result in ("critical_failure", "failure"):
                aspects.append("Failure Resonance")
            elif j_result == "critical_success":
                aspects.append("Glory's Shadow")
        if a_triggered and mental_val <= 39:
            aspects.append("Vigor Erosion")
        if m_trauma and a_triggered:
            aspects.append("Inner-Outer Convergence")
        if m_trauma and j_active:
            aspects.append("Resurgence")
        if j_result == "critical_failure" and mental_val <= 14:
            aspects.append("Abyss")
        if bus.anomaly and bus.anomaly.get("escalated"):
            aspects.append("Loss of Control")
        if aspects:
            directive_parts.append("[Aspects]: " + ", ".join(aspects))

        # ── Layer 4: [Atmosphere] — Doom Clock + Vigor ──
        atmosphere = []
        active_modules = context.request.active_modules

        # Doom = 8-Segment FitD Clock (only when module active)
        if "doom" in active_modules:
            if doom_val >= 88:
                atmosphere.append(f"Threat Clock {doom_val}% [IMMINENT] — about to break")
            elif doom_val >= 76:
                atmosphere.append(f"Threat Clock {doom_val}% [CRISIS] — running out of time")
            elif doom_val >= 63:
                atmosphere.append(f"Threat Clock {doom_val}% [THREAT] — danger closing in")
            elif doom_val >= 50:
                atmosphere.append(f"Threat Clock {doom_val}% [TENSION] — unease fills the air")
            elif doom_val >= 38:
                atmosphere.append(f"Threat Clock {doom_val}% [ALERT] — uneasy calm")
            elif doom_val >= 25:
                atmosphere.append(f"Threat Clock {doom_val}% [NEUTRAL] — equilibrium")
            elif doom_val >= 13:
                atmosphere.append(f"Threat Clock {doom_val}% [STABLE] — relative safety")
            else:
                atmosphere.append(f"Threat Clock {doom_val}% [RELAXED] — threat has receded")

        # Vigor = PC holistic resource (only when module active)
        if "mental" in active_modules:
            if mental_val <= 14:
                atmosphere.append(f"Vigor COLLAPSE ({mental_val}%) — past the limit, body and mind breaking")
            elif mental_val <= 39:
                atmosphere.append(f"Vigor DEPLETED ({mental_val}%) — exhausted, everything is a struggle")
            elif mental_val <= 69:
                atmosphere.append(f"Vigor SHAKEN ({mental_val}%) — wavering, hard to focus")

        if m_trauma:
            atmosphere.append("Trauma Awakening — rebirth from the brink")

        if atmosphere:
            directive_parts.append("[Atmosphere]: " + " / ".join(atmosphere))

        # Fallbacks
        fallback_msg = self.pipeline.get_fallback_directives(context.request.active_modules)
        if fallback_msg:
            directive_parts.append(f"\n[Module Constraints]:\n{fallback_msg}")

        return {
            "directive": "\n".join(directive_parts),
            "system_msg": system_msg,
            "has_anomaly": bool(a_triggered),
            "anomaly_header": anomaly_sys.split("━━")[0] if anomaly_sys else "",
            "adaptation_line": result_line if a_triggered else "",
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
