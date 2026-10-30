"""Fail-closed check that authored Python sources are not distributed.

Only source filenames belonging to the project are rejected. Third-party
frameworks may legitimately bundle Python data files with the same suffix.
"""

from __future__ import annotations

from pathlib import Path, PurePosixPath

from velto_cli.domain.build import BundleMode
from velto_cli.domain.errors import QyroError


class SourceExposureGuard:
    """Inspect frozen filesystem payloads and PyInstaller onefile archives."""

    _EXCLUDED = frozenset({
        ".git", ".velto", ".venv", "venv", "build", "dist",
        "__pycache__", ".tox", ".pytest_cache", "node_modules",
    })

    def verify(
        self, *, project_root: Path, entry_point: Path,
        output_dir: Path, executable_path: Path, mode: BundleMode,
    ) -> None:
        authored = {
            path.relative_to(project_root).parts
            for path in project_root.rglob("*.py")
            if path.is_file() and not path.is_symlink()
            and not any(part in self._EXCLUDED for part in path.relative_to(project_root).parts)
        }
        if not authored:
            return

        if mode == BundleMode.ONEFILE:
            from PyInstaller.archive.readers import CArchiveReader
            try:
                entries = CArchiveReader(str(executable_path)).toc.keys()
            except Exception as exc:
                raise QyroError(
                    "Cannot inspect the PyInstaller onefile archive for exposed sources.",
                    hint=str(exc),
                ) from exc
            candidates = [PurePosixPath(name).parts for name in entries if name.endswith(".py")]
        else:
            candidates = [
                file.relative_to(output_dir).parts
                for file in output_dir.rglob("*.py") if file.is_file()
            ]

        exposed = [
            "/".join(candidate)
            for candidate in candidates
            if any(
                len(candidate) >= len(source) and candidate[-len(source):] == source
                for source in authored
            )
        ]
        if exposed:
            raise QyroError(
                "Build contains plaintext application Python sources: " + ", ".join(sorted(exposed)),
                hint="Do not collect application .py files as data. Rebuild with the PSX precompiler.",
            )
