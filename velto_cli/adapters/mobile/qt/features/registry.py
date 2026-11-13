"""Declarative registry for PSX capabilities that alter the Qt Android runtime.

Add a new native PSX capability here rather than teaching the build pipeline
about another individual tag.  Each feature owns detection, wheel validation,
spec configuration, and APK certification in one entry.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from collections.abc import Callable

from .svg import (
    assert_android_svg_wheel, configure_android_svg_spec, project_uses_svg,
    verify_android_svg_apk,
)
from .webview import (
    assert_android_webview_wheel, configure_android_webview_buildozer, configure_android_webview_spec,
    prepare_android_webview_gradle, project_uses_webview, verify_android_webview_apk,
)


@dataclass(frozen=True)
class QtAndroidFeature:
    """One optional PSX/Qt capability with Android deployment requirements."""
    name: str
    module: str
    detect: Callable[[Path], bool]
    validate_wheel: Callable[[Path], None]
    configure_spec: Callable[[Path], bool]
    verify_apk: Callable[[Path], None]
    configure_buildozer: Callable[[Path], bool] | None = None
    prepare_gradle: Callable[..., bool] | None = None
    required_permissions: tuple[str, ...] = ()
    # Increment when native deployment changes. Source-only edits can still
    # reuse an APK, while an older APK for this feature must be rebuilt.
    deployment_revision: int = 1


FEATURES: tuple[QtAndroidFeature, ...] = (
    QtAndroidFeature(
        name="svg", module="Svg", detect=project_uses_svg,
        validate_wheel=assert_android_svg_wheel,
        configure_spec=configure_android_svg_spec, verify_apk=verify_android_svg_apk,
    ),
    QtAndroidFeature(
        name="webview", module="WebView", detect=project_uses_webview,
        validate_wheel=assert_android_webview_wheel,
        configure_spec=configure_android_webview_spec, verify_apk=verify_android_webview_apk,
        configure_buildozer=configure_android_webview_buildozer,
        prepare_gradle=prepare_android_webview_gradle,
        required_permissions=("android.permission.INTERNET",),
        deployment_revision=3,
    ),
)


@dataclass(frozen=True)
class EnabledQtAndroidFeatures:
    """Resolved immutable feature set for one Android project build."""
    values: tuple[QtAndroidFeature, ...]

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(feature.name for feature in self.values)

    @property
    def modules(self) -> tuple[str, ...]:
        return tuple(feature.module for feature in self.values)

    @property
    def required_permissions(self) -> tuple[str, ...]:
        return tuple(dict.fromkeys(permission for feature in self.values
                                   for permission in feature.required_permissions))

    @property
    def deployment_revisions(self) -> dict[str, int]:
        return {feature.name: feature.deployment_revision for feature in self.values
                if feature.deployment_revision != 1}

    def prepare(self, wheel: Path, spec: Path) -> None:
        for feature in self.values:
            feature.validate_wheel(wheel)
            feature.configure_spec(spec)

    def verify(self, artifact: Path) -> None:
        for feature in self.values:
            feature.verify_apk(artifact)

    def configure_buildozer(self, root: Path) -> None:
        for feature in self.values:
            if feature.configure_buildozer is not None:
                feature.configure_buildozer(root)

    def prepare_gradle(self, artifact: Path, *, debug: bool,
                       run: Callable[[list[str], Path], object]) -> bool:
        changed = False
        for feature in self.values:
            if feature.prepare_gradle is not None:
                changed = feature.prepare_gradle(artifact, debug=debug, run=run) or changed
        return changed


def enabled_qt_android_features(root: Path) -> EnabledQtAndroidFeatures:
    """Resolve every enabled feature once; preserve registry declaration order."""
    return EnabledQtAndroidFeatures(tuple(feature for feature in FEATURES if feature.detect(root)))
