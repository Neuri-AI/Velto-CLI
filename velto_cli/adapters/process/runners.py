"""Subprocess, importlib, unittest and upload adapters."""

import os
import subprocess
import sys
import tempfile
from importlib.util import find_spec
from os.path import isfile, join
from pathlib import Path
from typing import List, Sequence, Optional
from unittest import TestSuite, TextTestRunner, defaultTestLoader



class ImportlibModuleRegistry:
    """ModuleRegistryPort — replaces the old `_has_module`."""

    def is_installed(self, module_name: str) -> bool:
        try:
            return bool(find_spec(module_name))
        except (ImportError, ValueError):
            return False


class SubprocessRunner:
    """Runs subprocess commands for initial setup like installing dependencies."""
    def run(
        self,
        command: Sequence[str],
        env: Optional[dict[str, str]] = None,
        cwd: Optional[str] = None,
    ) -> int:
        completed = subprocess.run(
            command,
            env=env,
            cwd=cwd,
        )

        return completed.returncode
class SubprocessAppRunner:
    """Run an app from source, statically preparing inline PSX markup when used."""

    def run_from_source(
        self,
        main_module_path: str,
        source_root: str,
    ) -> int:
        env = dict(os.environ)
        existing = env.get("PYTHONPATH", "")

        env["PYTHONPATH"] = (
            source_root + os.pathsep + existing
            if existing
            else source_root
        )

        with _prepared_psx_entry_point(main_module_path) as prepared_path:
            # Let optional PSX development tooling watch the authored file,
            # not the short-lived transformed entry point.
            env["PSX_SOURCE_ENTRY"] = str(Path(main_module_path).resolve())
            completed = subprocess.run(
                [sys.executable, prepared_path],
                env=env,
                cwd=source_root,
            )
            return completed.returncode


class _PreparedEntryPoint:
    """Context manager retaining a generated source file for one child process."""

    def __init__(self, source_path: str, output_dir: Path | None = None) -> None:
        self.source_path = source_path
        self.output_dir = output_dir
        self._temporary: tempfile.TemporaryDirectory[str] | None = None

    def __enter__(self) -> str:
        source = Path(self.source_path)
        if not source.is_file():
            # Keeps the runner usable by callers that supply a virtual path
            # (and preserves their eventual Python-file diagnostic).
            return self.source_path
        text = source.read_text(encoding="utf-8")
        # Avoid importing PSX at all for applications that do not use it.
        if "psx(" not in text:
            return self.source_path
        try:
            from psx.markup.transform import transform_source
        except ImportError:
            # Preserve Qyro's normal import diagnostic for a project that
            # references PSX without installing it.
            return self.source_path
        result = transform_source(text, filename=str(source))
        if result.transformed_calls == 0:
            return self.source_path
        if self.output_dir is None:
            self._temporary = tempfile.TemporaryDirectory(prefix="qyro-psx-")
            directory = Path(self._temporary.name)
        else:
            # Freezing needs a stable path for the generated spec and analysis
            # cache, retained until PyInstaller has finished reading it.
            directory = self.output_dir
            directory.mkdir(parents=True, exist_ok=True)
        generated = directory / source.name
        generated.write_text(result.source, encoding="utf-8")
        return str(generated)

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        if self._temporary is not None:
            self._temporary.cleanup()


def _prepared_psx_entry_point(
    main_module_path: str, *, output_dir: Path | None = None
) -> _PreparedEntryPoint:
    """Return a one-process static M4B preparation of an entry point."""
    return _PreparedEntryPoint(main_module_path, output_dir=output_dir)


class UnittestRunner:
    """TestRunnerPort. Returns False when there was nothing to run."""

    def run(self, source_root: str, test_directories: List[str]) -> bool:
        sys.path.append(source_root)
        suite = TestSuite()
        for test_dir in test_directories:
            sys.path.append(test_dir)
            try:
                entries = os.listdir(test_dir)
            except FileNotFoundError:
                continue
            for entry in entries:
                if isfile(join(test_dir, entry, "__init__.py")):
                    suite.addTest(
                        defaultTestLoader.discover(
                            entry, top_level_dir=test_dir)
                    )
        if not list(suite):
            return False
        TextTestRunner().run(suite)
        return True


class QyroUploader:
    """UploaderPort."""

    def upload_repository(self, username: str, password: str) -> None:
        from qyro.upload import _upload_repo
        _upload_repo(username, password)
