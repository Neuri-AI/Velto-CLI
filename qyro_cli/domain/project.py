"""
Core project entities: the Qt binding, the project configuration and the spec
for a generated component.

All the small rules that used to be inlined in `init()` and `create()` live
here now — default bundle id, default base widget per binding, CamelCase
conversion — which means they're unit-testable without a terminal.
"""
import sys
import re
from enum import Enum
from typing import Dict, Optional
from dataclasses import dataclass, field
from qyro_cli.domain.errors import InvalidBindingError
from qyro_cli.domain.version import Version


def to_camel_case(name: str) -> str:
    """Convert snake_case / kebab-case / spaced names to CamelCase."""
    parts = name.strip().replace("-", " ").replace("_", " ").split()
    return "".join(word[:1].upper() + word[1:] for word in parts)


class AddonModule(str, Enum):
    HOTRL = "hotrl"
    PYDUX = "pydux"
    SENTRY = "sentry-sdk"
    REQUESTS = "requests"


class TargetPlatform(str, Enum):
    IPHONE = "iPhone"
    ANDROID = "Android"
    X86_64 = "x86_64"
    APPLE_SILICON = "Apple Silicon"


class Binding(str, Enum):
    PYQT5 = "PyQt5"
    PYQT6 = "PyQt6"
    PYSIDE2 = "PySide2"
    PYSIDE6 = "PySide6"
    KIVY = "Kivy"
    TKINTER = "Tkinter"

    @property
    def import_name(self) -> str:
        """Return the actual module name expected by importlib."""
        mapping = {
            Binding.KIVY: "kivy",
            Binding.TKINTER: "tkinter",
        }
        return mapping.get(self, self.value)

    @classmethod
    def values(cls) -> tuple:
        return tuple(b.value for b in cls)

    @classmethod
    def parse(cls, value: str) -> "Binding":
        for binding in cls:
            if binding.value.lower() == str(value).strip().lower():
                return binding
        raise InvalidBindingError(str(value), cls.values())

    @property
    def is_pyside(self) -> bool:
        return self in (Binding.PYSIDE2, Binding.PYSIDE6)

    @property
    def default_base_widget(self) -> str:
        if self.is_pyside:
            return "QWidget"
        if self == Binding.KIVY:
            return "Widget"
        if self == Binding.TKINTER:
            return "Frame"
        return "QtWidget"

    def supports(self, platform: TargetPlatform) -> bool:
        if platform in (
            TargetPlatform.IPHONE,
            TargetPlatform.ANDROID,
        ):
            return self in (
                Binding.KIVY,
                Binding.PYSIDE6,
            )

        if platform == TargetPlatform.X86_64:
            return True

        if platform == TargetPlatform.APPLE_SILICON:
            return self in (
                Binding.PYSIDE6,
                Binding.KIVY,
                Binding.PYQT6,
                Binding.TKINTER
            )

        return False

    @property
    def dependency_spec(self) -> str:
        if self == Binding.PYQT5 and sys.platform.startswith("win"):
            # Keep PyQt5 and restrict PyQt5-Qt5 on Windows without compiling
            return 'PyQt5", "PyQt5-Qt5<=5.15.2; sys_platform == \'win32\''

        if self == Binding.PYSIDE2:
            # Force 5.15.2 for PySide2 and limit python <3.11
            return 'PySide2>=5.15.2; python_version < "3.11"'

        return self.value

    @property
    def default_exclude_modules(self) -> tuple[str, ...]:
        return ("tkinter", "unittest", "test", "pydoc")



@dataclass
class ProjectConfig:
    """Everything `qyro init` needs to scaffold a project."""

    app_name: str
    version: Version
    author: str
    binding: Binding
    mac_bundle_identifier: Optional[str] = None
    addons: list[AddonModule] = field(default_factory=list)
    target_platform: TargetPlatform = TargetPlatform.X86_64

    DEFAULT_HIDDEN_IMPORTS = ("__future__",)

    @staticmethod
    def suggest_bundle_identifier(app_name: str, author: str) -> str:
        author_part = (author.lower().split() or ["unknown"])[0]
        app_part = "".join(app_name.lower().split())
        return f"com.{author_part}.{app_part}"

    def as_display_dict(self) -> Dict[str, str]:
        """Field -> value, for the confirmation summary. No formatting here."""
        return {
            "App name": self.app_name,
            "Version": str(self.version),
            "Author": self.author,
            "Binding": self.binding.value,
            "Mac bundle identifier": self.mac_bundle_identifier or "(none)",
            "Add-ons": ", ".join(addon.value for addon in self.addons) or "(none)",
            "Target platform": self.target_platform.value,
        }

    def as_template_variables(self) -> Dict[str, object]:
        """Variables substituted into the project template files."""
        return {
            "app_name": self.app_name,
            "class_name": self.to_pascal_case,
            "author": self.author,
            "mac_bundle_identifier": self.mac_bundle_identifier,
            "python_bindings": self.binding.value,
            "framework": self.binding.dependency_spec,
            # for qyro_runtime info
            "addons": [addon.value for addon in self.addons],
            "python_version": self.target_python_version,
            # for pyproject.toml
            "addon_dependencies": ",\n".join(
                f'    "{addon.value}"'
                for addon in self.addons
            ),
            "target_platform": self.target_platform.value,
            "version": str(self.version),
        }

    def as_base_settings(self) -> Dict[str, object]:
        """The keys written into <project_location>/settings/base.json."""
        return {
            "app_name": self.app_name,
            "author": self.author,
            "version": str(self.version),
            "binding": self.binding.value,
            "hidden_imports": list(self.DEFAULT_HIDDEN_IMPORTS),
            "optimization": {
                "exclude_modules": list(self.binding.default_exclude_modules),
            },
        }

    @property
    def target_python_version(self) -> str:
        """Returns the target Python version for this binding."""
        if self.binding == Binding.PYSIDE2:
            return ">=3.8,<3.11"

        if self.binding == Binding.PYQT5:
            return ">=3.8,<3.13"

        return ">=3.10,<3.15"

    @property
    def to_pascal_case(self) -> str:
        """
        Transforms the instance's name into a valid, idiomatic Python PascalCase class identifier.
        Handles hyphens, underscores, spaces, dots, and camelCase transitions.
        Examples:
            'my-awesome-app' -> 'MyAwesomeApp'
            'my_awesome_app' -> 'MyAwesomeApp'
            'my awesome app' -> 'MyAwesomeApp'
            'myAwesomeApp'   -> 'MyAwesomeApp'
        """
        s = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", self.app_name)
        s = re.sub(r"[^a-zA-Z0-9]+", " ", s).strip()

        if not s:
            return "App"

        words = s.split()
        pascal = "".join(w[:1].upper() + w[1:] for w in words)

        if pascal[0].isdigit():
            pascal = f"App{pascal}"

        return pascal or "App"


