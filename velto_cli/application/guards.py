"""
Preconditions shared by several use cases.

Replaces `require_existing_project`, `require_frozen_app` and
`require_installer` from builtin_commands/_util.py — same idea, but they take
their filesystem access through a port instead of importing os.path.
"""

from velto_cli.application.ports import FileSystemPort, SettingsPort
from velto_cli.domain.errors import (
    FrozenAppNotFoundError, InstallerNotFoundError, NotAProjectError,
)


class ProjectGuards:
    def __init__(self, files: FileSystemPort, settings: SettingsPort):
        self._files = files
        self._settings = settings

    def require_existing_project(self) -> None:
        if not self._files.exists("pyproject.toml"):
            raise NotAProjectError(self._files.resolve("."))

    def require_frozen_app(self) -> None:
        self.require_existing_project()
        freeze_dir = str(self._settings.get_optional("freeze_dir", "build"))
        if not self._files.exists(freeze_dir):
            raise FrozenAppNotFoundError(freeze_dir)

    def require_installer(self) -> None:
        self.require_existing_project()
        installer = str(self._settings.get("installer"))
        target = f"target/{installer}"
        if not self._files.exists(target):
            raise InstallerNotFoundError(target)
