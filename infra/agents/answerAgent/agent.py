import os
import json
from pathlib import Path
from typing import Any, Dict
from dotenv import load_dotenv
from langchain_openai import ChatOpenAI
from langchain_mcp_adapters.client import MultiServerMCPClient
from langchain.agents import create_agent
from langchain_core.tools import tool
from infra.agents.AuditRecorder import AuditRecorder
from infra.listener.llm_listener import invoke_agent_with_listener
# טעינת משתני סביבה
load_dotenv(Path(__file__).resolve().parent / ".env", override=True)
APP_ID = os.getenv("APP_ID", "id1512793879")


def safe_project_path(project_root: Path, requested_path: str) -> Path:
    """
    מוודא שהנתיב המבוקש נמצא בתוך תיקיית הפרויקט המבודדת (Sandbox).
    """
    requested = Path(requested_path)
    resolved = requested.resolve() if requested.is_absolute() else (project_root / requested).resolve()

    if not str(resolved).startswith(str(project_root.resolve())):
        raise ValueError(f"Blocked unsafe file path outside project root: {requested_path}")

    return resolved


async def create_sdk_integration_agent(
        project_root_str: str,  # נתיב ה-Sandbox (מתקבל כמחרוזת)
        platform: str,  # 'ios' או 'android'
        user_prompt: str,  # בקשת המשתמש
        audit_recorder: AuditRecorder  # אובייקט התיעוד
) -> Dict[str, Any]:
    """
    מכין את סוכן ה-AI: מתחבר ל-MCP, מגדיר כלי קבצים מותאמים לפלטפורמה,
    ובונה את הפרומפט. מחזיר את הסוכן והפרומפט להרצה.
    """

    project_root = Path(project_root_str)
    platform_lower = platform.lower()
    # 1. טעינת מפתחות API
    openai_api_key = os.getenv("OPENAI_API_KEY")
    dev_key = os.getenv("APPSFLYER_DEV_KEY")
    if not openai_api_key or not dev_key:
        raise RuntimeError("Missing OPENAI_API_KEY or APPSFLYER_DEV_KEY in .env")
    # 2. אתחול מודל ה-LLM
    model = ChatOpenAI(model="gpt-4o-mini", api_key=openai_api_key, temperature=0)
    # 3. התחברות לשרת ה-MCP
    mcp_client = MultiServerMCPClient({
        "appsflyer-sdk-mcp": {
            "transport": "stdio",
            "command": "npx",
            "args": ["-y", "@appsflyer/sdk-mcp-server"],
            "env": {"APP_ID": APP_ID, "DEV_KEY": dev_key},
        }
    })

    mcp_tools = await mcp_client.get_tools()
    audit_recorder.write("TOOLS_DISCOVERED", {
        "tools": [getattr(t, "name", str(t)) for t in mcp_tools]
    })

    # 4. הגדרת כלי הקבצים (Custom Tools)

    @tool
    def list_project_files() -> str:
        """
        List relevant editable files in the project.
        Use this before deciding which file to read or edit.
        """
        # פיצול הסיומות והשמות המורשים לפי פלטפורמה
        if platform_lower == 'ios':
            allowed_suffixes = {".swift", ".plist", ".podspec", ".pbxproj", ".xcodeproj", ".xcworkspace"}
            allowed_names = {"Podfile", "Package.swift"}
        elif platform_lower == 'android':
            allowed_suffixes = {".java", ".kt", ".xml", ".gradle", ".kts", ".properties"}
            allowed_names = {"AndroidManifest.xml"}
        else:
            return json.dumps({"error": f"Unsupported platform: {platform_lower}"})

        files = []
        for path in project_root.rglob("*"):
            if path.is_file() and (path.name in allowed_names or path.suffix in allowed_suffixes):
                files.append(str(path.relative_to(project_root)))

        return json.dumps({"project_root": str(project_root), "files": files}, ensure_ascii=False, indent=2)

    @tool
    def read_project_file(file_path: str) -> str:
        """Read a project file (relative to project root)."""
        try:
            path = safe_project_path(project_root, file_path)
            if not path.is_file():
                return json.dumps({"status": "FAILED", "reason": "Not a file or does not exist"}, indent=2)
            return json.dumps({"status": "OK", "content": path.read_text(encoding="utf-8")}, indent=2)
        except Exception as e:
            return json.dumps({"status": "FAILED", "error": str(e)}, indent=2)

    @tool
    def write_to_project_file(file_path: str, content: str) -> str:
        """
        Write exact content to a project file.
        You must prepare the full updated file content yourself.
        """
        try:
            path = safe_project_path(project_root, file_path)
            path.parent.mkdir(parents=True, exist_ok=True)

            old_content = path.read_text(encoding="utf-8") if path.is_file() else ""
            path.write_text(content, encoding="utf-8")
            changed = old_content != content

            # תיעוד הפעולה (ללא הרצת התקנות כמו pod install)
            audit_recorder.write("PROJECT_FILE_WRITTEN", {
                "file_path": file_path,
                "changed": changed
            })

            return json.dumps({"status": "WRITTEN" if changed else "NO_CHANGES", "file_path": file_path}, indent=2)
        except Exception as e:
            return json.dumps({"status": "FAILED", "error": str(e)}, indent=2)

    agent_tools = [*mcp_tools, list_project_files, read_project_file, write_to_project_file]
    # 5. בניית הפרומפט הנוקשה
    final_execution_prompt = (
        f"User Request:\n"
        f"\"\"\"{user_prompt}\"\"\"\n\n"
        f"Project path: {project_root}\n"
        f"Platform: {platform.upper()}\n"
        f"Make sure to use the correct MCP tools and edit the correct files for {platform.upper()}.\n\n"
        f"You are connected directly to the AppsFlyer MCP tools, and you have generic file tools.\n\n"
        f"Important rules:\n"
        f"1. Use AppsFlyer MCP tools for guidance.\n"
        f"2. MCP tools may return instructions, but they DO NOT edit files for you.\n"
        f"3. You must inspect files with list_project_files and read_project_file.\n"
        f"4. You must apply changes yourself using write_to_project_file.\n"
        f"5. Do not say the task is complete until you have written the updated files.\n"
        f"6. Do not run verification tools before writing changes.\n"
        f"7. You must prepare the full updated file content yourself.\n"
        f"8. After writing, you may call the verification tool once.\n"
        f"9. Do not ask the user to confirm paths. Use the file tools instead.\n"
        f"10. If you need more information or are stuck, simply ask your question clearly in your text response."
    )
    audit_recorder.write("AGENT_PROMPT_GENERATED", {"prompt": final_execution_prompt})
    # 6. יצירת הסוכן
    sdk_agent = create_agent(model=model, tools=agent_tools)
    # 7. החזרת התוצרים ל-Listener
    return {
        "agent": sdk_agent,
        "tools": agent_tools,
        "initial_prompt": final_execution_prompt
    }

    # 7. הפעלת ה-Listener!
    print(":arrows_counterclockwise: Starting Listener Loop...")
    try:
        # קריאה לפונקציה של הצוות שמריצה את הלולאה
        response, updates = invoke_agent_with_listener(
            state=initial_state,
            base_prompt=final_execution_prompt,
            node_name="sdk_integration_agent"
        )

        # תיעוד סיום הריצה
        audit_recorder.write("WORKFLOW_FINISHED", {
            "status": updates.get("test_status", "success"),
            "updates": updates
        })

        return {
            "status": updates.get("test_status", "success"),
            "response": response,
            "updates": updates
        }

    except Exception as e:
        audit_recorder.write("RUN_ERROR", {"error": str(e)})
        return {"status": "error", "error": str(e)}
