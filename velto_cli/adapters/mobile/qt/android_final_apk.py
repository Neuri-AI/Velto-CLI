"""Complete and re-sign the APK that Qt's Android wrapper leaves behind.

The pyside6-android-deploy subprocess can clean .buildozer/android/platform
before returning. Buildozer then leaves only a project-root APK. Never
publish an APK that has been changed without Android signature verification.
"""
from __future__ import annotations

import io
from gzip import GzipFile
import os
import re
from pathlib import Path
import shutil
import subprocess
import tarfile
import tempfile
from zipfile import ZipFile

from velto_cli.adapters.mobile.qt.android_sources import _project_runtime_assets


def _android_sdk_tool(name: str) -> Path:
    candidates = []
    for variable in ("ANDROID_HOME", "ANDROID_SDK_ROOT"):
        if os.environ.get(variable):
            candidates.append(Path(os.environ[variable]))
    candidates.extend((
        Path.home() / ".pyside6_android_deploy/android-sdk",
        Path.home() / ".buildozer/android/platform/android-sdk",
        Path.home() / "Library/Android/sdk",
    ))
    # Prefer build-tools belonging to the actual SDK on the host.
    for sdk in candidates:
        # Some PySide6 installers nest the SDK one directory below its
        # configured download root.
        for base in (sdk, sdk / "android-sdk", sdk / "sdk"):
            build_tools = base / "build-tools"
            if build_tools.is_dir():
                for version in sorted(build_tools.iterdir(), reverse=True):
                    found = version / name
                    if found.is_file():
                        return found
    executable = shutil.which(name)
    if executable:
        return Path(executable)
    raise RuntimeError(
        f"Android SDK {name} is required to finalize a signed APK. "
        "Configure ANDROID_HOME or ANDROID_SDK_ROOT to an installed Android SDK."
    )


def _debug_keystore() -> Path:
    store = Path.home() / ".android/debug.keystore"
    if store.is_file():
        return store
    keytool = shutil.which("keytool")
    if not keytool:
        raise RuntimeError(
            "Android debug keystore is missing and keytool is not available."
        )
    store.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run([
        keytool, "-genkeypair", "-v",
        "-keystore", str(store), "-alias", "androiddebugkey",
        "-keyalg", "RSA", "-keysize", "2048", "-validity", "10000",
        "-storepass", "android", "-keypass", "android",
        "-dname", "CN=Android Debug,O=Android,C=US",
    ], check=True, capture_output=True, text=True)
    return store


def _missing_parent_dirs(archive: tarfile.TarFile) -> set[str]:
    """Find directories required by archive members but not explicitly stored.

    p4a's Android extractor relies on directory entries in private.tar;
    Python's tarfile extractor can create parents implicitly and therefore
    does not reproduce this device-side failure.
    """
    members = archive.getmembers()
    existing = {
        m.name.removeprefix("./").strip("/")
        for m in members if m.isdir()
    }
    needed: set[str] = set()
    for member in members:
        name = member.name.removeprefix("./").strip("/")
        components = name.split("/")
        for depth in range(1, len(components)):
            needed.add("/".join(components[:depth]))
    return needed - existing


def _amended_private_tar(
    data: bytes, additions: dict[str, Path], *,
    replace: dict[str, Path] | None = None,
    remove: frozenset[str] = frozenset(),
) -> bytes:
    """Build p4a-compatible gzip USTAR with explicit parent directories."""
    replace = replace or {}
    if set(additions) & set(remove) or set(replace) & set(remove):
        raise ValueError("Conflicting Android archive changes")
    buffer = io.BytesIO()
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:*") as original:
        members = original.getmembers()
        existing = {
            member.name.removeprefix("./").strip("/")
            for member in members if member.isfile()
        }
        all_paths = [
            m.name.removeprefix("./").strip("/")
            for m in members
        ] + list(additions) + list(replace)
        required_dirs: set[str] = set()
        for path in all_paths:
            components = path.split("/")
            for depth in range(1, len(components)):
                required_dirs.add("/".join(components[:depth]))
        existing_dirs = {
            m.name.removeprefix("./").strip("/")
            for m in members if m.isdir()
        }
        try:
            with GzipFile(fileobj=buffer, mode="wb", mtime=0) as compressed:
                with tarfile.open(
                    fileobj=compressed, mode="w", format=tarfile.USTAR_FORMAT,
                ) as new:
                    # Explicitly create missing directories *before* any
                    # member that references them, as p4a's make_tar does.
                    for directory in sorted(
                        required_dirs - existing_dirs,
                        key=lambda item: (item.count("/"), item),
                    ):
                        info = tarfile.TarInfo(directory)
                        info.type = tarfile.DIRTYPE
                        info.mode = 0o755
                        info.uid = info.gid = 0
                        info.mtime = 0
                        new.addfile(info)
                    for member in members:
                        if member.type in (tarfile.XHDTYPE, tarfile.XGLTYPE):
                            continue
                        name = member.name.removeprefix("./").strip("/")
                        if name in remove:
                            continue
                        member.pax_headers = {}
                        source = replace.get(name) if member.isfile() else None
                        if source is not None:
                            member.size = source.stat().st_size
                            with source.open("rb") as stream:
                                new.addfile(member, stream)
                        else:
                            stream = original.extractfile(member) if member.isfile() else None
                            new.addfile(member, stream)
                    for name, path in sorted(additions.items()):
                        if name in existing:
                            continue
                        info = tarfile.TarInfo(name)
                        info.size = path.stat().st_size
                        info.mode = 0o644
                        info.uid = info.gid = 0
                        info.mtime = 0
                        with path.open("rb") as stream:
                            new.addfile(info, stream)
        except ValueError as exc:
            raise RuntimeError(
                "Cannot encode Android application data as gzip USTAR; "
                "check archive metadata and path lengths."
            ) from exc
    return buffer.getvalue()


# p4a compares a compiled string resource named "private_version" with
# files/app/private.version at launch. Merely changing assets/private.tar
# does NOT force Android to extract new Python files. For debug APKs only,
# change the fixed-width 40-character SHA1 resource without changing the
# surrounding binary XML/string-pool offsets. Reject ambiguous tables.
_HEX40 = re.compile(rb"(?<![0-9a-f])[0-9a-f]{40}(?![0-9a-f])")
_UTF16_HEX40 = re.compile(
    rb"(?<![0-9a-f]\\x00)(?:[0-9a-f]\\x00){40}(?![0-9a-f]\\x00)"
)


def android_private_version_marker(arsc: bytes) -> tuple[bytes, bytes] | None:
    """Identify a unique p4a private_version marker in compiled resources."""
    if (b"private_version" not in arsc
            and "private_version".encode("utf-16le") not in arsc):
        return None
    matches = [(m.group(), b"ascii") for m in _HEX40.finditer(arsc)]
    matches.extend((m.group(), b"utf16") for m in _UTF16_HEX40.finditer(arsc))
    if len(matches) != 1:
        return None
    return matches[0]


def replace_android_private_version(arsc: bytes, marker: str) -> bytes:
    """Patch only a unique, fixed-length UTF-8/UTF-16 resource string."""
    if not re.fullmatch(r"[0-9a-f]{40}", marker):
        raise ValueError("Android private version must be a 40-character SHA1 hex")
    located = android_private_version_marker(arsc)
    if located is None:
        raise RuntimeError(
            "Cannot safely find the Android private_version resource. "
            "Rebuild the Android application instead of reusing stale assets."
        )
    old, encoding = located
    new = (marker.encode("ascii") if encoding == b"ascii"
           else marker.encode("utf-16le"))
    if new == old:
        raise ValueError("Updated Android private_version must differ")
    result = arsc.replace(old, new)
    if len(result) != len(arsc):
        raise AssertionError("Android resource table size changed")
    return result


def finalize_copied_debug_apk(
    apk: Path, root: Path, *, icon: Path | None,
    updates: dict[str, Path] | None = None,
    removals: frozenset[str] = frozenset(),
    private_version: str | None = None,
) -> tuple[Path, tuple[str, ...], bool]:
    """Patch project assets and launcher PNG, zipalign, sign and verify.

    The original APK is unchanged until a signed replacement is fully
    verified. This operation does not rebuild Python, Qt or Java.
    """
    if apk.suffix.lower() != ".apk" or not apk.is_file():
        raise RuntimeError(f"Expected an existing Android debug APK: {apk}")
    resources = _project_runtime_assets(root)
    updates = updates or {}
    with ZipFile(apk) as source:
        private_names = [
            n for n in source.namelist()
            if n in ("assets/private.tar", "assets/private.mp3")
        ]
        if len(private_names) != 1:
            raise RuntimeError(
                "Qt APK has no unambiguous assets/private.tar to stage Velto resources."
            )
        private_name = private_names[0]
        original_private = source.read(private_name)
        with tarfile.open(fileobj=io.BytesIO(original_private), mode="r:*") as tar:
            present = {
                m.name.removeprefix("./").strip("/")
                for m in tar.getmembers() if m.isfile()
            }
            missing_parents = _missing_parent_dirs(tar)
        missing = {name: source for name, source in resources.items() if name not in present}
        # Android resources may be under different density-specific folders.
        icons = [
            name for name in source.namelist()
            if icon is not None
            and name.startswith("res/mipmap")
            and name.endswith("/icon.png")
        ]
        icon_bytes = icon.read_bytes() if icon is not None else None
        icon_changed = any(source.read(name) != icon_bytes for name in icons)
        # p4a expects gzip+USTAR even though the filename is private.tar.
        # Repair already-modified APKs whose content was rewritten as plain
        # TAR, including when all resources and icons are already present.
        private_needs_repair = not original_private.startswith(bytes((0x1f, 0x8b)))
        if (not missing and not updates and not removals and private_version is None
                and not icon_changed and not private_needs_repair and not missing_parents):
            return apk, (), False

        if icon is not None and not icons:
            raise RuntimeError(
                "Qt APK does not expose a replaceable mipmap icon.png. "
                "Cannot certify project launcher icon without Gradle resources."
            )

    zipalign = _android_sdk_tool("zipalign")
    apksigner = _android_sdk_tool("apksigner")
    keystore = _debug_keystore()

    with tempfile.TemporaryDirectory(prefix="velto-android-apk-") as folder:
        stage = Path(folder)
        unsigned = stage / "unsigned.apk"
        aligned = stage / "aligned.apk"
        signed = stage / "signed.apk"
        extra = {name: path for name, path in updates.items() if name not in present}
        overwrite = {name: path for name, path in updates.items() if name in present}
        updated_private = _amended_private_tar(
            original_private, {**missing, **extra},
            replace=overwrite, remove=removals,
        )
        with ZipFile(apk) as original, ZipFile(unsigned, "w") as output:
            for member in original.infolist():
                name = member.filename
                # Old v1 signatures are invalid after modifications; v2/v3
                # signatures are recreated by apksigner.
                if name.startswith("META-INF/") and name.upper().endswith(
                    (".RSA", ".DSA", ".EC", ".SF")
                ):
                    continue
                if name == private_name:
                    payload = updated_private
                elif name in icons and icon_bytes is not None:
                    payload = icon_bytes
                elif name == "resources.arsc" and private_version is not None:
                    payload = replace_android_private_version(
                        original.read(name), private_version
                    )
                else:
                    payload = original.read(name)
                output.writestr(member, payload)

        subprocess.run(
            [str(zipalign), "-f", "-p", "4", str(unsigned), str(aligned)],
            check=True, capture_output=True, text=True,
        )
        subprocess.run([
            str(apksigner), "sign", "--ks", str(keystore),
            "--ks-key-alias", "androiddebugkey",
            "--ks-pass", "pass:android", "--key-pass", "pass:android",
            "--out", str(signed), str(aligned),
        ], check=True, capture_output=True, text=True)
        subprocess.run(
            [str(apksigner), "verify", "--verbose", str(signed)],
            check=True, capture_output=True, text=True,
        )

        # Check the exact final payload before replacing the original.
        with ZipFile(signed) as check:
            if private_version is not None:
                patched = android_private_version_marker(
                    check.read("resources.arsc")
                )
                if patched is None:
                    raise RuntimeError("Signed APK lost the private_version marker")
                expected = (
                    private_version.encode("ascii") if patched[1] == b"ascii"
                    else private_version.encode("utf-16le")
                )
                if patched[0] != expected:
                    raise RuntimeError("Signed APK retained stale extraction version")
            private_bytes = check.read(private_name)
            if not private_bytes.startswith(bytes((0x1f, 0x8b))):
                raise RuntimeError(
                    "Signed APK private.tar is not gzip-compressed; "
                    "Android will reject its application data."
                )
            with tarfile.open(
                fileobj=io.BytesIO(private_bytes), mode="r:*"
            ) as archive:
                included = {
                    m.name.removeprefix("./").strip("/")
                    for m in archive.getmembers() if m.isfile()
                }
            if not set(resources).issubset(included):
                raise RuntimeError("Signed APK is missing required project assets.")
            if not set(updates).issubset(included) or (set(removals) & included):
                raise RuntimeError("Signed APK does not contain requested source changes.")
            if icons and icon_bytes is not None:
                if any(check.read(name) != icon_bytes for name in icons):
                    raise RuntimeError("Signed APK does not retain the selected icon.")
        # Copy the fully verified APK to a sibling before the atomic swap.
        replacement = apk.with_name(apk.name + ".velto-signed")
        try:
            shutil.copyfile(signed, replacement)
            os.replace(replacement, apk)
        finally:
            replacement.unlink(missing_ok=True)
    return apk, tuple(sorted(missing)), bool(icon_changed)
