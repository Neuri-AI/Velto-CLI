"""
Filesystem, settings and template adapters.

The global mutable SETTINGS dict is now reached through SettingsRepository, so
use cases depend on an interface they can fake instead of on module state.
"""

import shutil
import json
import getpass
from pathlib import Path
from string import Template
from typing import Sequence, Any

from qyro_cli.domain.errors import MissingSettingError

BASE_SETTINGS = "settings/base.json"
SECRETS_SETTINGS = "settings/secrets.json"
SIGNING_SETTING_KEYS = {
    "sign",
    "windows_sign_certificate",
    "windows_sign_pass",
    "windows_sign_server",
    "windows_sign_description",
    "mac_sign_identity",
    "mac_sign_entitlements",
    "mac_sign_notarize",
    "mac_sign_staple",
    "mac_sign_assess",
    "mac_sign_notary_profile",
    "mac_sign_notary_key",
    "mac_sign_notary_key_id",
    "mac_sign_notary_issuer",
    "mac_sign_apple_id",
    "mac_sign_team_id",
    "mac_sign_app_password",
}
TEXT_EXTENSIONS = {
    ".cfg",
    ".ini",
    ".json",
    ".md",
    ".py",
    ".toml",
    ".txt",
    ".xml",
    ".yaml",
    ".yml",
}


class OsFileSystem:
    def exists(self, path: str) -> bool:
        return Path(path).exists()

    def is_dir(self, path: str) -> bool:
        return Path(path).is_dir()

    def resolve(self, relative_path: str) -> str:
        return str(Path(relative_path).resolve())

    def make_directory(self, relative_path: str) -> None:
        Path(relative_path).mkdir(parents=True, exist_ok=True)

    def remove_tree(self, relative_path: str) -> None:
        shutil.rmtree(relative_path, ignore_errors=True)

    def empty_directory(self, relative_path: str) -> None:
        directory = Path(relative_path)

        if not directory.exists():
            return

        for path in directory.iterdir():
            if path.is_dir():
                shutil.rmtree(path, ignore_errors=True)
            else:
                path.unlink(missing_ok=True)

    def write_text(
        self,
        relative_path: str,
        content: str,
    ) -> None:
        target = Path(relative_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")

    def read_text(self, relative_path: str) -> str:
        return Path(relative_path).read_text(encoding="utf-8")

    def copy_tree(
        self,
        source: Path,
        destination: Path,
    ) -> None:
        shutil.copytree(
            source,
            destination,
            dirs_exist_ok=True,
        )

    def current_user(self) -> str:
        try:
            return getpass.getuser()
        except Exception:
            return "Unknown"

    def _render_variable(self, value: object) -> str:
        if isinstance(value, (dict, list, tuple)):
            return json.dumps(value)

        return str(value)

    def render_tree(
        self,
        root: str,
        variables: dict[str, object],
        exclude: Sequence[str] | None = None,
    ) -> None:
        root_path = Path(root)
        excluded = set(exclude or ())

        rendered_variables = {
            key: self._render_variable(value)
            for key, value in variables.items()
        }

        for path in root_path.rglob("*"):
            if not path.is_file():
                continue

            if str(path.relative_to(root_path)) in excluded:
                continue

            if path.suffix.lower() not in TEXT_EXTENSIONS:
                continue

            content = path.read_text(encoding="utf-8")
            rendered = Template(content).substitute(rendered_variables)

            path.write_text(
                rendered,
                encoding="utf-8",
            )


class SettingsRepository:
    """
    SettingsPort over qyro's SETTINGS dict.

    `get` raises a typed domain error instead of a bare KeyError, so use cases
    never have to catch KeyError and guess what it meant.
    """

    def __init__(self, project_root: Path = None):
        self._root = project_root or Path.cwd()
        self._base_settings_path = self._detect_base_settings_path()
        self._settings = self._load_base()
        self._apply_secrets_overrides()

    @property
    def base_settings_path(self) -> str:
        return str(self._base_settings_path)

    def _detect_base_settings_path(self) -> Path:
        settings_base = self._root / BASE_SETTINGS
        # In non-project directories (e.g. before `qyro init`), the file may
        # not exist yet. Keep the canonical target path and allow lazy creation.
        return settings_base

    def _settings_dirs(self) -> list[Path]:
        settings_dir = self._root / "settings"
        if settings_dir.exists() and settings_dir.is_dir():
            return [settings_dir]
        return []

    def _load_base(self) -> dict:
        merged: dict[str, Any] = {}

        for base_path in [
            self._root / BASE_SETTINGS,
        ]:
            if not base_path.exists():
                continue
            try:
                data = json.loads(base_path.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    self._deep_merge(merged, data)
            except Exception:
                continue

        return merged

    def _load_json_dict(self, path: Path) -> dict[str, Any] | None:
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data
        except Exception:
            return None
        return None

    def _apply_secrets_overrides(self) -> None:
        """
        Apply protected application settings with highest precedence.

        This file is packaged in encrypted form for the application runtime
        and should never be committed to source control. Signing credentials
        belong in the separate, local-only settings/sign.json profile.
        """
        for secrets_path in [
            self._root / SECRETS_SETTINGS,
        ]:
            data = self._load_json_dict(secrets_path)
            if data:
                data = {
                    key: value
                    for key, value in data.items()
                    if key not in SIGNING_SETTING_KEYS
                }
                self._deep_merge(self._settings, data)

    def get(self, key: str):
        try:
            return self._settings[key]
        except KeyError:
            raise MissingSettingError(key) from None

    def get_optional(self, key: str, default: Any = None) -> Any:
        return self._settings.get(key, default)

    def set(self, key: str, value: Any) -> None:
        self._settings[key] = value

    def persist_base(self, values: dict[str, object]) -> None:
        path = self._base_settings_path
        path.parent.mkdir(parents=True, exist_ok=True)
        self._settings.update(values)
        path.write_text(json.dumps(self._settings, indent=4), encoding="utf-8")

    def activate_profile(self, profile: str) -> None:
        """Load platform / profile specific JSON from settings directories."""
        candidates: list[Path] = []
        for settings_dir in self._settings_dirs():
            candidates.extend([
                settings_dir / f"{profile}.json",
                settings_dir / f"{profile.lower()}.json",
            ])

        p_lower = profile.lower()
        if p_lower in ("windows", "win32", "win"):
            prefix: list[Path] = []
            for settings_dir in self._settings_dirs():
                prefix.extend([
                    settings_dir / "windows.json",
                    settings_dir / "release.json",
                ])
            candidates = prefix + candidates
        elif p_lower in ("linux", "linux2", "gnu"):
            prefix = []
            for settings_dir in self._settings_dirs():
                prefix.append(settings_dir / "linux.json")
            candidates = prefix + candidates
        elif p_lower in ("mac", "macos", "darwin", "osx"):
            prefix = []
            for settings_dir in self._settings_dirs():
                prefix.extend([
                    settings_dir / "macos.json",
                    settings_dir / "mac.json",
                ])
            candidates = prefix + candidates

        seen: set[Path] = set()
        for candidate in candidates:
            if candidate in seen:
                continue
            seen.add(candidate)
            if candidate.exists():
                try:
                    data = json.loads(candidate.read_text(encoding="utf-8"))
                    if isinstance(data, dict):
                        self._deep_merge(self._settings, data)
                except Exception:
                    pass

        # Re-apply local secret overrides to keep them highest precedence.
        self._apply_secrets_overrides()

    def _deep_merge(self, base: dict, update: dict) -> None:
        for k, v in update.items():
            if isinstance(v, dict) and k in base and isinstance(base[k], dict):
                self._deep_merge(base[k], v)
            else:
                base[k] = v
