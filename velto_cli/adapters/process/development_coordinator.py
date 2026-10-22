"""Coordinate source changes with one development child process."""
from __future__ import annotations

import json
import os
from pathlib import Path
import secrets
import socket
import subprocess
import threading
import time


IGNORED = frozenset({".git", ".venv", "__pycache__", "build", "dist", "venv"})


def snapshot(root: Path) -> dict[Path, tuple[int, int]]:
    """Return a bounded-to-project snapshot, excluding generated artifacts."""
    found = {}
    for folder, subdirs, files in os.walk(root):
        subdirs[:] = [name for name in subdirs if name not in IGNORED]
        for name in files:
            if Path(name).suffix not in {".py", ".css", ".psx"}:
                continue
            path = Path(folder, name).resolve()
            try:
                path.relative_to(root)
                stat = path.stat()
            except (OSError, ValueError):
                continue
            found[path] = stat.st_mtime_ns, stat.st_size
    return found


class DevelopmentCoordinator:
    """The CLI alone supervises a watcher; the child owns the native event loop."""

    def __init__(self, root: Path, debounce: float = 0.15):
        self.root = root.resolve()
        self.debounce = debounce
        self._stop = threading.Event()
        self._connection = None
        self._lock = threading.Lock()
        self._revision = 0

    def _notify(self, paths):
        self._revision += 1
        envelope = {"version": 1, "kind": "change",
                    "request_id": str(self._revision),
                    "paths": [str(path.relative_to(self.root)) for path in sorted(paths)]}
        with self._lock:
            if self._connection is not None:
                try:
                    self._connection.sendall((json.dumps(envelope) + "\n").encode())
                except OSError:
                    self._connection = None
