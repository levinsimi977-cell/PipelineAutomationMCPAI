import shutil
import uuid
from datetime import datetime
from pathlib import Path
import argparse
import asyncio
import json
import os
from typing import Any, Dict

from infra.application.app_validator import validate_application
from infra.application.mcp_environment import check_mcp_alive
from infra.load_env import get_dev_key

_PROJECT_ROOT = Path(__file__).resolve().parents[2]

_PLATFORM_SAMPLE_APPS = {
    "android": "appsflyer-onelink-android-sample-apps",
    "ios": "appsflyer-onelink-ios-sample-apps",
}


def _find_ios_project_root(sample_apps_root: Path) -> Path:
    """Pick the shallowest Xcode project inside the iOS sample-apps bundle."""
    projects = sorted(sample_apps_root.rglob("*.xcodeproj"))
    if not projects:
        raise FileNotFoundError(
            f"No .xcodeproj found under iOS sample apps: {sample_apps_root}"
        )
    return min(projects, key=lambda path: len(path.parts)).parent


def _find_android_project_root(sample_apps_root: Path) -> Path:
    """Pick the shallowest Gradle project inside the Android sample-apps bundle."""
    matches: list[Path] = []
    for marker in ("settings.gradle", "settings.gradle.kts"):
        matches.extend(sample_apps_root.rglob(marker))
    if not matches:
        raise FileNotFoundError(
            f"No settings.gradle found under Android sample apps: {sample_apps_root}"
        )
    return min(matches, key=lambda path: len(path.parts)).parent


def resolve_sample_app_source_path(platform_name: str) -> Path:
    """
    Map platform to the concrete mobile project directory (not the repo wrapper).
    Sample-app folders contain multiple nested projects; validation needs the
    directory that actually has Podfile/xcodeproj or settings.gradle.
    """
    folder_name = _PLATFORM_SAMPLE_APPS.get(platform_name)
    if folder_name is None:
        raise ValueError(
            f"Unsupported platform: {platform_name}. Must be 'android' or 'ios'."
        )

    sample_apps_root = _PROJECT_ROOT / "data" / "application" / folder_name
    if not sample_apps_root.exists():
        raise FileNotFoundError(
            f"Required application folder missing at: {sample_apps_root}"
        )

    if platform_name == "ios":
        return _find_ios_project_root(sample_apps_root)
    return _find_android_project_root(sample_apps_root)

# =====================================================================
# TASK 1: Sandbox Replication Environment (Team 1 Integration)
# =====================================================================

def create_sandbox_app(original_app_path: str) -> str:
    """
    Creates a sterile, isolated Sandbox environment for the application under test.
    Handles both structural directory trees (folders) and individual standalone files safely.
    Filters out unnecessary heavy files/metadata during directory duplication.
    """
    # Generate a unique timestamp for the individual pipeline execution run
    unique_id = uuid.uuid4().hex[:8]
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    
    # Ensure the dynamic container folder physically exists on disk
    sandbox_root = _PROJECT_ROOT / "sandboxes" / f"run_{timestamp}_{unique_id}"
    
    
    original_path = Path(original_app_path)
    sandbox_app_path = sandbox_root / original_path.name
    
    # Scenario A: The application under test is a heavy project/source folder
    if original_path.is_dir():
        def ignore_dirs(_, names):
            return {
                name
                for name in names
                if name in {
                    "build", ".gradle", ".git", "__pycache__",
                    ".venv", ".idea", "local.properties", ".DS_Store",
                    "ios-sdk-logs.txt", "ios-deeplink-logs.txt", "DerivedData",
                } or name.endswith(".iml")
            }
        shutil.copytree(str(original_path), str(sandbox_app_path), ignore=ignore_dirs)
        
    # Scenario B: The application under test is an atomic standalone file (e.g., .apk, .exe, placeholder file)
    else:
        shutil.copy(str(original_path), str(sandbox_app_path))
        
    return str(sandbox_app_path.resolve())

# ****helper function****
# =====================================================================
# FUNCTION 1: Input Data Parsing
# =====================================================================
def extract_platform_from_json(state: dict) -> str:
    """
    Extracts the platform name from the incoming UseCase JSON state payload.
    Raises a KeyError if the required 'PLATFORM' key is missing.
    """
    platform = state.get("platform")
    if not platform:
        raise KeyError("Missing 'platform' key in the input UseCase JSON.")
    return platform

# ****helper function
# =====================================================================
# FUNCTION 2: Directory Mapping and Sandbox Routing
# =============================================
# ========================
def _preserve_run_artifacts() -> bool:
    """Whether run artifacts (sandbox, audit, reports) should be preserved.

    Controlled by env var PRESERVE_RUN_ARTIFACTS. Default is 'true' to
    respect the user's request to keep run outputs for debugging.
    """
    val = os.getenv("PRESERVE_RUN_ARTIFACTS", "true") or "true"
    return str(val).strip().lower() in ("1", "true", "yes")


def cleanup_stale_sandboxes() -> None:
    """
    Delete leftover sandbox directories from previous runs unless preservation
    is requested via PRESERVE_RUN_ARTIFACTS. When preservation is on, do nothing
    to avoid losing artifacts the user needs for debugging.
    """
    if _preserve_run_artifacts():
        print("🧾 PRESERVE_RUN_ARTIFACTS=true: skipping stale sandbox cleanup")
        return

    sandboxes_root = _PROJECT_ROOT / "sandboxes"
    if not sandboxes_root.exists():
        return
    for entry in sandboxes_root.iterdir():
        if entry.is_dir() and entry.name.startswith("run_"):
            try:
                shutil.rmtree(entry)
                print(f"🧹 Removed stale sandbox from a previous run: {entry}")
            except Exception as exc:
                print(f"⚠️ Could not remove stale sandbox {entry}: {exc}")


def resolve_and_replicate_app(platform_name: str) -> str:
    """
    Maps the extracted platform name to the local directory structure 
    and passes the validated absolute path to the sandbox replicator.
    """
    cleanup_stale_sandboxes()
    absolute_app_path = str(resolve_sample_app_source_path(platform_name))
    return create_sandbox_app(absolute_app_path)


def setup_environment(state: dict) -> dict:
    """
    Official pipeline workflow node. Parses the contract state,
    provisions the sandbox directory, and outputs the updated pipeline payload.
    """
    try:
        platform_name = extract_platform_from_json(state)        
        sandbox_app_path = resolve_and_replicate_app(platform_name)
        # Register immediately so teardown can delete even if this node
        # crashes before LangGraph commits sandbox_path into state.
        run_id = state.get("run_id")
        if run_id:
            try:
                from infra.workflow.run_resource_registry import register_sandbox

                register_sandbox(str(run_id), sandbox_app_path)
            except Exception:
                pass
        
        # Return state compatible with downstream pipeline components
        return {
            "platform": platform_name,
            "app_path": sandbox_app_path,             
            "sandbox_path": sandbox_app_path,
            "test_status": "READY"
        }
    except KeyError as k_err:
        print(f"Validation Error: {k_err}")
        return {
            "test_status": "FAIL",
            "error_reason": str(k_err)
        }
    except Exception as exc:
        print(f"Critical Error during environment setup orchestrator: {exc}")
        return {
            "test_status": "FAIL",
            "error_reason": f"Environment setup failed: {str(exc)}"
        }
        
def cleanup_environment(sandbox_path: str) -> dict:
    """
    Teardown Phase: Safely removes the allocated sandbox directory and its contents
    to prevent local disk bloating after the UseCase execution completes.

    Args:
        sandbox_path (str): The absolute or relative path to the sandbox application directory.

    Returns:
        dict: A status dictionary indicating whether the cleanup was SUCCESS, SKIPPED, or FAILED.
    """
    try:
        # Convert to an absolute Path object for reliable filesystem operations
        app_path = Path(sandbox_path).resolve()
        
        # The actual container to delete is the unique run folder (the parent of the app directory)
        run_folder = app_path.parent
        
        # If preservation is requested, skip deletion and keep artifacts intact.
        if _preserve_run_artifacts():
            print(f"🧾 PRESERVE_RUN_ARTIFACTS=true: keeping run artifacts at {run_folder}")
            return {"cleanup_status": "SKIPPED", "reason": "Preserve run artifacts enabled"}

        # Safety Check: Ensure the target exists, is a directory, and contains our dynamic "run_" prefix.
        if run_folder.exists() and run_folder.is_dir() and "run_" in run_folder.name:
            shutil.rmtree(run_folder)
            print(f"🛡️ Cleanup Success: Fully deleted sandbox run environment at: {run_folder}")
            _forget_sandbox_path(sandbox_path)
            return {"cleanup_status": "SUCCESS"}
            
        # Fallback: Safely delete the specific app path instead if the parent layout differs
        elif app_path.exists():
            if _preserve_run_artifacts():
                print(f"🧾 PRESERVE_RUN_ARTIFACTS=true: keeping app path {app_path}")
                return {"cleanup_status": "SKIPPED", "reason": "Preserve run artifacts enabled"}
            if app_path.is_dir():
                shutil.rmtree(app_path)
            else:
                app_path.unlink()
            print(f"🛡️ Cleanup Success: Deleted atomic sandbox application target at: {app_path}")
            _forget_sandbox_path(sandbox_path)
            return {"cleanup_status": "SUCCESS"}
            
        else:
            print(f"⚠️ Cleanup Warning: Target path does not exist, skipping deletion: {sandbox_path}")
            _forget_sandbox_path(sandbox_path)
            return {"cleanup_status": "SKIPPED", "reason": "Path not found"}
            
    except Exception as exc:
        print(f"❌ Critical Error during environment cleanup: {exc}")
        return {"cleanup_status": "FAILED", "error_reason": str(exc)}


def _forget_sandbox_path(sandbox_path: str) -> None:
    """Drop a sandbox path from the run-resource registry (best-effort)."""
    try:
        from infra.workflow.run_resource_registry import forget_sandbox_path

        forget_sandbox_path(sandbox_path)
    except Exception:
        pass
def build_application_report(
    app_path: Path,
    workdir: Path,
    run_build_check: bool = False,
) -> Dict[str, Any]:
    app_validation = validate_application(app_path=app_path, run_build_check=run_build_check)

    return {
        "application_validation": app_validation,
        "workdir": str(Path(workdir).resolve()),
    }


async def run_tasks_3_and_4(
    app_path: Path,
    workdir: Path,
    run_build_check: bool = False,
    *,
    app_id: str | None = None,
    dev_key: str | None = None,
    mcp_startup_timeout_seconds: int | None = None,
) -> Dict[str, Any]:
    app_report = build_application_report(
        app_path=app_path,
        workdir=workdir,
        run_build_check=run_build_check,
    )

    mcp_kwargs: Dict[str, Any] = {}
    if mcp_startup_timeout_seconds is not None:
        mcp_kwargs["startup_timeout_seconds"] = mcp_startup_timeout_seconds

    mcp_report = await check_mcp_alive(
        workdir=workdir,
        app_id=app_id or os.getenv("APP_ID"),
        dev_key=dev_key or get_dev_key(),
        **mcp_kwargs,
    )

    final_status = "OK"

    if app_report["application_validation"].get("status") != "OK":
        final_status = "FAILED"

    if mcp_report.get("status") != "OK":
        final_status = "FAILED"

    return {
        "status": final_status,
        "task_3_mcp_alive": mcp_report,
        "task_4_application_validation": app_report["application_validation"],
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run application-1 tasks 3 and 4.")
    parser.add_argument("--app-path", required=True, help="Path to the selected application/project.")
    parser.add_argument("--workdir", default=".", help="Work directory where MCP should be checked.")
    parser.add_argument(
        "--run-build-check",
        action="store_true",
        help="Run xcodebuild/gradle lightweight project checks when possible.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    result = asyncio.run(
        run_tasks_3_and_4(
            app_path=Path(args.app_path),
            workdir=Path(args.workdir),
            run_build_check=args.run_build_check,
        )
    )

    print(json.dumps(result, ensure_ascii=False, indent=2))

if __name__ == "__main__":
    main()

