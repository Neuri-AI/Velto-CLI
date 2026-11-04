"""Public Qt for Python Android wheel repository.

The official Qt archive distributes Android wheels over HTTPS without
requiring qtpip credentials. This adapter never resolves arbitrary URLs.
"""
from html.parser import HTMLParser
import os
from pathlib import Path
import re
import tempfile
from urllib.parse import urljoin
from urllib.request import urlopen
from zipfile import is_zipfile


_BASE = "https://download.qt.io/official_releases/QtForPython/"
_NAMES = ("pyside6", "shiboken6")


class _WheelLinks(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.names: set[str] = set()

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag != "a":
            return
        href = dict(attrs).get("href")
        if href and re.fullmatch(r"[A-Za-z0-9_.+-]+\.whl", href):
            self.names.add(href)


class QtAndroidWheelArchive:
    """Download a matching open-source PySide6/Shiboken6 wheel pair once."""

    @staticmethod
    def _valid(name: str, package: str, version: str, arch: str) -> bool:
        parts = name.split("-")
        return (
            len(parts) in (5, 6)
            and parts[0].lower().replace("_", "") == package
            and parts[1] == version
            and name.endswith(f"-android_{arch}.whl")
        )

    @classmethod
    def _find_name(cls, package: str, version: str, arch: str) -> str:
        listing_url = _BASE + package + "/"
        try:
            with urlopen(listing_url, timeout=30) as response:
                page = response.read(1_000_001)
        except (OSError, ValueError) as exc:
            raise RuntimeError(
                f"Cannot access official Qt for Python Android wheel index: "
                f"{listing_url}: {exc}"
            ) from exc
        if len(page) > 1_000_000:
            raise RuntimeError("Qt wheel index exceeds expected size")
        links = _WheelLinks()
        links.feed(page.decode("utf-8", errors="replace"))
        matches = sorted(
            name for name in links.names
            if cls._valid(name, package, version, arch)
        )
        if not matches:
            raise RuntimeError(
                f"No official open-source {package} Android wheel for Qt "
                f"{version} / {arch} in {listing_url}. "
                "Use --wheel-pyside and --wheel-shiboken with compatible "
                "wheels, or cross-compile Qt for Python for Android."
            )
        return matches[-1]

    @staticmethod
    def _download(package: str, name: str, cache: Path) -> Path:
        destination = cache / name
        if destination.is_file() and is_zipfile(destination):
            return destination
        cache.mkdir(parents=True, exist_ok=True)
        url = urljoin(_BASE + package + "/", name)
        # Do not trust redirects or wheel names outside the fixed Qt archive.
        fd, temp = tempfile.mkstemp(prefix=".qt-wheel-", suffix=".part", dir=cache)
        try:
            with os.fdopen(fd, "wb") as output:
                with urlopen(url, timeout=60) as response:
                    while chunk := response.read(1024 * 1024):
                        output.write(chunk)
            if not is_zipfile(temp):
                raise RuntimeError(f"Qt returned an invalid wheel archive for {name}")
            os.replace(temp, destination)
        finally:
            if os.path.exists(temp):
                os.unlink(temp)
        return destination

    def resolve(self, version: str, arch: str, cache: Path) -> tuple[Path, Path]:
        paths: list[Path] = []
        for package in _NAMES:
            # Cached valid wheels do not require a network request.
            cached = sorted(
                path for path in cache.glob("*.whl")
                if self._valid(path.name, package, version, arch)
                and is_zipfile(path)
            ) if cache.exists() else []
            if cached:
                paths.append(cached[-1])
                continue
            name = self._find_name(package, version, arch)
            paths.append(self._download(package, name, cache))
        return paths[0], paths[1]
