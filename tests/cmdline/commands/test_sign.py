import argparse
from unittest.mock import ANY, Mock

from velto_cli.cmdline.commands.sign import SignCommand


def test_configure_defines_sign_arguments():
    parser = argparse.ArgumentParser()

    SignCommand.configure(parser)

    args = parser.parse_args([])

    assert args.platform == "auto"
    assert args.check is False


def test_execute_delegates_to_sign_use_case(monkeypatch):
    use_case = Mock()
    container = Mock()
    container.sign_compiled_app_use_case = use_case

    monkeypatch.setattr(
        "velto_cli.cmdline.commands.sign.get_container",
        lambda: container,
    )

    args = argparse.Namespace(platform="windows", check=False)

    result = SignCommand.execute(args)

    assert result == 0
    use_case.execute.assert_called_once_with(
        project_root=ANY,
        target_platform="windows",
        mac_overrides=None,
    )


def test_execute_runs_check_mode(monkeypatch):
    use_case = Mock()
    container = Mock()
    container.sign_compiled_app_use_case = use_case

    monkeypatch.setattr(
        "velto_cli.cmdline.commands.sign.get_container",
        lambda: container,
    )

    args = argparse.Namespace(platform="mac", check=True)

    result = SignCommand.execute(args)

    assert result == 0
    use_case.check.assert_called_once_with(
        project_root=ANY,
        target_platform="mac",
        mac_overrides=None,
    )
    use_case.execute.assert_not_called()


def test_execute_returns_one_on_error(monkeypatch):
    use_case = Mock()
    use_case.execute.side_effect = RuntimeError("boom")

    container = Mock()
    container.sign_compiled_app_use_case = use_case
    container.ui = Mock()

    monkeypatch.setattr(
        "velto_cli.cmdline.commands.sign.get_container",
        lambda: container,
    )

    args = argparse.Namespace(platform="auto", check=False)

    result = SignCommand.execute(args)

    assert result == 1
    container.ui.error.assert_called_once_with("boom")
