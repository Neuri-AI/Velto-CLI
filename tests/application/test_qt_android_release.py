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
    with pytest.raises(RuntimeError, match="release APK"):
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
