"""Keep generated build outputs out of Buildozer's application source scan."""
from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
import tempfile
from typing import Iterator


@contextmanager
def isolate_previous_android_build_outputs(root: Path) -> Iterator[None]:
    """Temporarily move build/ outside the project before Android deploy.

    PySide6 regenerates buildozer.spec, so editing it beforehand is not a
    reliable way to exclude build/. Buildozer otherwise recursively copies
    old APK inspection directories, including unreadable device-extracted
    files, into .buildozer/android/app.

    Move rather than delete. Preserve all old outputs on both successful
    builds and failures. The returned APK is published *after* this context.
    """
    prior = root / "build"
    if not prior.exists():
        yield
        return
    if not prior.is_dir() or prior.is_symlink():
        raise RuntimeError(
            f"Cannot safely isolate Android build output: {prior}"
        )

    # Keep the temporary source tree outside of root so Buildozer's
    # recursive source walker cannot pick it up again.
    temporary = Path(tempfile.mkdtemp(
        prefix=".velto-android-build-", dir=root.parent,
    ))
    saved = temporary / "build"
    try:
        prior.rename(saved)
        try:
            yield
        finally:
            # Preserve the previous output even when a third-party builder
            # unexpectedly creates its own build/ tree.
            if prior.exists():
                if not prior.is_dir() or prior.is_symlink():
                    raise RuntimeError(
                        "Android deploy recreated build/ as a non-directory. "
                        f"Original build outputs remain at {saved}"
                    )
                for item in tuple(saved.iterdir()):
                    target = prior / item.name
                    if target.exists():
                        raise RuntimeError(
                            "Android deploy recreated a conflicting output "
                            f"{target}. Original outputs remain at {saved}"
                        )
                    item.rename(target)
                saved.rmdir()
            else:
                saved.rename(prior)
    finally:
        if not saved.exists():
            temporary.rmdir()
