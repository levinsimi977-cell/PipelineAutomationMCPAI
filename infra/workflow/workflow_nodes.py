from __future__ import annotations

import asyncio
from typing import Any, Literal, NotRequired, TypedDict

from infra.agents.AuditRecorder import AuditRecorder
from infra.agents.sdkAgent.tools.agent import run_sdk_integration_agent

PromptType = Literal["integrate_prompt", "event_prompt", "verify_prompt"]

_PROMPT_SEQUENCE: list[PromptType] = [
    "integrate_prompt",
    "event_prompt",
    "verify_prompt",
]


def _next_prompt_type(current: PromptType) -> PromptType | None:
    """Return the next prompt type in the pipeline, or None after verify_prompt."""
    try:
        index = _PROMPT_SEQUENCE.index(current)
    except ValueError:
        return None
    if index + 1 < len(_PROMPT_SEQUENCE):
        return _PROMPT_SEQUENCE[index + 1]
    return None


class PipelineState(TypedDict, total=False):
    """Shared state threaded through every node of the workflow graph."""

    visited_user_actions: bool
    last_prompt_type: PromptType | None
    prompt_just_run: PromptType
    type_agent: str
    agent_prompts: dict[str, str]
    question_rounds: int
    installation_answers: list[dict[str, Any]]
    test_status: str
    last_agent_message: str
    sandbox_path: str
    platform: str
    audit_recorder: AuditRecorder
    run_id: str
    fail_reason: NotRequired[Any]
    nodes_logs: list[dict[str, Any]]


def json_use_case_input_node(state: PipelineState) -> PipelineState:
    """Node 1: JSON Use Case Input — User enters use cases (G2)"""
    return state


def artifact_generator_node(state: PipelineState) -> PipelineState:
    """Node 2: Artifact Generator — Prompt + RULES + TEST files (G2)"""
    return state


def environment_setup_node(state: PipelineState) -> PipelineState:
    """Node 3: Environment Setup — Sandbox + MCP health check (G1)"""
    return state


def prompt_agent_node(state: PipelineState) -> PipelineState:
    """Node 4: Prompt Agent — enriched structured prompt (G3)"""
    return state


def sdk_agent_node(state: PipelineState) -> PipelineState:
    """Node 5: SDK Agent — single node, revisited on every loop of the
    workflow (integration / event / verify passes).

    `last_prompt_type` starts as None. On the first visit it is set to
    integrate_prompt; each successful run advances it to the next prompt
    (integrate → event → verify) for the following visit.
    """
    if state.get("last_prompt_type") is None:
        state["last_prompt_type"] = "integrate_prompt"

    current_prompt_type = state["last_prompt_type"]
    agent_prompts = state["agent_prompts"]
    sandbox_path = state["sandbox_path"]
    platform = state["platform"]
    audit_recorder = state["audit_recorder"]
    run_id = state["run_id"]

    user_prompt = agent_prompts[current_prompt_type]

    result = asyncio.run(
        run_sdk_integration_agent(
            project_root_str=sandbox_path,
            platform=platform,
            user_prompt=user_prompt,
            audit_recorder=audit_recorder,
            run_id=run_id,
        )
    )

    state["type_agent"] = "sdk_agent"
    state["last_agent_message"] = user_prompt
    state["prompt_just_run"] = current_prompt_type

    node_succeeded = result.get("status") != "FAIL"
    node_log: dict[str, Any] = {
        "node": "sdk_agent",
        "status": "Success" if node_succeeded else "Failure",
        "prompt_type": current_prompt_type,
    }
    if not node_succeeded and "reason" in result:
        node_log["reason"] = result["reason"]

    nodes_logs = list(state.get("nodes_logs") or [])
    nodes_logs.append(node_log)
    state["nodes_logs"] = nodes_logs

    if not node_succeeded:
        state["test_status"] = "FAIL"
        if "reason" in result:
            state["fail_reason"] = result["reason"]
    else:
        next_prompt_type = _next_prompt_type(current_prompt_type)
        if next_prompt_type is not None:
            state["last_prompt_type"] = next_prompt_type

    return state


def compilation_check_node(state: PipelineState) -> PipelineState:
    """Node 6: Compilation Check — build validation before run (G4)"""
    return state


def emulator_node(state: PipelineState) -> PipelineState:
    """Node 7: Emulator — launch compiled app (G5)"""
    return state


def user_actions_node(state: PipelineState) -> PipelineState:
    """Node 8: User Actions — simulated taps on screen (G5)"""
    state["visited_user_actions"] = True
    return state


def deep_link_node(state: PipelineState) -> PipelineState:
    """Node 9: Deep Link — verify deep link behavior (G5)"""
    return state


def test_runner_node(state: PipelineState) -> PipelineState:
    """Node 10: Test Runner — full test suite execution (G2)"""
    return state


def visual_report_node(state: PipelineState) -> PipelineState:
    """Node 11: Visual Report — HTML audit dashboard (G2/4)"""
    return state


def route_from_sdk_agent(state: PipelineState) -> str:
    """Conditional edge out of `sdk_agent`.

    Uses `prompt_just_run` (the prompt that just finished) because
    `last_prompt_type` may already point at the next pass.

    - prompt_just_run == "verify_prompt"                     -> test_runner
    - prompt_just_run in {"integrate_prompt", "event_prompt"} -> compilation_check
    """
    prompt_just_run = state.get("prompt_just_run") or state.get("last_prompt_type")
    if prompt_just_run is None:
        return "compilation_check"
    if prompt_just_run == "verify_prompt":
        return "test_runner"
    return "compilation_check"


def route_from_emulator(state: PipelineState) -> str:
    """Conditional edge out of `emulator`.

    - last_prompt_type == "integrate_prompt"                       -> sdk_agent
    - last_prompt_type == "event_prompt" and visited_user_actions  -> sdk_agent
    - last_prompt_type == "event_prompt" and not visited_user_actions -> user_actions
    """
    if state["last_prompt_type"] == "event_prompt" and not state["visited_user_actions"]:
        return "user_actions"
    return "sdk_agent"
