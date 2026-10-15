"""
qyro.cmdline.commands.build
Implementation of the `qyro build` and `qyro freeze` commands.
Clean architecture command interfacing with FreezeDesktopUseCase.
"""

import argparse
from pathlib import Path
from typing import Optional

from velto_cli.cmdline.registry import command
from velto_cli.container import get_container, Container
from velto_cli.domain.mobile import MobileTarget


@command(
    "build",
    help="Build, optimize, and freeze the desktop application into an executable.",
    aliases=("freeze",),
)
class BuildCommand:
    """Build and freeze the Qyro application into standalone binaries."""

    @staticmethod
    def configure(parser: argparse.ArgumentParser) -> None:
        parser.add_argument(
            "-m",
            "--mode",
            dest="bundle_mode",
            choices=["onedir", "onefile"],
            default=None,
            help="Packaging bundle mode: 'onedir' (fast directory) or 'onefile' (single exe).",
        )

        parser.add_argument(
            "--onefile",
            dest="is_onefile",
            action="store_true",
            help="Shorthand flag to freeze as a single standalone executable file.",
        )

        parser.add_argument(
            "--debug",
            dest="debug",
            action="store_true",
            default=None,
            help="Enable debug mode with attached console window and verbose tracebacks.",
        )

        parser.add_argument(
            "--console",
            dest="console",
            action="store_true",
            default=None,
            help="Show attached console window for debugging stdout/stderr.",
        )

        parser.add_argument(
            "--uac",
            dest="uac",
            action="store_true",
            default=None,
            help="Request Administrator privilege elevation manifest on Windows (requireAdministrator).",
        )

        parser.add_argument(
            "-p",
            "--profile",
            dest="profile",
            default="release",
            help="Target profile configuration (e.g., release, debug, linux, mac, windows).",
        )

        parser.add_argument(
            "-c",
            "--clean",
            dest="clean",
            action="store_true",
            default=None,
            help="Clean PyInstaller cache and remove temporary build artifacts before and after building.",
        )

        parser.add_argument(
            "-i",
            "--interactive",
            dest="interactive",
            action="store_true",
            help="Interactively review and confirm build parameters before freezing.",
        )

        parser.add_argument(
            "--target",
            dest="target",
            choices=["desktop", "windows", "win", "mac", "macos", "darwin", "linux", "android", "ios"],
            default="desktop",
            help="Build target platform (e.g. desktop, macos, windows, linux, android, ios).",
        )

        parser.add_argument(
            "--init-spec",
            dest="init_spec",
            action="store_true",
            help="Generate mobile build specification file.",
        )

    @staticmethod
    def execute(
        args: argparse.Namespace,
        container: Optional[Container] = None,
    ) -> int:
        c = container or get_container()

        target_str = str(getattr(args, "target", "desktop")).lower()

        # Handle mobile targets if requested
        if target_str in ("android", "ios"):
            target = (
                MobileTarget.ANDROID
                if target_str == "android"
                else MobileTarget.IOS
            )
            if hasattr(c, "mobile_build_use_case"):
                c.mobile_build_use_case.execute(
                    target=target,
                    init_only=getattr(args, "init_spec", False),
                    debug=getattr(args, "debug", False),
                )
            else:
                c.ui.error("Mobile build use case is not configured in this runtime.")
                return 1
            return 0

        # Determine profile from --profile or --target if user specifies platform OS
        profile_val = getattr(args, "profile", "release")
        if target_str in ("windows", "win"):
            profile_val = "windows"
        elif target_str in ("mac", "macos", "darwin"):
            profile_val = "mac"
        elif target_str in ("linux",):
            profile_val = "linux"

        # Determine bundle mode
        bundle = "onefile" if getattr(args, "is_onefile", False) else getattr(args, "bundle_mode", None)

        try:
            c.freeze_desktop_use_case.execute(
                project_root=Path.cwd(),
                bundle=bundle,
                profile=profile_val,
                debug=getattr(args, "debug", None),
                console=getattr(args, "console", None),
                uac=getattr(args, "uac", None),
                clean=getattr(args, "clean", None),
                interactive=getattr(args, "interactive", False),
            )
            return 0
        except Exception as exc:
            c.ui.error(str(exc))
            return 1
