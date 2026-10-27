"""모듈 틀 — 등록부·정거장 (2026-10-22 C58 M0).

스펙  파티쳇수정/output_artifact/C58_모듈틀_M0·M1_스펙_2026-10-22.md
지도  파티쳇수정/output_artifact/C56_모듈틀_지도_2026-10-22.md

모듈 하나 = 선언 한 장(Module). 틀이 쥐는 건 **순서와 격리**뿐이다 — 게이트·콜·적재·저장은 모듈 몫.
  - 정거장(station) = 턴 시간표의 한 자리. M1에서 실제로 부르는 건 BACKGROUND 하나
    (orchestration.execute 9.52 전이 알림 flush 뒤 · 9.9 임베드 재그림 앞).
  - run_station 은 order 순으로 돌고, 한 모듈이 예외를 내도 나머지는 돈다.

⚠ 새 배경 태스크는 orchestration 에 인라인으로 넣지 말고 builtin_modules 에 선언한다.
⚠ [C60 M2] 턴 꼬리 버튼·Activity 탭도 선언이다 — add_button / add_screen 한 줄(손으로 쓴 표 금지).
⚠ [C62 M3] 날 경계(날짜가 넘어간 첫 턴)에 할 일도 선언이다 — 모듈 stations 에 ON_DAY 한 줄(옛 boundary SUBSCRIBERS 폐지).
⚠ [C62 M3] 토글이 있는 모듈은 꺼진 채널에서 정거장이 안 돈다(run_station 이 본다). 이식 모듈은 몸에 토글 검사를 안 써도 된다.
⚠ [C63 M4] 배경 추출 결과는 구독 정거장 둘(AFTER_OUTPUTS · AFTER_BATCH)로 나간다 — 추출을 먹는 새 일은 거기 한 줄(shared_extract 참고).
⚠ [C64 M5] 입력 갈래(OOC 등)는 ON_INPUT(run_chain — 먹으면 멈춤), 렌더 앞 보조 콜은 PRE_RENDER_CALL(프롬프트에 끼우기),
  persistent view(버튼 판)는 add_view 한 줄 — main.on_ready 손글 목록 금지.
⚠ 장르 이론 '모듈'(theory_emphasis_engine.get_active_modules(active_genres))과는 다른 물건이다.
⚠ [C74 W4-2·3] BACKGROUND 함수는 큐에 바로 넣는 대신 BgJob(콜 · 적용)을 돌려줄 수 있다. 짝 표(pair_background)에 오른
  둘은 틀이 한 큐 태스크로 묶어 두 콜을 같이 띄우고(gather) 적용은 order 순서대로 한다. 짝이 없거나 빠지면 혼자 적재.
"""
import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple

logger = logging.getLogger("ModuleFrame")

# ── 정거장 (C56 §4.1) ─────────────────────────────────────────────
ON_INPUT = "on_input"                # 턴 입력 직후, Theoria 앞
PRE_RENDER = "pre_render"            # waterfall process · 경계 · expr
PRE_RENDER_CALL = "pre_render_call"  # 산문이 기다리는 보조 콜(도착물)
AFTER_PROSE = "after_prose"          # 산문 전송 직후, message id 확정 뒤(동기·짧게)
BACKGROUND = "background"            # 렌더 뒤 배경 큐 적재
ON_DAY = "on_day"                    # 날 경계
ON_CLICK = "on_click"                # 버튼 · Activity
ON_COMMAND = "on_command"            # !명령
ON_RESET = "on_reset"                # !클리어 · !리셋 · !다시
AFTER_OUTPUTS = "after_outputs"      # [C63 M4] 공용 출력물 추출(B-Outputs) 결과 구독 — 옛 _apply_outputs 6단
AFTER_BATCH = "after_batch"          # [C63 M4] 공용 세계 추출(B-Batch) 결과 구독 — 옛 _execute_background_extraction 블록
STATIONS = (ON_INPUT, PRE_RENDER, PRE_RENDER_CALL, AFTER_PROSE, BACKGROUND,
            ON_DAY, ON_CLICK, ON_COMMAND, ON_RESET, AFTER_OUTPUTS, AFTER_BATCH)

TIERS = ("engine", "core", "toggle", "declared", "risu")
SHELVES = ("core", "toggle", "config", "")   # !모듈 목록에서 서는 칸 ("" = 안 보임)


@dataclass(frozen=True)
class Call:
    """보조 콜 설명(목록·Activity 가 읽는다). 실행은 stations 함수가 한다."""
    label: str      # 로그 라벨 / 큐 이름
    role: str       # config.role_model 역할
    when: str       # 게이트 한 줄


@dataclass(frozen=True)
class Toggle:
    """채널별 켜고 끄기. 저장은 지금 자리 그대로(domain_manager)."""
    get: Callable[[str], bool]
    set: Optional[Callable[[str, bool], None]] = None


@dataclass(frozen=True)
class Setting:
    """[2026-10-23 C72 A5] 모듈 설정 칸 하나 — 켬/끔 말고 고르는 값. Activity 모듈 앱이 폼으로 그리고 `mod.set` 이 쓴다.

    저장은 그 모듈의 **지금 자리 그대로**(Toggle 과 같은 꼴 — get · set 함수만 낸다). 옮기는 데이터 0.
    kind: switch(참/거짓) | select(options 안 값만). 이식 모듈의 범용 저장은 그 모듈이 들어올 때 만든다.
    """
    key: str
    label: str
    kind: str                                   # "switch" | "select"
    get: Callable[[str], Any]                   # 채널 → 지금 값
    set: Callable[[str, Any], None]
    options: Tuple[Tuple[str, str], ...] = ()   # select: (값, 라벨)


@dataclass(frozen=True)
class Button:
    """[2026-10-23 C60 M2a] 턴 꼬리 버튼 한 개 — 선언 한 장이 옛 "다섯 자리 동시 수정"을 대신한다.

    custom_id 는 옛 값 그대로(이미 보낸 메시지의 버튼이 산다). 스타일은 늘 secondary(지금과 같다).
    붙이는 건 일 하는 모듈이다 — 응답 함수가 사는 자리(turn_mail)가 `add_button` 으로 붙인다.
    """
    custom_id: str
    emoji: str
    label: str
    order: int
    gate: Callable[[str, int, Tuple[str, ...]], bool]   # (채널, 메시지 id, 그 메시지 적립 kinds) → 붙일까
    respond: Callable[[Any], Awaitable[None]]           # interaction → 응답
    label_fn: Optional[Callable[[], str]] = None        # 지금 라벨(없으면 label) — 💠: 화면이 떠 있으면 "상태창"
    scope: str = "archive"   # archive = 옛 메시지에서도 유효(지금 넷 전부) · turn = (예약) 다음 턴에 옛 메시지에서 뗀다


@dataclass(frozen=True)
class Screen:
    """[2026-10-23 C60 M2b] Activity 탭 한 개. 화면은 /api/tabs 로 이 목록을 받아 탭 줄을 그린다.

    [2026-10-23 C67 A1] 탭 줄 → 홈 + 앱. 한 장 = 앱 하나. 칸 셋이 늘었다(기본값이 있어 옛 선언이 그대로 산다):
      drawer — 홈 서랍에 서나(False = 데이터 경로만, 예: 고치기)
      kind   — builtin | extension (서랍의 기본 칸 / 확장 칸, C66 §8)
      edit   — 편집 모드에서 보일 고치기 묶음 고르개(activity_edit.build_edit_view 의 group key,
               목록은 list:builtin / list:declared). 비면 그 앱은 편집 버튼이 비활성.
    """
    tab: str
    label: str
    icon: str        # Lucide 이름 — app.js ICONS 표에 있어야 한다(없으면 layers). 서버는 이름만 보낸다
    order: int
    view: Callable[[str, str], Dict[str, Any]]   # (채널, uid) → JSON
    renderer: str = "cards"   # status | archive | people | decl | edit | cards(범용 — 새 모듈의 기본)
    drawer: bool = True
    kind: str = "builtin"
    edit: Tuple[str, ...] = ()


@dataclass(frozen=True)
class View:
    """[2026-10-23 C64 M5] persistent view(버튼 판) 한 개 — main.on_ready 가 order 순으로 `client.add_view(factory())`.
    로그 줄은 옛 on_ready 글자 그대로(ok_log · fail_log 의 "{e}" 자리에 예외)."""
    name: str
    factory: Callable[[], Any]
    order: int
    ok_log: str
    fail_log: str


@dataclass
class Module:
    id: str
    name: str
    tier: str
    order: int = 100
    stations: Dict[str, Callable] = field(default_factory=dict)   # 정거장 → async fn(rt, turn)
    orders: Dict[str, int] = field(default_factory=dict)          # [C62 M3] 정거장별 순서(없으면 order) — 정거장마다 줄이 다를 때
    calls: Tuple[Call, ...] = ()
    toggle: Optional[Toggle] = None
    aliases: Tuple[str, ...] = ()
    shelf: str = ""
    status: Optional[Callable[[str], str]] = None   # shelf=config 의 한 줄 상태(채널 → 글)
    off_notice: str = ""                              # !모듈 <이름> off 에 덧붙는 안내
    buttons: Tuple["Button", ...] = ()               # [C60 M2a] 턴 꼬리 버튼(add_button 이 붙인다)
    screens: Tuple["Screen", ...] = ()               # [C60 M2b] Activity 탭(add_screen 이 붙인다)
    views: Tuple["View", ...] = ()                   # [C64 M5] persistent view(add_view 가 붙인다)
    settings: Tuple["Setting", ...] = ()             # [C72 A5] 설정 칸(Activity 모듈 앱 폼 · mod.set)
    bg_pair: str = ""                                # [C74 W4-2·3] BACKGROUND 짝 — order 로 바로 뒤 모듈 id(그 콜과 같이 띄운다)

    def __post_init__(self):
        if self.tier not in TIERS:
            raise ValueError(f"[Frame] {self.id}: 모르는 tier {self.tier!r}")
        if self.shelf not in SHELVES:
            raise ValueError(f"[Frame] {self.id}: 모르는 shelf {self.shelf!r}")
        bad = [s for s in self.stations if s not in STATIONS]
        if bad:
            raise ValueError(f"[Frame] {self.id}: 모르는 정거장 {bad}")


def channel_toggle(code: str) -> Toggle:
    """[2026-10-23 C62 M3] 채널별 켜고 끄기 — 저장은 domain_manager 두 축(active_modules / disabled_modules) 그대로.
    아무 이름이나 받는다. `DEFAULT_ON_MODULES`(게시판·속마음)에 없는 이름은 **꺼진 채로** 시작한다 → 이식 모듈은 켜야 돈다.
    선언 = `Module(id=code, …, shelf="toggle", toggle=channel_toggle(code))` — 이것만으로 `!모듈 <code> on/off`·목록 줄이 생긴다."""
    def _get(ch: str) -> bool:
        import domain_manager
        return code in (domain_manager.get_active_modules(ch) or [])

    def _set(ch: str, on: bool) -> None:
        import domain_manager
        domain_manager.toggle_module(ch, code, on)

    return Toggle(_get, _set)


@dataclass
class BgJob:
    """[2026-10-24 C74 W4-2·3] BACKGROUND 정거장 함수가 돌려주는 배경 일감 — 큐 적재는 틀이 한다.
    call  = 입력 읽기 + 콜(쓰기 없음) → 결과. 실패 정책은 옛 태스크 몸 그대로 안에 둔다(옛 몸이 삼키던 예외는 여기서도 삼킨다).
    apply = 결과 적용(정거장 · 저장). call 결과를 받는다.
    짝으로 묶이면 두 call 이 같이 돌고, apply 는 앞 모듈 → 뒤 모듈 순서로 차례로 돈다(옛 큐 순서 그대로)."""
    name: str
    call: Callable[[], Awaitable[Any]]
    apply: Callable[[Any], Awaitable[None]]
    priority: Any = None


@dataclass
class AfterProse:
    """BACKGROUND 정거장에 넘기는 묶음 — 옛 9.55~9.8 블록이 읽던 지역 변수 넷 + [C63 M4] 입력 메시지(배경 추출이 채널에 알린다)."""
    channel_id: str
    ctx: Any
    response: str
    sent_msgs: list
    message: Any = None
    panel_before: Any = None   # [2026-10-24 C74 W4-1] 재그림(panel_refresh 모듈)의 비교 기준 = 보낼 때 그린 임베드 데이터


@dataclass
class OutputsTurn:
    """[2026-10-23 C63 M4] AFTER_OUTPUTS 정거장 묶음 — 공용 출력물 추출 콜의 산출 하나."""
    channel_id: str
    ctx: Any
    outputs: Dict[str, Any]
    message: Any = None


@dataclass
class BatchTurn:
    """[2026-10-23 C63 M4] AFTER_BATCH 정거장 묶음 — 공용 세계 추출 콜의 산출(updates) + 옛 블록들이 같이 읽던 값.
    turn_now = 이 추출이 속한 턴(렌더 시점 ctx.dai 기준, 옛 `_bg_turn_now`) · session_memory = 콜 앞에서 한 번 읽은 세션 메모리.
    notes = [C74 판2] 앞 구독자가 뒤 구독자에게 넘기는 쪽지(goal_receipts: world 50 → goal 55 알림)."""
    channel_id: str
    ctx: Any
    response: str
    message: Any
    updates: Dict[str, Any]
    turn_now: Callable[..., int]
    session_memory: Dict[str, Any]
    notes: Dict[str, Any] = field(default_factory=dict)


@dataclass
class DayTick:
    """[2026-10-23 C62 M3] ON_DAY 정거장에 넘기는 묶음 — boundary_engine.on_turn 이 날짜 경계 턴에만 만든다.
    info = {prev_day, now_day, days_jumped}(check_boundary 그대로)."""
    channel_id: str
    info: Dict[str, Any]


@dataclass
class InputTurn:
    """[2026-10-23 C64 M5] ON_INPUT 묶음 — main._process_message 의 입구(세션·참가자 검사 뒤 · 대기/자동 앞).
    stop = 입력을 먹었다(뒤 모듈 · 평턴 경로 안 돎). turn_override = stop 과 같이: 이 글로 턴을 돈다(None = 턴 없음)."""
    channel_id: str
    message: Any
    parsed: Optional[Dict[str, Any]]
    stop: bool = False
    turn_override: Optional[str] = None
    record_user_history: bool = True


@dataclass
class PreRenderCall:
    """[2026-10-23 C64 M5] PRE_RENDER_CALL 묶음 — orchestration 5.5(조립 뒤 · 렌더 앞). 산문이 기다리는 자리.
    prompt = 이번 턴 문서(정거장 함수가 끼워 고쳐 쓴다) · message_ids = `!다시` 삭제 목록에 더할 메시지 id."""
    channel_id: str
    ctx: Any
    message: Any
    prompt: str
    message_ids: list


REGISTRY: Dict[str, Module] = {}


def register(m: Module) -> Module:
    """같은 id 두 번 = 예외(조용한 덮어쓰기 금지)."""
    if m.id in REGISTRY:
        raise ValueError(f"[Frame] 이미 등록된 모듈: {m.id}")
    REGISTRY[m.id] = m
    if m.bg_pair:                                    # [C74 W4-2·3] 짝도 선언 한 장 안에서
        pair_background(m.id, m.bg_pair)
    return m


def get(module_id: str) -> Optional[Module]:
    return REGISTRY.get(module_id)


def all_modules() -> List[Module]:
    """등록 순서 그대로."""
    return list(REGISTRY.values())


def on_shelf(shelf: str) -> List[Module]:
    return [m for m in REGISTRY.values() if m.shelf == shelf]


def by_alias(word: str) -> Optional[Module]:
    """id 또는 별칭(대소문자 무시)으로 찾는다."""
    w = (word or "").strip().lower()
    if not w:
        return None
    for m in REGISTRY.values():
        if w == m.id.lower() or w in (a.lower() for a in m.aliases):
            return m
    return None


def resolve_module(channel_id: str, word: Any) -> Tuple[str, Any, List[str]]:
    """[2026-10-23 C72] `!모듈 <말>` · Activity `mod.toggle` 의 고르기.
    순서: ① 코드 모듈 id · 별칭 정확히 ② `2` · `#2` = 올린 것 번호(선언 모듈 · [C73] 코드 앱 — 번호 줄 하나)
          ③ 선언 모듈 이름표 · 파일 이름 · [C73] 코드 앱 이름 · id 정확히
          ④ 앞글자 — 토글 코드 모듈(id · 이름 · 별칭) + 선언 모듈 이름표 + 코드 앱 이름 전체에서 **하나로만** 맞을 때.
    → ("code", Module, []) | ("bundle", source, []) | ("app", app_id, []) | ("", None, 후보 라벨들)."""
    w = str(word or "").strip()
    if not w:
        return "", None, []
    m = by_alias(w)
    if m is not None:
        return "code", m, []
    try:
        import output_router as _or
        bl = _or.bundles(channel_id)
    except Exception as e:
        logger.debug(f"[Frame] bundle list skipped: {e}")
        bl = []
    try:
        import app_host as _ah
        al = _ah.list_apps(channel_id)
    except Exception as e:
        logger.debug(f"[Frame] app list skipped: {e}")
        al = []
    num = w[1:] if w.startswith("#") else w
    if num.isdigit():
        hit = [b for b in bl if b.get("no") == int(num)]
        if hit:
            return "bundle", hit[0]["source"], []
        ahit = [a for a in al if a.get("no") == int(num)]
        return ("app", ahit[0]["id"], []) if ahit else ("", None, [])
    exact = [b for b in bl if w in (b.get("label"), b.get("source"))]
    aexact = [a for a in al if w in (a.get("name"), a.get("id"))]
    if len(exact) + len(aexact) == 1:
        return ("bundle", exact[0]["source"], []) if exact else ("app", aexact[0]["id"], [])
    lw = w.lower()
    code_pre = [x for x in REGISTRY.values() if x.shelf == "toggle" and x.toggle is not None
                and any(str(a).lower().startswith(lw) for a in (x.id, x.name, *x.aliases))]
    bun_pre = [b for b in bl if str(b.get("label") or "").lower().startswith(lw)]
    app_pre = [a for a in al if str(a.get("name") or "").lower().startswith(lw)]
    if len(code_pre) + len(bun_pre) + len(app_pre) == 1:
        if code_pre:
            return "code", code_pre[0], []
        return ("bundle", bun_pre[0]["source"], []) if bun_pre else ("app", app_pre[0]["id"], [])
    return "", None, ([x.name for x in code_pre] + [f"#{b['no']} {b['label']}" for b in bun_pre]
                      + [f"#{a['no']} {a['name']}" for a in app_pre])


def set_module_on(channel_id: str, kind: str, target: Any, on: bool) -> Tuple[bool, str]:
    """[2026-10-23 C72] 켜고 끄기 한 곳 — `!모듈` 명령과 Activity `mod.toggle` 이 같은 함수(입구 둘).
    코드 모듈은 토글 칸(`Toggle.set`), 선언 모듈은 치워 두기 / 되돌리기(output_router)."""
    if kind == "code":
        m = target
        if not isinstance(m, Module) or m.shelf != "toggle" or m.toggle is None or m.toggle.set is None:
            return False, "끄고 켤 수 없는 모듈이에요"
        m.toggle.set(channel_id, bool(on))
        return True, f"{m.name} {'켬' if on else '끔'}"
    if kind == "bundle":
        import output_router as _or
        return _or.set_bundle(channel_id, target, bool(on))
    if kind == "app":   # [2026-10-23 C73] 코드 앱 — 앱 표 켬끔(워터마크 밖이라 `!다시`에 산다)
        import app_host as _ah
        return _ah.set_on(channel_id, str(target), bool(on))
    return False, "모르는 모듈이에요"


def setting_of(m: Module, key: str) -> Optional[Setting]:
    for st in (m.settings or ()):
        if st.key == key:
            return st
    return None


def station_order(m: Module, station: str) -> int:
    """[C62 M3] 그 정거장에서의 순서 — orders 에 적혀 있으면 그것, 없으면 order."""
    return int(m.orders.get(station, m.order))


def modules_at(station: str) -> List[Module]:
    """그 정거장에 선 모듈 — 정거장별 순서(station_order), 같으면 등록 순."""
    seq = list(REGISTRY.values())
    return sorted((m for m in seq if station in m.stations),
                  key=lambda m: (station_order(m, station), seq.index(m)))


def is_on(m: Module, channel_id: str) -> bool:
    """[C62 M3] 이 채널에서 이 모듈이 켜졌나. 토글 없음 = 늘 켬. 토글을 못 읽으면(예외) 켬 —
    설정을 못 읽는 건 끄라는 뜻이 아니다(turn_mail.mind_enabled 와 같은 규율)."""
    if m.toggle is None or not channel_id:
        return True
    try:
        return bool(m.toggle.get(channel_id))
    except Exception as e:
        logger.debug(f"[Frame] {m.id} toggle read skipped: {e}")
        return True


# ── 표면 (C60 M2) ─────────────────────────────────────────────────
def add_button(module_id: str, b: Button) -> None:
    """버튼을 모듈에 붙인다. 같은 custom_id 가 어디든 이미 있으면 **교체**(모듈 재import 에도 중복 0)."""
    m = REGISTRY.get(module_id)
    if m is None:
        raise ValueError(f"[Frame] 없는 모듈에 버튼: {module_id}")
    for other in REGISTRY.values():
        if any(x.custom_id == b.custom_id for x in other.buttons):
            other.buttons = tuple(x for x in other.buttons if x.custom_id != b.custom_id)
    m.buttons = tuple(m.buttons) + (b,)


def buttons() -> List[Button]:
    """턴 꼬리 버튼 전부 — order 순(같으면 붙인 순)."""
    seq = [b for m in REGISTRY.values() for b in m.buttons]
    return sorted(seq, key=lambda b: (b.order, seq.index(b)))


def add_screen(module_id: str, s: Screen) -> None:
    """탭을 모듈에 붙인다. 같은 tab 이 어디든 이미 있으면 **교체**."""
    m = REGISTRY.get(module_id)
    if m is None:
        raise ValueError(f"[Frame] 없는 모듈에 탭: {module_id}")
    for other in REGISTRY.values():
        if any(x.tab == s.tab for x in other.screens):
            other.screens = tuple(x for x in other.screens if x.tab != s.tab)
    m.screens = tuple(m.screens) + (s,)


def screens() -> List[Screen]:
    """Activity 탭 전부 — order 순(같으면 붙인 순)."""
    seq = [s for m in REGISTRY.values() for s in m.screens]
    return sorted(seq, key=lambda s: (s.order, seq.index(s)))


def add_view(module_id: str, v: View) -> None:
    """[C64 M5] view 를 모듈에 붙인다. 같은 name 이 어디든 이미 있으면 **교체**(재import 에도 중복 0)."""
    m = REGISTRY.get(module_id)
    if m is None:
        raise ValueError(f"[Frame] 없는 모듈에 view: {module_id}")
    for other in REGISTRY.values():
        if any(x.name == v.name for x in other.views):
            other.views = tuple(x for x in other.views if x.name != v.name)
    m.views = tuple(m.views) + (v,)


def views() -> List[View]:
    """persistent view 전부 — order 순(같으면 붙인 순)."""
    seq = [v for m in REGISTRY.values() for v in m.views]
    return sorted(seq, key=lambda v: (v.order, seq.index(v)))


def screen(tab: str) -> Optional[Screen]:
    for s in screens():
        if s.tab == tab:
            return s
    return None


# ── 배경 짝 (C74 W4-2·3) ──────────────────────────────────────────
#   앞 모듈 id → 뒤 모듈 id. 둘 다 BgJob 을 내면 한 큐 태스크(이름 "앞∥뒤")로 묶는다.
#   조건(짝을 올리는 쪽이 확인할 것): 뒤 콜이 앞 적용 결과를 안 읽고, 앞 콜도 뒤 적용 결과를 안 읽는다 · 둘이 order 로 붙어 있다.
_BG_PAIRS: Dict[str, str] = {}


def pair_background(first_id: str, second_id: str) -> None:
    _BG_PAIRS[str(first_id)] = str(second_id)


def bg_partner(module_id: str) -> Optional[str]:
    return _BG_PAIRS.get(str(module_id))


async def _run_solo(job: BgJob) -> None:
    await job.apply(await job.call())


async def _run_pair(channel_id: str, a: BgJob, b: BgJob) -> None:
    """두 콜을 같이 띄우고 적용은 a → b 차례. 한쪽이 예외를 내면 다른 쪽은 그대로 서고, 낸 쪽만 혼자 다시 적재한다
    (옛 큐 재시도 = 꼬리에 다시 섬과 같은 결 — 시도 수도 옛과 같게 재시도 한 번을 쓴 채로 선다).
    [판2 검토 #1] a 적용은 a 콜이 끝나는 대로 — b 콜을 안 기다린다(출력물 값 · 타격칸 저장이 옛 시각 그대로).
      두 콜을 **둘 다 태스크로 먼저 띄운 뒤** 기다린다: b 의 첫 걸음(입력 읽기 — await 앞)이 a 적용보다 먼저 돈다
      (a 콜이 한 번도 안 쉬고 끝나도 — 준비 줄이 FIFO 라 b 첫 걸음이 a 완료 알림보다 앞에 서 있다)."""
    async def _timed(j: BgJob):
        t = time.perf_counter()
        try:
            return await j.call(), time.perf_counter() - t, None
        except Exception as e:
            return None, time.perf_counter() - t, e

    fa = asyncio.ensure_future(_timed(a))
    fb = asyncio.ensure_future(_timed(b))
    spent = 0.0
    dts: List[float] = []
    again: List[BgJob] = []
    for j, fut in ((a, fa), (b, fb)):
        r, dt, e = await fut
        dts.append(dt)
        if e is not None:
            logger.warning(f"[Pair] {j.name} 콜 실패 — 혼자 다시 적재: {e}")
            again.append(j)
            _member_fail(channel_id, j, dt)
            continue
        t1 = time.perf_counter()
        try:
            await j.apply(r)
        except Exception as e2:
            logger.warning(f"[Pair] {j.name} 적용 실패 — 혼자 다시 적재: {e2}")
            again.append(j)
            _member_fail(channel_id, j, dt)
        spent += time.perf_counter() - t1
    logger.info(f"[Pair] ch={channel_id} {a.name} {dts[0]:.1f}s ∥ {b.name} {dts[1]:.1f}s · apply {spent:.1f}s")
    for j in again:
        await enqueue_jobs(channel_id, [j], retry_count=1)


def _member_fail(channel_id: str, j: BgJob, dt: float) -> None:
    """[판2 검토 #2] 짝 안에서 죽은 쪽도 계측에 실패로 선다(짝 태스크 자체는 ok 로 끝나므로)."""
    try:
        import turn_timing
        turn_timing.bg_done(channel_id, j.name, 0.0, dt, ok=False)
    except Exception:
        pass


async def enqueue_jobs(channel_id: str, jobs: List[BgJob], retry_count: int = 0) -> None:
    """BgJob 하나 = 그 이름 그대로 혼자 적재(옛 태스크와 같은 이름 · 우선순위). 둘 = 짝 태스크 하나("앞∥뒤").
    셋 이상이면 앞 둘을 짝으로, 나머지는 혼자 차례로(지금 부르는 곳은 둘까지). retry_count = 이미 쓴 재시도(짝에서 죽은 쪽)."""
    import background_task_queue as _btq
    jobs = [j for j in jobs if isinstance(j, BgJob)]
    if not jobs:
        return
    if len(jobs) == 1:
        j = jobs[0]
        _kw = {"retry_count": retry_count} if retry_count else {}   # 0 이면 안 넘긴다(옛 시그니처 스텁 호환)
        await _btq.enqueue_background_task(channel_id, j.name, lambda: _run_solo(j),
                                           priority=j.priority or _btq.TaskPriority.NORMAL, **_kw)
        return
    a, b = jobs[0], jobs[1]
    await _btq.enqueue_background_task(channel_id, f"{a.name}∥{b.name}", lambda: _run_pair(channel_id, a, b),
                                       priority=a.priority or _btq.TaskPriority.NORMAL)
    for j in jobs[2:]:
        await enqueue_jobs(channel_id, [j])


def check_pairs(station: str = BACKGROUND) -> List[str]:
    """[판2 검토 #4] 짝은 order 로 바로 이웃이어야 묶인다 — 사이에 다른 모듈이 끼면 조용히 혼자 둘이 된다.
    어긋난 짝 이름 목록(빈 목록 = 다 맞음). builtin_modules 등록 끝에서 한 번 부르고 어긋나면 WARNING."""
    ids = [m.id for m in modules_at(station)]
    bad = []
    for first, second in _BG_PAIRS.items():
        if first in ids and (ids.index(first) + 1 >= len(ids) or ids[ids.index(first) + 1] != second):
            bad.append(f"{first}∥{second}")
    return bad


async def run_station(station: str, rt: Any, turn: Any) -> None:
    """정거장 하나를 돈다. 모듈 함수가 예외를 내도 다음 모듈은 돈다.
    [C62 M3] 토글이 꺼진 채널이면 그 모듈은 건너뛴다(채널 = 묶음의 channel_id — AfterProse·DayTick 둘 다 있다).
    [C74 W4-2·3] 함수가 BgJob 을 돌려주면 틀이 적재한다. 짝 앞 모듈의 일감은 바로 다음(짝 뒤) 모듈까지 들고 있다가
      둘이면 짝 태스크로, 뒤가 꺼졌거나 · 일감이 없거나 · 죽었으면 혼자 적재한다 — 큐에 서는 자리는 옛 순서 그대로."""
    channel_id = str(getattr(turn, "channel_id", "") or "")
    held: Optional[Tuple[str, BgJob]] = None

    async def _flush() -> None:
        nonlocal held
        if held is not None:
            j = held[1]
            held = None
            try:
                await enqueue_jobs(channel_id, [j])
            except Exception as e:
                logger.warning(f"[Frame] {j.name} 적재 실패: {e}")

    for m in modules_at(station):
        if held is not None and m.id != bg_partner(held[0]):
            await _flush()
        if not is_on(m, channel_id):
            continue
        _t0 = time.perf_counter()
        r = None
        try:
            r = await m.stations[station](rt, turn)
        except Exception as e:  # 바깥 그물 — 모듈 안 try/except 가 1차
            # [2026-10-24 C74 W0-5] DEBUG → WARNING. 소리 없이 죽는 모듈이 서버 로그에 보이게(동작은 그대로 — 다음 모듈로).
            logger.warning(f"[Frame] {m.id}@{station} skip: {e}")
        if isinstance(r, BgJob):
            try:
                if held is not None:                  # m = 짝 뒤 → 둘을 한 태스크로
                    pair = [held[1], r]
                    held = None
                    await enqueue_jobs(channel_id, pair)
                elif bg_partner(m.id):                # m = 짝 앞 → 뒤 모듈까지 들고 간다
                    held = (m.id, r)
                else:
                    await enqueue_jobs(channel_id, [r])
            except Exception as e:
                logger.warning(f"[Frame] {m.id}@{station} 적재 실패: {e}")
        elif held is not None:                        # 짝 뒤가 일감을 안 냄 → 앞만 혼자
            await _flush()
        # [2026-10-24 C74 W0-5] 모듈 하나가 200ms 를 넘으면 INFO(넘지 않으면 안 찍는다 — 배경 정거장은 적재만이라 보통 ms).
        _dt = time.perf_counter() - _t0
        if _dt >= 0.2:
            logger.info(f"[Frame] {m.id}@{station} {_dt * 1000:.0f}ms")
    await _flush()


_RAISE_TIERS = ("engine", "core", "toggle")   # [C64 M5] run_chain 에서 예외를 위로 올리는 tier(그 밖 = 이식 모듈)


async def run_chain(station: str, rt: Any, turn: Any) -> None:
    """[2026-10-23 C64 M5] 줄 세우기 정거장(ON_INPUT) — 순서대로 돌다가 `turn.stop` 이 서면 멈춘다. 토글 검사는 run_station 과 같다.
    예외: 기본 모듈(engine · core · toggle)은 위로 올린다 — 옛 main 바깥 catch(`⚠️ Error` + 턴 없음) 그대로.
          이식 모듈(risu · declared)의 예외는 틀이 삼킨다 — 그 모듈만 빠지고 입력은 다음으로 흐른다(C64 §6-1)."""
    channel_id = str(getattr(turn, "channel_id", "") or "")
    for m in modules_at(station):
        if getattr(turn, "stop", False):
            break
        if not is_on(m, channel_id):
            continue
        if m.tier in _RAISE_TIERS:
            await m.stations[station](rt, turn)
            continue
        try:
            await m.stations[station](rt, turn)
        except Exception as e:
            logger.warning(f"[Frame] {m.id}@{station} 실패 — 건너뜀: {e}")


# 기본 모듈 등록 — 파일 끝에서 한 번(builtin_modules 가 위 정의를 쓰므로 순환 import 안전).
import builtin_modules  # noqa: E402,F401
