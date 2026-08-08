"""Chinese exam typesetter v0.4.3 with reliable frozen-build CJK fonts."""

from __future__ import annotations

import desktop_app_current as runtime
import desktop_app_v041 as v041
from app.preview_service_v043 import PreviewService


APP_TITLE = v041.APP_TITLE
APP_VERSION = "0.4.3"


class CurrentDesktopApp(v041.CurrentDesktopApp):
    def __init__(self) -> None:
        runtime.PreviewService = PreviewService
        super().__init__()
        self.title(f"{APP_TITLE} v{APP_VERSION}")


runtime.PreviewService = PreviewService


def main() -> int:
    CurrentDesktopApp().mainloop()
    return 0


__all__ = ["APP_TITLE", "APP_VERSION", "CurrentDesktopApp", "main"]


if __name__ == "__main__":
    raise SystemExit(main())
