"""Source-process development environment contract."""
import sys
from pathlib import Path
from unittest.mock import Mock

from velto_cli.adapters.process.runners import SubprocessAppRunner


def test_source_runner_sets_development_flag(tmp_path, monkeypatch):
    source = tmp_path / "main.py"
    source.write_text("print('ready')", encoding="utf-8")
    recorded = Mock(returncode=0)
    run = Mock(return_value=recorded)
    monkeypatch.setattr("velto_cli.adapters.process.runners.subprocess.run", run)
    result = SubprocessAppRunner().run_from_source(str(source), str(tmp_path))
    assert result == 0
    assert run.call_args.kwargs["env"]["VELTO_DEVELOPMENT"] == "1"
    assert run.call_args.kwargs["env"].get("PSX_HOT_RELOAD") != "0"
    assert run.call_args.kwargs["cwd"] == str(tmp_path)


def test_source_runner_disable_switch_is_forwarded_to_child(tmp_path, monkeypatch):
    source = tmp_path / "main.py"
    source.write_text("print('ready')", encoding="utf-8")
    run = Mock(return_value=Mock(returncode=0))
    monkeypatch.setattr("velto_cli.adapters.process.runners.subprocess.run", run)
    assert SubprocessAppRunner().run_from_source(
        str(source), str(tmp_path), disable_hot_reload=True) == 0
    assert run.call_args.kwargs["env"]["PSX_HOT_RELOAD"] == "0"
