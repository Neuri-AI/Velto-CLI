"""
qyro.freeze.mac
macOS freeze helper entrypoint that delegates to FreezeDesktopUseCase.
Loads settings/base.json, settings/mac.json, build.json automatically.
"""

from pathlib import Path
from typing import List, Optional
from velto_cli.container import get_container
container = get_container()


def freeze_mac(
    project_root: Optional[Path] = None,
    debug: Optional[bool] = None,
    mac_bundle_identifier: Optional[str] = None,
    extra_args: Optional[List[str]] = None,
) -> Path:
    """Delegates macOS application freezing to Container IoC FreezeDesktopUseCase."""

    root = (project_root or Path.cwd()).resolve()

    # If mac_bundle_identifier or extra_args provided, set in settings or pass along
    if mac_bundle_identifier:
        container.settings.set("mac_bundle_identifier", mac_bundle_identifier)

    artifact = container.freeze_desktop_use_case.execute(
        project_root=root,
        debug=debug,
        extra_args=extra_args,
    )

    app_bundle = root / "target" / f"{artifact.executable_path.stem}.app"
    if app_bundle.exists():
        return app_bundle
    return artifact.output_dir


def generate_iconset(source_icon: Path, iconset_dir: Path) -> None:
    """Helper to generate standard macOS .iconset directory structure."""
    from velto_cli.container import get_container
    get_container().freezer._handle_mac_icon(
        source_icon.parent,
        iconset_dir.parent,
        None,  # type: ignore
    )


