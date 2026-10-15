"""
qyro.domain.errors
Pure typed exceptions representing specific business rule and operational violations.
"""

from typing import Optional


class QyroError(Exception):
    """Base error for all Qyro domain failures."""

    def __init__(self, message: str, hint: Optional[str] = None):
        super().__init__(message)
        self.message = message
        self.hint = hint

    def __str__(self) -> str:
        if self.hint:
            return f"{self.message}\n\nHint: {self.hint}"

        return self.message

# --- Project lifecycle ------------------------------------------------------

class ApplicationExecutionError(QyroError):
    def __init__(self, exit_code: int):
        super().__init__(
            f"Application failed with exit code {exit_code}.",
            hint="Check the application output for details.",
        )
        self.exit_code = exit_code

class ProjectAlreadyExistsError(QyroError):
    def __init__(self, path: str):
        super().__init__(
            f"A project already exists at destination: '{path}'",
            hint="Choose another directory or clear the existing directory.",
        )
        self.path = path


class NotAProjectError(QyroError):
    def __init__(self, current_dir: str):
        super().__init__(
            f"Current directory '{current_dir}' is not a valid Qyro project.",
            hint=(
                "Run 'qyro init' first or navigate to a project containing a valid qyro project"
            ),
        )
        self.current_dir = current_dir


class FrozenAppNotFoundError(QyroError):
    def __init__(self, expected_path: str = ""):
        super().__init__(
            "Could not find the frozen application.",
            hint="Run 'qyro freeze' first.",
        )
        self.expected_path = expected_path


class InstallerNotFoundError(QyroError):
    def __init__(self, expected_path: str = ""):
        super().__init__(
            "Could not find the installer.",
            hint="Run 'qyro installer' first.",
        )
        self.expected_path = expected_path


class AbortedByUserError(QyroError):
    def __init__(self):
        super().__init__("Operation aborted by user.")


# --- Validation -------------------------------------------------------------


class InvalidVersionError(QyroError):
    def __init__(self, version_str: str):
        super().__init__(
            f"'{version_str}' is not a valid Semantic Version "
            "(expected MAJOR.MINOR.PATCH).",
            hint="Use semver format such as '1.0.0' or '2.1.0'.",
        )
        self.version_str = version_str


class InvalidBindingError(QyroError):
    def __init__(self, value: str, valid_values: tuple):
        super().__init__(
            f"Invalid binding '{value}'.",
            hint=f"Valid bindings are: {', '.join(valid_values)}",
        )
        self.value = value
        self.valid_values = valid_values


# --- Environment ------------------------------------------------------------


class UnsupportedPlatformError(QyroError):
    def __init__(self, platform_name: str):
        super().__init__(
            f"Platform '{platform_name}' is not supported for this build command."
        )
        self.platform_name = platform_name


class UnsupportedDistributionError(QyroError):
    def __init__(self, distribution: str = ""):
        message = "This Linux distribution is not supported."
        if distribution:
            message = f"Linux distribution '{distribution}' is not supported."

        super().__init__(
            message,
            hint=(
                "Run 'qyro buildvm' followed by 'qyro runvm' to start a "
                "Docker VM using a supported distribution."
            ),
        )
        self.distribution = distribution


class UnsupportedOperationError(QyroError):
    """The current platform does not support the requested operation."""

    def __init__(self, operation: str, platform_name: str):
        super().__init__(
            f"Operation '{operation}' is not supported on {platform_name}."
        )
        self.operation = operation
        self.platform_name = platform_name


class MissingDependencyError(QyroError):
    def __init__(self, package_name: str):
        super().__init__(
            f"Required dependency '{package_name}' is not installed "
            "in the environment.",
            hint=f"Install it using: pip install {package_name}",
        )
        self.package_name = package_name


class MissingBindingError(QyroError):
    def __init__(self, candidates):
        if isinstance(candidates, str):
            candidates = (candidates,)
        candidates = tuple(candidates)

        names = ", ".join(candidates)

        if len(candidates) == 1:
            message = f"Could not find binding '{candidates[0]}'."
        elif len(candidates) == 2:
            message = f"Could not find binding '{candidates[0]}' or '{candidates[1]}'."
        else:
            message = f"Could not find any of the required bindings: {names}."

        hint = "\n".join(
            f"Install it using: pip install {candidate}"
            for candidate in candidates
        )

        super().__init__(
            message,
            hint=hint,
        )
        self.candidates = candidates


class PackageInstallationError(QyroError):
    def __init__(self, package: str):
        super().__init__(
            f"Failed to install package '{package}'.",
            hint=(
                "Check your internet connection and permissions, "
                "then try again."
            ),
        )
        self.package = package


# --- Configuration ----------------------------------------------------------


class MissingSettingError(QyroError):
    def __init__(self, key: str):
        super().__init__(
            f"Required configuration key '{key}' is missing in project settings.",
            hint="Check 'settings/base.json' or target environment configs.",
        )
        self.key = key

class EntryPointNotFoundError(QyroError):
    def __init__(self, path: str):
        super().__init__(
            f"Entry point '{path}' was not found.",
            hint="Check the 'entry_point' path in 'settings/base.json'.",
        )
        self.path = path


class GpgKeyNotConfiguredError(QyroError):
    def __init__(self):
        super().__init__(
            "GPG key for code signing is not configured.",
            hint=(
                "Run 'qyro gengpgkey' or configure 'gpg_key' and "
                "'gpg_pass' in the project settings."
            ),
        )


class CredentialsNotConfiguredError(QyroError):
    def __init__(self, key: str):
        super().__init__(
            f"Required credential '{key}' is not configured.",
            hint="Run 'qyro register' or 'qyro login'.",
        )
        self.key = key


# --- Project generation / build --------------------------------------------

class FreezeExecutionError(QyroError):
    """Raised when PyInstaller returns a non-zero exit code."""

    def __init__(self, command: str, exit_code: int, output: str):
        super().__init__(
            f"Freezing failed with exit code {exit_code}.",
            hint=f"Check PyInstaller logs below:\n{output[-1500:] if len(output) > 1500 else output}",
        )
        self.command = command
        self.exit_code = exit_code
        self.output = output


class TemplateUnavailableError(QyroError):
    def __init__(self, reason: str):
        super().__init__(
            f"Could not download project template: {reason}",
            hint="Check your internet connection or GitHub token permissions.",
        )
        self.reason = reason


class MobileBuildError(QyroError):
    def __init__(self, target: str, details: str):
        super().__init__(
            f"Failed to compile for target '{target}': {details}",
            hint="Verify SDK path and platform build dependencies.",
        )
        self.target = target
        self.details = details