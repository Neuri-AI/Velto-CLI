"""End-to-end proof that frozen PSX components do not need .py files."""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

from velto_cli.adapters.freeze.psx_source_preparer import PSXProjectSourcePreparer
from velto_cli.adapters.freeze.source_exposure_guard import SourceExposureGuard
from velto_cli.domain.build import BundleMode


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="velto-frozen-component-") as temp:
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

        prepared_entry, staging = PSXProjectSourcePreparer().prepare(
            project, project / "main.py",
        )
        assert prepared_entry.is_file()
        assert staging != project
        assert "__psx_build_precompiled__ = True" in (
            staging / "components" / "sidebar.py"
        ).read_text(encoding="utf-8")

        for mode in (BundleMode.ONEDIR, BundleMode.ONEFILE):
            name = "ComponentSmoke"
            cmd = [
                sys.executable, "-m", "PyInstaller",
                "--onedir" if mode == BundleMode.ONEDIR else "--onefile",
                "--clean", "-y", "--name", name,
                "--distpath", str(project / f"dist-{mode.value}"),
                "--workpath", str(project / f"work-{mode.value}"),
                "--specpath", str(project / f"spec-{mode.value}"),
                "--paths", str(staging), str(prepared_entry),
            ]
            env = dict(os.environ)
            env["PYTHONPATH"] = str(staging)
            result = subprocess.run(cmd, cwd=project, env=env,
                                    capture_output=True, text=True)
            if result.returncode:
                raise RuntimeError(result.stdout[-5000:] + result.stderr[-5000:])

            dist = project / f"dist-{mode.value}"
            directory = dist / name if mode == BundleMode.ONEDIR else dist
            exe = directory / (name + (".exe" if os.name == "nt" else ""))
            assert exe.exists(), exe
            assert not list(directory.rglob("components/*.py"))
            SourceExposureGuard().verify(
                project_root=project,
                entry_point=project / "main.py",
                output_dir=directory,
                executable_path=exe,
                mode=mode,
            )
            isolated_env = dict(os.environ)
            isolated_env.pop("PYTHONPATH", None)
            run = subprocess.run(
                [str(exe)], cwd=temp, env=isolated_env,
                capture_output=True, text=True, timeout=35,
            )
            if run.returncode or "FROZEN_COMPONENT_OK" not in run.stdout:
                raise AssertionError(
                    f"{mode.value}: frozen component exited {run.returncode}:\n"
                    f"{run.stdout}\n{run.stderr}"
                )
            print(mode.value + ": " + run.stdout.strip())


if __name__ == "__main__":
    main()
