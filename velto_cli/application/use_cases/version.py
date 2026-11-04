"""Use case for displaying the installed Velto CLI version."""

from velto_cli.application.ports import (
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
        self.ui.info(f"Velto [#ff8c00]CLI[/#ff8c00] v{cli_version}")
