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
import platform
import re
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
ERROR_EXCERPT_CHARS = 4000

# Matches the actual diagnostic lines that explain *why* a build failed:
# clang/swift "<file>:<line>:<col>: error: ..." and Gradle's "FAILURE:" /
# "e: <file>: ..." compiler-error lines / "* What went wrong" summary.
_ERROR_LINE_RE = re.compile(r"error:|FAILURE:|^\* What went wrong", re.IGNORECASE)

# Fixed, dedicated cache dir shared across all sandbox runs. Only the Gradle
# distribution/dependency cache lives here — project source files stay inside
# each sandbox, so run isolation is unaffected. Without this, every sandbox
# re-downloaded the full Gradle distribution + deps from scratch.
# Override with the GRADLE_USER_HOME env var (e.g. in .env) if needed.
#
# The bare Windows path used to be the only fallback -- but on macOS/Linux,
# pathlib has no concept of a "C:\" drive or backslash separators, so
# `Path(r"C:\Shared_CI_Cache\.gradle-user-home")` isn't an absolute path at
# all: it's a single relative path *segment* (literally containing colons
# and backslashes as characters), created under whatever the process's cwd
# happens to be. The cache dir "worked" in the sense that mkdir(parents=True)
# never failed, but it silently defeated the whole point of this constant on
# any non-Windows machine.
_DEFAULT_GRADLE_USER_HOME = (
    r"C:\Shared_CI_Cache\.gradle-user-home"
    if os.name == "nt"
    else str(Path.home() / ".shared_ci_cache" / "gradle-user-home")
)
SHARED_GRADLE_USER_HOME = os.environ.get("GRADLE_USER_HOME") or _DEFAULT_GRADLE_USER_HOME

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
    error_excerpt: str = ""
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


def _extract_error_excerpt(*texts: str, max_chars: int = ERROR_EXCERPT_CHARS) -> str:
    """
    Pull out just the lines that actually explain a build failure (compiler
    "error:" diagnostics, Gradle "FAILURE:"/"What went wrong" lines), in
    original order, deduplicated -- from the full stdout/stderr, not just
    their tail.

    `xcodebuild` (without `-quiet`) echoes every clang invocation as one
    giant, often >4000-char, raw command-line dump per source file. A plain
    `text[-LOG_TAIL_CHARS:]` slice can land entirely inside one such dump
    and never show the actual "error:" line that caused the failure, even
    though it's sitting right there earlier in the very same output.
    Scanning line-by-line for the diagnostic markers finds it regardless of
    how much unrelated verbose output surrounds it.
    """
    seen: set[str] = set()
    matches: list[str] = []
    for text in texts:
        for line in (text or "").splitlines():
            stripped = line.strip()
            if stripped and _ERROR_LINE_RE.search(stripped) and stripped not in seen:
                seen.add(stripped)
                matches.append(stripped)
    excerpt = "\n".join(matches)
    return excerpt[-max_chars:] if excerpt else ""


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


def _find_android_sdk_root() -> Optional[str]:
    """
    Locate the Android SDK root directory:
    1. ANDROID_HOME / ANDROID_SDK_ROOT, if already set and pointing at a
       real directory.
    2. Common default install locations per Operating System (matches
       infra/agents/sdkAgent/tools/emulator.py's `_find_android_sdk_root`,
       and what CONFIG.md documents as the guessed fallback).
    Returns None if no valid SDK root could be found either way.
    """
    for var in ("ANDROID_HOME", "ANDROID_SDK_ROOT"):
        path = os.environ.get(var)
        if path and os.path.isdir(path):
            return path

    system = platform.system()
    if system == "Windows":
        candidate = os.path.join(os.environ.get("LOCALAPPDATA", ""), "Android", "Sdk")
    elif system == "Darwin":
        candidate = os.path.expanduser("~/Library/Android/sdk")
    else:
        candidate = os.path.expanduser("~/Android/Sdk")

    return candidate if os.path.isdir(candidate) else None


def _find_java_home() -> Optional[str]:
    """
    Locate a real JDK's home directory (the folder containing `bin/java`):
    1. JAVA_HOME, if already set and valid.
    2. `/usr/libexec/java_home` (macOS's own JDK locator) — the correct
       answer whenever a "properly installed" JDK (Oracle/Adoptium/etc, or
       a Homebrew *cask*) registered itself with it.
    3. Common install locations that `java_home` does NOT know about:
       Homebrew's own `openjdk` *formula* (as opposed to a cask) is
       keg-only by design and never symlinks itself into
       `/Library/Java/JavaVirtualMachines` or onto PATH, so a plain
       `openjdk`/`openjdk@<N>` install is invisible to both `java_home`
       and a bare `java` on PATH — which then resolves to macOS's stub
       `/usr/bin/java` that only prints "Unable to locate a Java Runtime"
       instead of running anything.
    Returns None if no valid JDK could be found either way — gradlew then
    fails with its own clear error, same fallback pattern as
    `_find_android_sdk_root`.
    """
    java_home = os.environ.get("JAVA_HOME")
    if java_home and os.path.isfile(os.path.join(java_home, "bin", "java")):
        return java_home

    if platform.system() == "Darwin":
        try:
            result = subprocess.run(
                ["/usr/libexec/java_home"],
                capture_output=True, text=True, timeout=10, check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            result = None
        if result is not None and result.returncode == 0:
            candidate = result.stdout.strip()
            if candidate and os.path.isfile(os.path.join(candidate, "bin", "java")):
                return candidate

        for brew_prefix in ("/opt/homebrew", "/usr/local"):
            opt_dir = Path(brew_prefix) / "opt"
            if not opt_dir.is_dir():
                continue
            for candidate_dir in sorted(opt_dir.glob("openjdk*"), reverse=True):
                home = candidate_dir / "libexec" / "openjdk.jdk" / "Contents" / "Home"
                if (home / "bin" / "java").is_file():
                    return str(home)

        jvm_dir = Path("/Library/Java/JavaVirtualMachines")
        if jvm_dir.is_dir():
            for candidate_dir in sorted(jvm_dir.glob("*"), reverse=True):
                home = candidate_dir / "Contents" / "Home"
                if (home / "bin" / "java").is_file():
                    return str(home)
    elif platform.system() != "Windows":
        jvm_dir = Path("/usr/lib/jvm")
        if jvm_dir.is_dir():
            for candidate_dir in sorted(jvm_dir.glob("*"), reverse=True):
                if (candidate_dir / "bin" / "java").is_file():
                    return str(candidate_dir)

    return None


def _find_built_apk(project_root: Path, task: str) -> Optional[str]:
    """
    Locate the APK produced by `task` under the standard Gradle output
    layout `<module>/build/outputs/apk/<buildType>/*.apk`. Prefers a path
    matching the build type implied by `task` (e.g. "assembleDebug" ->
    "debug"), falling back to the most recently modified APK found anywhere
    under build/outputs/apk if nothing matches (custom task names/flavors).

    Without this, the pipeline built an APK that was never installed on the
    emulator: emulator_node only ever called `driver.activate_app(...)` on
    a package that didn't exist on the freshly-booted device, leaving the
    emulator sitting on its home screen instead of showing the app.
    """
    candidates = sorted(
        project_root.glob("*/build/outputs/apk/**/*.apk"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    if not candidates:
        return None

    build_type = task.lower().removeprefix("assemble") or "debug"
    for apk in candidates:
        if build_type in apk.parent.as_posix().lower():
            return str(apk)
    return str(candidates[0])


def _ensure_android_sdk_location(project_root: Path) -> None:
    """
    Make sure Gradle can find the Android SDK. `local.properties` is always
    git-ignored, so a freshly cloned/copied sandbox never has it; if it's
    missing, write `sdk.dir` from ANDROID_HOME / ANDROID_SDK_ROOT — or, if
    neither is set, from the OS's common default SDK install location — so
    the build doesn't fail with "SDK location not found". Leaves
    `local.properties` unwritten (and the Gradle build to fail with its own
    clear error) only when no SDK install could be found at all.
    """
    local_properties = project_root / "local.properties"
    if local_properties.exists():
        return

    sdk_dir = _find_android_sdk_root()
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

    Path(SHARED_GRADLE_USER_HOME).mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env["GRADLE_USER_HOME"] = SHARED_GRADLE_USER_HOME

    # gradlew shells out to a plain `java` on PATH. A GUI-launched process
    # (e.g. the IDE's debugger) doesn't inherit JAVA_HOME/PATH tweaks from
    # ~/.zshrc the way a login shell does, so even a perfectly good JDK
    # install can be invisible here -- `java` then resolves to macOS's own
    # stub at /usr/bin/java, which fails immediately with "Unable to locate
    # a Java Runtime" instead of ever reaching Gradle.
    if not (env.get("JAVA_HOME") and os.path.isfile(os.path.join(env["JAVA_HOME"], "bin", "java"))):
        java_home = _find_java_home()
        if java_home:
            env["JAVA_HOME"] = java_home
            env["PATH"] = os.path.join(java_home, "bin") + os.pathsep + env.get("PATH", "")

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
            error_excerpt=_extract_error_excerpt(exc.stdout, exc.stderr),
        )
    except OSError as exc:
        return CompilationResult(
            status="ERROR", platform="android", scheme=gradle_task,
            detail=f"Failed to launch Gradle wrapper: {exc}",
        )

    duration = time.monotonic() - started
    success = result.returncode == 0
    log_path = _write_log(log_dir, result.stdout or "", result.stderr or "", prefix="gradle_build")

    extra: dict[str, Any] = {}
    if success:
        apk_path = _find_built_apk(project_root, gradle_task)
        if apk_path:
            extra["apk_path"] = apk_path

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
        error_excerpt="" if success else _extract_error_excerpt(result.stdout, result.stderr),
        extra=extra,
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


def _find_built_app_bundle(derived_data_path: Path, configuration: str, sdk: str) -> Optional[str]:
    """
    Locate the .app bundle xcodebuild just produced, at the deterministic
    `-derivedDataPath` location: `<derivedDataPath>/Build/Products/<configuration>-<sdk>/*.app`.
    Falls back to the most recently modified .app anywhere under
    `<derivedDataPath>/Build/Products` if that exact folder doesn't match
    (e.g. a device SDK instead of a simulator one).
    """
    products_dir = derived_data_path / "Build" / "Products"
    exact_dir = products_dir / f"{configuration}-{sdk}"
    if exact_dir.is_dir():
        bundles = sorted(exact_dir.glob("*.app"))
        if bundles:
            return str(bundles[0])

    if not products_dir.is_dir():
        return None
    candidates = sorted(
        products_dir.glob("*/*.app"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    return str(candidates[0]) if candidates else None


def _ensure_cocoapods_installed(project_root: Path) -> Optional[str]:
    """Run ``pod install`` when Podfile declares pods but ``Pods/`` is missing."""
    from infra.agents.sdkAgent.tools.sdk_project_tools import (
        _PODFILE_POD_LINE_RE,
        find_podfile_directory,
        run_pod_install_command,
    )

    pod_dir = find_podfile_directory(project_root)
    if pod_dir is None:
        return None

    podfile = pod_dir / "Podfile"
    if not podfile.is_file():
        return None

    if not _PODFILE_POD_LINE_RE.search(podfile.read_text(encoding="utf-8")):
        return None

    if (pod_dir / "Pods" / "Manifest.lock").is_file():
        return None

    outcome = run_pod_install_command(pod_dir)
    if outcome.get("status") == "OK":
        return None
    return outcome.get("reason") or outcome.get("error") or "pod install failed"


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

    pod_error = _ensure_cocoapods_installed(root)
    if pod_error:
        return CompilationResult(
            status="FAILED",
            platform="ios",
            detail=f"CocoaPods install failed before build: {pod_error}",
        )

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
    # Pinning -derivedDataPath inside the sandbox (instead of the default,
    # shared ~/Library/Developer/Xcode/DerivedData/<hash>/) makes the built
    # .app's location deterministic, so it can be found afterwards and
    # installed on a simulator — see _find_built_app_bundle below.
    derived_data_path = root / "DerivedData"
    command = [
        "xcodebuild",
        flag, str(target),
        "-scheme", resolved_scheme,
        "-configuration", configuration,
        "-sdk", sdk,
        "-derivedDataPath", str(derived_data_path),
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
            stdout_tail=(exc.stdout or "")[-LOG_TAIL_CHARS:],
            stderr_tail=(exc.stderr or "")[-LOG_TAIL_CHARS:],
            error_excerpt=_extract_error_excerpt(exc.stdout, exc.stderr),
        )
    except OSError as exc:
        return CompilationResult(
            status="ERROR", platform="ios", scheme=resolved_scheme,
            detail=f"Failed to launch xcodebuild: {exc}",
        )

    duration = time.monotonic() - started
    success = result.returncode == 0
    log_path = _write_log(log_dir, result.stdout or "", result.stderr or "", prefix="xcodebuild")

    extra: dict[str, Any] = {}
    if success:
        app_bundle_path = _find_built_app_bundle(derived_data_path, configuration, sdk)
        if app_bundle_path:
            extra["app_bundle_path"] = app_bundle_path

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
        error_excerpt="" if success else _extract_error_excerpt(result.stdout, result.stderr),
        extra=extra,
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
