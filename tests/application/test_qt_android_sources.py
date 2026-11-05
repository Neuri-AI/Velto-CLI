"""Source staging lifecycle for the Qt Android application's private archive."""
from pathlib import Path

import pytest

from velto_cli.adapters.mobile.qt import android_sources


def test_sources_are_staged_without_native_extensions_and_removed(tmp_path, monkeypatch):
    sources = tmp_path / "installed"
    for name in android_sources.PACKAGES:
        pkg = sources / name
        pkg.mkdir(parents=True)
        (pkg / "__init__.py").write_text("VERSION = 1\n")
        (pkg / "native.so").write_bytes(b"macOS library -- must not ship")
        (pkg / "code.py").write_text("value = 1\n")
        (pkg / "__pycache__").mkdir()
        (pkg / "__pycache__" / "code.pyc").write_bytes(b"bytecode")
    monkeypatch.setattr(
        android_sources,
        "_source_package",
        lambda name: sources / name,
    )
    app = tmp_path / "app"
    app.mkdir()
    with android_sources.staged_velto_sources(app, enabled=True):
        for name in android_sources.PACKAGES:
            assert (app / name / "__init__.py").is_file()
            assert (app / name / "code.py").is_file()
            assert not (app / name / "native.so").exists()
            assert not (app / name / "__pycache__").exists()
    assert all(not (app / name).exists() for name in android_sources.PACKAGES)


def test_staging_cleans_up_if_deployment_raises(tmp_path, monkeypatch):
    sources = tmp_path / "source"
    sources.mkdir()
    (sources / "__init__.py").write_text("")
    monkeypatch.setattr(android_sources, "_source_package", lambda name: sources)
    app = tmp_path / "app"
    app.mkdir()
    with pytest.raises(RuntimeError, match="deploy crashed"):
        with android_sources.staged_velto_sources(app, enabled=True):
            raise RuntimeError("deploy crashed")
    assert all(not (app / name).exists() for name in android_sources.PACKAGES)


def test_existing_project_modules_are_not_overwritten(tmp_path, monkeypatch):
    existing = tmp_path / "velto"
    existing.mkdir()
    (existing / "__init__.py").write_text("USER DATA")
    source = tmp_path / "source"
    source.mkdir()
    (source / "__init__.py").write_text("VENDORED DATA")
    monkeypatch.setattr(android_sources, "_source_package", lambda name: source)
    with pytest.raises(RuntimeError, match="already contains: velto"):
        with android_sources.staged_velto_sources(tmp_path, enabled=True):
            pass
    assert (existing / "__init__.py").read_text() == "USER DATA"


def test_no_staging_for_other_frameworks(tmp_path):
    with android_sources.staged_velto_sources(tmp_path, enabled=False):
        assert not (tmp_path / "psx").exists()


def test_pyside_build_stages_velto_around_deployment(tmp_path, monkeypatch):
    from contextlib import contextmanager
    from velto_cli.adapters.mobile.freezer import MobileToolchainFreezer
    from velto_cli.domain.mobile import (
        MobileFramework, MobileFreezeRequest, MobileTarget,
    )

    (tmp_path / "main.py").write_text("from velto import VeltoContext\\n")
    events = []

    @contextmanager
    def fake_staging(root, *, enabled):
        events.append(("enter", root, enabled))
        yield enabled
        events.append(("exit", root, enabled))

    monkeypatch.setattr(
        "velto_cli.adapters.mobile.qt.android.staged_velto_sources",
        fake_staging,
    )

    def fake_deploy(self, request):
        assert events == [("enter", tmp_path, True)]
        return "artifact"

    monkeypatch.setattr(MobileToolchainFreezer, "_pyside_android_impl", fake_deploy)
    request = MobileFreezeRequest(
        project_root=tmp_path, target=MobileTarget.ANDROID,
        framework=MobileFramework.PYSIDE6,
        app_name="Demo", version="1.0", entry_point="main.py",
    )
    assert MobileToolchainFreezer(None)._pyside_android(request) == "artifact"
    assert events == [
        ("enter", tmp_path, True),
        ("exit", tmp_path, True),
    ]
