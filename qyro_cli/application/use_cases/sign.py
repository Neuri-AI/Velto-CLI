"""
qyro.application.use_cases.sign
Sign compiled application artifacts (executables / app bundles).
"""

from pathlib import Path

from qyro_cli.application.guards import ProjectGuards
from qyro_cli.application.ports import (
    ApplicationSignerPort,
    SettingsPort,
    UserInteractionPort,
)
from qyro_cli.domain.build import SigningArtifact


class SignCompiledAppUseCase:
    """Sign frozen application artifacts for trusted distribution."""

    def __init__(
        self,
        signer: ApplicationSignerPort,
        settings: SettingsPort,
        guards: ProjectGuards,
        ui: UserInteractionPort,
    ):
        self._signer = signer
        self._settings = settings
        self._guards = guards
        self._ui = ui

    def execute(
        self,
        *,
        project_root: Path | None = None,
        target_platform: str = "auto",
        mac_overrides: dict[str, object] | None = None,
    ) -> SigningArtifact:
        root, app_name, freeze_dir, signing_options = self._prepare_signing_context(
            project_root=project_root,
            mac_overrides=mac_overrides,
        )

        self._ui.progress(
            f"Signing compiled artifacts for '{app_name}' from {freeze_dir}/ ..."
        )

        artifact = self._signer.sign(
            project_root=root,
            app_name=app_name,
            freeze_dir=freeze_dir,
            target_platform=target_platform,
            signing_options=signing_options,
        )

        self._ui.success("\nApplication signing completed.")
        self._ui.info(f"  - Platform: {artifact.platform}")
        self._ui.info(f"  - Signed files: {len(artifact.signed_paths)}")
        if artifact.platform == "mac":
            self._ui.info(f"  - Notarized: {'yes' if artifact.notarized else 'no'}")
            self._ui.info(f"  - Stapled: {'yes' if artifact.stapled else 'no'}")
            self._ui.info(
                "  - Gatekeeper assess: "
                f"{'yes' if artifact.gatekeeper_assessed else 'no'}"
            )

        return artifact

    def check(
        self,
        *,
        project_root: Path | None = None,
        target_platform: str = "auto",
        mac_overrides: dict[str, object] | None = None,
    ) -> None:
        root, app_name, freeze_dir, signing_options = self._prepare_signing_context(
            project_root=project_root,
            mac_overrides=mac_overrides,
        )

        self._ui.progress("Running signing preflight checks...")
        self._signer.preflight(
            project_root=root,
            app_name=app_name,
            freeze_dir=freeze_dir,
            target_platform=target_platform,
            signing_options=signing_options,
        )
        self._ui.success("\nSigning preflight checks passed.")

    def _prepare_signing_context(
        self,
        *,
        project_root: Path | None,
        mac_overrides: dict[str, object] | None = None,
    ) -> tuple[Path, str, str, dict[str, object]]:
        root = (project_root or Path.cwd()).resolve()

        if hasattr(self._settings, "activate_profile"):
            self._settings.activate_profile("release")
            self._settings.activate_profile("sign")

        self._guards.require_existing_project()
        self._guards.require_frozen_app()

        app_name = str(self._settings.get_optional("app_name", root.name))
        freeze_dir = str(self._settings.get_optional("freeze_dir", "build"))

        sign_settings = self._settings.get_optional("sign", {})
        if not isinstance(sign_settings, dict):
            sign_settings = {}

        windows_raw = sign_settings.get("windows", {})
        if not isinstance(windows_raw, dict):
            windows_raw = {}

        mac_raw = sign_settings.get("mac", {})
        if not isinstance(mac_raw, dict):
            mac_raw = {}

        mac_notary_raw = mac_raw.get("notary", {})
        if not isinstance(mac_notary_raw, dict):
            mac_notary_raw = {}

        # Backward-compatible fallback keys similar to legacy tooling.
        windows_fallback = {
            "certificate": self._settings.get_optional(
                "windows_sign_certificate",
                "src/sign/windows/certificate.pfx",
            ),
            "password": self._settings.get_optional("windows_sign_pass", ""),
            "timestamp_server": self._settings.get_optional("windows_sign_server", ""),
            "description": self._settings.get_optional(
                "windows_sign_description",
                app_name,
            ),
            "url": self._settings.get_optional("url", ""),
        }

        mac_fallback = {
            "identity": self._settings.get_optional("mac_sign_identity", ""),
            "entitlements": self._settings.get_optional("mac_sign_entitlements", ""),
            "notary": {
                "enabled": bool(self._settings.get_optional("mac_sign_notarize", False)),
                "staple": bool(self._settings.get_optional("mac_sign_staple", True)),
                "assess_gatekeeper": bool(self._settings.get_optional("mac_sign_assess", False)),
                "keychain_profile": self._settings.get_optional("mac_sign_notary_profile", ""),
                "key_path": self._settings.get_optional("mac_sign_notary_key", ""),
                "key_id": self._settings.get_optional("mac_sign_notary_key_id", ""),
                "issuer": self._settings.get_optional("mac_sign_notary_issuer", ""),
                "apple_id": self._settings.get_optional("mac_sign_apple_id", ""),
                "team_id": self._settings.get_optional("mac_sign_team_id", ""),
                "app_password": self._settings.get_optional("mac_sign_app_password", ""),
            },
        }

        windows_options = {**windows_fallback, **windows_raw}
        mac_options = {**mac_fallback, **mac_raw}
        mac_options["notary"] = {
            **mac_fallback["notary"],
            **mac_notary_raw,
        }

        if mac_overrides:
            for key, value in mac_overrides.items():
                if key == "notary" and isinstance(value, dict):
                    current_notary = mac_options.get("notary", {})
                    if not isinstance(current_notary, dict):
                        current_notary = {}
                    mac_options["notary"] = {**current_notary, **value}
                else:
                    mac_options[key] = value

        return root, app_name, freeze_dir, {
            "windows": windows_options,
            "mac": mac_options,
        }
