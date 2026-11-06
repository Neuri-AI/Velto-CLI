"""Fail-closed Cython/Android-NDK extension compiler for protected app sources.

Only the developer-owned Python tree is transformed, never CPython stdlib or
third-party wheels. Requires the exact Android CPython headers, libpython
and NDK toolchain from the same target distribution. Never run host-built .so
files on Android or substitute .pyc.
"""
from __future__ import annotations

import os
import hashlib
import shutil
import tempfile
from pathlib import Path, PurePosixPath
import re
import subprocess
import sys
from zipfile import ZipFile


def module_name(source_name: str) -> str:
    path = PurePosixPath(source_name)
    if path.suffix != ".py" or not path.parts or any(
        piece in (".", "..") for piece in path.parts
    ):
        raise ValueError(f"Invalid Android Cython source path: {source_name}")
    if path.name == "__init__.py":
        parts = path.parts[:-1]
    else:
        parts = path.with_suffix("").parts
    if not parts or any(not part.isidentifier() for part in parts):
        raise ValueError(f"Cannot compile non-module path with Cython: {source_name}")
    return ".".join(parts)


def output_name(source_name: str) -> str:
    # CPython importlib.ExtensionFileLoader accepts the untagged .so suffix.
    return str(PurePosixPath(source_name).with_suffix(".so"))


def _is_elf_arm64(data: bytes) -> bool:
    # ELF64 little-endian, e_machine=183 (EM_AARCH64).
    return (
        len(data) >= 20 and data[:6] == b"\x7fELF\x02\x01"
        and int.from_bytes(data[18:20], "little") == 183
    )


def _ndk_root() -> Path:
    candidates = [
        os.environ.get("ANDROID_NDK_HOME"),
        os.environ.get("ANDROIDNDK"),
        os.environ.get("ANDROID_NDK_ROOT"),
        Path.home() / ".pyside6_android_deploy/android-ndk/android-ndk-r28c",
    ]
    candidates.extend(sorted((Path.home() / ".buildozer/android/platform").glob(
        "android-ndk*"
    )))
    for candidate in candidates:
        if not candidate:
            continue
        root = Path(candidate).expanduser()
        if (root / "toolchains/llvm/prebuilt").is_dir():
            return root
    raise RuntimeError(
        "Cython Android requires a configured Android NDK. Set ANDROID_NDK_HOME "
        "or install the NDK used by pyside6-android-deploy. No .pyc fallback."
    )


def _ndk_tools(api: int) -> tuple[Path, Path | None]:
    prebuilt = _ndk_root() / "toolchains/llvm/prebuilt"
    for host in ("darwin-x86_64", "darwin-arm64", "linux-x86_64", "windows-x86_64"):
        bin_dir = prebuilt / host / "bin"
        compiler = bin_dir / f"aarch64-linux-android{api}-clang"
        if compiler.is_file():
            strip = bin_dir / "llvm-strip"
            return compiler, strip if strip.is_file() else None
    raise RuntimeError(
        f"Android NDK has no aarch64-linux-android{api}-clang cross-compiler. "
        "Cannot publish a Cython-protected APK."
    )


def _target_header_cache() -> Path:
    """Persistent ABI-specific native header cache, outside every project."""
    return Path.home() / ".velto/mobile/android/qt/target-python-headers"


def has_cached_target_headers() -> bool:
    """Used only to decide whether p4a must rebuild its Python recipe."""
    base = _target_header_cache()
    if not base.is_dir():
        return False
    return any(
        (child / "include/Python.h").is_file()
        and (
            (child / "include/pyconfig.h").is_file()
            or (child / "config/pyconfig.h").is_file()
        )
        for child in base.iterdir() if child.is_dir()
    )


def _cached_headers(libpython_hash: str) -> list[Path] | None:
    base = _target_header_cache() / libpython_hash
    include, config = base / "include", base / "config"
    if (include / "Python.h").is_file():
        if (include / "pyconfig.h").is_file():
            return [include]
        if (config / "pyconfig.h").is_file():
            return [include, config]
    return None


def _cache_headers(headers: list[Path], libpython_hash: str) -> list[Path]:
    """Persist only headers produced by this exact Android libpython build.

    Do not move originals; copy after verifying no directory symlinks can
    escape the transient build tree. Cache paths include the binary hash.
    """
    cached = _cached_headers(libpython_hash)
    if cached:
        return cached
    root = _target_header_cache()
    root.mkdir(parents=True, exist_ok=True)
    destination = root / libpython_hash
    with tempfile.TemporaryDirectory(prefix=".velto-headers-", dir=root) as tmp:
        stage = Path(tmp)
        shutil.copytree(
            headers[0], stage / "include", symlinks=False,
        )
        if len(headers) > 1:
            (stage / "config").mkdir()
            shutil.copy2(headers[1] / "pyconfig.h", stage / "config/pyconfig.h")
        if not (stage / "include/Python.h").is_file():
            raise RuntimeError("Cannot cache Android target Python.h")
        if not (
            (stage / "include/pyconfig.h").is_file()
            or (stage / "config/pyconfig.h").is_file()
        ):
            raise RuntimeError("Cannot cache Android target pyconfig.h")
        if not destination.exists():
            stage.rename(destination)
    result = _cached_headers(libpython_hash)
    if not result:
        raise RuntimeError("Android target header cache was not preserved")
    return result


def _python_header_roots(toolchain_root: Path) -> list[Path]:
    """Discover p4a build trees without touching developer-owned files."""
    candidates = [
        toolchain_root / ".buildozer/android/platform",
        Path.home() / ".buildozer/android/platform",
        Path.home() / ".pyside6_android_deploy",
    ]
    # Qt can reuse global Buildozer/p4a distributions even when the
    # disposable project stage has no retained CPython recipe headers.
    for variable in ("P4A_BUILD_ROOT", "VELTO_ANDROID_P4A_BUILD_ROOT"):
        if os.environ.get(variable):
            candidates.insert(0, Path(os.environ[variable]).expanduser())
    return [candidate for candidate in candidates if candidate.is_dir()]


def _target_headers(
    toolchain_root: Path, version: tuple[int, int],
    libpython_hash: str | None = None,
) -> list[Path]:
    """Find Android-target Python.h and pyconfig.h from the SAME p4a recipe.

    python-for-android's Python3Recipe installs headers in
    android-build/android-root/include/pythonX.Y. Never use host/Conda
    pyconfig.h: it describes macOS, not Android.
    """
    wanted = f"python{version[0]}.{version[1]}"
    if libpython_hash:
        cached = _cached_headers(libpython_hash)
        if cached:
            return cached
    explicit = os.environ.get("VELTO_ANDROID_PYTHON_INCLUDE")
    if explicit:
        include = Path(explicit).expanduser().resolve()
        if include.name != wanted or not (
            include / "Python.h"
        ).is_file() or not (include / "pyconfig.h").is_file():
            raise RuntimeError(
                "VELTO_ANDROID_PYTHON_INCLUDE must point to the Android "
                f"{wanted} include directory containing Python.h and "
                "Android pyconfig.h; refusing host/macOS Python headers."
            )
        return [include]
    searched: list[str] = []
    for root in _python_header_roots(toolchain_root):
        searched.append(str(root))
        # Restrict globbing to plausible recipe / install roots. The earlier
        # selector incorrectly required python-installs and omitted the actual
        # Android python3 recipe's android-root include directory.
        patterns = (
            f"build-*/build/other_builds/python3*/**/android-build/android-root/include/{wanted}/Python.h",
            f"build-*/build/python-installs/**/include/{wanted}/Python.h",
            f"**/android-build/android-root/include/{wanted}/Python.h",
            f"**/python-installs/**/include/{wanted}/Python.h",
        )
        seen: set[Path] = set()
        for pattern in patterns:
            for header in sorted(root.glob(pattern)):
                include = header.parent
                if include in seen:
                    continue
                seen.add(include)
                if (include / "pyconfig.h").is_file():
                    return [include]
                # Do not combine headers from different CPython builds.
                # Only the same recipe's android-build tree is acceptable.
                build = next(
                    (parent for parent in include.parents if parent.name == "android-build"),
                    None,
                )
                if build is not None:
                    for config in (
                        build / "pyconfig.h",
                        build / "Include/pyconfig.h",
                        build / "android-root/include/pyconfig.h",
                    ):
                        if config.is_file():
                            return [include, config.parent]
    raise RuntimeError(
        f"Cython Android needs CPython {version[0]}.{version[1]} TARGET "
        "headers (Python.h and Android pyconfig.h), but the reused p4a "
        "distribution has no retained Python recipe build. Searched: "
        + ", ".join(searched or ["no p4a build roots found"])
        + ". Do not install host Python headers or copy macOS pyconfig.h. "
        "Rebuild the p4a Python recipe with retained Android build files, "
        "or provide its build directory via VELTO_ANDROID_P4A_BUILD_ROOT. "
        "No .pyc fallback."
    )



def _run(command: list[str], *, cwd: Path | None = None) -> None:
    try:
        result = subprocess.run(
            command, cwd=cwd, capture_output=True, text=True,
            errors="replace", check=False,
        )
    except OSError as exc:
        raise RuntimeError(f"Cannot launch Android Cython tool: {command[0]}") from exc
    if result.returncode:
        raise RuntimeError(
            f"Android Cython step failed ({Path(command[0]).name}): "
            + (result.stderr or result.stdout)[-3500:]
        )


def _cython_python() -> Path:
    """Use Cython 3 without breaking Buildozer's separate Cython<3 constraint."""
    from importlib import metadata
    try:
        version = metadata.version("Cython")
    except metadata.PackageNotFoundError:
        version = ""
    parts = version.split(".")
    if len(parts) >= 3 and all(part.isdigit() for part in parts[:3]):
        if tuple(int(part) for part in parts[:3]) >= (3, 2, 8):
            return Path(sys.executable)
    isolated = Path.home() / ".velto/mobile/android/qt/cython3"
    executable = isolated / "bin/python"
    if not executable.is_file():
        _run([sys.executable, "-m", "venv", str(isolated)])
        _run([
            str(executable), "-m", "pip", "install",
            "--disable-pip-version-check", "Cython>=3.2.8,<4",
        ])
    check = subprocess.run(
        [str(executable), "-c",
         "import Cython; assert tuple(map(int, Cython.__version__.split('.')[:3])) >= (3, 2, 8)"],
        capture_output=True, text=True, check=False,
    )
    if check.returncode:
        raise RuntimeError(
            "Cython>=3.2.8 is required for Python 3.14-compatible extensions. The "
            "isolated compiler environment is invalid. No .pyc fallback."
        )
    return executable



def compile_android_extensions(
    jobs: list[tuple[str, Path, Path]],
    *, apk: Path, toolchain_root: Path, obfuscate: bool,
    min_api: int = 28,
) -> None:
    """Cross-compile prepared project modules to verified Android ELF .so.

    Each job: (original .py member, prepared source path, destination .so).
    The original source tree is not modified; this runs in disposable stage.
    """
    if not jobs:
        raise RuntimeError("Cython protection selected but no Python modules found")
    cython_python = _cython_python()
    compiler, stripper = _ndk_tools(min_api)
    with ZipFile(apk) as bundle:
        versions = {
            (int(m.group(1)), int(m.group(2)))
            for name in bundle.namelist()
            if (m := re.search(r"/libpython(\d+)\.(\d+)\.so$", name))
        }
        if len(versions) != 1:
            raise RuntimeError(
                f"Cannot identify one Android CPython ABI: {sorted(versions)}"
            )
        version = next(iter(versions))
        library = next(
            (name for name in bundle.namelist()
             if name.endswith(f"/libpython{version[0]}.{version[1]}.so")), None
        )
        if library is None:
            raise RuntimeError("APK lacks the Android target libpython")
        from tempfile import TemporaryDirectory
        with TemporaryDirectory(prefix="velto-cython-link-") as directory:
            link = Path(directory)
            target_lib = link / f"libpython{version[0]}.{version[1]}.so"
            target_lib.write_bytes(bundle.read(library))
            if not _is_elf_arm64(target_lib.read_bytes()[:32]):
                raise RuntimeError("APK libpython is not an ARM64 ELF binary")
            target_hash = hashlib.sha256(target_lib.read_bytes()).hexdigest()
            headers = _target_headers(
                toolchain_root, version, libpython_hash=target_hash,
            )
            if _cached_headers(target_hash) is None:
                headers = _cache_headers(headers, target_hash)
            for index, (name, source, destination) in enumerate(jobs):
                mod = module_name(name)
                cfile = link / f"module_{index}.c"
                _run([
                    str(cython_python), "-m", "cython", "-3",
                    "--module-name", mod,
                    *(["--no-docstrings"] if obfuscate else []),
                    "-o", str(cfile), str(source),
                ])
                destination.parent.mkdir(parents=True, exist_ok=True)
                command = [
                    str(compiler), "-shared", "-fPIC", "-O2",
                    "-Wl,--no-undefined", "-Wl,-soname," + destination.name,
                    *[flag for directory in headers for flag in ("-I", str(directory))],
                    str(cfile), "-L", str(link),
                    f"-lpython{version[0]}.{version[1]}",
                    "-o", str(destination),
                ]
                _run(command)
                if not _is_elf_arm64(destination.read_bytes()[:32]):
                    raise RuntimeError(
                        f"Cython output is not Android ARM64 ELF: {destination}"
                    )
                if obfuscate:
                    if stripper is None:
                        raise RuntimeError(
                            "obfuscate=true requires NDK llvm-strip; "
                            "cannot safely claim obfuscation was applied."
                        )
                    _run([str(stripper), "--strip-unneeded", str(destination)])
                    if not _is_elf_arm64(destination.read_bytes()[:32]):
                        raise RuntimeError(
                            f"Stripping damaged Cython ELF module: {destination}"
                        )
