import argparse
from unittest.mock import ANY, Mock

from velto_cli.cmdline.commands.bundle import BundleCommand


def test_configure_defines_bundle_arguments():
    parser = argparse.ArgumentParser()

    BundleCommand.configure(parser)

    args = parser.parse_args([])

    assert args.release_dir == "release"
    assert args.no_resources is False
    assert args.zip_archive is False
    assert args.platform == "auto"
    assert args.package_format == "auto"
    assert args.check is False


def test_execute_delegates_to_bundle_use_case(monkeypatch):
    use_case = Mock()
    container = Mock()
    container.bundle_release_use_case = use_case

    monkeypatch.setattr(
        "velto_cli.cmdline.commands.bundle.get_container",
        lambda: container,
    )

    args = argparse.Namespace(
        release_dir="release",
        no_resources=False,
        zip_archive=True,
        platform="mac",
        package_format="dmg",
    )

    result = BundleCommand.execute(args)

    assert result == 0
    use_case.execute.assert_called_once_with(
        project_root=ANY,
        release_dir="release",
        include_resources=True,
        create_archive=True,
        package_format="dmg",
        target_platform="mac",
    )


def test_execute_returns_one_on_error(monkeypatch):
    use_case = Mock()
    use_case.execute.side_effect = RuntimeError("boom")

    container = Mock()
    container.bundle_release_use_case = use_case
    container.ui = Mock()

    monkeypatch.setattr(
        "velto_cli.cmdline.commands.bundle.get_container",
        lambda: container,
    )

    args = argparse.Namespace(
        release_dir="release",
        no_resources=False,
        zip_archive=False,
        platform="auto",
        package_format="auto",
    )

    result = BundleCommand.execute(args)

    assert result == 1
    container.ui.error.assert_called_once_with("boom")


def test_execute_runs_check_mode(monkeypatch):
    use_case = Mock()
    container = Mock()
    container.bundle_release_use_case = use_case

    monkeypatch.setattr(
        "velto_cli.cmdline.commands.bundle.get_container",
        lambda: container,
    )

    args = argparse.Namespace(
        release_dir="release",
        no_resources=False,
        zip_archive=False,
        platform="mac",
        package_format="dmg",
        check=True,
    )

    result = BundleCommand.execute(args)

    assert result == 0
    use_case.check.assert_called_once_with(
        project_root=ANY,
        package_format="dmg",
        target_platform="mac",
    )
    use_case.execute.assert_not_called()
