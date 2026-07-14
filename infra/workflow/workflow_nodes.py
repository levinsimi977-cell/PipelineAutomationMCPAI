from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import Any, Literal, Optional, TypedDict, get_args

from infra.application.app import run_tasks_3_and_4, setup_environment
from infra.agents.promptGanertorAgent.tools.prompt_agent_core import (
    prompt_agent_node as build_prompts,
)
from infra.agents.compilationAgent.compilation_agent import check_compilation
from infra.agents.sdkAgent.tools.agent import run_sdk_integration_agent

from infra.agents.answerAgent.answer_policy_repository import (
get_answer_policy_repository,
)   
PromptType = Literal["integrate_prompt", "event_prompt", "verify_prompt"]

_PROMPT_SEQUENCE: tuple[PromptType, ...] = get_args(PromptType)

import sys
import asyncio
from infra.agents.AuditRecorder import AuditRecorder
from infra.agents.sdkAgent.tools.agent import run_sdk_integration_agent

from typing import Any, Literal, Optional, TypedDict,get_args

# Resolve the emulator tools directory relative to this file so the import
# works regardless of the working directory the process was launched from.
_TOOLS_DIR = os.path.normpath(
    os.path.join(os.path.dirname(__file__), "..", "agents", "sdkAgent", "tools")
)
if _TOOLS_DIR not in sys.path:
    sys.path.insert(0, _TOOLS_DIR)

from emulator import (
    setup_appium_environment,
    start_appium_server,
    list_android_emulators,
    start_android_emulator,
    list_ios_simulators,
    start_ios_simulator,
    list_devices,
    start_device,
    launch_app_on_device,
)

def _next_prompt_type(current: PromptType) -> Optional[PromptType]:
    """Returns the prompt type that follows `current` in the
    integrate_prompt -> event_prompt -> verify_prompt sequence, or None if
    `current` is already the last one.
    """
    index = _PROMPT_SEQUENCE.index(current)
    if index + 1 < len(_PROMPT_SEQUENCE):
        return _PROMPT_SEQUENCE[index + 1]
    return None

_PROMPT_SEQUENCE: list[PromptType] = [
    "integrate_prompt",
    "event_prompt",
    "verify_prompt",
]


def _next_prompt_type(current: PromptType) -> PromptType | None:
    """Return the next prompt type in the pipeline, or None after verify_prompt."""
    try:
        index = _PROMPT_SEQUENCE.index(current)
    except ValueError:
        return None
    if index + 1 < len(_PROMPT_SEQUENCE):
        return _PROMPT_SEQUENCE[index + 1]
    return None

class PipelineState(TypedDict, total=False):
    """Shared state threaded through every node of the workflow graph."""

    visited_user_actions: bool
    last_prompt_type: PromptType
    current_node: str
    next_node: str
    nodes_log: list
    incoming_question: str | None
    audit_path: str
    platform: str
    last_prompt_type: PromptType | None
    prompt_just_run: PromptType
    type_agent: str
    agent_prompts: dict[str, str]
    question_rounds: int
    installation_answers: list[dict[str, Any]]
    test_status: str
    last_agent_message: str
    sandbox_path: str
    platform: str
    audit_recorder: AuditRecorder
    run_id: str
    fail_reason: NotRequired[Any]
    nodes_logs: list[dict[str, Any]]

    # Use-case queue (json_use_case_input -> artifact_generator <-> visual_report loop)
    run_id: str
    selected_use_cases: list[dict]
    use_cases_dir: str
    current_use_case_path: Optional[str]
    current_use_case: dict

    # Emulator node inputs
    action_type: str
    device_id: str
    platform: str
    app_id: str
    remote_url: str

    # Emulator node outputs
    available_devices: str
    nodes_log: str
    driver: Any


def json_use_case_input_node(state: PipelineState) -> PipelineState:
    """Node 1: JSON Use Case Input — User enters use cases (G2)

    Takes the use cases the user picked (`state["selected_use_cases"]`, a
    list of use-case dicts) and materializes each one as its own JSON file
    inside a fresh `use_cases_dir` folder. `current_use_case_path` is set to
    the first file so the loop (`artifact_generator` <-> `visual_report`,
    see `route_from_visual_report`) knows which case to run next.
    """
    selected_cases = state.get("selected_use_cases") or []
    run_id = state.get("run_id", "run")

    use_cases_dir = os.path.join("data", "runs", run_id, "use_cases")
    os.makedirs(use_cases_dir, exist_ok=True)

    case_paths = []
    for index, case in enumerate(selected_cases):
        case_id = case.get("id", str(index))
        case_path = os.path.join(use_cases_dir, f"{case_id}.json")
        with open(case_path, "w", encoding="utf-8") as f:
            json.dump(case, f, ensure_ascii=False, indent=2)
        case_paths.append(case_path)

    state["use_cases_dir"] = use_cases_dir
    state["current_use_case_path"] = case_paths[0] if case_paths else None
    return state


def artifact_generator_node(state: PipelineState) -> PipelineState:
    """Node 2: Artifact Generator — Prompt + RULES + TEST files (G2)

    Loads the use case currently pointed to by `current_use_case_path`
    (set by `json_use_case_input_node` on the first pass, or refreshed by
    `visual_report_node` on every following loop) into `current_use_case`
    so the rest of the pipeline works off the active case's data.
    """
    
    current_path = state.get("current_use_case_path")
    if current_path and os.path.exists(current_path):
        with open(current_path, "r", encoding="utf-8") as f:
            current_use_case = json.load(f)

        state["current_use_case"] = current_use_case
        state["selected_use_cases_path"] = current_path
        state["platform"] = current_use_case.get("platform", state.get("platform", "android"))
        state["app_path"] = state.get("app_path") or current_use_case.get("app_path")
        state["answer_policy"] = current_use_case.get("answer_policy") or {}

        if current_use_case.get("answer_policy"):
            run_id = state.get("run_id", "run")
            repo = get_answer_policy_repository()
            repo.load_from_use_case(run_id, current_use_case)

    return state



async def environment_setup_node(state: PipelineState) -> PipelineState:
    """
    Node 3: Environment Setup — Sandbox + MCP health check (G1)

    Responsibilities:
    1. Create an isolated sandbox copy of the selected application.
    2. Check that the AppsFlyer MCP server is alive.
    3. Validate that the sandbox contains a valid mobile application.
    4. Save all results back into the pipeline state.
    """

    # Step 1: Create the sandbox environment
    environment_result = setup_environment(dict(state))

    if environment_result.get("test_status") == "FAIL":
        return {
            **state,
            **environment_result,
            "environment_setup_status": "FAILED",
        }

    sandbox_path = environment_result.get("sandbox_path")

    if not sandbox_path:
        return {
            **state,
            **environment_result,
            "test_status": "FAIL",
            "environment_setup_status": "FAILED",
            "error_reason": "Environment setup did not return a sandbox_path.",
        }

    # Step 2: MCP check + application validation
    checks_result = await run_tasks_3_and_4(
        app_path=Path(sandbox_path),
        workdir=Path(sandbox_path),
        run_build_check=bool(state.get("run_build_check", False)),
    )

    # Step 3: Determine final status
    checks_succeeded = checks_result.get("status") == "OK"

    final_test_status = "READY" if checks_succeeded else "FAIL"
    environment_setup_status = "OK" if checks_succeeded else "FAILED"

    # Step 4: Merge everything into the existing state
    return {
        **state,
        **environment_result,

        "app_path": sandbox_path,
        "sandbox_path": sandbox_path,

        "environment_setup_status": environment_setup_status,
        "test_status": final_test_status,

        "task_3_mcp_alive": checks_result.get("task_3_mcp_alive"),
        "task_4_application_validation": checks_result.get(
            "task_4_application_validation"
        ),

        "environment_setup_result": checks_result,
    }


def prompt_agent_node(state: PipelineState) -> PipelineState:
    """Node 4: Prompt Agent — enriched structured prompt (G3)"""
    state["prompt_agent_node_status"] = "RUNNING"

    try:
        current_path = state.get("current_use_case_path")
        if current_path:
            state["selected_use_cases_path"] = current_path

        updates = build_prompts(state)
        state.update(updates)

        missing = []
        agent_prompts = state.get("agent_prompts") or {}

        for prompt_name in get_args(PromptType):
            prompt_value = agent_prompts.get(prompt_name)
            if not isinstance(prompt_value, str) or not prompt_value.strip():
                missing.append(f"agent_prompts.{prompt_name}")

        platform = state.get("platform")
        if not isinstance(platform, str) or not platform.strip():
            missing.append("platform")

        if missing:
            state["prompt_agent_node_status"] = "FAIL"
            state["prompt_agent_node_error"] = (
                "Prompt Agent did not save required fields: " + ", ".join(missing)
            )
        else:
            state["prompt_agent_node_status"] = "SUCCESS"
            state.pop("prompt_agent_node_error", None)
    except Exception as exc:
        state["prompt_agent_node_status"] = "FAIL"
        state["prompt_agent_node_error"] = str(exc)

    state["nodes_log"] = [
        *(state.get("nodes_log") or []),
        {
            "node": "prompt_agent",
            "status": state["prompt_agent_node_status"],
            "message": state.get(
                "prompt_agent_node_error",
                "Prompt Agent generated and saved all required prompts.",
            ),
        },
    ]
    return state


def sdk_agent_node(state: PipelineState) -> PipelineState:
    """Node 5: SDK Agent — single node, revisited on every loop of the
    workflow (integration / event / verify passes).

    `last_prompt_type` starts as None. On the first visit it is set to
    integrate_prompt; each successful run advances it to the next prompt
    (integrate → event → verify) for the following visit.
    """
    if state.get("last_prompt_type") is None:
        state["last_prompt_type"] = "integrate_prompt"

    current_prompt_type = state["last_prompt_type"]
    agent_prompts = state["agent_prompts"]
    sandbox_path = state["sandbox_path"]
    platform = state["platform"]
    audit_recorder = state["audit_recorder"]
    run_id = state["run_id"]

    user_prompt = agent_prompts[current_prompt_type]

    result = asyncio.run(
        run_sdk_integration_agent(
            project_root_str=sandbox_path,
            platform=platform,
            user_prompt=user_prompt,
            audit_recorder=audit_recorder,
            run_id=run_id,
        )
    )

    state["type_agent"] = "sdk_agent"
    state["last_agent_message"] = user_prompt
    state["prompt_just_run"] = current_prompt_type

    node_succeeded = result.get("status") != "FAIL"
    node_log: dict[str, Any] = {
        "node": "sdk_agent",
        "status": "Success" if node_succeeded else "Failure",
        "prompt_type": current_prompt_type,
    }
    if not node_succeeded and "reason" in result:
        node_log["reason"] = result["reason"]

    nodes_logs = list(state.get("nodes_logs") or [])
    nodes_logs.append(node_log)
    state["nodes_log"] = [*(state.get("nodes_log") or []), node_log]


    if not node_succeeded:
        state["test_status"] = "FAIL"
        if "reason" in result:
            state["fail_reason"] = result["reason"]
    else:
        next_prompt_type = _next_prompt_type(current_prompt_type)
        if next_prompt_type is not None:
            state["last_prompt_type"] = next_prompt_type

    return state


def compilation_check_node(state: PipelineState) -> PipelineState:
    """Node 6: Compilation Check — build validation before run (G4)

    Wrapper around `check_compilation` (compilation_agent.py): builds the
    sandboxed app copy (xcodebuild/gradlew) and merges compilation_passed /
    compilation_result / audit_events back into state, plus the standard
    per-node bookkeeping (current_node, next_node, visited_*, nodes_log).
    """
    platform = state.get("platform") or state.get("prompt_platform")
    result = check_compilation({**state, "platform": platform})
    state.update(result)

    state["current_node"] = "compilation_check"
    state["next_node"] = "emulator"
    state["visited_compilation_check"] = True
    state["nodes_log"] = [
        *(state.get("nodes_log") or []),
        {
            "node": "compilation_check",
            "status": "SUCCESS" if result.get("compilation_passed") else "FAIL",
        },
    ]
    return state


def emulator_node(state: PipelineState) -> dict:
    """Node 7: Emulator — launch compiled app (G5)

    Runs the full launch sequence in a single call:
      1. setup_appium_environment  — install Appium + platform driver
      2. start_appium_server       — start Appium server on port 4723
      3. start_device(device_id)   — boot the target device / simulator
      4. launch_app_on_device      — connect Appium and activate the app

    Required state keys:
      - device_id      : AVD name (Android) or simulator UUID (iOS)
      - os_type        : "android" or "ios"
      - app_identifier : package name (Android) or bundle ID (iOS)

    Optional state keys:
      - remote_url     : Appium server URL (default: http://127.0.0.1:4723)

    Returns only the fields that changed so LangGraph can merge them into
    the shared graph state.
    """
    device_id = state.get("device_id")
    platform = state.get("platform")
    app_id = state.get("app_id")
    remote_url = state.get("remote_url", "http://127.0.0.1:4723")

    steps: list[str] = []
    driver_instance: Any = None
    devices_listing: str = ""

    try:
        # Step 1 — install Appium + platform driver
        steps.append(f"[setup] {setup_appium_environment()}")

        # Step 2 — start Appium server
        steps.append(f"[server] {start_appium_server()}")

        # Step 3 — list available devices/simulators on the current platform
        devices_listing = list_devices()
        steps.append(f"[devices] {devices_listing}")

        # Step 4 — boot the specific device
        if not device_id:
            steps.append("[device] Skipped: device_id is missing from state.")
        else:
            steps.append(f"[device] {start_device(device_id)}")

            # Step 5 — connect Appium and launch the app
            if not all([os_type, app_identifier]):
                steps.append("[launch] Skipped: os_type or app_identifier is missing from state.")
            else:
                driver_result = launch_app_on_device(os_type, device_id, app_identifier, remote_url)
                if isinstance(driver_result, str):
                    steps.append(f"[launch] {driver_result}")
                else:
                    driver_instance = driver_result
                    steps.append("[launch] App launched successfully, driver is ready.")

    except Exception as e:
        steps.append(f"[error] Node execution failed: {str(e)}")

    return {
        "available_devices": devices_listing,
        "execution_result": "\n".join(steps),
        "driver": driver_instance,
    }


def deep_link_node(state: PipelineState) -> PipelineState:
    """Node 9: Deep Link — verify deep link behavior (G5)"""
    return state


def test_runner_node(state: PipelineState) -> PipelineState:
    """Node 10: Test Runner — full test suite execution (G2)"""
    return state


def visual_report_node(state: PipelineState) -> PipelineState:
    """Node 11: Visual Report — HTML audit dashboard (G2/4)

    Once the report for the current use case is done, checks whether more
    use-case files are still waiting in `use_cases_dir`:

    - If there are more files -> delete the file just used and point
      `current_use_case_path` at another remaining file, so the graph loops
      back into `artifact_generator` (see `route_from_visual_report`).
    - If it was the last file -> leave it in place and clear
      `current_use_case_path`, so the graph ends.
    """
    use_cases_dir = state.get("use_cases_dir")
    current_path = state.get("current_use_case_path")

    if use_cases_dir and current_path and os.path.isdir(use_cases_dir):
        remaining = sorted(
            os.path.join(use_cases_dir, name)
            for name in os.listdir(use_cases_dir)
            if name.endswith(".json")
            and os.path.join(use_cases_dir, name) != current_path
        )
        if remaining:
            if os.path.exists(current_path):
                os.remove(current_path)
            state["current_use_case_path"] = remaining[0]
        else:
            state["current_use_case_path"] = None

    return state


def route_from_sdk_agent(state: PipelineState) -> str:
    """Conditional edge out of `sdk_agent`.

    Uses `prompt_just_run` (the prompt that just finished) because
    `last_prompt_type` may already point at the next pass.

    - prompt_just_run == "verify_prompt"                     -> test_runner
    - prompt_just_run in {"integrate_prompt", "event_prompt"} -> compilation_check
    """
    prompt_just_run = state.get("prompt_just_run") or state.get("last_prompt_type")
    if prompt_just_run is None:
        return "compilation_check"
    if prompt_just_run == "verify_prompt":
        return "test_runner"
    return "compilation_check"


def route_from_emulator(state: PipelineState) -> str:
    """Conditional edge out of `emulator`.

    - last_prompt_type == "integrate_prompt"                       -> sdk_agent
    - last_prompt_type == "event_prompt" and visited_user_actions  -> sdk_agent
    - last_prompt_type == "event_prompt" and not visited_user_actions -> user_actions
    """
    if state["last_prompt_type"] == "event_prompt" and not state["visited_user_actions"]:
        return "user_actions"
    return "sdk_agent"


def route_from_visual_report(state: PipelineState) -> str:
    """Conditional edge out of `visual_report`.

    - `current_use_case_path` still set -> another use case is waiting in
      `use_cases_dir`, loop back to `artifact_generator`.
    - `current_use_case_path` is empty/None -> no use cases left, end the run.
    """
    if state.get("current_use_case_path"):
        return "artifact_generator"
    return "end"
