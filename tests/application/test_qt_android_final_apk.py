"""Regression: Qt can clean .buildozer and leave a project-root APK only."""
import io
import shutil
import tarfile
from pathlib import Path
from zipfile import ZipFile

import pytest

from velto_cli.adapters.mobile.qt import android_final_apk


def _apk(path: Path) -> None:
    tar_bytes = io.BytesIO()
    with tarfile.open(fileobj=tar_bytes, mode="w") as tar:
        content = b"print('hello')\n"
        info = tarfile.TarInfo("main.py")
        info.size = len(content)
        tar.addfile(info, io.BytesIO(content))
    with ZipFile(path, "w") as apk:
        apk.writestr("assets/private.tar", tar_bytes.getvalue())
        apk.writestr("res/mipmap/icon.png", b"toolchain-default")


def test_complete_root_copy_without_gradle_rebuild(tmp_path, monkeypatch):
    apk = tmp_path / "AndroidAppQt-0.1-arm64-v8a-debug.apk"
    _apk(apk)
    (tmp_path / "settings").mkdir()
    (tmp_path / "settings/base.json").write_text('{"framework":"PySide6"}')
    (tmp_path / "settings/secrets.json").write_text('{"token":"private"}')
    (tmp_path / "resources/linux/icons").mkdir(parents=True)
    icon = tmp_path / "resources/linux/icons/512.png"
    icon.write_bytes(b"project-icon")
    monkeypatch.setattr(android_final_apk, "_android_sdk_tool",
                        lambda name: Path("/fake-sdk") / name)
    monkeypatch.setattr(android_final_apk, "_debug_keystore",
                        lambda: Path("/fake-debug.keystore"))
    commands = []

    def fake_run(args, **kwargs):
        commands.append(args)
        if Path(args[0]).name == "zipalign":
            shutil.copyfile(args[-2], args[-1])
        elif "sign" in args:
            shutil.copyfile(args[-1], args[args.index("--out") + 1])
        elif "verify" in args:
            assert Path(args[-1]).is_file()
        else:
            raise AssertionError(f"Unexpected command: {args}")
        from subprocess import CompletedProcess
        return CompletedProcess(args, 0)

    monkeypatch.setattr(android_final_apk.subprocess, "run", fake_run)
    completed, files, icon_changed = android_final_apk.finalize_copied_debug_apk(
        apk, tmp_path, icon=icon
    )
    assert completed == apk
    assert "settings/base.json" in files
    assert icon_changed
    assert [Path(command[0]).name for command in commands] == [
        "zipalign", "apksigner", "apksigner"
    ]
    with ZipFile(apk) as output:
        assert output.read("res/mipmap/icon.png") == b"project-icon"
        assert output.read("assets/private.tar").startswith(bytes((0x1f, 0x8b)))
        with tarfile.open(fileobj=io.BytesIO(output.read("assets/private.tar")), mode="r:*") as tar:
            names = set(tar.getnames())
            assert "settings/base.json" in names
            assert "settings/secrets.json" not in names


def test_original_apk_unchanged_if_signing_fails(tmp_path, monkeypatch):
    apk = tmp_path / "app.apk"
    _apk(apk)
    original = apk.read_bytes()
    (tmp_path / "settings").mkdir()
    (tmp_path / "settings/base.json").write_text('{"framework":"PySide6"}')
    monkeypatch.setattr(android_final_apk, "_android_sdk_tool",
                        lambda name: Path("/fake-sdk") / name)
    monkeypatch.setattr(android_final_apk, "_debug_keystore",
                        lambda: Path("/fake-debug.keystore"))

    def fail(*args, **kwargs):
        raise RuntimeError("signing rejected")

    monkeypatch.setattr(android_final_apk.subprocess, "run", fail)
    with pytest.raises(RuntimeError, match="signing rejected"):
        android_final_apk.finalize_copied_debug_apk(
            apk, tmp_path, icon=None
        )
    assert apk.read_bytes() == original


def test_repair_previously_modified_plain_tar_without_new_assets(tmp_path, monkeypatch):
    """A previously signed APK with a plain private.tar must be repaired."""
    apk = tmp_path / "AndroidAppQt-debug.apk"
    _apk(apk)
    monkeypatch.setattr(
        android_final_apk, "_android_sdk_tool",
        lambda name: Path("/fake-sdk") / name,
    )
    monkeypatch.setattr(
        android_final_apk, "_debug_keystore",
        lambda: Path("/fake-debug.keystore"),
    )
    commands = []

    def fake_run(args, **kwargs):
        commands.append(args)
        if Path(args[0]).name == "zipalign":
            shutil.copyfile(args[-2], args[-1])
        elif "sign" in args:
            shutil.copyfile(args[-1], args[args.index("--out") + 1])
        elif "verify" in args:
            assert Path(args[-1]).is_file()
        else:
            raise AssertionError(f"Unexpected command: {args}")
        from subprocess import CompletedProcess
        return CompletedProcess(args, 0)

    monkeypatch.setattr(android_final_apk.subprocess, "run", fake_run)
    result, added, icon_changed = android_final_apk.finalize_copied_debug_apk(
        apk, tmp_path, icon=None,
    )
    assert result == apk
    assert added == ()
    assert not icon_changed
    assert len(commands) == 3
    with ZipFile(apk) as z:
        assert z.read("assets/private.tar").startswith(bytes((0x1f, 0x8b)))
        with tarfile.open(fileobj=io.BytesIO(z.read("assets/private.tar")), mode="r:*") as tar:
            assert "main.py" in tar.getnames()

    # After repair, repeating the same operation is a no-op.
    commands.clear()
    assert android_final_apk.finalize_copied_debug_apk(
        apk, tmp_path, icon=None,
    ) == (apk, (), False)
    assert commands == ()
