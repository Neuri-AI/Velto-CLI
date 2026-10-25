"""End-to-end PyInstaller import of a source-backed PSX @component."""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

from velto_cli.adapters.freeze.pyinstaller_adapter import PyInstallerFreezer
from velto_cli.domain.build import FreezeManifest


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="velto-frozen-source-") as temp:
        project = Path(temp) / "project"
        component_dir = project / "components"
        component_dir.mkdir(parents=True)
        (component_dir / "__init__.py").write_text("", encoding="utf-8")
        (component_dir / "sidebar.py").write_text(
            "from psx import component, psx\n"
            "@component\n"
            "def Sidebar():\n"
            "    return psx('''<div>Frozen Sidebar</div>''')\n",
            encoding="utf-8",
        )
        (project / "main.py").write_text(
            "from components.sidebar import Sidebar\n"
            "assert Sidebar is not None\n"
            "print('FROZEN_COMPONENT_OK')\n",
            encoding="utf-8",
        )
        manifest = FreezeManifest(app_name="ComponentSmoke",
                                  target_platform="windows" if os.name == "nt" else "linux")
        source_args = PyInstallerFreezer._psx_component_source_args(project, manifest)
        assert source_args, "No staged @component source was identified"
        cmd = [
            sys.executable, "-m", "PyInstaller", "--onedir", "--clean", "-y",
            "--name", "ComponentSmoke",
            "--distpath", str(project / "dist"),
            "--workpath", str(project / "work"),
            "--specpath", str(project / "spec"),
            "--paths", str(project),
            *source_args,
            str(project / "main.py"),
        ]
        result = subprocess.run(cmd, cwd=project, capture_output=True, text=True)
        if result.returncode:
            raise RuntimeError(result.stdout[-5000:] + result.stderr[-5000:])
        exe = project / "dist" / "ComponentSmoke" / (
            "ComponentSmoke.exe" if os.name == "nt" else "ComponentSmoke"
        )
        assert exe.exists(), exe
        run = subprocess.run([str(exe)], cwd=project,
                             capture_output=True, text=True, timeout=35)
        if run.returncode or "FROZEN_COMPONENT_OK" not in run.stdout:
            raise AssertionError(
                f"Frozen component exited {run.returncode}:\n{run.stdout}\n{run.stderr}"
            )
        print(run.stdout.strip())


if __name__ == "__main__":
    main()
