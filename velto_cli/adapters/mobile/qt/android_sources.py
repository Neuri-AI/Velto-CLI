"""Inspect Android application sources, installed packages, and assets."""
from __future__ import annotations

import ast
import os
import shutil
from pathlib import Path
import tempfile
import tarfile
from typing import Callable
from zipfile import BadZipFile, ZipFile


from velto_cli.adapters.mobile.qt.android_build_config import (
    ANDROID_TOOL_DIRS, android_exclusion_patterns, is_android_excluded, is_android_project_payload,
)

PACKAGES = ("velto", "qyro", "psx")


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


def project_uses_velto(root: Path, entry: Path) -> bool:
    """Recognize declared Velto projects even when imports live in modules."""
    if uses_velto(entry):
        return True
    project = root / "pyproject.toml"
    if not project.is_file():
        return False
    from packaging.requirements import Requirement
    from velto_cli.adapters.mobile.qt.android_requirements import tomllib
    try:
        with project.open("rb") as stream:
            dependencies = tomllib.load(stream).get("project", {}).get("dependencies", [])
        return any(Requirement(item).name.lower().replace("_", "-") == "velto"
                   for item in dependencies)
    except (OSError, ValueError, TypeError):
        return False


def verify_velto_apk_sources(artifact: Path, root: Path | None = None) -> None:
    """Check the installed distributions in the actual Android Python bundle."""
    import io
    try:
        with ZipFile(artifact) as apk:
            bundles = [name for name in apk.namelist() if name.endswith('/libpybundle.so')]
            if not bundles:
                raise RuntimeError("Android artifact has no Python distribution bundle")
            for bundle in bundles:
                with tarfile.open(fileobj=io.BytesIO(apk.read(bundle)), mode="r:*") as archive:
                    members = {item.name.lstrip('./') for item in archive if item.isfile()}
                missing = [name for name in PACKAGES if not any(
                    item.endswith(f"/site-packages/{name}/__init__.py")
                    or item.endswith(f"/site-packages/{name}/__init__.pyc")
                    for item in members
                )]
                if missing:
                    raise RuntimeError("Android Python bundle is missing required Velto modules: " + ", ".join(missing))
                for distribution in ("velto", "qyro_engine", "velto_core",
                                     "velto_cinnamon_core", "resvg_py"):
                    prefix = f"/site-packages/{distribution}-"
                    if not any(prefix in item.lower() and item.endswith('.dist-info/METADATA') for item in members):
                        raise RuntimeError(f"Android Python bundle lacks installed distribution metadata: {distribution}")
                if not any('/site-packages/_sfle_native/' in item and item.endswith('.so')
                           for item in members):
                    raise RuntimeError("Android Python bundle is missing Cinnamon's native library")
    except (OSError, ValueError, tarfile.TarError, BadZipFile) as exc:
        raise RuntimeError(f"Cannot inspect Android Python bundle: {exc}") from exc

def _project_runtime_assets(root: Path) -> dict[str, Path]:
    """Collect project payload from every directory, preserving relative paths."""
    from velto_cli.adapters.mobile.qt.android_build_inputs import android_root_inspection_dirs

    assets: dict[str, Path] = {}
    exclusions = android_exclusion_patterns(root)
    inspection = {path.name for path in android_root_inspection_dirs(root)}
    for base, dirs, files in os.walk(root, followlinks=False):
        parent = Path(base)
        relative_parent = parent.relative_to(root)
        dirs[:] = sorted(
            name for name in dirs
            if not (parent / name).is_symlink()
            and name not in ANDROID_TOOL_DIRS
            and not (parent == root and name.startswith('.'))
            and not (parent == root and (name.lower() in {'build', 'deployment', 'dist', 'bin', 'release'}
                                          or name in inspection))
            and not is_android_excluded((relative_parent / name).as_posix(), exclusions)
        )
        for name in sorted(files):
            item = parent / name
            if item.is_symlink() or not item.is_file():
                continue
            relative = item.relative_to(root).as_posix()
            if is_android_project_payload(relative, exclusions):
                assets[relative] = item
    return assets


def _gradle_directory(artifact: Path) -> Path | None:
    """Find the p4a distribution by its dists/<name> path, not gradlew.

    p4a may use a gradlew command from PATH, so absence of the wrapper
    inside a distribution is not evidence that the distribution is absent.
    """
    for directory in artifact.parents:
        if (directory.parent.name == "dists"
                and (directory / "src/main/assets").is_dir()):
            return directory
    return None


def _gradle_command(directory: Path) -> str:
    wrapper = directory / "gradlew"
    if wrapper.is_file():
        return str(wrapper)
    installed = shutil.which("gradlew")
    if installed is not None:
        return installed
    raise RuntimeError(
        f"Qt Android Gradle wrapper is unavailable for distribution: {directory}"
    )


def complete_qt_android_private_assets(
    root: Path, artifact: Path, run: Callable[[list[str], Path], None],
    *, debug: bool,
) -> tuple[str, ...]:
    """Complete the p4a private tar and rebuild with Gradle, retaining signatures.

    PySide6's generated Buildozer spec may omit JSON, CSS and SVG files.
    This runs *after* Qt/Buildozer generation, so wrapper regenerations do
    not silently undo the inclusion policy. Never patch a signed APK in place.
    """
    assets = _project_runtime_assets(root)
    if not assets:
        return ()
    gradle = _gradle_directory(artifact)
    if gradle is None:
        raise RuntimeError(
            "Cannot locate Qt Android Gradle project to include runtime assets."
        )
    asset_dir = gradle / "src/main/assets"
    archives = [
        path for name in ("private.tar", "private.mp3")
        if (path := asset_dir / name).is_file()
    ]
    if len(archives) != 1:
        raise RuntimeError(
            "Cannot locate an unambiguous Qt Android private application tar "
            "under the generated Gradle project's src/main/assets."
        )
    private = archives[0]
    with tarfile.open(private, "r:*") as archive:
        present = {
            member.name.removeprefix("./").strip("/")
            for member in archive.getmembers() if member.isfile()
        }
    missing = {name: path for name, path in assets.items() if name not in present}
    # Match the copied-APK finalizer: p4a requires gzip USTAR and explicit
    # parent directory entries, including for nested settings/resources.
    # Import lazily to avoid the android_final_apk -> android_sources cycle.
    from velto_cli.adapters.mobile.qt.android_final_apk import (
        _amended_private_tar,
        _missing_parent_dirs,
    )
    with tarfile.open(private, "r:*") as archive:
        missing_parents = _missing_parent_dirs(archive)
    if not missing and not missing_parents:
        return ()
    with tempfile.NamedTemporaryFile(
        prefix=".velto-private-", suffix=".tar", dir=asset_dir, delete=False
    ) as temp:
        staged = Path(temp.name)
    try:
        staged.write_bytes(_amended_private_tar(private.read_bytes(), missing))
        staged.replace(private)
    finally:
        staged.unlink(missing_ok=True)

    run([_gradle_command(gradle),
         "assembleDebug" if debug else "bundleRelease"], gradle)
    return tuple(sorted(missing))


def verify_qt_android_runtime_assets(artifact: Path, root: Path) -> None:
    """Fail an Android build if any allowed project runtime asset is absent."""
    expected = _project_runtime_assets(root)
    if not expected:
        return
    import io
    try:
        with ZipFile(artifact) as apk:
            names = [
                name for name in apk.namelist()
                if name in ("assets/private.tar", "assets/private.mp3",
                            "base/assets/private.tar", "base/assets/private.mp3")
            ]
            if len(names) != 1:
                raise RuntimeError("APK has no unambiguous private application archive")
            with tarfile.open(fileobj=io.BytesIO(apk.read(names[0])), mode="r:*") as tar:
                actual = {
                    m.name.removeprefix("./").strip("/")
                    for m in tar.getmembers() if m.isfile()
                }
    except (OSError, ValueError, BadZipFile, tarfile.TarError) as exc:
        raise RuntimeError(f"Cannot verify Android app resources: {exc}") from exc
    missing = sorted(set(expected) - actual)
    if missing:
        raise RuntimeError(
            "Android APK is missing required Velto runtime assets: "
            + ", ".join(missing[:20])
        )

