"""Classify Android Qt startup failures from adb logcat without guessing causes."""
from dataclasses import dataclass


@dataclass(frozen=True)
class AndroidStartupFinding:
    code: str
    description: str


def classify_android_startup(log: str) -> list[AndroidStartupFinding]:
    """Identify actionable startup failures in full, unfiltered logcat output."""
    patterns = (
        (
            "qt-loader-library-missing",
            lambda s: (
                "QtLoader" in s
                and ("UnsatisfiedLinkError" in s or "Can't load" in s)
                and ("lib_arm64-v8a.so" in s or "dlopen failed" in s)
            ),
            "QtLoader cannot load a declared native library. Compare libs.xml "
            "load_local_libs against lib/<abi>/ entries in the APK.",
        ),
        (
            "qt-jni-unresolved",
            lambda s: (
                "UnsatisfiedLinkError" in s
                and "No implementation found" in s
                and "org.qtproject.qt.android" in s
            ),
            "Qt Java called an unavailable JNI method. Verify the Qt Android JARs, "
            "native Qt libraries, platform plugin, and bootstrap are compatible.",
        ),
        (
            "python-import",
            lambda s: "ModuleNotFoundError:" in s or "ImportError:" in s,
            "Python cannot import a dependency inside the Android bundle. "
            "Inspect the traceback and package compatibility.",
        ),
        (
            "python-traceback",
            lambda s: "Traceback (most recent call last)" in s,
            "An uncaught Python exception occurred during application startup.",
        ),
        (
            "native-signal",
            lambda s: "Fatal signal " in s or "SIGSEGV" in s or "SIGABRT" in s,
            "The Android process received a fatal native signal.",
        ),
        (
            "java-fatal",
            lambda s: "FATAL EXCEPTION" in s,
            "An uncaught Android Java exception occurred.",
        ),
    )
    return [
        AndroidStartupFinding(code, message)
        for code, matches, message in patterns
        if matches(log)
    ]
