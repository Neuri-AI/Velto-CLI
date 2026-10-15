
import argparse
from unittest.mock import Mock

from velto_cli.cmdline.commands.init import InitCommand


def test_configure_defines_init_arguments():
    parser = argparse.ArgumentParser()

    InitCommand.configure(parser)

    args = parser.parse_args([])

    assert args.name == "."
    assert args.binding is None
    assert args.template_version is None
    assert args.target_platform is None
    assert args.app_name is None
    assert args.app_version is None
    assert args.author is None
    assert args.addons is None
    assert args.bundle_id is None
    assert args.yes is False


def test_configure_accepts_all_arguments():
    parser = argparse.ArgumentParser()

    InitCommand.configure(parser)

    args = parser.parse_args(
        [
            "--name",
            "MyApp",
            "--binding",
            "PySide6",
            "--template-version",
            "1.2.3",
            "--target-platform",
            "desktop",
            "--app-name",
            "My App",
            "--version",
            "2.0.0",
            "--author",
            "Jane",
            "--addon",
            "pydux",
            "--addon",
            "requests",
            "--bundle-id",
            "com.example.myapp",
            "--yes",
        ],
    )

    assert args.name == "MyApp"
    assert args.binding == "PySide6"
    assert args.template_version == "1.2.3"
    assert args.target_platform == "desktop"
    assert args.app_name == "My App"
    assert args.app_version == "2.0.0"
    assert args.author == "Jane"
    assert args.addons == ["pydux", "requests"]
    assert args.bundle_id == "com.example.myapp"
    assert args.yes is True


def test_execute_delegates_to_init_use_case(monkeypatch):
    use_case = Mock()
    container = Mock()
    container.init_project_use_case = use_case

    monkeypatch.setattr(
        "velto_cli.cmdline.commands.init.get_container",
        lambda: container,
    )

    args = argparse.Namespace(
        name="MyApp",
        binding="PySide6",
        template_version="1.2.3",
        target_platform=None,
        app_name=None,
        app_version=None,
        author=None,
        addons=None,
        bundle_id=None,
        yes=False,
    )

    result = InitCommand.execute(args)

    assert result == 0

    use_case.execute.assert_called_once_with(
        target_dir="MyApp",
        default_binding="PySide6",
        template_version="1.2.3",
        target_platform=None,
        app_name=None,
        app_version=None,
        author=None,
        addons=None,
        bundle_id=None,
        confirm=None,
    )


def test_execute_passes_none_for_optional_arguments(monkeypatch):
    use_case = Mock()
    container = Mock()
    container.init_project_use_case = use_case

    monkeypatch.setattr(
        "velto_cli.cmdline.commands.init.get_container",
        lambda: container,
    )

    args = argparse.Namespace(
        name=".",
        binding=None,
        template_version=None,
        target_platform="desktop",
        app_name="My App",
        app_version="1.0.0",
        author="Jane",
        addons=["pydux"],
        bundle_id=None,
        yes=True,
    )

    result = InitCommand.execute(args)

    assert result == 0

    use_case.execute.assert_called_once_with(
        target_dir=".",
        default_binding=None,
        template_version=None,
        target_platform="desktop",
        app_name="My App",
        app_version="1.0.0",
        author="Jane",
        addons=["pydux"],
        bundle_id=None,
        confirm=True,
    )

