"""
Implementation of the `qyro init` command.
"""

import argparse

from velto_cli.cmdline.registry import command
from velto_cli.container import get_container
from velto_cli.domain.project import Binding


_FRAMEWORK_CHOICES = tuple(binding.value for binding in Binding)


def _parse_framework(value: str) -> str:
    """Normalize framework input to its canonical display name."""
    normalized = value.strip().lower()
    for framework in _FRAMEWORK_CHOICES:
        if framework.lower() == normalized:
            return framework
    raise argparse.ArgumentTypeError(
        f"invalid framework: {value!r} (choose from {', '.join(_FRAMEWORK_CHOICES)})"
    )


@command(
    "init",
    help="Initialize a new Velto project.",
)
class InitCommand:
    """Initialize a new Velto project."""

    @staticmethod
    def configure(parser: argparse.ArgumentParser) -> None:
        parser.add_argument(
            "-n",
            "--name",
            nargs="?",
            default=".",
            help=(
                "Project directory name. "
                "Defaults to the current directory."
            ),
        )

        parser.add_argument(
            "-f",
            "--framework",
            type=_parse_framework,
            choices=_FRAMEWORK_CHOICES,
            help="Pre-select the UI framework.",
        )

        parser.add_argument(
            "--template-version",
            help="Use a specific template version.",
        )

        parser.add_argument(
            "--target-platform",
            dest="target_platform",
            choices=[
                "desktop",
                "x86_64",
                "apple-silicon",
                "iphone",
                "android",
            ],
            help=(
                "Target platform for scaffolding. "
                "Use desktop/x86_64 for standard desktop apps."
            ),
        )

        parser.add_argument(
            "--app-name",
            dest="app_name",
            help="Application display name to write into project settings.",
        )

        parser.add_argument(
            "--version",
            dest="app_version",
            help="Application semantic version (e.g. 1.0.0).",
        )

        parser.add_argument(
            "--author",
            dest="author",
            help="Author name to store in project settings.",
        )

        parser.add_argument(
            "--addon",
            dest="addons",
            action="append",
            default=None,
            choices=[
                "hotrl",
                "pydux",
                "sentry-sdk",
                "requests",
            ],
            help=(
                "Optional add-on to enable. Can be used multiple times, "
                "for example: --addon pydux --addon requests"
            ),
        )

        parser.add_argument(
            "--bundle-id",
            dest="bundle_id",
            help="Bundle identifier for iPhone/Apple Silicon targets.",
        )

        parser.add_argument(
            "-y",
            "--yes",
            dest="yes",
            action="store_true",
            help="Skip confirmation prompt and continue with provided/default values.",
        )

    @staticmethod
    def execute(args: argparse.Namespace) -> int:
        get_container().init_project_use_case.execute(
            target_dir=args.name,
            default_binding=args.framework,
            template_version=args.template_version,
            target_platform=args.target_platform,
            app_name=args.app_name,
            app_version=args.app_version,
            author=args.author,
            addons=args.addons,
            bundle_id=args.bundle_id,
            confirm=True if args.yes else None,
        )

        return 0
