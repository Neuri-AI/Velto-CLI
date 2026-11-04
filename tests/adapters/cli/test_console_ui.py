
from unittest.mock import MagicMock, patch
import pytest
from rich.console import Console

from velto_cli.adapters.cli.console_ui import (
    QUESTIONARY_STYLE,
    RichConsoleUI,
)


@pytest.fixture
def console():
    return MagicMock(spec=Console)


@pytest.fixture
def ui(console):
    return RichConsoleUI(console=console)


@patch("velto_cli.adapters.cli.console_ui.Prompt.ask")
def test_ask_text(mock_ask, ui):
    mock_ask.return_value = "MyApp"

    result = ui.ask_text("Application name")

    assert result == "MyApp"
    mock_ask.assert_called_once_with(
        "[bold]Application name[/bold]",
        default="",
        show_default=True,
        console=ui._console,
    )


@patch("velto_cli.adapters.cli.console_ui.Prompt.ask")
def test_ask_text_with_default(mock_ask, ui):
    mock_ask.return_value = "MyApp"

    result = ui.ask_text(
        "Application name",
        default="DefaultApp",
    )

    assert result == "MyApp"
    mock_ask.assert_called_once_with(
        "[bold]Application name[/bold]",
        default="DefaultApp",
        show_default=True,
        console=ui._console,
    )


def test_ask_choice_without_choices(ui):
    result = ui.ask_choice(
        "Binding",
        [],
        default="PySide6",
    )

    assert result == "PySide6"


@patch("velto_cli.adapters.cli.console_ui.questionary.select")
def test_ask_choice(mock_select, ui):
    mock_select.return_value.ask.return_value = "PySide6"

    result = ui.ask_choice(
        "Binding",
        ["PySide6", "PyQt6"],
    )

    assert result == "PySide6"

    mock_select.assert_called_once_with(
        "Binding",
        choices=["PySide6", "PyQt6"],
        default="PySide6",
        style=QUESTIONARY_STYLE,
    )


@patch("velto_cli.adapters.cli.console_ui.questionary.select")
def test_ask_choice_raises_when_cancelled(
    mock_select,
    ui,
):
    mock_select.return_value.ask.return_value = None

    with pytest.raises(KeyboardInterrupt):
        ui.ask_choice(
            "Binding",
            ["PySide6", "PyQt6"],
            default="PyQt6",
        )


@patch("velto_cli.adapters.cli.console_ui.questionary.checkbox")
def test_ask_multi_choice(mock_checkbox, ui):
    mock_checkbox.return_value.ask.return_value = [
        "Sentry",
        "Pydux",
    ]

    result = ui.ask_multi_choice(
        "Add-ons",
        ["Hot Reloading", "Pydux", "Sentry"],
    )

    assert result == ["Sentry", "Pydux"]

    mock_checkbox.assert_called_once()

    kwargs = mock_checkbox.call_args.kwargs

    assert kwargs["style"] is not None
    assert len(kwargs["choices"]) == 3
    assert all(
        choice.value == choice.title
        for choice in kwargs["choices"]
    )
    assert all(
        choice.checked is False
        for choice in kwargs["choices"]
    )


@patch("velto_cli.adapters.cli.console_ui.questionary.checkbox")
def test_ask_multi_choice_raises_when_cancelled(
    mock_checkbox,
    ui,
):
    mock_checkbox.return_value.ask.return_value = None

    with pytest.raises(KeyboardInterrupt):
        ui.ask_multi_choice(
            "Add-ons",
            ["Sentry"],
        )


@patch("velto_cli.adapters.cli.console_ui.Confirm.ask")
def test_confirm(mock_confirm, ui):
    mock_confirm.return_value = True

    result = ui.confirm("Continue?")

    assert result is True
    mock_confirm.assert_called_once_with(
        "[bold #fff]Continue?[/bold #fff]",
        default=True,
        console=ui._console,
    )


@patch("velto_cli.adapters.cli.console_ui.Confirm.ask")
def test_confirm_with_default(mock_confirm, ui):
    mock_confirm.return_value = False

    result = ui.confirm(
        "Continue?",
        default=False,
    )

    assert result is False
    mock_confirm.assert_called_once_with(
        "[bold #fff]Continue?[/bold #fff]",
        default=False,
        console=ui._console,
    )


def test_show_summary(ui, console):
    ui.show_summary(
        "Project",
        {
            "Name": "MyApp",
            "Binding": "PySide6",
        },
    )

    console.print.assert_called_once()

    panel = console.print.call_args.args[0]

    assert panel.title == "[bold]Project[/bold]"
    assert panel.border_style == "#ff8c00"
    assert panel.renderable.title == "Project Configuration"


@pytest.mark.parametrize(
    ("method", "message", "expected"),
    [
        ("info", "hello", "hello"),
        (
            "success",
            "done",
            "[bold #fff]done[/bold #fff]",
        ),
        (
            "warning",
            "warning",
            "[#fdba74]warning[/#fdba74]",
        ),
        (
            "progress",
            "working",
            "[#fdba74]⏳ working[/#fdba74]",
        ),
        (
            "error",
            "failed",
            "\n💔 [bold red]Error:[/bold red] failed",
        ),
    ],
)
def test_console_messages(
    ui,
    console,
    method,
    message,
    expected,
):
    getattr(ui, method)(message)

    console.print.assert_called_once_with(expected)
