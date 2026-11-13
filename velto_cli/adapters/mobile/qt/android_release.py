"""Fail-closed source-minimized Android release APK finalization.

The generated native extensions are not encryption or protection against
decompilers.
Only application-owned Python modules are transformed. Dependencies and
python-for-android's interpreter and standard library are left untouched.
"""
from __future__ import annotations

import ast
from dataclasses import fields, is_dataclass
import hashlib
import hmac
import io
import json
import os
import re
import secrets
from pathlib import Path, PurePosixPath
import shutil
import subprocess
import tarfile
import tempfile
import zipfile
from zipfile import ZipFile

from velto_cli.adapters.mobile.qt.android_final_apk import (
    _amended_private_tar, _android_sdk_tool, _debug_keystore,
    replace_android_private_version,
)
from velto_cli.adapters.mobile.qt.android_cython import (
    compile_android_extensions, module_name, output_name, _is_elf_arm64,
)
from velto_cli.adapters.mobile.qt.android_build_config import (
    android_exclusion_patterns, is_android_excluded, load_android_signing,
    verify_android_exclusions,
)


_RESOURCE_PACK_MAGIC = b"QYRPKG"
_RESOURCE_SECRETS_PATH = ".velto/secrets.enc"


def android_resource_protection_enabled(root: Path) -> bool:
    """Read the same resource_protection declaration used by desktop builds."""
    value = None
    # Existing projects keep this manifest in settings/, while the desktop
    # freezer also accepts a root build.json. The root file wins if both exist.
    for path in (root / "settings" / "build.json", root / "build.json"):
        if not path.exists():
            continue
        if path.is_symlink() or not path.is_file():
            raise ValueError(f"{path.relative_to(root)} must be a regular file")
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise ValueError(f"Cannot read {path.relative_to(root)}: {exc}") from exc
        if not isinstance(data, dict):
            raise ValueError(f"{path.relative_to(root)} must be a JSON object")
        if "resource_protection" in data:
            value = data["resource_protection"]
    return bool(value.get("enabled", True)) if isinstance(value, dict) else bool(value)


def android_protected_resource_prefixes(root: Path) -> tuple[str, ...]:
    """Project paths moved into resources.pak instead of private.tar."""
    if not android_resource_protection_enabled(root):
        return ()
    settings, resources = _resource_protection_config(root)
    return tuple(path.relative_to(root.resolve()).as_posix().rstrip("/") + "/"
                 for path in (settings, resources))


def _resource_protection_config(root: Path) -> tuple[Path, Path]:
    value: object = None
    for path in (root / "settings" / "build.json", root / "build.json"):
        if path.is_file():
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, dict) and "resource_protection" in data:
                value = data["resource_protection"]
    if isinstance(value, dict):
        settings = value.get("settings_dir", "settings")
        resources = value.get("resources_dir", "resources")
    else:
        settings, resources = "settings", "resources"
    if not isinstance(settings, str) or not isinstance(resources, str):
        raise ValueError("resource_protection settings_dir/resources_dir must be strings")
    paths = []
    for item in (settings, resources):
        candidate = (root / item).resolve()
        if not candidate.is_relative_to(root.resolve()):
            raise ValueError("resource_protection directories must stay inside the project")
        paths.append(candidate)
    return paths[0], paths[1]


def _resource_keystream(seed: bytes, size: int) -> bytes:
    blocks, index = [], 0
    while sum(map(len, blocks)) < size:
        blocks.append(hashlib.sha256(seed + index.to_bytes(8, "big")).digest())
        index += 1
    return b"".join(blocks)[:size]


def _resource_xor(data: bytes, key: bytes, nonce: bytes) -> bytes:
    output = bytearray(len(data))
    for offset in range(0, len(data), 32):
        stream = hashlib.sha256(key + nonce + (offset // 32).to_bytes(8, "big")).digest()
        for index, value in enumerate(data[offset:offset + len(stream)]):
            output[offset + index] = value ^ stream[index]
    return bytes(output)


def _prepare_android_resource_package(root: Path, directory: Path) -> tuple[Path, Path, Path, frozenset[str]]:
    """Create desktop-compatible resources.pak and Android runtime source."""
    directory.mkdir(parents=True, exist_ok=True)
    settings_dir, resources_dir = _resource_protection_config(root)
    patterns = android_exclusion_patterns(root)
    entries: list[tuple[Path, str]] = []
    remove: set[str] = set()
    for base, prefix in ((settings_dir, "settings"), (resources_dir, "resources")):
        if not base.is_dir():
            continue
        for source in sorted(base.rglob("*")):
            if not source.is_file() or source.is_symlink():
                continue
            rel_project = source.relative_to(root).as_posix()
            rel = source.relative_to(base).as_posix()
            if prefix == "settings" and rel == "sign.json":
                remove.add(f"{prefix}/{rel}")
                continue
            # secrets.json is excluded from private.tar, but belongs inside
            # the encrypted/authenticated QYRPKG payload on Android.
            if is_android_excluded(rel_project, patterns) and not (
                prefix == "settings" and rel == "secrets.json"
            ):
                remove.add(f"{prefix}/{rel}")
                continue
            entries.append((source, f"{prefix}/{rel}"))
            remove.add(f"{prefix}/{rel}")

    runtime_secret = secrets.token_bytes(32)
    payload = io.BytesIO()
    with zipfile.ZipFile(payload, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for source, name in entries:
            archive.write(source, name)
    if not entries:
        raise RuntimeError("resource_protection is enabled but settings/ and resources/ are empty")
    resource_key, salt, nonce = secrets.token_bytes(32), secrets.token_bytes(16), secrets.token_bytes(16)
    plaintext = payload.getvalue()
    ciphertext = _resource_xor(plaintext, resource_key, nonce)
    tag = hmac.new(resource_key, nonce + ciphertext, hashlib.sha256).digest()
    mask = _resource_keystream(hashlib.sha256(runtime_secret + salt).digest(), len(resource_key))
    package = directory / "resources.pak"
    package.write_bytes(_RESOURCE_PACK_MAGIC + salt + bytes(a ^ b for a, b in zip(resource_key, mask)) + nonce + tag + ciphertext)
    source = directory / "runtime.py"
    source.write_text(f'RUNTIME_SECRET_HEX = "{runtime_secret.hex()}"\n', encoding="utf-8")
    return package, source, directory / "runtime.so", frozenset(remove)


def _owned(name: str, root: Path) -> bool:
    """Restrict transformation to Python files owned by the project."""
    parts = PurePosixPath(name).parts
    if not parts or ".." in parts or parts[0].startswith("."):
        return False
    if len(parts) == 1:
        return name == "main.py" or (name.endswith(".py") and (root / name).is_file())
    first = parts[0]
    return (
        first not in {"build", ".buildozer", "deployment", "tests", "test"}
        and (root / first).is_dir()
        and (root / first / parts[1]).exists()
    )


def _prepare_cython_source(source: bytes, name: str, *, entry_point: str = "main.py") -> bytes:
    """Compile project PSX markup lexically before Cython C translation."""
    from psx.markup.transform import transform_source
    # precompiled=True also sets the decorator marker correctly after
    # __future__ imports, without depending on inspect at Android runtime.
    prepared = transform_source(
        source.decode("utf-8"), filename=name, precompiled=True,
    ).source
    # The PSX transformer can mistakenly capture the stylesheet <link> tag
    # as a Python name. Keep actual {link} references and only remove the
    # spurious capture from templates that do not reference that name.
    from psx.markup.ast import Reference
    from psx.markup.parser import parse

    def references_link(value):
        if isinstance(value, Reference):
            return bool(value.path) and value.path[0] == "link"
        if is_dataclass(value) and not isinstance(value, type):
            return any(references_link(getattr(value, f.name)) for f in fields(value))
        if isinstance(value, (tuple, list)):
            return any(references_link(item) for item in value)
        return False

    tree = ast.parse(prepared, filename=name)
    stylesheet_templates = set()
    for statement in tree.body:
        if (isinstance(statement, ast.Assign) and len(statement.targets) == 1
            and isinstance(statement.targets[0], ast.Name)
            and isinstance(statement.value, ast.Call)
            and isinstance(statement.value.func, ast.Name)
            and statement.value.func.id == "_psx_compile_template"
            and statement.value.args
            and isinstance(statement.value.args[0], ast.Constant)
            and isinstance(statement.value.args[0].value, str)):
            markup = statement.value.args[0].value
            if "<link" in markup and not references_link(parse(markup, filename=name)):
                stylesheet_templates.add(statement.targets[0].id)

    class RemoveSpuriousLink(ast.NodeTransformer):
        def visit_Call(self, node):
            self.generic_visit(node)
            if (isinstance(node.func, ast.Attribute)
                and node.func.attr == "render_lexical"
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id in stylesheet_templates
                and node.args and isinstance(node.args[0], ast.Dict)):
                scope = node.args[0]
                pairs = [(key, value) for key, value in zip(scope.keys, scope.values)
                         if not (isinstance(key, ast.Constant) and key.value == "link")]
                scope.keys = [key for key, _ in pairs]
                scope.values = [value for _, value in pairs]
            return node

    if stylesheet_templates:
        tree = ast.fix_missing_locations(RemoveSpuriousLink().visit(tree))
        prepared = ast.unparse(tree)
    if name == entry_point:
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


def _bootstrap(entry_point: str = "main.py") -> bytes:
    # The sole readable Python application entry required by p4a.
    # importlib prefers the compiled main.so extension over this source when
    # importing the same module name from the private application directory.
    module = module_name(entry_point)
    parent = PurePosixPath(entry_point).parent
    if str(parent) == ".":
        return f"import importlib\nimportlib.import_module({module!r})\n".encode("utf-8")
    return (
        "import importlib\nimport sys\nfrom pathlib import Path\n"
        f"sys.path.insert(0, str(Path(__file__).resolve().parent / {str(parent)!r}))\n"
        f"importlib.import_module({module!r})\n"
    ).encode("utf-8")


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
    entry_point: str = "main.py", protect_source: bool = True,
    protect_resources: bool = False,
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
    if protect_source:
        module_name(entry_point)
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
        resource_additions: dict[str, Path] = {}
        resource_removals: frozenset[str] = frozenset()
        if protect_resources:
            package, runtime_source, runtime_extension, resource_removals = (
                _prepare_android_resource_package(root, temp)
            )
            # The loader imports this extension explicitly as module runtime;
            # the .velto directory is only its on-disk container.
            jobs.append(("runtime.py", runtime_source, runtime_extension))
            resource_additions = {
                ".velto/resources.pak": package,
                ".velto/runtime.so": runtime_extension,
            }
        with tarfile.open(fileobj=io.BytesIO(original_private), mode="r:*") as tar:
            members = [m for m in tar.getmembers() if m.isfile()]
            for member in members:
                name = member.name.removeprefix("./").strip("/")
                if (not protect_source or not name.endswith(".py") or not _owned(name, root)
                        or (entry_point != "main.py" and name == "main.py")):
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
                source_path.write_bytes(_prepare_cython_source(payload, name, entry_point=entry_point))
                jobs.append((name, source_path, target))
                compiled[target_name] = target
                protected.add(name)
        # With a custom entry, main.py in the APK is the p4a launcher. If the
        # project also owns main.py, compile its original contents separately
        # so imports of ``main`` retain their normal meaning.
        original_main = root / "main.py"
        if protect_source and entry_point != "main.py" and original_main.is_file():
            target = temp / "compiled" / "main.so"
            target.parent.mkdir(parents=True, exist_ok=True)
            source_path = temp / "sources" / "main.py"
            source_path.parent.mkdir(parents=True, exist_ok=True)
            source_path.write_bytes(_prepare_cython_source(
                original_main.read_bytes(), "main.py", entry_point=entry_point
            ))
            jobs.append(("main.py", source_path, target))
            compiled["main.so"] = target
            protected.add("main.py")
        if protect_source and entry_point not in protected:
            raise RuntimeError(f"Android private archive is missing project entry point: {entry_point}")
        if jobs:
            compile_android_extensions(
                jobs, apk=apk, toolchain_root=toolchain_root,
                obfuscate=obfuscate if protect_source else False,
            )
        source_stub = None
        if protect_source:
            source_stub = temp / "main.py"
            source_stub.write_bytes(_bootstrap(entry_point))
        additions = {**compiled, **resource_additions}
        replacements = {"main.py": source_stub} if source_stub is not None else {}
        private = _amended_private_tar(
            original_private,
            additions,
            remove=frozenset(protected - {"main.py"}) | resource_removals,
            replace=replacements,
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
        if protect_source:
            verify_android_release_source_protection(signed, root, entry_point=entry_point)
        if protect_resources:
            verify_android_resource_protection(signed)
        verify_android_exclusions(signed, root)
        destination = apk.with_name(apk.name + ".velto-protected")
        try:
            shutil.copy2(signed, destination)
            os.replace(destination, apk)
        finally:
            destination.unlink(missing_ok=True)
    return apk


def verify_android_resource_protection(apk: Path) -> None:
    """Ensure Qyro's encrypted bundle replaces plaintext runtime resources."""
    with ZipFile(apk) as archive:
        private_name = next((name for name in ("assets/private.tar", "assets/private.mp3")
                             if name in archive.namelist()), None)
        if private_name is None:
            raise RuntimeError("Android resource-protection APK lacks private archive")
        with tarfile.open(fileobj=io.BytesIO(archive.read(private_name)), mode="r:*") as tar:
            files = {member.name.removeprefix("./").strip("/"): member
                     for member in tar if member.isfile()}
            package = tar.extractfile(files.get(".velto/resources.pak"))
            runtime = tar.extractfile(files.get(".velto/runtime.so"))
            if package is None or runtime is None:
                raise RuntimeError("Android resource protection lacks resources.pak or runtime.so")
            if not package.read().startswith(_RESOURCE_PACK_MAGIC):
                raise RuntimeError("Android protected resources package has invalid format")
            if not _is_elf_arm64(runtime.read(32)):
                raise RuntimeError("Android protected resource runtime is not ARM64 ELF")
            leaked = sorted(name for name in files
                            if name.startswith(("settings/", "resources/")))
            if leaked:
                raise RuntimeError("Android APK exposes protected resources: " + ", ".join(leaked[:12]))


def verify_android_release_source_protection(apk: Path, root: Path, *, entry_point: str = "main.py") -> None:
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
            if output_name(entry_point) not in items or "main.py" not in items:
                raise RuntimeError("Protected release lacks the compiled entry point and bootstrap")
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
            if bootstrap is None or bootstrap.read() != _bootstrap(entry_point):
                raise RuntimeError("Protected release contains an unverified main.py")
