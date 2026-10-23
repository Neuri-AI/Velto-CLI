"""Coordinate source changes with one development child process."""
from __future__ import annotations

import json
import fnmatch
import os
from pathlib import Path
import secrets
import socket
import sys
import signal
import subprocess
import threading
import time
import tempfile
from contextlib import ExitStack


IGNORED = frozenset({".git", ".venv", "__pycache__", "build", "dist", "venv"})


def snapshot(root: Path, *, watch=('.',), ignore=()) -> dict[Path, tuple[int, int]]:
    """Return a bounded-to-project snapshot, excluding generated artifacts."""
    found = {}
    for folder, subdirs, files in os.walk(root):
        subdirs[:] = [name for name in subdirs if name not in IGNORED]
        for name in files:
            if Path(name).suffix.lower() not in {'.py', '.css', '.psx', '.json', '.so', '.pyd', '.dll', '.dylib'}:
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
        self._restart = threading.Event()
        self._state_ready = threading.Event()
        self._runtime_identity = self._read_runtime_identity()
        self._source_changed = threading.Event()

    def _read_runtime_identity(self):
        """Resolve the effective backend from the active development profiles."""
        platform = ("windows.json" if sys.platform == "win32" else
                    "mac.json" if sys.platform == "darwin" else "linux.json")
        effective = {}
        for name in ("base.json", platform, "development.json"):
            path = self.root / "settings" / name
            if not path.is_file():
                continue
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (ValueError, OSError):
                # An invalid save never forces a restart of the working app.
                return None
            if not isinstance(data, dict):
                return None
            for key in ("framework", "binding"):
                if key in data:
                    effective[key] = data[key]
        return effective.get("framework") or effective.get("binding")

    def _classify_change(self, paths):
        """Restart only when the effective native event-loop backend changes."""
        if not any(path.parent == self.root / "settings" and
                   path.name in {"base.json", "windows.json", "linux.json",
                                 "mac.json", "development.json"}
                   for path in paths):
            return False
        current = self._read_runtime_identity()
        if current is None:
            return False
        previous = self._runtime_identity
        self._runtime_identity = current
        return previous is not None and current != previous

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
            current = snapshot(self.root, watch=self.watch, ignore=self.ignore)
            changed = set(previous) ^ set(current)
            changed.update(p for p in current.keys() & previous.keys()
                           if current[p] != previous[p])
            previous = current
            if changed:
                self._source_changed.set()
                pending.update(changed)
                last = time.monotonic()
            if pending and last is not None and time.monotonic() - last >= self.debounce:
                if self._classify_change(pending):
                    self._restart.set()
                else:
                    self._notify(pending)
                pending.clear()

    def _receive_requests(self, connection, generation_stop):
        """Accept bounded restart requests from the authenticated child."""
        data = bytearray()
        connection.settimeout(0.2)
        while not generation_stop.is_set() and not self._stop.is_set():
            try:
                chunk = connection.recv(4096)
            except socket.timeout:
                continue
            except OSError:
                return
            if not chunk:
                return
            data.extend(chunk)
            if len(data) > 65536:
                return
            while b"\n" in data:
                raw, _, remaining = data.partition(b"\n")
                data = bytearray(remaining)
                try:
                    message = json.loads(raw)
                except (TypeError, ValueError):
                    continue
                if (type(message) is dict and message.get("version") == 1
                        and message.get("kind") == "restart_ready"):
                    self._state_ready.set()
                    continue
                if (type(message) is dict and message.get("version") == 1
                        and message.get("kind") == "restart_request"
                        and message.get("reason") in {
                            "loaded_native_extension_changed",
                            "runtime_bootstrap_changed", "backend_changed",
                            "interpreter_changed", "unsafe_global_initialization",
                            "external_resource_incompatible",
                        }):
                    self._restart.set()
                    return

    @staticmethod
    def _stop_process_tree(child):
        """Stop a supervised process group, including its live descendants."""
        if os.name == "nt":
            if child.poll() is None:
                try:
                    subprocess.run(
                        ["taskkill", "/PID", str(child.pid), "/T", "/F"],
                        check=False, stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL, timeout=5,
                    )
                except (OSError, subprocess.TimeoutExpired):
                    pass
                if child.poll() is None:
                    child.kill()
            child.wait()
            return
        # The child starts in its own POSIX process group. Group cleanup
        # remains necessary even if its leader has already exited.
        try:
            os.killpg(child.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        try:
            child.wait(timeout=3)
        except subprocess.TimeoutExpired:
            pass
        try:
            os.killpg(child.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        child.wait()

    def run(self, command, environment, *, authored_entry=None):
        """Supervise successive process generations with exactly one watcher."""
        self._stop.clear()
        self._restart.clear()
        token = secrets.token_hex(24)
        restart_state = tempfile.TemporaryDirectory(prefix="velto-state-")
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server:
            server.bind(("127.0.0.1", 0))
            server.listen(1)
            server.settimeout(0.15)
            env = dict(environment)
            env["VELTO_RELOAD_ENDPOINT"] = "127.0.0.1:" + str(server.getsockname()[1])
            env["VELTO_RELOAD_TOKEN"] = token
            env["VELTO_RESTART_STATE_PATH"] = str(Path(restart_state.name) / "snapshot.json")
            watcher = threading.Thread(target=self._watch, daemon=True,
                                       name="velto-source-watcher")
            watcher.start()
            child = None
            try:
                while True:
                    self._restart.clear()
                    self._state_ready.clear()
                    self._source_changed.clear()
                    token = secrets.token_hex(24)
                    env["VELTO_RELOAD_TOKEN"] = token
                    with self._lock:
                        if self._connection is not None:
                            self._connection.close()
                            self._connection = None
                    with ExitStack() as generation:
                        generation_stop = threading.Event()
                        receiver_thread = None
                        active_command = command
                        if authored_entry is not None:
                            from .runners import _prepared_psx_entry_point
                            active_command = [*command[:-1], generation.enter_context(
                                _prepared_psx_entry_point(authored_entry)
                            )]
                        child = subprocess.Popen(active_command, env=env, cwd=str(self.root), start_new_session=(os.name != 'nt'), creationflags=(subprocess.CREATE_NEW_PROCESS_GROUP if os.name == 'nt' else 0))
                        while child.poll() is None and not self._restart.is_set():
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
                                receiver_thread = threading.Thread(
                                    target=self._receive_requests,
                                    args=(conn, generation_stop), daemon=True,
                                    name="velto-restart-requests",
                                )
                                receiver_thread.start()
                            except (OSError, UnicodeError):
                                conn.close()
                        if self._restart.is_set() and child.poll() is None:
                            with self._lock:
                                connection = self._connection
                                if connection is not None:
                                    try:
                                        connection.sendall(
                                            b'{"version":1,"kind":"prepare_restart"}\n'
                                        )
                                    except OSError:
                                        connection = None
                            if connection is not None:
                                self._state_ready.wait(timeout=1.5)
                        generation_stop.set()
                        if receiver_thread is not None:
                            receiver_thread.join(timeout=1)
                        if self._restart.is_set():
                            self._stop_process_tree(child)
                            continue
                        code = child.wait()
                        if code == 0:
                            return code
                        while not self._stop.is_set():
                            if self._source_changed.wait(0.2):
                                self._source_changed.clear()
                                break
                        else:
                            return code
                        continue
            except KeyboardInterrupt:
                if child is not None:
                    self._stop_process_tree(child)
                return 130
            finally:
                self._stop.set()
                watcher.join(timeout=3)
                with self._lock:
                    if self._connection is not None:
                        self._connection.close()
                        self._connection = None
                if child is not None:
                    self._stop_process_tree(child)
                restart_state.cleanup()
                if watcher.is_alive():
                    raise RuntimeError("Source watcher failed to stop")
