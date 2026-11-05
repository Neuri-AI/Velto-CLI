"""Keep generated APK inspection and build outputs out of Android source scans."""
from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
import tempfile
from typing import Iterator


def android_root_inspection_dirs(root: Path) -> tuple[Path, ...]:
    """Only recognize directories paired with a root-level .apk/.aab.

    Never recursively inspect arbitrary directories: extracted APK trees may
    contain unreadable members and are not trusted as project source.
    """
    return tuple(sorted(
        (entry for entry in root.iterdir()
         if entry.is_dir()
         and not entry.is_symlink()
         and (
             (root / (entry.name + ".apk")).is_file()
             or (root / (entry.name + ".aab")).is_file()
         )),
        key=lambda path: path.name,
    ))


@contextmanager
def isolate_previous_android_build_outputs(root: Path) -> Iterator[None]:
    """Hide generated build/ and root APK extraction folders during deploy.

    PySide6 scans every Python file in the project even if Buildozer would
    subsequently exclude it. It can hit unreadable Python files extracted
    from APK archives in root/<apk-stem>/assets/private. Move those and build/
    outside the project without reading their contents, restore on success
    and failure, and never follow symlinks.
    """
    candidates = [root / "build", *android_root_inspection_dirs(root)]
    candidates = [path for path in candidates if path.exists() or path.is_symlink()]
    if not candidates:
        yield
        return
    for path in candidates:
        if not path.is_dir() or path.is_symlink():
            raise RuntimeError(
                f"Cannot safely isolate Android build output: {path}"
            )
    temporary = Path(tempfile.mkdtemp(
        prefix=".velto-android-build-", dir=root.parent,
    ))
    moved: list[tuple[Path, Path]] = []
    try:
        for index, original in enumerate(candidates):
            saved = temporary / f"saved-{index}"
            original.rename(saved)
            moved.append((original, saved))
        yield
    finally:
        # Restore even when the deploy process raises. Keep original data
        # intact if a tool recreated its destination.
        for original, saved in reversed(moved):
            if not saved.exists():
                continue
            if original.exists() or original.is_symlink():
                if not original.is_dir() or original.is_symlink():
                    raise RuntimeError(
                        "Android deploy recreated a non-directory output at "
                        f"{original}. Preserved original at {saved}"
                    )
                for child in tuple(saved.iterdir()):
                    target = original / child.name
                    if target.exists() or target.is_symlink():
                        raise RuntimeError(
                            "Android deploy recreated conflicting output "
                            f"{target}. Preserved original at {saved}"
                        )
                    child.rename(target)
                saved.rmdir()
            else:
                saved.rename(original)
        if not any(temporary.iterdir()):
            temporary.rmdir()
