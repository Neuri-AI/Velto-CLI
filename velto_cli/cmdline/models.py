# qyro/cmdline/models.py
"""
Models and contracts used by the Qyro command-line interface.
"""

from argparse import ArgumentParser, Namespace
from dataclasses import dataclass
from typing import Protocol


class CommandConfigurator(Protocol):
    """Defines how a command configures its argument parser."""

    def __call__(self, parser: ArgumentParser) -> None:
        ...


class CommandExecutor(Protocol):
    """Defines how a command is executed."""

    def __call__(self, args: Namespace) -> int:
        ...


@dataclass(frozen=True)
class CommandDefinition:
    """Definition of a registered CLI command."""

    name: str
    help: str
    configure: CommandConfigurator
    execute: CommandExecutor
    aliases: tuple[str, ...] = ()