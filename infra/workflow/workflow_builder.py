from __future__ import annotations

from langgraph.graph import END, START, StateGraph

from infra.agents.userActions.node import user_actions_node
from infra.workflow.workflow_nodes import (
    PipelineState,
    artifact_generator_node,
    compilation_check_node,
    deep_link_node,
    emulator_node,
    environment_setup_node,
    json_use_case_input_node,
    prompt_agent_node,
    route_after_artifact_generator,
    route_after_compilation_check,
    route_after_deep_link,
    route_after_environment_setup,
    route_after_json_use_case_input,
    route_after_prompt_agent,
    route_after_user_actions,
    route_from_emulator,
    route_from_sdk_agent,
    route_from_visual_report,
    sdk_agent_node,
    test_runner_node,
    visual_report_node,
)

# Every conditional router may return "test_runner" when test_status == "FAIL".
_FAIL_OR_NEXT = {
    "test_runner": "test_runner",
}


def build_workflow():
    graph = StateGraph(PipelineState)

    # Register all nodes
    graph.add_node("json_use_case_input", json_use_case_input_node)
    graph.add_node("artifact_generator", artifact_generator_node)
    graph.add_node("environment_setup", environment_setup_node)
    graph.add_node("prompt_agent", prompt_agent_node)
    graph.add_node("sdk_agent", sdk_agent_node)
    graph.add_node("compilation_check", compilation_check_node)
    graph.add_node("emulator", emulator_node)
    graph.add_node("user_actions", user_actions_node)
    graph.add_node("deep_link", deep_link_node)
    graph.add_node("test_runner", test_runner_node)
    graph.add_node("visual_report", visual_report_node)

    graph.add_edge(START, "json_use_case_input")

    graph.add_conditional_edges(
        "json_use_case_input",
        route_after_json_use_case_input,
        {**_FAIL_OR_NEXT, "artifact_generator": "artifact_generator"},
    )
    graph.add_conditional_edges(
        "artifact_generator",
        route_after_artifact_generator,
        {**_FAIL_OR_NEXT, "environment_setup": "environment_setup"},
    )
    graph.add_conditional_edges(
        "environment_setup",
        route_after_environment_setup,
        {**_FAIL_OR_NEXT, "prompt_agent": "prompt_agent"},
    )
    graph.add_conditional_edges(
        "prompt_agent",
        route_after_prompt_agent,
        {**_FAIL_OR_NEXT, "sdk_agent": "sdk_agent"},
    )

    graph.add_conditional_edges(
        "sdk_agent",
        route_from_sdk_agent,
        {
            "compilation_check": "compilation_check",
            "user_actions": "user_actions",
            "test_runner": "test_runner",
            # route_from_sdk_agent returns this after an event_prompt turn
            # (see workflow_nodes.py) -- missing here meant LangGraph raised
            # a bare `KeyError: 'user_actions'` (uncaught, before any report
            # could be written) the moment a use case reached that turn.
            "user_actions": "user_actions",
        },
    )

    graph.add_conditional_edges(
        "compilation_check",
        route_after_compilation_check,
        {**_FAIL_OR_NEXT, "emulator": "emulator"},
    )

    graph.add_conditional_edges(
        "emulator",
        route_from_emulator,
        {
            "sdk_agent": "sdk_agent",
            "user_actions": "user_actions",
            "test_runner": "test_runner",
        },
    )

    graph.add_conditional_edges(
        "user_actions",
        route_after_user_actions,
        {**_FAIL_OR_NEXT, "deep_link": "deep_link"},
    )
    graph.add_conditional_edges(
        "deep_link",
        route_after_deep_link,
        {**_FAIL_OR_NEXT, "sdk_agent": "sdk_agent"},
    )

    graph.add_edge("test_runner", "visual_report")

    graph.add_conditional_edges(
        "visual_report",
        route_from_visual_report,
        {
            "artifact_generator": "artifact_generator",
            "end": END,
        },
    )

    return graph.compile()


workflow_app = build_workflow()
