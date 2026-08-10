from __future__ import annotations

import json
from pathlib import Path
import unittest
from unittest.mock import patch

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
                    "browser_download_url": "https://github.com/example/ChineseExamTypesetter_0.2.0.exe",
                    "size": 1024,
                },
            ],
        }
        with patch("app.github_updater.urlopen", return_value=_Response(payload)):
            info = check_latest_release("0.1.0")

        self.assertTrue(info.newer)
        self.assertEqual(info.version, "0.2.0")
        self.assertEqual(info.asset_name, "ChineseExamTypesetter_0.2.0.exe")

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
