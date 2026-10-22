from pathlib import Path

from velto_cli.application.guards import ProjectGuards
from velto_cli.application.ports import (
    AppRunnerPort,
    FileSystemPort,
    ImportlibModuleRegistry,
    SettingsPort,
    UserInteractionPort,
)
from velto_cli.domain.errors import (
    ApplicationExecutionError,
    EntryPointNotFoundError,
    InvalidBindingError,
    MissingBindingError,
)
from velto_cli.domain.project import Binding


class RunApplicationUseCase:
    def __init__(
        self,
        ui: UserInteractionPort,
        fs: FileSystemPort,
        settings: SettingsPort,
        app_runner: AppRunnerPort,
        modules: ImportlibModuleRegistry,
        guards: ProjectGuards,
    ):
        self.ui = ui
        self.fs = fs
        self.settings = settings
        self.app_runner = app_runner
        self.modules = modules
        self.guards = guards

    def execute(self, *, disable_hot_reload: bool = False) -> None:
        self.guards.require_existing_project()
        self.settings.activate_profile('development')

        raw_binding = self.settings.get_optional("framework")
        try:
            binding = Binding.parse(raw_binding)
        except InvalidBindingError:
            raise MissingBindingError(Binding.values())

        if not self.modules.is_installed(binding.import_name):
            raise MissingBindingError((binding.value,))

        current_dir = self.fs.resolve(".")

        entry_point = self.settings.get("entry_point")
        entry_point_path = Path(current_dir) / str(entry_point)

        if not self.fs.exists(str(entry_point_path)):
            raise EntryPointNotFoundError(str(entry_point_path))

        exit_code = self.app_runner.run_from_source(
            main_module_path=str(entry_point_path),
            source_root=current_dir,
            disable_hot_reload=disable_hot_reload,
        )

        if exit_code != 0:
            raise ApplicationExecutionError(exit_code)
