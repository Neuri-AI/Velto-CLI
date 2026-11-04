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


def test_pyside_android_init_resolves_public_archive_wheels(project, monkeypatch):
    """Qt credentials/qtpip should not be involved in the normal workflow."""
    runner = Mock()
    def run_qt(command, cwd):
        (project / "pysidedeploy.spec").write_text("[app]\\n")
        return 0
    runner.run.side_effect = run_qt
    pyside = project / "pyside6-6.12.0-6.12.0-cp310-abi3-android_aarch64.whl"
    shiboken = project / "shiboken6-6.12.0-6.12.0-cp310-abi3-android_aarch64.whl"
    pyside.touch()
    shiboken.touch()

    archive = Mock()
    archive.resolve.return_value = (pyside, shiboken)
    monkeypatch.setattr(
        "velto_cli.adapters.mobile.freezer.QtAndroidWheelArchive",
        lambda: archive,
    )
    monkeypatch.setattr("velto_cli.adapters.mobile.freezer.Path.home", lambda: project)
    monkeypatch.setattr(
        "velto_cli.adapters.mobile.freezer.distribution_version",
        lambda name: "6.12.0",
    )
    monkeypatch.setattr(
        "velto_cli.adapters.mobile.freezer.shutil.which",
        lambda name: None if name == "qtpip" else "/usr/bin/" + name,
    )
    request = MobileFreezeRequest(
        project_root=project, target=MobileTarget.ANDROID,
        framework=MobileFramework.PYSIDE6, app_name="Demo",
        version="1.0.0", init_only=True,
    )
    assert MobileToolchainFreezer(runner).freeze(request) is None
    archive.resolve.assert_called_once_with(
        "6.12.0", "aarch64",
        project / ".velto" / "mobile" / "pyside6" / "android" / "aarch64" / "6.12.0" / "public",
    )
    runner.run.assert_called_once()
    args = runner.run.call_args.args[0]
    assert args[0] == "/usr/bin/pyside6-android-deploy"
    assert args[args.index("--wheel-pyside") + 1] == str(pyside)
    assert args[args.index("--wheel-shiboken") + 1] == str(shiboken)


def test_pyside_android_init_passes_wheels_to_qt(project, monkeypatch):
    wheel_pyside = project / "PySide6-test.whl"
    wheel_shiboken = project / "shiboken6-test.whl"
    wheel_pyside.touch()
    wheel_shiboken.touch()
    runner = Mock()
    runner.run.return_value = 0

    def run_qt(command, cwd):
        (project / "pysidedeploy.spec").write_text("[app]\\n")
        return 0

    runner.run.side_effect = run_qt
    monkeypatch.setattr(
        "velto_cli.adapters.mobile.freezer.shutil.which",
        lambda name: "/usr/bin/" + name,
    )
    monkeypatch.setattr(
        "velto_cli.adapters.mobile.freezer.distribution_version",
        lambda name: "6.12.0",
    )
    # Qt wheel filename check prevents passing desktop or version-mismatched wheels.
    wheel_pyside.rename(project / "PySide6-6.12.0-cp311-abi3-android_aarch64.whl")
    wheel_shiboken.rename(project / "shiboken6-6.12.0-cp311-abi3-android_aarch64.whl")
    wheel_pyside = project / "PySide6-6.12.0-cp311-abi3-android_aarch64.whl"
    wheel_shiboken = project / "shiboken6-6.12.0-cp311-abi3-android_aarch64.whl"
    request = MobileFreezeRequest(
        project_root=project, target=MobileTarget.ANDROID,
        framework=MobileFramework.PYSIDE6, app_name="Demo", version="1.0.0",
        init_only=True, wheel_pyside=wheel_pyside, wheel_shiboken=wheel_shiboken,
    )
    assert MobileToolchainFreezer(runner).freeze(request) is None
    args = runner.run.call_args.args[0]
    assert args[:4] == ["/usr/bin/pyside6-android-deploy", "--init", "--name", "Demo"]
    assert args[args.index("--wheel-pyside") + 1] == str(wheel_pyside)
    assert args[args.index("--wheel-shiboken") + 1] == str(wheel_shiboken)


def test_mobile_use_case_passes_validated_qt_wheel_paths(project):
    settings = Mock()
    settings.get_optional.side_effect = lambda key, default=None: {
        "framework": "PySide6", "app_name": "Demo",
    }.get(key, default)
    pyside = project / "PySide6-android.whl"
    shiboken = project / "shiboken6-android.whl"
    pyside.touch()
    shiboken.touch()
    builder = Mock()
    builder.freeze.return_value = None
    FreezeMobileUseCase(builder, settings, Mock()).execute(
        target=MobileTarget.ANDROID, project_root=project, init_only=True,
        wheel_pyside=pyside.name, wheel_shiboken=shiboken.name,
    )
    request = builder.freeze.call_args.args[0]
    assert request.wheel_pyside == pyside
    assert request.wheel_shiboken == shiboken


def test_pyside_prefers_licensed_qtpip_when_available(project, monkeypatch):
    runner = Mock()
    cache = (project / ".velto" / "mobile" / "pyside6"
             / "android" / "aarch64" / "6.12.0" / "commercial")
    def qtpip_succeeds(command, cwd):
        if command[0] == "/usr/bin/qtpip":
            cache.mkdir(parents=True, exist_ok=True)
            (cache / "PySide6-6.12.0-6.12.0-cp310-abi3-android_aarch64.whl").touch()
            (cache / "shiboken6-6.12.0-6.12.0-cp310-abi3-android_aarch64.whl").touch()
        return 0
    runner.run_capture.side_effect = lambda command, cwd: (qtpip_succeeds(command, cwd), "[INFO] downloaded")
    archive = Mock()
    monkeypatch.setattr(
        "velto_cli.adapters.mobile.freezer.QtAndroidWheelArchive", lambda: archive
    )
    monkeypatch.setattr("velto_cli.adapters.mobile.freezer.Path.home", lambda: project)
    monkeypatch.setattr(
        "velto_cli.adapters.mobile.freezer.distribution_version",
        lambda name: "6.12.0",
    )
    monkeypatch.setattr(
        "velto_cli.adapters.mobile.freezer.shutil.which",
        lambda name: "/usr/bin/" + name,
    )
    request = MobileFreezeRequest(
        project_root=project, target=MobileTarget.ANDROID,
        framework=MobileFramework.PYSIDE6, app_name="Demo",
        version="1.0.0", init_only=True,
    )
    wheels = MobileToolchainFreezer(runner)._qt_android_wheels(request)
    assert all(path.parent == cache for path in wheels)
    runner.run_capture.assert_called_once_with(
        ["/usr/bin/qtpip", "download", "PySide6", "--android",
         "--arch", "aarch64"], cwd=str(cache)
    )
    archive.resolve.assert_not_called()


def test_pyside_falls_back_to_public_on_missing_commercial_license(project, monkeypatch):
    runner = Mock()
    runner.run_capture.return_value = (1, "[ERROR] Commercial License NOT found")
    archive = Mock()
    expected = (project / "community-PySide6.whl",
                project / "community-shiboken6.whl")
    archive.resolve.return_value = expected
    monkeypatch.setattr(
        "velto_cli.adapters.mobile.freezer.QtAndroidWheelArchive", lambda: archive
    )
    monkeypatch.setattr("velto_cli.adapters.mobile.freezer.Path.home", lambda: project)
    monkeypatch.setattr(
        "velto_cli.adapters.mobile.freezer.distribution_version",
        lambda name: "6.12.0",
    )
    monkeypatch.setattr(
        "velto_cli.adapters.mobile.freezer.shutil.which",
        lambda name: "/usr/bin/" + name,
    )
    request = MobileFreezeRequest(
        project_root=project, target=MobileTarget.ANDROID,
        framework=MobileFramework.PYSIDE6, app_name="Demo",
        version="1.0.0", init_only=True,
    )
    assert MobileToolchainFreezer(runner)._qt_android_wheels(request) == expected
    archive.resolve.assert_called_once_with(
        "6.12.0", "aarch64",
        project / ".velto" / "mobile" / "pyside6"
        / "android" / "aarch64" / "6.12.0" / "public",
    )



def test_qtpip_zero_exit_without_wheels_uses_public_archive(project, monkeypatch):
    """qtpip can exit 0 after printing 'Commercial License NOT found'."""
    runner = Mock()
    runner.run_capture.return_value = (
        0, "[ERROR] Commercial License NOT found"
    )  # qtpip sometimes exits successfully despite failed authorization
    archive = Mock()
    pyside = project / "public-PySide6.whl"
    shiboken = project / "public-shiboken6.whl"
    archive.resolve.return_value = (pyside, shiboken)
    monkeypatch.setattr(
        "velto_cli.adapters.mobile.freezer.QtAndroidWheelArchive",
        lambda: archive,
    )
    monkeypatch.setattr("velto_cli.adapters.mobile.freezer.Path.home", lambda: project)
    monkeypatch.setattr(
        "velto_cli.adapters.mobile.freezer.distribution_version",
        lambda name: "6.12.0",
    )
    monkeypatch.setattr(
        "velto_cli.adapters.mobile.freezer.shutil.which",
        lambda name: "/usr/bin/" + name,
    )
    request = MobileFreezeRequest(
        project_root=project, target=MobileTarget.ANDROID,
        framework=MobileFramework.PYSIDE6, app_name="Demo",
        version="1.0.0", init_only=True,
    )
    assert MobileToolchainFreezer(runner)._qt_android_wheels(request) == (
        pyside, shiboken
    )
    runner.run.assert_called_once()
    archive.resolve.assert_called_once_with(
        "6.12.0", "aarch64",
        project / ".velto" / "mobile" / "pyside6" / "android"
        / "aarch64" / "6.12.0" / "public",
    )


def test_unlicensed_qtpip_is_warning_with_live_progress(project, monkeypatch):
    """No raw [ERROR] line escapes; the user sees an ongoing download spinner."""
    from contextlib import nullcontext
    runner = Mock()
    runner.run_capture.return_value = (
        0, "[ERROR] Commercial License NOT found\\n"
           "[ERROR] No Commercial License found.",
    )
    ui = Mock()
    ui.status.return_value = nullcontext()
    archive = Mock()
    archive.resolve.return_value = (project / "pyside.whl", project / "shiboken.whl")
    monkeypatch.setattr(
        "velto_cli.adapters.mobile.freezer.QtAndroidWheelArchive", lambda: archive,
    )
    monkeypatch.setattr(
        "velto_cli.adapters.mobile.freezer.Path.home", lambda: project,
    )
    monkeypatch.setattr(
        "velto_cli.adapters.mobile.freezer.distribution_version",
        lambda name: "6.12.0",
    )
    monkeypatch.setattr(
        "velto_cli.adapters.mobile.freezer.shutil.which",
        lambda name: "/usr/bin/" + name,
    )
    request = MobileFreezeRequest(
        project_root=project, target=MobileTarget.ANDROID,
        framework=MobileFramework.PYSIDE6, app_name="Demo",
        version="1.0.0", init_only=True,
    )
    assert MobileToolchainFreezer(runner, ui=ui)._qt_android_wheels(request) == (
        project / "pyside.whl", project / "shiboken.whl"
    )
    ui.warning.assert_called_once()
    assert "Qt Commercial license not available" in ui.warning.call_args.args[0]
    assert ui.status.call_count == 2
    assert "Checking commercial" in ui.status.call_args_list[0].args[0]
    assert "Downloading/verifying public" in ui.status.call_args_list[1].args[0]
    runner.run.assert_not_called()
