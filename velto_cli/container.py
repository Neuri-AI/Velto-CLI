"""
qyro.container
Composition Root implementing Inversion of Control (IoC).
All dependencies between Use Cases, Ports, and Adapters are instantiated here.
"""

from pathlib import Path
from velto_cli.adapters.cli.console_ui import RichConsoleUI
from velto_cli.adapters.cli.progress import RichProgress
from velto_cli.adapters.package.release_bundler import ReleaseBundler
from velto_cli.adapters.package.compiled_app_signer import CompiledAppSigner
from velto_cli.adapters.package.metadata import PackageMetadata
from velto_cli.adapters.persistence.storage import (
    OsFileSystem,
    SettingsRepository,
)
from velto_cli.adapters.process.runners import (
    ImportlibModuleRegistry,
    SubprocessAppRunner,
    SubprocessRunner,
)
from velto_cli.adapters.process.dependencies import DependencyInstaller
from velto_cli.adapters.templates.bundled import BundledTemplateProvider
from velto_cli.adapters.templates.fallback import FallbackTemplateProvider
from velto_cli.adapters.templates.github_cached import (
    GitHubCachedTemplateProvider,
)
from velto_cli.adapters.freeze.framework_hooks import FrameworkHookResolver
from velto_cli.adapters.freeze.optimizer import BinaryOptimizer
from velto_cli.adapters.freeze.pyinstaller_adapter import PyInstallerFreezer
from velto_cli.adapters.freeze.psx_source_preparer import PSXProjectSourcePreparer
from velto_cli.application.use_cases.freeze import FreezeDesktopUseCase
from velto_cli.application.use_cases.bundle import BundleReleaseUseCase
from velto_cli.application.use_cases.clean import CleanProjectUseCase
from velto_cli.application.use_cases.sign import SignCompiledAppUseCase
from velto_cli.application.use_cases.init import InitProjectUseCase
from velto_cli.application.use_cases.version import ShowVersionUseCase
from velto_cli.application.use_cases.start import RunApplicationUseCase
from velto_cli.application.guards import ProjectGuards



TEMPLATE_ORGANIZATION = "Neuri-AI"
TEMPLATE_CACHE_DIR = Path.home() / ".qyro" / "templates"
TEMPLATE_DIR = Path(__file__).resolve().parent / "templates"


class Container:
    """Dependency Injection Container."""

    def __init__(self):
        self.ui = RichConsoleUI()
        self.fs = OsFileSystem()
        self.settings = SettingsRepository()
        self.app_runner = SubprocessAppRunner()
        self.subprocess_runner = SubprocessRunner()
        dependency_installer = DependencyInstaller(
            runner=self.subprocess_runner,
        )
        self.modules = ImportlibModuleRegistry()
        self.guards = ProjectGuards(
            files=self.fs,
            settings=self.settings,
        )
        self.metadata = PackageMetadata()
        self.progress = RichProgress()

        github_provider = GitHubCachedTemplateProvider(
            organization=TEMPLATE_ORGANIZATION,
            cache_dir=TEMPLATE_CACHE_DIR,
            supported_range="^1.0.0",
            progress=self.progress,
        )

        bundled_provider = BundledTemplateProvider(
            templates_dir=TEMPLATE_DIR,
        )

        self.template_provider = FallbackTemplateProvider(
            primary=github_provider,
            fallback=bundled_provider,
        )

        self.hook_resolver = FrameworkHookResolver()
        self.optimizer = BinaryOptimizer(ui=self.ui)
        self.bundler = ReleaseBundler()
        self.signer = CompiledAppSigner()
        self.source_preparer = PSXProjectSourcePreparer()
        self.freezer = PyInstallerFreezer(
            resolver=self.hook_resolver,
            source_preparer=self.source_preparer,
            ui=self.ui,
            progress=self.progress,
        )


        #! USE CASES
        self.init_project_use_case = InitProjectUseCase(
            ui=self.ui,
            fs=self.fs,
            dependencies=dependency_installer,
            templates=self.template_provider,
            settings_repo=self.settings,
        )

        self.show_version_use_case = ShowVersionUseCase(
            ui=self.ui,
            metadata=self.metadata,
        )

        self.run_application_use_case = RunApplicationUseCase(
            ui=self.ui,
            fs=self.fs,
            settings=self.settings,
            app_runner=self.app_runner,
            modules=self.modules,
            guards=self.guards,
        )

        self.freeze_desktop_use_case = FreezeDesktopUseCase(
            freezer=self.freezer,
            settings_repo=self.settings,
            optimizer=self.optimizer,
            ui=self.ui,
        )

        self.bundle_release_use_case = BundleReleaseUseCase(
            bundler=self.bundler,
            settings=self.settings,
            guards=self.guards,
            ui=self.ui,
        )

        self.clean_project_use_case = CleanProjectUseCase(
            ui=self.ui,
            fs=self.fs,
            settings=self.settings,
            guards=self.guards,
        )

        self.sign_compiled_app_use_case = SignCompiledAppUseCase(
            signer=self.signer,
            settings=self.settings,
            guards=self.guards,
            ui=self.ui,
        )


_container: Container | None = None


def get_container() -> Container:
    global _container

    if _container is None:
        _container = Container()

    return _container


def set_container(container: Container) -> None:
    """Replace the container, primarily for tests."""
    global _container
    _container = container
