"""
NPC 프로필 섹션 분할 미리보기 도구.

사용법:
    python check_npc_sections.py <NPC프로필.txt>
    python check_npc_sections.py <파일.txt> --preview 80
    python check_npc_sections.py <파일.txt> --no-content

봇 코드 의존 없음. npc_manager._parse_sections / _select_profile_sections
(Sprint L 2026-04-29 기준)을 그대로 복제한 standalone 버전.
"""

import re
import sys
import argparse
from pathlib import Path
from typing import Dict


# =========================================================
# 섹션 분할 로직 (npc_manager.py에서 복제)
# =========================================================

_CORE_SECTIONS = ["Identity", "Hard Rules"]
_MAX_TOTAL_PER_NPC = 50000


def _parse_sections(desc: str) -> Dict[str, str]:
    """### 헤더 기준으로 프로필을 섹션 dict로 분할."""
    sections: Dict[str, str] = {}
    parts = re.split(r'\n(?=###\s)', desc)
    for part in parts:
        header_m = re.match(r'###\s+(.+)', part)
        if header_m:
            sec_name = header_m.group(1).strip()
            sections[sec_name] = part.strip()
        elif not sections:
            sections["_preamble"] = part.strip()
    return sections


def _is_hybrid_profile(desc: str) -> bool:
    """프로필이 hybrid v2 포맷인지 판별. '### Voice' 섹션 존재 여부로 결정."""
    return bool(re.search(r'^###\s+Voice\b', desc, re.MULTILINE))


def _select_profile_sections(desc: str) -> str:
    """모든 섹션을 _CORE 우선으로 순서대로 노출 (Sprint L 2026-04-29)."""
    if not desc or '###' not in desc:
        return desc[:_MAX_TOTAL_PER_NPC] if desc else ""

    parsed = _parse_sections(desc)
    if len(parsed) <= 1:
        return desc[:_MAX_TOTAL_PER_NPC]

    result_parts = []
    included = set()

    preamble = parsed.get("_preamble", "")
    if preamble and preamble.strip():
        result_parts.append(preamble)

    for core_name in _CORE_SECTIONS:
        for sec_name, sec_text in parsed.items():
            if sec_name == "_preamble" or sec_name in included:
                continue
            if core_name.lower() in sec_name.lower():
                result_parts.append(sec_text)
                included.add(sec_name)
                break

    for sec_name, sec_text in parsed.items():
        if sec_name == "_preamble" or sec_name in included:
            continue
        result_parts.append(sec_text)
        included.add(sec_name)

    result = "\n\n".join(result_parts)
    if len(result) > _MAX_TOTAL_PER_NPC:
        result = result[:_MAX_TOTAL_PER_NPC].rstrip()
    return result


# =========================================================
# 분석 / 출력
# =========================================================

def analyze(desc: str) -> dict:
    """프로필 분석 정보 dict."""
    parsed = _parse_sections(desc)
    is_hybrid = _is_hybrid_profile(desc)

    core_found = []
    core_missing = []
    for core in _CORE_SECTIONS:
        found = False
        for sec_name in parsed:
            if sec_name == "_preamble":
                continue
            if core.lower() in sec_name.lower():
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
    fmt = "Hybrid v2 (Voice 통합)" if info["is_hybrid"] else "Legacy (다중 ### 섹션)"
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
        lines.append("⚠ ### 섹션 없음 — 봇은 _MAX_TOTAL_PER_NPC 자르기만 적용")
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
    for core_name in _CORE_SECTIONS:
        for sec_name, sec_text in parsed.items():
            if sec_name == "_preamble" or sec_name in included:
                continue
            if core_name.lower() in sec_name.lower():
                size = len(sec_text)
                bar = "█" * min(40, size // 100)
                lines.append(f"[{idx:>2}] ★CORE   ({size:>5,}자) {bar}")
                lines.append(f"             ### {sec_name}")
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
        lines.append(f"             ### {sec_name}")
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
