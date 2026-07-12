"""
Task 4 — Application validation.

This module verifies that the chosen application/project exists and looks like a
valid mobile project before the agent starts changing files.
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any, Dict, List, Optional


IGNORED_DIRS = {".git", "build", "DerivedData", "node_modules", "Pods"}


@dataclass
class ApplicationValidationResult:
    status: str
    app_path: str
    platform: str
    markers: Dict[str, bool]
    key_files: List[str]
    build_check: Optional[Dict[str, Any]] = None
    error: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=2)


def _safe_relative(root: Path, path: Path) -> str:
    return str(path.resolve().relative_to(root.resolve()))


def _iter_relevant_files(app_path: Path) -> List[Path]:
    files: List[Path] = []

    for path in app_path.rglob("*"):
        relative_parts = path.relative_to(app_path).parts

        if any(part in IGNORED_DIRS for part in relative_parts):
            continue

        if path.is_file():
            files.append(path)

    return files


def detect_platform(app_path: Path) -> str:
    app_path = Path(app_path).resolve()

    if app_path.suffix == ".app":
        return "ios_app_bundle"

    has_ios_marker = (
        (app_path / "Podfile").exists()
        or bool(list(app_path.glob("*.xcodeproj")))
        or bool(list(app_path.glob("*.xcworkspace")))
    )

    if has_ios_marker:
        return "ios"

    has_android_marker = (
        (app_path / "settings.gradle").exists()
        or (app_path / "settings.gradle.kts").exists()
        or (app_path / "build.gradle").exists()
        or (app_path / "build.gradle.kts").exists()
    )

    if has_android_marker:
        return "android"

    return "unknown"


def _collect_markers(app_path: Path) -> Dict[str, bool]:
    return {
        "exists": app_path.exists(),
        "is_directory": app_path.is_dir(),
        "is_file": app_path.is_file(),
        "is_app_bundle": app_path.suffix == ".app",
        "has_podfile": (app_path / "Podfile").exists(),
        "has_xcodeproj": bool(list(app_path.glob("*.xcodeproj"))),
        "has_xcworkspace": bool(list(app_path.glob("*.xcworkspace"))),
        "has_settings_gradle": (app_path / "settings.gradle").exists()
        or (app_path / "settings.gradle.kts").exists(),
        "has_build_gradle": (app_path / "build.gradle").exists()
        or (app_path / "build.gradle.kts").exists(),
    }


def _collect_key_files(app_path: Path) -> List[str]:
    if app_path.is_file():
        return [app_path.name]

    key_files: List[str] = []
    allowed_names = {
        "Podfile",
        "Package.swift",
        "Info.plist",
        "settings.gradle",
        "settings.gradle.kts",
        "build.gradle",
        "build.gradle.kts",
    }
    allowed_suffixes = {".swift", ".plist", ".gradle", ".kts", ".pbxproj"}

    for path in _iter_relevant_files(app_path):
        if path.name in allowed_names or path.suffix in allowed_suffixes:
            key_files.append(_safe_relative(app_path, path))

    return sorted(key_files)


def _run_command(command: List[str], cwd: Path, timeout: int = 120) -> Dict[str, Any]:
    try:
        completed = subprocess.run(
            command,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=timeout,
        )

        return {
            "command": command,
            "exit_code": completed.returncode,
            "stdout": completed.stdout[-4000:],
            "stderr": completed.stderr[-4000:],
        }

    except Exception as exc:
        return {
            "command": command,
            "exit_code": -1,
            "error_type": type(exc).__name__,
            "error": str(exc),
        }


def run_basic_build_check(app_path: Path, platform: str) -> Optional[Dict[str, Any]]:
    """
    Runs a lightweight build/list check when possible.

    This is intentionally safe: if required tools are missing, the function returns
    the error in a structured way instead of crashing.
    """

    if platform == "ios":
        workspaces = list(app_path.glob("*.xcworkspace"))
        projects = list(app_path.glob("*.xcodeproj"))

        if workspaces:
            return _run_command(["xcodebuild", "-list", "-workspace", workspaces[0].name], cwd=app_path)

        if projects:
            return _run_command(["xcodebuild", "-list", "-project", projects[0].name], cwd=app_path)

        return None

    if platform == "android":
        gradlew = app_path / "gradlew"

        if gradlew.exists():
            return _run_command(["./gradlew", "tasks"], cwd=app_path)

        return _run_command(["gradle", "tasks"], cwd=app_path)

    return None


def validate_application(app_path: Path, run_build_check: bool = False) -> Dict[str, Any]:
    """Validate that the application exists and has expected mobile project markers."""

    app_path = Path(app_path).resolve()

    if not app_path.exists():
        return ApplicationValidationResult(
            status="FAILED",
            app_path=str(app_path),
            platform="unknown",
            markers={"exists": False},
            key_files=[],
            error=f"Application path does not exist: {app_path}",
        ).to_dict()

    platform = detect_platform(app_path)
    markers = _collect_markers(app_path)
    key_files = _collect_key_files(app_path)

    if platform == "unknown":
        return ApplicationValidationResult(
            status="FAILED",
            app_path=str(app_path),
            platform=platform,
            markers=markers,
            key_files=key_files,
            error="Could not detect iOS or Android project markers.",
        ).to_dict()

    if platform == "ios_app_bundle" and app_path.is_file() and app_path.stat().st_size == 0:
        return ApplicationValidationResult(
            status="FAILED",
            app_path=str(app_path),
            platform=platform,
            markers=markers,
            key_files=key_files,
            error="The .app path exists but is an empty file, not a valid app bundle directory.",
        ).to_dict()

    build_check = run_basic_build_check(app_path, platform) if run_build_check else None

    if build_check and build_check.get("exit_code") != 0:
        return ApplicationValidationResult(
            status="FAILED",
            app_path=str(app_path),
            platform=platform,
            markers=markers,
            key_files=key_files,
            build_check=build_check,
            error="Application structure was detected, but build/list check failed.",
        ).to_dict()

    return ApplicationValidationResult(
        status="OK",
        app_path=str(app_path),
        platform=platform,
        markers=markers,
        key_files=key_files,
        build_check=build_check,
        error=None,
    ).to_dict()
