"""
NPC 프로필 섹션 분할 GUI (Tkinter).

실시간 paste/edit + 즉시 섹션 목록 + Identity/Hard Rules 우선 순서 시각화.
파라미터 없음 (### 헤더로 결정론). 알고리즘은 check_npc_sections.py에서 import.

사용법:
    python npc_section_gui.py
    또는 더블클릭 (Windows에서 Python 연결되어 있으면)
"""

import sys
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from check_npc_sections import (analyze, format_report, _CORE_FAMILIES,
                                _section_family, _MAX_TOTAL_PER_NPC)


class NPCSectionGUI:
    def __init__(self, root: tk.Tk):
        self.root = root
        root.title("NPC 프로필 섹션 미리보기")
        root.geometry("1100x720")
        root.minsize(800, 500)

        self._update_after_id = None
        self._loaded_path: Path | None = None

        self._build_menu()
        self._build_ui()
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
        ttk.Label(left, text="NPC 프로필  (paste / edit / 파일 열기)").pack(anchor=tk.W)

        text_frame = ttk.Frame(left)
        text_frame.pack(fill=tk.BOTH, expand=True, pady=(2, 0))

        text_font = ("Malgun Gothic", 10) if sys.platform == "win32" else ("TkDefaultFont", 10)

        self.text = tk.Text(text_frame, wrap=tk.WORD, font=text_font, undo=True)
        text_scroll = ttk.Scrollbar(text_frame, orient=tk.VERTICAL, command=self.text.yview)
        self.text.configure(yscrollcommand=text_scroll.set)
        self.text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        text_scroll.pack(side=tk.RIGHT, fill=tk.Y)

        self.text.bind("<<Modified>>", self._on_text_modified)

        paned.add(left, weight=2)

        # === 우측: 정보 + 섹션 ===
        right = ttk.Frame(paned)

        # --- NPC 정보 ---
        info_frame = ttk.LabelFrame(right, text="NPC 정보")
        info_frame.pack(fill=tk.X, padx=4, pady=(0, 4))
        self.info_label = ttk.Label(info_frame, text="—", justify=tk.LEFT, font=("Consolas", 10))
        self.info_label.pack(anchor=tk.W, padx=6, pady=4)

        # --- 섹션 리스트 ---
        sections_frame = ttk.LabelFrame(right, text="섹션 (Identity/Hard Rules 우선 순서)")
        sections_frame.pack(fill=tk.BOTH, expand=True, padx=4, pady=4)

        sections_inner = ttk.Frame(sections_frame)
        sections_inner.pack(fill=tk.BOTH, expand=True)

        self.sections_text = tk.Text(sections_inner, wrap=tk.NONE, font=("Consolas", 9), height=20)
        sections_scroll_y = ttk.Scrollbar(sections_inner, orient=tk.VERTICAL, command=self.sections_text.yview)
        sections_scroll_x = ttk.Scrollbar(sections_frame, orient=tk.HORIZONTAL, command=self.sections_text.xview)
        self.sections_text.configure(yscrollcommand=sections_scroll_y.set, xscrollcommand=sections_scroll_x.set)
        self.sections_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        sections_scroll_y.pack(side=tk.RIGHT, fill=tk.Y)
        sections_scroll_x.pack(fill=tk.X)

        # CORE 표시용 색상 태그
        self.sections_text.tag_configure("core", foreground="#cc6600")
        self.sections_text.tag_configure("warn", foreground="#cc0000")

        paned.add(right, weight=1)

        # === 하단: 상태바 ===
        self.status = ttk.Label(self.root, text="NPC 프로필을 입력하거나 파일을 여세요", relief=tk.SUNKEN, anchor=tk.W)
        self.status.pack(side=tk.BOTTOM, fill=tk.X)

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
    # 분석 + 표시
    # =========================================================

    def _update(self):
        text = self.text.get("1.0", tk.END).strip()

        if not text:
            self.info_label.config(text="—")
            self.sections_text.delete("1.0", tk.END)
            self.status.config(text="입력 없음")
            return

        try:
            info = analyze(text)
        except Exception as e:
            self.status.config(text=f"오류: {e}")
            return

        # --- 정보 ---
        fmt = "Hybrid v2 (Voice 통합)" if info["is_hybrid"] else "Legacy (다중 ###)"
        info_lines = [
            f"포맷       {fmt}",
            f"총 길이    {info['total_size']:,}자",
            f"섹션 수    {info['section_count']}개",
        ]
        if info["preamble_size"]:
            info_lines.append(f"머리말     {info['preamble_size']:,}자")
        if info["core_missing"]:
            info_lines.append(f"⚠ CORE 누락  {', '.join(info['core_missing'])}")
        else:
            info_lines.append(f"CORE 확인  {', '.join(c[0] for c in info['core_found'])}")
        self.info_label.config(text="\n".join(info_lines))

        # --- 섹션 리스트 ---
        self.sections_text.delete("1.0", tk.END)
        parsed = info["parsed"]

        if info["section_count"] == 0 and not info["preamble_size"]:
            self.sections_text.insert(tk.END, "⚠ ### 섹션 없음\n", "warn")
            self.sections_text.insert(tk.END, "봇은 _MAX_TOTAL_PER_NPC 자르기만 적용\n")
            self.status.config(text=f"입력 {len(text):,}자  →  섹션 0개")
            return

        included = set()
        idx = 0

        # 머리말
        if info["preamble_size"]:
            size = info["preamble_size"]
            bar = "█" * min(40, size // 100)
            self.sections_text.insert(tk.END, f"[머리말]      ({size:>5,}자) {bar}\n")
            self.sections_text.insert(tk.END, "\n")
            idx += 1

        # CORE 섹션 (★) — [2026-09-02] 정확일치 리스트 → 가족 판정(실물·CLI와 동일).
        #   구 코드는 `## 1. Basic Info`처럼 번호·자유 명명 시트에서 전부 빗나갔다.
        for core_name in _CORE_FAMILIES:
            for sec_name, sec_text in parsed.items():
                if sec_name == "_preamble" or sec_name in included:
                    continue
                if _section_family(sec_name) == core_name:
                    size = len(sec_text)
                    bar = "█" * min(40, size // 100)
                    self.sections_text.insert(tk.END, f"[{idx:>2}] ", "core")
                    self.sections_text.insert(tk.END, f"★CORE   ", "core")
                    self.sections_text.insert(tk.END, f"({size:>5,}자) {bar}\n")
                    self.sections_text.insert(tk.END, f"            ### {sec_name}\n\n")
                    included.add(sec_name)
                    idx += 1
                    break

        # 나머지
        for sec_name, sec_text in parsed.items():
            if sec_name == "_preamble" or sec_name in included:
                continue
            size = len(sec_text)
            bar = "█" * min(40, size // 100)
            self.sections_text.insert(tk.END, f"[{idx:>2}]         ({size:>5,}자) {bar}\n")
            self.sections_text.insert(tk.END, f"            ### {sec_name}\n\n")
            included.add(sec_name)
            idx += 1

        # 경고
        if info["total_size"] >= _MAX_TOTAL_PER_NPC:
            self.sections_text.insert(tk.END, "─" * 60 + "\n")
            self.sections_text.insert(tk.END, f"⚠ 총 ≥ {_MAX_TOTAL_PER_NPC:,}자 — 안전망에서 잘림\n", "warn")
        elif info["total_size"] > 30000:
            self.sections_text.insert(tk.END, "─" * 60 + "\n")
            self.sections_text.insert(tk.END, f"※ 큰 프로필 ({info['total_size']:,}자) — Slot 7 부담\n")

        # --- 상태바 ---
        path_info = f"  |  {self._loaded_path.name}" if self._loaded_path else ""
        self.status.config(text=f"입력 {len(text):,}자  →  섹션 {info['section_count']}개{path_info}")

    # =========================================================
    # 파일 I/O
    # =========================================================

    def load_file(self):
        path = filedialog.askopenfilename(
            title="NPC 프로필 파일 열기",
            filetypes=[
                ("텍스트/마크다운", "*.txt *.md"),
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

        default_name = "npc_section_report.txt"
        if self._loaded_path:
            default_name = f"{self._loaded_path.stem}_sections.txt"

        path = filedialog.asksaveasfilename(
            title="리포트 저장",
            defaultextension=".txt",
            initialfile=default_name,
            filetypes=[("텍스트 파일", "*.txt")],
        )
        if not path:
            return

        report = format_report(text, preview_chars=80)
        Path(path).write_text(report, encoding="utf-8")
        messagebox.showinfo("저장 완료", f"{path}")


def main():
    root = tk.Tk()
    NPCSectionGUI(root)
    root.mainloop()


if __name__ == "__main__":
    main()
