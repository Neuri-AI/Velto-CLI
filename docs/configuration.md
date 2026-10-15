# Qyro CLI configuration reference

This reference explains every configuration file used by the desktop workflow: `settings/base.json`, the platform profiles, `build.json`, `settings/release.json`, `settings/sign.json`, and `settings/secrets.json`.

> [!TIP]
> The recommended starting point is [Qyro Settings Builder](https://qyro-settings-builder.up.railway.app/). Export the non-secret files into `settings/`, then review them before building. Create `secrets.json` and `sign.json` locally; do not submit passwords, tokens, certificates, or private keys to a web form.

## Files and consumers

```text
my-app/
├── build.json
└── settings/
    ├── base.json
    ├── windows.json
    ├── linux.json
    ├── mac.json
    ├── release.json
    ├── sign.json
    └── secrets.json
```

| File | Used by | Purpose |
| --- | --- | --- |
| `settings/base.json` | CLI and Qyro Engine | Application identity, entry point, binding, and common values. |
| Platform JSON | CLI and Qyro Engine | Host-specific overrides. |
| `build.json` | `qyro build` only | Freezer-specific override at the project root. |
| `settings/release.json` | `build`, `bundle`, and `sign` | Release build, packaging, and protection options. |
| `settings/sign.json` | `qyro sign` only | Local signing and notarization configuration. Never bundled or committed. |
| `settings/secrets.json` | CLI and Engine | Protected application values needed at runtime. Never commit it. |

The CLI deep-merges JSON objects; nested keys that are not overwritten survive. Arrays and scalar values are replaced while loading profiles. During a normal `qyro build`, the effective order is:

```text
base.json → host platform → selected profile (release by default)
          → secrets.json → recognized build.json fields → CLI flags
```

`bundle` activates the release profile. `sign` activates `release.json` and then the dedicated `sign.json` profile. The repository reapplies application secrets after profiles, but `secrets.json` must not contain signing keys. The current Windows profile loader also includes `release.json`; do not rely on that implementation detail when organizing new projects.

Profiles named `windows`, `win32`, or `win` load `windows.json`; `linux`, `linux2`, or `gnu` load `linux.json`; `mac`, `macos`, `darwin`, or `osx` merge `macos.json` and then `mac.json` when present. The loader then also tries `<profile-name>.json` when that is a distinct file. New projects should use the generated names `windows.json`, `linux.json`, and `mac.json`.

The Engine has different rules: it uses a shallow merge and does not activate `release.json` or `sign.json`, nor does it read `build.json`. Build-only keys remain inert at runtime unless application code reads them itself.

Invalid JSON, and JSON whose top level is not an object, is silently ignored by the profile repository. Validate files before release.

## `settings/base.json`

```json
{
  "app_name": "QyroKivyApp",
  "author": "Qyro Team",
  "entry_point": "main.py",
  "addons": [],
  "version": "1.0.0",
  "binding": "pyside6",
  "icon": "resources/base/app.ico",
  "hidden_imports": [
    "__future__",
    "PySide6.QtCore",
    "PySide6.QtGui",
    "PySide6.QtWidgets"
  ]
}
```

| Key | Type | Build default | Meaning |
| --- | --- | --- | --- |
| `app_name` | non-empty string | project directory name | Executable, bundle, and application name. |
| `author` | string | `Developer` | Author/publisher metadata and input to the suggested macOS bundle ID. |
| `version` | string | `1.0.0` | Artifact/package version. SemVer is recommended but not enforced by build. |
| `entry_point` | path string | `main.py` | Python script to run and freeze. |
| `binding` | string | `PySide6` | `PySide6`, `PyQt6`, `PyQt5`, `PySide2`, `Kivy`, or `Tkinter`. Parsing is case-insensitive. |
| `icon` | path string | unset | Icon passed to PyInstaller. `app_icon` is an Engine alias but is not a build alias. |
| `freeze_dir` | path string | `build` | Directory used by clean, bundle, sign, and their guards. The current freezer always writes to `build`, so do not customize this value until both paths are wired together. |
| `hidden_imports` | string array | `[]` | Dynamic modules, emitted as PyInstaller `--hidden-import` arguments. |
| `addons` | array | `[]` | Template/application metadata. The current manifest loader does not wire this value into build hooks. |
| `identifier` | string | Engine default | Runtime metadata; use `mac_bundle_identifier` for the macOS bundle. |
| `description` | string | empty | Runtime metadata. Current package adapters do not propagate it automatically. |

`qyro start` requires `binding` and `entry_point` to exist in loaded settings and checks that the entry point exists. Build has the defaults shown above, so an incomplete file can build yet fail with `qyro start`.

Framework hooks already add core modules for the selected binding and exclude competing GUI bindings. Add a hidden import when a module is loaded dynamically and PyInstaller cannot discover it. Use `collect_all` instead when a package also needs data files or native libraries.

Unknown keys remain available to Qyro Engine through `context.app_settings`, but the CLI does not act on them unless a use case explicitly recognizes them.

## Common platform options

`windows.json`, `linux.json`, and `mac.json` share these fields:

| Key | Values / default | Effect |
| --- | --- | --- |
| `bundle_mode` | `onedir` | `onedir` creates a directory; `onefile`, `bundle`, and `single` select one file. Unknown values fall back to `onedir`. |
| `paths` | `[]` | Additional import/binary lookup paths, one `--paths` argument per item. |
| `collect_all` | `[]` | Packages to collect with `--collect-all`. This can increase size significantly. |
| `hidden_imports` | `[]` | Extra dynamic imports. Build and settings lists are concatenated and deduplicated. |
| `extra_args` | `[]` | Raw PyInstaller arguments. `extra_pyinstaller_args` is an alias in settings. |

Relative paths should point inside the project so builds do not depend on one workstation.

### Debug options

```json
{
  "debug": {
    "enabled": false,
    "console": false,
    "unstripped": false,
    "bootloader_debug": false
  }
}
```

| Key | Default | Effect |
| --- | --- | --- |
| `enabled` | `false` | Enables build debug and the console window. |
| `console` | `false` | Keeps a terminal window. `console_window` is an alias. |
| `unstripped` | `false` | Adds PyInstaller import debugging; it does not disable `strip_binaries`. |
| `bootloader_debug` | `false` | Adds full PyInstaller bootloader debugging and takes precedence over import debugging. |

`"debug": true` enables both debug and the console. `verbose_imports` exists in an internal domain type but is not loaded from JSON and is not a supported public option.

### Optimization options

```json
{
  "optimization": {
    "strip_binaries": true,
    "clean_build": true,
    "upx_enabled": true,
    "upx_dir": null,
    "upx_level": 9,
    "upx_excludes": [],
    "exclude_binaries": [],
    "exclude_plugins": [],
    "bytecode_opt": 1,
    "remove_translations": true,
    "exclude_modules": []
  }
}
```

| Key | Default | Effect |
| --- | --- | --- |
| `strip_binaries` | `true` | Uses `strip` on compatible Unix binaries. Skipped on Windows. |
| `clean_build` | `true` | Uses PyInstaller `--clean` and removes temporary files, `*.dist-info`, translations, and selected generic/Wayland Qt plugins. |
| `upx_enabled` | `true` | Enables PyInstaller and post-build UPX compression. Disabled on macOS. |
| `upx_dir` | `null` | Directory containing the UPX executable. |
| `upx_level` | `9` | Post-build compression level, integer from 1 through 9. |
| `upx_excludes` | runtime-specific defaults | Glob patterns that must not be compressed. Preserve Python, Qt Core, and C/C++ runtime binaries. |
| `bytecode_opt` | `1` | `0` for none, `1` for `-O`, `2` for `-OO` (which removes docstrings). |
| `exclude_modules` | `unittest`, `test`, `pydoc` | Excludes modules in PyInstaller and purges related companion binaries afterward. |
| `exclude_binaries` | `[]` | Glob patterns removed from the output after freezing. |
| `exclude_plugins` | `[]` | Qt plugin categories/globs removed after freezing. `*` or `all` preserves only `platforms`. |
| `remove_translations` | `true` | Removes translation directories. `clean_build: true` also removes them. |

> [!WARNING]
> Exclusions trigger a post-build deletion pass. Removing `QtNetwork`, `tls`, `imageformats`, `styles`, QML/Quick, multimedia, OpenGL, or their native companions can break a build that PyInstaller completed successfully. Begin with empty exclusion lists, add one item at a time, and test the affected features on a clean machine.

### What the example exclusions remove

| Entry or family | Capability to test before excluding it |
| --- | --- |
| `PySide6.QtWebEngine*` | Embedded Chromium, advanced HTML, and WebEngine Widgets/Quick. |
| `PySide6.Qt3D*`, `QtQuick3D` | 3D scenes, animation, and rendering. |
| `PySide6.QtPdf` | Qt PDF reading and display. |
| `PySide6.QtMultimedia`, `QtSpatialAudio` | Audio, video, cameras, and spatial audio. |
| `PySide6.QtSensors`, `QtBluetooth` | Device sensors and Bluetooth. |
| `PySide6.QtCharts` | Qt Charts. |
| `PySide6.QtDesigner` | Designer runtime integration and dependent form-loading workflows. |
| `PySide6.QtVirtualKeyboard` | Virtual/touch keyboard. |
| `PySide6.QtQml`, `QtQuick`, `QtQuickWidgets` | QML engine, Qt Quick UI, and widget integration. |
| `PySide6.QtOpenGL`, `QtOpenGLWidgets` | OpenGL APIs and widgets. |
| `PySide6.QtSvg` | Qt SVG rendering. |
| `PySide6.QtNetwork` | Qt networking, sockets, and features that may support TLS/HTTPS. |
| `tkinter` | Tk UI and Tcl/Tk libraries. |
| `unittest`, `test`, `pydoc` | Standard-library development tools; generally the lowest-risk production exclusions. |

Qt plugin categories in the sample have equally concrete effects: `generic` removes generic input plugins; `networkinformation` connectivity detection; `tls` secure-transport backends; `styles` extra widget styles; `platforminputcontexts` IME/input methods; `iconengines` extra icon engines; and `imageformats` codecs such as JPEG, GIF, WebP, or SVG depending on the binding installation.

The sample binaries `opengl32sw.dll`, `qdirect2d.dll`, `qoffscreen.dll`, and `qminimal.dll` are the software OpenGL renderer and Direct2D, offscreen, and minimal platform backends. Remove them only when production cannot select those backends. The `platforms` plugin directory containing `qwindows`, `qcocoa`, or `qxcb` is required to show windows and is protected by Qyro's global plugin purge.

## `settings/windows.json`

```json
{
  "bundle_mode": "onedir",
  "paths": [],
  "collect_all": [],
  "uac": {
    "level": "asInvoker",
    "ui_access": false
  },
  "debug": {
    "enabled": false,
    "console_window": false
  },
  "optimization": {
    "strip_binaries": true,
    "clean_build": true,
    "upx_enabled": true,
    "upx_dir": null,
    "upx_level": 9,
    "upx_excludes": [
      "vcruntime140.dll",
      "python3*.dll",
      "PySide6.QtCore.pyd",
      "MSVCP140.dll",
      "Qt6Core.dll"
    ],
    "exclude_binaries": [],
    "exclude_plugins": [],
    "bytecode_opt": 1,
    "exclude_modules": ["unittest", "test", "pydoc"]
  }
}
```

`uac.level` accepts:

- `asInvoker`: normal user permissions; default.
- `highestAvailable`: parsed by the model, but the current adapter does not emit a dedicated PyInstaller flag for it.
- `requireAdministrator`: emits `--uac-admin` and triggers elevation.

`uac.ui_access: true` emits `--uac-uiaccess`. It has Windows signing and installation-location requirements; do not enable it casually. A boolean `uac` is also accepted, and `qyro build --uac` forces administrator mode.

UPX can reduce size but can also increase antivirus false positives. Test the final, signed artifact with the intended deployment controls.

## `settings/linux.json`

```json
{
  "categories": "Utility;",
  "description": "Desktop client",
  "author_email": "dev@example.com",
  "url": "https://example.com",
  "bundle_mode": "onedir",
  "paths": [],
  "collect_all": [],
  "debug": {"enabled": false, "console": false},
  "optimization": {
    "strip_binaries": true,
    "clean_build": true,
    "upx_enabled": true,
    "upx_dir": null,
    "upx_level": 9,
    "upx_excludes": ["libpython3*.so*", "libQt6Core.so*", "QtCore.abi3.so"],
    "exclude_binaries": [],
    "exclude_plugins": [],
    "bytecode_opt": 1,
    "exclude_modules": ["unittest", "test", "pydoc"]
  }
}
```

`categories`, `author_email`, `description`, and `url` remain available as settings, but the current FPM adapter does not transfer them into Linux packages. The `deb`, `rpm`, and `arch` flows primarily use `app_name`, `version`, and `/opt/<app_name>`.

Windows DLL patterns in `exclude_binaries` have no useful effect in a Linux profile. Keep per-platform lists aligned with the binaries that platform actually produces.

## `settings/mac.json`

```json
{
  "bundle_mode": "onedir",
  "paths": [],
  "collect_all": [],
  "mac_bundle_identifier": "com.qyroteam.qyrokivyapp",
  "debug": {"enabled": false, "console": false},
  "optimization": {
    "strip_binaries": true,
    "clean_build": true,
    "upx_enabled": false,
    "exclude_binaries": [],
    "exclude_plugins": [],
    "bytecode_opt": 1,
    "exclude_modules": ["unittest", "test", "pydoc"]
  }
}
```

`mac_bundle_identifier` is the reverse-DNS identifier passed to PyInstaller; `bundle_identifier` is an alias. If omitted, Qyro suggests one from author and application name. UPX is disabled on macOS regardless of the JSON value.

Use `mac_target_architecture` in `mac.json` for the macOS build target. A `universal2` build requires universal dependencies; the current flow may warn and use the build machine's native architecture instead. Qyro applies an ad-hoc signature after modifying the `.app`; distribution still requires `qyro sign` and a valid Developer ID.

## Project-root `build.json`

```json
{
  "entry_point": "src/main.py",
  "platform": "windows",
  "bundle_mode": "onefile",
  "icon": "resources/base/app.ico",
  "hidden_imports": ["my_app.plugins.pdf"],
  "paths": ["vendor"],
  "collect_all": ["babel"],
  "extra_args": ["--log-level", "WARN"],
  "resource_protection": {
    "enabled": true,
    "settings_dir": "settings",
    "resources_dir": "resources",
    "bundle_path": ".qyro/resources.pak"
  }
}
```

Supported keys are `app_name`, `author`, `version`, `entry_point`, `binding`, `platform`, `bundle`/`bundle_mode`, `uac`, `debug`, `optimization`, `hidden_imports`, `icon`, `mac_bundle_identifier`/`bundle_identifier`, `extra_args`, `paths`, `collect_all`, and `resource_protection`/`protected_resources`.

For most scalars, `build.json` wins over merged settings. `hidden_imports`, `paths`, `collect_all`, and `extra_args` are concatenated with settings; only hidden imports are deduplicated. CLI flags win for bundle mode, debug, console, UAC, and clean behavior.

Choosing another `platform` configures a manifest; it does not provide cross-compilation. Build each target on its native operating system.

## `settings/release.json`

```json
{
  "bundle_mode": "onedir",
  "resource_protection": {
    "enabled": true,
    "settings_dir": "settings",
    "resources_dir": "resources",
    "bundle_path": ".qyro/resources.pak"
  },
  "bundle": {
    "dmg": {
      "window": {"x": 200, "y": 120},
      "window_size": {"width": 660, "height": 420},
      "icon_size": 120,
      "app_position": {"x": 180, "y": 180},
      "applications_position": {"x": 480, "y": 180},
      "background": "assets/dmg-background.jpg",
      "extra_files": [
        "README.md",
        {
          "source": "docs/RELEASE_NOTES.md",
          "destination": "docs/RELEASE_NOTES.md"
        }
      ]
    },
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

Top-level values such as `"release": true` or `"environment": "production"` may be useful to application code, but current bundle and sign use cases do not assign behavior to them. `release_dir`, package format, resource copying, and ZIP creation are command flags, not `release.json` defaults.

Because `release.json` is merged before the build manifest is created, it may also override common build fields such as `bundle_mode`, `debug`, `optimization`, `paths`, `collect_all`, `hidden_imports`, `icon`, and `resource_protection`. `bundle` is interpreted by the bundle use case; signing configuration belongs in `sign.json`.

### Resource protection

| Key | Default | Meaning |
| --- | --- | --- |
| `resource_protection.enabled` | `false` | Encrypts settings/resources into a protected package and compiles the runtime secret module. |
| `settings_dir` | `settings` | Source settings directory. |
| `resources_dir` | `resources` | Source resources directory. |
| `bundle_path` | `.qyro/resources.pak` | Path inside the frozen layout. |

`protected_resources` is an alias. Even without full resource protection, the current build compiles a runtime module for encrypted secrets, so the desktop extra, Cython, and a native compiler are required.

### Extra files

`bundle.extra_files` accepts a string source copied to the release root or an object with `source` and optional `destination`. The source must exist. The destination must be relative and cannot escape the output directory.

Run `qyro bundle --check` to validate these paths without packaging.

### DMG

| Key | Shape | Meaning |
| --- | --- | --- |
| `window` | `{x, y}` | Window position. |
| `window_size` | `{width, height}` | Window size. |
| `icon_size` | positive integer | Icon size. |
| `app_position` | `{x, y}` | App icon position; `app_icon_position` is an alias. |
| `applications_position` | `{x, y}` | Applications link position; `app_drop_link` is an alias. |
| `background` | path | Existing background image. |
| `extra_files` | list | Files or directories included at the DMG root. Uses the same string/object forms as `bundle.extra_files`. |

`bundle.dmg.extra_files` is copied into the DMG staging directory, so it is included in the final image. It requires `create-dmg`; Qyro rejects this option when it would otherwise fall back to `hdiutil`, because that path does not support these files. Generic `bundle.extra_files` remains outside the DMG and is intended for `dir`, ZIP, and tarball releases.

Any custom DMG option requires `create-dmg`. With no customization, Qyro can fall back to macOS `hdiutil`.

### NSIS

| Key | Values / default | Meaning |
| --- | --- | --- |
| `icons.install` | `resources/base/install.ico` if present | Installer icon; an explicitly configured path must exist. |
| `icons.uninstall` | `resources/base/uninstall.ico` if present | Uninstaller icon; an explicitly configured path must exist. |
| `welcome_bitmap` | unset | Optional bitmap; an explicitly configured path must exist. |
| `install_location` | `programfiles64`, `programfiles32`, `appdata`; default `programfiles64` | Install base directory. |
| `execution_level` | `highest`, `admin`, `user`; default `highest` | NSIS installer execution level. |

NSIS requires `makensis`. This execution level is independent of the frozen executable's `uac` setting.

## `settings/sign.json`

This local profile contains everything consumed only by `qyro sign`. The CLI
loads it after `release.json`; Qyro Engine never loads it, and the build system
excludes it from both ordinary PyInstaller data and protected resource
packages.

```json
{
  "sign": {
    "windows": {
      "certificate": "src/sign/windows/certificate.pfx",
      "password": "certificate-password",
      "timestamp_server": "https://timestamp.digicert.com",
      "description": "QyroKivyApp",
      "url": "https://example.com"
    },
    "mac": {
      "identity": "Developer ID Application: Example (TEAMID)",
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

Keep `settings/sign.json`, certificate files, private keys, and passwords out
of version control. In CI, create them from the platform's secret store just
before signing and remove them with the disposable workspace.

### Windows signing

| Key | Required | Meaning |
| --- | --- | --- |
| `sign.windows.certificate` | yes | PFX/P12 file. Legacy default: `src/sign/windows/certificate.pfx`. |
| `password` | yes | Certificate password stored locally in `sign.json`. |
| `timestamp_server` | no | Timestamp service used by `signtool`. |
| `description` | no | Signed description; defaults to `app_name`. |
| `url` | no | Informational URL included in the signature. |

On Windows, `qyro sign` scans `.exe`, `.cab`, `.dll`, `.ocx`, `.msi`, and `.xpi`. It requires `signtool`.

### macOS signing and notarization

| Key | Meaning |
| --- | --- |
| `sign.mac.identity` | Keychain identity for `codesign`. |
| `entitlements` | Entitlements plist used while signing. |
| `notary.enabled` | Submit with `notarytool`. |
| `notary.staple` | Staple the accepted ticket; default `true`. |
| `notary.assess_gatekeeper` | Run Gatekeeper assessment; default `false`. |

Configure one authentication method:

1. `notary.keychain_profile`;
2. `notary.key_path` + `notary.key_id` and optional `notary.issuer`;
3. `notary.apple_id` + `notary.team_id` + `notary.app_password`.

The `qyro sign` flags for notarization, stapling, assessment, and keychain profile override JSON values for that invocation.

### Legacy signing keys

Older projects may use these flat aliases inside `sign.json`: `windows_sign_certificate`, `windows_sign_pass`, `windows_sign_server`, `windows_sign_description`, `mac_sign_identity`, `mac_sign_entitlements`, `mac_sign_notarize`, `mac_sign_staple`, `mac_sign_assess`, `mac_sign_notary_profile`, `mac_sign_notary_key`, `mac_sign_notary_key_id`, `mac_sign_notary_issuer`, `mac_sign_apple_id`, `mac_sign_team_id`, and `mac_sign_app_password`. Nested `sign.windows` and `sign.mac` values are recommended for new configuration. `mac_target_architecture` is a build option and belongs in `mac.json`.

## `settings/secrets.json`

This file contains API keys, tokens, licenses, and other protected values required by the application at runtime:

```json
{
  "api_url": "https://api.example.com",
  "api_key": "application-api-key",
  "service_token": "protected-token",
  "license_key": "local-license"
}
```

In source mode, Qyro Engine reads the local JSON. During `qyro build`, the CLI excludes the plaintext file, encrypts the complete object with AES-256-GCM, and stores `.qyro/secrets.enc` inside `resources.pak`. The compiled runtime module lets the Engine authenticate, decrypt, and merge it in memory. Application code reads the values through `context.app_settings` or `self.app_settings` in both modes.

The Engine uses a shallow merge, so repeat a complete nested object in secrets or prefer flat keys. The CLI uses a deep merge. Do not add a `sign` object or legacy flat signing keys here: the build rejects them instead of embedding signing credentials in the client. Signing and notarization values belong exclusively in `settings/sign.json`.

Operational rules:

- Keep `settings/secrets.json` in `.gitignore` and inspect `git status` before commits.
- Keep `settings/sign.json` in `.gitignore`; it is never needed by the application runtime.
- Use `{}` when the initial unprotected build requires the file but the application has no runtime secrets.
- Create and edit `secrets.json` locally; do not submit its values to Settings Builder.
- Never log the merged settings dictionary.
- Values are decrypted in memory when used. For high-value secrets, apply minimum scope, rotation, and short-lived tokens.

## Safe baseline

Start with a debuggable `onedir` build and no aggressive deletion:

```json
{
  "bundle_mode": "onedir",
  "debug": {"enabled": false, "console": false},
  "optimization": {
    "clean_build": true,
    "strip_binaries": false,
    "upx_enabled": false,
    "bytecode_opt": 1,
    "remove_translations": false,
    "exclude_modules": [],
    "exclude_binaries": [],
    "exclude_plugins": []
  }
}
```

Test the artifact, then enable one optimization at a time. Switch to `onefile` only after validating startup time, temporary extraction behavior, and security-tool compatibility.

## Release checklist

- All entry point, icon, certificate, entitlement, background, and extra-file paths exist.
- The configured binding is installed in the Python environment running Qyro CLI.
- Each target was built on its native operating system.
- Exclusions were tested against network/TLS, image formats, styles, printing, multimedia, and every optional feature the app uses.
- `qyro sign --check` and `qyro bundle --check` succeed where applicable.
- The final artifact starts on a clean machine.
- `settings/secrets.json` and `settings/sign.json` are untracked, and no signing credentials are present in the runtime secrets payload.
