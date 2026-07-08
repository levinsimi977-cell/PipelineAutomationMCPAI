"""
Compilation Agent — verifies that the app still compiles after the SDK
installation step (sdkAgent) has modified its project files.

Architecture: two deterministic build runners, no LLM involved anywhere:
  * `run_gradle_build`  -> Android, shells out to the project's `gradlew`.
  * `run_xcodebuild`    -> iOS, shells out to `xcodebuild`.
Both return the same `CompilationResult` shape, so the rest of the
pipeline doesn't need to care which platform actually ran.

`check_compilation(state)` is the pipeline entry point: it reads
`state["platform"]` ("android" | "ios") and dispatches to the matching
runner. Runs after `sdkAgent` applies the SDK changes and before the
runtime verification step (`answer_agent` / verify_sdk log checks).

This module intentionally fixes the pitfalls that broke the reference
implementation (see mcp_test_runner/android_project.py in the AppsFlyer MCP
demo project):
  * no timeout -> a hung Gradle/Xcode process could block the pipeline forever
  * `gradlew` copied without its executable bit -> "Permission denied" on
    macOS/Linux sandboxes
  * `shell=True` combined with a list of args on Windows breaks as soon as
    the sandbox path contains spaces or non-ASCII characters (this is
    exactly what happens under `sandboxes/run_<timestamp>/...`)
  * only the last 500 chars of output were kept, with no full log on disk
    for debugging a failure
  * a missing Android SDK location (`local.properties` / `ANDROID_HOME`) is
    the single most common Android "compilation" failure and was not
    handled at all
"""
from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

DEFAULT_GRADLE_TASK = "assembleDebug"
DEFAULT_IOS_CONFIGURATION = "Debug"
DEFAULT_IOS_SDK = "iphonesimulator"
DEFAULT_TIMEOUT_SECONDS = 900  # 15 min: a first/clean build downloads the whole toolchain + deps
LOG_TAIL_CHARS = 4000

# State keys checked (in order) to find the project to compile. Kept
# flexible on purpose: the pre-build pipeline state is a plain dict (not
# the pydantic UseCaseContract yet), and different teams/stages may name
# the working-copy path differently.
APP_PATH_STATE_KEYS = ("sandbox_path", "app_path", "source_path", "project_path")


@dataclass
class CompilationResult:
    """Structured, platform-agnostic outcome of a single compilation check run."""

    success: bool
    status: str  # "PASSED" | "FAILED" | "TIMEOUT" | "SKIPPED" | "ERROR"
    message: str
    platform: str = "android"
    build_target: str = ""  # gradle task (android) or scheme (ios)
    return_code: Optional[int] = None
    duration_seconds: float = 0.0
    log_path: Optional[str] = None
    stdout_tail: str = ""
    stderr_tail: str = ""
    extra: dict[str, Any] = field(default_factory=dict)

    def to_audit_event(self, source: str = "compilationAgent") -> dict[str, Any]:
        """
        Shape this result as an audit event consumable by
        infra/user_interface_use_case/reports/reporter.py (ReportGenerator
        buckets anything mentioning "build"/"compile"/"gradle"/"xcode" into
        the "Build" phase automatically).
        """
        status_map = {
            "PASSED": "passed",
            "FAILED": "failed",
            "TIMEOUT": "failed",
            "ERROR": "failed",
            "SKIPPED": "warning",
        }
        return {
            "source": source,
            "event": f"{self.platform}_compilation",
            "phase": "Build",
            "status": status_map.get(self.status, "info"),
            "details": self.message,
            "timestamp": datetime.utcnow().isoformat() + "Z",
        }


def _resolve_app_path(state: dict[str, Any]) -> str:
    for key in APP_PATH_STATE_KEYS:
        value = state.get(key)
        if value:
            return str(value)
    return ""


def _write_log(project_root: Path, stdout: str, stderr: str, prefix: str) -> str:
    log_dir = project_root / "compilation-logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / f"{prefix}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
    log_path.write_text(
        f"----- STDOUT -----\n{stdout}\n\n----- STDERR -----\n{stderr}\n",
        encoding="utf-8",
    )
    return str(log_path)


# ─────────────────────────────────────────────────────────────────────────
# Android — Gradle
# ─────────────────────────────────────────────────────────────────────────


def _find_gradle_wrapper(app_path: str | Path) -> Optional[Path]:
    """
    Locate the Gradle wrapper for the project. Checks `app_path` itself
    first, then falls back to a shallow scan of its immediate
    subdirectories, since `app_path` may point at a folder that merely
    *contains* the Android project (e.g. an unzipped sandbox) rather than
    the Gradle project root itself.
    """
    root = Path(app_path)
    candidates = [root] + ([p for p in root.iterdir() if p.is_dir()] if root.is_dir() else [])

    for candidate in candidates:
        for name in ("gradlew.bat", "gradlew"):
            wrapper = candidate / name
            if wrapper.is_file():
                return wrapper
    return None


def _ensure_executable(executable: Path) -> None:
    """Restore the executable bit on POSIX systems (lost on plain file copies/zips)."""
    if os.name == "nt":
        return
    mode = executable.stat().st_mode
    executable.chmod(mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def _ensure_android_sdk_location(project_root: Path) -> None:
    """
    Make sure Gradle can find the Android SDK. `local.properties` is always
    git-ignored, so a freshly cloned/copied sandbox never has it; if it's
    missing, write `sdk.dir` from ANDROID_HOME / ANDROID_SDK_ROOT so the
    build doesn't fail with "SDK location not found".
    """
    local_properties = project_root / "local.properties"
    if local_properties.exists():
        return

    sdk_dir = os.environ.get("ANDROID_HOME") or os.environ.get("ANDROID_SDK_ROOT")
    if not sdk_dir:
        return

    escaped = sdk_dir.replace("\\", "\\\\").replace(":", "\\:")
    local_properties.write_text(f"sdk.dir={escaped}\n", encoding="utf-8")


def _run_gradle_command(
    gradlew: Path,
    project_root: Path,
    task: str,
    timeout_seconds: int,
    extra_args: list[str],
) -> subprocess.CompletedProcess:
    """
    Execute the Gradle wrapper. On Windows, `.bat` files require `shell=True`
    to run at all, but `shell=True` with a list of args mis-quotes as soon
    as the path has spaces/unicode -> build a single, explicitly quoted
    command string instead. On POSIX, pass a plain arg list (no shell).
    """
    args = [str(gradlew), "--no-daemon", task, *extra_args]

    gradle_user_home = project_root / ".gradle-user-home"
    gradle_user_home.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env["GRADLE_USER_HOME"] = str(gradle_user_home)

    if os.name == "nt":
        command: Any = " ".join(f'"{part}"' for part in args)
        use_shell = True
    else:
        command = args
        use_shell = False

    return subprocess.run(
        command,
        cwd=str(project_root),
        shell=use_shell,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
        timeout=timeout_seconds,
        check=False,
    )


def run_gradle_build(
    app_path: str | Path,
    gradle_task: str = DEFAULT_GRADLE_TASK,
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
    extra_args: Optional[list[str]] = None,
) -> CompilationResult:
    """
    Run the Android Gradle build and report whether it compiled cleanly.

    Core, reusable function: call it directly, from a test, or through
    `check_compilation()` below to plug it into the pipeline as a node.
    """
    started = time.monotonic()
    root = Path(app_path)

    if not root.exists():
        return CompilationResult(
            success=False, status="ERROR", platform="android",
            message=f"app_path does not exist: {root}",
        )

    gradlew = _find_gradle_wrapper(root)
    if gradlew is None:
        return CompilationResult(
            success=False, status="SKIPPED", platform="android",
            message=f"No Gradle wrapper (gradlew/gradlew.bat) found under {root}.",
        )

    project_root = gradlew.parent
    try:
        _ensure_executable(gradlew)
        _ensure_android_sdk_location(project_root)
        result = _run_gradle_command(
            gradlew, project_root, gradle_task, timeout_seconds, extra_args or []
        )
    except subprocess.TimeoutExpired as exc:
        duration = time.monotonic() - started
        log_path = _write_log(project_root, exc.stdout or "", exc.stderr or "", prefix="gradle_build")
        return CompilationResult(
            success=False, status="TIMEOUT", platform="android",
            message=f"Gradle build exceeded the {timeout_seconds}s timeout.",
            build_target=gradle_task, duration_seconds=duration, log_path=log_path,
            stdout_tail=(exc.stdout or "")[-LOG_TAIL_CHARS:],
            stderr_tail=(exc.stderr or "")[-LOG_TAIL_CHARS:],
        )
    except OSError as exc:
        return CompilationResult(
            success=False, status="ERROR", platform="android",
            message=f"Failed to launch Gradle wrapper: {exc}", build_target=gradle_task,
        )

    duration = time.monotonic() - started
    success = result.returncode == 0
    log_path = _write_log(project_root, result.stdout or "", result.stderr or "", prefix="gradle_build")

    return CompilationResult(
        success=success,
        status="PASSED" if success else "FAILED",
        platform="android",
        message=(
            f"`gradlew {gradle_task}` succeeded."
            if success
            else f"`gradlew {gradle_task}` failed with exit code {result.returncode}."
        ),
        build_target=gradle_task,
        return_code=result.returncode,
        duration_seconds=duration,
        log_path=log_path,
        stdout_tail=(result.stdout or "")[-LOG_TAIL_CHARS:],
        stderr_tail=(result.stderr or "")[-LOG_TAIL_CHARS:],
    )


# ─────────────────────────────────────────────────────────────────────────
# iOS — xcodebuild
# ─────────────────────────────────────────────────────────────────────────


def _find_ios_project(app_path: str | Path) -> Optional[tuple[Path, str]]:
    """
    Locate the Xcode project to build. Returns (path, "workspace"|"project").
    Prefers `.xcworkspace` (needed whenever CocoaPods/SPM are used) over a
    bare `.xcodeproj`, and looks one level deep too, mirroring
    `_find_gradle_wrapper`'s "app_path may be a parent folder" behavior.
    """
    root = Path(app_path)
    if not root.is_dir():
        return None
    search_dirs = [root] + [p for p in root.iterdir() if p.is_dir()]

    for directory in search_dirs:
        workspaces = sorted(directory.glob("*.xcworkspace"))
        if workspaces:
            return workspaces[0], "workspace"
    for directory in search_dirs:
        projects = sorted(directory.glob("*.xcodeproj"))
        if projects:
            return projects[0], "project"
    return None


def _detect_scheme(project_file: Path, project_type: str) -> Optional[str]:
    """Ask xcodebuild which schemes exist and pick the first one."""
    flag = "-workspace" if project_type == "workspace" else "-project"
    try:
        result = subprocess.run(
            ["xcodebuild", flag, str(project_file), "-list", "-json"],
            capture_output=True, text=True, timeout=60, check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None

    try:
        data = json.loads(result.stdout or "{}")
    except json.JSONDecodeError:
        return None

    container = data.get("workspace") or data.get("project") or {}
    schemes = container.get("schemes") or []
    return schemes[0] if schemes else None


def run_xcodebuild(
    app_path: str | Path,
    scheme: Optional[str] = None,
    configuration: str = DEFAULT_IOS_CONFIGURATION,
    sdk: str = DEFAULT_IOS_SDK,
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
) -> CompilationResult:
    """
    Run the iOS Xcode build and report whether it compiled cleanly.

    Builds unsigned, for the simulator SDK by default (CODE_SIGNING_ALLOWED
    /REQUIRED=NO) so the check works on a plain CI/sandbox machine without
    provisioning profiles — this is a *compilation* check, not a device
    install/run check.
    """
    started = time.monotonic()
    root = Path(app_path)

    if not root.exists():
        return CompilationResult(
            success=False, status="ERROR", platform="ios",
            message=f"app_path does not exist: {root}",
        )

    if shutil.which("xcodebuild") is None:
        return CompilationResult(
            success=False, status="SKIPPED", platform="ios",
            message="xcodebuild is not available on this machine (requires macOS + Xcode).",
        )

    found = _find_ios_project(root)
    if found is None:
        return CompilationResult(
            success=False, status="SKIPPED", platform="ios",
            message=f"No .xcworkspace/.xcodeproj found under {root}.",
        )
    project_file, project_type = found

    resolved_scheme = scheme or _detect_scheme(project_file, project_type)
    if not resolved_scheme:
        return CompilationResult(
            success=False, status="ERROR", platform="ios",
            message="Could not determine an Xcode scheme to build (pass `scheme` explicitly).",
        )

    project_root = project_file.parent
    flag = "-workspace" if project_type == "workspace" else "-project"
    args = [
        "xcodebuild",
        flag, str(project_file),
        "-scheme", resolved_scheme,
        "-configuration", configuration,
        "-sdk", sdk,
        "CODE_SIGNING_ALLOWED=NO",
        "CODE_SIGNING_REQUIRED=NO",
        "build",
    ]

    try:
        result = subprocess.run(
            args,
            cwd=str(project_root),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_seconds,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        duration = time.monotonic() - started
        log_path = _write_log(project_root, exc.stdout or "", exc.stderr or "", prefix="xcodebuild")
        return CompilationResult(
            success=False, status="TIMEOUT", platform="ios",
            message=f"xcodebuild exceeded the {timeout_seconds}s timeout.",
            build_target=resolved_scheme, duration_seconds=duration, log_path=log_path,
            stdout_tail=(exc.stdout or "")[-LOG_TAIL_CHARS:],
            stderr_tail=(exc.stderr or "")[-LOG_TAIL_CHARS:],
        )
    except OSError as exc:
        return CompilationResult(
            success=False, status="ERROR", platform="ios",
            message=f"Failed to launch xcodebuild: {exc}", build_target=resolved_scheme,
        )

    duration = time.monotonic() - started
    success = result.returncode == 0
    log_path = _write_log(project_root, result.stdout or "", result.stderr or "", prefix="xcodebuild")

    return CompilationResult(
        success=success,
        status="PASSED" if success else "FAILED",
        platform="ios",
        message=(
            f"`xcodebuild` succeeded for scheme {resolved_scheme!r}."
            if success
            else f"`xcodebuild` failed (exit code {result.returncode}) for scheme {resolved_scheme!r}."
        ),
        build_target=resolved_scheme,
        return_code=result.returncode,
        duration_seconds=duration,
        log_path=log_path,
        stdout_tail=(result.stdout or "")[-LOG_TAIL_CHARS:],
        stderr_tail=(result.stderr or "")[-LOG_TAIL_CHARS:],
    )


# ─────────────────────────────────────────────────────────────────────────
# Pipeline entry point
# ─────────────────────────────────────────────────────────────────────────


def check_compilation(state: dict[str, Any]) -> dict[str, Any]:
    """
    Pipeline node: read `state["platform"]` ("android" | "ios") + the
    project path (`state["app_path"]`, or `sandbox_path`/`source_path`/
    `project_path` if that's what an earlier node named it — see
    APP_PATH_STATE_KEYS), run the matching compilation check, and return
    the state updates + a ready-to-use audit event.

    Returns a dict meant to be merged into the pipeline state:
      - "compilation_passed": bool                 -> gate for the next node
      - "compilation_result": CompilationResult     -> full detail, incl. log_path
      - "audit_events": list[dict]                  -> feed straight into
        infra/user_interface_use_case/reports/reporter.py's audit_events list
    """
    platform = str(state.get("platform") or "").strip().lower()
    app_path = _resolve_app_path(state)
    timeout_seconds = state.get("compilation_timeout_seconds", DEFAULT_TIMEOUT_SECONDS)

    if platform == "android":
        result = run_gradle_build(
            app_path,
            gradle_task=state.get("gradle_task", DEFAULT_GRADLE_TASK),
            timeout_seconds=timeout_seconds,
        )
    elif platform == "ios":
        result = run_xcodebuild(
            app_path,
            scheme=state.get("xcode_scheme"),
            configuration=state.get("xcode_configuration", DEFAULT_IOS_CONFIGURATION),
            sdk=state.get("xcode_sdk", DEFAULT_IOS_SDK),
            timeout_seconds=timeout_seconds,
        )
    else:
        result = CompilationResult(
            success=False,
            status="ERROR",
            platform=platform or "unknown",
            message=f"Unsupported platform for compilation check: {platform!r} (expected 'android' or 'ios').",
        )

    return {
        "compilation_passed": result.success,
        "compilation_result": result,
        "audit_events": [result.to_audit_event()],
    }
