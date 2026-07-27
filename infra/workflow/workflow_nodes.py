from __future__ import annotations

import json
import os
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
from infra.agents.answerAgent.answer_policy_repository import (
    get_answer_policy_repository,
)
from infra.agents.userActions.deep_link import (
    extract_deep_link_url_from_audit,
    simulate_deep_link_click,
)
from infra.workflow.nodes.nodeEmulator import (
    emulator_node as _emulator_node_impl,
    route_from_emulator as _route_from_emulator_impl,
)
from infra.load_env import get_app_id_for_platform, get_dev_key, resolve_app_id_for_platform
from infra.use_case_service.repositories.run_repository import (
    RUNS_DIR,
    delete_run_selection,
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
    if state.get("compilation_retry_pending"):
        return "sdk_agent"
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

    agent_id: NotRequired[Optional[str]]

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

    # Real compiler stderr/stdout excerpt from the failing build, handed back
    # to sdk_agent_node for exactly one self-correction retry per prompt
    # stage (see compilation_check_node / route_after_compilation_check).
    compilation_fix_context: NotRequired[str]

    # Prompt stages ("integrate_prompt" / "event_prompt") that have already
    # consumed their one compilation retry -- prevents an infinite
    # compile-fail -> retry -> compile-fail loop.
    compilation_retry_used_for: NotRequired[list[str]]

    # True for exactly the compilation_check_node run that just armed a
    # retry -- route_after_compilation_check reads this to send flow back
    # to sdk_agent instead of test_runner despite test_status not being FAIL.
    compilation_retry_pending: NotRequired[bool]


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


def _quit_appium_driver(state: PipelineState) -> None:
    """Best-effort Appium driver.quit() so sessions do not linger between UCs."""
    driver = state.get("driver")
    if driver is None:
        return
    try:
        quit_fn = getattr(driver, "quit", None)
        if callable(quit_fn):
            quit_fn()
    except Exception:
        pass
    run_id = state.get("run_id")
    if run_id:
        try:
            from infra.workflow.run_resource_registry import unregister_driver

            unregister_driver(str(run_id), driver)
        except Exception:
            pass


def json_use_case_input_node(state: PipelineState) -> PipelineState:
    """
    Node 1: JSON Use Case Input

    Creates a JSON file for every selected use case and
    points current_use_case_path to the first one.
    """

    try:
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

        if not case_paths:
            state["test_status"] = "FAIL"
            state["fail_reason"] = "No use cases selected for this run."
            state["current_use_case_path"] = None

            return state

        state["current_use_case_path"] = case_paths[0]

        return state

    except Exception as e:
        state["test_status"] = "FAIL"
        state["error_detected"] = True
        state["failed_node"] = "json_use_case_input_node"
        state["error_message"] = str(e)
        state["fail_reason"] = str(e)
        state["current_use_case_path"] = None

        return state


def _reset_runtime_fields_for_next_use_case(state: PipelineState) -> None:
    """
    Clear per-use-case runtime fields so UC-N cannot leak into UC-N+1.

    Called at the start of artifact_generator — AFTER visual_report already
    wrote the previous use case's HTML (so clearing nodes_log here is safe).

    KEEP (run-level accumulators — do not touch):
      run_id, selected_use_cases, use_cases_dir, current_use_case_path,
      use_case_reports, audit_recorder (object; in-memory events cleared
      on follow-on UC), report_path

    Credentials / platform / device_id are popped here and re-resolved from
    the next use case in artifact_generator_node.

    Ownership: workflow state-isolation (not Prompt Agent / SDK Agent).
    """
    # Close any leftover SDK session BEFORE nulling agent_id, otherwise the
    # entry stays orphaned in _AGENT_SESSIONS until process exit.
    try:
        close_sdk_integration_agent(state, state.get("audit_recorder"))
    except Exception:
        pass

    # Release Appium session before dropping the driver handle from state.
    _quit_appium_driver(state)

    # Drop in-memory answer_policy for this run so UC-N's policy cannot
    # answer questions belonging to UC-N+1 (repo is keyed by run_id only).
    run_id = state.get("run_id")
    if run_id:
        try:
            get_answer_policy_repository().clear(str(run_id))
        except Exception:
            pass

    # Delete previous sandbox on disk (best-effort). Only sandbox_path —
    # never catalog app_path (cleanup_environment can delete real sample apps).
    old_sandbox = state.get("sandbox_path")
    if old_sandbox:
        try:
            from infra.application.app import cleanup_environment

            cleanup_environment(str(old_sandbox))
        except Exception:
            pass

    state["test_status"] = "READY"
    state["error_detected"] = False
    state["last_prompt_type"] = None
    state["visited_user_actions"] = False
    # Force a new SDK conversation for this use case.
    state["agent_id"] = None

    # Follow-on UC only: wipe logs/visited from the previous UC.
    # Prefer artifact_generator_is_visited — it is set after the first UC
    # loads and does not depend on report success. "use_case_reports" in
    # state covers the case where visual_report setdefault'd an empty list
    # after a failed card append (bool([]) would wrongly look like UC-1).
    # Do NOT wipe on the first UC — early log entries must stay for the report.
    is_follow_on_use_case = bool(
        state.get("artifact_generator_is_visited")
        or ("use_case_reports" in state)
    )
    if is_follow_on_use_case:
        state["nodes_log"] = []
        state["nodes_logs"] = []
        for visited_key in (
            "json_use_case_input_is_visited",
            "artifact_generator_is_visited",
            "environment_setup_is_visited",
            "sdk_agent_is_visited",
            "compilation_check_is_visited",
            "emulator_is_visited",
            "user_actions_is_visited",
            "deep_link_is_visited",
            "test_runner_is_visited",
            "visual_report_is_visited",
        ):
            state.pop(visited_key, None)
        # Isolate per-UC Audit Trail in reports (disk audit.jsonl stays full-run).
        recorder = state.get("audit_recorder")
        if recorder is not None:
            try:
                clear_fn = getattr(recorder, "clear_memory", None)
                if callable(clear_fn):
                    clear_fn()
            except Exception:
                pass

    # Drop fields that belong to the previous use case only.
    for key in (
        # Failure / routing
        "fail_reason",
        "error_reason",
        "failed_node",
        "error_message",
        "prompt_just_run",
        "current_node",
        "next_node",
        # Credentials / platform (reloaded from UC below — never keep leftovers)
        "platform",
        "dev_key",
        "app_id",
        "run_build_check",
        # Sandbox / app paths (catalog app_path reloaded from UC below)
        "sandbox_path",
        "app_path",
        "original_app_path",
        "remote_url",
        "app_status",
        "execution_result",
        # Answer policy (reloaded from UC / repo below)
        "answer_policy",
        "selected_use_cases_path",
        "current_use_case",
        # Prompt / SDK agent
        "agent_prompts",
        "agent_model",
        "type_agent",
        "type_aggent",
        "last_agent_message",
        "last_aggent_massage",
        "agent_messages",
        "prompt_agent_answer",
        "prompt_agent_sdk",
        "prompt_agent_node_status",
        "prompt_agent_node_error",
        "sdk_agent_status",
        "installation_answers",
        "question_rounds",
        # MCP / listener (call_log MUST clear — listener appends to it)
        "call_log",
        "mcp_sequence",
        "mcp_health_check",
        "mcp_tools_available",
        "mcp_tools_availble",
        "mcp_tools_call",
        "mcp_tools_used",
        "mcp_tools_used_success",
        "mcp_integration_text",
        "task_3_mcp_alive",
        "task_4_application_validation",
        "environment_setup_status",
        "environment_setup_result",
        # Compilation / emulator
        "compilation_passed",
        "compilation_result",
        "compilation_fix_context",
        "compilation_retry_used_for",
        "compilation_retry_pending",
        "driver",
        "available_devices",
        "emulator_checking",
        "sdk_verified",
        # Deep link / verification outcomes
        "deep_link_status",
        "triggered_deep_link_url",
        "is_verify_deep_link",
        "is_verify_deep_link_message",
        "is_verify_deep_link_massage",
        "is_tool_order_valid",
        "is_tool_order_valid_message",
        "is_tool_order_valid_massage",
        "files_modified",
        "applied_files",
        # Per-UC timing / misc report fields
        "started_at",
        "ended_at",
        "start_time",
        "end_time",
        "audit_events",
        "dev_key_configured",
        "dev_key_source",
        "prompt_platform",
    ):
        state.pop(key, None)


def artifact_generator_node(state: PipelineState) -> PipelineState:
    """
    Node 2: Artifact Generator

    Resolves the active use case from `selected_use_cases` (this run's state),
    using `current_use_case_path` when looping. If memory is empty, falls back
    only to `data/runs/<run_id>/` for the same run — never other runs.
    """
    from infra.use_case_service.repositories.run_repository import (
        load_selected_use_cases,
    )
    # Isolate this use case from whatever the previous one left in state.
    _reset_runtime_fields_for_next_use_case(state)

    selected = list(state.get("selected_use_cases") or [])
    run_id = state.get("run_id", "run")
    current_path = state.get("current_use_case_path")
    if current_path and os.path.exists(current_path):
        with open(current_path, "r", encoding="utf-8") as f:
            current_use_case = json.load(f)

        state["current_use_case"] = current_use_case
        state["selected_use_cases_path"] = current_path
        # run_platform (stamped by the UI when the use case was selected —
        # see ui/app.py's _stamp_run_platform) is the concrete platform to
        # run against and takes priority. Falling back to "platform" alone
        # would break for a "common" use case, whose own platform field is
        # literally the string "common", not a real platform.
        # Do not fall back to state["platform"] — reset already popped it so
        # a missing run_platform cannot revive the previous use case's value.
        platform = (
            current_use_case.get("run_platform")
            or current_use_case.get("platform")
            or state.get("platform", "android")
        )
        if isinstance(platform, str):
            platform = platform.strip().lower()
        state["platform"] = platform
        # Catalog path only — environment_setup overwrites with sandbox_path.
        # Do NOT reuse a previous use case's app_path/sandbox here.
        state["app_path"] = current_use_case.get("app_path")
        state["answer_policy"] = current_use_case.get("answer_policy") or {}
        # Resolve credentials for THIS use case (not leftovers from UC-N-1).
        state["dev_key"] = current_use_case.get("dev_key") or get_dev_key()
        state["app_id"] = resolve_app_id_for_platform(
            platform,
            current_use_case.get("app_id") or get_app_id_for_platform(platform),
        )
        # answer_policy.android only ever applies to an android run — an iOS
        # (or "common"→ios) use case that happens to carry leftover/copy-pasted
        # android policy data must never leak its device_id (an AVD name) in as
        # this run's simulator UDID. Mirrors _resolve_device_id's platform
        # guard in infra/agents/userActions/deep_link.py.
        android_policy = (
            (current_use_case.get("answer_policy") or {}).get("android") or {}
            if platform == "android"
            else {}
        )
        device_id = (
            current_use_case.get("device_id") or android_policy.get("device_id")
        )
        if device_id:
            state["device_id"] = device_id
        if "run_build_check" in current_use_case:
            state["run_build_check"] = bool(current_use_case.get("run_build_check"))

    current_use_case = state.get("current_use_case") or {}

    # Repo was cleared in _reset_runtime_fields_for_next_use_case; reload
    # only when this UC actually ships a policy (empty/missing → stay clear).
    if current_use_case.get("answer_policy"):
        get_answer_policy_repository().load_from_use_case(
            run_id or "run",
            current_use_case,
        )

    # current_use_case_path already fully resolved current_use_case above --
    # only fall back to selected_use_cases (reloading it from disk for this
    # run_id if even that memory is empty, per this function's docstring)
    # when there was no valid current_use_case_path to load from.
    if not current_use_case:
        if not selected:
            selected = load_selected_use_cases(run_id)

        use_case = selected[0] if selected and isinstance(selected[0], dict) else None
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
            reason = "Active use case could not be resolved for this run."
            state["test_status"] = "FAIL"
            state["fail_reason"] = reason
            state["nodes_log"] = [
                *(state.get("nodes_log") or []),
                {
                    "node": "artifact_generator",
                    "status": "Failure",
                    "message": reason,
                },
            ]
            return state

        state["current_use_case"] = use_case
        current_use_case = use_case
        state["answer_policy"] = use_case.get("answer_policy") or state.get("answer_policy") or {}
        platform = (
            use_case.get("run_platform")
            or use_case.get("platform")
            or state.get("platform", "android")
        )
        if isinstance(platform, str):
            platform = platform.strip().lower()
        state["platform"] = platform
        state["app_path"] = state.get("app_path") or use_case.get("app_path")
        # Each use case gets its own sdk_agent conversation: reset agent_id so
        # run_sdk_integration_agent builds a fresh agent instead of reusing a
        # (by now closed) session id left over from the previous use case.
        state["agent_id"] = None
        state["dev_key"] = use_case.get("dev_key") or state.get("dev_key") or get_dev_key()
        state["app_id"] = resolve_app_id_for_platform(
            platform,
            use_case.get("app_id") or state.get("app_id") or get_app_id_for_platform(platform),
        )
        # last_prompt_type/visited_user_actions are per-use-case progress
        # markers for the sdk_agent loop (see sdk_agent_node/route_from_emulator)
        # but neither was ever reset between use cases: without this, a use
        # case after the first would inherit "verify_prompt"/True left over
        # from the previous use case and skip straight past integrate_prompt,
        # event_prompt, and user_actions.
        state["last_prompt_type"] = "integrate_prompt"
        state["visited_user_actions"] = False

        if use_case.get("answer_policy"):
            get_answer_policy_repository().load_from_use_case(
                run_id or "run",
                use_case,
            )

    if current_path:
        state["selected_use_cases_path"] = state.get("selected_use_cases_path") or current_path

    state["artifact_generator_is_visited"] = True
    case_id = current_use_case.get("id", "?")
    state["nodes_log"] = [
        *(state.get("nodes_log") or []),
        {
            "node": "artifact_generator",
            "status": "Success",
            "message": f"Loaded use case '{case_id}' from {current_path or 'state'}.",
        },
    ]

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
        mcp_startup_timeout_seconds=state.get("mcp_startup_timeout_seconds"),
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

async  def sdk_agent_node(
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

    # Set by compilation_check_node when the build after this exact prompt
    # stage failed and this stage hasn't used its one retry yet -- hand the
    # agent the real compiler output so it can fix its own mistake, the same
    # way a human developer would react to a failed build.
    compilation_fix_context = state.pop("compilation_fix_context", None)
    if compilation_fix_context:
        user_prompt = (
            f"{user_prompt}\n\n"
            "---\n"
            "Your previous changes were compiled and the build FAILED. This is the "
            "real compiler output (not a guess) -- fix the exact issue(s) it "
            "reports, then make sure the project still satisfies the rest of "
            "this prompt:\n"
            f"{compilation_fix_context}"
        )

    # The meta-prompt that generates agent_prompts (prompt_agent_core.py) only
    # ever sees answer_policy -- it never sees the real dev_key/app_id, so it
    # cannot embed them, and integrateSdk's own example snippets use
    # placeholder text like "YOUR_DEV_KEY_HERE"/"YOUR_APP_ID_HERE" as a stand-
    # in for wherever the caller's real credentials come from. Observed
    # failure: the agent mechanically copied that placeholder text verbatim
    # into AppDelegate.m instead of substituting a real value nowhere in its
    # context, which compiled fine but made every AppsFlyer network call
    # fail for real ("App ID is incorrect" / HTTP 404) -- not a hallucinated
    # API, just missing configuration data no amount of header-reading could
    # ever supply. Injecting the literal values here removes the ambiguity.
    real_dev_key = state.get("dev_key")
    real_app_id = state.get("app_id")
    if real_dev_key and real_app_id:
        user_prompt = (
            f"{user_prompt}\n\n"
            "---\n"
            "Real AppsFlyer credentials for this app (use these exact literal "
            "strings anywhere SDK initialization code needs a dev key / app ID "
            "-- e.g. initWithDevKey:appleAppId: or equivalent). Do NOT write "
            "placeholder text such as YOUR_DEV_KEY_HERE or YOUR_APP_ID_HERE; "
            "if you see that placeholder text in an MCP tool's example output, "
            "replace it with these real values instead of copying it verbatim:\n"
            f"- devKey: {real_dev_key}\n"
            f"- appId / appleAppId: {real_app_id}"
        )

    # iOS deep-link logs are collected by deep_link_node only after the app is
    # launched and the link is fired -- long after prompt_agent_node pre-generated
    # this static prompt text. Append the concrete file path here, at call time,
    # so the verify phase can call verifyIosDeepLink directly instead of asking
    # the user to paste logs manually (which it has no way to do in this pipeline).
    ios_deeplink_log_file = state.get("ios_deeplink_log_file")
    if (
        current_prompt_type == "verify_prompt"
        and str(platform).lower() == "ios"
        and ios_deeplink_log_file
    ):
        user_prompt = (
            f"{user_prompt}\n\n"
            "---\n"
            "iOS deep-link verification data (already collected by the pipeline):\n"
            f"- Log file path: {ios_deeplink_log_file}\n"
            "First call verifyIosDeepLink with action=\"prepare\" and "
            f"projectPath=\"{sandbox_path}\" -- the log file already exists at the path "
            "above, so this call will just confirm it will be reused (it does not "
            "overwrite an existing file). This step is required by the tool itself and "
            "by the pipeline's MCP tool-order validation; skipping it will mark the run "
            "as having an invalid tool sequence even if the deep link actually worked. "
            "Then call verifyIosDeepLink again with action=\"verify\", "
            f"logFilePath=\"{ios_deeplink_log_file}\", and confirmLogFileReady=true. "
            "Do not ask the user to paste logs manually -- this file was already "
            "populated automatically from the simulator's system log right after the "
            "deep link was triggered."
        )

    # Same idea as ios_deeplink_log_file above, but for verifyIosSdk's separate
    # log file: emulator_node collects it right after app launch (before any
    # deep link exists), since that tool checks base SDK start/conversion-data
    # readiness, not deep-link-specific behavior.
    #
    # emulator_node only merges "ios_sdk_log_file" into the returned partial
    # state when it collected something on THIS invocation -- if a later
    # invocation (e.g. the post-event_prompt emulator pass) collects nothing
    # (a transient log-marker timeout, log_show hiccup, etc.) its partial
    # result simply omits the key, and depending on how the graph merges
    # state, a previously-set value can end up unavailable here even though
    # emulator_node always writes to the exact same fixed path on disk. Fall
    # back to that fixed path and confirm it's actually there with content --
    # cheap, and it turns a state-propagation gap into a correct mandatory
    # verifyIosSdk call instead of a silent, evidence-free FAIL.
    ios_sdk_log_file = state.get("ios_sdk_log_file")
    if not ios_sdk_log_file and str(platform).lower() == "ios":
        candidate = Path(str(state.get("sandbox_path") or "")) / "ios-sdk-logs.txt"
        if candidate.is_file() and candidate.stat().st_size > 0:
            ios_sdk_log_file = str(candidate)

    # verifyIosInAppEvent uses its OWN dedicated log file (created by its own
    # `prepare` step), separate from ios-sdk-logs.txt -- it is NOT "the same
    # log file" verifyIosSdk uses, even though nodeEmulator.py's
    # _collect_ios_sdk_logs happens to populate both with the same captured
    # window (see that function's docstring) since an in-app-event log line
    # fires within seconds of "start" and both need the same one to be
    # collected. Resolving it separately here -- same state-then-disk-
    # fallback pattern as ios_sdk_log_file above -- means the agent gets a
    # real, tool-appropriate path instead of one borrowed from a different
    # tool's prepare step, which is what verifyIosInAppEvent's own
    # documentation actually calls for.
    ios_inapp_event_log_file = state.get("ios_inapp_event_log_file")
    if not ios_inapp_event_log_file and str(platform).lower() == "ios":
        candidate = Path(str(state.get("sandbox_path") or "")) / "ios-inapp-event-logs.txt"
        if candidate.is_file() and candidate.stat().st_size > 0:
            ios_inapp_event_log_file = str(candidate)

    if (
        current_prompt_type == "verify_prompt"
        and str(platform).lower() == "ios"
        and ios_sdk_log_file
    ):
        # verify_sdk.verify_logs_ready in the use case's answer_policy is this
        # use case's own explicit request for a real SDK-level check (it hits
        # AppsFlyer's backend with the UID from the logs) -- when it's set,
        # calling verifyIosSdk is not optional, otherwise an agent that
        # already "thinks" it knows the answer from a manual code read can
        # skip the one check that would actually confirm or refute that.
        verify_sdk_policy = (state.get("answer_policy") or {}).get("verify_sdk") or {}
        sdk_check_required = bool(verify_sdk_policy.get("verify_logs_ready"))
        directive = (
            "This use case requires SDK-level verification -- you MUST call verifyIosSdk"
            if sdk_check_required
            else "If you call verifyIosSdk, call it"
        )
        user_prompt = (
            f"{user_prompt}\n\n"
            "---\n"
            "iOS SDK verification data (already collected by the pipeline):\n"
            f"- Log file path: {ios_sdk_log_file}\n"
            f"{directive} with action=\"prepare\" and projectPath=\"{sandbox_path}\" "
            "first -- the log file already exists at the path above, so this call will "
            "just confirm it will be reused (it does not overwrite an existing file). "
            "This step is required by the tool itself and by the pipeline's MCP "
            "tool-order validation; skipping it will mark the run as having an invalid "
            "tool sequence even if the SDK actually verified successfully. Then call "
            "verifyIosSdk again with action=\"verify\", "
            f"logFilePath=\"{ios_sdk_log_file}\", and confirmLogFileReady=true. "
            "Do not ask the user to paste logs manually -- this file was already "
            "populated automatically from the simulator's system log right after the "
            "app was launched."
        )
        if ios_inapp_event_log_file:
            user_prompt = (
                f"{user_prompt}\n\n"
                "iOS in-app-event verification data (already collected by the "
                "pipeline, in its OWN dedicated file -- do not reuse the SDK log "
                "file path above for this tool):\n"
                f"- Log file path: {ios_inapp_event_log_file}\n"
                "The same two-step prepare-then-verify sequence applies to "
                "verifyIosInAppEvent: call it with action=\"prepare\" and "
                f"projectPath=\"{sandbox_path}\" first, then with action=\"verify\", "
                f"logFilePath=\"{ios_inapp_event_log_file}\", and "
                "confirmLogFileReady=true. Do not ask the user to paste logs "
                "manually -- this file was already populated automatically from "
                "the simulator's system log right after the app was launched."
            )


    # Snapshot how many audit events exist before this turn so we can tell,
    # right after the call, whether *this specific turn* actually wrote any
    # source file -- as opposed to just a Podfile/config tweak. Needed below
    # to detect a real "integrate_prompt wrote no code at all" failure.
    events_before_turn = len(audit_recorder.events) if audit_recorder else 0

    result = await run_sdk_integration_agent(
        state=state,
        project_root_str=sandbox_path,
        platform=platform,
        user_prompt=user_prompt,
        audit_recorder=audit_recorder,
    )

    deep_link_url = extract_deep_link_url_from_audit(audit_recorder)
    if deep_link_url:
        state["deep_link_url"] = deep_link_url

    state["type_agent"] = "sdk_agent"

    state["last_agent_message"] = user_prompt

    state["prompt_just_run"] = current_prompt_type


    node_succeeded = (
        result.get("status")
        != "FAIL"
    )

    # Objective (not classifier-dependent) check: integrate_prompt is the one
    # turn whose whole job is to write the SDK integration code. If it failed
    # AND no source file (.m/.h/.mm/.swift/.java/.kt) was written during this
    # exact turn -- only e.g. a Podfile -- then compilation_check/emulator
    # will just build and boot an app that was never touched by AppsFlyer
    # code, which cannot possibly pass verify_prompt later. Running that full
    # build+simulator-boot cycle anyway (as happened in practice, twice, for
    # ~10 minutes) is pure waste, so short-circuit straight to test_runner
    # instead. This is deliberately narrower than "classifier said FAIL"
    # (route_from_sdk_agent still ignores that on its own, since the
    # classifier can be wrong) -- it only fires on the objective, checkable
    # fact that zero code exists to compile.
    sdk_integration_incomplete = False
    if current_prompt_type == "integrate_prompt" and not node_succeeded and audit_recorder:
        code_suffixes = (".m", ".h", ".mm", ".swift", ".java", ".kt")
        wrote_code_file = any(
            e["event_type"] == "PROJECT_FILE_WRITTEN"
            and e["payload"].get("changed")
            and str(e["payload"].get("file_path", "")).endswith(code_suffixes)
            for e in audit_recorder.events[events_before_turn:]
        )
        if not wrote_code_file:
            sdk_integration_incomplete = True
            state["test_status"] = "FAIL"
            state["fail_reason"] = (
                "sdk_agent failed integrate_prompt without writing any source "
                "file (.m/.h/.mm/.swift/.java/.kt) -- no AppsFlyer integration "
                "code exists to compile, so compilation_check/emulator were "
                "skipped instead of running on a build that could never pass."
            )

    state["sdk_integration_incomplete"] = sdk_integration_incomplete

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


    state["sdk_agent_is_visited"] = True

    state["nodes_log"] = [
        *(state.get("nodes_log") or []),
        node_log,
    ]



    if not node_succeeded:

        # Intentionally NOT setting state["test_status"] = "FAIL" here:
        # that field is the shared gate every other node's router checks
        # (_is_pipeline_fail/route_after_node), so leaving it untouched
        # lets the pipeline continue past an sdk_agent failure instead of
        # jumping straight to test_runner. The failure is still fully
        # recorded above in nodes_log and below in fail_reason.

        if "reason" in result:

            state["fail_reason"] = result["reason"]

    # Advance to the next prompt type regardless of pass/fail (not just on
    # success): a FAIL here doesn't necessarily mean the agent actually
    # failed the task — it still needs to move forward instead of retrying
    # the same prompt forever. The failure is already recorded in
    # nodes_log/fail_reason for later inspection.
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

    Runs compilation validation and stores results. On failure, the prompt
    stage that just wrote the failing code (integrate_prompt / event_prompt)
    gets exactly one retry: the real compiler output is handed back to
    sdk_agent_node via compilation_fix_context, and last_prompt_type is
    rewound so that same stage runs again instead of advancing. This mirrors
    how a human developer reacts to a failed build, without ever telling the
    agent what the fix should be.
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

    compilation_passed = bool(result.get("compilation_passed"))
    retry_pending = False

    if not compilation_passed:
        state["test_status"] = "FAIL"

        prompt_just_run = state.get("prompt_just_run")
        already_retried = prompt_just_run in (state.get("compilation_retry_used_for") or [])

        # verify_prompt never writes new code that reaches this node, so
        # only integrate/event stages are eligible for a retry.
        if prompt_just_run and prompt_just_run != "verify_prompt" and not already_retried:
            compilation_result = result.get("compilation_result")
            fix_context = (
                getattr(compilation_result, "error_excerpt", "")
                or getattr(compilation_result, "detail", "")
                or "Build failed (no error detail captured)."
            )
            state["compilation_fix_context"] = fix_context
            state["compilation_retry_used_for"] = [
                *(state.get("compilation_retry_used_for") or []),
                prompt_just_run,
            ]
            # sdk_agent_node already advanced last_prompt_type past
            # prompt_just_run right after that turn -- rewind it so the same
            # stage is re-run instead of moving on to the next one.
            state["last_prompt_type"] = prompt_just_run
            state["test_status"] = "READY"
            retry_pending = True

    state["compilation_retry_pending"] = retry_pending

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
                if compilation_passed
                else ("RETRY" if retry_pending else "FAIL")
            ),
        },
    ]


    return state


def emulator_node(state: PipelineState) -> dict:
    """Node 7: Emulator — launch compiled app (G5)"""
    return _emulator_node_impl(state)


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
    state["deep_link_is_visited"] = True
    state.update(simulate_deep_link_click(state))

    # Every other node appends a {"node": ..., "status": ...} entry to
    # nodes_log so the report can show whether it passed -- this one used to
    # skip that step, so the report always showed "Deep Link: Status —" even
    # when the URL was sent and logs were collected successfully.
    state["nodes_log"] = [
        *(state.get("nodes_log") or []),
        {
            "node": "deep_link",
            "status": state.get("deep_link_status") or "UNKNOWN",
            "message": state.get("deep_link_message") or state.get("error_reason") or "",
            "alert_dismissal": state.get("deep_link_alert_dismissal") or "",
        },
    ]

    return state



def test_runner_node(
    state: PipelineState,
) -> PipelineState:
    """
    Node 10: Test Runner

    Final roll-up for sdk_agent only: sdk_agent_node deliberately does NOT
    set state["test_status"] on failure (see sdk_agent_node), so the
    pipeline can keep routing normally instead of jumping straight here.
    That means, by the time we get here, test_status may still say
    everything is fine even though sdk_agent actually failed at some point
    during this use case. Scan nodes_log for that node specifically (not
    any other node — those already set test_status themselves on failure)
    and restore "FAIL" so end-of-run consumers (e.g. ui/app.py's
    final_state.get("test_status") == "FAIL" check) still see the correct
    verdict for this use case.

    Deliberately does NOT scope this scan to "only entries from the
    current use case" — nodes_log accumulates across every use case in
    this run and nothing resets it between use cases yet, so a failure in
    an earlier use case's sdk_agent calls will also be picked up here for
    a later, otherwise-passing use case. That is a known, separate
    limitation (not addressed here).
    """
    if state.get("test_status") != "FAIL":
        log = [
            entry
            for entry in (state.get("nodes_log") or [])
            if isinstance(entry, dict)
        ]

        sdk_agent_failed = any(
            entry.get("node") == "sdk_agent"
            and str(entry.get("status", "")).lower() in ("failure", "fail")
            for entry in log
        )

        if sdk_agent_failed:
            state["test_status"] = "FAIL"

    return state



def _clear_run_dir(state: PipelineState) -> None:
    """Erase the entire data/runs/<run_id>/ directory for this run.

    Called once the workflow has processed the last selected use case, since
    everything under it (runtime-config.json, audit.jsonl, the top-level
    saved-selection JSON files, and the use_cases/ working copies) is
    regenerated automatically the next time a run is started. Reuses
    run_repository.delete_run_selection() — the same delete used by the
    UI's manual "Saved run selections pending cleanup" housekeeping button —
    so there is only one place that knows how to tear down a run dir.

    Skipped on a FAILED run (see run_resource_registry.should_keep_failed_run_artifacts)
    so audit.jsonl survives for post-mortem inspection — this node runs
    before run_launcher's own teardown ever gets a chance to apply that
    same rule to the sandbox, so it must check it too.
    """
    run_id = state.get("run_id")
    if not run_id:
        return

    if state.get("test_status") == "FAIL":
        from infra.workflow.run_resource_registry import (
            should_keep_failed_run_artifacts,
        )

        if should_keep_failed_run_artifacts():
            return

    delete_run_selection(run_id)


def visual_report_node(
    state: PipelineState,
) -> PipelineState:
    """
    Node 11: Visual Report

    Handles multiple use cases loop. Every time this node runs, state still
    reflects the use case that just finished (current_use_case_path hasn't
    advanced yet) — so it first builds that use case's own detail report via
    data/reports/build_report.py (RunReportBuilder, the same builder used by
    the demo reports) and registers it as a card under
    state["use_case_reports"]. Once every selected use case has been
    processed (current_use_case_path is exhausted), it builds the run's
    index page — cards for every use case, each linking to its own detail
    report — and records its path under state["report_path"] — regardless
    of whether the run passed or failed.
    """

    use_cases_dir = state.get(
        "use_cases_dir"
    )

    current_path = state.get(
        "current_use_case_path"
    )

    # Mark this node as visited and log its execution *before* building the
    # report. record_use_case_report() below reads the live state, so setting
    # these first is what makes node 11 show up as "Visited: Yes / Success"
    # in the very report it generates (otherwise it always looked SKIPPED,
    # even though its own output proves it ran).
    state["visual_report_is_visited"] = True
    state["nodes_log"] = [
        *(state.get("nodes_log") or []),
        {
            "node": "visual_report",
            "status": "Success",
            "message": "Generated the run report.",
        },
    ]

    if current_path:
        # Release SDK/Appium before the report so resources do not linger
        # into the next use case (or after the run ends).
        try:
            close_sdk_integration_agent(state, state.get("audit_recorder"))
        except Exception:
            pass
        _quit_appium_driver(state)

        from data.reports.build_report import record_use_case_report

        state = record_use_case_report(
            state, audit_recorder=state.get("audit_recorder")
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


    if not state.get("current_use_case_path"):
        from data.reports.build_report import attach_index_report

        state = attach_index_report(state)

        # Last node of the run: no use cases remain, so wipe the entire
        # data/runs/<run_id>/ directory now that the final report is built.
        _clear_run_dir(state)

    return state


def route_from_sdk_agent(
    state: PipelineState,
) -> str:
    """
    Conditional edge from SDK agent.

    verify_prompt (pass or fail) -> test_runner
    integrate/event (pass or fail) -> compilation_check

    Deliberately does NOT check _is_pipeline_fail here in general: a
    classifier FAIL on its own is recorded in nodes_log/fail_reason (see
    sdk_agent_node) but must not short-circuit straight to test_runner —
    the classifier can be wrong, so integrate/event failures still continue
    to the same next node they'd reach on success.

    The one exception is state["sdk_integration_incomplete"]: an objective
    (not classifier-dependent) signal set by sdk_agent_node when
    integrate_prompt failed AND wrote zero source files. In that case there
    is provably no SDK code to compile, so running compilation_check/emulator
    anyway would just waste a full build+simulator-boot cycle on a build that
    cannot pass -- go straight to test_runner instead.
    """
    if state.get("sdk_integration_incomplete"):
        return "test_runner"

    prompt_just_run = (
        state.get("prompt_just_run")
        or state.get("last_prompt_type")
    )

    if prompt_just_run == "verify_prompt":
        return "test_runner"
        
    return "compilation_check"



def route_from_emulator(state: PipelineState) -> str:
    """
    Conditional edge from emulator.

    FAIL -> test_runner
    otherwise delegate to nodeEmulator routing logic
    """
    if _is_pipeline_fail(state):
        return "test_runner"

    return _route_from_emulator_impl(state)


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