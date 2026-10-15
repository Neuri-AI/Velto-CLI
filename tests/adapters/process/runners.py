import os
import subprocess
import sys
from types import ModuleType, SimpleNamespace
from unittest import mock

import pytest

from qyro_cli.adapters.process.runners import (
    ImportlibModuleRegistry,
    QyroUploader,
    SubprocessAppRunner,
    UnittestRunner,
)


class TestImportlibModuleRegistry:
    def test_is_installed_returns_true_for_installed_module(self):
        registry = ImportlibModuleRegistry()

        assert registry.is_installed("json") is True

    def test_is_installed_returns_false_for_missing_module(self):
        registry = ImportlibModuleRegistry()

        assert registry.is_installed("module_that_does_not_exist") is False

    def test_is_installed_returns_false_when_find_spec_raises_import_error(
        self,
    ):
        registry = ImportlibModuleRegistry()

        with mock.patch(
            "qyro_cli.adapters.process.runners.find_spec",
            side_effect=ImportError,
        ):
            assert registry.is_installed("example") is False

    def test_is_installed_returns_false_when_find_spec_raises_value_error(
        self,
    ):
        registry = ImportlibModuleRegistry()

        with mock.patch(
            "qyro_cli.adapters.process.runners.find_spec",
            side_effect=ValueError,
        ):
            assert registry.is_installed("example") is False

class TestSubprocessAppRunner:
    def test_run_from_source_prepares_literal_psx_markup_automatically(self, tmp_path, monkeypatch):
        source = tmp_path / "main.py"
        source.write_text('node = psx("<Text>{title}</Text>")\n', encoding="utf-8")
        transformed = SimpleNamespace(source="print('generated')\n", transformed_calls=1)
        transform_module = ModuleType("psx.markup.transform")
        transform_module.transform_source = lambda text, filename: transformed
        psx_module = ModuleType("psx")
        psx_module.__path__ = []
        markup_module = ModuleType("psx.markup")
        markup_module.__path__ = []
        monkeypatch.setitem(sys.modules, "psx", psx_module)
        monkeypatch.setitem(sys.modules, "psx.markup", markup_module)
        monkeypatch.setitem(sys.modules, "psx.markup.transform", transform_module)
        observed: dict[str, object] = {}

        def run(command, **kwargs):
            generated = command[1]
            observed["path"] = generated
            observed["source"] = open(generated, encoding="utf-8").read()
            return mock.Mock(returncode=0)

        with mock.patch("qyro_cli.adapters.process.runners.subprocess.run", side_effect=run):
            result = SubprocessAppRunner().run_from_source(str(source), str(tmp_path))

        assert result == 0
        assert observed["path"] != str(source)
        assert observed["source"] == "print('generated')\n"

    def test_run_from_source_sets_pythonpath(self, monkeypatch):
        runner = SubprocessAppRunner()
        monkeypatch.setenv("PYTHONPATH", "existing/path")

        with mock.patch(
            "qyro_cli.adapters.process.runners.subprocess.run"
        ) as run:
            run.return_value.returncode = 0

            result = runner.run_from_source(
                "src/main.py",
                "src",
            )

        assert result == 0

        run.assert_called_once()
        args, kwargs = run.call_args

        assert args[0] == [sys.executable, "src/main.py"]
        assert kwargs["env"]["PYTHONPATH"] == (
            "src" + os.pathsep + "existing/path"
        )

    def test_run_from_source_sets_pythonpath_when_none_exists(
        self,
        monkeypatch,
    ):
        runner = SubprocessAppRunner()
        monkeypatch.delenv("PYTHONPATH", raising=False)

        with mock.patch(
            "qyro_cli.adapters.process.runners.subprocess.run"
        ) as run:
            run.return_value.returncode = 7

            result = runner.run_from_source(
                "main.py",
                "src",
            )

        assert result == 7

        kwargs = run.call_args.kwargs
        assert kwargs["env"]["PYTHONPATH"] == "src"

    def test_run_from_source_returns_process_exit_code(self):
        runner = SubprocessAppRunner()

        with mock.patch(
            "qyro_cli.adapters.process.runners.subprocess.run"
        ) as run:
            run.return_value.returncode = 42

            result = runner.run_from_source(
                "main.py",
                "src",
            )

        assert result == 42


class TestUnittestRunner:
    def test_run_returns_false_when_test_directory_does_not_exist(
        self,
        tmp_path,
    ):
        runner = UnittestRunner()

        result = runner.run(
            str(tmp_path),
            [str(tmp_path / "tests")],
        )

        assert result is False

    def test_run_returns_false_when_no_tests_are_discovered(
        self,
        tmp_path,
    ):
        test_dir = tmp_path / "tests"
        test_dir.mkdir()

        runner = UnittestRunner()

        with mock.patch(
            "qyro_cli.adapters.process.runners.defaultTestLoader.discover"
        ) as discover:
            discover.return_value = mock.Mock()
            discover.return_value.__iter__ = mock.Mock(
                return_value=iter(())
            )

            result = runner.run(
                str(tmp_path),
                [str(test_dir)],
            )

        assert result is False

    def test_run_discovers_tests_and_runs_suite(self, tmp_path):
        test_dir = tmp_path / "tests"
        test_package = test_dir / "sample"
        test_package.mkdir(parents=True)
        (test_package / "__init__.py").write_text("")

        runner = UnittestRunner()

        discovered_test = mock.Mock()

        with mock.patch(
            "qyro_cli.adapters.process.runners.defaultTestLoader.discover",
            return_value=discovered_test,
        ) as discover, mock.patch(
            "qyro_cli.adapters.process.runners.TextTestRunner.run"
        ) as test_runner_run:
            result = runner.run(
                str(tmp_path),
                [str(test_dir)],
            )

        assert result is True

        discover.assert_called_once_with(
            "sample",
            top_level_dir=str(test_dir),
        )
        test_runner_run.assert_called_once()

    def test_run_adds_source_root_to_sys_path(
        self,
        tmp_path,
        monkeypatch,
    ):
        runner = UnittestRunner()

        original_path = list(sys.path)

        try:
            runner.run(
                str(tmp_path),
                [str(tmp_path / "missing")],
            )

            assert str(tmp_path) in sys.path
        finally:
            sys.path[:] = original_path

    def test_run_adds_test_directories_to_sys_path(
        self,
        tmp_path,
    ):
        test_dir = tmp_path / "tests"
        test_dir.mkdir()

        runner = UnittestRunner()

        original_path = list(sys.path)

        try:
            runner.run(
                str(tmp_path),
                [str(test_dir)],
            )

            assert str(test_dir) in sys.path
        finally:
            sys.path[:] = original_path


class TestQyroUploader:
    def test_upload_repository_calls_upload_repo(self):
        uploader = QyroUploader()

        upload_repo = mock.Mock()

        fake_module = mock.Mock()
        fake_module._upload_repo = upload_repo

        with mock.patch.dict(
            sys.modules,
            {"qyro.upload": fake_module},
        ):
            uploader.upload_repository(
                "username",
                "password",
            )

        upload_repo.assert_called_once_with(
            "username",
            "password",
        )
