# Android: Cython native source protection (PySide6)

Cython is the only protected Android backend. It does not fall back to .pyc.
Unprotected builds remain possible when protect_source is false in Debug.

## settings/android.json

```json
{"protect_source":true,"protection_backend":"cython","obfuscate":true,"exclude":["settings/android.json","settings/sign.json","settings/secrets.json"]}
```

## Pipeline

1. Read and validate configuration, Android exclusions, and Release signing.
2. Copy the project into a filtered temporary staging directory, leaving original files untouched.
3. Stage Velto, PSX and Qyro Python packages in the temporary project.
4. Use pyside6-android-deploy and python-for-android to produce the base Android APK and libpython.
5. Inspect embedded target libpython version, and locate matching p4a headers plus Android NDK clang.
6. Transform app PSX markup on the build host and prepare its Python source.
7. Run Cython >=3 to generate C for each protected application/framework module.
8. Cross-compile C into Android ARM64 ELF .so extensions linked against the APK's libpython.
9. When obfuscate=true, omit Cython docstrings and strip unneeded ELF symbols using llvm-strip.
10. Replace the Python sources inside private.tar with the ARM64 extensions. Preserve only minimal Android main.py bootstrap that imports compiled main.so.
11. Update p4a private_version, align APK, sign it and verify signatures, absence of sources, native ELF architecture, and excluded paths.
12. Publish the verified APK in the original project's build/android/qt/ directory.

## CLI

```bash
velto build --target android --debug
velto build --target android --debug --clean
velto build --target android
```

## Restrictions

- Cython support currently targets PySide6 Android ARM64 APK, not AAB, iOS, or Kivy.
- Protected builds bypass the old .pyc incremental cache rather than silently falling back.
- Buildozer may require Cython<3; Velto uses Cython 3 from an isolated build-only virtual environment when needed.
- A matching Android Python development header tree, libpython.so, and NDK cross compiler must be available. Missing tools and failed native compilation stop the build.
- Selective obfuscation here means removal of docstrings and unneeded native symbols. It is not cryptographic protection.
- The Android device smoke test and PSX UI parity certification are still pending; static ELF and APK verification alone cannot certify execution.

## Troubleshooting: missing target CPython headers

Cython requires the **Android-target** `Python.h` and `pyconfig.h`, not
headers from macOS/Conda. The p4a Python3Recipe normally places them in
`android-build/android-root/include/python3.14/` inside its Python recipe
build tree. Qt may reuse a pre-built distribution without retaining those
intermediate files. Velto checks both the disposable stage and global p4a
build roots. An existing APK alone cannot regenerate target-specific
`pyconfig.h` safely.

If Qt/p4a removed the recipe build files, rebuild the p4a Python recipe
with retained build artifacts or point
`VELTO_ANDROID_P4A_BUILD_ROOT` at an existing preserved p4a build root.
Alternatively, `VELTO_ANDROID_PYTHON_INCLUDE` may point directly at the
matching Android target include directory (containing both headers).
Do **not** point it at the host's Python or Conda include directory.
Velto refuses to produce an APK with incorrect ABI headers and never falls
back to .pyc.

## Automatic header recovery from reused p4a distributions

Velto does **not** pass `--force-build` through Buildozer's
`APP_P4A_EXTRA_ARGS`: on the Qt bootstrap, doing so forces the *APK*
phase to rebuild without PySide6's mandatory `--qt-libs` and
`--load-local-libs`, and APK packaging fails.

Instead, Velto lets PySide6 produce the base APK normally. If the APK's
target Python headers are absent and no matching ABI-keyed cache exists,
Velto then calls a **separate p4a create --force-build** using the
existing Qt distribution's `dist_info.json` and generated local recipes,
only to regenerate the native recipe build headers. Cython can then
cross-compile .so extensions and the APK can be signed. This recovery
is costly on the first run and is still pending device certification.

When matching headers are available, Velto copies them into
`~/.velto/mobile/android/qt/target-python-headers/<sha256-of-libpython>/`
for later reuse. Sources are copied rather than moved. If recovery
fails, it stops; no macOS headers or .pyc fallback are accepted.

## Qt distribution artifact validation

Some PySide6/p4a builds place `libpython3.14.so` exclusively inside the
signed APK's `lib/arm64-v8a/` directory, rather than retaining it under
`dists/<app>/libs/arm64-v8a/`. Header recovery validates the real
`build/outputs/apk/**/*.apk` ZIP library entries and the Python private
archive when distribution metadata is absent; it does not insist on
intermediate library copies being present.

## Recovering Qt p4a dist from the completed APK

PySide6 sometimes removes `dist_info.json` and intermediate copies of
native libraries inside the p4a distribution. The initial recovery logic
incorrectly required these files. Velto now takes the APK it has **already
built and checked** as the source of truth: it checks for Android ARM64
`libpython*.so`, a Qt6 native library, and p4a's private application archive
in the APK ZIP. The matching p4a stage distribution is then selected by the
project directory name or, only when unambiguous, the unique distribution.
The recovery command remains a separate `p4a create`, never an APK build
with a globally injected `--force-build` option.
