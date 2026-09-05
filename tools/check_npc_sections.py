"""
NPC 프로필 섹션 분할 미리보기 도구.

사용법:
    python check_npc_sections.py <NPC프로필.txt>
    python check_npc_sections.py <파일.txt> --preview 80
    python check_npc_sections.py <파일.txt> --no-content

[2026-09-02] **복제본 폐기 → 실물 호출.**
구 버전은 "봇 코드 의존 없음"을 위해 npc_manager의 섹션 로직을 복제했는데, 실측 결과
Sprint L(2026-04-29) 상태로 **넉 달간 드리프트**해 있었다 — h4 깊이 판정(07-28)도,
Aside 인식(08-10)도, 강등 프레임도 없었다. 즉 도구가 **봇이 하지 않는 일을 보고**하고
있었다. 의존을 피하려다 얻은 게 "틀린 미리보기"라면 그 거래는 손해다.
→ 실물을 import하고, 무거운 외부 패키지만 스텁으로 흡수한다(스모크와 같은 골격).
"""

import sys
import argparse
import types as _t
from pathlib import Path
from typing import Dict

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

for _n in ("google", "google.genai", "google.api_core", "openai", "discord",
           "aiohttp", "voyageai"):
    if _n in sys.modules:
        continue
    try:
        __import__(_n)
    except ModuleNotFoundError:
        class _Any:
            def __init__(self, *a, **k): pass
            def __call__(self, *a, **k): return self
            def __getattr__(self, k): return self
        _m = _t.ModuleType(_n)
        _m.__getattr__ = lambda n, _A=_Any: _A()
        sys.modules[_n] = _m
        if "." in _n:
            _parent, _child = _n.rsplit(".", 1)
            setattr(sys.modules[_parent], _child, _m)

import npc_manager as _nm   # noqa: E402

_CORE_SECTIONS = _nm._CORE_SECTIONS          # 구 표기(호환). 실제 판정은 아래 둘을 쓴다.
_CORE_FAMILIES = _nm._CORE_FAMILIES
_section_family = _nm._section_family
_MAX_TOTAL_PER_NPC = _nm._MAX_TOTAL_PER_NPC
_parse_sections = _nm._parse_sections
_is_hybrid_profile = _nm._is_hybrid_profile


def _select_profile_sections(desc: str) -> str:
    """렌더러가 실제로 받는 형태 — 강등·은닉 태그 포함(demote_background=True)."""
    return _nm._select_profile_sections(desc, demote_background=True)


# =========================================================
# 분석 / 출력
# =========================================================

def analyze(desc: str) -> dict:
    """프로필 분석 정보 dict."""
    parsed = _parse_sections(desc)
    is_hybrid = _is_hybrid_profile(desc)

    core_found = []
    core_missing = []
    for core in _nm._CORE_FAMILIES:      # [09-02] 정확일치 리스트 → 가족 판정(실물과 동일)
        found = False
        for sec_name in parsed:
            if sec_name == "_preamble":
                continue
            if _nm._section_family(sec_name) == core:
                core_found.append((core, sec_name))
                found = True
                break
        if not found:
            core_missing.append(core)

    return {
        "total_size": len(desc),
        "parsed": parsed,
        "section_count": sum(1 for s in parsed if s != "_preamble"),
        "preamble_size": len(parsed.get("_preamble", "")),
        "is_hybrid": is_hybrid,
        "core_found": core_found,
        "core_missing": core_missing,
    }


def format_report(desc: str, preview_chars: int = 0) -> str:
    if not desc.strip():
        return "입력 없음"

    info = analyze(desc)
    parsed = info["parsed"]
    lines = []

    # 헤더 통계
    _hh = "#" * _nm._section_header_depth(desc)
    fmt = "Hybrid (Voice/Aside 블록 보유)" if info["is_hybrid"] else ("Legacy (다중 %s 섹션)" % _hh)
    lines.append(f"포맷:      {fmt}")
    lines.append(f"총 길이:   {info['total_size']:,}자")
    lines.append(f"섹션 수:   {info['section_count']}개")
    if info["preamble_size"]:
        lines.append(f"머리말:    있음 ({info['preamble_size']:,}자)")

    if info["core_missing"]:
        lines.append(f"⚠ CORE 누락: {', '.join(info['core_missing'])}")
    else:
        lines.append(f"CORE 확인: {', '.join(c[0] for c in info['core_found'])} 모두 존재")

    if info["section_count"] == 0 and not info["preamble_size"]:
        lines.append("")
        lines.append("⚠ 섹션 구분자 없음 — 봇은 _MAX_TOTAL_PER_NPC 자르기만 적용")
        return "\n".join(lines)

    lines.append("")
    lines.append("─" * 70)
    lines.append("최종 노출 순서 (Identity/Hard Rules 우선, 나머지 작성 순서)")
    lines.append("─" * 70)

    included = set()
    idx = 0

    # 머리말
    if info["preamble_size"]:
        size = info["preamble_size"]
        bar = "█" * min(40, size // 100)
        lines.append(f"[머리말]      ({size:>5,}자) {bar}")
        if preview_chars > 0:
            preview = parsed["_preamble"][:preview_chars].replace("\n", " ⏎ ")
            if len(parsed["_preamble"]) > preview_chars:
                preview += "..."
            lines.append(f"              ┊ {preview}")
        lines.append("")
        idx += 1

    # CORE 섹션 (★)
    for core_name in _nm._CORE_FAMILIES:
        for sec_name, sec_text in parsed.items():
            if sec_name == "_preamble" or sec_name in included:
                continue
            if core_name.lower() in sec_name.lower():
                size = len(sec_text)
                bar = "█" * min(40, size // 100)
                lines.append(f"[{idx:>2}] ★CORE   ({size:>5,}자) {bar}")
                lines.append(f"             {_hh} {sec_name}")
                if preview_chars > 0:
                    preview = sec_text[:preview_chars].replace("\n", " ⏎ ")
                    if len(sec_text) > preview_chars:
                        preview += "..."
                    lines.append(f"             ┊ {preview}")
                lines.append("")
                included.add(sec_name)
                idx += 1
                break

    # 나머지
    for sec_name, sec_text in parsed.items():
        if sec_name == "_preamble" or sec_name in included:
            continue
        size = len(sec_text)
        bar = "█" * min(40, size // 100)
        lines.append(f"[{idx:>2}]         ({size:>5,}자) {bar}")
        lines.append(f"             {_hh} {sec_name}")
        if preview_chars > 0:
            preview = sec_text[:preview_chars].replace("\n", " ⏎ ")
            if len(sec_text) > preview_chars:
                preview += "..."
            lines.append(f"             ┊ {preview}")
        lines.append("")
        included.add(sec_name)
        idx += 1

    # 길이 경고
    final = _select_profile_sections(desc)
    if len(final) >= _MAX_TOTAL_PER_NPC:
        lines.append("─" * 70)
        lines.append(f"⚠ 최종 출력 ≥ {_MAX_TOTAL_PER_NPC:,}자 — 안전망에서 잘림")
    elif info["total_size"] > 30000:
        lines.append("─" * 70)
        lines.append(f"※ 총 길이 {info['total_size']:,}자 — 큰 프로필 (Slot 7 부담 ↑)")

    return "\n".join(lines)


# =========================================================
# CLI
# =========================================================

def main():
    ap = argparse.ArgumentParser(description="NPC 프로필 섹션 분할 미리보기")
    ap.add_argument("file", type=str, help="NPC 프로필 텍스트 파일")
    ap.add_argument("--preview", type=int, default=0,
                    help="각 섹션의 처음 N자 미리보기 (기본: 0=비표시)")
    ap.add_argument("--no-content", action="store_true",
                    help="섹션 내용 미리보기 비활성화 (구조만)")
    args = ap.parse_args()

    path = Path(args.file)
    if not path.exists():
        print(f"❌ 파일 없음: {path}", file=sys.stderr)
        sys.exit(1)

    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        text = path.read_text(encoding="cp949")

    print(f"📄 입력: {path.name}  ({len(text):,}자)")
    print()

    preview = 0 if args.no_content else args.preview
    print(format_report(text, preview_chars=preview))


if __name__ == "__main__":
    main()
