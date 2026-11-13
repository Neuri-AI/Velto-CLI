"""Android smoke-test log collection must tolerate native non-UTF-8 bytes."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import subprocess
import sys


def test_smoke_test_preserves_non_utf8_logcat(tmp_path, monkeypatch, capsys):
    source = Path(__file__).resolve().parents[2] / "scripts/android_smoke_test.py"
    spec = importlib.util.spec_from_file_location("velto_android_smoke_test", source)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    apk = tmp_path / "demo-debug.apk"
    apk.write_bytes(b"fake")
    output = tmp_path / "logs"
    monkeypatch.setattr(sys, "argv", [
        "android_smoke_test.py", "--apk", str(apk),
        "--package", "org.example.demo", "--seconds", "0.01",
        "--output", str(output),
    ])

    def fake_adb(*args, **kwargs):
        if args == ("get-state",):
            return subprocess.CompletedProcess(args, 0, "device\n", "")
        if args[:2] == ("install", "-r"):
            return subprocess.CompletedProcess(args, 0, "Success\n", "")
        if args[:2] == ("shell", "monkey"):
            return subprocess.CompletedProcess(args, 0, "Events injected: 1\n", "")
        if args[:2] == ("shell", "pidof"):
            return subprocess.CompletedProcess(args, 0, "1234\n", "")
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(module, "adb", fake_adb)
    raw = b"11-06 10:00:00.000  1234  1234 I MyApp: running\xff\xfe\n"
    calls = []

    def fake_run(argv, **kwargs):
        calls.append((argv, kwargs))
        return subprocess.CompletedProcess(argv, 0, raw, b"")

    monkeypatch.setattr(module.subprocess, "run", fake_run)
    monkeypatch.setattr(module, "classify_android_startup", lambda text: [])
    assert module.main() == 0
    logdir = next(output.iterdir())
    assert (logdir / "logcat.raw").read_bytes() == raw
    assert "\ufffd" in (logdir / "logcat.txt").read_text(encoding="utf-8")
    assert "Observed process: True" in (logdir / "summary.txt").read_text()
    assert calls[0][1]["capture_output"] is True
    assert "text" not in calls[0][1]
    assert "PASS" in capsys.readouterr().out


def test_smoke_test_runs_directly_from_checkout(tmp_path):
    import os

    root = Path(__file__).resolve().parents[2]
    env = os.environ.copy()
    env.pop("PYTHONPATH", None)
    result = subprocess.run(
        [sys.executable, str(root / "scripts/android_smoke_test.py"), "--help"],
        cwd=tmp_path, env=env, capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "--apk" in result.stdout
