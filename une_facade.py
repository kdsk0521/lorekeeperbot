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

    # Narrative Anchors
    anchors = {
        "appearance": mem.get("appearance", ""),
        "personality": mem.get("personality", ""),
        "background": mem.get("background", ""),
        "relations": mem.get("relationships", {}),
        "passives": mem.get("passives", []),
        "inventory": [],
        "memos": []
    }

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
        """
        엔진을 실행하여 서사적 결과물(Directive)을 반환합니다.
        
        Args:
            channel_id: 디스코드 채널 ID
            user_id: 유저 ID
            user_input: 사용자 입력 텍스트
            
        Returns:
            Dict: {
                "game_context": object,
                "directive": str,      # LLM 지시문
                "system_message": str  # 유저에게 보여줄 시스템 로그 (판정 결과 등)
            }
        """
        # 1. Convert legacy data to GameContext
        context = convert_to_game_context(channel_id, user_id, user_input)
        p_data = domain_manager.get_participant_data(channel_id, user_id)
        mask = p_data.get("mask") if p_data else "PC"
        
        # 2. Execute Waterfall Pipeline
        updated_context = await self.pipeline.execute(context)
        
        # 3. Sync result back to legacy storage
        sync_from_game_context(channel_id, user_id, updated_context)
        
        # 4. Generate Directive for Final LLM
        bus = updated_context.shared_bus
        directive_parts = []
        
        # Judgment result in directive
        if bus.judgment and bus.judgment.get("active"):
            reason_txt = f" (근거: {bus.judgment.get('reason')})" if bus.judgment.get('reason') else ""
            directive_parts.append(f"[판정 결과]: {bus.judgment.get('result')} ({bus.judgment.get('roll')}){reason_txt}")
        
        # Anomaly outcome in directive
        if bus.anomaly and bus.anomaly.get("triggered"):
            tag = bus.anomaly.get("tag")
            intensity = bus.anomaly.get("intensity")
            polarity = bus.anomaly.get("polarity")
            category = bus.anomaly.get("category")
            cat_txt = f" / 적응키 {category}" if category and category != tag else ""
            directive_parts.append(
                f"[이변 활성화]: {tag} - {intensity} / {polarity}{cat_txt}"
            )
            if bus.anomaly.get("output"):
                directive_parts.append(bus.anomaly.get("output"))
            
        # Fallbacks
        fallback_msg = self.pipeline.get_fallback_directives(context.request.active_modules)
        if fallback_msg:
            directive_parts.append(f"\n[모듈 제약 지침]:\n{fallback_msg}")

        # System message for UI
        system_msg = ""
        if bus.judgment and bus.judgment.get("active"):
            system_msg += bus.judgment.get("output", "")
        if bus.anomaly and bus.anomaly.get("triggered"):
            tag = bus.anomaly.get("tag")
            intensity = bus.anomaly.get("intensity")
            polarity = bus.anomaly.get("polarity")
            category = bus.anomaly.get("category")
            adapt_pct = bus.anomaly.get("adapt_pct")
            adapt_new_pct = bus.anomaly.get("adapt_new_pct")
            defense_success = bus.anomaly.get("defense_success")
            defense_note = bus.anomaly.get("defense_note", "")
            line = bus.anomaly.get("line")
            header = f"⚡ 이변 발생: [[{tag or '이변'}]]"
            system_msg += f"\n{header}"
            if line:
                system_msg += f"\n{line}"
            else:
                info_parts = []
                if tag:
                    info_parts.append(f"태그: {tag}")
                if intensity:
                    info_parts.append(f"강도: {intensity}")
                if polarity:
                    info_parts.append(f"성격: {polarity}")
                if category and category != tag:
                    info_parts.append(f"적응키: {category}")
                info_line = " / ".join(info_parts) if info_parts else "이변 정보: (미상)"
                system_msg += f"\n{info_line}"

            divider = "━" * 20
            system_msg += f"\n{divider}\n{divider}"

            result_title = f"🎲 적응 판정 결과: [[{category or tag or '이변'}]]"
            system_msg += f"\n{result_title}"
            result_line = _build_adaptation_result_line(
                mask,
                defense_success,
                defense_note,
                adapt_pct,
                adapt_new_pct
            )
            if result_line:
                system_msg += f"\n{result_line}"

            # Optional extra flavor line can be added later if needed.
        if bus.doom and bus.doom.get("relief_log"):
            system_msg += f"\n{bus.doom.get('relief_log')}"
        if bus.doom and bus.doom.get("mental_pressure_log"):
            system_msg += f"\n{bus.doom.get('mental_pressure_log')}"
        
        # Integrate Mental impact and log into one line
        if bus.mental:
            mental_parts = []
            if bus.mental.get("impact_log"):
                impact_log = bus.mental.get("impact_log")
                # Extract reason from impact_log: "🧠 정신적 영향: -10 (reason)"
                if "(" in impact_log and ")" in impact_log:
                    reason = impact_log.split("(", 1)[1].rsplit(")", 1)[0]
                    mental_parts.append(reason)
            
            if bus.mental.get("log"):
                mental_parts.append(bus.mental.get("log"))
            
            if mental_parts:
                system_msg += f"\n{' → '.join(mental_parts)}"

        return {
            "game_context": updated_context,
            "directive": "\n".join(directive_parts),
            "system_message": system_msg
        }
