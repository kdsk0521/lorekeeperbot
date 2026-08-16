# -*- coding: utf-8 -*-
"""
narrative_queries — V10 적립층 위 읽기 전용 서사 집계 레이어 (Phase 0, 2026-07-02).

목적: 분석 콜(현행 Theoria, 이후 A안 서사 콜)과 B안 챕터 Director에
      '히스토리 재탕'이 아니라 '계측'을 급식한다.
      반복=대조(recent_beats), 정체=수치(thread_ages/pacing_curve),
      소외=비중(screen_time), 부재=기간(last_seen).

설계 문서: 파티쳇수정/ab_db_design_review_2026-07.md
원칙:
  - 전부 순수 코드 (LLM 콜 0). 실패 시 빈값 — 파이프라인 무해.
  - 산출은 1~2줄 텔레그래픽 고정 (집계 요약이 길어지면 본말전도).
  - fetch(얇음)/format(순수) 분리 — _format_* 는 단독 스모크 가능.

소비 현황 (2026-07-02):
  - last_seen_turns / recent_beats = 배선됨 (ABSENT CAST 부재기간 + 비트 반복 회피).
  - pacing_curve / thread_ages / emotion_arc / screen_time = A안(서사 콜)·B안(챕터 회고 팩)
    예약분 — 감사 시 orphan 아님 (staged infrastructure, 소비자 계획 명시).
"""

import logging
import re
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("NarrativeQueries")


# =========================================================
# 순수 포맷터 (스모크 대상 — DB 무접촉)
# =========================================================

def _format_pacing_curve(snaps: List[Dict[str, Any]]) -> str:
    """turn_snapshot 리스트(오래된→최신) → 1줄 페이싱 요약."""
    if not snaps:
        return ""
    n = len(snaps)
    d0 = snaps[0].get("doom_value")
    d1 = snaps[-1].get("doom_value")
    phase = snaps[-1].get("doom_phase") or "?"
    idle_n = sum(1 for s in snaps if s.get("sd_idle"))
    beat_n = sum(1 for s in snaps if s.get("sd_beat"))
    pacings = [s.get("sd_pacing") for s in snaps if s.get("sd_pacing")]
    mode = max(set(pacings), key=pacings.count) if pacings else "?"
    return (
        f"last {n} turns: doom {d0}->{d1} (phase {phase}), "
        f"idle {idle_n}/{n}, dominant pacing {mode}, beats present {beat_n}/{n}"
    )


def _format_recent_beats(dai_rows: List[Tuple[int, Dict[str, Any]]], cap: int = 8) -> List[str]:
    """dai_logs 행(오래된→최신) → 최근 제안 비트 unique 목록 (최신 우선, cap개)."""
    seen = set()
    out: List[str] = []
    for _turn, dai in reversed(dai_rows):  # 최신부터
        if not isinstance(dai, dict):
            continue
        cands: List[str] = []
        sb = dai.get("suggested_beats")
        if isinstance(sb, list):
            cands.extend(str(b).strip() for b in sb if isinstance(b, str) and b.strip())
        nb = (dai.get("story_direction") or {}).get("next_beat") if isinstance(dai.get("story_direction"), dict) else None
        if isinstance(nb, str) and nb.strip():
            cands.append(nb.strip())
        for c in cands:
            key = c.lower()
            if key in seen:
                continue
            seen.add(key)
            out.append(c)
            if len(out) >= cap:
                return out
    return out


def _beat_at_turn(dai: Dict[str, Any]) -> str:
    """한 턴의 DAI → 그 턴이 무엇에 관한 장면이었나 1구. 없으면 "".

    우선순위: 그 턴에 실제로 열린 스레드 > 제안 비트 > 장면 레지스터.
    (suggested_beats는 '제안'이라 실현 보장이 없지만, spike가 난 턴의 제안은
     그 턴의 압력 축과 사실상 같다 — 폴백으로 유효.)
    """
    if not isinstance(dai, dict):
        return ""
    chain = dai.get("narrative_chain") or {}
    if isinstance(chain, dict):
        th = chain.get("open_threads")
        if isinstance(th, list) and th:
            s = str(th[0]).strip()
            if s:
                return s
    sb = dai.get("suggested_beats")
    if isinstance(sb, list):
        for b in sb:
            if isinstance(b, str) and b.strip():
                return b.strip()
    sr = dai.get("scene_register")
    if isinstance(sr, str) and sr.strip():
        return sr.strip()
    return ""


def _format_emotion_residue(
    spikes: List[Dict[str, Any]],
    dai_rows: List[Tuple[int, Dict[str, Any]]],
    current_turn: int,
    cap: int = 4,
) -> List[str]:
    """spike 턴 × 그 턴의 DAI = 잔열의 출처. (최신 우선, cap개)

    [2026-07-15] 감정 부채·이자 (딥스키 0712 §7-2 / preset_analysis_deepseek0712).
    emotion_log·dai_logs 둘 다 turn 키를 갖는데, 기존 포맷터가 양쪽에서
    턴을 버려(_format_emotion_arc=개수만, _format_recent_beats=`_turn` 폐기)
    서사 콜이 '언제 튀었나'와 '그때 무슨 일'을 조인할 수 없었다.
    여기서 코드가 미리 붙여 급식한다 → 서사 콜의 psyche_narrative.resurfacing 재료.

    LLM 콜 0 · 새 테이블/필드 0 · emotion_engine 무접촉 (읽기 전용 조인).
    """
    if not spikes:
        return []
    by_turn: Dict[int, Dict[str, Any]] = {}
    for t, d in (dai_rows or []):
        try:
            by_turn[int(t)] = d
        except (TypeError, ValueError):
            continue
    out: List[str] = []
    for s in reversed(spikes):  # 최신부터
        try:
            t = int(s.get("turn", -1))
        except (TypeError, ValueError):
            continue
        if t < 0:
            continue
        age = int(current_turn) - t
        if age < 1:
            continue  # 이번 턴 스파이크는 잔열이 아니라 현재 사건
        npc = str(s.get("npc_name") or "").strip()
        if not npc:
            continue
        base = str(s.get("base") or "").strip()
        mod = str(s.get("modifier") or "").strip()
        pair = f"{base}x{mod}" if base and mod else (base or mod or "?")
        beat = _beat_at_turn(by_turn.get(t, {}))
        # 조인 실패(그 턴 DAI가 보관 범위 밖) → 나이·페어만. 발명 금지.
        src = f" amid «{beat}»" if beat else ""
        out.append(f"{npc}: {pair} spiked {age} turns ago{src}")
        if len(out) >= cap:
            break
    return out


def _format_thread_ages(dai_rows: List[Tuple[int, Dict[str, Any]]]) -> List[Tuple[str, int]]:
    """dai_logs 행(오래된→최신) → 현재 열린 스레드별 나이(턴). [(thread, age)] 나이 내림차순."""
    if not dai_rows:
        return []
    first_seen: Dict[str, int] = {}
    for turn, dai in dai_rows:
        if not isinstance(dai, dict):
            continue
        chain = dai.get("narrative_chain") or {}
        threads = chain.get("open_threads") if isinstance(chain, dict) else None
        if not isinstance(threads, list):
            continue
        for t in threads:
            key = str(t).strip()
            if key and key not in first_seen:
                first_seen[key] = int(turn)
    last_turn, last_dai = dai_rows[-1]
    chain = (last_dai.get("narrative_chain") or {}) if isinstance(last_dai, dict) else {}
    current = chain.get("open_threads") if isinstance(chain, dict) else None
    if not isinstance(current, list):
        return []
    out = []
    for t in current:
        key = str(t).strip()
        if not key:
            continue
        age = int(last_turn) - first_seen.get(key, int(last_turn))
        out.append((key, age))
    out.sort(key=lambda x: (-x[1], x[0]))
    return out


def _format_emotion_arc(npc: str, traj: List[Dict[str, Any]]) -> str:
    """emotion_log 궤적(오래된→최신) → 1줄 요약."""
    if not traj:
        return ""
    bases = [t.get("base") for t in traj if t.get("base")]
    dominant = max(set(bases), key=bases.count) if bases else "?"
    spikes = sum(1 for t in traj if t.get("spike"))
    half = max(1, len(traj) // 2)
    early = sum(float(t.get("intensity", 0) or 0) for t in traj[:half]) / half
    late_slice = traj[half:] or traj
    late = sum(float(t.get("intensity", 0) or 0) for t in late_slice) / len(late_slice)
    if late > early + 0.08:
        trend = "rising"
    elif late < early - 0.08:
        trend = "fading"
    else:
        trend = "held"
    return f"{npc}: dominant {dominant}, intensity {trend} ({early:.2f}->{late:.2f}), spikes {spikes}/{len(traj)}"


def _format_attitude_shifts(rows: List[Dict[str, Any]], cap: int = 5) -> List[str]:
    """attitude_log 행(오래된→최신) → 'T12 리아 wary→open (사유)' 라인.
    initial 제외(확립은 이력이 아니라 기준점) — accepted/clamped 실전이만."""
    out = []
    for r in rows:
        if not isinstance(r, dict) or r.get("result") not in ("accepted", "clamped"):
            continue
        reason = (r.get("reason") or "").strip()
        reason_str = f" ({reason[:40]})" if reason else ""
        clamp_str = " [clamped]" if r.get("result") == "clamped" else ""
        out.append(
            f"T{r.get('turn', '?')} {r.get('npc_name', '?')} "
            f"{r.get('from') or '?'}->{r.get('to', '?')}{clamp_str}{reason_str}"
        )
    return out[-cap:]


def _format_screen_time(sums: Dict[str, float], autonomy_counts: Dict[str, int]) -> List[Tuple[str, float]]:
    """감정 강도 합 + 자율 트리거 수 → 정규화 비중 내림차순. [(npc, share 0~1)]"""
    if not sums and not autonomy_counts:
        return []
    names = set(sums) | set(autonomy_counts)
    raw = {n: float(sums.get(n, 0.0)) + 0.5 * autonomy_counts.get(n, 0) for n in names}
    total = sum(raw.values())
    if total <= 0:
        return []
    return sorted(((n, v / total) for n, v in raw.items()), key=lambda x: -x[1])


# =========================================================
# fetch 래퍼 (DB 접촉 — 실패 시 빈값)
# =========================================================

def pacing_curve(channel_id: str, n: int = 8) -> str:
    try:
        import sqlite_store
        return _format_pacing_curve(sqlite_store.read_turn_snapshots(channel_id, limit=n))
    except Exception as e:
        logger.debug(f"[NQ] pacing_curve skip: {e}")
        return ""


def attitude_shifts(channel_id: str, n: int = 20, cap: int = 5) -> List[str]:
    """[고아 승격 2026-07-18] read_attitude_log → 최근 실전이 라인.
    태도가 '왜 지금 이 값인지'의 이력 급식 (D2 감정부채와 동병동처방)."""
    try:
        import sqlite_store
        return _format_attitude_shifts(sqlite_store.read_attitude_log(channel_id, limit=n), cap=cap)
    except Exception as e:
        logger.debug(f"[NQ] attitude_shifts skip: {e}")
        return []


def recent_beats(channel_id: str, n: int = 10, cap: int = 8) -> List[str]:
    try:
        import sqlite_store
        return _format_recent_beats(sqlite_store.read_dai_logs(channel_id, limit=n), cap=cap)
    except Exception as e:
        logger.debug(f"[NQ] recent_beats skip: {e}")
        return []


def thread_ages(channel_id: str, n: int = 40) -> List[Tuple[str, int]]:
    try:
        import sqlite_store
        return _format_thread_ages(sqlite_store.read_dai_logs(channel_id, limit=n))
    except Exception as e:
        logger.debug(f"[NQ] thread_ages skip: {e}")
        return []


def emotion_arc(channel_id: str, npc: str, n: int = 12) -> str:
    try:
        import sqlite_store
        return _format_emotion_arc(npc, sqlite_store.read_emotion_trajectory(channel_id, npc, limit=n))
    except Exception as e:
        logger.debug(f"[NQ] emotion_arc skip: {e}")
        return ""


def emotion_residue(channel_id: str, current_turn: int, n: int = 8, cap: int = 4) -> List[str]:
    """[2026-07-15] 잔열의 출처 — 과거 spike × 그 턴의 사건.

    read_emotion_spikes(고아 승격: 외부 호출 0건이었음) + read_dai_logs 조인.
    dai_logs limit은 spike 조회창(n)보다 넉넉히(x5, 상한 100=append_dai_log keep) —
    스파이크가 dai 보관창 밖이면 조인이 조용히 실패하므로.
    """
    try:
        import sqlite_store
        spikes = sqlite_store.read_emotion_spikes(channel_id, limit=n)
        if not spikes:
            return []
        _span = max(int(n) * 5, 40)
        dai_rows = sqlite_store.read_dai_logs(channel_id, limit=min(_span, 100))
        return _format_emotion_residue(spikes, dai_rows, current_turn, cap=cap)
    except Exception as e:
        logger.debug(f"[NQ] emotion_residue skip: {e}")
        return []


def last_seen_turns(channel_id: str) -> Dict[str, int]:
    try:
        import sqlite_store
        return sqlite_store.read_npc_last_turns(channel_id)
    except Exception as e:
        logger.debug(f"[NQ] last_seen skip: {e}")
        return {}


def screen_time(channel_id: str, turn_from: int, turn_to: int) -> List[Tuple[str, float]]:
    try:
        import sqlite_store
        sums = sqlite_store.read_npc_intensity_sums(channel_id, turn_from, turn_to)
        auto = sqlite_store.read_autonomy_log(channel_id, limit=200)
        counts: Dict[str, int] = {}
        for row in auto:
            t = row.get("turn")
            if t is not None and turn_from <= int(t) <= turn_to:
                nm = str(row.get("npc_name", "") or "")
                if nm:
                    counts[nm] = counts.get(nm, 0) + 1
        return _format_screen_time(sums, counts)
    except Exception as e:
        logger.debug(f"[NQ] screen_time skip: {e}")
        return []  # [2026-08-11 리더 §7] 형제 함수 계약 통일 — 종전 None 반환(암묵 fall-through)


# =========================================================
# [Reader-GM R4] 지속성 카운트 + 후보 큐 (reader_gm_spec §6a)
# 원칙: 독자의 단발 해석은 상태를 못 만든다 — W턴 창에서 M턴 이상 재수신된 축만 후보 승격.
# 후보는 제안일 뿐(ai_session_memory.reader_candidates, 윈도우 기반 재계산=휘발) —
# 생성·결정은 기존 기관(시계/퀘스트/아크/스토리텔러)이 자기 규칙으로 소비(소비 배선=R4b, FEED 게이트).
# =========================================================

_READER_STOP = {"this", "that", "with", "from", "into", "what", "where", "when", "will",
                "would", "could", "next", "turn", "still", "them", "they", "their", "have"}
_READER_PERSIST_FIELDS = ("live_threads", "table_affordances", "open_questions")
# [2026-08-11 리더 소비자] C1 — fog는 **별도 축**으로 돈다. 같은 클러스터러를 쓰되 결과를 섞지 않는다:
# reader_candidates를 먹는 기존 소비자(anomaly _reader_axes[:8] quote축 / 間 시드 입력[:6])의
# 입력 혼합비를 건드리면 안 되기 때문(철칙). 산출 키도 reader_fog로 분리 — 소비자는 서사 콜 하나뿐.
_READER_FOG_FIELDS = ("comprehension_fog",)


def _reader_note_tokens(note: str) -> set:
    """노트 → 내용어 토큰(영문 4자+, 불용어 제외). 순수 — 스모크 대상."""
    return {t for t in re.findall(r"[a-z]{4,}", str(note).lower()) if t not in _READER_STOP}


def _cluster_reader_notes(rows: List[Tuple[int, Dict[str, Any]]], min_turns: int,
                          fields: Optional[Tuple[str, ...]] = None) -> List[Dict[str, Any]]:
    """reader_log tail(오래된→최신) → 지속 축 클러스터. 순수 — 스모크 대상.

    같은 필드에서 내용어 2개 이상 공유하면 같은 축(거친 매칭 — 과소검출 허용).
    산출: [{field, note(최신), turns(재수신 턴 수)}] — turns >= min_turns만.
    fields=None(기본)이면 기존 3종 그대로 — 기존 호출·스모크 시그니처 불변."""
    _fields = tuple(fields) if fields else _READER_PERSIST_FIELDS
    clusters: List[Dict[str, Any]] = []  # {field, tokens, turns:set, note}
    for turn, digest in rows:
        for f in _fields:
            for it in (digest.get(f) or []):
                toks = _reader_note_tokens(it.get("note", ""))
                if not toks:
                    continue
                for c in clusters:
                    if c["field"] == f and len(c["tokens"] & toks) >= 2:
                        c["turns"].add(turn)
                        c["tokens"] |= toks
                        c["note"] = it.get("note", c["note"])  # 최신으로 갱신
                        c["quote"] = it.get("quote", c.get("quote", ""))  # 한국어 인용 — R4b 부스트 매칭 열쇠
                        break
                else:
                    clusters.append({"field": f, "tokens": set(toks), "turns": {turn},
                                     "note": it.get("note", ""), "quote": it.get("quote", "")})
    return [
        {"field": c["field"], "note": c["note"], "quote": c.get("quote", ""), "turns": len(c["turns"])}
        for c in clusters if len(c["turns"]) >= min_turns
    ]


def reader_persistence(channel_id: str) -> List[Dict[str, Any]]:
    """지속성 게이트 통과 축을 계산해 후보 큐(ai_session_memory.reader_candidates)에 반영.

    reader_gm.run_reader 말미(백그라운드)에서 호출 — 턴 지연 0. 실패=빈값·무동작."""
    try:
        import config
        import sqlite_store
        # [2026-08-11 리더 §7] 창=reader_log **행 수** 기준(턴 수 아님) — READER_GM_INTERVAL>1이면
        # 실창이 5×INTERVAL턴으로 늘어난다(현행 INTERVAL=1이라 행=턴, 무해).
        window = getattr(config, "READER_PERSIST_WINDOW", 5)
        min_turns = getattr(config, "READER_PERSIST_MIN", 3)
        rows = sqlite_store.read_reader_log_tail(channel_id, limit=window)
        promoted = _cluster_reader_notes(rows, min_turns)[:8]
        # [2026-08-11 리더 소비자] C1 — fog는 **두 번째 패스**로 따로 뽑아 별도 키에 넣는다.
        # 같은 리스트에 합치면 anomaly/시드가 보는 입력이 바뀐다(기존 소비자 입력 불변이 철칙).
        fog = _cluster_reader_notes(
            rows, min_turns, fields=_READER_FOG_FIELDS
        )[:max(0, int(getattr(config, "READER_FOG_CAP", 3)))]
        # 윈도우 기반 재계산이라 누적 아님(휘발) — 매번 통째 교체.
        import domain_manager
        domain_manager.update_session_ai_memory(
            channel_id, {"reader_candidates": promoted, "reader_fog": fog})
        if promoted:
            logger.info("[ReaderPersist] %d candidate(s): %s",
                        len(promoted),
                        "; ".join(f"{p['field']}×{p['turns']}: {p['note'][:40]}" for p in promoted))
        if fog:
            logger.info("[ReaderFog] %d persisted fog: %s",
                        len(fog),
                        "; ".join(f"×{p['turns']}: {p['note'][:40]}" for p in fog))
        return promoted
    except Exception as e:
        logger.debug(f"[NQ] reader_persistence skip: {e}")
        return []


def reader_signal_block(channel_id: str, cap_chars: int = 300) -> str:
    """[2026-08-11 리더 소비자] C1+C3 — 서사 콜에 붙는 독자 신호 블록. 없으면 "" (조건부).

    C1 fog: 지속성 게이트를 통과한 comprehension_fog = GM이 전달에 실패했고 독자가 계속
      발이 걸린 자리. 지시는 **다른 각도의 재조명** — 같은 장면 재생은 NO REPLAY 위반이자
      "수렴하지 않고 살짝 벗어나는 진행"의 반대편.
    C3 예측가능성: 최근 창에서 독자 예측이 계속 맞으면 = 전개가 뻔하다 → 굴절 1줄.
    FEED 게이트 공유. 렌더 직행 아님(좌뇌 서사 콜 입력)."""
    try:
        import config
        if not getattr(config, "READER_GM_FEED", 0):
            return ""
        import domain_manager
        mem = domain_manager.get_session_ai_memory(channel_id) or {}
        lines: List[str] = []

        fog = [f for f in (mem.get("reader_fog") or []) if isinstance(f, dict) and f.get("note")]
        fog = fog[:max(0, int(getattr(config, "READER_FOG_CAP", 3)))]
        if fog:
            _turns = max(int(f.get("turns", 0) or 0) for f in fog)
            _notes = "; ".join(str(f.get("note", "")).strip() for f in fog)[:cap_chars]
            lines.append(
                f"READER FOG (a blind reader lost these threads, persisted {_turns} turns): {_notes}"
            )
            lines.append(
                "Re-illuminate from a NEW angle; never replay the original scene."
            )

        hist = [h for h in (mem.get("reader_predict") or []) if isinstance(h, dict)]
        _win = max(1, int(getattr(config, "READER_PREDICT_WINDOW", 8)))
        _high = int(getattr(config, "READER_PREDICT_HIGH", 6))
        _recent = hist[-_win:]
        if len(_recent) >= _high > 0 and sum(1 for h in _recent if h.get("hit")) >= _high:
            lines.append(
                "PREDICTABILITY high: the table guesses your arcs — deflect slightly; "
                "bend the expected beat, do not break it."
            )

        # [2026-08-11 당일 정정] C4 momentum note의 정당한 착지는 여기다 — 렌더 직행이 아니라
        # 좌뇌 서사 콜의 재해석 재료(레티어스: "렌더 직행 애매" → SD 쪽 텍스트 회수, 본문은 이관).
        _rm_cap = int(getattr(config, "READER_MOMENTUM_CAP", 120))
        if _rm_cap > 0:
            try:
                import sqlite_store
                _rows = sqlite_store.read_reader_log_tail(channel_id, limit=1)
                _mlist = (_rows[-1][1].get("momentum") or []) if _rows else []
                for _it in _mlist:
                    _n = str(_it.get("note", "") or "").strip() if isinstance(_it, dict) else ""
                    if _n:
                        lines.append(
                            f"READER MOMENTUM (the push a blind reader felt): {_n[:_rm_cap]} "
                            "— direction material, not prose to copy."
                        )
                        break
            except Exception:
                pass

        if not lines:
            return ""
        return "### READER SIGNAL (from the blind reader's notebook — direction, not content)\n" + "\n".join(lines)
    except Exception as e:
        logger.debug(f"[NQ] reader_signal_block skip: {e}")
        return ""
