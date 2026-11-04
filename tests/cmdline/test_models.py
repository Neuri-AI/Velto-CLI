from argparse import ArgumentParser, Namespace

from velto_cli.cmdline.models import CommandDefinition


def test_command_definition_defaults_aliases():
    configure = lambda parser: None
    execute = lambda args: 0

    definition = CommandDefinition(
        name="test",
        help="Test command.",
        configure=configure,
        execute=execute,
    )

    assert definition.name == "test"
    assert definition.help == "Test command."
    assert definition.configure is configure
    assert definition.execute is execute
    assert definition.aliases == ()


def test_command_definition_stores_aliases():
    configure = lambda parser: None
    execute = lambda args: 0

    definition = CommandDefinition(
        name="test",
        help="Test command.",
        configure=configure,
        execute=execute,
        aliases=("t", "testing"),
    )

    assert definition.aliases == ("t", "testing")


def test_command_definition_is_immutable():
    definition = CommandDefinition(
        name="test",
        help="Test command.",
        configure=lambda parser: None,
        execute=lambda args: 0,
    )

    try:
        definition.name = "other"
    except AttributeError:
        pass
    else:
        raise AssertionError("CommandDefinition should be immutable")
