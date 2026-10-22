# qyro/cmdline/commands/start.py
"""
Implementation of the `qyro start` command.
"""

import argparse

from velto_cli.cmdline.registry import command
from velto_cli.container import get_container


@command(
    "start",
    help="Start the application in development mode.",
)
class StartCommand:
    """Start the application in development mode."""

    @staticmethod
    def configure(parser: argparse.ArgumentParser) -> None:
        pass

    @staticmethod
    def execute(args: argparse.Namespace) -> int:
        """
        Execute the start command.

        Application-layer integration will be connected here once
        the corresponding use case is available.
        """

        get_container().run_application_use_case.execute(disable_hot_reload=getattr(args, 'no_hot_reload', False))

        return 0
