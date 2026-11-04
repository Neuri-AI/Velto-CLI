"""
qyro.freeze.windows
Windows freeze helper entrypoint that delegates to FreezeDesktopUseCase.
Loads settings/base.json, settings/windows.json, build.json automatically.
"""

from pathlib import Path
from typing import Optional
from velto_cli.container import get_container
container = get_container()


def freeze_windows(
    project_root: Optional[Path] = None,
    debug: Optional[bool] = None,
) -> Path:
    """Delegates Windows application freezing to Container IoC FreezeDesktopUseCase."""
    from velto_cli.container import get_container

    root = (project_root or Path.cwd()).resolve()
    container = get_container()

    artifact = container.freeze_desktop_use_case.execute(
        project_root=root,
        debug=debug,
    )

    return artifact.executable_path


