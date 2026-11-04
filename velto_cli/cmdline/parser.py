# velto_cli/cmdline/parser.py
"""
Argument parser construction for the Velto CLI.
"""

import argparse

from velto_cli.cmdline.registry import get_commands


def build_parser() -> argparse.ArgumentParser:
    """Build the Velto CLI argument parser."""

    parser = argparse.ArgumentParser(
        prog="velto",
        description=(
            "Velto CLI — Cross-platform application builder."
        ),
    )

    subparsers = parser.add_subparsers(
        dest="command",
        required=True,
    )

    processed: set[int] = set()

    for command in get_commands().values():
        command_id = id(command)

        # Aliases point to the same CommandDefinition.
        if command_id in processed:
            continue

        processed.add(command_id)

        subparser = subparsers.add_parser(
            command.name,
            aliases=list(command.aliases),
            help=command.help,
            description=command.help,
        )

        command.configure(subparser)

    return parser
