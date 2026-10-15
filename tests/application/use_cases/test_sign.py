from pathlib import Path
from unittest.mock import Mock, call

from qyro_cli.application.use_cases.sign import SignCompiledAppUseCase
from qyro_cli.domain.build import SigningArtifact


def test_sign_use_case_delegates_to_signer(tmp_path):
    signer = Mock()
    settings = Mock()
    guards = Mock()
    ui = Mock()

    settings.get_optional.side_effect = lambda key, default=None: {
        "app_name": "MyApp",
        "freeze_dir": "build",
        "sign": {
            "windows": {
                "certificate": "src/sign/windows/certificate.pfx",
                "password": "secret",
            }
        },
    }.get(key, default)

    expected = SigningArtifact(
        platform="windows",
        signed_paths=[tmp_path / "build" / "MyApp.exe"],
    )
    signer.sign.return_value = expected

    use_case = SignCompiledAppUseCase(
        signer=signer,
        settings=settings,
        guards=guards,
        ui=ui,
    )

    result = use_case.execute(
        project_root=tmp_path,
        target_platform="windows",
    )

    assert result is expected
    guards.require_existing_project.assert_called_once_with()
    guards.require_frozen_app.assert_called_once_with()
    signer.sign.assert_called_once_with(
        project_root=tmp_path,
        app_name="MyApp",
        freeze_dir="build",
        target_platform="windows",
        signing_options={
            "windows": {
                "certificate": "src/sign/windows/certificate.pfx",
                "password": "secret",
                "timestamp_server": "",
                "description": "MyApp",
                "url": "",
            },
            "mac": {
                "identity": "",
                "entitlements": "",
                "notary": {
                    "enabled": False,
                    "staple": True,
                    "assess_gatekeeper": False,
                    "keychain_profile": "",
                    "key_path": "",
                    "key_id": "",
                    "issuer": "",
                    "apple_id": "",
                    "team_id": "",
                    "app_password": "",
                },
            },
        },
    )
    assert settings.activate_profile.call_args_list == [
        call("release"),
        call("sign"),
    ]


def test_sign_use_case_check_runs_preflight(tmp_path):
    signer = Mock()
    settings = Mock()
    guards = Mock()
    ui = Mock()

    settings.get_optional.side_effect = lambda key, default=None: {
        "app_name": "MyApp",
        "freeze_dir": "build",
        "sign": {},
    }.get(key, default)

    use_case = SignCompiledAppUseCase(
        signer=signer,
        settings=settings,
        guards=guards,
        ui=ui,
    )

    use_case.check(project_root=tmp_path, target_platform="mac")

    signer.preflight.assert_called_once_with(
        project_root=tmp_path,
        app_name="MyApp",
        freeze_dir="build",
        target_platform="mac",
        signing_options={
            "windows": {
                "certificate": "src/sign/windows/certificate.pfx",
                "password": "",
                "timestamp_server": "",
                "description": "MyApp",
                "url": "",
            },
            "mac": {
                "identity": "",
                "entitlements": "",
                "notary": {
                    "enabled": False,
                    "staple": True,
                    "assess_gatekeeper": False,
                    "keychain_profile": "",
                    "key_path": "",
                    "key_id": "",
                    "issuer": "",
                    "apple_id": "",
                    "team_id": "",
                    "app_password": "",
                },
            },
        },
    )
    signer.sign.assert_not_called()
    ui.success.assert_called_once()


def test_sign_use_case_uses_root_name_when_missing_app_name(tmp_path):
    signer = Mock()
    settings = Mock()
    guards = Mock()
    ui = Mock()

    settings.get_optional.side_effect = lambda key, default=None: {
        "freeze_dir": "build",
        "sign": {},
    }.get(key, default)

    signer.sign.return_value = SigningArtifact(
        platform="windows",
        signed_paths=[Path("build") / "app.exe"],
    )

    use_case = SignCompiledAppUseCase(
        signer=signer,
        settings=settings,
        guards=guards,
        ui=ui,
    )

    use_case.execute(project_root=tmp_path, target_platform="windows")

    kwargs = signer.sign.call_args.kwargs
    assert kwargs["app_name"] == tmp_path.name


def test_sign_use_case_applies_mac_overrides(tmp_path):
    signer = Mock()
    settings = Mock()
    guards = Mock()
    ui = Mock()

    settings.get_optional.side_effect = lambda key, default=None: {
        "app_name": "MyApp",
        "freeze_dir": "build",
        "sign": {
            "mac": {
                "identity": "Developer ID Application: Example",
                "notary": {
                    "enabled": False,
                    "keychain_profile": "BASE-PROFILE",
                },
            }
        },
    }.get(key, default)

    signer.sign.return_value = SigningArtifact(platform="mac", signed_paths=[])

    use_case = SignCompiledAppUseCase(
        signer=signer,
        settings=settings,
        guards=guards,
        ui=ui,
    )

    use_case.execute(
        project_root=tmp_path,
        target_platform="mac",
        mac_overrides={
            "notary": {
                "enabled": True,
                "keychain_profile": "OVERRIDE-PROFILE",
            }
        },
    )

    kwargs = signer.sign.call_args.kwargs
    assert kwargs["signing_options"]["mac"]["notary"]["enabled"] is True
    assert kwargs["signing_options"]["mac"]["notary"]["keychain_profile"] == "OVERRIDE-PROFILE"
