import zipfile
from pathlib import Path

import pytest

from qyro_cli.adapters.package.release_bundler import ReleaseBundler
from qyro_cli.domain.errors import (
    FrozenAppNotFoundError,
    MissingDependencyError,
    QyroError,
)


def test_bundle_copies_onedir_layout_and_resources(tmp_path):
    project_root = tmp_path
    freeze_dir = project_root / "build" / "MyApp"
    freeze_dir.mkdir(parents=True)
    (freeze_dir / "MyApp.exe").write_text("binary", encoding="utf-8")
    (freeze_dir / "_internal").mkdir(parents=True)

    resources = project_root / "resources"
    resources.mkdir(parents=True)
    (resources / "config.json").write_text("{}", encoding="utf-8")

    bundler = ReleaseBundler()
    artifact = bundler.bundle(
        project_root=project_root,
        app_name="MyApp",
        app_author="Developer",
        freeze_dir="build",
        release_dir="release",
        include_resources=True,
        create_archive=False,
        package_format="dir",
        target_platform="linux",
        app_version="1.0.0",
        extra_files=[],
        dmg_options={},
        nsis_options={},
    )

    assert artifact.output_dir == project_root / "release" / "MyApp"
    assert (artifact.output_dir / "MyApp.exe").exists()
    assert (artifact.output_dir / "_internal").exists()
    assert (artifact.output_dir / "resources" / "config.json").exists()
    assert artifact.archive_path is None
    assert artifact.package_path is None


def test_bundle_can_create_zip_archive_for_onefile(tmp_path):
    project_root = tmp_path
    freeze_root = project_root / "build"
    freeze_root.mkdir(parents=True)
    (freeze_root / "MyApp.exe").write_text("binary", encoding="utf-8")

    bundler = ReleaseBundler()
    artifact = bundler.bundle(
        project_root=project_root,
        app_name="MyApp",
        app_author="Developer",
        freeze_dir="build",
        release_dir="release",
        include_resources=False,
        create_archive=True,
        package_format="zip",
        target_platform="linux",
        app_version="1.0.0",
        extra_files=[],
        dmg_options={},
        nsis_options={},
    )

    assert artifact.archive_path is not None
    assert artifact.archive_path.exists()
    assert artifact.package_path is not None
    assert artifact.package_path.suffix == ".zip"
    with zipfile.ZipFile(artifact.archive_path, "r") as zf:
        assert "MyApp.exe" in zf.namelist()


def test_bundle_raises_when_freeze_output_is_missing(tmp_path):
    bundler = ReleaseBundler()

    with pytest.raises(FrozenAppNotFoundError):
        bundler.bundle(
            project_root=tmp_path,
            app_name="MyApp",
            app_author="Developer",
            freeze_dir="build",
            release_dir="release",
            include_resources=False,
            create_archive=False,
            package_format="dir",
            target_platform="linux",
            app_version="1.0.0",
            extra_files=[],
            dmg_options={},
            nsis_options={},
        )


def test_package_dmg_uses_staged_source_dir_with_only_app(tmp_path, monkeypatch):
    release_root = tmp_path / "release"
    output_root = release_root / "MyApp"
    app_bundle = output_root / "MyApp.app"
    app_contents = app_bundle / "Contents" / "MacOS"

    app_contents.mkdir(parents=True)
    (app_contents / "MyApp").write_text("binary", encoding="utf-8")
    (output_root / "_internal").mkdir(parents=True)

    commands: list[list[str]] = []
    observed_source_dir: Path | None = None

    def fake_run(command, cwd):
        nonlocal observed_source_dir
        commands.append(command)
        observed_source_dir = Path(command[-1])
        assert (observed_source_dir / "MyApp.app").exists()
        assert not (observed_source_dir / "_internal").exists()

    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/create-dmg" if name == "create-dmg" else None)

    bundler = ReleaseBundler()
    monkeypatch.setattr(bundler, "_run", fake_run)

    bundler._package_dmg(
        output_root=output_root,
        release_root=release_root,
        app_name="MyApp",
        app_version="1.0.0",
        project_root=tmp_path,
        dmg_options={},
    )

    assert commands, "Expected create-dmg command to be executed"
    source_dir = Path(commands[0][-1])
    assert source_dir.name == ".MyApp-dmg-src"
    assert source_dir != output_root
    assert source_dir.exists() is False
    assert observed_source_dir is not None


def test_package_dmg_copies_dmg_extra_files_when_create_dmg_is_available(tmp_path, monkeypatch):
    release_root = tmp_path / "release"
    output_root = release_root / "MyApp"
    app_bundle = output_root / "MyApp.app" / "Contents" / "MacOS"
    app_bundle.mkdir(parents=True)
    (app_bundle / "MyApp").write_text("binary", encoding="utf-8")
    notes = tmp_path / "docs" / "RELEASE_NOTES.md"
    notes.parent.mkdir()
    notes.write_text("notes", encoding="utf-8")

    def fake_run(command, cwd):
        source_dir = Path(command[-1])
        assert (source_dir / "MyApp.app").exists()
        assert (source_dir / "README.md").read_text(encoding="utf-8") == "notes"
        assert (source_dir / "RELEASE_NOTES.md").read_text(encoding="utf-8") == "notes"

    monkeypatch.setattr(
        "shutil.which",
        lambda name: "/usr/bin/create-dmg" if name == "create-dmg" else None,
    )
    bundler = ReleaseBundler()
    monkeypatch.setattr(bundler, "_run", fake_run)

    bundler._package_dmg(
        output_root=output_root,
        release_root=release_root,
        app_name="MyApp",
        app_version="1.0.0",
        project_root=tmp_path,
        dmg_options={
            "extra_files": [
                {"source": "docs/RELEASE_NOTES.md", "destination": "README.md"},
                "docs/RELEASE_NOTES.md",
            ]
        },
    )


def test_bundle_copies_extra_files_from_release_settings(tmp_path):
    project_root = tmp_path
    freeze_root = project_root / "build"
    freeze_root.mkdir(parents=True)
    (freeze_root / "MyApp.exe").write_text("binary", encoding="utf-8")
    (project_root / "README.md").write_text("hello", encoding="utf-8")

    docs_dir = project_root / "docs"
    docs_dir.mkdir(parents=True)
    (docs_dir / "NOTES.txt").write_text("notes", encoding="utf-8")

    bundler = ReleaseBundler()
    artifact = bundler.bundle(
        project_root=project_root,
        app_name="MyApp",
        app_author="Developer",
        freeze_dir="build",
        release_dir="release",
        include_resources=False,
        create_archive=False,
        package_format="dir",
        target_platform="linux",
        app_version="1.0.0",
        extra_files=[
            "README.md",
            {"source": "docs/NOTES.txt", "destination": "extras/NOTES.txt"},
        ],
        dmg_options={},
        nsis_options={},
    )

    assert (artifact.output_dir / "README.md").exists()
    assert (artifact.output_dir / "extras" / "NOTES.txt").exists()


def test_package_nsis_renders_external_template(tmp_path, monkeypatch):
    release_root = tmp_path / "release"
    output_root = release_root / "My App"
    output_root.mkdir(parents=True)
    (output_root / "My App.exe").write_text("binary", encoding="utf-8")
    install_icon = tmp_path / "resources" / "base" / "install.ico"
    uninstall_icon = tmp_path / "resources" / "base" / "uninstall.ico"
    welcome_bitmap = tmp_path / "resources" / "base" / "welcome.bmp"
    install_icon.parent.mkdir(parents=True)
    install_icon.write_text("ico", encoding="utf-8")
    uninstall_icon.write_text("ico", encoding="utf-8")
    welcome_bitmap.write_text("bmp", encoding="utf-8")

    commands: list[list[str]] = []

    def fake_run(command, cwd):
        commands.append(command)

    monkeypatch.setattr(
        "shutil.which",
        lambda name: "C:\\Program Files\\NSIS\\makensis.exe" if name == "makensis" else None,
    )

    bundler = ReleaseBundler()
    monkeypatch.setattr(bundler, "_run", fake_run)

    installer = bundler._package_nsis(
        output_root=output_root,
        release_root=release_root,
        app_name="My App",
        app_author='Team "A"',
        app_version="2.1.0",
        project_root=tmp_path,
        nsis_options={
            "icons": {
                "install": "resources/base/install.ico",
                "uninstall": "resources/base/uninstall.ico",
            },
            "welcome_bitmap": "resources/base/welcome.bmp",
            "install_location": "appdata",
            "execution_level": "admin",
        },
    )

    assert installer == release_root / "My App-2.1.0-setup.exe"
    assert commands, "Expected makensis command"

    nsi_path = output_root / "My App-installer.nsi"
    assert nsi_path.exists()
    content = nsi_path.read_text(encoding="utf-8")
    assert "Name \"My App\"" in content
    assert 'WriteRegStr SHCTX "${UNINST_KEY}" "Publisher" "Team $\\"A$\\\""' in content
    assert "OutFile \"..\\My App-2.1.0-setup.exe\"" in content
    assert '!define VERSION "2.1.0.0"' in content
    assert 'File /r /x ${app_name}-installer.nsi /x README.md /x install.ico /x uninstall.ico ..\\${app_name}\\*'.replace("${app_name}", "My App") in content
    assert '!define MUI_ICON "' in content
    assert '!define MUI_UNICON "' in content
    assert '!define MUI_WELCOMEFINISHPAGE_BITMAP "' in content
    assert "!define MULTIUSER_EXECUTIONLEVEL admin" in content
    assert 'StrCpy $InstDir "$LOCALAPPDATA\\My App"' in content


def test_package_dmg_applies_visual_options(tmp_path, monkeypatch):
    release_root = tmp_path / "release"
    output_root = release_root / "MyApp"
    app_bundle = output_root / "MyApp.app"
    app_contents = app_bundle / "Contents" / "MacOS"
    app_contents.mkdir(parents=True)
    (app_contents / "MyApp").write_text("binary", encoding="utf-8")

    background = tmp_path / "assets" / "dmg-background.png"
    background.parent.mkdir(parents=True)
    background.write_text("png", encoding="utf-8")

    commands: list[list[str]] = []

    def fake_run(command, cwd):
        commands.append(command)

    monkeypatch.setattr(
        "shutil.which",
        lambda name: "/usr/bin/create-dmg" if name == "create-dmg" else None,
    )

    bundler = ReleaseBundler()
    monkeypatch.setattr(bundler, "_run", fake_run)

    bundler._package_dmg(
        output_root=output_root,
        release_root=release_root,
        app_name="MyApp",
        app_version="1.0.0",
        project_root=tmp_path,
        dmg_options={
            "window": {"x": 200, "y": 120},
            "window_size": {"width": 660, "height": 420},
            "icon_size": 120,
            "app_position": {"x": 180, "y": 180},
            "applications_position": {"x": 480, "y": 180},
            "background": "assets/dmg-background.png",
        },
    )

    assert commands, "Expected create-dmg command"
    command = commands[0]
    assert "--window-pos" in command
    assert "--window-size" in command
    assert "--icon-size" in command
    assert "--icon" in command
    assert "--app-drop-link" in command
    assert "--background" in command


def test_package_dmg_raises_when_custom_options_without_create_dmg(tmp_path, monkeypatch):
    release_root = tmp_path / "release"
    output_root = release_root / "MyApp"
    app_bundle = output_root / "MyApp.app"
    app_contents = app_bundle / "Contents" / "MacOS"
    app_contents.mkdir(parents=True)
    (app_contents / "MyApp").write_text("binary", encoding="utf-8")

    monkeypatch.setattr("shutil.which", lambda name: None)

    bundler = ReleaseBundler()

    with pytest.raises(QyroError):
        bundler._package_dmg(
            output_root=output_root,
            release_root=release_root,
            app_name="MyApp",
            app_version="1.0.0",
            project_root=tmp_path,
            dmg_options={"icon_size": 120},
        )


def test_package_dmg_extra_files_require_create_dmg(tmp_path, monkeypatch):
    release_root = tmp_path / "release"
    output_root = release_root / "MyApp"
    app_bundle = output_root / "MyApp.app" / "Contents" / "MacOS"
    app_bundle.mkdir(parents=True)
    (app_bundle / "MyApp").write_text("binary", encoding="utf-8")
    (tmp_path / "README.md").write_text("notes", encoding="utf-8")

    monkeypatch.setattr("shutil.which", lambda name: None)

    with pytest.raises(QyroError, match="create-dmg"):
        ReleaseBundler()._package_dmg(
            output_root=output_root,
            release_root=release_root,
            app_name="MyApp",
            app_version="1.0.0",
            project_root=tmp_path,
            dmg_options={"extra_files": ["README.md"]},
        )


def test_package_dmg_raises_when_background_path_missing(tmp_path, monkeypatch):
    release_root = tmp_path / "release"
    output_root = release_root / "MyApp"
    app_bundle = output_root / "MyApp.app"
    app_contents = app_bundle / "Contents" / "MacOS"
    app_contents.mkdir(parents=True)
    (app_contents / "MyApp").write_text("binary", encoding="utf-8")

    monkeypatch.setattr(
        "shutil.which",
        lambda name: "/usr/bin/create-dmg" if name == "create-dmg" else None,
    )

    bundler = ReleaseBundler()

    with pytest.raises(FileNotFoundError):
        bundler._package_dmg(
            output_root=output_root,
            release_root=release_root,
            app_name="MyApp",
            app_version="1.0.0",
            project_root=tmp_path,
            dmg_options={"background": "assets/missing-bg.png"},
        )


def test_preflight_fails_when_extra_file_missing(tmp_path):
    build_dir = tmp_path / "build"
    build_dir.mkdir(parents=True)
    (build_dir / "MyApp.app").mkdir(parents=True)

    bundler = ReleaseBundler()

    with pytest.raises(FileNotFoundError):
        bundler.preflight(
            project_root=tmp_path,
            app_name="MyApp",
            freeze_dir="build",
            package_format="dir",
            target_platform="linux",
            extra_files=["docs/RELEASE_NOTES.md"],
            dmg_options={},
            nsis_options={},
        )


def test_preflight_fails_when_dmg_customized_without_create_dmg(tmp_path, monkeypatch):
    build_dir = tmp_path / "build"
    app_bundle = build_dir / "MyApp.app" / "Contents" / "MacOS"
    app_bundle.mkdir(parents=True)
    (app_bundle / "MyApp").write_text("binary", encoding="utf-8")

    monkeypatch.setattr("shutil.which", lambda name: None)

    bundler = ReleaseBundler()
    with pytest.raises(QyroError):
        bundler.preflight(
            project_root=tmp_path,
            app_name="MyApp",
            freeze_dir="build",
            package_format="dmg",
            target_platform="mac",
            extra_files=[],
            dmg_options={"icon_size": 120},
            nsis_options={},
        )


def test_preflight_fails_when_nsis_option_path_is_missing(tmp_path, monkeypatch):
    build_dir = tmp_path / "build"
    build_dir.mkdir(parents=True)
    (build_dir / "MyApp.exe").write_text("binary", encoding="utf-8")

    monkeypatch.setattr(
        "shutil.which",
        lambda name: "C:\\Program Files\\NSIS\\makensis.exe" if name == "makensis" else None,
    )

    bundler = ReleaseBundler()
    with pytest.raises(FileNotFoundError):
        bundler.preflight(
            project_root=tmp_path,
            app_name="MyApp",
            freeze_dir="build",
            package_format="nsis",
            target_platform="windows",
            extra_files=[],
            dmg_options={},
            nsis_options={
                "icons": {
                    "install": "resources/base/missing-install.ico",
                }
            },
        )


def test_preflight_fails_when_nsis_option_values_are_invalid(tmp_path, monkeypatch):
    build_dir = tmp_path / "build"
    build_dir.mkdir(parents=True)
    (build_dir / "MyApp.exe").write_text("binary", encoding="utf-8")

    monkeypatch.setattr(
        "shutil.which",
        lambda name: "C:\\Program Files\\NSIS\\makensis.exe" if name == "makensis" else None,
    )

    bundler = ReleaseBundler()
    with pytest.raises(ValueError):
        bundler.preflight(
            project_root=tmp_path,
            app_name="MyApp",
            freeze_dir="build",
            package_format="nsis",
            target_platform="windows",
            extra_files=[],
            dmg_options={},
            nsis_options={"install_location": "system32"},
        )
