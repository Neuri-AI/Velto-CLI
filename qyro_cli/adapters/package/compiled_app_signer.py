"""
qyro.adapters.package.compiled_app_signer
Infrastructure adapter for compiled app signing.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from qyro_cli.application.ports import ApplicationSignerPort
from qyro_cli.domain.build import SigningArtifact
from qyro_cli.domain.errors import FrozenAppNotFoundError, MissingDependencyError, QyroError


class CompiledAppSigner(ApplicationSignerPort):
    """Signs frozen binaries using platform-native signing tooling."""

    _WINDOWS_SIGNABLE_EXTENSIONS = {
        ".exe",
        ".cab",
        ".dll",
        ".ocx",
        ".msi",
        ".xpi",
    }

    def preflight(
        self,
        *,
        project_root: Path,
        app_name: str,
        freeze_dir: str,
        target_platform: str,
        signing_options: dict[str, object],
    ) -> None:
        freeze_root = project_root / freeze_dir
        if not freeze_root.exists():
            raise FrozenAppNotFoundError(str(freeze_root))

        platform_name = self._resolve_platform(target_platform)
        if platform_name == "windows":
            self._validate_windows_requirements(
                project_root=project_root,
                signing_options=signing_options,
            )
            self._collect_windows_targets(freeze_root)
            return

        if platform_name == "mac":
            self._validate_mac_requirements(
                project_root=project_root,
                app_name=app_name,
                freeze_root=freeze_root,
                signing_options=signing_options,
            )
            return

        raise QyroError(
            f"Compiled app signing is not supported for target platform '{platform_name}'.",
            hint="Use --platform windows or --platform mac.",
        )

    def sign(
        self,
        *,
        project_root: Path,
        app_name: str,
        freeze_dir: str,
        target_platform: str,
        signing_options: dict[str, object],
    ) -> SigningArtifact:
        freeze_root = project_root / freeze_dir
        if not freeze_root.exists():
            raise FrozenAppNotFoundError(str(freeze_root))

        platform_name = self._resolve_platform(target_platform)

        if platform_name == "windows":
            self._validate_windows_requirements(
                project_root=project_root,
                signing_options=signing_options,
            )
            signed_paths = self._sign_windows(
                freeze_root=freeze_root,
                signing_options=signing_options,
                project_root=project_root,
            )
            return SigningArtifact(platform=platform_name, signed_paths=signed_paths)

        if platform_name == "mac":
            app_bundle = self._validate_mac_requirements(
                project_root=project_root,
                app_name=app_name,
                freeze_root=freeze_root,
                signing_options=signing_options,
            )
            notarized, stapled, assessed = self._sign_mac(
                app_bundle=app_bundle,
                project_root=project_root,
                signing_options=signing_options,
            )
            return SigningArtifact(
                platform=platform_name,
                signed_paths=[app_bundle],
                notarized=notarized,
                stapled=stapled,
                gatekeeper_assessed=assessed,
            )

        raise QyroError(
            f"Compiled app signing is not supported for target platform '{platform_name}'.",
            hint="Use --platform windows or --platform mac.",
        )

    def _resolve_platform(self, target_platform: str) -> str:
        value = (target_platform or "auto").strip().lower()
        if value in {"windows", "mac"}:
            return value

        if value != "auto":
            return value

        if sys.platform.startswith("win"):
            return "windows"
        if sys.platform.startswith("darwin"):
            return "mac"
        return "linux"

    def _validate_windows_requirements(
        self,
        *,
        project_root: Path,
        signing_options: dict[str, object],
    ) -> None:
        if shutil.which("signtool") is None:
            raise MissingDependencyError("signtool")

        options = self._windows_options(signing_options)

        certificate_rel = str(options.get("certificate", "")).strip()
        if not certificate_rel:
            raise QyroError(
                "Windows signing certificate is not configured.",
                hint="Set sign.windows.certificate in settings/sign.json.",
            )

        certificate_path = (project_root / certificate_rel).resolve()
        if not certificate_path.exists():
            raise QyroError(
                "Could not find Windows signing certificate.",
                hint=f"Expected certificate at: {certificate_path}",
            )

        password = str(options.get("password", ""))
        if not password:
            raise QyroError(
                "Windows signing password is not configured.",
                hint="Set sign.windows.password in settings/sign.json.",
            )

    def _validate_mac_requirements(
        self,
        *,
        project_root: Path,
        app_name: str,
        freeze_root: Path,
        signing_options: dict[str, object],
    ) -> Path:
        if shutil.which("codesign") is None:
            raise MissingDependencyError("codesign")

        options = self._mac_options(signing_options)
        identity = str(options.get("identity", "")).strip()
        if not identity:
            raise QyroError(
                "macOS signing identity is not configured.",
                hint="Set sign.mac.identity in settings/sign.json.",
            )

        notary_options = self._mac_notary_options(options)
        notarization_enabled = self._is_enabled(notary_options.get("enabled", False))
        should_assess = self._is_enabled(notary_options.get("assess_gatekeeper", False))

        if notarization_enabled:
            if shutil.which("xcrun") is None:
                raise QyroError(
                    "xcrun is required for macOS notarization.",
                    hint="Install Xcode Command Line Tools: xcode-select --install",
                )
            if shutil.which("ditto") is None:
                raise QyroError(
                    "ditto is required to create notarization zip archives.",
                    hint="Ensure macOS host tools are available in PATH.",
                )
            self._validate_notary_auth(project_root, notary_options)

        if should_assess and shutil.which("spctl") is None:
            raise QyroError(
                "spctl is required for Gatekeeper assessment.",
                hint="Run this command on macOS where Gatekeeper tools are available.",
            )

        entitlements_value = str(options.get("entitlements", "")).strip()
        if entitlements_value:
            entitlements_path = (project_root / entitlements_value).resolve()
            if not entitlements_path.exists():
                raise QyroError(
                    "macOS entitlements file was not found.",
                    hint=f"Expected entitlements at: {entitlements_path}",
                )

        app_bundle = freeze_root / f"{app_name}.app"
        if not app_bundle.exists():
            app_candidates = sorted(freeze_root.glob("*.app"))
            if len(app_candidates) == 1:
                app_bundle = app_candidates[0]
            else:
                raise FrozenAppNotFoundError(str(app_bundle))

        return app_bundle

    def _collect_windows_targets(self, freeze_root: Path) -> list[Path]:
        targets: list[Path] = []
        for path in freeze_root.rglob("*"):
            if not path.is_file():
                continue
            if path.suffix.lower() in self._WINDOWS_SIGNABLE_EXTENSIONS:
                targets.append(path)

        if not targets:
            raise QyroError(
                "No Windows signable artifacts were found in the freeze output.",
                hint=f"Expected one of: {', '.join(sorted(self._WINDOWS_SIGNABLE_EXTENSIONS))}",
            )

        return targets

    def _sign_windows(
        self,
        *,
        freeze_root: Path,
        signing_options: dict[str, object],
        project_root: Path,
    ) -> list[Path]:
        options = self._windows_options(signing_options)
        certificate_path = (project_root / str(options["certificate"])).resolve()

        timestamp_server = str(options.get("timestamp_server", "")).strip()
        description = str(options.get("description", "")).strip()
        url = str(options.get("url", "")).strip()

        targets = self._collect_windows_targets(freeze_root)
        for target in targets:
            command = [
                "signtool",
                "sign",
                "/f",
                str(certificate_path),
                "/p",
                str(options["password"]),
                "/fd",
                "sha256",
                "/td",
                "sha256",
            ]
            if timestamp_server:
                command.extend(["/tr", timestamp_server])
            if description:
                command.extend(["/d", description])
            if url:
                command.extend(["/du", url])
            command.append(str(target))

            self._run(command)

        return targets

    def _sign_mac(
        self,
        *,
        app_bundle: Path,
        project_root: Path,
        signing_options: dict[str, object],
    ) -> tuple[bool, bool, bool]:
        options = self._mac_options(signing_options)
        identity = str(options["identity"])

        xattr_path = shutil.which("xattr")
        if xattr_path is not None:
            self._run([xattr_path, "-cr", str(app_bundle)])

        # Sign nested binaries first; sign bundle root last with Hardened Runtime.
        nested_targets = self._collect_macos_sign_targets(app_bundle)
        for target in nested_targets:
            self._run([
                "codesign",
                "--force",
                "--sign",
                identity,
                "--timestamp",
                str(target),
            ])

        bundle_command = [
            "codesign",
            "--force",
            "--sign",
            identity,
            "--timestamp",
            "--options",
            "runtime",
        ]

        entitlements_value = str(options.get("entitlements", "")).strip()
        if entitlements_value:
            entitlements_path = (project_root / entitlements_value).resolve()
            bundle_command.extend(["--entitlements", str(entitlements_path)])

        bundle_command.append(str(app_bundle))
        self._run(bundle_command)

        self._run([
            "codesign",
            "--verify",
            "--deep",
            "--strict",
            "--verbose=2",
            str(app_bundle),
        ])

        notary_options = self._mac_notary_options(options)
        notarized = self._is_enabled(notary_options.get("enabled", False))
        stapled = False
        assessed = False

        if notarized:
            with tempfile.TemporaryDirectory(prefix="qyro-notary-") as temp_dir:
                zip_path = Path(temp_dir) / f"{app_bundle.stem}.zip"
                self._run([
                    "ditto",
                    "-c",
                    "-k",
                    "--keepParent",
                    str(app_bundle),
                    str(zip_path),
                ])

                submit_cmd = [
                    "xcrun",
                    "notarytool",
                    "submit",
                    str(zip_path),
                    *self._build_notary_auth_args(project_root, notary_options),
                    "--wait",
                ]
                self._run(submit_cmd)

            if self._is_enabled(notary_options.get("staple", True)):
                self._run(["xcrun", "stapler", "staple", str(app_bundle)])
                stapled = True

        if self._is_enabled(notary_options.get("assess_gatekeeper", False)):
            self._run([
                "spctl",
                "--assess",
                "--type",
                "execute",
                "-vvv",
                str(app_bundle),
            ])
            assessed = True

        return notarized, stapled, assessed

    def _collect_macos_sign_targets(self, app_bundle: Path) -> list[Path]:
        targets: list[Path] = []
        for path in app_bundle.rglob("*"):
            if not path.is_file() or path.is_symlink():
                continue

            suffix = path.suffix.lower()
            if suffix in {".dylib", ".so"}:
                targets.append(path)
                continue

            if "Contents/MacOS/" in str(path) or "Contents/Frameworks/" in str(path):
                if path.stat().st_mode & 0o111:
                    targets.append(path)

        # Sign deeper paths first to avoid invalidating parent signatures.
        targets.sort(key=lambda p: len(p.parts), reverse=True)
        return targets

    def _mac_notary_options(self, mac_options: dict[str, object]) -> dict[str, object]:
        notary = mac_options.get("notary", {})
        if isinstance(notary, dict):
            return notary
        return {}

    def _is_enabled(self, value: object) -> bool:
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            return value.strip().lower() in {"1", "true", "yes", "on"}
        return bool(value)

    def _validate_notary_auth(
        self,
        project_root: Path,
        notary_options: dict[str, object],
    ) -> None:
        profile = str(notary_options.get("keychain_profile", "")).strip()
        if profile:
            return

        key_path_value = str(notary_options.get("key_path", "")).strip()
        key_id = str(notary_options.get("key_id", "")).strip()
        if key_path_value and key_id:
            key_path = (project_root / key_path_value).resolve()
            if not key_path.exists():
                raise QyroError(
                    "notarytool API key file was not found.",
                    hint=f"Expected key at: {key_path}",
                )
            return

        apple_id = str(notary_options.get("apple_id", "")).strip()
        team_id = str(notary_options.get("team_id", "")).strip()
        app_password = str(notary_options.get("app_password", "")).strip()
        if apple_id and team_id and app_password:
            return

        raise QyroError(
            "Notarization is enabled but no valid notarytool authentication is configured.",
            hint=(
                "Configure one method under sign.mac.notary in settings/sign.json:\n"
                "1) keychain_profile\n"
                "2) key_path + key_id (+ issuer for Team keys)\n"
                "3) apple_id + team_id + app_password"
            ),
        )

    def _build_notary_auth_args(
        self,
        project_root: Path,
        notary_options: dict[str, object],
    ) -> list[str]:
        profile = str(notary_options.get("keychain_profile", "")).strip()
        if profile:
            return ["--keychain-profile", profile]

        key_path_value = str(notary_options.get("key_path", "")).strip()
        key_id = str(notary_options.get("key_id", "")).strip()
        issuer = str(notary_options.get("issuer", "")).strip()
        if key_path_value and key_id:
            key_path = (project_root / key_path_value).resolve()
            args = ["--key", str(key_path), "--key-id", key_id]
            if issuer:
                args.extend(["--issuer", issuer])
            return args

        apple_id = str(notary_options.get("apple_id", "")).strip()
        team_id = str(notary_options.get("team_id", "")).strip()
        app_password = str(notary_options.get("app_password", "")).strip()
        if apple_id and team_id and app_password:
            return [
                "--apple-id",
                apple_id,
                "--team-id",
                team_id,
                "--password",
                app_password,
            ]

        raise QyroError(
            "Could not resolve notarytool authentication arguments.",
            hint="Run 'qyro sign --check --platform mac' to validate notarization setup.",
        )

    def _windows_options(self, signing_options: dict[str, object]) -> dict[str, object]:
        options = signing_options.get("windows", {})
        if not isinstance(options, dict):
            return {}
        return options

    def _mac_options(self, signing_options: dict[str, object]) -> dict[str, object]:
        options = signing_options.get("mac", {})
        if not isinstance(options, dict):
            return {}
        return options

    def _run(self, command: list[str]) -> None:
        completed = subprocess.run(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
        )
        if completed.returncode != 0:
            output = (completed.stdout or "") + ("\n" + completed.stderr if completed.stderr else "")
            raise QyroError(
                "Signing command failed.",
                hint=(
                    f"Command: {' '.join(command)}\n"
                    f"Exit code: {completed.returncode}\n"
                    f"Output:\n{output.strip()}"
                ),
            )
