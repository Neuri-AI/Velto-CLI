"""Tests for public Qt Android wheel download with no commercial credentials."""
from io import BytesIO
from pathlib import Path
from unittest.mock import Mock
from zipfile import ZipFile

import pytest

from velto_cli.adapters.mobile.qt_android_archive import QtAndroidWheelArchive


_VERSION = "6.12.0"
_ARCH = "aarch64"
_PYSIDE = "pyside6-6.12.0-6.12.0-cp310-abi3-android_aarch64.whl"
_SHIBOKEN = "shiboken6-6.12.0-6.12.0-cp310-abi3-android_aarch64.whl"
_BASE = "https://download.qt.io/official_releases/QtForPython/"


def wheel_bytes() -> bytes:
    out = BytesIO()
    with ZipFile(out, "w") as wheel:
        wheel.writestr("data.txt", "fixture")
    return out.getvalue()


def test_public_archive_download_and_reuse_cache(tmp_path, monkeypatch):
    zip_data = wheel_bytes()
    calls = []

    def fake_urlopen(url, timeout=30):
        calls.append(url)
        if url == _BASE + "pyside6/":
            body = f'<a href="{_PYSIDE}">wheel</a><a href="../">parent</a>'
        elif url == _BASE + "shiboken6/":
            body = f'<a href="{_SHIBOKEN}">wheel</a>'
        elif url in (_BASE + "pyside6/" + _PYSIDE,
                     _BASE + "shiboken6/" + _SHIBOKEN):
            return BytesIO(zip_data)
        else:
            raise AssertionError(f"Unexpected network request: {url}")
        return BytesIO(body.encode())

    monkeypatch.setattr(
        "velto_cli.adapters.mobile.qt_android_archive.urlopen", fake_urlopen
    )
    archive = QtAndroidWheelArchive()
    pyside, shiboken = archive.resolve(_VERSION, _ARCH, tmp_path)
    assert pyside.name == _PYSIDE
    assert shiboken.name == _SHIBOKEN
    assert pyside.read_bytes() == zip_data
    assert shiboken.read_bytes() == zip_data
    assert len(calls) == 4

    assert archive.resolve(_VERSION, _ARCH, tmp_path) == (pyside, shiboken)
    assert len(calls) == 4  # no re-download


def test_public_archive_rejects_wrong_architecture(tmp_path, monkeypatch):
    def fake_urlopen(url, timeout=30):
        return BytesIO(
            b'<a href="pyside6-6.12.0-6.12.0-cp310-abi3-android_x86_64.whl">wrong</a>'
        )
    monkeypatch.setattr(
        "velto_cli.adapters.mobile.qt_android_archive.urlopen", fake_urlopen
    )
    with pytest.raises(RuntimeError, match="No official open-source"):
        QtAndroidWheelArchive().resolve(_VERSION, _ARCH, tmp_path)


def test_public_archive_rejects_corrupt_download(tmp_path, monkeypatch):
    def fake_urlopen(url, timeout=30):
        if url.endswith("/"):
            return BytesIO(f'<a href="{_PYSIDE}">wheel</a>'.encode())
        return BytesIO(b"not a valid wheel archive")
    monkeypatch.setattr(
        "velto_cli.adapters.mobile.qt_android_archive.urlopen", fake_urlopen
    )
    with pytest.raises(RuntimeError, match="invalid wheel archive"):
        QtAndroidWheelArchive().resolve(_VERSION, _ARCH, tmp_path)
    assert not list(tmp_path.glob("*.whl"))
    assert not list(tmp_path.glob("*.part"))


def test_public_archive_rejects_mismatched_qt_versions():
    assert not QtAndroidWheelArchive._valid(
        _PYSIDE, "pyside6", "6.11.3", _ARCH
    )
    assert not QtAndroidWheelArchive._valid(
        _PYSIDE, "pyside6", _VERSION, "x86_64"
    )
