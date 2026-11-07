"""Detection of Velto imports without executing the application."""
from velto_cli.adapters.mobile.qt.android_sources import uses_velto, project_uses_velto


def test_detects_velto_import(tmp_path):
    entry = tmp_path / "main.py"
    entry.write_text("from velto import VeltoContext\n")
    assert uses_velto(entry)
    entry.write_text("# import velto\nprint(1)\n")
    assert not uses_velto(entry)


def test_project_dependency_is_detected_when_main_imports_app_modules(tmp_path):
    entry = tmp_path / "main.py"
    entry.write_text("from app import run\n")
    (tmp_path / "pyproject.toml").write_text('[project]\ndependencies = ["velto>=0.1"]\n')
    assert project_uses_velto(tmp_path, entry)
