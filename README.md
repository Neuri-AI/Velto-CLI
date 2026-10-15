<p align="center">
  <img
    src="https://ik.imagekit.io/kummiktgaiq/ppg/Qyro-logo.svg?updatedAt=1755215983279"
    alt="Qyro Logo"
    width="50%"
  >
</p>

> [!WARNING]
> **Qyro CLI is currently in alpha.** Commands, flags, templates, packaging
> behavior, and configuration formats may change between releases.
>
> Supported workflows currently focus on desktop applications. Mobile
> packaging is experimental and is not part of the supported production
> workflow.

# Qyro CLI

> **The official developer CLI and project orchestrator for the
> [Qyro](https://github.com/Neuri-AI/qyro) desktop and mobile application
> ecosystem.**

[![Python](https://img.shields.io/badge/Python-3.10%20%7C%203.11%20%7C%203.12%20%7C%203.13%20%7C%203.14-blue.svg)](https://python.org)
[![Qyro Platforms Tests](https://github.com/Neuri-AI/qyro-cli/actions/workflows/matrix.yml/badge.svg)](https://github.com/Neuri-AI/qyro-cli/actions/workflows/matrix.yml)
![GitHub Release](https://img.shields.io/github/v/release/Neuri-AI/qyro-cli?include_prereleases&display_name=release&color=stable)
![GitHub Issues](https://img.shields.io/github/issues/Neuri-AI/qyro-cli)
![GitHub Issues Closed](https://img.shields.io/github/issues-closed/Neuri-AI/qyro-cli?color=green)
![GitHub forks](https://img.shields.io/github/forks/Neuri-AI/qyro-cli)
![GitHub stars](https://img.shields.io/github/stars/Neuri-AI/qyro-cli)
[![License](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
[![Sponsor](https://img.shields.io/badge/Sponsor-Buy%20Me%20a%20Coffee-FFDD00?logo=buymeacoffee&logoColor=000000)](https://buymeacoffee.com/neuri)

> [!WARNING]
> **Mobile support is experimental.** Buildozer packaging, the `[mobile]`
> extra, Android/iOS targets, and Kivy mobile workflows are incomplete and may
> change without notice.
>
> The currently supported Qyro CLI workflow is desktop-only on Windows, macOS,
> and Linux.

---

## Features

- Unified Multi-Framework Support:** Scaffold projects for **PySide6**,
  **PyQt6**, **PyQt5**, **PySide2**, **Kivy**, or **Tkinter**.
- Smart Template Resolution:** Uses template providers with fallback
  support for robust initialization workflows.
- Packaging & Freezing Ready:** Native desktop freezing with PyInstaller.
- Protected Resource Encryption:** Optional encrypted packaging for `settings/` and `resources/` with per-build runtime secrets compiled via Cython.
- Distribution Bundling:** Platform-aware bundling for DMG, NSIS, and
  Linux package formats.
- Code Signing & Notarization:** Windows Authenticode and macOS signing
  with optional notarization and stapling.
- Release Preflight Checks:** Validate dependencies and `release.json`
  paths and options before packaging.
- Artifact Cleanup:** Clean build outputs and optional release outputs with
  one command.

---

## Compatibility

The table below reports validation results for the complete desktop workflow:

```text
init → start → build → bundle
```

> Table last validated: **2026-10-02**  
> CI run: [#37052349565](https://github.com/Neuri-AI/qyro-cli/actions/runs/37052349565)

| Framework | Platform | Python 3.10 | Python 3.11 | Python 3.12 | Python 3.13 | Python 3.14 | Notes |
| --------- | -------- | ----------- | ----------- | ----------- | ----------- | ----------- | ----- |
| PySide6 | Windows | ✅ | ✅ | ✅ | ✅ | ✅ | Full pass |
| PySide6 | macOS | ✅ | ✅ | ✅ | ✅ | ✅ | Full pass |
| PySide6 | Linux | ✅ | ✅ | ✅ | ✅ | ✅ | Full pass |
| PySide2 | Linux | — | — | — | — | — | No CI coverage |
| PySide2 | Windows | — | — | — | — | — | No CI coverage |
| PySide2 | macOS | — | — | — | — | — | No CI coverage |
| PyQt6 | Windows | ✅ | ✅ | ✅ | ✅ | ✅ | Full pass |
| PyQt6 | macOS | ✅ | ✅ | ✅ | ✅ | ✅ | Full pass |
| PyQt6 | Linux | ✅ | ✅ | ✅ | ✅ | ✅ | Full pass |
| PyQt5 | Windows | ✅ | ✅ | ✅ | ✅ | ✅ | Full pass |
| PyQt5 | macOS | ✅ | ✅ | ✅ | ✅ | ✅ | Full pass |
| PyQt5 | Linux | ✅ | ✅ | ✅ | ✅ | ✅ | Full pass |
| Kivy | Windows | ✅ | ✅ | ✅ | ✅ | ❌ | Python 3.14 failed at `install` |
| Kivy | macOS | ✅ | ✅ | ✅ | ✅ | ✅ | Full pass |
| Kivy | Linux | ✅ | ✅ | ✅ | ✅ | ✅ | Full pass |
| Tkinter | Windows | ✅ | ✅ | ✅ | ✅ | ✅ | Full pass |
| Tkinter | macOS | ✅ | ✅ | ✅ | ✅ | ✅ | Full pass |
| Tkinter | Linux | ✅ | ✅ | ✅ | ✅ | ✅ | Full pass |

Legend:

- ✅ `FULL PASS`: all four workflow stages completed.
- ❌ `FAIL`: validation was attempted and failed.
- — `NOT TESTED`: no validation result is available.

Known failure:

- **Kivy · Python 3.14 · Windows:** failed during `install`.

Compatibility results are specific to the operating system, Python version,
framework, dependency state, and CI environment used during testing.


---

## Installation

### Supported desktop installation

```bash
# Using pip
pip install "qyro-cli[desktop]"

# Using Poetry
poetry add qyro-cli -E desktop
```

The core package can also be installed without the desktop extra:

```bash
# Using pip
pip install qyro-cli

# Using Poetry
poetry add qyro-cli
```

> [!IMPORTANT]
> Resource protection encryption requires the desktop extra because it uses
> Cython during build to compile the runtime secret module.

### Experimental mobile installation

> [!WARNING]
> Mobile packaging is experimental and is not part of the supported production
> workflow. Install this extra only if you are testing the Buildozer
> integration.

```bash
# Using pip
pip install "qyro-cli[mobile]"

# Using Poetry
poetry add qyro-cli -E mobile
```

The `[all]` extra includes experimental mobile dependencies and is not
recommended for production use:

```bash
# Using pip
pip install "qyro-cli[all]"

# Using Poetry
poetry add qyro-cli -E all
```

---

## Qyro Settings Builder

The **Qyro Settings Builder** provides a visual interface for preparing
application, platform-specific, and release configuration files used by Qyro
projects.

It is the recommended starting point for creating the non-secret JSON files.

It helps simplify:

- Application settings.
- Platform-specific settings.
- Release and packaging options.
- Distribution metadata.

Open the builder:

[Open Qyro Settings Builder](https://qyro-settings-builder.up.railway.app/)

Generated files can be placed in the project's `settings/` directory and
reviewed before running Qyro CLI commands.

> [!NOTE]
> The Settings Builder is an auxiliary tool. Always review generated files
> before building or releasing an application. Create `secrets.json` and
> `sign.json` locally and never enter real credentials into the web interface.

See the [complete configuration reference](docs/configuration.md) for every
field, default, precedence rule, platform option, `release.json`, `sign.json`,
and `secrets.json`.

---

## 💻 Quick Start

Qyro CLI provides the following desktop workflow:

1. Initialize a project from a framework template.
2. Run the application from source.
3. Freeze the application into executable artifacts.
4. Bundle the artifacts for distribution.
5. Optionally sign and notarize the release.

### 1) Initialize a project

```bash
qyro init --name my-app
```

You can preselect a binding and template version:

```bash
qyro init \
  --name my-app \
  --binding PySide6 \
  --template-version 1.0.x
```

For non-interactive initialization:

```bash
qyro init \
  --name my-app \
  --target-platform desktop \
  --binding PySide6 \
  --app-name "My App" \
  --version 1.0.0 \
  --author "Your Name" \
  --addon pydux \
  --addon requests \
  --yes
```

Supported initialization options:

- `--target-platform`: `desktop`, `x86_64`, `apple-silicon`, `iphone`, or
  `android`.
- `--app-name`: Application display name.
- `--version`: Semantic application version.
- `--author`: Author name.
- `--addon`: Optional dependency add-on. Repeatable. Supported values:
  `hotrl`, `pydux`, `sentry-sdk`, and `requests`.
- `--bundle-id`: Bundle identifier for iPhone or Apple Silicon templates.
- `-y, --yes`: Skip the confirmation prompt.

Notes:

- `desktop` maps to the desktop template target.
- If these flags are omitted, `qyro init` uses the interactive wizard.
- Mobile targets are experimental and are not part of the supported desktop
  workflow.

Supported bindings:

- `PySide6`
- `PyQt6`
- `PyQt5`
- `PySide2`
- `Kivy`
- `Tkinter`

> [!NOTE]
> Kivy desktop support is reflected in the compatibility matrix. Kivy mobile
> builds are not currently supported.

### 2) Run from source

Run this command from the generated project directory:

```bash
cd my-app
qyro start
```

`qyro start` currently runs the application from source without release flag
variants.

### 3) Freeze executable artifacts

Run these commands from a Qyro project directory:

```bash
# Default desktop target with the release profile
qyro build

# Single executable
qyro build --onefile

# Equivalent mode syntax
qyro build --mode onefile

# Platform target override
qyro build --target mac
qyro build --target windows
qyro build --target linux

# Additional controls
qyro build --debug --console --uac --clean --interactive
```

### Protected resource encryption

Qyro can protect project resources during `qyro build` by encrypting
`settings/` and `resources/` into a single protected package.

Enable in `settings/release.json`:

```json
{
  "resource_protection": {
    "enabled": true,
    "settings_dir": "settings",
    "resources_dir": "resources",
    "bundle_path": ".qyro/resources.pak"
  }
}
```

When enabled, Qyro:

- Generates a random secret for each build.
- Compiles a native `runtime` module with Cython (no plaintext `.py` secret in output).
- Wraps the resource key with that runtime secret.
- Embeds the protected package and compiled runtime module into the frozen app.

`resources.pak` keeps its current internal mechanism for `settings/`
and `resources/`.

`settings/secrets.json` is handled separately with authenticated cryptography:

- AES-256-GCM encryption.
- HKDF-SHA256 key derivation from `RUNTIME_SECRET_HEX` and a random salt.
- Random 12-byte nonce per encryption.
- Versioned binary payload format (`QYRSEC` header) for future compatibility.

Secrets protection is part of the frozen desktop build even when full
`settings/` and `resources/` protection is disabled. The plaintext source file
is excluded; the encrypted payload is packaged for Qyro Engine to decrypt and
merge at runtime.

Output artifacts inside the app bundle/freeze layout include:

- `.qyro/resources.pak`
- `.qyro/runtime*.so` on macOS/Linux or `.qyro/runtime*.pyd` on Windows

Notes:

- This is anti-tampering/anti-casual-extraction protection, not absolute
  cryptographic secrecy against determined reverse engineering.
- Encryption of `secrets.json` protects embedded secrets at rest, but it does
  not provide absolute protection against an attacker who can execute, debug,
  or reverse engineer the application.
- Cython compilation requires a working native toolchain on the build machine
  (for example, Xcode CLT on macOS or Build Tools on Windows).

> [!NOTE]
> Available `--target` values are desktop platforms only: `mac`, `windows`,
> and `linux`.

### 4) Bundle for distribution

Run these commands from a Qyro project directory:

```bash
# Select the default format for the host operating system
qyro bundle

# Explicit platform and format
qyro bundle --platform mac --format dmg
qyro bundle --platform windows --format nsis
qyro bundle --platform linux --format tar.gz
qyro bundle --platform linux --format deb
qyro bundle --platform linux --format rpm
qyro bundle --platform linux --format arch

# Include an additional ZIP and use a custom output directory
qyro bundle --zip --release-dir release
```

### Validate before packaging

```bash
qyro bundle --check
```

This validates dependencies and `release` settings, such as DMG backgrounds
and extra files, without generating artifacts.

---

## Windows NSIS customization

When packaging with:

```bash
qyro bundle --platform windows --format nsis
```

you can customize installer behavior using `bundle.nsis` in
`settings/release.json`.

Supported options:

- `bundle.nsis.icons.install`: Installer icon path (`.ico`).
- `bundle.nsis.icons.uninstall`: Uninstaller icon path (`.ico`).
- `bundle.nsis.welcome_bitmap`: Welcome/finish bitmap path.
- `bundle.nsis.install_location`: `programfiles64`, `programfiles32`, or
  `appdata`.
- `bundle.nsis.execution_level`: `highest`, `admin`, or `user`.

Defaults:

- `install_location`: `programfiles64`
- `execution_level`: `highest`
- `icons.install`: `resources/base/install.ico` if the file exists.
- `icons.uninstall`: `resources/base/uninstall.ico` if the file exists.
- `welcome_bitmap`: not set by default.

Example:

```json
{
  "bundle": {
    "nsis": {
      "icons": {
        "install": "resources/base/install.ico",
        "uninstall": "resources/base/uninstall.ico"
      },
      "welcome_bitmap": "resources/base/welcome.bmp",
      "install_location": "programfiles64",
      "execution_level": "highest"
    }
  }
}
```

Validate NSIS options before generating the installer:

```bash
qyro bundle --check --platform windows --format nsis
```

---

## Clean outputs

Run these commands from a Qyro project directory:

```bash
# Clean the freeze directory
qyro clean

# Also clean the release directory
qyro clean --release
```

---

## Sign compiled artifacts

Run these commands from a Qyro project directory:

```bash
# Preflight validation only
qyro sign --check --platform windows
qyro sign --check --platform mac

# Sign frozen binaries or application bundles
qyro sign --platform windows
qyro sign --platform mac

# macOS notarization flow
qyro sign \
  --platform mac \
  --notarize \
  --staple \
  --keychain-profile "QYRO-NOTARY"

# Skip Gatekeeper assessment if required
qyro sign \
  --platform mac \
  --notarize \
  --staple \
  --no-assess
```

---

## Signing guide

Use the local-only `settings/sign.json` file to configure signing and
notarization. Qyro loads it only for `qyro sign`, after the release profile,
and excludes it from frozen applications and protected resource packages.
Keep release packaging options in `settings/release.json`.

### Windows signing

Requirements:

- Windows host with `signtool` available in `PATH`.
- Code-signing certificate file, such as `.pfx`.
- Certificate and password configured in `settings/sign.json`.

Example `settings/sign.json`:

```json
{
  "sign": {
    "windows": {
      "certificate": "src/sign/windows/certificate.pfx",
      "password": "<certificate-password>",
      "timestamp_server": "https://timestamp.digicert.com",
      "description": "MyApp",
      "url": "https://example.com"
    }
  }
}
```

Do not commit `settings/sign.json`, certificates, passwords, API keys, or
private keys. Inject the file from your CI secret store when automating a
release.

Recommended flow:

```bash
# 1. Build artifacts
qyro build --target windows

# 2. Validate signing prerequisites
qyro sign --check --platform windows

# 3. Sign supported binaries in build/
qyro sign --platform windows
```

By default, Qyro signs supported binary types in the freeze output, such as
`.exe`, `.dll`, `.msi`, and `.cab`.

### macOS signing and notarization

Requirements:

- macOS host with Xcode Command Line Tools:
  `codesign`, `xcrun`, `notarytool`, `stapler`, and `spctl`.
- Apple Developer membership.
- Valid `Developer ID Application` certificate in Keychain.
- Entitlements file for Python runtime behavior, recommended for GUI
  applications.

Example `settings/sign.json`:

```json
{
  "sign": {
    "mac": {
      "identity": "Developer ID Application: Your Name (TEAMID)",
      "entitlements": "src/sign/mac/entitlements.plist",
      "notary": {
        "enabled": true,
        "staple": true,
        "assess_gatekeeper": true,
        "keychain_profile": "QYRO-NOTARY"
      }
    }
  }
}
```

Do not commit Apple credentials, API keys, passwords, private keys, or
`settings/sign.json` to the repository. For build architecture, use
`mac_target_architecture` in `settings/mac.json`; signing credentials remain in
`settings/sign.json`.

Recommended flow:

```bash
# 1. Build the macOS application bundle
qyro build --target mac

# 2. Validate signing and notarization prerequisites
qyro sign --check --platform mac

# 3. Sign the application
qyro sign --platform mac

# 4. Sign, notarize, and staple
qyro sign \
  --platform mac \
  --notarize \
  --staple \
  --keychain-profile "QYRO-NOTARY"
```

Supported notarization authentication options:

- `keychain_profile`
- `key_path` + `key_id` + optional `issuer`
- `apple_id` + `team_id` + `app_password`

`qyro sign` reads `sign.mac.identity`, entitlements, and notarization
credentials from `settings/sign.json`. The build command does not activate
that profile.

---

## Secret management

Qyro supports a local-only secrets file for API keys, service tokens, license
data, and other protected values that the application needs at runtime:

- `settings/secrets.json`.

Qyro loads base and profile settings first, then applies `secrets.json` as the
highest-precedence override.

During a frozen desktop build, Qyro encrypts `settings/secrets.json` with
AES-256-GCM and embeds it inside
`resources.pak` as an internal payload (for example,
`.qyro/secrets.enc` inside the package ZIP).
No standalone `.qyro/secrets.json` file is emitted in the distributed app.
The Engine decrypts the payload in memory and exposes the values through
`app_settings`, so application code uses the same settings API in source and
frozen modes.

Example `settings/secrets.json`:

```json
{
  "api_url": "https://api.example.com",
  "api_key": "<application-api-key>",
  "service_token": "<protected-token>",
  "license_key": "<local-license>"
}
```

Do not put code-signing or notarization credentials in this file. Qyro encrypts
and packages its complete object in the client build, because the Engine must
be able to recover those values at runtime. Put all signing-only values in
`settings/sign.json`, which is never bundled. The build rejects `sign` and the
legacy flat signing keys when they appear in `secrets.json`.

Repository safety:

- Never commit `settings/secrets.json`.
- Never commit `settings/sign.json`.
- Add both paths to `.gitignore` and verify `git status` before committing.

---

## Alpha status

The following areas are still under active development:

- Some optional CLI flags may change or require further validation.
- Mobile targets and Buildozer integration are experimental.
- Code signing and notarization require platform-specific tools and
  credentials.
- Compatibility depends on the framework, operating system, Python version,
  dependency versions, and test environment.

The compatibility matrix distinguishes successful, failed, and not-yet-tested
combinations. It does not guarantee compatibility with every dependency
revision or operating-system update.

---

## CLI Commands Reference

Commands marked as requiring a project must be run from a generated Qyro
project directory.

| Command | Project required | Flags / Args | Description |
| :--- | :---: | :--- | :--- |
| `qyro init` | No | `-n, --name`, `-b, --binding`, `--template-version` | Initialize a new project. |
| `qyro start` | Yes | None | Run the application from source. |
| `qyro build` | Yes | `-m, --mode`, `--onefile`, `--debug`, `--console`, `--uac`, `-p, --profile`, `-c, --clean`, `-i, --interactive`, `--target`, `--init-spec` | Freeze/build artifacts. |
| `qyro bundle` | Yes | `--release-dir`, `--no-resources`, `--zip`, `--platform`, `--format`, `--check` | Create distributable packages or run preflight checks. |
| `qyro sign` | Yes | `--platform`, `--check`, `--notarize`, `--staple`, `--no-assess`, `--keychain-profile` | Sign compiled artifacts and optionally notarize/staple macOS apps. |
| `qyro clean` | Yes | `--release` | Remove generated build artifacts. |
| `qyro version` | No | None | Show version information. |

---

## Bundle configuration

Projects can configure bundle behavior in:

- `settings/release.json` — current layout.
- `settings/sign.json` — local signing and notarization configuration.

For a field-by-field explanation of bundle, signing, protection, platform, and
secret options, read the [complete configuration reference](docs/configuration.md).

Example:

```json
{
  "release": true,
  "environment": "development",
  "bundle": {
    "dmg": {
      "window": {
        "x": 200,
        "y": 120
      },
      "window_size": {
        "width": 660,
        "height": 420
      },
      "icon_size": 120,
      "app_position": {
        "x": 180,
        "y": 180
      },
      "applications_position": {
        "x": 480,
        "y": 180
      },
      "background": "assets/dmg-background.jpg",
      "extra_files": [
        "README.md",
        {
          "source": "docs/RELEASE_NOTES.md",
          "destination": "docs/RELEASE_NOTES.md"
        }
      ]
    }
  }
}
```

### `bundle.extra_files`

Supported forms:

- String path: copied to the bundle root.
- Object with `source` and `destination`: copied to a relative destination
  inside the bundle.

Validation rules:

- `source` must exist.
- `destination` must be relative.
- Absolute destinations are not allowed.
- `destination` must not escape the output directory.

### `bundle.dmg`

`bundle.dmg.extra_files` copies files into the DMG and therefore requires
`create-dmg`. Each entry uses the same string or `{ "source", "destination" }`
shape as `bundle.extra_files`.

When DMG customization is configured, `create-dmg` is required.

Without custom DMG options, bundling can fall back to native `hdiutil` on macOS.
`hdiutil` does not support `bundle.dmg.extra_files`, so Qyro rejects that
configuration rather than generating a DMG without the requested files.

---

## Packaging dependencies

| Format | Requirement |
| :--- | :--- |
| DMG with customization | `create-dmg` |
| DMG without customization | `hdiutil` on macOS |
| NSIS | `makensis` |
| DEB, RPM, or Arch | `fpm` |

Install `create-dmg` on macOS with one of:

```bash
brew install create-dmg
```

or:

```bash
npm install -g create-dmg
```

---

## Supported framework ecosystem

`qyro-cli` generates applications that integrate with Qyro adapters:

| Binding | Adapter | Best For |
| :--- | :--- | :--- |
| **PySide6** | `PySide6Adapter` | Modern Qt 6 desktop applications. |
| **PyQt6** | `PyQt6Adapter` | Feature-complete Qt 6 desktop applications. |
| **PyQt5** | `PyQt5Adapter` | Legacy Qt 5 desktop applications. |
| **PySide2** | `PySide2Adapter` | Qt 5 environments; not included in the current matrix. |
| **Kivy** | `KivyAdapter` | Cross-platform desktop interfaces; mobile support is experimental. |
| **Tkinter** | `TkinterAdapter` | Desktop utilities built on the Python standard library. |

> [!WARNING]
> All adapters are currently supported on desktop only. Mobile deployment is
> experimental and is not part of the supported production workflow.

---

## Built-in add-ons

Projects can be configured with modular add-ons:

- **`hotrl` — Hot Reloading:** Iterative development with live code reload.
- **`pydux` — Predictable State:** Redux-inspired state container patterns.
- **`sentry-sdk` — Telemetry:** Exception and crash reporting integration.
- **`requests` — HTTP Client:** Optional HTTP client dependency.

---

## Notes for developers

- `qyro bundle --check` is the fastest way to validate release readiness in CI.
- `qyro clean --release` is useful before reproducible release builds.
- If a bundle step fails, inspect the exact command output and step-specific
  logs.
- Compatibility results should be refreshed when supported Python versions,
  dependency constraints, or packaging logic change.

---

## 🤝 Contributing

Contributions to `qyro-cli` and the Qyro ecosystem are welcome.

1. Fork the repository on GitHub.
2. Create a feature branch:

   ```bash
   git checkout -b feature/amazing-feature
   ```

3. Run the test suite:

   ```bash
   poetry run pytest
   ```

4. Commit your changes:

   ```bash
   git commit -m "feat: add amazing feature"
   ```

5. Push your branch:

   ```bash
   git push origin feature/amazing-feature
   ```

6. Open a pull request.

---

## 📄 License

MIT. See [LICENSE](LICENSE).

---

## 👥 Organization and maintainers

- **Organization:** [Neuri](https://github.com/Neuri-AI)
- **Lead Maintainer:** Luis Alfredo De Los Reyes
  ([luisalfredoreyes98@gmail.com](mailto:luisalfredoreyes98@gmail.com))
- **Ecosystem:** [Qyro](https://github.com/Neuri-AI/qyro) •
  [Qyro CLI](https://github.com/Neuri-AI/qyro-cli) •
  [Boilerplates](https://github.com/Neuri-AI)
