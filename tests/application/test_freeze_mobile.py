"""Tests for the mobile freeze application boundary and toolchain adapter."""
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from velto_cli.application.use_cases.freeze_mobile import FreezeMobileUseCase
from velto_cli.adapters.mobile.freezer import MobileToolchainFreezer
from velto_cli.domain.mobile import (
    MobileFramework, MobileFreezeRequest, MobileTarget,
)


@pytest.fixture
def project(tmp_path):
    (tmp_path / "pyproject.toml").write_text('[project]\nname="demo"\n')
    (tmp_path / "main.py").write_text("print('hello')\n")
    return tmp_path


def test_use_case_dispatches_kivy_android(project):
    settings = Mock()
    settings.get_optional.side_effect = lambda key, default=None: {
        "framework": "Kivy", "app_name": "Demo", "version": "1.0.0",
        "entry_point": "main.py",
    }.get(key, default)
    builder = Mock()
    builder.freeze.return_value = None
    use_case = FreezeMobileUseCase(builder, settings, Mock())
    use_case.execute(target=MobileTarget.ANDROID, project_root=project)
    request = builder.freeze.call_args.args[0]
    assert request.framework == MobileFramework.KIVY
    assert request.target == MobileTarget.ANDROID
    assert request.app_name == "Demo"


def test_use_case_rejects_tkinter(project):
    settings = Mock()
    settings.get_optional.side_effect = lambda key, default=None: {
        "framework": "Tkinter"
    }.get(key, default)
    builder = Mock()
    with pytest.raises(ValueError, match="Kivy or PySide6"):
        FreezeMobileUseCase(builder, settings, Mock()).execute(
            target=MobileTarget.IOS, project_root=project
        )
    builder.freeze.assert_not_called()


def test_kivy_spec_initialization_is_non_destructive(project):
    adapter = MobileToolchainFreezer(Mock())
    request = MobileFreezeRequest(
        project_root=project, target=MobileTarget.ANDROID,
        framework=MobileFramework.KIVY, app_name="Demo",
        version="1.0.0", init_only=True,
    )
    assert adapter.freeze(request) is None
    assert "requirements = python3,kivy,qyro-engine,velto-core" in (
        project / "buildozer.spec"
    ).read_text()
    with pytest.raises(FileExistsError):
        adapter.freeze(request)


def test_kivy_build_uses_injected_runner(project, monkeypatch):
    (project / "buildozer.spec").write_text("[app]\n")
    bin_dir = project / "bin"
    bin_dir.mkdir()
    apk = bin_dir / "demo.apk"
    apk.write_bytes(b"apk")
    runner = Mock()
    runner.run.return_value = 0
    adapter = MobileToolchainFreezer(runner)
    monkeypatch.setattr(
        "velto_cli.adapters.mobile.freezer.shutil.which",
        lambda name: "/usr/bin/" + name,
    )
    request = MobileFreezeRequest(
        project_root=project, target=MobileTarget.ANDROID,
        framework=MobileFramework.KIVY, app_name="Demo",
        version="1.0.0", debug=True,
    )
    result = adapter.freeze(request)
    assert result.path == apk
    assert result.kind == "apk"
    runner.run.assert_called_once_with(
        ["/usr/bin/buildozer", "android", "debug"], cwd=str(project)
    )


def test_android_windows_requires_wsl2(project, monkeypatch):
    monkeypatch.setattr("velto_cli.adapters.mobile.freezer.sys.platform", "win32")
    request = MobileFreezeRequest(
        project_root=project, target=MobileTarget.ANDROID,
        framework=MobileFramework.KIVY, app_name="Demo", version="1.0.0",
    )
    with pytest.raises(RuntimeError, match="WSL2"):
        MobileToolchainFreezer(Mock()).freeze(request)


def test_ios_linux_requires_mac(project, monkeypatch):
    monkeypatch.setattr("velto_cli.adapters.mobile.freezer.sys.platform", "linux")
    request = MobileFreezeRequest(
        project_root=project, target=MobileTarget.IOS,
        framework=MobileFramework.PYSIDE6, app_name="Demo", version="1.0.0",
    )
    with pytest.raises(RuntimeError, match="macOS"):
        MobileToolchainFreezer(Mock()).freeze(request)


def test_tool_failure_never_returns_artifact(project, monkeypatch):
    (project / "buildozer.spec").write_text("[app]\n")
    monkeypatch.setattr(
        "velto_cli.adapters.mobile.freezer.shutil.which",
        lambda name: "/usr/bin/" + name,
    )
    runner = Mock()
    runner.run.return_value = 2
    request = MobileFreezeRequest(
        project_root=project, target=MobileTarget.ANDROID,
        framework=MobileFramework.KIVY, app_name="Demo",
        version="1.0.0", debug=True,
    )
    with pytest.raises(RuntimeError, match="exit 2"):
        MobileToolchainFreezer(runner).freeze(request)
