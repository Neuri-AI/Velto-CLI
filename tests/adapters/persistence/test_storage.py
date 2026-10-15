
import json
import sys
import types
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from velto_cli.adapters.persistence.storage import (
    OsFileSystem,
    SettingsRepository,
)
from velto_cli.domain.errors import MissingSettingError


class TestOsFileSystem:
    def test_exists(self, tmp_path):
        path = tmp_path / "file.txt"
        path.write_text("hello")

        fs = OsFileSystem()

        assert fs.exists(str(path)) is True
        assert fs.exists(str(tmp_path / "missing.txt")) is False

    def test_is_dir(self, tmp_path):
        fs = OsFileSystem()

        assert fs.is_dir(str(tmp_path)) is True
        assert fs.is_dir(str(tmp_path / "missing")) is False

    def test_make_dir(self, tmp_path):
        path = tmp_path / "nested" / "directory"

        fs = OsFileSystem()
        fs.make_directory(str(path))

        assert path.is_dir()

    def test_write_file(self, tmp_path):
        path = tmp_path / "nested" / "file.txt"

        fs = OsFileSystem()
        fs.write_text(str(path), "hello world")

        assert path.read_text(encoding="utf-8") == "hello world"

    def test_read_file(self, tmp_path):
        path = tmp_path / "file.txt"
        path.write_text("hello", encoding="utf-8")

        fs = OsFileSystem()

        assert fs.read_text(str(path)) == "hello"

    def test_copy_tree(self, tmp_path):
        source = tmp_path / "source"
        destination = tmp_path / "destination"

        source.mkdir()
        (source / "file.txt").write_text("hello")

        fs = OsFileSystem()
        fs.copy_tree(str(source), destination)

        assert (
            destination / "file.txt"
        ).read_text() == "hello"

    def test_remove_dir(self, tmp_path):
        path = tmp_path / "directory"
        path.mkdir()
        (path / "file.txt").write_text("hello")

        fs = OsFileSystem()
        fs.remove_tree(str(path))

        assert not path.exists()

    def test_default_author(self, monkeypatch):
        monkeypatch.setattr(
            "velto_cli.adapters.persistence.storage.getpass.getuser",
            lambda: "test-user",
        )

        fs = OsFileSystem()

        assert fs.current_user() == "test-user"

    def test_default_author_fallback(self, monkeypatch):
        def raise_error():
            raise RuntimeError("failure")

        monkeypatch.setattr(
            "velto_cli.adapters.persistence.storage.getpass.getuser",
            raise_error,
        )

        fs = OsFileSystem()

        assert fs.current_user() == "Unknown"


class TestSettingsRepository:
    def _write_base(self, tmp_path, content=None):
        settings_dir = tmp_path / "settings"
        settings_dir.mkdir(parents=True, exist_ok=True)
        (settings_dir / "base.json").write_text(
            json.dumps(content or {}),
            encoding="utf-8",
        )

    def test_get(self, tmp_path):
        self._write_base(tmp_path, {"project_name": "MyApp"})

        repository = SettingsRepository(project_root=tmp_path)

        assert repository.get("project_name") == "MyApp"

    def test_get_missing_setting(self, tmp_path):
        self._write_base(tmp_path)
        repository = SettingsRepository(project_root=tmp_path)

        with pytest.raises(MissingSettingError):
            repository.get("missing")

    def test_get_optional(self, tmp_path):
        self._write_base(tmp_path)
        repository = SettingsRepository(project_root=tmp_path)

        assert repository.get_optional(
            "missing",
            "default",
        ) == "default"

    def test_get_optional_existing_value(self, tmp_path):
        self._write_base(tmp_path, {"name": "MyApp"})

        repository = SettingsRepository(project_root=tmp_path)

        assert repository.get_optional(
            "name",
            "default",
        ) == "MyApp"

    def test_set(self, tmp_path):
        self._write_base(tmp_path)
        repository = SettingsRepository(project_root=tmp_path)
        repository.set("name", "MyApp")

        assert repository.get_optional("name") == "MyApp"

    def test_base_settings_path(self, tmp_path):
        self._write_base(tmp_path)
        repository = SettingsRepository(project_root=tmp_path)

        assert (
            repository.base_settings_path
            == str(tmp_path / "settings" / "base.json")
        )

    def test_activate_profile(self, tmp_path):
        settings_dir = tmp_path / "settings"
        settings_dir.mkdir(parents=True)
        (settings_dir / "base.json").write_text(
            json.dumps({"project_name": "MyApp"}),
            encoding="utf-8",
        )
        (settings_dir / "development.json").write_text(
            json.dumps({"debug": True}),
            encoding="utf-8",
        )

        repository = SettingsRepository(project_root=tmp_path)
        repository.activate_profile("development")

        assert repository.get("project_name") == "MyApp"
        assert repository.get("debug") is True

    def test_persist_base(self, tmp_path):
        self._write_base(tmp_path)
        repository = SettingsRepository(project_root=tmp_path)

        values = {
            "name": "MyApp",
            "version": "1.0.0",
        }

        repository.persist_base(values)

        base_path = tmp_path / "settings" / "base.json"
        assert base_path.exists()
        assert json.loads(base_path.read_text(encoding="utf-8")) == values

