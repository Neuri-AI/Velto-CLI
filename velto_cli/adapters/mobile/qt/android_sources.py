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
from pathlib import Path, PurePosixPath
import shutil
import tempfile
import tarfile
from typing import Callable, Iterator
from zipfile import BadZipFile, ZipFile


from velto_cli.adapters.mobile.qt.android_build_config import (
    android_exclusion_patterns, is_android_excluded,
)

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

# Runtime files that must retain their normal project-relative locations.
# Never implicitly ship encrypted secrets, signing material, dotfiles or keys.
_ASSET_DIRS = frozenset({
    "settings", "resources", "images", "css", "assets", "fonts",
    "components", "views",
})
_ASSET_SUFFIXES = frozenset({
    ".json", ".css", ".svg", ".png", ".jpg", ".jpeg", ".gif", ".webp",
    ".ico", ".icns", ".ttf", ".otf", ".woff", ".woff2", ".qss",
    ".qml", ".kv", ".atlas", ".xml", ".html", ".js", ".txt",
})
_ASSET_FORBIDDEN_PARTS = frozenset({
    ".velto", ".git", ".venv", ".buildozer", "__pycache__",
    "node_modules", "tests", "test", "sign", "signing", "private",
})
_ASSET_FORBIDDEN_STEMS = frozenset({
    "secrets", "secret", ".env", "credentials", "credential",
    "keystore", "key", "password", "sign",
})


def _project_runtime_assets(root: Path) -> dict[str, Path]:
    """Discover non-executable application assets, excluding sensitive files."""
    assets: dict[str, Path] = {}
    exclusions = android_exclusion_patterns(root)
    for directory in sorted(_ASSET_DIRS):
        base = root / directory
        if not base.is_dir() or base.is_symlink():
            continue
        for item in base.rglob("*"):
            if not item.is_file() or item.is_symlink():
                continue
            rel = item.relative_to(root)
            if is_android_excluded(rel.as_posix(), exclusions):
                continue
            if any(part.startswith(".") or part.lower() in _ASSET_FORBIDDEN_PARTS
                   for part in rel.parts):
                continue
            if (item.suffix.lower() not in _ASSET_SUFFIXES
                    or item.stem.lower() in _ASSET_FORBIDDEN_STEMS):
                continue
            assets[rel.as_posix()] = item
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

