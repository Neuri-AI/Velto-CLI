"""Select project-owned Android launcher icons without platform host assumptions.

The Velto boilerplate contains base/ (small PNG/ICO), linux/ and mac/
(high-resolution PNG) icon directories. Android needs a PNG; avoid reusing
Windows ICOs or macOS ICNS icons.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator


@dataclass(frozen=True)
class AndroidProjectIcon:
    source: Path
    relative_path: str
    folder: str


_ICON_FOLDERS = ("resources/linux/icons", "resources/mac/icons", "resources/base/icons")
# Prefer a 512px launcher icon; larger assets are valid fallback.
_ICON_NAMES = ("512.png", "1024.png", "256.png", "128.png", "64.png", "32.png")


def select_android_project_icon(root: Path) -> AndroidProjectIcon | None:
    """Pick a suitable project PNG deterministically, regardless of build host."""
    for folder in _ICON_FOLDERS:
        for name in _ICON_NAMES:
            candidate = root / folder / name
            if candidate.is_file() and not candidate.is_symlink():
                return AndroidProjectIcon(
                    source=candidate,
                    relative_path=candidate.relative_to(root).as_posix(),
                    folder=folder,
                )
    return None


@contextmanager
def configured_kivy_android_icon(
    spec: Path, icon: AndroidProjectIcon | None
) -> Iterator[None]:
    """Temporarily apply the chosen project icon to Buildozer's input spec."""
    if icon is None:
        yield
        return
    original = spec.read_bytes()
    try:
        lines = original.decode("utf-8").splitlines(keepends=True)
        section = None
        replaced = False
        output = []
        for line in lines:
            stripped = line.strip()
            if stripped.startswith("[") and stripped.endswith("]"):
                section = stripped.lower()
            if section == "[app]" and stripped.startswith("icon.filename"):
                output.append(f"icon.filename = {icon.relative_path}\n")
                replaced = True
            else:
                output.append(line)
        if not replaced:
            index = next(
                (i + 1 for i, line in enumerate(output)
                 if line.strip().lower() == "[app]"), None
            )
            if index is None:
                raise ValueError("Missing [app] in buildozer.spec")
            output.insert(index, f"icon.filename = {icon.relative_path}\n")
        spec.write_text("".join(output), encoding="utf-8")
        yield
    finally:
        spec.write_bytes(original)
