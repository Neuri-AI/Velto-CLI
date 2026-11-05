"""Qt Android must not report success for a Velto APK with missing imports."""
from pathlib import Path
from unittest.mock import Mock

import pytest

from velto_cli.adapters.mobile.freezer import MobileToolchainFreezer


def android_distribution(tmp_path: Path):
    dist = (
        tmp_path / ".buildozer/android/platform/build-arm64-v8a/dists/Demo"
    )
    bundle = dist / "_python_bundle__arm64-v8a/_python_bundle/site-packages"
    bundle.mkdir(parents=True)
    artifact = dist / "build/outputs/apk/debug/Demo-debug.apk"
    artifact.parent.mkdir(parents=True)
    artifact.write_bytes(b"apk")
    return artifact, bundle


def test_velto_import_missing_bundle_is_an_error(tmp_path):
    artifact, _ = android_distribution(tmp_path)
    entry = tmp_path / "main.py"
    entry.write_text("from velto import VeltoContext\n")
    with pytest.raises(RuntimeError, match="missing velto, qyro, psx"):
        MobileToolchainFreezer(Mock())._verify_qt_android_velto_bundle(
            artifact, entry=entry
        )


def test_velto_import_succeeds_when_packages_present(tmp_path):
    artifact, bundle = android_distribution(tmp_path)
    entry = tmp_path / "main.py"
    entry.write_text("from velto.ui import psx\n")
    for name in ("velto", "qyro", "psx"):
        package = bundle / name
        package.mkdir()
        (package / "__init__.py").write_text("")
    assert MobileToolchainFreezer(Mock())._verify_qt_android_velto_bundle(
        artifact, entry=entry
    ) is None


def test_non_velto_qt_app_is_not_restricted(tmp_path):
    artifact, _ = android_distribution(tmp_path)
    entry = tmp_path / "main.py"
    entry.write_text("from PySide6.QtWidgets import QApplication\n")
    assert MobileToolchainFreezer(Mock())._verify_qt_android_velto_bundle(
        artifact, entry=entry
    ) is None


def test_ast_import_detection(tmp_path):
    entry = tmp_path / "main.py"
    detector = MobileToolchainFreezer._requires_velto_runtime
    entry.write_text("import velto.ui\n")
    assert detector(entry)
    entry.write_text("# from velto import VeltoContext\nprint('ok')\n")
    assert not detector(entry)
