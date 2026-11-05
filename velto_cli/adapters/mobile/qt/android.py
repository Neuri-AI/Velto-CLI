"""PySide6 Android wheel resolution, Qt Java bootstrap, and APK packaging."""
import shutil
from importlib.metadata import PackageNotFoundError, version as distribution_version
from pathlib import Path
from zipfile import BadZipFile, ZipFile

from velto_cli.adapters.mobile.qt.wheel_archive import QtAndroidWheelArchive
from velto_cli.domain.mobile import MobileBuildArtifact, MobileFreezeRequest, MobileTarget


class QtAndroidBuilder:
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
        """Prefer commercial Qt wheels when licensed; otherwise use public wheels.

        qtpip is Qt's authority for validating commercial credentials. Simply
        finding a Qt account configuration file is not proof of a valid license.
        Explicit wheels bypass download and retain the caller's licensing choice.
        """
        arch = "aarch64"
        try:
            qt_version = distribution_version("PySide6")
        except PackageNotFoundError as exc:
            raise RuntimeError(
                "PySide6 is not installed in this build environment. "
                "Install velto-cli[mobile-pyside6] first."
            ) from exc

        root_cache = (
            Path.home() / ".velto" / "mobile" / "pyside6"
            / "android" / arch / qt_version
        )
        override = (request.wheel_pyside, request.wheel_shiboken)
        if any(override) and not all(override):
            raise ValueError(
                "Both --wheel-pyside and --wheel-shiboken must be supplied together."
            )

        def discover(directory: Path) -> tuple[Path, Path] | None:
            paths = list(directory.glob("*.whl")) if directory.exists() else []
            pyside = sorted(
                (p for p in paths if self._android_wheel_version(
                    p, "PySide6", arch
                ) == qt_version),
                key=str,
            )
            shiboken = sorted(
                (p for p in paths if self._android_wheel_version(
                    p, "shiboken6", arch
                ) == qt_version),
                key=str,
            )
            return (pyside[-1], shiboken[-1]) if pyside and shiboken else None

        if all(override):
            pair = (override[0], override[1])
            for candidate, package in zip(pair, ("PySide6", "shiboken6")):
                if candidate is None or not candidate.is_file():
                    raise ValueError(f"Android wheel does not exist: {candidate}")
                if self._android_wheel_version(candidate, package, arch) != qt_version:
                    raise ValueError(
                        f"Android {package} wheel must match installed "
                        f"PySide6 {qt_version} and target {arch}: {candidate}"
                    )
            return pair

        commercial_cache = root_cache / "commercial"
        public_cache = root_cache / "public"

        # Qt's license provider performs the actual authorization check. A
        # locally installed qtpip alone does not establish a commercial license.
        qtpip = shutil.which("qtpip")
        if qtpip:
            cached = discover(commercial_cache)
            if cached:
                self._notice(f"Using cached commercial Qt Android wheels ({arch}).")
                return cached
            commercial_cache.mkdir(parents=True, exist_ok=True)
            # qtpip prints '[ERROR] Commercial License NOT found' even when
            # its process exits successfully. Capture and classify its output
            # instead of letting those internal log levels mislead the user.
            with self._status("Checking commercial Qt Android wheels..."):
                status, output = self._runner.run_capture(
                    [qtpip, "download", "PySide6", "--android", "--arch", arch],
                    cwd=str(commercial_cache),
                )
            downloaded = discover(commercial_cache)
            if downloaded is not None:
                self._notice(f"Using commercial Qt Android wheels ({arch}).")
                return downloaded
            license_missing = (
                "commercial license not found" in output.lower()
                or "no commercial license found" in output.lower()
            )
            if license_missing:
                self._warning(
                    "Qt Commercial license not available; switching to the "
                    "public Qt for Python Android repository."
                )
            else:
                self._warning(
                    f"Qt commercial download produced no matching wheels "
                    f"(exit {status}); trying the public Qt archive."
                )
        else:
            self._notice("No commercial Qt downloader found; using the public Qt archive.")

        self._notice(
            f"Resolving public PySide6/Shiboken6 {qt_version} Android {arch} wheels..."
        )
        with self._status("Downloading/verifying public Qt Android wheels..."):
            pair = QtAndroidWheelArchive().resolve(
                qt_version, arch, public_cache
            )
        self._notice("Public Qt Android wheels ready.")
        return pair

    def _repair_qt_android_gradle(self, root: Path, *, debug: bool) -> bool:
        """Repair Qt bootstrap's missing Qt6Android.jar classpath entry.

        Some PySide6/python-for-android combinations add only
        Qt6AndroidBindings.jar to build.gradle, although QtActivity inherits
        QtActivityBase from Qt6Android.jar. Work only on the generated Gradle
        distribution, never on the user's sources or global Gradle settings.
        """
        dists = root / ".buildozer" / "android" / "platform"
        if not dists.exists():
            return False
        candidates = sorted(
            dists.glob("build-*/dists/*/build.gradle"),
            key=lambda p: p.stat().st_mtime, reverse=True,
        )
        for gradle in candidates:
            distribution = gradle.parent
            libs = distribution / "src" / "main" / "libs"
            bindings = libs / "Qt6AndroidBindings.jar"
            if not bindings.is_file():
                continue
            source = root / "deployment" / "jar" / "PySide6" / "jar" / "Qt6Android.jar"
            if not source.is_file():
                continue
            try:
                with ZipFile(source) as jar:
                    if "org/qtproject/qt/android/QtActivityBase.class" not in jar.namelist():
                        continue
            except BadZipFile:
                continue

            content = gradle.read_text(encoding="utf-8")
            binding_line = "implementation files('src/main/libs/Qt6AndroidBindings.jar')"
            qt_line = "implementation files('src/main/libs/Qt6Android.jar')"
            if binding_line not in content:
                continue
            # The user may have added the Gradle declaration during an
            # earlier attempt. Buildozer regenerates src/main/libs on each
            # invocation, which removes the JAR while leaving build.gradle.
            # Repair either missing piece, not just a missing declaration.
            qt_jar = libs / "Qt6Android.jar"
            try:
                current = qt_jar.is_file() and qt_jar.read_bytes() == source.read_bytes()
            except OSError:
                current = False
            if qt_line in content and current:
                continue
            if not current:
                shutil.copy2(source, qt_jar)
            if qt_line not in content:
                gradle.write_text(
                    content.replace(binding_line, binding_line + "\n    " + qt_line, 1),
                    encoding="utf-8",
                )
            self._warning(
                "Qt Android bootstrap omitted Qt6Android.jar from Gradle; "
                "Velto added the missing Java dependency and is resuming packaging."
            )
            self._run(
                [str(distribution / "gradlew"),
                 "assembleDebug" if debug else "bundleRelease"],
                distribution,
            )
            return True
        return False

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

        # Buildozer fails during preflight without javac, but the Qt wrapper
        # can swallow that failure and report a successful exit status.
        # Diagnose the missing host dependency before any downloads/builds.
        if not request.init_only and shutil.which("javac") is None:
            raise RuntimeError(
                "Android build requires a JDK with 'javac' on PATH. "
                "On Arch Linux install jdk17-openjdk and verify 'javac -version'; "
                "configure JAVA_HOME/PATH if Java is already installed."
            )

        wheel_pyside, wheel_shiboken = self._qt_android_wheels(request)
        command = self._command("pyside6-android-deploy")
        args = [command]
        if request.init_only:
            args.extend(["--init", "--name", request.app_name])
        else:
            args.extend(["--config-file", str(spec), "--force"])
            # Qt's static scan does not see imports made inside PSX's native
            # adapter. Explicit modules survive automatic spec regeneration.
            # Retain the generated Java/Gradle project when Android fails,
            # so missing classpath JARs can be inspected and diagnosed.
            args.extend([
                "--extra-modules", "Core,Gui,Widgets",
                "--keep-deployment-files",
            ])
        args.extend([
            "--wheel-pyside", str(wheel_pyside),
            "--wheel-shiboken", str(wheel_shiboken),
        ])
        try:
            self._run(args, root)
        except RuntimeError:
            # Qt's wrapper can report Buildozer failure (or exit 0 without an
            # APK) when QtActivityBase is absent from Gradle's Java classpath.
            # Retry only when the generated project proves the missing-JAR
            # condition; unrelated deployment failures retain their error.
            if request.init_only or not self._repair_qt_android_gradle(
                root, debug=request.debug
            ):
                raise
        if request.init_only:
            if not spec.is_file():
                raise RuntimeError(
                    "Qt Android deploy completed without generating pysidedeploy.spec."
                )
            return None

        try:
            artifact = self._latest(root, (".apk", ".aab"))
        except RuntimeError as exc:
            if self._repair_qt_android_gradle(root, debug=request.debug):
                artifact = self._latest(root, (".apk", ".aab"))
            else:
                raise RuntimeError(
                    "PySide6 Android deployment returned without an APK/AAB. "
                    "The Qt deployment wrapper may have swallowed a Buildozer error. "
                    "Inspect the preceding toolchain output for its first failure "
                    "(e.g. missing javac, SDK, or NDK)."
                ) from exc
        return MobileBuildArtifact(artifact, request.target, request.framework,
                                   artifact.suffix.lstrip("."))
