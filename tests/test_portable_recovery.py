"""Exercise the real Windows replacement script against disposable windowless applications."""
import os
from pathlib import Path
import subprocess
from time import monotonic, sleep

import pytest

from app.github_updater import _portable_update_script


@pytest.mark.skipif(os.name != "nt", reason="portable replacement runs on Windows")
@pytest.mark.parametrize("healthy", [True, False])
def test_startup_health_decides_whether_to_keep_or_restore_package(tmp_path, healthy):
    compiler = Path(os.environ["WINDIR"]) / "Microsoft.NET/Framework64/v4.0.30319/csc.exe"
    if not compiler.is_file():
        pytest.skip("Windows .NET compiler unavailable")
    target = tmp_path / "original app.exe"
    candidate = tmp_path / "ChineseExamTypesetter_0.3.1.exe"
    marker = tmp_path / "started.txt"
    flags = subprocess.CREATE_NO_WINDOW
    for path, label, status in ((target, "old", "GUI_STARTUP_OK"),
                                (candidate, "new", "GUI_STARTUP_OK" if healthy else "GUI_STARTUP_FAILED")):
        source = tmp_path / "health.cs"
        source.write_text(
            'using System.IO; class Health { static void Main(string[] args) { '
            'if(args.Length == 2) File.WriteAllText(args[1], '
            '@"{""status"":""'+status+'"",""version"":""0.3.1""}"); '
            'else File.WriteAllText(@"'+str(marker)+'","'+label+'"); }}', encoding="utf-8-sig")
        subprocess.run([str(compiler), "/nologo", "/target:winexe", "/out:"+str(path), str(source)],
                       check=True, capture_output=True, creationflags=flags, timeout=25)
    old_bytes, new_bytes = target.read_bytes(), candidate.read_bytes()
    script = tmp_path / "upgrade.ps1"
    script.write_text(_portable_update_script(candidate, target, 2147483647), encoding="utf-8-sig")
    outcome = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                              "-File", str(script)], capture_output=True, creationflags=flags, timeout=30)
    deadline = monotonic() + 5
    while not marker.is_file() and monotonic() < deadline:
        sleep(0.05)
    assert (outcome.returncode == 0) == healthy, outcome.stderr
    assert marker.read_text() == ("new" if healthy else "old")
    assert target.read_bytes() == (new_bytes if healthy else old_bytes)
    assert target.with_suffix(".exe.previous").exists() != healthy
    assert not candidate.exists()
