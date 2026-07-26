"""Local sandbox tools for the SDK integration agent (CocoaPods, path safety)."""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path
from typing import Any

from langchain_core.tools import tool

DEFAULT_POD_INSTALL_TIMEOUT_SECONDS = 120
_LOG_TAIL_CHARS = 8000
_PODFILE_POD_LINE_RE = re.compile(r"^\s*pod\s+['\"]", re.MULTILINE)
_IOS_POD_DEPENDENCY_HINT = (
    "\n\nAfter CocoaPods setup steps, dependencies must be installed "
    "before building or running verification."
)


def safe_project_path(project_root: Path, requested_path: str) -> Path:
    """Resolve requested_path and reject paths outside project_root."""
    requested = Path(requested_path)
    resolved = (
        requested.resolve()
        if requested.is_absolute()
        else (project_root / requested).resolve()
    )
    if not str(resolved).startswith(str(project_root.resolve())):
        raise ValueError(
            f"Blocked unsafe file path outside project root: {requested_path}"
        )
    return resolved


def find_podfile_directory(project_root: Path) -> Path | None:
    """Return the directory containing the shallowest Podfile under project_root."""
    podfiles = [p for p in project_root.rglob("Podfile") if p.is_file()]
    if not podfiles:
        return None
    return min(
        podfiles,
        key=lambda p: len(p.relative_to(project_root).parts),
    ).parent


def _tail(text: str, limit: int = _LOG_TAIL_CHARS) -> str:
    if len(text) <= limit:
        return text
    return f"...(truncated)\n{text[-limit:]}"


def run_pod_install_command(
    work_dir: Path,
    *,
    timeout: int = DEFAULT_POD_INSTALL_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    """Run `pod install` in work_dir and return a structured outcome dict."""
    try:
        completed = subprocess.run(
            ["pod", "install"],
            cwd=work_dir,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except FileNotFoundError:
        return {
            "status": "FAILED",
            "reason": "CocoaPods CLI (`pod`) not found on PATH",
        }
    except subprocess.TimeoutExpired as exc:
        stdout = _tail(exc.stdout or "")
        stderr = _tail(exc.stderr or "")
        return {
            "status": "FAILED",
            "reason": f"pod install timed out after {timeout}s",
            "stdout_tail": stdout,
            "stderr_tail": stderr,
        }

    stdout = _tail(completed.stdout or "")
    stderr = _tail(completed.stderr or "")
    if completed.returncode != 0:
        return {
            "status": "FAILED",
            "reason": f"pod install exited with code {completed.returncode}",
            "stdout_tail": stdout,
            "stderr_tail": stderr,
        }
    return {
        "status": "OK",
        "working_directory": str(work_dir),
        "stdout_tail": stdout,
        "stderr_tail": stderr,
    }


def append_ios_pod_dependency_hint(text: str) -> str:
    """Append a soft CocoaPods dependency hint when integrateSdk omits it."""
    if not text:
        return text
    lowered = text.lower()
    if "dependencies must be installed" in lowered:
        return text
    if "podfile" not in lowered and "cocoapods" not in lowered:
        return text
    return text.rstrip() + _IOS_POD_DEPENDENCY_HINT


def wrap_integrate_sdk_with_ios_hint(mcp_tools: list[Any]) -> None:
    """Post-process integrateSdk iOS responses with a soft dependency-install hint."""
    for mcp_tool in mcp_tools:
        if getattr(mcp_tool, "name", "") != "integrateSdk":
            continue
        original = mcp_tool.coroutine
        if original is None:
            continue

        async def _coroutine_with_ios_hint(_original=original, **kwargs: Any) -> Any:
            result = await _original(**kwargs)
            platform = (kwargs.get("platform") or "").lower()
            if isinstance(result, str) and (
                platform == "ios" or ("podfile" in result.lower() and platform != "android")
            ):
                return append_ios_pod_dependency_hint(result)
            return result

        mcp_tool.coroutine = _coroutine_with_ios_hint


def build_run_pod_install_tool(
    project_root: Path,
    platform: str,
    audit_recorder: Any,
):
    """Build the runPodInstall tool for iOS CocoaPods dependency resolution."""
    platform_lower = platform.lower()

    @tool("runPodInstall")
    def run_pod_install(project_path: str = "") -> str:
        """Executes `pod install` in the iOS project directory to resolve CocoaPods
        dependencies after modifying the Podfile. Call after adding or changing any
        `pod` lines in the Podfile, before verification or declaring integration complete."""
        if platform_lower != "ios":
            return json.dumps(
                {
                    "status": "SKIPPED",
                    "reason": f"runPodInstall is iOS-only (platform={platform_lower})",
                },
                indent=2,
            )
        try:
            if project_path.strip():
                work_dir = safe_project_path(project_root, project_path)
            else:
                found = find_podfile_directory(project_root)
                if found is None:
                    return json.dumps(
                        {
                            "status": "FAILED",
                            "reason": "No Podfile found under the project root",
                        },
                        indent=2,
                    )
                work_dir = found

            podfile_path = work_dir / "Podfile"
            if not podfile_path.is_file():
                return json.dumps(
                    {
                        "status": "FAILED",
                        "reason": f"No Podfile in {work_dir}",
                    },
                    indent=2,
                )

            podfile_text = podfile_path.read_text(encoding="utf-8")
            if not _PODFILE_POD_LINE_RE.search(podfile_text):
                outcome = {
                    "status": "SKIPPED",
                    "reason": "Podfile has no pod dependencies yet",
                    "working_directory": str(work_dir),
                }
                audit_recorder.write("POD_INSTALL", outcome)
                return json.dumps(outcome, indent=2)

            outcome = run_pod_install_command(work_dir)
            audit_recorder.write("POD_INSTALL", outcome)
            return json.dumps(outcome, indent=2)
        except Exception as exc:
            outcome = {"status": "FAILED", "error": str(exc)}
            audit_recorder.write("POD_INSTALL", outcome)
            return json.dumps(outcome, indent=2)

    return run_pod_install
