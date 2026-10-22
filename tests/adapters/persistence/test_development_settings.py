"""Source profile loading must not activate release packaging settings."""
import json

import pytest

from velto_cli.adapters.persistence.storage import SettingsRepository


def test_source_profile_recursively_merges_without_release(tmp_path, monkeypatch):
    import velto_cli.adapters.persistence.storage as storage
    monkeypatch.setattr(storage.sys, "platform", "linux")
    settings = tmp_path / "settings"
    settings.mkdir()
    data = {
        "base": {"hot_reload": {"enabled": False,
                 "module_reload": {"third_party": "restart"}}},
        "linux": {"hot_reload": {"debounce_ms": 250}},
        "development": {"hot_reload": {"enabled": True, "debounce_ms": 150}},
        "release": {"hot_reload": {"enabled": False}, "bundle_mode": "onefile"},
    }
    for name, value in data.items():
        (settings / (name + ".json")).write_text(json.dumps(value), encoding="utf-8")
    repo = SettingsRepository(project_root=tmp_path)
    repo.activate_development_profile()
    assert repo.get("hot_reload") == {
        "enabled": True,
        "debounce_ms": 150,
        "module_reload": {"third_party": "restart"},
    }
    assert repo.get_optional("bundle_mode") is None


def test_invalid_development_profile_has_diagnostic(tmp_path):
    settings = tmp_path / "settings"
    settings.mkdir()
    (settings / "development.json").write_text("[]", encoding="utf-8")
    with pytest.raises(ValueError, match="development.json"):
        SettingsRepository(project_root=tmp_path).activate_development_profile()
