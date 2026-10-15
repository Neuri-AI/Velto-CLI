
"""
qyro.adapters.freeze.framework_hooks
Framework-specific resolution hooks for Qyro's Freeze system.
Handles PyQt5, PyQt6, PySide2, PySide6, Kivy, Tkinter hidden imports and resource data,
integrating with Qyro's domain Binding and Project entities.
"""

from pathlib import Path
from typing import Callable, List

from qyro_cli.application.ports import FrameworkHookResolverPort
from qyro_cli.domain.build import FreezeManifest
from qyro_cli.domain.project import Binding, AddonModule


class FrameworkHookResolver(FrameworkHookResolverPort):
    """
    Intelligently inspects project files and domain Binding to resolve:
    - PyInstaller hidden imports
    - Collect data / resource paths
    - Binary dependencies (SDL2, GLEW, etc. for Kivy)
    - Plugin directories for Qt family
    - Common project settings and assets
    """

    _SECRETS_FILE_NAME = "secrets.json"
    _SIGN_FILE_NAME = "sign.json"

    def resolve_args(
        self,
        project_root: Path,
        manifest: FreezeManifest,
    ) -> List[str]:
        # FrameworkFactory imports adapters dynamically. Include the whole
        # adapter package so explicit selection, auto-detection and the
        # headless fallback work in frozen apps. Competing toolkit packages
        # are still excluded below; adapters remain lazy at runtime.
        args: List[str] = ["--collect-submodules", "qyro.adapters.frameworks"]

        binding = Binding.parse(manifest.binding)

        resolvers: dict[Binding, Callable[[Path, FreezeManifest], List[str]]] = {
            Binding.PYSIDE6: self._resolve_pyside6,
            Binding.PYQT6: self._resolve_pyqt6,
            Binding.PYQT5: self._resolve_pyqt5,
            Binding.PYSIDE2: self._resolve_pyside2,
            Binding.KIVY: self._resolve_kivy,
            Binding.TKINTER: self._resolve_tkinter,
        }

        resolver = resolvers.get(binding)

        if resolver:
            args.extend(resolver(project_root, manifest))

        args.extend(self._resolve_competing_framework_exclusions(binding, manifest))

        args.extend(self._resolve_addons(manifest))
        args.extend(self._resolve_project_assets(project_root, manifest))

        return args

    def _resolve_qt(
        self,
        package: str,
        *,
        sip: bool = False,
    ) -> List[str]:
        args = [
            "--hidden-import", package,
            "--hidden-import", f"{package}.QtCore",
            "--hidden-import", f"{package}.QtGui",
            "--hidden-import", f"{package}.QtWidgets",
        ]

        if package == "PySide6":
            args.extend(["--exclude-module", "PySide6.scripts"])

        if package == "PySide2":
            args.extend(["--exclude-module", "PySide2.scripts"])

        if package == "PyQt6":
            args.extend(["--exclude-module", "PyQt6.scripts"])

        if package == "PyQt5":
            args.extend(["--exclude-module", "PyQt5.scripts"])

        if sip:
            args.extend(["--hidden-import", f"{package}.sip"])

        return args

    def _resolve_pyside6(
        self,
        project_root: Path,
        manifest: FreezeManifest,
    ) -> List[str]:
        args = self._resolve_qt("PySide6")

        # Check for QML / Quick in project only if not explicitly excluded
        user_excludes = {m.lower() for m in manifest.optimization.exclude_modules}
        if self._has_qml(project_root):
            if "pyside6.qtquick" not in user_excludes:
                args.extend(["--hidden-import", "PySide6.QtQuick"])
            if "pyside6.qtqml" not in user_excludes:
                args.extend(["--hidden-import", "PySide6.QtQml"])

        return args

    def _resolve_pyqt6(
        self,
        project_root: Path,
        manifest: FreezeManifest,
    ) -> List[str]:
        return self._resolve_qt("PyQt6", sip=True)

    def _resolve_pyqt5(
        self,
        project_root: Path,
        manifest: FreezeManifest,
    ) -> List[str]:
        return self._resolve_qt("PyQt5", sip=True)

    def _resolve_pyside2(
        self,
        project_root: Path,
        manifest: FreezeManifest,
    ) -> List[str]:
        args = self._resolve_qt("PySide2")
        args.extend(["--collect-all", "PySide2", "--collect-all", "shiboken2"])
        return args

    def _resolve_kivy(
        self,
        project_root: Path,
        manifest: FreezeManifest,
    ) -> List[str]:
        args = [
            "--hidden-import", "kivy",
            "--hidden-import", "kivy.app",
            "--hidden-import", "kivy.uix",
            "--hidden-import", "kivy.factory",
            "--hidden-import", "kivy.lang",
            "--hidden-import", "kivy.lang.builder",
            "--hidden-import", "kivy.core.window",
            "--hidden-import", "kivy.core.window.window_sdl2",
            "--hidden-import", "kivy.core.text",
            "--hidden-import", "kivy.core.text.text_sdl2",
            "--hidden-import", "kivy.core.image",
            "--hidden-import", "kivy.core.image.img_sdl2",
            "--hidden-import", "kivy.core.clipboard.clipboard_sdl2",
            "--hidden-import", "kivy.graphics",
            "--hidden-import", "kivy.graphics.cgl",
            "--hidden-import", "kivy.graphics.cgl_backend.cgl_glew",
            "--collect-data", "kivy",
        ]

        # Scan and bundle .kv files (Kivy declarative layout files)
        kv_patterns = [
            "*.kv",
            "views/**/*.kv",
            "components/**/*.kv",
        ]

        collected_kv: set[str] = set()

        for pattern in kv_patterns:
            for path in project_root.glob(pattern):
                if not path.is_file():
                    continue

                relative = path.relative_to(project_root)
                source = path.resolve().as_posix()
                destination = relative.parent.as_posix()

                if destination == ".":
                    destination = "."

                collected_kv.add(
                    f"{source}{self._data_separator(manifest)}{destination}"
                )

        for item in sorted(collected_kv):
            args.extend(["--add-data", item])

        # Attach Qyro's bundled Kivy runtime hook to set custom window icon
        kivy_rthook = Path(__file__).parent / "hooks" / "rthook_kivy_icon.py"
        if kivy_rthook.exists():
            args.extend(["--runtime-hook", kivy_rthook.as_posix()])

        return args

    def _resolve_tkinter(
        self,
        project_root: Path,
        manifest: FreezeManifest,
    ) -> List[str]:
        args = [
            "--hidden-import", "_tkinter",
            "--hidden-import", "tkinter",
            "--hidden-import", "tkinter.ttk",
            "--hidden-import", "tkinter.messagebox",
            "--hidden-import", "tkinter.filedialog",
            "--collect-data", "tkinter",
        ]

        import sys
        sep = self._data_separator(manifest)
        py_prefix = Path(sys.prefix)

        tcl_tk_candidates = [
            (py_prefix / "lib" / "tcl8.6", "tcl8.6"),
            (py_prefix / "lib" / "tk8.6", "tk8.6"),
            (py_prefix / "lib" / "tcl8.5", "tcl8.5"),
            (py_prefix / "lib" / "tk8.5", "tk8.5"),
            (py_prefix / "tcl", "tcl"),
            (py_prefix / "tk", "tk"),
        ]

        for src_path, target_name in tcl_tk_candidates:
            if src_path.is_dir():
                args.extend(["--add-data", f"{src_path.as_posix()}{sep}{target_name}"])

        return args

    def _resolve_addons(
        self,
        manifest: FreezeManifest,
    ) -> List[str]:
        """Resolves PyInstaller args for registered addons."""
        args: List[str] = []
        addons = {str(addon).lower() for addon in (manifest.addons or [])}

        if AddonModule.REQUESTS.value.lower() in addons:
            args.extend([
                "--hidden-import", "requests",
                "--hidden-import", "urllib3",
                "--hidden-import", "certifi",
                "--collect-data", "certifi",
            ])

        if "kivymd" in addons:
            args.extend([
                "--hidden-import", "kivymd",
                "--hidden-import", "kivymd.app",
                "--hidden-import", "kivymd.uix",
                "--collect-data", "kivymd",
            ])

        if "plyer" in addons:
            args.extend([
                "--hidden-import", "plyer",
                "--hidden-import", "plyer.platforms",
            ])

        return args

    def _resolve_project_assets(
        self,
        project_root: Path,
        manifest: FreezeManifest,
    ) -> List[str]:
        args: List[str] = []
        sep = self._data_separator(manifest)

        if manifest.protect_resources:
            return args

        resources_dir_name = manifest.resources_dir or "resources"
        resources_path = (project_root / resources_dir_name).resolve()

        if resources_path.is_dir():
            # If resources/base exists, include platform profiles
            base_dir = resources_path / "base"
            if base_dir.is_dir():
                # Add base resources
                args.extend(["--add-data", f"{base_dir.as_posix()}{sep}{resources_dir_name}"])

                # Add platform-specific resources if present
                plat = manifest.target_platform.lower()
                plat_dir = None
                if plat in ("mac", "macos", "darwin"):
                    plat_dir = resources_path / "mac"
                elif plat == "windows":
                    plat_dir = resources_path / "windows"
                elif plat == "linux":
                    plat_dir = resources_path / "linux"

                if plat_dir and plat_dir.is_dir():
                    args.extend(["--add-data", f"{plat_dir.as_posix()}{sep}{resources_dir_name}"])
            else:
                # Flat resources directory
                args.extend([
                    "--add-data",
                    f"{resources_path.as_posix()}{sep}{resources_dir_name}",
                ])

        settings_path = (project_root / "settings").resolve()
        if settings_path.is_dir():
            for source in sorted(settings_path.rglob("*")):
                if not source.is_file():
                    continue
                if source.name.lower() in {
                    self._SECRETS_FILE_NAME,
                    self._SIGN_FILE_NAME,
                }:
                    continue

                rel_parent = source.relative_to(settings_path).parent.as_posix()
                destination = "settings" if rel_parent == "." else f"settings/{rel_parent}"
                args.extend([
                    "--add-data",
                    f"{source.as_posix()}{sep}{destination}",
                ])

        return args

    def _data_separator(self, manifest: FreezeManifest) -> str:
        return ";" if manifest.target_platform == "windows" else ":"

    def _has_qml(self, project_root: Path) -> bool:
        # Avoid detecting .qml in build directories or virtual environments
        for path in project_root.rglob("*.qml"):
            rel_parts = path.relative_to(project_root).parts
            if any(part in ("build", "dist", ".git", ".venv", "venv", "temp") for part in rel_parts):
                continue
            return True
        return False

    def _resolve_competing_framework_exclusions(
        self,
        binding: Binding,
        manifest: FreezeManifest,
    ) -> List[str]:
        """
        Automatically excludes competing UI framework packages from PyInstaller scanning
        so users never need to manually list 'kivy', 'kivy_install', etc. in exclude_modules.
        """
        exclusions: List[str] = []
        user_excludes = {m.lower() for m in manifest.optimization.exclude_modules}

        # If active binding is Qt-based (PySide6, PyQt6, PyQt5, PySide2)
        if binding.is_pyside or binding in (Binding.PYQT6, Binding.PYQT5):
            competing = ["kivy", "kivy_install", "tkinter"]
            if binding != Binding.PYSIDE6:
                competing.append("PySide6")
            if binding != Binding.PYQT6:
                competing.append("PyQt6")
            if binding != Binding.PYQT5:
                competing.append("PyQt5")
            if binding != Binding.PYSIDE2:
                competing.append("PySide2")

            for mod in competing:
                if mod.lower() not in user_excludes:
                    exclusions.extend(["--exclude-module", mod])

        # If active binding is Kivy
        elif binding == Binding.KIVY:
            competing = ["PySide6", "PyQt6", "PyQt5", "PySide2", "tkinter"]
            for mod in competing:
                if mod.lower() not in user_excludes:
                    exclusions.extend(["--exclude-module", mod])

        # If active binding is Tkinter
        elif binding == Binding.TKINTER:
            competing = ["kivy", "kivy_install", "PySide6", "PyQt6", "PyQt5", "PySide2"]
            for mod in competing:
                if mod.lower() not in user_excludes:
                    exclusions.extend(["--exclude-module", mod])

        return exclusions
