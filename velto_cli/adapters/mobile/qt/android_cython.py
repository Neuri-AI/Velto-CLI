"""Fail-closed Cython/Android-NDK extension compiler for protected app sources.

Only the developer-owned Python tree is transformed, never CPython stdlib or
third-party wheels. Requires the exact Android CPython headers, libpython
and NDK toolchain from the same target distribution. Never run host-built .so
files on Android or substitute .pyc.
"""
from __future__ import annotations

import os
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


def _target_headers(toolchain_root: Path, version: tuple[int, int]) -> list[Path]:
    base = toolchain_root / ".buildozer/android/platform"
    if not base.is_dir():
        raise RuntimeError("Cython Android requires the retained p4a build headers")
    wanted = f"python{version[0]}.{version[1]}"
    # Prefer the target Python installation headers, not Conda/macOS headers.
    matches = sorted(base.glob(f"build-*/build/python-installs/**/include/{wanted}/Python.h"))
    matches += sorted(base.glob("build-*/build/other_builds/python3*/**/Include/Python.h"))
    for header in matches:
        include = header.parent
        candidates = [include]
        build_root = include.parent
        candidates += [build_root, build_root / "android-build", build_root / "PC"]
        candidates += list(include.parent.glob("**/android-build"))
        config = next(
            (item for item in candidates if (item / "pyconfig.h").is_file()),
            None,
        )
        if config is not None:
            return list(dict.fromkeys([include, config]))
    raise RuntimeError(
        f"Missing target CPython {version[0]}.{version[1]} development headers "
        "and pyconfig.h in retained python-for-android build. Cannot build "
        "Cython extensions safely; no bytecode fallback."
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
    if version.split(".", 1)[0].isdigit() and int(version.split(".", 1)[0]) >= 3:
        return Path(sys.executable)
    isolated = Path.home() / ".velto/mobile/android/qt/cython3"
    executable = isolated / "bin/python"
    if not executable.is_file():
        _run([sys.executable, "-m", "venv", str(isolated)])
        _run([
            str(executable), "-m", "pip", "install",
            "--disable-pip-version-check", "Cython>=3.1,<4",
        ])
    check = subprocess.run(
        [str(executable), "-c",
         "import Cython; assert int(Cython.__version__.split('.')[0]) >= 3"],
        capture_output=True, text=True, check=False,
    )
    if check.returncode:
        raise RuntimeError(
            "Cython>=3 is required for Android native extensions. The "
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
            headers = _target_headers(toolchain_root, version)
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
