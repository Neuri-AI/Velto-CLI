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
    assert android_cython.has_cached_target_headers()


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
    assert "--qt-libs" not in cmd
    assert "--load-local-libs" not in cmd
    assert "apk" not in cmd


def test_p4a_header_recovery_clears_global_force_flag(tmp_path, monkeypatch):
    import json
    from subprocess import CompletedProcess
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
    monkeypatch.setenv("APP_P4A_EXTRA_ARGS", "--force-build")
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
        return CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(android_p4a_headers.subprocess, "run", mock_run)
    android_p4a_headers.recover_android_python_headers(tmp_path, (3, 14))
    assert len(commands) == 1
    assert commands[0][0][3] == "create"
    assert "APP_P4A_EXTRA_ARGS" not in commands[0][1]
    assert "--force-build" in commands[0][0]


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
    with pytest.raises(RuntimeError, match="valid Qt APK"):
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
