"""
Implementation of the `qyro bundle` command.
"""

import argparse
from pathlib import Path

from velto_cli.cmdline.registry import command
from velto_cli.container import get_container


@command(
    "bundle",
    help="Package frozen build artifacts into a distributable release layout.",
    aliases=("installer",),
)
class BundleCommand:
    """Bundle built artifacts for application distribution."""

    @staticmethod
    def configure(parser: argparse.ArgumentParser) -> None:
        parser.add_argument(
            "--release-dir",
            dest="release_dir",
            default="release",
            help="Release output directory (default: release).",
        )

        parser.add_argument(
            "--no-resources",
            dest="no_resources",
            action="store_true",
            help="Skip copying the project resources/ directory into the bundle.",
        )

        parser.add_argument(
            "--zip",
            dest="zip_archive",
            action="store_true",
            help="Also generate a .zip archive in the release directory.",
        )

        parser.add_argument(
            "--platform",
            dest="platform",
            choices=["auto", "mac", "windows", "linux"],
            default="auto",
            help="Target installer platform (default: auto from host OS).",
        )

        parser.add_argument(
            "--format",
            dest="package_format",
            choices=["auto", "dir", "zip", "dmg", "nsis", "tar.gz", "deb", "rpm", "arch"],
            default="auto",
            help=(
                "Installer/package format. auto => mac:dmg, windows:nsis, linux:tar.gz. "
                "Use dir to skip installer creation."
            ),
        )

        parser.add_argument(
            "--check",
            dest="check",
            action="store_true",
            help="Validate dependencies and release settings without creating artifacts.",
        )

    @staticmethod
    def execute(args: argparse.Namespace) -> int:
        c = get_container()

        try:
            if getattr(args, "check", False):
                c.bundle_release_use_case.check(
                    project_root=Path.cwd(),
                    package_format=args.package_format,
                    target_platform=args.platform,
                )
                return 0

            c.bundle_release_use_case.execute(
                project_root=Path.cwd(),
                release_dir=args.release_dir,
                include_resources=not args.no_resources,
                create_archive=args.zip_archive,
                package_format=args.package_format,
                target_platform=args.platform,
            )
            return 0
        except Exception as exc:
            c.ui.error(str(exc))
            return 1
