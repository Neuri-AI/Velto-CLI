"""Conservative, signed incremental APK builds for PySide6 Android.

Only Python and approved project runtime assets may be overlaid. All other
project/toolchain changes invalidate the cached APK and use the normal
pyside6-android-deploy path. No direct manipulation of installed app data.
"""
from __future__ import annotations

import hashlib
from importlib import metadata
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
from typing import Any
from zipfile import ZipFile
import io
import tarfile

from velto_cli.adapters.mobile.common.android_icons import select_android_project_icon
from velto_cli.adapters.mobile.qt.android_final_apk import (
    finalize_copied_debug_apk, android_private_version_marker,
)
from velto_cli.adapters.mobile.qt.android_sources import (
    PACKAGES, _project_runtime_assets, _source_package,
    verify_qt_android_runtime_assets, verify_velto_apk_sources,
)
from velto_cli.adapters.mobile.qt.android_svg import (
    project_uses_svg, verify_android_svg_apk,
)

_FORMAT = 1
_SKIP = {
    ".git", ".venv", "venv", "__pycache__", ".pytest_cache",
    ".buildozer", "build", ".idea", ".mypy_cache", ".ruff_cache",
    "node_modules",
}
_OUTPUT_SUFFIXES = {".apk", ".aab", ".pyc", ".pyo"}


def _sha_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _cache_root(root: Path) -> Path:
    project_id = hashlib.sha256(
        str(root.resolve()).encode("utf-8")
    ).hexdigest()[:24]
    return Path.home() / ".velto/mobile/android/qt/incremental" / project_id


def _scan(root: Path) -> tuple[dict[str, Path], dict[str, str], str]:
    """Return allowed source files, hashes, and a conservative native key."""
    root = root.resolve()
    sources: dict[str, Path] = {}
    native: dict[str, str] = {}
    assets = _project_runtime_assets(root)
    for base, dirs, names in os.walk(root, followlinks=False):
        parent = Path(base)
        for directory in dirs:
            if (parent / directory).is_symlink():
                raise RuntimeError(
                    "Android incremental mode cannot certify symlink directory: "
                    + str(parent / directory)
                )
        dirs[:] = sorted(d for d in dirs if d not in _SKIP and
                         not (parent / d).is_symlink())
        for name in sorted(names):
            item = parent / name
            rel = item.relative_to(root).as_posix()
            if item.is_symlink():
                # A symlink could reach outside the build tree; no incremental
                # patch is permitted when we cannot prove its provenance.
                raise RuntimeError(
                    "Android incremental mode cannot certify symlink file: "
                    + str(item)
                )
            if not item.is_file() or item.suffix.lower() in _OUTPUT_SUFFIXES:
                continue
            if rel in assets or item.suffix.lower() == ".py":
                sources[rel] = item
            else:
                native[rel] = _sha_file(item)
    # Source framework packages are temporarily staged by the normal build
    # procedure, but not by the fast path. Include their installed contents.
    # All native binaries are excluded by staging, so changing them requires
    # a full build through the package/toolchain key below.
    for package in PACKAGES:
        location = _source_package(package)
        for item in sorted(location.rglob("*")):
            if (not item.is_file() or item.is_symlink()
                    or any(p in _SKIP for p in item.relative_to(location).parts)):
                continue
            rel = f"{package}/{item.relative_to(location).as_posix()}"
            if item.suffix.lower() == ".py":
                if rel in sources:
                    raise RuntimeError("Ambiguous Android Python source: " + rel)
                sources[rel] = item
            elif item.suffix.lower() in (".so", ".dylib", ".pyd", ".dll"):
                native[f"installed:{rel}"] = _sha_file(item)
    keystore = Path.home() / ".android/debug.keystore"
    native["debug-signing-key"] = (
        _sha_file(keystore) if keystore.is_file() else "<not-created>"
    )
    file_hashes = {name: _sha_file(path) for name, path in sorted(sources.items())}
    versions: dict[str, str] = {}
    for package in ("PySide6", "shiboken6", "buildozer", "python-for-android",
                    "velto-cli", "velto-core", "qyro-engine", "velto-cinnamon-core"):
        try:
            versions[package] = metadata.version(package)
        except metadata.PackageNotFoundError:
            versions[package] = "<absent>"
    # Include the source package paths as well, since a venv switch can have
    # identical distribution versions and different native installations.
    installed = {
        package: str(_source_package(package)) for package in PACKAGES
    }
    key_material = json.dumps({
        "files": native,
        "versions": versions,
        "paths": installed,
        "python": sys.version,
        "platform": sys.platform,
        "svg": project_uses_svg(root),
        "packager_implementation": {
            name: _sha_file(Path(__file__).with_name(name))
            for name in (
                "android_incremental.py", "android_final_apk.py",
                "android_sources.py", "android.py",
                "android_svg.py", "android_build_inputs.py",
            )
        },
        "format": _FORMAT,
    }, sort_keys=True).encode("utf-8")
    return sources, file_hashes, hashlib.sha256(key_material).hexdigest()


def _request_key(request: Any) -> str:
    """Changes to native deployment options must invalidate the source cache."""
    wheel_data = {}
    for key in ("wheel_pyside", "wheel_shiboken"):
        wheel = getattr(request, key, None)
        wheel_data[key] = (
            {"path": str(wheel), "sha256": _sha_file(Path(wheel))}
            if wheel is not None else None
        )
    data = {
        "app": request.app_name,
        "version": request.version,
        "entry": request.entry_point,
        "target": request.target.value,
        "framework": request.framework.value,
        "debug": request.debug,
        "wheels": wheel_data,
    }
    return hashlib.sha256(
        json.dumps(data, sort_keys=True).encode("utf-8")
    ).hexdigest()


def _cache_manifest(cache: Path) -> dict[str, Any] | None:
    try:
        data = json.loads((cache / "manifest.json").read_text(encoding="utf-8"))
        if data.get("format") != _FORMAT:
            return None
        if not isinstance(data.get("files"), dict):
            return None
        if not isinstance(data.get("native"), str):
            return None
        if not isinstance(data.get("apk_sha256"), str):
            return None
        if not isinstance(data.get("apk_name"), str):
            return None
        if Path(data["apk_name"]).name != data["apk_name"]:
            return None
        return data
    except (OSError, ValueError, TypeError):
        return None


def _archive_files(apk: Path) -> set[str]:
    with ZipFile(apk) as binary:
        names = [n for n in ("assets/private.tar", "assets/private.mp3")
                 if n in binary.namelist()]
        if len(names) != 1:
            raise RuntimeError("Cached Android APK missing private archive")
        with tarfile.open(fileobj=io.BytesIO(binary.read(names[0])), mode="r:*") as tar:
            return {
                m.name.removeprefix("./").strip("/")
                for m in tar if m.isfile()
            }


def _store(cache: Path, apk: Path, hashes: dict[str, str], native: str) -> None:
    cache.mkdir(parents=True, exist_ok=True)
    # Never store the cache metadata until the entire signed APK has been
    # copied to its permanent location. The cache is immutable on failure.
    with tempfile.TemporaryDirectory(prefix=".velto-cache-", dir=cache) as tmp:
        copied = Path(tmp) / "base.apk"
        shutil.copy2(apk, copied)
        manifest = {
            "format": _FORMAT,
            "native": native,
            "files": hashes,
            "apk_sha256": _sha_file(copied),
            "apk_name": apk.name,
        }
        (Path(tmp) / "manifest.json").write_text(
            json.dumps(manifest, sort_keys=True, indent=2), encoding="utf-8"
        )
        os.replace(copied, cache / "base.apk")
        os.replace(Path(tmp) / "manifest.json", cache / "manifest.json")


def record_full_build(root: Path, apk: Path, request: Any) -> None:
    """Seed the fast path using a successfully produced signed debug APK."""
    if not apk.is_file() or apk.suffix.lower() != ".apk":
        return
    sources, hashes, native = _scan(root)
    contents = _archive_files(apk)
    if not set(sources).issubset(contents):
        # Do not trust an artifact that cannot represent the next source diff.
        return
    with ZipFile(apk) as signed:
        if ("resources.arsc" not in signed.namelist()
                or android_private_version_marker(
                    signed.read("resources.arsc")) is None):
            # Refuse incremental packaging unless p4a extraction invalidation
            # can be updated safely in the compiled Android resources.
            return
    _store(_cache_root(root), apk, hashes, native + _request_key(request))


def try_incremental_build(root: Path, request: Any) -> Path | None:
    """Return a verified APK, or None when a complete build is needed.

    The source/destination APK is NEVER modified if validation or signing
    fails. A native key mismatch intentionally falls back to full rebuild.
    """
    cache = _cache_root(root)
    meta = _cache_manifest(cache)
    previous = cache / "base.apk"
    if meta is None or not previous.is_file():
        return None
    if _sha_file(previous) != meta["apk_sha256"]:
        return None
    sources, hashes, native = _scan(root)
    native += _request_key(request)
    if native != meta["native"]:
        return None
    old = meta["files"]
    additions = set(hashes) - set(old)
    deletions = set(old) - set(hashes)
    changed = {
        name for name in set(hashes) & set(old)
        if hashes[name] != old[name]
    }
    changed.update(additions)
    # Refuse to delete or replace anything not owned by the previous
    # recorded snapshot; never remove standard p4a files or Qt libraries.
    packaged = _archive_files(previous)
    if (set(old) - packaged) or (additions & packaged):
        return None
    with ZipFile(previous) as apk:
        if "resources.arsc" not in apk.namelist():
            return None
        old_marker = android_private_version_marker(
            apk.read("resources.arsc")
        )
        if old_marker is None:
            return None
    # Compile-time p4a private_version must change when the private tarball
    # changes. Otherwise Android keeps the *previous* extracted main.py and
    # resources despite successfully installing the newly signed APK.
    marker = hashlib.sha1(
        old_marker[0] + json.dumps(hashes, sort_keys=True).encode("utf-8")
    ).hexdigest()

    with tempfile.TemporaryDirectory(prefix=".velto-incremental-") as work:
        artifact = Path(work) / meta["apk_name"]
        shutil.copy2(previous, artifact)
        icon = select_android_project_icon(root)
        if changed or deletions:
            finalize_copied_debug_apk(
                artifact, root, icon=icon.source if icon else None,
                updates={name: sources[name] for name in changed},
                removals=frozenset(deletions),
                private_version=marker,
            )
        # Ensure the final artifact includes the *actual source content*,
        # not just filenames, and certify the existing Qt plugin binaries.
        with ZipFile(artifact) as apk:
            private = next(n for n in ("assets/private.tar", "assets/private.mp3")
                           if n in apk.namelist())
            with tarfile.open(fileobj=io.BytesIO(apk.read(private)), mode="r:*") as tar:
                for name in changed:
                    member = tar.extractfile(name)
                    if member is None or hashlib.sha256(member.read()).hexdigest() != hashes[name]:
                        raise RuntimeError("Incremental APK has stale Python/resource: " + name)
                if deletions and deletions & {
                    m.name.removeprefix("./").strip("/") for m in tar if m.isfile()
                }:
                    raise RuntimeError("Incremental APK retains deleted resources")
        verify_velto_apk_sources(artifact)
        verify_qt_android_runtime_assets(artifact, root)
        if project_uses_svg(root):
            verify_android_svg_apk(artifact)
        # Atomically publish a verified, signed APK under the stable output.
        output = root / "build/android/qt"
        output.mkdir(parents=True, exist_ok=True)
        target = output / meta["apk_name"]
        staged = output / (target.name + ".velto-incremental")
        try:
            shutil.copy2(artifact, staged)
            os.replace(staged, target)
        finally:
            staged.unlink(missing_ok=True)
        _store(cache, target, hashes, native)
        return target
