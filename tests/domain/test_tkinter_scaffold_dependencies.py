"""Regression tests for standard-library Tkinter scaffolding."""
import re

from qyro_cli.domain.project import Binding, ProjectConfig, TargetPlatform
from qyro_cli.domain.version import Version


def _config(binding, addons=None):
    return ProjectConfig(
        app_name="testapp",
        version=Version.parse("1.0.0"),
        author="test",
        binding=binding,
        target_platform=TargetPlatform.X86_64,
        addons=addons or [],
    )


def test_tkinter_scaffold_omits_pypi_dependency():
    variables = _config(Binding.TKINTER).as_template_variables()
    assert variables["python_bindings"] == "Tkinter"
    assert variables["binding_dependencies"] == ""


def test_other_bindings_keep_installable_dependencies():
    for binding in (Binding.PYSIDE6, Binding.PYQT6, Binding.PYQT5, Binding.KIVY):
        variables = _config(binding).as_template_variables()
        assert binding.value in variables["binding_dependencies"]


def test_old_tkinter_dependency_line_is_removable():
    old = 'dependencies = [\\n    "Tkinter",\\n    "requests"\\n]\\n'
    cleaned = re.sub(r'(?im)^[ \\t]*"tkinter",?[ \\t]*\\r?\\n', "", old)
    assert '"Tkinter"' not in cleaned
    assert '"requests"' in cleaned
