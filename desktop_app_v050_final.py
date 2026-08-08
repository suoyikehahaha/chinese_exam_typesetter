"""工作台 v0.5.0 最终入口，保护未修改结构块。"""

from __future__ import annotations

import argparse
from pathlib import Path

import desktop_app as base
from app.editor_importers import import_exam
from desktop_app_v050 import AdvancedDesktopApp


base.VERSION = "0.5.0"


class FinalDesktopApp(AdvancedDesktopApp):
    """仅在用户修改后提交结构内容，防止选择操作改写数据。"""

    def __init__(self) -> None:
        self.selection_dirty = False
        super().__init__()

    def schedule_live_preview(self) -> None:
        if not self.loading_fields and self.selected_block_index is not None:
            self.selection_dirty = True
        super().schedule_live_preview()

    def apply_current_question(self, *, silent: bool = False) -> bool:
        if silent and not self.selection_dirty:
            return False
        changed = super().apply_current_question(silent=silent)
        if changed:
            self.selection_dirty = False
        return changed

    def _on_tree_select(self, event: object) -> None:
        if not self.selection_dirty:
            self.selected_block_index = None
        super()._on_tree_select(event)
        self.selection_dirty = False


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
    FinalDesktopApp().mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
