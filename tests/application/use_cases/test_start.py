from unittest.mock import Mock

import pytest

from velto_cli.application.use_cases.start import RunApplicationUseCase
from velto_cli.domain.errors import MissingBindingError


def make_use_case():
    ui = Mock()
    fs = Mock()
    settings = Mock()
    app_runner = Mock()
    modules = Mock()
    guards = Mock()

    return RunApplicationUseCase(
        ui=ui,
        fs=fs,
        settings=settings,
        app_runner=app_runner,
        modules=modules,
        guards=guards,
    ), fs, settings, modules, guards


def test_execute_reports_missing_binding_as_full_name():
    use_case, fs, settings, modules, guards = make_use_case()

    guards.require_existing_project.return_value = None
    settings.get_optional.return_value = "PySide2"
    modules.is_installed.return_value = False
    fs.resolve.return_value = "."

    with pytest.raises(MissingBindingError) as exc_info:
        use_case.execute()

    message = str(exc_info.value)
    assert "PySide2" in message
    assert "P, y, S, i, d, e, 2" not in message
    assert "Install it using: pip install PySide2" in message


def test_execute_rejects_legacy_binding_setting():
    use_case, _fs, settings, modules, guards = make_use_case()

    guards.require_existing_project.return_value = None
    settings.get_optional.return_value = None

    with pytest.raises(MissingBindingError) as exc_info:
        use_case.execute()

    assert "PySide6" in str(exc_info.value)
    settings.get_optional.assert_called_once_with("framework")
