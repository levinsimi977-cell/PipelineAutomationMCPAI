from __future__ import annotations

import asyncio
from typing import Any

from infra.agents.AuditRecorder import AuditRecorder
from infra.load_env import get_app_id_for_platform, get_dev_key, load_project_env
from infra.use_case_service.repositories import run_repository as run_repo
from infra.workflow.workflow_nodes import PipelineState


def _platform_from_use_cases(use_cases: list[dict]) -> str:
    """
    run_platform (stamped by the UI at selection time — see ui/app.py's
    _stamp_run_platform) takes priority over the use case's own "platform"
    field: a "common" use case's platform field is literally the string
    "common", which isn't a real platform get_app_id_for_platform() could
    resolve credentials for.
    """
    if not use_cases:
        return "android"
    first_case = use_cases[0]
    platform = first_case.get("run_platform") or first_case.get("platform")
    if isinstance(platform, str) and platform.strip():
        return platform.strip().lower()
    return "android"


def _resolve_run_credentials(
    use_cases: list[dict],
    *,
    platform: str,
) -> tuple[str, str | None, str | None]:
    """Resolve platform, dev_key, and app_id for the initial pipeline state."""
    first_case = use_cases[0] if use_cases else {}

    dev_key = first_case.get("dev_key") or get_dev_key()
    app_id = first_case.get("app_id") or get_app_id_for_platform(platform)

    return platform, dev_key, app_id


def build_initial_state(run_id: str) -> PipelineState:
    """
    Assemble the PipelineState the workflow graph starts from, using only
    what was actually saved to data/runs/<run_id>/. json_use_case_input_node
    (the graph's entry node, see workflow_nodes.py) is what pulls the first
    use case out of this list to kick off the run.
    """
    selected_use_cases = run_repo.load_selected_use_cases(run_id)

    run_dir = run_repo.RUNS_DIR / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    platform, dev_key, app_id = _resolve_run_credentials(
        selected_use_cases,
        platform=_platform_from_use_cases(selected_use_cases),
    )

    state: PipelineState = {
        "run_id": run_id,
        "selected_use_cases": selected_use_cases,
        "audit_recorder": AuditRecorder(run_dir),
        "visited_user_actions": False,
        "platform": platform,
    }

    if dev_key:
        state["dev_key"] = dev_key
    if app_id:
        state["app_id"] = app_id

    return state


def _teardown_after_run(state: dict[str, Any] | None, run_id: str) -> None:
    """
    Best-effort cleanup after a workflow invoke (success, FAIL, or crash).

    Keeps each run isolated for the next click:
    - close SDK agents / Appium drivers / sandboxes (state + mid-node registry)
    - stop Appium / emulator processes this run started
    - drop in-memory answer_policy for this run_id
    - delete data/runs/<run_id>/ if visual_report did not already

    Never raises — teardown must not hide the original workflow error.
    """
    state = state or {"run_id": run_id}

    # Registry covers resources allocated mid-node before LangGraph committed
    # them into the streamed state (also covers handles still on `state`).
    try:
        from infra.workflow.run_resource_registry import release_run_resources

        release_run_resources(run_id, state)
    except Exception:
        pass

    try:
        from infra.agents.sdkAgent.tools.emulator import stop_owned_device_processes

        stop_owned_device_processes()
    except Exception:
        pass

    try:
        from infra.agents.answerAgent.answer_policy_repository import (
            get_answer_policy_repository,
        )

        get_answer_policy_repository().clear(run_id)
    except Exception:
        pass

    try:
        run_repo.delete_run_selection(run_id)
    except Exception:
        pass


async def _ainvoke_tracking_latest(
    app: Any,
    initial_state: dict[str, Any],
    latest: dict[str, Any],
) -> dict[str, Any]:
    """
    Run the graph with stream_mode="values", copying each full state snapshot
    into `latest` as nodes complete.

    Unlike ainvoke (which returns nothing on raise), this keeps the last known
    sandbox_path / agent_id / driver available for `_teardown_after_run` even
    when a node crashes mid-run.
    """
    latest.clear()
    latest.update(initial_state)
    async for values in app.astream(initial_state, stream_mode="values"):
        if isinstance(values, dict):
            latest.clear()
            latest.update(values)
    return latest


def start_workflow(run_id: str) -> dict[str, Any]:
    """
    Build the initial state for `run_id` and run it through the compiled
    LangGraph workflow.

    workflow_app is imported here, not at module load time, because
    building the graph pulls in heavy/partially-optional dependencies
    (langgraph, langchain, the Appium emulator helpers). Keeping the import
    local means the rest of the app keeps working even if those aren't
    installed yet — the error only surfaces when this function actually runs.

    Always runs `_teardown_after_run` in finally so the next Save and run
    starts clean even if this invoke crashed mid-way. Uses astream so the
    finally block still sees the last known runtime resources on crash.
    """
    from infra.workflow.workflow_builder import workflow_app

    load_project_env()
    initial_state = build_initial_state(run_id)
    # Mutable bag: updated after every node so crash mid-run still teardowns.
    latest_state: dict[str, Any] = dict(initial_state)
    try:
        return asyncio.run(
            _ainvoke_tracking_latest(workflow_app, initial_state, latest_state)
        )
    finally:
        _teardown_after_run(latest_state, run_id)
