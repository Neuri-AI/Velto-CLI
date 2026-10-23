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


def test_authenticated_child_can_request_native_restart(tmp_path, monkeypatch):
    from velto_cli.adapters.process import runners
    prepare = runners._prepared_psx_entry_point
    preparations = []
    def tracked(source):
        preparations.append(source)
        return prepare(source)
    monkeypatch.setattr(runners, "_prepared_psx_entry_point", tracked)
    import os
    script = tmp_path / "child.py"
    script.write_text("""
import json
import os
from pathlib import Path
import socket
import time

count = Path("launches")
generation = int(count.read_text()) if count.exists() else 0
count.write_text(str(generation + 1))
if generation == 0:
    host, port = os.environ["VELTO_RELOAD_ENDPOINT"].split(":")
    with socket.create_connection((host, int(port)), timeout=3) as connection:
        connection.sendall((os.environ["VELTO_RELOAD_TOKEN"] + "\\n").encode())
        time.sleep(.15)
        connection.sendall((json.dumps({
            "version": 1, "kind": "restart_request",
            "reason": "loaded_native_extension_changed"
        }) + "\\n").encode())
        while True:
            time.sleep(.05)
else:
    Path("restarted").write_text("yes")
""", encoding="utf-8")
    coordinator = DevelopmentCoordinator(tmp_path, debounce=.05)
    assert coordinator.run([sys.executable, str(script)], dict(os.environ), authored_entry=str(script)) == 0
    assert (tmp_path / "launches").read_text() == "2"
    assert (tmp_path / "restarted").exists()
    assert preparations == [str(script), str(script)]


def test_restart_prepares_snapshot_before_replacement_child(tmp_path):
    import os
    child = tmp_path / "child.py"
    child.write_text("""
import json
import os
from pathlib import Path
import socket
import time

state_path = Path(os.environ["VELTO_RESTART_STATE_PATH"])
counter = Path("generations")
n = int(counter.read_text()) if counter.exists() else 0
counter.write_text(str(n + 1))
if n:
    Path("restored").write_text(state_path.read_text())
else:
    host, port = os.environ["VELTO_RELOAD_ENDPOINT"].split(":")
    with socket.create_connection((host, int(port)), timeout=3) as conn:
        conn.sendall((os.environ["VELTO_RELOAD_TOKEN"] + "\\n").encode())
        time.sleep(.15)
        conn.sendall((json.dumps({
            "version": 1, "kind": "restart_request",
            "reason": "loaded_native_extension_changed"
        }) + "\\n").encode())
        message = b""
        while b"\\n" not in message:
            message += conn.recv(4096)
        assert json.loads(message.split(b"\\n")[0])["kind"] == "prepare_restart"
        state_path.write_text('{"version":1,"store":{"version":1,"state":{"count":42}}}')
        conn.sendall((json.dumps({"version": 1, "kind": "restart_ready"}) + "\\n").encode())
        while True:
            time.sleep(.05)
""", encoding="utf-8")
    coordinator = DevelopmentCoordinator(tmp_path, debounce=.05)
    assert coordinator.run([sys.executable, str(child)], dict(os.environ)) == 0
    assert (tmp_path / "generations").read_text() == "2"
    assert json.loads((tmp_path / "restored").read_text())["store"]["state"]["count"] == 42


def test_only_effective_development_backend_triggers_restart(tmp_path):
    import sys
    settings = tmp_path / "settings"
    settings.mkdir()
    base = settings / "base.json"
    base.write_text('{"framework":"pyside6"}', encoding="utf-8")
    development = settings / "development.json"
    development.write_text('{"hot_reload":{"enabled":true}}', encoding="utf-8")
    coordinator = DevelopmentCoordinator(tmp_path)
    development.write_text('{"hot_reload":{"enabled":false}}', encoding="utf-8")
    assert not coordinator._classify_change({development})
    unused = settings / ("windows.json" if sys.platform != "win32" else "linux.json")
    unused.write_text('{"framework":"kivy"}', encoding="utf-8")
    assert not coordinator._classify_change({unused})
    development.write_text('{"framework":"tkinter"}', encoding="utf-8")
    assert coordinator._classify_change({development})
    assert not coordinator._classify_change({development})

def test_failed_boot_recovers_on_subsequent_source_save(tmp_path):
    import os
    from pathlib import Path
    child = tmp_path / "startup.py"
    child.write_text("from pathlib import Path\n"                     "count = Path(\'boot_count\')\n"                     "n = int(count.read_text()) if count.exists() else 0\n"                     "count.write_text(str(n + 1))\n"                     "raise SystemExit(5 if n == 0 else 0)\n", encoding="utf-8")
    coordinator = DevelopmentCoordinator(tmp_path, debounce=0.05)
    observed = []
    worker = threading.Thread(
        target=lambda: observed.append(coordinator.run([sys.executable, str(child)], dict(os.environ))),
        daemon=True,
    )
    worker.start()
    deadline = time.monotonic() + 8
    while not (tmp_path / "boot_count").exists() and time.monotonic() < deadline:
        time.sleep(0.02)
    assert (tmp_path / "boot_count").exists()
    time.sleep(0.25)
    (tmp_path / "corrected.py").write_text("updated = True", encoding="utf-8")
    worker.join(timeout=10)
    assert not worker.is_alive()
    assert observed == [0]
    assert (tmp_path / "boot_count").read_text() == "2"


def test_restart_kills_child_process_group_descendants(tmp_path):
    import os
    import subprocess
    import pytest
    if os.name == "nt":
        pytest.skip("POSIX process-group regression")
    script = tmp_path / "parent.py"
    script.write_text(
        "import subprocess,sys,time\n"
        "from pathlib import Path\n"
        "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
        "Path('worker_pid').write_text(str(child.pid))\n"
        "time.sleep(60)\n", encoding="utf-8",
    )
    process = subprocess.Popen(
        [sys.executable, str(script)], cwd=str(tmp_path), start_new_session=True,
    )
    try:
        deadline = time.monotonic() + 5
        while not (tmp_path / "worker_pid").exists() and time.monotonic() < deadline:
            time.sleep(.02)
        assert (tmp_path / "worker_pid").exists()
        pid = int((tmp_path / "worker_pid").read_text())
        DevelopmentCoordinator._stop_process_tree(process)
        import signal
        with pytest.raises(ProcessLookupError):
            os.killpg(process.pid, 0)
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()
