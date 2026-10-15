
from unittest.mock import Mock

import pytest

from velto_cli.application.guards import ProjectGuards
from velto_cli.domain.errors import (
    FrozenAppNotFoundError,
    InstallerNotFoundError,
    NotAProjectError,
)


class TestProjectGuards:
    def test_require_existing_project_passes_when_base_settings_exist(self):
        files = Mock()
        settings = Mock()

        files.exists.return_value = True

        guards = ProjectGuards(files, settings)

        guards.require_existing_project()

        files.exists.assert_called_once_with(
            "pyproject.toml"
        )

    def test_require_existing_project_raises_when_base_settings_do_not_exist(
        self,
    ):
        files = Mock()
        settings = Mock()

        files.exists.return_value = False

        guards = ProjectGuards(files, settings)

        with pytest.raises(NotAProjectError):
            guards.require_existing_project()

        files.exists.assert_called_once_with(
            "pyproject.toml"
        )

    def test_require_frozen_app_passes_when_freeze_directory_exists(self):
        files = Mock()
        settings = Mock()

        files.exists.side_effect = [True, True]
        settings.get_optional.return_value = "target"

        guards = ProjectGuards(files, settings)

        guards.require_frozen_app()

        assert files.exists.call_args_list[0].args == (
            "pyproject.toml",
        )
        assert files.exists.call_args_list[1].args == ("target",)

        settings.get_optional.assert_called_once_with(
            "freeze_dir",
            "build",
        )

    def test_require_frozen_app_uses_configured_freeze_directory(self):
        files = Mock()
        settings = Mock()

        files.exists.side_effect = [True, True]
        settings.get_optional.return_value = "custom/freeze"

        guards = ProjectGuards(files, settings)

        guards.require_frozen_app()

        assert files.exists.call_args_list[1].args == (
            "custom/freeze",
        )

    def test_require_frozen_app_raises_when_freeze_directory_is_missing(
        self,
    ):
        files = Mock()
        settings = Mock()

        files.exists.side_effect = [True, False]
        settings.get_optional.return_value = "target"

        guards = ProjectGuards(files, settings)

        with pytest.raises(FrozenAppNotFoundError) as exc_info:
            guards.require_frozen_app()

        assert exc_info.value.expected_path == "target"

    def test_require_installer_passes_when_installer_exists(self):
        files = Mock()
        settings = Mock()

        files.exists.side_effect = [True, True]
        settings.get.return_value = "installer.exe"

        guards = ProjectGuards(files, settings)

        guards.require_installer()

        assert files.exists.call_args_list[0].args == (
            "pyproject.toml",
        )
        assert files.exists.call_args_list[1].args == (
            "target/installer.exe",
        )

        settings.get.assert_called_once_with("installer")

    def test_require_installer_raises_when_installer_is_missing(self):
        files = Mock()
        settings = Mock()

        files.exists.side_effect = [True, False]
        settings.get.return_value = "installer.exe"

        guards = ProjectGuards(files, settings)

        with pytest.raises(InstallerNotFoundError) as exc_info:
            guards.require_installer()

        assert exc_info.value.expected_path == "target/installer.exe"

