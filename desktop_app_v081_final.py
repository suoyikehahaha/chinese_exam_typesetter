"""Final v0.8.1 entry with strictly local character formatting."""

from __future__ import annotations

import argparse
from pathlib import Path

import desktop_app as base
import desktop_app_v070 as v070
from app.flexible_importers_v7 import import_exam
from desktop_app_v080_production import ProductionDesktopApp
from desktop_app_v081_release import build_documents_v81


base.APP_TITLE = "高中语文试卷智能排版工作台（公众号：蓑衣微言）"
base.VERSION = "0.8.1"
base.build_documents = build_documents_v81
v070.import_exam = import_exam


class FinalProductionDesktopApp(ProductionDesktopApp):
    """Separate selected-character settings from paragraph settings."""

    def _save_selected_paragraph_formats(self) -> None:
        super()._save_selected_paragraph_formats()
        for entry in self.current_paragraph_formats:
            entry.pop("font", None)
            entry.pop("size_pt", None)
            entry.pop("bold", None)


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
    FinalProductionDesktopApp().mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
