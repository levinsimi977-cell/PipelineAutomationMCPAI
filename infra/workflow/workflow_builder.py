from __future__ import annotations

from langgraph.graph import END, START, StateGraph

from infra.workflow.workflow_nodes import (
    PipelineState,
    artifact_generator_node,
    compilation_check_node,
    deep_link_node,
    emulator_node,
    environment_setup_node,
    json_use_case_input_node,
    prompt_agent_node,
    route_from_emulator,
    route_from_sdk_agent,
    sdk_agent_node,
    test_runner_node,
    user_actions_node,
    visual_report_node,
)


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

    # Fixed edges
    graph.add_edge(START, "json_use_case_input")
    graph.add_edge("json_use_case_input", "artifact_generator")
    graph.add_edge("artifact_generator", "environment_setup")
    graph.add_edge("environment_setup", "prompt_agent")
    graph.add_edge("prompt_agent", "sdk_agent")
    graph.add_edge("compilation_check", "emulator")
    graph.add_edge("user_actions", "deep_link")
    graph.add_edge("deep_link", "sdk_agent")
    graph.add_edge("test_runner", "visual_report")
    graph.add_edge("visual_report", END)

    # Conditional edges — driven by PipelineState (last_prompt_type / visited_user_actions)
    graph.add_conditional_edges(
        "sdk_agent",
        route_from_sdk_agent,
        {
            "compilation_check": "compilation_check",
            "test_runner": "test_runner",
        },
    )
    graph.add_conditional_edges(
        "emulator",
        route_from_emulator,
        {
            "sdk_agent": "sdk_agent",
            "user_actions": "user_actions",
        },
    )

    return graph.compile()


workflow_app = build_workflow()
