"""PySide6 Android wheel resolution, Qt Java bootstrap, and APK packaging."""
import shutil
import sys
from dataclasses import replace
from importlib.metadata import PackageNotFoundError, version as distribution_version
from pathlib import Path
import xml.etree.ElementTree as ET
from zipfile import BadZipFile, ZipFile

from velto_cli.adapters.mobile.common.android_icons import select_android_project_icon
from velto_cli.adapters.mobile.common.android_entrypoint import android_entrypoint
from velto_cli.adapters.mobile.qt.android_final_apk import finalize_copied_debug_apk
from velto_cli.adapters.mobile.qt.android_release import (
    _release_credentials, android_resource_protection_enabled, protect_android_apk,
)
from velto_cli.adapters.mobile.qt.android_build_inputs import (
    isolate_previous_android_build_outputs, staged_android_project,
)
from velto_cli.adapters.mobile.qt.android_build_config import (
    verify_android_exclusions, load_android_build_config,
)
from velto_cli.adapters.mobile.qt.android_incremental import (
    try_incremental_build, record_full_build, incremental_cache_miss_reason, _scan,
)
from velto_cli.adapters.mobile.qt.android_local_wheels import local_wheel_overrides, local_wheels
from velto_cli.adapters.mobile.qt.features import (
    EnabledQtAndroidFeatures, enabled_qt_android_features,
)
from velto_cli.adapters.mobile.qt.wheel_archive import QtAndroidWheelArchive
from velto_cli.adapters.mobile.qt.android_sources import (
    project_uses_velto, verify_velto_apk_sources,
    complete_qt_android_private_assets, verify_qt_android_runtime_assets,
    _gradle_directory, _gradle_command,
)
from velto_cli.domain.mobile import MobileBuildArtifact, MobileFreezeRequest, MobileTarget


class QtAndroidBuilder:
    def _install_android_splash_bridge(self, artifact: Path, root: Path, *, debug: bool) -> bool:
        distribution = _gradle_directory(artifact)
        if distribution is None:
            return False
        manifest = distribution / "src/main/AndroidManifest.xml"
        if not manifest.is_file():
            return False
        namespace = "{http://schemas.android.com/apk/res/android}"
        tree = ET.parse(manifest)
        activity = next((node for node in tree.getroot().iter("activity")
                         if (node.get(namespace + "name") or "").endswith("PythonActivity")), None)
        if activity is None:
            return False
        target = "org.velto.SplashPythonActivity"
        changed = activity.get(namespace + "name") != target
        activity.set(namespace + "name", target)
        source = distribution / "src/main/java/org/velto/SplashPythonActivity.java"
        source.parent.mkdir(parents=True, exist_ok=True)
        gradle = distribution / "build.gradle"
        if not gradle.is_file():
            raise RuntimeError("Android splash bridge cannot locate build.gradle")
        dependency = 'implementation "androidx.core:core-splashscreen:1.0.1"'
        gradle_text = gradle.read_text(encoding="utf-8")
        if dependency not in gradle_text:
            marker = "dependencies {"
            # The first dependencies block belongs to buildscript and only
            # accepts Gradle classpath entries. p4a's application block is
            # the final one and owns implementation dependencies.
            position = gradle_text.rfind(marker)
            if position < 0:
                raise RuntimeError("Android splash bridge cannot locate Gradle dependencies block")
            gradle.write_text(gradle_text[:position] + marker + "\n    " + dependency
                              + gradle_text[position + len(marker):],
                              encoding="utf-8")
            changed = True
        java = '''package org.velto;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.view.SurfaceHolder;
import android.view.SurfaceView;
import android.view.View;
import android.view.ViewGroup;
import android.view.ViewTreeObserver;
import androidx.core.splashscreen.SplashScreen;
import org.kivy.android.PythonActivity;
public class SplashPythonActivity extends PythonActivity {
  private static volatile boolean veltoReady = false;
  private final Handler mainHandler = new Handler(Looper.getMainLooper());
  private boolean releaseScheduled = false;
  @Override public void onCreate(Bundle state) {
    SplashScreen splash = SplashScreen.installSplashScreen(this);
    splash.setKeepOnScreenCondition(() -> !veltoReady);
    super.onCreate(state);
    // Qt's Android surface belongs to the Java activity, so use that surface
    // as the readiness signal.  This avoids a Python-to-JNI bridge (which is
    // not ABI-compatible with every Qt/p4a runtime) and keeps the splash up
    // until Qt has had a turn to submit its first buffer.
    getWindow().getDecorView().post(this::watchQtSurface);
    // Last-resort recovery for a broken third-party Qt runtime.
    new Handler(Looper.getMainLooper()).postDelayed(() -> veltoReady = true, 15000);
  }
  private void watchQtSurface() {
    final View root = getWindow().getDecorView();
    final ViewTreeObserver observer = root.getViewTreeObserver();
    observer.addOnGlobalLayoutListener(new ViewTreeObserver.OnGlobalLayoutListener() {
      @Override public void onGlobalLayout() {
        SurfaceView surface = findQtSurface(root);
        if (surface == null) return;
        if (observer.isAlive()) observer.removeOnGlobalLayoutListener(this);
        SurfaceHolder holder = surface.getHolder();
        holder.addCallback(new SurfaceHolder.Callback() {
          @Override public void surfaceCreated(SurfaceHolder ignored) { scheduleRelease(); }
          @Override public void surfaceChanged(SurfaceHolder ignored, int format, int width, int height) { }
          @Override public void surfaceDestroyed(SurfaceHolder ignored) { }
        });
        if (holder.getSurface().isValid()) scheduleRelease();
      }
    });
  }
  private SurfaceView findQtSurface(View view) {
    if (view instanceof SurfaceView
        && view.getClass().getName().equals("org.qtproject.qt.android.QtSurface")) {
      return (SurfaceView) view;
    }
    if (!(view instanceof ViewGroup)) return null;
    ViewGroup group = (ViewGroup) view;
    for (int index = 0; index < group.getChildCount(); index++) {
      SurfaceView surface = findQtSurface(group.getChildAt(index));
      if (surface != null) return surface;
    }
    return null;
  }
  private void scheduleRelease() {
    if (releaseScheduled) return;
    releaseScheduled = true;
    mainHandler.postDelayed(() -> veltoReady = true, 120);
  }
  public static void markVeltoReady() { veltoReady = true; }
}
'''
        if not source.is_file() or source.read_text(encoding="utf-8") != java:
            source.write_text(java, encoding="utf-8")
            changed = True
        splash = load_android_build_config(root).get("splash")
        if splash is not None:
            if not isinstance(splash, dict):
                raise ValueError("android.json splash must be an object")
            image, background = splash.get("image"), splash.get("background", "#171717")
            if not isinstance(image, str) or not isinstance(background, str):
                raise ValueError("android.json splash requires image and background strings")
            candidate = (root / image).resolve()
            if (not candidate.is_relative_to(root.resolve()) or not candidate.is_file()
                    or candidate.suffix.lower() != ".png"):
                raise ValueError("android.json splash.image must be a project PNG")
            drawable = distribution / "src/main/res/drawable/velto_splash.png"
            drawable.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(candidate, drawable)
            values = distribution / "src/main/res/values-v31/velto_splash.xml"
            values.parent.mkdir(parents=True, exist_ok=True)
            values.write_text(
                '<resources><style name="VeltoSplashTheme" parent="android:style/Theme.Material.NoActionBar">'
                f'<item name="android:windowSplashScreenBackground">{background}</item>'
                '<item name="android:windowSplashScreenAnimatedIcon">@drawable/velto_splash</item>'
                '</style></resources>', encoding="utf-8")
            activity.set(namespace + "theme", "@style/VeltoSplashTheme")
            changed = True
        if not changed:
            return False
        tree.write(manifest, encoding="utf-8", xml_declaration=True)
        self._run([_gradle_command(distribution), "assembleDebug" if debug else "bundleRelease"], distribution)
        return True

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
            if any((item.text or "").strip() == correct for item in items):
                return False
            native_lib = distribution / "libs" / "arm64-v8a" / "libmain_arm64-v8a.so"
            if not native_lib.is_file():
                return False
            item = ET.SubElement(loader_arrays[0], "item")
            item.text = correct
            tree.write(resources, encoding="utf-8", xml_declaration=True)
            self._warning("QtLoader omitted its main library; repaired the generated loader list.")
            self._run([str(distribution / "gradlew"),
                       "assembleDebug" if debug else "bundleRelease"], distribution)
            return True
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

    def _verify_qt_android_velto_bundle(
        self, artifact: Path, *, entry: Path, project_root: Path | None = None
    ) -> None:
        if project_uses_velto(project_root or entry.parent, entry):
            verify_velto_apk_sources(artifact, project_root)

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
            and project_uses_velto(request.project_root, request.project_root / request.entry_point)
        )
        # A user-owned main.py must share the generated launcher path. Do not
        # incrementally replace that combined file with the original source.
        launcher_conflict = (request.entry_point != "main.py"
                             and (request.project_root / "main.py").exists())
        resources_protected = android_resource_protection_enabled(request.project_root)
        overrides = local_wheels(request.project_root)
        # A protected APK contains compiled application modules, so only
        # unprotected debug builds can safely replace source files in-place.
        if (enabled and request.debug and not request.clean and not request.protect_source
                and not launcher_conflict and not overrides):
            try:
                quick = try_incremental_build(request.project_root, request)
            except Exception as exc:
                self._warning(f"Android incremental reuse unavailable; using full deployment: {exc}")
                quick = None
            if quick is not None:
                if resources_protected:
                    protect_android_apk(
                        quick, request.project_root, debug=True,
                        toolchain_root=request.project_root,
                        entry_point=request.entry_point, protect_source=False,
                        protect_resources=True,
                    )
                    # Store the sealed artifact, never the transient archive
                    # where incremental patching temporarily restored assets.
                    record_full_build(request.project_root, quick, request)
                self._notice("Reused Android runtime; updated signed APK.")
                return MobileBuildArtifact(quick, request.target, request.framework, "apk")
            self._notice(incremental_cache_miss_reason(request.project_root))
        if enabled and not request.debug and not request.init_only:
            # Validate release signing before downloading Qt wheels or running
            # Buildozer. Never spend a full compile on an unsignable APK.
            _release_credentials(request.project_root)
        if request.init_only:
            # --init-spec creates the developer-owned specification; it is not
            # an APK compilation and must write to the actual project.
            with android_entrypoint(request.project_root, request.entry_point):
                return self._pyside_android_impl(request)
        cache_snapshot = None
        if (enabled and request.debug and not request.protect_source
                and not launcher_conflict and not overrides):
            try:
                _, hashes, native = _scan(request.project_root)
                cache_snapshot = (hashes, native)
            except (OSError, RuntimeError, ValueError) as exc:
                self._warning(f"Android incremental cache unavailable: {exc}")
        # Work exclusively inside a disposable, filtered project mirror.
        # The original app (including excluded settings) is never moved.
        with staged_android_project(request.project_root) as stage:
            staged_request = replace(request, project_root=stage)
            with local_wheel_overrides(overrides):
                with android_entrypoint(stage, request.entry_point):
                    built = self._pyside_android_impl(staged_request)
            if built is None:
                return None
            if not built.path.is_file():
                raise RuntimeError("Android stage produced no artifact")
            local_qyro = overrides.get("qyro-engine")
            if local_qyro is not None:
                verify_velto_apk_sources(built.path, request.project_root,
                                         qyro_wheel=local_qyro)
            # Verify the stage against ORIGINAL host-only exclusions, not
            # against the filtered stage's absent android.json/sign.json.
            verify_android_exclusions(built.path, request.project_root)
            if request.protect_source and request.debug:
                protect_android_apk(
                    built.path, request.project_root, debug=True,
                    toolchain_root=stage,
                    entry_point=request.entry_point,
                    obfuscate=load_android_build_config(
                        request.project_root
                    ).get("obfuscate", False),
                    protect_resources=resources_protected,
                )
                self._notice("Cython Android debug extensions signed and verified.")
            elif not request.debug:
                protect_android_apk(
                    built.path, request.project_root, debug=False, toolchain_root=stage,
                    entry_point=request.entry_point,
                    obfuscate=load_android_build_config(
                        request.project_root
                    ).get("obfuscate", False),
                    protect_resources=resources_protected,
                )
                self._notice("Cython Android release extensions verified and APK signed.")
            elif resources_protected:
                protect_android_apk(
                    built.path, request.project_root, debug=True, toolchain_root=stage,
                    entry_point=request.entry_point, protect_source=False,
                    protect_resources=True,
                )
                self._notice("Android protected resources encrypted and verified.")
            if cache_snapshot is not None:
                try:
                    if not record_full_build(request.project_root, built.path, request, cache_snapshot):
                        self._warning("Could not save the incremental cache: the project changed during the build or no longer matches the APK.")
                except (OSError, RuntimeError, ValueError) as exc:
                    self._warning(f"Android incremental cache unavailable: {exc}")
            verify_android_exclusions(built.path, request.project_root)
            output = request.project_root / "build" / "android" / "qt" / built.path.name
            output.parent.mkdir(parents=True, exist_ok=True)
            # Replace only a completed and verified APK, never a project file.
            temporary = output.with_name(output.name + ".velto-staged")
            try:
                shutil.copy2(built.path, temporary)
                temporary.replace(output)
            finally:
                temporary.unlink(missing_ok=True)
            built = MobileBuildArtifact(
                output, request.target, request.framework, output.suffix.lstrip("."),
            )
        return built

    def _pyside_android_impl(
        self, request: MobileFreezeRequest
    ) -> MobileBuildArtifact | None:
        root = request.project_root
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
        features = EnabledQtAndroidFeatures(())
        if not request.init_only:
            features = enabled_qt_android_features(root)
            features.prepare(wheel_pyside, spec)
        self._command("pyside6-android-deploy")
        args = [sys.executable, str(Path(__file__).with_name("android_deploy_entry.py")),
                "--velto-entry", request.entry_point]
        if request.init_only:
            args.extend(["--init", "--name", request.app_name, "--keep-deployment-files"])
        else:
            args.extend(["--config-file", str(spec), "--force"])
            # Qt's static scan does not see imports made inside PSX's native
            # adapter. Explicit modules survive automatic spec regeneration.
            # Retain the generated Java/Gradle project when Android fails,
            # so missing classpath JARs can be inspected and diagnosed.
            args.extend([
                "--extra-modules", ",".join(
                    ("Core", "Gui", "Widgets")
                    + features.modules
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
        # Keep python-for-android's APK invocation under PySide6's control.
        # Forcing a distribution build through APP_P4A_EXTRA_ARGS also forces
        # the subsequent Qt APK packaging phase, which lacks --qt-libs and
        # --load-local-libs. Any missing header recovery must run as a
        # *separate p4a create command*, after the normal APK is produced.
        with isolate_previous_android_build_outputs(root):
            try:
                self._run(args, root)
            except RuntimeError:
                if request.init_only or not self._repair_qt_android_gradle(
                    root, debug=request.debug
                ):
                    raise
        if request.init_only:
            if not spec.is_file():
                raise RuntimeError(
                    "Qt Android deploy completed without generating pysidedeploy.spec."
                )
            if not (root / "buildozer.spec").is_file():
                raise RuntimeError("Qt Android deploy did not generate buildozer.spec")
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
        if features.prepare_gradle(artifact, debug=request.debug, run=self._run):
            artifact = self._qt_gradle_artifact(
                root, artifact, debug=request.debug
            )
        if self._install_android_splash_bridge(artifact, root, debug=request.debug):
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
        enabled = project_uses_velto(root, root / request.entry_point)
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
            artifact, entry=root / request.entry_point, project_root=root
        )
        if enabled:
            verify_qt_android_runtime_assets(artifact, root)
        features.verify(artifact)
        published = self._publish_android_artifact(
            artifact, root, backend="qt"
        )
        return MobileBuildArtifact(published, request.target, request.framework,
                                   published.suffix.lstrip("."))
