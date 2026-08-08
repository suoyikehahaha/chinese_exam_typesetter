"""工作台 v0.7.0 最终入口，校准题干悬挂字段提示。"""

from __future__ import annotations

import argparse
from pathlib import Path
import tkinter as tk
from tkinter import ttk
from typing import Any

import desktop_app as base
from app.flexible_importers_v4 import import_exam
from desktop_app_v070 import FinalDesktopAppV7, build_documents_v7


base.APP_TITLE = "高中语文试卷智能排版工作台（公众号：蓑衣微言）"
base.VERSION = "0.7.0"
base.build_documents = build_documents_v7


class ReleaseDesktopApp(FinalDesktopAppV7):
    """题目显示固定悬挂值，结构内容仍显示首行缩进。"""

    def _build_combined_editor(self, tab: ttk.Frame) -> None:
        super()._build_combined_editor(tab)
        self.indent_label: ttk.Label | None = None
        for widget in tab.grid_slaves():
            if isinstance(widget, ttk.Label) and widget.cget("text") == "首行缩进":
                self.indent_label = widget
                break

    def _load_question_fields(self, question: dict[str, Any]) -> None:
        super()._load_question_fields(question)
        if self.indent_label is not None:
            self.indent_label.configure(text="题干悬挂")
        self.indent_var.set("1.5")

    def _load_nonquestion_fields(self, block: dict[str, Any]) -> None:
        super()._load_nonquestion_fields(block)
        if self.indent_label is not None:
            self.indent_label.configure(text="首行缩进")


def main() -> int:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--self-test", metavar="OUTPUT")
    parser.add_argument("--import-test", metavar="FILE")
    args, _ = parser.parse_known_args()
    if args.self_test:
        return base.self_test(Path(args.self_test))
    if args.import_test:
        data = import_exam(Path(args.import_test))
        print(
            [
                block["question"]["number"]
                for block in data["blocks"]
                if block.get("type") == "question"
            ]
        )
        return 0
    ReleaseDesktopApp().mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
