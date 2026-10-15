"""
qyro.application.use_cases.bundle
Packages frozen outputs into a distributable release artifact.
"""

from pathlib import Path

from velto_cli.application.guards import ProjectGuards
from velto_cli.application.ports import BundlePort, SettingsPort, UserInteractionPort
from velto_cli.domain.build import BundleArtifact


class BundleReleaseUseCase:
    """Create a distributable bundle from previously frozen artifacts."""

    def __init__(
        self,
        bundler: BundlePort,
        settings: SettingsPort,
        guards: ProjectGuards,
        ui: UserInteractionPort,
    ):
        self._bundler = bundler
        self._settings = settings
        self._guards = guards
        self._ui = ui

    def execute(
        self,
        *,
        project_root: Path | None = None,
        release_dir: str = "release",
        include_resources: bool = True,
        create_archive: bool = False,
        package_format: str = "auto",
        target_platform: str = "auto",
    ) -> BundleArtifact:
        root, app_name, app_author, freeze_dir, app_version, extra_files, dmg_options, nsis_options = self._prepare_bundle_context(
            project_root=project_root,
        )

        self._ui.progress(
            f"Bundling release artifacts for '{app_name}' from {freeze_dir}/ ..."
        )

        artifact = self._bundler.bundle(
            project_root=root,
            app_name=app_name,
            app_author=app_author,
            freeze_dir=freeze_dir,
            release_dir=release_dir,
            include_resources=include_resources,
            create_archive=create_archive,
            package_format=package_format,
            target_platform=target_platform,
            app_version=app_version,
            extra_files=extra_files,
            dmg_options=dmg_options,
            nsis_options=nsis_options,
        )

        self._ui.success("\nBundle created successfully.")
        self._ui.info(f"  - Output: {artifact.output_dir}")
        if artifact.archive_path is not None:
            self._ui.info(f"  - Archive: {artifact.archive_path}")
        if artifact.package_path is not None:
            self._ui.info(
                f"  - Installer ({artifact.package_format}): {artifact.package_path}"
            )

        return artifact

    def check(
        self,
        *,
        project_root: Path | None = None,
        package_format: str = "auto",
        target_platform: str = "auto",
    ) -> None:
        root, app_name, _app_author, freeze_dir, _app_version, extra_files, dmg_options, nsis_options = self._prepare_bundle_context(
            project_root=project_root,
        )

        self._ui.progress("Running bundle preflight checks...")
        self._bundler.preflight(
            project_root=root,
            app_name=app_name,
            freeze_dir=freeze_dir,
            package_format=package_format,
            target_platform=target_platform,
            extra_files=extra_files,
            dmg_options=dmg_options,
            nsis_options=nsis_options,
        )
        self._ui.success("\nBundle preflight checks passed.")

    def _prepare_bundle_context(
        self,
        *,
        project_root: Path | None,
    ) -> tuple[Path, str, str, str, str, list[object], dict[str, object], dict[str, object]]:
        root = (project_root or Path.cwd()).resolve()

        if hasattr(self._settings, "activate_profile"):
            self._settings.activate_profile("release")

        self._guards.require_existing_project()
        self._guards.require_frozen_app()

        app_name = str(self._settings.get_optional("app_name", root.name))
        app_author = str(self._settings.get_optional("author", "Developer"))
        freeze_dir = str(self._settings.get_optional("freeze_dir", "build"))
        app_version = str(self._settings.get_optional("version", "1.0.0"))
        bundle_settings = self._settings.get_optional("bundle", {})
        if not isinstance(bundle_settings, dict):
            bundle_settings = {}

        extra_files = bundle_settings.get("extra_files", [])
        if not isinstance(extra_files, list):
            extra_files = []

        dmg_options = bundle_settings.get("dmg", {})
        if not isinstance(dmg_options, dict):
            dmg_options = {}

        nsis_options = bundle_settings.get("nsis", {})
        if not isinstance(nsis_options, dict):
            nsis_options = {}

        return root, app_name, app_author, freeze_dir, app_version, extra_files, dmg_options, nsis_options
