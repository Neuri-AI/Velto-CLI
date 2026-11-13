"""Qt WebView deployment feature for PSX's native ``<WebView>`` port."""
from __future__ import annotations

import configparser
import io
import tarfile
from pathlib import Path
from zipfile import ZipFile
import shutil
import xml.etree.ElementTree as ET
from collections.abc import Callable


def project_uses_webview(root: Path) -> bool:
    """Detect PSX WebView use, including declarative markup and Python imports."""
    from velto_cli.adapters.mobile.qt.android_sources import _project_runtime_assets
    for path in _project_runtime_assets(root).values():
        if path.suffix.lower() != ".py":
            continue
        try:
            source = path.read_text(encoding="utf-8")
        except UnicodeError:
            continue
        if ("<WebView" in source or "<Webview" in source
                or "import WebView" in source or "import Webview" in source):
            return True
    return False


def assert_android_webview_wheel(wheel: Path) -> None:
    """Fail before deployment if the target wheel lacks Qt's Android plugin."""
    with ZipFile(wheel) as archive:
        names = [name.lower() for name in archive.namelist()]
    required = ("qtwebview.abi3.so", "libqt6webview_", "plugins/webview/",
                "qt6androidwebview.jar")
    if not all(any(token in name for name in names) for token in required):
        raise RuntimeError(
            "PySide6 Android wheel cannot supply QtWebView and its Android plugin. "
            f"Wheel checked: {wheel}. Supply a compatible Android PySide6 wheel."
        )


def configure_android_webview_spec(spec: Path) -> bool:
    """Declare the Qt module and platform plugin required by ``QWebView``."""
    parser = configparser.ConfigParser(interpolation=None)
    parser.optionxform = str
    parser.read(spec, encoding="utf-8")
    updates = {("qt", "modules"): "WebView", ("android", "plugins"): "webview_qtwebview_android"}
    changed = False
    for (section, field), required in updates.items():
        if not parser.has_section(section):
            parser.add_section(section)
        values = [item.strip() for item in parser.get(section, field, fallback="").split(",")
                  if item.strip()]
        existing = {item.lower().removeprefix("qt") if section == "qt" else item.lower()
                    for item in values}
        if required.lower() not in existing:
            values.append(required)
            parser.set(section, field, ",".join(values))
            changed = True
    if changed:
        with spec.open("w", encoding="utf-8") as stream:
            parser.write(stream)
    return changed


def configure_android_webview_buildozer(root: Path) -> bool:
    """Declare WebView's Java bridge to Buildozer.

    PySide's module scan can copy the native plugin while omitting the JAR
    named by Qt6WebView's dependency XML.  Buildozer's ``android.add_jars``
    is the authoritative input consumed by p4a/Gradle after Qt initializes.
    """
    deploy_spec = root / "pysidedeploy.spec"
    buildozer_spec = root / "buildozer.spec"
    deploy = configparser.ConfigParser(interpolation=None)
    deploy.read(deploy_spec, encoding="utf-8")
    jars_dir = Path(deploy.get("buildozer", "jars_dir", fallback="")).expanduser()
    jar = jars_dir / "Qt6AndroidWebView.jar"
    if not jar.is_file():
        raise RuntimeError(f"Qt WebView Android JAR is unavailable: {jar}")
    config = configparser.ConfigParser(interpolation=None)
    config.read(buildozer_spec, encoding="utf-8")
    if not config.has_section("app"):
        raise RuntimeError("Qt WebView cannot configure a Buildozer spec without [app]")
    jars = [item.strip() for item in config.get("app", "android.add_jars", fallback="").split(",")
            if item.strip()]
    if str(jar) in jars:
        return False
    jars.append(str(jar))
    config.set("app", "android.add_jars", ",".join(jars))
    with buildozer_spec.open("w", encoding="utf-8") as stream:
        config.write(stream)
    return True


def prepare_android_webview_gradle(artifact: Path, *, debug: bool,
                                   run: Callable[[list[str], Path], object]) -> bool:
    """Preload WebView's JNI-registration plugin before Qt starts Python."""
    distribution = next(
        (parent for parent in artifact.parents if parent.parent.name == "dists"), None
    )
    if distribution is None:
        return False
    arch = "arm64-v8a"
    name = "libplugins_webview_qtwebview_android_arm64-v8a.so"
    bundle = distribution / f"_python_bundle__{arch}"
    source = bundle / "_python_bundle/site-packages/PySide6/Qt/plugins/webview" / name
    resources = distribution / "src/main/res/values/libs.xml"
    if not bundle.is_dir() or not resources.is_file():
        return False
    if not source.is_file():
        raise RuntimeError(f"Qt Android WebView plugin is missing from the Python bundle: {source}")
    try:
        tree = ET.parse(resources)
        loader = tree.getroot().find("./array[@name='load_local_libs']")
        if loader is None:
            raise RuntimeError(f"Qt Android deployment has no load_local_libs array: {resources}")
    except ET.ParseError as error:
        raise RuntimeError(f"Cannot parse Qt Android loader metadata: {error}") from error
    entry = f"{arch};{name}"
    main = f"{arch};libmain_arm64-v8a.so"
    entries = [(item.text or "").strip() for item in loader.findall("item")]
    if main not in entries:
        raise RuntimeError(f"Qt Android loader does not declare {main}: {resources}")
    if entries.count(entry) > 1:
        raise RuntimeError(f"Duplicate Qt WebView loader entries in {resources}")
    target = distribution / "libs" / arch / name
    copy_needed = not target.is_file() or target.read_bytes() != source.read_bytes()
    order_needed = entry not in entries or entries.index(entry) > entries.index(main)
    if not copy_needed and not order_needed:
        return False
    target.parent.mkdir(parents=True, exist_ok=True)
    if copy_needed:
        shutil.copy2(source, target)
    if order_needed:
        for item in tuple(loader.findall("item")):
            if (item.text or "").strip() == entry:
                loader.remove(item)
        item = ET.Element("item")
        item.text = entry
        loader.insert(0, item)
        tree.write(resources, encoding="utf-8", xml_declaration=True)
    run([str(distribution / "gradlew"), "assembleDebug" if debug else "bundleRelease"], distribution)
    return True


def verify_android_webview_apk(artifact: Path) -> None:
    """Reject an APK missing WebView's plugin, Java bridge, or loader entry."""
    if artifact.suffix.lower() != ".apk":
        return
    with ZipFile(artifact) as apk:
        entries = list(apk.namelist())
        has_plugin = any("plugins_webview_qtwebview_android" in name.lower() and name.endswith(".so")
                         for name in entries)
        if not has_plugin:
            for private in ("assets/private.tar", "assets/private.mp3"):
                if private not in entries:
                    continue
                with tarfile.open(fileobj=io.BytesIO(apk.read(private)), mode="r:*") as payload:
                    has_plugin = any("plugins_webview_qtwebview_android" in member.name.lower()
                                     and member.name.endswith(".so") for member in payload.getmembers())
                if has_plugin:
                    break
        has_java_bridge = any(
            b"QtAndroidWebViewController" in apk.read(name)
            for name in entries if name.startswith("classes") and name.endswith(".dex")
        )
        # The native plugin being present is insufficient: QtLoader must
        # System.loadLibrary it to invoke JNI_OnLoad and register callbacks.
        loader_entry = b"arm64-v8a;libplugins_webview_qtwebview_android_arm64-v8a.so"
        has_loader_entry = ("resources.arsc" in entries
                            and loader_entry in apk.read("resources.arsc"))
        if has_plugin and has_java_bridge and has_loader_entry:
            return
    raise RuntimeError(
        "Android APK lacks Qt's WebView plugin, Java bridge, or native QtLoader "
        "preload entry. A full Android build is required for WebView JNI callbacks."
    )
