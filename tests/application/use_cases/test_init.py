from pathlib import Path
from unittest.mock import Mock

import pytest

from velto_cli.application.use_cases.init import InitProjectUseCase
from velto_cli.domain.errors import ProjectAlreadyExistsError
from velto_cli.domain.project import Binding, TargetPlatform
from velto_cli.domain.version import Version


def make_use_case():
    ui = Mock()
    fs = Mock()
    dependencies = Mock()
    templates = Mock()
    settings = Mock()

    use_case = InitProjectUseCase(
        ui=ui,
        fs=fs,
        dependencies=dependencies,
        templates=templates,
        settings_repo=settings,
    )

    return use_case, ui, fs, dependencies, templates, settings


def configure_common_answers(ui, *, include_binding=True, platform="Desktop (x86_64)"):
    if include_binding:
        ui.ask_choice.side_effect = [platform, "PySide6"]
    else:
        ui.ask_choice.side_effect = [platform]

    ui.ask_text.side_effect = [
        "MyApp",
        "1.0.0",
        "John Doe",
    ]
    ui.ask_multi_choice.return_value = []
    ui.confirm.return_value = True


class TestInitProjectUseCase:
    def test_execute_raises_when_project_already_exists(self, tmp_path):
        use_case, ui, fs, _dependencies, templates, _settings = make_use_case()

        fs.exists.return_value = True

        with pytest.raises(ProjectAlreadyExistsError) as exc_info:
            use_case.execute(str(tmp_path))

        assert exc_info.value.path == str(tmp_path.resolve())
        ui.welcome.assert_not_called()
        templates.resolve_template.assert_not_called()
        fs.copy_tree.assert_not_called()

    def test_execute_uses_preselected_binding(self, tmp_path):
        use_case, ui, fs, dependencies, templates, _settings = make_use_case()

        fs.exists.return_value = False
        fs.current_user.return_value = "John Doe"
        templates.resolve_template.return_value = Path("/templates/pyside6")
        configure_common_answers(ui, include_binding=False)

        use_case.execute(
            target_dir=str(tmp_path),
            default_binding="PySide6",
        )

        choices = [call.args[0] for call in ui.ask_choice.call_args_list]

        assert choices == ["Select target platform"]
        templates.resolve_template.assert_called_once_with(
            binding=Binding.PYSIDE6,
            target_platform=TargetPlatform.X86_64,
            version=None,
        )
        dependencies.install.assert_called_once_with(str(tmp_path.resolve()))

    def test_execute_rejects_binding_not_supported_by_platform(self, tmp_path):
        use_case, ui, fs, _dependencies, _templates, _settings = make_use_case()

        fs.exists.return_value = False
        ui.ask_choice.return_value = "iPhone"

        with pytest.raises(ValueError, match="not available"):
            use_case.execute(
                target_dir=str(tmp_path),
                default_binding="PyQt6",
            )

    def test_execute_runs_dependency_install_after_scaffolding(self, tmp_path):
        use_case, ui, fs, dependencies, templates, _settings = make_use_case()

        fs.exists.return_value = False
        fs.current_user.return_value = "John Doe"
        templates.resolve_template.return_value = Path("/templates/pyside6")
        configure_common_answers(ui)

        use_case.execute(target_dir=str(tmp_path))

        dependencies.install.assert_called_once_with(str(tmp_path.resolve()))

    def test_execute_aborts_before_installing_or_resolving_template(self, tmp_path):
        use_case, ui, fs, dependencies, templates, _settings = make_use_case()

        fs.exists.return_value = False
        fs.current_user.return_value = "John Doe"
        configure_common_answers(ui)
        ui.confirm.return_value = False

        use_case.execute(target_dir=str(tmp_path))

        dependencies.install.assert_not_called()
        templates.resolve_template.assert_not_called()
        fs.copy_tree.assert_not_called()
        fs.render_tree.assert_not_called()
        ui.info.assert_any_call("Operation aborted by user.")

    def test_execute_resolves_template_without_explicit_version(self, tmp_path):
        use_case, ui, fs, _dependencies, templates, _settings = make_use_case()

        templates.resolve_template.return_value = Path("/templates/pyside6")
        fs.exists.return_value = False
        fs.current_user.return_value = "John Doe"
        configure_common_answers(ui)

        use_case.execute(target_dir=str(tmp_path))

        templates.resolve_template.assert_called_once_with(
            binding=Binding.PYSIDE6,
            target_platform=TargetPlatform.X86_64,
            version=None,
        )

    def test_execute_resolves_requested_template_version(self, tmp_path):
        use_case, ui, fs, _dependencies, templates, _settings = make_use_case()

        templates.resolve_template.return_value = Path("/templates/pyside6")
        fs.exists.return_value = False
        fs.current_user.return_value = "John Doe"
        configure_common_answers(ui)

        use_case.execute(
            target_dir=str(tmp_path),
            template_version="1.1.0",
        )

        templates.resolve_template.assert_called_once_with(
            binding=Binding.PYSIDE6,
            target_platform=TargetPlatform.X86_64,
            version=Version.parse("1.1.0"),
        )

    def test_execute_copies_template_to_destination(self, tmp_path):
        use_case, ui, fs, _dependencies, templates, _settings = make_use_case()

        template_dir = Path("/templates/pyside6")
        templates.resolve_template.return_value = template_dir
        fs.exists.return_value = False
        fs.current_user.return_value = "John Doe"
        configure_common_answers(ui)

        use_case.execute(target_dir=str(tmp_path))

        fs.copy_tree.assert_called_once_with(
            template_dir,
            tmp_path.resolve(),
        )

    def test_execute_renders_template_variables_and_settings(self, tmp_path):
        use_case, ui, fs, _dependencies, templates, _settings = make_use_case()

        templates.resolve_template.return_value = Path("/templates/pyside6")
        fs.exists.return_value = False
        fs.current_user.return_value = "John Doe"
        configure_common_answers(ui)
        ui.ask_multi_choice.return_value = [
            "Pydux (Redux-style State Management)",
            "Sentry (Crash Reporting Integration)",
        ]

        use_case.execute(target_dir=str(tmp_path))

        fs.render_tree.assert_called_once()
        variables = fs.render_tree.call_args.args[1]

        assert variables["binding"] == "PySide6"
        assert variables["version"] == "1.0.0"
        assert variables["addons"] == ["pydux", "sentry-sdk"]

    def test_execute_configures_ios_bundle_identifier(self, tmp_path):
        use_case, ui, fs, _dependencies, templates, _settings = make_use_case()

        templates.resolve_template.return_value = Path("/templates/pyside6")
        fs.exists.return_value = False
        fs.current_user.return_value = "John Doe"
        ui.ask_choice.side_effect = ["iPhone", "PySide6"]
        ui.ask_text.side_effect = [
            "MyApp",
            "1.0.0",
            "John Doe",
            "com.john.myapp",
        ]
        ui.ask_multi_choice.return_value = []
        ui.confirm.return_value = True

        use_case.execute(target_dir=str(tmp_path))

        summary = ui.show_summary.call_args.args[1]

        assert summary["Mac bundle identifier"] == "com.john.myapp"

    def test_execute_limits_mobile_addons_to_pydux(self, tmp_path):
        use_case, ui, fs, _dependencies, templates, _settings = make_use_case()

        templates.resolve_template.return_value = Path("/templates/pyside6")
        fs.exists.return_value = False
        fs.current_user.return_value = "John Doe"
        ui.ask_choice.side_effect = ["Android", "PySide6"]
        ui.ask_text.side_effect = [
            "MyApp",
            "1.0.0",
            "John Doe",
        ]
        ui.ask_multi_choice.return_value = [
            "Pydux (Redux-style State Management)",
        ]
        ui.confirm.return_value = True

        use_case.execute(target_dir=str(tmp_path))

        selected_options = ui.ask_multi_choice.call_args.args[1]

        assert selected_options == [
            "Pydux (Redux-style State Management)"
        ]

        templates.resolve_template.assert_called_once_with(
            binding=Binding.PYSIDE6,
            target_platform=TargetPlatform.ANDROID,
            version=None,
        )

    def test_execute_parses_version_before_confirmation(self, tmp_path):
        use_case, ui, fs, _dependencies, templates, _settings = make_use_case()

        fs.exists.return_value = False
        fs.current_user.return_value = "John Doe"
        ui.ask_choice.side_effect = ["Desktop (x86_64)", "PySide6"]
        ui.ask_text.side_effect = [
            "MyApp",
            "not-a-version",
            "John Doe",
        ]

        with pytest.raises(Exception):
            use_case.execute(target_dir=str(tmp_path))

        ui.confirm.assert_not_called()
        templates.resolve_template.assert_not_called()

    def test_execute_non_interactive_uses_cli_flags(self, tmp_path):
        use_case, ui, fs, dependencies, templates, _settings = make_use_case()

        templates.resolve_template.return_value = Path("/templates/pyside6")
        fs.exists.return_value = False

        use_case.execute(
            target_dir=str(tmp_path),
            default_binding="PySide6",
            target_platform="desktop",
            app_name="My App",
            app_version="2.0.0",
            author="Jane",
            addons=["pydux", "requests"],
            confirm=True,
        )

        ui.welcome.assert_not_called()
        ui.ask_choice.assert_not_called()
        ui.ask_text.assert_not_called()
        ui.ask_multi_choice.assert_not_called()
        ui.confirm.assert_not_called()

        templates.resolve_template.assert_called_once_with(
            binding=Binding.PYSIDE6,
            target_platform=TargetPlatform.X86_64,
            version=None,
        )

        fs.render_tree.assert_called_once()
        variables = fs.render_tree.call_args.args[1]
        assert variables["app_name"] == "My App"
        assert variables["version"] == "2.0.0"
        assert variables["author"] == "Jane"
        assert variables["addons"] == ["pydux", "requests"]
        dependencies.install.assert_called_once_with(str(tmp_path.resolve()))

    def test_execute_non_interactive_apple_silicon_bundle_id(self, tmp_path):
        use_case, ui, fs, _dependencies, templates, _settings = make_use_case()

        templates.resolve_template.return_value = Path("/templates/pyside6")
        fs.exists.return_value = False

        use_case.execute(
            target_dir=str(tmp_path),
            default_binding="PySide6",
            target_platform="apple-silicon",
            app_name="MyApp",
            app_version="1.0.0",
            author="Jane",
            addons=[],
            bundle_id="com.example.myapp",
            confirm=True,
        )

        summary = ui.show_summary.call_args.args[1]
        assert summary["Mac bundle identifier"] == "com.example.myapp"

    def test_execute_non_interactive_without_addons_skips_prompt(self, tmp_path):
        use_case, ui, fs, _dependencies, templates, _settings = make_use_case()

        templates.resolve_template.return_value = Path("/templates/pyside6")
        fs.exists.return_value = False

        use_case.execute(
            target_dir=str(tmp_path),
            default_binding="PySide6",
            target_platform="desktop",
            app_name="MyApp",
            app_version="1.0.0",
            author="Jane",
            confirm=True,
        )

        ui.ask_multi_choice.assert_not_called()
        variables = fs.render_tree.call_args.args[1]
        assert variables["addons"] == []
