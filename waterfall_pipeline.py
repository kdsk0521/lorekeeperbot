"""
Lorekeeper UNE - Waterfall Pipeline
Orchestrates the sequence of narrative analysis and mechanical updates.
"""

import logging
import re  # [2026-10-09 E-0] _normalize_soma_enums
from typing import Dict, Any
from orchestration_context import GameContext, SharedBus
from theoria_analyzer import TheoriaAnalyzer
from vigor_composure_module import VigorComposureModule
from judgment_engine import JudgmentEngine
from anomaly_module import AnomalyModule
from doom_module import DoomModule
from judgment_gate import gate_judgment
from emotion_engine import EmotionEngine, EmotionState
from story_director import StoryDirector
import domain_manager
import voice_seed  # [2026-09-22] 세션 NPC 시드 — 게이트·굴림·버퍼(배선 스펙 §2 A·D·F)

logger = logging.getLogger("Waterfall")


# =========================================================
# P6: 인과 구속 힌트 (Flash에 능력 범위 주입)
# =========================================================
def _inject_capability_hints(npc_profiles: dict) -> str:
    """NPC별 능력 범위 요약을 Flash 프롬프트에 주입.
    하드블록 아님. Flash가 참조할 수 있는 힌트만."""
    hints = []
    for name, profile in npc_profiles.items():
        if not isinstance(profile, dict):
            continue
        caps = profile.get("capabilities", {})
        static_traits = profile.get("static_traits", {})

        parts = []
        if caps:
            strengths = caps.get("strengths", "?")
            limits = caps.get("limits", "?")
            parts.append(f"can={strengths} | cannot={limits}")
        if static_traits:
            # [2026-07-28] 기본값은 힌트로 내보내지 않는다 — coping_style이 구조적으로
            # 항상 "adaptive"였던 탓에 **전 NPC에 같은 문구**가 매 턴 나가고 있었다
            # (빈 값보다 나쁘다: Flash가 "이 인물은 적응적/중립적"이라고 읽는다).
            # 패턴 보강으로 이제 실제 값이 나오지만, 기본값이면 침묵이 맞다.
            coping = static_traits.get("coping_style", "")
            moral = static_traits.get("moral_stance", "")
            _bits = []
            if coping and coping != "adaptive":
                _bits.append(f"coping={coping}")
            if moral and moral != "neutral":
                _bits.append(f"moral={moral}")
            if _bits:
                parts.append(", ".join(_bits))

        if parts:
            hints.append(f"[{name}] {' | '.join(parts)}")

    return "\n".join(hints) if hints else ""


def _pc_masks(context) -> set:
    """이번 턴 PC 마스크 집합 — DAI PC 정제(아래 ★단일 정제)와 시드 게이트의 **같은 원천**.

    [2026-09-22 voice_seed §2 A 2] 시드 게이트는 자기 명단을 따로 만들지 않는다.
    한쪽만 고쳐져 두 판정이 갈리는 걸 막으려고 원천을 함수 하나로 묶었다."""
    anchors = getattr(context, "narrative_anchors", None) or {}
    try:
        masks = {p.get("mask") for p in (anchors.get("all_pcs", {}) or {}).values()
                 if isinstance(p, dict) and p.get("mask")}
    except Exception:
        return set()
    masks.discard("")
    return masks


def _pc_registry(context) -> dict:
    """[2026-10-07 Mira PC 방향 P4 → PC 이름 단일화] `!가면` 등록 명부 — {mask: {"aliases": PC 위키 페이지 별칭(옛 가면)}}.

    원천은 domain_manager.pc_registry(채널 participants + PC 페이지 별칭) 하나. 이번 턴 all_pcs 가면이 거기 없으면
    더한다(채널 미상·테스트 앵커). 실패 무해 — 못 읽으면 all_pcs 가면만.
    스펙 analysis_line/PC이름단일화·배경추출PC줄_수리스펙_2026-10-07.md S1."""
    anchors = getattr(context, "narrative_anchors", None) or {}
    ch = str(anchors.get("channel_id", "") or "")
    reg: dict = {}
    if ch:
        try:
            import domain_manager
            reg = dict(domain_manager.pc_registry(ch) or {})
        except Exception:
            reg = {}
    try:
        pcs = anchors.get("all_pcs", {}) or {}
        items = pcs.items() if isinstance(pcs, dict) else []
    except Exception:
        items = []
    for uid, p in items:
        m = str((p or {}).get("mask") or "").strip() if isinstance(p, dict) else ""
        if m and m not in ("PC", "Unknown") and m not in reg:
            al: list = []
            try:
                import wiki_store
                pid = wiki_store.pc_page_id(ch, uid) if ch else None
                pg = wiki_store.get_page(ch, pid) if pid else None
                al = [a for a in ((pg or {}).get("aliases") or []) if isinstance(a, str) and a.strip()]
            except Exception:
                al = []
            reg[m] = {"aliases": al}
    return reg


def _is_pc_key(name, registry: dict) -> bool:
    """키가 PC인가 — 판정은 domain_manager.is_pc_name 하나(`!가면` 이름 규칙, allow_token=False).
    "Adamant"·"Adam Kim"·"아담 박사"는 안 붙는다. 전엔 `n in masks` 정확일치라 "Adam"·"아담"이 통과했다."""
    if not registry or not isinstance(name, str) or not name.strip():
        return False
    try:
        import domain_manager
        return domain_manager.is_pc_name(name, registry)
    except Exception:
        return name in registry


def _match_psyche_key(name: str, ps) -> "str | None":
    """Mira가 적은 인물 이름 → 같은 턴 psyche_states 키. psyche_narrative 병합이 쓰던 대조 그대로
    (정확 → 괄호 앞 같음 → 부분 문자열, 처음 맞는 키). [2026-10-10 별칭 A1] trait_connections도 같이 쓰려고 밖으로 뺐다."""
    if not isinstance(ps, dict) or not ps:
        return None
    if name in ps:
        return name
    _b = name.split("(")[0].strip().lower()
    for _k in ps:
        _kb = _k.split("(")[0].strip().lower()
        if _kb == _b or _b in _k.lower() or _kb in name.lower():
            return _k
    return None


def _align_trait_connections(tc, ps, pc_reg) -> tuple:
    """[2026-10-10 별칭 A1] trait_connections 키를 psyche 키로 맞춘다.
    Mira가 "Bibi"로 적으면 small tells엔 Bibi, 응답 칸엔 비비(Bibi)로 실려 렌더가 두 사람으로 읽을 수 있었다(B_L2 T3).
    - 맞는 키가 있으면 그 키로 옮긴다. 없으면 이름 그대로 둔다.
    - 같은 사람이 정본 키로도 와 있으면 정본 키 줄을 남긴다(순서 무관).
    - 이 시점 analysis의 psyche_states엔 PC 키가 남아 있을 수 있다(사전 전개 정제는 bus.dai 쪽만 걷는다).
      그래서 PC 이름은 옮기지도 않고 옮겨 받지도 않는다. PC 줄은 뒤의 단일 정제가 걷는다.
    돌려주는 것: (새 dict, 옮긴 목록). 바뀐 게 없으면 받은 dict 그대로.
    근거: 파티쳇수정/npc/별칭_지도·스펙_2026-10-10.md §4 A1"""
    if not isinstance(tc, dict) or not tc or not isinstance(ps, dict) or not ps:
        return tc, []
    out, exact_keys, moved, changed = {}, set(), [], False
    for name, val in tc.items():
        tgt, exact = name, name in ps
        if not exact and not _is_pc_key(str(name), pc_reg):
            m = _match_psyche_key(str(name), ps)
            if m and not _is_pc_key(m, pc_reg):
                tgt = m
        if tgt != name:
            changed = True
        if tgt in out:
            if exact and tgt not in exact_keys:
                out[tgt] = val          # 정본 키 줄이 먼저 옮겨 앉은 별칭 줄을 이긴다
                exact_keys.add(tgt)
            continue
        out[tgt] = val
        if exact:
            exact_keys.add(tgt)
        if tgt != name:
            moved.append(f"{name}→{tgt}")
    return (out, moved) if changed else (tc, [])


def _turn_now(context) -> int:
    """[2026-09-22] 시드 TTL·born_turn 시계. 아래 판정 게이트 current_turn 과 같은 읽기 순서
    (세션 메모리 turn_count → world_state turn_index)."""
    anchors = getattr(context, "narrative_anchors", None) or {}
    try:
        t = (anchors.get("session_memory") or {}).get("turn_count", 0)
        if not t:
            t = domain_manager.get_world_state(anchors.get("channel_id", "")).get("turn_index", 0)
        return int(t or 0)
    except Exception:
        return 0


# [2026-10-15 C40] load/save_last_judgment_turn 삭제 — 게이트 Rule 2(쿨다운)가 사라져 읽는 곳 0.
#   world_state 의 옛 "last_judgment_turn" 키는 무해하게 남고 !클리어 때 사라진다.


def judgment_snapshot(j: dict):
    """[2026-09-16 3차 §10.3] turn_snapshot `judgment` dict — 판정이 돈 턴만(게이트가 막은 턴은 gate 만).

    terms 는 0 포함 전 항 — 합 = final − roll(불변식). 판정 요청이 없던 턴은 None."""
    j = j if isinstance(j, dict) else {}
    if not j.get("active"):
        return {"gate": j.get("gate_reason", "")} if j.get("gate_requested") else None
    # [2026-10-15 C42] 4dF — 합 = dice_sum + mod = total(불변식). mod_raw 는 폭에 잘리기 전 원합.
    return {
        "n": j.get("n"), "edge": j.get("edge"), "faces": list(j.get("faces") or []),
        "dropped": j.get("dropped"), "dice_sum": j.get("dice_sum"), "mod": j.get("mod"),
        "mod_raw": j.get("mod_raw"), "total": j.get("total"), "result": j.get("result"),
        "mod_terms": list(j.get("mod_terms") or []), "count_terms": list(j.get("count_terms") or []),
        "edge_terms": list(j.get("edge_terms") or []),
        "active_passives": list(j.get("active_passives") or []),
        "active_items": list(j.get("active_items") or []), "assist": list(j.get("assist") or []),
        "active_strikes": list(j.get("active_strikes") or []),
        "gate": j.get("gate_reason", ""),
    }


def _degrade_stage(bus, stage_name: str, error: Exception) -> None:
    """W5: Record pipeline degradation and apply fallback from config table."""
    try:
        import config as _cfg
        rule = _cfg.PIPELINE_DEGRADATION.get(stage_name, {})
        behavior = rule.get("absent_behavior", "skip")
        logger.warning("[Degradation] %s → %s: %s", stage_name, behavior, error)
        degraded = bus.dai.setdefault("_degraded_stages", [])
        degraded.append({"stage": stage_name, "behavior": behavior, "error": str(error)[:200]})
        # Apply fallback DAI values if present
        for k, v in rule.get("fallback_dai", {}).items():
            if k not in bus.dai or not bus.dai[k]:
                bus.dai[k] = v
    except Exception as inner:
        logger.error("[Degradation] Handler itself failed for %s: %s", stage_name, inner)


# =========================================================
# [2026-08-11 soma 지속] B축 몸 상태 병합 (순수 함수 — I/O 0)
# =========================================================
def _write_emotion_tracker(channel_id, prev_emotions, emotion_results, current_turn, context) -> None:
    """[2026-09-15 §12] world_state `npc_emotion_states` 병합 쓰기 — 본체는 `EmotionEngine.merge_tracked_states`.
    사망 이름 = anchors `npc_inactive`의 dead(down은 유지). 값이 같으면 쓰지 않는다."""
    _inactive = (getattr(context, "narrative_anchors", None) or {}).get("npc_inactive", {}) or {}
    _dead = [n for n, v in _inactive.items() if str(v).lower() == "dead"] if isinstance(_inactive, dict) else []
    merged = EmotionEngine.merge_tracked_states(prev_emotions or {}, emotion_results or {}, current_turn, _dead)
    world = domain_manager.get_world_state(channel_id)
    if world.get("npc_emotion_states") != merged:
        world["npc_emotion_states"] = merged
        domain_manager.update_world_state(channel_id, world)


def _merge_soma_states(prev: dict, psyche_states: dict, current_turn: int) -> tuple[dict, list]:
    """이번 턴 psyche 관측분을 이전 턴 soma 스냅샷에 **NPC별로 병합**하고,
    상태가 실제로 뒤집힌 NPC만 전이 리스트로 돌려준다.

    반환: (새 npc_soma_states, [(npc, from_polyvagal, to_polyvagal,
                                from_dissociation, to_dissociation), ...])

    ★I/O 0 — world_state 읽기/쓰기도, soma_log 적립도 여기서 하지 않는다(호출부 책임).

    [2026-08-02 B축 지속] 왜 스냅샷을 남기나.
      `dissociation` 스키마엔 **"Track across turns"**가 이미 적혀 있는데
      이전 턴 값이 어디에도 남지 않아 **집행 재료 없는 사문 지시**였다.
      (npc_emotion_states엔 Plutchik 8축만, PREVIOUS FRAME엔 위치/에너지/밀도/시선만)
      ★새 게이지를 만들지 않는다 — 이미 있는 enum을 다음 턴에 되돌려줄 뿐.

    [2026-08-11 soma 지속] 통짜 교체 → **NPC별 병합 + since_turn 도장**.
      구 코드는 `world["npc_soma_states"] = _soma_snap`으로 dict를 통째 갈아서
      (a) 이번 턴 psyche에 없는 NPC = **증발**(안 만나면 몸 상태 기억이 사라짐)
      (b) 같은 상태가 언제부터인지 = 알 방법 없음(1턴 창).

    오프스테이지 잔존: 이번 턴 관측분으로 덮되, 못 본 NPC는 그대로 둔다.
      그래서 전이 로그도 **관측 턴에만** 쌓인다 — 잔존은 사건이 아니다.

    시계 문법: A축=등장 / C축=무변화 / **B축=무변화**(같은 상태가 언제부터냐).
      ★무변화면 도장을 안 찍는다 — npc_manager.set_drive_gated L1946과 같은 규율.
        동일 상태에 재도장하면 지속 시계가 매 턴 리셋돼 영원히 임계를 못 넘는다.

    ⚠`psyche_states`는 **PC 제외본**을 넘겨야 한다(execute()의 `_pc_masks_em` 필터 결과).
      bus.dai 원본은 PC를 품고 있어, 잔존 배선과 만나면 PC 몸 상태가 **영구**
      잔류한다(통짜 교체로 매 턴 증발하던 시절엔 무해했다).
    """
    _psy = psyche_states or {}
    _soma_prev = prev if isinstance(prev, dict) else {}
    _soma_snap = {k: dict(v) for k, v in _soma_prev.items()
                  if isinstance(v, dict)}
    _soma_moves = []
    for _sn, _sblk in _psy.items():
        if not isinstance(_sblk, dict):
            continue
        _s = _sblk.get("soma")
        if not isinstance(_s, dict):
            continue
        _keep = {k: _s.get(k) for k in ("polyvagal", "dissociation")
                 if _s.get(k)}
        if not _keep:
            continue
        _old = _soma_prev.get(_sn)
        if not isinstance(_old, dict):
            _old = None
        _same = bool(_old) and all(
            (_old.get(_k) or None) == (_keep.get(_k) or None)
            for _k in ("polyvagal", "dissociation")
        )
        if _same:
            _keep["since_turn"] = int(_old.get("since_turn", current_turn) or 0)
        else:
            _keep["since_turn"] = int(current_turn)
            # 전이 로그는 **관측 턴에만** — 오프스테이지 잔존은 사건이 아니다.
            _soma_moves.append((
                _sn,
                (_old or {}).get("polyvagal"), _keep.get("polyvagal"),
                (_old or {}).get("dissociation"), _keep.get("dissociation"),
            ))
        _soma_snap[_sn] = _keep
    return _soma_snap, _soma_moves



_SOMA_ENUMS = {
    "polyvagal": ("ventral", "sympathetic", "dorsal"),
    "dissociation": ("none", "mild", "moderate", "severe"),
}
_SOMA_ENUM_RE = {k: re.compile(r"\b(" + "|".join(v) + r")\b") for k, v in _SOMA_ENUMS.items()}


# [2026-10-09 SD개편] 비트 주인(_screen_beats·_name_forms_beat·_BEAT_OWNER_RE) 은퇴 — suggested_beats와 함께.


def _normalize_soma_enums(psyche_states) -> list:
    """[2026-10-09 E-0] Theoria soma enum(polyvagal·dissociation)을 소비 전에 enum 값으로 맞춘다. 제자리 수정.

    모델이 4b `Soma(prev): … (held Nt)` 주석이나 판단 근거를 값에 섞는다("ventral (held 3t) — sympathetic flicker …").
    그대로 두면 ① emotion_engine `POLYVAGAL_BIAS.get()`이 {}를 돌려 편향이 소리 없이 빠지고 ② `==` 분기가 빗나가며
    ③ soma 스냅샷이 깨진 문자열을 저장해 다음 턴 4b로 되먹인다(같은 꼴이 턴마다 이어진다).
    규칙: 값 안에서 맨 앞에 나오는 enum 낱말을 쓴다. 하나도 없으면 키를 뺀다(모델이 안 쓴 것과 같다 — 스냅샷은 지난 값 유지).
    문자열 아닌 값(None 등)은 건드리지 않는다. 반환: [(인물, 키, 옛 값, 새 값|None)] — 로그용.
    근거: analysis_line/묶음A_do칸착지_측정_2026-10-09 §5.7·§6.2 (10-07 이후 Theoria 167개 중 9개 enum 밖).
    """
    fixed = []
    if not isinstance(psyche_states, dict):
        return fixed
    for name, blk in psyche_states.items():
        soma = blk.get("soma") if isinstance(blk, dict) else None
        if not isinstance(soma, dict):
            continue
        for key, vals in _SOMA_ENUMS.items():
            v = soma.get(key)
            if not isinstance(v, str):
                continue
            low = v.strip().lower()
            if low in vals:
                if low != v:
                    soma[key] = low
                continue
            m = _SOMA_ENUM_RE[key].search(low)
            new = m.group(1) if m else None
            if new:
                soma[key] = new
            else:
                soma.pop(key, None)
            fixed.append((name, key, v, new))
    return fixed


_PEPLAU_PHASES = ("orientation", "identification", "exploitation", "resolution")
_ATTACH_KINDS = ("secure", "anxious", "avoidant", "disorganized")


def _merge_relation_states(prev: dict, psyche_states: dict, current_turn: int) -> dict:
    """[2026-10-07 a묶음 A4] 이번 턴 relation 판독(phase·attachment)을 지난 턴 스냅샷에 NPC별로 병합.

    `_merge_soma_states`와 같은 규율 — I/O 0, 못 본 NPC는 그대로(오프스테이지 잔존), phase가 **바뀐 턴에만**
    since_turn 도장(같은 값 재도장 = 지속 시계 리셋). enum 밖 값은 버린다. PC 제외본을 넘길 것.
    """
    _prev = prev if isinstance(prev, dict) else {}
    snap = {k: dict(v) for k, v in _prev.items() if isinstance(v, dict)}
    for name, blk in (psyche_states or {}).items():
        rel = blk.get("relation") if isinstance(blk, dict) else None
        if not isinstance(rel, dict):
            continue
        ph = str(rel.get("phase") or "").strip().lower()
        at = str(rel.get("attachment") or "").strip().lower()
        ph = ph if ph in _PEPLAU_PHASES else ""
        at = at if at in _ATTACH_KINDS else ""
        if not ph and not at:
            continue
        old = _prev.get(name) if isinstance(_prev.get(name), dict) else {}
        new = dict(old)
        if ph:
            if ph != old.get("phase") or old.get("since_turn") is None:
                new["since_turn"] = int(current_turn)
            new["phase"] = ph
        if at:
            new["attachment"] = at
        snap[name] = new
    return snap


def _relation_view(psyche_states, stored: dict = None) -> dict:
    """[2026-09-15 관계 통합] psyche_states[NPC].relation → {NPC: {attitude, reason, bond, trajectory}} 파생.
    bond 없으면 옛 value 폴백. trajectory = 저장 엣지 대비 이번 판독의 부호(domain_manager.trajectory_from_delta).
    저장하지 않는다."""
    out = {}
    if not isinstance(psyche_states, dict):
        return out
    try:
        from domain_manager import attitude_from_bond, trajectory_from_delta
    except Exception:
        return out
    for _n, _st in psyche_states.items():
        _rel = _st.get("relation") if isinstance(_st, dict) else None
        if not isinstance(_n, str) or not isinstance(_rel, dict):
            continue
        _b = _rel.get("bond", _rel.get("value"))
        try:
            _b = int(float(_b)) if _b is not None and not isinstance(_b, bool) else None
        except (TypeError, ValueError):
            _b = None
        if _b is None:
            continue
        # depth/tension은 싣지 않는다 — 소비부(une_facade 자율 병합)가 저장 엣지 값으로 채운다(구 동작 유지).
        _prev = (stored or {}).get(_n) if isinstance(stored, dict) else None
        _traj = (trajectory_from_delta(_b - int(_prev.get("bond", _prev.get("depth", 0)) or 0))
                 if isinstance(_prev, dict) else "stable")
        out[_n] = {"attitude": attitude_from_bond(_b), "reason": str(_rel.get("descriptor") or ""),
                   "bond": _b, "trajectory": _traj}
    return out

class WaterfallPipeline:
    def __init__(self, client, model_id: str):
        self.theoria = TheoriaAnalyzer(client, model_id)
        # These will be lazy loaded or injected
        self.judgment = None
        self.doom = None
        self.anomaly = None
        self.vigor_composure = None

    def _ensure_bus_schema(self, bus: SharedBus) -> None:
        """
        Ensure SharedBus has required dict fields without overwriting existing values.
        This is a schema guard for DLC on/off during runtime.
        """
        defaults = SharedBus()
        for key in ("dai", "judgment", "doom", "anomaly", "vigor", "composure", "emotion"):
            current = getattr(bus, key, None)
            if current is None or not isinstance(current, dict):
                setattr(bus, key, getattr(defaults, key))

    async def execute(self, context: GameContext) -> GameContext:
        """Analysis -> Mental(pre) -> Judgment -> Storyteller -> Doom -> Mental(sync)

        All core modules (Doom, Anomaly, Mental, Judgment) are always active.
        Judgment trigger is gated by judgment_gate (N1).

        Data-flow map (SharedBus ownership):
        - Theoria: bus.dai (all analysis), bus.judgment, bus.doom
        - Mental(pre): stage snapshot only (no delta consumption)
        - Judgment: resolves action, writes consequences (doom.delta, primary axis delta, clock effects, momentum)
        - Storyteller: bus.anomaly.triggered/tag/decision (narrative only, no deltas)
        - Doom: bus.doom.value/delta/log, consumes judgment doom_delta, may write vigor/composure pressure delta
        - Mental(sync): consumes ALL accumulated deltas (judgment + doom pressure + rest + status)
        """

        self._ensure_bus_schema(context.shared_bus)
        
        # 1. [A안 v2 2026-07-02 직렬] 추출 콜(냉, 기계 필드) → 서사 콜(온, 방향+심리해석 필드).
        # 병렬→직렬 전환(레티어스 "지연 감수"): 서사 콜이 이번 턴 추출 다이제스트를 입력으로 받아
        # 동턴 정합 확보 — 2차 이사(deep_read 등 심리 해석층)의 전제. W5 강하는 콜별 독립 유지.
        bus = context.shared_bus
        try:
            analysis = await self.theoria.analyze_input(context)
        except Exception as e:
            _degrade_stage(bus, "theoria_analysis", e)
            analysis = {}

        # Safety: Gemini가 JSON 배열을 반환하면 첫 번째 요소를 사용
        if isinstance(analysis, list):
            logger.warning(f"[Theoria] Returned list instead of dict, extracting first element")
            analysis = analysis[0] if analysis and isinstance(analysis[0], dict) else {}
        if not isinstance(analysis, dict):
            logger.error(f"[Theoria] Invalid response type: {type(analysis)}")
            analysis = {}
        # [2026-09-24 감사] analyze_input 은 파싱 실패·빈 응답·API 예외를 raise 하지 않고
        #   `{"error": ...}` 로 반환한다 → 위 except 를 안 타서 W5 강하 기록(_degraded_stages·Slot 16 고지)이
        #   주된 실패 모드에서 침묵했다. 오류 전용 dict 는 강하로 기록하고 빈 분석으로 바꾼다.
        if set(analysis.keys()) == {"error"}:
            _degrade_stage(bus, "theoria_analysis", RuntimeError(str(analysis.get("error"))[:200]))
            analysis = {}
        # [2026-10-09 E-0] soma enum 정규화 — 감정 엔진·soma 스냅샷·4b 되먹임 전부 이 값을 본다(소비 전 한 자리).
        try:
            for _sn, _sk, _old, _new in _normalize_soma_enums(analysis.get("psyche_states")):
                logger.info("[Soma] %s.%s 정규화: %r → %s", _sn, _sk, str(_old)[:80], _new or "(뺌)")
        except Exception as _e_se:
            logger.warning("[Soma] enum 정규화 건너뜀: %s", _e_se)

        # [2026-09-22 voice_seed §2 A] 미등록 신규 인물 게이트 + 굴림 — 서사 콜 **앞**.
        #   새 콜 0(전부 코드). 굴림은 서사 콜 입력(§2 C NEWCOMERS 블록)의 재료가 된다.
        #   시드는 best-effort다 — 어디서 터져도 예외를 삼키고 턴은 그대로 간다.
        _seed_ch = (context.narrative_anchors or {}).get("channel_id", "")
        _seed_turn = _turn_now(context)
        _seed_rolls = {}
        try:
            voice_seed.tick(_seed_ch, _seed_turn)          # TTL 퍼지(게이트 직전, §2 F)
            _seed_rolls = voice_seed.gate_and_roll(
                _seed_ch, analysis.get("RelevantNPCs"), _pc_masks(context)) or {}
            # 빈 dict면 앵커 키 자체를 안 넣는다 — 서사 콜 프롬프트 순증 0(§2 C).
            if _seed_rolls and isinstance(context.narrative_anchors, dict):
                context.narrative_anchors["newcomer_rolls"] = _seed_rolls
        except Exception as _e_seed:
            logger.warning("[Seed] gate/roll skipped: %s", _e_seed)
            _seed_rolls = {}

        # ===== [2026-10-01 2단계] 판정 먼저 — 추출 → 게이트 → prime → 판정 → 서사 콜 =====
        #   서사 콜(Mira)이 굴림 결과를 알고 앞날 필드(손·훅·스레드·cannot)와 roll_outcome 을 쓰게 한다.
        #   의존 실독(스펙 §1): 판정은 추출 몫만 읽는다(서사 콜 값은 디스코드 훅 줄 하나 — 사후 처리로 뺐다).
        #   판정 엔진엔 LLM 콜이 없어 지연은 그대로다. 감정 엔진은 deep_read(서사 몫)를 읽으므로 서사 콜 뒤에 남는다.
        # (사전 전개) 판정이 읽는 추출 몫 9키(C42 time_flow 추가)를 bus.dai 에 먼저 싣는다 — 형 정규화 + PC 정제는 아래 본 전개와 같은 규칙.
        #   본 전개(서사 합류 뒤)가 같은 값으로 다시 덮어쓴다(무해). 판정이 bus.dai 에 쓰는 키(turn_index — C42 로 effort_failed 퇴역)는
        #   본 전개가 건드리지 않는다.
        _pc_reg = _pc_registry(context)   # [2026-10-07 P4] `!가면` 등록 명부 — 사전 전개·단일 정제가 같이 쓴다
        try:
            for _pk, _src, _ty, _dflt in (("scene_type", "SceneType", str, "normal"),
                                          ("current_location", "CurrentLocation", str, ""),
                                          ("psyche_states", "psyche_states", dict, None),
                                          ("relevant_npcs", "RelevantNPCs", list, None),
                                          ("aspects", "Aspects", list, None),
                                          ("action_meta", "action_meta", dict, None),
                                          ("position", "Position", dict, None),
                                          ("effect", "Effect", dict, None),
                                          # [2026-10-15 C42] 판정 개수의 "시간을 들임"(−1)이 판정 앞에서 읽는다.
                                          ("time_flow", "time_flow", dict, None)):
                _pv = analysis.get(_src)
                if not isinstance(_pv, _ty):
                    _pv = _ty() if _dflt is None else _dflt
                bus.dai[_pk] = _pv
            # [2026-10-10 던지는판 A1·B1] Storyteller(energy·quality_flags)와 NPC 자율(지식·태도)이 서사 콜 앞으로 와서 같이 싣는다.
            #   본 전개가 같은 값으로 다시 쓴다(무해). npc_attitudes는 여기선 정렬(align_theoria_relations, DB 쓰기) 없이
            #   읽기만 — 자율이 보는 depth·tension은 원래 저장값이다. 정렬과 재계산은 본 전개 자리 그대로.
            bus.dai["energy_direction"] = analysis.get("EnergyDirection", "idle") or "idle"
            _qf_pre = analysis.get("QualityFlags")
            bus.dai["quality_flags"] = _qf_pre if isinstance(_qf_pre, dict) else {}
            _nk_pre = analysis.get("NPCKnowledge")
            bus.dai["npc_knowledge"] = _nk_pre if isinstance(_nk_pre, dict) else {}
            bus.dai["npc_attitudes"] = _relation_view(
                bus.dai["psyche_states"], (context.narrative_anchors or {}).get("stored_npc_attitudes"))
            # [2026-10-07 P4] 정확일치 → `!가면` 이름 규칙(_is_pc_key). 명부는 아래 단일 정제와 같은 것을 쓴다.
            if _pc_reg:
                bus.dai["psyche_states"] = {k: v for k, v in bus.dai["psyche_states"].items() if not _is_pc_key(k, _pc_reg)}
                bus.dai["relevant_npcs"] = [n for n in bus.dai["relevant_npcs"] if not _is_pc_key(n, _pc_reg)]
                for _pk2 in ("npc_knowledge", "npc_attitudes"):
                    bus.dai[_pk2] = {k: v for k, v in bus.dai[_pk2].items() if not _is_pc_key(k, _pc_reg)}
        except Exception as _e_pre:
            logger.warning(f"[Judgment-first] pre-map skipped: {_e_pre}")

        # N1: Judgment Gate — Flash의 needs_judgment를 코드 게이트로 검증
        # [2026-10-01 2단계] 자리 이동: 전개 뒤 → 서사 콜 앞(판정 먼저). 읽는 것은 전부 추출 몫(needs_judgment·action_meta·
        #   asset_evaluation)이라 그대로 옮겼다. narrative_hook 대입만 서사 콜 뒤(훅 사후 처리)로 뺐다.
        raw_needs = analysis.get("needs_judgment", False)
        # [2026-10-15 C40] 쿨다운 장부 읽기 삭제(연속 판정 허용). current_turn 은 아래 turn_index 적재에 쓴다.
        current_turn = (context.narrative_anchors or {}).get(
            "session_memory", {}
        ).get("turn_count", 0) or domain_manager.get_world_state(
            (context.narrative_anchors or {}).get("channel_id", "")
        ).get("turn_index", 0)

        # [2026-06-11 소비자 감사 #1] turn_index 배선 — bus.dai["turn_index"]를 아무도 안 실어
        # 항상 0이었음 → doom 시계 fade 7개 읽기 + une_facade 퀘스트 stale archive가 0 기반 동작
        # (staleness 트리거 사망). 게이트 계산용 current_turn을 그대로 적재.
        # 주의: 부활 첫 턴에 묵은 퀘스트 일괄 archive는 정상 동작.
        bus.dai["turn_index"] = current_turn

        final_needs, gate_reason = gate_judgment(
            user_input=context.request.user_input,
            flash_needs_judgment=raw_needs,
        )

        bus.judgment["active"] = final_needs
        bus.judgment["gate_reason"] = gate_reason
        bus.judgment["gate_requested"] = bool(raw_needs)
        if final_needs:
            bus.judgment["meta"] = analysis.get("action_meta") or {}  # [07-27] 명시 null 방어
            eval_data = analysis.get("asset_evaluation") or {}  # [07-19] 명시 null 방어
            bus.judgment["eval"] = eval_data
            # [2026-10-15 C42] modifications(Theoria 자유 ±숫자) 퇴역 — 칸별로 나뉘어 코드가 센다.

        # 2. Mental Pre-pass: annotate current stage for downstream modules. [2026-10-01 2단계: 서사 콜 앞으로]
        try:
            self.vigor_composure = VigorComposureModule()
            context = await self.vigor_composure.prime(context)
        except Exception as e:
            _degrade_stage(bus, "vigor_composure", e)

        # 3. Judgment (gated by Flash needs_judgment + judgment_gate)
        if bus.judgment["active"]:
            try:
                self.judgment = JudgmentEngine(self.theoria.client, self.theoria.model_id)
                context = await self.judgment.process(context)
            except Exception as e:
                bus.judgment["active"] = False
                _degrade_stage(bus, "judgment_engine", e)

        # ===== [2026-10-10 던지는판 A1] 이변 제안 전개 + Storyteller — 서사 콜(Mira) 앞 =====
        #   Storyteller가 읽는 것은 전부 추출 몫(제안·조건·energy·quality_flags·장면·위치)과 판정 활성·間 상태다. Mira가 이번 턴
        #   터지는 사건·전조를 알고 응답·hook을 쓰게 앞으로 옮겼다. bus.anomaly 블록은 **옮김**(뒤 본 전개에서 지움).
        #   스펙 analysis_line/던지는판·NPC자율_스펙_2026-10-10 §2 A.
        # Anomaly Profile 연동
        anomaly_profile = analysis.get("anomaly_profile") or {}
        if isinstance(anomaly_profile, dict):
            tag = anomaly_profile.get("trigger") or ""
            category = anomaly_profile.get("category") or ""
            intensity = anomaly_profile.get("intensity") or ""
            polarity = anomaly_profile.get("polarity") or ""
            line = anomaly_profile.get("line") or ""
            reason = anomaly_profile.get("reason") or ""

            if tag:
                bus.anomaly["tag"] = tag
            if category:
                bus.anomaly["category"] = category
            if intensity:
                bus.anomaly["intensity"] = intensity
            if polarity:
                bus.anomaly["polarity"] = polarity
            if line:
                bus.anomaly["line"] = line
            if reason:
                bus.anomaly["reason"] = reason
            # [2026-10-10 큐 이어짐] 이 제안이 잇는 실의 id(E# 대기 사건 / C# 서 있는 조건). 길잡이는 anomaly_module 입구.
            _cont = str(anomaly_profile.get("continues") or "").strip().upper()
            if re.fullmatch(r"[EC]\d{1,2}", _cont):
                bus.anomaly["continues"] = _cont

        # Condition resolved (from Flash analysis)
        cond_resolved = analysis.get("condition_resolved")
        if isinstance(cond_resolved, list) and cond_resolved:
            bus.anomaly["condition_resolved"] = cond_resolved

        # Condition updates — severity transition (from Flash analysis)
        cond_updates = analysis.get("condition_updates")
        if isinstance(cond_updates, list) and cond_updates:
            bus.anomaly["condition_updates"] = cond_updates

        # Event location override (from Flash anomaly_profile)
        if isinstance(anomaly_profile, dict):
            event_location = (anomaly_profile.get("location") or "").strip()
            if event_location:
                bus.anomaly["location"] = event_location

        # Normalize defaults for downstream use
        if bus.anomaly.get("tag") and not bus.anomaly.get("category"):
            bus.anomaly["category"] = bus.anomaly.get("tag")
        if not bus.anomaly.get("intensity"):
            bus.anomaly["intensity"] = "Mid"
        if not bus.anomaly.get("polarity"):
            bus.anomaly["polarity"] = "mixed"
        # Default line for a Flash/Theoria-proposed anomaly that has a tag but no line.
        # (Seed-based fallback 제거 2026-07-09 이후로 이 경로는 진짜 Flash anomaly에만 적용된다.)
        if bus.anomaly.get("tag") and not bus.anomaly.get("line"):
            bus.anomaly["line"] = f"{bus.anomaly['tag']}의 기운이 감돈다."

        # 4. Storyteller: inject state + set potential
        bus.anomaly["potential"] = True
        _st_ch = (context.narrative_anchors or {}).get("channel_id", "")
        if _st_ch:
            st_state = domain_manager.get_storyteller_state(_st_ch)
            bus.anomaly["_storyteller_state"] = st_state
            bus.anomaly["_current_turn"] = domain_manager.get_world_state(_st_ch).get("turn_index", 0)
            bus.anomaly["_channel_id"] = _st_ch

        # 5. Storyteller Decision
        if bus.anomaly.get("potential"):
            try:
                self.anomaly = AnomalyModule(self.theoria.client, self.theoria.model_id)
                context = await self.anomaly.process(context)
            except Exception as e:
                bus.anomaly["triggered"] = False
                _degrade_stage(bus, "anomaly_module", e)

        # ===== [2026-10-10 던지는판 B1] NPC 자율 평가 — Mira 앞. 렌더 지시([NPC Autonomous Behavior]) 은퇴, Mira 재료(pull)로 =====
        #   bus.dai['autonomous_triggers'](fg 선별·수면 깊이)·쿨다운·autonomy_log는 그대로. pull은 서사 콜 추출 다이제스트 인물 줄로.
        try:
            import npc_autonomous as _na
            _pulls = _na.evaluate_for_turn(bus, context)
            if _pulls:
                analysis["_autonomy_pulls"] = _pulls
        except Exception as e:
            _degrade_stage(bus, "npc_autonomy", e)

        # 서사 콜에 넘길 굴림 사실 — 게이트 뒤 active 가 정본(Flash 원 요청이 아니라). 라벨·위치 단계는 theoria 가 판정층과
        #   같은 사전으로 만든다.
        _roll = {"active": bool(bus.judgment.get("active")),
                 "result": str(bus.judgment.get("result") or "") if bus.judgment.get("active") else "",
                 "position_value": (bus.dai.get("position") or {}).get("value", 0.5)
                 if isinstance(bus.dai.get("position"), dict) else 0.5}

        try:
            narrative = await self.theoria.analyze_narrative(context, extract=analysis, roll=_roll)
        except Exception as e:
            _degrade_stage(bus, "narrative_analysis", e)
            narrative = {}
        if not isinstance(narrative, dict):
            narrative = {}

        # [A안] 서사 콜 결과 합류 — 서사 필드는 narrative 콜이 소유 (추출 스키마에서 제거됨).
        # 서사 콜 실패 시 키 부재 → 아래 전개가 기존 디폴트({}/[]/None) 적용 = 현행 강하와 동일 동작.
        if narrative:
            _merge_keys = ("narrative_chain", "narrative_hook",   # [2026-10-09 SD개편] suggested_beats 은퇴
                           "open_invitations",  # [H9 2026-07-18] 플레이어향 전방 affordance
                           "offscreen_trace", "scene_register", "trait_connections",
                           "newcomer_seeds",  # [2026-09-22 voice_seed §2 D 1] 여기 빠지면 증발한다
                           "roll_outcome")    # [2026-10-01 2단계] 굴림 결과의 장면 구체화(Mira) — 굴림 없는 턴은 null
            for _nk in _merge_keys:
                _nv = narrative.get(_nk)
                if _nv is not None:
                    analysis[_nk] = _nv

            # [2차 이사] psyche_narrative → psyche_states per-NPC 병합
            # (deep_read/resurfacing=톱레벨, value_conflict=relation 내부 — 하류 소비 형태 그대로)
            _pn = narrative.get("psyche_narrative")
            _ps = analysis.get("psyche_states")
            if isinstance(_pn, dict) and isinstance(_ps, dict) and _ps:
                def _match_npc(name: str):
                    return _match_psyche_key(name, _ps)   # [2026-10-10 별칭 A1] 대조는 모듈 함수로 — 규칙 그대로
                for _pn_name, _pn_blk in _pn.items():
                    if not isinstance(_pn_blk, dict):
                        continue
                    _tgt = _match_npc(str(_pn_name))
                    if not _tgt or not isinstance(_ps.get(_tgt), dict):
                        continue
                    if _pn_blk.get("deep_read"):
                        _ps[_tgt]["deep_read"] = _pn_blk["deep_read"]
                    if _pn_blk.get("resurfacing") is not None:
                        _ps[_tgt]["resurfacing"] = _pn_blk["resurfacing"]
                    if _pn_blk.get("value_conflict") is not None:
                        _rel = _ps[_tgt].setdefault("relation", {})
                        if isinstance(_rel, dict):
                            _rel["value_conflict"] = _pn_blk["value_conflict"]
                    # [2026-07-22 카드1] pressure 병합 — LLM이 명시 null로 줄 수도 있으므로 setdefault 금지·isinstance 정규화.
                    # [2026-10-09 압력 want·can] 칸 = drives(want 꼴) + can. 옛 cannot은 스키마에서 빠졌다.
                    _pr = _pn_blk.get("pressure")
                    if isinstance(_pr, dict):
                        _drives = _pr.get("drives")
                        _can = _pr.get("can")
                        _clean = {}
                        if isinstance(_drives, str) and _drives.strip() and _drives.strip().lower() != "null":
                            _clean["drives"] = _drives.strip()
                        if isinstance(_can, str) and _can.strip() and _can.strip().lower() != "null":
                            _clean["can"] = _can.strip()
                        if _clean:
                            _ps[_tgt]["pressure"] = _clean
                    # [2026-10-09 반응주인] response = 이번 턴 이 몸이 입력에 답하는 것(굴림 뒤, 서사 콜이 주인). 저장 안 함(pressure와 같다).
                    _resp = _pn_blk.get("response")
                    if isinstance(_resp, str) and _resp.strip() and _resp.strip().lower() != "null":
                        _ps[_tgt]["response"] = _resp.strip()
                    # [2026-10-10 칸이관] Theoria에서 넘어온 네 칸 — 시트를 보는 Mira가 쓰고, 먹는 쪽이 읽던 자리 그대로 앉힌다.
                    #   self_opacity → psyche(감정 surprise 약가산·렌더 거울), logos_layer → relation(Slot 17 dialogue direction 핵심),
                    #   starts_as·friction_starts → relation(관계 시드 — 아래 align_theoria_relations가 Mira 뒤에 읽는다).
                    #   근거: analysis_line/Theoria→Mira_넘길칸_지도_2026-10-10
                    def _txt(v):
                        return v.strip() if isinstance(v, str) and v.strip() and v.strip().lower() != "null" else ""
                    _so = _txt(_pn_blk.get("self_opacity"))
                    if _so:
                        _psy = _ps[_tgt].setdefault("psyche", {})
                        if isinstance(_psy, dict):
                            _psy["self_opacity"] = _so
                    _rel2 = _ps[_tgt].setdefault("relation", {})
                    if isinstance(_rel2, dict):
                        for _rk in ("logos_layer", "starts_as", "friction_starts"):
                            _rv = _txt(_pn_blk.get(_rk))
                            if _rv:
                                _rel2[_rk] = _rv.lower() if _rk != "logos_layer" else _rv

            # [2026-10-10 별칭 A1] trait_connections 키도 psyche 키로 — 위 psyche_narrative와 같은 대조. small tells 줄이 정본 키로 나간다.
            _tc_al, _tc_moved = _align_trait_connections(analysis.get("trait_connections"), _ps, _pc_reg)
            if _tc_moved:
                analysis["trait_connections"] = _tc_al
                logger.info(f"[Narrative] trait_connections 키 맞춤: {', '.join(_tc_moved)}")
            elif _tc_al is not analysis.get("trait_connections"):
                analysis["trait_connections"] = _tc_al

            # [2026-10-10 칸이관] 친밀 분석의 인물 칸(desire_type·dual_control·power_dynamic·body_memory·post_encounter_prediction)은
            #   Mira 몫 — Theoria IA(window_check)에 얹는다. 아래 bus.dai["intimacy_analysis"] 전개·PC 정제가 그대로 받는다.
            _mi = narrative.get("intimacy")
            if isinstance(_mi, dict) and _mi:
                _tia = analysis.get("IntimacyAnalysis")
                _tia = dict(_tia) if isinstance(_tia, dict) else {}
                for _ik in ("desire_type", "dual_control", "power_dynamic", "body_memory", "post_encounter_prediction"):
                    if _mi.get(_ik) is not None:
                        _tia[_ik] = _mi[_ik]
                analysis["IntimacyAnalysis"] = _tia
            elif str(analysis.get("SceneType") or "").strip().lower() == "intimate":
                logger.info("[빈칸] Mira intimacy null (SceneType=intimate)")
            logger.info("[Narrative] merged: "
                        + ", ".join(k for k in _merge_keys if narrative.get(k) is not None)
                        + (f" + psyche_narrative({len(_pn)})" if isinstance(_pn, dict) and _pn else ""))

        # Store ALL Theoria results in SharedBus.dai (replaces nvc_result)
        bus.dai["input_analysis"] = analysis.get("InputAnalysis", {})
        bus.dai["observation"] = analysis.get("Observation", "")
        bus.dai["user_intent"] = analysis.get("UserIntent", "")
        bus.dai["current_location"] = analysis.get("CurrentLocation", "")
        # [2026-09-02 R1] 위치 계층 재료 — 스펙 §2.5 ⓑ. 자동 노드 생성이 parent 미지정이라
        #   전부 루트로 앉던 구멍(§4)을 메우려면 "루트부터의 경로"가 필요하다.
        #   선택 필드라 부재가 상례 → `or []` / `or ""`로 **명시 null도 누락과 같게** 받는다.
        _lp = analysis.get("location_path")
        bus.dai["location_path"] = _lp if isinstance(_lp, list) else []
        bus.dai["location_type"] = str(analysis.get("location_type") or "")
        bus.dai["location_risk"] = analysis.get("LocationRisk", "Low")
        bus.dai["time_context"] = analysis.get("TimeContext", "")
        bus.dai["scene_type"] = analysis.get("SceneType", "normal")
        bus.dai["energy_direction"] = analysis.get("EnergyDirection", "idle")  # 2026-06-25: 디폴트 rising→idle. omission/불확실 시 긴장을 만들지 않음(둠·톤·페이싱 안전쪽). Theoria가 보통 채움.
        bus.dai["quality_flags"] = analysis.get("QualityFlags", {})
        bus.dai["position"] = analysis.get("Position", {})
        bus.dai["effect"] = analysis.get("Effect", {})
        bus.dai["aspects"] = analysis.get("Aspects", [])
        bus.dai["psyche_states"] = analysis.get("psyche_states", {})
        bus.dai["narrative_chain"] = analysis.get("narrative_chain", {})
        # [2026-10-09 SD개편] SD-Bb2 suggested_beats 전개·비트 주인 걸러내기 은퇴(소비처였던 SD 비트 큐가 은퇴).
        # [2026-07-02 Offscreen Motion — 뮈토스 이식] 부재 캐스트 흔적 (dict or null, null이 상례)
        _ot_raw = analysis.get("offscreen_trace")
        bus.dai["offscreen_trace"] = _ot_raw if isinstance(_ot_raw, dict) else None
        bus.dai["scene_register"] = analysis.get("scene_register")
        bus.dai["input_mode"] = analysis.get("input_mode", "decree")
        bus.dai["memory_triggers"] = analysis.get("memory_triggers", [])
        bus.dai["narrative_hook"] = analysis.get("narrative_hook", "")
        # [2026-10-01 2단계] roll_outcome — 굴림이 돈 턴에만 의미가 있다(문자열 아니거나 "null"이면 None).
        _ro = analysis.get("roll_outcome")
        bus.dai["roll_outcome"] = (_ro.strip() if isinstance(_ro, str) and _ro.strip()
                                   and _ro.strip().lower() != "null" and bus.judgment.get("active") else None)
        # [2026-09-24 감사] H9 open_invitations 전개 누락 복구 — 위 _merge_keys 로 analysis 에는 합류했으나
        #   bus.dai 매핑이 없어 아래 정규화가 [] 로 채웠다 → Slot 16 translate_open_invitations 영구 빈손(07-18~).
        bus.dai["open_invitations"] = analysis.get("open_invitations") or []
        bus.dai["time_flow"] = analysis.get("time_flow", {})
        bus.dai["doom_clocks"] = analysis.get("doom_clocks", {})
        # doom_relief 제거 (2026-05-23) — legacy 위기진폭 잔재
        # [2026-09-06 P8b] mental_impact 매핑 삭제 — Theoria 스키마에서 필드가 사라졌다.
        #   옛 모델이 그 키를 계속 보내도 매핑이 없으니 bus 에 실리지 않는다(무시 = 정규화).
        bus.dai["anomaly_profile"] = analysis.get("anomaly_profile", {})
        bus.dai["temporal_orientation"] = analysis.get("TemporalOrientation", {})
        # [2026-09-15 관계 통합] NPCAttitudes 질문 삭제 — 이 버스 키는 이제 **이번 턴 relation 층의 파생 뷰**
        #   (attitude=bond 구간, reason=descriptor). 저장 아님(저장은 orchestration의 write_theoria_relations → 엣지).
        #   하류(slot iceberg·자율 트리거·world_board·presence) 소비 모양 유지용.
        # [2026-09-25 관계 한 숫자 — 레티어스 "한 턴 안에 두 숫자가 따로 노는 건 위험"] + [관계 정성]
        #   Theoria relation 을 **이번 턴 저장될 값**으로 먼저 맞춘다: 이동 말(bond_shift/tension_shift) → 숫자,
        #   옛 숫자 출력이면 시드·턴당 캡·범위로 클램프. ★자리 = 이 파생 뷰(_relation_view) **앞** — 첫 수리(감정 단계 앞)는
        #   이 뷰가 날숫자를 읽는 걸 놓쳤다(npc_attitudes → iceberg·자율 트리거·월드보드·presence). 감정·iceberg·저장도 같은 값.
        #   행동 PC = anchors.acting_user_id 의 mask(쓰기 경로 ctx.user_id 마스크와 같은 사람), 턴 = 같은 turn_index.
        # [2026-10-12 C32 몸·인격] Theoria `vessel` 사건 → 표(world_state). 장전 안 된 채널은 스키마에 칸이 없어 늘 None.
        #   ★자리 = 관계 정렬 **앞** — 이번 턴에 마음이 바뀌었으면 이번 턴 관계·지식 쓰기부터 새 마음으로 간다.
        #   같은 턴 렌더(Slot 7 합성)도 이 뒤에 지어진다. 관문(PC 몸 거부·명부 밖 마음 버림)은 vessel.apply_events.
        _vs_ev = analysis.get("vessel")
        bus.dai["vessel"] = _vs_ev if isinstance(_vs_ev, list) and _vs_ev else None
        if bus.dai["vessel"]:
            try:
                import vessel as _vs_w
                _ch_vs = (context.narrative_anchors or {}).get("channel_id", "")
                if _ch_vs:
                    _vs_w.apply_events(_ch_vs, bus.dai["vessel"],
                                       turn=int(domain_manager.get_world_state(_ch_vs).get("turn_index", 0) or 0))
            except Exception as _e_vs:
                logger.debug(f"[Vessel] apply skip: {_e_vs}")
        try:
            _anch_al = context.narrative_anchors or {}
            _ch_al = _anch_al.get("channel_id", "")
            _acting_al = ((_anch_al.get("all_pcs") or {}).get(_anch_al.get("acting_user_id", "")) or {}).get("mask")
            if _ch_al and _acting_al:
                _turn_al = int(domain_manager.get_world_state(_ch_al).get("turn_index", 0) or 0)
                domain_manager.align_theoria_relations(
                    _ch_al, analysis.get("psyche_states") or {}, _acting_al, _turn_al)
        except Exception as _e_al:
            logger.debug(f"[Relation] align skip: {_e_al}")
        bus.dai["npc_attitudes"] = _relation_view(
            analysis.get("psyche_states"),
            (context.narrative_anchors or {}).get("stored_npc_attitudes"))
        bus.dai["npc_knowledge"] = analysis.get("NPCKnowledge", {})
        bus.dai["sensory_anchors"] = analysis.get("SensoryAnchors", [])
        bus.dai["habitus_analysis"] = analysis.get("HabitusAnalysis", {})
        bus.dai["intimacy_analysis"] = analysis.get("IntimacyAnalysis")
        bus.dai["relevant_context"] = analysis.get("RelevantContext", [])
        bus.dai["relevant_npcs"] = analysis.get("RelevantNPCs", [])
        bus.dai["relevant_chunks"] = analysis.get("relevant_chunks", [])
        bus.dai["needs_judgment"] = analysis.get("needs_judgment", False)
        bus.dai["action_meta"] = analysis.get("action_meta", {})
        bus.dai["asset_evaluation"] = analysis.get("asset_evaluation", {})
        bus.dai["flashback_eval"] = analysis.get("flashback_eval")
        # [2026-09-06 P8b] rest_eval 매핑 삭제 — "부재를 감지하지 않는다". 소비자(다운타임
        #   코드 효과·휴식 회복·산문 지시)는 같은 카드에서 전부 폐기됐다.
        # [2026-10-23 C61] item_usage 은퇴 — 소지품 변화는 출력물 추출 콜이 산문(결과)을 보고 잡는다.
        # [2026-09-13 P14] 도착물 방아쇠 ② — 불리언 하나. 부재/비불리언 = False(무동작).
        #   소비자는 world_board.pick_arrival_request 하나뿐이고, 종류·내용은 여기서 안 온다.
        bus.dai["arrival"] = bool(analysis.get("arrival") is True)
        # [2026-06-11 소비자 감사 #2~4] 운송 누락 복구 — Theoria 스키마에 실재(=Flash가 매 턴 생산)
        # 했으나 매핑이 빠져 슬롯 번역기 3종(trait_connections/spatial_inscription/continuity_check)이
        # 영구 빈손이었음 (dai_consumer_audit.md). 번역기들은 빈값 관용이라 연결만으로 안전.
        bus.dai["trait_connections"] = analysis.get("trait_connections", {})
        bus.dai["spatial_read"] = analysis.get("spatial_read", {})
        bus.dai["continuity_check"] = analysis.get("continuity_check")
        # [2026-09-22 voice_seed §2 D 2] 굴림 + 서사 콜 콜라주 병합. 굴린 이름만 채택하고
        #   목록 밖 이름은 버린다(모델이 인물을 발명하는 자리를 여기서 막는다).
        #   굴림이 없으면(게이트 통과 0 · 마스터 OFF) `{}` — 하류(Slot 7 꼬리)는 빈손이면 침묵.
        bus.dai["newcomer_seeds"] = voice_seed.merge_collage(
            _seed_rolls, analysis.get("newcomer_seeds")) if _seed_rolls else {}

        # [2026-07-19 명시 null 정규화] LLM Optional 필드는 "누락"뿐 아니라 "명시 null"로도
        # 온다 — .get(k, default)는 키가 존재하면 null을 그대로 통과시킴 (E3 프로덕션 크래시
        # 교훈). 형 계약 필드를 여기서 일괄 정규화 — 하류 무가드 순회/슬라이스 방어 초크포인트.
        # (의미상 null 허용 필드는 제외: anomaly는 {}=falsy로 동치, offscreen_trace/scene_register/
        #  intimacy_analysis/flashback_eval/item_usage/continuity_check는 null 계약 유지)
        for _lk in ("aspects", "memory_triggers", "sensory_anchors", "relevant_context",
                    "relevant_npcs", "relevant_chunks", "open_invitations"):
            if not isinstance(bus.dai.get(_lk), list):
                bus.dai[_lk] = []
        for _dk in ("input_analysis", "quality_flags", "position", "effect", "psyche_states",
                    "narrative_chain", "time_flow", "doom_clocks",
                    "anomaly_profile", "temporal_orientation",
                    "npc_attitudes", "npc_knowledge", "habitus_analysis", "action_meta",
                    "asset_evaluation", "trait_connections", "spatial_read"):
            if not isinstance(bus.dai.get(_dk), dict):
                bus.dai[_dk] = {}
        if not isinstance(bus.dai["narrative_chain"].get("open_threads"), list):
            bus.dai["narrative_chain"]["open_threads"] = []

        # [2026-07-28 PC 혼입 단일 정제] ★같은 병의 5·6번째 재발을 끊는 자리.
        # 그동안은 **소비처마다** 가드를 새로 달았다(NPCAttitudes 07-13 / npc_knowledge /
        # scene_npcs / psyche_states 07-28). 그런데 그 가드들은 대개 **지역 변수만** 정제하고
        # bus.dai 원본은 그대로 둬서, 원본을 보는 하류(world_board·story_director·
        # narrative_tracker)는 여전히 PC가 섞인 데이터를 받았다 — world_board는 PC 이름으로
        # 세계 게시물을 만들 수 있는 상태였다.
        # 처방: **DAI가 만들어지는 이 초크포인트에서 한 번만** 걷어내고 하류는 믿게 한다.
        # (PC=카메라 원칙. 개별 가드는 이중 안전으로 남겨도 무해하다.)
        try:
            # [2026-09-22] 시드 게이트와 같은 원천(all_pcs). [2026-10-07 P4] 판정은 `!가면` 이름 규칙(_is_pc_key) —
            #   정확일치였을 땐 "Adam"·"아담"으로 적힌 키가 그대로 통과해 렌더 small tells·저장까지 갔다.
            if _pc_reg:
                _purged = []
                # [2026-09-22 voice_seed §2 D 3] newcomer_seeds 편입 — 게이트 2가 이미 걸렀지만
                #   서사 콜이 키를 PC 이름으로 바꿔 돌려줄 수 있어 이중으로 본다.
                # [2026-10-07 P4] trait_connections 편입 — 서사 콜이 PC 키로 낸 render_hint 가 Slot 16 small tells 로
                #   그대로 갔다(PC 몸짓 지시, 녹화 9/83블록).
                for _nk in ("psyche_states", "npc_attitudes", "npc_knowledge", "newcomer_seeds", "trait_connections"):
                    _blk = bus.dai.get(_nk)
                    if isinstance(_blk, dict):
                        _hit = [n for n in _blk if _is_pc_key(n, _pc_reg)]
                        if _hit:
                            bus.dai[_nk] = {k: v for k, v in _blk.items() if k not in _hit}
                            _purged.append(f"{_nk}({','.join(_hit)})")
                # [2026-10-10 칸이관] 친밀 분석의 인물별 칸도 — PC 욕망·브레이크가 Slot 17로 가면 PC 봉인 위반.
                _ia_pc = bus.dai.get("intimacy_analysis")
                if isinstance(_ia_pc, dict):
                    for _ik in ("window_check", "dual_control", "desire_type", "post_encounter_prediction"):
                        _sub = _ia_pc.get(_ik)
                        if isinstance(_sub, dict):
                            _hit = [n for n in _sub if _is_pc_key(n, _pc_reg)]
                            if _hit:
                                _ia_pc[_ik] = {k: v for k, v in _sub.items() if k not in _hit}
                                _purged.append(f"intimacy_analysis.{_ik}({','.join(_hit)})")
                _rn = bus.dai.get("relevant_npcs")
                if isinstance(_rn, list):
                    _hit = [n for n in _rn if _is_pc_key(n, _pc_reg)]
                    if _hit:
                        bus.dai["relevant_npcs"] = [n for n in _rn if n not in _hit]
                        _purged.append(f"relevant_npcs({','.join(_hit)})")
                if _purged:
                    logger.warning("[DAI] PC 혼입 제외: %s", " / ".join(_purged))
        except Exception as _e_pcp:
            logger.debug(f"[DAI] PC purge skipped: {_e_pcp}")

        # [2026-09-22 voice_seed §2 F] 버퍼 적재 — **정제 뒤**다(PC 키가 버퍼에 눕지 않게).
        #   등록 관문(§G, PR 3)이 이걸 집어 lore 절로 앉힌다. 여기서도 예외는 삼킨다.
        try:
            if bus.dai.get("newcomer_seeds"):
                voice_seed.buffer_put(_seed_ch, bus.dai["newcomer_seeds"], _seed_turn)
        except Exception as _e_seedbuf:
            logger.warning("[Seed] buffer_put skipped: %s", _e_seedbuf)

        # [2026-06-11 소비자 감사 #6] 죽은 저장 제거 — capability hints는 이제 anchors 경유로
        # Theoria *입력*에 배달됨 (une_facade에서 계산, theoria_analyzer 로스터 옆 렌더 — 원설계).
        # 기존 이 자리 코드는 Flash 콜 후 저장 + 독자 0 + npc_roster가 str이라 isinstance(dict)
        # 가드에 막혀 사실상 한 번도 실행 안 됨 (이중 사망 확인).

        # [2026-10-01 2단계] 판정 훅 사후 처리 — 판정은 서사 콜 **앞**에서 돌았다(게이트·prime·판정 블록 이사).
        #   훅은 서사 콜 몫이라 여기서 싣고, 디스코드 판정 메시지의 "잠재적 위기" 줄은 결과 3종에서만 사후에 붙인다
        #   (구: judgment_engine 안에서 붙임 + party_wide_hook 플래그 — 읽는 곳 0이라 같이 지웠다).
        #   이제 훅은 굴림 결과를 알고 쓴 것이다. 스펙 composition/분석렌더_2단계_판정먼저_스펙_2026-10-01.md §2.
        if bus.judgment.get("active"):
            _hook = str(analysis.get("narrative_hook") or "").strip()
            bus.judgment["narrative_hook"] = _hook
            # [2026-10-15 C42] 훅은 판정 임베드의 칸으로 — 📋 결과 영향 칸 앞에 끼운다(한 줄 output 은 그대로).
            _emb = bus.judgment.get("embed")
            if (_hook and bus.judgment.get("result") in ("partial", "failure", "critical_failure")
                    and isinstance(_emb, dict)):
                _fl = list(_emb.get("fields") or [])
                _ci = next((i for i, f in enumerate(_fl) if f and str(f[0]).startswith("📋")), len(_fl))
                _fl.insert(_ci, ["⚠️ 잠재적 위기", _hook])
                _emb["fields"] = _fl

        # [V10] DAI 스냅샷 롤링 보존 — bus.dai 완성 직후, 코드만(콜 0)·실패 무해.
        # 용도: ①관측 — 필드 비대/모델 JSON 버릇을 실데이터로 ②Sprint 4 동적 NPC 원재료
        # (턴별 심리·사회 이력 질의). 읽기: sqlite_store.read_dai_logs(channel_id, n).
        # [2026-09-24 감사] 적재 자리를 StoryDirector **뒤**로 옮겼다(아래 5.5 직후) — 여기서 찍으면
        #   story_direction.next_beat 가 아직 없어 서사 콜 RECENT BEATS(반복 회피 목록)에 디렉터 비트가 영영 안 들어갔다.
        #   ([2026-10-09 SD개편] next_beat 은퇴 — RECENT PULLS는 narrative_hook에서 모은다. 자리는 그대로 둔다.)
        
        # Doom Clocks v3 연동 (clock_updates, clock_new, clock_resolved)
        # relief 제거 (2026-05-23) — legacy 위기진폭 잔재. 둠은 서사 진행도라 평화 장면 자동 감소는 의미 충돌.
        doom_clocks_output = analysis.get("doom_clocks") or {}
        if isinstance(doom_clocks_output, dict):
            bus.doom["flash_clock_updates"] = doom_clocks_output.get("clock_updates") or []  # [07-19] 명시 null 방어
            bus.doom["flash_clock_new"] = doom_clocks_output.get("clock_new")
            bus.doom["flash_clock_resolved"] = doom_clocks_output.get("clock_resolved") or []

        # [2026-09-06 P8b] **Composure Impact 번역 삭제.** 여기가 mental_impact 를 bus.composure
        #   의 impact 칸으로 옮기던 마지막 다리였다 — severity enum → 수치(MENTAL_IMPACT_ENUM_SCALE),
        #   씬타입별 캡, 방향 전환 감쇠까지 전부 "장면을 분류해 숫자를 정하는 코드"라 함께 사라졌다.
        #   평형의 이동은 이제 전담 추출 콜의 deltas 한 경로뿐이다(기력과 완전 대칭).

        # [2026-10-10 던지는판 A1] anomaly_profile·condition_*·location 전개와 기본값 보정은 서사 콜 앞으로 **옮겼다**(아래 재전개 0).
        #   Storyteller가 큐 발화로 tag·line을 고쳐 쓰고 길잡이가 이은 제안을 비우므로, 여기서 다시 덮으면 터진 사건이 원 제안으로 돌아간다.
        # M3 제거 (2026-07-02): "chain CLOSED → anomaly 강제" 컷.
        # ① 키 드리프트('status' → 'chain_status')로 장기간 사망 상태였고 부재가 관측된 적 없음.
        # ② 현 자세와 충돌 — quiet resolution 허용(Scheherazade 완화) + 페이싱은 doom 起承轉結/storyteller 결정이 담당.
        # 부활 시: bus.dai["narrative_chain"].get("chain_status") == "CLOSED" 게이트로 재작성할 것.

        # Seed fallback 제거 (2026-07-09): "tag 제안 없음 → 시드 무작위 강제" 컷.
        # M3(chain CLOSED→anomaly 강제)의 쌍둥이 — 조용한 턴 허용 + 페이싱=doom/storyteller 원칙에
        # 동일하게 걸린다. 매 조용한 턴 무작위 시드 발화(Mid 강도 + "{tag}의 기운이 감돈다" 위협 프레임 line)는
        # wingbeat 시드 재설계(작은·장르중립·로어접지)와 정면 충돌. 능동성 공급은 story_director/reader_gm/doom이
        # 담당하고, 날개짓의 성장은 reader_gm replenish(축 기반, 게이트됨)가 맡는다.
        # 부활 시: 무작위 강제가 아니라 stall 감지 게이트 + 중립 line + 적합 시드 선택으로 재작성할 것.

        # 1.5 Emotion Engine: psyche_states → normalized emotion tracking
        channel_id = (context.narrative_anchors or {}).get("channel_id", "")
        current_turn = 0
        if channel_id:
            current_turn = domain_manager.get_world_state(channel_id).get("turn_index", 0)

        # [2026-09-25] 관계 정렬(align_theoria_relations)은 위 `_relation_view` 앞으로 옮겼다 — 여기서는 이미 한 숫자.

        try:
            psyche_states = bus.dai.get("psyche_states", {})
            # [2026-06-12] PC 혼입 차단 (4호) — Theoria가 psyche_states에 PC를 포함시키는데
            # 감정엔진과 하류 3기관(스토리디렉터 focus/NPC자율 집단게이트/iceberg Slot 14·16)은
            # 전부 NPC 전용. PC가 흘러들면: 디렉터가 PC를 연출 대상으로(focus=도만 관측됨),
            # 집단 게이트 인원 수 부풀림, PC 내면 힌트가 Pro에 주입(사칭 압력). PC=카메라 원칙.
            _pc_masks_em = _pc_reg   # [2026-10-07 PC 이름 단일화] 정확일치 → 같은 명부·같은 판정(_is_pc_key)
            if psyche_states and _pc_masks_em:
                _removed = [n for n in psyche_states if _is_pc_key(n, _pc_masks_em)]
                if _removed:
                    psyche_states = {k: v for k, v in psyche_states.items() if k not in _removed}
                    logger.debug(f"[EmotionEngine] PC 제외: {', '.join(_removed)}")
            if psyche_states:
                prev_emotions = {}
                if channel_id:
                    prev_emotions = domain_manager.get_world_state(channel_id).get(
                        "npc_emotion_states", {}
                    )
                # P2: scene 컨텍스트와 memory_triggers 전달 (_derive_relational Tier 4/6/7 입력)
                _narrative_chain = bus.dai.get("narrative_chain", {}) or {}
                _scene_ctx = {
                    "register": bus.dai.get("scene_register"),
                    "silence_type": _narrative_chain.get("silence_type"),
                    # [2026-07-13] 친밀 장면 재서열 신호 — intimate에서 관계-긍정 tier 선발화
                    "scene_type": bus.dai.get("scene_type"),
                }
                emotion_results = EmotionEngine.process_turn(
                    psyche_states=psyche_states,
                    previous_emotions=prev_emotions,
                    current_turn=current_turn,
                    scene_ctx=_scene_ctx,
                    memory_triggers=bus.dai.get("memory_triggers", []),
                )
                bus.emotion = EmotionEngine.to_bus_dict(emotion_results)
                # 6.2 (2026-05-20): slot_manager fast-path 배선. slot_manager가
                # `dai._emotion_states_for_slot`를 우선 보고 있었으나 어디서도 set
                # 안 해 항상 world_state round-trip으로 폴백했음. 같은 턴 내에서는
                # 이 라이브 dict가 가장 신선하므로 직접 주입한다.
                # 값 타입: Dict[str, EmotionState] — slot_manager가 그대로 소비.
                bus.dai["_emotion_states_for_slot"] = emotion_results
                if channel_id:
                    # [2026-09-15 §12 추적기] 통째 교체 → 병합. 이번 턴 결과가 없는 NPC는 탈락이 아니라
                    #   기준선 감쇠(prev×EMOTION_DECAY), 사망은 즉시 제거. 버스·Slot 16·log는 이번 턴 결과만(무변경).
                    _write_emotion_tracker(channel_id, prev_emotions, emotion_results, current_turn, context)
                if channel_id and emotion_results:
                    world = domain_manager.get_world_state(channel_id)
                    # [2026-08-11 soma 지속] B축 스냅샷 — 병합·도장 본체는 `_merge_soma_states()`
                    #   (모듈 상단, 근거 주석 전량 독스트링). 여기선 읽기/저장/적립만.
                    try:
                        _psy = psyche_states or {}  # ⚠PC 제외본(위 _pc_masks_em 필터 결과)
                        world = domain_manager.get_world_state(channel_id)
                        _soma_prev = world.get("npc_soma_states")
                        if not isinstance(_soma_prev, dict):
                            _soma_prev = {}
                        _soma_snap, _soma_moves = _merge_soma_states(_soma_prev, _psy, current_turn)
                        if _soma_snap != _soma_prev:
                            world["npc_soma_states"] = _soma_snap
                            domain_manager.update_world_state(channel_id, world)
                        # [2026-10-07 a묶음 A4] 관계 지난 턴 상태 — soma와 같은 자리·같은 규율.
                        _rel_prev = world.get("npc_relation_states")
                        if not isinstance(_rel_prev, dict):
                            _rel_prev = {}
                        _rel_snap = _merge_relation_states(_rel_prev, _psy, current_turn)
                        if _rel_snap != _rel_prev:
                            world["npc_relation_states"] = _rel_snap
                            domain_manager.update_world_state(channel_id, world)
                        if _soma_moves:
                            try:  # [V10 적립] soma_log — 몸 상태가 *언제* 뒤집혔나. 실패 무해.
                                import sqlite_store
                                for _tn, _fp, _tp, _fd, _td in _soma_moves:
                                    sqlite_store.append_soma_log(
                                        channel_id, current_turn, _tn, _fp, _tp, _fd, _td)
                            except Exception as _e_somalog:
                                logger.debug(f"[Soma] log skipped: {_e_somalog}")
                    except Exception as _e_soma:
                        logger.debug(f"[Soma] snapshot skip: {_e_soma}")
                    # [V10 적립] emotion_log 적립 — bus.emotion 완성 직후, 코드만(콜0)·실패 무해.
                    # 턴별 per-NPC 감정 스냅샷 → 궤적/스파이크 질의(독자: sqlite_store.read_emotion_*).
                    try:
                        import sqlite_store
                        sqlite_store.append_emotion_log(
                            channel_id, current_turn, bus.emotion,
                            log_extra={n: {k: getattr(st, k, "") for k in EmotionState.LOG_ONLY_KEYS}
                                       for n, st in emotion_results.items()})
                    except Exception as _e_emolog:
                        logger.debug(f"[V10] emotion log skipped: {_e_emolog}")
                spikes = [
                    f"{n}({s.spike_detail})"
                    for n, s in emotion_results.items()
                    if s.spike_detected
                ]
                if spikes:
                    logger.info(f"[EmotionEngine] Spikes: {', '.join(spikes)}")
            elif channel_id:
                # [2026-09-15 §12] 이번 턴 psyche_states가 비어도 부재 감쇠는 돈다(전원 부재 턴).
                _prev_only = domain_manager.get_world_state(channel_id).get("npc_emotion_states", {})
                if _prev_only:
                    _write_emotion_tracker(channel_id, _prev_only, {}, current_turn, context)
        except Exception as e:
            _degrade_stage(bus, "emotion_engine", e)

        # [2026-10-01 2단계] Mental Pre-pass(prime)·Judgment 는 서사 콜 앞으로 옮겼다(판정 먼저). 위 사전 전개 블록 참고.

        # [2026-10-10 던지는판 A1] 4·5 Storyteller(주입·결정)는 서사 콜 앞으로 옮겼다. SD는 감정 엔진(서사 콜 뒤)을 읽어 여기 남는다.
        # 5.5 Story Director: pacing, plot hints, idle handling, transition mood
        try:
            context = StoryDirector.process(context)
        except Exception as e:
            _degrade_stage(bus, "story_director", e)

        # [V10 Sprint 0] 턴별 DAI 스냅샷 적재 — [2026-09-24 감사] 위(분석 전개 직후)에서 이사: 디렉터 비트 포함.
        try:
            import sqlite_store
            _dai_ch = (context.narrative_anchors or {}).get("channel_id", "")
            if _dai_ch:
                sqlite_store.append_dai_log(_dai_ch, current_turn, bus.dai)
        except Exception as _e_dai:
            logger.debug(f"[V10] dai log skipped: {_e_dai}")

        # 5.6 Seven Dice persistence → DiceEngine가 자체 처리 (dice_engine.py)

        # 6. Doom Update — consumes judgment doom_delta naturally
        try:
            self.doom = DoomModule()
            context = await self.doom.process(context)
        except Exception as e:
            _degrade_stage(bus, "doom_module", e)

        # 7. Vigor/Composure Sync (LAST — consumes all accumulated deltas)
        try:
            self.vigor_composure = VigorComposureModule()
            context = await self.vigor_composure.process(context)
        except Exception as e:
            _degrade_stage(bus, "vigor_composure", e)

        # ===== Pipeline Summary Log =====
        self._log_pipeline_summary(bus)

        # [V10 적립] turn_snapshot — 파이프라인 말미, 모든 신호 최종. 콜0·append-only·실패무해.
        try:
            import sqlite_store
            _ts_ch = (context.narrative_anchors or {}).get("channel_id", "")
            if _ts_ch:
                _sd = (bus.dai.get("story_direction") or {}) if isinstance(bus.dai, dict) else {}
                _snap = {
                    "doom_value": bus.doom.get("value"),
                    "doom_phase": bus.doom.get("chapter_phase", ""),
                    "vigor": bus.vigor.get("value"),
                    "vigor_delta": bus.vigor.get("delta_applied", 0) or 0,
                    "composure": bus.composure.get("value"),
                    "composure_delta": bus.composure.get("delta_applied", 0) or 0,
                    "sd_pacing": _sd.get("pacing", ""),
                    "sd_tension": _sd.get("tension_axis", ""),
                    "sd_focus": (_sd.get("focus") or {}).get("spotlight", ""),
                    "sd_idle": bool(_sd.get("is_idle_input")),
                    "judgment": judgment_snapshot(bus.judgment),
                    "anomaly_triggered": bool(bus.anomaly.get("triggered")),
                }
                sqlite_store.append_turn_snapshot(_ts_ch, current_turn, _snap)
        except Exception as _e_ts:
            logger.debug(f"[V10] turn_snapshot skipped: {_e_ts}")

        return context

    def _log_pipeline_summary(self, bus: SharedBus) -> None:
        """파이프라인 실행 결과 한눈에 볼 수 있는 요약 로그."""
        parts = ["[Pipeline Summary] (all modules always active)"]

        # Judgment
        if bus.judgment.get("active"):
            j = bus.judgment
            result = j.get("result", "N/A")
            gate = j.get("gate_reason", "")
            parts.append(f"  Judgment: {result} (4dF n={j.get('n', '?')} {j.get('edge', '?')}, "
                         f"D={j.get('dice_sum', '?')} M={j.get('mod', '?')} T={j.get('total', '?')}) [{gate}]")
        else:
            parts.append(f"  Judgment: skipped (no action requiring roll)")

        # Doom
        doom_val = bus.doom.get("value", "?")
        doom_log = bus.doom.get("log", "")
        parts.append(f"  Doom: {doom_val}/100 — {doom_log[:100]}" if doom_log else f"  Doom: {doom_val}/100 (no change)")

        # Storyteller
        anomaly_triggered = bus.anomaly.get("triggered", False)
        if anomaly_triggered:
            a_tag = bus.anomaly.get("tag", "?")
            a_int = bus.anomaly.get("intensity", "?")
            a_dec = bus.anomaly.get("decision", "?")
            a_reason = bus.anomaly.get("decision_reason", "")
            parts.append(f"  Storyteller: ACT [{a_tag}] intensity={a_int} ({a_dec}: {a_reason})")
        else:
            a_dec = bus.anomaly.get("decision", "skip")
            a_reason = bus.anomaly.get("decision_reason", "")
            parts.append(f"  Storyteller: {a_dec} ({a_reason})" if a_reason else f"  Storyteller: {a_dec}")

        # Vigor / Composure
        v_val = bus.vigor.get("value", "?")
        c_val = bus.composure.get("value", "?")
        v_delta = bus.vigor.get("delta_applied", 0) or 0
        c_delta = bus.composure.get("delta_applied", 0) or 0
        v_sign = f"+{v_delta}" if v_delta > 0 else str(v_delta)
        c_sign = f"+{c_delta}" if c_delta > 0 else str(c_delta)
        parts.append(f"  Vigor: {v_val} ({v_sign}) | Composure: {c_val} ({c_sign})")

        # Story Director (SD-A4 새 스키마 — plot_hints/transition.mood 제거, focus.spotlight 사용. [2026-10-09] next_beat 은퇴)
        sd = bus.dai.get("story_direction") or {}  # [07-27] 명시 null 방어 (요약 로그는 try 밖)
        if sd.get("active"):
            sd_pacing = sd.get("pacing", "?")
            sd_tension = sd.get("tension_axis", "?")
            sd_idle = sd.get("is_idle_input", False)
            sd_focus = (sd.get("focus") or {}).get("spotlight", "none")
            sd_cut = (sd.get("transition") or {}).get("cut", "?")
            parts.append(
                f"  StoryDir: pacing={sd_pacing} tension={sd_tension} idle={sd_idle} "
                f"focus={sd_focus} cut={sd_cut}"
            )
            # Seven Dice
            sd_dice = sd.get("dice", {})
            if sd_dice:
                parts.append(f"  Dice: {sd_dice.get('name','?')} (state={sd_dice.get('scene_state','?')}, visible={sd_dice.get('visible',False)})")
        else:
            parts.append("  StoryDir: inactive")

        # Degradation
        _deg = bus.dai.get("_degraded_stages", [])
        if _deg:
            _deg_str = ", ".join(d.get("stage", "?") for d in _deg if isinstance(d, dict))
            parts.append(f"  ⚠ Degraded: {_deg_str}")

        # Emotion — pair 스키마 v2: 'dominant' → 'base' + 'modifier' (to_bus_dict summary)
        emotion_data = bus.emotion
        if emotion_data.get("active"):
            summaries = emotion_data.get("summary", {})
            emo_parts = []
            for n, s in summaries.items():
                if s.get("intensity", 0) <= 0.05:
                    continue
                _base = s.get("base", "?") or "?"
                _mod = s.get("modifier", "")
                _pair = f"{_base}×{_mod}" if _mod else _base
                # [2026-08-03] pair_confidence 노출. 이미 계산돼 bus에 실려 있었는데
                #   요약 로그만 안 찍어서, "왜 이 modifier가 붙었나"를 소스에서
                #   역추적해야 했다. confidence는 _derive_relational의 **티어 번호 역수**라
                #   값 하나로 어느 티어가 결정했는지 특정된다:
                #     1.0=T1 value_conflict / 0.9=T2 negotiation_stance / 0.8=T3 cultural_affect
                #     0.7=T4 memory_trigger / 0.6=T5 attachment / 0.55=T5b poise
                #     0.5=T6 register / 0.4=T7 silence_type / 0.3=T8 deep_read
                #   ★특히 T6·T7은 scene_ctx(장면 공유) 소스라, 여러 NPC가 **같은 modifier**로
                #     몰렸을 때 "개인 신호인가 장면 필드가 덮었나"를 이걸로 가른다.
                #   solo base(modifier 없음)면 confidence 무의미 → 생략.
                _conf = s.get("pair_confidence", 0)
                _conf_s = f"/{_conf:.2f}" if _mod and _conf else ""
                emo_parts.append(
                    f"{n}={_pair}({s.get('intensity',0):.1f}{_conf_s})"
                    + (" ⚡" if s.get("spike") else "")
                )
            if emo_parts:
                parts.append(f"  Emotion: {', '.join(emo_parts)}")
        else:
            parts.append("  Emotion: inactive")

            # [2026-08-03] Scene register / silence_type — **modifier 쏠림 때만** 발화.
            #   _derive_relational의 Tier 6(register)·Tier 7(silence_type)은 scene_ctx 소스라
            #   한 값이 전원의 modifier를 덮는다. 그래서 "여러 NPC가 같은 modifier"는
            #   개인 신호가 아니라 장면 필드가 결정했다는 신호고, 그때만 이 두 필드가
            #   설명력을 갖는다. register는 감정 modifier뿐 아니라 Slot 16 연출 지시
            #   (iceberg.translate_register)도 만들어서, 오판정이면 두 곳이 같이 틀린다.
            #   ★상시 출력하지 않는다 — 쏠림이 없는 턴엔 설명할 게 없다(침묵 경로).
            #     오늘 detail_density에서 고친 것과 같은 규율을 이 줄에도 적용.
            try:
                _mods = [s.get("modifier") for s in summaries.values()
                         if s.get("modifier") and s.get("intensity", 0) > 0.05]
                if len(_mods) >= 2 and len(set(_mods)) == 1:
                    _sr = (bus.dai.get("scene_register") or "") if isinstance(bus.dai, dict) else ""
                    _sil = ((bus.dai.get("narrative_chain") or {}).get("silence_type") or "") \
                        if isinstance(bus.dai, dict) else ""
                    parts.append(
                        f"  └ modifier {len(_mods)}인 쏠림 — register={_sr or '-'} silence={_sil or '-'}"
                    )
            except Exception:
                pass

        logger.info("\n".join(parts))
