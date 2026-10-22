"""Source-process development environment contract."""

from velto_cli.adapters.process.runners import SubprocessAppRunner


def test_runner_exposes_source_execution_contract():
    assert callable(SubprocessAppRunner().run_from_source)
