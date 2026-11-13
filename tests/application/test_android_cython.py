"""Unit validation of native Android Cython pipeline contracts.

These tests validate translation and policy only; Android NDK/device
certification runs separately on macOS/Android hardware.
"""
from __future__ import annotations

from pathlib import Path
import subprocess
import sys

import pytest

from velto_cli.adapters.mobile.qt.android_cython import (
    module_name, output_name, _is_elf_arm64,
)


def test_cython_does_not_fallback_to_python_bytecode(monkeypatch):
    from velto_cli.adapters.mobile.qt import android_cython

    def fail(*args, **kwargs):
        raise RuntimeError("NDK unavailable")
    monkeypatch.setattr(android_cython, "_ndk_tools", fail)
    monkeypatch.setattr(android_cython, "_cython_python", lambda: Path(sys.executable))
    with pytest.raises(RuntimeError, match="NDK unavailable"):
        android_cython.compile_android_extensions(
            [("main.py", Path("main.py"), Path("main.so"))],
            apk=Path("missing.apk"),
            toolchain_root=Path("."),
            obfuscate=True,
        )
    assert not Path("main.so").exists()


def test_android_extension_links_math_library(tmp_path, monkeypatch):
    from zipfile import ZipFile
    from velto_cli.adapters.mobile.qt import android_cython

    elf = bytearray(32)
    elf[:6] = b"\x7fELF\x02\x01"
    elf[18:20] = (183).to_bytes(2, "little")
    apk = tmp_path / "app.apk"
    with ZipFile(apk, "w") as bundle:
        bundle.writestr("lib/arm64-v8a/libpython3.14.so", elf)
    destination = tmp_path / "module.so"
    commands = []

    monkeypatch.setattr(android_cython, "_cython_python", lambda: Path(sys.executable))
    monkeypatch.setattr(android_cython, "_ndk_tools", lambda api: (Path("clang"), None))
    monkeypatch.setattr(android_cython, "_target_headers", lambda *a, **kw: [tmp_path])
    monkeypatch.setattr(android_cython, "_cached_headers", lambda key: [tmp_path])

    def run(command):
        commands.append(command)
        if command[0] == "clang":
            destination.write_bytes(elf)

    monkeypatch.setattr(android_cython, "_run", run)
    android_cython.compile_android_extensions(
        [("module.py", tmp_path / "module.py", destination)],
        apk=apk, toolchain_root=tmp_path, obfuscate=False,
    )
    assert "annotation_typing=False" in commands[0]
    assert "-lm" in commands[1]
    assert commands[1].index("-lm") > commands[1].index("-lpython3.14")


@pytest.mark.parametrize("source_name,expected_module,expected_output", [
    ("main.py", "main", "main.so"),
    ("components/test.py", "components.test", "components/test.so"),
    ("components/__init__.py", "components", "components/__init__.so"),
])
def test_native_module_mapping(source_name, expected_module, expected_output):
    assert module_name(source_name) == expected_module
    assert output_name(source_name) == expected_output


def test_rejects_host_elf_when_android_arm64_required():
    elf = bytearray(32)
    elf[:6] = b"\x7fELF\x02\x01"
    elf[18:20] = (62).to_bytes(2, "little")  # x86_64
    assert not _is_elf_arm64(elf)


def test_cython_can_translate_package_native_initializer(tmp_path):
    cython = pytest.importorskip("Cython")
    parts = cython.__version__.split(".")
    if not all(p.isdigit() for p in parts[:3]) or tuple(
        int(p) for p in parts[:3]
    ) < (3, 2, 8):
        pytest.skip("Cython 3.2.8+ needed for native CPython 3.14 generation")
    source = tmp_path / "__init__.py"
    source.write_text("VALUE = 42\n", encoding="utf-8")
    output = tmp_path / "components.c"
    result = subprocess.run(
        [
            sys.executable, "-m", "cython", "-3", "--module-name",
            "components", "-o", str(output), str(source),
        ],
        capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "PyInit_components" in output.read_text(encoding="utf-8")


def test_cython_finds_actual_p4a_android_root_headers(tmp_path, monkeypatch):
    from velto_cli.adapters.mobile.qt import android_cython
    platform = tmp_path / ".buildozer/android/platform"
    include = (
        platform / "build-arm64-v8a/build/other_builds/python3/arm64-v8a__ndk_target_28"
        / "python3/android-build/android-root/include/python3.14"
    )
    include.mkdir(parents=True)
    (include / "Python.h").write_text("/* Android target */")
    (include / "pyconfig.h").write_text("/* Android ABI */")
    monkeypatch.setattr(
        android_cython, "_python_header_roots", lambda root: [platform],
    )
    assert android_cython._target_headers(tmp_path, (3, 14)) == [include]


def test_cython_rejects_macos_pyconfig_for_android(tmp_path, monkeypatch):
    from velto_cli.adapters.mobile.qt import android_cython
    platform = tmp_path / ".buildozer/android/platform"
    include = (
        platform / "build-arm64-v8a/build/other_builds/python3/arm64"
        / "python3/android-build/android-root/include/python3.14"
    )
    include.mkdir(parents=True)
    (include / "Python.h").write_text("/* target */")
    # No Android pyconfig.h. Host pyconfig.h elsewhere is forbidden.
    (tmp_path / "macos-pyconfig.h").write_text("/* host */")
    monkeypatch.setattr(
        android_cython, "_python_header_roots", lambda root: [platform],
    )
    with pytest.raises(RuntimeError, match="TARGET headers"):
        android_cython._target_headers(tmp_path, (3, 14))


def test_cython_can_find_reused_global_p4a_headers(tmp_path, monkeypatch):
    from velto_cli.adapters.mobile.qt import android_cython
    global_platform = tmp_path / "global" / ".buildozer/android/platform"
    include = (
        global_platform / "build-arm64-v8a/build/other_builds/python3/android"
        / "python3/android-build/android-root/include/python3.14"
    )
    include.mkdir(parents=True)
    (include / "Python.h").write_text("/* target */")
    (include / "pyconfig.h").write_text("/* target */")
    monkeypatch.setattr(
        android_cython, "_python_header_roots",
        lambda stage: [stage / ".buildozer/android/platform", global_platform],
    )
    assert android_cython._target_headers(tmp_path / "stage", (3, 14)) == [include]


def test_target_header_cache_persists_without_moving_originals(
    tmp_path, monkeypatch,
):
    from velto_cli.adapters.mobile.qt import android_cython
    cache_root = tmp_path / "persisted-cache"
    monkeypatch.setattr(android_cython, "_target_header_cache", lambda: cache_root)
    include = tmp_path / "p4a" / "include"
    include.mkdir(parents=True)
    (include / "Python.h").write_text("target header")
    (include / "pyconfig.h").write_text("target pyconfig")
    key = "a" * 64
    result = android_cython._cache_headers([include], key)
    assert result == [cache_root / key / "include"]
    assert (result[0] / "Python.h").read_text() == "target header"
    assert (include / "Python.h").read_text() == "target header"
    assert android_cython._cached_headers(key) == result


def test_target_header_cache_separates_distinct_android_libpython(tmp_path, monkeypatch):
    from velto_cli.adapters.mobile.qt import android_cython
    monkeypatch.setattr(
        android_cython, "_target_header_cache", lambda: tmp_path / "cache",
    )
    include = tmp_path / "target"
    include.mkdir()
    (include / "Python.h").write_text("python target")
    (include / "pyconfig.h").write_text("android ABI")
    android_cython._cache_headers([include], "a" * 64)
    assert android_cython._cached_headers("b" * 64) is None
    assert android_cython._target_headers(
        tmp_path, (3, 14), libpython_hash="a" * 64,
    ) == [tmp_path / "cache" / ("a" * 64) / "include"]



def test_p4a_native_recovery_only_invokes_create(tmp_path, monkeypatch):
    import json
    from velto_cli.adapters.mobile.qt import android_p4a_headers

    storage = tmp_path / ".buildozer/android/platform/build-arm64-v8a"
    dist = storage / "dists/AndroidAppQt"
    dist.mkdir(parents=True)
    (dist / "dist_info.json").write_text(json.dumps({
        "recipes": [
            "hostpython3", "libffi", "openssl", "sqlite3", "python3",
            "genericndkbuild", "shiboken6", "pyside6",
        ]
    }))
    (tmp_path / "deployment/recipes").mkdir(parents=True)
    (tmp_path / "buildozer.spec").write_text(
        "[app]\nandroid.api = 35\nandroid.ndk_api = 28\n"
    )
    monkeypatch.setattr(
        android_p4a_headers, "_ndk_root", lambda: tmp_path / "ndk",
    )
    monkeypatch.setattr(
        android_p4a_headers, "_android_sdk_root", lambda: tmp_path / "sdk",
    )
    cmd = android_p4a_headers._create_command(tmp_path, 28)
    assert cmd[3] == "create"
    assert "--force-build" in cmd
    assert "--local-recipes" in cmd
    assert cmd[cmd.index("--android-api") + 1] == "35"
    assert cmd[cmd.index("--ndk-api") + 1] == "28"
    assert "--qt-libs" not in cmd
    assert "--load-local-libs" not in cmd
    assert "apk" not in cmd


def test_p4a_header_recovery_clears_global_force_flag(tmp_path, monkeypatch, capsys):
    import json
    import os
    from io import StringIO
    from velto_cli.adapters.mobile.qt import android_p4a_headers

    storage = tmp_path / ".buildozer/android/platform/build-arm64-v8a"
    dist = storage / "dists/AndroidAppQt"
    dist.mkdir(parents=True)
    (dist / "dist_info.json").write_text(json.dumps({
        "recipes": ["python3", "pyside6", "shiboken6"],
    }))
    (tmp_path / "deployment/recipes").mkdir(parents=True)
    monkeypatch.setattr(
        android_p4a_headers, "_ndk_root", lambda: tmp_path / "ndk",
    )
    monkeypatch.setattr(
        android_p4a_headers, "_android_sdk_root", lambda: tmp_path / "sdk",
    )
    checkout = tmp_path / ".buildozer/android/platform/python-for-android"
    package = checkout / "pythonforandroid"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("")
    (package / "toolchain.py").write_text("")
    monkeypatch.setenv("APP_P4A_EXTRA_ARGS", "--force-build")
    monkeypatch.setenv("PYTHONPATH", "/existing/python/path")
    commands = []
    def mock_run(cmd, **kwargs):
        commands.append((cmd, kwargs["env"]))
        include = (
            storage / "build/other_builds/python3/arm64-v8a__ndk_target_28"
            / "python3/android-build/android-root/include/python3.14"
        )
        include.mkdir(parents=True)
        (include / "Python.h").write_text("native")
        (include / "pyconfig.h").write_text("target")
        class Process:
            stdout = StringIO("p4a compiling Python\n")
            def wait(self):
                return 0
        return Process()

    monkeypatch.setattr(android_p4a_headers.subprocess, "Popen", mock_run)
    android_p4a_headers.recover_android_python_headers(tmp_path, (3, 14))
    assert len(commands) == 1
    assert commands[0][0][3] == "create"
    assert "APP_P4A_EXTRA_ARGS" not in commands[0][1]
    assert commands[0][1]["PYTHONPATH"] == (
        str(checkout) + os.pathsep + "/existing/python/path"
    )
    assert "--force-build" in commands[0][0]
    assert commands[0][1]["PYTHONUNBUFFERED"] == "1"
    assert "p4a compiling Python" in capsys.readouterr().out


def test_p4a_recovery_streams_failure_and_reports_exit(tmp_path, monkeypatch, capsys):
    from velto_cli.adapters.mobile.qt import android_p4a_headers as p4a

    monkeypatch.setattr(p4a, "_create_command", lambda *args: [
        sys.executable, "-c",
        "import sys; print('recipe failed', file=sys.stderr); sys.exit(7)",
    ])
    monkeypatch.setattr(p4a, "_p4a_checkout", lambda root: tmp_path)
    monkeypatch.setattr(p4a, "_ndk_root", lambda: tmp_path)
    monkeypatch.setattr(p4a, "_android_sdk_root", lambda: tmp_path)
    with pytest.raises(RuntimeError, match="p4a create failed \\(exit 7\\).*recipe failed"):
        p4a.recover_android_python_headers(tmp_path, (3, 14))
    assert "recipe failed" in capsys.readouterr().out


@pytest.mark.parametrize("interactive", [False, True])
def test_p4a_download_progress_avoids_flooding_and_preserves_logs(interactive):
    from io import StringIO
    from velto_cli.adapters.mobile.qt.android_p4a_headers import _RecoveryOutput

    class Stream(StringIO):
        def isatty(self):
            return interactive

    stream = Stream()
    output = _RecoveryOutput(stream)
    output.write("[INFO] Downloading python3 from example\n")
    for percent in (0, 1, 2, 9, 10, 11, 48, 48.1, 50, 100):
        output.write(f"- Download {percent}%\n")
    output.write("Download failed: retrying\n")
    output.write("- Download 0%\n")
    output.finish()
    result = stream.getvalue()
    assert "Downloading python3 from example" in result
    assert "Download failed: retrying" in result
    if interactive:
        assert "\r  Download [" in result
        assert "100.00%\nDownload failed" in result
    else:
        assert "Download 48.10%" not in result
        assert result.count("Download 0.00%") == 2
        assert len(result.splitlines()) < 10


def test_p4a_recovery_finds_verified_qt_dist_without_metadata(tmp_path, monkeypatch):
    from velto_cli.adapters.mobile.qt import android_p4a_headers as p4a
    dist = (
        tmp_path / ".buildozer/android/platform/build-arm64-v8a"
        / "dists/AndroidAppQt"
    )
    apk = dist / "build/outputs/apk/debug/AndroidAppQt-debug.apk"
    apk.parent.mkdir(parents=True)
    from zipfile import ZipFile
    with ZipFile(apk, "w") as archive:
        archive.writestr("lib/arm64-v8a/libmain_arm64-v8a.so", b"fixture")
        archive.writestr("lib/arm64-v8a/libQt6Core_arm64-v8a.so", b"fixture")
        archive.writestr("lib/arm64-v8a/libpython3.14.so", b"fixture")
        archive.writestr("assets/private.tar", b"fixture")
    (tmp_path / "deployment/recipes").mkdir(parents=True)
    monkeypatch.setattr(p4a, "_ndk_root", lambda: tmp_path / "ndk")
    monkeypatch.setattr(p4a, "_android_sdk_root", lambda: tmp_path / "sdk")
    found, storage, info = p4a._distribution(tmp_path, apk)
    assert found == dist
    assert storage.name == "build-arm64-v8a"
    assert {"python3", "pyside6", "shiboken6"}.issubset(info["recipes"])
    cmd = p4a._create_command(tmp_path, 28, apk)
    assert cmd[3] == "create"
    assert "--force-build" in cmd
    assert "--requirements" in cmd


def test_p4a_recovery_refuses_unverified_dist_without_metadata(tmp_path):
    from velto_cli.adapters.mobile.qt import android_p4a_headers as p4a
    dist = (
        tmp_path / ".buildozer/android/platform/build-arm64-v8a"
        / "dists/AndroidAppQt"
    )
    dist.mkdir(parents=True)
    (dist / "build/outputs/apk/debug").mkdir(parents=True)
    (dist / "build/outputs/apk/debug/stale.apk").write_bytes(b"stale")
    with pytest.raises(RuntimeError, match="valid previously generated Qt APK"):
        p4a._distribution(tmp_path, dist / "build/outputs/apk/debug/stale.apk")


def test_p4a_apk_authority_without_intermediate_libs(tmp_path):
    from zipfile import ZipFile
    from velto_cli.adapters.mobile.qt import android_p4a_headers as p4a
    dist = (
        tmp_path / ".buildozer/android/platform/build-arm64-v8a"
        / "dists/AndroidAppQt"
    )
    dist.mkdir(parents=True)
    apk = tmp_path / "AndroidAppQt-0.1-arm64-v8a-debug.apk"
    with ZipFile(apk, "w") as bundle:
        bundle.writestr("lib/arm64-v8a/libpython3.14.so", b"fixture")
        bundle.writestr("lib/arm64-v8a/libQt6Gui_arm64-v8a.so", b"fixture")
        bundle.writestr("assets/private.tar", b"fixture")
    # The very scenario seen in PySide6: APK exists, intermediate native
    # library folder and distribution metadata are not available.
    assert not (dist / "libs").exists()
    assert not (dist / "dist_info.json").exists()
    selected, storage, info = p4a._distribution(tmp_path, apk)
    assert selected == dist
    assert storage.name == "build-arm64-v8a"
    assert set(("python3", "shiboken6", "pyside6")).issubset(info["recipes"])


def test_p4a_recovery_when_qt_erases_entire_dists_tree(tmp_path, monkeypatch):
    from zipfile import ZipFile
    from velto_cli.adapters.mobile.qt import android_p4a_headers as p4a
    apk = tmp_path / "AndroidAppQt-0.1-arm64-v8a-debug.apk"
    with ZipFile(apk, "w") as z:
        z.writestr("lib/arm64-v8a/libpython3.14.so", b"fixture")
        z.writestr("lib/arm64-v8a/libQt6Gui_arm64-v8a.so", b"fixture")
        z.writestr("assets/private.tar", b"fixture")
    (tmp_path / "deployment/recipes").mkdir(parents=True)
    monkeypatch.setattr(p4a, "_ndk_root", lambda: tmp_path / "ndk")
    monkeypatch.setattr(p4a, "_android_sdk_root", lambda: tmp_path / "sdk")
    assert not (tmp_path / ".buildozer/android/platform/build-arm64-v8a/dists").exists()
    distribution, storage, info = p4a._distribution(tmp_path, apk)
    assert distribution == storage / "dists/AndroidAppQt"
    assert set(("python3", "pyside6", "shiboken6")).issubset(info["recipes"])
    args = p4a._create_command(tmp_path, 28, apk)
    assert args[3] == "create"
    assert args[args.index("--dist_name") + 1] == "AndroidAppQt"
    assert "--force-build" in args
    assert not distribution.exists()


def test_p4a_missing_dists_cannot_proceed_without_real_apk(tmp_path):
    from velto_cli.adapters.mobile.qt import android_p4a_headers as p4a
    with pytest.raises(RuntimeError, match="Cannot verify a Qt"):
        p4a._distribution(tmp_path)
    fake = tmp_path / "broken.apk"
    fake.write_bytes(b"not an apk")
    with pytest.raises(RuntimeError, match="valid previously generated Qt"):
        p4a._distribution(tmp_path, fake)


@pytest.mark.parametrize("filename,expected", [
    ("AndroidAppQt-0.1-arm64-v8a-debug.apk", "AndroidAppQt"),
    ("AndroidAppQt-debug.apk", "AndroidAppQt"),
    ("AndroidAppQt-1.2.3-arm64-v8a-release.apk", "AndroidAppQt"),
])
def test_p4a_distribution_name_from_verified_apk_filename(filename, expected):
    from velto_cli.adapters.mobile.qt.android_p4a_headers import (
        _distribution_name_from_apk,
    )
    assert _distribution_name_from_apk(Path(filename)) == expected


def test_p4a_distribution_name_rejects_unexpected_filename():
    from velto_cli.adapters.mobile.qt.android_p4a_headers import (
        _distribution_name_from_apk,
    )
    with pytest.raises(RuntimeError, match="Cannot determine"):
        _distribution_name_from_apk(Path("broken.apk"))


def test_p4a_checkout_uses_buildozer_staged_module(tmp_path):
    from velto_cli.adapters.mobile.qt.android_p4a_headers import _p4a_checkout
    package = (
        tmp_path / ".buildozer/android/platform/python-for-android"
        / "pythonforandroid"
    )
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("")
    (package / "toolchain.py").write_text("")
    assert _p4a_checkout(tmp_path) == package.parent


def test_p4a_checkout_fails_closed_if_missing(tmp_path):
    from velto_cli.adapters.mobile.qt.android_p4a_headers import _p4a_checkout
    with pytest.raises(RuntimeError, match="Buildozer removed its python-for-android source checkout"):
        _p4a_checkout(tmp_path)


def test_p4a_checkout_finds_existing_global_buildozer_tree(
    tmp_path, monkeypatch,
):
    from velto_cli.adapters.mobile.qt import android_p4a_headers as p4a
    fake_home = tmp_path / "home"
    monkeypatch.setattr(p4a.Path, "home", lambda: fake_home)
    package = (
        fake_home / ".buildozer/android/platform/python-for-android"
        / "pythonforandroid"
    )
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("")
    (package / "toolchain.py").write_text("")
    assert p4a._p4a_checkout(tmp_path / "stage") == package.parent


def test_p4a_checkout_reads_buildozer_source_dir(tmp_path, monkeypatch):
    from velto_cli.adapters.mobile.qt import android_p4a_headers as p4a
    monkeypatch.delenv("VELTO_ANDROID_P4A_SOURCE_DIR", raising=False)
    checkout = tmp_path / "source"
    package = checkout / "pythonforandroid"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("")
    (package / "toolchain.py").write_text("")
    stage = tmp_path / "stage"
    stage.mkdir()
    (stage / "buildozer.spec").write_text(
        "[app]\np4a.source_dir = " + str(checkout) + "\n"
    )
    assert p4a._p4a_checkout(stage) == checkout


def test_p4a_checkout_requires_pinned_commit_for_reclone(
    tmp_path, monkeypatch,
):
    from velto_cli.adapters.mobile.qt import android_p4a_headers as p4a
    monkeypatch.setattr(p4a.Path, "home", lambda: tmp_path / "home")
    monkeypatch.delenv("VELTO_ANDROID_P4A_SOURCE_DIR", raising=False)
    stage = tmp_path / "stage"
    stage.mkdir()
    (stage / "buildozer.spec").write_text(
        "[app]\np4a.branch = develop\np4a.commit = HEAD\n"
    )
    with pytest.raises(RuntimeError, match="exact Git commit hash"):
        p4a._p4a_checkout(stage)
