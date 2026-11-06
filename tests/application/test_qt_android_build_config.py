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
    load_android_build_config,
    verify_android_exclusions,
)
from velto_cli.adapters.mobile.qt.android_sources import _project_runtime_assets


def test_android_settings_are_not_runtime_assets(tmp_path):
    settings = tmp_path / "settings"
    settings.mkdir()
    (settings / "base.json").write_text('{"framework": "pyside6"}')
    (settings / "android.json").write_text(
        '{"protect_source": true}'
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


def test_release_credentials_read_sign_json(tmp_path):
    import json
    import secrets
    from velto_cli.adapters.mobile.qt.android_release import _release_credentials

    settings = tmp_path / "settings"
    settings.mkdir()
    store = tmp_path / "release.jks"
    store.write_bytes(b"keystore")
    store_password = secrets.token_urlsafe(18)
    key_password = secrets.token_urlsafe(18)
    (settings / "sign.json").write_text(json.dumps({
        "android": {
            "keystore": "release.jks",
            "alias": "release",
            "keystore_password": store_password,
            "key_password": key_password,
        },
    }))
    assert _release_credentials(tmp_path) == (
        store.resolve(), "release", store_password, key_password
    )


def test_release_credentials_env_override_sign_json(tmp_path, monkeypatch):
    import json
    import secrets
    from velto_cli.adapters.mobile.qt.android_release import _release_credentials

    settings = tmp_path / "settings"
    settings.mkdir()
    store = tmp_path / "ci.jks"
    store.write_bytes(b"ci")
    (settings / "sign.json").write_text(json.dumps({
        "android": {
            "keystore": "missing.jks", "alias": "old",
            "keystore_password": secrets.token_urlsafe(18),
            "key_password": secrets.token_urlsafe(18),
        },
    }))
    store_password = secrets.token_urlsafe(18)
    key_password = secrets.token_urlsafe(18)
    monkeypatch.setenv("VELTO_ANDROID_KEYSTORE", str(store))
    monkeypatch.setenv("VELTO_ANDROID_KEY_ALIAS", "ci")
    monkeypatch.setenv("VELTO_ANDROID_KEYSTORE_PASSWORD", store_password)
    monkeypatch.setenv("VELTO_ANDROID_KEY_PASSWORD", key_password)
    assert _release_credentials(tmp_path) == (
        store.resolve(), "ci", store_password, key_password
    )


def test_project_relative_signing_keystore_is_always_excluded(tmp_path):
    settings = tmp_path / "settings"
    settings.mkdir()
    (settings / "sign.json").write_text(
        '{"android": {"keystore": "keys/private-release.bin"}}'
    )
    (tmp_path / "keys").mkdir()
    store = tmp_path / "keys/private-release.bin"
    store.write_bytes(b"PRIVATE-KEY")
    patterns = android_exclusion_patterns(tmp_path)
    assert is_android_excluded("keys/private-release.bin", patterns)
    from velto_cli.adapters.mobile.qt.android_build_inputs import staged_android_project
    with staged_android_project(tmp_path) as stage:
        assert not (stage / "keys/private-release.bin").exists()
        assert store.is_file()
    assert store.read_bytes() == b"PRIVATE-KEY"


def test_init_spec_does_not_require_release_protection(tmp_path):
    from unittest.mock import Mock
    from velto_cli.application.use_cases.freeze_mobile import FreezeMobileUseCase
    from velto_cli.domain.mobile import MobileTarget
    (tmp_path / "pyproject.toml").write_text("[project]\nname='demo'\n")
    settings = tmp_path / "settings"
    settings.mkdir()
    (settings / "android.json").write_text('{"protect_source": true}')
    provider = Mock()
    provider.get_optional.side_effect = lambda key, default=None: {
        "framework": "pyside6",
    }.get(key, default)
    builder = Mock()
    builder.freeze.return_value = None
    FreezeMobileUseCase(builder, provider, Mock()).execute(
        target=MobileTarget.ANDROID, project_root=tmp_path, init_only=True,
    )
    assert builder.freeze.call_args.args[0].protect_source is False


def test_sign_json_is_never_bundled_and_windows_mac_preserved(tmp_path):
    from velto_cli.adapters.mobile.qt.android_build_config import load_android_signing
    settings = tmp_path / "settings"
    settings.mkdir()
    content = {
        "windows": {"certificate": "sign/windows/certificate.pfx"},
        "mac": {"identity": "Developer ID Application"},
        "android": {"keystore": "sign/android/release.jks", "alias": "release"},
    }
    import json
    (settings / "sign.json").write_text(json.dumps(content))
    assert load_android_signing(tmp_path) == content["android"]
    assert "settings/sign.json" not in _project_runtime_assets(tmp_path)
    assert is_android_excluded("settings/sign.json", android_exclusion_patterns(tmp_path))
    from velto_cli.adapters.mobile.qt.android_build_inputs import staged_android_project
    with staged_android_project(tmp_path) as stage:
        assert not (stage / "settings/sign.json").exists()
        assert (settings / "sign.json").exists()
    assert json.loads((settings / "sign.json").read_text()) == content


def test_sign_json_legacy_sign_wrapper(tmp_path):
    from velto_cli.adapters.mobile.qt.android_build_config import load_android_signing
    settings = tmp_path / "settings"
    settings.mkdir()
    (settings / "sign.json").write_text('{"sign": {"android": {"alias": "old"}}}')
    assert load_android_signing(tmp_path) == {"alias": "old"}


def test_android_json_does_not_accept_signing_secrets(tmp_path):
    settings = tmp_path / "settings"
    settings.mkdir()
    (settings / "android.json").write_text('{"signing": {"alias": "release"}}')
    with pytest.raises(ValueError, match="settings/sign.json"):
        load_android_build_config(tmp_path)


def test_sign_json_leak_in_apk_is_rejected(tmp_path):
    settings = tmp_path / "settings"
    settings.mkdir()
    import json
    import secrets
    (settings / "sign.json").write_text(json.dumps({
        "android": {"key_password": secrets.token_urlsafe(18)},
    }))
    private = io.BytesIO()
    with tarfile.open(fileobj=private, mode="w:gz") as tar:
        raw = (settings / "sign.json").read_bytes()
        info = tarfile.TarInfo("settings/sign.json")
        info.size = len(raw)
        tar.addfile(info, io.BytesIO(raw))
    apk = tmp_path / "signed.apk"
    with ZipFile(apk, "w") as z:
        z.writestr("assets/private.tar", private.getvalue())
    with pytest.raises(RuntimeError, match="excluded build-only files"):
        verify_android_exclusions(apk, tmp_path)


def test_full_build_guard_never_moves_excluded_user_files(tmp_path):
    from velto_cli.adapters.mobile.qt.android_build_config import (
        assert_android_full_build_safe,
    )
    settings = tmp_path / "settings"
    settings.mkdir()
    sign = settings / "sign.json"
    sign.write_text('{"android": {"alias": "demo"}}')
    android = settings / "android.json"
    android.write_text('{"exclude": ["settings/sign.json"]}')
    with pytest.raises(RuntimeError, match="Files were NOT moved or deleted"):
        assert_android_full_build_safe(tmp_path)
    assert sign.is_file()
    assert android.is_file()
    assert not list(tmp_path.parent.glob(".velto-android-excluded-*"))


def test_full_android_stage_keeps_original_files_and_excludes_secrets(tmp_path):
    from velto_cli.adapters.mobile.qt.android_build_inputs import staged_android_project
    root = tmp_path / "AndroidAppQt"
    (root / "settings").mkdir(parents=True)
    (root / "components").mkdir()
    (root / "main.py").write_text("print('test')\n")
    (root / "components/test.py").write_text("COMPONENT = 1\n")
    (root / "pysidedeploy.spec").write_text("[app]\n")
    (root / "settings/base.json").write_text('{"framework":"pyside6"}')
    (root / "settings/android.json").write_text(
        '{"protect_source": true, "exclude": ["settings/development.json"]}'
    )
    (root / "settings/development.json").write_text('{"debug":true}')
    (root / "settings/sign.json").write_text('{"android":{"alias":"release"}}')
    (root / "settings/secrets.json").write_text('{"token":"private"}')
    (root / "build").mkdir()
    (root / "build/old.apk").write_bytes(b"old")
    (root / ".buildozer").mkdir()
    (root / ".buildozer/old.txt").write_text("old")

    with staged_android_project(root) as stage:
        assert stage != root
        assert (stage / "main.py").is_file()
        assert (stage / "components/test.py").is_file()
        assert (stage / "pysidedeploy.spec").is_file()
        assert (stage / "settings/base.json").is_file()
        for name in ("android.json", "development.json", "sign.json", "secrets.json"):
            assert not (stage / "settings" / name).exists()
            assert (root / "settings" / name).is_file()
        assert not (stage / "build").exists()
        assert not (stage / ".buildozer").exists()
        staging_parent = stage.parent
    assert not staging_parent.exists()
    assert (root / "build/old.apk").read_bytes() == b"old"
    assert (root / ".buildozer/old.txt").read_text() == "old"


def test_android_stage_cleanup_after_failure_does_not_touch_user_files(tmp_path):
    from velto_cli.adapters.mobile.qt.android_build_inputs import staged_android_project
    root = tmp_path / "app"
    (root / "settings").mkdir(parents=True)
    sign = root / "settings/sign.json"
    sign.write_text('{"mac": {"identity":"example"}}')
    stage_parent = None
    with pytest.raises(RuntimeError, match="toolchain error"):
        with staged_android_project(root) as stage:
            stage_parent = stage.parent
            assert sign.is_file()
            raise RuntimeError("toolchain error")
    assert stage_parent is not None and not stage_parent.exists()
    assert sign.is_file()


def test_android_stage_rejects_symlinked_source_without_modification(tmp_path):
    from velto_cli.adapters.mobile.qt.android_build_inputs import staged_android_project
    root = tmp_path / "app"
    root.mkdir()
    outside = tmp_path / "outside.py"
    outside.write_text("SECRET = True")
    (root / "other.py").symlink_to(outside)
    with pytest.raises(RuntimeError, match="symlinked project file"):
        with staged_android_project(root):
            pass
    assert (root / "other.py").is_symlink()
    assert outside.read_text() == "SECRET = True"
