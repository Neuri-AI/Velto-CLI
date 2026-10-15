"""
qyro.application.use_cases.clean
Removes generated build artifacts from the project workspace.
"""

from velto_cli.application.guards import ProjectGuards
from velto_cli.application.ports import FileSystemPort, SettingsPort, UserInteractionPort


class CleanProjectUseCase:
    """Delete build outputs produced by freeze/bundle workflows."""

    def __init__(
        self,
        ui: UserInteractionPort,
        fs: FileSystemPort,
        settings: SettingsPort,
        guards: ProjectGuards,
    ):
        self._ui = ui
        self._fs = fs
        self._settings = settings
        self._guards = guards

    def execute(self, include_release: bool = False) -> None:
        self._guards.require_existing_project()

        freeze_dir = str(self._settings.get_optional("freeze_dir", "build"))
        targets = [freeze_dir]

        if include_release:
            targets.append("release")

        removed: list[str] = []
        for target in targets:
            if not self._fs.exists(target):
                continue

            try:
                self._fs.remove_tree(target)
            except OSError:
                # Mounted/protected directories may reject tree deletion.
                self._fs.empty_directory(target)

            removed.append(target)

        if removed:
            joined = ", ".join(removed)
            self._ui.success(f"\nCleaned artifacts: {joined}")
        else:
            self._ui.info("Nothing to clean.")
