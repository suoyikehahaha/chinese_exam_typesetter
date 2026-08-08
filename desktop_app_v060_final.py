"""工作台 v0.6.0 最终入口，完善历史状态切换。"""

from __future__ import annotations

import argparse
from pathlib import Path

import desktop_app as base
from app.flexible_importers_v3 import import_exam
from desktop_app_v060 import RichDesktopApp, build_documents_v6


base.VERSION = "0.6.0"
base.build_documents = build_documents_v6


class FinalRichDesktopApp(RichDesktopApp):
    """避免历史恢复时把当前编辑器内容再次写回旧快照。"""

    def apply_selected_text_format(self) -> None:
        super().apply_selected_text_format()
        self.selection_dirty = False

    def clear_selected_text_format(self) -> None:
        super().clear_selected_text_format()
        self.selection_dirty = False

    def _live_preview_now(self) -> None:
        super()._live_preview_now()
        self.selection_dirty = False

    def _restore_history_selection(self) -> None:
        index = self.selected_block_index
        self.selection_dirty = False
        self.selected_block_index = None
        self._populate_tree()
        if index is not None and self.tree.exists(f"block-{index}"):
            self.tree.selection_set(f"block-{index}")
            self.tree.focus(f"block-{index}")
            self._on_tree_select(None)


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
    FinalRichDesktopApp().mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
