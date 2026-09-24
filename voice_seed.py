# -*- coding: utf-8 -*-
"""voice_seed.py — 세션 NPC 시드: 게이트 · 굴림 · 버퍼 · 시드 텍스트 3종.

스펙
    정본  `spec/voice_seed_spec_v4_2026-09-21.md`  (§2 표 4장 · §3 굴림 · §4 콜라주 계약 · §5 SEED CARD)
    배선  `spec/voice_seed_wiring_v4_2026-09-21.md` (§1 데이터 · §2 A·F · §3 config)
    2026-09-22.

이 PR 범위 = **배선 0** (배선 스펙 §5 "PR 1: 표+굴림+버퍼").
    이 모듈을 import 하는 기존 파일은 아직 **하나도 없다**. waterfall(§A·D·F)·theoria(§C)·
    iceberg/slot_manager(§E)·domain_manager(§G)·cognition(§J) 배선은 PR 2·3 몫이다.
    지금 여기 있는 건 그 배선이 부를 순수 함수들과, 그것들을 지키는 스모크(①②③⑤)뿐이다.

원칙 하나만 기억하면 된다 — **이론은 주소까지만**(스펙 §2.1).
    표의 `label`(handle) · `ko` · `tag` · `anchor` · `row` · `pol` · 칸 번호는 코드와 감수자의 것이다.
    모델에 닿는 문자열은 T의 `mech` 한 줄과 V/P/S 본문뿐이고, 그 조립은 아래 텍스트 함수
    셋(`seed_card_text` · `narrative_block` · `seed_sheet_text`)만 한다. 스모크 ⑤가 표 전 칸을
    순회하며 이 셋의 출력을 grep 한다 — 새 텍스트 함수를 늘리려거든 스모크도 같이 늘린다.
"""
from __future__ import annotations

import logging
import random
import re
from typing import Any, Dict, Iterable, List, Optional

import config
import domain_manager
import npc_manager
import voice_seed_tables as _T

logger = logging.getLogger(__name__)

TABLE_VERSION = _T.TABLE_VERSION

# 버퍼 저장 키 — `session_ai_memory["pending_seeds"]` (배선 스펙 §1.3).
_BUF_KEY = "pending_seeds"
BUFFER_KEY = _BUF_KEY          # 세션 스냅숏을 직접 만지는 호출자(§G 관문)용 공개 이름


# =========================================================
# config 플래그 (배선 스펙 §3)
# =========================================================
# config.py 는 이번 PR에서 안 고친다 — 전부 getattr 폴백으로 읽는다.
# 상수를 config 에 심는 건 PR 2. 그때까지 아래 기본값이 곧 스펙값이다.
def _flag(name: str, default: Any) -> Any:
    return getattr(config, name, default)


def enabled() -> bool:
    """마스터 스위치. OFF = 롤백(§3: 별도 롤백 게이트 없음, 실패 바닥=현행 콜드 즉흥)."""
    return bool(_flag("VOICE_SEED", True))


def _ttl() -> int:
    return int(_flag("VOICE_SEED_TTL", 5))


def _max_per_turn() -> int:
    return int(_flag("VOICE_SEED_MAX_PER_TURN", 3))


def _stage_reroll_limit() -> int:
    return int(_flag("VOICE_SEED_STAGE_REROLL", 5))


# =========================================================
# 이름 정규화 (배선 스펙 §G "voice_seed._norm")
# =========================================================
# 몹 표식 꼬리 — domain_manager 가 소유한 정규식을 빌려 쓴다(새 정규식 0, §G).
# 폴백은 그 모양을 베낀 것: `#2A` 또는 폴백 4자리 `#1234`.
_TAG_TAIL_RE = getattr(domain_manager, "_TAG_TAIL_RE",
                       re.compile(r'\s*#([A-Za-z0-9]{2}|\d{4})$'))


def _norm(name: Any) -> str:
    """버퍼 키·후보 대조용 정규형.

    domain_manager._normalize_npc_name 이 있으면 그것(괄호 공백·몹 표식 꼬리형 통일)을 쓰고,
    없으면 strip+casefold 만. 어느 쪽이든 마지막에 casefold 해서 대소문자를 지운다 —
    이 값은 **대조 전용**이고 사람이 볼 문자열로는 안 나간다.
    """
    if not isinstance(name, str):
        return ""
    fn = getattr(domain_manager, "_normalize_npc_name", None)
    if callable(fn):
        try:
            name = fn(name)
        except Exception:
            pass
    return name.strip().casefold()


def mob_base(name: str) -> str:
    """`'여관 주인 #2A'` → `'여관 주인'`. 표식 꼬리만 떼고 나머지는 손대지 않는다."""
    if not isinstance(name, str):
        return ""
    return _TAG_TAIL_RE.sub("", name).strip()


def _candidates(*names: Any) -> List[str]:
    """대조 후보 정규형 목록 — 원 이름 + 몹 태그 꼬리를 뗀 base(§G 후보 키 셋)."""
    out: List[str] = []
    for n in names:
        if not isinstance(n, str) or not n.strip():
            continue
        for form in (n, mob_base(n)):
            k = _norm(form)
            if k and k not in out:
                out.append(k)
    return out


# =========================================================
# 굴림 (스펙 §3 · 배선 스펙 §1.2)
# =========================================================
def _cell_sets(stage_rolls: Optional[Iterable]) -> tuple:
    """무대에 이미 선 굴림들의 T·V·S 칸 집합.

    P는 6칸뿐이라 겹침을 허용한다(스펙 §3). 입력은 roll dict 들의 목록이지만,
    `core_traits_roll`(=roll + turn)도 같은 모양이라 그대로 받는다.
    """
    t, v, s = set(), set(), set()
    for r in (stage_rolls or []):
        if not isinstance(r, dict):
            continue
        for c in (r.get("t") or []):
            if isinstance(c, str):
                t.add(c)
        if isinstance(r.get("v"), str):
            v.add(r["v"])
        if isinstance(r.get("s"), str):
            s.add(r["s"])
    return t, v, s


def seam_hint(code_a: str, code_b: str) -> str:
    """두 T 굴림의 (요인, 극성 부호) 비교 → 콜라주에 줄 한 단어 (스펙 §3).

    같은 행·부호 반대 → conflict / 같은 행·같은 부호 → amplify / 다른 행 → free.
    극성 0은 표에 없다(§2.6) — 부호는 항상 갈린다.
    """
    a = _T.T_TABLE.get(code_a) or {}
    b = _T.T_TABLE.get(code_b) or {}
    if a.get("row") != b.get("row"):
        return "free"
    return "amplify" if (int(a.get("pol", 0)) > 0) == (int(b.get("pol", 0)) > 0) else "conflict"


def roll(rng: Optional[random.Random] = None, stage_rolls: Optional[Iterable] = None) -> Dict[str, Any]:
    """T×2 · V · P · S 를 굴려 roll dict 하나 (배선 스펙 §1.2).

    · T 두 굴림이 **동일 칸이면 둘째만 다시** — 이건 상한과 무관한 불변식이다(§3).
      공존 가능성은 굴림이 아니라 표 저작(§2.6 ③ 앵커 상호 배타)이 보장하므로,
      같은 행 둘은 그대로 둔다(극성 열이 갈등/증폭을 판정한다).
    · **무대 차집합** — 이미 무대에 선 인물의 T·V·S 칸과 겹치면 다시 굴린다.
      상한 VOICE_SEED_STAGE_REROLL(5)회, 고갈하면 겹침을 **허용하고** 플래그를 세운다
      (인물을 못 만드는 것보다 겹치는 게 낫다).
    · rng 는 스모크가 주입한다(seeded 재현). 실배선 기본은 `random.Random()`(몹 태그 선례).
    """
    rng = rng or random.Random()
    t_used, v_used, s_used = _cell_sets(stage_rolls)
    limit = _stage_reroll_limit()
    state = {"rerolls": 0, "exhausted": False}

    def pick(codes, stage_avoid, hard_avoid=frozenset()):
        """stage_avoid = 무대 차집합(고갈 시 양보) / hard_avoid = 동일 칸(양보 없음)."""
        avoid = set(stage_avoid) | set(hard_avoid)
        c = rng.choice(codes)
        tries = 0
        while c in avoid and tries < limit:
            c = rng.choice(codes)
            tries += 1
        state["rerolls"] += tries
        if c in avoid:
            state["exhausted"] = True
            if c in hard_avoid:
                # 무대는 양보해도 "T 두 칸이 같다"는 양보하지 않는다 — 남은 칸에서 고른다.
                alt = [k for k in codes if k not in hard_avoid]
                if alt:
                    c = rng.choice(alt)
        return c

    t1 = pick(_T.T_CODES, t_used)
    t2 = pick(_T.T_CODES, t_used, hard_avoid={t1})
    v = pick(_T.V_CODES, v_used)
    p = rng.choice(_T.P_CODES)          # P는 겹침 허용(6칸)
    s = pick(_T.S_CODES, s_used)

    return {
        "t": [t1, t2],
        "v": v,
        "p": p,
        "s": s,
        "seam_hint": seam_hint(t1, t2),
        "table": TABLE_VERSION,
        "rerolls": state["rerolls"],
        "stage_diff_exhausted": state["exhausted"],
    }


def roll_address(r: Dict[str, Any]) -> str:
    """굴림 주소 한 토막 — `T34+T12/V52/P5/S41`. 로그 전용(모델 비노출)."""
    t = [c for c in (r.get("t") or []) if isinstance(c, str)]
    return "+".join("T" + c for c in t) + f"/V{r.get('v')}/P{r.get('p')}/S{r.get('s')}"


def _log_roll(name: str, r: Dict[str, Any]) -> None:
    """배선 스펙 §A 로그 1줄. 라벨·극성은 안 찍는다 — 주소면 표로 되짚을 수 있다."""
    logger.info("[Seed] %s: %s hint=%s rerolls=%s table=%s",
                name, roll_address(r), r.get("seam_hint"), r.get("rerolls"), r.get("table"))


# =========================================================
# 게이트 + 굴림 (배선 스펙 §2 A)
# =========================================================
def gate_and_roll(channel_id: str, relevant_npcs: Any, all_pcs: Any = None,
                  *, rng: Optional[random.Random] = None) -> Dict[str, Dict[str, Any]]:
    """이번 턴 RelevantNPCs 에서 **미등록 신규 인물만** 걸러 굴린다.

    호출 위치(PR 2): waterfall, 추출 콜 결과 확정 뒤 · 서사 콜 **앞**.
    전부 코드다 — 새 LLM 콜 0. 약한 프리필터이고 최종 권위는 §G 등록 관문에 있다.

    게이트(배선 스펙 §2 A):
      1. `_find_npc_key` 미해상만 통과 — 별칭·표기 변형까지 푸는 제1 방어선.
         해상되면 기존 인물이므로 굴리지 않는다(굴리면 인물이 갈라진다).
      2. PC 마스크 제외. `all_pcs` 는 호출자가 이미 가진 set 을 그대로 받는다
         (waterfall `_pc_masks_dai` 와 **같은 원천** — 여기서 따로 만들지 않는다).
      3. 몹 태그 이름(`is_mob_tag`) 제외 — 태그가 추출에 그대로 나왔다는 건 등록 인물이다.
      5. 상한 VOICE_SEED_MAX_PER_TURN(3). 초과분은 굴리지 않고 로그 한 줄.
      6. 이미 `pending_seeds` 에 있는 이름(지난 턴 굴렸는데 아직 미등록)은 **재굴림하지 않고**
         기존 entry 를 재사용한다 → 이 반환값(=서사 콜 입력)에는 싣지 않는다.

    **4. dead 검사는 여기 없다** — 일부러다(배선 스펙 §2 A 4항).
      게이트 1을 지난 이름은 정의상 npcs dict 에 없으므로 status 를 읽을 대상 자체가 없다.
      사망 인물이 개명·별칭 해상 실패로 새 이름처럼 보이는 경우는 이 게이트가 못 막고,
      `_npc_roster_pass` 의 dead 가드(등록 관문 쪽)가 맡는다.

    Returns: `{name: roll}` — 원 이름(정규화 안 한 RelevantNPCs 문자열)이 키다(§1.3).
    """
    if not enabled():
        return {}
    if not isinstance(relevant_npcs, (list, tuple, set)):
        return {}          # 추출 콜이 list 를 안 줬다 — 굴림 없이 조용히 빠진다(§A isinstance 방어)

    try:
        npcs = npc_manager.get_npcs(channel_id) or {}
    except Exception:
        logger.warning("[Seed] get_npcs 실패 — 이번 턴 굴림 없음", exc_info=True)
        return {}

    pc_keys = set()
    for m in (all_pcs or []):
        k = _norm(m)
        if k:
            pc_keys.add(k)

    pending = _buffer_load(channel_id)
    pending_keys = {_norm(k) for k in pending}

    # [2026-09-24 감사] 게이트 1b 재료 — 표식 인물(`경비병 #2A`)의 역할명 base. `_find_npc_key` 는 표식 키를
    #   토큰 매칭에서 빼므로 bare "경비병" 이 미해상으로 통과해, **기존 몹에 무작위 성격 카드**가 렌더러로 갔다
    #   (로스터 패스는 ②′로 기존 인물에 합류시켜 시드도 안 소비 → TTL 뒤 재굴림으로 매번 다른 성격).
    #   진짜 새 동명 개체는 로스터 패스 `roll_for`(new_individual)가 따로 굴린다 — 여기서 건너뛰어도 빈자리 없음.
    tagged_bases = set()
    for _k in npcs:
        try:
            _nk = domain_manager._normalize_npc_name(str(_k))
            _m = domain_manager._TAG_TAIL_RE.search(_nk)
            if _m:
                tagged_bases.add(npc_manager._norm_label(_nk[:_m.start()]))
        except Exception:
            continue

    # 무대 차집합의 재료 — relevant_npcs 중 **등록된** 인물의 core_traits_roll (§A 굴림).
    stage_rolls: List[Dict[str, Any]] = []
    out: Dict[str, Dict[str, Any]] = {}
    capped = False

    for raw in relevant_npcs:
        if not isinstance(raw, str) or not raw.strip():
            continue
        name = raw.strip()

        key = None
        try:
            key = domain_manager._find_npc_key(npcs, name)
        except Exception:
            key = None
        if key:                                            # 게이트 1 — 기존 인물
            prev = (npcs.get(key) or {}).get("core_traits_roll")
            if isinstance(prev, dict):
                stage_rolls.append(prev)                   # 무대에 이미 선 굴림
            continue
        if _norm(name) in pc_keys:                         # 게이트 2 — PC 마스크
            continue
        if npc_manager.is_mob_tag(name):                   # 게이트 3 — 몹 태그명
            continue
        if tagged_bases and npc_manager._norm_label(name) in tagged_bases:   # 게이트 1b — 역할명 = 기존 표식 인물
            continue
        if _candidates(name) and any(c in pending_keys for c in _candidates(name)):
            continue                                       # 게이트 6 — 지난 턴 entry 재사용
        if len(out) >= _max_per_turn():                    # 게이트 5 — 턴 상한
            if not capped:
                logger.info("[Seed] capped at %s this turn (skipped: %s)", _max_per_turn(), name)
                capped = True
            continue

        r = roll(rng, stage_rolls)
        out[name] = r
        stage_rolls.append(r)                              # 이번 턴 굴린 것도 무대의 일부
        _log_roll(name, r)

    return out


def roll_for(channel_id: str, name: str, turn_now: int,
             *, rng: Optional[random.Random] = None) -> Optional[Dict[str, Any]]:
    """게이트 없이 **이 이름 하나**를 굴려 버퍼에 "rolled" 로 앉힌다 (배선 스펙 §G new_individual).

    호출 위치: orchestration `_npc_roster_pass`, `register_ai_npc(... new_individual)` **직전**.
    그 경로의 이름은 기존 키로 해상되므로 §A 게이트를 못 지난다(굴림 없음) — 그런데 태그가
    붙는 순간 태어나는 건 **새 개체**다. 그래서 태그 전 base 이름으로 여기서 굴려 두면,
    이어지는 `update_npc("여관 주인 #2A")` 의 §G 관문이 후보 `mob_base(final_key)` 로 집어 간다.

    · 무대 차집합 **없음** — 이 자리는 렌더 뒤(서사 콜 지나감)라 무대 굴림을 모으는 의미가 없고,
      동명 별개체의 겹침은 어차피 §A 가 관리할 수 없는 자리다(겹치면 겹친 대로 둔다).
    · 콜라주도 없다 — seam·aside 는 §J 증류가 첫 정리 때 채운다.
    · **이미 entry 가 있으면 그대로 둔다**(되굴림 0, "collaged" 덮지 않기와 같은 규율).

    Returns: 버퍼에 앉은 entry(또는 이미 있던 entry). OFF·실패면 None.
    """
    if not enabled():
        return None
    nm = str(name or "").strip()
    if not nm:
        return None
    buf = _buffer_load(channel_id)
    wanted = _candidates(nm)
    for key, entry in buf.items():
        if isinstance(key, str) and any(c in _candidates(key) for c in wanted):
            return entry if isinstance(entry, dict) else None      # 이미 굴려 둔 것이 있다
    r = roll(rng)
    _log_roll(nm, r)
    if not buffer_put(channel_id, {nm: {"roll": r}}, turn_now):
        return None
    return _buffer_load(channel_id).get(nm)


# =========================================================
# 텍스트 3종 — 모델에 닿는 유일한 조립점
# =========================================================
# 아래 셋의 출력에는 label·anchor·row·pol·칸 번호가 **절대** 안 들어간다(스펙 §2.1·스모크 ⑤).
def _mechs(r: Dict[str, Any]) -> List[str]:
    return [(_T.T_TABLE.get(c) or {}).get("mech", "")
            for c in (r.get("t") or []) if isinstance(c, str)]


def _roll_of(entry: Any) -> Dict[str, Any]:
    """entry(버퍼/merge_collage 결과)든 roll dict 든 받아 roll 을 꺼낸다."""
    if isinstance(entry, dict):
        inner = entry.get("roll")
        if isinstance(inner, dict):
            return inner
        if "t" in entry:
            return entry
    return {}


def seed_card_text(entry: Dict[str, Any], name: str = "") -> str:
    """SEED CARD — 렌더 브리핑 블록 (스펙 §5 · 배선 스펙 §2 E 형식).

        ### <이름>
        Core Traits: <기전 1> / <기전 2> / <seam>
        Aside: <aside>

    기전만이다. 라벨·앵커·요인·극성·칸 번호 어디에도 없다.
    명령문 0 · 수량/형식 지시 0 — 약한 모델은 무시하고 강한 모델은 없어도 한다(§5).
    seam 이 없으면(state="rolled") Core Traits 는 두 항목, Aside 줄은 통째로 빠진다.
    """
    r = _roll_of(entry)
    parts = [m for m in _mechs(r) if m]
    seam = entry.get("seam") if isinstance(entry, dict) else None
    if isinstance(seam, str) and seam.strip():
        parts.append(seam.strip())
    if not parts:
        return ""
    lines = []
    if name:
        lines.append(f"### {name}")
    lines.append("Core Traits: " + " / ".join(parts))
    aside = entry.get("aside") if isinstance(entry, dict) else None
    if isinstance(aside, str) and aside.strip():
        lines.append("Aside: " + aside.strip())
    return "\n".join(lines)


# 서사 콜(§2 C) 블록 머리. 게이트 통과 인물이 없으면 블록 자체가 없다(프롬프트 순증 0, §4).
_NEWCOMERS_HEAD = "### NEWCOMERS (unregistered this turn — rolled skeleton, seam these)"


def narrative_block(rolls: Dict[str, Dict[str, Any]]) -> str:
    """서사 콜 입력 블록 (배선 스펙 §2 C 형식).

        ### NEWCOMERS (...)
        - <name> [hint: conflict]
          trait 1: <mech>
          trait 2: <mech>
          vocabulary well: <V text>
          under pressure: <P text>
          speech rule: <S text>

    여기도 기전·본문뿐 — hint 한 단어만 코드가 얹는다(그건 라벨이 아니라 지시다).
    """
    if not isinstance(rolls, dict) or not rolls:
        return ""
    lines = [_NEWCOMERS_HEAD]
    for name, r in rolls.items():
        r = _roll_of(r)
        if not r:
            continue
        lines.append(f"- {name} [hint: {r.get('seam_hint', 'free')}]")
        for i, mech in enumerate(_mechs(r), start=1):
            if mech:
                lines.append(f"  trait {i}: {mech}")
        v = (_T.V_TABLE.get(r.get("v")) or "").strip()
        p = (_T.P_TABLE.get(r.get("p")) or "").strip()
        s = (_T.S_TABLE.get(r.get("s")) or "").strip()
        if v:
            lines.append(f"  vocabulary well: {v}")
        if p:
            lines.append(f"  under pressure: {p}")
        if s:
            lines.append(f"  speech rule: {s}")
    return "\n".join(lines) if len(lines) > 1 else ""


def seed_sheet_text(entry: Dict[str, Any]) -> str:
    """등록 관문(§G)이 `data["description"]` 에 넣는 lore 절 원문 (배선 스펙 §1.4).

        ### Core Traits
        <기전 1>
        <기전 2>
        <seam>            ← state=="rolled" 면 이 줄 없음

        ### Aside
        <aside>           ← state=="rolled" 면 절 자체 없음

    `###` h3형 = `_parse_sections` 가 절 둘로 가르고 `map_sheet_sections` 가 enum 정확 일치로
    앉힌다 — **새 절 이름 0**. 기전 두 줄은 canon 이라 증류가 안 건드리고(§6),
    seam 은 **셋째 줄 고정**이다(증류가 줄 번호로 바꾼다 — 텍스트 대조가 아니다, §1.4).
    """
    r = _roll_of(entry)
    mechs = [m for m in _mechs(r) if m]
    if not mechs:
        return ""
    body = list(mechs)
    seam = entry.get("seam") if isinstance(entry, dict) else None
    if isinstance(seam, str) and seam.strip():
        body.append(seam.strip())
    out = ["### Core Traits"] + body
    aside = entry.get("aside") if isinstance(entry, dict) else None
    if isinstance(aside, str) and aside.strip():
        out += ["", "### Aside", aside.strip()]
    return "\n".join(out)


# =========================================================
# 콜라주 병합 (배선 스펙 §2 D 2)
# =========================================================
SEAM_MAX = 160
ASIDE_MAX = 480


def _clip(value: Any, limit: int) -> Optional[str]:
    """str 아니면 None. 길면 자른다 — 서사 콜이 문단을 흘려도 프로필 블록이 안 부푼다."""
    if not isinstance(value, str):
        return None
    v = value.strip()
    if not v:
        return None
    return v[:limit]


def merge_collage(rolls: Dict[str, Dict[str, Any]],
                  collage: Any) -> Dict[str, Dict[str, Any]]:
    """굴림 + 서사 콜 출력 → `{name: {"roll", "seam", "aside"}}` (배선 스펙 §2 D 2).

    · **굴린 이름만 채택.** 서사 콜이 목록 밖 이름을 만들면 버리고 로그 한 줄
      (모델이 인물을 발명하는 자리를 여기서 막는다).
    · seam/aside 가 str 이 아니면 None — 서사 콜 실패(`{}` 폴백)면 전부 None =
      state "rolled" 로 그대로 진행한다. 카드는 기전 두 줄만이고, 그것만으로도 도달했다(run5 C).
    """
    out: Dict[str, Dict[str, Any]] = {}
    if not isinstance(rolls, dict):
        return out
    coll = collage if isinstance(collage, dict) else {}
    seen = {_norm(k): k for k in coll if isinstance(k, str)}

    for name, r in rolls.items():
        r = _roll_of(r)
        if not r:
            continue
        got = coll.get(name)
        if not isinstance(got, dict):
            alt = seen.get(_norm(name))
            got = coll.get(alt) if alt else None
        got = got if isinstance(got, dict) else {}
        out[name] = {"roll": r,
                     "seam": _clip(got.get("seam"), SEAM_MAX),
                     "aside": _clip(got.get("aside"), ASIDE_MAX)}

    if coll:
        stray = [k for k in coll if _norm(k) not in {_norm(n) for n in out}]
        if stray:
            logger.info("[Seed] collage dropped (not rolled this turn): %s", stray)
    return out


# =========================================================
# 버퍼 (배선 스펙 §1.3 · §2 F)
# =========================================================
# 저장소 = session_ai_memory["pending_seeds"] — 휘발 세션 메모리다(world_mail 큐 선례).
# 세션이 끝나면 같이 사라지는 게 옳다: 시드는 "이번 장면의 소품"이고, 안 앉으면 TTL로 진다.
def _buffer_load(channel_id: str) -> Dict[str, Any]:
    try:
        buf = (domain_manager.get_session_ai_memory(channel_id) or {}).get(_BUF_KEY)
    except Exception:
        logger.warning("[Seed] 버퍼 읽기 실패", exc_info=True)
        return {}
    return dict(buf) if isinstance(buf, dict) else {}


def _buffer_save(channel_id: str, buf: Dict[str, Any]) -> None:
    try:
        domain_manager.update_session_ai_memory(channel_id, {_BUF_KEY: buf})
    except Exception:
        logger.warning("[Seed] 버퍼 쓰기 실패", exc_info=True)


def buffer_put(channel_id: str, seeds: Dict[str, Any], turn_now: int) -> int:
    """굴림(+콜라주)을 버퍼에 적재 (배선 스펙 §2 F 쓰기). 반환 = 적재 건수.

    entry = `{"roll", "seam", "aside", "born_turn", "state"}`.
    state = seam·aside 둘 다 있으면 "collaged", 아니면 "rolled".
    **기존 entry 가 "collaged" 면 덮지 않는다** — 콜라주는 서사 콜 한 번뿐이라 되굴림보다 귀하다.
    "rolled" 위에 덮어 쓸 때는 `born_turn` 을 보존한다(TTL 시계를 되감지 않는다).
    """
    if not enabled() or not isinstance(seeds, dict) or not seeds:
        return 0
    buf = _buffer_load(channel_id)
    n = 0
    for name, value in seeds.items():
        if not isinstance(name, str) or not name.strip():
            continue
        r = _roll_of(value)
        if not r:
            continue
        seam = _clip((value or {}).get("seam"), SEAM_MAX) if isinstance(value, dict) else None
        aside = _clip((value or {}).get("aside"), ASIDE_MAX) if isinstance(value, dict) else None
        prev = buf.get(name)
        if isinstance(prev, dict) and prev.get("state") == "collaged":
            continue
        born = prev.get("born_turn") if isinstance(prev, dict) else None
        buf[name] = {
            "roll": r,
            "seam": seam,
            "aside": aside,
            "born_turn": int(born if isinstance(born, int) else turn_now),
            "state": "collaged" if (seam and aside) else "rolled",
        }
        n += 1
    if n:
        _buffer_save(channel_id, buf)
    return n


def buffer_take(channel_id: str, key_candidates: Any) -> Optional[Dict[str, Any]]:
    """후보 이름들로 entry 하나를 꺼낸다(꺼내면 버퍼에서 지운다) — 배선 스펙 §2 F 읽기.

    대조는 정규형(`_norm`)으로 한다. 후보에는 몹 표식 꼬리를 뗀 base 도 자동으로 붙는다
    (`'여관 주인 #2A'` 로 등록되는 개체가 `'여관 주인'` 으로 굴린 시드를 집는다, §G).
    버퍼 키 쪽도 base 형을 같이 본다 — 어느 쪽에 꼬리가 붙었든 만난다.
    """
    if not enabled():
        return None
    if isinstance(key_candidates, str):
        key_candidates = [key_candidates]
    if not isinstance(key_candidates, (list, tuple, set)):
        return None
    wanted = _candidates(*key_candidates)
    if not wanted:
        return None

    buf = _buffer_load(channel_id)
    for key in list(buf):
        if not isinstance(key, str):
            continue
        if any(c in _candidates(key) for c in wanted):
            entry = buf.pop(key)
            _buffer_save(channel_id, buf)
            return entry if isinstance(entry, dict) else None
    return None


def buffer_state(channel_id: str) -> Dict[str, Any]:
    """현재 버퍼 사본. 저장 스냅숏에 되실어 주는 호출자(§G 관문)를 위한 공개 읽기."""
    return _buffer_load(channel_id)


def tick(channel_id: str, turn_now: int) -> int:
    """TTL 퍼지 (배선 스펙 §2 F). 반환 = 지운 건수.

    호출 위치(PR 2): waterfall 진입, 게이트 **직전**.
    `turn_now − born_turn > VOICE_SEED_TTL(5)` 이면 조용히 지운다 —
    만료는 실패가 아니라 "그 인물은 장면 소품이었다"는 뜻이다(스펙 §6).
    """
    if not enabled():
        return 0
    buf = _buffer_load(channel_id)
    if not buf:
        return 0
    ttl = _ttl()
    gone = []
    for key, entry in list(buf.items()):
        born = (entry or {}).get("born_turn") if isinstance(entry, dict) else None
        if not isinstance(born, int):
            continue
        if turn_now - born > ttl:
            buf.pop(key, None)
            gone.append((key, born))
    if gone:
        for key, born in gone:
            logger.info("[Seed] expired: %s (born %s)", key, born)
        _buffer_save(channel_id, buf)
    return len(gone)
