"""
Compilation Agent — verifies that the app still compiles after the SDK
installation step (sdkAgent) has modified its project files.

Architecture: two deterministic build runners, no LLM involved anywhere:
  * `run_gradle_build`  -> Android, shells out to the project's `gradlew`.
  * `run_xcodebuild`    -> iOS, shells out to `xcodebuild`.
Both return the same `CompilationResult` shape, so the rest of the
pipeline doesn't need to care which platform actually ran.

`check_compilation(state)` is the pipeline entry point: it reads
`state["platform"]` ("android" | "ios", defaults to "ios" if absent) and
dispatches to the matching runner. Runs after `sdkAgent` applies the SDK
changes and before the runtime verification step (`answer_agent` /
verify_sdk log checks).

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
import stat
import subprocess
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

DEFAULT_GRADLE_TASK = "assembleDebug"
DEFAULT_IOS_CONFIGURATION = "Debug"
DEFAULT_IOS_SDK = "iphonesimulator"
DEFAULT_TIMEOUT_SECONDS = 900  # 15 min: a first/clean build downloads the whole toolchain + deps
LOG_TAIL_CHARS = 4000

# Repo-root shared Gradle cache. Per-sandbox GRADLE_USER_HOME forced a full
# Gradle distribution download on every run; one cache serves all sandboxes.
_PROJECT_ROOT = Path(__file__).resolve().parents[3]
_SHARED_GRADLE_USER_HOME = _PROJECT_ROOT / ".cache" / "gradle"

# State keys checked (in order) to find the project to compile. Kept
# flexible on purpose: the pre-build pipeline state is a plain dict (not
# the pydantic UseCaseContract yet), and different teams/stages may name
# the working-copy path differently.
APP_PATH_STATE_KEYS = ("sandbox_dir", "sandbox_path", "app_path", "source_path", "project_path")


@dataclass
class CompilationResult:
    """Structured, platform-agnostic outcome of a single compilation check run."""

    status: str  # "PASSED" | "FAILED" | "TIMEOUT" | "SKIPPED" | "ERROR"
    platform: str
    return_code: Optional[int] = None
    duration_seconds: float = 0.0
    log_path: Optional[str] = None
    scheme: Optional[str] = None  # Xcode scheme (ios) or Gradle task name (android)
    detail: str = ""
    stdout_tail: str = ""
    stderr_tail: str = ""
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def success(self) -> bool:
        return self.status == "PASSED"

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
            "details": self.detail,
            "timestamp": datetime.utcnow().isoformat() + "Z",
        }


def _resolve_app_path(state: dict[str, Any]) -> str:
    for key in APP_PATH_STATE_KEYS:
        value = state.get(key)
        if value:
            return str(value)
    return ""


def _write_log(log_dir: str | Path, stdout: str, stderr: str, prefix: str) -> str:
    log_dir = Path(log_dir)
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

    Real-world repos routinely commit *both* `gradlew` and `gradlew.bat`
    (cross-platform support) — the OS-appropriate one must be preferred,
    or we'd try to execute a Windows `.bat` file on macOS/Linux (and fail
    with "Exec format error") even though the correct POSIX wrapper sits
    right next to it.
    """
    names = ("gradlew.bat", "gradlew") if os.name == "nt" else ("gradlew", "gradlew.bat")
    root = Path(app_path)
    candidates = [root] + ([p for p in root.iterdir() if p.is_dir()] if root.is_dir() else [])

    for candidate in candidates:
        for name in names:
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

    # Prefer an explicit GRADLE_USER_HOME from the environment; otherwise use
    # the shared repo cache so wrapper dists / deps are downloaded once.
    env = os.environ.copy()
    gradle_user_home = Path(
        env.get("GRADLE_USER_HOME") or _SHARED_GRADLE_USER_HOME
    )
    gradle_user_home.mkdir(parents=True, exist_ok=True)
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
            status="ERROR", platform="android",
            detail=f"app_path does not exist: {root}",
        )

    gradlew = _find_gradle_wrapper(root)
    if gradlew is None:
        return CompilationResult(
            status="SKIPPED", platform="android",
            detail=f"No Gradle wrapper (gradlew/gradlew.bat) found under {root}.",
        )

    project_root = gradlew.parent
    log_dir = project_root / "compilation-logs"
    try:
        _ensure_executable(gradlew)
        _ensure_android_sdk_location(project_root)
        result = _run_gradle_command(
            gradlew, project_root, gradle_task, timeout_seconds, extra_args or []
        )
    except subprocess.TimeoutExpired as exc:
        duration = time.monotonic() - started
        log_path = _write_log(log_dir, exc.stdout or "", exc.stderr or "", prefix="gradle_build")
        return CompilationResult(
            status="TIMEOUT", platform="android", scheme=gradle_task,
            duration_seconds=duration, log_path=log_path,
            detail=f"Gradle build exceeded the {timeout_seconds}s timeout.",
            stdout_tail=(exc.stdout or "")[-LOG_TAIL_CHARS:],
            stderr_tail=(exc.stderr or "")[-LOG_TAIL_CHARS:],
        )
    except OSError as exc:
        return CompilationResult(
            status="ERROR", platform="android", scheme=gradle_task,
            detail=f"Failed to launch Gradle wrapper: {exc}",
        )

    duration = time.monotonic() - started
    success = result.returncode == 0
    log_path = _write_log(log_dir, result.stdout or "", result.stderr or "", prefix="gradle_build")

    return CompilationResult(
        status="PASSED" if success else "FAILED",
        platform="android",
        scheme=gradle_task,
        return_code=result.returncode,
        duration_seconds=duration,
        log_path=log_path,
        detail=(
            f"`gradlew {gradle_task}` succeeded."
            if success
            else f"`gradlew {gradle_task}` failed with exit code {result.returncode}."
        ),
        stdout_tail=(result.stdout or "")[-LOG_TAIL_CHARS:],
        stderr_tail=(result.stderr or "")[-LOG_TAIL_CHARS:],
    )


# ─────────────────────────────────────────────────────────────────────────
# iOS — xcodebuild
# ─────────────────────────────────────────────────────────────────────────


def _find_ios_project(app_path: str | Path) -> Dict[str, Optional[Path]]:
    """
    Locate the Xcode project to build directly under `app_path` (top level
    only — a `.xcworkspace` nested inside a `.xcodeproj`, e.g. CocoaPods'
    `project.xcworkspace`, must NOT be picked up).

    Returns {"workspace": Path|None, "project": Path|None} so the caller can
    prefer the workspace (needed whenever CocoaPods/SPM are used) while
    still falling back to a bare `.xcodeproj`.
    """
    root = Path(app_path)
    if not root.is_dir():
        return {"workspace": None, "project": None}

    workspaces = sorted(root.glob("*.xcworkspace"))
    projects = sorted(root.glob("*.xcodeproj"))
    return {
        "workspace": workspaces[0] if workspaces else None,
        "project": projects[0] if projects else None,
    }


def _detect_scheme(workspace: Optional[Path], project: Optional[Path]) -> Optional[str]:
    """
    Ask xcodebuild which schemes exist and pick the best one: prefer a
    scheme whose name matches the workspace/project name exactly, else the
    first scheme that isn't a CocoaPods-generated `Pods-*` scheme (those
    can't be built standalone), else whatever is left.
    """
    target = workspace or project
    if target is None:
        return None

    flag = "-workspace" if workspace is not None else "-project"
    try:
        result = subprocess.run(
            ["xcodebuild", flag, str(target), "-list", "-json"],
            capture_output=True,
            text=True,
            timeout=60,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None

    if result.returncode != 0:
        return None

    try:
        data = json.loads(result.stdout or "{}")
    except json.JSONDecodeError:
        return None

    container = data.get("workspace") or data.get("project") or {}
    schemes = container.get("schemes") or []
    if not schemes:
        return None

    name = container.get("name")
    if name and name in schemes:
        return name

    non_pods_schemes = [s for s in schemes if not s.lower().startswith("pods-")]
    return (non_pods_schemes or schemes)[0]


def run_xcodebuild(
    app_path: str | Path,
    log_dir: str | Path,
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

    located = _find_ios_project(root)
    workspace, project = located["workspace"], located["project"]
    if workspace is None and project is None:
        return CompilationResult(
            status="ERROR", platform="ios",
            detail=f"No .xcworkspace/.xcodeproj found under {root}.",
        )

    resolved_scheme = scheme or _detect_scheme(workspace, project)
    if not resolved_scheme:
        return CompilationResult(
            status="ERROR", platform="ios",
            detail="Could not determine an Xcode scheme to build (pass `scheme` explicitly).",
        )

    target = workspace or project
    flag = "-workspace" if workspace is not None else "-project"
    command = [
        "xcodebuild",
        flag, str(target),
        "-scheme", resolved_scheme,
        "-configuration", configuration,
        "-sdk", sdk,
        "CODE_SIGNING_ALLOWED=NO",
        "CODE_SIGNING_REQUIRED=NO",
        "build",
    ]

    try:
        result = subprocess.run(
            command,
            cwd=str(target.parent),
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired as exc:
        duration = time.monotonic() - started
        log_path = _write_log(log_dir, exc.stdout or "", exc.stderr or "", prefix="xcodebuild")
        return CompilationResult(
            status="TIMEOUT", platform="ios", scheme=resolved_scheme,
            duration_seconds=duration, log_path=log_path,
            detail=f"xcodebuild exceeded the {timeout_seconds}s timeout.",
        )
    except OSError as exc:
        return CompilationResult(
            status="ERROR", platform="ios", scheme=resolved_scheme,
            detail=f"Failed to launch xcodebuild: {exc}",
        )

    duration = time.monotonic() - started
    success = result.returncode == 0
    log_path = _write_log(log_dir, result.stdout or "", result.stderr or "", prefix="xcodebuild")

    return CompilationResult(
        status="PASSED" if success else "FAILED",
        platform="ios",
        scheme=resolved_scheme,
        return_code=result.returncode,
        duration_seconds=duration,
        log_path=log_path,
        detail=(
            f"`xcodebuild` succeeded for scheme {resolved_scheme!r}."
            if success
            else f"`xcodebuild` failed (exit code {result.returncode}) for scheme {resolved_scheme!r}."
        ),
        stdout_tail=(result.stdout or "")[-LOG_TAIL_CHARS:],
        stderr_tail=(result.stderr or "")[-LOG_TAIL_CHARS:],
    )


# ─────────────────────────────────────────────────────────────────────────
# Pipeline entry point
# ─────────────────────────────────────────────────────────────────────────


def check_compilation(state: dict[str, Any]) -> dict[str, Any]:
    """
    Pipeline node: read `state["platform"]` ("android" | "ios", defaults to
    "ios" if the key is absent) + the project path (`state["sandbox_dir"]`,
    or `sandbox_path`/`app_path`/`source_path`/`project_path` — see
    APP_PATH_STATE_KEYS), run the matching compilation check, and return
    the state updates + a ready-to-use audit event.

    Returns a dict meant to be merged into the pipeline state:
      - "compilation_passed": bool                 -> gate for the next node
      - "compilation_result": CompilationResult     -> full detail, incl. log_path
      - "audit_events": list[dict]                  -> feed straight into
        infra/user_interface_use_case/reports/reporter.py's audit_events list
    """
    platform = str(state.get("platform") or "ios").strip().lower()
    project_path = _resolve_app_path(state)
    timeout_seconds = state.get("compilation_timeout_seconds", DEFAULT_TIMEOUT_SECONDS)

    if platform == "ios":
        if not project_path:
            result = CompilationResult(
                status="ERROR", platform="ios",
                detail="No project path found in state (expected 'sandbox_dir').",
            )
        else:
            run_dir = state.get("run_dir") or project_path
            log_dir = Path(run_dir) / "compilation-logs"
            result = run_xcodebuild(
                project_path,
                log_dir=log_dir,
                scheme=state.get("xcode_scheme"),
                configuration=state.get("xcode_configuration", DEFAULT_IOS_CONFIGURATION),
                sdk=state.get("xcode_sdk", DEFAULT_IOS_SDK),
                timeout_seconds=timeout_seconds,
            )
    elif platform == "android":
        result = run_gradle_build(
            project_path,
            gradle_task=state.get("gradle_task", DEFAULT_GRADLE_TASK),
            timeout_seconds=timeout_seconds,
        )
    else:
        result = CompilationResult(
            status="ERROR",
            platform=platform,
            detail=f"Unsupported platform for compilation check: {platform!r} (expected 'android' or 'ios').",
        )

    return {
        "compilation_passed": result.success,
        "compilation_result": result,
        "audit_events": [result.to_audit_event()],
    }
