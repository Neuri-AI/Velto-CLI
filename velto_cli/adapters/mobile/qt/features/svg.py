"""Qt SVG deployment feature for native PSX <img src="...svg"> on Android.

QtGui alone does not pull the Qt SVG image format plugin into the APK.
PySide6 Android deploy uses qt.modules and android.plugins (NOT qt.plugins)
from pysidedeploy.spec. Both must be set before invoking the deploy wrapper.
"""
from __future__ import annotations

import configparser
import io
import tarfile
from pathlib import Path
from zipfile import ZipFile


def project_uses_svg(root: Path) -> bool:
    """Detect SVG assets anywhere in the packaged project."""
    from velto_cli.adapters.mobile.qt.android_sources import _project_runtime_assets
    return any(path.suffix.lower() == ".svg" for path in _project_runtime_assets(root).values())


def assert_android_svg_wheel(wheel: Path) -> None:
    """Fail before a lengthy Android build when the wheel cannot supply SVG."""
    with ZipFile(wheel) as archive:
        names = [name.lower() for name in archive.namelist()]
    plugin = [
        name for name in names
        if "/plugins/imageformats/" in name
        and "qsvg" in Path(name).name
        and name.endswith(".so")
    ]
    if not plugin:
        raise RuntimeError(
            "PySide6 Android wheel has no Qt SVG imageformats (qsvg) plugin. "
            "Velto cannot render project SVGs with QPixmap on Android. "
            f"Wheel checked: {wheel}. Supply a compatible Android wheel "
            "with plugins/imageformats/libplugins_imageformats_qsvg_*.so."
        )


def configure_android_svg_spec(spec: Path) -> bool:
    """Add Svg module and imageformats_qsvg plugin to existing deploy spec.

    Preserve the developer's other module/plugin choices, ensure repeated
    builds are idempotent and never modify unrelated project configuration.
    """
    original = spec.read_text(encoding="utf-8")
    parser = configparser.ConfigParser(interpolation=None)
    parser.optionxform = str
    parser.read_string(original)

    updates = {
        ("qt", "modules"): "Svg",
        ("android", "plugins"): "imageformats_qsvg",
    }
    changed = False
    for (section, field), required in updates.items():
        if not parser.has_section(section):
            parser.add_section(section)
        existing = [
            item.strip() for item in parser.get(
                section, field, fallback="",
            ).split(",") if item.strip()
        ]
        if section == "android" and any(item.lower() == "imageformats" for item in existing):
            raise ValueError("Invalid Android plugin 'imageformats'; specify 'imageformats_qsvg'")
        if required.lower() not in {item.lower().removeprefix("qt") if section == "qt" else item.lower() for item in existing}:
            existing.append(required)
            parser.set(section, field, ",".join(existing))
            changed = True
    if not changed:
        return False

    # Rewrite the explicit config once, then leave it unchanged on later
    # builds. configparser preserves unrelated values and all sections.
    from io import StringIO
    buffer = StringIO()
    parser.write(buffer)
    spec.write_text(buffer.getvalue(), encoding="utf-8")
    return True


def verify_android_svg_apk(artifact: Path) -> None:
    """Do not publish a Velto SVG app without its Android Qt image plugin.

    The platform may package Qt plugins as native APK libraries or inside
    the private Python archive. Check both, rather than assuming that
    declaring Svg in the deploy spec was sufficient.
    """
    if artifact.suffix.lower() != ".apk":
        return  # AAB has a different layout; verified by the bundle toolchain.
    with ZipFile(artifact) as apk:
        entries = list(apk.namelist())
        for name in entries:
            if "qsvg" in name.lower() and name.endswith(".so"):
                return
        for private in ("assets/private.tar", "assets/private.mp3"):
            if private not in entries:
                continue
            with tarfile.open(
                fileobj=io.BytesIO(apk.read(private)), mode="r:*"
            ) as payload:
                if any(
                    "qsvg" in member.name.lower()
                    and member.name.endswith(".so")
                    for member in payload.getmembers()
                ):
                    return
    raise RuntimeError(
        "Android APK lacks Qt's SVG imageformats plugin (qsvg). "
        "The selected PySide6 wheel includes SVG, but the Qt Android deploy "
        "did not package it. Check [qt] modules=Svg and "
        "[android] plugins=imageformats_qsvg in pysidedeploy.spec."
    )
