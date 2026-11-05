"""Mobile toolchain dispatch.

Qt and Kivy have independent backend implementations. This façade preserves
the existing application API and chooses the target-specific builder.
"""
import sys

from velto_cli.adapters.mobile.common.base import MobileBuilderBase
from velto_cli.adapters.mobile.kivy.android import KivyAndroidBuilder
from velto_cli.adapters.mobile.kivy.ios import KivyIOSBuilder
from velto_cli.adapters.mobile.qt.android import QtAndroidBuilder
from velto_cli.adapters.mobile.qt.ios import QtIOSBuilder
from velto_cli.domain.mobile import MobileBuildArtifact, MobileFramework, MobileFreezeRequest, MobileTarget


class MobileToolchainFreezer(
    MobileBuilderBase, QtAndroidBuilder, QtIOSBuilder, KivyAndroidBuilder, KivyIOSBuilder
):
    """Stable mobile build boundary; backend logic lives in dedicated modules."""

    def freeze(self, request: MobileFreezeRequest) -> MobileBuildArtifact | None:
        if request.target == MobileTarget.IOS and sys.platform != "darwin":
            raise RuntimeError(
                "iOS builds require macOS with Xcode. "
                "Run the same command on a macOS build agent."
            )
        if request.target == MobileTarget.ANDROID and sys.platform == "win32":
            raise RuntimeError(
                "Android toolchains require a Unix host. "
                "On Windows run Velto-CLI inside WSL2."
            )
        if request.framework == MobileFramework.KIVY:
            if request.target == MobileTarget.ANDROID:
                return self._kivy_android(request)
            return self._kivy_ios(request)
        if request.target == MobileTarget.ANDROID:
            return self._pyside_android(request)
        return self._pyside_ios(request)
