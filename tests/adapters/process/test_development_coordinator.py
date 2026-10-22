"""Source watcher contracts for the command-line development supervisor."""
import json
import socket
import sys
import threading
import time

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


def test_authenticated_child_receives_debounced_file_change(tmp_path):
    child_script = tmp_path / "listener.py"
    child_script.write_text("""
import json
import os
from pathlib import Path
import socket

host, port = os.environ["VELTO_RELOAD_ENDPOINT"].split(":")
with socket.create_connection((host, int(port)), timeout=3) as conn:
    conn.sendall((os.environ["VELTO_RELOAD_TOKEN"] + "\\n").encode())
    Path("ready").write_text("yes")
    conn.settimeout(5)
    data = b""
    while b"\\n" not in data:
        data += conn.recv(4096)
    Path("received").write_bytes(data)
""", encoding="utf-8")
    coordinator = DevelopmentCoordinator(tmp_path, debounce=0.05)
    result = []
    thread = threading.Thread(target=lambda: result.append(
        coordinator.run([sys.executable, str(child_script)], dict(__import__("os").environ))),
        daemon=True)
    thread.start()
    ready = tmp_path / "ready"
    deadline = time.monotonic() + 6
    while not ready.exists() and time.monotonic() < deadline:
        time.sleep(0.02)
    assert ready.exists()
    time.sleep(0.3)
    (tmp_path / "updated.py").write_text("changed = True", encoding="utf-8")
    thread.join(timeout=8)
    assert not thread.is_alive()
    assert result == [0]
    payload = json.loads((tmp_path / "received").read_text(encoding="utf-8"))
    assert payload["kind"] == "change"
    assert "updated.py" in payload["paths"]
