"""Validated local Android wheel overrides shared by deploy and caching."""
from __future__ import annotations

import re
import json
import os
from contextlib import contextmanager
from pathlib import Path
from zipfile import ZipFile

from velto_cli.adapters.mobile.qt.android_build_config import load_android_build_config


def normalized_name(name: str) -> str:
    """Return the PEP 503 spelling used as an android.json map key."""
    return re.sub(r"[-_.]+", "-", name).lower()


def recipe_name(name: str) -> str:
    """p4a recipe names use underscores, unlike distribution names."""
    return normalized_name(name).replace("-", "_")


def local_wheels(root: Path) -> dict[str, Path]:
    """Read every user-declared local wheel, rejecting ambiguous overrides."""
    encoded = os.environ.get("VELTO_ANDROID_LOCAL_WHEELS")
    if encoded is None:
        value = load_android_build_config(root).get("local_wheels", {})
    else:
        try:
            value = json.loads(encoded)
        except json.JSONDecodeError as error:
            raise ValueError("VELTO_ANDROID_LOCAL_WHEELS must contain JSON") from error
    if value is None:
        return {}
    if not isinstance(value, dict) or any(not isinstance(key, str) or not isinstance(path, str)
                                          for key, path in value.items()):
        raise ValueError("android.json local_wheels must map distribution names to wheel paths")
    result: dict[str, Path] = {}
    for configured_name, configured_path in value.items():
        name = normalized_name(configured_name)
        if name in result:
            raise ValueError(f"android.json local_wheels declares {name!r} more than once")
        wheel = Path(configured_path).expanduser()
        if not wheel.is_absolute():
            wheel = root / wheel
        wheel = wheel.resolve()
        if not wheel.is_file() or wheel.suffix.lower() != ".whl":
            raise ValueError(f"android.json local_wheels.{configured_name} must reference an existing .whl")
        result[name] = wheel
    return result


@contextmanager
def local_wheel_overrides(wheels: dict[str, Path]):
    """Make original absolute paths available inside the disposable stage."""
    old = os.environ.get("VELTO_ANDROID_LOCAL_WHEELS")
    try:
        os.environ["VELTO_ANDROID_LOCAL_WHEELS"] = json.dumps(
            {name: str(path) for name, path in wheels.items()}, sort_keys=True
        )
        yield
    finally:
        if old is None:
            os.environ.pop("VELTO_ANDROID_LOCAL_WHEELS", None)
        else:
            os.environ["VELTO_ANDROID_LOCAL_WHEELS"] = old


def wheel_site_package(wheel: Path) -> str:
    """Find the import root p4a should inspect after installing a wheel."""
    with ZipFile(wheel) as archive:
        names = archive.namelist()
        top_level = next((name for name in names if name.endswith(".dist-info/top_level.txt")), None)
        if top_level is not None:
            candidates = [line.strip() for line in archive.read(top_level).decode("utf-8").splitlines()
                          if line.strip() and line.strip().isidentifier()]
            if candidates:
                return candidates[0]
        candidates = sorted({name.split("/", 1)[0] for name in names
                             if "/" in name and not name.startswith((".", "__"))
                             and ".dist-info/" not in name and ".data/" not in name})
    if len(candidates) == 1 and candidates[0].isidentifier():
        return candidates[0]
    raise ValueError(f"Cannot determine the Python package supplied by local wheel: {wheel.name}")
