"""
qyro.adapters.templates.fallback

Composes primary GitHub template provider with local bundled fallback.
"""

from pathlib import Path

from velto_cli.application.ports import TemplateProviderPort
from velto_cli.domain.project import Binding, TargetPlatform
from velto_cli.domain.version import Version


class FallbackTemplateProvider(TemplateProviderPort):
    def __init__(
        self,
        primary: TemplateProviderPort,
        fallback: TemplateProviderPort,
    ):
        self.primary = primary
        self.fallback = fallback

    def resolve_template(
        self,
        binding: Binding,
        target_platform: TargetPlatform,
        version: Version | None = None,
    ) -> Path:
        try:
            return self.primary.resolve_template(
                binding=binding,
                target_platform=target_platform,
                version=version,
            )
        except Exception as e:
            print(e)
            return self.fallback.resolve_template(
                binding=binding,
                target_platform=target_platform,
                version=version,
            )
