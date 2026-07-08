import shutil
from datetime import datetime
from pathlib import Path
import argparse
import asyncio
import json
import os
from typing import Any, Dict

from infra.application.app_validator import validate_application
from infra.application.mcp_environment import check_mcp_alive

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
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    sandbox_root = Path("sandboxes").resolve() / f"run_{timestamp}"
    
    # Ensure the dynamic container folder physically exists on disk
    sandbox_root.mkdir(parents=True, exist_ok=True)
    
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
                    ".venv", ".idea", "local.properties", ".DS_Store"
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
# =====================================================================
def resolve_and_replicate_app(platform_name: str) -> str:
    """
    Maps the extracted platform name to the local directory structure 
    and passes the validated absolute path to the sandbox replicator.
    """
    base_dir = "data/application"
    
    # Map the platform string to your exact repository folder names
    if platform_name == "android":
        app_folder_name = "appsflyer-onelink-android-sample-apps"
    elif platform_name == "ios":
        app_folder_name = "appsflyer-onelink-ios-sample-apps"
    else:
        raise ValueError(f"Unsupported platform: {platform_name}. Must be 'android' or 'ios'.")
        
    # Build the absolute path to the target folder
    absolute_app_path = str(Path(base_dir).resolve() / app_folder_name)
    
    # Verify the physical folder exists locally before duplicating
    if not Path(absolute_app_path).exists():
        raise FileNotFoundError(f"Required application folder missing at: {absolute_app_path}")
        
    # Execute replication by calling your original function
    return create_sandbox_app(absolute_app_path)


def setup_environment(state: dict) -> dict:
    """
    Official pipeline workflow node. Parses the contract state,
    provisions the sandbox directory, and outputs the updated pipeline payload.
    """
    try:
        platform_name = extract_platform_from_json(state)        
        sandbox_app_path = resolve_and_replicate_app(platform_name)
        
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
) -> Dict[str, Any]:
    app_report = build_application_report(
        app_path=app_path,
        workdir=workdir,
        run_build_check=run_build_check,
    )

    mcp_report = await check_mcp_alive(
        workdir=workdir,
        app_id=os.getenv("APP_ID"),
        dev_key=os.getenv("APPSFLYER_DEV_KEY") or os.getenv("DEV_KEY"),
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

