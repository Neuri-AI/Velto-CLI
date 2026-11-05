"""Stage the pure-Python Velto import chain for Qt Android application sources.

Qt's Android wrapper only declares PySide6/shiboken in p4a requirements.
App-level Python sources, unlike native extensions, can be included in the
private application tarball; do not copy host libraries or mutate project
files persistently.
"""
from __future__ import annotations

import ast
from contextlib import contextmanager
from importlib import util
from pathlib import Path
import shutil
import tarfile
from typing import Iterator
from zipfile import BadZipFile, ZipFile


PACKAGES = ("velto", "qyro", "psx")
_NATIVE_SUFFIXES = (".so", ".pyd", ".dll", ".dylib", ".a", ".o", ".pyc")
_SKIP_DIRS = {"__pycache__", ".git", ".pytest_cache", "tests", "test"}


def uses_velto(entry: Path) -> bool:
    """Detect imports without importing Qt or executing the program."""
    try:
        tree = ast.parse(entry.read_text(encoding="utf-8"), filename=str(entry))
    except (OSError, SyntaxError, UnicodeError):
        return False
    for node in ast.walk(tree):
        if isinstance(node, ast.Import) and any(
            a.name == "velto" or a.name.startswith("velto.") for a in node.names
        ):
            return True
        if isinstance(node, ast.ImportFrom) and (
            node.module == "velto" or (node.module or "").startswith("velto.")
        ):
            return True
    return False


def _source_package(name: str) -> Path:
    # find_spec for top-level packages does not import them, unlike import_module.
    spec = util.find_spec(name)
    if spec is None or not spec.submodule_search_locations:
        raise RuntimeError(
            f"Cannot stage Velto Android sources: '{name}' is not installed "
            "in the build environment. Install the Velto runtime packages "
            "(velto, qyro-engine, velto-core) into the host environment first."
        )
    locations = [Path(p) for p in spec.submodule_search_locations]
    if len(locations) != 1 or not (locations[0] / "__init__.py").is_file():
        raise RuntimeError(
            f"Cannot stage ambiguous or non-filesystem Android package '{name}'."
        )
    return locations[0].resolve()


def _excluded(directory: str, names: list[str]) -> set[str]:
    return {
        name for name in names
        if name in _SKIP_DIRS
        or name.endswith(_NATIVE_SUFFIXES)
        or name.endswith((".dist-info", ".egg-info"))
    }


@contextmanager
def staged_velto_sources(root: Path, *, enabled: bool) -> Iterator[bool]:
    """Temporarily include framework sources in the p4a private tarball.

    Refuse to overwrite existing user packages. The source installations are
    read only, the project staging dirs are removed even on build failure,
    and platform-native host binaries are never copied.
    """
    if not enabled:
        yield False
        return
    sources = {name: _source_package(name) for name in PACKAGES}
    collisions = [
        name for name in PACKAGES if (root / name).exists()
    ]
    if collisions:
        raise RuntimeError(
            "Cannot stage Velto Android source packages because the project "
            "already contains: " + ", ".join(collisions)
            + ". Use project-local package resolution instead of overwriting "
            "application files."
        )
    staged: list[Path] = []
    try:
        for name, source in sources.items():
            destination = root / name
            shutil.copytree(
                source, destination, ignore=_excluded, symlinks=False
            )
            staged.append(destination)
        yield True
    finally:
        for destination in reversed(staged):
            shutil.rmtree(destination)


def verify_velto_apk_sources(artifact: Path) -> None:
    """Require the *actual* APK private source archive to contain Velto.

    A folder in the host staging directory or the generated site-packages
    is insufficient evidence of what shipped. p4a stores application source
    in assets/private.tar (some builds name the asset private.mp3).
    """
    if artifact.suffix.lower() not in (".apk", ".aab"):
        raise RuntimeError(
            f"Not an Android APK or AAB for Velto source verification: {artifact}"
        )
    try:
        with ZipFile(artifact) as apk:
            candidates = [
                name for name in apk.namelist()
                if name in (
                    "assets/private.tar", "assets/private.mp3",
                    "base/assets/private.tar", "base/assets/private.mp3",
                )
            ]
            if len(candidates) != 1:
                raise RuntimeError(
                    "Android APK has no unambiguous private application archive."
                )
            import io
            with tarfile.open(fileobj=io.BytesIO(apk.read(candidates[0])), mode="r:*") as archive:
                members = {
                    member.name.lstrip("./")
                    for member in archive.getmembers() if member.isfile()
                }
    except (OSError, ValueError, tarfile.TarError, BadZipFile) as exc:
        raise RuntimeError(
            f"Cannot inspect Android APK private source archive: {exc}"
        ) from exc
    missing = [
        name for name in PACKAGES
        if not any(
            item == f"{name}/__init__.py"
            or item.endswith(f"/{name}/__init__.py")
            for item in members
        )
    ]
    if missing:
        raise RuntimeError(
            "APK private source archive is missing required Velto modules: "
            + ", ".join(missing)
            + ". Qt's deployment omitted the staged Python packages."
        )
