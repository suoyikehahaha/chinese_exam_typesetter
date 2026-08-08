"""工作台 v0.4.0，增加上下文识别与 PDF 导入。"""

from __future__ import annotations

import argparse
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox

import desktop_app as base
from app.editor_importers import import_exam


base.VERSION = "0.4.0"


class FlexibleDesktopApp(base.DesktopApp):
    """支持多结构 DOCX 和文本型 PDF 的工作台。"""

    def import_new_exam(self) -> None:
        path = filedialog.askopenfilename(
            title="导入新试题",
            filetypes=[
                ("支持的试题", "*.json *.docx *.pdf *.txt *.md"),
                ("结构化 JSON", "*.json"),
                ("Word 文档", "*.docx"),
                ("PDF 文档", "*.pdf"),
                ("文本或 Markdown", "*.txt *.md"),
                ("所有文件", "*.*"),
            ],
        )
        if not path:
            return
        try:
            self.raw_exam = import_exam(path)
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror(base.APP_TITLE, str(exc))
            return
        self.current_exam_path = Path(path)
        self.selected_block_index = None
        self._populate_tree()
        self._load_global_fields()
        question_count = sum(
            1 for block in self.raw_exam.get("blocks", []) if block.get("type") == "question"
        )
        notice_count = len(self.raw_exam.get("metadata", {}).get("notices", []))
        notice_text = f"，卷首说明 {notice_count} 条" if notice_count else ""
        self.status_var.set(
            f"已导入 {Path(path).name}，识别 {question_count} 道题{notice_text}。"
        )
        self.request_preview()


def main() -> int:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--self-test", metavar="OUTPUT")
    parser.add_argument("--import-test", metavar="FILE")
    args, _ = parser.parse_known_args()
    if args.self_test:
        return base.self_test(Path(args.self_test))
    if args.import_test:
        data = import_exam(Path(args.import_test))
        numbers = [
            block["question"]["number"]
            for block in data["blocks"]
            if block.get("type") == "question"
        ]
        print(numbers)
        return 0
    FlexibleDesktopApp().mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
