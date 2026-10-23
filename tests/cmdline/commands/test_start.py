
import argparse
from unittest.mock import Mock

from velto_cli.cmdline.commands.start import StartCommand


def test_configure_exposes_development_reload_override():
    parser = argparse.ArgumentParser()

    StartCommand.configure(parser)

    args = parser.parse_args([])

    assert args.no_hot_reload is False
    assert parser.parse_args(['--no-hot-reload']).no_hot_reload is True


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
    use_case.execute.assert_called_once_with(disable_hot_reload=False)


def test_start_can_disable_hot_reload(monkeypatch):
    use_case = Mock()
    container = Mock(run_application_use_case=use_case)
    monkeypatch.setattr("velto_cli.cmdline.commands.start.get_container", lambda: container)
    assert StartCommand.execute(argparse.Namespace(no_hot_reload=True)) == 0
    use_case.execute.assert_called_once_with(disable_hot_reload=True)
