"""Android parity tests for Qyro's protected resource bundle format."""
from __future__ import annotations

import hashlib
import hmac
import io
import json
import zipfile

from velto_cli.adapters.mobile.qt.android_release import (
    _RESOURCE_PACK_MAGIC,
    _prepare_android_resource_package,
    _resource_keystream,
    _resource_xor,
    android_resource_protection_enabled,
)


def test_android_resource_package_uses_qyro_desktop_format(tmp_path):
    root = tmp_path / "app"
    settings = root / "settings"
    resources = root / "resources"
    settings.mkdir(parents=True)
    resources.mkdir()
    (settings / "build.json").write_text(json.dumps({"resource_protection": {"enabled": True}}))
    (settings / "base.json").write_text('{"framework":"PySide6"}')
    (settings / "secrets.json").write_text('{"token":"private"}')
    (resources / "logo.txt").write_text("asset")

    package, source, extension, removals = _prepare_android_resource_package(root, tmp_path / "out")
    assert android_resource_protection_enabled(root)
    assert source.read_text().startswith("RUNTIME_SECRET_HEX")
    assert extension.name == "runtime.so"
    assert {"settings/base.json", "settings/secrets.json", "resources/logo.txt"} <= removals

    raw = package.read_bytes()
    assert raw.startswith(_RESOURCE_PACK_MAGIC)
    cursor = len(_RESOURCE_PACK_MAGIC)
    salt, wrapped = raw[cursor:cursor + 16], raw[cursor + 16:cursor + 48]
    nonce, tag, ciphertext = raw[cursor + 48:cursor + 64], raw[cursor + 64:cursor + 96], raw[cursor + 96:]
    secret = bytes.fromhex(source.read_text().split('"')[1])
    mask = _resource_keystream(hashlib.sha256(secret + salt).digest(), 32)
    key = bytes(a ^ b for a, b in zip(wrapped, mask))
    assert hmac.compare_digest(tag, hmac.new(key, nonce + ciphertext, hashlib.sha256).digest())
    with zipfile.ZipFile(io.BytesIO(_resource_xor(ciphertext, key, nonce))) as archive:
        assert archive.read("settings/base.json") == b'{"framework":"PySide6"}'
        assert archive.read("resources/logo.txt") == b"asset"
        assert json.loads(archive.read("settings/secrets.json")) == {"token": "private"}
