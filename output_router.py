# -*- coding: utf-8 -*-
"""output_router.py — 준비물 텍스트 → 닫힌 항목 N개 → 기존 관문 (스펙 v0.2 §3.1~§3.3 · §0.5)

[2026-09-06 P6] 무엇이 바뀌나: `!출력룰 추가` + 텍스트 파일 첨부가 지금까지 **파일 = 규칙 1개**
  (키=파일명 → Slot 33 통짜)였다. 이제 **파일 = 항목 N개**다. 등록 시점에 1회성 heavy 콜을
  한 번(파일이 크면 문단 경계로 쪼개 순차) 돌려 닫힌 스키마 배열을 받고, 항목마다 **이미 있는
  관문**에 넣는다. 턴 경로 콜은 0 — 이 모듈은 `run_turn` 에서 한 번도 불리지 않는다.

세 가지 규율이 이 파일의 뼈다:
  ① **라우터는 이해하지 않는다, 자리만 정한다.** `format` 항목의 원문은 한 글자도 안 고친다.
  ② **강등은 "문법 밖"만.** 식이 `expr_engine.compile_expr` 을 못 지나면 거부가 아니라
     `narrative`(패널 섹션)로 내려보낸다 — 지켜짐은 잃되 출력은 산다(§0.5 마지막 문단).
     거부(✗)로 남는 것은 "이름 없음"과 "이름 충돌" 둘뿐이다.
  ③ **재등록은 값을 안 죽인다.** 같은 `source` 로 다시 오면 이름 기준 diff: 있는 이름은 선언
     갱신(현재값 보존 — `custom_vars.register` 가 이미 그 계약이다), 없어진 이름은 **안 지우고**
     되비침에 `삭제?` 로만 띄운다. 지우는 것은 사람이 말할 때만(`!출력룰 삭제`).

명령·하위 동사·버튼·env 신설 0. discord import 0.
"""
import json
import logging
import time
from typing import Any, Dict, List, Optional, Tuple

import config

logger = logging.getLogger("OutputRouter")

# 파일이 이보다 크면 문단 경계로 쪼개 순차 콜(§3.1). 등록은 여전히 1회성이다.
MAX_CHUNK_BYTES = 8000
MAX_ITEMS_PER_CALL = 40
MAX_TOTAL_ITEMS = 120
NAME_MAX = 24
RULE_MAX = 300
FORMAT_TEXT_MAX = 4000

# [2026-09-13 P16] 다섯째 종류 `directive`(지시) — 조건이 참인 **동안** 산문에 붙는 유저 문장.
#   값을 안 쓰므로 전이 뒤에 선다: 순서는 "값 → 그 값을 읽는 것들" 하나뿐이다.
KINDS = ("value", "narrative", "format", "transition", "directive")
# 적용 순서는 고정이다: 값이 먼저 서야 그 값을 참조하는 전이가 "이름 없음"으로 안 튕긴다(§4 R2).
KIND_ORDER = {k: i for i, k in enumerate(KINDS)}

SURFACES = ("header", "panel", "mail", "notebook")
SCOPES = ("global", "pc", "npc", "user")
CADENCES = ("turn", "slot", "day", "week", "month", "on_transition", "on_demand")
FEED_MODES = ("always", "mentioned", "never")

_KIND_ALIASES = {
    "value": "value", "값": "value", "variable": "value", "변수": "value",
    "narrative": "narrative", "서술": "narrative", "section": "narrative", "패널": "narrative",
    "format": "format", "형식": "format", "prose": "format", "산문": "format",
    "transition": "transition", "전이": "transition", "event": "transition", "사건": "transition",
    "directive": "directive", "지시": "directive", "조건지시": "directive",
    "conditional": "directive",
}
_KIND_LABEL = {"value": "값", "narrative": "서술", "format": "형식",
               "transition": "전이", "directive": "지시(조건)"}

STATUS_MARK = {"ok": "✓", "fail": "✗", "demoted": "↓"}


# =========================================================
# 1. 프롬프트 — 라우터 콜의 원문 (보고서 §5 에 그대로 실린다)
# =========================================================
# ★서사 권능 선언(로어 분석·보이스카드가 system_instruction 에 싣는 그 상수)을 붙이지 않는다:
#   이 콜은 서사 생성이 아니라 표 분류다(`extract_schedule` 과 같은 판단). 작업 지시만 싣는다.
#   그래서 이 파일은 그 상수를 import 하지도, 이름으로 부르지도 않는다.
ROUTER_SYSTEM_INSTRUCTION = (
    "You sort a TRPG player's preparation notes into a closed list of registry items. "
    "You return one JSON object and nothing else."
)

ROUTER_PROMPT = """\
You are a ROUTER for a TRPG bot's output registry.
The player wrote preparation notes (a text file) describing what their game should track and show.
Your job is to CUT that text into closed items and say WHERE each one belongs.
Do not interpret, summarize, improve, or invent. Sorting is the whole job.

## The four kinds — pick exactly one per item

- value      A number the CODE owns and updates. gauge (bounded meter) / counter (tally) /
             enum (named stages) / list (named items each holding a count).
- narrative  A named panel section a background call rewrites each turn. It has a name and a
             continuity rule in plain language ("what this section always shows").
             `mode:"append"` instead when the notes describe a LOG that GROWS: one line added
             each time the thing happens, only the most recent few shown (a photo album, a
             list of recordings, a diary of incidents). `keep` = how many recent lines show.
- format     Prose style or structure instructions for the writer. COPY THE TEXT VERBATIM.
- transition A one-shot event: a condition becoming true, then something happens once.
- directive  A STANDING INSTRUCTION TO THE WRITER that applies only WHILE a condition holds.
             It changes no number — it changes how the scene is written ("의뢰가 3건 이상이면
             새 의뢰는 거절하게 해", "수분이 2 아래면 잎이 처진 걸 묘사해").

## Output — one JSON object, nothing else

{"items": [ {
  "kind": "value|narrative|format|transition",
  "name": "짧은 한국어 이름 (필수 — 이름이 없으면 그 항목은 버려진다)",
  "surface": "header|panel|mail|notebook",
  "scope": "global|pc|npc|user",
  "cadence": "turn|day",
  "feed": {"prose": "always|mentioned|never"},
  "raw": "이 항목이 나온 원문 한 조각",

  "value":      {"type":"gauge|counter|enum|list|text", "range":[0,100], "start":0,
                 "stages":["단계1","단계2"], "item_mode":"progress|stock", "max":30,
                 "stage_first":100, "stage_step":50,
                 "fields":{"수분":[0,5],"성장":[0,12]}, "init":{"수분":2,"성장":0},
                 "stage_of":"성장", "stage_at":[0,3,6,9,12],
                 "rule":"언제 오르고 언제 내리는지 — 한국어 한 문장 (필수)",
                 "derive":"세율 * 인구 // 100  (다른 값에서 계산되는 값일 때만, 아니면 null)"},
  "narrative":  {"panel_section":"댓글창", "continuity_rule":"이 칸이 늘 보여주는 것", "lines":3,
                 "mode":"rewrite|append", "keep":3},
  "format":     {"text":"원문 그대로. 한 글자도 고치지 마라.",
                 "template":"표시 줄 하나를 자리표시자로 환원한 것 — 환원 불가면 null",
                 "empty":{"무장":"none"}},
  "transition": {"trigger":"expr|narrated",
                 "when":"호감도[리나] >= 70",
                 "do":"철 -= 2; 검 += 1",
                 "narrated_cue":"첫 키스",
                 "check":"judgment|none", "on_fail":null,
                 "once":true, "notify":"mail|mind|none",
                 "deliver":{"kind":"letter|bulletin|sns","from":"보낸이 이름 또는 null",
                            "brief":"무엇에 대한 편지·공고인지 한 문장"},
                 "hold_turns":0, "hold_days":0, "cooldown":0},
  "directive":  {"when":"count(의뢰) >= 3",
                 "text":"새 의뢰는 거절하게 써라 — the instruction alone, the player's own words"}
} ] }

## Rules

1. NAME every item. An item with no name is thrown away — that is the only rejection.
2. One sentence of the notes may become SEVERAL items. "호감도를 보여주고 70 넘으면 데이트 해금"
   = one value + one transition. Split it.
3. EXPRESSIONS may only use the names in DECLARED NAMES below, plus names you are creating in
   this same response, plus the read-only code names listed there. Numbers and the functions
   min max abs floor round rand has count sum day slot are allowed. Nothing else: no attributes,
   no function definitions, no loops, no strings as code.
4. IF YOU CANNOT WRITE IT AS AN EXPRESSION, MAKE IT `narrative` INSTEAD. Do not force an
   expression. "분위기가 좋으면", "적절할 때", "GM 판단으로" are narrative, not `when`.
5. `rand` only when the notes actually talk about chance or randomness.
6. `format` text is copied character for character. Never shorten it, never rewrite it,
   never translate it. If you are unsure whether something is a format instruction, it is.
6b. `format.template` — the same item, once more, as a DRAWING. If the item contains a display
   line (a status line the game paints every turn), return that ONE line again with every shown
   value replaced by `[이름]`: a value you declare in this same response, a name in DECLARED
   NAMES, one of the system placeholders listed there, or a narrative item's `[섹션/필드]`.
   `[이름.max]` / `[이름.min]` give that value's declared bounds. Labels, separators, emoji and
   spacing stay exactly as the player wrote them — you are re-pointing the line, not rewriting
   it. If the item is style guidance or an end-of-message rule (nothing to paint), return
   `"template": null`. `text` is unaffected either way — it is always the verbatim original.
   `format.empty` says what to print when a placeholder holds nothing, e.g. {"무장":"none"}.
   `format` on a letter/sign/mail item takes `"surface":"mail"`.
7. `value.rule` is required and must say what MOVES the number. Without it the extraction
   call cannot move that value.
8. `feed.prose`: "always" when the notes say this must always be visible / on the status line;
   otherwise "mentioned".
8b. `value.max` (type `list` only) — the number of ITEMS that list may hold, when the notes say
   so ("최대 30개", "칸은 12개", "슬롯 6개"). A whole number 1-99. Omit it when the notes are
   silent; the code has a default. This is the item COUNT, not `range` (what one item counts to).
9. `cadence`: "day" when the notes tie it to a day boundary or a daily summary; else "turn".
   A `transition` with "day" is evaluated ONCE at the day boundary, not every turn. Its
   `when` may be empty — "every day, X happens" is a day transition with `do` only (Rule 15).
   `hold_days` is `hold_turns` counted in days.
9c. `transition.deliver` — when the notes say something ARRIVES on that event (a letter, a
   notice, an article, a posting, a summons, a commission board entry), add `deliver` with
   `notify:"mail"`. `kind`: "letter" when it is addressed to the player character, "bulletin"
   when it is posted where anyone reads it, "sns" when it is a feed. `brief` is ONE sentence
   about what it says — not the text itself; the arrival call writes the body. Omit `deliver`
   entirely when nothing arrives.
9a. `narrative.mode` — "append" when the section is a record that ACCUMULATES one line per
   event and shows only the latest few ("사진첩", "찍은 영상 목록", "사건 기록"); "rewrite"
   (the default) when the section is a picture of NOW that is redrawn every turn. `keep` is
   how many recent lines stay on screen — omit it and the code picks. An append section's
   `continuity_rule` says WHEN a line is added, not what the section always shows.
9b. `value.fields` — a `list` whose every item carries THE SAME SET of named numbers (a pot
   with water/nutrient/sun/growth/age; a shop item with stock/price/quality; a village with
   population/order/tax) is a RECORD list. Give `fields` as {name:[min,max]} in display order
   and `init` as the value each field starts at when an item is created. If one field drives a
   named stage, add `stages` plus `stage_of` (that field) and `stage_at` (the threshold of each
   stage, ascending, same length as `stages`). A list whose items are just a count keeps no
   `fields` at all. Expressions read a record field with TWO subscripts: `화분["양파"]["수분"]`.
   `each(목록,"필드",델타)` moves that field on every item; `each(목록,"필드",델타,"조건필드",최소,…)`
   only on items where every named field is at least that much. `add(목록,"항목")` /
   `remove(목록,"항목")` create and destroy an item. There are no loops — if a rule needs one
   ("whichever pot has been dry for two days"), make it `narrative`.
10. Items you are unsure about still get emitted — with the kind that loses the least.
    Order of preference when torn: value > transition > narrative > format.
12. `directive` — "X이면 …하게 해 / …로 써라 / …를 묘사해": a CONDITION plus an instruction
    about the WRITING, not about a number. `when` follows Rule 3 exactly (same names, same
    functions); `text` is the instruction WITHOUT its condition clause, copied in the player's
    own words, one line. A directive never changes a value — if the sentence moves a number,
    that part is a `transition`, and you may emit both. An instruction with NO condition
    (always in force) is `format`, never a directive with `when` invented for it.
13. `value.type":"text"` — a value that is a SHORT LINE OF WORDS, not a number: a nickname,
    a current title, the name of where the party is, one line of rumour. It has no `range`,
    no `stages`, no caps; `start` is the opening text. Use it only when the notes name a thing
    that is written, not counted. Long prose is `narrative`, never `text`.
13b. TRUE/FALSE — 참/거짓, 했다/안 했다, 열림/닫힘, 있다/없다 — is an `enum` with exactly TWO
    stages, the false one first (`"stages":["거짓","참"]`, `start` the false one). There is no
    boolean type: two stages IS the boolean, and it shows on the panel with the player's words.
14. `a if c else b` is allowed anywhere an expression is (`derive`, `when`, `do`): 
    `"견습" if 명성 <= 150 else "장인"`. Nest it at most 4 deep — deeper is a `stages` table,
    not an expression. Write it in this form; `? :` is not accepted.
15. A `transition` with NO `when` runs its `do` EVERY cadence — every turn, or every day when
    `cadence` is "day". That is the shape for "매턴 …", "매일 …", "하루가 끝나면 정산" —
    an ongoing CALCULATION (`금 = 금 + 매출`), not an event. It never notifies, never records,
    and `once`/`hold`/`cooldown` mean nothing on it. An event that happens WHEN something
    becomes true keeps its `when`.
16. `transition.cooldown` — N cadence units during which this transition will NOT fire again
    after firing, even if its condition goes false and true again. Use it for random events
    (`when` with `rand`) the notes say must not repeat right away. `once` beats `cooldown`.
17. DEADLINES — when the notes say something expires ("3일 뒤 시들다", "기한까지", "일주일이
    지나면 사라진다"), store the FIXED END, not a countdown: give the record list a field named
    기한, set it once when the item is created (`화분["양파"]["기한"] = 날 + 3`), and add a
    day transition whose `do` is `expire(화분,"기한")`. `날` is the read-only running day count
    of the 360-day calendar (`일` is the day WITHIN the month and wraps). The code prints a
    기한 field as `남은 n일`. "X까지 얼마나 남았나" as a number is a derive: `X - 값`.
18. `[이름.남은]` / `[이름.퍼센트]` in a `format.template` give what is LEFT to the end and how
    far along it is — the same pair the panel already draws as `값/최대`. They work on a value
    with a range and on a record field (`[화분/양파/기한.남은]` = days left). Do not invent a
    derive for "얼마 남았나" when the suffix says it.
19. EXPERIENCE BARS / LEVELS — "레벨마다 경험치 100씩 더 필요" is ONE accumulating gauge with
    `stage_first` (what level 2 costs) and `stage_step` (how much more each level costs after
    that); the code expands that into the level boundaries and names them. Never emit a
    transition that resets 경험치 to 0 and adds 1 to 레벨 — the total is the record, and
    `[경험치.남은]` / `[경험치.퍼센트]` are the bar.
20. `sum(목록)` adds up the quantities in a list; `count(목록)` counts the items that have any.
    `add(목록,"항목")` puts an item in with quantity 1, so `has`/`count` see it at once.
11. Korean names. JSON only. No prose, no code fence, no commentary.
"""


# =========================================================
# 2. 순수 함수 — 쪼개기 · 정규화 · 순서
# =========================================================
def split_chunks(text: Any, limit: int = MAX_CHUNK_BYTES) -> List[str]:
    """문단(빈 줄) 경계로 쪼갠다. 한 문단이 상한을 넘으면 줄 경계로 한 번 더.

    경계를 문단에 두는 이유: 준비물 파일은 "한 문단 = 한 규칙"으로 쓰이는 일이 많고,
    글자 수로 자르면 한 규칙이 두 콜에 걸쳐 둘 다 반쪽으로 읽힌다.
    """
    body = str(text or "").strip()
    if not body:
        return []
    if len(body.encode("utf-8")) <= limit:
        return [body]

    def _blocks(src: str, sep: str) -> List[str]:
        return [b for b in src.split(sep) if b.strip()]

    out: List[str] = []
    cur = ""
    for para in _blocks(body, "\n\n"):
        pieces = [para]
        if len(para.encode("utf-8")) > limit:
            pieces = _blocks(para, "\n") or [para]
        for piece in pieces:
            cand = (cur + "\n\n" + piece) if cur else piece
            if cur and len(cand.encode("utf-8")) > limit:
                out.append(cur)
                cur = piece
            else:
                cur = cand
    if cur:
        out.append(cur)
    return out


# =========================================================
# 2-a. CBS 껍데기 벗기기 — 라우터 입력 전처리 (2026-09-07 P10)
# =========================================================
# 유저 준비물은 RisuAI/SillyTavern 시트를 그대로 붙여 온 것이 많고, 그러면 `{{#if …}}`·
# `{{getvar::…}}`·`{{user}}` 같은 **다른 엔진의 문법**이 원문에 박혀 온다. 이 문법을 그대로
# 라우터 콜에 넣으면 모델이 그걸 "형식의 일부"로 읽어 `text` 에 베껴 넣고, 그 리터럴이
# 끝내 Slot 33 과 임베드에 샌다. 그래서 **조건을 평가하지 않고 껍데기만** 벗긴다 —
# 조건 블록은 본문을 남기고(둘 중 하나를 고를 근거가 이 층엔 없다), 나머지 토큰은 지운다.
_CBS_TOKEN = __import__("re").compile(r"\{\{([^{}]*)\}\}")
_CBS_MAX_PASSES = 8


def _pc_mask(channel_id: str) -> str:
    """`{{user}}` 가 가리키는 것 = 현재 PC 가면. 없으면 "PC".

    [2026-09-07 상위 정정] 가면이 없을 때 빈 문자열로 지우면 "{{user}}의 잔여 마나"가
    "의 잔여 마나"가 되어 라우터가 **스코프(pc)를 읽을 단서**를 잃는다(등록은 준비 전
    채널에서도 일어난다). 낱말 하나는 남긴다 — 화면에 뜨는 문자열이 아니라 라우터 입력이다.
    """
    try:
        import game_world as gw
        masks = gw._get_active_player_masks(channel_id) or []
        return str(masks[0]) if masks else "PC"
    except Exception as e:
        logger.debug("[Router] PC 가면 조회 skip: %s", e)
        return "PC"


def _cbs_one(inner: str, mask: str) -> str:
    low = str(inner or "").strip().lower()
    if low == "user":
        return mask
    # `{{char}}`: 형식 항목은 **채널 스코프**다 — 이 자리엔 NPC 문맥이 없다(npc_manager 의
    #   시트 치환과 달리 가리킬 대상이 없다). 리터럴로 남기면 그대로 화면에 뜨므로 지운다.
    return ""


def strip_cbs(text: Any, channel_id: str = "") -> str:
    """CBS(`{{…}}`) 껍데기 제거. 조건 평가 0 · 본문 보존 · `{{user}}` → PC 가면."""
    body = str(text or "")
    if "{{" not in body:
        return body
    mask = _pc_mask(channel_id)
    for _ in range(_CBS_MAX_PASSES):        # 안쪽부터 벗겨 나온다(중첩 `{{#if {{eq…}}}}`)
        if "{{" not in body:
            break
        nxt = _CBS_TOKEN.sub(lambda m: _cbs_one(m.group(1), mask), body)
        if nxt == body:
            break
        body = nxt
    return body.replace("{{", "").replace("}}", "")


def _s(v: Any, limit: int = 0) -> str:
    t = str(v if v is not None else "").strip()
    return t[:limit] if limit and len(t) > limit else t


def _int(v: Any, default: Optional[int] = None) -> Optional[int]:
    try:
        if isinstance(v, bool):
            return default
        return int(v)
    except (TypeError, ValueError):
        return default


def _range(v: Any) -> Optional[List[int]]:
    if isinstance(v, str):
        import re
        m = re.search(r"(-?\d+)\s*[-~:]\s*(-?\d+)", v)
        v = [m.group(1), m.group(2)] if m else None
    if isinstance(v, (list, tuple)) and len(v) == 2:
        lo, hi = _int(v[0]), _int(v[1])
        if lo is not None and hi is not None:
            return [lo, hi]
    return None


def _pick(d: Any, *keys: str) -> Any:
    if not isinstance(d, dict):
        return None
    for k in keys:
        if d.get(k) not in (None, ""):
            return d[k]
    return None


def _enum(v: Any, allowed: Tuple[str, ...], default: str) -> str:
    t = _s(v).lower()
    return t if t in allowed else default


def normalize_items(data: Any, source: str = "") -> List[Dict[str, Any]]:
    """콜 출력 → 닫힌 항목 목록. **폐기는 조용하지 않다**(로그 한 줄).

    폐기 사유는 둘뿐이다: 모르는 kind, 이름 없음. 그 밖의 잘못된 칸은 폐기가 아니라
    기본값으로 접힌다 — 라우터가 칸 하나 틀렸다고 유저의 준비물을 버리면 안 된다.
    """
    rows: Any = data
    if isinstance(data, dict):
        rows = _pick(data, "items", "항목", "list", "results") or []
    if not isinstance(rows, (list, tuple)):
        return []

    out: List[Dict[str, Any]] = []
    dropped_kind = dropped_name = 0
    for raw in rows:
        if not isinstance(raw, dict):
            continue
        kind = _KIND_ALIASES.get(_s(_pick(raw, "kind", "종류")).lower())
        if kind not in KINDS:
            dropped_kind += 1
            continue
        name = _s(_pick(raw, "name", "이름"), NAME_MAX)
        if not name or "|" in name or "\n" in name:
            dropped_name += 1
            continue

        item: Dict[str, Any] = {
            "kind": kind,
            "name": name,
            "surface": _enum(_pick(raw, "surface", "자리"), SURFACES,
                             {"value": "header", "narrative": "panel",
                              "format": "panel", "transition": "mail",
                              "directive": "panel"}[kind]),
            "scope": _enum(_pick(raw, "scope", "스코프"), SCOPES, "global"),
            "cadence": _enum(_pick(raw, "cadence", "때"), CADENCES, "turn"),
            "raw": _s(_pick(raw, "raw", "원문"), RULE_MAX),
            "source": source,
        }
        feed = _pick(raw, "feed", "급식")
        prose = _enum(_pick(feed, "prose") if isinstance(feed, dict) else feed,
                      FEED_MODES, "")
        if prose:
            item["feed"] = {"prose": prose}

        sub = raw.get(kind) if isinstance(raw.get(kind), dict) else {}
        if kind == "value":
            item["value"] = _norm_value(sub, raw)
        elif kind == "narrative":
            item["narrative"] = _norm_narrative(sub, raw, name)
        elif kind == "format":
            item["format"] = _norm_format(sub, raw)
        elif kind == "transition":
            item["transition"] = _norm_transition(sub, raw)
        else:
            item["directive"] = _norm_directive(sub, raw)
        out.append(item)
        if len(out) >= MAX_TOTAL_ITEMS:
            break

    if dropped_kind or dropped_name:
        logger.info("[Router] 폐기 — 모르는 kind %d · 이름 없음 %d (source=%s)",
                    dropped_kind, dropped_name, source)
    return out


def expand_stage_at(first: Any, step: Any, top: Any) -> List[int]:
    """[2026-09-13 P17] 레벨당 필요치 수식 → **경계 목록**. 라우터가 여기서 전개한다.

    경계 k = 경계 k-1 + (first + (k-1)*step) — "레벨마다 +step 씩 더 필요"의 누적형이다.
    `custom_vars.MAX_STAGES` 를 넘지 않고 range 의 천장(top)도 넘지 않는다.
    ★수식을 값 층으로 들여보내지 않는 이유: 단계 판정이 매 표시마다 식을 풀면 경계가
      선언이 아니라 계산이 되고, 그러면 되비침이 "무엇이 레벨 3인가"를 못 적는다.
    """
    import custom_vars as cv
    try:
        f, st, hi = int(first), int(step), int(top)
    except (TypeError, ValueError):
        return []
    if f <= 0:
        return []
    out = [0]
    cur = 0
    for k in range(1, int(getattr(cv, "MAX_STAGES", 8))):
        cur += f + (k - 1) * max(0, st)
        if cur > hi:
            break
        out.append(cur)
    return out if len(out) >= 2 else []


def _norm_value(sub: Dict[str, Any], raw: Dict[str, Any]) -> Dict[str, Any]:
    vtype = _s(_pick(sub, "type", "타입")).lower() or "gauge"
    stages = _pick(sub, "stages", "단계")
    stages = [_s(x, NAME_MAX) for x in stages if _s(x)] if isinstance(stages, (list, tuple)) else None
    # [2026-09-13 P17] 경험치바 — "레벨당 필요 경험치" 수식은 여기서 **경계 목록**이 된다.
    #   리셋 전이(경험치=0, 레벨+1)는 만들지 않는다: 누적이 정본이고 바는 `.남은`/`.퍼센트` 다.
    rng = _range(_pick(sub, "range", "범위"))
    _at_raw = _pick(sub, "stage_at", "단계문턱")
    _at_nums = ([_int(x) for x in _at_raw] if isinstance(_at_raw, (list, tuple)) else [])
    _at_nums = [x for x in _at_nums if x is not None]
    _first = _int(_pick(sub, "stage_first", "첫단계필요"))
    _step = _int(_pick(sub, "stage_step", "단계증가"))
    if not _at_nums and _first is not None and rng:
        _at_nums = expand_stage_at(_first, _step if _step is not None else 0, rng[1])
    if _at_nums and vtype in ("gauge", "counter") and not stages:
        stages = [f"레벨{i + 1}" for i in range(len(_at_nums))]
    if _at_nums and len(_at_nums) != len(stages or []):
        _at_nums = []                       # 짝이 안 맞는 경계는 버린다(값 층이 또 거른다)
    if stages and vtype in ("gauge", "counter") and not _at_nums:
        vtype = "enum"
    v: Dict[str, Any] = {"type": vtype}
    if rng:
        v["range"] = rng
    if vtype == "text":
        # text 의 시작값은 수가 아니라 **한 줄 글자**다.
        _ts = _s(_pick(sub, "start", "init", "시작값"), 200)
        if _ts:
            v["start"] = _ts
    else:
        start = _int(_pick(sub, "start", "init", "시작값"))
        if start is not None:
            v["start"] = start
    if stages:
        v["stages"] = stages
    if _at_nums and vtype in ("gauge", "counter"):
        v["stage_at"] = _at_nums
    mode = _s(_pick(sub, "item_mode", "항목모드")).lower()
    if mode:
        v["item_mode"] = mode
    # [2026-09-13 P15] `max` = 이 목록이 몇 항목까지 드나. 원문이 "최대 30개" 라고
    #   말할 때만 찬다 — 유저 문법이 아니라 **라우터 내부 표기**다. 범위 판정(1~99)은
    #   custom_vars.validate_declaration 한 곳이 하므로 여기선 수로만 옮긴다.
    cap = _int(_pick(sub, "max", "최대", "항목수"))
    if cap is not None:
        v["max"] = cap
    # [2026-09-09 P11] 레코드 목록 — 항목마다 같은 모양의 값 묶음. 칸이 없으면 옛 list 그대로다.
    flds = _pick(sub, "fields", "필드")
    if isinstance(flds, dict) and flds:
        clean_f: Dict[str, List[int]] = {}
        for fk, fv in flds.items():
            nk = _s(fk, NAME_MAX)
            rg = _range(fv)
            if nk and rg:
                clean_f[nk] = rg
        if clean_f:
            v["fields"] = clean_f
            init = _pick(sub, "init", "시작")
            if isinstance(init, dict):
                clean_i = {}
                for fk, fv in init.items():
                    nk = _s(fk, NAME_MAX)
                    iv = _int(fv)
                    if nk in clean_f and iv is not None:
                        clean_i[nk] = iv
                if clean_i:
                    v["init"] = clean_i
            so = _s(_pick(sub, "stage_of", "단계필드"), NAME_MAX)
            if so:
                v["stage_of"] = so
            at = _pick(sub, "stage_at", "단계문턱")
            if isinstance(at, (list, tuple)):
                nums = [_int(x) for x in at]
                if nums and all(x is not None for x in nums):
                    v["stage_at"] = nums
    fmt = _s(_pick(sub, "format", "표시"), 60)
    if fmt:
        v["format"] = fmt
    v["rule"] = _s(_pick(sub, "rule", "규칙") or _pick(raw, "rule", "규칙", "raw", "원문"), RULE_MAX)
    derive = _s(_pick(sub, "derive", "파생"), RULE_MAX)
    v["derive"] = derive or None
    return v


def _norm_narrative(sub: Dict[str, Any], raw: Dict[str, Any], name: str) -> Dict[str, Any]:
    """[2026-09-09 P12] 칸 둘 순증 — `mode`(rewrite|append) · `keep`(표시 창).

    기본은 rewrite 라 옛 산출·옛 저장분은 한 글자도 안 바뀐다. 상한 클램프는
    status_panel 이 문다(선언 층의 관문은 거기 하나여야 한다).
    """
    return {
        "panel_section": _s(_pick(sub, "panel_section", "섹션") or name, NAME_MAX),
        "continuity_rule": _s(_pick(sub, "continuity_rule", "rule", "규칙")
                              or _pick(raw, "rule", "raw", "원문"), RULE_MAX),
        "lines": _int(_pick(sub, "lines", "줄수")),
        "mode": _enum(_pick(sub, "mode", "방식") or _pick(raw, "mode", "방식"),
                      ("rewrite", "append"), "rewrite"),
        "keep": _int(_pick(sub, "keep", "표시줄")),
    }


def _norm_format(sub: Dict[str, Any], raw: Dict[str, Any]) -> Dict[str, Any]:
    """형식은 **원문 그대로**. strip 밖의 손질은 하지 않는다(§6 형식 원문 불변).

    [2026-09-07 P10] 옆칸이 둘 늘었다 — `template`(표시 줄의 자리표시자 환원형)과
    `empty`(빈 값의 표기 표). `text` 는 종전과 한 글자도 다르지 않다: 환원이 실패하든
    성공하든 **원문은 언제나 Slot 33 으로 간다**(강등≠폐기).
    """
    text = _pick(sub, "text", "원문", "desc") or _pick(raw, "text", "raw", "원문")
    tpl = _s(_pick(sub, "template", "틀", "표시틀"), FORMAT_TEXT_MAX)
    blanks: Dict[str, str] = {}
    src_empty = _pick(sub, "empty", "빈값")
    if isinstance(src_empty, dict):
        for k, v in src_empty.items():
            nk = _s(k, NAME_MAX)
            if nk:
                blanks[nk] = _s(v, NAME_MAX)
    return {"text": _s(text, FORMAT_TEXT_MAX), "template": tpl or None, "empty": blanks}


def _norm_deliver(src: Any) -> Optional[Dict[str, Any]]:
    """[2026-09-13 P14] `deliver` 서브dict → 칸 셋. 아니면 None.

    종류 정규화는 여기서 **하지 않는다** — 등록기(expr_engine.register_transition)가
    한 곳에서 접는다(두 곳에서 접으면 두 기본값이 생긴다).
    """
    if not isinstance(src, dict):
        return None
    return {"kind": _s(_pick(src, "kind", "종류")).lower() or "letter",
            "from": _s(_pick(src, "from", "보낸이", "발신"), NAME_MAX) or None,
            "brief": _s(_pick(src, "brief", "요지", "무엇"), RULE_MAX)}


def _norm_transition(sub: Dict[str, Any], raw: Dict[str, Any]) -> Dict[str, Any]:
    cue = _s(_pick(sub, "narrated_cue", "큐"), RULE_MAX)
    trig = _s(_pick(sub, "trigger", "방아쇠")).lower()
    if trig not in ("expr", "narrated", "operation"):
        trig = "narrated" if cue and not _pick(sub, "when") else "expr"
    return {
        "trigger": trig,
        "when": _s(_pick(sub, "when", "조건"), RULE_MAX) or None,
        "do": _s(_pick(sub, "do", "실행"), RULE_MAX) or None,
        "on_fail": _s(_pick(sub, "on_fail", "실패"), RULE_MAX) or None,
        "narrated_cue": cue or None,
        "check": _enum(_pick(sub, "check", "판정"), ("judgment", "none"), "none"),
        "once": bool(_pick(sub, "once", "1회")),
        "hold_turns": _int(_pick(sub, "hold_turns"), 0) or 0,
        "hold_days": _int(_pick(sub, "hold_days"), 0) or 0,
        # [2026-09-24 감사] 스키마(L125)·규칙 16 이 요구하는 칸인데 정규화가 빠뜨려
        #   register_transition 에 늘 0 으로 도착했다(쿨다운 사문).
        "cooldown": _int(_pick(sub, "cooldown", "쿨다운"), 0) or 0,
        "notify": _enum(_pick(sub, "notify", "알림"), ("mail", "mind", "none"), "mail"),
        # [2026-09-07 P10] 도착물이 **어떤 형식으로** 도착하는가. 이름만 나른다(본문 렌더 0).
        "notify_format": _s(_pick(sub, "notify_format", "도착물형식"), NAME_MAX) or None,
        # [2026-09-13 P14] 도착물 선언 — 칸 셋만 나른다(본문 0). 정규화·기본값은 expr_engine.
        "deliver": _norm_deliver(_pick(sub, "deliver", "도착")),
        "record": sub.get("record") if isinstance(sub.get("record"), dict) else None,
    }


def _norm_directive(sub: Dict[str, Any], raw: Dict[str, Any]) -> Dict[str, Any]:
    """[2026-09-13 P16] 칸 둘뿐이다 — 조건식과 문장. 문장은 **한 글자도 안 고친다**(형식과 같은 규율)."""
    return {
        "when": _s(_pick(sub, "when", "조건", "if"), RULE_MAX),
        "text": _s(_pick(sub, "text", "문장", "지시", "instruction"), RULE_MAX),
    }


def sorted_items(items: Any) -> List[Dict[str, Any]]:
    """value → narrative → format → transition. **파일 안의 순서는 그 안에서만 산다.**"""
    rows = [i for i in (items or []) if isinstance(i, dict)]
    return [r for _, r in sorted(
        enumerate(rows),
        key=lambda p: (KIND_ORDER.get(p[1].get("kind"), 99), p[0]))]


# =========================================================
# 3. 콜 — 등록 시점 1회성 heavy (턴 경로 0)
# =========================================================
def known_names(channel_id: str) -> Dict[str, List[str]]:
    """식이 참조할 수 있는 이름 전부. 라우터가 이걸 모르면 없는 이름으로 식을 쓴다."""
    out = {"values": [], "items": [], "sections": [], "transitions": [], "directives": [],
           "readonly": [], "system": []}
    try:
        import custom_vars as _cv
        out["system"] = list(getattr(_cv, "SYSTEM_PLACEHOLDERS", ()) or ())
    except Exception as e:
        logger.debug("[Router] 시스템 이름표 skip: %s", e)
    try:
        import custom_vars as cv
        out["values"] = sorted(cv.get_declarations(channel_id))
    except Exception as e:
        logger.debug("[Router] 변수 목록 skip: %s", e)
    try:
        import domain_manager as dm
        nb = dm.get_notebook_data(channel_id) or {}
        items = ((nb.get("sections") or {}).get("소지품") or {}).get("items") or {}
        out["items"] = sorted(items)
    except Exception as e:
        logger.debug("[Router] 소지품 목록 skip: %s", e)
    try:
        import status_panel as sp
        out["sections"] = sorted(sp.list_panel_sections(channel_id))
    except Exception as e:
        logger.debug("[Router] 섹션 목록 skip: %s", e)
    try:
        import expr_engine as ee
        out["transitions"] = sorted(ee.list_transitions(channel_id))
        out["directives"] = sorted(ee.list_directives(channel_id))
        out["readonly"] = sorted(getattr(ee, "CODE_READONLY", ()) or ())
    except Exception as e:
        logger.debug("[Router] 전이 목록 skip: %s", e)
    return out


def build_router_prompt(channel_id: str, chunk: str,
                        prior_names: Any = ()) -> Tuple[str, str]:
    """(sys, usr). 콜 입력에 **현재 선언 이름 목록**을 동봉한다 — 식이 쓸 이름의 사전이다."""
    kn = known_names(channel_id)
    lines = ["## DECLARED NAMES (expressions may use only these, plus names you create now)"]
    lines.append("values: " + (", ".join(kn["values"]) or "(none)"))
    lines.append("재고 items: " + (", ".join(kn["items"]) or "(none)"))
    lines.append("panel sections: " + (", ".join(kn["sections"]) or "(none)"))
    lines.append("transitions: " + (", ".join(kn["transitions"]) or "(none)"))
    lines.append("directives: " + (", ".join(kn.get("directives") or []) or "(none)"))
    lines.append("read-only code names: " + (", ".join(kn["readonly"]) or "(none)"))
    # [2026-09-07 P10] 시스템 자리표시자 — 이름표의 **단일 원천**은 custom_vars 의 상수다.
    lines.append("system placeholders (read-only, a format template may use these): "
                 + (", ".join(f"[{n}]" for n in kn["system"]) or "(none)"))
    prior = [str(n) for n in (prior_names or []) if str(n).strip()]
    if prior:
        lines.append("names you already created earlier in THIS SAME FILE "
                     "(do not create them again; you may reference them): "
                     + ", ".join(prior))
    # 이름 사전은 **유저 쪽**에 싣는다: 조각마다 달라지는 재료라 지시문(sys)에 두면
    # 조각 순서에 따라 지시문 자체가 흔들린다. 지시(sys)는 파일 전체에서 한 모양이다.
    sys_prompt = ROUTER_PROMPT
    usr = ("\n".join(lines)
           + "\n\n[준비물 텍스트 — 이 안의 내용만 분류하라]\n\n" + str(chunk or ""))
    return sys_prompt, usr


async def _call_router(client: Any, sys_prompt: str, usr_prompt: str,
                       op_name: str = "Output Router") -> Dict[str, Any]:
    """1회성 heavy 콜 1개. 스모크는 이 함수를 통째로 갈아 끼운다.

    ★`max_output_tokens` 를 **명시**한다. 로어 분석에서 겪은 병이 그대로 여기 있다 —
      heavy 라우트는 추론 토큰이 출력 예산을 먹어 content 가 비고 "candidates 없음"이 뜬다.
      `cognition._call_extract` 는 서사 권능 선언까지 붙이므로 이 콜에는 못 쓴다.
      (구 사유였던 "상한 미지정"은 2026-09-12에 해소 — 배치도 `BATCH_MAX_OUTPUT_TOKENS`
      8192 를 명시한다. 남은 사유는 권능 선언 하나뿐이다.) 잘림(MAX_TOKENS) 방어는 `api_call_with_retry`:
      `allow_truncated=False` 면 잘린 JSON 을 폐기하고 None 을 돌려준다 — 반쪽 JSON 을
      등록하느니 그 조각을 통째로 못 읽은 것으로 치는 쪽이 낫다.
    """
    from google.genai import types
    from memory_system import api_call_with_retry, safe_parse_json

    cfg = types.GenerateContentConfig(
        system_instruction=ROUTER_SYSTEM_INSTRUCTION,
        response_mime_type="application/json",
        temperature=config.ANALYSIS_TEMPERATURE,
        safety_settings=config.SAFETY_SETTINGS,
        max_output_tokens=getattr(config, "ROUTER_MAX_OUTPUT_TOKENS", 32768),  # [2026-09-13] 전용 상한(config 주석)
    )
    contents = [
        types.Content(role="user", parts=[types.Part(text=sys_prompt)]),
        types.Content(role="model", parts=[types.Part(
            text="확인. 준비물 텍스트를 닫힌 항목으로 나눠 JSON 하나만 출력합니다.")]),
        types.Content(role="user", parts=[types.Part(text=usr_prompt)]),
    ]
    with config.heavy_analysis():          # 1회성 → reasoning ON (턴 경로 미적용)
        res = await api_call_with_retry(client, config.role_model("heavy"),
                                        contents, cfg, operation_name=op_name)
    if not res:
        logger.warning("[Router] 빈 응답 — 그 조각은 항목 0개로 친다")
        return {}
    parsed = safe_parse_json(res)
    return parsed if isinstance(parsed, dict) else {"items": parsed}


class RouteResult:
    """콜 결과 한 봉지. `items` 는 정규화까지 끝난 닫힌 항목."""
    __slots__ = ("items", "calls", "error")

    def __init__(self, items: Optional[List[Dict[str, Any]]] = None,
                 calls: int = 0, error: str = ""):
        self.items = items or []
        self.calls = calls
        self.error = error


async def route_text(client: Any, channel_id: str, text: str,
                     source: str = "") -> RouteResult:
    """준비물 텍스트 → 항목 N개. 파일이 크면 문단 경계로 쪼개 **순차** 콜."""
    chunks = split_chunks(strip_cbs(text, channel_id))
    if not chunks:
        return RouteResult([], 0, "내용이 비어 있습니다.")
    items: List[Dict[str, Any]] = []
    seen: List[str] = []
    calls = 0
    for chunk in chunks:
        sys_p, usr_p = build_router_prompt(channel_id, chunk, seen)
        try:
            data = await _call_router(client, sys_p, usr_p)
        except Exception as e:
            logger.warning("[Router] 콜 실패(조각 %d/%d): %s", calls + 1, len(chunks), e)
            data = {}
        calls += 1
        for it in normalize_items(data, source)[:MAX_ITEMS_PER_CALL]:
            if it["name"] in seen:          # 같은 파일 안 중복은 뒤엣것을 버린다
                continue
            seen.append(it["name"])
            items.append(it)
        if len(items) >= MAX_TOTAL_ITEMS:
            break
    err = "" if items else "읽어낼 항목이 없었습니다."
    logger.info("[Router] %s — 조각 %d · 콜 %d · 항목 %d", source, len(chunks), calls, len(items))
    return RouteResult(items, calls, err)


# =========================================================
# 4. 관문 매핑 — 네 관문은 이미 있다. 여기서는 자리만 고른다.
# =========================================================
def _owner_source(channel_id: str, name: str) -> Optional[str]:
    """그 이름이 이미 어느 source 소유인가. 없으면 None, source 태그가 없으면 ""."""
    for _gate, rec in _find_all(channel_id, name):
        if isinstance(rec, dict):
            return _s(rec.get("source"))
    return None


def _find_all(channel_id: str, name: str) -> List[Tuple[str, Any]]:
    """이름 하나가 어느 관문에 사는지 전부. 삭제·충돌 검사가 같은 눈을 쓴다."""
    hits: List[Tuple[str, Any]] = []
    try:
        import custom_vars as cv
        rec = cv.get_declarations(channel_id).get(name)
        if isinstance(rec, dict) and not rec.get("system"):
            hits.append(("value", rec))
    except Exception as e:
        logger.debug("[Router] 변수 조회 skip: %s", e)
    try:
        import status_panel as sp
        rec = sp.list_panel_sections(channel_id).get(name)
        if rec:
            hits.append(("narrative", rec))
    except Exception as e:
        logger.debug("[Router] 섹션 조회 skip: %s", e)
    try:
        import domain_manager as dm
        rec = dm.get_output_rules(channel_id).get(name)
        if rec:
            hits.append(("format", rec if isinstance(rec, dict) else {"desc": rec}))
    except Exception as e:
        logger.debug("[Router] 출력룰 조회 skip: %s", e)
    try:
        import expr_engine as ee
        rec = ee.list_transitions(channel_id).get(name)
        if rec:
            hits.append(("transition", rec))
        rec = ee.list_directives(channel_id).get(name)
        if rec:
            hits.append(("directive", rec))
    except Exception as e:
        logger.debug("[Router] 전이 조회 skip: %s", e)
    return hits


def _compiles(src: Any, mode: str) -> bool:
    """식이 §3.4 문법 안인가. 밖이면 그 항목은 거부가 아니라 **강등**이다."""
    if src in (None, ""):
        return True
    try:
        import expr_engine as ee
        ee.compile_expr(src, mode)
        return True
    except Exception:
        return False


def _demote(channel_id: str, item: Dict[str, Any], source: str,
            why: str) -> Dict[str, Any]:
    """문법 밖 → 패널 섹션. 지켜짐은 잃고 출력은 산다(§0.5)."""
    rule = why or item.get("raw") or item["name"]
    narrative = dict(item)
    narrative["kind"] = "narrative"
    narrative["surface"] = "panel"
    narrative["narrative"] = {"panel_section": item["name"],
                              "continuity_rule": _s(rule, RULE_MAX), "lines": None}
    okk, msg = _register_narrative(channel_id, narrative, source)
    return {"item": narrative, "name": item["name"],
            "status": "demoted" if okk else "fail",
            "msg": (_line(narrative) + "  ↓서술로 받음") if okk else msg}


def _register_narrative(channel_id: str, item: Dict[str, Any],
                        source: str) -> Tuple[bool, str]:
    import status_panel as sp
    n = item.get("narrative") or {}
    cadence = item.get("cadence") if item.get("cadence") in ("turn", "day") else "turn"
    return sp.register_panel_section(
        channel_id, item["name"], n.get("continuity_rule") or item.get("raw") or item["name"],
        n.get("lines"), cadence, source=source,
        mode=n.get("mode"), keep=n.get("keep"))


def _register_value(channel_id: str, item: Dict[str, Any],
                    source: str) -> Tuple[bool, str]:
    import custom_vars as cv
    v = item.get("value") or {}
    spec: Dict[str, Any] = {
        "name": item["name"],
        "type": v.get("type") or "gauge",
        "scope": item.get("scope") or "global",
        "rule": v.get("rule") or item.get("raw") or item["name"],
        "source": source,
    }
    for key, val in (("range", v.get("range")),
                     # [P11] 레코드 목록은 `init` 이 **필드별 dict** 다(수 하나가 아니다).
                     ("init", v.get("init") if v.get("fields") else v.get("start")),
                     ("stages", v.get("stages")), ("item_mode", v.get("item_mode")),
                     ("max", v.get("max")),
                     ("fields", v.get("fields")), ("stage_of", v.get("stage_of")),
                     ("stage_at", v.get("stage_at")),
                     ("format", v.get("format")), ("feed", item.get("feed"))):
        if val not in (None, "", []):
            spec[key] = val
    clean, err = cv.validate_declaration(spec, cv.get_declarations(channel_id))
    if clean is None:
        return False, (err or "선언을 읽지 못했습니다.").splitlines()[0]
    # ★`validate_declaration` 은 **화이트리스트 반환**이라 source 가 거기서 증발한다.
    #   custom_vars 의 관문을 늘리는 대신 통과분에 한 칸 얹는다 — `register` 는 spec 을
    #   통째로 복사해 저장하므로(entry = dict(spec)) 이 한 줄이 태그의 전부다.
    if source:
        clean["source"] = str(source)
    okk, msg = cv.register(channel_id, clean)
    if not okk:
        return False, msg
    derive = v.get("derive")
    if derive:
        import expr_engine as ee
        d_ok, d_msg = ee.register_derive(channel_id, item["name"], derive)
        if not d_ok:
            return False, d_msg
    return True, ""


def _register_format(channel_id: str, item: Dict[str, Any], source: str,
                     batch_names: Any = (), batch_sections: Any = ()
                     ) -> Tuple[bool, str, List[str]]:
    """원문 그대로 `output_rules` → Slot 33. 라우터는 형식의 **뜻**을 이해하지 않는다.

    [2026-09-07 P10] 관문 하나가 붙었다 — 콜 없이, 코드만으로:
      `template` 의 자리표시자가 전부 (선언 변수 ∪ 같은 배치 신규 이름 ∪ 시스템 이름표 ∪
      `이름.max/.min` ∪ `섹션/필드`) 안이면 **템플릿 형식**(코드가 그린다).
      하나라도 밖이면 **문체 형식으로 강등**한다(template 을 떨어뜨린다).
    ★강등은 폐기가 아니다: `desc` 는 어느 쪽이든 원문 그대로 남아 Slot 33 으로 간다.
      코드가 못 그리면 산문이 그리면 된다 — 잃는 것은 "코드가 그림"뿐이다.
    """
    import domain_manager as dm
    fmt = item.get("format") or {}
    text = fmt.get("text") or item.get("raw") or ""
    if not text:
        return False, "형식 원문이 비었습니다.", []
    tpl = _s(fmt.get("template"), FORMAT_TEXT_MAX)
    bad: List[str] = []
    if tpl:
        try:
            import custom_vars as cv
            bad = cv.unresolved_placeholders(channel_id, tpl, batch_names, batch_sections)
        except Exception as e:
            logger.debug("[Router] 템플릿 검증 skip: %s", e)
            bad = []
        if bad:
            tpl = ""                       # ↓ 문체로. 원문(desc)은 그대로 산다.
    rules = dm.get_output_rules(channel_id)
    prev = rules.get(item["name"]) if isinstance(rules.get(item["name"]), dict) else {}
    rec: Dict[str, Any] = {"desc": text,
                           "created_at": prev.get("created_at") or time.strftime("%Y-%m-%d"),
                           "source": source,
                           "template": tpl or None,
                           "empty": fmt.get("empty") or {}}
    surface = _s(item.get("surface"))
    if surface:
        rec["surface"] = surface
    rules[item["name"]] = rec
    dm.set_output_rules(channel_id, rules)
    return True, "", bad


def _register_directive(channel_id: str, item: Dict[str, Any],
                        source: str) -> Tuple[bool, str]:
    import expr_engine as ee
    d = dict(item.get("directive") or {})
    d["name"] = item["name"]
    d["source"] = source
    if not _s(d.get("text")):
        d["text"] = item.get("raw") or ""
    return ee.register_directive(channel_id, d)


def _demote_directive(channel_id: str, item: Dict[str, Any], source: str,
                      why: str, batch_names: Any = (), batch_sections: Any = ()
                      ) -> Dict[str, Any]:
    """[2026-09-13 P16] 조건을 못 읽은 지시 → **출력룰 원문**(Slot 33). P10 강등과 같은 길이다.

    ★서술(패널 섹션)이 아니라 출력룰인 이유: 지시는 "화면에 늘 보이는 칸"이 아니라 **산문에게
      하는 말**이다. 패널로 내리면 유저 문장이 상태창 한 칸이 되어 GM 은 그 말을 영영 못 듣는다.
      조건만 잃고 문장은 상시 규칙으로 산다(지켜짐은 잃고 출력은 산다, §0.5).
    """
    d = item.get("directive") or {}
    text = _s(d.get("text")) or _s(item.get("raw")) or item["name"]
    fmt_item = dict(item)
    fmt_item["kind"] = "format"
    fmt_item["surface"] = "panel"
    fmt_item["format"] = {"text": text, "template": None, "empty": {}}
    okk, msg, _bad = _register_format(channel_id, fmt_item, source,
                                      batch_names, batch_sections)
    if not okk:
        return {"item": item, "name": item["name"], "status": "fail", "msg": msg}
    return {"item": fmt_item, "name": item["name"], "status": "demoted",
            "msg": f"{item['name']} · 지시 ↓ 상시 규칙으로(조건을 못 읽음: {_s(why, 80)}) · {text}"}


def _register_transition(channel_id: str, item: Dict[str, Any],
                         source: str) -> Tuple[bool, str]:
    import expr_engine as ee
    t = dict(item.get("transition") or {})
    t["name"] = item["name"]
    t["source"] = source
    # [2026-09-09 P11] `cadence` 는 **항목 칸**이다(전이 서브dict 가 아니라). 여기서 옮겨
    #   싣지 않으면 라우터가 "매일"이라 읽은 것이 매턴 전이로 등록된다.
    t["cadence"] = item.get("cadence") if item.get("cadence") in ("turn", "day") else "turn"
    return ee.register_transition(channel_id, t)


def _line(item: Dict[str, Any]) -> str:
    try:
        import expr_engine as ee
        return ee.format_registration(item)
    except Exception:
        return f"{item.get('name','')} · {_KIND_LABEL.get(item.get('kind'), '')}"


def apply_items(channel_id: str, items: Any, source: str = "") -> List[Dict[str, Any]]:
    """항목 → 관문. 반환은 되비침 재료 `[{item, name, status, msg}]`.

    status: ok(✓) / demoted(↓ 문법 밖 → 서술) / fail(✗ 이름 없음·이름 충돌·관문 거부).
    적용 순서는 여기서도 강제한다 — 호출자가 정렬을 잊어도 전이가 값보다 먼저 서지 않는다.
    """
    rows = sorted_items(items)
    # [2026-09-07 P10] 배치 전체를 먼저 훑는다 — 형식 template 의 자리표시자가 "이 응답에서
    #   막 만들어진 이름"을 가리켜도 통과해야 하기 때문(값이 먼저 서는 KIND_ORDER 만으로는
    #   같은 배치 뒤쪽 이름을 못 본다).
    batch_names = {it.get("name") for it in rows if it.get("name")}
    batch_sections = {it.get("name") for it in rows
                      if it.get("kind") == "narrative" and it.get("name")}
    variants: Dict[str, int] = {}
    for it in rows:
        nm0 = it.get("name") or ""
        if nm0:
            variants[nm0] = variants.get(nm0, 0) + 1
    seen_names: set = set()

    out: List[Dict[str, Any]] = []
    for item in rows:
        name = item.get("name") or ""
        kind = item.get("kind")
        # 같은 배치 안 같은 이름이 둘 이상이면 **첫 항목을 채택**한다. 옛 동작은 뒤엣것이
        #   앞엣것을 조용히 덮는 것이었다 — 조용한 덮어쓰기는 "왜 내 규칙이 사라졌나"가 된다.
        if name and name in seen_names:
            out.append({"item": item, "name": name, "status": "demoted",
                        "msg": f"{name} · ↓ 변형 {variants.get(name, 1)}개 중 1 채택"})
            continue
        if name:
            seen_names.add(name)
        owner = _owner_source(channel_id, name)
        if owner is not None and _s(owner) and _s(owner) != _s(source):
            out.append({"item": item, "name": name, "status": "fail",
                        "msg": f"{name} · 이름 충돌: {owner}"})
            continue
        try:
            if kind == "value":
                derive = (item.get("value") or {}).get("derive")
                if derive and not _compiles(derive, "eval"):
                    out.append(_demote(channel_id, item, source, str(derive)))
                    continue
                okk, msg = _register_value(channel_id, item, source)
            elif kind == "narrative":
                okk, msg = _register_narrative(channel_id, item, source)
            elif kind == "format":
                okk, msg, bad = _register_format(channel_id, item, source,
                                                 batch_names, batch_sections)
                if okk and bad:
                    out.append({"item": item, "name": name, "status": "demoted",
                                "msg": f"{_line(item)}  ↓문체로(문법 밖 이름: "
                                       + ", ".join(bad) + ")"})
                    continue
            elif kind == "directive":
                # 관문 하나로 묶는다 — 컴파일 실패도, 이름 없음도, 조건 부재도 **같은 강등**이다.
                #   (거부로 남기면 유저가 쓴 문장이 통째로 사라진다.)
                okk, msg = _register_directive(channel_id, item, source)
                if not okk:
                    out.append(_demote_directive(channel_id, item, source, msg,
                                                 batch_names, batch_sections))
                    continue
            elif kind == "transition":
                t = item.get("transition") or {}
                bad = [(t.get("when"), "eval"), (t.get("do"), "exec"),
                       (t.get("on_fail"), "exec")]
                broken = [src for src, mode in bad if not _compiles(src, mode)]
                if broken:
                    out.append(_demote(channel_id, item, source, "; ".join(broken)))
                    continue
                okk, msg = _register_transition(channel_id, item, source)
            else:
                continue
        except Exception as e:                       # 관문 하나가 죽어도 나머지는 등록된다
            logger.warning("[Router] 등록 실패 %s(%s): %s", name, kind, e)
            okk, msg = False, f"{name} · 등록 실패: {e}"
        # [2026-09-24 감사] 서술·전이는 turn/day 만 등록된다(week·month 등은 turn 으로 접힘). 되비침이 "주"로
        #   보이면 유저는 매주 정산으로 믿는데 실제는 매턴 — 등록된 값으로 보여 주고 접혔다고 적는다.
        _cad_note = ""
        if okk and kind in ("narrative", "transition") and item.get("cadence") not in (None, "turn", "day"):
            _cad_note = f"  ⚠ 때 '{item.get('cadence')}' 미지원 → 매턴"
            item = dict(item, cadence="turn")
        out.append({"item": item, "name": name,
                    "status": "ok" if okk else "fail",
                    "msg": (_line(item) + _cad_note) if okk else (msg or f"{name} · 등록 실패")})
    return out


# =========================================================
# 5. 재등록 diff · 되비침
# =========================================================
def names_by_source(channel_id: str) -> Dict[str, List[str]]:
    """source 태그별 이름 묶음. 태그 없는 옛 항목은 `""` 키로 모인다."""
    out: Dict[str, List[str]] = {}

    def _add(src: Any, name: str) -> None:
        out.setdefault(_s(src), []).append(name)

    try:
        import custom_vars as cv
        for nm, rec in (cv.get_declarations(channel_id) or {}).items():
            if isinstance(rec, dict) and not rec.get("system"):
                _add(rec.get("source"), nm)
    except Exception as e:
        logger.debug("[Router] 변수 묶음 skip: %s", e)
    try:
        import status_panel as sp
        for nm, rec in (sp.list_panel_sections(channel_id) or {}).items():
            _add((rec or {}).get("source"), nm)
    except Exception as e:
        logger.debug("[Router] 섹션 묶음 skip: %s", e)
    try:
        import domain_manager as dm
        for nm, rec in dm.get_output_rules(channel_id).items():
            _add(rec.get("source") if isinstance(rec, dict) else "", nm)
    except Exception as e:
        logger.debug("[Router] 출력룰 묶음 skip: %s", e)
    try:
        import expr_engine as ee
        for nm, rec in (ee.list_transitions(channel_id) or {}).items():
            _add((rec or {}).get("source"), nm)
        for nm, rec in (ee.list_directives(channel_id) or {}).items():
            _add((rec or {}).get("source"), nm)
    except Exception as e:
        logger.debug("[Router] 전이 묶음 skip: %s", e)
    return {k: sorted(set(v)) for k, v in out.items()}


def missing_names(channel_id: str, source: str, new_names: Any) -> List[str]:
    """같은 source 로 다시 왔을 때 **이번 파일에 없어진** 이름. 지우지 않는다 — 묻기만 한다."""
    have = set(names_by_source(channel_id).get(_s(source), []))
    return sorted(have - {_s(n) for n in (new_names or [])})


def format_reflection(source: str, results: Any, missing: Any = ()) -> str:
    """"📋 이렇게 읽었다" 블록. 항목당 한 줄, ✓/✗/↓ 접두(§3.4 되비침 형식)."""
    rows = [r for r in (results or []) if isinstance(r, dict)]
    head = f"📋 **이렇게 읽었다** ({source or '서술'}, {len(rows)}항목)"
    lines = [head]
    for r in rows:
        mark = STATUS_MARK.get(r.get("status"), "✗")
        body = " ".join(str(r.get("msg") or r.get("name") or "").split())
        lines.append(f"{mark} {body}")
    miss = [str(m) for m in (missing or []) if str(m).strip()]
    if miss:
        lines.append("삭제? " + " · ".join(miss)
                     + "  — 이번 파일에 없어진 이름입니다. 지우려면 `!출력룰 삭제 <이름>`")
    lines.append("잘못 읽은 항목은 (ooc: …)로 고쳐줘")
    return "\n".join(lines)


# =========================================================
# 6. 삭제 — 목적지 정정(동사 신설 0)
# =========================================================
def remove_name(channel_id: str, name: str) -> List[str]:
    """이름 하나를 **모든 관문**에서 찾아 지운다(값·서술·형식·전이·지시). 지운 관문 이름들을 돌려준다."""
    nm = _s(name)
    gone: List[str] = []
    if not nm:
        return gone
    try:
        import custom_vars as cv
        import expr_engine as ee
        if nm in (cv.get_declarations(channel_id) or {}):
            ee.unregister_derive(channel_id, nm)
            if cv.unregister(channel_id, nm):
                gone.append("값")
    except Exception as e:
        logger.debug("[Router] 변수 삭제 skip: %s", e)
    try:
        import status_panel as sp
        okk, _msg = sp.remove_panel_section(channel_id, nm)
        if okk:
            gone.append("서술")
    except Exception as e:
        logger.debug("[Router] 섹션 삭제 skip: %s", e)
    try:
        import domain_manager as dm
        rules = dm.get_output_rules(channel_id)
        if nm in rules:
            del rules[nm]
            dm.set_output_rules(channel_id, rules)
            gone.append("형식")
    except Exception as e:
        logger.debug("[Router] 출력룰 삭제 skip: %s", e)
    try:
        import expr_engine as ee2
        if ee2.unregister_transition(channel_id, nm):
            gone.append("전이")
        if ee2.unregister_directive(channel_id, nm):
            gone.append("지시")
    except Exception as e:
        logger.debug("[Router] 전이 삭제 skip: %s", e)
    return gone


def remove_source(channel_id: str, source: str) -> List[str]:
    """그 source 로 들어온 항목 전량. 파일 하나를 통째로 무르는 길이다."""
    names = names_by_source(channel_id).get(_s(source), [])
    gone = [nm for nm in names if remove_name(channel_id, nm)]
    return sorted(gone)


# =========================================================
# 7. 명령 판별 — 기존 동사의 목적지만 정한다
# =========================================================
def route_decision(channel_id: str, args: Any, raw_args: str = "",
                   has_attachment: bool = False) -> str:
    """`추가` 뒤가 어느 문으로 가나. "router" / "pipe" / "legacy".

    판별은 **모양**이지 낱말 목록이 아니다: 첨부가 있으면 라우터, 첫 낱말이 알려진 키거나
    이미 등록된 키면 옛 경로, `변수`거나 파이프가 있으면 파이프 경로, 그 밖이면 라우터.
    """
    argv = [str(a) for a in (args or [])]
    if has_attachment:
        return "router"
    rest = argv[1:] if len(argv) > 1 else []
    if not rest:
        return "legacy"                       # 텍스트가 없으면 옛 사용법 안내가 맞다
    key = rest[0].strip()
    low = key.lower()
    if low in ("변수", "var", "vars", "variable", "변수선언"):
        return "pipe"
    if "|" in str(raw_args or ""):
        return "pipe"
    try:
        import status_panel as sp
        if sp.is_panel_key(key) or sp.is_header_key(key):
            return "legacy"
    except Exception as e:
        logger.debug("[Router] 키 판별 skip: %s", e)
    try:
        import domain_manager as dm
        if key in dm.get_output_rules(channel_id):
            return "legacy"                   # 이미 있는 키의 수정은 옛 길 그대로
    except Exception as e:
        logger.debug("[Router] 기존 키 조회 skip: %s", e)
    return "router"
