"""Infrastructure adapter for Kivy/Qt mobile toolchains.

Never treats tool invocation as proof of a runnable mobile application.
Native dependencies (including Cinnamon) still require target-compatible builds.
"""
import re
import shutil
import sys
from importlib.metadata import PackageNotFoundError, version as distribution_version
from pathlib import Path

from velto_cli.application.ports import ProcessRunnerPort
from velto_cli.domain.mobile import (
    MobileBuildArtifact, MobileFramework, MobileFreezeRequest, MobileTarget,
)


class MobileToolchainFreezer:
    def __init__(self, runner: ProcessRunnerPort) -> None:
        self._runner = runner

    @staticmethod
    def _command(name: str) -> str:
        resolved = shutil.which(name)
        if not resolved:
            raise RuntimeError(
                f"Required mobile tool '{name}' not found on PATH. "
                "Install the target toolchain before building."
            )
        return resolved

    def _run(self, command: list[str], root: Path) -> None:
        code = self._runner.run(command, cwd=str(root))
        if code != 0:
            raise RuntimeError(f"Mobile tool failed (exit {code}): {command[0]}")

    @staticmethod
    def _latest(root: Path, suffixes: tuple[str, ...]) -> Path:
        matches = [p for suffix in suffixes for p in root.rglob(f"*{suffix}")
                   if p.is_file()]
        if not matches:
            raise RuntimeError(
                "Tool exited successfully but no mobile artifact was found. "
                "Inspect the toolchain logs and project dependencies."
            )
        return max(matches, key=lambda p: p.stat().st_mtime)

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

    def _kivy_android(self, request: MobileFreezeRequest) -> MobileBuildArtifact | None:
        root = request.project_root
        spec = root / "buildozer.spec"
        if request.init_only:
            if spec.exists():
                raise FileExistsError(f"Refusing to overwrite {spec}")
            name = re.sub(r"[^a-z0-9]", "", request.app_name.lower()) or "veltoapp"
            spec.write_text(
                "[app]\n"
                f"title = {request.app_name}\n"
                f"package.name = {name}\n"
                "package.domain = org.velto\n"
                f"version = {request.version}\n"
                "source.dir = .\n"
                "source.include_exts = py,png,jpg,kv,atlas,json,css,svg\n"
                "requirements = python3,kivy,qyro-engine,velto-core\n"
                "orientation = all\n"
                "android.archs = arm64-v8a\n"
                "\n[buildozer]\nlog_level = 2\n",
                encoding="utf-8",
            )
            return None
        if not spec.is_file():
            raise RuntimeError(
                "Missing buildozer.spec. Run 'velto build --target android "
                "--init-spec', then review requirements and package.domain."
            )
        if request.entry_point != "main.py":
            raise ValueError("Buildozer expects main.py at the project root")
        command = self._command("buildozer")
        self._run([command, "android", "debug" if request.debug else "release"], root)
        artifact = self._latest(root / "bin", (".apk",) if request.debug else (".aab", ".apk"))
        return MobileBuildArtifact(artifact, request.target, request.framework,
                                   artifact.suffix.lstrip("."))

    @staticmethod
    def _android_wheel_version(path: Path, package: str, arch: str) -> str | None:
        """Read wheel filename metadata without importing host or Android binaries."""
        # Example: PySide6-6.12.0-cp311-abi3-android_aarch64.whl
        parts = path.name.split("-")
        if len(parts) < 5 or parts[0].lower().replace("_", "") != package.lower().replace("_", ""):
            return None
        if not path.name.endswith(f"android_{arch}.whl"):
            return None
        return parts[1]

    def _qt_android_wheels(self, request: MobileFreezeRequest) -> tuple[Path, Path]:
        """Resolve matching Android wheels, downloading once into Velto's cache.

        Explicit wheel paths are an advanced override; default workflow downloads
        through Qt's qtpip utility into a version/architecture-specific directory.
        """
        arch = "aarch64"
        try:
            qt_version = distribution_version("PySide6")
        except PackageNotFoundError as exc:
            raise RuntimeError(
                "PySide6 is not installed in this build environment. "
                "Install velto-cli[mobile-pyside6] first."
            ) from exc

        cache = Path.home() / ".cache" / "velto" / "mobile" / "pyside6" / "android" / arch / qt_version
        override = (request.wheel_pyside, request.wheel_shiboken)
        if any(override) and not all(override):
            raise ValueError(
                "Both --wheel-pyside and --wheel-shiboken must be supplied together."
            )

        def discover() -> tuple[Path, Path] | None:
            if all(override):
                pair = (override[0], override[1])
            else:
                paths = list(cache.rglob("*.whl")) if cache.exists() else []
                pyside = sorted(
                    (p for p in paths if self._android_wheel_version(p, "PySide6", arch) == qt_version),
                    key=str,
                )
                shiboken = sorted(
                    (p for p in paths if self._android_wheel_version(p, "shiboken6", arch) == qt_version),
                    key=str,
                )
                if not pyside or not shiboken:
                    return None
                pair = (pyside[-1], shiboken[-1])
            for candidate, package in zip(pair, ("PySide6", "shiboken6")):
                if candidate is None or not candidate.is_file():
                    raise ValueError(f"Android wheel does not exist: {candidate}")
                actual = self._android_wheel_version(candidate, package, arch)
                if actual != qt_version:
                    raise ValueError(
                        f"Android {package} wheel must match installed PySide6 "
                        f"{qt_version} and target {arch}: {candidate}"
                    )
            return pair

        found = discover()
        if found is not None:
            return found

        qtpip = self._command("qtpip")
        cache.mkdir(parents=True, exist_ok=True)
        self._run([qtpip, "download", "PySide6", "--android", "--arch", arch], cache)
        found = discover()
        if found is None:
            raise RuntimeError(
                f"qtpip did not produce matching PySide6/shiboken6 Android "
                f"{arch} wheels for installed PySide6 {qt_version}. "
                "Check available Qt Android distributions and qtpip output."
            )
        return found

    def _pyside_android(self, request: MobileFreezeRequest) -> MobileBuildArtifact | None:
        root = request.project_root
        if request.entry_point != "main.py":
            raise ValueError("PySide6 Android deployment requires root main.py")

        spec = root / "pysidedeploy.spec"
        if request.init_only and spec.exists():
            raise FileExistsError(f"Refusing to overwrite {spec}")
        if not request.init_only and not spec.is_file():
            raise RuntimeError(
                "Missing pysidedeploy.spec. Run 'velto build --target android "
                "--init-spec' first."
            )

        wheel_pyside, wheel_shiboken = self._qt_android_wheels(request)
        command = self._command("pyside6-android-deploy")
        args = [command]
        if request.init_only:
            args.extend(["--init", "--name", request.app_name])
        else:
            args.extend(["--config-file", str(spec), "--force"])
        args.extend([
            "--wheel-pyside", str(wheel_pyside),
            "--wheel-shiboken", str(wheel_shiboken),
        ])
        self._run(args, root)
        if request.init_only:
            if not spec.is_file():
                raise RuntimeError(
                    "Qt Android deploy completed without generating pysidedeploy.spec."
                )
            return None

        artifact = self._latest(root, (".apk", ".aab"))
        return MobileBuildArtifact(artifact, request.target, request.framework,
                                   artifact.suffix.lstrip("."))

    def _pyside_ios(self, request: MobileFreezeRequest) -> MobileBuildArtifact | None:
        root = request.project_root
        spec = root / "pysidedeploy.spec"
        command = self._command("pyside6-ios-deploy")
        if not spec.is_file():
            raise RuntimeError(
                "Provide pysidedeploy.spec with matching iOS PySide6 and "
                "shiboken6 wheels before running the iOS deploy tool."
            )
        args = [command, "--config-file", str(spec)]
        if request.init_only:
            args.append("--init")
        self._run(args, root)
        if request.init_only:
            return None
        projects = sorted(root.glob("deployment/**/*.xcodeproj"))
        if not projects:
            raise RuntimeError("iOS deployment did not produce an Xcode project")
        return MobileBuildArtifact(projects[-1], request.target, request.framework,
                                   "xcode-project")

    def _kivy_ios(self, request: MobileFreezeRequest) -> MobileBuildArtifact | None:
        root = request.project_root
        # kivy-ios's 'toolchain create' generates a project, not a signed IPA.
        toolchain = self._command("toolchain")
        if request.init_only:
            raise RuntimeError(
                "Kivy iOS needs a prebuilt kivy-ios toolchain and Xcode; "
                "there is no standalone mobile spec to initialize."
            )
        if not (root / "main.py").is_file():
            raise RuntimeError("Kivy iOS requires main.py at project root")
        self._command("xcodebuild")
        output = root / "build" / "ios"
        output.mkdir(parents=True, exist_ok=True)
        project_name = re.sub(r"[^A-Za-z0-9]", "", request.app_name) or "VeltoApp"
        self._run([toolchain, "create", project_name, str(root)], output)
        projects = sorted(output.glob("**/*.xcodeproj"))
        if not projects:
            raise RuntimeError("kivy-ios did not generate an Xcode project")
        return MobileBuildArtifact(projects[-1], request.target, request.framework,
                                   "xcode-project")
