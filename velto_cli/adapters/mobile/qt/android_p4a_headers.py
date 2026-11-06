"""Recover Android CPython build headers by rebuilding p4a recipes only.

Never forward --force-build to Qt APK packaging. Recreate the native p4a
distribution using a separate create command after the normal APK build.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys

from velto_cli.adapters.mobile.qt.android_cython import _ndk_root


# Recipes resolved by the PySide6 Qt Android toolchain. This is a fallback
# ONLY when the complete existing Qt distribution can be authenticated by
# its generated native libraries / APK; otherwise fail closed.
_QT_RECIPES = (
    "hostpython3", "libffi", "openssl", "sqlite3", "python3",
    "genericndkbuild", "shiboken6", "pyside6",
)


def _qt_distribution_artifact(distribution: Path) -> bool:
    """Accept a freshly generated Qt ARM64 dist, not an arbitrary folder."""
    native = distribution / "libs/arm64-v8a"
    if not (
        (native / "libmain_arm64-v8a.so").is_file()
        and (native / "libpython3.14.so").is_file()
    ):
        return False
    return (
        any((distribution / "build/outputs/apk").glob("**/*.apk"))
        or (distribution / "src/main/AndroidManifest.xml").is_file()
    )


def _distribution(toolchain_root: Path) -> tuple[Path, Path, dict]:
    platform = toolchain_root / ".buildozer/android/platform"
    roots = sorted(
        (candidate for candidate in platform.glob("build-*/dists/*")
         if candidate.is_dir() and candidate.parent.parent.name == "build-arm64-v8a"),
        key=lambda candidate: candidate.stat().st_mtime_ns,
        reverse=True,
    )
    for distribution in roots:
        # dist_info.json can be missing from a PySide6 Qt distribution whose
        # APK and native libraries were nevertheless successfully generated.
        # Do not depend on metadata being written at one fixed path.
        metadata = distribution / "dist_info.json"
        info = None
        if metadata.is_file():
            try:
                value = json.loads(metadata.read_text(encoding="utf-8"))
                if isinstance(value, dict):
                    info = value
            except (OSError, ValueError):
                pass
        recipes = info.get("recipes") if info else None
        if (
            isinstance(recipes, list)
            and all(isinstance(item, str) and item for item in recipes)
            and {"python3", "pyside6", "shiboken6"}.issubset(recipes)
        ):
            return distribution, distribution.parent.parent, info
        if _qt_distribution_artifact(distribution):
            return (
                distribution, distribution.parent.parent,
                {"recipes": list(_QT_RECIPES)},
            )
    raise RuntimeError(
        "No verified ARM64 Qt python-for-android distribution found in "
        f"{platform}. Expected a generated Qt APK, libmain_arm64-v8a.so "
        "and Android libpython3.14.so. Cannot recover Cython headers."
    )



def _android_sdk_root() -> Path:
    from velto_cli.adapters.mobile.qt.android_final_apk import _android_sdk_tool
    return _android_sdk_tool("zipalign").parent.parent.parent


def _create_command(toolchain_root: Path, ndk_api: int) -> list[str]:
    distribution, storage, info = _distribution(toolchain_root)
    if storage.name != "build-arm64-v8a":
        raise RuntimeError("Cython header recovery supports only arm64-v8a")
    recipes = toolchain_root / "deployment/recipes"
    if not recipes.is_dir():
        raise RuntimeError(
            "PySide6 Qt local recipes missing; cannot safely rebuild p4a"
        )
    return [
        sys.executable, "-m", "pythonforandroid.toolchain", "create",
        "--bootstrap", "qt", "--dist_name", distribution.name,
        "--requirements", ",".join(info["recipes"]),
        "--arch", "arm64-v8a", "--ndk-api", str(ndk_api),
        "--storage-dir", str(storage),
        "--local-recipes", str(recipes),
        "--sdk-dir", str(_android_sdk_root()),
        "--ndk-dir", str(_ndk_root()),
        "--copy-libs", "--force-build",
    ]


def recover_android_python_headers(
    toolchain_root: Path, version: tuple[int, int], *, ndk_api: int = 28,
) -> None:
    """Rebuild p4a distribution only, never Qt APK packaging."""
    command = _create_command(toolchain_root, ndk_api)
    env = os.environ.copy()
    env.pop("APP_P4A_EXTRA_ARGS", None)
    env["ANDROIDNDK"] = str(_ndk_root())
    env["ANDROIDSDK"] = str(_android_sdk_root())
    env["PYTHONFORANDROID_PREREQUISITES_INSTALL_INTERACTIVE"] = "0"
    print(
        "[INFO] Rebuilding Android CPython recipe for Cython headers "
        "(separate p4a create, without Qt APK arguments).",
        flush=True,
    )
    try:
        result = subprocess.run(
            command, env=env, cwd=toolchain_root,
            capture_output=True, text=True, encoding="utf-8",
            errors="replace", check=False,
        )
    except OSError as exc:
        raise RuntimeError("Cannot run p4a native recipe recovery") from exc
    if result.returncode:
        raise RuntimeError(
            f"p4a create failed (exit {result.returncode}): "
            + (result.stderr or result.stdout)[-4000:]
        )
    wanted = f"python{version[0]}.{version[1]}"
    platform = toolchain_root / ".buildozer/android/platform"
    for header in platform.glob(
        f"**/android-build/android-root/include/{wanted}/Python.h"
    ):
        if (header.parent / "pyconfig.h").is_file():
            return
    raise RuntimeError(
        f"p4a create succeeded but Android {wanted} development headers "
        "were not retained. Never use host headers or .pyc."
    )
