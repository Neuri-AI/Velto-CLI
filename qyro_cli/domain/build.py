"""
qyro.domain.build
Domain models for Qyro Build / Freeze System with Clean Architecture.
Integrates with qyro.domain.project entities (Binding, AddonModule, ProjectConfig).
Supports PyInstaller arguments synthesis, platform settings merging,
Windows UAC elevation manifests, onefile/onedir modes, and GUI binding hooks
(PySide6, PyQt6, PyQt5, PySide2, Kivy, Tkinter).
"""

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import List, Optional, Union

from qyro_cli.domain.project import Binding, AddonModule, ProjectConfig
from qyro_cli.domain.errors import InvalidBindingError


class BundleMode(str, Enum):
    ONEDIR = "onedir"
    ONEFILE = "onefile"

    @classmethod
    def parse(cls, value: str) -> "BundleMode":
        val = str(value).strip().lower()
        if val in ("onefile", "bundle", "single"):
            return cls.ONEFILE
        return cls.ONEDIR


class UACLevel(str, Enum):
    AS_INVOKER = "asInvoker"
    HIGHEST_AVAILABLE = "highestAvailable"
    REQUIRE_ADMINISTRATOR = "requireAdministrator"

    @classmethod
    def parse(cls, value: str) -> "UACLevel":
        val = str(value).strip()
        for level in cls:
            if level.value.lower() == val.lower():
                return level
        if "admin" in val.lower():
            return cls.REQUIRE_ADMINISTRATOR
        return cls.AS_INVOKER


@dataclass(frozen=True)
class UACConfig:
    level: UACLevel = UACLevel.AS_INVOKER
    ui_access: bool = False

    @property
    def requires_admin(self) -> bool:
        return self.level == UACLevel.REQUIRE_ADMINISTRATOR


@dataclass(frozen=True)
class DebugConfig:
    enabled: bool = False
    console_window: bool = False
    unstripped: bool = False
    verbose_imports: bool = False
    bootloader_debug: bool = False


@dataclass(frozen=True)
class OptimizationConfig:
    upx_enabled: bool = True
    upx_level: int = 9  # 1 to 9
    upx_dir: Optional[str] = None
    upx_excludes: List[str] = field(default_factory=lambda: [
        "vcruntime140.dll", "python3*.dll", "sdl2.dll", "glew32.dll", "kivy*.pyd"
    ])
    bytecode_opt: int = 1  # 0: None, 1: -O, 2: -OO
    strip_binaries: bool = True
    exclude_modules: List[str] = field(default_factory=lambda: [
        "unittest", "test", "pydoc"
    ])
    exclude_binaries: List[str] = field(default_factory=list)
    exclude_plugins: List[str] = field(default_factory=list)
    remove_translations: bool = True
    clean_build: bool = True


@dataclass(frozen=True)
class KivyConfig:
    """Kivy and KivyMD specific configuration."""
    deps_mode: str = "minimal"  # 'minimal' or 'all'
    include_sdl2: bool = True
    include_glew: bool = True
    include_gstreamer: bool = False
    kv_files_auto_collect: bool = True
    custom_kv_paths: List[str] = field(default_factory=lambda: [
        "views/*.kv", "components/*.kv", "*.kv"
    ])


@dataclass(frozen=True)
class FreezeManifest:
    app_name: str
    author: str = "Developer"
    version: str = "1.0.0"
    entry_point: str = "main.py"
    target_platform: str = "windows"  # windows, linux, mac
    bundle_mode: BundleMode = BundleMode.ONEDIR
    binding: Union[Binding, str] = "kivy"
    icon_path: Optional[str] = None
    mac_bundle_identifier: Optional[str] = None
    uac: UACConfig = field(default_factory=UACConfig)
    debug: DebugConfig = field(default_factory=DebugConfig)
    optimization: OptimizationConfig = field(default_factory=OptimizationConfig)
    kivy: Optional[KivyConfig] = field(default_factory=KivyConfig)
    addons: List[Union[AddonModule, str]] = field(default_factory=list)
    hidden_imports: List[str] = field(default_factory=lambda: list(ProjectConfig.DEFAULT_HIDDEN_IMPORTS))
    resources_dir: Optional[str] = "resources"
    protect_resources: bool = False
    protected_settings_dir: str = "settings"
    protected_resources_dir: str = "resources"
    protected_bundle_relative_path: str = ".qyro/resources.pak"
    extra_pyinstaller_args: List[str] = field(default_factory=list)
    paths: List[str] = field(default_factory=list)
    collect_all: List[str] = field(default_factory=list)

    @property
    def binding_enum(self) -> Binding:
        """Returns the parsed strongly-typed Binding enum."""
        if isinstance(self.binding, Binding):
            return self.binding
        try:
            return Binding.parse(str(self.binding))
        except (InvalidBindingError, ValueError):
            return Binding.KIVY

    @property
    def is_pyside(self) -> bool:
        """Checks if binding belongs to PySide family."""
        return self.binding_enum.is_pyside

    @property
    def is_kivy(self) -> bool:
        """Checks if binding is Kivy."""
        return self.binding_enum == Binding.KIVY

    @property
    def is_tkinter(self) -> bool:
        """Checks if binding is Tkinter."""
        return self.binding_enum == Binding.TKINTER

    def has_addon(self, addon: Union[AddonModule, str]) -> bool:
        """Checks if an addon is declared in the manifest."""
        target = addon.value if isinstance(addon, AddonModule) else str(addon).lower()
        return any(
            (a.value if isinstance(a, AddonModule) else str(a)).lower() == target
            for a in self.addons
        )

    def suggest_bundle_id(self) -> str:
        """Generates macOS / iOS bundle identifier using domain project rules."""
        return ProjectConfig.suggest_bundle_identifier(self.app_name, self.author)

    def validate(self) -> None:
        if not self.app_name or not self.app_name.strip():
            raise ValueError("Application name cannot be empty.")
        if not self.entry_point or not self.entry_point.strip():
            raise ValueError("Entry point script path is required.")
        if self.optimization.upx_level < 1 or self.optimization.upx_level > 9:
            raise ValueError("UPX compression level must be between 1 and 9.")


@dataclass(frozen=True)
class BuildArtifact:
    output_dir: Path
    executable_path: Path
    bundle_mode: BundleMode
    size_bytes: int
    duration_seconds: float
    manifest_used: FreezeManifest
    uac_applied: bool
    debug_mode: bool
    binding: str


@dataclass(frozen=True)
class BundleArtifact:
    output_dir: Path
    included_paths: List[Path] = field(default_factory=list)
    archive_path: Optional[Path] = None
    package_path: Optional[Path] = None
    package_format: Optional[str] = None


@dataclass(frozen=True)
class SigningArtifact:
    platform: str
    signed_paths: List[Path] = field(default_factory=list)
    notarized: bool = False
    stapled: bool = False
    gatekeeper_assessed: bool = False
