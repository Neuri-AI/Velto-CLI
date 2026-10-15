
from unittest.mock import Mock

from qyro_cli.application.use_cases.version import ShowVersionUseCase


class TestShowVersionUseCase:
    def test_execute_displays_cli_and_engine_versions(self):
        ui = Mock()
        metadata = Mock()

        metadata.cli_version.return_value = "1.0.0"
        metadata.engine_version.return_value = "1.0.0"

        use_case = ShowVersionUseCase(
            ui=ui,
            metadata=metadata,
        )

        result = use_case.execute()

        assert result is None
        metadata.cli_version.assert_called_once_with()
        metadata.engine_version.assert_called_once_with()
        assert ui.info.call_count == 2
        ui.info.assert_any_call("Qyro [#ff8c00]CLI[/#ff8c00] v1.0.0")
        ui.info.assert_any_call("Qyro [#ff8c00]Engine[/#ff8c00] v1.0.0")
