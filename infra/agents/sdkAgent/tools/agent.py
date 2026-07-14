import os
import json
from pathlib import Path
from typing import Any, Dict
from dotenv import load_dotenv
from langchain_openai import ChatOpenAI
from langchain_mcp_adapters.client import MultiServerMCPClient
from langchain.agents import create_agent
from langchain_core.tools import tool
from langgraph.checkpoint.memory import MemorySaver
from infra.agents.AuditRecorder import AuditRecorder
# Classifies each finished turn's Memory (SUCCESS/FAIL/QUESTION), answers
# questions, and records everything to AuditRecorder. See llm_listener.py.
from infra.listener.llm_listener import listener_on_agent_turn
# Loads API keys from the .env file located next to this module
load_dotenv(Path(__file__).resolve().parent / ".env", override=True)
APP_ID = os.getenv("APP_ID", "id1512793879")
def safe_project_path(project_root: Path, requested_path: str) -> Path:
    """Sandbox guard: resolves requested_path and rejects it if it escapes project_root.

    Raises:
        ValueError: If the resolved path is outside project_root.
    """
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
# ============================================================================
# Session registry: run_id -> {agent, tools, state, turn_offset}.
# Keeps the built agent (and its in-memory checkpointer) alive across
# separate calls, so a repeated run_id resumes instead of starting fresh.
# Limitation: in-process only, lost on restart (swap for a DB-backed
# checkpointer + registry if persistence across restarts is ever needed).
# ============================================================================
_AGENT_SESSIONS: Dict[str, Dict[str, Any]] = {}
# ============================================================================
# Step A - Build only (Setup)
# ============================================================================
async def create_sdk_integration_agent(
    project_root_str: str,        # Path to the project's Sandbox directory (as a string)
    platform: str,                 # 'ios' or 'android'
    user_prompt: str,               # The original user request (e.g. "install the SDK")
    audit_recorder: AuditRecorder,  # The audit object - every step here is recorded to it
) -> Dict[str, Any]:
    """Builds (but does not run) the SDK integration agent: loads API keys,
    connects to the AppsFlyer MCP server, registers file tools, builds the
    execution prompt, and wires up a checkpointer for multi-turn memory.

    Returns:
        dict with "agent", "tools", and "initial_prompt".
    """
    project_root = Path(project_root_str)
    platform_lower = platform.lower()
    openai_api_key = os.getenv("OPENAI_API_KEY")
    dev_key = os.getenv("APPSFLYER_DEV_KEY")
    if not openai_api_key or not dev_key:
        raise RuntimeError("Missing OPENAI_API_KEY or APPSFLYER_DEV_KEY in .env")
    model = ChatOpenAI(
        model="gpt-5.1",
        api_key=openai_api_key,
        temperature=1.5,
    )
    # Runs AppsFlyer's MCP server as a child process over stdio;
    # get_tools() is async because it talks to that external process.
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
    # --------------------------------------------------------------------
    # Custom (non-MCP) file tools, letting the agent read/write files
    # itself inside the isolated Sandbox.
    # --------------------------------------------------------------------
    @tool
    def list_project_files() -> str:
        """List relevant editable files in the project. Use this before deciding which file to read or edit."""
        if platform_lower == 'ios':
            allowed_suffixes = {".swift", ".plist", ".podspec", ".pbxproj", ".xcodeproj", ".xcworkspace"}
            allowed_names = {"Podfile", "Package.swift"}
        elif platform_lower == 'android':
            allowed_suffixes = {".java", ".kt", ".xml", ".gradle", ".kts", ".properties"}
            allowed_names = {"AndroidManifest.xml"}
        else:
            return json.dumps({"error": f"Unsupported platform: {platform_lower}"})
        files = [
            str(p.relative_to(project_root)) for p in project_root.rglob("*")
            if p.is_file() and (p.name in allowed_names or p.suffix in allowed_suffixes)
        ]
        return json.dumps({"project_root": str(project_root), "files": files}, ensure_ascii=False, indent=2)
    @tool
    def read_project_file(file_path: str) -> str:
        """Read a project file (relative to project root)."""
        try:
            path = safe_project_path(project_root, file_path)  # Sandbox guard
            if not path.is_file():
                return json.dumps({"status": "FAILED", "reason": "Not a file or does not exist"}, indent=2)
            return json.dumps({"status": "OK", "content": path.read_text(encoding="utf-8")}, indent=2)
        except Exception as e:
            return json.dumps({"status": "FAILED", "error": str(e)}, indent=2)
    @tool
    def write_to_project_file(file_path: str, content: str) -> str:
        """Write exact content to a project file. You must prepare the full updated file content yourself."""
        try:
            path = safe_project_path(project_root, file_path)  # Sandbox guard
            path.parent.mkdir(parents=True, exist_ok=True)
            old_content = path.read_text(encoding="utf-8") if path.is_file() else ""
            path.write_text(content, encoding="utf-8")
            changed = old_content != content
            # Recorded immediately, not at end-of-turn, since a file write
            # is a sensitive action worth auditing the moment it happens.
            audit_recorder.write("PROJECT_FILE_WRITTEN", {
                "file_path": file_path,
                "changed": changed,
            })
            return json.dumps({"status": "WRITTEN" if changed else "NO_CHANGES", "file_path": file_path}, indent=2)
        except Exception as e:
            return json.dumps({"status": "FAILED", "error": str(e)}, indent=2)

    @tool
    def write_events_manifest(manifest_json: str) -> str:
        """After wiring in-app event UI, write events.wired.json in the project for Appium."""
        try:
            data = json.loads(manifest_json)
            events = data.get("events") or []
            if data.get("platform") != platform_lower or not events:
                raise ValueError("platform must match and events must not be empty")
            for event in events:
                name = event.get("eventName", "")
                trigger_id = event.get("triggerId", "")
                if not name.startswith("af_") or trigger_id != f"af_trigger_{name}":
                    raise ValueError(f"invalid event wiring for {name}")
            if platform_lower == "android" and not (data.get("appPackage") and data.get("mainActivity")):
                raise ValueError("android requires appPackage and mainActivity")
            if platform_lower == "ios" and not data.get("bundleId"):
                raise ValueError("ios requires bundleId")
            manifest_path = safe_project_path(project_root, "events.wired.json")
            manifest_path.parent.mkdir(parents=True, exist_ok=True)
            manifest_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
            return json.dumps({
                "status": "OK",
                "file_path": "events.wired.json",
                "event_count": len(events),
            }, indent=2)
        except Exception as e:
            return json.dumps({"status": "FAILED", "error": str(e)}, indent=2)

    # All tools (MCP + files) together - these are the tools registered to the agent
    agent_tools = [*mcp_tools, list_project_files, read_project_file, write_to_project_file, write_events_manifest]
    # --------------------------------------------------------------------
    # Ground rules for the agent. Rule 13 is critical: it lets the
    # orchestrator below detect true turn completion via "STATUS: SUCCESS".
    # --------------------------------------------------------------------
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
        f"9. Do not ask the user to confirm paths. Use the file tools instead.\n"
        f"10. If you need more information or are stuck, simply ask your question clearly in your text response.\n"
        f"11. After calling integrateSdk and before running any verification tool, explicitly state that the integration has finished successfully, and instruct the user to prepare for emulator launch.\n"
        f"12. Once the verification tool confirms success, declare that the process is complete and instruct the user to launch the emulator.\n"
        f"13. At the end of each response, return the status you got like this: STATUS: SUCCESS/FAILURE/QUESTION\n"
        f"14. When creating in-app events: wire triggerId in UI (Android contentDescription / iOS accessibilityIdentifier), "
        f"then call write_events_manifest before finishing.\n"
    )
    audit_recorder.write("AGENT_PROMPT_GENERATED", {"prompt": final_execution_prompt})
    # The checkpointer is the agent's memory: it lets repeated .ainvoke()
    # calls with the same thread_id continue one conversation instead of
    # each call starting fresh. Kept alive across calls via _AGENT_SESSIONS.
    checkpointer = MemorySaver()
    sdk_agent = create_agent(model=model, tools=agent_tools, checkpointer=checkpointer)
    return {
        "agent": sdk_agent,
        "tools": agent_tools,
        "initial_prompt": final_execution_prompt,
    }
# ============================================================================
# Step B - The Orchestrator (this is the single entry point external code calls!)
# ============================================================================
async def run_sdk_integration_agent(
    project_root_str: str,
    platform: str,
    user_prompt: str,
    audit_recorder: AuditRecorder,
    run_id: str,               # Unique run identifier - both the session key and the thread_id
    max_turns: int = 15,       # Safety net: prevents a theoretical infinite loop
) -> Dict[str, Any]:
    """Orchestrates the SDK installation: reuses the session for run_id if one
    exists (same agent + memory), otherwise builds one. Loops turns via
    sdk_agent.ainvoke(), delegating classification/audit to
    listener_on_agent_turn, until it returns "done" or "fail".

    Returns:
        dict with "status" ("SUCCESS"/"FAIL"), "turns", and "reason" on failure.
    """
    session = _AGENT_SESSIONS.get(run_id)
    if session is None:
        # No session yet for this run_id: build a new agent (one-time setup).
        setup = await create_sdk_integration_agent(
            project_root_str, platform, user_prompt, audit_recorder
        )
        session = {
            "agent": setup["agent"],
            "tools": setup["tools"],
            "state": {"platform": platform},
            "turn_offset": 0,   # Keeps turn_index continuous across separate calls
        }
        _AGENT_SESSIONS[run_id] = session
        prompt = setup["initial_prompt"]
    else:
        # Session already exists: continue the same agent with a new prompt.
        prompt = user_prompt
    sdk_agent = session["agent"]
    state = session["state"]
    # Fixed thread_id for this run_id's lifetime, so the checkpointer
    # recognizes all calls below as belonging to the same conversation.
    config = {"configurable": {"thread_id": f"sdk_agent_{run_id}"}}
    for i in range(max_turns):
        turn_index = session["turn_offset"] + i
        # One turn: awaits until the LLM stops requesting tools.
        result = await sdk_agent.ainvoke(
            {"messages": [("user", prompt)]}, config=config
        )
        all_messages = result["messages"]  # Full accumulated Memory, including prior turns
        # Listener classifies the turn, answers questions if needed, and audits it.
        action, next_prompt, updates = listener_on_agent_turn(
            state, "sdk_agent", prompt, all_messages, audit_recorder,
        )
        if action == "done":
            session["turn_offset"] = turn_index + 1
            state["status"] = "SUCCESS"
            return {"status": "SUCCESS", "turns": turn_index + 1}
        if action == "fail":
            session["turn_offset"] = turn_index + 1
            state["status"] = "FAIL"
            return {"status": "FAIL", "turns": turn_index + 1, "reason": updates}
        # "continue": either a question was answered (answer is in next_prompt) or we just proceed.
        prompt = next_prompt
    # Hit max_turns without done/fail: stop instead of looping forever.
    session["turn_offset"] += max_turns
    audit_recorder.write("INSTALLATION_TIMEOUT", {"max_turns": max_turns})
    state["status"] = "FAIL"
    return {"status": "FAIL", "reason": f"Exceeded max turns ({max_turns})"}
