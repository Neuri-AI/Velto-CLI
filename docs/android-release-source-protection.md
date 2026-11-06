# Android signing, source protection and build-only exclusions

Velto reads **settings/android.json** from the developer's project, not from
the APK. This file is host-only and is removed from the PySide6/Buildozer
source tree during deployment, then restored.

Keep protection and exclusions in settings/android.json:

```json
{
  "protect_source": true,
  "exclude": []
}
```

Keep signing keys for all platforms in the existing **settings/sign.json**:

```json
{
  "windows": { "certificate": "src/sign/windows/certificate.pfx" },
  "mac": { "identity": "Developer ID Application: Your Name (TEAMID)" },
  "android": {
    "keystore": "src/sign/android/release.jks",
    "alias": "release",
    "keystore_password": "<local-secret>",
    "key_password": "<local-secret>"
  }
}
```

The signing section is used for **Release**, not for Debug. Debug uses Android's
automatic debug signing key. The existing \`VELTO_ANDROID_KEYSTORE\`,
\`VELTO_ANDROID_KEY_ALIAS\`, \`VELTO_ANDROID_KEYSTORE_PASSWORD\`, and
\`VELTO_ANDROID_KEY_PASSWORD\` environment variables remain supported and
override sign.json values for CI. Relative keystore paths are resolved against
the project root.

**Important:** sign.json is not encrypted. Keep real passwords and signing keys
out of Git. The Android build excludes both settings/sign.json and
settings/android.json from the APK, isolates them while PySide scans sources,
then restores them locally. A preexisting APK containing either file is
rejected rather than distributed.

## Commands

```shell
velto build --target android --debug          # fast debug, protection follows android.json
velto build --target android --debug --clean  # force full rebuild, no incremental APK reuse
velto build --target android                  # release: source-minimized + release signing
```

`--clean` is the existing long-form flag, not a positional `clean` argument.

For compatibility, `--protect-source` remains available as an explicit
override. A Debug build protects source when `protect_source` in android.json
is `true`. Release builds always require source protection.

## Exclusion policy

The central policy is implemented in
`velto_cli/adapters/mobile/qt/android_build_config.py`.
`settings/android.json` and `settings/sign.json` are always excluded. Keystore files (`.jks`,
`.keystore`, `.p12`, `.pfx`) and the configured signing keystore are also
excluded, even when the keystore has a custom extension. The `exclude` array accepts
additional project-relative paths or globs for future build-only files.
For example `"exclude": ["private/", "settings/*.secrets.json"]`.

Exclusions are enforced in a disposable filtered project copy before third-party
source scanning, while creating incremental fingerprints, and when verifying
APK private archives. Original developer files are never moved or renamed.
Previous cached APKs containing newly excluded paths are rejected. The build
fails if an excluded file remains in the generated APK, rather than publishing it.

## Source protection and limitations

Release output remains fail-closed. Debug builds default to raw Python unless
enabled in android.json. The protection mode compiles modules to sourceless
`.pyc` and signs the APK; this does not encrypt Python, and bytecode can be
decompiled. Velto inspects the CPython version embedded in the APK and compiles
bytecode using the matching native `hostpython3` from the Android build when
available. For incremental builds, a matching host Python installation can
be selected automatically or provided using `VELTO_ANDROID_BYTECODE_PYTHON`
(e.g. a Python 3.14 executable when the APK embeds Python 3.14).
When the temporary p4a hostpython is already gone and Conda is available,
Velto can provision Python 3.14 into the persistent
`~/.velto/mobile/android/qt/bytecode-python/3.14` build cache. This does not
modify the active Conda environment, project files, or source-exclusion rules.
The initial installation requires access to configured Conda package channels.
If automatic provisioning fails, Velto reports the Conda error rather than
writing incompatible bytecode.
The APK must include a compatible CPython interpreter. Existing
AAB protection and native compilation with Nuitka are **not implemented**.
Do not label these artifacts as Nuitka-protected.

A protected APK still requires a minimal Python launcher `main.py`.
Any API keys, signing passwords, and other secrets must stay out of mobile
client packages entirely.
