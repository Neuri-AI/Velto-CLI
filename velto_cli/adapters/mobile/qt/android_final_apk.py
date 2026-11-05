"""Complete and re-sign the APK that Qt's Android wrapper leaves behind.

The pyside6-android-deploy subprocess can clean .buildozer/android/platform
before returning. Buildozer then leaves only a project-root APK. Never
publish an APK that has been changed without Android signature verification.
"""
from __future__ import annotations

import io
import os
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
        build_tools = sdk / "build-tools"
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


def _amended_private_tar(data: bytes, additions: dict[str, Path]) -> bytes:
    if not additions:
        return data
    buffer = io.BytesIO()
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:*") as original:
        existing = {
            member.name.removeprefix("./").strip("/")
            for member in original.getmembers() if member.isfile()
        }
        with tarfile.open(fileobj=buffer, mode="w") as new:
            for member in original:
                stream = original.extractfile(member) if member.isfile() else None
                new.addfile(member, stream)
            for name, path in sorted(additions.items()):
                if name in existing:
                    continue
                info = tarfile.TarInfo(name)
                info.size = path.stat().st_size
                info.mode = 0o644
                with path.open("rb") as stream:
                    new.addfile(info, stream)
    return buffer.getvalue()


def finalize_copied_debug_apk(
    apk: Path, root: Path, *, icon: Path | None,
) -> tuple[Path, tuple[str, ...], bool]:
    """Patch project assets and launcher PNG, zipalign, sign and verify.

    The original APK is unchanged until a signed replacement is fully
    verified. This operation does not rebuild Python, Qt or Java.
    """
    if apk.suffix.lower() != ".apk" or not apk.is_file():
        raise RuntimeError(f"Expected an existing Android debug APK: {apk}")
    resources = _project_runtime_assets(root)
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
        if not missing and not icon_changed:
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
        updated_private = _amended_private_tar(original_private, missing)
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
            with tarfile.open(
                fileobj=io.BytesIO(check.read(private_name)), mode="r:*"
            ) as archive:
                included = {
                    m.name.removeprefix("./").strip("/")
                    for m in archive.getmembers() if m.isfile()
                }
            if not set(resources).issubset(included):
                raise RuntimeError("Signed APK is missing required project assets.")
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
