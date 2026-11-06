"""Release APKs must not ship application sources or accept debug signing."""
from __future__ import annotations

import io
from pathlib import Path
import tarfile
from zipfile import ZipFile

import pytest

from velto_cli.adapters.mobile.qt.android_release import (
    _owned,
    _bootstrap,
    protect_android_release_apk,
    verify_android_release_source_protection,
)


def _apk(path: Path, files: dict[str, bytes]) -> None:
    private = io.BytesIO()
    with tarfile.open(fileobj=private, mode="w:gz") as tar:
        for name, data in files.items():
            member = tarfile.TarInfo(name)
            member.size = len(data)
            tar.addfile(member, io.BytesIO(data))
    with ZipFile(path, "w") as apk:
        apk.writestr("assets/private.tar", private.getvalue())


def test_verifier_rejects_unprotected_component_sources(tmp_path):
    root = tmp_path / "app"
    (root / "components").mkdir(parents=True)
    (root / "components/test.py").write_text("from velto.ui import component\n")
    apk = tmp_path / "release.apk"
    _apk(apk, {
        "main.py": _bootstrap(),
        "main.so": b"fake compiled module",
        "components/test.py": b"TOP_SECRET = 'raw Python'\n",
    })
    with pytest.raises(RuntimeError, match="exposes application Python"):
        verify_android_release_source_protection(apk, root)


def test_verifier_accepts_cython_modules_and_entry_bootstrap(tmp_path):
    root = tmp_path / "app"
    (root / "components").mkdir(parents=True)
    (root / "components/test.py").write_text("ANSWER = 42\n")
    apk = tmp_path / "release.apk"
    _apk(apk, {
        "main.py": _bootstrap(),
        "main.pyc": _compile_bytecode(b"PASS = True\n", "main.py"),
        "components/test.so": b"fake compiled module",
    })
    verify_android_release_source_protection(apk, root)


def test_verifier_rejects_unprotected_framework_sources(tmp_path):
    root = tmp_path / "app"
    root.mkdir()
    apk = tmp_path / "release.apk"
    _apk(apk, {
        "main.py": _bootstrap(),
        "main.pyc": _compile_bytecode(b"PASS = True\n", "main.py"),
        "velto/__init__.py": b"EXPOSED = True\n",
    })
    with pytest.raises(RuntimeError, match="exposes application Python"):
        verify_android_release_source_protection(apk, root)


def test_release_does_not_rewrite_debug_apk(tmp_path):
    root = tmp_path / "app"
    root.mkdir()
    apk = tmp_path / "app-debug.apk"
    apk.write_bytes(b"original")
    with pytest.raises(RuntimeError, match="debug/release signing mode"):
        protect_android_release_apk(apk, root)
    assert apk.read_bytes() == b"original"


def test_owned_paths_excludes_p4a_stdlib_and_tools(tmp_path):
    root = tmp_path / "app"
    (root / "components").mkdir(parents=True)
    (root / "components/test.py").write_text("x = 1")
    assert _owned("components/test.py", root)
    assert _owned("psx/core/component.py", root)
    assert not _owned("python3.12/site.py", root)
    assert not _owned("deployment/tool.py", root)
    assert not _owned("site-packages/random.py", root)


def test_use_case_passes_protection_flag_to_builder(tmp_path):
    from unittest.mock import Mock
    from velto_cli.application.use_cases.freeze_mobile import FreezeMobileUseCase
    from velto_cli.domain.mobile import MobileTarget

    (tmp_path / "pyproject.toml").write_text("[project]\nname='demo'\n")
    (tmp_path / "main.py").write_text("from velto import VeltoContext\n")
    settings = Mock()
    settings.get_optional.side_effect = lambda name, fallback=None: {
        "framework": "pyside6", "entry_point": "main.py",
    }.get(name, fallback)
    builder = Mock()
    builder.freeze.return_value = None
    FreezeMobileUseCase(builder, settings, Mock()).execute(
        target=MobileTarget.ANDROID, project_root=tmp_path,
        debug=True, protect_source=True,
    )
    request = builder.freeze.call_args.args[0]
    assert request.debug and request.protect_source


def test_hostpython_discovery_checks_installed_p4a_root_prefix(tmp_path):
    from velto_cli.adapters.mobile.qt.android_bytecode import (
        _candidate_interpreters,
    )
    hostpython = (
        tmp_path / ".buildozer/android/platform/build-arm64-v8a/build/"
        "other_builds/hostpython3/desktop/hostpython3/native-build/"
        "root/usr/local/bin/python"
    )
    hostpython.parent.mkdir(parents=True)
    hostpython.write_bytes(b"fake binary")
    assert str(hostpython) in list(_candidate_interpreters((3, 14), tmp_path))


def test_hostpython_fallback_recognizes_homebrew_python(tmp_path, monkeypatch):
    from velto_cli.adapters.mobile.qt import android_bytecode
    monkeypatch.delenv("VELTO_ANDROID_BYTECODE_PYTHON", raising=False)
    original_is_file = android_bytecode.Path.is_file

    def fake_is_file(self):
        if str(self) == "/opt/homebrew/opt/python@3.14/bin/python3.14":
            return True
        return original_is_file(self)

    monkeypatch.setattr(android_bytecode.Path, "is_file", fake_is_file)
    assert (
        "/opt/homebrew/opt/python@3.14/bin/python3.14"
        in list(android_bytecode._candidate_interpreters((3, 14), None))
    )


def test_managed_bytecode_python_uses_isolated_conda_prefix(tmp_path, monkeypatch):
    from subprocess import CompletedProcess
    from velto_cli.adapters.mobile.qt import android_bytecode as mod

    prefix = tmp_path / "android-python" / "3.14"
    interpreter = prefix / "bin" / "python3.14"
    monkeypatch.setattr(mod, "_managed_python_root", lambda version: prefix)
    monkeypatch.setenv("CONDA_EXE", "/fake/bin/conda")
    calls = []

    def run(args, **kwargs):
        calls.append(args)
        interpreter.parent.mkdir(parents=True, exist_ok=True)
        interpreter.write_bytes(b"test executable")
        return CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(mod.subprocess, "run", run)
    assert mod._resolve_managed_python((3, 14), install=True) == str(interpreter)
    assert calls == [[
        "/fake/bin/conda", "create", "--yes", "--quiet",
        "--prefix", str(prefix), "python=3.14",
    ]]


def test_managed_bytecode_python_does_not_modify_existing_prefix(
    tmp_path, monkeypatch,
):
    from velto_cli.adapters.mobile.qt import android_bytecode as mod
    prefix = tmp_path / "cache" / "3.14"
    interpreter = prefix / "bin/python3.14"
    interpreter.parent.mkdir(parents=True)
    interpreter.write_bytes(b"cached")
    monkeypatch.setattr(mod, "_managed_python_root", lambda version: prefix)

    def forbidden_run(*args, **kwargs):
        raise AssertionError("Conda must not run for already cached interpreter")
    monkeypatch.setattr(mod.subprocess, "run", forbidden_run)
    assert mod._resolve_managed_python((3, 14), install=True) == str(interpreter)


def test_mismatched_python_without_conda_is_not_auto_installed(
    tmp_path, monkeypatch,
):
    import sys
    from velto_cli.adapters.mobile.qt import android_bytecode as mod

    target = (3, 14) if sys.version_info[:2] != (3, 14) else (3, 12)
    monkeypatch.delenv("CONDA_EXE", raising=False)
    monkeypatch.setattr(mod.shutil, "which", lambda cmd: None)
    monkeypatch.setattr(
        mod, "_candidate_interpreters", lambda version, toolchain_root: [],
    )
    monkeypatch.setattr(
        mod, "_managed_python_root",
        lambda version: tmp_path / "isolated" / "missing",
    )
    # Avoid touching the real user's ~/miniconda3 while testing.
    monkeypatch.setattr(mod.Path, "home", lambda: tmp_path)
    with pytest.raises(RuntimeError, match="no matching native build interpreter"):
        mod.find_android_bytecode_python(target)


def test_cython_module_names_and_library_paths():
    from velto_cli.adapters.mobile.qt.android_cython import (
        module_name, output_name,
    )
    assert module_name("main.py") == "main"
    assert module_name("components/test.py") == "components.test"
    assert module_name("components/__init__.py") == "components"
    assert module_name("velto/__init__.py") == "velto"
    assert output_name("components/test.py") == "components/test.so"
    assert output_name("components/__init__.py") == "components/__init__.so"


def test_cython_preparation_preserves_main_guard_on_import():
    from velto_cli.adapters.mobile.qt.android_release import (
        _prepare_bytecode_source, _bootstrap,
    )
    source = (
        b"def boot(): return 'ready'\n"
        b"if __name__ == '__main__':\n"
        b"    boot()\n"
    )
    prepared = _prepare_bytecode_source(source, "main.py")
    import ast
    ast.parse(prepared)
    assert b"if True:" in prepared
    assert b"importlib.import_module('main')" in _bootstrap()
    assert b".pyc" not in _bootstrap()


def test_cython_target_is_android_elf64_arm64_only():
    from velto_cli.adapters.mobile.qt.android_cython import _is_elf_arm64
    data = bytearray(32)
    data[:6] = b"\x7fELF\x02\x01"
    data[18:20] = (183).to_bytes(2, "little")
    assert _is_elf_arm64(data)
    data[18:20] = (62).to_bytes(2, "little")
    assert not _is_elf_arm64(data)


def test_cython_fails_closed_without_android_ndk(monkeypatch):
    from velto_cli.adapters.mobile.qt import android_cython
    monkeypatch.setattr(
        android_cython, "_ndk_root",
        lambda: Path("/nonexistent-android-ndk"),
    )
    with pytest.raises(RuntimeError, match="no aarch64"):
        android_cython._ndk_tools(28)


