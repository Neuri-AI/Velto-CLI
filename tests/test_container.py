from pathlib import Path

from velto_cli.container import (
    Container,
    get_container,
    set_container,
)
from velto_cli.adapters.cli.console_ui import RichConsoleUI
from velto_cli.adapters.package.metadata import PackageMetadata
from velto_cli.adapters.persistence.storage import (
    OsFileSystem,
    SettingsRepository,
)
from velto_cli.adapters.process.runners import (
    ImportlibModuleRegistry,
    SubprocessAppRunner,
)
from velto_cli.adapters.templates.fallback import FallbackTemplateProvider
from velto_cli.application.use_cases.init import InitProjectUseCase
from velto_cli.application.use_cases.version import ShowVersionUseCase


def test_container_creates_adapters():
    container = Container()

    assert isinstance(container.ui, RichConsoleUI)
    assert isinstance(container.fs, OsFileSystem)
    assert isinstance(container.settings, SettingsRepository)
    assert isinstance(container.app_runner, SubprocessAppRunner)
    assert isinstance(container.modules, ImportlibModuleRegistry)
    assert isinstance(container.metadata, PackageMetadata)


def test_container_creates_template_provider():
    container = Container()

    assert isinstance(
        container.template_provider,
        FallbackTemplateProvider,
    )


def test_container_creates_init_use_case():
    container = Container()

    assert isinstance(
        container.init_project_use_case,
        InitProjectUseCase,
    )


def test_container_creates_version_use_case():
    container = Container()

    assert isinstance(
        container.show_version_use_case,
        ShowVersionUseCase,
    )


def test_init_use_case_receives_container_dependencies():
    container = Container()
    use_case = container.init_project_use_case

    assert use_case.ui is container.ui
    assert use_case.fs is container.fs
    assert use_case.dependencies is not None
    assert use_case.templates is container.template_provider
    assert use_case.settings is container.settings


def test_version_use_case_receives_container_dependencies():
    container = Container()
    use_case = container.show_version_use_case

    assert use_case.ui is container.ui
    assert use_case.metadata is container.metadata


def test_get_container_returns_singleton(monkeypatch):
    container = Container()

    monkeypatch.setattr(
        "velto_cli.container._container",
        container,
    )

    assert get_container() is container
    assert get_container() is container


def test_get_container_creates_container_when_missing(monkeypatch):
    monkeypatch.setattr(
        "velto_cli.container._container",
        None,
    )

    container = get_container()

    assert isinstance(container, Container)
    assert get_container() is container


def test_set_container_replaces_container(monkeypatch):
    first = Container()
    second = Container()

    monkeypatch.setattr(
        "velto_cli.container._container",
        first,
    )

    set_container(second)

    assert get_container() is second


def test_set_container_accepts_test_double(monkeypatch):
    fake_container = object()

    set_container(fake_container)

    assert get_container() is fake_container
