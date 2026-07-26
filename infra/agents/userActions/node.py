from __future__ import annotations

from pathlib import Path

from infra.agents.userActions.core import run_user_actions_pipeline
from infra.workflow.workflow_nodes import PipelineState

NODE_NAME = "user_actions"
NEXT_NODE = "deep_link"


# The only inapp_event_method values that involve the SDK Agent creating a
# brand-new in-app event and wiring a triggerId to a tappable UI control
# (see data/rules/sdk-agent-main-rules.json -> event_wiring_rules: "these
# rules apply when the SDK Agent creates in-app events via MCP tools").
# Those are the only cases where a UI element actually needs to be
# discovered and tapped by Appium, so they're the only ones that produce
# (and require) events.wired.json.
_TAP_BASED_EVENT_METHODS = {"log_event", "button_tap"}


def _requires_tap_discovery(state: PipelineState) -> bool:
    """True when this use case's policy requires the discover/tap flow.

    False covers both:
    - `inapp_event_method == "none"` (the schema default,
      infra/use_case_service/schemas.py) — no in-app event at all, e.g.
      deep-link/app-link validation use cases.
    - `inapp_event_method == "validate_payload"` (and any other
      non-tap method) — an event that fires automatically / is verified
      from its payload or logs (e.g. `af_app_opened` on first launch),
      with no UI control to create or tap.

    Neither case ever produces `events.wired.json` during `event_prompt`
    (there's nothing to wire), so this node must skip rather than fail for
    them.
    """
    in_app_event_policy = (state.get("answer_policy") or {}).get("in_app_event") or {}
    method = in_app_event_policy.get("inapp_event_method")
    return method in _TAP_BASED_EVENT_METHODS


def user_actions_node(state: PipelineState) -> PipelineState:
    state["current_node"] = NODE_NAME

    if state.get("incoming_question"):
        state["next_node"] = NODE_NAME
        return state

    sandbox_path = state.get("sandbox_path")
    platform = state.get("platform")

    if not sandbox_path or not platform:
        state.setdefault("nodes_log", []).append({
            "node": NODE_NAME,
            "status": "Fail",
            "details": {"error": "sandbox_path, platform, and events.wired.json are required"},
        })
        state["next_node"] = NODE_NAME
        state["test_status"] = "FAIL"
        return state

    manifest_path = Path(sandbox_path) / "events.wired.json"
    if not manifest_path.is_file():
        if not _requires_tap_discovery(state):
            state["visited_user_actions"] = True
            state["next_node"] = NEXT_NODE
            state.setdefault("nodes_log", []).append({
                "node": NODE_NAME,
                "status": "Skipped",
                "details": {
                    "reason": "This use case's answer_policy.in_app_event.inapp_event_method "
                              "does not require UI-tap wiring (e.g. 'none' or 'validate_payload'); "
                              "nothing to discover/tap.",
                },
            })
            return state

        state.setdefault("nodes_log", []).append({
            "node": NODE_NAME,
            "status": "Fail",
            "details": {"error": "sandbox_path, platform, and events.wired.json are required"},
        })
        state["next_node"] = NODE_NAME
        state["test_status"] = "FAIL"
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
    if result["status"] != "Success":
        # route_after_user_actions (workflow_nodes.py) gates on test_status,
        # not next_node -- without this, a real tap/discovery failure here
        # was silently swallowed and the graph moved on to deep_link anyway.
        state["test_status"] = "FAIL"
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
