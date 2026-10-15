
import argparse

from velto_cli.cmdline.models import CommandDefinition
from velto_cli.cmdline.parser import build_parser


def test_build_parser_configures_registered_commands(monkeypatch):
    configure_calls = []

    def configure(parser):
        configure_calls.append(parser)
        parser.add_argument("--value")

    definition = CommandDefinition(
        name="test",
        help="Test command.",
        configure=configure,
        execute=lambda args: 0,
    )

    monkeypatch.setattr(
        "velto_cli.cmdline.parser.get_commands",
        lambda: {"test": definition},
    )

    parser = build_parser()
    assert parser.prog == "velto"
    args = parser.parse_args(["test", "--value", "hello"])

    assert args.command == "test"
    assert args.value == "hello"
    assert len(configure_calls) == 1


def test_build_parser_registers_aliases(monkeypatch):
    definition = CommandDefinition(
        name="version",
        help="Show version.",
        configure=lambda parser: None,
        execute=lambda args: 0,
        aliases=("v",),
    )

    monkeypatch.setattr(
        "velto_cli.cmdline.parser.get_commands",
        lambda: {
            "version": definition,
            "v": definition,
        },
    )

    parser = build_parser()

    args = parser.parse_args(["v"])

    assert args.command == "v"


def test_build_parser_processes_same_definition_only_once(monkeypatch):
    calls = []

    def configure(parser):
        calls.append(parser)

    definition = CommandDefinition(
        name="version",
        help="Show version.",
        configure=configure,
        execute=lambda args: 0,
        aliases=("v",),
    )

    monkeypatch.setattr(
        "velto_cli.cmdline.parser.get_commands",
        lambda: {
            "version": definition,
            "v": definition,
        },
    )

    build_parser()

    assert len(calls) == 1


def test_build_parser_requires_command(monkeypatch):
    definition = CommandDefinition(
        name="test",
        help="Test.",
        configure=lambda parser: None,
        execute=lambda args: 0,
    )

    monkeypatch.setattr(
        "velto_cli.cmdline.parser.get_commands",
        lambda: {"test": definition},
    )

    parser = build_parser()

    try:
        parser.parse_args([])
    except SystemExit as exc:
        assert exc.code == 2
    else:
        raise AssertionError("Expected argparse to reject missing command")
