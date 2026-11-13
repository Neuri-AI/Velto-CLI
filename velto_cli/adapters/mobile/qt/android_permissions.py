"""Map project Android permissions to Qt's generated Buildozer spec."""
from __future__ import annotations

import configparser
from pathlib import Path

from .android_build_config import load_android_build_config


def android_permissions(root: Path, required: tuple[str, ...] = ()) -> tuple[str, ...]:
    """Return user-declared and enabled-feature permissions without duplicates."""
    configured = load_android_build_config(root).get("permissions", [])
    names: dict[str, str] = {}
    for permission in (*configured, *required):
        full_name = (permission if "." in permission
                     else f"android.permission.{permission}")
        names.setdefault(full_name, full_name)
    return tuple(names.values())


def configure_android_permissions(root: Path, required: tuple[str, ...] = ()) -> bool:
    """Write the authoritative permissions to the generated Buildozer spec."""
    spec = root / "buildozer.spec"
    parser = configparser.ConfigParser(interpolation=None)
    parser.read(spec, encoding="utf-8")
    if not parser.has_section("app"):
        raise RuntimeError("Qt did not generate buildozer.spec with an [app] section")
    value = ",".join(android_permissions(root, required))
    if (parser.get("app", "android.permissions", fallback="") == value
            and (value or not parser.has_option("app", "android.permissions"))):
        return False
    if value:
        parser.set("app", "android.permissions", value)
    else:
        parser.remove_option("app", "android.permissions")
    with spec.open("w", encoding="utf-8") as stream:
        parser.write(stream)
    return True
