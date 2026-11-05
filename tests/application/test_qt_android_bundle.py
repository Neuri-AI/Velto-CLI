"""Qt APK payload verification for the public Velto import chain."""
import io
import tarfile
from pathlib import Path
from unittest.mock import Mock
from zipfile import ZipFile

import pytest

from velto_cli.adapters.mobile.freezer import MobileToolchainFreezer


def android_distribution(tmp_path: Path, *, packages=()):
    dist = tmp_path / ".buildozer/android/platform/build-arm64-v8a/dists/Demo"
    artifact = dist / "build/outputs/apk/debug/Demo-debug.apk"
    artifact.parent.mkdir(parents=True)
    with ZipFile(artifact, "w") as apk:
        payload = io.BytesIO()
        with tarfile.open(fileobj=payload, mode="w") as bundle:
            for name in packages:
                content = b"# Python source"
                info = tarfile.TarInfo(f"{name}/__init__.py")
                info.size = len(content)
                bundle.addfile(info, io.BytesIO(content))
        apk.writestr("assets/private.tar", payload.getvalue())
    return artifact


def test_velto_import_missing_bundle_is_an_error(tmp_path):
    artifact = android_distribution(tmp_path)
    entry = tmp_path / "main.py"
    entry.write_text("from velto import VeltoContext\n")
    with pytest.raises(RuntimeError, match="missing required Velto modules: velto, qyro, psx"):
        MobileToolchainFreezer(Mock())._verify_qt_android_velto_bundle(
            artifact, entry=entry
        )


def test_velto_import_succeeds_when_packages_present(tmp_path):
    artifact = android_distribution(tmp_path, packages=("velto", "qyro", "psx"))
    entry = tmp_path / "main.py"
    entry.write_text("from velto.ui import psx\n")
    assert MobileToolchainFreezer(Mock())._verify_qt_android_velto_bundle(
        artifact, entry=entry
    ) is None


def test_non_velto_qt_app_is_not_restricted(tmp_path):
    artifact = android_distribution(tmp_path)
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


def test_gradle_apk_is_preferred_over_project_copy(tmp_path):
    project_copy = tmp_path / "Demo-0.1-arm64-v8a-debug.apk"
    project_copy.write_bytes(b"copy")
    canonical = android_distribution(tmp_path, packages=("velto", "qyro", "psx"))
    selected = MobileToolchainFreezer._qt_gradle_artifact(
        tmp_path, project_copy, debug=True
    )
    assert selected == canonical


def test_gradle_artifact_selection_rejects_missing_canonical_build(tmp_path):
    source = tmp_path / "app.apk"
    source.write_bytes(b"apk")
    with pytest.raises(RuntimeError, match="Gradle artifact not found"):
        MobileToolchainFreezer._qt_gradle_artifact(
            tmp_path, source, debug=True
        )


def test_gradle_debug_apk_selected_when_cli_debug_is_false(tmp_path):
    project_copy = tmp_path / "AndroidAppQt-0.1-arm64-v8a-debug.apk"
    project_copy.write_bytes(b"app copy")
    canonical = android_distribution(
        tmp_path, packages=("velto", "qyro", "psx")
    )
    assert MobileToolchainFreezer._qt_gradle_artifact(
        tmp_path, project_copy, debug=False
    ) == canonical


def test_gradle_release_bundle_selection(tmp_path):
    project_copy = tmp_path / "Demo-release.aab"
    project_copy.write_bytes(b"copy")
    canonical = (
        tmp_path / ".buildozer/android/platform/build-arm64-v8a"
        / "dists/Demo/build/outputs/bundle/release/Demo-release.aab"
    )
    canonical.parent.mkdir(parents=True)
    canonical.write_bytes(b"original")
    assert MobileToolchainFreezer._qt_gradle_artifact(
        tmp_path, project_copy, debug=False
    ) == canonical
