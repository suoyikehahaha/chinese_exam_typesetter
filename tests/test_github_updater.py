from __future__ import annotations

import json
import os
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from app.github_updater import (
    GITHUB_API,
    GITHUB_REPOSITORY,
    _portable_update_script,
    check_latest_release,
)


class _Response:
    def __init__(self, payload: dict[str, object]) -> None:
        self.payload = payload

    def __enter__(self) -> "_Response":
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def read(self) -> bytes:
        return json.dumps(self.payload).encode("utf-8")


class GitHubUpdaterTests(unittest.TestCase):
    def _desktop(self, *, automatic: bool = True):
        from app.ui.main_window import CurrentDesktopApp

        app = SimpleNamespace(
            _pending_update=None,
            _update_in_progress=True,
            _update_automatic=automatic,
            _busy=False,
            _check_update_action=Mock(),
            _set_status=Mock(),
            _set_busy=Mock(),
            _show_error=Mock(),
            _ask_yes_no=Mock(return_value=False),
            _download_update=Mock(),
            _signals=Mock(),
        )
        return app, CurrentDesktopApp

    def test_automatic_latest_check_is_quiet(self) -> None:
        app, cls = self._desktop(automatic=True)
        with patch("app.ui.session_actions.QMessageBox.information") as show:
            cls._on_update_checked(app, SimpleNamespace(newer=False))
        show.assert_not_called()
        app._set_busy.assert_not_called()
        self.assertFalse(app._update_in_progress)

    def test_automatic_failure_preserves_ui_and_reports_retry(self) -> None:
        app, cls = self._desktop(automatic=True)
        cls._on_update_error(app, "offline")
        app._show_error.assert_not_called()
        app._set_status.assert_not_called()
        self.assertIn("重新检查", app._check_update_action.setText.call_args.args[0])
        self.assertFalse(app._update_in_progress)

    def test_new_release_requires_download_confirmation(self) -> None:
        info = SimpleNamespace(
            newer=True, version="0.4.0", name="Release", asset_name="test.exe", asset_size=1024
        )
        app, cls = self._desktop(automatic=True)
        cls._on_update_checked(app, info)
        app._ask_yes_no.assert_called_once()
        app._download_update.assert_not_called()

    def test_duplicate_checks_do_not_start_workers(self) -> None:
        app, cls = self._desktop(automatic=True)
        with patch("app.ui.session_actions.threading.Thread") as thread:
            cls.check_for_updates(app)
        thread.assert_not_called()

    def test_manual_latest_check_shows_result(self) -> None:
        app, cls = self._desktop(automatic=False)
        with patch("app.ui.session_actions.QMessageBox.information") as show:
            cls._on_update_checked(app, SimpleNamespace(newer=False))
        show.assert_called_once()

    def test_official_repository_is_fixed(self) -> None:
        self.assertEqual(
            GITHUB_REPOSITORY,
            "https://github.com/suoyikehahaha/chinese_exam_typesetter",
        )
        self.assertIn("suoyikehahaha/chinese_exam_typesetter", GITHUB_API)

    def test_latest_release_selects_portable_exe(self) -> None:
        payload = {
            "tag_name": "v0.2.0",
            "name": "0.2.0",
            "body": "更新说明",
            "html_url": "https://github.com/suoyikehahaha/chinese_exam_typesetter/releases/tag/v0.2.0",
            "assets": [
                {
                    "name": "source.zip",
                    "browser_download_url": "https://github.com/example/source.zip",
                    "size": 10,
                },
                {
                    "name": "ChineseExamTypesetter_0.2.0.exe",
                    "browser_download_url": "https://github.com/suoyikehahaha/chinese_exam_typesetter/releases/download/v0.2.0/ChineseExamTypesetter_0.2.0.exe",
                    "size": 1024,
                },
            ],
        }
        with patch("app.github_updater.urlopen", return_value=_Response(payload)):
            info = check_latest_release("0.1.0")

        self.assertTrue(info.newer)
        self.assertEqual(info.version, "0.2.0")
        self.assertEqual(info.asset_name, "ChineseExamTypesetter_0.2.0.exe")

    @unittest.skipUnless(os.name == "nt", "Windows native HTTPS client")
    def test_native_transport_rechecks_https_and_returns_exact_bytes(self) -> None:
        from urllib.request import Request
        from app.github_updater import _open_release_request

        def transfer(args, *, stdout, **kwargs):
            self.assertEqual(args[args.index("--proto-redir") + 1], "=https")
            self.assertEqual(args[args.index("--proto") + 1], "=https")
            self.assertNotIn("--insecure", args)
            self.assertEqual(args[-1], GITHUB_API)
            stdout.write(b"release bytes")
            return SimpleNamespace(returncode=0)

        with (
            patch("app.github_updater.urlopen", side_effect=FileNotFoundError()),
            patch("app.github_updater.subprocess.run", side_effect=transfer),
        ):
            with _open_release_request(Request(GITHUB_API), 10) as stream:
                self.assertEqual(stream.read(), b"release bytes")

    def test_portable_update_script_replaces_and_restarts_exe(self) -> None:
        script = _portable_update_script(
            Path("C:/Temp/ChineseExamTypesetter_0.2.0.exe"),
            Path("C:/Tools/ChineseExamTypesetter_0.1.0.exe"),
            123,
        )

        self.assertIn("Wait-Process -Id $processId", script)
        self.assertIn("Copy-Item -LiteralPath $download -Destination $target", script)
        self.assertIn("Start-Process -FilePath $target", script)
        self.assertIn(".previous", script)


if __name__ == "__main__":
    unittest.main()
