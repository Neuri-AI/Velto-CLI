# Android release source minimization (PySide6)

Velto's ordinary **debug** workflow retains Python source and supports incremental APK
re-signing. Use `velto build --target android --debug` for development.

To test bytecode-only packaging on a device without any release signing key:

```shell
velto build --target android --debug --protect-source
```

The protected debug artifact uses Android's automatic **debug keystore**,
not a release key. The incremental cache keeps an unprotected base APK and
re-signs only the published protected output. Do **not** distribute the debug
key as a production identity. A debug APK is still signed: Android does not
install unsigned APKs.

A **non-debug** PySide6 Android build is fail-closed: Velto refuses to publish
an APK/AAB when it cannot verify a source-minimized Android release.

## Release workflow

Before running `velto build --target android`, configure your own Android
release signing key via the following environment variables:

- `VELTO_ANDROID_KEYSTORE`: path to an existing release keystore.
- `VELTO_ANDROID_KEY_ALIAS`: key alias.
- `VELTO_ANDROID_KEYSTORE_PASSWORD`: keystore password.
- `VELTO_ANDROID_KEY_PASSWORD`: private key password.

Do not store passwords in project files or commit them to Git. Supply them from
a secure CI secret store when building in automation.

The current source-minimization implementation supports **release APKs only**.
It does not convert a debug APK into a release artifact and does not silently
claim to protect an AAB; those paths fail with explicit diagnostics.

For a supported release APK, Velto:

1. Verifies that the APK embeds a CPython interpreter matching the host
   interpreter used to generate bytecode.
2. Compiles application-owned `.py` sources and bundled Velto/PSX/Qyro sources
   into version-matched sourceless `.pyc` modules.
3. Precompiles project PSX templates and retains a minimal `main.py` bootstrap
   for the python-for-android entry point.
4. Rewrites `assets/private.tar`, updates Android's extraction version
   marker, aligns the APK, signs with the configured release key and verifies
   the signature.
5. Reopens the *signed* artifact and rejects any remaining known application
   or framework `.py` files (except the bootstrap).

Failures leave the input artifact unchanged and **do not publish** a release.

## Security scope

This is **source minimization**, not encryption or strong obfuscation.
Python bytecode can be decompiled. Keep API keys, credentials, proprietary
secrets, and signing keys outside the APK. Binary extensions can also be
reverse-engineered. R8/ProGuard does not protect Python bytecode.

Android does not automatically accept host Python bytecode: CPython minor
versions and the magic format must match. If the embedded Python interpreter
cannot be verified, the build fails rather than shipping unusable bytecode.

The separate native-extension distribution and AAB-specific protection flows
remain future work. Do not label an unprotected AAB as a protected release.
