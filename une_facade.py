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
import game_character
import game_world

logger = logging.getLogger("UNE")

def _pick(items):
    return random.choice(items) if items else ""

# =========================================================
# Genre MC Move Tables (Phase 8)
# Key: (position_tier, result) → Korean MC move text
# =========================================================
GENRE_MC_MOVES = {
    "cosmic_horror": {
        ("desperate", "critical_failure"): "현실이 무너진다 — 돌이킬 수 없는 진실이 열린다",
        ("desperate", "failure"): "공포가 실체가 된다 — 되돌릴 수 없는 결과",
        ("desperate", "partial"): "살아남았지만 대가가 크다 — 세계관이 흔들린다",
        ("desperate", "success"): "절망 속 한 줄기 빛 — 그러나 그 빛도 의심스럽다",
        ("desperate", "critical_success"): "불가능한 기적 — 대가는 아직 청구되지 않았다",
        ("risky", "critical_failure"): "최악이 현실이 된다 — 공포가 구체화한다",
        ("risky", "failure"): "상황이 악화된다 — 새로운 공포가 모습을 드러낸다",
        ("risky", "partial"): "일부 성공했지만 — 무언가 알아서는 안 될 것을 알게 되었다",
        ("risky", "success"): "위험을 넘겼다 — 잠시간의 안전",
        ("risky", "critical_success"): "선명한 통찰 — 공포의 정체를 직시하고 살아남았다",
        ("controlled", "critical_failure"): "예상치 못한 반전 — 안전이 무너진다",
        ("controlled", "failure"): "작은 실패가 균열을 만든다 — 불안의 씨앗",
        ("controlled", "partial"): "부분적 성과 — 미묘한 불편함이 남는다",
        ("controlled", "success"): "깔끔한 성공 — 평온이 유지된다",
        ("controlled", "critical_success"): "완벽한 대응 — 공포를 이해의 영역으로 끌어왔다",
    },
    "romance": {
        ("desperate", "critical_failure"): "마음이 드러난 순간, 취약성만 남았다 — 상처가 깊다",
        ("desperate", "failure"): "진심이 전해지지 않았다 — 오해만 깊어진다",
        ("desperate", "partial"): "감정이 닿았지만 타이밍이 아니었다 — 여운이 남는다",
        ("desperate", "success"): "절박한 진심이 통했다 — 관계가 급격히 움직인다",
        ("desperate", "critical_success"): "운명적 순간 — 모든 벽이 무너진다",
        ("risky", "critical_failure"): "결정적 오해가 발생한다 — 관계가 흔들린다",
        ("risky", "failure"): "감정의 엇갈림 — 라이벌이나 장애물이 선명해진다",
        ("risky", "partial"): "마음은 전했지만 완전하지 않다 — 불안이 남는다",
        ("risky", "success"): "감정이 전해졌다 — 관계가 한 걸음 나아간다",
        ("risky", "critical_success"): "완벽한 순간 — 두 사람만의 세계가 열린다",
        ("controlled", "critical_failure"): "안전한 거리에서 예상치 못한 감정이 터진다",
        ("controlled", "failure"): "소소한 실수 — 그러나 감정의 여운",
        ("controlled", "partial"): "일상적 교류 — 미세한 설렘",
        ("controlled", "success"): "자연스러운 친밀함 — 편안한 진전",
        ("controlled", "critical_success"): "완벽한 하모니 — 서로를 깊이 이해하는 순간",
    },
    "comedy": {
        ("desperate", "critical_failure"): "전부 들통 — 숨긴 모든 것이 한꺼번에 공개된다",
        ("desperate", "failure"): "상황이 완전히 통제를 벗어났다 — 그러나 웃기다",
        ("desperate", "partial"): "겨우 수습했지만 새 거짓말이 필요하다",
        ("desperate", "success"): "기적적 수습 — 아무도 믿지 못할 행운",
        ("desperate", "critical_success"): "모든 거짓말이 우연히 진실이 된다",
        ("risky", "critical_failure"): "최악의 타이밍에 최악의 사람이 등장한다",
        ("risky", "failure"): "소동이 커진다 — 목격자가 늘어난다",
        ("risky", "partial"): "절반만 성공 — 나머지 절반이 문제를 만든다",
        ("risky", "success"): "깔끔한 수습 — 잠깐의 안도",
        ("risky", "critical_success"): "예상치 못한 방식으로 완벽하게 해결된다",
        ("controlled", "critical_failure"): "확실한 상황에서 황당한 실패",
        ("controlled", "failure"): "사소한 실수가 나비효과를 일으킨다",
        ("controlled", "partial"): "되긴 됐는데 뭔가 어색하다",
        ("controlled", "success"): "순조로운 진행 — 평화로운 한 때",
        ("controlled", "critical_success"): "모든 것이 완벽하게 맞아떨어진다 — 기분 좋은 놀라움",
    },
    "noir": {
        ("desperate", "critical_failure"): "덫이 닫힌다 — 탈출구 없음",
        ("desperate", "failure"): "진실이 무기가 되어 돌아온다 — 배신의 대가",
        ("desperate", "partial"): "살아남았지만 빚이 생겼다 — 누군가에게 약점을 잡혔다",
        ("desperate", "success"): "어둠 속에서 한 수 앞을 내다봤다 — 위험한 도박의 성공",
        ("desperate", "critical_success"): "모든 퍼즐이 맞아떨어진다 — 그러나 그 대가는?",
        ("risky", "critical_failure"): "증거가 뒤바뀐다 — 사냥꾼이 사냥감이 된다",
        ("risky", "failure"): "수사선이 꼬인다 — 새로운 용의자, 새로운 의혹",
        ("risky", "partial"): "일부 진실에 접근했지만 — 더 큰 비밀이 있다",
        ("risky", "success"): "한 겹을 벗겼다 — 진실에 한 발 더 가까이",
        ("risky", "critical_success"): "결정적 단서 확보 — 퍼즐의 핵심 조각",
        ("controlled", "critical_failure"): "안전하다고 생각한 곳에서 칼이 날아온다",
        ("controlled", "failure"): "사소한 실수가 흔적을 남긴다",
        ("controlled", "partial"): "조용한 진전 — 그러나 감시의 눈이 있다",
        ("controlled", "success"): "계획대로 — 아직은 주도권을 쥐고 있다",
        ("controlled", "critical_success"): "완벽한 수 — 상대방은 움직였다는 것조차 모른다",
    },
    "action": {
        ("desperate", "critical_failure"): "최악의 결과 — 치명적 부상 또는 장비 파괴",
        ("desperate", "failure"): "위기가 실체화된다 — 후퇴할 곳이 없다",
        ("desperate", "partial"): "살아남았지만 상처가 깊다 — 전투 능력 저하",
        ("desperate", "success"): "기사회생 — 절체절명에서의 역전",
        ("desperate", "critical_success"): "전설적 순간 — 불가능을 가능으로",
        ("risky", "critical_failure"): "전세가 역전된다 — 적이 주도권을 잡는다",
        ("risky", "failure"): "공격이 빗나간다 — 적이 반격 기회를 잡는다",
        ("risky", "partial"): "명중했지만 완전하지 않다 — 적도 반격한다",
        ("risky", "success"): "확실한 타격 — 전세가 유리해진다",
        ("risky", "critical_success"): "완벽한 일격 — 적을 압도한다",
        ("controlled", "critical_failure"): "방심의 대가 — 예상치 못한 반격",
        ("controlled", "failure"): "실수로 기회를 놓친다",
        ("controlled", "partial"): "무난한 성과 — 조금 부족하다",
        ("controlled", "success"): "깔끔한 처리 — 전문가다운 수행",
        ("controlled", "critical_success"): "압도적 우위 — 적이 전의를 상실한다",
    },
    "slice_of_life": {
        ("desperate", "critical_failure"): "최악의 타이밍에 모든 것이 엉킨다 — 관계에 금이 간다",
        ("desperate", "failure"): "진심이 전해지지 않았다 — 오해가 깊어진다",
        ("desperate", "partial"): "마음은 닿았지만 방식이 서툴렀다",
        ("desperate", "success"): "서투르지만 진심이 통했다 — 작은 기적",
        ("desperate", "critical_success"): "모든 것이 제자리를 찾는다 — 일상의 따뜻함",
        ("risky", "critical_failure"): "일상의 균형이 무너진다 — 익숙한 것이 낯설어진다",
        ("risky", "failure"): "사소한 것이 꼬인다 — 불편함이 쌓인다",
        ("risky", "partial"): "되긴 됐지만 아쉬움이 남는다",
        ("risky", "success"): "자연스럽게 잘 풀린다 — 소소한 성취",
        ("risky", "critical_success"): "예상치 못한 좋은 일 — 일상의 반짝임",
        ("controlled", "critical_failure"): "확실하다고 생각했는데 뜻밖의 변수",
        ("controlled", "failure"): "사소한 실수 — 웃어넘길 수 있는 정도",
        ("controlled", "partial"): "평범한 하루의 한 장면",
        ("controlled", "success"): "편안한 일상 — 모든 것이 순조롭다",
        ("controlled", "critical_success"): "완벽한 하루 — 일상이 빛나는 순간",
    },
}

def _get_genre_mc_move(genre: str, pos_tier: str, result: str) -> str:
    """장르별 MC Move를 반환. 매칭 없으면 빈 문자열."""
    genre_table = GENRE_MC_MOVES.get(genre, {})
    return genre_table.get((pos_tier, result), "")

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
        mechanic = genres_raw.get("mechanic_profile", {})
        genres = {
            "stage": layers.get("world_setting", []),
            "flavor": layers.get("style_tech", []),
            "lens": layers.get("narrative_tone", []),
            "atmosphere": genres_raw.get("atmosphere_guide", ""),
            "mechanic": mechanic,
        }
    else:
        genres = {
            "stage": [genres_raw[0]] if isinstance(genres_raw, list) and genres_raw else ([str(genres_raw)] if genres_raw else []),
            "flavor": [],
            "lens": [],
            "atmosphere": "",
            "mechanic": {},
        }
    # 하위 호환: str이 들어오면 List로 래핑
    for key in ["stage", "flavor", "lens"]:
        val = genres[key]
        if isinstance(val, str):
            genres[key] = [val] if val else []

    # Active Modules
    active_modules = domain_manager.get_active_modules(channel_id)

    # Lore Summary (V4) + Chunks (V5)
    lore_summary = domain_manager.get_lore_summary_data(channel_id)
    lore_chunks = domain_manager.get_lore_chunks(channel_id)

    # History & Lore Text (V4 - fallback)
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
        "status_effects": p_data.get("status_effects", []) if p_data else [],
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
                "vigor_value": pmem.get("vigor", pmem.get("mental", {})).get("value", 100),
                "composure_value": pmem.get("composure", {}).get("value", 100),
            }
    anchors["all_pcs"] = all_pcs
    anchors["acting_user_id"] = user_id

    # NPC Knowledge & Attitudes (피드백용)
    anchors["stored_npc_knowledge"] = domain_manager.get_npc_knowledge(channel_id)
    anchors["stored_npc_attitudes"] = domain_manager.get_npc_attitudes(channel_id)

    # NPC Roster (Theoria용 이름+역할 요약)
    import npc_manager as _npc_mgr
    _npc_mgr.migrate_npc_fields(channel_id)  # desc→description 통일 + 구조화 필드 자동 추출
    anchors["npc_roster"] = _npc_mgr.get_npc_roster(channel_id)

    # Session Memory (World State Updater 피드백용)
    anchors["session_memory"] = domain_manager.get_session_ai_memory(channel_id)

    # Pending Flashback (회상 대기)
    anchors["pending_flashback"] = domain_manager.get_pending_flashback(channel_id)

    # Bus initialization
    bus = SharedBus()
    bus.doom["value"] = world.get("doom", 40)
    # Doom clocks (local threats)
    clocks = world.get("doom_clocks", [])
    bus.doom["clocks"] = clocks if isinstance(clocks, list) else []

    # Vigor/Composure migration: old "mental" → vigor + composure
    if "mental" in mem and "vigor" not in mem:
        old_val = mem["mental"].get("value", 100)
        old_delta = mem["mental"].get("last_delta", 0)
        mem["vigor"] = {"value": old_val, "last_delta": old_delta}
        mem["composure"] = {"value": old_val, "last_delta": 0}
        del mem["mental"]

    vigor_data = mem.get("vigor", {"value": 100, "last_delta": 0})
    bus.vigor["value"] = vigor_data.get("value", 100)
    bus.vigor["last_delta"] = vigor_data.get("last_delta", 0)
    composure_data = mem.get("composure", {"value": 100, "last_delta": 0})
    bus.composure["value"] = composure_data.get("value", 100)
    bus.composure["last_delta"] = composure_data.get("last_delta", 0)
    adaptation = mem.get("abnormal_exposure", {})
    if not adaptation and p_data:
        adaptation = p_data.get("abnormal_exposure", {})
    bus.vigor["adaptation"] = adaptation

    context = GameContext(
        request=RequestData(
            user_input=user_input,
            genres=genres,
            active_modules=active_modules,
            lore_summary=lore_summary,
            history_text=history_text,
            lore_text=lore_text,
            lore_chunks=lore_chunks
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
    if bus.doom.get("active") or isinstance(bus.doom.get("clocks"), list):
        world = domain_manager.get_world_state(channel_id)
        world["doom"] = bus.doom["value"]
        if isinstance(bus.doom.get("clocks"), list):
            world["doom_clocks"] = bus.doom.get("clocks", [])
        domain_manager.update_world_state(channel_id, world)

    # 2. Participant Data Sync (Vigor, Composure, Adaptation)
    p_data = domain_manager.get_participant_data(channel_id, user_id)
    if p_data:
        mem = p_data.setdefault("ai_memory", {})

        # Remove legacy "mental" key if present
        mem.pop("mental", None)

        for axis_name in ("vigor", "composure"):
            axis_bus = getattr(bus, axis_name)
            if axis_bus.get("active"):
                axis_sys = mem.setdefault(axis_name, {"value": 100, "last_delta": 0})
                axis_sys["value"] = axis_bus["value"]
                axis_sys["last_delta"] = axis_bus.get("last_delta", 0)

                # Trauma Trigger
                if axis_bus.get("trauma_trigger"):
                    passives = mem.setdefault("passives", [])
                    trauma_name = f"트라우마 ({axis_name} 각성)"
                    if not any(p.get("name") == trauma_name for p in passives if isinstance(p, dict)):
                        label = "기력" if axis_name == "vigor" else "평정"
                        passives.append({
                            "name": trauma_name,
                            "tags": ["Trauma", "Hard-to-cure"],
                            "modifier": -5,
                            "desc": f"{label} 붕괴에서 깨어난 트라우마입니다. 모든 판정에 -5 패널티를 받습니다."
                        })

        # Adaptation Updates (vigor에서만 관리)
        updates = bus.vigor.get("adaptation_update")
        if updates:
            mem.setdefault("abnormal_exposure", {}).update(updates)
            p_data.setdefault("abnormal_exposure", {}).update(updates)

        domain_manager.save_participant_data(channel_id, user_id, p_data)

class UniversalNarrativeEngine:
    def __init__(self, client, model_id: str):
        self.pipeline = WaterfallPipeline(client, model_id)

    async def run(self, channel_id: str, user_id: str, user_input: str) -> Dict[str, Any]:
        """단일 PC 행동 처리 (솔로/자동 모드용)"""
        turn_index = game_world.increment_turn_index(channel_id)
        game_character.process_status_expiry(channel_id, user_id, turn_index)
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
        turn_index = game_world.increment_turn_index(channel_id)

        for uid, info in pending_actions.items():
            game_character.process_status_expiry(channel_id, uid, turn_index)
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
        turn_index = game_world.increment_turn_index(channel_id)
        participants = domain_manager.get_domain(channel_id).get("participants", {})
        base_uid = None
        for uid, p in participants.items():
            if p.get("status") == "active":
                base_uid = uid
                break

        if not base_uid:
            return {"game_context": None, "directive": "", "system_message": ""}

        for uid, pdata in participants.items():
            if pdata.get("status") == "active":
                game_character.process_status_expiry(channel_id, uid, turn_index)

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
        vigor_val = bus.vigor.get("value", 100) if bus.vigor else 100
        composure_val = bus.composure.get("value", 100) if bus.composure else 100

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

            # MC Move: Genre-specific first, then generic fallback (PbtA)
            mechanic = context.request.genres.get("mechanic", {})
            primary_genre = mechanic.get("primary_lens", "")

            # Try genre-specific MC move first
            move = _get_genre_mc_move(primary_genre, pos_tier, j_result)

            # Fallback: generic MC moves
            if not move:
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

            # Genre scene hint
            mechanic = context.request.genres.get("mechanic", {})
            primary_genre = mechanic.get("primary_lens", "")

            genre_scene_hints = {
                "cosmic_horror": "Genre: Cosmic Horror — dread builds from the unseen and unknowable",
                "romance": "Genre: Romance — emotional resonance and interpersonal nuance matter most",
                "comedy": "Genre: Comedy — timing, escalation, and social absurdity drive the scene",
                "noir": "Genre: Noir — shadows hide truth, trust is currency, everyone has angles",
                "action": "Genre: Action — momentum, physical stakes, and tactical decisions",
                "slice_of_life": "Genre: Slice of Life — quiet moments carry meaning, change is gradual",
            }
            if primary_genre in genre_scene_hints:
                hints.append(genre_scene_hints[primary_genre])

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

        # ── Layer 3: [Intrusion] — Cypher GM Intrusion (Genre-Aware) ──
        anomaly_sys = ""
        a_triggered = bus.anomaly and bus.anomaly.get("triggered")
        if a_triggered:
            tag = bus.anomaly.get("tag") or "이변"
            intensity = bus.anomaly.get("intensity")
            polarity = bus.anomaly.get("polarity")
            category = bus.anomaly.get("category")
            line = bus.anomaly.get("line", "")

            # Resolve genre for framing
            mechanic = context.request.genres.get("mechanic", {})
            intrusion_genre = mechanic.get("primary_lens", "")

            # Genre-specific anomaly framing
            genre_frames = {
                "cosmic_horror": {"positive": "a glimpse of forbidden understanding", "negative": "the veil thins — reality distorts", "mixed": "revelation wrapped in dread"},
                "romance": {"positive": "a fateful encounter or revelation", "negative": "emotional disruption — hearts shaken", "mixed": "a moment that changes everything"},
                "comedy": {"positive": "absurd luck — things go impossibly right", "negative": "comedic disaster — everything that can go wrong does", "mixed": "the situation escalates hilariously"},
                "noir": {"positive": "an unexpected card to play", "negative": "the net tightens — exposure looms", "mixed": "a new piece enters the game"},
                "action": {"positive": "tactical advantage appears", "negative": "the battlefield shifts against you", "mixed": "chaos reshapes the fight"},
                "slice_of_life": {"positive": "a pleasant surprise in the routine", "negative": "the familiar becomes uncomfortable", "mixed": "change ripples through daily life"},
            }
            default_frame = {"positive": "may serve as opportunity", "negative": "arrives as threat", "mixed": "both opportunity and threat"}
            frame_table = genre_frames.get(intrusion_genre, default_frame)
            polarity_frame = frame_table.get(polarity, frame_table.get("mixed", "shifts the situation"))
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
        if bus.doom and bus.doom.get("clock_log"):
            system_msg += f"\n⏰ {bus.doom.get('clock_log')}"
        if bus.vigor:
            log_parts = []
            if bus.vigor.get("log"):
                log_parts.append(bus.vigor.get("log"))
            if log_parts:
                system_msg += f"\n{' → '.join(log_parts)}"

        # ── Layer 2: [Aspects] — Fate Aspect declaration (Genre-Aware) ──
        aspects = []
        import config as _cfg
        mechanic = context.request.genres.get("mechanic", {})
        primary_axis = mechanic.get("primary_resource") or "vigor"
        primary_val = vigor_val if primary_axis == "vigor" else composure_val

        m_trauma = (bus.vigor and bus.vigor.get("trauma_trigger")) or (bus.composure and bus.composure.get("trauma_trigger"))
        if j_active and a_triggered:
            if j_result in ("critical_failure", "failure"):
                aspects.append("Failure Resonance")
            elif j_result == "critical_success":
                aspects.append("Glory's Shadow")
        if a_triggered and primary_val <= 39:
            erosion_label = "기력 침식" if primary_axis == "vigor" else "평정 균열"
            aspects.append(erosion_label)
        if m_trauma and a_triggered:
            aspects.append("Inner-Outer Convergence")
        if m_trauma and j_active:
            aspects.append("Resurgence")
        if j_result == "critical_failure" and primary_val <= 14:
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
            # Genre-aware doom stage lookup
            import game_world as _gw
            mechanic_doom = context.request.genres.get("mechanic", {})
            primary_genre = mechanic_doom.get("primary_lens", "")
            doom_info = _gw.get_doom_info(doom_val, genre=primary_genre)
            stage_name = doom_info.get("name", "")
            stage_emoji = doom_info.get("emoji", "")

            if doom_val >= 88:
                atmosphere.append(f"Tension Clock {doom_val}% {stage_emoji}[{stage_name}] — about to break")
            elif doom_val >= 76:
                atmosphere.append(f"Tension Clock {doom_val}% {stage_emoji}[{stage_name}] — running out of time")
            elif doom_val >= 63:
                atmosphere.append(f"Tension Clock {doom_val}% {stage_emoji}[{stage_name}] — closing in")
            elif doom_val >= 50:
                atmosphere.append(f"Tension Clock {doom_val}% {stage_emoji}[{stage_name}] — tension fills the air")
            elif doom_val >= 38:
                atmosphere.append(f"Tension Clock {doom_val}% {stage_emoji}[{stage_name}] — uneasy calm")
            elif doom_val >= 25:
                atmosphere.append(f"Tension Clock {doom_val}% {stage_emoji}[{stage_name}] — equilibrium")
            elif doom_val >= 13:
                atmosphere.append(f"Tension Clock {doom_val}% {stage_emoji}[{stage_name}] — relative calm")
            else:
                atmosphere.append(f"Tension Clock {doom_val}% {stage_emoji}[{stage_name}] — tension has receded")

        # Vigor + Composure = 2-axis PC state (only when module active)
        if "mental" in active_modules:
            if vigor_val <= 14:
                atmosphere.append(f"기력 붕괴 ({vigor_val}%) — 한계를 넘어 신체가 무너진다")
            elif vigor_val <= 39:
                atmosphere.append(f"기력 고갈 ({vigor_val}%) — 탈진으로 몸이 버거워진다")
            elif vigor_val <= 69:
                atmosphere.append(f"기력 동요 ({vigor_val}%) — 신체 균형이 흔들린다")

            if composure_val <= 14:
                atmosphere.append(f"평정 붕괴 ({composure_val}%) — 정신이 무너진다")
            elif composure_val <= 39:
                atmosphere.append(f"평정 동요 ({composure_val}%) — 감정이 취약해진다")
            elif composure_val <= 69:
                atmosphere.append(f"평정 흔들림 ({composure_val}%) — 내면이 불안정하다")

        v_trauma = bus.vigor and bus.vigor.get("trauma_trigger")
        c_trauma = bus.composure and bus.composure.get("trauma_trigger")
        if v_trauma:
            atmosphere.append("기력 트라우마 각성 — 벼랑 끝에서 신체가 재점화된다")
        if c_trauma:
            atmosphere.append("평정 트라우마 각성 — 붕괴 직전에서 정신이 재기동된다")

        if atmosphere:
            directive_parts.append("[Atmosphere]: " + " / ".join(atmosphere))

        # ── NPC Autonomous Behavior Triggers (Phase 7) ──
        if bus.dai and bus.dai.get("psyche_states"):
            from npc_autonomous import NPCAutonomousEngine
            triggers = NPCAutonomousEngine.evaluate_triggers(
                psyche_states=bus.dai.get("psyche_states", {}),
                npc_knowledge=bus.dai.get("npc_knowledge", {}),
                npc_attitudes=bus.dai.get("npc_attitudes", {}),
                scene_type=bus.dai.get("scene_type", "normal"),
            )
            auto_directive = NPCAutonomousEngine.build_autonomous_directive(triggers)
            if auto_directive:
                directive_parts.append(auto_directive)

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
            "mental_log": bus.vigor.get("log", "") if bus.vigor else "",
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
