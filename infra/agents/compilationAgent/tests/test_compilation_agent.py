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
        return FakeCompletedProcess(0, stdout="** BUILD SUCCEEDED **", stderr="")

    monkeypatch.setattr(ca.subprocess, "run", fake_run)

    result = ca.run_xcodebuild(str(project_root), log_dir=log_dir)

    assert result.status == "PASSED"
    assert result.return_code == 0
    assert result.scheme == "basic_app"
    assert result.log_path is not None
    assert Path(result.log_path).exists()
    assert "BUILD SUCCEEDED" in Path(result.log_path).read_text()


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


@POSIX_ONLY
def test_run_gradle_build_failure(tmp_path: Path):
    _make_fake_gradlew(tmp_path, exit_code=1, stderr="error: cannot find symbol")

    result = ca.run_gradle_build(tmp_path)

    assert result.success is False
    assert result.status == "FAILED"
    assert result.return_code == 1
    assert "cannot find symbol" in result.stderr_tail


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
