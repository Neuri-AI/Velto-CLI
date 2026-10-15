
import argparse
from unittest.mock import Mock

from velto_cli.cmdline.commands.start import StartCommand


def test_configure_does_not_add_arguments():
    parser = argparse.ArgumentParser()

    StartCommand.configure(parser)

    args = parser.parse_args([])

    assert vars(args) == {}


def test_execute_delegates_to_run_application_use_case(monkeypatch):
    use_case = Mock()
    container = Mock()
    container.run_application_use_case = use_case

    monkeypatch.setattr(
        "velto_cli.cmdline.commands.start.get_container",
        lambda: container,
    )

    result = StartCommand.execute(argparse.Namespace())

    assert result == 0
    use_case.execute.assert_called_once_with()
