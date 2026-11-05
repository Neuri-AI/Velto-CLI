"""Mobile build contracts shared by the CLI application and infrastructure layers."""
from dataclasses import dataclass
from enum import Enum
from pathlib import Path


class MobileTarget(str, Enum):
    ANDROID = "android"
    IOS = "ios"


class MobileFramework(str, Enum):
    KIVY = "kivy"
    PYSIDE6 = "pyside6"


@dataclass(frozen=True)
class AndroidSpec:
    package_name: str
    min_sdk: int = 26
    target_sdk: int = 35
    archs: tuple[str, ...] = ("arm64-v8a",)
    permissions: tuple[str, ...] = ("INTERNET",)


@dataclass(frozen=True)
class IOSSpec:
    bundle_id: str
    deployment_target: str = "15.0"
    team_id: str | None = None


@dataclass(frozen=True)
class MobileFreezeRequest:
    project_root: Path
    target: MobileTarget
    framework: MobileFramework
    app_name: str
    version: str
    entry_point: str = "main.py"
    debug: bool = False
    init_only: bool = False
    clean: bool = False
    wheel_pyside: Path | None = None
    wheel_shiboken: Path | None = None


@dataclass(frozen=True)
class MobileBuildArtifact:
    path: Path
    target: MobileTarget
    framework: MobileFramework
    kind: str  # apk, aab or xcode-project
