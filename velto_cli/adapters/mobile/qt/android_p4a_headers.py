"""Recover Android CPython build headers by rebuilding p4a recipes only.

Never forward --force-build to Qt APK packaging. Recreate the native p4a
distribution using a separate create command after the normal APK build.
"""
from __future__ import annotations

import json
from configparser import ConfigParser
import shutil
import os
import re
from pathlib import Path
import subprocess
import sys
from zipfile import ZipFile, BadZipFile

from velto_cli.adapters.mobile.qt.android_cython import _ndk_root


# Recipes resolved by the PySide6 Qt Android toolchain. This is a fallback
# ONLY when the complete existing Qt distribution can be authenticated by
# its generated native libraries / APK; otherwise fail closed.
_QT_RECIPES = (
    "hostpython3", "libffi", "openssl", "sqlite3", "python3",
    "genericndkbuild", "shiboken6", "pyside6",
)


def _verified_qt_apk(apk: Path) -> bool:
    """The actual successfully built APK is our authoritative artifact.

    p4a/Qt can remove or relocate both dist_info.json and copied native
    libraries. Checking their presence inside intermediate directories
    causes false failures. The Android APK is the source of truth.
    """
    from zipfile import ZipFile, BadZipFile
    try:
        with ZipFile(apk) as archive:
            names = archive.namelist()
            return (
                any(name.startswith("lib/arm64-v8a/libpython")
                    and name.endswith(".so") for name in names)
                and any(name.startswith("lib/arm64-v8a/libQt6")
                        and name.endswith(".so") for name in names)
                and ("assets/private.tar" in names or
                     "assets/private.mp3" in names)
            )
    except (OSError, BadZipFile):
        return False


def _distribution_name_from_apk(apk: Path) -> str:
    """Read the distribution name from an already-verified Qt APK filename.

    PySide6 produces <name>-<version>-arm64-v8a-debug.apk, while the
    original Gradle output uses <name>-debug.apk.
    """
    name = apk.name
    match = re.fullmatch(
        r"(?P<name>[A-Za-z][A-Za-z0-9_.-]*?)-"
        r"(?:[0-9]+(?:\.[0-9]+)*-arm64-v8a-)?"
        r"(?:debug|release)\.apk",
        name,
    )
    if match is None or not match.group("name"):
        raise RuntimeError(
            f"Cannot determine the Qt p4a distribution name from APK: {name}"
        )
    return match.group("name")


def _distribution(
    toolchain_root: Path, apk: Path | None = None,
) -> tuple[Path, Path, dict]:
    """Select existing p4a dist, or plan a fresh one from a verified APK.

    PySide6 may delete the complete build-arm64-v8a/dists tree after
    packaging. A missing dist is NOT evidence that the earlier APK failed.
    The new dist path is only a command argument to p4a create, not a
    directory that Velto creates or modifies itself.
    """
    platform = toolchain_root / ".buildozer/android/platform"
    storage = platform / "build-arm64-v8a"
    dist_base = storage / "dists"
    if apk is not None and not _verified_qt_apk(apk):
        raise RuntimeError(
            "Cython recovery requires a valid previously generated Qt "
            "APK containing ARM64 CPython, Qt6, and private assets."
        )
    roots = (
        sorted(p for p in dist_base.iterdir() if p.is_dir())
        if dist_base.is_dir() else []
    )
    preferred = (
        dist_base / _distribution_name_from_apk(apk)
        if apk is not None else dist_base / toolchain_root.name
    )
    if preferred.is_dir():
        roots = [preferred]
    elif not roots and apk is not None:
        # Qt cleaned dists/ after finishing the APK. Reconstruct the dist
        # metadata from the verified output, leaving all app files intact.
        return preferred, storage, {"recipes": list(_QT_RECIPES)}
    elif len(roots) != 1:
        raise RuntimeError(
            "Cannot verify a Qt p4a distribution: cannot unambiguously select it. "
            f"Found: {[candidate.name for candidate in roots]}"
        )
    for distribution in roots:
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
            return distribution, storage, info
        if apk is not None:
            return distribution, storage, {"recipes": list(_QT_RECIPES)}
    raise RuntimeError(
        "Cannot verify a Qt p4a distribution. Supply the finished "
        "APK to native header recovery."
    )



def _android_sdk_root() -> Path:
    from velto_cli.adapters.mobile.qt.android_final_apk import _android_sdk_tool
    return _android_sdk_tool("zipalign").parent.parent.parent


def _create_command(
    toolchain_root: Path, ndk_api: int, apk: Path | None = None,
) -> list[str]:
    distribution, storage, info = _distribution(toolchain_root, apk)
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


def _valid_p4a_checkout(checkout: Path) -> bool:
    return (
        (checkout / "pythonforandroid/__init__.py").is_file()
        and (checkout / "pythonforandroid/toolchain.py").is_file()
    )


def _p4a_checkout(toolchain_root: Path) -> Path:
    """Resolve p4a from the existing build, then the original pinned revision.

    PySide6/Buildozer may clean their temporary clone after APK creation.
    Do not assume a pip-installable package exists in the host interpreter.
    """
    platform = toolchain_root / ".buildozer/android/platform"
    spec = toolchain_root / "buildozer.spec"
    config = ConfigParser(interpolation=None)
    if spec.is_file():
        config.read(spec, encoding="utf-8")
    candidates = [
        platform / "python-for-android",
        Path.home() / ".buildozer/android/platform/python-for-android",
    ]
    for variable in ("VELTO_ANDROID_P4A_SOURCE_DIR", "P4A_SOURCE_DIR"):
        if os.environ.get(variable):
            candidates.insert(0, Path(os.environ[variable]).expanduser())
    declared = config.get("app", "p4a.source_dir", fallback="").strip()
    if declared:
        path = Path(declared).expanduser()
        candidates.insert(
            0, path if path.is_absolute() else toolchain_root / path,
        )
    for candidate in candidates:
        if _valid_p4a_checkout(candidate):
            return candidate

    # Buildozer may remove its clone after a successful build. The generated
    # buildozer.spec pins its exact p4a Git commit. Re-create precisely that
    # source revision outside the disposable project, not a floating branch.
    commit = config.get("app", "p4a.commit", fallback="").strip()
    fork = config.get("app", "p4a.fork", fallback="kivy").strip()
    url = config.get("app", "p4a.url", fallback="").strip()
    if not url:
        url = f"https://github.com/{fork}/python-for-android.git"
    if not re.fullmatch(r"[0-9a-fA-F]{7,40}", commit):
        raise RuntimeError(
            "Buildozer removed its python-for-android source checkout and "
            "no usable local checkout was found. Recovery requires the "
            "exact Git commit hash (7-40 hexadecimal characters) in "
            "p4a.commit pinned in the generated "
            f"buildozer.spec at {spec}, or "
            "VELTO_ANDROID_P4A_SOURCE_DIR pointing to that checkout. "
            "Do not install an unpinned p4a version."
        )
    if not re.fullmatch(
        r"https://github\.com/[A-Za-z0-9_.-]+/python-for-android(?:\.git)?",
        url,
    ):
        raise RuntimeError(
            "Cannot recover p4a source from an unexpected Git URL. "
            "Provide a trusted checkout using VELTO_ANDROID_P4A_SOURCE_DIR."
        )
    cache = (
        Path.home() / ".velto/mobile/android/qt/p4a-source" / commit.lower()
    )
    if _valid_p4a_checkout(cache):
        return cache
    cache.parent.mkdir(parents=True, exist_ok=True)
    if cache.exists():
        raise RuntimeError(
            f"Invalid p4a checkout already exists at {cache}; refusing overwrite."
        )
    temporary = cache.parent / (".p4a-" + commit.lower())
    if temporary.exists():
        raise RuntimeError(
            f"Interrupted p4a checkout exists at {temporary}; refusing overwrite."
        )
    try:
        subprocess.run(
            ["git", "clone", "--filter=blob:none", "--no-checkout",
             url, str(temporary)],
            check=True, capture_output=True, text=True,
        )
        subprocess.run(
            ["git", "-C", str(temporary), "checkout", "--detach", commit],
            check=True, capture_output=True, text=True,
        )
        actual = subprocess.run(
            ["git", "-C", str(temporary), "rev-parse", "HEAD"],
            check=True, capture_output=True, text=True,
        ).stdout.strip()
        if not actual.lower().startswith(commit.lower()) or not _valid_p4a_checkout(temporary):
            raise RuntimeError("Downloaded p4a source does not match pinned commit")
        temporary.rename(cache)
    except (OSError, subprocess.CalledProcessError) as exc:
        raise RuntimeError(
            "Unable to restore the pinned p4a checkout from the generated "
            f"buildozer.spec (commit {commit}). "
            "Use VELTO_ANDROID_P4A_SOURCE_DIR for an existing matching checkout."
        ) from exc
    finally:
        if temporary.exists():
            shutil.rmtree(temporary)
    return cache



def recover_android_python_headers(
    toolchain_root: Path, version: tuple[int, int], *, ndk_api: int = 28,
    apk: Path | None = None,
) -> None:
    """Rebuild p4a distribution only, never Qt APK packaging."""
    command = _create_command(toolchain_root, ndk_api, apk)
    checkout = _p4a_checkout(toolchain_root)
    env = os.environ.copy()
    env.pop("APP_P4A_EXTRA_ARGS", None)
    env["PYTHONPATH"] = os.pathsep.join(
        [str(checkout), *([env["PYTHONPATH"]] if env.get("PYTHONPATH") else [])]
    )
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
