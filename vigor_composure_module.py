"""
Lorekeeper UNE - Vigor/Composure Module (v3.1)
Manages 2-axis PC state: Vigor (physical+will) and Composure (mental+social).
Replaces mental_module.py.
v3.1: 회복 1+2 하이브리드(event_delta 게이트) + 트라우마 dwell 분리/일시 디버프.
"""

import logging
from typing import TYPE_CHECKING
import config

logger = logging.getLogger("VigorComposure")

if TYPE_CHECKING:
    from orchestration_context import GameContext


def _get_stage(val: int) -> int:
    if val >= 70: return 0
    if val >= 40: return 1
    if val >= 15: return 2
    return 3


def _get_primary_axis(context: "GameContext") -> str:
    mechanic = context.request.genres.get("mechanic", {})
    return mechanic.get("primary_resource") or "vigor"


def _extract_active_genre_tags(active_genres) -> set:
    """
    active_genres에서 14개 장르 태그(stage 6 + flavor 4 + lens 4) 추출.
    형식 다양 지원: str / list / dict({stage,flavor,lens} 또는 {layers}).

    Returns: set of tag strings (GENRE_BASELINE_DRAIN 키에 있는 것만).
    """
    known = set(config.GENRE_BASELINE_DRAIN.keys())
    tags = set()

    if isinstance(active_genres, str):
        if active_genres in known:
            tags.add(active_genres)
        return tags

    if isinstance(active_genres, list):
        for g in active_genres:
            if isinstance(g, str) and g in known:
                tags.add(g)
        return tags

    if isinstance(active_genres, dict):
        # stage/flavor/lens 직접 형식
        for key in ("stage", "flavor", "lens"):
            val = active_genres.get(key, [])
            if isinstance(val, list):
                for v in val:
                    if isinstance(v, str) and v in known:
                        tags.add(v)
            elif isinstance(val, str) and val in known:
                tags.add(val)

        # layers 형식
        layers = active_genres.get("layers", {})
        if isinstance(layers, dict):
            for lst in layers.values():
                if isinstance(lst, list):
                    for v in lst:
                        if isinstance(v, str) and v in known:
                            tags.add(v)

        # fallback: 모든 값 순회
        if not tags:
            for v in active_genres.values():
                if isinstance(v, list):
                    for item in v:
                        if isinstance(item, str) and item in known:
                            tags.add(item)
                elif isinstance(v, str) and v in known:
                    tags.add(v)

    return tags


def _compute_baseline_drain(active_genres, scene_type: str) -> dict:
    """
    F: 장르 × 축 + 씬타입 × 축 baseline drain 계산. layer-cap 적용.

    Returns: {"vigor": int (≤0), "composure": int (≤0)}
    """
    tags = _extract_active_genre_tags(active_genres)

    # 같은 axis Y 켜진 layer 수 (layer-cap)
    vigor_layers = set()
    composure_layers = set()
    for tag in tags:
        drain = config.GENRE_BASELINE_DRAIN.get(tag)
        if not drain:
            continue
        # tag가 속한 layer 찾기
        tag_layer = None
        for layer_key, layer_tags in config.GENRE_LAYERS.items():
            if tag in layer_tags:
                tag_layer = layer_key
                break
        if not tag_layer:
            continue
        if drain.get("vigor"):
            vigor_layers.add(tag_layer)
        if drain.get("composure"):
            composure_layers.add(tag_layer)

    genre_v = -len(vigor_layers)  # 최대 -3
    genre_c = -len(composure_layers)

    # 씬타입 baseline
    scene_sig = config.ACTION_BASELINE_DRAIN.get(scene_type, {"vigor": False, "composure": False})
    scene_v = -1 if scene_sig.get("vigor") else 0
    scene_c = -1 if scene_sig.get("composure") else 0

    return {"vigor": genre_v + scene_v, "composure": genre_c + scene_c}


class VigorComposureModule:
    def __init__(self):
        pass

    async def prime(self, context: "GameContext") -> "GameContext":
        """Pre-pass for pipeline order: annotate current stage without consuming deltas."""
        bus = context.shared_bus
        # 채널 토글 OFF면 기력/평형 전체 스킵 (수치 동결)
        if not bus.vigor.get("module_active", True):
            return context
        bus.vigor["stage"] = _get_stage(int(bus.vigor.get("value", 100)))
        bus.composure["stage"] = _get_stage(int(bus.composure.get("value", 100)))
        return context

    async def process(self, context: "GameContext") -> "GameContext":
        bus = context.shared_bus
        # 채널 토글 OFF면 delta 소비/회복/baseline/로그 전부 스킵.
        # "active" 미설정 → sync_from_game_context 쓰기도 스킵 → 수치 동결.
        if not bus.vigor.get("module_active", True):
            return context
        primary_axis = _get_primary_axis(context)

        # Phase 2 F: baseline drain (장르 × 축 + 씬타입 × 축, layer-cap)
        try:
            active_genres = context.request.genres or {}
            scene_type = bus.dai.get("scene_type", "normal") if bus.dai else "normal"
            baseline = _compute_baseline_drain(active_genres, scene_type)
            if baseline["vigor"] != 0:
                bus.vigor["delta"] = bus.vigor.get("delta", 0) + baseline["vigor"]
            if baseline["composure"] != 0:
                bus.composure["delta"] = bus.composure.get("delta", 0) + baseline["composure"]
        except Exception as _e_base:
            logger.warning("[VigorComposure] baseline drain skipped: %s", _e_base)

        # Process each axis
        self._process_axis(context, bus.vigor, "vigor", primary_axis)
        self._process_axis(context, bus.composure, "composure", primary_axis)

        # Phase 2 G: 챕터 종결 refresh (intermission_active 시 max value 60)
        if bus.doom.get("intermission_active"):
            threshold = config.CHAPTER_REFRESH_THRESHOLD
            for ax in (bus.vigor, bus.composure):
                if int(ax.get("value", 100) or 100) < threshold:
                    ax["value"] = threshold

        # Combine logs
        mask = context.get_acting_mask()
        v_val = bus.vigor["value"]
        c_val = bus.composure["value"]
        v_delta = bus.vigor.get("_final_delta", 0)
        c_delta = bus.composure.get("_final_delta", 0)

        log_parts = []
        if v_delta != 0 or c_delta != 0:
            v_sign = f"+{v_delta}" if v_delta > 0 else str(v_delta)
            c_sign = f"+{c_delta}" if c_delta > 0 else str(c_delta)
            log_parts.append(f"{mask}: 💪 활력 {v_sign} → {v_val}/100 | 😌 평형 {c_sign} → {c_val}/100")
        elif bus.vigor.get("active") or bus.composure.get("active"):
            log_parts.append(f"{mask}: 💪 활력 {v_val}/100 | 😌 평형 {c_val}/100 (자연 회복)")

        # Judgment emotion
        v_emo = bus.vigor.get("judgment_emotion", 0)
        c_emo = bus.composure.get("judgment_emotion", 0)
        if v_emo > 0:
            log_parts.append(f" (판정 고양 +{v_emo})")
        elif v_emo < 0:
            log_parts.append(f" (판정 절망 {v_emo})")
        if c_emo and c_emo != v_emo:
            log_parts.append(f" (평형 판정 {'+' if c_emo > 0 else ''}{c_emo})")

        # Rest log
        rest_log = bus.vigor.get("rest_log")
        if rest_log:
            log_parts.append(f"\n{rest_log}")
        c_rest_log = bus.composure.get("rest_log")
        if c_rest_log:
            log_parts.append(f"\n{c_rest_log}")

        # Cascade log
        v_cascade = bus.vigor.get("cascade_drain", 0)
        c_cascade = bus.composure.get("cascade_drain", 0)
        if v_cascade:
            log_parts.append(f"\n🔗 활력 ← 평형 cascade ({v_cascade})")
        if c_cascade:
            log_parts.append(f"\n🔗 평형 ← 활력 cascade ({c_cascade})")

        # Clamping/Trauma
        if bus.vigor.get("_clamped"):
            log_parts.append("\n❗ **충격 완화** (활력 Clamping)")
        if bus.composure.get("_clamped"):
            log_parts.append("\n❗ **충격 완화** (평형 Clamping)")
        if bus.vigor.get("trauma_trigger"):
            log_parts.append("\n✨ **트라우마 각성** (활력 Awakening)")
        if bus.composure.get("trauma_trigger"):
            log_parts.append("\n✨ **트라우마 각성** (평형 Awakening)")

        combined_log = "".join(log_parts)
        bus.vigor["log"] = combined_log
        bus.composure["log"] = combined_log  # Same log for both

        # Delta applied (Pipeline Summary에서 참조)
        bus.vigor["delta_applied"] = v_delta
        bus.composure["delta_applied"] = c_delta

        logger.info("[VigorComposure] vigor=%d(%s%d) composure=%d(%s%d) primary=%s",
                     v_val, "+" if v_delta >= 0 else "", v_delta,
                     c_val, "+" if c_delta >= 0 else "", c_delta,
                     primary_axis)

        # Cleanup temp keys (trauma_trigger는 sync_from_game_context에서 사용 후 정리)
        for axis in (bus.vigor, bus.composure):
            axis.pop("_final_delta", None)
            axis.pop("_clamped", None)
            axis.pop("cascade_drain", None)

        return context

    def _process_axis(self, context: "GameContext", axis: dict, axis_name: str, primary_axis: str):
        """Process a single axis (vigor or composure)."""
        bus = context.shared_bus
        _dai = bus.dai or {}  # V-5 fix: bus.dai None-guard (rest_eval/scene_type 등 .get 크래시 방지)

        # 1. Collect Delta
        delta = axis.get("delta", 0)
        # event_delta: "이번 턴에 실제 사건이 있었나" 신호. AI impact + 판정 감정만 누적.
        # baseline/cascade/status 같은 구조적 상시 드레인은 제외 → 자연회복 게이트가
        # 사건 유무로 판정되게 한다 (구조 드레인이 회복을 영구 봉쇄하던 버그 차단).
        event_delta = 0

        # 1a. Rest Recovery (both axes — composure at reduced rate)
        # activity != "rest"인 다운타임은 orchestration._process_downtime()에서 별도 처리
        rest_eval = _dai.get("rest_eval")
        if rest_eval and rest_eval.get("detected") and rest_eval.get("activity", "rest") == "rest":
            quality = rest_eval.get("quality", "brief")
            base_recovery = config.REST_RECOVERY.get(quality, 10)
            if not rest_eval.get("safe_location", True):
                base_recovery = int(base_recovery * config.REST_UNSAFE_MODIFIER)
            if axis_name != "vigor":
                base_recovery = int(base_recovery * config.REST_COMPOSURE_RATIO)
            if base_recovery > 0:
                delta += base_recovery
                safe_tag = "safe" if rest_eval.get("safe_location", True) else "unsafe"
                axis["rest_log"] = f"💤 휴식({quality}) +{base_recovery} ({safe_tag})"

        # 1b. Judgment Emotional Impact (보조축에 적용 — 주축은 consequence primary_delta가 담당)
        if axis_name != primary_axis and bus.judgment.get("active"):
            j_result = bus.judgment.get("result", "")
            j_emotion = {
                "critical_success": 3,
                "success": 1,
                "partial": 0,
                "failure": -2,
                "critical_failure": -4,
            }.get(j_result, 0)
            if j_emotion != 0:
                delta += j_emotion
                event_delta += j_emotion
                axis["judgment_emotion"] = j_emotion

        # 1c. Cross-Axis Cascade — other axis's bad state drains this axis
        other_name = "composure" if axis_name == "vigor" else "vigor"
        other_bus = getattr(bus, other_name)
        other_stage = other_bus.get("stage", 0)  # prime()에서 설정된 턴 시작 스테이지
        cascade = config.CROSS_AXIS_CASCADE.get(other_stage, 0)
        if cascade != 0:
            delta += cascade
            axis["cascade_drain"] = cascade

        # 1d. Status severity → drain (primary axis만; intimate 씬 제외)
        #     intimate는 주축이 composure로 뒤집히는데, 신체 status("vigor_drain")가 주축으로 라우팅돼
        #     평형을 효과당 무제한 누적 차감(sev3=-15/개)→NSFW에서 평형만 크래시하던 버그. intimate는
        #     이미 시계/판정/스토리텔러 suppress 대상이라 status 차감 제외가 일관됨.
        if axis_name == primary_axis and _dai.get("scene_type") != "intimate":
            from game_character import normalize_status_effects
            raw_effects = (context.narrative_anchors or {}).get("status_effects", [])
            status_effects = normalize_status_effects(raw_effects)
            for eff in status_effects:
                sev = eff.get("severity", 0)
                sev_cfg = config.SEVERITY_EFFECTS.get(sev, {})
                drain = sev_cfg.get("vigor_drain", 0)
                if drain != 0:
                    delta += drain

        # 2. AI-Analyzed Impact (씬타입별 클램프 + 방향 전환 감쇠)
        impact_data = axis.get("impact", {})
        if impact_data.get("applicable", False):
            # 씬타입별 impact 상한: intimate ±8, social ±10, 나머지 ±15
            scene_type = _dai.get("scene_type", "normal")
            _SCENE_IMPACT_CAP = {"intimate": 8, "social": 10, "combat": 15, "normal": 15, "summary": 5}
            cap = _SCENE_IMPACT_CAP.get(scene_type, 15)

            # Phase 2 F: severity enum → 수치 (새 형식). 레거시 delta 필드 폴백.
            severity = impact_data.get("severity")
            if severity is not None:
                raw_delta = config.MENTAL_IMPACT_ENUM_SCALE.get(str(severity).lower(), 0)
            else:
                raw_delta = impact_data.get("delta", 0)
            impact_delta = max(-cap, min(cap, raw_delta))

            # 방향 전환 감쇠: 이전 턴 delta와 반대 방향이면 50% 감쇠 (요요 방지)
            prev_delta = axis.get("last_delta", 0)
            if prev_delta != 0 and impact_delta != 0:
                if (impact_delta > 0 and prev_delta < 0) or (impact_delta < 0 and prev_delta > 0):
                    impact_delta = int(impact_delta * 0.5)
                    logger.debug("[%s] Direction reversal damping: %d → %d", axis_name, impact_data.get("delta", 0), impact_delta)

            delta += impact_delta
            event_delta += impact_delta

        # 2b. Passive Drain Modifiers (theory tag system)
        if delta < 0:
            drain_key = f"{axis_name}_drain"
            passives = (context.narrative_anchors or {}).get("passives", [])
            drain_mult = 1.0
            for passive in passives:
                mods = config.get_passive_modifiers(passive)
                if drain_key in mods:
                    drain_mult *= mods[drain_key]
            drain_mult = max(0.5, min(1.5, drain_mult))  # 극단값 방지
            if drain_mult != 1.0:
                delta = int(delta * drain_mult)

        # 2a. Natural Recovery (1+2 하이브리드): 이번 턴 큰 사건이 없으면(|event_delta| ≤ T)
        #     구조적 드레인(baseline/cascade/status) 여부와 무관하게 +1 트리클.
        #     → 캐스케이드 걸린 조용한 턴에도 회복이 점화되어 일방통행 래칫이 풀린다.
        if abs(event_delta) <= config.NATURAL_RECOVERY_THRESHOLD:
            current_val = axis.get("value", 100)
            if current_val < 100:
                delta += config.NATURAL_RECOVERY_AMOUNT

        # delta == 0 시 stage 조정 없음 종료
        if delta == 0:
            axis["active"] = True
            axis["last_delta"] = 0
            axis["_final_delta"] = 0
            return

        # 3. Inertia (Successive changes amplification)
        last_delta = axis.get("last_delta", 0)
        actual_delta = delta
        if (delta > 0 and last_delta > 0) or (delta < 0 and last_delta < 0):
            actual_delta = int(delta * 1.1)

        # 3b. Per-turn drop 안전캡 (mis-mapping/소스 스택이 한 턴에 축을 폭락시키는 것 방지).
        #     소스가 무엇이든 턴당 낙폭을 scene별 상한으로 묶는다. 초과 시 WARNING 로그 →
        #     다른 씬의 비정상 과차감 관측 채널(별도 검출기 불필요). 낙폭만 제한, 상승/회복은 무제한.
        _scene = _dai.get("scene_type", "normal")
        _drop_cap = config.MAX_AXIS_DROP_PER_TURN.get(_scene, config.MAX_AXIS_DROP_PER_TURN.get("default", 18))
        if actual_delta < -_drop_cap:
            logger.warning("[%s] per-turn drop %d exceeded safety cap -%d (scene=%s) — clamped; sources stacked abnormally.",
                           axis_name, actual_delta, _drop_cap, _scene)
            actual_delta = -_drop_cap

        # 4. Clamping (Max 2 stage drop per turn)
        current_val = axis.get("value", 100)
        current_stage = _get_stage(current_val)

        # V-1 fix: floor 계산을 실제 적용값(actual_delta) 기준으로 통일.
        # 기존엔 base_target/base_stage를 inertia 적용 전 delta로 계산해, inertia로 증폭된
        # actual_delta가 >2단계 낙폭이어도 floor가 그걸 못 막던 불일치.
        target_val = max(0, min(100, current_val + actual_delta))
        base_stage = _get_stage(target_val)
        clamp_floor = target_val

        if base_stage > current_stage + 2:
            limit_stage = current_stage + 2
            floors = {0: 70, 1: 40, 2: 15, 3: 0}
            clamp_floor = floors.get(limit_stage, 0)
        clamped = False
        if actual_delta < 0:
            if target_val < clamp_floor:
                target_val = clamp_floor
                clamped = True

        # 5. Trauma Awakening (Collapse dwell -> Rebound) — delta 부호와 분리.
        #    stage 3(붕괴)에 TRAUMA_DWELL_TURNS 이상 연속으로 머물면 절박한 리바운드 발동.
        #    회복 +1 같은 미동이 90 점프를 유발하던 엉킴 제거.
        trauma_triggered = False
        new_stage = _get_stage(target_val)
        if new_stage == 3:
            axis["stage3_turns"] = axis.get("stage3_turns", 0) + 1
        else:
            axis["stage3_turns"] = 0

        if axis.get("stage3_turns", 0) >= config.TRAUMA_DWELL_TURNS:
            target_val = config.TRAUMA_REBOUND_VALUE
            trauma_triggered = True
            axis["trauma_trigger"] = True
            axis["stage3_turns"] = 0  # 리바운드 후 카운터 리셋 (즉시 재발동 방지)

        # 6. Update
        axis["value"] = target_val
        axis["active"] = True
        axis["last_delta"] = delta
        axis["_final_delta"] = actual_delta
        axis["_clamped"] = clamped
