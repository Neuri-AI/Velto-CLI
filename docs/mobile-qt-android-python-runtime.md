# Qt Android: packaging the Velto Python runtime

The PySide6 Android deploy wrapper uses python-for-android and creates a Buildozer
configuration with Python, Shiboken and PySide6. It does **not** automatically
include the Velto packages declared by an application in `pyproject.toml`.

For projects whose `main.py` imports `velto`, `velto build --target android`
now stages the **installed host Python source trees** of `velto`, `qyro`, and
`psx` into the application's source directory for the duration of PySide6
deployment. The staging is deleted after the build, including after errors.
Host platform native extensions (`.so`, `.pyd`, `.dll`, `.dylib`) are
never copied. The generated APK's `assets/private.tar` (or alternative
private archive name) is checked for the three packages before returning an
artifact.

The Python Cinnamon fallback in `psx.layout.fallback` is included with PSX.
The Android build does not require the native Rust `_sfle_native` extension
for layouts that are supported by that fallback.

## Requirements and limits

* Install compatible **source** versions of `velto`, `qyro-engine`, and
  `velto-core` in the **same environment that runs Velto CLI**. Installed
  PyPI versions may differ from unmerged GitHub branches. The staged code is
  taken from the installed environment; it is not downloaded by the build.
* Keep `velto` as an application dependency in `pyproject.toml`. This
  declaration does not cause pyside6-android-deploy to resolve its dependencies;
  source staging handles the three Velto packages only.
* Other third-party packages, notably native modules such as
  `cryptography`, `Pillow`, and `resvg_py`, still require compatible Android
  packages or p4a recipes when the application uses them. Do **not** copy their
  macOS/Linux wheels into Android. Android support for these dependencies has
  not been certified.
* Qyro on `feature/mobile-runtime` defers importing `cryptography` unless
  encrypted secrets are actually present. Builds relying on encrypted secrets
  must still package an Android-compatible `cryptography`.
* The APK archive check certifies **presence of source files**, not a complete
  successful runtime import or rendering. Perform device smoke testing and
  check Logcat for `ModuleNotFoundError`, `ImportError` and native crashes.

## Test

```sh
poetry run pytest \
  tests/application/test_qt_android_bundle.py \
  tests/application/test_qt_android_sources.py \
  tests/application/test_qt_android_loader.py \
  tests/application/test_qt_android_qpa.py -v
```

After building, confirm that the APK includes the private source tarball and
launch it on an Android device. Inspect Python Traceback messages in Logcat
before declaring Android runtime support complete.
