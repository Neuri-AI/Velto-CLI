from pathlib import Path

import pytest

from velto_cli.adapters.package.compiled_app_signer import CompiledAppSigner
from velto_cli.domain.errors import MissingDependencyError, QyroError


def test_preflight_windows_requires_signtool(tmp_path, monkeypatch):
    freeze_dir = tmp_path / "build"
    freeze_dir.mkdir(parents=True)
    (freeze_dir / "MyApp.exe").write_text("binary", encoding="utf-8")

    signer = CompiledAppSigner()
    monkeypatch.setattr("shutil.which", lambda name: None)

    with pytest.raises(MissingDependencyError):
        signer.preflight(
            project_root=tmp_path,
            app_name="MyApp",
            freeze_dir="build",
            target_platform="windows",
            signing_options={
                "windows": {
                    "certificate": "src/sign/windows/certificate.pfx",
                    "password": "secret",
                }
            },
        )


def test_sign_windows_signs_supported_extensions(tmp_path, monkeypatch):
    freeze_dir = tmp_path / "build"
    freeze_dir.mkdir(parents=True)
    exe = freeze_dir / "MyApp.exe"
    dll = freeze_dir / "helper.dll"
    txt = freeze_dir / "README.txt"
    exe.write_text("exe", encoding="utf-8")
    dll.write_text("dll", encoding="utf-8")
    txt.write_text("text", encoding="utf-8")

    cert_dir = tmp_path / "src" / "sign" / "windows"
    cert_dir.mkdir(parents=True)
    (cert_dir / "certificate.pfx").write_text("cert", encoding="utf-8")

    monkeypatch.setattr("shutil.which", lambda name: "signtool" if name == "signtool" else None)

    signer = CompiledAppSigner()
    commands: list[list[str]] = []

    def fake_run(command: list[str]) -> None:
        commands.append(command)

    monkeypatch.setattr(signer, "_run", fake_run)

    artifact = signer.sign(
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
            }
        },
    )

    assert artifact.platform == "windows"
    assert len(artifact.signed_paths) == 2
    assert exe in artifact.signed_paths
    assert dll in artifact.signed_paths
    assert all(str(txt) not in " ".join(cmd) for cmd in commands)


def test_sign_mac_runs_codesign_and_verify(tmp_path, monkeypatch):
    freeze_dir = tmp_path / "build"
    app_bundle = freeze_dir / "MyApp.app"
    app_contents = app_bundle / "Contents" / "MacOS"
    app_contents.mkdir(parents=True)
    (app_contents / "MyApp").write_text("binary", encoding="utf-8")

    def fake_which(name: str):
        if name == "codesign":
            return "/usr/bin/codesign"
        if name == "xattr":
            return "/usr/bin/xattr"
        return None

    monkeypatch.setattr("shutil.which", fake_which)

    signer = CompiledAppSigner()
    commands: list[list[str]] = []

    def fake_run(command: list[str]) -> None:
        commands.append(command)

    monkeypatch.setattr(signer, "_run", fake_run)

    artifact = signer.sign(
        project_root=tmp_path,
        app_name="MyApp",
        freeze_dir="build",
        target_platform="mac",
        signing_options={
            "mac": {
                "identity": "Developer ID Application: Example",
                "entitlements": "",
                "notary": {
                    "enabled": False,
                    "staple": True,
                    "assess_gatekeeper": False,
                },
            }
        },
    )

    assert artifact.platform == "mac"
    assert artifact.signed_paths == [app_bundle]
    assert artifact.notarized is False
    assert artifact.stapled is False
    assert artifact.gatekeeper_assessed is False
    assert any(cmd and cmd[0].endswith("xattr") for cmd in commands)
    assert any(cmd[:2] == ["codesign", "--force"] for cmd in commands)
    assert any(cmd[:2] == ["codesign", "--verify"] for cmd in commands)


def test_sign_mac_notarize_and_staple(tmp_path, monkeypatch):
    freeze_dir = tmp_path / "build"
    app_bundle = freeze_dir / "MyApp.app"
    app_contents = app_bundle / "Contents" / "MacOS"
    app_contents.mkdir(parents=True)
    binary_path = app_contents / "MyApp"
    binary_path.write_text("binary", encoding="utf-8")
    binary_path.chmod(0o755)

    def fake_which(name: str):
        if name in {"codesign", "xattr", "xcrun", "ditto"}:
            return f"/usr/bin/{name}"
        return None

    monkeypatch.setattr("shutil.which", fake_which)

    signer = CompiledAppSigner()
    commands: list[list[str]] = []

    def fake_run(command: list[str]) -> None:
        commands.append(command)

    monkeypatch.setattr(signer, "_run", fake_run)

    artifact = signer.sign(
        project_root=tmp_path,
        app_name="MyApp",
        freeze_dir="build",
        target_platform="mac",
        signing_options={
            "mac": {
                "identity": "Developer ID Application: Example",
                "entitlements": "",
                "notary": {
                    "enabled": True,
                    "staple": True,
                    "assess_gatekeeper": False,
                    "keychain_profile": "QYRO-NOTARY",
                },
            }
        },
    )

    assert artifact.notarized is True
    assert artifact.stapled is True
    assert artifact.gatekeeper_assessed is False
    assert any(cmd[:3] == ["xcrun", "notarytool", "submit"] for cmd in commands)
    assert any(cmd[:3] == ["xcrun", "stapler", "staple"] for cmd in commands)


def test_sign_mac_notary_requires_auth(tmp_path, monkeypatch):
    freeze_dir = tmp_path / "build"
    app_bundle = freeze_dir / "MyApp.app"
    app_contents = app_bundle / "Contents" / "MacOS"
    app_contents.mkdir(parents=True)
    (app_contents / "MyApp").write_text("binary", encoding="utf-8")

    def fake_which(name: str):
        if name in {"codesign", "xcrun", "ditto"}:
            return f"/usr/bin/{name}"
        return None

    monkeypatch.setattr("shutil.which", fake_which)

    signer = CompiledAppSigner()

    with pytest.raises(QyroError):
        signer.preflight(
            project_root=tmp_path,
            app_name="MyApp",
            freeze_dir="build",
            target_platform="mac",
            signing_options={
                "mac": {
                    "identity": "Developer ID Application: Example",
                    "entitlements": "",
                    "notary": {
                        "enabled": True,
                        "staple": True,
                        "assess_gatekeeper": False,
                    },
                }
            },
        )


def test_sign_windows_requires_password(tmp_path, monkeypatch):
    freeze_dir = tmp_path / "build"
    freeze_dir.mkdir(parents=True)
    (freeze_dir / "MyApp.exe").write_text("binary", encoding="utf-8")

    cert_dir = tmp_path / "src" / "sign" / "windows"
    cert_dir.mkdir(parents=True)
    (cert_dir / "certificate.pfx").write_text("cert", encoding="utf-8")

    monkeypatch.setattr("shutil.which", lambda name: "signtool" if name == "signtool" else None)

    signer = CompiledAppSigner()

    with pytest.raises(QyroError):
        signer.sign(
            project_root=tmp_path,
            app_name="MyApp",
            freeze_dir="build",
            target_platform="windows",
            signing_options={
                "windows": {
                    "certificate": "src/sign/windows/certificate.pfx",
                    "password": "",
                }
            },
        )
