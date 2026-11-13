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


def test_staged_project_keeps_nested_project_folders_but_omits_root_outputs(tmp_path):
    from velto_cli.adapters.mobile.qt.android_build_inputs import staged_android_project
    (tmp_path / "nested/build").mkdir(parents=True)
    (tmp_path / "nested/build/data.custom").write_text("runtime")
    (tmp_path / "nested/.well-known").mkdir()
    (tmp_path / "nested/.well-known/data.json").write_text("runtime")
    (tmp_path / "node_modules/example").mkdir(parents=True)
    (tmp_path / "node_modules/example/data.txt").write_text("dependency")
    (tmp_path / "build").mkdir()
    (tmp_path / "build/output.txt").write_text("build output")
    with staged_android_project(tmp_path) as stage:
        assert (stage / "nested/build/data.custom").read_text() == "runtime"
        assert (stage / "nested/.well-known/data.json").read_text() == "runtime"
        assert (stage / "node_modules/example/data.txt").read_text() == "dependency"
        assert not (stage / "build").exists()


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


def test_configured_android_entry_runs_as_main_and_launcher_is_temporary(tmp_path):
    import subprocess
    import sys
    from velto_cli.adapters.mobile.common.android_entrypoint import android_entrypoint

    (tmp_path / 'src').mkdir()
    (tmp_path / 'src/helper.py').write_text('VALUE = "configured"\n')
    (tmp_path / 'src/start.py').write_text(
        'from helper import VALUE\nif __name__ == "__main__": print(VALUE)\n'
    )
    with android_entrypoint(tmp_path, 'src/start.py'):
        result = subprocess.run([sys.executable, str(tmp_path / 'main.py')],
                                text=True, capture_output=True, check=True)
        assert result.stdout.strip() == 'configured'
    assert not (tmp_path / 'main.py').exists()
    assert (tmp_path / 'src/start.py').is_file()


def test_custom_android_entry_preserves_existing_main_module(tmp_path):
    import subprocess
    import sys
    from velto_cli.adapters.mobile.common.android_entrypoint import android_entrypoint
    main = tmp_path / 'main.py'
    main.write_text('VALUE = "original"\n')
    (tmp_path / 'start.py').write_text('import main\nprint(main.VALUE)\n')
    with android_entrypoint(tmp_path, 'start.py'):
        result = subprocess.run([sys.executable, str(main)], text=True,
                                capture_output=True, check=True)
        assert result.stdout.strip() == 'original'
    assert main.read_text() == 'VALUE = "original"\n'
