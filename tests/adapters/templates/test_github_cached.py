import io
import zipfile
from unittest.mock import Mock, patch

import pytest
import requests

from velto_cli.adapters.templates.github_cached import (
    GitHubCachedTemplateProvider,
)
from velto_cli.domain.errors import TemplateUnavailableError
from velto_cli.domain.project import Binding, TargetPlatform
from velto_cli.domain.version import Version


class FakeProgress:
    def __init__(self):
        self.started = []
        self.updates = []
        self.stopped = False

    def start(self, message, total=None):
        self.started.append((message, total))

    def update(self, completed):
        self.updates.append(completed)

    def stop(self):
        self.stopped = True


class TestGitHubCachedTemplateProvider:
    @pytest.mark.parametrize(
        ("binding", "target_platform"),
        [
            (Binding.PYSIDE6, TargetPlatform.X86_64),
            (Binding.KIVY, TargetPlatform.IPHONE),
            (Binding.PYSIDE6, TargetPlatform.ANDROID),
            (Binding.TKINTER, TargetPlatform.APPLE_SILICON),
        ],
    )
    def test_resolve_template_uses_shared_repository(
        self,
        tmp_path,
        binding,
        target_platform,
    ):
        provider = GitHubCachedTemplateProvider(
            organization="Neuri-AI",
            cache_dir=tmp_path,
        )

        result = provider._resolve_repo(
            binding,
            target_platform,
        )

        assert result == (
            "Neuri-AI/velto-boilerplate"
        )

    def test_cache_name_is_shared_across_bindings_and_platforms(
        self,
        tmp_path,
    ):
        provider = GitHubCachedTemplateProvider(
            organization="Neuri-AI",
            cache_dir=tmp_path,
        )

        desktop_result = provider._cache_name(
            Binding.PYSIDE6,
            TargetPlatform.X86_64,
            Version.parse("1.1.0"),
        )
        mobile_result = provider._cache_name(
            Binding.KIVY,
            TargetPlatform.ANDROID,
            Version.parse("1.1.0"),
        )

        assert desktop_result == "template-velto-1.1.0"
        assert mobile_result == desktop_result

    def test_find_compatible_tags_filters_versions(self, tmp_path):
        provider = GitHubCachedTemplateProvider(
            organization="Neuri-AI",
            cache_dir=tmp_path,
            supported_range="^1.0.0",
        )

        tags = [
            {
                "name": "v1.0.0",
                "zipball_url": "https://example.com/1.0.0.zip",
            },
            {
                "name": "1.1.0",
                "zipball_url": "https://example.com/1.1.0.zip",
            },
            {
                "name": "v2.0.0",
                "zipball_url": "https://example.com/2.0.0.zip",
            },
            {
                "name": "invalid",
                "zipball_url": "https://example.com/invalid.zip",
            },
        ]

        result = provider._find_compatible_tags(tags)

        assert [version for version, _ in result] == [
            Version.parse("1.0.0"),
            Version.parse("1.1.0"),
        ]

    def test_resolve_template_downloads_latest_compatible_tag(
        self,
        tmp_path,
        monkeypatch,
    ):
        provider = GitHubCachedTemplateProvider(
            organization="Neuri-AI",
            cache_dir=tmp_path,
        )

        tags = [
            {
                "name": "v1.0.0",
                "zipball_url": "https://example.com/1.0.0.zip",
            },
            {
                "name": "v1.1.0",
                "zipball_url": "https://example.com/1.1.0.zip",
            },
        ]

        monkeypatch.setattr(
            provider,
            "_fetch_tags",
            lambda repo, headers: tags,
        )

        downloaded = tmp_path / "downloaded"

        monkeypatch.setattr(
            provider,
            "_download_template",
            lambda **kwargs: downloaded,
        )

        result = provider.resolve_template(
            Binding.PYSIDE6,
            TargetPlatform.X86_64,
        )

        assert result == downloaded

    def test_resolve_template_selects_requested_version(
        self,
        tmp_path,
        monkeypatch,
    ):
        provider = GitHubCachedTemplateProvider(
            organization="Neuri-AI",
            cache_dir=tmp_path,
        )

        tags = [
            {
                "name": "v1.0.0",
                "zipball_url": "https://example.com/1.0.0.zip",
            },
            {
                "name": "v1.1.0",
                "zipball_url": "https://example.com/1.1.0.zip",
            },
        ]

        monkeypatch.setattr(
            provider,
            "_fetch_tags",
            lambda repo, headers: tags,
        )

        download = Mock(return_value=tmp_path / "template")

        monkeypatch.setattr(
            provider,
            "_download_template",
            download,
        )

        requested_version = Version.parse("1.0.0")

        result = provider.resolve_template(
            Binding.PYSIDE6,
            TargetPlatform.X86_64,
            requested_version,
        )

        assert result == tmp_path / "template"

        download.assert_called_once_with(
            zipball_url="https://example.com/1.0.0.zip",
            cached_path=(
                tmp_path
                / "template-velto-1.0.0"
            ),
            headers=mock_any_headers(),
        )

    def test_resolve_template_returns_cached_template(
        self,
        tmp_path,
        monkeypatch,
    ):
        cached_path = (
            tmp_path
            / "template-velto-1.1.0"
        )
        cached_path.mkdir()

        provider = GitHubCachedTemplateProvider(
            organization="Neuri-AI",
            cache_dir=tmp_path,
        )

        monkeypatch.setattr(
            provider,
            "_fetch_tags",
            lambda repo, headers: [
                {
                    "name": "v1.1.0",
                    "zipball_url": "https://example.com/1.1.0.zip",
                }
            ],
        )

        download = Mock()

        monkeypatch.setattr(
            provider,
            "_download_template",
            download,
        )

        result = provider.resolve_template(
            Binding.PYSIDE6,
            TargetPlatform.X86_64,
        )

        assert result == cached_path
        download.assert_not_called()

    def test_resolve_template_uses_exact_cached_version(
        self,
        tmp_path,
        monkeypatch,
    ):
        cached_path = (
            tmp_path
            / "template-velto-1.1.0"
        )
        cached_path.mkdir()

        provider = GitHubCachedTemplateProvider(
            organization="Neuri-AI",
            cache_dir=tmp_path,
        )

        monkeypatch.setattr(
            provider,
            "_fetch_tags",
            Mock(side_effect=requests.RequestException),
        )

        result = provider.resolve_template(
            Binding.PYSIDE6,
            TargetPlatform.X86_64,
            Version.parse("1.1.0"),
        )

        assert result == cached_path

    def test_resolve_template_raises_when_requested_version_is_not_cached(
        self,
        tmp_path,
        monkeypatch,
    ):
        provider = GitHubCachedTemplateProvider(
            organization="Neuri-AI",
            cache_dir=tmp_path,
        )

        monkeypatch.setattr(
            provider,
            "_fetch_tags",
            Mock(side_effect=requests.RequestException),
        )

        with pytest.raises(TemplateUnavailableError):
            provider.resolve_template(
                Binding.PYSIDE6,
                TargetPlatform.X86_64,
                Version.parse("1.1.0"),
            )

    def test_resolve_template_uses_latest_compatible_cache_when_github_fails(
        self,
        tmp_path,
        monkeypatch,
    ):
        old_template = (
            tmp_path
            / "template-velto-1.0.0"
        )
        new_template = (
            tmp_path
            / "template-velto-1.1.0"
        )

        old_template.mkdir()
        new_template.mkdir()

        provider = GitHubCachedTemplateProvider(
            organization="Neuri-AI",
            cache_dir=tmp_path,
        )

        monkeypatch.setattr(
            provider,
            "_fetch_tags",
            Mock(side_effect=requests.RequestException),
        )

        result = provider.resolve_template(
            Binding.PYSIDE6,
            TargetPlatform.X86_64,
        )

        assert result == new_template

    def test_download_template_extracts_archive(
        self,
        tmp_path,
    ):
        provider = GitHubCachedTemplateProvider(
            organization="Neuri-AI",
            cache_dir=tmp_path,
        )

        archive_data = io.BytesIO()

        with zipfile.ZipFile(
            archive_data,
            "w",
            zipfile.ZIP_DEFLATED,
        ) as archive:
            archive.writestr(
                "template-abc/main.py",
                "print('hello')",
            )

        content = archive_data.getvalue()

        response = Mock()
        response.headers = {
            "Content-Length": str(len(content)),
        }
        response.iter_content.return_value = [content]

        with pytest.MonkeyPatch.context() as monkeypatch:
            monkeypatch.setattr(
                "velto_cli.adapters.templates.github_cached.requests.get",
                Mock(return_value=response),
            )

            result = provider._download_template(
                zipball_url="https://example.com/template.zip",
                cached_path=tmp_path / "template",
                headers={},
            )

        assert result == tmp_path / "template"
        assert (
            result / "main.py"
        ).read_text() == "print('hello')"

    def test_download_template_replaces_existing_cache(
        self,
        tmp_path,
    ):
        provider = GitHubCachedTemplateProvider(
            organization="Neuri-AI",
            cache_dir=tmp_path,
        )

        cached_path = tmp_path / "template"
        cached_path.mkdir()
        (cached_path / "old.txt").write_text("old")

        archive_data = io.BytesIO()

        with zipfile.ZipFile(
            archive_data,
            "w",
            zipfile.ZIP_DEFLATED,
        ) as archive:
            archive.writestr(
                "template-new/main.py",
                "print('new')",
            )

        content = archive_data.getvalue()

        response = Mock()
        response.headers = {
            "Content-Length": str(len(content)),
        }
        response.iter_content.return_value = [content]

        with pytest.MonkeyPatch.context() as monkeypatch:
            monkeypatch.setattr(
                "velto_cli.adapters.templates.github_cached.requests.get",
                Mock(return_value=response),
            )

            result = provider._download_template(
                zipball_url="https://example.com/template.zip",
                cached_path=cached_path,
                headers={},
            )

        assert result == cached_path
        assert not (cached_path / "old.txt").exists()
        assert (
            cached_path / "main.py"
        ).read_text() == "print('new')"

    def test_download_template_rejects_empty_archive(
        self,
        tmp_path,
    ):
        provider = GitHubCachedTemplateProvider(
            organization="Neuri-AI",
            cache_dir=tmp_path,
        )

        archive_data = io.BytesIO()

        with zipfile.ZipFile(
            archive_data,
            "w",
            zipfile.ZIP_DEFLATED,
        ):
            pass

        content = archive_data.getvalue()

        response = Mock()
        response.headers = {
            "Content-Length": str(len(content)),
        }
        response.iter_content.return_value = [content]

        with pytest.MonkeyPatch.context() as monkeypatch:
            monkeypatch.setattr(
                "velto_cli.adapters.templates.github_cached.requests.get",
                Mock(return_value=response),
            )

            with pytest.raises(zipfile.BadZipFile):
                provider._download_template(
                    zipball_url="https://example.com/template.zip",
                    cached_path=tmp_path / "template",
                    headers={},
                )

    def test_resolve_cached_template_raises_when_no_cache_exists(
        self,
        tmp_path,
    ):
        provider = GitHubCachedTemplateProvider(
            organization="Neuri-AI",
            cache_dir=tmp_path,
        )

        with pytest.raises(TemplateUnavailableError):
            provider._resolve_cached_template(
                Binding.PYSIDE6,
                TargetPlatform.X86_64,
            )

    def test_download_template_reports_progress(
        self,
        tmp_path,
    ):
        provider = GitHubCachedTemplateProvider(
            organization="Neuri-AI",
            cache_dir=tmp_path / "cache",
        )

        progress = FakeProgress()
        provider.progress = progress

        archive_path = tmp_path / "template.zip"

        with zipfile.ZipFile(
            archive_path,
            "w",
        ) as archive:
            archive.writestr(
                "template/file.txt",
                "hello",
            )

        content = archive_path.read_bytes()

        class Response:
            headers = {
                "Content-Length": str(len(content)),
            }

            def raise_for_status(self):
                pass

            def iter_content(self, chunk_size):
                yield content[:10]
                yield content[10:]

        with patch(
            "velto_cli.adapters.templates.github_cached.requests.get",
            return_value=Response(),
        ):
            cached_path = (
                tmp_path
                / "cache"
                / "template"
            )

            result = provider._download_template(
                zipball_url="https://example.com/template.zip",
                cached_path=cached_path,
                headers={},
            )

        assert result == cached_path
        assert progress.started
        assert progress.started[0] == (
            "Downloading boilerplate...",
            len(content),
        )
        assert progress.updates
        assert progress.updates[-1] == len(content)
        assert progress.stopped


def mock_any_headers():
    return {
        "User-Agent": "Velto-CLI/1.0",
        "Accept": "application/vnd.github+json",
    }
