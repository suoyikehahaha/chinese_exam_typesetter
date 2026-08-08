"""Final v0.8.2 entry with the automatic typeset filename suffix."""

from __future__ import annotations

import argparse
from pathlib import Path

import desktop_app as base
from desktop_app_v070 import WordOnlyExportDialog
from desktop_app_v082_release import (
    AdaptiveDesktopApp as AdaptiveDesktopAppBase,
    build_documents_v82,
    import_exam,
)


base.APP_TITLE = "高中语文试卷智能排版工作台（公众号：蓑衣微言）"
base.VERSION = "0.8.2"
base.build_documents = build_documents_v82


def typeset_name(value: str) -> str:
    """Return a safe export name carrying one typeset suffix."""

    name = base._safe_filename(value.strip())
    suffix = "（排版）"
    return name if name.endswith(suffix) else f"{name}{suffix}"


class TypesetExportDialog(WordOnlyExportDialog):
    """Add the suffix when the dialog opens and again before submission."""

    def __init__(self, parent: "AdaptiveDesktopApp") -> None:
        super().__init__(parent)
        self.name_var.set(typeset_name(self.name_var.get()))

    def _submit(self) -> None:
        self.name_var.set(typeset_name(self.name_var.get()))
        super()._submit()


class AdaptiveDesktopApp(AdaptiveDesktopAppBase):
    """Use the adaptive editor with a consistent exported filename."""

    def open_export_dialog(self) -> None:
        if self.busy:
            base.messagebox.showinfo(base.APP_TITLE, "请等待当前预览任务完成。")
            return
        self.apply_current_question(silent=True)
        TypesetExportDialog(self)


def run_cli() -> int:
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
    AdaptiveDesktopApp().mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(run_cli())
