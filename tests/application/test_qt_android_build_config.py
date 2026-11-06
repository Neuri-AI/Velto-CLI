"""Host-only Android settings must never enter a full or incremental APK."""
from __future__ import annotations

import io
import tarfile
from pathlib import Path
from zipfile import ZipFile

import pytest

from velto_cli.adapters.mobile.qt.android_build_config import (
    android_exclusion_patterns,
    is_android_excluded,
    isolate_android_excluded_sources,
    load_android_build_config,
    verify_android_exclusions,
)
from velto_cli.adapters.mobile.qt.android_sources import _project_runtime_assets


def test_android_settings_are_not_runtime_assets(tmp_path):
    settings = tmp_path / "settings"
    settings.mkdir()
    (settings / "base.json").write_text('{"framework": "pyside6"}')
    (settings / "android.json").write_text(
        '{"protect_source": true, "signing": {"keystore_password": "NEVER-SHIP"}}'
    )
    assets = _project_runtime_assets(tmp_path)
    assert "settings/base.json" in assets
    assert "settings/android.json" not in assets


def test_exclusion_policy_accepts_future_paths_and_globs(tmp_path):
    settings = tmp_path / "settings"
    settings.mkdir()
    (settings / "android.json").write_text(
        '{"exclude": ["private/", "settings/*.secrets.json"]}'
    )
    patterns = android_exclusion_patterns(tmp_path)
    assert is_android_excluded("settings/android.json", patterns)
    assert is_android_excluded("private/credentials.pem", patterns)
    assert is_android_excluded("settings/prod.secrets.json", patterns)
    assert not is_android_excluded("settings/base.json", patterns)


def test_isolation_restores_excluded_files_after_failure(tmp_path):
    settings = tmp_path / "settings"
    settings.mkdir()
    android = settings / "android.json"
    android.write_text('{"protect_source": true}')
    with pytest.raises(RuntimeError, match="deploy failed"):
        with isolate_android_excluded_sources(tmp_path):
            assert not android.exists()
            raise RuntimeError("deploy failed")
    assert load_android_build_config(tmp_path)["protect_source"] is True


def test_isolation_hides_other_excluded_files_without_deleting(tmp_path):
    settings = tmp_path / "settings"
    settings.mkdir()
    (settings / "android.json").write_text('{"exclude": ["private/"]}')
    private = tmp_path / "private"
    private.mkdir()
    secret = private / "key.txt"
    secret.write_text("private")
    with isolate_android_excluded_sources(tmp_path):
        assert not (settings / "android.json").exists()
        assert not private.exists()
    assert secret.read_text() == "private"


def test_source_policy_rejects_excluded_apk_member(tmp_path):
    settings = tmp_path / "settings"
    settings.mkdir()
    (settings / "android.json").write_text('{"protect_source": true}')
    data = io.BytesIO()
    with tarfile.open(fileobj=data, mode="w:gz") as tar:
        blob = b'{"keystore_password": "LEAKED"}'
        member = tarfile.TarInfo("settings/android.json")
        member.size = len(blob)
        tar.addfile(member, io.BytesIO(blob))
    apk = tmp_path / "app.apk"
    with ZipFile(apk, "w") as z:
        z.writestr("assets/private.tar", data.getvalue())
    with pytest.raises(RuntimeError, match="excluded build-only files"):
        verify_android_exclusions(apk, tmp_path)


def test_protect_source_is_read_from_android_json(tmp_path):
    from unittest.mock import Mock
    from velto_cli.application.use_cases.freeze_mobile import FreezeMobileUseCase
    from velto_cli.domain.mobile import MobileTarget
    settings = tmp_path / "settings"
    settings.mkdir()
    (settings / "android.json").write_text('{"protect_source": true}')
    (tmp_path / "pyproject.toml").write_text("[project]\nname='demo'\n")
    (tmp_path / "main.py").write_text("from velto import VeltoContext\n")
    provider = Mock()
    provider.get_optional.side_effect = lambda key, default=None: {
        "framework": "pyside6",
    }.get(key, default)
    builder = Mock()
    builder.freeze.return_value = None
    FreezeMobileUseCase(builder, provider, Mock()).execute(
        target=MobileTarget.ANDROID, project_root=tmp_path, debug=True
    )
    assert builder.freeze.call_args.args[0].protect_source is True


def test_release_credentials_read_android_json(tmp_path):
    from velto_cli.adapters.mobile.qt.android_release import _release_credentials
    settings = tmp_path / "settings"
    settings.mkdir()
    store = tmp_path / "release.jks"
    store.write_bytes(b"keystore")
    (settings / "android.json").write_text(
        '{"signing": {"keystore": "release.jks", "alias": "release", '
        '"keystore_password": "store-pass", "key_password": "key-pass"}}'
    )
    assert _release_credentials(tmp_path) == (
        store.resolve(), "release", "store-pass", "key-pass"
    )


def test_release_credentials_env_override_json(tmp_path, monkeypatch):
    from velto_cli.adapters.mobile.qt.android_release import _release_credentials
    settings = tmp_path / "settings"
    settings.mkdir()
    store = tmp_path / "ci.jks"
    store.write_bytes(b"ci")
    (settings / "android.json").write_text(
        '{"signing": {"keystore": "missing.jks", "alias": "old", '
        '"keystore_password": "old", "key_password": "old"}}'
    )
    monkeypatch.setenv("VELTO_ANDROID_KEYSTORE", str(store))
    monkeypatch.setenv("VELTO_ANDROID_KEY_ALIAS", "ci")
    monkeypatch.setenv("VELTO_ANDROID_KEYSTORE_PASSWORD", "ci-store-pass")
    monkeypatch.setenv("VELTO_ANDROID_KEY_PASSWORD", "ci-key-pass")
    assert _release_credentials(tmp_path) == (
        store.resolve(), "ci", "ci-store-pass", "ci-key-pass"
    )
