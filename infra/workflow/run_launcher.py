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


def start_workflow(run_id: str) -> dict[str, Any]:
    """
    Build the initial state for `run_id` and run it through the compiled
    LangGraph workflow.

    workflow_app is imported here, not at module load time, because
    building the graph pulls in heavy/partially-optional dependencies
    (langgraph, langchain, the Appium emulator helpers). Keeping the import
    local means the rest of the app keeps working even if those aren't
    installed yet — the error only surfaces when this function actually runs.
    """
    from infra.workflow.workflow_builder import workflow_app

    load_project_env()
    initial_state = build_initial_state(run_id)
    return asyncio.run(workflow_app.ainvoke(initial_state))
