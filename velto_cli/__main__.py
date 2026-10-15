"""
Module entry point.

Allows invoking the CLI directly via:

    python -m velto_cli <command> [options]
"""

import sys

from velto_cli.cmdline import main


def _configure_stdio() -> None:
    """Use UTF-8 output when possible to avoid Windows codepage crashes."""
    for stream_name in ("stdout", "stderr"):
        stream = getattr(sys, stream_name, None)
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            try:
                reconfigure(encoding="utf-8", errors="replace")
            except Exception:
                # Keep default stream settings if the runtime disallows reconfigure.
                pass


if __name__ == "__main__":
    _configure_stdio()
    sys.exit(main())
