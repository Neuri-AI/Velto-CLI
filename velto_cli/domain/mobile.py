"""
!EXPERIMENTAL: This module is just for defining mobile target specifications for Android and iOS

!#########################
! AVOID USING THIS MODULE
!#########################

qyro.domain.mobile
Target specifications for Android and iOS compilation.
"""
from dataclasses import dataclass
from enum import Enum
from typing import Optional

class MobileTarget(str, Enum):
    ANDROID = "android"
    IOS = "ios"

@dataclass(frozen=True)
class AndroidSpec:
    package_name: str
    min_sdk: int = 26
    target_sdk: int = 33
    archs: tuple[str, ...] = ("arm64-v8a", "armeabi-v7a")
    permissions: tuple[str, ...] = ("INTERNET",)

@dataclass(frozen=True)
class IOSSpec:
    bundle_id: str
    deployment_target: str = "15.0"
    team_id: Optional[str] = None
