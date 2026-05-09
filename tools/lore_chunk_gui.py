"""
로어북 청크 분할 GUI (Tkinter).

실시간 paste/edit + 슬라이더로 파라미터 조정 + 청크 분포 즉시 확인.
청킹 알고리즘은 check_lore_chunks.py에서 import (sync 부담 0).

사용법:
    python lore_chunk_gui.py
    또는 더블클릭 (Windows에서 Python 연결되어 있으면)
"""

import sys
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from pathlib import Path

# 같은 폴더의 check_lore_chunks에서 import
sys.path.insert(0, str(Path(__file__).parent))
from check_lore_chunks import _split_lore_chunks, format_report


class ChunkGUI:
    def __init__(self, root: tk.Tk):
        self.root = root
        root.title("로어북 청크 미리보기")
        root.geometry("1100x720")
        root.minsize(800, 500)

        self._update_after_id = None
        self._loaded_path: Path | None = None

        self._build_menu()
        self._build_ui()

        # 초기 빈 상태
        self._update()

    # =========================================================
    # UI 구성
    # =========================================================

    def _build_menu(self):
        menubar = tk.Menu(self.root)
        filemenu = tk.Menu(menubar, tearoff=0)
        filemenu.add_command(label="파일 열기...", command=self.load_file, accelerator="Ctrl+O")
        filemenu.add_command(label="리포트 저장...", command=self.save_report, accelerator="Ctrl+S")
        filemenu.add_separator()
        filemenu.add_command(label="종료", command=self.root.quit)
        menubar.add_cascade(label="파일", menu=filemenu)
        self.root.config(menu=menubar)
        self.root.bind("<Control-o>", lambda e: self.load_file())
        self.root.bind("<Control-s>", lambda e: self.save_report())

    def _build_ui(self):
        paned = ttk.PanedWindow(self.root, orient=tk.HORIZONTAL)
        paned.pack(fill=tk.BOTH, expand=True, padx=8, pady=8)

        # === 좌측: 텍스트 입력 ===
        left = ttk.Frame(paned)
        ttk.Label(left, text="로어 텍스트  (paste / edit / 파일 열기)").pack(anchor=tk.W)

        text_frame = ttk.Frame(left)
        text_frame.pack(fill=tk.BOTH, expand=True, pady=(2, 0))

        # 한국어 호환 폰트 (Windows: 맑은 고딕, Mac: AppleGothic, Linux: NanumGothic 폴백)
        text_font = ("Malgun Gothic", 10) if sys.platform == "win32" else ("TkDefaultFont", 10)

        self.text = tk.Text(text_frame, wrap=tk.WORD, font=text_font, undo=True)
        text_scroll = ttk.Scrollbar(text_frame, orient=tk.VERTICAL, command=self.text.yview)
        self.text.configure(yscrollcommand=text_scroll.set)
        self.text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        text_scroll.pack(side=tk.RIGHT, fill=tk.Y)

        self.text.bind("<<Modified>>", self._on_text_modified)

        paned.add(left, weight=2)

        # === 우측: 파라미터 + 통계 + 청크 ===
        right = ttk.Frame(paned)

        # --- 파라미터 ---
        params = ttk.LabelFrame(right, text="파라미터")
        params.pack(fill=tk.X, padx=4, pady=(0, 4))

        self.max_chunk = tk.IntVar(value=4000)
        self.min_chunk = tk.IntVar(value=800)  # V3 영어 로어북 기준
        self.min_len = tk.IntVar(value=50)

        self._add_slider(params, "MAX_CHUNK", self.max_chunk, 1000, 10000, 500)
        self._add_slider(params, "MIN_CHUNK", self.min_chunk, 50, 1000, 50)
        self._add_slider(params, "min_len", self.min_len, 10, 500, 10)

        btn_row = ttk.Frame(params)
        btn_row.pack(fill=tk.X, padx=4, pady=(4, 4))
        ttk.Button(btn_row, text="갱신", command=self._update).pack(side=tk.LEFT, padx=2)
        ttk.Button(btn_row, text="기본값", command=self._reset_params).pack(side=tk.LEFT, padx=2)

        # --- 통계 ---
        stats_frame = ttk.LabelFrame(right, text="통계")
        stats_frame.pack(fill=tk.X, padx=4, pady=4)
        self.stats_label = ttk.Label(stats_frame, text="—", justify=tk.LEFT, font=("Consolas", 10))
        self.stats_label.pack(anchor=tk.W, padx=6, pady=4)

        # --- 청크 리스트 ---
        chunks_frame = ttk.LabelFrame(right, text="청크")
        chunks_frame.pack(fill=tk.BOTH, expand=True, padx=4, pady=4)

        chunks_inner = ttk.Frame(chunks_frame)
        chunks_inner.pack(fill=tk.BOTH, expand=True)

        # Consolas는 한국어도 fallback으로 표시 가능
        self.chunks_text = tk.Text(chunks_inner, wrap=tk.NONE, font=("Consolas", 9), height=15)
        chunks_scroll_y = ttk.Scrollbar(chunks_inner, orient=tk.VERTICAL, command=self.chunks_text.yview)
        chunks_scroll_x = ttk.Scrollbar(chunks_frame, orient=tk.HORIZONTAL, command=self.chunks_text.xview)
        self.chunks_text.configure(yscrollcommand=chunks_scroll_y.set, xscrollcommand=chunks_scroll_x.set)
        self.chunks_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        chunks_scroll_y.pack(side=tk.RIGHT, fill=tk.Y)
        chunks_scroll_x.pack(fill=tk.X)

        paned.add(right, weight=1)

        # === 하단: 상태바 ===
        self.status = ttk.Label(self.root, text="텍스트 입력 또는 파일 열기", relief=tk.SUNKEN, anchor=tk.W)
        self.status.pack(side=tk.BOTTOM, fill=tk.X)

    def _add_slider(self, parent: ttk.LabelFrame, label: str, var: tk.IntVar,
                     mn: int, mx: int, step: int):
        frame = ttk.Frame(parent)
        frame.pack(fill=tk.X, padx=4, pady=2)

        ttk.Label(frame, text=f"{label}:", width=10).pack(side=tk.LEFT)
        value_label = ttk.Label(frame, text=str(var.get()), width=6, anchor=tk.E)
        value_label.pack(side=tk.RIGHT)

        def on_change(v):
            snapped = round(float(v) / step) * step
            snapped = max(mn, min(mx, snapped))
            var.set(int(snapped))
            value_label.config(text=str(var.get()))
            self._schedule_update()

        scale = ttk.Scale(frame, from_=mn, to=mx, orient=tk.HORIZONTAL,
                           variable=var, command=on_change)
        scale.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=4)

    def _reset_params(self):
        self.max_chunk.set(4000)
        self.min_chunk.set(800)  # V3 영어 로어북 기준
        self.min_len.set(50)
        # 슬라이더 옆 숫자 라벨도 갱신해야 해서 강제 update
        self._schedule_update(delay_ms=50)

    # =========================================================
    # 이벤트
    # =========================================================

    def _on_text_modified(self, event=None):
        if self.text.edit_modified():
            self._schedule_update()
            self.text.edit_modified(False)

    def _schedule_update(self, delay_ms: int = 300):
        if self._update_after_id:
            self.root.after_cancel(self._update_after_id)
        self._update_after_id = self.root.after(delay_ms, self._update)

    # =========================================================
    # 청킹 + 표시
    # =========================================================

    def _update(self):
        text = self.text.get("1.0", tk.END).strip()

        if not text:
            self.stats_label.config(text="—")
            self.chunks_text.delete("1.0", tk.END)
            self.status.config(text="입력 없음")
            return

        try:
            chunks = _split_lore_chunks(
                text,
                min_len=self.min_len.get(),
                max_chunk=self.max_chunk.get(),
                min_chunk=self.min_chunk.get(),
            )
        except Exception as e:
            self.status.config(text=f"오류: {e}")
            return

        if not chunks:
            self.stats_label.config(text="청크 0개\n(min_len 미달)")
            self.chunks_text.delete("1.0", tk.END)
            self.status.config(text=f"입력 {len(text):,}자  →  청크 0개")
            return

        # --- 통계 ---
        sizes = [len(c["content"]) for c in chunks]
        n = len(chunks)
        total = sum(sizes)
        avg = total // n
        mn, mx = min(sizes), max(sizes)

        warnings = []
        small = sum(1 for s in sizes if s < self.min_chunk.get())
        large = sum(1 for s in sizes if s >= self.max_chunk.get())
        if small:
            warnings.append(f"⚠ <{self.min_chunk.get()}자: {small}개 (병합 안 잡힘)")
        if large:
            warnings.append(f"⚠ ≥{self.max_chunk.get()}자: {large}개 (분할 한계)")

        stats_text = (
            f"총 청크    {n}개\n"
            f"총 길이    {total:,}자\n"
            f"평균       {avg:,}자\n"
            f"최소       {mn:,}자\n"
            f"최대       {mx:,}자"
        )
        if warnings:
            stats_text += "\n\n" + "\n".join(warnings)
        self.stats_label.config(text=stats_text)

        # --- 청크 리스트 ---
        self.chunks_text.delete("1.0", tk.END)
        for c in chunks:
            size = len(c["content"])
            bar = "█" * min(40, size // 100)
            self.chunks_text.insert(tk.END, f"[{c['index']:>3}] ({size:>5,}자) {bar}\n")
            self.chunks_text.insert(tk.END, f"      {c['label']}\n\n")

        # --- 상태바 ---
        path_info = f"  |  {self._loaded_path.name}" if self._loaded_path else ""
        self.status.config(text=f"입력 {len(text):,}자  →  청크 {n}개{path_info}")

    # =========================================================
    # 파일 I/O
    # =========================================================

    def load_file(self):
        path = filedialog.askopenfilename(
            title="로어 파일 열기",
            filetypes=[
                ("텍스트/마크다운/로어북", "*.txt *.md *.lorebook *.json"),
                ("모든 파일", "*.*"),
            ],
        )
        if not path:
            return
        try:
            content = Path(path).read_text(encoding="utf-8")
        except UnicodeDecodeError:
            try:
                content = Path(path).read_text(encoding="cp949")
            except Exception as e:
                messagebox.showerror("파일 로드 실패", f"인코딩 문제:\n{e}")
                return
        except Exception as e:
            messagebox.showerror("파일 로드 실패", str(e))
            return

        self.text.delete("1.0", tk.END)
        self.text.insert("1.0", content)
        self.text.edit_modified(False)
        self._loaded_path = Path(path)
        self._update()

    def save_report(self):
        text = self.text.get("1.0", tk.END).strip()
        if not text:
            messagebox.showinfo("저장 불가", "먼저 텍스트를 입력하거나 파일을 여세요")
            return

        chunks = _split_lore_chunks(
            text,
            min_len=self.min_len.get(),
            max_chunk=self.max_chunk.get(),
            min_chunk=self.min_chunk.get(),
        )

        default_name = "chunk_report.txt"
        if self._loaded_path:
            default_name = f"{self._loaded_path.stem}_chunks.txt"

        path = filedialog.asksaveasfilename(
            title="리포트 저장",
            defaultextension=".txt",
            initialfile=default_name,
            filetypes=[("텍스트 파일", "*.txt")],
        )
        if not path:
            return

        report = format_report(chunks, preview_chars=80)
        Path(path).write_text(report, encoding="utf-8")
        messagebox.showinfo("저장 완료", f"{path}")


def main():
    root = tk.Tk()
    ChunkGUI(root)
    root.mainloop()


if __name__ == "__main__":
    main()
