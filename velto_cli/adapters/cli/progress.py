from rich.console import Console
from rich.progress import (
    BarColumn,
    DownloadColumn,
    Progress,
    TextColumn,
    TimeRemainingColumn,
    TransferSpeedColumn,
)


class RichProgress:
    def __init__(self, console: Console | None = None):
        self._progress = Progress(
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            DownloadColumn(),
            TransferSpeedColumn(),
            TimeRemainingColumn(),
            console=console,
        )
        self._task_id = None

    def start(
        self,
        message: str,
        total: int | None = None,
    ) -> None:
        self._task_id = self._progress.add_task(
            message,
            total=total,
        )
        self._progress.start()

    def update(self, completed: int) -> None:
        if self._task_id is not None:
            self._progress.update(
                self._task_id,
                completed=completed,
            )

    def stop(self) -> None:
        if self._task_id is not None:
            self._progress.stop()
            self._progress.remove_task(self._task_id)
            self._task_id = None