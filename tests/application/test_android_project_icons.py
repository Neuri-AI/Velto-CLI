"""Project-owned launcher icon discovery for both Android backends."""
from pathlib import Path
from unittest.mock import Mock

import pytest

from velto_cli.adapters.mobile.common.android_icons import (
    configured_kivy_android_icon,
    select_android_project_icon,
)
from velto_cli.adapters.mobile.freezer import MobileToolchainFreezer


def _icon(root: Path, folder: str, name: str, data: bytes = b"PNG") -> Path:
    path = root / folder / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def test_linux_512_png_takes_precedence_over_mac_and_base(tmp_path):
    expected = _icon(tmp_path, "resources/linux/icons", "512.png")
    _icon(tmp_path, "resources/mac/icons", "1024.png")
    _icon(tmp_path, "resources/base/icons", "64.png")
    selected = select_android_project_icon(tmp_path)
    assert selected is not None
    assert selected.source == expected
    assert selected.folder == "resources/linux/icons"
    assert selected.relative_path == "resources/linux/icons/512.png"


def test_mac_high_resolution_is_fallback(tmp_path):
    _icon(tmp_path, "resources/base/icons", "64.png")
    expected = _icon(tmp_path, "resources/mac/icons", "512.png")
    selected = select_android_project_icon(tmp_path)
    assert selected is not None
    assert selected.source == expected
    assert selected.folder == "resources/mac/icons"


def test_base_png_is_used_when_no_high_resolution_icon(tmp_path):
    expected = _icon(tmp_path, "resources/base/icons", "64.png")
    _icon(tmp_path, "resources/base/icons", "Icon.ico")
    selected = select_android_project_icon(tmp_path)
    assert selected is not None
    assert selected.source == expected
    assert selected.folder == "resources/base/icons"


def test_no_icon_keeps_toolchain_default(tmp_path):
    assert select_android_project_icon(tmp_path) is None


def test_kivy_icon_configuration_is_temporary(tmp_path):
    icon = _icon(tmp_path, "resources/linux/icons", "512.png")
    selected = select_android_project_icon(tmp_path)
    spec = tmp_path / "buildozer.spec"
    original = "[app]\nname = test\n\n[buildozer]\nlog_level = 2\n"
    spec.write_text(original)
    with configured_kivy_android_icon(spec, selected):
        assert "icon.filename = resources/linux/icons/512.png" in spec.read_text()
        assert icon.is_file()
    assert spec.read_text() == original


def test_kivy_icon_restored_after_failed_build(tmp_path):
    _icon(tmp_path, "resources/linux/icons", "512.png")
    spec = tmp_path / "buildozer.spec"
    original = "[app]\nicon.filename = original.png\n"
    spec.write_text(original)
    with pytest.raises(RuntimeError, match="build failed"):
        with configured_kivy_android_icon(spec, select_android_project_icon(tmp_path)):
            assert "icon.filename = resources/linux/icons/512.png" in spec.read_text()
            raise RuntimeError("build failed")
    assert spec.read_text() == original


def test_qt_launcher_icon_replaces_generated_mipmap_before_build(tmp_path):
    _icon(tmp_path, "resources/linux/icons", "512.png", b"new")
    icon = select_android_project_icon(tmp_path)
    gradle = tmp_path / ".buildozer/android/platform/build-arm64/dists/App"
    gradle.mkdir(parents=True)
    (gradle / "gradlew").write_text("#!/bin/sh\n")
    mipmap = gradle / "src/main/res/mipmap"
    mipmap.mkdir(parents=True)
    target = mipmap / "icon.png"
    target.write_bytes(b"toolchain default")
    apk = gradle / "build/outputs/apk/debug/app.apk"
    apk.parent.mkdir(parents=True)
    apk.write_bytes(b"placeholder")
    assert icon is not None
    assert MobileToolchainFreezer._apply_qt_android_icon(apk, icon.source)
    assert target.read_bytes() == b"new"
    assert not MobileToolchainFreezer._apply_qt_android_icon(apk, icon.source)
