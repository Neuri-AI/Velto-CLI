"""PySide6 Android wheel resolution, Qt Java bootstrap, and APK packaging."""
import shutil
from importlib.metadata import PackageNotFoundError, version as distribution_version
from pathlib import Path
import xml.etree.ElementTree as ET
from zipfile import BadZipFile, ZipFile

from velto_cli.adapters.mobile.common.android_icons import select_android_project_icon
from velto_cli.adapters.mobile.qt.wheel_archive import QtAndroidWheelArchive
from velto_cli.adapters.mobile.qt.android_sources import (
    uses_velto, staged_velto_sources, verify_velto_apk_sources,
    complete_qt_android_private_assets, verify_qt_android_runtime_assets,
)
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

    def _repair_qt_android_loader(self, artifact: Path, *, debug: bool) -> bool:
        """Repair the generated QtLoader library list when it omits 'main'.

        PySide6's Android deploy may emit arm64-v8a;lib_arm64-v8a.so in
        load_local_libs even though the AndroidManifest declares lib_name=main
        and the packaged library is libmain_arm64-v8a.so. Restrict the fix to
        that proven mismatch in the artifact's own Gradle distribution.
        """
        distribution = next(
            (parent for parent in artifact.parents if parent.parent.name == "dists"),
            None,
        )
        if distribution is None:
            return False
        manifest = distribution / "src" / "main" / "AndroidManifest.xml"
        resources = distribution / "src" / "main" / "res" / "values" / "libs.xml"
        if not manifest.is_file() or not resources.is_file():
            return False

        try:
            manifest_root = ET.parse(manifest).getroot()
            android_ns = "{http://schemas.android.com/apk/res/android}"
            library_names = [
                element.get(android_ns + "value")
                for element in manifest_root.iter("meta-data")
                if element.get(android_ns + "name") == "android.app.lib_name"
            ]
            if library_names != ["main"]:
                return False

            tree = ET.parse(resources)
            loader_arrays = [
                element for element in tree.getroot().findall("array")
                if element.get("name") == "load_local_libs"
            ]
            if len(loader_arrays) != 1:
                return False
            items = loader_arrays[0].findall("item")
        except ET.ParseError as exc:
            raise RuntimeError(
                f"Cannot parse Qt Android loader metadata: {exc}"
            ) from exc

        wrong = "arm64-v8a;lib_arm64-v8a.so"
        correct = "arm64-v8a;libmain_arm64-v8a.so"
        matches = [item for item in items if (item.text or "").strip() == wrong]
        if not matches:
            return False
        if len(matches) != 1 or any(
            (item.text or "").strip() == correct for item in items
        ):
            raise RuntimeError(
                f"Ambiguous QtLoader main library entries in {resources}"
            )
        native_lib = distribution / "libs" / "arm64-v8a" / "libmain_arm64-v8a.so"
        if not native_lib.is_file():
            raise RuntimeError(
                f"QtLoader references {wrong} but the expected native "
                f"library is absent: {native_lib}"
            )

        matches[0].text = correct
        tree.write(resources, encoding="utf-8", xml_declaration=True)
        self._warning(
            "QtLoader references missing lib_arm64-v8a.so; repaired the "
            "generated loader list to libmain_arm64-v8a.so and rebuilding."
        )
        self._run(
            [str(distribution / "gradlew"),
             "assembleDebug" if debug else "bundleRelease"],
            distribution,
        )
        return True

    def _repair_qt_android_qpa(self, artifact: Path, *, debug: bool) -> bool:
        """Preload the Android QPA plugin which implements QtActivityDelegate JNI.

        Some PySide6/p4a deployments keep qtforandroid only in the Python
        bundle. QtActivityDelegateBase calls updateUiMotionScale() before
        Python starts, so the plugin must also be in the APK native library
        directory and loaded by QtLoader before the main library.
        """
        distribution = next(
            (parent for parent in artifact.parents if parent.parent.name == "dists"),
            None,
        )
        if distribution is None:
            return False

        resources = distribution / "src/main/res/values/libs.xml"
        if not resources.is_file():
            return False
        arch = "arm64-v8a"
        name = "libplugins_platforms_qtforandroid_arm64-v8a.so"
        bundle_root = distribution / f"_python_bundle__{arch}"
        source = (
            bundle_root / "_python_bundle/site-packages/PySide6/Qt/plugins"
            / "platforms" / name
        )
        if not bundle_root.is_dir():
            return False  # Incomplete or unrelated Gradle distribution.
        if not source.is_file():
            raise RuntimeError(
                f"Qt Android QPA plugin is missing from the Python bundle: {source}"
            )

        manifest = distribution / "src/main/AndroidManifest.xml"
        if not manifest.is_file():
            return False
        try:
            manifest_root = ET.parse(manifest).getroot()
            android_ns = "{http://schemas.android.com/apk/res/android}"
            if not any(
                node.get(android_ns + "name") == "android.app.lib_name"
                and node.get(android_ns + "value") == "main"
                for node in manifest_root.iter("meta-data")
            ):
                return False
            tree = ET.parse(resources)
            loader = tree.getroot().find("./array[@name='load_local_libs']")
            if loader is None:
                raise RuntimeError(
                    f"Qt Android deployment has no load_local_libs array: {resources}"
                )
        except ET.ParseError as exc:
            raise RuntimeError(
                f"Cannot parse Qt Android QPA loader metadata: {exc}"
            ) from exc

        plugin_entry = f"{arch};{name}"
        entries = [(item.text or "").strip() for item in loader.findall("item")]
        main_entry = f"{arch};libmain_arm64-v8a.so"
        if main_entry not in entries:
            raise RuntimeError(
                f"Qt Android loader does not declare {main_entry}: {resources}"
            )
        if entries.count(plugin_entry) > 1:
            raise RuntimeError(f"Duplicate Qt QPA loader entries in {resources}")

        target = distribution / "libs" / arch / name
        needs_copy = not target.is_file() or target.read_bytes() != source.read_bytes()
        needs_order = (
            plugin_entry not in entries
            or entries.index(plugin_entry) > entries.index(main_entry)
        )
        if not needs_copy and not needs_order:
            return False
        target.parent.mkdir(parents=True, exist_ok=True)
        if needs_copy:
            shutil.copy2(source, target)
        if needs_order:
            for item in tuple(loader.findall("item")):
                if (item.text or "").strip() == plugin_entry:
                    loader.remove(item)
            new_item = ET.Element("item")
            new_item.text = plugin_entry
            loader.insert(0, new_item)
            tree.write(resources, encoding="utf-8", xml_declaration=True)
        self._warning(
            "Qt Android QPA plugin supplies the QtActivityDelegate JNI methods; "
            "Velto packaged it as a native library and added it to QtLoader "
            "before the main application library."
        )
        self._run(
            [str(distribution / "gradlew"),
             "assembleDebug" if debug else "bundleRelease"],
            distribution,
        )
        return True

    @staticmethod
    def _apply_qt_android_icon(artifact: Path, source: Path) -> bool:
        """Replace p4a's default launcher image in the generated Gradle tree.

        AndroidManifest points to @mipmap/icon. Updating that resource before
        a Gradle rebuild preserves Android signing and avoids patching an APK.
        """
        distribution = next(
            (parent for parent in artifact.parents
             if parent.parent.name == "dists" and
             (parent / "src/main/AndroidManifest.xml").is_file()),
            None,
        )
        if distribution is None:
            raise RuntimeError(
                f"Qt Android Gradle distribution not found for APK: {artifact}. "
                "Expected a built APK under .buildozer/android/platform/"
                "build-*/dists/<name>/build/outputs/."
            )
        res = distribution / "src/main/res"
        manifest = ET.parse(distribution / "src/main/AndroidManifest.xml")
        android_ns = "{http://schemas.android.com/apk/res/android}"
        application = manifest.getroot().find("application")
        reference = (
            application.get(android_ns + "icon")
            if application is not None else None
        )
        if not reference or not reference.startswith("@mipmap/"):
            raise RuntimeError(
                f"Qt Android manifest does not declare a mipmap launcher icon: "
                f"{reference!r} in {distribution}"
            )
        stem = reference.split("/", 1)[1]
        targets = sorted(
            path for path in res.glob(f"mipmap*/{stem}.png")
            if path.is_file()
        )
        if not targets:
            raise RuntimeError(
                f"Qt Android launcher PNG resources missing for {reference}: {res}"
            )
        source_bytes = source.read_bytes()
        changed = False
        for target in targets:
            if target.read_bytes() != source_bytes:
                shutil.copyfile(source, target)
                changed = True
        return changed

    @staticmethod
    def _requires_velto_runtime(entry: Path) -> bool:
        return uses_velto(entry)

    def _verify_qt_android_velto_bundle(
        self, artifact: Path, *, entry: Path
    ) -> None:
        if uses_velto(entry):
            verify_velto_apk_sources(artifact)

    @staticmethod
    def _qt_gradle_artifact(
        root: Path, artifact: Path, *, debug: bool
    ) -> Path:
        """Prefer the built Gradle artifact over p4a's copied root-level APK.

        The Qt deploy wrapper copies the APK into the project root and may
        give it a versioned name. The Gradle distribution is authoritative
        for QtLoader repairs and for identifying the built Python payload.
        """
        base = root / ".buildozer/android/platform"
        if debug:
            patterns = ("build-*/dists/*/build/outputs/apk/debug/*.apk",)
        else:
            patterns = ("build-*/dists/*/build/outputs/bundle/release/*.aab",)
        candidates = [
            item for pattern in patterns for item in base.glob(pattern)
            if item.is_file()
        ]
        if not candidates:
            return artifact
        return max(candidates, key=lambda item: item.stat().st_mtime_ns)

    def _pyside_android(self, request: MobileFreezeRequest) -> MobileBuildArtifact | None:
        """Build with temporary framework sources; never modify user code permanently."""
        enabled = (
            not request.init_only
            and uses_velto(request.project_root / request.entry_point)
        )
        with staged_velto_sources(request.project_root, enabled=enabled):
            return self._pyside_android_impl(request)

    def _pyside_android_impl(
        self, request: MobileFreezeRequest
    ) -> MobileBuildArtifact | None:
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
        artifact = self._qt_gradle_artifact(
            root, artifact, debug=request.debug
        )
        if self._repair_qt_android_loader(artifact, debug=request.debug):
            artifact = self._qt_gradle_artifact(
                root, artifact, debug=request.debug
            )
        if self._repair_qt_android_qpa(artifact, debug=request.debug):
            artifact = self._qt_gradle_artifact(
                root, artifact, debug=request.debug
            )
        icon = select_android_project_icon(root)
        icon_changed = False
        if icon is not None:
            icon_changed = self._apply_qt_android_icon(artifact, icon)
            self._notice(
                f"Android launcher icon: {icon.relative_path} "
                f"(folder: {icon.folder})"
            )
        else:
            self._warning(
                "No project PNG launcher icon found in resources/linux/icons, "
                "resources/mac/icons or resources/base/icons; "
                "keeping the Android toolchain default."
            )

        if enabled := uses_velto(root / request.entry_point):
            added = complete_qt_android_private_assets(
                root, artifact, self._run, debug=request.debug,
            )
            if added:
                self._notice(
                    f"Included {len(added)} Velto settings/resource files "
                    "in the Qt Android package."
                )
            elif icon_changed:
                # Assets already complete: Gradle must still package the new
                # launcher icon. Never modify a signed APK in place.
                distribution = next(
                    p for p in artifact.parents if (p / "gradlew").is_file()
                )
                self._run([
                    str(distribution / "gradlew"),
                    "assembleDebug" if request.debug else "bundleRelease",
                ], distribution)
        elif icon_changed:
            distribution = next(
                p for p in artifact.parents if (p / "gradlew").is_file()
            )
            self._run([
                str(distribution / "gradlew"),
                "assembleDebug" if request.debug else "bundleRelease",
            ], distribution)
        if icon_changed or (enabled and added):
            artifact = self._qt_gradle_artifact(
                root, artifact, debug=request.debug
            )
        self._verify_qt_android_velto_bundle(
            artifact, entry=root / request.entry_point
        )
        if enabled:
            verify_qt_android_runtime_assets(artifact, root)
        published = self._publish_android_artifact(
            artifact, root, backend="qt"
        )
        return MobileBuildArtifact(published, request.target, request.framework,
                                   published.suffix.lstrip("."))
