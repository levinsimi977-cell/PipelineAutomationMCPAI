"""
Tests for compilation_agent.py.

iOS tests do NOT invoke the real xcodebuild — they build a minimal fake
.xcworkspace/.xcodeproj folder structure and monkeypatch subprocess.run so
`xcodebuild -list` / `xcodebuild ... build` behave the way a real success or
failure run would, without needing Xcode installed on the test machine.

Android tests use a tiny fake `gradlew` shell script instead of a real
Gradle wrapper, for the same reason (no Android SDK required to run them).
"""

from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict

import pytest

# compilation_agent.py has no package (`__init__.py`) around it, same as the
# repo's other agent folders (answerAgent, sdkAgent, ...) — make it
# importable regardless of which directory pytest is invoked from.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import compilation_agent as ca

POSIX_ONLY = pytest.mark.skipif(os.name == "nt", reason="fake gradlew/xcodebuild are POSIX shell scripts")


def _make_project(tmp_path: Path, use_workspace: bool = True) -> Path:
    project_root = tmp_path / "sandbox_env" / "ncp10" / "swift" / "basic_app"
    project_root.mkdir(parents=True)

    (project_root / "basic_app.xcodeproj").mkdir()
    if use_workspace:
        (project_root / "basic_app.xcworkspace").mkdir()
        # Nested workspace inside the xcodeproj should be ignored.
        (project_root / "basic_app.xcodeproj" / "project.xcworkspace").mkdir()

    return project_root


class FakeCompletedProcess:
    def __init__(self, returncode: int, stdout: str = "", stderr: str = ""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


# ── _extract_error_snippet ────────────────────────────────────────────────


def test_extract_error_excerpt_finds_compiler_error_line():
    """The excerpt includes 1 line of context around the match (default
    context_lines=1), but must not pull in unrelated noise sitting further
    away in a long, verbose build log."""
    stdout = (
        "note: some unrelated earlier build step\n"
        "note: building target\n"
        "AppDelegate.m:23:5: error: assignment to readonly property 'appleAppID'\n"
        "note: done\n"
        "note: some unrelated later build step\n"
    )

    excerpt = ca._extract_error_snippet(stdout, "")

    assert "error: assignment to readonly property 'appleAppID'" in excerpt
    assert "note: some unrelated earlier build step" not in excerpt
    assert "note: some unrelated later build step" not in excerpt


def test_extract_error_excerpt_dedupes_repeated_lines():
    stdout = "x.m:1:1: error: boom\n" * 3

    excerpt = ca._extract_error_snippet(stdout, "")

    assert excerpt.count("error: boom") == 1


def test_extract_error_excerpt_scans_full_text_not_just_the_tail():
    """
    Regression: xcodebuild (without -quiet) echoes each clang invocation as
    one giant raw command-line dump per source file, easily >LOG_TAIL_CHARS
    on its own. A plain `text[-LOG_TAIL_CHARS:]` slice can land entirely
    inside one such dump and never surface the actual "error:" line that
    caused the failure, even though it appears earlier in the very same
    stdout. _extract_error_excerpt must find it regardless of where in the
    text it sits or how much noise follows it.
    """
    huge_trailing_noise = "clang -cc1 " + ("-Wno-something " * 2000)  # far over LOG_TAIL_CHARS
    stdout = (
        "AppDelegate.m:23:5: error: assignment to readonly property 'appleAppID'\n"
        + huge_trailing_noise
    )
    assert "error:" not in stdout[-ca.LOG_TAIL_CHARS:]  # sanity check the setup actually reproduces the bug

    excerpt = ca._extract_error_snippet(stdout, "")

    assert "error: assignment to readonly property 'appleAppID'" in excerpt


def test_extract_error_excerpt_matches_gradle_failure_marker():
    stderr = "> Task :app:compileDebugJavaWithJavac FAILED\n\nFAILURE: Build failed with an exception.\n"

    excerpt = ca._extract_error_snippet("", stderr)

    assert "FAILURE: Build failed with an exception." in excerpt


def test_extract_error_excerpt_empty_when_no_error_markers():
    assert ca._extract_error_snippet("BUILD SUCCEEDED", "") == ""


# ── iOS: _find_ios_project ──────────────────────────────────────────────


def test_find_ios_project_prefers_workspace(tmp_path):
    project_root = _make_project(tmp_path, use_workspace=True)

    located = ca._find_ios_project(project_root)

    assert located["workspace"] is not None
    assert located["workspace"].name == "basic_app.xcworkspace"
    assert located["project"] is not None
    assert located["project"].name == "basic_app.xcodeproj"


def test_find_ios_project_ignores_nested_workspace(tmp_path):
    project_root = _make_project(tmp_path, use_workspace=True)

    located = ca._find_ios_project(project_root)

    # Only the top-level workspace should be picked, not project.xcworkspace.
    assert located["workspace"].parent == project_root


def test_find_ios_project_falls_back_to_xcodeproj(tmp_path):
    project_root = _make_project(tmp_path, use_workspace=False)

    located = ca._find_ios_project(project_root)

    assert located["workspace"] is None
    assert located["project"] is not None


def test_find_ios_project_missing_returns_none(tmp_path):
    missing_root = tmp_path / "does_not_exist"

    located = ca._find_ios_project(missing_root)

    assert located["workspace"] is None
    assert located["project"] is None


# ── iOS: _detect_scheme ─────────────────────────────────────────────────


def test_detect_scheme_prefers_matching_name(monkeypatch, tmp_path):
    project_root = _make_project(tmp_path, use_workspace=True)
    workspace = project_root / "basic_app.xcworkspace"

    def fake_run(args, capture_output, text, timeout):
        assert "-list" in args
        payload = {"workspace": {"name": "basic_app", "schemes": [
            "AppsFlyerFramework", "basic_app", "Pods-basic_app",
        ]}}
        return FakeCompletedProcess(0, stdout=json.dumps(payload))

    monkeypatch.setattr(ca.subprocess, "run", fake_run)

    scheme = ca._detect_scheme(workspace, None)

    assert scheme == "basic_app"


def test_detect_scheme_skips_pods_scheme_when_no_exact_match(monkeypatch, tmp_path):
    project_root = _make_project(tmp_path, use_workspace=True)
    workspace = project_root / "basic_app.xcworkspace"

    def fake_run(args, capture_output, text, timeout):
        payload = {"workspace": {"name": "other", "schemes": [
            "Pods-basic_app", "MyRealScheme",
        ]}}
        return FakeCompletedProcess(0, stdout=json.dumps(payload))

    monkeypatch.setattr(ca.subprocess, "run", fake_run)

    scheme = ca._detect_scheme(workspace, None)

    assert scheme == "MyRealScheme"


def test_detect_scheme_returns_none_when_list_fails(monkeypatch, tmp_path):
    project_root = _make_project(tmp_path, use_workspace=True)
    workspace = project_root / "basic_app.xcworkspace"

    def fake_run(args, capture_output, text, timeout):
        return FakeCompletedProcess(1, stdout="", stderr="boom")

    monkeypatch.setattr(ca.subprocess, "run", fake_run)

    assert ca._detect_scheme(workspace, None) is None


# ── iOS: run_xcodebuild ─────────────────────────────────────────────────


def test_run_xcodebuild_success(monkeypatch, tmp_path):
    project_root = _make_project(tmp_path, use_workspace=True)
    log_dir = tmp_path / "run_dir" / "compilation-logs"

    monkeypatch.setattr(ca, "_detect_scheme", lambda workspace, project: "basic_app")

    def fake_run(command, cwd, capture_output, text, timeout):
        assert "build" in command
        assert "-scheme" in command and "basic_app" in command
        assert "-derivedDataPath" in command
        return FakeCompletedProcess(0, stdout="** BUILD SUCCEEDED **", stderr="")

    monkeypatch.setattr(ca.subprocess, "run", fake_run)

    result = ca.run_xcodebuild(str(project_root), log_dir=log_dir)

    assert result.status == "PASSED"
    assert result.return_code == 0
    assert result.scheme == "basic_app"
    assert result.log_path is not None
    assert Path(result.log_path).exists()
    assert "BUILD SUCCEEDED" in Path(result.log_path).read_text()
    assert result.error_excerpt == ""


# ── iOS: built .app bundle discovery (_find_built_app_bundle) ────────────


def test_find_built_app_bundle_at_exact_configuration_sdk_dir(tmp_path: Path):
    derived_data = tmp_path / "DerivedData"
    products_dir = derived_data / "Build" / "Products" / "Debug-iphonesimulator"
    products_dir.mkdir(parents=True)
    app_bundle = products_dir / "basic_app.app"
    app_bundle.mkdir()

    found = ca._find_built_app_bundle(derived_data, "Debug", "iphonesimulator")

    assert found == str(app_bundle)


def test_find_built_app_bundle_falls_back_to_most_recent(tmp_path: Path):
    derived_data = tmp_path / "DerivedData"
    other_dir = derived_data / "Build" / "Products" / "Release-iphoneos"
    other_dir.mkdir(parents=True)
    app_bundle = other_dir / "basic_app.app"
    app_bundle.mkdir()

    found = ca._find_built_app_bundle(derived_data, "Debug", "iphonesimulator")

    assert found == str(app_bundle)


def test_find_built_app_bundle_returns_none_when_nothing_built(tmp_path: Path):
    assert ca._find_built_app_bundle(tmp_path / "DerivedData", "Debug", "iphonesimulator") is None


def test_run_xcodebuild_success_populates_app_bundle_path(monkeypatch, tmp_path):
    project_root = _make_project(tmp_path, use_workspace=True)
    log_dir = tmp_path / "run_dir" / "compilation-logs"

    monkeypatch.setattr(ca, "_detect_scheme", lambda workspace, project: "basic_app")

    def fake_run(command, cwd, capture_output, text, timeout):
        # Simulate xcodebuild actually producing the .app at -derivedDataPath.
        derived_data_index = command.index("-derivedDataPath") + 1
        derived_data_path = Path(command[derived_data_index])
        products_dir = derived_data_path / "Build" / "Products" / "Debug-iphonesimulator"
        products_dir.mkdir(parents=True)
        (products_dir / "basic_app.app").mkdir()
        return FakeCompletedProcess(0, stdout="** BUILD SUCCEEDED **", stderr="")

    monkeypatch.setattr(ca.subprocess, "run", fake_run)

    result = ca.run_xcodebuild(str(project_root), log_dir=log_dir)

    assert result.extra["app_bundle_path"].endswith("basic_app.app")


def test_run_xcodebuild_failure(monkeypatch, tmp_path):
    project_root = _make_project(tmp_path, use_workspace=True)
    log_dir = tmp_path / "run_dir" / "compilation-logs"

    monkeypatch.setattr(ca, "_detect_scheme", lambda workspace, project: "basic_app")

    def fake_run(command, cwd, capture_output, text, timeout):
        return FakeCompletedProcess(65, stdout="", stderr="error: cannot find 'AppsFlyerLib' in scope")

    monkeypatch.setattr(ca.subprocess, "run", fake_run)

    result = ca.run_xcodebuild(str(project_root), log_dir=log_dir)

    assert result.status == "FAILED"
    assert result.return_code == 65
    assert "AppsFlyerLib" in Path(result.log_path).read_text()
    assert "error: cannot find 'AppsFlyerLib' in scope" in result.error_excerpt


def test_run_xcodebuild_missing_project(tmp_path):
    missing_root = tmp_path / "nowhere"
    log_dir = tmp_path / "logs"

    result = ca.run_xcodebuild(str(missing_root), log_dir=log_dir)

    assert result.status == "ERROR"
    assert "No .xcworkspace" in result.detail


def test_run_xcodebuild_scheme_detection_fails(monkeypatch, tmp_path):
    project_root = _make_project(tmp_path, use_workspace=True)
    log_dir = tmp_path / "logs"

    monkeypatch.setattr(ca, "_detect_scheme", lambda workspace, project: None)

    result = ca.run_xcodebuild(str(project_root), log_dir=log_dir)

    assert result.status == "ERROR"
    assert "scheme" in result.detail.lower()


def test_run_xcodebuild_tool_not_installed(monkeypatch, tmp_path):
    project_root = _make_project(tmp_path, use_workspace=True)
    log_dir = tmp_path / "logs"

    monkeypatch.setattr(ca, "_detect_scheme", lambda workspace, project: "basic_app")

    def fake_run(command, cwd, capture_output, text, timeout):
        raise FileNotFoundError("xcodebuild not found")

    monkeypatch.setattr(ca.subprocess, "run", fake_run)

    result = ca.run_xcodebuild(str(project_root), log_dir=log_dir)

    assert result.status == "ERROR"
    assert "not found" in result.detail.lower()


def test_run_xcodebuild_timeout(monkeypatch, tmp_path):
    project_root = _make_project(tmp_path, use_workspace=True)
    log_dir = tmp_path / "logs"

    monkeypatch.setattr(ca, "_detect_scheme", lambda workspace, project: "basic_app")

    def fake_run(command, cwd, capture_output, text, timeout):
        raise subprocess.TimeoutExpired(cmd=command, timeout=timeout, output="partial", stderr="")

    monkeypatch.setattr(ca.subprocess, "run", fake_run)

    result = ca.run_xcodebuild(str(project_root), log_dir=log_dir, timeout_seconds=5)

    assert result.status == "TIMEOUT"
    assert Path(result.log_path).exists()


# ── check_compilation() pipeline node — iOS + dispatch ──────────────────


def test_check_compilation_dispatches_ios_and_prefers_sandbox_dir(monkeypatch, tmp_path):
    project_root = _make_project(tmp_path, use_workspace=True)

    captured: Dict[str, Any] = {}

    def fake_run_xcodebuild(project_root_str, log_dir, **kwargs):
        captured["project_root_str"] = project_root_str
        captured["log_dir"] = log_dir
        return ca.CompilationResult(
            status="PASSED",
            platform="ios",
            return_code=0,
            duration_seconds=1.23,
            log_path=str(log_dir / "fake.log"),
        )

    monkeypatch.setattr(ca, "run_xcodebuild", fake_run_xcodebuild)

    state = {
        "platform": "ios",
        "sandbox_dir": str(project_root),
        "app_path": "should_be_ignored.ipa",
        "run_dir": str(tmp_path / "run_dir"),
    }

    output = ca.check_compilation(state)

    assert output["compilation_passed"] is True
    assert isinstance(output["compilation_result"], ca.CompilationResult)
    assert output["audit_events"][0]["phase"] == "Build"
    assert captured["project_root_str"] == str(project_root)
    assert captured["log_dir"] == tmp_path / "run_dir" / "compilation-logs"


def test_check_compilation_missing_project_path_errors():
    output = ca.check_compilation({"platform": "ios"})

    assert output["compilation_passed"] is False
    assert output["compilation_result"].status == "ERROR"


def test_check_compilation_truly_unsupported_platform_errors(tmp_path):
    # NOTE: unlike an earlier draft of this test, "android" is intentionally
    # NOT treated as unsupported here — see the Android section below, it's
    # fully implemented via run_gradle_build(). Only a genuinely unknown
    # platform value should hit this branch.
    project_root = _make_project(tmp_path, use_workspace=True)

    output = ca.check_compilation({
        "platform": "windows",
        "sandbox_dir": str(project_root),
    })

    assert output["compilation_passed"] is False
    assert output["compilation_result"].status == "ERROR"


def test_check_compilation_defaults_to_ios_when_platform_missing(monkeypatch, tmp_path):
    project_root = _make_project(tmp_path, use_workspace=True)

    monkeypatch.setattr(
        ca,
        "run_xcodebuild",
        lambda *a, **k: ca.CompilationResult(status="PASSED", platform="ios", return_code=0, duration_seconds=0.1),
    )

    output = ca.check_compilation({"sandbox_dir": str(project_root)})

    assert output["compilation_passed"] is True


# ── Android: fake gradlew helper ─────────────────────────────────────────


def _make_fake_gradlew(project_dir: Path, exit_code: int, stdout: str = "", stderr: str = "") -> Path:
    """Create a tiny fake `gradlew` that stands in for the real Gradle wrapper."""
    gradlew = project_dir / "gradlew"
    gradlew.write_text(
        "#!/bin/sh\n"
        f'echo "{stdout}"\n'
        f'echo "{stderr}" 1>&2\n'
        f"exit {exit_code}\n",
        encoding="utf-8",
    )
    # Simulate a copy that lost its executable bit, to exercise _ensure_executable.
    gradlew.chmod(stat.S_IRUSR | stat.S_IWUSR)
    return gradlew


# ── Android: SDK location discovery (_find_android_sdk_root / _ensure_android_sdk_location) ──


def test_find_android_sdk_root_uses_env_var(tmp_path, monkeypatch):
    monkeypatch.setenv("ANDROID_HOME", str(tmp_path))
    monkeypatch.delenv("ANDROID_SDK_ROOT", raising=False)

    assert ca._find_android_sdk_root() == str(tmp_path)


def test_find_android_sdk_root_falls_back_to_os_default(tmp_path, monkeypatch):
    """
    Regression test: previously only ANDROID_HOME/ANDROID_SDK_ROOT were
    checked, so an SDK actually installed at the OS's common default
    location (but not exported as an env var) was invisible to the
    compilation check — Gradle then failed with "SDK location not found"
    even though the SDK was genuinely installed.
    """
    monkeypatch.delenv("ANDROID_HOME", raising=False)
    monkeypatch.delenv("ANDROID_SDK_ROOT", raising=False)
    monkeypatch.setattr(ca.platform, "system", lambda: "Linux")
    monkeypatch.setattr(ca.os.path, "expanduser", lambda p: str(tmp_path))

    assert ca._find_android_sdk_root() == str(tmp_path)


def test_find_android_sdk_root_returns_none_when_nothing_found(tmp_path, monkeypatch):
    monkeypatch.delenv("ANDROID_HOME", raising=False)
    monkeypatch.delenv("ANDROID_SDK_ROOT", raising=False)
    monkeypatch.setattr(ca.platform, "system", lambda: "Linux")
    monkeypatch.setattr(ca.os.path, "expanduser", lambda p: str(tmp_path / "does-not-exist"))

    assert ca._find_android_sdk_root() is None


def test_ensure_android_sdk_location_writes_local_properties(tmp_path, monkeypatch):
    project_root = tmp_path / "project"
    project_root.mkdir()
    sdk_dir = tmp_path / "sdk"
    sdk_dir.mkdir()
    monkeypatch.setenv("ANDROID_HOME", str(sdk_dir))

    ca._ensure_android_sdk_location(project_root)

    local_properties = project_root / "local.properties"
    assert local_properties.exists()
    assert "sdk.dir=" in local_properties.read_text()


def test_ensure_android_sdk_location_uses_default_when_env_unset(tmp_path, monkeypatch):
    project_root = tmp_path / "project"
    project_root.mkdir()
    monkeypatch.delenv("ANDROID_HOME", raising=False)
    monkeypatch.delenv("ANDROID_SDK_ROOT", raising=False)
    monkeypatch.setattr(ca, "_find_android_sdk_root", lambda: str(tmp_path / "default-sdk"))

    ca._ensure_android_sdk_location(project_root)

    local_properties = project_root / "local.properties"
    assert local_properties.exists()
    assert "default-sdk" in local_properties.read_text()


def test_ensure_android_sdk_location_noop_when_already_exists(tmp_path, monkeypatch):
    project_root = tmp_path / "project"
    project_root.mkdir()
    local_properties = project_root / "local.properties"
    local_properties.write_text("sdk.dir=/existing/path\n", encoding="utf-8")
    monkeypatch.setenv("ANDROID_HOME", str(tmp_path))

    ca._ensure_android_sdk_location(project_root)

    assert local_properties.read_text() == "sdk.dir=/existing/path\n"


def test_ensure_android_sdk_location_noop_when_sdk_not_found(tmp_path, monkeypatch):
    project_root = tmp_path / "project"
    project_root.mkdir()
    monkeypatch.setattr(ca, "_find_android_sdk_root", lambda: None)

    ca._ensure_android_sdk_location(project_root)

    assert not (project_root / "local.properties").exists()


@POSIX_ONLY
def test_run_gradle_build_success(tmp_path: Path):
    _make_fake_gradlew(tmp_path, exit_code=0, stdout="BUILD SUCCESSFUL")

    result = ca.run_gradle_build(tmp_path)

    assert isinstance(result, ca.CompilationResult)
    assert result.success is True
    assert result.status == "PASSED"
    assert result.platform == "android"
    assert result.return_code == 0
    assert "BUILD SUCCESSFUL" in result.stdout_tail
    assert result.log_path and Path(result.log_path).exists()


# ── Android: built APK discovery (_find_built_apk) ───────────────────────
#
# Regression coverage for the bug where a device that didn't already have
# the app installed (e.g. a freshly auto-booted emulator) was left sitting
# on its home screen: the pipeline built an APK but never installed it.


def _make_apk(project_root: Path, module: str, build_type: str, name: str) -> Path:
    apk_dir = project_root / module / "build" / "outputs" / "apk" / build_type
    apk_dir.mkdir(parents=True, exist_ok=True)
    apk = apk_dir / name
    apk.write_bytes(b"fake-apk-bytes")
    return apk


def test_find_built_apk_prefers_matching_build_type(tmp_path: Path):
    _make_apk(tmp_path, "app", "release", "app-release.apk")
    debug_apk = _make_apk(tmp_path, "app", "debug", "app-debug.apk")

    found = ca._find_built_apk(tmp_path, "assembleDebug")

    assert found == str(debug_apk)


def test_find_built_apk_falls_back_to_most_recent_when_no_match(tmp_path: Path):
    older = _make_apk(tmp_path, "app", "customFlavor", "app-custom.apk")
    os.utime(older, (1, 1))

    found = ca._find_built_apk(tmp_path, "assembleCustomFlavorRelease")

    assert found == str(older)


def test_find_built_apk_returns_none_when_nothing_built(tmp_path: Path):
    assert ca._find_built_apk(tmp_path, "assembleDebug") is None


@POSIX_ONLY
def test_run_gradle_build_success_populates_apk_path(tmp_path: Path):
    _make_fake_gradlew(tmp_path, exit_code=0, stdout="BUILD SUCCESSFUL")
    debug_apk = _make_apk(tmp_path, "app", "debug", "app-debug.apk")

    result = ca.run_gradle_build(tmp_path)

    assert result.extra["apk_path"] == str(debug_apk)


@POSIX_ONLY
def test_run_gradle_build_failure_does_not_populate_apk_path(tmp_path: Path):
    _make_apk(tmp_path, "app", "debug", "app-debug.apk")
    _make_fake_gradlew(tmp_path, exit_code=1, stderr="error: cannot find symbol")

    result = ca.run_gradle_build(tmp_path)

    assert result.extra == {}


@POSIX_ONLY
def test_run_gradle_build_failure(tmp_path: Path):
    _make_fake_gradlew(tmp_path, exit_code=1, stderr="error: cannot find symbol")

    result = ca.run_gradle_build(tmp_path)

    assert result.success is False
    assert result.status == "FAILED"
    assert result.return_code == 1
    assert "cannot find symbol" in result.stderr_tail
    assert "cannot find symbol" in result.error_excerpt


def test_run_gradle_build_missing_wrapper(tmp_path: Path):
    result = ca.run_gradle_build(tmp_path)

    assert result.success is False
    assert result.status == "SKIPPED"


def test_run_gradle_build_missing_app_path(tmp_path: Path):
    result = ca.run_gradle_build(tmp_path / "does-not-exist")

    assert result.success is False
    assert result.status == "ERROR"


@POSIX_ONLY
def test_finds_wrapper_in_nested_project_dir(tmp_path: Path):
    nested = tmp_path / "basic_app"
    nested.mkdir()
    _make_fake_gradlew(nested, exit_code=0, stdout="BUILD SUCCESSFUL")

    result = ca.run_gradle_build(tmp_path)

    assert result.success is True


@POSIX_ONLY
def test_prefers_posix_wrapper_when_both_exist(tmp_path: Path):
    """
    Regression test: real repos routinely commit both `gradlew` and
    `gradlew.bat` (cross-platform support). On macOS/Linux the POSIX
    script must be picked — picking `.bat` here fails with "Exec format
    error" (found while running this module against a real cloned repo).
    """
    _make_fake_gradlew(tmp_path, exit_code=0, stdout="BUILD SUCCESSFUL")
    (tmp_path / "gradlew.bat").write_text("@echo off\r\nexit /b 0\r\n", encoding="utf-8")

    wrapper = ca._find_gradle_wrapper(tmp_path)

    assert wrapper.name == "gradlew"


@POSIX_ONLY
def test_ensure_cocoapods_installed_reports_missing_pods_without_running_install(
    monkeypatch, tmp_path: Path
):
    """_ensure_cocoapods_installed is report-only: the SDK agent (LLM) is
    responsible for running `pod install` itself as part of integration. If
    Pods/Manifest.lock is still missing by compile time, this must surface a
    clear error message -- and must NOT silently run `pod install` itself,
    which would paper over the agent's mistake instead of reporting it."""
    project_root = _make_project(tmp_path)
    (project_root / "Podfile").write_text(
        "target 'basic_app' do\n  pod 'AppsFlyerFramework'\nend\n",
        encoding="utf-8",
    )
    calls: list[str] = []

    def fake_run_pod_install_command(work_dir: Path, *, timeout: int = 120):
        calls.append(str(work_dir))
        (work_dir / "Pods").mkdir()
        (work_dir / "Pods" / "Manifest.lock").write_text("LOCK", encoding="utf-8")
        return {"status": "OK"}

    monkeypatch.setattr(
        "infra.agents.sdkAgent.tools.sdk_project_tools.run_pod_install_command",
        fake_run_pod_install_command,
    )

    error = ca._ensure_cocoapods_installed(project_root)

    assert error is not None
    assert "pod install" in error
    assert calls == []


@POSIX_ONLY
def test_check_compilation_node_android(tmp_path: Path):
    _make_fake_gradlew(tmp_path, exit_code=0, stdout="BUILD SUCCESSFUL")
    state = {"platform": "android", "app_path": str(tmp_path)}

    updates = ca.check_compilation(state)

    assert updates["compilation_passed"] is True
    event = updates["audit_events"][0]
    assert event["phase"] == "Build"
    assert event["status"] == "passed"


@POSIX_ONLY
def test_check_compilation_node_prefers_sandbox_path_over_app_path(tmp_path: Path):
    real_project = tmp_path / "sandbox_copy"
    real_project.mkdir()
    _make_fake_gradlew(real_project, exit_code=0, stdout="BUILD SUCCESSFUL")

    state = {
        "platform": "android",
        "app_path": str(tmp_path / "does-not-exist"),
        "sandbox_path": str(real_project),
    }

    updates = ca.check_compilation(state)

    assert updates["compilation_passed"] is True
