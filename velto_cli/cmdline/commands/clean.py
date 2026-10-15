# qyro/cmdline/commands/clean.py
"""
Implementation of the `qyro clean` command.
"""

import argparse
from velto_cli.cmdline.registry import command
from velto_cli.container import get_container


@command(
    "clean",
    help="Remove target build outputs and log files.",
)
class CleanCommand:
    """Clean generated project artifacts."""

    @staticmethod
    def configure(parser: argparse.ArgumentParser) -> None:
        parser.add_argument(
            "--release",
            dest="include_release",
            action="store_true",
            help="Also remove release/ artifacts created by qyro bundle.",
        )

    @staticmethod
    def execute(args: argparse.Namespace) -> int:
        get_container().clean_project_use_case.execute(
            include_release=getattr(args, "include_release", False)
        )
        return 0
