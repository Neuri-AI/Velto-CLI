"""Qt Android must ship settings and project resources, not only Python modules."""
import io
import tarfile
from pathlib import Path
from zipfile import ZipFile

from velto_cli.adapters.mobile.qt.android_sources import (
    complete_qt_android_private_assets,
    verify_qt_android_runtime_assets,
)


def _android_project(tmp_path):
    gradle = (
        tmp_path / ".buildozer/android/platform/build-arm64-v8a"
        / "dists/AndroidAppQt"
    )
    (gradle / "src/main/assets").mkdir(parents=True)
    (gradle / "gradlew").write_text("#!/bin/sh\\n")
    app_tar = gradle / "src/main/assets/private.tar"
    with tarfile.open(app_tar, "w") as archive:
        info = tarfile.TarInfo("main.py")
        data = b"print('hello')"
        info.size = len(data)
        archive.addfile(info, io.BytesIO(data))
    apk = gradle / "build/outputs/apk/debug/app-debug.apk"
    apk.parent.mkdir(parents=True)
    with ZipFile(apk, "w") as archive:
        archive.write(app_tar, "assets/private.tar")
    return gradle, app_tar, apk


def _rebuild_from_tar(private, apk):
    with ZipFile(apk, "w") as archive:
        archive.write(private, "assets/private.tar")


def test_settings_and_resources_reach_real_apk(tmp_path):
    root = tmp_path
    (root / "settings").mkdir()
    (root / "settings/base.json").write_text('{"framework": "PySide6"}')
    (root / "settings/secrets.json").write_text('{"token": "do not ship"}')
    (root / "settings/sign.json").write_text('{"password": "private"}')
    (root / "resources/base/icons").mkdir(parents=True)
    (root / "resources/base/icons/app.svg").write_text("<svg/>")
    (root / "css").mkdir()
    (root / "css/styles.css").write_text(".root { color: red; }")
    (root / "images").mkdir()
    (root / "images/banner.png").write_bytes(b"image")
    gradle, private, apk = _android_project(root)
    calls = []

    def fake_run(command, cwd):
        calls.append((command, cwd))
        assert cwd == gradle
        assert command == [str(gradle / "gradlew"), "assembleDebug"]
        _rebuild_from_tar(private, apk)

    added = complete_qt_android_private_assets(
        root, apk, fake_run, debug=True
    )
    assert set(added) == {
        "settings/base.json", "resources/base/icons/app.svg",
        "css/styles.css", "images/banner.png",
    }
    assert len(calls) == 1
    verify_qt_android_runtime_assets(apk, root)
    with tarfile.open(private) as tar:
        names = {m.name for m in tar.getmembers()}
        assert "settings/secrets.json" not in names
        assert "settings/sign.json" not in names
        assert "settings/base.json" in names

    # Rebuild is not repeated when everything is present.
    assert complete_qt_android_private_assets(
        root, apk, fake_run, debug=True
    ) == ()
    assert len(calls) == 1


def test_missing_configuration_is_detected(tmp_path):
    (tmp_path / "settings").mkdir()
    (tmp_path / "settings/base.json").write_text('{"framework": "PySide6"}')
    _, _, apk = _android_project(tmp_path)
    import pytest
    with pytest.raises(RuntimeError, match="settings/base.json"):
        verify_qt_android_runtime_assets(apk, tmp_path)


def test_no_resources_means_no_gradle_rebuild(tmp_path):
    _, _, apk = _android_project(tmp_path)

    def should_not_run(*args):
        raise AssertionError("Unexpected Gradle rebuild")

    assert complete_qt_android_private_assets(
        tmp_path, apk, should_not_run, debug=True
    ) == ()
