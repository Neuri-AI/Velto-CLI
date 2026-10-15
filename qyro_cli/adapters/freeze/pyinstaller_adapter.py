"""
qyro.adapters.freeze.pyinstaller_adapter
PyInstaller-based freezer implementing FreezerPort.
Constructs arguments, creates Windows UAC manifests, handles debug/onefile/onedir modes,
runs the PyInstaller process cleanly, and captures output artifacts.
"""

import fnmatch
import hashlib
import hmac
import json
import os
import secrets
import shutil
import subprocess
import sys
import time
import zipfile
from pathlib import Path
from typing import List, Optional

from qyro_cli.application.ports import (
    FreezerPort,
    FrameworkHookResolverPort,
    ProgressPort,
    UserInteractionPort,
)
from qyro_cli.domain.build import (
    BuildArtifact,
    BundleMode,
    FreezeManifest
)
from PIL import Image
from qyro_cli.adapters.freeze.secrets_crypto import encrypt_secrets_payload
from qyro_cli.adapters.persistence.storage import SIGNING_SETTING_KEYS
from qyro_cli.adapters.process.runners import _prepared_psx_entry_point
from qyro_cli.domain.errors import QyroError, FreezeExecutionError


class PyInstallerFreezer(FreezerPort):
    """Clean Architecture adapter wrapping PyInstaller executable."""

    _RESOURCE_PACK_MAGIC = b"QYRPKG"
    _RESOURCE_SALT_LEN = 16
    _RESOURCE_KEY_LEN = 32
    _RUNTIME_SECRET_MODULE_STEM = "runtime"
    _SECRETS_FILE_NAME = "secrets.json"
    _SIGN_FILE_NAME = "sign.json"
    _SECRETS_ARCHIVE_PATH = ".qyro/secrets.enc"

    def __init__(
        self,
        resolver: FrameworkHookResolverPort,
        ui: Optional[UserInteractionPort] = None,
        progress: Optional[ProgressPort] = None,
    ):
        self._resolver = resolver
        self._ui = ui
        self._progress = progress

    def build_command(
        self,
        project_root: Path,
        manifest: FreezeManifest,
        extra_args: Optional[List[str]] = None,
    ) -> List[str]:
        """Constructs the complete argument list for PyInstaller."""
        cmd = [sys.executable, "-m", "PyInstaller"]

        # Target script
        entry_script = project_root / manifest.entry_point
        if not entry_script.exists():
            raise QyroError(
                f"Entry point '{manifest.entry_point}' not found in '{project_root}'",
                hint="Verify 'entry_point' in settings/base.json or build.json.",
            )

        # Match `qyro start`: freeze lexical PSX templates as ordinary Python
        # rather than relying on the untransformed runtime explicit-scope API.
        with _prepared_psx_entry_point(
            str(entry_script), output_dir=project_root / "build" / "temp" / "psx"
        ) as prepared_script:
            cmd.append(prepared_script)
            if prepared_script != str(entry_script):
                cmd.extend(["--paths", str(project_root)])

        # Application Name
        cmd.extend(["--name", manifest.app_name])

        # Distpath & Workpath inside 'build/' (Qyro convention)
        build_dir = project_root / "build"
        dist_dir = build_dir
        build_work_dir = build_dir / "temp"
        spec_dir = build_dir

        cmd.extend(["--distpath", str(dist_dir)])
        cmd.extend(["--workpath", str(build_work_dir)])
        cmd.extend(["--specpath", str(spec_dir)])

        is_mac_target = manifest.target_platform.lower() in ("mac", "macos", "darwin") or (
            manifest.target_platform == "auto" and sys.platform == "darwin"
        )

        # Bundle mode (onedir vs onefile)
        if manifest.bundle_mode == BundleMode.ONEFILE:
            cmd.append("--onefile")
        else:
            cmd.append("--onedir")

        # Debug & Console
        is_console_mode = manifest.debug.console_window or manifest.debug.enabled
        if is_console_mode:
            cmd.append("--console")
        else:
            cmd.append("--windowed")  # GUI app: no terminal popup

        if is_mac_target and not is_console_mode and "--windowed" not in cmd and "-w" not in cmd:
            cmd.append("-w")  # Force generation of .app bundle on macOS for windowed GUI apps

        if manifest.debug.bootloader_debug:
            cmd.append("--debug=all")
        elif manifest.debug.unstripped:
            cmd.append("--debug=imports")

        # Windows UAC elevation
        if manifest.target_platform == "windows" and manifest.uac.requires_admin:
            cmd.append("--uac-admin")
        if manifest.target_platform == "windows" and manifest.uac.ui_access:
            cmd.append("--uac-uiaccess")

        # macOS Bundle Identifier
        if is_mac_target:
            bundle_id = manifest.mac_bundle_identifier or manifest.suggest_bundle_id()
            if bundle_id:
                cmd.extend(["--osx-bundle-identifier", bundle_id])

        # Icon handling
        if is_mac_target:
            icns_file = self._handle_mac_icon(project_root, dist_dir, manifest)
            if icns_file and icns_file.exists():
                cmd.extend(["--icon", str(icns_file)])
        else:
            icon_file = self._resolve_icon(project_root, dist_dir, manifest)
            if icon_file and icon_file.exists():
                cmd.extend(["--icon", str(icon_file)])

        # Hidden imports from manifest
        for hi in manifest.hidden_imports:
            cmd.extend(["--hidden-import", hi])

        # Framework-specific hooks (Qt, Kivy, Tkinter, resources)
        framework_args = self._resolver.resolve_args(project_root, manifest)
        cmd.extend(framework_args)

        if manifest.protect_resources:
            package_path, runtime_secret_module_path = self._prepare_protected_resources_package(
                project_root=project_root,
                manifest=manifest,
            )
        else:
            package_path, runtime_secret_module_path = self._prepare_protected_secrets_package(
                project_root=project_root,
                manifest=manifest,
            )

        sep = ";" if manifest.target_platform == "windows" else ":"
        # The runtime discovers this package before it can read settings, so
        # its embedded filename must be fixed even when bundle_path is custom.
        # Keep the configured output file and stage a copy for PyInstaller.
        if package_path.name != "resources.pak":
            embedded_package_path = build_work_dir / ".qyro" / "resources.pak"
            embedded_package_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(package_path, embedded_package_path)
            package_path = embedded_package_path
        cmd.extend(["--add-data", f"{package_path.as_posix()}{sep}.qyro"])
        cmd.extend(["--add-data", f"{runtime_secret_module_path.as_posix()}{sep}.qyro"])

        # Optimization configurations
        if manifest.optimization.clean_build:
            cmd.append("--clean")

        # Bytecode optimization flag for PyInstaller 6.6+
        if manifest.optimization.bytecode_opt > 0:
            cmd.extend(["--optimize", str(manifest.optimization.bytecode_opt)])

        # Strip binaries (symbol table stripping on Linux/macOS)
        if manifest.optimization.strip_binaries and not (manifest.target_platform.lower() == "windows"):
            cmd.append("--strip")

        if is_mac_target or not manifest.optimization.upx_enabled:
            cmd.append("--noupx")
        else:
            # Check for custom upx directory or auto-detect in environment
            upx_bin = "upx.exe" if manifest.target_platform == "windows" else "upx"
            upx_dir_to_use = None
            if manifest.optimization.upx_dir and Path(manifest.optimization.upx_dir).exists():
                upx_dir_to_use = manifest.optimization.upx_dir
            elif not shutil.which("upx"):
                py_prefix = Path(sys.prefix)
                for search_cand in [py_prefix / "Scripts", py_prefix / "bin", py_prefix]:
                    if (search_cand / upx_bin).exists():
                        upx_dir_to_use = str(search_cand)
                        break

            if upx_dir_to_use:
                cmd.extend(["--upx-dir", upx_dir_to_use])

            for upx_exc in manifest.optimization.upx_excludes:
                exc_lower = upx_exc.lower()
                target_p = manifest.target_platform.lower()
                # Smart OS filter: discard incompatible binary extensions
                if target_p == "linux":
                    if exc_lower.endswith(".dll") or exc_lower.endswith(".pyd"):
                        continue
                elif target_p == "windows":
                    if exc_lower.endswith(".so") or ".so." in exc_lower or exc_lower.endswith(".dylib"):
                        continue
                cmd.extend(["--upx-exclude", upx_exc])

        # Module exclusions for lightweight binaries
        for exc_mod in manifest.optimization.exclude_modules:
            cmd.extend(["--exclude-module", exc_mod])

        # Additional search paths for modules and DLLs
        for p in manifest.paths:
            cmd.extend(["--paths", str(p)])

        # Packages to collect completely (submodules, data, binaries)
        for ca in manifest.collect_all:
            cmd.extend(["--collect-all", ca])

        # Extra PyInstaller arguments
        if manifest.extra_pyinstaller_args:
            cmd.extend(manifest.extra_pyinstaller_args)
        if extra_args:
            cmd.extend(extra_args)

        cmd.append("-y")  # Overwrite output directory without prompting
        return cmd

    def freeze(
        self,
        project_root: Path,
        manifest: FreezeManifest,
        extra_args: Optional[List[str]] = None,
    ) -> BuildArtifact:
        start_time = time.time()

        build_dir = project_root / "build"
        build_work_dir = build_dir / "temp"
        dist_dir = build_dir
        is_mac_target = manifest.target_platform.lower() in ("mac", "macos", "darwin")

        # Build PyInstaller command list
        cmd = self.build_command(project_root, manifest, extra_args)

        # Save command to build/pyinstaller_command.txt
        cmd_str = " ".join(f'"{arg}"' if (" " in arg or not arg) else arg for arg in cmd)
        build_dir.mkdir(parents=True, exist_ok=True)
        try:
            (build_dir / "pyinstaller_command.txt").write_text(cmd_str, encoding="utf-8")
        except Exception:
            pass

        # Check UPX availability and notify if missing
        upx_bin = "upx.exe" if manifest.target_platform == "windows" else "upx"
        upx_available = bool(shutil.which("upx"))
        if not upx_available:
            if manifest.optimization.upx_dir and (Path(manifest.optimization.upx_dir) / upx_bin).exists():
                upx_available = True
            else:
                py_prefix = Path(sys.prefix)
                for search_cand in [py_prefix / "Scripts", py_prefix / "bin", py_prefix]:
                    if (search_cand / upx_bin).exists():
                        upx_available = True
                        break

        if manifest.optimization.upx_enabled and not is_mac_target and not upx_available:
            warn_msg = (
                "⚠️ UPX is enabled in settings, but 'upx' was NOT found in PATH or environment.\n"
                "   Binary compression will be skipped by PyInstaller.\n"
                "   To activate UPX:\n"
                "     • Conda: conda install -c conda-forge upx\n"
                "     • Chocolatey: choco install upx\n"
                "     • Or specify 'upx_dir': 'path/to/upx' in settings/windows.json"
            )
            if self._ui:
                self._ui.warning(warn_msg)
            else:
                sys.stdout.write(f"\n{warn_msg}\n\n")
                sys.stdout.flush()

        if self._progress:
            self._progress.start(f"Freezing '{manifest.app_name}' with {manifest.binding}...")
        elif self._ui:
            self._ui.progress(f"Freezing '{manifest.app_name}' [{manifest.bundle_mode.value}]...")

        # Run PyInstaller subprocess
        env = dict(os.environ)

        # Filter out site-packages from PYTHONPATH to avoid PyInstaller DEPRECATION warning / foreign env error
        existing_py_path = env.get("PYTHONPATH", "")
        cleaned_paths: List[str] = []
        if existing_py_path:
            for p in existing_py_path.split(os.pathsep):
                if p and "site-packages" not in p.lower():
                    cleaned_paths.append(p)

        env["PYTHONPATH"] = (
            str(project_root) + (os.pathsep + os.pathsep.join(cleaned_paths) if cleaned_paths else "")
        )

        # Bytecode optimization flag
        if manifest.optimization.bytecode_opt > 0:
            env["PYTHONOPTIMIZE"] = str(manifest.optimization.bytecode_opt)

        process = subprocess.run(
            cmd,
            cwd=str(project_root),
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )

        if self._progress:
            self._progress.stop()

        # Save PyInstaller engine output to build/pyinstaller_output.log for inspection
        try:
            (build_dir / "pyinstaller_output.log").write_text(process.stdout or "", encoding="utf-8")
        except Exception:
            pass

        if process.returncode != 0:
            raise FreezeExecutionError(
                command=" ".join(cmd),
                exit_code=process.returncode,
                output=process.stdout,
            )

        duration = time.time() - start_time

        # Cleanup temporary build files and any leftover empty nested directories
        if manifest.optimization.clean_build and build_work_dir.exists():
            shutil.rmtree(build_work_dir, ignore_errors=True)

        leftover_nested_build = build_dir / "build"
        if leftover_nested_build.exists() and leftover_nested_build.is_dir():
            shutil.rmtree(leftover_nested_build, ignore_errors=True)

        # Post-process for macOS app bundle
        if is_mac_target:
            app_bundle = dist_dir / f"{manifest.app_name}.app"
            if app_bundle.exists():
                self._remove_unwanted_pyinstaller_files(app_bundle)
                self._copy_mac_resources(project_root, app_bundle, manifest)

        # Calculate output executable path and size
        if manifest.bundle_mode == BundleMode.ONEFILE:
            ext = ".exe" if manifest.target_platform == "windows" else ""
            out_exe = dist_dir / f"{manifest.app_name}{ext}"
            out_folder = dist_dir
            size = out_exe.stat().st_size if out_exe.exists() else 0
        else:
            out_folder = dist_dir / manifest.app_name
            ext = ".exe" if manifest.target_platform == "windows" else ""
            out_exe = out_folder / f"{manifest.app_name}{ext}"
            if is_mac_target and (dist_dir / f"{manifest.app_name}.app").exists():
                out_folder = dist_dir / f"{manifest.app_name}.app"
                out_exe = out_folder / "Contents" / "MacOS" / manifest.app_name
            self._purge_excluded_binaries(out_folder, manifest)
            size = self._calc_dir_size(out_folder)

        # If binding is Kivy, replace default Kivy logo files in output with user's custom icon
        if str(manifest.binding).lower() == "kivy":
            self._override_kivy_icons_in_output(out_folder, project_root)

        # Sign macOS app bundle as the final step after all file modifications are complete
        if is_mac_target:
            app_bundle = dist_dir / f"{manifest.app_name}.app"
            if app_bundle.exists():
                self._codesign_mac_bundle(app_bundle)

        return BuildArtifact(
            output_dir=out_folder,
            executable_path=out_exe,
            bundle_mode=manifest.bundle_mode,
            size_bytes=size,
            duration_seconds=duration,
            manifest_used=manifest,
            uac_applied=manifest.uac.requires_admin,
            debug_mode=manifest.debug.enabled,
            binding=manifest.binding,
        )

    def _calc_dir_size(self, path: Path) -> int:
        if not path.exists():
            return 0
        if path.is_file():
            return path.stat().st_size
        total = 0
        for entry in path.rglob("*"):
            if entry.is_file():
                total += entry.stat().st_size
        return total

    def _prepare_protected_resources_package(
        self,
        *,
        project_root: Path,
        manifest: FreezeManifest,
    ) -> tuple[Path, Path]:
        settings_dir = (project_root / manifest.protected_settings_dir).resolve()
        resources_dir = (project_root / manifest.protected_resources_dir).resolve()
        package_path = (project_root / manifest.protected_bundle_relative_path).resolve()
        runtime_secret_module_path = self._runtime_secret_binary_path(package_path.parent)
        secrets_source_path = settings_dir / self._SECRETS_FILE_NAME
        sign_source_path = settings_dir / self._SIGN_FILE_NAME

        package_path.parent.mkdir(parents=True, exist_ok=True)

        entries: list[tuple[Path, str]] = []
        memory_entries: list[tuple[str, bytes]] = []
        if settings_dir.exists() and settings_dir.is_dir():
            for source in sorted(settings_dir.rglob("*")):
                if source.is_file():
                    if source.resolve() in {
                        secrets_source_path.resolve(),
                        sign_source_path.resolve(),
                    }:
                        continue
                    rel = source.relative_to(settings_dir).as_posix()
                    entries.append((source, f"settings/{rel}"))

        if resources_dir.exists() and resources_dir.is_dir():
            for source in sorted(resources_dir.rglob("*")):
                if source.is_file():
                    rel = source.relative_to(resources_dir).as_posix()
                    entries.append((source, f"resources/{rel}"))

        runtime_secret, runtime_secret_module_path = self._write_runtime_secret_module(runtime_secret_module_path)

        has_secrets = secrets_source_path.exists() and secrets_source_path.is_file()
        if has_secrets:
            encrypted_payload = self._encrypt_project_secrets(
                secrets_source_path=secrets_source_path,
                runtime_secret=runtime_secret,
            )
            memory_entries.append((self._SECRETS_ARCHIVE_PATH, encrypted_payload))

        if not entries and not has_secrets:
            raise QyroError(
                "Resource protection is enabled, but no files were found in settings/ or resources/.",
                hint="Add files to settings/ or resources/, or disable resource_protection.enabled.",
            )

        zip_payload = self._build_zip_payload(entries, memory_entries)
        self._write_protected_package_payload(
            package_path=package_path,
            zip_payload=zip_payload,
            runtime_secret=runtime_secret,
        )
        return package_path, runtime_secret_module_path

    def _prepare_protected_secrets_package(
        self,
        *,
        project_root: Path,
        manifest: FreezeManifest,
    ) -> tuple[Path, Path]:
        settings_dir = (project_root / manifest.protected_settings_dir).resolve()
        package_path = (project_root / manifest.protected_bundle_relative_path).resolve()
        runtime_secret_module_path = self._runtime_secret_binary_path(package_path.parent)
        secrets_source_path = settings_dir / self._SECRETS_FILE_NAME

        if not secrets_source_path.exists() or not secrets_source_path.is_file():
            raise QyroError(
                "settings/secrets.json is required for frozen builds.",
                hint="Create settings/secrets.json with a JSON object before running qyro build.",
            )

        package_path.parent.mkdir(parents=True, exist_ok=True)
        runtime_secret, runtime_secret_module_path = self._write_runtime_secret_module(runtime_secret_module_path)
        encrypted_payload = self._encrypt_project_secrets(
            secrets_source_path=secrets_source_path,
            runtime_secret=runtime_secret,
        )

        zip_payload = self._build_zip_payload([], [(self._SECRETS_ARCHIVE_PATH, encrypted_payload)])
        self._write_protected_package_payload(
            package_path=package_path,
            zip_payload=zip_payload,
            runtime_secret=runtime_secret,
        )
        return package_path, runtime_secret_module_path

    def _write_protected_package_payload(
        self,
        *,
        package_path: Path,
        zip_payload: bytes,
        runtime_secret: bytes,
    ) -> None:
        resource_key = secrets.token_bytes(self._RESOURCE_KEY_LEN)
        salt = secrets.token_bytes(self._RESOURCE_SALT_LEN)
        nonce = secrets.token_bytes(16)
        ciphertext = self._xor_keystream(zip_payload, key=resource_key, nonce=nonce)
        tag = hmac.new(resource_key, nonce + ciphertext, hashlib.sha256).digest()
        wrapped_key = self._wrap_key(resource_key=resource_key, salt=salt, runtime_secret=runtime_secret)

        package_path.write_bytes(self._RESOURCE_PACK_MAGIC + salt + wrapped_key + nonce + tag + ciphertext)

    def _encrypt_project_secrets(
        self,
        *,
        secrets_source_path: Path,
        runtime_secret: bytes,
    ) -> bytes:
        raw_text = secrets_source_path.read_text(encoding="utf-8")
        if not raw_text.strip():
            raise QyroError(
                "settings/secrets.json is empty.",
                hint="Provide a valid JSON object or remove settings/secrets.json before building.",
            )

        try:
            parsed = json.loads(raw_text)
        except json.JSONDecodeError as exc:
            raise QyroError(
                "settings/secrets.json is not valid JSON.",
                hint="Fix settings/secrets.json syntax before building.",
            ) from exc

        if not isinstance(parsed, dict):
            raise QyroError(
                "settings/secrets.json must contain a JSON object.",
                hint="Wrap secret keys under an object at the root of settings/secrets.json.",
            )

        signing_keys = sorted(SIGNING_SETTING_KEYS.intersection(parsed))
        if signing_keys:
            raise QyroError(
                "settings/secrets.json contains signing configuration.",
                hint=(
                    "Move these keys to settings/sign.json before building: "
                    + ", ".join(signing_keys)
                ),
            )

        return encrypt_secrets_payload(raw_text.encode("utf-8"), runtime_secret)

    def _runtime_secret_binary_path(self, output_dir: Path) -> Path:
        existing = self._find_runtime_secret_binary(output_dir)
        if existing is not None:
            return existing

        suffix = ".pyd" if sys.platform.startswith("win") else ".so"
        return output_dir / f"{self._RUNTIME_SECRET_MODULE_STEM}{suffix}"

    def _find_runtime_secret_binary(self, output_dir: Path) -> Path | None:
        candidates = [
            *sorted(output_dir.glob(f"{self._RUNTIME_SECRET_MODULE_STEM}*.so")),
            *sorted(output_dir.glob(f"{self._RUNTIME_SECRET_MODULE_STEM}*.pyd")),
            *sorted(output_dir.glob(f"{self._RUNTIME_SECRET_MODULE_STEM}*.dylib")),
        ]
        for candidate in candidates:
            if candidate.is_file():
                return candidate
        return None

    def _write_runtime_secret_module(self, runtime_secret_module_path: Path) -> tuple[bytes, Path]:
        runtime_secret = secrets.token_bytes(self._RESOURCE_KEY_LEN)
        output_dir = runtime_secret_module_path.parent
        source_path = output_dir / f"{self._RUNTIME_SECRET_MODULE_STEM}.py"
        existing = self._find_runtime_secret_binary(output_dir)
        if existing is not None:
            existing.unlink(missing_ok=True)

        content = (
            "# Auto-generated by Qyro freeze. Do not commit.\n"
            f"RUNTIME_SECRET_HEX = \"{runtime_secret.hex()}\"\n"
        )
        source_path.write_text(content, encoding="utf-8")

        compile_cmd = [
            sys.executable,
            "-m",
            "Cython.Build.Cythonize",
            "-3",
            "-i",
            source_path.name,
        ]

        c_artifact = source_path.with_suffix(".c")
        try:
            proc = subprocess.run(
                compile_cmd,
                cwd=str(output_dir),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
            )
            if proc.returncode != 0:
                output = (proc.stdout or "").strip()
                tail = "\n".join(output.splitlines()[-20:]) if output else "No compiler output captured."
                raise QyroError(
                    "Failed to compile protected runtime module with Cython.",
                    hint=(
                        "Install qyro-cli with the desktop extra and ensure a C compiler is available.\n"
                        f"Cython output (last lines):\n{tail}"
                    ),
                )

            compiled = self._find_runtime_secret_binary(output_dir)
            if compiled is None:
                raise QyroError(
                    "Cython compilation completed but runtime secret binary was not found.",
                    hint="Check Cython build output and toolchain availability.",
                )

            return runtime_secret, compiled
        finally:
            c_artifact.unlink(missing_ok=True)
            source_path.unlink(missing_ok=True)

    def _wrap_key(self, *, resource_key: bytes, salt: bytes, runtime_secret: bytes) -> bytes:
        mask = self._keystream(seed=self._master_seed(salt, runtime_secret), size=len(resource_key))
        return bytes(value ^ mask[i] for i, value in enumerate(resource_key))

    def _master_seed(self, salt: bytes, runtime_secret: bytes) -> bytes:
        return hashlib.sha256(runtime_secret + salt).digest()

    def _keystream(self, *, seed: bytes, size: int) -> bytes:
        blocks: list[bytes] = []
        index = 0
        total = 0
        while total < size:
            block = hashlib.sha256(seed + index.to_bytes(8, "big")).digest()
            blocks.append(block)
            total += len(block)
            index += 1
        return b"".join(blocks)[:size]

    def _build_zip_payload(
        self,
        entries: list[tuple[Path, str]],
        memory_entries: list[tuple[str, bytes]] | None = None,
    ) -> bytes:
        import io

        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for source, archive_name in entries:
                archive.write(source, archive_name)
            for archive_name, data in memory_entries or []:
                archive.writestr(archive_name, data)
        return buffer.getvalue()

    def _xor_keystream(self, data: bytes, *, key: bytes, nonce: bytes) -> bytes:
        out = bytearray(len(data))
        block_index = 0
        offset = 0

        while offset < len(data):
            block_seed = nonce + block_index.to_bytes(8, "big")
            stream = hashlib.sha256(key + block_seed).digest()
            chunk = data[offset:offset + len(stream)]

            for i, value in enumerate(chunk):
                out[offset + i] = value ^ stream[i]

            offset += len(chunk)
            block_index += 1

        return bytes(out)

    def _handle_mac_icon(
        self,
        project_root: Path,
        target_dir: Path,
        manifest: FreezeManifest,
    ) -> Optional[Path]:
        """Auto-generates target/Icon.icns from source icon using iconutil if needed."""
        if manifest.icon_path:
            icon_p = project_root / manifest.icon_path
            if icon_p.exists() and icon_p.suffix.lower() == ".icns":
                return icon_p

        icns_path = target_dir / "Icon.icns"
        if icns_path.exists():
            return icns_path

        candidates = [
            project_root / "resources" / "mac" / "icons" / "1024.png",
            project_root / "resources" / "mac" / "icon.png",
            project_root / "resources" / "base" / "icons" / "icon.png",
            project_root / "resources" / "base" / "icon.png",
            project_root / "resources" / "icon.png",
            project_root / "icon.png",
        ]

        source_icon = next((c for c in candidates if c.exists()), None)
        if not source_icon:
            return None

        iconset_dir = target_dir / "Icon.iconset"
        iconset_dir.mkdir(parents=True, exist_ok=True)
        sizes = [
            (16, 1), (16, 2),
            (32, 1), (32, 2),
            (128, 1), (128, 2),
            (256, 1), (256, 2),
            (512, 1), (512, 2),
        ]

        try:

            img = Image.open(source_icon)
            for size, scale in sizes:
                px = size * scale
                dest_name = f"icon_{size}x{size}"
                if scale != 1:
                    dest_name += f"@{scale}x"
                dest_name += ".png"
                resized = img.resize((px, px), Image.Resampling.LANCZOS)
                resized.save(iconset_dir / dest_name)
        except Exception:
            for size, scale in sizes:
                dest_name = f"icon_{size}x{size}"
                if scale != 1:
                    dest_name += f"@{scale}x"
                dest_name += ".png"
                shutil.copy(source_icon, iconset_dir / dest_name)

        try:
            subprocess.run(
                ["iconutil", "-c", "icns", str(iconset_dir), "-o", str(icns_path)],
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            return icns_path
        except (subprocess.SubprocessError, FileNotFoundError):
            return None

    def _resolve_icon(
        self,
        project_root: Path,
        build_dir: Path,
        manifest: FreezeManifest,
    ) -> Optional[Path]:
        """Resolves icon file (.ico for Windows, .ico/.png for Linux) with fallback to base/."""
        # 1. Explicit icon_path in manifest
        if manifest.icon_path:
            p = project_root / manifest.icon_path
            if p.exists():
                return p

        platform_name = manifest.target_platform.lower()

        # 2. Search platform-specific icon first, then fallback to base/
        candidates: List[Path] = [
            # Platform specific .ico
            project_root / "resources" / platform_name / "icons" / "Icon.ico",
            project_root / "resources" / platform_name / "icons" / "icon.ico",
            project_root / "resources" / platform_name / "Icon.ico",
            project_root / "resources" / platform_name / "icon.ico",
            # Base fallback .ico
            project_root / "resources" / "base" / "icons" / "Icon.ico",
            project_root / "resources" / "base" / "icons" / "icon.ico",
            project_root / "resources" / "base" / "Icon.ico",
            project_root / "resources" / "base" / "icon.ico",
            # Root resources .ico
            project_root / "resources" / "icons" / "Icon.ico",
            project_root / "resources" / "icons" / "icon.ico",
            project_root / "resources" / "Icon.ico",
            project_root / "resources" / "icon.ico",
            project_root / "Icon.ico",
            project_root / "icon.ico",
        ]

        for cand in candidates:
            if cand.exists():
                return cand

        # 3. Search PNG candidates
        png_candidates = [
            project_root / "resources" / platform_name / "icons" / "256.png",
            project_root / "resources" / platform_name / "icons" / "512.png",
            project_root / "resources" / platform_name / "icons" / "1024.png",
            project_root / "resources" / platform_name / "icon.png",
            project_root / "resources" / "base" / "icons" / "256.png",
            project_root / "resources" / "base" / "icons" / "512.png",
            project_root / "resources" / "base" / "icons" / "icon.png",
            project_root / "resources" / "base" / "icon.png",
            project_root / "resources" / "icon.png",
            project_root / "icon.png",
        ]

        png_source = next((p for p in png_candidates if p.exists()), None)
        if png_source:
            if platform_name == "windows":
                try:
                    ico_target = build_dir / "Icon.ico"
                    build_dir.mkdir(parents=True, exist_ok=True)
                    img = Image.open(png_source)
                    img.save(
                        ico_target,
                        format="ICO",
                        sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)],
                    )
                    return ico_target
                except Exception:
                    return None
            else:
                return png_source

        return None

    def _remove_unwanted_pyinstaller_files(self, app_bundle: Path) -> None:
        """Removes unwanted folders like include, lib, lib2to3 inside Contents/Resources."""
        contents_res = app_bundle / "Contents" / "Resources"
        if not contents_res.exists():
            return

        for unwanted in ("include", "lib", "lib2to3"):
            target = contents_res / unwanted
            if target.exists() or target.is_symlink():
                try:
                    if target.is_dir() and not target.is_symlink():
                        shutil.rmtree(target, ignore_errors=True)
                    else:
                        os.unlink(target)
                except OSError:
                    pass

    def _copy_mac_resources(self, project_root: Path, app_bundle: Path, manifest: FreezeManifest) -> None:
        """Copies resources to Contents/Resources inside the .app bundle."""
        if manifest.protect_resources:
            return

        res_dest = app_bundle / "Contents" / "Resources"
        res_dest.mkdir(parents=True, exist_ok=True)

        resource_sources = [
            project_root / "resources",
            project_root / "src" / "main" / "resources",
            project_root / "src" / "freeze",
        ]

        for src_dir in resource_sources:
            if src_dir.exists() and src_dir.is_dir():
                for item in src_dir.iterdir():
                    dest = res_dest / item.name
                    if item.is_dir():
                        shutil.copytree(item, dest, dirs_exist_ok=True)
                    else:
                        shutil.copy2(item, dest)

    def _override_kivy_icons_in_output(self, output_dir: Path, project_root: Path) -> None:
        """Overwrites Kivy default logo files inside dist output directory with user's custom icon."""
        if not output_dir.exists():
            return

        target_plat = "mac" if sys.platform == "darwin" else ("windows" if sys.platform == "win32" else "linux")

        icon_candidates = [
            # Platform specific (mac/linux/windows)
            project_root / "resources" / target_plat / "icons" / "1024.png",
            project_root / "resources" / target_plat / "icons" / "512.png",
            project_root / "resources" / target_plat / "icons" / "256.png",
            project_root / "resources" / target_plat / "icons" / "128.png",
            project_root / "resources" / target_plat / "icons" / "64.png",
            project_root / "resources" / target_plat / "icons" / "32.png",
            project_root / "resources" / target_plat / "icon.png",
            # Base fallback
            project_root / "resources" / "base" / "icons" / "256.png",
            project_root / "resources" / "base" / "icons" / "128.png",
            project_root / "resources" / "base" / "icons" / "64.png",
            project_root / "resources" / "base" / "icons" / "32.png",
            project_root / "resources" / "base" / "icons" / "24.png",
            project_root / "resources" / "base" / "icons" / "16.png",
            project_root / "resources" / "base" / "icon.png",
            # Flat resources folder
            project_root / "resources" / "icons" / "512.png",
            project_root / "resources" / "icons" / "256.png",
            project_root / "resources" / "icons" / "128.png",
            project_root / "resources" / "icons" / "64.png",
            project_root / "resources" / "icons" / "32.png",
            project_root / "resources" / "icons" / "icon.png",
            project_root / "resources" / "icon.png",
            project_root / "icon.png",
        ]

        user_icon = next((c for c in icon_candidates if c.is_file()), None)
        if not user_icon:
            return

        for logo_dir in output_dir.rglob("logo"):
            if logo_dir.is_dir() and ("kivy" in str(logo_dir).lower()):
                for logo_file in logo_dir.glob("kivy-icon-*"):
                    try:
                        shutil.copy2(user_icon, logo_file)
                    except Exception:
                        pass

    def _codesign_mac_bundle(self, app_bundle: Path) -> None:
        """Applies ad-hoc code signing to the macOS .app bundle using codesign after setting permissions and clearing quarantine."""
        if not app_bundle.exists() or sys.platform != "darwin":
            return
        try:
            # 1. Delete stale _CodeSignature directory before re-signing modified bundle
            code_sig_dir = app_bundle / "Contents" / "_CodeSignature"
            if code_sig_dir.exists():
                shutil.rmtree(code_sig_dir, ignore_errors=True)

            # 2. Ensure executable permissions on binaries in Contents/MacOS and shared libraries
            for item in app_bundle.rglob("*"):
                if item.is_file() and not item.is_symlink():
                    if "Contents/MacOS/" in str(item) or item.suffix in (".dylib", ".so"):
                        try:
                            os.chmod(item, 0o755)
                        except Exception:
                            pass

            # 3. Clear all extended attributes (quarantine, provenance, etc.)
            subprocess.run(
                ["xattr", "-cr", str(app_bundle)],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )

            # 4. Ad-hoc code sign bundle cleanly
            subprocess.run(
                ["codesign", "--force", "--deep", "--timestamp=none", "-s", "-", str(app_bundle)],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )
        except Exception:
            pass

    def _purge_excluded_binaries(self, output_dir: Path, manifest: FreezeManifest) -> None:
        """Purges C++ frameworks, shared objects (.so), DLLs, dylibs, and binary plugins for modules listed in exclude_modules and exclude_binaries."""
        if not output_dir.exists():
            return

        exclude_modules = set(manifest.optimization.exclude_modules or [])
        exclude_binaries = list(manifest.optimization.exclude_binaries or [])
        exclude_plugins = set(manifest.optimization.exclude_plugins or [])

        # Mapping of standard Python/Qt modules to their companion shared libraries / DLLs / pyds
        COMPANION_BINARIES = {
            "ssl": ["_ssl.pyd", "libssl*.dll", "libcrypto*.dll", "libssl.so*", "libcrypto.so*"],
            "_ssl": ["_ssl.pyd", "libssl*.dll", "libcrypto*.dll", "libssl.so*", "libcrypto.so*"],
            "hashlib": ["_hashlib.pyd", "libcrypto*.dll", "libcrypto.so*"],
            "_hashlib": ["_hashlib.pyd", "libcrypto*.dll", "libcrypto.so*"],
            "bz2": ["_bz2.pyd", "*bz2*.dll", "libbz2.so*", "libbz2*.so*"],
            "_bz2": ["_bz2.pyd", "*bz2*.dll", "libbz2.so*", "libbz2*.so*"],
            "lzma": ["_lzma.pyd", "*lzma*.dll", "liblzma.so*", "liblzma*.so*"],
            "_lzma": ["_lzma.pyd", "*lzma*.dll", "liblzma.so*", "liblzma*.so*"],
            "xml": ["pyexpat.pyd", "*expat*.dll", "libexpat.so*", "libexpat*.so*"],
            "pyexpat": ["pyexpat.pyd", "*expat*.dll", "libexpat.so*", "libexpat*.so*"],
            "ctypes": ["_ctypes.pyd", "ffi.dll", "*ffi*.dll", "libffi.so*", "libffi*.so*"],
            "_ctypes": ["_ctypes.pyd", "ffi.dll", "*ffi*.dll", "libffi.so*", "libffi*.so*"],
            "asyncio": ["_asyncio.pyd", "_overlapped.pyd"],
            "_asyncio": ["_asyncio.pyd", "_overlapped.pyd"],
            "multiprocessing": ["_multiprocessing.pyd"],
            "_multiprocessing": ["_multiprocessing.pyd"],
            "decimal": ["_decimal.pyd"],
            "_decimal": ["_decimal.pyd"],
            "socket": ["_socket.pyd"],
            "_socket": ["_socket.pyd"],
            "select": ["select.pyd"],
            "sqlite3": ["_sqlite3.pyd", "*sqlite3*.dll", "libsqlite3.so*"],
            "_sqlite3": ["_sqlite3.pyd", "*sqlite3*.dll", "libsqlite3.so*"],
            "opengl": ["opengl32sw.dll"],
            "qtopengl": ["opengl32sw.dll", "Qt6OpenGL.dll", "libQt6OpenGL.so*"],
            "pyside6.qtopengl": ["opengl32sw.dll", "Qt6OpenGL.dll", "libQt6OpenGL.so*"],
            "pyqt6.qtopengl": ["opengl32sw.dll", "Qt6OpenGL.dll", "libQt6OpenGL.so*"],
        }

        # Expand companion binaries from excluded modules
        for mod in exclude_modules:
            mod_lower = mod.lower()
            if mod_lower in COMPANION_BINARIES:
                exclude_binaries.extend(COMPANION_BINARIES[mod_lower])
            short = mod_lower.split(".")[-1]
            if short in COMPANION_BINARIES:
                exclude_binaries.extend(COMPANION_BINARIES[short])

        # Extract lowercase keywords for matching (e.g. 'virtualkeyboard', 'webengine', 'pdf', 'qml', 'quick')
        keywords: set[str] = set()
        exact_targets: set[str] = set()

        for exc in exclude_modules:
            parts = exc.split(".")
            short_name = parts[-1]  # e.g., QtWebEngineCore, Qt3DCore, QtQuick, QtQml, QtVirtualKeyboard
            qt_suffix = short_name[2:] if short_name.startswith("Qt") and len(short_name) > 2 else short_name

            kw = qt_suffix.lower()
            if kw:
                keywords.add(kw)
            if short_name.lower():
                keywords.add(short_name.lower())

            # Specific exact targets
            exact_targets.update([
                f"{short_name}.framework".lower(),
                f"{short_name}.abi3.so".lower(),
                f"{short_name}.so".lower(),
                f"{short_name}.pyd".lower(),
                f"{short_name}.dylib".lower(),
                f"{short_name}.dll".lower(),
            ])

        # Walk bottom-up to safely delete files and subdirectories
        PROTECTED_CORE = (
            manifest.app_name.lower(),
            "qtcore", "qtgui", "qtwidgets", "shiboken", "qwindows",
            "python3", "vcruntime", "base_library", "pyiboot01"
        )

        for root, dirs, files in os.walk(str(output_dir), topdown=False):
            # Never purge anything directly inside Contents/MacOS
            if "contents/macos" in root.lower():
                continue

            # Check files and symlinks
            for f in files:
                file_p = Path(root) / f
                f_lower = f.lower()

                # Core runtime files and main application executable are strictly protected
                if f_lower == manifest.app_name.lower() or any(prot in f_lower for prot in PROTECTED_CORE):
                    continue

                should_delete = False
                if f_lower in exact_targets:
                    should_delete = True
                elif any(fnmatch.fnmatch(f_lower, pat.lower()) for pat in exclude_binaries):
                    should_delete = True
                else:
                    for kw in keywords:
                        if kw in f_lower:
                            should_delete = True
                            break

                if should_delete:
                    try:
                        if file_p.is_symlink() or file_p.is_file():
                            os.unlink(file_p)
                        elif file_p.is_dir():
                            shutil.rmtree(file_p, ignore_errors=True)
                    except OSError:
                        pass

            # Check directory names
            for d in dirs:
                dir_p = Path(root) / d
                d_lower = d.lower()

                should_delete = False
                for kw in keywords:
                    if kw in d_lower:
                        should_delete = True
                        break

                if should_delete:
                    try:
                        if dir_p.is_symlink():
                            os.unlink(dir_p)
                        elif dir_p.is_dir():
                            shutil.rmtree(dir_p, ignore_errors=True)
                    except OSError:
                        pass

        # 1. Purge translations folders (Windows PySide6/translations, Linux PySide6/Qt/translations, PyQt, etc.)
        if manifest.optimization.remove_translations or manifest.optimization.clean_build:
            for trans_dir in list(output_dir.rglob("translations")):
                if trans_dir.exists() and trans_dir.is_dir():
                    shutil.rmtree(trans_dir, ignore_errors=True)

        # 2. Purge QML directory if QML was excluded
        if any("qml" in k or "quick" in k for k in keywords):
            for qml_dir in list(output_dir.rglob("qml")):
                if qml_dir.exists() and qml_dir.is_dir():
                    shutil.rmtree(qml_dir, ignore_errors=True)

        # 3. Purge Qt plugins based on exclude_plugins configuration or clean_build defaults
        for plugins_dir in list(output_dir.rglob("plugins")):
            if not plugins_dir.exists() or not plugins_dir.is_dir():
                continue

            # If exclude_plugins is configured
            if exclude_plugins:
                if "*" in exclude_plugins or "all" in exclude_plugins:
                    # Remove all plugin categories EXCEPT 'platforms' (needed to show UI window)
                    for item in plugins_dir.iterdir():
                        if item.name.lower() != "platforms":
                            if item.is_dir():
                                shutil.rmtree(item, ignore_errors=True)
                            else:
                                try:
                                    os.unlink(item)
                                except OSError:
                                    pass
                else:
                    for plug_pattern in exclude_plugins:
                        for item in plugins_dir.iterdir():
                            if fnmatch.fnmatch(item.name.lower(), plug_pattern.lower()):
                                if item.name.lower() != "platforms":
                                    if item.is_dir():
                                        shutil.rmtree(item, ignore_errors=True)
                                    else:
                                        try:
                                            os.unlink(item)
                                        except OSError:
                                            pass

            # If clean_build is active, remove safe-to-purge desktop plugins by default
            if manifest.optimization.clean_build:
                for default_plug in (
                    "generic",
                    "egldeviceintegrations",
                    "wayland-decoration-client",
                    "wayland-graphics-integration-client",
                    "wayland-shell-integration",
                ):
                    plug_p = plugins_dir / default_plug
                    if plug_p.exists():
                        shutil.rmtree(plug_p, ignore_errors=True)

        # 4. Purge package metadata directories (*.dist-info) in clean_build
        if manifest.optimization.clean_build:
            for dist_info in list(output_dir.rglob("*.dist-info")):
                if dist_info.exists() and dist_info.is_dir():
                    shutil.rmtree(dist_info, ignore_errors=True)

        # Cleanup Pass: Purge any broken symlinks remaining anywhere in output_dir (except Contents/MacOS and main binary)
        for root, dirs, files in os.walk(str(output_dir), topdown=False):
            if "contents/macos" in root.lower():
                continue
            for name in files + dirs:
                if name.lower() == manifest.app_name.lower():
                    continue
                p = Path(root) / name
                if p.is_symlink() and not p.exists():
                    try:
                        os.unlink(p)
                    except OSError:
                        pass
