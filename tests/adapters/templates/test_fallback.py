from pathlib import Path
from unittest.mock import Mock

from velto_cli.adapters.templates.fallback import FallbackTemplateProvider
from velto_cli.domain.project import Binding, TargetPlatform
from velto_cli.domain.version import Version


class TestFallbackTemplateProvider:
    def test_resolve_template_uses_primary_provider(self):
        primary = Mock()
        fallback = Mock()

        template_path = Path("/templates/pyside6")
        primary.resolve_template.return_value = template_path

        provider = FallbackTemplateProvider(
            primary=primary,
            fallback=fallback,
        )

        result = provider.resolve_template(
            Binding.PYSIDE6,
            TargetPlatform.X86_64,
        )

        assert result == template_path

        primary.resolve_template.assert_called_once_with(
            binding=Binding.PYSIDE6,
            target_platform=TargetPlatform.X86_64,
            version=None,
        )
        fallback.resolve_template.assert_not_called()

    def test_resolve_template_uses_fallback_when_primary_fails(self):
        primary = Mock()
        fallback = Mock()

        primary.resolve_template.side_effect = RuntimeError(
            "GitHub unavailable"
        )

        template_path = Path("/templates/pyside6")
        fallback.resolve_template.return_value = template_path

        provider = FallbackTemplateProvider(
            primary=primary,
            fallback=fallback,
        )

        result = provider.resolve_template(
            Binding.PYSIDE6,
            TargetPlatform.X86_64,
        )

        assert result == template_path

        fallback.resolve_template.assert_called_once_with(
            binding=Binding.PYSIDE6,
            target_platform=TargetPlatform.X86_64,
            version=None,
        )

    def test_resolve_template_passes_version_to_primary(self):
        primary = Mock()
        fallback = Mock()

        template_path = Path("/templates/pyside6")
        primary.resolve_template.return_value = template_path

        provider = FallbackTemplateProvider(
            primary=primary,
            fallback=fallback,
        )

        version = Version.parse("1.1.0")

        result = provider.resolve_template(
            Binding.PYSIDE6,
            TargetPlatform.X86_64,
            version,
        )

        assert result == template_path

        primary.resolve_template.assert_called_once_with(
            binding=Binding.PYSIDE6,
            target_platform=TargetPlatform.X86_64,
            version=version,
        )

    def test_resolve_template_passes_version_to_fallback(self):
        primary = Mock()
        fallback = Mock()

        primary.resolve_template.side_effect = RuntimeError(
            "GitHub unavailable"
        )

        template_path = Path("/templates/pyside6")
        fallback.resolve_template.return_value = template_path

        provider = FallbackTemplateProvider(
            primary=primary,
            fallback=fallback,
        )

        version = Version.parse("1.1.0")

        result = provider.resolve_template(
            Binding.PYSIDE6,
            TargetPlatform.X86_64,
            version,
        )

        assert result == template_path

        fallback.resolve_template.assert_called_once_with(
            binding=Binding.PYSIDE6,
            target_platform=TargetPlatform.X86_64,
            version=version,
        )
