from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any, Literal, Optional, TypedDict, get_args

from typing_extensions import NotRequired

from infra.application.app import run_tasks_3_and_4, setup_environment
from infra.agents.promptGanertorAgent.tools.prompt_agent_core import (
    prompt_agent_node as build_prompts,
)
from infra.agents.compilationAgent.compilation_agent import check_compilation
from infra.agents.sdkAgent.tools.agent import (
    close_sdk_integration_agent,
    run_sdk_integration_agent,
)
from infra.agents.AuditRecorder import AuditRecorder
from infra.agents.answerAgent.answer_policy_repository import (
    get_answer_policy_repository,
)
from infra.use_case_service.repositories.run_repository import RUNS_DIR


# Resolve emulator tools directory relative to this file
_TOOLS_DIR = os.path.normpath(
    os.path.join(os.path.dirname(__file__), "..", "agents", "sdkAgent", "tools")
)

if _TOOLS_DIR not in sys.path:
    sys.path.insert(0, _TOOLS_DIR)


from emulator import (
    setup_appium_environment,
    start_appium_server,
    list_devices,
    start_device,
    launch_app_on_device,
)


PromptType = Literal[
    "integrate_prompt",
    "event_prompt",
    "verify_prompt",
]


_PROMPT_SEQUENCE: list[PromptType] = [
    "integrate_prompt",
    "event_prompt",
    "verify_prompt",
]


def _next_prompt_type(current: PromptType) -> PromptType | None:
    """
    Return the next prompt type in the pipeline.
    """
    try:
        index = _PROMPT_SEQUENCE.index(current)
    except ValueError:
        return None

    if index + 1 < len(_PROMPT_SEQUENCE):
        return _PROMPT_SEQUENCE[index + 1]

    return None


def _is_pipeline_fail(state: PipelineState) -> bool:
    """True when the pipeline should stop normal flow and go to test_runner."""
    return state.get("test_status") == "FAIL"


def route_after_node(state: PipelineState, *, on_success: str) -> str:
    """Shared gate: FAIL -> test_runner, otherwise the normal next node."""
    if _is_pipeline_fail(state):
        return "test_runner"
    return on_success


def route_after_json_use_case_input(state: PipelineState) -> str:
    return route_after_node(state, on_success="artifact_generator")


def route_after_artifact_generator(state: PipelineState) -> str:
    return route_after_node(state, on_success="environment_setup")


def route_after_environment_setup(state: PipelineState) -> str:
    return route_after_node(state, on_success="prompt_agent")


def route_after_prompt_agent(state: PipelineState) -> str:
    return route_after_node(state, on_success="sdk_agent")


def route_after_compilation_check(state: PipelineState) -> str:
    return route_after_node(state, on_success="emulator")


def route_after_user_actions(state: PipelineState) -> str:
    return route_after_node(state, on_success="deep_link")


def route_after_deep_link(state: PipelineState) -> str:
    return route_after_node(state, on_success="sdk_agent")


class PipelineState(TypedDict, total=False):
    """
    Shared state threaded through every node of the workflow graph.
    """

    # ==================================================
    # General use case information
    # ==================================================

    user_id: NotRequired[str]

    use_case_ids: NotRequired[list[str]]

    selected_use_cases: NotRequired[list[dict]]

    selected_use_cases_path: NotRequired[str]

    use_case_count: NotRequired[int]

    primary_use_case_id: NotRequired[str]

    primary_use_case_name: NotRequired[str]


    # ==================================================
    # Use-case queue
    # ==================================================

    use_cases_dir: NotRequired[str]

    current_use_case_path: NotRequired[Optional[str]]

    current_use_case: NotRequired[dict]


    run_id: NotRequired[str]

    answer_policy: NotRequired[dict]


    # ==================================================
    # Application information
    # ==================================================

    app_id: NotRequired[str]

    dev_key: NotRequired[str]

    platform: NotRequired[str]

    app_status: NotRequired[str]

    remote_url: NotRequired[str]

    app_path: NotRequired[str]

    original_app_path: NotRequired[str]

    sandbox_path: NotRequired[str]


    dev_key_configured: NotRequired[bool]

    dev_key_source: NotRequired[str]


    # ==================================================
    # MCP
    # ==================================================

    mcp_health_check: NotRequired[bool]

    mcp_tools_available: NotRequired[list]

    mcp_tools_call: NotRequired[list]

    mcp_tools_used: NotRequired[list]

    mcp_tools_used_success: NotRequired[bool]

    mcp_integration_text: NotRequired[str]


    # ==================================================
    # Agent management
    # ==================================================

    agent_id: NotRequired[int]

    agent_model: NotRequired[str]

    type_agent: NotRequired[str]

    agent_prompts: NotRequired[dict[str, str]]

    last_prompt_type: NotRequired[PromptType]

    prompt_just_run: NotRequired[PromptType]


    question_rounds: NotRequired[int]

    installation_answers: NotRequired[list]

    last_agent_message: NotRequired[str]


    audit_recorder: NotRequired[Any]


    # ==================================================
    # User actions
    # ==================================================

    prompt_agent_answer: NotRequired[str]

    visited_user_actions: NotRequired[bool]


    # ==================================================
    # Execution status
    # ==================================================

    test_status: NotRequired[str]

    fail_reason: NotRequired[Any]

    emulator_checking: NotRequired[bool]


    # ==================================================
    # Emulator
    # ==================================================

    available_devices: NotRequired[list]

    driver: NotRequired[Any]

    device_id: NotRequired[str]


    # ==================================================
    # Compilation
    # ==================================================

    compilation_passed: NotRequired[bool]

    compilation_result: NotRequired[Any]

    audit_events: NotRequired[list]


    # ==================================================
    # Logs
    # ==================================================

    nodes_log: NotRequired[list]

    nodes_logs: NotRequired[list[dict[str, Any]]]


    json_use_case_input_is_visited: NotRequired[bool]
    artifact_generator_is_visited: NotRequired[bool]
    environment_setup_is_visited: NotRequired[bool]
    sdk_agent_is_visited: NotRequired[bool]
    compilation_check_is_visited: NotRequired[bool]
    emulator_is_visited: NotRequired[bool]
    user_actions_is_visited: NotRequired[bool]
    deep_link_is_visited: NotRequired[bool]
    test_runner_is_visited: NotRequired[bool]
    visual_report_is_visited: NotRequired[bool]


    report_path: NotRequired[str]

    sdk_verified: NotRequired[bool]

def json_use_case_input_node(state: PipelineState) -> PipelineState:
    """
    Node 1: JSON Use Case Input

    Creates a JSON file for every selected use case and
    points current_use_case_path to the first one.
    """

    # New pipeline behavior: every run starts with a fresh sdk agent id
    state.setdefault("agent_id", 0)

    selected_cases = state.get("selected_use_cases") or []
    run_id = state.get("run_id", "run")

    use_cases_dir = RUNS_DIR / run_id / "use_cases"

    use_cases_dir.mkdir(parents=True, exist_ok=True)

    case_paths = []

    for index, case in enumerate(selected_cases):
        case_id = case.get("id", str(index))

        case_path = use_cases_dir / f"{case_id}.json"

        with case_path.open("w", encoding="utf-8") as f:
            json.dump(
                case,
                f,
                ensure_ascii=False,
                indent=2,
            )

        case_paths.append(str(case_path))

    state["use_cases_dir"] = str(use_cases_dir)

    state["current_use_case_path"] = (
        case_paths[0]
        if case_paths
        else None
    )

    if not case_paths:
        state["test_status"] = "FAIL"
        state["fail_reason"] = "No use cases selected for this run."

    return state



def artifact_generator_node(state: PipelineState) -> PipelineState:
    """
    Node 2: Artifact Generator

    Loads the active use case into state.
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



async def environment_setup_node(
    state: PipelineState,
) -> PipelineState:
    """
    Node 3: Environment Setup

    Creates sandbox environment,
    validates MCP,
    validates application.
    """

    def _append_node_log(
        *,
        status: str,
        message: str,
        extra: dict | None = None,
    ) -> list[dict]:
        entry: dict = {
            "node": "environment_setup",
            "status": status,
            "message": message,
        }
        if extra:
            entry.update(extra)
        return [*(state.get("nodes_log") or []), entry]

    environment_result = setup_environment(
        dict(state)
    )


    if environment_result.get("test_status") == "FAIL":
        reason = environment_result.get("error_reason", "Environment setup failed.")
        return {
            **state,
            **environment_result,
            "environment_setup_status": "FAILED",
            "fail_reason": reason,
            "nodes_log": _append_node_log(
                status="Failure",
                message=reason,
            ),
        }


    sandbox_path = environment_result.get(
        "sandbox_path"
    )


    if not sandbox_path:
        reason = "Environment setup did not return a sandbox_path."
        return {
            **state,
            **environment_result,
            "test_status": "FAIL",
            "environment_setup_status": "FAILED",
            "error_reason": reason,
            "fail_reason": reason,
            "nodes_log": _append_node_log(
                status="Failure",
                message=reason,
            ),
        }


    checks_result = await run_tasks_3_and_4(
        app_path=Path(sandbox_path),
        workdir=Path(sandbox_path),
        run_build_check=bool(
            state.get(
                "run_build_check",
                False,
            )
        ),
        app_id=state.get("app_id"),
        dev_key=state.get("dev_key"),
    )


    checks_succeeded = (
        checks_result.get("status")
        == "OK"
    )

    if checks_succeeded:
        node_message = "Sandbox created; MCP and application validation passed."
    else:
        mcp_status = (checks_result.get("task_3_mcp_alive") or {}).get("status")
        app_validation = checks_result.get("task_4_application_validation") or {}
        app_status = app_validation.get("status")
        app_error = app_validation.get("error")
        node_message = (
            "Environment checks failed "
            f"(mcp={mcp_status}, app_validation={app_status}"
            + (f": {app_error}" if app_error else "")
            + ")."
        )

    return {
        **state,
        **environment_result,

        "app_path": sandbox_path,

        "sandbox_path": sandbox_path,

        "environment_setup_status": (
            "OK"
            if checks_succeeded
            else "FAILED"
        ),

        "test_status": (
            "READY"
            if checks_succeeded
            else "FAIL"
        ),

        "fail_reason": (
            None
            if checks_succeeded
            else node_message
        ),

        "task_3_mcp_alive": checks_result.get(
            "task_3_mcp_alive"
        ),

        "task_4_application_validation": checks_result.get(
            "task_4_application_validation"
        ),

        "environment_setup_result": checks_result,

        "nodes_log": _append_node_log(
            status="Success" if checks_succeeded else "Failure",
            message=node_message,
            extra={
                "mcp_status": (checks_result.get("task_3_mcp_alive") or {}).get("status"),
                "app_validation_status": (
                    (checks_result.get("task_4_application_validation") or {}).get("status")
                ),
            },
        ),
    }



def prompt_agent_node(
    state: PipelineState,
) -> PipelineState:
    """
    Node 4: Prompt Agent

    Generates required SDK prompts.
    """

    state["prompt_agent_node_status"] = "RUNNING"


    try:

        current_path = state.get(
            "current_use_case_path"
        )


        if current_path:

            state["selected_use_cases_path"] = (
                current_path
            )


        updates = build_prompts(state)

        state.update(updates)


        missing = []

        agent_prompts = (
            state.get("agent_prompts")
            or {}
        )


        for prompt_name in get_args(PromptType):

            prompt_value = agent_prompts.get(
                prompt_name
            )

            if (
                not isinstance(
                    prompt_value,
                    str,
                )
                or not prompt_value.strip()
            ):
                missing.append(
                    f"agent_prompts.{prompt_name}"
                )


        platform = state.get("platform")


        if (
            not isinstance(platform, str)
            or not platform.strip()
        ):
            missing.append("platform")



        if missing:

            state["prompt_agent_node_status"] = "FAIL"
            state["test_status"] = "FAIL"

            state["prompt_agent_node_error"] = (
                "Prompt Agent did not save required fields: "
                + ", ".join(missing)
            )

        else:

            state["prompt_agent_node_status"] = "SUCCESS"

            state.pop(
                "prompt_agent_node_error",
                None,
            )


    except Exception as exc:

        state["prompt_agent_node_status"] = "FAIL"
        state["test_status"] = "FAIL"

        state["prompt_agent_node_error"] = str(exc)



    state["nodes_log"] = [
        *(state.get("nodes_log") or []),

        {
            "node": "prompt_agent",

            "status": state[
                "prompt_agent_node_status"
            ],

            "message": state.get(
                "prompt_agent_node_error",
                "Prompt Agent generated prompts successfully.",
            ),
        },
    ]


    return state

def sdk_agent_node(
    state: PipelineState,
) -> PipelineState:
    """
    Node 5: SDK Agent

    Single SDK agent node that is revisited for:
    integrate -> event -> verify passes.
    """

    if state.get("last_prompt_type") is None:
        state["last_prompt_type"] = "integrate_prompt"


    current_prompt_type = state["last_prompt_type"]


    agent_prompts = state.get(
        "agent_prompts",
        {},
    )

    sandbox_path = state.get(
        "sandbox_path",
    )

    if not sandbox_path:
        state["test_status"] = "FAIL"
        state["nodes_log"] = list(state.get("nodes_log") or []) + [{
            "node": "sdk_agent",
            "status": "Failure",
            "reason": "sandbox_path is missing — environment_setup may have failed.",
        }]
        return state

    platform = state.get(
        "platform",
    )

    audit_recorder = state.get(
        "audit_recorder",
    )


    user_prompt = agent_prompts[
        current_prompt_type
    ]


    result = asyncio.run(
        run_sdk_integration_agent(
            state=state,
            project_root_str=sandbox_path,
            platform=platform,
            user_prompt=user_prompt,
            audit_recorder=audit_recorder,
        )
    )


    state["type_agent"] = "sdk_agent"

    state["last_agent_message"] = user_prompt

    state["prompt_just_run"] = current_prompt_type


    node_succeeded = (
        result.get("status")
        != "FAIL"
    )


    node_log = {
        "node": "sdk_agent",

        "status": (
            "Success"
            if node_succeeded
            else "Failure"
        ),

        "prompt_type": current_prompt_type,
    }


    if (
        not node_succeeded
        and "reason" in result
    ):
        node_log["reason"] = result["reason"]



    state["nodes_log"] = [
        *(state.get("nodes_log") or []),
        node_log,
    ]



    if not node_succeeded:

        state["test_status"] = "FAIL"

        if "reason" in result:

            state["fail_reason"] = result["reason"]


    else:

        next_prompt_type = _next_prompt_type(
            current_prompt_type
        )


        if next_prompt_type is not None:

            state["last_prompt_type"] = (
                next_prompt_type
            )



    if current_prompt_type == "verify_prompt":

        close_sdk_integration_agent(
            state,
            audit_recorder,
        )


    return state



def compilation_check_node(
    state: PipelineState,
) -> PipelineState:
    """
    Node 6: Compilation Check

    Runs compilation validation
    and stores results.
    """

    platform = (
        state.get("platform")
        or state.get("prompt_platform")
    )


    result = check_compilation(
        {
            **state,
            "platform": platform,
        }
    )


    state.update(result)

    if not result.get("compilation_passed"):
        state["test_status"] = "FAIL"

    state["current_node"] = (
        "compilation_check"
    )

    state["next_node"] = (
        "emulator"
    )


    state["compilation_check_is_visited"] = True


    state["nodes_log"] = [
        *(state.get("nodes_log") or []),

        {
            "node": "compilation_check",

            "status": (
                "SUCCESS"
                if result.get(
                    "compilation_passed"
                )
                else "FAIL"
            ),
        },
    ]


    return state



def emulator_node(
    state: PipelineState,
) -> dict:
    """
    Node 7: Emulator

    Starts Appium,
    starts device,
    launches application.
    """

    device_id = state.get(
        "device_id"
    )

    app_id = state.get(
        "app_id"
    )

    remote_url = state.get(
        "remote_url",
        "http://127.0.0.1:4723",
    )


    steps = []

    driver_instance = None

    devices_listing = ""


    try:

        steps.append(
            f"[setup] {setup_appium_environment()}"
        )


        steps.append(
            f"[server] {start_appium_server()}"
        )


        devices_listing = list_devices()


        steps.append(
            f"[devices] {devices_listing}"
        )


        if not device_id:

            steps.append(
                "[device] Skipped: device_id missing."
            )

        else:

            steps.append(
                f"[device] {start_device(device_id)}"
            )


            driver_result = launch_app_on_device(
                state.get("platform"),
                device_id,
                app_id,
                remote_url,
            )


            if isinstance(driver_result, str):

                steps.append(
                    f"[launch] {driver_result}"
                )

            else:

                driver_instance = driver_result

                steps.append(
                    "[launch] App launched successfully."
                )


    except Exception as exc:

        steps.append(
            f"[error] Node execution failed: {exc}"
        )



    return {
        "available_devices": devices_listing,

        "execution_result": "\n".join(steps),

        "driver": driver_instance,
    }
def user_actions_node(
    state: PipelineState,
) -> PipelineState:
    """
    Node 8: User Actions
    """

    state["visited_user_actions"] = True

    state["user_actions_is_visited"] = True

    return state



def deep_link_node(
    state: PipelineState,
) -> PipelineState:
    """
    Node 9: Deep Link
    """

    return state



def test_runner_node(
    state: PipelineState,
) -> PipelineState:
    """
    Node 10: Test Runner
    """

    return state



def visual_report_node(
    state: PipelineState,
) -> PipelineState:
    """
    Node 11: Visual Report

    Handles multiple use cases loop.
    """

    use_cases_dir = state.get(
        "use_cases_dir"
    )

    current_path = state.get(
        "current_use_case_path"
    )


    if (
        use_cases_dir
        and current_path
        and os.path.isdir(use_cases_dir)
    ):

        remaining = sorted(
            os.path.join(
                use_cases_dir,
                name,
            )

            for name in os.listdir(
                use_cases_dir
            )

            if (
                name.endswith(".json")
                and os.path.join(
                    use_cases_dir,
                    name,
                ) != current_path
            )
        )


        if remaining:

            if os.path.exists(current_path):

                os.remove(current_path)


            state["current_use_case_path"] = (
                remaining[0]
            )

        else:

            state["current_use_case_path"] = None



    return state



def route_from_sdk_agent(
    state: PipelineState,
) -> str:
    """
    Conditional edge from SDK agent.

    FAIL (any prompt) -> test_runner
    verify_prompt success -> test_runner
    integrate/event success -> compilation_check
    """
    if _is_pipeline_fail(state):
        return "test_runner"

    prompt_just_run = (
        state.get("prompt_just_run")
        or state.get("last_prompt_type")
    )

    if prompt_just_run == "verify_prompt":
        return "test_runner"

    return "compilation_check"



def route_from_emulator(
    state: PipelineState,
) -> str:
    """
    Conditional edge from emulator.

    FAIL -> test_runner
    event_prompt without user_actions -> user_actions
    otherwise -> sdk_agent (next prompt pass)
    """
    if _is_pipeline_fail(state):
        return "test_runner"

    if (
        state.get("last_prompt_type")
        == "event_prompt"
        and not state.get(
            "visited_user_actions",
            False,
        )
    ):
        return "user_actions"

    return "sdk_agent"



def route_from_visual_report(
    state: PipelineState,
) -> str:
    """
    Conditional edge from visual report.
    """

    if state.get(
        "current_use_case_path"
    ):

        return "artifact_generator"


    return "end"
    # End of workflow_nodes.py