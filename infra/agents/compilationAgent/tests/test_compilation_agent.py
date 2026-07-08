import os
import stat
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from compilation_agent import (  # noqa: E402
    CompilationResult,
    check_compilation,
    run_gradle_build,
    run_xcodebuild,
)

POSIX_ONLY = pytest.mark.skipif(os.name == "nt", reason="fake gradlew/xcodebuild are POSIX shell scripts")


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


def _make_fake_xcodebuild(bin_dir: Path, exit_code: int, scheme: str = "MyApp") -> Path:
    """
    Create a fake `xcodebuild` on PATH that answers `-list -json` with a
    single scheme, and otherwise exits with `exit_code` for the actual
    build invocation.
    """
    script = bin_dir / "xcodebuild"
    script.write_text(
        "#!/bin/sh\n"
        'case "$*" in\n'
        '  *-list*)\n'
        f'    echo \'{{"project": {{"schemes": ["{scheme}"]}}}}\'\n'
        "    exit 0\n"
        "    ;;\n"
        "esac\n"
        'echo "BUILD OUTPUT"\n'
        f"exit {exit_code}\n",
        encoding="utf-8",
    )
    script.chmod(stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR)
    return script


# ── Android (Gradle) ────────────────────────────────────────────────────


@POSIX_ONLY
def test_run_gradle_build_success(tmp_path: Path):
    _make_fake_gradlew(tmp_path, exit_code=0, stdout="BUILD SUCCESSFUL")

    result = run_gradle_build(tmp_path)

    assert isinstance(result, CompilationResult)
    assert result.success is True
    assert result.status == "PASSED"
    assert result.platform == "android"
    assert result.return_code == 0
    assert "BUILD SUCCESSFUL" in result.stdout_tail
    assert result.log_path and Path(result.log_path).exists()


@POSIX_ONLY
def test_run_gradle_build_failure(tmp_path: Path):
    _make_fake_gradlew(tmp_path, exit_code=1, stderr="error: cannot find symbol")

    result = run_gradle_build(tmp_path)

    assert result.success is False
    assert result.status == "FAILED"
    assert result.return_code == 1
    assert "cannot find symbol" in result.stderr_tail


def test_run_gradle_build_missing_wrapper(tmp_path: Path):
    result = run_gradle_build(tmp_path)

    assert result.success is False
    assert result.status == "SKIPPED"


def test_run_gradle_build_missing_app_path(tmp_path: Path):
    result = run_gradle_build(tmp_path / "does-not-exist")

    assert result.success is False
    assert result.status == "ERROR"


@POSIX_ONLY
def test_finds_wrapper_in_nested_project_dir(tmp_path: Path):
    nested = tmp_path / "basic_app"
    nested.mkdir()
    _make_fake_gradlew(nested, exit_code=0, stdout="BUILD SUCCESSFUL")

    result = run_gradle_build(tmp_path)

    assert result.success is True


# ── iOS (xcodebuild) ────────────────────────────────────────────────────


@POSIX_ONLY
def test_run_xcodebuild_success(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _make_fake_xcodebuild(bin_dir, exit_code=0, scheme="MyApp")
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")

    project_dir = tmp_path / "ios_app"
    (project_dir / "MyApp.xcodeproj").mkdir(parents=True)

    result = run_xcodebuild(project_dir)

    assert result.success is True
    assert result.status == "PASSED"
    assert result.platform == "ios"
    assert result.build_target == "MyApp"
    assert result.log_path and Path(result.log_path).exists()


@POSIX_ONLY
def test_run_xcodebuild_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _make_fake_xcodebuild(bin_dir, exit_code=65, scheme="MyApp")
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")

    project_dir = tmp_path / "ios_app"
    (project_dir / "MyApp.xcworkspace").mkdir(parents=True)

    result = run_xcodebuild(project_dir)

    assert result.success is False
    assert result.status == "FAILED"
    assert result.return_code == 65


def test_run_xcodebuild_missing_project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _make_fake_xcodebuild(bin_dir, exit_code=0)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")

    result = run_xcodebuild(tmp_path)

    assert result.success is False
    assert result.status == "SKIPPED"


def test_run_xcodebuild_unavailable_tool(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("PATH", str(tmp_path))  # empty dir -> xcodebuild not found

    result = run_xcodebuild(tmp_path)

    assert result.success is False
    assert result.status == "SKIPPED"
    assert "xcodebuild is not available" in result.message


# ── check_compilation() pipeline node ───────────────────────────────────


@POSIX_ONLY
def test_check_compilation_node_android(tmp_path: Path):
    _make_fake_gradlew(tmp_path, exit_code=0, stdout="BUILD SUCCESSFUL")
    state = {"platform": "android", "app_path": str(tmp_path)}

    updates = check_compilation(state)

    assert updates["compilation_passed"] is True
    event = updates["audit_events"][0]
    assert event["phase"] == "Build"
    assert event["status"] == "passed"


@POSIX_ONLY
def test_check_compilation_node_ios(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _make_fake_xcodebuild(bin_dir, exit_code=0, scheme="MyApp")
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")

    project_dir = tmp_path / "ios_app"
    (project_dir / "MyApp.xcodeproj").mkdir(parents=True)
    state = {"platform": "ios", "app_path": str(project_dir)}

    updates = check_compilation(state)

    assert updates["compilation_passed"] is True
    assert updates["compilation_result"].platform == "ios"


def test_check_compilation_node_unsupported_platform(tmp_path: Path):
    state = {"platform": "windows", "app_path": str(tmp_path)}

    updates = check_compilation(state)

    assert updates["compilation_passed"] is False
    assert updates["compilation_result"].status == "ERROR"


@POSIX_ONLY
def test_check_compilation_node_prefers_sandbox_path(tmp_path: Path):
    real_project = tmp_path / "sandbox_copy"
    real_project.mkdir()
    _make_fake_gradlew(real_project, exit_code=0, stdout="BUILD SUCCESSFUL")

    state = {
        "platform": "android",
        "app_path": str(tmp_path / "does-not-exist"),
        "sandbox_path": str(real_project),
    }

    updates = check_compilation(state)

    assert updates["compilation_passed"] is True
