"""
Regression coverage for infra/workflow/workflow_builder.py's conditional-edge
wiring.

Regression: route_from_sdk_agent can return "user_actions" (after an
event_prompt turn -- see its docstring), but the conditional-edges mapping
for the "sdk_agent" node only listed "compilation_check" and "test_runner"
as valid targets. LangGraph looks up a router's return value in that mapping
to find the next node; a value with no entry raises a bare, uncaught
`KeyError: 'user_actions'` deep inside LangGraph -- before any node, log, or
report is written, which is exactly why the UI could only show a raw
"'user_actions'" error with no report to explain it.

These tests exercise every branch of each conditional router function
against the *actual* compiled graph's wired edges, so any router return
value missing from its own mapping in workflow_builder.py fails loudly here
instead of surfacing as an unexplained crash mid-run.
"""

from __future__ import annotations

import pytest

from infra.workflow.workflow_builder import workflow_app
from infra.workflow.workflow_nodes import (
    route_after_artifact_generator,
    route_after_compilation_check,
    route_after_deep_link,
    route_after_environment_setup,
    route_after_json_use_case_input,
    route_after_prompt_agent,
    route_after_user_actions,
    route_from_sdk_agent,
    route_from_visual_report,
)
from infra.workflow.nodes.nodeEmulator import route_from_emulator


def _wired_targets(source_node: str) -> set[str]:
    """All targets actually reachable from `source_node` in the compiled graph.

    LangGraph's own END sentinel is rendered as the node name "__end__" in
    graph.get_graph(), even though workflow_builder.py's mapping spells its
    own key "end" (-> END) -- normalize that one name back so routers that
    return the literal string "end" (route_from_visual_report) compare
    correctly against the compiled graph.
    """
    graph = workflow_app.get_graph()
    targets = {edge.target for edge in graph.edges if edge.source == source_node}
    if "__end__" in targets:
        targets.add("end")
    return targets


@pytest.mark.parametrize(
    "source_node, router, state",
    [
        ("json_use_case_input", route_after_json_use_case_input, {}),
        ("json_use_case_input", route_after_json_use_case_input, {"test_status": "FAIL"}),
        ("artifact_generator", route_after_artifact_generator, {}),
        ("artifact_generator", route_after_artifact_generator, {"test_status": "FAIL"}),
        ("environment_setup", route_after_environment_setup, {}),
        ("environment_setup", route_after_environment_setup, {"test_status": "FAIL"}),
        ("prompt_agent", route_after_prompt_agent, {}),
        ("prompt_agent", route_after_prompt_agent, {"test_status": "FAIL"}),
        ("compilation_check", route_after_compilation_check, {}),
        ("compilation_check", route_after_compilation_check, {"test_status": "FAIL"}),
        ("user_actions", route_after_user_actions, {}),
        ("user_actions", route_after_user_actions, {"test_status": "FAIL"}),
        ("deep_link", route_after_deep_link, {}),
        ("deep_link", route_after_deep_link, {"test_status": "FAIL"}),
        # route_from_sdk_agent's three branches -- see its docstring.
        ("sdk_agent", route_from_sdk_agent, {"last_prompt_type": "verify_prompt"}),
        ("sdk_agent", route_from_sdk_agent, {"last_prompt_type": "event_prompt"}),
        ("sdk_agent", route_from_sdk_agent, {"last_prompt_type": "integrate_prompt"}),
        # route_from_emulator's two branches -- see its docstring.
        (
            "emulator",
            route_from_emulator,
            {"prompt_just_run": "event_prompt", "visited_user_actions": False},
        ),
        (
            "emulator",
            route_from_emulator,
            {"prompt_just_run": "event_prompt", "visited_user_actions": True},
        ),
        ("emulator", route_from_emulator, {"prompt_just_run": "integrate_prompt"}),
        ("visual_report", route_from_visual_report, {}),
        ("visual_report", route_from_visual_report, {"current_use_case_path": "x.json"}),
    ],
)
def test_router_return_value_is_wired_in_the_compiled_graph(source_node, router, state):
    destination = router(state)
    wired = _wired_targets(source_node)
    assert destination in wired, (
        f"{router.__name__}({state!r}) returned {destination!r}, which is not "
        f"wired as a target for {source_node!r} in workflow_builder.py "
        f"(wired targets: {sorted(wired)}). This would crash the real run with "
        f"an uncaught KeyError the moment this branch is taken."
    )
