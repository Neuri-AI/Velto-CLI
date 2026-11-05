#!/usr/bin/env python3
"""Install and smoke-test a Velto Android APK on an attached ADB device.

Example:
  python scripts/android_smoke_test.py --apk path/to/app-debug.apk \
      --package org.androidappqt.androidappqt --seconds 8
"""
import argparse
from datetime import datetime
from pathlib import Path
import subprocess
import sys
import time

from velto_cli.adapters.mobile.qt.android_runtime_diagnostics import (
    classify_android_startup,
)


def adb(*args: str, capture: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["adb", *args], text=True, capture_output=capture, check=False
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apk", type=Path, required=True)
    parser.add_argument("--package", required=True)
    parser.add_argument("--seconds", type=float, default=8.0)
    parser.add_argument("--output", type=Path, default=Path("android-smoke-logs"))
    args = parser.parse_args()

    if not args.apk.is_file():
        parser.error(f"APK does not exist: {args.apk}")
    if args.seconds <= 0:
        parser.error("--seconds must be positive")

    session = args.output / datetime.now().strftime("%Y%m%d_%H%M%S")
    session.mkdir(parents=True, exist_ok=True)
    device = adb("get-state")
    if device.returncode or device.stdout.strip() != "device":
        print("ADB device unavailable or unauthorized", file=sys.stderr)
        return 2

    install = adb("install", "-r", str(args.apk.resolve()))
    (session / "install.txt").write_text(
        install.stdout + install.stderr, encoding="utf-8"
    )
    if install.returncode or "Success" not in install.stdout:
        print(f"APK installation failed; see {session / 'install.txt'}", file=sys.stderr)
        return 2

    adb("shell", "am", "force-stop", args.package)
    cleared = adb("logcat", "-c")
    if cleared.returncode:
        print("Warning: unable to clear logcat; results may include older errors.")

    launch = adb(
        "shell", "monkey", "-p", args.package,
        "-c", "android.intent.category.LAUNCHER", "1",
    )
    (session / "launch.txt").write_text(
        launch.stdout + launch.stderr, encoding="utf-8"
    )
    if launch.returncode or "Events injected: 1" not in launch.stdout:
        print(f"Android launch failed; see {session / 'launch.txt'}", file=sys.stderr)
        return 2

    # A process that remains alive for the observation period passes the basic
    # liveness check; it does NOT certify a rendered or interactive UI.
    deadline = time.monotonic() + args.seconds
    observed = False
    exited = False
    while time.monotonic() < deadline:
        pid = adb("shell", "pidof", args.package)
        running = pid.returncode == 0 and bool(pid.stdout.strip())
        if running:
            observed = True
        elif observed:
            exited = True
            break
        time.sleep(0.25)

    logs = adb("logcat", "-d", "-v", "threadtime", "-b", "main",
               "-b", "system", "-b", "crash")
    full_log = logs.stdout + logs.stderr
    (session / "logcat.txt").write_text(full_log, encoding="utf-8")
    if logs.returncode:
        print(f"Could not read logcat; see {session / 'logcat.txt'}", file=sys.stderr)
        return 2

    findings = classify_android_startup(full_log)
    report = [
        f"Package: {args.package}",
        f"Observed process: {observed}",
        f"Exited during observation: {exited}",
        f"Findings: {', '.join(f.code for f in findings) or 'none'}",
    ]
    report += [f"  {f.code}: {f.description}" for f in findings]
    (session / "summary.txt").write_text(
        "\n".join(report) + "\n", encoding="utf-8"
    )
    print("\n".join(report))
    print(f"Saved diagnostic session: {session}")

    if findings or exited or not observed:
        print("FAIL: Android startup is not yet certified", file=sys.stderr)
        return 1
    print("PASS: process stayed alive; visual UI still needs manual certification")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
