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

## Published Android artifacts

`velto build --target android` publishes finished artifacts to a stable
directory under the application project:

- Qt/PySide6: `build/android/qt/<artifact>.apk` or `.aab`
- Kivy: `build/android/kivy/<artifact>.apk` or `.aab`

These are copies of the verified builder outputs. Buildozer and Gradle keep
their original internal artifacts for incremental compilation and debugging.
The CLI reports the published path, not the internal distribution path.
Repeated builds atomically replace the published file with the same name.

## Qt application settings and resources

The Qt/Buildozer generated source filter can omit required non-Python
resources, notably `settings/base.json`. On a Velto Qt Android build, the
CLI now discovers ordinary project assets under `settings/`, `resources/`,
`css/`, `images/`, `assets/`, `fonts/`, `components/`, and `views/`.
It inserts missing supported resources into the generated Gradle project's
private application tar, **then rebuilds the APK/AAB with Gradle**. This
preserves proper Android packaging/signing; it does not modify an already
signed APK.

Files retain their original application-relative paths (for example,
`settings/base.json`, `resources/base/icons/app.svg`,
`css/styles.css`). Signing files, dotfiles and secret/credential filenames
are excluded by default; unrecognized file types are not copied blindly.

The CLI verifies that discovered runtime assets are in the *final APK/AAB*.
This fixes source availability; native or third-party runtime dependencies
remain a separate Android compatibility concern.

Test:
`poetry run pytest tests/application/test_qt_android_assets.py -v`

## Android application launcher icon

The build automatically discovers the developer's PNG launcher icons. Selection
is deterministic and independent of whether the build host runs macOS or Linux:

1. `resources/linux/icons/512.png` (or the closest supported PNG in that folder)
2. `resources/mac/icons/512.png` (fallback when the Linux PNG is absent)
3. `resources/base/icons/64.png` (fallback for projects with only base icons)

Within each folder, search preference is `512.png`, `1024.png`,
`256.png`, `128.png`, `64.png`, `32.png`. Other desktop-only
`.ico` and `.icns` files are not used as Android launch icons.

**Qt:** apply the selected PNG to Gradle's `src/main/res/mipmap/icon.png`
and rebuild through Gradle before publishing the signed artifact. **Kivy:**
temporarily set `icon.filename` in `buildozer.spec` during the build,
restoring the developer's original file afterwards.

The CLI reports both the icon filename and its project folder:
`Android launcher icon: resources/linux/icons/512.png (folder: resources/linux/icons)`.
If no suitable PNG exists, it warns and retains the toolchain default.

Tests: `poetry run pytest tests/application/test_android_project_icons.py -v`.
