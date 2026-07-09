from __future__ import annotations

from typing import Literal, TypedDict

PromptType = Literal["integrate_prompt", "event_prompt", "verify_prompt"]


class PipelineState(TypedDict):
    """Shared state threaded through every node of the workflow graph."""

    visited_user_actions: bool
    last_prompt_type: PromptType


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

    `state["last_prompt_type"]` tells this node which prompt is being sent
    for the current pass; `route_from_sdk_agent` reads it right after this
    node runs to decide whether to go build+run again or jump to the final
    test run.
    """
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

    - last_prompt_type == "verify_prompt"                     -> test_runner
    - last_prompt_type in {"integrate_prompt", "event_prompt"} -> compilation_check
    """
    if state["last_prompt_type"] == "verify_prompt":
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
