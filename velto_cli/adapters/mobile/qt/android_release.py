"""Fail-closed source-minimized Android release APK finalization.

This is bytecode packaging, NOT encryption or protection against decompilers.
Only application-owned Python modules and the staged Velto runtime are
transformed. Never rewrite python-for-android's interpreter/stdlib.
"""
from __future__ import annotations

import hashlib
import importlib._bootstrap_external as _bootstrap_external
import io
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
from zipfile import ZipFile

from velto_cli.adapters.mobile.qt.android_final_apk import (
    _amended_private_tar, _android_sdk_tool, replace_android_private_version,
)
from velto_cli.adapters.mobile.qt.android_sources import PACKAGES


def _owned(name: str, root: Path) -> bool:
    """Restrict transformation to known project paths and framework packages."""
    parts = PurePosixPath(name).parts
    if not parts or ".." in parts or parts[0].startswith("."):
        return False
    if len(parts) == 1:
        return name == "main.py" or (name.endswith(".py") and (root / name).is_file())
    first = parts[0]
    if first in PACKAGES:
        return True
    return (
        first not in {"build", ".buildozer", "deployment", "tests", "test"}
        and (root / first).is_dir()
        and (root / first / parts[1]).exists()
    )


def _compile_bytecode(source: bytes, name: str) -> bytes:
    code = compile(source, name, "exec", optimize=2, dont_inherit=True)
    # Sourceless imports recognize module.pyc and package/__init__.pyc.
    return _bootstrap_external._code_to_timestamp_pyc(
        code, mtime=0, source_size=len(source)
    )


def _bootstrap() -> bytes:
    # p4a launches main.py. Keep only a small bootstrap, not the app source.
    return (
        b"from pathlib import Path\n"
        b"import runpy\n"
        b"runpy.run_path(str(Path(__file__).with_suffix('.pyc')), run_name='__main__')\n"
    )


def _release_credentials() -> tuple[Path, str, str, str]:
    required = (
        "VELTO_ANDROID_KEYSTORE", "VELTO_ANDROID_KEY_ALIAS",
        "VELTO_ANDROID_KEYSTORE_PASSWORD", "VELTO_ANDROID_KEY_PASSWORD",
    )
    missing = [name for name in required if not os.environ.get(name)]
    if missing:
        raise RuntimeError(
            "Protected Android release requires signing configuration: "
            + ", ".join(missing)
        )
    store = Path(os.environ[required[0]]).expanduser().resolve()
    if not store.is_file():
        raise RuntimeError(f"Android release keystore does not exist: {store}")
    return (store, *(os.environ[name] for name in required[1:]))


def _private_entry(name: str) -> bool:
    return name in ("assets/private.tar", "assets/private.mp3")


def protect_android_release_apk(apk: Path, root: Path) -> Path:
    """Rewrite source files into same-version sourceless bytecode and re-sign.

    The caller must supply a real release APK. Signatures and bytecode format
    are verified before publishing; failures do not mutate the input artifact.
    """
    if apk.suffix.lower() != ".apk" or "debug" in apk.stem.lower():
        raise RuntimeError(
            "Protected Android release requires a release APK, not a debug APK/AAB."
        )
    if sys.version_info[:2] != (3, 12):
        # Target Python must be proven, not assumed. Current Qt/p4a profile
        # is certified against CPython 3.12 only.
        raise RuntimeError(
            "Protected Android release bytecode is currently certified for "
            "Python 3.12 builds only; refusing cross-version bytecode."
        )
    store, alias, store_password, key_password = _release_credentials()
    with ZipFile(apk) as archive:
        names = [name for name in archive.namelist() if _private_entry(name)]
        if len(names) != 1 or "resources.arsc" not in archive.namelist():
            raise RuntimeError("Android release APK lacks an unambiguous private archive")
        private_name = names[0]
        original_private = archive.read(private_name)
        from velto_cli.adapters.mobile.qt.android_final_apk import android_private_version_marker
        old_marker = android_private_version_marker(archive.read("resources.arsc"))
        if old_marker is None:
            raise RuntimeError(
                "Cannot update Android private_version after protecting release sources"
            )

    with tempfile.TemporaryDirectory(prefix="velto-release-") as td:
        temp = Path(td)
        compiled: dict[str, Path] = {}
        protected: set[str] = set()
        with tarfile.open(fileobj=io.BytesIO(original_private), mode="r:*") as tar:
            members = [m for m in tar.getmembers() if m.isfile()]
            for member in members:
                name = member.name.removeprefix("./").strip("/")
                if not name.endswith(".py") or not _owned(name, root):
                    continue
                file = tar.extractfile(member)
                if file is None:
                    raise RuntimeError(f"Cannot read Android release source: {name}")
                payload = file.read()
                target = temp / "compiled" / (name + "c")
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(_compile_bytecode(payload, name))
                compiled[name + "c"] = target
                protected.add(name)
        if "main.py" not in protected:
            raise RuntimeError("Android private archive is missing project main.py")
        source_stub = temp / "main.py"
        source_stub.write_bytes(_bootstrap())
        private = _amended_private_tar(
            original_private,
            {**compiled, "main.py": source_stub},
            remove=frozenset(protected - {"main.py"}),
            replace={"main.py": source_stub},
        )
        marker = hashlib.sha1(original_private + private).hexdigest()
        unsigned = temp / "unsigned.apk"
        aligned = temp / "aligned.apk"
        signed = temp / "signed.apk"
        with ZipFile(apk) as original, ZipFile(unsigned, "w") as output:
            for entry in original.infolist():
                name = entry.filename
                if name.startswith("META-INF/") and name.upper().endswith(
                    (".RSA", ".DSA", ".EC", ".SF")
                ):
                    continue
                if name == private_name:
                    data = private
                elif name == "resources.arsc":
                    data = replace_android_private_version(original.read(name), marker)
                else:
                    data = original.read(name)
                output.writestr(entry, data)
        subprocess.run(
            [str(_android_sdk_tool("zipalign")), "-f", "-p", "4",
             str(unsigned), str(aligned)],
            check=True, capture_output=True, text=True,
        )
        # Credentials are passed to apksigner through temporary files rather
        # than command-line arguments to avoid appearing in process listings.
        store_pass = temp / "store.pass"
        key_pass = temp / "key.pass"
        for path, secret in ((store_pass, store_password), (key_pass, key_password)):
            path.write_text(secret, encoding="utf-8")
            path.chmod(0o600)
        subprocess.run(
            [str(_android_sdk_tool("apksigner")), "sign",
             "--ks", str(store), "--ks-key-alias", alias,
             "--ks-pass", f"file:{store_pass}", "--key-pass", f"file:{key_pass}",
             "--out", str(signed), str(aligned)],
            check=True, capture_output=True, text=True,
        )
        subprocess.run(
            [str(_android_sdk_tool("apksigner")), "verify", "--verbose",
             str(signed)],
            check=True, capture_output=True, text=True,
        )
        verify_android_release_source_protection(signed, root)
        destination = apk.with_name(apk.name + ".velto-protected")
        try:
            shutil.copy2(signed, destination)
            os.replace(destination, apk)
        finally:
            destination.unlink(missing_ok=True)
    return apk


def verify_android_release_source_protection(apk: Path, root: Path) -> None:
    """Fail if application/framework Python source survived in an APK."""
    with ZipFile(apk) as archive:
        names = [name for name in archive.namelist() if _private_entry(name)]
        if len(names) != 1:
            raise RuntimeError("Protected Android APK private archive not found")
        with tarfile.open(fileobj=io.BytesIO(archive.read(names[0])), mode="r:*") as tar:
            items = {
                m.name.removeprefix("./").strip("/")
                for m in tar.getmembers() if m.isfile()
            }
            if "main.pyc" not in items or "main.py" not in items:
                raise RuntimeError("Protected release has no Python entry/bootstrap")
            exposed = sorted(
                name for name in items
                if name != "main.py" and name.endswith(".py") and _owned(name, root)
            )
            if exposed:
                raise RuntimeError(
                    "Protected release exposes application Python sources: "
                    + ", ".join(exposed[:12])
                )
            bootstrap = tar.extractfile("main.py")
            if bootstrap is None or bootstrap.read() != _bootstrap():
                raise RuntimeError("Protected release contains an unverified main.py")
