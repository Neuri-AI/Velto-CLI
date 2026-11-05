"""Automatic, conservative Android debug APK re-packaging regression tests."""
import io
from pathlib import Path
import shutil
import tarfile
from types import SimpleNamespace
from zipfile import ZipFile

import pytest

from velto_cli.adapters.mobile.qt import android_final_apk as final
from velto_cli.adapters.mobile.qt import android_incremental as inc
from velto_cli.domain.mobile import MobileFramework, MobileTarget


def _request(version="0.1.0"):
    return SimpleNamespace(
        app_name="AndroidAppQt",
        version=version,
        entry_point="main.py",
        target=MobileTarget.ANDROID,
        framework=MobileFramework.PYSIDE6,
        debug=True,
        wheel_pyside=None,
        wheel_shiboken=None,
    )


@pytest.fixture
def project(tmp_path, monkeypatch):
    root = tmp_path / "app"
    (root / "settings").mkdir(parents=True)
    (root / "css").mkdir()
    (root / "main.py").write_text("print('one')")
    (root / "settings/base.json").write_text('{"framework":"PySide6"}')
    (root / "css/styles.css").write_text("p { color: red; }")
    (root / "pyproject.toml").write_text("[project]\nname='demo'\n")

    packages = tmp_path / "packages"
    for package in ("velto", "qyro", "psx"):
        directory = packages / package
        directory.mkdir(parents=True)
        (directory / "__init__.py").write_text(f"# {package}")

    monkeypatch.setattr(inc, "_source_package",
                        lambda name: packages / name)
    monkeypatch.setattr(inc, "_cache_root",
                        lambda base: tmp_path / "cache")
    monkeypatch.setattr(final, "_android_sdk_tool",
                        lambda name: Path("/fake-sdk") / name)
    monkeypatch.setattr(final, "_debug_keystore",
                        lambda: Path("/fake.keystore"))

    def run(args, **kwargs):
        if Path(args[0]).name == "zipalign":
            shutil.copyfile(args[-2], args[-1])
        elif "sign" in args:
            shutil.copyfile(args[-1], args[args.index("--out") + 1])
        else:
            assert "verify" in args
        from subprocess import CompletedProcess
        return CompletedProcess(args, 0)

    monkeypatch.setattr(final.subprocess, "run", run)
    files = {
        "main.py": (root / "main.py").read_bytes(),
        "settings/base.json": (root / "settings/base.json").read_bytes(),
        "css/styles.css": (root / "css/styles.css").read_bytes(),
    }
    for package in ("velto", "qyro", "psx"):
        files[package + "/__init__.py"] = (
            packages / package / "__init__.py"
        ).read_bytes()

    private = io.BytesIO()
    with tarfile.open(fileobj=private, mode="w:gz",
                      format=tarfile.USTAR_FORMAT) as tar:
        for folder in ("settings", "css", "velto", "qyro", "psx"):
            member = tarfile.TarInfo(folder)
            member.type = tarfile.DIRTYPE
            member.mode = 0o755
            tar.addfile(member)
        for name, data in files.items():
            member = tarfile.TarInfo(name)
            member.size = len(data)
            member.mode = 0o644
            tar.addfile(member, io.BytesIO(data))
    apk = root / "AndroidAppQt-debug.apk"
    with ZipFile(apk, "w") as container:
        container.writestr("assets/private.tar", private.getvalue())
        container.writestr("resources.arsc",
                           b"private_version\x00" + b"a" * 40 + b"\x00")
    return root, apk


def _tar_files(apk):
    with ZipFile(apk) as package:
        with tarfile.open(fileobj=io.BytesIO(package.read("assets/private.tar")),
                          mode="r:*") as tar:
            return {
                member.name: tar.extractfile(member).read()
                for member in tar if member.isfile()
            }


def test_incremental_updates_python_css_and_extraction_version(project):
    root, apk = project
    inc.record_full_build(root, apk, _request())
    (root / "main.py").write_text("print('two')")
    (root / "css/styles.css").write_text("p { color: blue; }")
    result = inc.try_incremental_build(root, _request())
    assert result is not None and result.is_file()
    contents = _tar_files(result)
    assert contents["main.py"] == b"print('two')"
    assert contents["css/styles.css"] == b"p { color: blue; }"
    with ZipFile(result) as zipped:
        assert (final.android_private_version_marker(
            zipped.read("resources.arsc"))[0] != b"a" * 40)
    # Repeating with no changes returns the valid cached APK.
    assert inc.try_incremental_build(root, _request()) == result


def test_incremental_removes_deleted_source(project):
    root, apk = project
    inc.record_full_build(root, apk, _request())
    (root / "css/styles.css").unlink()
    result = inc.try_incremental_build(root, _request())
    assert result is not None
    assert "css/styles.css" not in _tar_files(result)


def test_native_configuration_changes_force_full_build(project):
    root, apk = project
    inc.record_full_build(root, apk, _request())
    (root / "pyproject.toml").write_text("[project]\nname='changed'\n")
    assert inc.try_incremental_build(root, _request()) is None


def test_deployment_version_change_forces_full_build(project):
    root, apk = project
    inc.record_full_build(root, apk, _request())
    assert inc.try_incremental_build(root, _request("0.2.0")) is None


def test_private_marker_refuses_ambiguous_compiled_resource():
    marker = b"private_version " + b"a" * 40
    assert final.android_private_version_marker(marker) is not None
    replaced = final.replace_android_private_version(marker, "b" * 40)
    assert len(replaced) == len(marker)
    assert b"b" * 40 in replaced
    with pytest.raises(RuntimeError):
        final.replace_android_private_version(
            marker + b" " + b"c" * 40, "d" * 40
        )


def test_qt_builder_uses_cache_before_invoking_android_toolchain(
        project, monkeypatch):
    from unittest.mock import Mock
    from velto_cli.adapters.mobile.freezer import MobileToolchainFreezer
    from velto_cli.domain.mobile import MobileFreezeRequest

    root, apk = project
    (root / "main.py").write_text("from velto import VeltoContext")
    monkeypatch.setattr(
        "velto_cli.adapters.mobile.qt.android.try_incremental_build",
        lambda current, request: apk
    )
    toolchain = MobileToolchainFreezer(Mock())
    request = MobileFreezeRequest(
        project_root=root, target=MobileTarget.ANDROID,
        framework=MobileFramework.PYSIDE6, app_name="AndroidAppQt",
        version="0.1.0", debug=True,
    )
    result = toolchain.freeze(request)
    assert result.path == apk
    toolchain._runner.run.assert_not_called()


def test_clean_forces_full_deployment_instead_of_incremental(
        project, monkeypatch):
    from contextlib import contextmanager
    from unittest.mock import Mock
    from velto_cli.adapters.mobile.freezer import MobileToolchainFreezer
    from velto_cli.domain.mobile import MobileBuildArtifact, MobileFreezeRequest

    root, apk = project
    (root / "main.py").write_text("from velto import VeltoContext")
    called = []
    def fast(*args):
        called.append("fast")
        return apk

    @contextmanager
    def stage(*args, **kwargs):
        yield True

    def full(self, request):
        called.append("full")
        return MobileBuildArtifact(
            apk, MobileTarget.ANDROID, MobileFramework.PYSIDE6, "apk"
        )

    monkeypatch.setattr(
        "velto_cli.adapters.mobile.qt.android.try_incremental_build", fast
    )
    monkeypatch.setattr(
        "velto_cli.adapters.mobile.qt.android.staged_velto_sources", stage
    )
    monkeypatch.setattr(
        "velto_cli.adapters.mobile.qt.android.QtAndroidBuilder._pyside_android_impl",
        full,
    )
    monkeypatch.setattr(
        "velto_cli.adapters.mobile.qt.android.record_full_build",
        lambda *args: None,
    )
    request = MobileFreezeRequest(
        project_root=root, target=MobileTarget.ANDROID,
        framework=MobileFramework.PYSIDE6, app_name="AndroidAppQt",
        version="0.1.0", debug=True, clean=True,
    )
    result = MobileToolchainFreezer(Mock()).freeze(request)
    assert result.path == apk
    assert called == ["full"]


def test_utf16_private_version_string_is_replaced_without_repacking():
    original = ("private_version" + chr(0)).encode("utf-16le") + (
        "a" * 40
    ).encode("utf-16le")
    result = final.replace_android_private_version(original, "f" * 40)
    assert len(result) == len(original)
    found = final.android_private_version_marker(result)
    assert found is not None
    assert found[1] == b"utf16"
    assert found[0] == ("f" * 40).encode("utf-16le")


def test_missing_android_version_resource_disables_cache(project):
    root, apk = project
    with ZipFile(apk, "r") as zipped:
        members = [(member, zipped.read(member.filename))
                   for member in zipped.infolist()]
    with ZipFile(apk, "w") as zipped:
        for member, payload in members:
            zipped.writestr(
                member, b"not-a-private-version" if member.filename == "resources.arsc"
                else payload
            )
    inc.record_full_build(root, apk, _request())
    assert inc.try_incremental_build(root, _request()) is None
