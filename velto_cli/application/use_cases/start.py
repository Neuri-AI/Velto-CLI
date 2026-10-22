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
        self.settings.activate_development_profile()

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

        reload_settings = self.settings.get_optional('hot_reload', {})
        if not isinstance(reload_settings, dict):
            raise ValueError('hot_reload settings must be an object')
        enabled = reload_settings.get('enabled', False)
        if not isinstance(enabled, bool):
            raise ValueError('hot_reload.enabled must be a boolean')
        debounce = reload_settings.get('debounce_ms', 150)
        if type(debounce) is not int or not 0 <= debounce <= 10000:
            raise ValueError('hot_reload.debounce_ms must be an integer from 0 to 10000')
        watch = reload_settings.get('watch', ['.'])
        ignore = reload_settings.get('ignore', [])
        for setting, patterns in (('watch', watch), ('ignore', ignore)):
            if (not isinstance(patterns, list)
                    or not all(isinstance(item, str) and item and
                               not item.startswith('/') and
                               '..' not in Path(item).parts for item in patterns)):
                raise ValueError(f'hot_reload.{setting} must contain project-relative paths')
        exit_code = self.app_runner.run_from_source(
            main_module_path=str(entry_point_path),
            source_root=current_dir,
            disable_hot_reload=disable_hot_reload,
            enable_hot_reload=enabled and not disable_hot_reload,
            debounce_ms=debounce,
            watch=tuple(watch),
            ignore=tuple(ignore),
        )

        if exit_code != 0:
            raise ApplicationExecutionError(exit_code)
