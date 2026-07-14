from __future__ import annotations

from typing import Any

from infra.agents.AuditRecorder import AuditRecorder
from infra.use_case_service.repositories import run_repository as run_repo
from infra.workflow.workflow_nodes import PipelineState


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

    return {
        "run_id": run_id,
        "selected_use_cases": selected_use_cases,
        "audit_recorder": AuditRecorder(run_dir),
        "visited_user_actions": False,
    }


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

    initial_state = build_initial_state(run_id)
    return workflow_app.invoke(initial_state)
