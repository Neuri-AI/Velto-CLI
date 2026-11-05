"""Canonical mobile artifact publication for Qt and Kivy Android builds."""
from pathlib import Path

import pytest

from velto_cli.adapters.mobile.freezer import MobileToolchainFreezer


@pytest.mark.parametrize("backend", ["qt", "kivy"])
@pytest.mark.parametrize("extension", [".apk", ".aab"])
def test_android_artifact_is_published_under_build(
    tmp_path: Path, backend: str, extension: str
):
    source = tmp_path / "dist" / ("Sample-debug" + extension)
    source.parent.mkdir()
    source.write_bytes(b"first build")
    target = MobileToolchainFreezer._publish_android_artifact(
        source, tmp_path, backend=backend
    )
    assert target == (
        tmp_path / "build" / "android" / backend / source.name
    )
    assert target.read_bytes() == b"first build"
    assert source.read_bytes() == b"first build"

    source.write_bytes(b"second build")
    republished = MobileToolchainFreezer._publish_android_artifact(
        source, tmp_path, backend=backend
    )
    assert republished == target
    assert republished.read_bytes() == b"second build"
    assert not list(target.parent.glob(".velto-android-*"))


def test_backend_outputs_do_not_collide(tmp_path: Path):
    first = tmp_path / "qt.apk"
    second = tmp_path / "kivy.apk"
    first.write_bytes(b"Qt")
    second.write_bytes(b"Kivy")
    qt = MobileToolchainFreezer._publish_android_artifact(
        first, tmp_path, backend="qt"
    )
    kivy = MobileToolchainFreezer._publish_android_artifact(
        second, tmp_path, backend="kivy"
    )
    assert qt.read_bytes() == b"Qt"
    assert kivy.read_bytes() == b"Kivy"


@pytest.mark.parametrize("bad", ["desktop", "qt/../../outside", ""])
def test_invalid_backend_is_rejected(tmp_path: Path, bad: str):
    source = tmp_path / "app.apk"
    source.write_bytes(b"APK")
    with pytest.raises(ValueError, match="Unsupported Android"):
        MobileToolchainFreezer._publish_android_artifact(
            source, tmp_path, backend=bad
        )


def test_missing_source_never_creates_published_artifact(tmp_path: Path):
    with pytest.raises(RuntimeError, match="Missing Android"):
        MobileToolchainFreezer._publish_android_artifact(
            tmp_path / "missing.apk", tmp_path, backend="qt"
        )
    assert not (tmp_path / "build/android/qt").exists()
