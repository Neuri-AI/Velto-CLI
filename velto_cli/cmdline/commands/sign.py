"""
Implementation of the `qyro sign` command.
"""

import argparse
from pathlib import Path

from velto_cli.cmdline.registry import command
from velto_cli.container import get_container


@command(
    "sign",
    help="Sign compiled application artifacts for trusted distribution.",
)
class SignCommand:
    """Sign frozen application outputs (exe/dll/.app)."""

    @staticmethod
    def configure(parser: argparse.ArgumentParser) -> None:
        parser.add_argument(
            "--platform",
            dest="platform",
            choices=["auto", "windows", "mac"],
            default="auto",
            help="Signing target platform (default: auto from host OS).",
        )

        parser.add_argument(
            "--check",
            dest="check",
            action="store_true",
            help="Validate signing dependencies and settings without signing files.",
        )

        parser.add_argument(
            "--notarize",
            dest="notarize",
            action="store_true",
            help="macOS only: submit signed .app for Apple notarization.",
        )

        parser.add_argument(
            "--staple",
            dest="staple",
            action="store_true",
            help="macOS only: staple notarization ticket to the .app (implies --notarize).",
        )

        parser.add_argument(
            "--no-assess",
            dest="no_assess",
            action="store_true",
            help="macOS only: skip Gatekeeper assessment with spctl.",
        )

        parser.add_argument(
            "--keychain-profile",
            dest="keychain_profile",
            default="",
            help="macOS only: override sign.mac.notary.keychain_profile for notarytool.",
        )

    @staticmethod
    def _build_mac_overrides(args: argparse.Namespace) -> dict[str, object] | None:
        notary_overrides: dict[str, object] = {}

        if getattr(args, "notarize", False):
            notary_overrides["enabled"] = True
        if getattr(args, "staple", False):
            notary_overrides["enabled"] = True
            notary_overrides["staple"] = True
        if getattr(args, "no_assess", False):
            notary_overrides["assess_gatekeeper"] = False

        profile = str(getattr(args, "keychain_profile", "") or "").strip()
        if profile:
            notary_overrides["keychain_profile"] = profile

        if not notary_overrides:
            return None

        return {"notary": notary_overrides}

    @staticmethod
    def execute(args: argparse.Namespace) -> int:
        c = get_container()
        mac_overrides = SignCommand._build_mac_overrides(args)

        try:
            if getattr(args, "check", False):
                c.sign_compiled_app_use_case.check(
                    project_root=Path.cwd(),
                    target_platform=args.platform,
                    mac_overrides=mac_overrides,
                )
                return 0

            c.sign_compiled_app_use_case.execute(
                project_root=Path.cwd(),
                target_platform=args.platform,
                mac_overrides=mac_overrides,
            )
            return 0
        except Exception as exc:
            c.ui.error(str(exc))
            return 1
