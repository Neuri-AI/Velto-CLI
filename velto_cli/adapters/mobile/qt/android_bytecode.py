"""Compile APK application modules with the exact CPython minor used on Android.

Never emit bytecode using a mismatched host interpreter. A compatible native
hostpython3 produced by python-for-android is preferred during full deploy;
a separately installed matching python3.X supports incremental updates.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys


_COMPILER = r"""
import importlib._bootstrap_external as boot
import json
from pathlib import Path
import sys

jobs = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
for source_name, source_path, output_path in jobs:
    payload = Path(source_path).read_bytes()
    code = compile(payload, source_name, "exec", dont_inherit=True, optimize=2)
    compiled = boot._code_to_timestamp_pyc(code, mtime=0, source_size=len(payload))
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(compiled)
"""


def _candidate_interpreters(version: tuple[int, int], toolchain_root: Path | None):
    wanted = f"python{version[0]}.{version[1]}"
    explicit = os.environ.get("VELTO_ANDROID_BYTECODE_PYTHON")
    if explicit:
        yield explicit
    # p4a hostpython3 installs to native-build/root/usr/local/bin/python.
    # That executable normally exists during fresh builds but may be omitted
    # when p4a reuses an existing distribution and cleans build directories.
    if toolchain_root is not None:
        platform = toolchain_root / ".buildozer/android/platform"
        if platform.is_dir():
            for build_root in sorted(platform.glob(
                "build-*/build/other_builds/hostpython3*"
            )):
                for pattern in (
                    "**/native-build/root/usr/local/bin/python",
                    "**/native-build/root/usr/local/bin/python3",
                    f"**/native-build/root/usr/local/bin/{wanted}",
                    "**/native-build/python",
                    "**/native-build/python3",
                    f"**/native-build/{wanted}",
                    "**/native-build/bin/python3",
                    f"**/native-build/bin/{wanted}",
                ):
                    for executable in sorted(build_root.glob(pattern)):
                        if executable.is_file():
                            yield str(executable)
    if sys.version_info[:2] == version:
        yield sys.executable
    executable = shutil.which(wanted)
    if executable:
        yield executable
    for candidate in (
        f"/opt/homebrew/opt/python@{version[0]}.{version[1]}/bin/{wanted}",
        f"/usr/local/opt/python@{version[0]}.{version[1]}/bin/{wanted}",
        f"/opt/homebrew/bin/{wanted}",
        f"/usr/local/bin/{wanted}",
        f"/usr/bin/{wanted}",
    ):
        if Path(candidate).is_file():
            yield candidate



def _managed_python_root(version: tuple[int, int]) -> Path:
    """Persistent build-only interpreter; never mutate the active Conda env."""
    return Path.home() / ".velto/mobile/android/qt/bytecode-python" / (
        f"{version[0]}.{version[1]}"
    )


def _resolve_managed_python(version: tuple[int, int], *, install: bool) -> str | None:
    """Resolve or provision a persistent matching host CPython via Conda."""
    prefix = _managed_python_root(version)
    executable = prefix / "bin" / f"python{version[0]}.{version[1]}"
    if executable.is_file():
        return str(executable)
    if not install:
        return None
    conda = (
        os.environ.get("CONDA_EXE") or shutil.which("conda")
        or (
            str(Path.home() / "miniconda3/bin/conda")
            if (Path.home() / "miniconda3/bin/conda").is_file() else None
        )
    )
    if not conda:
        return None
    prefix.parent.mkdir(parents=True, exist_ok=True)
    try:
        completed = subprocess.run(
            [
                conda, "create", "--yes", "--quiet", "--prefix", str(prefix),
                f"python={version[0]}.{version[1]}",
            ],
            capture_output=True, text=True, check=False, timeout=900,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError(
            "Velto could not provision the Android bytecode interpreter "
            "in its isolated cache; the active Conda environment is unchanged."
        ) from exc
    if completed.returncode:
        raise RuntimeError(
            f"Velto could not provision Python {version[0]}.{version[1]} "
            "in its isolated Android build cache. Conda reported: "
            + (completed.stderr or completed.stdout)[-1400:]
        )
    if not executable.is_file():
        raise RuntimeError(
            f"Conda did not create the Android build interpreter at {executable}"
        )
    return str(executable)


def find_android_bytecode_python(
    version: tuple[int, int], *, toolchain_root: Path | None = None,
) -> str:
    """Return a runnable matching Python executable or give actionable error."""
    checked: set[str] = set()
    managed = _resolve_managed_python(version, install=False)
    candidates = list(_candidate_interpreters(version, toolchain_root))
    if managed is not None:
        candidates.insert(0, managed)
    for candidate in candidates:
        if candidate in checked:
            continue
        checked.add(candidate)
        try:
            outcome = subprocess.run(
                [candidate, "-I", "-S", "-c",
                 "import sys; print('%d.%d' % sys.version_info[:2])"],
                capture_output=True, text=True, timeout=10, check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            continue
        if (outcome.returncode == 0
                and outcome.stdout.strip() == f"{version[0]}.{version[1]}"):
            return candidate
    # A previous Android build may have discarded hostpython3 when it
    # removed its temporary workspace. Recreate a matching persistent
    # interpreter without switching or modifying the active Conda env.
    managed = _resolve_managed_python(version, install=True)
    if managed is not None and managed not in checked:
        result = subprocess.run(
            [managed, "-I", "-S", "-c", "import sys; print('%d.%d' % sys.version_info[:2])"],
            capture_output=True, text=True, timeout=10, check=False,
        )
        if result.returncode == 0 and result.stdout.strip() == (
            f"{version[0]}.{version[1]}"
        ):
            return managed
    label = f"Python {version[0]}.{version[1]}"
    raise RuntimeError(
        f"Android APK uses {label}; no matching native build interpreter "
        "was found. Velto will not use incompatible host bytecode. "
        f"Set VELTO_ANDROID_BYTECODE_PYTHON to a working {label} executable, "
        "or make Conda available so Velto can provision one in its build cache."
    )


def compile_android_bytecode(
    jobs: list[tuple[str, Path, Path]], version: tuple[int, int],
    *, toolchain_root: Path | None = None,
) -> None:
    """Compile prepared source modules in one matching interpreter invocation."""
    if not jobs:
        return
    interpreter = find_android_bytecode_python(
        version, toolchain_root=toolchain_root,
    )
    import tempfile
    with tempfile.TemporaryDirectory(prefix="velto-bytecode-") as directory:
        manifest = Path(directory) / "jobs.json"
        manifest.write_text(
            json.dumps([(name, str(source), str(output))
                        for name, source, output in jobs]), encoding="utf-8",
        )
        result = subprocess.run(
            [interpreter, "-I", "-S", "-c", _COMPILER, str(manifest)],
            capture_output=True, text=True, check=False,
        )
        if result.returncode:
            raise RuntimeError(
                f"Android bytecode compiler failed using {interpreter}: "
                + (result.stderr or result.stdout)[-1800:]
            )
    missing = [str(output) for _, _, output in jobs if not output.is_file()]
    if missing:
        raise RuntimeError(
            "Android compiler did not produce required bytecode: "
            + ", ".join(missing[:5])
        )
