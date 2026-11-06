"""Fail-closed source-minimized Android release APK finalization.

This is bytecode packaging, NOT encryption or protection against decompilers.
Only application-owned Python modules and the staged Velto runtime are
transformed. Never rewrite python-for-android's interpreter/stdlib.
"""
from __future__ import annotations

import ast
import hashlib
import io
import os
import re
from pathlib import Path, PurePosixPath
import shutil
import subprocess
import tarfile
import tempfile
from zipfile import ZipFile

from velto_cli.adapters.mobile.qt.android_final_apk import (
    _amended_private_tar, _android_sdk_tool, _debug_keystore,
    replace_android_private_version,
)
from velto_cli.adapters.mobile.qt.android_sources import PACKAGES
from velto_cli.adapters.mobile.qt.android_cython import (
    compile_android_extensions, output_name, _is_elf_arm64,
)
from velto_cli.adapters.mobile.qt.android_build_config import (
    load_android_signing, verify_android_exclusions,
)


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


def _prepare_cython_source(source: bytes, name: str) -> bytes:
    """Precompile project PSX on the build host; leave framework imports alone."""
    if name.startswith(tuple(package + "/" for package in PACKAGES)):
        return source
    from psx.markup.transform import transform_source
    # precompiled=True also sets the decorator marker correctly after
    # __future__ imports, without depending on inspect at Android runtime.
    prepared = transform_source(
        source.decode("utf-8"), filename=name, precompiled=True,
    ).source
    if name == "main.py":
        # p4a requires an executable main.py loader. The protected extension
        # imports as module "main", so preserve the application's original
        # __main__ guard when its native extension is imported by the loader.
        class EntryGuard(ast.NodeTransformer):
            def visit_Compare(self, node):
                self.generic_visit(node)
                if (
                    len(node.ops) == 1 and isinstance(node.ops[0], ast.Eq)
                    and isinstance(node.left, ast.Name)
                    and node.left.id == "__name__"
                    and len(node.comparators) == 1
                    and isinstance(node.comparators[0], ast.Constant)
                    and node.comparators[0].value == "__main__"
                ):
                    return ast.copy_location(ast.Constant(True), node)
                return node
        tree = ast.parse(prepared, filename=name)
        prepared = ast.unparse(ast.fix_missing_locations(EntryGuard().visit(tree)))
    return (prepared + "\n").encode("utf-8")


def _bootstrap() -> bytes:
    # The sole readable Python application entry required by p4a.
    # importlib prefers the compiled main.so extension over this source when
    # importing the same module name from the private application directory.
    return b"import importlib\nimportlib.import_module('main')\n"


def _release_credentials(root: Path | None = None) -> tuple[Path, str, str, str]:
    """Resolve signing from settings/sign.json android and optional CI overrides."""
    signing = load_android_signing(root) if root is not None else {}
    def value(env: str, field: str) -> str:
        provided = os.environ.get(env) or signing.get(field, "")
        if not isinstance(provided, str):
            raise ValueError(f"Android signing {field} must be a string")
        return provided
    store_value = value("VELTO_ANDROID_KEYSTORE", "keystore")
    alias = value("VELTO_ANDROID_KEY_ALIAS", "alias")
    store_password = value("VELTO_ANDROID_KEYSTORE_PASSWORD", "keystore_password")
    key_password = value("VELTO_ANDROID_KEY_PASSWORD", "key_password")
    if not all((store_value, alias, store_password, key_password)):
        raise RuntimeError(
            "Android Release signing requires signing.keystore, signing.alias, "
            "signing.keystore_password and signing.key_password in "
            "settings/sign.json under android or equivalent VELTO_ANDROID_* environment variables"
        )
    store = Path(store_value).expanduser()
    if not store.is_absolute():
        store = (root if root is not None else Path.cwd()) / store
    store = store.resolve()
    if not store.is_file():
        raise RuntimeError(f"Android Release keystore not found: {store}")
    return store, alias, store_password, key_password


def _private_entry(name: str) -> bool:
    return name in ("assets/private.tar", "assets/private.mp3")


def protect_android_apk(
    apk: Path, root: Path, *, debug: bool,
    toolchain_root: Path | None = None, obfuscate: bool = False,
) -> Path:
    """Cross-compile sources as Android ARM64 Cython extensions and re-sign.

    A real Android toolchain and target CPython headers are required. The
    original signed APK is untouched until every module is compiled, stripped
    when requested, and verified. No .pyc fallback exists.
    """
    if apk.suffix.lower() != ".apk" or (("debug" in apk.stem.lower()) != debug):
        raise RuntimeError(
            "Android source protection requires an APK matching the selected "
            "debug/release signing mode; AAB is not supported."
        )
    if toolchain_root is None:
        raise RuntimeError(
            "Cython Android protection requires a full deployment with retained "
            "python-for-android headers and NDK. Incremental .pyc fallback disabled."
        )
    if debug:
        store, alias, store_password, key_password = (
            _debug_keystore(), "androiddebugkey", "android", "android"
        )
    else:
        store, alias, store_password, key_password = _release_credentials(root)
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
        jobs: list[tuple[str, Path, Path]] = []
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
                target_name = output_name(name)
                target = temp / "compiled" / target_name
                target.parent.mkdir(parents=True, exist_ok=True)
                source_path = temp / "sources" / name
                source_path.parent.mkdir(parents=True, exist_ok=True)
                source_path.write_bytes(_prepare_cython_source(payload, name))
                jobs.append((name, source_path, target))
                compiled[target_name] = target
                protected.add(name)
        if "main.py" not in protected:
            raise RuntimeError("Android private archive is missing project main.py")
        compile_android_extensions(
            jobs, apk=apk, toolchain_root=toolchain_root,
            obfuscate=obfuscate,
        )
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
        verify_android_exclusions(signed, root)
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
            if "main.so" not in items or "main.py" not in items:
                raise RuntimeError("Protected release lacks Cython main.so and bootstrap")
            exposed = sorted(
                name for name in items
                if name != "main.py" and name.endswith(".py") and _owned(name, root)
            )
            if exposed:
                raise RuntimeError(
                    "Protected release exposes application Python sources: "
                    + ", ".join(exposed[:12])
                )
            forbidden_pyc = sorted(
                name for name in items if name.endswith((".pyc", ".pyo"))
                and (_owned(name[:-1], root) or name == "main.pyc")
            )
            if forbidden_pyc:
                raise RuntimeError("Protected APK contains .pyc application modules: "
                                   + ", ".join(forbidden_pyc[:12]))
            # A renamed macOS/Linux .so is not an Android extension. Validate
            # every protected native module, not just the bootstrap.
            for member in tar.getmembers():
                name = member.name.removeprefix("./").strip("/")
                if not member.isfile() or not name.endswith(".so"):
                    continue
                owned_py = name[:-3] + ".py"
                if not _owned(owned_py, root):
                    continue
                payload = tar.extractfile(member)
                if payload is None or not _is_elf_arm64(payload.read(32)):
                    raise RuntimeError(
                        "Protected APK contains non-Android Cython extension: " + name
                    )
            bootstrap = tar.extractfile("main.py")
            if bootstrap is None or bootstrap.read() != _bootstrap():
                raise RuntimeError("Protected release contains an unverified main.py")


def protect_android_release_apk(
    apk: Path, root: Path, *, toolchain_root: Path | None = None,
    obfuscate: bool = False,
) -> Path:
    """Backward-compatible release entry point; never uses a debug key."""
    return protect_android_apk(
        apk, root, debug=False, toolchain_root=toolchain_root,
        obfuscate=obfuscate,
    )
