
import pytest

from velto_cli.cmdline import registry


@pytest.fixture(autouse=True)
def clear_registry():
    registry._COMMANDS.clear()
    yield
    registry._COMMANDS.clear()


def test_command_registers_command():
    @registry.command(
        "build",
        help="Build project.",
    )
    class BuildCommand:
        @staticmethod
        def configure(parser):
            pass

        @staticmethod
        def execute(args):
            return 0

    command = registry.get_command("build")

    assert command is not None
    assert command.name == "build"
    assert command.help == "Build project."
    assert command.configure is BuildCommand.configure
    assert command.execute is BuildCommand.execute


def test_command_registers_aliases():
    @registry.command(
        "version",
        help="Show version.",
        aliases=("v",),
    )
    class VersionCommand:
        @staticmethod
        def configure(parser):
            pass

        @staticmethod
        def execute(args):
            return 0

    command = registry.get_command("version")
    alias = registry.get_command("v")

    assert command is alias
    assert command.name == "version"


def test_command_returns_original_class():
    @registry.command(
        "test",
        help="Test.",
    )
    class TestCommand:
        @staticmethod
        def configure(parser):
            pass

        @staticmethod
        def execute(args):
            return 0

    assert TestCommand.__name__ == "TestCommand"


def test_duplicate_command_name_raises():
    @registry.command(
        "test",
        help="First.",
    )
    class FirstCommand:
        @staticmethod
        def configure(parser):
            pass

        @staticmethod
        def execute(args):
            return 0

    with pytest.raises(
        RuntimeError,
        match="CLI command 'test' is already registered",
    ):
        SecondCommand = type(
            "SecondCommand",
            (),
            {
                "configure": staticmethod(lambda parser: None),
                "execute": staticmethod(lambda args: 0),
            },
        )
        registry.command(
            "test",
            help="Second.",
        )(SecondCommand)


def test_duplicate_alias_raises():
    @registry.command(
        "first",
        help="First.",
        aliases=("f",),
    )
    class FirstCommand:
        @staticmethod
        def configure(parser):
            pass

        @staticmethod
        def execute(args):
            return 0

    with pytest.raises(
        RuntimeError,
        match="CLI command 'f' is already registered",
    ):
        SecondCommand = type(
            "SecondCommand",
            (),
            {
                "configure": staticmethod(lambda parser: None),
                "execute": staticmethod(lambda args: 0),
            },
        )
        registry.command(
            "second",
            help="Second.",
            aliases=("f",),
        )(SecondCommand)


def test_get_command_returns_none_for_unknown_command():
    assert registry.get_command("unknown") is None


def test_get_commands_returns_copy():
    @registry.command(
        "test",
        help="Test.",
    )
    class TestCommand:
        @staticmethod
        def configure(parser):
            pass

        @staticmethod
        def execute(args):
            return 0

    commands = registry.get_commands()
    commands.clear()

    assert registry.get_command("test") is not None
