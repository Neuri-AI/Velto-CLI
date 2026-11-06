# Android signing, source protection and build-only exclusions

Velto reads **settings/android.json** from the developer's project, not from
the APK. This file is host-only and is removed from the PySide6/Buildozer
source tree during deployment, then restored.

Example:

```json
{
  "protect_source": true,
  "signing": {
    "keystore": "/absolute/path/to/release.jks",
    "alias": "velto-release",
    "keystore_password": "your-keystore-password",
    "key_password": "your-key-password"
  },
  "exclude": []
}
```

The signing section is used for **Release**, not for Debug. For Debug,
Android's automatic debug signing key is used. Environment variables
`VELTO_ANDROID_KEYSTORE`, `VELTO_ANDROID_KEY_ALIAS`,
`VELTO_ANDROID_KEYSTORE_PASSWORD`, and `VELTO_ANDROID_KEY_PASSWORD` still
work and take precedence over JSON values (useful for CI secrets).

**Important:** the JSON file is not encrypted. Excluding it from the APK
does not protect it on your computer, in Git, or in uploaded source archives.
Do not commit real passwords. Put `settings/android.json` in `.gitignore`
and keep an `android.example.json` with empty placeholders when needed.
Use CI secrets through environment variables for published releases.

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
`settings/android.json` is always excluded. The `exclude` array accepts
additional project-relative paths or globs for future build-only files.
For example `"exclude": ["private/", "settings/*.secrets.json"]`.

Exclusions are enforced before third-party source scanning, while creating
incremental fingerprints, and when verifying APK private archives. Previous
cached APKs containing newly excluded paths are rejected. The build fails if
an excluded file remains in the generated APK, rather than publishing it.

## Source protection and limitations

Release output remains fail-closed. Debug builds default to raw Python unless
enabled in android.json. The protection mode compiles modules to sourceless
`.pyc` and signs the APK; this does not encrypt Python, and bytecode can be
decompiled. The APK must include a compatible CPython interpreter. Existing
AAB protection and native compilation with Nuitka are **not implemented**.
Do not label these artifacts as Nuitka-protected.

A protected APK still requires a minimal Python launcher `main.py`.
Any API keys, signing passwords, and other secrets must stay out of mobile
client packages entirely.
