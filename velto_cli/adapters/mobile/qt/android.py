"""PySide6 Android wheel resolution, Qt Java bootstrap, and APK packaging."""
import shutil
from importlib.metadata import PackageNotFoundError, version as distribution_version
from pathlib import Path
import xml.etree.ElementTree as ET
from zipfile import BadZipFile, ZipFile

from velto_cli.adapters.mobile.common.android_icons import select_android_project_icon
from velto_cli.adapters.mobile.qt.android_final_apk import finalize_copied_debug_apk
from velto_cli.adapters.mobile.qt.android_build_inputs import isolate_previous_android_build_outputs
from velto_cli.adapters.mobile.qt.android_incremental import (
    try_incremental_build, record_full_build,
)
from velto_cli.adapters.mobile.qt.android_svg import (
    project_uses_svg, assert_android_svg_wheel, configure_android_svg_spec,
    verify_android_svg_apk,
)
from velto_cli.adapters.mobile.qt.wheel_archive import QtAndroidWheelArchive
from velto_cli.adapters.mobile.qt.android_sources import (
    uses_velto, staged_velto_sources, verify_velto_apk_sources,
    complete_qt_android_private_assets, verify_qt_android_runtime_assets,
    _gradle_directory, _gradle_command,
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
        # The Qt wrapper can emit a debug APK even if the Velto CLI was
        # invoked without --debug. Choose the actual artifact kind, not the
        # requested flag, and never silently fall back to the root copy when
        # a Gradle build is expected.
        base = root / ".buildozer/android/platform"
        patterns = {
            ".apk": ("build-*/dists/*/build/outputs/apk/debug/*.apk",
                     "build-*/dists/*/build/outputs/apk/release/*.apk"),
            ".aab": ("build-*/dists/*/build/outputs/bundle/release/*.aab",),
        }
        desired = artifact.suffix.lower()
        search = patterns.get(desired, (*patterns[".apk"], *patterns[".aab"]))
        candidates = [
            item for pattern in search for item in base.glob(pattern)
            if item.is_file()
        ]
        if not candidates:
            # PySide6 can clean its generated Gradle project after copying
            # a complete APK into the project root.
            return artifact
        return max(candidates, key=lambda item: item.stat().st_mtime_ns)

    def _pyside_android(self, request: MobileFreezeRequest) -> MobileBuildArtifact | None:
        """Build normally for native changes; re-sign the APK for source-only changes."""
        enabled = (
            not request.init_only
            and uses_velto(request.project_root / request.entry_point)
        )
        # The incremental cache is an opt-out implementation detail of debug
        # builds, never an alternate public API. It runs BEFORE transient
        # framework packages are staged into the project tree.
        if enabled and not request.clean:
            try:
                quick = try_incremental_build(request.project_root, request)
            except Exception as exc:
                # Corrupt cache or unpatchable debug APK must not block a
                # normal PySide6 build or leave partially written artifacts.
                self._warning(
                    "Android incremental reuse unavailable; using full "
                    f"deployment: {exc}"
                )
                quick = None
            if quick is not None:
                self._notice("Reused Android runtime; updated signed APK.")
                return MobileBuildArtifact(
                    quick, request.target, request.framework, "apk",
                )
        with staged_velto_sources(request.project_root, enabled=enabled):
            built = self._pyside_android_impl(request)
        # Source staging has now been removed. Snapshot the real developer
        # tree, not the temporary Velto/Qyro/PSX package copies.
        if enabled and built is not None and built.kind == "apk":
            try:
                record_full_build(request.project_root, built.path, request)
            except (OSError, RuntimeError, ValueError) as exc:
                self._warning(
                    "Android incremental cache unavailable; future builds "
                    f"will use the normal toolchain: {exc}"
                )
        return built

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
        # QPixmap needs the SVG imageformats plugin, not just QtGui.
        # Verify that the target Android wheel can supply it before p4a
        # starts compiling, then declare BOTH the QtSvg dependency and the
        # imageformats plugin in pyside's Android-specific spec section.
        svg_required = not request.init_only and project_uses_svg(root)
        if svg_required:
            assert_android_svg_wheel(wheel_pyside)
            configure_android_svg_spec(spec)
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
                "--extra-modules", (
                    "Core,Gui,Widgets,Svg" if svg_required
                    else "Core,Gui,Widgets"
                ),
                "--keep-deployment-files",
            ])
        args.extend([
            "--wheel-pyside", str(wheel_pyside),
            "--wheel-shiboken", str(wheel_shiboken),
        ])
        # Buildozer recursively scans the project. Prior APK inspection
        # directories under build/android/qt can contain extracted files
        # owned by another UID; those are NOT app sources. Isolate build
        # outputs outside the project while pyside6-android-deploy runs.
        preexisting_artifacts = {
            p.resolve(): (p.stat().st_mtime_ns, p.stat().st_size)
            for suffix in ("*.apk", "*.aab")
            for p in root.glob(suffix) if p.is_file()
        }
        with isolate_previous_android_build_outputs(root):
            try:
                self._run(args, root)
            except RuntimeError:
                # Retry only for proven Qt bootstrap classpath defects.
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
        # PySide6 sometimes prints a Buildozer traceback yet returns success.
        # A previously built APK is not evidence the current build succeeded.
        previous = preexisting_artifacts.get(artifact.resolve())
        if previous is not None and (
            artifact.stat().st_mtime_ns, artifact.stat().st_size
        ) == previous:
            raise RuntimeError(
                "PySide6 Android deploy produced no new APK/AAB; "
                "a previous artifact was found unchanged. Inspect the "
                "Buildozer traceback above instead of treating it as success."
            )
        if self._repair_qt_android_loader(artifact, debug=request.debug):
            artifact = self._qt_gradle_artifact(
                root, artifact, debug=request.debug
            )
        if self._repair_qt_android_qpa(artifact, debug=request.debug):
            artifact = self._qt_gradle_artifact(
                root, artifact, debug=request.debug
            )
        # PySide6 can remove its generated Gradle tree before returning.
        # Complete the copied APK and re-sign it instead of rebuilding Qt.
        icon = select_android_project_icon(root)
        if icon is not None:
            self._notice(
                f"Android launcher icon: {icon.relative_path} "
                f"(folder: {icon.folder})"
            )
        else:
            self._warning("No project PNG launcher icon found; keeping Qt default.")
        enabled = uses_velto(root / request.entry_point)
        if _gradle_directory(artifact) is None:
            if artifact.suffix.lower() != ".apk":
                raise RuntimeError(
                    "Qt removed Gradle sources for a release AAB. "
                    "Cannot safely modify the bundle without Gradle."
                )
            artifact, added, icon_changed = finalize_copied_debug_apk(
                artifact, root, icon=icon.source if icon else None,
            )
            if added:
                self._notice(
                    f"Included {len(added)} Velto settings/resource files "
                    "in the signed copied Android APK."
                )
            if icon_changed:
                self._notice("Updated Android launcher icon in verified signed APK.")
        else:
            artifact_is_apk = artifact.suffix.lower() == ".apk"
            icon_changed = False
            if icon is not None:
                icon_changed = self._apply_qt_android_icon(
                    artifact, icon.source
                )
            added = ()
            if enabled:
                added = complete_qt_android_private_assets(
                    root, artifact, self._run, debug=artifact_is_apk,
                )
            if icon_changed and not added:
                distribution = _gradle_directory(artifact)
                self._run([
                    _gradle_command(distribution),
                    "assembleDebug" if artifact_is_apk else "bundleRelease",
                ], distribution)
            if added or icon_changed:
                artifact = self._qt_gradle_artifact(
                    root, artifact, debug=request.debug
                )
        self._verify_qt_android_velto_bundle(
            artifact, entry=root / request.entry_point
        )
        if enabled:
            verify_qt_android_runtime_assets(artifact, root)
        if svg_required:
            verify_android_svg_apk(artifact)
        published = self._publish_android_artifact(
            artifact, root, backend="qt"
        )
        return MobileBuildArtifact(published, request.target, request.framework,
                                   published.suffix.lstrip("."))
