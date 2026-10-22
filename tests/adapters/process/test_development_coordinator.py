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


def test_backend_change_requests_restart_but_ordinary_edit_does_not(tmp_path):
    settings = tmp_path / "settings"
    settings.mkdir()
    base = settings / "base.json"
    base.write_text('{"framework": "pyside6"}', encoding="utf-8")
    coordinator = DevelopmentCoordinator(tmp_path)
    assert not coordinator._classify_change({tmp_path / "main.py"})
    base.write_text('{"framework": "tkinter"}', encoding="utf-8")
    assert coordinator._classify_change({base})
    assert not coordinator._classify_change({base})
    base.write_text('{"framework": ', encoding="utf-8")
    assert not coordinator._classify_change({base})
    base.write_text('{"framework": "kivy"}', encoding="utf-8")
    assert coordinator._classify_change({base})


def test_restart_replaces_child_and_retains_single_watcher(tmp_path):
    import os
    settings = tmp_path / "settings"
    settings.mkdir()
    base = settings / "base.json"
    base.write_text('{"framework": "pyside6"}', encoding="utf-8")
    program = tmp_path / "child.py"
    program.write_text(
        "from pathlib import Path\n"
        "import os, time\n"
        "p = Path('launches')\n"
        "n = int(p.read_text()) if p.exists() else 0\n"
        "p.write_text(str(n + 1))\n"
        "if n == 0:\n"
        "    while True: time.sleep(.05)\n"
        "else:\n"
        "    Path('replacement_ready').write_text('yes')\n",
        encoding="utf-8",
    )
    supervisor = DevelopmentCoordinator(tmp_path, debounce=0.05)
    result = []
    thread = threading.Thread(
        target=lambda: result.append(supervisor.run([sys.executable, str(program)], dict(os.environ))),
        daemon=True,
    )
    thread.start()
    deadline = time.monotonic() + 8
    while not (tmp_path / "launches").exists() and time.monotonic() < deadline:
        time.sleep(.02)
    assert (tmp_path / "launches").exists()
    time.sleep(.25)
    base.write_text('{"framework": "tkinter"}', encoding="utf-8")
    thread.join(timeout=10)
    assert not thread.is_alive()
    assert result == [0]
    assert (tmp_path / "launches").read_text() == "2"
    assert (tmp_path / "replacement_ready").exists()
