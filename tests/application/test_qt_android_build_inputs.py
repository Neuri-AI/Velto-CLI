"""Buildozer must not copy prior APK inspection folders as project sources."""
from pathlib import Path
import pytest

from velto_cli.adapters.mobile.qt.android_build_inputs import (
    isolate_previous_android_build_outputs,
)


def test_prior_build_tree_is_hidden_during_deploy_then_restored(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    old = root / "build/android/qt/Copia de APK/assets/private/velto"
    old.mkdir(parents=True)
    (old / "__init__.py").write_text("previous")
    with isolate_previous_android_build_outputs(root):
        assert not (root / "build").exists()
        assert any(
            p.name.startswith(".velto-android-build-")
            for p in tmp_path.iterdir() if p.is_dir()
        )
    assert (old / "__init__.py").read_text() == "previous"


def test_build_tree_restored_after_failed_deploy(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    source = root / "build/android/qt/app.apk"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"previous")
    with pytest.raises(RuntimeError, match="failed"):
        with isolate_previous_android_build_outputs(root):
            raise RuntimeError("deployment failed")
    assert source.read_bytes() == b"previous"


def test_no_build_dir_is_noop(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    with isolate_previous_android_build_outputs(root):
        assert not (root / "build").exists()
    assert not (root / "build").exists()


def test_symlink_build_dir_is_never_moved(tmp_path):
    root = tmp_path / "project"
    external = tmp_path / "external"
    root.mkdir()
    external.mkdir()
    (root / "build").symlink_to(external, target_is_directory=True)
    with pytest.raises(RuntimeError, match="Cannot safely isolate"):
        with isolate_previous_android_build_outputs(root):
            pass
    assert (root / "build").is_symlink()


def test_root_apk_extraction_folder_hidden_and_restored(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    (root / "demo-debug.apk").write_bytes(b"apk")
    extracted = root / "demo-debug/assets/private/velto"
    extracted.mkdir(parents=True)
    (extracted / "__init__.py").write_text("read-only extracted package")
    with isolate_previous_android_build_outputs(root):
        assert not (root / "demo-debug").exists()
        assert (root / "demo-debug.apk").exists()
        assert not (root / "build").exists()
    assert (extracted / "__init__.py").read_text() == "read-only extracted package"


def test_root_apk_inspection_folder_restored_after_failure(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    (root / "demo-debug.apk").touch()
    extracted = root / "demo-debug/assets/private"
    extracted.mkdir(parents=True)
    (extracted / "main.py").write_text("old")
    with pytest.raises(RuntimeError, match="deploy failed"):
        with isolate_previous_android_build_outputs(root):
            assert not extracted.exists()
            raise RuntimeError("deploy failed")
    assert (extracted / "main.py").read_text() == "old"


def test_regular_project_directories_are_not_isolated(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    application = root / "components"
    application.mkdir()
    (application / "component.py").write_text("real source")
    with isolate_previous_android_build_outputs(root):
        assert (application / "component.py").is_file()
    assert (application / "component.py").is_file()
