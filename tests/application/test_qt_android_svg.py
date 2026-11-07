"""SVG support must be packaged, not just available on desktop."""
import configparser
from pathlib import Path
from zipfile import ZipFile

import pytest

from velto_cli.adapters.mobile.qt.android_svg import (
    project_uses_svg,
    assert_android_svg_wheel,
    configure_android_svg_spec,
    verify_android_svg_apk,
)


def test_svg_assets_require_android_plugin(tmp_path):
    assert not project_uses_svg(tmp_path)
    source = tmp_path / "resources/base/images/logo.svg"
    source.parent.mkdir(parents=True)
    source.write_text("<svg/>")
    assert project_uses_svg(tmp_path)


def test_svg_wheel_preflight(tmp_path):
    wheel = tmp_path / "pyside6-android.whl"
    with ZipFile(wheel, "w") as z:
        z.writestr(
            "PySide6/Qt/plugins/imageformats/libplugins_imageformats_qsvg_arm64-v8a.so",
            b"plugin",
        )
    assert assert_android_svg_wheel(wheel) is None


def test_missing_svg_plugin_fails_before_build(tmp_path):
    wheel = tmp_path / "pyside6-android.whl"
    with ZipFile(wheel, "w") as z:
        z.writestr("PySide6/Qt/plugins/platforms/libqtforandroid.so", b"qt")
    with pytest.raises(RuntimeError, match="SVG imageformats"):
        assert_android_svg_wheel(wheel)


def test_svg_spec_is_additive_and_idempotent(tmp_path):
    spec = tmp_path / "pysidedeploy.spec"
    spec.write_text(
        "[qt]\nmodules = Core,Gui,Widgets\n"
        "[android]\nplugins = platforms\narch = aarch64\n"
        "[buildozer]\nmode = debug\n",
        encoding="utf-8",
    )
    assert configure_android_svg_spec(spec) is True
    config = configparser.ConfigParser()
    config.read(spec)
    assert config["qt"]["modules"] == "Core,Gui,Widgets,Svg"
    assert config["android"]["plugins"] == "platforms,imageformats_qsvg"
    assert config["android"]["arch"] == "aarch64"
    assert config["buildozer"]["mode"] == "debug"
    before = spec.read_bytes()
    assert configure_android_svg_spec(spec) is False
    assert spec.read_bytes() == before


def test_svg_spec_recognizes_qtsvg_already_present(tmp_path):
    spec = tmp_path / "pysidedeploy.spec"
    spec.write_text(
        "[qt]\nmodules = QtSvg\n[android]\nplugins = imageformats_qsvg\n"
    )
    before = spec.read_bytes()
    assert configure_android_svg_spec(spec) is False
    assert spec.read_bytes() == before


def test_svg_apk_verification_checks_native_plugin(tmp_path):
    apk = tmp_path / "app.apk"
    with ZipFile(apk, "w") as z:
        z.writestr(
            "lib/arm64-v8a/libplugins_imageformats_qsvg_arm64-v8a.so", b"elf",
        )
    verify_android_svg_apk(apk)


def test_svg_apk_verification_rejects_missing_plugin(tmp_path):
    apk = tmp_path / "app.apk"
    with ZipFile(apk, "w") as z:
        z.writestr("classes.dex", b"dex")
    with pytest.raises(RuntimeError, match="SVG imageformats plugin"):
        verify_android_svg_apk(apk)


def test_svg_apk_verification_checks_private_bundle(tmp_path):
    import io
    import tarfile
    apk = tmp_path / "app.apk"
    bundle = io.BytesIO()
    with tarfile.open(fileobj=bundle, mode="w:gz") as tar:
        data = b"library"
        entry = tarfile.TarInfo(
            "_python_bundle/site-packages/PySide6/Qt/plugins/"
            "imageformats/libplugins_imageformats_qsvg_arm64-v8a.so"
        )
        entry.size = len(data)
        tar.addfile(entry, io.BytesIO(data))
    with ZipFile(apk, "w") as z:
        z.writestr("assets/private.tar", bundle.getvalue())
    verify_android_svg_apk(apk)


def test_legacy_invalid_imageformats_spec_is_repaired(tmp_path):
    spec = tmp_path / "pysidedeploy.spec"
    spec.write_text(
        "[qt]\nmodules = Core,Gui,Widgets,Svg\n"
        "[android]\nplugins = platforms,imageformats\n"
    )
    assert configure_android_svg_spec(spec)
    parser = configparser.ConfigParser()
    parser.read(spec)
    assert parser["android"]["plugins"] == "platforms,imageformats_qsvg"
    assert not configure_android_svg_spec(spec)
