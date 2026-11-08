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


@contextmanager
def staged_android_project(root: Path) -> Iterator[Path]:
    """Build from an independent filtered copy; never rename user sources.

    PySide6/Buildozer scan the entire working tree. A separate directory is
    the only reliable way to omit host-only settings without relocating
    files owned by the developer. The staging directory is disposable.
    """
    import os
    import shutil
    from velto_cli.adapters.mobile.qt.android_build_config import (
        ANDROID_TOOL_DIRS, android_exclusion_patterns, is_android_excluded,
    )

    patterns = android_exclusion_patterns(root)
    ignored_root_outputs = {"build", "dist", "bin", "release"}
    inspection = {p.name for p in android_root_inspection_dirs(root)}
    with tempfile.TemporaryDirectory(prefix="velto-android-stage-") as td:
        stage = Path(td) / root.name
        stage.mkdir()
        for base, dirs, files in os.walk(root, topdown=True, followlinks=False):
            parent = Path(base)
            relative_parent = parent.relative_to(root)
            dest = stage / relative_parent
            dest.mkdir(parents=True, exist_ok=True)
            kept = []
            for directory in sorted(dirs):
                rel = (relative_parent / directory).as_posix()
                if (directory in ANDROID_TOOL_DIRS or (parent == root and directory.startswith("."))
                        or (parent == root and directory.lower() in ignored_root_outputs)
                        or (parent == root and directory in inspection)
                        or (parent == root and directory.startswith(".velto-android-"))
                        or is_android_excluded(rel, patterns)):
                    continue
                child = parent / directory
                if child.is_symlink():
                    raise RuntimeError(
                        f"Android stage refuses symlinked project directory: {rel}"
                    )
                kept.append(directory)
            dirs[:] = kept
            for file in sorted(files):
                rel = (relative_parent / file).as_posix()
                if (is_android_excluded(rel, patterns) and rel != "settings/android.json"
                        or (parent == root and file.startswith("."))
                        or file.endswith((".apk", ".aab", ".pyc", ".pyo"))
                        or file in {"buildozer.spec"}):
                    # buildozer.spec is generated by the Android frontend.
                    continue
                source = parent / file
                if source.is_symlink():
                    raise RuntimeError(
                        f"Android stage refuses symlinked project file: {rel}"
                    )
                shutil.copy2(source, dest / file)
        # Qt's generated spec may contain absolute paths to the developer's
        # working tree. Redirect those references into the filtered copy.
        # This never modifies the original specification.
        spec = stage / "pysidedeploy.spec"
        if spec.is_file():
            content = spec.read_text(encoding="utf-8")
            if str(root) in content:
                spec.write_text(
                    content.replace(str(root), str(stage)), encoding="utf-8"
                )
        yield stage

