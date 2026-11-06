"""Orchestrate mobile freezing through an application port."""
from pathlib import Path

from velto_cli.application.ports import MobileBuildPort, SettingsPort, UserInteractionPort
from velto_cli.adapters.mobile.qt.android_build_config import load_android_build_config
from velto_cli.domain.mobile import (
    MobileBuildArtifact, MobileFramework, MobileFreezeRequest, MobileTarget,
)


class FreezeMobileUseCase:
    def __init__(
        self,
        builder: MobileBuildPort,
        settings_repo: SettingsPort,
        ui: UserInteractionPort,
    ) -> None:
        self._builder = builder
        self._settings = settings_repo
        self._ui = ui

    def execute(
        self,
        *,
        target: MobileTarget,
        project_root: Path | None = None,
        init_only: bool = False,
        debug: bool = False,
        clean: bool = False,
        protect_source: bool = False,
        wheel_pyside: str | None = None,
        wheel_shiboken: str | None = None,
    ) -> MobileBuildArtifact | None:
        root = (project_root or Path.cwd()).resolve()
        if not (root / "pyproject.toml").is_file():
            raise ValueError(f"Not a Velto project (missing pyproject.toml): {root}")
        raw_framework = self._settings.get_optional("framework", None)
        if not isinstance(raw_framework, str):
            raise ValueError("Missing 'framework' in settings/base.json")
        try:
            framework = MobileFramework(raw_framework.strip().lower())
        except ValueError as exc:
            raise ValueError("Mobile builds support only Kivy or PySide6") from exc

        android_settings = (load_android_build_config(root)
                            if target is MobileTarget.ANDROID else {})
        if (target is MobileTarget.ANDROID
                and framework is MobileFramework.PYSIDE6
                and not init_only):
            # Release remains protected by default; debug is configurable.
            protect_source = protect_source or (
                not debug or android_settings.get("protect_source", False)
            )
        if protect_source and (target is not MobileTarget.ANDROID or framework is not MobileFramework.PYSIDE6):
            raise ValueError("--protect-source currently supports only PySide6 Android APKs")
        if protect_source and init_only:
            raise ValueError("--protect-source cannot be combined with --init-spec")

        app_name = self._settings.get_optional("app_name", root.name)
        version = self._settings.get_optional("version", "0.1.0")
        entry_point = self._settings.get_optional("entry_point", "main.py")
        if not isinstance(entry_point, str) or not entry_point.endswith(".py"):
            raise ValueError("Mobile entry_point must be a Python file")
        entry = (root / entry_point).resolve()
        if not entry.is_relative_to(root):
            raise ValueError("Mobile entry_point must stay inside the project")
        if not init_only and not entry.is_file():
            raise FileNotFoundError(f"Mobile entry point not found: {entry}")
        def resolve_wheel(value: str | None) -> Path | None:
            if not value:
                return None
            candidate = (root / value).resolve()
            if not candidate.is_file() or candidate.suffix != ".whl":
                raise ValueError(f"Expected an existing Android wheel (.whl): {candidate}")
            return candidate

        request = MobileFreezeRequest(
            project_root=root, target=target, framework=framework,
            app_name=str(app_name), version=str(version),
            entry_point=entry_point, debug=debug, init_only=init_only, clean=clean,
            protect_source=protect_source,
            wheel_pyside=resolve_wheel(wheel_pyside),
            wheel_shiboken=resolve_wheel(wheel_shiboken),
        )
        artifact = self._builder.freeze(request)
        if artifact is not None:
            self._ui.success(f"Mobile artifact ({artifact.kind}): {artifact.path}")
        return artifact
