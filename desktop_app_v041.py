"""Chinese exam typesetter v0.4.1 with complete read-only preview."""

from __future__ import annotations

import desktop_app_current as runtime
from app.preview_service_v041 import PreviewService

runtime.PreviewService = PreviewService

import desktop_app_v04_final as v04  # noqa: E402


APP_TITLE = v04.APP_TITLE
APP_VERSION = "0.4.1"


class CurrentDesktopApp(v04.CurrentDesktopApp):
    def __init__(self) -> None:
        super().__init__()
        self.title(f"{APP_TITLE} v{APP_VERSION}")


def main() -> int:
    CurrentDesktopApp().mainloop()
    return 0


__all__ = ["APP_TITLE", "APP_VERSION", "CurrentDesktopApp", "main"]


if __name__ == "__main__":
    raise SystemExit(main())
