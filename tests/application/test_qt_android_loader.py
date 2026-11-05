"""Regression tests for the QtLoader Android main-library metadata mismatch."""
from pathlib import Path
from unittest.mock import Mock
import xml.etree.ElementTree as ET

import pytest

from velto_cli.adapters.mobile.freezer import MobileToolchainFreezer


ANDROID_NS = "http://schemas.android.com/apk/res/android"


def generated_distribution(tmp_path: Path, *, lib_name="main", entry=None):
    dist = (
        tmp_path / ".buildozer/android/platform/build-arm64-v8a"
        / "dists/AndroidAppQt"
    )
    resources = dist / "src/main/res/values/libs.xml"
    resources.parent.mkdir(parents=True)
    manifest = dist / "src/main/AndroidManifest.xml"
    manifest.write_text(
        '<manifest xmlns:android="http://schemas.android.com/apk/res/android">'
        '<application><activity><meta-data '
        'android:name="android.app.lib_name" '
        f'android:value="{lib_name}"/>'
        '</activity></application></manifest>',
        encoding="utf-8",
    )
    entry = entry or "arm64-v8a;lib_arm64-v8a.so"
    resources.write_text(
        '<resources><array name="qt_libs">'
        '<item>arm64-v8a;Qt6Core_arm64-v8a</item></array>'
        '<array name="load_local_libs">'
        f'<item>{entry}</item>'
        '<item>arm64-v8a;libshiboken6.abi3.so</item>'
        '</array></resources>',
        encoding="utf-8",
    )
    native = dist / "libs/arm64-v8a/libmain_arm64-v8a.so"
    native.parent.mkdir(parents=True)
    native.write_bytes(b"native-test-fixture")
    artifact = dist / "build/outputs/apk/debug/AndroidAppQt-debug.apk"
    artifact.parent.mkdir(parents=True)
    artifact.write_bytes(b"mock-apk")
    return dist, resources, native, artifact


def test_repairs_only_generated_loader_entry_and_repackages(tmp_path):
    dist, resources, _, artifact = generated_distribution(tmp_path)
    runner = Mock()
    runner.run.return_value = 0
    ui = Mock()
    builder = MobileToolchainFreezer(runner, ui=ui)

    assert builder._repair_qt_android_loader(artifact, debug=True) is True
    root = ET.parse(resources).getroot()
    entries = [i.text for i in root.find("./array[@name='load_local_libs']")]
    assert entries == [
        "arm64-v8a;libmain_arm64-v8a.so",
        "arm64-v8a;libshiboken6.abi3.so",
    ]
    assert root.find("./array[@name='qt_libs']/item").text == (
        "arm64-v8a;Qt6Core_arm64-v8a"
    )
    runner.run.assert_called_once_with(
        [str(dist / "gradlew"), "assembleDebug"], cwd=str(dist)
    )
    assert builder._repair_qt_android_loader(artifact, debug=True) is False
    runner.run.assert_called_once()


def test_loader_repair_respects_manifest_and_already_valid_metadata(tmp_path):
    dist, resources, _, artifact = generated_distribution(tmp_path, lib_name="other")
    runner = Mock()
    builder = MobileToolchainFreezer(runner)
    assert builder._repair_qt_android_loader(artifact, debug=True) is False
    assert "lib_arm64-v8a.so" in resources.read_text()

    _, resources, _, artifact = generated_distribution(
        tmp_path, entry="arm64-v8a;libmain_arm64-v8a.so"
    )
    assert builder._repair_qt_android_loader(artifact, debug=True) is False
    runner.run.assert_not_called()


def test_loader_repair_fails_closed_when_main_library_missing(tmp_path):
    _, resources, native, artifact = generated_distribution(tmp_path)
    native.unlink()
    runner = Mock()
    builder = MobileToolchainFreezer(runner)
    with pytest.raises(RuntimeError, match="expected native library is absent"):
        builder._repair_qt_android_loader(artifact, debug=False)
    assert "lib_arm64-v8a.so" in resources.read_text()
    runner.run.assert_not_called()


def test_loader_repair_does_not_touch_unrelated_artifact(tmp_path):
    runner = Mock()
    artifact = tmp_path / "another.apk"
    artifact.touch()
    assert MobileToolchainFreezer(runner)._repair_qt_android_loader(
        artifact, debug=True
    ) is False
    runner.run.assert_not_called()
