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
    "settings/secrets.json",
    "src/sign/",
    "*.jks", "*.keystore", "*.p12", "*.pfx",
    "*.pem", "*.key", "*.env",
    ".gitignore", "*/.gitignore", ".gitkeep", "*/.gitkeep", ".DS_Store", "*/.DS_Store",
})
ANDROID_OUTPUT_DIRS = frozenset({"build", "deployment", "dist", "bin", "release"})
ANDROID_TOOL_DIRS = frozenset({
    ".git", ".venv", ".buildozer", ".pytest_cache", ".mypy_cache",
    ".ruff_cache", ".tox", ".nox", "__pycache__",
})
ANDROID_BUILD_FILES = frozenset({
    "buildozer.spec", "pysidedeploy.spec", "pyproject.toml", "poetry.lock",
    "uv.lock", "pipfile", "pipfile.lock", "setup.py", "setup.cfg", "manifest.in",
})
ANDROID_ARTIFACT_SUFFIXES = frozenset({".apk", ".aab", ".pyc", ".pyo", ".whl"})
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
    backend = value.get("protection_backend", "cython")
    if backend != "cython":
        raise ValueError(
            "Android protection_backend must be 'cython'; .pyc and Nuitka "
            "are not enabled for protected Android builds."
        )
    if "obfuscate" in value and type(value["obfuscate"]) is not bool:
        raise ValueError("android.json obfuscate must be boolean")
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
    if "sign" in data:
        raise ValueError(f"{ANDROID_SIGN_CONFIG} must define android at the root level")
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


def is_android_project_payload(relative: str, patterns: tuple[str, ...]) -> bool:
    """Whether a project file is application data, not build metadata."""
    path = PurePosixPath(relative)
    parts = path.parts
    if not parts or parts[0].startswith(".") or any(part in ANDROID_TOOL_DIRS for part in parts[:-1]):
        return False
    if parts[0].lower() in ANDROID_OUTPUT_DIRS:
        return False
    name = path.name.lower()
    if name in ANDROID_BUILD_FILES or name == "readme" or name.startswith("readme."):
        return False
    if len(parts) == 1 and (name == "license" or name.startswith("license.")):
        return False
    if path.suffix.lower() in ANDROID_ARTIFACT_SUFFIXES:
        return False
    return not is_android_excluded(relative, patterns)


def is_android_project_directory(relative: str, patterns: tuple[str, ...]) -> bool:
    parts = PurePosixPath(relative).parts
    return bool(parts) and not (
        parts[0].startswith(".") or any(part in ANDROID_TOOL_DIRS for part in parts)
        or parts[0].lower() in ANDROID_OUTPUT_DIRS
        or is_android_excluded(relative, patterns)
    )


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
                if (member.name.removeprefix("./").strip("/")
                    and member.name.removeprefix("./").strip("/") not in {
                        ".velto", ".velto/resources.pak", ".velto/runtime.so",
                    }
                    and not (is_android_project_directory if member.isdir()
                             else is_android_project_payload)(
                                 member.name.removeprefix("./").strip("/"), patterns
                             ))
            )
    if forbidden:
        raise RuntimeError(
            "Android APK contains excluded build-only files: "
            + ", ".join(forbidden[:12])
        )


