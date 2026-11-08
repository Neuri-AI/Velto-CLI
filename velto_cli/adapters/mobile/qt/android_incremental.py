"""Conservative, signed incremental APK builds for PySide6 Android.

Only Python and approved project runtime assets may be overlaid. All other
project/toolchain changes invalidate the cached APK and use the normal
pyside6-android-deploy path. No direct manipulation of installed app data.
"""
from __future__ import annotations

import hashlib
import ast
import configparser
from importlib import metadata
import json
import os
import re
from pathlib import Path
import shutil
import sys
import tempfile
from typing import Any
from zipfile import ZipFile
import io
import tarfile

from velto_cli.adapters.mobile.common.android_icons import select_android_project_icon
from velto_cli.adapters.mobile.qt.android_build_inputs import android_root_inspection_dirs
from velto_cli.adapters.mobile.qt.android_build_config import (
    ANDROID_BUILD_FILES, ANDROID_TOOL_DIRS, android_exclusion_patterns, is_android_excluded,
    is_android_project_directory, is_android_project_payload, verify_android_exclusions,
)
from velto_cli.adapters.mobile.qt.android_final_apk import (
    finalize_copied_debug_apk, android_private_version_marker,
)
from velto_cli.adapters.mobile.qt.android_sources import (
    PACKAGES, _project_runtime_assets,
    verify_qt_android_runtime_assets, verify_velto_apk_sources,
)
from velto_cli.adapters.mobile.qt.android_svg import (
    project_uses_svg, verify_android_svg_apk,
)
from velto_cli.adapters.mobile.qt.android_release import android_protected_resource_prefixes

# Version 5 moves Android splash readiness into the Qt Java surface.  Older
# cached APKs depended on a Python JNI extension that is not portable across
# every PySide6 Android runtime.
_FORMAT = 5
_ROOT_OUTPUT_DIRS = {"build", "dist", "bin", "release"}
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
    protected_resource_prefixes = android_protected_resource_prefixes(root)
    exclusions = android_exclusion_patterns(root)
    ignored_root_dirs = {entry.name for entry in android_root_inspection_dirs(root)}
    for base, dirs, names in os.walk(root, followlinks=False):
        parent = Path(base)
        kept = []
        for directory in sorted(dirs):
            rel = (parent / directory).relative_to(root).as_posix()
            if (directory in ANDROID_TOOL_DIRS or (parent == root and directory.startswith('.'))
                    or is_android_excluded(rel, exclusions)
                    or (parent == root and directory.lower() in _ROOT_OUTPUT_DIRS)
                    or (parent == root and directory in ignored_root_dirs)):
                continue
            if (parent / directory).is_symlink():
                raise RuntimeError("Android incremental mode cannot certify symlink directory: " + str(parent / directory))
            kept.append(directory)
        dirs[:] = kept
        for name in sorted(names):
            item = parent / name
            rel = item.relative_to(root).as_posix()
            if (parent == root and name.startswith(".")) or is_android_excluded(rel, exclusions):
                continue
            if item.is_symlink():
                # A symlink could reach outside the build tree; no incremental
                # patch is permitted when we cannot prove its provenance.
                raise RuntimeError(
                    "Android incremental mode cannot certify symlink file: "
                    + str(item)
                )
            if not item.is_file() or item.suffix.lower() in _OUTPUT_SUFFIXES:
                continue
            if rel in assets and not rel.startswith(protected_resource_prefixes):
                if item.suffix.lower() in {".so", ".a", ".o", ".dll", ".dylib", ".pyd", ".jar", ".aar", ".class"}:
                    native[rel] = _sha_file(item)
                else:
                    sources[rel] = item
            elif ((parent == root and item.name.lower() in ANDROID_BUILD_FILES)
                  or rel.startswith("deployment/")):
                native[rel] = _sha_file(item)
    # android.json controls source protection and which project files are
    # shipped.  It does not change the already-built Qt/Python runtime:
    # protection is handled before selecting this debug fast path, and file
    # exclusions are checked against the resulting private archive below.
    # Likewise sign.json is relevant only to release APKs (which never use
    # this cache).  Hashing either here made harmless settings edits trigger
    # a complete p4a/Gradle build.
    spec = root / "pysidedeploy.spec"
    if spec.is_file():
        config = configparser.ConfigParser(interpolation=None)
        config.read(spec, encoding="utf-8")
        for key in ("wheel_pyside", "wheel_shiboken"):
            wheel = config.get("android", key, fallback="").strip()
            if wheel:
                path = Path(wheel).expanduser()
                if not path.is_absolute():
                    path = root / path
                if not path.is_file():
                    raise RuntimeError(f"Android wheel is unavailable: {path}")
                native[f"configured-wheel:{key}"] = _sha_file(path)
    keystore = Path.home() / ".android/debug.keystore"
    native["debug-signing-key"] = (
        _sha_file(keystore) if keystore.is_file() else "<not-created>"
    )
    file_hashes = {name: _sha_file(path) for name, path in sorted(sources.items())}
    # Adding an explicit Qt module import is a native dependency change, not
    # merely a source edit.
    qt_imports: set[str] = set()
    qt_ref = re.compile(r"\b(?:PySide6|PyQt6|PySide2|PyQt5)\.(Qt[A-Za-z0-9_]+)")
    for path in sources.values():
        if path.suffix.lower() == ".py":
            try:
                source = path.read_text(encoding="utf-8")
                qt_imports.update(qt_ref.findall(source))
                tree = ast.parse(source, filename=str(path))
                for node in ast.walk(tree):
                    if (isinstance(node, ast.ImportFrom)
                            and node.module in {"PySide6", "PyQt6", "PySide2", "PyQt5"}):
                        qt_imports.update(alias.name for alias in node.names
                                          if alias.name.startswith("Qt"))
            except (UnicodeError, SyntaxError) as exc:
                raise RuntimeError(f"Cannot inspect Android Python source: {path}") from exc
    native["qt-required-modules"] = ",".join(sorted(qt_imports))
    qml_imports: set[str] = set()
    for path in sources.values():
        if path.suffix.lower() == ".qml":
            try:
                qml_imports.update(re.findall(r"(?m)^\s*import\s+([A-Za-z_][\w.]*)", path.read_text(encoding="utf-8")))
            except UnicodeError as exc:
                raise RuntimeError(f"Cannot inspect QML source: {path}") from exc
    native["qml-required-modules"] = ",".join(sorted(qml_imports))
    versions: dict[str, str] = {}
    for package in ("PySide6", "shiboken6", "buildozer", "python-for-android",
                    "velto-cli", "velto-core", "qyro-engine", "velto-cinnamon-core"):
        try:
            versions[package] = metadata.version(package)
        except metadata.PackageNotFoundError:
            versions[package] = "<absent>"
    key_material = json.dumps({
        "files": native,
        "versions": versions,
        "python": sys.version,
        "platform": sys.platform,
        "android_entrypoint": _sha_file(
            Path(__file__).parent.parent / "common" / "android_entrypoint.py"
        ),
        "svg": project_uses_svg(root),
        # This deliberately contains only inputs that affect the executable
        # Android runtime.  The implementation of the Velto packager is not
        # one of them: changing this module must improve how an existing APK
        # is patched, not force Gradle/p4a to rebuild a byte-identical
        # runtime.  The archive, ABI, wheel, Qt/QML-import and Android-config
        # checks below remain the safety boundary for cache reuse.
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


def incremental_cache_miss_reason(root: Path) -> str:
    """Explain a fallback without reading or modifying cached artifacts."""
    path = _cache_root(root) / "manifest.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return "No hay una caché incremental válida; se necesita una compilación completa."
    if not isinstance(data, dict) or data.get("format") != _FORMAT:
        return "La caché incremental es de una versión anterior; se necesita una compilación completa para renovarla."
    return "La caché incremental no coincide con la configuración o el APK actual; se necesita una compilación completa."


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


def _archive_build_only_entries(apk: Path, exclusions: tuple[str, ...]) -> set[str]:
    with ZipFile(apk) as binary:
        private = next(name for name in ("assets/private.tar", "assets/private.mp3")
                       if name in binary.namelist())
        with tarfile.open(fileobj=io.BytesIO(binary.read(private)), mode="r:*") as tar:
            return {
                name for member in tar
                if (name := member.name.removeprefix("./").strip("/"))
                and not (is_android_project_directory if member.isdir()
                         else is_android_project_payload)(name, exclusions)
            }


def _archive_project_hashes(apk: Path, paths: set[str]) -> dict[str, str]:
    """Hash actual cached files instead of trusting possibly stale metadata."""
    with ZipFile(apk) as binary:
        private = next(name for name in ("assets/private.tar", "assets/private.mp3")
                       if name in binary.namelist())
        with tarfile.open(fileobj=io.BytesIO(binary.read(private)), mode="r:*") as tar:
            hashes: dict[str, str] = {}
            for member in tar:
                name = member.name.removeprefix("./").strip("/")
                if not member.isfile() or name not in paths:
                    continue
                if name in hashes:
                    raise RuntimeError(f"Cached Android APK contains duplicate project file: {name}")
                source = tar.extractfile(member)
                if source is None:
                    raise RuntimeError(f"Cannot read cached Android project file: {name}")
                digest = hashlib.sha256()
                for chunk in iter(lambda: source.read(1024 * 1024), b""):
                    digest.update(chunk)
                hashes[name] = digest.hexdigest()
            return hashes


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


def _is_debug_apk(apk: Path) -> bool:
    """Never reuse Android release artifacts with the debug signing workflow."""
    return apk.suffix.lower() == ".apk" and "debug" in apk.stem.lower()


def record_full_build(root: Path, apk: Path, request: Any,
                      baseline: tuple[dict[str, str], str] | None = None) -> bool:
    """Seed the fast path only from a successfully produced debug APK."""
    if not apk.is_file() or not _is_debug_apk(apk):
        return False
    sources, hashes, native = _scan(root)
    if baseline is not None and baseline != (hashes, native):
        return False
    contents = _archive_files(apk)
    if any(name.startswith(tuple(package + "/" for package in PACKAGES))
           and name not in sources for name in contents):
        return False
    verify_velto_apk_sources(apk, root)
    exclusions = android_exclusion_patterns(root)
    if any(is_android_excluded(name, exclusions) for name in contents):
        return False
    if not set(sources).issubset(contents):
        # Do not trust an artifact that cannot represent the next source diff.
        return False
    with ZipFile(apk) as signed:
        if ("resources.arsc" not in signed.namelist()
                or android_private_version_marker(
                    signed.read("resources.arsc")) is None):
            # Refuse incremental packaging unless p4a extraction invalidation
            # can be updated safely in the compiled Android resources.
            return False
        private = next(name for name in ("assets/private.tar", "assets/private.mp3")
                       if name in signed.namelist())
        with tarfile.open(fileobj=io.BytesIO(signed.read(private)), mode="r:*") as tar:
            members = {member.name.removeprefix("./").strip("/"): member
                       for member in tar if member.isfile()}
            for name, digest in hashes.items():
                payload = tar.extractfile(members[name])
                if payload is None or hashlib.sha256(payload.read()).hexdigest() != digest:
                    return False
    _store(_cache_root(root), apk, hashes, native + _request_key(request))
    return True


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
    if not _is_debug_apk(Path(meta["apk_name"])):
        return None
    if _sha_file(previous) != meta["apk_sha256"]:
        return None
    sources, hashes, native = _scan(root)
    native += _request_key(request)
    if native != meta["native"]:
        return None
    old = meta["files"]
    additions = set(hashes) - set(old)
    # Refuse to delete or replace anything not owned by the previous
    # recorded snapshot; never remove standard p4a files or Qt libraries.
    packaged = _archive_files(previous)
    packaged_hashes = _archive_project_hashes(previous, set(hashes))
    changed = {name for name, digest in hashes.items()
               if packaged_hashes.get(name) != digest}
    deletions = (set(old) - set(hashes)) & packaged
    # Never reuse an APK whose base already contains excluded host-only data.
    exclusions = android_exclusion_patterns(root)
    # Older APKs may include build-only files. Strip those from the app data
    # when updating, rather than carrying deployment/ or project metadata
    # into every incremental APK.
    build_only = _archive_build_only_entries(previous, exclusions)
    deletions.update(build_only)
    # A full Buildozer APK may already contain project files that an older
    # source manifest did not track. They are safe to replace by path, but
    # p4a's own bootstrap/environment files must never be overwritten.
    reserved = {"main.py", "p4a_env_vars.txt", "private.version", "libpybundle.version"}
    collisions = additions & (packaged - build_only)
    if (collisions & reserved
            or any(name.startswith("_python_bundle/") for name in collisions)):
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
                    m.name.removeprefix("./").strip("/") for m in tar
                }:
                    raise RuntimeError("Incremental APK retains deleted resources")
        verify_velto_apk_sources(artifact, root)
        verify_qt_android_runtime_assets(artifact, root)
        if project_uses_svg(root):
            verify_android_svg_apk(artifact)
        verify_android_exclusions(artifact, root)
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
