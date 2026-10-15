import json

from qyro_cli.adapters.persistence.storage import SettingsRepository


def test_loads_base_from_settings_base(tmp_path):
    settings_dir = tmp_path / "settings"
    settings_dir.mkdir(parents=True)
    (settings_dir / "base.json").write_text(
        json.dumps({"app_name": "LegacyApp", "version": "1.2.3"}),
        encoding="utf-8",
    )

    repo = SettingsRepository(project_root=tmp_path)

    assert repo.get("app_name") == "LegacyApp"
    assert repo.get("version") == "1.2.3"


def test_activate_profile_reads_release_from_settings_directory(tmp_path):
    settings_dir = tmp_path / "settings"
    settings_dir.mkdir(parents=True)
    (settings_dir / "base.json").write_text(
        json.dumps({"app_name": "LegacyApp"}),
        encoding="utf-8",
    )
    (settings_dir / "release.json").write_text(
        json.dumps(
            {
                "bundle": {
                    "extra_files": ["README.md"],
                    "dmg": {"icon_size": 120},
                }
            }
        ),
        encoding="utf-8",
    )

    repo = SettingsRepository(project_root=tmp_path)
    repo.activate_profile("release")

    bundle = repo.get_optional("bundle", {})
    assert bundle.get("extra_files") == ["README.md"]
    assert bundle.get("dmg", {}).get("icon_size") == 120


def test_secrets_override_base_settings(tmp_path):
    settings_dir = tmp_path / "settings"
    settings_dir.mkdir(parents=True)
    (settings_dir / "base.json").write_text(
        json.dumps({"api_token": "from-base"}),
        encoding="utf-8",
    )
    (settings_dir / "secrets.json").write_text(
        json.dumps({"api_token": "from-secrets"}),
        encoding="utf-8",
    )

    repo = SettingsRepository(project_root=tmp_path)

    assert repo.get_optional("api_token") == "from-secrets"


def test_secrets_remain_highest_precedence_after_profile_activation(tmp_path):
    settings_dir = tmp_path / "settings"
    settings_dir.mkdir(parents=True)
    (settings_dir / "base.json").write_text(
        json.dumps({"api_token": "from-base"}),
        encoding="utf-8",
    )
    (settings_dir / "release.json").write_text(
        json.dumps({"api_token": "from-release"}),
        encoding="utf-8",
    )
    (settings_dir / "secrets.json").write_text(
        json.dumps({"api_token": "from-secrets"}),
        encoding="utf-8",
    )

    repo = SettingsRepository(project_root=tmp_path)
    repo.activate_profile("release")

    assert repo.get_optional("api_token") == "from-secrets"


def test_activate_sign_profile_reads_sign_json(tmp_path):
    settings_dir = tmp_path / "settings"
    settings_dir.mkdir(parents=True)
    (settings_dir / "base.json").write_text(
        json.dumps({"app_name": "MyApp"}),
        encoding="utf-8",
    )
    (settings_dir / "sign.json").write_text(
        json.dumps(
            {
                "sign": {
                    "windows": {
                        "certificate": "certificates/app.pfx",
                        "password": "local-password",
                    }
                }
            }
        ),
        encoding="utf-8",
    )

    repo = SettingsRepository(project_root=tmp_path)
    assert repo.get_optional("sign") is None

    repo.activate_profile("sign")

    windows = repo.get_optional("sign", {}).get("windows", {})
    assert windows["certificate"] == "certificates/app.pfx"
    assert windows["password"] == "local-password"


def test_signing_configuration_is_not_loaded_from_secrets_json(tmp_path):
    settings_dir = tmp_path / "settings"
    settings_dir.mkdir(parents=True)
    (settings_dir / "base.json").write_text("{}", encoding="utf-8")
    (settings_dir / "secrets.json").write_text(
        json.dumps(
            {
                "api_token": "runtime-secret",
                "sign": {"windows": {"password": "wrong-file"}},
                "windows_sign_pass": "legacy-wrong-file",
            }
        ),
        encoding="utf-8",
    )

    repo = SettingsRepository(project_root=tmp_path)

    assert repo.get_optional("api_token") == "runtime-secret"
    assert repo.get_optional("sign") is None
    assert repo.get_optional("windows_sign_pass") is None
