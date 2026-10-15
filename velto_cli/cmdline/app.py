# qyro/cmdline/app.py
"""
Application entry point for the Qyro command-line interface.
"""

from velto_cli.cmdline.parser import build_parser
from velto_cli.cmdline.registry import get_command


def main(args: list[str] | None = None) -> int:
    """
    Run the Qyro command-line interface.

    Args:
        args: Optional command-line arguments. If omitted, argparse
            reads them from sys.argv.

    Returns:
        Process exit code.
    """

    # Import built-in commands so their decorators register them.
    from velto_cli.cmdline import commands  # noqa: F401

    parser = build_parser()
    parsed = parser.parse_args(args)

    command = get_command(parsed.command)

    if command is None:
        parser.print_help()
        return 1

    try:
        return command.execute(parsed)

    except KeyboardInterrupt:
        print("\nOperation aborted by user.")
        return 130

    except Exception as exc:
        print(f"Unexpected failure: {exc}")
        return 2
