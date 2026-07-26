import os
import shutil
import subprocess
import json
import uuid
from pathlib import Path
from typing import Any, Dict, Optional
from langchain_openai import ChatOpenAI
from langchain_mcp_adapters.client import MultiServerMCPClient
from langchain.agents import create_agent
from langchain_core.tools import tool
from langgraph.checkpoint.memory import MemorySaver
from infra.load_env import get_app_id_for_platform, get_dev_key, load_project_env
from infra.agents.AuditRecorder import AuditRecorder
# Classifies each finished turn's Memory (SUCCESS/FAIL/QUESTION), answers
# questions, and records everything to AuditRecorder. See llm_listener.py.
from infra.listener.llm_listener import listener_on_agent_turn

load_project_env(override=True)
APP_ID = os.getenv("APP_ID", "sQ84wpdxRTR4RMCaE9YqS4")
# Safety net: caps how many sdk_agent.ainvoke() turns a single call to
# run_sdk_integration_agent() may take before giving up.
MAX_TURNS = 15
# CocoaPods can take a while to resolve/download on a cold cache.
POD_INSTALL_TIMEOUT_SECONDS = 180
# infra/agents/sdkAgent/tools/agent.py -> project root is 4 levels up
_PROJECT_ROOT = Path(__file__).resolve().parents[4]
_MAIN_RULES_PATH = _PROJECT_ROOT / "data" / "rules" / "sdk-agent-main-rules.json"
def _load_agent_rules_text() -> str:
    """Loads the agent's ground rules from data/rules/sdk-agent-main-rules.json
    and joins them into the exact block that used to be hardcoded inline in
    final_execution_prompt.
    """
    with open(_MAIN_RULES_PATH, "r", encoding="utf-8") as f:
        rules_data = json.load(f)
    rules = rules_data["prompt"]["important_rules"]
    return "\n".join(f"{r['number']}. {r['text']}" for r in rules)


def _tool_accepts_device_id(mcp_tool: Any) -> bool:
    """True when this MCP tool's schema declares a `deviceId` argument."""
    schema = getattr(mcp_tool, "args_schema", None)
    if schema is None:
        return False
    fields = getattr(schema, "model_fields", None)
    if fields is None:
        fields = getattr(schema, "__fields__", None) or {}
    return "deviceId" in fields


def _bind_live_device_id(mcp_tools: list, device_id_holder: Dict[str, Any]) -> None:
    """Forces every device-aware MCP tool's `deviceId` kwarg from `device_id_holder["device_id"]`
    (a mutable container refreshed live by run_sdk_integration_agent) instead of trusting the LLM's
    guess, since tools are built once during integrate_prompt, before a real device is known."""
    for mcp_tool in mcp_tools:
        if not _tool_accepts_device_id(mcp_tool):
            continue
        original = mcp_tool.coroutine
        if original is None:
            continue

        async def _coroutine_with_live_device_id(_original=original, **kwargs: Any) -> Any:
            current_device_id = device_id_holder.get("device_id")
            if current_device_id:
                kwargs["deviceId"] = current_device_id
            else:
                kwargs.pop("deviceId", None)
            return await _original(**kwargs)

        mcp_tool.coroutine = _coroutine_with_live_device_id


def safe_project_path(project_root: Path, requested_path: str) -> Path:
    """Sandbox guard: resolves requested_path and rejects it if it escapes project_root.
    Raises: ValueError: If the resolved path is outside project_root.
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
# Session registry: agent_id -> {agent, tools, turn_offset}.
# Keeps the built agent (and its in-memory checkpointer) alive across
# separate calls, so a repeated agent_id resumes instead of starting fresh.
# agent_id has no relation to the pipeline run_id or the sandbox path -- it
# identifies only "which agent conversation", nothing else.
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
    *,
    app_id: str | None = None,
    dev_key: str | None = None,
    device_id_holder: Dict[str, Any] | None = None,
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
    resolved_dev_key = dev_key or get_dev_key()
    resolved_app_id = app_id or get_app_id_for_platform(platform_lower) or APP_ID
    if not openai_api_key or not resolved_dev_key:
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
            "env": {"APP_ID": resolved_app_id, "DEV_KEY": resolved_dev_key},
        }
    })
    # get_tools() spawns/handshakes with the MCP subprocess - a communication
    # failure here (npx missing, server crash on startup, timeout, ...) is a
    # transport error, not a tool-level failure, so it is recorded distinctly
    # from MCP_TOOL_RESULT (see MCP_TRANSPORT_ERROR below for the in-turn case).
    try:
        mcp_tools = await mcp_client.get_tools()
    except Exception as exc:
        audit_recorder.write("MCP_CONNECTION_FAILED", {
            "stage": "startup",
            "error_type": type(exc).__name__,
            "error": str(exc),
        })
        raise RuntimeError(
            f"Failed to connect to AppsFlyer MCP server: {exc}"
        ) from exc
    if device_id_holder is not None:
        _bind_live_device_id(mcp_tools, device_id_holder)
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
    def run_pod_install() -> str:
        """iOS only: run `pod install` in the directory containing the
        project's Podfile. Call this immediately after writing/updating
        the Podfile — do NOT just tell the user to run it manually.
        Without this, the CocoaPods dependency you added is never
        actually resolved/linked and the later compilation check fails.
        """
        if platform_lower != "ios":
            return json.dumps({
                "status": "SKIPPED",
                "reason": "run_pod_install is iOS-only; Android dependencies are resolved by Gradle at build time.",
            }, indent=2)
        if shutil.which("pod") is None:
            return json.dumps({
                "status": "FAILED",
                "reason": "`pod` (CocoaPods) is not installed/available on this machine.",
            }, indent=2)
        podfile_matches = list(project_root.rglob("Podfile"))
        if not podfile_matches:
            return json.dumps({
                "status": "FAILED",
                "reason": "No Podfile found under the project root. Write the Podfile first.",
            }, indent=2)
        podfile_dir = podfile_matches[0].parent
        try:
            result = subprocess.run(
                ["pod", "install"],
                cwd=str(podfile_dir),
                capture_output=True,
                text=True,
                timeout=POD_INSTALL_TIMEOUT_SECONDS,
            )
        except subprocess.TimeoutExpired:
            return json.dumps({
                "status": "FAILED",
                "reason": f"`pod install` exceeded the {POD_INSTALL_TIMEOUT_SECONDS}s timeout.",
            }, indent=2)
        except Exception as e:
            return json.dumps({"status": "FAILED", "error": str(e)}, indent=2)
        succeeded = result.returncode == 0
        audit_recorder.write("POD_INSTALL_RUN", {
            "cwd": str(podfile_dir),
            "return_code": result.returncode,
            "succeeded": succeeded,
        })
        return json.dumps({
            "status": "OK" if succeeded else "FAILED",
            "return_code": result.returncode,
            "stdout_tail": (result.stdout or "")[-2000:],
            "stderr_tail": (result.stderr or "")[-2000:],
        }, indent=2)

    @tool
    def write_events_manifest(manifest_json: str) -> str:
        """After wiring in-app event UI, write events.wired.json in the project for Appium.

        manifest_json must be a JSON object: {"platform": ..., "appPackage"/"bundleId": ...,
        "mainActivity" (android): ..., "events": [{"eventName": "af_x", "triggerId": "af_trigger_af_x",
        "layoutFile": "<path to the UI file, relative to the project root, where triggerId was wired
        as android:contentDescription / accessibilityIdentifier>"}]}. layoutFile is verified against
        the real file on disk -- this call fails if triggerId isn't actually found in it."""
        try:
            data = json.loads(manifest_json)
            events = data.get("events") or []
            if data.get("platform") != platform_lower or not events:
                raise ValueError("platform must match and events must not be empty")
            for event in events:
                name = event.get("eventName", "")
                trigger_id = event.get("triggerId", "")
                layout_file = event.get("layoutFile", "")
                if not name.startswith("af_") or trigger_id != f"af_trigger_{name}":
                    raise ValueError(f"invalid event wiring for {name}")
                # Don't just trust the agent's claim -- confirm triggerId was
                # actually written into the UI file (android:contentDescription /
                # accessibilityIdentifier), the way "invalid event wiring" above
                # already refuses to trust a made-up triggerId string.
                if not layout_file:
                    raise ValueError(f"layoutFile is required for {name}")
                layout_path = safe_project_path(project_root, layout_file)
                if not layout_path.exists() or trigger_id not in layout_path.read_text(encoding="utf-8", errors="ignore"):
                    raise ValueError(
                        f"triggerId {trigger_id!r} not found in {layout_file} -- wire it into the "
                        f"UI (android:contentDescription / accessibilityIdentifier) before calling this tool"
                    )
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
    agent_tools = [
        *mcp_tools,
        list_project_files,
        read_project_file,
        write_to_project_file,
        run_pod_install,
        write_events_manifest,
    ]
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
        f"{_load_agent_rules_text()}\n"
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
    state: Dict[str, Any],      # The workflow's PipelineState - agent_id is read from/written to it directly
    project_root_str: str,
    platform: str,
    user_prompt: str,
    audit_recorder: AuditRecorder,
) -> Dict[str, Any]:
    """Orchestrates the SDK installation.
    agent_id lives in state["agent_id"] (initialized to None by the workflow) and
    has no relation to the pipeline run_id or the sandbox path:
    - state["agent_id"] is None  -> no agent exists yet for this use case; build
      one, mint a fresh agent_id, and write it back into state["agent_id"] so
      later calls (verify pass, etc.) can resume this same conversation.
    - state["agent_id"] is not None  -> an agent is expected to already exist; look
      it up in _AGENT_SESSIONS. If it is missing (e.g. process restarted and
      the in-memory registry was lost), fail explicitly instead of silently
      starting a new, unrelated conversation under the old id.
    Loops turns via sdk_agent.ainvoke(), delegating classification/audit to
    listener_on_agent_turn, until it returns "done" or "fail".
    Returns:
        dict with "status" ("SUCCESS"/"FAIL"), "agent_id", "turns", and
        "reason" (always a string) on failure.
    """
    agent_id = state.get("agent_id")
    resolved_app_id = state.get("app_id") or get_app_id_for_platform(platform)
    resolved_dev_key = state.get("dev_key") or get_dev_key()
    if agent_id is None:
        # Build a new agent/session; device_id_holder is refreshed from state on
        # every call below since emulator_node sets state["device_id"] later.
        agent_id = str(uuid.uuid4())
        device_id_holder: Dict[str, Any] = {"device_id": state.get("device_id")}
        setup = await create_sdk_integration_agent(
            project_root_str, platform, user_prompt, audit_recorder,
            app_id=resolved_app_id, dev_key=resolved_dev_key, device_id_holder=device_id_holder,
        )
        session = {
            "agent": setup["agent"], "tools": setup["tools"],
            "turn_offset": 0, "device_id_holder": device_id_holder,
        }
        _AGENT_SESSIONS[agent_id] = session
        state["agent_id"] = agent_id
        prompt = setup["initial_prompt"]
    else:
        session = _AGENT_SESSIONS.get(agent_id)
        if session is None:
            audit_recorder.write("AGENT_SESSION_LOST", {"agent_id": agent_id})
            reason = f"Conversation for agent_id={agent_id} no longer exists (session lost)."
            return {"status": "FAIL", "agent_id": agent_id, "reason": reason}
        prompt = user_prompt
        # Refresh in case emulator_node has changed state["device_id"] since.
        device_id_holder = session.get("device_id_holder")
        if device_id_holder is not None:
            device_id_holder["device_id"] = state.get("device_id")
    sdk_agent = session["agent"]
    # inner_state IS the outer PipelineState (same object, not a copy) - so it
    # already has run_id, app_path, etc. Mutations here persist back to the
    # workflow because sdk_agent_node returns this same `state` object.
    inner_state = state
    # Fixed thread_id for this agent_id's lifetime, so the checkpointer
    # recognizes all calls below as belonging to the same conversation.
    config = {"configurable": {"thread_id": f"sdk_agent_{agent_id}"}}
    for i in range(MAX_TURNS):
        turn_index = session["turn_offset"] + i
        # One turn: awaits until the LLM stops requesting tools.
        # A tool that responds with isError=True still returns normally here
        # (langchain_mcp_adapters turns that into a ToolMessage(status="error")
        # - see llm_listener.py). What we catch here is the other case: the MCP
        # subprocess/connection itself dying mid-turn (crash, broken pipe,
        # timeout, ...), which surfaces as a raw exception out of ainvoke()
        # instead of a normal ToolMessage.
        try:
            result = await sdk_agent.ainvoke(
                {"messages": [("user", prompt)]}, config=config
            )
        except Exception as exc:
            audit_recorder.write("MCP_TRANSPORT_ERROR", {
                "turn_index": turn_index,
                "error_type": type(exc).__name__,
                "error": str(exc),
            })
            session["turn_offset"] = turn_index + 1
            inner_state["sdk_agent_status"] = "FAIL"
            return {
                "status": "FAIL",
                "agent_id": agent_id,
                "turns": turn_index + 1,
                "reason": f"MCP server transport error on turn {turn_index}: {exc}",
            }
        all_messages = result["messages"]  # Full accumulated Memory, including prior turns
        # Listener classifies the turn, answers questions if needed, and audits it.
        action, next_prompt, updates = listener_on_agent_turn(
            inner_state, "sdk_agent", prompt, all_messages, audit_recorder,
        )
        if action == "done":
            session["turn_offset"] = turn_index + 1
            inner_state["sdk_agent_status"] = "SUCCESS"
            return {"status": "SUCCESS", "agent_id": agent_id, "turns": turn_index + 1}
        if action == "fail":
            session["turn_offset"] = turn_index + 1
            inner_state["sdk_agent_status"] = "FAIL"
            return {"status": "FAIL", "agent_id": agent_id, "turns": turn_index + 1, "reason": str(updates)}
        # "continue": either a question was answered (answer is in next_prompt) or we just proceed.
        prompt = next_prompt
    # Hit MAX_TURNS without done/fail: stop instead of looping forever.
    session["turn_offset"] += MAX_TURNS
    audit_recorder.write("INSTALLATION_TIMEOUT", {"max_turns": MAX_TURNS})
    inner_state["sdk_agent_status"] = "FAIL"
    return {"status": "FAIL", "agent_id": agent_id, "reason": f"Exceeded max turns ({MAX_TURNS})"}
# ============================================================================
# Step C - Teardown (call once the conversation is truly finished, e.g.
# after the verify_prompt pass, so the session doesn't stay alive forever)
# ============================================================================
def close_sdk_integration_agent(state: Dict[str, Any], audit_recorder: Optional[AuditRecorder] = None) -> None:
    """Frees the session (agent, tools, checkpointer memory) held for
    state["agent_id"] - same state-based lookup as run_sdk_integration_agent.

    Safe to call even if agent_id is None or already closed - it is then
    simply a no-op. Only removes the entry from _AGENT_SESSIONS; does not
    clear state["agent_id"] (the caller may still want it around for logging).
    """
    agent_id = state.get("agent_id")
    session = _AGENT_SESSIONS.pop(agent_id, None)
    if session is not None and audit_recorder is not None:
        audit_recorder.write("AGENT_SESSION_CLOSED", {"agent_id": agent_id})
