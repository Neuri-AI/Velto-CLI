"""Qt Android smoke-test classifications.

These tests do not require an Android device or external binaries.
"""
from velto_cli.adapters.mobile.qt.android_runtime_diagnostics import (
    classify_android_startup,
)


def codes(log: str) -> set[str]:
    return {f.code for f in classify_android_startup(log)}


def test_missing_library_is_reported():
    log = (
        "E QtLoader: Can't load '/data/app/lib/arm64/lib_arm64-v8a.so'\n"
        "java.lang.UnsatisfiedLinkError: dlopen failed: library "
        '\"lib_arm64-v8a.so\" not found'
    )
    assert "qt-loader-library-missing" in codes(log)


def test_qt_jni_unsatisfied_link_is_not_misclassified_as_missing_library():
    log = (
        "FATAL EXCEPTION: main\n"
        "java.lang.UnsatisfiedLinkError: No implementation found for void "
        "org.qtproject.qt.android.QtActivityDelegateBase.updateUiMotionScale(float)"
    )
    assert codes(log) == {"qt-jni-unresolved", "java-fatal"}


def test_python_import_and_traceback_are_detected():
    log = (
        "Traceback (most recent call last):\n"
        "ModuleNotFoundError: No module named 'velto'"
    )
    assert codes(log) == {"python-import", "python-traceback"}


def test_native_crash_is_detected():
    assert codes("A/libc: Fatal signal 11 (SIGSEGV)") == {"native-signal"}


def test_valid_startup_logs_have_no_findings():
    assert codes(
        "I QtLoader: Qt6Core loaded\nI ActivityTaskManager: Displayed "
        "org.androidappqt.androidappqt"
    ) == set()
