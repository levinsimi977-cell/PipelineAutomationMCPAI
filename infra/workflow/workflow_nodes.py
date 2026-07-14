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

    use_cases_dir = os.path.join(
        "data",
        "runs",
        run_id,
        "use_cases",
    )

    os.makedirs(use_cases_dir, exist_ok=True)

    case_paths = []

    for index, case in enumerate(selected_cases):
        case_id = case.get("id", str(index))

        case_path = os.path.join(
            use_cases_dir,
            f"{case_id}.json",
        )

        with open(case_path, "w", encoding="utf-8") as f:
            json.dump(
                case,
                f,
                ensure_ascii=False,
                indent=2,
            )

        case_paths.append(case_path)

    state["use_cases_dir"] = use_cases_dir

    state["current_use_case_path"] = (
        case_paths[0]
        if case_paths
        else None
    )

    return state


def artifact_generator_node(state: PipelineState) -> PipelineState:
    """
    Node 2: Artifact Generator

    Resolves the active use case from `selected_use_cases` (official state),
    using `current_use_case_path` when the pipeline is looping over cases.
    Writes `answer_policy` (and `platform` for downstream nodes) into state,
    and loads the policy into the answer-policy repository.
    """
    selected = state.get("selected_use_cases") or []
    if not selected:
        return state

    use_case = selected[0]
    current_path = state.get("current_use_case_path")
    if current_path:
        stem = Path(str(current_path)).stem
        for case in selected:
            if isinstance(case, dict) and str(case.get("id", "")) == stem:
                use_case = case
                break
        else:
            if stem.isdigit():
                index = int(stem)
                if 0 <= index < len(selected) and isinstance(selected[index], dict):
                    use_case = selected[index]

    if not isinstance(use_case, dict):
        return state

    state["current_use_case"] = use_case
    state["answer_policy"] = use_case.get("answer_policy") or state.get("answer_policy") or {}
    state["platform"] = use_case.get("platform", state.get("platform", "android"))
    state["app_path"] = state.get("app_path") or use_case.get("app_path")

    if use_case.get("answer_policy"):
        run_id = state.get("run_id", "run")
        repo = get_answer_policy_repository()
        repo.load_from_use_case(run_id, use_case)

    if current_path:
        state["selected_use_cases_path"] = state.get("selected_use_cases_path") or current_path

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

    environment_result = setup_environment(
        dict(state)
    )


    if environment_result.get("test_status") == "FAIL":

        return {
            **state,
            **environment_result,
            "environment_setup_status": "FAILED",
        }


    sandbox_path = environment_result.get(
        "sandbox_path"
    )


    if not sandbox_path:

        return {
            **state,
            **environment_result,
            "test_status": "FAIL",
            "environment_setup_status": "FAILED",
            "error_reason": (
                "Environment setup did not return "
                "a sandbox_path."
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
    )


    checks_succeeded = (
        checks_result.get("status")
        == "OK"
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

        "task_3_mcp_alive": checks_result.get(
            "task_3_mcp_alive"
        ),

        "task_4_application_validation": checks_result.get(
            "task_4_application_validation"
        ),

        "environment_setup_result": checks_result,
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



    state["nodes_logs"] = [
        *(state.get("nodes_logs") or []),
        node_log,
    ]


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

    verify_prompt -> test_runner

    integrate/event -> compilation_check
    """

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
    """

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