# velto_cli/cmdline/registry.py

"""
Command registration and lookup for the Velto CLI.
"""

from collections.abc import Callable

from velto_cli.cmdline.models import CommandDefinition


_COMMANDS: dict[str, CommandDefinition] = {}


def command(
    name: str,
    *,
    help: str,
    aliases: tuple[str, ...] = (),
) -> Callable:
    """
    Register a CLI command.

    The decorated class must provide:

        configure(parser)
        execute(args)

    The class itself is returned unchanged.
    """

    def decorator(command_type: type) -> type:
        definition = CommandDefinition(
            name=name,
            help=help,
            configure=command_type.configure,
            execute=command_type.execute,
            aliases=aliases,
        )

        if name in _COMMANDS:
            raise RuntimeError(
                f"CLI command '{name}' is already registered."
            )

        _COMMANDS[name] = definition

        for alias in aliases:
            if alias in _COMMANDS:
                raise RuntimeError(
                    f"CLI command '{alias}' is already registered."
                )

            _COMMANDS[alias] = definition

        return command_type

    return decorator


def get_command(name: str) -> CommandDefinition | None:
    """Return a registered command by name or alias."""
    return _COMMANDS.get(name)


def get_commands() -> dict[str, CommandDefinition]:
    """Return a copy of the registered commands."""
    return dict(_COMMANDS)
