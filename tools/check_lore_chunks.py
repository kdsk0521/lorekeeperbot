"""
로어북 청크 분할 미리보기 도구.

사용법:
    python check_lore_chunks.py <로어파일.txt>
    python check_lore_chunks.py <로어파일.txt> --preview 80
    python check_lore_chunks.py <로어파일.txt> --no-content

봇 코드 의존 없음 (re 모듈만 사용). 어디서나 단독 실행 가능.
command_handler.py의 _split_lore_chunks (V2)를 그대로 복제한 standalone 버전.
"""

import re
import sys
import argparse
from pathlib import Path


# =========================================================
# 청크 분할 로직 (command_handler.py L38-218에서 복제)
# =========================================================

def _split_lore_chunks(lore_text: str, min_len: int = 50,
                        max_chunk: int = 4000, min_chunk: int = 800) -> list:
    """로어 텍스트를 섹션 단위로 청크 분할 (V3 — 영어 로어북 기준).

    봇 default와 동일 (max_chunk=4000, min_chunk=800). GUI에서 슬라이더로
    실험할 수 있도록 파라미터화. 봇 코드는 default 값으로 호출하므로 동작 동일.
    """
    if not lore_text or not lore_text.strip():
        return []

    _MAX_CHUNK = max_chunk
    _MIN_CHUNK = min_chunk

    _SEP = re.compile(r'^[\s]*[=\-\*~]{3,}[\s]*$')
    # 마크다운 헤더만 (# Title / ## Title)
    # 07-28 대조: 봇 원본도 V3에서 \d+\. / SECTION 패턴 제거 완료 — 두 정규식 동일.
    _MAJOR = re.compile(r'^#{1,2}\s+')
    _MINOR = re.compile(
        r'^(?:\[[\d.]+\]\s|---\s+.+\s+---|#{3,}\s+)'
    )

    def _label(text: str) -> str:
        s = text.lstrip('#').strip().rstrip(':').strip()
        return s[:80] if s else "Section"

    # Step 1: 구분선 제거
    lines = [l for l in lore_text.split('\n') if not _SEP.match(l)]

    # Step 2: 메이저 헤더 기준 분리
    sections = []
    buf = []
    cur_label = ""

    for line in lines:
        stripped = line.strip()
        indent = len(line) - len(line.lstrip())
        if indent <= 1 and stripped and _MAJOR.match(stripped):
            content = '\n'.join(buf).strip()
            if content and len(content) >= min_len:
                sections.append({"label": cur_label or _label(content), "content": content})
            buf = [line]
            cur_label = _label(stripped)
        else:
            if not cur_label and stripped:
                cur_label = _label(stripped)
            buf.append(line)

    content = '\n'.join(buf).strip()
    if content and len(content) >= min_len:
        sections.append({"label": cur_label or _label(content), "content": content})

    # Step 2b: 폴백 (섹션 1개 + 대형이면 문단 분할)
    if len(sections) <= 1 and sections and len(sections[0]["content"]) > _MAX_CHUNK:
        sections = _chunk_by_paragraph(sections[0]["content"], min_len, _MAX_CHUNK)

    # Step 3: 대형 섹션 서브헤더 분할
    split_result = []
    for sec in sections:
        if len(sec["content"]) <= _MAX_CHUNK:
            split_result.append(sec)
        else:
            split_result.extend(
                _chunk_split_minor(sec, _MINOR, min_len, _MAX_CHUNK)
            )

    # Step 4: 소형 섹션 병합
    merged = []
    for sec in split_result:
        if merged and len(merged[-1]["content"]) < _MIN_CHUNK:
            merged[-1]["content"] += "\n\n" + sec["content"]
            if len(merged[-1]["label"]) < 50:
                merged[-1]["label"] += " + " + sec["label"]
        else:
            merged.append(sec)
    if len(merged) > 1 and len(merged[-1]["content"]) < _MIN_CHUNK:
        merged[-2]["content"] += "\n\n" + merged[-1]["content"]
        merged.pop()

    # Step 5: 인덱싱
    for i, c in enumerate(merged):
        c["index"] = i
        c["label"] = c["label"][:80]
    return merged


def _chunk_split_minor(section: dict, minor_re, min_len: int, max_chunk: int) -> list:
    """대형 섹션을 마이너 헤더에서 분할."""
    parent = section["label"]
    lines = section["content"].split('\n')
    parts = []
    buf = []
    sub_label = ""

    def _lbl(text):
        m = re.match(r'\[([\d.]+)\]\s*(.*)', text)
        if m:
            return f"{m.group(1)} {m.group(2).strip().rstrip(':').strip()}"[:60]
        m = re.match(r'---\s+(.+?)\s+---', text)
        if m:
            return m.group(1).strip()[:60]
        return text.lstrip('#').strip().rstrip(':').strip()[:60]

    for line in lines:
        stripped = line.strip()
        if stripped and minor_re.match(stripped) and buf:
            content = '\n'.join(buf).strip()
            if content and len(content) >= min_len:
                lbl = f"{parent} > {sub_label}" if sub_label else parent
                parts.append({"label": lbl, "content": content})
            buf = [line]
            sub_label = _lbl(stripped)
        else:
            if not sub_label and stripped:
                sub_label = _lbl(stripped)
            buf.append(line)

    if buf:
        content = '\n'.join(buf).strip()
        if content and len(content) >= min_len:
            lbl = f"{parent} > {sub_label}" if sub_label else parent
            parts.append({"label": lbl, "content": content})

    if len(parts) == 1:
        parts[0]["label"] = parent

    final = []
    for p in (parts or [section]):
        if len(p["content"]) > max_chunk:
            final.extend(_chunk_by_paragraph(p["content"], min_len, max_chunk, p["label"]))
        else:
            final.append(p)
    return final


def _chunk_by_paragraph(text: str, min_len: int, max_chunk: int, parent_label: str = "") -> list:
    """문단 기반 폴백 분할."""
    paragraphs = re.split(r'\n{2,}', text)
    chunks = []
    buf = ""

    def _lbl(t):
        first = t.split('\n')[0].strip().lstrip('#').strip().rstrip(':').strip()
        return first[:80] if first else "Section"

    for para in paragraphs:
        para = para.strip()
        if not para:
            continue
        if buf and len(buf) + len(para) + 2 > max_chunk:
            if len(buf) >= min_len:
                chunks.append({"label": parent_label or _lbl(buf), "content": buf})
            buf = para
        else:
            buf = buf + "\n\n" + para if buf else para

    if buf and len(buf) >= min_len:
        chunks.append({"label": parent_label or _lbl(buf), "content": buf})

    if parent_label and len(chunks) > 1:
        parent_prefix = parent_label.split('>')[0].strip()[:30]
        for c in chunks:
            for line in c["content"].split('\n'):
                line = line.strip()
                if line and line[:30] != parent_prefix:
                    c["label"] = f"{parent_label} > {line[:40]}"
                    break
    return chunks


# =========================================================
# CLI / 출력
# =========================================================

def format_report(chunks: list, preview_chars: int = 0) -> str:
    """청크 리스트 → 사람이 읽는 리포트."""
    if not chunks:
        return "청크 0개 — 입력 텍스트가 비었거나 min_len 미달."

    lines = []
    n = len(chunks)
    sizes = [len(c["content"]) for c in chunks]
    total = sum(sizes)
    avg = total // n
    mn, mx = min(sizes), max(sizes)

    lines.append(f"총 청크: {n}개")
    lines.append(f"총 길이: {total:,}자  |  평균: {avg:,}자  |  최소: {mn:,}자  |  최대: {mx:,}자")
    lines.append("")
    lines.append("─" * 70)

    for c in chunks:
        size = len(c["content"])
        bar = "█" * min(40, size // 100)  # 시각적 길이 바 (100자당 1블록, max 40)
        lines.append(f"[{c['index']:>2}] ({size:>5,}자) {bar}")
        lines.append(f"     {c['label']}")
        if preview_chars > 0:
            preview = c["content"][:preview_chars].replace('\n', ' ⏎ ')
            if len(c["content"]) > preview_chars:
                preview += "..."
            lines.append(f"     ┊ {preview}")
        lines.append("")

    # 분포 경고
    warnings = []
    if mn < 200:
        small = sum(1 for s in sizes if s < 200)
        warnings.append(f"⚠ 200자 미만 청크 {small}개 — 병합 룰이 안 잡은 케이스")
    if mx >= 4000:
        large = sum(1 for s in sizes if s >= 4000)
        warnings.append(f"⚠ 4000자 이상 청크 {large}개 — 마이너 헤더로 더 못 쪼갠 케이스")
    if warnings:
        lines.append("─" * 70)
        for w in warnings:
            lines.append(w)

    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser(description="로어북 청크 분할 미리보기")
    ap.add_argument("file", type=str, help="로어 텍스트 파일 경로 (.txt, .md 등)")
    ap.add_argument("--preview", type=int, default=0,
                    help="각 청크의 처음 N자 미리보기 (기본: 0=비표시)")
    ap.add_argument("--min-len", type=int, default=50,
                    help="최소 청크 길이 (기본: 50)")
    ap.add_argument("--max-chunk", type=int, default=4000,
                    help="최대 청크 크기, 초과 시 분할 (기본: 4000)")
    ap.add_argument("--min-chunk", type=int, default=800,
                    help="최소 청크 크기, 미만 시 인접 병합 (기본: 800, 영어 로어북 기준)")
    ap.add_argument("--no-content", action="store_true",
                    help="청크 내용 미리보기 비활성화 (구조만 보고 싶을 때)")
    args = ap.parse_args()

    path = Path(args.file)
    if not path.exists():
        print(f"❌ 파일 없음: {path}", file=sys.stderr)
        sys.exit(1)

    text = path.read_text(encoding="utf-8")
    print(f"📄 입력: {path.name}  ({len(text):,}자)")
    print()

    chunks = _split_lore_chunks(text, min_len=args.min_len,
                                  max_chunk=args.max_chunk, min_chunk=args.min_chunk)
    preview = 0 if args.no_content else args.preview
    print(format_report(chunks, preview_chars=preview))


if __name__ == "__main__":
    main()
