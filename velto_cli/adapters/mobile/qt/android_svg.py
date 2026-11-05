"""Explicit Qt SVG deployment for native PSX <img src="...svg"> on Android.

QtGui alone does not pull the Qt SVG image format plugin into the APK.
PySide6 Android deploy uses qt.modules and android.plugins (NOT qt.plugins)
from pysidedeploy.spec. Both must be set before invoking the deploy wrapper.
"""
from __future__ import annotations

import configparser
from pathlib import Path
from zipfile import ZipFile


def project_uses_svg(root: Path) -> bool:
    """Detect SVG assets in directories packaged with the Velto application."""
    for relative in ("resources", "images", "assets"):
        directory = root / relative
        if directory.is_dir() and any(directory.rglob("*.svg")):
            return True
    return False


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
    """Add Svg module and imageformats plugin to existing deploy spec.

    Preserve the developer's other module/plugin choices, ensure repeated
    builds are idempotent and never modify unrelated project configuration.
    """
    original = spec.read_text(encoding="utf-8")
    parser = configparser.ConfigParser(interpolation=None)
    parser.optionxform = str
    parser.read_string(original)

    updates = {
        ("qt", "modules"): "Svg",
        ("android", "plugins"): "imageformats",
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
