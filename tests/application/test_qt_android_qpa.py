"""Regression tests for eager Qt Android QPA JNI library packaging."""
import xml.etree.ElementTree as ET
from pathlib import Path
from unittest.mock import Mock

import pytest

from velto_cli.adapters.mobile.freezer import MobileToolchainFreezer


NAME = "libplugins_platforms_qtforandroid_arm64-v8a.so"


def distribution(tmp_path: Path, *, include_plugin=True, entry=None):
    dist = tmp_path / ".buildozer/android/platform/build-arm64-v8a/dists/App"
    manifest = dist / "src/main/AndroidManifest.xml"
    manifest.parent.mkdir(parents=True)
    manifest.write_text(
        '<manifest xmlns:android="http://schemas.android.com/apk/res/android">'
        '<application><activity><meta-data android:name="android.app.lib_name" '
        'android:value="main"/></activity></application></manifest>'
    )
    resources = dist / "src/main/res/values/libs.xml"
    resources.parent.mkdir(parents=True)
    extra = f"<item>arm64-v8a;{NAME}</item>" if entry else ""
    resources.write_text(
        '<resources><array name="load_local_libs">'
        '<item>arm64-v8a;libmain_arm64-v8a.so</item>'
        + extra
        + '<item>arm64-v8a;libshiboken6.abi3.so</item>'
        '</array></resources>'
    )
    bundle = (
        dist / "_python_bundle__arm64-v8a/_python_bundle/site-packages/"
        "PySide6/Qt/plugins/platforms"
    )
    bundle.mkdir(parents=True)
    if include_plugin:
        (bundle / NAME).write_bytes(b"QPA-native-plugin")
    artifact = dist / "build/outputs/apk/debug/App-debug.apk"
    artifact.parent.mkdir(parents=True)
    artifact.write_bytes(b"fixture")
    return dist, resources, artifact


def test_qpa_plugin_is_copied_and_loaded_before_main(tmp_path):
    dist, resources, artifact = distribution(tmp_path)
    runner = Mock()
    builder = MobileToolchainFreezer(runner)
    assert builder._repair_qt_android_qpa(artifact, debug=True) is True
    assert (dist / "libs/arm64-v8a" / NAME).read_bytes() == b"QPA-native-plugin"
    entries = [
        node.text for node in ET.parse(resources).getroot().find(
            "./array[@name='load_local_libs']"
        )
    ]
    assert entries == [
        f"arm64-v8a;{NAME}",
        "arm64-v8a;libmain_arm64-v8a.so",
        "arm64-v8a;libshiboken6.abi3.so",
    ]
    runner.run.assert_called_once_with(
        [str(dist / "gradlew"), "assembleDebug"], cwd=str(dist)
    )
    assert builder._repair_qt_android_qpa(artifact, debug=True) is False
    runner.run.assert_called_once()


def test_qpa_plugin_missing_fails_with_actionable_error(tmp_path):
    _, _, artifact = distribution(tmp_path, include_plugin=False)
    builder = MobileToolchainFreezer(Mock())
    with pytest.raises(RuntimeError, match="QPA plugin is missing"):
        builder._repair_qt_android_qpa(artifact, debug=True)


def test_qpa_existing_entry_is_reordered(tmp_path):
    dist, resources, artifact = distribution(tmp_path, entry=True)
    (dist / "libs/arm64-v8a").mkdir(parents=True)
    (dist / "libs/arm64-v8a" / NAME).write_bytes(b"QPA-native-plugin")
    runner = Mock()
    assert MobileToolchainFreezer(runner)._repair_qt_android_qpa(
        artifact, debug=False
    )
    entries = [
        node.text for node in ET.parse(resources).getroot().find(
            "./array[@name='load_local_libs']"
        )
    ]
    assert entries[0] == f"arm64-v8a;{NAME}"
    runner.run.assert_called_once_with(
        [str(dist / "gradlew"), "bundleRelease"], cwd=str(dist)
    )


def test_qpa_unrelated_artifact_is_ignored(tmp_path):
    artifact = tmp_path / "app.apk"
    artifact.write_bytes(b"apk")
    runner = Mock()
    assert MobileToolchainFreezer(runner)._repair_qt_android_qpa(
        artifact, debug=True
    ) is False
    runner.run.assert_not_called()
