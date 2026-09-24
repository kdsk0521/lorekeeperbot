"""
Lorekeeper UNE - Vigor/Composure Module (v5.0 — 얇은 리더)

v5.0 (2026-09-06 P8b — **기계화**): 레티어스 결정 "룰의 주인은 자연어, 숫자만 코드의 것".
      08-18 에 기력에서 지운 코드 공식을 **평형에서도 전량 삭제**했다. 삭제 판정 기준 한 줄:
      *장면을 분류해서 숫자를 정하는 코드는 전부 rule 의 몫*. 그래서 사라진 것 —
        baseline drain(장르 14태그 × 씬타입, layer-cap) · cross-axis cascade ·
        자연회복(갭 비례 트리클) · 휴식 회복(rest_eval) · status severity drain ·
        AI mental_impact 소비(방향 전환 감쇠·씬별 캡) · 관성(1.1배) · 낙폭 안전캡 ·
        2단계 Clamping · 챕터 리프레시 · 트라우마 dwell 잔재.
      기력·평형은 이제 **둘 다 custom_vars 의 시스템 선언**이고, 값을 미는 문은 둘뿐이다:
        ① LLM 관측 델타(전담 추출 콜 `extract_outputs.deltas`, 비대칭 캡 7/5)
        ② 코드 소유 쓰기(judgment 감정 · doom defense reward · judgment Effort 선불) —
           셋 다 **사건이 코드에서 확정된 뒤의 대가**지 장면 분류가 아니다.
      이 모듈에 남은 일은 하나 — **읽어서 bus 에 싣는 것**. 26곳/13파일의 소비자가
      `bus.vigor/composure` 를 읽으므로 이름과 모양(value/stage/delta_applied/log)은 그대로다.
      값의 정본 = custom_vars(world_state.custom_var_values["기력"|"평형"]).
"""

import logging
from typing import TYPE_CHECKING, Optional

logger = logging.getLogger("VigorComposure")

if TYPE_CHECKING:
    from orchestration_context import GameContext

_AXES = (("vigor", "기력"), ("composure", "평형"))


def _get_stage(val: int) -> int:
    """값 → 4단계. **값의 함수**라 남는다 — 판정 구간표·표시가 읽는다(계산 자리만 여기)."""
    if val >= 70: return 0
    if val >= 40: return 1
    if val >= 15: return 2
    return 3


def _channel(context: "GameContext") -> str:
    return str((context.narrative_anchors or {}).get("channel_id", "") or "")


def _actor(context: "GameContext") -> str:
    return str((context.narrative_anchors or {}).get("acting_user_id", "") or "")


def _read(context: "GameContext", name: str) -> Optional[int]:
    """레지스트리 한 문. 기능이 꺼졌거나 못 읽으면 None → 호출부가 bus 사본을 유지한다."""
    try:
        import custom_vars as _cv
        return _cv.get_system_value(_channel(context), name, _actor(context))
    except Exception as e:
        logger.debug("[VigorComposure] %s 레지스트리 조회 skip: %s", name, e)
        return None


def _last_change_delta(context: "GameContext", name: str) -> int:
    """이번 턴 레지스트리 이동폭 = 그 축 도장의 delta(같은 턴 것만).

    ★모듈이 델타를 **계산하지 않는다**는 것을 로그가 그대로 드러내야 한다 — 여기 숫자는
      누군가(추출 콜/판정/둠)가 이미 밀어 놓은 결과를 되읽은 것뿐이다.
    """
    try:
        import custom_vars as _cv
        entry = _cv.get_values(_channel(context)).get(name) or {}
        stamps = entry.get("last_change")
        stamp = stamps.get(_actor(context)) if isinstance(stamps, dict) else None
        if not isinstance(stamp, dict):
            return 0
        turn = int((context.shared_bus.dai or {}).get("turn_index", -1))
        if turn >= 0 and int(stamp.get("turn", -1)) != turn:
            return 0
        return int(stamp.get("delta", 0) or 0)
    except Exception as e:
        logger.debug("[VigorComposure] %s 도장 조회 skip: %s", name, e)
        return 0


class VigorComposureModule:
    def __init__(self):
        pass

    async def prime(self, context: "GameContext") -> "GameContext":
        """턴 시작 — 두 축의 현재값·stage 를 bus 에 싣는다. 쓰기 0."""
        self._load(context)
        return context

    async def process(self, context: "GameContext") -> "GameContext":
        """턴 끝 — 값을 **다시 읽고**(그 사이 코드 소유 쓰기가 있었다) 로그 한 줄."""
        bus = context.shared_bus
        if not bus.vigor.get("module_active", True):
            return context      # 채널 토글 OFF = 동결. 읽기도 로그도 없다(구 semantics 보존).

        self._load(context)
        mask = context.get_acting_mask()
        v_val, c_val = int(bus.vigor.get("value", 0)), int(bus.composure.get("value", 0))
        v_delta = int(bus.vigor.get("delta_applied", 0))
        c_delta = int(bus.composure.get("delta_applied", 0))

        if v_delta or c_delta:
            v_sign = f"+{v_delta}" if v_delta > 0 else str(v_delta)
            c_sign = f"+{c_delta}" if c_delta > 0 else str(c_delta)
            log = f"{mask}: 💪 활력 {v_sign} → {v_val}/100 | 😌 평형 {c_sign} → {c_val}/100"
        else:
            log = f"{mask}: 💪 활력 {v_val}/100 | 😌 평형 {c_val}/100"
        bus.vigor["log"] = log
        bus.composure["log"] = log      # 두 축 같은 줄 — 하류 표시가 한 줄만 집는다

        logger.info("[VigorComposure] vigor=%d(%+d) composure=%d(%+d)",
                    v_val, v_delta, c_val, c_delta)
        return context

    def _load(self, context: "GameContext") -> None:
        """레지스트리 → bus 읽기 사본. **이 모듈의 전부**."""
        bus = context.shared_bus
        if not bus.vigor.get("module_active", True):
            return
        for attr, name in _AXES:
            axis = getattr(bus, attr)
            val = _read(context, name)
            if val is not None:
                axis["value"] = int(val)
            # [2026-09-24 감사] `or 100` 이 값 0(탈진·붕괴)을 100 으로 바꿔 stage 가 0(최상)으로
            #   뒤집혔다. 기본값은 값이 **없을 때(None)만** 100.
            _v = axis.get("value")
            axis["stage"] = _get_stage(int(_v) if _v is not None else 100)
            axis["delta_applied"] = _last_change_delta(context, name)
