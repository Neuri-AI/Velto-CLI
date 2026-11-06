"""Host-only Android settings, signing metadata, and centralized APK exclusions."""
from __future__ import annotations

from fnmatch import fnmatchcase
import json
import os
from pathlib import Path, PurePosixPath

ANDROID_CONFIG = "settings/android.json"
ANDROID_SIGN_CONFIG = "settings/sign.json"
# Extend this set as new host-only files are discovered. Never package them.
ALWAYS_EXCLUDED = frozenset({
    ANDROID_CONFIG,
    ANDROID_SIGN_CONFIG,
    "*.jks", "*.keystore", "*.p12", "*.pfx",
})
_IGNORED_TREES = frozenset({
    ".git", ".buildozer", "build", ".venv", "venv", "__pycache__",
    ".pytest_cache", "node_modules",
})


def load_android_build_config(root: Path) -> dict:
    path = root / ANDROID_CONFIG
    if not path.exists():
        return {}
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"{ANDROID_CONFIG} must be a regular file")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ValueError(f"Cannot read {ANDROID_CONFIG}: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"{ANDROID_CONFIG} must be a JSON object")
    if "protect_source" in value and type(value["protect_source"]) is not bool:
        raise ValueError("android.json protect_source must be boolean")
    if "signing" in value:
        raise ValueError(
            "Android signing belongs in settings/sign.json under the android key, "
            "not in settings/android.json"
        )
    patterns = value.get("exclude", [])
    if not isinstance(patterns, list) or any(
        not isinstance(pattern, str) or not pattern.strip()
        or pattern.startswith("/") or ".." in PurePosixPath(pattern).parts
        or "\\" in pattern
        for pattern in patterns
    ):
        raise ValueError("android.json exclude must be a list of relative paths/globs")
    return value


def load_android_signing(root: Path) -> dict:
    """Load the Android section of the existing host-only sign.json profile.

    Preserve the unrelated Windows and macOS signing sections. Never merge
    this config with application runtime settings.
    """
    path = root / ANDROID_SIGN_CONFIG
    if not path.exists():
        return {}
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"{ANDROID_SIGN_CONFIG} must be a regular file")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ValueError(f"Cannot read {ANDROID_SIGN_CONFIG}: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError(f"{ANDROID_SIGN_CONFIG} must be a JSON object")
    # The sign command accepts the platform keys directly. Also recognize
    # its older {"sign": {...}} wrapper to avoid breaking older projects.
    data = data.get("sign", data)
    if not isinstance(data, dict):
        raise ValueError(f"{ANDROID_SIGN_CONFIG} sign must be an object")
    android = data.get("android", {})
    if not isinstance(android, dict):
        raise ValueError(f"{ANDROID_SIGN_CONFIG} android must be an object")
    return android


def android_exclusion_patterns(root: Path) -> tuple[str, ...]:
    config = load_android_build_config(root)
    patterns = set(ALWAYS_EXCLUDED)
    patterns.update(config.get("exclude", []))
    # An explicitly configured keystore inside the project must never be
    # shipped, regardless of its extension or location.
    signing = load_android_signing(root)
    stored = signing.get("keystore")
    if isinstance(stored, str) and stored:
        location = Path(stored).expanduser()
        if not location.is_absolute():
            location = root / location
        try:
            relative = location.resolve().relative_to(root.resolve())
        except ValueError:
            pass  # External keystore is outside the project's source scan.
        else:
            patterns.add(relative.as_posix())
    return tuple(sorted(patterns))


def is_android_excluded(relative: str, patterns: tuple[str, ...]) -> bool:
    name = relative.replace("\\", "/").removeprefix("./")
    for pattern in patterns:
        pattern = pattern.rstrip("/")
        if name == pattern or name.startswith(pattern + "/") or fnmatchcase(name, pattern):
            return True
    return False


def verify_android_exclusions(apk: Path, root: Path) -> None:
    """Reject any excluded project file in the final signed archive."""
    import io
    import tarfile
    from zipfile import ZipFile
    patterns = android_exclusion_patterns(root)
    with ZipFile(apk) as z:
        private = [n for n in ("assets/private.tar", "assets/private.mp3") if n in z.namelist()]
        if len(private) != 1:
            raise RuntimeError("Android APK lacks a unique private archive")
        with tarfile.open(fileobj=io.BytesIO(z.read(private[0])), mode="r:*") as tar:
            forbidden = sorted(
                member.name.removeprefix("./").strip("/")
                for member in tar
                if member.isfile() and is_android_excluded(
                    member.name.removeprefix("./").strip("/"), patterns
                )
            )
    if forbidden:
        raise RuntimeError(
            "Android APK contains excluded build-only files: "
            + ", ".join(forbidden[:12])
        )


def assert_android_full_build_safe(root: Path) -> None:
    """Fail closed rather than moving developer files into ephemeral storage.

    The PySide6 Android frontend recursively stages the project and provides
    no guaranteed per-file exclusion hook. Incremental builds exclude files
    without touching them; full builds are blocked until a separate staging
    workspace can be used safely.
    """
    patterns = android_exclusion_patterns(root)
    found = []
    for parent, dirs, files in os.walk(root, followlinks=False):
        directory = Path(parent)
        dirs[:] = [name for name in dirs if name not in _IGNORED_TREES]
        for name in (*dirs, *files):
            relative = (directory / name).relative_to(root).as_posix()
            if is_android_excluded(relative, patterns):
                found.append(relative)
        dirs[:] = [
            name for name in dirs if not is_android_excluded(
                (directory / name).relative_to(root).as_posix(), patterns
            )
        ]
    if found:
        raise RuntimeError(
            "Refusing full Android deployment while excluded project files "
            "are present: " + ", ".join(sorted(found)[:12]) + ". "
            "Files were NOT moved or deleted. Incremental builds may reuse "
            "the APK cache; full builds require isolated source staging."
        )


