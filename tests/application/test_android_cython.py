"""Unit validation of native Android Cython pipeline contracts.

These tests validate translation and policy only; Android NDK/device
certification runs separately on macOS/Android hardware.
"""
from __future__ import annotations

from pathlib import Path
import subprocess
import sys

import pytest

from velto_cli.adapters.mobile.qt.android_cython import (
    module_name, output_name, _is_elf_arm64,
)


def test_cython_does_not_fallback_to_python_bytecode(monkeypatch):
    from velto_cli.adapters.mobile.qt import android_cython

    def fail(*args, **kwargs):
        raise RuntimeError("NDK unavailable")
    monkeypatch.setattr(android_cython, "_ndk_tools", fail)
    monkeypatch.setattr(android_cython, "_cython_python", lambda: Path(sys.executable))
    with pytest.raises(RuntimeError, match="NDK unavailable"):
        android_cython.compile_android_extensions(
            [("main.py", Path("main.py"), Path("main.so"))],
            apk=Path("missing.apk"),
            toolchain_root=Path("."),
            obfuscate=True,
        )
    assert not Path("main.so").exists()


@pytest.mark.parametrize("source_name,expected_module,expected_output", [
    ("main.py", "main", "main.so"),
    ("components/test.py", "components.test", "components/test.so"),
    ("components/__init__.py", "components", "components/__init__.so"),
])
def test_native_module_mapping(source_name, expected_module, expected_output):
    assert module_name(source_name) == expected_module
    assert output_name(source_name) == expected_output


def test_rejects_host_elf_when_android_arm64_required():
    elf = bytearray(32)
    elf[:6] = b"\x7fELF\x02\x01"
    elf[18:20] = (62).to_bytes(2, "little")  # x86_64
    assert not _is_elf_arm64(elf)


def test_cython_can_translate_package_native_initializer(tmp_path):
    cython = pytest.importorskip("Cython")
    parts = cython.__version__.split(".")
    if not all(p.isdigit() for p in parts[:3]) or tuple(
        int(p) for p in parts[:3]
    ) < (3, 2, 8):
        pytest.skip("Cython 3.2.8+ needed for native CPython 3.14 generation")
    source = tmp_path / "__init__.py"
    source.write_text("VALUE = 42\n", encoding="utf-8")
    output = tmp_path / "components.c"
    result = subprocess.run(
        [
            sys.executable, "-m", "cython", "-3", "--module-name",
            "components", "-o", str(output), str(source),
        ],
        capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "PyInit_components" in output.read_text(encoding="utf-8")
