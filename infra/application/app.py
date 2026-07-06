import shutil
import os
from datetime import datetime
from pathlib import Path

# =====================================================================
# TASK 2: Application Selection & Validation (Team 2 Integration)
# =====================================================================

def list_available_applications(base_dir: str = "data/application") -> list:
    """
    Scans the application data directory and returns a list of available app folders/files.
    Excludes hidden files/directories (e.g., .gitkeep, .DS_Store).
    """
    base_path = Path(base_dir)
    if not base_path.exists():
        print(f"Warning: Base directory {base_dir} does not exist.")
        return []
        
    # Standard security & cleanup filter to list only visible target apps
    return [f.name for f in base_path.iterdir() if not f.name.startswith(".")]

def select_and_validate_app(app_name: str, base_dir: str = "data/application") -> str:
    """
    Validates if the selected application exists in the target directory.
    Ensures strict path safety to prevent directory traversal attacks (e.g., passing "../../etc").
    Returns the absolute path if valid, otherwise raises an exception.
    """
    base_path = Path(base_dir).resolve()
    # Resolve the target path completely to eliminate relative segments like '..'
    target_app_path = (base_path / app_name).resolve()
    
    # Security Guardrail: Prevent escaping the designated data/application directory
    if base_path not in target_app_path.parents and target_app_path != base_path:
        raise ValueError(f"Security Alert: Path traversal detected for input '{app_name}'")
    
    if not target_app_path.exists():
        raise FileNotFoundError(f"Requested application '{app_name}' was not found in {base_dir}")
        
    return str(target_app_path)


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

def setup_environment(state: dict) -> dict:
    """
    Workflow Node function that serves as the official pipeline entry point.
    Dynamically extracts the selected app name from the shared state, validates it,
    clones it into an isolated sandbox, and updates the pipeline state for downstream tasks.
    """
    selected_app_name = state.get("selected_app")
    
    if not selected_app_name:
        return {
            "test_status": "FAIL",
            "error_reason": "Missing 'selected_app' in the initial workflow state."
        }
        
    try:
        # Step 1: Dynamically scan and validate user selection
        absolute_app_path = select_and_validate_app(selected_app_name)
        
        # Step 2: Provision the sterile environment
        sandbox_app_path = create_sandbox_app(absolute_app_path)
        
        # Step 3: Populate and return the state update payload
        return {
            "selected_app": selected_app_name,
            "original_app_path": absolute_app_path, 
            "app_path": sandbox_app_path,             
            "sandbox_path": sandbox_app_path,
            "test_status": "READY"
        }
    except Exception as exc:
        print(f"Error during environment setup: {exc}")
        return {
            "selected_app": selected_app_name,
            "original_app_path": "",
            "app_path": "",
            "sandbox_path": "",
            "test_status": "FAIL",
            "error_reason": f"Environment setup failed: {str(exc)}"
        }
        
