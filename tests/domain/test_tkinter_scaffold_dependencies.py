"""Tkinter must never be resolved as a PyPI dependency."""
import re

from velto_cli.domain.project import Binding, ProjectConfig, TargetPlatform
from velto_cli.domain.version import Version


def test_tkinter_has_no_installable_binding_dependency():
    config = ProjectConfig(
        app_name="tk_sample",
        version=Version.parse("1.0.0"),
        author="test",
        binding=Binding.TKINTER,
        target_platform=TargetPlatform.X86_64,
    )
    variables = config.as_template_variables()
    assert variables["python_bindings"] == "Tkinter"
    assert variables["binding_dependencies"] == ""


def test_other_bindings_have_installable_dependencies():
    for binding in (Binding.PYSIDE6, Binding.PYQT6, Binding.KIVY):
        config = ProjectConfig(
            app_name="sample",
            version=Version.parse("1.0.0"),
            author="test",
            binding=binding,
        )
        assert binding.value in config.as_template_variables()["binding_dependencies"]


def test_legacy_manifest_cleanup():
    original = 'dependencies = [\n    "Tkinter",\n    "requests"\n]\n'
    cleaned = re.sub(r'(?im)^[ \t]*"tkinter",?[ \t]*\r?\n', "", original)
    assert '"Tkinter"' not in cleaned
    assert '"requests"' in cleaned
