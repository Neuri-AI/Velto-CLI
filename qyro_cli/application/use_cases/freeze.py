"""
qyro.application.use_cases.freeze
Orchestrates freezing desktop applications following Clean Architecture principles.
Supports build.json manifests, settings/base.json, platform-specific JSONs,
UAC elevation, onefile/onedir modes, debug flags, performance optimizations,
and bindings (PyQt5/6, PySide6/2, Kivy, Tkinter).
"""

import datetime
import json
import platform
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from qyro_cli.application.ports import (
    BinaryOptimizerPort,
    FreezerPort,
    SettingsPort,
    UserInteractionPort,
)
from qyro_cli.domain.build import (
    BuildArtifact,
    BundleMode,
    DebugConfig,
    FreezeManifest,
    OptimizationConfig,
    UACConfig,
    UACLevel,
)


class FreezeDesktopUseCase:
    """
    Main use case for building and freezing desktop executables.
    Merges configuration hierarchically:
    1. settings/base.json
    2. settings/<platform>.json or settings/release.json
    3. build.json (or custom manifest)
    4. CLI argument overrides (--debug, --onefile, --uac, etc.)
    """

    def __init__(
        self,
        freezer: FreezerPort,
        settings_repo: SettingsPort,
        optimizer: BinaryOptimizerPort,
        ui: UserInteractionPort,
    ):
        self._freezer = freezer
        self._settings = settings_repo
        self._optimizer = optimizer
        self._ui = ui

    def execute(
        self,
        project_root: Optional[Path] = None,
        bundle: Optional[str] = None,
        profile: str = "release",
        debug: Optional[bool] = None,
        console: Optional[bool] = None,
        uac: Optional[bool] = None,
        clean: Optional[bool] = None,
        extra_args: Optional[List[str]] = None,
        interactive: bool = False,
    ) -> BuildArtifact:
        root = (project_root or Path.cwd()).resolve()

        # Step 1: Detect Host / Target Platform
        current_platform = self._detect_platform()
        active_profile = profile.lower() if profile else current_platform

        # Normalize macos / mac / windows / win / linux profile names
        target_override = None
        if active_profile in ("mac", "macos", "darwin", "osx"):
            target_override = "mac"
        elif active_profile in ("windows", "win", "win32"):
            target_override = "windows"
        elif active_profile in ("linux", "gnu"):
            target_override = "linux"

        # Step 2: Activate platform and profile in SettingsPort if supported
        if hasattr(self._settings, "activate_profile"):
            self._settings.activate_profile(current_platform)
            if active_profile and active_profile != current_platform:
                self._settings.activate_profile(active_profile)

        # Step 3: Parse and merge settings and build.json
        manifest = self._load_manifest(
            root=root,
            platform_name=target_override or current_platform,
            cli_bundle=bundle,
            cli_debug=debug,
            cli_console=console,
            cli_uac=uac,
            cli_clean=clean,
        )

        # Setup build log tracker
        build_dir = root / "build"
        build_dir.mkdir(parents=True, exist_ok=True)
        log_entries: List[str] = []

        def log_step(msg: str) -> None:
            ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            log_entries.append(f"[{ts}] {msg}")

        log_step(f"Qyro Freeze started for project '{root.name}'")
        log_step(f"Target platform: {target_override or current_platform} (Host: {current_platform})")
        log_step(f"Binding: {manifest.binding}, Bundle mode: {manifest.bundle_mode.value}")
        log_step(f"Bytecode Opt: {manifest.optimization.bytecode_opt}, UPX: {manifest.optimization.upx_enabled}")
        if manifest.optimization.exclude_binaries:
            log_step(f"Excluded binaries: {', '.join(manifest.optimization.exclude_binaries)}")
        if manifest.optimization.exclude_plugins:
            log_step(f"Excluded plugins: {', '.join(manifest.optimization.exclude_plugins)}")
        if manifest.optimization.exclude_modules:
            log_step(f"Excluded modules ({len(manifest.optimization.exclude_modules)} total)")

        # Step 4: Validate manifest
        manifest.validate()
        log_step("Manifest validated successfully")

        # Step 5: Interactive confirmation if requested
        if interactive:
            self._show_interactive_review(manifest)

        # Step 6: Inform user
        self._ui.info(
            f"🚀 [bold #ff8c00]Qyro Freeze[/bold #ff8c00] :: Preparing binary for "
            f"[bold #fff]{manifest.app_name}[/bold #fff] ({manifest.target_platform})"
        )

        # Step 7: Execute Freezing via FreezerPort
        log_step("Invoking PyInstaller packaging engine...")
        artifact = self._freezer.freeze(
            project_root=root,
            manifest=manifest,
            extra_args=extra_args,
        )
        log_step(f"PyInstaller packaging completed in {artifact.duration_seconds:.2f}s")
        log_step(f"Intermediate executable generated: {artifact.executable_path}")

        # Step 8: Apply binary optimizations (UPX, stripping)
        log_step("Applying post-freeze binary optimizations...")
        self._optimizer.optimize(artifact, manifest.optimization)
        log_step("Binary optimizations completed")

        # Step 9: Save build log to build/build_log.txt
        log_step(f"Build finished successfully. Final output: {artifact.output_dir} ({artifact.size_bytes:,} bytes)")
        try:
            (build_dir / "build_log.txt").write_text("\n".join(log_entries) + "\n", encoding="utf-8")
        except Exception:
            pass

        # Step 10: Report results cleanly
        self._report_success(artifact)

        return artifact

    def _detect_platform(self) -> str:
        if sys.platform.startswith("win"):
            return "windows"
        elif sys.platform.startswith("darwin"):
            return "mac"
        return "linux"

    def _load_manifest(
        self,
        root: Path,
        platform_name: str,
        cli_bundle: Optional[str],
        cli_debug: Optional[bool],
        cli_console: Optional[bool] = None,
        cli_uac: Optional[bool] = None,
        cli_clean: Optional[bool] = None,
    ) -> FreezeManifest:
        # Load from build.json if present
        build_json_path = root / "build.json"
        build_data: Dict[str, Any] = {}
        if build_json_path.exists():
            try:
                build_data = json.loads(build_json_path.read_text(encoding="utf-8"))
            except Exception as e:
                self._ui.warning(f"Failed to parse build.json: {e}")

        # Base properties with fallback to settings
        app_name = (
            build_data.get("app_name")
            or self._settings.get_optional("app_name", root.name)
        )
        author = (
            build_data.get("author")
            or self._settings.get_optional("author", "Developer")
        )
        version = (
            build_data.get("version")
            or self._settings.get_optional("version", "1.0.0")
        )
        entry_point = (
            build_data.get("entry_point")
            or self._settings.get_optional("entry_point", "main.py")
        )
        binding = (
            build_data.get("binding")
            or self._settings.get_optional("binding", "PySide6")
        )

        # Target platform
        target_platform = build_data.get("platform", platform_name)

        # Bundle mode (CLI overrides JSON and settings)
        bundle_str = (
            cli_bundle
            or build_data.get("bundle")
            or build_data.get("bundle_mode")
            or self._settings.get_optional("bundle_mode")
            or self._settings.get_optional("bundle", "onedir")
        )
        bundle_mode = BundleMode.parse(bundle_str)

        # UAC Config (checks build_data first, then settings/<platform>.json)
        uac_raw = build_data.get("uac") or self._settings.get_optional("uac", {})
        if isinstance(uac_raw, bool):
            uac_level = UACLevel.REQUIRE_ADMINISTRATOR if uac_raw else UACLevel.AS_INVOKER
            uac_uiaccess = False
        elif isinstance(uac_raw, dict):
            uac_level = UACLevel.parse(uac_raw.get("level", "asInvoker"))
            uac_uiaccess = bool(uac_raw.get("ui_access", False))
        else:
            uac_level = UACLevel.AS_INVOKER
            uac_uiaccess = False

        if cli_uac is not None:
            uac_level = UACLevel.REQUIRE_ADMINISTRATOR if cli_uac else UACLevel.AS_INVOKER

        uac_config = UACConfig(level=uac_level, ui_access=uac_uiaccess)

        # Debug Config (checks build_data first, then settings/<platform>.json)
        debug_raw = build_data.get("debug") or self._settings.get_optional("debug", {})
        if isinstance(debug_raw, bool):
            dbg_enabled = debug_raw
            dbg_console = debug_raw
            dbg_unstripped = False
            dbg_bootloader = False
        elif isinstance(debug_raw, dict):
            dbg_enabled = bool(debug_raw.get("enabled", False))
            dbg_console = bool(debug_raw.get("console", debug_raw.get("console_window", False)))
            dbg_unstripped = bool(debug_raw.get("unstripped", False))
            dbg_bootloader = bool(debug_raw.get("bootloader_debug", False))
        else:
            dbg_enabled = False
            dbg_console = False
            dbg_unstripped = False
            dbg_bootloader = False

        if cli_debug is not None:
            dbg_enabled = cli_debug
            if cli_debug:
                dbg_console = True

        if cli_console is not None:
            dbg_console = cli_console
            if cli_console:
                dbg_enabled = True

        debug_config = DebugConfig(
            enabled=dbg_enabled,
            console_window=dbg_console,
            unstripped=dbg_unstripped,
            bootloader_debug=dbg_bootloader,
        )

        # Optimization Config (checks build_data first, then settings/<platform>.json)
        opt_raw = build_data.get("optimization") or self._settings.get_optional("optimization", {})
        clean_build = (
            cli_clean
            if cli_clean is not None
            else bool(opt_raw.get("clean_build", True))
        )
        opt_config = OptimizationConfig(
            upx_enabled=bool(opt_raw.get("upx_enabled", True)),
            upx_level=int(opt_raw.get("upx_level", 9)),
            upx_dir=opt_raw.get("upx_dir", None),
            upx_excludes=opt_raw.get("upx_excludes", [
                "vcruntime140.dll", "python3*.dll", "sdl2.dll", "glew32.dll", "kivy*.pyd"
            ]),
            bytecode_opt=int(opt_raw.get("bytecode_opt", 1)),
            strip_binaries=bool(opt_raw.get("strip_binaries", True)),
            exclude_modules=opt_raw.get("exclude_modules", ["unittest", "test", "pydoc"]),
            exclude_binaries=opt_raw.get("exclude_binaries", []),
            exclude_plugins=opt_raw.get("exclude_plugins", []),
            remove_translations=bool(opt_raw.get("remove_translations", True)),
            clean_build=clean_build,
        )

        # Hidden imports and resources
        hidden_imports = list(build_data.get("hidden_imports", []))
        base_hidden = self._settings.get_optional("hidden_imports", [])
        if isinstance(base_hidden, list):
            hidden_imports.extend(base_hidden)
        # Deduplicate while preserving order
        dedup_hidden = list(dict.fromkeys(hidden_imports))

        icon_path = build_data.get("icon") or self._settings.get_optional("icon", None)
        mac_bundle_identifier = (
            build_data.get("mac_bundle_identifier")
            or build_data.get("bundle_identifier")
            or self._settings.get_optional("mac_bundle_identifier")
            or self._settings.get_optional("bundle_identifier")
        )
        extra_args = list(build_data.get("extra_args", []))
        settings_extra = (
            self._settings.get_optional("extra_pyinstaller_args", [])
            or self._settings.get_optional("extra_args", [])
        )
        if isinstance(settings_extra, list):
            extra_args.extend(settings_extra)

        if target_platform in ("mac", "macos", "darwin", "osx"):
            self._append_macos_signing_args(extra_args, root)

        paths = list(build_data.get("paths", []))
        settings_paths = self._settings.get_optional("paths", [])
        if isinstance(settings_paths, list):
            paths.extend(settings_paths)

        collect_all = list(build_data.get("collect_all", []))
        settings_collect_all = self._settings.get_optional("collect_all", [])
        if isinstance(settings_collect_all, list):
            collect_all.extend(settings_collect_all)

        protection_raw = (
            build_data.get("resource_protection")
            or build_data.get("protected_resources")
            or self._settings.get_optional("resource_protection", {})
            or self._settings.get_optional("protected_resources", {})
        )
        if isinstance(protection_raw, dict):
            protect_resources = bool(protection_raw.get("enabled", False))
            protected_settings_dir = str(protection_raw.get("settings_dir", "settings"))
            protected_resources_dir = str(protection_raw.get("resources_dir", "resources"))
            protected_bundle_relative_path = str(
                protection_raw.get("bundle_path", ".qyro/resources.pak")
            )
        else:
            protect_resources = bool(protection_raw)
            protected_settings_dir = "settings"
            protected_resources_dir = "resources"
            protected_bundle_relative_path = ".qyro/resources.pak"

        return FreezeManifest(
            app_name=app_name,
            author=author,
            version=version,
            entry_point=entry_point,
            target_platform=target_platform,
            bundle_mode=bundle_mode,
            binding=binding,
            icon_path=icon_path,
            mac_bundle_identifier=mac_bundle_identifier,
            uac=uac_config,
            debug=debug_config,
            optimization=opt_config,
            hidden_imports=dedup_hidden,
            extra_pyinstaller_args=extra_args,
            paths=paths,
            collect_all=collect_all,
            protect_resources=protect_resources,
            protected_settings_dir=protected_settings_dir,
            protected_resources_dir=protected_resources_dir,
            protected_bundle_relative_path=protected_bundle_relative_path,
        )

    def _append_macos_signing_args(self, extra_args: list[str], project_root: Path) -> None:
        sign_settings = self._settings.get_optional("sign", {})
        if not isinstance(sign_settings, dict):
            sign_settings = {}

        mac_sign_settings = sign_settings.get("mac", {})
        if not isinstance(mac_sign_settings, dict):
            mac_sign_settings = {}

        identity = str(
            mac_sign_settings.get("identity")
            or self._settings.get_optional("mac_sign_identity", "")
        ).strip()

        entitlements = str(
            mac_sign_settings.get("entitlements")
            or self._settings.get_optional("mac_sign_entitlements", "")
        ).strip()

        target_arch = str(
            mac_sign_settings.get("target_architecture")
            or self._settings.get_optional("mac_target_architecture", "")
        ).strip()

        def has_arg(flag: str) -> bool:
            return any(arg == flag or arg.startswith(flag + "=") for arg in extra_args)

        if identity and not has_arg("--codesign-identity"):
            if self._is_placeholder_identity(identity):
                self._ui.warning(
                    "macOS codesign identity looks like a template value. "
                    "Continuing build without --codesign-identity."
                )
            elif self._is_available_codesign_identity(identity):
                extra_args.extend(["--codesign-identity", identity])
            else:
                self._ui.warning(
                    "macOS codesign identity was not found in the local keychain. "
                    "Continuing build without --codesign-identity."
                )

        if entitlements and not has_arg("--osx-entitlements-file"):
            entitlements_path = Path(entitlements)
            if not entitlements_path.is_absolute():
                entitlements_path = (project_root / entitlements_path).resolve()

            if entitlements_path.exists():
                extra_args.extend(["--osx-entitlements-file", str(entitlements_path)])
            else:
                self._ui.warning(
                    "macOS entitlements file not found. "
                    "Continuing build without --osx-entitlements-file."
                )

        if target_arch and not has_arg("--target-arch") and not has_arg("--target-architecture"):
            effective_arch = self._resolve_macos_target_arch(target_arch)
            if effective_arch:
                extra_args.extend(["--target-arch", effective_arch])

    def _is_placeholder_identity(self, identity: str) -> bool:
        normalized = identity.strip().lower()
        if not normalized:
            return True

        placeholders = (
            "your name",
            "teamid",
            "example",
            "developer id application: your name (teamid)",
        )
        return any(token in normalized for token in placeholders)

    def _is_available_codesign_identity(self, identity: str) -> bool:
        try:
            result = subprocess.run(
                ["security", "find-identity", "-v", "-p", "codesigning"],
                capture_output=True,
                text=True,
                check=False,
            )
        except Exception:
            return False

        if result.returncode != 0:
            return False

        output = f"{result.stdout}\n{result.stderr}".lower()
        return identity.strip().lower() in output

    def _resolve_macos_target_arch(self, configured_arch: str) -> str:
        normalized = configured_arch.strip().lower()
        if not normalized:
            return ""

        # universal2 requires all collected binaries to be fat binaries.
        # Most local environments are single-arch, so default to native arch
        # for developer builds unless user explicitly overrides via extra args.
        if normalized == "universal2":
            machine = platform.machine().strip().lower()
            if machine in ("arm64", "aarch64"):
                native = "arm64"
            elif machine in ("x86_64", "amd64", "x64"):
                native = "x86_64"
            else:
                native = ""

            if native:
                self._ui.warning(
                    "macOS target_architecture=universal2 requires universal/fat dependencies. "
                    f"Using native architecture '{native}' for this build."
                )
                return native

            self._ui.warning(
                "Could not determine native macOS architecture. "
                "Continuing build without --target-arch."
            )
            return ""

        return normalized

    def _show_interactive_review(self, manifest: FreezeManifest) -> None:
        fields = {
            "Application": manifest.app_name,
            "Target OS": manifest.target_platform,
            "GUI Binding": manifest.binding,
            "Entry Point": manifest.entry_point,
            "Bundle Format": manifest.bundle_mode.value.upper(),
            "Debug Mode": "ENABLED (Console open)" if manifest.debug.enabled else "Disabled (Windowed)",
            "Windows UAC": manifest.uac.level.value,
            "UPX Compression": f"Level {manifest.optimization.upx_level}" if manifest.optimization.upx_enabled else "Disabled",
        }
        self._ui.show_summary("Freeze Configuration Review", fields)
        if not self._ui.confirm("Proceed with build?", default=True):
            self._ui.info("Build cancelled by user.")
            sys.exit(0)

    def _report_success(self, artifact: BuildArtifact) -> None:
        size_mb = artifact.size_bytes / (1024 * 1024)
        self._ui.success("\n✨ [bold green]Application frozen successfully![/bold green]")
        self._ui.info(f"  • [bold]Output Location:[/bold] [#fdba74]{artifact.output_dir}[/#fdba74]")
        self._ui.info(f"  • [bold]Executable:[/bold] [#fdba74]{artifact.executable_path.name}[/#fdba74]")
        self._ui.info(f"  • [bold]Size:[/bold] {size_mb:.2f} MB ({artifact.size_bytes:,} bytes)")
        self._ui.info(f"  • [bold]Duration:[/bold] {artifact.duration_seconds:.2f}s")
        if artifact.uac_applied:
            self._ui.info("  • [bold]UAC Manifest:[/bold] Administrator Privilege Elevation Enabled (requireAdministrator)")
        if artifact.debug_mode:
            self._ui.info("  • [bold]Debug:[/bold] Active console attached for stdout/stderr debugging")
            if artifact.manifest_used.target_platform.lower() in ("mac", "macos", "darwin"):
                self._ui.info(f"    [dim]macOS Log Stream: log stream --process {artifact.manifest_used.app_name}[/dim]")

        # Optimizations reported
        opt = artifact.manifest_used.optimization
        if opt.bytecode_opt > 0:
            self._ui.info(f"  • [bold]Bytecode Opt:[/bold] Level {opt.bytecode_opt} (-O{opt.bytecode_opt})")

        is_win = artifact.manifest_used.target_platform.lower() == "windows"
        if opt.strip_binaries:
            if is_win:
                self._ui.info("  • [bold]Strip Binaries:[/bold] [dim]Skipped on Windows (PE format - not applicable)[/dim]")
            else:
                self._ui.info("  • [bold]Strip Binaries:[/bold] [green]Active[/green] (symbol tables stripped)")

        if opt.upx_enabled and not (artifact.manifest_used.target_platform.lower() in ("mac", "macos", "darwin")):
            upx_bin = "upx.exe" if is_win else "upx"
            upx_found = bool(shutil.which("upx"))
            if not upx_found:
                if opt.upx_dir and (Path(opt.upx_dir) / upx_bin).exists():
                    upx_found = True
                else:
                    py_prefix = Path(sys.prefix)
                    for search_cand in [py_prefix / "Scripts", py_prefix / "bin", py_prefix]:
                        if (search_cand / upx_bin).exists():
                            upx_found = True
                            break
            if upx_found:
                self._ui.info(f"  • [bold]UPX Compression:[/bold] [green]Active[/green] (Level {opt.upx_level})")
            else:
                self._ui.info("  • [bold]UPX Compression:[/bold] [yellow]Inactive (UPX binary not found in PATH/environment)[/yellow]")

        # Report build logs location
        build_log_file = artifact.output_dir.parent / "build_log.txt"
        if not build_log_file.exists():
            build_log_file = artifact.output_dir / "build_log.txt"
        if build_log_file.exists():
            self._ui.info(f"  • [bold #38bdf8]Build Log:[/bold #38bdf8] {build_log_file}")
