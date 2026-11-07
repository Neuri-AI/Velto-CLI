"""Generate the bootstrap required by python-for-android."""
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator


@contextmanager
def android_entrypoint(root: Path, entry_point: str) -> Iterator[None]:
    """Provide p4a's required main.py while preserving the configured entry."""
    entry = (root / entry_point).resolve()
    if not entry.is_relative_to(root.resolve()) or entry.suffix != ".py":
        raise ValueError("Android entry_point must be a Python file inside the project")
    if entry == (root / "main.py").resolve():
        yield
        return
    launcher = root / "main.py"
    if launcher.is_symlink() or (launcher.exists() and not launcher.is_file()):
        raise ValueError("Android launcher cannot replace a symlink or non-file main.py")
    original = launcher.read_bytes() if launcher.is_file() else None
    relative = entry.relative_to(root.resolve()).as_posix()
    # The same launcher is imported as ``main`` if the selected entry uses the
    # project's original main.py as a module. Preserve that behavior.
    original_branch = (
        f"else:\n    exec(compile({original!r}, __file__, 'exec'), globals())\n"
        if original is not None else ""
    )
    launcher.write_text(
        "if __name__ == '__main__':\n"
        "    import runpy\n    import sys\n    from pathlib import Path\n"
        f"    _entry = Path(__file__).resolve().parent / {relative!r}\n"
        "    sys.path.insert(0, str(_entry.parent))\n"
        "    runpy.run_path(str(_entry), run_name='__main__')\n"
        + original_branch,
        encoding="utf-8",
    )
    try:
        yield
    finally:
        if original is None:
            launcher.unlink(missing_ok=True)
        else:
            launcher.write_bytes(original)


