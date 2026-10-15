"""
qyro.adapters.templates.bundled

Fallback template provider for offline environments.
"""

from pathlib import Path

from velto_cli.application.ports import TemplateProviderPort
from velto_cli.domain.errors import TemplateUnavailableError
from velto_cli.domain.project import Binding, TargetPlatform
from velto_cli.domain.version import Version


class BundledTemplateProvider(TemplateProviderPort):
    def __init__(self, templates_dir: Path):
        self.templates_dir = templates_dir

    def resolve_template(
        self,
        binding: Binding,
        target_platform: TargetPlatform,
        version: Version | None = None,
    ) -> Path:
        if target_platform in {
            TargetPlatform.IPHONE,
            TargetPlatform.ANDROID,
        }:
            template_dir = (
                self.templates_dir
                / binding.value.lower()
                / target_platform.value.lower()
            )
        else:
            template_dir = (
                self.templates_dir
                / binding.value.lower()
                / "desktop"
            )

        if not template_dir.is_dir():
            raise TemplateUnavailableError(
                f"No bundled template is available for "
                f"{binding.value} on {target_platform.value}."
            )

        return template_dir
