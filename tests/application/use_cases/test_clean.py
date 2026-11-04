from unittest.mock import Mock

from velto_cli.application.use_cases.clean import CleanProjectUseCase


def make_use_case():
    ui = Mock()
    fs = Mock()
    settings = Mock()
    guards = Mock()

    use_case = CleanProjectUseCase(
        ui=ui,
        fs=fs,
        settings=settings,
        guards=guards,
    )

    return use_case, ui, fs, settings, guards


def test_execute_cleans_freeze_dir_by_default():
    use_case, ui, fs, settings, guards = make_use_case()

    settings.get_optional.return_value = "build"
    fs.exists.side_effect = lambda path: path == "build"

    use_case.execute(include_release=False)

    guards.require_existing_project.assert_called_once_with()
    settings.get_optional.assert_called_once_with("freeze_dir", "build")
    fs.remove_tree.assert_called_once_with("build")
    fs.empty_directory.assert_not_called()
    ui.success.assert_called_once()


def test_execute_cleans_release_when_flag_enabled():
    use_case, ui, fs, settings, _guards = make_use_case()

    settings.get_optional.return_value = "build"
    fs.exists.side_effect = lambda path: path in ("build", "release")

    use_case.execute(include_release=True)

    fs.remove_tree.assert_any_call("build")
    fs.remove_tree.assert_any_call("release")
    assert fs.remove_tree.call_count == 2
    ui.success.assert_called_once()


def test_execute_uses_empty_directory_when_remove_tree_fails():
    use_case, _ui, fs, settings, _guards = make_use_case()

    settings.get_optional.return_value = "build"
    fs.exists.return_value = True

    def remove_tree(path):
        raise OSError("mounted")

    fs.remove_tree.side_effect = remove_tree

    use_case.execute(include_release=False)

    fs.empty_directory.assert_called_once_with("build")


def test_execute_reports_nothing_to_clean():
    use_case, ui, fs, settings, _guards = make_use_case()

    settings.get_optional.return_value = "build"
    fs.exists.return_value = False

    use_case.execute(include_release=True)

    ui.info.assert_called_once_with("Nothing to clean.")
    ui.success.assert_not_called()
