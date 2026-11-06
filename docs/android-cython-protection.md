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
