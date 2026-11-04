"""
qyro.adapters.freeze.optimizer
Binary optimization adapter implementing BinaryOptimizerPort.
Runs UPX executable compression if upx is present on PATH, and cleans up artifacts.
"""

import fnmatch
import shutil
import subprocess
from pathlib import Path

from velto_cli.application.ports import BinaryOptimizerPort, UserInteractionPort
from velto_cli.domain.build import BuildArtifact, BundleMode, OptimizationConfig


class BinaryOptimizer(BinaryOptimizerPort):
    def __init__(self, ui: UserInteractionPort | None = None):
        self._ui = ui

    def optimize(
        self,
        artifact: BuildArtifact,
        config: OptimizationConfig,
    ) -> None:
        if config.strip_binaries:
            self._strip_binaries(artifact)

        if config.upx_enabled:
            self._compress_with_upx(artifact, config)

    def _strip_binaries(self, artifact: BuildArtifact) -> None:
        strip_bin = shutil.which("strip")

        if not strip_bin or not artifact.output_dir or not artifact.output_dir.exists():
            return

        stripped_count = 0

        for file_path in artifact.output_dir.rglob("*"):
            if not self._is_strippable_binary(file_path):
                continue

            try:
                subprocess.run(
                    [strip_bin, "--strip-unneeded", str(file_path)],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                stripped_count += 1
            except Exception:
                continue

        if stripped_count and self._ui:
            self._ui.info(
                f"✓ Stripped unneeded debug symbols from {stripped_count} binary files."
            )

    @staticmethod
    def _is_strippable_binary(file_path: Path) -> bool:
        if not file_path.is_file() or file_path.is_symlink():
            return False

        return (
            file_path.suffix in (".so", ".dylib")
            or ".so." in file_path.name
        )

    def _compress_with_upx(
        self,
        artifact: BuildArtifact,
        config: OptimizationConfig,
    ) -> None:
        upx_bin = shutil.which("upx")

        if not upx_bin:
            return

        if not artifact.executable_path or not artifact.executable_path.exists():
            return

        compressed_count = 0

        # Solo comprimimos directamente el ejecutable raíz en modo ONEFILE.
        # En ONEDIR, alterar de manera externa el bootloader de PyInstaller causa inestabilidad crítica.
        if artifact.bundle_mode == BundleMode.ONEFILE:
            try:
                result = subprocess.run(
                    [
                        upx_bin,
                        f"-{config.upx_level}",
                        str(artifact.executable_path),
                    ],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                if result.returncode == 0:
                    compressed_count += 1
            except Exception:
                pass

        # En modo ONEDIR, comprimimos las librerías dinámicas respetando estrictamente upx_excludes
        if artifact.bundle_mode == BundleMode.ONEDIR and artifact.output_dir and artifact.output_dir.exists():
            excludes = config.upx_excludes or []

            for file_path in artifact.output_dir.rglob("*"):
                if not file_path.is_file() or file_path.is_symlink() or file_path == artifact.executable_path:
                    continue

                if not (file_path.suffix in (".so", ".dll", ".dylib", ".pyd", ".exe") or ".so." in file_path.name):
                    continue

                # Extraer la ruta relativa respecto a la raíz del artefacto para coincidir con carpetas internas
                try:
                    rel_path = str(file_path.relative_to(artifact.output_dir))
                except ValueError:
                    rel_path = file_path.name

                name = file_path.name

                # Evaluar exclusión contra el nombre exacto del archivo o contra su estructura de subdirectorio
                if any(fnmatch.fnmatch(name, pat) or fnmatch.fnmatch(rel_path, pat) for pat in excludes):
                    continue

                try:
                    res = subprocess.run(
                        [
                            upx_bin,
                            f"-{config.upx_level}",
                            str(file_path),
                        ],
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                    )
                    if res.returncode == 0:
                        compressed_count += 1
                except Exception:
                    pass

        if compressed_count > 0 and self._ui:
            self._ui.info(
                f"✓ UPX binary compression successfully applied ({compressed_count} files).")
