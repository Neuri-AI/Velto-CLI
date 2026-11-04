
import argparse
from unittest.mock import Mock

from velto_cli.cmdline.commands.clean import CleanCommand


def test_configure_does_not_add_arguments():
    parser = argparse.ArgumentParser()

    CleanCommand.configure(parser)

    args = parser.parse_args([])

    assert args.include_release is False


def test_configure_accepts_release_flag():
    parser = argparse.ArgumentParser()

    CleanCommand.configure(parser)

    args = parser.parse_args(["--release"])

    assert args.include_release is True


def test_execute_delegates_to_clean_use_case(monkeypatch):
    use_case = Mock()
    container = Mock()
    container.clean_project_use_case = use_case

    monkeypatch.setattr(
        "velto_cli.cmdline.commands.clean.get_container",
        lambda: container,
    )

    result = CleanCommand.execute(
        argparse.Namespace(include_release=False)
    )

    assert result == 0
    use_case.execute.assert_called_once_with(include_release=False)


def test_execute_passes_release_flag(monkeypatch):
    use_case = Mock()
    container = Mock()
    container.clean_project_use_case = use_case

    monkeypatch.setattr(
        "velto_cli.cmdline.commands.clean.get_container",
        lambda: container,
    )

    result = CleanCommand.execute(
        argparse.Namespace(include_release=True)
    )

    assert result == 0
    use_case.execute.assert_called_once_with(include_release=True)
