"""Fetch and cache the shared Velto boilerplate from GitHub."""

import os
import shutil
import zipfile
from pathlib import Path

import requests

from velto_cli.application.ports import ProgressPort
from velto_cli.domain.errors import (
    InvalidVersionError,
    TemplateUnavailableError,
)
from velto_cli.domain.project import Binding, TargetPlatform
from velto_cli.domain.version import Version


class GitHubCachedTemplateProvider:
    """Provides the shared Velto boilerplate from GitHub with local caching."""

    _BOILERPLATE_REPOSITORY = "velto-boilerplate"
    _CACHE_PREFIX = "template-velto-"

    def __init__(
        self,
        organization: str,
        cache_dir: Path,
        supported_range: str = "^1.0.0",
        progress: ProgressPort | None = None,
    ) -> None:
        self.organization = organization
        self.cache_dir = cache_dir
        self.cache_dir.mkdir(
            parents=True,
            exist_ok=True,
        )
        self.progress = progress

        self.min_version = Version.parse(
            supported_range.lstrip("^")
        )

    def resolve_template(
        self,
        binding: Binding,
        target_platform: TargetPlatform,
        version: Version | None = None,
    ) -> Path:
        repo = self._resolve_repo(
            binding,
            target_platform,
        )

        headers = {
            "User-Agent": "Velto-CLI/1.0",
            "Accept": "application/vnd.github+json",
        }

        token = (
            os.environ.get("QYRO_GITHUB_TOKEN")
            or os.environ.get("GITHUB_TOKEN")
            or os.environ.get("GH_TOKEN")
        )

        if token:
            headers["Authorization"] = f"Bearer {token}"

        try:
            tags = self._fetch_tags(
                repo,
                headers,
            )

            compatible_tags = self._find_compatible_tags(tags)

            if version is not None:
                compatible_tags = [
                    item
                    for item in compatible_tags
                    if item[0] == version
                ]
            else:
                compatible_tags.sort(
                    key=lambda item: item[0],
                    reverse=True,
                )

            if compatible_tags:
                selected_version, zipball_url = compatible_tags[0]

                cached_path = (
                    self.cache_dir
                    / self._cache_name(
                        binding,
                        target_platform,
                        selected_version,
                    )
                )

                if cached_path.exists():
                    return cached_path

                return self._download_template(
                    zipball_url=zipball_url,
                    cached_path=cached_path,
                    headers=headers,
                )

            if version is None:
                default_branch_url = self._default_branch_zipball_url(repo, headers)
                if default_branch_url:
                    fallback_version = self.min_version
                    cached_path = (
                        self.cache_dir
                        / self._cache_name(
                            binding,
                            target_platform,
                            fallback_version,
                        )
                    )

                    if cached_path.exists():
                        return cached_path

                    return self._download_template(
                        zipball_url=default_branch_url,
                        cached_path=cached_path,
                        headers=headers,
                    )

        except requests.RequestException:
            pass

        except (zipfile.BadZipFile, OSError):
            pass

        return self._resolve_cached_template(
            binding,
            target_platform,
            version,
        )

    def _resolve_repo(
        self,
        binding: Binding,
        target_platform: TargetPlatform,
    ) -> str:
        """Return the single boilerplate repository for every project shape."""
        del binding, target_platform
        return f"{self.organization}/{self._BOILERPLATE_REPOSITORY}"

    def _cache_name(
        self,
        binding: Binding,
        target_platform: TargetPlatform,
        version: Version,
    ) -> str:
        del binding, target_platform
        return f"{self._CACHE_PREFIX}{version}"

    def _fetch_tags(
        self,
        repo: str,
        headers: dict[str, str],
    ) -> list[dict]:
        url = f"https://api.github.com/repos/{repo}/tags"

        response = requests.get(
            url,
            headers=headers,
            timeout=10,
        )

        response.raise_for_status()

        return response.json()

    def _default_branch_zipball_url(
        self,
        repo: str,
        headers: dict[str, str],
    ) -> str | None:
        repo_url = f"https://api.github.com/repos/{repo}"

        response = requests.get(
            repo_url,
            headers=headers,
            timeout=10,
        )
        response.raise_for_status()

        payload = response.json()
        branch = str(payload.get("default_branch", "")).strip()

        if not branch:
            return None

        return f"https://api.github.com/repos/{repo}/zipball/{branch}"

    def _find_compatible_tags(
        self,
        tags: list[dict],
    ) -> list[tuple[Version, str]]:
        compatible: list[tuple[Version, str]] = []

        for tag in tags:
            try:
                tag_name = tag["name"].lstrip("v")
                zipball_url = tag["zipball_url"]

                version = Version.parse(tag_name)

                if version.matches_caret(self.min_version):
                    compatible.append(
                        (version, zipball_url)
                    )

            except (
                InvalidVersionError,
                KeyError,
                TypeError,
                ValueError,
            ):
                continue

        return compatible

    def _download_template(
        self,
        zipball_url: str,
        cached_path: Path,
        headers: dict[str, str],
    ) -> Path:
        response = requests.get(
            zipball_url,
            headers=headers,
            stream=True,
            timeout=30,
        )

        response.raise_for_status()

        content_length = response.headers.get("Content-Length")
        total = int(content_length) if content_length else None

        temporary_zip = (
            cached_path.parent
            / f".tmp-{cached_path.name}.zip"
        )

        downloaded = 0

        if self.progress:
            self.progress.start(
                "Downloading boilerplate...",
                total=total,
            )

        try:
            with open(temporary_zip, "wb") as file:
                for chunk in response.iter_content(
                    chunk_size=8192,
                ):
                    if not chunk:
                        continue

                    file.write(chunk)
                    downloaded += len(chunk)

                    if self.progress:
                        self.progress.update(downloaded)

            temporary_dir = (
                cached_path.parent
                / f".tmp-{cached_path.name}"
            )

            if temporary_dir.exists():
                shutil.rmtree(temporary_dir)

            temporary_dir.mkdir(
                parents=True,
                exist_ok=True,
            )

            try:
                with zipfile.ZipFile(temporary_zip) as archive:
                    members = archive.namelist()

                    if not members:
                        raise zipfile.BadZipFile(
                            "The template archive is empty."
                        )

                    archive.extractall(temporary_dir)

                root_folder = members[0].split("/")[0]
                source = temporary_dir / root_folder

                if not source.is_dir():
                    raise zipfile.BadZipFile(
                        "The template archive has an invalid structure."
                    )

                if cached_path.exists():
                    shutil.rmtree(cached_path)

                shutil.move(
                    str(source),
                    str(cached_path),
                )

                return cached_path

            finally:
                shutil.rmtree(
                    temporary_dir,
                    ignore_errors=True,
                )

        finally:
            if self.progress:
                self.progress.stop()

            temporary_zip.unlink(
                missing_ok=True,
            )

    def _resolve_cached_template(
        self,
        binding: Binding,
        target_platform: TargetPlatform,
        requested_version: Version | None = None,
    ) -> Path:
        prefix = self._CACHE_PREFIX

        if requested_version is not None:
            cached_path = (
                self.cache_dir
                / self._cache_name(
                    binding,
                    target_platform,
                    requested_version,
                )
            )

            if cached_path.is_dir():
                return cached_path

            raise TemplateUnavailableError(
                f"Template version {requested_version} is not "
                f"available locally."
            )

        cached_templates: list[tuple[Version, Path]] = []

        for path in self.cache_dir.glob(
            f"{prefix}*"
        ):
            version_text = path.name.removeprefix(prefix)

            try:
                version = Version.parse(version_text)
            except ValueError:
                continue

            if version.matches_caret(self.min_version):
                cached_templates.append(
                    (version, path)
                )

        if cached_templates:
            cached_templates.sort(
                key=lambda item: item[0],
                reverse=True,
            )

            return cached_templates[0][1]

        raise TemplateUnavailableError(
            "Failed to reach GitHub and no compatible "
            "local template is available."
        )
    
