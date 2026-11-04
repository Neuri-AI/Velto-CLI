
import argparse
from unittest.mock import Mock

from velto_cli.cmdline.commands.version import VersionCommand


def test_configure_does_not_add_arguments():
    parser = argparse.ArgumentParser()

    VersionCommand.configure(parser)

    args = parser.parse_args([])

    assert vars(args) == {}


def test_execute_delegates_to_version_use_case(monkeypatch):
    use_case = Mock()
    container = Mock()
    container.show_version_use_case = use_case

    monkeypatch.setattr(
        "velto_cli.cmdline.commands.version.get_container",
        lambda: container,
    )

    result = VersionCommand.execute(argparse.Namespace())

    assert result == 0
    use_case.execute.assert_called_once_with()
