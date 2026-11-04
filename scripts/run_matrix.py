from __future__ import annotations

import argparse
import itertools
import json
import os
import platform
import shutil
import signal
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TextIO


DEFAULT_CLI_REPOSITORY = (
    "https://github.com/Neuri-AI/Velto-CLI.git"
)

# Maximum seconds any single subprocess may run before its whole
# process tree is killed. Prevents CI jobs from freezing silently.
DEFAULT_COMMAND_TIMEOUT = 900.0

FRAMEWORK_PACKAGES = {
    "PySide6": "PySide6",
    "PyQt6": "PyQt6",
    "PyQt5": "PyQt5",
    "Kivy": "kivy",
    "Tkinter": None,
}

FRAMEWORK_DISPLAY_NAMES = {
    "PySide6": "PySide6",
    "PyQt6": "PyQt6",
    "PyQt5": "PyQt5",
    "Kivy": "Kivy",
    "Tkinter": "Tkinter",
}


@dataclass(frozen=True)
class TestCase:
    python_version: str
    framework: str


@dataclass
class TestResult:
    case: TestCase
    environment: str
    host_platform: str
    status: str
    failed_step: str | None = None
    error: str | None = None
    start_skipped: bool = False


def print_command(command: list[str]) -> None:
    print("$", " ".join(command), flush=True)


def run_command(
    command: list[str],
    *,
    cwd: Path | None = None,
    log_file: Path | None = None,
    append_log: bool = False,
    check: bool = True,
    timeout: float | None = DEFAULT_COMMAND_TIMEOUT,
) -> subprocess.CompletedProcess[str]:
    print_command(command)

    resolved_cwd = cwd.resolve() if cwd else None

    if resolved_cwd:
        if not resolved_cwd.exists():
            raise FileNotFoundError(
                f"Working directory does not exist: {resolved_cwd}"
            )

        if not resolved_cwd.is_dir():
            raise NotADirectoryError(
                f"Working directory is not a directory: {resolved_cwd}"
            )

    popen_kwargs: dict = {}

    # Own process group / session so the whole tree can be killed.
    if os.name == "nt":
        popen_kwargs["creationflags"] = (
            subprocess.CREATE_NEW_PROCESS_GROUP
        )
    else:
        popen_kwargs["start_new_session"] = True

    process = subprocess.Popen(
        command,
        cwd=resolved_cwd,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
        **popen_kwargs,
    )

    chunks: list[str] = []

    def pump() -> None:
        assert process.stdout is not None

        for line in process.stdout:
            chunks.append(line)
            print(line, end="", flush=True)

    reader = threading.Thread(target=pump, daemon=True)
    reader.start()

    timed_out = False

    try:
        process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        timed_out = True

        print(
            f"TIMEOUT after {timeout:.0f}s; killing process tree.",
            file=sys.stderr,
            flush=True,
        )

        close_process_tree(process)

    # Do not wait forever for EOF if a grandchild keeps the pipe open.
    reader.join(timeout=5)

    output = "".join(chunks)

    if log_file:
        log_file.parent.mkdir(parents=True, exist_ok=True)

        mode = "a" if append_log else "w"

        with log_file.open(mode, encoding="utf-8") as handle:
            if append_log:
                handle.write("$ " + " ".join(command) + "\n")

            handle.write(output)

            if append_log and output and not output.endswith("\n"):
                handle.write("\n")

    if timed_out:
        raise subprocess.TimeoutExpired(
            command,
            timeout,
            output=output,
        )

    returncode = process.returncode

    if check and returncode != 0:
        raise subprocess.CalledProcessError(
            returncode,
            command,
            output=output,
        )

    return subprocess.CompletedProcess(
        command,
        returncode,
        stdout=output,
    )


def find_conda() -> str:
    conda = shutil.which("conda")

    if not conda:
        raise RuntimeError(
            "Conda was not found in PATH. "
            "Install Miniconda or Anaconda and try again."
        )

    return conda


def sanitize_name(value: str) -> str:
    return (
        value.lower()
        .replace(".", "")
        .replace("-", "")
        .replace("_", "")
        .replace(" ", "")
    )


def environment_name(case: TestCase, prefix: str) -> str:
    return (
        f"{prefix}-py{sanitize_name(case.python_version)}-"
        f"{sanitize_name(case.framework)}"
    )


def conda_run_command(
    conda: str,
    environment: str,
    command: list[str],
    *,
    cwd: Path | None = None,
) -> list[str]:
    result = [
        conda,
        "run",
        "--no-capture-output",
        "--name",
        environment,
    ]

    if cwd:
        absolute_cwd = cwd.resolve()

        if not absolute_cwd.exists():
            raise FileNotFoundError(
                f"Working directory does not exist: {absolute_cwd}"
            )

        if not absolute_cwd.is_dir():
            raise NotADirectoryError(
                f"Working directory is not a directory: {absolute_cwd}"
            )

        result.extend(["--cwd", str(absolute_cwd)])

    result.extend(command)

    return result


def run_in_conda(
    conda: str,
    environment: str,
    command: list[str],
    *,
    cwd: Path | None = None,
    log_file: Path | None = None,
    append_log: bool = False,
    check: bool = True,
    timeout: float | None = DEFAULT_COMMAND_TIMEOUT,
) -> subprocess.CompletedProcess[str]:
    return run_command(
        conda_run_command(
            conda,
            environment,
            command,
            cwd=cwd,
        ),
        log_file=log_file,
        append_log=append_log,
        check=check,
        timeout=timeout,
    )


def list_conda_environments(conda: str) -> set[str]:
    result = run_command(
        [conda, "env", "list", "--json"],
        check=True,
    )

    data = json.loads(result.stdout)

    return {
        Path(prefix).name
        for prefix in data.get("envs", [])
    }


def remove_conda_environment(
    conda: str,
    environment: str,
) -> None:
    print(
        f"Removing Conda environment: {environment}",
        flush=True,
    )

    run_command(
        [
            conda,
            "env",
            "remove",
            "--yes",
            "--name",
            environment,
        ],
        check=True,
    )


def create_conda_environment(
    conda: str,
    environment: str,
    python_version: str,
) -> None:
    command = [
        conda,
        "create",
        "--yes",
        "--name",
        environment,
        "--channel",
        "conda-forge",
        f"python={python_version}",
        "pip",
    ]

    run_command(command)


def install_package(
    conda: str,
    environment: str,
    package: str,
    log_file: Path,
) -> None:
    run_in_conda(
        conda,
        environment,
        [
            "python",
            "-m",
            "pip",
            "install",
            "--upgrade",
            package,
        ],
        log_file=log_file,
        append_log=True,
    )


def install_framework(
    conda: str,
    environment: str,
    framework: str,
    log_file: Path,
) -> None:
    package = FRAMEWORK_PACKAGES[framework]

    if package is None:
        return

    install_package(
        conda,
        environment,
        package,
        log_file,
    )


def install_project(
    conda: str,
    environment: str,
    framework: str,
    cli_source: Path | None,
    runtime_source: Path | None,
    cli_repository: str,
    log_file: Path,
) -> None:
    install_package(
        conda,
        environment,
        "pip",
        log_file,
    )

    install_package(
        conda,
        environment,
        "Pillow",
        log_file,
    )

    install_package(
        conda,
        environment,
        "PyInstaller",
        log_file,
    )

    # `velto init` uses Poetry; it is not guaranteed on CI runners.
    install_package(
        conda,
        environment,
        "poetry",
        log_file,
    )

    install_framework(
        conda,
        environment,
        framework,
        log_file,
    )

    if runtime_source:
        install_package(
            conda,
            environment,
            str(runtime_source.resolve()),
            log_file,
        )
    else:
        install_package(
            conda,
            environment,
            "qyro-engine>=0.1.0,<1.0.0",
            log_file,
        )

    # PSX markup compilation is provided by Velto Core, not Qyro Engine.
    install_package(
        conda,
        environment,
        "velto-core>=0.1.0,<1.0.0",
        log_file,
    )

    if cli_source:
        install_package(
            conda,
            environment,
            str(cli_source.resolve()),
            log_file,
        )
    else:
        install_package(
            conda,
            environment,
            "velto-cli",
            log_file,
        )


def create_project(
    conda: str,
    environment: str,
    case: TestCase,
    workspace: Path,
    log_file: Path,
) -> Path:
    workspace = workspace.resolve()

    project_name = "sample-app"
    project_dir = (workspace / project_name).resolve()

    command = [
        "velto",
        "init",
        "--name",
        project_name,
        "--target-platform",
        "desktop",
        "--framework",
        case.framework,
        "--app-name",
        "SampleApp",
        "--version",
        "1.0.0",
        "--author",
        "Qyro Matrix Test",
        "--yes",
    ]

    try:
        run_in_conda(
            conda,
            environment,
            command,
            cwd=workspace,
            log_file=log_file,
        )

    except subprocess.CalledProcessError as error:
        if case.framework != "PyQt5":
            raise

        if not project_dir.exists():
            raise RuntimeError(
                "PyQt5 initialization failed and the project directory "
                "was not created."
            ) from error

        failure_log = log_file.with_name(
            "init-poetry-failure.log"
        )

        failure_log.write_text(
            error.output or "",
            encoding="utf-8",
        )

        print(
            "PyQt5 project was created, but Poetry failed. "
            "Continuing with pip fallback.",
            file=sys.stderr,
            flush=True,
        )

    if not project_dir.exists():
        raise RuntimeError(
            "velto init completed, but the project was not created: "
            f"{project_dir}"
        )

    return project_dir


def install_pyqt5_fallback(
    conda: str,
    environment: str,
    log_file: Path,
) -> None:
    print(
        "Installing PyQt5 with pip fallback...",
        flush=True,
    )

    install_package(
        conda,
        environment,
        "PyQt5",
        log_file,
    )


def close_process_tree(
    process: subprocess.Popen[str],
    *,
    grace_period: float = 8.0,
) -> None:
    if process.poll() is not None:
        return

    if os.name == "nt":
        subprocess.run(
            [
                "taskkill",
                "/PID",
                str(process.pid),
                "/T",
                "/F",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )

        try:
            process.wait(timeout=grace_period)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=grace_period)

        return

    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        return

    try:
        process.wait(timeout=grace_period)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass

        process.wait(timeout=grace_period)


def start_application(
    conda: str,
    environment: str,
    project_dir: Path,
    log_file: Path,
    *,
    startup_timeout: float,
    run_time: float,
) -> None:
    project_dir = project_dir.resolve()
    log_file = log_file.resolve()
    log_file.parent.mkdir(parents=True, exist_ok=True)

    log_handle: TextIO = log_file.open(
        "w",
        encoding="utf-8",
    )

    command = conda_run_command(
        conda,
        environment,
        ["velto", "start"],
        cwd=project_dir,
    )

    print_command(command)

    popen_kwargs = {
        "cwd": project_dir,
        "stdin": subprocess.DEVNULL,
        "stdout": log_handle,
        "stderr": subprocess.STDOUT,
        "text": True,
    }

    if os.name == "nt":
        popen_kwargs["creationflags"] = (
            subprocess.CREATE_NEW_PROCESS_GROUP
        )
    else:
        popen_kwargs["start_new_session"] = True

    process = subprocess.Popen(
        command,
        **popen_kwargs,
    )

    try:
        deadline = time.monotonic() + startup_timeout

        while time.monotonic() < deadline:
            return_code = process.poll()

            if return_code is not None:
                if return_code == 0:
                    print(
                        "Application closed normally with exit code 0.",
                        flush=True,
                    )
                    return

                raise RuntimeError(
                    "velto start exited during startup with exit code "
                    f"{return_code}"
                )

            time.sleep(0.25)

        print(
            f"Application remained alive for {run_time:.1f} seconds.",
            flush=True,
        )

        time.sleep(run_time)

        return_code = process.poll()

        if return_code is not None and return_code != 0:
            raise RuntimeError(
                "velto start exited unexpectedly with exit code "
                f"{return_code}"
            )

    finally:
        close_process_tree(process)
        log_handle.close()


def run_build(
    conda: str,
    environment: str,
    project_dir: Path,
    log_file: Path,
) -> None:
    run_in_conda(
        conda,
        environment,
        ["velto", "build", "--clean"],
        cwd=project_dir,
        log_file=log_file,
    )


def run_bundle(
    conda: str,
    environment: str,
    project_dir: Path,
    log_file: Path,
) -> None:
    run_in_conda(
        conda,
        environment,
        ["velto", "bundle"],
        cwd=project_dir,
        log_file=log_file,
    )


def run_case(
    conda: str,
    case: TestCase,
    results_root: Path,
    *,
    prefix: str,
    cli_source: Path | None,
    runtime_source: Path | None,
    cli_repository: str,
    startup_timeout: float,
    run_time: float,
    keep_projects: bool,
    recreate_environment: bool,
    clean_environment: bool,
    skip_start: bool = False,
) -> TestResult:
    results_root = results_root.resolve()

    environment = environment_name(case, prefix)
    case_root = results_root / environment
    workspace = case_root / "workspace"
    logs = case_root / "logs"
    status_file = case_root / "status.txt"

    if case_root.exists() and recreate_environment:
        shutil.rmtree(case_root)

    case_root.mkdir(parents=True, exist_ok=True)
    workspace.mkdir(parents=True, exist_ok=True)
    logs.mkdir(parents=True, exist_ok=True)

    host_platform = platform.platform()
    failed_step: str | None = None
    project_dir: Path | None = None

    print()
    print("=" * 80)
    print(
        f"Testing Python {case.python_version} / "
        f"{FRAMEWORK_DISPLAY_NAMES[case.framework]}"
    )
    print(f"Host platform: {host_platform}")
    print(f"Conda environment: {environment}")
    print("=" * 80)

    try:
        existing_environments = list_conda_environments(conda)

        if (
            recreate_environment
            and environment in existing_environments
        ):
            failed_step = "environment-clean"

            remove_conda_environment(
                conda,
                environment,
            )

            existing_environments.remove(environment)

        if environment not in existing_environments:
            failed_step = "environment"

            create_conda_environment(
                conda,
                environment,
                case.python_version,
            )

        failed_step = "install"

        # install.log is appended to by every install command.
        (logs / "install.log").unlink(missing_ok=True)

        install_project(
            conda,
            environment,
            case.framework,
            cli_source,
            runtime_source,
            cli_repository,
            logs / "install.log",
        )

        failed_step = "init"

        project_dir = create_project(
            conda,
            environment,
            case,
            workspace,
            logs / "init.log",
        )

        if case.framework == "PyQt5":
            failed_step = "pyqt5-fallback"

            install_pyqt5_fallback(
                conda,
                environment,
                logs / "pyqt5-fallback.log",
            )

        if skip_start:
            print(
                f"SKIP start: disabled for "
                f"{FRAMEWORK_DISPLAY_NAMES[case.framework]} "
                f"on this runner.",
                flush=True,
            )
        else:
            failed_step = "start"

            start_application(
                conda,
                environment,
                project_dir,
                logs / "start.log",
                startup_timeout=startup_timeout,
                run_time=run_time,
            )

        failed_step = "build"

        run_build(
            conda,
            environment,
            project_dir,
            logs / "build.log",
        )

        failed_step = "bundle"

        run_bundle(
            conda,
            environment,
            project_dir,
            logs / "bundle.log",
        )

        status_file.write_text(
            "PASS\n",
            encoding="utf-8",
        )

        print(
            f"PASS: Python {case.python_version} / "
            f"{FRAMEWORK_DISPLAY_NAMES[case.framework]}",
            flush=True,
        )

        return TestResult(
            case=case,
            environment=environment,
            host_platform=host_platform,
            status="PASS",
            start_skipped=skip_start,
        )

    except subprocess.CalledProcessError as error:
        message = (
            f"Command failed with exit code {error.returncode}"
        )

        status_file.write_text(
            f"FAIL\nstep={failed_step}\n{message}\n",
            encoding="utf-8",
        )

        print(
            f"FAIL: {failed_step} - {message}",
            file=sys.stderr,
            flush=True,
        )

        return TestResult(
            case=case,
            environment=environment,
            host_platform=host_platform,
            status="FAIL",
            failed_step=failed_step,
            error=message,
        )

    except Exception as error:
        # Includes subprocess.TimeoutExpired.
        message = str(error)

        status_file.write_text(
            f"FAIL\nstep={failed_step}\n{message}\n",
            encoding="utf-8",
        )

        print(
            f"FAIL: {failed_step} - {message}",
            file=sys.stderr,
            flush=True,
        )

        return TestResult(
            case=case,
            environment=environment,
            host_platform=host_platform,
            status="FAIL",
            failed_step=failed_step,
            error=message,
        )

    finally:
        if not keep_projects:
            if project_dir and project_dir.exists():
                shutil.rmtree(project_dir)

        if clean_environment:
            try:
                remove_conda_environment(
                    conda,
                    environment,
                )
            except Exception as error:
                print(
                    f"WARNING: could not remove environment "
                    f"{environment}: {error}",
                    file=sys.stderr,
                    flush=True,
                )


def write_summary(
    results_root: Path,
    results: list[TestResult],
) -> Path:
    summary_file = results_root / "summary.md"

    lines = [
        "# Velto CLI Compatibility Test Summary",
        "",
        f"- Host platform: `{platform.platform()}`",
        f"- Host Python: `{platform.python_version()}`",
        f"- Generated at: "
        f"`{time.strftime('%Y-%m-%d %H:%M:%S')}`",
        "",
        "| Framework | Python | Platform | Environment | Status | Step |",
        "|---|---:|---|---|---|---|",
    ]

    for result in results:
        if result.status == "PASS":
            status = (
                "✅ PASS (start skipped)"
                if result.start_skipped
                else "✅ PASS"
            )
        else:
            status = "❌ FAIL"

        step = result.failed_step or "—"

        lines.append(
            f"| {FRAMEWORK_DISPLAY_NAMES[result.case.framework]} "
            f"| {result.case.python_version} "
            f"| {result.host_platform} "
            f"| `{result.environment}` "
            f"| {status} "
            f"| {step} |"
        )

    lines.extend(
        [
            "",
            "## Interpretation",
            "",
            "- `✅ PASS`: "
            "`init → start → build → bundle` completed.",
            "- `✅ PASS (start skipped)`: "
            "`init → build → bundle` completed; the `start` smoke "
            "test was skipped (e.g. runner without a GPU).",
            "- `❌ FAIL`: inspect the step-specific log directory.",
            "- Results apply only to the host platform shown above.",
        ]
    )

    summary_file.write_text(
        "\n".join(lines) + "\n",
        encoding="utf-8",
    )

    return summary_file


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Create Conda environments and test the complete "
            "Velto CLI workflow."
        )
    )

    parser.add_argument(
        "--python",
        nargs="+",
        default=[
            "3.10",
            "3.11",
            "3.12",
            "3.13",
            "3.14",
        ],
        help="Python versions to test.",
    )

    parser.add_argument(
        "--framework",
        nargs="+",
        choices=sorted(FRAMEWORK_PACKAGES),
        default=list(FRAMEWORK_PACKAGES),
        help="Frameworks to test.",
    )

    parser.add_argument(
        "--root",
        type=Path,
        default=Path("velto-matrix-results"),
        help="Directory for generated projects and logs.",
    )

    parser.add_argument(
        "--prefix",
        default="velto",
        help="Prefix used for Conda environment names.",
    )

    parser.add_argument(
        "--cli-source",
        type=Path,
        help=(
            "Local Velto-CLI checkout. If omitted, Velto-CLI is "
            "installed from PyPI."
        ),
    )

    parser.add_argument(
        "--runtime-source",
        type=Path,
        help=(
            "Local Qyro Engine checkout. If omitted, qyro-engine is installed "
            "from PyPI."
        ),
    )

    parser.add_argument(
        "--cli-repository",
        default=DEFAULT_CLI_REPOSITORY,
        help=(
            "Legacy Git repository URL (unused; CLI installs from PyPI when "
            "--cli-source is not provided)."
        ),
    )

    parser.add_argument(
        "--startup-timeout",
        type=float,
        default=15.0,
        help=(
            "Seconds to wait for velto start to remain alive "
            "after launching."
        ),
    )

    parser.add_argument(
        "--run-time",
        type=float,
        default=5.0,
        help=(
            "Seconds to keep velto start alive after startup "
            "validation."
        ),
    )

    parser.add_argument(
        "--skip-start",
        nargs="*",
        choices=sorted(FRAMEWORK_PACKAGES),
        default=[],
        help=(
            "Frameworks for which the `velto start` smoke test is "
            "skipped (e.g. CI runners without a GPU)."
        ),
    )

    parser.add_argument(
        "--keep-projects",
        action="store_true",
        help="Keep generated project directories.",
    )

    parser.add_argument(
        "--recreate-environments",
        action="store_true",
        help="Delete and recreate existing test environments.",
    )

    parser.add_argument(
        "--clean-environments",
        action="store_true",
        help="Remove each Conda environment after its test.",
    )

    return parser.parse_args()


def main() -> int:
    args = parse_arguments()

    try:
        conda = find_conda()
    except RuntimeError as error:
        print(error, file=sys.stderr)
        return 2

    if args.cli_source and not args.cli_source.exists():
        print(
            f"CLI source path does not exist: {args.cli_source}",
            file=sys.stderr,
        )
        return 2

    if args.runtime_source and not args.runtime_source.exists():
        print(
            f"Runtime source path does not exist: "
            f"{args.runtime_source}",
            file=sys.stderr,
        )
        return 2

    args.root.mkdir(parents=True, exist_ok=True)

    cases = [
        TestCase(
            python_version=python_version,
            framework=framework,
        )
        for python_version, framework in itertools.product(
            args.python,
            args.framework,
        )
    ]

    results: list[TestResult] = []

    for case in cases:
        result = run_case(
            conda,
            case,
            args.root,
            prefix=args.prefix,
            cli_source=args.cli_source,
            runtime_source=args.runtime_source,
            cli_repository=args.cli_repository,
            startup_timeout=args.startup_timeout,
            run_time=args.run_time,
            keep_projects=args.keep_projects,
            recreate_environment=args.recreate_environments,
            clean_environment=args.clean_environments,
            skip_start=case.framework in args.skip_start,
        )

        results.append(result)

    summary_file = write_summary(
        args.root,
        results,
    )

    passed = sum(
        result.status == "PASS"
        for result in results
    )
    failed = len(results) - passed

    print()
    print("=" * 80)
    print("TEST SUMMARY")
    print("=" * 80)
    print(f"Passed: {passed}")
    print(f"Failed: {failed}")
    print(f"Summary: {summary_file}")

    if failed:
        print()
        print("Failed cases:")

        for result in results:
            if result.status == "FAIL":
                print(
                    f"- {FRAMEWORK_DISPLAY_NAMES[result.case.framework]} "
                    f"/ Python {result.case.python_version} "
                    f"/ step {result.failed_step}"
                )

        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())