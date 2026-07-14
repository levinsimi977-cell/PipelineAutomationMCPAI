from __future__ import annotations

from pathlib import Path

from infra.agents.userActions.core import run_user_actions_pipeline
from infra.workflow.workflow_nodes import PipelineState

NODE_NAME = "user_actions"
NEXT_NODE = "deep_link"


def user_actions_node(state: PipelineState) -> PipelineState:
    state["current_node"] = NODE_NAME

    if state.get("incoming_question"):
        state["next_node"] = NODE_NAME
        return state

    run_id = state.get("run_id")
    platform = state.get("platform")
    manifest_path = Path("data") / "runs" / run_id / "events.wired.json" if run_id else None
    if not run_id or not platform or not manifest_path or not manifest_path.is_file():
        state.setdefault("nodes_log", []).append({
            "node": NODE_NAME,
            "status": "Fail",
            "details": {"error": "run_id, platform, and events.wired.json are required"},
        })
        state["next_node"] = NODE_NAME
        return state

    result = run_user_actions_pipeline(
        manifest_path=manifest_path,
        platform=platform,
        appium_url=state.get("appium_url", "http://127.0.0.1:4723"),
        wait_seconds=state.get("wait_seconds", 2.0),
        only_event=state.get("only_event"),
    )

    state["visited_user_actions"] = True
    state["next_node"] = NEXT_NODE if result["status"] == "Success" else NODE_NAME
    state.setdefault("nodes_log", []).append({
        "node": NODE_NAME,
        "status": result["status"],
        "details": {
            "phase": result["phase"],
            "discovery_validation": result["discovery_validation"],
            "tap_validation": result.get("tap_validation"),
            "event_count": result.get("event_count", 0),
            "tap_count": result.get("tap_count", 0),
        },
    })
    return state
