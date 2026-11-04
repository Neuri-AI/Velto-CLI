"""Precompile authored PSX modules before PyInstaller analyzes project imports.

This infrastructure adapter prepares a disposable source tree; PyInstaller
packs its code objects, not the staged .py files. Normal development remains
source-backed and continues to use the regular component compiler.
"""

from __future__ import annotations

import re
from pathlib import Path

from velto_cli.domain.errors import QyroError


class PSXProjectSourcePreparer:
    """Prepare the entire importable project tree for a source-free freeze."""

    _EXCLUDED = frozenset({
        ".git", ".velto", ".venv", "venv", "build", "dist",
        "__pycache__", ".tox", ".pytest_cache", "node_modules",
    })

    def prepare(self, project_root: Path, entry_point: Path) -> tuple[Path, Path]:
        sources: list[tuple[Path, Path]] = []
        needs_psx = False
        for source in sorted(project_root.rglob("*.py")):
            relative = source.relative_to(project_root)
            if any(part in self._EXCLUDED for part in relative.parts):
                continue
            if not source.is_file() or source.is_symlink():
                continue
            content = source.read_text(encoding="utf-8")
            sources.append((relative, source))
            if re.search(r"\bpsx\s*\(", content):
                needs_psx = True

        if not needs_psx:
            return entry_point, project_root

        try:
            from psx.markup.transform import transform_source
        except ImportError as exc:
            raise QyroError(
                "Frozen PSX builds require an installed PSX precompiler.",
                hint="Install an updated PSX before running velto build.",
            ) from exc

        staging = project_root / "build" / "psx-compiled"
        for relative, original_path in sources:
            original = original_path.read_text(encoding="utf-8")
            # Fail the build on unsupported/syntactically invalid input rather
            # than falling back to the user's original source modules.
            try:
                transformed = transform_source(
                    original, filename=str(original_path), precompiled=True,
                )
            except (SyntaxError, ValueError, TypeError) as exc:
                raise QyroError(
                    f"Unable to precompile PSX source: {relative}",
                    hint=str(exc),
                ) from exc
            staged = staging / relative
            staged.parent.mkdir(parents=True, exist_ok=True)
            staged.write_text(transformed.source, encoding="utf-8")

        return staging / entry_point.relative_to(project_root), staging
