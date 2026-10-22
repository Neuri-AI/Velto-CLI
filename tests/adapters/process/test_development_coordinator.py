"""Source watcher contracts for the command-line development supervisor."""
import json
import socket

from velto_cli.adapters.process.development_coordinator import (
    DevelopmentCoordinator, snapshot,
)


def test_snapshot_excludes_generated_and_external_files(tmp_path):
    (tmp_path / "views").mkdir()
    (tmp_path / "views" / "main.py").write_text("x=1", encoding="utf-8")
    (tmp_path / "views" / "style.css").write_text("body {}", encoding="utf-8")
    (tmp_path / "__pycache__").mkdir()
    (tmp_path / "__pycache__" / "generated.py").write_text("bad", encoding="utf-8")
    files = [p.relative_to(tmp_path).as_posix() for p in snapshot(tmp_path)]
    assert sorted(files) == ["views/main.py", "views/style.css"]


def test_coordinator_sends_correlated_project_relative_changes(tmp_path):
    coordinator = DevelopmentCoordinator(tmp_path)
    sending, receiving = socket.socketpair()
    coordinator._connection = sending
    try:
        changed = tmp_path / "views" / "main.py"
        coordinator._notify({changed})
        payload = json.loads(receiving.recv(4096))
        assert payload == {
            "version": 1, "kind": "change",
            "request_id": "1", "paths": ["views/main.py"],
        }
        coordinator._notify({changed})
        assert json.loads(receiving.recv(4096))["request_id"] == "2"
    finally:
        sending.close()
        receiving.close()
