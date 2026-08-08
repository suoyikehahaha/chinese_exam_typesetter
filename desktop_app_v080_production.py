"""Production v0.8.0 entry with safe local-format behavior."""

from __future__ import annotations

import argparse
from copy import deepcopy
from pathlib import Path

import desktop_app as base
import desktop_app_v070 as v070
from app.flexible_importers_v7 import import_exam
from desktop_app_v080_final import build_documents_v8_final
from desktop_app_v080_release import WordLikeDesktopApp


base.APP_TITLE = "高中语文试卷智能排版工作台（公众号：蓑衣微言）"
base.VERSION = "0.8.0"
base.build_documents = build_documents_v8_final
v070.import_exam = import_exam


class ProductionDesktopApp(WordLikeDesktopApp):
    """Keep whole-structure styles unchanged during selected-text edits."""

    def apply_current_question(self, *, silent: bool = False) -> bool:
        preserve_whole = (
            self.selection_dirty
            and self.selected_block_index is not None
            and self._has_selection()
        )
        previous = None
        had_format = False
        owner = None
        if preserve_whole:
            block = self.raw_exam["blocks"][self.selected_block_index]
            owner = block["question"] if block.get("type") == "question" else block
            had_format = "format" in owner
            previous = deepcopy(owner.get("format"))
        applied = super().apply_current_question(silent=silent)
        if applied and preserve_whole and owner is not None:
            if had_format:
                owner["format"] = previous
            else:
                owner.pop("format", None)
        return applied

    def _format_changed(self) -> None:
        if self.loading_fields or self.selected_block_index is None:
            return
        if self._has_selection():
            try:
                self._record_history()
                self._apply_selection_format()
                self._save_selected_paragraph_formats()
                self.selection_dirty = True
                self.apply_current_question(silent=True)
                self.selection_hint_var.set(
                    "已局部应用，整道题原有字体保持不变，正在更新预览。"
                )
            except ValueError:
                return
        self.schedule_live_preview()


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
    ProductionDesktopApp().mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
