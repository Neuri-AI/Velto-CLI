# Incremental Qt Android debug builds

Velto CLI automatically reuses a previously verified PySide6 Android APK
for debug builds when only Python source files or approved packaged resources
have changed. The developer does not need to enable or configure the feature.

```shell
velto build --target android --debug
```

The **first successful full build** seeds a persistent cache outside the
project at `~/.velto/mobile/android/qt/incremental/<project-id>/`. Later
debug builds compare SHA-256 hashes of project sources and installed
Velto/Qyro/PSX Python files to the recorded snapshot.

## Fast path

For source-only changes, Velto patches the APK's p4a `private.tar` using
gzip-compressed USTAR with explicit parent-directory entries. It can add,
overwrite and remove files. It also changes p4a's fixed-width
`private_version` string resource in the compiled Android resource table,
so an `adb install -r` update re-extracts new application files on startup.

The APK is zipaligned, re-signed with the Android debug keystore and
signature-verified before it is atomically published at
`build/android/qt/<apk-name>`. The previous signed cache survives failures.

## Full rebuild triggers

The normal PySide6 deploy path runs when no verified cache exists, the cache
is corrupt, the compiled p4a version resource cannot be changed
unambiguously, a native/project configuration changes, or an installed Qt,
native Velto dependency, package version, Python version, signing key, APK
build option or requested Android wheel changes. SVG plugin requirements are
also part of the native configuration fingerprint.

Incremental packaging is **debug APK only**, not release signing or AAB
publishing. Uncertifiable source symlinks do not qualify.

Force a full build without disabling future incremental builds:

```shell
velto build --target android --debug --clean
```

`--clean` bypasses the incremental APK cache; it does not erase global
SDK/NDK or Gradle caches.

## Verification

```shell
poetry run pytest tests/application/test_qt_android_incremental.py \
  tests/application/test_qt_android_final_apk.py \
  tests/application/test_qt_android_build_inputs.py -v
```

After producing a debug APK, use the existing Android smoke tester:

```shell
python scripts/android_smoke_test.py --apk \
  "$PWD/build/android/qt/<apk-name>.apk" \
  --package <your.package.name> --seconds 15
```

The smoke test confirms process liveness and captures exceptions. It does not
certify visual Qt rendering or touchscreen behavior.

Implementation intentionally falls back to the full Android build on an
uncertain resource-table or cache state instead of installing stale Python
code. Both full and incremental builds still need one on-device smoke test
before considering the workflow certified.
