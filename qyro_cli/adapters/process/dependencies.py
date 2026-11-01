import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

from qyro_cli.application.ports import ProcessRunnerPort
from qyro_cli.domain.errors import PackageInstallationError


@dataclass(frozen=True)
class DependencyManager:
    executable: str
    lock_command: list[str]
    install_command: list[str]


class DependencyInstaller:
    def __init__(self, runner: ProcessRunnerPort):
        self.runner = runner

    def install(self, project_dir: str) -> None:
        project_path = Path(project_dir)
        manager = self._resolve_manager()

        try:
            self._run(manager.lock_command, project_path)
            self._run(manager.install_command, project_path)
        except PackageInstallationError:
            raise
        except Exception as exc:
            raise PackageInstallationError(
                manager.executable
            ) from exc

    def _resolve_manager(self) -> DependencyManager:
        managers = (
            DependencyManager(
                "poetry",
                ["poetry", "lock"],
                ["poetry", "install"],
            ),
            DependencyManager(
                "uv",
                ["uv", "lock"],
                ["uv", "sync"],
            ),
            DependencyManager(
                "pipenv",
                ["pipenv", "lock"],
                ["pipenv", "install"],
            ),
            DependencyManager(
                sys.executable,
                [],
                [
                    sys.executable,
                    "-m",
                    "pip",
                    "install",
                    ".",
                ],
            ),
        )

        return next(
            (
                manager
                for manager in managers
                if manager.executable == sys.executable
                or shutil.which(manager.executable)
            ),
            managers[-1],
        )

    def _run(
        self,
        command: list[str],
        project_dir: Path,
    ) -> None:
        if not command:
            return

        result = self.runner.run(
            command,
            cwd=str(project_dir),
        )

        if result != 0:
            raise PackageInstallationError(command[0])