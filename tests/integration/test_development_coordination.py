"""Integration between the CLI source watcher and Qyro's passive receiver."""
import json
import os
from pathlib import Path
import sys
import threading
import time

from velto_cli.adapters.process.development_coordinator import DevelopmentCoordinator


def test_qyro_receiver_gets_changes_without_touching_gui(tmp_path):
    program = tmp_path / "child.py"
    program.write_text("""
from pathlib import Path
import time
from qyro.client.development_changes import DevelopmentChangeReceiver

received = []
receiver = DevelopmentChangeReceiver(received.append)
receiver.start()
Path("connected").write_text("ready", encoding="utf-8")
deadline = time.monotonic() + 7
while not received and time.monotonic() < deadline:
    time.sleep(0.025)
Path("messages.json").write_text(__import__("json").dumps(received), encoding="utf-8")
receiver.stop()
raise SystemExit(0 if received else 3)
""", encoding="utf-8")
    coordinator = DevelopmentCoordinator(tmp_path, debounce=0.05)
    result = []
    thread = threading.Thread(
        target=lambda: result.append(coordinator.run([sys.executable, str(program)], dict(os.environ))),
        daemon=True,
    )
    thread.start()
    deadline = time.monotonic() + 6
    while not (tmp_path / "connected").exists() and time.monotonic() < deadline:
        time.sleep(0.02)
    assert (tmp_path / "connected").exists(), "Qyro child did not establish development transport"
    time.sleep(0.3)
    (tmp_path / "view.py").write_text("title = 'updated'", encoding="utf-8")
    thread.join(timeout=10)
    assert not thread.is_alive(), "Source coordinator failed to release the child"
    assert result == [0]
    messages = json.loads((tmp_path / "messages.json").read_text(encoding="utf-8"))
    assert any("view.py" in group for group in messages)
