"""Coordinate source changes with one development child process."""
from __future__ import annotations

import json
import fnmatch
import os
from pathlib import Path
import secrets
import socket
import subprocess
import threading
import time


IGNORED = frozenset({".git", ".venv", "__pycache__", "build", "dist", "venv"})


def snapshot(root: Path, *, watch=('.',), ignore=()) -> dict[Path, tuple[int, int]]:
    """Return a bounded-to-project snapshot, excluding generated artifacts."""
    found = {}
    for folder, subdirs, files in os.walk(root):
        subdirs[:] = [name for name in subdirs if name not in IGNORED]
        for name in files:
            if Path(name).suffix.lower() not in {'.py', '.css', '.psx', '.json'}:
                continue
            path = Path(folder, name).resolve()
            try:
                relative = path.relative_to(root).as_posix()
                if any(fnmatch.fnmatch(relative, pattern) or relative.startswith(pattern.rstrip('/') + '/')
                       for pattern in ignore):
                    continue
                if not any(pattern in {'.', './'} or fnmatch.fnmatch(relative, pattern)
                           or relative.startswith(pattern.rstrip('/') + '/') for pattern in watch):
                    continue
                stat = path.stat()
            except (OSError, ValueError):
                continue
            found[path] = stat.st_mtime_ns, stat.st_size
    return found


class DevelopmentCoordinator:
    """The CLI alone supervises a watcher; the child owns the native event loop."""

    def __init__(self, root: Path, debounce: float = 0.15, *, watch=('.',), ignore=()):
        self.root = root.resolve()
        self.debounce = debounce
        self.watch = tuple(watch)
        self.ignore = tuple(ignore)
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
                    self._connection.close()
                    self._connection = None

    def _watch(self):
        previous = snapshot(self.root, watch=self.watch, ignore=self.ignore)
        pending = set()
        last = None
        while not self._stop.wait(0.1):
            current = snapshot(self.root)
            changed = set(previous) ^ set(current)
            changed.update(p for p in current.keys() & previous.keys()
                           if current[p] != previous[p])
            previous = current
            if changed:
                pending.update(changed)
                last = time.monotonic()
            if pending and last is not None and time.monotonic() - last >= self.debounce:
                self._notify(pending)
                pending.clear()

    def run(self, command, environment):
        """Start one child and authenticate its loopback notification channel."""
        self._stop.clear()
        token = secrets.token_hex(24)
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server:
            server.bind(("127.0.0.1", 0))
            server.listen(1)
            server.settimeout(0.15)
            env = dict(environment)
            env["VELTO_RELOAD_ENDPOINT"] = "127.0.0.1:" + str(server.getsockname()[1])
            env["VELTO_RELOAD_TOKEN"] = token
            child = subprocess.Popen(command, env=env, cwd=str(self.root))
            watcher = threading.Thread(target=self._watch, daemon=True,
                                       name="velto-source-watcher")
            watcher.start()
            try:
                while child.poll() is None:
                    try:
                        conn, _ = server.accept()
                    except socket.timeout:
                        continue
                    conn.settimeout(2)
                    try:
                        greeting = bytearray()
                        while b"\n" not in greeting and len(greeting) < 128:
                            part = conn.recv(128 - len(greeting))
                            if not part:
                                break
                            greeting.extend(part)
                        if greeting.partition(b"\n")[0].decode() != token:
                            conn.close()
                            continue
                        conn.settimeout(None)
                        with self._lock:
                            if self._connection is not None:
                                self._connection.close()
                            self._connection = conn
                    except (OSError, UnicodeError):
                        conn.close()
                return child.wait()
            except KeyboardInterrupt:
                child.terminate()
                child.wait()
                return 130
            finally:
                self._stop.set()
                watcher.join(timeout=3)
                if watcher.is_alive():
                    raise RuntimeError("Source watcher failed to stop")
                with self._lock:
                    if self._connection is not None:
                        self._connection.close()
                        self._connection = None
                if child.poll() is None:
                    child.terminate()
                    child.wait()
