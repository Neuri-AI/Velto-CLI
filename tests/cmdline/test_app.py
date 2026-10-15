
from unittest.mock import Mock

from velto_cli.cmdline import app


def test_main_executes_registered_command(monkeypatch):
    command = Mock()
    command.execute.return_value = 7

    parser = Mock()
    parsed = Mock()
    parsed.command = "test"

    parser.parse_args.return_value = parsed

    monkeypatch.setattr(app, "build_parser", lambda: parser)
    monkeypatch.setattr(app, "get_command", lambda name: command)

    result = app.main(["test"])

    assert result == 7
    parser.parse_args.assert_called_once_with(["test"])
    command.execute.assert_called_once_with(parsed)


def test_main_returns_one_for_unknown_command(monkeypatch):
    parser = Mock()
    parsed = Mock()
    parsed.command = "unknown"

    parser.parse_args.return_value = parsed

    monkeypatch.setattr(app, "build_parser", lambda: parser)
    monkeypatch.setattr(app, "get_command", lambda name: None)

    result = app.main(["unknown"])

    assert result == 1
    parser.print_help.assert_called_once_with()


def test_main_returns_130_on_keyboard_interrupt(monkeypatch, capsys):
    parser = Mock()
    parsed = Mock()
    parsed.command = "test"

    parser.parse_args.return_value = parsed

    command = Mock()
    command.execute.side_effect = KeyboardInterrupt

    monkeypatch.setattr(app, "build_parser", lambda: parser)
    monkeypatch.setattr(app, "get_command", lambda name: command)

    result = app.main(["test"])

    captured = capsys.readouterr()

    assert result == 130
    assert captured.out == "\nOperation aborted by user.\n"


def test_main_returns_two_on_unexpected_exception(monkeypatch, capsys):
    parser = Mock()
    parsed = Mock()
    parsed.command = "test"

    parser.parse_args.return_value = parsed

    command = Mock()
    command.execute.side_effect = RuntimeError("boom")

    monkeypatch.setattr(app, "build_parser", lambda: parser)
    monkeypatch.setattr(app, "get_command", lambda name: command)

    result = app.main(["test"])

    captured = capsys.readouterr()

    assert result == 2
    assert captured.out == "Unexpected failure: boom\n"
