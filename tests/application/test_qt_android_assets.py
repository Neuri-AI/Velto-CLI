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


def test_assets_include_arbitrary_project_paths_and_exclude_build_files(tmp_path):
    from velto_cli.adapters.mobile.qt.android_sources import _project_runtime_assets
    for name in ("translations/es/messages.dat", "templates/page.html", "root.custom",
                 "nested/build/image.dat", "site/.well-known/assetlinks.json", "components/new.py", "settings/base.json",
                 "deployment/recipes/local.py", "build/output.dat", ".hidden/file.dat",
                 "components/.gitkeep", "README.md", "pyproject.toml", "pysidedeploy.spec", "buildozer.spec"):
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("content")
    assets = set(_project_runtime_assets(tmp_path))
    assert {"translations/es/messages.dat", "templates/page.html", "root.custom",
            "nested/build/image.dat", "site/.well-known/assetlinks.json", "components/new.py", "settings/base.json"} <= assets
    assert not assets.intersection({"deployment/recipes/local.py", "build/output.dat",
                                    ".hidden/file.dat", "components/.gitkeep", "README.md", "pyproject.toml",
                                    "pysidedeploy.spec", "buildozer.spec"})


def test_full_packaging_includes_arbitrary_files_without_build_inputs(tmp_path):
    for name, payload in {
        "translations/es/catalog.dat": b"translation",
        "models/data.custom": b"model",
        "components/new.py": b"VALUE = 1\n",
        "deployment/recipes/local.py": b"build only",
        "README.md": b"documentation",
        "pyproject.toml": b"build metadata",
        ".hidden/config": b"hidden",
    }.items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)
    gradle, private, apk = _android_project(tmp_path)
    def rebuild(command, cwd):
        assert cwd == gradle
        _rebuild_from_tar(private, apk)
    added = complete_qt_android_private_assets(tmp_path, apk, rebuild, debug=True)
    assert set(added) == {"translations/es/catalog.dat", "models/data.custom", "components/new.py"}
    with tarfile.open(private, mode="r:*") as archive:
        names = {item.name for item in archive}
    assert not {"deployment", "deployment/recipes/local.py", "README.md", "pyproject.toml", ".hidden/config"} & names
    verify_qt_android_runtime_assets(apk, tmp_path)
