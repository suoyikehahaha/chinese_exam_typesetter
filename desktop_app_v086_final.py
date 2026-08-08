"""Final v0.8.6 entry with stable structure-to-preview navigation."""

from __future__ import annotations

import argparse
from pathlib import Path

import desktop_app as base
from app.flexible_importers_v10 import import_exam
from desktop_app_v086_release import (
    ProductionDesktopApp as ProductionDesktopAppV086,
    build_documents_v86,
)


base.APP_TITLE = "高中语文试卷智能排版工作台（公众号：蓑衣微言）"
base.VERSION = "0.8.6"
base.build_documents = build_documents_v86


class ProductionDesktopApp(ProductionDesktopAppV086):
    """Repeat the requested scroll position after canvas resize events settle."""

    def __init__(self) -> None:
        self._preview_scroll_target: float | None = None
        super().__init__()

    def _jump_to_block(self, block_index: int) -> None:
        locator = self._preview_block_locators.get(block_index)
        if locator is not None:
            self._preview_scroll_target = locator[1]
        super()._jump_to_block(block_index)
        self._schedule_scroll_target()

    def _show_current_page(self) -> None:
        super()._show_current_page()
        self._schedule_scroll_target()

    def _schedule_scroll_target(self) -> None:
        if self._preview_scroll_target is None:
            return
        for delay in (15, 70, 160):
            self.after(delay, self._apply_scroll_target)

    def _apply_scroll_target(self) -> None:
        if self._preview_scroll_target is not None:
            self.canvas.yview_moveto(self._preview_scroll_target)

    def previous_page(self) -> None:
        self._preview_scroll_target = None
        super().previous_page()

    def next_page(self) -> None:
        self._preview_scroll_target = None
        super().next_page()


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
    ProductionDesktopApp().mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(run_cli())
