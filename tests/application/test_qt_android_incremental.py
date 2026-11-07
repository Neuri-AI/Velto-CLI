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
    private = io.BytesIO()
    with tarfile.open(fileobj=private, mode="w:gz",
                      format=tarfile.USTAR_FORMAT) as tar:
        for folder in ("settings", "css"):
            member = tarfile.TarInfo(folder)
            member.type = tarfile.DIRTYPE
            member.mode = 0o755
            tar.addfile(member)
        for name, data in files.items():
            member = tarfile.TarInfo(name)
            member.size = len(data)
            member.mode = 0o644
            tar.addfile(member, io.BytesIO(data))
    python_bundle = io.BytesIO()
    with tarfile.open(fileobj=python_bundle, mode="w:gz") as tar:
        for name in ("velto", "qyro", "psx"):
            info = tarfile.TarInfo(f"_python_bundle/site-packages/{name}/__init__.py")
            info.size = 1
            tar.addfile(info, io.BytesIO(b" "))
        for name in ("velto", "qyro_engine", "velto_core", "velto_cinnamon_core", "resvg_py"):
            info = tarfile.TarInfo(f"_python_bundle/site-packages/{name}-0.1.dist-info/METADATA")
            info.size = 1
            tar.addfile(info, io.BytesIO(b" "))
        info = tarfile.TarInfo("_python_bundle/site-packages/_sfle_native/_sfle_native.so")
        info.size = 1
        tar.addfile(info, io.BytesIO(b" "))
    apk = root / "AndroidAppQt-debug.apk"
    with ZipFile(apk, "w") as container:
        container.writestr("assets/private.tar", private.getvalue())
        container.writestr("lib/arm64-v8a/libpybundle.so", python_bundle.getvalue())
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
    (root / "components").mkdir()
    (root / "components/card.py").write_text("TITLE = 'new'\n")
    result = inc.try_incremental_build(root, _request())
    assert result is not None and result.is_file()
    contents = _tar_files(result)
    assert contents["main.py"] == b"print('two')"
    assert contents["css/styles.css"] == b"p { color: blue; }"
    assert contents["components/card.py"] == b"TITLE = 'new'\n"
    assert not any(name.startswith(("velto/", "qyro/", "psx/")) for name in contents)
    with ZipFile(result) as zipped:
        with ZipFile(apk) as original:
            assert zipped.read("lib/arm64-v8a/libpybundle.so") == original.read("lib/arm64-v8a/libpybundle.so")
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


def test_incremental_repairs_file_when_manifest_claims_stale_apk_is_current(project):
    import hashlib
    import json
    root, apk = project
    assert inc.record_full_build(root, apk, _request())
    css = root / "css/styles.css"
    css.write_text("p { color: green; }")
    manifest = inc._cache_root(root) / "manifest.json"
    metadata = json.loads(manifest.read_text())
    # Reproduce a cache whose manifest was updated without updating its APK.
    metadata["files"]["css/styles.css"] = hashlib.sha256(css.read_bytes()).hexdigest()
    manifest.write_text(json.dumps(metadata))
    result = inc.try_incremental_build(root, _request())
    assert result is not None
    assert _tar_files(result)["css/styles.css"] == css.read_bytes()


def test_incremental_includes_new_files_from_any_project_directory(project):
    root, apk = project
    assert inc.record_full_build(root, apk, _request())
    additions = {
        "resources/icons/new.png": b"image",
        "settings/profile.json": b'{"theme":"new"}',
        "components/card.py": b"TITLE = 'new'\n",
        "translations/es/catalog.custom": b"arbitrary format",
        "nested/build/data.bin": b"nested build is application data",
        "site/.well-known/assetlinks.json": b'{}',
        "root.data": b"root-level data",
    }
    for name, data in additions.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    for name in ("README.md", ".hidden/private.txt", "build/ignored.txt"):
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("not shipped")
    result = inc.try_incremental_build(root, _request())
    assert result is not None
    contents = _tar_files(result)
    assert all(contents[name] == data for name, data in additions.items())
    assert not {"README.md", ".hidden/private.txt", "build/ignored.txt"} & contents.keys()
    with ZipFile(result) as package:
        with tarfile.open(fileobj=io.BytesIO(package.read('assets/private.tar')), mode='r:*') as tar:
            directories = {item.name for item in tar if item.isdir()}
    assert {"translations", "translations/es", "nested", "nested/build"} <= directories

    (root / "translations/es/catalog.custom").unlink()
    (root / "settings/profile.json").write_bytes(b'{"theme":"updated"}')
    second = inc.try_incremental_build(root, _request())
    assert second is not None
    second_contents = _tar_files(second)
    assert "translations/es/catalog.custom" not in second_contents
    assert second_contents["settings/profile.json"] == b'{"theme":"updated"}'


def test_qml_changes_reuse_cache_unless_imports_change(project):
    root, apk = project
    assert inc.record_full_build(root, apk, _request())
    (root / "ui").mkdir()
    qml = root / "ui/view.qml"
    qml.write_text('Item { property string label: "one" }\n')
    assert inc.try_incremental_build(root, _request()) is not None
    qml.write_text('Item { property string label: "two" }\n')
    assert inc.try_incremental_build(root, _request()) is not None
    qml.write_text('import QtQuick.Controls\nItem {}\n')
    assert inc.try_incremental_build(root, _request()) is None


def test_incremental_removes_build_files_and_directories_from_cached_apk(project):
    root, apk = project
    with ZipFile(apk) as package:
        members = [(member, package.read(member.filename)) for member in package.infolist()]
    original = next(data for member, data in members if member.filename == 'assets/private.tar')
    rewritten = io.BytesIO()
    with tarfile.open(fileobj=io.BytesIO(original), mode='r:*') as source:
        with tarfile.open(fileobj=rewritten, mode='w:gz') as target:
            for item in source:
                target.addfile(item, source.extractfile(item) if item.isfile() else None)
            for name in ('deployment', 'deployment/recipes'):
                item = tarfile.TarInfo(name)
                item.type = tarfile.DIRTYPE
                target.addfile(item)
            for name in ('deployment/recipes/qt.py', 'README.md', 'LICENSE'):
                item = tarfile.TarInfo(name)
                item.size = 1
                target.addfile(item, io.BytesIO(b'x'))
    with ZipFile(apk, 'w') as package:
        for member, data in members:
            package.writestr(member, rewritten.getvalue() if member.filename == 'assets/private.tar' else data)
    assert inc.record_full_build(root, apk, _request())
    result = inc.try_incremental_build(root, _request())
    assert result is not None
    with ZipFile(result) as package:
        with tarfile.open(fileobj=io.BytesIO(package.read('assets/private.tar')), mode='r:*') as tar:
            names = {item.name for item in tar}
    assert not {'deployment', 'deployment/recipes', 'deployment/recipes/qt.py', 'README.md', 'LICENSE'} & names


def test_incremental_updates_project_file_already_present_but_not_tracked(project):
    import json
    import hashlib
    root, apk = project
    assert inc.record_full_build(root, apk, _request())
    cache = inc._cache_root(root)
    cached = cache / 'base.apk'
    with ZipFile(cached) as package:
        members = [(member, package.read(member.filename)) for member in package.infolist()]
    old = next(data for member, data in members if member.filename == 'assets/private.tar')
    rewritten = io.BytesIO()
    with tarfile.open(fileobj=io.BytesIO(old), mode='r:*') as source:
        with tarfile.open(fileobj=rewritten, mode='w:gz') as target:
            for item in source:
                target.addfile(item, source.extractfile(item) if item.isfile() else None)
            directory = tarfile.TarInfo('translations')
            directory.type = tarfile.DIRTYPE
            target.addfile(directory)
            entry = tarfile.TarInfo('translations/existing.dat')
            entry.size = 3
            target.addfile(entry, io.BytesIO(b'old'))
    with ZipFile(cached, 'w') as package:
        for member, data in members:
            package.writestr(member, rewritten.getvalue() if member.filename == 'assets/private.tar' else data)
    manifest = cache / 'manifest.json'
    meta = json.loads(manifest.read_text())
    meta['apk_sha256'] = hashlib.sha256(cached.read_bytes()).hexdigest()
    manifest.write_text(json.dumps(meta))
    (root / 'translations').mkdir()
    (root / 'translations/existing.dat').write_bytes(b'new')
    result = inc.try_incremental_build(root, _request())
    assert result is not None
    assert _tar_files(result)['translations/existing.dat'] == b'new'


def test_native_configuration_changes_force_full_build(project):
    root, apk = project
    inc.record_full_build(root, apk, _request())
    (root / "pyproject.toml").write_text("[project]\nname='changed'\n")
    assert inc.try_incremental_build(root, _request()) is None


def test_new_qt_import_forces_full_build(project):
    root, apk = project
    inc.record_full_build(root, apk, _request())
    (root / "main.py").write_text("from PySide6 import QtSvg\n")
    assert inc.try_incremental_build(root, _request()) is None


def test_configured_android_wheel_change_forces_full_build(project):
    root, apk = project
    wheel = root / "pyside.whl"
    wheel.write_bytes(b"old")
    (root / "pysidedeploy.spec").write_text("[android]\nwheel_pyside = pyside.whl\n")
    inc.record_full_build(root, apk, _request())
    wheel.write_bytes(b"new")
    assert inc.try_incremental_build(root, _request()) is None


def test_old_cache_format_is_not_reused(project):
    import json
    root, apk = project
    inc.record_full_build(root, apk, _request())
    manifest = inc._cache_root(root) / "manifest.json"
    value = json.loads(manifest.read_text())
    value["format"] = 1
    manifest.write_text(json.dumps(value))
    assert inc.try_incremental_build(root, _request()) is None
    assert "versión anterior" in inc.incremental_cache_miss_reason(root)


def test_full_build_does_not_cache_stale_application_source(project):
    root, apk = project
    (root / "main.py").write_text("print('changed while building')")
    assert not inc.record_full_build(root, apk, _request())
    assert not (inc._cache_root(root) / "manifest.json").exists()


def test_full_build_does_not_cache_if_native_inputs_changed(project):
    root, apk = project
    _, hashes, native = inc._scan(root)
    (root / "pyproject.toml").write_text("[project]\nname='different'\n")
    assert not inc.record_full_build(root, apk, _request(), (hashes, native))
    assert not (inc._cache_root(root) / "manifest.json").exists()


def test_debug_protection_settings_do_not_invalidate_unprotected_runtime(project):
    root, apk = project
    settings = root / "settings/android.json"
    settings.write_text('{"protect_source": false}')
    inc.record_full_build(root, apk, _request())
    settings.write_text('{"protect_source": false, "obfuscate": true}')
    assert inc.try_incremental_build(root, _request()) is not None


def test_cache_rejects_apk_with_copied_framework_sources(project):
    root, apk = project
    with ZipFile(apk) as archive:
        members = [(item, archive.read(item.filename)) for item in archive.infolist()]
    old = next(payload for item, payload in members if item.filename == "assets/private.tar")
    data = io.BytesIO()
    with tarfile.open(fileobj=io.BytesIO(old), mode="r:*") as source:
        with tarfile.open(fileobj=data, mode="w:gz") as target:
            for item in source:
                target.addfile(item, source.extractfile(item) if item.isfile() else None)
            copied = tarfile.TarInfo("velto/__init__.py")
            copied.size = 1
            target.addfile(copied, io.BytesIO(b" "))
    with ZipFile(apk, "w") as archive:
        for item, payload in members:
            archive.writestr(item, data.getvalue() if item.filename == "assets/private.tar" else payload)
    inc.record_full_build(root, apk, _request())
    assert not (inc._cache_root(root) / "base.apk").exists()


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


def test_qt_builder_reuses_valid_incremental_apk(project, monkeypatch):
    from unittest.mock import Mock
    from velto_cli.adapters.mobile.freezer import MobileToolchainFreezer
    from velto_cli.domain.mobile import MobileFreezeRequest
    root, apk = project
    (root / "main.py").write_text("from velto import VeltoContext")
    monkeypatch.setattr("velto_cli.adapters.mobile.qt.android.try_incremental_build", lambda *args: apk)
    request = MobileFreezeRequest(project_root=root, target=MobileTarget.ANDROID,
        framework=MobileFramework.PYSIDE6, app_name="AndroidAppQt", version="0.1.0", debug=True)
    builder = MobileToolchainFreezer(Mock())
    assert builder.freeze(request).path == apk
    builder._runner.run.assert_not_called()


def test_full_build_seeds_cache_and_next_source_change_uses_it(project, monkeypatch):
    from contextlib import nullcontext
    from unittest.mock import Mock
    from velto_cli.adapters.mobile.freezer import MobileToolchainFreezer
    from velto_cli.domain.mobile import MobileBuildArtifact, MobileFreezeRequest

    root, apk = project
    (root / "pyproject.toml").write_text('[project]\nname="demo"\ndependencies=["velto"]\n')
    calls = []
    monkeypatch.setattr("velto_cli.adapters.mobile.qt.android.staged_android_project", lambda path: nullcontext(path))
    def full(self, request):
        calls.append("full")
        return MobileBuildArtifact(apk, MobileTarget.ANDROID, MobileFramework.PYSIDE6, "apk")
    monkeypatch.setattr("velto_cli.adapters.mobile.qt.android.QtAndroidBuilder._pyside_android_impl", full)
    request = MobileFreezeRequest(project_root=root, target=MobileTarget.ANDROID,
        framework=MobileFramework.PYSIDE6, app_name="AndroidAppQt", version="0.1.0", debug=True)
    builder = MobileToolchainFreezer(Mock())
    builder.freeze(request)
    assert (inc._cache_root(root) / "manifest.json").is_file()
    (root / "main.py").write_text("from psx.layout.engine.backend import load_layout_engine\nprint('two')")
    result = builder.freeze(request)
    assert calls == ["full"]
    assert b"load_layout_engine" in _tar_files(result.path)["main.py"]


@pytest.mark.parametrize("options", [{"clean": True}, {"protect_source": True}])
def test_clean_or_protected_build_never_reuses_incremental_apk(project, monkeypatch, options):
    from contextlib import nullcontext
    from unittest.mock import Mock
    from velto_cli.adapters.mobile.freezer import MobileToolchainFreezer
    from velto_cli.domain.mobile import MobileBuildArtifact, MobileFreezeRequest
    root, apk = project
    (root / "main.py").write_text("from velto import VeltoContext")
    fast = Mock(side_effect=AssertionError("incremental cache must not be used"))
    monkeypatch.setattr("velto_cli.adapters.mobile.qt.android.try_incremental_build", fast)
    monkeypatch.setattr("velto_cli.adapters.mobile.qt.android.staged_android_project", lambda root: nullcontext(root))
    monkeypatch.setattr("velto_cli.adapters.mobile.qt.android.protect_android_apk", lambda *args, **kwargs: None)
    monkeypatch.setattr("velto_cli.adapters.mobile.qt.android.record_full_build", lambda *args: None)
    monkeypatch.setattr("velto_cli.adapters.mobile.qt.android.QtAndroidBuilder._pyside_android_impl",
                        lambda self, request: MobileBuildArtifact(apk, MobileTarget.ANDROID, MobileFramework.PYSIDE6, "apk"))
    request = MobileFreezeRequest(project_root=root, target=MobileTarget.ANDROID,
        framework=MobileFramework.PYSIDE6, app_name="AndroidAppQt", version="0.1.0", debug=True, **options)
    MobileToolchainFreezer(Mock()).freeze(request)
    fast.assert_not_called()


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


def test_release_apk_never_seeds_incremental_cache(project):
    root, debug_apk = project
    release = debug_apk.with_name("AndroidAppQt-release.apk")
    shutil.copy2(debug_apk, release)
    inc.record_full_build(root, release, _request())
    assert inc.try_incremental_build(root, _request()) is None


def test_extracted_root_apk_is_not_scanned_as_application_source(
        project):
    root, apk = project
    extracted = root / apk.stem / "assets/private/velto"
    extracted.mkdir(parents=True)
    (extracted / "__init__.py").write_text("unreadable archive contents")
    inc.record_full_build(root, apk, _request())
    # A discarded/extracted inspection tree must not invalidate the cache
    # or introduce its private Python modules into the sources manifest.
    sources, hashes, native = inc._scan(root)
    assert not any(name.startswith(apk.stem + "/") for name in hashes)
    assert inc.try_incremental_build(root, _request()) is not None


def test_qt_custom_entry_uses_temporary_launcher(project, monkeypatch):
    from contextlib import nullcontext
    from unittest.mock import Mock
    from velto_cli.adapters.mobile.freezer import MobileToolchainFreezer
    from velto_cli.domain.mobile import MobileBuildArtifact, MobileFreezeRequest
    root, apk = project
    (root / 'main.py').unlink()
    (root / 'launch.py').write_text('from velto import VeltoContext\n')
    monkeypatch.setattr('velto_cli.adapters.mobile.qt.android.staged_android_project', lambda path: nullcontext(path))
    monkeypatch.setattr('velto_cli.adapters.mobile.qt.android.try_incremental_build', lambda *args: None)
    monkeypatch.setattr('velto_cli.adapters.mobile.qt.android.record_full_build', lambda *args: None)
    def full(self, request):
        assert request.entry_point == 'launch.py'
        assert 'launch.py' in (request.project_root / 'main.py').read_text()
        return MobileBuildArtifact(apk, MobileTarget.ANDROID, MobileFramework.PYSIDE6, 'apk')
    monkeypatch.setattr('velto_cli.adapters.mobile.qt.android.QtAndroidBuilder._pyside_android_impl', full)
    request = MobileFreezeRequest(project_root=root, target=MobileTarget.ANDROID,
        framework=MobileFramework.PYSIDE6, app_name='AndroidAppQt', version='0.1.0',
        entry_point='launch.py', debug=True)
    MobileToolchainFreezer(Mock()).freeze(request)
    assert not (root / 'main.py').exists()
    assert (root / 'launch.py').is_file()


def test_custom_entry_with_existing_main_does_not_reuse_unsafe_cache(project, monkeypatch):
    from contextlib import nullcontext
    from unittest.mock import Mock
    from velto_cli.adapters.mobile.freezer import MobileToolchainFreezer
    from velto_cli.domain.mobile import MobileBuildArtifact, MobileFreezeRequest
    root, apk = project
    original = (root / 'main.py').read_text()
    (root / 'launch.py').write_text('from velto import VeltoContext\n')
    fast = Mock(side_effect=AssertionError('cache must not be used'))
    monkeypatch.setattr('velto_cli.adapters.mobile.qt.android.try_incremental_build', fast)
    monkeypatch.setattr('velto_cli.adapters.mobile.qt.android.staged_android_project', lambda path: nullcontext(path))
    def full(self, request):
        assert 'launch.py' in (request.project_root / 'main.py').read_text()
        return MobileBuildArtifact(apk, MobileTarget.ANDROID, MobileFramework.PYSIDE6, 'apk')
    monkeypatch.setattr('velto_cli.adapters.mobile.qt.android.QtAndroidBuilder._pyside_android_impl', full)
    request = MobileFreezeRequest(project_root=root, target=MobileTarget.ANDROID,
        framework=MobileFramework.PYSIDE6, app_name='AndroidAppQt', version='0.1.0',
        entry_point='launch.py', debug=True)
    MobileToolchainFreezer(Mock()).freeze(request)
    fast.assert_not_called()
    assert (root / 'main.py').read_text() == original
