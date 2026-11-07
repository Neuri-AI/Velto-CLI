"""Backend-independent process, output and user-notification helpers."""
import os
import shutil
import tempfile
from contextlib import nullcontext
from pathlib import Path
from velto_cli.application.ports import ProcessRunnerPort, UserInteractionPort


class MobileBuilderBase:
    def __init__(
        self, runner: ProcessRunnerPort, ui: UserInteractionPort | None = None
    ) -> None:
        self._runner = runner
        self._ui = ui

    def _status(self, message: str):
        if self._ui is None:
            return nullcontext()
        return self._ui.status(message)

    def _notice(self, message: str) -> None:
        if self._ui is not None:
            self._ui.info(message)

    def _warning(self, message: str) -> None:
        if self._ui is not None:
            self._ui.warning(message)

    @staticmethod
    def _command(name: str) -> str:
        resolved = shutil.which(name)
        if not resolved:
            raise RuntimeError(
                f"Required mobile tool '{name}' not found on PATH. "
                "Install the target toolchain before building."
            )
        return resolved

    def _run(self, command: list[str], root: Path) -> None:
        code = self._runner.run(command, cwd=str(root))
        if code != 0:
            raise RuntimeError(f"Mobile tool failed (exit {code}): {command[0]}")

    @staticmethod
    def _publish_android_artifact(
        source: Path, project_root: Path, *, backend: str
    ) -> Path:
        """Publish a finished Android artifact under build/android/<backend>.

        Preserve the original Gradle/Buildozer output for incremental builds
        and diagnostics. Atomically replace the public copy so failed copies
        never leave a partially written APK or AAB.
        """
        if backend not in ("qt", "kivy"):
            raise ValueError(f"Unsupported Android output backend: {backend}")
        if source.suffix.lower() not in (".apk", ".aab") or not source.is_file():
            raise RuntimeError(f"Missing Android APK/AAB to publish: {source}")
        output = project_root / "build" / "android" / backend
        output.mkdir(parents=True, exist_ok=True)
        target = output / source.name
        if source.resolve() == target.resolve():
            return target
        fd, temp_path = tempfile.mkstemp(
            prefix=".velto-android-", suffix=source.suffix, dir=output
        )
        try:
            with os.fdopen(fd, "wb") as stream, source.open("rb") as incoming:
                shutil.copyfileobj(incoming, stream)
            os.replace(temp_path, target)
        finally:
            if os.path.exists(temp_path):
                os.unlink(temp_path)
        return target

    @staticmethod
    def _latest(root: Path, suffixes: tuple[str, ...]) -> Path:
        matches = [p for suffix in suffixes for p in root.rglob(f"*{suffix}")
                   if p.is_file()]
        if not matches:
            raise RuntimeError(
                "Tool exited successfully but no mobile artifact was found. "
                "Inspect the toolchain logs and project dependencies."
            )
        return max(matches, key=lambda p: p.stat().st_mtime)
