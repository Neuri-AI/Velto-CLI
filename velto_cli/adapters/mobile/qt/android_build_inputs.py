"""Keep generated build outputs out of Buildozer's application source scan."""
from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
import shutil
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
    with tempfile.TemporaryDirectory(
        prefix=".velto-android-build-", dir=root.parent,
    ) as temporary:
        saved = Path(temporary) / "build"
        prior.rename(saved)
        try:
            yield
        finally:
            # Normal PySide6 deployment places the intermediate APK at the
            # project root. If an external tool recreated build/, do not
            # destroy either version on restoration.
            if prior.exists():
                if not prior.is_dir() or prior.is_symlink():
                    raise RuntimeError(
                        "Android deploy recreated build/ as a non-directory; "
                        f"saved output remains in {saved}"
                    )
                for item in saved.iterdir():
                    target = prior / item.name
                    if target.exists():
                        raise RuntimeError(
                            "Android deploy created conflicting build output "
                            f"{target}. Previous build remains in {saved}"
                        )
                    item.rename(target)
                saved.rmdir()
            else:
                saved.rename(prior)
