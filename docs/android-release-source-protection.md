# Android source protection

The former `.pyc` protection mode has been replaced by **Cython-native Android ARM64 extensions**. Protected builds no longer generate application bytecode or fall back to `.pyc`.

See [Android Cython source protection](android-cython-protection.md) for the current config, Android build pipeline, supported targets and certification requirements.

Keep signing credentials under `settings/sign.json` (Android section); keep excluded paths under `settings/android.json`. Both stay out of the APK. Native compilation is fail-closed and requires the matching Android Python headers and NDK.
