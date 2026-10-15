from __future__ import annotations

import hashlib
import hmac
import io
import json
import logging
from pathlib import Path
from types import SimpleNamespace
import zipfile

import pytest

from qyro_cli.adapters.freeze.framework_hooks import FrameworkHookResolver
from qyro_cli.adapters.freeze.pyinstaller_adapter import PyInstallerFreezer
from qyro_cli.adapters.freeze.secrets_crypto import (
    SecretsCryptoError,
    decrypt_secrets_payload,
    derive_aes256_key,
    encrypt_secrets_payload,
)
from qyro_cli.domain.build import FreezeManifest
from qyro_cli.domain.errors import QyroError


def test_encrypt_decrypt_roundtrip() -> None:
    runtime_secret = b"a" * 32
    plaintext = b'{"client_secret":"abc"}'

    encrypted = encrypt_secrets_payload(plaintext, runtime_secret)
    decrypted = decrypt_secrets_payload(encrypted, runtime_secret)

    assert decrypted == plaintext


def test_modified_ciphertext_is_rejected() -> None:
    runtime_secret = b"b" * 32
    encrypted = bytearray(encrypt_secrets_payload(b'{"x":1}', runtime_secret))
    encrypted[-1] ^= 0x01

    with pytest.raises(SecretsCryptoError):
        decrypt_secrets_payload(bytes(encrypted), runtime_secret)


def test_wrong_runtime_secret_is_rejected() -> None:
    encrypted = encrypt_secrets_payload(b'{"x":1}', b"c" * 32)

    with pytest.raises(SecretsCryptoError):
        decrypt_secrets_payload(encrypted, b"d" * 32)


def test_same_plaintext_encrypts_to_different_payloads() -> None:
    runtime_secret = b"e" * 32
    plaintext = b'{"x":1}'

    a = encrypt_secrets_payload(plaintext, runtime_secret)
    b = encrypt_secrets_payload(plaintext, runtime_secret)

    assert a != b


def test_derived_key_is_32_bytes() -> None:
    key = derive_aes256_key(b"f" * 32, b"s" * 16)
    assert len(key) == 32


def test_payload_has_versioned_magic_header() -> None:
    encrypted = encrypt_secrets_payload(b"{}", b"g" * 32)

    assert encrypted.startswith(b"QYRSEC")
    assert encrypted[6] == 1


def test_encryption_does_not_emit_secret_logs(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)
    runtime_secret = b"h" * 32
    plaintext = b'{"password":"super-secret-value"}'

    encrypted = encrypt_secrets_payload(plaintext, runtime_secret)
    _ = decrypt_secrets_payload(encrypted, runtime_secret)

    captured = "\n".join(record.getMessage() for record in caplog.records)
    assert "super-secret-value" not in captured
    assert runtime_secret.hex() not in captured


def test_encrypt_project_secrets_rejects_empty_file(tmp_path: Path) -> None:
    freezer = PyInstallerFreezer(FrameworkHookResolver())
    source = tmp_path / "secrets.json"
    source.write_text("", encoding="utf-8")

    with pytest.raises(QyroError):
        freezer._encrypt_project_secrets(
            secrets_source_path=source,
            runtime_secret=b"i" * 32,
        )


def test_encrypt_project_secrets_rejects_invalid_json(tmp_path: Path) -> None:
    freezer = PyInstallerFreezer(FrameworkHookResolver())
    source = tmp_path / "secrets.json"
    source.write_text("{invalid", encoding="utf-8")

    with pytest.raises(QyroError):
        freezer._encrypt_project_secrets(
            secrets_source_path=source,
            runtime_secret=b"j" * 32,
        )


@pytest.mark.parametrize("returncode", [0, 1])
def test_runtime_secret_sources_are_removed_when_cython_cannot_produce_binary(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    returncode: int,
) -> None:
    freezer = PyInstallerFreezer(FrameworkHookResolver())

    def fake_run(*_args, **_kwargs):
        (tmp_path / "runtime.c").write_text("contains generated code", encoding="utf-8")
        return SimpleNamespace(returncode=returncode, stdout="compiler failed")

    monkeypatch.setattr(
        "qyro_cli.adapters.freeze.pyinstaller_adapter.subprocess.run",
        fake_run,
    )

    with pytest.raises(QyroError):
        freezer._write_runtime_secret_module(tmp_path / "runtime.pyd")

    assert not (tmp_path / "runtime.py").exists()
    assert not (tmp_path / "runtime.c").exists()


@pytest.mark.parametrize(
    "target_platform",
    [
        "windows",
        "linux",
        "mac",
    ],
)
def test_build_command_does_not_add_external_encrypted_secrets_entry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    target_platform: str,
) -> None:
    (tmp_path / "main.py").write_text("print('ok')\n", encoding="utf-8")

    freezer = PyInstallerFreezer(FrameworkHookResolver())

    package = tmp_path / ".qyro" / "resources.pak"
    runtime_module = tmp_path / ".qyro" / "runtime.pyd"
    package.parent.mkdir(parents=True, exist_ok=True)
    package.write_bytes(b"pak")
    runtime_module.write_bytes(b"mod")

    monkeypatch.setattr(
        freezer,
        "_prepare_protected_resources_package",
        lambda **_: (package, runtime_module),
    )

    manifest = FreezeManifest(
        app_name="MyApp",
        binding="PySide2",
        entry_point="main.py",
        target_platform=target_platform,
        protect_resources=True,
    )

    cmd = freezer.build_command(tmp_path, manifest)
    add_data_values = [cmd[i + 1] for i, token in enumerate(cmd) if token == "--add-data"]

    assert not any(".qyro/secrets.json" in value for value in add_data_values)


def test_protected_package_embeds_encrypted_secrets_payload(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "main.py").write_text("print('ok')\n", encoding="utf-8")
    settings_dir = tmp_path / "settings"
    resources_dir = tmp_path / "resources"
    settings_dir.mkdir(parents=True)
    resources_dir.mkdir(parents=True)
    (settings_dir / "base.json").write_text('{"app_name": "MyApp"}', encoding="utf-8")
    (settings_dir / "secrets.json").write_text('{"api": {"token": "local-secret"}}', encoding="utf-8")
    (settings_dir / "sign.json").write_text(
        '{"sign": {"windows": {"password": "signing-secret"}}}',
        encoding="utf-8",
    )
    (resources_dir / "logo.png").write_text("png", encoding="utf-8")

    freezer = PyInstallerFreezer(FrameworkHookResolver())
    runtime_secret = b"r" * 32
    runtime_module = tmp_path / ".qyro" / "runtime.pyd"
    runtime_module.parent.mkdir(parents=True, exist_ok=True)
    runtime_module.write_bytes(b"module")

    monkeypatch.setattr(
        freezer,
        "_write_runtime_secret_module",
        lambda runtime_secret_module_path: (runtime_secret, runtime_module),
    )

    manifest = FreezeManifest(
        app_name="MyApp",
        binding="PySide2",
        entry_point="main.py",
        target_platform="windows",
        protect_resources=True,
    )

    package_path, _ = freezer._prepare_protected_resources_package(project_root=tmp_path, manifest=manifest)

    assert package_path.exists()
    assert not (tmp_path / ".qyro" / "secrets.json").exists()

    raw = package_path.read_bytes()
    assert raw.startswith(b"QYRPKG")
    assert b"local-secret" not in raw

    cursor = len(b"QYRPKG")
    salt = raw[cursor:cursor + 16]
    cursor += 16
    wrapped_key = raw[cursor:cursor + 32]
    cursor += 32
    nonce = raw[cursor:cursor + 16]
    cursor += 16
    tag = raw[cursor:cursor + 32]
    cursor += 32
    ciphertext = raw[cursor:]

    mask = freezer._keystream(seed=freezer._master_seed(salt, runtime_secret), size=32)
    resource_key = bytes(value ^ mask[i] for i, value in enumerate(wrapped_key))

    expected_tag = hmac.new(resource_key, nonce + ciphertext, hashlib.sha256).digest()
    assert hmac.compare_digest(tag, expected_tag)

    zip_bytes = freezer._xor_keystream(ciphertext, key=resource_key, nonce=nonce)
    with zipfile.ZipFile(io.BytesIO(zip_bytes), "r") as archive:
        names = set(archive.namelist())
        assert "settings/base.json" in names
        assert "settings/sign.json" not in names
        assert "resources/logo.png" in names
        assert ".qyro/secrets.enc" in names

        encrypted_payload = archive.read(".qyro/secrets.enc")
        assert b"local-secret" not in encrypted_payload

    decrypted = decrypt_secrets_payload(encrypted_payload, runtime_secret)
    payload_json = json.loads(decrypted.decode("utf-8"))
    assert payload_json == {"api": {"token": "local-secret"}}
    assert b"local-secret" not in zip_bytes


def test_build_command_protects_secrets_even_when_resource_protection_is_disabled(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (tmp_path / "main.py").write_text("print('ok')\n", encoding="utf-8")
    settings_dir = tmp_path / "settings"
    resources_dir = tmp_path / "resources"
    settings_dir.mkdir(parents=True)
    resources_dir.mkdir(parents=True)
    (settings_dir / "base.json").write_text('{"app_name": "MyApp"}', encoding="utf-8")
    (settings_dir / "secrets.json").write_text('{"api": {"token": "local-secret"}}', encoding="utf-8")
    (resources_dir / "logo.png").write_text("png", encoding="utf-8")

    freezer = PyInstallerFreezer(FrameworkHookResolver())
    package = tmp_path / ".qyro" / "resources.pak"
    runtime_module = tmp_path / ".qyro" / "runtime.pyd"
    package.parent.mkdir(parents=True, exist_ok=True)
    package.write_bytes(b"pak")
    runtime_module.write_bytes(b"mod")

    monkeypatch.setattr(
        freezer,
        "_prepare_protected_secrets_package",
        lambda **_: (package, runtime_module),
    )

    manifest = FreezeManifest(
        app_name="MyApp",
        binding="PySide2",
        entry_point="main.py",
        target_platform="windows",
        protect_resources=False,
    )

    cmd = freezer.build_command(tmp_path, manifest)
    add_data_values = [cmd[i + 1] for i, token in enumerate(cmd) if token == "--add-data"]

    assert any(".qyro/resources.pak" in value for value in add_data_values)
    assert any(".qyro/runtime" in value and ".pyd" in value for value in add_data_values)
    assert not any(".qyro/secrets.json" in value for value in add_data_values)


def test_build_command_requires_secrets_json_even_when_resource_protection_is_disabled(tmp_path: Path) -> None:
    (tmp_path / "main.py").write_text("print('ok')\n", encoding="utf-8")
    settings_dir = tmp_path / "settings"
    settings_dir.mkdir(parents=True)
    (settings_dir / "base.json").write_text('{"app_name": "MyApp"}', encoding="utf-8")

    freezer = PyInstallerFreezer(FrameworkHookResolver())
    manifest = FreezeManifest(
        app_name="MyApp",
        binding="PySide2",
        entry_point="main.py",
        target_platform="windows",
        protect_resources=False,
    )

    with pytest.raises(QyroError, match="settings/secrets.json is required"):
        freezer.build_command(tmp_path, manifest)


def test_build_rejects_signing_configuration_in_secrets_json(tmp_path: Path) -> None:
    settings_dir = tmp_path / "settings"
    settings_dir.mkdir(parents=True)
    secrets_path = settings_dir / "secrets.json"
    secrets_path.write_text(
        '{"api_token":"runtime","sign":{"windows":{"password":"wrong-file"}}}',
        encoding="utf-8",
    )

    freezer = PyInstallerFreezer(FrameworkHookResolver())

    with pytest.raises(QyroError, match="contains signing configuration"):
        freezer._encrypt_project_secrets(
            secrets_source_path=secrets_path,
            runtime_secret=b"r" * 32,
        )
