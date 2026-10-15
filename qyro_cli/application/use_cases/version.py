"""
qyro.application.use_cases.version
Use case for displaying the installed Qyro version.
"""

from qyro_cli.application.ports import (
    PackageMetadataPort,
    UserInteractionPort,
)


class ShowVersionUseCase:
    def __init__(
        self,
        ui: UserInteractionPort,
        metadata: PackageMetadataPort,
    ):
        self.ui = ui
        self.metadata = metadata

    def execute(self) -> None:
        cli_version = self.metadata.cli_version()
        engine_version = self.metadata.engine_version()
        self.ui.info(f"Qyro [#ff8c00]CLI[/#ff8c00] v{cli_version}")
        self.ui.info(f"Qyro [#ff8c00]Engine[/#ff8c00] v{engine_version}")