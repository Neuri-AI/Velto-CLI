from pathlib import Path
from unittest.mock import Mock

from velto_cli.application.use_cases.bundle import BundleReleaseUseCase
from velto_cli.domain.build import BundleArtifact


def test_bundle_use_case_validates_and_delegates(tmp_path):
    bundler = Mock()
    settings = Mock()
    guards = Mock()
    ui = Mock()

    settings.get_optional.side_effect = lambda key, default=None: {
        "app_name": "MyApp",
        "author": "Acme Team",
        "freeze_dir": "build",
        "version": "2.3.4",
        "bundle": {
            "extra_files": ["README.md"],
            "dmg": {"icon_size": 140},
            "nsis": {"execution_level": "admin"},
        },
    }.get(key, default)

    expected = BundleArtifact(
        output_dir=tmp_path / "release" / "MyApp",
        included_paths=[tmp_path / "release" / "MyApp" / "MyApp.exe"],
        archive_path=tmp_path / "release" / "MyApp.zip",
        package_path=tmp_path / "release" / "MyApp-2.3.4.dmg",
        package_format="dmg",
    )
    bundler.bundle.return_value = expected

    use_case = BundleReleaseUseCase(
        bundler=bundler,
        settings=settings,
        guards=guards,
        ui=ui,
    )

    result = use_case.execute(
        project_root=tmp_path,
        release_dir="release",
        include_resources=True,
        create_archive=True,
        package_format="dmg",
        target_platform="mac",
    )

    assert result is expected
    guards.require_existing_project.assert_called_once_with()
    guards.require_frozen_app.assert_called_once_with()
    bundler.bundle.assert_called_once_with(
        project_root=tmp_path,
        app_name="MyApp",
        app_author="Acme Team",
        freeze_dir="build",
        release_dir="release",
        include_resources=True,
        create_archive=True,
        package_format="dmg",
        target_platform="mac",
        app_version="2.3.4",
        extra_files=["README.md"],
        dmg_options={"icon_size": 140},
        nsis_options={"execution_level": "admin"},
    )
    settings.activate_profile.assert_called_once_with("release")
    ui.progress.assert_called_once()
    ui.success.assert_called_once()
    assert ui.info.call_count >= 1


def test_bundle_use_case_uses_root_name_when_missing_app_name(tmp_path):
    bundler = Mock()
    settings = Mock()
    guards = Mock()
    ui = Mock()

    settings.get_optional.side_effect = lambda key, default=None: {
        "freeze_dir": "build",
        "version": "1.0.0",
        "bundle": {},
    }.get(key, default)

    bundler.bundle.return_value = BundleArtifact(
        output_dir=Path("release") / tmp_path.name,
    )

    use_case = BundleReleaseUseCase(
        bundler=bundler,
        settings=settings,
        guards=guards,
        ui=ui,
    )

    use_case.execute(project_root=tmp_path)

    kwargs = bundler.bundle.call_args.kwargs
    assert kwargs["app_name"] == tmp_path.name
    assert kwargs["app_author"] == "Developer"
    assert kwargs["app_version"] == "1.0.0"
    assert kwargs["extra_files"] == []
    assert kwargs["dmg_options"] == {}
    assert kwargs["nsis_options"] == {}


def test_bundle_use_case_check_runs_preflight(tmp_path):
    bundler = Mock()
    settings = Mock()
    guards = Mock()
    ui = Mock()

    settings.get_optional.side_effect = lambda key, default=None: {
        "app_name": "MyApp",
        "freeze_dir": "build",
        "version": "1.0.0",
        "bundle": {
            "extra_files": ["README.md"],
            "dmg": {"icon_size": 120},
            "nsis": {"install_location": "appdata"},
        },
    }.get(key, default)

    use_case = BundleReleaseUseCase(
        bundler=bundler,
        settings=settings,
        guards=guards,
        ui=ui,
    )

    use_case.check(
        project_root=tmp_path,
        package_format="dmg",
        target_platform="mac",
    )

    bundler.preflight.assert_called_once_with(
        project_root=tmp_path,
        app_name="MyApp",
        freeze_dir="build",
        package_format="dmg",
        target_platform="mac",
        extra_files=["README.md"],
        dmg_options={"icon_size": 120},
        nsis_options={"install_location": "appdata"},
    )
    bundler.bundle.assert_not_called()
    ui.success.assert_called_once()
