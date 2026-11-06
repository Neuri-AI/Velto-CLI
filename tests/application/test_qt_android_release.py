"""Release APKs must not ship application sources or accept debug signing."""
from __future__ import annotations

import io
from pathlib import Path
import tarfile
from zipfile import ZipFile

import pytest

from velto_cli.adapters.mobile.qt.android_release import (
    _compile_bytecode,
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


def test_bytecode_is_sourceless_and_executable(tmp_path):
    import importlib.machinery
    import importlib.util

    pyc = tmp_path / "module.pyc"
    pyc.write_bytes(_compile_bytecode(b"ANSWER = 42\n", "module.py"))
    loader = importlib.machinery.SourcelessFileLoader("example_release_module", str(pyc))
    spec = importlib.util.spec_from_loader("example_release_module", loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    assert module.ANSWER == 42


def test_bytecode_supports_future_imports(tmp_path):
    data = b"from __future__ import annotations\nANSWER: int = 42\n"
    pyc = _compile_bytecode(data, "components/sample.py")
    assert pyc[:4] == __import__("importlib.util", fromlist=["MAGIC_NUMBER"]).MAGIC_NUMBER


def test_verifier_rejects_unprotected_component_sources(tmp_path):
    root = tmp_path / "app"
    (root / "components").mkdir(parents=True)
    (root / "components/test.py").write_text("from velto.ui import component\n")
    apk = tmp_path / "release.apk"
    _apk(apk, {
        "main.py": _bootstrap(),
        "main.pyc": _compile_bytecode(b"PASS = True\n", "main.py"),
        "components/test.py": b"TOP_SECRET = 'raw Python'\n",
    })
    with pytest.raises(RuntimeError, match="exposes application Python"):
        verify_android_release_source_protection(apk, root)


def test_verifier_accepts_app_bytecode_and_entry_bootstrap(tmp_path):
    root = tmp_path / "app"
    (root / "components").mkdir(parents=True)
    (root / "components/test.py").write_text("ANSWER = 42\n")
    apk = tmp_path / "release.apk"
    _apk(apk, {
        "main.py": _bootstrap(),
        "main.pyc": _compile_bytecode(b"PASS = True\n", "main.py"),
        "components/test.pyc": _compile_bytecode(b"ANSWER = 42\n", "components/test.py"),
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


def test_protected_debug_fails_closed_if_transform_fails(tmp_path, monkeypatch):
    from unittest.mock import Mock
    from velto_cli.adapters.mobile.freezer import MobileToolchainFreezer
    from velto_cli.domain.mobile import MobileFreezeRequest, MobileFramework, MobileTarget

    (tmp_path / "main.py").write_text("from velto import VeltoContext\n")
    result = tmp_path / "demo-debug.apk"
    result.write_bytes(b"original")
    monkeypatch.setattr(
        "velto_cli.adapters.mobile.qt.android.try_incremental_build",
        lambda *args: result,
    )
    def fail(*args, **kwargs):
        raise RuntimeError("protection failed")
    monkeypatch.setattr(
        "velto_cli.adapters.mobile.qt.android.protect_android_apk", fail,
    )
    builder = MobileToolchainFreezer(Mock())
    request = MobileFreezeRequest(
        project_root=tmp_path, target=MobileTarget.ANDROID,
        framework=MobileFramework.PYSIDE6, app_name="demo",
        version="0.1", debug=True, protect_source=True,
    )
    with pytest.raises(RuntimeError, match="protection failed"):
        builder.freeze(request)
    assert result.read_bytes() == b"original"


def test_protected_debug_reuses_incremental_build(tmp_path, monkeypatch):
    from unittest.mock import Mock
    from velto_cli.adapters.mobile.freezer import MobileToolchainFreezer
    from velto_cli.domain.mobile import MobileFreezeRequest, MobileFramework, MobileTarget

    (tmp_path / "main.py").write_text("from velto import VeltoContext\n")
    result = tmp_path / "demo-debug.apk"
    result.write_bytes(b"original")
    monkeypatch.setattr(
        "velto_cli.adapters.mobile.qt.android.try_incremental_build",
        lambda *args: result,
    )
    protected = Mock(return_value=result)
    monkeypatch.setattr(
        "velto_cli.adapters.mobile.qt.android.protect_android_apk", protected,
    )
    runner = Mock()
    builder = MobileToolchainFreezer(runner)
    request = MobileFreezeRequest(
        project_root=tmp_path, target=MobileTarget.ANDROID,
        framework=MobileFramework.PYSIDE6, app_name="demo",
        version="0.1", debug=True, protect_source=True,
    )
    artifact = builder.freeze(request)
    assert artifact.path == result
    protected.assert_called_once_with(result, tmp_path, debug=True)
    runner.run.assert_not_called()


def test_android_bytecode_compiler_uses_current_interpreter_when_versions_match(
    tmp_path,
):
    import sys
    from velto_cli.adapters.mobile.qt.android_bytecode import (
        compile_android_bytecode,
    )
    prepared = tmp_path / "test.py"
    compiled = tmp_path / "test.pyc"
    prepared.write_text("NUMBER = 42\n")
    compile_android_bytecode(
        [("components/test.py", prepared, compiled)], sys.version_info[:2],
    )
    assert compiled.read_bytes()[:4] == __import__(
        "importlib.util", fromlist=["MAGIC_NUMBER"]
    ).MAGIC_NUMBER


def test_bytecode_compiler_never_uses_mismatched_host(tmp_path, monkeypatch):
    import sys
    from velto_cli.adapters.mobile.qt.android_bytecode import (
        find_android_bytecode_python,
    )
    from unittest.mock import Mock
    monkeypatch.delenv("VELTO_ANDROID_BYTECODE_PYTHON", raising=False)
    monkeypatch.setattr(
        "velto_cli.adapters.mobile.qt.android_bytecode._candidate_interpreters",
        lambda version, toolchain_root: [sys.executable],
    )
    target = (3, 14) if sys.version_info[:2] != (3, 14) else (3, 12)
    with pytest.raises(RuntimeError, match="no matching native build interpreter"):
        find_android_bytecode_python(target)


def test_bytecode_compiler_accepts_verified_matching_build_interpreter(
    tmp_path, monkeypatch,
):
    import sys
    from velto_cli.adapters.mobile.qt.android_bytecode import (
        find_android_bytecode_python,
    )
    monkeypatch.setattr(
        "velto_cli.adapters.mobile.qt.android_bytecode._candidate_interpreters",
        lambda version, toolchain_root: ["/not/a/python", sys.executable],
    )
    assert find_android_bytecode_python(sys.version_info[:2]) == sys.executable


def test_protected_psx_preparation_sets_component_marker():
    from velto_cli.adapters.mobile.qt.android_release import (
        _prepare_bytecode_source,
    )
    import ast
    source = (
        b"from __future__ import annotations\n"
        b"from velto.ui import psx\n"
        b"def render(): return psx('<p>Hello</p>')\n"
    )
    compiled_source = _prepare_bytecode_source(source, "components/test.py")
    ast.parse(compiled_source)
    assert b"__psx_build_precompiled__" in compiled_source
    assert b"__future__" in compiled_source


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
