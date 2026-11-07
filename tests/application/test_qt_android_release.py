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


def _elf_arm64_fixture():
    data = bytearray(32)
    data[:6] = b"\x7fELF\x02\x01"
    data[18:20] = (183).to_bytes(2, "little")
    return bytes(data)


def test_verifier_rejects_unprotected_component_sources(tmp_path):
    root = tmp_path / "app"
    (root / "components").mkdir(parents=True)
    (root / "components/test.py").write_text("from velto.ui import component\n")
    apk = tmp_path / "release.apk"
    _apk(apk, {
        "main.py": _bootstrap(),
        "main.so": _elf_arm64_fixture(),
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
        "main.so": _elf_arm64_fixture(),
        "components/test.so": _elf_arm64_fixture(),
    })
    verify_android_release_source_protection(apk, root)


def test_verifier_rejects_unprotected_framework_sources(tmp_path):
    root = tmp_path / "app"
    root.mkdir()
    apk = tmp_path / "release.apk"
    _apk(apk, {
        "main.py": _bootstrap(),
        "main.so": _elf_arm64_fixture(),
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
        _prepare_cython_source, _bootstrap,
    )
    source = (
        b"def boot(): return 'ready'\n"
        b"if __name__ == '__main__':\n"
        b"    boot()\n"
    )
    prepared = _prepare_cython_source(source, "main.py")
    import ast
    ast.parse(prepared)
    assert b"if True:" in prepared
    assert b"importlib.import_module('main')" in _bootstrap()
    assert b".pyc" not in _bootstrap()


def test_cython_preparation_does_not_capture_stylesheet_tag():
    from velto_cli.adapters.mobile.qt.android_release import _prepare_cython_source

    source = b'''from velto.ui import psx
def render():
    return psx('<link rel="stylesheet" href="css/styles.css"/><div>Hello</div>')
'''
    prepared = _prepare_cython_source(source, "components/test.py")
    assert b"lambda: link" not in prepared
    assert b"css/styles.css" in prepared


def test_cython_preparation_keeps_real_link_reference():
    from velto_cli.adapters.mobile.qt.android_release import _prepare_cython_source

    source = b'''from velto.ui import psx
def render(link):
    return psx('<link rel="stylesheet" href="css/styles.css"/><div>{link}</div>')
'''
    prepared = _prepare_cython_source(source, "components/test.py")
    assert b"lambda: link" in prepared


def test_cython_preparation_preserves_numeric_tuple_values():
    from velto_cli.adapters.mobile.qt.android_release import _prepare_cython_source

    source = b'''from __future__ import annotations
def color(visible):
    return (0.62, 0.78) if visible else (0, 0)
'''
    prepared = _prepare_cython_source(source, "psx/renderers/kivy/scroll.py")
    namespace = {}
    exec(compile(prepared, "scroll.py", "exec"), namespace)
    assert namespace["color"](True) == (0.62, 0.78)
    assert namespace["color"](False) == (0, 0)
    assert all(type(item) is int for item in namespace["color"](False))
    assert b"_velto_cython_tuple" in prepared


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


