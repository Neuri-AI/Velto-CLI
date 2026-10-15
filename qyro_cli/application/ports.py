"""
qyro.application.ports
Application-layer interfaces for peripheral adapters.

These Protocols define what the application layer needs from the outside
world. Implementations belong to infrastructure/presentation adapters.

No concrete infrastructure dependencies are allowed here.
"""

from pathlib import Path
from typing import Any, Optional, Protocol, Sequence
from qyro_cli.domain.version import Version

from qyro_cli.domain.mobile import MobileTarget
from qyro_cli.domain.project import (
    Binding,
    ProjectConfig,
    TargetPlatform,
)
from qyro_cli.domain.build import (
    FreezeManifest,
    BuildArtifact,
    BundleArtifact,
    SigningArtifact,
    OptimizationConfig,
)


# --- Presentation -----------------------------------------------------------


class UserInteractionPort(Protocol):
    """Everything application use cases need from a human."""

    def welcome(self) -> None: ...

    def ask_text(
        self,
        prompt: str,
        default: str = "",
        show_default: bool = True,
    ) -> str: ...

    def ask_choice(
        self,
        prompt: str,
        choices: Sequence[str],
        default: str = "",
    ) -> str: ...

    def ask_multi_choice(
        self,
        prompt: str,
        choices: Sequence[str],
    ) -> Sequence[str]: ...

    def confirm(
        self,
        prompt: str,
        default: bool = True,
    ) -> bool: ...

    def show_summary(
        self,
        fields: dict[str, str],
    ) -> None: ...

    def info(self, message: str) -> None: ...

    def success(self, message: str) -> None: ...

    def warning(self, message: str) -> None: ...

    def progress(self, message: str) -> None:
        """Notify that a long-running operation is starting."""

    def error(self, message: str) -> None: ...


# --- Filesystem & project layout -------------------------------------------


class FileSystemPort(Protocol):
    """Project filesystem operations required by application use cases."""

    def exists(self, path: str) -> bool: ...

    def is_dir(self, path: str) -> bool: ...

    def resolve(self, relative_path: str) -> str:
        """Resolve a project-relative path to an absolute path."""

    def make_directory(self, relative_path: str) -> None: ...

    def remove_tree(self, relative_path: str) -> None: ...

    def empty_directory(self, relative_path: str) -> None:
        """Delete a directory's contents while preserving the directory."""

    def write_text(
        self,
        relative_path: str,
        content: str,
    ) -> None: ...

    def read_text(self, relative_path: str) -> str: ...

    def copy_tree(
        self,
        source: Path,
        destination: Path,
    ) -> None: ...

    def current_user(self) -> str: ...

    def render_tree(self, root: str, variables: dict[str, object], exclude: Sequence[str] | None = None) -> None: ...



# --- Templates --------------------------------------------------------------


class TemplateProviderPort(Protocol):
    def resolve_template(
        self,
        binding: Binding,
        target_platform: TargetPlatform,
        version: Version | None = None,
    ) -> Path: ...


# --- Settings ---------------------------------------------------------------


class SettingsPort(Protocol):
    """Access to project configuration and build settings."""

    def get(self, key: str) -> Any:
        """Return a required setting or raise MissingSettingError."""

    def get_optional(
        self,
        key: str,
        default: Any = None,
    ) -> Any: ...

    def set(
        self,
        key: str,
        value: Any,
    ) -> None:
        """Update an in-memory setting."""

    def persist_base(
        self,
        values: dict[str, object],
    ) -> None:
        """Persist values to base project settings."""

    def activate_profile(self, profile: str) -> None: ...

    @property
    def base_settings_path(self) -> str: ...


class PackageMetadataPort(Protocol):
    """Provides metadata about the installed Qyro package."""

    def cli_version(self) -> str: ...

    def engine_version(self) -> str: ...

    def qyro_version(self) -> str: ...


# --- Dependencies & packages -----------------------------------------------

class DependencyInstallerPort(Protocol):
    """Installs dependencies declared by a project."""

    def install(self, project_dir: str) -> None: ...

class ImportlibModuleRegistry:
    """Checks whether Python modules are installed."""
    def is_installed(self, module_name: str) -> bool: ...

# --- Processes & application execution -------------------------------------


class ProcessRunnerPort(Protocol):
    """Runs external processes without exposing subprocess to the application."""

    def run(
        self,
        command: Sequence[str],
        env: Optional[dict[str, str]] = None,
        cwd: Optional[str] = None,
    ) -> int: ...


class AppRunnerPort(Protocol):
    """Runs the user's application from source."""

    def run_from_source(
        self,
        main_module_path: str,
        source_root: str,
    ) -> int: ...


# --- Repository / upload ----------------------------------------------------


class UploaderPort(Protocol):
    """Uploads the project repository/package to the configured service."""

    def upload_repository(
        self,
        username: str,
        password: str,
    ) -> None: ...



class ProgressPort(Protocol):
    def start(
        self,
        message: str,
        total: int | None = None,
    ) -> None:
        ...

    def update(self, completed: int) -> None:
        ...

    def stop(self) -> None:
        ...


class FreezerPort(Protocol):
    """Executes Python application freezing (e.g. PyInstaller adapter)."""

    def build_command(
        self,
        project_root: Path,
        manifest: FreezeManifest,
        extra_args: Optional[list[str]] = None,
    ) -> list[str]: ...

    def freeze(
        self,
        project_root: Path,
        manifest: FreezeManifest,
        extra_args: Optional[list[str]] = None,
    ) -> BuildArtifact: ...


class FrameworkHookResolverPort(Protocol):
    """Resolves framework-specific hidden imports and data files (Kivy, Qt, Tkinter)."""

    def resolve_args(
        self,
        project_root: Path,
        manifest: FreezeManifest,
    ) -> list[str]: ...


class BinaryOptimizerPort(Protocol):
    """Optimizes compiled binary artifacts via UPX and stripping."""

    def optimize(
        self,
        artifact: BuildArtifact,
        config: OptimizationConfig,
    ) -> None: ...


class BundlePort(Protocol):
    """Packages frozen artifacts into a distributable release directory."""

    def preflight(
        self,
        *,
        project_root: Path,
        app_name: str,
        freeze_dir: str,
        package_format: str,
        target_platform: str,
        extra_files: list[object],
        dmg_options: dict[str, object],
        nsis_options: dict[str, object],
    ) -> None: ...

    def bundle(
        self,
        *,
        project_root: Path,
        app_name: str,
        app_author: str,
        freeze_dir: str,
        release_dir: str,
        include_resources: bool,
        create_archive: bool,
        package_format: str,
        target_platform: str,
        app_version: str,
        extra_files: list[object],
        dmg_options: dict[str, object],
        nsis_options: dict[str, object],
    ) -> BundleArtifact: ...


class ApplicationSignerPort(Protocol):
    """Signs compiled application artifacts for distribution trust."""

    def preflight(
        self,
        *,
        project_root: Path,
        app_name: str,
        freeze_dir: str,
        target_platform: str,
        signing_options: dict[str, object],
    ) -> None: ...

    def sign(
        self,
        *,
        project_root: Path,
        app_name: str,
        freeze_dir: str,
        target_platform: str,
        signing_options: dict[str, object],
    ) -> SigningArtifact: ...


class MobileBuildPort(Protocol):

    """
    !EXPERIMENTAL: This module is just for defining mobile target specifications for Android and iOS

    !#########################
    ! AVOID USING THIS MODULE
    !#########################


    Builds a project for a supported mobile target.
    """

    def init_spec(
        self,
        target: MobileTarget,
        config: ProjectConfig,
    ) -> None: ...

    def compile(
        self,
        target: MobileTarget,
        debug: bool,
    ) -> str: ...